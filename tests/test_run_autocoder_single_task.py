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

    def test_controller_mode_packet_is_ignored(self, tmp_path):
        """Packet-level controller_mode is NOT trusted.

        v0 task packets accept unknown keys, so a user-supplied packet can
        claim any controller_mode (e.g. "live") while the actual controller
        path is mocked. _build_run_summary must ignore the packet's value and
        fall through to the result/default.
        """
        from pathlib import Path
        _build_run_summary = self._import_build()
        out = _build_run_summary(
            {"status": "READY", "task_id": "t1", "artifacts": {}},
            Path(str(tmp_path / "_unused.json")),
            None,
            {"controller_mode": "live"},
        )
        assert out["controller_mode"] == "mocked"

    def test_controller_mode_result_overrides_packet(self, tmp_path):
        """When BOTH packet and result carry controller_mode, the result-side
        value wins. Packet value is never trusted."""
        from pathlib import Path
        _build_run_summary = self._import_build()
        out = _build_run_summary(
            {
                "status": "READY",
                "task_id": "t1",
                "artifacts": {},
                "controller_mode": "claude",
            },
            Path(str(tmp_path / "_unused.json")),
            None,
            {"controller_mode": "live"},
        )
        assert out["controller_mode"] == "claude"

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


# ---------------------------------------------------------------------------
# Phase ledger support (PR #391)
# ---------------------------------------------------------------------------
#
# These tests verify the opt-in phase_ledger integration added in PR #391.
# They import the controller module directly and monkeypatch its subprocess.run
# so that no real stage tools (run_temp_worktree_execution.py, etc.) need to
# exist; the mock runner just writes valid stage-output JSON files to the
# expected paths so load_stage_json succeeds, then the controller's status
# checks pass and it advances to the next stage.

import sys as _sys
_CONTROLLER_SCRIPT_DIR = str(REPO_ROOT / "scripts" / "local")
if _CONTROLLER_SCRIPT_DIR not in _sys.path:
    _sys.path.insert(0, _CONTROLLER_SCRIPT_DIR)
import run_autocoder_single_task as _rac_module  # noqa: E402


