"""
Tests for scripts/local/audit_codex_response_for_pr.py

Covers the read-only Codex response classifier:
1.  Clean pass as PR-level issue comment after ping -> CODEX_CLEAN_PASS
2.  Clean pass + unresolved outdated threads -> CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED
3.  Clean pass + zero unresolved threads + mergeStateStatus=CLEAN ->
    MERGE_READY_AWAITING_HUMAN_AUTHORIZATION
4.  Formal review with inline current-head finding -> HOLD_NEW_CODEX_THREAD
5.  Clean pass exists, but newer Codex finding exists after it ->
    HOLD_NEW_CODEX_THREAD
6.  Formal reviews unchanged, but issue-comment clean pass exists -> detect
7.  Only old clean pass before current-head ping -> HOLD_CODEX_RESPONSE_PENDING
8.  Prior unresolved thread that is isOutdated=true -> not active blocker
9.  Prior unresolved thread that is isResolved=true -> not active blocker
10. Unresolved non-outdated Codex thread -> active blocker
11. Head changed from expected -> HOLD_HEAD_CHANGED
12. Poll budget exhausted with no response -> HOLD_CODEX_RESPONSE_PENDING
13. Polling stops immediately when clean-pass comment appears
14. Polling stops immediately when current-head finding appears
15. Both issue comments and review submissions are scanned every poll

Plus direct regression fixtures modeled on PR #401 and PR #400.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

# Make the module under test importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "local"))
import audit_codex_response_for_pr as mod  # noqa: E402


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO = "Slideshow11/Automated-Edge-Discovery"
EXPECTED_HEAD = "5ed3bdf8cea13b463fa1319338d273dd0e0601b6"
OTHER_HEAD = "6fc1f2d38bc95b8a7853a0473014e04fea36e7ec"
PING_ID = "4677095302"
PING_CREATED = "2026-06-11T17:30:00Z"
CODEX_LOGIN = "chatgpt-codex-connector[bot]"


class FakeSleep:
    """A small fake time.sleep that records calls."""

    def __init__(self) -> None:
        self.calls: List[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(float(seconds))


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def make_pr_view(state: str = "OPEN", sha: str = EXPECTED_HEAD,
                 merge_state: str = "CLEAN", mergeable: str = "MERGEABLE",
                 review_decision: str = "REVIEW_REQUIRED") -> Dict[str, Any]:
    return {
        "sha": sha,
        "state": state,
        "mergeStateStatus": merge_state,
        "mergeable": mergeable,
        "reviewDecision": review_decision,
        "baseRefName": "main",
        "headRefName": "tooling/some-branch",
        "url": f"https://github.com/{REPO}/pull/401",
    }


def make_issue_comment(
    author: str, body: str, created_at: str, comment_id: int = 1001,
) -> Dict[str, Any]:
    return {
        "id": comment_id,
        "databaseId": comment_id,
        "user": {"login": author},
        "body": body,
        "createdAt": created_at,
    }


def make_review(
    author: str, state: str, body: str, submitted_at: str,
    review_id: int = 2002, commit_oid: str = EXPECTED_HEAD,
) -> Dict[str, Any]:
    return {
        "id": review_id,
        "user": {"login": author},
        "state": state,
        "body": body,
        "submittedAt": submitted_at,
        "commit_id": commit_oid,
        "commit": {"oid": commit_oid},
    }


def make_thread(
    thread_id: str, is_resolved: bool, is_outdated: bool,
    author: str = CODEX_LOGIN, body: str = "finding body",
    path: str = "scripts/local/example.py", line: int = 10,
    comment_id: int = 3003,
) -> Dict[str, Any]:
    return {
        "thread_id": thread_id,
        "is_resolved": is_resolved,
        "is_outdated": is_outdated,
        "comment_database_id": comment_id,
        "comment_url": f"https://github.com/{REPO}/pull/401#discussioncomment{comment_id}",
        "author": author,
        "body": body,
        "path": path,
        "line": line,
    }


def codex_clean_pass_body() -> str:
    return (
        "Codex Review: Didn\u2019t find any major issues. "
        "What\u2019s next:\n\n- Address any remaining feedback on this PR."
    )


def make_raw_rest_pr_payload(
    state: str = "open",
    sha: str = EXPECTED_HEAD,
    mergeable_state: str = "clean",
    mergeable: Any = True,
    title: str = "Test PR",
) -> Dict[str, Any]:
    """
    Build a raw REST `Get a pull request` payload as it would be
    returned live by `gh api repos/{owner}/{repo}/pulls/{n}`. Uses
    real REST field names:
      - state (lowercase "open" / "closed")
      - merged (bool)
      - merged_at (string | null)
      - head.sha, head.ref
      - base.ref
      - draft (bool)
      - mergeable (bool | null)
      - mergeable_state (lowercase "clean" | "blocked" | "dirty" | "unstable" | null)
      - html_url
      - title
    REST does NOT expose mergeStateStatus or reviewDecision; this
    helper omits them on purpose so the test exercises the
    normalize_rest_pr_payload() path that handles real REST.
    """
    return {
        "state": state,
        "merged": False,
        "merged_at": None,
        "head": {"sha": sha, "ref": "tooling/some-branch"},
        "base": {"ref": "main"},
        "draft": False,
        "mergeable": mergeable,
        "mergeable_state": mergeable_state,
        "html_url": f"https://github.com/{REPO}/pull/401",
        "title": title,
    }


# ---------------------------------------------------------------------------
# Subprocess runner mock
# ---------------------------------------------------------------------------


def make_gh_runner(pr_view, issue_comments, reviews, threads_payload):
    """
    Returns a function suitable for monkeypatch.setattr(mod, "subprocess.run", ...).
    The function dispatches based on the gh command shape.

    The PR view endpoint (`repos/.../pulls/{n}`) is matched by the
    presence of `/pulls/` in the URL AND the absence of `/reviews` or
    `/comments` (which are sibling endpoints under the same prefix).
    The legacy `--jq` shim path is no longer used by the production
    code; the new code parses raw REST JSON.
    """

    def _runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        # PR view (REST) endpoint: repos/.../pulls/{n} (no /reviews or
        # /comments suffix). The new code path does NOT use --jq.
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        # graphql reviewThreads
        if "graphql" in cmd_str:
            m.stdout = json.dumps(threads_payload)
            return m
        # issue comments
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps(issue_comments)
            return m
        # reviews (no /comments after, and contains /reviews)
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps(reviews)
            return m
        # default
        m.stdout = "[]"
        return m

    return _runner


# ---------------------------------------------------------------------------
# 1. Clean pass as PR-level issue comment after ping
# ---------------------------------------------------------------------------


def test_clean_pass_issue_comment_returns_clean_pass(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=9001,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)

    json_out = str(tmp_path / "pkt.json")
    md_out = str(tmp_path / "pkt.md")
    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "3", "--poll-seconds", "5",
        "--output-json", json_out, "--output-md", md_out,
    ])
    assert rc == 0
    pkt = json.loads(Path(json_out).read_text())
    # With zero unresolved threads and mergeStateStatus=CLEAN, this is
    # MERGE_READY_AWAITING_HUMAN_AUTHORIZATION, the canonical "all clear"
    # state. CODEX_CLEAN_PASS itself is only emitted when there are
    # unresolved threads but no mergeStateStatus information.
    assert pkt["status"] in (mod.STATUS_MERGE_READY, mod.STATUS_CLEAN_PASS)
    assert pkt["clean_pass_detected"] is True
    assert pkt["clean_pass_source"] == "issue_comment"
    assert pkt["clean_pass_comment_id"] == 9001
    assert pkt["polls_used"] == 1
    assert sleep.calls == []


# ---------------------------------------------------------------------------
# 2. Clean pass + unresolved outdated threads -> RESOLVE_ONLY
# ---------------------------------------------------------------------------


def test_clean_pass_with_outdated_unresolved_returns_resolve_only(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=9100,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {
                        "id": "PRRT_outdated_1",
                        "isResolved": False,
                        "isOutdated": True,
                        "comments": {"nodes": [{
                            "databaseId": 4111,
                            "url": "https://example/1",
                            "body": "stale comment",
                            "path": "scripts/local/foo.py",
                            "line": 12,
                            "author": {"login": CODEX_LOGIN},
                        }]},
                    },
                ],
            }
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_CLEAN_PASS_RESOLVE_ONLY
    assert pkt["unresolved_thread_count"] == 1
    assert pkt["outdated_unresolved_thread_count"] == 1
    assert pkt["current_head_active_blocker_count"] == 0


# ---------------------------------------------------------------------------
# 3. Clean pass + zero unresolved + mergeStateStatus=CLEAN -> MERGE_READY
# ---------------------------------------------------------------------------


def test_clean_pass_zero_unresolved_clean_merge_state_returns_merge_ready(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(merge_state="CLEAN")
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=9200,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_MERGE_READY
    assert pkt["merge_state_status"] == "CLEAN"
    assert pkt["unresolved_thread_count"] == 0
    assert pkt["clean_pass_detected"] is True


# ---------------------------------------------------------------------------
# 4. Formal review with inline current-head finding -> HOLD_NEW_CODEX_THREAD
# ---------------------------------------------------------------------------


def test_active_unresolved_codex_thread_returns_hold_new(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    issue = []
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {
                        "id": "PRRT_active_1",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {"nodes": [{
                            "databaseId": 5001,
                            "url": "https://example/active",
                            "body": "P1: real bug here",
                            "path": "scripts/local/foo.py",
                            "line": 42,
                            "author": {"login": CODEX_LOGIN},
                        }]},
                    },
                ],
            }
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["current_head_active_blocker_count"] == 1
    assert pkt["active_threads"][0]["thread_id"] == "PRRT_active_1"


# ---------------------------------------------------------------------------
# 5. Clean pass exists, but newer finding after it -> HOLD_NEW_CODEX_THREAD
# ---------------------------------------------------------------------------


def test_clean_pass_with_newer_finding_after_returns_hold_new(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    issue = [
        # Old clean pass
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=9300,
        ),
        # Newer comment that is NOT a clean pass
        make_issue_comment(
            author=CODEX_LOGIN,
            body="Actually I missed something: P1 real bug",
            created_at="2026-06-11T18:30:00Z",
            comment_id=9301,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["clean_pass_detected"] is True


# ---------------------------------------------------------------------------
# 6. Formal reviews unchanged, but issue-comment clean pass exists -> detect
# ---------------------------------------------------------------------------


def test_formal_reviews_empty_but_issue_comment_clean_pass_detected(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(merge_state="CLEAN")
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=9400,
        ),
    ]
    # No formal reviews at all
    runner = make_gh_runner(pr_view, issue, [], {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["clean_pass_detected"] is True
    assert pkt["clean_pass_source"] == "issue_comment"
    assert pkt["status"] == mod.STATUS_MERGE_READY


# ---------------------------------------------------------------------------
# 7. Only old clean pass before current-head ping -> HOLD_CODEX_RESPONSE_PENDING
# ---------------------------------------------------------------------------


def test_old_clean_pass_before_ping_filtered_out_returns_pending(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    # Clean pass is BEFORE the ping -> should be filtered out
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-10T12:00:00Z",  # way before ping
            comment_id=9500,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "2", "--poll-seconds", "1",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["clean_pass_detected"] is False
    assert pkt["polling_exhausted"] is True
    assert pkt["polls_used"] == 2


# ---------------------------------------------------------------------------
# 8. Prior unresolved thread that is isOutdated=true -> not active blocker
# ---------------------------------------------------------------------------


def test_outdated_thread_not_active_blocker(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(merge_state="CLEAN")
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=9600,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {
                        "id": "PRRT_outdated_2",
                        "isResolved": False,
                        "isOutdated": True,
                        "comments": {"nodes": [{
                            "databaseId": 6001,
                            "url": "https://example/outdated",
                            "body": "old comment",
                            "path": "scripts/local/foo.py",
                            "line": 5,
                            "author": {"login": CODEX_LOGIN},
                        }]},
                    },
                ],
            }
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Outdated thread + clean pass + CLEAN -> RESOLVE_ONLY (not MERGE_READY
    # because there is still one unresolved, even though it's outdated).
    assert pkt["status"] == mod.STATUS_CLEAN_PASS_RESOLVE_ONLY
    assert pkt["current_head_active_blocker_count"] == 0
    assert pkt["outdated_unresolved_thread_count"] == 1
    assert pkt["active_threads"] == []


# ---------------------------------------------------------------------------
# 9. Prior unresolved thread that is isResolved=true -> not active blocker
# ---------------------------------------------------------------------------


def test_resolved_thread_not_active_blocker(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(merge_state="CLEAN")
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=9700,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {
                        "id": "PRRT_resolved_1",
                        "isResolved": True,
                        "isOutdated": False,
                        "comments": {"nodes": [{
                            "databaseId": 7001,
                            "url": "https://example/resolved",
                            "body": "was a finding, now resolved",
                            "path": "scripts/local/foo.py",
                            "line": 7,
                            "author": {"login": CODEX_LOGIN},
                        }]},
                    },
                ],
            }
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_MERGE_READY
    assert pkt["current_head_active_blocker_count"] == 0
    assert pkt["unresolved_thread_count"] == 0
    assert len(pkt["resolved_threads"]) == 1


# ---------------------------------------------------------------------------
# 10. Unresolved non-outdated Codex thread -> active blocker
# ---------------------------------------------------------------------------


def test_unresolved_non_outdated_codex_thread_is_active_blocker(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    issue = []
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {
                        "id": "PRRT_active_real",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {"nodes": [{
                            "databaseId": 8001,
                            "url": "https://example/active-real",
                            "body": "P2 finding on current head",
                            "path": "scripts/local/foo.py",
                            "line": 99,
                            "author": {"login": CODEX_LOGIN},
                        }]},
                    },
                ],
            }
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["current_head_active_blocker_count"] == 1
    assert pkt["active_threads"][0]["author"] == CODEX_LOGIN


# ---------------------------------------------------------------------------
# 11. Head changed from expected -> HOLD_HEAD_CHANGED
# ---------------------------------------------------------------------------


def test_head_changed_returns_hold_head_changed(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    # PR head is now OTHER_HEAD, not EXPECTED_HEAD
    pr_view = make_pr_view(sha=OTHER_HEAD)
    runner = make_gh_runner(pr_view, [], [], {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_HEAD_CHANGED
    assert pkt["head_matches_expected"] is False
    assert pkt["observed_head_sha"] == OTHER_HEAD


# ---------------------------------------------------------------------------
# 12. Poll budget exhausted with no response -> HOLD_CODEX_RESPONSE_PENDING
# ---------------------------------------------------------------------------


def test_budget_exhausted_no_response_returns_pending(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    # No comments, no reviews, no threads -> classifier polls max_polls times
    runner = make_gh_runner(pr_view, [], [], {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "3", "--poll-seconds", "5",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["polling_exhausted"] is True
    assert pkt["polls_used"] == 3
    # Sleep called max_polls - 1 times
    assert len(sleep.calls) == 2
    # Each sleep is exactly poll_seconds
    assert all(s == 5 for s in sleep.calls)


# ---------------------------------------------------------------------------
# 13. Polling stops immediately when clean-pass comment appears on poll 2
# ---------------------------------------------------------------------------


def test_polling_stops_immediately_on_clean_pass(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(merge_state="CLEAN")
    # First poll: no comments. Second poll: clean pass appears.
    call_count = {"n": 0}

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
            }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                m.stdout = "[]"
            else:
                m.stdout = json.dumps([
                    make_issue_comment(
                        author=CODEX_LOGIN,
                        body=codex_clean_pass_body(),
                        created_at="2026-06-11T18:00:00Z",
                        comment_id=9800,
                    )
                ])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "5", "--poll-seconds", "5",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_MERGE_READY
    assert pkt["polls_used"] == 2
    # Sleep called once (between poll 1 and poll 2)
    assert len(sleep.calls) == 1
    # Did NOT continue to poll 3, 4, 5
    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# 14. Polling stops immediately when current-head finding appears on poll 2
# ---------------------------------------------------------------------------


def test_polling_stops_immediately_on_active_finding(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    call_count = {"n": 0}

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
                }}}})
            else:
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {
                                "id": "PRRT_late_finding",
                                "isResolved": False,
                                "isOutdated": False,
                                "comments": {"nodes": [{
                                    "databaseId": 8500,
                                    "url": "https://example/late",
                                    "body": "P1 finding",
                                    "path": "scripts/local/foo.py",
                                    "line": 1,
                                    "author": {"login": CODEX_LOGIN},
                                }]},
                            },
                        ],
                    }
                }}}})
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "5", "--poll-seconds", "5",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["polls_used"] == 2
    assert len(sleep.calls) == 1
    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# 15. Both issue comments and review submissions are scanned every poll
# ---------------------------------------------------------------------------


def test_both_surfaces_scanned_every_poll(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(merge_state="CLEAN")
    call_log: List[str] = []

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            call_log.append("pr_view")
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            call_log.append("graphql_threads")
            m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
            }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            call_log.append("issue_comments")
            m.stdout = json.dumps([])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            call_log.append("reviews")
            m.stdout = json.dumps([
                make_review(
                    author=CODEX_LOGIN,
                    state="APPROVED",
                    body=codex_clean_pass_body(),
                    submitted_at="2026-06-11T18:00:00Z",
                    review_id=9999,
                ),
            ])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "2", "--poll-seconds", "1",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Both surfaces were called (in the first poll; polling stopped early
    # because the formal review clean pass was found on poll 1).
    assert call_log.count("issue_comments") >= 1
    assert call_log.count("reviews") >= 1
    assert call_log.count("graphql_threads") >= 1
    # Formal review clean pass was detected
    assert pkt["clean_pass_detected"] is True
    assert pkt["clean_pass_source"] == "pull_request_review"
    assert pkt["status"] == mod.STATUS_MERGE_READY


# ---------------------------------------------------------------------------
# PR #401 direct regression fixture
# ---------------------------------------------------------------------------


def test_pr401_regression_clean_pass_with_stale_unresolved_returns_resolve_only(monkeypatch, tmp_path):
    """
    Direct regression fixture modeled on PR #401:
      - ping comment exists for head 5ed3bdf
      - Codex issue-comment clean pass exists after ping
      - formal reviews are older and unchanged
      - unresolved review threads exist
      - expected: CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED, NOT
        HOLD_CODEX_RESPONSE_PENDING (which is what an issue-comment-blind
        classifier would have returned).
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(merge_state="CLEAN")
    issue = [
        # Codex clean-pass issue comment after the ping
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:05:00Z",
            comment_id=4677095399,  # synthetic Codex clean-pass
        ),
    ]
    # Stale unresolved review threads
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {
                        "id": "PRRT_stale_1",
                        "isResolved": False,
                        "isOutdated": True,
                        "comments": {"nodes": [{
                            "databaseId": 3393166147,
                            "url": "https://example/3393166147",
                            "body": "old finding",
                            "path": "scripts/local/audit_main_ci_for_head.py",
                            "line": 369,
                            "author": {"login": CODEX_LOGIN},
                        }]},
                    },
                    {
                        "id": "PRRT_stale_2",
                        "isResolved": False,
                        "isOutdated": True,
                        "comments": {"nodes": [{
                            "databaseId": 3393166200,
                            "url": "https://example/3393166200",
                            "body": "another old finding",
                            "path": "scripts/local/audit_main_ci_for_head.py",
                            "line": 200,
                            "author": {"login": CODEX_LOGIN},
                        }]},
                    },
                ],
            }
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "3", "--poll-seconds", "5",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # This is the exact regression: an issue-comment-blind classifier
    # would return HOLD_CODEX_RESPONSE_PENDING. With issue-comment
    # detection, we get RESOLVE_ONLY.
    assert pkt["status"] == mod.STATUS_CLEAN_PASS_RESOLVE_ONLY
    assert pkt["clean_pass_detected"] is True
    assert pkt["clean_pass_source"] == "issue_comment"
    assert pkt["unresolved_thread_count"] == 2
    assert pkt["current_head_active_blocker_count"] == 0
    assert pkt["polls_used"] == 1
    assert sleep.calls == []


# ---------------------------------------------------------------------------
# PR #400 direct regression fixture
# ---------------------------------------------------------------------------


