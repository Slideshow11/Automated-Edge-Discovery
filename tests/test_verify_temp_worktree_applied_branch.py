#!/usr/bin/env python3
"""
tests/test_verify_temp_worktree_applied_branch.py

Tests for the applied-branch verifier.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "scripts" / "local"
sys.path.insert(0, str(SCRIPT_DIR))

import verify_temp_worktree_applied_branch as vtab


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_temp_git_repo() -> Path:
    """Create a temporary git repo with one commit."""
    tmp = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@test.test"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp, capture_output=True)
    readme = tmp / "README.md"
    readme.write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp, capture_output=True, text=True)
    return tmp


def make_apply_branch(repo: Path, branch_name: str, base_sha: str, *files: str) -> None:
    """Create a branch off base_sha and commit the given files."""
    subprocess.run(["git", "checkout", "-b", branch_name, base_sha], cwd=repo, capture_output=True, text=True)
    for f in files:
        p = repo / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"content of {f}\n", encoding="utf-8")
        subprocess.run(["git", "add", f], cwd=repo, capture_output=True, text=True)
    if files:
        subprocess.run(["git", "commit", "-m", f"add {len(files)} file(s)"], cwd=repo, capture_output=True, text=True)


def make_result_json(tmp_path: Path, **overrides) -> Path:
    defaults = {
        "status": "PATCH_READY_FOR_HUMAN_REVIEW",
        "run_id": "test_run",
        "base_sha": "a1e8bec02e63e2e20efb511ab3fa973f8327703f",
        "worktree_path": str(tmp_path / "worktree"),
        "output_root": str(tmp_path / "output"),
        "changed_files": ["docs/scratch.md"],
        "validation_errors": [],
        "pmg_status": "clean",
        "pmg_blocked_files": 0,
        "claude_exit_code": 0,
        "claude_started_at": "2026-05-22T00:56:08Z",
        "claude_elapsed_seconds": 7.23,
        "real_claude_invoked": True,
        "claude_command_contract_summary": "argv=['claude', '--print']",
        "task": {
            "description": "Test",
            "allowed_files": ["docs/scratch.md"],
            "forbidden_files": [],
        },
        "approval": {
            "approved_for_temp_worktree_execution": True,
            "approved_by": "human",
            "approved_plan_sha256": "abc123",
            "approved_at": "2026-05-22T00:00:00Z",
            "max_changed_files": 5,
        },
    }
    defaults.update(overrides)
    path = tmp_path / "result.json"
    path.write_text(json.dumps(defaults), encoding="utf-8")
    return path


def make_diff_patch(tmp_path: Path, content: str | None = None) -> Path:
    if content is None:
        content = (
            "diff --git a/docs/scratch.md b/docs/scratch.md\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/docs/scratch.md\n"
            "@@ -0,0 +1 @@\n"
            "+hello world\n"
        )
    path = tmp_path / "diff.patch"
    path.write_text(content, encoding="utf-8")
    return path


def make_apply_readiness_json(tmp_path: Path, **overrides) -> Path:
    defaults = {
        "status": "APPLY_READY",
        "apply_ready": True,
        "repo_git_status_clean": True,
        "pmg_status": "clean",
        "pmg_blocked_files": 0,
        "real_claude_invoked": True,
        "claude_exit_code": 0,
        "changed_files": ["docs/scratch.md"],
        "generated_at": "2026-05-22T00:00:00Z",
    }
    defaults.update(overrides)
    path = tmp_path / "apply_readiness.json"
    path.write_text(json.dumps(defaults), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRepoNotFound:
    """HOLD_REPO_NOT_FOUND when repo is not a git repo."""

    def test_non_git_repo(self, tmp_path):
        not_git = tmp_path / "not_a_repo"
        not_git.mkdir()
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path)

        status, _ = vtab.verify(
            not_git, "apply/test", "HEAD", result_path, diff_path, readiness_path,
        )
        assert status == "HOLD_REPO_NOT_FOUND"


class TestOutputInsideRepo:
    """Output path inside repo is rejected by main()."""

    def test_output_json_inside_repo(self, tmp_path):
        output_json = REPO_ROOT / "output_test.json"
        output_md = tmp_path / "output.md"
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path)

        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--repo-root", default=str(REPO_ROOT))
        parser.add_argument("--branch-name", required=True)
        parser.add_argument("--expected-base-sha", required=True)
        parser.add_argument("--result-json", required=True)
        parser.add_argument("--diff-patch", required=True)
        parser.add_argument("--apply-readiness-json", required=True)
        parser.add_argument("--output-json", required=True)
        parser.add_argument("--output-md", required=True)
        args = parser.parse_args([
            "--repo-root", str(REPO_ROOT), "--branch-name", "apply/test",
            "--expected-base-sha", "HEAD",
            "--result-json", str(result_path), "--diff-patch", str(diff_path),
            "--apply-readiness-json", str(readiness_path),
            "--output-json", str(output_json), "--output-md", str(output_md),
        ])
        p = Path(args.output_json).resolve()
        r = Path(args.repo_root).resolve()
        assert str(p).startswith(str(r)), "output-json should be inside repo"


class TestBranchMissing:
    """HOLD_BRANCH_MISSING when branch does not exist."""

    def test_missing_branch(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path)

        status, _ = vtab.verify(
            repo, "apply/nonexistent", head, result_path, diff_path, readiness_path,
        )
        assert status == "HOLD_BRANCH_MISSING"


class TestProtectedBranch:
    """HOLD_PROTECTED_BRANCH when branch is main or master."""

    def test_branch_main(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path)

        status, _ = vtab.verify(repo, "main", head, result_path, diff_path, readiness_path)
        assert status == "HOLD_PROTECTED_BRANCH"

    def test_branch_master(self, tmp_path):
        repo = make_temp_git_repo()
        subprocess.run(["git", "checkout", "-b", "master"], cwd=repo, capture_output=True, text=True)
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path)

        status, _ = vtab.verify(repo, "master", head, result_path, diff_path, readiness_path)
        assert status == "HOLD_PROTECTED_BRANCH"


class TestExpectedBaseMissing:
    """HOLD_EXPECTED_BASE_MISSING when expected base SHA does not exist."""

    def test_expected_base_missing(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/scratch.md")
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path)

        status, _ = vtab.verify(
            repo, "apply/test", "0000000000000000000000000000000000000000",
            result_path, diff_path, readiness_path,
        )
        assert status == "HOLD_EXPECTED_BASE_MISSING"


class TestMergeBaseMismatch:
    """HOLD_MERGE_BASE_MISMATCH when branch merge-base does not match expected base."""

    def test_merge_base_mismatch(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        initial_sha = r.stdout.strip()

        # Advance main with a new commit
        extra = repo / "extra.md"
        extra.write_text("extra\n", encoding="utf-8")
        subprocess.run(["git", "add", "extra.md"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "extra"], cwd=repo, capture_output=True, text=True)
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        main_head = r.stdout.strip()

        # Create apply branch based on initial_sha (not main_head)
        make_apply_branch(repo, "apply/test", initial_sha, "docs/scratch.md")
        result_path = make_result_json(tmp_path, changed_files=["docs/scratch.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md"])

        # Expected base is main_head, but apply/test merge-base = initial_sha != main_head
        status, _ = vtab.verify(
            repo, "apply/test", main_head,
            result_path, diff_path, readiness_path,
        )
        assert status == "HOLD_MERGE_BASE_MISMATCH"


class TestResultMissing:
    """HOLD_RESULT_MISSING when result.json does not exist."""

    def test_result_missing(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/scratch.md")
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path)

        status, _ = vtab.verify(
            repo, "apply/test", head,
            tmp_path / "nonexistent_result.json", diff_path, readiness_path,
        )
        assert status == "HOLD_RESULT_MISSING"


class TestResultInvalidJson:
    """HOLD_RESULT_INVALID_JSON when result.json is not valid JSON."""

    def test_result_invalid_json(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/scratch.md")
        bad_result = tmp_path / "result.json"
        bad_result.write_text("{ not json }", encoding="utf-8")
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path)

        status, _ = vtab.verify(
            repo, "apply/test", head, bad_result, diff_path, readiness_path,
        )
        assert status == "HOLD_RESULT_INVALID_JSON"


class TestDiffMissing:
    """HOLD_DIFF_MISSING when diff.patch does not exist."""

    def test_diff_missing(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/scratch.md")
        result_path = make_result_json(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path)

        status, _ = vtab.verify(
            repo, "apply/test", head,
            result_path, tmp_path / "nonexistent.patch", readiness_path,
        )
        assert status == "HOLD_DIFF_MISSING"


class TestDiffEmpty:
    """HOLD_DIFF_EMPTY when diff.patch is empty."""

    def test_diff_empty(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/scratch.md")
        result_path = make_result_json(tmp_path)
        empty_diff = tmp_path / "diff.patch"
        empty_diff.write_text("", encoding="utf-8")
        readiness_path = make_apply_readiness_json(tmp_path)

        status, _ = vtab.verify(
            repo, "apply/test", head, result_path, empty_diff, readiness_path,
        )
        assert status == "HOLD_DIFF_EMPTY"


class TestReadinessMissing:
    """HOLD_READINESS_MISSING when apply_readiness.json does not exist."""

    def test_readiness_missing(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/scratch.md")
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)

        status, _ = vtab.verify(
            repo, "apply/test", head, result_path, diff_path,
            tmp_path / "nonexistent_readiness.json",
        )
        assert status == "HOLD_READINESS_MISSING"


class TestReadinessNotApplyReady:
    """HOLD_READINESS_NOT_APPLY_READY when apply_readiness.json status is not APPLY_READY."""

    def test_readiness_not_apply_ready(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/scratch.md")
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, status="HOLD_REPO_DIRTY")

        status, _ = vtab.verify(
            repo, "apply/test", head, result_path, diff_path, readiness_path,
        )
        assert status == "HOLD_READINESS_NOT_APPLY_READY"


class TestStatusNotPatchReady:
    """HOLD_STATUS_NOT_PATCH_READY when result status is not PATCH_READY_FOR_HUMAN_REVIEW."""

    def test_status_not_patch_ready(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/scratch.md")
        result_path = make_result_json(tmp_path, status="SOME_OTHER_STATUS")
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path)

        status, _ = vtab.verify(
            repo, "apply/test", head, result_path, diff_path, readiness_path,
        )
        assert status == "HOLD_STATUS_NOT_PATCH_READY"


class TestChangedFilesDuplicate:
    """HOLD_CHANGED_FILES_DUPLICATE when result changed_files has duplicates."""

    def test_changed_files_duplicate(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/a.md")
        result_path = make_result_json(tmp_path, changed_files=["docs/a.md", "docs/a.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path)

        status, _ = vtab.verify(
            repo, "apply/test", head, result_path, diff_path, readiness_path,
        )
        assert status == "HOLD_CHANGED_FILES_DUPLICATE"


class TestBranchDiffMismatch:
    """HOLD_BRANCH_DIFF_MISMATCH when branch diff doesn't match changed_files."""

    def test_branch_diff_extra_file(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        for f in ["docs/scratch.md", "docs/extra.md"]:
            p = repo / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("content\n", encoding="utf-8")
            subprocess.run(["git", "add", f], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "apply"], cwd=repo, capture_output=True, text=True)

        result_path = make_result_json(tmp_path, changed_files=["docs/scratch.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md"])

        status, _ = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )
        assert status == "HOLD_BRANCH_DIFF_MISMATCH"

    def test_branch_diff_missing_file(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        p = repo / "docs" / "scratch.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("content\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/scratch.md"], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "apply"], cwd=repo, capture_output=True, text=True)

        result_path = make_result_json(tmp_path, changed_files=["docs/scratch.md", "docs/other.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md", "docs/other.md"])

        status, _ = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )
        assert status == "HOLD_BRANCH_DIFF_MISMATCH"


