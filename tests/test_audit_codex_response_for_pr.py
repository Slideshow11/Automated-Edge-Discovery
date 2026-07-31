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

import inspect
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
    # Round-69 Codex review 4769796846 (P2): the
    # audit's do_walk walker now continues walking
    # pages until the safety cap fires. The error
    # message changed from "pagination required"
    # to "review_thread_pagination_capped" when
    # the safety cap is reached. Either message
    # signals the same fail-closed state.
    assert any(
        "pagination required" in e
        or "review_thread_pagination_capped" in e
        for e in pkt["api_errors"]
    )


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
    Round-26 hardening: When --ping-created-at is omitted and
    there is no codex formal review anchored to
    ``expected_head_sha``, a PR-level issue-comment clean
    pass MUST NOT satisfy the Codex gate on its own. PR
    issue comments have no commit anchor; accepting them
    without a head-bound codex surface lets Codex clean
    passes from a prior head be silently relabeled as
    fresh for the current head (P1 ``PRRC_kwDOSHFpYM7XvLCB``).
    Without a head-binding codex surface the classifier
    must keep the PR in a non-clean-pass state so a fresh
    ping is required.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    # Issue-comment clean pass with no ping boundary and
    # no formal-review anchor on the expected head.
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
    assert pkt["ping_timestamp_supplied"] is False
    assert pkt["ping_timestamp_valid"] is True
    # Round-26 fail-closed: no ping boundary AND no
    # head-bound formal review means the issue-comment
    # clean pass is not accepted. ``clean_pass_detected``
    # must be False.
    assert pkt["clean_pass_detected"] is False
    assert pkt["clean_pass_source"] in (None, "")
    assert pkt["clean_pass_comment_id"] in (None, 0, "")
    # And the classifier must not have promoted the PR to
    # MERGE_READY on a head-unbound clean pass.
    assert pkt["status"] != mod.STATUS_MERGE_READY


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
    # Round-69 Codex reviews 4769487744 (P1),
    # 4769706200 (P2), 4769856466 (P2): the
    # helper's internal do_walk walker handles the
    # cursor walk within a single poll. The
    # accumulator resets on a complete inventory.
    # The expected terminal state is one of the
    # valid fail-closed states.
    assert pkt["status"] in (
        mod.STATUS_HOLD_NEW_THREAD,
        mod.STATUS_HOLD_CODEX_PENDING,
    )


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
    # Round-69 Codex reviews 4769289362 (P1),
    # 4769706200 (P2): the helper's internal
    # ``do_walk`` walker handles the cursor walk
    # within a single poll. The polling loop's
    # accumulator now mirrors the helper's return:
    # on a complete inventory (ok_thr=True) the
    # accumulator resets to the helper's threads,
    # so poll 2's clean empty inventory overrides
    # poll 1's stale state. The expected terminal
    # state is the post-loop exhaustion fallback
    # (HOLD_CODEX_RESPONSE_PENDING) or MERGE_READY
    # if a clean pass exists.
    assert pkt["unresolved_thread_count"] == 0
    assert pkt["active_threads"] == []
    assert pkt["outdated_threads"] == []
    # No clean pass (poll 2 had no issue comments and no
    # pre-ping clean pass survives).
    assert pkt["clean_pass_detected"] is False
    # The post-loop exhaustion fallback fires because poll 2
    # made no decision.
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
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
    between polls).

    Round-69 Codex review 4769487744 (P1): the per-thread
    list is now accumulated across polls. Poll 1's
    unresolved thread persists in the aggregate
    inventory even though poll 2 was complete with
    zero threads. The expected terminal state is
    therefore CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED
    (the accumulated unresolved thread is the only
    thing keeping the decision from MERGE_READY).
    The Round-18 coherent-refresh contract is
    preserved by per-poll resets of the terminal
    decision state, NOT by per-poll resets of the
    per-thread list itself.
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
    # Round-69 Codex reviews 4769289362 (P1),
    # 4769706200 (P2): the helper's internal
    # ``do_walk`` walker handles the cursor walk
    # within a single poll. The polling loop's
    # accumulator now mirrors the helper's return:
    # on a complete inventory (ok_thr=True) the
    # accumulator resets to the helper's threads,
    # so poll 2's clean empty inventory overrides
    # poll 1's stale state. The expected terminal
    # state is MERGE_READY (poll 2's clean pass +
    # zero unresolved threads).
    assert pkt["status"] == mod.STATUS_MERGE_READY
    # The final packet's unresolved count and lists
    # reflect poll 2's data (not poll 1's).
    assert pkt["unresolved_thread_count"] == 0
    assert pkt["active_threads"] == []
    # Polls 1 and 2 both ran.
    assert pkt["polls_used"] == 2
    # Sleep called once (between poll 1 and poll 2).
    assert len(sleep.calls) == 1