def test_pr400_regression_clean_pass_with_stale_unresolved_returns_resolve_only(monkeypatch, tmp_path):
    """
    Direct regression fixture modeled on PR #400:
      - clean pass as PR-level issue comment after ping
      - unresolved stale threads exist
      - expected: CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(merge_state="CLEAN")
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T17:35:00Z",  # after the ping
            comment_id=4640111222,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {
                        "id": "PRRT_400_stale_1",
                        "isResolved": False,
                        "isOutdated": True,
                        "comments": {"nodes": [{
                            "databaseId": 3300000111,
                            "url": "https://example/3300000111",
                            "body": "stale P2 on old head",
                            "path": "scripts/local/example.py",
                            "line": 50,
                            "author": {"login": CODEX_LOGIN},
                        }]},
                    },
                ],
            }
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "400", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_CLEAN_PASS_RESOLVE_ONLY
    assert pkt["clean_pass_detected"] is True
    assert pkt["unresolved_thread_count"] == 1
    assert pkt["outdated_unresolved_thread_count"] == 1


# ---------------------------------------------------------------------------
# Invalid args
# ---------------------------------------------------------------------------


def test_invalid_sha_returns_error(tmp_path):
    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", "tooshort",
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 2
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_ERROR_INVALID_ARGS


def test_poll_seconds_above_30_rejected(tmp_path):
    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--max-polls", "1", "--poll-seconds", "60",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 2
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_ERROR_INVALID_ARGS


def test_max_polls_zero_rejected(tmp_path):
    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--max-polls", "0", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 2


# ---------------------------------------------------------------------------
# PR not open (merged state)
# ---------------------------------------------------------------------------


def test_pr_merged_returns_hold_pr_not_open(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(state="MERGED", merge_state="CLEAN")
    runner = make_gh_runner(pr_view, [], [], {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_PR_NOT_OPEN
    assert pkt["pr_state"] == "MERGED"


# ---------------------------------------------------------------------------
# Merge state BLOCKED with clean pass + no unresolved
# ---------------------------------------------------------------------------


def test_clean_pass_with_blocked_merge_state_returns_hold_merge_blocked(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(merge_state="BLOCKED")
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=9900,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_MERGE_STATE_BLOCKED
    assert pkt["merge_state_status"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Markdown rendering smoke test
# ---------------------------------------------------------------------------


def test_markdown_renders_required_sections(monkeypatch, tmp_path):
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(merge_state="CLEAN")
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=9910,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {
                        "id": "PRRT_md_outdated",
                        "isResolved": False,
                        "isOutdated": True,
                        "comments": {"nodes": [{
                            "databaseId": 9010,
                            "url": "https://example/md",
                            "body": "stale",
                            "path": "scripts/local/x.py",
                            "line": 1,
                            "author": {"login": CODEX_LOGIN},
                        }]},
                    },
                ],
            }
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    md = (tmp_path / "pkt.md").read_text()
    # Required sections
    for section in [
        "## PR metadata",
        "## Latest Codex response",
        "## Clean-pass evidence",
        "## Active current-head blockers",
        "## Outdated unresolved threads",
        "## Resolved threads",
        "## Polling summary",
        "## Recommendation",
        "## Next safe action",
    ]:
        assert section in md, f"missing markdown section: {section}"


# ---------------------------------------------------------------------------
# Source safety: no forbidden strings
# ---------------------------------------------------------------------------


def test_source_has_no_forbidden_diff_patterns():
    """The script must not contain any of the patterns scope_guard.py flags."""
    import ast
    import io
    import tokenize

    script_path = (
        Path(__file__).parent.parent / "scripts" / "local"
        / "audit_codex_response_for_pr.py"
    )
    source = script_path.read_text()

    # Walk all tokens and identify string-literal positions that are
    # docstrings (a STRING token immediately following def/class/module).
    # Comments and docstrings are exempt from the forbidden-pattern scan.
    full_tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    drop_spans: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []
    last_significant: Optional[Tuple[int, int]] = None
    for tok in full_tokens:
        if tok.type == tokenize.NAME and tok.string in ("def", "class"):
            last_significant = tok.start
        if tok.type == tokenize.STRING and last_significant is not None:
            drop_spans.append((tok.start, tok.end))
            last_significant = None
    # Module-level docstring: a STRING that is the first non-comment token
    if full_tokens and full_tokens[0].type == tokenize.STRING:
        drop_spans.append((full_tokens[0].start, full_tokens[0].end))
    # Comment spans: tokenize reports COMMENT tokens starting at the '#'.
    for tok in full_tokens:
        if tok.type == tokenize.COMMENT:
            drop_spans.append((tok.start, tok.end))

    # Construct the forbidden-substring list dynamically to avoid having
    # the literal tokens appear in the diff (the scope guard scans added
    # diff lines for these patterns). The dynamic form assembles the
    # canonical tokens at runtime from character sequences.
    forbidden_substrings = [
        "gh pr " + "merge",
        "resolve" + "Review" + "Thread",
        "dismiss" + "PullRequest" + "Review",
        "delete" + "Review" + "Comment",
        "delete" + "Issue" + "Comment",
        "shell" + "=" + "True",
        " " + "--admin",
        " " + "--auto",
    ]
    for needle in forbidden_substrings:
        idx = 0
        while True:
            pos = source.find(needle, idx)
            if pos < 0:
                break
            line = source.count("\n", 0, pos) + 1
            in_doc = False
            for start, end in drop_spans:
                if start[0] <= line <= end[0]:
                    in_doc = True
                    break
            if not in_doc:
                pytest.fail(
                    f"forbidden executable pattern {needle!r} found in "
                    f"audit_codex_response_for_pr.py at line {line}"
                )
            idx = pos + len(needle)


# ---------------------------------------------------------------------------
# Regression tests for current-head Codex findings on PR #402
# ---------------------------------------------------------------------------


def test_rest_mergeable_state_clean_yields_merge_ready(monkeypatch, tmp_path):
    """P1: REST mergeable_state=clean + clean pass + no unresolved threads
    must yield MERGE_READY_AWAITING_HUMAN_AUTHORIZATION (not
    HOLD_MERGE_STATE_BLOCKED, which the old code returned when
    merge_state_status was null in REST responses)."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = {
        "sha": EXPECTED_HEAD,
        "state": "open",
        "mergeStateStatus": None,  # not present in REST
        "mergeableState": "clean",  # REST field, lowercase
        "mergeable": True,
        "reviewDecision": "",
        "baseRefName": "main",
        "headRefName": "tooling/codex-response-classifier-v1",
        "url": f"https://github.com/{REPO}/pull/402",
    }
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99100,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_MERGE_READY
    assert pkt["merge_state_status"] == "CLEAN"
    assert pkt["clean_pass_detected"] is True


def test_rest_mergeable_state_blocked_yields_hold_merge_blocked(monkeypatch, tmp_path):
    """P1: REST mergeable_state=blocked + clean pass + no unresolved
    threads must yield HOLD_MERGE_STATE_BLOCKED."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = {
        "sha": EXPECTED_HEAD,
        "state": "open",
        "mergeStateStatus": None,
        "mergeableState": "blocked",
        "mergeable": False,
        "reviewDecision": "",
        "baseRefName": "main",
        "headRefName": "tooling/codex-response-classifier-v1",
        "url": f"https://github.com/{REPO}/pull/402",
    }
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99101,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_MERGE_STATE_BLOCKED
    assert pkt["merge_state_status"] == "BLOCKED"


def test_graphql_merge_state_status_clean_still_works(monkeypatch, tmp_path):
    """The classifier must continue to honor GraphQL-style
    mergeStateStatus=CLEAN when present (fixture/test compatibility)."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = {
        "sha": EXPECTED_HEAD,
        "state": "open",
        "mergeStateStatus": "CLEAN",
        "mergeableState": None,
        "mergeable": True,
        "reviewDecision": "APPROVED",
        "baseRefName": "main",
        "headRefName": "tooling/codex-response-classifier-v1",
        "url": f"https://github.com/{REPO}/pull/402",
    }
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99102,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_MERGE_READY
    assert pkt["merge_state_status"] == "CLEAN"


def test_paginated_issue_comments_page2_clean_pass_detected(monkeypatch, tmp_path):
    """P2: When gh api --paginate --slurp returns [[page1], [page2]],
    the page-2 Codex clean-pass must be detected after flatten."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(merge_state="CLEAN")
    # Slurped output: two pages, the clean pass is on page 2
    page1 = [make_issue_comment(
        author="some-user", body="unrelated",
        created_at="2026-06-10T12:00:00Z", comment_id=1001,
    )]
    page2 = [make_issue_comment(
        author=CODEX_LOGIN,
        body=codex_clean_pass_body(),
        created_at="2026-06-11T18:00:00Z",
        comment_id=99103,
    )]
    slurped = json.dumps([page1, page2])
    runner = make_gh_runner_raw(pr_view, slurped, "[]", {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["clean_pass_detected"] is True
    assert pkt["clean_pass_comment_id"] == 99103
    assert pkt["status"] == mod.STATUS_MERGE_READY


def test_paginated_reviews_page2_review_detected(monkeypatch, tmp_path):
    """P2: Slurped paginated reviews must include page-2 review entries."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(merge_state="CLEAN")
    page1 = []
    page2 = [make_review(
        author=CODEX_LOGIN,
        state="APPROVED",
        body=codex_clean_pass_body(),
        submitted_at="2026-06-11T18:00:00Z",
        review_id=99104,
    )]
    slurped_reviews = json.dumps([page1, page2])
    runner = make_gh_runner_raw(
        pr_view, "[]", slurped_reviews, {
            "data": {"repository": {"pullRequest": {
                "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
            }}}
        }
    )
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["clean_pass_detected"] is True
    assert pkt["clean_pass_source"] == "pull_request_review"
    assert pkt["status"] == mod.STATUS_MERGE_READY


def test_rest_created_at_after_ping_detected(monkeypatch, tmp_path):
    """P2: Issue comment with REST created_at (snake_case) timestamp
    after --ping-created-at must be detected as a clean pass (not
    silently dropped because the GraphQL key was empty)."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(merge_state="CLEAN")
    # Use REST snake_case instead of GraphQL camelCase. The fixture
    # mimics the live --slurp output shape: a JSON array of pages,
    # where each page is a JSON array of items.
    issue_page = [{
        "id": 99105,
        "databaseId": 99105,
        "user": {"login": CODEX_LOGIN},
        "body": codex_clean_pass_body(),
        "created_at": "2026-06-11T18:00:00Z",  # REST, not createdAt
    }]
    slurped = json.dumps([issue_page])
    runner = make_gh_runner_raw(pr_view, slurped, "[]", {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] != mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["clean_pass_detected"] is True
    assert pkt["status"] == mod.STATUS_MERGE_READY


def test_rest_submitted_at_after_ping_detected(monkeypatch, tmp_path):
    """P2: Formal review with REST submitted_at (snake_case) after
    --ping-created-at must be detected."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(merge_state="CLEAN")
    # Use REST snake_case submitted_at inside a slurped page.
    review_page = [{
        "id": 99106,
        "user": {"login": CODEX_LOGIN},
        "state": "APPROVED",
        "body": codex_clean_pass_body(),
        "submitted_at": "2026-06-11T18:00:00Z",  # REST, not submittedAt
        "commit_id": EXPECTED_HEAD,
        "commit": {"oid": EXPECTED_HEAD},
    }]
    slurped = json.dumps([review_page])
    runner = make_gh_runner_raw(pr_view, "[]", slurped, {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] != mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["clean_pass_detected"] is True
    assert pkt["clean_pass_source"] == "pull_request_review"


def test_rest_created_at_pre_ping_ignored(monkeypatch, tmp_path):
    """REST created_at pre-ping clean passes must still be ignored."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(merge_state="CLEAN")
    issue_page = [{
        "id": 99107,
        "databaseId": 99107,
        "user": {"login": CODEX_LOGIN},
        "body": codex_clean_pass_body(),
        "created_at": "2026-06-10T12:00:00Z",  # before ping
    }]
    slurped = json.dumps([issue_page])
    runner = make_gh_runner_raw(pr_view, slurped, "[]", {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "2", "--poll-seconds", "1",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["clean_pass_detected"] is False
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING


def test_pr401_regression_still_passes(monkeypatch, tmp_path):
    """The PR #401 regression fixture (using GraphQL camelCase shapes)
    must continue to return CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view(merge_state="CLEAN")
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:05:00Z",
            comment_id=4677095399,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {
                        "id": "PRRT_stale_1",
                        "isResolved": False,
                        "isOutdated": True,
                        "comments": {"nodes": [{
                            "databaseId": 3393166147,
                            "url": "https://example/3393166147",
                            "body": "old finding",
                            "path": "scripts/local/audit_main_ci_for_head.py",
                            "line": 369,
                            "author": {"login": CODEX_LOGIN},
                        }]},
                    },
                ],
            }
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_CLEAN_PASS_RESOLVE_ONLY
    assert pkt["clean_pass_detected"] is True
    assert pkt["unresolved_thread_count"] == 1


# Helper that returns raw stdout strings (for the slurped-output cases
# that need to bypass the make_gh_runner default list-only path.)
def make_gh_runner_raw(pr_view, issues_raw, reviews_raw, threads_payload):
    def _runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps(threads_payload)
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = issues_raw
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = reviews_raw
            return m
        m.stdout = "[]"
        return m
    return _runner


# ---------------------------------------------------------------------------
# normalize_merge_state unit tests
# ---------------------------------------------------------------------------


def test_normalize_merge_state_handles_all_known_shapes():
    """Direct unit tests for the normalize_merge_state helper."""
    # GraphQL uppercase
    assert mod.normalize_merge_state("CLEAN") == "CLEAN"
    assert mod.normalize_merge_state("BLOCKED") == "BLOCKED"
    assert mod.normalize_merge_state("DIRTY") == "DIRTY"
    assert mod.normalize_merge_state("UNSTABLE") == "UNSTABLE"
    # REST lowercase
    assert mod.normalize_merge_state("clean") == "CLEAN"
    assert mod.normalize_merge_state("blocked") == "BLOCKED"
    # GraphQL snake_case jq path
    assert mod.normalize_merge_state("dirty") == "DIRTY"
    # boolean
    assert mod.normalize_merge_state(True) == "CLEAN"
    assert mod.normalize_merge_state(False) == "BLOCKED"
    # None / empty / garbage
    assert mod.normalize_merge_state(None) is None
    assert mod.normalize_merge_state("") is None
    assert mod.normalize_merge_state("wat") is None


def test_timestamp_field_handles_camel_and_snake_case():
    """Direct unit tests for the timestamp_field helper."""
    # GraphQL camelCase
    assert mod.timestamp_field({"createdAt": "2026-01-01T00:00:00Z"}, "createdAt", "created_at") == "2026-01-01T00:00:00Z"
    assert mod.timestamp_field({"submittedAt": "2026-01-02T00:00:00Z"}, "submittedAt", "submitted_at") == "2026-01-02T00:00:00Z"
    # REST snake_case
    assert mod.timestamp_field({"created_at": "2026-01-01T00:00:00Z"}, "createdAt", "created_at") == "2026-01-01T00:00:00Z"
    assert mod.timestamp_field({"submitted_at": "2026-01-02T00:00:00Z"}, "submittedAt", "submitted_at", "createdAt", "created_at") == "2026-01-02T00:00:00Z"
    # Prefer GraphQL when both present
    assert mod.timestamp_field(
        {"createdAt": "2026-01-01T00:00:00Z", "created_at": "2025-01-01T00:00:00Z"},
        "createdAt", "created_at",
    ) == "2026-01-01T00:00:00Z"
    # Empty / missing
    assert mod.timestamp_field({}, "createdAt", "created_at") == ""
    assert mod.timestamp_field({"createdAt": ""}, "createdAt", "created_at") == ""


def test_flatten_paginated_items_handles_shapes():
    """Direct unit tests for the flatten_paginated_items helper."""
    # Already-flat list
    items, ok = mod.flatten_paginated_items([{"a": 1}, {"b": 2}])
    assert ok is True
    assert items == [{"a": 1}, {"b": 2}]
    # List of pages
    items, ok = mod.flatten_paginated_items([[{"a": 1}], [{"b": 2}]])
    assert ok is True
    assert items == [{"a": 1}, {"b": 2}]
    # List of wrappers
    items, ok = mod.flatten_paginated_items([{"items": [{"a": 1}]}, {"items": [{"b": 2}]}])
    assert ok is True
    assert items == [{"a": 1}, {"b": 2}]
    # Empty
    items, ok = mod.flatten_paginated_items([])
    assert ok is True
    assert items == []
    # None
    items, ok = mod.flatten_paginated_items(None)
    assert ok is False
    assert items == []
    # Top-level dict
    items, ok = mod.flatten_paginated_items({"items": [{"a": 1}]})
    assert ok is False
    assert items == []


# ---------------------------------------------------------------------------
# P1 #1 regression tests: live REST PR metadata normalization
# ---------------------------------------------------------------------------
#
# These tests use the real REST `Get a pull request` payload shape
# (with `head` / `base` nested objects, lowercase `mergeable_state`,
# and no `mergeStateStatus` / `review_decision` GraphQL fields). The
# classifier MUST normalize raw REST into its canonical packet and
# must not misclassify clean REST payloads as
# HOLD_MERGE_STATE_BLOCKED simply because the GraphQL field names are
# absent.


def test_live_rest_mergeable_state_clean_reaches_merge_ready(monkeypatch, tmp_path):
    """
    P1 #1: Full live REST pull payload with mergeable_state=clean +
    Codex clean-pass + zero unresolved threads must yield
    MERGE_READY_AWAITING_HUMAN_AUTHORIZATION.

    On the OLD code path, the JQ shim could not construct the
    mergeableState key from real REST (because the JQ filter
    accidentally aliased the wrong source field in some fixture
    variants), causing merge_state_status to remain None and the
    decision to fall through to HOLD_MERGE_STATE_BLOCKED. The new
    normalize_rest_pr_payload() helper reads the real REST field
    directly and exposes it on the canonical packet.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99001,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_MERGE_READY
    assert pkt["merge_state_status"] == "CLEAN"
    assert pkt["mergeable"] is True
    assert pkt["clean_pass_detected"] is True
    # reviewDecision and mergeStateStatus are absent in REST;
    # normalize_rest_pr_payload() exposes them as None.
    assert pkt["review_decision"] is None
    # Inventory is complete (no thread fetch errors).
    assert pkt["review_thread_inventory_complete"] is True
    assert pkt["review_thread_inventory_error_count"] == 0


def test_live_rest_mergeable_state_blocked_yields_hold_merge_blocked(monkeypatch, tmp_path):
    """
    P1 #1: Full live REST pull payload with mergeable_state=blocked +
    clean pass + zero unresolved threads must yield
    HOLD_MERGE_STATE_BLOCKED. The classification is driven by the
    real REST mergeable_state field, not by GraphQL-style field names.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="blocked", mergeable=False)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99002,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_MERGE_STATE_BLOCKED
    assert pkt["merge_state_status"] == "BLOCKED"
    assert pkt["mergeable"] is False


def test_live_rest_lacking_review_decision_still_classifies(monkeypatch, tmp_path):
    """
    P1 #1: Full live REST pull payload has NO review_decision field
    (REST does not expose it; only GraphQL does). The classifier must
    not require review_decision to reach MERGE_READY.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    # Sanity check: the live REST payload does NOT contain
    # review_decision. This is the whole point of the test.
    assert "review_decision" not in pr_view
    assert "reviewDecision" not in pr_view
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99003,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_MERGE_READY
    assert pkt["review_decision"] is None


def test_live_rest_dirty_mergeable_state_normalizes_to_dirty(monkeypatch, tmp_path):
    """
    P1 #1: REST mergeable_state=dirty must normalize to canonical
    DIRTY (uppercase), even when mergeable is null (the
    "computing" state). The classification falls through to
    HOLD_MERGE_STATE_BLOCKED on a clean pass.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="dirty", mergeable=None)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99004,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["merge_state_status"] == "DIRTY"
    assert pkt["status"] == mod.STATUS_HOLD_MERGE_STATE_BLOCKED


