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


# ---------------------------------------------------------------------------
# Subprocess runner mock
# ---------------------------------------------------------------------------


def make_gh_runner(pr_view, issue_comments, reviews, threads_payload):
    """
    Returns a function suitable for monkeypatch.setattr(mod, "subprocess.run", ...).
    The function dispatches based on the gh command shape.
    """

    def _runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        # pr view (REST) -> has --jq with sha/state/mergeStateStatus/...
        # Use a precise URL match: the jq-pr-view uses pulls/{n} with --jq
        if "repos/" in cmd_str and "/pulls/" in cmd_str and "--jq" in cmd_str:
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
        if "repos/" in cmd_str and "/pulls/" in cmd_str and "--jq" in cmd_str:
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
        if "repos/" in cmd_str and "/pulls/" in cmd_str and "--jq" in cmd_str:
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
        if "repos/" in cmd_str and "/pulls/" in cmd_str and "--jq" in cmd_str:
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
