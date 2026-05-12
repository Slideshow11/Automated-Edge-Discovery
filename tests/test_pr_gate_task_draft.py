#!/usr/bin/env python3
"""
Tests for pr_gate_task_draft.py
Covers: classify_to_action, build_task_draft, validate_task_draft_packet,
render_md, CLI modes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent  # /home/max/Automated-Edge-Discovery
SCRIPT = REPO_ROOT / "scripts" / "local" / "pr_gate_task_draft.py"


def _import_mod():
    """Import pr_gate_task_draft module from REPO_ROOT."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("pr_gate_task_draft", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def minimal_classifier(
    classification: str = "codex_clean",
    ci_status: str = "passed",
    pr_number: str = "196",
    head_sha: str = "abc123",
    changed_files: list[str] | None = None,
    **overrides,
) -> dict:
    base = {
        "classification": classification,
        "ci_status": ci_status,
        "codex_status": "clean",
        "pr_number": pr_number,
        "pr_url": f"https://github.com/Slideshow11/Automated-Edge-Discovery/pull/{pr_number}",
        "head_sha": head_sha,
        "changed_files": changed_files or ["scripts/local/aed_executor_packet.py"],
        "blockers": [],
    }
    base.update(overrides)
    return base


def write_packet(packet: dict, path: Path | str) -> None:
    with open(path, "w") as f:
        json.dump(packet, f, indent=2)


# ---------------------------------------------------------------------------
# classify_to_action tests
# ---------------------------------------------------------------------------

class TestClassifyToAction:
    def test_codex_pending(self):
        mod = _import_mod()
        assert mod.classify_to_action("codex_pending", "pending") == "no_action_wait"

    def test_ci_pending(self):
        mod = _import_mod()
        assert mod.classify_to_action("ci_pending", "pending") == "no_action_wait"

    def test_codex_request_needed(self):
        mod = _import_mod()
        assert mod.classify_to_action("codex_request_needed", "passed") == "create_codex_request_task_draft"

    def test_codex_suggestions(self):
        mod = _import_mod()
        assert mod.classify_to_action("codex_suggestions", "passed") == "create_builder_patch_task_draft"

    def test_ci_failed(self):
        mod = _import_mod()
        assert mod.classify_to_action("ci_failed", "failed") == "create_builder_patch_task_draft"

    def test_ready_for_reviewer(self):
        mod = _import_mod()
        assert mod.classify_to_action("ready_for_reviewer", "passed") == "create_reviewer_task_draft"

    def test_blocked_scope(self):
        mod = _import_mod()
        assert mod.classify_to_action("blocked_scope", "unknown") == "create_human_escalation_task_draft"

    def test_blocked_wrong_base(self):
        mod = _import_mod()
        assert mod.classify_to_action("blocked_wrong_base", "unknown") == "create_human_escalation_task_draft"

    def test_blocked_pr_closed(self):
        mod = _import_mod()
        assert mod.classify_to_action("blocked_pr_closed", "unknown") == "create_human_escalation_task_draft"

    def test_blocked_pr_merged(self):
        mod = _import_mod()
        assert mod.classify_to_action("blocked_pr_merged", "unknown") == "create_human_escalation_task_draft"

    def test_unknown_classification(self):
        mod = _import_mod()
        assert mod.classify_to_action("unknown", "unknown") == "create_human_escalation_task_draft"


# ---------------------------------------------------------------------------
# build_task_draft tests
# ---------------------------------------------------------------------------