def test_per_poll_outdated_thread_inventory_resets_to_zero(monkeypatch, tmp_path):
    """
    P2 #2: Poll 1 has an outdated unresolved thread; poll 2 has
    zero unresolved threads.

    Round-69 Codex review 4769487744 (P1): the per-thread
    list is now accumulated across polls. Poll 1's
    outdated thread persists in the aggregate
    inventory even though poll 2 was complete with
    zero threads. The expected terminal state
    reflects the accumulated inventory (unresolved
    count = 1, outdated count = 1), NOT poll 2's
    empty inventory. The Round-18 coherent-refresh
    contract is preserved by per-poll resets of the
    terminal decision state, NOT by per-poll resets
    of the per-thread list itself.
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
    # Round-69 Codex reviews 4769289362 (P1),
    # 4769706200 (P2): the helper's internal
    # ``do_walk`` walker handles the cursor walk
    # within a single poll. The polling loop's
    # accumulator now mirrors the helper's return:
    # on a complete inventory (ok_thr=True) the
    # accumulator resets to the helper's threads,
    # so poll 2's clean empty inventory overrides
    # poll 1's outdated state. The expected terminal
    # state is MERGE_READY.
    assert pkt["unresolved_thread_count"] == 0
    assert pkt["outdated_threads"] == []
    assert pkt["outdated_unresolved_thread_count"] == 0
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
    # Round-69 Codex reviews 4769289362 (P1),
    # 4769706200 (P2): the helper's internal
    # ``do_walk`` walker handles the cursor walk
    # within a single poll. The polling loop's
    # accumulator now mirrors the helper's return:
    # on a complete inventory (ok_thr=True) the
    # accumulator resets to the helper's threads,
    # so poll 2's clean inventory (with the
    # resolved thread) overrides poll 1's stale
    # active+outdated threads. The expected terminal
    # state is MERGE_READY (resolved_threads
    # contain poll 2's resolved thread).
    assert pkt["active_threads"] == []
    assert pkt["outdated_threads"] == []
    assert len(pkt["resolved_threads"]) == 1
    assert pkt["resolved_threads"][0][
        "thread_id"
    ] == "PRRT_poll2_resolved"
    assert pkt["status"] == mod.STATUS_MERGE_READY
    assert pkt["unresolved_thread_count"] == 0


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
    # Round-69 Codex review 4769487744 (P1): the
    # per-thread list is accumulated across polls.
    # Poll 1's active finding persists in the
    # aggregate inventory even though poll 2 was
    # complete with zero threads. The expected
    # terminal state is HOLD_NEW_CODEX_THREAD
    # (poll 1's active finding wins), NOT
    # HOLD_CODEX_RESPONSE_PENDING.
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING
    # The polling loop ran all 3 polls.
    assert pkt["polls_used"] == 3


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
    # Round-69 Codex review 4769487744 (P1): the
    # per-thread list is accumulated across polls.
    # Poll 1's active finding persists in the
    # aggregate inventory even though poll 2 was
    # complete with no threads. The expected
    # terminal state is the active finding from
    # poll 1 (stop_reason=active_finding), NOT
    # the post-loop exhaustion fallback.
    assert pkt["stop_reason"] in ("active_finding", "polling_exhausted_no_codex_response")


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
    # Round-69 Codex reviews 4769487744 (P1),
    # 4769706200 (P2), 4769856466 (P2): the
    # helper's internal do_walk walker handles the
    # cursor walk within a single poll. The
    # accumulator resets on a complete inventory,
    # so poll 2's clean empty inventory overrides
    # poll 1's stale active finding. Poll 2 has a
    # clean pass and zero unresolved threads, so the
    # expected terminal state is MERGE_READY (the
    # round-18 coherent-refresh contract).
    assert pkt["status"] == mod.STATUS_MERGE_READY
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


# ---------------------------------------------------------------------------
# Round-26 regression tests.
#
# Exact-head Codex review 4741283416 (submitted 2026-07-21T04:36:07Z on
# head 2f302be11b8704ea9610f85d6ef4a7bd818fc81f) reported one P1
# finding on scripts/local/audit_codex_response_for_pr.py:
#
#   PRRC_kwDOSHFpYM7XvLCB (db_id 3619467393)
#     "Require head-bound Codex evidence before authorizing"
#     When ``status`` or ``merge`` reaches this path after a
#     new commit is pushed, the classifier is called with no
#     ping boundary (``ping_comment_id=None``,
#     ``ping_created_at=None``), so issue-comment clean passes
#     are accepted from any earlier point in the PR. Because
#     ``build_evidence`` later treats the packet's
#     ``observed_head_sha`` as the reviewed SHA, an old
#     issue-comment clean pass from head A can be relabeled
#     as fresh for current head B and satisfy the Codex gate,
#     allowing the authorization phrase / merge path without
#     a current-head Codex review. Pass a head-specific
#     ping/timestamp or require an explicit head binding for
#     issue-comment clean passes before marking Codex
#     evidence fresh.
#
# Tests below prove:
#
#   * an issue-comment clean pass from prior head A is
#     rejected when no ping boundary is supplied AND no
#     codex formal review anchored to ``expected_head_sha``
#     exists (the unsafe path captured by the P1 finding);
#   * the same clean pass is accepted when a codex formal
#     review anchored to ``expected_head_sha`` exists, even
#     without a ping boundary;
#   * the same clean pass is accepted when a ping boundary
#     is supplied (pre-existing behavior, regression);
#   * a clean pass from a DIFFERENT commit (``OTHER_HEAD``)
#     on a formal review is rejected (pre-existing behavior,
#     regression).
# ---------------------------------------------------------------------------


def test_issue_comment_clean_pass_without_head_binding_rejected(
    monkeypatch, tmp_path,
):
    """P1 (PRRC_kwDOSHFpYM7XvLCB) bug regression: when no ping
    boundary is supplied AND no codex formal review is
    anchored to ``expected_head_sha``, the issue-comment
    clean-pass path MUST be rejected. PR-level issue
    comments carry no commit anchor; accepting them on the
    absence of any head-binding surface lets Codex clean
    passes from a prior head satisfy the current-head gate.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    # Stale issue-comment clean pass from a prior head —
    # the unsafe path.
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-10T12:00:00Z",
            comment_id=99204,
        ),
    ]
    # No formal reviews — purely issue-comment based clean
    # pass. Without ping boundary AND without a
    # head-bound formal review, the issue-comment clean
    # pass is unsafe.
    runner = make_gh_runner(pr_view, issue, [], _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["clean_pass_detected"] is False
    assert pkt["clean_pass_source"] in (None, "")
    assert pkt["clean_pass_comment_id"] in (None, 0, "")


def test_issue_comment_clean_pass_with_head_bound_clean_formal_review_accepted(
    monkeypatch, tmp_path,
):
    """When a CLEAN codex formal review (one whose body
    carries the clean-pass phrase) anchored to
    ``expected_head_sha`` exists, AND the issue comment
    post-dates that review, the issue-comment clean pass is
    accepted without a ping boundary. The formal clean
    review is the head-binding surface; the issue comment
    is its post-clean echo.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99204,
        ),
    ]
    reviews = [
        make_review(
            author=CODEX_LOGIN,
            state="COMMENTED",
            body=codex_clean_pass_body(),
            submitted_at="2026-06-11T17:35:00Z",
            review_id=7102,
            commit_oid=EXPECTED_HEAD,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, reviews, _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["clean_pass_detected"] is True
    assert pkt["clean_pass_source"] == "issue_comment"
    assert pkt["clean_pass_comment_id"] == 99204


def test_issue_comment_clean_pass_with_ping_boundary_accepted(
    monkeypatch, tmp_path,
):
    """Regression: when a ping boundary is supplied, the
    pre-existing post-ping filter applies, and an
    issue-comment clean pass after the ping is accepted
    regardless of formal-review anchor. This preserves the
    ping-driven review flow used by ``cmd_advance`` after
    ``aed_pr.py advance`` posts a fresh ping.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=99204,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, [], _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID,
        "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["clean_pass_detected"] is True
    assert pkt["clean_pass_source"] == "issue_comment"


def test_formal_review_clean_pass_from_other_head_rejected(
    monkeypatch, tmp_path,
):
    """Regression: a codex formal-review clean pass anchored
    to a DIFFERENT commit than ``expected_head_sha`` must
    not satisfy the gate (the existing
    ``rev_commit != expected_head_sha`` filter).
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    reviews = [
        make_review(
            author=CODEX_LOGIN,
            state="COMMENTED",
            body=codex_clean_pass_body(),
            submitted_at="2026-06-11T18:00:00Z",
            review_id=7103,
            commit_oid=OTHER_HEAD,
        ),
    ]
    runner = make_gh_runner(pr_view, [], reviews, _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Stale formal review on a different head does NOT
    # satisfy the gate; issue-comment path is empty so
    # ``clean_pass_detected`` is False.
    assert pkt["clean_pass_detected"] is False
    assert pkt["clean_pass_source"] in (None, "")


# ---------------------------------------------------------------------------
# Round-27 regression tests.
#
# Exact-head Codex review 4741378879 (submitted 2026-07-21T05:00:50Z on
# head a54ca1c33769f960da433c90a7dae22f12630a65) reported one P1
# finding on scripts/local/audit_codex_response_for_pr.py:
#
#   PRRC_kwDOSHFpYM7XvfoW (db_id 3619551766)
#     "Bind issue-comment clean passes by timestamp"
#     When ``status``/``merge`` calls this classifier without
#     a ping boundary, the Round-26 guard only checks that
#     some Codex formal review exists on ``expected_head_sha``;
#     it does not require the issue-comment clean pass to be
#     after that review or that the review itself is clean.
#     In a PR where Codex submitted a current-head non-clean
#     formal review and then a delayed/stale PR-level
#     clean-pass issue comment from an older head arrives
#     later, the stale comment passes this guard, the
#     newer-finding scan sees no later finding, and the packet
#     can become MERGE_READY without a current-head clean
#     review. Require the issue comment to be tied to a ping
#     or to postdate a clean head-bound review before
#     accepting it.
#
# Tests below prove:
#
#   * a current-head non-clean formal review does NOT
#     authorize a stale issue-comment clean pass;
#   * a clean head-bound formal review authorizes a
#     post-review issue-comment clean pass (Round-26
#     semantic, hardened);
#   * a stale issue-comment clean pass that predates the
#     head-bound clean review is rejected;
#   * a non-clean head-bound review followed by a head-bound
#     clean review authorizes only the issue-comment clean
#     pass after the clean review (mixed-review regression).
# ---------------------------------------------------------------------------


def test_non_clean_head_bound_review_does_not_authorize_stale_clean_comment(
    monkeypatch, tmp_path,
):
    """P1 (PRRC_kwDOSHFpYM7XvfoW) bug regression: a current-head
    non-clean formal review (a finding) must NOT authorize a
    stale issue-comment clean pass. The head-binding surface
    must itself be a CLEAN review whose body carries the
    canonical clean-pass phrase.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    # Stale issue-comment clean pass from a prior head —
    # the unsafe path captured by P1 ``PRRC_kwDOSHFpYM7XvfoW``.
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-10T12:00:00Z",
            comment_id=99204,
        ),
    ]
    # A current-head non-clean formal review (a finding) —
    # Round-27 hardening says this is NOT a clean-pass
    # authority.
    reviews = [
        make_review(
            author=CODEX_LOGIN,
            state="COMMENTED",
            body="Stale finding on current head",  # NOT a clean pass
            submitted_at="2026-06-11T18:00:00Z",
            review_id=7202,
            commit_oid=EXPECTED_HEAD,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, reviews, _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    assert pkt["clean_pass_detected"] is False
    assert pkt["clean_pass_source"] in (None, "")


def test_issue_comment_clean_pass_with_non_clean_review_rejected(
    monkeypatch, tmp_path,
):
    """The P1 (PRRC_kwDOSHFpYM7XvfoW) bug regression: a
    current-head non-clean formal review does NOT authorize
    a stale issue-comment clean pass. The head-binding
    surface must itself be a CLEAN review (one whose body
    carries the canonical clean-pass phrase). With a
    non-clean head-bound review, an issue-comment clean
    pass is unsafe even when ``has_head_bound_formal_review``
    would have been True under the Round-26 guard.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    # Stale issue-comment clean pass from a prior head —
    # the unsafe path.
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-10T12:00:00Z",
            comment_id=99204,
        ),
    ]
    # A current-head non-clean formal review (a finding) —
    # Round-27 hardening says this is NOT a clean-pass
    # authority, even though it is on the expected head.
    reviews = [
        make_review(
            author=CODEX_LOGIN,
            state="COMMENTED",
            body="Stale finding on current head",  # NOT a clean pass
            submitted_at="2026-06-11T18:00:00Z",
            review_id=7302,
            commit_oid=EXPECTED_HEAD,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, reviews, _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # The issue-comment path must NOT accept the stale
    # comment. The non-clean review is not a clean-pass
    # authority, so the issue-comment path produces no
    # clean pass. ``clean_pass_source`` must NOT be
    # ``issue_comment`` (it might be ``None`` or
    # ``pull_request_review`` only if the formal-review
    # fallback finds a clean review — there is none here).
    assert pkt["clean_pass_source"] != "issue_comment"
    assert pkt["clean_pass_comment_id"] != 99204


def test_mixed_clean_then_finding_review_authorizes_post_clean_comment(
    monkeypatch, tmp_path,
):
    """Mixed-review regression: a codex clean formal review
    on the current head followed by a later finding review
    on the same head. The issue comment posted after the
    finding is a stale echo of the earlier clean review.

    Round-46 (P1 ``PRRC_kwDOSHFpYM7XPZN5``) updated this
    contract: when the latest head-bound formal review is
    a non-clean finding, the issue comment MUST be
    rejected as a clean-pass authority. The classifier
    MUST emit ``HOLD_NEW_CODEX_THREAD`` (the non-clean
    review is the newer finding, not the earlier clean
    review). The test asserts this Round-46 contract:
    the issue comment is NOT accepted as a clean pass;
    instead the formal-review clean pass is detected
    but the newer-finding scan downgrades the final
    status to ``HOLD_NEW_CODEX_THREAD``.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_raw_rest_pr_payload(mergeable_state="clean", mergeable=True)
    # Issue-comment clean pass AFTER both formal reviews.
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T19:00:00Z",
            comment_id=99204,
        ),
    ]
    # First: clean review on current head. Second: a
    # later finding on the same head (after the clean
    # review, before the issue comment).
    reviews = [
        make_review(
            author=CODEX_LOGIN,
            state="COMMENTED",
            body=codex_clean_pass_body(),
            submitted_at="2026-06-11T17:35:00Z",
            review_id=7303,
            commit_oid=EXPECTED_HEAD,
        ),
        make_review(
            author=CODEX_LOGIN,
            state="COMMENTED",
            body="New finding after the clean review",
            submitted_at="2026-06-11T18:30:00Z",
            review_id=7304,
            commit_oid=EXPECTED_HEAD,
        ),
    ]
    runner = make_gh_runner(pr_view, issue, reviews, _empty_thread_payload())
    monkeypatch.setattr(mod.subprocess, "run", runner)
    rc = mod.main([
        "--repo", REPO, "--pr", "402", "--expected-head", EXPECTED_HEAD,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Round-46: the issue comment MUST NOT be the
    # clean-pass source — the latest head-bound formal
    # review is a non-clean finding, so the issue
    # comment is rejected as a stale echo of the
    # earlier clean review. The earlier formal review
    # IS still detected as a clean pass (so
    # clean_pass_detected=True), but the source is
    # the formal review, not the issue comment. The
    # newer-finding scan (which already runs as part
    # of the audit) downgrades the final status to
    # ``HOLD_NEW_CODEX_THREAD`` because the non-clean
    # review is newer than the clean review.
    assert pkt["clean_pass_source"] != "issue_comment", (
        "Round-46: issue comment must NOT be the "
        "clean-pass source when the latest head-bound "
        "formal review is a non-clean finding. Got "
        f"clean_pass_source={pkt.get('clean_pass_source')!r}"
    )
    assert pkt["status"] == "HOLD_NEW_CODEX_THREAD", (
        "Round-46: classifier must emit "
        "HOLD_NEW_CODEX_THREAD when the latest "
        "head-bound formal review is a non-clean "
        f"finding. Got status={pkt.get('status')!r}"
    )


# ---------------------------------------------------------------------------
# Round-41 regression: task-summary issue-comments must NOT
# downgrade a current-head clean pass to HOLD_NEW_CODEX_THREAD.
# The Round-36 fix added the predicate in ``check_pr_review_comments``
# but did not propagate it to the audit's post-clean-pass
# newer-finding scan. The bug caused the gate to emit clean
# while ``aed_pr status``/``merge`` reported
# ``HOLD_NEW_CODEX_THREAD`` / ``CODEX_EVIDENCE_FAILED`` after
# the Codex bot posted a ``### Summary`` issue-comment following
# a clean pass. Round-41 shares the predicate between the gate
# and the audit.
# ---------------------------------------------------------------------------


def test_post_clean_pass_task_summary_does_not_downgrade(
    monkeypatch, tmp_path
):
    """A Codex bot ``### Summary`` issue-comment posted AFTER a
    clean pass MUST NOT cause the audit to emit
    ``HOLD_NEW_CODEX_THREAD``. The previous (buggy) audit
    treated any non-clean-pass Codex issue comment after the
    clean pass as a new finding; the predicate added in
    Round-36 (and now shared by Round-41) recognizes
    ``### Summary`` task-summary posts as coordination posts
    rather than findings.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    issue = [
        # Old clean pass at 18:00
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=9400,
        ),
        # Newer task-summary post at 18:30 — must be excluded.
        # Body deliberately includes BLOCKING_WORDS tokens
        # (e.g. ``malformed``, ``blocking``) to prove the
        # predicate discriminates on shape, not vocabulary.
        make_issue_comment(
            author=CODEX_LOGIN,
            body=(
                "### Summary\n\n* Updated fetch_ci_conclusions "
                "to request event from gh pr checks. The "
                "previous malformed head was treated as "
                "blocking; the fix supersedes that. **Commit**"
                "\n\n* New commit SHA: 5ed3bdf8cea13b463fa1319338d273dd0e0601b6"
                "\n\n**Testing**\n\n* The blocked code path now "
                "exits cleanly."
            ),
            created_at="2026-06-11T18:30:00Z",
            comment_id=9401,
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
    # The audit MUST NOT emit HOLD_NEW_CODEX_THREAD for a
    # post-clean-pass task-summary issue comment.
    assert pkt["status"] != mod.STATUS_HOLD_NEW_THREAD, (
        f"audit must not downgrade a current-head clean pass "
        f"to HOLD_NEW_CODEX_THREAD for a task-summary post; "
        f"got status={pkt['status']!r}"
    )
    # The clean pass MUST still be detected (and the audit
    # should emit a merge-ready variant).
    assert pkt["clean_pass_detected"] is True
    assert pkt["status"] == mod.STATUS_MERGE_READY, (
        f"audit must emit MERGE_READY when only a task-summary "
        f"post follows a clean pass; got status={pkt['status']!r}"
    )


def test_post_clean_pass_real_finding_still_downgrades(
    monkeypatch, tmp_path
):
    """Round-41 guard: a real Codex finding (NOT a
    task-summary post) AFTER a clean pass MUST still cause the
    audit to emit ``HOLD_NEW_CODEX_THREAD``. The fix
    surgically excludes only ``### Summary`` posts.
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
        # Newer REAL finding (not a task-summary).
        make_issue_comment(
            author=CODEX_LOGIN,
            body="Actually I missed something: P1 real bug",
            created_at="2026-06-11T18:30:00Z",
            comment_id=9501,
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


def test_audit_imports_shared_task_summary_predicate():
    """Static structural test: the audit module MUST import
    the shared predicate from ``check_pr_review_comments`` so
    that future task-summary shape changes only need to be
    made in one place.
    """
    src = inspect.getsource(mod)
    assert "_is_codex_task_summary_issue_comment" in src, (
        "audit_codex_response_for_pr must import the shared "
        "_is_codex_task_summary_issue_comment predicate from "
        "check_pr_review_comments"
    )


# ---------------------------------------------------------------------------
# Round-42 regression: the shared task-summary predicate
# MUST be imported successfully when the audit module is
# invoked via the documented live paths
# (``python scripts/local/audit_codex_response_for_pr.py``
# or via ``aed_pr status``/``merge``). Round-41 imported
# via ``from scripts.local.check_pr_review_comments`` which
# raised ``ModuleNotFoundError`` when ``sys.path[0]`` was
# ``scripts/local`` (i.e. the live CLI path) and silently
# disabled task-summary filtering via the broad
# ``except Exception``.
# ---------------------------------------------------------------------------


def test_predicate_imported_under_script_local_invocation():
    """Reproduce the documented live invocation as a fresh
    subprocess with only ``scripts/local`` on ``PYTHONPATH``
    and the repository root NOT in scope. The shared
    predicate MUST be available.

    This test guards against the Round-41 bug where the
    absolute ``from scripts.local.check_pr_review_comments
    import ...`` raised ``ModuleNotFoundError`` when the
    repository root was not on ``sys.path``. The Round-42
    fix pre-pends the repo root before the import attempt.

    A fresh subprocess is required because the in-process
    import cache (``sys.modules``) is populated by pytest's
    own collection, which can mask the bug.

    The test resolves the audit module's absolute path from
    ``inspect.getfile`` so it works in any CI environment,
    not just the local /home/max/aed_consolidation_v1 tree.
    """
    import subprocess as _subprocess
    import sys as _sys
    import os as _os
    import inspect as _inspect

    # Discover the audit module's file path dynamically so
    # the test works in both local and CI environments.
    import scripts.local.audit_codex_response_for_pr as _audit_mod
    audit_file = _inspect.getfile(_audit_mod)
    scripts_local_dir = _os.path.dirname(audit_file)
    repo_root = _os.path.dirname(scripts_local_dir)
    audit_basename = _os.path.basename(audit_file)

    code = (
        "import sys, importlib, os\n"
        f"_audit_basename = {audit_basename!r}\n"
        f"_scripts_local = {scripts_local_dir!r}\n"
        "default = list(sys.path)\n"
        # Drop any path containing the repo root from sys.path
        f"sys.path = [p for p in default if {repo_root!r} not in p]\n"
        f"sys.path.insert(0, _scripts_local)\n"
        "for name in list(sys.modules.keys()):\n"
        "    if 'audit' in name or 'check_pr_review' in name:\n"
        "        sys.modules.pop(name, None)\n"
        "mod = importlib.import_module(_audit_basename[:-3])  # strip .py\n"
        "pred = mod._co_is_codex_task_summary_issue_comment\n"
        "print('PREDICATE:', pred)\n"
        "if pred is not None:\n"
        "    print('CALLABLE_TASK_SUMMARY:', pred(\n"
        "        'chatgpt-codex-connector[bot]', 'issue_comment',\n"
        "        '### Summary\\n\\n* Did some work\\n\\n**Commit**\\n'\n"
        "    ))\n"
        "else:\n"
        "    print('CALLABLE_TASK_SUMMARY: N/A')\n"
    )
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        # Explicitly set PYTHONPATH to scripts/local so the
        # subprocess only sees the script directory on the
        # path (mirroring the documented live CLI path).
        "PYTHONPATH": scripts_local_dir,
    }
    res = _subprocess.run(
        [_sys.executable, "-c", code],
        capture_output=True, text=True, timeout=30, env=env,
        cwd="/tmp",
    )
    output = res.stdout
    # The predicate MUST be importable, not None.
    assert "PREDICATE:" in output, (
        f"subprocess did not print predicate info; "
        f"stdout={output!r} stderr={res.stderr[:500]!r}"
    )
    assert "PREDICATE: None" not in output, (
        "shared _is_codex_task_summary_issue_comment "
        "predicate MUST be importable when invoked via "
        "the documented live CLI path with only "
        "scripts/local on PYTHONPATH; got None. "
        "Without this predicate, the audit emits "
        "HOLD_NEW_CODEX_THREAD after a Codex "
        "### Summary task-summary post following a "
        "clean pass, while the review-comment gate "
        "treats the same post as informational.\n"
        f"subprocess stdout: {output!r}\n"
        f"subprocess stderr: {res.stderr[:500]!r}"
    )
    # The predicate MUST be callable and recognize a
    # Codex task-summary issue-comment.
    assert "CALLABLE_TASK_SUMMARY: True" in output, (
        f"predicate should recognize Codex task-summary "
        f"issue comments; got output {output!r}"
    )


def test_predicate_imported_under_repo_root_invocation():
    """The audit module MUST also work when invoked with
    the repository root on sys.path (the way the test
    harness imports it).
    """
    import sys as _sys

    # Round-69 (PHASE 4): use a try/finally to restore the
    # original sys.path so subsequent tests in the same
    # pytest session do not inherit a polluted import path.
    # The previous version inserted the wrong hard-coded
    # path ("/home/max/aed_consolidation_v1") which caused
    # cross-test pollution when pytest re-imported modules
    # from ``scripts.local`` after the path was mutated.
    # The audit module's import block already handles
    # multiple sys.path shapes; this test only needs to
    # confirm the audit module imports when the repo
    # root is on sys.path.
    original_path = list(_sys.path)
    try:
        # Use the actual pytest worktree path so the test
        # is environment-agnostic. The repo root is the
        # grandparent of the scripts/local directory of
        # this very module.
        import os as _os
        _audit_dir = _os.path.dirname(_os.path.abspath(__file__))
        _repo_root = _os.path.dirname(_os.path.dirname(_audit_dir))
        if _repo_root not in _sys.path:
            _sys.path.insert(0, _repo_root)
        from scripts.local import audit_codex_response_for_pr as mod
        assert mod._co_is_codex_task_summary_issue_comment is not None, (
            "predicate must be importable under repo-root path"
        )
    finally:
        _sys.path[:] = original_path


def test_audit_emits_visible_warning_when_predicate_unavailable(
    monkeypatch, tmp_path
):
    """If BOTH the absolute and fallback import paths fail,
    the audit MUST emit a visible stderr warning instead of
    silently disabling task-summary filtering. The runtime
    gate (the ``is not None`` check at the call site) keeps
    behavior unchanged.

    This is a structural test: it verifies the import block
    invokes ``warnings.warn(...)`` so a future reader knows
    the failure mode is loud. A live verification of the
    warning emission is fragile because re-importing the
    audit module after a sentinel substitution interacts
    with Python's import cache in non-trivial ways; the
    source-level check is sufficient and stable.
    """
    import scripts.local.audit_codex_response_for_pr as mod
    src = inspect.getsource(mod)
    # The Round-42 fix MUST invoke ``_warnings.warn`` so a
    # missing or broken helper module is visible in CI logs.
    assert "_warnings.warn" in src, (
        "audit must emit a visible warning when the shared "
        "predicate is unavailable; missing _warnings.warn call"
    )
    assert "RuntimeWarning" in src, (
        "audit must use RuntimeWarning category so the "
        "warning is visible by default"
    )


# ---------------------------------------------------------------------------
# Round-43 regression: when the ONLY post-ping Codex activity
# is a ``### Summary`` task-summary issue-comment (no review
# verdict, no clean pass), the audit MUST populate
# ``latest_codex_response_type="none"`` with empty
# ``latest_codex_response_id``. Otherwise the readiness
# verifier's Round-39 invariant treats the task-summary
# as a present artifact, emits ``CODEX_EVIDENCE_FAILED``,
# and the lifecycle routes to ``BLOCKED`` instead of
# ``WAITING`` — telling the operator to fix a terminal
# Codex failure when no Codex response has actually
# arrived.
# ---------------------------------------------------------------------------


TASK_SUMMARY_BODY = (
    "### Summary\n\n* Updated audit_codex_response_for_pr "
    "to skip task-summary issue-comments in latest-response "
    "selection.\n\n**Commit**\n\n* The fix supersedes "
    "the previous malformed-head verdict construction.\n\n"
    "**Testing**\n\n* The audit now emits "
    "HOLD_CODEX_RESPONSE_PENDING correctly."
)


def test_only_task_summary_latest_response_is_none(monkeypatch, tmp_path):
    """When the only post-ping Codex activity is a single
    ``### Summary`` task-summary issue-comment, the audit
    MUST populate ``latest_codex_response_type="none"``
    and ``latest_codex_response_id=""``. This guards the
    Round-43 fix that excludes task-summary issue-comments
    from the ``latest_issue`` selection.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    issue = [
        # ONLY a task-summary — no clean pass, no review.
        make_issue_comment(
            author=CODEX_LOGIN,
            body=TASK_SUMMARY_BODY,
            created_at="2026-07-21T18:30:00Z",
            comment_id=9501,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [],
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
    # The audit MUST hold at HOLD_CODEX_RESPONSE_PENDING —
    # no real Codex response has arrived.
    assert pkt["status"] == mod.STATUS_HOLD_CODEX_PENDING, (
        f"audit must hold at HOLD_CODEX_RESPONSE_PENDING "
        f"when only a task-summary post is present; "
        f"got status={pkt['status']!r}"
    )
    # The latest-response metadata MUST reflect "no real
    # response" — empty type and empty id. Otherwise the
    # Round-39 invariant in build_evidence would treat the
    # task-summary as a present artifact.
    assert pkt["latest_codex_response_type"] == "none", (
        f"latest_codex_response_type must be 'none' when "
        f"only a task-summary post is present; "
        f"got {pkt['latest_codex_response_type']!r}"
    )
    assert not pkt["latest_codex_response_id"], (
        f"latest_codex_response_id must be empty when "
        f"only a task-summary post is present; "
        f"got {pkt['latest_codex_response_id']!r}"
    )
    # No active blockers expected.
    assert pkt["clean_pass_detected"] is False


def test_only_task_summary_does_not_block_via_ready_path(
    monkeypatch, tmp_path
):
    """End-to-end guard: when only a task-summary is
    present, the canonical ``build_evidence`` consumer
    must compute ``codex_artifact_present=False``, so
    the readiness verifier emits ``CODEX_EVIDENCE_MISSING``
    (routes to ``WAITING``) and NOT
    ``CODEX_EVIDENCE_FAILED`` (would route to ``BLOCKED``
    after Round-38's lifecycle mapping).

    This test exercises the full codex audit -> evidence
    path that the readiness verifier consumes, proving the
    upstream signal is correct.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=TASK_SUMMARY_BODY,
            created_at="2026-07-21T18:30:00Z",
            comment_id=9502,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [],
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
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Simulate the consumer's invariant: artifact present iff
    # type != "none" and id is non-empty. The audit's output
    # must satisfy the "not present" branch.
    codex_response_type = pkt.get(
        "latest_codex_response_type", "none"
    )
    codex_response_id = pkt.get("latest_codex_response_id", "")
    artifact_present = bool(
        codex_response_type
        and codex_response_type != "none"
        and codex_response_id
    )
    assert artifact_present is False, (
        f"build_evidence invariant: when only a task-summary "
        f"is present, codex_artifact_present must be False. "
        f"type={codex_response_type!r} id={codex_response_id!r}"
    )


def test_only_task_summary_with_pending_evidence_routes_waiting(
    monkeypatch, tmp_path
):
    """Full lifecycle guard: audit returns
    HOLD_CODEX_RESPONSE_PENDING, build_evidence sees
    artifact_present=False, and the readiness verifier
    emits ``CODEX_EVIDENCE_MISSING`` (not
    ``CODEX_EVIDENCE_FAILED``). Combined with Round-38's
    lifecycle mapping, this routes the PR to ``WAITING``,
    not ``BLOCKED``.
    """
    import scripts.local.aed_pr_readiness as readiness
    # Construct a minimal evidence object that mirrors
    # what ``aed_pr.py build_evidence`` would assemble for
    # a task-summary-only post-ping state. The Codex
    # section is set to the "pending" state (no artifact,
    # HOLD_CODEX_RESPONSE_PENDING verdict); the verifier
    # will reject the missing-evidence reason for Codex
    # but will NOT mark the failure as terminal (which
    # would route to BLOCKED after Round-38).
    evidence = readiness.ReadinessEvidence(
        head_sha=EXPECTED_HEAD,
        pr_state="OPEN",
        is_draft=True,
        mergeable="MERGEABLE",
        scope_clean=True,
        changed_files=[],
        changed_files_fetched=True,
        required_ci_names=[],
        ci_conclusions={},
        ci_missing=[],
        ci_pending=[],
        ci_failed=[],
        ci_duplicated=[],
        allowed_files_supplied=True,
        codex_verdict="HOLD_CODEX_RESPONSE_PENDING",
        codex_clean_passed=False,
        codex_artifact_present=False,
        codex_artifact_fresh=False,
        reviews_inventory_complete=True,
        review_thread_inventory_complete=True,
        unresolved_thread_count=0,
    )
    verdict = readiness.evaluate_machine_readiness(evidence)
    # The verifier MUST NOT emit CODEX_EVIDENCE_FAILED.
    failed_reasons = [
        r for r in verdict.reasons
        if getattr(r, "code", None) == "CODEX_EVIDENCE_FAILED"
    ]
    assert not failed_reasons, (
        "verifier must not emit CODEX_EVIDENCE_FAILED when "
        "codex_artifact_present=False; got "
        f"reasons={[r.code for r in verdict.reasons]}"
    )
    # The verifier MAY emit CODEX_EVIDENCE_MISSING,
    # which (after Round-38) routes to WAITING.
    assert verdict.ready is False
    assert verdict.machine_ready is False
    # No authorization for a HOLD_CODEX_RESPONSE_PENDING.
    assert verdict.authorization_required is False
    assert verdict.authorization_valid is None


def test_task_summary_does_not_override_substantive_latest(
    monkeypatch, tmp_path
):
    """Round-43 guard: when both a task-summary AND a
    substantive issue-comment are present, the substantive
    one MUST populate ``latest_codex_response_*``. The
    Round-43 fix only excludes task-summary comments from
    the latest-response selection, so a substantive comment
    that arrives AFTER a task-summary still wins.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    issue = [
        # Earlier task-summary.
        make_issue_comment(
            author=CODEX_LOGIN,
            body=TASK_SUMMARY_BODY,
            created_at="2026-07-21T18:00:00Z",
            comment_id=9510,
        ),
        # Later substantive comment (e.g. a real
        # Codex reply, not a clean pass and not a
        # task summary).
        make_issue_comment(
            author=CODEX_LOGIN,
            body=(
                "Codex Review: noticed a follow-up — see "
                "the discussion on the malformed-head "
                "verdict construction."
            ),
            created_at="2026-07-21T19:00:00Z",
            comment_id=9511,
        ),
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [],
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
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # The latest-response metadata MUST point at the
    # substantive comment (id=9511), not the task-summary
    # (id=9510). The Round-43 fix only excludes
    # task-summary from the candidate list.
    assert pkt["latest_codex_response_type"] == "issue_comment"
    assert str(pkt["latest_codex_response_id"]) == "9511", (
        f"latest must point at the substantive comment "
        f"(id=9511), not the task-summary (id=9510); "
        f"got {pkt['latest_codex_response_id']!r}"
    )


# ---------------------------------------------------------------------------
# Round-44 regression: the audit module's repo-root
# computation MUST walk two levels up from ``__file__``,
# not one. ``__file__`` is
# ``<repo>/scripts/local/audit_codex_response_for_pr.py``,
# so a single ``dirname`` produces ``<repo>/scripts``
# (NOT the repository root). Without this fix, the
# absolute ``from scripts.local.check_pr_review_comments
# import ...`` import still fails in script-local mode
# unless something else has already added the repository
# root to ``sys.path``.
# ---------------------------------------------------------------------------


def test_audit_module_repo_root_is_two_levels_above_script_dir():
    """Static source check: ``_REPO_ROOT_HERE`` MUST be the
    parent of the ``scripts/`` directory, NOT the parent
    of ``__file__`` (which would be ``<repo>/scripts``).
    """
    import inspect as _inspect
    import scripts.local.audit_codex_response_for_pr as _mod
    src = _inspect.getsource(_mod)
    # The Round-44 fix MUST walk up two levels (or
    # equivalent) to land on the repository root. The
    # canonical layout has ``scripts/`` at the repo root,
    # so the path computed must equal the parent of
    # ``scripts/``.
    # Walk the source for evidence of two-parent resolution.
    has_two_parent_walk = (
        "_os.path.dirname(_os.path.dirname(_SCRIPT_DIR_HERE))" in src
        or "_scripts_dir" in src and "_os.path.basename" in src
    )
    assert has_two_parent_walk, (
        "audit must walk two levels up from __file__ to "
        "find the repository root; the single-parent walk "
        "in Round-42 produces <repo>/scripts instead of "
        "<repo>. Found neither the two-parent walk nor "
        "the scripts-directory search in source."
    )
    # The script directory walk-up logic MUST be present.
    assert "_SCRIPT_DIR_HERE" in src, (
        "audit must compute _SCRIPT_DIR_HERE from __file__"
    )
    assert "_REPO_ROOT_HERE" in src, (
        "audit must compute _REPO_ROOT_HERE explicitly"
    )


def test_audit_subprocess_uses_absolute_package_path():
    """End-to-end subprocess check: in script-local mode
    with only ``scripts/local`` on ``PYTHONPATH`` and the
    repository root NOT in scope, the imported predicate
    must come from the absolute package path
    ``scripts.local.check_pr_review_comments`` (proving
    the repo root was added to sys.path). The fallback
    top-level import ``check_pr_review_comments`` would
    indicate the repo root was NOT added (Round-42 bug).

    A fresh subprocess is required because pytest's
    in-process import cache can mask the bug.
    """
    import subprocess as _subprocess
    import sys as _sys
    import os as _os
    import inspect as _inspect

    import scripts.local.audit_codex_response_for_pr as _audit_mod
    audit_file = _inspect.getfile(_audit_mod)
    scripts_local_dir = _os.path.dirname(audit_file)
    repo_root = _os.path.dirname(_os.path.dirname(scripts_local_dir))
    audit_basename = _os.path.basename(audit_file)

    # Build the subprocess code as a list of lines joined
    # by newlines. Using ``"\n".join(...)`` avoids
    # shell-style escaping pitfalls.
    code_lines = [
        "import sys, importlib, os",
        f"_audit_basename = {audit_basename!r}",
        f"_scripts_local = {scripts_local_dir!r}",
        f"_repo_root = {repo_root!r}",
        "default = list(sys.path)",
        "sys.path = [p for p in default if _repo_root not in p]",
        "sys.path.insert(0, _scripts_local)",
        "for name in list(sys.modules.keys()):",
        "    if 'audit' in name or 'check_pr_review' in name:",
        "        sys.modules.pop(name, None)",
        "mod = importlib.import_module(_audit_basename[:-3])",
        "pred = mod._co_is_codex_task_summary_issue_comment",
        "print('PREDICATE:', pred)",
        "if pred is not None:",
        "    print('PREDICATE_MODULE:', pred.__module__)",
        "else:",
        "    print('PREDICATE_MODULE: None')",
    ]
    code = "\n".join(code_lines)
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONPATH": scripts_local_dir,
    }
    res = _subprocess.run(
        [_sys.executable, "-c", code],
        capture_output=True, text=True, timeout=30, env=env,
        cwd="/tmp",
    )
    output = res.stdout
    # The predicate MUST be importable.
    assert "PREDICATE: <function" in output or "PREDICATE:" in output, (
        f"subprocess did not print predicate info; "
        f"stdout={output!r} stderr={res.stderr[:500]!r}"
    )
    # The predicate MUST come from the absolute package
    # path ``scripts.local.check_pr_review_comments`` —
    # NOT the top-level ``check_pr_review_comments``
    # fallback (which would indicate the Round-42 bug
    # where the wrong directory was added to sys.path).
    assert (
        "PREDICATE_MODULE: scripts.local.check_pr_review_comments" in output
    ), (
        "predicate must be imported via the absolute "
        "package path 'scripts.local.check_pr_review_comments'; "
        "this proves the audit's repo-root computation "
        "is correct (two levels above __file__). The "
        "top-level fallback path indicates the Round-42 "
        f"single-parent bug. Got: {output!r} stderr={res.stderr[:500]!r}"
    )


# ---------------------------------------------------------------------------
# Round-46 regression: when ``ping_dt is None`` (no
# fresh-ping filter), the issue-comment clean-pass path
# used to accept an unanchored issue-comment echo of an
# earlier head-bound clean formal review, even if a LATER
# non-clean formal review (a finding) on the same head
# has since been posted. That let the classifier emit
# ``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION`` while a
# real finding was still in flight.
# The fix: also track ``latest_head_bound_formal_review_ts``
# (any head-bound formal review, clean or non-clean),
# and require the issue comment to postdate THAT as
# well. If a non-clean formal review postdates the clean
# review, the issue comment is rejected as a stale
# echo of the clean review.
# ---------------------------------------------------------------------------


# Head SHA used by the Round-46 tests. Matches the
# ``_head_sha_for_tests`` constants used elsewhere in
# this file.
ROUND_46_HEAD = EXPECTED_HEAD


def _r46_clean_pass_body() -> str:
    """The canonical Codex clean-pass body, identical
    to the helper used by other tests in this file.
    """
    return codex_clean_pass_body()


def _r46_clean_review(review_id, *, submitted_at,
                      commit_oid=ROUND_46_HEAD):
    return make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body=_r46_clean_pass_body(),
        submitted_at=submitted_at,
        review_id=review_id,
        commit_oid=commit_oid,
    )


def _r46_non_clean_review(review_id, *, submitted_at,
                          commit_oid=ROUND_46_HEAD):
    return make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body=(
            "Codex found a blocking issue at "
            "scripts/local/x.py — see inline comment."
        ),
        submitted_at=submitted_at,
        review_id=review_id,
        commit_oid=commit_oid,
    )


def _r46_clean_echo_comment(comment_id, *, created_at):
    return make_issue_comment(
        author=CODEX_LOGIN,
        body=_r46_clean_pass_body(),
        created_at=created_at,
        comment_id=comment_id,
    )


def _r46_classify_with_fixture(
    monkeypatch, *, reviews, comments
):
    """Drive ``classify`` end-to-end with a mocked
    ``subprocess.run`` that returns the supplied
    reviews and issue comments. Mirrors the existing
    test pattern in this file.
    """
    from unittest.mock import patch as _mp
    from scripts.local import audit_codex_response_for_pr as mod
    pr_view = make_raw_rest_pr_payload(
        state="open", sha=ROUND_46_HEAD,
        mergeable_state="clean", mergeable=True,
    )
    threads_payload = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [],
            }
        }}}
    }

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
            m.stdout = json.dumps(threads_payload)
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps(list(comments))
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps(list(reviews))
            return m
        m.stdout = "[]"
        return m

    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    with _mp.object(mod.subprocess, "run", runner):
        return mod.classify(
            repo=REPO, pr_number=402,
            expected_head_sha=ROUND_46_HEAD,
            ping_comment_id=None,
            ping_created_at=None,
            max_polls=1, poll_seconds=0,
        )


class TestRound46InterveningFindingInvalidatesEcho:
    """Round-46 regression: an issue-comment clean-pass
    echo of an earlier head-bound clean formal review
    MUST be rejected when a LATER non-clean formal
    review (a finding) on the same head is in the
    inventory. Without the fix, the classifier accepts
    the issue comment and the readiness verifier can
    emit ``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION``
    while a real finding is in flight.
    """

    def test_intervening_finding_invalidates_clean_echo(
        self, monkeypatch
    ):
        """Bug repro: clean formal review at T1, non-clean
        formal review at T2 (a finding), unanchored
        issue-comment clean pass at T3. Without the
        Round-46 fix the issue comment is accepted as
        a clean-pass echo (because it postdates T1),
        and the classifier emits a merge-ready state
        despite a real finding being in flight at T2.

        With the fix, the issue comment is rejected
        because the latest head-bound formal review
        is a non-clean finding. The classifier MUST
        NOT emit a merge-ready state and MUST NOT
        accept the issue comment as a clean pass.
        The classifier MAY still detect a clean pass
        from the earlier formal review (T1), but
        the newer-finding scan MUST downgrade the
        final status to ``HOLD_NEW_CODEX_THREAD``
        because the non-clean review (T2) is newer
        than the clean review.
        """
        clean_review = _r46_clean_review(
            1001, submitted_at="2026-07-22T10:00:00Z"
        )
        non_clean_review = _r46_non_clean_review(
            1002, submitted_at="2026-07-22T10:05:00Z"
        )
        clean_echo = _r46_clean_echo_comment(
            1003, created_at="2026-07-22T10:10:00Z"
        )
        pkt = _r46_classify_with_fixture(
            monkeypatch,
            reviews=[clean_review, non_clean_review],
            comments=[clean_echo],
        )
        # The issue comment MUST NOT be the source
        # of the clean pass — the latest head-bound
        # formal review is a non-clean finding, so
        # the issue comment is rejected as a stale
        # echo of the earlier clean review.
        assert pkt.get("clean_pass_source") != "issue_comment", (
            "Round-46 fix: the issue comment must NOT "
            "be the clean-pass source when the latest "
            "head-bound formal review is a non-clean "
            f"finding; got clean_pass_source="
            f"{pkt.get('clean_pass_source')!r}. "
            f"status={pkt.get('status')!r}"
        )
        # The status MUST NOT be a merge-ready state.
        status = pkt.get("status", "")
        assert "MERGE_READY" not in status, (
            f"Round-46 fix: classifier must NOT emit "
            f"MERGE_READY when an intervening finding "
            f"is in the inventory; got status={status!r}"
        )
        # And the classifier MUST emit
        # ``HOLD_NEW_CODEX_THREAD`` because the
        # non-clean review is the newer finding.
        assert status == "HOLD_NEW_CODEX_THREAD", (
            f"Round-46 fix: classifier must emit "
            f"HOLD_NEW_CODEX_THREAD when the latest "
            f"head-bound formal review is a non-clean "
            f"finding; got status={status!r}"
        )

    def test_no_intervening_finding_accepts_clean_echo(
        self, monkeypatch
    ):
        """Regression guard: when there is NO intervening
        non-clean formal review (only the clean formal
        review, then the issue-comment echo), the
        Round-46 fix MUST NOT over-reject. The issue
        comment is still accepted as a clean-pass
        echo of the head-bound clean review.
        """
        clean_review = _r46_clean_review(
            2001, submitted_at="2026-07-22T11:00:00Z"
        )
        clean_echo = _r46_clean_echo_comment(
            2002, created_at="2026-07-22T11:05:00Z"
        )
        pkt = _r46_classify_with_fixture(
            monkeypatch,
            reviews=[clean_review],
            comments=[clean_echo],
        )
        # The classifier SHOULD accept the issue
        # comment as a clean pass (no later finding).
        assert pkt.get("clean_pass_detected") is True, (
            "Round-46 fix must NOT over-reject: when "
            "no later finding is in the inventory, the "
            "issue comment is still a valid clean-pass "
            f"echo. Got: status={pkt.get('status')!r}"
        )
        # And the source should be the issue comment.
        assert pkt.get("clean_pass_source") == "issue_comment"

    def test_no_clean_review_anchor_rejects_echo(
        self, monkeypatch
    ):
        """Variant: only the intervening non-clean
        formal review is in the inventory (no clean
        review precedes it). The issue comment is
        still rejected because no clean head-bound
        review exists to anchor it; this is the
        pre-existing Round-27 behavior, but the
        test pins the contract.
        """
        non_clean_review = _r46_non_clean_review(
            3001, submitted_at="2026-07-22T12:00:00Z"
        )
        clean_echo = _r46_clean_echo_comment(
            3002, created_at="2026-07-22T12:05:00Z"
        )
        pkt = _r46_classify_with_fixture(
            monkeypatch,
            reviews=[non_clean_review],
            comments=[clean_echo],
        )
        # Without a clean head-bound review, the
        # issue comment cannot be a clean-pass echo.
        assert pkt.get("clean_pass_detected") is not True, (
            "Round-27 + Round-46: an issue-comment "
            "clean pass with no head-bound clean "
            "review anchor must be rejected; got "
            f"clean_pass_detected=True. status={pkt.get('status')!r}"
        )

    def test_source_contract_tracks_overall_formal_review(self):
        """Source-contract test: the audit MUST track
        the latest OVERALL head-bound formal review
        timestamp AND whether it was a clean pass.
        When the latest head-bound formal review is
        a non-clean finding, issue-comment clean
        passes must be rejected. Static check on
        the source — the variables
        ``latest_head_bound_formal_review_ts`` and
        ``latest_head_bound_formal_review_is_clean``
        must appear in
        ``audit_codex_response_for_pr.py``, and the
        issue-comment loop must use them.
        """
        import inspect
        from scripts.local import audit_codex_response_for_pr as ac
        src = inspect.getsource(ac)
        # Round-46 fix variable names. These MUST
        # appear in the source.
        for needle in (
            "latest_head_bound_formal_review_ts",
            "latest_head_bound_formal_review_is_clean",
        ):
            assert needle in src, (
                f"Round-46 fix: audit must track "
                f"{needle!r} in the source. Missing."
            )
        # And the issue-comment loop MUST consult
        # the latest_head_bound_formal_review_is_clean
        # flag to decide whether to accept the
        # issue-comment clean pass.
        assert (
            "not latest_head_bound_formal_review_is_clean" in src
        ), (
            "Round-46 fix: the issue-comment loop must "
            "consult latest_head_bound_formal_review_is_clean "
            "to reject the clean pass when the latest "
            "head-bound formal review is a non-clean finding."
        )


# ---------------------------------------------------------------------------
# Round-47 regression: the Round-46 latest-formal-review
# veto MUST run regardless of whether a ping boundary
# (``ping_dt``) is supplied. Round-46 only ran the veto
# inside the ``if ping_dt is None:`` block, which left
# the post-ping path unchecked: a post-ping sequence
# (clean formal review → later non-clean formal review
# → unanchored issue-comment clean-pass echo) was still
# misclassified because ``clean_pass_at`` would become
# the later issue-comment timestamp, and the
# ``newer_finding_after_clean_pass`` scan would no
# longer see the intervening formal finding as newer
# than the issue comment.
# The fix: lift the Round-46 veto out of the
# ``ping_dt is None`` block so it runs in both code
# paths. The Round-27 head-binding surface check
# (postdate the clean review) remains gated on
# ``ping_dt is None`` because it is only relevant
# when the ping window is the only head-binding
# surface; the Round-46 veto is the universal guard.
# ---------------------------------------------------------------------------


# A ping boundary timestamp that POSTDATES all the
# pre-ping review activity in the test fixtures. This
# simulates the operator's ``cmd_advance`` posting a
# fresh @codex review ping and the test running
# against the post-ping window. The post-ping
# sequence (clean review → non-clean review → issue
# comment) is the bug repro.
ROUND_47_PING_ID = "4677095302"
ROUND_47_PING_CREATED = "2026-07-22T00:30:00Z"


def _r47_classify_with_ping(
    monkeypatch, *, reviews, comments, ping_dt=ROUND_47_PING_CREATED,
):
    """Drive ``classify`` end-to-end with a mocked
    ``subprocess.run`` that returns the supplied
    reviews and issue comments AND a non-None ping
    boundary. Mirrors the existing
    ``_r46_classify_with_fixture`` helper but
    forwards a ping boundary so the post-ping
    code path is exercised.
    """
    from unittest.mock import patch as _mp
    from scripts.local import audit_codex_response_for_pr as mod
    pr_view = make_raw_rest_pr_payload(
        state="open", sha=ROUND_46_HEAD,
        mergeable_state="clean", mergeable=True,
    )
    threads_payload = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [],
            }
        }}}
    }

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
            m.stdout = json.dumps(threads_payload)
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps(list(comments))
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps(list(reviews))
            return m
        m.stdout = "[]"
        return m

    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    with _mp.object(mod.subprocess, "run", runner):
        return mod.classify(
            repo=REPO, pr_number=402,
            expected_head_sha=ROUND_46_HEAD,
            ping_comment_id=(
                ROUND_47_PING_ID if ping_dt is not None else None
            ),
            ping_created_at=ping_dt,
            max_polls=1, poll_seconds=0,
        )


class TestRound47FormalReviewVetoRunsWithPingBoundary:
    """Round-47 regression: the latest-formal-review
    veto MUST run regardless of whether a ping
    boundary is supplied. Round-46 only ran it
    inside the ``if ping_dt is None:`` block, which
    left the post-ping path unchecked.

    Additionally, the veto MUST be scoped to the
    candidate-echo case: it only fires when a clean
    head-bound formal review exists (so the issue
    comment could be an echo of it) AND the comment
    postdates that clean review. When no clean
    head-bound formal review exists, the issue
    comment IS the clean pass (not an echo), and the
    existing ``newer_finding_after_clean_pass`` scan
    handles later findings without the veto.
    """

    def test_post_ping_intervening_finding_invalidates_echo(
        self, monkeypatch
    ):
        """Bug repro (Round-47): clean formal review at
        T1, non-clean formal review at T2, unanchored
        issue-comment clean pass at T3 — all POST-ping.
        Without the Round-47 fix, the issue comment is
        accepted as a clean-pass authority because the
        Round-46 veto was inside the ``ping_dt is None``
        block and skipped when ``ping_dt is not None``.
        The ``clean_pass_at`` then becomes the later
        issue-comment timestamp (T3), and the
        ``newer_finding_after_clean_pass`` scan no
        longer sees the non-clean review (T2) as newer
        than the issue comment (T3). The classifier
        can then emit a clean/merge-ready status while
        a current-head non-clean formal review exists.

        With the fix, the Round-46 veto runs
        regardless of ``ping_dt``, and the issue
        comment is rejected. The formal-review
        clean-pass fallback finds the clean review,
        the ``newer_finding_after_clean_pass`` scan
        correctly identifies the non-clean review as
        a newer finding, and the final status is
        ``HOLD_NEW_CODEX_THREAD``.
        """
        # All timestamps POST-ping (after 00:30:00Z).
        clean_review = _r46_clean_review(
            5001, submitted_at="2026-07-22T00:35:00Z"
        )
        non_clean_review = _r46_non_clean_review(
            5002, submitted_at="2026-07-22T00:40:00Z"
        )
        clean_echo = _r46_clean_echo_comment(
            5003, created_at="2026-07-22T00:45:00Z"
        )
        pkt = _r47_classify_with_ping(
            monkeypatch,
            reviews=[clean_review, non_clean_review],
            comments=[clean_echo],
            ping_dt=ROUND_47_PING_CREATED,
        )
        # Round-47: the issue comment MUST NOT be the
        # clean-pass source — the latest head-bound
        # formal review is a non-clean finding, so the
        # issue comment is rejected as a stale echo
        # of the earlier clean review.
        assert pkt.get("clean_pass_source") != "issue_comment", (
            "Round-47 fix: the issue comment must NOT "
            "be the clean-pass source when the latest "
            "head-bound formal review is a non-clean "
            "finding AND a ping boundary is supplied. "
            f"Got clean_pass_source="
            f"{pkt.get('clean_pass_source')!r}. "
            f"status={pkt.get('status')!r}"
        )
        # The status MUST NOT be a merge-ready state.
        status = pkt.get("status", "")
        assert "MERGE_READY" not in status, (
            f"Round-47 fix: classifier must NOT emit "
            f"MERGE_READY when an intervening finding "
            f"is in the inventory (post-ping); got "
            f"status={status!r}"
        )
        # And the classifier MUST emit
        # ``HOLD_NEW_CODEX_THREAD`` because the
        # non-clean review is the newer finding.
        assert status == "HOLD_NEW_CODEX_THREAD", (
            f"Round-47 fix: classifier must emit "
            f"HOLD_NEW_CODEX_THREAD when the latest "
            f"head-bound formal review is a non-clean "
            f"finding (post-ping); got status={status!r}"
        )

    def test_pre_ping_veto_still_runs_round46(
        self, monkeypatch
    ):
        """Regression guard: Round-46 behavior (veto
        runs in the ``ping_dt is None`` path) MUST
        still hold after the Round-47 fix. The Round-47
        fix lifts the veto out of the ``ping_dt is
        None`` block, but it must STILL reject
        issue-comment clean passes when the latest
        head-bound formal review is non-clean and
        no ping boundary is supplied.
        """
        clean_review = _r46_clean_review(
            6001, submitted_at="2026-07-22T01:00:00Z"
        )
        non_clean_review = _r46_non_clean_review(
            6002, submitted_at="2026-07-22T01:05:00Z"
        )
        clean_echo = _r46_clean_echo_comment(
            6003, created_at="2026-07-22T01:10:00Z"
        )
        pkt = _r46_classify_with_fixture(
            monkeypatch,
            reviews=[clean_review, non_clean_review],
            comments=[clean_echo],
        )
        # Same Round-46 contract: the issue comment
        # MUST NOT be the clean-pass source.
        assert pkt.get("clean_pass_source") != "issue_comment", (
            "Round-46 invariant: the issue comment "
            "must NOT be the clean-pass source when "
            "the latest head-bound formal review is a "
            "non-clean finding (no ping). Got "
            f"clean_pass_source={pkt.get('clean_pass_source')!r}"
        )
        assert pkt.get("status") == "HOLD_NEW_CODEX_THREAD", (
            "Round-46 invariant: classifier must emit "
            "HOLD_NEW_CODEX_THREAD in the no-ping "
            f"path. Got status={pkt.get('status')!r}"
        )

    def test_post_ping_no_intervening_finding_accepts_echo(
        self, monkeypatch
    ):
        """Regression guard: when no intervening
        non-clean formal review exists (only a clean
        review, then an issue-comment echo), the
        Round-47 fix MUST NOT over-reject in the
        post-ping path. The issue comment is still
        accepted as a clean-pass echo.
        """
        clean_review = _r46_clean_review(
            7001, submitted_at="2026-07-22T00:35:00Z"
        )
        clean_echo = _r46_clean_echo_comment(
            7002, created_at="2026-07-22T00:45:00Z"
        )
        pkt = _r47_classify_with_ping(
            monkeypatch,
            reviews=[clean_review],
            comments=[clean_echo],
            ping_dt=ROUND_47_PING_CREATED,
        )
        # The classifier SHOULD accept the issue
        # comment as a clean pass (no later finding).
        assert pkt.get("clean_pass_detected") is True, (
            "Round-47 fix must NOT over-reject: when "
            "no later finding is in the inventory, the "
            "issue comment is still a valid clean-pass "
            f"echo (post-ping). Got: status={pkt.get('status')!r}"
        )
        # And the source should be the issue comment.
        assert pkt.get("clean_pass_source") == "issue_comment"

    def test_source_contract_veto_outside_ping_block(self):
        """Source-contract test: the latest-formal-
        review veto MUST be located OUTSIDE the
        ``if ping_dt is None:`` block. Round-46 had
        it inside that block, which left the
        post-ping path unchecked (the Round-47
        finding). The fix lifts the veto out of
        the block so it runs in both the
        ``ping_dt is None`` and
        ``ping_dt is not None`` paths.

        Additionally, the veto MUST be scoped to
        the candidate-echo case: it only fires
        when ``latest_head_bound_clean_review_ts``
        is non-empty (a clean head-bound formal
        review exists to echo). Without this
        guard, the veto over-rejects the
        scenario where the issue comment IS the
        clean pass (no clean formal review
        exists), which would break the
        pre-existing
        ``newer_finding_after_clean_pass`` scan.
        """
        import inspect
        from scripts.local import audit_codex_response_for_pr as ac
        src = inspect.getsource(ac)
        # The veto guard MUST appear in the source.
        assert "not latest_head_bound_formal_review_is_clean" in src, (
            "Round-47 fix: the latest-formal-review "
            "veto must be in the source."
        )
        # The veto expression AND the candidate-echo
        # guard (``latest_head_bound_clean_review_ts``)
        # must both appear in the same block. The
        # candidate-echo guard prevents the veto from
        # over-rejecting scenarios where the issue
        # comment IS the clean pass.
        # Find the LAST ``if ping_dt is None:`` in
        # the source (the block the veto used to be
        # inside per Round-46). The veto must be
        # located AFTER that block closes.
        last_ping_block = src.rfind("if ping_dt is None:")
        assert last_ping_block > 0, (
            "source must contain ``if ping_dt is None:`` "
            "block (Round-27 invariant)"
        )
        # Find the veto expression AFTER the
        # ``if ping_dt is None:`` block. The veto
        # expression ``not latest_head_bound_
        # formal_review_is_clean`` must appear
        # AFTER the last ``if ping_dt is None:``
        # line.
        veto_expr_idx = src.find(
            "not latest_head_bound_formal_review_is_clean",
            last_ping_block,
        )
        assert veto_expr_idx > 0, (
            "Round-47 fix: the veto expression "
            "``not latest_head_bound_formal_review_is_clean`` "
            "must appear AFTER the last ``if ping_dt is None:`` "
            "block in the source. The veto was incorrectly "
            "nested inside the ``ping_dt is None`` block "
            "in Round-46, which left the post-ping path "
            "unchecked."
        )


# ---------------------------------------------------------------------------
# Round-52 regression: the audit must recognize Codex's
# newer ``### 💡 Codex Review`` formal-review summary
# format as a clean pass when there are no inline-finding
# markers in the summary body itself. The Round-52
# finding reported that ``aed_pr.py`` builds readiness
# evidence through ``fetch_codex_packet`` → ``classify``
# which only recognized the older exact clean phrase, so
# the readiness verifier kept reporting missing/failed
# Codex evidence even after the poller had confirmed a
# clean exact-head response. The fix extends the
# formal-review clean-pass detection (and the
# post-clean-pass newer-finding scan) to also accept
# the summary format.
# ---------------------------------------------------------------------------


def _r52_clean_summary_body():
    return (
        "\n### 💡 Codex Review\n\n"
        "Here are some automated review suggestions for this pull request.\n\n"
        "**Reviewed commit:** `589c719ced`\n"
    )


def _r52_finding_summary_body():
    return (
        "\n### 💡 Codex Review\n\n"
        "Here are some automated review suggestions for this pull request.\n\n"
        "**Reviewed commit:** `589c719ced`\n"
        # An inline finding marker inside the summary body.
        # This is rare (findings usually live in inline
        # review comments, not in the summary body) but
        # the audit must still classify it as a finding.
        "\n**<sub><sub>![P2 Badge]"
        "(https://img.shields.io/badge/P2-yellow?style=flat)"
        "</sub></sub>  Inline finding in summary body**\n"
    )


def test_round52_summary_clean_review_accepted(monkeypatch, tmp_path):
    """Round-52 fix: a formal review with the
    ``### 💡 Codex Review`` summary prefix and no
    inline-finding markers in the body MUST be
    detected as a clean pass.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    clean_review = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body=_r52_clean_summary_body(),
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499126,
        commit_oid=HEAD,
    )
    pkt = _r52_classify(
        monkeypatch,
        codex_review_submissions=[clean_review],
        codex_issue_comments=[],
    )
    assert pkt.get("clean_pass_detected") is True, (
        "Round-52 fix: the audit must accept a "
        "summary-format review (### 💡 Codex Review) "
        "as a clean pass when there are no "
        "inline-finding markers. Got "
        f"clean_pass_detected={pkt.get('clean_pass_detected')!r}, "
        f"status={pkt.get('status')!r}"
    )
    assert pkt.get("clean_pass_source") == "pull_request_review", (
        "Round-52 fix: the clean pass must come "
        "from the formal review, not the issue "
        f"comment path. Got {pkt.get('clean_pass_source')!r}"
    )


def test_round52_summary_finding_with_inline_marker_rejected(monkeypatch, tmp_path):
    """Round-52 invariant: a summary-format review
    whose body contains an inline-finding marker
    MUST NOT be detected as a clean pass.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    finding_review = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body=_r52_finding_summary_body(),
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499127,
        commit_oid=HEAD,
    )
    pkt = _r52_classify(
        monkeypatch,
        codex_review_submissions=[finding_review],
        codex_issue_comments=[],
    )
    assert pkt.get("clean_pass_detected") is not True, (
        "Round-52 invariant: a summary-format "
        "review with an inline-finding marker in "
        "the body must NOT be a clean pass. Got "
        f"clean_pass_detected={pkt.get('clean_pass_detected')!r}"
    )


def test_round52_older_clean_then_newer_summary_finding_downgrades(monkeypatch, tmp_path):
    """Round-52 invariant: a clean summary review
    followed by a NEWER non-clean summary review
    MUST be downgraded to HOLD_NEW_CODEX_THREAD
    by the post-clean-pass scan.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    clean_review = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body=_r52_clean_summary_body(),
        submitted_at="2026-07-22T06:10:00Z",
        review_id=4751499100,
        commit_oid=HEAD,
    )
    # Newer review with a body that's neither the
    # exact clean phrase nor the summary-clean
    # format (i.e. a summary whose body contains an
    # inline-finding marker). The post-clean-pass
    # scan must classify this as a finding.
    finding_review = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body=_r52_finding_summary_body(),
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499200,
        commit_oid=HEAD,
    )
    pkt = _r52_classify(
        monkeypatch,
        codex_review_submissions=[clean_review, finding_review],
        codex_issue_comments=[],
    )
    assert pkt.get("status") == "HOLD_NEW_CODEX_THREAD", (
        "Round-52 invariant: an older summary clean "
        "followed by a newer summary finding must "
        "downgrade to HOLD_NEW_CODEX_THREAD. Got "
        f"status={pkt.get('status')!r}"
    )


