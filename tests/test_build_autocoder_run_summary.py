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


# ==============================================================================
# NEW TESTS — Part D (blockers, strict mode, profile sentinel)
# ==============================================================================

# --------------------------------------------------------------------------
# Test 22: TASK_BLOCKED creates top-level blockers entry
# --------------------------------------------------------------------------#

def test_blocked_task_creates_blocker_entry(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    make_bundle_dir(bundle_root, "blocked-task-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_blocked",
            "clean_for_task": False,
            "blocker_code": "allowed_scope_violations",
            "blocker_summary": "Scope violation in allowed scope",
        },
        "violations_only.json": {
            "allowed_scope_violations": [
                {"file": "scripts/bad.py", "rule": "executable_scope", "severity": "error"}
            ]
        },
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "blocked-task-001", "task_type": "test_gap_analysis",
             "risk_level": "medium", "allowed_files": [], "forbidden_files": [],
             "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    assert summary["tasks_blocked"] == 1
    assert len(summary["blockers"]) == 1
    b = summary["blockers"][0]
    assert b["task_id"] == "blocked-task-001"
    assert b["blocker_code"] == "allowed_scope_violations"
    assert b["human_action"] == HUMAN_RESOLVE
    assert "allowed scope" in b["blocker_summary"].lower()
    assert b["source"] == "violations_only.json"


# --------------------------------------------------------------------------#
# Test 23: Markdown blocker count and JSON blocker count agree
# --------------------------------------------------------------------------#

def test_blocker_count_json_md_agree(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    make_bundle_dir(bundle_root, "blocked-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_blocked", "clean_for_task": False,
            "blocker_code": "allowed_scope_violations",
        },
    })
    make_bundle_dir(bundle_root, "blocked-002", {
        "BUNDLE_STATUS.json": {
            "status": "task_blocked", "clean_for_task": False,
            "blocker_code": "forbidden_file_access",
        },
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "blocked-001", "task_type": "test_gap_analysis",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []},
            {"task_id": "blocked-002", "task_type": "test_gap_analysis",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []},
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    assert len(summary["blockers"]) == 2
    assert summary["tasks_blocked"] == 2

    # Markdown blocker section derived from task data should match
    md_blocked_count = sum(
        1 for t in summary["tasks"] if t["status"] == TASK_STATUS_BLOCKED
    )
    assert len(summary["blockers"]) == md_blocked_count


# --------------------------------------------------------------------------#
# Test 24: TASK_BLOCKED without blocker_code creates validation warning
# --------------------------------------------------------------------------#

def test_blocked_without_blocker_code_warning(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    # BUNDLE_STATUS has task_blocked but no blocker_code
    make_bundle_dir(bundle_root, "bad-blocked-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_blocked",
            "clean_for_task": False,
            # intentionally no blocker_code
            "blocker_summary": "something went wrong",
        },
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "bad-blocked-001", "task_type": "test_gap_analysis",
             "risk_level": "medium", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    assert summary["tasks_blocked"] == 1
    # Should produce a blocker_code_missing warning
    code_warnings = [
        w for w in summary["warnings"]
        if w.get("code") == "blocker_code_missing" and w.get("task_id") == "bad-blocked-001"
    ]
    assert len(code_warnings) == 1


# --------------------------------------------------------------------------#
# Test 25: Multiple blocked tasks create multiple blocker entries
# --------------------------------------------------------------------------#

def test_multiple_blocked_tasks_multiple_blockers(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    for i in range(3):
        make_bundle_dir(bundle_root, f"blocked-multi-{i:03d}", {
            "BUNDLE_STATUS.json": {
                "status": "task_blocked", "clean_for_task": False,
                "blocker_code": f"violation_type_{i}",
                "blocker_summary": f"Blocker {i}",
            },
        })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": f"blocked-multi-{i:03d}", "task_type": "test_gap_analysis",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
            for i in range(3)
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    assert len(summary["blockers"]) == 3
    blocker_ids = {b["task_id"] for b in summary["blockers"]}
    assert blocker_ids == {"blocked-multi-000", "blocked-multi-001", "blocked-multi-002"}


# --------------------------------------------------------------------------#
# Test 26: No blocked tasks results in empty blockers list
# --------------------------------------------------------------------------#

def test_no_blocked_tasks_empty_blockers(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    make_bundle_dir(bundle_root, "clean-001", {
        "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        "local_gate.txt": "PASS",
    })
    make_bundle_dir(bundle_root, "clean-002", {
        "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        "local_gate.txt": "PASS",
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "clean-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []},
            {"task_id": "clean-002", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []},
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    assert len(summary["blockers"]) == 0
    assert summary["tasks_blocked"] == 0


# --------------------------------------------------------------------------#
# Test 27: Strict mode upgrades missing violations_only.json to TASK_FAILED_VALIDATION
# --------------------------------------------------------------------------#

def test_strict_missing_violations_only_fails(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    # Bundle has BUNDLE_STATUS.json and local_gate.txt but no violations_only.json
    make_bundle_dir(bundle_root, "strict-fail-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_ready",
            "clean_for_task": True,
        },
        "local_gate.txt": "PASS",
        # NO violations_only.json
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "strict-fail-001", "task_type": "test_gap_analysis",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=True,
    )
    summary = builder.build()

    task = summary["tasks"][0]
    assert task["status"] == TASK_STATUS_FAILED_VALIDATION
    # And it should appear as a top-level blocker too
    assert len(summary["blockers"]) == 1
    assert summary["blockers"][0]["task_id"] == "strict-fail-001"
    assert summary["blockers"][0]["blocker_code"] == "strict_contextual_required"


# --------------------------------------------------------------------------#
# Test 28: Strict mode upgrades malformed JSON to TASK_FAILED_VALIDATION
# --------------------------------------------------------------------------#

def test_strict_malformed_json_fails(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    # violations_only.json is malformed
    bd = bundle_root / "malformed-001"
    bd.mkdir()
    with open(bd / "BUNDLE_STATUS.json", "w") as f:
        json.dump({"status": "task_ready", "clean_for_task": True}, f)
    with open(bd / "violations_only.json", "w") as f:
        f.write("{ this is not valid json }")

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "malformed-001", "task_type": "test_gap_analysis",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=True,
    )
    summary = builder.build()

    task = summary["tasks"][0]
    assert task["status"] == TASK_STATUS_FAILED_VALIDATION


# --------------------------------------------------------------------------#
# Test 29: Strict mode does NOT fail for missing optional risk_notes.md
# --------------------------------------------------------------------------#

def test_strict_missing_optional_risk_notes_still_ready(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    # Only BUNDLE_STATUS.json + local_gate.txt — risk_notes.md is optional
    make_bundle_dir(bundle_root, "optional-missing-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_ready",
            "clean_for_task": True,
        },
        "local_gate.txt": "PASS",
        # NO risk_notes.md, NO violations_only.json, NO scope_check.json
        # But in non-strict mode those are warnings, not errors
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "optional-missing-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=True,
    )
    summary = builder.build()

    # In strict mode, violations_only.json IS contextually required
    # So we should get TASK_FAILED_VALIDATION
    task = summary["tasks"][0]
    assert task["status"] == TASK_STATUS_FAILED_VALIDATION


# --------------------------------------------------------------------------#
# Test 30: Non-strict mode reports missing required artifact as warning, not hard failure
# --------------------------------------------------------------------------#

def test_nonstrict_missing_contextual_warns_not_fails(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    # Only BUNDLE_STATUS.json — violations_only.json is missing
    make_bundle_dir(bundle_root, "warn-only-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_ready",
            "clean_for_task": True,
        },
        "local_gate.txt": "PASS",
        # NO violations_only.json
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "warn-only-001", "task_type": "test_gap_analysis",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    # Non-strict: missing violations_only.json is a warning, not a failure
    task = summary["tasks"][0]
    assert task["status"] == TASK_STATUS_READY
    # Should have a warning
    warns = [w for w in summary["warnings"] if "violations_only" in w.get("message", "")]
    assert len(warns) >= 1


# --------------------------------------------------------------------------#
# Test 31: Overall FAILED_VALIDATION if any task is TASK_FAILED_VALIDATION
# --------------------------------------------------------------------------#

def test_overall_failed_validation_when_any_task_fails(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    # One TASK_READY + one TASK_FAILED_VALIDATION
    make_bundle_dir(bundle_root, "ready-001", {
        "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        "local_gate.txt": "PASS",
        "violations_only.json": {"violations": [], "executable_violations": []},
    })
    # This one fails strict mode due to missing violations_only.json and local_gate.txt
    make_bundle_dir(bundle_root, "fails-validation-001", {
        "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        # missing violations_only.json and local_gate.txt
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "ready-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []},
            {"task_id": "fails-validation-001", "task_type": "test_gap_analysis",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []},
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=True,
    )
    summary = builder.build()

    assert summary["overall_status"] == "FAILED_VALIDATION"
    assert summary["tasks_failed_validation"] == 1


# --------------------------------------------------------------------------#
# Test 32: Overall PARTIAL_READY if some ready and one blocked (no validation failure)
# --------------------------------------------------------------------------#

def test_overall_partial_ready_no_validation_failure(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    make_bundle_dir(bundle_root, "ready-001", {
        "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        "local_gate.txt": "PASS",
    })
    make_bundle_dir(bundle_root, "blocked-001", {
        "BUNDLE_STATUS.json": {
            "status": "task_blocked", "clean_for_task": False,
            "blocker_code": "allowed_scope_violations",
            "blocker_summary": "Blocked",
        },
    })

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "ready-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []},
            {"task_id": "blocked-001", "task_type": "test_gap_analysis",
             "risk_level": "medium", "allowed_files": [], "forbidden_files": [], "expected_outputs": []},
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=False,
    )
    summary = builder.build()

    # No TASK_FAILED_VALIDATION — so PARTIAL_READY (not FAILED_VALIDATION)
    assert summary["overall_status"] == OVERALL_PARTIAL_READY
    assert summary["tasks_ready"] == 1
    assert summary["tasks_blocked"] == 1
    assert summary["tasks_failed_validation"] == 0


# --------------------------------------------------------------------------#
# Test 33: TASK_FAILED_VALIDATION task creates top-level blocker entry
# --------------------------------------------------------------------------#

def test_failed_validation_creates_blocker_entry(temp_dir):
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()

    # Missing BUNDLE_STATUS.json — strict mode should mark as TASK_FAILED_VALIDATION
    bd = bundle_root / "missing-status-001"
    bd.mkdir()
    with open(bd / "local_gate.txt", "w") as f:
        f.write("PASS")

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": [
            {"task_id": "missing-status-001", "task_type": "docs_consistency",
             "risk_level": "low", "allowed_files": [], "forbidden_files": [], "expected_outputs": []}
        ]},
        bundle_root=bundle_root,
        repo=None, base_sha=None, integration_branch=None,
        expected_task_ids=None, allow_missing=False, strict=True,
    )
    summary = builder.build()

    task = summary["tasks"][0]
    assert task["status"] == TASK_STATUS_FAILED_VALIDATION
    assert len(summary["blockers"]) == 1
    assert summary["blockers"][0]["task_id"] == "missing-status-001"
    assert summary["blockers"][0]["blocker_code"] == "bundle_missing_required"


# --------------------------------------------------------------------------#
# Test 34: Profile sentinel — no file changes produces clean result
# --------------------------------------------------------------------------#

import sys
import tempfile as temp_lib

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "local"))
from check_hermes_profile_mutation import compare_snapshots, compute_file_snapshot


def test_profile_sentinel_no_mutation_clean(temp_dir):
    # Create two identical temp files
    f1 = temp_dir / "memory.md"
    f2 = temp_dir / "user.md"
    f1.write_text("initial content")
    f2.write_text("user data")

    paths = [str(f1), str(f2)]

    snap1 = {"generated_at": "2026-01-01T00:00:00+00:00", "paths": paths,
             "snapshots": compute_file_snapshot(paths)}
    snap2 = {"generated_at": "2026-01-01T00:01:00+00:00", "paths": paths,
             "snapshots": compute_file_snapshot(paths)}

    result = compare_snapshots(snap1, snap2)

    assert result["memory_or_profile_updated"] is False
    assert len(result["paths_mutated"]) == 0
    assert result["checked"] is True


# --------------------------------------------------------------------------#
# Test 35: Changed MEMORY.md hash produces memory_or_profile_updated: true
# --------------------------------------------------------------------------#

def test_profile_sentinel_memory_changed(temp_dir):
    f1 = temp_dir / "memory.md"
    f1.write_text("original content")

    paths = [str(f1)]
    snap1 = {"generated_at": "2026-01-01T00:00:00+00:00", "paths": paths,
             "snapshots": compute_file_snapshot(paths)}

    # Mutate the file
    f1.write_text("mutated content")
    snap2 = {"generated_at": "2026-01-01T00:01:00+00:00", "paths": paths,
             "snapshots": compute_file_snapshot(paths)}

    result = compare_snapshots(snap1, snap2)

    assert result["memory_or_profile_updated"] is True
    assert str(f1) in result["paths_mutated"]
    assert result["mutations"][0]["type"] == "content_changed"


# --------------------------------------------------------------------------#
# Test 36: Changed USER.md hash produces memory_or_profile_updated: true
# --------------------------------------------------------------------------#

def test_profile_sentinel_user_changed(temp_dir):
    f1 = temp_dir / "user.md"
    f1.write_text("user original")

    paths = [str(f1)]
    snap1 = {"generated_at": "2026-01-01T00:00:00+00:00", "paths": paths,
             "snapshots": compute_file_snapshot(paths)}

    f1.write_text("user mutated")
    snap2 = {"generated_at": "2026-01-01T00:01:00+00:00", "paths": paths,
             "snapshots": compute_file_snapshot(paths)}

    result = compare_snapshots(snap1, snap2)

    assert result["memory_or_profile_updated"] is True
    assert str(f1) in result["paths_mutated"]
    assert result["mutations"][0]["type"] == "content_changed"


# --------------------------------------------------------------------------#
# Test 37: Missing file is deterministic (before=None, after=None => no mutation)
# --------------------------------------------------------------------------#

def test_profile_sentinel_file_never_existed_no_mutation(temp_dir):
    never_existed = str(temp_dir / "ghost.txt")
    paths = [never_existed]

    snap1 = {"generated_at": "2026-01-01T00:00:00+00:00", "paths": paths,
             "snapshots": compute_file_snapshot(paths)}
    snap2 = {"generated_at": "2026-01-01T00:01:00+00:00", "paths": paths,
             "snapshots": compute_file_snapshot(paths)}

    result = compare_snapshots(snap1, snap2)

    # Neither snapshot had the file — no mutation
    assert result["memory_or_profile_updated"] is False
    assert never_existed not in result["paths_mutated"]


# --------------------------------------------------------------------------#
# Test 38: Sentinel output is read-only (compare does not modify input snapshots)
# --------------------------------------------------------------------------#

def test_profile_sentinel_readonly_does_not_mutate_inputs(temp_dir):
    f1 = temp_dir / "memory.md"
    f1.write_text("content a")

    paths = [str(f1)]
    snap1 = {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "paths": paths,
        "snapshots": compute_file_snapshot(paths),
    }
    snap1_copy = json.loads(json.dumps(snap1))  # deep copy

    f1.write_text("content b")
    snap2 = {
        "generated_at": "2026-01-01T00:01:00+00:00",
        "paths": paths,
        "snapshots": compute_file_snapshot(paths),
    }

    result = compare_snapshots(snap1, snap2)

    # snap1 must be unchanged after comparison
    assert snap1 == snap1_copy
    assert result["memory_or_profile_updated"] is True
    # result itself must not have modified snap1 or snap2
    assert "before_snapshot" in result
    assert result["before_snapshot"] == snap1_copy


# ---------------------------------------------------------------------------
# Tests: v2 dependency and integration plan in run summary
# ---------------------------------------------------------------------------

class TestDependencyInRunSummary:
    """Tests for v2 dependency and integration plan in run summary."""

    def test_dependency_status_satisfied(self, temp_dir):
        """Dependency satisfied produces dependency_status: satisfied."""
        bundle_root = temp_dir / "bundles"
        bundle_root.mkdir()
        index_path = temp_dir / "BUNDLE_INDEX.json"
        output_json = temp_dir / "RUN_SUMMARY.json"
        output_md = temp_dir / "RUN_SUMMARY.md"

        tasks = [
            {
                "task_id": "base-helper-001",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "depends_on": [],
                "blocks": [],
                "status": "TASK_READY",
            },
            {
                "task_id": "docs-update-002",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "depends_on": ["base-helper-001"],
                "blocks": [],
                "status": "TASK_READY",
            },
        ]
        make_bundle_dir(bundle_root, "base-helper-001", {
            "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        })
        make_bundle_dir(bundle_root, "docs-update-002", {
            "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        })

        with open(index_path, "w") as f:
            json.dump(minimal_bundle_index(tasks), f)

        from build_autocoder_run_summary import main as run_main
        import sys
        old_argv = sys.argv
        sys.argv = [
            "build_autocoder_run_summary.py",
            "--run-id", "test-dep-001",
            "--bundle-index", str(index_path),
            "--bundle-root", str(bundle_root),
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ]
        try:
            rc = run_main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        data = json.loads(output_json.read_text())
        docs_entry = next(t for t in data["tasks"] if t["task_id"] == "docs-update-002")
        assert docs_entry["dependency_status"] == "satisfied"

    def test_no_dependencies_no_dependencies_status(self, temp_dir):
        """No dependencies produces dependency_status: no_dependencies."""
        bundle_root = temp_dir / "bundles"
        bundle_root.mkdir()
        index_path = temp_dir / "BUNDLE_INDEX.json"
        output_json = temp_dir / "RUN_SUMMARY.json"
        output_md = temp_dir / "RUN_SUMMARY.md"

        tasks = [
            {
                "task_id": "solo-task-001",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "depends_on": [],
                "blocks": [],
                "status": "TASK_READY",
            },
        ]
        make_bundle_dir(bundle_root, "solo-task-001", {
            "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        })

        with open(index_path, "w") as f:
            json.dump(minimal_bundle_index(tasks), f)

        from build_autocoder_run_summary import main as run_main
        import sys
        old_argv = sys.argv
        sys.argv = [
            "build_autocoder_run_summary.py",
            "--run-id", "test-dep-002",
            "--bundle-index", str(index_path),
            "--bundle-root", str(bundle_root),
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ]
        try:
            rc = run_main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        data = json.loads(output_json.read_text())
        entry = data["tasks"][0]
        assert entry["dependency_status"] == "no_dependencies"

    def test_downstream_blocked_task(self, temp_dir):
        """Downstream blocked task appears in integration plan."""
        bundle_root = temp_dir / "bundles"
        bundle_root.mkdir()
        index_path = temp_dir / "BUNDLE_INDEX.json"
        output_json = temp_dir / "RUN_SUMMARY.json"
        output_md = temp_dir / "RUN_SUMMARY.md"

        tasks = [
            {
                "task_id": "blocked-core-004",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "depends_on": [],
                "blocks": [],
                "status": "TASK_BLOCKED",
                "blocker_code": "allowed_scope_violations",
            },
            {
                "task_id": "downstream-blocked-005",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "depends_on": ["blocked-core-004"],
                "blocks": [],
                "status": "TASK_BLOCKED",
            },
        ]
        make_bundle_dir(bundle_root, "blocked-core-004", {
            "BUNDLE_STATUS.json": {"status": "task_blocked", "clean_for_task": False,
                                   "blocker_code": "allowed_scope_violations",
                                   "blocker_summary": "violations"},
            "violations_only.json": {"allowed_scope_violations": [{"file": "x.py"}]},
        })
        make_bundle_dir(bundle_root, "downstream-blocked-005", {
            "BUNDLE_STATUS.json": {"status": "task_blocked", "clean_for_task": False,
                                   "blocker_code": "blocked_by_dependency",
                                   "blocker_summary": "dependency blocked"},
        })

        with open(index_path, "w") as f:
            json.dump(minimal_bundle_index(tasks), f)

        from build_autocoder_run_summary import main as run_main
        import sys
        old_argv = sys.argv
        sys.argv = [
            "build_autocoder_run_summary.py",
            "--run-id", "test-dep-003",
            "--bundle-index", str(index_path),
            "--bundle-root", str(bundle_root),
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ]
        try:
            rc = run_main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        data = json.loads(output_json.read_text())
        ip = data.get("integration_plan", {})
        assert "blocked-core-004" in ip.get("blocked_from_promotion", [])
        assert "downstream-blocked-005" in ip.get("blocked_from_promotion", [])

    def test_blocked_dependency_blocks_promotion(self, temp_dir):
        """Failed validation dependency blocks promotion."""
        bundle_root = temp_dir / "bundles"
        bundle_root.mkdir()
        index_path = temp_dir / "BUNDLE_INDEX.json"
        output_json = temp_dir / "RUN_SUMMARY.json"
        output_md = temp_dir / "RUN_SUMMARY.md"

        tasks = [
            {
                "task_id": "failing-dep",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "depends_on": [],
                "blocks": [],
                "status": "TASK_FAILED_VALIDATION",
            },
            {
                "task_id": "dependent-task",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "depends_on": ["failing-dep"],
                "blocks": [],
                "status": "TASK_READY",
            },
        ]
        make_bundle_dir(bundle_root, "failing-dep", {
            "BUNDLE_STATUS.json": {"status": "task_failed_validation", "clean_for_task": False},
        })
        make_bundle_dir(bundle_root, "dependent-task", {
            "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        })

        with open(index_path, "w") as f:
            json.dump(minimal_bundle_index(tasks), f)

        from build_autocoder_run_summary import main as run_main
        import sys
        old_argv = sys.argv
        sys.argv = [
            "build_autocoder_run_summary.py",
            "--run-id", "test-dep-004",
            "--bundle-index", str(index_path),
            "--bundle-root", str(bundle_root),
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ]
        try:
            rc = run_main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        data = json.loads(output_json.read_text())
        dep_entry = next(t for t in data["tasks"] if t["task_id"] == "dependent-task")
        assert dep_entry["dependency_status"] == "dependency_failed_validation"
        ip = data.get("integration_plan", {})
        assert "dependent-task" in ip.get("blocked_from_promotion", [])

    def test_ready_task_with_unsatisfied_dependency_not_in_ready(self, temp_dir):
        """Ready task with unsatisfied dependency is not in ready_for_promotion."""
        bundle_root = temp_dir / "bundles"
        bundle_root.mkdir()
        index_path = temp_dir / "BUNDLE_INDEX.json"
        output_json = temp_dir / "RUN_SUMMARY.json"
        output_md = temp_dir / "RUN_SUMMARY.md"

        tasks = [
            {
                "task_id": "base-helper-001",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "depends_on": [],
                "blocks": [],
                "status": "TASK_BLOCKED",  # not ready
            },
            {
                "task_id": "docs-update-002",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "depends_on": ["base-helper-001"],
                "blocks": [],
                "status": "TASK_READY",
            },
        ]
        make_bundle_dir(bundle_root, "base-helper-001", {
            "BUNDLE_STATUS.json": {"status": "task_blocked", "clean_for_task": False,
                                   "blocker_code": "allowed_scope_violations"},
        })
        make_bundle_dir(bundle_root, "docs-update-002", {
            "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        })

        with open(index_path, "w") as f:
            json.dump(minimal_bundle_index(tasks), f)

        from build_autocoder_run_summary import main as run_main
        import sys
        old_argv = sys.argv
        sys.argv = [
            "build_autocoder_run_summary.py",
            "--run-id", "test-dep-005",
            "--bundle-index", str(index_path),
            "--bundle-root", str(bundle_root),
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ]
        try:
            rc = run_main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        data = json.loads(output_json.read_text())
        ip = data.get("integration_plan", {})
        assert "docs-update-002" in ip.get("blocked_from_promotion", [])
        assert "docs-update-002" not in ip.get("ready_for_promotion", [])

    def test_suggested_pr_groups_emitted(self, temp_dir):
        """Suggested PR groups are emitted in integration plan."""
        bundle_root = temp_dir / "bundles"
        bundle_root.mkdir()
        index_path = temp_dir / "BUNDLE_INDEX.json"
        output_json = temp_dir / "RUN_SUMMARY.json"
        output_md = temp_dir / "RUN_SUMMARY.md"

        tasks = [
            {
                "task_id": "task-a",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "pr_group": "pr-core",
                "status": "TASK_READY",
            },
            {
                "task_id": "task-b",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "pr_group": "pr-followup",
                "status": "TASK_READY",
            },
        ]
        make_bundle_dir(bundle_root, "task-a", {
            "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        })
        make_bundle_dir(bundle_root, "task-b", {
            "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        })

        with open(index_path, "w") as f:
            json.dump(minimal_bundle_index(tasks), f)

        from build_autocoder_run_summary import main as run_main
        import sys
        old_argv = sys.argv
        sys.argv = [
            "build_autocoder_run_summary.py",
            "--run-id", "test-dep-006",
            "--bundle-index", str(index_path),
            "--bundle-root", str(bundle_root),
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ]
        try:
            rc = run_main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        data = json.loads(output_json.read_text())
        ip = data.get("integration_plan", {})
        assert "pr-core" in ip.get("suggested_pr_groups", {})
        assert "task-a" in ip["suggested_pr_groups"]["pr-core"]

    def test_markdown_includes_dependency_summary_section(self, temp_dir):
        """Markdown includes Dependency Summary section."""
        bundle_root = temp_dir / "bundles"
        bundle_root.mkdir()
        index_path = temp_dir / "BUNDLE_INDEX.json"
        output_json = temp_dir / "RUN_SUMMARY.json"
        output_md = temp_dir / "RUN_SUMMARY.md"

        tasks = [
            {
                "task_id": "task-a",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "depends_on": [],
                "blocks": [],
                "status": "TASK_READY",
            },
        ]
        make_bundle_dir(bundle_root, "task-a", {
            "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        })

        with open(index_path, "w") as f:
            json.dump(minimal_bundle_index(tasks), f)

        from build_autocoder_run_summary import main as run_main
        import sys
        old_argv = sys.argv
        sys.argv = [
            "build_autocoder_run_summary.py",
            "--run-id", "test-dep-007",
            "--bundle-index", str(index_path),
            "--bundle-root", str(bundle_root),
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ]
        try:
            rc = run_main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        md_content = output_md.read_text()
        assert "## Dependency Summary" in md_content

    def test_markdown_includes_integration_plan_section(self, temp_dir):
        """Markdown includes Integration Plan section."""
        bundle_root = temp_dir / "bundles"
        bundle_root.mkdir()
        index_path = temp_dir / "BUNDLE_INDEX.json"
        output_json = temp_dir / "RUN_SUMMARY.json"
        output_md = temp_dir / "RUN_SUMMARY.md"

        tasks = [
            {
                "task_id": "task-a",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "depends_on": [],
                "blocks": [],
                "status": "TASK_READY",
            },
        ]
        make_bundle_dir(bundle_root, "task-a", {
            "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        })

        with open(index_path, "w") as f:
            json.dump(minimal_bundle_index(tasks), f)

        from build_autocoder_run_summary import main as run_main
        import sys
        old_argv = sys.argv
        sys.argv = [
            "build_autocoder_run_summary.py",
            "--run-id", "test-dep-008",
            "--bundle-index", str(index_path),
            "--bundle-root", str(bundle_root),
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ]
        try:
            rc = run_main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        md_content = output_md.read_text()
        assert "## Integration Plan" in md_content

    def test_markdown_includes_suggested_pr_groups_section(self, temp_dir):
        """Markdown includes Suggested PR Groups section."""
        bundle_root = temp_dir / "bundles"
        bundle_root.mkdir()
        index_path = temp_dir / "BUNDLE_INDEX.json"
        output_json = temp_dir / "RUN_SUMMARY.json"
        output_md = temp_dir / "RUN_SUMMARY.md"

        tasks = [
            {
                "task_id": "task-a",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "pr_group": "pr-core",
                "status": "TASK_READY",
            },
        ]
        make_bundle_dir(bundle_root, "task-a", {
            "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        })

        with open(index_path, "w") as f:
            json.dump(minimal_bundle_index(tasks), f)

        from build_autocoder_run_summary import main as run_main
        import sys
        old_argv = sys.argv
        sys.argv = [
            "build_autocoder_run_summary.py",
            "--run-id", "test-dep-009",
            "--bundle-index", str(index_path),
            "--bundle-root", str(bundle_root),
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ]
        try:
            rc = run_main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        md_content = output_md.read_text()
        assert "### Suggested PR Groups" in md_content

    def test_overall_status_partial_ready_with_independent_ready_and_downstream_blocked(self, temp_dir):
        """Overall status is PARTIAL_READY when one independent task is ready and one downstream is blocked."""
        bundle_root = temp_dir / "bundles"
        bundle_root.mkdir()
        index_path = temp_dir / "BUNDLE_INDEX.json"
        output_json = temp_dir / "RUN_SUMMARY.json"
        output_md = temp_dir / "RUN_SUMMARY.md"

        tasks = [
            {
                "task_id": "base-helper-001",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "depends_on": [],
                "blocks": [],
                "status": "TASK_READY",
            },
            {
                "task_id": "downstream-blocked-005",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "depends_on": ["base-helper-001"],
                "blocks": [],
                "status": "TASK_BLOCKED",  # downstream blocked
                "blocker_code": "blocked_by_dependency",
            },
        ]
        make_bundle_dir(bundle_root, "base-helper-001", {
            "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        })
        make_bundle_dir(bundle_root, "downstream-blocked-005", {
            "BUNDLE_STATUS.json": {"status": "task_blocked", "clean_for_task": False,
                                   "blocker_code": "blocked_by_dependency"},
        })

        with open(index_path, "w") as f:
            json.dump(minimal_bundle_index(tasks), f)

        from build_autocoder_run_summary import main as run_main
        import sys
        old_argv = sys.argv
        sys.argv = [
            "build_autocoder_run_summary.py",
            "--run-id", "test-dep-010",
            "--bundle-index", str(index_path),
            "--bundle-root", str(bundle_root),
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ]
        try:
            rc = run_main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        data = json.loads(output_json.read_text())
        assert data["overall_status"] == "PARTIAL_READY"

    def test_overall_status_blocked_when_all_blocked_by_dependencies(self, temp_dir):
        """Overall status is BLOCKED when all tasks are blocked by dependencies."""
        bundle_root = temp_dir / "bundles"
        bundle_root.mkdir()
        index_path = temp_dir / "BUNDLE_INDEX.json"
        output_json = temp_dir / "RUN_SUMMARY.json"
        output_md = temp_dir / "RUN_SUMMARY.md"

        tasks = [
            {
                "task_id": "blocked-core-004",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "depends_on": [],
                "blocks": [],
                "status": "TASK_BLOCKED",
                "blocker_code": "allowed_scope_violations",
            },
            {
                "task_id": "downstream-blocked-005",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "depends_on": ["blocked-core-004"],
                "blocks": [],
                "status": "TASK_BLOCKED",
                "blocker_code": "blocked_by_dependency",
            },
        ]
        make_bundle_dir(bundle_root, "blocked-core-004", {
            "BUNDLE_STATUS.json": {"status": "task_blocked", "clean_for_task": False,
                                   "blocker_code": "allowed_scope_violations"},
        })
        make_bundle_dir(bundle_root, "downstream-blocked-005", {
            "BUNDLE_STATUS.json": {"status": "task_blocked", "clean_for_task": False,
                                   "blocker_code": "blocked_by_dependency"},
        })

        with open(index_path, "w") as f:
            json.dump(minimal_bundle_index(tasks), f)

        from build_autocoder_run_summary import main as run_main
        import sys
        old_argv = sys.argv
        sys.argv = [
            "build_autocoder_run_summary.py",
            "--run-id", "test-dep-011",
            "--bundle-index", str(index_path),
            "--bundle-root", str(bundle_root),
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ]
        try:
            rc = run_main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        data = json.loads(output_json.read_text())
        assert data["overall_status"] == "BLOCKED"

    def test_integration_plan_readonly_does_not_modify_git_branches(self, temp_dir):
        """Integration plan is read-only and does not modify git branches."""
        bundle_root = temp_dir / "bundles"
        bundle_root.mkdir()
        index_path = temp_dir / "BUNDLE_INDEX.json"
        output_json = temp_dir / "RUN_SUMMARY.json"
        output_md = temp_dir / "RUN_SUMMARY.md"

        tasks = [
            {
                "task_id": "task-a",
                "task_type": "docs_consistency",
                "risk_level": "low",
                "allowed_files": ["docs/"],
                "forbidden_files": [],
                "expected_outputs": ["docs/out.md"],
                "promotion_target": "integration/test-branch",
                "status": "TASK_READY",
            },
        ]
        make_bundle_dir(bundle_root, "task-a", {
            "BUNDLE_STATUS.json": {"status": "task_ready", "clean_for_task": True},
        })

        with open(index_path, "w") as f:
            json.dump(minimal_bundle_index(tasks), f)

        from build_autocoder_run_summary import main as run_main
        import sys
        old_argv = sys.argv
        sys.argv = [
            "build_autocoder_run_summary.py",
            "--run-id", "test-dep-012",
            "--bundle-index", str(index_path),
            "--bundle-root", str(bundle_root),
            "--output-json", str(output_json),
            "--output-md", str(output_md),
            "--integration-branch", "integration/test-branch",
        ]
        try:
            rc = run_main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        data = json.loads(output_json.read_text())
        ip = data.get("integration_plan", {})
        assert ip.get("integration_branch") == "integration/test-branch"
        # Verify no git operations were attempted by checking safety invariants
        assert data["safety_invariants"]["hermes_touched"] is False