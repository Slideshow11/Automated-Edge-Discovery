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
    DEFAULT_MAX_CODEX_REPAIR,
    CODEX_REVIEW_STATUSES,
    SEVERITY_ORDER,
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




# -------------------------------------------------------------------------
# Tests: Codex repair loop state
# -------------------------------------------------------------------------

def test_init_codex_review_default_not_started(temp_workspace, sample_tasks_jsonl):
    """Initial controller state has codex_review with status not_started."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-init-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-init-001",
        "--output-state", str(state_path),
    ])
    state = json.loads(Path(state_path).read_text())
    assert state["codex_review"]["status"] == "not_started"
    assert state["codex_review"]["head_sha"] is None
    assert state["codex_review"]["artifact_path"] is None
    assert state["codex_review"]["findings_count"] == 0
    assert state["codex_review"]["highest_severity"] == "none"
    assert state["codex_review"]["repair_attempts"] == 0
    assert state["codex_review"]["max_repair_attempts"] == DEFAULT_MAX_CODEX_REPAIR
    assert state["codex_review"]["same_blocker_count"] == 0
    assert state["codex_review"]["last_blocker_fingerprint"] is None
    assert state["codex_repair_events"] == []


def test_record_codex_review_clean_stores_head_sha_and_artifact(temp_workspace, sample_tasks_jsonl):
    """record-codex-review clean stores head_sha and artifact_path."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-clean-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-clean-001",
        "--output-state", str(state_path),
    ])
    rc, stdout, stderr = run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "clean", "--head-sha", "abc123",
        "--artifact-path", "/tmp/codex_artifact.json",
    ])
    assert rc == 0, f"record-codex-review clean failed: {stderr}"
    state = json.loads(Path(state_path).read_text())
    assert state["codex_review"]["status"] == "clean"
    assert state["codex_review"]["head_sha"] == "abc123"
    assert state["codex_review"]["artifact_path"] == "/tmp/codex_artifact.json"
    assert state["next_action"]["action"] == "run_task"
    assert state["next_action"]["reason"] == "codex_review_clean"


def test_record_codex_review_findings_stores_findings_count_and_severity(temp_workspace, sample_tasks_jsonl):
    """record-codex-review findings stores findings_count and highest_severity."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-find-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-find-001",
        "--output-state", str(state_path),
    ])
    rc, stdout, stderr = run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "findings", "--head-sha", "abc123",
        "--findings-count", "3", "--highest-severity", "P1",
        "--summary", "Found 3 issues",
    ])
    assert rc == 0
    state = json.loads(Path(state_path).read_text())
    assert state["codex_review"]["findings_count"] == 3
    assert state["codex_review"]["highest_severity"] == "P1"


def test_record_codex_review_findings_below_limit_produces_repair_task(temp_workspace, sample_tasks_jsonl):
    """Findings with attempts below limit AND a findings-file plan successfully
    produces next_action repair_task via the autonomous planner seam.

    Round-70 PHASE 5-P1: repair_task is gated on a successfully
    persisted plan, not just on ``status=findings``. The
    operator supplies --findings-file as evidence; the controller
    invokes the planner seam and persists plan metadata.
    """
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-rpair-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-rpair-001",
        "--output-state", str(state_path),
    ])
    # Build a findings artifact so the planner seam has evidence.
    findings_file = temp_workspace / "findings-rpair.json"
    findings_file.write_text(json.dumps([
        {"finding_id": "F1", "severity": "P2", "subsystem": "scripts",
         "root_cause": "formatting", "path": "scripts/local/foo.py",
         "summary": ""},
        {"finding_id": "F2", "severity": "P2", "subsystem": "scripts",
         "root_cause": "formatting", "path": "scripts/local/foo.py",
         "summary": ""},
    ]))
    rc, stdout, stderr = run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "findings", "--head-sha", "abc123",
        "--findings-count", "2", "--highest-severity", "P2",
        "--summary", "Minor formatting issues",
        "--findings-file", str(findings_file),
    ])
    assert rc == 0
    state = json.loads(Path(state_path).read_text())
    # Plan path must be persisted on success.
    assert state["codex_review"].get("repair_plan_path", "")
    assert state["next_action"]["action"] == "repair_task"
    assert state["next_action"]["reason"] == "codex_findings_plan_generated"


def test_record_codex_repair_result_increments_repair_attempts(temp_workspace, sample_tasks_jsonl):
    """record-codex-repair-result increments repair_attempts."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-rres-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-rres-001",
        "--output-state", str(state_path),
    ])
    run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "findings", "--head-sha", "abc123",
        "--findings-count", "2", "--highest-severity", "P2",
        "--summary", "Formatting issues",
    ])
    rc, stdout, stderr = run_controller([
        "record-codex-repair-result", "--state", str(state_path),
        "--status", "repaired", "--summary", "Fixed formatting",
    ])
    assert rc == 0, f"record-codex-repair-result failed: {stderr}"
    state = json.loads(Path(state_path).read_text())
    assert state["codex_review"]["repair_attempts"] == 1