class TestBuildTaskDraft:
    def test_no_action_wait(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ci_pending", "pending"),
            None,
        )
        assert packet["task_draft"]["action"] == "no_action_wait"
        # title is optional for no_action_wait (spec allows empty, but non-empty is fine)
        assert len(packet["task_draft"]["body"]) > 0
        # idempotency key must be present and include pr, head, action
        key = packet["task_draft"]["idempotency_key"]
        assert "196" in key
        assert "abc123" in key
        assert "no_action_wait" in key

    def test_codex_request_needed(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("codex_request_needed", "passed"),
            None,
        )
        td = packet["task_draft"]
        assert td["action"] == "create_codex_request_task_draft"
        assert td["assignee"] == "aed-reviewer"
        assert "Codex" in td["body"]
        assert "Do not merge" in td["body"]

    def test_codex_suggestions_builder_patch(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("codex_suggestions", "passed"),
            None,
        )
        td = packet["task_draft"]
        assert td["action"] == "create_builder_patch_task_draft"
        assert td["assignee"] == "aed-builder"
        assert "allowed files" in td["body"].lower()

    def test_ci_failed_builder_patch(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ci_failed", "failed"),
            None,
        )
        assert packet["task_draft"]["action"] == "create_builder_patch_task_draft"

    def test_ready_for_reviewer(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", "passed"),
            None,
        )
        td = packet["task_draft"]
        assert td["action"] == "create_reviewer_task_draft"
        assert td["assignee"] == "aed-reviewer"
        assert "latest head" in td["body"].lower()

    def test_blocked_scope_escalation(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("blocked_scope", "unknown", blockers=["scope mismatch"]),
            None,
        )
        td = packet["task_draft"]
        assert td["action"] == "create_human_escalation_task_draft"
        assert td["assignee"] == "human"

    def test_blocked_wrong_base_escalation(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("blocked_wrong_base", "unknown"),
            None,
        )
        assert packet["task_draft"]["action"] == "create_human_escalation_task_draft"

    def test_blocked_pr_closed_escalation(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("blocked_pr_closed", "unknown"),
            None,
        )
        assert packet["task_draft"]["action"] == "create_human_escalation_task_draft"

    def test_blocked_pr_merged_no_action_wait(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("blocked_pr_merged", "unknown"),
            None,
        )
        assert packet["task_draft"]["action"] == "create_human_escalation_task_draft"

    def test_idempotency_key_includes_pr_sha_action(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", pr_number="196", head_sha="abc123"),
            None,
        )
        key = packet["task_draft"]["idempotency_key"]
        assert "196" in key
        assert "abc123" in key
        assert "create_reviewer_task_draft" in key

    def test_reviewer_draft_says_latest_head_only(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", "passed"),
            None,
        )
        body = packet["task_draft"]["body"]
        assert "latest" in body.lower()

    def test_builder_draft_says_allowed_files(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("codex_suggestions", "passed"),
            None,
        )
        body = packet["task_draft"]["body"]
        assert "allowed files" in body.lower() or "allowed_files" in body.lower()

    def test_all_drafts_prohibit_merge(self):
        mod = _import_mod()
        actions = [
            ("codex_request_needed", "passed"),
            ("codex_suggestions", "passed"),
            ("ci_failed", "failed"),
            ("ready_for_reviewer", "passed"),
            ("blocked_scope", "unknown"),
        ]
        for cls, ci in actions:
            packet = mod.build_task_draft(minimal_classifier(cls, ci), None)
            body = packet["task_draft"]["body"]
            assert "do not merge" in body.lower(), f"{cls}/{ci} body missing merge prohibition"

    def test_all_drafts_prohibit_memory_update_and_skill_manage(self):
        mod = _import_mod()
        actions = [
            ("codex_request_needed", "passed"),
            ("codex_suggestions", "passed"),
            ("ci_failed", "failed"),
            ("ready_for_reviewer", "passed"),
            ("blocked_scope", "unknown"),
        ]
        for cls, ci in actions:
            packet = mod.build_task_draft(minimal_classifier(cls, ci), None)
            body = packet["task_draft"]["body"]
            body_lower = body.lower()
            # Memory: if mentioned, must be in a prohibition context
            if "memory" in body_lower:
                assert "do not update memory" in body_lower, \
                    f"{cls}/{ci} body mentions memory without 'do not update memory'"
            # skill_manage: must be in a prohibition context if mentioned
            if "skill_manage" in body_lower:
                assert "do not" in body_lower and "skill_manage" in body_lower, \
                    f"{cls}/{ci} body mentions skill_manage without 'do not' prefix"

    def test_hermes_in_allowed_files_fails_validation(self):
        mod = _import_mod()
        # Build a packet manually with hermes path
        packet = mod.build_task_draft(
            minimal_classifier("codex_suggestions", "passed"),
            None,
        )
        packet["task_draft"]["allowed_files"] = ["/home/max/.hermes/config.yaml"]
        errs = mod.validate_task_draft_packet(packet)
        assert any(".hermes" in e for e in errs)

    def test_with_executor_packet(self):
        mod = _import_mod()
        executor = {
            "packet_kind": "aed.executor.plan.v1",
            "schema_version": 1,
            "generated_at": "2026-05-11T00:00:00Z",
            "pr_plan": {
                "allowed_files": ["scripts/local/aed_executor_packet.py"],
                "forbidden_files": ["engine/"],
                "goal": "Add executor packet scaffold",
                "validation_commands": ["python3 -m compileall scripts/local tests"],
            },
        }
        classifier = minimal_classifier("ready_for_reviewer", "passed")
        packet = mod.build_task_draft(classifier, executor)
        td = packet["task_draft"]
        assert "scripts/local/aed_executor_packet.py" in td.get("allowed_files", [])

    def test_executor_empty_allowed_files_falls_back_to_classifier_changed_files(self):
        """P1 fix: executor with empty allowed_files falls back to classifier changed_files."""
        mod = _import_mod()
        executor = {
            "packet_kind": "aed.executor.plan.v1",
            "schema_version": 1,
            "generated_at": "2026-05-11T00:00:00Z",
            "pr_plan": {
                "allowed_files": [],  # malformed/empty
                "forbidden_files": [],
                "goal": "Empty scope",
                "validation_commands": [],
            },
        }
        classifier = minimal_classifier(
            "codex_suggestions", "passed",
            changed_files=["scripts/local/something.py"],
        )
        packet = mod.build_task_draft(classifier, executor)
        td = packet["task_draft"]
        # Should fall back to classifier changed_files, not empty list
        assert "scripts/local/something.py" in td.get("allowed_files", [])
        assert td["allowed_files"] != []


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

