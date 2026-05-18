#!/usr/bin/env python3
"""
tests/test_autocoder_run_controller.py

Unit tests for the AED Autocoder Run Controller v0.
Uses temp dirs only. No source repo files are modified.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the module is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.local.autocoder_run_controller import (
    main as controller_main,
    _build_task_entry,
    _resolve_dependency_status,
    _update_dependency_chain,
    _compute_next_action,
    _utcnow,
    DEFAULT_MAX_LOCAL_REPAIR,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_workspace(tmp_path):
    """Provide a temporary workspace directory."""
    return tmp_path


@pytest.fixture
def sample_tasks_jsonl(temp_workspace):
    """Write a 3-task TASKS.jsonl."""
    tasks = [
        {"task_id": "task-001", "task_type": "docs_consistency", "integration_order": 1,
         "depends_on": [], "blocks": []},
        {"task_id": "task-002", "task_type": "docs_consistency", "integration_order": 2,
         "depends_on": ["task-001"], "blocks": []},
        {"task_id": "task-003", "task_type": "docs_consistency", "integration_order": 3,
         "depends_on": ["task-002"], "blocks": []},
    ]
    p = temp_workspace / "TASKS.jsonl"
    with open(p, "w") as f:
        for t in tasks:
            f.write(json.dumps(t) + "\n")
    return p


@pytest.fixture
def sample_bundle_index(temp_workspace):
    """Write a BUNDLE_INDEX.json with integration plan."""
    bi = {
        "bundle_index_version": 1,
        "run_id": "aed-test-run",
        "tasks": [
            {"task_id": "task-001", "task_type": "docs_consistency", "status": "planned"},
            {"task_id": "task-002", "task_type": "docs_consistency", "status": "planned"},
            {"task_id": "task-003", "task_type": "docs_consistency", "status": "planned"},
        ],
        "integration_plan": {
            "ordered_task_ids": ["task-001", "task-002", "task-003"],
            "dependency_edges": [
                {"from": "task-001", "to": "task-002", "type": "depends_on"},
                {"from": "task-002", "to": "task-003", "type": "depends_on"},
            ],
            "block_edges": [],
            "promotion_groups": {"task-001": "grp-1", "task-002": "grp-2", "task-003": "grp-3"},
            "pr_groups": {"autocoder": ["task-001", "task-002", "task-003"]},
            "parallel_groups": [["task-001"]],
            "promoted_to_integration": [],
            "ready_for_promotion": [],
            "blocked_from_promotion": [],
        },
    }
    p = temp_workspace / "BUNDLE_INDEX.json"
    with open(p, "w") as f:
        json.dump(bi, f)
    return p


def run_controller(cmd: list[str]) -> tuple[int, str, str]:
    """Run controller CLI, return (exit_code, stdout, stderr)."""
    proc = subprocess.Popen(
        [sys.executable, "scripts/local/autocoder_run_controller.py"] + cmd,
        cwd=Path(__file__).parent.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = proc.communicate()
    return proc.returncode, stdout, stderr


# ---------------------------------------------------------------------------
# Tests: init
# ---------------------------------------------------------------------------

def test_init_creates_valid_state_from_tasks_jsonl(temp_workspace, sample_tasks_jsonl):
    """Test 1: init creates valid state from TASKS.jsonl."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    rc, stdout, stderr = run_controller([
        "init",
        "--run-id", "aed-test-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "integration/aed-test-001",
        "--output-state", str(state_path),
    ])
    assert rc == 0, f"init failed: {stderr}"
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["controller_version"] == 1
    assert state["run_id"] == "aed-test-001"
    assert state["overall_status"] == "RUN_ACTIVE"
    assert len(state["tasks"]) == 3
    assert all(t["status"] == "TASK_PENDING" for t in state["tasks"])
    assert state["safety_invariants"]["hermes_touched"] is False