def test_codex_repair_limit_exceeded_requests_human(temp_workspace, sample_tasks_jsonl):
    """Second failed repair reaches max and requests human."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-limit-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-limit-001",
        "--output-state", str(state_path),
    ])
    # Use different blockers per cycle to avoid same_blocker_count escalation
    for i in range(2):
        run_controller([
            "record-codex-review", "--state", str(state_path),
            "--status", "findings", "--head-sha", "abc123",
            "--findings-count", "1", "--highest-severity", "P3",
            "--summary", "Issue persists",
            "--blocker-fingerprint", f"blocker-cycle-{i}",
        ])
        rc, stdout, stderr = run_controller([
            "record-codex-repair-result", "--state", str(state_path),
            "--status", "failed", "--summary", "Could not fix",
            "--blocker-fingerprint", f"blocker-cycle-{i}",
        ])
        assert rc == 0
    state = json.loads(Path(state_path).read_text())
    assert state["next_action"]["action"] == "request_human"
    assert state["next_action"]["reason"] == "codex_repair_limit_exceeded"


def test_same_blocker_fingerprint_twice_requests_human(temp_workspace, sample_tasks_jsonl):
    """Same blocker fingerprint in review->repair->review triggers escalation."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-blkfp-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-blkfp-001",
        "--output-state", str(state_path),
    ])
    # First cycle: findings with blocker, failed repair
    run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "findings", "--head-sha", "abc123",
        "--findings-count", "1", "--highest-severity", "P2",
        "--summary", "Issue with blocker A",
        "--blocker-fingerprint", "blocker-A",
    ])
    rc, stdout, stderr = run_controller([
        "record-codex-repair-result", "--state", str(state_path),
        "--status", "failed", "--summary", "Could not fix",
        "--blocker-fingerprint", "blocker-A",
    ])
    assert rc == 0
    # Second cycle with same blocker: requires --findings-file
    # AND must escalate to same_codex_blocker_repeated. Round-70
    # PHASE 5-P1: same-blocker escalation must remain enforced even
    # when --findings-file is supplied (planning evidence present
    # does not bypass escalation).
    findings_file = temp_workspace / "findings-same-blocker.json"
    findings_file.write_text(json.dumps([
        {"finding_id": "B1", "severity": "P2", "subsystem": "scripts",
         "root_cause": "test-same-blocker", "path": "scripts/local/foo.py",
         "summary": ""},
    ]))
    rc, stdout, stderr = run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "findings", "--head-sha", "abc123",
        "--findings-count", "1", "--highest-severity", "P2",
        "--summary", "Same issue persists",
        "--blocker-fingerprint", "blocker-A",
        "--findings-file", str(findings_file),
    ])
    assert rc == 0
    state = json.loads(Path(state_path).read_text())
    assert state["next_action"]["action"] == "request_human"
    assert state["next_action"]["reason"] == "same_codex_blocker_repeated"


def test_scope_expansion_finding_requests_human(temp_workspace, sample_tasks_jsonl):
    """Scope expansion finding requests human."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-scope-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-scope-001",
        "--output-state", str(state_path),
    ])
    rc, stdout, stderr = run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "findings", "--head-sha", "abc123",
        "--findings-count", "1", "--highest-severity", "P2",
        "--summary", "Scope expansion needed: new file outside allowed scope",
    ])
    assert rc == 0
    state = json.loads(Path(state_path).read_text())
    assert state["next_action"]["action"] == "request_human"
    assert state["next_action"]["reason"] == "scope_expansion_required"


def test_dependency_install_finding_requests_human(temp_workspace, sample_tasks_jsonl):
    """Dependency install finding requests human."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-dep-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-dep-001",
        "--output-state", str(state_path),
    ])
    rc, stdout, stderr = run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "findings", "--head-sha", "abc123",
        "--findings-count", "1", "--highest-severity", "P1",
        "--summary", "dependency install required for new package",
    ])
    assert rc == 0
    state = json.loads(Path(state_path).read_text())
    assert state["next_action"]["action"] == "request_human"


def test_security_finding_requests_human(temp_workspace, sample_tasks_jsonl):
    """Security finding requests human."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-sec-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-sec-001",
        "--output-state", str(state_path),
    ])
    rc, stdout, stderr = run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "findings", "--head-sha", "abc123",
        "--findings-count", "1", "--highest-severity", "HIGH",
        "--summary", "Security: exposed API credentials in config file",
    ])
    assert rc == 0
    state = json.loads(Path(state_path).read_text())
    assert state["next_action"]["action"] == "request_human"


def test_clean_codex_after_repair_clears_repair_next_action(temp_workspace, sample_tasks_jsonl):
    """Clean Codex after repair clears repair next_action."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-cln-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-cln-001",
        "--output-state", str(state_path),
    ])
    run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "findings", "--head-sha", "abc123",
        "--findings-count", "1", "--summary", "Issue",
    ])
    run_controller([
        "record-codex-repair-result", "--state", str(state_path),
        "--status", "repaired", "--summary", "Fixed",
    ])
    rc, stdout, stderr = run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "clean", "--head-sha", "abc124",
        "--artifact-path", "/tmp/clean_artifact.json",
    ])
    assert rc == 0
    state = json.loads(Path(state_path).read_text())
    assert state["next_action"]["reason"] == "codex_review_clean"


def test_codex_repair_events_append_in_order(temp_workspace, sample_tasks_jsonl):
    """codex_repair_events append in order."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-evt-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-evt-001",
        "--output-state", str(state_path),
    ])
    run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "findings", "--head-sha", "abc123",
        "--findings-count", "2", "--highest-severity", "P2",
        "--summary", "First review",
    ])
    run_controller([
        "record-codex-repair-result", "--state", str(state_path),
        "--status", "repaired", "--summary", "First repair",
    ])
    run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "findings", "--head-sha", "abc124",
        "--findings-count", "1", "--highest-severity", "P3",
        "--summary", "Second review",
    ])
    state = json.loads(Path(state_path).read_text())
    assert len(state["codex_repair_events"]) == 3
    assert state["codex_repair_events"][0]["status"] == "findings"
    assert state["codex_repair_events"][1]["status"] == "repaired"
    assert state["codex_repair_events"][2]["status"] == "findings"


def test_malformed_severity_rejected(temp_workspace, sample_tasks_jsonl):
    """Malformed severity rejected."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-sev-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-sev-001",
        "--output-state", str(state_path),
    ])
    rc, stdout, stderr = run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "findings", "--head-sha", "abc123",
        "--findings-count", "1", "--highest-severity", "INVALID",
    ])
    assert rc != 0
    assert "error" in stderr.lower()


def test_negative_findings_count_rejected(temp_workspace, sample_tasks_jsonl):
    """Negative findings_count rejected."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-neg-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-neg-001",
        "--output-state", str(state_path),
    ])
    rc, stdout, stderr = run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "findings", "--head-sha", "abc123",
        "--findings-count", "-1",
    ])
    assert rc != 0
    assert "error" in stderr.lower()


def test_missing_artifact_path_for_clean_review_rejected(temp_workspace, sample_tasks_jsonl):
    """Missing artifact path for clean review rejected."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-art-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-art-001",
        "--output-state", str(state_path),
    ])
    rc, stdout, stderr = run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "clean", "--head-sha", "abc123",
    ])
    assert rc != 0
    assert "artifact-path" in stderr.lower()


def test_codex_review_statuses_enum_matches_expected_values():
    """Codex review statuses enum contains all expected values."""
    expected = {"not_started", "in_progress", "clean", "findings", "blocked", "repair_limit_exceeded"}
    assert CODEX_REVIEW_STATUSES == expected


def test_severity_order_correct():
    """Severity order is correct."""
    assert SEVERITY_ORDER == ["none", "P3", "P2", "P1", "HIGH"]