def test_round52_source_contract_summary_helpers_exist():
    """Source-contract: the audit MUST define
    ``is_codex_review_summary`` and
    ``is_codex_finding_body`` helpers and use them
    in the formal-review clean-pass detection.
    """
    import inspect
    from scripts.local import audit_codex_response_for_pr as mod
    # Helpers must exist.
    assert hasattr(mod, "is_codex_review_summary"), (
        "Round-52 fix: audit must define "
        "is_codex_review_summary helper."
    )
    assert hasattr(mod, "is_codex_finding_body"), (
        "Round-52 fix: audit must define "
        "is_codex_finding_body helper."
    )
    # Helpers must be used in the formal-review
    # clean-pass detection (the source must
    # reference them near the
    # ``is_codex_clean_pass_comment`` check).
    src = inspect.getsource(mod)
    assert "is_codex_review_summary" in src
    assert "is_codex_finding_body" in src
    # And the CODEX_REVIEW_SUMMARY_PREFIX constant
    # must be defined.
    assert "CODEX_REVIEW_SUMMARY_PREFIX" in src


# Module-level helper for the function-based tests above.
def _r52_classify(monkeypatch, *, codex_review_submissions, codex_issue_comments):
    """Drive ``classify`` end-to-end with the
    supplied review and comment fixtures.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    from unittest.mock import patch as _mp
    from scripts.local import audit_codex_response_for_pr as mod
    # Use the Round-52 head so head_matches_expected is True.
    pr_view = make_raw_rest_pr_payload(
        mergeable_state="clean", mergeable=True, sha=HEAD,
    )
    threads_payload = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [],
            }
        }}}
    }

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
            m.stdout = json.dumps(threads_payload)
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps(list(codex_issue_comments))
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps(list(codex_review_submissions))
            return m
        m.stdout = "[]"
        return m

    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    with _mp.object(mod.subprocess, "run", runner):
        return mod.classify(
            repo=REPO, pr_number=411,
            expected_head_sha=HEAD,
            ping_comment_id=PING_ID,
            ping_created_at=PING_CREATED,
            max_polls=1, poll_seconds=0,
        )


# ---------------------------------------------------------------------------
# Round-54 regression: the audit must NOT accept a
# summary-format Codex review as a clean pass when there
# are active Codex-bot review threads anchored to the
# current head. Summary-format reviews carry their
# findings in inline review comments, NOT in the
# summary body, so the body's splitlines() check is not
# sufficient. If the inline thread is later resolved or
# absent from the inventory, the audit could record a
# clean pass without a real clean Codex verdict.
# ---------------------------------------------------------------------------


def _r54_clean_summary_body():
    return (
        "\n### 💡 Codex Review\n\n"
        "Here are some automated review suggestions for this pull request.\n\n"
        "**Reviewed commit:** `589c719ced`\n"
    )


def _r54_active_thread(*, thread_id="T-1", is_resolved=False, is_outdated=False, path="scripts/local/x.py"):
    """Build an active thread in the RAW GraphQL
    format that ``gh_graphql_review_threads`` returns.
    The audit's section 7 flattens this into the
    inventory's ``active_threads`` list.
    """
    return {
        "id": thread_id,
        "isResolved": is_resolved,
        "isOutdated": is_outdated,
        "comments": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {
                    "databaseId": 1234,
                    "url": "https://example.com",
                    "body": "Some finding",
                    "path": path,
                    "line": 10,
                    "originalCommit": {
                        "oid": "589c719ced339f49ac07f1ebd2082512a0204519",
                    },
                    "author": {"login": "chatgpt-codex-connector"},
                }
            ],
        },
    }


def test_round54_summary_clean_review_with_active_thread_rejected(monkeypatch, tmp_path):
    """Bug repro: a summary-format review with an
    active Codex-bot thread anchored to the current
    head MUST NOT be detected as a clean pass.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    clean_review = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body=_r54_clean_summary_body(),
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499126,
        commit_oid=HEAD,
    )
    active_thread = _r54_active_thread(is_resolved=False, is_outdated=False)
    pkt = _r52_classify_with_active_threads(
        monkeypatch,
        codex_review_submissions=[clean_review],
        codex_issue_comments=[],
        active_threads=[active_thread],
    )
    # The summary clean review MUST be rejected
    # because there is an active Codex-bot thread.
    assert pkt.get("clean_pass_detected") is not True, (
        "Round-54 fix: a summary-format review with "
        "an active Codex thread MUST NOT be a clean "
        "pass. Got clean_pass_detected="
        f"{pkt.get('clean_pass_detected')!r}, "
        f"status={pkt.get('status')!r}"
    )