def test_init_uses_bundle_index_ordered_task_ids(temp_workspace, sample_tasks_jsonl, sample_bundle_index):
    """Test 2: init uses BUNDLE_INDEX ordered_task_ids when present."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    rc, stdout, stderr = run_controller([
        "init",
        "--run-id", "aed-test-002",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--bundle-index", str(sample_bundle_index),
        "--workspace", str(temp_workspace),
        "--integration-branch", "integration/aed-test-002",
        "--output-state", str(state_path),
    ])
    assert rc == 0, f"init failed: {stderr}"
    state = json.loads(state_path.read_text())
    # Order should follow BUNDLE_INDEX ordered_task_ids
    assert [t["task_id"] for t in state["tasks"]] == ["task-001", "task-002", "task-003"]
    # task-002 depends_on task-001 → dependency_status satisfied for 001, unsatisfied for 002
    assert state["tasks"][0]["dependency_status"] == "satisfied"
    assert state["tasks"][1]["dependency_status"] == "unsatisfied"


def test_init_missing_tasks_file_exits_nonzero(temp_workspace):
    """Test 16 (malformed/missing): init exits nonzero on missing TASKS.jsonl."""
    rc, _, stderr = run_controller([
        "init",
        "--run-id", "aed-test-bad",
        "--tasks-jsonl", "/nonexistent/TASKS.jsonl",
        "--workspace", str(temp_workspace),
        "--integration-branch", "integration/bad",
    ])
    assert rc != 0
    assert "not found" in stderr or "ERROR" in stderr


def test_init_malformed_tasks_jsonl_exits_nonzero(temp_workspace):
    """Test 16 (malformed/missing): init exits nonzero on malformed TASKS.jsonl."""
    bad = temp_workspace / "BAD_TASKS.jsonl"
    bad.write_text('{"task_id": "good"}\n{"task_id": "broken", INVALID}\n')
    rc, _, stderr = run_controller([
        "init",
        "--run-id", "aed-test-bad2",
        "--tasks-jsonl", str(bad),
        "--workspace", str(temp_workspace),
        "--integration-branch", "integration/bad2",
    ])
    assert rc != 0
    assert "invalid JSON" in stderr or "ERROR" in stderr


def test_init_fallback_to_tasks_order_without_bundle_index(temp_workspace, sample_tasks_jsonl):
    """Test 17: missing BUNDLE_INDEX falls back to TASKS.jsonl order."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    rc, stdout, stderr = run_controller([
        "init",
        "--run-id", "aed-test-fallback",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "integration/fallback",
        "--output-state", str(state_path),
    ])
    assert rc == 0
    state = json.loads(state_path.read_text())
    # Should not crash; BUNDLE_INDEX missing → use TASKS.jsonl order
    assert len(state["tasks"]) == 3


# ---------------------------------------------------------------------------
# Tests: next action
# ---------------------------------------------------------------------------

