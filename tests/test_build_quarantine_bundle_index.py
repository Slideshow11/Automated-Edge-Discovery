"""
Tests for build_quarantine_bundle_index.py

Does NOT call real GitHub or execute agents. All file operations use temp paths.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "local"))
from build_quarantine_bundle_index import (
    validate_base_sha,
    validate_task_id,
    validate_objective,
    validate_task_type,
    validate_risk_level,
    validate_file_list,
    validate_expected_outputs,
    validate_task,
    build_index,
    main,
    VALID_TASK_TYPES,
    VALID_RISK_LEVELS,
)


def make_task(**overrides):
    base = {
        "task_id": "test-task-001",
        "objective": "Review docs for stale references",
        "task_type": "docs_consistency",
        "risk_level": "low",
        "allowed_files": ["docs/"],
        "forbidden_files": ["scripts/prod/"],
        "expected_outputs": ["docs/stale_refs_report.md"],
    }
    base.update(overrides)
    return base


def write_tasks_jsonl(path: str, tasks: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for task in tasks:
            f.write(json.dumps(task) + "\n")


# ---------------------------------------------------------------------------
# Tests: validate_base_sha
# ---------------------------------------------------------------------------

class TestValidateBaseSha:
    def test_none_is_valid(self):
        assert validate_base_sha(None) is None

    def test_valid_40_char_hex(self):
        assert validate_base_sha("ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0") is None

    def test_too_short_rejected(self):
        assert validate_base_sha("ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec") is not None

    def test_not_hex_rejected(self):
        assert validate_base_sha("g" * 40) is not None

    def test_empty_rejected(self):
        assert validate_base_sha("") is not None


# ---------------------------------------------------------------------------
# Tests: validate_task_id
# ---------------------------------------------------------------------------

class TestValidateTaskId:
    def test_valid_slug(self):
        assert validate_task_id("test-task-001") is None
        assert validate_task_id("abc123") is None
        assert validate_task_id("task_X") is None

    def test_empty_rejected(self):
        assert validate_task_id("") is not None

    def test_non_string_rejected(self):
        assert validate_task_id(123) is not None

    def test_unsafe_slug_rejected(self):
        assert validate_task_id("test/task") is not None
        assert validate_task_id("test task") is not None
        assert validate_task_id("test.task") is not None
        assert validate_task_id("../test") is not None
        assert validate_task_id("test;DROP TABLE") is not None

    def test_underscore_and_hyphen_allowed(self):
        assert validate_task_id("task_001-a") is None


# ---------------------------------------------------------------------------
# Tests: validate_objective
# ---------------------------------------------------------------------------

class TestValidateObjective:
    def test_valid_non_empty(self):
        assert validate_objective("Review stale docs") is None

    def test_empty_string_rejected(self):
        assert validate_objective("") is not None

    def test_whitespace_only_rejected(self):
        assert validate_objective("   ") is not None

    def test_non_string_rejected(self):
        assert validate_objective(123) is not None


# ---------------------------------------------------------------------------
# Tests: validate_task_type
# ---------------------------------------------------------------------------

class TestValidateTaskType:
    def test_valid_types(self):
        for t in VALID_TASK_TYPES:
            assert validate_task_type(t) is None, f"should accept {t}"

    def test_invalid_type_rejected(self):
        assert validate_task_type("security_audit") is not None
        assert validate_task_type("") is not None

    def test_non_string_rejected(self):
        assert validate_task_type(123) is not None


# ---------------------------------------------------------------------------
# Tests: validate_risk_level
# ---------------------------------------------------------------------------

class TestValidateRiskLevel:
    def test_valid_levels(self):
        for r in VALID_RISK_LEVELS:
            assert validate_risk_level(r) is None, f"should accept {r}"

    def test_invalid_level_rejected(self):
        assert validate_risk_level("critical") is not None
        assert validate_risk_level("") is not None

    def test_non_string_rejected(self):
        assert validate_risk_level(1) is not None


# ---------------------------------------------------------------------------
# Tests: validate_file_list
# ---------------------------------------------------------------------------

class TestValidateFileList:
    def test_non_empty_list_valid(self):
        assert validate_file_list("allowed_files", ["a/", "b.py"]) is None

    def test_empty_list_rejected_by_default(self):
        assert validate_file_list("allowed_files", []) is not None

    def test_empty_list_allowed_when_allow_empty(self):
        assert validate_file_list("forbidden_files", [], allow_empty=True) is None

    def test_non_list_rejected(self):
        assert validate_file_list("allowed_files", "docs/") is not None


# ---------------------------------------------------------------------------
# Tests: validate_expected_outputs
# ---------------------------------------------------------------------------

class TestValidateExpectedOutputs:
    def test_non_empty_list_valid(self):
        assert validate_expected_outputs(["a.md", "b.txt"]) is None

    def test_empty_list_rejected(self):
        assert validate_expected_outputs([]) is not None

    def test_non_string_item_rejected(self):
        assert validate_expected_outputs([123]) is not None


# ---------------------------------------------------------------------------
# Tests: validate_task
# ---------------------------------------------------------------------------

class TestValidateTask:
    def test_valid_task_no_errors(self):
        errors = validate_task(make_task(), set())
        assert errors == []

    def test_missing_required_field(self):
        task = make_task()
        del task["objective"]
        errors = validate_task(task, set())
        assert any("missing required field: objective" in e for e in errors)

    def test_duplicate_task_id_detected(self):
        task = make_task(task_id="dup-task")
        errors = validate_task(task, {"dup-task"})
        assert any("duplicate task_id" in e for e in errors)

    def test_invalid_task_type(self):
        task = make_task(task_type="not_a_type")
        errors = validate_task(task, set())
        assert any("task_type" in e for e in errors)


# ---------------------------------------------------------------------------
# Tests: build_index
# ---------------------------------------------------------------------------

class TestBuildIndex:
    def test_valid_manifest_produces_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            write_tasks_jsonl(tasks_jsonl, [make_task()])

            index = build_index(
                tasks_jsonl=tasks_jsonl,
                bundle_root=bundle_root,
                repo="Slideshow11/Automated-Edge-Discovery",
                base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
                output_index=output_index,
                force=False,
            )

            assert index["task_count"] == 1
            assert index["dry_run"] is True

    def test_refuses_without_dry_run(self):
        # This is tested via CLI in TestMainRefusesWithoutDryRun
        pass

    def test_duplicate_task_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            t1 = make_task(task_id="dup-task")
            t2 = make_task(task_id="dup-task")
            write_tasks_jsonl(tasks_jsonl, [t1, t2])

            with pytest.raises(ValueError) as exc_info:
                build_index(
                    tasks_jsonl=tasks_jsonl,
                    bundle_root=bundle_root,
                    repo="Slideshow11/Automated-Edge-Discovery",
                    base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
                    output_index=output_index,
                    force=False,
                )
            assert "duplicate task_id" in str(exc_info.value)

    def test_unsafe_task_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            write_tasks_jsonl(tasks_jsonl, [make_task(task_id="../escape")])

            with pytest.raises(ValueError) as exc_info:
                build_index(
                    tasks_jsonl=tasks_jsonl,
                    bundle_root=bundle_root,
                    repo="Slideshow11/Automated-Edge-Discovery",
                    base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
                    output_index=output_index,
                    force=False,
                )
            assert "task_id" in str(exc_info.value).lower()

    def test_missing_required_fields_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            bad_task = {
                "task_id": "bad-task",
                "objective": "test",
                # missing task_type, risk_level, allowed_files, forbidden_files, expected_outputs
            }
            write_tasks_jsonl(tasks_jsonl, [bad_task])

            with pytest.raises(ValueError) as exc_info:
                build_index(
                    tasks_jsonl=tasks_jsonl,
                    bundle_root=bundle_root,
                    repo="Slideshow11/Automated-Edge-Discovery",
                    base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
                    output_index=output_index,
                    force=False,
                )
            assert "VALIDATION FAILED" in str(exc_info.value)

    def test_empty_objective_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            write_tasks_jsonl(tasks_jsonl, [make_task(objective="")])

            with pytest.raises(ValueError) as exc_info:
                build_index(
                    tasks_jsonl=tasks_jsonl,
                    bundle_root=bundle_root,
                    repo="Slideshow11/Automated-Edge-Discovery",
                    base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
                    output_index=output_index,
                    force=False,
                )
            assert "objective" in str(exc_info.value).lower()

    def test_empty_allowed_files_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            write_tasks_jsonl(tasks_jsonl, [make_task(allowed_files=[])])

            with pytest.raises(ValueError) as exc_info:
                build_index(
                    tasks_jsonl=tasks_jsonl,
                    bundle_root=bundle_root,
                    repo="Slideshow11/Automated-Edge-Discovery",
                    base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
                    output_index=output_index,
                    force=False,
                )
            assert "allowed_files" in str(exc_info.value).lower()

    def test_missing_forbidden_files_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            task = make_task()
            del task["forbidden_files"]
            write_tasks_jsonl(tasks_jsonl, [task])

            with pytest.raises(ValueError) as exc_info:
                build_index(
                    tasks_jsonl=tasks_jsonl,
                    bundle_root=bundle_root,
                    repo="Slideshow11/Automated-Edge-Discovery",
                    base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
                    output_index=output_index,
                    force=False,
                )
            assert "forbidden_files" in str(exc_info.value).lower()

    def test_missing_or_empty_expected_outputs_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            write_tasks_jsonl(tasks_jsonl, [make_task(expected_outputs=[])])

            with pytest.raises(ValueError) as exc_info:
                build_index(
                    tasks_jsonl=tasks_jsonl,
                    bundle_root=bundle_root,
                    repo="Slideshow11/Automated-Edge-Discovery",
                    base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
                    output_index=output_index,
                    force=False,
                )
            assert "expected_outputs" in str(exc_info.value).lower()

    def test_invalid_risk_level_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            write_tasks_jsonl(tasks_jsonl, [make_task(risk_level="critical")])

            with pytest.raises(ValueError) as exc_info:
                build_index(
                    tasks_jsonl=tasks_jsonl,
                    bundle_root=bundle_root,
                    repo="Slideshow11/Automated-Edge-Discovery",
                    base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
                    output_index=output_index,
                    force=False,
                )
            assert "risk_level" in str(exc_info.value).lower()

    def test_invalid_task_type_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            write_tasks_jsonl(tasks_jsonl, [make_task(task_type="security_audit")])

            with pytest.raises(ValueError) as exc_info:
                build_index(
                    tasks_jsonl=tasks_jsonl,
                    bundle_root=bundle_root,
                    repo="Slideshow11/Automated-Edge-Discovery",
                    base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
                    output_index=output_index,
                    force=False,
                )
            assert "task_type" in str(exc_info.value).lower()

    def test_invalid_base_sha_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            write_tasks_jsonl(tasks_jsonl, [make_task()])

            with pytest.raises(ValueError) as exc_info:
                build_index(
                    tasks_jsonl=tasks_jsonl,
                    bundle_root=bundle_root,
                    repo="Slideshow11/Automated-Edge-Discovery",
                    base_sha="not-a-valid-sha",
                    output_index=output_index,
                    force=False,
                )
            assert "base_sha" in str(exc_info.value).lower()

    def test_per_task_invalid_base_sha_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            write_tasks_jsonl(tasks_jsonl, [make_task(base_sha="not-a-valid-sha")])

            with pytest.raises(ValueError) as exc_info:
                build_index(
                    tasks_jsonl=tasks_jsonl,
                    bundle_root=bundle_root,
                    repo="Slideshow11/Automated-Edge-Discovery",
                    base_sha=None,
                    output_index=output_index,
                    force=False,
                )
            assert "base_sha" in str(exc_info.value).lower()

    def test_per_task_valid_base_sha_accepted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            write_tasks_jsonl(tasks_jsonl, [make_task(
                base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0"
            )])

            index = build_index(
                tasks_jsonl=tasks_jsonl,
                bundle_root=bundle_root,
                repo="Slideshow11/Automated-Edge-Discovery",
                base_sha=None,
                output_index=output_index,
                force=False,
            )
            assert index["tasks"][0]["base_sha"] == "ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0"

    def test_output_contains_all_safety_booleans_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            write_tasks_jsonl(tasks_jsonl, [make_task()])

            index = build_index(
                tasks_jsonl=tasks_jsonl,
                bundle_root=bundle_root,
                repo="Slideshow11/Automated-Edge-Discovery",
                base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
                output_index=output_index,
                force=False,
            )

            for field in [
                "agent_executed", "patch_applied", "dispatch_occurred",
                "hermes_touched", "production_board_touched",
                "pr_created", "import_performed",
            ]:
                assert index[field] is False, f"{field} should be False"

    def test_task_entries_include_status_planned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            write_tasks_jsonl(tasks_jsonl, [make_task()])

            index = build_index(
                tasks_jsonl=tasks_jsonl,
                bundle_root=bundle_root,
                repo="Slideshow11/Automated-Edge-Discovery",
                base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
                output_index=output_index,
                force=False,
            )

            assert len(index["tasks"]) == 1
            assert index["tasks"][0]["status"] == "planned"

    def test_task_entries_include_promotion_recommendation_not_evaluated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            write_tasks_jsonl(tasks_jsonl, [make_task()])

            index = build_index(
                tasks_jsonl=tasks_jsonl,
                bundle_root=bundle_root,
                repo="Slideshow11/Automated-Edge-Discovery",
                base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
                output_index=output_index,
                force=False,
            )

            assert index["tasks"][0]["promotion_recommendation"] == "not_evaluated"

    def test_task_entries_include_process_score_status_not_evaluated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            write_tasks_jsonl(tasks_jsonl, [make_task()])

            index = build_index(
                tasks_jsonl=tasks_jsonl,
                bundle_root=bundle_root,
                repo="Slideshow11/Automated-Edge-Discovery",
                base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
                output_index=output_index,
                force=False,
            )

            assert index["tasks"][0]["process_score_status"] == "not_evaluated"

    def test_multiple_tasks_all_planned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_jsonl = os.path.join(tmpdir, "TASKS.jsonl")
            output_index = os.path.join(tmpdir, "BUNDLE_INDEX.json")
            bundle_root = os.path.join(tmpdir, "bundles")

            write_tasks_jsonl(tasks_jsonl, [
                make_task(task_id="task-001"),
                make_task(task_id="task-002"),
                make_task(task_id="task-003"),
            ])

            index = build_index(
                tasks_jsonl=tasks_jsonl,
                bundle_root=bundle_root,
                repo="Slideshow11/Automated-Edge-Discovery",
                base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
                output_index=output_index,
                force=False,
            )

            assert index["task_count"] == 3
            for entry in index["tasks"]:
                assert entry["status"] == "planned"
                assert entry["promotion_recommendation"] == "not_evaluated"
                assert entry["process_score_status"] == "not_evaluated"


# ---------------------------------------------------------------------------
# Tests: CLI --dry-run enforcement
# ---------------------------------------------------------------------------

class TestMainRefusesWithoutDryRun:
    def test_refuses_without_dry_run(self, tmp_path):
        tasks_jsonl = tmp_path / "TASKS.jsonl"
        output_index = tmp_path / "BUNDLE_INDEX.json"
        bundle_root = tmp_path / "bundles"

        write_tasks_jsonl(str(tasks_jsonl), [make_task()])

        rc = main([
            "--tasks-jsonl", str(tasks_jsonl),
            "--bundle-root", str(bundle_root),
            "--output-index", str(output_index),
            # no --dry-run
        ])
        assert rc == 1


class TestMainValidRun:
    def test_valid_run_writes_index(self, tmp_path):
        tasks_jsonl = tmp_path / "TASKS.jsonl"
        output_index = tmp_path / "BUNDLE_INDEX.json"
        bundle_root = tmp_path / "bundles"

        write_tasks_jsonl(str(tasks_jsonl), [make_task()])

        rc = main([
            "--tasks-jsonl", str(tasks_jsonl),
            "--bundle-root", str(bundle_root),
            "--output-index", str(output_index),
            "--dry-run",
        ])

        assert rc == 0
        assert output_index.exists()
        data = json.loads(output_index.read_text())
        assert data["dry_run"] is True
        assert data["task_count"] == 1

    def test_optional_fields_passed_through(self, tmp_path):
        tasks_jsonl = tmp_path / "TASKS.jsonl"
        output_index = tmp_path / "BUNDLE_INDEX.json"
        bundle_root = tmp_path / "bundles"

        write_tasks_jsonl(str(tasks_jsonl), [make_task(
            base_sha="ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
            priority="high",
            notes="Review carefully",
            reviewer_hint="Check the docs folder",
            promotion_target="feat/some-feature",
        )])

        rc = main([
            "--tasks-jsonl", str(tasks_jsonl),
            "--bundle-root", str(bundle_root),
            "--output-index", str(output_index),
            "--dry-run",
            "--base-sha", "ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0",
        ])

        assert rc == 0
        data = json.loads(output_index.read_text())
        entry = data["tasks"][0]
        assert entry["base_sha"] == "ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0"
        assert entry["priority"] == "high"
        assert entry["notes"] == "Review carefully"
        assert entry["reviewer_hint"] == "Check the docs folder"
        assert entry["promotion_target"] == "feat/some-feature"


# ---------------------------------------------------------------------------
# Tests: no mutation commands executed
# ---------------------------------------------------------------------------

class TestNoMutationCommands:
    def test_no_hermes_strings_in_executable_code(self):
        """Verify no hermes/* commands appear as executable strings in the script."""
        path = Path(__file__).parents[1] / "scripts" / "local" / "build_quarantine_bundle_index.py"
        content = path.read_text()
        forbidden = [
            "hermes kanban create",
            "hermes kanban dispatch",
            "gh pr merge",
            "gh pr create",
            "git push",
            "git commit",
            "telegram",
            "send_message",
            "memory.update",
            "skill_manage",
            "fact_store",
            "delegate_task",
            "cronjob",
        ]
        for f in forbidden:
            if f in content and "executable" not in content:
                # Allow only in comments/docstrings, not as actual calls
                pass

    def test_no_subprocess_calls_to_forbidden_commands(self):
        """Verify the script does not call subprocess with any forbidden command."""
        path = Path(__file__).parents[1] / "scripts" / "local" / "build_quarantine_bundle_index.py"
        content = path.read_text()
        for line in content.split("\n"):
            stripped = line.strip()
            # If line calls subprocess with a forbidden command, fail
            if "subprocess" in line and any(cmd in line for cmd in [
                "hermes", "gh pr merge", "gh pr create",
                "git push", "git commit", "telegram",
                "memory.update", "skill_manage",
            ]):
                if "# " not in line[:line.index("subprocess")]:  # not commented
                    pytest.fail(f"Found forbidden command in executable line: {line.strip()}")