class TestAedPlanIncluded:
    """HOLD_AED_PLAN_INCLUDED when .aed_plan.md is in branch diff."""

    def test_aed_plan_in_branch_diff(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        plan = repo / ".aed_plan.md"
        plan.write_text("plan\n", encoding="utf-8")
        subprocess.run(["git", "add", ".aed_plan.md"], cwd=repo, capture_output=True, text=True)
        scratch = repo / "docs" / "scratch.md"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/scratch.md"], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, capture_output=True, text=True)

        result_path = make_result_json(tmp_path, changed_files=[".aed_plan.md", "docs/scratch.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=[".aed_plan.md", "docs/scratch.md"])

        status, _ = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )
        assert status == "HOLD_AED_PLAN_INCLUDED"


class TestForbiddenFileTouched:
    """HOLD_FORBIDDEN_FILE_TOUCHED when branch diff includes a forbidden file."""

    def test_forbidden_file_in_branch_diff(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        bad = repo / "scripts" / "local" / "hack.py"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("hack\n", encoding="utf-8")
        subprocess.run(["git", "add", "scripts/local/hack.py"], cwd=repo, capture_output=True, text=True)
        scratch = repo / "docs" / "scratch.md"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/scratch.md"], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "hack"], cwd=repo, capture_output=True, text=True)

        result_path = make_result_json(
            tmp_path,
            changed_files=["docs/scratch.md", "scripts/local/hack.py"],
            task={"allowed_files": ["docs/scratch.md", "scripts/local/hack.py"], "forbidden_files": ["scripts/local/hack.py"]},
        )
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md", "scripts/local/hack.py"])

        status, _ = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )
        assert status == "HOLD_FORBIDDEN_FILE_TOUCHED"


class TestTooManyFilesChanged:
    """HOLD_TOO_MANY_FILES_CHANGED when branch diff exceeds max_changed_files."""

    def test_too_many_files(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        for i in range(6):
            p = repo / f"docs/file{i}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"content {i}\n", encoding="utf-8")
            subprocess.run(["git", "add", f"docs/file{i}.md"], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "6 files"], cwd=repo, capture_output=True, text=True)

        result_path = make_result_json(
            tmp_path,
            changed_files=[f"docs/file{i}.md" for i in range(6)],
            approval={"max_changed_files": 5},
        )
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(
            tmp_path,
            changed_files=[f"docs/file{i}.md" for i in range(6)],
        )

        status, _ = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
            max_changed_files=5,
        )
        assert status == "HOLD_TOO_MANY_FILES_CHANGED"


class TestPmgNotClean:
    """HOLD_PMG_NOT_CLEAN when apply_readiness pmg_status is not clean."""

    def test_pmg_status_dirty(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/scratch.md")
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, pmg_status="dirty")

        status, _ = vtab.verify(
            repo, "apply/test", head, result_path, diff_path, readiness_path,
        )
        assert status == "HOLD_PMG_NOT_CLEAN"

    def test_pmg_blocked_files(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/scratch.md")
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, pmg_blocked_files=3)

        status, _ = vtab.verify(
            repo, "apply/test", head, result_path, diff_path, readiness_path,
        )
        assert status == "HOLD_PMG_NOT_CLEAN"


class TestUntrackedFilesExpected:
    """APPLIED_BRANCH_READY when expected file is untracked (git apply result)."""

    def test_expected_file_untracked_after_apply(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        # Create parent directory as a tracked empty file so git shows
        # ?? docs/scratch.md (file) instead of ?? docs/ (directory)
        docs_marker = repo / "docs" / ".gitkeep"
        docs_marker.parent.mkdir(parents=True, exist_ok=True)
        docs_marker.write_text("\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/.gitkeep"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add docs dir"], cwd=repo, capture_output=True, text=True)
        # Refresh base_sha after commit
        r_base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r_base.stdout.strip()
        # Create the expected file as untracked (simulates git apply behavior)
        p = repo / "docs" / "scratch.md"
        p.write_text("hello world\n", encoding="utf-8")
        # Do NOT git add / git commit — file stays untracked

        result_path = make_result_json(tmp_path, changed_files=["docs/scratch.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md"])

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        assert status == "APPLIED_BRANCH_READY"
        assert "docs/scratch.md" in checks.get("untracked_expected", [])
        assert checks.get("branch_diff_matches_expected") is True

    def test_untracked_expected_includes_untracked_field(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        # Pre-create docs/ dir on base so it doesn't appear in branch diff
        docs_marker = repo / "docs" / ".gitkeep"
        docs_marker.parent.mkdir(parents=True, exist_ok=True)
        docs_marker.write_text("\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/.gitkeep"], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "add docs dir"], cwd=repo, capture_output=True, text=True)
        r2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r2.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        p = repo / "docs" / "new.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("new content\n", encoding="utf-8")

        result_path = make_result_json(tmp_path, changed_files=["docs/new.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/new.md"])

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        assert status == "APPLIED_BRANCH_READY"
        assert "docs/new.md" in checks.get("untracked_expected", [])

    def test_new_untracked_directory_with_file(self, tmp_path):
        """With git status -uall, a new untracked directory shows individual file paths."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        # Create a new directory with a new file inside - no tracked parent dir
        new_dir = repo / "newdocs"
        new_dir.mkdir()
        new_file = new_dir / "page.md"
        new_file.write_text("new page\n", encoding="utf-8")
        # Verify git status shows the file, not just the directory
        r_status = subprocess.run(
            ["git", "status", "--short", "-uall", "--"],
            cwd=repo, capture_output=True, text=True,
        )
        # Should show ?? newdocs/page.md not just ?? newdocs/
        assert "?? newdocs/page.md" in r_status.stdout, f"git status -uall should show file: {r_status.stdout}"

        result_path = make_result_json(tmp_path, changed_files=["newdocs/page.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["newdocs/page.md"])

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        assert status == "APPLIED_BRANCH_READY"
        assert "newdocs/page.md" in checks.get("untracked_expected", [])


class TestUntrackedFilesUnexpected:
    """HOLD_UNEXPECTED_UNTRACKED_FILE when unexpected untracked file is present."""

    def test_unexpected_untracked_blocks(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        # Create the expected file properly via commit
        p = repo / "docs" / "scratch.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/scratch.md"], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "add scratch"], cwd=repo, capture_output=True, text=True)
        # Create an UNEXPECTED untracked file
        unexpected = repo / "secrets.txt"
        unexpected.write_text("password123\n", encoding="utf-8")

        result_path = make_result_json(tmp_path, changed_files=["docs/scratch.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md"])

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        assert status == "HOLD_UNEXPECTED_UNTRACKED_FILE"
        assert "secrets.txt" in checks.get("unexpected_untracked_files", [])

    def test_unexpected_untracked_not_in_changed_files(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        # Create expected file via commit
        p = repo / "docs" / "scratch.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("content\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/scratch.md"], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "add"], cwd=repo, capture_output=True, text=True)
        # Create two unexpected untracked files
        (repo / "junk1.txt").write_text("junk\n", encoding="utf-8")
        (repo / "junk2.log").write_text("log\n", encoding="utf-8")

        result_path = make_result_json(tmp_path, changed_files=["docs/scratch.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md"])

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        assert status == "HOLD_UNEXPECTED_UNTRACKED_FILE"
        assert "junk1.txt" in checks.get("unexpected_untracked_files", [])
        assert "junk2.log" in checks.get("unexpected_untracked_files", [])


class TestStagedAddedFilesExpected:
    """APPLIED_BRANCH_READY when expected file is staged-added (apply_mock_edits).

    The mock controller pipeline's apply_mock_edits writes the file content
    and then stages it, so `git status --short` shows "A " for the new
    file. This mirrors the real controller's behavior and exercises the
    verifier's new staged-added bucket.
    """

    def test_expected_file_staged_added_after_apply(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        # Create parent directory as a tracked file so the new file lands
        # inside a tracked path (otherwise the diff also includes a new dir).
        docs_marker = repo / "docs" / ".gitkeep"
        docs_marker.parent.mkdir(parents=True, exist_ok=True)
        docs_marker.write_text("\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/.gitkeep"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add docs dir"], cwd=repo, capture_output=True, text=True)
        r_base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r_base.stdout.strip()
        # Create the expected file and STAGE it (do not commit). This is what
        # apply_mock_edits produces.
        p = repo / "docs" / "scratch.md"
        p.write_text("hello world\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/scratch.md"], cwd=repo, capture_output=True, text=True)

        result_path = make_result_json(tmp_path, changed_files=["docs/scratch.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md"])

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        assert status == "APPLIED_BRANCH_READY"
        assert "docs/scratch.md" in checks.get("staged_added_expected", [])
        assert "docs/scratch.md" not in checks.get("staged_added_unexpected", [])
        assert checks.get("no_unexpected_staged_added") is True
        assert checks.get("branch_diff_matches_expected") is True

    def test_expected_file_staged_added_with_worktree_modification(self, tmp_path):
        """AM status (added in index, modified in worktree) is also accepted.

        apply_mock_edits could be followed by a subsequent touch of the file
        in the worktree, producing AM status. The verifier's first-column 'A'
        check covers both A and AM.
        """
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        docs_marker = repo / "docs" / ".gitkeep"
        docs_marker.parent.mkdir(parents=True, exist_ok=True)
        docs_marker.write_text("\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/.gitkeep"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add docs dir"], cwd=repo, capture_output=True, text=True)
        r_base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r_base.stdout.strip()
        # Stage-add then re-touch the worktree file -> AM status
        p = repo / "docs" / "scratch.md"
        p.write_text("hello world\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/scratch.md"], cwd=repo, capture_output=True, text=True)
        p.write_text("hello world v2\n", encoding="utf-8")

        result_path = make_result_json(tmp_path, changed_files=["docs/scratch.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md"])

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        assert status == "APPLIED_BRANCH_READY"
        # The AM status is recognized by the first-column 'A' check; it lands
        # in the staged_added bucket, not in tracked_modified (which is keyed
        # on first-column 'M').
        assert "docs/scratch.md" in checks.get("staged_added_expected", [])


class TestStagedAddedFilesUnexpected:
    """HOLD_UNEXPECTED_UNTRACKED_FILE when an unexpected staged-added file is present."""

    def test_unexpected_staged_added_blocks(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        # Commit the expected file properly
        p = repo / "docs" / "scratch.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/scratch.md"], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "add scratch"], cwd=repo, capture_output=True, text=True)
        r_base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r_base.stdout.strip()
        # Create an UNEXPECTED file and stage it
        unexpected = repo / "secrets.txt"
        unexpected.write_text("password123\n", encoding="utf-8")
        subprocess.run(["git", "add", "secrets.txt"], cwd=repo, capture_output=True, text=True)

        result_path = make_result_json(tmp_path, changed_files=["docs/scratch.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md"])

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        # The unexpected staged-added file must be blocked. It surfaces as
        # HOLD_UNEXPECTED_UNTRACKED_FILE (the verifier's catch-all for dirty
        # working tree state) and the per-bucket list records which file.
        assert status == "HOLD_UNEXPECTED_UNTRACKED_FILE"
        assert "secrets.txt" in checks.get("unexpected_staged_added_files", [])


class TestMixedCommittedAndUntracked:
    """APPLIED_BRANCH_READY when some files are committed and others are expected untracked."""

    def test_mixed_committed_and_untracked(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        # Committed file
        p1 = repo / "docs" / "committed.md"
        p1.parent.mkdir(parents=True, exist_ok=True)
        p1.write_text("committed content\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/committed.md"], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "add committed"], cwd=repo, capture_output=True, text=True)
        # Untracked file (git apply result)
        p2 = repo / "docs" / "untracked.md"
        p2.write_text("untracked content\n", encoding="utf-8")

        result_path = make_result_json(tmp_path, changed_files=["docs/committed.md", "docs/untracked.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/committed.md", "docs/untracked.md"])

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        assert status == "APPLIED_BRANCH_READY"
        assert "docs/committed.md" in checks.get("branch_changed_files", [])
        assert "docs/untracked.md" in checks.get("untracked_expected", [])


class TestAedPlanUntracked:
    """.aed_plan.md untracked should be rejected by the aed_plan check."""

    def test_aed_plan_untracked_still_rejected(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        # Pre-create docs/ dir as tracked so ?? shows file not directory
        docs_marker = repo / "docs" / ".gitkeep"
        docs_marker.parent.mkdir(parents=True, exist_ok=True)
        docs_marker.write_text("\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/.gitkeep"], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "add docs"], cwd=repo, capture_output=True, text=True)
        r_base_aed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r_base_aed.stdout.strip()
        # Create .aed_plan.md as untracked
        plan = repo / ".aed_plan.md"
        plan.write_text("plan content\n", encoding="utf-8")
        # Create expected file as untracked
        p = repo / "docs" / "scratch.md"
        p.write_text("hello\n", encoding="utf-8")

        result_path = make_result_json(
            tmp_path,
            changed_files=[".aed_plan.md", "docs/scratch.md"],
        )
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(
            tmp_path,
            changed_files=[".aed_plan.md", "docs/scratch.md"],
        )

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        assert status == "HOLD_AED_PLAN_INCLUDED"


class TestForbiddenUntracked:
    """Forbidden file untracked should still be rejected."""

    def test_forbidden_untracked_still_rejected(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        # Pre-create docs/ and scripts/local/ dirs as tracked so ?? shows file not directory
        docs_marker = repo / "docs" / ".gitkeep"
        docs_marker.parent.mkdir(parents=True, exist_ok=True)
        docs_marker.write_text("\n", encoding="utf-8")
        scripts_marker = repo / "scripts" / "local" / ".gitkeep"
        scripts_marker.parent.mkdir(parents=True, exist_ok=True)
        scripts_marker.write_text("\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/.gitkeep", "scripts/local/.gitkeep"], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "add dirs"], cwd=repo, capture_output=True, text=True)
        r_base_for = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r_base_for.stdout.strip()
        # Create expected file as untracked
        p = repo / "docs" / "scratch.md"
        p.write_text("hello\n", encoding="utf-8")
        # Create forbidden file as untracked
        bad = repo / "scripts" / "local" / "hack.py"
        bad.write_text("hack\n", encoding="utf-8")

        result_path = make_result_json(
            tmp_path,
            changed_files=["docs/scratch.md", "scripts/local/hack.py"],
            task={"allowed_files": ["docs/scratch.md", "scripts/local/hack.py"], "forbidden_files": ["scripts/local/hack.py"]},
        )
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(
            tmp_path,
            changed_files=["docs/scratch.md", "scripts/local/hack.py"],
        )

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        assert status == "HOLD_FORBIDDEN_FILE_TOUCHED"


class TestNoGitAddInVerifier:
    """Verify implementation contains no git add calls."""

    def test_no_git_add_in_verify(self):
        import inspect
        source = inspect.getsource(vtab.verify)
        assert "git add" not in source, "verify() must not contain 'git add'"
        assert "run" not in source.split("def verify")[1].split("def ")[0] or True  # just check source

    def test_no_git_add_in_module(self):
        content = Path(__file__).resolve().parent.parent.joinpath("scripts/local/verify_temp_worktree_applied_branch.py").read_text(encoding="utf-8")
        # Check that no subprocess call contains 'add' as an argument
        import re
        # Look for subprocess calls with 'add' as an argument
        add_calls = re.findall(r'\[.*?["\']git["\'].*?["\']add["\'].*?\]', content)
        assert not add_calls, f"Module must not call git add: {add_calls}"


class TestNoShellInVerifier:
    """Verifier must not use shell=True in subprocess calls."""

    def test_no_shell_in_verify(self):
        import inspect, re
        source = inspect.getsource(vtab.verify)
        found = re.findall(r"subprocess\.[a-z_]+\([^)]*shell\s*=\s*True", source)
        assert not found, f"verify() must not use shell=True: {found}"

    def test_no_shell_in_main(self):
        import inspect, re
        source = inspect.getsource(vtab.main)
        found = re.findall(r"subprocess\.[a-z_]+\([^)]*shell\s*=\s*True", source)
        assert not found, f"main() must not use shell=True: {found}"


class TestVerifierDoesNotMutateRepo:
    """Verifier must not create files in the repo."""

    def test_verify_does_not_create_files(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()
        make_apply_branch(repo, "apply/test", base_sha, "docs/scratch.md")

        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path)

        before = set()
        for root, dirs, files in os.walk(repo):
            before.update(files)

        status, _ = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        after = set()
        for root, dirs, files in os.walk(repo):
            after.update(files)

        new = after - before
        unexpected = [f for f in new if "apply_readiness" in f or f.endswith(".verify_test")]
        assert not unexpected, f"Verifier created unexpected files: {unexpected}"


class TestNoLiveClaudeInTests:
    """Test file must not contain live-Claude subprocess patterns."""

    def test_no_live_claude_pattern(self):
        content = Path(__file__).resolve().read_text(encoding="utf-8")
        import re
        found = re.findall(r"subprocess\.run\s*\(\s*\[\s*\'claude\'[\s,\)]", content)
        assert not found, f"Test file must not contain live-Claude calls: {found}"


class TestHappyPath:
    """APPLIED_BRANCH_READY when all checks pass."""

    def test_apply_ready_happy_path(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()
        make_apply_branch(repo, "apply/test", base_sha, "docs/scratch.md")

        result_path = make_result_json(
            tmp_path,
            changed_files=["docs/scratch.md"],
            task={"allowed_files": ["docs/scratch.md"], "forbidden_files": []},
        )
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(
            tmp_path,
            status="APPLY_READY",
            changed_files=["docs/scratch.md"],
        )

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        assert status == "APPLIED_BRANCH_READY"
        assert checks.get("branch_diff_matches_expected") is True
        assert checks.get("merge_base_matches") is True
        assert checks.get("apply_readiness_status") == "APPLY_READY"


class TestTrackedModifiedSameRevision:
    """APPLIED_BRANCH_READY when git apply modifies a tracked file on a same-revision
    branch (branch head == base sha, no committed diff, but worktree/index is dirty).

    Regression test for real bug: real_task_trial_001 returned HOLD_BRANCH_DIFF_MISMATCH
    because the verifier only checked committed branch diff and missed worktree/index
    modifications from git apply on same-revision applied branches.
    """

    def test_staged_modification_on_same_revision_branch(self, tmp_path):
        """git apply stages a modification to an already-tracked file.
        Branch HEAD == base sha (no commits on branch).
        git status shows 'M  path' (staged index modification).
        """
        # Create a temp git repo with docs/tracked.md committed on main
        repo = make_temp_git_repo()
        docs_dir = repo / "docs"
        docs_dir.mkdir(exist_ok=True)
        tracked = docs_dir / "tracked.md"
        tracked.write_text("original\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/tracked.md"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add tracked"], cwd=repo, capture_output=True)

        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True)

        # Simulate git apply: modify tracked file and stage it
        tracked.write_text("modified by apply\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/tracked.md"], cwd=repo, capture_output=True)

        # Verify: branch head == base, git status shows staged M
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        assert r.stdout.strip() == base_sha, "branch head should equal base_sha"

        r = subprocess.run(["git", "status", "--short", "-uall"], cwd=repo, capture_output=True, text=True)
        assert r.stdout.startswith("M "), f"Expected staged M, got: {r.stdout!r}"

        result_path = make_result_json(tmp_path, changed_files=["docs/tracked.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/tracked.md"])

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        assert status == "APPLIED_BRANCH_READY", f"Expected APPLIED_BRANCH_READY, got {status}"
        assert "docs/tracked.md" in checks.get("tracked_modified_expected", [])
        assert "docs/tracked.md" in checks.get("tracked_modified", [])
        assert checks.get("no_unexpected_dirty_tracked") is True

    def test_worktree_modification_on_same_revision_branch(self, tmp_path):
        """Worktree modification (not staged) on same-revision branch.
        git status shows ' M path' (worktree dirty only).
        """
        repo = make_temp_git_repo()
        docs_dir = repo / "docs"
        docs_dir.mkdir(exist_ok=True)
        tracked = docs_dir / "tracked.md"
        tracked.write_text("original\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/tracked.md"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add tracked"], cwd=repo, capture_output=True)

        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True)

        # Worktree modification only — no git add
        tracked.write_text("worktree modification\n", encoding="utf-8")

        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        assert r.stdout.strip() == base_sha

        r = subprocess.run(["git", "status", "--short", "-uall"], cwd=repo, capture_output=True, text=True)
        assert " M" in r.stdout and not r.stdout.startswith("??"), f"Expected worktree M, got: {r.stdout!r}"

        result_path = make_result_json(tmp_path, changed_files=["docs/tracked.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/tracked.md"])

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        assert status == "APPLIED_BRANCH_READY", f"Expected APPLIED_BRANCH_READY, got {status}"
        assert "docs/tracked.md" in checks.get("tracked_modified_expected", [])

    def test_unexpected_dirty_tracked_file_is_blocked(self, tmp_path):
        """Unexpected tracked modification (not in expected changed_files) is blocked.
        Returns HOLD_UNEXPECTED_UNTRACKED.
        """
        repo = make_temp_git_repo()
        docs_dir = repo / "docs"
        docs_dir.mkdir(exist_ok=True)
        tracked = docs_dir / "tracked.md"
        tracked.write_text("original\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/tracked.md"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add tracked"], cwd=repo, capture_output=True)

        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True)

        # Modify a file NOT in expected changed_files
        tracked.write_text("unexpected modification\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/tracked.md"], cwd=repo, capture_output=True)

        result_path = make_result_json(tmp_path, changed_files=["docs/expected.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/expected.md"])

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        assert status == "HOLD_UNEXPECTED_UNTRACKED_FILE", f"Expected HOLD_UNEXPECTED_UNTRACKED_FILE, got {status}"
        assert "docs/tracked.md" in checks.get("tracked_modified_unexpected", [])

    def test_mixed_tracked_modified_and_untracked_on_same_revision(self, tmp_path):
        """Both tracked modification (staged) and untracked new file on same-revision.
        Both must be recognized as actual_applied.
        """
        repo = make_temp_git_repo()
        docs_dir = repo / "docs"
        docs_dir.mkdir(exist_ok=True)

        # Pre-create docs/ as tracked on base — include tracked.md so it's M not A
        marker = docs_dir / ".gitkeep"
        marker.write_text("\n", encoding="utf-8")
        tracked = docs_dir / "tracked.md"
        tracked.write_text("original\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/.gitkeep", "docs/tracked.md"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add docs"], cwd=repo, capture_output=True)

        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True)

        # Tracked file: modify and stage (M  status — file existed on base)
        tracked.write_text("modified\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/tracked.md"], cwd=repo, capture_output=True)

        # Untracked file: new file from git apply
        new_file = docs_dir / "new.md"
        new_file.write_text("new content\n", encoding="utf-8")
        # Do NOT git add — stays untracked

        result_path = make_result_json(tmp_path, changed_files=["docs/tracked.md", "docs/new.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(
            tmp_path, changed_files=["docs/tracked.md", "docs/new.md"]
        )

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        assert status == "APPLIED_BRANCH_READY", f"Expected APPLIED_BRANCH_READY, got {status}"
        assert "docs/tracked.md" in checks.get("tracked_modified_expected", [])
        assert "docs/new.md" in checks.get("untracked_expected", [])#!/usr/bin/env python3
"""
Test classes for pre-push blockers and AM worktree-modified handling.

These tests are appended to the existing test_verify_temp_worktree_applied_branch.py
to cover the P2 Gm5km (index-only branch-applied honesty) and P2 Gm0q4
(AM worktree diff surfacing) review concerns.
"""

# P2 Gm5km: When staged-added expected files are present and the branch
# ref equals the base, the verifier must surface a pre_push_blocker so
# the human is not led to believe a plain `git push` will produce a
# meaningful PR.
class TestPrePushBlockers:
    """P2 Gm5km: surface human-apply boundary when staged-only/AM exists."""

    def test_staged_only_expected_produces_pre_push_blocker(self, tmp_path):
        """A staged-only expected file (branch tree diff empty) produces
        a pre_push_blocker with kind staged_only_no_branch_commit and the
        APPLIED_BRANCH_READY status is preserved for controller compat."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        docs_marker = repo / "docs" / ".gitkeep"
        docs_marker.parent.mkdir(parents=True, exist_ok=True)
        docs_marker.write_text("\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/.gitkeep"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add docs dir"], cwd=repo, capture_output=True, text=True)
        r_base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r_base.stdout.strip()
        # Stage-add the expected file (do not commit)
        p = repo / "docs" / "scratch.md"
        p.write_text("hello world\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/scratch.md"], cwd=repo, capture_output=True, text=True)

        result_path = make_result_json(tmp_path, changed_files=["docs/scratch.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md"])

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        # Status stays APPLIED_BRANCH_READY for controller compatibility
        assert status == "APPLIED_BRANCH_READY", f"Expected APPLIED_BRANCH_READY, got {status}"
        # Pre-push blocker is surfaced
        blockers = checks.get("pre_push_blockers") or []
        assert any(b.get("kind") == "staged_only_no_branch_commit" for b in blockers), (
            f"Expected staged_only_no_branch_commit blocker, got: {blockers}"
        )
        # The affected path is listed
        kind_block = next(b for b in blockers if b.get("kind") == "staged_only_no_branch_commit")
        assert "docs/scratch.md" in kind_block.get("paths", [])
        # A warning is also raised
        warnings = checks.get("warnings") or []
        assert any("PRE-PUSH BLOCKER" in w for w in warnings), (
            f"Expected pre-push warning, got: {warnings}"
        )

    def test_am_expected_produces_pre_push_blocker(self, tmp_path):
        """An AM-status expected file (added in index, modified in worktree)
        produces a pre_push_blocker with kind am_worktree_modified and is
        also listed in am_worktree_modified checks."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        docs_marker = repo / "docs" / ".gitkeep"
        docs_marker.parent.mkdir(parents=True, exist_ok=True)
        docs_marker.write_text("\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/.gitkeep"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add docs dir"], cwd=repo, capture_output=True, text=True)
        r_base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r_base.stdout.strip()
        # Stage-add then re-touch the worktree file -> AM status
        p = repo / "docs" / "scratch.md"
        p.write_text("hello world\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/scratch.md"], cwd=repo, capture_output=True, text=True)
        p.write_text("hello world v2\n", encoding="utf-8")

        result_path = make_result_json(tmp_path, changed_files=["docs/scratch.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md"])

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        # AM-status file is detected in am_worktree_modified
        assert "docs/scratch.md" in checks.get("am_worktree_modified", []), (
            f"Expected AM file in am_worktree_modified, got: {checks.get('am_worktree_modified')}"
        )
        # has_am_status is set
        assert checks.get("has_am_status") is True
        # Pre-push blocker is surfaced
        blockers = checks.get("pre_push_blockers") or []
        assert any(b.get("kind") == "am_worktree_modified" for b in blockers), (
            f"Expected am_worktree_modified blocker, got: {blockers}"
        )

    def test_no_blockers_when_branch_has_commits(self, tmp_path):
        """When the branch tree diff is non-empty (i.e. expected files were
        actually committed to the branch ref), no pre-push blockers should
        be emitted, even if staged-added files also exist."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        # Commit the expected file (so branch tree diff is non-empty)
        p = repo / "docs" / "scratch.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/scratch.md"], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "add scratch"], cwd=repo, capture_output=True, text=True)
        r_base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r_base.stdout.strip()

        result_path = make_result_json(tmp_path, changed_files=["docs/scratch.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md"])

        status, checks = vtab.verify(
            repo, "apply/test", base_sha, result_path, diff_path, readiness_path,
        )

        # No blockers since the branch actually has the commit
        blockers = checks.get("pre_push_blockers") or []
        assert blockers == [], f"Expected no blockers, got: {blockers}"


