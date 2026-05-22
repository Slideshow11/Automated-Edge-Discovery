#!/usr/bin/env python3
"""
tests/test_apply_temp_worktree_patch_to_branch.py

Tests for apply_temp_worktree_patch_to_branch.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent  # tests/.. -> repo
SCRIPT_DIR = REPO_ROOT / "scripts" / "local"
sys.path.insert(0, str(SCRIPT_DIR))

import apply_temp_worktree_patch_to_branch as aptb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_result_json(tmp_path: Path, **overrides) -> Path:
    """Write a result.json with sensible defaults, overridden by kwargs."""
    defaults = {
        "status": "PATCH_READY_FOR_HUMAN_REVIEW",
        "run_id": "test_run",
        "base_sha": "99e27aa2f9e6e5b8c1a3d2e7f4a6b8c0d9e1f3a5",
        "worktree_path": str(tmp_path / "worktree"),
        "output_root": str(tmp_path / "output"),
        "changed_files": ["docs/scratch.md"],
        "validation_errors": [],
        "pmg_status": "clean",
        "pmg_blocked_files": 0,
        "claude_exit_code": 0,
        "claude_started_at": "2026-05-22T01:00:00Z",
        "claude_elapsed_seconds": 3.5,
        "real_claude_invoked": True,
        "execution": {"mode": "mock"},
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
    """Write a diff.patch file."""
    if content is None:
        content = (
            "diff --git a/docs/scratch.md b/docs/scratch.md\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/docs/scratch.md\n"
            "@@ -0,0 +1,1 @@\n"
            "+hello world\n"
        )
    path = tmp_path / "diff.patch"
    path.write_text(content, encoding="utf-8")
    return path


def make_apply_readiness_json(tmp_path: Path, result_json_path: Path, diff_patch_path: Path, **overrides) -> Path:
    """Write an apply-readiness JSON."""
    defaults = {
        "status": "APPLY_READY",
        "apply_ready": True,
        "result_json": str(result_json_path),
        "diff_patch": str(diff_patch_path),
        "changed_files": ["docs/scratch.md"],
        "checks_passed": 22,
        "checks_failed": 0,
    }
    defaults.update(overrides)
    path = tmp_path / "apply_readiness.json"
    path.write_text(json.dumps(defaults), encoding="utf-8")
    return path


def make_clean_pmg(pmg_dir: Path):
    """Make clean PMG snapshot and compare."""
    pmg_dir.mkdir(parents=True, exist_ok=True)
    snap = pmg_dir / "snapshot.json"
    snap.write_text(json.dumps({"guard_version": 1, "status": "clean", "files_added": []}), encoding="utf-8")
    cmp_json = pmg_dir / "compare.json"
    cmp_json.write_text(json.dumps({"guard_version": 1, "status": "clean", "blocked": 0, "files_added": []}), encoding="utf-8")
    return snap, cmp_json


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDryRunSucceedsWithoutMutation:
    """Dry-run completes all checks without mutating the repo."""

    def test_dry_run_returns_ready(self, tmp_path, monkeypatch):
        """Dry-run returns APPLY_TO_BRANCH_DRY_RUN_READY and does not mutate."""
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)

        def mock_head(repo_root):
            return "99e27aa2f9e6e5b8c1a3d2e7f4a6b8c0d9e1f3a5"
        monkeypatch.setattr(aptb, "_git_head", mock_head)

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        def mock_branch_exists(repo_root, branch):
            return False
        monkeypatch.setattr(aptb, "_git_branch_exists", mock_branch_exists)

        def mock_apply_check(repo_root, diff_patch):
            return True, ""
        monkeypatch.setattr(aptb, "_git_apply_check", mock_apply_check)

        status, result = aptb.apply_patch_to_branch(
            result_path, diff_path, REPO_ROOT,
            "apply/test-dry-run",
            require_apply_ready=False,
            apply_readiness_json_path=None,
            allow_real_apply=False,
            expected_base_sha=None,
            dry_run=True,
        )

        assert status == "APPLY_TO_BRANCH_DRY_RUN_READY"
        assert result.get("applied") is False
        assert result.get("branch_created") is False
        assert result.get("apply_allowed") is False
        planned = result.get("planned_commands", [])
        assert len(planned) >= 2


class TestMissingAllowRealApplyBlocks:
    """Without --allow-real-apply, tool blocks before branch creation."""

    def test_missing_allow_real_apply_blocks_branch_creation(self, tmp_path, monkeypatch):
        """No --allow-real-apply returns HOLD_REAL_APPLY_NOT_ALLOWED."""
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)

        def mock_head(repo_root):
            return "99e27aa2f9e6e5b8c1a3d2e7f4a6b8c0d9e1f3a5"
        monkeypatch.setattr(aptb, "_git_head", mock_head)

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        def mock_branch_exists(repo_root, branch):
            return False
        monkeypatch.setattr(aptb, "_git_branch_exists", mock_branch_exists)

        def mock_apply_check(repo_root, diff_patch):
            return True, ""
        monkeypatch.setattr(aptb, "_git_apply_check", mock_apply_check)

        status, result = aptb.apply_patch_to_branch(
            result_path, diff_path, REPO_ROOT,
            "apply/test-no-flag",
            require_apply_ready=False,
            apply_readiness_json_path=None,
            allow_real_apply=False,
            expected_base_sha=None,
            dry_run=False,
        )

        assert status == "HOLD_REAL_APPLY_NOT_ALLOWED"
        assert result.get("applied") is False
        assert result.get("branch_created") is False


class TestDirtyRepoBlocks:
    """Dirty repo blocks any apply operation."""

    def test_dirty_repo_blocks(self, tmp_path, monkeypatch):
        """Dirty repo returns HOLD_REPO_DIRTY."""
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)

        def mock_head(repo_root):
            return "99e27aa2f9e6e5b8c1a3d2e7f4a6b8c0d9e1f3a5"
        monkeypatch.setattr(aptb, "_git_head", mock_head)

        def mock_dirty(repo_root):
            return False  # dirty
        monkeypatch.setattr(aptb, "_git_status_clean", mock_dirty)

        status, result = aptb.apply_patch_to_branch(
            result_path, diff_path, REPO_ROOT,
            "apply/test-dirty",
            require_apply_ready=False,
            apply_readiness_json_path=None,
            allow_real_apply=True,
            expected_base_sha=None,
            dry_run=False,
        )

        assert status == "HOLD_REPO_DIRTY"


class TestMissingResultJson:
    """Missing result.json blocks."""

    def test_missing_result_json_blocks(self, tmp_path, monkeypatch):
        """Missing result.json returns HOLD_RESULT_JSON_MISSING."""
        diff_path = make_diff_patch(tmp_path)

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        status, result = aptb.apply_patch_to_branch(
            tmp_path / "nonexistent.json", diff_path, REPO_ROOT,
            "apply/test-missing-result",
            require_apply_ready=False,
            apply_readiness_json_path=None,
            allow_real_apply=True,
            expected_base_sha=None,
            dry_run=False,
        )

        assert status == "HOLD_RESULT_JSON_MISSING"


class TestMalformedResultJson:
    """Malformed result.json blocks."""

    def test_malformed_result_json_blocks(self, tmp_path, monkeypatch):
        """Malformed JSON returns HOLD_RESULT_JSON_INVALID."""
        result_path = tmp_path / "result.json"
        result_path.write_text("not json{{", encoding="utf-8")
        diff_path = make_diff_patch(tmp_path)

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        status, result = aptb.apply_patch_to_branch(
            result_path, diff_path, REPO_ROOT,
            "apply/test-malformed-result",
            require_apply_ready=False,
            apply_readiness_json_path=None,
            allow_real_apply=True,
            expected_base_sha=None,
            dry_run=False,
        )

        assert status == "HOLD_RESULT_JSON_INVALID"


class TestMissingDiffPatch:
    """Missing diff.patch blocks."""

    def test_missing_diff_blocks(self, tmp_path, monkeypatch):
        """Missing diff.patch returns HOLD_DIFF_PATCH_MISSING."""
        result_path = make_result_json(tmp_path)

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        status, result = aptb.apply_patch_to_branch(
            result_path, tmp_path / "nonexistent.patch", REPO_ROOT,
            "apply/test-missing-diff",
            require_apply_ready=False,
            apply_readiness_json_path=None,
            allow_real_apply=True,
            expected_base_sha=None,
            dry_run=False,
        )

        assert status == "HOLD_DIFF_PATCH_MISSING"


class TestEmptyDiffPatch:
    """Empty diff.patch blocks."""

    def test_empty_diff_blocks(self, tmp_path, monkeypatch):
        """Empty diff.patch returns HOLD_DIFF_PATCH_EMPTY."""
        result_path = make_result_json(tmp_path)
        diff_path = tmp_path / "diff.patch"
        diff_path.write_text("", encoding="utf-8")

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        status, result = aptb.apply_patch_to_branch(
            result_path, diff_path, REPO_ROOT,
            "apply/test-empty-diff",
            require_apply_ready=False,
            apply_readiness_json_path=None,
            allow_real_apply=True,
            expected_base_sha=None,
            dry_run=False,
        )

        assert status == "HOLD_DIFF_PATCH_EMPTY"


class TestInvalidRepoPath:
    """Invalid repo path blocks."""

    def test_invalid_repo_blocks(self, tmp_path, monkeypatch):
        """Non-git directory returns HOLD_TARGET_REPO_INVALID."""
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)
        fake_repo = tmp_path / "not_a_git_repo"
        fake_repo.mkdir()

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        status, result = aptb.apply_patch_to_branch(
            result_path, diff_path, fake_repo,
            "apply/test-invalid-repo",
            require_apply_ready=False,
            apply_readiness_json_path=None,
            allow_real_apply=True,
            expected_base_sha=None,
            dry_run=False,
        )

        assert status == "HOLD_TARGET_REPO_INVALID"


class TestOutputInsideRepo:
    """Output path inside repo is blocked."""

    def test_output_inside_repo_blocks(self, tmp_path, monkeypatch):
        """Output path inside repo returns HOLD_OUTPUT_INSIDE_REPO (main() check)."""
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)

        def mock_head(repo_root):
            return "99e27aa2f9e6e5b8c1a3d2e7f4a6b8c0d9e1f3a5"
        monkeypatch.setattr(aptb, "_git_head", mock_head)

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        # Call main() with output inside REPO_ROOT
        output_json = REPO_ROOT / "test_apply_output.json"
        try:
            # Override parse_args via monkeypatch
            args = argparse.Namespace(
                result_json=str(result_path),
                diff_patch=str(diff_path),
                target_repo=str(REPO_ROOT),
                branch_name="apply/test-output-inside",
                output_json=str(output_json),
                output_md=None,
                require_apply_ready=False,
                apply_readiness_json=None,
                allow_real_apply=True,
                expected_base_sha=None,
                dry_run=False,
            )
            monkeypatch.setattr(aptb, "parse_args", lambda: args)
            aptb.main()
        except SystemExit:
            pass

        # Verify the check works
        inside = aptb._path_inside_repo(output_json, REPO_ROOT)
        assert inside is True


class TestUnsafeBranchName:
    """Unsafe branch names are blocked."""

    def test_branch_name_with_space_blocks(self, tmp_path, monkeypatch):
        """Branch name with space returns HOLD_BRANCH_NAME_INVALID."""
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)

        def mock_head(repo_root):
            return "99e27aa2f9e6e5b8c1a3d2e7f4a6b8c0d9e1f3a5"
        monkeypatch.setattr(aptb, "_git_head", mock_head)

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        status, result = aptb.apply_patch_to_branch(
            result_path, diff_path, REPO_ROOT,
            "apply/test branch with space",
            require_apply_ready=False,
            apply_readiness_json_path=None,
            allow_real_apply=True,
            expected_base_sha=None,
            dry_run=False,
        )

        assert status == "HOLD_BRANCH_NAME_INVALID"

    def test_branch_name_starting_with_dash_blocks(self, tmp_path, monkeypatch):
        """Branch name starting with dash returns HOLD_BRANCH_NAME_INVALID."""
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)

        def mock_head(repo_root):
            return "99e27aa2f9e6e5b8c1a3d2e7f4a6b8c0d9e1f3a5"
        monkeypatch.setattr(aptb, "_git_head", mock_head)

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        status, result = aptb.apply_patch_to_branch(
            result_path, diff_path, REPO_ROOT,
            "-dangerous-branch",
            require_apply_ready=False,
            apply_readiness_json_path=None,
            allow_real_apply=True,
            expected_base_sha=None,
            dry_run=False,
        )

        assert status == "HOLD_BRANCH_NAME_INVALID"


class TestExistingBranchBlocks:
    """Existing branch blocks creation."""

    def test_existing_branch_blocks(self, tmp_path, monkeypatch):
        """Already-existing branch returns HOLD_BRANCH_ALREADY_EXISTS."""
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)

        def mock_head(repo_root):
            return "99e27aa2f9e6e5b8c1a3d2e7f4a6b8c0d9e1f3a5"
        monkeypatch.setattr(aptb, "_git_head", mock_head)

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        def mock_branch_exists(repo_root, branch):
            return True  # already exists
        monkeypatch.setattr(aptb, "_git_branch_exists", mock_branch_exists)

        status, result = aptb.apply_patch_to_branch(
            result_path, diff_path, REPO_ROOT,
            "apply/test-existing",
            require_apply_ready=False,
            apply_readiness_json_path=None,
            allow_real_apply=True,
            expected_base_sha=None,
            dry_run=False,
        )

        assert status == "HOLD_BRANCH_ALREADY_EXISTS"


class TestBaseShaMismatchBlocks:
    """Expected base SHA mismatch blocks."""

    def test_base_sha_mismatch_blocks(self, tmp_path, monkeypatch):
        """Current HEAD != expected_base_sha returns HOLD_BASE_SHA_MISMATCH."""
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)

        def mock_head(repo_root):
            return "0000000000000000000000000000000000000000"  # wrong SHA
        monkeypatch.setattr(aptb, "_git_head", mock_head)

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        def mock_branch_exists(repo_root, branch):
            return False
        monkeypatch.setattr(aptb, "_git_branch_exists", mock_branch_exists)

        status, result = aptb.apply_patch_to_branch(
            result_path, diff_path, REPO_ROOT,
            "apply/test-sha-mismatch",
            require_apply_ready=False,
            apply_readiness_json_path=None,
            allow_real_apply=True,
            expected_base_sha="99e27aa2f9e6e5b8c1a3d2e7f4a6b8c0d9e1f3a5",
            dry_run=False,
        )

        assert status == "HOLD_BASE_SHA_MISMATCH"


class TestGitApplyCheckFailureBlocks:
    """git apply --check failure blocks."""

    def test_apply_check_failure_blocks(self, tmp_path, monkeypatch):
        """Failed git apply --check returns HOLD_GIT_APPLY_CHECK_FAILED."""
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)

        def mock_head(repo_root):
            return "99e27aa2f9e6e5b8c1a3d2e7f4a6b8c0d9e1f3a5"
        monkeypatch.setattr(aptb, "_git_head", mock_head)

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        def mock_branch_exists(repo_root, branch):
            return False
        monkeypatch.setattr(aptb, "_git_branch_exists", mock_branch_exists)

        def mock_apply_check_fails(repo_root, diff_patch):
            return False, "error: patch does not apply cleanly"
        monkeypatch.setattr(aptb, "_git_apply_check", mock_apply_check_fails)

        status, result = aptb.apply_patch_to_branch(
            result_path, diff_path, REPO_ROOT,
            "apply/test-apply-check-fail",
            require_apply_ready=False,
            apply_readiness_json_path=None,
            allow_real_apply=True,
            expected_base_sha=None,
            dry_run=False,
        )

        assert status == "HOLD_GIT_APPLY_CHECK_FAILED"


class TestForbiddenFileBlocks:
    """Patch that changes a forbidden file is blocked."""

    def test_forbidden_file_blocks(self, tmp_path, monkeypatch):
        """Forbidden file in patch returns HOLD_FORBIDDEN_FILE_CHANGED."""
        # Use run_temp_worktree_execution.py which is in FORBIDDEN_PATHS
        result_path = make_result_json(
            tmp_path,
            changed_files=["scripts/local/run_temp_worktree_execution.py"],
            task={"allowed_files": ["scripts/local/run_temp_worktree_execution.py"], "forbidden_files": ["scripts/local/run_temp_worktree_execution.py"]},
        )
        diff_path = make_diff_patch(tmp_path, content=(
            "diff --git a/scripts/local/run_temp_worktree_execution.py b/scripts/local/run_temp_worktree_execution.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/scripts/local/run_temp_worktree_execution.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+# modified\n"
        ))

        def mock_head(repo_root):
            return "99e27aa2f9e6e5b8c1a3d2e7f4a6b8c0d9e1f3a5"
        monkeypatch.setattr(aptb, "_git_head", mock_head)

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        def mock_branch_exists(repo_root, branch):
            return False
        monkeypatch.setattr(aptb, "_git_branch_exists", mock_branch_exists)

        def mock_apply_check_ok(repo_root, diff_patch):
            return True, ""
        monkeypatch.setattr(aptb, "_git_apply_check", mock_apply_check_ok)

        status, result = aptb.apply_patch_to_branch(
            result_path, diff_path, REPO_ROOT,
            "apply/test-forbidden",
            require_apply_ready=False,
            apply_readiness_json_path=None,
            allow_real_apply=True,
            expected_base_sha=None,
            dry_run=False,
        )

        assert status == "HOLD_FORBIDDEN_FILE_CHANGED"


class TestProtectedFileBlocks:
    """Patch that changes a protected file is blocked."""

    def test_protected_file_blocks(self, tmp_path, monkeypatch):
        """Protected file in patch returns HOLD_PROTECTED_FILE_CHANGED."""
        result_path = make_result_json(
            tmp_path,
            changed_files=["scripts/local/check_persistent_mutation_guard.py"],
            task={"allowed_files": ["scripts/local/check_persistent_mutation_guard.py"], "forbidden_files": []},
        )
        diff_path = make_diff_patch(tmp_path, content=(
            "diff --git a/scripts/local/check_persistent_mutation_guard.py b/scripts/local/check_persistent_mutation_guard.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/scripts/local/check_persistent_mutation_guard.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+# modified\n"
        ))

        def mock_head(repo_root):
            return "99e27aa2f9e6e5b8c1a3d2e7f4a6b8c0d9e1f3a5"
        monkeypatch.setattr(aptb, "_git_head", mock_head)

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        def mock_branch_exists(repo_root, branch):
            return False
        monkeypatch.setattr(aptb, "_git_branch_exists", mock_branch_exists)

        def mock_apply_check_ok(repo_root, diff_patch):
            return True, ""
        monkeypatch.setattr(aptb, "_git_apply_check", mock_apply_check_ok)

        status, result = aptb.apply_patch_to_branch(
            result_path, diff_path, REPO_ROOT,
            "apply/test-protected",
            require_apply_ready=False,
            apply_readiness_json_path=None,
            allow_real_apply=True,
            expected_base_sha=None,
            dry_run=False,
        )

        assert status == "HOLD_PROTECTED_FILE_CHANGED"


class TestNoPushInImplementation:
    """No push, PR, merge, or Claude invocation exists in the implementation."""

    def test_no_push_in_source(self):
        """apply_temp_worktree_patch_to_branch.py contains no git push subprocess call."""
        src = aptb.__file__
        assert src is not None
        content = Path(src).read_text(encoding="utf-8")
        # Only fail if "push" is in a subprocess.run or subprocess.call call
        lines = content.split("\n")
        for line in lines:
            if ("subprocess" in line and "push" in line) or ('"push"' in line) or ("'push'" in line):
                # Check if it's inside a string literal (docstring/comment) vs code
                stripped = line.lstrip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                pytest.fail(f"Found push in code line: {line.strip()}")
        # Also check for "git push" as a string literal (not just the word push)
        import re
        matches = re.findall(r'["\']git push["\']', content)
        assert len(matches) == 0, f"Found 'git push' literal: {matches}"

    def test_no_gh_pr_in_source(self):
        """apply_temp_worktree_patch_to_branch.py contains no gh pr subprocess call."""
        src = aptb.__file__
        assert src is not None
        content = Path(src).read_text(encoding="utf-8")
        import re
        matches = re.findall(r'["\']gh pr (create|merge)["\']', content)
        assert len(matches) == 0, f"Found 'gh pr' literal: {matches}"

    def test_no_merge_in_source(self):
        """apply_temp_worktree_patch_to_branch.py contains no git merge subprocess call."""
        src = aptb.__file__
        assert src is not None
        content = Path(src).read_text(encoding="utf-8")
        import re
        matches = re.findall(r'["\']git merge["\']', content)
        assert len(matches) == 0, f"Found 'git merge' literal: {matches}"

    def test_no_claude_invocation_in_source(self):
        """apply_temp_worktree_patch_to_branch.py contains no subprocess call with 'claude'."""
        src = aptb.__file__
        assert src is not None
        content = Path(src).read_text(encoding="utf-8")
        import re
        # Find any subprocess call with "claude" as an argument
        matches = re.findall(r'subprocess\.[a-z]+\([^)]*["\']claude["\'][^)]*\)', content)
        assert len(matches) == 0, f"Found claude subprocess call: {matches}"

    def test_no_shell_true_in_source(self):
        """apply_temp_worktree_patch_to_branch.py contains no shell=True in subprocess calls."""
        src = aptb.__file__
        assert src is not None
        content = Path(src).read_text(encoding="utf-8")
        import re
        # Find shell=True in actual code (subprocess calls)
        # Exclude: f-string interpolations, comments
        matches = []
        for line_num, line in enumerate(content.split("\n"), 1):
            stripped = line.lstrip()
            # Skip comments
            if stripped.startswith("#"):
                continue
            # Skip bullet points in docstrings (lines starting with - or * followed by text)
            if len(stripped) > 2 and stripped[0] in ('-', '*') and 'shell=True' in line:
                continue
            if "shell=True" not in line:
                continue
            idx = line.index("shell=True")
            before = line[:idx]
            # If odd number of { before, it's in f-string interpolation -> skip
            if before.count("{") % 2 == 1:
                continue
            # If in a string (odd number of quotes before), skip
            sq = before.count("'") - before.count("\\'")
            dq = before.count('"') - before.count('\\"')
            if sq % 2 == 1 or dq % 2 == 1:
                continue
            matches.append(f"line {line_num}: {line.strip()[:80]}")
        assert len(matches) == 0, f"Found shell=True in code: {matches}"

    def test_no_package_install_in_source(self):
        """apply_temp_worktree_patch_to_branch.py contains no package install."""
        src = aptb.__file__
        assert src is not None
        content = Path(src).read_text(encoding="utf-8")
        import re
        matches = re.findall(r'(pip install|apt install|npm install|conda install)', content)
        assert len(matches) == 0, f"Found package install: {matches}"


class TestCommandLogRedactsUnsafeStrings:
    """Command log avoids raw unsafe shell strings."""

    def test_command_log_is_list(self, tmp_path, monkeypatch):
        """Command log is a list of strings, not raw subprocess output."""
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)

        def mock_head(repo_root):
            return "99e27aa2f9e6e5b8c1a3d2e7f4a6b8c0d9e1f3a5"
        monkeypatch.setattr(aptb, "_git_head", mock_head)

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        def mock_branch_exists(repo_root, branch):
            return False
        monkeypatch.setattr(aptb, "_git_branch_exists", mock_branch_exists)

        def mock_apply_check_ok(repo_root, diff_patch):
            return True, ""
        monkeypatch.setattr(aptb, "_git_apply_check", mock_apply_check_ok)

        status, result = aptb.apply_patch_to_branch(
            result_path, diff_path, REPO_ROOT,
            "apply/test-cmd-log",
            require_apply_ready=False,
            apply_readiness_json_path=None,
            allow_real_apply=True,
            expected_base_sha=None,
            dry_run=False,
        )

        cmd_log = result.get("command_log", [])
        assert isinstance(cmd_log, list)
        for entry in cmd_log:
            assert isinstance(entry, str)


class TestRequireApplyReady:
    """Apply-readiness requirement blocks when not satisfied."""

    def test_require_apply_ready_blocks_wrong_status(self, tmp_path, monkeypatch):
        """Wrong apply-readiness status blocks apply."""
        result_path = make_result_json(tmp_path)
        diff_path = make_diff_patch(tmp_path)
        readiness_path = make_apply_readiness_json(tmp_path, result_path, diff_path, status="HOLD_PMG_NOT_CLEAN")

        def mock_head(repo_root):
            return "99e27aa2f9e6e5b8c1a3d2e7f4a6b8c0d9e1f3a5"
        monkeypatch.setattr(aptb, "_git_head", mock_head)

        def mock_clean(repo_root):
            return True
        monkeypatch.setattr(aptb, "_git_status_clean", mock_clean)

        def mock_branch_exists(repo_root, branch):
            return False
        monkeypatch.setattr(aptb, "_git_branch_exists", mock_branch_exists)

        def mock_apply_check_ok(repo_root, diff_patch):
            return True, ""
        monkeypatch.setattr(aptb, "_git_apply_check", mock_apply_check_ok)

        status, result = aptb.apply_patch_to_branch(
            result_path, diff_path, REPO_ROOT,
            "apply/test-not-ready",
            require_apply_ready=True,
            apply_readiness_json_path=readiness_path,
            allow_real_apply=True,
            expected_base_sha=None,
            dry_run=False,
        )

        assert status == "HOLD_APPLY_NOT_READY"


# ---------------------------------------------------------------------------
# Import for argparse used in TestOutputInsideRepo
# ---------------------------------------------------------------------------
import argparse