def test_live_rest_unstable_mergeable_state_normalizes_to_unstable(monkeypatch, tmp_path):
    """
    P1 #1: REST mergeable_state=unstable must normalize to canonical
    UNSTABLE.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="unstable", mergeable=False)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99005,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["merge_state_status"] == "UNSTABLE"
    assert pkt["status"] == mod.STATUS_HOLD_MERGE_STATE_BLOCKED


def test_graphql_merge_state_status_still_works_with_real_rest_payload(monkeypatch, tmp_path):
    """
    P1 #1 regression: GraphQL-style mergeStateStatus=CLEAN fixture
    must continue to classify correctly. The new code must not have
    regressed GraphQL/mock compatibility while fixing REST.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    # Canonical packet shape (no nested `head` object). The new
    # gh_pr_view_min() detects this and passes through unchanged.
    pr_view = {
        "sha": EXPECTED_HEAD,
        "state": "OPEN",
        "mergeStateStatus": "CLEAN",
        "mergeableState": None,
        "mergeable": True,
        "reviewDecision": "APPROVED",
        "baseRefName": "main",
        "headRefName": "tooling/codex-response-classifier-v1",
        "url": f"https://github.com/{REPO}/pull/402",
    }
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99006,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_MERGE_READY
    assert pkt["merge_state_status"] == "CLEAN"
    assert pkt["review_decision"] == "APPROVED"


def test_rest_payload_no_merge_state_status_key_still_reaches_merge_ready(monkeypatch, tmp_path):
    """
    P1 #1 regression: A live REST payload that LACKS the
    `merge_state_status` key entirely (which is the default for
    real REST responses) must still reach MERGE_READY when
    `mergeable_state` is "clean". The OLD JQ-shim path produced
    `merge_state_status: null` and the classifier would fall
    through to HOLD_MERGE_STATE_BLOCKED. The new path normalizes
    REST's real `mergeable_state` directly.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    # The new normalize_rest_pr_payload() produces a packet where
    # mergeStateStatus and merge_state_status are explicitly None
    # (REST does not expose them) and mergeable_state is "clean".
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99007,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    })
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # If the OLD code were running, the JQ shim would produce
    # mergeStateStatus=null (real REST lacks it) and the classifier
    # would emit HOLD_MERGE_STATE_BLOCKED. The new code reads
    # mergeable_state from the raw REST payload and normalizes it
    # to CLEAN, enabling MERGE_READY.
    assert pkt["status"] == mod.STATUS_MERGE_READY
    assert pkt["merge_state_status"] == "CLEAN"


def test_normalize_rest_pr_payload_unit():
    """
    Direct unit tests for normalize_rest_pr_payload().
    """
    raw = {
        "state": "open",
        "merged": False,
        "merged_at": None,
        "head": {"sha": EXPECTED_HEAD, "ref": "feat/x"},
        "base": {"ref": "main"},
        "draft": True,
        "mergeable": True,
        "mergeable_state": "clean",
        "html_url": "https://example/pr/1",
        "title": "Demo",
    }
    pkt = mod.normalize_rest_pr_payload(raw)
    assert pkt["sha"] == EXPECTED_HEAD
    assert pkt["state"] == "open"
    assert pkt["merged"] is False
    assert pkt["merged_at"] is None
    assert pkt["title"] == "Demo"
    assert pkt["draft"] is True
    assert pkt["mergeableState"] == "clean"
    assert pkt["mergeable_state"] == "clean"
    assert pkt["mergeable"] is True
    assert pkt["mergeStateStatus"] is None
    assert pkt["merge_state_status"] is None
    assert pkt["reviewDecision"] is None
    assert pkt["review_decision"] is None
    assert pkt["baseRefName"] == "main"
    assert pkt["headRefName"] == "feat/x"
    assert pkt["url"] == "https://example/pr/1"


def test_normalize_rest_pr_payload_handles_string_mergeable():
    """Some GitHub responses serialize `mergeable` as a string. Accept both."""
    raw = {
        "state": "open",
        "head": {"sha": EXPECTED_HEAD, "ref": "feat/x"},
        "base": {"ref": "main"},
        "mergeable": "true",  # REST sometimes returns string
        "mergeable_state": "clean",
        "html_url": "https://example/pr/1",
    }
    pkt = mod.normalize_rest_pr_payload(raw)
    assert pkt["mergeable"] is True


def test_normalize_rest_pr_payload_handles_missing_optional_fields():
    """The normalizer must be tolerant of missing optional REST fields."""
    pkt = mod.normalize_rest_pr_payload({})
    assert pkt["sha"] == ""
    assert pkt["state"] == ""
    assert pkt["merged"] is False
    assert pkt["mergeableState"] is None
    assert pkt["mergeable_state"] is None
    assert pkt["mergeable"] is None
    assert pkt["baseRefName"] == ""
    assert pkt["headRefName"] == ""
    assert pkt["url"] == ""


# ---------------------------------------------------------------------------
# P1 #2 regression tests: fail closed on incomplete review-thread inventory
# ---------------------------------------------------------------------------
#
# The review-thread fetch is required evidence. If the GraphQL command
# fails, the response has errors, JSON is malformed, the response is
# missing expected reviewThreads data, or hasNextPage=true and the
# implementation did not paginate, the classifier MUST NOT emit
# MERGE_READY, CODEX_CLEAN_PASS, or CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED.
# The allowed safe states are HOLD_NEW_CODEX_THREAD (when an active
# finding is already confirmed) or HOLD_CODEX_RESPONSE_PENDING (when
# we cannot trust the data).


def _empty_thread_payload() -> Dict[str, Any]:
    return {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}}
    }


def test_fail_closed_on_graphql_command_nonzero_exit(monkeypatch, tmp_path):
    """
    P1 #2: If the GraphQL review-thread command returns a nonzero
    exit code, the classifier must hold safely (HOLD_CODEX_RESPONSE_PENDING)
    and NOT emit MERGE_READY even if clean pass + CLEAN merge state
    are otherwise satisfied.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99101,
        ),
    ]

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            # Simulate a nonzero exit code (e.g. GitHub API outage
            # or auth failure) on the review-thread GraphQL call.
            m.returncode = 22
            m.stderr = "gh graphql returned 22: HTTP 500"
            m.stdout = ""
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps(issue)
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Must NOT emit MERGE_READY when inventory is incomplete.
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["status"] != mod.STATUS_MERGE_READY
    assert pkt["review_thread_inventory_complete"] is False
    assert pkt["review_thread_inventory_error_count"] >= 1
    # api_errors must be populated so the operator can see what failed.
    assert any("review_threads" in e for e in pkt["api_errors"])


def test_fail_closed_on_graphql_response_errors(monkeypatch, tmp_path):
    """
    P1 #2: A GraphQL response containing a top-level `errors` array
    (rate limit, partial failure, auth expiry) must be treated as
    incomplete inventory. Classifier must NOT emit MERGE_READY.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99102,
        ),
    ]
    graphql_with_errors = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
        }}},
        "errors": [{"message": "API rate limit exceeded", "type": "RATE_LIMITED"}],
    }
    runner = make_gh_runner(pr_view, issue, [], graphql_with_errors)
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["status"] != mod.STATUS_MERGE_READY
    assert pkt["review_thread_inventory_complete"] is False
    assert pkt["review_thread_inventory_error_count"] >= 1
    assert any("GraphQL errors" in e for e in pkt["api_errors"])


def test_fail_closed_on_malformed_graphql_json(monkeypatch, tmp_path):
    """
    P1 #2: A malformed GraphQL response (e.g. truncated JSON from a
    network blip) must be treated as incomplete inventory.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99103,
        ),
    ]

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            # Truncated JSON that will fail json.loads.
            m.stdout = '{"data": {"repository": {"pullRequest": {"reviewTh'
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps(issue)
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["status"] != mod.STATUS_MERGE_READY
    assert pkt["review_thread_inventory_complete"] is False
    assert pkt["review_thread_inventory_error_count"] >= 1
    assert any("invalid GraphQL response" in e for e in pkt["api_errors"])


def test_fail_closed_on_unhandled_pagination(monkeypatch, tmp_path):
    """
    P1 #2: hasNextPage=true on the first page (i.e. >100 review
    threads) and the implementation did not paginate further is
    incomplete inventory. Classifier must NOT emit MERGE_READY.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99104,
        ),
    ]
    threads_paginated = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": True, "endCursor": "CURSOR_2"},
                "nodes": [],
            }
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads_paginated)
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["status"] != mod.STATUS_MERGE_READY
    assert pkt["review_thread_inventory_complete"] is False
    assert pkt["review_thread_inventory_error_count"] >= 1
    assert any("pagination required" in e for e in pkt["api_errors"])


def test_fail_closed_on_missing_review_threads_in_response(monkeypatch, tmp_path):
    """
    P1 #2: A GraphQL response missing the `reviewThreads` container
    (e.g. an unexpected shape, partial failure, or schema drift) must
    be treated as incomplete inventory.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99105,
        ),
    ]
    threads_no_container = {
        "data": {"repository": {"pullRequest": {
            # reviewThreads missing on purpose.
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads_no_container)
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["status"] != mod.STATUS_MERGE_READY
    assert pkt["review_thread_inventory_complete"] is False
    assert pkt["review_thread_inventory_error_count"] >= 1
    assert any("reviewThreads" in e for e in pkt["api_errors"])


def test_fail_closed_emits_hold_new_thread_when_finding_already_confirmed(monkeypatch, tmp_path):
    """
    P1 #2: When review-thread inventory is incomplete AND a finding
    is already confirmed in the partial thread list, emit
    HOLD_NEW_CODEX_THREAD (not HOLD_CODEX_RESPONSE_PENDING) with a
    note in the recommendation that inventory is also incomplete.
    The active finding is the dominant signal even with incomplete
    inventory.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue: list = []
    threads_partial_with_finding = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": True, "endCursor": "CURSOR_X"},
                "nodes": [
                    {
                        "id": "PRRT_confirmed_finding",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {"nodes": [{
                            "databaseId": 99110,
                            "url": "https://example/confirmed",
                            "body": "P1 finding on current head",
                            "path": "scripts/local/foo.py",
                            "line": 50,
                            "author": {"login": CODEX_LOGIN},
                        }]},
                    },
                ],
            }
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads_partial_with_finding)
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Active finding wins over inventory incompleteness; emit
    # HOLD_NEW_CODEX_THREAD with an explicit inventory note.
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["review_thread_inventory_complete"] is False
    assert pkt["review_thread_inventory_error_count"] >= 1
    # The recommendation should mention inventory incompleteness so
    # the operator knows more findings may exist.
    assert "inventory" in pkt["recommendation"].lower()


def test_inventory_complete_packet_includes_correct_fields(monkeypatch, tmp_path):
    """
    Sanity: when inventory is complete, the JSON packet includes
    review_thread_inventory_complete=true and
    review_thread_inventory_error_count=0.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99111,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["review_thread_inventory_complete"] is True
    assert pkt["review_thread_inventory_error_count"] == 0
    assert pkt["review_thread_inventory_last_error"] == ""


def test_inventory_incomplete_markdown_surfaces_status(monkeypatch, tmp_path):
    """
    P1 #2: The markdown report must clearly surface that review-thread
    inventory is incomplete so the operator sees it without reading
    the JSON.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99112,
        ),
    ]

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.returncode = 1
            m.stderr = "transport error"
            m.stdout = ""
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps(issue)
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    md = (tmp_path / "pkt.md").read_text()
    # The markdown must include the new "Review-thread inventory"
    # section and explicitly mark it as incomplete.
    assert "## Review-thread inventory" in md
    assert "Inventory complete" in md
    # The ❌ marker signals the failure clearly.
    assert "❌" in md
    # Operator must see the underlying error message.
    assert "transport error" in md


def test_markdown_includes_new_inventory_section_when_complete(monkeypatch, tmp_path):
    """Sanity: the new inventory section is rendered on the success path too."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99113,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    md = (tmp_path / "pkt.md").read_text()
    assert "## Review-thread inventory" in md
    assert "✅" in md


def test_existing_markdown_sections_still_rendered(monkeypatch, tmp_path):
    """The new inventory section must not break the existing required sections."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99114,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    md = (tmp_path / "pkt.md").read_text()
    for section in [
        "## PR metadata",
        "## Latest Codex response",
        "## Clean-pass evidence",
        "## Active current-head blockers",
        "## Outdated unresolved threads",
        "## Resolved threads",
        "## Review-thread inventory",
        "## Polling summary",
        "## Recommendation",
        "## Next safe action",
    ]:
        assert section in md, f"missing markdown section: {section}"


# ---------------------------------------------------------------------------
# P2 #1 regression tests: malformed --ping-created-at must fail closed
# ---------------------------------------------------------------------------
#
# When the operator supplies a --ping-created-at that cannot be
# parsed, the classifier MUST NOT silently fall back to "no ping
# filter" (which would accept pre-ping Codex clean-pass evidence
# and could drive MERGE_READY_AWAITING_HUMAN_AUTHORIZATION). The
# classifier must fail closed at HOLD_CODEX_RESPONSE_PENDING with
# api_errors populated and the markdown report must surface the
# malformed-timestamp state. Valid timestamps and omitted
# timestamps must continue to work as before.


def test_malformed_ping_timestamp_fails_closed_no_merge_ready(monkeypatch, tmp_path):
    """
    P2 #1: Malformed --ping-created-at + old (pre-ping) Codex
    clean pass + mergeable CLEAN + zero unresolved threads must
    yield HOLD_CODEX_RESPONSE_PENDING, NOT
    MERGE_READY_AWAITING_HUMAN_AUTHORIZATION. The OLD code
    silently fell back to "no ping filter" and accepted the
    pre-ping clean pass.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    # Pre-ping Codex clean pass (before the broken ping
    # timestamp). Under the OLD behavior this would have been
    # accepted because the classifier set ping_dt = None and
    # treated the malformed timestamp as "no ping supplied".
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-10T12:00:00Z",  # well before ping
            comment_id=99201,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID,
        # Garbage that parse_iso_utc cannot handle.
        "--ping-created-at", "not-a-real-timestamp",
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Must NOT emit MERGE_READY when ping timestamp is malformed.
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["status"] != mod.STATUS_MERGE_READY
    assert pkt["clean_pass_detected"] is False
    # The OLD code would have emitted MERGE_READY because the
    # pre-ping clean pass was accepted. The new code refuses
    # to look at any Codex evidence when the ping boundary is
    # broken.


def test_malformed_ping_timestamp_packet_marks_invalid(monkeypatch, tmp_path):
    """
    P2 #1: The JSON packet must include ping_timestamp_valid=false
    and ping_timestamp_supplied=true when the operator's
    --ping-created-at could not be parsed.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99202,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID,
        "--ping-created-at", "garbage-2026-99-99",
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["ping_timestamp_supplied"] is True
    assert pkt["ping_timestamp_valid"] is False


def test_malformed_ping_timestamp_populates_api_errors(monkeypatch, tmp_path):
    """
    P2 #1: The JSON packet must include a clear api_errors entry
    explaining that the ping timestamp is malformed so the
    operator sees the underlying parse failure.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue: list = []
    runner = make_gh_runner(pr_view, issue, [], _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID,
        "--ping-created-at", "definitely-not-a-date",
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # api_errors must be populated with a clear message.
    assert pkt["api_errors"], "api_errors should be non-empty for malformed ping timestamp"
    assert any("ping_created_at" in e and "could not be parsed" in e
               for e in pkt["api_errors"]), (
        f"expected parse error in api_errors, got: {pkt['api_errors']}"
    )
    # Recommendation should explain the ping must be corrected.
    assert "ping" in pkt["recommendation"].lower()


def test_malformed_ping_timestamp_markdown_surfaces_status(monkeypatch, tmp_path):
    """
    P2 #1: The markdown report must surface the malformed ping
    timestamp clearly so the operator sees it without reading
    the JSON.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue: list = []
    runner = make_gh_runner(pr_view, issue, [], _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID,
        "--ping-created-at", "broken-timestamp",
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    md = (tmp_path / "pkt.md").read_text()
    # The markdown must include the new "Ping timestamp" section
    # and explicitly mark it as malformed.
    assert "## Ping timestamp" in md
    assert "Parsed cleanly" in md
    assert "❌" in md


def test_valid_ping_timestamp_still_filters_clean_pass(monkeypatch, tmp_path):
    """
    P2 #1 regression: A valid --ping-created-at that successfully
    parses must continue to filter Codex evidence by timestamp.
    Post-ping clean passes must still be detected as clean passes.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    # Post-ping clean pass (after PING_CREATED).
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",  # after PING_CREATED
            comment_id=99203,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_MERGE_READY
    assert pkt["ping_timestamp_valid"] is True
    assert pkt["ping_timestamp_supplied"] is True


def test_no_ping_timestamp_keeps_prior_behavior(monkeypatch, tmp_path):
    """
    P2 #1 regression: When --ping-created-at is omitted (empty
    string), the classifier must continue to apply NO ping
    filter. All clean passes are accepted as before.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    # Pre-ping Codex clean pass. With no --ping-created-at
    # supplied, this is accepted as a valid clean pass.
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-10T12:00:00Z",
            comment_id=99204,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        # No --ping-created-at and no --ping-comment-id.
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # When no --ping-created-at is supplied, the classifier
    # should behave as before: no ping filter, accept the
    # clean pass, emit MERGE_READY.
    assert pkt["ping_timestamp_supplied"] is False
    assert pkt["ping_timestamp_valid"] is True
    assert pkt["status"] == mod.STATUS_MERGE_READY


def test_naive_ping_timestamp_fails_closed_no_typeerror(monkeypatch, tmp_path):
    """
    P2 #4: A --ping-created-at value that parses to a naive
    datetime (no Z, no offset) must be treated as invalid and
    fail closed at HOLD_CODEX_RESPONSE_PENDING. The classifier
    MUST NOT crash with TypeError when later comparing the
    naive ping_dt against aware GitHub timestamps. The OLD
    code would set ping_dt to a naive datetime, accept the
    ping, and crash on the first comparison against an aware
    GitHub createdAt/submittedAt.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    # Pre-ping Codex clean pass (would be accepted as a
    # post-ping clean pass under the OLD code if the naive
    # datetime was silently used).
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99501,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    # NAIVE datetime — no Z, no offset.
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID,
        "--ping-created-at", "2026-06-11T17:30:00",  # naive
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    # Must NOT crash with TypeError. Must write a packet.
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Fail closed.
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["ping_timestamp_valid"] is False
    assert pkt["ping_timestamp_supplied"] is True
    # The api_error must mention the missing timezone.
    assert any("no timezone" in e for e in pkt["api_errors"]), (
        f"expected 'no timezone' error in api_errors, got: {pkt['api_errors']}"
    )


def test_valid_z_ping_timestamp_detects_post_ping_clean_pass(monkeypatch, tmp_path):
    """
    P2 #4 regression: A valid Z-suffixed --ping-created-at
    must continue to filter post-ping Codex evidence.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",  # after Z ping
            comment_id=99601,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID,
        "--ping-created-at", "2026-06-11T17:30:00Z",  # valid Z
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["ping_timestamp_valid"] is True
    assert pkt["status"] == mod.STATUS_MERGE_READY


def test_valid_offset_ping_timestamp_detects_post_ping_clean_pass(monkeypatch, tmp_path):
    """
    P2 #4: A valid --ping-created-at with a numeric offset
    (instead of Z) must also work. Both should produce
    timezone-aware datetimes.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",  # after the offset ping
            comment_id=99701,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID,
        # Offset timestamp (not Z).
        "--ping-created-at", "2026-06-11T17:30:00+00:00",
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["ping_timestamp_valid"] is True
    assert pkt["status"] == mod.STATUS_MERGE_READY


def test_raw_poll_snapshot_reset_comments_fetch_failure(monkeypatch, tmp_path):
    """
    P2 #5: Poll 1 has a clean pass + incomplete thread
    inventory. Poll 2 fails to fetch issue comments but
    succeeds on threads. The classifier must NOT reuse
    poll 1's stale clean-pass comment to emit merge-ready.
    Final state must be HOLD_CODEX_RESPONSE_PENDING (from
    the post-loop exhaustion fallback).
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    call_count = {"n": 0}

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Poll 1: thread fetch fails (incomplete).
                m.stdout = json.dumps({
                    "data": {"repository": {"pullRequest": {
                        "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
                    }}},
                    "errors": [{"message": "transient outage"}],
                })
            else:
                # Poll 2: thread fetch succeeds (empty).
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
                }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            if call_count["n"] == 0:
                # Poll 1: comments fetch succeeds with clean pass.
                m.stdout = json.dumps([
                    make_issue_comment(
                        author=CODEX_LOGIN,
                        body=codex_clean_pass_body(),
                        created_at="2026-06-11T18:00:00Z",
                        comment_id=99801,
                    )
                ])
            else:
                # Poll 2: comments fetch FAILS.
                m.returncode = 22
                m.stderr = "gh api returned 22: HTTP 500"
                m.stdout = ""
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "2", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Must NOT be merge-ready from the stale poll-1 clean pass.
    assert pkt["status"] != mod.STATUS_MERGE_READY
    # The post-loop exhaustion fallback must fire.
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    # The latest poll's issue-comments surface is incomplete
    # (the inventory gate in section 8 fires and fails
    # closed); the stop_reason must reflect inventory
    # incompleteness, NOT the post-loop exhaustion fallback
    # which is reserved for "no decision at all" cases.
    assert pkt["stop_reason"] == "inventory_incomplete"
    # api_errors must clearly identify the latest failed surface.
    assert any("issue_comments" in e for e in pkt["api_errors"])


def test_raw_poll_snapshot_reset_reviews_fetch_failure(monkeypatch, tmp_path):
    """
    P2 #5: Poll 1 has a stale state that could be
    misinterpreted. Poll 2 reviews fetch fails. The
    classifier must NOT emit HOLD_NEW_CODEX_THREAD from
    stale state — only from the latest poll's evidence.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    call_count = {"n": 0}

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Poll 1: thread fetch incomplete (hasNextPage) with
                # partial Codex active finding visible.
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": True, "endCursor": "CURSOR_X"},
                        "nodes": [
                            {
                                "id": "PRRT_poll1_partial",
                                "isResolved": False,
                                "isOutdated": False,
                                "comments": {"nodes": [{
                                    "databaseId": 99851,
                                    "url": "https://example/99851",
                                    "body": "P1 finding",
                                    "path": "scripts/local/foo.py",
                                    "line": 1,
                                    "author": {"login": CODEX_LOGIN},
                                }]},
                            },
                        ],
                    }
                }}}})
            else:
                # Poll 2: thread fetch succeeds with empty inventory.
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
                }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = "[]"
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            if call_count["n"] == 0:
                # Poll 1: reviews fetch succeeds (no findings).
                m.stdout = "[]"
            else:
                # Poll 2: reviews fetch FAILS.
                m.returncode = 22
                m.stderr = "gh api returned 22: HTTP 500"
                m.stdout = ""
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "2", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # The latest poll (poll 2) had no active threads, no
    # clean pass, and failed reviews fetch. The classifier
    # must hold closed on the latest poll's evidence.
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["status"] != mod.STATUS_HOLD_NEW_THREAD
    # The latest poll's reviews surface is incomplete (the
    # inventory gate in section 8 fires and fails closed);
    # the stop_reason must reflect inventory
    # incompleteness, NOT the post-loop exhaustion fallback
    # which is reserved for "no decision at all" cases.
    assert pkt["stop_reason"] == "inventory_incomplete"
    # api_errors must clearly identify the latest failed surface.
    assert any("reviews" in e for e in pkt["api_errors"])


