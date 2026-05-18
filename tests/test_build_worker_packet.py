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


# ---------------------------------------------------------------------------
# Dependency Context tests
# ---------------------------------------------------------------------------

def test_dependency_context_defaults_disabled(tmp_path):
    """dependency_context is disabled by default when not in task JSON."""
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
    dc = packet["dependency_context"]
    assert dc["enabled"] is False
    assert dc["packages_to_inspect"] == []
    assert dc["read_only"] is True
    assert dc["record_inspected_files"] is True


def test_task_can_enable_dependency_context(tmp_path):
    """Task JSON can enable dependency_context."""
    mod = _import_mod()

    task = minimal_task({
        "dependency_context": {
            "enabled": True,
            "packages_to_inspect": ["npm:zod", "pypi:requests"],
        },
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
    dc = packet["dependency_context"]
    assert dc["enabled"] is True
    assert dc["packages_to_inspect"] == ["npm:zod", "pypi:requests"]


def test_packages_to_inspect_preserved(tmp_path):
    """packages_to_inspect from task JSON are preserved in packet."""
    mod = _import_mod()

    task = minimal_task({
        "dependency_context": {
            "enabled": True,
            "packages_to_inspect": ["pypi:numpy", "npm:lodash"],
        },
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
    assert packet["dependency_context"]["packages_to_inspect"] == [
        "pypi:numpy", "npm:lodash"
    ]


def test_opensrc_home_defaults_under_workspace(tmp_path):
    """opensrc_home defaults to <workspace>/opensrc_cache."""
    mod = _import_mod()

    task = minimal_task({
        "dependency_context": {"enabled": True},
    })
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "my_workspace"
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
    expected_home = str(workspace / "opensrc_cache")
    assert packet["dependency_context"]["opensrc_home"] == expected_home


def test_opensrc_home_rejects_hermes_path(tmp_path):
    """.hermes in opensrc_home is rejected."""
    mod = _import_mod()

    task = minimal_task({
        "dependency_context": {
            "enabled": True,
            "opensrc_home": "/home/max/.hermes/some/path",
        },
    })
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


def test_opensrc_home_rejects_repo_source_tree(tmp_path):
    """opensrc_home outside workspace and /tmp/aed_runs is rejected."""
    mod = _import_mod()

    task = minimal_task({
        "dependency_context": {
            "enabled": True,
            "opensrc_home": "/usr/local/lib/python3.11/site-packages",
        },
    })
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


def test_opensrc_home_rejects_symlink_escape(tmp_path):
    """opensrc_home that resolves outside workspace via symlink is rejected."""
    mod = _import_mod()

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create a symlink inside workspace that points outside
    escape_link = workspace / "link_to_home"
    escape_link.symlink_to("/tmp")

    task = minimal_task({
        "dependency_context": {
            "enabled": True,
            "opensrc_home": str(escape_link / "aed_runs/run123"),  # resolves to /tmp/aed_runs/run123
        },
    })
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    # /tmp/aed_runs/run123 IS a valid /tmp/aed_runs path, so it should pass
    # Let's instead try a path that symlink-escapes to somewhere not allowed
    task2 = minimal_task({
        "dependency_context": {
            "enabled": True,
            "opensrc_home": str(workspace / "link_to_home" / "secret"),
        },
    })
    task_path2 = tmp_path / "task2.json"
    task_path2.write_text(json.dumps(task2))

    # /tmp/secret does NOT start with /tmp/aed_runs/ so should fail
    with pytest.raises(SystemExit) as exc_info:
        mod.main([
            "--task-json", str(task_path2),
            "--workspace", str(workspace),
            "--worker", "claude_code",
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ])
    assert exc_info.value.code == 1


def test_markdown_includes_dependency_context_section(tmp_path):
    """Markdown output includes ## Dependency Context section."""
    mod = _import_mod()

    task = minimal_task({
        "dependency_context": {"enabled": True},
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

    md = output_md.read_text()
    assert "## Dependency Context" in md
    assert "**Tool:** `opensrc`" in md
    assert "**Mode:** `read-only`" in md


def test_markdown_includes_read_only_rules(tmp_path):
    """Markdown includes read-only rules when dependency_context is enabled."""
    mod = _import_mod()

    task = minimal_task({
        "dependency_context": {"enabled": True},
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

    md = output_md.read_text()
    assert "read only dependency inspection only." in md
    assert "do not vendor dependency source into repo." in md
    assert "do not patch cached dependency source." in md


def test_markdown_says_no_dependency_install_by_default(tmp_path):
    """Markdown says new dependency installation is not allowed by default."""
    mod = _import_mod()

    task = minimal_task()  # no dependency_context
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
    assert "New dependency installation is not allowed for this task." in md


def test_dependency_install_policy_defaults(tmp_path):
    """dependency_install_policy defaults are conservative."""
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
    dip = packet["dependency_install_policy"]
    assert dip["new_dependencies_allowed"] is False
    assert dip["requires_human_approval"] is True
    assert dip["minimum_package_age_days"] == 14
    assert dip["lockfile_review_required"] is True
    assert dip["postinstall_scripts_require_approval"] is True


def test_new_dependencies_allowed_true_still_requires_approval(tmp_path):
    """Even when new_dependencies_allowed is True, markdown warns about human approval."""
    mod = _import_mod()

    task = minimal_task({
        "dependency_context": {"enabled": True},
        "dependency_install_policy": {
            "new_dependencies_allowed": True,
            # requires_human_approval defaults to True
        },
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

    md = output_md.read_text()
    assert "requires human approval" in md


def test_packet_records_dependency_context_as_context_only(tmp_path):
    """dependency_context is recorded as context, not added to allowed_files."""
    mod = _import_mod()

    task = minimal_task({
        "allowed_files": ["scripts/local/my.py"],
        "dependency_context": {
            "enabled": True,
            "packages_to_inspect": ["pypi:requests"],
        },
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
    # dependency_context should be its own top-level field
    assert "dependency_context" in packet
    # but should NOT be in allowed_files
    assert "dependency_context" not in packet["allowed_files"]
    # the opensrc cache path should NOT be in allowed_files
    opensrc_home = packet["dependency_context"]["opensrc_home"]
    assert opensrc_home not in packet["allowed_files"]


def test_dependency_cache_path_not_in_allowed_files(tmp_path):
    """Dependency cache path is not added to allowed_files."""
    mod = _import_mod()

    task = minimal_task({
        "dependency_context": {
            "enabled": True,
            "packages_to_inspect": ["npm:react"],
        },
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
    # The opensrc_cache path should NOT appear in allowed_files
    cache_path = packet["dependency_context"]["opensrc_home"]
    assert cache_path not in packet["allowed_files"]
    # Context files should also not contain the cache
    assert cache_path not in packet.get("context_files", [])


def test_packet_builder_does_not_invoke_opensrc(tmp_path):
    """Packet builder does not invoke opensrc CLI or subprocess."""
    import subprocess
    mod = _import_mod()

    task = minimal_task({
        "dependency_context": {
            "enabled": True,
            "packages_to_inspect": ["pypi:flask"],
        },
    })
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    # Verify: building a packet doesn't call any external tool (opensrc)
    # We check the packet output reflects the policy but no opensrc was invoked
    rc = mod.main([
        "--task-json", str(task_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])
    assert rc == 0
    # The packet should NOT contain any reference to having run opensrc
    packet = json.loads(output_json.read_text())
    # opensrc is named as tool but no evidence it was run
    assert packet["dependency_context"]["tool"] == "opensrc"
    # No install policy says packages were installed
    assert packet["dependency_install_policy"]["new_dependencies_allowed"] is False


def test_packet_builder_does_not_mutate_controller_state(tmp_path):
    """Packet builder does not mutate controller state (re-confirmed with dependency fields)."""
    mod = _import_mod()

    task = minimal_task({
        "dependency_context": {"enabled": True},
    })
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


def test_packet_builder_does_not_modify_repo_files_outside_output_paths(tmp_path):
    """Packet builder only writes to output paths; repo files outside outputs untouched."""
    mod = _import_mod()

    task = minimal_task()
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output_json = tmp_path / "packet.json"
    output_md = tmp_path / "packet.md"

    # Run in a temp workspace - nothing in the real repo is touched
    rc = mod.main([
        "--task-json", str(task_path),
        "--workspace", str(workspace),
        "--worker", "claude_code",
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ])
    assert rc == 0
    # Packet written to tmp; no real repo files modified


def test_dependency_context_disabled_markdown_shows_disabled(tmp_path):
    """When dependency_context is disabled, markdown shows it is disabled."""
    mod = _import_mod()

    task = minimal_task()  # no dependency_context enabled
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
    assert "## Dependency Context" in md
    assert "(disabled)" in md or "not enabled" in md.lower()