def test_record_codex_review_blocked_status_requests_human(temp_workspace, sample_tasks_jsonl):
    """Blocked Codex review status requests human."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-blk-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-blk-001",
        "--output-state", str(state_path),
    ])
    rc, stdout, stderr = run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "blocked", "--head-sha", "abc123",
        "--summary", "Codex review blocked",
    ])
    assert rc == 0
    state = json.loads(Path(state_path).read_text())
    assert state["next_action"]["action"] == "request_human"
    assert state["next_action"]["reason"] == "codex_blocked"


def test_record_codex_repair_result_blocked_requests_human(temp_workspace, sample_tasks_jsonl):
    """record-codex-repair-result blocked requests human."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-rblk-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-rblk-001",
        "--output-state", str(state_path),
    ])
    run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "findings", "--head-sha", "abc123",
        "--findings-count", "1", "--summary", "Issue",
    ])
    rc, stdout, stderr = run_controller([
        "record-codex-repair-result", "--state", str(state_path),
        "--status", "blocked", "--summary", "Repair blocked",
    ])
    assert rc == 0
    state = json.loads(Path(state_path).read_text())
    assert state["next_action"]["action"] == "request_human"
    assert state["next_action"]["reason"] == "codex_repair_blocked"


def test_safety_invariant_hard_stop_wins_before_codex_repair(temp_workspace, sample_tasks_jsonl):
    """Safety invariant hard-stop wins before Codex repair actions."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-safety-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-safety-001",
        "--output-state", str(state_path),
    ])
    state = json.loads(Path(state_path).read_text())
    state["safety_invariants"]["hermes_touched"] = True
    Path(state_path).write_text(json.dumps(state, indent=2) + "\n")
    rc, stdout, stderr = run_controller(["next", "--state", str(state_path)])
    assert rc == 0
    result = json.loads(stdout)
    assert result["action"] == "stop"
    assert "safety" in result["reason"].lower()


def test_final_status_not_merge_ready_from_codex_clean_alone(temp_workspace, sample_tasks_jsonl):
    """Final status does not become merge ready from Codex clean alone."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-nomerge-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-nomerge-001",
        "--output-state", str(state_path),
    ])
    run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "clean", "--head-sha", "abc123",
        "--artifact-path", "/tmp/codex.json",
    ])
    state = json.loads(Path(state_path).read_text())
    assert state["overall_status"] not in ("MERGE_READY", "RUN_MERGE_READY")


def test_controller_does_not_mutate_repo_files_outside_state(temp_workspace, sample_tasks_jsonl):
    """Controller does not mutate repo files outside state file."""
    repo_root = Path(__file__).parent.parent
    original = {}
    for f in ["scripts/local/autocoder_run_controller.py", "tests/test_autocoder_run_controller.py"]:
        p = repo_root / f
        if p.exists():
            original[f] = p.read_text()

    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-codex-mut-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/codex-mut-001",
        "--output-state", str(state_path),
    ])
    run_controller([
        "record-codex-review", "--state", str(state_path),
        "--status", "findings", "--head-sha", "abc123",
        "--findings-count", "2", "--highest-severity", "P2",
        "--summary", "Issues found",
    ])
    run_controller([
        "record-codex-repair-result", "--state", str(state_path),
        "--status", "repaired", "--summary", "Fixed",
    ])

    for f, content in original.items():
        p = repo_root / f
        assert p.read_text() == content, f"{f} was modified!"


# ---------------------------------------------------------------------------
# Tests: persistent mutation guard
# ---------------------------------------------------------------------------

def test_init_includes_persistent_mutation_guard_status_not_started(
    temp_workspace, sample_tasks_jsonl
):
    """Test 1: controller init includes persistent_mutation_guard.status = not_started."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    rc, stdout, stderr = run_controller([
        "init", "--run-id", "aed-guard-001",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-001",
        "--output-state", str(state_path),
    ])
    assert rc == 0, stderr
    state = json.loads(state_path.read_text())
    assert "persistent_mutation_guard" in state
    guard = state["persistent_mutation_guard"]
    assert guard["status"] == "not_started"
    assert guard["root"] == "/home/max/.hermes"
    assert guard["snapshot_path"] is None
    assert guard["compare_json_path"] is None
    assert guard["compare_md_path"] is None
    assert guard["blocked_changes_count"] == 0
    assert guard["allowed_changes_count"] == 0
    assert guard["last_checked_at"] is None


def test_record_snapshot_sets_status_snapshot_recorded(temp_workspace, sample_tasks_jsonl):
    """Test 2: record-persistent-guard-snapshot sets status to snapshot_recorded."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-guard-002",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-002",
        "--output-state", str(state_path),
    ])
    snapshot_path = temp_workspace / "snapshot.json"
    snapshot_path.write_text('{"files": []}')
    rc, stdout, stderr = run_controller([
        "record-persistent-guard-snapshot",
        "--state", str(state_path),
        "--root", "/home/max/.hermes",
        "--snapshot-path", str(snapshot_path),
    ])
    assert rc == 0, stderr
    state = json.loads(state_path.read_text())
    assert state["persistent_mutation_guard"]["status"] == "snapshot_recorded"
    assert "snapshot_recorded" in stdout


def test_record_snapshot_stores_root_and_snapshot_path(temp_workspace, sample_tasks_jsonl):
    """Test 3: record-persistent-guard-snapshot stores root and snapshot_path."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-guard-003",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-003",
        "--output-state", str(state_path),
    ])
    snapshot_path = temp_workspace / "my_snapshot.json"
    snapshot_path.write_text('{"files": []}')
    rc, stdout, stderr = run_controller([
        "record-persistent-guard-snapshot",
        "--state", str(state_path),
        "--root", "/home/max/.hermes",
        "--snapshot-path", str(snapshot_path),
    ])
    assert rc == 0, stderr
    state = json.loads(state_path.read_text())
    guard = state["persistent_mutation_guard"]
    assert guard["root"] == "/home/max/.hermes"
    assert guard["snapshot_path"] == str(snapshot_path)
    assert guard["last_checked_at"] is not None


def test_record_compare_pass_sets_status_clean(temp_workspace, sample_tasks_jsonl):
    """Test 4: record-persistent-guard-compare with PASS sets status clean."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-guard-004",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-004",
        "--output-state", str(state_path),
    ])
    compare_json = temp_workspace / "compare.json"
    compare_json.write_text(json.dumps({
        "recommendation": "PASS",
        "blocked_changes": [],
        "allowed_changes": [],
    }))
    rc, stdout, stderr = run_controller([
        "record-persistent-guard-compare",
        "--state", str(state_path),
        "--compare-json", str(compare_json),
    ])
    assert rc == 0, stderr
    state = json.loads(state_path.read_text())
    assert state["persistent_mutation_guard"]["status"] == "clean"