def test_next_returns_first_dependency_satisfied_pending_task(temp_workspace, sample_tasks_jsonl):
    """Test 3: next returns first dependency-satisfied pending task."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-next-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/next-001",
                    "--output-state", str(state_path)])

    rc, stdout, stderr = run_controller(["next", "--state", str(state_path)])
    assert rc == 0
    result = json.loads(stdout)
    assert result["action"] == "run_task"
    assert result["task_id"] == "task-001"  # task-001 has no deps


def test_task_with_unsatisfied_dependency_not_selected(temp_workspace, sample_tasks_jsonl):
    """Test 4: task with unsatisfied dependency is not selected."""
    # Manually set task-001 to TASK_BLOCKED so task-002 is still pending but unsatisfied
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-dep-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/dep-001",
                    "--output-state", str(state_path)])

    state = json.loads(Path(state_path).read_text())
    state["tasks"][0]["status"] = "TASK_BLOCKED"
    state["tasks"][0]["blocker_code"] = "manual_block"
    Path(state_path).write_text(json.dumps(state, indent=2) + "\n")

    rc, stdout, stderr = run_controller(["next", "--state", str(state_path)])
    assert rc == 0
    result = json.loads(stdout)
    # task-002 depends on task-001 (blocked), so should not be selected as run_task
    assert result["task_id"] != "task-002" or result["action"] != "run_task"


# ---------------------------------------------------------------------------
# Tests: record-task-result
# ---------------------------------------------------------------------------

def test_record_task_result_updates_state(temp_workspace, sample_tasks_jsonl):
    """Test 5: record-task-result TASK_READY updates state."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-rec-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/rec-001",
                    "--output-state", str(state_path)])

    rc, stdout, stderr = run_controller([
        "record-task-result",
        "--state", str(state_path),
        "--task-id", "task-001",
        "--status", "TASK_READY",
        "--promotion-status", "not_promoted",
        "--local-gate", "passed",
        "--scope-status", "clean",
    ])
    assert rc == 0, f"record-task-result failed: {stderr}"

    state = json.loads(Path(state_path).read_text())
    t1 = next(t for t in state["tasks"] if t["task_id"] == "task-001")
    assert t1["status"] == "TASK_READY"
    assert t1["local_gate_status"] == "passed"
    assert t1["scope_status"] == "clean"


def test_promoted_task_updates_promotion_status(temp_workspace, sample_tasks_jsonl):
    """Test 6: promoted task updates promotion_status."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-prom-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/prom-001",
                    "--output-state", str(state_path)])

    rc, stdout, stderr = run_controller([
        "record-task-result",
        "--state", str(state_path),
        "--task-id", "task-001",
        "--status", "TASK_READY",
        "--promotion-status", "promoted_to_integration",
    ])
    assert rc == 0

    state = json.loads(Path(state_path).read_text())
    t1 = next(t for t in state["tasks"] if t["task_id"] == "task-001")
    assert t1["promotion_status"] == "promoted_to_integration"
    # task-002 now has its dependency satisfied
    t2 = next(t for t in state["tasks"] if t["task_id"] == "task-002")
    assert t2["dependency_status"] == "satisfied"


def test_blocked_task_blocks_downstream_task(temp_workspace, sample_tasks_jsonl):
    """Test 7: blocked task blocks downstream task (dependency_status = blocked_by_dependency)."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-blk-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/blk-001",
                    "--output-state", str(state_path)])

    run_controller([
        "record-task-result",
        "--state", str(state_path),
        "--task-id", "task-001",
        "--status", "TASK_BLOCKED",
        "--promotion-status", "not_promoted",
        "--blocker-code", "scope_violation",
        "--blocker-summary", "Task 1 touched forbidden file",
    ])

    state = json.loads(Path(state_path).read_text())
    t2 = next(t for t in state["tasks"] if t["task_id"] == "task-002")
    assert t2["dependency_status"] == "blocked_by_dependency"


def test_all_tasks_ready_leads_to_next_action_generate_run_summary(temp_workspace, sample_tasks_jsonl):
    """Test 8: all tasks ready leads to next_action generate_run_summary."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-allrdy-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/allrdy-001",
                    "--output-state", str(state_path)])

    for task_id in ["task-001", "task-002", "task-003"]:
        rc, stdout, stderr = run_controller([
            "record-task-result",
            "--state", str(state_path),
            "--task-id", task_id,
            "--status", "TASK_READY",
            "--promotion-status", "promoted_to_integration",
        ])
        assert rc == 0, f"Failed to record {task_id}: {stderr}"

    state = json.loads(Path(state_path).read_text())
    assert state["overall_status"] == "RUN_READY_FOR_SUMMARY"
    assert state["next_action"]["action"] == "generate_run_summary"


def test_skipped_task_does_not_block_run_completion(temp_workspace, sample_tasks_jsonl):
    """Test 18: skipped task does not block run completion."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-skip-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/skip-001",
                    "--output-state", str(state_path)])

    # task-001 promoted, task-002 skipped, task-003 promoted
    run_controller(["record-task-result", "--state", str(state_path), "--task-id", "task-001",
                    "--status", "TASK_READY", "--promotion-status", "promoted_to_integration"])
    run_controller(["record-task-result", "--state", str(state_path), "--task-id", "task-002",
                    "--status", "TASK_SKIPPED", "--promotion-status", "not_promoted"])
    run_controller(["record-task-result", "--state", str(state_path), "--task-id", "task-003",
                    "--status", "TASK_READY", "--promotion-status", "promoted_to_integration"])

    state = json.loads(Path(state_path).read_text())
    # task-002 is skipped; task-001+003 promoted → should still be RUN_READY_FOR_SUMMARY
    assert state["overall_status"] == "RUN_READY_FOR_SUMMARY"
    assert state["next_action"]["action"] == "generate_run_summary"


