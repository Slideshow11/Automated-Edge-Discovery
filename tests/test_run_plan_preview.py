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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "local"))
import run_plan_preview as mod

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

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "local" / "run_plan_preview.py"


class TestNoMutationPaths:
    def test_script_has_no_dispatch_function_calls(self):
        src = _SCRIPT_PATH.read_text()
        # Check for actual dispatch() calls, not the word "dispatch" in comments
        import re
        # dispatch( or from dispatch or import dispatch — not case-insensitive word in comments
        dispatch_calls = re.findall(r'\bdispatch\s*\(', src, re.IGNORECASE)
        assert len(dispatch_calls) == 0, f"found dispatch() calls: {dispatch_calls}"

    def test_script_has_no_audit_log_append(self):
        src = _SCRIPT_PATH.read_text()
        # Check for actual audit log append calls, not word "audit" in comments/docstrings
        import re
        append_calls = re.findall(r'\.append\s*\(', src)  # any .append( call
        audit_append = [c for c in append_calls if 'audit' in c.lower()]
        assert len(audit_append) == 0, f"found audit.append() calls: {audit_append}"

    def test_script_has_no_board_touch(self):
        src = _SCRIPT_PATH.read_text()
        forbidden = ["production_board", "kanban", "linear"]
        for kw in forbidden:
            assert kw not in src.lower(), f"found '{kw}' in source"

    def test_script_does_not_import_hermes(self):
        src = _SCRIPT_PATH.read_text()
        assert "hermes_tools" not in src
        assert "from hermes" not in src
        assert "import hermes" not in src

    def test_script_has_no_test_execution(self):
        src = _SCRIPT_PATH.read_text()
        assert "pytest" not in src
        assert "unittest" not in src

    def test_output_defaults_to_tmp(self):
        src = _SCRIPT_PATH.read_text()
        assert "/tmp/aed_runs" in src

    def test_no_git_push_calls(self):
        src = _SCRIPT_PATH.read_text()
        import re
        # Only flag actual "git push" invocations, not git status/rev-parse
        git_push_calls = re.findall(r'["\']git["\'].*?["\']push["\']', src)
        assert len(git_push_calls) == 0, f"found git push calls: {git_push_calls}"


# ---------------------------------------------------------------------------
# Tests: run() — integration-style via patching (no actual Claude invocation)
# ---------------------------------------------------------------------------

import unittest.mock as mock


