"""Tests for the canonical AED PR-lifecycle controller and its shared
readiness evaluator.

This module restores and migrates the load-bearing safety assertions
that lived in the deleted wrappers
(``test_aed_final_gate.py``, ``test_merge_authorization_guard.py``,
``test_merge_readiness_with_phase_ledger.py``, ``test_phase_ledger.py``).
The 12 specific safety conditions required by the PR #411 brief each
have a dedicated test in :class:`TestMergeSafetyGating`:

  1. missing CI blocks merge
  2. failed CI blocks merge
  3. missing scope blocks merge
  4. out-of-scope files block merge
  5. missing Codex blocks merge
  6. stale Codex blocks merge
  7. clean exact-head Codex issue comment passes
  8. clean exact-head formal review passes
  9. unresolved thread blocks merge
 10. thread-fetch failure blocks merge
 11. head movement invalidates authorization
 12. blocked cases never invoke gh pr merge
 13. safe merge invokes exactly one correctly bound command

The controller's CLI surface (status / advance / merge) is exercised
in :class:`TestControllerCLISurface` and
:class:`TestAdvanceBehavior`. The shared ``aed_pr_lib`` and
``aed_pr_readiness`` modules are exercised in their respective classes.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT_AED_PR = REPO / "scripts" / "local" / "aed_pr.py"

sys.path.insert(0, str(REPO))

from scripts.local import aed_pr_lib as L  # noqa: E402
from scripts.local import aed_pr_readiness as R  # noqa: E402
from scripts.local import audit_codex_response_for_pr as CODEX  # noqa: E402
from scripts.local import aed_pr as ctrl_module  # noqa: E402

REQUIRED_CHECK_NAMES = ctrl_module.REQUIRED_CHECK_NAMES


# -----------------------------------------------------------------------------
# in-process main() runner (no subprocess; ABD test isolation requirement)
# -----------------------------------------------------------------------------

def _run_aed_pr(argv):
    """Run scripts/local/aed_pr.py main() in-process and capture output.

    The runner monkey-patches subprocess.run so that no real
    ``gh pr merge`` (or any other gh command) is ever invoked. It
    captures the argv list of every subprocess invocation so tests
    can assert that ``gh pr merge`` is (or is not) called.
    """
    from scripts.local import aed_pr as ctrl

    invoked_argv: list = []
    invoked_env: dict = {}

    class _FakeCompleted:
        def __init__(self, returncode=0, stdout="{}", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(cmd, *args, **kwargs):
        invoked_argv.append(list(cmd))
        invoked_env.update(kwargs.get("env") or {})
        cmd_str = " ".join(str(x) for x in cmd)
        if cmd[:3] == ["gh", "pr", "view"] and "--json" in cmd:
            fields_idx = cmd.index("--json") + 1
            fields = cmd[fields_idx].split(",")
            payload: dict = {
                "number": 411, "title": "t", "state": "OPEN",
                "isDraft": False, "mergeable": True,
                "headRefOid": "a" * 40, "baseRefOid": "b" * 40,
                "baseRefName": "main", "additions": 0, "deletions": 0,
                "changedFiles": 0, "url": "u", "files": [],
            }
            for f in ("files",):
                if f not in fields:
                    payload.pop(f, None)
            if "files" in fields:
                payload["files"] = []
            return _FakeCompleted(returncode=0, stdout=json.dumps(payload))
        if cmd[:3] == ["gh", "api"] and "pulls/" in cmd[3]:
            return _FakeCompleted(
                returncode=0,
                stdout=json.dumps({
                    "head": {"sha": "a" * 40},
                    "mergeable": True,
                    "merge_state_status": "clean",
                    "state": "open",
                    "comments": [],
                    "review_comments": [],
                }),
            )
        if cmd[:3] == ["gh", "api"] and "issues/" in cmd[3] and "comments" in cmd[3]:
            return _FakeCompleted(returncode=0, stdout="[]")
        if cmd[:3] == ["gh", "api"] and "issues/comments" in cmd[3]:
            return _FakeCompleted(returncode=0, stdout="[]")
        if cmd[:3] == ["gh", "api"] and "reviews" in cmd[3]:
            return _FakeCompleted(returncode=0, stdout="[]")
        if cmd[:3] == ["gh", "run", "list"]:
            return _FakeCompleted(returncode=0, stdout="[]")
        if cmd[:3] == ["gh", "api"] and "reviewthreads" in cmd[3]:
            return _FakeCompleted(returncode=0, stdout="{}")
        if cmd[:3] == ["gh", "api"] and "pulls/" in cmd[3] and "review-thread" in cmd[3]:
            return _FakeCompleted(returncode=0, stdout="{}")
        if cmd[:3] == ["gh", "pr", "ready"]:
            return _FakeCompleted(returncode=0, stdout="")
        if cmd[:3] == ["gh", "pr", "merge"]:
            return _FakeCompleted(returncode=0, stdout="merged\n", stderr="")
        return _FakeCompleted(returncode=0, stdout="", stderr="")

    old_argv = sys.argv
    sys.argv = ["aed_pr.py"] + list(argv)
    buf_out, buf_err = io.StringIO(), io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = buf_out, buf_err
    code = 0
    try:
        with mock.patch.object(subprocess, "run", side_effect=_fake_run):
            code = ctrl.main(sys.argv[1:])
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        sys.argv = old_argv

    return code, buf_out.getvalue(), buf_err.getvalue(), invoked_argv


# Round-2 fix: the in-process controller requires explicit scope via
# the new ``--allowed-files`` flag. Tests that exercise the merge path
# must therefore pass a scope policy through this constant.
DEFAULT_TEST_ALLOWED_FILES = ["scripts/local/aed_pr*.py", "tests/test_aed_pr*.py", "docs/aed_pr*.md"]


# -----------------------------------------------------------------------------
# Helpers: build a passing evidence bundle
# -----------------------------------------------------------------------------

def _passing_evidence(head_sha="a" * 40, phrase=None, pr_number=411):
    """Build a ReadinessEvidence bundle where every gate passes.

    Used as the baseline; individual tests mutate one field to make
    one gate fail.
    """
    if phrase is None:
        phrase = L.build_authorization_phrase(pr_number, head_sha)
    ev = R.ReadinessEvidence(
        pr_state="OPEN",
        is_draft=False,
        mergeable=True,
        head_sha=head_sha,
        authorization_phrase=phrase,
        changed_files=["scripts/local/aed_pr.py"],
        changed_files_fetched=True,
        scope_clean=True,
        out_of_scope_files=[],
        forbidden_files_touched=[],
        scope_blockers=[],
        allowed_files_supplied=True,
        required_ci_names=list(REQUIRED_CHECK_NAMES),
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


# -----------------------------------------------------------------------------
# SHA + phrase enforcement
# -----------------------------------------------------------------------------

class TestShaEnforcement:
    def test_is_full_sha_accepts_40_hex(self):
        assert L.is_full_sha("0" * 40)
        assert L.is_full_sha("a" * 40)
        assert L.is_full_sha("0123456789abcdef0123456789abcdef01234567")

    def test_is_full_sha_rejects_short_prefix(self):
        assert not L.is_full_sha("a" * 39)
        assert not L.is_full_sha("a" * 7)

    def test_is_full_sha_rejects_non_hex(self):
        assert not L.is_full_sha("g" * 40)
        assert not L.is_full_sha("Z" * 40)

    def test_is_full_sha_rejects_uppercase(self):
        assert not L.is_full_sha("A" * 40)

    def test_extract_full_sha_from_phrase_returns_40_only(self):
        phrase = (
            "I confirm merge PR #410 at "
            "0123456789abcdef0123456789abcdef01234567 "
            "using final-head reviewed clean state."
        )
        assert L.extract_full_sha_from_phrase(phrase) == (
            "0123456789abcdef0123456789abcdef01234567"
        )

    def test_extract_full_sha_rejects_short_sha(self):
        phrase = (
            "I confirm merge PR #410 at "
            "0123456789abcdef0123456789abcdef0123456 "
            "using final-head reviewed clean state."
        )
        assert L.extract_full_sha_from_phrase(phrase) is None


class TestAuthorizationPhrase:
    def test_build_authorization_phrase_shape(self):
        phrase = L.build_authorization_phrase(410, "0" * 40)
        assert phrase == (
            "I confirm merge PR #410 at 0000000000000000000000000000000000000000 "
            "using final-head reviewed clean state."
        )

    def test_build_rejects_short_sha(self):
        with pytest.raises(ValueError):
            L.build_authorization_phrase(410, "a" * 39)

    def test_build_rejects_non_int_pr_number(self):
        with pytest.raises(ValueError):
            L.build_authorization_phrase("not-an-int", "a" * 40)

    def test_is_valid_authorization_phrase_byte_match(self):
        phrase = L.build_authorization_phrase(410, "a" * 40)
        assert L.is_valid_authorization_phrase(phrase, 410, "a" * 40) is True

    def test_is_valid_authorization_phrase_short_sha_rejects(self):
        phrase = (
            "I confirm merge PR #410 at a using final-head reviewed clean state."
        )
        assert L.is_valid_authorization_phrase(phrase, 410, "a" * 40) is False

    def test_is_valid_authorization_phrase_stale_head(self):
        phrase = L.build_authorization_phrase(410, "a" * 40)
        assert L.is_valid_authorization_phrase(phrase, 410, "b" * 40) is False

    def test_is_valid_authorization_phrase_whitespace_strict(self):
        canonical = L.build_authorization_phrase(410, "a" * 40)
        assert L.is_valid_authorization_phrase(canonical + " ", 410, "a" * 40) is False


class TestSafeMergeCommand:
    PR = 411
    REPO = "Slideshow11/Automated-Edge-Discovery"
    HEAD = "0" * 40

    def test_exact_safe_merge_argv(self):
        import shlex
        cmd = L.build_safe_merge_command(self.PR, self.REPO, self.HEAD)
        argv = shlex.split(cmd)
        assert argv[0:3] == ["gh", "pr", "merge"]
        assert "--repo" in argv
        assert "Slideshow11/Automated-Edge-Discovery" in argv
        assert "--squash" in argv
        assert "--delete-branch" in argv
        assert "--match-head-commit" in argv
        assert self.HEAD in argv
        assert "--admin" not in argv
        assert "--auto" not in argv

    def test_safe_merge_short_sha_rejected(self):
        with pytest.raises(ValueError):
            L.build_safe_merge_command(self.PR, self.REPO, "a" * 39)

    def test_safe_merge_bad_repo_rejected(self):
        with pytest.raises(ValueError):
            L.build_safe_merge_command(self.PR, "no-slash", self.HEAD)


class TestArgvSafety:
    def test_argv_is_safe_rejects_admin(self):
        assert L.argv_is_safe(["gh", "pr", "merge", "1", "--admin"]) is False

    def test_argv_is_safe_rejects_admin_in_string_arg(self):
        assert L.argv_is_safe(["gh", "pr", "merge", "1 --admin"]) is False

    def test_argv_is_safe_rejects_auto(self):
        assert L.argv_is_safe(["gh", "pr", "merge", "1", "--auto"]) is False
        assert L.argv_is_safe(["gh", "pr", "merge", "1", "--auto=yes"]) is False

    def test_argv_is_safe_accepts_clean(self):
        argv = [
            "gh", "pr", "merge", "1",
            "--repo", "owner/name",
            "--squash", "--delete-branch",
            "--match-head-commit", "a" * 40,
        ]
        assert L.argv_is_safe(argv) is True

    def test_reject_admin_argv_raises(self):
        with pytest.raises(ValueError):
            L.reject_admin_argv(["gh", "pr", "merge", "--admin"])


# -----------------------------------------------------------------------------
# Readiness evaluator: each of the 12 gates
# -----------------------------------------------------------------------------

class TestReadinessAllPass:
    def test_all_passing_evidence_yields_ready(self):
        ev = _passing_evidence()
        v = R.evaluate_readiness(ev)
        assert v.ready is True
        assert v.gates_failed == []
        # All MACHINE_GATES pass; the authorization gate (gate 4) is also
        # recorded in gates_passed because a valid phrase was supplied.
        assert set(v.gates_passed) == set(R.ALL_GATES)
        assert v.reasons == []
        assert v.machine_ready is True
        assert v.authorization_valid is True
        assert v.merge_ready is True


class TestGateCIRequired:
    def test_missing_ci_blocks_merge(self):
        ev = _passing_evidence()
        ev.ci_missing = ["test (3.11)"]
        ev.ci_conclusions = {
            "validator": "SUCCESS", "governance-validators": "SUCCESS",
            "pr-gate-live-smoke": "SUCCESS", "review-comment-gate": "SUCCESS",
        }
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_CI_MISSING in {r.code for r in v.reasons}
        assert R.GATE_CI_PRESENT in v.gates_failed

    def test_failed_ci_blocks_merge(self):
        ev = _passing_evidence()
        ev.ci_failed = ["validator"]
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_CI_FAILED in {r.code for r in v.reasons}
        assert R.GATE_CI_PRESENT in v.gates_failed

    def test_pending_ci_blocks_merge(self):
        ev = _passing_evidence()
        ev.ci_pending = ["test (3.11)"]
        ev.ci_conclusions = {
            "validator": "SUCCESS", "governance-validators": "SUCCESS",
            "pr-gate-live-smoke": "SUCCESS", "review-comment-gate": "SUCCESS",
        }
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_CI_PENDING in {r.code for r in v.reasons}
        assert R.GATE_CI_PRESENT in v.gates_failed


class TestGateScope:
    def test_missing_scope_blocks_merge(self):
        ev = _passing_evidence()
        ev.scope_clean = None
        ev.changed_files_fetched = False
        ev.changed_files = None
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.GATE_SCOPE_CLEAN in v.gates_failed
        assert R.REASON_SCOPE_UNKNOWN in {r.code for r in v.reasons}

    def test_out_of_scope_files_block_merge(self):
        ev = _passing_evidence()
        ev.scope_clean = False
        ev.out_of_scope_files = ["some/random/file.py"]
        ev.forbidden_files_touched = []
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_SCOPE_VIOLATION in {r.code for r in v.reasons}

    def test_forbidden_files_touched_block_merge(self):
        ev = _passing_evidence()
        ev.scope_clean = False
        ev.out_of_scope_files = []
        ev.forbidden_files_touched = ["scripts/local/aed_final_gate.py"]
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_FORBIDDEN_FILE_TOUCHED in {r.code for r in v.reasons}


class TestGateCodex:
    def test_missing_codex_blocks_merge(self):
        ev = _passing_evidence()
        ev.codex_artifact_present = False
        ev.codex_artifact_fresh = None
        ev.codex_verdict = ""
        ev.codex_clean_passed = False
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_CODEX_MISSING in {r.code for r in v.reasons}
        assert R.GATE_CODEX_EVIDENCE in v.gates_failed

    def test_stale_codex_blocks_merge(self):
        ev = _passing_evidence()
        ev.codex_reviewed_sha = "b" * 40  # different from head
        ev.codex_artifact_fresh = False
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_CODEX_STALE in {r.code for r in v.reasons}

    def test_clean_codex_issue_comment_passes(self):
        ev = _passing_evidence()
        ev.codex_verdict = "CODEX_CLEAN_PASS"
        ev.codex_source = "issue_comment"
        ev.codex_reviewed_sha = ev.head_sha
        ev.codex_artifact_fresh = True
        ev.codex_clean_passed = True
        v = R.evaluate_readiness(ev)
        assert v.ready is True
        assert R.GATE_CODEX_EVIDENCE in v.gates_passed

    def test_clean_codex_formal_review_passes(self):
        ev = _passing_evidence()
        ev.codex_verdict = "CODEX_CLEAN_PASS"
        ev.codex_source = "review"
        ev.codex_reviewed_sha = ev.head_sha
        ev.codex_artifact_fresh = True
        ev.codex_clean_passed = True
        v = R.evaluate_readiness(ev)
        assert v.ready is True
        assert R.GATE_CODEX_EVIDENCE in v.gates_passed

    def test_failed_codex_verdict_blocks_merge(self):
        ev = _passing_evidence()
        ev.codex_verdict = "HOLD_NEW_CODEX_THREAD"
        ev.codex_clean_passed = False
        ev.codex_artifact_present = True
        ev.codex_artifact_fresh = True
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_CODEX_FAILED in {r.code for r in v.reasons}


class TestGateThreads:
    def test_unresolved_thread_blocks_merge(self):
        ev = _passing_evidence()
        ev.review_thread_inventory_complete = True
        ev.unresolved_thread_count = 1
        ev.unresolved_thread_ids = ["thread-1"]
        ev.review_threads = [
            {"thread_id": "thread-1", "isResolved": False,
             "isOutdated": False, "author": "chatgpt-codex-connector[bot]"}
        ]
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_UNRESOLVED_THREAD in {r.code for r in v.reasons}

    def test_unresolved_human_thread_blocks_merge(self):
        ev = _passing_evidence()
        ev.review_thread_inventory_complete = True
        ev.unresolved_thread_count = 1
        ev.unresolved_human_thread_ids = ["thread-h1"]
        ev.unresolved_thread_ids = ["thread-h1"]
        ev.review_threads = [
            {"thread_id": "thread-h1", "isResolved": False,
             "isOutdated": False, "author": "alice"}
        ]
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_UNRESOLVED_THREAD in {r.code for r in v.reasons}

    def test_thread_fetch_failure_blocks_merge(self):
        ev = _passing_evidence()
        ev.review_thread_inventory_complete = False
        ev.review_thread_inventory_error = "GraphQL timeout"
        ev.unresolved_thread_count = 0
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_THREAD_INVENTORY_FAILED in {r.code for r in v.reasons}
        assert R.GATE_THREAD_INVENTORY in v.gates_failed

    def test_outdated_bot_thread_does_not_block_by_inventory_alone(self):
        # Outdated Codex-bot threads are eligible for bounded auto-resolution,
        # but until resolve_advance() has cleared them, they still count as
        # unresolved and the gate stays closed.
        ev = _passing_evidence()
        ev.review_thread_inventory_complete = True
        ev.unresolved_thread_count = 1
        ev.outdated_bot_thread_ids = ["thread-bot-1"]
        ev.unresolved_thread_ids = ["thread-bot-1"]
        ev.unresolved_bot_thread_ids = []
        ev.review_threads = [
            {"thread_id": "thread-bot-1", "isResolved": False,
             "isOutdated": True,
             "author": "chatgpt-codex-connector[bot]"}
        ]
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_UNRESOLVED_THREAD in {r.code for r in v.reasons}


class TestGateAuthorizationPhrase:
    def test_head_movement_invalidates_authorization(self):
        ev = _passing_evidence(head_sha="a" * 40)
        # Authorization phrase was minted for head "a"*40 but head has
        # moved to "b"*40.
        ev.head_sha = "b" * 40
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_PHRASE_MISMATCH in {r.code for r in v.reasons}
        assert R.GATE_AUTHORIZATION_PHRASE in v.gates_failed

    def test_missing_phrase_blocks_merge(self):
        ev = _passing_evidence()
        ev.authorization_phrase = None
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_PHRASE_MISMATCH in {r.code for r in v.reasons}


class TestGateReviewsAndComments:
    def test_incomplete_reviews_inventory_blocks_merge(self):
        ev = _passing_evidence()
        ev.reviews_inventory_complete = False
        ev.reviews_inventory_error = "issue_comment fetch failed"
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_REVIEWS_INCOMPLETE in {r.code for r in v.reasons}


class TestGateNoMissingEvidence:
    def test_missing_evidence_blocks_merge(self):
        ev = _passing_evidence()
        ev.evidence_sources["codex_audit"] = "skipped:no_auth"
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_EVIDENCE_MISSING in {r.code for r in v.reasons}
        assert R.GATE_NO_MISSING_EVIDENCE in v.gates_failed


class TestGatePrState:
    def test_closed_pr_blocks_merge(self):
        ev = _passing_evidence()
        ev.pr_state = "CLOSED"
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_PR_NOT_OPEN in {r.code for r in v.reasons}

    def test_draft_pr_blocks_merge(self):
        ev = _passing_evidence()
        ev.is_draft = True
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_PR_IS_DRAFT in {r.code for r in v.reasons}

    def test_non_mergeable_pr_blocks_merge(self):
        ev = _passing_evidence()
        ev.mergeable = False
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_PR_NOT_MERGEABLE in {r.code for r in v.reasons}


class TestPartitionThreads:
    def test_partition_human_vs_bot(self):
        threads = [
            {"thread_id": "h1", "isResolved": False, "isOutdated": False,
             "author": "alice"},
            {"thread_id": "b1", "isResolved": False, "isOutdated": False,
             "author": "chatgpt-codex-connector[bot]"},
            {"thread_id": "b2", "isResolved": False, "isOutdated": True,
             "author": "chatgpt-codex-connector[bot]"},
            {"thread_id": "r1", "isResolved": True, "isOutdated": False,
             "author": "alice"},
        ]
        part = R.partition_unresolved_threads(threads)
        assert {t["thread_id"] for t in part["unresolved_human"]} == {"h1"}
        assert {t["thread_id"] for t in part["unresolved_bot_current"]} == {"b1"}
        assert {t["thread_id"] for t in part["outdated_bot_unresolved"]} == {"b2"}
        assert {t["thread_id"] for t in part["resolved"]} == {"r1"}

    def test_classify_thread_actor(self):
        assert R.classify_thread_actor("alice") == "human"
        assert R.classify_thread_actor("chatgpt-codex-connector[bot]") == "bot"
        assert R.classify_thread_actor("github-actions[bot]") == "bot"
        assert R.classify_thread_actor(None) == "unknown"
        assert R.classify_thread_actor("") == "unknown"


# -----------------------------------------------------------------------------
# Controller CLI surface (in-process)
# -----------------------------------------------------------------------------

class TestControllerCLISurface:
    def test_status_rejects_missing_pr_number(self):
        from scripts.local import aed_pr as ctrl
        with pytest.raises(SystemExit):
            ctrl.main(["status"])

    def test_merge_requires_authorization_phrase(self):
        from scripts.local import aed_pr as ctrl
        with pytest.raises(SystemExit):
            ctrl.main(["merge", "--pr-number", "411"])

    def test_advance_help_lists_pr_number(self):
        from scripts.local import aed_pr as ctrl
        with pytest.raises(SystemExit) as exc:
            ctrl.main(["advance", "--help"])
        assert exc.value.code == 0


class TestControllerStatusInProcess:
    def test_status_emits_full_evidence_report(self):
        code, out, err, invoked = _run_aed_pr(["status", "--pr-number", "411"])
        assert code == 0, f"status failed: {err}"
        report = json.loads(out)
        assert report["tool"] == "aed_pr.status"
        assert report["pr_number"] == 411
        assert "lifecycle_state" in report
        assert "changed_files_fetched" in report
        assert "scope_clean" in report
        assert "required_ci_names" in report
        assert "ci_conclusions" in report
        assert "codex_verdict" in report
        assert "unresolved_thread_count" in report
        assert "evidence_sources" in report
        assert "reason_codes" in report
        # Authorization phrase only emitted when ALL gates pass; with the
        # fake gh environment none of the inventories are complete, so
        # the phrase must be None (fail-closed).
        assert report["required_authorization_phrase"] is None
        # gh pr merge must NEVER have been called by status.
        merges = [a for a in invoked if a[:3] == ["gh", "pr", "merge"]]
        assert merges == []


class TestControllerAdvanceInProcess:
    def test_advance_dry_run_reports_actions_skipped(self):
        code, out, err, invoked = _run_aed_pr(
            ["advance", "--pr-number", "411", "--dry-run"]
        )
        assert code == 0, f"advance failed: {err}"
        report = json.loads(out)
        assert report["tool"] == "aed_pr.advance"
        actions = report.get("actions_taken") or []
        # dry-run must skip every mutation.
        assert any(a.get("action") == "dry_run" for a in actions)
        # gh pr merge must NEVER have been called by advance.
        merges = [a for a in invoked if a[:3] == ["gh", "pr", "merge"]]
        assert merges == []
        # Authorization phrase must be None on a non-ready PR.
        assert report["required_authorization_phrase_if_ready"] is None

    def test_advance_duplicate_codex_ping_prevented(self):
        # Pre-populate an existing Codex-review ping comment for the
        # exact head, then run advance and expect the controller to
        # detect it as a duplicate and skip posting another.
        existing_comment = {
            "id": 9001,
            "body": "Codex review request for head " + "a" * 40 + " ...",
        }

        def _fake_run(cmd, *args, **kwargs):
            cmd_str = " ".join(str(x) for x in cmd)
            if cmd[:3] == ["gh", "pr", "view"] and "--json" in cmd:
                payload = {
                    "number": 411, "title": "t", "state": "OPEN",
                    "isDraft": False, "mergeable": True,
                    "headRefOid": "a" * 40, "baseRefOid": "b" * 40,
                    "baseRefName": "main", "additions": 0, "deletions": 0,
                    "changedFiles": 0, "url": "u", "files": [],
                }
                return mock.DEFAULT
            if cmd[:3] == ["gh", "api"] and "issues/" in cmd[3] and "comments" in cmd[3] and "-X" not in cmd:
                return mock.DEFAULT
            return mock.DEFAULT

        from scripts.local import aed_pr as ctrl

        # Patch the controller's _run_json_or_none + fetch_pr_state so
        # the comment list returns our existing ping.
        with mock.patch.object(subprocess, "run") as mrun:
            def side(cmd, *args, **kwargs):
                cmd_str = " ".join(str(x) for x in cmd)
                if cmd[:3] == ["gh", "api"] and "issues/" in cmd[3] and "comments" in cmd[3]:
                    return mock.Mock(
                        returncode=0,
                        stdout=json.dumps([existing_comment]),
                        stderr="",
                    )
                if cmd[:3] == ["gh", "pr", "view"] and "--json" in cmd:
                    payload = {
                        "number": 411, "title": "t", "state": "OPEN",
                        "isDraft": False, "mergeable": True,
                        "headRefOid": "a" * 40, "baseRefOid": "b" * 40,
                        "baseRefName": "main", "additions": 0, "deletions": 0,
                        "changedFiles": 0, "url": "u", "files": [],
                    }
                    return mock.Mock(returncode=0, stdout=json.dumps(payload), stderr="")
                if cmd[:3] == ["gh", "api"] and "pulls/" in cmd[3]:
                    return mock.Mock(returncode=0, stdout="{}", stderr="")
                if cmd[:3] == ["gh", "run", "list"]:
                    return mock.Mock(returncode=0, stdout="[]", stderr="")
                return mock.Mock(returncode=0, stdout="{}", stderr="")
            mrun.side_effect = side

            buf = io.StringIO()
            old_out = sys.stdout
            sys.stdout = buf
            try:
                ctrl.main(["advance", "--pr-number", "411"])
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
            finally:
                sys.stdout = old_out

            # Look at every subprocess call for a POST create comment.
            post_calls = []
            for c in mrun.call_args_list:
                argv = c.args[0] if c.args else []
                if len(argv) >= 4 and argv[:3] == ["gh", "api", "-X"] and argv[3] == "POST":
                    post_calls.append(argv)
            # No POST create comment should be invoked (duplicate
            # prevented). If the controller did try to POST, that is
            # the bug we are guarding against.
            create_calls = [
                c for c in post_calls
                if "issues/" in " ".join(c) and "comments" in " ".join(c)
            ]
            assert create_calls == [], (
                f"expected duplicate ping to be prevented, but controller "
                f"issued create comment calls: {create_calls}"
            )


class TestControllerMergeInProcess:
    def test_blocked_cases_never_invoke_gh_pr_merge(self):
        # Passphrase mismatch case — must NOT call gh pr merge.
        code, out, err, invoked = _run_aed_pr([
            "merge", "--pr-number", "411",
            "--authorization-phrase",
            "I confirm merge PR #999 at " + "f" * 40
            + " using final-head reviewed clean state.",
        ])
        assert code != 0, f"merge must exit non-zero on phrase mismatch; got {code}"
        merges = [a for a in invoked if a[:3] == ["gh", "pr", "merge"]]
        assert merges == [], (
            f"blocked case must NOT invoke gh pr merge; got {merges}"
        )
        assert "phrase does NOT byte-match" in err

    def test_safe_merge_invokes_exactly_one_correctly_bound_command(self):
        # With the fake gh harness returning clean inventories, the
        # controller will eventually have all gates green. It must
        # invoke exactly one ``gh pr merge`` and that command must be
        # the canonical shape. We mock the codex classifier directly
        # so the test does not depend on the full gh GraphQL mock.
        from scripts.local import aed_pr as ctrl

        merged_argv: list = []

        def _fake_run(cmd, *args, **kwargs):
            cmd_str = " ".join(str(x) for x in cmd)
            if cmd[:3] == ["gh", "pr", "merge"]:
                merged_argv.append(list(cmd))
                return mock.Mock(returncode=0, stdout="merged\n", stderr="")
            if cmd[:3] == ["gh", "pr", "view"] and "--json" in cmd:
                payload = {
                    "number": 411, "title": "t", "state": "OPEN",
                    "isDraft": False, "mergeable": True,
                    "headRefOid": "a" * 40, "baseRefOid": "b" * 40,
                    "baseRefName": "main", "additions": 0, "deletions": 0,
                    "changedFiles": 1, "url": "u",
                    "files": [{"path": "scripts/local/aed_pr.py"}],
                }
                return mock.Mock(returncode=0, stdout=json.dumps(payload), stderr="")
            # Round-2 fix: gh pr checks (check names, not workflow names).
            if cmd[:3] == ["gh", "pr", "checks"]:
                return mock.Mock(returncode=0, stdout=json.dumps([
                    {"name": "test (3.11)", "state": "SUCCESS", "workflow": "CI"},
                    {"name": "validator", "state": "SUCCESS", "workflow": "CI"},
                    {"name": "governance-validators", "state": "SUCCESS",
                     "workflow": "CI"},
                    {"name": "pr-gate-live-smoke", "state": "SUCCESS",
                     "workflow": "CI"},
                    {"name": "review-comment-gate", "state": "SUCCESS",
                     "workflow": "CI"},
                ]), stderr="")
            return mock.Mock(returncode=0, stdout="{}", stderr="")

        def _fake_codex(*args, **kwargs):
            return {
                "status": "CODEX_CLEAN_PASS",
                "observed_head_sha": "a" * 40,
                "head_matches_expected": True,
                "clean_pass_detected": True,
                "clean_pass_source": "issue_comment",
                "latest_codex_response_id": "12345",
                "latest_codex_response_url": "https://example/codex",
                "latest_codex_response_type": "issue_comment",
                "active_threads": [],
                "outdated_threads": [],
                "issue_comment_inventory_complete": True,
                "issue_comment_inventory_error_count": 0,
                "issue_comment_inventory_last_error": None,
                "review_submission_inventory_complete": True,
                "review_submission_inventory_error_count": 0,
                "review_submission_inventory_last_error": None,
                "review_thread_inventory_complete": True,
                "review_thread_inventory_error_count": 0,
                "review_thread_inventory_last_error": None,
                "review_thread_comment_inventory_complete": True,
                "review_thread_comment_inventory_error_count": 0,
                "review_thread_comment_incomplete_thread_ids": [],
                "merge_state_status": "clean",
                "mergeable": True,
                "review_decision": "APPROVED",
            }

        # Round-3 fix: write the trusted scope file via the
        # controller's ``write_trusted_scope`` helper. The merge
        # path reads from ``_CANONICAL_SCOPE_ROOT``, which tests
        # monkey-patch here so the merge path operates on a
        # tempdir. Production callers NEVER supply ``scope_root``
        # and are NEVER redirected by an env-var override.
        import tempfile
        from scripts.local import aed_pr as ctrl_module
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            saved_root = ctrl_module._CANONICAL_SCOPE_ROOT
            ctrl_module._CANONICAL_SCOPE_ROOT = tmp_path
            try:
                ctrl_module.write_trusted_scope(
                    ctrl_module.DEFAULT_REPO, 411, "a" * 40,
                    DEFAULT_TEST_ALLOWED_FILES,
                )
                with mock.patch.object(subprocess, "run", side_effect=_fake_run), \
                     mock.patch.object(ctrl.CODEX, "classify", side_effect=_fake_codex):
                    old_argv = sys.argv
                    sys.argv = ["aed_pr.py", "merge", "--pr-number", "411",
                                "--authorization-phrase",
                                "I confirm merge PR #411 at " + "a" * 40
                                + " using final-head reviewed clean state."]
                    buf_out, buf_err = io.StringIO(), io.StringIO()
                    old_out, old_err = sys.stdout, sys.stderr
                    sys.stdout, sys.stderr = buf_out, buf_err
                    try:
                        try:
                            ctrl.main(sys.argv[1:])
                        except SystemExit:
                            pass
                    finally:
                        sys.stdout, sys.stderr = old_out, old_err
                        sys.argv = old_argv
            finally:
                ctrl_module._CANONICAL_SCOPE_ROOT = saved_root

        assert len(merged_argv) == 1, (
            f"safe merge must invoke exactly one gh pr merge; got {merged_argv}"
        )
        argv = merged_argv[0]
        # Canonical shape.
        assert argv[0:3] == ["gh", "pr", "merge"]
        assert "411" in argv
        assert "--squash" in argv
        assert "--delete-branch" in argv
        assert "--match-head-commit" in argv
        assert "a" * 40 in argv
        assert "--admin" not in argv
        assert "--auto" not in argv


# -----------------------------------------------------------------------------
# Head-movement invalidates authorization (regression — direct on cmd_merge)
# -----------------------------------------------------------------------------

class TestHeadMovementInvalidatesAuthorization:
    def test_phrase_for_ancestor_head_does_not_authorize_descendant(self):
        # Stale phrase must not authorize any merge.
        ev = _passing_evidence(head_sha="b" * 40)  # current head is b...
        # ...but the authorization phrase was minted for head a...
        ev.authorization_phrase = L.build_authorization_phrase(411, "a" * 40)
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_PHRASE_MISMATCH in {r.code for r in v.reasons}

    def test_phrase_for_descendant_head_does_not_authorize_ancestor(self):
        ev = _passing_evidence(head_sha="a" * 40)
        ev.authorization_phrase = L.build_authorization_phrase(411, "b" * 40)
        v = R.evaluate_readiness(ev)
        assert v.ready is False
        assert R.REASON_PHRASE_MISMATCH in {r.code for r in v.reasons}


# -----------------------------------------------------------------------------
# Codex-finding regression tests
# -----------------------------------------------------------------------------
#
# These cover the four findings from the initial-head Codex review on
# commit 0413708 (PR #411). Each finding is a P1 or P2 against the
# canonical controller and shared readiness evaluator.
#
# Finding 3596210370 (P1):
#   "Handle gh's mergeable enum before denying merges"
#   Fix: normalize_mergeable() must accept boolean True and the exact
#   string "MERGEABLE" and reject "CONFLICTING", "UNKNOWN", None, and
#   any unrecognized value.
#
# Finding 3596210374 (P1):
#   "Re-run readiness gates before executing the merge"
#   Fix: cmd_merge() must call evaluate_readiness() on freshly-fetched
#   live evidence immediately before invoking gh pr merge, and it must
#   fail closed on every verdict != ready.
#
# Finding 3596210377 (P2):
#   "Return READY when the merge gates have converged"
#   Fix: cmd_status() must derive the lifecycle state from the readiness
#   verdict, and the verdict must allow READY_FOR_MERGE_AUTHORIZATION
#   when the only remaining requirement is the authorization phrase and
#   the evidence bundle is complete.
#
# Finding 3596210381 (P2):
#   "Mock the live GitHub call in the new test"
#   Fix: the unit-test surface must not invoke a real `gh pr view`. All
#   controller tests must mock subprocess.run or fetch_pr_state; no
#   ordinary unit test depends on `gh`, authentication, network, or
#   the live state of PR #410.


class TestCodexFindingMergeableNormalization:
    """Regression tests for Codex finding 3596210370 (P1)."""

    def test_boolean_true_accepted(self):
        assert R.normalize_mergeable(True) is True

    def test_string_MERGEABLE_accepted(self):
        assert R.normalize_mergeable("MERGEABLE") is True

    def test_string_mergeable_lowercase_accepted(self):
        assert R.normalize_mergeable("mergeable") is True

    def test_string_Mergeable_mixed_case_accepted(self):
        assert R.normalize_mergeable("Mergeable") is True

    def test_boolean_false_rejected(self):
        assert R.normalize_mergeable(False) is False

    def test_string_CONFLICTING_rejected(self):
        # The string "CONFLICTING" is truthy in Python; the normalizer
        # must not let it slip through as "mergeable".
        assert R.normalize_mergeable("CONFLICTING") is False

    def test_string_UNKNOWN_rejected(self):
        assert R.normalize_mergeable("UNKNOWN") is False

    def test_string_unknown_lowercase_rejected(self):
        assert R.normalize_mergeable("unknown") is False

    def test_none_rejected(self):
        assert R.normalize_mergeable(None) is None

    def test_empty_string_rejected(self):
        # Empty string is falsy but is NOT the mergeable signal.
        assert R.normalize_mergeable("") is None

    def test_unrecognized_string_rejected(self):
        assert R.normalize_mergeable("maybe") is None

    def test_integer_zero_rejected(self):
        # 0 is falsy but is NOT a valid mergeable signal.
        assert R.normalize_mergeable(0) is None

    def test_integer_one_rejected(self):
        # 1 is truthy but is NOT a valid mergeable signal.
        assert R.normalize_mergeable(1) is None

    def test_list_rejected(self):
        assert R.normalize_mergeable(["MERGEABLE"]) is None

    def test_dict_rejected(self):
        assert R.normalize_mergeable({"value": "MERGEABLE"}) is None

    def test_readiness_passes_on_boolean_true(self):
        ev = _passing_evidence()
        ev.mergeable = True
        v = R.evaluate_readiness(ev)
        # All other gates must still pass; mergeable alone must not
        # flip the verdict.
        assert R.GATE_PR_MERGEABLE in v.gates_passed
        assert R.REASON_PR_NOT_MERGEABLE not in {r.code for r in v.reasons}

    def test_readiness_passes_on_string_MERGEABLE(self):
        ev = _passing_evidence()
        ev.mergeable = "MERGEABLE"
        v = R.evaluate_readiness(ev)
        assert R.GATE_PR_MERGEABLE in v.gates_passed
        assert R.REASON_PR_NOT_MERGEABLE not in {r.code for r in v.reasons}

    def test_readiness_blocks_on_string_CONFLICTING(self):
        ev = _passing_evidence()
        ev.mergeable = "CONFLICTING"
        v = R.evaluate_readiness(ev)
        assert R.GATE_PR_MERGEABLE in v.gates_failed
        assert R.REASON_PR_NOT_MERGEABLE in {r.code for r in v.reasons}
        assert v.ready is False

    def test_readiness_blocks_on_string_UNKNOWN(self):
        ev = _passing_evidence()
        ev.mergeable = "UNKNOWN"
        v = R.evaluate_readiness(ev)
        assert R.GATE_PR_MERGEABLE in v.gates_failed
        assert R.REASON_PR_NOT_MERGEABLE in {r.code for r in v.reasons}
        assert v.ready is False

    def test_readiness_blocks_on_none(self):
        ev = _passing_evidence()
        ev.mergeable = None
        v = R.evaluate_readiness(ev)
        assert R.GATE_PR_MERGEABLE in v.gates_failed
        assert v.ready is False


class TestCodexFindingReadyStatusEmission:
    """Regression tests for Codex finding 3596210377 (P2).

    The lifecycle state READY_FOR_MERGE_AUTHORIZATION must be reachable
    when every gate has converged and only the operator's authorization
    phrase is required.
    """

    def _build_clean_evidence(self, mergeable=True):
        return R.ReadinessEvidence(
            pr_state="OPEN",
            is_draft=False,
            mergeable=mergeable,
            head_sha="a" * 40,
            authorization_phrase=None,
            changed_files=["scripts/local/aed_pr.py"],
            changed_files_fetched=True,
            scope_clean=True,
            out_of_scope_files=[],
            forbidden_files_touched=[],
            scope_blockers=[],
            allowed_files_supplied=True,
            required_ci_names=["test", "validator"],
            ci_conclusions={"test": "SUCCESS", "validator": "SUCCESS"},
            ci_missing=[],
            ci_pending=[],
            ci_failed=[],
            codex_verdict="CODEX_CLEAN_PASS",
            codex_source="issue_comment",
            codex_reviewed_sha="a" * 40,
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

    def test_ready_state_emitted_when_mergeable_is_boolean_true(self):
        from scripts.local import aed_pr as ctrl
        ev = self._build_clean_evidence(mergeable=True)
        pr_view = {
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "a" * 40,
            "url": "u",
            "title": "t",
            "mergeable": True,
        }
        # The status path uses evaluate_machine_readiness so the
        # canonical phrase is emitted even when no phrase is supplied.
        verdict = R.evaluate_machine_readiness(ev)
        state = ctrl.derive_lifecycle_state(verdict, pr_view)
        assert state == "READY_FOR_MERGE_AUTHORIZATION", (
            f"expected READY_FOR_MERGE_AUTHORIZATION, got {state!r}"
        )

    def test_ready_state_emitted_when_mergeable_is_string_MERGEABLE(self):
        from scripts.local import aed_pr as ctrl
        ev = self._build_clean_evidence(mergeable="MERGEABLE")
        pr_view = {
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "a" * 40,
            "url": "u",
            "title": "t",
            "mergeable": "MERGEABLE",
        }
        verdict = R.evaluate_machine_readiness(ev)
        state = ctrl.derive_lifecycle_state(verdict, pr_view)
        assert state == "READY_FOR_MERGE_AUTHORIZATION", (
            f"expected READY_FOR_MERGE_AUTHORIZATION, got {state!r}"
        )

    def test_ready_state_blocked_when_mergeable_is_CONFLICTING(self):
        from scripts.local import aed_pr as ctrl
        ev = self._build_clean_evidence(mergeable="CONFLICTING")
        pr_view = {
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "a" * 40,
            "url": "u",
            "title": "t",
            "mergeable": "CONFLICTING",
        }
        verdict = R.evaluate_readiness(ev)
        state = ctrl.derive_lifecycle_state(verdict, pr_view)
        assert state in {"BLOCKED", "ACTION_REQUIRED"}, (
            f"expected blocked state, got {state!r}"
        )

    def test_ready_state_blocked_when_mergeable_is_UNKNOWN(self):
        from scripts.local import aed_pr as ctrl
        ev = self._build_clean_evidence(mergeable="UNKNOWN")
        pr_view = {
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "a" * 40,
            "url": "u",
            "title": "t",
            "mergeable": "UNKNOWN",
        }
        verdict = R.evaluate_readiness(ev)
        state = ctrl.derive_lifecycle_state(verdict, pr_view)
        assert state in {"BLOCKED", "ACTION_REQUIRED"}, (
            f"expected blocked state, got {state!r}"
        )


class TestCodexFindingMergeReevaluatesLiveReadiness:
    """Regression tests for Codex finding 3596210374 (P1)."""

    def test_merge_uses_shared_evaluator_path(self):
        from scripts.local import aed_pr as ctrl
        import inspect
        source = inspect.getsource(ctrl.cmd_merge)
        assert "R.evaluate_readiness" in source, (
            "cmd_merge must call R.evaluate_readiness on the live evidence"
        )
        # The merge path must not have its own local gate definitions
        # that bypass the shared evaluator.
        assert "if evidence.mergeable is True" not in source, (
            "cmd_merge must not apply its own truthiness check on mergeable"
        )

    def test_merge_blocked_when_only_mergeable_normalizes_to_CONFLICTING(self):
        from scripts.local import aed_pr as ctrl
        import subprocess as sp
        from scripts.local import audit_codex_response_for_pr as co

        # Capture every subprocess.run invocation through a Mock that
        # supports .call_args_list introspection after the patch ends.
        recorded_calls = []

        def _fake_run(cmd, *args, **kwargs):
            recorded_calls.append(list(cmd))
            if cmd[:3] == ["gh", "pr", "view"] and "--json" in cmd:
                payload = {
                    "number": 411, "title": "t", "state": "OPEN",
                    "isDraft": False, "mergeable": "CONFLICTING",
                    "headRefOid": "a" * 40, "baseRefOid": "b" * 40,
                    "baseRefName": "main", "additions": 0, "deletions": 0,
                    "changedFiles": 1, "url": "u",
                    "files": [{"path": "scripts/local/aed_pr.py"}],
                }
                return mock.Mock(
                    returncode=0, stdout=json.dumps(payload), stderr=""
                )
            if cmd[:3] == ["gh", "run", "list"]:
                return mock.Mock(returncode=0, stdout=json.dumps([
                    {"workflowName": "test", "conclusion": "SUCCESS",
                     "status": "COMPLETED", "headSha": "a" * 40},
                    {"workflowName": "validator", "conclusion": "SUCCESS",
                     "status": "COMPLETED", "headSha": "a" * 40},
                    {"workflowName": "governance-validators",
                     "conclusion": "SUCCESS", "status": "COMPLETED",
                     "headSha": "a" * 40},
                    {"workflowName": "pr-gate-live-smoke",
                     "conclusion": "SUCCESS", "status": "COMPLETED",
                     "headSha": "a" * 40},
                ]), stderr="")
            if cmd[:3] == ["gh", "api"]:
                return mock.Mock(returncode=0, stdout="[]", stderr="")
            return mock.Mock(returncode=0, stdout="{}", stderr="")

        def _fake_codex(*args, **kwargs):
            return {
                "status": "CODEX_CLEAN_PASS",
                "observed_head_sha": "a" * 40,
                "head_matches_expected": True,
                "clean_pass_detected": True,
                "clean_pass_source": "issue_comment",
                "active_threads": [],
                "outdated_threads": [],
                "issue_comment_inventory_complete": True,
                "issue_comment_inventory_error_count": 0,
                "review_submission_inventory_complete": True,
                "review_submission_inventory_error_count": 0,
                "review_thread_inventory_complete": True,
                "review_thread_inventory_error_count": 0,
                "review_thread_comment_inventory_complete": True,
                "review_thread_comment_inventory_error_count": 0,
            }

        with mock.patch.object(
            sp, "run", side_effect=_fake_run
        ), mock.patch.object(
            co, "classify", side_effect=_fake_codex
        ):
            buf_out, buf_err = io.StringIO(), io.StringIO()
            old_out, old_err = sys.stdout, sys.stderr
            old_argv = sys.argv
            sys.stdout = buf_out
            sys.stderr = buf_err
            sys.argv = [
                "aed_pr.py", "merge", "--pr-number", "411",
                "--authorization-phrase",
                "I confirm merge PR #411 at " + "a" * 40
                + " using final-head reviewed clean state.",
            ]
            try:
                try:
                    ctrl.main(sys.argv[1:])
                except SystemExit:
                    pass
            finally:
                sys.stdout, sys.stderr, sys.argv = old_out, old_err, old_argv

        merges = [a for a in recorded_calls if a[:3] == ["gh", "pr", "merge"]]
        assert merges == [], (
            f"blocked merge must NOT invoke gh pr merge; got {merges}"
        )


class TestCodexFindingNoLiveGhCallInUnitTests:
    """Regression tests for Codex finding 3596210381 (P2)."""

    def test_cmd_status_unit_test_uses_mocks(self):
        import inspect
        from tests import test_aed_pr as tmod
        source = inspect.getsource(tmod.TestControllerStatusInProcess)
        assert "_run_aed_pr" in source, (
            "TestControllerStatusInProcess must use _run_aed_pr "
            "(which patches subprocess.run) so it never reaches real gh"
        )
        assert "subprocess.run(" not in source, (
            "TestControllerStatusInProcess must not call subprocess.run "
            "directly; route through the mocked harness instead"
        )

    def test_normalize_aed_pr_tests_subprocess_runs_are_mocked(self):
        # Walk the test module's AST and assert that every
        # ``subprocess.run(...)`` call appears inside a
        # ``mock.patch.object(subprocess, "run", ...)`` context (or
        # inside the in-process ``_run_aed_pr`` harness).
        import ast
        import inspect
        from tests import test_aed_pr as tmod

        tree = ast.parse(inspect.getsource(tmod))

        # Find every call to subprocess.run(...) and verify it sits
        # inside an enclosing mock.patch.object or _run_aed_pr body.
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            attr = getattr(func, "attr", None)
            if attr != "run":
                continue
            value = getattr(func, "value", None)
            if not isinstance(value, ast.Name) or value.id != "subprocess":
                continue
            # Walk up the parent chain; we don't have a parent map, so
            # we approximate by checking the source range. The simplest
            # proxy: confirm the call sits inside a function whose
            # source contains "mock.patch.object" on ``subprocess``.
            src_segment = inspect.getsource(tmod)
            # Locate the call by line number.
            line = node.lineno
            # Coarse check: the substring from line-30 to line+5 must
            # mention either mock.patch.object(subprocess ...) or
            # _run_aed_pr's body.
            lines = src_segment.splitlines()
            lo = max(0, line - 30)
            hi = min(len(lines), line + 5)
            window = "\n".join(lines[lo:hi])
            if (
                "mock.patch.object(subprocess" not in window
                and "mock.patch.object(sp, \"run\"" not in window
                and "_run_aed_pr(" not in window
            ):
                offenders.append(f"line {line}: {lines[line - 1].strip()}")
        assert offenders == [], (
            "subprocess.run invocations found outside any mock context: "
            f"{offenders}"
        )