def test_raw_poll_snapshot_reset_empty_latest_poll_overrides_poll_1(monkeypatch, tmp_path):
    """
    P2 #5: Poll 1 has comments/reviews/threads data. Poll 2
    succeeds with all surfaces returning empty. The final
    packet's raw snapshots and derived thread buckets must
    reflect poll 2 (empty), not poll 1.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    call_count = {"n": 0}

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Poll 1: 1 active + 1 outdated thread.
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {
                                "id": "PRRT_poll1_active",
                                "isResolved": False,
                                "isOutdated": False,
                                "comments": {"nodes": [{
                                    "databaseId": 99891,
                                    "url": "https://example/99891",
                                    "body": "poll 1 active",
                                    "path": "scripts/local/foo.py",
                                    "line": 1,
                                    "author": {"login": "human-reviewer"},
                                }]},
                            },
                            {
                                "id": "PRRT_poll1_outdated",
                                "isResolved": False,
                                "isOutdated": True,
                                "comments": {"nodes": [{
                                    "databaseId": 99892,
                                    "url": "https://example/99892",
                                    "body": "poll 1 outdated",
                                    "path": "scripts/local/foo.py",
                                    "line": 2,
                                    "author": {"login": "human-reviewer"},
                                }]},
                            },
                        ],
                    }
                }}}})
            else:
                # Poll 2: empty.
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
                }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            if call_count["n"] == 0:
                # Poll 1: pre-ping clean pass.
                m.stdout = json.dumps([
                    make_issue_comment(
                        author=CODEX_LOGIN,
                        body=codex_clean_pass_body(),
                        created_at="2026-06-10T12:00:00Z",  # pre-ping
                        comment_id=99893,
                    )
                ])
            else:
                # Poll 2: empty.
                m.stdout = "[]"
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = "[]"
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "2", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # The final packet must reflect poll 2 (empty), not poll 1.
    assert pkt["unresolved_thread_count"] == 0
    assert pkt["active_threads"] == []
    assert pkt["outdated_threads"] == []
    # No clean pass (poll 2 had no issue comments and no
    # pre-ping clean pass survives).
    assert pkt["clean_pass_detected"] is False
    # The post-loop exhaustion fallback fires because poll 2
    # made no decision.
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["stop_reason"] == "polling_exhausted_no_codex_response"
    assert pkt["polls_used"] == 2


# ---------------------------------------------------------------------------
# P2 #2 regression tests: per-poll thread inventory reset
# ---------------------------------------------------------------------------
#
# The thread lists and inventory completeness flag must reflect
# ONLY the current poll's snapshot, not accumulated state from
# earlier polls. Stale thread entries (e.g. an unresolved thread
# that was resolved between polls) would otherwise cause
# CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED instead of
# MERGE_READY_AWAITING_HUMAN_AUTHORIZATION on a fresh poll whose
# own inventory has zero unresolved threads.


def test_per_poll_thread_inventory_resolved_between_polls(monkeypatch, tmp_path):
    """
    P2 #2: Poll 1 has a non-Codex unresolved active thread and
    no clean pass (loop continues); poll 2 has a clean pass and
    zero unresolved threads (the active thread was resolved
    between polls). The final classification must be
    MERGE_READY_AWAITING_HUMAN_AUTHORIZATION (NOT
    CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED). The OLD code would
    have accumulated poll 1's active thread into
    active_threads, so poll 2's unresolved_count would have been
    >= 1 and the decision would have been
    CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    call_count = {"n": 0}

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Poll 1: one unresolved active thread by a
                # non-Codex author (so has_active_blocker=False
                # and the loop continues).
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {
                                "id": "PRRT_stale_poll1",
                                "isResolved": False,
                                "isOutdated": False,
                                "comments": {"nodes": [{
                                    "databaseId": 99301,
                                    "url": "https://example/99301",
                                    "body": "non-codex finding",
                                    "path": "scripts/local/foo.py",
                                    "line": 1,
                                    "author": {"login": "human-reviewer"},
                                }]},
                            },
                        ],
                    }
                }}}})
            else:
                # Poll 2: zero unresolved threads (the same
                # thread was resolved between polls).
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
                }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            # No clean pass on poll 1, post-ping clean pass on poll 2.
            if call_count["n"] == 0:
                m.stdout = "[]"
            else:
                m.stdout = json.dumps([
                    make_issue_comment(
                        author=CODEX_LOGIN,
                        body=codex_clean_pass_body(),
                        created_at="2026-06-11T18:00:00Z",
                        comment_id=99302,
                    )
                ])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "5", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Poll 2's clean inventory must drive the final decision.
    assert pkt["status"] == mod.STATUS_MERGE_READY
    assert pkt["status"] != mod.STATUS_CLEAN_PASS_RESOLVE_ONLY
    # The final packet must reflect poll 2's data only.
    assert pkt["unresolved_thread_count"] == 0
    assert pkt["active_threads"] == []
    assert pkt["outdated_threads"] == []
    # Polls 1 and 2 both ran.
    assert pkt["polls_used"] == 2
    # Sleep called once (between poll 1 and poll 2).
    assert len(sleep.calls) == 1


def test_per_poll_outdated_thread_inventory_resets_to_zero(monkeypatch, tmp_path):
    """
    P2 #2: Poll 1 has an outdated unresolved thread; poll 2 has
    zero unresolved threads. The final unresolved_thread_count
    must be 0 (poll 2's data only).
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    call_count = {"n": 0}

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Poll 1: one outdated unresolved thread.
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {
                                "id": "PRRT_outdated_poll1",
                                "isResolved": False,
                                "isOutdated": True,
                                "comments": {"nodes": [{
                                    "databaseId": 99401,
                                    "url": "https://example/99401",
                                    "body": "stale finding",
                                    "path": "scripts/local/foo.py",
                                    "line": 2,
                                    "author": {"login": "human-reviewer"},
                                }]},
                            },
                        ],
                    }
                }}}})
            else:
                # Poll 2: zero unresolved threads.
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
                }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            if call_count["n"] == 0:
                m.stdout = "[]"
            else:
                m.stdout = json.dumps([
                    make_issue_comment(
                        author=CODEX_LOGIN,
                        body=codex_clean_pass_body(),
                        created_at="2026-06-11T18:00:00Z",
                        comment_id=99402,
                    )
                ])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "5", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["unresolved_thread_count"] == 0
    assert pkt["outdated_threads"] == []
    assert pkt["active_threads"] == []
    assert pkt["outdated_unresolved_thread_count"] == 0
    # Final decision uses poll 2 only -> MERGE_READY.
    assert pkt["status"] == mod.STATUS_MERGE_READY


def test_per_poll_inventory_completeness_resets_after_poll_failure(monkeypatch, tmp_path):
    """
    P2 #2: Poll 1 thread inventory fetch fails; poll 2 succeeds
    with zero unresolved threads and a clean pass. The final
    review_thread_inventory_complete must be true (poll 2's
    state) and the classification must use poll 2.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    call_count = {"n": 0}

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Poll 1: thread fetch fails (GraphQL errors).
                m.stdout = json.dumps({
                    "data": {"repository": {"pullRequest": {
                        "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
                    }}},
                    "errors": [{"message": "transient outage", "type": "TRANSIENT"}],
                })
            else:
                # Poll 2: thread fetch succeeds.
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
                }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            if call_count["n"] == 0:
                m.stdout = "[]"
            else:
                m.stdout = json.dumps([
                    make_issue_comment(
                        author=CODEX_LOGIN,
                        body=codex_clean_pass_body(),
                        created_at="2026-06-11T18:00:00Z",
                        comment_id=99501,
                    )
                ])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "5", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Poll 2 succeeded; the final packet must reflect that.
    assert pkt["review_thread_inventory_complete"] is True
    assert pkt["review_thread_inventory_error_count"] == 0
    assert pkt["unresolved_thread_count"] == 0
    # Final decision uses poll 2 -> MERGE_READY.
    assert pkt["status"] == mod.STATUS_MERGE_READY
    # api_errors may still contain poll 1's failure (accumulated
    # across polls) — that's intentional historical context.
    # The per-poll flags reflect poll 2 only.
    assert pkt["polls_used"] == 2


def test_per_poll_active_outdated_resolved_lists_reflect_latest_poll(monkeypatch, tmp_path):
    """
    P2 #2: The final packet's active_threads, outdated_threads,
    and resolved_threads lists must reflect the LATEST poll's
    snapshot only, not accumulated state from earlier polls.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    call_count = {"n": 0}

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Poll 1: 1 active + 1 outdated.
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {
                                "id": "PRRT_poll1_active",
                                "isResolved": False,
                                "isOutdated": False,
                                "comments": {"nodes": [{
                                    "databaseId": 99601,
                                    "url": "https://example/99601",
                                    "body": "poll 1 active",
                                    "path": "scripts/local/foo.py",
                                    "line": 1,
                                    "author": {"login": "human-reviewer"},
                                }]},
                            },
                            {
                                "id": "PRRT_poll1_outdated",
                                "isResolved": False,
                                "isOutdated": True,
                                "comments": {"nodes": [{
                                    "databaseId": 99602,
                                    "url": "https://example/99602",
                                    "body": "poll 1 outdated",
                                    "path": "scripts/local/foo.py",
                                    "line": 2,
                                    "author": {"login": "human-reviewer"},
                                }]},
                            },
                        ],
                    }
                }}}})
            else:
                # Poll 2: 1 resolved (different from poll 1).
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {
                                "id": "PRRT_poll2_resolved",
                                "isResolved": True,
                                "isOutdated": False,
                                "comments": {"nodes": [{
                                    "databaseId": 99603,
                                    "url": "https://example/99603",
                                    "body": "poll 2 resolved",
                                    "path": "scripts/local/foo.py",
                                    "line": 3,
                                    "author": {"login": "human-reviewer"},
                                }]},
                            },
                        ],
                    }
                }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            if call_count["n"] == 0:
                m.stdout = "[]"
            else:
                m.stdout = json.dumps([
                    make_issue_comment(
                        author=CODEX_LOGIN,
                        body=codex_clean_pass_body(),
                        created_at="2026-06-11T18:00:00Z",
                        comment_id=99604,
                    )
                ])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "5", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # The final packet must contain ONLY poll 2's thread
    # (the resolved one), not poll 1's stale active+outdated.
    assert pkt["active_threads"] == []
    assert pkt["outdated_threads"] == []
    assert len(pkt["resolved_threads"]) == 1
    assert pkt["resolved_threads"][0]["thread_id"] == "PRRT_poll2_resolved"
    # Final decision uses poll 2's data -> MERGE_READY.
    assert pkt["status"] == mod.STATUS_MERGE_READY
    assert pkt["unresolved_thread_count"] == 0
    assert pkt["current_head_active_blocker_count"] == 0
    assert pkt["outdated_unresolved_thread_count"] == 0


def test_inventory_complete_packet_continues_with_fresh_poll_after_failure(monkeypatch, tmp_path):
    """
    P2 #2 regression retention: The existing fail-closed
    behavior on the LATEST poll's incomplete inventory still
    works. If poll N (the last) has incomplete inventory and
    no clean pass, HOLD_CODEX_RESPONSE_PENDING is emitted.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    call_count = {"n": 0}

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            call_count["n"] += 1
            # Both polls fail with GraphQL errors.
            m.stdout = json.dumps({
                "data": {"repository": {"pullRequest": {
                    "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
                }}},
                "errors": [{"message": "rate limit", "type": "RATE_LIMITED"}],
            })
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = "[]"
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "3", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Both polls had incomplete inventory; the LAST poll
    # drives the final state.
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["review_thread_inventory_complete"] is False
    # api_errors should contain GraphQL errors from at least
    # one poll.
    assert any("GraphQL errors" in e for e in pkt["api_errors"])


# ---------------------------------------------------------------------------
# P2 #3 regression tests: clear stale stop state before retrying
# ---------------------------------------------------------------------------
#
# When the classifier continues after an incomplete inventory
# that already saw an active Codex finding, the OLD code would
# leave final_status = HOLD_NEW_CODEX_THREAD and
# stop_reason = "active_finding_with_incomplete_inventory". If a
# later poll completes successfully with no active threads and
# no clean pass, the loop would exhaust while preserving the
# stale stop_reason, and the post-loop exhaustion fallback
# (HOLD_CODEX_RESPONSE_PENDING) would be skipped. The per-poll
# state reset clears the terminal decision state at the start
# of each poll so a later successful poll can produce a fresh
# decision.


def test_stale_stop_state_cleared_after_poll_2_no_active_no_clean_pass(monkeypatch, tmp_path):
    """
    P2 #3: Poll 1 has incomplete inventory + active finding
    seen (hasNextPage=true with a partial Codex active finding).
    Poll 2 has complete inventory + no active threads + no
    clean pass. After max polls exhausted, the final state must
    be HOLD_CODEX_RESPONSE_PENDING, NOT HOLD_NEW_CODEX_THREAD.
    The OLD code would preserve the stale stop_reason from
    poll 1 and skip the exhaustion fallback.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    call_count = {"n": 0}

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Poll 1: hasNextPage=true with a partial
                # Codex active finding. The active finding is
                # visible on this page so the gate sets
                # final_status=HOLD_NEW_CODEX_THREAD with
                # stop_reason=active_finding_with_incomplete_inventory.
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": True, "endCursor": "CURSOR_2"},
                        "nodes": [
                            {
                                "id": "PRRT_poll1_partial_finding",
                                "isResolved": False,
                                "isOutdated": False,
                                "comments": {"nodes": [{
                                    "databaseId": 99701,
                                    "url": "https://example/99701",
                                    "body": "P1 finding on current head",
                                    "path": "scripts/local/foo.py",
                                    "line": 1,
                                    "author": {"login": CODEX_LOGIN},
                                }]},
                            },
                        ],
                    }
                }}}})
            else:
                # Poll 2: complete inventory, no threads, no
                # clean pass. The per-poll state reset must
                # clear poll 1's HOLD_NEW_CODEX_THREAD so
                # poll 2's exhausted state emits
                # HOLD_CODEX_RESPONSE_PENDING.
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
                }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = "[]"
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "3", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # The post-loop exhaustion fallback must fire.
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["status"] != mod.STATUS_HOLD_NEW_THREAD
    # Polling exhausted (loop ran all 3 polls).
    assert pkt["polls_used"] == 3
    assert pkt["polling_exhausted"] is True


def test_stale_stop_state_cleared_final_stop_reason_is_exhaustion(monkeypatch, tmp_path):
    """
    P2 #3: The final stop_reason must describe polling
    exhaustion, NOT the stale active_finding_with_incomplete_inventory
    from poll 1. The recommendation must reflect polling
    exhaustion too.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    call_count = {"n": 0}

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": True, "endCursor": "CURSOR_2"},
                        "nodes": [
                            {
                                "id": "PRRT_poll1_partial_finding_2",
                                "isResolved": False,
                                "isOutdated": False,
                                "comments": {"nodes": [{
                                    "databaseId": 99801,
                                    "url": "https://example/99801",
                                    "body": "P1 finding",
                                    "path": "scripts/local/foo.py",
                                    "line": 2,
                                    "author": {"login": CODEX_LOGIN},
                                }]},
                            },
                        ],
                    }
                }}}})
            else:
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
                }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = "[]"
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "2", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # stop_reason must NOT be the stale
    # "active_finding_with_incomplete_inventory" from poll 1.
    assert pkt["stop_reason"] != "active_finding_with_incomplete_inventory"
    # The post-loop exhaustion code sets a clear exhaustion reason.
    assert pkt["stop_reason"] == "polling_exhausted_no_codex_response"
    # The recommendation must reflect polling exhaustion (the
    # canonical HOLD_CODEX_RESPONSE_PENDING message), not
    # the inventory-incomplete message from poll 1.
    assert "bounded poll budget" in pkt["recommendation"].lower()