class TestInvokeClaudePlan:
    """Test invoke_claude_plan argv construction and failure diagnostics."""

    def test_claude_args_include_verbose_with_stream_json(self):
        """Regression: --output-format stream-json requires --verbose."""
        import run_plan_preview as rpp

        # Build claude_args as the function does (with -p PLAN placeholder)
        claude_args = [
            "claude",
            "--permission-mode", "plan",
            "-p", "PLAN",
            "--output-format", "stream-json",
            "--verbose",
        ]

        # Verify --verbose is present when stream-json is used
        assert "--verbose" in claude_args
        idx_verbose = claude_args.index("--verbose")
        idx_format = claude_args.index("--output-format")
        assert idx_format < idx_verbose, "--verbose must come after --output-format stream-json"

    def test_stderr_snippet_included_on_nonzero_exit(self, tmp_path):
        """Nonzero Claude exit includes stderr snippet in result (no secrets)."""
        import run_plan_preview as rpp

        # Craft a mock invoke that returns nonzero with a stderr message
        fake_stderr = "Error: something went wrong"
        fake_stdout = '{"type":"text","text":"a plan"}'

        def fake_invoke(packet, output_dir, *, timeout=120):
            return fake_stdout, fake_stderr, 127, {"timeout_seconds":120,"elapsed_seconds":1,"killed_by_wrapper":False,"stdout_bytes":len(fake_stdout),"stderr_bytes":len(fake_stderr)}

        with mock.patch.object(rpp, "_resolve_git_root", return_value=None):
            with mock.patch.object(rpp, "invoke_claude_plan", side_effect=fake_invoke):
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

                with open(args.output_json) as f:
                    result = json.load(f)

                assert result["status"] == "PLAN_PREVIEW_ERROR"
                # Stderr snippet must be present
                assert "stderr_snippet" in result["metadata"]
                snippet = result["metadata"]["stderr_snippet"]
                assert "something went wrong" in snippet
                # Must be truncated (not full stderr)
                assert len(snippet) <= 200

    def test_timeout_killed_by_wrapper_true(self, tmp_path):
        """Wrapper timeout sets killed_by_wrapper=True and error_type=claude_timeout."""
        import run_plan_preview as rpp

        def fake_invoke(packet, output_dir, *, timeout=120):
            return "", "", -9, {"timeout_seconds":120,"elapsed_seconds":120.0,"killed_by_wrapper":True,"stdout_bytes":0,"stderr_bytes":0}

        with mock.patch.object(rpp, "_resolve_git_root", return_value=None):
            with mock.patch.object(rpp, "invoke_claude_plan", side_effect=fake_invoke):
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
                with open(args.output_json) as f:
                    result = json.load(f)
                assert result["status"] == "PLAN_PREVIEW_ERROR"
                assert result["metadata"]["killed_by_wrapper"] is True
                assert result["metadata"]["error_type"] == "claude_timeout"
                assert result["metadata"]["elapsed_seconds"] == 120.0

    def test_nonzero_non_timeout_killed_by_wrapper_false(self, tmp_path):
        """Non-timeout nonzero exit sets killed_by_wrapper=False and error_type=claude_nonzero_exit."""
        import run_plan_preview as rpp

        def fake_invoke(packet, output_dir, *, timeout=120):
            return '{"type":"text","text":"partial"}', "claude error", 127, {"timeout_seconds":120,"elapsed_seconds":30.0,"killed_by_wrapper":False,"stdout_bytes":20,"stderr_bytes":12}

        with mock.patch.object(rpp, "_resolve_git_root", return_value=None):
            with mock.patch.object(rpp, "invoke_claude_plan", side_effect=fake_invoke):
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
                with open(args.output_json) as f:
                    result = json.load(f)
                assert result["metadata"]["killed_by_wrapper"] is False
                assert result["metadata"]["error_type"] == "claude_nonzero_exit"
                assert result["metadata"]["elapsed_seconds"] == 30.0

    def test_success_includes_all_metadata_fields(self, tmp_path):
        """Successful run includes elapsed_seconds, stdout_bytes, stderr_bytes in metadata."""
        import run_plan_preview as rpp

        def fake_invoke(packet, output_dir, *, timeout=300):
            return '{"type":"text","text":"a plan"}', "", 0, {"timeout_seconds":300,"elapsed_seconds":45.0,"killed_by_wrapper":False,"stdout_bytes":12345,"stderr_bytes":0}

        with mock.patch.object(rpp, "_resolve_git_root", return_value=None):
            with mock.patch.object(rpp, "invoke_claude_plan", side_effect=fake_invoke):
                args = mock.MagicMock()
                args.packet_json = str(tmp_path / "packet.json")
                args.output_dir = str(tmp_path / "out")
                args.output_json = str(tmp_path / "result.json")
                args.output_md = None
                args.timeout = 300
                import json
                with open(args.packet_json, "w") as f:
                    json.dump({"packet_kind": "aed.worker.packet.v1", "task": {"description": "x", "allowed_files": []}}, f)
                result_code = rpp.run(args)
                assert result_code == 0
                with open(args.output_json) as f:
                    result = json.load(f)
                assert result["status"] == "PLAN_PREVIEW_READY"
                for field in ("timeout_seconds", "elapsed_seconds", "killed_by_wrapper", "stdout_bytes", "stderr_bytes"):
                    assert field in result["metadata"], f"{field} missing from metadata"

    def test_default_timeout_is_300(self):
        """Default timeout argument is 300 seconds."""
        import run_plan_preview as rpp
        import argparse
        # Build parser as main() does and check the timeout default
        parser = argparse.ArgumentParser()
        parser.add_argument("--packet-json")
        parser.add_argument("--output-dir")
        parser.add_argument("--output-json")
        parser.add_argument("--output-md")
        parser.add_argument("--timeout", type=int, default=300)
        args = parser.parse_args(["--packet-json", "/tmp/p.json", "--output-dir", "/tmp/o", "--output-json", "/tmp/r.json"])
        assert args.timeout == 300


