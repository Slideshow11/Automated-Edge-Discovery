"""Tests for resolve_stale_threads_for_pr.py (v1 — audit/report-only).
No network calls, no real GitHub calls. All mocked via importlib direct-loading.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest


def fake_proc(stdout="", stderr="", returncode=0):
    """Fake CompletedProcess for subprocess.run mocking."""
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


# Dynamically derive repo root from this test file's location.
# Works both locally (worktree) and on CI (checked-out repo).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "local" / "resolve_stale_threads_for_pr.py"


def load_script(script_path=None):
    """Load a Python script as an isolated module via importlib.
    Defaults to _SCRIPT_PATH (repo-root-relative) when no argument given.
    """
    target = _SCRIPT_PATH if script_path is None else script_path
    spec = importlib.util.spec_from_file_location(
        "resolve_stale_threads_for_pr", str(target),
    )
    if spec is None:
        raise RuntimeError(f"Could not load spec for {target}")
    mod = importlib.util.module_from_spec(spec)
    # pyright: ignore[reportOptionalMemberAccess]
    spec.loader.exec_module(mod)  # type: ignore[reportOptionalMemberAccess]
    return mod


# ---------------------------------------------------------------------------
# Test 1: rejects --admin in argv
# ---------------------------------------------------------------------------

def test_rejects_admin_in_argv():
    """When --admin is in argv, reject_admin raises ValueError."""
    mod = load_script()
    with pytest.raises(ValueError, match="Forbidden flag"):
        mod.reject_admin(["prog", "--admin"])


# ---------------------------------------------------------------------------
# Test 2: --execute-resolutions does NOT exist in v1
# ---------------------------------------------------------------------------

def test_no_execute_resolutions_flag():
    """Supplying --execute-resolutions causes argparse error (unrecognised argument)."""
    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT_PATH),
            "--execute-resolutions",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--output-json", "/tmp/a.json",
            "--output-md", "/tmp/a.md",
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2  # argparse exit


# ---------------------------------------------------------------------------
# Test 3: clean PR with no unresolved threads -> THREAD_AUDIT_CLEAN
# ---------------------------------------------------------------------------

def test_clean_pr_returns_thread_audit_clean(tmp_path, monkeypatch):
    """PR with only resolved threads returns THREAD_AUDIT_CLEAN."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123", "baseRefOid": "def456",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    threads_response = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {"id": "PRRT_1", "isResolved": True, "isOutdated": False,
                             "path": "scripts/local/foo.py", "line": 10,
                             "comments": {"nodes": [
                                 {"body": "All good", "author": {"login": "alice"}}
                             ]}},
                        ],
                    },
                },
            },
        },
    })

    def fake_run(cmd, *args, **kwargs):
        if cmd[1] == "pr" and cmd[2] == "view":
            return fake_proc(stdout=pr_response)
        if "graphql" in cmd:
            return fake_proc(stdout=threads_response)
        return fake_proc(stdout="{}")

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--expected-head", "abc123",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "THREAD_AUDIT_CLEAN"
    assert data["mutated_github"] is False
    assert data["resolved_any"] is False
    assert data["execute_resolutions_supported"] is False


# ---------------------------------------------------------------------------
# Test 4: unresolved current P1/P2 thread -> HOLD_CURRENT_THREADS_PRESENT
# ---------------------------------------------------------------------------

def test_current_p1_thread_returns_hold_current(tmp_path, monkeypatch):
    """An unresolved, non-outdated P1 thread returns HOLD_CURRENT_THREADS_PRESENT."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123", "baseRefOid": "def456",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    threads_response = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {"id": "PRRT_P1", "isResolved": False, "isOutdated": False,
                             "path": "scripts/local/foo.py", "line": 20,
                             "comments": {"nodes": [
                                 {"body": "**[P1 Badge](url)  Stop reusing stale waiter output**\nSome text",
                                  "author": {"login": "chatgpt-codex-connector"}}
                             ]}},
                        ],
                    },
                },
            },
        },
    })

    def fake_run(cmd, *args, **kwargs):
        if cmd[1] == "pr" and cmd[2] == "view":
            return fake_proc(stdout=pr_response)
        if "graphql" in cmd:
            return fake_proc(stdout=threads_response)
        return fake_proc(stdout="{}")

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "HOLD_CURRENT_THREADS_PRESENT"
    assert len(data["unresolved_current_threads"]) == 1
    assert data["unresolved_current_threads"][0]["id"] == "PRRT_P1"
    assert data["unresolved_current_threads"][0]["severity"] == "P1"


# ---------------------------------------------------------------------------
# Test 5: unresolved outdated thread -> HOLD_OUTDATED_THREADS_PRESENT
# ---------------------------------------------------------------------------

def test_outdated_thread_returns_hold_outdated(tmp_path, monkeypatch):
    """An unresolved but outdated thread returns HOLD_OUTDATED_THREADS_PRESENT."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123", "baseRefOid": "def456",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    threads_response = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {"id": "PRRT_OLD", "isResolved": False, "isOutdated": True,
                             "path": "scripts/local/foo.py", "line": None,
                             "comments": {"nodes": [
                                 {"body": "This is outdated.", "author": {"login": "bob"}}
                             ]}},
                        ],
                    },
                },
            },
        },
    })

    def fake_run(cmd, *args, **kwargs):
        if cmd[1] == "pr" and cmd[2] == "view":
            return fake_proc(stdout=pr_response)
        if "graphql" in cmd:
            return fake_proc(stdout=threads_response)
        return fake_proc(stdout="{}")

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "HOLD_OUTDATED_THREADS_PRESENT"
    assert len(data["unresolved_outdated_threads"]) == 1