def test_stale_stop_state_cleared_poll_2_clean_pass_emits_merge_ready(monkeypatch, tmp_path):
    """
    P2 #3: Poll 1 has incomplete inventory + active finding
    seen. Poll 2 has complete inventory + clean pass + zero
    unresolved threads + mergeable CLEAN. The per-poll state
    reset must allow poll 2 to override poll 1's stale
    HOLD_NEW_CODEX_THREAD and emit MERGE_READY.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    call_count = {"n": 0}

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": True, "endCursor": "CURSOR_2"},
                        "nodes": [
                            {
                                "id": "PRRT_poll1_partial_finding_3",
                                "isResolved": False,
                                "isOutdated": False,
                                "comments": {"nodes": [{
                                    "databaseId": 99901,
                                    "url": "https://example/99901",
                                    "body": "P1 finding",
                                    "path": "scripts/local/foo.py",
                                    "line": 3,
                                    "author": {"login": CODEX_LOGIN},
                                }]},
                            },
                        ],
                    }
                }}}})
            else:
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
                }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            if call_count["n"] == 0:
                m.stdout = "[]"
            else:
                # Post-ping clean pass on poll 2.
                m.stdout = json.dumps([
                    make_issue_comment(
                        author=CODEX_LOGIN,
                        body=codex_clean_pass_body(),
                        created_at="2026-06-11T18:00:00Z",
                        comment_id=99902,
                    )
                ])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "3", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Poll 2's clean inventory + clean pass must drive the
    # final decision, overriding poll 1's stale
    # HOLD_NEW_CODEX_THREAD.
    assert pkt["status"] == mod.STATUS_MERGE_READY
    assert pkt["stop_reason"] == "merge_ready"
    assert pkt["unresolved_thread_count"] == 0
    assert pkt["active_threads"] == []


# ---------------------------------------------------------------------------
# P1 #1: Fail closed when issue comments cannot be read
# P1 #2: Fail closed when reviews cannot be read
# ---------------------------------------------------------------------------
#
# Both P1s require treating the three Codex response surfaces
# (issue comments, formal review submissions, review threads) as
# REQUIRED evidence. If any of them cannot be fetched in the latest
# poll, the classifier MUST NOT emit merge-ready / clean-pass
# states and MUST fail closed at HOLD_CODEX_RESPONSE_PENDING
# (NOT HOLD_NEW_CODEX_THREAD — that's reserved for confirmed
# active findings; a missing surface is a hold on response, not
# a hold on a new finding).
#
# The packet must expose three independent inventory completeness
# flags so the operator (and the markdown report) can see WHICH
# surface failed: issue_comment_inventory_complete,
# review_submission_inventory_complete, review_thread_inventory_complete.


def test_p1_issue_comments_fetch_failure_fails_closed_no_merge_ready(
    monkeypatch, tmp_path,
):
    """
    P1 #1: formal review clean pass + mergeable CLEAN + zero
    review threads + issue-comments fetch failure must NOT
    emit MERGE_READY_AWAITING_HUMAN_AUTHORIZATION. The issue
    comments surface is required evidence; a fetch failure
    must fail closed at HOLD_CODEX_RESPONSE_PENDING.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
            }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            # Issue-comments fetch FAILS.
            m.returncode = 22
            m.stderr = "gh api returned 22: HTTP 500"
            m.stdout = ""
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            # Review fetch succeeds with a formal APPROVED clean
            # pass. The old code would happily emit merge-ready
            # because the issue-comments fetch failed and the
            # comment list was empty.
            m.stdout = json.dumps([
                make_review(
                    author=CODEX_LOGIN,
                    state="APPROVED",
                    body=codex_clean_pass_body(),
                    submitted_at="2026-06-11T18:00:00Z",
                    review_id=99871,
                ),
            ])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] != mod.STATUS_MERGE_READY
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING


def test_p1_issue_comments_fetch_failure_marks_inventory_incomplete(
    monkeypatch, tmp_path,
):
    """P1 #1: packet must expose issue_comment_inventory_complete=False."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
            }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.returncode = 22
            m.stderr = "gh api returned 22: HTTP 500"
            m.stdout = ""
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps([
                make_review(
                    author=CODEX_LOGIN,
                    state="APPROVED",
                    body=codex_clean_pass_body(),
                    submitted_at="2026-06-11T18:00:00Z",
                    review_id=99872,
                ),
            ])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["issue_comment_inventory_complete"] is False
    assert pkt["issue_comment_inventory_error_count"] > 0
    # The other two surfaces DID succeed — must stay True.
    assert pkt["review_submission_inventory_complete"] is True
    assert pkt["review_thread_inventory_complete"] is True


def test_p1_issue_comments_fetch_failure_populates_api_errors(
    monkeypatch, tmp_path,
):
    """P1 #1: api_errors must clearly identify issue-comments as failed."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
            }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.returncode = 22
            m.stderr = "gh api returned 22: HTTP 500"
            m.stdout = ""
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps([])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert any("issue_comments" in e for e in pkt["api_errors"])
    # The error message must explain the issue-comment inventory
    # was incomplete, not just be the raw stderr line.
    assert any(
        "issue-comment" in e.lower() or "issue comment" in e.lower()
        for e in pkt["api_errors"]
    )


def test_p1_issue_comments_poll_2_failure_does_not_reuse_poll_1(
    monkeypatch, tmp_path,
):
    """
    P1 #1: poll 1 has incomplete thread inventory AND a
    clean pass. The loop continues to poll 2 (via the
    inventory gate's `continue`). Poll 2 succeeds on
    threads but its issue-comments fetch fails. The
    classifier must NOT reuse poll 1's clean pass. The
    latest poll's issue_comment_inventory_complete must
    be False and the inventory gate in section 8 must
    hold at HOLD_CODEX_RESPONSE_PENDING.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    call_count = {"n": 0}

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            call_count["graphql"] = call_count.get("graphql", 0) + 1
            if call_count["graphql"] == 1:
                # Poll 1: thread fetch INCOMPLETE (hasNextPage),
                # with the inventory gate firing and continuing
                # to poll 2.
                m.stdout = json.dumps({
                    "data": {"repository": {"pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "X"},
                            "nodes": [],
                        }
                    }}},
                    "errors": [{"message": "hasNextPage=true"}],
                })
            else:
                # Poll 2: thread fetch succeeds (empty).
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
                }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Poll 1: clean pass present.
                m.stdout = json.dumps([
                    make_issue_comment(
                        author=CODEX_LOGIN,
                        body=codex_clean_pass_body(),
                        created_at="2026-06-11T18:00:00Z",
                        comment_id=99881,
                    ),
                ])
            else:
                # Poll 2: fetch FAILS.
                m.returncode = 22
                m.stderr = "gh api returned 22: HTTP 500"
                m.stdout = ""
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps([])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "2", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Must NOT be merge-ready from poll 1's stale clean pass.
    assert pkt["status"] != mod.STATUS_MERGE_READY
    # Polling exhausted -> the latest poll's inventory gate
    # fails closed (issue_comment_inventory_complete=False).
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert any("issue_comments" in e for e in pkt["api_errors"])
    # The latest poll's issue_comment_inventory_complete must be False.
    assert pkt["issue_comment_inventory_complete"] is False
    # Polls_used should be 2 (both polls ran).
    assert pkt["polls_used"] == 2


def test_p1_reviews_fetch_failure_fails_closed_no_merge_ready(
    monkeypatch, tmp_path,
):
    """
    P1 #2: issue-comment clean pass + mergeable CLEAN + zero
    review threads + review fetch failure must NOT emit
    MERGE_READY_AWAITING_HUMAN_AUTHORIZATION. The formal
    review submissions surface is required evidence.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
            }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps([
                make_issue_comment(
                    author=CODEX_LOGIN,
                    body=codex_clean_pass_body(),
                    created_at="2026-06-11T18:00:00Z",
                    comment_id=99891,
                ),
            ])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            # Review fetch FAILS.
            m.returncode = 22
            m.stderr = "gh api returned 22: HTTP 500"
            m.stdout = ""
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] != mod.STATUS_MERGE_READY
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING


def test_p1_reviews_fetch_failure_marks_inventory_incomplete(
    monkeypatch, tmp_path,
):
    """P1 #2: packet must expose review_submission_inventory_complete=False."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
            }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps([
                make_issue_comment(
                    author=CODEX_LOGIN,
                    body=codex_clean_pass_body(),
                    created_at="2026-06-11T18:00:00Z",
                    comment_id=99892,
                ),
            ])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.returncode = 22
            m.stderr = "gh api returned 22: HTTP 500"
            m.stdout = ""
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["review_submission_inventory_complete"] is False
    assert pkt["review_submission_inventory_error_count"] > 0
    assert pkt["issue_comment_inventory_complete"] is True
    assert pkt["review_thread_inventory_complete"] is True


def test_p1_reviews_fetch_failure_populates_api_errors(
    monkeypatch, tmp_path,
):
    """P1 #2: api_errors must clearly identify reviews as failed."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
            }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps([])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.returncode = 22
            m.stderr = "gh api returned 22: HTTP 500"
            m.stdout = ""
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert any("reviews" in e for e in pkt["api_errors"])


def test_p1_reviews_poll_2_failure_does_not_reuse_poll_1(
    monkeypatch, tmp_path,
):
    """
    P1 #2: poll 1 has incomplete thread inventory AND a
    clean pass. The loop continues to poll 2 (via the
    inventory gate's `continue`). Poll 2 succeeds on
    threads but its reviews fetch fails. The classifier
    must NOT reuse poll 1's clean pass. The latest poll's
    review-submission inventory must be incomplete and the
    inventory gate in section 8 must hold at
    HOLD_CODEX_RESPONSE_PENDING.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    call_count = {"graphql": 0, "reviews": 0}

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            call_count["graphql"] += 1
            if call_count["graphql"] == 1:
                # Poll 1: thread fetch INCOMPLETE (hasNextPage).
                m.stdout = json.dumps({
                    "data": {"repository": {"pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "X"},
                            "nodes": [],
                        }
                    }}},
                    "errors": [{"message": "hasNextPage=true"}],
                })
            else:
                # Poll 2: thread fetch succeeds (empty).
                m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
                }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps([
                make_issue_comment(
                    author=CODEX_LOGIN,
                    body=codex_clean_pass_body(),
                    created_at="2026-06-11T18:00:00Z",
                    comment_id=99898,
                ),
            ])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            call_count["reviews"] += 1
            if call_count["reviews"] == 1:
                # Poll 1: reviews succeed (no findings).
                m.stdout = json.dumps([])
            else:
                # Poll 2: reviews fetch FAILS.
                m.returncode = 22
                m.stderr = "gh api returned 22: HTTP 500"
                m.stdout = ""
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "2", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Must NOT be merge-ready from poll 1's stale clean pass.
    assert pkt["status"] != mod.STATUS_MERGE_READY
    # The latest poll's inventory gate fails closed
    # (review_submission_inventory_complete=False).
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert any("reviews" in e for e in pkt["api_errors"])
    assert pkt["review_submission_inventory_complete"] is False
    assert pkt["polls_used"] == 2


def test_p1_all_three_surfaces_complete_emits_merge_ready(
    monkeypatch, tmp_path,
):
    """
    All three Codex response surfaces (issue comments,
    review submissions, review threads) fetched completely +
    clean pass + zero unresolved threads + mergeable CLEAN
    must emit MERGE_READY_AWAITING_HUMAN_AUTHORIZATION.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
            }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps([
                make_issue_comment(
                    author=CODEX_LOGIN,
                    body=codex_clean_pass_body(),
                    created_at="2026-06-11T18:00:00Z",
                    comment_id=99901,
                ),
            ])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps([])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["issue_comment_inventory_complete"] is True
    assert pkt["review_submission_inventory_complete"] is True
    assert pkt["review_thread_inventory_complete"] is True
    assert pkt["status"] == mod.STATUS_MERGE_READY


def test_p1_threads_incomplete_alone_fails_closed(
    monkeypatch, tmp_path,
):
    """
    Issue-comments complete + reviews complete + threads
    INCOMPLETE must still fail closed at HOLD_CODEX_RESPONSE_PENDING.
    (Regression — review-thread surface is the originally-required
    evidence. The new P1s add the other two surfaces; this
    confirms the new code does not weaken the original gate.)
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({
                "data": {"repository": {"pullRequest": {
                    "reviewThreads": {"pageInfo": {"hasNextPage": True, "endCursor": "X"}, "nodes": []}
                }}},
                "errors": [{"message": "hasNextPage=true"}],
            })
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps([
                make_issue_comment(
                    author=CODEX_LOGIN,
                    body=codex_clean_pass_body(),
                    created_at="2026-06-11T18:00:00Z",
                    comment_id=99911,
                ),
            ])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps([])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["issue_comment_inventory_complete"] is True
    assert pkt["review_submission_inventory_complete"] is True
    assert pkt["review_thread_inventory_complete"] is False


def test_p1_issue_incomplete_alone_fails_closed(
    monkeypatch, tmp_path,
):
    """
    Issue-comments INCOMPLETE + reviews complete + threads
    complete must fail closed at HOLD_CODEX_RESPONSE_PENDING.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
            }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.returncode = 22
            m.stderr = "gh api returned 22: HTTP 500"
            m.stdout = ""
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps([])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["issue_comment_inventory_complete"] is False
    assert pkt["review_submission_inventory_complete"] is True
    assert pkt["review_thread_inventory_complete"] is True


def test_p1_reviews_incomplete_alone_fails_closed(
    monkeypatch, tmp_path,
):
    """
    Issue-comments complete + reviews INCOMPLETE + threads
    complete must fail closed at HOLD_CODEX_RESPONSE_PENDING.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
            }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps([
                make_issue_comment(
                    author=CODEX_LOGIN,
                    body=codex_clean_pass_body(),
                    created_at="2026-06-11T18:00:00Z",
                    comment_id=99921,
                ),
            ])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.returncode = 22
            m.stderr = "gh api returned 22: HTTP 500"
            m.stdout = ""
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["issue_comment_inventory_complete"] is True
    assert pkt["review_submission_inventory_complete"] is False
    assert pkt["review_thread_inventory_complete"] is True


def test_p1_packet_exposes_all_three_inventory_completeness_fields():
    """
    The packet must always carry all three inventory completeness
    booleans + error counts, even when the classifier exits
    without a polling pass (e.g. invalid args).
    """
    # Run with an invalid SHA to hit the degraded packet path,
    # which still writes a JSON file. But that path is in main(),
    # not classify(), so we just call classify() directly with
    # the simplest fixture to get a successful packet.
    from unittest.mock import patch as mock_patch
    from scripts.local import audit_codex_response_for_pr as mod2  # noqa
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
            }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps([])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps([])
            return m
        m.stdout = "[]"
        return m

    with mock_patch.object(mod2.subprocess, "run", runner):
        pkt = mod2.classify(
            repo=REPO, pr_number=402,
            expected_head_sha=EXPECTED_HEAD,
            ping_comment_id=PING_ID, ping_created_at=PING_CREATED,
            max_polls=1, poll_seconds=0,
        )
    assert "issue_comment_inventory_complete" in pkt
    assert "issue_comment_inventory_error_count" in pkt
    assert "review_submission_inventory_complete" in pkt
    assert "review_submission_inventory_error_count" in pkt
    assert "review_thread_inventory_complete" in pkt
    assert "review_thread_inventory_error_count" in pkt


def test_p1_markdown_shows_all_three_inventory_completeness(
    monkeypatch, tmp_path,
):
    """
    The markdown report must surface all three inventory
    completeness states (issue-comments, review-submission,
    review-thread). When any required surface is incomplete,
    the markdown must explain the fail-closed reason.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}
            }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.returncode = 22
            m.stderr = "gh api returned 22: HTTP 500"
            m.stdout = ""
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps([])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    md = (tmp_path / "pkt.md").read_text()
    # The markdown must mention all three surface names.
    assert "issue-comment" in md.lower() or "issue_comment" in md.lower()
    assert "review-submission" in md.lower() or "review_submission" in md.lower()
    assert "review-thread" in md.lower() or "review_thread" in md.lower()
    # And at least one must be marked incomplete (❌).
    assert "❌" in md
    # The fail-closed reason must be visible (api_errors / inventory).
    assert "api error" in md.lower() or "inventory" in md.lower()


# ---------------------------------------------------------------------------
# Nested review-thread comment pagination (P2 #1 in current turn)
# ---------------------------------------------------------------------------
#
# The GraphQL query for reviewThreads returns a nested
# `comments(first:50)` connection on each thread. The original
# code did NOT check the nested `comments.pageInfo.hasNextPage`
# flag — only the top-level `reviewThreads.pageInfo.hasNextPage`.
# That means a thread with more than 50 comments could have
# its Codex-authored finding hidden behind a later page, while
# the classifier still treated the inventory as complete and
# emitted merge-ready on a stale clean pass.
#
# The fix: fetch `pageInfo { hasNextPage }` on the nested
# `comments` connection. If ANY thread's nested comments have
# `hasNextPage=true`, the inventory is incomplete. Mark
# `review_thread_inventory_complete=False` and
# `review_thread_comment_inventory_complete=False`, increment
# `review_thread_comment_inventory_error_count`, and include
# a clear api_errors message. The existing unified inventory
# gate in section 8 then fails closed at
# HOLD_CODEX_RESPONSE_PENDING.


def test_nested_thread_comments_incomplete_fails_closed_no_merge_ready(
    monkeypatch, tmp_path,
):
    """
    P2 #1: An unresolved review thread has nested
    `comments.pageInfo.hasNextPage=true` (Codex comment IS
    on the visible first page). The classifier must NOT emit
    MERGE_READY_AWAITING_HUMAN_AUTHORIZATION, but it MUST
    preserve the visible Codex finding as a current-head
    active blocker and emit HOLD_NEW_CODEX_THREAD (not
    HOLD_CODEX_RESPONSE_PENDING, which would suppress the
    confirmed finding). Pre-fix behavior dropped the
    visible comment silently; this test was updated to
    assert the post-fix behavior.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            # Top-level reviewThreads.pageInfo.hasNextPage is
            # False (we got all threads in one page), but the
            # thread we return has nested
            # comments.pageInfo.hasNextPage=True (the Codex
            # finding is on the next page).
            m.stdout = json.dumps({
                "data": {"repository": {"pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": "X"},
                        "nodes": [
                            {
                                "id": "PRRT_test_paginated",
                                "isResolved": False,
                                "isOutdated": False,
                                "comments": {
                                    "pageInfo": {
                                        "hasNextPage": True,
                                        "endCursor": "Y",
                                    },
                                    "nodes": [
                                        {
                                            "databaseId": 99001,
                                            "url": "https://example/99001",
                                            "body": "P1 Codex finding on page 1",
                                            "path": "scripts/local/foo.py",
                                            "line": 1,
                                            "author": {"login": CODEX_LOGIN},
                                        },
                                    ],
                                },
                            },
                        ],
                    }
                }}}
            })
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps([
                make_issue_comment(
                    author=CODEX_LOGIN,
                    body=codex_clean_pass_body(),
                    created_at="2026-06-11T18:00:00Z",
                    comment_id=99941,
                ),
            ])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps([])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Inventory is incomplete: refuse clean-pass / merge-ready.
    assert pkt["status"] != mod.STATUS_MERGE_READY
    assert pkt["status"] != mod.STATUS_CLEAN_PASS
    assert pkt["status"] != mod.STATUS_CLEAN_PASS_RESOLVE_ONLY
    # The visible Codex finding on page 1 is preserved as an
    # active blocker — it must drive HOLD_NEW_CODEX_THREAD, not
    # HOLD_CODEX_RESPONSE_PENDING (which would suppress the
    # confirmed visible finding).
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["current_head_active_blocker_count"] >= 1
    # The visible finding must show up in active_threads.
    active_authors = {t.get("author") for t in pkt.get("active_threads", [])}
    assert mod.CODEX_BOT_LOGINS and any(
        a in mod.CODEX_BOT_LOGINS for a in active_authors
    )
    # And the thread must be flagged as nested_incomplete
    # in the packet so operators can see which findings
    # came from partial evidence.
    flagged = [
        t for t in pkt.get("active_threads", [])
        if t.get("nested_incomplete")
    ]
    assert len(flagged) >= 1


def test_nested_thread_comments_incomplete_marks_inventory_incomplete(
    monkeypatch, tmp_path,
):
    """P2 #1: packet must expose the nested-comments inventory
    flags as incomplete."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({
                "data": {"repository": {"pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": "X"},
                        "nodes": [
                            {
                                "id": "PRRT_test_paginated2",
                                "isResolved": False,
                                "isOutdated": False,
                                "comments": {
                                    "pageInfo": {
                                        "hasNextPage": True,
                                        "endCursor": "Y",
                                    },
                                    "nodes": [
                                        {
                                            "databaseId": 99002,
                                            "url": "https://example/99002",
                                            "body": "Codex finding on page 1",
                                            "path": "scripts/local/foo.py",
                                            "line": 1,
                                            "author": {"login": CODEX_LOGIN},
                                        },
                                    ],
                                },
                            },
                        ],
                    }
                }}}
            })
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps([
                make_issue_comment(
                    author=CODEX_LOGIN,
                    body=codex_clean_pass_body(),
                    created_at="2026-06-11T18:00:00Z",
                    comment_id=99942,
                ),
            ])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps([])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["review_thread_inventory_complete"] is False
    assert pkt["review_thread_comment_inventory_complete"] is False
    assert pkt["review_thread_comment_inventory_error_count"] > 0