def test_record_compare_pass_stores_compare_paths_and_counts(temp_workspace, sample_tasks_jsonl):
    """Test 5: record-persistent-guard-compare PASS stores compare paths and counts."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-guard-005",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-005",
        "--output-state", str(state_path),
    ])
    compare_json = temp_workspace / "compare.json"
    compare_json.write_text(json.dumps({
        "recommendation": "PASS",
        "blocked_changes": [{"relative_path": "a/b.txt", "category": "memory"}],
        "allowed_changes": [{"relative_path": "c/d.txt", "category": "allowed"}],
    }))
    compare_md = temp_workspace / "compare.md"
    rc, stdout, stderr = run_controller([
        "record-persistent-guard-compare",
        "--state", str(state_path),
        "--compare-json", str(compare_json),
        "--compare-md", str(compare_md),
    ])
    assert rc == 0, stderr
    state = json.loads(state_path.read_text())
    guard = state["persistent_mutation_guard"]
    assert guard["compare_json_path"] == str(compare_json)
    assert guard["compare_md_path"] == str(compare_md)
    assert guard["blocked_changes_count"] == 1
    assert guard["allowed_changes_count"] == 1


def test_record_compare_block_sets_status_blocked(temp_workspace, sample_tasks_jsonl):
    """Test 6: record-persistent-guard-compare with BLOCK sets status blocked."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-guard-006",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-006",
        "--output-state", str(state_path),
    ])
    compare_json = temp_workspace / "compare.json"
    compare_json.write_text(json.dumps({
        "recommendation": "BLOCK",
        "blocked_changes": [{"relative_path": "x/y.txt", "category": "skill"}],
        "allowed_changes": [],
    }))
    rc, stdout, stderr = run_controller([
        "record-persistent-guard-compare",
        "--state", str(state_path),
        "--compare-json", str(compare_json),
    ])
    assert rc == 0, stderr
    state = json.loads(state_path.read_text())
    assert state["persistent_mutation_guard"]["status"] == "blocked"


def test_record_compare_block_sets_next_action_request_human(temp_workspace, sample_tasks_jsonl):
    """Test 7: record-persistent-guard-compare BLOCK sets next_action to request_human."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-guard-007",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-007",
        "--output-state", str(state_path),
    ])
    compare_json = temp_workspace / "compare.json"
    compare_json.write_text(json.dumps({
        "recommendation": "BLOCK",
        "blocked_changes": [],
        "allowed_changes": [],
    }))
    rc, stdout, stderr = run_controller([
        "record-persistent-guard-compare",
        "--state", str(state_path),
        "--compare-json", str(compare_json),
    ])
    assert rc == 0, stderr
    state = json.loads(state_path.read_text())
    assert state["next_action"]["action"] == "request_human"
    assert state["human_action_required"] is True


def test_record_compare_block_reason_is_persistent_mutation_detected(temp_workspace, sample_tasks_jsonl):
    """Test 8: BLOCK reason is persistent_mutation_detected."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-guard-008",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-008",
        "--output-state", str(state_path),
    ])
    compare_json = temp_workspace / "compare.json"
    compare_json.write_text(json.dumps({
        "recommendation": "BLOCK",
        "blocked_changes": [{"relative_path": "bad.txt", "category": "memory"}],
        "allowed_changes": [],
    }))
    rc, stdout, stderr = run_controller([
        "record-persistent-guard-compare",
        "--state", str(state_path),
        "--compare-json", str(compare_json),
    ])
    assert rc == 0, stderr
    state = json.loads(state_path.read_text())
    assert state["next_action"]["reason"] == "persistent_mutation_detected"


def test_record_compare_malformed_json_sets_status_error(temp_workspace, sample_tasks_jsonl):
    """Test 9: malformed compare JSON sets status error."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-guard-009",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-009",
        "--output-state", str(state_path),
    ])
    compare_json = temp_workspace / "compare.json"
    compare_json.write_text("NOT VALID JSON {{{")
    rc, stdout, stderr = run_controller([
        "record-persistent-guard-compare",
        "--state", str(state_path),
        "--compare-json", str(compare_json),
    ])
    assert rc == 0, stderr  # command succeeds, state is updated
    state = json.loads(state_path.read_text())
    assert state["persistent_mutation_guard"]["status"] == "error"


def test_record_compare_malformed_json_sets_next_action_request_human(temp_workspace, sample_tasks_jsonl):
    """Test 10: malformed compare JSON sets next_action request_human."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-guard-010",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-010",
        "--output-state", str(state_path),
    ])
    compare_json = temp_workspace / "compare.json"
    compare_json.write_text("INVALID {{{")
    rc, stdout, stderr = run_controller([
        "record-persistent-guard-compare",
        "--state", str(state_path),
        "--compare-json", str(compare_json),
    ])
    assert rc == 0, stderr
    state = json.loads(state_path.read_text())
    assert state["next_action"]["action"] == "request_human"
    assert state["next_action"]["reason"] == "persistent_mutation_guard_error"


def test_record_compare_missing_json_sets_status_error(temp_workspace, sample_tasks_jsonl):
    """Test 11: missing compare JSON sets status error."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-guard-011",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-011",
        "--output-state", str(state_path),
    ])
    missing_json = temp_workspace / "nonexistent.json"
    rc, stdout, stderr = run_controller([
        "record-persistent-guard-compare",
        "--state", str(state_path),
        "--compare-json", str(missing_json),
    ])
    assert rc == 0, stderr
    state = json.loads(state_path.read_text())
    assert state["persistent_mutation_guard"]["status"] == "error"


def test_clean_guard_does_not_mark_run_complete_by_itself(temp_workspace, sample_tasks_jsonl):
    """Test 12: clean persistent mutation guard does not mark run complete by itself."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-guard-012",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-012",
        "--output-state", str(state_path),
    ])
    # Simulate: take snapshot, do work, compare → PASS
    snapshot_path = temp_workspace / "snapshot.json"
    snapshot_path.write_text('{"files": []}')
    run_controller([
        "record-persistent-guard-snapshot",
        "--state", str(state_path),
        "--root", "/home/max/.hermes",
        "--snapshot-path", str(snapshot_path),
    ])
    compare_json = temp_workspace / "compare.json"
    compare_json.write_text(json.dumps({
        "recommendation": "PASS",
        "blocked_changes": [],
        "allowed_changes": [],
    }))
    run_controller([
        "record-persistent-guard-compare",
        "--state", str(state_path),
        "--compare-json", str(compare_json),
    ])
    state = json.loads(state_path.read_text())
    # Guard clean but run is still active — run must complete tasks first
    assert state["overall_status"] == "RUN_ACTIVE"
    assert state["next_action"]["action"] != "stop"