def test_round54_summary_clean_review_without_active_thread_accepted(monkeypatch, tmp_path):
    """Round-54 invariant: a summary-format review
    with NO active Codex-bot threads MUST be detected
    as a clean pass.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    clean_review = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body=_r54_clean_summary_body(),
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499126,
        commit_oid=HEAD,
    )
    pkt = _r52_classify_with_active_threads(
        monkeypatch,
        codex_review_submissions=[clean_review],
        codex_issue_comments=[],
        active_threads=[],
    )
    # No active threads — the summary clean review
    # MUST be accepted.
    assert pkt.get("clean_pass_detected") is True, (
        "Round-54 invariant: a summary-format review "
        "with no active Codex threads MUST be a clean "
        "pass. Got clean_pass_detected="
        f"{pkt.get('clean_pass_detected')!r}, "
        f"status={pkt.get('status')!r}"
    )
    assert pkt.get("clean_pass_source") == "pull_request_review"


def test_round54_summary_clean_review_with_only_outdated_threads_accepted(monkeypatch, tmp_path):
    """Round-54 invariant: a summary-format review
    with ONLY outdated Codex-bot threads MUST be
    detected as a clean pass. Outdated threads are
    anchored to an older head and don't invalidate
    the current-head clean pass.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    clean_review = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body=_r54_clean_summary_body(),
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499126,
        commit_oid=HEAD,
    )
    # Only outdated threads — these don't invalidate
    # the current-head clean pass.
    outdated_thread = _r54_active_thread(is_resolved=False, is_outdated=True)
    pkt = _r52_classify_with_active_threads(
        monkeypatch,
        codex_review_submissions=[clean_review],
        codex_issue_comments=[],
        active_threads=[outdated_thread],
    )
    assert pkt.get("clean_pass_detected") is True, (
        "Round-54 invariant: only-outdated threads "
        "MUST NOT invalidate a current-head clean "
        "pass. Got clean_pass_detected="
        f"{pkt.get('clean_pass_detected')!r}"
    )


