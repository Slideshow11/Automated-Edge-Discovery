#!/usr/bin/env python3
"""
tests/test_pr_gate_kanban_task_create.py

Tests for pr_gate_kanban_task_create.py — dry-run by default,
explicit --apply for mutation.
"""

import importlib.util
import json
import textwrap
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    return Path(__file__).parent.parent


def _import_mod(name: str, rel_path: Path):
    """Import a module from scripts/local relative to REPO_ROOT."""
    full = (_repo_root() / "scripts" / "local" / rel_path).resolve()
    spec = importlib.util.spec_from_file_location(name, full)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_mod():
    return _import_mod("pr_gate_kanban_task_create", "pr_gate_kanban_task_create.py")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mod():
    return _load_mod()


@pytest.fixture
def valid_draft_builder():
    return {
        "packet_kind": "aed.pr_gate.task_draft.v1",
        "schema_version": 1,
        "idempotency_key": "pr197-4e5f25f-a1b2c3d4-create_builder_patch",
        "action": "create_builder_patch_task_draft",
        "pr_number": 197,
        "head_sha": "4e5f25f0eef1a33c1c7a48cdeb73d61a7dfb363c",
        "task_draft": {
            "title": "[PR #197] Builder patch task",
            "body": "## Goal\n\nImplement the builder patch for PR #197.",
            "assignee": "",
            "status": "TODO",
        },
    }


@pytest.fixture
def valid_draft_reviewer():
    return {
        "packet_kind": "aed.pr_gate.task_draft.v1",
        "schema_version": 1,
        "idempotency_key": "pr197-4e5f25f0-a1b2c3d4-create_reviewer",
        "action": "create_reviewer_task_draft",
        "pr_number": 197,
        "head_sha": "4e5f25f0eef1a33c1c7a48cdeb73d61a7dfb363c",
        "task_draft": {
            "title": "[PR #197] Reviewer task",
            "body": "## Goal\n\nReview the builder patch for PR #197.",
            "assignee": "",
            "status": "TODO",
        },
    }


@pytest.fixture
def no_action_draft():
    return {
        "packet_kind": "aed.pr_gate.task_draft.v1",
        "schema_version": 1,
        "idempotency_key": "pr197-4e5f25f0-a1b2c3d4-no_action",
        "action": "no_action_wait",
        "pr_number": 197,
        "head_sha": "4e5f25f0eef1a33c1c7a48cdeb73d61a7dfb363c",
        "task_draft": {
            "title": "No-op",
            "body": "No action required.",
            "assignee": "",
            "status": "TODO",
        },
    }


@pytest.fixture
def unsafe_draft():
    return {
        "packet_kind": "aed.pr_gate.task_draft.v1",
        "schema_version": 1,
        "idempotency_key": "pr197-4e5f25f0-a1b2c3d4-unsafe",
        "action": "create_builder_patch_task_draft",
        "pr_number": 197,
        "head_sha": "4e5f25f0eef1a33c1c7a48cdeb73d61a7dfb363c",
        "task_draft": {
            "title": "[PR #197] Unsafe task",
            "body": "## Goal\n\nRun: gh pr merge --squash\n",
            "assignee": "",
            "status": "TODO",
        },
    }


@pytest.fixture
def missing_idempotency_key_draft():
    return {
        "packet_kind": "aed.pr_gate.task_draft.v1",
        "schema_version": 1,
        "idempotency_key": "",
        "action": "create_builder_patch_task_draft",
        "pr_number": 197,
        "head_sha": "4e5f25f0eef1a33c1c7a48cdeb73d61a7dfb363c",
        "task_draft": {
            "title": "Missing IK",
            "body": "body",
            "assignee": "",
            "status": "TODO",
        },
    }


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

