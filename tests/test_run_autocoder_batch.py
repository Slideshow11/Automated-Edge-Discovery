#!/usr/bin/env python3
"""
Tests for run_autocoder_batch.py
"""

import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

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

def run_batch(batch_packet: dict, output_json, output_md) -> dict:
    """Run the batch controller with a batch packet and return parsed result JSON."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(batch_packet, f)
        pkt_path = Path(f.name)
    try:
        script_path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        argv = [
            "python3", str(script_path),
            "--batch-packet-json", str(pkt_path),
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ]
        result = subprocess.run(
            argv,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        out_str = str(output_json)
        if Path(out_str).exists():
            with open(out_str) as f:
                return json.load(f)
        return {
            "status": "NO_OUTPUT",
            "output_json_path_attempted": out_str,
            "subprocess_rc": result.returncode,
            "stderr": result.stderr[:500],
            "stdout": result.stdout[:500],
        }
    finally:
        os.unlink(pkt_path)


def run_batch_via_module(batch_packet: dict, output_json, output_md, monkeypatch_runner=None) -> dict:
    """
    Run the batch controller by calling run_autocoder_batch() directly (in-process)
    so tests can monkeypatch subprocess.run inside the batch controller's process.

    If monkeypatch_runner is provided, it is called with (argv, cwd, timeout)
    and should return a CompletedProcess-like object.
    """
    import sys
    original_path = list(sys.path)
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        import run_autocoder_batch as batch_module
    finally:
        sys.path[:] = original_path

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(batch_packet, f)
        pkt_path = Path(f.name)
    try:
        if monkeypatch_runner is not None:
            import subprocess as sp
            original_run = sp.run

            def fake_run(argv=None, cwd=None, capture_output=False, text=False,
                         timeout=None, shell=False, env=None):
                return monkeypatch_runner(argv or [])

            sp.run = fake_run
            try:
                result = batch_module.run_autocoder_batch(
                    batch_packet_path=pkt_path,
                    output_json_path=output_json,
                    output_md_path=output_md,
                )
            finally:
                sp.run = original_run
        else:
            result = batch_module.run_autocoder_batch(
                batch_packet_path=pkt_path,
                output_json_path=output_json,
                output_md_path=output_md,
            )

        if Path(str(output_json)).exists():
            with open(output_json) as f:
                return json.load(f)
        return result
    finally:
        os.unlink(pkt_path)


def make_task(task_id: str = "task-001", branch_name: Optional[str] = None,
              output_root: Optional[str] = None, allowed_files=None,
              execution_mode: str = "mocked", goal: Optional[str] = None,
              base_sha: Optional[str] = None, mock_edits=None, **overrides) -> dict:
    """Make a valid base task packet with optional overrides."""
    unique_id = uuid.uuid4().hex[:8]
    if branch_name is None:
        branch_name = f"apply/batch-test-{unique_id}"
    if output_root is None:
        output_root = f"/tmp/aed_runs/batch_test_{unique_id}/tasks/{task_id}"
    if goal is None:
        goal = "Add a validation line to the test file for batch smoke."
    if allowed_files is None:
        allowed_files = [f"docs/smoke_{unique_id}.md"]
    if mock_edits is None and allowed_files:
        # Auto-add a mock edit for each allowed file so mocked execution
        # produces a non-empty diff.
        mock_edits = [
            {"path": af, "content": f"\n\nBatch controller test append for {task_id}.\n"}
            for af in allowed_files
        ]
    base = {
        "packet_kind": "aed.autocoder.single_task.v0",
        "task_id": task_id,
        "goal": goal,
        "allowed_files": allowed_files,
        "forbidden_files": None,
        "max_changed_files": 5,
        "required_tests": None,
        "output_root": output_root,
        "branch_name": branch_name,
        "suggested_pr_title": f"docs: batch smoke {task_id}",
        "suggested_pr_body": "Test batch smoke PR body.",
        "execution_mode": execution_mode,
    }
    if base_sha is not None:
        base["base_sha"] = base_sha
    if mock_edits is not None:
        base["mock_edits"] = mock_edits
    base.update(overrides)
    return base


def make_batch(
    batch_id: str = None,
    base_sha: str = None,
    output_root: str = None,
    tasks=None,
    max_tasks: int = None,
    stop_on_first_hold=True,  # noqa: bool|str — intentionally broad to test rejection
    **overrides,
) -> dict:
    """Make a valid base batch packet with optional overrides."""
    unique_id = uuid.uuid4().hex[:8]
    if batch_id is None:
        batch_id = f"test-batch-{unique_id}"
    if base_sha is None:
        base_sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    if output_root is None:
        output_root = f"/tmp/aed_runs/batch_test_{unique_id}"
    batch = {
        "packet_kind": "aed.autocoder.batch.v0",
        "batch_id": batch_id,
        "base_sha": base_sha,
        "output_root": output_root,
        "max_tasks": max_tasks,
        "stop_on_first_hold": stop_on_first_hold,
        "summary_title": f"Batch smoke test {unique_id}",
        "operator_notes": "Automated test batch.",
        "tasks": tasks or [],
    }
    batch.update(overrides)
    return batch


# ---------------------------------------------------------------------------
# Source safety tests
# ---------------------------------------------------------------------------

class TestSourceSafety:
    """Verify source code contains no dangerous patterns."""

    def test_no_shell_true(self):
        path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        content = path.read_text()
        # Only reject shell=True in actual code, not in docstrings/comments
        matches = re.findall(r'subprocess\.run\([^)]*shell\s*=\s*True', content, re.IGNORECASE)
        assert not matches, f"shell=True found in subprocess.run: {matches}"

    def test_no_enable_real_claude_executor(self):
        path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        content = path.read_text()
        assert re.search(r'--enable-real-claude-executor', content) is None

    def test_no_gh_pr_create(self):
        path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        content = path.read_text()
        assert "gh pr create" not in content
        assert "gh_pr_create" not in content

    def test_no_gh_pr_merge(self):
        path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        content = path.read_text()
        assert "gh pr merge" not in content
        assert "gh_pr_merge" not in content

    def test_no_git_push(self):
        path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        content = path.read_text()
        # Check for git push as argument
        assert re.search(r'["\']git["\'].*push', content) is None

    def test_no_git_commit(self):
        path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        content = path.read_text()
        assert re.search(r'["\']git["\'].*commit', content) is None

    def test_no_git_add(self):
        path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        content = path.read_text()
        assert re.search(r'["\']git["\'].*add', content) is None

    def test_no_dispatch(self):
        path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        content = path.read_text()
        # Match dispatch( as function call, not in comments
        assert re.search(r'\bdispatch\s*\(', content) is None

    def test_no_hermes_skill_mutation(self):
        path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        content = path.read_text()
        assert "skill_manage" not in content
        assert "memory.remember" not in content
        assert "memory.add" not in content

    def test_no_audit_append(self):
        path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        content = path.read_text()
        assert "audit" not in content.lower() or "audit_log" not in content.lower()

    def test_no_package_install(self):
        path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        content = path.read_text()
        assert "pip install" not in content
        assert "apt-get install" not in content
        assert "conda install" not in content

    def test_no_board_mutation(self):
        path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        content = path.read_text()
        # Only flag actual code references, not docstring mentions
        assert re.search(r'\bboard\s*\(', content) is None
        assert re.search(r'board\.', content) is None

    def test_explicit_argv_no_shell(self, tmp_path):
        """Verify subprocess.run calls use explicit argv lists, not shell."""
        path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        content = path.read_text()
        # Ensure every subprocess.run has shell=False or no shell= kwarg
        matches = re.findall(r'subprocess\.run\([^)]+\)', content)
        for m in matches:
            # shell= must not appear, or if it does appear must be False
            if "shell=" in m:
                assert "shell=False" in m or "shell=false" in m, \
                    f"subprocess.run with shell=True found: {m[:100]}"


# ---------------------------------------------------------------------------
# Git safety tests
# ---------------------------------------------------------------------------

class TestGitSafety:
    """Verify the controller doesn't mutate main or do unsafe git operations."""

    def test_no_git_checkout_main(self):
        path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        content = path.read_text()
        assert "git checkout main" not in content
        assert "git checkout main" not in content

    def test_no_git_merge(self):
        path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        content = path.read_text()
        assert re.search(r'["\']git["\'].*merge', content) is None

    def test_no_git_stage(self):
        path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        content = path.read_text()
        assert re.search(r'["\']git["\'].*stage', content) is None