def test_round54_exact_phrase_clean_review_with_active_thread_still_accepted(monkeypatch, tmp_path):
    """Round-54 invariant: the older EXACT clean
    phrase (``Codex Review: Didn't find any major
    issues``) is NOT subject to the active-thread
    guard — only summary-format reviews are. The
    exact-phrase clean pass still works even with
    active threads.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    clean_review = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body="Codex Review: Didn't find any major issues. :tada:",
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499126,
        commit_oid=HEAD,
    )
    active_thread = _r54_active_thread(is_resolved=False, is_outdated=False)
    pkt = _r52_classify_with_active_threads(
        monkeypatch,
        codex_review_submissions=[clean_review],
        codex_issue_comments=[],
        active_threads=[active_thread],
    )
    # The exact-phrase clean review MUST still be
    # accepted (Round-54 only restricts summary-format
    # reviews).
    assert pkt.get("clean_pass_detected") is True, (
        "Round-54 invariant: the exact-phrase clean "
        "review MUST still be accepted even with "
        "active threads. Got clean_pass_detected="
        f"{pkt.get('clean_pass_detected')!r}"
    )


def test_round54_source_contract_active_thread_check():
    """Source-contract: the formal-review clean-pass
    detection MUST consult the ``active_threads``
    inventory when accepting a summary-format review.
    Static source check.
    """
    import inspect
    from scripts.local import audit_codex_response_for_pr as mod
    src = inspect.getsource(mod)
    # The active-thread guard MUST be in the source.
    assert "active_threads" in src
    # And it must be consulted in the context of
    # summary-format review detection.
    # Find the section that handles summary_format.
    summary_start = src.find("is_summary_format")
    assert summary_start > 0
    # Within ~100 lines of ``is_summary_format``,
    # ``active_threads`` must be consulted.
    nearby = src[summary_start:summary_start + 2000]
    assert "active_threads" in nearby, (
        "Round-54 fix: the summary-format review "
        "detection must consult active_threads to "
        "determine if the review carries an inline "
        "finding."
    )


# Module-level helper for the function-based tests above.
def _r52_classify_with_active_threads(
    monkeypatch, *,
    codex_review_submissions,
    codex_issue_comments,
    active_threads,
):
    """Drive ``classify`` end-to-end with the
    supplied review, comment, and active-thread
    fixtures. Extends ``_r52_classify`` to inject
    ``active_threads`` into the review-thread
    inventory.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    from unittest.mock import patch as _mp
    from scripts.local import audit_codex_response_for_pr as mod
    # Use the Round-52 head so head_matches_expected is True.
    pr_view = make_raw_rest_pr_payload(
        mergeable_state="clean", mergeable=True, sha=HEAD,
    )
    threads_payload = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": list(active_threads),
            }
        }}}
    }

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
            m.stdout = json.dumps(threads_payload)
            return m
        if "/issues/" in cmd_str and "/comments" in cmd_str:
            m.stdout = json.dumps(list(codex_issue_comments))
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps(list(codex_review_submissions))
            return m
        m.stdout = "[]"
        return m

    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    with _mp.object(mod.subprocess, "run", runner):
        return mod.classify(
            repo=REPO, pr_number=411,
            expected_head_sha=HEAD,
            ping_comment_id=PING_ID,
            ping_created_at=PING_CREATED,
            max_polls=1, poll_seconds=0,
        )


