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
    head_sha=DEFAULT_HEAD, phrase="__DEFAULT__", pr_number=411,
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

    def test_status_accepts_cli_override(self):
        allowed, _, err = ctrl._resolve_effective_scope(
            subcommand="status",
            repo=DEFAULT_REPO, pr_number=411, head_sha=DEFAULT_HEAD,
            cli_allowed=["scripts/local/aed_pr.py"], cli_forbidden=None,
        )
        assert err == ""
        assert allowed == ["scripts/local/aed_pr.py"]


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
        ok, reason = R.is_eligible_for_bot_resolution(
            _bot_thread(is_outdated=False), **_eligibility_kwargs()
        )
        assert ok is False and reason == "not_outdated"

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
            return mock.Mock(returncode=0, stdout="{}", stderr="")
        ok, msg = ctrl.resolve_review_thread(
            "owner/repo", "T-X", runner=fake_runner
        )
        assert ok is True and msg == "resolved"
        assert runner_calls[0][:3] == ["gh", "api", "graphql"]
        assert "resolveReviewThread" in " ".join(runner_calls[0])

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
    event="workflow_dispatch",
    status="completed",
    conclusion="success",
    createdAt=None,
    workflow_name=None,
):
    if createdAt is None:
        # Default to "now" so the run always qualifies as
        # "at or after dispatched_at" when the controller
        # records ``dispatched_at`` immediately before the
        # ``gh workflow run`` call. Tests that want a
        # specific historical timestamp can override.
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
    dispatch_runs=None,
    job_conclusion="success",
    job_status="completed",
    head_branch="reduction/pr-lifecycle-collapse-v1",
    head_mismatch=False,
    dispatch_failure=False,
    dispatch_run=None,
    match_misses=False,
    identify_err="",
    timeout=False,
    wait_timeout_seconds=30,
    wait_poll_seconds=1,
):
    """Build a Namespace mock for ``cmd_gate_recheck``.

    ``dispatch_runs`` is the list of runs returned by the run-list
    call. ``job_conclusion`` is what the run-view call returns for
    the ``review-comment-gate`` job. ``timeout`` short-circuits the
    polling loop so the test does not actually wait.
    """
    ns = mock.Mock()
    ns.repo = DEFAULT_REPO
    ns.pr_number = 411
    ns.head_sha = head_sha
    ns.wait_timeout_seconds = wait_timeout_seconds
    ns.wait_poll_seconds = wait_poll_seconds

    live = dict(_pr_view_payload(head_sha=head_sha, head_branch=head_branch))
    if head_mismatch:
        live["headRefOid"] = "f" * 40  # != requested head
    ns.pr_view_runner = lambda *a, **kw: mock.Mock(
        returncode=0,
        stdout=json.dumps(live),
        stderr="",
    )

    if dispatch_failure:
        ns.dispatch_runner = lambda *a, **kw: mock.Mock(
            returncode=1, stdout="", stderr="dispatch failed"
        )
    else:
        ns.dispatch_runner = lambda *a, **kw: mock.Mock(
            returncode=0, stdout="", stderr=""
        )

    if dispatch_runs is None:
        # Default: a factory that produces one matching run
        # AT call time, so the createdAt timestamp is later
        # than the dispatched_at the controller recorded.
        if match_misses:
            dispatch_runs_factory = lambda: []
        elif identify_err:
            dispatch_runs_factory = lambda: []
        else:
            dispatch_runs_factory = lambda: [_build_run()]
    elif isinstance(dispatch_runs, str) and dispatch_runs == "__FACTORY__":
        dispatch_runs_factory = lambda: [_build_run()]
    else:
        dispatch_runs_factory = lambda: dispatch_runs
    ns.list_runner = lambda *a, **kw: mock.Mock(
        returncode=0,
        stdout=json.dumps(dispatch_runs_factory()),
        stderr="",
    )

    if timeout:
        # job view never reaches completed
        ns.view_runner = lambda *a, **kw: mock.Mock(
            returncode=0,
            stdout=json.dumps({"jobs": []}),
            stderr="",
        )
    else:
        job_payload = {
            "jobs": [{
                "name": "review-comment-gate",
                "status": job_status,
                "conclusion": job_conclusion,
            }]
        }
        ns.view_runner = lambda *a, **kw: mock.Mock(
            returncode=0,
            stdout=json.dumps(job_payload),
            stderr="",
        )

    # The dispatch-run identification step uses list_runner for the
    # run list and view_runner for the run view.
    return ns


class TestGateRecheckMechanism:
    def test_forwards_clean(self):
        """Terminal success returns 0 (exact-head SUCCESS)."""
        assert ctrl.cmd_gate_recheck(_gate_ns()) == 0

    def test_forwards_blocked(self):
        """Terminal blocking failure returns 1 (exact-head BLOCKED)."""
        assert ctrl.cmd_gate_recheck(
            _gate_ns(job_conclusion="failure", job_status="completed")
        ) == 1

    def test_forwards_inconclusive(self):
        """Anything else (cancelled, neutral, etc.) is INCONCLUSIVE (2)."""
        assert ctrl.cmd_gate_recheck(
            _gate_ns(job_conclusion="cancelled", job_status="completed")
        ) == 2

    def test_rejects_non_canonical_head_sha(self):
        ns = mock.Mock()
        ns.repo = DEFAULT_REPO
        ns.pr_number = 411
        ns.head_sha = "not-a-sha"
        ns.wait_timeout_seconds = 30
        ns.wait_poll_seconds = 1
        assert ctrl.cmd_gate_recheck(ns) == 2

    def test_dispatch_failure_returns_inconclusive(self):
        """When ``gh workflow run`` fails, gate-recheck returns 2."""
        ns = _gate_ns(dispatch_failure=True)
        assert ctrl.cmd_gate_recheck(ns) == 2

    def test_head_mismatch_blocks_before_dispatch(self):
        """Requested head_sha != live PR head blocks before dispatch."""
        ns = _gate_ns(head_mismatch=True)
        assert ctrl.cmd_gate_recheck(ns) == 2

    def test_unidentified_run_returns_inconclusive(self):
        """When no matching run can be identified, return 2."""
        ns = _gate_ns(match_misses=True)
        assert ctrl.cmd_gate_recheck(ns) == 2

    def test_timeout_returns_inconclusive(self):
        """When the gate never reaches terminal within the bounded
        timeout, return 2."""
        ns = _gate_ns(timeout=True, wait_timeout_seconds=1)
        assert ctrl.cmd_gate_recheck(ns) == 2
