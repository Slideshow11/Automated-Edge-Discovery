#!/usr/bin/env python3
"""
Tests for build_autocoder_run_summary.py

Uses temp directories only. Does not read real bundle paths.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from typing import Any

# Add scripts/local to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "local"))

from build_autocoder_run_summary import (
    CURRENT_VERSION,
    TASK_STATUS_READY,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_SKIPPED,
    TASK_STATUS_FAILED_VALIDATION,
    TASK_STATUS_NOT_EVALUATED,
    PROMOTION_NOT_PROMOTED,
    OVERALL_RUN_READY,
    OVERALL_PARTIAL_READY,
    OVERALL_BLOCKED,
    OVERALL_NO_TASKS,
    OVERALL_FAILED_VALIDATION,
    OVERALL_INVALID_INPUT,
    HUMAN_NONE,
    HUMAN_RESOLVE,
    HUMAN_AUTHORIZE,
    HUMAN_REVIEW,
    HARD_FAIL_BOOLEANS,
    RunSummaryBuilder,
    load_bundle_index,
    build_markdown_report,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_dir():
    """Create a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


def minimal_bundle_index(tasks: list[dict]) -> dict:
    return {
        "version": 1,
        "bundle_root": "/tmp/bundles",
        "tasks": tasks,
    }


def make_bundle_dir(root: Path, task_id: str, files: dict[str, Any]) -> Path:
    """Create a bundle directory with given files.
    Keys ending in .json have their dict content written as JSON.
    Other values are written as plain strings.
    """
    d = root / task_id
    d.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        fpath = d / fname
        if fname.endswith(".json"):
            with open(fpath, "w") as f:
                json.dump(content, f)
        elif content is None:
            pass  # skip
        else:
            with open(fpath, "w") as f:
                if isinstance(content, str):
                    f.write(content)
                else:
                    f.write(str(content))
    return d


# ---------------------------------------------------------------------------
# Test 1: Empty bundle index produces NO_TASKS
# ---------------------------------------------------------------------------

def test_empty_bundle_index_no_tasks(temp_dir):
    index_path = temp_dir / "BUNDLE_INDEX.json"
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    with open(index_path, "w") as f:
        json.dump(minimal_bundle_index([]), f)

    builder = RunSummaryBuilder(
        run_id="test-run-001",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": []},
        bundle_root=bundle_root,
        repo=None,
        base_sha=None,
        integration_branch=None,
        expected_task_ids=None,
        allow_missing=False,
        strict=False,
    )
    summary = builder.build()
    assert summary["overall_status"] == OVERALL_NO_TASKS
    assert summary["task_count"] == 0


# ---------------------------------------------------------------------------
# Test 2: Valid three-task bundle produces summary JSON and markdown
# ---------------------------------------------------------------------------

