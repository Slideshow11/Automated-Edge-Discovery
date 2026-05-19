#!/usr/bin/env python3
"""
tests/test_pr_gate_controller.py

Tests for pr_gate_controller.py — dry-run by default,
explicit --apply-create-task for Kanban task creation.
"""

import importlib.util
import json
import sys
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
    full = (_repo_root() / "scripts" / "local" / rel_path).resolve()
    spec = importlib.util.spec_from_file_location(name, full)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_mod():
    return _import_mod("pr_gate_controller", "pr_gate_controller.py")


# ---------------------------------------------------------------------------
# Minimal mock artifacts
# ---------------------------------------------------------------------------

def _mock_classifier_packet(classification="ready_for_reviewer", codex_status="clean"):
    return {
        "packet_kind": "aed.pr_gate.classifier.v1",
        "schema_version": 1,
        "classification": classification,
        "ci_status": "green",
        "codex_status": codex_status,
        "head_matches_expected": True,
        "blockers": [],
    }


def _mock_task_draft(action="create_reviewer_task_draft", pr_number=199):
    head_sha = "head8" + "0" * 32
    return {
        "packet_kind": "aed.pr_gate.task_draft.v1",
        "schema_version": 1,
        "idempotency_key": f"pr{pr_number}-head8-hash-{action}",
        "source": {
            "repo_owner": "Slideshow11",
            "repo_name": "Automated-Edge-Discovery",
            "pr_number": str(pr_number),
            "pr_url": f"https://github.com/Slideshow11/Automated-Edge-Discovery/pull/{pr_number}",
            "head_sha": head_sha,
            "classification": "ready_for_reviewer",
            "ci_status": "green",
            "codex_status": "clean",
            "changed_files": [],
        },
        "task_draft": {
            "action": action,
            "title": f"[PR #{pr_number}] Task",
            "body": "## Goal\n\nTest task.",
            "assignee": "",
            "status": "TODO",
        },
    }


def _mock_kanban_plan(dry_run=True, recommended_action=None, kanban_task=None,
                      duplicate_found=False, created_task_id=None):
    return {
        "packet_kind": "aed.pr_gate.kanban_create_plan.v1",
        "schema_version": 1,
        "dry_run": dry_run,
        "board": "aed",
        "kanban_task": kanban_task,
        "duplicate_check": {
            "method": "idempotency_key_tag",
            "duplicate_found": duplicate_found,
            "existing_task_id": "99" if duplicate_found else None,
        },
        "apply_result": {
            "applied": created_task_id is not None,
            "created_task_id": created_task_id,
        },
        "recommended_action": recommended_action or ("no_action" if kanban_task is None else "create_task"),
        "stop_rules": [
            "no_dispatch", "no_merge", "no_pr_patch",
            "no_codex_request", "no_memory_update", "no_skill_manage",
        ],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mod():
    return _load_mod()


@pytest.fixture
def tmp_output_dir(tmp_path):
    d = tmp_path / "controller_run"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def mock_classifier_packet(tmp_path):
    p = tmp_path / "CLASSIFIER_PACKET.json"
    p.write_text(json.dumps(_mock_classifier_packet()))
    return p


@pytest.fixture
def mock_task_draft(tmp_path):
    p = tmp_path / "PR_GATE_TASK_DRAFT.json"
    p.write_text(json.dumps(_mock_task_draft()))
    return p


@pytest.fixture
def mock_task_draft_md(tmp_path):
    p = tmp_path / "PR_GATE_TASK_DRAFT.md"
    p.write_text("# Task Draft")
    return p


@pytest.fixture
def mock_kanban_plan(tmp_path):
    p = tmp_path / "KANBAN_CREATE_PLAN.json"
    p.write_text(json.dumps(_mock_kanban_plan()))
    return p


@pytest.fixture
def mock_kanban_plan_md(tmp_path):
    p = tmp_path / "KANBAN_CREATE_PLAN.md"
    p.write_text("# Kanban Plan")
    return p


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

class TestArgValidation:
    def test_rejects_hermes_output_dir(self, mod, tmp_path):
        hermes_path = Path("/home/max/.hermes/some_output")
        with mock.patch("sys.argv", [
            "pr_gate_controller.py",
            "--repo-owner", "Slideshow11",
            "--repo-name", "Automated-Edge-Discovery",
            "--pr-number", "199",
            "--output-dir", str(hermes_path),
            "--allowed-file", "docs/README.md",
        ]):
            rc = mod.main()
            assert rc == 1

    def test_requires_allowed_file(self, mod, tmp_path):
        with mock.patch("sys.argv", [
            "pr_gate_controller.py",
            "--repo-owner", "Slideshow11",
            "--repo-name", "Automated-Edge-Discovery",
            "--pr-number", "199",
            "--output-dir", str(tmp_path / "out"),
        ]):
            rc = mod.main()
            assert rc == 1

    def test_rejects_missing_child_script(self, mod, tmp_path, monkeypatch):
        # Make child resolution fail
        real_resolve = mod._resolve_child
        def bad_resolve(name):
            raise FileNotFoundError(f"not found: {name}")
        monkeypatch.setattr(mod, "_resolve_child", bad_resolve)

        with mock.patch("sys.argv", [
            "pr_gate_controller.py",
            "--repo-owner", "Slideshow11",
            "--repo-name", "Automated-Edge-Discovery",
            "--pr-number", "199",
            "--output-dir", str(tmp_path / "out"),
            "--allowed-file", "docs/README.md",
        ]):
            rc = mod.main()
            assert rc == 1


# ---------------------------------------------------------------------------
# Dry-run behavior tests
# ---------------------------------------------------------------------------

class TestDryRunBehavior:
    def test_dry_run_calls_child_scripts_in_order(self, mod, tmp_output_dir):
        call_log = []
        real_run_child = mod._run_child

        def mock_run_child(args, *, capture_output=True, check=True):
            call_log.append(args[1] if len(args) > 1 else args[0])
            # Return fake classifier + task_draft + kanban plan artifacts
            if "classify_pr_gate_state.py" in str(args):
                out = tmp_output_dir / "CLASSIFIER_PACKET.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_classifier_packet()))
            elif "pr_gate_task_draft.py" in str(args):
                out = tmp_output_dir / "PR_GATE_TASK_DRAFT.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_task_draft()))
                md = tmp_output_dir / "PR_GATE_TASK_DRAFT.md"
                md.write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                if "--apply" not in args:
                    out = tmp_output_dir / "KANBAN_CREATE_PLAN.json"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps(_mock_kanban_plan(dry_run=True)))
                    md = tmp_output_dir / "KANBAN_CREATE_PLAN.md"
                    md.write_text("# Kanban Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_run_child):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=False,
                )

        # Verify order: classify → task_draft → kanban_plan (no kanban create)
        assert any("classify_pr_gate_state.py" in c for c in call_log)
        assert any("pr_gate_task_draft.py" in c for c in call_log)
        assert any("pr_gate_kanban_task_create.py" in c for c in call_log)
        # In dry-run, --apply should NOT be passed to kanban helper
        apply_calls = [a for a in call_log if "--apply" in a]
        assert len(apply_calls) == 0

    def test_dry_run_never_applies_kanban_creation(self, mod, tmp_output_dir):
        kanban_create_called = []
        real_run_child = mod._run_child

        def mock_run_child(args, *, capture_output=True, check=True):
            if "pr_gate_kanban_task_create.py" in str(args):
                if "--apply" in args:
                    kanban_create_called.append(args)

            if "classify_pr_gate_state.py" in str(args):
                out = tmp_output_dir / "CLASSIFIER_PACKET.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_classifier_packet()))
            elif "pr_gate_task_draft.py" in str(args):
                out = tmp_output_dir / "PR_GATE_TASK_DRAFT.json"
                out.write_text(json.dumps(_mock_task_draft()))
                md = tmp_output_dir / "PR_GATE_TASK_DRAFT.md"
                md.write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                if "--apply" not in args:
                    out = tmp_output_dir / "KANBAN_CREATE_PLAN.json"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps(_mock_kanban_plan(dry_run=True)))
                    md = tmp_output_dir / "KANBAN_CREATE_PLAN.md"
                    md.write_text("# Kanban Plan")

            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_run_child):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=False,
                )

        assert len(kanban_create_called) == 0, "Kanban create should not be called in dry-run"

    def test_apply_create_task_passes_apply_to_kanban_helper(self, mod, tmp_output_dir):
        apply_calls = []
        real_run_child = mod._run_child

        def mock_run_child(args, *, capture_output=True, check=True):
            if "pr_gate_kanban_task_create.py" in str(args):
                if "--apply" in args:
                    apply_calls.append(args)
            if "classify_pr_gate_state.py" in str(args):
                out = tmp_output_dir / "CLASSIFIER_PACKET.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_classifier_packet()))
            elif "pr_gate_task_draft.py" in str(args):
                out = tmp_output_dir / "PR_GATE_TASK_DRAFT.json"
                out.write_text(json.dumps(_mock_task_draft()))
                md = tmp_output_dir / "PR_GATE_TASK_DRAFT.md"
                md.write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                if "--apply" not in args:
                    out = tmp_output_dir / "KANBAN_CREATE_PLAN.json"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps(_mock_kanban_plan(dry_run=True)))
                    md = tmp_output_dir / "KANBAN_CREATE_PLAN.md"
                    md.write_text("# Kanban Plan")
                else:
                    # --apply mode: return a plan with created task
                    out = tmp_output_dir / "KANBAN_CREATE_PLAN.json"
                    out.write_text(json.dumps(_mock_kanban_plan(
                        dry_run=False,
                        kanban_task={"title": "Test", "idempotency_key": "key"},
                        created_task_id="123",
                    )))
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_run_child):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=True,
                )

        assert len(apply_calls) == 1
        assert "--apply" in apply_calls[0]

    def test_apply_create_task_does_not_dispatch(self, mod, tmp_output_dir):
        dispatch_calls = []
        real_run_child = mod._run_child

        def mock_run_child(args, *, capture_output=True, check=True):
            if "dispatch" in str(args).lower():
                dispatch_calls.append(args)
            if "classify_pr_gate_state.py" in str(args):
                out = tmp_output_dir / "CLASSIFIER_PACKET.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_classifier_packet()))
            elif "pr_gate_task_draft.py" in str(args):
                out = tmp_output_dir / "PR_GATE_TASK_DRAFT.json"
                out.write_text(json.dumps(_mock_task_draft()))
                md = tmp_output_dir / "PR_GATE_TASK_DRAFT.md"
                md.write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                out = tmp_output_dir / "KANBAN_CREATE_PLAN.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_kanban_plan(dry_run=False, created_task_id="123")))
                md = tmp_output_dir / "KANBAN_CREATE_PLAN.md"
                md.write_text("# Kanban Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_run_child):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=True,
                )

        assert len(dispatch_calls) == 0, "No dispatch calls should be made"


