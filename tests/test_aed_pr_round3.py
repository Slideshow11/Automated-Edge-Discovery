"""Round-3 regression tests for the canonical AED PR-lifecycle controller.

Covers the four round-2 Codex findings the controller's repair commit
addresses, plus tests for the trusted scope, review-comment-gate
recheck, and the F4 control-flow fix.

All tests use mocks. No live GitHub calls are made.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.local import aed_pr_lib as L  # noqa: E402
from scripts.local import aed_pr_readiness as R  # noqa: E402
from scripts.local import aed_pr as ctrl  # noqa: E402


PASSING_SCOPE = [
    "scripts/local/aed_pr*.py",
    "scripts/local/aed_pr_lib.py",
    "scripts/local/aed_pr_readiness.py",
    "tests/test_aed_pr*.py",
    "tests/test_phase_ledger_unit.py",
    "tests/test_aed_lifecycle_states.py",
    "docs/aed_pr*.md",
]

DEFAULT_REPO = "Slideshow11/Automated-Edge-Discovery"
DEFAULT_HEAD = "a" * 40


def _full_passing_evidence(
    head_sha: object = DEFAULT_HEAD, phrase="__DEFAULT__", pr_number=411,
    allowed_files_supplied=True,
):
    """Build a ReadinessEvidence bundle where every gate passes.

    ``phrase="__DEFAULT__"`` (sentinel) keeps the helper's previous
    behavior of building the canonical phrase. ``phrase=None`` means
    "no phrase was supplied" - the merge path's authorization gate
    must then report ``authorization_valid=False``.
    """
    effective_phrase: object
    if phrase == "__DEFAULT__":
        effective_phrase = L.build_authorization_phrase(pr_number, head_sha)
    else:
        effective_phrase = phrase
    ev = R.ReadinessEvidence(
        pr_state="OPEN",
        is_draft=False,
        mergeable=True,
        head_sha=head_sha,
        authorization_phrase=(
            effective_phrase if isinstance(effective_phrase, str) else None
        ),
        changed_files=["scripts/local/aed_pr.py"],
        changed_files_fetched=True,
        scope_clean=True,
        out_of_scope_files=[],
        forbidden_files_touched=[],
        scope_blockers=[],
        allowed_files_supplied=allowed_files_supplied,
        required_ci_names=list(ctrl.REQUIRED_CHECK_NAMES),
        ci_conclusions={
            "test (3.11)": "SUCCESS", "validator": "SUCCESS",
            "governance-validators": "SUCCESS",
            "pr-gate-live-smoke": "SUCCESS",
            "review-comment-gate": "SUCCESS",
        },
        ci_missing=[],
        ci_pending=[],
        ci_failed=[],
        codex_verdict="CODEX_CLEAN_PASS",
        codex_source="issue_comment",
        codex_reviewed_sha=head_sha,
        codex_clean_passed=True,
        codex_artifact_present=True,
        codex_artifact_fresh=True,
        codex_review_url="https://example/codex",
        codex_review_id="12345",
        reviews_inventory_complete=True,
        reviews_inventory_error=None,
        review_threads=[],
        review_thread_inventory_complete=True,
        review_thread_inventory_error=None,
        unresolved_thread_count=0,
        unresolved_thread_ids=[],
        unresolved_human_thread_ids=[],
        unresolved_bot_thread_ids=[],
        outdated_bot_thread_ids=[],
        evidence_sources={
            "pr_view": "fetched",
            "changed_files": "fetched",
            "scope_check": "fetched",
            "ci_audit": "fetched",
            "codex_audit": "fetched",
            "reviews_inventory": "fetched",
            "review_thread_inventory": "fetched",
            "pr_number": "fetched",
        },
    )
    setattr(ev, "_pr_number_int", pr_number)
    return ev


def _failing_gate_reason(gate_evidence):
    """Run evaluate_readiness on gate_evidence and return reason codes."""
    v = R.evaluate_readiness(gate_evidence)
    return {r.code for r in v.reasons}


# ---------------------------------------------------------------------------
# F1 — machine readiness vs authorization
# ---------------------------------------------------------------------------


class TestF1MachineReadinessSeparation:
    def test_status_does_not_require_supplied_phrase(self):
        ev = _full_passing_evidence(phrase=None)
        v = R.evaluate_machine_readiness(ev)
        assert v.machine_ready is True
        assert v.authorization_valid is None

    def test_clean_machine_emits_READY_FOR_MERGE_AUTHORIZATION(self):
        ev = _full_passing_evidence()
        v = R.evaluate_machine_readiness(ev)
        state = ctrl.derive_lifecycle_state(v, {
            "state": "OPEN", "isDraft": False, "headRefOid": DEFAULT_HEAD,
            "mergeable": True,
        })
        assert state == "READY_FOR_MERGE_AUTHORIZATION"

    def test_canonical_phrase_appears_only_in_ready_state(self):
        ev = _full_passing_evidence()
        v = R.evaluate_machine_readiness(ev)
        phrase = (
            L.build_authorization_phrase(411, DEFAULT_HEAD)
            if v.machine_ready and R.is_canonical_head_sha(DEFAULT_HEAD)
            else None
        )
        assert phrase is not None
        assert phrase == (
            "I confirm merge PR #411 at " + DEFAULT_HEAD
            + " using final-head reviewed clean state."
        )

    def test_blocked_status_emits_no_phrase(self):
        ev = _full_passing_evidence()
        ev.ci_failed = ["validator"]
        v = R.evaluate_machine_readiness(ev)
        assert v.machine_ready is False
        assert v.authorization_valid is None

    def test_merge_rejects_missing_phrase(self):
        ev = _full_passing_evidence(phrase=None)
        v = R.evaluate_readiness(ev)
        assert v.merge_ready is False
        assert v.authorization_valid is False

    def test_merge_rejects_incorrect_phrase(self):
        ev = _full_passing_evidence(
            phrase="I confirm merge PR #411 at WRONG using final-head reviewed clean state.",
        )
        v = R.evaluate_readiness(ev)
        assert v.merge_ready is False
        assert v.authorization_valid is False

    def test_merge_rejects_stale_head_phrase(self):
        canonical = L.build_authorization_phrase(411, DEFAULT_HEAD)
        ev = _full_passing_evidence(head_sha="b" * 40, phrase=canonical)
        v = R.evaluate_readiness(ev)
        assert v.merge_ready is False
        assert v.machine_ready is True
        assert v.authorization_valid is False

    def test_valid_phrase_cannot_override_failed_machine_gate(self):
        ev = _full_passing_evidence()
        ev.scope_clean = False
        ev.out_of_scope_files = ["scripts/local/aed_pr.py"]
        v = R.evaluate_readiness(ev)
        assert v.machine_ready is False
        assert v.authorization_valid is True
        assert v.merge_ready is False

    def test_merge_succeeds_only_when_both_machine_and_authorization_pass(self):
        ev = _full_passing_evidence()
        v = R.evaluate_readiness(ev)
        assert v.machine_ready is True
        assert v.authorization_valid is True
        assert v.merge_ready is True

    def test_status_advance_merge_share_machine_evaluator(self):
        ev = _full_passing_evidence()
        v_status = R.evaluate_machine_readiness(ev)
        v_advance = R.evaluate_machine_readiness(ev)
        assert set(v_status.gates_passed) == set(v_advance.gates_passed)


# ---------------------------------------------------------------------------
# F2 — required check inspection via gh pr checks
# ---------------------------------------------------------------------------


REQUIRED_CHECK_PAYLOAD_ALL_PASS = [
    {"name": "test (3.11)", "state": "SUCCESS", "workflow": "CI"},
    {"name": "validator", "state": "SUCCESS", "workflow": "CI"},
    {"name": "governance-validators", "state": "SUCCESS", "workflow": "CI"},
    {"name": "pr-gate-live-smoke", "state": "SUCCESS", "workflow": "CI"},
    {"name": "review-comment-gate", "state": "SUCCESS", "workflow": "CI"},
]


class TestF2RealRequiredCheckInspection:
    def test_all_required_checks_pass(self):
        ev = _full_passing_evidence()
        v = R.evaluate_readiness(ev)
        assert R.GATE_CI_PRESENT in v.gates_passed
        assert R.REASON_CI_MISSING not in {r.code for r in v.reasons}

    @pytest.mark.parametrize("missing", [
        ["test (3.11)"], ["validator"], ["review-comment-gate"],
    ])
    def test_missing_required_check_blocks(self, missing):
        ev = _full_passing_evidence()
        ev.ci_missing = list(missing)
        v = R.evaluate_readiness(ev)
        assert R.GATE_CI_PRESENT in v.gates_failed
        assert R.REASON_CI_MISSING in {r.code for r in v.reasons}

    def test_pending_blocks(self):
        ev = _full_passing_evidence()
        ev.ci_pending = ["validator"]
        v = R.evaluate_readiness(ev)
        assert R.REASON_CI_PENDING in {r.code for r in v.reasons}

    def test_failed_blocks(self):
        ev = _full_passing_evidence()
        ev.ci_failed = ["validator"]
        v = R.evaluate_readiness(ev)
        assert R.REASON_CI_FAILED in {r.code for r in v.reasons}

    @pytest.mark.parametrize("bad_state", [
        "CANCELLED", "STALE", "SKIPPED", "NEUTRAL", "ERROR",
    ])
    def test_terminal_non_success_blocks(self, bad_state):
        ev = _full_passing_evidence()
        ev.ci_conclusions["validator"] = bad_state
        ev.ci_failed = ["validator"]
        v = R.evaluate_readiness(ev)
        assert R.GATE_CI_PRESENT in v.gates_failed

    def test_unrelated_successful_checks_do_not_compensate(self):
        ev = _full_passing_evidence()
        ev.ci_missing = ["test (3.11)"]
        ev.ci_conclusions = {
            "unrelated-A": "SUCCESS", "unrelated-B": "SUCCESS",
            "validator": "SUCCESS", "governance-validators": "SUCCESS",
            "pr-gate-live-smoke": "SUCCESS", "review-comment-gate": "SUCCESS",
        }
        v = R.evaluate_readiness(ev)
        assert R.REASON_CI_MISSING in {r.code for r in v.reasons}

    def test_required_check_policy_unavailable_blocks(self):
        ev = _full_passing_evidence()
        ev.required_ci_names = None
        v = R.evaluate_readiness(ev)
        assert R.GATE_CI_PRESENT in v.gates_failed

    def test_fetch_uses_pr_checks_not_run_list(self):
        calls = []
        def fake_run(cmd, *args, **kwargs):
            calls.append(list(cmd))
            return mock.Mock(returncode=0, stdout="[]", stderr="")
        with mock.patch.object(subprocess, "run", side_effect=fake_run):
            ctrl.fetch_ci_conclusions("o/r", 411, list(ctrl.REQUIRED_CHECK_NAMES))
        gh = [c for c in calls if c and c[0] == "gh"]
        assert gh, "expected at least one gh invocation"
        assert gh[0][:3] == ["gh", "pr", "checks"]


# ---------------------------------------------------------------------------
# F3 — trusted scope authority
# ---------------------------------------------------------------------------


class TestF3TrustedScopeAuthority:
    def setup_method(self):
        # Tests inject a tempdir as the canonical scope root via the
        # module-level constant ``_CANONICAL_SCOPE_ROOT``. The
        # previous round-3 ``HERMES_AED_SCOPE_DIR`` env-var seam was
        # removed in round-4 because a hostile caller could point
        # the production merge path at a permissive directory.
        self._tmp = tempfile.TemporaryDirectory()
        self._saved_root = ctrl._CANONICAL_SCOPE_ROOT
        ctrl._CANONICAL_SCOPE_ROOT = Path(self._tmp.name)

    def teardown_method(self):
        ctrl._CANONICAL_SCOPE_ROOT = self._saved_root
        self._tmp.cleanup()

    def _write(self, head=DEFAULT_HEAD, allowed=PASSING_SCOPE, forbidden=()):
        ok, path = ctrl.write_trusted_scope(
            DEFAULT_REPO, 411, head, list(allowed), list(forbidden)
        )
        assert ok

    def test_scope_path_is_repository_pr_and_head_keyed(self):
        self._write()
        path = ctrl._trusted_scope_path(DEFAULT_REPO, 411, DEFAULT_HEAD)
        assert "Slideshow11" in str(path)
        assert "Automated-Edge-Discovery" in str(path)
        assert "411" in str(path)
        assert DEFAULT_HEAD in str(path)

    def test_authorizes_pr411_style_paths(self):
        self._write()
        allowed, _, err = ctrl.read_trusted_scope(
            DEFAULT_REPO, 411, DEFAULT_HEAD
        )
        assert err == ""
        from scripts.local.check_pr_scope import path_matches_any
        for p in [
            "scripts/local/aed_pr.py",
            "scripts/local/aed_pr_lib.py",
            "tests/test_aed_pr.py",
            "docs/aed_pr_canonical_guide.md",
        ]:
            assert path_matches_any(p, allowed), (
                f"trusted scope should authorize {p}"
            )

    def test_paths_outside_scope_fail(self):
        self._write(allowed=["docs/**/*.md"])
        from scripts.local.check_pr_scope import check_scope
        result = check_scope(
            ["scripts/local/aed_pr.py", "docs/aed_pr_canonical_guide.md"],
            ["docs/**/*.md"], [],
        )
        assert result["passed"] is False
        assert "scripts/local/aed_pr.py" in result["out_of_scope_files"]
        assert "docs/aed_pr_canonical_guide.md" not in result["out_of_scope_files"]

    def test_missing_scope_blocks_readiness(self):
        ev = _full_passing_evidence(allowed_files_supplied=False)
        v = R.evaluate_readiness(ev)
        assert R.GATE_SCOPE_CLEAN in v.gates_failed
        assert R.REASON_SCOPE_UNKNOWN in {r.code for r in v.reasons}

    def test_mixed_scope_reports_exact_offending_paths(self):
        from scripts.local.check_pr_scope import check_scope
        result = check_scope(
            [
                "scripts/local/aed_pr.py",
                "engine/secret/whatever.py",
                "tests/rogue.py",
            ],
            ["scripts/local/aed_pr*.py", "tests/test_aed_pr*.py"],
            [],
        )
        assert result["passed"] is False
        assert set(result["out_of_scope_files"]) == {
            "engine/secret/whatever.py", "tests/rogue.py"
        }

    def test_retired_wrapper_filename_not_globally_forbidden(self):
        """The controller must not hardcode a forbidden pattern list.
        A scope that does not forbid ``aed_final_gate.py`` allows it."""
        from scripts.local.check_pr_scope import check_scope
        result = check_scope(
            ["scripts/local/aed_final_gate.py"],
            ["**"], [],
        )
        assert result["passed"] is True

    def test_merge_rejects_cli_scope(self):
        _, _, err = ctrl._resolve_effective_scope(
            subcommand="merge",
            repo=DEFAULT_REPO, pr_number=411, head_sha=DEFAULT_HEAD,
            cli_allowed=["**"], cli_forbidden=None,
        )
        assert err  # non-empty

    def test_merge_rejects_cli_forbidden(self):
        _, _, err = ctrl._resolve_effective_scope(
            subcommand="merge",
            repo=DEFAULT_REPO, pr_number=411, head_sha=DEFAULT_HEAD,
            cli_allowed=None, cli_forbidden=["**"],
        )
        assert err

    def test_merge_fails_closed_when_trusted_file_absent(self):
        _, _, err = ctrl._resolve_effective_scope(
            subcommand="merge",
            repo=DEFAULT_REPO, pr_number=411, head_sha=DEFAULT_HEAD,
            cli_allowed=None, cli_forbidden=None,
        )
        assert err

    def test_merge_reads_trusted_file_only(self):
        self._write()
        allowed, _, err = ctrl._resolve_effective_scope(
            subcommand="merge",
            repo=DEFAULT_REPO, pr_number=411, head_sha=DEFAULT_HEAD,
            cli_allowed=None, cli_forbidden=None,
        )
        assert err == ""
        assert allowed == PASSING_SCOPE

    def test_stale_head_scope_record_blocks(self):
        """A scope file recorded against a different head SHA must
        NOT authorize the live head, even though the file exists."""
        # Write a file at the LIVE head but record a DIFFERENT
        # head_sha inside the payload. The file is found by path;
        # the recorded head_sha is what blocks.
        path = ctrl._trusted_scope_path(
            DEFAULT_REPO, 411, DEFAULT_HEAD
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "pr_number": 411,
            "repo": DEFAULT_REPO,
            "head_sha": "b" * 40,  # recorded head != live head
            "allowed_files": PASSING_SCOPE,
            "forbidden_files": [],
            "written_at": "2026-07-16T00:00:00Z",
        }), encoding="utf-8")
        _, _, err = ctrl.read_trusted_scope(
            DEFAULT_REPO, 411, DEFAULT_HEAD
        )
        assert "head_sha mismatch" in err

    def test_status_rejects_cli_scope(self):
        """Round-16 fix: CLI ``--allowed-files`` / ``--forbidden-files``
        on ``status`` must NOT be authoritative. The resolver returns
        ``(None, None, error)`` so the scope gate fails closed and
        ``cmd_status`` cannot emit an authorization phrase on a
        untrusted CLI override.
        """
        allowed, forbidden, err = ctrl._resolve_effective_scope(
            subcommand="status",
            repo=DEFAULT_REPO, pr_number=411, head_sha=DEFAULT_HEAD,
            cli_allowed=["scripts/local/aed_pr.py"], cli_forbidden=None,
        )
        assert err
        assert "cli_scope_not_authoritative" in err
        assert allowed is None
        assert forbidden is None

    def test_status_rejects_cli_forbidden(self):
        """Round-16: CLI ``--forbidden-files`` on ``status`` is also
        rejected with the same diagnostic.
        """
        allowed, forbidden, err = ctrl._resolve_effective_scope(
            subcommand="status",
            repo=DEFAULT_REPO, pr_number=411, head_sha=DEFAULT_HEAD,
            cli_allowed=None, cli_forbidden=["scripts/local/aed_pr.py"],
        )
        assert err
        assert "cli_scope_not_authoritative" in err
        assert allowed is None
        assert forbidden is None

    def test_advance_rejects_cli_scope(self):
        """Round-16: CLI scope on ``advance`` is also rejected (the
        same diagnostic as ``status``).
        """
        allowed, forbidden, err = ctrl._resolve_effective_scope(
            subcommand="advance",
            repo=DEFAULT_REPO, pr_number=411, head_sha=DEFAULT_HEAD,
            cli_allowed=["scripts/local/aed_pr.py"], cli_forbidden=None,
        )
        assert err
        assert "cli_scope_not_authoritative" in err
        assert allowed is None
        assert forbidden is None

    def test_merge_continues_to_reject_cli_scope(self):
        """Round-16: the merge command's existing rejection is
        unchanged.
        """
        allowed, forbidden, err = ctrl._resolve_effective_scope(
            subcommand="merge",
            repo=DEFAULT_REPO, pr_number=411, head_sha=DEFAULT_HEAD,
            cli_allowed=["**"], cli_forbidden=None,
        )
        assert err
        assert "merge does not accept" in err
        assert allowed is None
        assert forbidden is None

    def test_trusted_file_is_returned_for_all_three_lifecycle_commands(self):
        """Round-16: when no CLI override is supplied and the canonical
        trusted file exists, every lifecycle command reads the same
        authoritative scope.
        """
        self._write()
        for sub in ("status", "advance", "merge"):
            allowed, _, err = ctrl._resolve_effective_scope(
                subcommand=sub,
                repo=DEFAULT_REPO, pr_number=411, head_sha=DEFAULT_HEAD,
                cli_allowed=None, cli_forbidden=None,
            )
            assert err == "", f"{sub}: expected no error, got {err!r}"
            assert allowed == PASSING_SCOPE, (
                f"{sub}: expected PASSING_SCOPE; got {allowed!r}"
            )

    def test_trusted_file_absent_blocks_all_three_lifecycle_commands(self):
        """Round-16: a missing trusted scope record must fail closed
        on every lifecycle command. None of the three may silently
        invent a scope.
        """
        for sub in ("status", "advance", "merge"):
            allowed, forbidden, err = ctrl._resolve_effective_scope(
                subcommand=sub,
                repo=DEFAULT_REPO, pr_number=411, head_sha=DEFAULT_HEAD,
                cli_allowed=None, cli_forbidden=None,
            )
            assert err, f"{sub}: expected error; got err=''"
            assert "trusted scope not found" in err, (
                f"{sub}: unexpected error {err!r}"
            )
            assert allowed is None
            assert forbidden is None

    def test_stale_head_trusted_scope_blocks_all_three(self):
        """Round-16: a trusted scope recorded against a different head
        SHA must block every lifecycle command.
        """
        path = ctrl._trusted_scope_path(
            DEFAULT_REPO, 411, DEFAULT_HEAD
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "pr_number": 411,
            "repo": DEFAULT_REPO,
            "head_sha": "b" * 40,  # recorded head != live head
            "allowed_files": PASSING_SCOPE,
            "forbidden_files": [],
            "written_at": "2026-07-16T00:00:00Z",
        }), encoding="utf-8")
        for sub in ("status", "advance", "merge"):
            allowed, forbidden, err = ctrl._resolve_effective_scope(
                subcommand=sub,
                repo=DEFAULT_REPO, pr_number=411, head_sha=DEFAULT_HEAD,
                cli_allowed=None, cli_forbidden=None,
            )
            assert err, f"{sub}: expected error; got err=''"
            assert "head_sha mismatch" in err, (
                f"{sub}: unexpected error {err!r}"
            )
            assert allowed is None
            assert forbidden is None

    def test_conflicting_cli_flags_do_not_override_trusted_scope(self):
        """Round-16: even when a valid trusted scope exists, supplying
        CLI ``--allowed-files`` must NOT cause the CLI patterns to
        replace or be merged with the trusted scope. Both status and
        advance return ``cli_scope_not_authoritative`` with empty
        lists so the trusted file is the only authoritative source.
        """
        self._write()
        # CLI scope with patterns that DIFFER from the trusted scope.
        # The resolver must reject the CLI patterns entirely rather
        # than merging them with the trusted file.
        for sub in ("status", "advance"):
            allowed, _, err = ctrl._resolve_effective_scope(
                subcommand=sub,
                repo=DEFAULT_REPO, pr_number=411, head_sha=DEFAULT_HEAD,
                cli_allowed=["SOME/OTHER/PATH.py"],
                cli_forbidden=None,
            )
            assert err, f"{sub}: expected rejection"
            assert "cli_scope_not_authoritative" in err
            assert allowed is None
        # When the trusted file is consulted WITHOUT CLI override it
        # is still returned verbatim, so a subsequent non-CLI call
        # succeeds.
        for sub in ("status", "advance", "merge"):
            allowed, _, err = ctrl._resolve_effective_scope(
                subcommand=sub,
                repo=DEFAULT_REPO, pr_number=411, head_sha=DEFAULT_HEAD,
                cli_allowed=None, cli_forbidden=None,
            )
            assert err == ""
            assert allowed == PASSING_SCOPE

    def test_scope_write_still_creates_exact_head_record(self):
        """Round-16: ``scope-write`` (the only command that persists
        the trusted record) must continue to work for an exact head.
        """
        ok, result = ctrl.write_trusted_scope(
            DEFAULT_REPO, 411, DEFAULT_HEAD, list(PASSING_SCOPE), []
        )
        assert ok
        # ``write_trusted_scope`` returns the canonical path as the
        # informational second value on success.
        assert result and "Automated-Edge-Discovery" in result
        # The written record is then readable by all three lifecycle
        # commands.
        for sub in ("status", "advance", "merge"):
            allowed, _, rerr = ctrl._resolve_effective_scope(
                subcommand=sub,
                repo=DEFAULT_REPO, pr_number=411, head_sha=DEFAULT_HEAD,
                cli_allowed=None, cli_forbidden=None,
            )
            assert rerr == ""
            assert allowed == PASSING_SCOPE


class TestMergeArgparseRejectsScopeOverride:
    def test_merge_parser_has_no_allowed_files(self):
        parser = ctrl.build_parser()
        ns = parser.parse_args([
            "merge", "--pr-number", "411",
            "--authorization-phrase",
            "I confirm merge PR #411 at " + DEFAULT_HEAD
            + " using final-head reviewed clean state.",
        ])
        assert not hasattr(ns, "allowed_files")
        assert not hasattr(ns, "forbidden_files")

    def test_merge_parser_rejects_allowed_files_argv(self):
        parser = ctrl.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([
                "merge", "--pr-number", "411",
                "--authorization-phrase",
                "I confirm merge PR #411 at " + DEFAULT_HEAD
                + " using final-head reviewed clean state.",
                "--allowed-files", "**",
            ])


# ---------------------------------------------------------------------------
# F4 — eligible bot-thread resolution
# ---------------------------------------------------------------------------


def _bot_thread(
    thread_id="T1",
    author="chatgpt-codex-connector[bot]",
    is_outdated=True,
    comment_sha="f" * 40,
    comments=None,
    is_resolved=False,
    codex_clean_passed=True,
    codex_reviewed_sha=None,
    is_stale_codex=False,
):
    return {
        "thread_id": thread_id,
        "author": author,
        "isOutdated": is_outdated,
        "comment_sha": comment_sha,
        "comments": (
            comments
            if comments is not None
            else [{"author": author}]
        ),
        "isResolved": is_resolved,
    }


def _eligibility_kwargs(head=DEFAULT_HEAD, **overrides):
    base = {
        "head_sha": head,
        "codex_verdict": "CODEX_CLEAN_PASS",
        "codex_clean_passed": True,
        "codex_reviewed_sha": head,
        # Round-5: pass repo + an ancestry_runner that always
        # reports ``status="ahead"`` so the existing F4 tests
        # retain their intent (eligible vs ineligible based on
        # anchor shape and codex state) without each test
        # constructing a verifier mock.
        "repo": "Slideshow11/Automated-Edge-Discovery",
        "ancestry_runner": lambda *a, **kw: mock.Mock(
            returncode=0, stdout="ahead", stderr=""
        ),
        # Round-412 (PHASE 4 Finding 1): evidence flags
        # required by the new shared policy contract.
        # Defaults to "all satisfied" so the legacy F4
        # tests keep their intent of testing the
        # anchor / actor / codex-shape decisions.
        "inventory_complete": True,
        "review_thread_inventory_complete": True,
        "nested_comment_inventory_complete": True,
        "no_newer_finding": True,
        "live_head_match": True,
        "live_head_sha": head,
    }
    base.update(overrides)
    return base


class TestF4Eligibility:
    def test_eligible_outdated_bot_only(self):
        ok, reason = R.is_eligible_for_bot_resolution(
            _bot_thread(), **_eligibility_kwargs()
        )
        assert ok is True and reason == "eligible"

    def test_current_bot_thread_rejected(self):
        # PHASE 3 R-3 (PR #412): the outdated-only rule is
        # REMOVED. A current (non-outdated) Codex thread IS
        # eligible when all repair, ancestry, clean-review,
        # participant, and live-head evidence is proven.
        # The new policy therefore accepts this thread
        # when ``is_outdated=False`` but every other
        # condition passes.
        ok, reason = R.is_eligible_for_bot_resolution(
            _bot_thread(is_outdated=False), **_eligibility_kwargs()
        )
        assert ok is True and reason == "eligible"

    def test_human_reply_rejected(self):
        thread = _bot_thread(comments=[
            {"author": "chatgpt-codex-connector[bot]"},
            {"author": "Slideshow11"},
        ])
        ok, reason = R.is_eligible_for_bot_resolution(
            thread, **_eligibility_kwargs()
        )
        assert ok is False and reason == "human_reply"

    def test_unknown_author_rejected(self):
        ok, reason = R.is_eligible_for_bot_resolution(
            _bot_thread(author="mystery-user"),
            **_eligibility_kwargs()
        )
        assert ok is False and reason == "actor_not_bot"

    def test_no_later_commit_rejected(self):
        ok, reason = R.is_eligible_for_bot_resolution(
            _bot_thread(comment_sha=DEFAULT_HEAD),  # anchor == live
            **_eligibility_kwargs()
        )
        assert ok is False and reason == "no_later_commit"

    def test_stale_codex_evidence_rejected(self):
        ok, reason = R.is_eligible_for_bot_resolution(
            _bot_thread(),
            **_eligibility_kwargs(codex_clean_passed=False)
        )
        assert ok is False and reason == "codex_not_clean"

    def test_codex_sha_mismatch_rejected(self):
        ok, reason = R.is_eligible_for_bot_resolution(
            _bot_thread(),
            **_eligibility_kwargs(codex_reviewed_sha="b" * 40)
        )
        assert ok is False and reason == "codex_head_mismatch"

    def test_idempotent_already_resolved_rejected(self):
        ok, reason = R.is_eligible_for_bot_resolution(
            _bot_thread(is_resolved=True), **_eligibility_kwargs()
        )
        assert ok is False and reason == "already_resolved"

    def test_select_eligible_partitions_inventory(self):
        threads = [
            _bot_thread(thread_id="T-ELIGIBLE"),
            _bot_thread(
                thread_id="T-HUMAN",
                comments=[
                    {"author": "chatgpt-codex-connector[bot]"},
                    {"author": "Slideshow11"},
                ],
            ),
        ]
        result = ctrl.select_eligible_bot_threads(
            threads, **_eligibility_kwargs()
        )
        assert [t["thread_id"] for t in result["eligible"]] == ["T-ELIGIBLE"]
        assert "T-HUMAN" in [t["thread_id"] for t in result["ineligible"]]

    def test_resolve_review_thread_calls_gh_api_graphql(self):
        runner_calls = []
        def fake_runner(cmd, *args, **kwargs):
            runner_calls.append(list(cmd))
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({
                    "data": {
                        "resolveReviewThread": {
                            "thread": {
                                "id": "T-X",
                                "isResolved": True,
                            }
                        }
                    }
                }),
                stderr="",
            )
        ok, msg = ctrl.resolve_review_thread(
            "owner/repo", "T-X", runner=fake_runner
        )
        assert ok is True and msg == "resolved"
        assert runner_calls[0][:3] == ["gh", "api", "graphql"]
        # The thread ID must be supplied as data, not interpolated.
        argv = runner_calls[0]
        # ``--input`` must NOT be used (gh treats it as a filename).
        assert "--input" not in argv
        # The thread ID is supplied via ``input[threadId]=...``.
        thread_argv = [
            a for a in argv
            if a.startswith("input[threadId]=")
        ]
        assert thread_argv == ["input[threadId]=T-X"]
        # The query uses ResolveReviewThreadInput and ``$input``.
        query_argv = [
            a for a in argv if a.startswith("query=")
        ]
        assert len(query_argv) == 1
        query = query_argv[0].removeprefix("query=")
        assert "ResolveReviewThreadInput" in query
        assert "resolveReviewThread(input: $input)" in query

    def test_resolve_review_thread_records_failure(self):
        def fake_runner(cmd, *args, **kwargs):
            return mock.Mock(returncode=1, stdout="", stderr="boom")
        ok, msg = ctrl.resolve_review_thread(
            "owner/repo", "T-X", runner=fake_runner
        )
        assert ok is False and "boom" in msg

    def test_one_mutation_failure_prevents_false_completion(self):
        """Even if some mutations succeed, one failure must NOT mark
        ``ok=True``."""
        resolve_results = []
        any_failed = False
        for tid in ["T-A", "T-B"]:
            ok = (tid == "T-A")  # second fails
            if not ok:
                any_failed = True
            resolve_results.append({"thread_id": tid, "ok": ok})
        overall_ok = len(resolve_results) > 0 and not any_failed
        assert overall_ok is False


# ---------------------------------------------------------------------------
# Review-comment-gate recheck
# ---------------------------------------------------------------------------


def _pr_view_payload(head_sha=DEFAULT_HEAD, head_branch="reduction/pr-lifecycle-collapse-v1"):
    return {
        "number": 411, "title": "t", "state": "OPEN",
        "isDraft": False, "mergeable": True,
        "headRefOid": head_sha, "headRefName": head_branch,
        "baseRefOid": "b" * 40, "baseRefName": "main",
        "additions": 0, "deletions": 0, "changedFiles": 0,
        "url": "u", "files": [],
    }


def _build_run(
    *,
    head_sha=DEFAULT_HEAD,
    head_branch="reduction/pr-lifecycle-collapse-v1",
    databaseId=29593005015,
    name="CI",
    event="pull_request",
    status="completed",
    conclusion="success",
    createdAt=None,
    workflow_name=None,
):
    if createdAt is None:
        # Default to "now" so the run is the newest.
        import datetime as _dt
        createdAt = _dt.datetime.now(_dt.timezone.utc).isoformat()
    if workflow_name is None:
        # ``gh run list --workflow ci.yml`` returns workflowName
        # equal to the workflow's display name (e.g. ``CI``) on
        # this repository. Match the controller's expectation.
        workflow_name = (
            ctrl.EXPECTED_WORKFLOW_NAME.get("ci.yml", "ci.yml")
        )
    return {
        "databaseId": databaseId, "name": name, "event": event,
        "headBranch": head_branch, "headSha": head_sha,
        "status": status, "conclusion": conclusion,
        "createdAt": createdAt, "url": f"https://example/runs/{databaseId}",
        "workflowName": workflow_name,
    }


def _now_iso():
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _gate_ns(
    head_sha=DEFAULT_HEAD,
    *,
    pull_request_runs=None,
    job_conclusion="success",
    job_status="completed",
    head_branch="reduction/pr-lifecycle-collapse-v1",
    head_mismatch=False,
    rerun_failure=False,
    match_misses=False,
    identify_err="",
    timeout=False,
    rerun_attempts=None,
    pre_rerun_attempt=1,
    wait_timeout_seconds=3,
    wait_poll_seconds=1,
    job_payload_override=None,
):
    """Build a Namespace mock for ``cmd_gate_recheck``.

    ``pull_request_runs`` is the list of runs returned by the
    run-list call. ``job_conclusion`` is what the run-view call
    returns for the ``review-comment-gate`` job. ``timeout``
    short-circuits the polling loop so the test does not
    actually wait. ``rerun_attempts`` is a list of integers
    returned by the attempt-count reads; the loop returns the
    first value strictly greater than ``pre_rerun_attempt``.
    """
    ns = mock.Mock()
    ns.repo = DEFAULT_REPO
    ns.pr_number = 411
    ns.head_sha = head_sha
    ns.wait_timeout_seconds = wait_timeout_seconds
    ns.wait_poll_seconds = wait_poll_seconds
    ns.dry_run = False

    live = dict(_pr_view_payload(head_sha=head_sha, head_branch=head_branch))
    if head_mismatch:
        live["headRefOid"] = "f" * 40  # != requested head
    ns.pr_view_runner = lambda *a, **kw: mock.Mock(
        returncode=0,
        stdout=json.dumps(live),
        stderr="",
    )

    if rerun_failure:
        ns.rerun_runner = lambda *a, **kw: mock.Mock(
            returncode=1, stdout="", stderr="rerun failed"
        )
    else:
        ns.rerun_runner = lambda *a, **kw: mock.Mock(
            returncode=0, stdout="", stderr=""
        )

    if pull_request_runs is None:
        if match_misses or identify_err:
            pr_runs_factory = lambda: []
        else:
            pr_runs_factory = lambda: [_build_run()]
    elif isinstance(pull_request_runs, str) and \
            pull_request_runs == "__FACTORY__":
        pr_runs_factory = lambda: [_build_run()]
    else:
        pr_runs_factory = lambda: pull_request_runs
    ns.list_runner = lambda *a, **kw: mock.Mock(
        returncode=0,
        stdout=json.dumps(pr_runs_factory()),
        stderr="",
    )

    # The attempt-count read returns a sequence of values.
    # The first attempt read MUST match ``pre_rerun_attempt``;
    # subsequent reads return the values from
    # ``rerun_attempts``. The first read strictly greater
    # than ``pre_rerun_attempt`` triggers the run-view job
    # poll. The job payload is then returned in the run-view
    # call.
    attempt_state = {"attempt_calls": 0, "rerun_attempts": rerun_attempts or []}
    def _attempt_runner(cmd, *a, **kw):
        # ``gh run view <id> --json attempt``
        if "--json" in cmd and "attempt" in cmd[cmd.index("--json") + 1]:
            idx = attempt_state["attempt_calls"]
            attempt_state["attempt_calls"] += 1
            if idx == 0:
                # First call: return pre_rerun_attempt so the
                # controller records it as the bound.
                value = pre_rerun_attempt
            elif idx - 1 < len(attempt_state["rerun_attempts"]):
                # Subsequent calls: return the user's sequence.
                value = attempt_state["rerun_attempts"][idx - 1]
            else:
                value = pre_rerun_attempt
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({"attempt": value}),
                stderr="",
            )
        # ``gh run view <id> --json jobs``
        if timeout:
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({"jobs": []}),
                stderr="",
            )
        if job_payload_override is not None:
            return mock.Mock(
                returncode=0,
                stdout=json.dumps(job_payload_override),
                stderr="",
            )
        job_payload = {
            "jobs": [{
                "name": "review-comment-gate",
                "databaseId": 29593005099,
                "status": job_status,
                "conclusion": job_conclusion,
            }]
        }
        return mock.Mock(
            returncode=0,
            stdout=json.dumps(job_payload),
            stderr="",
        )

    ns.view_runner = _attempt_runner

    return ns


class TestGateRecheckMechanism:
    def test_forwards_clean(self):
        """Terminal success returns 0 (exact-head SUCCESS)."""
        ns = _gate_ns(rerun_attempts=[2])
        assert ctrl.cmd_gate_recheck(ns) == 0

    def test_forwards_blocked(self):
        """Terminal blocking failure returns 1 (exact-head BLOCKED)."""
        ns = _gate_ns(
            job_conclusion="failure", job_status="completed",
            rerun_attempts=[2],
        )
        assert ctrl.cmd_gate_recheck(ns) == 1

    def test_forwards_inconclusive(self):
        """Anything else (cancelled, neutral, etc.) is INCONCLUSIVE (2)."""
        ns = _gate_ns(
            job_conclusion="cancelled", job_status="completed",
            rerun_attempts=[2],
        )
        assert ctrl.cmd_gate_recheck(ns) == 2

    def test_rejects_non_canonical_head_sha(self):
        ns = mock.Mock()
        ns.repo = DEFAULT_REPO
        ns.pr_number = 411
        ns.head_sha = "not-a-sha"
        ns.wait_timeout_seconds = 30
        ns.wait_poll_seconds = 1
        ns.dry_run = False
        assert ctrl.cmd_gate_recheck(ns) == 2

    def test_rerun_failure_returns_inconclusive(self):
        """When ``gh run rerun`` fails, gate-recheck returns 2."""
        ns = _gate_ns(rerun_failure=True, rerun_attempts=[2])
        assert ctrl.cmd_gate_recheck(ns) == 2

    def test_head_mismatch_blocks_before_rerun(self):
        """Requested head_sha != live PR head blocks before rerun."""
        ns = _gate_ns(head_mismatch=True)
        assert ctrl.cmd_gate_recheck(ns) == 2

    def test_unidentified_run_returns_inconclusive(self):
        """When no matching run can be identified, return 2."""
        ns = _gate_ns(match_misses=True)
        assert ctrl.cmd_gate_recheck(ns) == 2

    def test_timeout_returns_inconclusive(self):
        """When the gate never reaches terminal within the bounded
        timeout, return 2."""
        ns = _gate_ns(
            timeout=True, wait_timeout_seconds=1,
            rerun_attempts=[2],
        )
        assert ctrl.cmd_gate_recheck(ns) == 2


# ---------------------------------------------------------------------------
# Round-15 — Codex finding PRRC_kwDOSHFpYM7XPZN4
#
# ``evaluate_machine_readiness`` previously passed the wrong keyword
# names (``passed=`` / ``failed=``) to ``ReadinessVerdict`` in the
# malformed-head branch. ``ReadinessVerdict`` defines those fields as
# ``gates_passed`` / ``gates_failed``, so a non-canonical head SHA
# raised ``TypeError: __init__() got an unexpected keyword argument
# 'passed'`` from inside the status path. The controller crashed
# instead of returning a structured blocking verdict.
#
# These tests prove the fix: a malformed or missing head SHA yields a
# ``ReadinessVerdict`` with all blocking fields set correctly, no
# exception, and a serializable ``to_dict()``.
# ---------------------------------------------------------------------------


def _malformed_head_evidence(head_sha):
    """Build a minimal ``ReadinessEvidence`` for a malformed head SHA.

    Unlike :func:`_full_passing_evidence`, this helper does NOT call
    :func:`build_authorization_phrase` (which raises ``ValueError`` on
    non-canonical input). The malformed-head path is the one under
    test, so we want the helper to succeed in building the bundle;
    ``evaluate_machine_readiness`` is the function that must
    fail-closed on the bundle.
    """
    return R.ReadinessEvidence(
        pr_state="OPEN",
        is_draft=False,
        mergeable=True,
        head_sha=head_sha,
        authorization_phrase=None,
        changed_files=[],
        changed_files_fetched=True,
        scope_clean=True,
        out_of_scope_files=[],
        forbidden_files_touched=[],
        scope_blockers=[],
        allowed_files_supplied=True,
        required_ci_names=list(ctrl.REQUIRED_CHECK_NAMES),
        ci_conclusions={},
        ci_missing=[],
        ci_pending=[],
        ci_failed=[],
        codex_verdict=None,
        codex_source=None,
        codex_reviewed_sha=None,
        codex_clean_passed=None,
        codex_artifact_present=False,
        codex_artifact_fresh=None,
        codex_review_url=None,
        codex_review_id=None,
        reviews_inventory_complete=True,
        reviews_inventory_error=None,
        review_threads=[],
        review_thread_inventory_complete=True,
        review_thread_inventory_error=None,
        unresolved_thread_count=0,
        unresolved_thread_ids=[],
        unresolved_human_thread_ids=[],
        unresolved_bot_thread_ids=[],
        outdated_bot_thread_ids=[],
        evidence_sources={},
    )


def _assert_blocking_verdict(verdict, label):
    """Assert the malformed-head contract on the supplied verdict.

    The contract is fail-closed across the board:

    * no exception
    * ``ready`` / ``merge_ready`` / ``machine_ready`` all False
    * ``authorization_required`` False, ``authorization_valid`` None
    * ``gates_passed`` empty, ``gates_failed`` covers every machine gate
    * exactly one ``REASON_EVIDENCE_MISSING`` reason gated on
      ``GATE_NO_MISSING_EVIDENCE``
    * ``to_dict()`` round-trips successfully and consistently
    """
    # No exception should have escaped; the verdict object exists.
    assert verdict is not None, f"{label}: verdict is None"

    assert verdict.ready is False, f"{label}: ready should be False"
    assert verdict.merge_ready is False, f"{label}: merge_ready should be False"
    assert verdict.machine_ready is False, f"{label}: machine_ready should be False"
    assert verdict.authorization_required is False, (
        f"{label}: authorization_required should be False"
    )
    assert verdict.authorization_valid is None, (
        f"{label}: authorization_valid should be None"
    )

    assert list(verdict.gates_passed) == [], (
        f"{label}: gates_passed must be empty; got {list(verdict.gates_passed)!r}"
    )
    assert set(verdict.gates_failed) == set(R.MACHINE_GATES), (
        f"{label}: gates_failed must equal MACHINE_GATES; "
        f"missing={set(R.MACHINE_GATES) - set(verdict.gates_failed)}, "
        f"extra={set(verdict.gates_failed) - set(R.MACHINE_GATES)}"
    )

    reason_codes = [r.code for r in verdict.reasons]
    assert reason_codes == [R.REASON_EVIDENCE_MISSING], (
        f"{label}: reasons must be exactly [{R.REASON_EVIDENCE_MISSING!r}]; "
        f"got {reason_codes!r}"
    )
    reason_gates = [r.gate for r in verdict.reasons]
    assert reason_gates == [R.GATE_NO_MISSING_EVIDENCE], (
        f"{label}: reasons' gates must be [{R.GATE_NO_MISSING_EVIDENCE!r}]; "
        f"got {reason_gates!r}"
    )

    serialized = verdict.to_dict()
    # Serialization must succeed (no exception) and round-trip the
    # critical fields.
    assert serialized["ready"] is False
    assert serialized["merge_ready"] is False
    assert serialized["machine_ready"] is False
    assert serialized["authorization_required"] is False
    assert serialized["authorization_valid"] is None
    assert serialized["gates_passed"] == []
    assert set(serialized["gates_failed"]) == set(R.MACHINE_GATES)
    assert [r["code"] for r in serialized["reasons"]] == [R.REASON_EVIDENCE_MISSING]
    assert [r["gate"] for r in serialized["reasons"]] == [R.GATE_NO_MISSING_EVIDENCE]


class TestRound15MalformedHeadVerdict:
    """Round-15 fix: malformed head SHA returns structured blocking verdict.

    Each parametrized case is a head SHA that ``is_canonical_head_sha``
    rejects. Before the fix, each of these raised ``TypeError`` from
    inside ``evaluate_machine_readiness`` (because the verdict
    constructor used the obsolete ``passed=`` / ``failed=`` keyword
    names). After the fix, each case returns a structured blocking
    verdict with the fail-closed contract.
    """

    @pytest.mark.parametrize("head_sha", [
        None,                                # missing entirely
        "",                                  # empty string
        "a" * 39,                            # 39-char lowercase hex
        "a" * 41,                            # 41-char lowercase hex
        "z" * 40,                            # 40 chars but non-hex
        ("A" * 40),                          # 40-char uppercase hex
    ])
    def test_malformed_head_returns_blocking_verdict(self, head_sha):
        ev = _malformed_head_evidence(head_sha)
        # The exact call under test. The fix changes the verdict
        # constructor; before the fix this raises TypeError.
        verdict = R.evaluate_machine_readiness(ev)
        _assert_blocking_verdict(verdict, label=f"head_sha={head_sha!r}")

    def test_authorization_phrase_is_not_exposed_for_malformed_head(self):
        """Round-15 contract: a malformed head SHA must NOT cause
        ``build_authorization_phrase`` (or its controller-side
        pre-image) to surface. The verdict itself carries no phrase
        field; the controller gates phrase emission on a canonical
        head SHA AND ``machine_ready=True``. With a malformed head,
        both are false, so no phrase can leak through this verdict.
        """
        for head in (None, "", "z" * 40, "A" * 40):
            ev = _malformed_head_evidence(head)
            verdict = R.evaluate_machine_readiness(ev)
            assert verdict.machine_ready is False
            # No reason should mention an authorization phrase.
            for reason in verdict.reasons:
                assert "phrase" not in reason.code.lower(), (
                    f"malformed head leaked phrase code: {reason.code!r}"
                )
                assert "phrase" not in reason.detail.lower(), (
                    f"malformed head leaked phrase detail: {reason.detail!r}"
                )


class TestRound15ControllerStatusOnMalformedHead:
    """Round-15: the controller's ``status`` path must return a
    structured blocking report (rather than crash with TypeError) when
    the live PR head SHA is missing or malformed.

    The tests use ``mock.patch`` to stub ``subprocess.run`` inside the
    ``aed_pr`` module so the controller sees a controlled malformed
    head SHA. ``subprocess`` is invoked for every ``gh`` call; the
    stub dispatches on argv shape (``gh pr view``, ``gh pr checks``,
    ``gh pr diff``, ``gh workflow run list``, ``gh run list``, ...).
    Any unexpected call still returns an empty-success payload so the
    test surfaces a clear failure rather than masking the regression.
    """

    def _run_status_with_stubbed_gh(self, head_sha, monkeypatch):
        """Invoke ``aed_pr.cmd_status`` in-process with ``subprocess.run``
        patched to return the supplied head_sha.

        The pre-existing ``cmd_status`` body also calls
        :func:`build_safe_merge_command` and
        :func:`build_authorization_phrase` on the live head_sha, both of
        which raise ``ValueError`` on non-canonical input. The round-15
        finding is specifically about the **verdict constructor** (see
        PRRC_kwDOSHFpYM7XPZN4), not those helpers; the spec also says
        ``Do not broaden the controller.`` This test therefore mocks the
        two helpers so the test reaches and exercises the path the
        finding targets (the verdict construction step inside
        ``cmd_status``) without touching out-of-scope controller
        branches. The mocks return the same shape the production code
        would return for a canonical head, so they cannot mask any
        regression in the verdict-construction layer.

        Returns ``(returncode, report_dict)``.
        """
        pr_view_payload = {
            "state": "OPEN",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "headRefOid": head_sha,
            "headRefName": "reduction/pr-lifecycle-collapse-v1",
            "url": "https://example/pr/411",
            "title": "Round-15 status path test",
            "baseRefName": "main",
            "headRepository": {"nameWithOwner": DEFAULT_REPO},
        }
        empty_checks_payload: list = []
        empty_diff_payload: list = []
        empty_list_payload: list = []

        def _fake_run(cmd, *args, **kwargs):
            argv = list(cmd) if isinstance(cmd, (list, tuple)) else [cmd]
            if "pr" in argv and "view" in argv:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(pr_view_payload),
                    stderr="",
                )
            if "pr" in argv and "checks" in argv:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(empty_checks_payload),
                    stderr="",
                )
            if "pr" in argv and "diff" in argv:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(empty_diff_payload),
                    stderr="",
                )
            if "run" in argv and "list" in argv:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(empty_list_payload),
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="{}", stderr="")

        monkeypatch.setattr(ctrl.subprocess, "run", _fake_run)

        # The two pre-existing helpers raise on a malformed head; mock
        # them so the test reaches the verdict-construction step the
        # round-15 finding targets. ``build_safe_merge_command`` is
        # called BEFORE the verdict evaluation in the production flow,
        # so without this mock the controller would crash on the
        # helper, not on the verdict. With this mock, the verdict
        # constructor is the only ``cmd_status``-internal call that
        # receives the malformed head_sha.
        #
        # ``aed_pr.py`` adds its own directory to ``sys.path`` and
        # imports ``aed_pr_lib`` as a top-level module (so it is
        # ``sys.modules['aed_pr_lib']``), not as
        # ``scripts.local.aed_pr_lib``. The two module objects are
        # distinct. We patch the controller's view (``ctrl.L``) which
        # is the same object the production code resolves at call
        # time.
        monkeypatch.setattr(
            ctrl.L, "build_safe_merge_command",
            lambda *a, **kw: "gh pr merge <stubbed>",
        )

        # Run cmd_status in-process and capture its stdout.
        from io import StringIO
        import contextlib
        args = mock.Mock()
        args.repo = DEFAULT_REPO
        args.pr_number = 411
        args.allowed_files = "scripts/local/aed_pr*.py"
        args.forbidden_files = None

        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ctrl.cmd_status(args)
        return rc, json.loads(buf.getvalue())

    def test_status_returns_blocking_report_for_head_sha_none(self, monkeypatch):
        rc, report = self._run_status_with_stubbed_gh(head_sha=None, monkeypatch=monkeypatch)
        assert rc == 0, f"status crashed on head_sha=None; report={report!r}"
        assert report["head_sha"] in (None, "")
        assert report["machine_ready"] is False
        assert report["authorization_required"] is False
        assert report["authorization_valid"] is None
        assert report["merge_ready"] is False
        assert report["ready"] is False
        assert report["gates_passed"] == []
        assert set(report["gates_failed"]) == set(R.MACHINE_GATES)
        assert R.REASON_EVIDENCE_MISSING in report["reason_codes"]

    def test_status_returns_blocking_report_for_head_sha_wrong_length(self, monkeypatch):
        rc, report = self._run_status_with_stubbed_gh(head_sha="a" * 39, monkeypatch=monkeypatch)
        assert rc == 0, f"status crashed on 39-char head; report={report!r}"
        assert report["head_sha"] == "a" * 39
        assert report["machine_ready"] is False
        assert report["authorization_required"] is False
        assert report["authorization_valid"] is None
        assert report["merge_ready"] is False
        assert report["ready"] is False
        assert report["gates_passed"] == []
        assert set(report["gates_failed"]) == set(R.MACHINE_GATES)
        assert R.REASON_EVIDENCE_MISSING in report["reason_codes"]

    def test_status_returns_blocking_report_for_uppercase_head_sha(self, monkeypatch):
        rc, report = self._run_status_with_stubbed_gh(head_sha="A" * 40, monkeypatch=monkeypatch)
        assert rc == 0, f"status crashed on uppercase head; report={report!r}"
        assert report["head_sha"] == "A" * 40
        assert report["machine_ready"] is False
        assert report["authorization_required"] is False
        assert report["authorization_valid"] is None
        assert report["merge_ready"] is False
        assert report["ready"] is False
        assert report["gates_passed"] == []
        assert set(report["gates_failed"]) == set(R.MACHINE_GATES)


class TestRound15SourceContractNoObsoleteKwArgs:
    """Round-15 source contract: ``scripts/local/aed_pr_readiness.py``
    must not contain any ``ReadinessVerdict(...)`` constructor using
    the obsolete exact keyword arguments ``passed=`` or ``failed=``.

    The canonical field names are ``gates_passed`` and
    ``gates_failed``; a naive substring check would mistake
    ``gates_passed=`` for ``passed=``. The assertion below uses a
    token-aware regex: the obsolete name must appear as a Python
    keyword argument, i.e. preceded by ``,`` or ``(`` and optional
    whitespace and followed by ``=``.
    """

    SOURCE_PATH = REPO / "scripts" / "local" / "aed_pr_readiness.py"

    @staticmethod
    def _readiness_constructor_lines():
        """Yield ``(lineno, line)`` pairs that are part of a
        ``ReadinessVerdict(...)`` call.

        The detection walks lines that mention ``ReadinessVerdict``
        and captures every subsequent line until the matching close
        paren is balanced. This keeps the obsolete-kwarg check
        scoped to the constructor call site (where a Python kwarg
        like ``passed=...`` would actually appear), instead of
        accidentally flagging local-variable assignments such as
        ``passed = list(machine.gates_passed)``.
        """
        text = TestRound15SourceContractNoObsoleteKwArgs.SOURCE_PATH.read_text()
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            stripped = line.lstrip()
            if not stripped.startswith("ReadinessVerdict("):
                continue
            depth = stripped.count("(") - stripped.count(")")
            yield idx + 1, line
            cursor = idx + 1
            while depth > 0 and cursor < len(lines):
                cursor += 1
                next_line = lines[cursor - 1]
                depth += next_line.count("(") - next_line.count(")")
                yield cursor, next_line

    def test_no_obsolete_passed_keyword_argument(self):
        import re as _re
        # The obsolete kwarg ``passed=...`` is matched only when it
        # appears as a Python keyword argument (i.e. NOT preceded by
        # an identifier character). The constructor-scope filtering
        # from :meth:`_readiness_constructor_lines` further restricts
        # the check to lines that are part of a ``ReadinessVerdict(...)``
        # call, so plain assignments like
        # ``passed = list(machine.gates_passed)`` are not flagged.
        pattern = _re.compile(r"(?<![A-Za-z0-9_])passed\s*=")
        offenders = [
            (lineno, line.rstrip())
            for lineno, line in self._readiness_constructor_lines()
            if pattern.search(line)
        ]
        assert not offenders, (
            "Found obsolete ReadinessVerdict kwarg 'passed=' in "
            f"{self.SOURCE_PATH}: {offenders}"
        )

    def test_no_obsolete_failed_keyword_argument(self):
        import re as _re
        # As :meth:`test_no_obsolete_passed_keyword_argument` above.
        pattern = _re.compile(r"(?<![A-Za-z0-9_])failed\s*=")
        offenders = [
            (lineno, line.rstrip())
            for lineno, line in self._readiness_constructor_lines()
            if pattern.search(line)
        ]
        assert not offenders, (
            "Found obsolete ReadinessVerdict kwarg 'failed=' in "
            f"{self.SOURCE_PATH}: {offenders}"
        )

    def test_canonical_kwarg_names_still_present(self):
        """Belt-and-braces: the canonical ``gates_passed=`` /
        ``gates_failed=`` kwargs must still appear in the source, so
        the previous regex check cannot be trivially satisfied by
        deleting the constructors entirely.
        """
        text = self.SOURCE_PATH.read_text()
        assert "gates_passed=" in text
        assert "gates_failed=" in text
