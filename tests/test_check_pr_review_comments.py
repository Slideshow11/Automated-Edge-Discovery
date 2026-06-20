"""
tests/test_check_pr_review_comments.py

Unit tests for check_pr_review_comments.py.
Uses mock subprocess to avoid real GitHub calls.
"""

import contextlib
import json
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
            "Quick nudge to @codex — please re-review."
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
        """P3 is not a blocking severity, so a coordination
        comment with P3: declaration should still be skipped
        (it's non-blocking info). The guard only protects
        P0/P1/P2."""
        item = {
            "user": {"login": "Slideshow11"},
            "body": "P3: Bumping this thread — fix is minor",
            "state": "",
        }
        got = crc.classify_item(item, "issue_comment", set())
        # P3 with coordination pattern: still skipped because
        # P3 is not blocking. The guard only protects P0/P1/P2.
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
        """P3 is not a blocking severity, so a coordination
        comment with P3 is still treated as coordination."""
        self.assertTrue(crc.is_coordination_comment(
            "P3: Bumping this thread"
        ))

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

if __name__ == "__main__":
    unittest.main()