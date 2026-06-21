"""
tests/test_check_pr_review_comments.py

Unit tests for check_pr_review_comments.py.
Uses mock subprocess to avoid real GitHub calls.
"""

import contextlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

# Ensure the module is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "local"))
import check_pr_review_comments as crc


class FakeResult:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def gh_reply(items: list) -> FakeResult:
    return FakeResult(stdout=json.dumps(items))


def gh_fail(msg: str) -> FakeResult:
    return FakeResult(stdout="", stderr=msg, returncode=1)


def gh_pr_view_reply(oid: str = "aabbccdd") -> FakeResult:
    return FakeResult(stdout=json.dumps({
        "headRefOid": oid,
        "state": "OPEN",
        "url": "https://github.com/OWNER/REPO/pull/1",
    }))


class TestClassify(unittest.TestCase):
    def test_no_findings_when_no_needles(self):
        item = {"user": {"login": "alice"}, "body": "looks good to me"}
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(got, [])

    def test_p1_inline_comment(self):
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange) Emit a real newline marker**",
            "path": "scripts/local/run_temp_worktree_execution.py",
            "line": 1821,
            "commit_id": "abc123abc123",
            "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P1")
        self.assertEqual(got[0]["user"], "chatgpt-codex-connector[bot]")

    def test_p2_issue_comment(self):
        item = {
            "user": {"login": "codex-reviewer"},
            "body": "### P2\nValidate literal base SHA as object",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P2")

    def test_unspecified_blocking(self):
        item = {
            "user": {"login": "safety-bot"},
            "body": "Codex finding: CRITICAL — shell=True found in subprocess call — must fix",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "UNSPECIFIED_BLOCKING")

    def test_unspecified_info(self):
        item = {
            "user": {"login": "codex"},
            "body": "Nit: consider renaming variable",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "UNSPECIFIED_INFO")

    def test_ignore_user(self):
        item = {
            "user": {"login": "alice"},
            "body": "P1: this is a finding",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", {"alice"})
        self.assertEqual(got, [])

    def test_high_maps_to_p1(self):
        item = {
            "user": {"login": "reviewer"},
            "body": "High severity: path traversal risk",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P1")

    def test_medium_maps_to_p2(self):
        item = {
            "user": {"login": "reviewer"},
            "body": "Medium: test coverage gap",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P2")


class TestCoordinationCommentSkip(unittest.TestCase):
    """Regression for review-comment-gate false positives:
    human PR-author coordination comments that mention Codex
    (e.g. 'Re-requesting Codex review...') must NOT be
    classified as blocking findings, even though they contain
    Codex needles. Actual Codex review findings from the Codex
    bot must still be detected.
    """

    def test_is_coordination_comment_detects_re_requesting(self):
        self.assertTrue(crc.is_coordination_comment(
            "Re-requesting Codex review on 3982ee6 (Fix AF)."
        ))

    def test_is_coordination_comment_detects_re_request(self):
        self.assertTrue(crc.is_coordination_comment(
            "Re-request on commit a9555afaea44d13c02c67ab22ba1bc34e2e81009."
        ))

    def test_is_coordination_comment_detects_gentle_nudge(self):
        self.assertTrue(crc.is_coordination_comment(
            "Gentle nudge to @chatgpt-codex-connector — Fix AF pushed."
        ))

    def test_is_coordination_comment_detects_bumping(self):
        self.assertTrue(crc.is_coordination_comment(
            "Bumping this thread — Fix AG is now on 266a92e."
        ))

    def test_is_coordination_comment_detects_nudge_to_at(self):
        self.assertTrue(crc.is_coordination_comment(
            "Nudge to @codex — please re-review."
        ))

    def test_is_coordination_comment_case_insensitive(self):
        self.assertTrue(crc.is_coordination_comment(
            "RE-REQUESTING codex review."
        ))

    def test_is_coordination_comment_rejects_real_finding(self):
        # A real Codex review finding must NOT be classified as
        # a coordination comment.
        self.assertFalse(crc.is_coordination_comment(
            "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange) "
            "Emit a real newline marker**"
        ))

    def test_is_coordination_comment_rejects_plain_text(self):
        self.assertFalse(crc.is_coordination_comment(
            "Looks good to me."
        ))

    def test_is_coordination_comment_rejects_empty(self):
        self.assertFalse(crc.is_coordination_comment(""))

    def test_is_coordination_comment_handles_none(self):
        # Defensive: None body must not crash.
        self.assertFalse(crc.is_coordination_comment(None))  # type: ignore[arg-type]

    def test_human_re_requesting_issue_comment_not_classified(self):
        """The exact blocker from PR #405 run on 266a92e: a
        human issue comment by the PR author re-requesting
        Codex review must not produce a finding."""
        item = {
            "user": {"login": "Slideshow11"},
            "body": (
                "Re-requesting Codex review on 3982ee6 (Fix AF). "
                "The active P1 current-head finding from the previous "
                "review has been addressed.\n\n"
                "- 3443990130 (Fix AF): validate_checkpoint() now "
                "also validates that a present phase is a string."
            ),
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(
            got, [],
            "Human 'Re-requesting Codex review...' issue comment "
            "must not be classified as a finding"
        )

    def test_human_gentle_nudge_issue_comment_not_classified(self):
        item = {
            "user": {"login": "Slideshow11"},
            "body": (
                "Gentle nudge to @chatgpt-codex-connector — "
                "Fix AF (Codex 3443990130) pushed in 3982ee6, "
                "CI is 5/5 green, local tests pass."
            ),
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(
            got, [],
            "Human 'Gentle nudge to @codex' issue comment must "
            "not be classified as a finding"
        )

    def test_human_at_codex_mention_not_classified(self):
        item = {
            "user": {"login": "Slideshow11"},
            "body": "@codex review\n\nFix L (Codex 3422779962) addresses...",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(
            got, [],
            "Human '@codex review' issue comment must not be "
            "classified as a finding"
        )

    def test_human_bumping_issue_comment_not_classified(self):
        item = {
            "user": {"login": "Slideshow11"},
            "body": "Bumping this — Fix AG is now on 266a92e, CI green.",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(
            got, [],
            "Human 'Bumping' issue comment must not be classified "
            "as a finding"
        )

    def test_human_coordination_inline_review_not_classified(self):
        """Coordination comments posted as inline review comments
        must also be skipped, not just issue comments."""
        item = {
            "user": {"login": "Slideshow11"},
            "body": "Re-requesting Codex review on this thread.",
            "path": "aed_lifecycle/no_stall.py",
            "line": 1118,
        }
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(
            got, [],
            "Human coordination inline review comment must not "
            "be classified as a finding"
        )

    def test_actual_codex_review_finding_still_detected(self):
        """An actual Codex review finding must still be detected
        after the coordination-comment fix."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": (
                "**<sub><sub>![P1 Badge]"
                "(https://img.shields.io/badge/P1-orange) "
                "Emit a real newline marker**"
            ),
            "path": "scripts/local/run_temp_worktree_execution.py",
            "line": 1821,
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1, "Actual Codex P1 finding must be detected")
        self.assertEqual(got[0]["severity"], "P1")
        self.assertEqual(got[0]["user"], "chatgpt-codex-connector[bot]")

    def test_p1_at_codex_not_dropped_by_coordination_skip(self):
        """Regression for Codex finding AJ: a comment with an
        explicit P1 severity token must NOT be dropped by the
        coordination-skip, even if it also contains a broad
        coordination pattern like ``@codex`` or ``bumping``."""
        item = {
            "user": {"login": "reviewer"},
            "body": "P1: @codex flagged a security issue; must fix",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(
            len(got), 1,
            "P1 comment containing @codex must still be detected"
        )
        self.assertEqual(got[0]["severity"], "P1")

    def test_p0_at_codex_not_dropped(self):
        item = {
            "user": {"login": "reviewer"},
            "body": "P0: @codex — this is a critical issue",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P0")

    def test_p2_bumping_not_dropped(self):
        item = {
            "user": {"login": "reviewer"},
            "body": "P2: this bumping retry logic can fail",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P2")

    def test_p1_re_requesting_not_dropped(self):
        """Even though 'Re-requesting' is a coordination pattern,
        a comment with explicit P1 severity must still be
        detected."""
        item = {
            "user": {"login": "reviewer"},
            "body": "P1: Re-requesting this because the fix is wrong",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P1")

    def test_p3_colon_coordination_still_skipped(self):
        """P3 is not a blocking severity. A body starting with
        ``P3:`` followed by a coordination pattern is NOT
        skipped by Guard 1 (P3 not covered) and the
        coordination pattern is not at the start, so it's
        classified as a finding. To still be coordination,
        the body must start with a coordination pattern."""
        item = {
            "user": {"login": "Slideshow11"},
            "body": "Bumping this thread — P3: fix is minor",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        # Body starts with "Bumping" (coordination pattern),
        # so it IS skipped as coordination.
        self.assertEqual(got, [])

    def test_is_coordination_comment_p1_colon_returns_false(self):
        """is_coordination_comment must return False for bodies
        with explicit P0/P1/P2 severity declarations (P0:/P1:/P2:
        followed by colon), regardless of other patterns."""
        self.assertFalse(crc.is_coordination_comment(
            "P1: @codex flagged a security issue; must fix"
        ))
        self.assertFalse(crc.is_coordination_comment(
            "P0: bumping this because it's critical"
        ))
        self.assertFalse(crc.is_coordination_comment(
            "P2: Re-requesting review on this"
        ))

    def test_is_coordination_comment_p1_reference_still_true(self):
        """A bare P0/P1/P2 reference in prose (not followed by
        colon) must NOT be treated as a severity declaration.
        Coordination comments commonly reference prior findings
        like 'The active P1 current-head finding'."""
        self.assertTrue(crc.is_coordination_comment(
            "Re-requesting Codex review. The active P1 current-head "
            "finding from the previous review has been addressed."
        ))

    def test_is_coordination_comment_p3_still_true(self):
        """P3 is not a blocking severity. A body starting with
        a coordination pattern is still treated as
        coordination even if it also contains P3."""
        self.assertTrue(crc.is_coordination_comment(
            "Bumping this thread — P3: fix is minor"
        ))

    def test_badge_finding_with_bumping_not_coordination(self):
        """Regression for Codex finding AK: a badge-formatted
        finding containing a coordination word like 'bumping'
        must NOT be treated as a coordination comment."""
        body = (
            "**<sub><sub>![P1 Badge]"
            "(https://img.shields.io/badge/P1-orange) "
            "Bumping the retry counter can skip failures**"
        )
        self.assertFalse(crc.is_coordination_comment(body))

    def test_badge_finding_with_at_codex_not_coordination(self):
        body = (
            "**<sub><sub>![P2 Badge]"
            "(https://img.shields.io/badge/P2-yellow) "
            "@codex review missed a regression**"
        )
        self.assertFalse(crc.is_coordination_comment(body))

    def test_badge_finding_with_re_requesting_not_coordination(self):
        body = (
            "**<sub><sub>![P0 Badge]"
            "(https://img.shields.io/badge/P0-red) "
            "Re-requesting review on this critical path**"
        )
        self.assertFalse(crc.is_coordination_comment(body))

    def test_badge_finding_p2_bumping_detected_by_classify(self):
        """The exact Codex finding AK scenario: a P2 badge finding
        containing 'bumping' must still be detected by classify_item."""
        item = {
            "user": {"login": "reviewer"},
            "body": (
                "**<sub><sub>![P2 Badge]"
                "(https://img.shields.io/badge/P2-yellow) "
                "Bumping the retry counter can skip failures**"
            ),
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1, "Badge finding with 'bumping' must be detected")
        self.assertEqual(got[0]["severity"], "P2")

    def test_p3_badge_still_skipped(self):
        """P3 is not blocking. A P3 badge starting with
        coordination pattern is still skipped (non-blocking
        info). The coordination check fires because the body
        starts with a coordination pattern after the badge
        markup is stripped... actually the body starts with
        the badge markup. With the new start-of-body check,
        this is NOT skipped. The P3 badge is classified as
        a non-blocking finding (UNSPECIFIED_INFO)."""
        body = (
            "**<sub><sub>![P3 Badge]"
            "(https://img.shields.io/badge/P3-lightgrey) "
            "Bumping this minor nit**"
        )
        # P3 badge: guard 2 does NOT apply (only P0/P1/P2).
        # Coordination check: body starts with "**<sub>",
        # not a coordination pattern. So this is NOT a
        # coordination comment — it's classified as a
        # non-blocking finding.
        self.assertFalse(crc.is_coordination_comment(body))

    def test_bracketed_p1_finding_not_coordination(self):
        """Regression for Codex finding AM: a bracketed priority
        finding like ``[P1] ...`` must NOT be treated as a
        coordination comment, even if it contains a coordination
        word like 'bumping'."""
        body = "[P1] Bumping the retry counter can skip failures"
        self.assertFalse(crc.is_coordination_comment(body))

    def test_bracketed_p2_finding_not_coordination(self):
        body = "[P2] @codex review missed a regression"
        self.assertFalse(crc.is_coordination_comment(body))

    def test_bracketed_p0_finding_not_coordination(self):
        body = "[P0] Re-requesting review on this critical path"
        self.assertFalse(crc.is_coordination_comment(body))

    def test_bracketed_p1_bumping_detected_by_classify(self):
        """The exact Codex finding AM scenario: a bracketed P1
        finding containing 'bumping' must still be detected by
        classify_item."""
        item = {
            "user": {"login": "reviewer"},
            "body": "[P1] Bumping the retry counter can skip failures",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(
            len(got), 1,
            "Bracketed P1 finding with 'bumping' must be detected"
        )
        self.assertEqual(got[0]["severity"], "P1")

    def test_high_severity_bumping_not_coordination(self) -> None:
        """Regression for Codex finding AO: a comment using the
        ``High severity: ...`` text-alias form (which maps to
        P1 via :func:`extract_severity`) must NOT be dropped as
        a coordination comment, even if it contains a
        coordination word like 'bumping'."""
        body = "High severity: Bumping retry counter can skip failures"
        self.assertFalse(crc.is_coordination_comment(body))

    def test_codex_finding_bumping_not_coordination(self) -> None:
        """The exact Codex finding AO scenario: a comment using
        the ``Codex finding: ... — must fix`` form (which maps
        to UNSPECIFIED_BLOCKING via :func:`is_blocking`) must
        NOT be dropped as a coordination comment, even if it
        contains a coordination word like 'bumping'."""
        body = (
            "Codex finding: Bumping retry counter can skip "
            "failures — must fix"
        )
        self.assertFalse(crc.is_coordination_comment(body))

    def test_medium_severity_bumping_not_coordination(self):
        """``Medium: ...`` maps to P2. Must not be dropped."""
        body = "Medium: Bumping the retry counter is a regression"
        self.assertFalse(crc.is_coordination_comment(body))

    def test_high_severity_at_codex_not_coordination(self):
        body = "High severity: @codex missed a regression"
        self.assertFalse(crc.is_coordination_comment(body))

    def test_p1_finding_with_must_fix_not_coordination(self):
        """A blocking-word indicator (``must fix``) at the
        START of the body protects it from being dropped.
        (A blocking-word mention mid-body in a coordination
        message does NOT protect, because the body starts
        with a coordination pattern.)"""
        from aed_lifecycle.no_stall import is_valid_next_action  # noqa: F401
        # This body starts with a blocking word — not a
        # coordination pattern.
        body = "Must fix: Bumping retry counter can skip failures"
        self.assertFalse(crc.is_coordination_comment(body))

    def test_aq_blocking_word_in_body_blocks_coordination(self):
        """Regression for Codex finding AQ: a body that starts
        with a coordination pattern (``Bumping``) AND contains
        a blocking-word indicator (``can fail``) anywhere must
        NOT be dropped as a coordination comment. Real
        coordination messages like ``Bumping this thread —
        Fix AG is now on 266a92e.`` never contain blocking
        vocabulary, so this guard doesn't affect them.
        """
        body = (
            "Bumping the retry counter can fail when Codex "
            "reruns after a stale head"
        )
        self.assertFalse(crc.is_coordination_comment(body))

    def test_aq_re_requesting_with_must_fix_not_coordination(self):
        """Regression for Codex finding AQ: a body that starts
        with ``Re-requesting`` AND contains ``must fix``
        (BLOCKING_WORDS) anywhere must NOT be dropped as a
        coordination comment.
        """
        body = (
            "Re-requesting this because the must fix test is "
            "still failing"
        )
        self.assertFalse(crc.is_coordination_comment(body))

    def test_aq_gentle_nudge_with_stale_not_coordination(self):
        """Regression for Codex finding AQ: a body that starts
        with ``Gentle nudge`` AND contains ``stale`` (a
        BLOCKING_WORDS indicator) must NOT be dropped as a
        coordination comment.
        """
        body = (
            "Gentle nudge — the stale head ref is still wrong, "
            "must fix"
        )
        self.assertFalse(crc.is_coordination_comment(body))

    def test_aq_bumping_with_path_traversal_not_coordination(self):
        """Regression for Codex finding AQ: ``Bumping`` at start
        with ``path traversal`` (BLOCKING_WORDS) anywhere in
        the body must NOT be dropped as coordination.
        """
        body = (
            "Bumping this thread — the new helper introduces "
            "a path traversal risk in the input parser"
        )
        self.assertFalse(crc.is_coordination_comment(body))

    def test_aq_classify_aq_finding_detected(self):
        """End-to-end: the exact Codex finding AQ scenario — a
        body starting with ``Bumping`` and containing
        ``can fail`` and ``stale`` — must be detected by
        ``classify_item`` rather than silently dropped.
        """
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": (
                "Bumping the retry counter can fail when Codex "
                "reruns after a stale head"
            ),
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertGreaterEqual(
            len(got), 1,
            "Codex AQ finding with blocking vocabulary must "
            "NOT be dropped by coordination skip",
        )
        # The finding should be classified as blocking
        # because of the blocking-word indicators.
        self.assertIn(got[0]["severity"], ("UNSPECIFIED_BLOCKING", "P2"))

    def test_classify_high_severity_bumping_detected(self):
        """End-to-end: ``High severity: Bumping ...`` must be
        detected by classify_item as P1."""
        item = {
            "user": {"login": "reviewer"},
            "body": "High severity: Bumping retry counter can skip failures",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P1")

    def test_classify_codex_finding_bumping_detected(self):
        """End-to-end: ``Codex finding: Bumping ... — must fix``
        must be detected by classify_item as
        UNSPECIFIED_BLOCKING."""
        item = {
            "user": {"login": "reviewer"},
            "body": (
                "Codex finding: Bumping retry counter can skip "
                "failures — must fix"
            ),
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertGreaterEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "UNSPECIFIED_BLOCKING")

    def test_actual_codex_p2_finding_still_detected(self):
        """An actual Codex P2 review finding must still be detected."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": (
                "**<sub><sub>![P2 Badge]"
                "(https://img.shields.io/badge/P2-yellow) "
                "Parse spaced checkpoint_path assignments**"
            ),
            "path": "aed_lifecycle/no_stall.py",
            "line": 1118,
        }
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1, "Actual Codex P2 finding must be detected")
        self.assertEqual(got[0]["severity"], "P2")

    def test_existing_p2_issue_comment_still_classified(self):
        """The pre-existing test_p2_issue_comment scenario (a
        non-Codex user posting a P2 finding) must still work."""
        item = {
            "user": {"login": "codex-reviewer"},
            "body": "### P2\nValidate literal base SHA as object",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1, "Non-coordination P2 finding must still be detected")
        self.assertEqual(got[0]["severity"], "P2")

    def test_existing_unspecified_blocking_still_classified(self):
        """The pre-existing test_unspecified_blocking scenario must
        still work."""
        item = {
            "user": {"login": "safety-bot"},
            "body": "Codex finding: CRITICAL — shell=True found in subprocess call — must fix",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1, "Non-coordination blocking finding must still be detected")
        self.assertEqual(got[0]["severity"], "UNSPECIFIED_BLOCKING")

    def test_ignore_user_still_works(self):
        """The pre-existing ignore-users behavior must still work."""
        item = {
            "user": {"login": "alice"},
            "body": "P1: this is a finding",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", {"alice"})
        self.assertEqual(got, [], "Ignored user must still be skipped")

    def test_no_findings_when_no_needles_still_works(self):
        """The pre-existing no-needles test must still work."""
        item = {"user": {"login": "alice"}, "body": "looks good to me"}
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(got, [])

    def test_high_maps_to_p1_still_works(self):
        item = {
            "user": {"login": "reviewer"},
            "body": "High severity: path traversal risk",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P1")

    def test_medium_maps_to_p2_still_works(self):
        item = {
            "user": {"login": "reviewer"},
            "body": "Medium: test coverage gap",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P2")

    def test_text_alias_severity_in_leading_rescues_coordination_skip(self):
        """Regression for the fresh Codex P1 finding on head
        74e6b8e9e3fb (2026-06-21T02:15:04Z, dbID 3447794638):
        a body that starts with a coordination verb but
        declares severity using a text alias in the leading
        100 characters must NOT be classified as
        coordination. Previously, Guard 4 only protected
        colon-start forms like 'high severity:'; the new
        Guard 6 covers the no-colon forms like 'is high
        severity' as well. The finding example was:
        'Bumping the retry counter is high severity ...'."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Bumping the retry counter is high severity: classification must fail when stale",
            "state": "",
        }
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(
            len(got), 1,
            "Body with leading text-alias severity must be classified as a finding, not coordination"
        )
        self.assertEqual(got[0]["severity"], "P1")

    def test_text_alias_severity_no_colon_in_leading_rescues_too(self):
        """Same as above but the body uses 'is high severity'
        without the trailing colon. Guard 6 must also cover
        the no-colon form, which is the exact form from the
        fresh Codex finding."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Bumping the retry counter is high severity and this should be treated as a finding, not coordination",
            "state": "",
        }
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P1")

    def test_medium_severity_in_leading_rescues_coordination_skip(self):
        """Same as above for the medium alias (P2)."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Bumping this thread is medium severity: keep the new marker logic in the broad scan",
            "state": "",
        }
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P2")

    def test_text_alias_severity_in_meta_after_first_100_chars_still_skipped(self):
        """Sanity: a real coordination message that mentions
        severity only in meta-discussion past the first 100
        characters (the pattern Guard 5 protects against for
        blocking words) must still be skipped. The leading
        window keeps the rescue narrow and prevents
        false-positive rescues."""
        # 100+ char prefix, severity mentioned only after position 100
        body = (
            "Re-requesting Codex review on 3982ee6 (Fix AF). "
            "The active P1 current-head finding has been addressed "
            "by Fix AG and Fix T combined; allowing a malformed "
            "checkpoint to resume is no longer high severity."
        )
        self.assertGreater(len(body), 100, "test body must be over 100 chars to test the leading window")
        self.assertTrue(
            crc.is_coordination_comment(body),
            "Real coordination message with severity only in meta-discussion must still be skipped"
        )

    def test_negated_priority_does_not_rescue_coordination(self):
        """Regression for the third fresh Codex P2 finding on
        head a0ab489e9e54 (2026-06-21T02:54:45Z, dbID
        3447825478): 'Re-requesting Codex review — this is
        not high priority' must NOT be classified as a
        finding. The previous substring-based Guard 6
        matched the literal 'high priority' in the negation
        phrase and incorrectly rescued the body. The new
        regex-based Guard 6 requires an affirmative verb
        ('is'/'has'/'as'/'with') directly before the
        priority/severity token, so 'is not high priority'
        does not match."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — this is not high priority, please skip this re-prompt",
            "state": "",
        }
        # is_coordination_comment should return True (skip)
        self.assertTrue(
            crc.is_coordination_comment(item["body"]),
            "Negated priority ('is not high priority') must NOT trigger the Guard 6 rescue"
        )

    def test_negated_severity_does_not_rescue_coordination(self):
        """Same as above for the severity noun form."""
        body = "Re-requesting Codex review on 3982ee6 — this is not high severity, just a re-prompt"
        self.assertTrue(
            crc.is_coordination_comment(body),
            "Negated severity ('is not high severity') must NOT trigger the Guard 6 rescue"
        )

    def test_no_longer_severity_does_not_rescue_coordination(self):
        """Same as above for 'is no longer high severity' form."""
        body = "Re-requesting Codex review on 3982ee6 — is no longer high severity, please ignore"
        self.assertTrue(
            crc.is_coordination_comment(body),
            "'is no longer high severity' must NOT trigger the Guard 6 rescue"
        )

    def test_priority_word_alias_in_leading_rescues_coordination_skip(self):
        """Regression for the second fresh Codex P1 finding on
        head b4ff12deb316 (2026-06-21T02:46:26Z, dbID
        3447818802): 'Bumping the retry counter is high
        priority ...' must NOT be classified as coordination.
        The previous Guard 6 only covered 'high/medium/low
        severity'; extract_severity also accepts
        'high/medium/low priority' as text aliases, so the
        guard must cover both noun forms."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Bumping the retry counter is high priority and should be treated as a finding, not coordination",
            "state": "",
        }
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P1")

    def test_medium_priority_in_leading_rescues_coordination_skip(self):
        """Same as above for the medium priority alias (P2)."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Bumping this thread is medium priority: keep the new marker logic in the broad scan",
            "state": "",
        }
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P2")

    def test_low_priority_in_leading_rescues_coordination_skip(self):
        """Same as above for the low priority alias (P3)."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Bumping this thread is low priority: cosmetic issue in the rendered output",
            "state": "",
        }
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P3")

    def test_classified_as_high_priority_not_rescued(self):
        """Regression for the fourth fresh Codex P2 finding on
        head aa41a25b1bad (2026-06-21T03:01:27Z, dbID
        3447830523): a coordination comment that says a prior
        finding was 'classified as high priority' must NOT
        trigger the Guard 6 rescue. The previous cycle-5
        regex used ``as`` as an affirmative verb and falsely
        rescued this meta-discussion form. The cycle-6
        implementation narrows the verb set to copulas
        (``is``/``has``) only, so ``classified as high
        priority`` no longer matches."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — the prior finding was classified as high priority and fixed",
            "state": "",
        }
        # is_coordination_comment should return True (skip)
        self.assertTrue(
            crc.is_coordination_comment(item["body"]),
            "Meta-discussion 'classified as high priority' must NOT trigger Guard 6 rescue (dbID 3447830523)"
        )

    def test_flagged_as_high_severity_not_rescued(self):
        """Same as above for the 'flagged as high severity'
        meta-discussion form."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — this was flagged as high severity in the previous round",
            "state": "",
        }
        self.assertTrue(
            crc.is_coordination_comment(item["body"]),
            "Meta-discussion 'flagged as high severity' must NOT trigger Guard 6 rescue"
        )

    def test_with_high_priority_context_not_rescued(self):
        """Regression for dbID 3447830523: a coordination
        comment that mentions a 'with high priority context'
        form must NOT trigger the Guard 6 rescue. The
        cycle-5 regex used ``with`` as an affirmative verb
        and falsely rescued this context-form."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — fixed in prior commit with high priority context, please re-check",
            "state": "",
        }
        self.assertTrue(
            crc.is_coordination_comment(item["body"]),
            "Context phrase 'with high priority context' must NOT trigger Guard 6 rescue (dbID 3447830523)"
        )

    def test_with_high_severity_language_not_rescued(self):
        """Same as above for the 'with high severity language'
        context form."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — addressed previously with high severity language in the comment",
            "state": "",
        }
        self.assertTrue(
            crc.is_coordination_comment(item["body"]),
            "Context phrase 'with high severity language' must NOT trigger Guard 6 rescue"
        )

    def test_not_high_severity_negation_not_rescued(self):
        """Regression for cycle 5 (dbID 3447825478): the bare
        negation form 'not high severity' (no copula before
        'not') must NOT trigger Guard 6 rescue. The cycle-5
        regex with affirmative verbs handled this correctly;
        cycle 6 must preserve that behavior."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — this comment is not high severity, just a re-prompt",
            "state": "",
        }
        self.assertTrue(
            crc.is_coordination_comment(item["body"]),
            "Negation 'not high severity' must NOT trigger Guard 6 rescue"
        )

    def test_direct_is_high_priority_still_rescues(self):
        """Cycle-6 sanity check: the direct copula form
        'is high priority' MUST still trigger the Guard 6
        rescue. The cycle-5 cycle-6 fix only narrowed the
        verb set, did not eliminate the rescue itself."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting review: this comment is high priority and must be addressed before merge",
            "state": "",
        }
        # is_coordination_comment should return False (NOT skip)
        self.assertFalse(
            crc.is_coordination_comment(item["body"]),
            "Direct copula 'is high priority' MUST still trigger Guard 6 rescue"
        )

    def test_direct_has_high_severity_still_rescues(self):
        """Same as above for the 'has high severity' form."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting review: this finding has high severity implications for the watchdog loop",
            "state": "",
        }
        self.assertFalse(
            crc.is_coordination_comment(item["body"]),
            "Direct copula 'has high severity' MUST still trigger Guard 6 rescue"
        )

    def test_dbID_3447830523_exact_pattern_not_rescued(self):
        """Exact-pattern regression test for the Codex finding
        dbID 3447830523 (2026-06-21T03:01:27Z on head
        aa41a25b1bad). The finding's body contained 'classified
        as high priority' and 'with high priority context' as
        meta-discussion examples; both must NOT rescue. This
        test is the canonical pin for that specific Codex
        review finding."""
        body = (
            "Re-requesting Codex review — this prior finding was "
            "classified as high priority and was fixed with high "
            "priority context in the first 100 chars of this comment"
        )
        self.assertGreater(len(body), 100, "test body must exceed the leading-100 window")
        self.assertTrue(
            crc.is_coordination_comment(body),
            "The exact dbID 3447830523 pattern ('classified as high priority' "
            "+ 'with high priority context') must NOT trigger Guard 6 rescue"
        )

    def test_dbID_3447849261_article_bearing_priority_rescued(self):
        """Exact-pattern regression test for the cycle-6 fresh
        Codex P1 finding dbID 3447849261 (2026-06-21T03:25:34Z
        on head dfad7c833dd): 'Re-requesting review: this is a
        high priority issue' MUST be rescued by Guard 6 and
        classified as a P1 finding. The cycle-6 regex without
        the optional article dropped this real Codex finding
        as coordination. The cycle-7 regex restores the
        optional ``a``/``an`` article while keeping the verb
        set narrowed to copulas only."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting review: this comment is a high priority issue and must be addressed before merge",
            "state": "",
        }
        # is_coordination_comment should return False (NOT skip)
        self.assertFalse(
            crc.is_coordination_comment(item["body"]),
            "Article-bearing copula form 'is a high priority issue' MUST trigger Guard 6 rescue (dbID 3447849261)"
        )
        # And classify_item must classify it as P1
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1, f"Expected exactly 1 finding, got {got}")
        self.assertEqual(got[0]["severity"], "P1", f"Expected P1, got {got[0]['severity']}")

    def test_dbID_3447849261_article_bearing_severity_rescued(self):
        """Same as above for the 'has a high severity impact'
        form from the same Codex finding body."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting review: this comment has a high severity impact and should be treated as a finding",
            "state": "",
        }
        self.assertFalse(
            crc.is_coordination_comment(item["body"]),
            "Article-bearing copula form 'has a high severity impact' MUST trigger Guard 6 rescue (dbID 3447849261)"
        )
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1, f"Expected exactly 1 finding, got {got}")
        self.assertEqual(got[0]["severity"], "P1", f"Expected P1, got {got[0]['severity']}")

    def test_an_article_severity_rescued(self):
        """The 'an' article form must also be rescued (e.g.
        'is an high severity issue'). The 'an' article
        option in the regex matches when followed directly
        by the level token. This is grammatically unusual
        ('an high' is non-standard English) but tests the
        regex's 'an' code path directly."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting review: this is an high severity issue that must be fixed",
            "state": "",
        }
        self.assertFalse(
            crc.is_coordination_comment(item["body"]),
            "'is an high severity issue' MUST trigger Guard 6 rescue (testing 'an' code path)"
        )

    def test_direct_is_high_priority_preserved_from_cycle_5(self):
        """Cycle-7 sanity check: the cycle-5 direct copula
        form 'is high priority' (no article) MUST still
        trigger Guard 6 rescue. The cycle-7 fix only
        re-added the OPTIONAL article — it did not break
        the existing direct form."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting review: this comment is high priority and must be addressed before merge",
            "state": "",
        }
        self.assertFalse(
            crc.is_coordination_comment(item["body"]),
            "Direct copula 'is high priority' MUST still trigger Guard 6 rescue (cycle 5 regression check)"
        )

    def test_direct_has_high_severity_preserved_from_cycle_5(self):
        """Same as above for the 'has high severity' cycle-5
        direct form."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting review: this finding has high severity implications for the watchdog loop",
            "state": "",
        }
        self.assertFalse(
            crc.is_coordination_comment(item["body"]),
            "Direct copula 'has high severity' MUST still trigger Guard 6 rescue (cycle 5 regression check)"
        )

    def test_negated_article_bearing_priority_not_rescued(self):
        """Cycle-7 edge case: 'is not a high priority issue'
        (negation with article) must NOT be rescued. The
        article is optional, but the negation word 'not'
        between the copula and the article still breaks the
        regex match because the copula must be immediately
        followed by the optional article (no intervening
        'not')."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — this is not a high priority issue, just a re-prompt",
            "state": "",
        }
        self.assertTrue(
            crc.is_coordination_comment(item["body"]),
            "Negated article-bearing form 'is not a high priority issue' must NOT trigger Guard 6 rescue"
        )

    def test_has_no_severity_negation_not_rescued(self):
        """Cycle-7 edge case: 'has no high severity impact'
        (negation with article) must NOT be rescued. The
        copula is immediately followed by 'no', not by the
        optional article 'a' or 'an' or by the level token."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — this comment has no high severity impact, just a re-prompt",
            "state": "",
        }
        self.assertTrue(
            crc.is_coordination_comment(item["body"]),
            "Negation 'has no high severity impact' must NOT trigger Guard 6 rescue"
        )

    def test_described_as_article_priority_not_rescued(self):
        """Cycle-7 edge case: 'described as a high priority
        issue' (meta-discussion with article) must NOT be
        rescued. The verb 'as' is excluded, so the article
        pattern never engages here even though the article
        is technically allowed. The copula form would be
        'is a high priority issue' which IS rescued; the
        meta form 'described as a high priority issue' is
        NOT."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — this was described as a high priority issue in the prior round",
            "state": "",
        }
        self.assertTrue(
            crc.is_coordination_comment(item["body"]),
            "Meta-discussion 'described as a high priority issue' must NOT trigger Guard 6 rescue"
        )

    def test_classified_as_priority_preserved_from_cycle_6(self):
        """Cycle-7 regression check: the cycle-6 exact pattern
        'classified as high priority' must STILL NOT be
        rescued. The cycle-7 fix only added the optional
        article to the verb set; it did not change the
        verb set itself. The verb 'as' is still excluded."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — the prior finding was classified as high priority and fixed",
            "state": "",
        }
        self.assertTrue(
            crc.is_coordination_comment(item["body"]),
            "Cycle-6 regression: 'classified as high priority' must STILL NOT be rescued (cycle 7 regression check)"
        )

    def test_with_priority_context_preserved_from_cycle_6(self):
        """Cycle-7 regression check: the cycle-6 exact pattern
        'with high priority context' must STILL NOT be
        rescued. The verb 'with' is still excluded."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — fixed in prior commit with high priority context, please re-check",
            "state": "",
        }
        self.assertTrue(
            crc.is_coordination_comment(item["body"]),
            "Cycle-6 regression: 'with high priority context' must STILL NOT be rescued (cycle 7 regression check)"
        )

    def test_negated_priority_preserved_from_cycle_5(self):
        """Cycle-7 regression check: the cycle-5 exact
        negation 'is not high priority' must STILL NOT be
        rescued."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — this is not high priority, please skip this re-prompt",
            "state": "",
        }
        self.assertTrue(
            crc.is_coordination_comment(item["body"]),
            "Cycle-5 regression: 'is not high priority' must STILL NOT be rescued (cycle 7 regression check)"
        )


class TestExtractSeverityWordBoundary(unittest.TestCase):
    """Regression for Codex finding AI: severity aliases
    ``high``/``medium``/``low`` must be matched as whole
    WORDS, not substrings, so that words like ``highlight``,
    ``mediumship``, or ``below`` do not falsely match.
    """

    def test_high_severity_word_still_maps_to_p1(self):
        # ``High severity: ...`` must still map to P1.
        self.assertEqual(crc.extract_severity("High severity: path traversal"), "P1")

    def test_medium_severity_word_still_maps_to_p2(self):
        # ``Medium: ...`` must still map to P2.
        self.assertEqual(crc.extract_severity("Medium: test coverage gap"), "P2")

    def test_low_severity_word_still_maps_to_p3(self):
        # ``Low: ...`` must still map to P3.
        self.assertEqual(crc.extract_severity("Low: nit on variable name"), "P3")

    def test_highlight_does_not_match_high(self):
        # ``highlight`` contains ``high`` as a substring but must
        # NOT be classified as P1.
        self.assertIsNone(crc.extract_severity("Please highlight the docs"))

    def test_mediumship_does_not_match_medium(self):
        # ``mediumship`` contains ``medium`` as a substring but
        # must NOT be classified as P2.
        self.assertIsNone(crc.extract_severity("The mediumship of the message"))

    def test_below_does_not_match_low(self):
        # ``below`` contains ``low`` as a substring but must NOT
        # be classified as P3.
        self.assertIsNone(crc.extract_severity("See the section below"))

    def test_classify_item_highlight_not_p1(self):
        """The exact Codex finding AI scenario: a non-finding
        comment containing ``highlight`` must NOT be classified
        as a P1 blocker."""
        item = {
            "user": {"login": "reviewer"},
            "body": "Please highlight the docs in the README.",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        # Should be either empty (no severity) or UNSPECIFIED_INFO,
        # but NOT P1.
        for f in got:
            self.assertNotEqual(
                f["severity"], "P1",
                f"'highlight' must not be classified as P1, got {f}"
            )

    def test_p0_token_still_substring_match(self):
        # P0/P1/P2/P3 are short unambiguous tokens and should
        # still match as substrings.
        self.assertEqual(crc.extract_severity("this is a P0 critical bug"), "P0")

    def test_p1_token_still_substring_match(self):
        self.assertEqual(crc.extract_severity("P1: this is important"), "P1")

    def test_p2_token_still_substring_match(self):
        self.assertEqual(crc.extract_severity("P2: minor issue"), "P2")

    def test_p3_token_still_substring_match(self):
        self.assertEqual(crc.extract_severity("P3: nit"), "P3")

    def test_p0_takes_priority_over_aliases(self):
        # P0 must take priority over text aliases.
        self.assertEqual(crc.extract_severity("P0: high critical bug"), "P0")

    def test_existing_high_maps_to_p1_test_still_works(self):
        item = {
            "user": {"login": "reviewer"},
            "body": "High severity: path traversal risk",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P1")

    def test_existing_medium_maps_to_p2_test_still_works(self):
        item = {
            "user": {"login": "reviewer"},
            "body": "Medium: test coverage gap",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P2")


class TestExtractSeverityBadgePriority(unittest.TestCase):
    """Regression for Codex findings K87fX and K8vlc (PR #405
    repair). When a comment body documents the severity taxonomy
    (e.g. "P0/P1/P2 findings") AND declares a specific severity
    via badge/bracket/colon, the declared severity must win.
    The previous substring-first order returned the first
    P-token match, which was P0 in bodies that mentioned the
    P0/P1/P2 sequence before the declared P1 or P2.
    """

    def test_p1_badge_beats_p0_substring_in_taxonomy_body(self):
        # K87fX scenario: the body has ![P1 Badge] AND mentions
        # the P0/P1/P2 taxonomy. The declared P1 must win.
        body = (
            "<sub><sub>![P1 Badge]</sub></sub> Do not skip bracketed priority findings. "
            "When an actual review finding uses the bracketed priority format, "
            "e.g. [P1] Bumping the retry counter, this guard does not exempt it "
            "because it only recognizes P0/P1/P2: and badge syntax before applying "
            "the broad coordination substrings."
        )
        self.assertEqual(crc.extract_severity(body), "P1")

    def test_p2_badge_beats_p0_substring_in_taxonomy_body(self):
        # K8vlc scenario: the body has ![P2 Badge] AND mentions
        # the P0/P1/P2 taxonomy. The declared P2 must win.
        body = (
            "<sub><sub>![P2 Badge]</sub></sub> Narrow coordination skips before dropping findings. "
            "For example, a human review comment like P1: @codex flagged a security issue; "
            "must fix or P1: this bumping retry logic can fail. please restrict the skip "
            "to clearly identified PR-author coordination comments or classify explicit "
            "P0/P1/P2 findings first."
        )
        self.assertEqual(crc.extract_severity(body), "P2")

    def test_bracketed_priority_beats_substring(self):
        # The body has [P1] (bracketed) and P0 as a substring.
        # The bracketed form should win.
        body = "P0/P1/P2 finding forms: [P1] Bumping the retry counter"
        self.assertEqual(crc.extract_severity(body), "P1")

    def test_colon_form_beats_substring(self):
        # The body has P1: and P0 as a substring.
        # The colon form should win.
        body = "P0/P1/P2 findings exist. P1: this is the actual severity."
        self.assertEqual(crc.extract_severity(body), "P1")

    def test_badge_beats_bracket_and_colon(self):
        # If multiple specific forms are present, the highest-priority
        # (badge) wins. This is a tie-breaker, not a regression.
        body = "[P0] P1: something ![P2 Badge]"
        self.assertEqual(crc.extract_severity(body), "P2")

    def test_plain_substring_still_works_when_no_specific_form(self):
        # Backwards compatibility: if no badge/bracket/colon form
        # is present, the plain P-token substring still matches.
        self.assertEqual(crc.extract_severity("this is a P0 critical bug"), "P0")
        self.assertEqual(crc.extract_severity("this is a P1 important bug"), "P1")
        self.assertEqual(crc.extract_severity("this is a P2 minor issue"), "P2")
        self.assertEqual(crc.extract_severity("this is a P3 nit"), "P3")

    def test_p0_with_mentions_of_p1_p2_substring_still_returns_p0(self):
        # If a comment declares P0 and only mentions the P1/P2 taxonomy
        # in passing, P0 should still be returned. This was the
        # previous behavior and is preserved.
        body = "P0: critical bug. Note that P1/P2 findings are also relevant."
        self.assertEqual(crc.extract_severity(body), "P0")

    def test_classify_item_k87fx_severity(self):
        """End-to-end: classify_item on the K87fX body must return P1."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": (
                "<sub><sub>![P1 Badge]</sub></sub> Do not skip bracketed priority findings. "
                "When an actual review finding uses the bracketed priority format, "
                "e.g. [P1] Bumping the retry counter can skip failures, this guard "
                "does not exempt it because it only recognizes P0/P1/P2: and badge "
                "syntax before applying the broad coordination substrings."
            ),
            "state": "",
        }
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P1")

    def test_classify_item_k8vlc_severity(self):
        """End-to-end: classify_item on the K8vlc body must return P2."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": (
                "<sub><sub>![P2 Badge]</sub></sub> Narrow coordination skips before dropping findings. "
                "For example, a human review comment like P1: @codex flagged a security issue; "
                "must fix or P1: this bumping retry logic can fail now returns no finding, so the "
                "gate can report clean despite an unresolved blocker; please restrict the skip to "
                "clearly identified PR-author coordination comments or classify explicit P0/P1/P2 "
                "findings first."
            ),
            "state": "",
        }
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P2")


class TestGhApiSlurpPagination(unittest.TestCase):
    """Regression for Codex finding AH: ``gh_api`` must use
    ``--slurp`` so that multi-page responses are wrapped into
    a single JSON array, then flattened into a single list of
    items. Without ``--slurp``, ``gh api --paginate`` writes
    each page as a separate JSON document and ``json.loads``
    fails on the concatenated output.
    """

    def test_gh_api_uses_slurp_flag(self):
        """The ``gh api`` subprocess invocation must include
        the ``--slurp`` flag so multi-page responses are
        wrapped into a single JSON array."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = FakeResult(stdout="[]")
            crc.gh_api("OWNER/REPO", "issues/1/comments")
            cmd = mock_run.call_args[0][0]
            self.assertIn(
                "--slurp", cmd,
                "gh api command must include --slurp flag "
                "(Codex finding AH)"
            )

    def test_gh_api_uses_paginate_flag(self):
        """The ``gh api`` subprocess invocation must include
        the ``--paginate`` flag so multi-page responses are
        fetched."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = FakeResult(stdout="[]")
            crc.gh_api("OWNER/REPO", "issues/1/comments")
            cmd = mock_run.call_args[0][0]
            self.assertIn(
                "--paginate", cmd,
                "gh api command must include --paginate flag"
            )

    def test_gh_api_flattens_multi_page_response(self):
        """A multi-page response (list of lists) must be
        flattened into a single list of items."""
        page1 = [{"id": 1, "body": "first"}]
        page2 = [{"id": 2, "body": "second"}]
        slurped = json.dumps([page1, page2])  # --slurp output
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = FakeResult(stdout=slurped)
            ok, data, err = crc.gh_api("OWNER/REPO", "issues/1/comments")
            self.assertTrue(ok, f"expected ok=True, got err={err!r}")
            self.assertEqual(len(data), 2, "two pages should flatten to 2 items")
            self.assertEqual(data[0]["id"], 1)
            self.assertEqual(data[1]["id"], 2)

    def test_gh_api_single_page_response_works(self):
        """A single-page response (flat list) must still work."""
        single = [{"id": 1, "body": "only"}]
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = FakeResult(stdout=json.dumps(single))
            ok, data, err = crc.gh_api("OWNER/REPO", "issues/1/comments")
            self.assertTrue(ok, f"expected ok=True, got err={err!r}")
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["id"], 1)

    def test_gh_api_three_page_response_flattened(self):
        """A three-page response must be fully flattened."""
        pages = [
            [{"id": 1}],
            [{"id": 2}, {"id": 3}],
            [{"id": 4}],
        ]
        slurped = json.dumps(pages)
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = FakeResult(stdout=slurped)
            ok, data, err = crc.gh_api("OWNER/REPO", "issues/1/comments")
            self.assertTrue(ok, f"expected ok=True, got err={err!r}")
            self.assertEqual(len(data), 4)
            self.assertEqual([d["id"] for d in data], [1, 2, 3, 4])

    def test_gh_api_empty_response(self):
        """An empty response must return an empty list."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = FakeResult(stdout="")
            ok, data, err = crc.gh_api("OWNER/REPO", "issues/1/comments")
            self.assertTrue(ok)
            self.assertEqual(data, [])

    def test_gh_api_single_object_response(self):
        """A single-object response (not a list) must be wrapped
        into a single-item list."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = FakeResult(stdout=json.dumps({"id": 1}))
            ok, data, err = crc.gh_api("OWNER/REPO", "pulls/1")
            self.assertTrue(ok)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["id"], 1)


class TestDedup(unittest.TestCase):
    def test_exact_duplicate_removed(self):
        item = {
            "user": {"login": "bot"},
            "body": "P1: fix this",
            "state": "",
        }
        findings = [item, item]
        got = crc.dedup_findings(findings)
        self.assertEqual(len(got), 1)

    def test_different_users_kept(self):
        f1 = {"user": "alice", "body": "P1: fix this"}
        f2 = {"user": "bob", "body": "P1: fix this"}
        got = crc.dedup_findings([f1, f2])
        self.assertEqual(len(got), 2)


class TestWaiverLoad(unittest.TestCase):
    def test_valid_waiver(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "abc123abc123abc123abc123abc123abc123abcd12",
                "waivers": [
                    {
                        "finding_id": "p2-001",
                        "severity": "P2",
                        "status": "WAIVED_NON_BLOCKING",
                        "reason": "acceptable risk",
                        "evidence": "test passes",
                        "expires_after_pr": 321,
                    }
                ],
            }, fh)
            path = fh.name
        try:
            ok, data, err = crc.load_waiver(path, 320, "abc123abc123abc123abc123abc123abc123abcd12")
            self.assertTrue(ok)
            self.assertEqual(len(data["waivers"]), 1)
        finally:
            Path(path).unlink()

    def test_stale_sha_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "old_sha",
                "waivers": [],
            }, fh)
            path = fh.name
        try:
            ok, data, err = crc.load_waiver(path, 320, "new_sha")
            self.assertFalse(ok)
            self.assertIn("!=", err)
        finally:
            Path(path).unlink()

    def test_wrong_pr_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 319,
                "reported_head_sha": "abc123abc123abc123abc123abc123abc123abcd12",
                "waivers": [],
            }, fh)
            path = fh.name
        try:
            ok, data, err = crc.load_waiver(path, 320, "abc123")
            self.assertFalse(ok)
            self.assertIn("pr_number", err)
        finally:
            Path(path).unlink()


class TestIntegration(unittest.TestCase):
    """Run the script end-to-end with mocked gh API calls."""

    def _run(
        self,
        gh_responses: dict[str, FakeResult],
        extra_args: list[str] | None = None,
        gh_pr_view_oid: str = "abc123abc123abc123abc123abc123abc123abcd12",
        mock_load_waiver=None,
    ):
        """
        Patch subprocess.run to return FakeResult keyed by command snippet.
        Also patches gh_pr_view (the PR metadata function) directly.
        Optionally patches crc.load_waiver to verify it is or is not called.

        Provides a default empty GraphQL response for reviewThreads if not
        explicitly provided, so existing tests continue to work.
        """
        # Default empty GraphQL response if not overridden
        if "graphql" not in gh_responses:
            gh_responses["graphql"] = gh_reply({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"nodes": []},
            }}}})

        def fake_run(cmd, **kwargs):
            # Find matching response by command string
            cmd_str = " ".join(cmd)
            for key, resp in gh_responses.items():
                if key in cmd_str:
                    return resp
            return FakeResult(returncode=1, stderr=f"No mock for: {cmd_str}")

        def fake_gh_pr_view(repo: str, pr_number: int):
            return True, {"headRefOid": gh_pr_view_oid, "state": "OPEN", "url": f"https://github.com/{repo}/pull/{pr_number}"}, ""

        patches = [
            mock.patch.object(subprocess, "run", fake_run),
            mock.patch.object(crc, "gh_pr_view", fake_gh_pr_view),
        ]
        if mock_load_waiver is not None:
            patches.append(mock.patch.object(crc, "load_waiver", mock_load_waiver))

        ctx = contextlib.ExitStack()
        for p in patches:
            ctx.enter_context(p)
        with ctx:
            with tempfile.TemporaryDirectory() as tmp:
                json_out = Path(tmp) / "out.json"
                md_out = Path(tmp) / "out.md"
                args = [
                    "check_pr_review_comments.py",
                    "--repo", "OWNER/REPO",
                    "--pr-number", "320",
                    "--reported-head-sha", "abc123abc123abc123abc123abc123abc123abcd12",
                    "--output-json", str(json_out),
                    "--output-md", str(md_out),
                ]
                if extra_args:
                    args.extend(extra_args)
                with mock.patch.object(sys, "argv", args):
                    rc = crc.main()
                result = json.loads(json_out.read_text()) if json_out.exists() else {}
                md = md_out.read_text() if md_out.exists() else ""
                return rc, result

    def test_no_comments_clean(self):
        rc, data = self._run({
            "issues/320/comments": gh_reply([]),
            "pulls/320/comments": gh_reply([]),
            "pulls/320/reviews": gh_reply([]),
        })
        self.assertEqual(rc, crc.EXIT_CLEAN)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_CLEAN")
        self.assertEqual(len(data.get("findings", [])), 0)

    def test_p1_inline_blocked(self):
        rc, data = self._run({
            "issues/320/comments": gh_reply([]),
            "pulls/320/comments": gh_reply([{
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "body": "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange) Emit a real newline marker**",
                "path": "scripts/local/run_temp_worktree_execution.py",
                "line": 1821,
                "commit_id": "abc123abc123",
                "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
            }]),
            "pulls/320/reviews": gh_reply([]),
        })
        self.assertEqual(rc, crc.EXIT_BLOCKED)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_BLOCKED")
        self.assertEqual(len(data.get("blockers", [])), 1)
        self.assertEqual(data["blockers"][0]["severity"], "P1")

    def test_p2_blocked_by_default(self):
        rc, data = self._run({
            "issues/320/comments": gh_reply([{
                "user": {"login": "reviewer"},
                "body": "### P2\nValidate literal base SHA as object",
                "state": "",
            }]),
            "pulls/320/comments": gh_reply([]),
            "pulls/320/reviews": gh_reply([]),
        })
        self.assertEqual(rc, crc.EXIT_BLOCKED)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_BLOCKED")

    def test_p2_waived_clean(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "abc123abc123abc123abc123abc123abc123abcd12",
                "waivers": [
                    {
                        "finding_id": "p2-001",
                        "severity": "P2",
                        "status": "WAIVED_NON_BLOCKING",
                        "reason": "test risk acceptable",
                        "evidence": "smoke passes",
                        "expires_after_pr": 321,
                        "body_prefix": "P2: Validate literal base SHA",
                    }
                ],
            }, fh)
            waiver_path = fh.name
        import os
        os.environ["_TEST_WAIVER_PATH"] = waiver_path
        try:
            rc, data = self._run({
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "reviewer"},
                    "body": "P2: Validate literal base SHA",
                    "state": "",
                    "html_url": "https://github.com/OWNER/REPO/pull/1",
                }]),
                "pulls/320/reviews": gh_reply([]),
            }, extra_args=["--allow-p2-waivers", waiver_path])
            self.assertEqual(rc, crc.EXIT_CLEAN)
            self.assertEqual(data.get("status"), "REVIEW_COMMENTS_CLEAN")
            self.assertEqual(len(data.get("p2_waivers", [])), 1)
        finally:
            Path(waiver_path).unlink()

    def test_p1_waiver_still_blocked(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "abc123abc123abc123abc123abc123abc123abcd12",
                "waivers": [
                    {
                        "finding_id": "p1-001",
                        "severity": "P1",
                        "status": "WAIVED_NON_BLOCKING",
                        "reason": "intentional",
                        "evidence": "accepted risk",
                        "expires_after_pr": 321,
                    }
                ],
            }, fh)
            waiver_path = fh.name
        try:
            rc, data = self._run({
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "body": "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange) Emit a real newline marker**",
                    "path": "scripts/local/run_temp_worktree_execution.py",
                    "line": 1821,
                }]),
                "pulls/320/reviews": gh_reply([]),
            }, extra_args=["--allow-p2-waivers", waiver_path])
            # P1 waivers are not supported — must still block
            self.assertEqual(rc, crc.EXIT_BLOCKED)
        finally:
            Path(waiver_path).unlink()

    def test_finding_id_non_empty(self):
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange) Emit a real newline marker**",
            "path": "scripts/local/run_temp_worktree_execution.py",
            "line": 1821,
            "commit_id": "abc123abc123",
        }
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1)
        fid = got[0]["finding_id"]
        self.assertTrue(fid.startswith("codex-"), f"finding_id should start with codex-, got {fid}")
        self.assertEqual(len(fid), 18)  # "codex-" + 12 hex chars

    def test_duplicate_same_endpoint_same_id(self):
        """Same item from same endpoint -> same finding_id (dedup collapses it)."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange) Emit a real newline marker**",
            "path": "scripts/local/run_temp_worktree_execution.py",
            "line": 1821,
        }
        f1 = crc.classify_item(item, "inline_review_comment", set())
        f2 = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(f1[0]["finding_id"], f2[0]["finding_id"])

    def test_same_finding_different_endpoints_deduped(self):
        """
        Same Codex finding text from two different endpoints produces one finding
        with both source kinds in its 'sources' list.
        """
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange) Emit a real newline marker**",
            "path": "scripts/local/run_temp_worktree_execution.py",
            "line": 1821,
        }
        f1_raw = crc.classify_item(item, "inline_review_comment", set())
        f2_raw = crc.classify_item(item, "per_review_comment", set())
        for f in f1_raw:
            f["_source_kind"] = "inline_review_comment"
        for f in f2_raw:
            f["_source_kind"] = "per_review_comment"

        findings = f1_raw + f2_raw
        deduped = crc.dedup_findings(findings)

        self.assertEqual(len(deduped), 1, "Same finding from 2 endpoints should collapse to 1")
        self.assertIn("sources", deduped[0])
        self.assertEqual(len(deduped[0]["sources"]), 2)
        self.assertEqual(set(deduped[0]["sources"]), {"inline_review_comment", "per_review_comment"})

    def test_blocker_count_unique_by_finding_id(self):
        """
        The same finding appearing from 3 endpoints should count as 1 blocker.
        """
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "P1: emit a real newline marker",
            "path": "scripts/local/run_temp_worktree_execution.py",
            "line": 1821,
        }
        findings = []
        for src in ["issue_comment", "inline_review_comment", "per_review_comment"]:
            for f in crc.classify_item(item, src, set()):
                f["_source_kind"] = src
                findings.append(f)

        deduped = crc.dedup_findings(findings)
        # Only P0/P1/UNSPECIFIED_BLOCKING count as blockers
        blockers = [f for f in deduped if f["severity"] in ("P0", "P1", "UNSPECIFIED_BLOCKING")]
        self.assertEqual(len(blockers), 1, "Duplicate P1 from 3 endpoints = 1 blocker")

    def test_p2_waiver_by_finding_id_waives_dup(self):
        """
        A P2 finding from two endpoints is deduped to one entry.
        A waiver by exact finding_id should waive that one entry.
        """
        item = {
            "user": {"login": "reviewer"},
            "body": "P2: validate base SHA",
            "path": "scripts/local/run_autocoder_eval_corpus.py",
            "line": 42,
        }
        findings = []
        for src in ["inline_review_comment", "per_review_comment"]:
            for f in crc.classify_item(item, src, set()):
                f["_source_kind"] = src
                findings.append(f)

        deduped = crc.dedup_findings(findings)
        self.assertEqual(len(deduped), 1, "Same P2 from 2 endpoints -> 1 finding")
        fid = deduped[0]["finding_id"]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "abc123abc123abc123abc123abc123abc123abcd12",
                "waivers": [
                    {
                        "finding_id": fid,
                        "severity": "P2",
                        "status": "WAIVED_NON_BLOCKING",
                        "reason": "already fixed in later commits",
                        "evidence": "post-merge verification passes",
                        "expires_after_pr": 321,
                    }
                ],
            }, fh)
            waiver_path = fh.name
        try:
            rc, data = self._run({
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "reviewer"},
                    "body": "P2: validate base SHA",
                    "path": "scripts/local/run_autocoder_eval_corpus.py",
                    "line": 42,
                }]),
                "pulls/320/reviews": gh_reply([]),
            }, extra_args=["--allow-p2-waivers", waiver_path])
            self.assertEqual(rc, crc.EXIT_CLEAN, "Exact finding_id waiver should clear P2 blocker")
            self.assertEqual(data.get("status"), "REVIEW_COMMENTS_CLEAN")
        finally:
            Path(waiver_path).unlink()

    def test_p1_dup_still_blocks_once(self):
        """Same P1 from 2 endpoints -> 1 finding -> still blocks (P1 not waivable)."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "P1: emit a real newline marker",
            "path": "scripts/local/run_temp_worktree_execution.py",
            "line": 1821,
        }
        findings = []
        for src in ["inline_review_comment", "per_review_comment"]:
            for f in crc.classify_item(item, src, set()):
                f["_source_kind"] = src
                findings.append(f)

        deduped = crc.dedup_findings(findings)
        blockers = [f for f in deduped if f["severity"] in ("P0", "P1", "UNSPECIFIED_BLOCKING")]
        self.assertEqual(len(blockers), 1, "Duplicate P1 -> 1 blocker, not 2")

    def test_different_users_different_finding_ids(self):
        item1 = {
            "user": {"login": "alice"},
            "body": "P1: fix this",
            "path": "x.py",
            "line": 10,
        }
        item2 = {
            "user": {"login": "bob"},
            "body": "P1: fix this",
            "path": "x.py",
            "line": 10,
        }
        f1 = crc.classify_item(item1, "issue_comment", set())
        f2 = crc.classify_item(item2, "issue_comment", set())
        self.assertNotEqual(f1[0]["finding_id"], f2[0]["finding_id"])

    def test_finding_id_used_in_dedup(self):
        item = {
            "user": {"login": "bot"},
            "body": "P1: fix this",
            "state": "",
        }
        findings = [
            {"finding_id": "codex-aaa111", "user": "bot", "body": "P1: fix this"},
            {"finding_id": "codex-aaa111", "user": "bot", "body": "P1: fix this"},
            {"finding_id": "codex-zzz999", "user": "bot", "body": "P1: fix this"},
        ]
        got = crc.dedup_findings(findings)
        self.assertEqual(len(got), 2)  # two unique IDs

    def test_p2_waiver_by_finding_id_clean(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "abc123abc123abc123abc123abc123abc123abcd12",
                "waivers": [
                    {
                        "finding_id": "codex-abc123def456",
                        "severity": "P2",
                        "status": "WAIVED_NON_BLOCKING",
                        "reason": "test risk acceptable",
                        "evidence": "smoke passes",
                        "expires_after_pr": 321,
                        "body_prefix": "P2: validate",
                    }
                ],
            }, fh)
            waiver_path = fh.name
        try:
            rc, data = self._run({
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "reviewer"},
                    "body": "P2: validate literal base SHA",
                    "state": "",
                    "html_url": "https://github.com/OWNER/REPO/pull/1",
                }]),
                "pulls/320/reviews": gh_reply([]),
            }, extra_args=["--allow-p2-waivers", waiver_path, "--reported-head-sha", "abc123abc123abc123abc123abc123abc123abcd12"])
            # The finding_id won't match (random hash), so it should still block
            # unless finding_id is actually the same as the waiver
            # This tests that P2 still blocks without exact finding_id match
            self.assertEqual(rc, crc.EXIT_BLOCKED)
        finally:
            Path(waiver_path).unlink()

    def test_stale_waiver_sha_blocks(self):
        """
        Stale waiver SHA causes load_waiver to reject the file.
        P1-B fix: live head mismatch also blocks before waivers are applied.
        Result: P2 still blocks (waiver rejected), exit 1.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "old_sha_abc123",
                "waivers": [
                    {
                        "finding_id": "codex-abc123def456",
                        "severity": "P2",
                        "status": "WAIVED_NON_BLOCKING",
                        "reason": "old",
                        "evidence": "x",
                        "expires_after_pr": 321,
                        "body_prefix": "P2: validate",
                    }
                ],
            }, fh)
            waiver_path = fh.name
        try:
            rc, data = self._run({
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "reviewer"},
                    "body": "P2: validate literal base SHA",
                    "state": "",
                    "html_url": "https://github.com/OWNER/REPO/pull/1",
                }]),
                "pulls/320/reviews": gh_reply([]),
            }, extra_args=["--allow-p2-waivers", waiver_path, "--reported-head-sha", "xyz789xyz789xyz789xyz789xyz789xyz789xy0123"],
                gh_pr_view_oid="xyz789xyz789xyz789xyz789xyz789xyz789xy0123")
            # Waiver SHA mismatch (old_sha_abc123 != new_sha_xyz789) rejects the waiver.
            # P2 still blocks since it's not waived. Exit 1 BLOCKED.
            self.assertEqual(rc, crc.EXIT_BLOCKED)
            self.assertEqual(data.get("status"), "REVIEW_COMMENTS_BLOCKED")
        finally:
            Path(waiver_path).unlink()


    def test_live_head_mismatch_blocks_before_waivers(self):
        """
        P1-B: live head SHA != --reported-head-sha => inconclusive, waivers not applied.
        The finding is NOT waived (mismatch blocks before waiver lookup).
        Exit 2 INCONCLUSIVE since there are no blockers but there is a SHA mismatch.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "abc123abc123abc123abc123abc123abc123abcd12",
                "waivers": [
                    {
                        "finding_id": "codex-abc123def456",
                        "severity": "P2",
                        "status": "WAIVED_NON_BLOCKING",
                        "reason": "should not apply",
                        "evidence": "SHA mismatch blocks first",
                        "expires_after_pr": 321,
                        "body_prefix": "P2: validate",
                    }
                ],
            }, fh)
            waiver_path = fh.name
        try:
            # Live head is "xyz789new" but reported is "abc123"
            rc, data = self._run({
                "issues/320/comments": gh_reply([{
                    "user": {"login": "reviewer"},
                    "body": "P2: validate literal base SHA",
                    "state": "",
                    "html_url": "https://github.com/OWNER/REPO/pull/1",
                }]),
                "pulls/320/comments": gh_reply([]),
                "pulls/320/reviews": gh_reply([]),
            }, extra_args=["--allow-p2-waivers", waiver_path, "--reported-head-sha", "abc123abc123abc123abc123abc123abc123abcd12"],
                gh_pr_view_oid="xyz789xyz789xyz789xyz789xyz789xyz789xy0123")
            # SHA mismatch: waivers not applied, status inconclusive (no blockers but sha_mismatch=True)
            self.assertEqual(rc, crc.EXIT_INCONCLUSIVE)
            self.assertTrue(data.get("head_sha_mismatch"))
            self.assertTrue(len(data.get("api_errors", [])) >= 1)
            # Finding is NOT waived because waivers were not applied
            findings = data.get("findings", [])
            self.assertEqual(len(findings), 1)
            self.assertFalse(findings[0].get("_waived"))
        finally:
            Path(waiver_path).unlink()

    def test_live_head_matches_reported_clean(self):
        """
        P1-B: live head == --reported-head-sha => normal processing, waivers can apply.
        With no findings, status is CLEAN.
        """
        rc, data = self._run({
            "issues/320/comments": gh_reply([]),
            "pulls/320/comments": gh_reply([]),
            "pulls/320/reviews": gh_reply([]),
        }, gh_pr_view_oid="abc123abc123abc123abc123abc123abc123abcd12")
        self.assertEqual(rc, crc.EXIT_CLEAN)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_CLEAN")
        self.assertFalse(data.get("head_sha_mismatch"))
        self.assertEqual(data.get("live_head_sha"), "abc123abc123abc123abc123abc123abc123abcd12")

    def test_api_failure_on_any_endpoint_inconclusive(self):
        """
        P1-A: API failure on one endpoint => INCONCLUSIVE (never CLEAN with partial data).
        Even if some endpoints succeed and yield findings, any failure makes status inconclusive.
        """
        rc, data = self._run({
            "issues/320/comments": gh_reply([]),
            "pulls/320/comments": gh_reply([{
                "user": {"login": "reviewer"},
                "body": "P2: validate base SHA",
                "state": "",
            }]),
            "pulls/320/reviews": gh_fail("server error on reviews endpoint"),
        }, gh_pr_view_oid="abc123abc123abc123abc123abc123abc123abcd12")
        self.assertEqual(rc, crc.EXIT_INCONCLUSIVE)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_INCONCLUSIVE")
        self.assertTrue(len(data.get("api_errors", [])) >= 1)

    def test_per_review_comments_fetched(self):
        """Reviews with IDs trigger per-review comment fetch."""
        def fake_run(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "graphql" in cmd_str:
                return FakeResult(stdout=json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"nodes": []},
                }}}}))
            if "reviews/456/comments" in cmd_str:
                return gh_reply([])
            if "pulls/320/reviews" in cmd_str:
                return gh_reply([{
                    "id": 456,
                    "user": {"login": "reviewer"},
                    "body": "overall review",
                    "state": "COMMENTED",
                }])
            if "comments" in cmd_str:
                return gh_reply([])
            return gh_reply([])

        def fake_gh_pr_view(repo, pr_number):
            return True, {"headRefOid": "abc123abc123abc123abc123abc123abc123abcd12", "state": "OPEN", "url": f"https://github.com/{repo}/pull/{pr_number}"}, ""

        with mock.patch.object(subprocess, "run", fake_run):
            with mock.patch.object(crc, "gh_pr_view", fake_gh_pr_view):
                with tempfile.TemporaryDirectory() as tmp:
                    json_out = Path(tmp) / "out.json"
                    md_out = Path(tmp) / "out.md"
                    with mock.patch.object(sys, "argv", [
                        "check_pr_review_comments.py",
                        "--repo", "OWNER/REPO",
                        "--pr-number", "320",
                        "--reported-head-sha", "abc123abc123abc123abc123abc123abc123abcd12",
                        "--output-json", str(json_out),
                        "--output-md", str(md_out),
                    ]):
                        rc = crc.main()
        self.assertEqual(rc, crc.EXIT_CLEAN)

    def test_subprocess_uses_list_argv_no_shell(self):
        """Verify all subprocess.run calls use list argv and shell=False."""
        captured_calls = []
        def fake_gh_pr_view(repo, pr_number):
            return True, {"headRefOid": "abc123abc123abc123abc123abc123abc123abcd12", "state": "OPEN", "url": f"https://github.com/{repo}/pull/{pr_number}"}, ""
        def capturing_run(cmd, **kwargs):
            captured_calls.append(cmd)
            return FakeResult(stdout="[]")

        with mock.patch.object(subprocess, "run", capturing_run):
            with mock.patch.object(crc, "gh_pr_view", fake_gh_pr_view):
                with tempfile.TemporaryDirectory() as tmp:
                    json_out = Path(tmp) / "out.json"
                    md_out = Path(tmp) / "out.md"
                    with mock.patch.object(sys, "argv", [
                        "check_pr_review_comments.py",
                        "--repo", "OWNER/REPO",
                        "--pr-number", "320",
                        "--reported-head-sha", "abc123abc123abc123abc123abc123abc123abcd12",
                        "--output-json", str(json_out),
                        "--output-md", str(md_out),
                    ]):
                        try:
                            crc.main()
                        except Exception:
                            pass  # ignore errors, we only care about call patterns

        for cmd in captured_calls:
            self.assertIsInstance(cmd, (list, tuple)), \
                f"subprocess.run must receive list argv, got {type(cmd)}: {cmd}"
            self.assertNotIn("shell=True", " ".join(str(c) for c in cmd))

    def test_head_mismatch_load_waiver_never_called(self):
        """
        FAIL-FAST: when live head != reported head, load_waiver must NEVER be called.
        Even if a waiver file path is provided, the mismatch branch exits before
        waiver loading is reachable.
        """
        def load_waiver_raises(*args, **kwargs):
            raise AssertionError("load_waiver was called on head SHA mismatch — FAIL-FAST violated")

        fh = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        waiver_path = fh.name
        try:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "abc123abc123abc123abc123abc123abc123abcd12",
                "waivers": [{"finding_id": "p2-001", "severity": "P2",
                             "status": "WAIVED_NON_BLOCKING", "reason": "test",
                             "expires_after_pr": 321}],
            }, fh)
            fh.close()

            # Live head is "xyz789..." (mismatch with waiver SHA "abc123...")
            rc, data = self._run(
                {
                    "issues/320/comments": gh_reply([]),
                    "pulls/320/comments": gh_reply([]),
                    "pulls/320/reviews": gh_reply([]),
                },
                extra_args=["--allow-p2-waivers", waiver_path,
                            "--reported-head-sha", "abc123abc123abc123abc123abc123abc123abcd12"],
                gh_pr_view_oid="xyz789xyz789xyz789xyz789xyz789xyz789xy0123",
                mock_load_waiver=load_waiver_raises,
            )
            # Exit 2 INCONCLUSIVE due to mismatch
            self.assertEqual(rc, crc.EXIT_INCONCLUSIVE)
            self.assertEqual(data.get("status"), "REVIEW_COMMENTS_INCONCLUSIVE")
            self.assertTrue(data.get("head_sha_mismatch"))
            # Findings are reported but no waivers applied
            self.assertEqual(data.get("blockers"), [])
            self.assertEqual(data.get("p2_waivers"), [])
        finally:
            Path(waiver_path).unlink(missing_ok=True)

    def test_head_match_allows_load_waiver(self):
        """
        When live head == reported head, load_waiver IS called and waivers apply.
        """
        fid = crc.make_finding_id(
            "chatgpt-codex-connector[bot]",
            "scripts/local/run_temp_worktree_execution.py",
            "1821",
            "P2",
            "**<sub><sub>![P2](https://img.shields.io/badge/P2-yellow) fix this**"
        )
        fh = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        waiver_path = fh.name
        try:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "abc123abc123abc123abc123abc123abc123abcd12",
                "waivers": [{"finding_id": fid, "severity": "P2",
                             "status": "WAIVED_NON_BLOCKING", "reason": "acceptable risk",
                             "evidence": "test passes", "expires_after_pr": 321,
                             "body_prefix": "p2: fix this"}],
            }, fh)
            fh.close()

            # Matching SHA, P2 comment that matches the waiver
            rc, data = self._run(
                {
                    "issues/320/comments": gh_reply([]),
                    "pulls/320/comments": gh_reply([{
                        "user": {"login": "chatgpt-codex-connector[bot]"},
                        "body": "**<sub><sub>![P2](https://img.shields.io/badge/P2-yellow) fix this**",
                        "path": "scripts/local/run_temp_worktree_execution.py",
                        "line": 1821,
                        "commit_id": "abc123abc123",
                        "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                    }]),
                    "pulls/320/reviews": gh_reply([]),
                },
                extra_args=["--allow-p2-waivers", waiver_path],
                gh_pr_view_oid="abc123abc123abc123abc123abc123abc123abcd12",
            )
            # Waiver applies, P2 is no longer a blocker -> CLEAN
            self.assertEqual(rc, crc.EXIT_CLEAN)
            self.assertEqual(data.get("status"), "REVIEW_COMMENTS_CLEAN")
            self.assertFalse(data.get("head_sha_mismatch"))
            self.assertGreaterEqual(len(data.get("p2_waivers", [])), 1)
        finally:
            Path(waiver_path).unlink(missing_ok=True)

    def test_stale_waiver_not_replayed_after_new_commits(self):
        """
        Waiver was issued for OLD SHA. New commits landed (live head != waiver SHA).
        The waiver MUST NOT be loaded or applied. Status = INCONCLUSIVE.
        """
        fh = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        waiver_path = fh.name
        try:
            json.dump({
                "pr_number": 320,
                "reported_head_sha": "abc123abc123abc123abc123abc123abc123abcd12",
                "waivers": [{"finding_id": "p2-001", "severity": "P2",
                             "status": "WAIVED_NON_BLOCKING", "reason": "stale waiver",
                             "expires_after_pr": 321}],
            }, fh)
            fh.close()

            # Live head is "xyz789..." but waiver is for "abc123..."
            rc, data = self._run(
                {
                    "issues/320/comments": gh_reply([]),
                    "pulls/320/comments": gh_reply([{
                        "user": {"login": "chatgpt-codex-connector[bot]"},
                        "body": "**<sub><sub>![P2](https://img.shields.io/badge/P2-yellow) fix this**",
                        "path": "scripts/local/run_temp_worktree_execution.py",
                        "line": 1821,
                        "commit_id": "xyz789xyz789",  # current head commit
                        "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                    }]),
                    "pulls/320/reviews": gh_reply([]),
                },
                extra_args=["--allow-p2-waivers", waiver_path,
                            "--reported-head-sha", "abc123abc123abc123abc123abc123abc123abcd12"],
                gh_pr_view_oid="xyz789xyz789xyz789xyz789xyz789xyz789xy0123",
            )
            # Mismatch -> INCONCLUSIVE, waivers not applied
            self.assertEqual(rc, crc.EXIT_INCONCLUSIVE)
            self.assertTrue(data.get("head_sha_mismatch"))
            self.assertEqual(data.get("blockers"), [])  # no current-head blockers on mismatch
            self.assertEqual(data.get("p2_waivers"), [])
        finally:
            Path(waiver_path).unlink(missing_ok=True)

    def test_current_head_p1_still_blocks(self):
        """
        Current-head P1 findings still block normally when live head == reported head.
        This is the non-regression case: P1s never get waived silently.
        """
        rc, data = self._run(
            {
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "body": "**<sub><sub>![P1](https://img.shields.io/badge/P1-orange) fix this P1 issue**",
                    "path": "scripts/local/run_temp_worktree_execution.py",
                    "line": 1821,
                    "commit_id": "abc123abc123",
                    "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                }]),
                "pulls/320/reviews": gh_reply([]),
                "graphql": gh_reply({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"nodes": [{
                        "id": "PRRT_test1",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {"nodes": [{
                            "databaseId": 1,
                            "url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                        }]},
                    }]},
                }}}}),
            },
            gh_pr_view_oid="abc123abc123abc123abc123abc123abc123abcd12",
        )
        # P1 blocks, no waivers apply
        self.assertEqual(rc, crc.EXIT_BLOCKED)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_BLOCKED")
        self.assertEqual(len(data.get("blockers", [])), 1)
        self.assertEqual(data["blockers"][0]["severity"], "P1")
        self.assertEqual(data.get("p2_waivers"), [])
        # Thread state correctly attached
        self.assertEqual(data["blockers"][0].get("thread_id"), "PRRT_test1")
        self.assertEqual(data["blockers"][0].get("thread_resolved"), False)

    def test_current_head_resolved_p1_review_thread_does_not_block(self):
        """
        When a current-head P1 finding is in a resolved GitHub review thread,
        it is reported as resolved_non_blockers and does NOT block.
        Status should be CLEAN (no remaining blockers).
        """
        rc, data = self._run(
            {
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "body": "**<sub><sub>![P1](https://img.shields.io/badge/P1-orange) fix this P1 issue**",
                    "path": "scripts/local/run_temp_worktree_execution.py",
                    "line": 1821,
                    "commit_id": "abc123abc123",
                    "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                }]),
                "pulls/320/reviews": gh_reply([]),
                "graphql": gh_reply({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"nodes": [{
                        "id": "PRRT_resolved1",
                        "isResolved": True,  # Thread is RESOLVED
                        "isOutdated": False,
                        "comments": {"nodes": [{
                            "databaseId": 1,
                            "url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                        }]},
                    }]},
                }}}}),
            },
            gh_pr_view_oid="abc123abc123abc123abc123abc123abc123abcd12",
        )
        # Resolved thread -> not blocking
        self.assertEqual(rc, crc.EXIT_CLEAN)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_CLEAN")
        # The P1 is in resolved_non_blockers, not blockers
        self.assertEqual(len(data.get("blockers", [])), 0)
        self.assertEqual(len(data.get("resolved_non_blockers", [])), 1)
        self.assertEqual(data["resolved_non_blockers"][0]["severity"], "P1")
        self.assertEqual(data["resolved_non_blockers"][0].get("thread_resolved"), True)
        self.assertEqual(data["resolved_non_blockers"][0].get("thread_id"), "PRRT_resolved1")

    def test_current_head_p1_missing_thread_metadata_fails_closed(self):
        """
        When a current-head P1 has no thread metadata (finding URL not in GraphQL result),
        it should fail closed: treated as a blocker, status BLOCKED.
        """
        rc, data = self._run(
            {
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "body": "**<sub><sub>![P1](https://img.shields.io/badge/P1-orange) fix this P1 issue**",
                    "path": "scripts/local/run_temp_worktree_execution.py",
                    "line": 1821,
                    "commit_id": "abc123abc123",
                    "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                }]),
                "pulls/320/reviews": gh_reply([]),
                # GraphQL returns NO threads matching this URL
                "graphql": gh_reply({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"nodes": []},
                }}}}),
            },
            gh_pr_view_oid="abc123abc123abc123abc123abc123abc123abcd12",
        )
        # No thread metadata -> fail closed -> BLOCKED
        self.assertEqual(rc, crc.EXIT_BLOCKED)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_BLOCKED")
        self.assertEqual(len(data.get("blockers", [])), 1)
        self.assertEqual(data["blockers"][0]["severity"], "P1")
        self.assertEqual(data["blockers"][0].get("thread_id"), "")

    def test_graphql_review_threads_api_failure_inconclusive(self):
        """
        GraphQL API failure for reviewThreads -> INCONCLUSIVE.
        Cannot determine resolution state, so fail closed.
        """
        rc, data = self._run(
            {
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "body": "**<sub><sub>![P1](https://img.shields.io/badge/P1-orange) fix this P1 issue**",
                    "path": "scripts/local/run_temp_worktree_execution.py",
                    "line": 1821,
                    "commit_id": "abc123abc123",
                    "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                }]),
                "pulls/320/reviews": gh_reply([]),
                "graphql": gh_fail("GraphQL endpoint unavailable"),
            },
            gh_pr_view_oid="abc123abc123abc123abc123abc123abc123abcd12",
        )
        self.assertEqual(rc, crc.EXIT_INCONCLUSIVE)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_INCONCLUSIVE")
        self.assertIsNotNone(data.get("thread_api_error"))
        self.assertIn("graphql", data["thread_api_error"].lower())

    def test_stale_p1_still_reported_as_stale_not_silently_ignored(self):
        """
        Stale P1 findings are reported as stale_blockers, not silently dropped.
        They do not block, but they are clearly visible in the output.
        """
        rc, data = self._run(
            {
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "body": "**<sub><sub>![P1](https://img.shields.io/badge/P1-orange) fix this P1 issue**",
                    "path": "scripts/local/run_temp_worktree_execution.py",
                    "line": 1821,
                    "commit_id": "oldoldold",  # Does NOT match current head
                    "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                }]),
                "pulls/320/reviews": gh_reply([]),
                "graphql": gh_reply({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"nodes": [{
                        "id": "PRRT_stale1",
                        "isResolved": False,
                        "isOutdated": True,
                        "comments": {"nodes": [{
                            "databaseId": 1,
                            "url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                        }]},
                    }]},
                }}}}),
            },
            gh_pr_view_oid="abc123abc123abc123abc123abc123abc123abcd12",
        )
        # Stale P1 -> INCONCLUSIVE (not BLOCKED), reported as stale_blockers
        self.assertEqual(rc, crc.EXIT_INCONCLUSIVE)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_INCONCLUSIVE")
        self.assertEqual(len(data.get("stale_blockers", [])), 1)
        self.assertEqual(data["stale_blockers"][0]["severity"], "P1")
        self.assertEqual(data["stale_blockers"][0]["is_stale_head"], True)
        # Not in blockers
        self.assertEqual(len(data.get("blockers", [])), 0)

    def test_resolved_findings_appear_in_json_and_markdown_output(self):
        """
        Resolved findings appear in both JSON (resolved_non_blockers) and
        markdown (Resolved Review Threads section) output.
        """
        rc, data = self._run(
            {
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "body": "**<sub><sub>![P1](https://img.shields.io/badge/P1-orange) fix this P1 issue**",
                    "path": "scripts/local/run_temp_worktree_execution.py",
                    "line": 1821,
                    "commit_id": "abc123abc123",
                    "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                }]),
                "pulls/320/reviews": gh_reply([]),
                "graphql": gh_reply({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"nodes": [{
                        "id": "PRRT_resolved2",
                        "isResolved": True,
                        "isOutdated": False,
                        "comments": {"nodes": [{
                            "databaseId": 1,
                            "url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                        }]},
                    }]},
                }}}}),
            },
            gh_pr_view_oid="abc123abc123abc123abc123abc123abc123abcd12",
        )
        # JSON: resolved_non_blockers has the finding
        self.assertEqual(len(data.get("resolved_non_blockers", [])), 1)
        self.assertEqual(data["resolved_non_blockers"][0]["thread_resolved"], True)
        # Thread ID visible in finding
        self.assertEqual(data["resolved_non_blockers"][0]["thread_id"], "PRRT_resolved2")

    def test_resolved_stale_p1_returns_clean(self):
        """
        Resolved stale P1 findings return REVIEW_COMMENTS_CLEAN.
        thread_resolved=True on a stale finding means it is reported as
        resolved_stale_blockers in the output but does NOT make the gate
        inconclusive. This is the key semantics fix for PR #325.
        """
        rc, data = self._run(
            {
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "body": "**<sub><sub>![P1](https://img.shields.io/badge/P1-orange) fix this P1 issue**",
                    "path": "scripts/local/run_temp_worktree_execution.py",
                    "line": 1821,
                    "commit_id": "oldoldold",   # Does NOT match current head
                    "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                }]),
                "pulls/320/reviews": gh_reply([]),
                "graphql": gh_reply({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"nodes": [{
                        "id": "PRRT_stale_resolved",
                        "isResolved": True,   # Thread resolved — key fix
                        "isOutdated": True,
                        "comments": {"nodes": [{
                            "databaseId": 1,
                            "url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                        }]},
                    }]},
                }}}}),
            },
            gh_pr_view_oid="abc123abc123abc123abc123abc123abc123abcd12",
        )
        # Exit code 0 = CLEAN (not INCONCLUSIVE)
        self.assertEqual(rc, crc.EXIT_CLEAN)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_CLEAN")
        # Resolved stale P1 in resolved_stale_blockers
        self.assertEqual(len(data.get("resolved_stale_blockers", [])), 1)
        self.assertEqual(
            data["resolved_stale_blockers"][0]["severity"], "P1")
        self.assertEqual(
            data["resolved_stale_blockers"][0]["is_stale_head"], True)
        self.assertEqual(
            data["resolved_stale_blockers"][0]["thread_resolved"], True)
        # Not in stale_blockers (unresolved)
        self.assertEqual(len(data.get("stale_blockers", [])), 0)
        # Not in current-head blockers
        self.assertEqual(len(data.get("blockers", [])), 0)

    def test_resolved_stale_p1_plus_current_unspecified_info_returns_clean(self):
        """
        Resolved stale P1 + current-head UNSPECIFIED_INFO => CLEAN.
        UNSPECIFIED_INFO is never blocking. Resolved stale P1 is also not
        blocking. No current-head P0/P1/P2 blockers exist.
        """
        rc, data = self._run(
            {
                "issues/320/comments": gh_reply([{
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "body": "Nit: consider renaming variable",   # UNSPECIFIED_INFO
                    "commit_id": "abc123abc123abc123abc123abc123abc123abcd12",  # current
                    "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r99",
                }]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "body": "**<sub><sub>![P1](https://img.shields.io/badge/P1-orange) fix this P1 issue**",
                    "path": "scripts/local/run_temp_worktree_execution.py",
                    "line": 1821,
                    "commit_id": "oldoldold",   # stale
                    "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                }]),
                "pulls/320/reviews": gh_reply([]),
                "graphql": gh_reply({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"nodes": [{
                        "id": "PRRT_stale_p1_resolved",
                        "isResolved": True,
                        "isOutdated": True,
                        "comments": {"nodes": [{
                            "databaseId": 1,
                            "url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                        }]},
                    }]},
                }}}}),
            },
            gh_pr_view_oid="abc123abc123abc123abc123abc123abc123abcd12",
        )
        self.assertEqual(rc, crc.EXIT_CLEAN)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_CLEAN")
        # Resolved stale P1 in resolved_stale_blockers
        self.assertEqual(len(data.get("resolved_stale_blockers", [])), 1)
        # Current-head UNSPECIFIED_INFO not blocking
        self.assertEqual(len(data.get("blockers", [])), 0)
        self.assertEqual(len(data.get("stale_blockers", [])), 0)

    def test_unresolved_stale_p1_still_returns_inconclusive(self):
        """
        Unresolved stale P1 (thread_resolved=False) => INCONCLUSIVE.
        This confirms we did NOT weaken the unresolved-stale-path.
        """
        rc, data = self._run(
            {
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "body": "**<sub><sub>![P1](https://img.shields.io/badge/P1-orange) fix this P1 issue**",
                    "path": "scripts/local/run_temp_worktree_execution.py",
                    "line": 1821,
                    "commit_id": "oldoldold",
                    "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                }]),
                "pulls/320/reviews": gh_reply([]),
                "graphql": gh_reply({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"nodes": [{
                        "id": "PRRT_stale_unresolved",
                        "isResolved": False,   # NOT resolved — unchanged behavior
                        "isOutdated": True,
                        "comments": {"nodes": [{
                            "databaseId": 1,
                            "url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                        }]},
                    }]},
                }}}}),
            },
            gh_pr_view_oid="abc123abc123abc123abc123abc123abc123abcd12",
        )
        # Unresolved stale P1 => INCONCLUSIVE (not BLOCKED, not CLEAN)
        self.assertEqual(rc, crc.EXIT_INCONCLUSIVE)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_INCONCLUSIVE")
        self.assertEqual(len(data.get("stale_blockers", [])), 1)
        self.assertEqual(data["stale_blockers"][0]["severity"], "P1")
        self.assertEqual(data["stale_blockers"][0]["is_stale_head"], True)
        self.assertEqual(data["stale_blockers"][0]["thread_resolved"], False)
        self.assertEqual(len(data.get("blockers", [])), 0)
        self.assertEqual(len(data.get("resolved_stale_blockers", [])), 0)

    def test_resolved_stale_p1_plus_unresolved_stale_p1_returns_inconclusive(self):
        """
        Resolved stale P1 + unresolved stale P1 => INCONCLUSIVE.
        The unresolved stale P1 keeps the gate inconclusive even when
        another stale P1 is resolved.
        """
        rc, data = self._run(
            {
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([
                    {
                        "user": {"login": "chatgpt-codex-connector[bot]"},
                        "body": "**<sub><sub>![P1](https://img.shields.io/badge/P1-orange) fix this P1 issue**",
                        "path": "scripts/local/run_temp_worktree_execution.py",
                        "line": 1821,
                        "commit_id": "oldoldold",   # stale #1
                        "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                    },
                    {
                        "user": {"login": "chatgpt-codex-connector[bot]"},
                        "body": "**<sub><sub>![P1](https://img.shields.io/badge/P1-orange) another P1 issue**",
                        "path": "scripts/local/run_temp_worktree_execution.py",
                        "line": 2000,
                        "commit_id": "anotherold",   # stale #2
                        "html_url": "https://github.com/OWNER/REPO/pull/1#discussion_r2",
                    },
                ]),
                "pulls/320/reviews": gh_reply([]),
                "graphql": gh_reply({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"nodes": [
                        {
                            "id": "PRRT_stale1_resolved",
                            "isResolved": True,   # resolved — goes to resolved_stale_blockers
                            "isOutdated": True,
                            "comments": {"nodes": [{
                                "databaseId": 1,
                                "url": "https://github.com/OWNER/REPO/pull/1#discussion_r1",
                            }]},
                        },
                        {
                            "id": "PRRT_stale2_unresolved",
                            "isResolved": False,  # NOT resolved — keeps gate INCONCLUSIVE
                            "isOutdated": True,
                            "comments": {"nodes": [{
                                "databaseId": 2,
                                "url": "https://github.com/OWNER/REPO/pull/1#discussion_r2",
                            }]},
                        },
                    ]},
                }}}}),
            },
            gh_pr_view_oid="abc123abc123abc123abc123abc123abc123abcd12",
        )
        # Still INCONCLUSIVE due to unresolved stale P1
        self.assertEqual(rc, crc.EXIT_INCONCLUSIVE)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_INCONCLUSIVE")
        # Unresolved stale P1 in stale_blockers
        self.assertEqual(len(data.get("stale_blockers", [])), 1)
        self.assertEqual(data["stale_blockers"][0]["thread_resolved"], False)
        # Resolved stale P1 in resolved_stale_blockers
        self.assertEqual(len(data.get("resolved_stale_blockers", [])), 1)
        self.assertEqual(
            data["resolved_stale_blockers"][0]["thread_resolved"], True)
        # No current-head blockers
        self.assertEqual(len(data.get("blockers", [])), 0)

    def test_resolved_stale_blockers_field_in_json_output(self):
        """
        resolved_stale_blockers field is present in JSON output even when empty.
        Confirms the new field is always included for forward-compatibility.
        """
        rc, data = self._run(
            {
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([]),
                "pulls/320/reviews": gh_reply([]),
            },
            gh_pr_view_oid="abc123abc123abc123abc123abc123abc123abcd12",
        )
        self.assertEqual(rc, crc.EXIT_CLEAN)
        self.assertIn("resolved_stale_blockers", data)
        self.assertIsInstance(data["resolved_stale_blockers"], list)
        self.assertEqual(len(data["resolved_stale_blockers"]), 0)
        self.assertIn("resolved_stale_blockers", data["stale_findings_summary"])

    def test_mismatch_output_includes_resolved_stale_blockers_field(self):
        """
        Mismatch output path (head SHA mismatch) includes resolved_stale_blockers
        field to maintain schema consistency with the normal output path.
        Regression test for the PR #326 post-merge audit finding.
        """
        rc, data = self._run(
            {
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([]),
                "pulls/320/reviews": gh_reply([]),
            },
            # Live head differs from reported head -> mismatch path
            gh_pr_view_oid="xyz789xyz789xyz789xyz789xyz789xyz789xy0123",
        )
        self.assertEqual(rc, crc.EXIT_INCONCLUSIVE)
        self.assertTrue(data.get("head_sha_mismatch"))
        # Schema consistency: resolved_stale_blockers must be present
        self.assertIn("resolved_stale_blockers", data)
        self.assertIsInstance(data["resolved_stale_blockers"], list)
        self.assertEqual(len(data["resolved_stale_blockers"]), 0)

    def test_mismatch_output_includes_stale_findings_summary_resolved_stale_blockers(self):
        """
        Mismatch output path includes stale_findings_summary.resolved_stale_blockers
        to maintain schema consistency. Regression test for PR #326 audit.
        """
        rc, data = self._run(
            {
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([]),
                "pulls/320/reviews": gh_reply([]),
            },
            gh_pr_view_oid="xyz789xyz789xyz789xyz789xyz789xyz789xy0123",
        )
        self.assertEqual(rc, crc.EXIT_INCONCLUSIVE)
        self.assertTrue(data.get("head_sha_mismatch"))
        self.assertIn("stale_findings_summary", data)
        self.assertIn("resolved_stale_blockers", data["stale_findings_summary"])
        self.assertEqual(data["stale_findings_summary"]["resolved_stale_blockers"], 0)

    def test_blocked_output_includes_resolved_stale_blockers(self):
        """
        BLOCKED output path includes resolved_stale_blockers field.
        Confirms schema consistency when gate is BLOCKED.
        """
        rc, data = self._run(
            {
                "issues/320/comments": gh_reply([]),
                "pulls/320/comments": gh_reply([{
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "body": "P1: something is wrong",
                    "state": "",
                    "path": "scripts/test.py",
                    "line": 10,
                    "commit_id": "abc123abc123abc123abc123abc123abc123abcd12",
                    "html_url": "https://github.com/OWNER/REPO/pull/1",
                }]),
                "pulls/320/reviews": gh_reply([]),
            },
            gh_pr_view_oid="abc123abc123abc123abc123abc123abc123abcd12",
        )
        self.assertEqual(rc, crc.EXIT_BLOCKED)
        self.assertEqual(data.get("status"), "REVIEW_COMMENTS_BLOCKED")
        # Schema consistency: resolved_stale_blockers present even when blocked
        self.assertIn("resolved_stale_blockers", data)
        self.assertIsInstance(data["resolved_stale_blockers"], list)
        self.assertIn("resolved_stale_blockers", data.get("stale_findings_summary", {}))


class TestGhPrViewREST:
    """
    Tests for the gh_pr_view REST fix.

    Root cause: gh_pr_view originally used `gh pr view`, which invokes
    git status internally. When run from /tmp (no .git), it fails with
      "failed to run git: fatal: not a git repository"

    Fix: gh_pr_view now uses `gh api repos/{repo}/pulls/{n}` which does not
    require a git repository in the caller's cwd.

    These tests verify the REST-based implementation is called correctly
    and returns normalised data.
    """

    def test_gh_pr_view_uses_rest_endpoint(self):
        """gh_pr_view must call repos/{repo}/pulls/{pr_number}, not gh pr view."""
        captured = []

        def capture_run(cmd, **kwargs):
            captured.append(cmd)
            return FakeResult(stdout=json.dumps({
                "sha": "abc123abc123abc123abc123abc123abc123abcd12",
                "state": "OPEN",
                "url": "https://github.com/OWNER/REPO/pull/1",
            }))

        with mock.patch.object(subprocess, "run", capture_run):
            ok, data, err = crc.gh_pr_view("OWNER/REPO", 1)

        assert ok, f"gh_pr_view failed: {err}"
        assert "repos/OWNER/REPO/pulls/1" in " ".join(captured[0]), \
            f"Expected repos endpoint, got: {captured[0]}"
        assert "pr" not in " ".join(captured[0]).lower() or "pulls" in " ".join(captured[0]), \
            f"gh pr view must not be called: {captured[0]}"

    def test_gh_pr_view_normalises_sha_to_headRefOid(self):
        """REST returns sha under .head.sha; gh_pr_view must map it to headRefOid."""
        def fake_run(cmd, **kwargs):
            return FakeResult(stdout=json.dumps({
                "sha": "abc123abc123abc123abc123abc123abc123abcd12",
                "state": "OPEN",
                "url": "https://github.com/OWNER/REPO/pull/1",
            }))

        with mock.patch.object(subprocess, "run", fake_run):
            ok, data, err = crc.gh_pr_view("OWNER/REPO", 1)

        assert ok, f"gh_pr_view failed: {err}"
        assert data.get("headRefOid") == "abc123abc123abc123abc123abc123abc123abcd12", \
            f"Expected headRefOid mapped from sha, got: {data}"
        assert data.get("state") == "OPEN"
        assert data.get("url") == "https://github.com/OWNER/REPO/pull/1"

    def test_gh_pr_view_works_from_tmp_dir_no_git(self):
        """gh_pr_view must not require git in cwd; uses REST API only."""
        # Simulate /tmp by patching subprocess.run to succeed (no git involved)
        def fake_run(cmd, **kwargs):
            # gh api repos/.../pulls/{n} has no git dependency
            return FakeResult(stdout=json.dumps({
                "sha": "abc123abc123abc123abc123abc123abc123abcd12",
                "state": "OPEN",
                "url": "https://github.com/OWNER/REPO/pull/1",
            }))

        with mock.patch.object(subprocess, "run", fake_run):
            ok, data, err = crc.gh_pr_view("OWNER/REPO", 1)

        assert ok, f"gh_pr_view failed from non-git cwd: {err}"
        # If this had used `gh pr view`, it would have called git and failed

    def test_gh_pr_view_error_returncode_propagates(self):
        """Non-zero exit from gh api must return error tuple, not raise."""
        def fake_run(cmd, **kwargs):
            return FakeResult(
                returncode=1,
                stderr="Not Found",
                stdout="",
            )

        with mock.patch.object(subprocess, "run", fake_run):
            ok, data, err = crc.gh_pr_view("OWNER/REPO", 999)

        assert not ok, "Expected failure for returncode=1"
        assert "gh api returned 1" in err, f"Expected error prefix, got: {err}"

    def test_gh_pr_view_non_json_stderr_propagates(self):
        """Non-JSON stdout must return error tuple."""
        def fake_run(cmd, **kwargs):
            return FakeResult(
                returncode=0,
                stdout="this is not json",
                stderr="",
            )

        with mock.patch.object(subprocess, "run", fake_run):
            ok, data, err = crc.gh_pr_view("OWNER/REPO", 1)

        assert not ok, "Expected failure for non-JSON stdout"
        assert "non-JSON" in err, f"Expected non-JSON error, got: {err}"


import tempfile


class TestCodexIgnoreSafeguard(unittest.TestCase):
    """Regression tests for the gate-policy safeguard added in PR #405 repair.

    The previous policy invoked the gate with
    --ignore-users "chatgpt-codex-connector[bot]" which silently
    filtered out every Codex finding, making the gate green while
    18 unresolved P1/P2 Codex findings remained actionable. The
    repair introduces a script-level safeguard: ignoring the Codex
    bot without the explicit AED_ALLOW_CODEX_IGNORE=1 env override
    must fail closed.
    """

    def test_codex_p1_classified_as_finding(self):
        """A Codex-authored P1 must reach the findings list when the
        author is not in ignore_users. (The previous bug was that
        --ignore-users contained codex, so this never happened.)"""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "<sub><sub>![P1 Badge]</sub></sub> Reject malformed phase values",
            "state": "",
        }
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1, "Codex P1 must be classified as a finding")
        self.assertEqual(got[0]["severity"], "P1")

    def test_codex_p2_classified_as_finding(self):
        """A Codex-authored P2 must reach the findings list."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "<sub><sub>![P2 Badge]</sub></sub> Allow documented optional checkpoint fields",
            "state": "",
        }
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["severity"], "P2")

    def test_codex_coordination_still_skipped_via_classification(self):
        """A Codex coordination comment must not be classified as a
        finding. This confirms the existing is_coordination_comment
        logic is still the right place to handle codex noise — not
        --ignore-users. The body uses 'Re-requesting' which is a
        coordination marker; it does NOT contain a P-bare or badge
        that would exempt it from the coordination skip."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting Codex review on 3982ee6. The active P1 finding has been addressed.",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(got, [], "Codex coordination must still be skipped via classification, not ignore-users")

    def test_main_refuses_codex_ignore_without_env_override(self):
        """The script must exit non-zero (fail closed) when --ignore-users
        contains the codex bot login and AED_ALLOW_CODEX_IGNORE is not '1'."""
        with tempfile.TemporaryDirectory() as td:
            out_json = os.path.join(td, "status.json")
            out_md = os.path.join(td, "status.md")
            argv = [
                "check_pr_review_comments.py",
                "--repo", "OWNER/REPO",
                "--pr-number", "1",
                "--reported-head-sha", "deadbeef" * 5,
                "--ignore-users", "chatgpt-codex-connector[bot]",
                "--output-json", out_json,
                "--output-md", out_md,
            ]
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AED_ALLOW_CODEX_IGNORE", None)
                with mock.patch.object(sys, "argv", argv):
                    with mock.patch.object(crc, "gh_api", return_value=(True, [], "")):
                        with mock.patch.object(crc, "gh_graphql_review_threads", return_value=(True, [], "")):
                            with mock.patch.object(crc, "gh_pr_view", return_value=(True, {"headRefOid": "deadbeef" * 5, "state": "OPEN", "url": ""}, "")):
                                rc = crc.main()
        self.assertEqual(rc, 1, "Codex ignore without AED_ALLOW_CODEX_IGNORE must fail closed with rc=1")

    def test_main_allows_codex_ignore_with_env_override(self):
        """With AED_ALLOW_CODEX_IGNORE=1, the script must proceed (and
        log a warning to stderr). The pre-existing ignore behavior is
        preserved under the explicit opt-in."""
        with tempfile.TemporaryDirectory() as td:
            out_json = os.path.join(td, "status.json")
            out_md = os.path.join(td, "status.md")
            argv = [
                "check_pr_review_comments.py",
                "--repo", "OWNER/REPO",
                "--pr-number", "1",
                "--reported-head-sha", "deadbeef" * 5,
                "--ignore-users", "chatgpt-codex-connector[bot]",
                "--output-json", out_json,
                "--output-md", out_md,
            ]
            with mock.patch.dict(os.environ, {"AED_ALLOW_CODEX_IGNORE": "1"}, clear=False):
                with mock.patch.object(sys, "argv", argv):
                    with mock.patch.object(crc, "gh_api", return_value=(True, [], "")):
                        with mock.patch.object(crc, "gh_graphql_review_threads", return_value=(True, [], "")):
                            with mock.patch.object(crc, "gh_pr_view", return_value=(True, {"headRefOid": "deadbeef" * 5, "state": "OPEN", "url": ""}, "")):
                                rc = crc.main()
        self.assertEqual(rc, 0, "With AED_ALLOW_CODEX_IGNORE=1, gate must run to completion (rc=0 for empty-data CLEAN)")

    def test_main_does_not_refuse_non_codex_ignore(self):
        """Sanity check: a non-codex ignore user must not trigger the safeguard."""
        with tempfile.TemporaryDirectory() as td:
            out_json = os.path.join(td, "status.json")
            out_md = os.path.join(td, "status.md")
            argv = [
                "check_pr_review_comments.py",
                "--repo", "OWNER/REPO",
                "--pr-number", "1",
                "--reported-head-sha", "deadbeef" * 5,
                "--ignore-users", "some-other-user",
                "--output-json", out_json,
                "--output-md", out_md,
            ]
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AED_ALLOW_CODEX_IGNORE", None)
                with mock.patch.object(sys, "argv", argv):
                    with mock.patch.object(crc, "gh_api", return_value=(True, [], "")):
                        with mock.patch.object(crc, "gh_graphql_review_threads", return_value=(True, [], "")):
                            with mock.patch.object(crc, "gh_pr_view", return_value=(True, {"headRefOid": "deadbeef" * 5, "state": "OPEN", "url": ""}, "")):
                                rc = crc.main()
        self.assertEqual(rc, 0, "Non-codex ignore must not be blocked by the safeguard")


if __name__ == "__main__":
    unittest.main()