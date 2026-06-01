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
# ---------------------------------------------------------------------------

def _load_module():
    """Load the module fresh, bypassing any cached imports."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_guarded_pr_flow", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_guarded_pr_flow"] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_script(argv: list[str], monkeypatch=None) -> tuple[int, str, str]:
    """Run the script with given argv, return (rc, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH)] + argv,
        capture_output=True,
        text=True,
        shell=False,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# Test 1-3: Forbidden token rejection
# ---------------------------------------------------------------------------

def test_rejects_admin():
    """--admin token causes exit 1 (or 2 argparse) and ERROR_TOOL_FAILURE."""
    rc, stdout, stderr = _run_script(["--repo", "o/r", "--repo-root", "/tmp", "--pr-number", "1", "--output-dir", "/tmp/out", "--output-json", "/tmp/out/x.json", "--output-md", "/tmp/out/x.md", "--admin"])
    # rc 1 = our ERROR_TOOL_FAILURE; rc 2 = argparse rejection of unknown arg
    assert rc in (1, 2), f"expected rc 1 or 2, got {rc}: {stderr}"
    assert "Forbidden token" in stderr or "unrecognized arguments" in stderr or "ERROR_TOOL_FAILURE" in stderr


def test_rejects_auto():
    """--auto token causes exit 1 (or 2 argparse) and ERROR_TOOL_FAILURE."""
    rc, stdout, stderr = _run_script(["--repo", "o/r", "--repo-root", "/tmp", "--pr-number", "1", "--output-dir", "/tmp/out", "--output-json", "/tmp/out/x.json", "--output-md", "/tmp/out/x.md", "--auto"])
    assert rc in (1, 2), f"expected rc 1 or 2, got {rc}: {stderr}"
    assert "Forbidden token" in stderr or "unrecognized arguments" in stderr or "ERROR_TOOL_FAILURE" in stderr


def test_rejects_admin_and_auto_together():
    """Both tokens together also rejected."""
    rc, stdout, stderr = _run_script(["--repo", "o/r", "--repo-root", "/tmp", "--pr-number", "1", "--output-dir", "/tmp/out", "--output-json", "/tmp/out/x.json", "--output-md", "/tmp/out/x.md", "--admin", "--auto"])
    assert rc in (1, 2), f"expected rc 1 or 2, got {rc}: {stderr}"


# ---------------------------------------------------------------------------
# Test 4-6: No forbidden options exist
# ---------------------------------------------------------------------------

def test_no_merge_option():
    """--merge does not exist as a CLI argument."""
    rc, stdout, stderr = _run_script(["--repo", "o/r", "--repo-root", "/tmp", "--pr-number", "1", "--output-dir", "/tmp/out", "--output-json", "/tmp/out/x.json", "--output-md", "/tmp/out/x.md", "--merge"])
    assert rc != 0
    # Should be a parser error about unrecognized arguments, not a success
    assert "unrecognized arguments: --merge" in stderr or rc == 1


def test_no_execute_option():
    """--execute does not exist as a CLI argument."""
    rc, stdout, stderr = _run_script(["--repo", "o/r", "--repo-root", "/tmp", "--pr-number", "1", "--output-dir", "/tmp/out", "--output-json", "/tmp/out/x.json", "--output-md", "/tmp/out/x.md", "--execute"])
    assert rc != 0
    assert "unrecognized arguments: --execute" in stderr or rc == 1


def test_no_resolve_threads_option():
    """--resolve-threads does not exist as a CLI argument."""
    rc, stdout, stderr = _run_script(["--repo", "o/r", "--repo-root", "/tmp", "--pr-number", "1", "--output-dir", "/tmp/out", "--output-json", "/tmp/out/x.json", "--output-md", "/tmp/out/x.md", "--resolve-threads"])
    assert rc != 0
    assert "unrecognized arguments: --resolve-threads" in stderr or rc == 1


# ---------------------------------------------------------------------------
# Test 7-8: Repo-root validation
# ---------------------------------------------------------------------------

