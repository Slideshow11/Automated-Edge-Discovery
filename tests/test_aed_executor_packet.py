#!/usr/bin/env python3
"""
Tests for aed_executor_packet.py
Covers: validate, render-md, from-roadmap CLI modes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent  # /home/max/Automated-Edge-Discovery
SCRIPT = REPO_ROOT / "scripts" / "local" / "aed_executor_packet.py"


def _import_mod():
    """Import aed_executor_packet module from REPO_ROOT."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("aed_executor_packet", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def minimal_packet(overrides: dict | None = None) -> dict:
    """Return a minimal valid executor packet with test defaults."""
    base = {
        "packet_kind": "aed.executor.plan.v1",
        "schema_version": 1,
        "generated_at": "2026-05-11T00:00:00Z",
        "source_roadmap_packet": {
            "path": "/home/max/aed_tasker_runs/clean_tasker_run_after_pr195/ROADMAP_PACKET.json",
            "packet_kind": "aed.tasker.report.v1",
            "selected_candidate_id": "AED-CAND-202",
        },
        "selected_candidate": {
            "candidate_id": "AED-CAND-202",
            "title": "Add Executor planning packet scaffold",
            "goal": "Implement aed_executor_packet.py scaffold and tests",
            "why_now": "Architecture requires this before Builder can be dispatched",
            "risk_if_skipped": "Executor cannot produce bounded PR plans",
            "risk_if_built_too_early": "Low — isolated to tooling",
            "estimated_scope": "medium",
            "depends_on": [],
        },
        "pr_plan": {
            "pr_title": "tooling: add read-only AED Executor packet scaffold",
            "branch_name": "tooling/aed-executor-packet-scaffold",
            "goal": "Add aed_executor_packet.py with validate/render-md/from-roadmap CLI",
            "non_goals": [
                "Do not call LLMs",
                "Do not dispatch Builder",
                "Do not mutate registries",
            ],
            "allowed_files": [
                "scripts/local/aed_executor_packet.py",
                "tests/test_aed_executor_packet.py",
                "docs/aed_executor_packet_usage.md",
            ],
            "forbidden_files": ["engine/", "schemas/", "fixtures/"],
            "implementation_steps": [
                "Write aed_executor_packet.py",
                "Write tests",
                "Update docs",
            ],
            "expected_tests": ["tests/test_aed_executor_packet.py"],
            "validation_commands": [
                "python3 -m compileall scripts/local tests",
                "PYTHONPATH=. python3 -m pytest -q",
            ],
            "safety_grep_patterns": [
                "requests.post",
                "memory.update",
                "skill_manage",
                "gh pr merge",
            ],
            "scope_check": {
                "max_files_changed": 8,
                "allowed_path_prefixes": ["scripts/local/", "tests/", "docs/"],
                "forbidden_path_prefixes": ["engine/", "schemas/"],
            },
            "codex_review_policy": {
                "required_before_merge": True,
                "model": "gpt-5.3-codex",
                "focus_areas": ["file boundaries", "no forbidden path touched"],
            },
            "reviewer_focus": [
                "allowed_files respected",
                "validation commands pass",
            ],
            "merge_policy": {
                "required_authorization_phrase": "I confirm",
                "auto_merge_enabled": False,
                "require_exact_phrase_match": True,
            },
        },
        "gate_config": {
            "require_ci_green": True,
            "require_codex_clean": True,
            "require_reviewer_merge_recommendation": True,
            "require_human_merge_authorization": True,
            "max_patch_cycles": 3,
            "codex_cooldown_minutes": 5,
            "codex_unavailable_policy": "block_merge",
        },
        "split_triggers": [
            "Changes touch engine/ or fixtures/",
            "Changes add a new dependency",
        ],
        "blockers_or_uncertainty": [],
        "safety_annotations": {
            "registry_mutation_locked": True,
            "no_llm_call": True,
            "no_kanban_dispatch": True,
            "no_github_mutation": True,
        },
    }
    if overrides:
        base.update(overrides)
    return base


def write_packet(packet: dict, path: Path | str) -> None:
    with open(path, "w") as f:
        json.dump(packet, f, indent=2)


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

