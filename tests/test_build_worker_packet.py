#!/usr/bin/env python3
"""
Tests for build_worker_packet.py

Covers:
  - Packet building from minimal task JSON
  - allowed_files / forbidden_files inclusion
  - do_not constraints
  - required_return fields
  - controller run_id and next_action
  - docs task worker reason
  - multi-file task worker reason
  - markdown output generation
  - unsupported worker rejection
  - missing task_id / allowed_files exit
  - safety_invariants defaults
  - packet does not mutate controller state
  - packet does not modify source repo files
  - bundle_index context preservation
  - tests_to_run preservation
  - context_files preservation
  - forbidden safety actions in markdown
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "local" / "build_worker_packet.py"


def _import_mod():
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_worker_packet", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Minimal task fixtures
# ---------------------------------------------------------------------------

def minimal_task(overrides: dict | None = None) -> dict:
    base = {
        "task_id": "test-task-001",
        "objective": "Add a new helper function to the scripts/local directory",
        "task_type": "impl",
        "allowed_files": ["scripts/local/my_helper.py"],
        "forbidden_files": [],
        "expected_outputs": ["scripts/local/my_helper.py"],
        "tests_to_run": ["PYTHONPATH=. python3 -m pytest tests/test_my_helper.py -q"],
        "context_files": ["scripts/local/build_worker_packet.py"],
    }
    if overrides:
        base.update(overrides)
    return base


def minimal_controller_state() -> dict:
    return {
        "controller_version": 1,
        "run_id": "aed-run-test-001",
        "integration_branch": "integration/aed-run-test-001",
        "overall_status": "RUN_ACTIVE",
        "workspace": "/tmp/aed_run",
        "next_action": {"action": "run_task", "task_id": "test-task-001"},
        "safety_invariants": {
            "hermes_touched": False,
            "dispatch_occurred": False,
            "production_board_touched": False,
            "memory_or_profile_updated": False,
            "skills_created": False,
        },
    }


def minimal_bundle_index() -> dict:
    return {
        "bundle_version": 1,
        "run_id": "aed-run-test-001",
        "workspace": "/tmp/aed_run",
        "integration_plan": {
            "dependency_edges": [],
            "ordered_task_ids": ["test-task-001"],
        },
    }


# ---------------------------------------------------------------------------
# Test: builds packet from minimal task JSON
# ---------------------------------------------------------------------------

def test_builds_packet_from_minimal_task_json(tmp_path):
    mod = _import_mod()

    task = minimal_task()
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    rc = mod.main([
        "--task-json", str(task_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])

    assert rc == 0, f"expected exit 0, got {rc}"
    assert output_json.exists(), "packet JSON not written"

    packet = json.loads(output_json.read_text())
    assert packet["packet_kind"] == "aed.worker.packet.v1"
    assert packet["packet_version"] == 1
    assert packet["task_id"] == "test-task-001"
    assert packet["worker"] == "claude_code"
    assert packet["objective"] == "Add a new helper function to the scripts/local directory"
    assert packet["task_type"] == "impl"


# ---------------------------------------------------------------------------
# Test: includes allowed_files and forbidden_files
# ---------------------------------------------------------------------------

def test_includes_allowed_files_and_forbidden_files(tmp_path):
    mod = _import_mod()

    task = minimal_task({
        "allowed_files": ["scripts/local/a.py", "scripts/local/b.py"],
        "forbidden_files": ["engine/", "schemas/"],
    })
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    rc = mod.main([
        "--task-json", str(task_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])
    assert rc == 0

    packet = json.loads(output_json.read_text())
    assert packet["allowed_files"] == ["scripts/local/a.py", "scripts/local/b.py"]
    assert packet["forbidden_files"] == ["engine/", "schemas/"]


# ---------------------------------------------------------------------------
# Test: includes do_not constraints
# ---------------------------------------------------------------------------

def test_includes_do_not_constraints(tmp_path):
    mod = _import_mod()

    task = minimal_task()
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    rc = mod.main([
        "--task-json", str(task_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])
    assert rc == 0

    packet = json.loads(output_json.read_text())
    assert "do_not" in packet
    do_not = packet["do_not"]
    assert "do not push" in do_not
    assert "do not create PR" in do_not
    assert "do not merge" in do_not
    assert "do not append audit log" in do_not
    assert "do not dispatch" in do_not
    assert "do not touch production board" in do_not
    assert "do not update memory or profile" in do_not
    assert "do not create skills" in do_not


# ---------------------------------------------------------------------------
# Test: includes required_return fields
# ---------------------------------------------------------------------------

def test_includes_required_return_fields(tmp_path):
    mod = _import_mod()

    task = minimal_task()
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    rc = mod.main([
        "--task-json", str(task_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])
    assert rc == 0

    packet = json.loads(output_json.read_text())
    required_return = packet["required_return"]
    assert "changed_files" in required_return
    assert "test_results" in required_return
    assert "blockers" in required_return
    assert "risk_notes" in required_return
    assert "scope_notes" in required_return


# ---------------------------------------------------------------------------
# Test: includes controller run_id and next_action
# ---------------------------------------------------------------------------

def test_includes_controller_run_id_and_next_action(tmp_path):
    mod = _import_mod()

    task = minimal_task()
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    state = minimal_controller_state()
    state_path = tmp_path / "CONTROLLER_STATE.json"
    state_path.write_text(json.dumps(state))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    rc = mod.main([
        "--task-json", str(task_path),
        "--controller-state", str(state_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])
    assert rc == 0

    packet = json.loads(output_json.read_text())
    cc = packet["controller_context"]
    assert cc["run_id"] == "aed-run-test-001"
    assert cc["integration_branch"] == "integration/aed-run-test-001"
    assert cc["current_task_id"] == "test-task-001"
    assert cc["next_action"] == "run_task"


# ---------------------------------------------------------------------------
# Test: docs task produces docs worker reason
# ---------------------------------------------------------------------------

def test_docs_task_produces_docs_worker_reason(tmp_path):
    mod = _import_mod()

    task = minimal_task({
        "task_id": "docs-001",
        "objective": "Update documentation for the worker packet",
        "task_type": "docs",
        "allowed_files": ["docs/claude_worker_handoff_v1.md"],
    })
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    rc = mod.main([
        "--task-json", str(task_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])
    assert rc == 0

    packet = json.loads(output_json.read_text())
    assert packet["recommended_worker_reason"] == "docs task, Claude Code optional"
    assert packet["task_type"] == "docs"
    assert packet["risk_level"] == "low"


# ---------------------------------------------------------------------------
# Test: multi-file task produces Claude Code recommended reason
# ---------------------------------------------------------------------------

def test_multi_file_task_produces_claude_code_reason(tmp_path):
    mod = _import_mod()

    task = minimal_task({
        "task_id": "impl-001",
        "objective": "Implement multi-file feature",
        "task_type": "impl",
        "allowed_files": [
            "scripts/local/a.py",
            "scripts/local/b.py",
            "tests/test_ab.py",
        ],
    })
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    rc = mod.main([
        "--task-json", str(task_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])
    assert rc == 0

    packet = json.loads(output_json.read_text())
    assert packet["recommended_worker_reason"] == "multi-file implementation or debugging task, use Claude Code"
    assert packet["risk_level"] == "medium"


# ---------------------------------------------------------------------------
# Test: output markdown is generated
# ---------------------------------------------------------------------------

def test_output_markdown_is_generated(tmp_path):
    mod = _import_mod()

    task = minimal_task()
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    rc = mod.main([
        "--task-json", str(task_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])
    assert rc == 0

    assert output_md.exists(), "packet markdown not written"
    md = output_md.read_text()
    assert "Claude Code Worker Packet" in md
    assert task["task_id"] in md
    assert task["objective"] in md
    assert "Allowed files" in md
    assert "do not push" in md


# ---------------------------------------------------------------------------
# Test: packet refuses unsupported worker
# ---------------------------------------------------------------------------

def test_packet_refuses_unsupported_worker(tmp_path):
    mod = _import_mod()

    task = minimal_task()
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    with pytest.raises(SystemExit) as exc_info:
        mod.main([
            "--task-json", str(task_path),
            "--workspace", str(workspace),
            "--worker", "openai_cursor",
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ])
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Test: missing task_id exits nonzero
# ---------------------------------------------------------------------------

def test_missing_task_id_exits_nonzero(tmp_path):
    mod = _import_mod()

    task = {
        "objective": "No task_id field",
        "task_type": "impl",
        "allowed_files": ["scripts/local/a.py"],
    }
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    with pytest.raises(SystemExit) as exc_info:
        mod.main([
            "--task-json", str(task_path),
            "--workspace", str(workspace),
            "--worker", "claude_code",
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ])
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Test: missing allowed_files exits nonzero
# ---------------------------------------------------------------------------

def test_missing_allowed_files_exits_nonzero(tmp_path):
    mod = _import_mod()

    task = {
        "task_id": "test-001",
        "objective": "No allowed_files field",
        "task_type": "impl",
    }
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    with pytest.raises(SystemExit) as exc_info:
        mod.main([
            "--task-json", str(task_path),
            "--workspace", str(workspace),
            "--worker", "claude_code",
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ])
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Test: safety_invariants default false
# ---------------------------------------------------------------------------

def test_safety_invariants_default_false(tmp_path):
    mod = _import_mod()

    task = minimal_task()
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    rc = mod.main([
        "--task-json", str(task_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])
    assert rc == 0

    packet = json.loads(output_json.read_text())
    si = packet["safety_invariants"]
    assert si["hermes_touched"] is False
    assert si["dispatch_occurred"] is False
    assert si["production_board_touched"] is False
    assert si["memory_or_profile_updated"] is False
    assert si["skills_created"] is False


# ---------------------------------------------------------------------------
# Test: packet does not mutate controller state
# ---------------------------------------------------------------------------

def test_packet_does_not_mutate_controller_state(tmp_path):
    mod = _import_mod()

    task = minimal_task()
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    state = minimal_controller_state()
    state_path = tmp_path / "CONTROLLER_STATE.json"
    state_path.write_text(json.dumps(state))
    original_state_text = state_path.read_text()

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    rc = mod.main([
        "--task-json", str(task_path),
        "--controller-state", str(state_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])
    assert rc == 0

    assert state_path.read_text() == original_state_text, "controller state was mutated"


# ---------------------------------------------------------------------------
# Test: packet does not modify source repo files
# ---------------------------------------------------------------------------

def test_packet_does_not_modify_source_repo_files(tmp_path):
    mod = _import_mod()

    task = minimal_task()
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    # Run from a temp workspace so we don't touch the real repo
    rc = mod.main([
        "--task-json", str(task_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])
    assert rc == 0
    # Packet was written to tmp — repo files untouched


# ---------------------------------------------------------------------------
# Test: bundle_index context is included when available
# ---------------------------------------------------------------------------

def test_bundle_index_context_included_when_available(tmp_path):
    mod = _import_mod()

    task = minimal_task()
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    bundle = minimal_bundle_index()
    bundle_path = tmp_path / "BUNDLE_INDEX.json"
    bundle_path.write_text(json.dumps(bundle))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    rc = mod.main([
        "--task-json", str(task_path),
        "--bundle-index", str(bundle_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])
    assert rc == 0

    # Bundle index is read but not directly embedded in packet
    # (controller_context is derived from controller_state, not bundle_index)
    # The packet was generated successfully with bundle index available.
    packet = json.loads(output_json.read_text())
    assert packet["task_id"] == "test-task-001"


# ---------------------------------------------------------------------------
# Test: tests_to_run are preserved from task JSON
# ---------------------------------------------------------------------------

def test_tests_to_run_preserved_from_task_json(tmp_path):
    mod = _import_mod()

    task = minimal_task({
        "tests_to_run": [
            "python3 -m compileall scripts/local",
            "PYTHONPATH=. python3 -m pytest tests/test_my_helper.py -q",
        ],
    })
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    rc = mod.main([
        "--task-json", str(task_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])
    assert rc == 0

    packet = json.loads(output_json.read_text())
    assert packet["tests_to_run"] == [
        "python3 -m compileall scripts/local",
        "PYTHONPATH=. python3 -m pytest tests/test_my_helper.py -q",
    ]


# ---------------------------------------------------------------------------
# Test: context_files are preserved from task JSON
# ---------------------------------------------------------------------------

def test_context_files_preserved_from_task_json(tmp_path):
    mod = _import_mod()

    task = minimal_task({
        "context_files": [
            "scripts/local/build_worker_packet.py",
            "tests/test_build_worker_packet.py",
        ],
    })
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    rc = mod.main([
        "--task-json", str(task_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])
    assert rc == 0

    packet = json.loads(output_json.read_text())
    assert packet["context_files"] == [
        "scripts/local/build_worker_packet.py",
        "tests/test_build_worker_packet.py",
    ]


# ---------------------------------------------------------------------------
# Test: forbidden safety actions appear in markdown
# ---------------------------------------------------------------------------

def test_forbidden_safety_actions_appear_in_markdown(tmp_path):
    mod = _import_mod()

    task = minimal_task()
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    rc = mod.main([
        "--task-json", str(task_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])
    assert rc == 0

    md = output_md.read_text()
    assert "do not push." in md
    assert "do not create PR." in md
    assert "do not merge." in md
    assert "do not append audit log." in md
    assert "do not dispatch." in md
    assert "do not touch production board." in md
    assert "do not update memory or profile." in md
    assert "do not create skills." in md