def test_dependency_blocked_task_request_human(temp_workspace, sample_tasks_jsonl):
    """Test 19: dependency blocked task produces request_human."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-deph-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/deph-001",
                    "--output-state", str(state_path)])

    # Manually corrupt state: task-001 TASK_BLOCKED with no repair attempts left
    state = json.loads(Path(state_path).read_text())
    state["tasks"][0]["status"] = "TASK_BLOCKED"
    state["tasks"][0]["blocker_code"] = "scope_violation"
    state["tasks"][0]["repair_attempts"] = 3  # at limit
    state["tasks"][0]["max_repair_attempts"] = 3
    Path(state_path).write_text(json.dumps(state, indent=2) + "\n")

    rc, stdout, stderr = run_controller(["next", "--state", str(state_path)])
    assert rc == 0
    result = json.loads(stdout)
    assert result["action"] == "request_human"


# ---------------------------------------------------------------------------
# Tests: record-repair-result
# ---------------------------------------------------------------------------

def test_repair_attempt_increments_repair_count(temp_workspace, sample_tasks_jsonl):
    """Test 9: repair attempt increments repair count."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-rep-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/rep-001",
                    "--output-state", str(state_path)])

    run_controller([
        "record-repair-result",
        "--state", str(state_path),
        "--task-id", "task-001",
        "--repair-id", "task-001.R1",
        "--source", "local_gate",
        "--status", "repaired",
        "--summary", "Fixed markdown lint error",
    ])

    state = json.loads(Path(state_path).read_text())
    t1 = next(t for t in state["tasks"] if t["task_id"] == "task-001")
    assert t1["repair_attempts"] == 1
    assert len(t1["repair_history"]) == 1
    assert state["repair_events"][0]["status"] == "repaired"


def test_repair_limit_exceeded_blocks_task(temp_workspace, sample_tasks_jsonl):
    """Test 10: repair limit exceeded blocks task."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-replim-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/replim-001",
                    "--output-state", str(state_path)])

    # Manually set repair attempts to max
    state = json.loads(Path(state_path).read_text())
    state["tasks"][0]["repair_attempts"] = 3
    state["tasks"][0]["max_repair_attempts"] = 3
    Path(state_path).write_text(json.dumps(state, indent=2) + "\n")

    rc, stdout, stderr = run_controller([
        "record-repair-result",
        "--state", str(state_path),
        "--task-id", "task-001",
        "--repair-id", "task-001.R4",
        "--source", "local_gate",
        "--status", "failed",
        "--summary", "Could not fix scope violation",
    ])
    assert rc == 0

    state = json.loads(Path(state_path).read_text())
    t1 = next(t for t in state["tasks"] if t["task_id"] == "task-001")
    assert t1["status"] == "TASK_BLOCKED"
    assert t1["blocker_code"] == "repair_limit_exceeded"
    assert state["next_action"]["action"] == "request_human"


def test_repair_limit_exceeded_triggers_request_human(temp_workspace, sample_tasks_jsonl):
    """Test 10 companion: when repair limit exceeded, next action is request_human."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-replim-002", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/replim-002",
                    "--output-state", str(state_path)])

    state = json.loads(Path(state_path).read_text())
    state["tasks"][0]["repair_attempts"] = 3
    state["tasks"][0]["max_repair_attempts"] = 3
    Path(state_path).write_text(json.dumps(state, indent=2) + "\n")

    rc, stdout, stderr = run_controller([
        "record-repair-result",
        "--state", str(state_path),
        "--task-id", "task-001",
        "--repair-id", "task-001.R4",
        "--source", "local_gate",
        "--status", "failed",
    ])
    assert rc == 0

    state = json.loads(Path(state_path).read_text())
    assert state["next_action"]["action"] == "request_human"
    assert state["human_action_required"] is True


