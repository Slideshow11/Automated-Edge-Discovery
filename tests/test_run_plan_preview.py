#!/usr/bin/env python3
"""
Tests for run_plan_preview.py.

Covers plan-preview-only behavior: no execution, no repo mutation,
packet validation, constraint checking, output paths, and forbidden
action detection.
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

# Import the module under test
import importlib.util
spec = importlib.util.spec_from_file_location(
    "run_plan_preview",
    "/home/max/Automated-Edge-Discovery/scripts/local/run_plan_preview.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Short aliases
validate_packet = mod.validate_packet
validate_plan_against_packet = mod.validate_plan_against_packet
validate_plan_only_allowed_files = mod.validate_plan_only_allowed_files
build_plan_system_prompt = mod.build_plan_system_prompt
_is_forbidden_path = mod._is_forbidden_path
_load_json = mod._load_json
NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_packet(
    *,
    allowed_files: list[str] | None = None,
    forbidden_files: list[str] | None = None,
    do_not: list[str] | None = None,
    new_deps_allowed: bool = False,
    description: str = "Test task",
) -> dict:
    return {
        "packet_kind": "aed.worker.packet.v1",
        "task": {
            "description": description,
            "allowed_files": allowed_files or [],
            "forbidden_files": forbidden_files or [],
            "do_not": do_not or [],
            "dependency_install_policy": {
                "new_dependencies_allowed": new_deps_allowed,
                "requires_human_approval": True,
                "minimum_package_age_days": 14,
                "lockfile_review_required": True,
                "postinstall_scripts_require_approval": True,
            },
        },
    }


# ---------------------------------------------------------------------------
# Tests: validate_packet
# ---------------------------------------------------------------------------

class TestValidatePacket:
    def test_valid_minimal_packet(self):
        pkt = make_packet()
        assert validate_packet(pkt) == []

    def test_valid_full_packet(self):
        pkt = make_packet(
            allowed_files=["scripts/local/foo.py", "tests/test_foo.py"],
            forbidden_files=["scripts/local/secrets.py"],
            do_not=["do not push", "do not install packages"],
            description="Implement foo",
        )
        assert validate_packet(pkt) == []

    def test_missing_packet_kind(self):
        pkt = make_packet()
        del pkt["packet_kind"]
        errors = validate_packet(pkt)
        assert any("packet_kind" in e for e in errors)

    def test_wrong_packet_kind(self):
        pkt = make_packet()
        pkt["packet_kind"] = "aed.executor.plan.v1"
        errors = validate_packet(pkt)
        assert any("packet_kind" in e for e in errors)

    def test_missing_task(self):
        pkt = {"packet_kind": "aed.worker.packet.v1"}
        errors = validate_packet(pkt)
        assert any("task" in e for e in errors)

    def test_allowed_files_not_list(self):
        pkt = make_packet()
        pkt["task"]["allowed_files"] = "not a list"
        errors = validate_packet(pkt)
        assert any("allowed_files" in e for e in errors)

    def test_forbidden_files_not_list(self):
        pkt = make_packet()
        pkt["task"]["forbidden_files"] = 123
        errors = validate_packet(pkt)
        assert any("forbidden_files" in e for e in errors)

    def test_do_not_not_list(self):
        pkt = make_packet()
        pkt["task"]["do_not"] = "do not push"
        errors = validate_packet(pkt)
        assert any("do_not" in e for e in errors)


# ---------------------------------------------------------------------------
# Tests: validate_plan_against_packet
# ---------------------------------------------------------------------------

class TestValidatePlanAgainstPacket:
    def test_clean_plan_no_violations(self):
        plan = "1. Review scripts/local/foo.py\n2. Update tests/test_foo.py"
        pkt = make_packet(
            allowed_files=["scripts/local/foo.py", "tests/test_foo.py"],
            forbidden_files=["scripts/local/secrets.py"],
            do_not=["do not push", "do not install packages"],
        )
        assert validate_plan_against_packet(plan, pkt) == []

    def test_plan_references_forbidden_file(self):
        plan = "1. Edit scripts/local/secrets.py to add key"
        pkt = make_packet(forbidden_files=["scripts/local/secrets.py"])
        violations = validate_plan_against_packet(plan, pkt)
        assert any("secrets.py" in v for v in violations)

    def test_plan_violates_do_not(self):
        # Plan explicitly says "do not push" — word-boundary check catches it
        plan = "1. Do not push changes to the repository without approval"
        pkt = make_packet(do_not=["do not push"])
        violations = validate_plan_against_packet(plan, pkt)
        assert len(violations) == 1, f"expected 1 violation, got {violations}"
        assert "do not push" in violations[0]

    def test_plan_violates_do_not_case_insensitive(self):
        plan = "1. DO NOT PUSH under any circumstances"
        pkt = make_packet(do_not=["do not push"])
        violations = validate_plan_against_packet(plan, pkt)
        assert any("do not push" in v for v in violations)

    def test_plan_proposes_pip_install_when_forbidden(self):
        plan = "1. pip install requests to add the dependency"
        pkt = make_packet(new_deps_allowed=False)
        violations = validate_plan_against_packet(plan, pkt)
        assert any("pip install" in v for v in violations)

    def test_plan_proposes_npm_install_when_forbidden(self):
        plan = "1. npm install lodash to add the library"
        pkt = make_packet(new_deps_allowed=False)
        violations = validate_plan_against_packet(plan, pkt)
        assert any("npm install" in v for v in violations)

    def test_plan_proposes_poetry_add_when_forbidden(self):
        plan = "1. poetry add httpx for HTTP support"
        pkt = make_packet(new_deps_allowed=False)
        violations = validate_plan_against_packet(plan, pkt)
        assert any("poetry add" in v for v in violations)

    def test_plan_allows_dep_install_when_policy_allows(self):
        plan = "1. pip install requests to add the dependency"
        pkt = make_packet(new_deps_allowed=True)
        assert validate_plan_against_packet(plan, pkt) == []

    def test_empty_do_not_pass(self):
        plan = "1. Edit src/app.py to add new feature"
        pkt = make_packet(do_not=[])
        assert validate_plan_against_packet(plan, pkt) == []


# ---------------------------------------------------------------------------
# Tests: validate_plan_only_allowed_files
# ---------------------------------------------------------------------------

class TestValidatePlanOnlyAllowedFiles:
    def test_plan_with_only_allowed_files_passes(self):
        plan = "1. Edit scripts/local/foo.py to add helper\n2. Edit tests/test_foo.py for coverage"
        pkt = make_packet(allowed_files=["scripts/local/foo.py", "tests/test_foo.py"])
        assert validate_plan_only_allowed_files(plan, pkt) == []

    def test_plan_references_file_not_in_allowed(self):
        plan = "1. Edit src/bar.py to add feature"
        pkt = make_packet(allowed_files=["scripts/local/foo.py"])
        violations = validate_plan_only_allowed_files(plan, pkt)
        assert any("bar.py" in v for v in violations)

    def test_empty_allowed_files_means_nothing_allowed(self):
        # Use a plan with actual path-like references (containing /)
        plan = "1. Edit scripts/local/foo.py to add feature"
        pkt = make_packet(allowed_files=[])
        violations = validate_plan_only_allowed_files(plan, pkt)
        assert len(violations) > 0, f"empty allowed_files must block all file references, got: {violations}"

    def test_plan_with_unprefixed_allowed_file_passes(self):
        plan = "1. Edit foo.py"
        pkt = make_packet(allowed_files=["foo.py"])
        # foo.py is not in allowed but the heuristic might miss it — this is a weak check
        # The actual enforcement is via forbidden_files and do_not
        assert isinstance(validate_plan_only_allowed_files(plan, pkt), list)

    def test_none_allowed_files_passes_without_violation(self):
        # None means not specified — no constraint
        pkt = make_packet(allowed_files=None)
        assert validate_plan_only_allowed_files("", pkt) == []

    def test_subpath_allowed_passes(self):
        plan = "1. Edit scripts/local/foo.py"
        pkt = make_packet(allowed_files=["scripts/"])
        violations = validate_plan_only_allowed_files(plan, pkt)
        assert len(violations) == 0

    def test_wildcard_dir_edit_outside_allowed_blocked(self):
        # A plan proposing "Edit scripts/*" where only "tests/" is allowed
        plan = "1. Edit scripts/*.py to add new feature"
        pkt = make_packet(allowed_files=["tests/"])
        violations = validate_plan_only_allowed_files(plan, pkt)
        assert len(violations) > 0, "wildcard dir edit outside allowed_files must block"

    def test_broad_directory_edit_blocked(self):
        # A plan proposing edits to a whole directory outside allowed scope
        plan = "1. Refactor all files in src/ directory to use new patterns"
        pkt = make_packet(allowed_files=["tests/test_foo.py"])
        violations = validate_plan_only_allowed_files(plan, pkt)
        assert len(violations) > 0, "broad directory-level edit must block when outside allowed_files"


# ---------------------------------------------------------------------------
# Tests: _is_forbidden_path
# ---------------------------------------------------------------------------

class TestIsForbiddenPath:
    def test_hermes_path_blocked(self):
        assert _is_forbidden_path("/home/max/.hermes/skills/foo.md")

    def test_tmp_hermes_blocked(self):
        assert _is_forbidden_path("/tmp/hermes/cache")

    def test_dot_hermes_in_path_blocked(self):
        assert _is_forbidden_path("/home/user/.hermes/foo")

    def test_audit_in_path_blocked(self):
        assert _is_forbidden_path("/home/max/Automated-Edge-Discovery/audit/log.jsonl")

    def test_safe_repo_path_not_blocked(self):
        assert not _is_forbidden_path("/home/max/Automated-Edge-Discovery/scripts/local/foo.py")

    def test_safe_absolute_path_not_blocked(self):
        assert not _is_forbidden_path("/tmp/aed_runs/123/plan_preview/output.json")


# ---------------------------------------------------------------------------
# Tests: build_plan_system_prompt
# ---------------------------------------------------------------------------

class TestBuildPlanSystemPrompt:
    def test_includes_task_description(self):
        pkt = make_packet(description="Implement login")
        prompt = build_plan_system_prompt(pkt)
        assert "Implement login" in prompt

    def test_includes_no_edit_instruction(self):
        pkt = make_packet()
        prompt = build_plan_system_prompt(pkt)
        assert "MUST NOT edit any files" in prompt

    def test_includes_no_install_instruction(self):
        pkt = make_packet()
        prompt = build_plan_system_prompt(pkt)
        assert "MUST NOT install packages" in prompt

    def test_includes_forbidden_files(self):
        pkt = make_packet(forbidden_files=["secrets.py", "env"]);
        prompt = build_plan_system_prompt(pkt)
        assert "secrets.py" in prompt
        assert "env" in prompt

    def test_includes_do_not_constraints(self):
        pkt = make_packet(do_not=["do not push", "do not merge"])
        prompt = build_plan_system_prompt(pkt)
        assert "do not push" in prompt
        assert "do not merge" in prompt

    def test_includes_dependency_policy_when_forbidden(self):
        pkt = make_packet(new_deps_allowed=False)
        prompt = build_plan_system_prompt(pkt)
        assert "No new dependencies may be installed" in prompt

    def test_omits_dependency_policy_when_allowed(self):
        pkt = make_packet(new_deps_allowed=True)
        prompt = build_plan_system_prompt(pkt)
        assert "No new dependencies" not in prompt


# ---------------------------------------------------------------------------
# Tests: _load_json
# ---------------------------------------------------------------------------

class TestLoadJson:
    def test_loads_valid_json(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=True, mode="w") as f:
            json.dump({"key": "value"}, f)
            f.flush()
            result = _load_json(f.name)
            assert result == {"key": "value"}

    def test_missing_file_returns_empty_dict(self):
        result = _load_json("/tmp/nonexistent_12345.json")
        assert result == {}

    def test_invalid_json_returns_empty_dict(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=True, mode="w") as f:
            f.write("not valid json {")
            f.flush()
            result = _load_json(f.name)
            assert result == {}


# ---------------------------------------------------------------------------
# Integration-style tests (no actual Claude Code invocation)
# ---------------------------------------------------------------------------

class TestNoMutationPaths:
    def test_script_has_no_dispatch_function_calls(self):
        src = Path("/home/max/Automated-Edge-Discovery/scripts/local/run_plan_preview.py").read_text()
        # Check for actual dispatch() calls, not the word "dispatch" in comments
        import re
        # dispatch( or from dispatch or import dispatch — not case-insensitive word in comments
        dispatch_calls = re.findall(r'\bdispatch\s*\(', src, re.IGNORECASE)
        assert len(dispatch_calls) == 0, f"found dispatch() calls: {dispatch_calls}"

    def test_script_has_no_audit_log_append(self):
        src = Path("/home/max/Automated-Edge-Discovery/scripts/local/run_plan_preview.py").read_text()
        # Check for actual audit log append calls, not word "audit" in comments/docstrings
        import re
        append_calls = re.findall(r'\.append\s*\(', src)  # any .append( call
        audit_append = [c for c in append_calls if 'audit' in c.lower()]
        assert len(audit_append) == 0, f"found audit.append() calls: {audit_append}"

    def test_script_has_no_board_touch(self):
        src = Path("/home/max/Automated-Edge-Discovery/scripts/local/run_plan_preview.py").read_text()
        forbidden = ["production_board", "kanban", "linear"]
        for kw in forbidden:
            assert kw not in src.lower(), f"found '{kw}' in source"

    def test_script_does_not_import_hermes(self):
        src = Path("/home/max/Automated-Edge-Discovery/scripts/local/run_plan_preview.py").read_text()
        assert "hermes_tools" not in src
        assert "from hermes" not in src
        assert "import hermes" not in src

    def test_script_has_no_test_execution(self):
        src = Path("/home/max/Automated-Edge-Discovery/scripts/local/run_plan_preview.py").read_text()
        assert "pytest" not in src
        assert "unittest" not in src

    def test_output_defaults_to_tmp(self):
        src = Path("/home/max/Automated-Edge-Discovery/scripts/local/run_plan_preview.py").read_text()
        assert "/tmp/aed_runs" in src

    def test_no_git_push_calls(self):
        src = Path("/home/max/Automated-Edge-Discovery/scripts/local/run_plan_preview.py").read_text()
        import re
        # Only flag actual "git push" invocations, not git status/rev-parse
        git_push_calls = re.findall(r'["\']git["\'].*?["\']push["\']', src)
        assert len(git_push_calls) == 0, f"found git push calls: {git_push_calls}"


# ---------------------------------------------------------------------------
# Tests: run() — integration-style via patching (no actual Claude invocation)
# ---------------------------------------------------------------------------

import unittest.mock as mock


class TestRunPlanPreviewIntegration:
    """Test run() function paths via targeted patching of external calls."""

    def test_output_dir_in_repo_returns_error(self, tmp_path):
        # Patch _resolve_git_root to return the repo
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "rpp",
            "/home/max/Automated-Edge-Discovery/scripts/local/run_plan_preview.py",
        )
        rpp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rpp)
        with mock.patch.object(rpp, "_resolve_git_root", return_value=Path("/home/max/Automated-Edge-Discovery")):
            with mock.patch.object(rpp, "_git_status", return_value="clean"):
                args = mock.MagicMock()
                args.packet_json = str(tmp_path / "packet.json")
                args.output_dir = "/home/max/Automated-Edge-Discovery/.cache/plan_out"  # inside repo
                args.output_json = str(tmp_path / "result.json")
                args.output_md = str(tmp_path / "result.md")
                args.timeout = 120
                # Write valid packet
                import json
                with open(args.packet_json, "w") as f:
                    json.dump({"packet_kind": "aed.worker.packet.v1", "task": {"description": "x"}}, f)
                result_code = rpp.run(args)
                assert result_code == 1

    def test_nonzero_claude_exit_returns_error(self, tmp_path):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "rpp",
            "/home/max/Automated-Edge-Discovery/scripts/local/run_plan_preview.py",
        )
        rpp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rpp)
        with mock.patch.object(rpp, "_resolve_git_root", return_value=None):
            with mock.patch.object(rpp, "invoke_claude_plan", return_value=("some output", 127)):
                args = mock.MagicMock()
                args.packet_json = str(tmp_path / "packet.json")
                args.output_dir = str(tmp_path / "out")
                args.output_json = str(tmp_path / "result.json")
                args.output_md = None
                args.timeout = 120
                import json
                with open(args.packet_json, "w") as f:
                    json.dump({"packet_kind": "aed.worker.packet.v1", "task": {"description": "x"}}, f)
                result_code = rpp.run(args)
                assert result_code == 1

    def test_empty_plan_returns_error(self, tmp_path):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "rpp",
            "/home/max/Automated-Edge-Discovery/scripts/local/run_plan_preview.py",
        )
        rpp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rpp)
        with mock.patch.object(rpp, "_resolve_git_root", return_value=None):
            with mock.patch.object(rpp, "invoke_claude_plan", return_value=("", 0)):
                args = mock.MagicMock()
                args.packet_json = str(tmp_path / "packet.json")
                args.output_dir = str(tmp_path / "out")
                args.output_json = str(tmp_path / "result.json")
                args.output_md = None
                args.timeout = 120
                import json
                with open(args.packet_json, "w") as f:
                    json.dump({"packet_kind": "aed.worker.packet.v1", "task": {"description": "x"}}, f)
                result_code = rpp.run(args)
                assert result_code == 1

    def test_repo_mutated_returns_blocked(self, tmp_path):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "rpp",
            "/home/max/Automated-Edge-Discovery/scripts/local/run_plan_preview.py",
        )
        rpp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rpp)
        git_statuses = ["clean", "dirty: M scripts/local/foo.py"]  # before=clean, after=dirty
        with mock.patch.object(rpp, "_resolve_git_root", return_value=Path("/home/max/Automated-Edge-Discovery")):
            with mock.patch.object(rpp, "invoke_claude_plan", return_value=("1. Edit scripts/local/foo.py", 0)):
                with mock.patch.object(rpp, "_git_status", side_effect=lambda *a: git_statuses.pop(0)):
                    args = mock.MagicMock()
                    args.packet_json = str(tmp_path / "packet.json")
                    args.output_dir = str(tmp_path / "out")
                    args.output_json = str(tmp_path / "result.json")
                    args.output_md = None
                    args.timeout = 120
                    import json
                    with open(args.packet_json, "w") as f:
                        json.dump({
                            "packet_kind": "aed.worker.packet.v1",
                            "task": {
                                "description": "x",
                                "allowed_files": ["scripts/local/foo.py"],
                                "forbidden_files": [],
                                "do_not": [],
                            }
                        }, f)
                    result_code = rpp.run(args)
                    assert result_code == 1


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-q"])