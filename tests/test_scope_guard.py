"""Tests for scope_guard.py (v1 — audit/report-only).
No network calls, no real GitHub calls. All mocked via temp git repos.
"""

import json
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


def load_script():
    """Load scope_guard.py as a module."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "local" / "scope_guard.py"
    import importlib.util
    spec = importlib.util.spec_from_file_location("scope_guard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def make_git_repo(tmp_path: Path) -> Path:
    """Create a temp git repo with one initial commit on main branch. Returns the repo path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=False, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=repo, check=False, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo, check=False, capture_output=True
    )
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=False, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=False, capture_output=True)
    return repo


def add_file(repo: Path, rel_path: str, content: str) -> None:
    """Add a file to the repo at rel_path (creates parents), no commit."""
    p = repo / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def commit_all(repo: Path, message: str) -> None:
    """Stage all and commit."""
    subprocess.run(["git", "add", "."], cwd=repo, check=False, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=False, capture_output=True)


# ---------------------------------------------------------------------------
# Test 1: rejects --admin
# ---------------------------------------------------------------------------

def test_rejects_admin(tmp_path, monkeypatch, capsys):
    """scope_guard.py must exit with error if --admin appears in argv."""
    mod = load_script()
    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py", "--admin", "--repo-root", str(tmp_path),
        "--output-json", str(tmp_path / "out.json"),
        "--output-md", str(tmp_path / "out.md"),
    ])
    rc = mod.main()
    assert rc == 1
    out, err = capsys.readouterr()
    assert "--admin" in out or "--admin" in err


# ---------------------------------------------------------------------------
# Test 2: no --fix or --apply option exists
# ---------------------------------------------------------------------------

def test_no_fix_or_apply_option():
    """scope_guard.py has no --fix or --apply argument."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "local" / "scope_guard.py"
    content = path.read_text()
    import re
    # Check for --fix or --apply as actual CLI options (not just in help text)
    assert not re.search(r'add_argument\(["\']--fix["\']', content)
    assert not re.search(r'add_argument\(["\']--apply["\']', content)


# ---------------------------------------------------------------------------
# Test 3: clean no-change diff returns SCOPE_CLEAN
# ---------------------------------------------------------------------------

def test_clean_no_change_diff_returns_scope_clean(tmp_path, monkeypatch):
    """When no files differ between base and head, status is SCOPE_CLEAN."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "HEAD",
        "--head-ref", "HEAD",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "SCOPE_CLEAN"


# ---------------------------------------------------------------------------
# Test 4: allow-file permits exact changed file
# ---------------------------------------------------------------------------