class TestValidate:
    def test_valid_minimal_packet_passes(self, tmp_path: Path):
        p = tmp_path / "packet.json"
        write_packet(minimal_packet(), p)
        mod = _import_mod()
        valid, errors = mod.validate_packet(p)
        assert valid, f"Expected valid but got errors: {errors}"
        assert errors == []

    def test_wrong_packet_kind_fails(self, tmp_path: Path):
        p = tmp_path / "packet.json"
        write_packet(minimal_packet({"packet_kind": "wrong.kind.v1"}), p)
        mod = _import_mod()
        valid, errors = mod.validate_packet(p)
        assert not valid
        assert any("packet_kind must be 'aed.executor.plan.v1'" in e for e in errors)

    def test_missing_schema_version_fails(self, tmp_path: Path):
        p = tmp_path / "packet.json"
        write_packet(minimal_packet({"schema_version": None}), p)
        mod = _import_mod()
        valid, errors = mod.validate_packet(p)
        assert not valid
        assert any("schema_version must be 1" in e for e in errors)

    def test_missing_candidate_id_fails(self, tmp_path: Path):
        p = tmp_path / "packet.json"
        write_packet(
            minimal_packet(
                {"selected_candidate": {"candidate_id": "", "title": "t", "goal": "g"}}
            ),
            p,
        )
        mod = _import_mod()
        valid, errors = mod.validate_packet(p)
        assert not valid
        assert any("candidate_id is required" in e for e in errors)

    def test_missing_pr_title_fails(self, tmp_path: Path):
        p = tmp_path / "packet.json"
        write_packet(
            minimal_packet(
                {"pr_plan": {"pr_title": "", "branch_name": "b", "goal": "g"}}
            ),
            p,
        )
        mod = _import_mod()
        valid, errors = mod.validate_packet(p)
        assert not valid
        assert any("pr_plan.pr_title is required" in e for e in errors)

    def test_missing_allowed_files_fails(self, tmp_path: Path):
        p = tmp_path / "packet.json"
        write_packet(
            minimal_packet(
                {
                    "pr_plan": {
                        "allowed_files": [],
                        "forbidden_files": [],
                        "pr_title": "t",
                        "branch_name": "b",
                        "goal": "g",
                        "implementation_steps": ["s"],
                        "expected_tests": ["t"],
                        "validation_commands": ["c"],
                        "merge_policy": {"required_authorization_phrase": "x"},
                    }
                }
            ),
            p,
        )
        mod = _import_mod()
        valid, errors = mod.validate_packet(p)
        assert not valid
        assert any("allowed_files must be a non-empty list" in e for e in errors)

    def test_missing_forbidden_files_fails(self, tmp_path: Path):
        p = tmp_path / "packet.json"
        write_packet(
            minimal_packet(
                {
                    "pr_plan": {
                        "forbidden_files": "not-a-list",
                        "allowed_files": ["x"],
                        "pr_title": "t",
                        "branch_name": "b",
                        "goal": "g",
                        "implementation_steps": ["s"],
                        "expected_tests": ["t"],
                        "validation_commands": ["c"],
                        "merge_policy": {"required_authorization_phrase": "x"},
                    }
                }
            ),
            p,
        )
        mod = _import_mod()
        valid, errors = mod.validate_packet(p)
        assert not valid
        assert any("forbidden_files must be a list" in e for e in errors)

    def test_hermes_in_allowed_files_fails(self, tmp_path: Path):
        p = tmp_path / "packet.json"
        write_packet(
            minimal_packet(
                {
                    "pr_plan": {
                        "allowed_files": ["/home/max/.hermes/config.yaml"],
                        "forbidden_files": [],
                        "pr_title": "t",
                        "branch_name": "b",
                        "goal": "g",
                        "implementation_steps": ["s"],
                        "expected_tests": ["t"],
                        "validation_commands": ["c"],
                        "merge_policy": {"required_authorization_phrase": "x"},
                    }
                }
            ),
            p,
        )
        mod = _import_mod()
        valid, errors = mod.validate_packet(p)
        assert not valid
        assert any(".hermes" in e for e in errors)

    def test_registry_mutation_in_allowed_files_without_lock_fails(self, tmp_path: Path):
        p = tmp_path / "packet.json"
        write_packet(
            minimal_packet(
                {
                    "pr_plan": {
                        "allowed_files": ["scripts/local/edge_hypothesis_registry.py"],
                        "forbidden_files": [],
                        "pr_title": "t",
                        "branch_name": "b",
                        "goal": "g",
                        "implementation_steps": ["s"],
                        "expected_tests": ["t"],
                        "validation_commands": ["c"],
                        "merge_policy": {"required_authorization_phrase": "x"},
                    },
                    "safety_annotations": {"registry_mutation_locked": False},
                }
            ),
            p,
        )
        mod = _import_mod()
        valid, errors = mod.validate_packet(p)
        assert not valid
        assert any("registry" in e.lower() for e in errors)

    def test_require_human_merge_authorization_false_fails(self, tmp_path: Path):
        p = tmp_path / "packet.json"
        write_packet(
            minimal_packet(
                {
                    "gate_config": {
                        "require_human_merge_authorization": False,
                        "require_ci_green": True,
                        "require_codex_clean": True,
                        "require_reviewer_merge_recommendation": True,
                        "max_patch_cycles": 3,
                        "codex_cooldown_minutes": 5,
                        "codex_unavailable_policy": "block_merge",
                    }
                }
            ),
            p,
        )
        mod = _import_mod()
        valid, errors = mod.validate_packet(p)
        assert not valid
        assert any("require_human_merge_authorization must be true" in e for e in errors)

    def test_require_codex_clean_false_without_policy_fails(self, tmp_path: Path):
        p = tmp_path / "packet.json"
        write_packet(
            minimal_packet(
                {
                    "gate_config": {
                        "require_codex_clean": False,
                        "require_human_merge_authorization": True,
                        "require_ci_green": True,
                        "require_reviewer_merge_recommendation": True,
                        "max_patch_cycles": 3,
                        "codex_cooldown_minutes": 5,
                    }
                }
            ),
            p,
        )
        mod = _import_mod()
        valid, errors = mod.validate_packet(p)
        assert not valid
        assert any("codex_unavailable_policy" in e for e in errors)

    def test_empty_implementation_steps_fails(self, tmp_path: Path):
        p = tmp_path / "packet.json"
        write_packet(
            minimal_packet(
                {
                    "pr_plan": {
                        "implementation_steps": [],
                        "allowed_files": ["x"],
                        "forbidden_files": [],
                        "pr_title": "t",
                        "branch_name": "b",
                        "goal": "g",
                        "expected_tests": ["t"],
                        "validation_commands": ["c"],
                        "merge_policy": {"required_authorization_phrase": "x"},
                    }
                }
            ),
            p,
        )
        mod = _import_mod()
        valid, errors = mod.validate_packet(p)
        assert not valid
        assert any("implementation_steps must be a non-empty" in e for e in errors)

    def test_empty_expected_tests_fails(self, tmp_path: Path):
        p = tmp_path / "packet.json"
        write_packet(
            minimal_packet(
                {
                    "pr_plan": {
                        "expected_tests": [],
                        "allowed_files": ["x"],
                        "forbidden_files": [],
                        "pr_title": "t",
                        "branch_name": "b",
                        "goal": "g",
                        "implementation_steps": ["s"],
                        "validation_commands": ["c"],
                        "merge_policy": {"required_authorization_phrase": "x"},
                    }
                }
            ),
            p,
        )
        mod = _import_mod()
        valid, errors = mod.validate_packet(p)
        assert not valid
        assert any("expected_tests must be a non-empty" in e for e in errors)