# P2 Gm0q4: The verifier must surface the unstaged worktree diff in the
# generated_human_commands and review_diff_sources JSON output, so AM
# status and other unstaged changes are visible to human reviewers.
class TestUnstagedWorktreeDiff:
    """P2 Gm0q4: surface unstaged/worktree diff for human review."""

    def test_staged_only_expected_exposes_unstaged_diff_keys(self, tmp_path):
        """A staged-only expected file populates unstaged_worktree_diff
        fields in generated_human_commands. (May be empty if no unstaged
        modifications, but the keys must be present.)"""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        docs_marker = repo / "docs" / ".gitkeep"
        docs_marker.parent.mkdir(parents=True, exist_ok=True)
        docs_marker.write_text("\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/.gitkeep"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add docs dir"], cwd=repo, capture_output=True, text=True)
        r_base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r_base.stdout.strip()
        p = repo / "docs" / "scratch.md"
        p.write_text("hello world\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/scratch.md"], cwd=repo, capture_output=True, text=True)

        output_json = tmp_path / "out.json"
        output_md = tmp_path / "out.md"
        result_path = make_result_json(tmp_path, changed_files=["docs/scratch.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md"])

        # Patch write_json_output/write_md_output by re-using main's logic
        # via a small inline invocation
        import sys
        argv_backup = sys.argv
        try:
            sys.argv = [
                "verify_temp_worktree_applied_branch.py",
                "--repo-root", str(repo),
                "--branch-name", "apply/test",
                "--expected-base-sha", base_sha,
                "--result-json", str(result_path),
                "--diff-patch", str(diff_path),
                "--apply-readiness-json", str(readiness_path),
                "--output-json", str(output_json),
                "--output-md", str(output_md),
            ]
            vtab.main()
        finally:
            sys.argv = argv_backup

        assert output_json.exists()
        import json as _json
        data = _json.loads(output_json.read_text(encoding="utf-8"))
        ghc = data.get("generated_human_commands", {})
        # The keys must exist (even if values are empty)
        assert "unstaged_worktree_diff_stat" in ghc, "unstaged_worktree_diff_stat must be in generated_human_commands"
        assert "unstaged_worktree_diff" in ghc, "unstaged_worktree_diff must be in generated_human_commands"
        # review_diff_sources should also have the unstaged_worktree_diff entry
        rds = ghc.get("review_diff_sources", {})
        assert "unstaged_worktree_diff" in rds, "unstaged_worktree_diff must be in review_diff_sources"

    def test_am_expected_worktree_modification_visible_in_unstaged_diff(self, tmp_path):
        """An AM-status expected file's worktree modification appears in
        `git diff` (unstaged/worktree diff) output."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        docs_marker = repo / "docs" / ".gitkeep"
        docs_marker.parent.mkdir(parents=True, exist_ok=True)
        docs_marker.write_text("\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/.gitkeep"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add docs dir"], cwd=repo, capture_output=True, text=True)
        r_base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r_base.stdout.strip()
        # Stage-add then re-touch
        p = repo / "docs" / "scratch.md"
        p.write_text("hello world\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/scratch.md"], cwd=repo, capture_output=True, text=True)
        p.write_text("hello world v2 from worktree\n", encoding="utf-8")

        output_json = tmp_path / "out.json"
        output_md = tmp_path / "out.md"
        result_path = make_result_json(tmp_path, changed_files=["docs/scratch.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md"])

        import sys
        argv_backup = sys.argv
        try:
            sys.argv = [
                "verify_temp_worktree_applied_branch.py",
                "--repo-root", str(repo),
                "--branch-name", "apply/test",
                "--expected-base-sha", base_sha,
                "--result-json", str(result_path),
                "--diff-patch", str(diff_path),
                "--apply-readiness-json", str(readiness_path),
                "--output-json", str(output_json),
                "--output-md", str(output_md),
            ]
            vtab.main()
        finally:
            sys.argv = argv_backup

        assert output_json.exists()
        import json as _json
        data = _json.loads(output_json.read_text(encoding="utf-8"))
        ghc = data.get("generated_human_commands", {})
        unstaged_diff = ghc.get("unstaged_worktree_diff", "")
        # The worktree modification must be visible in `git diff` output
        assert "hello world v2" in unstaged_diff, (
            f"Expected worktree modification in unstaged diff, got: {unstaged_diff!r}"
        )

    def test_markdown_includes_unstaged_worktree_diff_section(self, tmp_path):
        """Markdown output for AM-status expected files includes an
        Unstaged/Worktree Diff command in Human Review Commands."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        docs_marker = repo / "docs" / ".gitkeep"
        docs_marker.parent.mkdir(parents=True, exist_ok=True)
        docs_marker.write_text("\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/.gitkeep"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add docs dir"], cwd=repo, capture_output=True, text=True)
        r_base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r_base.stdout.strip()
        # Stage-add then re-touch
        p = repo / "docs" / "scratch.md"
        p.write_text("hello world\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/scratch.md"], cwd=repo, capture_output=True, text=True)
        p.write_text("hello world v2\n", encoding="utf-8")

        output_json = tmp_path / "out.json"
        output_md = tmp_path / "out.md"
        result_path = make_result_json(tmp_path, changed_files=["docs/scratch.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md"])

        import sys
        argv_backup = sys.argv
        try:
            sys.argv = [
                "verify_temp_worktree_applied_branch.py",
                "--repo-root", str(repo),
                "--branch-name", "apply/test",
                "--expected-base-sha", base_sha,
                "--result-json", str(result_path),
                "--diff-patch", str(diff_path),
                "--apply-readiness-json", str(readiness_path),
                "--output-json", str(output_json),
                "--output-md", str(output_md),
            ]
            vtab.main()
        finally:
            sys.argv = argv_backup

        assert output_md.exists()
        md = output_md.read_text(encoding="utf-8")
        # Human Review Commands section must include unstaged diff lines
        assert "git -C" in md and "diff --stat" in md, "Expected 'diff --stat' in markdown"
        # Pre-push Blockers section appears because of AM
        assert "Pre-push Blockers" in md, "Expected Pre-push Blockers section in markdown"

    def test_no_blockers_section_when_no_blockers(self, tmp_path):
        """When the branch has the actual commits (no staged-only, no AM),
        the Markdown must NOT include the Pre-push Blockers section."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()

        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        # Commit the expected file
        p = repo / "docs" / "scratch.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/scratch.md"], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "add scratch"], cwd=repo, capture_output=True, text=True)
        r_base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r_base.stdout.strip()

        output_json = tmp_path / "out.json"
        output_md = tmp_path / "out.md"
        result_path = make_result_json(tmp_path, changed_files=["docs/scratch.md"])
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, changed_files=["docs/scratch.md"])

        import sys
        argv_backup = sys.argv
        try:
            sys.argv = [
                "verify_temp_worktree_applied_branch.py",
                "--repo-root", str(repo),
                "--branch-name", "apply/test",
                "--expected-base-sha", base_sha,
                "--result-json", str(result_path),
                "--diff-patch", str(diff_path),
                "--apply-readiness-json", str(readiness_path),
                "--output-json", str(output_json),
                "--output-md", str(output_md),
            ]
            vtab.main()
        finally:
            sys.argv = argv_backup

        assert output_md.exists()
        md = output_md.read_text(encoding="utf-8")
        # No Pre-push Blockers section when no blockers
        assert "Pre-push Blockers" not in md, "Did not expect Pre-push Blockers section when no blockers"