def test_allow_file_permits_exact_changed_file(tmp_path, monkeypatch):
    """When allow-file matches the only changed file, status is SCOPE_CLEAN."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "scripts/local/foo.py", "print('hello')\n")
    commit_all(repo, "add foo")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--allow-file", "scripts/local/foo.py",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "SCOPE_CLEAN"
    assert "scripts/local/foo.py" in data["changed_files"]


# ---------------------------------------------------------------------------
# Test 5: allow-glob permits matching changed file
# ---------------------------------------------------------------------------

def test_allow_glob_permits_matching_changed_file(tmp_path, monkeypatch):
    """When allow-glob matches the only changed file, status is SCOPE_CLEAN."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "scripts/local/bar.py", "x = 1\n")
    commit_all(repo, "add bar")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--allow-glob", "scripts/local/*.py",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "SCOPE_CLEAN"


# ---------------------------------------------------------------------------
# Test 6: allowlist provided and extra file changed returns HOLD_SCOPE_VIOLATION
# ---------------------------------------------------------------------------

def test_allowlist_extra_file_returns_scope_violation(tmp_path, monkeypatch):
    """When allowlist is provided but an extra file is changed, HOLD_SCOPE_VIOLATION."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "scripts/local/foo.py", "print('hello')\n")
    add_file(repo, "other.py", "x = 1\n")
    commit_all(repo, "add files")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--allow-file", "scripts/local/foo.py",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "HOLD_SCOPE_VIOLATION"
    assert "other.py" in data["not_allowlisted_files"]


# ---------------------------------------------------------------------------
# Test 7: forbid-file blocks exact changed file
# ---------------------------------------------------------------------------

def test_forbid_file_blocks_exact_changed_file(tmp_path, monkeypatch):
    """When forbid-file matches a changed file, HOLD_SCOPE_VIOLATION."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "scripts/local/foo.py", "print('hello')\n")
    commit_all(repo, "add foo")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--forbid-file", "scripts/local/foo.py",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "HOLD_SCOPE_VIOLATION"
    assert any(v["file"] == "scripts/local/foo.py" for v in data["forbidden_path_matches"])


# ---------------------------------------------------------------------------
# Test 8: forbid-glob blocks matching changed file
# ---------------------------------------------------------------------------

def test_forbid_glob_blocks_matching_changed_file(tmp_path, monkeypatch):
    """When forbid-glob matches a changed file, HOLD_SCOPE_VIOLATION."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "scripts/local/bar.py", "x = 1\n")
    commit_all(repo, "add bar")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--forbid-glob", "scripts/local/*.py",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "HOLD_SCOPE_VIOLATION"


# ---------------------------------------------------------------------------
# Test 9: default forbidden workflow path blocks .github/workflows/x.yml
# ---------------------------------------------------------------------------

def test_default_forbidden_workflow_path_blocks(tmp_path, monkeypatch):
    """Default forbid-glob includes .github/workflows/** which blocks workflow files."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, ".github/workflows/ci.yml", "name: CI\n")
    commit_all(repo, "add workflow")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "HOLD_SCOPE_VIOLATION"
    assert any(
        v["file"] == ".github/workflows/ci.yml"
        for v in data["forbidden_path_matches"]
    )


# ---------------------------------------------------------------------------
# Test 10: default forbidden diff blocks --admin
# ---------------------------------------------------------------------------

def test_default_forbidden_diff_blocks_admin(tmp_path, monkeypatch):
    """Default forbid-diff-regex includes --admin and must flag added lines with it."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "merge.sh", "#!/bin/bash\ngh pr merge 372 --admin\n")
    commit_all(repo, "add merge script")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "HOLD_FORBIDDEN_DIFF_PATTERN"
    assert len(data["forbidden_diff_matches"]) > 0


# ---------------------------------------------------------------------------
# Test 11: default forbidden diff blocks resolveReviewThread
# ---------------------------------------------------------------------------

def test_default_forbidden_diff_blocks_resolve_review_thread(tmp_path, monkeypatch):
    """Default forbid-diff-regex includes resolveReviewThread."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "script.py", "def resolve():\n    return 'resolveReviewThread'\n")
    commit_all(repo, "add script")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "HOLD_FORBIDDEN_DIFF_PATTERN"


# ---------------------------------------------------------------------------
# Test 12: default forbidden diff blocks gh api POST/PATCH/PUT/DELETE
# ---------------------------------------------------------------------------

def test_default_forbidden_diff_blocks_gh_api_mutations(tmp_path, monkeypatch):
    """Default forbid-diff-regex blocks gh api with mutation methods."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "script.sh", "#!/bin/bash\ngh api -X POST /repos/o/r/pulls\n")
    commit_all(repo, "add script")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "HOLD_FORBIDDEN_DIFF_PATTERN"


# ---------------------------------------------------------------------------
# Test 13: default forbidden diff blocks shell=True
# ---------------------------------------------------------------------------

def test_default_forbidden_diff_blocks_shell_true(tmp_path, monkeypatch):
    """Default forbid-diff-regex includes shell=True."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "script.py", "subprocess.run(cmd, shell=True)\n")
    commit_all(repo, "add script")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "HOLD_FORBIDDEN_DIFF_PATTERN"


# ---------------------------------------------------------------------------
# Test 14: default forbidden diff blocks ruff --fix
# ---------------------------------------------------------------------------

def test_default_forbidden_diff_blocks_ruff_fix(tmp_path, monkeypatch):
    """Default forbid-diff-regex includes ruff --fix."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "fix.sh", "#!/bin/bash\nruff --fix\n")
    commit_all(repo, "add fix script")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "HOLD_FORBIDDEN_DIFF_PATTERN"


# ---------------------------------------------------------------------------
# Test 15: companion test allowed for source file when flag is set
# ---------------------------------------------------------------------------

def test_companion_test_allowed_when_flag_set(tmp_path, monkeypatch):
    """With --allow-companion-tests, a test for a changed source is allowed."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "scripts/local/mylib.py", "def foo(): pass\n")
    add_file(repo, "tests/test_mylib.py", "def test_foo(): pass\n")
    commit_all(repo, "add lib and test")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--allow-file", "scripts/local/mylib.py",
        "--allow-companion-tests",
        "--source-path", "scripts/local/mylib.py",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "SCOPE_CLEAN"
    assert "tests/test_mylib.py" in data["companion_test_files"]


# ---------------------------------------------------------------------------
# Test 16: companion test not allowed when flag omitted
# ---------------------------------------------------------------------------

def test_companion_test_not_allowed_when_flag_omitted(tmp_path, monkeypatch):
    """Without --allow-companion-tests, a companion test is flagged as not_allowlisted."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "scripts/local/mylib.py", "def foo(): pass\n")
    add_file(repo, "tests/test_mylib.py", "def test_foo(): pass\n")
    commit_all(repo, "add lib and test")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--allow-file", "scripts/local/mylib.py",
        # NO --allow-companion-tests
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "HOLD_SCOPE_VIOLATION"
    assert "tests/test_mylib.py" in data["not_allowlisted_files"]


# ---------------------------------------------------------------------------
# Test 17: forbidden glob still blocks companion test if matched
# ---------------------------------------------------------------------------

def test_forbidden_glob_blocks_companion_test(tmp_path, monkeypatch):
    """Even with --allow-companion-tests, a forbidden glob match blocks the file."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, ".github/workflows/ci.yml", "name: CI\n")
    commit_all(repo, "add workflow")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--allow-companion-tests",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "HOLD_SCOPE_VIOLATION"
    assert any(
        v["file"] == ".github/workflows/ci.yml"
        for v in data["forbidden_path_matches"]
    )


# ---------------------------------------------------------------------------
# Test 18: scans added lines, not removed lines
# ---------------------------------------------------------------------------

def test_scans_added_lines_not_removed(tmp_path, monkeypatch):
    """A removed line containing --admin is NOT flagged; only added lines are scanned."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    # Add a file with --admin on main
    add_file(repo, "script.sh", "#!/bin/bash\ngh pr merge 372 --admin\n")
    commit_all(repo, "add script with admin")

    # On feature branch, replace with clean content (removes --admin line)
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "script.sh", "#!/bin/bash\necho hello\n")
    commit_all(repo, "update script")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    # No --admin in added lines, so should be clean
    assert data["status"] == "SCOPE_CLEAN"


# ---------------------------------------------------------------------------
# Test 19: max diff lines exceeded returns HOLD_GIT_DIFF_TOO_LARGE
# ---------------------------------------------------------------------------

def test_max_diff_lines_exceeded_returns_hold(tmp_path, monkeypatch):
    """When added lines exceed --max-diff-lines, HOLD_GIT_DIFF_TOO_LARGE is returned."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    # Write a file with many added lines (> 5)
    lines = ["# large file\n"] + [f"line_{i} = {i}\n" for i in range(10)]
    add_file(repo, "big.py", "".join(lines))
    commit_all(repo, "add big")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--max-diff-lines", "5",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["status"] == "HOLD_GIT_DIFF_TOO_LARGE"


# ---------------------------------------------------------------------------
# Test 20: JSON report includes mutated_github=false, modified_files=false, audit_only=true
# ---------------------------------------------------------------------------

def test_json_report_safety_fields(tmp_path, monkeypatch):
    """JSON report always includes mutated_github=false, modified_files=false, audit_only=true."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "scripts/local/foo.py", "print('hello')\n")
    commit_all(repo, "add foo")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--allow-file", "scripts/local/foo.py",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert data["mutated_github"] is False
    assert data["modified_files"] is False
    assert data["audit_only"] is True


# ---------------------------------------------------------------------------
# Test 21: Markdown report says audit-only
# ---------------------------------------------------------------------------

def test_markdown_report_says_audit_only(tmp_path, monkeypatch):
    """Markdown report explicitly states v1 is audit-only and does not modify files."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "scripts/local/foo.py", "print('hello')\n")
    commit_all(repo, "add foo")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--allow-file", "scripts/local/foo.py",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    md_text = md_out.read_text()
    assert "audit-only" in md_text.lower()
    assert "v1" in md_text


# ---------------------------------------------------------------------------
# Test 22: subprocess calls use list args and shell=False
# ---------------------------------------------------------------------------

def test_subprocess_calls_use_list_args():
    """All subprocess.run calls use list argv, not shell=True in code execution."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "local" / "scope_guard.py"
    content = path.read_text()
    import re
    # Find subprocess.run calls with shell=True
    shell_true_calls = re.findall(r'subprocess\.run\([^)]*shell\s*=\s*True', content)
    assert len(shell_true_calls) == 0, f"shell=True found in subprocess.run: {shell_true_calls}"


# ---------------------------------------------------------------------------
# Test 23: status precedence — forbidden diff beats allowlist clean
# ---------------------------------------------------------------------------

def test_status_precedence_forbidden_diff_beats_allowlist(tmp_path, monkeypatch):
    """A forbidden diff pattern takes precedence over a clean allowlist status."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "scripts/local/foo.py", "print('hello')\n")
    add_file(repo, "bad.py", "subprocess.run(cmd, shell=True)\n")
    commit_all(repo, "add files")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--allow-file", "scripts/local/foo.py",
        "--allow-file", "bad.py",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    # Forbidden diff takes precedence
    assert data["status"] == "HOLD_FORBIDDEN_DIFF_PATTERN"


# ---------------------------------------------------------------------------
# Test 24: invalid repo-root returns ERROR_TOOL_FAILURE
# ---------------------------------------------------------------------------

def test_invalid_repo_root_returns_error_tool_failure(tmp_path, monkeypatch, capsys):
    """An invalid or non-existent repo-root causes ERROR_TOOL_FAILURE."""
    mod = load_script()

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", "/this/path/does/not/exist",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 1
    out, err = capsys.readouterr()
    assert "does not exist" in out or "does not exist" in err or "ERROR" in out or "ERROR" in err


# ---------------------------------------------------------------------------
# Test 25: no GitHub API calls are made
# ---------------------------------------------------------------------------

def test_no_github_api_calls_are_made():
    """scope_guard.py makes no network calls to GitHub API."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "local" / "scope_guard.py"
    content = path.read_text()
    assert "gh api" not in content
    assert "github.com" not in content
    assert "requests" not in content
    assert "urllib" not in content


# ---------------------------------------------------------------------------
# Test 26: output includes base_sha and head_sha
# ---------------------------------------------------------------------------

def test_output_includes_base_sha_and_head_sha(tmp_path, monkeypatch):
    """JSON report includes base_sha and head_sha fields."""
    mod = load_script()
    repo = make_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=False, capture_output=True)
    add_file(repo, "scripts/local/foo.py", "print('hello')\n")
    commit_all(repo, "add foo")

    json_out = tmp_path / "scope.json"
    md_out = tmp_path / "scope.md"

    monkeypatch.setattr(sys, "argv", [
        "scope_guard.py",
        "--repo-root", str(repo),
        "--base-ref", "main",
        "--head-ref", "feature",
        "--allow-file", "scripts/local/foo.py",
        "--output-json", str(json_out),
        "--output-md", str(md_out),
    ])
    rc = mod.main()
    assert rc == 0
    data = json.loads(json_out.read_text())
    assert "base_sha" in data
    assert "head_sha" in data
    assert data["base_sha"] != ""
    assert data["head_sha"] != ""
    assert len(data["base_sha"]) == 40  # full SHA
    assert len(data["head_sha"]) == 40  # full SHA