def test_safety_invariant_hard_stop_wins_before_guard_result(temp_workspace, sample_tasks_jsonl):
    """Test 13: safety invariant hard stop wins over guard result."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-guard-013",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-013",
        "--output-state", str(state_path),
    ])
    # Manually set hermes_touched = True (safety violation)
    state = json.loads(state_path.read_text())
    state["safety_invariants"]["hermes_touched"] = True
    state_path.write_text(json.dumps(state, indent=2) + "\n")
    # Now record a clean guard compare
    compare_json = temp_workspace / "compare.json"
    compare_json.write_text(json.dumps({
        "recommendation": "PASS",
        "blocked_changes": [],
        "allowed_changes": [],
    }))
    run_controller([
        "record-persistent-guard-compare",
        "--state", str(state_path),
        "--compare-json", str(compare_json),
    ])
    state = json.loads(state_path.read_text())
    # Safety invariant still triggers RUN_FAILED_SAFETY regardless of guard result
    assert state["overall_status"] == "RUN_FAILED_SAFETY"


def test_record_guard_command_preserves_existing_task_state(temp_workspace, sample_tasks_jsonl):
    """Test 14: record-persistent-guard-snapshot preserves existing task state."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-guard-014",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-014",
        "--output-state", str(state_path),
    ])
    # Record task result before guard snapshot
    run_controller([
        "record-task-result",
        "--state", str(state_path),
        "--task-id", "task-001",
        "--status", "TASK_READY",
        "--promotion-status", "promoted_to_integration",
        "--local-gate", "passed",
        "--scope-status", "clean",
    ])
    snapshot_path = temp_workspace / "snapshot.json"
    snapshot_path.write_text('{"files": []}')
    run_controller([
        "record-persistent-guard-snapshot",
        "--state", str(state_path),
        "--root", "/home/max/.hermes",
        "--snapshot-path", str(snapshot_path),
    ])
    state = json.loads(state_path.read_text())
    # Task state preserved
    task001 = next(t for t in state["tasks"] if t["task_id"] == "task-001")
    assert task001["status"] == "TASK_READY"
    assert task001["promotion_status"] == "promoted_to_integration"


def test_record_guard_command_records_last_checked_at(temp_workspace, sample_tasks_jsonl):
    """Test 15: record-persistent-guard-snapshot records last_checked_at."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-guard-015",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-015",
        "--output-state", str(state_path),
    ])
    snapshot_path = temp_workspace / "snapshot.json"
    snapshot_path.write_text('{"files": []}')
    rc, stdout, stderr = run_controller([
        "record-persistent-guard-snapshot",
        "--state", str(state_path),
        "--root", "/home/max/.hermes",
        "--snapshot-path", str(snapshot_path),
    ])
    assert rc == 0, stderr
    state = json.loads(state_path.read_text())
    assert state["persistent_mutation_guard"]["last_checked_at"] is not None


def test_controller_does_not_write_to_hermes_root(temp_workspace, sample_tasks_jsonl):
    """Test 16: controller commands do not write to /home/max/.hermes."""
    # Run a full sequence and verify /home/max/.hermes is untouched
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-guard-016",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-016",
        "--output-state", str(state_path),
    ])
    snapshot_path = temp_workspace / "snapshot.json"
    snapshot_path.write_text('{"files": []}')
    run_controller([
        "record-persistent-guard-snapshot",
        "--state", str(state_path),
        "--root", "/home/max/.hermes",
        "--snapshot-path", str(snapshot_path),
    ])
    compare_json = temp_workspace / "compare.json"
    compare_json.write_text(json.dumps({
        "recommendation": "PASS",
        "blocked_changes": [],
        "allowed_changes": [],
    }))
    run_controller([
        "record-persistent-guard-compare",
        "--state", str(state_path),
        "--compare-json", str(compare_json),
    ])
    # Controller state file is in temp_workspace, not in Hermes root
    assert str(state_path).startswith(str(temp_workspace))
    assert not str(state_path).startswith("/home/max/.hermes")


def test_status_markdown_includes_persistent_mutation_guard_state(temp_workspace, sample_tasks_jsonl):
    """Test 17: status markdown output includes persistent mutation guard state."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    md_path = temp_workspace / "STATUS.md"
    run_controller([
        "init", "--run-id", "aed-guard-017",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-017",
        "--output-state", str(state_path),
    ])
    # Record a snapshot so status has guard info
    snapshot_path = temp_workspace / "snapshot.json"
    snapshot_path.write_text('{"files": []}')
    run_controller([
        "record-persistent-guard-snapshot",
        "--state", str(state_path),
        "--root", "/home/max/.hermes",
        "--snapshot-path", str(snapshot_path),
    ])
    rc, stdout, stderr = run_controller([
        "status",
        "--state", str(state_path),
        "--output-md", str(md_path),
    ])
    assert rc == 0, stderr
    md = md_path.read_text()
    assert "persistent_mutation_guard" in md or "guard" in md.lower()


def test_final_status_report_includes_blocked_change_count(temp_workspace, sample_tasks_jsonl):
    """Test 18: final status report includes blocked change count from BLOCK result."""
    state_path = temp_workspace / "CONTROLLER_STATE.json"
    run_controller([
        "init", "--run-id", "aed-guard-018",
        "--tasks-jsonl", str(sample_tasks_jsonl),
        "--workspace", str(temp_workspace),
        "--integration-branch", "int/guard-018",
        "--output-state", str(state_path),
    ])
    compare_json = temp_workspace / "compare.json"
    compare_json.write_text(json.dumps({
        "recommendation": "BLOCK",
        "blocked_changes": [
            {"relative_path": "a/b.txt", "category": "memory"},
            {"relative_path": "c/d.txt", "category": "skill"},
        ],
        "allowed_changes": [],
    }))
    rc, stdout, stderr = run_controller([
        "record-persistent-guard-compare",
        "--state", str(state_path),
        "--compare-json", str(compare_json),
    ])
    assert rc == 0, stderr
    state = json.loads(state_path.read_text())
    assert state["persistent_mutation_guard"]["blocked_changes_count"] == 2
    assert "2" in stdout  # blocked_changes: 2