class TestValidateTaskDraft:
    def test_valid_builder_draft_passes(self, mod, valid_draft_builder):
        errors = mod.validate_task_draft(valid_draft_builder)
        assert errors == [], f"unexpected errors: {errors}"

    def test_valid_reviewer_draft_passes(self, mod, valid_draft_reviewer):
        errors = mod.validate_task_draft(valid_draft_reviewer)
        assert errors == [], f"unexpected errors: {errors}"

    def test_valid_no_action_draft_passes(self, mod, no_action_draft):
        errors = mod.validate_task_draft(no_action_draft)
        assert errors == [], f"unexpected errors: {errors}"

    def test_wrong_packet_kind_fails(self, mod, valid_draft_builder):
        draft = dict(valid_draft_builder, packet_kind="wrong.kind")
        errors = mod.validate_task_draft(draft)
        assert any("packet_kind" in e for e in errors)

    def test_wrong_schema_version_fails(self, mod, valid_draft_builder):
        draft = dict(valid_draft_builder, schema_version=2)
        errors = mod.validate_task_draft(draft)
        assert any("schema_version" in e for e in errors)

    def test_missing_idempotency_key_fails(self, mod, missing_idempotency_key_draft):
        errors = mod.validate_task_draft(missing_idempotency_key_draft)
        assert any("idempotency_key" in e.lower() for e in errors)

    def test_bad_idempotency_key_format_fails(self, mod, valid_draft_builder):
        draft = dict(valid_draft_builder, idempotency_key="not-valid-format")
        errors = mod.validate_task_draft(draft)
        assert any("idempotency_key" in e.lower() for e in errors)

    def test_bad_head_sha_fails(self, mod, valid_draft_builder):
        draft = dict(valid_draft_builder, head_sha="not-a-sha")
        errors = mod.validate_task_draft(draft)
        assert any("head_sha" in e for e in errors)

    def test_unknown_action_fails(self, mod, valid_draft_builder):
        draft = dict(valid_draft_builder, action="unknown_action")
        errors = mod.validate_task_draft(draft)
        assert any("action" in e.lower() for e in errors)

    def test_unsafe_body_gh_pr_merge_fails(self, mod):
        draft = {
            "packet_kind": "aed.pr_gate.task_draft.v1",
            "schema_version": 1,
            "idempotency_key": "pr197-4e5f25f-x-create_builder",
            "action": "create_builder_patch_task_draft",
            "pr_number": 197,
            "head_sha": "4e5f25f0eef1a33c1c7a48cdeb73d61a7dfb363c",
            "task_draft": {
                "title": "Unsafe",
                "body": "Run gh pr merge --squash",
                "assignee": "",
                "status": "TODO",
            },
        }
        errors = mod.validate_task_draft(draft)
        assert any("forbidden" in e.lower() or "merge" in e.lower() for e in errors)

    def test_unsafe_body_dispatch_fails(self, mod):
        draft = {
            "packet_kind": "aed.pr_gate.task_draft.v1",
            "schema_version": 1,
            "idempotency_key": "pr197-4e5f25f-x-create_builder",
            "action": "create_builder_patch_task_draft",
            "pr_number": 197,
            "head_sha": "4e5f25f0eef1a33c1c7a48cdeb73d61a7dfb363c",
            "task_draft": {
                "title": "Unsafe",
                "body": "hermes kanban dispatch --board aed",
                "assignee": "",
                "status": "TODO",
            },
        }
        errors = mod.validate_task_draft(draft)
        assert any("forbidden" in e.lower() or "dispatch" in e.lower() for e in errors)

    def test_missing_pr_number_fails(self, mod, valid_draft_builder):
        draft = {k: v for k, v in valid_draft_builder.items() if k != "pr_number"}
        errors = mod.validate_task_draft(draft)
        assert any("pr_number" in e.lower() for e in errors)

    def test_non_string_body_fails(self, mod):
        draft = {
            "packet_kind": "aed.pr_gate.task_draft.v1",
            "schema_version": 1,
            "idempotency_key": "pr198-e6a9eb9-a1b2c3d4-create_builder_patch",
            "action": "create_builder_patch_task_draft",
            "pr_number": 198,
            "head_sha": "e6a9eb9c40c8f8a9f09c7dd64d3a14509ac94fc8",
            "task_draft": {
                "title": "Test",
                "body": 12345,  # non-string body
                "assignee": "",
                "status": "TODO",
            },
        }
        errors = mod.validate_task_draft(draft)
        assert any("must be a string" in e for e in errors)


# ---------------------------------------------------------------------------
# Build plan tests
# ---------------------------------------------------------------------------

