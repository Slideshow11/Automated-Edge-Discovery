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
AUTH_PHRASE = (
    "I authorize guarded squash merge of PR #999 at exact head "
    f"{ZERO_SHA}."
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
        codex_ping_comment_id=None,
        codex_ping_head_sha=None,
        audit_append_available=True,
        audit_append_only=True,
        explicit_authorization_phrase=AUTH_PHRASE,
        authorized_thread_ids=[],
        protected_pr_numbers=set(),
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
        self.assertEqual(re_loaded["protected_pr_numbers"], [])


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
        # Backward compatibility: a Python True is treated as mergeable.
        d = evaluate_action(
            AEDActionType.GITHUB_MERGE, _clean_state(mergeable=True)
        )
        self.assertTrue(d.allowed)


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
        self.assertTrue(_is_mergeable(True))

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
        self.assertTrue(_is_mergeable("mergeable"))

    def test_is_mergeable_string_conflicting(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable("CONFLICTING"))

    def test_is_mergeable_string_unknown(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable("UNKNOWN"))

    def test_is_mergeable_unsupported_type(self):
        from aed_policy.policy import _is_mergeable
        self.assertFalse(_is_mergeable(42))


if __name__ == "__main__":
    unittest.main()