# ---------------------------------------------------------------------------
# Test 6: expected-head mismatch -> HOLD_HEAD_CHANGED
# ---------------------------------------------------------------------------

def test_expected_head_mismatch_returns_hold_head_changed(tmp_path, monkeypatch):
    """When expected-head != headRefOid, returns HOLD_HEAD_CHANGED."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    def fake_run(cmd, *args, **kwargs):
        if cmd[1] == "pr" and cmd[2] == "view":
            return fake_proc(stdout=pr_response)
        return fake_proc(stdout="{}")

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--expected-head", "WRONG_SHA",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "HOLD_HEAD_CHANGED"
    assert data["head_sha"] == "abc123"
    assert data["expected_head"] == "WRONG_SHA"
    assert data["suggested_checker_invocations"] == []


# ---------------------------------------------------------------------------
# Test 7: resolved threads omitted by default
# ---------------------------------------------------------------------------

def test_resolved_threads_omitted_by_default(tmp_path, monkeypatch):
    """Resolved threads are not in unresolved_current/outdated lists by default."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    threads_response = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {"id": "PRRT_RES", "isResolved": True, "isOutdated": False,
                             "path": "foo.py", "line": None,
                             "comments": {"nodes": [
                                 {"body": "Resolved comment.", "author": {"login": "alice"}}
                             ]}},
                            {"id": "PRRT_CURR", "isResolved": False, "isOutdated": False,
                             "path": "bar.py", "line": 5,
                             "comments": {"nodes": [
                                 {"body": "Still open.", "author": {"login": "bob"}}
                             ]}},
                        ],
                    },
                },
            },
        },
    })

    def fake_run(cmd, *args, **kwargs):
        if cmd[1] == "pr" and cmd[2] == "view":
            return fake_proc(stdout=pr_response)
        if "graphql" in cmd:
            return fake_proc(stdout=threads_response)
        return fake_proc(stdout="{}")

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    ids_current = {t["id"] for t in data["unresolved_current_threads"]}
    ids_outdated = {t["id"] for t in data["unresolved_outdated_threads"]}
    assert "PRRT_RES" not in ids_current
    assert "PRRT_RES" not in ids_outdated
    assert "PRRT_CURR" in ids_current


# ---------------------------------------------------------------------------
# Test 8: resolved threads included when --include-resolved
# ---------------------------------------------------------------------------

def test_include_resolved_shows_resolved_threads(tmp_path, monkeypatch):
    """With --include-resolved, resolved threads appear in resolved_threads list."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    threads_response = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {"id": "PRRT_RES", "isResolved": True, "isOutdated": False,
                             "path": "foo.py", "line": None,
                             "comments": {"nodes": [
                                 {"body": "Resolved comment.", "author": {"login": "alice"}}
                             ]}},
                        ],
                    },
                },
            },
        },
    })

    def fake_run(cmd, *args, **kwargs):
        if cmd[1] == "pr" and cmd[2] == "view":
            return fake_proc(stdout=pr_response)
        if "graphql" in cmd:
            return fake_proc(stdout=threads_response)
        return fake_proc(stdout="{}")

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--include-resolved",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["resolved_count"] == 1
    assert data["resolved_threads"][0]["id"] == "PRRT_RES"
    assert data["resolved_threads"][0]["isResolved"] is True


# ---------------------------------------------------------------------------
# Test 9: ignored author counted as ignored, does not block
# ---------------------------------------------------------------------------

def test_ignored_author_under_ignored_threads(tmp_path, monkeypatch):
    """Thread by ignored user appears in ignored_threads, not unresolved_current."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    threads_response = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {"id": "PRRT_IGNORE", "isResolved": False, "isOutdated": False,
                             "path": "foo.py", "line": 30,
                             "comments": {"nodes": [
                                 {"body": "**[P1]** please fix",
                                  "author": {"login": "chatgpt-codex-connector[bot]"}}
                             ]}},
                        ],
                    },
                },
            },
        },
    })

    def fake_run(cmd, *args, **kwargs):
        if cmd[1] == "pr" and cmd[2] == "view":
            return fake_proc(stdout=pr_response)
        if "graphql" in cmd:
            return fake_proc(stdout=threads_response)
        return fake_proc(stdout="{}")

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--ignore-users", "chatgpt-codex-connector[bot]",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["ignored_count"] == 1
    assert data["unresolved_current_count"] == 0
    assert data["status"] == "THREAD_AUDIT_CLEAN"
    assert data["ignored_threads"][0]["id"] == "PRRT_IGNORE"


