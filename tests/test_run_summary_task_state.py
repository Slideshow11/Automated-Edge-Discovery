#!/usr/bin/env python3
"""
Tests for run-summary task-state interpretation fixes.

These tests verify that the run summary generator correctly derives task
state from per-task bundle artifacts (not from BUNDLE_INDEX.json planned status).

Each test uses temp directories only.
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "local"))

from build_autocoder_run_summary import (
    TASK_STATUS_READY,
    TASK_STATUS_BLOCKED,
    PROMOTION_PROMOTED,
    PROMOTION_NOT_PROMOTED,
    OVERALL_RUN_READY,
    RunSummaryBuilder,
    load_bundle_index,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


def minimal_bundle_index(tasks: list[dict]) -> dict:
    return {
        "version": 1,
        "bundle_root": "/tmp/bundles",
        "tasks": tasks,
    }


def make_bundle_dir(root: Path, task_id: str, files: dict) -> Path:
    d = root / task_id
    d.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        fpath = d / fname
        if fname.endswith(".json"):
            with open(fpath, "w") as f:
                json.dump(content, f)
        elif content is None:
            pass
        else:
            with open(fpath, "w") as f:
                if isinstance(content, str):
                    f.write(content)
                else:
                    f.write(str(content))
    return d


def run_summary(temp_dir, tasks, bundle_files_fn=None):
    """Helper: write bundle index + bundles, run builder, return JSON dict."""
    index_path = temp_dir / "BUNDLE_INDEX.json"
    bundle_root = temp_dir / "bundles"
    output_json = temp_dir / "RUN_SUMMARY.json"
    output_md = temp_dir / "RUN_SUMMARY.md"

    bundle_root.mkdir()
    with open(index_path, "w") as f:
        json.dump(minimal_bundle_index(tasks), f)

    if bundle_files_fn:
        for task in tasks:
            tid = task["task_id"]
            files = bundle_files_fn(tid)
            if files:
                make_bundle_dir(bundle_root, tid, files)

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index={"version": 1, "bundle_root": str(bundle_root), "tasks": tasks},
        bundle_root=bundle_root,
        repo=str(temp_dir),
        base_sha="0" * 40,
        integration_branch="integration/test",
        expected_task_ids=None,
        allow_missing=True,
        strict=False,
    )
    summary = builder.build()
    return summary


# ---------------------------------------------------------------------------
# Test 1: BUNDLE_INDEX planned but BUNDLE_STATUS promoted -> summary counts promoted
# ---------------------------------------------------------------------------

def test_bundle_status_promoted_overrides_index_planned(temp_dir):
    """Bundle index says 'planned' but per-task BUNDLE_STATUS says promoted_to_integration."""
    tasks = [
        {
            "task_id": "task-001",
            "task_type": "docs_consistency",
            "risk_level": "low",
            "status": "planned",  # BUNDLE_INDEX says planned
            "promotion_recommendation": "not_evaluated",
        }
    ]

    def bundle_files(tid):
        return {
            "BUNDLE_STATUS.json": {
                "status": "TASK_READY",
                "promotion_status": "promoted_to_integration",
                "clean_for_task": True,
            },
            "violations_only.json": {"allowed_scope_violations": []},
            "local_gate.txt": "PASS",
        }

    summary = run_summary(temp_dir, tasks, bundle_files)

    # Bundle evidence should override index evidence
    assert summary["tasks_promoted"] == 1
    assert "task-001" in summary["promoted_task_ids"]
    assert summary["tasks_ready"] == 1
    assert summary["tasks"][0]["promotion_status"] == "promoted_to_integration"


# ---------------------------------------------------------------------------
# Test 2: Three promoted tasks -> tasks_promoted = 3
# ---------------------------------------------------------------------------

def test_three_promoted_tasks_tasks_promoted_count(temp_dir):
    """All three tasks have promotion_status=promoted_to_integration."""
    tasks = [
        {"task_id": f"task-{i:03d}", "task_type": "docs_consistency", "risk_level": "low"}
        for i in range(1, 4)
    ]

    def bundle_files(tid):
        return {
            "BUNDLE_STATUS.json": {
                "status": "TASK_READY",
                "promotion_status": "promoted_to_integration",
                "clean_for_task": True,
            },
            "violations_only.json": {"allowed_scope_violations": []},
            "local_gate.txt": "PASS",
        }

    summary = run_summary(temp_dir, tasks, bundle_files)

    assert summary["tasks_promoted"] == 3
    assert len(summary["promoted_task_ids"]) == 3
    assert summary["tasks_ready"] == 3


# ---------------------------------------------------------------------------
# Test 3: Promoted task does not appear as blocked
# ---------------------------------------------------------------------------

def test_promoted_task_not_in_blocked(temp_dir):
    """Promoted tasks must not also appear in blocked_task_ids."""
    tasks = [
        {
            "task_id": "task-001",
            "task_type": "docs_consistency",
            "risk_level": "low",
            "status": "planned",  # BUNDLE_INDEX planned — bundle overrides
        },
        {
            "task_id": "task-002",
            "task_type": "docs_consistency",
            "risk_level": "low",
            "status": "planned",
        },
    ]

    def bundle_files(tid):
        if tid == "task-001":
            return {
                "BUNDLE_STATUS.json": {
                    "status": "TASK_READY",
                    "promotion_status": "promoted_to_integration",
                    "clean_for_task": True,
                },
                "violations_only.json": {"allowed_scope_violations": []},
                "local_gate.txt": "PASS",
            }
        return {
            "BUNDLE_STATUS.json": {
                "status": "TASK_READY",
                "promotion_status": "not_promoted",
                "clean_for_task": True,
            },
            "violations_only.json": {"allowed_scope_violations": []},
            "local_gate.txt": "PASS",
        }

    summary = run_summary(temp_dir, tasks, bundle_files)

    assert "task-001" in summary["promoted_task_ids"]
    assert "task-001" not in summary["blocked_task_ids"]
    assert "task-002" in summary["ready_task_ids"]
    assert "task-002" not in summary["blocked_task_ids"]


# ---------------------------------------------------------------------------
# Test 4: local_gate.txt containing LOCAL_GATE_RESULT=pass -> passed
# ---------------------------------------------------------------------------

def test_local_gate_pass_via_local_gate_result_equals_pass(temp_dir):
    """LOCAL_GATE_RESULT=pass marker is recognized as passed."""
    tasks = [{"task_id": "task-001", "task_type": "docs_consistency", "risk_level": "low"}]

    def bundle_files(tid):
        return {
            "BUNDLE_STATUS.json": {
                "status": "TASK_READY",
                "clean_for_task": True,
            },
            "violations_only.json": {"allowed_scope_violations": []},
            "local_gate.txt": "LOCAL_GATE_RESULT=pass\ncompileall: OK\ngit diff --check: OK",
        }

    summary = run_summary(temp_dir, tasks, bundle_files)

    assert summary["tasks"][0]["local_gate_status"] == "passed"
    assert summary["gate_summary"]["local_gate_passed"] == 1
    assert summary["gate_summary"]["local_gate_failed"] == 0


# ---------------------------------------------------------------------------
# Test 5: local_gate.txt containing PASS -> passed
# ---------------------------------------------------------------------------

def test_local_gate_pass_via_pass_literal(temp_dir):
    """Pure 'PASS' marker is recognized as passed."""
    tasks = [{"task_id": "task-001", "task_type": "docs_consistency", "risk_level": "low"}]

    def bundle_files(tid):
        return {
            "BUNDLE_STATUS.json": {
                "status": "TASK_READY",
                "clean_for_task": True,
            },
            "violations_only.json": {"allowed_scope_violations": []},
            "local_gate.txt": "PASS",
        }

    summary = run_summary(temp_dir, tasks, bundle_files)

    assert summary["tasks"][0]["local_gate_status"] == "passed"


# ---------------------------------------------------------------------------
# Test 6: Preview-only local_gate -> not_executed
# ---------------------------------------------------------------------------

def test_local_gate_preview_not_executed(temp_dir):
    """Preview-only marker should be interpreted as not_executed, not failed."""
    tasks = [{"task_id": "task-001", "task_type": "docs_consistency", "risk_level": "low"}]

    def bundle_files(tid):
        return {
            "BUNDLE_STATUS.json": {
                "status": "TASK_READY",
                "clean_for_task": True,
            },
            "violations_only.json": {"allowed_scope_violations": []},
            "local_gate.txt": "executed_in_phase2: false\n# preview only — dry run",
        }

    summary = run_summary(temp_dir, tasks, bundle_files)

    assert summary["tasks"][0]["local_gate_status"] == "not_executed"
    assert summary["gate_summary"]["local_gate_failed"] == 0


# ---------------------------------------------------------------------------
# Test 7: Missing local_gate warns but does not fail in non-strict
# ---------------------------------------------------------------------------

def test_missing_local_gate_warns_non_strict(temp_dir):
    """Missing local_gate.txt in non-strict mode produces a warning but no failure."""
    tasks = [{"task_id": "task-001", "task_type": "docs_consistency", "risk_level": "low"}]

    def bundle_files(tid):
        return {
            "BUNDLE_STATUS.json": {
                "status": "TASK_READY",
                "clean_for_task": True,
            },
            "violations_only.json": {"allowed_scope_violations": []},
            # no local_gate.txt
        }

    summary = run_summary(temp_dir, tasks, bundle_files)

    # Should still be TASK_READY (no hard failure in non-strict)
    assert summary["tasks"][0]["status"] == "TASK_READY"
    # local_gate_status should be not_executed
    assert summary["tasks"][0]["local_gate_status"] == "not_executed"
    # Warning about missing local_gate should be present
    warning_codes = [w["code"] for w in summary.get("warnings", [])]
    assert "bundle_file_warning" in warning_codes


# ---------------------------------------------------------------------------
# Test 8: Missing scope_clean does not dirty task if violations_only says clean
# ---------------------------------------------------------------------------

def test_missing_scope_clean_does_not_dirty_if_violations_clean(temp_dir):
    """scope_check.json missing scope_clean field but violations_only says clean_for_task=True."""
    tasks = [{"task_id": "task-001", "task_type": "docs_consistency", "risk_level": "low"}]

    def bundle_files(tid):
        return {
            "BUNDLE_STATUS.json": {
                "status": "TASK_READY",
                "clean_for_task": True,  # violations_only says clean
                "scope_status": "unknown",  # bundle_status is agnostic
            },
            # scope_check.json lacks scope_clean field
            "scope_check.json": {"checked_files": ["docs/foo.md"]},  # no passed/scope_clean/diff_status
            "violations_only.json": {
                "clean_for_task": True,
                "allowed_scope_violations": [],
            },
            "local_gate.txt": "PASS",
        }

    summary = run_summary(temp_dir, tasks, bundle_files)

    # Task should be clean — violations_only clean_for_task overrides missing scope_clean
    assert summary["tasks"][0]["scope_status"] == "unknown"
    assert summary["tasks"][0]["clean_for_task"] is True
    assert summary["tasks"][0]["status"] == "TASK_READY"


# ---------------------------------------------------------------------------
# Test 9: Malformed violations_only.json fails validation in strict mode
# ---------------------------------------------------------------------------

def test_malformed_violations_fails_strict(temp_dir):
    """violations_only.json with malformed JSON should fail in strict mode."""
    tasks = [{"task_id": "task-001", "task_type": "docs_consistency", "risk_level": "low"}]
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()
    (bundle_root / "task-001").mkdir()

    with open(bundle_root / "task-001" / "BUNDLE_STATUS.json", "w") as f:
        json.dump({"status": "TASK_READY", "clean_for_task": True}, f)
    with open(bundle_root / "task-001" / "violations_only.json", "w") as f:
        f.write("not valid json {")

    index = minimal_bundle_index(tasks)
    index_path = temp_dir / "BUNDLE_INDEX.json"
    with open(index_path, "w") as f:
        json.dump(index, f)

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index=index,
        bundle_root=bundle_root,
        repo=str(temp_dir),
        base_sha="0" * 40,
        integration_branch="integration/test",
        expected_task_ids=None,
        allow_missing=False,
        strict=True,  # strict mode
    )
    summary = builder.build()

    # Strict mode with malformed JSON should produce TASK_FAILED_VALIDATION
    assert summary["tasks"][0]["status"] == "TASK_FAILED_VALIDATION"
    assert summary["tasks_failed_validation"] == 1


# ---------------------------------------------------------------------------
# Test 10: Strict mode fails missing BUNDLE_STATUS.json
# ---------------------------------------------------------------------------

def test_strict_mode_missing_bundle_status_fails(temp_dir):
    """Missing BUNDLE_STATUS.json in strict mode is a hard failure."""
    tasks = [{"task_id": "task-001", "task_type": "docs_consistency", "risk_level": "low"}]
    bundle_root = temp_dir / "bundles"
    bundle_root.mkdir()
    # Bundle dir exists but no BUNDLE_STATUS.json
    (bundle_root / "task-001").mkdir()

    index = minimal_bundle_index(tasks)
    index_path = temp_dir / "BUNDLE_INDEX.json"
    with open(index_path, "w") as f:
        json.dump(index, f)

    builder = RunSummaryBuilder(
        run_id="test-run",
        bundle_index=index,
        bundle_root=bundle_root,
        repo=str(temp_dir),
        base_sha="0" * 40,
        integration_branch="integration/test",
        expected_task_ids=None,
        allow_missing=False,
        strict=True,
    )
    summary = builder.build()

    # Strict mode + missing required artifact = TASK_FAILED_VALIDATION
    assert summary["tasks"][0]["status"] == "TASK_FAILED_VALIDATION"
    assert summary["tasks_failed_validation"] == 1


# ---------------------------------------------------------------------------
# Test 11: Markdown includes promoted task count
# ---------------------------------------------------------------------------

def test_markdown_includes_promoted_task_count(temp_dir):
    """Markdown Task Counts table should include Promoted task IDs row."""
    tasks = [
        {
            "task_id": "task-001",
            "task_type": "docs_consistency",
            "risk_level": "low",
        },
        {
            "task_id": "task-002",
            "task_type": "docs_consistency",
            "risk_level": "low",
        },
    ]

    def bundle_files(tid):
        return {
            "BUNDLE_STATUS.json": {
                "status": "TASK_READY",
                "promotion_status": "promoted_to_integration",
                "clean_for_task": True,
            },
            "violations_only.json": {"allowed_scope_violations": []},
            "local_gate.txt": "PASS",
        }

    summary = run_summary(temp_dir, tasks, bundle_files)

    from build_autocoder_run_summary import build_markdown_report
    output_md = temp_dir / "RUN_SUMMARY.md"
    build_markdown_report(summary, output_md)
    md_content = output_md.read_text()

    # Markdown should mention the promoted task IDs
    assert "task-001" in md_content
    assert "task-002" in md_content
    # Should show "Promoted task IDs" in the counts table
    assert "Promoted task IDs" in md_content


# ---------------------------------------------------------------------------
# Test 12: JSON includes promoted_task_ids
# ---------------------------------------------------------------------------

def test_json_includes_promoted_task_ids(temp_dir):
    """Output JSON must have promoted_task_ids, ready_task_ids, blocked_task_ids."""
    tasks = [
        {"task_id": "task-001", "task_type": "docs_consistency", "risk_level": "low"},
        {"task_id": "task-002", "task_type": "docs_consistency", "risk_level": "low"},
        {"task_id": "task-003", "task_type": "docs_consistency", "risk_level": "low"},
    ]

    def bundle_files(tid):
        promoted = tid == "task-001"
        return {
            "BUNDLE_STATUS.json": {
                "status": "TASK_READY",
                "promotion_status": "promoted_to_integration" if promoted else "not_promoted",
                "clean_for_task": True,
            },
            "violations_only.json": {"allowed_scope_violations": []},
            "local_gate.txt": "PASS",
        }

    summary = run_summary(temp_dir, tasks, bundle_files)

    assert "promoted_task_ids" in summary
    assert "ready_task_ids" in summary
    assert "blocked_task_ids" in summary
    assert "task-001" in summary["promoted_task_ids"]
    assert "task-002" in summary["ready_task_ids"]
    assert "task-003" in summary["ready_task_ids"]
    assert len(summary["blocked_task_ids"]) == 0


# ---------------------------------------------------------------------------
# Test 13: Integration plan separates promoted_to_integration from ready_for_promotion
# ---------------------------------------------------------------------------

def test_integration_plan_separates_promoted_from_ready(temp_dir):
    """integration_plan.promoted_to_integration is distinct from ready_for_promotion."""
    tasks = [
        {"task_id": "task-001", "task_type": "docs_consistency", "risk_level": "low"},
        {"task_id": "task-002", "task_type": "docs_consistency", "risk_level": "low"},
    ]

    def bundle_files(tid):
        promoted = tid == "task-001"
        return {
            "BUNDLE_STATUS.json": {
                "status": "TASK_READY",
                "promotion_status": "promoted_to_integration" if promoted else "not_promoted",
                "clean_for_task": True,
            },
            "violations_only.json": {"allowed_scope_violations": []},
            "local_gate.txt": "PASS",
        }

    summary = run_summary(temp_dir, tasks, bundle_files)

    ip = summary.get("integration_plan", {})
    assert "promoted_to_integration" in ip
    assert "ready_for_promotion" in ip
    assert "task-001" in ip["promoted_to_integration"]
    assert "task-001" not in ip["ready_for_promotion"]
    assert "task-002" in ip["ready_for_promotion"]


# ---------------------------------------------------------------------------
# Test 14: Rehearsal-shaped fixture yields RUN_READY and tasks_promoted = 3
# ---------------------------------------------------------------------------

def test_rehearsal_shaped_fixture_yields_run_ready_and_tasks_promoted_3(temp_dir):
    """Rehearsal fixture: BUNDLE_INDEX planned, bundles say promoted_to_integration, PASS gates."""
    tasks = [
        {"task_id": "docs-autocoder-run-summary-example-001", "task_type": "docs_consistency", "risk_level": "low"},
        {"task_id": "docs-task-dependency-example-001", "task_type": "docs_consistency", "risk_level": "low"},
        {"task_id": "docs-audit-validator-expected-pr-example-001", "task_type": "docs_consistency", "risk_level": "low"},
    ]

    def bundle_files(tid):
        return {
            "BUNDLE_STATUS.json": {
                "status": "TASK_READY",
                "promotion_status": "promoted_to_integration",
                "clean_for_task": True,
                "scope_status": "clean",
            },
            "violations_only.json": {"clean_for_task": True, "allowed_scope_violations": []},
            "scope_check.json": {"passed": True, "checked_files": ["docs/test.md"]},
            "local_gate.txt": "PASS\ncompileall: OK\ngit diff --check: OK",
        }

    summary = run_summary(temp_dir, tasks, bundle_files)

    assert summary["overall_status"] == "RUN_READY"
    assert summary["tasks_promoted"] == 3
    assert len(summary["promoted_task_ids"]) == 3
    assert summary["tasks_ready"] == 3
    assert summary["tasks_blocked"] == 0


# ---------------------------------------------------------------------------
# Test 15: Reproduce the rehearsal bug (tasks_promoted was 0 before fix)
# ---------------------------------------------------------------------------

def test_rehearsal_bug_reproduction(temp_dir):
    """Before the fix, BUNDLE_INDEX planned + bundle promoted_to_integration gave tasks_promoted=0.

    This test reproduces the exact scenario from the rehearsal:
    - BUNDLE_INDEX.json has tasks with status="planned" (from dry-run scaffold)
    - Each per-task bundle has BUNDLE_STATUS.json with promotion_status="promoted_to_integration"
    - Expected: tasks_promoted=3 (bundle evidence wins)
    - Before fix: tasks_promoted=0 (index planned status was the source of truth incorrectly)
    """
    tasks = [
        {"task_id": "task-001", "task_type": "docs_consistency", "risk_level": "low", "status": "planned"},
        {"task_id": "task-002", "task_type": "docs_consistency", "risk_level": "low", "status": "planned"},
        {"task_id": "task-003", "task_type": "docs_consistency", "risk_level": "low", "status": "planned"},
    ]

    def bundle_files(tid):
        return {
            "BUNDLE_STATUS.json": {
                "status": "TASK_READY",
                "promotion_status": "promoted_to_integration",
                "clean_for_task": True,
            },
            "violations_only.json": {"clean_for_task": True, "allowed_scope_violations": []},
            "local_gate.txt": "PASS",
        }

    summary = run_summary(temp_dir, tasks, bundle_files)

    # This is the core bug: before fix, tasks_promoted was 0 because the index
    # planned status was used instead of per-task bundle promotion_status
    assert summary["tasks_promoted"] == 3, f"Expected 3, got {summary['tasks_promoted']}"
    assert len(summary["promoted_task_ids"]) == 3

    # Also verify integration_plan has promoted_to_integration list
    ip = summary.get("integration_plan", {})
    assert len(ip.get("promoted_to_integration", [])) == 3
    assert len(ip.get("ready_for_promotion", [])) == 0  # all already promoted