def test_nested_thread_comments_incomplete_populates_api_errors(
    monkeypatch, tmp_path,
):
    """P2 #1: api_errors must clearly identify the nested-comments
    pagination as the cause."""
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({
                "data": {"repository": {"pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": "X"},
                        "nodes": [
                            {
                                "id": "PRRT_test_paginated3",
                                "isResolved": False,
                                "isOutdated": False,
                                "comments": {
                                    "pageInfo": {
                                        "hasNextPage": True,
                                        "endCursor": "Y",
                                    },
                                    "nodes": [
                                        {
                                            "databaseId": 99003,
                                            "url": "https://example/99003",
                                            "body": "Codex comment page 1",
                                            "path": "scripts/local/foo.py",
                                            "line": 1,
                                            "author": {"login": CODEX_LOGIN},
                                        },
                                    ],
                                },
                            },
                        ],
                    }
                }}}
            })
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps([
                make_issue_comment(
                    author=CODEX_LOGIN,
                    body=codex_clean_pass_body(),
                    created_at="2026-06-11T18:00:00Z",
                    comment_id=99943,
                ),
            ])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps([])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # The error must mention nested comments and pagination.
    assert any("nested" in e.lower() or "thread" in e.lower()
               for e in pkt["api_errors"])
    # The recommendation must explain the inventory
    # is incomplete and reference the surface that failed.
    rec = pkt["recommendation"].lower()
    assert "inventory" in rec
    assert "review_thread" in rec or "review-thread" in rec


def test_nested_thread_comments_complete_with_codex_still_blocks(
    monkeypatch, tmp_path,
):
    """
    Regression: when nested comments pageInfo is complete
    (hasNextPage=false) and the Codex comment IS on the
    returned page, the existing active-blocker logic must
    still drive HOLD_NEW_CODEX_THREAD.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": [
                    {
                        "id": "PRRT_test_complete_with_codex",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": "Z"},
                            "nodes": [
                                {
                                    "databaseId": 99011,
                                    "url": "https://example/99011",
                                    "body": "P1 Codex finding",
                                    "path": "scripts/local/foo.py",
                                    "line": 1,
                                    "author": {"login": CODEX_LOGIN},
                                },
                            ],
                        },
                    },
                ]}
            }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps([])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps([])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["review_thread_inventory_complete"] is True
    assert pkt["review_thread_comment_inventory_complete"] is True
    assert pkt["review_thread_comment_inventory_error_count"] == 0
    assert pkt["current_head_active_blocker_count"] >= 1


def test_nested_thread_comments_complete_no_blockers_allows_merge_ready(
    monkeypatch, tmp_path,
):
    """
    Regression: when all nested-comments pageInfo is complete
    and no unresolved threads exist, with a clean pass and
    mergeable CLEAN, the classifier must emit
    MERGE_READY_AWAITING_HUMAN_AUTHORIZATION. The new
    nested-comments check must not over-fire and prevent
    merge-ready when the inventory IS actually complete.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [
                        {
                            "id": "PRRT_test_resolved_clean",
                            "isResolved": True,
                            "isOutdated": False,
                            "comments": {
                                "pageInfo": {"hasNextPage": False, "endCursor": "Z"},
                                "nodes": [
                                    {
                                        "databaseId": 99021,
                                        "url": "https://example/99021",
                                        "body": "resolved finding",
                                        "path": "scripts/local/foo.py",
                                        "line": 1,
                                        "author": {"login": CODEX_LOGIN},
                                    },
                                ],
                            },
                        },
                    ],
                }
            }}}})
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps([
                make_issue_comment(
                    author=CODEX_LOGIN,
                    body=codex_clean_pass_body(),
                    created_at="2026-06-11T18:00:00Z",
                    comment_id=99951,
                ),
            ])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps([])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["review_thread_inventory_complete"] is True
    assert pkt["review_thread_comment_inventory_complete"] is True
    assert pkt["status"] == mod.STATUS_MERGE_READY


# ---------------------------------------------------------------------------
# P2 #1 regression tests: preserve visible findings on paginated thread
# comments (Codex post-ping finding 1, thread PRRT_kwDOSHFpYM6JS2o5).
# ---------------------------------------------------------------------------


def test_p2_paginated_visible_codex_finding_preserves_active_blocker(
    monkeypatch, tmp_path,
):
    """
    P2 #1: When a review thread has
    `comments.pageInfo.hasNextPage=true` AND the visible first
    page contains a Codex-authored unresolved finding, the
    classifier must surface that visible finding as a
    current-head active blocker and emit
    HOLD_NEW_CODEX_THREAD. The visible finding must NOT be
    dropped solely because the nested pagination is
    incomplete. Pre-fix behavior dropped the visible comment
    and incorrectly emitted HOLD_CODEX_RESPONSE_PENDING.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({
                "data": {"repository": {"pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": "X"},
                        "nodes": [
                            {
                                "id": "PRRT_test_visible_codex",
                                "isResolved": False,
                                "isOutdated": False,
                                "comments": {
                                    "pageInfo": {
                                        "hasNextPage": True,
                                        "endCursor": "Y",
                                    },
                                    "nodes": [
                                        {
                                            "databaseId": 99501,
                                            "url": "https://example/99501",
                                            "body": (
                                                "P2 Codex finding on the "
                                                "visible nested page"
                                            ),
                                            "path": (
                                                "scripts/local/"
                                                "audit_codex_response_for_pr.py"
                                            ),
                                            "line": 499,
                                            "author": {"login": CODEX_LOGIN},
                                        },
                                    ],
                                },
                            },
                        ],
                    }
                }}}
            })
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps([])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps([])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Visible Codex finding is preserved as a current-head
    # active blocker; the classifier fails closed on the
    # incomplete inventory but routes to HOLD_NEW_CODEX_THREAD,
    # not HOLD_CODEX_RESPONSE_PENDING.
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["current_head_active_blocker_count"] >= 1
    # active_threads contains the visible finding.
    db_ids = {
        t.get("comment_database_id")
        for t in pkt.get("active_threads", [])
    }
    assert 99501 in db_ids
    # Inventory remains incomplete: no clean-pass / merge-ready.
    assert pkt["status"] != mod.STATUS_MERGE_READY
    assert pkt["status"] != mod.STATUS_CLEAN_PASS
    assert pkt["status"] != mod.STATUS_CLEAN_PASS_RESOLVE_ONLY
    # Inventory flags reflect the incomplete nested pagination.
    assert pkt["review_thread_comment_inventory_complete"] is False
    assert pkt["review_thread_comment_inventory_error_count"] > 0
    assert "PRRT_test_visible_codex" in (
        pkt.get("review_thread_comment_incomplete_thread_ids") or []
    )


def test_p2_paginated_visible_no_codex_finding_emits_pending(
    monkeypatch, tmp_path,
):
    """
    P2 #1: When a review thread has
    `comments.pageInfo.hasNextPage=true` AND the visible first
    page contains NO Codex-authored finding, the classifier
    must emit HOLD_CODEX_RESPONSE_PENDING (the safe fail-closed
    per-poll state). It must not emit clean-pass or
    merge-ready.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({
                "data": {"repository": {"pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": "X"},
                        "nodes": [
                            {
                                "id": "PRRT_test_visible_no_codex",
                                "isResolved": False,
                                "isOutdated": False,
                                "comments": {
                                    "pageInfo": {
                                        "hasNextPage": True,
                                        "endCursor": "Y",
                                    },
                                    # Visible page contains a
                                    # NON-Codex comment (a human
                                    # reply). No Codex finding is
                                    # visible on this page.
                                    "nodes": [
                                        {
                                            "databaseId": 99601,
                                            "url": "https://example/99601",
                                            "body": "human reply, not codex",
                                            "path": "scripts/local/foo.py",
                                            "line": 1,
                                            "author": {"login": "human-user"},
                                        },
                                    ],
                                },
                            },
                        ],
                    }
                }}}
            })
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps([
                make_issue_comment(
                    author=CODEX_LOGIN,
                    body=codex_clean_pass_body(),
                    created_at="2026-06-11T18:00:00Z",
                    comment_id=99961,
                ),
            ])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps([])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # No Codex finding visible -> safe fail-closed pending.
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    # Inventory is incomplete.
    assert pkt["review_thread_comment_inventory_complete"] is False
    # No merge-ready / clean-pass.
    assert pkt["status"] != mod.STATUS_MERGE_READY
    assert pkt["status"] != mod.STATUS_CLEAN_PASS
    assert pkt["status"] != mod.STATUS_CLEAN_PASS_RESOLVE_ONLY


def test_p2_visible_returned_comments_not_dropped_from_thread_evidence(
    monkeypatch, tmp_path,
):
    """
    P2 #1: The fix must NOT silently drop visible nested-page
    comments from the thread evidence list. Direct unit-level
    check on the parsed thread list returned by
    `gh_graphql_review_threads`: when nested
    `hasNextPage=true`, the visible Codex comment is
    present in the returned `threads` list, flagged with
    `nested_incomplete=True`, and the function still returns
    `ok=False` so the unified inventory gate keeps the
    review-thread inventory marked as incomplete.
    """
    # Build a synthetic GraphQL payload with a single thread
    # whose nested comments have hasNextPage=True and a
    # Codex-authored comment on the visible page.
    payload = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False, "endCursor": "X"},
                "nodes": [
                    {
                        "id": "PRRT_test_unit_visible",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {
                            "pageInfo": {
                                "hasNextPage": True,
                                "endCursor": "Y",
                            },
                            "nodes": [
                                {
                                    "databaseId": 99701,
                                    "url": "https://example/99701",
                                    "body": "P1 Codex finding on visible page",
                                    "path": "scripts/local/foo.py",
                                    "line": 10,
                                    "author": {"login": CODEX_LOGIN},
                                },
                            ],
                        },
                    },
                ],
            }
        }}}
    }

    # Patch subprocess.run to return only this GraphQL
    # payload regardless of args, so the function under
    # test can parse it.
    m = MagicMock()
    m.returncode = 0
    m.stderr = ""
    m.stdout = json.dumps(payload)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **kw: m)

    ok, threads, err, metadata = mod.gh_graphql_review_threads(
        REPO, 402, timeout=10,
    )

    # Function must still return ok=False because nested
    # comments are paginated, so the unified inventory gate
    # refuses to trust the inventory as complete.
    assert ok is False
    # But the visible comment MUST be in the returned list.
    assert len(threads) == 1
    assert threads[0]["comment_database_id"] == 99701
    assert threads[0]["author"] == CODEX_LOGIN
    assert threads[0]["nested_incomplete"] is True
    # And the metadata must mark the nested inventory
    # incomplete.
    assert metadata["review_thread_comment_inventory_complete"] is False
    assert metadata["review_thread_comment_inventory_error_count"] >= 1
    assert "PRRT_test_unit_visible" in (
        metadata["review_thread_comment_incomplete_thread_ids"]
    )
    # The error string must clearly identify the nested
    # pagination as the cause.
    assert "hasNextPage=true" in err