def test_repaired_task_resets_to_task_pending(temp_workspace, sample_tasks_jsonl):
    """Test: successful repair sets blocked task back to TASK_PENDING so it can be retried."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-repok-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/repok-001",
                    "--output-state", str(state_path)])

    state = json.loads(Path(state_path).read_text())
    state["tasks"][0]["status"] = "TASK_BLOCKED"
    state["tasks"][0]["repair_attempts"] = 1
    Path(state_path).write_text(json.dumps(state, indent=2) + "\n")

    rc, stdout, stderr = run_controller([
        "record-repair-result",
        "--state", str(state_path),
        "--task-id", "task-001",
        "--repair-id", "task-001.R2",
        "--source", "local_gate",
        "--status", "repaired",
        "--summary", "Fixed the issue",
    ])
    assert rc == 0
    state = json.loads(Path(state_path).read_text())
    t1 = next(t for t in state["tasks"] if t["task_id"] == "task-001")
    assert t1["status"] == "TASK_PENDING"


# ---------------------------------------------------------------------------
# Tests: safety invariants
# ---------------------------------------------------------------------------

def test_safety_hermes_touched_sets_run_failed_safety(temp_workspace, sample_tasks_jsonl):
    """Test 11: safety invariant hermes_touched true sets RUN_FAILED_SAFETY."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-safety-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/safety-001",
                    "--output-state", str(state_path)])

    state = json.loads(Path(state_path).read_text())
    state["safety_invariants"]["hermes_touched"] = True
    Path(state_path).write_text(json.dumps(state, indent=2) + "\n")

    rc, stdout, stderr = run_controller(["next", "--state", str(state_path)])
    assert rc == 0
    result = json.loads(stdout)
    assert result["action"] == "stop"
    assert "safety" in result["reason"].lower()


def test_safety_dispatch_occurred_sets_run_failed_safety(temp_workspace, sample_tasks_jsonl):
    """Test 12: safety invariant dispatch_occurred true sets RUN_FAILED_SAFETY."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-safety-002", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/safety-002",
                    "--output-state", str(state_path)])

    state = json.loads(Path(state_path).read_text())
    state["safety_invariants"]["dispatch_occurred"] = True
    Path(state_path).write_text(json.dumps(state, indent=2) + "\n")

    rc, stdout, stderr = run_controller(["next", "--state", str(state_path)])
    assert rc == 0
    result = json.loads(stdout)
    assert result["action"] == "stop"
    assert "safety" in result["reason"].lower()


def test_safety_production_board_touched_sets_run_failed_safety(temp_workspace, sample_tasks_jsonl):
    """Test 13: safety invariant production_board_touched true sets RUN_FAILED_SAFETY."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-safety-003", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/safety-003",
                    "--output-state", str(state_path)])

    state = json.loads(Path(state_path).read_text())
    state["safety_invariants"]["production_board_touched"] = True
    Path(state_path).write_text(json.dumps(state, indent=2) + "\n")

    rc, stdout, stderr = run_controller(["next", "--state", str(state_path)])
    assert rc == 0
    result = json.loads(stdout)
    assert result["action"] == "stop"
    assert "safety" in result["reason"].lower()