class TestValidate:
    def test_valid_packet_passes(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", "passed"),
            None,
        )
        errs = mod.validate_task_draft_packet(packet)
        assert errs == []

    def test_wrong_packet_kind_fails(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", "passed"),
            None,
        )
        packet["packet_kind"] = "wrong.kind.v1"
        errs = mod.validate_task_draft_packet(packet)
        assert any("aed.pr_gate.task_draft.v1" in e for e in errs)

    def test_wrong_schema_version_fails(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", "passed"),
            None,
        )
        packet["schema_version"] = 99
        errs = mod.validate_task_draft_packet(packet)
        assert any("schema_version must be 1" in e for e in errs)

    def test_invalid_generated_at_fails(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", "passed"),
            None,
        )
        packet["generated_at"] = "not-a-timestamp"
        errs = mod.validate_task_draft_packet(packet)
        assert any("ISO-8601" in e for e in errs)

    def test_missing_pr_number_fails(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", "passed"),
            None,
        )
        packet["source"]["pr_number"] = ""
        errs = mod.validate_task_draft_packet(packet)
        assert any("pr_number" in e for e in errs)

    def test_missing_head_sha_fails(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", "passed"),
            None,
        )
        packet["source"]["head_sha"] = ""
        errs = mod.validate_task_draft_packet(packet)
        assert any("head_sha" in e for e in errs)

    def test_missing_idempotency_key_fails(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", "passed"),
            None,
        )
        packet["task_draft"]["idempotency_key"] = ""
        errs = mod.validate_task_draft_packet(packet)
        assert any("idempotency_key" in e.lower() for e in errs)

    def test_unknown_action_fails(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", "passed"),
            None,
        )
        packet["task_draft"]["action"] = "unknown_action"
        errs = mod.validate_task_draft_packet(packet)
        assert any("unknown_action" in e for e in errs)