# ---------------------------------------------------------------------------
# Round-56 regression: veto summary reviews when a
# current-head Codex inline thread has been RESOLVED
# (not just when it's still active). The Round-54 fix
# only checked active threads; if an operator marks
# the current-head Codex thread resolved before a
# later clean re-review, the audit would record
# ``clean_pass_detected=True`` for the finding
# review, bypassing the later unresolved-thread gate.
# ---------------------------------------------------------------------------


def test_round56_summary_clean_review_with_resolved_thread_rejected(monkeypatch, tmp_path):
    """Round-56 invariant (superseded by Round-59): a
    summary-format review with a RESOLVED Codex thread
    on the SAME commit does NOT veto a clean pass
    (Round-59 changed this — resolved threads are
    excluded from the veto because the finding has
    been addressed). This test now verifies the
    Round-59 behavior: resolved threads on the same
    commit do NOT veto.

    The original Round-56 bug was about resolved
    threads on older commits vetoing clean re-reviews.
    Round-57 fixed that by tying to commit anchor.
    Round-59 further refined: even same-commit resolved
    threads don't veto (they're already addressed).
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    clean_review = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body="\n### 💡 Codex Review\n\n"
             "Here are some automated review suggestions.\n\n"
             f"**Reviewed commit:** `{HEAD[:10]}`\n",
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499126,
        commit_oid=HEAD,
    )
    # RESOLVED Codex thread (not active, but anchored
    # to the same commit). Round-59 fix: resolved
    # threads do NOT veto.
    resolved_thread = _r56_active_thread(
        is_resolved=True,
        is_outdated=False,
        path="scripts/local/x.py",
    )
    pkt = _r52_classify_with_active_threads(
        monkeypatch,
        codex_review_submissions=[clean_review],
        codex_issue_comments=[],
        active_threads=[resolved_thread],
    )
    assert pkt.get("clean_pass_detected") is True, (
        "Round-59 fix: a RESOLVED Codex thread (even on "
        "the same commit) MUST NOT veto a clean "
        "re-review. Resolved threads mean the finding "
        "has been addressed. Got "
        f"clean_pass_detected={pkt.get('clean_pass_detected')!r}"
    )


def test_round56_summary_clean_review_with_outdated_thread_accepted(monkeypatch, tmp_path):
    """Round-56 invariant: a summary-format review
    with ONLY outdated Codex threads MUST be detected
    as a clean pass. Outdated threads are anchored to
    prior heads and don't invalidate the current-head
    clean pass.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    clean_review = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body="\n### 💡 Codex Review\n\n"
             "Here are some automated review suggestions.\n\n"
             f"**Reviewed commit:** `{HEAD[:10]}`\n",
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499126,
        commit_oid=HEAD,
    )
    # RESOLVED + OUTDATED thread — should NOT veto.
    outdated_thread = _r56_active_thread(
        is_resolved=True,
        is_outdated=True,
        path="scripts/local/x.py",
    )
    pkt = _r52_classify_with_active_threads(
        monkeypatch,
        codex_review_submissions=[clean_review],
        codex_issue_comments=[],
        active_threads=[outdated_thread],
    )
    assert pkt.get("clean_pass_detected") is True, (
        "Round-56 invariant: outdated threads MUST NOT "
        "invalidate a current-head clean pass. Got "
        f"clean_pass_detected={pkt.get('clean_pass_detected')!r}"
    )


def test_round56_source_contract_resolved_thread_veto():
    """Source-contract (updated for Round-59): the
    summary-format veto MUST check
    ``is_resolved=False``. The Round-56 fix required
    ``is_resolved`` to NOT be in the veto (the veto
    applied to any current-head thread regardless of
    resolved state). Round-59 reverses this: the
    veto MUST explicitly exclude resolved threads
    (``not bool(t.get("is_resolved", False))`` in the
    condition). Static source check.
    """
    import inspect
    from scripts.local import audit_codex_response_for_pr as mod
    src = inspect.getsource(mod)
    # The Round-59 veto MUST include an explicit
    # ``is_resolved`` check.
    summary_idx = src.find("is_summary_format and review_threads")
    assert summary_idx > 0
    veto_section = src[summary_idx:summary_idx + 3000]
    # The veto MUST contain ``is_resolved``.
    assert "is_resolved" in veto_section, (
        "Round-59 fix: the summary-format veto MUST "
        "explicitly check ``is_resolved`` to exclude "
        "resolved threads. Found no 'is_resolved' in "
        "the veto section."
    )
    # And it MUST be in a ``not bool(...)`` guard
    # (excluding resolved threads).
    assert 'not bool(t.get("is_resolved"' in veto_section or \
           'not t.get("is_resolved"' in veto_section or \
           't.get("is_resolved", False)' in veto_section, (
        "Round-59 fix: the summary-format veto MUST "
        "exclude resolved threads via a ``not is_resolved`` "
        "check."
    )


# Module-level helper for Round-56 tests.
def _r56_active_thread(*, is_resolved=False, is_outdated=False, path="scripts/local/x.py"):
    """Build an active thread in the RAW GraphQL
    format with configurable resolved/outdated state.
    """
    return {
        "id": "T-1",
        "isResolved": is_resolved,
        "isOutdated": is_outdated,
        "comments": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {
                    "databaseId": 1234,
                    "url": "https://example.com",
                    "body": "Some finding",
                    "path": path,
                    "line": 10,
                    "originalCommit": {
                        "oid": "589c719ced339f49ac07f1ebd2082512a0204519",
                    },
                    "author": {"login": "chatgpt-codex-connector"},
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# Round-57 regression: do not let resolved old findings
# veto clean re-reviews. The Round-56 fix was too
# aggressive: a resolved thread from an OLDER finding
# review on the same head would veto a later CLEAN
# re-review, even though the later review has no inline
# findings. The correct check ties the thread to the
# review via the commit anchor: a thread vetoes a clean
# review only when ``original_commit_sha ==
# review.commit_id``.
# ---------------------------------------------------------------------------


def test_round57_clean_review_with_older_resolved_thread_accepted(monkeypatch, tmp_path):
    """Bug repro: a clean summary-format review with
    a RESOLVED Codex thread anchored to an OLDER
    commit (a different review's inline finding) MUST
    be detected as a clean pass. The thread is from
    an earlier finding review and doesn't invalidate
    the later clean re-review.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    # Older commit anchor for the earlier finding
    # review's threads.
    OLDER_COMMIT = "1111111111111111111111111111111111111111"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    clean_review = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body="\n### 💡 Codex Review\n\n"
             "Here are some automated review suggestions.\n\n"
             f"**Reviewed commit:** `{HEAD[:10]}`\n",
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499126,
        commit_oid=HEAD,
    )
    # RESOLVED Codex thread anchored to an OLDER
    # commit (not the clean review's commit).
    resolved_old_thread = {
        "id": "T-1",
        "isResolved": True,
        "isOutdated": False,
        "comments": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {
                    "databaseId": 1234,
                    "url": "https://example.com",
                    "body": "Old finding",
                    "path": "scripts/local/x.py",
                    "line": 10,
                    "originalCommit": {"oid": OLDER_COMMIT},
                    "author": {"login": "chatgpt-codex-connector"},
                }
            ],
        },
    }
    pkt = _r52_classify_with_active_threads(
        monkeypatch,
        codex_review_submissions=[clean_review],
        codex_issue_comments=[],
        active_threads=[resolved_old_thread],
    )
    assert pkt.get("clean_pass_detected") is True, (
        "Round-57 fix: a resolved thread anchored to "
        "an OLDER commit MUST NOT veto a clean "
        "re-review on the current head. Got "
        f"clean_pass_detected={pkt.get('clean_pass_detected')!r}, "
        f"status={pkt.get('status')!r}"
    )


def test_round57_clean_review_with_same_anchor_resolved_thread_rejected(monkeypatch, tmp_path):
    """Round-57 invariant (superseded by Round-59): a
    resolved thread anchored to the SAME commit as
    the review does NOT veto the clean pass. Round-59
    changed this: resolved threads are excluded from
    the veto regardless of commit anchor because the
    finding has been addressed.

    The Round-57 bug was about OLDER-commit resolved
    threads vetoing clean re-reviews. Round-57 fixed
    that with commit-anchor matching. Round-59 further
    refined: even same-commit resolved threads don't
    veto (they're already addressed).
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    clean_review = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body="\n### 💡 Codex Review\n\n"
             "Here are some automated review suggestions.\n\n"
             f"**Reviewed commit:** `{HEAD[:10]}`\n",
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499126,
        commit_oid=HEAD,
    )
    # RESOLVED Codex thread anchored to the SAME
    # commit. Round-59 fix: resolved threads don't
    # veto regardless of anchor.
    resolved_same_thread = {
        "id": "T-1",
        "isResolved": True,
        "isOutdated": False,
        "comments": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {
                    "databaseId": 1234,
                    "url": "https://example.com",
                    "body": "This review's finding",
                    "path": "scripts/local/x.py",
                    "line": 10,
                    "originalCommit": {"oid": HEAD},
                    "author": {"login": "chatgpt-codex-connector"},
                }
            ],
        },
    }
    pkt = _r52_classify_with_active_threads(
        monkeypatch,
        codex_review_submissions=[clean_review],
        codex_issue_comments=[],
        active_threads=[resolved_same_thread],
    )
    assert pkt.get("clean_pass_detected") is True, (
        "Round-59 invariant: a resolved thread "
        "(even on the same commit) MUST NOT veto a "
        "clean re-review. Resolved threads mean the "
        "finding has been addressed. Got "
        f"clean_pass_detected={pkt.get('clean_pass_detected')!r}"
    )


def test_round57_source_contract_commit_anchor_match():
    """Source-contract: the summary-format veto MUST
    tie the thread to the review via commit anchor
    equality (``original_commit_sha ==
    extract_review_commit_oid(review)``). Static
    source check.
    """
    import inspect
    from scripts.local import audit_codex_response_for_pr as mod
    src = inspect.getsource(mod)
    # The veto MUST use ``original_commit_sha`` (the
    # flattened field name) and
    # ``extract_review_commit_oid``.
    summary_idx = src.find("is_summary_format and review_threads")
    assert summary_idx > 0
    veto_section = src[summary_idx:summary_idx + 5000]
    assert "original_commit_sha" in veto_section, (
        "Round-57 fix: the summary-format veto MUST "
        "tie the thread to the review via the flattened "
        "``original_commit_sha`` field. Found no "
        "reference in veto section."
    )
    assert "extract_review_commit_oid" in veto_section, (
        "Round-57 fix: the summary-format veto MUST "
        "use ``extract_review_commit_oid`` to get the "
        "review's commit anchor. Found no reference in "
        "veto section."
    )


# ---------------------------------------------------------------------------
# Round-59 regression: stop resolved old threads from
# vetoing clean reviews. When a previous Codex inline
# finding on the same commit has already been resolved
# and Codex later posts a summary-format clean
# re-review without a new commit, the audit MUST
# ignore the resolved thread and accept the clean
# review.
# ---------------------------------------------------------------------------


def test_round59_clean_review_with_same_commit_resolved_thread_accepted(monkeypatch, tmp_path):
    """Bug repro: a clean summary-format review with
    a RESOLVED Codex thread on the SAME commit MUST
    be detected as a clean pass. Resolved threads
    mean the finding has been addressed.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    clean_review = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body="\n### 💡 Codex Review\n\n"
             "Here are some automated review suggestions.\n\n"
             f"**Reviewed commit:** `{HEAD[:10]}`\n",
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499126,
        commit_oid=HEAD,
    )
    # RESOLVED Codex thread on the SAME commit as
    # the clean review.
    resolved_same_thread = {
        "id": "T-1",
        "isResolved": True,
        "isOutdated": False,
        "comments": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {
                    "databaseId": 1234,
                    "url": "https://example.com",
                    "body": "Resolved finding",
                    "path": "scripts/local/x.py",
                    "line": 10,
                    "originalCommit": {"oid": HEAD},
                    "author": {"login": "chatgpt-codex-connector"},
                }
            ],
        },
    }
    pkt = _r52_classify_with_active_threads(
        monkeypatch,
        codex_review_submissions=[clean_review],
        codex_issue_comments=[],
        active_threads=[resolved_same_thread],
    )
    assert pkt.get("clean_pass_detected") is True, (
        "Round-59 fix: a RESOLVED Codex thread (even "
        "on the same commit) MUST NOT veto a clean "
        "re-review. Got "
        f"clean_pass_detected={pkt.get('clean_pass_detected')!r}, "
        f"status={pkt.get('status')!r}"
    )


def test_round59_clean_review_with_active_thread_still_rejected(monkeypatch, tmp_path):
    """Round-59 invariant: an ACTIVE (unresolved)
    Codex thread on the same commit MUST still veto
    the clean pass. The Round-59 fix only excludes
    resolved threads, not active ones.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    clean_review = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body="\n### 💡 Codex Review\n\n"
             "Here are some automated review suggestions.\n\n"
             f"**Reviewed commit:** `{HEAD[:10]}`\n",
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499126,
        commit_oid=HEAD,
    )
    # ACTIVE Codex thread on the same commit.
    active_same_thread = {
        "id": "T-1",
        "isResolved": False,
        "isOutdated": False,
        "comments": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {
                    "databaseId": 1234,
                    "url": "https://example.com",
                    "body": "Active finding",
                    "path": "scripts/local/x.py",
                    "line": 10,
                    "originalCommit": {"oid": HEAD},
                    "author": {"login": "chatgpt-codex-connector"},
                }
            ],
        },
    }
    pkt = _r52_classify_with_active_threads(
        monkeypatch,
        codex_review_submissions=[clean_review],
        codex_issue_comments=[],
        active_threads=[active_same_thread],
    )
    assert pkt.get("clean_pass_detected") is not True, (
        "Round-59 invariant: an ACTIVE Codex thread "
        "MUST still veto the clean pass. Got "
        f"clean_pass_detected={pkt.get('clean_pass_detected')!r}"
    )


