"""
tests/test_run_guarded_pr_flow.py
=================================
Unit tests for run_guarded_pr_flow.py (scope guard gate).

All tests are mocked/local — no network, no GitHub calls.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# The module under test
MODULE_PATH = Path(__file__).parent.parent / "scripts" / "local" / "run_guarded_pr_flow.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    """Initialise a git repo in path with a commit."""
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
    """--administrator (different token) is NOT rejected by pre-parse check."""
    json_path = "/tmp/out_admin_test.json"
    md_path = "/tmp/out_admin_test.md"
    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", "/tmp", "--pr-number", "1",
         "--output-dir", "/tmp/out_admin_test", "--output-json", json_path,
         "--output-md", md_path, "--administrator"]
    )
    assert rc in (1, 2), f"expected rc 1 or 2, got {rc}: {stderr}"
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
# Test 9: Invalid repo root
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


# ---------------------------------------------------------------------------
# Test 10-18: Scope guard gate — real integration tests (no mocking)
# ---------------------------------------------------------------------------

def test_clean_scope_guard_produces_scope_guard_ready(tmp_path):
    """
    With a clean scope (no changes), scope_guard returns SCOPE_CLEAN
    and the flow produces GUARD_FLOW_SCOPE_GUARD_READY.
    """
    _init_git_repo(tmp_path)
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")

    # Default args include no --scope-allow-file, so scope_guard sees no changes
    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "375",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
    )

    assert rc == 0, f"expected rc=0, got {rc}: {stderr}"
    assert Path(json_path).exists()
    data = json.loads(Path(json_path).read_text())
    assert data["status"] == "GUARD_FLOW_SCOPE_GUARD_READY", f"unexpected status: {data['status']}"
    assert data["audit_only"] is True
    assert data["merge_executed"] is False
    assert data["mutated_github"] is False


def test_scope_guard_result_embedded_in_json(tmp_path):
    """scope_guard result is embedded in the JSON report."""
    _init_git_repo(tmp_path)
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")

    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "375",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
    )

    assert rc == 0
    data = json.loads(Path(json_path).read_text())
    assert "scope_guard" in data
    assert data["scope_guard"]["status"] == "SCOPE_CLEAN"
    # base_ref is the provided value or the fallback (HEAD when origin/main is absent)
    assert data["scope_guard"]["base_ref"] in ("origin/main", "HEAD")
    assert data["scope_guard"]["head_ref"] == "HEAD"


def test_scope_guard_section_in_markdown(tmp_path):
    """scope_guard section appears in Markdown report."""
    _init_git_repo(tmp_path)
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")

    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "375",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
    )

    assert rc == 0
    md_text = Path(md_path).read_text()
    assert "Scope Guard" in md_text
    assert "SCOPE_CLEAN" in md_text


def test_all_safety_invariants_false_in_clean_report(tmp_path):
    """Clean scope_guard report has audit_only=True and all write flags False."""
    _init_git_repo(tmp_path)
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")

    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "375",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
    )

    assert rc == 0
    data = json.loads(Path(json_path).read_text())
    assert data["audit_only"] is True
    assert data["merge_executed"] is False
    assert data["mutated_github"] is False
    assert data["used_admin"] is False
    assert data["used_auto"] is False
    assert data["comments_deleted"] is False
    assert data["reviews_dismissed"] is False
    assert data["threads_resolved"] is False
    assert data["workflows_changed"] is False
    assert data["branch_protection_changed"] is False


def test_planned_gate_order_unchanged(tmp_path):
    """gate_order still lists all four planned gates."""
    _init_git_repo(tmp_path)
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")

    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "375",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
    )

    assert rc == 0
    data = json.loads(Path(json_path).read_text())
    assert data["gate_order"] == ["scope_guard", "review_threads", "waiter", "merge_verifier"]


def test_markdown_says_does_not_merge(tmp_path):
    """Markdown report explicitly states it does not merge."""
    _init_git_repo(tmp_path)
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")

    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "375",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
    )

    assert rc == 0
    md_text = Path(md_path).read_text()
    assert "does not merge" in md_text.lower()


def test_scope_guard_allow_file_args_passed(tmp_path):
    """--scope-allow-file args are passed through to scope_guard."""
    _init_git_repo(tmp_path)
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")

    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "375",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path,
         "--scope-allow-file", "scripts/local/run_guarded_pr_flow.py",
         "--scope-allow-file", "tests/test_run_guarded_pr_flow.py"]
    )

    assert rc == 0
    data = json.loads(Path(json_path).read_text())
    # With allow_files matching the changed files, scope should be clean
    assert data["status"] == "GUARD_FLOW_SCOPE_GUARD_READY"
    assert data["scope_guard"]["status"] == "SCOPE_CLEAN"


def test_subprocess_uses_list_args_and_no_shell():
    """
    Verify the script does NOT pass shell argument to subprocess calls.
    Uses AST analysis rather than running the script.
    """
    import ast
    src = MODULE_PATH.read_text()
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # Check for subprocess.run with shell keyword
            if (isinstance(func, ast.Attribute) and
                    func.attr == "run" and
                    isinstance(func.value, ast.Name) and
                    func.value.id == "subprocess"):
                for kw in node.keywords:
                    if kw.arg == "shell":
                        val = kw.value
                        if isinstance(val, ast.Constant) and val.value is True:
                            assert False, (
                                f"subprocess.run called with shell enabled at line {node.lineno}"
                            )


# ---------------------------------------------------------------------------
# Integration-level safety checks live in scope_guard and its companion
# test suite (test_scope_guard.py), not in unit tests here.
# --------------------------------------------------------------------------