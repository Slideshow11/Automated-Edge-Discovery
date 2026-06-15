"""Tests for the AED policy engine skeleton.

Stdlib-only: uses unittest, no pytest-only fixtures. The tests
import the package directly from the repo root and exercise
``aed_policy.policy.evaluate_action`` against a constructed
``AEDRunState``.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# Make the aed_policy package importable when pytest is invoked
# from any working directory.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from aed_policy.action_types import (  # noqa: E402
    AEDActionType,
    MUTATING_LOCAL_ACTIONS,
    READ_ONLY_ACTIONS,
)
from aed_policy.decisions import AEDDecision, AEDDecisionCode  # noqa: E402
from aed_policy.policy import evaluate_action  # noqa: E402
from aed_policy.reporting import (  # noqa: E402
    decision_to_paragraph,
    missing_evidence,
    summarize_denied,
)
from aed_policy.run_state import AEDRunState  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


ZERO_SHA = "0" * 40
# Live canonical phrase produced by both
# scripts/local/verify_final_head_merge_command.py and
# scripts/local/aed_final_gate.py. The policy engine must accept
# only this exact form; the previous policy-only form is no
# longer accepted.
AUTH_PHRASE = (
    f"I confirm merge PR #999 at {ZERO_SHA} "
    f"using final-head reviewed clean state."
)


def _clean_state(**overrides) -> AEDRunState:
    """Build a run state that is fully ready for merge.

    Each test can override individual fields to flip one
    decision at a time.
    """
    base = dict(
        repo="Slideshow11/Automated-Edge-Discovery",
        pr_number=999,
        current_head_sha=ZERO_SHA,
        expected_head_sha=ZERO_SHA,
        branch="docs/feature",
        base_branch="main",
        lifecycle_state="MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
        primary_worktree_path="/home/max/Automated-Edge-Discovery",
        primary_worktree_head=ZERO_SHA,
        primary_worktree_branch="main",
        primary_worktree_clean=True,
        isolated_workspace_path="/tmp/aed_runs/worktrees/test",
        isolated_workspace_head=ZERO_SHA,
        isolated_workspace_clean=True,
        ci_status="pass",
        scope_status="clean",
        merge_state_status="CLEAN",
        mergeable="MERGEABLE",
        unresolved_thread_count=0,
        active_thread_count=0,
        outdated_thread_count=0,
        codex_clean_pass_detected=True,
        codex_newer_finding_after_clean_pass=False,
        # AED-RULE-011: clean-pass evidence must be tied to the
        # current expected head. _clean_state sets it to the
        # ZERO_SHA so the engine treats the clean pass as a
        # current-head clean pass for PR #999.
        codex_clean_pass_head_sha=ZERO_SHA,
        # AED-RULE-011 strict variant: the harness's derived
        # current-head indicator must be True for the merge path
        # to allow. _clean_state sets it to True so the engine
        # treats the clean pass as a current-head clean pass.
        codex_clean_pass_for_current_head=True,
        codex_ping_comment_id=None,
        codex_ping_head_sha=None,
        audit_append_available=True,
        audit_append_only=True,
        explicit_authorization_phrase=AUTH_PHRASE,
        authorized_thread_ids=[],
        # AED-RULE-021: the field is omitted so the default
        # factory returns the canonical protected historical PR
        # set. 999 is not in the set, so merge is allowed.
    )
    base.update(overrides)
    return AEDRunState(**base)


# ---------------------------------------------------------------------------
# Read-only actions always allowed
# ---------------------------------------------------------------------------


class TestReadOnlyAllowed(unittest.TestCase):
    def test_read_only_status_allowed(self):
        d = evaluate_action(AEDActionType.READ_ONLY_STATUS, _clean_state())
        self.assertTrue(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.ALLOW)

    def test_file_read_allowed(self):
        d = evaluate_action(AEDActionType.FILE_READ, _clean_state())
        self.assertTrue(d.allowed)

    def test_git_read_only_allowed(self):
        d = evaluate_action(AEDActionType.GIT_READ_ONLY, _clean_state())
        self.assertTrue(d.allowed)

    def test_github_read_only_allowed(self):
        d = evaluate_action(AEDActionType.GITHUB_READ_ONLY, _clean_state())
        self.assertTrue(d.allowed)

    def test_terminal_read_only_allowed(self):
        d = evaluate_action(AEDActionType.TERMINAL_READ_ONLY, _clean_state())
        self.assertTrue(d.allowed)

    def test_read_only_set_contains_expected_actions(self):
        # Sanity check: the helper set is what the policy uses.
        for expected in (
            AEDActionType.READ_ONLY_STATUS,
            AEDActionType.FILE_READ,
            AEDActionType.GIT_READ_ONLY,
            AEDActionType.GITHUB_READ_ONLY,
            AEDActionType.TERMINAL_READ_ONLY,
        ):
            self.assertIn(expected, READ_ONLY_ACTIONS)


# ---------------------------------------------------------------------------
# Unknown action denied by default
# ---------------------------------------------------------------------------


class TestUnknownDenied(unittest.TestCase):
    def test_unknown_action_denied(self):
        d = evaluate_action(AEDActionType.UNKNOWN, _clean_state())
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.DENY)
        self.assertIn("AED-RULE-024", d.matched_rule_ids)


# ---------------------------------------------------------------------------
# Primary worktree protections
# ---------------------------------------------------------------------------


class TestPrimaryWorktreeRules(unittest.TestCase):
    def test_primary_mutation_denied_by_default(self):
        d = evaluate_action(
            AEDActionType.PRIMARY_WORKTREE_MUTATION,
            _clean_state(explicit_authorization_phrase=None),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.REQUIRE_EXPLICIT_AUTHORIZATION)
        self.assertIn("AED-RULE-001", d.matched_rule_ids)

    def test_primary_mutation_allowed_with_authorization_and_clean(self):
        d = evaluate_action(
            AEDActionType.PRIMARY_WORKTREE_MUTATION, _clean_state()
        )
        self.assertTrue(d.allowed)
        self.assertIn("AED-RULE-001", d.matched_rule_ids)

    def test_primary_mutation_denied_if_primary_dirty(self):
        d = evaluate_action(
            AEDActionType.PRIMARY_WORKTREE_MUTATION,
            _clean_state(primary_worktree_clean=False),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.REQUIRE_NO_PRIMARY_MUTATION)
        self.assertIn("AED-RULE-001", d.matched_rule_ids)

    def test_primary_sync_denied_by_default(self):
        d = evaluate_action(
            AEDActionType.PRIMARY_WORKTREE_SYNC,
            _clean_state(explicit_authorization_phrase=None),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.REQUIRE_EXPLICIT_AUTHORIZATION)
        self.assertIn("AED-RULE-002", d.matched_rule_ids)

    def test_primary_sync_allowed_with_authorization(self):
        d = evaluate_action(
            AEDActionType.PRIMARY_WORKTREE_SYNC, _clean_state()
        )
        self.assertTrue(d.allowed)
        self.assertIn("AED-RULE-002", d.matched_rule_ids)


# ---------------------------------------------------------------------------
# Merge rules
# ---------------------------------------------------------------------------


class TestMergeRules(unittest.TestCase):
    def test_merge_denied_without_authorization(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(explicit_authorization_phrase=None),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.REQUIRE_EXPLICIT_AUTHORIZATION)
        self.assertIn("AED-RULE-007", d.matched_rule_ids)

    def test_merge_denied_on_head_mismatch(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(current_head_sha="a" * 40),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_EXACT_HEAD_AUTHORIZATION
        )
        self.assertIn("AED-RULE-005", d.matched_rule_ids)

    def test_merge_denied_with_unresolved_threads(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(unresolved_thread_count=1),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_NO_UNRESOLVED_THREADS
        )
        self.assertIn("AED-RULE-008", d.matched_rule_ids)

    def test_merge_denied_when_ci_not_clean(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(ci_status="fail")
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.REQUIRE_CLEAN_CI)
        self.assertIn("AED-RULE-018", d.matched_rule_ids)

    def test_merge_denied_when_ci_pending(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(ci_status="pending")
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.REQUIRE_CLEAN_CI)

    def test_merge_denied_when_scope_not_clean(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(scope_status="dirty")
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.REQUIRE_CLEAN_SCOPE)
        self.assertIn("AED-RULE-019", d.matched_rule_ids)

    def test_merge_denied_when_merge_state_not_clean(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(merge_state_status="BLOCKED"),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_CLEAN_MERGE_STATE
        )

    def test_merge_denied_on_codex_newer_finding(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(codex_newer_finding_after_clean_pass=True),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.HOLD)
        self.assertIn("AED-RULE-011", d.matched_rule_ids)
        self.assertIn("AED-RULE-012", d.matched_rule_ids)

    def test_merge_allowed_when_all_clean(self):
        d = evaluate_action(AEDActionType.GITHUB_MERGE, _clean_state())
        self.assertTrue(d.allowed)
        for rule_id in (
            "AED-RULE-005",
            "AED-RULE-007",
            "AED-RULE-008",
            "AED-RULE-018",
            "AED-RULE-019",
        ):
            self.assertIn(rule_id, d.matched_rule_ids)


# ---------------------------------------------------------------------------
# Thread resolution
# ---------------------------------------------------------------------------


class TestThreadResolve(unittest.TestCase):
    def test_thread_resolve_denied_without_thread_list(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE, _clean_state()
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_THREAD_LIST_AUTHORIZATION
        )
        self.assertIn("AED-RULE-008", d.matched_rule_ids)

    def test_thread_resolve_allowed_with_thread_list(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_abc"]),
            target_thread_ids=["PRRT_abc"],
        )
        self.assertTrue(d.allowed)
        self.assertIn("AED-RULE-008", d.matched_rule_ids)


# ---------------------------------------------------------------------------
# Codex ping
# ---------------------------------------------------------------------------


class TestCodexPing(unittest.TestCase):
    def test_codex_ping_denied_on_duplicate_same_head(self):
        d = evaluate_action(
            AEDActionType.CODEX_PING,
            _clean_state(
                codex_ping_comment_id="12345",
                codex_ping_head_sha=ZERO_SHA,
            ),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_NO_DUPLICATE_CODEX_PING
        )
        self.assertIn("AED-RULE-010", d.matched_rule_ids)

    def test_codex_ping_allowed_when_no_existing_ping(self):
        d = evaluate_action(AEDActionType.CODEX_PING, _clean_state())
        self.assertTrue(d.allowed)
        self.assertIn("AED-RULE-010", d.matched_rule_ids)

    def test_codex_ping_allowed_when_existing_ping_is_for_different_head(self):
        d = evaluate_action(
            AEDActionType.CODEX_PING,
            _clean_state(
                codex_ping_comment_id="12345",
                codex_ping_head_sha="a" * 40,
            ),
        )
        self.assertTrue(d.allowed)


# ---------------------------------------------------------------------------
# Audit append
# ---------------------------------------------------------------------------


class TestAuditAppend(unittest.TestCase):
    def test_audit_append_denied_if_append_only_evidence_missing(self):
        d = evaluate_action(
            AEDActionType.AUDIT_APPEND,
            _clean_state(
                audit_append_available=False, audit_append_only=False
            ),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.REQUIRE_APPEND_ONLY_AUDIT)
        self.assertIn("AED-RULE-020", d.matched_rule_ids)

    def test_audit_append_denied_if_append_only_false(self):
        d = evaluate_action(
            AEDActionType.AUDIT_APPEND,
            _clean_state(audit_append_only=False),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.REQUIRE_APPEND_ONLY_AUDIT)

    def test_audit_append_allowed_when_evidence_present(self):
        d = evaluate_action(AEDActionType.AUDIT_APPEND, _clean_state())
        self.assertTrue(d.allowed)
        self.assertIn("AED-RULE-020", d.matched_rule_ids)


# ---------------------------------------------------------------------------
# File writes / local mutating actions / isolated workspace
# ---------------------------------------------------------------------------


class TestFileWriteAndIsolatedWorkspace(unittest.TestCase):
    def test_file_write_denied_without_isolated_workspace(self):
        d = evaluate_action(
            AEDActionType.FILE_WRITE,
            _clean_state(
                isolated_workspace_path="/home/max/Automated-Edge-Discovery"
            ),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_ISOLATED_WORKSPACE
        )
        self.assertIn("AED-RULE-003", d.matched_rule_ids)

    def test_file_write_allowed_in_isolated_workspace(self):
        d = evaluate_action(AEDActionType.FILE_WRITE, _clean_state())
        self.assertTrue(d.allowed)
        self.assertIn("AED-RULE-003", d.matched_rule_ids)

    def test_terminal_mutating_denied_without_isolated_workspace(self):
        d = evaluate_action(
            AEDActionType.TERMINAL_MUTATING,
            _clean_state(
                isolated_workspace_path="/home/max/Automated-Edge-Discovery"
            ),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_ISOLATED_WORKSPACE
        )

    def test_git_mutating_denied_without_isolated_workspace(self):
        d = evaluate_action(
            AEDActionType.GIT_MUTATING,
            _clean_state(
                isolated_workspace_path="/home/max/Automated-Edge-Discovery"
            ),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_ISOLATED_WORKSPACE
        )

    def test_terminal_mutating_allowed_in_isolated_workspace(self):
        d = evaluate_action(
            AEDActionType.TERMINAL_MUTATING, _clean_state()
        )
        self.assertTrue(d.allowed)

    def test_git_mutating_allowed_in_isolated_workspace(self):
        d = evaluate_action(AEDActionType.GIT_MUTATING, _clean_state())
        self.assertTrue(d.allowed)

    def test_isolated_workspace_path_must_be_under_aed_runs_worktrees(self):
        # A path under /tmp/ but not under /tmp/aed_runs/worktrees/
        # is not an isolated workspace.
        d = evaluate_action(
            AEDActionType.FILE_WRITE,
            _clean_state(isolated_workspace_path="/tmp/somewhere/else"),
        )
        self.assertFalse(d.allowed)

    def test_mutating_local_actions_set_contains_expected_actions(self):
        for expected in (
            AEDActionType.FILE_WRITE,
            AEDActionType.TERMINAL_MUTATING,
            AEDActionType.GIT_MUTATING,
        ):
            self.assertIn(expected, MUTATING_LOCAL_ACTIONS)


# ---------------------------------------------------------------------------
# Protected PRs
# ---------------------------------------------------------------------------


class TestProtectedPRs(unittest.TestCase):
    def test_merge_denied_for_protected_pr(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(pr_number=384, protected_pr_numbers={384}),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.DENY)
        self.assertIn("AED-RULE-021", d.matched_rule_ids)

    def test_reopen_denied_for_protected_pr(self):
        d = evaluate_action(
            AEDActionType.GITHUB_REOPEN,
            _clean_state(pr_number=384, protected_pr_numbers={384}),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-021", d.matched_rule_ids)

    def test_reopen_denied_without_authorization_for_non_protected_pr(self):
        d = evaluate_action(
            AEDActionType.GITHUB_REOPEN,
            _clean_state(explicit_authorization_phrase=None),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_EXPLICIT_AUTHORIZATION
        )
        self.assertIn("AED-RULE-021", d.matched_rule_ids)

    def test_reopen_allowed_for_non_protected_pr_with_authorization(self):
        d = evaluate_action(AEDActionType.GITHUB_REOPEN, _clean_state())
        self.assertTrue(d.allowed)
        self.assertIn("AED-RULE-021", d.matched_rule_ids)


# ---------------------------------------------------------------------------
# Decision shape / serialization
# ---------------------------------------------------------------------------


class TestDecisionSerialization(unittest.TestCase):
    def test_decision_includes_matched_rule_ids(self):
        d = evaluate_action(AEDActionType.GITHUB_MERGE, _clean_state())
        self.assertIsInstance(d.matched_rule_ids, list)
        self.assertTrue(
            all(isinstance(r, str) for r in d.matched_rule_ids)
        )
        self.assertGreater(len(d.matched_rule_ids), 0)

    def test_decision_serialization_roundtrips(self):
        d = evaluate_action(AEDActionType.GITHUB_MERGE, _clean_state())
        payload = d.to_dict()
        # Stable JSON shape.
        s = json.dumps(payload, sort_keys=True)
        self.assertIn('"allowed": true', s)
        self.assertIn('"code": "ALLOW"', s)
        self.assertIn("reason", s)
        self.assertIn("matched_rule_ids", s)
        self.assertIn("required_evidence", s)
        # Round-trip.
        d2 = AEDDecision.from_dict(json.loads(s))
        self.assertEqual(d2.allowed, d.allowed)
        self.assertEqual(d2.code, d.code)
        self.assertEqual(d2.reason, d.reason)
        self.assertEqual(d2.matched_rule_ids, d.matched_rule_ids)
        self.assertEqual(d2.required_evidence, d.required_evidence)

    def test_run_state_serialization_roundtrips_through_json(self):
        state = _clean_state()
        payload = state.to_dict()
        s = json.dumps(payload, sort_keys=True)
        self.assertIn('"pr_number": 999', s)
        self.assertIn('"ci_status": "pass"', s)
        # Round-trip the SHA fields without re-constructing the
        # dataclass (this PR does not need a from_dict on the
        # state, but we do verify the payload is JSON-clean).
        re_loaded = json.loads(s)
        self.assertEqual(re_loaded["expected_head_sha"], ZERO_SHA)
        # The default protected_pr_numbers set is the canonical
        # historical PR list; verify it round-trips through JSON.
        from aed_policy.run_state import DEFAULT_PROTECTED_PR_NUMBERS
        self.assertEqual(
            re_loaded["protected_pr_numbers"],
            sorted(int(p) for p in DEFAULT_PROTECTED_PR_NUMBERS),
        )


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


class TestReportingHelpers(unittest.TestCase):
    def test_decision_to_paragraph_allow(self):
        d = evaluate_action(AEDActionType.READ_ONLY_STATUS, _clean_state())
        s = decision_to_paragraph(d)
        self.assertIn("ALLOW", s)
        # Allow paragraph should reference at least the allowed code
        # and the policy reason.
        self.assertIn("ALLOW", s)
        self.assertIn("Action permitted", s)

    def test_decision_to_paragraph_deny(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(explicit_authorization_phrase=None),
        )
        s = decision_to_paragraph(d)
        self.assertIn("DENY", s)
        self.assertIn("REQUIRE_EXPLICIT_AUTHORIZATION", s)

    def test_missing_evidence_empty_when_allowed(self):
        d = evaluate_action(AEDActionType.READ_ONLY_STATUS, _clean_state())
        self.assertEqual(missing_evidence(d), [])

    def test_missing_evidence_nonempty_when_denied(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(explicit_authorization_phrase=None),
        )
        self.assertGreater(len(missing_evidence(d)), 0)

    def test_summarize_denied_all_allowed(self):
        d_allowed = evaluate_action(
            AEDActionType.READ_ONLY_STATUS, _clean_state()
        )
        s = summarize_denied([d_allowed])
        self.assertIn("All 1 decision(s) allowed.", s)

    def test_summarize_denied_mixed(self):
        d_allowed = evaluate_action(
            AEDActionType.READ_ONLY_STATUS, _clean_state()
        )
        d_denied = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(explicit_authorization_phrase=None),
        )
        s = summarize_denied([d_allowed, d_denied])
        self.assertIn("1 of 2", s)
        self.assertIn("DENY", s)


# ---------------------------------------------------------------------------
# Regression tests for Codex findings on PR #404
# ---------------------------------------------------------------------------
#
# These tests pin the fixes for the four Codex findings:
#   PRRT_kwDOSHFpYM6JaFHu (P1) — Require clean Codex evidence before allowing merges
#   PRRT_kwDOSHFpYM6JaFHw (P1) — Reject unmergeable PRs before allowing merge
#   PRRT_kwDOSHFpYM6JaFHx (P2) — Re-verify the live head before Codex pings
#   PRRT_kwDOSHFpYM6JaFHz (P2) — Re-verify the live head before resolving threads
#
# Each test below fails on the pre-patch implementation and passes after
# the patch.


class TestCodexCleanEvidenceBeforeMerge(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6JaFHu."""

    def test_merge_denied_when_codex_clean_pass_false(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(codex_clean_pass_detected=False),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.HOLD)
        self.assertIn("AED-RULE-011", d.matched_rule_ids)
        self.assertIn(
            "codex_clean_pass_detected=true", d.required_evidence
        )

    def test_merge_allowed_when_codex_clean_pass_true_and_no_newer_finding(
        self,
    ):
        d = evaluate_action(AEDActionType.GITHUB_MERGE, _clean_state())
        self.assertTrue(d.allowed)

    def test_merge_denied_when_codex_newer_finding_true(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(codex_newer_finding_after_clean_pass=True),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.HOLD)
        self.assertIn("AED-RULE-011", d.matched_rule_ids)
        self.assertIn("AED-RULE-012", d.matched_rule_ids)