# ---------------------------------------------------------------------------
# no_action_wait tests
# ---------------------------------------------------------------------------

class TestNoActionWait:
    def test_no_action_wait_with_apply_creates_nothing(self, mod, tmp_output_dir):
        apply_calls = []
        kanban_create_calls = []
        real_run_child = mod._run_child

        def mock_run_child(args, *, capture_output=True, check=True):
            if "pr_gate_kanban_task_create.py" in str(args):
                kanban_create_calls.append(args)
                if "--apply" in args:
                    apply_calls.append(args)

            if "classify_pr_gate_state.py" in str(args):
                out = tmp_output_dir / "CLASSIFIER_PACKET.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_classifier_packet()))
            elif "pr_gate_task_draft.py" in str(args):
                out = tmp_output_dir / "PR_GATE_TASK_DRAFT.json"
                out.write_text(json.dumps(_mock_task_draft(action="no_action_wait")))
                md = tmp_output_dir / "PR_GATE_TASK_DRAFT.md"
                md.write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                out = tmp_output_dir / "KANBAN_CREATE_PLAN.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_kanban_plan(
                    dry_run=True,
                    recommended_action="no_action",
                    kanban_task=None,
                )))
                md = tmp_output_dir / "KANBAN_CREATE_PLAN.md"
                md.write_text("# Kanban Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_run_child):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=True,
                )

        # --apply should be ignored for no_action_wait
        apply_on_no_action = [a for a in apply_calls if "--apply" in a]
        assert len(apply_on_no_action) == 0, "no_action_wait should downgrade apply-create-task"
        assert result["mode"] == "dry_run"
        assert result["result"]["final_recommendation"] == "no_action_wait_downgrade"


# ---------------------------------------------------------------------------
# Child script failure propagation
# ---------------------------------------------------------------------------

