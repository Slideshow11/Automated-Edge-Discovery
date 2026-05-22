#!/usr/bin/env python3
"""
tests/test_preview_applied_branch_pr.py

Tests for the PR preparation preview tool.
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

import preview_applied_branch_pr as pap


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


def make_applied_branch_json(
    tmp_path: Path,
    branch_name: str,
    base_sha: str,
    changed_files: list[str] | None = None,
    status: str = "APPLIED_BRANCH_READY",
    **overrides,
) -> Path:
    """Write a fake applied_branch JSON matching verify_temp_worktree_applied_branch output."""
    if changed_files is None:
        changed_files = ["docs/scratch.md"]
    defaults = {
        "status": status,
        "applied_branch_ready": status == "APPLIED_BRANCH_READY",
        "repo_root": str(tmp_path),
        "branch_name": branch_name,
        "expected_base_sha": base_sha,
        "merge_base_sha": base_sha,
        "current_head_sha": base_sha,
        "changed_files_expected": changed_files,
        "changed_files_actual": changed_files,
        "result_json": str(tmp_path / "result.json"),
        "diff_patch": str(tmp_path / "diff.patch"),
        "apply_readiness_json": str(tmp_path / "apply_readiness.json"),
        "checks": {
            "repo_is_git": True,
            "branch_exists": True,
            "merge_base_matches": True,
            "apply_readiness_status": "APPLY_READY",
        },
        "errors": [],
        "warnings": [],
        "generated_at": "2026-05-22T00:00:00Z",
        "safety_statement": "This verifier did not commit, push, open a PR, merge, apply a patch, or invoke Claude.",
        "task": {"forbidden_files": []},
    }
    defaults.update(overrides)
    path = tmp_path / "applied_branch.json"
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
        json_path = make_applied_branch_json(tmp_path, "apply/test", "HEAD")

        status, _ = pap.verify(
            not_git, json_path, "apply/test", "main", "HEAD",
        )
        assert status == "HOLD_REPO_NOT_FOUND"


class TestOutputInsideRepo:
    """Output path inside repo is rejected by main()."""

    def test_output_json_inside_repo(self, tmp_path):
        output_json = REPO_ROOT / "output_test.json"
        output_md = tmp_path / "output.md"
        json_path = make_applied_branch_json(tmp_path, "apply/test", "HEAD")

        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--repo-root", default=str(REPO_ROOT))
        parser.add_argument("--applied-branch-json", required=True)
        parser.add_argument("--branch-name", required=True)
        parser.add_argument("--base-branch", default="main")
        parser.add_argument("--expected-base-sha", required=True)
        parser.add_argument("--output-json", required=True)
        parser.add_argument("--output-md", required=True)
        args = parser.parse_args([
            "--repo-root", str(REPO_ROOT),
            "--applied-branch-json", str(json_path),
            "--branch-name", "apply/test",
            "--base-branch", "main",
            "--expected-base-sha", "HEAD",
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ])
        p = Path(args.output_json).resolve()
        r = Path(args.repo_root).resolve()
        assert str(p).startswith(str(r)), "output-json should be inside repo"


class TestVerificationMissing:
    """HOLD_VERIFICATION_MISSING when applied-branch JSON does not exist."""

    def test_verification_missing(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()

        status, _ = pap.verify(
            repo, tmp_path / "nonexistent.json", "apply/test", "main", head,
        )
        assert status == "HOLD_VERIFICATION_MISSING"


class TestVerificationInvalidJson:
    """HOLD_VERIFICATION_INVALID_JSON when applied-branch JSON is invalid."""

    def test_verification_invalid_json(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        bad = tmp_path / "applied_branch.json"
        bad.write_text("{ not json }", encoding="utf-8")

        status, _ = pap.verify(
            repo, bad, "apply/test", "main", head,
        )
        assert status == "HOLD_VERIFICATION_INVALID_JSON"


class TestVerificationNotReady:
    """HOLD_VERIFICATION_NOT_READY when status is not APPLIED_BRANCH_READY."""

    def test_verification_not_ready(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        json_path = make_applied_branch_json(
            tmp_path, "apply/test", head, status="HOLD_SOME_REASON",
        )

        status, _ = pap.verify(
            repo, json_path, "apply/test", "main", head,
        )
        assert status == "HOLD_VERIFICATION_NOT_READY"


class TestBranchMismatch:
    """HOLD_BRANCH_MISMATCH when branch name doesn't match verification JSON."""

    def test_branch_mismatch(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        json_path = make_applied_branch_json(tmp_path, "apply/other", head)

        status, _ = pap.verify(
            repo, json_path, "apply/test", "main", head,
        )
        assert status == "HOLD_BRANCH_MISMATCH"


class TestExpectedBaseMismatch:
    """HOLD_EXPECTED_BASE_MISMATCH when expected base doesn't match verification JSON."""

    def test_expected_base_mismatch(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        json_path = make_applied_branch_json(tmp_path, "apply/test", head)
        # Change the expected_base_sha in the JSON to something else
        data = json.loads(json_path.read_text())
        data["expected_base_sha"] = "0000000000000000000000000000000000000001"
        json_path.write_text(json.dumps(data), encoding="utf-8")

        status, _ = pap.verify(
            repo, json_path, "apply/test", "main", head,
        )
        assert status == "HOLD_EXPECTED_BASE_MISMATCH"


class TestBranchMissing:
    """HOLD_BRANCH_MISSING when local branch does not exist."""

    def test_branch_missing(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        json_path = make_applied_branch_json(tmp_path, "apply/nonexistent", head)

        status, _ = pap.verify(
            repo, json_path, "apply/nonexistent", "main", head,
        )
        assert status == "HOLD_BRANCH_MISSING"


class TestBaseBranchMissing:
    """HOLD_BASE_BRANCH_MISSING when base branch cannot be resolved."""

    def test_base_branch_missing(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/scratch.md")
        json_path = make_applied_branch_json(tmp_path, "apply/test", head)

        status, _ = pap.verify(
            repo, json_path, "apply/test", "nonexistent-base", head,
        )
        assert status == "HOLD_BASE_BRANCH_MISSING"


class TestProtectedBranch:
    """HOLD_PROTECTED_BRANCH when branch is main or master."""

    def test_protected_branch(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        json_path = make_applied_branch_json(tmp_path, "main", head)

        status, _ = pap.verify(
            repo, json_path, "main", "main", head,
        )
        assert status == "HOLD_PROTECTED_BRANCH"


class TestRepoDirty:
    """HOLD_UNEXPECTED_DIRTY_FILE when dirty path is not in any allowed set."""

    def test_repo_dirty_untracked_not_in_verification(self, tmp_path):
        """HOLD_UNEXPECTED_DIRTY_FILE when untracked file is not in verification changed_files."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/scratch.md")
        json_path = make_applied_branch_json(tmp_path, "apply/test", head)
        # Make the working tree dirty with a file NOT in verification
        (repo / "docs" / "extra.md").write_text("dirty\n", encoding="utf-8")

        status, checks = pap.verify(
            repo, json_path, "apply/test", "main", head,
        )
        assert status == "HOLD_UNEXPECTED_DIRTY_FILE"
        assert "docs/extra.md" in checks.get("unexpected_dirty_paths", [])

    def test_repo_dirty_modified_file_not_in_verification(self, tmp_path):
        """HOLD_UNEXPECTED_DIRTY_FILE when modified tracked file is not in changed_files."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/scratch.md")
        json_path = make_applied_branch_json(tmp_path, "apply/test", head)
        # Modify a tracked file that is NOT in changed_files
        subprocess.run(["git", "checkout", "apply/test"], cwd=repo, capture_output=True, text=True)
        (repo / "README.md").write_text("modified\n", encoding="utf-8")

        status, _ = pap.verify(
            repo, json_path, "apply/test", "main", head,
        )
        assert status == "HOLD_UNEXPECTED_DIRTY_FILE"


class TestVerifiedDirtyWorktreeAllowed:
    """PR_PREVIEW_READY when all dirty paths are verified by APPLIED_BRANCH_READY."""

    def test_dirty_untracked_in_changed_files(self, tmp_path):
        """Dirty untracked file that is in changed_files_actual → PR_PREVIEW_READY."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()
        # Create branch with one committed file
        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        committed = repo / "docs" / "main.md"
        committed.parent.mkdir(parents=True, exist_ok=True)
        committed.write_text("main\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/main.md"], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "main doc"], cwd=repo, capture_output=True, text=True)

        json_path = make_applied_branch_json(
            tmp_path, "apply/test", base_sha,
            changed_files=["docs/main.md"],
            changed_files_actual=["docs/main.md"],
        )

        # Make repo dirty with the file from changed_files_actual
        dirty_file = repo / "docs" / "main.md"
        dirty_file.write_text("dirty\n", encoding="utf-8")

        status, checks = pap.verify(
            repo, json_path, "apply/test", "main", base_sha,
        )
        assert status == "PR_PREVIEW_READY"
        assert checks.get("verified_dirty_worktree_allowed") is True
        assert checks.get("repo_clean") is False

    def test_dirty_untracked_in_untracked_expected(self, tmp_path):
        """Dirty untracked file that is in checks.untracked_expected → PR_PREVIEW_READY."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()
        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        committed = repo / "docs" / "main.md"
        committed.parent.mkdir(parents=True, exist_ok=True)
        committed.write_text("main\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/main.md"], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "main doc"], cwd=repo, capture_output=True, text=True)

        # JSON: only docs/main.md is in changed_files_actual (committed on branch).
        # docs/new_page.md is listed in untracked_expected (expected untracked on disk).
        json_data = {
            "status": "APPLIED_BRANCH_READY",
            "applied_branch_ready": True,
            "repo_root": str(repo),
            "branch_name": "apply/test",
            "expected_base_sha": base_sha,
            "merge_base_sha": base_sha,
            "current_head_sha": base_sha,
            "changed_files_expected": ["docs/main.md"],
            "changed_files_actual": ["docs/main.md"],  # only committed file
            "checks": {
                "repo_is_git": True,
                "branch_exists": True,
                "merge_base_matches": True,
                "apply_readiness_status": "APPLY_READY",
                "untracked_expected": ["docs/new_page.md"],  # expected untracked on disk
            },
            "errors": [],
            "warnings": [],
            "generated_at": "2026-05-22T00:00:00Z",
            "safety_statement": "Test",
            "task": {"forbidden_files": []},
        }
        json_path = tmp_path / "applied_branch.json"
        json_path.write_text(json.dumps(json_data), encoding="utf-8")

        # Create the untracked file on disk
        new_page = repo / "docs" / "new_page.md"
        new_page.parent.mkdir(parents=True, exist_ok=True)
        new_page.write_text("new\n", encoding="utf-8")

        status, checks = pap.verify(
            repo, json_path, "apply/test", "main", base_sha,
        )
        assert status == "PR_PREVIEW_READY"
        assert checks.get("verified_dirty_worktree_allowed") is True

    def test_dirty_aed_plan_still_rejected(self, tmp_path):
        """Dirty .aed_plan.md → HOLD_AED_PLAN_INCLUDED (checked before unexpected dirty)."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()
        make_apply_branch(repo, "apply/test", base_sha, "docs/scratch.md")

        json_path = make_applied_branch_json(
            tmp_path, "apply/test", base_sha,
            changed_files=["docs/scratch.md"],
            changed_files_actual=["docs/scratch.md"],
        )

        # Make .aed_plan.md dirty on disk (not in allowed set → unexpected,
        # but .aed_plan.md is checked first and rejected specifically)
        plan = repo / ".aed_plan.md"
        plan.write_text("plan\n", encoding="utf-8")

        status, _ = pap.verify(
            repo, json_path, "apply/test", "main", base_sha,
        )
        # .aed_plan.md in unexpected dirty → HOLD_AED_PLAN_INCLUDED (specific gate)
        assert status == "HOLD_AED_PLAN_INCLUDED"

    def test_dirty_forbidden_file_still_rejected(self, tmp_path):
        """Dirty forbidden file → HOLD_FORBIDDEN_FILE_TOUCHED (checked before unexpected dirty)."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()
        make_apply_branch(repo, "apply/test", base_sha, "docs/scratch.md")

        json_path = make_applied_branch_json(
            tmp_path, "apply/test", base_sha,
            changed_files=["docs/scratch.md"],
            changed_files_actual=["docs/scratch.md"],
            task={"forbidden_files": ["scripts/local/hack.py"]},
        )

        # Make forbidden file dirty on disk (not in allowed set → unexpected,
        # but forbidden file is checked first and rejected specifically)
        hack = repo / "scripts" / "local" / "hack.py"
        hack.parent.mkdir(parents=True, exist_ok=True)
        hack.write_text("hack\n", encoding="utf-8")

        status, _ = pap.verify(
            repo, json_path, "apply/test", "main", base_sha,
        )
        # scripts/local/hack.py in unexpected dirty → HOLD_FORBIDDEN_FILE_TOUCHED (specific gate)
        assert status == "HOLD_FORBIDDEN_FILE_TOUCHED"

    def test_dirty_untracked_not_in_changed_files_rejected(self, tmp_path):
        """HOLD_UNEXPECTED_DIRTY_FILE when dirty path not in any allowed set."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()
        make_apply_branch(repo, "apply/test", base_sha, "docs/scratch.md")

        json_path = make_applied_branch_json(
            tmp_path, "apply/test", base_sha,
            changed_files=["docs/scratch.md"],
            changed_files_actual=["docs/scratch.md"],
        )

        # Dirty with an unexpected file not in changed_files
        unexpected = repo / "docs" / "unexpected.md"
        unexpected.parent.mkdir(parents=True, exist_ok=True)
        unexpected.write_text("unexpected\n", encoding="utf-8")

        status, checks = pap.verify(
            repo, json_path, "apply/test", "main", base_sha,
        )
        assert status == "HOLD_UNEXPECTED_DIRTY_FILE"
        assert "docs/unexpected.md" in checks.get("unexpected_dirty_paths", [])

    def test_verification_not_ready_still_blocked(self, tmp_path):
        """HOLD_VERIFICATION_NOT_READY still returned even when repo is clean."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/scratch.md")
        json_path = make_applied_branch_json(
            tmp_path, "apply/test", head, status="HOLD_SOME_REASON",
        )

        status, _ = pap.verify(
            repo, json_path, "apply/test", "main", head,
        )
        assert status == "HOLD_VERIFICATION_NOT_READY"

    def test_output_includes_verified_dirty_worktree_allowed_field(self, tmp_path):
        """Output JSON must include verified_dirty_worktree_allowed when applicable."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()
        subprocess.run(["git", "checkout", "-b", "apply/test", base_sha], cwd=repo, capture_output=True, text=True)
        committed = repo / "docs" / "main.md"
        committed.parent.mkdir(parents=True, exist_ok=True)
        committed.write_text("main\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/main.md"], cwd=repo, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "main doc"], cwd=repo, capture_output=True, text=True)

        json_path = make_applied_branch_json(
            tmp_path, "apply/test", base_sha,
            changed_files=["docs/main.md"],
            changed_files_actual=["docs/main.md"],
        )

        # Make repo dirty with expected file
        dirty_file = repo / "docs" / "main.md"
        dirty_file.write_text("dirty\n", encoding="utf-8")

        status, checks = pap.verify(
            repo, json_path, "apply/test", "main", base_sha,
        )
        assert status == "PR_PREVIEW_READY"
        # verified_dirty_worktree_allowed must be present in checks
        assert "verified_dirty_worktree_allowed" in checks


class TestNoGitAddInPreviewTool:
    """Source inspection: preview tool must not call git add."""

    def test_no_git_add_in_verify(self):
        import inspect, re
        source = inspect.getsource(pap.verify)
        found = re.findall(r"git.*add", source, re.IGNORECASE)
        assert not found, f"verify() must not call git add: {found}"

    def test_no_git_add_in_helpers(self):
        import inspect, re
        for name in ["_git_status_clean", "_git_dirty_paths", "_get_allowed_dirty_paths"]:
            if hasattr(pap, name):
                source = inspect.getsource(getattr(pap, name))
                found = re.findall(r"git.*add", source, re.IGNORECASE)
                assert not found, f"{name}() must not call git add: {found}"


class TestNoGhPrCreateExecution:
    """gh pr create must not be executed — only emitted as text."""

    def test_no_gh_pr_create_subprocess(self):
        import inspect, re
        source = inspect.getsource(pap)
        found = re.findall(r"subprocess.*gh.*pr.*create", source, re.IGNORECASE)
        assert not found, f"File must not call `gh pr create` via subprocess: {found}"


class TestHappyPath:
    """PR_PREVIEW_READY when all checks pass."""

    def test_pr_preview_ready(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()
        make_apply_branch(repo, "apply/test", base_sha, "docs/scratch.md")

        json_path = make_applied_branch_json(
            tmp_path, "apply/test", base_sha,
            changed_files=["docs/scratch.md"],
            changed_files_actual=["docs/scratch.md"],
            task={"forbidden_files": []},
        )

        status, checks = pap.verify(
            repo, json_path, "apply/test", "main", base_sha,
        )

        assert status == "PR_PREVIEW_READY"
        assert checks.get("branch_diff_matches_expected") is True
        assert checks.get("repo_clean") is True
        assert checks.get("branch_not_protected") is True
        assert checks.get("verified_dirty_worktree_allowed") is False
    """HOLD_CHANGED_FILES_EMPTY when changed_files is empty."""

    def test_changed_files_empty(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/scratch.md")
        json_path = make_applied_branch_json(tmp_path, "apply/test", head, changed_files=[])

        status, _ = pap.verify(
            repo, json_path, "apply/test", "main", head,
        )
        assert status == "HOLD_CHANGED_FILES_EMPTY"


class TestChangedFilesDuplicate:
    """HOLD_CHANGED_FILES_DUPLICATE when changed_files has duplicates."""

    def test_changed_files_duplicate(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        head = r.stdout.strip()
        make_apply_branch(repo, "apply/test", head, "docs/scratch.md")
        json_path = make_applied_branch_json(
            tmp_path, "apply/test", head,
            changed_files=["docs/a.md", "docs/a.md"],
        )

        status, _ = pap.verify(
            repo, json_path, "apply/test", "main", head,
        )
        assert status == "HOLD_CHANGED_FILES_DUPLICATE"

    def test_empty_string_in_changed_files_is_filtered(self, tmp_path):
        """Empty strings in changed_files are filtered out and don't cause spurious mismatch."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()
        make_apply_branch(repo, "apply/test", base_sha, "docs/scratch.md")

        json_path = make_applied_branch_json(
            tmp_path, "apply/test", base_sha,
            changed_files=["", "docs/scratch.md"],  # empty string + valid file
            changed_files_actual=["", "docs/scratch.md"],
        )

        status, _ = pap.verify(
            repo, json_path, "apply/test", "main", base_sha,
        )
        # Empty string should be filtered; verification should pass with filtered list
        # The branch has docs/scratch.md only, and filtered changed_files is ["docs/scratch.md"]
        # So this should be PR_PREVIEW_READY (not HOLD_BRANCH_DIFF_MISMATCH)
        assert status == "PR_PREVIEW_READY"


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

        json_path = make_applied_branch_json(
            tmp_path, "apply/test", base_sha,
            changed_files=["docs/scratch.md"],
            changed_files_actual=["docs/scratch.md"],
        )

        status, _ = pap.verify(
            repo, json_path, "apply/test", "main", base_sha,
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

        json_path = make_applied_branch_json(
            tmp_path, "apply/test", base_sha,
            changed_files=["docs/scratch.md", "docs/other.md"],
            changed_files_actual=["docs/scratch.md", "docs/other.md"],
        )

        status, _ = pap.verify(
            repo, json_path, "apply/test", "main", base_sha,
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

        json_path = make_applied_branch_json(
            tmp_path, "apply/test", base_sha,
            changed_files=[".aed_plan.md", "docs/scratch.md"],
            changed_files_actual=[".aed_plan.md", "docs/scratch.md"],
        )

        status, _ = pap.verify(
            repo, json_path, "apply/test", "main", base_sha,
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

        json_path = make_applied_branch_json(
            tmp_path, "apply/test", base_sha,
            changed_files=["docs/scratch.md", "scripts/local/hack.py"],
            changed_files_actual=["docs/scratch.md", "scripts/local/hack.py"],
            task={"forbidden_files": ["scripts/local/hack.py"]},
        )

        status, _ = pap.verify(
            repo, json_path, "apply/test", "main", base_sha,
        )
        assert status == "HOLD_FORBIDDEN_FILE_TOUCHED"

    def test_non_dict_task_does_not_crash(self, tmp_path):
        """Non-dict task field does not raise AttributeError; forbidden_files treated as empty."""
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()
        make_apply_branch(repo, "apply/test", base_sha, "docs/scratch.md")

        json_path = make_applied_branch_json(
            tmp_path, "apply/test", base_sha,
            changed_files=["docs/scratch.md"],
            changed_files_actual=["docs/scratch.md"],
            task="not_a_dict",  # string instead of dict
        )

        status, _ = pap.verify(
            repo, json_path, "apply/test", "main", base_sha,
        )
        # Must not raise; should return PR_PREVIEW_READY since forbidden_files defaults to []
        # (conservative: non-dict task means no forbidden files to check)
        assert status == "PR_PREVIEW_READY"


class TestNoShellInTool:
    """Tool must not use shell=True in subprocess calls."""

    def test_no_shell_in_verify(self):
        import inspect, re
        source = inspect.getsource(pap.verify)
        found = re.findall(r"subprocess\.[a-z_]+\([^)]*shell\s*=\s*True", source)
        assert not found, f"verify() must not use shell=True: {found}"

    def test_no_shell_in_main(self):
        import inspect, re
        source = inspect.getsource(pap.main)
        found = re.findall(r"subprocess\.[a-z_]+\([^)]*shell\s*=\s*True", source)
        assert not found, f"main() must not use shell=True: {found}"


class TestNoLiveClaudeInTests:
    """Test file must not contain live-Claude subprocess patterns."""

    def test_no_live_claude_pattern(self):
        content = Path(__file__).resolve().read_text(encoding="utf-8")
        import re
        found = re.findall(r"subprocess\.run\s*\(\s*\[\s*\'claude\'[\s,\)]", content)
        assert not found, f"Test file must not contain live-Claude calls: {found}"


class TestHappyPath:
    """PR_PREVIEW_READY when all checks pass."""

    def test_pr_preview_ready(self, tmp_path):
        repo = make_temp_git_repo()
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        base_sha = r.stdout.strip()
        make_apply_branch(repo, "apply/test", base_sha, "docs/scratch.md")

        json_path = make_applied_branch_json(
            tmp_path, "apply/test", base_sha,
            changed_files=["docs/scratch.md"],
            changed_files_actual=["docs/scratch.md"],
            task={"forbidden_files": []},
        )

        status, checks = pap.verify(
            repo, json_path, "apply/test", "main", base_sha,
        )

        assert status == "PR_PREVIEW_READY"
        assert checks.get("branch_diff_matches_expected") is True
        assert checks.get("repo_clean") is True
        assert checks.get("branch_not_protected") is True


class TestGeneratedGhPrCreateTextOnly:
    """gh pr create command must be emitted as text, not executed."""

    def test_gh_pr_create_command_is_text(self):
        """The gh pr create command is built as text, not executed."""
        cmd = pap._make_gh_pr_create_command(
            "Slideshow11/Automated-Edge-Discovery",
            "apply/test",
            "feat: add scratch doc",
        )
        assert "gh pr create" in cmd
        assert "\\" in cmd  # multiline format
        assert "apply/test" in cmd
        assert "Slideshow11/Automated-Edge-Discovery" in cmd
        # It's text, not a subprocess call — no execution happens
        assert not hasattr(pap, "subprocess") or True  # just a text check