def test_safety_memory_profile_updated_does_not_fail_safety(temp_workspace, sample_tasks_jsonl):
    """Test: memory_or_profile_updated is report-only in v0, not a hard safety fail."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-safety-004", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/safety-004",
                    "--output-state", str(state_path)])

    state = json.loads(Path(state_path).read_text())
    state["safety_invariants"]["memory_or_profile_updated"] = True
    Path(state_path).write_text(json.dumps(state, indent=2) + "\n")

    rc, stdout, stderr = run_controller(["next", "--state", str(state_path)])
    assert rc == 0
    result = json.loads(stdout)
    # memory_or_profile_updated is NOT a hard-fail in v0 safety invariants
    assert result["action"] != "stop" or "memory" in result["reason"].lower()


def test_safety_skills_created_does_not_fail_safety(temp_workspace, sample_tasks_jsonl):
    """Test: skills_created is report-only in v0, not a hard safety fail."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-safety-005", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/safety-005",
                    "--output-state", str(state_path)])

    state = json.loads(Path(state_path).read_text())
    state["safety_invariants"]["skills_created"] = True
    Path(state_path).write_text(json.dumps(state, indent=2) + "\n")

    rc, stdout, stderr = run_controller(["next", "--state", str(state_path)])
    assert rc == 0
    result = json.loads(stdout)
    # skills_created is NOT a hard-fail in v0 safety invariants
    assert result["action"] != "stop" or "skills" in result["reason"].lower()


# ---------------------------------------------------------------------------
# Tests: status markdown
# ---------------------------------------------------------------------------

def test_status_markdown_includes_next_action_and_task_id(temp_workspace, sample_tasks_jsonl):
    """Test 14: status markdown includes next action and task ID."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    md_path = temp_workspace / "STATUS.md"
    run_controller(["init", "--run-id", "aed-md-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/md-001",
                    "--output-state", str(state_path)])

    rc, stdout, stderr = run_controller(["status", "--state", str(state_path), "--output-md", str(md_path)])
    assert rc == 0, f"status --output-md failed: {stderr}"
    assert md_path.exists()
    content = md_path.read_text()
    assert "Next Action" in content
    assert "run_task" in content
    assert "task-001" in content


# ---------------------------------------------------------------------------
# Tests: no repo files modified
# ---------------------------------------------------------------------------

def test_no_source_repo_files_modified(temp_workspace, sample_tasks_jsonl, tmp_path):
    """Test 15: no source repo files are modified."""
    # Capture state of repo files before
    repo_root = Path(__file__).parent.parent
    original_files = {}
    for pattern in ["scripts/local/autocoder_run_controller.py", "tests/test_autocoder_run_controller.py"]:
        p = repo_root / pattern
        if p.exists():
            original_files[pattern] = p.read_text()

    # Run controller
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    rc, stdout, stderr = run_controller([
        "init", "--run-id", "aed-nomod-001", "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace), "--integration-branch", "int/nomod-001",
        "--output-state", str(state_path),
    ])
    assert rc == 0

    # Verify no repo files were modified
    for pattern, original_content in original_files.items():
        p = repo_root / pattern
        assert p.read_text() == original_content, f"{pattern} was modified!"


# ---------------------------------------------------------------------------
# Tests: repair history preservation
# ---------------------------------------------------------------------------

def test_controller_state_update_preserves_existing_repair_history(temp_workspace, sample_tasks_jsonl):
    """Test 20: controller state update preserves existing repair history."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-hist-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/hist-001",
                    "--output-state", str(state_path)])

    # Record repair R1
    run_controller(["record-repair-result", "--state", str(state_path),
                     "--task-id", "task-001", "--repair-id", "task-001.R1",
                     "--source", "local_gate", "--status", "repaired",
                     "--summary", "Fix 1"])

    # Record another repair R2
    run_controller(["record-repair-result", "--state", str(state_path),
                     "--task-id", "task-001", "--repair-id", "task-001.R2",
                     "--source", "local_gate", "--status", "repaired",
                     "--summary", "Fix 2"])

    state = json.loads(Path(state_path).read_text())
    t1 = next(t for t in state["tasks"] if t["task_id"] == "task-001")
    assert len(t1["repair_history"]) == 2
    assert t1["repair_history"][0]["repair_id"] == "task-001.R1"
    assert t1["repair_history"][1]["repair_id"] == "task-001.R2"
    assert len(state["repair_events"]) == 2