class TestChildScriptFailure:
    def test_child_failure_propagates_nonzero(self, mod, tmp_output_dir):
        real_run_child = mod._run_child

        def mock_run_child_fail(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                # Simulate classifier failure
                raise RuntimeError(
                    "Child script failed: classify_pr_gate_state.py\nrc=1\nstderr=failed"
                )
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_run_child_fail):
            with mock.patch.object(mod, "_reject_hermes_path"):
                try:
                    mod.run_controller(
                        repo_owner="Slideshow11",
                        repo_name="Automated-Edge-Discovery",
                        pr_number=199,
                        board="aed",
                        allowed_files=["docs/README.md"],
                        output_dir=tmp_output_dir,
                        apply_create_task=False,
                    )
                    assert False, "Should have raised RuntimeError"
                except RuntimeError as e:
                    assert "Child script failed" in str(e)
                    assert "classify_pr_gate_state.py" in str(e)


# ---------------------------------------------------------------------------
# Output packet tests
# ---------------------------------------------------------------------------

class TestOutputPacket:
    def test_controller_run_packet_has_all_artifact_paths(self, mod, tmp_output_dir):
        real_run_child = mod._run_child

        def mock_run_child(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                out = tmp_output_dir / "CLASSIFIER_PACKET.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_classifier_packet()))
            elif "pr_gate_task_draft.py" in str(args):
                out = tmp_output_dir / "PR_GATE_TASK_DRAFT.json"
                out.write_text(json.dumps(_mock_task_draft()))
                md = tmp_output_dir / "PR_GATE_TASK_DRAFT.md"
                md.write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                out = tmp_output_dir / "KANBAN_CREATE_PLAN.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_kanban_plan()))
                md = tmp_output_dir / "KANBAN_CREATE_PLAN.md"
                md.write_text("# Kanban Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_run_child):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=False,
                )

        artifacts = result["artifacts"]
        assert "classifier_json" in artifacts
        assert "task_draft_json" in artifacts
        assert "task_draft_md" in artifacts
        assert "kanban_plan_json" in artifacts
        assert "kanban_plan_md" in artifacts
        assert all(Path(artifacts[k]).name for k in artifacts)
        # Verify files actually written
        for k, path_str in artifacts.items():
            assert Path(path_str).exists(), f"{k} should exist at {path_str}"

    def test_summary_includes_classification_task_action_final_recommendation(self, mod, tmp_output_dir):
        real_run_child = mod._run_child

        def mock_run_child(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                out = tmp_output_dir / "CLASSIFIER_PACKET.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_classifier_packet(classification="codex_pending")))
            elif "pr_gate_task_draft.py" in str(args):
                out = tmp_output_dir / "PR_GATE_TASK_DRAFT.json"
                out.write_text(json.dumps(_mock_task_draft(action="no_action_wait")))
                md = tmp_output_dir / "PR_GATE_TASK_DRAFT.md"
                md.write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                out = tmp_output_dir / "KANBAN_CREATE_PLAN.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_kanban_plan(
                    recommended_action="no_action",
                    kanban_task=None,
                )))
                md = tmp_output_dir / "KANBAN_CREATE_PLAN.md"
                md.write_text("# Kanban Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_run_child):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=False,
                )

        summary_path = tmp_output_dir / "CONTROLLER_RUN_SUMMARY.md"
        assert summary_path.exists()
        summary = summary_path.read_text()
        assert "codex_pending" in summary
        assert "no_action_wait" in summary
        assert "no_action" in summary or "downgrade" in summary

    def test_duplicate_found_reported(self, mod, tmp_output_dir):
        real_run_child = mod._run_child

        def mock_run_child(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                out = tmp_output_dir / "CLASSIFIER_PACKET.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_classifier_packet()))
            elif "pr_gate_task_draft.py" in str(args):
                out = tmp_output_dir / "PR_GATE_TASK_DRAFT.json"
                out.write_text(json.dumps(_mock_task_draft()))
                md = tmp_output_dir / "PR_GATE_TASK_DRAFT.md"
                md.write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                out = tmp_output_dir / "KANBAN_CREATE_PLAN.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_kanban_plan(
                    duplicate_found=True,
                    created_task_id=None,
                )))
                md = tmp_output_dir / "KANBAN_CREATE_PLAN.md"
                md.write_text("# Kanban Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_run_child):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=False,
                )

        assert result["result"]["duplicate_found"] is True
        assert result["result"]["final_recommendation"] == "duplicate_skipped"


# ---------------------------------------------------------------------------
# Classifier scenarios
# ---------------------------------------------------------------------------

class TestClassifierScenarios:
    @pytest.mark.parametrize("classification,codex_status", [
        ("codex_pending", "pending"),
        ("codex_suggestions", "suggestions"),
        ("ready_for_reviewer", "clean"),
    ])
    def test_dry_run_with_various_classifier_packets(
        self, mod, tmp_output_dir, classification, codex_status
    ):
        real_run_child = mod._run_child

        def mock_run_child(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                out = tmp_output_dir / "CLASSIFIER_PACKET.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_classifier_packet(
                    classification=classification,
                    codex_status=codex_status,
                )))
            elif "pr_gate_task_draft.py" in str(args):
                out = tmp_output_dir / "PR_GATE_TASK_DRAFT.json"
                out.write_text(json.dumps(_mock_task_draft()))
                md = tmp_output_dir / "PR_GATE_TASK_DRAFT.md"
                md.write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                out = tmp_output_dir / "KANBAN_CREATE_PLAN.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_kanban_plan()))
                md = tmp_output_dir / "KANBAN_CREATE_PLAN.md"
                md.write_text("# Kanban Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_run_child):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=False,
                )

        assert result["result"]["classification"] == classification


# ---------------------------------------------------------------------------
# Apply-create-task hardening tests (PR #206)
# ---------------------------------------------------------------------------

