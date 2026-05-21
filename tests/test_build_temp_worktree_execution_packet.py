#!/usr/bin/env python3
"""
Tests for build_temp_worktree_execution_packet.py
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path("/home/max/Automated-Edge-Discovery")
SCRIPT = REPO_ROOT / "scripts/local/build_temp_worktree_execution_packet.py"


# ---------------------------------------------------------------------------
# Helpers — each helper creates a UNIQUE file per call
# ---------------------------------------------------------------------------

def run_bridge(argv):
    """Run the bridge script. Returns (exitcode, stdout, stderr)."""
    cmd = [sys.executable, str(SCRIPT)] + argv
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout, r.stderr


def plan_file(tmp_path, content="Plan content"):
    """Create a unique plan file."""
    p = tmp_path / f"plan_{id(content)}.txt"
    p.write_text(content, encoding="utf-8")
    return str(p)


def allowed_json(tmp_path, data):
    """Create a unique allowed_files JSON file."""
    p = tmp_path / "allowed_files.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def forbidden_json(tmp_path, data):
    """Create a unique forbidden_files JSON file."""
    p = tmp_path / "forbidden_files.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def do_not_json(tmp_path, data):
    """Create a unique do_not JSON file."""
    p = tmp_path / "do_not.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def mock_edits_json(tmp_path, data):
    """Create a unique mock_edits JSON file."""
    p = tmp_path / "mock_edits.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def output_json(tmp_path):
    """Unique output JSON path."""
    return str(tmp_path / "packet.json")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBasicPacket:
    """Tests 1-4: Basic valid inputs produce correct packet."""

    def test_packet_kind_is_correct(self, tmp_path):
        """Test 1: packet_kind is aed.temp_worktree.execution.v0."""
        out = output_json(tmp_path)
        ec, out_s, err = run_bridge([
            "--run-id", "test_001", "--task-id", "TASK-001",
            "--task-description", "Test task",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, ["README.md"]),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
        ])
        assert ec == 0, f"exit {ec}: {err}"
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert data["packet_kind"] == "aed.temp_worktree.execution.v0"

    def test_approved_plan_sha256_matches_file(self, tmp_path):
        """Test 2: approved_plan_sha256 matches file content."""
        plan = plan_file(tmp_path, "Exact content here")
        expected_sha = sha256_file(plan)
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_002", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan,
            "--allowed-files-json", allowed_json(tmp_path, []),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
        ])

        assert ec == 0, f"exit {ec}: {err}"
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert data["approved_plan_sha256"] == expected_sha

    def test_approval_sha256_matches_top_level(self, tmp_path):
        """Test 3: approval.approved_plan_sha256 matches top-level field."""
        plan = plan_file(tmp_path)
        expected_sha = sha256_file(plan)
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_003", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan,
            "--allowed-files-json", allowed_json(tmp_path, []),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
        ])

        assert ec == 0, f"exit {ec}: {err}"
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert data["approval"]["approved_plan_sha256"] == data["approved_plan_sha256"]
        assert data["approval"]["approved_plan_sha256"] == expected_sha

    def test_execution_mode_is_mock(self, tmp_path):
        """Test 4: execution.mode is mock."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_004", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, ["README.md"]),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
        ])

        assert ec == 0, f"exit {ec}: {err}"
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert data["execution"]["mode"] == "mock"


class TestBaseSha:
    """Tests 5-6: base_sha handling."""

    def test_supplied_base_sha_is_used(self, tmp_path):
        """Test 6: supplied base_sha is used."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_006", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, []),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
            "--base-sha", "deadbeef1234567890abcdef",
        ])

        assert ec == 0, f"exit {ec}: {err}"
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert data["base_sha"] == "deadbeef1234567890abcdef"

    def test_base_sha_defaults_to_git_head(self, tmp_path):
        """Test 5: base_sha defaults to git rev-parse HEAD."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_005", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, []),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
            # no --base-sha — should default to git HEAD
        ])

        assert ec == 0, f"exit {ec}: {err}"
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        # Verify it's a valid 40-char SHA
        assert len(data["base_sha"]) == 40
        assert all(c in "0123456789abcdef" for c in data["base_sha"])