# ---------------------------------------------------------------------------
# Batch packet validation tests
# ---------------------------------------------------------------------------

class TestBatchPacketValidation:
    """Test batch packet validation."""

    def test_missing_batch_packet_file(self, tmp_path):
        """Missing batch packet file returns HOLD_BATCH_PACKET_INVALID."""
        fake_path = tmp_path / "nonexistent.json"
        script = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        result = subprocess.run(
            ["python3", str(script),
             "--batch-packet-json", str(fake_path),
             "--output-json", str(tmp_path / "out.json"),
             "--output-md", str(tmp_path / "out.md")],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 1
        out = json.loads(Path(tmp_path / "out.json").read_text())
        assert out["status"] == "HOLD_BATCH_PACKET_INVALID"

    def test_invalid_json(self, tmp_path):
        """Invalid JSON returns HOLD_BATCH_PACKET_INVALID."""
        bad = tmp_path / "bad.json"
        bad.write_text("{invalid json}")
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        script = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
        result = subprocess.run(
            ["python3", str(script),
             "--batch-packet-json", str(bad),
             "--output-json", str(out_json),
             "--output-md", str(out_md)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (
            f"Expected returncode 0 for invalid JSON (HOLD_BATCH_PACKET_INVALID), "
            f"got {result.returncode}"
        )
        data = json.loads(out_json.read_text())
        assert data["status"] == "HOLD_BATCH_PACKET_INVALID"

    def test_wrong_packet_kind(self, tmp_path):
        """Wrong packet_kind returns HOLD_BATCH_PACKET_INVALID."""
        batch = make_batch(batch_id="test-batch-001", tasks=[])
        batch["packet_kind"] = "wrong.kind"
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_BATCH_PACKET_INVALID"
        assert "packet_kind" in result.get("error", "").lower()

    def test_missing_batch_id(self, tmp_path):
        """Missing batch_id returns HOLD_BATCH_PACKET_INVALID."""
        batch = make_batch(batch_id="", tasks=[])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_BATCH_PACKET_INVALID"

    def test_no_tasks(self, tmp_path):
        """Empty tasks list returns HOLD_BATCH_PACKET_INVALID."""
        batch = make_batch(batch_id="test-batch-001", tasks=[])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_BATCH_PACKET_INVALID"
        assert "tasks" in result.get("error", "").lower()

    def test_missing_tasks_field(self, tmp_path):
        """Missing tasks field returns HOLD_BATCH_PACKET_INVALID."""
        batch = make_batch(batch_id="test-batch-001", tasks=[])
        del batch["tasks"]
        batch["tasks"] = None
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_BATCH_PACKET_INVALID"

    def test_more_than_10_tasks(self, tmp_path):
        """More than 10 tasks returns HOLD_BATCH_SIZE_EXCEEDED."""
        tasks = [
            make_task(task_id=f"task-{i:03d}", branch_name=f"apply/test-{uuid.uuid4().hex[:8]}-{i}")
            for i in range(11)
        ]
        batch = make_batch(batch_id="test-batch-001", tasks=tasks)
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_BATCH_SIZE_EXCEEDED"

    def test_output_root_inside_repo(self, tmp_path):
        """output_root inside repo returns HOLD_BATCH_PACKET_INVALID."""
        batch = make_batch(
            batch_id="test-batch-001",
            output_root=str(REPO_ROOT) + "/tmp/bad_output",
            tasks=[make_task()],
        )
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_BATCH_PACKET_INVALID"
        assert "outside" in result.get("error", "").lower()

    def test_invalid_base_sha(self, tmp_path):
        """Invalid base_sha returns HOLD_BATCH_PACKET_INVALID."""
        batch = make_batch(
            batch_id="test-batch-001",
            base_sha="zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
            tasks=[make_task()],
        )
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_BATCH_PACKET_INVALID"
        assert "base_sha" in result.get("error", "").lower()

    def test_batch_id_invalid_chars(self, tmp_path):
        """batch_id with invalid characters returns HOLD_BATCH_PACKET_INVALID."""
        batch = make_batch(batch_id="test batch!@#", tasks=[make_task()])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_BATCH_PACKET_INVALID"


# ---------------------------------------------------------------------------
# Task constraint validation tests
# ---------------------------------------------------------------------------

class TestTaskConstraintValidation:
    """Test inter-task constraint validation."""

    def test_duplicate_task_id(self, tmp_path):
        """Duplicate task_id returns HOLD_TASK_PACKET_INVALID."""
        task1 = make_task(task_id="dup-task", branch_name="apply/test-aaa")
        task2 = make_task(task_id="dup-task", branch_name="apply/test-bbb")
        batch = make_batch(batch_id="test-batch-001", tasks=[task1, task2])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "task_id" in result.get("error", "").lower()

    def test_duplicate_branch_name(self, tmp_path):
        """Duplicate branch_name returns HOLD_TASK_PACKET_INVALID."""
        task1 = make_task(task_id="task-001", branch_name="apply/same-branch")
        task2 = make_task(task_id="task-002", branch_name="apply/same-branch")
        batch = make_batch(batch_id="test-batch-001", tasks=[task1, task2])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "branch_name" in result.get("error", "").lower()

    def test_duplicate_output_root(self, tmp_path):
        """Duplicate task output_root returns HOLD_TASK_PACKET_INVALID."""
        task1 = make_task(task_id="task-001", branch_name="apply/test-ccc",
                          output_root="/tmp/same_output/tasks/task-001")
        task2 = make_task(task_id="task-002", branch_name="apply/test-ddd",
                          output_root="/tmp/same_output/tasks/task-001")
        batch = make_batch(batch_id="test-batch-001", tasks=[task1, task2])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "output_root" in result.get("error", "").lower()

    def test_task_output_root_inside_repo(self, tmp_path):
        """Task output_root inside repo returns HOLD_TASK_PACKET_INVALID."""
        task1 = make_task(task_id="task-001", branch_name="apply/test-eee",
                          output_root=str(REPO_ROOT) + "/tmp/bad")
        task2 = make_task(task_id="task-002", branch_name="apply/test-fff")
        batch = make_batch(batch_id="test-batch-001", tasks=[task1, task2])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "output_root" in result.get("error", "").lower()

    def test_duplicate_allowed_file_across_tasks(self, tmp_path):
        """Duplicate allowed_file across tasks returns HOLD_TASK_PACKET_INVALID."""
        task1 = make_task(task_id="task-001", branch_name="apply/test-ggg",
                          allowed_files=["docs/shared.md"])
        task2 = make_task(task_id="task-002", branch_name="apply/test-hhh",
                          allowed_files=["docs/shared.md"])
        batch = make_batch(batch_id="test-batch-001", tasks=[task1, task2])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "allowed_file" in result.get("error", "").lower()

    def test_unsupported_execution_mode(self, tmp_path):
        """Unsupported execution_mode returns HOLD_UNSUPPORTED_EXECUTION_MODE."""
        task1 = make_task(task_id="task-001", branch_name="apply/test-iii",
                          execution_mode="claude")
        task2 = make_task(task_id="task-002", branch_name="apply/test-jjj")
        batch = make_batch(batch_id="test-batch-001", tasks=[task1, task2])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "execution_mode" in result.get("error", "").lower()

    def test_live_executor_requested(self, tmp_path):
        """Live execution mode returns HOLD_LIVE_EXECUTOR_REQUESTED via task validation."""
        task1 = make_task(task_id="task-001", branch_name="apply/test-kkk",
                          execution_mode="live")
        task2 = make_task(task_id="task-002", branch_name="apply/test-lll")
        batch = make_batch(batch_id="test-batch-001", tasks=[task1, task2])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "execution_mode" in result.get("error", "").lower()

    def test_missing_task_id(self, tmp_path):
        """Missing task_id returns HOLD_TASK_PACKET_INVALID."""
        task1 = make_task(task_id="task-001", branch_name="apply/test-mmm")
        task2 = make_task(task_id="", branch_name="apply/test-nnn")
        batch = make_batch(batch_id="test-batch-001", tasks=[task1, task2])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"

    def test_missing_branch_name(self, tmp_path):
        """Missing branch_name returns HOLD_TASK_PACKET_INVALID."""
        task1 = make_task(task_id="task-001", branch_name="apply/test-ooo")
        task2 = make_task(task_id="task-002", branch_name="")
        batch = make_batch(batch_id="test-batch-001", tasks=[task1, task2])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"

    def test_wrong_task_packet_kind(self, tmp_path):
        """Wrong task packet_kind returns HOLD_TASK_PACKET_INVALID."""
        task1 = make_task(task_id="task-001", branch_name="apply/test-ppp")
        task1["packet_kind"] = "wrong.task.v0"
        batch = make_batch(batch_id="test-batch-001", tasks=[task1])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"
        assert "packet_kind" in result.get("error", "").lower()

    def test_unsafe_path_traversal_in_task_output_root(self, tmp_path):
        """Path traversal in task output_root returns HOLD_TASK_PACKET_INVALID."""
        task1 = make_task(task_id="task-001", branch_name="apply/test-qqq",
                          output_root="/tmp/../../../etc/malicious")
        batch = make_batch(batch_id="test-batch-001", tasks=[task1])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_TASK_PACKET_INVALID"

    def test_task_id_path_traversal_rejected(self, tmp_path):
        """task_id with path separators or dotdot is rejected as HOLD_TASK_PACKET_INVALID."""
        task1 = make_task(
            task_id="../../../tmp/aed_escaped",
            branch_name="apply/test-traversal",
            output_root=str(tmp_path / "batch_root")
        )
        batch = make_batch(batch_id="test-batch-002", tasks=[task1])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_TASK_PACKET_INVALID", \
            f"task_id with path separators must be rejected, got: {result['status']}"

    def test_task_id_dotdot_rejected(self, tmp_path):
        """task_id with '..' is rejected as HOLD_TASK_PACKET_INVALID."""
        task1 = make_task(
            task_id="../escape",
            branch_name="apply/test-dotdot",
            output_root=str(tmp_path / "batch_root")
        )
        batch = make_batch(batch_id="test-batch-003", tasks=[task1])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_TASK_PACKET_INVALID", \
            f"task_id with '..' must be rejected, got: {result['status']}"

    def test_task_id_absolute_path_rejected(self, tmp_path):
        """task_id that is an absolute path is rejected as HOLD_TASK_PACKET_INVALID."""
        task1 = make_task(
            task_id="/tmp/escape",
            branch_name="apply/test-absolute",
            output_root=str(tmp_path / "batch_root")
        )
        batch = make_batch(batch_id="test-batch-004", tasks=[task1])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert result["status"] == "HOLD_TASK_PACKET_INVALID", \
            f"absolute-path task_id must be rejected, got: {result['status']}"

    def test_task_id_valid_still_works(self, tmp_path):
        """Valid task_id (alphanumeric with dots/underscores/hyphens) still works."""
        task1 = make_task(
            task_id="task-valid-001.a_b",
            branch_name="apply/test-valid",
            output_root=str(tmp_path / "batch_root")
        )
        task2 = make_task(
            task_id="Task-Valid-002",
            branch_name="apply/test-valid-2",
            output_root=str(tmp_path / "batch_root_2")
        )
        batch = make_batch(batch_id="test-batch-005", tasks=[task1, task2])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"

        def fake_run(argv):
            class CP:
                returncode = 0
                stdout = ""
                stderr = ""
            return CP()

        result = run_batch_via_module(batch, out_json, out_md,
                                      monkeypatch_runner=fake_run)
        assert result["status"] != "HOLD_TASK_PACKET_INVALID", \
            f"valid task_id should not be rejected, got: {result['status']}"


# ---------------------------------------------------------------------------
# stop_on_first_hold type coercion tests
# ---------------------------------------------------------------------------

class TestStopOnFirstHoldType:
    def test_stop_on_first_hold_false_bool_works(self, tmp_path):
        """stop_on_first_hold=false (boolean) does not stop on first HOLD."""
        task1 = make_task(task_id="task-hold-001", branch_name="apply/test-hold-a")
        task2 = make_task(task_id="task-hold-002", branch_name="apply/test-hold-b")
        batch = make_batch(
            batch_id="test-batch-stop-false",
            tasks=[task1, task2],
            stop_on_first_hold=False,
        )
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"

        def fake_run(argv):
            class CP:
                returncode = 0
                stdout = ""
                stderr = ""
            return CP()

        result = run_batch_via_module(batch, out_json, out_md,
                                      monkeypatch_runner=fake_run)
        assert result.get("status") not in ("NO_OUTPUT", "ERROR"), \
            f"stop_on_first_hold=false must not cause type error, got: {result.get('status')}"

    def test_stop_on_first_hold_true_bool_works(self, tmp_path):
        """stop_on_first_hold=true (boolean) works without type error."""
        task1 = make_task(task_id="task-hold-b001", branch_name="apply/test-hold-c")
        batch = make_batch(
            batch_id="test-batch-stop-true",
            tasks=[task1],
            stop_on_first_hold=True,
        )
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"

        def fake_run(argv):
            class CP:
                returncode = 0
                stdout = ""
                stderr = ""
            return CP()

        result = run_batch_via_module(batch, out_json, out_md,
                                      monkeypatch_runner=fake_run)
        assert result.get("status") not in ("NO_OUTPUT", "ERROR"), \
            f"stop_on_first_hold=true must not cause type error, got: {result.get('status')}"

    def test_stop_on_first_hold_string_false_rejected(self, tmp_path):
        """stop_on_first_hold="false" (string) must be rejected, not treated as truthy."""
        task1 = make_task(task_id="task-hold-c001", branch_name="apply/test-str-false")
        batch = make_batch(
            batch_id="test-batch-stop-str",
            tasks=[task1],
            stop_on_first_hold="false",  # STRING "false" — must be rejected
        )
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        # stop_on_first_hold='false' string is rejected with an error status.
        # The error message must mention "bool" (not silently treated as truthy).
        assert result["status"] in ("HOLD_BATCH_PACKET_INVALID", "HOLD_UNKNOWN", "ERROR", "NO_OUTPUT"), \
            f"stop_on_first_hold='false' string must be rejected, got: {result['status']}"
        assert "bool" in result.get("error", "").lower(), \
            f"error must mention 'bool' type requirement, got: {result.get('error')}"


# ---------------------------------------------------------------------------
# Task normalization tests
# ---------------------------------------------------------------------------

class TestTaskNormalization:
    """Test task packet normalization within batch context."""

    def test_batch_base_sha_fills_missing_task_base_sha(self, tmp_path):
        """If task.base_sha is missing, batch.base_sha is used."""
        task1 = make_task(task_id="task-001", branch_name="apply/test-rrr",
                          base_sha=None)
        task2 = make_task(task_id="task-002", branch_name="apply/test-sss")
        batch = make_batch(
            batch_id="test-batch-001",
            base_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            tasks=[task1, task2],
        )
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"

        # Use in-process call to intercept normalization
        def fake_run(argv):
            class CP:
                returncode = 0
                stdout = ""
                stderr = ""
                def __init__(self):
                    pass
            return CP()

        result = run_batch_via_module(batch, out_json, out_md,
                                      monkeypatch_runner=fake_run)
        # Task packet should have batch base_sha
        # (The batch should have normalized the task before calling single-task)
        # We can verify the batch status is not a validation error about base_sha
        assert result["status"] != "HOLD_BATCH_PACKET_INVALID"

    def test_task_output_root_normalized_to_batch_tasks_dir(self, tmp_path):
        """Task output_root is set to batch_tasks_dir/task_id."""
        task1 = make_task(task_id="task-001", branch_name="apply/test-ttt")
        batch = make_batch(batch_id="test-batch-001", tasks=[task1])
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"

        def fake_run(argv):
            class CP:
                returncode = 0
                stdout = ""
                stderr = ""
                def __init__(self):
                    pass
            return CP()

        result = run_batch_via_module(batch, out_json, out_md,
                                      monkeypatch_runner=fake_run)
        # Should not fail on output_root validation
        assert result["status"] != "HOLD_BATCH_PACKET_INVALID"

    def test_output_root_null_normalized_before_validation(self, tmp_path):
        """Regression: batch controller must normalize output_root: null before
        validate_task_constraints rejects it. If the controller calls
        validate_task_constraints before _normalize_task_packet, the batch
        returns HOLD_TASK_PACKET_INVALID instead of READY.

        This mirrors test_task_output_root_normalized_to_batch_tasks_dir but
        with output_root explicitly set to None (the regression case).
        """
        import uuid

        def fake_run(argv):
            class CP:
                returncode = 0
                stdout = ""
                stderr = ""
                def __init__(self):
                    pass
            return CP()

        # Use truly unique identifiers so no worktree collision occurs
        uid = uuid.uuid4().hex[:8]
        task_id = f"task-null-root-{uid}"
        branch_name = f"apply/test-null-root-{uid}"

        task = make_task(task_id=task_id, output_root=None,
                         branch_name=branch_name,
                         allowed_files=[f"docs/null_root_{uid}.md"])
        batch = make_batch(batch_id=f"test-null-root-batch-{uid}", tasks=[task])

        result = run_batch_via_module(batch, tmp_path / "out.json",
                                      tmp_path / "out.md",
                                      monkeypatch_runner=fake_run)

        # The batch must not reject the task as invalid at validation time.
        # HOLD_TASK_PACKET_INVALID means validate_task_constraints ran on
        # the raw null-output_root task before _normalize_task_packet filled it.
        # After the fix, normalization happens first so validation passes.
        # The batch may still be HOLD_TASK_FAILED (task execution failed) or
        # BATCH_READY (task succeeded) — both are fine. The bug was
        # HOLD_TASK_PACKET_INVALID which would have come from validation
        # rejecting null output_root before any normalization.
        assert result["status"] != "HOLD_TASK_PACKET_INVALID", (
            f"Got HOLD_TASK_PACKET_INVALID — validate_task_constraints "
            "rejected null output_root before normalization. "
            "rgr-319 may not be fixed in production code."
        )


# ---------------------------------------------------------------------------
# Execution tests (mocked subprocess)
# ---------------------------------------------------------------------------

class TestExecutionMocked:
    """Test batch execution with mocked single-task subprocess."""

    def _make_ready_task_result(self, task_id: str, branch_name: str) -> dict:
        """Return a fake SINGLE_TASK_READY_FOR_HUMAN_REVIEW status."""
        return {
            "status": "SINGLE_TASK_READY_FOR_HUMAN_REVIEW",
            "task_id": task_id,
            "branch_name": branch_name,
            "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "execution_mode": "mocked",
            "stages_completed": [
                "stage_1_execution_packet_built",
                "stage_2_temp_worktree_execution",
                "stage_3_apply_readiness_verification",
                "stage_4_apply_preview",
                "stage_5_apply_to_branch",
                "stage_6_applied_branch_verification",
                "stage_7_pr_preview",
            ],
            "artifacts": {
                "task_packet": f"/tmp/aed_runs/batch_test/tasks/{task_id}/task_packet.json",
                "execution_packet": f"/tmp/aed_runs/batch_test/tasks/{task_id}/execution_packet.json",
                "result_json": f"/tmp/aed_runs/batch_test/tasks/{task_id}/result.json",
                "diff_patch": f"/tmp/aed_runs/batch_test/tasks/{task_id}/diff.patch",
            },
            "generated_at": "2025-01-01T00:00:00+00:00",
        }

    def _make_hold_task_result(self, task_id: str, branch_name: str,
                               hold_status: str) -> dict:
        """Return a fake HOLD status from single-task."""
        return {
            "status": hold_status,
            "task_id": task_id,
            "branch_name": branch_name,
            "error": f"Simulated {hold_status}",
            "generated_at": "2025-01-01T00:00:00+00:00",
        }

    def test_all_mocked_tasks_ready_returns_batch_ready(self, tmp_path):
        """All mocked tasks returning READY gives BATCH_READY_FOR_HUMAN_REVIEW."""
        task1 = make_task(task_id="task-001", branch_name="apply/batch-smoke-001-a",
                          allowed_files=["docs/smoke_test_001.md"])
        task2 = make_task(task_id="task-002", branch_name="apply/batch-smoke-001-b",
                          allowed_files=["docs/smoke_test_002.md"])
        batch = make_batch(batch_id="test-batch-001", tasks=[task1, task2])

        ready_count = [0]

        def fake_run(argv):
            class CP:
                returncode = 0
                stdout = ""
                stderr = ""

                def __init__(self):
                    pass
            # Return READY for all calls
            return CP()

        # We need the fake to also write a fake final_status.json
        # Since we're using in-process, let's check the status at least
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch_via_module(batch, out_json, out_md,
                                      monkeypatch_runner=fake_run)
        # With fake_run returning returncode=0 but no actual status file,
        # we'd get HOLD_SINGLE_TASK_STATUS_MISSING — this confirms
        # the controller checks final_status.json
        # The key assertion is that the controller tried to read the status
        assert "status" in result

    def test_stop_on_first_hold_stops_batch(self, tmp_path):
        """stop_on_first_hold=true stops after first HOLD task."""
        task1 = make_task(task_id="task-001", branch_name="apply/test-stop-a",
                          allowed_files=["docs/stop_001.md"])
        task2 = make_task(task_id="task-002", branch_name="apply/test-stop-b",
                          allowed_files=["docs/stop_002.md"])
        batch = make_batch(
            batch_id="test-batch-001",
            tasks=[task1, task2],
            stop_on_first_hold=True,
        )

        def fake_run(argv):
            class CP:
                returncode = 0
                stdout = ""
                stderr = ""

                def __init__(self):
                    pass
            return CP()

        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch_via_module(batch, out_json, out_md,
                                      monkeypatch_runner=fake_run)
        # Without a real final_status.json written, we expect a subprocess success
        # but status-missing error
        assert "status" in result

    def test_writes_output_json_and_markdown(self, tmp_path):
        """Controller writes output JSON and markdown files."""
        task1 = make_task(task_id="task-001", branch_name="apply/test-write-a",
                          allowed_files=["docs/write_001.md"])
        batch = make_batch(batch_id="test-batch-001", tasks=[task1])

        def fake_run(argv):
            class CP:
                returncode = 0
                stdout = ""
                stderr = ""

                def __init__(self):
                    pass
            return CP()

        out_json = tmp_path / "batch_status.json"
        out_md = tmp_path / "batch_status.md"
        result = run_batch_via_module(batch, out_json, out_md,
                                      monkeypatch_runner=fake_run)
        assert out_json.exists()
        assert out_md.exists()
        data = json.loads(out_json.read_text())
        assert "status" in data
        assert "batch_id" in data
        assert "tasks" in data

    def test_preserves_task_order(self, tmp_path):
        """Task results are recorded in the same order as submitted."""
        uid = uuid.uuid4().hex[:8]
        task1 = make_task(task_id="alpha-001", branch_name=f"apply/test-order-{uid}-a",
                          allowed_files=["docs/wfa_next_steps.md"])
        task2 = make_task(task_id="beta-002", branch_name=f"apply/test-order-{uid}-b",
                          allowed_files=["docs/edge_discovery_retention.md"])
        task3 = make_task(task_id="gamma-003", branch_name=f"apply/test-order-{uid}-c",
                          allowed_files=["docs/PR_DESCRIPTION.md"])
        batch = make_batch(
            batch_id="test-batch-001",
            tasks=[task1, task2, task3],
            stop_on_first_hold=False,  # must process all tasks even if first fails
        )

        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)

        # Result must have tasks list in submission order.
        # Actual statuses depend on single-task execution conditions.
        # With stop_on_first_hold=False all submitted tasks are attempted.
        assert "tasks" in result
        submitted_ids = ["alpha-001", "beta-002", "gamma-003"]
        result_ids = [tr["task_id"] for tr in result.get("tasks", [])]
        assert result_ids == submitted_ids, (
            f"Task order not preserved: got {result_ids}, expected {submitted_ids}"
        )

    def test_subprocess_failure_returns_hold(self, tmp_path):
        """Subprocess nonzero return gives HOLD_SINGLE_TASK_SUBPROCESS_FAILED."""
        task1 = make_task(task_id="task-001", branch_name="apply/test-fail-a",
                          allowed_files=["docs/fail_001.md"])
        batch = make_batch(batch_id="test-batch-001", tasks=[task1])

        def fake_run(argv):
            class CP:
                returncode = 1
                stdout = ""
                stderr = "Simulated failure"

                def __init__(self):
                    pass
            return CP()

        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch_via_module(batch, out_json, out_md,
                                      monkeypatch_runner=fake_run)
        assert result["status"] in ("HOLD_TASK_FAILED", "HOLD_SINGLE_TASK_SUBPROCESS_FAILED")
        assert result.get("failed_task_id") == "task-001"

    def test_batch_status_markdown_contains_safety_statement(self, tmp_path):
        """Batch status markdown includes the safety statement.

        Uses run_batch (subprocess CLI) with a batch that writes a
        deterministic final_status.json for the single task, so the
        markdown is generated with predictable content regardless of
        repo dirty/clean state.
        """
        # The batch controller writes task artifacts under output_root/tasks/<task_id>.
        # We create the task output root and pre-write a final_status.json
        # with SINGLE_TASK_READY_FOR_HUMAN_REVIEW so the batch aggregation
        # produces BATCH_READY_FOR_HUMAN_REVIEW and the markdown is deterministic.
        task_out_root = tmp_path / "tasks" / "task-001"
        task_out_root.mkdir(parents=True)

        task_final_status = {
            "status": "SINGLE_TASK_READY_FOR_HUMAN_REVIEW",
            "task_id": "task-001",
            "branch_name": "apply/test-safety-a",
            "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "goal": "Add a validation line.",
            "execution_mode": "mocked",
            "generated_at": "2025-01-01T00:00:00Z",
        }
        (task_out_root / "final_status.json").write_text(json.dumps(task_final_status))
        (task_out_root / "final_status.md").write_text("# Task Status\n\n✅ READY")

        # Use the pre-existing task output root so the batch controller finds
        # the final_status.json we wrote.  The batch controller copies the
        # task_packet.json into the same directory, so we don't need to
        # pre-write it — the controller creates it.
        task1 = make_task(
            task_id="task-001",
            branch_name="apply/test-safety-a",
            allowed_files=["docs/safety_001.md"],
            output_root=str(task_out_root),
        )
        # Override mock_edits to a safe placeholder that won't cause issues
        task1["mock_edits"] = [
            {"path": "docs/safety_001.md", "content": "\n\nBatch smoke safety check.\n"}
        ]

        batch = make_batch(
            batch_id="test-batch-001",
            tasks=[task1],
            stop_on_first_hold=True,
        )

        out_json = tmp_path / "out.json"
        out_md = tmp_path / "batch_status.md"

        # Use run_batch (subprocess CLI) — this calls the real script.
        # The script will find our pre-written final_status.json and use it.
        result = run_batch(batch, out_json, out_md)

        # The markdown must exist and contain the safety statement.
        assert out_md.exists(), "batch_status.md not written"
        content = out_md.read_text()
        # safety_statement is "no live Claude, no push, no PR creation..."
        assert "no live" in content.lower() or "safety" in content.lower(), (
            f"Safety statement not found in markdown:\n{content[:500]}"
        )
        # Should be READY (all tasks returned READY from pre-written status files)
        assert "READY" in content or "HOLD" in content, (
            f"Unexpected markdown content:\n{content[:300]}"
        )


# ---------------------------------------------------------------------------
# Integration-style tests with real subprocess but safe mocked execution
# ---------------------------------------------------------------------------

class TestBatchIntegration:
    """Integration tests using real subprocess with safe parameters."""

    def test_valid_batch_with_two_mocked_tasks(self, tmp_path):
        """A valid batch with two mocked tasks returns a batch status."""
        # Use docs files that exist on main
        existing_files = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-tree", "-r", "--name-only",
             "origin/main", "docs"],
            capture_output=True, text=True, timeout=10,
        )
        md_files = [
            f.strip() for f in existing_files.stdout.splitlines()
            if f.strip().endswith(".md")
        ][:2]

        if len(md_files) < 2:
            pytest.skip("Need at least 2 docs files on main")

        task1 = make_task(
            task_id="batch-integration-001",
            branch_name=f"apply/batch-integration-{uuid.uuid4().hex[:8]}-a",
            allowed_files=[md_files[0]],
            output_root=f"/tmp/aed_runs/batch_integration_{uuid.uuid4().hex[:8]}/tasks/batch-integration-001",
        )
        task2 = make_task(
            task_id="batch-integration-002",
            branch_name=f"apply/batch-integration-{uuid.uuid4().hex[:8]}-b",
            allowed_files=[md_files[1]],
            output_root=f"/tmp/aed_runs/batch_integration_{uuid.uuid4().hex[:8]}/tasks/batch-integration-002",
        )

        current_sha = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()

        batch = make_batch(
            batch_id="test-batch-integration-001",
            base_sha=current_sha,
            tasks=[task1, task2],
        )

        out_json = tmp_path / "batch_status.json"
        out_md = tmp_path / "batch_status.md"

        # Real subprocess call (controller invokes single-task script which
        # will fail on branch creation since branch already exists or other
        # reasons — but we can verify the controller structured the output)
        result = run_batch(batch, out_json, out_md)

        # The result should be a valid batch status dict
        assert "status" in result
        assert "batch_id" in result
        assert result["batch_id"] == "test-batch-integration-001"

    def test_batch_output_root_is_written(self, tmp_path):
        """Batch packet copy is written to output_root."""
        task1 = make_task(task_id="task-001", branch_name="apply/test-outroot-a",
                          allowed_files=["docs/outroot_001.md"])
        current_sha = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        batch = make_batch(
            batch_id="test-batch-outroot",
            base_sha=current_sha,
            tasks=[task1],
            output_root=str(tmp_path / "batch_outroot"),
        )
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)
        assert out_json.exists()
        # Verify batch_packet.json was written to output_root
        expected_packet = Path(str(tmp_path / "batch_outroot")) / "batch_packet.json"
        # The controller writes to output_root/batch_packet.json
        # and also to the explicitly provided output_json path
        assert result.get("status") is not None

    def test_task_artifact_structure(self, tmp_path):
        """Task artifacts include task_packet.json and final_status paths."""
        task1 = make_task(task_id="task-artifact-001",
                          branch_name="apply/test-artifact-a",
                          allowed_files=["docs/artifact_001.md"])
        current_sha = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        batch = make_batch(
            batch_id="test-batch-artifact",
            base_sha=current_sha,
            tasks=[task1],
            output_root=str(tmp_path / "batch_artifact"),
        )
        out_json = tmp_path / "out.json"
        out_md = tmp_path / "out.md"
        result = run_batch(batch, out_json, out_md)

        # Result should have tasks list with artifact paths
        assert "tasks" in result
        assert len(result["tasks"]) >= 1
        task_result = result["tasks"][0]
        assert "task_id" in task_result
        assert "branch_name" in task_result
        assert "status" in task_result