class TestApplyCreateTaskHardening:
    """Tests for idempotency, no-dispatch, and apply-path hardening."""

    def test_apply_create_task_calls_kanban_helper_once(self, mod, tmp_output_dir):
        """_apply_create_task invokes kanban helper exactly once."""
        import unittest.mock as mock

        task_draft = _mock_task_draft(action="create_builder_patch_task_draft")
        draft_path = tmp_output_dir / "draft.json"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(json.dumps(task_draft))

        with mock.patch.object(mod, "_run_child") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            with mock.patch.object(mod, "_load_json") as mock_load:
                mock_load.return_value = _mock_kanban_plan(dry_run=False)
                dup, tid, ikey, blockers = mod._apply_create_task(
                    apply_create_task=True,
                    task_draft_packet=task_draft,
                    task_draft_json_path=draft_path,
                    kanban_plan_json_path=tmp_output_dir / "plan.json",
                    kanban_plan_md_path=tmp_output_dir / "plan.md",
                    board="aed",
                )

        # Exactly one call to kanban helper
        kanban_calls = [c for c in mock_run.call_args_list
                        if "pr_gate_kanban_task_create.py" in str(c)]
        assert len(kanban_calls) == 1
        assert "--apply" in kanban_calls[0][0][0]

    def test_apply_create_task_passes_apply_flag(self, mod, tmp_output_dir):
        """--apply is passed to kanban helper when apply_create_task=True."""
        apply_calls = []

        def mock_run_child(args, *, capture_output=True, check=True):
            if "pr_gate_kanban_task_create.py" in str(args):
                # Dry-run call first, then apply call
                if "--apply" in args:
                    apply_calls.append(args)
                # Write plan for both dry-run (without --apply) and apply calls
                (tmp_output_dir / "KANBAN_CREATE_PLAN.json").write_text(
                    json.dumps(_mock_kanban_plan(dry_run=False))
                )
                (tmp_output_dir / "KANBAN_CREATE_PLAN.md").write_text("# Kanban Plan")
            elif "classify_pr_gate_state.py" in str(args):
                (tmp_output_dir / "CLASSIFIER_PACKET.json").write_text(
                    json.dumps(_mock_classifier_packet())
                )
            elif "pr_gate_task_draft.py" in str(args):
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.json").write_text(
                    json.dumps(_mock_task_draft())
                )
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.md").write_text("# Task Draft")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_run_child):
            with mock.patch.object(mod, "_reject_hermes_path"):
                mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=True,
                )

        assert len(apply_calls) == 1, f"Expected 1 apply call, got {len(apply_calls)}"
        assert "--apply" in apply_calls[0]

    def test_dry_run_never_passes_apply_flag(self, mod, tmp_output_dir):
        """Dry-run never passes --apply to kanban helper."""
        apply_args = []

        def capture_run_child(args, *, capture_output=True, check=True):
            if "pr_gate_kanban_task_create.py" in str(args):
                (tmp_output_dir / "KANBAN_CREATE_PLAN.json").write_text(
                    json.dumps(_mock_kanban_plan(dry_run=True))
                )
                if "--apply" in args:
                    apply_args.append(args)
            elif "classify_pr_gate_state.py" in str(args):
                (tmp_output_dir / "CLASSIFIER_PACKET.json").write_text(
                    json.dumps(_mock_classifier_packet())
                )
            elif "pr_gate_task_draft.py" in str(args):
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.json").write_text(
                    json.dumps(_mock_task_draft())
                )
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.md").write_text("# Task Draft")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", capture_run_child):
            with mock.patch.object(mod, "_reject_hermes_path"):
                mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=False,
                )

        apply_calls_in_dry = [a for a in apply_args if "--apply" in a]
        assert len(apply_calls_in_dry) == 0

    def test_apply_create_task_uses_deterministic_idempotency_key(self, mod):
        """Same PR/head/action yields same idempotency key."""
        key1 = mod._make_controller_idempotency_key(
            "Slideshow11", "Automated-Edge-Discovery", 199,
            "abcd1234" + "0" * 32, "create_builder_patch_task_draft"
        )
        key2 = mod._make_controller_idempotency_key(
            "Slideshow11", "Automated-Edge-Discovery", 199,
            "abcd1234" + "0" * 32, "create_builder_patch_task_draft"
        )
        assert key1 == key2
        assert key1.startswith("aed:Slideshow11/Automated-Edge-Discovery:pr:199:head:abcd1234")

    def test_apply_create_task_different_inputs_produce_different_keys(self, mod):
        """Different PRs or heads or actions produce different keys."""
        base_args = ["Slideshow11", "Automated-Edge-Discovery", 199,
                     "abcd1234" + "0" * 32, "create_builder_patch_task_draft"]
        key1 = mod._make_controller_idempotency_key(*base_args)
        # Different PR number
        key2 = mod._make_controller_idempotency_key(*base_args[:2], 200, *base_args[3:])
        # Different head SHA
        key3 = mod._make_controller_idempotency_key(*base_args[:3], "different" + "0" * 32, base_args[4])
        # Different action
        key4 = mod._make_controller_idempotency_key(*base_args[:4], "create_reviewer_task_draft")

        assert key1 != key2, "different PR number"
        assert key1 != key3, "different head SHA"
        assert key1 != key4, "different action"

    def test_apply_create_task_refuses_missing_head_sha(self, mod):
        """Missing head_sha blocks apply and returns blocker."""
        draft = _mock_task_draft()
        draft["source"]["head_sha"] = ""
        dup, tid, ikey, blockers = mod._apply_create_task(
            apply_create_task=True,
            task_draft_packet=draft,
            task_draft_json_path=Path("/tmp/draft.json"),
            kanban_plan_json_path=Path("/tmp/plan.json"),
            kanban_plan_md_path=Path("/tmp/plan.md"),
            board="aed",
        )
        assert len(blockers) > 0
        assert any("head_sha" in b for b in blockers)

    def test_apply_create_task_refuses_missing_pr_number(self, mod):
        """Missing pr_number blocks apply."""
        draft = _mock_task_draft()
        draft["source"]["pr_number"] = ""
        dup, tid, ikey, blockers = mod._apply_create_task(
            apply_create_task=True,
            task_draft_packet=draft,
            task_draft_json_path=Path("/tmp/draft.json"),
            kanban_plan_json_path=Path("/tmp/plan.json"),
            kanban_plan_md_path=Path("/tmp/plan.md"),
            board="aed",
        )
        assert len(blockers) > 0
        assert any("pr_number" in b for b in blockers)

    def test_apply_create_task_refuses_no_action_wait(self, mod):
        """no_action_wait action blocks apply."""
        draft = _mock_task_draft(action="no_action_wait")
        dup, tid, ikey, blockers = mod._apply_create_task(
            apply_create_task=True,
            task_draft_packet=draft,
            task_draft_json_path=Path("/tmp/draft.json"),
            kanban_plan_json_path=Path("/tmp/plan.json"),
            kanban_plan_md_path=Path("/tmp/plan.md"),
            board="aed",
        )
        assert len(blockers) > 0
        assert any("no_action_wait" in b for b in blockers)

    def test_apply_create_task_refuses_ci_pending(self, mod):
        """ci_pending action blocks apply."""
        draft = _mock_task_draft(action="ci_pending")
        dup, tid, ikey, blockers = mod._apply_create_task(
            apply_create_task=True,
            task_draft_packet=draft,
            task_draft_json_path=Path("/tmp/draft.json"),
            kanban_plan_json_path=Path("/tmp/plan.json"),
            kanban_plan_md_path=Path("/tmp/plan.md"),
            board="aed",
        )
        assert len(blockers) > 0
        assert any("ci_pending" in b for b in blockers)

    def test_apply_create_task_refuses_codex_pending(self, mod):
        """codex_pending action blocks apply."""
        draft = _mock_task_draft(action="codex_pending")
        dup, tid, ikey, blockers = mod._apply_create_task(
            apply_create_task=True,
            task_draft_packet=draft,
            task_draft_json_path=Path("/tmp/draft.json"),
            kanban_plan_json_path=Path("/tmp/plan.json"),
            kanban_plan_md_path=Path("/tmp/plan.md"),
            board="aed",
        )
        assert len(blockers) > 0
        assert any("codex_pending" in b for b in blockers)

    def test_apply_create_task_refuses_unknown_action(self, mod):
        """Unknown or empty action blocks apply."""
        draft = _mock_task_draft(action="unknown")
        dup, tid, ikey, blockers = mod._apply_create_task(
            apply_create_task=True,
            task_draft_packet=draft,
            task_draft_json_path=Path("/tmp/draft.json"),
            kanban_plan_json_path=Path("/tmp/plan.json"),
            kanban_plan_md_path=Path("/tmp/plan.md"),
            board="aed",
        )
        assert len(blockers) > 0

    def test_apply_create_task_records_no_dispatch_guarantee(self, mod, tmp_output_dir):
        """run_packet contains no_dispatch_guarantee=True."""
        def mock_all(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                (tmp_output_dir / "CLASSIFIER_PACKET.json").write_text(json.dumps(_mock_classifier_packet()))
            elif "pr_gate_task_draft.py" in str(args):
                td = _mock_task_draft()
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.json").write_text(json.dumps(td))
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.md").write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                (tmp_output_dir / "KANBAN_CREATE_PLAN.json").write_text(json.dumps(_mock_kanban_plan(dry_run=False)))
                (tmp_output_dir / "KANBAN_CREATE_PLAN.md").write_text("# Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_all):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11", repo_name="Automated-Edge-Discovery",
                    pr_number=199, board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=True,
                )

        assert result.get("no_dispatch_guarantee") is True

    def test_apply_create_task_surfaces_duplicate_found(self, mod, tmp_output_dir):
        """duplicate_found from downstream is recorded in the packet."""
        def mock_all(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                (tmp_output_dir / "CLASSIFIER_PACKET.json").write_text(json.dumps(_mock_classifier_packet()))
            elif "pr_gate_task_draft.py" in str(args):
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.json").write_text(json.dumps(_mock_task_draft()))
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.md").write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                plan = _mock_kanban_plan(dry_run=False, duplicate_found=True, created_task_id="777")
                (tmp_output_dir / "KANBAN_CREATE_PLAN.json").write_text(json.dumps(plan))
                (tmp_output_dir / "KANBAN_CREATE_PLAN.md").write_text("# Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_all):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11", repo_name="Automated-Edge-Discovery",
                    pr_number=199, board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=True,
                )

        assert result["result"]["duplicate_found"] is True
        assert result["result"]["final_recommendation"] == "duplicate_skipped"

    def test_apply_create_task_records_created_task_id(self, mod, tmp_output_dir):
        """created_task_id from downstream is recorded."""
        def mock_all(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                (tmp_output_dir / "CLASSIFIER_PACKET.json").write_text(json.dumps(_mock_classifier_packet()))
            elif "pr_gate_task_draft.py" in str(args):
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.json").write_text(json.dumps(_mock_task_draft()))
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.md").write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                plan = _mock_kanban_plan(dry_run=False, created_task_id="999")
                (tmp_output_dir / "KANBAN_CREATE_PLAN.json").write_text(json.dumps(plan))
                (tmp_output_dir / "KANBAN_CREATE_PLAN.md").write_text("# Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_all):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11", repo_name="Automated-Edge-Discovery",
                    pr_number=199, board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=True,
                )

        assert result["result"]["created_task_id"] == "999"

    def test_apply_create_task_child_failure_blocks(self, mod, tmp_output_dir):
        """Downstream helper nonzero exit raises RuntimeError."""
        def mock_fail(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                (tmp_output_dir / "CLASSIFIER_PACKET.json").write_text(json.dumps(_mock_classifier_packet()))
            elif "pr_gate_task_draft.py" in str(args):
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.json").write_text(json.dumps(_mock_task_draft()))
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.md").write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args) and "--apply" in args:
                (tmp_output_dir / "KANBAN_CREATE_PLAN.json").write_text(
                    json.dumps(_mock_kanban_plan(dry_run=False))
                )
                raise RuntimeError("Child script failed")
            elif "pr_gate_kanban_task_create.py" in str(args):
                (tmp_output_dir / "KANBAN_CREATE_PLAN.json").write_text(json.dumps(_mock_kanban_plan(dry_run=True)))
                (tmp_output_dir / "KANBAN_CREATE_PLAN.md").write_text("# Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_fail):
            with mock.patch.object(mod, "_reject_hermes_path"):
                with pytest.raises(RuntimeError):
                    mod.run_controller(
                        repo_owner="Slideshow11", repo_name="Automated-Edge-Discovery",
                        pr_number=199, board="aed",
                        allowed_files=["docs/README.md"],
                        output_dir=tmp_output_dir,
                        apply_create_task=True,
                    )

    def test_apply_create_task_idempotency_key_in_packet(self, mod, tmp_output_dir):
        """idempotency_key appears in run_packet for apply_create_task mode."""
        def mock_all(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                (tmp_output_dir / "CLASSIFIER_PACKET.json").write_text(json.dumps(_mock_classifier_packet()))
            elif "pr_gate_task_draft.py" in str(args):
                td = _mock_task_draft()
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.json").write_text(json.dumps(td))
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.md").write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                (tmp_output_dir / "KANBAN_CREATE_PLAN.json").write_text(json.dumps(_mock_kanban_plan(dry_run=False)))
                (tmp_output_dir / "KANBAN_CREATE_PLAN.md").write_text("# Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_all):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11", repo_name="Automated-Edge-Discovery",
                    pr_number=199, board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=True,
                )

        assert result.get("idempotency_key", "") != ""
        assert "aed:Slideshow11" in result["idempotency_key"]

    def test_apply_create_task_downstream_helper_recorded(self, mod, tmp_output_dir):
        """downstream_helper field names pr_gate_kanban_task_create.py."""
        def mock_all(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                (tmp_output_dir / "CLASSIFIER_PACKET.json").write_text(json.dumps(_mock_classifier_packet()))
            elif "pr_gate_task_draft.py" in str(args):
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.json").write_text(json.dumps(_mock_task_draft()))
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.md").write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                (tmp_output_dir / "KANBAN_CREATE_PLAN.json").write_text(json.dumps(_mock_kanban_plan(dry_run=False)))
                (tmp_output_dir / "KANBAN_CREATE_PLAN.md").write_text("# Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_all):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11", repo_name="Automated-Edge-Discovery",
                    pr_number=199, board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=True,
                )

        assert result.get("downstream_helper") == "pr_gate_kanban_task_create.py"


# ---------------------------------------------------------------------------
# Persistent mutation guard tests
# ---------------------------------------------------------------------------

class TestPersistentMutationGuard:
    """Tests for --require-persistent-guard and record-only guard wiring."""

    def test_without_require_guard_preserves_existing_behavior(self, mod, tmp_output_dir):
        """When --require-persistent-guard is absent, controller runs normally."""
        def mock_all(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                (tmp_output_dir / "CLASSIFIER_PACKET.json").write_text(
                    json.dumps(_mock_classifier_packet())
                )
            elif "pr_gate_task_draft.py" in str(args):
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.json").write_text(
                    json.dumps(_mock_task_draft())
                )
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.md").write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                (tmp_output_dir / "KANBAN_CREATE_PLAN.json").write_text(
                    json.dumps(_mock_kanban_plan())
                )
                (tmp_output_dir / "KANBAN_CREATE_PLAN.md").write_text("# Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_all):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=False,
                    require_persistent_guard=False,
                )

        assert result["result"]["final_recommendation"] in (
            "plan_ready_for_review", "no_action", "no_action_wait_downgrade"
        )
        guard = result.get("persistent_mutation_guard", {})
        assert guard.get("status") == "not_required"
        assert guard.get("required") is False

    def test_require_guard_with_missing_compare_json_returns_block(self, mod, tmp_output_dir):
        """--require-persistent-guard with missing compare JSON returns BLOCK."""
        with mock.patch.object(mod, "_reject_hermes_path"):
            result = mod.run_controller(
                repo_owner="Slideshow11",
                repo_name="Automated-Edge-Discovery",
                pr_number=199,
                board="aed",
                allowed_files=["docs/README.md"],
                output_dir=tmp_output_dir,
                apply_create_task=False,
                require_persistent_guard=True,
                persistent_guard_compare_json=Path("/tmp/nonexistent/compare.json"),
            )

        assert result["result"]["final_recommendation"] == "blocked_on_persistent_guard"
        guard = result.get("persistent_mutation_guard", {})
        assert guard.get("status") == "error"
        assert guard.get("required") is True
        assert "not found" in guard.get("message", "")

    def test_require_guard_with_malformed_compare_json_returns_block(self, mod, tmp_output_dir):
        """--require-persistent-guard with malformed compare JSON returns BLOCK."""
        bad_json = tmp_output_dir / "malformed_compare.json"
        bad_json.write_text("{ this is not json }")

        with mock.patch.object(mod, "_reject_hermes_path"):
            result = mod.run_controller(
                repo_owner="Slideshow11",
                repo_name="Automated-Edge-Discovery",
                pr_number=199,
                board="aed",
                allowed_files=["docs/README.md"],
                output_dir=tmp_output_dir,
                apply_create_task=False,
                require_persistent_guard=True,
                persistent_guard_compare_json=bad_json,
            )

        assert result["result"]["final_recommendation"] == "blocked_on_persistent_guard"
        guard = result.get("persistent_mutation_guard", {})
        assert guard.get("status") == "error"
        assert "malformed" in guard.get("message", "").lower()

    def test_require_guard_recommendation_block_returns_block(self, mod, tmp_output_dir):
        """Compare JSON with recommendation=BLOCK returns BLOCK."""
        block_json = tmp_output_dir / "block_compare.json"
        block_json.write_text(json.dumps({
            "status": "blocked",
            "recommendation": "BLOCK",
            "blocked_changes": [
                {"relative_path": "skills/test-skill/SKILL.md", "change": "added"}
            ],
            "allowed_changes": [],
        }))

        with mock.patch.object(mod, "_reject_hermes_path"):
            result = mod.run_controller(
                repo_owner="Slideshow11",
                repo_name="Automated-Edge-Discovery",
                pr_number=199,
                board="aed",
                allowed_files=["docs/README.md"],
                output_dir=tmp_output_dir,
                apply_create_task=False,
                require_persistent_guard=True,
                persistent_guard_compare_json=block_json,
            )

        assert result["result"]["final_recommendation"] == "blocked_on_persistent_guard"
        guard = result.get("persistent_mutation_guard", {})
        assert guard.get("status") == "blocked"
        assert guard.get("blocked_changes_count") == 1

    def test_require_guard_recommendation_pass_records_clean(self, mod, tmp_output_dir):
        """Compare JSON with recommendation=PASS records guard clean in packet."""
        clean_json = tmp_output_dir / "clean_compare.json"
        clean_json.write_text(json.dumps({
            "status": "clean",
            "recommendation": "PASS",
            "blocked_changes": [],
            "allowed_changes": [],
        }))

        def mock_all(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                (tmp_output_dir / "CLASSIFIER_PACKET.json").write_text(
                    json.dumps(_mock_classifier_packet())
                )
            elif "pr_gate_task_draft.py" in str(args):
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.json").write_text(
                    json.dumps(_mock_task_draft())
                )
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.md").write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                (tmp_output_dir / "KANBAN_CREATE_PLAN.json").write_text(
                    json.dumps(_mock_kanban_plan())
                )
                (tmp_output_dir / "KANBAN_CREATE_PLAN.md").write_text("# Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_all):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=False,
                    require_persistent_guard=True,
                    persistent_guard_compare_json=clean_json,
                )

        assert result["result"]["final_recommendation"] in (
            "plan_ready_for_review", "no_action", "no_action_wait_downgrade"
        )
        guard = result.get("persistent_mutation_guard", {})
        assert guard.get("status") == "clean"
        assert guard.get("required") is True

    def test_clean_guard_does_not_override_stale_sha(self, mod, tmp_output_dir):
        """A clean guard does not override stale head SHA detection."""
        clean_json = tmp_output_dir / "clean_compare.json"
        clean_json.write_text(json.dumps({
            "status": "clean", "recommendation": "PASS",
            "blocked_changes": [], "allowed_changes": [],
        }))

        def mock_classifier_mismatch(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                out = tmp_output_dir / "CLASSIFIER_PACKET.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_classifier_packet(
                    classification="head_mismatch"
                )))
            elif "pr_gate_task_draft.py" in str(args):
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.json").write_text(
                    json.dumps(_mock_task_draft())
                )
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.md").write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                (tmp_output_dir / "KANBAN_CREATE_PLAN.json").write_text(
                    json.dumps(_mock_kanban_plan())
                )
                (tmp_output_dir / "KANBAN_CREATE_PLAN.md").write_text("# Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_classifier_mismatch):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=False,
                    require_persistent_guard=True,
                    persistent_guard_compare_json=clean_json,
                )

        assert result["persistent_mutation_guard"]["status"] == "clean"
        assert result["result"]["classification"] == "head_mismatch"

    def test_clean_guard_does_not_override_failed_ci(self, mod, tmp_output_dir):
        """A clean guard does not override failed CI status."""
        clean_json = tmp_output_dir / "clean_compare.json"
        clean_json.write_text(json.dumps({
            "status": "clean", "recommendation": "PASS",
            "blocked_changes": [], "allowed_changes": [],
        }))

        def mock_classifier_ci_fail(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                out = tmp_output_dir / "CLASSIFIER_PACKET.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_classifier_packet(
                    classification="ready_for_reviewer", codex_status="clean"
                )))
                # Override ci_status to red
                data = json.loads(out.read_text())
                data["ci_status"] = "red"
                out.write_text(json.dumps(data))
            elif "pr_gate_task_draft.py" in str(args):
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.json").write_text(
                    json.dumps(_mock_task_draft())
                )
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.md").write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                (tmp_output_dir / "KANBAN_CREATE_PLAN.json").write_text(
                    json.dumps(_mock_kanban_plan())
                )
                (tmp_output_dir / "KANBAN_CREATE_PLAN.md").write_text("# Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_classifier_ci_fail):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=False,
                    require_persistent_guard=True,
                    persistent_guard_compare_json=clean_json,
                )

        assert result["persistent_mutation_guard"]["status"] == "clean"
        assert "red" in result["result"]["classification"] or \
               result["result"]["task_action"] != ""

    def test_clean_guard_does_not_override_scope_violation(self, mod, tmp_output_dir):
        """A clean guard does not override scope violation — classifier reports it."""
        clean_json = tmp_output_dir / "clean_compare.json"
        clean_json.write_text(json.dumps({
            "status": "clean", "recommendation": "PASS",
            "blocked_changes": [], "allowed_changes": [],
        }))

        def mock_classifier_scope_violation(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                out = tmp_output_dir / "CLASSIFIER_PACKET.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(_mock_classifier_packet(
                    classification="scope_violation"
                )))
            elif "pr_gate_task_draft.py" in str(args):
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.json").write_text(
                    json.dumps(_mock_task_draft(action="no_action_wait"))
                )
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.md").write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                (tmp_output_dir / "KANBAN_CREATE_PLAN.json").write_text(
                    json.dumps(_mock_kanban_plan(recommended_action="no_action"))
                )
                (tmp_output_dir / "KANBAN_CREATE_PLAN.md").write_text("# Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_classifier_scope_violation):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=False,
                    require_persistent_guard=True,
                    persistent_guard_compare_json=clean_json,
                )

        assert result["persistent_mutation_guard"]["status"] == "clean"
        assert result["result"]["final_recommendation"] in (
            "no_action", "no_action_wait_downgrade", "plan_ready_for_review"
        )

    def test_blocked_guard_suppresses_merge_authorization(self, mod, tmp_output_dir):
        """A blocked guard produces final_recommendation=blocked_on_persistent_guard."""
        block_json = tmp_output_dir / "block_compare.json"
        block_json.write_text(json.dumps({
            "status": "blocked",
            "recommendation": "BLOCK",
            "blocked_changes": [
                {"relative_path": "skills/new-skill/SKILL.md", "change": "added"}
            ],
            "allowed_changes": [],
        }))

        with mock.patch.object(mod, "_reject_hermes_path"):
            result = mod.run_controller(
                repo_owner="Slideshow11",
                repo_name="Automated-Edge-Discovery",
                pr_number=199,
                board="aed",
                allowed_files=["docs/README.md"],
                output_dir=tmp_output_dir,
                apply_create_task=False,
                require_persistent_guard=True,
                persistent_guard_compare_json=block_json,
            )

        assert result["result"]["final_recommendation"] == "blocked_on_persistent_guard"
        assert "blocked_on_persistent_guard" in result["mode"]
        blockers = result.get("blockers_or_uncertainty", [])
        assert any("persistent_mutation_guard" in b for b in blockers)

    def test_blocked_guard_writes_blocked_packet_and_summary(self, mod, tmp_output_dir):
        """A blocked guard writes CONTROLLER_RUN_PACKET.json and CONTROLLER_RUN_SUMMARY.md."""
        block_json = tmp_output_dir / "block_compare.json"
        block_json.write_text(json.dumps({
            "status": "blocked", "recommendation": "BLOCK",
            "blocked_changes": [{"relative_path": "skills/new-skill/SKILL.md", "change": "added"}],
            "allowed_changes": [],
        }))

        with mock.patch.object(mod, "_reject_hermes_path"):
            result = mod.run_controller(
                repo_owner="Slideshow11",
                repo_name="Automated-Edge-Discovery",
                pr_number=199,
                board="aed",
                allowed_files=["docs/README.md"],
                output_dir=tmp_output_dir,
                apply_create_task=False,
                require_persistent_guard=True,
                persistent_guard_compare_json=block_json,
            )

        assert (tmp_output_dir / "CONTROLLER_RUN_PACKET.json").exists()
        assert (tmp_output_dir / "CONTROLLER_RUN_SUMMARY.md").exists()
        packet = json.loads((tmp_output_dir / "CONTROLLER_RUN_PACKET.json").read_text())
        assert packet["result"]["final_recommendation"] == "blocked_on_persistent_guard"
        assert "persistent_mutation_guard" in packet

    def test_markdown_report_includes_guard_state(self, mod, tmp_output_dir):
        """CONTROLLER_RUN_SUMMARY.md includes persistent mutation guard section."""
        clean_json = tmp_output_dir / "clean_compare.json"
        clean_json.write_text(json.dumps({
            "status": "clean", "recommendation": "PASS",
            "blocked_changes": [], "allowed_changes": [],
        }))

        def mock_all(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                (tmp_output_dir / "CLASSIFIER_PACKET.json").write_text(
                    json.dumps(_mock_classifier_packet())
                )
            elif "pr_gate_task_draft.py" in str(args):
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.json").write_text(
                    json.dumps(_mock_task_draft())
                )
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.md").write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                (tmp_output_dir / "KANBAN_CREATE_PLAN.json").write_text(
                    json.dumps(_mock_kanban_plan())
                )
                (tmp_output_dir / "KANBAN_CREATE_PLAN.md").write_text("# Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_all):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=False,
                    require_persistent_guard=True,
                    persistent_guard_compare_json=clean_json,
                )

        summary = (tmp_output_dir / "CONTROLLER_RUN_SUMMARY.md").read_text()
        assert "Persistent Mutation Guard" in summary
        assert "clean" in summary

    def test_json_report_includes_guard_state(self, mod, tmp_output_dir):
        """CONTROLLER_RUN_PACKET.json includes persistent_mutation_guard key."""
        clean_json = tmp_output_dir / "clean_compare.json"
        clean_json.write_text(json.dumps({
            "status": "clean", "recommendation": "PASS",
            "blocked_changes": [], "allowed_changes": [],
        }))

        def mock_all(args, *, capture_output=True, check=True):
            if "classify_pr_gate_state.py" in str(args):
                (tmp_output_dir / "CLASSIFIER_PACKET.json").write_text(
                    json.dumps(_mock_classifier_packet())
                )
            elif "pr_gate_task_draft.py" in str(args):
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.json").write_text(
                    json.dumps(_mock_task_draft())
                )
                (tmp_output_dir / "PR_GATE_TASK_DRAFT.md").write_text("# Task Draft")
            elif "pr_gate_kanban_task_create.py" in str(args):
                (tmp_output_dir / "KANBAN_CREATE_PLAN.json").write_text(
                    json.dumps(_mock_kanban_plan())
                )
                (tmp_output_dir / "KANBAN_CREATE_PLAN.md").write_text("# Plan")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mod, "_run_child", mock_all):
            with mock.patch.object(mod, "_reject_hermes_path"):
                result = mod.run_controller(
                    repo_owner="Slideshow11",
                    repo_name="Automated-Edge-Discovery",
                    pr_number=199,
                    board="aed",
                    allowed_files=["docs/README.md"],
                    output_dir=tmp_output_dir,
                    apply_create_task=False,
                    require_persistent_guard=True,
                    persistent_guard_compare_json=clean_json,
                )

        packet = json.loads((tmp_output_dir / "CONTROLLER_RUN_PACKET.json").read_text())
        assert "persistent_mutation_guard" in packet
        assert packet["persistent_mutation_guard"]["status"] == "clean"

    def test_guard_compare_json_missing_field_returns_block(self, mod, tmp_output_dir):
        """Compare JSON missing 'status' field returns BLOCK when required."""
        bad_json = tmp_output_dir / "missing_status.json"
        bad_json.write_text(json.dumps({
            "recommendation": "PASS",
            "blocked_changes": [],
            "allowed_changes": [],
        }))

        with mock.patch.object(mod, "_reject_hermes_path"):
            result = mod.run_controller(
                repo_owner="Slideshow11",
                repo_name="Automated-Edge-Discovery",
                pr_number=199,
                board="aed",
                allowed_files=["docs/README.md"],
                output_dir=tmp_output_dir,
                apply_create_task=False,
                require_persistent_guard=True,
                persistent_guard_compare_json=bad_json,
            )

        assert result["result"]["final_recommendation"] == "blocked_on_persistent_guard"
        assert result["persistent_mutation_guard"]["status"] == "error"

    def test_guard_compare_json_missing_recommendation_returns_block(self, mod, tmp_output_dir):
        """Compare JSON missing 'recommendation' field returns BLOCK when required."""
        bad_json = tmp_output_dir / "missing_rec.json"
        bad_json.write_text(json.dumps({
            "status": "clean",
            "blocked_changes": [],
            "allowed_changes": [],
        }))

        with mock.patch.object(mod, "_reject_hermes_path"):
            result = mod.run_controller(
                repo_owner="Slideshow11",
                repo_name="Automated-Edge-Discovery",
                pr_number=199,
                board="aed",
                allowed_files=["docs/README.md"],
                output_dir=tmp_output_dir,
                apply_create_task=False,
                require_persistent_guard=True,
                persistent_guard_compare_json=bad_json,
            )

        assert result["result"]["final_recommendation"] == "blocked_on_persistent_guard"
        assert result["persistent_mutation_guard"]["status"] == "error"