class TestUnmergeableMergeDenied(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6JaFHw."""

    def test_merge_denied_when_mergeable_false(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable=False)
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_CLEAN_MERGE_STATE
        )
        self.assertIn("AED-RULE-019", d.matched_rule_ids)

    def test_merge_denied_when_mergeable_conflicting(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(mergeable="CONFLICTING"),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_CLEAN_MERGE_STATE
        )
        self.assertIn("AED-RULE-019", d.matched_rule_ids)

    def test_merge_denied_when_mergeable_unknown(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(mergeable="UNKNOWN"),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_CLEAN_MERGE_STATE
        )
        self.assertIn("AED-RULE-019", d.matched_rule_ids)

    def test_merge_denied_when_mergeable_none(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable=None)
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_CLEAN_MERGE_STATE
        )
        self.assertIn("AED-RULE-019", d.matched_rule_ids)

    def test_merge_denied_when_mergeable_non_mergeable_even_if_merge_state_clean(
        self,
    ):
        # merge_state_status is CLEAN (default), but mergeable is False.
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(
                mergeable=False, merge_state_status="CLEAN"
            ),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-019", d.matched_rule_ids)

    def test_merge_allowed_when_mergeable_mergeable_string(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable="MERGEABLE")
        )
        self.assertTrue(d.allowed)

    def test_merge_allowed_when_mergeable_true_bool(self):
        # The safer skeleton default is to require explicit GitHub
        # "MERGEABLE" evidence. A Python True is NOT accepted as a
        # substitute: this test pins the fail-closed behavior.
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable=True)
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-019", d.matched_rule_ids)


class TestCodexPingLiveHead(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6JaFHx."""

    def test_codex_ping_denied_when_head_mismatch(self):
        d = evaluate_action(
            AEDActionType.CODEX_PING,
            _clean_state(current_head_sha="a" * 40),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_EXACT_HEAD_AUTHORIZATION
        )
        self.assertIn("AED-RULE-005", d.matched_rule_ids)

    def test_codex_ping_still_denied_for_duplicate_same_head(self):
        d = evaluate_action(
            AEDActionType.CODEX_PING,
            _clean_state(
                codex_ping_comment_id="12345",
                codex_ping_head_sha=ZERO_SHA,
            ),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_NO_DUPLICATE_CODEX_PING
        )
        self.assertIn("AED-RULE-010", d.matched_rule_ids)

    def test_codex_ping_allowed_when_head_matches_and_no_duplicate(self):
        d = evaluate_action(AEDActionType.CODEX_PING, _clean_state())
        self.assertTrue(d.allowed)
        self.assertIn("AED-RULE-005", d.matched_rule_ids)
        self.assertIn("AED-RULE-010", d.matched_rule_ids)

    def test_codex_ping_allowed_when_existing_ping_for_different_head_and_head_matches(
        self,
    ):
        d = evaluate_action(
            AEDActionType.CODEX_PING,
            _clean_state(
                codex_ping_comment_id="12345",
                codex_ping_head_sha="a" * 40,
            ),
        )
        self.assertTrue(d.allowed)


class TestThreadResolveLiveHead(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6JaFHz."""

    def test_thread_resolve_denied_when_head_mismatch(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(
                current_head_sha="a" * 40,
                authorized_thread_ids=["PRRT_abc"],
            ),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_EXACT_HEAD_AUTHORIZATION
        )
        self.assertIn("AED-RULE-005", d.matched_rule_ids)

    def test_thread_resolve_allowed_when_head_matches_and_thread_list_authorized(
        self,
    ):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_abc"]),
            target_thread_ids=["PRRT_abc"],
        )
        self.assertTrue(d.allowed)
        self.assertIn("AED-RULE-005", d.matched_rule_ids)
        self.assertIn("AED-RULE-008", d.matched_rule_ids)

    def test_thread_resolve_head_check_runs_before_thread_list_check(self):
        # If head is mismatched, the head-check denial must take
        # precedence over the thread-list check, even when the
        # thread list is empty.
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(
                current_head_sha="a" * 40,
                authorized_thread_ids=[],
            ),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_EXACT_HEAD_AUTHORIZATION
        )
        # The thread-list code should NOT be the active denial
        # code; the head-check should run first.
        self.assertNotEqual(
            d.code, AEDDecisionCode.REQUIRE_THREAD_LIST_AUTHORIZATION
        )


class TestIsMergeableHelper(unittest.TestCase):
    """Direct unit tests for the _is_mergeable helper."""

    def test_is_mergeable_true_bool(self):
        from aed_policy.policy import _is_mergeable
        # A Python True is NOT accepted as a substitute for the
        # explicit GitHub "MERGEABLE" string.
        self.assertFalse(_is_mergeable(True))

    def test_is_mergeable_false_bool(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable(False))

    def test_is_mergeable_none(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable(None))

    def test_is_mergeable_string_mergeable(self):
        from aed_policy.policy import _is_mergeable
        self.assertTrue(_is_mergeable("MERGEABLE"))

    def test_is_mergeable_string_mergeable_lowercase(self):
        from aed_policy.policy import _is_mergeable
        # Strict variant: "mergeable" must deny; only the exact
        # raw "MERGEABLE" is accepted.
        self.assertFalse(_is_mergeable("mergeable"))

    def test_is_mergeable_string_conflicting(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable("CONFLICTING"))

    def test_is_mergeable_string_unknown(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable("UNKNOWN"))

    def test_is_mergeable_unsupported_type(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable(42))


# ---------------------------------------------------------------------------
# Regression tests for the five new Codex findings on PR #404
# ---------------------------------------------------------------------------
#
# These tests pin the fixes for the following Codex findings:
#   PRRT_kwDOSHFpYM6JaFHu (P1) — Require clean Codex evidence tied to current head
#   PRRT_kwDOSHFpYM6JaFHw (P1) — Reject unmergeable PRs (strict MERGEABLE)
#   PRRT_kwDOSHFpYM6JateB (P1) — Validate exact merge authorization phrase
#   PRRT_kwDOSHFpYM6JateD (P1) — Validate target thread against authorized list
#   PRRT_kwDOSHFpYM6JateF (P2) — Fail closed when protected_pr_numbers empty
#
# Each test below fails on the pre-patch implementation and passes after
# the patch.


class TestCodexCleanEvidenceTiedToCurrentHead(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6JaFHu (strict variant)."""

    def test_merge_denied_when_codex_clean_pass_false(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(
                codex_clean_pass_detected=False,
                codex_clean_pass_head_sha=None,
            ),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.HOLD)
        self.assertIn("AED-RULE-011", d.matched_rule_ids)
        self.assertIn(
            "codex_clean_pass_detected=true", d.required_evidence
        )

    def test_merge_denied_when_clean_pass_is_for_different_head(self):
        # Clean pass detected=True, but the clean-pass head SHA
        # is for a different head. The engine must deny because
        # the clean pass is stale.
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(
                codex_clean_pass_detected=True,
                codex_clean_pass_head_sha="a" * 40,
            ),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.HOLD)
        self.assertIn("AED-RULE-005", d.matched_rule_ids)
        self.assertIn("AED-RULE-011", d.matched_rule_ids)
        self.assertIn(
            "codex_clean_pass_head_sha == expected_head_sha",
            d.required_evidence,
        )

    def test_merge_denied_when_codex_clean_pass_head_sha_is_none(self):
        # A None clean-pass head SHA means the engine has no
        # evidence the clean pass is tied to the current head.
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(
                codex_clean_pass_detected=True,
                codex_clean_pass_head_sha=None,
            ),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.HOLD)
        self.assertIn("AED-RULE-011", d.matched_rule_ids)

    def test_merge_denied_when_codex_newer_finding_true(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(codex_newer_finding_after_clean_pass=True),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.HOLD)
        self.assertIn("AED-RULE-011", d.matched_rule_ids)
        self.assertIn("AED-RULE-012", d.matched_rule_ids)

    def test_merge_allowed_when_clean_pass_tied_to_current_head(self):
        d = evaluate_action(AEDActionType.GITHUB_MERGE, _clean_state())
        self.assertTrue(d.allowed)