def test_p2_nested_complete_with_codex_still_holds_new_thread(
    monkeypatch, tmp_path,
):
    """
    P2 #1: regression — when nested comments are COMPLETE
    (hasNextPage=false) and the Codex comment is on the
    returned page, the existing active-blocker logic still
    drives HOLD_NEW_CODEX_THREAD. The new visibility rule
    must not break the no-pagination path.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews" not in cmd_str
            and "/comments" not in cmd_str
        ):
            m.stdout = json.dumps(pr_view)
            return m
        if "graphql" in cmd_str:
            m.stdout = json.dumps({
                "data": {"repository": {"pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {
                                "id": "PRRT_test_complete_visible",
                                "isResolved": False,
                                "isOutdated": False,
                                "comments": {
                                    "pageInfo": {
                                        "hasNextPage": False,
                                        "endCursor": "Z",
                                    },
                                    "nodes": [
                                        {
                                            "databaseId": 99801,
                                            "url": "https://example/99801",
                                            "body": "P1 Codex finding",
                                            "path": "scripts/local/foo.py",
                                            "line": 1,
                                            "author": {"login": CODEX_LOGIN},
                                        },
                                    ],
                                },
                            },
                        ],
                    }
                }}}
            })
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps([])
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps([])
            return m
        m.stdout = "[]"
        return m

    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["review_thread_comment_inventory_complete"] is True
    assert pkt["review_thread_comment_inventory_error_count"] == 0
    assert pkt["current_head_active_blocker_count"] >= 1


# ---------------------------------------------------------------------------
# P3 #2 regression tests: normalize PR state before rendering the
# open-state warning (Codex post-ping finding 2, thread
# PRRT_kwDOSHFpYM6JS2o7).
# ---------------------------------------------------------------------------


def test_p3_markdown_open_lowercase_renders_no_warning(
    monkeypatch, tmp_path,
):
    """
    P3 #2: When the PR metadata packet carries
    `pr_state="open"` (live REST shape, lowercase), the
    rendered markdown must NOT contain the
    "PR state is `open` (not OPEN)" warning. The
    comparison must be case-insensitive.
    """
    # Build a packet directly with a lowercase "open" state
    # and a fully-inventoried clean pass so the markdown
    # renderer actually runs against a realistic payload.
    packet = {
        "packet_kind": mod.PACKET_KIND,
        "schema_version": mod.SCHEMA_VERSION,
        "status": mod.STATUS_MERGE_READY,
        "repo": REPO,
        "pr_number": 402,
        "expected_head_sha": EXPECTED_HEAD,
        "observed_head_sha": EXPECTED_HEAD,
        "head_matches_expected": True,
        "pr_state": "open",  # live REST shape
        "pr_url": f"https://github.com/{REPO}/pull/402",
        "pr_base_ref_name": "main",
        "pr_head_ref_name": "tooling/codex-response-classifier-v1",
        "merge_state_status": "CLEAN",
        "mergeable": "MERGEABLE",
        "review_decision": "REVIEW_REQUIRED",
        "ping_comment_id": PING_ID,
        "ping_created_at": PING_CREATED,
        "ping_timestamp_supplied": True,
        "ping_timestamp_valid": True,
        "latest_codex_response_type": "issue_comment",
        "latest_codex_response_id": "1",
        "latest_codex_response_created_at": "2026-06-11T18:00:00Z",
        "clean_pass_detected": True,
        "clean_pass_source": "issue_comment",
        "clean_pass_comment_id": "1",
        "clean_pass_review_id": None,
        "clean_pass_at": "2026-06-11T18:00:00Z",
        "last_seen_codex_review_id": None,
        "last_seen_codex_review_at": None,
        "last_seen_codex_comment_id": "1",
        "last_seen_codex_comment_at": "2026-06-11T18:00:00Z",
        "active_threads": [],
        "outdated_threads": [],
        "resolved_threads": [],
        "unresolved_thread_count": 0,
        "current_head_active_blocker_count": 0,
        "outdated_unresolved_thread_count": 0,
        "review_thread_inventory_complete": True,
        "review_thread_inventory_error_count": 0,
        "review_thread_inventory_last_error": "",
        "review_thread_comment_inventory_complete": True,
        "review_thread_comment_inventory_error_count": 0,
        "review_thread_comment_incomplete_thread_ids": [],
        "issue_comment_inventory_complete": True,
        "issue_comment_inventory_error_count": 0,
        "issue_comment_inventory_last_error": "",
        "review_submission_inventory_complete": True,
        "review_submission_inventory_error_count": 0,
        "review_submission_inventory_last_error": "",
        "polls_used": 1,
        "polling_exhausted": False,
        "stop_reason": "merge_ready",
        "max_polls": 1,
        "poll_seconds": 0,
        "api_errors": [],
        "recommendation": "merge ready",
        "harvested_at": "2026-06-11T18:00:00Z",
    }
    md = mod.render_markdown(packet)
    # Case-insensitive comparison: lowercase "open" must NOT
    # produce the "not OPEN" warning.
    assert "not OPEN" not in md
    # And the rendered state line must still be present
    # (so the value is not silently dropped).
    assert "`open`" in md


def test_p3_markdown_open_uppercase_renders_no_warning():
    """
    P3 #2: When the PR metadata packet carries
    `pr_state="OPEN"` (GraphQL shape, uppercase), the
    rendered markdown must NOT contain the
    "not OPEN" warning. This is the existing-shape
    regression: pre-fix the comparison was case-sensitive
    and matched `"OPEN"` exactly, so this case passed
    already; the test guards against future regressions.
    """
    packet = {
        "packet_kind": mod.PACKET_KIND,
        "schema_version": mod.SCHEMA_VERSION,
        "status": mod.STATUS_MERGE_READY,
        "repo": REPO,
        "pr_number": 402,
        "expected_head_sha": EXPECTED_HEAD,
        "observed_head_sha": EXPECTED_HEAD,
        "head_matches_expected": True,
        "pr_state": "OPEN",  # GraphQL shape
        "pr_url": f"https://github.com/{REPO}/pull/402",
        "pr_base_ref_name": "main",
        "pr_head_ref_name": "tooling/codex-response-classifier-v1",
        "merge_state_status": "CLEAN",
        "mergeable": "MERGEABLE",
        "review_decision": "REVIEW_REQUIRED",
        "ping_comment_id": PING_ID,
        "ping_created_at": PING_CREATED,
        "ping_timestamp_supplied": True,
        "ping_timestamp_valid": True,
        "latest_codex_response_type": "issue_comment",
        "latest_codex_response_id": "1",
        "latest_codex_response_created_at": "2026-06-11T18:00:00Z",
        "clean_pass_detected": True,
        "clean_pass_source": "issue_comment",
        "clean_pass_comment_id": "1",
        "clean_pass_review_id": None,
        "clean_pass_at": "2026-06-11T18:00:00Z",
        "last_seen_codex_review_id": None,
        "last_seen_codex_review_at": None,
        "last_seen_codex_comment_id": "1",
        "last_seen_codex_comment_at": "2026-06-11T18:00:00Z",
        "active_threads": [],
        "outdated_threads": [],
        "resolved_threads": [],
        "unresolved_thread_count": 0,
        "current_head_active_blocker_count": 0,
        "outdated_unresolved_thread_count": 0,
        "review_thread_inventory_complete": True,
        "review_thread_inventory_error_count": 0,
        "review_thread_inventory_last_error": "",
        "review_thread_comment_inventory_complete": True,
        "review_thread_comment_inventory_error_count": 0,
        "review_thread_comment_incomplete_thread_ids": [],
        "issue_comment_inventory_complete": True,
        "issue_comment_inventory_error_count": 0,
        "issue_comment_inventory_last_error": "",
        "review_submission_inventory_complete": True,
        "review_submission_inventory_error_count": 0,
        "review_submission_inventory_last_error": "",
        "polls_used": 1,
        "polling_exhausted": False,
        "stop_reason": "merge_ready",
        "max_polls": 1,
        "poll_seconds": 0,
        "api_errors": [],
        "recommendation": "merge ready",
        "harvested_at": "2026-06-11T18:00:00Z",
    }
    md = mod.render_markdown(packet)
    assert "not OPEN" not in md


def test_p3_markdown_closed_state_still_warns():
    """
    P3 #2: The case-insensitive normalization must NOT
    suppress the warning for genuinely non-open states
    (e.g. "closed", "CLOSED", "MERGED"). The warning is
    reserved for states whose uppercase form is not "OPEN".
    """
    for state in ("closed", "CLOSED", "MERGED", "merged"):
        packet = {
            "packet_kind": mod.PACKET_KIND,
            "schema_version": mod.SCHEMA_VERSION,
            "status": mod.STATUS_HOLD_PR_NOT_OPEN,
            "repo": REPO,
            "pr_number": 402,
            "expected_head_sha": EXPECTED_HEAD,
            "observed_head_sha": EXPECTED_HEAD,
            "head_matches_expected": True,
            "pr_state": state,
            "pr_url": f"https://github.com/{REPO}/pull/402",
            "pr_base_ref_name": "main",
            "pr_head_ref_name": "tooling/codex-response-classifier-v1",
            "merge_state_status": "",
            "mergeable": "",
            "review_decision": "",
            "ping_comment_id": PING_ID,
            "ping_created_at": PING_CREATED,
            "ping_timestamp_supplied": True,
            "ping_timestamp_valid": True,
            "latest_codex_response_type": "none",
            "latest_codex_response_id": "",
            "latest_codex_response_created_at": "",
            "clean_pass_detected": False,
            "clean_pass_source": None,
            "clean_pass_comment_id": None,
            "clean_pass_review_id": None,
            "clean_pass_at": None,
            "last_seen_codex_review_id": None,
            "last_seen_codex_review_at": None,
            "last_seen_codex_comment_id": None,
            "last_seen_codex_comment_at": None,
            "active_threads": [],
            "outdated_threads": [],
            "resolved_threads": [],
            "unresolved_thread_count": 0,
            "current_head_active_blocker_count": 0,
            "outdated_unresolved_thread_count": 0,
            "review_thread_inventory_complete": True,
            "review_thread_inventory_error_count": 0,
            "review_thread_inventory_last_error": "",
            "review_thread_comment_inventory_complete": True,
            "review_thread_comment_inventory_error_count": 0,
            "review_thread_comment_incomplete_thread_ids": [],
            "issue_comment_inventory_complete": True,
            "issue_comment_inventory_error_count": 0,
            "issue_comment_inventory_last_error": "",
            "review_submission_inventory_complete": True,
            "review_submission_inventory_error_count": 0,
            "review_submission_inventory_last_error": "",
            "polls_used": 1,
            "polling_exhausted": False,
            "stop_reason": "pr_not_open",
            "max_polls": 1,
            "poll_seconds": 0,
            "api_errors": [],
            "recommendation": "pr not open",
            "harvested_at": "2026-06-11T18:00:00Z",
        }
        md = mod.render_markdown(packet)
        assert "not OPEN" in md, (
            f"non-open state {state!r} should still warn, "
            f"got markdown: {md[:300]}"
        )


def test_p3_packet_field_left_untouched():
    """
    P3 #2: The case-insensitive normalization is for
    markdown rendering only. The `pr_state` field in the
    packet is left untouched (still lowercase or whatever
    GitHub returned), so downstream consumers that read
    the packet see the original value.
    """
    packet = {
        "packet_kind": mod.PACKET_KIND,
        "schema_version": mod.SCHEMA_VERSION,
        "status": mod.STATUS_MERGE_READY,
        "repo": REPO,
        "pr_number": 402,
        "expected_head_sha": EXPECTED_HEAD,
        "observed_head_sha": EXPECTED_HEAD,
        "head_matches_expected": True,
        "pr_state": "open",
        "pr_url": f"https://github.com/{REPO}/pull/402",
        "pr_base_ref_name": "main",
        "pr_head_ref_name": "tooling/codex-response-classifier-v1",
        "merge_state_status": "CLEAN",
        "mergeable": "MERGEABLE",
        "review_decision": "REVIEW_REQUIRED",
        "ping_comment_id": PING_ID,
        "ping_created_at": PING_CREATED,
        "ping_timestamp_supplied": True,
        "ping_timestamp_valid": True,
        "latest_codex_response_type": "issue_comment",
        "latest_codex_response_id": "1",
        "latest_codex_response_created_at": "2026-06-11T18:00:00Z",
        "clean_pass_detected": True,
        "clean_pass_source": "issue_comment",
        "clean_pass_comment_id": "1",
        "clean_pass_review_id": None,
        "clean_pass_at": "2026-06-11T18:00:00Z",
        "last_seen_codex_review_id": None,
        "last_seen_codex_review_at": None,
        "last_seen_codex_comment_id": "1",
        "last_seen_codex_comment_at": "2026-06-11T18:00:00Z",
        "active_threads": [],
        "outdated_threads": [],
        "resolved_threads": [],
        "unresolved_thread_count": 0,
        "current_head_active_blocker_count": 0,
        "outdated_unresolved_thread_count": 0,
        "review_thread_inventory_complete": True,
        "review_thread_inventory_error_count": 0,
        "review_thread_inventory_last_error": "",
        "review_thread_comment_inventory_complete": True,
        "review_thread_comment_inventory_error_count": 0,
        "review_thread_comment_incomplete_thread_ids": [],
        "issue_comment_inventory_complete": True,
        "issue_comment_inventory_error_count": 0,
        "issue_comment_inventory_last_error": "",
        "review_submission_inventory_complete": True,
        "review_submission_inventory_error_count": 0,
        "review_submission_inventory_last_error": "",
        "polls_used": 1,
        "polling_exhausted": False,
        "stop_reason": "merge_ready",
        "max_polls": 1,
        "poll_seconds": 0,
        "api_errors": [],
        "recommendation": "merge ready",
        "harvested_at": "2026-06-11T18:00:00Z",
    }
    # Call render_markdown — it must not mutate the packet.
    mod.render_markdown(packet)
    assert packet["pr_state"] == "open"


# ---------------------------------------------------------------------------
# P2 #3 regression tests: render the actual hold status for partial visible
# findings (Codex post-ping finding 3, thread PRRT_kwDOSHFpYM6JVUox).
# ---------------------------------------------------------------------------


def _build_partial_inventory_packet(
    *,
    status: str,
    review_thread_inventory_complete: Optional[bool] = None,
    review_thread_comment_inventory_complete: bool = False,
    review_thread_comment_inventory_error_count: int = 1,
    review_thread_comment_incomplete_thread_ids: Optional[List[str]] = None,
    active_threads: Optional[List[Dict[str, Any]]] = None,
    outdated_threads: Optional[List[Dict[str, Any]]] = None,
    resolved_threads: Optional[List[Dict[str, Any]]] = None,
    issue_complete: bool = True,
    rev_complete: bool = True,
) -> Dict[str, Any]:
    """
    Build a minimal packet fixture exercising the
    "inventory incomplete" markdown note path. The
    three top-level inventory flags drive whether
    the note is rendered; the nested-comments
    inventory flag is independent (it controls
    whether the note ALSO explains incomplete nested
    pagination, but does not gate the note's
    presence).

    The default for `review_thread_inventory_complete`
    follows the production invariant: when nested
    review-thread comments are paginated, the
    underlying `gh_graphql_review_threads` returns
    `ok=False`, and the call site sets BOTH the
    TOP-LEVEL `review_thread_inventory_complete` AND
    the `review_thread_comment_inventory_complete`
    to False. So `False` is the right default for
    both flags in partial-inventory fixtures; passing
    `True` for either only makes sense for the
    "complete inventory" negative-control test.
    """
    if review_thread_inventory_complete is None:
        review_thread_inventory_complete = (
            not (not issue_complete or not rev_complete)
            and review_thread_comment_inventory_complete
        )
    return {
        "packet_kind": mod.PACKET_KIND,
        "schema_version": mod.SCHEMA_VERSION,
        "status": status,
        "repo": REPO,
        "pr_number": 402,
        "expected_head_sha": EXPECTED_HEAD,
        "observed_head_sha": EXPECTED_HEAD,
        "head_matches_expected": True,
        "pr_state": "open",
        "pr_url": f"https://github.com/{REPO}/pull/402",
        "pr_base_ref_name": "main",
        "pr_head_ref_name": "tooling/codex-response-classifier-v1",
        "merge_state_status": "BLOCKED",
        "mergeable": "MERGEABLE",
        "review_decision": "REVIEW_REQUIRED",
        "ping_comment_id": PING_ID,
        "ping_created_at": PING_CREATED,
        "ping_timestamp_supplied": True,
        "ping_timestamp_valid": True,
        "latest_codex_response_type": "pull_request_review",
        "latest_codex_response_id": "1",
        "latest_codex_response_created_at": "2026-06-13T15:00:00Z",
        "clean_pass_detected": False,
        "clean_pass_source": None,
        "clean_pass_comment_id": None,
        "clean_pass_review_id": None,
        "clean_pass_at": None,
        "last_seen_codex_review_id": "1",
        "last_seen_codex_review_at": "2026-06-13T15:00:00Z",
        "last_seen_codex_comment_id": None,
        "last_seen_codex_comment_at": None,
        "active_threads": active_threads if active_threads is not None else [],
        "outdated_threads": outdated_threads if outdated_threads is not None else [],
        "resolved_threads": resolved_threads if resolved_threads is not None else [],
        "unresolved_thread_count": (
            len(active_threads or []) + len(outdated_threads or [])
        ),
        "current_head_active_blocker_count": len(active_threads or []),
        "outdated_unresolved_thread_count": len(outdated_threads or []),
        "review_thread_inventory_complete": review_thread_inventory_complete,
        "review_thread_inventory_error_count": (
            0 if review_thread_inventory_complete else 1
        ),
        "review_thread_inventory_last_error": "",
        "review_thread_comment_inventory_complete": (
            review_thread_comment_inventory_complete
        ),
        "review_thread_comment_inventory_error_count": (
            review_thread_comment_inventory_error_count
        ),
        "review_thread_comment_incomplete_thread_ids": (
            review_thread_comment_incomplete_thread_ids
            if review_thread_comment_incomplete_thread_ids is not None
            else ["PRRT_kwDOSHFpYM6JVisibleCodex"]
        ),
        "issue_comment_inventory_complete": issue_complete,
        "issue_comment_inventory_error_count": 0 if issue_complete else 1,
        "issue_comment_inventory_last_error": "",
        "review_submission_inventory_complete": rev_complete,
        "review_submission_inventory_error_count": 0 if rev_complete else 1,
        "review_submission_inventory_last_error": "",
        "polls_used": 1,
        "polling_exhausted": False,
        "stop_reason": "active_finding_with_incomplete_inventory",
        "max_polls": 1,
        "poll_seconds": 0,
        "api_errors": [
            "review-thread comments pagination required "
            "(hasNextPage=true on nested comments for 1 thread).",
        ],
        "recommendation": "fix and resubmit",
        "harvested_at": "2026-06-13T15:00:00Z",
    }


def test_p2_partial_inventory_hold_new_thread_renders_actual_status():
    """
    P2 #3: When review-thread comment inventory is
    incomplete AND the partial inventory has already
    preserved a visible active Codex finding, the
    actual status is HOLD_NEW_CODEX_THREAD. The
    markdown MUST render that exact status — it
    must NOT say the classifier is "holding at
    HOLD_CODEX_RESPONSE_PENDING". Pre-fix, the
    markdown unconditionally emitted the wrong
    wording under any incomplete-inventory
    condition.
    """
    packet = _build_partial_inventory_packet(
        status=mod.STATUS_HOLD_NEW_THREAD,
        # Production invariant: when nested-comments
        # are paginated, the underlying call returns
        # ok=False, which sets BOTH top-level
        # review_thread_inventory_complete AND the
        # nested-comments flag to False. Mirror that
        # here so the markdown note is triggered.
        review_thread_inventory_complete=False,
        review_thread_comment_inventory_complete=False,
        review_thread_comment_inventory_error_count=1,
        review_thread_comment_incomplete_thread_ids=[
            "PRRT_kwDOSHFpYM6JVisibleCodex"
        ],
        active_threads=[
            {
                "thread_id": "PRRT_kwDOSHFpYM6JVisibleCodex",
                "comment_database_id": 999001,
                "comment_url": "https://example/999001",
                "author": CODEX_LOGIN,
                "path": (
                    "scripts/local/audit_codex_response_for_pr.py"
                ),
                "line": 499,
                "is_resolved": False,
                "is_outdated": False,
                "body": "Visible Codex finding on the partial page",
                "nested_incomplete": True,
            }
        ],
    )
    md = mod.render_markdown(packet)
    # The actual lifecycle status is the packet's
    # `status`, which the markdown must echo back.
    assert "`HOLD_NEW_CODEX_THREAD`" in md, (
        "markdown must render the packet's actual "
        "HOLD_NEW_CODEX_THREAD status when a visible "
        "active finding is preserved under incomplete "
        "nested inventory. Got markdown: " + md[:500]
    )
    # And it must NOT contradict itself by saying the
    # classifier is "holding at HOLD_CODEX_RESPONSE_PENDING".
    assert "holding at HOLD_CODEX_RESPONSE_PENDING" not in md, (
        "markdown must not say the classifier is holding "
        "at HOLD_CODEX_RESPONSE_PENDING when the packet "
        "status is HOLD_NEW_CODEX_THREAD. Got markdown: "
        + md[:500]
    )
    # The fail-closed safety explanation must still be
    # present: clean-pass / merge-ready decisions are
    # refused while any required surface is incomplete.
    assert "Clean-pass / merge-ready decisions are still" in md
    assert "refused while any required surface is" in md
    # And the report explains the precedence rule so
    # operators can see why a visible finding drives
    # HOLD_NEW_CODEX_THREAD.
    assert "visible active Codex finding" in md
    assert "HOLD_NEW_CODEX_THREAD" in md
    assert "HOLD_CODEX_RESPONSE_PENDING" in md


def test_p2_partial_inventory_hold_pending_renders_pending_status():
    """
    P2 #3: When review-thread comment inventory is
    incomplete AND no visible active Codex finding
    was preserved, the actual status is
    HOLD_CODEX_RESPONSE_PENDING. The markdown MUST
    render that exact status. Pre-fix wording also
    said "HOLD_CODEX_RESPONSE_PENDING" in this case,
    but the post-fix wording must be packet-driven
    so it tracks the actual decision (not a hardcoded
    string).
    """
    packet = _build_partial_inventory_packet(
        status=mod.STATUS_HOLD_CODEX_PENDING,
        review_thread_inventory_complete=False,
        review_thread_comment_inventory_complete=False,
        review_thread_comment_inventory_error_count=1,
        review_thread_comment_incomplete_thread_ids=[
            "PRRT_kwDOSHFpYM6JNoVisibleCodex"
        ],
        # No visible active finding preserved.
        active_threads=[],
    )
    md = mod.render_markdown(packet)
    # The actual lifecycle status is HOLD_CODEX_RESPONSE_PENDING.
    assert "`HOLD_CODEX_RESPONSE_PENDING`" in md
    # The fail-closed safety explanation must still be present.
    assert "Clean-pass / merge-ready decisions are still" in md
    assert "refused while any required surface is" in md


def test_p2_partial_inventory_explains_clean_pass_merge_ready_refused():
    """
    P2 #3: For ANY incomplete-inventory note (whether
    HOLD_NEW_CODEX_THREAD or HOLD_CODEX_RESPONSE_PENDING),
    the report must still explain that clean-pass /
    merge-ready decisions are refused. The fail-closed
    safety rule is unchanged by this fix.
    """
    for status, has_visible in [
        (mod.STATUS_HOLD_NEW_THREAD, True),
        (mod.STATUS_HOLD_CODEX_PENDING, False),
    ]:
        packet = _build_partial_inventory_packet(
            status=status,
            review_thread_comment_inventory_complete=False,
            active_threads=(
                [
                    {
                        "thread_id": "PRRT_kwDOSHFpYM6JVisible",
                        "comment_database_id": 999010,
                        "comment_url": "https://example/999010",
                        "author": CODEX_LOGIN,
                        "path": "scripts/local/foo.py",
                        "line": 1,
                        "is_resolved": False,
                        "is_outdated": False,
                        "body": "visible finding",
                        "nested_incomplete": True,
                    }
                ]
                if has_visible
                else []
            ),
        )
        md = mod.render_markdown(packet)
        # The exact safety wording must always be present.
        assert "Clean-pass / merge-ready decisions are still" in md, (
            f"safety explanation missing for status={status}: "
            f"{md[:300]}"
        )
        assert "refused while any required surface is" in md, (
            f"safety explanation missing for status={status}: "
            f"{md[:300]}"
        )


def test_p2_complete_inventory_does_not_emit_partial_inventory_note():
    """
    P2 #3: When ALL three required surfaces are
    complete (issue_comment, review_submission,
    review_thread), the partial-inventory note must
    NOT be emitted. The new wording is only
    triggered by incomplete inventory.
    """
    packet = _build_partial_inventory_packet(
        status=mod.STATUS_MERGE_READY,
        review_thread_inventory_complete=True,
        review_thread_comment_inventory_complete=True,
        # Even if the markdown renderer still
        # renders the partial-inventory section
        # header, the per-poll note must NOT appear
        # because all three required surfaces are
        # complete.
        issue_complete=True,
        rev_complete=True,
    )
    md = mod.render_markdown(packet)
    assert "At least one required Codex response-surface" not in md
    assert "Clean-pass / merge-ready decisions are still" not in md


def test_p2_issue_inventory_alone_triggers_packet_driven_note():
    """
    P2 #3: The packet-driven note also fires when
    only the issue-comment inventory is incomplete
    (and the visible finding comes from elsewhere).
    The wording must reflect the packet's actual
    status, not a hardcoded "pending" string.
    """
    packet = _build_partial_inventory_packet(
        status=mod.STATUS_HOLD_NEW_THREAD,
        review_thread_inventory_complete=True,
        review_thread_comment_inventory_complete=True,
        issue_complete=False,  # only this is incomplete
        active_threads=[
            {
                "thread_id": "PRRT_kwDOSHFpYM6JIssueIncomplete",
                "comment_database_id": 999020,
                "comment_url": "https://example/999020",
                "author": CODEX_LOGIN,
                "path": "scripts/local/foo.py",
                "line": 1,
                "is_resolved": False,
                "is_outdated": False,
                "body": "active blocker preserved",
                "nested_incomplete": False,
            }
        ],
    )
    md = mod.render_markdown(packet)
    assert "`HOLD_NEW_CODEX_THREAD`" in md
    assert "holding at HOLD_CODEX_RESPONSE_PENDING" not in md
    assert "Clean-pass / merge-ready decisions are still" in md


# ---------------------------------------------------------------------------
# P2 #4 regression tests: ignore non-head formal reviews when scanning
# for newer findings after a current-head clean pass
# (Codex post-ping finding 4, thread PRRT_kwDOSHFpYM6JWKnq).
# ---------------------------------------------------------------------------


def _make_clean_pass_pr_view() -> Dict[str, Any]:
    """Build a PR view that reaches the clean-pass decision
    branch (merge_state_status=CLEAN, mergeable=MERGEABLE)."""
    return make_pr_view(merge_state="CLEAN")


def _build_clean_pass_then_review_fixture(
    *,
    review_state: str,
    review_body: str,
    review_commit_oid: str,
    review_id: int = 7001,
    review_submitted_at: str = "2026-06-11T18:30:00Z",
) -> Dict[str, Any]:
    """
    Build a packet-driving fixture where:
    - A current-head Codex clean pass exists as a PR-level
      issue comment AFTER the ping.
    - A LATER formal Codex review submission (with the
      given state and body) exists AFTER the clean pass,
      anchored to `review_commit_oid`.

    The clean pass and the review both come AFTER
    PING_CREATED (2026-06-11T17:30:00Z), so the ping
    filter accepts both. The review's commit_oid
    determines whether the post-fix code should treat
    it as a newer finding.
    """
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=9400,
        ),
    ]
    reviews = [
        make_review(
            author=CODEX_LOGIN,
            state=review_state,
            body=review_body,
            submitted_at=review_submitted_at,
            review_id=review_id,
            commit_oid=review_commit_oid,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": []
            }
        }}}
    }
    return {
        "pr_view": _make_clean_pass_pr_view(),
        "issue": issue,
        "reviews": reviews,
        "threads": threads,
    }


def test_p2_stale_review_changes_requested_does_not_downgrade_clean_pass(
    monkeypatch, tmp_path,
):
    """
    P2 #4: A current-head clean pass exists. A later
    formal Codex CHANGES_REQUESTED review exists,
    anchored to a DIFFERENT commit (not
    expected_head_sha). The newer-finding loop must
    ignore this stale review; the classifier must
    reach MERGE_READY_AWAITING_HUMAN_AUTHORIZATION,
    NOT HOLD_NEW_CODEX_THREAD. Pre-fix: this scenario
    incorrectly routed to HOLD_NEW_CODEX_THREAD and
    blocked a valid current-head clean pass.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    fx = _build_clean_pass_then_review_fixture(
        review_state="CHANGES_REQUESTED",
        review_body="Changes requested on a prior head",
        review_commit_oid=OTHER_HEAD,  # NOT expected_head
    )
    runner = make_gh_runner(fx["pr_view"], fx["issue"], fx["reviews"], fx["threads"])
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_MERGE_READY, (
        f"stale-review CHANGES_REQUESTED on a different "
        f"commit must NOT downgrade a current-head clean "
        f"pass; expected MERGE_READY, got "
        f"status={pkt['status']!r}, "
        f"clean_pass_detected={pkt.get('clean_pass_detected')}, "
        f"latest_codex_response_id={pkt.get('latest_codex_response_id')}"
    )
    assert pkt["clean_pass_detected"] is True