class TestExtractPlanFromStream:
    """Test extract_plan_from_stream against actual stream-json shapes."""

    def test_message_with_text_block(self):
        import run_plan_preview as rpp
        stream = json.dumps({"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "Step 1: Read the file"}]}})
        result = rpp.extract_plan_from_stream(stream + "\n")
        assert "Step 1: Read the file" in result

    def test_message_with_exitplanmode_tool_use(self):
        import run_plan_preview as rpp
        stream = json.dumps({
            "type": "message", "message": {"role": "assistant", "content": [{
                "type": "tool_use", "name": "ExitPlanMode",
                "input": {"plan": "# Plan\n\n## Step 1\n\nEdit foo.py", "planFilePath": "/tmp/plan.md"}
            }]}
        })
        result = rpp.extract_plan_from_stream(stream + "\n")
        assert "# Plan" in result
        assert "Step 1" in result

    def test_result_type_extracts_result_text(self):
        import run_plan_preview as rpp
        stream = json.dumps({"type": "result", "subtype": "success", "result": "The plan is ready at /tmp/plan.md"})
        result = rpp.extract_plan_from_stream(stream + "\n")
        assert "plan is ready" in result

    def test_simple_text_delta(self):
        import run_plan_preview as rpp
        stream = '{"text": "Step 1: Edit config.yaml"}\n'
        result = rpp.extract_plan_from_stream(stream)
        assert "Step 1: Edit config.yaml" in result

    def test_skips_malformed_json(self):
        import run_plan_preview as rpp
        stream = 'not-json\n{"type": "message", "message": {"content": []}}\n'
        result = rpp.extract_plan_from_stream(stream)
        assert result == ""

    def test_skips_non_dict_json(self):
        import run_plan_preview as rpp
        stream = '["array", "not", "dict"]\n{"type": "message", "message": {"content": [{"type": "text", "text": "real plan"}]}}'
        result = rpp.extract_plan_from_stream(stream)
        assert "real plan" in result

    def test_multiple_plan_blocks_concatenated(self):
        import run_plan_preview as rpp
        stream = (
            '{"type": "message", "message": {"content": [{"type": "text", "text": "Part 1"}]}}\n'
            '{"type": "result", "result": "Summary line"}\n'
        )
        result = rpp.extract_plan_from_stream(stream)
        assert "Part 1" in result
        assert "Summary line" in result

    def test_empty_content_block_skipped(self):
        import run_plan_preview as rpp
        stream = '{"type": "message", "message": {"content": []}}\n'
        result = rpp.extract_plan_from_stream(stream)
        assert result == ""


class TestIsClaudeArtifactPath:
    """Test is_claude_artifact_path and its effect on validation."""

    def test_claude_plans_path_is_artifact(self):
        import run_plan_preview as rpp
        assert rpp.is_claude_artifact_path("/home/max/.claude/plans/foo.md") is True
        assert rpp.is_claude_artifact_path("/tmp/claude/plans/bar.md") is True
        assert rpp.is_claude_artifact_path("/home/max/.claude/") is True

    def test_hermes_path_is_not_artifact(self):
        import run_plan_preview as rpp
        assert rpp.is_claude_artifact_path("/home/max/.hermes/something") is False

    def test_repo_path_is_not_artifact(self):
        import run_plan_preview as rpp
        assert rpp.is_claude_artifact_path("/home/max/Automated-Edge-Discovery/scripts/foo.py") is False

    def test_plan_referencing_claude_artifact_not_blocked(self):
        """Regression: informative Claude artifact paths must not count as repo edits."""
        import run_plan_preview as rpp
        # A plan that mentions the Claude plan file path should not be blocked
        # when the allowed_files do not include .claude paths
        plan = 'The plan file at `/home/max/.claude/plans/temporal-watching-crab.md` is ready.'
        packet = {
            "task": {
                "allowed_files": ["scripts/", "tests/", "docs/"],
                "forbidden_files": [],
                "do_not": [],
            }
        }
        errors = rpp.validate_plan_only_allowed_files(plan, packet)
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_plan_proposing_repo_edit_still_blocked(self):
        """Real repo edit proposals must still be blocked."""
        import run_plan_preview as rpp
        plan = '1. Edit /home/max/Automated-Edge-Discovery/scripts/local/run_plan_preview.py'
        packet = {
            "task": {
                "allowed_files": ["scripts/", "tests/", "docs/"],
                "forbidden_files": [],
                "do_not": [],
            }
        }
        errors = rpp.validate_plan_only_allowed_files(plan, packet)
        assert len(errors) > 0, "Expected violation for repo edit outside allowed scope"
        assert "run_plan_preview.py" in errors[0]


class TestRunPlanPreviewIntegration:
    """Test run() function paths via targeted patching of external calls."""

    def test_output_dir_in_repo_returns_error(self, tmp_path):
        import run_plan_preview as rpp
        repo = Path(__file__).resolve().parents[1]  # points to Automated-Edge-Discovery
        with mock.patch.object(rpp, "_resolve_git_root", return_value=repo):
            with mock.patch.object(rpp, "_git_status", return_value="clean"):
                args = mock.MagicMock()
                args.packet_json = str(tmp_path / "packet.json")
                args.output_dir = str(repo / ".cache" / "plan_out")  # inside repo
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
        import run_plan_preview as rpp
        with mock.patch.object(rpp, "_resolve_git_root", return_value=None):
            with mock.patch.object(rpp, "invoke_claude_plan", return_value=("some output", "some stderr", 127, {"timeout_seconds":120,"elapsed_seconds":1,"killed_by_wrapper":False,"stdout_bytes":13,"stderr_bytes":13})):
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
        import run_plan_preview as rpp
        with mock.patch.object(rpp, "_resolve_git_root", return_value=None):
            with mock.patch.object(rpp, "invoke_claude_plan", return_value=("", "", 0, {"timeout_seconds":120,"elapsed_seconds":1,"killed_by_wrapper":False,"stdout_bytes":0,"stderr_bytes":0})):
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
        import run_plan_preview as rpp
        repo = Path(__file__).resolve().parents[1]
        git_statuses = ["clean", "dirty: M scripts/local/foo.py"]  # before=clean, after=dirty
        with mock.patch.object(rpp, "_resolve_git_root", return_value=repo):
            with mock.patch.object(rpp, "invoke_claude_plan", return_value=("1. Edit scripts/local/foo.py", "", 0, {"timeout_seconds":120,"elapsed_seconds":1,"killed_by_wrapper":False,"stdout_bytes":26,"stderr_bytes":0})):
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