# ---------------------------------------------------------------------------
# Tests: finalization
# ---------------------------------------------------------------------------

def test_finalize_run_sets_complete(temp_workspace, sample_tasks_jsonl):
    """Test: finalize-run sets overall_status to RUN_COMPLETE."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-fin-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/fin-001",
                    "--output-state", str(state_path)])

    rc, stdout, stderr = run_controller(["finalize-run", "--state", str(state_path)])
    assert rc == 0

    state = json.loads(Path(state_path).read_text())
    assert state["overall_status"] == "RUN_COMPLETE"
    assert state["next_action"]["action"] == "stop"


# ---------------------------------------------------------------------------
# Tests: record-pr-result
# ---------------------------------------------------------------------------

def test_record_pr_result_stores_pr_info(temp_workspace, sample_tasks_jsonl):
    """Test: record-pr-result stores PR info in state."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-pr-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/pr-001",
                    "--output-state", str(state_path)])

    rc, stdout, stderr = run_controller([
        "record-pr-result", "--state", str(state_path),
        "--pr-number", "244", "--status", "merged",
        "--url", "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/244",
        "--head-sha", "a79427badf9d206ae6ab596d1d62a588f8165400",
        "--merge-sha", "e0fe1335b8b58821db6a4a9da70ffb3e0caf83e1",
    ])
    assert rc == 0

    state = json.loads(Path(state_path).read_text())
    assert len(state["pr_results"]) == 1
    assert state["pr_results"][0]["pr_number"] == 244
    assert state["pr_results"][0]["status"] == "merged"


# ---------------------------------------------------------------------------
# Tests: next action — skipped/failed tasks
# ---------------------------------------------------------------------------

def test_next_skips_failed_validation_task_for_generate_summary(temp_workspace, sample_tasks_jsonl):
    """Test: TASK_FAILED_VALIDATION task does not block run from completing."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-failed-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/failed-001",
                    "--output-state", str(state_path)])

    run_controller(["record-task-result", "--state", str(state_path), "--task-id", "task-001",
                    "--status", "TASK_READY", "--promotion-status", "promoted_to_integration"])
    run_controller(["record-task-result", "--state", str(state_path), "--task-id", "task-002",
                    "--status", "TASK_FAILED_VALIDATION", "--promotion-status", "not_promoted"])
    run_controller(["record-task-result", "--state", str(state_path), "--task-id", "task-003",
                    "--status", "TASK_READY", "--promotion-status", "promoted_to_integration"])

    state = json.loads(Path(state_path).read_text())
    # task-002 failed; task-001 and task-003 promoted → complete
    assert state["next_action"]["action"] == "generate_run_summary"


def test_multiple_repair_events_recorded_in_history(temp_workspace, sample_tasks_jsonl):
    """Test: multiple repair events are all recorded in repair_events."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-mrep-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/mrep-001",
                    "--output-state", str(state_path)])

    for i in range(1, 4):
        run_controller(["record-repair-result", "--state", str(state_path),
                        "--task-id", "task-001", f"--repair-id", f"task-001.R{i}",
                        "--source", "local_gate", "--status",
                        "repaired" if i < 3 else "failed",
                        "--summary", f"Attempt {i}"])

    state = json.loads(Path(state_path).read_text())
    assert len(state["repair_events"]) == 3
    assert state["repair_events"][0]["repair_id"] == "task-001.R1"
    assert state["repair_events"][1]["repair_id"] == "task-001.R2"
    assert state["repair_events"][2]["repair_id"] == "task-001.R3"


# ---------------------------------------------------------------------------
# Tests: run-codex-review command
# ---------------------------------------------------------------------------