class TestBuildPlan:
    def test_dry_run_builder_patch_produces_task(self, mod, valid_draft_builder):
        plan = mod.build_plan(valid_draft_builder, board="aed", dry_run=True, apply_mode=False)
        assert plan["dry_run"] is True
        assert plan["kanban_task"] is not None
        assert plan["kanban_task"]["idempotency_key"] == valid_draft_builder["idempotency_key"]
        assert plan["kanban_task"]["title"] == valid_draft_builder["task_draft"]["title"]

    def test_dry_run_reviewer_produces_task(self, mod, valid_draft_reviewer):
        plan = mod.build_plan(valid_draft_reviewer, board="aed", dry_run=True, apply_mode=False)
        assert plan["dry_run"] is True
        assert plan["kanban_task"] is not None

    def test_dry_run_no_action_wait_produces_no_task(self, mod, no_action_draft):
        plan = mod.build_plan(no_action_draft, board="aed", dry_run=True, apply_mode=False)
        assert plan["dry_run"] is True
        assert plan["kanban_task"] is None
        assert plan["recommended_action"] == "no_action"

    def test_dry_run_never_calls_hermes(self, mod, valid_draft_builder):
        with mock.patch.object(mod, "_call_hermes_kanban") as mock_call:
            plan = mod.build_plan(valid_draft_builder, board="aed", dry_run=True, apply_mode=False)
            mock_call.assert_not_called()
        assert plan["dry_run"] is True

    def test_apply_mode_calls_hermes_kanban_once(self, mod, valid_draft_builder):
        with mock.patch.object(mod, "_call_hermes_kanban") as mock_call:
            mock_call.return_value = (0, "", "")  # no duplicate -> proceed to create
            plan = mod.build_plan(valid_draft_builder, board="aed", dry_run=False, apply_mode=True)
            # duplicate check + create = 2 calls
            assert mock_call.call_count == 2

    def test_apply_mode_duplicate_prevents_create(self, mod, valid_draft_builder):
        with mock.patch.object(mod, "_call_hermes_kanban") as mock_call:
            mock_call.return_value = (0, "task 99", "")  # duplicate found
            plan = mod.build_plan(valid_draft_builder, board="aed", dry_run=False, apply_mode=True)
            assert plan["duplicate_check"]["duplicate_found"] is True
            assert plan["duplicate_check"]["existing_task_id"] == "99"
            assert plan["apply_result"]["applied"] is False

    def test_no_action_wait_apply_creates_nothing(self, mod, no_action_draft):
        with mock.patch.object(mod, "_call_hermes_kanban") as mock_call:
            plan = mod.build_plan(no_action_draft, board="aed", dry_run=False, apply_mode=True)
            mock_call.assert_not_called()
            assert plan["kanban_task"] is None
            assert plan["recommended_action"] == "no_action"

    def test_no_dispatch_in_stop_rules(self, mod, valid_draft_builder):
        plan = mod.build_plan(valid_draft_builder, board="aed", dry_run=True, apply_mode=False)
        assert "no_dispatch" in plan["stop_rules"]

    def test_no_merge_in_stop_rules(self, mod, valid_draft_builder):
        plan = mod.build_plan(valid_draft_builder, board="aed", dry_run=True, apply_mode=False)
        assert "no_merge" in plan["stop_rules"]

    def test_all_stop_rules_present(self, mod, valid_draft_builder):
        plan = mod.build_plan(valid_draft_builder, board="aed", dry_run=True, apply_mode=False)
        for rule in ["no_dispatch", "no_merge", "no_pr_patch", "no_codex_request",
                     "no_memory_update", "no_skill_manage"]:
            assert rule in plan["stop_rules"], f"missing stop rule: {rule}"


# ---------------------------------------------------------------------------
# Render markdown tests
# ---------------------------------------------------------------------------