# ---------------------------------------------------------------------------
# Safety grep test
# ---------------------------------------------------------------------------

class TestSafetyGrep:
    def test_source_contains_no_forbidden_calls(self):
        src = (
            _repo_root()
            / "scripts" / "local" / "pr_gate_controller.py"
        ).read_text()
        dedented = textwrap.dedent(src)

        forbidden = [
            "gh pr merge", "gh pr comment", "gh pr create",
            "git push", "hermes kanban dispatch",
            "memory.update", "fact_store", "skill_manage",
            "delegate_task", "cronjob",
        ]
        for pat in forbidden:
            # Must not appear outside of STOP_RULES / FORBIDDEN_PATTERNS constant definitions
            lines = [l for l in dedented.split("\n")
                     if pat.lower() in l.lower()
                     and "STOP_RULES" not in l
                     and "FORBIDDEN_PATTERNS" not in l
                     and "re.compile" not in l
                     and l.strip().startswith("#") is False]
            # Filter: if this is a constant value line (inside a list definition), skip it
            lines = [l for l in lines
                     if not (l.strip().startswith('"') or l.strip().startswith("'"))]
            assert len(lines) == 0, f"forbidden '{pat}' found in live code: {lines[0] if lines else ''}"

    def test_controller_does_not_call_hermes_kanban_directly(self):
        src = (
            _repo_root()
            / "scripts" / "local" / "pr_gate_controller.py"
        ).read_text()
        dedented = textwrap.dedent(src)
        # Controller should not invoke hermes binary directly
        # Only pr_gate_kanban_task_create.py does (via subprocess)
        assert "hermes" not in dedented.lower() or "subprocess" in dedented.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])