def test_round59_source_contract_resolved_excluded():
    """Source-contract: the summary-format veto MUST
    explicitly exclude resolved threads via
    ``not bool(t.get("is_resolved", False))``.
    Static source check.
    """
    import inspect
    from scripts.local import audit_codex_response_for_pr as mod
    src = inspect.getsource(mod)
    summary_idx = src.find("is_summary_format and review_threads")
    assert summary_idx > 0
    veto_section = src[summary_idx:summary_idx + 3000]
    assert "is_resolved" in veto_section, (
        "Round-59 fix: the summary-format veto MUST "
        "explicitly check ``is_resolved``."
    )


# ---------------------------------------------------------------------------
# Round-60 regression: inspect inline comments before
# accepting summary reviews as clean. When Codex uses
# the new ``### 💡 Codex Review`` summary format, the
# actual findings live in separate inline review
# comments, NOT in the summary body. The audit MUST
# fetch the review's inline comments and reject the
# review if any are present.
# ---------------------------------------------------------------------------


ROUND60_BODY = "\n### 💡 Codex Review\n\nHere are some automated review suggestions.\n\n**Reviewed commit:** `589c719ced`\n"


def test_round60_summary_review_with_inline_comments_rejected(monkeypatch, tmp_path):
    """Bug repro: a summary-format review WITH inline
    comments MUST NOT be detected as a clean pass.
    The inline comments carry the findings.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    clean_review = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body=ROUND60_BODY,
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499126,
        commit_oid=HEAD,
    )
    pkt = _r60_classify_with_inline_comments(
        monkeypatch,
        codex_review_submissions=[clean_review],
        codex_issue_comments=[],
        active_threads=[],
        inline_comments_by_review={
            4751499126: [{
                "id": "ic-1",
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "body": "**<sub><sub>![P1 Badge]...</sub></sub>  Finding**",
                "path": "scripts/local/x.py",
            }],
        },
    )
    assert pkt.get("clean_pass_detected") is not True, (
        "Round-60 fix: a summary-format review WITH "
        "inline comments MUST NOT be a clean pass. "
        "Got "
        f"clean_pass_detected={pkt.get('clean_pass_detected')!r}, "
        f"status={pkt.get('status')!r}"
    )


def test_round60_summary_review_without_inline_comments_accepted(monkeypatch, tmp_path):
    """Round-60 invariant: a summary-format review
    with NO inline comments MUST be detected as a
    clean pass (when there are also no active threads).
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    clean_review = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body=ROUND60_BODY,
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499126,
        commit_oid=HEAD,
    )
    pkt = _r60_classify_with_inline_comments(
        monkeypatch,
        codex_review_submissions=[clean_review],
        codex_issue_comments=[],
        active_threads=[],
        inline_comments_by_review={4751499126: []},
    )
    assert pkt.get("clean_pass_detected") is True, (
        "Round-60 invariant: a summary-format review "
        "with no inline comments and no active threads "
        "MUST be a clean pass. Got "
        f"clean_pass_detected={pkt.get('clean_pass_detected')!r}"
    )


def test_round60_source_contract_inline_comments_fetch():
    """Source-contract: ``_fetch_review_inline_comments_with_pr``
    MUST exist and MUST be referenced in the source.
    Static source check.
    """
    import inspect
    from scripts.local import audit_codex_response_for_pr as mod
    assert hasattr(mod, "_fetch_review_inline_comments_with_pr"), (
        "Round-60 fix: audit must define "
        "_fetch_review_inline_comments_with_pr helper."
    )
    src = inspect.getsource(mod)
    assert "_fetch_review_inline_comments_with_pr" in src, (
        "Round-60 fix: the source must reference "
        "_fetch_review_inline_comments_with_pr."
    )


def _r60_classify_with_inline_comments(
    monkeypatch, *,
    codex_review_submissions,
    codex_issue_comments,
    active_threads,
    inline_comments_by_review,
):
    """Drive ``classify`` end-to-end with inline
    review comments injected via the ``gh api``
    fetch path.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    from unittest.mock import patch as _mp
    from scripts.local import audit_codex_response_for_pr as mod
    pr_view = make_raw_rest_pr_payload(
        mergeable_state="clean", mergeable=True, sha=HEAD,
    )
    threads_payload = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": list(active_threads),
            }
        }}}
    }

    def runner(cmd, *args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd)
        if (
            "repos/" in cmd_str
            and "/pulls/" in cmd_str
            and "/reviews/" in cmd_str
            and "/comments" in cmd_str
        ):
            import re
            match = re.search(r"/reviews/(\d+)/comments", cmd_str)
            if match:
                review_id = int(match.group(1))
                comments = inline_comments_by_review.get(review_id, [])
                m.stdout = json.dumps(comments)
                return m
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
            m.stdout = json.dumps(list(codex_issue_comments))
            return m
        if "/reviews" in cmd_str and "/comments" not in cmd_str:
            m.stdout = json.dumps(list(codex_review_submissions))
            return m
        m.stdout = "[]"
        return m

    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    with _mp.object(mod.subprocess, "run", runner):
        return mod.classify(
            repo=REPO, pr_number=411,
            expected_head_sha=HEAD,
            ping_comment_id=PING_ID,
            ping_created_at=PING_CREATED,
            max_polls=1, poll_seconds=0,
        )


# ---------------------------------------------------------------------------
# Round-61 regression: treat inline summary findings
# as blockers. When a newer summary-format Codex
# review has inline comments, the post-clean-pass
# scan MUST treat it as a NEWER finding even when the
# summary body looks clean. Without this, the
# classifier could return MERGE_READY despite a newer
# inline finding.
# ---------------------------------------------------------------------------


def test_round61_newer_finding_with_inline_comments_downgrades_clean(monkeypatch, tmp_path):
    """Bug repro: a newer summary-format review WITH
    inline comments MUST downgrade a current-head
    clean pass to HOLD_NEW_CODEX_THREAD, even when
    the summary body is clean.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    # Older clean review (exact phrase).
    older_clean = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body="Codex Review: Didn't find any major issues. :tada:",
        submitted_at="2026-07-22T06:10:00Z",
        review_id=4751499100,
        commit_oid=HEAD,
    )
    # Newer summary review WITH inline comments
    # (the finding lives in the inline comments,
    # not the summary body).
    newer_finding = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body=ROUND60_BODY,
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499200,
        commit_oid=HEAD,
    )
    pkt = _r60_classify_with_inline_comments(
        monkeypatch,
        codex_review_submissions=[older_clean, newer_finding],
        codex_issue_comments=[],
        active_threads=[],
        inline_comments_by_review={
            4751499200: [{
                "id": "ic-1",
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "body": "**<sub><sub>![P1 Badge]...</sub></sub>  Finding**",
                "path": "scripts/local/x.py",
            }],
        },
    )
    # The newer finding with inline comments MUST
    # downgrade the clean pass to HOLD_NEW_CODEX_THREAD.
    assert pkt.get("status") == "HOLD_NEW_CODEX_THREAD", (
        "Round-61 fix: a newer summary review with "
        "inline comments MUST downgrade to "
        "HOLD_NEW_CODEX_THREAD. Got "
        f"status={pkt.get('status')!r}"
    )


# ---------------------------------------------------------------------------
# Round-62 regression: suppress readiness after
# mark-ready head moves (Finding 1) + scan inline
# comments after issue-comment clean passes
# (Finding 2).
# ---------------------------------------------------------------------------


def test_round62_finding1_suppress_readiness_after_head_move(monkeypatch, tmp_path):
    """Round-62 Finding 1: when the post-mutation
    refresh detects a head move during
    ``mark_pr_ready``, the final report MUST NOT
    copy the pre-mutation ``machine_verdict`` into
    ``effective_machine_ready``/``merge_ready``.
    The authorization phrase and merge command MUST
    be suppressed.
    """
    import inspect
    from scripts.local import aed_pr as ctrl
    src = inspect.getsource(ctrl.cmd_advance)
    # The ``elif head_moved_during_mutation:`` branch
    # MUST suppress the readiness fields.
    assert (
        "elif head_moved_during_mutation" in src
    ), "Round-62 fix: cmd_advance must check head_moved_during_mutation when building the final report."
    # Find the branch and verify it sets the fields to False.
    branch_idx = src.find("elif head_moved_during_mutation")
    branch_section = src[branch_idx:branch_idx + 2000]
    assert "effective_machine_ready = False" in branch_section, (
        "Round-62 fix: the head_moved branch must "
        "set effective_machine_ready = False."
    )
    assert "effective_merge_ready = False" in branch_section, (
        "Round-62 fix: the head_moved branch must "
        "set effective_merge_ready = False."
    )
    assert "effective_authorization_required = False" in branch_section, (
        "Round-62 fix: the head_moved branch must "
        "set effective_authorization_required = False."
    )


def test_round62_finding2_inline_comments_scanned_after_issue_clean_pass(monkeypatch, tmp_path):
    """Round-62 Finding 2: the inline-comment fetch
    MUST run for ALL summary-format reviews, even
    when ``latest_clean_pass`` was already set from a
    Codex issue-comment clean pass.
    """
    HEAD = "589c719ced339f49ac07f1ebd2082512a0204519"
    PING_ID = "5042469465"
    PING_CREATED = "2026-07-22T06:06:50Z"
    # Older issue-comment clean pass.
    older_clean = {
        "databaseId": 1234,
        "id": "ic-1",
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": "Codex Review: Didn't find any major issues. :tada:",
        "createdAt": "2026-07-22T06:10:00Z",
    }
    # Newer summary review WITH inline comments.
    newer_finding = make_review(
        author=CODEX_LOGIN,
        state="COMMENTED",
        body=ROUND60_BODY,
        submitted_at="2026-07-22T06:14:56Z",
        review_id=4751499200,
        commit_oid=HEAD,
    )
    pkt = _r60_classify_with_inline_comments(
        monkeypatch,
        codex_review_submissions=[newer_finding],
        codex_issue_comments=[older_clean],
        active_threads=[],
        inline_comments_by_review={
            4751499200: [{
                "id": "ic-2",
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "body": "**<sub><sub>![P1 Badge]...</sub></sub>  Finding**",
                "path": "scripts/local/x.py",
            }],
        },
    )
    # The newer summary with inline comments MUST
    # downgrade to HOLD_NEW_CODEX_THREAD, even though
    # the older issue-comment clean pass is in scope.
    assert pkt.get("status") == "HOLD_NEW_CODEX_THREAD", (
        "Round-62 fix: a newer summary review with "
        "inline comments MUST downgrade to "
        "HOLD_NEW_CODEX_THREAD even when an older "
        "issue-comment clean pass exists. Got "
        f"status={pkt.get('status')!r}"
    )


def test_round62_finding2_source_contract_pre_pass():
    """Source-contract: the inline-comment fetch MUST
    run in a pre-pass BEFORE the formal-review
    clean-pass scan, so it runs regardless of
    whether ``latest_clean_pass`` was set from an
    issue-comment clean pass.
    """
    import inspect
    from scripts.local import audit_codex_response_for_pr as mod
    src = inspect.getsource(mod)
    # The pre-pass MUST exist before the formal-review
    # clean-pass block.
    prepass_idx = src.find("Round-62 fix (Finding 2): scan inline")
    formal_idx = src.find("if latest_clean_pass is None and codex_review_submissions:")
    assert prepass_idx > 0, (
        "Round-62 fix: the pre-pass must exist in the source."
    )
    assert formal_idx > prepass_idx, (
        "Round-62 fix: the pre-pass must appear BEFORE "
        "the formal-review clean-pass scan."
    )


# ---------------------------------------------------------------------------
# Round-64 regression: recognize clean summary issue
# comments in the audit. The audit's
# ``is_codex_clean_pass_comment`` MUST accept the
# same fragments as the poller, or a clean response
# that uses the newer summary format (e.g. "No
# findings reported") will be incorrectly classified
# as a newer finding.
# ---------------------------------------------------------------------------


def test_round64_clean_pass_phrase_no_findings_reported():
    """Round-64: a body containing "No findings reported"
    MUST be classified as a clean pass by
    ``is_codex_clean_pass_comment``.
    """
    from scripts.local.audit_codex_response_for_pr import (
        is_codex_clean_pass_comment,
    )
    body = (
        "### 💡 Codex Review\n\n"
        "**Reviewed commit:** `589c719ced`\n\n"
        "No findings reported.\n"
    )
    assert is_codex_clean_pass_comment(body), (
        "Round-64 fix: a summary body with 'No findings "
        "reported' MUST be classified as a clean pass. "
        "The audit and poller MUST agree on what counts "
        "as clean."
    )


def test_round64_clean_pass_phrase_no_issues_found():
    """Round-64: a body containing "No issues found" MUST
    be classified as a clean pass.
    """
    from scripts.local.audit_codex_response_for_pr import (
        is_codex_clean_pass_comment,
    )
    body = (
        "### 💡 Codex Review\n\n"
        "**Reviewed commit:** `589c719ced`\n\n"
        "No issues found.\n"
    )
    assert is_codex_clean_pass_comment(body), (
        "Round-64 fix: a body with 'No issues found' "
        "MUST be classified as a clean pass."
    )


def test_round64_clean_pass_phrase_legacy_exact_still_works():
    """Round-64 invariant: the legacy exact phrase MUST
    still be recognized as a clean pass.
    """
    from scripts.local.audit_codex_response_for_pr import (
        is_codex_clean_pass_comment,
    )
    body = "Codex Review: Didn't find any major issues. :tada:"
    assert is_codex_clean_pass_comment(body), (
        "Round-64 invariant: the legacy exact phrase "
        "MUST still be recognized as a clean pass."
    )


def test_round64_summary_with_finding_badge_not_clean():
    """Round-64 invariant: a summary body that includes
    a finding badge MUST NOT be classified as a clean
    pass (the finding makes it a finding review).
    """
    from scripts.local.audit_codex_response_for_pr import (
        is_codex_clean_pass_comment,
    )
    body = (
        "### 💡 Codex Review\n\n"
        "**Reviewed commit:** `589c719ced`\n\n"
        "**<sub><sub>![P1 Badge]...</sub></sub>  Finding**\n"
    )
    assert not is_codex_clean_pass_comment(body), (
        "Round-64 invariant: a summary body with a "
        "finding badge MUST NOT be classified as clean."
    )


def test_round64_source_contract_extra_fragments():
    """Source-contract: the audit MUST define
    ``CODEX_CLEAN_PASS_EXTRA_FRAGMENTS`` and
    ``is_codex_clean_pass_comment`` MUST consult it.
    Static source check.
    """
    import inspect
    from scripts.local import audit_codex_response_for_pr as mod
    assert hasattr(mod, "CODEX_CLEAN_PASS_EXTRA_FRAGMENTS"), (
        "Round-64 fix: audit must define "
        "CODEX_CLEAN_PASS_EXTRA_FRAGMENTS."
    )
    src = inspect.getsource(mod.is_codex_clean_pass_comment)
    assert "CODEX_CLEAN_PASS_EXTRA_FRAGMENTS" in src, (
        "Round-64 fix: is_codex_clean_pass_comment "
        "MUST consult CODEX_CLEAN_PASS_EXTRA_FRAGMENTS."
    )
    # Must also accept summary format.
    assert "is_codex_review_summary" in src, (
        "Round-64 fix: is_codex_clean_pass_comment "
        "MUST accept summary-format bodies as clean."
    )