class TestRenderMarkdown:
    def test_renders_title_and_board(self, mod, valid_draft_builder):
        plan = mod.build_plan(valid_draft_builder, board="aed", dry_run=True, apply_mode=False)
        md = mod.render_markdown(plan)
        assert valid_draft_builder["task_draft"]["title"] in md
        assert "aed" in md

    def test_renders_idempotency_key(self, mod, valid_draft_builder):
        plan = mod.build_plan(valid_draft_builder, board="aed", dry_run=True, apply_mode=False)
        md = mod.render_markdown(plan)
        assert valid_draft_builder["idempotency_key"] in md

    def test_renders_dry_run_mode(self, mod, valid_draft_builder):
        plan = mod.build_plan(valid_draft_builder, board="aed", dry_run=True, apply_mode=False)
        md = mod.render_markdown(plan)
        assert "dry-run" in md.lower() or "DRY-RUN" in md

    def test_renders_apply_mode(self, mod, valid_draft_reviewer):
        with mock.patch.object(mod, "_call_hermes_kanban") as mock_call:
            mock_call.return_value = (0, "created task 456", "")
            plan = mod.build_plan(valid_draft_reviewer, board="aed", dry_run=False, apply_mode=True)
            md = mod.render_markdown(plan)
            assert "apply" in md.lower() or "APPLY" in md

    def test_renders_stop_rules(self, mod, valid_draft_builder):
        plan = mod.build_plan(valid_draft_builder, board="aed", dry_run=True, apply_mode=False)
        md = mod.render_markdown(plan)
        for rule in plan["stop_rules"]:
            assert rule in md

    def test_no_action_wait_shows_no_task(self, mod, no_action_draft):
        plan = mod.build_plan(no_action_draft, board="aed", dry_run=True, apply_mode=False)
        md = mod.render_markdown(plan)
        assert "no task" in md.lower() or "no_action" in md.lower()


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestCLI:
    def test_dry_run_default_does_not_call_hermes(self, mod, valid_draft_builder, tmp_path):
        draft_path = tmp_path / "draft.json"
        draft_path.write_text(json.dumps(valid_draft_builder))
        out_json = tmp_path / "plan.json"

        with mock.patch.object(mod, "_call_hermes_kanban") as mock_call:
            with mock.patch("sys.argv", [
                "pr_gate_kanban_task_create.py",
                "--task-draft", str(draft_path),
                "--board", "aed",
                "--output-json", str(out_json),
            ]):
                rc = mod.main()
                mock_call.assert_not_called()

    def test_apply_calls_hermes(self, mod, valid_draft_builder, tmp_path):
        draft_path = tmp_path / "draft.json"
        draft_path.write_text(json.dumps(valid_draft_builder))
        out_json = tmp_path / "plan.json"

        with mock.patch.object(mod, "_call_hermes_kanban") as mock_call:
            # First call (search): empty = no duplicate found
            # Second call (create): success with task ID
            mock_call.side_effect = [
                (0, "", ""),       # duplicate check: no existing task
                (0, "created task 789", ""),  # create: succeeded
            ]
            with mock.patch("sys.argv", [
                "pr_gate_kanban_task_create.py",
                "--task-draft", str(draft_path),
                "--board", "aed",
                "--output-json", str(out_json),
                "--apply",
            ]):
                rc = mod.main()
                assert mock_call.call_count == 2  # search + create

    def test_hermes_output_rejected(self, mod, valid_draft_builder, tmp_path):
        draft_path = tmp_path / "draft.json"
        draft_path.write_text(json.dumps(valid_draft_builder))
        out_json = Path("/home/max/.hermes/some_output.json")

        with mock.patch("sys.argv", [
            "pr_gate_kanban_task_create.py",
            "--task-draft", str(draft_path),
            "--board", "aed",
            "--output-json", str(out_json),
        ]):
            rc = mod.main()
            assert rc != 0

    def test_missing_task_draft_fails(self, mod, tmp_path):
        missing = tmp_path / "nonexistent.json"
        with mock.patch("sys.argv", [
            "pr_gate_kanban_task_create.py",
            "--task-draft", str(missing),
            "--board", "aed",
        ]):
            rc = mod.main()
            assert rc == 1

    def test_invalid_task_draft_json_fails(self, mod, tmp_path):
        draft_path = tmp_path / "bad.json"
        draft_path.write_text("not valid json")
        with mock.patch("sys.argv", [
            "pr_gate_kanban_task_create.py",
            "--task-draft", str(draft_path),
            "--board", "aed",
        ]):
            rc = mod.main()
            assert rc == 1

    def test_apply_no_action_wait_creates_nothing(self, mod, no_action_draft, tmp_path):
        draft_path = tmp_path / "draft.json"
        draft_path.write_text(json.dumps(no_action_draft))

        with mock.patch.object(mod, "_call_hermes_kanban") as mock_call:
            with mock.patch("sys.argv", [
                "pr_gate_kanban_task_create.py",
                "--task-draft", str(draft_path),
                "--board", "aed",
                "--apply",
            ]):
                rc = mod.main()
                mock_call.assert_not_called()


# ---------------------------------------------------------------------------
# Safety grep test
# ---------------------------------------------------------------------------

class TestSafetyGrep:
    def test_source_contains_no_merge_calls_in_live_code(self):
        src = (
            _repo_root()
            / "scripts" / "local" / "pr_gate_kanban_task_create.py"
        ).read_text()
        dedented = textwrap.dedent(src)

        for pat in ["gh pr merge", "gh pr comment", "gh pr create", "git push"]:
            assert pat.lower() not in dedented.lower(), f"forbidden '{pat}' found in source"

        assert "hermes kanban dispatch" not in dedented

    def test_safety_patterns_reject_unsafe_bodies(self, mod, unsafe_draft):
        errors = mod.validate_task_draft(unsafe_draft)
        assert len(errors) > 0
        assert any("forbidden" in e.lower() or "merge" in e.lower() for e in errors)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])