class TestMergeableStrictMergeableString(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6JaFHw (strict variant)."""

    def test_merge_denied_when_mergeable_none(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable=None)
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-019", d.matched_rule_ids)

    def test_merge_denied_when_mergeable_unknown(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable="UNKNOWN")
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-019", d.matched_rule_ids)

    def test_merge_denied_when_mergeable_conflicting(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable="CONFLICTING")
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-019", d.matched_rule_ids)

    def test_merge_denied_when_mergeable_false(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable=False)
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-019", d.matched_rule_ids)

    def test_merge_allowed_when_mergeable_strict_mergeable_string(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable="MERGEABLE")
        )
        self.assertTrue(d.allowed)

    def test_merge_allowed_when_mergeable_strict_mergeable_lowercase(self):
        # Strict variant: "mergeable" must deny; only the exact
        # raw "MERGEABLE" is accepted.
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable="mergeable")
        )
        self.assertFalse(d.allowed)


class TestExactMergeAuthorizationPhrase(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6JateB."""

    def test_merge_denied_when_phrase_is_none(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(explicit_authorization_phrase=None),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_EXPLICIT_AUTHORIZATION
        )
        self.assertIn("AED-RULE-007", d.matched_rule_ids)
        self.assertIn("AED-RULE-005", d.matched_rule_ids)

    def test_merge_denied_when_phrase_is_empty(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(explicit_authorization_phrase=""),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-007", d.matched_rule_ids)

    def test_merge_denied_when_phrase_is_arbitrary_text(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(explicit_authorization_phrase="ok"),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-007", d.matched_rule_ids)

    def test_merge_denied_when_phrase_has_wrong_pr_number(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(
                explicit_authorization_phrase=(
                    f"I authorize guarded squash merge of PR #1 at exact head {ZERO_SHA}."
                )
            ),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-007", d.matched_rule_ids)

    def test_merge_denied_when_phrase_has_wrong_head_sha(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(
                explicit_authorization_phrase=(
                    "I authorize guarded squash merge of PR #999 "
                    "at exact head deadbeefdeadbeefdeadbeefdeadbeefdeadbeef."
                )
            ),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-007", d.matched_rule_ids)

    def test_merge_denied_when_phrase_has_short_head_sha(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(
                explicit_authorization_phrase=(
                    "I authorize guarded squash merge of PR #999 at exact head abc."
                )
            ),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-007", d.matched_rule_ids)

    def test_merge_denied_when_phrase_uses_legacy_shorter_format(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(
                explicit_authorization_phrase=(
                    "I authorize guarded squash merge of PR #999 at exact head abc1234."
                )
            ),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-007", d.matched_rule_ids)

    def test_merge_allowed_with_exact_phrase(self):
        # _clean_state already sets the exact phrase for PR #999
        # at the ZERO_SHA head. The merge must be allowed.
        d = evaluate_action(AEDActionType.GITHUB_MERGE, _clean_state())
        self.assertTrue(d.allowed)
        self.assertIn("AED-RULE-007", d.matched_rule_ids)

    def test_merge_denied_when_phrase_has_normalized_whitespace(self):
        # Strict variant: a phrase with extra spaces must deny;
        # the operator phrase is compared to the canonical
        # phrase character-for-character with no whitespace
        # normalization on either side.
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(
                explicit_authorization_phrase=(
                    "I  authorize  guarded  squash  merge  of  PR  #999  "
                    f"at  exact  head  {ZERO_SHA}."
                )
            ),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-007", d.matched_rule_ids)


class TestTargetThreadValidation(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6JateD."""

    def test_thread_resolve_denied_when_no_target_supplied(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_abc"]),
            # No target_thread_ids supplied
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_THREAD_LIST_AUTHORIZATION
        )
        self.assertIn("AED-RULE-008", d.matched_rule_ids)
        self.assertIn("target_thread_ids", str(d.required_evidence))

    def test_thread_resolve_denied_when_target_not_in_authorized(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_target"]),
            target_thread_ids=["PRRT_other"],
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-008", d.matched_rule_ids)
        self.assertIn("PRRT_other", d.reason)

    def test_thread_resolve_denied_when_target_set_includes_unauthorized(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(
                authorized_thread_ids=["PRRT_a", "PRRT_b", "PRRT_c"]
            ),
            target_thread_ids=["PRRT_a", "PRRT_evil"],
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-008", d.matched_rule_ids)
        self.assertIn("PRRT_evil", d.reason)

    def test_thread_resolve_allowed_when_target_exact_authorized(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_abc"]),
            target_thread_ids=["PRRT_abc"],
        )
        self.assertTrue(d.allowed)

    def test_thread_resolve_allowed_when_target_is_strict_subset(self):
        # The action is resolving only PRRT_a, even though the
        # authorization also covers PRRT_b. A strict subset is
        # allowed.
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_a", "PRRT_b"]),
            target_thread_ids=["PRRT_a"],
        )
        self.assertTrue(d.allowed)

    def test_thread_resolve_denied_on_head_mismatch_even_with_authorized_target(
        self,
    ):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(
                current_head_sha="a" * 40,
                authorized_thread_ids=["PRRT_abc"],
            ),
            target_thread_ids=["PRRT_abc"],
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_EXACT_HEAD_AUTHORIZATION
        )
        self.assertIn("AED-RULE-005", d.matched_rule_ids)


class TestProtectedPRFailClosed(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6JateF."""

    def test_default_protected_pr_numbers_includes_historicals(self):
        from aed_policy.run_state import DEFAULT_PROTECTED_PR_NUMBERS
        for pr in (384, 386, 397, 398, 399, 400, 401, 402, 403):
            self.assertIn(pr, DEFAULT_PROTECTED_PR_NUMBERS)

    def test_aed_run_state_default_protected_pr_numbers_set(self):
        # Constructing AEDRunState without explicit
        # protected_pr_numbers must yield the canonical set.
        state = AEDRunState(
            repo="x",
            pr_number=999,
            current_head_sha=ZERO_SHA,
            expected_head_sha=ZERO_SHA,
            branch="x",
            base_branch="main",
            lifecycle_state="x",
            primary_worktree_path="/x",
            primary_worktree_head=ZERO_SHA,
            primary_worktree_branch="main",
            primary_worktree_clean=True,
            isolated_workspace_path="/tmp/aed_runs/worktrees/x",
            isolated_workspace_head=ZERO_SHA,
            isolated_workspace_clean=True,
            ci_status="pass",
            scope_status="clean",
            merge_state_status="CLEAN",
            mergeable="MERGEABLE",
            unresolved_thread_count=0,
            active_thread_count=0,
            outdated_thread_count=0,
            codex_clean_pass_detected=True,
            codex_newer_finding_after_clean_pass=False,
            codex_ping_comment_id=None,
            codex_ping_head_sha=None,
            audit_append_available=True,
            audit_append_only=True,
            explicit_authorization_phrase=AUTH_PHRASE,
            authorized_thread_ids=[],
            # protected_pr_numbers omitted
        )
        self.assertIn(384, state.protected_pr_numbers)
        self.assertIn(403, state.protected_pr_numbers)

    def test_protected_pr_mutation_denied_even_without_explicit_pr_numbers(self):
        # Construct state with pr_number=384 but no
        # protected_pr_numbers; the default factory must still
        # protect #384.
        state = AEDRunState(
            repo="x",
            pr_number=384,
            current_head_sha=ZERO_SHA,
            expected_head_sha=ZERO_SHA,
            branch="x",
            base_branch="main",
            lifecycle_state="x",
            primary_worktree_path="/x",
            primary_worktree_head=ZERO_SHA,
            primary_worktree_branch="main",
            primary_worktree_clean=True,
            isolated_workspace_path="/tmp/aed_runs/worktrees/x",
            isolated_workspace_head=ZERO_SHA,
            isolated_workspace_clean=True,
            ci_status="pass",
            scope_status="clean",
            merge_state_status="CLEAN",
            mergeable="MERGEABLE",
            unresolved_thread_count=0,
            active_thread_count=0,
            outdated_thread_count=0,
            codex_clean_pass_detected=True,
            codex_newer_finding_after_clean_pass=False,
            codex_ping_comment_id=None,
            codex_ping_head_sha=None,
            audit_append_available=True,
            audit_append_only=True,
            explicit_authorization_phrase=AUTH_PHRASE.replace("999", "384"),
            authorized_thread_ids=[],
            # protected_pr_numbers omitted
        )
        d = evaluate_action(AEDActionType.GITHUB_MERGE, state)
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-021", d.matched_rule_ids)

    def test_explicit_empty_protected_pr_numbers_denies_as_missing_evidence(
        self,
    ):
        # Explicitly supplying an empty set is treated as missing
        # protected-PR evidence (fail-closed).
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(protected_pr_numbers=set()),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.DENY)
        self.assertIn("AED-RULE-021", d.matched_rule_ids)

    def test_explicit_empty_protected_pr_numbers_denies_reopen(self):
        d = evaluate_action(
            AEDActionType.GITHUB_REOPEN,
            _clean_state(protected_pr_numbers=set()),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-021", d.matched_rule_ids)


class TestAuthorizationHelpers(unittest.TestCase):
    """Direct unit tests for the authorization helpers."""

    def test_expected_phrase_format(self):
        from aed_policy.policy import expected_merge_authorization_phrase
        from aed_policy.run_state import AEDRunState
        state = _clean_state()
        expected = expected_merge_authorization_phrase(state)
        self.assertEqual(
            expected,
            f"I confirm merge PR #999 at {ZERO_SHA} "
            f"using final-head reviewed clean state.",
        )

    def test_has_exact_authorization_with_matching_phrase(self):
        from aed_policy.policy import has_exact_merge_authorization
        state = _clean_state()
        self.assertTrue(has_exact_merge_authorization(state))

    def test_has_exact_authorization_with_arbitrary_text(self):
        from aed_policy.policy import has_exact_merge_authorization
        state = _clean_state(explicit_authorization_phrase="ok")
        self.assertFalse(has_exact_merge_authorization(state))

    def test_has_exact_authorization_with_empty_phrase(self):
        from aed_policy.policy import has_exact_merge_authorization
        state = _clean_state(explicit_authorization_phrase="")
        self.assertFalse(has_exact_merge_authorization(state))

    def test_has_exact_authorization_with_none_phrase(self):
        from aed_policy.policy import has_exact_merge_authorization
        state = _clean_state(explicit_authorization_phrase=None)
        self.assertFalse(has_exact_merge_authorization(state))

    def test_has_exact_authorization_with_wrong_pr(self):
        from aed_policy.policy import has_exact_merge_authorization
        state = _clean_state(
            explicit_authorization_phrase=(
                f"I authorize guarded squash merge of PR #1 at exact head {ZERO_SHA}."
            )
        )
        self.assertFalse(has_exact_merge_authorization(state))

    def test_has_exact_authorization_with_wrong_head(self):
        from aed_policy.policy import has_exact_merge_authorization
        state = _clean_state(
            explicit_authorization_phrase=(
                "I authorize guarded squash merge of PR #999 "
                "at exact head deadbeefdeadbeefdeadbeefdeadbeefdeadbeef."
            )
        )
        self.assertFalse(has_exact_merge_authorization(state))

    def test_normalize_phrase_strips_whitespace(self):
        from aed_policy.policy import _normalize_phrase
        self.assertEqual(
            _normalize_phrase("  hello  world  "), "hello world"
        )
        self.assertEqual(
            _normalize_phrase("hello\n\n\tworld"), "hello world"
        )
        self.assertEqual(_normalize_phrase(""), "")
        self.assertEqual(_normalize_phrase(None), "")


class TestEvaluateActionTargetThreadParameter(unittest.TestCase):
    """Sanity tests for the new target_thread_ids parameter."""

    def test_target_thread_ids_default_is_empty(self):
        # Calling without target_thread_ids should not raise and
        # should treat the parameter as empty (i.e. for
        # GITHUB_THREAD_RESOLVE: deny).
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_a"]),
        )
        self.assertFalse(d.allowed)

    def test_target_thread_ids_keyword_only(self):
        # target_thread_ids is keyword-only.
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_a"]),
            target_thread_ids=["PRRT_a"],
        )
        self.assertTrue(d.allowed)


# ---------------------------------------------------------------------------
# Strict head SHA validation (PRRT_kwDOSHFpYM6Ja5A0)
# ---------------------------------------------------------------------------


class TestIsFullShaHelper(unittest.TestCase):
    """Regression tests for the new ``_is_full_sha`` helper."""

    def test_is_full_sha_true_on_40_char_hex(self):
        from aed_policy.policy import _is_full_sha
        self.assertTrue(_is_full_sha("a" * 40))
        self.assertTrue(_is_full_sha("0123456789abcdef0123456789ABCDEF01234567"))
        self.assertTrue(_is_full_sha(ZERO_SHA))

    def test_is_full_sha_false_on_abbreviated(self):
        from aed_policy.policy import _is_full_sha
        self.assertFalse(_is_full_sha("abc1234"))
        self.assertFalse(_is_full_sha(""))
        self.assertFalse(_is_full_sha("1234567"))

    def test_is_full_sha_false_on_non_hex_40_chars(self):
        from aed_policy.policy import _is_full_sha
        # 40 characters but contains non-hex chars (e.g. 'z').
        self.assertFalse(_is_full_sha("z" * 40))
        self.assertFalse(_is_full_sha("a" * 39 + "z"))

    def test_is_full_sha_false_on_none_and_non_string(self):
        from aed_policy.policy import _is_full_sha
        self.assertFalse(_is_full_sha(None))
        self.assertFalse(_is_full_sha(1234567890))
        self.assertFalse(_is_full_sha(True))

    def test_is_full_sha_false_on_41_char_string(self):
        from aed_policy.policy import _is_full_sha
        self.assertFalse(_is_full_sha("a" * 41))


class TestHeadMatchesStrictFullSha(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6Ja5A0.

    ``_head_matches`` must reject matching abbreviated SHAs and
    accept only matching full 40-character hex SHAs.
    """

    def test_head_matches_false_on_abbreviated_matching(self):
        from aed_policy.policy import _head_matches
        s = _clean_state(current_head_sha="abc1234", expected_head_sha="abc1234")
        self.assertFalse(_head_matches(s))

    def test_head_matches_false_on_non_hex_40_matching(self):
        from aed_policy.policy import _head_matches
        bad = "z" * 40
        s = _clean_state(current_head_sha=bad, expected_head_sha=bad)
        self.assertFalse(_head_matches(s))

    def test_head_matches_false_on_none(self):
        from aed_policy.policy import _head_matches
        s = _clean_state(current_head_sha=None, expected_head_sha=ZERO_SHA)
        self.assertFalse(_head_matches(s))
        s = _clean_state(current_head_sha=ZERO_SHA, expected_head_sha=None)
        self.assertFalse(_head_matches(s))

    def test_head_matches_true_on_full_hex_matching(self):
        from aed_policy.policy import _head_matches
        s = _clean_state()
        self.assertTrue(_head_matches(s))

    def test_head_matches_false_on_full_hex_different(self):
        from aed_policy.policy import _head_matches
        s = _clean_state(
            current_head_sha="a" * 40, expected_head_sha="b" * 40
        )
        self.assertFalse(_head_matches(s))


class TestCodexPingDeniesAbbreviatedHead(unittest.TestCase):
    """CODEX_PING must deny on matching abbreviated heads (AED-RULE-004)."""

    def test_codex_ping_denied_on_abbreviated_matching_head(self):
        d = evaluate_action(
            AEDActionType.CODEX_PING,
            _clean_state(
                current_head_sha="abc1234", expected_head_sha="abc1234"
            ),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-005", d.matched_rule_ids)


class TestThreadResolveDeniesAbbreviatedHead(unittest.TestCase):
    """GITHUB_THREAD_RESOLVE must deny on matching abbreviated heads."""

    def test_thread_resolve_denied_on_abbreviated_matching_head(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(
                current_head_sha="abc1234",
                expected_head_sha="abc1234",
                authorized_thread_ids=["PRRT_a"],
            ),
            target_thread_ids=["PRRT_a"],
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-005", d.matched_rule_ids)


class TestMergeDeniesAbbreviatedHead(unittest.TestCase):
    """GITHUB_MERGE must deny on matching abbreviated heads."""

    def test_merge_denied_on_abbreviated_matching_head(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(
                current_head_sha="abc1234", expected_head_sha="abc1234"
            ),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-005", d.matched_rule_ids)


# ---------------------------------------------------------------------------
# Strict raw MERGEABLE string (PRRT_kwDOSHFpYM6Ja5Av)
# ---------------------------------------------------------------------------


class TestIsMergeableStrictRawString(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6Ja5Av.

    ``_is_mergeable`` must allow only the exact raw GitHub string
    ``"MERGEABLE"``. No case folding, no whitespace stripping, no
    Python True compatibility, no normalization.
    """

    def test_is_mergeable_allows_only_exact_MERGEABLE(self):
        from aed_policy.policy import _is_mergeable
        self.assertTrue(_is_mergeable("MERGEABLE"))

    def test_is_mergeable_denies_lowercase(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable("mergeable"))

    def test_is_mergeable_denies_uppercase_mixed(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable("Mergeable"))
        self.assertFalse(_is_mergeable("MERGEABLE "))
        self.assertFalse(_is_mergeable(" MERGEABLE"))

    def test_is_mergeable_denies_trailing_newline(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable("MERGEABLE\n"))

    def test_is_mergeable_denies_python_true(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable(True))

    def test_is_mergeable_denies_none(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable(None))

    def test_is_mergeable_denies_false(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable(False))

    def test_is_mergeable_denies_unknown(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable("UNKNOWN"))

    def test_is_mergeable_denies_conflicting(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable("CONFLICTING"))

    def test_is_mergeable_denies_unsupported_types(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable(42))
        self.assertFalse(_is_mergeable(["MERGEABLE"]))
        self.assertFalse(_is_mergeable({"value": "MERGEABLE"}))


class TestMergeStrictMergeableString(unittest.TestCase):
    """GITHUB_MERGE must require the exact raw ``"MERGEABLE"`` string."""

    def test_merge_denied_when_mergeable_lowercase(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable="mergeable")
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-019", d.matched_rule_ids)

    def test_merge_denied_when_mergeable_padded(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable=" MERGEABLE ")
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-019", d.matched_rule_ids)

    def test_merge_denied_when_mergeable_trailing_newline(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable="MERGEABLE\n")
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-019", d.matched_rule_ids)


# ---------------------------------------------------------------------------
# Strict exact authorization phrase (PRRT_kwDOSHFpYM6Ja5Ay)
# ---------------------------------------------------------------------------


class TestHasExactMergeAuthorizationStrict(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6Ja5Ay.

    ``has_exact_merge_authorization`` must compare the operator
    phrase to the canonical exact-head phrase character-for-character,
    with no whitespace stripping, no whitespace collapsing, no
    embedded-newline tolerance, and no case folding.
    """

    def test_allows_only_exact_canonical_phrase(self):
        from aed_policy.policy import has_exact_merge_authorization
        s = _clean_state()
        self.assertTrue(has_exact_merge_authorization(s))

    def test_denies_phrase_with_leading_space(self):
        from aed_policy.policy import has_exact_merge_authorization
        s = _clean_state(
            explicit_authorization_phrase=(
                f" I authorize guarded squash merge of PR #999 at exact head {ZERO_SHA}."
            )
        )
        self.assertFalse(has_exact_merge_authorization(s))

    def test_denies_phrase_with_trailing_space(self):
        from aed_policy.policy import has_exact_merge_authorization
        s = _clean_state(
            explicit_authorization_phrase=(
                f"I authorize guarded squash merge of PR #999 at exact head {ZERO_SHA}. "
            )
        )
        self.assertFalse(has_exact_merge_authorization(s))

    def test_denies_phrase_with_double_space(self):
        from aed_policy.policy import has_exact_merge_authorization
        s = _clean_state(
            explicit_authorization_phrase=(
                f"I authorize  guarded squash merge of PR #999 at exact head {ZERO_SHA}."
            )
        )
        self.assertFalse(has_exact_merge_authorization(s))

    def test_denies_phrase_with_embedded_newline(self):
        from aed_policy.policy import has_exact_merge_authorization
        s = _clean_state(
            explicit_authorization_phrase=(
                f"I authorize guarded squash merge\nof PR #999 at exact head {ZERO_SHA}."
            )
        )
        self.assertFalse(has_exact_merge_authorization(s))

    def test_denies_phrase_with_wrong_pr_number(self):
        from aed_policy.policy import has_exact_merge_authorization
        s = _clean_state(
            explicit_authorization_phrase=(
                f"I authorize guarded squash merge of PR #1 at exact head {ZERO_SHA}."
            )
        )
        self.assertFalse(has_exact_merge_authorization(s))

    def test_denies_phrase_with_wrong_head(self):
        from aed_policy.policy import has_exact_merge_authorization
        s = _clean_state(
            explicit_authorization_phrase=(
                "I authorize guarded squash merge of PR #999 "
                "at exact head " + "a" * 40 + "."
            )
        )
        self.assertFalse(has_exact_merge_authorization(s))

    def test_denies_phrase_with_abbreviated_head(self):
        from aed_policy.policy import has_exact_merge_authorization
        s = _clean_state(
            explicit_authorization_phrase=(
                "I authorize guarded squash merge of PR #999 at exact head abc1234."
            )
        )
        self.assertFalse(has_exact_merge_authorization(s))

    def test_denies_phrase_with_none_phrase(self):
        from aed_policy.policy import has_exact_merge_authorization
        s = _clean_state(explicit_authorization_phrase=None)
        self.assertFalse(has_exact_merge_authorization(s))

    def test_denies_phrase_with_empty_phrase(self):
        from aed_policy.policy import has_exact_merge_authorization
        s = _clean_state(explicit_authorization_phrase="")
        self.assertFalse(has_exact_merge_authorization(s))

    def test_denies_phrase_with_arbitrary_text(self):
        from aed_policy.policy import has_exact_merge_authorization
        s = _clean_state(explicit_authorization_phrase="ok")
        s2 = _clean_state(explicit_authorization_phrase="merge")
        self.assertFalse(has_exact_merge_authorization(s))
        self.assertFalse(has_exact_merge_authorization(s2))

    def test_merge_denied_when_phrase_does_not_match_exactly(self):
        # Confirms the strict comparison flows through GITHUB_MERGE.
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(
                explicit_authorization_phrase=(
                    f" I authorize guarded squash merge of PR #999 at exact head {ZERO_SHA}."
                )
            ),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-007", d.matched_rule_ids)


# ---------------------------------------------------------------------------
# Codex clean-pass tied to FULL current head (PRRT_kwDOSHFpYM6JaFHu strict)
# ---------------------------------------------------------------------------


class TestCodexCleanPassForCurrentHead(unittest.TestCase):
    """Regression tests for the strict JaFHu variant.

    A merge must require both:
    - ``codex_clean_pass_for_current_head=True`` (the harness's
      derived current-head indicator)
    - ``codex_clean_pass_head_sha`` is a full 40-character hex
      SHA equal to ``expected_head_sha``
    """

    def test_merge_denied_when_for_current_head_is_false(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(codex_clean_pass_for_current_head=False),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-011", d.matched_rule_ids)

    def test_merge_denied_when_clean_pass_head_sha_is_none(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(codex_clean_pass_head_sha=None),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-011", d.matched_rule_ids)

    def test_merge_denied_when_clean_pass_head_sha_is_abbreviated(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(codex_clean_pass_head_sha="abc1234"),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-011", d.matched_rule_ids)

    def test_merge_denied_when_clean_pass_head_sha_is_non_hex_40(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(codex_clean_pass_head_sha="z" * 40),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-011", d.matched_rule_ids)

    def test_merge_denied_when_clean_pass_head_sha_differs_from_expected(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(codex_clean_pass_head_sha="a" * 40),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-011", d.matched_rule_ids)

    def test_merge_allowed_when_clean_pass_tied_to_full_current_head(self):
        d = evaluate_action(AEDActionType.GITHUB_MERGE, _clean_state())
        self.assertTrue(d.allowed)


# ---------------------------------------------------------------------------
# Strict target thread set (PRRT_kwDOSHFpYM6JateD strict)
# ---------------------------------------------------------------------------


class TestTargetThreadStrict(unittest.TestCase):
    """Regression tests for the strict JateD variant.

    Every target thread ID must be in the authorized set, and
    empty target IDs are rejected.
    """

    def test_thread_resolve_denied_when_target_is_empty(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_a"]),
            target_thread_ids=[],
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-008", d.matched_rule_ids)

    def test_thread_resolve_denied_when_target_id_is_empty_string(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_a"]),
            target_thread_ids=[""],
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-008", d.matched_rule_ids)

    def test_thread_resolve_denied_when_any_target_id_unauthorized(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_a"]),
            target_thread_ids=["PRRT_a", "PRRT_other"],
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-008", d.matched_rule_ids)
        self.assertIn("PRRT_other", d.reason)

    def test_thread_resolve_allowed_when_target_is_strict_subset(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_a", "PRRT_b"]),
            target_thread_ids=["PRRT_a"],
        )
        self.assertTrue(d.allowed)

    def test_thread_resolve_allowed_when_target_equals_authorized(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_a", "PRRT_b"]),
            target_thread_ids=["PRRT_a", "PRRT_b"],
        )
        self.assertTrue(d.allowed)

    def test_thread_resolve_denied_on_head_mismatch_with_authorized_target(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(
                authorized_thread_ids=["PRRT_a"],
                current_head_sha="a" * 40,
            ),
            target_thread_ids=["PRRT_a"],
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-005", d.matched_rule_ids)

    def test_thread_resolve_denied_on_duplicates_treated_as_set(self):
        # Duplicate target IDs are normalized to a set for
        # comparison, so a duplicated authorized target is still
        # allowed (the set is a subset of the authorized set).
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_a"]),
            target_thread_ids=["PRRT_a", "PRRT_a"],
        )
        self.assertTrue(d.allowed)

    def test_thread_resolve_denied_with_duplicate_unauthorized(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_a"]),
            target_thread_ids=["PRRT_a", "PRRT_other", "PRRT_other"],
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-008", d.matched_rule_ids)


# ---------------------------------------------------------------------------
# JbKcR: canonicalize isolated workspace path
# ---------------------------------------------------------------------------


class TestInIsolatedWorkspaceCanonicalize(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6JbKcR.

    ``_in_isolated_workspace`` must canonicalize the candidate
    workspace path (dot-segment and symlink resolution) before
    comparison, and verify the resolved path is actually inside
    ``/tmp/aed_runs/worktrees``.
    """

    def test_in_isolated_workspace_denies_none(self):
        from aed_policy.policy import _in_isolated_workspace
        s = _clean_state(isolated_workspace_path=None)
        self.assertFalse(_in_isolated_workspace(s))

    def test_in_isolated_workspace_denies_empty_string(self):
        from aed_policy.policy import _in_isolated_workspace
        s = _clean_state(isolated_workspace_path="")
        self.assertFalse(_in_isolated_workspace(s))

    def test_in_isolated_workspace_denies_non_string(self):
        from aed_policy.policy import _in_isolated_workspace
        s = _clean_state(isolated_workspace_path=12345)
        self.assertFalse(_in_isolated_workspace(s))
        s = _clean_state(isolated_workspace_path=["/tmp/aed_runs/worktrees/x"])
        self.assertFalse(_in_isolated_workspace(s))

    def test_in_isolated_workspace_denies_dot_segment_escape(self):
        # Dot-segment escape that resolves outside the
        # /tmp/aed_runs/worktrees root must deny.
        from aed_policy.policy import _in_isolated_workspace
        # /tmp/aed_runs/worktrees/../..  -> /tmp; deny
        s = _clean_state(
            isolated_workspace_path="/tmp/aed_runs/worktrees/../../home/max/Automated-Edge-Discovery"
        )
        self.assertFalse(_in_isolated_workspace(s))

    def test_in_isolated_workspace_denies_resolved_path_outside_root(self):
        from aed_policy.policy import _in_isolated_workspace
        # Even with trailing slash, resolution lands at /home/...
        s = _clean_state(isolated_workspace_path="/home/max/Automated-Edge-Discovery")
        self.assertFalse(_in_isolated_workspace(s))

    def test_in_isolated_workspace_denies_sibling_root_prefix(self):
        # /tmp/aed_runs/worktrees_evil/x has a sibling-prefix
        # of /tmp/aed_runs/worktrees but is NOT inside it; deny.
        from aed_policy.policy import _in_isolated_workspace
        s = _clean_state(isolated_workspace_path="/tmp/aed_runs/worktrees_evil/x")
        self.assertFalse(_in_isolated_workspace(s))

    def test_in_isolated_workspace_allows_normal_path(self):
        from aed_policy.policy import _in_isolated_workspace
        # _clean_state already sets a /tmp/aed_runs/worktrees path.
        s = _clean_state(isolated_workspace_path="/tmp/aed_runs/worktrees/aed_policy_engine_skeleton_v1")
        self.assertTrue(_in_isolated_workspace(s))

    def test_in_isolated_workspace_allows_trailing_separator_path(self):
        from aed_policy.policy import _in_isolated_workspace
        s = _clean_state(isolated_workspace_path="/tmp/aed_runs/worktrees/aed_x/")
        self.assertTrue(_in_isolated_workspace(s))

    def test_in_isolated_workspace_denies_primary_path(self):
        from aed_policy.policy import _in_isolated_workspace
        s = _clean_state(isolated_workspace_path="/home/max/Automated-Edge-Discovery")
        self.assertFalse(_in_isolated_workspace(s))


class TestMutatingActionsCanonicalizePath(unittest.TestCase):
    """MUTATING_LOCAL_ACTIONS must deny when workspace resolves outside root."""

    def test_file_write_denied_on_escape(self):
        from aed_policy.action_types import AEDActionType
        s = _clean_state(
            isolated_workspace_path="/tmp/aed_runs/worktrees/../../home/max/Automated-Edge-Discovery"
        )
        d = evaluate_action(AEDActionType.FILE_WRITE, s)
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-003", d.matched_rule_ids)

    def test_terminal_mutation_denied_on_escape(self):
        from aed_policy.action_types import AEDActionType
        s = _clean_state(
            isolated_workspace_path="/tmp/aed_runs/worktrees/../../home/max/Automated-Edge-Discovery"
        )
        # Use a mutating terminal action; the policy engine maps
        # them to MUTATING_LOCAL_ACTIONS.
        for mut_action in (
            AEDActionType.FILE_WRITE,
            AEDActionType.TERMINAL_MUTATING,
            AEDActionType.GIT_MUTATING,
        ):
            d = evaluate_action(mut_action, s)
            self.assertFalse(d.allowed, msg=f"{mut_action.value} should be denied")
            self.assertIn(
                "AED-RULE-003", d.matched_rule_ids,
                msg=f"{mut_action.value} should cite AED-RULE-003",
            )


# ---------------------------------------------------------------------------
# JbKcU: live canonical merge authorization phrase
# ---------------------------------------------------------------------------


# Build the live canonical phrase from the same Python source
# the live scripts use, so the test is not just an inline
# string but actually the same shape the live producers emit.
LIVE_CANONICAL_AUTH_PHRASE = (
    f"I confirm merge PR #999 at {ZERO_SHA} "
    f"using final-head reviewed clean state."
)


class TestExpectedPhraseMatchesLive(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6JbKcU."""

    def test_expected_phrase_matches_live_canonical(self):
        from aed_policy.policy import expected_merge_authorization_phrase
        s = _clean_state()
        self.assertEqual(
            expected_merge_authorization_phrase(s),
            LIVE_CANONICAL_AUTH_PHRASE,
        )

    def test_expected_phrase_built_via_live_producer(self):
        # Independently compute the live producer's phrase and
        # compare to the policy engine's phrase. This proves the
        # policy engine is aligned with the live producer
        # character-for-character.
        from aed_policy.policy import expected_merge_authorization_phrase
        s = _clean_state()
        independent = (
            f"I confirm merge PR #{s.pr_number} at {s.expected_head_sha} "
            f"using final-head reviewed clean state."
        )
        self.assertEqual(
            expected_merge_authorization_phrase(s), independent
        )

    def test_has_exact_authorization_accepts_live_canonical_phrase(self):
        from aed_policy.policy import has_exact_merge_authorization
        s = _clean_state(explicit_authorization_phrase=LIVE_CANONICAL_AUTH_PHRASE)
        self.assertTrue(has_exact_merge_authorization(s))

    def test_has_exact_authorization_rejects_old_policy_only_phrase(self):
        from aed_policy.policy import has_exact_merge_authorization
        # The OLD policy-only phrase must be rejected now that
        # the policy engine is aligned with the live canonical
        # phrase.
        old_phrase = (
            f"I authorize guarded squash merge of PR #999 at exact head {ZERO_SHA}."
        )
        s = _clean_state(explicit_authorization_phrase=old_phrase)
        self.assertFalse(has_exact_merge_authorization(s))

    def test_has_exact_authorization_rejects_leading_space(self):
        from aed_policy.policy import has_exact_merge_authorization
        s = _clean_state(explicit_authorization_phrase=(" " + LIVE_CANONICAL_AUTH_PHRASE))
        self.assertFalse(has_exact_merge_authorization(s))

    def test_has_exact_authorization_rejects_trailing_space(self):
        from aed_policy.policy import has_exact_merge_authorization
        s = _clean_state(explicit_authorization_phrase=(LIVE_CANONICAL_AUTH_PHRASE + " "))
        self.assertFalse(has_exact_merge_authorization(s))

    def test_has_exact_authorization_rejects_embedded_newline(self):
        from aed_policy.policy import has_exact_merge_authorization
        s = _clean_state(
            explicit_authorization_phrase=(
                "I confirm merge PR #999 at " + ZERO_SHA + "\n"
                "using final-head reviewed clean state."
            )
        )
        self.assertFalse(has_exact_merge_authorization(s))

    def test_has_exact_authorization_rejects_wrong_pr_number(self):
        from aed_policy.policy import has_exact_merge_authorization
        bad = LIVE_CANONICAL_AUTH_PHRASE.replace("PR #999", "PR #1")
        s = _clean_state(explicit_authorization_phrase=bad)
        self.assertFalse(has_exact_merge_authorization(s))

    def test_has_exact_authorization_rejects_wrong_head(self):
        from aed_policy.policy import has_exact_merge_authorization
        bad = LIVE_CANONICAL_AUTH_PHRASE.replace(ZERO_SHA, "a" * 40)
        s = _clean_state(explicit_authorization_phrase=bad)
        self.assertFalse(has_exact_merge_authorization(s))

    def test_has_exact_authorization_rejects_abbreviated_head(self):
        from aed_policy.policy import has_exact_merge_authorization
        bad = LIVE_CANONICAL_AUTH_PHRASE.replace(ZERO_SHA, "abc1234")
        s = _clean_state(explicit_authorization_phrase=bad)
        self.assertFalse(has_exact_merge_authorization(s))

    def test_merge_denied_unless_live_canonical_phrase_matches(self):
        # The merge path must require the live canonical phrase
        # in GITHUB_MERGE.
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(explicit_authorization_phrase="ok"),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-007", d.matched_rule_ids)

    def test_merge_allowed_with_live_canonical_phrase(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(explicit_authorization_phrase=LIVE_CANONICAL_AUTH_PHRASE),
        )
        self.assertTrue(d.allowed)


# ---------------------------------------------------------------------------
# Still-active prior findings: extra regression tests pinning behavior
# ---------------------------------------------------------------------------


class TestPriorFindingsStillActive(unittest.TestCase):
    """Regression tests for the four still-active prior findings.

    These tests pin behavior already covered in the existing
    test classes; they exist to make the "all prior findings
    still hold after the latest fix turn" guarantee explicit in
    the test report.
    """

    def test_jaFHu_merge_denied_without_clean_evidence(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(codex_clean_pass_detected=False),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-011", d.matched_rule_ids)

    def test_jaFHu_merge_denied_with_stale_clean_pass(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(
                codex_clean_pass_detected=True,
                codex_clean_pass_for_current_head=False,
                codex_clean_pass_head_sha="a" * 40,
            ),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-011", d.matched_rule_ids)

    def test_jaFHw_merge_denied_when_mergeable_not_MERGEABLE(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable=None)
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-019", d.matched_rule_ids)
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable="CONFLICTING")
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-019", d.matched_rule_ids)

    def test_jaFHw_merge_allowed_only_when_mergeable_is_MERGEABLE(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable="MERGEABLE")
        )
        self.assertTrue(d.allowed)

    def test_jateD_thread_resolve_denied_when_no_target(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_a"]),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-008", d.matched_rule_ids)

    def test_jateD_thread_resolve_denied_when_unauthorized_target(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(authorized_thread_ids=["PRRT_a"]),
            target_thread_ids=["PRRT_other"],
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-008", d.matched_rule_ids)

    def test_ja5A0_codex_ping_denied_on_abbreviated_head(self):
        d = evaluate_action(
            AEDActionType.CODEX_PING,
            _clean_state(current_head_sha="abc1234", expected_head_sha="abc1234"),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-005", d.matched_rule_ids)

    def test_ja5A0_thread_resolve_denied_on_abbreviated_head(self):
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            _clean_state(
                current_head_sha="abc1234",
                expected_head_sha="abc1234",
                authorized_thread_ids=["PRRT_a"],
            ),
            target_thread_ids=["PRRT_a"],
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-005", d.matched_rule_ids)

    def test_ja5A0_merge_denied_on_abbreviated_head(self):
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE,
            _clean_state(current_head_sha="abc1234", expected_head_sha="abc1234"),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-005", d.matched_rule_ids)


# ---------------------------------------------------------------------------
# JbcAl: reject /tmp/aed_runs/worktrees root as an isolated workspace
# ---------------------------------------------------------------------------


class TestInIsolatedWorkspaceRejectsRoot(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6JbcAl.

    ``_in_isolated_workspace`` must deny when the canonicalized
    isolated workspace path is exactly
    ``/tmp/aed_runs/worktrees``. The policy engine requires a
    per-task isolated worktree under
    ``/tmp/aed_runs/worktrees/<task-name>``, never the shared
    worktree parent itself.
    """

    def test_in_isolated_workspace_denies_exact_root(self):
        from aed_policy.policy import _in_isolated_workspace
        s = _clean_state(isolated_workspace_path="/tmp/aed_runs/worktrees")
        self.assertFalse(_in_isolated_workspace(s))

    def test_in_isolated_workspace_denies_root_with_trailing_slash(self):
        from aed_policy.policy import _in_isolated_workspace
        s = _clean_state(isolated_workspace_path="/tmp/aed_runs/worktrees/")
        self.assertFalse(_in_isolated_workspace(s))

    def test_in_isolated_workspace_denies_path_canonicalizing_to_root(self):
        # /tmp/aed_runs/worktrees/. canonicalizes to /tmp/aed_runs/worktrees.
        from aed_policy.policy import _in_isolated_workspace
        s = _clean_state(isolated_workspace_path="/tmp/aed_runs/worktrees/.")
        self.assertFalse(_in_isolated_workspace(s))

    def test_in_isolated_workspace_allows_named_workspace(self):
        from aed_policy.policy import _in_isolated_workspace
        s = _clean_state(
            isolated_workspace_path="/tmp/aed_runs/worktrees/aed_policy_engine_skeleton_v1"
        )
        self.assertTrue(_in_isolated_workspace(s))

    def test_in_isolated_workspace_allows_child_path(self):
        from aed_policy.policy import _in_isolated_workspace
        s = _clean_state(
            isolated_workspace_path="/tmp/aed_runs/worktrees/aed_x/subdir/nested"
        )
        self.assertTrue(_in_isolated_workspace(s))

    def test_in_isolated_workspace_still_denies_sibling_prefix(self):
        from aed_policy.policy import _in_isolated_workspace
        s = _clean_state(isolated_workspace_path="/tmp/aed_runs/worktrees_evil/x")
        self.assertFalse(_in_isolated_workspace(s))

    def test_in_isolated_workspace_still_denies_dot_segment_escape(self):
        from aed_policy.policy import _in_isolated_workspace
        s = _clean_state(
            isolated_workspace_path="/tmp/aed_runs/worktrees/../../home/max/Automated-Edge-Discovery"
        )
        self.assertFalse(_in_isolated_workspace(s))

    def test_in_isolated_workspace_still_denies_none_and_empty(self):
        from aed_policy.policy import _in_isolated_workspace
        s = _clean_state(isolated_workspace_path=None)
        self.assertFalse(_in_isolated_workspace(s))
        s = _clean_state(isolated_workspace_path="")
        self.assertFalse(_in_isolated_workspace(s))


class TestMutatingActionsRejectsRoot(unittest.TestCase):
    """MUTATING_LOCAL_ACTIONS must deny when the workspace path is the root."""

    def test_file_write_denied_at_root(self):
        s = _clean_state(isolated_workspace_path="/tmp/aed_runs/worktrees")
        d = evaluate_action(AEDActionType.FILE_WRITE, s)
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-003", d.matched_rule_ids)

    def test_terminal_mutating_denied_at_root(self):
        s = _clean_state(isolated_workspace_path="/tmp/aed_runs/worktrees")
        d = evaluate_action(AEDActionType.TERMINAL_MUTATING, s)
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-003", d.matched_rule_ids)

    def test_git_mutating_denied_at_root(self):
        s = _clean_state(isolated_workspace_path="/tmp/aed_runs/worktrees")
        d = evaluate_action(AEDActionType.GIT_MUTATING, s)
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-003", d.matched_rule_ids)

    def test_root_denial_includes_AED_RULE_003(self):
        s = _clean_state(isolated_workspace_path="/tmp/aed_runs/worktrees")
        d = evaluate_action(AEDActionType.FILE_WRITE, s)
        # AED-RULE-003 must be cited explicitly so the decision is
        # auditable end-to-end.
        self.assertIn("AED-RULE-003", d.matched_rule_ids)


# ---------------------------------------------------------------------------
# JcYvO: validate thread IDs before set/sort/comparison
# ---------------------------------------------------------------------------


class TestIsValidThreadIdHelper(unittest.TestCase):
    """Regression tests for the new ``_is_valid_thread_id`` helper."""

    def test_is_valid_thread_id_true_on_string(self):
        from aed_policy.policy import _is_valid_thread_id
        self.assertTrue(_is_valid_thread_id("PRRT_a"))
        self.assertTrue(_is_valid_thread_id("x"))

    def test_is_valid_thread_id_false_on_none(self):
        from aed_policy.policy import _is_valid_thread_id
        self.assertFalse(_is_valid_thread_id(None))

    def test_is_valid_thread_id_false_on_int(self):
        from aed_policy.policy import _is_valid_thread_id
        self.assertFalse(_is_valid_thread_id(123))

    def test_is_valid_thread_id_false_on_list(self):
        from aed_policy.policy import _is_valid_thread_id
        self.assertFalse(_is_valid_thread_id([]))
        self.assertFalse(_is_valid_thread_id(["PRRT_a"]))

    def test_is_valid_thread_id_false_on_dict(self):
        from aed_policy.policy import _is_valid_thread_id
        self.assertFalse(_is_valid_thread_id({}))
        self.assertFalse(_is_valid_thread_id({"id": "PRRT_a"}))

    def test_is_valid_thread_id_false_on_empty_string(self):
        from aed_policy.policy import _is_valid_thread_id
        self.assertFalse(_is_valid_thread_id(""))

    def test_is_valid_thread_id_false_on_bool(self):
        from aed_policy.policy import _is_valid_thread_id
        # bool is a subclass of int; it is not a valid thread ID.
        self.assertFalse(_is_valid_thread_id(True))
        self.assertFalse(_is_valid_thread_id(False))


class TestValidateThreadIdsHelper(unittest.TestCase):
    """Regression tests for ``_validate_thread_ids``."""

    def test_validate_thread_ids_returns_none_for_valid(self):
        from aed_policy.policy import _validate_thread_ids
        result = _validate_thread_ids(["PRRT_a", "PRRT_b"], "target")
        self.assertIsNone(result)

    def test_validate_thread_ids_denies_non_list(self):
        from aed_policy.policy import _validate_thread_ids
        result = _validate_thread_ids("PRRT_a", "target")
        self.assertIsNotNone(result)
        self.assertFalse(result.allowed)
        self.assertIn("AED-RULE-008", result.matched_rule_ids)

    def test_validate_thread_ids_denies_empty_list(self):
        from aed_policy.policy import _validate_thread_ids
        result = _validate_thread_ids([], "target")
        self.assertIsNotNone(result)
        self.assertFalse(result.allowed)
        self.assertIn("AED-RULE-008", result.matched_rule_ids)

    def test_validate_thread_ids_denies_none_entry(self):
        from aed_policy.policy import _validate_thread_ids
        result = _validate_thread_ids(["PRRT_a", None], "target")
        self.assertIsNotNone(result)
        self.assertFalse(result.allowed)
        self.assertIn("AED-RULE-008", result.matched_rule_ids)

    def test_validate_thread_ids_denies_int_entry(self):
        from aed_policy.policy import _validate_thread_ids
        result = _validate_thread_ids([42], "target")
        self.assertIsNotNone(result)
        self.assertFalse(result.allowed)
        self.assertIn("AED-RULE-008", result.matched_rule_ids)

    def test_validate_thread_ids_denies_empty_string_entry(self):
        from aed_policy.policy import _validate_thread_ids
        result = _validate_thread_ids([""], "target")
        self.assertIsNotNone(result)
        self.assertFalse(result.allowed)
        self.assertIn("AED-RULE-008", result.matched_rule_ids)


class TestThreadResolveMalformedTargetIds(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6JcYvO (malformed target IDs)."""

    def _assert_denied_no_TypeError(self, d):
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-008", d.matched_rule_ids)

    def test_target_None_denied(self):
        s = _clean_state(authorized_thread_ids=["PRRT_a"])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=[None],
        )
        self._assert_denied_no_TypeError(d)

    def test_target_int_denied(self):
        s = _clean_state(authorized_thread_ids=["PRRT_a"])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=[123],
        )
        self._assert_denied_no_TypeError(d)

    def test_target_list_denied(self):
        s = _clean_state(authorized_thread_ids=["PRRT_a"])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=[["PRRT_a"]],
        )
        self._assert_denied_no_TypeError(d)

    def test_target_dict_denied(self):
        s = _clean_state(authorized_thread_ids=["PRRT_a"])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=[{"id": "PRRT_a"}],
        )
        self._assert_denied_no_TypeError(d)

    def test_target_empty_string_denied(self):
        s = _clean_state(authorized_thread_ids=["PRRT_a"])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=[""],
        )
        self._assert_denied_no_TypeError(d)

    def test_mixed_target_None_and_string(self):
        s = _clean_state(authorized_thread_ids=["PRRT_target"])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=[None, "PRRT_other"],
        )
        # No TypeError; denial cites AED-RULE-008.
        self._assert_denied_no_TypeError(d)


class TestThreadResolveMalformedAuthorizedIds(unittest.TestCase):
    """Regression tests for malformed authorized_thread_ids."""

    def _assert_denied(self, d):
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-008", d.matched_rule_ids)

    def test_authorized_None_denied(self):
        s = _clean_state(authorized_thread_ids=[None])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=["PRRT_a"],
        )
        self._assert_denied(d)

    def test_authorized_int_denied(self):
        s = _clean_state(authorized_thread_ids=[1, 2, 3])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=["PRRT_a"],
        )
        self._assert_denied(d)

    def test_authorized_list_denied(self):
        s = _clean_state(authorized_thread_ids=[["PRRT_a"]])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=["PRRT_a"],
        )
        self._assert_denied(d)

    def test_authorized_dict_denied(self):
        s = _clean_state(authorized_thread_ids=[{"id": "PRRT_a"}])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=["PRRT_a"],
        )
        self._assert_denied(d)

    def test_authorized_empty_string_denied(self):
        s = _clean_state(authorized_thread_ids=[""])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=["PRRT_a"],
        )
        self._assert_denied(d)

    def test_mixed_authorized_None_and_string(self):
        s = _clean_state(authorized_thread_ids=[None, "PRRT_target"])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=["PRRT_other"],
        )
        # No TypeError; denial cites AED-RULE-008.
        self._assert_denied(d)


class TestThreadResolveValidIdsStillWork(unittest.TestCase):
    """Valid IDs continue to be accepted after the malformed-ID hardening."""

    def test_valid_strict_subset_allowed(self):
        s = _clean_state(authorized_thread_ids=["PRRT_a", "PRRT_b"])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=["PRRT_a"],
        )
        self.assertTrue(d.allowed)

    def test_valid_equal_set_allowed(self):
        s = _clean_state(authorized_thread_ids=["PRRT_a", "PRRT_b"])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=["PRRT_a", "PRRT_b"],
        )
        self.assertTrue(d.allowed)

    def test_valid_unauthorized_string_still_denied(self):
        s = _clean_state(authorized_thread_ids=["PRRT_a"])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=["PRRT_other"],
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-008", d.matched_rule_ids)

    def test_valid_target_with_authorized_containing_unauthorized_string(self):
        # When the target list is valid but the authorized list
        # also contains an unauthorized string, target validation
        # passes and authorized validation passes (no type
        # issues), so the resolution decision reduces to subset
        # checking: PRRT_target is a subset of {PRRT_target}.
        s = _clean_state(authorized_thread_ids=["PRRT_target", "PRRT_other"])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=["PRRT_target"],
        )
        self.assertTrue(d.allowed)


# ---------------------------------------------------------------------------
# Jcmlx: reject non-list AED thread containers (tuple / set / str / dict)
# ---------------------------------------------------------------------------


class TestValidateThreadIdsContainerType(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6Jcmlx.

    ``_validate_thread_ids`` must accept only ``list`` as the
    container type. Tuples, sets, strings, dicts, generators,
    and ``None`` are denied with a policy decision that cites
    ``AED-RULE-008`` and never raises.
    """

    def test_validate_thread_ids_denies_tuple(self):
        from aed_policy.policy import _validate_thread_ids
        result = _validate_thread_ids(("PRRT_a",), "target")
        self.assertIsNotNone(result)
        self.assertFalse(result.allowed)
        self.assertIn("AED-RULE-008", result.matched_rule_ids)

    def test_validate_thread_ids_denies_empty_tuple(self):
        from aed_policy.policy import _validate_thread_ids
        result = _validate_thread_ids(tuple(), "target")
        self.assertIsNotNone(result)
        self.assertFalse(result.allowed)
        self.assertIn("AED-RULE-008", result.matched_rule_ids)

    def test_validate_thread_ids_denies_set(self):
        from aed_policy.policy import _validate_thread_ids
        result = _validate_thread_ids({"PRRT_a"}, "target")
        self.assertIsNotNone(result)
        self.assertFalse(result.allowed)
        self.assertIn("AED-RULE-008", result.matched_rule_ids)

    def test_validate_thread_ids_denies_frozenset(self):
        from aed_policy.policy import _validate_thread_ids
        result = _validate_thread_ids(frozenset({"PRRT_a"}), "target")
        self.assertIsNotNone(result)
        self.assertFalse(result.allowed)
        self.assertIn("AED-RULE-008", result.matched_rule_ids)

    def test_validate_thread_ids_denies_string(self):
        from aed_policy.policy import _validate_thread_ids
        # A bare string is iterable char-by-char, but the
        # contract requires a list, so a string is denied.
        result = _validate_thread_ids("PRRT_a", "target")
        self.assertIsNotNone(result)
        self.assertFalse(result.allowed)
        self.assertIn("AED-RULE-008", result.matched_rule_ids)

    def test_validate_thread_ids_denies_dict(self):
        from aed_policy.policy import _validate_thread_ids
        result = _validate_thread_ids({"id": "PRRT_a"}, "target")
        self.assertIsNotNone(result)
        self.assertFalse(result.allowed)
        self.assertIn("AED-RULE-008", result.matched_rule_ids)

    def test_validate_thread_ids_denies_none(self):
        from aed_policy.policy import _validate_thread_ids
        result = _validate_thread_ids(None, "target")
        self.assertIsNotNone(result)
        self.assertFalse(result.allowed)
        self.assertIn("AED-RULE-008", result.matched_rule_ids)

    def test_validate_thread_ids_denies_generator(self):
        from aed_policy.policy import _validate_thread_ids
        def _gen():
            yield "PRRT_a"
        result = _validate_thread_ids(_gen(), "target")
        self.assertIsNotNone(result)
        self.assertFalse(result.allowed)
        self.assertIn("AED-RULE-008", result.matched_rule_ids)


class TestThreadResolveTupleContainerDenied(unittest.TestCase):
    """Regression tests: GITHUB_THREAD_RESOLVE denies tuple containers."""

    def _assert_denied(self, d):
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-008", d.matched_rule_ids)

    def test_target_thread_ids_tuple_denied(self):
        s = _clean_state(authorized_thread_ids=["PRRT_a"])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=("PRRT_a",),
        )
        self._assert_denied(d)

    def test_authorized_thread_ids_tuple_denied(self):
        s = _clean_state(authorized_thread_ids=("PRRT_a",))
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=["PRRT_a"],
        )
        self._assert_denied(d)

    def test_both_target_and_authorized_tuples_denied(self):
        s = _clean_state(authorized_thread_ids=("PRRT_a",))
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=("PRRT_a",),
        )
        self._assert_denied(d)

    def test_target_set_denied(self):
        s = _clean_state(authorized_thread_ids=["PRRT_a"])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids={"PRRT_a"},
        )
        self._assert_denied(d)

    def test_authorized_set_denied(self):
        s = _clean_state(authorized_thread_ids={"PRRT_a"})
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=["PRRT_a"],
        )
        self._assert_denied(d)

    def test_target_string_denied(self):
        s = _clean_state(authorized_thread_ids=["PRRT_a"])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids="PRRT_a",
        )
        self._assert_denied(d)

    def test_tuple_path_no_TypeError(self):
        # A tuple of authorized thread IDs would have made the
        # pre-patch implementation allow thread resolution.
        # After the patch, the tuple container is denied with
        # AED-RULE-008 and never raises.
        s = _clean_state(authorized_thread_ids=("PRRT_a", "PRRT_b"))
        try:
            d = evaluate_action(
                AEDActionType.GITHUB_THREAD_RESOLVE,
                s,
                target_thread_ids=("PRRT_a",),
            )
        except TypeError as e:
            self.fail(f"evaluate_action raised TypeError: {e}")
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-008", d.matched_rule_ids)


class TestThreadResolveListAllowedAfterJcmlxFix(unittest.TestCase):
    """Lists of non-empty strings are still allowed after the Jcmlx fix."""

    def test_target_and_authorized_both_lists_allowed(self):
        s = _clean_state(authorized_thread_ids=["PRRT_a", "PRRT_b"])
        d = evaluate_action(
            AEDActionType.GITHUB_THREAD_RESOLVE,
            s,
            target_thread_ids=["PRRT_a"],
        )
        self.assertTrue(d.allowed)


# ---------------------------------------------------------------------------
# Jcv5i: CODEX_PING must deny duplicate same-head pings based on head
# evidence alone, not gated by ``codex_ping_comment_id`` truthiness.
# ---------------------------------------------------------------------------


class TestCodexPingDeniesOnHeadEvidenceAlone(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6Jcv5i.

    The pre-patch implementation gated duplicate-same-head
    denials on ``state.codex_ping_comment_id and
    state.codex_ping_head_sha and ...``, so a partial scan
    with a same-head SHA but a falsy comment id slipped
    through and allowed a second ping. The strict variant
    must deny on the head SHA alone, and must also deny when
    a prior head SHA is recorded but is not a full
    40-character hex value (malformed evidence must not
    silently satisfy "no prior ping").
    """

    def test_codex_ping_denied_when_ping_head_matches_expected_and_comment_id_none(
        self,
    ):
        d = evaluate_action(
            AEDActionType.CODEX_PING,
            _clean_state(
                codex_ping_comment_id=None,
                codex_ping_head_sha=ZERO_SHA,
            ),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_NO_DUPLICATE_CODEX_PING
        )
        self.assertIn("AED-RULE-010", d.matched_rule_ids)

    def test_codex_ping_denied_when_ping_head_matches_expected_and_comment_id_empty(
        self,
    ):
        d = evaluate_action(
            AEDActionType.CODEX_PING,
            _clean_state(
                codex_ping_comment_id="",
                codex_ping_head_sha=ZERO_SHA,
            ),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_NO_DUPLICATE_CODEX_PING
        )
        self.assertIn("AED-RULE-010", d.matched_rule_ids)

    def test_codex_ping_denied_when_ping_head_matches_current_and_expected(
        self,
    ):
        # current_head_sha == expected_head_sha == ZERO_SHA
        # in _clean_state, so a codex_ping_head_sha == ZERO_SHA
        # matches BOTH conditions.
        d = evaluate_action(
            AEDActionType.CODEX_PING,
            _clean_state(
                codex_ping_comment_id=None,
                codex_ping_head_sha=ZERO_SHA,
            ),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-010", d.matched_rule_ids)

    def test_codex_ping_denied_when_ping_head_is_malformed_abbreviated(
        self,
    ):
        # Abbreviated SHA (12 chars) is partial evidence and
        # must NOT be treated as "no prior ping". AED-RULE-010
        # forbids falling through to allow on malformed input.
        d = evaluate_action(
            AEDActionType.CODEX_PING,
            _clean_state(
                codex_ping_comment_id=None,
                codex_ping_head_sha=ZERO_SHA[:12],
            ),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_NO_DUPLICATE_CODEX_PING
        )
        self.assertIn("AED-RULE-010", d.matched_rule_ids)

    def test_codex_ping_allowed_only_when_no_prior_ping_head(self):
        # _clean_state has codex_ping_head_sha=None; this is
        # the only allow path for the duplicate check.
        d = evaluate_action(AEDActionType.CODEX_PING, _clean_state())
        self.assertTrue(d.allowed)
        self.assertIn("AED-RULE-010", d.matched_rule_ids)

    def test_codex_ping_allowed_when_ping_head_is_different_full_sha(self):
        d = evaluate_action(
            AEDActionType.CODEX_PING,
            _clean_state(
                codex_ping_comment_id="12345",
                codex_ping_head_sha="a" * 40,
            ),
        )
        self.assertTrue(d.allowed)
        self.assertIn("AED-RULE-010", d.matched_rule_ids)

    def test_codex_ping_duplicate_denial_cites_aed_rule_010(self):
        d = evaluate_action(
            AEDActionType.CODEX_PING,
            _clean_state(
                codex_ping_comment_id=None,
                codex_ping_head_sha=ZERO_SHA,
            ),
        )
        self.assertIn("AED-RULE-010", d.matched_rule_ids)


# ---------------------------------------------------------------------------
# Jcv5k: catch-all denial must handle raw strings, None, ints, dicts, and
# arbitrary objects without reading ``action.value`` and without throwing.
# ---------------------------------------------------------------------------


class TestEvaluateActionAcceptsUnknownInputs(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6Jcv5k.

    The pre-patch catch-all read ``action.value`` directly,
    which raises ``AttributeError`` for raw strings,
    ``None``, ``int``, ``dict``, and arbitrary objects. The
    strict variant uses ``_action_name`` so the fail-closed
    denial path never throws. AED-RULE-024 still fires for
    any input that is not a recognized ``AEDActionType`` enum
    member.
    """

    def test_evaluate_action_raw_string_unknown_returns_deny_no_raise(self):
        try:
            d = evaluate_action("NEW_TOOL_ACTION", _clean_state())
        except AttributeError as e:
            self.fail(f"evaluate_action raised AttributeError: {e}")
        self.assertIsNotNone(d)
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.DENY)
        self.assertIn("AED-RULE-024", d.matched_rule_ids)

    def test_evaluate_action_raw_string_known_returns_deny_no_raise(self):
        # A raw string that happens to match an enum VALUE
        # name should still be treated as not-an-enum and
        # fall through to the catch-all (AED-RULE-024). The
        # function compares against ``AEDActionType`` enum
        # members, not Python strings, so a raw string
        # never satisfies the enum checks.
        try:
            d = evaluate_action("GITHUB_MERGE", _clean_state())
        except AttributeError as e:
            self.fail(f"evaluate_action raised AttributeError: {e}")
        self.assertIsNotNone(d)
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.DENY)
        self.assertIn("AED-RULE-024", d.matched_rule_ids)

    def test_evaluate_action_none_returns_deny_no_raise(self):
        try:
            d = evaluate_action(None, _clean_state())
        except AttributeError as e:
            self.fail(f"evaluate_action raised AttributeError: {e}")
        self.assertIsNotNone(d)
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.DENY)
        self.assertIn("AED-RULE-024", d.matched_rule_ids)

    def test_evaluate_action_int_returns_deny_no_raise(self):
        try:
            d = evaluate_action(123, _clean_state())
        except AttributeError as e:
            self.fail(f"evaluate_action raised AttributeError: {e}")
        self.assertIsNotNone(d)
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.DENY)
        self.assertIn("AED-RULE-024", d.matched_rule_ids)

    def test_evaluate_action_dict_returns_deny_no_raise(self):
        try:
            d = evaluate_action({"action": "NEW"}, _clean_state())
        except AttributeError as e:
            self.fail(f"evaluate_action raised AttributeError: {e}")
        self.assertIsNotNone(d)
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.DENY)
        self.assertIn("AED-RULE-024", d.matched_rule_ids)

    def test_evaluate_action_arbitrary_object_returns_deny_no_raise(self):
        class _Weird:
            pass
        try:
            d = evaluate_action(_Weird(), _clean_state())
        except AttributeError as e:
            self.fail(f"evaluate_action raised AttributeError: {e}")
        self.assertIsNotNone(d)
        self.assertFalse(d.allowed)
        self.assertEqual(d.code, AEDDecisionCode.DENY)
        self.assertIn("AED-RULE-024", d.matched_rule_ids)

    def test_evaluate_action_int_has_useful_reason_text(self):
        d = evaluate_action(123, _clean_state())
        # The reason text should mention the action label so
        # operators can tell what the engine saw. The strict
        # variant uses _action_name -> type(action).__name__
        # for an int, which is "int".
        self.assertIn("int", d.reason)


