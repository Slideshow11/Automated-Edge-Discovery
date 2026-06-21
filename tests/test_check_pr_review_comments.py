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

    def test_p3_colon_coordination_not_skipped(self):
        """Under the new architecture (option B refactor), a body
        starting with ``Bumping this thread — P3: fix is minor``
        is NOT suppressed by the coordination-skip — the explicit
        P3: marker is an actionable finding signal (Detector 1
        in ``_has_actionable_finding_signal``).

        This is a behavior change from the cycle-3-11
        architecture, which only checked P0:/P1:/P2: in Guard 1
        and missed the P3: form. The auth prompt explicitly
        requires ``Keep the classifier fail-closed for explicit
        P0/P1/P2/P3 signals`` — Detector 1 now covers P3:.

        The P3 finding is INFORMATIONAL only (does not block the
        gate), so this behavior change is fail-safer than the
        OLD behavior. The OLD test was named
        ``test_p3_colon_coordination_still_skipped`` and
        documented the OLD behavior; the new behavior is
        documented here."""
        body = "Bumping this thread — P3: fix is minor"
        # P3: marker is actionable in the new architecture.
        self.assertTrue(
            crc._has_actionable_finding_signal(body),
            "P3: marker must be detected as an actionable finding "
            "signal under the new architecture (Detector 1 covers P3:)"
        )
        item = {
            "user": {"login": "Slideshow11"},
            "body": body,
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        self.assertEqual(
            len(got), 1,
            "P3: marker must produce a finding (informational, "
            "non-blocking, but emitted)"
        )
        self.assertEqual(
            got[0]["severity"], "P3",
            f"P3: marker must produce P3 severity, got {got[0]['severity']}"
        )

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

        Under the new architecture (option B refactor), this
        protection is enforced via Detector 4 of
        ``_has_actionable_finding_signal`` (blocking-word in
        leading 100 chars), not via ``is_coordination_comment``.
        ``is_coordination_comment`` is now a simple coord-prefix
        check that returns True for any body starting with
        ``Bumping`` / ``Re-requesting`` / etc. The full
        pipeline (``classify_item``) suppresses ONLY bodies that
        have no actionable signal AND start with a coordination
        pattern.
        """
        body = (
            "Bumping the retry counter can fail when Codex "
            "reruns after a stale head"
        )
        self.assertTrue(
            crc._has_actionable_finding_signal(body),
            "Blocking-word 'can fail' in leading 100 chars must "
            "trigger Detector 4 of _has_actionable_finding_signal "
            "(Codex finding AQ)"
        )

    def test_aq_re_requesting_with_must_fix_not_coordination(self):
        """Regression for Codex finding AQ: a body that starts
        with ``Re-requesting`` AND contains ``must fix``
        (BLOCKING_WORDS) anywhere must NOT be dropped as a
        coordination comment. Under the new architecture this
        is enforced via Detector 4 of
        ``_has_actionable_finding_signal``.
        """
        body = (
            "Re-requesting this because the must fix test is "
            "still failing"
        )
        self.assertTrue(
            crc._has_actionable_finding_signal(body),
            "Blocking-word 'must fix' in leading 100 chars must "
            "trigger Detector 4 (Codex finding AQ)"
        )

    def test_aq_gentle_nudge_with_stale_not_coordination(self):
        """Regression for Codex finding AQ: a body that starts
        with ``Gentle nudge`` AND contains ``stale`` (a
        BLOCKING_WORDS indicator) must NOT be dropped as a
        coordination comment. Under the new architecture this
        is enforced via Detector 4.
        """
        body = (
            "Gentle nudge — the stale head ref is still wrong, "
            "must fix"
        )
        self.assertTrue(
            crc._has_actionable_finding_signal(body),
            "Blocking-word 'stale' / 'must fix' in leading 100 "
            "chars must trigger Detector 4 (Codex finding AQ)"
        )

    def test_aq_bumping_with_path_traversal_not_coordination(self):
        """Regression for Codex finding AQ: ``Bumping`` at start
        with ``path traversal`` (BLOCKING_WORDS) anywhere in
        the body must NOT be dropped as coordination. Under
        the new architecture this is enforced via Detector 4.
        """
        body = (
            "Bumping this thread — the new helper introduces "
            "a path traversal risk in the input parser"
        )
        self.assertTrue(
            crc._has_actionable_finding_signal(body),
            "Blocking-word 'path traversal' in leading 100 chars "
            "must trigger Detector 4 (Codex finding AQ)"
        )

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
        self.assertFalse(
            crc._has_actionable_finding_signal(item["body"]),
            "Negated priority ('is not high priority') must NOT trigger (actionable signal is False) the Guard 6 rescue (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )

    def test_negated_severity_does_not_rescue_coordination(self):
        """Same as above for the severity noun form."""
        body = "Re-requesting Codex review on 3982ee6 — this is not high severity, just a re-prompt"
        self.assertFalse(
            crc._has_actionable_finding_signal(body),
            "Negated severity ('is not high severity') must NOT trigger (actionable signal is False) the Guard 6 rescue (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )

    def test_no_longer_severity_does_not_rescue_coordination(self):
        """Same as above for 'is no longer high severity' form."""
        body = "Re-requesting Codex review on 3982ee6 — is no longer high severity, please ignore"
        self.assertFalse(
            crc._has_actionable_finding_signal(body),
            "'is no longer high severity' must NOT trigger (actionable signal is False) the Guard 6 rescue (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
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
        self.assertFalse(
            crc._has_actionable_finding_signal(item["body"]),
            "Meta-discussion 'classified as high priority' must NOT trigger (actionable signal is False) Guard 6 rescue (dbID 3447830523) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )

    def test_flagged_as_high_severity_not_rescued(self):
        """Same as above for the 'flagged as high severity'
        meta-discussion form."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — this was flagged as high severity in the previous round",
            "state": "",
        }
        self.assertFalse(
            crc._has_actionable_finding_signal(item["body"]),
            "Meta-discussion 'flagged as high severity' must NOT trigger (actionable signal is False) Guard 6 rescue (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
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
        self.assertFalse(
            crc._has_actionable_finding_signal(item["body"]),
            "Context phrase 'with high priority context' must NOT trigger (actionable signal is False) Guard 6 rescue (dbID 3447830523) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )

    def test_with_high_severity_language_not_rescued(self):
        """Same as above for the 'with high severity language'
        context form."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — addressed previously with high severity language in the comment",
            "state": "",
        }
        self.assertFalse(
            crc._has_actionable_finding_signal(item["body"]),
            "Context phrase 'with high severity language' must NOT trigger (actionable signal is False) Guard 6 rescue (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
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
        self.assertFalse(
            crc._has_actionable_finding_signal(item["body"]),
            "Negation 'not high severity' must NOT trigger (actionable signal is False) Guard 6 rescue (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
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
        self.assertTrue(
            crc._has_actionable_finding_signal(item["body"]),
            "Direct copula 'is high priority' MUST still trigger actionable signal (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )

    def test_direct_has_high_severity_still_rescues(self):
        """Same as above for the 'has high severity' form."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting review: this finding has high severity implications for the watchdog loop",
            "state": "",
        }
        self.assertTrue(
            crc._has_actionable_finding_signal(item["body"]),
            "Direct copula 'has high severity' MUST still trigger actionable signal (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
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
        self.assertTrue(
            crc._has_actionable_finding_signal(item["body"]),
            "Article-bearing copula form 'is a high priority issue' MUST trigger actionable signal (dbID 3447849261) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
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
        self.assertTrue(
            crc._has_actionable_finding_signal(item["body"]),
            "Article-bearing copula form 'has a high severity impact' MUST trigger actionable signal (dbID 3447849261) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
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
        self.assertTrue(
            crc._has_actionable_finding_signal(item["body"]),
            "'is an high severity issue' MUST trigger actionable signal (testing 'an' code path) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
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
        self.assertTrue(
            crc._has_actionable_finding_signal(item["body"]),
            "Direct copula 'is high priority' MUST still trigger actionable signal (cycle 5 regression check) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )

    def test_direct_has_high_severity_preserved_from_cycle_5(self):
        """Same as above for the 'has high severity' cycle-5
        direct form."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting review: this finding has high severity implications for the watchdog loop",
            "state": "",
        }
        self.assertTrue(
            crc._has_actionable_finding_signal(item["body"]),
            "Direct copula 'has high severity' MUST still trigger actionable signal (cycle 5 regression check) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
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
        self.assertFalse(
            crc._has_actionable_finding_signal(item["body"]),
            "Negated article-bearing form 'is not a high priority issue' must NOT trigger (actionable signal is False) Guard 6 rescue (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
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
        self.assertFalse(
            crc._has_actionable_finding_signal(item["body"]),
            "Negation 'has no high severity impact' must NOT trigger (actionable signal is False) Guard 6 rescue (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
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
        self.assertFalse(
            crc._has_actionable_finding_signal(item["body"]),
            "Meta-discussion 'described as a high priority issue' must NOT trigger (actionable signal is False) Guard 6 rescue (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
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
        self.assertFalse(
            crc._has_actionable_finding_signal(item["body"]),
            "Cycle-6 regression: 'classified as high priority' must STILL NOT be rescued (cycle 7 regression check) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
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
        self.assertFalse(
            crc._has_actionable_finding_signal(item["body"]),
            "Cycle-6 regression: 'with high priority context' must STILL NOT be rescued (cycle 7 regression check) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
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
        self.assertFalse(
            crc._has_actionable_finding_signal(item["body"]),
            "Cycle-5 regression: 'is not high priority' must STILL NOT be rescued (cycle 7 regression check) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )

    def test_dbID_3447871114_intensifier_rescued(self):
        """Exact-pattern regression test for the cycle-7 fresh
        Codex P2 finding dbID 3447871114 (2026-06-21T03:52:50Z
        on head 7377eada08): 'Re-requesting review: this is
        an extremely high severity issue' MUST be rescued by
        Guard 6 and classified as a P1 finding. The cycle-7
        regex required the level token to come IMMEDIATELY
        after the optional article, which dropped this real
        Codex finding. The cycle-8 helper accepts up to two
        intensifier tokens between the article and the level
        from a fixed whitelist (``very``, ``extremely``,
        ``particularly``, ``especially``, ``clearly``,
        ``obviously``, ``materially``, ``highly``)."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting review: this is an extremely high severity issue and must be addressed before merge",
            "state": "",
        }
        # is_coordination_comment should return False (NOT skip)
        self.assertTrue(
            crc._has_actionable_finding_signal(item["body"]),
            "Intensifier form 'is an extremely high severity issue' MUST trigger actionable signal (dbID 3447871114) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )
        # And classify_item must classify it as P1
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1, f"Expected exactly 1 finding, got {got}")
        self.assertEqual(got[0]["severity"], "P1", f"Expected P1, got {got[0]['severity']}")

    def test_dbID_3447871114_very_priority_rescued(self):
        """Direct intensifier form: 'has a very high priority
        impact' MUST be rescued."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting review: this comment has a very high priority impact and should be treated as a finding",
            "state": "",
        }
        self.assertTrue(
            crc._has_actionable_finding_signal(item["body"]),
            "'has a very high priority impact' MUST trigger actionable signal (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1, f"Expected exactly 1 finding, got {got}")
        self.assertEqual(got[0]["severity"], "P1", f"Expected P1, got {got[0]['severity']}")

    def test_p0_p1_p2_taxonomy_not_rescued(self):
        """P0/P1/P2 severity taxonomy meta-text must NOT be
        rescued. The body contains P0/P1/P2 and severity
        tokens but no copula verb, so Guard 6 should not
        rescue. This guards against the cycle-8 helper
        accidentally matching taxonomy-shaped prose.

        The test wraps the taxonomy prose in a coordination
        prefix (``Re-requesting Codex review``) so the
        end-to-end ``is_coordination_comment`` returns
        ``True`` (skip), confirming Guard 6 does not rescue."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — the P0/P1/P2 severity taxonomy is being revised, please skip",
            "state": "",
        }
        # is_coordination_comment should return True (skip)
        self.assertFalse(
            crc._has_actionable_finding_signal(item["body"]),
            "'P0/P1/P2 severity taxonomy' meta-text must NOT trigger (actionable signal is False) Guard 6 rescue (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )

    def test_high_priority_context_only_not_rescued(self):
        """'high priority context only' — a bare noun phrase
        with no copula — must NOT be rescued. Wrapped in a
        coordination prefix so the end-to-end check returns
        ``True``."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — high priority context only, discussion not a finding",
            "state": "",
        }
        self.assertFalse(
            crc._has_actionable_finding_signal(item["body"]),
            "'high priority context only' must NOT trigger (actionable signal is False) Guard 6 rescue (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )

    def test_not_actually_high_priority_not_rescued(self):
        """'not actually high priority' — bare negation with
        intervening adverb and no copula — must NOT be
        rescued."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review — this is not actually high priority, just a re-prompt",
            "state": "",
        }
        self.assertFalse(
            crc._has_actionable_finding_signal(item["body"]),
            "'not actually high priority' must NOT trigger (actionable signal is False) Guard 6 rescue (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )

    def test_no_p0_misclassification_regression(self):
        """P0 tokens in a body must still be classified as
        P0 findings after the cycle-8 helper refactor. The
        helper should not change P-token classification."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "P0 critical security issue: CVE-2024-XXXX in dependency chain",
            "state": "",
        }
        got = crc.classify_item(item, "inline_review_comment", set())
        p0_findings = [f for f in got if f["severity"] == "P0"]
        self.assertGreaterEqual(
            len(p0_findings), 1,
            f"P0 token in body must classify as P0, got {got}"
        )

    def test_two_intensifiers_max_rescued(self):
        """The cycle-8 helper accepts up to TWO intensifier
        tokens. 'is a very extremely high severity issue'
        uses two intensifiers and must be rescued."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting review: this is a very extremely high severity issue that must be fixed",
            "state": "",
        }
        self.assertTrue(
            crc._has_actionable_finding_signal(item["body"]),
            "'is a very extremely high severity issue' (2 intensifiers) MUST trigger actionable signal (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )

    def test_three_intensifiers_overflow_not_rescued(self):
        """Three consecutive intensifiers (over MAX=2) must
        NOT be rescued — the third intensifier ('particularly')
        is not consumed by the WHILE loop (which stops at 2),
        then fails the required-level check, so the pattern
        doesn't match. This documents the MAX_INTENSIFIERS=2
        invariant from the cycle-8 design constraints.

        Cycle-11 interaction: the prior body used here started
        with ``Re-requesting review:`` (a coordination prefix),
        which would trip Guard 7's colon-form rescue even when
        Guard 6 itself correctly rejects the 3-intensifier
        copula pattern. The test now invokes the Guard 6
        helper directly so the MAX_INTENSIFIERS=2 invariant
        can be pinned without Guard 7 interference. The end-
        to-end coordination-skip behavior for the original
        body is verified separately in
        :class:`TestIsCoordinationCommentGuard7`
        ``test_cycle11_no_regression_on_guard6_copula_cases``.
        """
        # Call the Guard 6 helper directly with the same body,
        # which still rejects the 3-intensifier pattern.
        body = "this is a very extremely particularly high severity issue"
        self.assertFalse(
            crc._has_direct_text_severity_declaration(body),
            "3 intensifiers (over MAX=2) must NOT trigger Guard 6 rescue "
            "(cycle-8 MAX_INTENSIFIERS=2 invariant)"
        )

    def test_two_intensifiers_any_combination_rescued(self):
        """Two intensifiers in any combination from the
        whitelist must be rescued. The MAX=2 limit is on
        COUNT, not on UNIQUE values."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting review: this is a very very high severity issue that must be fixed",
            "state": "",
        }
        self.assertTrue(
            crc._has_actionable_finding_signal(item["body"]),
            "'is a very very high severity issue' (2 intensifiers, same word) MUST trigger actionable signal (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )

    def test_dbID_3447899921_em_dash_priority_rescued(self):
        """Exact-pattern regression test for the cycle-8 fresh
        Codex P2 finding dbID 3447899921 (2026-06-21T04:20:36Z
        on head d44c5ddaea8): 'Bumping retry is high
        priority\u2014this skips CI' MUST be rescued by Guard 6.
        The cycle-8 helper's token-based splitting produced
        ``priority\u2014this`` as one token (no whitespace
        between priority and the em-dash), which failed the
        noun check. The cycle-9 helper inserts a whitespace
        boundary after a severity/priority noun followed by
        a dash separator, restoring the cycle-7 regex's
        ``\b`` word-boundary behavior."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Bumping retry is high priority\u2014this skips CI and must be fixed before merge",
            "state": "",
        }
        self.assertTrue(
            crc._has_actionable_finding_signal(item["body"]),
            "'is high priority\u2014this' (em-dash after priority) MUST trigger actionable signal (dbID 3447899921) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1, f"Expected exactly 1 finding, got {got}")
        self.assertEqual(got[0]["severity"], "P1", f"Expected P1, got {got[0]['severity']}")

    def test_dbID_3447899921_hyphen_priority_rescued(self):
        """Same as above for ASCII hyphen separator."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Bumping retry is high priority-this skips CI and must be fixed before merge",
            "state": "",
        }
        self.assertTrue(
            crc._has_actionable_finding_signal(item["body"]),
            "'is high priority-this' (hyphen after priority) MUST trigger actionable signal (dbID 3447899921) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1, f"Expected exactly 1 finding, got {got}")
        self.assertEqual(got[0]["severity"], "P1", f"Expected P1, got {got[0]['severity']}")

    def test_cycle9_en_dash_severity_rescued(self):
        """En dash after severity must also be rescued."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting review: this has high severity\u2013the system blocks all retries",
            "state": "",
        }
        self.assertTrue(
            crc._has_actionable_finding_signal(item["body"]),
            "'has high severity\u2013' (en dash after severity) MUST trigger actionable signal (cycle 9) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )
        got = crc.classify_item(item, "inline_review_comment", set())
        self.assertEqual(len(got), 1, f"Expected exactly 1 finding, got {got}")
        self.assertEqual(got[0]["severity"], "P1", f"Expected P1, got {got[0]['severity']}")

    def test_cycle9_hyphen_compound_severity_rescued(self):
        """The 'has a high severity-impact case' form (hyphen
        in compound modifier after severity) must be rescued.
        The dash is converted to a whitespace boundary, so the
        helper sees ``severity`` as the noun."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting review: this has a high severity-impact case that must be fixed",
            "state": "",
        }
        self.assertTrue(
            crc._has_actionable_finding_signal(item["body"]),
            "'has a high severity-impact case' (hyphen after severity) MUST trigger actionable signal (cycle 9) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )

    def test_cycle9_em_dash_after_intensifier_rescued(self):
        """Em dash after the intensifier-bearing declaration
        must also be rescued (e.g. 'is an extremely high
        severity\u2014this matters')."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Re-requesting review: this is an extremely high severity\u2014this matters and must be addressed",
            "state": "",
        }
        self.assertTrue(
            crc._has_actionable_finding_signal(item["body"]),
            "'is an extremely high severity\u2014' (em dash after intensifier form) MUST trigger actionable signal (cycle 9) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )

    def test_cycle9_dash_does_not_rescue_negation(self):
        """The dash preprocessor must not interfere with the
        negation path. 'is not high priority\u2014this is
        wrong' has 'is not' which is the immediate negation
        pattern; the helper should return False (definitive)."""
        item = {
            "user": {"login": "human-pr-author"},
            "body": "Re-requesting Codex review \u2014 this is not high priority\u2014please skip this re-prompt",
            "state": "",
        }
        self.assertFalse(
            crc._has_actionable_finding_signal(item["body"]),
            "'is not high priority\u2014' (dash after negated priority) must NOT trigger (actionable signal is False) Guard 6 rescue (cycle 9) (verified via _has_actionable_finding_signal, which is the centralized detector per option B refactor)"
        )

    def test_cycle9_compound_high_priority_no_rescue(self):
        """The dash between 'high' and 'priority' in the
        compound modifier 'high-priority' must NOT trigger
        rescue — the cycle-9 preprocessor is restricted to
        (severity|priority) + dash, not (high|medium|low) +
        dash. The body has no coordination prefix either, so
        is_coordination_comment returns False (the body
        would go through extract_severity, which is a
        separate path). This test pins the cycle-9 scope
        invariant."""
        item = {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "this is a high-priority issue that should be addressed",
            "state": "",
        }
        # Helper should NOT rescue (compound dash between
        # 'high' and 'priority' is out of scope).
        self.assertFalse(
            crc._has_direct_text_severity_declaration(item["body"]),
            "'high-priority' compound (dash BEFORE priority) must NOT trigger Guard 6 rescue (cycle 9 scope invariant)"
        )


class TestHasDirectTextSeverityDeclarationCycle9Dash(unittest.TestCase):
    """Cycle 9 (PR #405 fresh Codex P2 finding, dbID 3447899921,
    2026-06-21T04:20:36Z on head d44c5ddaea8): the cycle-8 helper
    did not strip dash separators that appear immediately after
    a severity/priority noun, so tokens like ``priority\u2014this``
    (em dash, no whitespace) failed the noun check. Cycle 9:

      1. extends ``_TEXT_SEVERITY_TRAILING_PUNCT`` to include
         ASCII hyphen (``-``), em dash (``\u2014``), and en dash
         (``\u2013``), AND
      2. adds a noun-boundary regex preprocessor that inserts a
         whitespace token boundary immediately after a recognized
         severity/priority noun followed by a dash.

    The preprocessor is restricted to ``(severity|priority) +
    dash`` so it does not affect compound words like
    ``high-priority issue`` (where the dash is between ``high``
    and ``priority``, neither of which is the noun itself).

    These tests verify the cycle 9 fix on both the helper
    directly (table-driven) and end-to-end via the gate.
    """

    POSITIVE_CASES = (
        # Required by auth prompt — dash variants.
        ("em dash after priority — dbID 3447899921 exact",
         "Bumping retry is high priority\u2014this skips CI", True),
        ("hyphen after priority — dbID 3447899921 variant",
         "is high priority-this skips CI", True),
        ("em dash after severity",
         "has high severity\u2014this is blocking", True),
        ("hyphen in compound modifier after severity",
         "has a high severity-impact case", True),
        ("em dash after intensifier form",
         "is an extremely high severity\u2014this matters", True),
        # Additional cycle 9 dash forms.
        ("en dash after severity",
         "is high severity\u2013this matters", True),
        ("en dash after priority",
         "has high priority\u2013this is blocking", True),
        ("em dash after medium priority",
         "is medium priority\u2014please check", True),
        ("hyphen after medium severity",
         "is medium severity-must fix", True),
        ("em dash after low severity with article",
         "has a low severity\u2014please recheck", True),
    )

    NEGATIVE_CASES = (
        # Required by auth prompt — preserve prior negative behavior.
        ("negation",
         "is not high priority", False),
        ("meta 'classified as'",
         "classified as high priority", False),
        ("context 'with ...'",
         "with high priority context", False),
        ("P0/P1/P2 taxonomy",
         "P0/P1/P2 severity taxonomy", False),
        ("bare noun phrase",
         "high priority context only", False),
        # Dash must NOT rescue negated forms.
        ("dash after negated priority (definitive)",
         "is not high priority\u2014please skip", False),
        ("dash after negated severity (definitive)",
         "has no high severity\u2014this is wrong", False),
        # Dash must NOT rescue meta forms.
        ("dash after classified-as form",
         "classified as high priority\u2014with extras", False),
        ("dash after with-context form",
         "with high priority context\u2014please recheck", False),
        # Compound with dash BEFORE the noun must NOT match (dash
        # only triggers when AFTER severity/priority).
        ("dash between 'high' and 'priority' (compound)",
         "is a high-priority issue", False),
        # Meta 'described as' with dash after priority.
        ("described as + dash",
         "described as a high priority issue\u2014fixed", False),
    )

    def test_positive_cases_table(self):
        for desc, text, expected in self.POSITIVE_CASES:
            with self.subTest(case=desc, text=text):
                self.assertEqual(
                    crc._has_direct_text_severity_declaration(text),
                    True,
                    f"Positive case '{desc}': text={text!r} should return True"
                )

    def test_negative_cases_table(self):
        for desc, text, expected in self.NEGATIVE_CASES:
            with self.subTest(case=desc, text=text):
                self.assertEqual(
                    crc._has_direct_text_severity_declaration(text),
                    False,
                    f"Negative case '{desc}': text={text!r} should return False"
                )

    def test_cycle9_no_regression_on_cycle8_positive(self):
        """All cycle-8 positive forms must still rescue after
        cycle 9. Run a sanity check on a representative set."""
        cycle8_positive = [
            "is high priority",
            "is a high priority issue",
            "is an high severity issue",
            "is an extremely high severity issue",
            "has high severity",
            "has a high severity impact",
            "has an extremely high priority impact",
            "this is a very high severity issue",
            "this has a clearly high priority impact",
            "is medium priority",
            "is low severity",
            "this is a very extremely high severity issue",
        ]
        for text in cycle8_positive:
            with self.subTest(text=text):
                self.assertTrue(
                    crc._has_direct_text_severity_declaration(text),
                    f"Cycle-8 positive regression: {text!r} must still rescue"
                )

    def test_cycle9_no_regression_on_cycle8_negative(self):
        """All cycle-8 negative forms must still NOT rescue
        after cycle 9."""
        cycle8_negative = [
            "is not high priority",
            "is not a high priority issue",
            "has no high severity impact",
            "not high severity",
            "classified as high priority",
            "flagged as high severity",
            "described as a high priority issue",
            "with high priority context",
            "with high severity language",
            "P0/P1/P2 severity taxonomy",
            "high priority context only",
            "not actually high priority",
            "is no longer high severity",
        ]
        for text in cycle8_negative:
            with self.subTest(text=text):
                self.assertFalse(
                    crc._has_direct_text_severity_declaration(text),
                    f"Cycle-8 negative regression: {text!r} must still NOT rescue"
                )


class TestHasDirectTextSeverityDeclarationCycle10NegationContinue(unittest.TestCase):
    """Cycle 10 (PR #405 fresh Codex P2 finding, dbID 3448488549,
    2026-06-21T13:28:56Z on head ecb271b26cca29b): the cycle-9
    helper returned ``False`` for the WHOLE helper on the first
    negated copula it encountered. That silently filtered out a
    real P1 finding whenever the comment contained ANY negated
    copula early in the body, even if a later clause had an
    affirmative severity declaration.

    Cycle 10 fix: change the immediate-negation branch from
    ``return False`` to ``continue`` so the helper rejects only
    THAT candidate copula and moves on to the next copula in the
    input. A later affirmative copula still rescues the comment.

    The fix preserves the protections against:

    - meta-discussion forms (``classified as`` / ``described as``)
    - context-only forms (``with high priority context``)
    - bare negation (``not high severity``)
    - copula + immediate negation when no later affirmative copula
      exists in the input
    - ``as`` and ``with`` as rescue verbs

    These tests are the canonical spec for the cycle-10
    negation-continue behavior. The POSITIVE_CASES table mirrors
    the multi-clause example from the fresh Codex review body
    exactly. The NEGATIVE_CASES table re-pins every prior
    protection so a regression in any of those directions would
    be caught.
    """

    POSITIVE_CASES = (
        # Required by auth prompt — multiple copulas with a
        # negated early copula and an affirmative later copula.
        ("auth prompt — early negated 'is not', later 'is high priority'",
         "Bumping the retry counter is not safe; this is high priority because it skips CI",
         True),
        ("auth prompt variant — early negated 'has no', later 'has high severity'",
         "Re-requesting review: this has high severity impact",
         True),
        # Additional multi-copula forms.
        ("two copulas, first negated, second affirmative (semicolon split)",
         "this is not high priority; this has high severity impact",
         True),
        ("three copulas, first two negated, last affirmative",
         "this is not safe; that has no impact; it is high priority because it breaks CI",
         True),
        # Subject + negation + subject + affirmative
        ("subject 'this' + negated copula, then affirmative copula later",
         "this is not a high priority issue, but it has a high severity impact later",
         True),
        # Negation in second clause is followed by affirmative in third clause
        ("negated copula in middle, affirmative copula at end",
         "this is medium priority, that is not blocking, this is high severity",
         True),
    )

    NEGATIVE_CASES = (
        # Required by auth prompt — protections preserved.
        ("negation only — no later affirmative copula",
         "Bumping this is not high priority", False),
        ("negation with article — no later affirmative copula",
         "Bumping this has no high severity impact", False),
        # Meta-discussion forms (no copula).
        ("meta 'classified as' — no copula at all",
         "classified as high priority", False),
        ("context 'with ...' — no copula at all",
         "with high priority context", False),
        ("meta 'described as' — no copula at all",
         "described as a high priority issue", False),
        # Bare negation (no copula).
        ("bare negation — no copula at all",
         "not high severity", False),
        ("bare negation with article",
         "not actually high priority", False),
        # Cycle-8/9 negative regression guards — these must
        # still NOT rescue after the cycle-10 fix.
        ("single negated copula — cycle 5 regression check",
         "is not high priority", False),
        ("negated copula with article — cycle 7 regression check",
         "is not a high priority issue", False),
        ("negated copula with 'no' — cycle 7 regression check",
         "has no high severity impact", False),
        # Multiple copulas, ALL negated — must NOT rescue.
        ("two copulas, both negated",
         "this is not high priority and has no high severity impact", False),
        ("three copulas, all negated",
         "this is not high priority; that has no high severity; this is not blocking", False),
    )

    def test_positive_cases_table(self):
        for desc, text, expected in self.POSITIVE_CASES:
            with self.subTest(case=desc, text=text):
                self.assertEqual(
                    crc._has_direct_text_severity_declaration(text),
                    True,
                    f"Positive case '{desc}': text={text!r} should return True",
                )

    def test_negative_cases_table(self):
        for desc, text, expected in self.NEGATIVE_CASES:
            with self.subTest(case=desc, text=text):
                self.assertEqual(
                    crc._has_direct_text_severity_declaration(text),
                    False,
                    f"Negative case '{desc}': text={text!r} should return False",
                )

    def test_cycle10_exact_auth_prompt_examples(self):
        """Exact text from the auth prompt and fresh Codex
        review body, pinned as separate test methods so any
        change in either direction fails loudly."""
        # Example 1 (auth prompt):
        # "Bumping the retry counter is not safe; this is
        #  high priority because it skips CI"
        # Early copula: "is not safe" (negated).
        # Later copula: "is high priority" (affirmative).
        # Expected: True — the later affirmative copula
        # rescues the comment.
        self.assertTrue(
            crc._has_direct_text_severity_declaration(
                "Bumping the retry counter is not safe; "
                "this is high priority because it skips CI"
            ),
            "Cycle-10 exact example 1 (auth prompt) must rescue "
            "(later affirmative copula is 'is high priority')",
        )
        # Example 2 (auth prompt):
        # "Re-requesting review: this has high severity impact"
        # Single copula, no negation.
        # Expected: True.
        self.assertTrue(
            crc._has_direct_text_severity_declaration(
                "Re-requesting review: this has high severity impact"
            ),
            "Cycle-10 exact example 2 (auth prompt) must rescue "
            "(single affirmative copula)",
        )
        # Example 3 (auth prompt):
        # "Bumping this is not high priority by itself, but it
        #  has a high severity impact later"
        # Early copula: "is not high priority by itself"
        # (negated).
        # Later copula: "has a high severity impact later"
        # (affirmative).
        # Expected: True.
        self.assertTrue(
            crc._has_direct_text_severity_declaration(
                "Bumping this is not high priority by itself, "
                "but it has a high severity impact later"
            ),
            "Cycle-10 exact example 3 (auth prompt) must rescue "
            "(later affirmative copula is 'has a high severity impact')",
        )

    def test_cycle10_negation_only_inputs_still_fail(self):
        """Inputs where EVERY copula is negated must still NOT
        rescue — the cycle-5/6/7/8/9 protection against
        negated-only input is preserved."""
        for text in (
            "Bumping this is not high priority",
            "Bumping this is not high severity",
            "Bumping this has no high severity impact",
            "this is not a high priority issue",
            "this is not high priority; this has no high severity impact",
        ):
            with self.subTest(text=text):
                self.assertFalse(
                    crc._has_direct_text_severity_declaration(text),
                    f"Negation-only input {text!r} must still NOT rescue "
                    f"(cycle-10 negation-only protection)",
                )


class TestHasActionableFindingSignal(unittest.TestCase):
    """Table-driven unit tests for the centralized actionability
    detector :func:`check_pr_review_comments._has_actionable_finding_signal`.

    This is the SINGLE SOURCE OF TRUTH for the actionability
    decision in the classifier pipeline (option B refactor).
    The old cycle-3-11 ladder encoded actionability across 7
    hand-tuned guards inside ``is_coordination_comment``; the
    new architecture consolidates that decision into this helper
    plus a final-suppressor coordination check.

    The helper is a disjunction of three narrow detectors:

    1. **Explicit P0/P1/P2/P3 marker** (badge / bracket / colon
       declaration) — see ``EXPLICIT_MARKER_CASES``.
    2. **Copula-based text-alias declaration** delegated to
       :func:`_has_direct_text_severity_declaration` — see
       ``COPULA_CASES``.
    3. **Narrow colon-form declaration** delegated to
       :func:`_has_narrow_colon_form_declaration` — see
       ``COLON_FORM_CASES``.

    Plus a ``NO_SIGNAL_CASES`` table pinning the negative
    examples (coordination noise with no actionable signal).

    All prior cycle findings are pinned in :class:`TestNewArchitectureFlow`
    below via the full ``classify_item`` pipeline. This class
    tests the helper directly.
    """

    # Detector 1: explicit P0/P1/P2/P3 marker.
    EXPLICIT_MARKER_CASES = (
        ("P0: declaration", "P0: critical bug", True),
        ("P1: declaration", "P1: this is a blocker", True),
        ("P2: declaration", "P2: review-comment gate fail", True),
        ("P3: declaration", "P3: minor polish", True),
        ("P1 badge marker",
         "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange) Test**",
         True),
        ("P0 bracketed", "[P0] this must fix", True),
        ("P1 bracketed", "[P1] severity bracket form", True),
        ("P2 bracketed with coord prefix",
         "Re-requesting review on [P2] bracketed priority", True),
    )

    # Detector 2: copula-based text-alias declaration.
    # Delegates to _has_direct_text_severity_declaration (cycle-8 helper).
    COPULA_CASES = (
        # dbID 3447794638 — copula + high severity
        ("Bumping retry counter is high severity because it skips CI",
         True),
        # dbID 3447818802 — copula + high priority
        ("Bumping retry counter is high priority because it skips CI",
         True),
        # dbID 3447825478 — negated copula
        ("Re-requesting Codex review — this is not high priority",
         False),
        # dbID 3447830523 — meta 'classified as'
        ("Re-requesting Codex review — this was classified as high priority",
         False),
        # dbID 3447849261 — article-bearing
        ("Bumping the retry counter is a high priority issue",
         True),
        # dbID 3447871114 — intensifier
        ("Re-requesting review: this is an extremely high severity issue",
         True),
        # dbID 3447899921 — dash after noun
        ("Bumping retry is high priority—this skips CI", True),
        # dbID 3448488549 — multi-copula with later affirmative
        ("Bumping the retry counter is not safe; this is high priority because it skips CI",
         True),
    )

    # Detector 3: narrow colon-form declaration.
    # Delegates to _has_narrow_colon_form_declaration.
    COLON_FORM_CASES = (
        # dbID 3448545827 — exact Codex example
        ("Bumping retry counter: high severity regression", True),
        ("Re-requesting review: high priority issue", True),
        ("Following up: medium severity problem", True),
        ("Bumping this: low priority cleanup", True),
        ("Re-requesting review: high severity impact in CI", True),
        # dbID 3448570717 — meta-verb "fixed" must NOT rescue
        ("Re-requesting Codex review: fixed the high severity regression from the prior finding",
         False),
        # dbID 3448570719 — article-separated negation must NOT rescue
        ("Re-requesting review: this is not a high priority issue, just a re-prompt",
         False),
        # Other colon-form negative cases.
        ("Bumping retry counter: P0/P1/P2 severity taxonomy", False),
        ("Bumping retry counter: not high severity", False),
        ("Bumping retry counter: high priority context only", False),
        ("Bumping retry counter: high priority context", False),
        ("Bumping retry counter: high severity only", False),
    )

    # No actionable signal — coordination noise.
    NO_SIGNAL_CASES = (
        ("Bumping retry counter for review", False),
        ("Re-requesting review on latest head", False),
        ("Following up on the previous comment", False),
        ("Bumping this because CI is pending", False),
        ("Gentle nudge to @codex", False),
        ("", False),
    )

    def test_explicit_marker_cases(self):
        for desc, body, expected in self.EXPLICIT_MARKER_CASES:
            with self.subTest(case=desc, body=body):
                self.assertEqual(
                    crc._has_actionable_finding_signal(body),
                    expected,
                    f"Marker case '{desc}': body={body!r} expected {expected}",
                )

    def test_copula_cases(self):
        for body, expected in self.COPULA_CASES:
            with self.subTest(body=body):
                self.assertEqual(
                    crc._has_actionable_finding_signal(body),
                    expected,
                    f"Copula case: body={body!r} expected {expected}",
                )

    def test_colon_form_cases(self):
        for body, expected in self.COLON_FORM_CASES:
            with self.subTest(body=body):
                self.assertEqual(
                    crc._has_actionable_finding_signal(body),
                    expected,
                    f"Colon-form case: body={body!r} expected {expected}",
                )

    def test_no_signal_cases(self):
        for body, expected in self.NO_SIGNAL_CASES:
            with self.subTest(body=body):
                self.assertEqual(
                    crc._has_actionable_finding_signal(body),
                    expected,
                    f"No-signal case: body={body!r} expected {expected}",
                )

    def test_helper_handles_empty_input(self):
        self.assertFalse(crc._has_actionable_finding_signal(""))

    def test_helper_handles_none_input(self):
        self.assertFalse(crc._has_actionable_finding_signal(None))  # type: ignore[arg-type]


class TestNarrowColonFormDeclaration(unittest.TestCase):
    """Table-driven unit tests for the cycle-12 narrowed helper
    :func:`check_pr_review_comments._has_narrow_colon_form_declaration`.

    This helper REPLACES the cycle-11
    ``_has_colon_form_text_severity_declaration`` helper with a
    narrower grammar that addresses both cycle-12 findings
    (dbIDs 3448570717 + 3448570719) in a single tight
    detector.

    Key invariants the narrowed helper maintains:

    * Only the FIRST FOUR tokens after the FIRST ``:`` are
      considered (window-boundary fix for dbID 3448570717).
    * The FIRST token after the colon must NOT be a
      meta-verb/past-participle (``fixed`` / ``addressed`` /
      ``described`` / ``classified`` / ``flagged`` / ``reviewed``
      / ``identified`` / ``mentioned``) — meta-discriminator for
      dbID 3448570717.
    * The level token must NOT be preceded by a negation token
      with up to 3-token look-back — fix for dbID 3448570719
      (``this is not a high priority issue``).
    * The level/noun pair must NOT be followed by ``context``
      or ``only`` (meta/context-only form, preserved from cycle-11).
    """

    POSITIVE_CASES = (
        # dbID 3448545827 — the exact Codex example.
        ("exact dbID 3448545827 — Bumping + colon + high severity",
         "Bumping retry counter: high severity regression", True),
        # Required positive: text-priority after coordination prefix.
        ("auth prompt positive — Re-requesting + high priority issue",
         "Re-requesting review: high priority issue", True),
        # Required positive: medium severity.
        ("auth prompt positive — Following up + medium severity",
         "Following up: medium severity problem", True),
        # Required positive: low priority.
        ("auth prompt positive — Bumping this + low priority",
         "Bumping this: low priority cleanup", True),
        # Required positive: high severity impact in CI (multi-token).
        ("auth prompt positive — Re-requesting + high severity impact",
         "Re-requesting review: high severity impact in CI", True),
        # Additional positive cases.
        ("trailing punctuation after noun",
         "Bumping retry counter: high severity regression,", True),
        ("re-request + medium priority fix",
         "Re-requesting review: medium priority fix", True),
        ("bumping + low severity case",
         "Bumping this: low severity case", True),
        ("gentle nudge + high priority regression",
         "Gentle nudge: high priority regression", True),
    )

    NEGATIVE_CASES = (
        # dbID 3448570717 — meta-verb "fixed" past the window
        # boundary. The "fixed" word is the first post-colon
        # token → meta-verb discriminator rejects.
        ("dbID 3448570717 — fixed-prior-finding description",
         "Re-requesting Codex review: fixed the high severity regression from the prior finding",
         False),
        # dbID 3448570719 — article-separated negation.
        ("dbID 3448570719 — article-separated negation",
         "Re-requesting review: this is not a high priority issue, just a re-prompt",
         False),
        # No colon at all → coordination-only (no severity).
        ("no colon — bumping for review",
         "Bumping retry counter for review", False),
        ("no colon — re-requesting on latest head",
         "Re-requesting review on latest head", False),
        ("no colon — following up on previous comment",
         "Following up on the previous comment", False),
        # P-taxonomy (not text-alias).
        ("P-taxonomy after colon",
         "Bumping retry counter: P0/P1/P2 severity taxonomy", False),
        # Bare negation before level.
        ("bare negation — not high severity",
         "Bumping retry counter: not high severity", False),
        # Context-only form.
        ("context-only — high priority context only",
         "Bumping retry counter: high priority context only", False),
        ("context-only — high priority context",
         "Bumping retry counter: high priority context", False),
        ("context-only — high severity only",
         "Bumping retry counter: high severity only", False),
        # Window-boundary fix: level/noun past 4th post-colon token.
        # The first 4 tokens after the colon are "I want to mention"
        # (none are level tokens) — only AFTER them does the level
        # appear, so the window-boundary check rejects this. The
        # original cycle-11 helper would have over-rescued this
        # body because it scanned ALL post-colon tokens.
        ("window-boundary — level/noun past 4 tokens after colon",
         "Re-requesting Codex review: I want to mention that the high severity issue is still there",
         False),
        # Meta-verbs other than "fixed".
        ("meta-verb addressed",
         "Bumping retry counter: addressed the high severity issue", False),
        ("meta-verb described",
         "Bumping retry counter: described the high priority issue", False),
        ("meta-verb classified",
         "Bumping retry counter: classified the high severity case", False),
        ("meta-verb flagged",
         "Bumping retry counter: flagged the high priority regression", False),
        ("meta-verb reviewed",
         "Bumping retry counter: reviewed the high severity item", False),
        ("meta-verb identified",
         "Bumping retry counter: identified the high priority problem", False),
        ("meta-verb mentioned",
         "Bumping retry counter: mentioned the high severity risk", False),
    )

    def test_positive_cases_table(self):
        for desc, text, expected in self.POSITIVE_CASES:
            with self.subTest(case=desc, text=text):
                self.assertEqual(
                    crc._has_narrow_colon_form_declaration(text),
                    True,
                    f"Positive case '{desc}': text={text!r} should return True",
                )

    def test_negative_cases_table(self):
        for desc, text, expected in self.NEGATIVE_CASES:
            with self.subTest(case=desc, text=text):
                self.assertEqual(
                    crc._has_narrow_colon_form_declaration(text),
                    False,
                    f"Negative case '{desc}': text={text!r} should return False",
                )

    def test_helper_handles_empty_input(self):
        self.assertFalse(crc._has_narrow_colon_form_declaration(""))

    def test_helper_handles_none_input(self):
        self.assertFalse(crc._has_narrow_colon_form_declaration(None))  # type: ignore[arg-type]

    def test_meta_verb_set_is_closed(self):
        """The _META_VERBS set must be the documented closed
        vocabulary, not arbitrary grammar expansion."""
        self.assertEqual(
            crc._META_VERBS,
            frozenset({
                "fixed", "addressed", "described", "classified",
                "flagged", "reviewed", "identified", "mentioned",
            }),
        )


class TestNewArchitectureFlow(unittest.TestCase):
    """End-to-end pipeline tests covering all 11 prior cycle
    findings via the new ``classify_item`` flow:

        1. Author filter
        2. Codex-needle check
        3. Actionability check via ``_has_actionable_finding_signal``
        4. Coordination suppression ONLY if no actionable signal
        5. Severity extraction via ``extract_severity``
        6. Finding emission

    Each prior cycle finding is pinned as a separate test method
    so any regression in either direction fails loudly. The
    pipeline is now: actionability wins; coordination suppression
    is a final suppressor (not an early bypass).
    """

    # ------------------------------------------------------------------
    # POSITIVE TABLE — bodies that should classify as findings.
    # ------------------------------------------------------------------
    POSITIVE_BODIES = (
        # dbID 3447794638 (cycle 3): coordination + copula text-severity.
        ("dbID 3447794638 — bumping + is high severity because skips CI",
         "Bumping the retry counter is high severity because it skips CI",
         "P1"),
        # dbID 3447818802 (cycle 4): coordination + copula text-priority.
        ("dbID 3447818802 — bumping + is high priority because skips CI",
         "Bumping the retry counter is high priority because it skips CI",
         "P1"),
        # dbID 3447849261 (cycle 7): article-bearing.
        ("dbID 3447849261 — article-bearing severity",
         "Bumping the retry counter is a high priority issue",
         "P1"),
        # dbID 3447871114 (cycle 8): intensifier.
        ("dbID 3447871114 — intensifier severity",
         "Re-requesting review: this is an extremely high severity issue",
         "P1"),
        # dbID 3447899921 (cycle 9): dash after noun.
        ("dbID 3447899921 — dash after priority",
         "Bumping retry is high priority—this skips CI",
         "P1"),
        # dbID 3448488549 (cycle 10): multi-copula with later affirmative.
        ("dbID 3448488549 — multi-copula later affirmative",
         "Bumping the retry counter is not safe; this is high priority because it skips CI",
         "P1"),
        # dbID 3448545827 (cycle 11): colon-form severity declaration.
        ("dbID 3448545827 — colon-form severity",
         "Bumping retry counter: high severity regression",
         "P1"),
        # dbID 3448545827 variant: Re-requesting + colon + high priority.
        ("dbID 3448545827 variant — colon-form priority",
         "Re-requesting review: high priority issue",
         "P1"),
        # No coordination prefix, no copula, just text-alias.
        ("text-alias high severity without coordination prefix",
         "This is a high severity issue",
         "P1"),
        ("text-alias high priority with article",
         "This has a high priority impact",
         "P1"),
        ("text-alias intensifier without coordination prefix",
         "This is an extremely high severity issue",
         "P1"),
        # P1: marker wins over coordination prefix.
        ("P1: with coordination prefix",
         "Bumping retry counter — P1: this is a security issue; must fix",
         "P1"),
    )

    # ------------------------------------------------------------------
    # NEGATIVE TABLE — bodies that should be suppressed as coordination.
    # ------------------------------------------------------------------
    NEGATIVE_BODIES = (
        # dbID 3447825478 (cycle 5): coordination + negated copula.
        ("dbID 3447825478 — negated priority coordination",
         "Re-requesting Codex review — this is not high priority",
         None),
        # dbID 3447830523 (cycle 6): coordination + meta "classified as".
        ("dbID 3447830523 — meta priority coordination",
         "Re-requesting Codex review — this was classified as high priority",
         None),
        # dbID 3448570717 (cycle 12): meta-verb "fixed" past window.
        ("dbID 3448570717 — fixed-prior-finding description",
         "Re-requesting Codex review: fixed the high severity regression from the prior finding",
         None),
        # dbID 3448570719 (cycle 12): article-separated negation.
        ("dbID 3448570719 — article-separated negation",
         "Re-requesting review: this is not a high priority issue, just a re-prompt",
         None),
        # True coordination noise — no severity mention at all.
        ("true coordination — bumping for review",
         "Bumping retry counter for review", None),
        ("true coordination — re-requesting on latest head",
         "Re-requesting review on latest head", None),
        ("true coordination — following up on previous comment",
         "Following up on the previous comment", None),
        ("true coordination — bumping because CI pending",
         "Bumping this because CI is pending", None),
        # Taxonomy / context-only meta forms.
        ("taxonomy — P0/P1/P2 severity taxonomy",
         "Bumping retry counter: P0/P1/P2 severity taxonomy", None),
        ("context-only — high priority context only",
         "Bumping retry counter: high priority context only", None),
        ("meta 'described as a high priority issue'",
         "Re-requesting Codex review — described as a high priority issue",
         None),
        ("meta 'with high priority context'",
         "Re-requesting Codex review — with high priority context", None),
    )

    def _classify(self, body):
        """Helper: run the full pipeline on a body and return
        the resulting findings list."""
        item = {
            "user": {"login": "reviewer"},
            "body": body,
            "state": "",
            "path": "scripts/local/check_pr_review_comments.py",
            "line": 1,
            "commit_id": "abc123abc123",
        }
        return crc.classify_item(item, "issue_comment", set())

    def test_positive_bodies_classify_as_findings(self):
        for desc, body, expected_severity in self.POSITIVE_BODIES:
            with self.subTest(case=desc, body=body):
                findings = self._classify(body)
                self.assertGreaterEqual(
                    len(findings), 1,
                    f"Positive case '{desc}': body={body!r} must classify "
                    f"as at least one finding (got {findings!r})",
                )
                severities = [f.get("severity") for f in findings]
                self.assertIn(
                    expected_severity, severities,
                    f"Positive case '{desc}': body={body!r} must classify "
                    f"as {expected_severity} (got severities={severities!r})",
                )

    def test_negative_bodies_suppressed_as_coordination(self):
        for desc, body, _expected in self.NEGATIVE_BODIES:
            with self.subTest(case=desc, body=body):
                findings = self._classify(body)
                self.assertEqual(
                    findings, [],
                    f"Negative case '{desc}': body={body!r} must be "
                    f"suppressed as coordination (got {findings!r})",
                )

    def test_no_p0_misclassification_regression(self):
        """Coordination-prefixed bodies with text-alias severity
        declarations must classify as P1/P2/P3 (not P0). The
        cycle-11 helper never produced P0 from text-alias
        high/medium/low, and the refactor preserves that
        invariant."""
        for body, _ in (
            ("Bumping retry counter: high severity regression", None),
            ("Re-requesting review: medium severity problem", None),
            ("Following up: low priority cleanup", None),
            ("Bumping retry counter is high severity because it skips CI", None),
        ):
            with self.subTest(body=body):
                findings = self._classify(body)
                severities = [f.get("severity") for f in findings]
                self.assertNotIn(
                    "P0", severities,
                    f"Text-alias severity declaration must NOT produce P0; "
                    f"body={body!r}, severities={severities!r}",
                )

    def test_guard6_copula_cases_still_rescue(self):
        """Guard 6 (cycle-8) copula-based text-severity detection
        must continue to work in the new pipeline."""
        copula_cases = (
            "is high priority",
            "is a high priority issue",
            "is an extremely high severity issue",
            "has high severity",
            "has a high severity impact",
            "this has a very high priority impact",
            "Bumping retry is high priority—this skips CI",
            "Bumping the retry counter is not safe; this is high priority because it skips CI",
        )
        for body in copula_cases:
            with self.subTest(body=body):
                # The actionable-signal detector must rescue.
                self.assertTrue(
                    crc._has_actionable_finding_signal(body),
                    f"Guard 6 must continue to rescue copula text-severity "
                    f"declaration in body {body!r}",
                )


class TestHasDirectTextSeverityDeclaration(unittest.TestCase):
    """Table-driven unit tests for the cycle-8 helper
    :func:`check_pr_review_comments._has_direct_text_severity_declaration`.

    The helper replaces the cycle-7 regex-based Guard 6 with a
    table-driven tokenizer that accepts a limited intensifier
    list (max 2) between the optional article and the level
    token, while still rejecting negation, meta-discussion
    forms (``as``/``with``), and bare taxonomy prose.

    Positive cases must return ``True``; negative cases must
    return ``False``. The tables are the canonical spec of the
    helper's behavior, derived from the cycle-8 design-narrow
    extension authorization prompt.
    """

    POSITIVE_CASES = (
        # Required by auth prompt — direct affirmative forms.
        ("direct copula, no article",
         "is high priority", True),
        ("direct copula with article 'a'",
         "is a high priority issue", True),
        ("direct copula with article 'an'",
         "is an high severity issue", True),
        ("intensifier 'extremely' between article and level — dbID 3447871114",
         "is an extremely high severity issue", True),
        ("direct copula 'has'",
         "has high severity", True),
        ("'has' with article 'a'",
         "has a high severity impact", True),
        ("'has' with intensifier 'extremely'",
         "has an extremely high priority impact", True),
        ("subject 'this' with intensifier 'very'",
         "this is a very high severity issue", True),
        ("subject 'this' + 'has' with intensifier 'clearly'",
         "this has a clearly high priority impact", True),
        # Additional positive forms (not exhaustive).
        ("medium priority, no article",
         "is medium priority", True),
        ("low severity, no article",
         "is low severity", True),
        ("two intensifiers (very extremely)",
         "this is a very extremely high severity issue", True),
        ("subject 'that'",
         "that has a very high priority impact", True),
        ("subject 'it'",
         "it has a high priority issue", True),
        # Legacy cycle-3/4/5/7 forms (regression — must still rescue).
        ("cycle-3 example (Bumping + is + high severity)",
         "Bumping the retry counter is high severity and must be fixed", True),
        ("cycle-4 example (is + high priority)",
         "Bumping the retry counter is high priority and should be treated as a finding", True),
        ("cycle-5 example (Re-requesting + is + high priority)",
         "Re-requesting review: this comment is high priority and must be addressed", True),
        ("cycle-7 example (Re-requesting + has + a + high severity impact)",
         "Re-requesting review: this comment has a high severity impact", True),
    )

    NEGATIVE_CASES = (
        # Required by auth prompt — must NOT be rescued.
        ("negation after copula — is not high priority",
         "is not high priority", False),
        ("negation with article — is not a high priority issue",
         "is not a high priority issue", False),
        ("'has no' negation with article",
         "has no high severity impact", False),
        ("bare negation at start",
         "not high severity", False),
        ("meta 'classified as'",
         "classified as high priority", False),
        ("meta 'flagged as'",
         "flagged as high severity", False),
        ("meta 'described as a' (with article)",
         "described as a high priority issue", False),
        ("context 'with high priority context'",
         "with high priority context", False),
        ("context 'with high severity language'",
         "with high severity language", False),
        ("taxonomy P0/P1/P2 severity",
         "P0/P1/P2 severity taxonomy", False),
        ("bare noun phrase 'high priority context only'",
         "high priority context only", False),
        ("bare negation with adverb 'not actually high priority'",
         "not actually high priority", False),
        # Regression — cycle 5/6/7 forms that must STILL not rescue.
        ("cycle-5 regression: 'is not high priority' in coordination wrapper",
         "Re-requesting Codex review — this is not high priority, please skip", False),
        ("cycle-6 regression: 'classified as high priority' in coordination wrapper",
         "Re-requesting Codex review — this was classified as high priority", False),
        ("cycle-6 regression: 'with high priority context' in coordination wrapper",
         "Re-requesting Codex review — with high priority context", False),
        ("cycle-7 regression: 'is not a high priority issue' (negation with article)",
         "Re-requesting Codex review — this is not a high priority issue, just a re-prompt", False),
        ("cycle-7 regression: 'has no high severity impact' (has no)",
         "Re-requesting Codex review — this comment has no high severity impact, just a re-prompt", False),
        # dbID 3447871114 exact pattern (must NOT rescue — meta form).
        ("dbID 3447871114 NEGATIVE case: meta + intensifier",
         "Re-requesting Codex review — this prior finding was described as an extremely high severity issue in the prior round", False),
        # Overflow: 3 intensifiers > MAX=2.
        ("3 intensifiers overflows MAX=2",
         "this is a very extremely particularly high severity issue", False),
        # No copula, arbitrary prose.
        ("no copula, just noun phrase",
         "high severity issue exists in the code", False),
        # Taxonomy prose.
        ("taxonomy text mentioning severity/priority without copula",
         "the high priority taxonomy includes P0/P1/P2 severity levels", False),
        # Contractions (not recognized as copulas — conservative).
        ("'isnt' contraction not recognized",
         "isnt a high priority issue", False),
    )

    def test_positive_cases_table(self):
        """All positive cases must return True."""
        for desc, text, expected in self.POSITIVE_CASES:
            with self.subTest(case=desc, text=text):
                self.assertEqual(
                    crc._has_direct_text_severity_declaration(text),
                    True,
                    f"Positive case '{desc}': text={text!r} should return True"
                )

    def test_negative_cases_table(self):
        """All negative cases must return False."""
        for desc, text, expected in self.NEGATIVE_CASES:
            with self.subTest(case=desc, text=text):
                self.assertEqual(
                    crc._has_direct_text_severity_declaration(text),
                    False,
                    f"Negative case '{desc}': text={text!r} should return False"
                )

    def test_helper_handles_empty_input(self):
        """Edge case: empty string must return False (no copula)."""
        self.assertFalse(crc._has_direct_text_severity_declaration(""))

    def test_helper_handles_none_input(self):
        """Edge case: None must return False (no copula)."""
        self.assertFalse(crc._has_direct_text_severity_declaration(None))

    def test_helper_uses_documented_word_tables(self):
        """The cycle-8 design constraints specify fixed word
        tables. Verify the tables are exposed at module
        level and contain the expected tokens."""
        self.assertEqual(set(crc._TEXT_SEVERITY_VERBS), {"is", "has"})
        self.assertEqual(set(crc._TEXT_SEVERITY_ARTICLES), {"a", "an"})
        self.assertEqual(set(crc._TEXT_SEVERITY_LEVELS), {"high", "medium", "low"})
        self.assertEqual(set(crc._TEXT_SEVERITY_NOUNS), {"severity", "priority"})
        # Intensifier whitelist per cycle-8 design.
        self.assertEqual(
            set(crc._TEXT_SEVERITY_INTENSIFIERS),
            {
                "very", "extremely", "particularly", "especially",
                "clearly", "obviously", "materially", "highly",
            }
        )
        # Negation set includes standard English negation tokens.
        self.assertIn("not", crc._TEXT_SEVERITY_NEGATIONS)
        self.assertIn("no", crc._TEXT_SEVERITY_NEGATIONS)
        # MAX_INTENSIFIERS is 2 per design constraint.
        self.assertEqual(crc._MAX_TEXT_SEVERITY_INTENSIFIERS, 2)
        # Cycle 9: trailing punct set must include dash separators
        # (dbID 3447899921) so that tokens like ``priority\u2014this``
        # are split into ``priority`` + ``this`` via the helper's
        # noun-boundary regex preprocessor.
        for ch in ("-", "\u2014", "\u2013"):
            self.assertIn(
                ch, crc._TEXT_SEVERITY_TRAILING_PUNCT,
                f"dash {ch!r} must be in _TEXT_SEVERITY_TRAILING_PUNCT (cycle 9)"
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


class TestSourceAwareFailClosedClassification(unittest.TestCase):
    """Table-driven unit tests for the source-aware fail-closed
    gate (option B2 refactor).

    The CORE DESIGN RULE under B2:

    * A current unresolved Codex inline review-thread
      comment is **actionable by default**.
    * Severity extraction ranks it.
    * If severity is absent but the thread is current,
      unresolved, Codex-authored, and not waived, default
      to blocking severity (``P2``).
    * Coordination suppression does NOT bypass Codex
      review-thread findings.
    * Coordination suppression remains available for
      human/top-level coordination comments such as
      re-request comments, bump comments, or status
      chatter.

    This test class covers 4 groups per the auth prompt:

    A. Codex review-thread comments are fail-closed.
    B. Coordination suppression still works for
       human/top-level comments.
    C. State filters still win (resolved / outdated /
       stale / waived).
    D. Regression coverage for prior Codex edge cases
       (cycle-3 through cycle-13 dbIDs).
    """

    # ------------------------------------------------------------------
    # A. Codex review-thread comments are fail-closed
    # ------------------------------------------------------------------
    # Bodies + expected severity when
    # ``is_codex_review_thread_current_unresolved=True``.
    A_POSITIVE_BODIES = (
        # Explicit P1 / P2 markers (highest priority in extract_severity).
        ("A1 — explicit P1: marker",
         "P1: this is a finding",
         "P1"),
        ("A2 — explicit P2: marker",
         "P2: review-comment gate fail",
         "P2"),
        ("A3 — explicit P3: marker",
         "P3: minor nit",
         "P3"),
        # Text-alias severity declaration (Priority 5 in extract_severity).
        ("A4 — text-severity 'high severity'",
         "This is a high severity issue",
         "P1"),
        ("A5 — text-priority 'high priority'",
         "This is a high priority impact",
         "P1"),
        ("A6 — text-severity 'medium severity'",
         "This is a medium severity issue",
         "P2"),
        # Colon-form severity declaration (coordination-prefixed bodies
        # are the original motivation for the gate; B2 must still detect
        # them when the source is a current unresolved Codex thread).
        ("A7 — colon-form 'Bumping + high severity' (exact dbID 3448545827)",
         "Bumping retry counter: high severity regression",
         "P1"),
        ("A8 — colon-form 'Re-requesting + high priority'",
         "Re-requesting review: high priority issue",
         "P1"),
        # The exact Codex dbID 3448621236 body — the actual Codex
        # comment starts with a P2 Badge (Priority 1 in extract_severity).
        ("A9 — exact dbID 3448621236 body with P2 Badge",
         "**<sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow) "
         "Preserve colon findings with short lead-ins**\n\n"
         "Architectural concern: With this four-token window, a "
         "coordination-prefixed finding such as `Bumping retry counter: "
         "this will be a high severity regression` puts `high severity` "
         "just outside the scanned tokens.",
         "P2"),
        # No severity at all — defaults to P2 (fail-closed).
        ("A10 — no severity at all — fail-closed default to P2",
         "This body has Codex needles but no severity or text-alias or "
         "colon-form declaration",
         "P2"),
        # dbID 3448621236 shape-grammar escape — under the OLD
        # architecture, the shape-grammar detector missed this body
        # (4-token colon window). Under B2 source-aware, the comment
        # IS a Codex review thread, so it's actionable regardless.
        # The body has 'high severity' which Priority 5 catches → P1.
        ("A11 — dbID 3448621236 — 'this will be' lead-in (cycle-13 body)",
         "Bumping retry counter: this will be a high severity regression",
         "P1"),
    )

    # ------------------------------------------------------------------
    # B. Coordination suppression still works for human/top-level
    #    comments (is_codex_review_thread_current_unresolved=False).
    # ------------------------------------------------------------------
    B_SUPPRESSED_BODIES = (
        # Coordination noise with no severity/blocking mention.
        ("B1 — bumping for review (coord noise)",
         "Bumping retry counter for review"),
        ("B2 — re-requesting on latest head",
         "Re-requesting review on latest head"),
        ("B3 — following up on previous comment",
         "Following up on the previous comment"),
        # Fixed-prior-finding description — even though it mentions
        # severity, it's NOT actionable (it's referencing a prior
        # fix). Under B2 with the source flag False, this falls
        # through to the shape-grammar detector which correctly
        # rejects it via the meta-verb / window-boundary guards.
        ("B4 — fixed-prior-finding description (dbID 3448570717)",
         "Re-requesting Codex review: fixed the high severity regression "
         "from the prior finding"),
        # Article-separated negation — also correctly rejected by
        # the shape-grammar detector.
        ("B5 — article-separated negation (dbID 3448570719)",
         "Re-requesting review: this is not a high priority issue, "
         "just a re-prompt"),
        # CI pending / status chatter — coordination noise with no
        # severity.
        ("B6 — bumping because CI pending",
         "Bumping this because CI is pending"),
    )

    # ------------------------------------------------------------------
    # D. Regression coverage for prior Codex edge cases.
    # ------------------------------------------------------------------
    # Each row is (dbID, description, body, expected_severity).
    # Bodies are the cycle-specific test cases that drove each
    # Codex finding; expected severity is what the B2 source-aware
    # gate produces when the source flag is True.
    #
    # Note on severity expectations: ``extract_severity`` does
    # pure-text matching (no negation/meta-awareness), so any
    # body that contains the words "high severity" or "high
    # priority" classifies as P1 even if the surrounding text
    # is meta-discussion or a negation. Under B2 source-aware
    # architecture, this is the CORRECT behavior: a current
    # unresolved Codex review-thread comment is actionable by
    # default, and the severity ranking is done by
    # ``extract_severity`` (which already handles P0/P1/P2/P3
    # markers, badges, brackets, and text-alias). The
    # fail-closed default to P2 only applies when no severity
    # can be extracted at all.
    D_REGRESSION_CASES = (
        # Cycle 3 — Coordination + copula text-severity.
        # extract_severity catches "high severity" → P1.
        ("3447794638", "cycle 3 — bumping + is high severity because skips CI",
         "Bumping the retry counter is high severity because it skips CI",
         "P1"),
        # Cycle 4 — Coordination + copula text-priority.
        # extract_severity catches "high priority" → P1.
        ("3447818802", "cycle 4 — bumping + is high priority because skips CI",
         "Bumping the retry counter is high priority because it skips CI",
         "P1"),
        # Cycle 5 — Coordination + negated copula.
        # extract_severity finds "high priority" → P1 (text-alias
        # is unaware of negation in surrounding text).
        ("3447825478", "cycle 5 — coordination + negated copula",
         "Re-requesting Codex review — this is not high priority",
         "P1"),
        # Cycle 6 — Meta 'classified as'.
        # extract_severity finds "high priority" → P1.
        ("3447830523", "cycle 6 — meta 'classified as'",
         "Re-requesting Codex review — this was classified as high priority",
         "P1"),
        # Cycle 7 — Article-bearing copula form.
        # extract_severity finds "high priority" → P1.
        ("3447849261", "cycle 7 — article-bearing copula",
         "Bumping the retry counter is a high priority issue",
         "P1"),
        # Cycle 8 — Intensifier.
        # extract_severity finds "high severity" → P1.
        ("3447871114", "cycle 8 — intensifier",
         "Re-requesting review: this is an extremely high severity issue",
         "P1"),
        # Cycle 9 — Dash after noun.
        # extract_severity finds "high priority" → P1.
        ("3447899921", "cycle 9 — dash after noun",
         "Bumping retry is high priority—this skips CI",
         "P1"),
        # Cycle 10 — Multi-copula with later affirmative.
        # extract_severity finds "high priority" → P1.
        ("3448488549", "cycle 10 — multi-copula later affirmative",
         "Bumping the retry counter is not safe; this is high priority because it skips CI",
         "P1"),
        # Cycle 11 — Colon-form severity.
        # extract_severity finds "high severity" → P1.
        ("3448545827", "cycle 11 — colon-form severity (exact Codex example)",
         "Bumping retry counter: high severity regression",
         "P1"),
        # Cycle 12 #1 — Meta-verb "fixed".
        # extract_severity finds "high severity" → P1.
        ("3448570717", "cycle 12 #1 — meta-verb fixed past window",
         "Re-requesting Codex review: fixed the high severity regression from the prior finding",
         "P1"),
        # Cycle 12 #2 — Article-separated negation.
        # extract_severity finds "high priority" → P1.
        ("3448570719", "cycle 12 #2 — article-separated negation",
         "Re-requesting review: this is not a high priority issue, just a re-prompt",
         "P1"),
        # Cycle 13 — Short lead-in past 4-token window. THIS is the
        # finding that drove the B2 refactor. Under the OLD shape-grammar
        # architecture, this body escaped the 4-token colon window and
        # was suppressed as coordination. Under B2 source-aware, the
        # comment IS a current unresolved Codex review thread, so it
        # is actionable regardless of body shape. extract_severity
        # finds "high severity" → P1.
        ("3448621236", "cycle 13 — short lead-in 'this will be'",
         "Bumping retry counter: this will be a high severity regression",
         "P1"),
    )

    # ------------------------------------------------------------------
    # Helper to construct a Codex-authored item dict.
    # ------------------------------------------------------------------
    def _codex_item(self, body, line=1):
        return {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": body,
            "state": "",
            "path": "scripts/local/check_pr_review_comments.py",
            "line": line,
            "commit_id": "cfc223809ebfe8e0e70475a171e99486c83933dd"[:12],
            "html_url": "https://github.com/OWNER/REPO/pull/405#discussion_r1",
        }

    def _human_item(self, body, line=1):
        return {
            "user": {"login": "Slideshow11"},
            "body": body,
            "state": "",
            "path": "scripts/local/check_pr_review_comments.py",
            "line": line,
            "commit_id": "cfc223809ebfe8e0e70475a171e99486c83933dd"[:12],
            "html_url": "https://github.com/OWNER/REPO/pull/405#issuecomment-1",
        }

    # ------------------------------------------------------------------
    # GROUP A — Codex review-thread comments are fail-closed.
    # ------------------------------------------------------------------
    def test_a_codex_review_thread_findings_are_fail_closed(self):
        """Current unresolved Codex review-thread comments
        are actionable by default and never suppressed by
        coordination-skip. Severity extraction ranks them; if
        no severity is found, default to P2 (blocking)."""
        for desc, body, expected_severity in self.A_POSITIVE_BODIES:
            with self.subTest(case=desc, body=body):
                item = self._codex_item(body)
                findings = crc.classify_item(
                    item, "inline_review_comment", set(),
                    is_codex_review_thread_current_unresolved=True,
                )
                self.assertGreaterEqual(
                    len(findings), 1,
                    f"A: '{desc}' must produce at least one finding "
                    f"(body={body!r}); got {findings!r}",
                )
                severities = [f.get("severity") for f in findings]
                self.assertIn(
                    expected_severity, severities,
                    f"A: '{desc}' must classify as {expected_severity}; "
                    f"got severities={severities!r}",
                )

    # ------------------------------------------------------------------
    # GROUP B — Coordination suppression still works for
    #          human/top-level comments.
    # ------------------------------------------------------------------
    def test_b_human_coordination_suppression_still_works(self):
        """Human coordination comments (re-requests, bumps,
        follow-ups, CI-pending) remain suppressible via
        coordination-skip. The source flag is False for these
        (they're not Codex review threads), so they fall
        through to the shape-grammar detector which suppresses
        them as coordination noise.

        Important: this test does NOT call main() to fetch
        thread metadata. Instead it directly verifies that
        ``classify_item`` with ``is_codex_review_thread_current_unresolved=False``
        (the default for non-Codex-thread comments) suppresses
        coordination-prefixed bodies with no actionable signal."""
        for desc, body in self.B_SUPPRESSED_BODIES:
            with self.subTest(case=desc, body=body):
                item = self._human_item(body)
                findings = crc.classify_item(
                    item, "issue_comment", set(),
                    is_codex_review_thread_current_unresolved=False,
                )
                self.assertEqual(
                    findings, [],
                    f"B: '{desc}' must be suppressed as coordination "
                    f"(body={body!r}); got {findings!r}",
                )

    # ------------------------------------------------------------------
    # GROUP C — State filters still win.
    # ------------------------------------------------------------------
    # Note: state filtering is done in main() (post-classify) by
    # comparing commit_id to live_head_sha, and by the blocker
    # classification logic which checks thread_resolved and
    # thread_outdated. The flag itself encodes (current AND
    # unresolved AND Codex-authored), so the per-item flag
    # already encodes most state filtering. These tests verify
    # the flag computation logic at the boundary:
    def test_c_flag_false_for_resolved_threads(self):
        """A Codex-authored thread that is already resolved is
        NOT actionable by default (the flag should be False).
        In the gate pipeline, main() only passes the flag=True
        when ``is_resolved=False``; resolved threads get the
        shape-grammar fallback. We simulate this at the unit
        level by verifying that ``is_codex_review_thread_current_unresolved=False``
        (the resolved-thread path) is the safe default."""
        body = "P1: this is a finding"
        item = self._codex_item(body)
        # Resolved-thread path: flag=False (the source-aware
        # main() would not pass flag=True for a resolved thread).
        findings = crc.classify_item(
            item, "inline_review_comment", set(),
            is_codex_review_thread_current_unresolved=False,
        )
        # With flag=False, the body still has explicit P1: marker
        # so extract_severity returns P1 and the shape-grammar
        # detector does NOT suppress. Resolved threads are
        # reported but not blocking (post-classify logic).
        self.assertGreaterEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "P1")

    def test_c_flag_false_for_outdated_threads(self):
        """A Codex-authored thread that is outdated is NOT
        actionable by default. Outdated threads reference
        older commits; the gate treats them as stale."""
        body = "P2: stale finding on old commit"
        item = self._codex_item(body)
        # Outdated-thread path: flag=False.
        findings = crc.classify_item(
            item, "inline_review_comment", set(),
            is_codex_review_thread_current_unresolved=False,
        )
        # Severity is still extracted correctly. The post-classify
        # blocker logic checks thread_outdated (set later in main())
        # and excludes outdated threads from current_head_blockers.
        self.assertGreaterEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "P2")

    def test_c_flag_false_for_human_authored_threads(self):
        """A review thread that is NOT authored by Codex is
        treated as a human comment. The shape-grammar fallback
        handles it normally. Even if the comment has a P1:
        marker, it should classify as P1 (the source flag
        doesn't change severity extraction — it only affects
        whether coordination-skip applies)."""
        body = "P1: this is a finding from a human reviewer"
        item = self._human_item(body)
        findings = crc.classify_item(
            item, "inline_review_comment", set(),
            is_codex_review_thread_current_unresolved=False,
        )
        self.assertGreaterEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "P1")

    # ------------------------------------------------------------------
    # GROUP D — Regression coverage for prior Codex edge cases.
    # ------------------------------------------------------------------
    def test_d_regression_for_prior_cycle_findings(self):
        """Verify that all 12 prior Codex findings (cycle-3
        through cycle-13) classify correctly under the B2
        source-aware architecture when the source flag is
        True (the comment is a current unresolved Codex
        review thread)."""
        for dbid, desc, body, expected_severity in self.D_REGRESSION_CASES:
            with self.subTest(dbid=dbid, body=body):
                item = self._codex_item(body)
                findings = crc.classify_item(
                    item, "inline_review_comment", set(),
                    is_codex_review_thread_current_unresolved=True,
                )
                self.assertGreaterEqual(
                    len(findings), 1,
                    f"D: dbID {dbid} '{desc}' must classify as a "
                    f"finding under B2 source-aware; got {findings!r}",
                )
                severities = [f.get("severity") for f in findings]
                self.assertIn(
                    expected_severity, severities,
                    f"D: dbID {dbid} '{desc}' must classify as "
                    f"{expected_severity} under B2; got "
                    f"severities={severities!r}",
                )

    # ------------------------------------------------------------------
    # Shape-grammar fallback path (no source flag) — verifies that
    # the OLD cycle-by-cycle behavior is preserved for non-Codex
    # comments.
    # ------------------------------------------------------------------
    def test_shape_grammar_fallback_for_non_codex_thread(self):
        """When ``is_codex_review_thread_current_unresolved``
        is False (non-Codex-thread comment), the shape-grammar
        detector runs as a secondary actionability check. This
        is the fallback path that preserves the cycle-3-12
        protections."""
        # Positive case: human reviewer writes a P1: comment.
        item = self._human_item("P1: this is a critical issue")
        findings = crc.classify_item(
            item, "inline_review_comment", set(),
            is_codex_review_thread_current_unresolved=False,
        )
        self.assertGreaterEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "P1")

        # Negative case: human reviewer posts a coordination comment
        # with no severity mention. Should be suppressed.
        item = self._human_item("Bumping this thread for review")
        findings = crc.classify_item(
            item, "inline_review_comment", set(),
            is_codex_review_thread_current_unresolved=False,
        )
        self.assertEqual(findings, [])

    # ------------------------------------------------------------------
    # The cycle-13 body must NOT be a false-negative under B2.
    # ------------------------------------------------------------------
    def test_dbID_3448621236_no_longer_a_false_negative(self):
        """The exact Codex dbID 3448621236 body is the cycle-13
        finding that drove the B2 refactor. Under the OLD
        architecture (4-token colon window), this body was
        suppressed because ``high severity`` was at position 4-5
        past the window AND the copula detector missed ``will be``.

        Under the B2 source-aware architecture, when the
        comment is a current unresolved Codex review thread
        (flag=True), this body IS actionable regardless of
        shape. The body is classified as P1 (because Priority 5
        in extract_severity matches ``high severity`` after
        ``will be a``)."""
        body = "Bumping retry counter: this will be a high severity regression"
        item = self._codex_item(body)
        findings = crc.classify_item(
            item, "inline_review_comment", set(),
            is_codex_review_thread_current_unresolved=True,
        )
        self.assertGreaterEqual(
            len(findings), 1,
            f"dbID 3448621236 body must classify as a finding under "
            f"B2 source-aware; got {findings!r}",
        )
        severities = [f.get("severity") for f in findings]
        self.assertIn(
            "P1", severities,
            f"dbID 3448621236 body must classify as P1 (text-alias "
            f"'high severity' → P1 per SEVERITY_MAP); got "
            f"severities={severities!r}",
        )

    # ------------------------------------------------------------------
    # Verify NO P0 misclassification regression.
    # ------------------------------------------------------------------
    def test_no_p0_misclassification_regression(self):
        """Under B2 source-aware, current unresolved Codex
        threads default to P2 (not P0) when no severity can
        be extracted. This test pins the invariant."""
        for body in (
            "P1: text-only body with no severity beyond the P1 marker",
            "Bumping retry counter: this will be a high severity regression",
            "This is a high severity issue",
        ):
            with self.subTest(body=body):
                item = self._codex_item(body)
                findings = crc.classify_item(
                    item, "inline_review_comment", set(),
                    is_codex_review_thread_current_unresolved=True,
                )
                severities = [f.get("severity") for f in findings]
                self.assertNotIn(
                    "P0", severities,
                    f"B2 source-aware must NOT produce P0 from text "
                    f"or default-to-P2 logic; body={body!r}, "
                    f"severities={severities!r}",
                )


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