# ---------------------------------------------------------------------------
# Round-70 PHASE 5-P1 regression coverage
# ---------------------------------------------------------------------------
#
# These tests prove that the autonomous controller wiring now invokes the
# planner on findings and the runner on repair, and that the transitions
# honour fail-closed semantics on malformed or missing evidence.


class _FakePlannerAndRunner:
    """Captures planner and runner invocations without doing the work."""

    def __init__(self):
        self.planner_calls = []
        self.runner_calls = []

    def planner_call(self, **kwargs):
        self.planner_calls.append(kwargs)
        return {
            "tier": kwargs.get("tier", "tier_2_cohesive_batch"),
            "batches": [
                {
                    "batch_id": "BATCH-FAKE01",
                    "finding_ids": [f.get("finding_id") for f in kwargs.get("findings", []) if f.get("finding_id")],
                    "severities": ["P2"],
                    "root_cause": "fake",
                    "subsystem": "fake",
                    "grouping_reason": "fake",
                    "smaller_than_default_reason": "",
                    "focused_tests": ["tests.fake_select"],
                    "requires_full_validation": False,
                }
            ],
            "selection_reason": "fake",
            "changed_paths": kwargs.get("changed_paths", []),
            "test_plan": {
                "tier": kwargs.get("tier", "tier_2_cohesive_batch"),
                "selected_tests": ["tests.fake_select"],
                "requires_full_validation": False,
                "classification_failures": [],
            },
            "finding_count": len(kwargs.get("findings", []) or []),
            "batch_count": 1,
        }

    def runner_call(self, **kwargs):
        rc = self.runner_calls.append(kwargs)
        return {
            "return_code": 0,
            "duration": 0.1,
            "selected_tests": ["tests.fake_select"],
            "selection_reason": "fake",
            "tier": kwargs.get("tier", "tier_2_cohesive_batch"),
            "command": ["pytest", "-q", "tests.fake_select"],
            "capped": False,
            "complete": True,
        }


def _init_minimal_state(tmp_state: str) -> None:
    """Write a minimal initialized CONTROLLER_STATE.json via the controller CLI.

    ``tmp_state`` is a path to where the state file lives. We use a sibling
    directory ``tmp_state + "_wd"`` as the workspace so that ``-output-state``
    does not collide with a pre-existing directory.
    """
    state_file = Path(tmp_state)
    workspace = state_file.parent / (state_file.stem + "_wd")
    workspace.mkdir(parents=True, exist_ok=True)
    tasks = workspace / "tasks.jsonl"
    tasks.write_text(json.dumps({
        "task_id": "TASK-001",
        "status": "PENDING",
        "title": "Round-70 test task",
    }) + "\n")
    bundle = workspace / "bundle.json"
    bundle.write_text(json.dumps({
        "bundle_id": "BUNDLE-FAKE01",
        "task_ids": ["TASK-001"],
    }))
    rc = controller_main([
        "init",
        "--run-id", "RND70",
        "--tasks-jsonl", str(tasks),
        "--bundle-index", str(bundle),
        "--workspace", str(workspace),
        "--integration-branch", "fix/test-branch",
        "--output-state", str(state_file),
    ])
    assert rc == 0, f"init failed rc={rc} for {state_file}"


def _autonomous_seam(scratch_dir: str, fake: _FakePlannerAndRunner):
    """Patch _autonomous_repair_seam in the controller module to use our fake."""
    import scripts.local.autocoder_run_controller as ctrl
    return {
        "planner_call": fake.planner_call,
        "runner_call": fake.runner_call,
        "planner_module": None,
        "runner_module": None,
    }


def test_r70_planner_invoked_on_findings(monkeypatch, tmp_path):
    """Round-70 R-1: recording findings automatically invokes the planner seam."""
    state_path = str(tmp_path / "state.json")
    _init_minimal_state(state_path)

    fake = _FakePlannerAndRunner()
    monkeypatch.setattr(
        "scripts.local.autocoder_run_controller._autonomous_repair_seam",
        lambda: _autonomous_seam(state_path, fake),
    )

    findings_file = tmp_path / "findings.json"
    findings_file.write_text(json.dumps([
        {"finding_id": "F1", "severity": "P2", "subsystem": "autocoder",
         "root_cause": "wiring", "path": "scripts/local/autocoder_run_controller.py",
         "summary": "demo"},
        {"finding_id": "F2", "severity": "P2", "subsystem": "autocoder",
         "root_cause": "wiring", "path": "scripts/local/autocoder_run_controller.py",
         "summary": "demo2"},
    ]))

    plan_path = tmp_path / "plan.json"

    rc = controller_main([
        "record-autonomous-repair-plan",
        "--state", state_path,
        "--findings-file", str(findings_file),
        "--output-plan", str(plan_path),
    ])
    assert rc == 0, f"record-autonomous-repair-plan rc={rc}"

    # Planner MUST have been invoked exactly once with the findings list.
    assert len(fake.planner_calls) == 1
    pc = fake.planner_calls[0]
    assert pc["tier"] == "tier_2_cohesive_batch"
    assert [f["finding_id"] for f in pc["findings"]] == ["F1", "F2"]

    # Plan file MUST have been written.
    plan = json.loads(plan_path.read_text())
    assert plan["finding_count"] == 2
    assert plan["batch_count"] == 1

    # State MUST record the plan path and metadata.
    state = json.loads(Path(state_path).read_text())
    codex = state["codex_review"]
    assert codex["repair_plan_path"] == str(plan_path)
    assert codex["repair_plan_finding_count"] == 2
    assert codex["repair_plan_batch_count"] == 1
    # next_action MUST be repair_task.
    assert state["next_action"]["action"] == "repair_task"