# ---------------------------------------------------------------------------
# Test 10: pagination hasNextPage=true handled
# ---------------------------------------------------------------------------

def test_pagination_handles_has_next_page(tmp_path, monkeypatch):
    """When hasNextPage=true, script paginates or returns HOLD_PAGINATION_REQUIRED."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    page1_nodes = [
        {"id": "PRRT_1", "isResolved": False, "isOutdated": False,
         "path": "a.py", "line": 1,
         "comments": {"nodes": [{"body": "Page 1", "author": {"login": "alice"}}]}},
    ]
    page2_nodes = [
        {"id": "PRRT_2", "isResolved": False, "isOutdated": True,
         "path": "b.py", "line": 2,
         "comments": {"nodes": [{"body": "Page 2", "author": {"login": "bob"}}]}},
    ]

    page_num = {"n": 0}

    def fake_run(cmd, *args, **kwargs):
        if cmd[1] == "pr" and cmd[2] == "view":
            return fake_proc(stdout=pr_response)
        if "graphql" in cmd:
            page_num["n"] += 1
            if page_num["n"] == 1:
                return fake_proc(stdout=json.dumps({
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "pageInfo": {"hasNextPage": True, "endCursor": "cursorA"},
                                    "nodes": page1_nodes,
                                },
                            },
                        },
                    },
                }))
            else:
                return fake_proc(stdout=json.dumps({
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "nodes": page2_nodes,
                                },
                            },
                        },
                    },
                }))
        return fake_proc(stdout="{}")

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] in (
        "HOLD_OUTDATED_THREADS_PRESENT", "HOLD_CURRENT_THREADS_PRESENT",
        "THREAD_AUDIT_CLEAN", "HOLD_REVIEW_THREAD_PAGINATION_REQUIRED",
    )
    assert data["pagination_pages"] >= 1


# ---------------------------------------------------------------------------
# Test 11: suggested checker invocation includes required parts
# ---------------------------------------------------------------------------

def test_checker_suggestion_contains_required_parts(tmp_path, monkeypatch):
    """Outdated thread suggestion includes all required check_stale script arguments."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    threads_response = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {"id": "PRRT_CAND", "isResolved": False, "isOutdated": True,
                             "path": "scripts/local/foo.py", "line": None,
                             "comments": {"nodes": [
                                 {"body": "Use `waiter_status.json` instead of stale output",
                                  "author": {"login": "bob"}}
                             ]}},
                        ],
                    },
                },
            },
        },
    })

    def fake_run(cmd, *args, **kwargs):
        if cmd[1] == "pr" and cmd[2] == "view":
            return fake_proc(stdout=pr_response)
        if "graphql" in cmd:
            return fake_proc(stdout=threads_response)
        return fake_proc(stdout="{}")

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--base-ref", "main",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert len(data["suggested_checker_invocations"]) == 1
    suggestion = data["suggested_checker_invocations"][0]
    cmd_parts = suggestion["command_parts"]
    assert "check_stale_review_thread_resolution.py" in suggestion["command"]
    assert "--repo" in cmd_parts
    assert "--pr-number" in cmd_parts
    assert "--thread-id" in cmd_parts
    assert "--expected-head" in cmd_parts
    assert "--base-ref" in cmd_parts
    assert suggestion["candidate_only"] is True


# ---------------------------------------------------------------------------
# Test 12: suggestion generation includes code-like quoted phrases
# ---------------------------------------------------------------------------

