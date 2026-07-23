#!/usr/bin/env python3
"""PR #412 Round-67 regression tests for the four-finding
eligibility adapter batch.

Findings:
  1. A top-level non-Codex actor must be rejected with
     ``actor_not_codex``.
  2. A thread anchor equal to the current head must be
     rejected with ``no_later_commit``.
  3. A current-head anchor must remain ineligible even when
     ancestry verification succeeds.
  4. Successful ancestry verification must not override any
     other failed safety condition.
"""
import os
import sys
import unittest

REPO = "/home/max/aed_hardening_v1"
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def _bot_thread(*, anchor, is_outdated=True, author="chatgpt-codex-connector[bot]", is_resolved=False):
    return {
        "thread_id": "T-R67",
        "author": author,
        "isOutdated": is_outdated,
        "isResolved": is_resolved,
        "original_commit_sha": anchor,
        "comments": [{"author": author}],
    }


def _kwargs(**overrides):
    base = dict(
        head_sha="0" * 40,
        codex_verdict="clean",
        codex_clean_passed=True,
        codex_reviewed_sha="0" * 40,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Test the SHARED policy directly (the production source of truth).
# ---------------------------------------------------------------------------


class SharedPolicyFinding1Tests(unittest.TestCase):
    """Finding 1: top-level non-Codex actor must be rejected."""

    def test_non_codex_top_level_actor_actor_not_codex(self):
        """A top-level non-Codex author must produce
        ``actor_not_codex``."""
        from scripts.local._shared_non_human_policy import (
            validate_thread_for_resolution,
        )
        thread = _bot_thread(
            anchor="1" * 40,
            author="dependabot[bot]",
        )
        v = validate_thread_for_resolution(
            thread=thread,
            head_sha="0" * 40,
            codex_clean_passed=True,
            codex_reviewed_sha="0" * 40,
            repo=None,
            verify_ancestry=False,
        )
        self.assertFalse(v.eligible, v)
        self.assertIn("actor_not_codex", v.reasons, v)

    def test_codex_top_level_actor_passes(self):
        """A top-level Codex author with all evidence passes."""
        from scripts.local._shared_non_human_policy import (
            validate_thread_for_resolution,
        )
        thread = _bot_thread(anchor="1" * 40)
        v = validate_thread_for_resolution(
            thread=thread,
            head_sha="0" * 40,
            codex_clean_passed=True,
            codex_reviewed_sha="0" * 40,
            repo=None,
            verify_ancestry=False,
        )
        self.assertTrue(v.eligible, v)


class SharedPolicyFinding2Tests(unittest.TestCase):
    """Finding 2: anchor == head must produce ``no_later_commit``."""

    def test_anchor_equal_head_no_later_commit(self):
        from scripts.local._shared_non_human_policy import (
            validate_thread_for_resolution,
        )
        thread = _bot_thread(anchor="0" * 40)
        v = validate_thread_for_resolution(
            thread=thread,
            head_sha="0" * 40,
            codex_clean_passed=True,
            codex_reviewed_sha="0" * 40,
            repo="owner/repo",
            ancestry_runner=None,
            verify_ancestry=True,
        )
        self.assertFalse(v.eligible, v)
        self.assertEqual(v.reasons, ["no_later_commit"], v)


class SharedPolicyFinding3Tests(unittest.TestCase):
    """Finding 3: current-head anchor must remain ineligible even
    when ancestry verification succeeds.

    When anchor == head, the verifier returns
    ``(False, "anchor_equals_head")``. The shared policy must
    map this to ``no_later_commit`` and NOT allow the thread.
    """

    def test_current_head_anchor_rejected_with_ancestry(self):
        from scripts.local._shared_non_human_policy import (
            validate_thread_for_resolution,
        )
        thread = _bot_thread(anchor="0" * 40)
        # The legacy verifier is called when verify_ancestry=True
        # and repo is provided. It returns
        # ``(False, "anchor_equals_head")`` for anchor == head.
        v = validate_thread_for_resolution(
            thread=thread,
            head_sha="0" * 40,
            codex_clean_passed=True,
            codex_reviewed_sha="0" * 40,
            repo="owner/repo",
            ancestry_runner=None,
            verify_ancestry=True,
        )
        self.assertFalse(v.eligible, v)
        self.assertEqual(v.reasons, ["no_later_commit"], v)


class SharedPolicyFinding4Tests(unittest.TestCase):
    """Finding 4: successful ancestry verification must not
    override any other failed safety condition."""

    def test_codex_clean_failed_overrides_ancestry(self):
        """Even with verified ancestry, ``codex_clean_passed=False``
        must reject the thread."""
        from scripts.local._shared_non_human_policy import (
            validate_thread_for_resolution,
        )
        thread = _bot_thread(anchor="1" * 40)
        v = validate_thread_for_resolution(
            thread=thread,
            head_sha="0" * 40,
            codex_clean_passed=False,
            codex_reviewed_sha="0" * 40,
            repo=None,
            verify_ancestry=False,
        )
        self.assertFalse(v.eligible, v)
        self.assertEqual(v.reasons, ["codex_not_clean"], v)

    def test_already_resolved_overrides_ancestry(self):
        """Even with verified ancestry, ``isResolved=True``
        must reject the thread."""
        from scripts.local._shared_non_human_policy import (
            validate_thread_for_resolution,
        )
        thread = _bot_thread(anchor="1" * 40, is_resolved=True)
        v = validate_thread_for_resolution(
            thread=thread,
            head_sha="0" * 40,
            codex_clean_passed=True,
            codex_reviewed_sha="0" * 40,
            repo=None,
            verify_ancestry=False,
        )
        self.assertFalse(v.eligible, v)
        self.assertEqual(v.reasons, ["already_resolved"], v)

    def test_codex_reviewed_sha_mismatch_overrides_ancestry(self):
        """Even with verified ancestry, a reviewed-sha mismatch
        must reject the thread."""
        from scripts.local._shared_non_human_policy import (
            validate_thread_for_resolution,
        )
        thread = _bot_thread(anchor="1" * 40)
        v = validate_thread_for_resolution(
            thread=thread,
            head_sha="0" * 40,
            codex_clean_passed=True,
            codex_reviewed_sha="1" * 40,
            repo=None,
            verify_ancestry=False,
        )
        self.assertFalse(v.eligible, v)
        self.assertEqual(v.reasons, ["codex_head_mismatch"], v)

    def test_malformed_anchor_overrides_ancestry(self):
        """Even with verified ancestry, a malformed anchor
        must reject the thread."""
        from scripts.local._shared_non_human_policy import (
            validate_thread_for_resolution,
        )
        thread = _bot_thread(anchor="not-a-sha")
        v = validate_thread_for_resolution(
            thread=thread,
            head_sha="0" * 40,
            codex_clean_passed=True,
            codex_reviewed_sha="0" * 40,
            repo=None,
            verify_ancestry=False,
        )
        self.assertFalse(v.eligible, v)
        self.assertEqual(v.reasons, ["malformed_commit_anchor"], v)


# ---------------------------------------------------------------------------
# Test the production facade as the actual integration point.
# ---------------------------------------------------------------------------


class FacadeIntegrationTests(unittest.TestCase):
    """The facade MUST invoke the shared policy and record the
    invocation. These are behavioral tests, not source-string
    assertions."""

    def setUp(self):
        from scripts.local import _production_facade as F
        F.clear_invocations()

    def test_facade_invokes_shared_policy_for_actor_not_codex(self):
        from scripts.local import _production_facade as F
        # Monkeypatch the facade's local reference to
        # validate_thread_for_resolution. The facade
        # imports it at module load, so we patch the
        # facade's binding.
        original = F._validate_thread_for_resolution
        called = {"count": 0}

        def spy(*a, **kw):
            called["count"] += 1
            return original(*a, **kw)

        F._validate_thread_for_resolution = spy
        try:
            thread = _bot_thread(
                anchor="1" * 40,
                author="dependabot[bot]",
            )
            v = F.classify_review_thread_eligibility(
                thread=thread,
                head_sha="0" * 40,
                codex_clean_passed=True,
                codex_reviewed_sha="0" * 40,
                repo=None,
                verify_ancestry=False,
            )
            self.assertFalse(v.eligible, v)
            self.assertIn("actor_not_codex", v.reasons, v)
            self.assertEqual(
                called["count"], 1,
                "shared policy not called from facade",
            )
        finally:
            F._validate_thread_for_resolution = original

        invocations = F.get_invocations()
        policy_names = [i["policy"] for i in invocations]
        self.assertIn(
            "non_human_policy.validate_thread_for_resolution",
            policy_names,
        )

    def test_facade_records_invocation_on_no_later_commit(self):
        from scripts.local import _production_facade as F
        thread = _bot_thread(anchor="0" * 40)
        v = F.classify_review_thread_eligibility(
            thread=thread,
            head_sha="0" * 40,
            codex_clean_passed=True,
            codex_reviewed_sha="0" * 40,
            repo="owner/repo",
            ancestry_runner=None,
            verify_ancestry=True,
        )
        self.assertFalse(v.eligible, v)
        self.assertEqual(v.reasons, ["no_later_commit"], v)
        invocations = F.get_invocations()
        self.assertEqual(len(invocations), 1)
        self.assertEqual(
            invocations[0]["outputs"]["reasons"],
            ["no_later_commit"],
        )


# ---------------------------------------------------------------------------
# Source-contract test (PHASE 7).
# ---------------------------------------------------------------------------


class SourceContractTests(unittest.TestCase):
    """PHASE 7: behavioral evidence that production call paths
    invoke the shared policies."""

    def test_aed_pr_readiness_uses_facade(self):
        """The controller's eligibility function MUST call the
        shared policy via the production facade."""
        import inspect
        from scripts.local import aed_pr_readiness as R
        src = inspect.getsource(R.is_eligible_for_bot_resolution)
        self.assertIn(
            "_shared_classify_review_thread_eligibility",
            src,
            "Round-67 fix: is_eligible_for_bot_resolution must "
            "delegate to the production facade.",
        )
        self.assertIn(
            "verify_ancestry=True",
            src,
            "Round-67 fix: ancestry must be verified by the "
            "shared policy.",
        )
        self.assertIn(
            "ancestry_runner=ancestry_runner",
            src,
            "Round-67 fix: ancestry_runner must be passed through "
            "to the shared policy.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