def test_three_task_summary_json_and_md(temp_dir):
    """Test that a valid 3-task bundle produces both JSON and Markdown outputs."""
    index_path = temp_dir / "BUNDLE_INDEX.json"
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    tasks = [
        {
            "task_id": "docs-task-001",
            "task_type": "docs_consistency",
            "risk_level": "low",
            "allowed_files": ["docs/"],
            "forbidden_files": [],
            "expected_outputs": ["docs/output.md"],
        },
        {
            "task_id": "tests-task-002",
            "task_type": "test_gap_analysis",
            "risk_level": "medium",
            "allowed_files": ["tests/"],
            "forbidden_files": [],
            "expected_outputs": ["docs/report.md"],
        },
        {
            "task_id": "ci-task-003",
            "task_type": "ci_review",
            "risk_level": "low",
            "allowed_files": [".github/workflows/"],
            "forbidden_files": [],
            "expected_outputs": ["docs/ci_report.md"],
        },
    ]

    # Task 1: clean TASK_READY
    make_bundle_dir(bundle_root, "docs-task-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_ready",
            "clean_for_task": True,
            "scope_status": "clean",
            "changed_files": ["docs/example.md"],
        },
        "local_gate.txt": "PASS",
    })

    # Task 2: TASK_BLOCKED
    make_bundle_dir(bundle_root, "tests-task-002", {
        "BUNDLE_STATUS.json": {
            "status": "task_blocked",
            "clean_for_task": False,
            "scope_status": "dirty",
            "blocker_code": "allowed_scope_violations",
            "blocker_summary": "File(s) outside allowed scope were modified",
            "blocker_breakdown": {"allowed_scope_violations": 2},
        },
        "local_gate.txt": "FAIL",
        "violations_only.json": {
            "allowed_scope_violations": [
                {"file": "scripts/bad.py", "rule": "forbidden_file_access"},
                {"file": "src/evil.go", "rule": "forbidden_file_access"},
            ]
        },
    })

    # Task 3: TASK_SKIPPED
    make_bundle_dir(bundle_root, "ci-task-003", {
        "BUNDLE_STATUS.json": {
            "status": "task_skipped",
            "clean_for_task": True,
            "scope_status": "unknown",
        },
    })

    output_json = temp_dir / "RUN_SUMMARY.json"
    output_md = temp_dir / "RUN_SUMMARY.md"

    with open(index_path, "w") as f:
        json.dump(minimal_bundle_index(tasks), f)

    builder = RunSummaryBuilder(
        run_id="test-run-001",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": tasks},
        bundle_root=bundle_root,
        repo="/home/max/Automated-Edge-Discovery",
        base_sha="51eb88ac7c6602774e2e522120515a943d14409c",
        integration_branch="integration/test-run-001",
        expected_task_ids=None,
        allow_missing=False,
        strict=False,
    )
    summary = builder.build()

    # Write outputs
    with open(output_json, "w") as f:
        json.dump(summary, f, indent=2)
    build_markdown_report(summary, output_md)

    # Verify JSON
    assert output_json.exists()
    with open(output_json) as f:
        j = json.load(f)
    assert j["summary_version"] == CURRENT_VERSION
    assert j["task_count"] == 3
    assert j["tasks_ready"] == 1
    assert j["tasks_blocked"] == 1
    assert j["tasks_skipped"] == 1
    assert j["overall_status"] == OVERALL_PARTIAL_READY

    # Verify Markdown
    assert output_md.exists()
    md_content = output_md.read_text()
    assert "AED Autocoder Run Summary" in md_content
    assert "TASK_READY" in md_content
    assert "TASK_BLOCKED" in md_content
    assert "TASK_SKIPPED" in md_content


# ---------------------------------------------------------------------------
# Test 3: TASK_READY when clean_for_task true and no blockers
# ---------------------------------------------------------------------------