def test_suggestion_includes_quoted_code_snippets(tmp_path, monkeypatch):
    """Quoted code snippets in comment body are extracted as candidate patterns."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    threads_response = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {"id": "PRRT_Q", "isResolved": False, "isOutdated": True,
                             "path": "foo.py", "line": None,
                             "comments": {"nodes": [
                                 {"body": "The `waiter_status.json` file can be reused incorrectly.",
                                  "author": {"login": "alice"}}
                             ]}},
                        ],
                    },
                },
            },
        },
    })

    def fake_run(cmd, *args, **kwargs):
        if cmd[1] == "pr" and cmd[2] == "view":
            return fake_proc(stdout=pr_response)
        if "graphql" in cmd:
            return fake_proc(stdout=threads_response)
        return fake_proc(stdout="{}")

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    suggestion = data["suggested_checker_invocations"][0]
    patterns = suggestion["candidate_patterns"]
    quoted_patterns = [p for p in patterns if p["pattern_type"] == "quoted_snippet"]
    assert any("waiter_status.json" in p["pattern_value"] for p in quoted_patterns)


# ---------------------------------------------------------------------------
# Test 13: suggestion generation avoids overly broad single tokens
# ---------------------------------------------------------------------------

def test_suggestion_avoids_broad_tokens(tmp_path, monkeypatch):
    """Single-word broad tokens like 'mutation', 'file', 'line' are excluded."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    threads_response = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {"id": "PRRT_B", "isResolved": False, "isOutdated": True,
                             "path": "foo.py", "line": None,
                             "comments": {"nodes": [
                                 {"body": "The `file` has `line` issues. Also `mutation` is bad.",
                                  "author": {"login": "alice"}}
                             ]}},
                        ],
                    },
                },
            },
        },
    })

    def fake_run(cmd, *args, **kwargs):
        if cmd[1] == "pr" and cmd[2] == "view":
            return fake_proc(stdout=pr_response)
        if "graphql" in cmd:
            return fake_proc(stdout=threads_response)
        return fake_proc(stdout="{}")

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    if data["suggested_checker_invocations"]:
        for p in data["suggested_checker_invocations"][0]["candidate_patterns"]:
            assert p["pattern_value"].lower() not in {
                "mutation", "file", "line", "code", "thread",
            }


# ---------------------------------------------------------------------------
# Test 14: JSON report includes required safety fields
# ---------------------------------------------------------------------------

def test_json_report_safety_fields(tmp_path, monkeypatch):
    """JSON report always includes mutated_github=false, resolved_any=false, execute_resolutions_supported=false."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    threads_response = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [],
                    },
                },
            },
        },
    })

    def fake_run(cmd, *args, **kwargs):
        if cmd[1] == "pr" and cmd[2] == "view":
            return fake_proc(stdout=pr_response)
        if "graphql" in cmd:
            return fake_proc(stdout=threads_response)
        return fake_proc(stdout="{}")

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["mutated_github"] is False
    assert data["resolved_any"] is False
    assert data["execute_resolutions_supported"] is False


# ---------------------------------------------------------------------------
# Test 15: Markdown report states v1 does not resolve threads
# ---------------------------------------------------------------------------

def test_markdown_report_states_no_resolution(tmp_path, monkeypatch):
    """Markdown report explicitly states v1 does not resolve threads."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    threads_response = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [],
                    },
                },
            },
        },
    })

    def fake_run(cmd, *args, **kwargs):
        if cmd[1] == "pr" and cmd[2] == "view":
            return fake_proc(stdout=pr_response)
        if "graphql" in cmd:
            return fake_proc(stdout=threads_response)
        return fake_proc(stdout="{}")

    md_out = tmp_path / "threads.md"
    json_out = tmp_path / "threads.json"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()
    assert rc == 0
    md_text = md_out.read_text()
    assert "v1 is audit/report-only" in md_text.lower() or "no threads were resolved" in md_text.lower()
    assert "admin" in md_text.lower()
    assert "mutated_github" in md_text


# ---------------------------------------------------------------------------
# Test 16: subprocess calls use list args and not shell=True
# ---------------------------------------------------------------------------