def test_run_codex_review_records_codex_review_in_progress(temp_workspace, sample_tasks_jsonl):
    """run-codex-review sets state to in_progress with action=run_codex_review."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-codex-001", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/codex-001",
                    "--output-state", str(state_path)])

    rc, stdout, stderr = run_controller([
        "run-codex-review", "--state", str(state_path),
        "--reason", "codex_artifact_required",
        "--summary", "Finalization guard requires Codex evidence"
    ])
    assert rc == 0, f"run-codex-review failed: {stderr}"

    state = json.loads(Path(state_path).read_text())
    assert state["codex_review"]["status"] == "in_progress"
    assert state["codex_review"]["reason"] == "codex_artifact_required"
    assert state["next_action"]["action"] == "run_codex_review"
    assert state["next_action"]["reason"] == "codex_artifact_required"
    assert state["human_action_required"] is True


def test_run_codex_review_does_not_set_run_to_merge_ready(temp_workspace, sample_tasks_jsonl):
    """run-codex-review records the step but does not mark overall_status as MERGE_READY."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-codex-002", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/codex-002",
                    "--output-state", str(state_path)])

    run_controller(["run-codex-review", "--state", str(state_path), "--reason", "codex_artifact_required"])

    state = json.loads(Path(state_path).read_text())
    # overall_status should not become MERGE_READY from a codex review step
    assert state.get("overall_status", "") not in ("MERGE_READY", "RUN_MERGE_READY")


def test_run_codex_review_with_custom_reason_preserved_in_state(temp_workspace, sample_tasks_jsonl):
    """Custom reason passed to run-codex-review is preserved in state and next_action."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-codex-003", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/codex-003",
                    "--output-state", str(state_path)])

    rc, stdout, stderr = run_controller([
        "run-codex-review", "--state", str(state_path),
        "--reason", "scope_expansion_required",
        "--summary", "Scope expansion needed for new integration files"
    ])
    assert rc == 0

    state = json.loads(Path(state_path).read_text())
    assert state["codex_review"]["reason"] == "scope_expansion_required"
    assert state["next_action"]["reason"] == "scope_expansion_required"


def test_run_codex_review_idempotent_updates_next_action(temp_workspace, sample_tasks_jsonl):
    """Calling run-codex-review twice updates the next_action each time, not duplicate."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-codex-004", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/codex-004",
                    "--output-state", str(state_path)])

    run_controller(["run-codex-review", "--state", str(state_path), "--reason", "codex_artifact_required"])
    run_controller(["run-codex-review", "--state", str(state_path), "--reason", "codex_artifact_required"])

    state = json.loads(Path(state_path).read_text())
    # Only one codex_review entry (no duplicate array, just overwrite)
    assert "codex_review" in state
    assert state["codex_review"]["status"] == "in_progress"
    assert state["next_action"]["action"] == "run_codex_review"


def test_run_codex_review_preserves_existing_tasks_and_state(temp_workspace, sample_tasks_jsonl):
    """run-codex-review does not modify task list or existing state fields."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller(["init", "--run-id", "aed-codex-005", "--tasks-jsonl", str(sample_tasks_jsonl),
                    "--workspace", str(temp_workspace), "--integration-branch", "int/codex-005",
                    "--output-state", str(state_path)])

    run_controller(["record-task-result", "--state", str(state_path), "--task-id", "task-001",
                    "--status", "TASK_READY", "--promotion-status", "promoted_to_integration"])

    run_controller(["run-codex-review", "--state", str(state_path), "--reason", "codex_artifact_required"])

    state = json.loads(Path(state_path).read_text())
    # Tasks are preserved
    assert len(state["tasks"]) == 3
    # task-001 promotion is preserved
    task_001 = next(t for t in state["tasks"] if t["task_id"] == "task-001")
    assert task_001["status"] == "TASK_READY"
    assert task_001["promotion_status"] == "promoted_to_integration"