def test_task_ready_when_clean(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    make_bundle_dir(bundle_root, "clean-task-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_ready",
            "clean_for_task": True,
            "scope_status": "clean",
        },
        "local_gate.txt": "PASS",
        "scope_check.json": {"passed": True},
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "clean-task-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()
    task = summary["tasks"][0]
    assert task["status"] == TASK_STATUS_READY
    assert task["clean_for_task"] is True
    assert task["blocker_code"] is None


# ---------------------------------------------------------------------------
# Test 4: TASK_BLOCKED when clean_for_task false with allowed-scope violations
# ---------------------------------------------------------------------------

def test_task_blocked_with_scope_violations(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    make_bundle_dir(bundle_root, "blocked-task-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_blocked",
            "clean_for_task": False,
            "scope_status": "dirty",
            "blocker_code": "allowed_scope_violations",
            "blocker_summary": "Modified files outside allowed scope",
            "blocker_breakdown": {"allowed_scope_violations": 1},
        },
        "violations_only.json": {
            "allowed_scope_violations": [
                {"file": "scripts/forbidden.py", "rule": "forbidden_file_access"}
            ]
        },
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "blocked-task-001", "task_type": "test_gap_analysis",
             "risk_level": "medium", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()
    task = summary["tasks"][0]
    assert task["status"] == TASK_STATUS_BLOCKED
    assert task["clean_for_task"] is False
    assert task["blocker_code"] == "allowed_scope_violations"
    assert task["allowed_scope_violations_count"] == 1


# ---------------------------------------------------------------------------
# Test 5: TASK_FAILED_VALIDATION when malformed JSON appears
# ---------------------------------------------------------------------------

def test_task_failed_validation_malformed_json(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    # Create malformed JSON in scope_check
    bd = bundle_root / "bad-task-001"
    bd.mkdir()
    with open(bd / "scope_check.json", "w") as f:
        f.write("{ this is not valid json }")

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "bad-task-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=True,
    )
    summary = builder.build()
    task = summary["tasks"][0]
    assert task["status"] == TASK_STATUS_FAILED_VALIDATION


# ---------------------------------------------------------------------------
# Test 6: Missing optional files produce warnings, not errors
# ---------------------------------------------------------------------------

def test_missing_optional_files_warnings_not_errors(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    # Bundle with only BUNDLE_STATUS.json — no optional files
    make_bundle_dir(bundle_root, "minimal-task-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_ready",
            "clean_for_task": True,
        },
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "minimal-task-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    # No errors should have been raised
    assert summary["overall_status"] == OVERALL_RUN_READY
    # Warnings about missing optional files
    warnings = [w for w in summary["warnings"] if "risk_notes.md" in w.get("message", "")]
    assert len(warnings) >= 1


# ---------------------------------------------------------------------------
# Test 7: Missing bundle status produces warning in non-strict mode
# ---------------------------------------------------------------------------

def test_missing_bundle_status_warning_non_strict(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    # Create a task directory but NO BUNDLE_STATUS.json
    bd = bundle_root / "no-status-task-001"
    bd.mkdir()
    with open(bd / "local_gate.txt", "w") as f:
        f.write("PASS")

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "no-status-task-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()
    task = summary["tasks"][0]
    # Status should be NOT_EVALUATED since no bundle status was found
    assert task["status"] == TASK_STATUS_NOT_EVALUATED


# ---------------------------------------------------------------------------
# Test 8: Missing bundle status fails in strict mode
# ---------------------------------------------------------------------------

def test_missing_bundle_status_fails_strict(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    # Create a task directory but NO BUNDLE_STATUS.json
    bd = bundle_root / "no-status-task-001"
    bd.mkdir()
    with open(bd / "local_gate.txt", "w") as f:
        f.write("PASS")

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "no-status-task-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=True,
    )
    summary = builder.build()
    task = summary["tasks"][0]
    # In strict mode, missing BUNDLE_STATUS.json is an error
    assert task["status"] == TASK_STATUS_FAILED_VALIDATION


# ---------------------------------------------------------------------------
# Test 9: Hard fail if any bundle has hermes_touched: true
# ---------------------------------------------------------------------------

def test_hard_fail_hermes_touched(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    make_bundle_dir(bundle_root, "bad-hermes-task-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_ready",
            "clean_for_task": True,
            "hermes_touched": True,
        },
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "bad-hermes-task-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    # Should be flagged in safety_invariants
    assert summary["safety_invariants"]["hermes_touched"] is True

    # In the main() function, hard_fail_keys would cause exit code 2
    hard_fail_keys = [
        k for k, v in summary["safety_invariants"].items()
        if k in HARD_FAIL_BOOLEANS and v is True
    ]
    assert "hermes_touched" in hard_fail_keys


# ---------------------------------------------------------------------------
# Test 10: Hard fail if any bundle has dispatch_occurred: true
# ---------------------------------------------------------------------------

def test_hard_fail_dispatch_occurred(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    make_bundle_dir(bundle_root, "bad-dispatch-task-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_ready",
            "clean_for_task": True,
            "dispatch_occurred": True,
        },
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "bad-dispatch-task-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    assert summary["safety_invariants"]["dispatch_occurred"] is True

    hard_fail_keys = [
        k for k, v in summary["safety_invariants"].items()
        if k in HARD_FAIL_BOOLEANS and v is True
    ]
    assert "dispatch_occurred" in hard_fail_keys


# ---------------------------------------------------------------------------
# Test 11: Hard fail if any bundle has production_board_touched: true
# ---------------------------------------------------------------------------

def test_hard_fail_production_board_touched(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    make_bundle_dir(bundle_root, "bad-board-task-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_ready",
            "clean_for_task": True,
            "production_board_touched": True,
        },
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "bad-board-task-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    assert summary["safety_invariants"]["production_board_touched"] is True

    hard_fail_keys = [
        k for k, v in summary["safety_invariants"].items()
        if k in HARD_FAIL_BOOLEANS and v is True
    ]
    assert "production_board_touched" in hard_fail_keys


# ---------------------------------------------------------------------------
# Test 12: Expected task missing is reported
# ---------------------------------------------------------------------------

def test_expected_task_missing_warning(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    # Bundle has task-001 but expected tasks include task-001 and task-002
    make_bundle_dir(bundle_root, "task-001", {
        "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "task-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=["task-001", "task-002"],
        allow_missing=False, strict=False,
    )
    summary = builder.build()

    # task-002 is missing from bundle but expected
    expected_missing = [
        w for w in summary["warnings"]
        if w.get("code") == "expected_task_missing" and w.get("task_id") == "task-002"
    ]
    assert len(expected_missing) == 1


# ---------------------------------------------------------------------------
# Test 13: Duplicate task ID fails
# ---------------------------------------------------------------------------

def test_duplicate_task_id_warning(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    tasks = [
        {"task_id": "dup-task", "task_type": "docs_consistency",
         "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []},
        {"task_id": "dup-task", "task_type": "test_gap",
         "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []},
    ]

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": tasks},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    dup_warnings = [
        w for w in summary["warnings"]
        if w.get("code") == "duplicate_task_id"
    ]
    assert len(dup_warnings) >= 1


# ---------------------------------------------------------------------------
# Test 14: Bundle path traversal outside root fails
# ---------------------------------------------------------------------------

def test_bundle_path_traversal_outside_root(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    # Create a task with a name that tries to escape
    # Path traversal would be caught by validate_bundle_path
    # In practice this is handled by the bundle_index structure
    # We test the validate_bundle_path function directly

    from build_autocoder_run_summary import validate_bundle_path

    # A path that goes outside bundle_root
    evil_path = temp_dir / "..tmp" / "escape"
    result = validate_bundle_path(evil_path, bundle_root)
    # Should return error because it escapes
    assert result is not None


# ---------------------------------------------------------------------------
# Test 15: Markdown output includes task table
# ---------------------------------------------------------------------------

def test_markdown_includes_task_table(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    make_bundle_dir(bundle_root, "task-table-test-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_ready",
            "clean_for_task": True,
            "scope_status": "clean",
            "ci_status": "success",
        },
        "local_gate.txt": "PASS",
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "task-table-test-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo="/home/max/Automated-Edge-Discovery",
        base_sha="51eb88ac7c6602774e2e522120515a943d14409c",
        integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    output_md = temp_dir / "RUN_SUMMARY.md"
    build_markdown_report(summary, output_md)

    md = output_md.read_text()
    assert "| Task ID |" in md  # table header
    # Task ID appears in task table rows (may be truncated by display, check for partial match)
    assert "task-table-test-" in md  # partial match — task ID appears in table
    assert "Task Counts" in md
    assert "Safety Invariants" in md
    assert "Gate Summary" in md


# ---------------------------------------------------------------------------
# Test 16: JSON output includes artifact index
# ---------------------------------------------------------------------------

def test_json_includes_artifact_index(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "artifact-test-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    # artifact_index should be present
    assert "artifact_index" in summary
    assert "json_report" in summary["artifact_index"]
    assert "markdown_report" in summary["artifact_index"]


# ---------------------------------------------------------------------------
# Test 17: Human action becomes resolve_blocker when any task is blocked
# ---------------------------------------------------------------------------

def test_human_action_resolve_blocker_when_blocked(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    make_bundle_dir(bundle_root, "blocked-action-test-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_blocked",
            "clean_for_task": False,
            "blocker_code": "allowed_scope_violations",
            "blocker_summary": "Scope violation detected",
        },
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "blocked-action-test-001", "task_type": "test_gap_analysis",
             "risk_level": "medium", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    assert summary["human_action"] == HUMAN_RESOLVE
    assert summary["human_action_required"] is True


# ---------------------------------------------------------------------------
# Test 18: Overall status RUN_READY when all tasks are TASK_READY
# ---------------------------------------------------------------------------

def test_overall_ready_when_all_tasks_ready(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    make_bundle_dir(bundle_root, "ready-task-001", {
        "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        "local_gate.txt": "PASS",
    })
    make_bundle_dir(bundle_root, "ready-task-002", {
        "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        "local_gate.txt": "PASS",
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "ready-task-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []},
            {"task_id": "ready-task-002", "task_type": "test_gap_analysis",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []},
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    assert summary["overall_status"] == OVERALL_RUN_READY
    assert summary["tasks_ready"] == 2
    assert summary["tasks_blocked"] == 0


# ---------------------------------------------------------------------------
# Test 19: Overall status PARTIAL_READY when some ready and some blocked
# ---------------------------------------------------------------------------

def test_overall_partial_ready_mixed(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    make_bundle_dir(bundle_root, "mixed-ready-001", {
        "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        "local_gate.txt": "PASS",
    })
    make_bundle_dir(bundle_root, "mixed-blocked-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_blocked", "clean_for_task": False,
            "blocker_code": "allowed_scope_violations", "blocker_summary": "blocked",
        },
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "mixed-ready-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []},
            {"task_id": "mixed-blocked-001", "task_type": "test_gap_analysis",
             "risk_level": "medium", "allowed_files": [], "forbidden_files": [], "expected_outputs": []},
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    assert summary["overall_status"] == OVERALL_PARTIAL_READY
    assert summary["tasks_ready"] == 1
    assert summary["tasks_blocked"] == 1


# ---------------------------------------------------------------------------
# Test 20: Overall status BLOCKED when all attempted tasks are blocked
# ---------------------------------------------------------------------------

def test_overall_blocked_all_blocked(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    make_bundle_dir(bundle_root, "all-blocked-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_blocked", "clean_for_task": False,
            "blocker_code": "allowed_scope_violations", "blocker_summary": "blocked",
        },
    })
    make_bundle_dir(bundle_root, "all-blocked-002", {
        "BUNDLE_STATUS.json": {
            "status": "task_blocked", "clean_for_task": False,
            "blocker_code": "forbidden_file_access", "blocker_summary": "blocked",
        },
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "all-blocked-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []},
            {"task_id": "all-blocked-002", "task_type": "test_gap_analysis",
             "risk_level": "medium", "allowed_files": [], "forbidden_files": [], "expected_outputs": []},
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    assert summary["overall_status"] == OVERALL_BLOCKED
    assert summary["tasks_ready"] == 0
    assert summary["tasks_blocked"] == 2


# ---------------------------------------------------------------------------
# Test 21: Read-only behavior — input bundle files are not modified
# ---------------------------------------------------------------------------

def test_read_only_behavior(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    # Create a BUNDLE_STATUS.json
    status_content = {"status": "task_ready", "clean_for_task": True}
    make_bundle_dir(bundle_root, "readonly-test-001", {
        "BUNDLE_STATUS.json": status_content,
        "local_gate.txt": "PASS",
    })

    # Read the file's content before
    status_path = bundle_root / "readonly-test-001" / "BUNDLE_STATUS.json"
    with open(status_path) as f:
        original_content = f.read()

    # Run the builder
    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "readonly-test-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    # Read the file's content after
    with open(status_path) as f:
        after_content = f.read()

    # Content must be unchanged
    assert original_content == after_content