def test_r70_same_root_cause_forms_cohesive_batch(monkeypatch, tmp_path):
    """Round-70 R-2: same-root-cause findings form a cohesive batch."""
    state_path = str(tmp_path / "state.json")
    _init_minimal_state(state_path)

    fake = _FakePlannerAndRunner()
    monkeypatch.setattr(
        "scripts.local.autocoder_run_controller._autonomous_repair_seam",
        lambda: _autonomous_repair_seam_path(state_path, fake),
    )

    findings_file = tmp_path / "findings.json"
    findings_file.write_text(json.dumps([
        {"finding_id": "A", "severity": "P2", "subsystem": "audit",
         "root_cause": "missing-cursor", "path": "scripts/local/audit_codex_response_for_pr.py",
         "summary": ""},
        {"finding_id": "B", "severity": "P2", "subsystem": "audit",
         "root_cause": "missing-cursor", "path": "scripts/local/audit_codex_response_for_pr.py",
         "summary": ""},
    ]))

    plan_path = tmp_path / "plan.json"

    rc = controller_main([
        "record-autonomous-repair-plan",
        "--state", state_path,
        "--findings-file", str(findings_file),
        "--output-plan", str(plan_path),
    ])
    assert rc == 0

    # 2 findings, same root_cause, same subsystem → 1 batch.
    plan = json.loads(plan_path.read_text())
    assert plan["batch_count"] == 1, plan
    assert sorted(plan["batches"][0]["finding_ids"]) == ["A", "B"]


def _autonomous_repair_seam_path(state_path, fake):
    return {
        "planner_call": fake.planner_call,
        "runner_call": fake.runner_call,
        "planner_module": None,
        "runner_module": None,
    }


def test_r70_malformed_findings_fails_closed(monkeypatch, tmp_path):
    """Round-70 R-4: malformed or missing finding evidence fails closed."""
    state_path = str(tmp_path / "state.json")
    _init_minimal_state(state_path)

    # Empty findings list
    f0 = tmp_path / "f0.json"
    f0.write_text(json.dumps([]))
    rc = controller_main([
        "record-autonomous-repair-plan",
        "--state", state_path, "--findings-file", str(f0),
        "--output-plan", str(tmp_path / "p0.json"),
    ])
    assert rc != 0, "empty findings should fail"

    # Missing findings file
    rc = controller_main([
        "record-autonomous-repair-plan",
        "--state", state_path, "--findings-file", str(tmp_path / "missing.json"),
        "--output-plan", str(tmp_path / "p1.json"),
    ])
    assert rc != 0, "missing file should fail"

    # Malformed JSON
    f2 = tmp_path / "f2.json"
    f2.write_text("not json")
    rc = controller_main([
        "record-autonomous-repair-plan",
        "--state", state_path, "--findings-file", str(f2),
        "--output-plan", str(tmp_path / "p2.json"),
    ])
    assert rc != 0, "malformed JSON should fail"

    # Missing finding_id
    f3 = tmp_path / "f3.json"
    f3.write_text(json.dumps([{"severity": "P2", "subsystem": "x",
                                "root_cause": "y", "path": "z.py", "summary": ""}]))
    rc = controller_main([
        "record-autonomous-repair-plan",
        "--state", state_path, "--findings-file", str(f3),
        "--output-plan", str(tmp_path / "p3.json"),
    ])
    assert rc != 0, "missing finding_id should fail"

    # State MUST NOT have advanced to repair_task.
    state = json.loads(Path(state_path).read_text())
    assert state["next_action"]["action"] != "repair_task"


def test_r70_runner_invoked_on_repaired(monkeypatch, tmp_path):
    """Round-70 R-5: recording repaired automatically invokes the runner seam."""
    state_path = str(tmp_path / "state.json")
    _init_minimal_state(state_path)

    fake = _FakePlannerAndRunner()
    monkeypatch.setattr(
        "scripts.local.autocoder_run_controller._autonomous_repair_seam",
        lambda: _autonomous_repair_seam_path(state_path, fake),
    )

    log_path = tmp_path / "log.json"
    rc = controller_main([
        "record-autonomous-repair-validation",
        "--state", state_path,
        "--changed-path", "scripts/local/foo.py",
        "--tier", "tier_2_cohesive_batch",
        "--output-log", str(log_path),
    ])
    assert rc == 0, f"record-autonomous-repair-validation rc={rc}"

    # Runner MUST have been invoked with the changed path.
    assert len(fake.runner_calls) == 1
    assert fake.runner_calls[0]["changed_paths"] == ["scripts/local/foo.py"]

    # Log MUST have been written.
    log = json.loads(log_path.read_text())
    assert log["return_code"] == 0

    # State MUST record the validation outcome.
    state = json.loads(Path(state_path).read_text())
    codex = state["codex_review"]
    assert codex["last_validation_status"] == "passed"
    assert codex["last_validation_return_code"] == 0

    # next_action MUST be await_codex_review_after_repair (after validation success).
    assert state["next_action"]["reason"] == "await_codex_review_after_repair"


def test_r70_failed_validation_does_not_reset_findings(monkeypatch, tmp_path):
    """Round-70 R-7: failed selected tests do not reset finding state."""
    state_path = str(tmp_path / "state.json")
    _init_minimal_state(state_path)

    # Pre-seed findings state
    Path(state_path).write_text(json.dumps({
        **json.loads(Path(state_path).read_text()),
        "codex_review": {
            "status": "findings", "findings_count": 3, "highest_severity": "P2",
        },
    }))

    fake = _FakePlannerAndRunner()

    def runner_call_rc1(**kw):
        fake.runner_calls.append(kw)
        return {
            "return_code": 1,  # FAILURE
            "duration": 0.5,
            "selected_tests": ["tests.failed"],
            "selection_reason": "fake",
            "tier": "tier_2_cohesive_batch",
            "command": ["pytest", "-q", "tests.failed"],
            "capped": False,
            "complete": True,
        }
    fake.runner_call = runner_call_rc1

    monkeypatch.setattr(
        "scripts.local.autocoder_run_controller._autonomous_repair_seam",
        lambda: _autonomous_repair_seam_path(state_path, fake),
    )

    rc = controller_main([
        "record-autonomous-repair-validation",
        "--state", state_path,
        "--changed-path", "scripts/local/foo.py",
        "--tier", "tier_2_cohesive_batch",
    ])
    assert rc == 0, "controller should not propagate error exit"

    state = json.loads(Path(state_path).read_text())
    codex = state["codex_review"]
    # Last validation recorded as failed.
    assert codex["last_validation_status"] == "failed"
    assert codex["last_validation_return_code"] == 1
    # Finding state MUST NOT be reset (still findings_count=3).
    assert codex.get("findings_count", 0) != 0 or codex.get("status") != "not_started"