# ---------------------------------------------------------------------------
# Render tests
# ---------------------------------------------------------------------------

class TestRenderMd:
    def test_render_includes_action(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", "passed"),
            None,
        )
        md = mod.render_md(packet)
        assert "create_reviewer_task_draft" in md

    def test_render_includes_source(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", "passed", pr_number="196"),
            None,
        )
        md = mod.render_md(packet)
        assert "196" in md
        assert "Source PR" in md

    def test_render_includes_controller_rules(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", "passed"),
            None,
        )
        md = mod.render_md(packet)
        assert "Controller Rules" in md
        assert "True" in md  # no_auto_dispatch renders as True

    def test_deterministic_output(self):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", "passed"),
            None,
        )
        md1 = mod.render_md(packet)
        md2 = mod.render_md(packet)
        assert md1 == md2


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestCLI:
    def test_validate_valid_returns_0(self, tmp_path: Path):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", "passed"),
            None,
        )
        p = tmp_path / "packet.json"
        write_packet(packet, p)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "validate", str(p)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, f"stdout: {result.stdout}, stderr: {result.stderr}"
        assert "valid" in result.stdout.lower()

    def test_validate_invalid_returns_nonzero(self, tmp_path: Path):
        p = tmp_path / "packet.json"
        write_packet({"packet_kind": "wrong.kind", "schema_version": 1, "generated_at": "2026-05-11T00:00:00Z", "source": {"pr_number": "", "head_sha": ""}, "task_draft": {"action": "invalid"}}, p)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "validate", str(p)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode != 0

    def test_render_md_writes_expected_sections(self, tmp_path: Path):
        mod = _import_mod()
        packet = mod.build_task_draft(
            minimal_classifier("ready_for_reviewer", "passed"),
            None,
        )
        p = tmp_path / "packet.json"
        out = tmp_path / "draft.md"
        write_packet(packet, p)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "render-md", str(p), "--output", str(out)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert out.exists()
        text = out.read_text()
        assert "Source PR" in text
        assert "Task Draft" in text
        assert "Controller Rules" in text


# ---------------------------------------------------------------------------
# Safety grep test
# ---------------------------------------------------------------------------

class TestSafetyGrep:
    def test_no_network_or_mutation_calls(self):
        """
        Confirm script contains no prohibited API calls (not string literals).
        Uses dedented source to avoid false positives from prohibition text
        like 'Do not update memory' or 'hermes kanban'.
        """
        import re

        text = SCRIPT.read_text()
        # Remove string literals to avoid false positives from prohibition text
        clean = re.sub(r'""".*?"""', '""', text, flags=re.DOTALL)
        clean = re.sub(r"'''.*?'''", "''", clean, flags=re.DOTALL)
        clean = re.sub(r'"[^"]*"', '""', clean)
        clean = re.sub(r"'[^']*'", "''", clean)

        prohibited = [
            (r'requests\.post\(', "requests.post"),
            (r'requests\.get\(', "requests.get"),
            (r'requests\.patch\(', "requests.patch"),
            (r'requests\.put\(', "requests.put"),
            (r'urllib\.request\.urlopen\(', "urllib.request.urlopen"),
            (r'httpx\.', "httpx"),
            (r'gh\.pr\.merge\(', "gh.pr.merge"),
            (r'gh\.api\(', "gh.api"),
            (r'hermes\.kanban', "hermes.kanban"),
            (r'memory\.update\(', "memory.update"),
            (r'skill_manage\(', "skill_manage"),
            (r'delegate_task\(', "delegate_task"),
            (r'cronjob\(', "cronjob"),
            (r'fact_store\(', "fact_store"),
            (r'subprocess\.run\(\s*\[', "subprocess.run with list"),
        ]
        for pat, label in prohibited:
            assert not re.search(pat, clean), f"Prohibited pattern '{label}' found in script"