# ---------------------------------------------------------------------------
# Render-md tests
# ---------------------------------------------------------------------------

class TestRenderMd:
    def test_render_includes_goal(self, tmp_path: Path):
        mod = _import_mod()
        md = mod.render_md(minimal_packet())
        # pr_plan.goal is "Add aed_executor_packet.py with validate/render-md/from-roadmap CLI"
        assert "aed_executor_packet.py" in md

    def test_render_includes_allowed_files(self, tmp_path: Path):
        mod = _import_mod()
        md = mod.render_md(minimal_packet())
        assert "scripts/local/aed_executor_packet.py" in md

    def test_render_includes_gate_config(self, tmp_path: Path):
        mod = _import_mod()
        md = mod.render_md(minimal_packet())
        assert "Require CI green" in md
        assert "Require Codex clean" in md

    def test_render_includes_merge_policy(self, tmp_path: Path):
        mod = _import_mod()
        md = mod.render_md(minimal_packet())
        assert "I confirm" in md
        assert "Auto-merge enabled" in md

    def test_deterministic_output_stable(self, tmp_path: Path):
        mod = _import_mod()
        p1 = mod.deterministic_serialize(minimal_packet())
        p2 = mod.deterministic_serialize(minimal_packet())
        assert p1 == p2


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestCLIValidate:
    def test_validate_valid_returns_0(self, tmp_path: Path):
        p = tmp_path / "packet.json"
        write_packet(minimal_packet(), p)
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
        write_packet(minimal_packet({"packet_kind": "bad"}), p)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "validate", str(p)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode != 0
        assert "ERROR" in result.stderr

    def test_validate_missing_file_returns_nonzero(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "validate", "/nonexistent/packet.json"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode != 0


class TestCLIRenderMd:
    def test_render_md_writes_expected_sections(self, tmp_path: Path):
        p = tmp_path / "packet.json"
        out = tmp_path / "plan.md"
        write_packet(minimal_packet(), p)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "render-md", str(p), "--output", str(out)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert out.exists()
        text = out.read_text()
        assert "Executor Execution Plan" in text
        assert "Allowed files" in text
        assert "Gate Config" in text
        assert "I confirm" in text


# ---------------------------------------------------------------------------
# from-roadmap tests
# ---------------------------------------------------------------------------

class TestFromRoadmap:
    def test_from_roadmap_selects_candidate_and_writes_executor_packet(self, tmp_path: Path):
        roadmap = {
            "packet_kind": "aed.tasker.report.v1",
            "candidate_prs": [
                {
                    "candidate_id": "AED-CAND-202",
                    "title": "Add Executor planning packet scaffold",
                    "goal": "Implement aed_executor_packet.py scaffold and tests",
                    "why_now": "Architecture requires this before Builder can be dispatched",
                    "risk_if_skipped": "Executor cannot produce bounded PR plans",
                    "risk_if_built_too_early": "Low — isolated to tooling",
                    "allowed_files": [
                        "scripts/local/aed_executor_packet.py",
                        "tests/test_aed_executor_packet.py",
                        "docs/aed_executor_packet_usage.md",
                    ],
                    "forbidden_files": ["engine/", "schemas/", "fixtures/"],
                    "expected_tests": ["tests/test_aed_executor_packet.py"],
                }
            ],
        }
        roadmap_path = tmp_path / "ROADMAP_PACKET.json"
        write_packet(roadmap, roadmap_path)
        output_json = tmp_path / "EXECUTOR_PACKET.json"
        output_md = tmp_path / "AED_EXECUTION_PLAN.md"

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "from-roadmap",
                "--roadmap-packet",
                str(roadmap_path),
                "--candidate-id",
                "AED-CAND-202",
                "--output-json",
                str(output_json),
                "--output-md",
                str(output_md),
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}, stdout: {result.stdout}"
        assert output_json.exists()
        assert output_md.exists()

        mod = _import_mod()
        valid, errors = mod.validate_packet(output_json)
        assert valid, f"Generated packet should be valid: {errors}"

        with open(output_json) as f:
            pkt = json.load(f)
        assert pkt["selected_candidate"]["candidate_id"] == "AED-CAND-202"
        assert pkt["source_roadmap_packet"]["selected_candidate_id"] == "AED-CAND-202"

    def test_from_roadmap_fails_if_candidate_missing(self, tmp_path: Path):
        roadmap = {
            "packet_kind": "aed.tasker.report.v1",
            "candidate_prs": [
                {
                    "candidate_id": "AED-CAND-999",
                    "title": "Different candidate",
                    "goal": "Something",
                    "allowed_files": [],
                    "forbidden_files": [],
                    "expected_tests": [],
                }
            ],
        }
        roadmap_path = tmp_path / "ROADMAP_PACKET.json"
        write_packet(roadmap, roadmap_path)

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "from-roadmap",
                "--roadmap-packet",
                str(roadmap_path),
                "--candidate-id",
                "AED-CAND-202",
                "--output-json",
                str(tmp_path / "out.json"),
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode != 0
        assert "AED-CAND-202" in result.stderr
        assert "not found" in result.stderr.lower()


# ---------------------------------------------------------------------------
# Safety grep test
# ---------------------------------------------------------------------------

class TestSafetyGrep:
    def test_no_network_or_mutation_calls(self):
        """Confirm script contains no prohibited API calls (not just string literals).

        Checks for function-call patterns only, so string values in
        safety_grep_patterns lists do not trigger false positives.
        """
        text = SCRIPT.read_text()
        # Function-call forms only — string literals (in safety_grep_patterns lists)
        # use different syntax and won't match these patterns.
        prohibited_calls = [
            "requests.post(",
            "requests.get(",
            "requests.patch(",
            "requests.put(",
            "urllib.request.urlopen",
            "httpx.",
            "gh.pr.merge",      # gh CLI module usage
            "gh.api",           # gh API function
            "hermes.kanban",    # hermes kanban module
            "memory.update(",
            "skill_manage(",
            "delegate_task(",
            "cronjob(",
            "subprocess.run([",
        ]
        for pat in prohibited_calls:
            assert pat not in text, f"Prohibited pattern '{pat}' found in script"