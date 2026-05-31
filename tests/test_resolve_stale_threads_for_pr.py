"""
Tests for resolve_stale_threads_for_pr.py (v1 — audit/report-only).
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


def load_script(script_path: str):
    """Load a Python script as an isolated module via importlib."""
    spec = importlib.util.spec_from_file_location(
        "resolve_stale_threads_for_pr", script_path,
    )
    if spec is None:
        raise RuntimeError(f"Could not load spec for {script_path}")
    mod = importlib.util.module_from_spec(spec)
    # pyright: ignore[reportOptionalMemberAccess]
    spec.loader.exec_module(mod)  # type: ignore[reportOptionalMemberAccess]
    return mod


# ---------------------------------------------------------------------------
# Test 1: rejects --admin in argv
# ---------------------------------------------------------------------------

def test_rejects_admin_in_argv():
    """When --admin is in argv, reject_admin raises ValueError."""
    WT = "/tmp/aed_runs/worktrees/pr372_resolve_stale_threads_audit_v1"
    mod = load_script(WT + "/scripts/local/resolve_stale_threads_for_pr.py")
    with pytest.raises(ValueError, match="Forbidden flag"):
        mod.reject_admin(["prog", "--admin"])


# ---------------------------------------------------------------------------
# Test 2: --execute-resolutions does NOT exist in v1
# ---------------------------------------------------------------------------

def test_no_execute_resolutions_flag():
    """Supplying --execute-resolutions causes argparse error (unrecognised argument)."""
    WT = "/tmp/aed_runs/worktrees/pr372_resolve_stale_threads_audit_v1"
    result = subprocess.run(
        [
            sys.executable,
            WT + "/scripts/local/resolve_stale_threads_for_pr.py",
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
    WT = "/tmp/aed_runs/worktrees/pr372_resolve_stale_threads_audit_v1"

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

    monkeypatch.setattr(subprocess, "run", fake_run)

    json_out = tmp_path / "threads.json"
    md_out = tmp_path / "threads.md"
    mod = load_script(WT + "/scripts/local/resolve_stale_threads_for_pr.py")
    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", fake_run)
        # Patch sys.argv so reject_admin sees --admin if present
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
    WT = "/tmp/aed_runs/worktrees/pr372_resolve_stale_threads_audit_v1"

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
    mod = load_script(WT + "/scripts/local/resolve_stale_threads_for_pr.py")
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
    WT = "/tmp/aed_runs/worktrees/pr372_resolve_stale_threads_audit_v1"

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
    mod = load_script(WT + "/scripts/local/resolve_stale_threads_for_pr.py")
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
    WT = "/tmp/aed_runs/worktrees/pr372_resolve_stale_threads_audit_v1"

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
    mod = load_script(WT + "/scripts/local/resolve_stale_threads_for_pr.py")
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
    WT = "/tmp/aed_runs/worktrees/pr372_resolve_stale_threads_audit_v1"

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
    mod = load_script(WT + "/scripts/local/resolve_stale_threads_for_pr.py")
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
    WT = "/tmp/aed_runs/worktrees/pr372_resolve_stale_threads_audit_v1"

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
    mod = load_script(WT + "/scripts/local/resolve_stale_threads_for_pr.py")
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
    WT = "/tmp/aed_runs/worktrees/pr372_resolve_stale_threads_audit_v1"

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
    mod = load_script(WT + "/scripts/local/resolve_stale_threads_for_pr.py")
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


def test_multiple_threads_correct_counts(tmp_path, monkeypatch):
    """Multiple threads of different types produce correct per-category counts."""
    WT = "/tmp/aed_runs/worktrees/pr372_resolve_stale_threads_audit_v1"

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
    mod = load_script(WT + "/scripts/local/resolve_stale_threads_for_pr.py")
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