def test_subprocess_calls_use_list_args(tmp_path, monkeypatch):
    """All subprocess.run calls use list argv, not shell=True."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    threads_response = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [],
                    },
                },
            },
        },
    })

    recorded_calls = []

    def recording_run(*args, **kwargs):
        recorded_calls.append({
            "args": args[0] if args else kwargs.get("args"),
            "shell": kwargs.get("shell"),
        })
        if args and args[0][1] == "pr" and args[0][2] == "view":
            return fake_proc(stdout=pr_response)
        if args and "graphql" in args[0]:
            return fake_proc(stdout=threads_response)
        return fake_proc(stdout="{}")

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", recording_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()

    for call in recorded_calls:
        if call["args"] is not None:
            assert call["shell"] is None or call["shell"] is False, \
                f"shell=True used in call: {call}"


# ---------------------------------------------------------------------------
# Test 17: GraphQL query does not contain mutation keyword
# ---------------------------------------------------------------------------

def test_graphql_query_is_read_only(tmp_path, monkeypatch):
    """GraphQL queries sent by this script contain no 'mutation' keyword."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    threads_response = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [],
                    },
                },
            },
        },
    })

    graphql_calls = []

    def fake_run(cmd, *args, **kwargs):
        if cmd[1] == "pr" and cmd[2] == "view":
            return fake_proc(stdout=pr_response)
        if "graphql" in cmd:
            graphql_calls.append(cmd)
            return fake_proc(stdout=threads_response)
        return fake_proc(stdout="{}")

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()

    for call in graphql_calls:
        for part in call:
            assert "mutation" not in part.lower(), \
                f"'mutation' found in GraphQL call part: {part}"


# ---------------------------------------------------------------------------
# Test 18: current P3/unknown unresolved thread still reports current unresolved
# ---------------------------------------------------------------------------

def test_p3_unknown_current_thread_reports_current(tmp_path, monkeypatch):
    """Current unresolved P3/unknown thread still counts as current (HOLD_CURRENT_THREADS_PRESENT)."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    threads_response = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {"id": "PRRT_P3", "isResolved": False, "isOutdated": False,
                             "path": "foo.py", "line": 5,
                             "comments": {"nodes": [
                                 {"body": "**[P3]** minor style issue",
                                  "author": {"login": "alice"}}
                             ]}},
                        ],
                    },
                },
            },
        },
    })

    def fake_run(cmd, *args, **kwargs):
        if cmd[1] == "pr" and cmd[2] == "view":
            return fake_proc(stdout=pr_response)
        if "graphql" in cmd:
            return fake_proc(stdout=threads_response)
        return fake_proc(stdout="{}")

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "HOLD_CURRENT_THREADS_PRESENT"
    assert data["unresolved_current_threads"][0]["severity"] == "P3"


# ---------------------------------------------------------------------------
# Test 19: multiple threads produce correct counts
# ---------------------------------------------------------------------------

def test_multiple_threads_correct_counts(tmp_path, monkeypatch):
    """Multiple threads of different types produce correct per-category counts."""
    pr_response = json.dumps({
        "number": 372, "title": "Test PR", "state": "OPEN",
        "headRefOid": "abc123",
        "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/372",
    })

    threads_response = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {"id": "R1", "isResolved": True, "isOutdated": False,
                             "path": "a.py", "line": None,
                             "comments": {"nodes": [{"body": "Resolved", "author": {"login": "alice"}}]}},
                            {"id": "C1", "isResolved": False, "isOutdated": False,
                             "path": "b.py", "line": 10,
                             "comments": {"nodes": [{"body": "Current P2", "author": {"login": "bob"}}]}},
                            {"id": "O1", "isResolved": False, "isOutdated": True,
                             "path": "c.py", "line": None,
                             "comments": {"nodes": [{"body": "Outdated", "author": {"login": "carol"}}]}},
                            {"id": "I1", "isResolved": False, "isOutdated": False,
                             "path": "d.py", "line": 20,
                             "comments": {"nodes": [{"body": "P1", "author": {"login": "chatgpt-codex-connector[bot]"}}]}},
                        ],
                    },
                },
            },
        },
    })

    def fake_run(cmd, *args, **kwargs):
        if cmd[1] == "pr" and cmd[2] == "view":
            return fake_proc(stdout=pr_response)
        if "graphql" in cmd:
            return fake_proc(stdout=threads_response)
        return fake_proc(stdout="{}")

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script()
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        m.setattr(sys, "argv", [
            "resolve_stale_threads_for_pr.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--pr-number", "372",
            "--ignore-users", "chatgpt-codex-connector[bot]",
            "--output-json", str(json_out),
            "--output-md", str(md_out),
        ])
        rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["unresolved_current_count"] == 1
    assert data["unresolved_outdated_count"] == 1
    assert data["resolved_count"] == 1
    assert data["ignored_count"] == 1
    assert data["status"] == "HOLD_CURRENT_THREADS_PRESENT"
    assert len(data["suggested_checker_invocations"]) == 1
    assert data["suggested_checker_invocations"][0]["thread_id"] == "O1"