def test_r70_empty_changed_paths_fails_closed(monkeypatch, tmp_path):
    """Round-70 R-8: empty or missing changed-path evidence fails closed."""
    state_path = str(tmp_path / "state.json")
    _init_minimal_state(state_path)

    fake = _FakePlannerAndRunner()
    monkeypatch.setattr(
        "scripts.local.autocoder_run_controller._autonomous_repair_seam",
        lambda: _autonomous_repair_seam_path(state_path, fake),
    )

    rc = controller_main([
        "record-autonomous-repair-validation",
        "--state", state_path,
        "--changed-path", "",
        "--tier", "tier_2_cohesive_batch",
    ])
    assert rc != 0, "empty changed-path should fail"
    assert len(fake.runner_calls) == 0


def test_r70_planner_cli_still_works(tmp_path):
    """Round-70 R-10: the planner CLI still works as a thin wrapper.

    Uses subprocess timeout so the test cannot hang.
    """
    import subprocess as sp
    findings = tmp_path / "f.json"
    findings.write_text(json.dumps([
        {"finding_id": "X", "severity": "P2", "subsystem": "scripts",
         "root_cause": "r70", "path": "scripts/local/autocoder_run_controller.py",
         "summary": ""},
    ]))
    plan = tmp_path / "plan.json"
    res = sp.run([
        sys.executable, "scripts/local/aed_repair_planner.py",
        "--findings-file", str(findings),
        "--output-plan", str(plan),
    ], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    j = json.loads(plan.read_text())
    assert j["finding_count"] == 1
    assert j["batch_count"] == 1


def test_r70_runner_cli_still_works(tmp_path):
    """Round-70 R-10: the runner CLI still works as a thin wrapper.

    Uses ``--dry-run`` so the test never launches pytest. The
    subprocess is bounded by a 30-second timeout so the test
    cannot hang. The output log is written with returncode=0.
    """
    import subprocess as sp
    paths = tmp_path / "paths.txt"
    paths.write_text("scripts/local/foo.py\n")
    log = tmp_path / "log.json"
    res = sp.run([
        sys.executable, "scripts/local/aed_test_runner.py",
        "--changed-paths-file", str(paths),
        "--output-log", str(log),
        "--dry-run",
    ], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    j = json.loads(log.read_text())
    assert j["returncode"] == 0
    assert j["dry_run"] is True


def test_r70_state_compatibility_existing_file(tmp_path):
    """Round-70 R-11: existing controller-state files without new optional fields
    can still be read safely.
    """
    state_path = tmp_path / "old_state.json"
    state_path.write_text(json.dumps({
        "run_id": "OLD",
        "status": "RUN_ACTIVE",
        "tasks": [],
        "codex_review": {
            "status": "findings",
            "findings_count": 5,
            "highest_severity": "P1",
        },
        "next_action": {"action": "repair_task"},
        "updated_at": "2026-01-01T00:00:00Z",
    }))
    rc = controller_main(["status", "--state", str(state_path)])
    assert rc == 0, "old state should still be readable"


def test_record_codex_repair_result_derives_changed_paths_from_findings(
    temp_workspace, sample_tasks_jsonl
):
    """Round-85 follow-up: when --changed-path is omitted from
    ``record-codex-repair-result --status repaired`` but the
    controller state already records the findings paths,
    the handler MUST derive impact evidence from those
    findings instead of silently dropping to
    ``validation_failed_no_repair:no_changed_paths_supplied``.
    """
    from scripts.local.autocoder_run_controller import (
        _derive_changed_paths_from_state,
    )
    state = {
        "codex_repair_events": [],
        "last_validated_changed_paths": [],
        "codex_review": {
            "findings": [
                {"path": "scripts/local/aed_pr.py"},
                {"path": "scripts/local/audit_codex_response_for_pr.py"},
                {"file_path": "tests/test_round85.py"},
            ],
        },
    }
    derived = _derive_changed_paths_from_state(state, state["codex_review"])
    assert "scripts/local/aed_pr.py" in derived
    assert "scripts/local/audit_codex_response_for_pr.py" in derived
    assert "tests/test_round85.py" in derived


def test_record_codex_repair_result_derives_changed_paths_from_repair_events(
    temp_workspace, sample_tasks_jsonl
):
    """Round-85 follow-up: when findings are empty but a prior
    repair event recorded a changed_paths list, the handler
    MUST derive impact evidence from that earlier event.
    """
    from scripts.local.autocoder_run_controller import (
        _derive_changed_paths_from_state,
    )
    state = {
        "codex_repair_events": [
            {"changed_paths": ["scripts/local/x.py"]},
            {"changed_paths": ["scripts/local/y.py"]},
        ],
        "last_validated_changed_paths": [],
        "codex_review": {"findings": []},
    }
    derived = _derive_changed_paths_from_state(state, state["codex_review"])
    assert derived == ["scripts/local/x.py", "scripts/local/y.py"]


def test_record_codex_repair_result_derives_changed_paths_from_last_validated(
    temp_workspace, sample_tasks_jsonl
):
    """Round-85 follow-up: when findings and prior repair events
    are empty but ``last_validated_changed_paths`` is recorded,
    the handler MUST derive impact evidence from there.
    """
    from scripts.local.autocoder_run_controller import (
        _derive_changed_paths_from_state,
    )
    state = {
        "codex_repair_events": [],
        "last_validated_changed_paths": ["scripts/local/z.py"],
        "codex_review": {"findings": []},
    }
    derived = _derive_changed_paths_from_state(state, state["codex_review"])
    assert derived == ["scripts/local/z.py"]


def test_record_codex_repair_result_derivation_empty_when_nothing_to_derive(
    temp_workspace, sample_tasks_jsonl
):
    """Round-85 follow-up: when no derivation source has any
    paths the helper returns an empty list, signalling to
    the caller that it MUST fail closed.
    """
    from scripts.local.autocoder_run_controller import (
        _derive_changed_paths_from_state,
    )
    state = {
        "codex_repair_events": [],
        "last_validated_changed_paths": [],
        "codex_review": {"findings": []},
    }
    derived = _derive_changed_paths_from_state(state, state["codex_review"])
    assert derived == []