class TestConstraintFiles:
    """Tests 7-9: constraint file validation."""

    def test_allowed_files_json_must_be_list(self, tmp_path):
        """Test 7: allowed_files_json must be a JSON list."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_007", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, {"not": "list"}),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
        ])

        assert ec == 1
        assert "must be a JSON list" in err

    def test_allowed_files_items_must_be_strings(self, tmp_path):
        """Test 7b: allowed_files items must be strings."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_007b", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, ["ok", 123, "also_ok"]),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
        ])

        assert ec == 1
        assert "must contain only strings" in err

    def test_forbidden_files_json_must_be_list(self, tmp_path):
        """Test 8: forbidden_files_json must be a JSON list."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_008", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, []),
            "--forbidden-files-json", forbidden_json(tmp_path, {"not": "list"}),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
        ])

        assert ec == 1
        assert "must be a JSON list" in err

    def test_do_not_json_must_be_list(self, tmp_path):
        """Test 9: do_not_json must be a JSON list."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_009", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, []),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, "not a list"),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
        ])

        assert ec == 1
        assert "must be a JSON list" in err


class TestErrorCases:
    """Tests 10-12: error cases."""

    def test_missing_approved_plan_path_fails(self, tmp_path):
        """Test 10: missing approved_plan_path fails."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_010", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", "/nonexistent/plan.txt",
            "--allowed-files-json", allowed_json(tmp_path, []),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
        ])

        assert ec == 1
        assert "not found" in err.lower()

    def test_output_root_inside_repo_fails(self, tmp_path):
        """Test 11: output_root inside repo fails (strict)."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_011", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, []),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            # output-root inside repo — should fail
            "--output-root", str(REPO_ROOT / "scripts" / "local"),
            "--output-json", out,
        ])

        assert ec == 1
        assert "cannot be inside repo" in err

    def test_mock_edits_missing_content_field(self, tmp_path):
        """Test 12: mock_edits entry missing content field fails."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_012", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, ["README.md"]),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
            "--mock-edits-json", mock_edits_json(tmp_path, [
                {"path": "README.md", "content": "Updated!"},
                {"path": "bad"},  # missing content
            ]),
        ])

        assert ec == 1
        assert "missing required field: content" in err

    def test_mock_edits_empty_path_fails(self, tmp_path):
        """Test 12b: mock_edits path must be non-empty string."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_012b", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, ["README.md"]),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
            "--mock-edits-json", mock_edits_json(tmp_path, [
                {"path": "", "content": "content"},
            ]),
        ])

        assert ec == 1
        assert "non-empty string" in err

    def test_mock_edits_content_not_string_fails(self, tmp_path):
        """Test 12c: mock_edits content must be string."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_012c", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, ["README.md"]),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
            "--mock-edits-json", mock_edits_json(tmp_path, [
                {"path": "README.md", "content": 12345},  # content not string
            ]),
        ])

        assert ec == 1
        assert "must be a string" in err


class TestNoOpBehavior:
    """Tests 13-14: no worktree, no harness execution."""

    def test_no_worktree_created(self, tmp_path):
        """Test 13: bridge creates no worktree."""
        out = output_json(tmp_path)

        ec, _, _ = run_bridge([
            "--run-id", "test_013_nowt", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, ["README.md"]),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
        ])

        assert ec == 0
        worktrees = Path("/tmp/aed_runs/worktrees")
        if worktrees.exists():
            assert not (worktrees / "test_013_nowt").exists()

    def test_bridge_outputs_only_success_message(self, tmp_path):
        """Test 14: bridge output contains only success message."""
        out = output_json(tmp_path)

        ec, stdout, stderr = run_bridge([
            "--run-id", "test_014", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, ["README.md"]),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
        ])

        assert ec == 0
        assert "Packet written to" in stdout
        assert stderr == ""

    def test_mock_edits_included_when_supplied(self, tmp_path):
        """Mock edits appear in packet when --mock-edits-json is provided."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_mocks", "--task-id", "TASK-001",
            "--task-description", "Test with mock edits",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, ["README.md"]),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
            "--mock-edits-json", mock_edits_json(tmp_path, [
                {"path": "README.md", "content": "# Hello\n"},
            ]),
        ])

        assert ec == 0, f"exit {ec}: {err}"
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert "mock_edits" in data["execution"]
        assert data["execution"]["mock_edits"] == [{"path": "README.md", "content": "# Hello\n"}]

    def test_no_mock_edits_when_not_supplied(self, tmp_path):
        """Packet omits mock_edits when --mock-edits-json is not given."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_nomocks", "--task-id", "TASK-001",
            "--task-description", "Test without mock edits",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, ["README.md"]),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
        ])

        assert ec == 0, f"exit {ec}: {err}"
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert "mock_edits" not in data["execution"]


class TestSecurityStrings:
    """Tests 16-17: no dangerous strings in source."""

    def test_no_dangerous_strings_in_source(self):
        """Test 16: no git push, gh pr, dispatch, board, Hermes, etc. in source."""
        src = SCRIPT.read_text(encoding="utf-8")
        forbidden = [
            "git push", "gh pr create", "gh pr merge",
            "dispatch", "board",
            "shell=True",
        ]
        for term in forbidden:
            assert term not in src, f"Forbidden term in source: {term!r}"

    def test_no_real_execution_mode_in_source(self):
        """Test 17: no Claude invocation, no real execution mode strings."""
        src = SCRIPT.read_text(encoding="utf-8")
        forbidden = [
            "claude", "anthropic", "OpenAI", "openai",
        ]
        for term in forbidden:
            assert term not in src, f"Found '{term}' in source — real execution not allowed"

    def test_no_package_install_in_source(self):
        """Test: no package install strings in source."""
        src = SCRIPT.read_text(encoding="utf-8")
        assert "pip install" not in src.lower()
        assert "apt install" not in src.lower()
        assert "npm install" not in src.lower()


class TestSchemaCompleteness:
    """Verify packet has all fields required by run_temp_worktree_execution.py."""

    def test_all_required_fields_present(self, tmp_path):
        """Packet has all required top-level, approval, task, execution fields."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_schema", "--task-id", "TASK-001",
            "--task-description", "Full schema test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, ["README.md"]),
            "--forbidden-files-json", forbidden_json(tmp_path, ["scripts/local/final_gate_status.py"]),
            "--do-not-json", do_not_json(tmp_path, ["do not hack"]),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
            "--max-changed-files", "3",
        ])

        assert ec == 0, f"exit {ec}: {err}"
        data = json.loads(Path(out).read_text(encoding="utf-8"))

        # Top-level
        for field in ["packet_kind", "run_id", "task_id", "base_sha",
                      "approved_plan_path", "approved_plan_sha256"]:
            assert field in data, f"Missing: {field}"

        # Approval
        for field in ["approved_for_temp_worktree_execution", "approved_by",
                      "approved_plan_sha256", "approved_at", "max_changed_files"]:
            assert field in data["approval"], f"Missing approval: {field}"

        # Task
        for field in ["description", "allowed_files", "forbidden_files", "do_not"]:
            assert field in data["task"], f"Missing task: {field}"

        # Execution
        for field in ["mode", "output_root", "timeout_seconds"]:
            assert field in data["execution"], f"Missing execution: {field}"

        # Values
        assert data["approval"]["approved_for_temp_worktree_execution"] is True
        assert data["approval"]["approved_by"] == "human"
        assert data["approval"]["max_changed_files"] == 3
        assert data["execution"]["mode"] == "mock"
        assert data["execution"]["timeout_seconds"] == 60

    def test_approved_at_is_valid_iso_timestamp(self, tmp_path):
        """approved_at is a valid ISO-8601 UTC timestamp."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_ts", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, []),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
        ])

        assert ec == 0, f"exit {ec}: {err}"
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        ts = data["approval"]["approved_at"]
        # Should be ISO format ending in Z
        assert ts.endswith("Z"), f"approved_at should end in Z: {ts}"
        from datetime import datetime, timezone
        datetime.fromisoformat(ts.replace("Z", "+00:00"))  # raises if invalid


class TestMaxChangedFiles:
    """Test --max-changed-files option."""

    def test_max_changed_files_default(self, tmp_path):
        """Default max_changed_files is 2."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_max", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, []),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
        ])

        assert ec == 0, f"exit {ec}: {err}"
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert data["approval"]["max_changed_files"] == 2

    def test_max_changed_files_custom(self, tmp_path):
        """Custom max_changed_files is respected."""
        out = output_json(tmp_path)

        ec, _, err = run_bridge([
            "--run-id", "test_max", "--task-id", "TASK-001",
            "--task-description", "Test",
            "--approved-plan-path", plan_file(tmp_path),
            "--allowed-files-json", allowed_json(tmp_path, []),
            "--forbidden-files-json", forbidden_json(tmp_path, []),
            "--do-not-json", do_not_json(tmp_path, []),
            "--output-root", str(tmp_path / "out"),
            "--output-json", out,
            "--max-changed-files", "10",
        ])

        assert ec == 0, f"exit {ec}: {err}"
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert data["approval"]["max_changed_files"] == 10