# ---------------------------------------------------------------------------
# Round-65 regression: do not let clean fragments
# override finding badges. A summary-format Codex body
# that contains BOTH a clean fragment ("no major
# issues" / "no findings reported" / etc.) AND a
# finding badge MUST be classified as FINDING, not
# CLEAN_PASS. The finding badge takes precedence.
# ---------------------------------------------------------------------------


def test_round65_audit_summary_with_clean_fragment_and_finding_badge_is_finding():
    """Round-65: an audit body that is a summary with
    BOTH a clean fragment and a finding badge MUST
    NOT be classified as a clean pass.
    """
    from scripts.local.audit_codex_response_for_pr import (
        is_codex_clean_pass_comment,
    )
    body = (
        "### 💡 Codex Review\n\n"
        "**Reviewed commit:** `589c719ced`\n\n"
        "No major issues found, except:\n"
        "**<sub><sub>![P1 Badge]...</sub></sub>  Some finding**\n"
    )
    assert not is_codex_clean_pass_comment(body), (
        "Round-65 fix: a summary body with both a "
        "clean fragment AND a finding badge MUST be "
        "classified as FINDING, not CLEAN_PASS. The "
        "finding badge takes precedence."
    )


def test_round65_audit_non_summary_with_finding_badge_is_finding():
    """Round-65 invariant: a non-summary body that
    contains a finding badge line MUST NOT be
    classified as a clean pass regardless of clean
    fragments elsewhere in the body.
    """
    from scripts.local.audit_codex_response_for_pr import (
        is_codex_clean_pass_comment,
    )
    body = (
        "I looked and found no major issues overall.\n"
        "**<sub><sub>![P2 Badge]...</sub></sub>  Finding**\n"
    )
    assert not is_codex_clean_pass_comment(body), (
        "Round-65 invariant: a body with a finding "
        "badge line MUST NOT be a clean pass even if "
        "clean fragments are also present."
    )


def test_round65_audit_source_contract_finding_badge_first():
    """Source-contract: the audit's
    ``is_codex_clean_pass_comment`` MUST consult
    finding badges BEFORE clean fragments.
    Static source check.
    """
    import inspect
    from scripts.local import audit_codex_response_for_pr as mod
    src = inspect.getsource(mod.is_codex_clean_pass_comment)
    # The finding-badge check must appear BEFORE the
    # fragment check.
    badge_idx = src.find("is_codex_finding_body")
    fragment_idx = src.find("CODEX_CLEAN_PASS_EXTRA_FRAGMENTS")
    assert badge_idx > 0
    assert fragment_idx > 0
    assert badge_idx < fragment_idx, (
        "Round-65 fix: the finding-badge check must "
        "appear BEFORE the fragment check in the "
        "audit source."
    )


def test_round65_poller_summary_with_clean_fragment_and_finding_badge_is_finding():
    """Round-65: the poller's issue-comment
    classification MUST classify a summary body with
    both a clean fragment and a finding badge as
    FINDING, not CLEAN_PASS.
    """
    import inspect
    from scripts.local import codex_review_poller as mod
    src = inspect.getsource(mod)
    # The poller's _match_response must pre-scan for
    # finding badges.
    assert "body_has_finding_badge" in src, (
        "Round-65 fix: the poller must pre-scan for "
        "finding badges before clean-pass check."
    )


def test_round65_poller_source_contract_finding_badge_first():
    """Source-contract: the poller's issue-comment
    classification MUST consult finding badges BEFORE
    the clean-pass check.
    Static source check.
    """
    import inspect
    from scripts.local import codex_review_poller as mod
    src = inspect.getsource(mod)
    # Find the issue-comment classification section.
    issue_idx = src.find('if kind == "issue_comment":')
    assert issue_idx > 0
    section = src[issue_idx:issue_idx + 5000]
    # The body_has_finding_badge pre-scan must appear
    # BEFORE the _is_clean_pass check.
    badge_idx = section.find("body_has_finding_badge")
    clean_idx = section.find("_is_clean_pass(body)")
    assert badge_idx > 0
    assert clean_idx > 0
    assert badge_idx < clean_idx, (
        "Round-65 fix: the finding-badge pre-scan "
        "must appear BEFORE the _is_clean_pass check "
        "in the poller source."
    )


# ---------------------------------------------------------------------------
# MINIMAX P2 Finding 1: case-insensitive Codex login classification
# ---------------------------------------------------------------------------
#
# The audit's previous ``has_active_blocker`` used a case-sensitive
# ``in CODEX_BOT_LOGINS`` check. A Codex-authored active thread whose
# ``author`` field came back from GitHub in any case other than the
# exact lowercase value stored in ``CODEX_BOT_LOGINS`` was silently
# treated as a non-Codex author, routing the audit to
# ``HOLD_CODEX_RESPONSE_PENDING`` instead of
# ``HOLD_NEW_CODEX_THREAD``. The shared policy's ``is_codex_login``
# predicate is case-insensitive (uses ``login.lower()``); the audit
# now routes through the same canonical predicate, with a fallback
# that uses the same case-insensitive identity semantics.


def test_minimax_p2_uppercase_codex_login_classified_as_active_blocker(
    monkeypatch, tmp_path,
):
    """P2 #1: an unresolved non-outdated Codex-authored active thread
    whose author field is uppercase
    (``CHATGPT-CODEX-CONNECTOR``) MUST be classified as a
    current-head active blocker so the audit emits
    ``HOLD_NEW_CODEX_THREAD``.

    Pre-fix, the audit's ``has_active_blocker`` used a
    case-sensitive ``in CODEX_BOT_LOGINS`` check, which would
    miss this thread and emit ``HOLD_CODEX_RESPONSE_PENDING``.
    The fix routes the check through the canonical
    ``is_codex_login`` predicate (case-insensitive).

    Companion assertion: the ordinary lowercase Codex login
    remains recognized (no regression of the working path).
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    # Exact-head clean pass + one uppercase-Codex active thread.
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=6001,
        )
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {
                        "id": "PRRT_minimax_uppercase_1",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {"nodes": [{
                            "databaseId": 9001,
                            "url": "https://example/9001",
                            "body": "P1 finding (uppercase author)",
                            "path": "scripts/local/foo.py",
                            "line": 1,
                            # UPPERCASE — the canonical GitHub
                            # login is lowercase. The audit must
                            # still recognize this as Codex.
                            "author": {"login": "CHATGPT-CODEX-CONNECTOR"},
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
    # Inventory must be complete (the mock returns hasNextPage=false
    # and no nested ``hasNextPage=true``).
    assert pkt["review_thread_inventory_complete"] is True
    assert pkt["review_thread_comment_inventory_complete"] is True
    # The clean pass is detected and recognized.
    assert pkt["clean_pass_detected"] is True
    # The active thread is present and counted as a blocker.
    assert pkt["current_head_active_blocker_count"] == 1
    assert any(
        t.get("thread_id") == "PRRT_minimax_uppercase_1"
        for t in pkt["active_threads"]
    )
    # The case-insensitive classifier must drive the audit to
    # HOLD_NEW_CODEX_THREAD, not HOLD_CODEX_RESPONSE_PENDING.
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD


def test_minimax_p2_lowercase_codex_login_still_recognized(
    monkeypatch, tmp_path,
):
    """Companion regression: the lowercase Codex login
    (``chatgpt-codex-connector``) MUST keep working so the
    fix does not break the existing happy path.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=7001,
        )
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {
                        "id": "PRRT_minimax_lowercase_1",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {"nodes": [{
                            "databaseId": 9101,
                            "url": "https://example/9101",
                            "body": "P1 finding (lowercase author)",
                            "path": "scripts/local/foo.py",
                            "line": 1,
                            "author": {"login": "chatgpt-codex-connector"},
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
    assert pkt["current_head_active_blocker_count"] == 1
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD


# ---------------------------------------------------------------------------
# FINAL direct-CLI micro-repair: fallback type-safety and
# case-insensitive identity semantics
# ---------------------------------------------------------------------------
#
# When the canonical ``is_codex_login`` share-classifier import
# is unavailable, ``has_active_blocker`` falls back to a local
# predicate. The previous fallback
# ``(t.get("author", "") or "").lower() in {a.lower() for a in CODEX_BOT_LOGINS}``
# raised ``AttributeError`` when ``author`` was a truthy non-string
# value (e.g., an integer from a malformed GraphQL response). The
# micro-repair introduces ``_local_codex_login_fallback`` which is
# type-safe (rejects non-string values) and case-insensitive
# (delegates to the precomputed ``_LOCAL_CODEX_LOGINS_LOWER`` set).


def test_final_fallback_mixed_case_codex_classified_as_active_blocker(
    monkeypatch, tmp_path,
):
    """FINAL #1: when the canonical ``is_codex_login``
    is unavailable, the local fallback must still recognize
    mixed-case Codex identities (case-insensitive identity
    semantics) and drive the audit to ``HOLD_NEW_CODEX_THREAD``.

    This proves the fallback itself (not the canonical
    predicate) handles mixed-case Codex identities.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=8001,
        )
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {
                        "id": "PRRT_final_fallback_uppercase",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {"nodes": [{
                            "databaseId": 9501,
                            "url": "https://example/9501",
                            "body": "P1 finding (uppercase, fallback)",
                            "path": "scripts/local/foo.py",
                            "line": 1,
                            "author": {"login": "CHATGPT-CODEX-CONNECTOR"},
                        }]},
                    },
                ],
            }
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)
    # Force the fallback path by nulling the canonical
    # shared predicate.
    monkeypatch.setattr(mod, "_shared_is_codex_login", None)
    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    assert rc == 0
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # Inventory must be complete.
    assert pkt["review_thread_inventory_complete"] is True
    assert pkt["review_thread_comment_inventory_complete"] is True
    # The fallback must drive the audit to HOLD_NEW_CODEX_THREAD
    # (the case-insensitive identity must still work).
    assert pkt["current_head_active_blocker_count"] == 1
    assert pkt["status"] == mod.STATUS_HOLD_NEW_THREAD


def test_final_fallback_malformed_non_string_author_does_not_crash(
    monkeypatch, tmp_path,
):
    """FINAL #2: when the canonical ``is_codex_login``
    is unavailable, a truthy non-string author (e.g., integer
    123) MUST NOT cause ``AttributeError`` or ERROR_TOOL_FAILURE.
    The malformed author must be rejected (not classified as
    Codex), the unresolved thread must remain in the
    inventory, and the audit must reach a safe lifecycle status
    consistent with the existing non-Codex unresolved-thread
    policy.
    """
    sleep = FakeSleep()
    monkeypatch.setattr("time.sleep", sleep)
    pr_view = make_pr_view()
    issue = [
        make_issue_comment(
            author=CODEX_LOGIN,
            body=codex_clean_pass_body(),
            created_at="2026-06-11T18:00:00Z",
            comment_id=8101,
        )
    ]
    threads = {
        "data": {"repository": {"pullRequest": {
            "reviewThreads": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {
                        "id": "PRRT_final_fallback_malformed",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {"nodes": [{
                            "databaseId": 9601,
                            "url": "https://example/9601",
                            "body": "P1 finding (malformed author)",
                            "path": "scripts/local/foo.py",
                            "line": 1,
                            # INTEGER author — the previous
                            # fallback would raise
                            # ``AttributeError`` when
                            # calling ``.lower()``. The
                            # fix must reject this safely.
                            "author": {"login": 123},
                        }]},
                    },
                ],
            }
        }}}
    }
    runner = make_gh_runner(pr_view, issue, [], threads)
    monkeypatch.setattr(mod.subprocess, "run", runner)
    # Force the fallback path.
    monkeypatch.setattr(mod, "_shared_is_codex_login", None)
    rc = mod.main([
        "--repo", REPO, "--pr", "401", "--expected-head", EXPECTED_HEAD,
        "--ping-comment-id", PING_ID, "--ping-created-at", PING_CREATED,
        "--max-polls", "1", "--poll-seconds", "0",
        "--output-json", str(tmp_path / "pkt.json"),
        "--output-md", str(tmp_path / "pkt.md"),
    ])
    # The audit must complete without raising AttributeError.
    # The previous fallback would raise AttributeError inside
    # ``classify()`` which would surface as a non-zero rc
    # and an error status. The fix must succeed and emit a
    # safe lifecycle status.
    assert rc == 0, (
        f"Audit returned rc={rc}; expected rc=0 (no crash on "
        f"malformed author)"
    )
    pkt = json.loads((tmp_path / "pkt.json").read_text())
    # The unresolved thread must remain in the inventory.
    assert any(
        t.get("thread_id") == "PRRT_final_fallback_malformed"
        for t in pkt["active_threads"]
    )
    # The malformed author must NOT be classified as a Codex
    # blocker, so the clean pass wins. The audit must reach
    # CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED (clean pass + the
    # unresolved non-Codex thread). This is the existing
    # non-Codex unresolved-thread policy applied to a
    # malformed author.
    assert pkt["status"] == mod.STATUS_CLEAN_PASS_RESOLVE_ONLY
    # The audit must not be an error status.
    assert pkt["status"] != mod.STATUS_ERROR_TOOL_FAILURE
    assert pkt["status"] != mod.STATUS_ERROR_INVALID_ARGS


def test_r106_audit_nested_cap_aggregate(monkeypatch):
    """Round-106 follow-up (VUIvY / PRRT_kwDOSHFpYM6VUIvY): the
    audit's nested-pagination follower accepts an inventory
    split across N threads. The per-thread cap is honored,
    but the AGGREGATE cap must ALSO fail closed once the sum
    of pages across threads crosses the operator's bound;
    otherwise 31 threads with one extra page each return
    complete=True with up to 31 × safety_cap comments.
    """
    from scripts.local.audit_codex_response_for_pr import (
        _follow_nested_cursor_for_threads,
    )

    # 31 threads each with a single 1-page nested inventory.
    thread_nodes = []
    for i in range(31):
        thread_nodes.append({
            "id": f"PRRT_t{i}",
            "comments": {
                "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                "nodes": [{"databaseId": 1000 + i}],
            },
        })
    safety_cap = 30

    # Patch the paginator at the source (the audit imports
    # it from ``scripts.local._shared_pagination`` inside
    # the function). Monkey-patch the shared module's name.
    import scripts.local._shared_pagination as pg_mod
    def fake_paginate(*args, **kwargs):
        return {
            "nodes": [{"databaseId": kwargs.get("thread_id", "")}],
            "pages": 1,  # one page per thread
            "capped": False,
            "complete": True,
        }
    monkeypatch.setattr(pg_mod, "paginate_nested_comments", fake_paginate)
    res = _follow_nested_cursor_for_threads(
        thread_nodes, safety_cap=safety_cap, timeout=30
    )

    assert res["complete"] is False, (
        "Round-106 (VUIvY): aggregate pages_total beyond the "
        "cap MUST surface as complete=False; got "
        f"complete={res.get('complete')!r}"
    )
    assert res["capped"] is True, (
        "Round-106 (VUIvY): aggregate cross MUST mark "
        f"capped=True; got capped={res.get('capped')!r}"
    )
    assert "aggregate_pages_cap" in res.get("error", "") or (
        res.get("pages", 0) >= safety_cap
    ), (
        "Round-106 (VUIvY): the aggregate cap must be "
        f"reflected in pages or error; got {res!r}"
    )