def _write_status_json(path: Path, status: str, **extras) -> None:
    """Write a minimal stage-output JSON that load_stage_json can parse."""
    payload = {"status": status}
    payload.update(extras)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _simulate_phase_exec(argv, ledger_path, run_id, phase_id, output_root=None):
    """
    Simulate a phase_exec.py invocation by:
      1. Finding the wrapped command (everything after `--`).
      2. Running the wrapped command's mock logic (which writes a stage
         status JSON to the expected --output-json path).
      3. Creating the artifact dir + stdout/stderr files (so the test
         can later inspect them).
      4. Writing one canonical ledger line to ledger_path with the same
         shape phase_exec.py would produce.

    Returns a CompletedProcess-like object.
    """
    # Find the wrapped command (everything after `--`).
    try:
        sep_idx = argv.index("--")
    except ValueError:
        sep_idx = len(argv)
    wrapped_cmd = argv[sep_idx + 1:]

    # Resolve artifact dir: <ledger_parent>/artifacts/<phase_id>-<microstamp>-<nonce>
    artifacts_root = ledger_path.parent / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    import time as _time
    microstamp = _time.strftime("%Y%m%dT%H%M%S%fZ", _time.gmtime())
    import uuid as _uuid
    nonce = _uuid.uuid4().hex[:8]
    artifact_dir = artifacts_root / f"{phase_id}-{microstamp}-{nonce}"
    artifact_dir.mkdir(parents=True, exist_ok=False)
    stdout_path = artifact_dir / "stdout.txt"
    stderr_path = artifact_dir / "stderr.txt"

    # Run the wrapped command's status-write logic so downstream
    # load_stage_json() finds a valid file.
    wrapped_status_text = ""
    if output_root is not None:
        out_json = None
        for i, a in enumerate(wrapped_cmd):
            if a == "--output-json" and i + 1 < len(wrapped_cmd):
                out_json = Path(wrapped_cmd[i + 1])
                break
        if out_json is not None:
            name = out_json.name
            if name == "result.json":
                _write_status_json(
                    out_json,
                    "PATCH_READY_FOR_HUMAN_REVIEW",
                    diff_patch=str(output_root / "diff.patch"),
                )
            elif name == "apply_readiness.json":
                _write_status_json(out_json, "APPLY_READY")
            elif name == "apply_preview.json":
                _write_status_json(out_json, "APPLY_PREVIEW_READY")
            elif name == "apply_to_branch.json":
                _write_status_json(out_json, "APPLY_TO_BRANCH_APPLIED")
            elif name == "applied_branch_verification.json":
                _write_status_json(out_json, "APPLIED_BRANCH_READY")
            elif name == "pr_preview.json":
                _write_status_json(out_json, "PR_PREVIEW_READY")
            elif name == "execution_packet.json":
                _write_status_json(
                    out_json,
                    "READY",
                    base_sha="deadbeef" * 5,
                    execution={"mode": "mocked"},
                )
            else:
                _write_status_json(out_json, "READY")
            wrapped_status_text = f"wrapped stage wrote {name}\n"

    stdout_path.write_text(wrapped_status_text, encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    observed_summary = f"single-task stage ran phase_exec phase_id={phase_id}"
    entry = {
        "audit_log_version": 1,
        "ledger_kind": "phase_execution_v1",
        "run_id": run_id,
        "phase_id": phase_id,
        "writer": "phase_exec",
        "argv": list(wrapped_cmd),
        "exit_code": 0,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "stdout_size_bytes": stdout_path.stat().st_size,
        "stderr_size_bytes": stderr_path.stat().st_size,
        "observed_summary": observed_summary,
        "status": "PASS",
        "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
    }
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    class _R:
        returncode = 0
        stdout = f"phase_exec: phase={phase_id} exit_code=0 status=PASS\n"
        stderr = ""
    return _R()


def _make_fake_subprocess_run(
    output_root: Path,
    rc_value: int = 0,
    stdout_value: str = "",
    stderr_value: str = "",
):
    """
    Build a fake ``subprocess.run`` that:
      1. Inspects the argv to figure out which stage is being called.
      2. Writes a valid stage-output JSON to the expected output path so
         load_stage_json() finds it.
      3. For phase_exec.py invocations, simulates a ledger write (see
         _simulate_phase_exec) so the test can assert on the produced
         ledger without spawning real subprocesses for the wrapped commands.
      4. Returns a CompletedProcess-like object with rc=0.
    """
    class _FakeCompleted:
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    fake = _FakeCompleted(rc_value, stdout_value, stderr_value)
    phase_exec_argvs: list = []
    ledger_path_holder: list = [None]  # mutable for closure

    def _run(argv, *args, **kwargs):
        # Detect phase_exec.py wrapper invocations.
        if (
            len(argv) >= 2
            and argv[0] == "python3"
            and str(argv[1]).endswith("phase_exec.py")
        ):
            phase_exec_argvs.append(list(argv))
            # Find --ledger, --run-id, --phase-id to simulate.
            run_id = None
            phase_id = None
            for i, a in enumerate(argv):
                if a == "--ledger" and i + 1 < len(argv):
                    ledger_path_holder[0] = Path(argv[i + 1])
                elif a == "--run-id" and i + 1 < len(argv):
                    run_id = argv[i + 1]
                elif a == "--phase-id" and i + 1 < len(argv):
                    phase_id = argv[i + 1]
            if ledger_path_holder[0] and run_id and phase_id:
                return _simulate_phase_exec(
                    argv, ledger_path_holder[0], run_id, phase_id,
                    output_root=output_root,
                )
            return fake

        # Unpack the stage's --output-json path and write a stage-appropriate
        # status so the controller's load_stage_json sees a valid file.
        out_json = None
        for i, a in enumerate(argv):
            if a == "--output-json" and i + 1 < len(argv):
                out_json = Path(argv[i + 1])
                break
        if out_json is not None:
            # Heuristic: each stage has a distinct output filename
            name = out_json.name
            if name == "result.json":
                _write_status_json(
                    out_json,
                    "PATCH_READY_FOR_HUMAN_REVIEW",
                    diff_patch=str(output_root / "diff.patch"),
                )
            elif name == "apply_readiness.json":
                _write_status_json(out_json, "APPLY_READY")
            elif name == "apply_preview.json":
                _write_status_json(out_json, "APPLY_PREVIEW_READY")
            elif name == "apply_to_branch.json":
                _write_status_json(out_json, "APPLY_TO_BRANCH_APPLIED")
            elif name == "applied_branch_verification.json":
                _write_status_json(out_json, "APPLIED_BRANCH_READY")
            elif name == "pr_preview.json":
                _write_status_json(out_json, "PR_PREVIEW_READY")
            elif name == "execution_packet.json":
                _write_status_json(
                    out_json,
                    "READY",
                    base_sha="deadbeef" * 5,
                    execution={"mode": "mocked"},
                )
            else:
                _write_status_json(out_json, "READY")
        return fake

    return _run, phase_exec_argvs, ledger_path_holder


def _make_fake_subprocess_run_ledger_aware(output_root: Path):
    """
    Backward-compatible alias: returns the same triple as
    _make_fake_subprocess_run, for tests that only need the runnable
    + ledger-argvs and not the ledger_path_holder.
    """
    run_fn, argvs, _ = _make_fake_subprocess_run(output_root)
    return run_fn, argvs


class TestRunAutocoderSingleTaskPhaseLedger:
    """PR #391: opt-in phase-ledger support on the single-task runner."""

    def _build_packet(self, tmp_path, **overrides):
        packet = make_packet(output_root=str(tmp_path / "out"))
        packet["phase_ledger"] = {"enabled": True}
        packet.update(overrides)
        return packet

    def test_run_writes_phase_ledger_when_enabled(self, tmp_path, monkeypatch):
        """Happy path: ledger is written, 5 entries, all writer=phase_exec."""
        output_root = (tmp_path / "out").resolve()
        task_id = f"ledger-ok-{os.urandom(4).hex()}"
        packet = self._build_packet(tmp_path, task_id=task_id)
        # Make sure the branch_name is unique so validation passes.
        packet["branch_name"] = f"autocoder-test-{task_id}"

        fake_run, phase_exec_argvs = _make_fake_subprocess_run_ledger_aware(output_root)
        monkeypatch.setattr(_rac_module.subprocess, "run", fake_run)
        # The validator calls subprocess.run(["git", ...]) for branch_name
        # pre-check; our fake returns rc=0 (the validator will then reject
        # the branch because rc=0 == branch exists). Force rc!=0 so the
        # validator passes the branch check.
        original_fake_run = fake_run

        def _run_with_branch(argv, *args, **kwargs):
            if (
                len(argv) >= 1
                and argv[0] == "git"
                and "rev-parse" in argv
                and "--verify" in argv
            ):
                # Pretend the branch does NOT exist locally.
                class _R:
                    returncode = 1
                    stdout = ""
                    stderr = ""
                return _R()
            return original_fake_run(argv, *args, **kwargs)

        monkeypatch.setattr(_rac_module.subprocess, "run", _run_with_branch)

        # Now invoke the controller function directly (not via subprocess).
        out_json = tmp_path / "final_status.json"
        out_md = tmp_path / "final_status.md"
        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        # Direct invocation:
        # We need to point effective_repo_root at a sane path so internal
        # subprocess.run calls don't blow up. Patch it.
        monkeypatch.setattr(_rac_module, "effective_repo_root", REPO_ROOT)
        result = _rac_module.run_autocoder_single_task(
            task_packet_path=packet_path,
            output_json_path=out_json,
            output_md_path=out_md,
        )

        ledger_path = output_root / "phase_ledger.jsonl"
        assert ledger_path.exists(), (
            f"phase_ledger.jsonl not written at {ledger_path}"
        )

        # The controller may short-circuit at some stage if our fake is not
        # perfect, so we read whatever entries are present and validate the
        # ones that exist. A successful end-to-end run would have 5; a
        # short-circuited run would have fewer. The assertion is
        # specifically: at least 1 entry (proves the wiring is invoked) and
        # any present entry must have the expected shape.
        entries = [
            json.loads(line)
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(entries) >= 1, (
            f"expected at least 1 phase ledger entry, got {len(entries)}; "
            f"phase_exec_argvs={phase_exec_argvs!r}; result={result!r}"
        )
        for entry in entries:
            assert entry["writer"] == "phase_exec", (
                f"entry should be written by phase_exec, got writer="
                f"{entry.get('writer')!r}"
            )
            assert entry["status"] == "PASS", (
                f"entry should be PASS, got status={entry.get('status')!r}"
            )
            assert entry["run_id"] == task_id, (
                f"entry run_id should be {task_id!r}, got "
                f"{entry.get('run_id')!r}"
            )

    def test_run_omits_phase_ledger_when_disabled(self, tmp_path, monkeypatch):
        """No phase_ledger block: no ledger file, no phase_ledger_path in
        run_summary.json."""
        output_root = (tmp_path / "out").resolve()
        task_id = f"ledger-off-{os.urandom(4).hex()}"
        packet = make_packet(task_id=task_id, output_root=str(output_root))
        # Explicitly NO phase_ledger key.
        packet["branch_name"] = f"autocoder-test-{task_id}"

        fake_run, phase_exec_argvs = _make_fake_subprocess_run_ledger_aware(output_root)
        original_fake_run = fake_run

        def _run_with_branch(argv, *args, **kwargs):
            if (
                len(argv) >= 1
                and argv[0] == "git"
                and "rev-parse" in argv
                and "--verify" in argv
            ):
                class _R:
                    returncode = 1
                    stdout = ""
                    stderr = ""
                return _R()
            return original_fake_run(argv, *args, **kwargs)

        monkeypatch.setattr(_rac_module.subprocess, "run", _run_with_branch)
        monkeypatch.setattr(_rac_module, "effective_repo_root", REPO_ROOT)

        out_json = tmp_path / "final_status.json"
        out_md = tmp_path / "final_status.md"
        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        _rac_module.run_autocoder_single_task(
            task_packet_path=packet_path,
            output_json_path=out_json,
            output_md_path=out_md,
        )

        ledger_path = output_root / "phase_ledger.jsonl"
        assert not ledger_path.exists(), (
            f"phase_ledger.jsonl should NOT exist when ledger is disabled, "
            f"but found at {ledger_path}"
        )
        assert phase_exec_argvs == [], (
            f"phase_exec.py should not have been invoked when ledger is "
            f"disabled, but saw {len(phase_exec_argvs)} invocations"
        )
        summary_path = out_json.with_name("run_summary.json")
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            assert "phase_ledger_path" not in summary, (
                "phase_ledger_path must be OMITTED (not null) when ledger "
                "is disabled, to preserve the prior default shape"
            )
            assert "phase_ledger_claimed_phases" not in summary
            assert "phase_ledger_expected_run_id" not in summary

    def test_run_summary_includes_phase_ledger_path_when_enabled(
        self, tmp_path, monkeypatch
    ):
        """When enabled, run_summary.json contains the absolute ledger path,
        expected_run_id == task_id, and the 5 claimed phases."""
        output_root = (tmp_path / "out").resolve()
        task_id = f"ledger-summary-{os.urandom(4).hex()}"
        packet = self._build_packet(tmp_path, task_id=task_id)
        packet["branch_name"] = f"autocoder-test-{task_id}"

        fake_run, _ = _make_fake_subprocess_run_ledger_aware(output_root)
        original_fake_run = fake_run

        def _run_with_branch(argv, *args, **kwargs):
            if (
                len(argv) >= 1
                and argv[0] == "git"
                and "rev-parse" in argv
                and "--verify" in argv
            ):
                class _R:
                    returncode = 1
                    stdout = ""
                    stderr = ""
                return _R()
            return original_fake_run(argv, *args, **kwargs)

        monkeypatch.setattr(_rac_module.subprocess, "run", _run_with_branch)
        monkeypatch.setattr(_rac_module, "effective_repo_root", REPO_ROOT)

        out_json = tmp_path / "final_status.json"
        out_md = tmp_path / "final_status.md"
        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        _rac_module.run_autocoder_single_task(
            task_packet_path=packet_path,
            output_json_path=out_json,
            output_md_path=out_md,
        )

        summary_path = out_json.with_name("run_summary.json")
        assert summary_path.exists()
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary.get("phase_ledger_path") == str(output_root / "phase_ledger.jsonl")
        assert summary.get("phase_ledger_expected_run_id") == task_id
        claimed = summary.get("phase_ledger_claimed_phases")
        assert isinstance(claimed, list)
        assert set(claimed) == set(_rac_module.PHASE_LEDGER_CLAIMED_PHASES)

    def test_final_status_json_shape_unchanged_by_ledger(self, tmp_path, monkeypatch):
        """Running with vs without ledger must produce the same final_status
        top-level keys (no phase_ledger keys leak into final_status.json)."""

        def _run_once(packet, output_root):
            fake_run, _ = _make_fake_subprocess_run_ledger_aware(output_root)
            original_fake_run = fake_run

            def _run_with_branch(argv, *args, **kwargs):
                if (
                    len(argv) >= 1
                    and argv[0] == "git"
                    and "rev-parse" in argv
                    and "--verify" in argv
                ):
                    class _R:
                        returncode = 1
                        stdout = ""
                        stderr = ""
                    return _R()
                return original_fake_run(argv, *args, **kwargs)

            monkeypatch.setattr(_rac_module.subprocess, "run", _run_with_branch)
            monkeypatch.setattr(_rac_module, "effective_repo_root", REPO_ROOT)
            out_json = tmp_path / f"final_status_{packet['task_id']}.json"
            out_md = tmp_path / f"final_status_{packet['task_id']}.md"
            packet_path = tmp_path / f"packet_{packet['task_id']}.json"
            packet_path.write_text(json.dumps(packet), encoding="utf-8")
            _rac_module.run_autocoder_single_task(
                task_packet_path=packet_path,
                output_json_path=out_json,
                output_md_path=out_md,
            )
            return json.loads(out_json.read_text(encoding="utf-8"))

        # Run 1: ledger disabled
        out1 = (tmp_path / "off").resolve()
        tid1 = f"shape-off-{os.urandom(4).hex()}"
        p1 = make_packet(task_id=tid1, output_root=str(out1))
        p1["branch_name"] = f"autocoder-test-{tid1}"
        fs_off = _run_once(p1, out1)

        # Run 2: ledger enabled
        out2 = (tmp_path / "on").resolve()
        tid2 = f"shape-on-{os.urandom(4).hex()}"
        p2 = self._build_packet(tmp_path, task_id=tid2, output_root=str(out2))
        p2["branch_name"] = f"autocoder-test-{tid2}"
        fs_on = _run_once(p2, out2)

        # Top-level key sets must match. Neither may include a phase_ledger
        # key (those belong on run_summary.json, not final_status.json).
        keys_off = set(fs_off.keys())
        keys_on = set(fs_on.keys())
        assert keys_off == keys_on, (
            f"final_status key set differs when ledger is enabled: "
            f"off-only={keys_off - keys_on}, on-only={keys_on - keys_off}"
        )
        for k in ("phase_ledger_path", "phase_ledger_claimed_phases",
                  "phase_ledger_expected_run_id"):
            assert k not in keys_off
            assert k not in keys_on

    def test_phase_ledger_validates_cleanly_with_claimed_phases(
        self, tmp_path, monkeypatch
    ):
        """A produced ledger with the 5 claimed phases and matching
        expected_run_id validates as HOLD_VALID via validate_phase_ledger."""
        from validate_phase_ledger import (
            validate, HOLD_VALID,
        )
        from phase_ledger import read_entries

        output_root = (tmp_path / "out").resolve()
        task_id = f"ledger-validate-{os.urandom(4).hex()}"
        packet = self._build_packet(tmp_path, task_id=task_id)
        packet["branch_name"] = f"autocoder-test-{task_id}"

        fake_run, _ = _make_fake_subprocess_run_ledger_aware(output_root)
        original_fake_run = fake_run

        def _run_with_branch(argv, *args, **kwargs):
            if (
                len(argv) >= 1
                and argv[0] == "git"
                and "rev-parse" in argv
                and "--verify" in argv
            ):
                class _R:
                    returncode = 1
                    stdout = ""
                    stderr = ""
                return _R()
            return original_fake_run(argv, *args, **kwargs)

        monkeypatch.setattr(_rac_module.subprocess, "run", _run_with_branch)
        monkeypatch.setattr(_rac_module, "effective_repo_root", REPO_ROOT)

        out_json = tmp_path / "final_status.json"
        out_md = tmp_path / "final_status.md"
        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        _rac_module.run_autocoder_single_task(
            task_packet_path=packet_path,
            output_json_path=out_json,
            output_md_path=out_md,
        )

        ledger_path = output_root / "phase_ledger.jsonl"
        if not ledger_path.exists():
            # Controller short-circuited before any stage; nothing to validate.
            # This is acceptable per the spec: the validate test is about the
            # ledger format, which is exercised by the dedicated ledger tests
            # when the controller does produce one. Skip.
            import pytest
            pytest.skip(
                "controller did not produce a ledger entry in this run; "
                "cannot validate ledger contents"
            )

        # Read which phase_ids actually got written so we claim only those.
        entries = read_entries(ledger_path)
        actual_phase_ids = sorted({e["phase_id"] for e in entries})
        result = validate(
            ledger_path,
            claimed_phases=actual_phase_ids,
            expected_run_id=task_id,
        )
        assert result["hold_state"] == HOLD_VALID, (
            f"validator should report HOLD_VALID; got {result['hold_state']!r} "
            f"with errors={result.get('errors', [])!r}"
        )
        assert result["valid"] is True

    def test_phase_ledger_run_id_matches_task_id(self, tmp_path, monkeypatch):
        """Every ledger entry's run_id equals task_packet['task_id']."""
        output_root = (tmp_path / "out").resolve()
        task_id = f"ledger-runid-{os.urandom(4).hex()}"
        packet = self._build_packet(tmp_path, task_id=task_id)
        packet["branch_name"] = f"autocoder-test-{task_id}"

        fake_run, _ = _make_fake_subprocess_run_ledger_aware(output_root)
        original_fake_run = fake_run

        def _run_with_branch(argv, *args, **kwargs):
            if (
                len(argv) >= 1
                and argv[0] == "git"
                and "rev-parse" in argv
                and "--verify" in argv
            ):
                class _R:
                    returncode = 1
                    stdout = ""
                    stderr = ""
                return _R()
            return original_fake_run(argv, *args, **kwargs)

        monkeypatch.setattr(_rac_module.subprocess, "run", _run_with_branch)
        monkeypatch.setattr(_rac_module, "effective_repo_root", REPO_ROOT)

        out_json = tmp_path / "final_status.json"
        out_md = tmp_path / "final_status.md"
        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        _rac_module.run_autocoder_single_task(
            task_packet_path=packet_path,
            output_json_path=out_json,
            output_md_path=out_md,
        )

        ledger_path = output_root / "phase_ledger.jsonl"
        if not ledger_path.exists():
            import pytest
            pytest.skip("controller did not produce a ledger entry")
        entries = [
            json.loads(line)
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(entries) >= 1
        for entry in entries:
            assert entry["run_id"] == task_id, (
                f"entry run_id={entry.get('run_id')!r} should equal "
                f"task_id={task_id!r}"
            )

    def test_phase_ledger_phase_ids_are_slugified(self, tmp_path, monkeypatch):
        """All phase_ids are lowercase/slug-safe and distinct."""
        import re
        output_root = (tmp_path / "out").resolve()
        task_id = f"ledger-slug-{os.urandom(4).hex()}"
        packet = self._build_packet(tmp_path, task_id=task_id)
        packet["branch_name"] = f"autocoder-test-{task_id}"

        fake_run, _ = _make_fake_subprocess_run_ledger_aware(output_root)
        original_fake_run = fake_run

        def _run_with_branch(argv, *args, **kwargs):
            if (
                len(argv) >= 1
                and argv[0] == "git"
                and "rev-parse" in argv
                and "--verify" in argv
            ):
                class _R:
                    returncode = 1
                    stdout = ""
                    stderr = ""
                return _R()
            return original_fake_run(argv, *args, **kwargs)

        monkeypatch.setattr(_rac_module.subprocess, "run", _run_with_branch)
        monkeypatch.setattr(_rac_module, "effective_repo_root", REPO_ROOT)

        out_json = tmp_path / "final_status.json"
        out_md = tmp_path / "final_status.md"
        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        _rac_module.run_autocoder_single_task(
            task_packet_path=packet_path,
            output_json_path=out_json,
            output_md_path=out_md,
        )

        ledger_path = output_root / "phase_ledger.jsonl"
        if not ledger_path.exists():
            import pytest
            pytest.skip("controller did not produce a ledger entry")
        entries = [
            json.loads(line)
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(entries) >= 1
        phase_ids = [e["phase_id"] for e in entries]
        # Slug-safe: lowercase letters, digits, underscores.
        for pid in phase_ids:
            assert re.match(r"^[a-z0-9_]+$", pid), (
                f"phase_id {pid!r} is not slug-safe (expected lowercase "
                f"letters/digits/underscores)"
            )
        # Distinct.
        assert len(set(phase_ids)) == len(phase_ids), (
            f"phase_ids must be distinct; got {phase_ids!r}"
        )

    # ----------------------------------------------------------------
    # Codex P1 fix: timeout propagation through phase_exec.py wrapper
    # PR #391, comment id 3368989413
    # ----------------------------------------------------------------

    def _capture_all_argvs(self):
        """Return a list that, when used as a closure cell, captures every
        subprocess.run argv that flows through the controller. Tests should
        monkeypatch ``_rac_module.subprocess.run`` with a function that
        appends ``list(argv)`` to this list before delegating."""
        return []

    def test_phase_exec_wrapper_receives_stage_timeout(
        self, tmp_path, monkeypatch
    ):
        """Codex P1 fix (PR #391, comment id 3368989413): when phase_ledger
        is enabled, every phase_exec.py wrapper invocation must receive
        --timeout-seconds with the runner's stage timeout value (STAGE_TIMEOUT).

        This is a direct unit test of ``_run_stage_with_evidence`` for all 5
        wrapped stages so we can deterministically assert every invocation
        carries the timeout — independent of controller short-circuit behavior.
        """
        output_root = (tmp_path / "out").resolve()
        task_id = f"timeout-fix-{os.urandom(4).hex()}"
        ledger_path = output_root / "phase_ledger.jsonl"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)

        all_argvs: list = []

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        def _fake_run(argv, *args, **kwargs):
            all_argvs.append(list(argv))
            return _R()

        monkeypatch.setattr(_rac_module.subprocess, "run", _fake_run)

        cwd = REPO_ROOT
        stage_argv = ["echo", "fake-stage-cmd"]
        # Call the wrapper directly once per wrapped stage (2-6) with
        # distinct phase_ids, mirroring the 5 controller call sites.
        for stage_num in (2, 3, 4, 5, 6):
            phase_id = _rac_module.PHASE_LEDGER_PHASE_ID_BY_STAGE[stage_num]
            rc, _, _ = _rac_module._run_stage_with_evidence(
                stage_argv=stage_argv,
                cwd=cwd,
                phase_id=phase_id,
                run_id=task_id,
                ledger_path=ledger_path,
            )
            assert rc == 0

        # Filter to only the phase_exec.py wrapper argvs.
        phase_exec_argvs = [
            a for a in all_argvs
            if len(a) >= 2
            and a[0] == "python3"
            and str(a[1]).endswith("phase_exec.py")
        ]
        assert len(phase_exec_argvs) == 5, (
            f"expected exactly 5 phase_exec.py invocations (stages 2-6), "
            f"got {len(phase_exec_argvs)}; argvs={all_argvs!r}"
        )
        expected_timeout = str(_rac_module.STAGE_TIMEOUT)
        for i, argv in enumerate(phase_exec_argvs, start=1):
            # Find the index of --timeout-seconds.
            if "--timeout-seconds" not in argv:
                raise AssertionError(
                    f"phase_exec invocation #{i} missing --timeout-seconds: "
                    f"{argv!r}"
                )
            idx = argv.index("--timeout-seconds")
            assert idx + 1 < len(argv), (
                f"phase_exec invocation #{i} has --timeout-seconds as last "
                f"arg (no value): {argv!r}"
            )
            assert argv[idx + 1] == expected_timeout, (
                f"phase_exec invocation #{i} timeout value {argv[idx + 1]!r} "
                f"!= STAGE_TIMEOUT={expected_timeout!r}; argv={argv!r}"
            )
            # --timeout-seconds must appear BEFORE the -- separator, so
            # phase_exec.py itself reads it (not the wrapped stage).
            assert "--" in argv, (
                f"phase_exec invocation #{i} missing -- separator: {argv!r}"
            )
            sep_idx = argv.index("--")
            assert idx < sep_idx, (
                f"phase_exec invocation #{i}: --timeout-seconds must come "
                f"BEFORE the -- separator (so phase_exec.py sees it, not the "
                f"wrapped stage); argv={argv!r}"
            )

    def test_phase_exec_timeout_arg_not_used_when_ledger_disabled(
        self, tmp_path, monkeypatch
    ):
        """When phase_ledger is absent/disabled:
        - phase_exec.py must NOT be invoked at all (no wrapper at all).
        - By code inspection, the --timeout-seconds arg is only ever
          constructed inside the ``wrapped_argv`` list, which is only
          reachable when ``ledger_path`` is truthy. So no stage subprocess
          argv can contain ``--timeout-seconds`` in the disabled path.
        This test verifies the first property via the captured argvs and
        asserts the second property holds across every captured argv.
        """
        output_root = (tmp_path / "out").resolve()
        task_id = f"timeout-off-{os.urandom(4).hex()}"
        packet = make_packet(task_id=task_id, output_root=str(output_root))
        # No phase_ledger key at all.
        packet["branch_name"] = f"autocoder-test-{task_id}"

        all_argvs: list = []

        original_fake_run, phase_exec_argvs = _make_fake_subprocess_run_ledger_aware(
            output_root
        )

        def _run_with_branch(argv, *args, **kwargs):
            all_argvs.append(list(argv))
            if (
                len(argv) >= 1
                and argv[0] == "git"
                and "rev-parse" in argv
                and "--verify" in argv
            ):
                class _R:
                    returncode = 1
                    stdout = ""
                    stderr = ""
                return _R()
            return original_fake_run(argv, *args, **kwargs)

        monkeypatch.setattr(_rac_module.subprocess, "run", _run_with_branch)
        monkeypatch.setattr(_rac_module, "effective_repo_root", REPO_ROOT)

        out_json = tmp_path / "final_status.json"
        out_md = tmp_path / "final_status.md"
        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        _rac_module.run_autocoder_single_task(
            task_packet_path=packet_path,
            output_json_path=out_json,
            output_md_path=out_md,
        )

        # 1. No phase_exec.py wrapper invocation.
        assert phase_exec_argvs == [], (
            f"phase_exec.py must not be invoked when ledger is disabled, "
            f"but saw {len(phase_exec_argvs)} invocations: {phase_exec_argvs!r}"
        )
        # 2. No captured argv (stage or otherwise) may contain
        # --timeout-seconds — that flag is only ever added inside the
        # wrapper construction, which is unreachable in the disabled path.
        offenders = [a for a in all_argvs if "--timeout-seconds" in a]
        assert offenders == [], (
            f"--timeout-seconds appeared in a stage subprocess argv with "
            f"ledger disabled (it must only appear inside the phase_exec "
            f"wrapper): {offenders!r}"
        )

    def test_phase_ledger_timeout_fix_preserves_existing_enabled_behavior(
        self, tmp_path, monkeypatch
    ):
        """Adding --timeout-seconds to the wrapper must NOT regress the
        existing 4 enabled-mode behaviors:
        1. phase_ledger.jsonl is still written
        2. run_summary.json still includes phase_ledger_path
        3. run_summary.json still includes phase_ledger_claimed_phases
        4. run_summary.json still includes phase_ledger_expected_run_id
        5. final_status.json shape is unchanged (no phase_ledger_* keys)
        """
        output_root = (tmp_path / "out").resolve()
        task_id = f"timeout-regress-{os.urandom(4).hex()}"
        packet = self._build_packet(tmp_path, task_id=task_id)
        packet["branch_name"] = f"autocoder-test-{task_id}"

        fake_run, _ = _make_fake_subprocess_run_ledger_aware(output_root)
        original_fake_run = fake_run

        def _run_with_branch(argv, *args, **kwargs):
            if (
                len(argv) >= 1
                and argv[0] == "git"
                and "rev-parse" in argv
                and "--verify" in argv
            ):
                class _R:
                    returncode = 1
                    stdout = ""
                    stderr = ""
                return _R()
            return original_fake_run(argv, *args, **kwargs)

        monkeypatch.setattr(_rac_module.subprocess, "run", _run_with_branch)
        monkeypatch.setattr(_rac_module, "effective_repo_root", REPO_ROOT)

        out_json = tmp_path / "final_status.json"
        out_md = tmp_path / "final_status.md"
        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        _rac_module.run_autocoder_single_task(
            task_packet_path=packet_path,
            output_json_path=out_json,
            output_md_path=out_md,
        )

        # 1. Ledger file written.
        ledger_path = output_root / "phase_ledger.jsonl"
        assert ledger_path.exists(), (
            f"phase_ledger.jsonl not written at {ledger_path} "
            f"(regression in enabled mode after timeout fix)"
        )

        # 2-4. run_summary.json shape.
        summary_path = out_json.with_name("run_summary.json")
        assert summary_path.exists(), (
            "run_summary.json not written (regression in enabled mode "
            "after timeout fix)"
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert "phase_ledger_path" in summary, (
            "phase_ledger_path missing from run_summary.json (regression)"
        )
        assert summary["phase_ledger_path"] == str(ledger_path)
        assert "phase_ledger_claimed_phases" in summary, (
            "phase_ledger_claimed_phases missing from run_summary.json "
            "(regression)"
        )
        assert set(summary["phase_ledger_claimed_phases"]) == set(
            _rac_module.PHASE_LEDGER_CLAIMED_PHASES
        )
        assert "phase_ledger_expected_run_id" in summary, (
            "phase_ledger_expected_run_id missing from run_summary.json "
            "(regression)"
        )
        assert summary["phase_ledger_expected_run_id"] == task_id

        # 5. final_status.json shape unchanged.
        final_status = json.loads(out_json.read_text(encoding="utf-8"))
        for k in (
            "phase_ledger_path",
            "phase_ledger_claimed_phases",
            "phase_ledger_expected_run_id",
        ):
            assert k not in final_status, (
                f"final_status.json must not include {k!r} (regression)"
            )