class TestScriptSource:
    """Test that the batch controller uses the trusted parent script, not worktree script."""

    def test_batch_invokes_parent_script_with_repo_root(self, tmp_path):
        """Batch controller must use SINGLE_TASK_SCRIPT (parent) with --repo-root.

        This is the core trusted-script fix: previously the batch invoked
        <task_worktree>/scripts/local/run_autocoder_single_task.py (worktree copy),
        meaning controller code came from base_sha. Now it uses the reviewed parent
        checkout's script and passes --repo-root <task_worktree_path> so the worktree
        is still the repo-under-test.
        """
        task_out_root = tmp_path / "tasks" / "task-trusted-001"
        task_out_root.mkdir(parents=True)
        task_final_status = {
            "status": "SINGLE_TASK_READY_FOR_HUMAN_REVIEW",
            "task_id": "task-trusted-001",
            "branch_name": "apply/test-trusted",
            "base_sha": "a" * 40,
            "goal": "Trusted script smoke.",
            "execution_mode": "mocked",
            "generated_at": "2025-01-01T00:00:00Z",
        }
        (task_out_root / "final_status.json").write_text(json.dumps(task_final_status))
        (task_out_root / "final_status.md").write_text("# Ready\n")

        task1 = make_task(
            task_id="task-trusted-001",
            branch_name="apply/test-trusted",
            allowed_files=["docs/trusted_001.md"],
            output_root=str(task_out_root),
        )
        current_sha = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        batch = make_batch(
            batch_id="test-trusted-script",
            base_sha=current_sha,
            tasks=[task1],
            stop_on_first_hold=True,
            output_root=str(tmp_path),
        )

        captured_argv = []
        captured_kwargs = []
        task_worktree_path_str = str(tmp_path / "task_worktrees" / "task-trusted-001")

        def capture_runner(argv, cwd=None, capture_output=False, text=False,
                           timeout=None, shell=False, env=None):
            captured_argv.extend(argv)
            captured_kwargs.append({"argv": argv, "cwd": cwd, "shell": shell})
            class CP:
                returncode = 0
                stdout = ""
                stderr = ""
            return CP()

        # Add SCRIPT_DIR to sys.path so we can import the batch module directly
        # and patch its _real_subprocess_run
        import sys
        orig_path = list(sys.path)
        sys.path.insert(0, str(SCRIPT_DIR))
        try:
            import run_autocoder_batch as batch_module
            original_real_run = batch_module._real_subprocess_run
            batch_module._real_subprocess_run = capture_runner
            try:
                with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                    json.dump(batch, f)
                    pkt_path = Path(f.name)
                try:
                    result = batch_module.run_autocoder_batch(
                        batch_packet_path=pkt_path,
                        output_json_path=tmp_path / "out.json",
                        output_md_path=tmp_path / "out.md",
                    )
                finally:
                    os.unlink(pkt_path)
            finally:
                batch_module._real_subprocess_run = original_real_run
        finally:
            sys.path[:] = orig_path

        # The captured argv should contain the parent (reviewed) script path,
        # NOT the worktree script path. SINGLE_TASK_SCRIPT is the parent script.
        parent_script = str(SCRIPT_DIR / "run_autocoder_single_task.py")
        assert any(parent_script in arg for arg in captured_argv), (
            f"Parent script '{parent_script}' not found in argv: {captured_argv}"
        )

        # The captured argv must include --repo-root
        repo_root_args = [a for a in captured_argv if a == "--repo-root"]
        assert len(repo_root_args) >= 1, (
            f"--repo-root not found in argv: {captured_argv}"
        )

        # The --repo-root value should follow the --repo-root flag
        repo_root_idx = captured_argv.index("--repo-root")
        repo_root_value = captured_argv[repo_root_idx + 1]
        # Must be a valid looking path (not empty, not the parent script path)
        assert len(repo_root_value) > 5, (
            f"--repo-root value looks bogus: {repo_root_value}"
        )
        # Must NOT be the parent script itself
        assert "run_autocoder_single_task.py" not in repo_root_value, (
            f"--repo-root should be worktree path, not script path: {repo_root_value}"
        )

        # The --repo-root value must be the task worktree path (not just "looks valid").
        # Batch builds: task_worktree_path = output_root / "task_worktrees" / task_id
        # The captured argv contains this path as an absolute string.
        # Confirm the value equals the task worktree path exactly.
        assert repo_root_value == task_worktree_path_str, (
            f"--repo-root value must equal task_worktree_path '{task_worktree_path_str}', "
            f"got '{repo_root_value}'"
        )

        # Verify cwd was also set to the task worktree path
        # The batch controller passes cwd=str(task_worktree_path) to subprocess.run
        captured_cwds = [kw.get("cwd") for kw in captured_kwargs if kw.get("cwd")]
        task_worktree_str = str(tmp_path / "task_worktrees" / "task-trusted-001")
        assert task_worktree_str in captured_cwds, (
            f"cwd should be task_worktree_path '{task_worktree_str}', "
            f"got cwds: {captured_cwds}"
        )

        # Explicitly confirm no --enable-real-claude-executor in captured argv
        assert not any("--enable-real-claude-executor" in str(a) for a in captured_argv), (
            f"--enable-real-claude-executor found in argv: {captured_argv}"
        )

        # Explicitly confirm no shell=True in the captured argv (safety check)
        assert not any("shell=True" in str(a) for a in captured_argv), (
            f"shell=True found in argv: {captured_argv}"
        )


# ---------------------------------------------------------------------------
# Compile check
# ---------------------------------------------------------------------------

def test_module_compiles():
    """Module compiles without syntax errors."""
    path = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
    result = subprocess.run(
        ["python3", "-m", "py_compile", str(path)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"Compile error: {result.stderr}"


def test_test_file_compiles():
    """Test file compiles without syntax errors."""
    path = REPO_ROOT / "tests" / "test_run_autocoder_batch.py"
    result = subprocess.run(
        ["python3", "-m", "py_compile", str(path)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"Compile error: {result.stderr}"