# ---------------------------------------------------------------------------
# Jcv5l: workspace path canonicalization must reject relative paths so the
# policy decision never depends on the process's cwd.
# ---------------------------------------------------------------------------


class TestCanonicalizePathRejectsRelative(unittest.TestCase):
    """Regression tests for PRRT_kwDOSHFpYM6Jcv5l.

    The pre-patch ``_canonicalize_path`` called
    ``os.path.realpath`` on any non-empty string, so a
    relative path like ``"."`` or
    ``"./aed_policy_engine_skeleton_v1"`` would resolve
    against the policy process's cwd. If the process was
    running from inside
    ``/tmp/aed_runs/worktrees/<task>``, the resolved path
    would pass the workspace check; from any other cwd it
    would fail. The strict variant rejects non-absolute paths
    at the boundary so the decision is a pure function of
    the inputs.
    """

    def test_dot_relative_denied(self):
        d = evaluate_action(
            AEDActionType.FILE_WRITE,
            _clean_state(isolated_workspace_path="."),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-003", d.matched_rule_ids)

    def test_dot_slash_task_relative_denied(self):
        d = evaluate_action(
            AEDActionType.FILE_WRITE,
            _clean_state(
                isolated_workspace_path="./aed_policy_engine_skeleton_v1"
            ),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-003", d.matched_rule_ids)

    def test_relative_worktrees_subdir_denied(self):
        d = evaluate_action(
            AEDActionType.FILE_WRITE,
            _clean_state(
                isolated_workspace_path="tmp/aed_runs/worktrees/x"
            ),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-003", d.matched_rule_ids)

    def test_relative_path_denied_even_if_cwd_would_resolve_under_root(self):
        # Simulate: a process running the policy engine from
        # /tmp/aed_runs/worktrees/<task> with isolated_workspace_path="."
        # would have pre-patch resolved "." to the cwd and
        # allowed the action. The strict variant must still
        # deny because the policy decision must not depend
        # on the process's cwd.
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir("/tmp/aed_runs/worktrees/aed_policy_engine_skeleton_v1")
            d = evaluate_action(
                AEDActionType.FILE_WRITE,
                _clean_state(isolated_workspace_path="."),
            )
        finally:
            os.chdir(original_cwd)
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-003", d.matched_rule_ids)

    def test_file_write_denied_when_isolated_workspace_is_dot(self):
        d = evaluate_action(
            AEDActionType.FILE_WRITE,
            _clean_state(isolated_workspace_path="."),
        )
        self.assertFalse(d.allowed)
        self.assertEqual(
            d.code, AEDDecisionCode.REQUIRE_ISOLATED_WORKSPACE
        )
        self.assertIn("AED-RULE-003", d.matched_rule_ids)

    def test_terminal_mutating_denied_when_isolated_workspace_is_dot(self):
        d = evaluate_action(
            AEDActionType.TERMINAL_MUTATING,
            _clean_state(isolated_workspace_path="."),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-003", d.matched_rule_ids)

    def test_git_mutating_denied_when_isolated_workspace_is_dot(self):
        d = evaluate_action(
            AEDActionType.GIT_MUTATING,
            _clean_state(isolated_workspace_path="."),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-003", d.matched_rule_ids)

    def test_absolute_named_workspace_still_allowed(self):
        d = evaluate_action(
            AEDActionType.FILE_WRITE,
            _clean_state(
                isolated_workspace_path="/tmp/aed_runs/worktrees/some_task"
            ),
        )
        self.assertTrue(d.allowed)
        self.assertIn("AED-RULE-003", d.matched_rule_ids)

    def test_absolute_worktrees_root_still_denied(self):
        # /tmp/aed_runs/worktrees (without a task subdir) is
        # the worktrees root itself; the engine must still
        # reject it because AED-RULE-003 requires a per-task
        # isolated workspace strictly under the root.
        d = evaluate_action(
            AEDActionType.FILE_WRITE,
            _clean_state(
                isolated_workspace_path="/tmp/aed_runs/worktrees"
            ),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-003", d.matched_rule_ids)

    def test_absolute_dot_segment_escape_still_denied(self):
        # Absolute but escapes via dot segments; the engine
        # must still deny after realpath resolution. The
        # path is absolute so the new check does not block
        # it; instead _is_under must reject it because the
        # canonicalized path is /home/max/... which is not
        # under /tmp/aed_runs/worktrees.
        d = evaluate_action(
            AEDActionType.FILE_WRITE,
            _clean_state(
                isolated_workspace_path=(
                    "/tmp/aed_runs/worktrees/../../home/max/x"
                )
            ),
        )
        self.assertFalse(d.allowed)
        self.assertIn("AED-RULE-003", d.matched_rule_ids)


if __name__ == "__main__":
    unittest.main()