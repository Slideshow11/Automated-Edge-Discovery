#!/usr/bin/env python3
"""
Tests for run_autocoder_single_task.py
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

# Resolve once from test file location
_TEST_FILE = Path(__file__).resolve()
_REPO_ROOT = _TEST_FILE.parent.parent
_SCRIPT_DIR = _REPO_ROOT / "scripts" / "local"

REPO_ROOT = _REPO_ROOT
SCRIPT_DIR = _SCRIPT_DIR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_controller(task_packet: dict, output_json, output_md) -> dict:
    """Run the controller with a task packet and return parsed result JSON."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(task_packet, f)
        pkt_path = Path(f.name)
    try:
        # Resolve script path via test file's resolved directory
        test_file = Path(__file__).resolve()
        repo_root = test_file.parent.parent
        script_path = repo_root / "scripts" / "local" / "run_autocoder_single_task.py"
        argv = [
            "python3", str(script_path),
            "--task-packet-json", str(pkt_path),
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ]
        result = subprocess.run(
            argv,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        out_str = str(output_json)
        if Path(out_str).exists():
            with open(out_str) as f:
                return json.load(f)
        out_fspath = getattr(output_json, "__fspath__", lambda: out_str)()
        if out_fspath != out_str and Path(out_fspath).exists():
            with open(out_fspath) as f:
                return json.load(f)
        return {
            "status": "NO_OUTPUT",
            "output_json_path_attempted": out_str,
            "fspath_attempted": out_fspath,
            "subprocess_rc": result.returncode,
            "stderr": result.stderr[:200],
        }
    finally:
        os.unlink(pkt_path)


def make_packet(task_id: str = None, **overrides) -> dict:
    """Make a valid base task packet with optional overrides.

    task_id defaults to a fresh UUID so each call produces a unique branch_name,
    avoiding spurious 'branch already exists' failures in concurrent or
    sequential test runs.
    """
    import uuid as _uuid
    if task_id is None:
        task_id = f"test-task-{_uuid.uuid4().hex[:8]}"
    base = {
        "packet_kind": "aed.autocoder.single_task.v0",
        "task_id": task_id,
        "goal": "Add a simple doc file to the docs directory for testing.",
        "allowed_files": None,
        "forbidden_files": ["bin/", "examples/"],
        "max_changed_files": 5,
        "required_tests": None,
        "output_root": "/tmp/aed_runs/autocoder_test",
        "branch_name": f"autocoder-test-{task_id}",
        "suggested_pr_title": f"docs: add {task_id}",
        "suggested_pr_body": "Test PR body.",
        "execution_mode": "mocked",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Validation tests (stage 1 only — no tool execution)
# ---------------------------------------------------------------------------

class TestTaskPacketValidation:
    """Test that invalid task packets are rejected at validation stage."""

    def test_rejects_wrong_packet_kind(self, tmp_path):
        packet = make_packet(packet_kind="bad")
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "packet_kind" in result.get("error", "").lower()

    def test_rejects_empty_task_id(self, tmp_path):
        packet = make_packet(task_id="")
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"

    def test_rejects_task_id_with_spaces(self, tmp_path):
        packet = make_packet(task_id="has spaces")
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"

    def test_rejects_short_goal(self, tmp_path):
        packet = make_packet(goal="short")
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"

    def test_rejects_long_goal(self, tmp_path):
        packet = make_packet(goal="x" * 1001)
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"

    def test_rejects_missing_execution_mode(self, tmp_path):
        packet = make_packet()
        del packet["execution_mode"]
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"

    def test_rejects_claude_execution_mode(self, tmp_path):
        packet = make_packet(execution_mode="claude")
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "claude" in result.get("error", "").lower()

    def test_rejects_unknown_execution_mode(self, tmp_path):
        packet = make_packet(execution_mode="unknown")
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"

    def test_rejects_output_root_inside_repo(self, tmp_path):
        packet = make_packet(output_root=str(REPO_ROOT / "tmp" / "test"))
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "output_root" in result.get("error", "").lower()

    def test_rejects_existing_branch(self, tmp_path):
        branch = f"autocoder-test-conflict"
        subprocess.run(["git", "branch", branch, "HEAD"], cwd=str(REPO_ROOT))
        try:
            packet = make_packet(task_id="conflict", branch_name=branch)
            result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
            assert result["status"] == "HOLD_TASK_PACKET_INVALID"
            assert "branch" in result.get("error", "").lower()
        finally:
            subprocess.run(["git", "branch", "-D", branch], cwd=str(REPO_ROOT))

    def test_rejects_empty_pr_title(self, tmp_path):
        packet = make_packet(suggested_pr_title="")
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"

    def test_rejects_empty_pr_body(self, tmp_path):
        packet = make_packet(suggested_pr_body="")
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"

    def test_rejects_allowed_files_with_whitespace(self, tmp_path):
        packet = make_packet(allowed_files=["has space"])
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"

    def test_rejects_bad_max_changed_files(self, tmp_path):
        packet = make_packet(max_changed_files=0)
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"

    def test_accepts_valid_minimal_packet(self, tmp_path):
        packet = make_packet()
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        # Valid packet passes validation and proceeds to stage 2 (mock execution).
        # We only verify it doesn't fail at stage 1.
        assert result["status"] != "HOLD_TASK_PACKET_INVALID"


# ---------------------------------------------------------------------------
# Status taxonomy tests
# ---------------------------------------------------------------------------

class TestStatusTaxonomy:
    """Test that the correct hold status is returned for each failure type."""

    def test_claude_mode_returns_task_packet_invalid(self, tmp_path):
        packet = make_packet(execution_mode="claude")
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"

    def test_missing_goal_returns_task_packet_invalid(self, tmp_path):
        packet = make_packet(goal="")
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"

    def test_whitespace_allowed_files_returns_task_packet_invalid(self, tmp_path):
        packet = make_packet(allowed_files=["bad entry"])
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"


# ---------------------------------------------------------------------------
# Safety boundary tests
# ---------------------------------------------------------------------------

class TestSafetyBoundaries:
    """Test that the controller respects hard safety boundaries."""

    def test_no_subprocess_run_with_shell_true(self, tmp_path):
        """Verify all subprocess.run calls use shell=False (explicit check)."""
        import ast
        with open(SCRIPT_DIR / "run_autocoder_single_task.py") as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "run":
                    for kw in node.keywords:
                        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                            pytest.fail("shell=True found in subprocess.run call")

    def test_no_git_push_in_subprocess_calls(self, tmp_path):
        """Verify no subprocess call includes 'git push'."""
        import ast
        with open(SCRIPT_DIR / "run_autocoder_single_task.py") as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "run":
                    for arg in node.args:
                        if isinstance(arg, ast.List):
                            for elt in arg.elts:
                                if isinstance(elt, ast.Constant) and elt.value == "push":
                                    pytest.fail(f"git push found in subprocess.run argument")
                                if isinstance(elt, ast.Constant) and elt.value == "git":
                                    # check next element isn't push
                                    idx = arg.elts.index(elt)
                                    if idx + 1 < len(arg.elts):
                                        next_val = arg.elts[idx + 1]
                                        if isinstance(next_val, ast.Constant) and next_val.value == "push":
                                            pytest.fail(f"git push found")

    def test_no_gh_pr_create_in_subprocess_calls(self, tmp_path):
        """Verify no subprocess call includes 'gh pr create'."""
        with open(SCRIPT_DIR / "run_autocoder_single_task.py") as f:
            code = f.read()
        # Check for literal 'gh' followed by 'pr' followed by 'create' in argv lists
        import re
        argv_matches = re.findall(r'\["gh",\s*"pr",\s*"create"\]', code)
        assert not argv_matches, f"gh pr create subprocess call found: {argv_matches}"

    def test_no_gh_pr_merge_in_subprocess_calls(self, tmp_path):
        """Verify no subprocess call includes 'gh pr merge'."""
        with open(SCRIPT_DIR / "run_autocoder_single_task.py") as f:
            code = f.read()
        import re
        argv_matches = re.findall(r'\["gh",\s*"pr",\s*"merge"\]', code)
        assert not argv_matches, f"gh pr merge subprocess call found: {argv_matches}"


# --------------------------------------------------------------------------
# mock_edits validation tests
# --------------------------------------------------------------------------


class TestMockEditsValidation:
    """Test that mock_edits is validated before build_execution_packet."""

    def test_rejects_mock_edits_not_list(self, tmp_path):
        packet = make_packet(mock_edits="not a list")
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "mock_edits" in result.get("error", "").lower()

    def test_rejects_empty_mock_edits_list(self, tmp_path):
        packet = make_packet(mock_edits=[])
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "mock_edits" in result.get("error", "").lower()

    def test_rejects_mock_edit_missing_path(self, tmp_path):
        packet = make_packet(mock_edits=[{"content": "new content"}])
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "path" in result.get("error", "").lower()

    def test_rejects_mock_edit_empty_path(self, tmp_path):
        packet = make_packet(mock_edits=[{"path": "", "content": "new content"}])
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"

    def test_rejects_mock_edit_not_in_allowed_files(self, tmp_path):
        packet = make_packet(
            allowed_files=["docs/safe.md"],
            mock_edits=[{"path": "docs/forbidden.md", "content": "new"}],
        )
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "allowed_files" in result.get("error", "").lower()

    def test_rejects_mock_edit_in_forbidden_files(self, tmp_path):
        packet = make_packet(
            forbidden_files=["scripts/secret.py"],
            mock_edits=[{"path": "scripts/secret.py", "content": "new"}],
        )
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "forbidden" in result.get("error", "").lower()

    def test_rejects_mock_edit_under_forbidden_prefix_dir(self, tmp_path):
        """Forbidden dir with trailing slash blocks files inside it."""
        packet = make_packet(
            forbidden_files=["scripts/"],
            mock_edits=[{"path": "scripts/local/run_autocoder_single_task.py", "content": "hacked"}],
        )
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "forbidden" in result.get("error", "").lower()

    def test_rejects_mock_edit_under_forbidden_prefix_no_trailing_slash(self, tmp_path):
        """Forbidden dir without trailing slash also blocks files inside it."""
        packet = make_packet(
            forbidden_files=["scripts"],
            mock_edits=[{"path": "scripts/local/run_autocoder_single_task.py", "content": "hacked"}],
        )
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "forbidden" in result.get("error", "").lower()

    def test_rejects_mock_edit_absolute_path_with_null_forbidden(self, tmp_path):
        """Absolute paths rejected even when forbidden_files is null."""
        packet = make_packet(
            forbidden_files=None,
            mock_edits=[{"path": "/tmp/evil.md", "content": "absolute"}],
        )
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        err = result.get("error", "").lower()
        assert "forbidden" in err

    def test_rejects_mock_edit_traversal_path(self, tmp_path):
        """Paths containing '..' are rejected (caught by allowed or forbidden check)."""
        packet = make_packet(
            allowed_files=["docs/safe.md"],
            mock_edits=[{"path": "../docs/safe.md", "content": "escape"}],
        )
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        # Rejected either because path is not in allowed_files OR path is forbidden (starts with / or contains ..)
        err = result.get("error", "").lower()
        assert "forbidden" in err or "not in allowed" in err

    def test_rejects_mock_edit_absolute_path(self, tmp_path):
        """Absolute paths are rejected unconditionally (before forbidden check)."""
        packet = make_packet(
            forbidden_files=["/tmp/"],
            mock_edits=[{"path": "/tmp/aed_runs/somefile.md", "content": "absolute"}],
        )
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        err = result.get("error", "").lower()
        assert "forbidden" in err

    def test_rejects_mock_edit_backslash_normalized(self, tmp_path):
        """Backslashes are normalized and prefix match applied."""
        packet = make_packet(
            forbidden_files=["scripts/"],
            mock_edits=[{"path": "scripts\\local\\run.py", "content": "backslashes"}],
        )
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "forbidden" in result.get("error", "").lower()

    def test_rejects_mock_edits_count_exceeds_max_changed_files(self, tmp_path):
        packet = make_packet(
            max_changed_files=1,
            mock_edits=[
                {"path": "docs/a.md", "content": "a"},
                {"path": "docs/b.md", "content": "b"},
            ],
        )
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "max_changed_files" in result.get("error", "").lower()

    def test_accepts_valid_mock_edits(self, tmp_path):
        packet = make_packet(
            allowed_files=["docs/test.md"],
            mock_edits=[{"path": "docs/test.md", "content": "new content"}],
        )
        result = run_controller(packet, tmp_path / "out.json", tmp_path / "out.md")
        # Passes validation — proceeds to stage 2 (not necessarily to READY)
        assert result["status"] != "HOLD_TASK_PACKET_INVALID"


class TestMockEditsBuildIntegration:
    """Test that build_execution_packet includes mock_edits in execution dict."""

    def test_build_execution_packet_includes_mock_edits(self, tmp_path):
        """Verify execution.mock_edits is populated from task packet."""
        import sys
        sys.path.insert(0, str(SCRIPT_DIR))
        from run_autocoder_single_task import build_execution_packet

        task_packet = make_packet(
            task_id="test-mock-packet",
            allowed_files=["docs/test.md"],
            mock_edits=[{"path": "docs/test.md", "content": "new"}],
        )
        from pathlib import Path
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("plan", encoding="utf-8")
        plan_sha = "deadbeef" * 8  # fake SHA for this unit test

        exec_packet = build_execution_packet(task_packet, plan_sha, plan_file)

        assert "execution" in exec_packet
        assert "mock_edits" in exec_packet["execution"]
        assert exec_packet["execution"]["mock_edits"] == [{"path": "docs/test.md", "content": "new"}]

    def test_build_execution_packet_empty_mock_edits_when_absent(self, tmp_path):
        """Verify execution.mock_edits defaults to [] when not in task packet."""
        import sys
        sys.path.insert(0, str(SCRIPT_DIR))
        from run_autocoder_single_task import build_execution_packet

        task_packet = make_packet(task_id="test-no-mock")
        from pathlib import Path
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("plan", encoding="utf-8")
        plan_sha = "deadbeef" * 8

        exec_packet = build_execution_packet(task_packet, plan_sha, plan_file)

        assert "execution" in exec_packet
        assert exec_packet["execution"].get("mock_edits") == []


class TestRepoRootArg:
    """Test --repo-root argument handling."""

    def run_with_repo_root(self, task_packet, output_json, output_md, repo_root_path):
        """Run controller with --repo-root override."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(task_packet, f)
            pkt_path = Path(f.name)
        try:
            script_path = SCRIPT_DIR / "run_autocoder_single_task.py"
            argv = [
                "python3", str(script_path),
                "--task-packet-json", str(pkt_path),
                "--output-json", str(output_json),
                "--output-md", str(output_md),
                "--repo-root", str(repo_root_path),
            ]
            result = subprocess.run(
                argv,
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if Path(str(output_json)).exists():
                with open(str(output_json)) as f:
                    return json.load(f)
            return {
                "status": "NO_OUTPUT",
                "subprocess_rc": result.returncode,
                "stderr": result.stderr[:200],
            }
        finally:
            os.unlink(pkt_path)

    def test_rejects_nonexistent_repo_root(self, tmp_path):
        """--repo-root pointing to non-existent path returns HOLD_TASK_PACKET_INVALID."""
        packet = make_packet(
            task_id="test-repo-root-nonexistent",
            allowed_files=["docs/test.md"],
        )
        result = self.run_with_repo_root(
            packet, tmp_path / "out.json", tmp_path / "out.md",
            "/nonexistent/path/that/does/not/exist",
        )
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "not a git repository" in result.get("error", "").lower()

    def test_rejects_non_git_repo_root(self, tmp_path):
        """--repo-root pointing to non-git directory returns HOLD_TASK_PACKET_INVALID."""
        packet = make_packet(
            task_id="test-repo-root-not-git",
            allowed_files=["docs/test.md"],
        )
        result = self.run_with_repo_root(
            packet, tmp_path / "out.json", tmp_path / "out.md",
            str(tmp_path),  # tmp_path is a plain directory, not a git repo
        )
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "not a git repository" in result.get("error", "").lower()

    def test_accepts_valid_repo_root(self, tmp_path):
        """--repo-root pointing to a valid git repo is accepted and used."""
        # Use the AED repo root itself as a known-valid git repo
        packet = make_packet(
            task_id="test-repo-root-valid",
            allowed_files=["docs/test.md"],
            output_root=str(tmp_path / "aed_runs"),
        )
        result = self.run_with_repo_root(
            packet, tmp_path / "out.json", tmp_path / "out.md",
            str(REPO_ROOT),  # The test repo is a valid git repo
        )
        # Should pass validation (not HOLD_TASK_PACKET_INVALID)
        # May fail at later stage since docs/test.md may not exist, but
        # not with "not a git repository"
        assert "not a git repository" not in result.get("error", "").lower()


# ---------------------------------------------------------------------------
# Full mock controller regression (P3C-B1 unblocker)
# ---------------------------------------------------------------------------

class TestFullMockRunReachesReady:
    """End-to-end mock controller run reaches SINGLE_TASK_READY_FOR_HUMAN_REVIEW.

    This is the regression test for the pre-existing mock controller pipeline
    blocker: stage 5 (apply_to_branch) treated staged-added files as
    applied, but stage 6 (verify_temp_worktree_applied_branch) did not. The
    test runs the full controller CLI with a valid mock task packet and
    asserts the terminal state is READY (not HOLD_BRANCH_DIFF_MISMATCH).
    """

    def test_full_mock_run_reaches_ready(self, tmp_path):
        # Use a unique task_id and a temp output_root / worktree_root outside
        # the repo to avoid collisions with concurrent runs and to keep the
        # AED worktree clean.
        import uuid as _uuid
        task_id = f"full-mock-{_uuid.uuid4().hex[:8]}"
        output_root = tmp_path / "aed_runs" / task_id
        worktree_root = tmp_path / "wt" / task_id
        repo_under_test = tmp_path / "repo_under_test"
        temp_hermes_home = tmp_path / ".hermes"
        temp_hermes_home.mkdir()
        # P2 GmCjg: temp HERMES_HOME is passed to the subprocess so the test
        # does not depend on or mutate the real developer's ~/.hermes.
        assert temp_hermes_home != Path.home() / ".hermes"
        controller_env = {
            **os.environ,
            "HERMES_HOME": str(temp_hermes_home),
        }
        # P2 GmCjg: explicit assertions so a reviewer (or Codex) can see at a
        # glance that the env is wired correctly and is not the real home.
        assert "HERMES_HOME" in controller_env
        assert controller_env["HERMES_HOME"] == str(temp_hermes_home)
        assert Path(controller_env["HERMES_HOME"]) != Path.home() / ".hermes"
        branch_name = f"autocoder-full-mock-{task_id}"
        setup_result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "worktree", "add", "--detach", str(repo_under_test), "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert setup_result.returncode == 0, setup_result.stderr
        controller_source_paths = [
            Path("scripts/local/preview_applied_branch_pr.py"),
            Path("scripts/local/verify_temp_worktree_applied_branch.py"),
        ]
        for rel_path in controller_source_paths:
            target = repo_under_test / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / rel_path, target)
        source_status_result = subprocess.run(
            ["git", "-C", str(repo_under_test), "status", "--porcelain", "--"]
            + [str(p) for p in controller_source_paths],
            capture_output=True,
            text=True,
        )
        if source_status_result.stdout.strip():
            subprocess.run(
                ["git", "-C", str(repo_under_test), "config", "user.email", "test@test.test"],
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "-C", str(repo_under_test), "config", "user.name", "Test"],
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "-C", str(repo_under_test), "add", "--"]
                + [str(p) for p in controller_source_paths],
                capture_output=True,
                text=True,
            )
            commit_sources_result = subprocess.run(
                ["git", "-C", str(repo_under_test), "commit", "-m", "test controller script sources"],
                capture_output=True,
                text=True,
            )
            assert commit_sources_result.returncode == 0, commit_sources_result.stderr
        base_branch_result = subprocess.run(
            ["git", "-C", str(repo_under_test), "rev-parse", "--verify", "refs/heads/main"],
            capture_output=True,
            text=True,
        )
        if base_branch_result.returncode != 0:
            create_base_result = subprocess.run(
                ["git", "-C", str(repo_under_test), "branch", "main", "HEAD"],
                capture_output=True,
                text=True,
            )
            assert create_base_result.returncode == 0, create_base_result.stderr
        original_branch_result = subprocess.run(
            ["git", "-C", str(repo_under_test), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
        )
        original_branch = original_branch_result.stdout.strip()
        original_head_result = subprocess.run(
            ["git", "-C", str(repo_under_test), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        original_head = original_head_result.stdout.strip()
        # The mock edit must target a path under the actual AED repo because
        # the controller's apply_to_branch expects the change to land in the
        # repo's working tree. Pick a path under scripts/local/ which is
        # already in the allowed_files glob of the task packet.
        mock_path = f"scripts/local/_full_mock_{task_id}.py"
        packet = make_packet(
            task_id=task_id,
            allowed_files=[
                "scripts/local/*.py",
                mock_path,
            ],
            forbidden_files=[".github/**", "*.json", "*.md", "bin/", "examples/"],
            max_changed_files=3,
            required_tests=None,
            output_root=str(output_root),
            worktree_root=str(worktree_root),
            branch_name=branch_name,
            suggested_pr_title=f"tooling: full mock regression {task_id}",
            suggested_pr_body="P3C-B1 unblocker regression test.",
            execution_mode="mocked",
            mock_edits=[{"path": mock_path, "content": f"# full mock {task_id}\n"}],
        )

        # Write packet
        pkt_path = tmp_path / "packet.json"
        with open(pkt_path, "w") as f:
            json.dump(packet, f)

        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        script_path = repo_under_test / "scripts" / "local" / "run_autocoder_single_task.py"

        def cleanup_mock_run(*, remove_repo: bool = False) -> None:
            subprocess.run(
                ["git", "-C", str(repo_under_test), "reset", "HEAD", "--", mock_path],
                capture_output=True,
                text=True,
            )
            mock_full = repo_under_test / mock_path
            if mock_full.exists():
                mock_full.unlink()
            current_branch_result = subprocess.run(
                ["git", "-C", str(repo_under_test), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
            )
            if (
                current_branch_result.stdout.strip() == branch_name
                and original_branch
                and original_branch != branch_name
            ):
                if original_branch == "HEAD":
                    subprocess.run(
                        ["git", "-C", str(repo_under_test), "switch", "--detach", original_head],
                        capture_output=True,
                        text=True,
                    )
                else:
                    subprocess.run(
                        ["git", "-C", str(repo_under_test), "switch", original_branch],
                        capture_output=True,
                        text=True,
                    )
            subprocess.run(
                ["git", "-C", str(repo_under_test), "branch", "-D", branch_name],
                capture_output=True,
                text=True,
            )
            # Also clean any temp worktree the controller may have created
            if worktree_root.exists():
                subprocess.run(
                    ["git", "-C", str(repo_under_test), "worktree", "remove",
                     "--force", str(worktree_root)],
                    capture_output=True, text=True,
                )
            if remove_repo and repo_under_test.exists():
                subprocess.run(
                    ["git", "-C", str(REPO_ROOT), "worktree", "remove",
                     "--force", str(repo_under_test)],
                    capture_output=True, text=True,
                )

        # Pre-clean any leftover branch from a prior failed run.
        cleanup_mock_run()

        try:
            argv = [
                "python3", str(script_path),
                "--task-packet-json", str(pkt_path),
                "--output-json", str(out_json),
                "--output-md", str(out_md),
                "--repo-root", str(repo_under_test),
            ]
            result = subprocess.run(
                argv,
                cwd=str(repo_under_test),
                env=controller_env,
                capture_output=True,
                text=True,
                timeout=120,
            )

            assert out_json.exists(), (
                f"controller produced no output JSON; rc={result.returncode}; "
                f"stderr={result.stderr[:300]}"
            )
            cs = json.loads(out_json.read_text())
            # Pre-fix this was HOLD_APPLIED_BRANCH_NOT_READY / HOLD_BRANCH_DIFF_MISMATCH.
            # Post-fix it should be SINGLE_TASK_READY_FOR_HUMAN_REVIEW.
            assert cs.get("status") == "SINGLE_TASK_READY_FOR_HUMAN_REVIEW", (
                f"expected SINGLE_TASK_READY_FOR_HUMAN_REVIEW, got "
                f"{cs.get('status')!r}; stage={cs.get('stage')!r}; "
                f"actual={cs.get('actual')!r}; expected={cs.get('expected')!r}"
            )

            artifacts = cs["artifacts"]
            apply_to_branch = json.loads(Path(artifacts["apply_to_branch_json"]).read_text(encoding="utf-8"))
            applied_branch = json.loads(Path(artifacts["applied_branch_verification_json"]).read_text(encoding="utf-8"))
            pr_preview = json.loads(Path(artifacts["pr_preview_json"]).read_text(encoding="utf-8"))
            applied_branch_md = Path(artifacts["applied_branch_verification_json"]).with_suffix(".md").read_text(encoding="utf-8")
            pr_preview_md = Path(artifacts["pr_preview_json"]).with_suffix(".md").read_text(encoding="utf-8")

            assert apply_to_branch["status"] == "APPLY_TO_BRANCH_APPLIED"
            assert applied_branch["status"] == "APPLIED_BRANCH_READY"
            assert mock_path in applied_branch["checks"]["staged_added_expected"]
            assert mock_path in applied_branch["changed_files_actual"]
            assert mock_path in applied_branch["generated_human_commands"]["git_index_diff"]
            assert mock_path in pr_preview["review_diff_sources"]["git_index_diff"]
            assert "git -C" in applied_branch_md and "diff --cached" in applied_branch_md
            assert "Staged/Index Diff" in pr_preview_md
            assert mock_path in pr_preview_md
            assert applied_branch["checks"].get("missing_files_from_branch", []) == []
        finally:
            cleanup_mock_run(remove_repo=True)


# ---------------------------------------------------------------------------
# run_summary.json emission (aed.run_summary.v0)
# ---------------------------------------------------------------------------

class TestRunSummaryEmission:
    """Every controller run must emit a sibling run_summary.json next to
    final_status.json. The summary is best-effort, additive, and must not
    change final_status.json behavior, exit codes, or batch behavior.
    """

    def test_run_summary_emitted_on_hold_path(self, tmp_path):
        """HOLD_TASK_PACKET_INVALID writes both final_status.json and
        run_summary.json with the aed.run_summary.v0 schema."""
        output_json = tmp_path / "out.json"
        output_md = tmp_path / "out.md"
        # Use an invalid packet (wrong packet_kind) to deterministically hit
        # the early HOLD_TASK_PACKET_INVALID return without touching the repo.
        packet = make_packet(packet_kind="bad")
        result = run_controller(packet, output_json, output_md)

        # final_status.json was written
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert output_json.exists()

        # run_summary.json was written next to final_status.json
        summary_path = output_json.with_name("run_summary.json")
        assert summary_path.exists(), (
            f"run_summary.json not found at {summary_path}; "
            f"final_status.json was {output_json}"
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

        # Schema fields
        assert summary["run_summary_version"] == "aed.run_summary.v0"
        assert summary["controller"] == "run_autocoder_single_task.py"
        assert summary["status"] == result["status"]
        # task_id, packet_kind, execution_mode, base_sha, branch_name come
        # from the packet where available.
        assert summary["task_id"] == packet["task_id"]
        assert summary["packet_kind"] == "bad"  # whatever the packet had
        assert summary["execution_mode"] == packet["execution_mode"]
        assert summary["branch_name"] == packet["branch_name"]
        # HOLD path: hold_reason should be the validation error string.
        assert isinstance(summary["hold_reason"], str)
        assert summary["hold_reason"]  # non-empty
        # Artifacts map always includes the primary controller outputs.
        assert summary["artifacts"]["final_status_json"] == str(output_json)
        assert summary["artifacts"]["final_status_md"] == str(output_md)
        # output_json / output_md mirror the actual paths.
        assert summary["output_json"] == str(output_json)
        assert summary["output_md"] == str(output_md)
        # changed_files and tests_run are lists (possibly empty).
        assert isinstance(summary["changed_files"], list)
        assert isinstance(summary["tests_run"], list)
        # generated_at is populated.
        assert "generated_at" in summary and summary["generated_at"]

    def test_run_summary_emitted_on_fatal_file_not_found(self, tmp_path):
        """Main() fatal 'task packet not found' must also write run_summary.json."""
        # Point --output-json to a temp path; --task-packet-json to a
        # non-existent file so main() short-circuits to the fatal early
        # return. The controller's run_controller helper writes the
        # packet to a temp file, so we drive the controller via subprocess
        # directly to exercise the not-found branch.
        output_json = tmp_path / "out.json"
        output_md = tmp_path / "out.md"
        missing_packet = tmp_path / "does_not_exist.json"
        script_path = REPO_ROOT / "scripts" / "local" / "run_autocoder_single_task.py"
        proc = subprocess.run(
            [
                "python3", str(script_path),
                "--task-packet-json", str(missing_packet),
                "--output-json", str(output_json),
                "--output-md", str(output_md),
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        # main() returns 1 on fatal; the summary must still be emitted.
        assert proc.returncode == 1
        assert output_json.exists()
        summary_path = output_json.with_name("run_summary.json")
        assert summary_path.exists(), "run_summary.json must be written on fatal paths"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["run_summary_version"] == "aed.run_summary.v0"
        assert summary["controller"] == "run_autocoder_single_task.py"
        assert summary["status"] == "HOLD_TASK_PACKET_INVALID"
        # No packet was available, so task_id / packet_kind are null.
        assert summary["task_id"] is None
        assert summary["packet_kind"] is None
        # hold_reason falls back to the error string.
        assert "File not found" in summary["hold_reason"]
        # Primary output paths are still recorded.
        assert summary["output_json"] == str(output_json)
        assert summary["output_md"] == str(output_md)


class TestRunSummaryReadyPopulatesChangedFiles:
    """Codex P2 (PRRT_kwDOSHFpYM6HhRdr / PRRT_kwDOSHFpYM6HhX2n): the READY
    run_summary.json must include the actual changed file list. Without
    the fix, the trimmed final-status result has no changed_files and
    run_summary.json reports changed_files: [] for successful patches.
    """

    def test_ready_run_summary_includes_changed_files(self, tmp_path):
        import uuid as _uuid
        task_id = f"rsum-cf-{_uuid.uuid4().hex[:8]}"
        output_root = tmp_path / "aed_runs" / task_id
        worktree_root = tmp_path / "wt" / task_id
        repo_under_test = tmp_path / "repo_under_test"
        temp_hermes_home = tmp_path / ".hermes"
        temp_hermes_home.mkdir()
        assert temp_hermes_home != Path.home() / ".hermes"
        controller_env = {**os.environ, "HERMES_HOME": str(temp_hermes_home)}
        branch_name = f"autocoder-rsum-cf-{task_id}"
        setup_result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "worktree", "add", "--detach",
             str(repo_under_test), "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
        assert setup_result.returncode == 0, setup_result.stderr
        controller_source_paths = [
            Path("scripts/local/preview_applied_branch_pr.py"),
            Path("scripts/local/verify_temp_worktree_applied_branch.py"),
            # Copy the patched controller itself so the subprocess runs
            # the b2023f05+ fix that populates run_summary.changed_files.
            Path("scripts/local/run_autocoder_single_task.py"),
        ]
        for rel_path in controller_source_paths:
            target = repo_under_test / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / rel_path, target)
        # Commit the source modifications (including the patched controller)
        # so the controller's main_git_status check stays clean.
        source_status_result = subprocess.run(
            ["git", "-C", str(repo_under_test), "status", "--porcelain", "--"]
            + [str(p) for p in controller_source_paths],
            capture_output=True,
            text=True,
        )
        if source_status_result.stdout.strip():
            subprocess.run(
                ["git", "-C", str(repo_under_test), "config", "user.email", "test@test.test"],
                capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "-C", str(repo_under_test), "config", "user.name", "Test"],
                capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "-C", str(repo_under_test), "add", "--"]
                + [str(p) for p in controller_source_paths],
                capture_output=True,
                text=True,
            )
            commit_sources_result = subprocess.run(
                ["git", "-C", str(repo_under_test), "commit",
                 "-m", "test controller script sources (with run_summary changed_files fix)"],
                capture_output=True,
                text=True,
            )
            assert commit_sources_result.returncode == 0, commit_sources_result.stderr
        # Pre-cleanup: drop any leftover branch / worktree
        subprocess.run(
            ["git", "-C", str(repo_under_test), "branch", "-D", branch_name],
            capture_output=True, text=True,
        )
        mock_path = f"scripts/local/_rsum_cf_{task_id}.py"
        required_tests = [f"tests/test_runsum_cf_{task_id}.py::test_x"]
        packet = make_packet(
            task_id=task_id,
            allowed_files=["scripts/local/*.py", mock_path],
            forbidden_files=[".github/**", "*.json", "*.md", "bin/", "examples/"],
            max_changed_files=3,
            required_tests=required_tests,
            output_root=str(output_root),
            worktree_root=str(worktree_root),
            branch_name=branch_name,
            suggested_pr_title=f"tooling: rsum changed_files {task_id}",
            suggested_pr_body="Verify run_summary.json populates changed_files on READY.",
            execution_mode="mocked",
            mock_edits=[{"path": mock_path, "content": f"# rsum changed_files {task_id}\n"}],
        )
        pkt_path = tmp_path / "packet.json"
        with open(pkt_path, "w") as f:
            json.dump(packet, f)
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        script_path = repo_under_test / "scripts" / "local" / "run_autocoder_single_task.py"

        try:
            argv = [
                "python3", str(script_path),
                "--task-packet-json", str(pkt_path),
                "--output-json", str(out_json),
                "--output-md", str(out_md),
                "--repo-root", str(repo_under_test),
            ]
            proc = subprocess.run(
                argv, cwd=str(repo_under_test), env=controller_env,
                capture_output=True, text=True, timeout=120,
            )
            assert out_json.exists(), (
                f"controller produced no output JSON; rc={proc.returncode}; "
                f"stderr={proc.stderr[:300]}"
            )
            cs = json.loads(out_json.read_text())
            assert cs.get("status") == "SINGLE_TASK_READY_FOR_HUMAN_REVIEW", (
                f"expected READY, got {cs.get('status')!r}; "
                f"stage={cs.get('stage')!r}; actual={cs.get('actual')!r}"
            )

            summary_path = out_json.with_name("run_summary.json")
            assert summary_path.exists(), (
                f"run_summary.json not found at {summary_path}"
            )
            summary = json.loads(summary_path.read_text())

            # Schema fields
            assert summary["run_summary_version"] == "aed.run_summary.v0"
            assert summary["controller"] == "run_autocoder_single_task.py"
            assert summary["status"] == "SINGLE_TASK_READY_FOR_HUMAN_REVIEW"
            assert summary["task_id"] == task_id
            # The whole point of the fix:
            assert summary["changed_files"], (
                "run_summary.changed_files must be populated for READY; "
                f"got {summary['changed_files']!r}"
            )
            assert mock_path in summary["changed_files"], (
                f"expected {mock_path!r} in run_summary.changed_files; "
                f"got {summary['changed_files']!r}"
            )
            # Final-status JSON must NOT have grown changed_files
            # (it stays the trimmed shape per the patch contract).
            assert "changed_files" not in cs, (
                "final_status.json must remain the trimmed shape; "
                f"unexpected changed_files key: {cs.get('changed_files')!r}"
            )
            # tests_run is also surfaced when the packet provides it
            assert summary["tests_run"] == required_tests
            # The artifact map still records the final_status paths.
            assert summary["artifacts"]["final_status_json"] == str(out_json)
            assert summary["artifacts"]["final_status_md"] == str(out_md)
        finally:
            # Cleanup: reset mock file, drop branch + worktree
            subprocess.run(
                ["git", "-C", str(repo_under_test), "reset", "HEAD", "--", mock_path],
                capture_output=True, text=True,
            )
            mock_full = repo_under_test / mock_path
            if mock_full.exists():
                mock_full.unlink()
            current_branch_result = subprocess.run(
                ["git", "-C", str(repo_under_test), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True,
            )
            if current_branch_result.stdout.strip() == branch_name:
                subprocess.run(
                    ["git", "-C", str(repo_under_test), "switch", "--detach", "HEAD"],
                    capture_output=True, text=True,
                )
            subprocess.run(
                ["git", "-C", str(repo_under_test), "branch", "-D", branch_name],
                capture_output=True, text=True,
            )
            if worktree_root.exists():
                subprocess.run(
                    ["git", "-C", str(repo_under_test), "worktree", "remove",
                     "--force", str(worktree_root)],
                    capture_output=True, text=True,
                )
            if repo_under_test.exists():
                subprocess.run(
                    ["git", "-C", str(REPO_ROOT), "worktree", "remove",
                     "--force", str(repo_under_test)],
                    capture_output=True, text=True,
                )


# ---------------------------------------------------------------------------
# controller_mode in _build_run_summary
# ---------------------------------------------------------------------------

class TestRunSummaryControllerMode:
    """Tests for the controller_mode field in _build_run_summary."""

    def _import_build(self):
        import sys
        sys.path.insert(0, str(SCRIPT_DIR))
        from run_autocoder_single_task import _build_run_summary
        return _build_run_summary

    def test_default_controller_mode_is_mocked(self, tmp_path):
        """Default controller_mode is the literal string "mocked"."""
        from pathlib import Path
        _build_run_summary = self._import_build()
        out = _build_run_summary(
            {"status": "READY", "task_id": "t1", "artifacts": {}},
            Path(str(tmp_path / "_unused.json")),
            None,
            None,
        )
        assert out["controller_mode"] == "mocked"

    def test_controller_mode_from_task_packet(self, tmp_path):
        """Packet-level controller_mode overrides the default."""
        from pathlib import Path
        _build_run_summary = self._import_build()
        out = _build_run_summary(
            {"status": "READY", "task_id": "t1", "artifacts": {}},
            Path(str(tmp_path / "_unused.json")),
            None,
            {"controller_mode": "real"},
        )
        assert out["controller_mode"] == "real"

    def test_controller_mode_falls_back_to_result(self, tmp_path):
        """If packet has no controller_mode, result.controller_mode is used."""
        from pathlib import Path
        _build_run_summary = self._import_build()
        out = _build_run_summary(
            {
                "status": "READY",
                "task_id": "t1",
                "artifacts": {},
                "controller_mode": "live",
            },
            Path(str(tmp_path / "_unused.json")),
            None,
            None,
        )
        assert out["controller_mode"] == "live"

    def test_controller_mode_distinct_from_execution_mode(self, tmp_path):
        """controller_mode and execution_mode are independent fields."""
        from pathlib import Path
        _build_run_summary = self._import_build()
        out = _build_run_summary(
            {
                "status": "READY",
                "task_id": "t1",
                "execution_mode": "mocked",
                "artifacts": {},
            },
            Path(str(tmp_path / "_unused.json")),
            None,
            None,
        )
        assert "controller_mode" in out
        assert "execution_mode" in out
        assert out["controller_mode"] == "mocked"
        assert out["execution_mode"] == "mocked"

    def test_final_status_outcome_unchanged(self, tmp_path):
        """_build_run_summary does NOT write final_status.json or .md; the
        sidecar run_summary.json is the only file produced."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(SCRIPT_DIR))
        from run_autocoder_single_task import (
            _build_run_summary, _write_run_summary,
        )
        out_json = tmp_path / "final_status.json"
        out_md = tmp_path / "final_status.md"
        result = {
            "status": "READY",
            "task_id": "t1",
            "execution_mode": "mocked",
            "artifacts": {},
        }
        # _build_run_summary alone must not produce any sidecar IO.
        _build_run_summary(result, out_json, out_md, None)
        assert not out_json.exists(), (
            "_build_run_summary must not write final_status.json"
        )
        assert not out_md.exists(), (
            "_build_run_summary must not write final_status.md"
        )
        # _write_run_summary must write the run_summary.json sidecar only,
        # not the final_status paths.
        summary_path = _write_run_summary(
            result=result,
            output_json_path=out_json,
            output_md_path=out_md,
            task_packet=None,
        )
        assert summary_path is not None
        assert summary_path.name == "run_summary.json"
        assert summary_path.exists()
        assert not out_json.exists(), (
            "_write_run_summary must not write final_status.json"
        )
        assert not out_md.exists(), (
            "_write_run_summary must not write final_status.md"
        )
        with open(summary_path) as f:
            summary = json.load(f)
        assert summary["controller_mode"] == "mocked"

    def test_run_summary_includes_known_keys(self, tmp_path):
        """Sanity guard: the standard key set is preserved (with
        controller_mode added)."""
        from pathlib import Path
        _build_run_summary = self._import_build()
        out = _build_run_summary(
            {"status": "READY", "task_id": "t1", "artifacts": {}},
            Path(str(tmp_path / "_unused.json")),
            None,
            None,
        )
        expected_keys = {
            "run_summary_version", "controller", "controller_mode",
            "generated_at", "task_id", "packet_kind", "execution_mode",
            "status", "stage", "hold_reason", "base_sha", "head_sha",
            "branch_name", "changed_files", "tests_run", "artifacts",
            "output_json", "output_md",
        }
        missing = expected_keys - set(out.keys())
        assert not missing, f"missing keys: {sorted(missing)}"
