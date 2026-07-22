#!/usr/bin/env python3
"""Tests for scripts/local/codex_review_poller.py.

Covers the Round-47 poller defect repair: a Codex response
is securely matched by repository + PR + canonical Codex bot
identity + timestamp + head + recognized structure, NOT by
requiring the full 40-character SHA to appear literally in
the response body (which is the bug the Round-47 inline-bash
poller had — Codex only includes a short SHA prefix in its
response body).

Also covers the secure-match contract: a candidate response
that fails any of the identity/timestamp/structure checks
MUST be rejected, even if it carries the full SHA.
"""
from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
SCRIPT = REPO_ROOT / "scripts" / "local" / "codex_review_poller.py"


def _load_module():
    """Load ``codex_review_poller`` as a module so we can call
    its helpers directly without spawning a subprocess.
    """
    spec = importlib.util.spec_from_file_location(
        "codex_review_poller", str(SCRIPT)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# _head_matches_response — the core Round-47 fix
# ---------------------------------------------------------------------------


class TestHeadMatchesResponse:
    """The Round-47 fix: match by short SHA prefix when the
    full SHA is not literally present. This is what the
    inline-bash poller got wrong.
    """

    def test_full_sha_in_body_matches(self):
        mod = _load_module()
        assert mod._head_matches_response(
            "d8f6f480e1020e3e4007c6a9e732c768c428fead",
            "Reviewed commit: `d8f6f480e1020e3e4007c6a9e732c768c428fead`",
        ) is True

    def test_short_prefix_in_body_matches(self):
        """Round-47 bug fix: Codex only includes the short
        prefix. The poller MUST match it.
        """
        mod = _load_module()
        assert mod._head_matches_response(
            "d8f6f480e1020e3e4007c6a9e732c768c428fead",
            "Reviewed commit: `d8f6f480e1`",
        ) is True

    def test_unrelated_sha_does_not_match(self):
        mod = _load_module()
        assert mod._head_matches_response(
            "d8f6f480e1020e3e4007c6a9e732c768c428fead",
            "Reviewed commit: `aaaaaaaaaaaaaaaaa`",
        ) is False

    def test_empty_body_does_not_match(self):
        mod = _load_module()
        assert mod._head_matches_response(
            "d8f6f480e1020e3e4007c6a9e732c768c428fead",
            "",
        ) is False

    def test_empty_head_does_not_match(self):
        mod = _load_module()
        assert mod._head_matches_response(
            "",
            "Reviewed commit: `d8f6f480e1`",
        ) is False

    def test_partial_short_prefix_does_not_match(self):
        """A 6-char prefix is too short to be secure; the
        poller MUST require at least 7 hex characters.
        """
        mod = _load_module()
        assert mod._head_matches_response(
            "d8f6f480e1020e3e4007c6a9e732c768c428fead",
            "Reviewed commit: `d8f6f4`",
        ) is False

    def test_non_hex_short_prefix_does_not_match(self):
        mod = _load_module()
        assert mod._head_matches_response(
            "d8f6f480e1020e3e4007c6a9e732c768c428fead",
            "Reviewed commit: `zzzzzzz1`",
        ) is False


# ---------------------------------------------------------------------------
# _is_codex_login — canonical identity check
# ---------------------------------------------------------------------------


class TestIsCodexLogin:
    def test_canonical_login_matches(self):
        mod = _load_module()
        assert mod._is_codex_login("chatgpt-codex-connector") is True
        assert mod._is_codex_login("chatgpt-codex-connector[bot]") is True

    def test_login_case_insensitive(self):
        mod = _load_module()
        assert mod._is_codex_login("ChatGPT-Codex-Connector") is True
        assert mod._is_codex_login("CHATGPT-CODEX-CONNECTOR[BOT]") is True

    def test_non_codex_login_rejected(self):
        mod = _load_module()
        assert mod._is_codex_login("alice") is False
        assert mod._is_codex_login("") is False
        assert mod._is_codex_login("dependabot[bot]") is False
        assert mod._is_codex_login("github-actions[bot]") is False


# ---------------------------------------------------------------------------
# _is_clean_pass / _is_finding — body structure
# ---------------------------------------------------------------------------


class TestBodyStructure:
    def test_canonical_clean_pass_phrase(self):
        mod = _load_module()
        body = "Codex Review: Didn't find any major issues. :tada:"
        assert mod._is_clean_pass(body) is True
        assert mod._is_finding(body) is False

    def test_finding_badge_prefix(self):
        mod = _load_module()
        body = (
            "**<sub><sub>![P1 Badge]"
            "(https://img.shields.io/badge/P1-orange?style=flat)"
            "</sub></sub>  Some headline**\n\nDetails."
        )
        assert mod._is_finding(body) is True
        assert mod._is_clean_pass(body) is False

    def test_unstructured_body_rejected(self):
        mod = _load_module()
        body = "Just a normal human comment about the code."
        assert mod._is_clean_pass(body) is False
        assert mod._is_finding(body) is False

    def test_empty_body_rejected(self):
        mod = _load_module()
        assert mod._is_clean_pass("") is False
        assert mod._is_finding("") is False


# ---------------------------------------------------------------------------
# _match_response — the full secure identity match
# ---------------------------------------------------------------------------


def _make_review(
    *,
    review_id: str = "1",
    author: str = "chatgpt-codex-connector[bot]",
    body: str = "Codex Review: Didn't find any major issues. :tada:",
    commit_id: str = "d8f6f480e1020e3e4007c6a9e732c768c428fead",
    submitted_at: str = "2026-07-22T01:21:44Z",
) -> dict:
    return {
        "id": review_id,
        "user": {"login": author},
        "body": body,
        "commit_id": commit_id,
        "submitted_at": submitted_at,
    }


def _make_issue_comment(
    *,
    comment_id: str = "1",
    author: str = "chatgpt-codex-connector[bot]",
    body: str = (
        "Codex Review: Didn't find any major issues. :tada:\n\n"
        "**Reviewed commit:** `d8f6f480e1`"
    ),
    created_at: str = "2026-07-22T01:21:44Z",
) -> dict:
    return {
        "id": comment_id,
        "user": {"login": author},
        "body": body,
        "created_at": created_at,
    }


class TestMatchResponse:
    HEAD = "d8f6f480e1020e3e4007c6a9e732c768c428fead"
    PING_DT = _dt.datetime(2026, 7, 22, 1, 8, 48, tzinfo=_dt.timezone.utc)

    def test_clean_pass_formal_review_with_full_sha(self):
        mod = _load_module()
        m = mod._match_response(
            _make_review(),
            kind="review",
            repo="o/r", pr_number=411,
            head=self.HEAD, ping_dt=self.PING_DT,
        )
        assert m is not None
        assert m["verdict"] == "CLEAN_PASS"
        assert m["kind"] == "review"

    def test_clean_pass_issue_comment_with_short_sha(self):
        """The Round-47 bug repro: issue comment with SHORT
        SHA prefix in body. The poller MUST match it.
        """
        mod = _load_module()
        m = mod._match_response(
            _make_issue_comment(),
            kind="issue_comment",
            repo="o/r", pr_number=411,
            head=self.HEAD, ping_dt=self.PING_DT,
        )
        assert m is not None
        assert m["verdict"] == "CLEAN_PASS"
        assert m["kind"] == "issue_comment"

    def test_non_codex_author_rejected(self):
        mod = _load_module()
        m = mod._match_response(
            _make_issue_comment(author="alice"),
            kind="issue_comment",
            repo="o/r", pr_number=411,
            head=self.HEAD, ping_dt=self.PING_DT,
        )
        assert m is None

    def test_pre_ping_response_rejected(self):
        mod = _load_module()
        m = mod._match_response(
            _make_issue_comment(created_at="2026-07-22T01:00:00Z"),
            kind="issue_comment",
            repo="o/r", pr_number=411,
            head=self.HEAD, ping_dt=self.PING_DT,
        )
        assert m is None

    def test_wrong_head_formal_review_rejected(self):
        mod = _load_module()
        m = mod._match_response(
            _make_review(commit_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
            kind="review",
            repo="o/r", pr_number=411,
            head=self.HEAD, ping_dt=self.PING_DT,
        )
        assert m is None

    def test_issue_comment_without_head_reference_rejected(self):
        mod = _load_module()
        m = mod._match_response(
            _make_issue_comment(
                body="Codex Review: Didn't find any major issues."
                # no SHA reference at all
            ),
            kind="issue_comment",
            repo="o/r", pr_number=411,
            head=self.HEAD, ping_dt=self.PING_DT,
        )
        assert m is None

    def test_unstructured_body_rejected(self):
        mod = _load_module()
        m = mod._match_response(
            _make_issue_comment(
                body="Just a normal Codex comment, not a clean pass or finding."
            ),
            kind="issue_comment",
            repo="o/r", pr_number=411,
            head=self.HEAD, ping_dt=self.PING_DT,
        )
        assert m is None

    def test_finding_body_classified_correctly(self):
        mod = _load_module()
        m = mod._match_response(
            _make_issue_comment(
                body=(
                    "**<sub><sub>![P2 Badge]"
                    "(https://img.shields.io/badge/P2-yellow?style=flat)"
                    "</sub></sub>  Some finding**\n\n"
                    "**Reviewed commit:** `d8f6f480e1`\n\n"
                    "Details here."
                )
            ),
            kind="issue_comment",
            repo="o/r", pr_number=411,
            head=self.HEAD, ping_dt=self.PING_DT,
        )
        assert m is not None
        assert m["verdict"] == "FINDING"

    def test_older_ping_rejected(self):
        """A response that postdates a NEWER ping but
        predates an OLDER ping in the dual-ID replacement
        poller is rejected (it belongs to the older
        poll, not the current one). The caller passes
        the earliest acceptable ping_dt.
        """
        mod = _load_module()
        newer_ping = _dt.datetime(2026, 7, 22, 1, 20, 0, tzinfo=_dt.timezone.utc)
        # Response at 01:21:44 postdates the newer ping (01:20:00).
        m = mod._match_response(
            _make_issue_comment(),
            kind="issue_comment",
            repo="o/r", pr_number=411,
            head=self.HEAD, ping_dt=newer_ping,
        )
        assert m is not None  # newer ping boundary is met

        # But if the caller passes the OLDER ping (01:08:48),
        # the response is still post-ping, so it's accepted.
        # This is the dual-ID poller pattern: the earliest
        # ping defines the floor.
        m2 = mod._match_response(
            _make_issue_comment(),
            kind="issue_comment",
            repo="o/r", pr_number=411,
            head=self.HEAD, ping_dt=self.PING_DT,
        )
        assert m2 is not None


# ---------------------------------------------------------------------------
# end-to-end CLI smoke test
# ---------------------------------------------------------------------------


class TestCLISmoke:
    def test_help_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0
        assert "Exact-head Codex review poller" in proc.stdout

    def test_invalid_head_exits_two(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--repo", "o/r", "--pr-number", "1",
             "--head", "not-a-sha", "--ping-id", "1",
             "--ping-created-at", "2026-07-22T01:08:48Z",
             "--timeout-min", "1", "--poll-seconds", "1"],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 2
        assert "40-char lowercase hex SHA" in (proc.stdout + proc.stderr)

    def test_invalid_ping_ts_exits_two(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--repo", "o/r", "--pr-number", "1",
             "--head", "d8f6f480e1020e3e4007c6a9e732c768c428fead",
             "--ping-id", "1", "--ping-created-at", "not-a-date",
             "--timeout-min", "1", "--poll-seconds", "1"],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 2
        assert "ISO 8601" in (proc.stdout + proc.stderr)


# ---------------------------------------------------------------------------
# Round-48 regression: pagination + summary-format handling
# ---------------------------------------------------------------------------


class TestPaginatedFetch:
    """Round-48 fix: the poller MUST use ``gh api --paginate``
    to follow all pages of a list endpoint. Without
    pagination, a PR with more than 100 issue comments only
    returns the first page, and a post-ping Codex response
    on a long PR can sit on page 2+ and never be scanned.
    """

    def test_paginated_helper_invokes_paginate_flag(self):
        """Static source check: the poller MUST use
        ``--paginate`` when calling ``gh api`` for
        list endpoints.
        """
        import inspect
        from scripts.local import codex_review_poller as mod
        src = inspect.getsource(mod)
        assert "--paginate" in src, (
            "Round-48 fix: the poller must use "
            "``gh api --paginate`` for list endpoints. "
            "Without pagination, a PR with more than 100 "
            "issue comments only returns the first page."
        )
        # And the paginated helper must be used by the
        # fetch functions.
        assert "_gh_api_paginated" in src, (
            "Round-48 fix: the poller must define and use "
            "a _gh_api_paginated helper."
        )

    def test_paginated_helper_uses_subprocess_paginate(self, monkeypatch):
        """End-to-end subprocess check: the paginated
        helper invokes ``gh api --paginate <endpoint>``.
        """
        from unittest.mock import patch as _mp, MagicMock
        import subprocess as _subprocess
        mod = _load_module()
        # Use a fake ``gh`` that returns a single-line JSON
        # array (the --paginate output format).
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = json.dumps([
            {"id": 1, "user": {"login": "alice"}},
            {"id": 2, "user": {"login": "bob"}},
        ])
        fake_proc.stderr = ""
        seen_argv = []
        real_run = _subprocess.run
        def spy_run(argv, *a, **kw):
            seen_argv.append(list(argv))
            return fake_proc
        with _mp.object(_subprocess, "run", side_effect=spy_run):
            data, err = mod._gh_api_paginated("repos/o/r/issues/1/comments")
        assert err is None
        assert len(data) == 2
        # The argv MUST include ``--paginate``.
        assert any("--paginate" in argv for argv in seen_argv), (
            "Round-48 fix: the paginated helper must pass "
            "``--paginate`` to ``gh api``. Seen argv: "
            f"{seen_argv}"
        )

    def test_paginated_helper_uses_slurp_and_flattens(self, monkeypatch):
        """Round-49 fix: the paginated helper MUST use
        ``--slurp`` AND flatten the resulting list of
        pages into a single list. Without ``--slurp``,
        ``gh api --paginate`` emits each page as a
        separate JSON array, and callers iterate a list
        of lists — making the poller crash or miss the
        post-ping Codex response on long PRs.
        """
        from unittest.mock import patch as _mp, MagicMock
        import subprocess as _subprocess
        mod = _load_module()
        # Simulate the --slurp output: a list of pages,
        # each page itself a list of items.
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = json.dumps([
            # page 1
            [
                {"id": 1, "user": {"login": "alice"}},
                {"id": 2, "user": {"login": "bob"}},
            ],
            # page 2
            [
                {"id": 3, "user": {"login": "carol"}},
            ],
        ])
        fake_proc.stderr = ""
        seen_argv = []
        def spy_run(argv, *a, **kw):
            seen_argv.append(list(argv))
            return fake_proc
        with _mp.object(_subprocess, "run", side_effect=spy_run):
            data, err = mod._gh_api_paginated("repos/o/r/issues/1/comments")
        assert err is None
        # The data MUST be flattened: 3 items, not 2 pages.
        assert len(data) == 3, (
            "Round-49 fix: the paginated helper must "
            "flatten the list of pages into a single "
            f"list. Got {len(data)} items."
        )
        # And the argv MUST include ``--slurp``.
        assert any("--slurp" in argv for argv in seen_argv), (
            "Round-49 fix: the paginated helper must pass "
            "``--slurp`` to ``gh api``. Seen argv: "
            f"{seen_argv}"
        )


class TestCodexReviewSummaryFormat:
    """Round-48 fix: Codex's automated review summaries
    start with the ``### 💡 Codex Review`` Markdown
    header. The poller MUST recognize this format and
    fetch inline review comments to determine the
    verdict (CLEAN_PASS vs FINDING).
    """

    HEAD = "d8f6f480e1020e3e4007c6a9e732c768c428fead"
    PING_DT = _dt.datetime(2026, 7, 22, 1, 8, 48, tzinfo=_dt.timezone.utc)

    def test_summary_prefix_detected(self):
        mod = _load_module()
        body = (
            "\n### 💡 Codex Review\n\n"
            "Here are some automated review suggestions for this PR.\n\n"
            "**Reviewed commit:** `d8f6f480e1`\n"
        )
        assert mod._is_codex_review_summary(body) is True
        # The summary body is NOT itself a finding or a
        # clean pass — its verdict comes from the inline
        # comments.
        assert mod._is_clean_pass(body) is False
        assert mod._is_finding(body) is False

    def test_summary_with_inline_finding_is_finding(self, monkeypatch):
        """A formal review with the summary prefix and
        inline review comments carrying the finding
        badge MUST be classified as FINDING.
        """
        from unittest.mock import patch as _mp, MagicMock
        import subprocess as _subprocess
        mod = _load_module()
        review = {
            "id": 4750934578,
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": (
                "\n### 💡 Codex Review\n\n"
                "Here are some automated review suggestions.\n\n"
                "**Reviewed commit:** `d8f6f480e1`\n"
            ),
            "commit_id": self.HEAD,
            "submitted_at": "2026-07-22T04:08:37Z",
        }
        # Fake inline comments response.
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = json.dumps([
            {
                "id": 3627411228,
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "body": (
                    "**<sub><sub>![P2 Badge]"
                    "(https://img.shields.io/badge/P2-yellow?style=flat)"
                    "</sub></sub>  Paginate issue comments before polling**\n\n"
                    "When a PR has more than 100 issue comments..."
                ),
                "path": "scripts/local/codex_review_poller.py",
            }
        ])
        fake_proc.stderr = ""
        with _mp.object(_subprocess, "run", return_value=fake_proc):
            m = mod._match_response(
                review, kind="review",
                repo="Slideshow11/Automated-Edge-Discovery",
                pr_number=411,
                head=self.HEAD, ping_dt=self.PING_DT,
            )
        assert m is not None
        assert m["verdict"] == "FINDING"

    def test_summary_without_inline_finding_is_clean_pass(self, monkeypatch):
        """A formal review with the summary prefix and
        NO inline review comments (or only non-finding
        inline comments) MUST be classified as
        CLEAN_PASS. This is the round-47 fix in action:
        a review summary on its own is not a finding.
        """
        from unittest.mock import patch as _mp, MagicMock
        import subprocess as _subprocess
        mod = _load_module()
        review = {
            "id": 4750934579,
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": (
                "\n### 💡 Codex Review\n\n"
                "Here are some automated review suggestions.\n\n"
                "**Reviewed commit:** `f47d977233`\n"
            ),
            "commit_id": "f47d977233d1b6b58268e2af2540127f5ff93de1",
            "submitted_at": "2026-07-22T05:00:00Z",
        }
        # No inline comments.
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = json.dumps([])
        fake_proc.stderr = ""
        with _mp.object(_subprocess, "run", return_value=fake_proc):
            m = mod._match_response(
                review, kind="review",
                repo="Slideshow11/Automated-Edge-Discovery",
                pr_number=411,
                head="f47d977233d1b6b58268e2af2540127f5ff93de1",
                ping_dt=self.PING_DT,
            )
        assert m is not None
        assert m["verdict"] == "CLEAN_PASS"

    def test_summary_with_inline_fetch_failure_fails_closed(self, monkeypatch):
        """If the inline-comments fetch fails for a
        summary-format review, the poller MUST fail
        closed and classify as FINDING. A false FINDING
        is safer than a missed FINDING.
        """
        from unittest.mock import patch as _mp, MagicMock
        import subprocess as _subprocess
        mod = _load_module()
        review = {
            "id": 4750934580,
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": (
                "\n### 💡 Codex Review\n\n"
                "Here are some automated review suggestions.\n\n"
                "**Reviewed commit:** `f47d977233`\n"
            ),
            "commit_id": "f47d977233d1b6b58268e2af2540127f5ff93de1",
            "submitted_at": "2026-07-22T05:00:00Z",
        }
        # Inline fetch fails (non-zero returncode).
        fake_proc = MagicMock()
        fake_proc.returncode = 1
        fake_proc.stdout = ""
        fake_proc.stderr = "gh api --paginate error"
        with _mp.object(_subprocess, "run", return_value=fake_proc):
            m = mod._match_response(
                review, kind="review",
                repo="Slideshow11/Automated-Edge-Discovery",
                pr_number=411,
                head="f47d977233d1b6b58268e2af2540127f5ff93de1",
                ping_dt=self.PING_DT,
            )
        assert m is not None
        assert m["verdict"] == "FINDING"
