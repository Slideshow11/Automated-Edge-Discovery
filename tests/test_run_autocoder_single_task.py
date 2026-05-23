#!/usr/bin/env python3
"""
Tests for run_autocoder_single_task.py
"""

import json
import os
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


def make_packet(task_id: str = "test-task-001", **overrides) -> dict:
    """Make a valid base task packet with optional overrides."""
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