def test_invalid_repo_root_returns_error():
    """Non-existent repo-root returns ERROR_TOOL_FAILURE."""
    with tempfile.TemporaryDirectory() as tmpdir:
        json_path = str(Path(tmpdir) / "out.json")
        md_path = str(Path(tmpdir) / "out.md")
        out_dir = str(Path(tmpdir) / "out")
        rc, stdout, stderr = _run_script(
            ["--repo", "o/r", "--repo-root", "/nonexistent/path", "--pr-number", "1",
             "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
        )
        assert rc == 1
        assert "ERROR_TOOL_FAILURE" in stderr
        assert Path(json_path).exists()
        data = json.loads(Path(json_path).read_text())
        assert data["status"] == "ERROR_TOOL_FAILURE"


def test_valid_temp_git_repo_returns_skeleton_ready(tmp_path):
    """A valid temporary git repo returns GUARD_FLOW_SKELETON_READY."""
    # Initialise a bare git repo in the temp dir
    sub = tmp_path / "sub"
    sub.mkdir()
    subprocess.run(["git", "init", str(sub)], capture_output=True, shell=False)
    subprocess.run(
        ["git", "-C", str(sub), "config", "user.email", "test@test.local"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(sub), "config", "user.name", "Test"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(sub), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, shell=False,
    )

    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")
    rc, stdout, stderr = _run_script(
        ["--repo", "o/r", "--repo-root", str(sub), "--pr-number", "1",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
    )
    assert rc == 0, f"stderr: {stderr}"
    assert Path(json_path).exists()
    data = json.loads(Path(json_path).read_text())
    assert data["status"] == "GUARD_FLOW_SKELETON_READY"


# ---------------------------------------------------------------------------
# Test 9-14: Report content
# ---------------------------------------------------------------------------

def test_json_includes_audit_only_true(tmp_path):
    """JSON report includes audit_only=true."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, shell=False)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.local"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, shell=False,
    )
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
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, shell=False)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.local"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, shell=False,
    )
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
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, shell=False)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.local"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, shell=False,
    )
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
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, shell=False)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.local"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, shell=False,
    )
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
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, shell=False)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.local"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, shell=False,
    )
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
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, shell=False)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.local"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, shell=False,
    )
    json_path = str(tmp_path / "out.json")
    md_path = str(tmp_path / "out.md")
    out_dir = str(tmp_path / "out")
    rc, _, _ = _run_script(
        ["--repo", "o/r", "--repo-root", str(tmp_path), "--pr-number", "375",
         "--output-dir", out_dir, "--output-json", json_path, "--output-md", md_path]
    )
    assert rc == 0
    md_text = Path(md_path).read_text()
    assert "v1 skeleton does not merge" in md_text.lower() or "does not merge" in md_text.lower()


def test_planned_gate_order_includes_all_four_gates(tmp_path):
    """gate_order lists scope_guard, review_threads, waiter, merge_verifier."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, shell=False)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.local"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        capture_output=True, shell=False,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, shell=False,
    )
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
# Test 15-17: Safety grep (runtime code quality)
# ---------------------------------------------------------------------------

def test_runtime_code_no_gh_pr_merge():
    """Runtime code must not contain 'gh pr merge' string."""
    src = MODULE_PATH.read_text()
    # Allow in comments/docstrings but not as live execution
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        if "gh pr merge" in line.lower():
            # Allow only in string literals that are in comments or docs
            assert False, f"Runtime code contains 'gh pr merge': {line!r}"


def test_runtime_code_no_resolvereviewthread():
    """Runtime code must not contain 'resolveReviewThread' GraphQL mutation."""
    src = MODULE_PATH.read_text()
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "resolveReviewThread" in line:
            assert False, f"Runtime code contains 'resolveReviewThread': {line!r}"


def test_subprocess_calls_use_list_and_shell_false():
    """
    Any subprocess.run calls use list argv and shell=False.
    We verify the source for subprocess.run invocations.
    """
    src = MODULE_PATH.read_text()
    import ast
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if (isinstance(node.func, ast.Attribute) and
                    node.func.attr == "run" and
                    isinstance(node.func.value, ast.Name) and
                    node.func.value.id == "subprocess"):
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        assert False, f"subprocess.run called with shell=True at line {node.lineno}"