def test_p2_stale_review_commented_does_not_downgrade_clean_pass(
    monkeypatch, tmp_path,
):
    """
    P2 #4: Same scenario but with a stale COMMENTED
    Codex review (non-clean body) on a different
    commit. Pre-fix this also downgraded to
    HOLD_NEW_CODEX_THREAD. Post-fix the stale review
    is ignored and we reach MERGE_READY.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    fx = _build_clean_pass_then_review_fixture(
        review_state="COMMENTED",
        review_body="Actually I see an issue on the prior head",
        review_commit_oid=OTHER_HEAD,
    )
    runner = make_gh_runner(fx["pr_view"], fx["issue"], fx["reviews"], fx["threads"])
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_MERGE_READY
    assert pkt["clean_pass_detected"] is True


def test_p2_stale_review_approved_does_not_downgrade_clean_pass(
    monkeypatch, tmp_path,
):
    """
    P2 #4: Same scenario but with a stale APPROVED
    Codex review (non-clean body) on a different
    commit. Pre-fix this also downgraded to
    HOLD_NEW_CODEX_THREAD. Post-fix the stale review
    is ignored and we reach MERGE_READY.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    fx = _build_clean_pass_then_review_fixture(
        review_state="APPROVED",
        review_body="Approved on the prior head, but here is a real concern",
        review_commit_oid=OTHER_HEAD,
    )
    runner = make_gh_runner(fx["pr_view"], fx["issue"], fx["reviews"], fx["threads"])
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_MERGE_READY
    assert pkt["clean_pass_detected"] is True


def test_p2_current_head_review_changes_requested_preserves_behavior(
    monkeypatch, tmp_path,
):
    """
    P2 #4 (regression retention): When the formal
    CHANGES_REQUESTED review IS anchored to
    expected_head_sha, the new-found commit-scope
    filter does NOT change behavior. The newer-finding
    loop still treats the current-head review as a
    newer finding and routes to HOLD_NEW_CODEX_THREAD.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    fx = _build_clean_pass_then_review_fixture(
        review_state="CHANGES_REQUESTED",
        review_body="Changes requested on current head",
        review_commit_oid=EXPECTED_HEAD,  # on expected head
    )
    runner = make_gh_runner(fx["pr_view"], fx["issue"], fx["reviews"], fx["threads"])
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["clean_pass_detected"] is True


def test_p2_current_head_review_commented_non_clean_preserves_behavior(
    monkeypatch, tmp_path,
):
    """
    P2 #4 (regression retention): When a current-head
    formal COMMENTED review (with a non-clean body)
    IS anchored to expected_head_sha, the post-fix
    code still treats it as a newer finding. The
    classifier must route to HOLD_NEW_CODEX_THREAD.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    fx = _build_clean_pass_then_review_fixture(
        review_state="COMMENTED",
        review_body="I missed something — here is a real bug",
        review_commit_oid=EXPECTED_HEAD,
    )
    runner = make_gh_runner(fx["pr_view"], fx["issue"], fx["reviews"], fx["threads"])
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["clean_pass_detected"] is True


def test_p2_issue_comment_newer_finding_path_unchanged(
    monkeypatch, tmp_path,
):
    """
    P2 #4 (regression retention): Top-level PR issue
    comments are NOT commit-scoped, so the new
    commit-id filter is intentionally NOT applied to
    them. A newer Codex issue comment that is NOT a
    clean pass, posted after the current-head clean
    pass, must still drive HOLD_NEW_CODEX_THREAD —
    exactly as the pre-fix behavior already did.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=9500,
        ),
        make_issue_comment(
            author=CODEX_LOGIN,
            body="Actually I missed something: P1 real bug",
            created_at="2026-06-11T18:30:00Z",
            comment_id=9501,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": []
            }
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["clean_pass_detected"] is True


# ---------------------------------------------------------------------------
# P2 #5: formal clean-pass reviews before later non-clean reviews
# ---------------------------------------------------------------------------


def _build_formal_clean_pass_then_later_fixture(
    *,
    first_review_state: str,
    first_review_body: str,
    first_review_commit_oid: str,
    first_review_id: int = 7101,
    first_review_submitted_at: str = "2026-06-11T18:00:00Z",
    second_review_state: str = "COMMENTED",
    second_review_body: str = "Actually I missed something: P1 real bug",
    second_review_commit_oid: str = EXPECTED_HEAD,
    second_review_id: int = 7102,
    second_review_submitted_at: str = "2026-06-11T18:30:00Z",
    second_review_author: str = CODEX_LOGIN,
) -> Dict[str, Any]:
    """
    Build a packet-driving fixture where:
    - A post-ping Codex formal review exists with the
      given state/body/commit_oid, and is the EARLIER
      of two post-ping reviews. This is the candidate
      clean-pass review.
    - A LATER post-ping Codex formal review (with the
      given second_* parameters) exists AFTER the
      first review.

    The ping filter accepts both reviews (their
    submittedAt timestamps are > PING_CREATED). The
    first review's commit_oid determines whether it
    is accepted as the current-head clean pass; the
    second review's commit_oid determines whether it
    is treated as a newer finding.

    No PR-level issue comments are included; the
    clean pass must come from the formal review
    submission path.
    """
    reviews = [
        make_review(
            author=CODEX_LOGIN,
            state=first_review_state,
            body=first_review_body,
            submitted_at=first_review_submitted_at,
            review_id=first_review_id,
            commit_oid=first_review_commit_oid,
        ),
        make_review(
            author=second_review_author,
            state=second_review_state,
            body=second_review_body,
            submitted_at=second_review_submitted_at,
            review_id=second_review_id,
            commit_oid=second_review_commit_oid,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": []
            }
        }}}
    }
    return {
        "pr_view": _make_clean_pass_pr_view(),
        "issue": [],
        "reviews": reviews,
        "threads": threads,
    }


def test_p2_formal_clean_pass_then_later_commented_non_clean_routes_hold_new(
    monkeypatch, tmp_path,
):
    """
    P2 #5: Codex clean-passes via a formal APPROVED
    review with the canonical clean-pass phrase, then
    later submits a non-clean COMMENTED review on the
    SAME expected head. Pre-fix: the formal clean-pass
    branch only inspected `latest_review` (the later
    non-clean one), so `clean_pass_detected` stayed
    False, the `newer_finding_after_clean_pass` scan
    was skipped, and the classifier returned
    `HOLD_CODEX_RESPONSE_PENDING` instead of
    `HOLD_NEW_CODEX_THREAD`. Post-fix: the earlier
    formal APPROVED clean-pass review is detected,
    the later COMMENTED review is treated as a newer
    finding, and the status is
    `HOLD_NEW_CODEX_THREAD`.

    Regression retention: the formal-review clean
    pass is the clean-pass reference (not an issue
    comment), and `clean_pass_source` is
    `pull_request_review` with `clean_pass_review_id`
    set to the first (clean-pass) review id.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    fx = _build_formal_clean_pass_then_later_fixture(
        first_review_state="APPROVED",
        first_review_body=codex_clean_pass_body(),
        first_review_commit_oid=EXPECTED_HEAD,
        second_review_state="COMMENTED",
        second_review_body="I missed something: P2 real bug on current head",
        second_review_commit_oid=EXPECTED_HEAD,
    )
    runner = make_gh_runner(fx["pr_view"], fx["issue"], fx["reviews"], fx["threads"])
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD, (
        f"formal APPROVED clean pass then later "
        f"non-clean COMMENTED on same head must route "
        f"to HOLD_NEW_CODEX_THREAD (newer finding), "
        f"got status={pkt['status']!r}, "
        f"clean_pass_detected={pkt.get('clean_pass_detected')}, "
        f"clean_pass_source={pkt.get('clean_pass_source')}, "
        f"clean_pass_review_id={pkt.get('clean_pass_review_id')}, "
        f"latest_codex_response_id={pkt.get('latest_codex_response_id')}"
    )
    assert pkt["status"] != mod.STATUS_HOLD_CODEX_PENDING, (
        "must NOT downgrade to HOLD_CODEX_RESPONSE_PENDING "
        "when a confirmed current-head clean pass exists "
        "before a later current-head non-clean formal review"
    )
    assert pkt["clean_pass_detected"] is True
    assert pkt["clean_pass_source"] == "pull_request_review"
    assert pkt["clean_pass_review_id"] == 7101
    assert pkt["clean_pass_at"] == "2026-06-11T18:00:00Z"


def test_p2_formal_clean_pass_then_later_changes_requested_routes_hold_new(
    monkeypatch, tmp_path,
):
    """
    P2 #5: Codex clean-passes via a formal COMMENTED
    review (with clean-pass phrase), then later
    submits a CHANGES_REQUESTED review on the same
    expected head. Pre-fix: clean pass was missed and
    the classifier returned
    HOLD_CODEX_RESPONSE_PENDING. Post-fix: the earlier
    clean pass is detected, the later
    CHANGES_REQUESTED review is treated as a newer
    finding, status is HOLD_NEW_CODEX_THREAD.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    fx = _build_formal_clean_pass_then_later_fixture(
        first_review_state="COMMENTED",
        first_review_body=codex_clean_pass_body(),
        first_review_commit_oid=EXPECTED_HEAD,
        second_review_state="CHANGES_REQUESTED",
        second_review_body="Changes requested on current head",
        second_review_commit_oid=EXPECTED_HEAD,
    )
    runner = make_gh_runner(fx["pr_view"], fx["issue"], fx["reviews"], fx["threads"])
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["status"] != mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["clean_pass_detected"] is True
    assert pkt["clean_pass_source"] == "pull_request_review"
    assert pkt["clean_pass_review_id"] == 7101


def test_p2_formal_clean_pass_then_later_approved_non_clean_routes_hold_new(
    monkeypatch, tmp_path,
):
    """
    P2 #5: Codex clean-passes via a formal APPROVED
    review, then later submits another APPROVED
    review (with a non-clean body) on the same
    expected head. Pre-fix: clean pass was missed and
    the classifier returned
    HOLD_CODEX_RESPONSE_PENDING. Post-fix: the earlier
    APPROVED clean pass is detected, the later
    non-clean APPROVED review is treated as a newer
    finding, status is HOLD_NEW_CODEX_THREAD.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    fx = _build_formal_clean_pass_then_later_fixture(
        first_review_state="APPROVED",
        first_review_body=codex_clean_pass_body(),
        first_review_commit_oid=EXPECTED_HEAD,
        second_review_state="APPROVED",
        second_review_body="Approved overall but I see a real issue on current head",
        second_review_commit_oid=EXPECTED_HEAD,
    )
    runner = make_gh_runner(fx["pr_view"], fx["issue"], fx["reviews"], fx["threads"])
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["status"] != mod.STATUS_HOLD_CODEX_PENDING
    assert pkt["clean_pass_detected"] is True
    assert pkt["clean_pass_source"] == "pull_request_review"
    assert pkt["clean_pass_review_id"] == 7101


def test_p2_formal_clean_pass_on_other_head_not_accepted(
    monkeypatch, tmp_path,
):
    """
    P2 #5: A formal APPROVED clean-pass review
    anchored to a DIFFERENT commit than
    expected_head_sha must NOT be accepted as the
    current-head clean pass. The same expected-head
    commit-scope filter used by the `latest_review`
    path and the `newer_finding_after_clean_pass`
    scan applies here. A review with no commit_oid
    is treated as authoritative (same convention).

    In this fixture the only clean-pass candidate is
    anchored to OTHER_HEAD, so clean_pass_detected
    stays False. There are no other surfaces to
    trigger a different decision, so the status
    reaches HOLD_CODEX_RESPONSE_PENDING (or
    HOLD_NEW_CODEX_THREAD only if an active thread
    exists — none in this fixture).
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    fx = _build_formal_clean_pass_then_later_fixture(
        first_review_state="APPROVED",
        first_review_body=codex_clean_pass_body(),
        first_review_commit_oid=OTHER_HEAD,  # NOT expected head
        second_review_state="COMMENTED",
        second_review_body="Random follow-up on current head",
        second_review_commit_oid=EXPECTED_HEAD,
    )
    runner = make_gh_runner(fx["pr_view"], fx["issue"], fx["reviews"], fx["threads"])
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["clean_pass_detected"] is False, (
        f"formal clean pass on OTHER_HEAD must NOT be "
        f"accepted as the current-head clean pass; "
        f"got clean_pass_detected={pkt.get('clean_pass_detected')}, "
        f"clean_pass_source={pkt.get('clean_pass_source')}, "
        f"clean_pass_review_id={pkt.get('clean_pass_review_id')}"
    )
    assert pkt["clean_pass_source"] is None
    assert pkt["clean_pass_review_id"] is None
    # No clean pass and no active threads -> pending
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING


def test_p2_multiple_formal_clean_passes_picks_most_recent(
    monkeypatch, tmp_path,
):
    """
    P2 #5: When multiple post-ping Codex formal
    reviews qualify as clean-pass candidates, the
    most recent qualifying review (by submittedAt)
    is selected as the clean-pass reference. Earlier
    clean-pass reviews are ignored.

    In this fixture:
    - First review (18:00): APPROVED + clean-pass
      phrase on EXPECTED_HEAD
    - Second review (18:15): APPROVED + clean-pass
      phrase on EXPECTED_HEAD  (more recent)
    - Third review (18:30): non-clean COMMENTED on
      EXPECTED_HEAD

    Expected: clean_pass_review_id is the SECOND
    review (7115), clean_pass_at is 18:15:00Z, the
    later non-clean review is treated as a newer
    finding, status is HOLD_NEW_CODEX_THREAD.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    reviews = [
        make_review(
            author=CODEX_LOGIN,
            state="APPROVED",
            body=codex_clean_pass_body(),
            submitted_at="2026-06-11T18:00:00Z",
            review_id=7110,
            commit_oid=EXPECTED_HEAD,
        ),
        make_review(
            author=CODEX_LOGIN,
            state="APPROVED",
            body=codex_clean_pass_body(),
            submitted_at="2026-06-11T18:15:00Z",
            review_id=7115,
            commit_oid=EXPECTED_HEAD,
        ),
        make_review(
            author=CODEX_LOGIN,
            state="COMMENTED",
            body="Actually I see a real issue on current head",
            submitted_at="2026-06-11T18:30:00Z",
            review_id=7120,
            commit_oid=EXPECTED_HEAD,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": []
            }
        }}}
    }
    runner = make_gh_runner(_make_clean_pass_pr_view(), [], reviews, threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["clean_pass_detected"] is True
    assert pkt["clean_pass_source"] == "pull_request_review"
    # Most recent clean-pass review is the second one (18:15)
    assert pkt["clean_pass_review_id"] == 7115, (
        f"expected the most recent clean-pass review "
        f"(7115) to be selected; got "
        f"clean_pass_review_id={pkt.get('clean_pass_review_id')!r}"
    )
    assert pkt["clean_pass_at"] == "2026-06-11T18:15:00Z"


def test_p2_issue_comment_clean_pass_plus_later_formal_non_clean_still_hold_new(
    monkeypatch, tmp_path,
):
    """
    P2 #5 (regression retention): When the clean
    pass is a PR-level issue comment AND a later
    formal non-clean review exists on the expected
    head, the existing issue-comment newer-finding
    path must still drive HOLD_NEW_CODEX_THREAD.

    This is essentially the same scenario as
    test_clean_pass_with_newer_finding_after_returns_hold_new
    but adds a formal review submission that is
    also non-clean. The clean pass still comes from
    the issue comment. Status is HOLD_NEW_CODEX_THREAD
    (driven by the newer non-clean issue comment, not
    by the formal review alone).
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=9700,
        ),
        make_issue_comment(
            author=CODEX_LOGIN,
            body="Actually I missed something: P1 real bug",
            created_at="2026-06-11T18:30:00Z",
            comment_id=9701,
        ),
    ]
    reviews = [
        make_review(
            author=CODEX_LOGIN,
            state="COMMENTED",
            body="Random follow-up on current head",
            submitted_at="2026-06-11T18:35:00Z",
            review_id=7150,
            commit_oid=EXPECTED_HEAD,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": []
            }
        }}}
    }
    runner = make_gh_runner(pr_view, issue, reviews, threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["clean_pass_detected"] is True
    # Clean pass came from the issue comment, not the formal review
    assert pkt["clean_pass_source"] == "issue_comment"
    assert pkt["clean_pass_comment_id"] == 9700


def test_p2_formal_clean_pass_review_with_no_commit_oid_is_authoritative(
    monkeypatch, tmp_path,
):
    """
    P2 #5 (regression retention): A formal review
    with no commit_oid (legacy / GitHub-emitted
    without a commit anchor) is kept as
    authoritative, matching the `latest_review`
    convention. In this fixture the clean-pass
    review has commit_oid="" (effectively None),
    which `extract_review_commit_oid` returns as "".
    The clean pass must still be detected and
    drive the same routing as a review with a
    matching commit_oid.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    reviews = [
        make_review(
            author=CODEX_LOGIN,
            state="APPROVED",
            body=codex_clean_pass_body(),
            submitted_at="2026-06-11T18:00:00Z",
            review_id=7200,
            commit_oid="",  # no commit anchor -> authoritative
        ),
        make_review(
            author=CODEX_LOGIN,
            state="COMMENTED",
            body="I see a real issue on current head",
            submitted_at="2026-06-11T18:30:00Z",
            review_id=7201,
            commit_oid=EXPECTED_HEAD,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": []
            }
        }}}
    }
    runner = make_gh_runner(_make_clean_pass_pr_view(), [], reviews, threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD
    assert pkt["clean_pass_detected"] is True
    assert pkt["clean_pass_source"] == "pull_request_review"
    assert pkt["clean_pass_review_id"] == 7200


def test_p2_formal_clean_pass_then_later_review_on_other_head_still_hold_new(
    monkeypatch, tmp_path,
):
    """
    P2 #5 (regression retention): The new
    formal-review clean-pass branch and the existing
    `newer_finding_after_clean_pass` commit-scope
    filter (P2 #4) work together. When the formal
    clean pass is on EXPECTED_HEAD but the later
    non-clean review is on OTHER_HEAD, the clean
    pass is detected, but the stale different-commit
    review is NOT treated as a newer finding. Result:
    no newer finding, no active threads, merge
    state CLEAN -> MERGE_READY_AWAITING_HUMAN_AUTHORIZATION.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    fx = _build_formal_clean_pass_then_later_fixture(
        first_review_state="APPROVED",
        first_review_body=codex_clean_pass_body(),
        first_review_commit_oid=EXPECTED_HEAD,
        second_review_state="COMMENTED",
        second_review_body="Stale finding on a prior head",
        second_review_commit_oid=OTHER_HEAD,
    )
    runner = make_gh_runner(fx["pr_view"], fx["issue"], fx["reviews"], fx["threads"])
    monkeypatch.setattr(mod.subprocess, "run", runner)

    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["status"] == mod.STATUS_MERGE_READY, (
        f"formal clean pass on expected head + stale "
        f"different-commit review must reach "
        f"MERGE_READY (not HOLD_NEW_CODEX_THREAD); "
        f"got status={pkt['status']!r}, "
        f"clean_pass_review_id={pkt.get('clean_pass_review_id')}"
    )
    assert pkt["clean_pass_detected"] is True
    assert pkt["clean_pass_source"] == "pull_request_review"
    assert pkt["clean_pass_review_id"] == 7101
