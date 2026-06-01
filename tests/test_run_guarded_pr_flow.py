"""
tests/test_run_guarded_pr_flow.py
=================================
Unit tests for run_guarded_pr_flow.py v1 skeleton.

All tests are mocked/local — no network, no GitHub calls.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# The module under test — import via importlib to force re-load each time
MODULE_PATH = Path(__file__).parent.parent / "scripts" / "local" / "run_guarded_pr_flow.py"


# ---------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _load_module():
    """Load the module fresh, bypassing any cached imports."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_guarded_pr_flow", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_guarded_pr_flow"] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_script(argv: list[str]) -> tuple[int, str, str]:
    """Run the script with given argv, return (rc, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH)] + argv,
        capture_output=True,
        text=True,
        shell=False,
    )
    return result.returncode, result.stdout, result.stderr


def _init_git_repo(path: Path) -> None:
    """Initialise a bare git repo in path."""
    subprocess.run(["git", "init", str(path)], capture_output=True, shell=False)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@test.local"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, shell=False,
    )


# ---------------------------------------------------------------------------
# Test 1-5: Forbidden token rejection (pre-parse check, reports written)
# ---------------------------------------------------------------------------

def test_rejects_admin_returns_1_and_writes_json(tmp_path):
    """--admin is rejected before parse_args; JSON report written."""
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")
    _init_git_repo(tmp_path)
    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "1",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path,
         "--admin"]
    )
    assert rc == 1, f"expected rc=1, got {rc}: {stderr}"
    assert "Forbidden token" in stderr
    assert Path(json_path).exists(), "JSON report not written before parse_args exits"
    data = json.loads(Path(json_path).read_text())
    assert data["status"] == "ERROR_TOOL_FAILURE"


def test_rejects_auto_returns_1_and_writes_json(tmp_path):
    """--auto is rejected before parse_args; JSON report written."""
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")
    _init_git_repo(tmp_path)
    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "1",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path,
         "--auto"]
    )
    assert rc == 1, f"expected rc=1, got {rc}: {stderr}"
    assert "Forbidden token" in stderr
    assert Path(json_path).exists(), "JSON report not written before parse_args exits"
    data = json.loads(Path(json_path).read_text())
    assert data["status"] == "ERROR_TOOL_FAILURE"


def test_rejects_admin_and_auto_together(tmp_path):
    """Both tokens together are rejected."""
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")
    _init_git_repo(tmp_path)
    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "1",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path,
         "--admin", "--auto"]
    )
    assert rc == 1, f"expected rc=1, got {rc}: {stderr}"


def test_rejects_admin_writes_markdown(tmp_path):
    """--admin rejection writes Markdown report when --output-md is provided."""
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")
    _init_git_repo(tmp_path)
    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "1",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path,
         "--admin"]
    )
    assert rc == 1
    assert Path(md_path).exists(), "Markdown report not written before parse_args exits"
    md_text = Path(md_path).read_text()
    assert "ERROR_TOOL_FAILURE" in md_text


def test_administrator_not_rejected():
    """--administrator (different token) is NOT rejected."""
    json_path = "/tmp/out_admin_test.json"
    md_path = "/tmp/out_admin_test.md"
    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", "/tmp", "--pr-number", "1",
         "--output-dir", "/tmp/out_admin_test", "--output-json", json_path,
         "--output-md", md_path, "--administrator"]
    )
    # Should not be rejected as forbidden (--administrator != --admin)
    # argparse will reject it as an unknown option, but not our pre-parse check
    assert rc in (1, 2), f"expected rc 1 or 2, got {rc}: {stderr}"
    # Should NOT contain our custom forbidden-token message
    assert "Forbidden token in argv: --administrator" not in stderr


# ---------------------------------------------------------------------------
# Test 6-8: No forbidden CLI options exist
# ---------------------------------------------------------------------------

def test_no_merge_option():
    """--merge does not exist as a CLI argument."""
    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", "/tmp", "--pr-number", "1",
         "--output-dir", "/tmp/out", "--output-json", "/tmp/out.json",
         "--output-md", "/tmp/out.md", "--merge"]
    )
    assert rc != 0
    assert "unrecognized arguments: --merge" in stderr or rc == 1


def test_no_execute_option():
    """--execute does not exist as a CLI argument."""
    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", "/tmp", "--pr-number", "1",
         "--output-dir", "/tmp/out", "--output-json", "/tmp/out.json",
         "--output-md", "/tmp/out.md", "--execute"]
    )
    assert rc != 0
    assert "unrecognized arguments: --execute" in stderr or rc == 1


def test_no_resolve_threads_option():
    """--resolve-threads does not exist as a CLI argument."""
    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", "/tmp", "--pr-number", "1",
         "--output-dir", "/tmp/out", "--output-json", "/tmp/out.json",
         "--output-md", "/tmp/out.md", "--resolve-threads"]
    )
    assert rc != 0
    assert "unrecognized arguments: --resolve-threads" in stderr or rc == 1


# ---------------------------------------------------------------------------
# Test 9-10: Repo-root validation
# ---------------------------------------------------------------------------

def test_invalid_repo_root_returns_error():
    """Non-existent repo-root returns ERROR_TOOL_FAILURE."""
    json_path = "/tmp/test_invalid_root.json"
    md_path = "/tmp/test_invalid_root.md"
    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", "/nonexistent/path", "--pr-number", "1",
         "--output-dir", "/tmp/out", "--output-json", json_path, "--output-md", md_path]
    )
    assert rc == 1
    assert "ERROR_TOOL_FAILURE" in stderr
    assert Path(json_path).exists()
    data = json.loads(Path(json_path).read_text())
    assert data["status"] == "ERROR_TOOL_FAILURE"


def test_valid_temp_git_repo_returns_skeleton_ready(tmp_path):
    """A valid temporary git repo returns GUARD_FLOW_SKELETON_READY."""
    _init_git_repo(tmp_path)
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")
    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "1",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
    )
    assert rc == 0, f"stderr: {stderr}"
    assert Path(json_path).exists()
    data = json.loads(Path(json_path).read_text())
    assert data["status"] == "GUARD_FLOW_SKELETON_READY"


# ---------------------------------------------------------------------------
# Test 11-17: Report content
# ---------------------------------------------------------------------------

def test_json_includes_audit_only_true(tmp_path):
    """JSON report includes audit_only=true."""
    _init_git_repo(tmp_path)
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")
    rc, _, _ = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "375",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
    )
    assert rc == 0
    data = json.loads(Path(json_path).read_text())
    assert data["audit_only"] is True


def test_json_includes_merge_executed_false(tmp_path):
    """JSON report includes merge_executed=false."""
    _init_git_repo(tmp_path)
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")
    rc, _, _ = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "375",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
    )
    assert rc == 0
    data = json.loads(Path(json_path).read_text())
    assert data["merge_executed"] is False


def test_json_includes_mutated_github_false(tmp_path):
    """JSON report includes mutated_github=false."""
    _init_git_repo(tmp_path)
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")
    rc, _, _ = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "375",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
    )
    assert rc == 0
    data = json.loads(Path(json_path).read_text())
    assert data["mutated_github"] is False


def test_json_includes_used_admin_false_and_used_auto_false(tmp_path):
    """JSON report includes used_admin=false and used_auto=false."""
    _init_git_repo(tmp_path)
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")
    rc, _, _ = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "375",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
    )
    assert rc == 0
    data = json.loads(Path(json_path).read_text())
    assert data["used_admin"] is False
    assert data["used_auto"] is False


def test_json_includes_safety_flags(tmp_path):
    """JSON report includes all safety flags as False."""
    _init_git_repo(tmp_path)
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")
    rc, _, _ = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "375",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
    )
    assert rc == 0
    data = json.loads(Path(json_path).read_text())
    assert data["comments_deleted"] is False
    assert data["reviews_dismissed"] is False
    assert data["threads_resolved"] is False
    assert data["workflows_changed"] is False
    assert data["branch_protection_changed"] is False


def test_markdown_says_skeleton_does_not_merge(tmp_path):
    """Markdown report explicitly states v1 skeleton does not merge."""
    _init_git_repo(tmp_path)
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")
    rc, _, _ = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "375",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
    )
    assert rc == 0
    md_text = Path(md_path).read_text()
    assert "does not merge" in md_text.lower()


def test_planned_gate_order_includes_all_four_gates(tmp_path):
    """gate_order lists scope_guard, review_threads, waiter, merge_verifier."""
    _init_git_repo(tmp_path)
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")
    rc, _, _ = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "375",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
    )
    assert rc == 0
    data = json.loads(Path(json_path).read_text())
    assert data["gate_order"] == ["scope_guard", "review_threads", "waiter", "merge_verifier"]


# ---------------------------------------------------------------------------
# Integration-level safety checks live in scope_guard and its companion
# test suite (test_scope_guard.py), not in unit tests here.
# --------------------------------------------------------------------------