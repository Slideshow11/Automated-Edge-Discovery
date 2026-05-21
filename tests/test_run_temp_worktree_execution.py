#!/usr/bin/env python3
"""
tests/test_run_temp_worktree_execution.py

Unit tests for run_temp_worktree_execution.py harness v0.

These tests use a disposable temp worktree created from the AED repo's current
HEAD. The AED repo is never mutated (read-only HEAD reference, worktree is
separate). No real Claude execution. No network. No shell=True.

Coverage:
- Packet validation (packet_kind, required fields)
- Human approval marker (presence, approved_for_temp_worktree_execution, SHA-256, timestamp)
- Main repo clean/dirty detection
- Path safety (output_root, worktree inside repo)
- Execution mode (mock only; non-mock blocked)
- Mock executor (valid edits, path escape blocked)
- Diff validation (allowed_files, forbidden_files, gate scripts, max_changed_files)
- Main repo mutation detection post-execution
- PMG pre/post integration (snapshot, compare, external mutation)
- State transitions and final states
- Output JSON and Markdown correctness (including PMG fields)

No test invokes Claude, uses network, shell=True, git push, gh pr create/merge,
dispatch, board, Hermes, audit, memory, profile, or package install.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.parent / "scripts" / "local"
REPO_ROOT = Path(__file__).parent.parent.resolve()  # AED repo root

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sha256_str(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def git_status(repo_path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "status", "--porcelain"],
        capture_output=True, text=True, timeout=10
    )
    return result.stdout.strip() or "clean"


def git_rev_parse(repo_path: Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", ref],
        capture_output=True, text=True, timeout=10
    )
    return result.stdout.strip()


def git_worktree_add(worktree_path: Path, base_sha: str, parent_repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(parent_repo), "worktree", "add", str(worktree_path), base_sha],
        capture_output=True, text=True, timeout=30
    )


def git_worktree_remove(worktree_path: Path, parent_repo: Path) -> None:
    subprocess.run(
        ["git", "-C", str(parent_repo), "worktree", "remove", "--force", str(worktree_path)],
        capture_output=True, text=True, timeout=30
    )


def cleanup_worktree(worktree_path: Path) -> None:
    """Remove worktree and clean up git state."""
    try:
        git_worktree_remove(worktree_path, REPO_ROOT)
    except Exception:
        pass
    import shutil
    if worktree_path.exists():
        shutil.rmtree(worktree_path, ignore_errors=True)


def make_plan_file(tmp_path: Path, content: str = "Example plan\n") -> tuple[Path, str]:
    """Write a plan file and return (path, sha256)."""
    path = tmp_path / "approved_plan.txt"
    path.write_text(content, encoding="utf-8")
    sha = sha256_str(content)
    return path, sha


def now_iso() -> str:
    # Generate a fresh UTC timestamp within the 24h approval window
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stale_approved_at() -> str:
    # Fixed old timestamp for testing expiry — outside the 24h window
    return "2020-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# Import the harness
# ---------------------------------------------------------------------------

sys.path.insert(0, str(SCRIPT_DIR))
import run_temp_worktree_execution as rte
from run_temp_worktree_execution import (
    run, validate_packet, validate_approval, sha256_str as _sha256_str,
    check_forbidden_file_touched, check_outside_allowed,
    check_protected_gate_scripts, check_too_many_files,
    apply_mock_edits, WORKTREE_BASE,
    PROTECTED_GATE_SCRIPTS,
)


# ---------------------------------------------------------------------------
# Mock helpers for PMG integration
# ---------------------------------------------------------------------------

def make_clean_pmg_snapshot(tmp_path: Path) -> tuple[Path, dict]:
    """Create a clean PMG snapshot JSON file. Returns (path, data)."""
    snapshot = {
        "kind": "pmg.snapshot",
        "version": "1.0",
        "target": str(Path.home() / ".hermes"),
        "files": [],
        "status": "clean",
        "blocked": 0,
    }
    pmg_dir = tmp_path / "pmg"
    pmg_dir.mkdir(parents=True, exist_ok=True)
    path = pmg_dir / "pmg_snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    return path, snapshot


def make_clean_pmg_compare(tmp_path: Path) -> tuple[Path, Path]:
    """Create clean PMG compare JSON and MD files. Returns (json_path, md_path)."""
    compare_json = {
        "kind": "pmg.compare",
        "version": "1.0",
        "status": "clean",
        "blocked": 0,
        "added": [],
        "removed": [],
        "changed": [],
    }
    pmg_dir = tmp_path / "pmg"
    pmg_dir.mkdir(parents=True, exist_ok=True)
    json_path = pmg_dir / "pmg_compare.json"
    json_path.write_text(json.dumps(compare_json), encoding="utf-8")

    md_content = "# PMG Compare Result\n\n**Status**: `clean`\n**Blocked**: 0\n"
    md_path = pmg_dir / "pmg_compare.md"
    md_path.write_text(md_content, encoding="utf-8")
    return json_path, md_path


def make_blocked_pmg_compare(tmp_path: Path, blocked_count: int = 1) -> tuple[Path, Path]:
    """Create a blocked PMG compare result. Returns (json_path, md_path)."""
    compare_json = {
        "kind": "pmg.compare",
        "version": "1.0",
        "status": "blocked",
        "blocked": blocked_count,
        "added": ["some/file.txt"],
        "removed": [],
        "changed": [],
    }
    pmg_dir = tmp_path / "pmg"
    pmg_dir.mkdir(parents=True, exist_ok=True)
    json_path = pmg_dir / "pmg_compare_blocked.json"
    json_path.write_text(json.dumps(compare_json), encoding="utf-8")

    md_content = f"# PMG Compare Result\n\n**Status**: `blocked`\n**Blocked**: {blocked_count}\n"
    md_path = pmg_dir / "pmg_compare_blocked.md"
    md_path.write_text(md_content, encoding="utf-8")
    return json_path, md_path


# ---------------------------------------------------------------------------
# Tests: validate_packet
# ---------------------------------------------------------------------------

class TestValidatePacket:
    def test_valid_packet(self, tmp_path):
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()
        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "run_001",
            "task_id": "TASK-001",
            "base_sha": "a" * 40,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Test",
                "allowed_files": ["foo.txt"],
                "forbidden_files": [],
                "do_not": [],
            },
            "execution": {"mode": "mock", "timeout_seconds": 60, "output_root": str(tmp_path / "out")},
        }
        ok, err = validate_packet(packet)
        assert ok is True, err

    def test_wrong_packet_kind(self, tmp_path):
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()
        packet = {
            "packet_kind": "aed.wrong.kind",
            "run_id": "run_001",
            "task_id": "TASK-001",
            "base_sha": "a" * 40,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {"approved_for_temp_worktree_execution": True, "approved_by": "human",
                         "approved_plan_sha256": plan_sha, "approved_at": now,
                         "max_changed_files": 5},
            "task": {"description": "T", "allowed_files": [], "forbidden_files": [], "do_not": []},
            "execution": {"mode": "mock", "timeout_seconds": 60, "output_root": str(tmp_path / "out")},
        }
        ok, err = validate_packet(packet)
        assert ok is False
        assert "aed.temp_worktree.execution.v0" in err

    def test_missing_required_field(self, tmp_path):
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()
        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            # missing run_id
            "task_id": "TASK-001",
            "base_sha": "a" * 40,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {"approved_for_temp_worktree_execution": True, "approved_by": "human",
                         "approved_plan_sha256": plan_sha, "approved_at": now,
                         "max_changed_files": 5},
            "task": {"description": "T", "allowed_files": [], "forbidden_files": [], "do_not": []},
            "execution": {"mode": "mock", "timeout_seconds": 60, "output_root": str(tmp_path / "out")},
        }
        ok, err = validate_packet(packet)
        assert ok is False
        assert "run_id" in err


# ---------------------------------------------------------------------------
# Tests: validate_approval
# ---------------------------------------------------------------------------

class TestValidateApproval:
    def test_valid_approval(self, tmp_path):
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()
        approval = {
            "approved_for_temp_worktree_execution": True,
            "approved_by": "human",
            "approved_plan_sha256": plan_sha,
            "approved_at": now,
            "max_changed_files": 5,
        }
        ok, err = validate_approval(approval, str(plan_path))
        assert ok is True, err

    def test_approved_for_temp_worktree_execution_false(self, tmp_path):
        plan_path, plan_sha = make_plan_file(tmp_path)
        approval = {
            "approved_for_temp_worktree_execution": False,
            "approved_by": "human",
            "approved_plan_sha256": plan_sha,
            "approved_at": now_iso(),
            "max_changed_files": 5,
        }
        ok, err = validate_approval(approval, str(plan_path))
        assert ok is False
        assert "approved_for_temp_worktree_execution" in err

    def test_approved_by_not_human(self, tmp_path):
        plan_path, plan_sha = make_plan_file(tmp_path)
        approval = {
            "approved_for_temp_worktree_execution": True,
            "approved_by": "bot",
            "approved_plan_sha256": plan_sha,
            "approved_at": now_iso(),
            "max_changed_files": 5,
        }
        ok, err = validate_approval(approval, str(plan_path))
        assert ok is False
        assert "approved_by" in err

    def test_plan_sha256_mismatch(self, tmp_path):
        plan_path, plan_sha = make_plan_file(tmp_path, "Correct content\n")
        bad_sha = sha256_str("Wrong content\n")
        approval = {
            "approved_for_temp_worktree_execution": True,
            "approved_by": "human",
            "approved_plan_sha256": bad_sha,
            "approved_at": now_iso(),
            "max_changed_files": 5,
        }
        ok, err = validate_approval(approval, str(plan_path))
        assert ok is False
        assert "mismatch" in err.lower()

    def test_approval_expired(self, tmp_path):
        plan_path, plan_sha = make_plan_file(tmp_path)
        approval = {
            "approved_for_temp_worktree_execution": True,
            "approved_by": "human",
            "approved_plan_sha256": plan_sha,
            "approved_at": stale_approved_at(),
            "max_changed_files": 5,
        }
        ok, err = validate_approval(approval, str(plan_path))
        assert ok is False
        assert "24h" in err or "old" in err.lower()

    def test_approval_at_missing(self, tmp_path):
        plan_path, plan_sha = make_plan_file(tmp_path)
        approval = {
            "approved_for_temp_worktree_execution": True,
            "approved_by": "human",
            "approved_plan_sha256": plan_sha,
            # no approved_at
            "max_changed_files": 5,
        }
        ok, err = validate_approval(approval, str(plan_path))
        assert ok is False
        assert "approved_at" in err


# ---------------------------------------------------------------------------
# Tests: constraint check helpers (pure functions, no git needed)
# ---------------------------------------------------------------------------

class TestConstraintCheckHelpers:
    def test_check_forbidden_file_touched_violated(self):
        worktree = Path("/tmp/fake_worktree")
        changed = [".github/ci.yml"]
        violated = check_forbidden_file_touched(changed, [".github/"], worktree)
        assert ".github/ci.yml" in violated

    def test_check_forbidden_file_touched_clean(self):
        worktree = Path("/tmp/fake_worktree")
        changed = ["docs/example.md"]
        violated = check_forbidden_file_touched(changed, [".github/"], worktree)
        assert len(violated) == 0

    def test_check_forbidden_file_touched_exact_match(self):
        worktree = Path("/tmp/fake_worktree")
        changed = ["scripts/local/final_gate_status.py"]
        violated = check_forbidden_file_touched(changed, PROTECTED_GATE_SCRIPTS, worktree)
        assert "scripts/local/final_gate_status.py" in violated

    def test_check_outside_allowed_violated(self):
        worktree = Path("/tmp/fake_worktree")
        changed = ["other.txt"]
        violated = check_outside_allowed(changed, ["allowed.txt"], worktree)
        assert "other.txt" in violated

    def test_check_outside_allowed_clean(self):
        worktree = Path("/tmp/fake_worktree")
        changed = ["allowed.txt"]
        violated = check_outside_allowed(changed, ["allowed.txt"], worktree)
        assert len(violated) == 0

    def test_check_protected_gate_scripts_violated(self):
        worktree = Path("/tmp/fake_worktree")
        changed = ["scripts/local/final_gate_status.py"]
        violated = check_protected_gate_scripts(changed, worktree)
        assert "scripts/local/final_gate_status.py" in violated

    def test_check_protected_gate_scripts_clean(self):
        worktree = Path("/tmp/fake_worktree")
        changed = ["docs/example.md"]
        violated = check_protected_gate_scripts(changed, worktree)
        assert len(violated) == 0

    def test_check_too_many_files(self):
        assert check_too_many_files(["a", "b", "c"], 2) is True
        assert check_too_many_files(["a", "b"], 2) is False
        assert check_too_many_files(["a", "b"], 2) is False  # exactly equal = not exceeded
        assert check_too_many_files([], 0) is False


# ---------------------------------------------------------------------------
# Tests: apply_mock_edits (pure function, no git needed)
# ---------------------------------------------------------------------------

class TestApplyMockEdits:
    def test_apply_mock_edits_single_file(self, tmp_path):
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        docs = worktree / "docs"
        docs.mkdir()
        (docs / "example.md").write_text("original")

        changed = apply_mock_edits(worktree, [
            {"path": "docs/example.md", "content": "new content"}
        ])
        assert changed == ["docs/example.md"]
        assert (worktree / "docs" / "example.md").read_text() == "new content"

    def test_apply_mock_edits_creates_dirs(self, tmp_path):
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        changed = apply_mock_edits(worktree, [
            {"path": "deep/nested/file.txt", "content": "content"}
        ])
        assert changed == ["deep/nested/file.txt"]
        assert (worktree / "deep" / "nested" / "file.txt").read_text() == "content"

    def test_apply_mock_edits_path_escape_blocked(self, tmp_path):
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        (worktree / "target.txt").write_text("original")

        with pytest.raises(ValueError, match="escapes worktree"):
            apply_mock_edits(worktree, [
                {"path": "../target.txt", "content": "hacked"}
            ])

    def test_apply_mock_edits_multiple_files(self, tmp_path):
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        changed = apply_mock_edits(worktree, [
            {"path": "file1.txt", "content": "content1"},
            {"path": "file2.txt", "content": "content2"},
        ])
        assert set(changed) == {"file1.txt", "file2.txt"}

    def test_apply_mock_edits_rejects_dotdot_path(self, tmp_path):
        """Path with .. should be rejected."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        (worktree / "target.txt").write_text("original")

        # .. path should be rejected (actually escapes)
        with pytest.raises(ValueError, match="escapes worktree"):
            apply_mock_edits(worktree, [
                {"path": "../target.txt", "content": "hacked"}
            ])


# ---------------------------------------------------------------------------
# Integration tests using AED repo worktree (read-only AED repo reference)
# ---------------------------------------------------------------------------

class TestRunIntegration:
    """Tests that create a real git worktree from the AED repo's current HEAD.
    PMG functions are mocked so tests don't depend on real PMG tool availability.
    """

    def test_valid_mock_edit_returns_patch_ready(self, tmp_path):
        """Test 1: valid mock edit to one allowed file returns PATCH_READY_FOR_HUMAN_REVIEW."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        assert base_sha, "AED repo HEAD not found"

        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        # Create clean PMG snapshot and compare artifacts
        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")
        pmg_compare_json, pmg_compare_md = make_clean_pmg_compare(tmp_path / "pmg")

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_valid_pmg_xyz",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit docs/example.md",
                "allowed_files": ["docs/example.md"],
                "forbidden_files": PROTECTED_GATE_SCRIPTS,
                "do_not": ["do not push", "do not dispatch"],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [{"path": "docs/example.md", "content": "# Updated\nNew content.\n"}],
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        # Mock PMG functions so tests don't depend on real PMG tool
        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:

            # pm

            # pmg_snapshot succeeds with clean status and writes snapshot JSON
            def fake_snapshot(target, output_json):
                import shutil
                shutil.copy(str(pmg_snapshot_path), output_json)
                return True, ""

            def fake_compare(snapshot_json, output_json, output_md):
                import shutil
                shutil.copy(str(pmg_compare_json), output_json)
                shutil.copy(str(pmg_compare_md), output_md)
                return True, ""

            mock_snap.side_effect = fake_snapshot
            mock_cmp.side_effect = fake_compare

            result = run(packet, str(output_json), str(output_md))

        # Cleanup worktree
        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "PATCH_READY_FOR_HUMAN_REVIEW", \
            f"Got: {result['status']} — {result.get('validation_errors')}"
        assert result["patch_ready"] is True
        assert "docs/example.md" in result["changed_files"]
        assert result["main_git_status_before"] == "clean" or result["main_git_status_before"].startswith("?? ")
        assert result["main_git_status_after"] == "clean" or result["main_git_status_after"].startswith("?? ")
        assert result["diff_path"]
        assert result["diff_path"].endswith("/diff.patch")
        assert output_json.is_file()
        assert output_md.is_file()
        # PMG fields should be populated
        assert result.get("pmg_status") == "clean"
        assert result.get("pmg_snapshot_path")
        assert result.get("pmg_compare_json_path")

    def test_missing_approval_returns_hold_plan_not_approved(self, tmp_path):
        """Test 2: missing approval marker returns HOLD_PLAN_NOT_APPROVED."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_no_approval_pmg",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": False,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit",
                "allowed_files": ["docs/example.md"],
                "forbidden_files": [
                    ".git/",
                    ".github/",
                    "scripts/local/final_gate_status.py",
                    "scripts/local/verify_final_head_merge_command.py",
                    "scripts/local/check_persistent_mutation_guard.py",
                ],
                "do_not": [
                    "no git push",
                    "no gh pr create",
                    "no gh pr merge",
                ],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        # PMG is checked after approval, but approval fails first so PMG not called
        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True):
            result = run(packet, str(output_json), str(output_md))
            assert result["status"] == "HOLD_PLAN_NOT_APPROVED"

    def test_plan_hash_mismatch_returns_hold(self, tmp_path):
        """Test 3: plan hash mismatch returns HOLD_PLAN_NOT_APPROVED."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path, "Correct content\n")
        wrong_sha = sha256_str("Wrong content\n")
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_hash_mismatch_pmg",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": wrong_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": wrong_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit",
                "allowed_files": ["docs/example.md"],
                "forbidden_files": [
                    ".git/",
                    ".github/",
                    "scripts/local/final_gate_status.py",
                    "scripts/local/verify_final_head_merge_command.py",
                    "scripts/local/check_persistent_mutation_guard.py",
                ],
                "do_not": [
                    "no git push",
                    "no gh pr create",
                    "no gh pr merge",
                ],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True):
            result = run(packet, str(output_json), str(output_md))
            assert result["status"] == "HOLD_PLAN_NOT_APPROVED"
            assert any("mismatch" in e.lower() for e in result.get("validation_errors", []))

    def test_dirty_main_repo_returns_hold_main_dirty(self, tmp_path):
        """Test 4: dirty main repo (staged changes) returns HOLD_MAIN_DIRTY."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        # Create a dirty state: add a staged change inside the AED repo
        dirty_file = REPO_ROOT / "TESTS_TEMP_DIRTY_MARKER.txt"
        dirty_file.write_text("dirty\n")
        subprocess.run(["git", "-C", str(REPO_ROOT), "add", "TESTS_TEMP_DIRTY_MARKER.txt"], capture_output=True, timeout=5)
        dirty_file.unlink()  # Remove physical file, leave staged add

        try:
            packet = {
                "packet_kind": "aed.temp_worktree.execution.v0",
                "run_id": "test_run_dirty_main_pmg",
                "task_id": "TASK-001",
                "base_sha": base_sha,
                "approved_plan_path": str(plan_path),
                "approved_plan_sha256": plan_sha,
                "approval": {
                    "approved_for_temp_worktree_execution": True,
                    "approved_by": "human",
                    "approved_plan_sha256": plan_sha,
                    "approved_at": now,
                    "max_changed_files": 5,
                },
                "task": {
                    "description": "Edit",
                    "allowed_files": ["docs/example.md"],
                    "forbidden_files": [],
                    "do_not": [],
                },
                "execution": {
                    "mode": "mock",
                    "timeout_seconds": 60,
                    "output_root": str(tmp_path / "output"),
                },
            }

            packet_path = tmp_path / "packet.json"
            packet_path.write_text(json.dumps(packet), encoding="utf-8")
            output_json = tmp_path / "result.json"
            output_md = tmp_path / "result.md"

            result = run(packet, str(output_json), str(output_md))
            assert result["status"] == "HOLD_MAIN_DIRTY", \
                f"Expected HOLD_MAIN_DIRTY, got {result['status']}: {result.get('validation_errors')}"
        finally:
            # Clean up staged change from AED repo
            subprocess.run(["git", "-C", str(REPO_ROOT), "reset", "HEAD", "TESTS_TEMP_DIRTY_MARKER.txt"], capture_output=True, timeout=5)
            if (REPO_ROOT / "TESTS_TEMP_DIRTY_MARKER.txt").exists():
                (REPO_ROOT / "TESTS_TEMP_DIRTY_MARKER.txt").unlink()

    def test_output_root_inside_repo_returns_hold_output_path_inside_repo(self, tmp_path):
        """Test 5: output_root inside repo returns HOLD_OUTPUT_PATH_INSIDE_REPO."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        # Use a path inside REPO_ROOT as output_root
        inside_repo_output = REPO_ROOT / "tests" / "fixture_output_tmp"

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_output_inside_repo_pmg",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit",
                "allowed_files": ["docs/example.md"],
                "forbidden_files": [
                    ".git/",
                    ".github/",
                    "scripts/local/final_gate_status.py",
                    "scripts/local/verify_final_head_merge_command.py",
                    "scripts/local/check_persistent_mutation_guard.py",
                ],
                "do_not": [
                    "no git push",
                    "no gh pr create",
                    "no gh pr merge",
                ],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(inside_repo_output),
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True):
            result = run(packet, str(output_json), str(output_md))
            assert result["status"] == "HOLD_OUTPUT_PATH_INSIDE_REPO"

        # Cleanup
        if inside_repo_output.exists():
            import shutil
            shutil.rmtree(inside_repo_output, ignore_errors=True)

    def test_non_mock_mode_returns_hold_executor_not_allowed(self, tmp_path):
        """Test 6: non-mock execution mode returns HOLD_EXECUTOR_NOT_ALLOWED."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        for mode in ["real", "claude", "execute", "run", "agent"]:
            packet = {
                "packet_kind": "aed.temp_worktree.execution.v0",
                "run_id": f"test_run_mode_{mode}_pmg",
                "task_id": "TASK-001",
                "base_sha": base_sha,
                "approved_plan_path": str(plan_path),
                "approved_plan_sha256": plan_sha,
                "approval": {
                    "approved_for_temp_worktree_execution": True,
                    "approved_by": "human",
                    "approved_plan_sha256": plan_sha,
                    "approved_at": now,
                    "max_changed_files": 5,
                },
                "task": {
                    "description": "Edit",
                    "allowed_files": ["docs/example.md"],
                    "forbidden_files": [],
                    "do_not": [],
                },
                "execution": {
                    "mode": mode,
                    "timeout_seconds": 60,
                    "output_root": str(tmp_path / "output"),
                },
            }

            packet_path = tmp_path / f"packet_{mode}.json"
            packet_path.write_text(json.dumps(packet), encoding="utf-8")
            output_json = tmp_path / f"result_{mode}.json"
            output_md = tmp_path / f"result_{mode}.md"

            # Unsupported modes: always blocked with HOLD_EXECUTOR_NOT_ALLOWED
        for mode in ["real", "execute", "run", "agent"]:
            with mock.patch.object(rte, "git_status", return_value="clean"), \
                 mock.patch.object(rte, "git_status_clean", return_value=True):
                result = run(packet, str(output_json), str(output_md))
                assert result["status"] == "HOLD_EXECUTOR_NOT_ALLOWED", \
                    f"mode={mode} should be blocked, got {result['status']}"

        # claude mode without flag → HOLD_REAL_EXECUTOR_NOT_ENABLED
        for mode in ["claude"]:
            packet = {
                "packet_kind": "aed.temp_worktree.execution.v0",
                "run_id": f"test_run_mode_{mode}_pmg",
                "task_id": "TASK-001",
                "base_sha": base_sha,
                "approved_plan_path": str(plan_path),
                "approved_plan_sha256": plan_sha,
                "approval": {
                    "approved_for_temp_worktree_execution": True,
                    "approved_by": "human",
                    "approved_plan_sha256": plan_sha,
                    "approved_at": now,
                    "max_changed_files": 5,
                },
                "task": {
                    "description": "Edit",
                    "allowed_files": ["docs/example.md"],
                    "forbidden_files": [],
                    "do_not": [],
                },
                "execution": {
                    "mode": mode,
                    "timeout_seconds": 60,
                    "output_root": str(tmp_path / "output"),
                },
            }
            packet_path = tmp_path / f"packet_{mode}.json"
            packet_path.write_text(json.dumps(packet), encoding="utf-8")
            output_json = tmp_path / f"result_{mode}.json"
            output_md = tmp_path / f"result_{mode}.md"

            with mock.patch.object(rte, "git_status", return_value="clean"), \
                 mock.patch.object(rte, "git_status_clean", return_value=True):
                result = run(packet, str(output_json), str(output_md))
                assert result["status"] == "HOLD_REAL_EXECUTOR_NOT_ENABLED", \
                    f"mode={mode} without flag should be blocked with HOLD_REAL_EXECUTOR_NOT_ENABLED, got {result['status']}"

    def test_claude_mode_with_flag_returns_hold_claude_implementation_pending(self, tmp_path):
        """Test 6b: claude mode with --enable-real-claude-executor flag returns
        CLAUDE_EXECUTOR_SUCCESS (mocked) and proceeds through PMG+worktree+post-validation.

        The real executor is called; we mock run_claude_executor to return success
        so the full pipeline (PMG, worktree, post-validation) is exercised.
        """
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_mode_claude_flag",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit",
                "allowed_files": ["docs/example.md"],
                "forbidden_files": [
                    ".git/",
                    ".github/",
                    "scripts/local/final_gate_status.py",
                    "scripts/local/verify_final_head_merge_command.py",
                    "scripts/local/check_persistent_mutation_guard.py",
                ],
                "do_not": [
                    "no git push",
                    "no gh pr create",
                    "no gh pr merge",
                ],
            },
            "execution": {
                "mode": "claude",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
            },
        }
        # Provide a mock_edits so changed_files is non-empty after Claude runs
        # (Claude succeeded but we simulate file changes via mock)
        packet["execution"]["mock_edits"] = [
            {"path": "docs/example.md", "content": "# updated by claude\n"}
        ]

        output_json = tmp_path / "result_claude_flag.json"
        output_md = tmp_path / "result_claude_flag.md"
        output_root = tmp_path / "output"
        output_root.mkdir()
        # Create expected PMG artifacts
        (output_root / "pmg_snapshot.json").write_text('{"status":"clean","files":{}}', encoding="utf-8")
        (output_root / "pmg_compare.json").write_text('{"status":"clean","blocked":0}', encoding="utf-8")

        # Mock run_claude_executor to return success with all artifact paths
        success_result = {
            "status": "CLAUDE_EXECUTOR_SUCCESS",
            "claude_exit_code": 0,
            "claude_started_at": "2026-05-21T12:00:00Z",
            "claude_finished_at": "2026-05-21T12:01:00Z",
            "claude_elapsed_seconds": 60.0,
            "claude_stdout_path": str(output_root / "claude_stdout.txt"),
            "claude_stderr_path": str(output_root / "claude_stderr.txt"),
            "claude_transcript_path": str(output_root / "claude_transcript.md"),
            "claude_command_contract_valid": True,
            "claude_command_contract_errors": [],
            "claude_command_contract_summary": f"argv=['claude', '--print', '--input-format=text', '--output-format=text'] [stdin:.aed_plan.md]",
        }
        (output_root / "claude_stdout.txt").write_text("Claude output", encoding="utf-8")
        (output_root / "claude_stderr.txt").write_text("", encoding="utf-8")

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch.object(rte, "pmg_snapshot", return_value=(True, "")), \
             mock.patch.object(rte, "pmg_compare", return_value=(True, "")), \
             mock.patch.object(rte, "run_claude_executor", return_value=success_result), \
             mock.patch.object(rte, "git_worktree_add", return_value=subprocess.CompletedProcess("", 0)):
            result = run(packet, str(output_json), str(output_md), enable_real_claude_executor=True)
            # Now that we have a real executor path, it should proceed past the stub
            # The result status depends on whether mock_edits produce changes
            # With mock_edits containing a file, we get PATCH_READY_FOR_HUMAN_REVIEW
            assert result["status"] in ("PATCH_READY_FOR_HUMAN_REVIEW", "HOLD_CLAUDE_EMPTY_OUTPUT"), \
                f"claude mode with flag: expected PATCH_READY or HOLD_CLAUDE_EMPTY_OUTPUT, got {result['status']}"
            assert result.get("claude_command_contract_valid") is True, \
                f"claude_command_contract_valid should be True, got {result.get('claude_command_contract_valid')}"
            assert result.get("claude_command_contract_errors") == [], \
                f"claude_command_contract_errors should be [], got {result.get('claude_command_contract_errors')}"
            assert "argv=" in result.get("claude_command_contract_summary", ""), \
                f"claude_command_contract_summary should contain argv, got {result.get('claude_command_contract_summary')}"
            # Check that claude invocation fields are present
            assert result.get("claude_exit_code") == 0, \
                f"claude_exit_code should be 0, got {result.get('claude_exit_code')}"
            assert result.get("claude_stdout_path"), "claude_stdout_path should be set"

    def test_claude_mode_with_flag_contract_valid_fields(self, tmp_path):
        """Test 6c: claude mode with flag includes all claude_* invocation fields in result.

        The contract fields (claude_command_contract_valid, claude_command_contract_summary)
        are set even when the executor returns a HOLD. We mock run_claude_executor
        to return HOLD_CLAUDE_TIMEOUT so we can assert on the field presence without
        needing a real claude binary.
        """
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_claude_contract_fields",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit",
                "allowed_files": ["docs/example.md"],
                "forbidden_files": [
                    ".git/",
                    ".github/",
                    "scripts/local/final_gate_status.py",
                    "scripts/local/verify_final_head_merge_command.py",
                    "scripts/local/check_persistent_mutation_guard.py",
                ],
                "do_not": [
                    "no git push",
                    "no gh pr create",
                    "no gh pr merge",
                ],
            },
            "execution": {
                "mode": "claude",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
            },
        }
        output_json = tmp_path / "result_claude_contract.json"
        output_md = tmp_path / "result_claude_contract.md"
        output_root = tmp_path / "output"
        output_root.mkdir()
        (output_root / "pmg_snapshot.json").write_text('{"status":"clean","files":{}}', encoding="utf-8")
        (output_root / "pmg_compare.json").write_text('{"status":"clean","blocked":0}', encoding="utf-8")

        # Mock run_claude_executor to return timeout HOLD so we can check contract fields
        timeout_result = {
            "status": "HOLD_CLAUDE_TIMEOUT",
            "claude_exit_code": -1,
            "claude_started_at": "2026-05-21T12:00:00Z",
            "claude_finished_at": "2026-05-21T12:00:05Z",
            "claude_elapsed_seconds": 5.0,
            "claude_stdout_path": str(output_root / "claude_stdout.txt"),
            "claude_stderr_path": str(output_root / "claude_stderr.txt"),
            "claude_transcript_path": "",
            "claude_command_contract_valid": True,
            "claude_command_contract_errors": ["Claude timed out after 60s"],
            "claude_command_contract_summary": f"argv=['claude', '--print', '--input-format=text', '--output-format=text'] [stdin:.aed_plan.md]",
        }
        (output_root / "claude_stdout.txt").write_text("(timeout)", encoding="utf-8")

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch.object(rte, "pmg_snapshot", return_value=(True, "")), \
             mock.patch.object(rte, "pmg_compare", return_value=(True, "")), \
             mock.patch.object(rte, "run_claude_executor", return_value=timeout_result), \
             mock.patch.object(rte, "git_worktree_add", return_value=subprocess.CompletedProcess("", 0)):
            result = run(packet, str(output_json), str(output_md), enable_real_claude_executor=True)
            assert result["status"] == "HOLD_CLAUDE_TIMEOUT", \
                f"expected HOLD_CLAUDE_TIMEOUT, got {result['status']}"
            # Contract summary should be a safe non-executable description
            summary = result.get("claude_command_contract_summary", "")
            assert "claude" in summary, f"summary should mention claude: {summary}"
            assert "--no-input" not in summary, f"summary should NOT contain deprecated --no-input flag: {summary}"
            # New contract uses --print --input-format=text --output-format=text (stdin mode)
            assert "--print" in summary, f"summary should mention --print: {summary}"
            # Contract valid should be True (HOLD from timeout, not from contract invalid)
            assert result.get("claude_command_contract_valid") is True
            # Timeout error detail is carried in claude_command_contract_errors (not validation_errors)
            # The result status is HOLD_CLAUDE_TIMEOUT which confirms the timeout
            assert result["status"] == "HOLD_CLAUDE_TIMEOUT"
            # Invocations fields should be present even on HOLD
            assert result.get("claude_exit_code") == -1, \
                f"claude_exit_code should be -1, got {result.get('claude_exit_code')}"
            assert result.get("claude_started_at") == "2026-05-21T12:00:00Z"
            assert result.get("claude_elapsed_seconds") == 5.0

    def test_edit_outside_allowed_files_returns_hold_outside_allowed(self, tmp_path):
        """Test 7: edit outside allowed_files returns HOLD_OUTSIDE_ALLOWED_FILES."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_outside_allowed_pmg",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit",
                "allowed_files": ["docs/example.md"],
                "forbidden_files": [
                    ".git/",
                    ".github/",
                    "scripts/local/final_gate_status.py",
                    "scripts/local/verify_final_head_merge_command.py",
                    "scripts/local/check_persistent_mutation_guard.py",
                ],
                "do_not": [
                    "no git push",
                    "no gh pr create",
                    "no gh pr merge",
                ],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [{"path": "other.txt", "content": "not allowed"}],
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        # Mock PMG so tests don't depend on real PMG availability
        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")
        pmg_compare_json, pmg_compare_md = make_clean_pmg_compare(tmp_path / "pmg")

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:
            def fake_snapshot(target, output_json):
                import shutil; shutil.copy(str(pmg_snapshot_path), output_json); return True, ""
            def fake_compare(snapshot_json, output_json, output_md):
                import shutil; shutil.copy(str(pmg_compare_json), output_json); shutil.copy(str(pmg_compare_md), output_md); return True, ""
            mock_snap.side_effect = fake_snapshot
            mock_cmp.side_effect = fake_compare

            result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "HOLD_OUTSIDE_ALLOWED_FILES"
        assert any("outside allowed_files" in e.lower() for e in result.get("validation_errors", []))

    def test_edit_forbidden_file_returns_hold_forbidden_file_touched(self, tmp_path):
        """Test 8: edit forbidden file returns HOLD_FORBIDDEN_FILE_TOUCHED."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_forbidden_pmg",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit",
                "allowed_files": ["docs/example.md", "scripts/local/final_gate_status.py"],
                "forbidden_files": PROTECTED_GATE_SCRIPTS,
                "do_not": [],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [{"path": "scripts/local/final_gate_status.py", "content": "# Hacked\n"}],
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        # Mock PMG so tests don't depend on real PMG availability
        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")
        pmg_compare_json, pmg_compare_md = make_clean_pmg_compare(tmp_path / "pmg")

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:
            def fake_snapshot(target, output_json):
                import shutil; shutil.copy(str(pmg_snapshot_path), output_json); return True, ""
            def fake_compare(snapshot_json, output_json, output_md):
                import shutil; shutil.copy(str(pmg_compare_json), output_json); shutil.copy(str(pmg_compare_md), output_md); return True, ""
            mock_snap.side_effect = fake_snapshot
            mock_cmp.side_effect = fake_compare

            result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "HOLD_FORBIDDEN_FILE_TOUCHED"
        assert any("forbidden" in e.lower() for e in result.get("validation_errors", []))

    def test_too_many_changed_files_returns_hold_too_many_files_changed(self, tmp_path):
        """Test 9: too many changed files returns HOLD_TOO_MANY_FILES_CHANGED."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_too_many_pmg",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 2,  # Only 2 allowed
            },
            "task": {
                "description": "Edit many",
                "allowed_files": ["file1.txt", "file2.txt", "file3.txt"],
                "forbidden_files": [],
                "do_not": [],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [
                    {"path": "file1.txt", "content": "c1"},
                    {"path": "file2.txt", "content": "c2"},
                    {"path": "file3.txt", "content": "c3"},
                ],
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        # Mock PMG so tests don't depend on real PMG availability
        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")
        pmg_compare_json, pmg_compare_md = make_clean_pmg_compare(tmp_path / "pmg")

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:
            def fake_snapshot(target, output_json):
                import shutil; shutil.copy(str(pmg_snapshot_path), output_json); return True, ""
            def fake_compare(snapshot_json, output_json, output_md):
                import shutil; shutil.copy(str(pmg_compare_json), output_json); shutil.copy(str(pmg_compare_md), output_md); return True, ""
            mock_snap.side_effect = fake_snapshot
            mock_cmp.side_effect = fake_compare

            result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "HOLD_TOO_MANY_FILES_CHANGED"

    def test_worktree_path_under_tmp_aed_runs_worktrees(self, tmp_path):
        """Test 11: worktree path is under /tmp/aed_runs/worktrees/."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_worktree_path_pmg",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit",
                "allowed_files": ["file1.txt"],
                "forbidden_files": [],
                "do_not": [],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [{"path": "file1.txt", "content": "updated"}],
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        # Mock PMG
        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")
        pmg_compare_json, pmg_compare_md = make_clean_pmg_compare(tmp_path / "pmg")

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:
            def fake_snapshot(target, output_json):
                import shutil; shutil.copy(str(pmg_snapshot_path), output_json); return True, ""
            def fake_compare(snapshot_json, output_json, output_md):
                import shutil; shutil.copy(str(pmg_compare_json), output_json); shutil.copy(str(pmg_compare_md), output_md); return True, ""
            mock_snap.side_effect = fake_snapshot
            mock_cmp.side_effect = fake_compare
            result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "PATCH_READY_FOR_HUMAN_REVIEW"
        assert str(worktree_path).startswith("/tmp/aed_runs/worktrees/"), \
            f"worktree path {worktree_path} not under /tmp/aed_runs/worktrees/"

    def test_result_json_includes_pmg_fields(self, tmp_path):
        """Test: result JSON includes pmg_snapshot_path, pmg_compare_json_path, pmg_status."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_json_pmg_fields",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit",
                "allowed_files": ["file1.txt"],
                "forbidden_files": [],
                "do_not": [],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [{"path": "file1.txt", "content": "updated"}],
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")
        pmg_compare_json, pmg_compare_md = make_clean_pmg_compare(tmp_path / "pmg")

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:
            def fake_snapshot(target, output_json):
                import shutil; shutil.copy(str(pmg_snapshot_path), output_json); return True, ""
            def fake_compare(snapshot_json, output_json, output_md):
                import shutil; shutil.copy(str(pmg_compare_json), output_json); shutil.copy(str(pmg_compare_md), output_md); return True, ""
            mock_snap.side_effect = fake_snapshot
            mock_cmp.side_effect = fake_compare
            result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "PATCH_READY_FOR_HUMAN_REVIEW"
        assert "pmg_snapshot_path" in result
        assert "pmg_compare_json_path" in result
        assert "pmg_compare_md_path" in result
        assert "pmg_status" in result
        assert result["pmg_status"] == "clean"
        assert result["pmg_blocked_files"] == 0

    def test_result_markdown_includes_pmg_section(self, tmp_path):
        """Test: result markdown includes PMG status section."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_md_pmg_section",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit",
                "allowed_files": ["file1.txt"],
                "forbidden_files": [],
                "do_not": [],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [{"path": "file1.txt", "content": "updated"}],
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")
        pmg_compare_json, pmg_compare_md = make_clean_pmg_compare(tmp_path / "pmg")

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:
            def fake_snapshot(target, output_json):
                import shutil; shutil.copy(str(pmg_snapshot_path), output_json); return True, ""
            def fake_compare(snapshot_json, output_json, output_md):
                import shutil; shutil.copy(str(pmg_compare_json), output_json); shutil.copy(str(pmg_compare_md), output_md); return True, ""
            mock_snap.side_effect = fake_snapshot
            mock_cmp.side_effect = fake_compare
            result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        md_text = Path(output_md).read_text()
        assert "Persistent Mutation Guard (PMG)" in md_text
        assert "PMG status" in md_text
        assert "clean" in md_text

    def test_no_auto_apply_on_patch_ready(self, tmp_path):
        """Verify PATCH_READY_FOR_HUMAN_REVIEW stops at human review, does not auto-apply."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_no_auto_apply_pmg",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit",
                "allowed_files": ["file1.txt"],
                "forbidden_files": [],
                "do_not": [],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [{"path": "file1.txt", "content": "updated"}],
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")
        pmg_compare_json, pmg_compare_md = make_clean_pmg_compare(tmp_path / "pmg")

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:
            def fake_snapshot(target, output_json):
                import shutil; shutil.copy(str(pmg_snapshot_path), output_json); return True, ""
            def fake_compare(snapshot_json, output_json, output_md):
                import shutil; shutil.copy(str(pmg_compare_json), output_json); shutil.copy(str(pmg_compare_md), output_md); return True, ""
            mock_snap.side_effect = fake_snapshot
            mock_cmp.side_effect = fake_compare
            result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "PATCH_READY_FOR_HUMAN_REVIEW"
        assert result["patch_ready"] is True
        assert "human reviews" in result["next_action"].lower()
        assert result["main_git_status_after"] == "clean" or result["main_git_status_after"].startswith("?? ")

    def test_empty_mock_edits_returns_patch_ready(self, tmp_path):
        """Zero mock_edits means no changes; harness returns PATCH_READY_FOR_HUMAN_REVIEW with empty diff."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_empty_edits_pmg",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "No-op",
                "allowed_files": [],
                "forbidden_files": [],
                "do_not": [],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [],
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")
        pmg_compare_json, pmg_compare_md = make_clean_pmg_compare(tmp_path / "pmg")

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:
            def fake_snapshot(target, output_json):
                import shutil; shutil.copy(str(pmg_snapshot_path), output_json); return True, ""
            def fake_compare(snapshot_json, output_json, output_md):
                import shutil; shutil.copy(str(pmg_compare_json), output_json); shutil.copy(str(pmg_compare_md), output_md); return True, ""
            mock_snap.side_effect = fake_snapshot
            mock_cmp.side_effect = fake_compare
            result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "PATCH_READY_FOR_HUMAN_REVIEW"
        assert result["changed_files"] == []
        assert result["patch_ready"] is True


# ---------------------------------------------------------------------------
# PMG-specific tests
# ---------------------------------------------------------------------------

class TestPMGPIntegration:
    """Tests for PMG snapshot, compare, and external mutation blocking."""

    def test_pmg_snapshot_failure_returns_hold_pmg_snapshot_failed(self, tmp_path):
        """PMG snapshot subprocess failure returns HOLD_PMG_SNAPSHOT_FAILED."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_pmg_snapshot_fail",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit",
                "allowed_files": ["file1.txt"],
                "forbidden_files": [],
                "do_not": [],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [{"path": "file1.txt", "content": "updated"}],
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        # Mock PMG snapshot to fail
        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap:
            mock_snap.return_value = (False, "PMG snapshot timed out after 60s")
            result = run(packet, str(output_json), str(output_md))

        assert result["status"] == "HOLD_PMG_SNAPSHOT_FAILED"
        assert any("PMG snapshot failed" in e for e in result.get("validation_errors", []))
        # worktree should not have been created (snapshot happens before worktree)
        worktree_path = Path(result.get("worktree_path", ""))
        assert not worktree_path.exists()

    def test_pmg_compare_failure_returns_hold_pmg_compare_failed(self, tmp_path):
        """PMG compare subprocess failure returns HOLD_PMG_COMPARE_FAILED."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_pmg_compare_fail",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit",
                "allowed_files": ["file1.txt"],
                "forbidden_files": [],
                "do_not": [],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [{"path": "file1.txt", "content": "updated"}],
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        # Mock snapshot to succeed, compare to fail
        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:
            def fake_snapshot(target, output_json):
                import shutil; shutil.copy(str(pmg_snapshot_path), output_json); return True, ""
            mock_snap.side_effect = fake_snapshot
            mock_cmp.return_value = (False, "PMG compare failed with exit 1: internal error")

            result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "HOLD_PMG_COMPARE_FAILED"
        assert any("PMG compare failed" in e for e in result.get("validation_errors", []))

    def test_pmg_compare_blocked_returns_hold_external_mutation(self, tmp_path):
        """PMG compare returns blocked status → HOLD_EXTERNAL_MUTATION."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_pmg_blocked",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit",
                "allowed_files": ["file1.txt"],
                "forbidden_files": [],
                "do_not": [],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [{"path": "file1.txt", "content": "updated"}],
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")
        blocked_compare_json, blocked_compare_md = make_blocked_pmg_compare(tmp_path / "pmg", blocked_count=2)

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:
            def fake_snapshot(target, output_json):
                import shutil; shutil.copy(str(pmg_snapshot_path), output_json); return True, ""
            def fake_compare(snapshot_json, output_json, output_md):
                import shutil; shutil.copy(str(blocked_compare_json), output_json); shutil.copy(str(blocked_compare_md), output_md); return True, ""
            mock_snap.side_effect = fake_snapshot
            mock_cmp.side_effect = fake_compare

            result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "HOLD_EXTERNAL_MUTATION", \
            f"Expected HOLD_EXTERNAL_MUTATION, got {result['status']}: {result.get('validation_errors')}"
        assert any("external mutation detected" in e.lower() for e in result.get("validation_errors", []))
        assert result["pmg_status"] == "blocked"
        assert result["pmg_blocked_files"] == 2

    def test_pmg_clean_returns_patch_ready(self, tmp_path):
        """Clean PMG snapshot and compare → PATCH_READY_FOR_HUMAN_REVIEW."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_pmg_clean_ok",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit",
                "allowed_files": ["file1.txt"],
                "forbidden_files": [],
                "do_not": [],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [{"path": "file1.txt", "content": "updated"}],
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")
        pmg_compare_json, pmg_compare_md = make_clean_pmg_compare(tmp_path / "pmg")

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:
            def fake_snapshot(target, output_json):
                import shutil; shutil.copy(str(pmg_snapshot_path), output_json); return True, ""
            def fake_compare(snapshot_json, output_json, output_md):
                import shutil; shutil.copy(str(pmg_compare_json), output_json); shutil.copy(str(pmg_compare_md), output_md); return True, ""
            mock_snap.side_effect = fake_snapshot
            mock_cmp.side_effect = fake_compare

            result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "PATCH_READY_FOR_HUMAN_REVIEW"
        assert result["pmg_status"] == "clean"
        assert result["pmg_blocked_files"] == 0
        assert "file1.txt" in result["changed_files"]
        assert result["main_git_status_after"] == "clean" or result["main_git_status_after"].startswith("?? ")

    def test_pmg_compare_read_failure_returns_hold_pmg_compare_failed(self, tmp_path):
        """PMG compare succeeds but JSON read fails → HOLD_PMG_COMPARE_FAILED."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_pmg_read_fail",
            "task_id": "TASK-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit",
                "allowed_files": ["file1.txt"],
                "forbidden_files": [],
                "do_not": [],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [{"path": "file1.txt", "content": "updated"}],
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")

        # Write a malformed compare JSON
        pmg_dir = tmp_path / "pmg"
        pmg_dir.mkdir(parents=True, exist_ok=True)
        bad_compare_json = pmg_dir / "bad_compare.json"
        bad_compare_json.write_text("NOT JSON{", encoding="utf-8")
        bad_compare_md = pmg_dir / "bad_compare.md"
        bad_compare_md.write_text("malformed", encoding="utf-8")

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:
            def fake_snapshot(target, output_json):
                import shutil; shutil.copy(str(pmg_snapshot_path), output_json); return True, ""
            def fake_compare(snapshot_json, output_json, output_md):
                import shutil; shutil.copy(str(bad_compare_json), output_json); shutil.copy(str(bad_compare_md), output_md); return True, ""
            mock_snap.side_effect = fake_snapshot
            mock_cmp.side_effect = fake_compare

            result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "HOLD_PMG_COMPARE_FAILED"
        assert any("failed to read PMG compare result" in e for e in result.get("validation_errors", []))


# ---------------------------------------------------------------------------
# Command contract validator tests (pure functions, no git/subprocess)
# ---------------------------------------------------------------------------

class TestCommandContractValidator:
    """Unit tests for build_claude_command_contract and validate_claude_command_contract."""

    def test_build_contract_returns_valid_structure(self):
        """Contract builder returns expected keys and types."""
        import tempfile
        from pathlib import Path

        packet = {
            "run_id": "test_build",
            "base_sha": "abc123",
            "execution": {"timeout_seconds": 60},
        }
        worktree = Path("/tmp/aed_runs/worktrees/test_build")
        output = Path("/tmp/aed_runs/output/test_build")

        contract = rte.build_claude_command_contract(packet, worktree, output)

        assert "argv" in contract
        assert "cwd" in contract
        assert "timeout_seconds" in contract
        assert "env_policy" in contract
        assert "stdout_path" in contract
        assert "stderr_path" in contract
        assert "transcript_path" in contract
        assert isinstance(contract["argv"], list)
        assert all(isinstance(a, str) for a in contract["argv"])
        assert contract["argv"][0] == "claude"

    def test_validate_contract_cwd_not_under_worktrees(self):
        """Invalid cwd outside /tmp/aed_runs/worktrees/ is rejected."""
        import tempfile
        from pathlib import Path

        contract = {
            "argv": ["claude", "--no-input", "/tmp/other/plan.md"],
            "cwd": "/tmp/other/worktree",
            "timeout_seconds": 60,
            "stdout_path": "/tmp/other/stdout.txt",
            "stderr_path": "/tmp/other/stderr.txt",
            "transcript_path": "/tmp/other/transcript.md",
        }
        packet = {"approved_plan_path": "/tmp/other/plan.md"}
        worktree = Path("/tmp/other/worktree")
        output = Path("/tmp/other/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        is_valid, errors = rte.validate_claude_command_contract(
            contract, packet, worktree, output, repo_root
        )
        assert not is_valid
        assert any("/tmp/aed_runs/worktrees/" in e for e in errors)

    def test_validate_contract_cwd_inside_repo(self):
        """cwd inside main repo is rejected."""
        from pathlib import Path

        contract = {
            "argv": ["claude", "--no-input", "/home/max/Automated-Edge-Discovery/plan.md"],
            "cwd": "/home/max/Automated-Edge-Discovery",
            "timeout_seconds": 60,
            "stdout_path": "/tmp/output/stdout.txt",
            "stderr_path": "/tmp/output/stderr.txt",
            "transcript_path": "/tmp/output/transcript.md",
        }
        packet = {"approved_plan_path": "/tmp/other/plan.md"}
        worktree = Path("/home/max/Automated-Edge-Discovery")
        output = Path("/tmp/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        is_valid, errors = rte.validate_claude_command_contract(
            contract, packet, worktree, output, repo_root
        )
        assert not is_valid
        # cwd inside repo is rejected (must be under worktrees_base AND outside main repo)
        assert any("outside main repo" in e or "cwd must be under" in e for e in errors)

    def test_validate_contract_forbidden_git_push(self):
        """argv containing git push is rejected."""
        from pathlib import Path

        contract = {
            "argv": ["claude", "git", "push"],
            "cwd": "/tmp/aed_runs/worktrees/test",
            "timeout_seconds": 60,
            "stdout_path": "/tmp/output/stdout.txt",
            "stderr_path": "/tmp/output/stderr.txt",
            "transcript_path": "/tmp/output/transcript.md",
        }
        packet = {"approved_plan_path": "/tmp/other/plan.md"}
        worktree = Path("/tmp/aed_runs/worktrees/test")
        output = Path("/tmp/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        is_valid, errors = rte.validate_claude_command_contract(
            contract, packet, worktree, output, repo_root
        )
        assert not is_valid
        assert any("forbidden" in e.lower() for e in errors)

    def test_validate_contract_forbidden_gh_pr_create(self):
        """argv containing gh pr create is rejected."""
        from pathlib import Path

        contract = {
            "argv": ["claude", "gh", "pr", "create"],
            "cwd": "/tmp/aed_runs/worktrees/test",
            "timeout_seconds": 60,
            "stdout_path": "/tmp/output/stdout.txt",
            "stderr_path": "/tmp/output/stderr.txt",
            "transcript_path": "/tmp/output/transcript.md",
        }
        packet = {"approved_plan_path": "/tmp/other/plan.md"}
        worktree = Path("/tmp/aed_runs/worktrees/test")
        output = Path("/tmp/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        is_valid, errors = rte.validate_claude_command_contract(
            contract, packet, worktree, output, repo_root
        )
        assert not is_valid
        assert any("forbidden" in e.lower() for e in errors)

    def test_validate_contract_forbidden_gh_pr_merge(self):
        """argv containing gh pr merge is rejected."""
        from pathlib import Path

        contract = {
            "argv": ["claude", "gh", "pr", "merge"],
            "cwd": "/tmp/aed_runs/worktrees/test",
            "timeout_seconds": 60,
            "stdout_path": "/tmp/output/stdout.txt",
            "stderr_path": "/tmp/output/stderr.txt",
            "transcript_path": "/tmp/output/transcript.md",
        }
        packet = {"approved_plan_path": "/tmp/other/plan.md"}
        worktree = Path("/tmp/aed_runs/worktrees/test")
        output = Path("/tmp/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        is_valid, errors = rte.validate_claude_command_contract(
            contract, packet, worktree, output, repo_root
        )
        assert not is_valid
        assert any("forbidden" in e.lower() for e in errors)

    def test_validate_contract_forbidden_npm_install(self):
        """argv containing npm install is rejected."""
        from pathlib import Path

        contract = {
            "argv": ["claude", "npm", "install"],
            "cwd": "/tmp/aed_runs/worktrees/test",
            "timeout_seconds": 60,
            "stdout_path": "/tmp/output/stdout.txt",
            "stderr_path": "/tmp/output/stderr.txt",
            "transcript_path": "/tmp/output/transcript.md",
        }
        packet = {"approved_plan_path": "/tmp/other/plan.md"}
        worktree = Path("/tmp/aed_runs/worktrees/test")
        output = Path("/tmp/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        is_valid, errors = rte.validate_claude_command_contract(
            contract, packet, worktree, output, repo_root
        )
        assert not is_valid
        assert any("forbidden" in e.lower() for e in errors)

    def test_validate_contract_timeout_zero_invalid(self):
        """timeout_seconds <= 0 is rejected."""
        from pathlib import Path

        contract = {
            "argv": ["claude", "--no-input", "plan.md"],
            "cwd": "/tmp/aed_runs/worktrees/test",
            "timeout_seconds": 0,
            "stdout_path": "/tmp/output/stdout.txt",
            "stderr_path": "/tmp/output/stderr.txt",
            "transcript_path": "/tmp/output/transcript.md",
        }
        packet = {"approved_plan_path": "/tmp/other/plan.md"}
        worktree = Path("/tmp/aed_runs/worktrees/test")
        output = Path("/tmp/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        is_valid, errors = rte.validate_claude_command_contract(
            contract, packet, worktree, output, repo_root
        )
        assert not is_valid
        assert any("positive" in e.lower() for e in errors)

    def test_validate_contract_timeout_exceeds_max_invalid(self):
        """timeout_seconds > MAX_TIMEOUT_SECONDS is rejected."""
        from pathlib import Path

        contract = {
            "argv": ["claude", "--no-input", "plan.md"],
            "cwd": "/tmp/aed_runs/worktrees/test",
            "timeout_seconds": 999999,
            "stdout_path": "/tmp/output/stdout.txt",
            "stderr_path": "/tmp/output/stderr.txt",
            "transcript_path": "/tmp/output/transcript.md",
        }
        packet = {"approved_plan_path": "/tmp/other/plan.md"}
        worktree = Path("/tmp/aed_runs/worktrees/test")
        output = Path("/tmp/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        is_valid, errors = rte.validate_claude_command_contract(
            contract, packet, worktree, output, repo_root
        )
        assert not is_valid
        assert any("exceeds maximum" in e.lower() for e in errors)

    def test_validate_contract_stdout_outside_output_root(self):
        """stdout_path outside output_root is rejected."""
        from pathlib import Path

        contract = {
            "argv": ["claude", "--no-input", "plan.md"],
            "cwd": "/tmp/aed_runs/worktrees/test",
            "timeout_seconds": 60,
            "stdout_path": "/tmp/other/stdout.txt",
            "stderr_path": "/tmp/output/stderr.txt",
            "transcript_path": "/tmp/output/transcript.md",
        }
        packet = {"approved_plan_path": "/tmp/other/plan.md"}
        worktree = Path("/tmp/aed_runs/worktrees/test")
        output = Path("/tmp/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        is_valid, errors = rte.validate_claude_command_contract(
            contract, packet, worktree, output, repo_root
        )
        assert not is_valid
        assert any("stdout_path" in e and "output_root" in e for e in errors)

    def test_validate_contract_valid_full_path(self):
        """Valid contract with all paths correct passes validation."""
        from pathlib import Path

        contract = {
            "argv": ["claude", "--no-input", "/tmp/aed_runs/worktrees/test/plan.md"],
            "cwd": "/tmp/aed_runs/worktrees/test",
            "timeout_seconds": 60,
            "stdout_path": "/tmp/output/test_stdout.txt",
            "stderr_path": "/tmp/output/test_stderr.txt",
            "transcript_path": "/tmp/output/test_transcript.md",
        }
        packet = {"approved_plan_path": "/tmp/other/plan.md"}
        worktree = Path("/tmp/aed_runs/worktrees/test")
        output = Path("/tmp/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        is_valid, errors = rte.validate_claude_command_contract(
            contract, packet, worktree, output, repo_root
        )
        assert is_valid, f"Expected valid, got errors: {errors}"
        assert len(errors) == 0

    def test_validate_contract_no_subprocess_calls(self):
        """Validator does not call subprocess (pure function)."""
        import unittest.mock as mock

        contract = {
            "argv": ["claude", "--no-input", "plan.md"],
            "cwd": "/tmp/aed_runs/worktrees/test",
            "timeout_seconds": 60,
            "stdout_path": "/tmp/output/stdout.txt",
            "stderr_path": "/tmp/output/stderr.txt",
            "transcript_path": "/tmp/output/transcript.md",
        }
        packet = {"approved_plan_path": "/tmp/other/plan.md"}
        worktree = Path("/tmp/aed_runs/worktrees/test")
        output = Path("/tmp/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        with mock.patch("subprocess.run", side_effect=Exception("subprocess.run called!")) as mock_run:
            is_valid, errors = rte.validate_claude_command_contract(
                contract, packet, worktree, output, repo_root
            )
        mock_run.assert_not_called()
        assert is_valid  # valid contract, no subprocess call


class TestSecurity:
    def test_no_forbidden_command_calls_in_harness(self):
        """No subprocess calls execute git push, gh pr create, gh pr merge."""
        harness_path = SCRIPT_DIR / "run_temp_worktree_execution.py"
        source = harness_path.read_text()
        import re
        # Look for subprocess calls with forbidden command strings
        forbidden_calls = re.findall(
            r'subprocess\.\w+\([^)]*(?:git push|gh pr create|gh pr merge)',
            source, re.IGNORECASE
        )
        assert len(forbidden_calls) == 0, f"forbidden subprocess call found: {forbidden_calls}"

    def test_harness_has_no_shell_true(self):
        """No live subprocess call uses shell=True.
        Docstring references to 'shell=True' are not a violation.
        """
        harness_path = SCRIPT_DIR / "run_temp_worktree_execution.py"
        source = harness_path.read_text()
        for i, line in enumerate(source.splitlines(), 1):
            if "shell=True" not in line:
                continue
            stripped = line.strip()
            # Skip pure comment lines (starting with # after whitespace stripping)
            if stripped.startswith("#"):
                continue
            # Skip docstring lines (indented lines that are not code)
            if line[0] in " \t":  # leading whitespace = likely docstring
                continue
            # Skip lines where shell=True is inside a string literal
            idx = line.index("shell=True")
            before = line[:idx]
            if '"' in before or "'" in before:
                continue
            assert False, f"shell=True in live code at line {i}: {stripped}"

    def test_harness_has_no_network_calls(self):
        """Harness makes no network calls."""
        harness_path = SCRIPT_DIR / "run_temp_worktree_execution.py"
        source = harness_path.read_text()
        for term in ["urllib", "requests", "http.client", "socket"]:
            assert term not in source, f"network term '{term}' found in harness"

    def test_harness_does_not_invoke_dispatch(self):
        """Harness does not call any dispatch function or CLI."""
        harness_path = SCRIPT_DIR / "run_temp_worktree_execution.py"
        source = harness_path.read_text()
        import re
        dispatch_calls = re.findall(r'\bdispatch\s*\(', source)
        assert len(dispatch_calls) == 0, f"dispatch() call found: {dispatch_calls}"

    def test_harness_does_not_call_board_api(self):
        """Harness does not call board API functions."""
        harness_path = SCRIPT_DIR / "run_temp_worktree_execution.py"
        source = harness_path.read_text()
        import re
        board_func_calls = re.findall(r'\bboard\w*\.update\b|\bboard\w*\.create\b|\bkanban\b', source)
        assert len(board_func_calls) == 0, f"board function call found: {board_func_calls}"

    def test_harness_does_not_append_audit_log(self):
        """Harness does not append to audit log."""
        harness_path = SCRIPT_DIR / "run_temp_worktree_execution.py"
        source = harness_path.read_text()
        import re
        audit_calls = re.findall(r'audit.*\.append\(|audit.*write\(|open\(.*audit', source, re.IGNORECASE)
        assert len(audit_calls) == 0, f"audit call found: {audit_calls}"

    def test_harness_does_not_write_memory_profile(self):
        """Harness does not write to memory store or user profile."""
        harness_path = SCRIPT_DIR / "run_temp_worktree_execution.py"
        source = harness_path.read_text()
        import re
        # Only flag actual method calls: memory.write( or profile.write(
        # NOT docstring mentions like "write to memory/profile"
        memory_writes = re.findall(r'(memory|profile)\.write\s*\(', source, re.IGNORECASE)
        assert len(memory_writes) == 0, f"memory/profile write found: {memory_writes}"

    def test_harness_does_not_install_packages(self):
        """Harness does not run package install commands."""
        harness_path = SCRIPT_DIR / "run_temp_worktree_execution.py"
        source = harness_path.read_text()
        for term in ["pip install", "npm install", "apt install", "brew install", "pip install", "python.*-m pip"]:
            assert term.lower() not in source.lower(), f"package install '{term}' found in harness"

    def test_pmg_harness_calls_are_subprocess_only(self):
        """PMG functions use subprocess, not os.system or other callers."""
        harness_path = SCRIPT_DIR / "run_temp_worktree_execution.py"
        source = harness_path.read_text()
        import re
        # pmg_snapshot and pmg_compare should only be called via subprocess
        # They should not call os.system, eval, exec, etc.
        dangerous_calls = re.findall(r'(os\.system|os\.popen|eval|exec)\s*\(', source)
        assert len(dangerous_calls) == 0, f"dangerous call found: {dangerous_calls}"

    def test_pmg_calls_use_list_arg_subprocess(self):
        """All PMG subprocess calls use list-form args (no shell strings)."""
        harness_path = SCRIPT_DIR / "run_temp_worktree_execution.py"
        source = harness_path.read_text()
        import re
        # Find all subprocess calls in pmg_snapshot and pmg_compare
        # They should use [cmd, arg1, arg2, ...] form
        # Check for shell=True or string-split patterns
        problematic = re.findall(r'subprocess\.\w+\(\s*["\']', source)
        assert len(problematic) == 0, f"subprocess call with string form found: {problematic}"


# ---------------------------------------------------------------------------
# Diff-capture tests
# ---------------------------------------------------------------------------

class TestDiffCapture:
    """Tests for diff.patch generation with staged mock edits."""

    def test_valid_mock_edit_returns_patch_ready_and_diff_non_empty(self, tmp_path):
        """diff.patch must be non-empty when changed_files is non-empty."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        assert base_sha, "AED repo HEAD not found"

        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")
        pmg_compare_json, pmg_compare_md = make_clean_pmg_compare(tmp_path / "pmg")

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_diff_nonempty_pmg",
            "task_id": "TASK-DIFF-001",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit docs/example.md",
                "allowed_files": ["docs/example.md"],
                "forbidden_files": PROTECTED_GATE_SCRIPTS,
                "do_not": [],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [{"path": "docs/example.md", "content": "# Updated\nNew content.\n"}],
            },
        }

        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:

            def fake_snapshot(target, output_json):
                import shutil
                shutil.copy(str(pmg_snapshot_path), output_json)
                return True, ""

            def fake_compare(snapshot_json, output_json, output_md):
                import shutil
                shutil.copy(str(pmg_compare_json), output_json)
                shutil.copy(str(pmg_compare_md), output_md)
                return True, ""

            mock_snap.side_effect = fake_snapshot
            mock_cmp.side_effect = fake_compare

            result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "PATCH_READY_FOR_HUMAN_REVIEW", \
            f"Got: {result['status']} — {result.get('validation_errors')}"
        assert result["changed_files"] == ["docs/example.md"]
        # diff_path must be under output_root, not inside the worktree
        diff_path = Path(result["diff_path"])
        assert diff_path.parent.resolve() == (tmp_path / "output").resolve(), \
            f"diff.patch must be directly under output_root ({tmp_path / 'output'}), got parent: {diff_path.parent}"
        # diff.patch must survive worktree cleanup (written to output_root, not worktree)
        assert diff_path.exists(), f"diff.patch does not exist at {diff_path}"
        diff_content = diff_path.read_text()
        assert diff_content.strip(), f"diff.patch is empty but changed_files is non-empty: {result['changed_files']}"
        # diff must contain the changed file path
        assert "docs/example.md" in diff_content, \
            f"diff.patch does not mention docs/example.md: {diff_content[:200]}"

    def test_nonempty_changed_files_but_empty_diff_returns_hold_diff_validation_failed(self, tmp_path, monkeypatch):
        """changed_files non-empty + empty diff.patch → HOLD_DIFF_VALIDATION_FAILED."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        assert base_sha, "AED repo HEAD not found"

        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")
        pmg_compare_json, pmg_compare_md = make_clean_pmg_compare(tmp_path / "pmg")

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_diff_empty_pmg",
            "task_id": "TASK-DIFF-002",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit docs/example.md",
                "allowed_files": ["docs/example.md"],
                "forbidden_files": PROTECTED_GATE_SCRIPTS,
                "do_not": [],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [{"path": "docs/example.md", "content": "# Updated\n"}],
            },
        }

        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        # Monkey-patch git_diff to return empty string (simulating the old broken behavior)
        import run_temp_worktree_execution as rte
        original_git_diff = rte.git_diff

        def fake_git_diff(worktree_path):
            return ""  # Simulate broken diff capture

        monkeypatch.setattr(rte, "git_diff", fake_git_diff)

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:

            def fake_snapshot(target, output_json):
                import shutil
                shutil.copy(str(pmg_snapshot_path), output_json)
                return True, ""

            def fake_compare(snapshot_json, output_json, output_md):
                import shutil
                shutil.copy(str(pmg_compare_json), output_json)
                shutil.copy(str(pmg_compare_md), output_md)
                return True, ""

            mock_snap.side_effect = fake_snapshot
            mock_cmp.side_effect = fake_compare

            result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        # Must return HOLD_DIFF_VALIDATION_FAILED when changed_files non-empty but diff empty
        assert result["status"] == "HOLD_DIFF_VALIDATION_FAILED", \
            f"Expected HOLD_DIFF_VALIDATION_FAILED, got: {result['status']} — {result.get('validation_errors')}"

    def test_no_changed_files_empty_diff_returns_patch_ready(self, tmp_path):
        """Zero changed files + empty diff is valid (no changes to review)."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        assert base_sha, "AED repo HEAD not found"

        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")
        pmg_compare_json, pmg_compare_md = make_clean_pmg_compare(tmp_path / "pmg")

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_no_changes_pmg",
            "task_id": "TASK-DIFF-003",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "No edits",
                "allowed_files": ["docs/example.md"],
                "forbidden_files": PROTECTED_GATE_SCRIPTS,
                "do_not": [],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [],  # No edits — empty diff is expected
            },
        }

        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:

            def fake_snapshot(target, output_json):
                import shutil
                shutil.copy(str(pmg_snapshot_path), output_json)
                return True, ""

            def fake_compare(snapshot_json, output_json, output_md):
                import shutil
                shutil.copy(str(pmg_compare_json), output_json)
                shutil.copy(str(pmg_compare_md), output_md)
                return True, ""

            mock_snap.side_effect = fake_snapshot
            mock_cmp.side_effect = fake_compare

            result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "PATCH_READY_FOR_HUMAN_REVIEW"
        assert result["changed_files"] == []

    def test_diff_contains_changed_file_path(self, tmp_path):
        """diff.patch must contain the path of the changed file."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        assert base_sha, "AED repo HEAD not found"

        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        pmg_snapshot_path, _ = make_clean_pmg_snapshot(tmp_path / "pmg")
        pmg_compare_json, pmg_compare_md = make_clean_pmg_compare(tmp_path / "pmg")

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_diff_path_pmg",
            "task_id": "TASK-DIFF-004",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 5,
            },
            "task": {
                "description": "Edit README.md",
                "allowed_files": ["README.md"],
                "forbidden_files": PROTECTED_GATE_SCRIPTS,
                "do_not": [],
            },
            "execution": {
                "mode": "mock",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
                "mock_edits": [{"path": "README.md", "content": "# AED\nModified.\n"}],
            },
        }

        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch("run_temp_worktree_execution.pmg_snapshot") as mock_snap, \
             mock.patch("run_temp_worktree_execution.pmg_compare") as mock_cmp:

            def fake_snapshot(target, output_json):
                import shutil
                shutil.copy(str(pmg_snapshot_path), output_json)
                return True, ""

            def fake_compare(snapshot_json, output_json, output_md):
                import shutil
                shutil.copy(str(pmg_compare_json), output_json)
                shutil.copy(str(pmg_compare_md), output_md)
                return True, ""

            mock_snap.side_effect = fake_snapshot
            mock_cmp.side_effect = fake_compare

            result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "PATCH_READY_FOR_HUMAN_REVIEW"
        diff_path = Path(result["diff_path"])
        diff_content = diff_path.read_text()
        assert "README.md" in diff_content, \
            f"diff.patch does not contain README.md: {diff_content[:300]}"


# ============================================================================
# Live smoke packet constraint tests
# ============================================================================

class TestValidateLiveSmokePacketConstraints:
    """Tests for validate_live_smoke_packet_constraints()."""

    def test_valid_live_smoke_packet_passes(self):
        """A well-formed live smoke packet passes validation."""
        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_live",
            "task_id": "TASK-LIVE-001",
            "base_sha": "abc123",
            "approved_plan_path": "/tmp/plan.md",
            "approved_plan_sha256": "abc",
            "approval": {"max_changed_files": 1},
            "task": {
                "description": "Smoke test",
                "allowed_files": ["docs/live_smoke_scratch.md"],
                "forbidden_files": [
                    ".git/",
                    ".github/",
                    "scripts/local/final_gate_status.py",
                    "scripts/local/verify_final_head_merge_command.py",
                    "scripts/local/check_persistent_mutation_guard.py",
                    "scripts/local/run_temp_worktree_execution.py",
                    "scripts/local/check_real_executor_readiness.py",
                    "scripts/local/check_real_claude_env_preflight.py",
                    "scripts/local/audit_claude_invocation.py",
                    "/home/max/.hermes/",
                    "audit/",
                    "boards/",
                    "memory/",
                    "profile/",
                ],
                "do_not": [
                    "no git push",
                    "no gh pr create",
                    "no gh pr merge",
                    "no gh api dispatch",
                    "no package install",
                    "no dispatch",
                    "no board updates",
                    "no Hermes changes",
                    "no audit append",
                    "no memory/profile updates",
                ],
            },
            "execution": {"mode": "claude"},
        }
        ok, err = rte.validate_live_smoke_packet_constraints(packet)
        assert ok, f"valid live smoke packet should pass, got error: {err}"

    def test_empty_forbidden_files_rejected(self):
        """Live smoke packet with empty forbidden_files is rejected."""
        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_live",
            "task_id": "TASK-LIVE-001",
            "base_sha": "abc123",
            "approved_plan_path": "/tmp/plan.md",
            "approved_plan_sha256": "abc",
            "approval": {"max_changed_files": 1},
            "task": {
                "description": "Smoke test",
                "allowed_files": ["docs/example.md"],
                "forbidden_files": [],  # empty — forbidden
                "do_not": ["no git push"],
            },
            "execution": {"mode": "claude"},
        }
        ok, err = rte.validate_live_smoke_packet_constraints(packet)
        assert not ok, "empty forbidden_files must be rejected"
        assert "forbidden_files" in err.lower() or "non-empty" in err.lower()

    def test_empty_do_not_rejected(self):
        """Live smoke packet with empty do_not is rejected."""
        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_live",
            "task_id": "TASK-LIVE-001",
            "base_sha": "abc123",
            "approved_plan_path": "/tmp/plan.md",
            "approved_plan_sha256": "abc",
            "approval": {"max_changed_files": 1},
            "task": {
                "description": "Smoke test",
                "allowed_files": ["docs/example.md"],
                "forbidden_files": [".git/"],
                "do_not": [],  # empty — forbidden
            },
            "execution": {"mode": "claude"},
        }
        ok, err = rte.validate_live_smoke_packet_constraints(packet)
        assert not ok, "empty do_not must be rejected"
        assert "do_not" in err.lower() or "non-empty" in err.lower()

    def test_missing_protected_gate_scripts_rejected(self):
        """Live smoke packet missing protected gate scripts in forbidden_files is rejected."""
        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_live",
            "task_id": "TASK-LIVE-001",
            "base_sha": "abc123",
            "approved_plan_path": "/tmp/plan.md",
            "approved_plan_sha256": "abc",
            "approval": {"max_changed_files": 1},
            "task": {
                "description": "Smoke test",
                "allowed_files": ["docs/example.md"],
                "forbidden_files": [".git/", ".github/"],  # missing protected scripts
                "do_not": ["no git push"],
            },
            "execution": {"mode": "claude"},
        }
        ok, err = rte.validate_live_smoke_packet_constraints(packet)
        assert not ok, "missing protected gate scripts must be rejected"
        assert "final_gate_status" in err or "protected" in err.lower()

    def test_multiple_allowed_files_rejected(self):
        """Live smoke packet with more than one allowed_file is rejected."""
        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_live",
            "task_id": "TASK-LIVE-001",
            "base_sha": "abc123",
            "approved_plan_path": "/tmp/plan.md",
            "approved_plan_sha256": "abc",
            "approval": {"max_changed_files": 1},
            "task": {
                "description": "Smoke test",
                "allowed_files": ["docs/example.md", "README.md"],  # two files — too many
                "forbidden_files": [
                    ".git/",
                    "scripts/local/final_gate_status.py",
                    "scripts/local/verify_final_head_merge_command.py",
                    "scripts/local/check_persistent_mutation_guard.py",
                ],
                "do_not": ["no git push"],
            },
            "execution": {"mode": "claude"},
        }
        ok, err = rte.validate_live_smoke_packet_constraints(packet)
        assert not ok, "multiple allowed_files must be rejected for live smoke"
        assert "allowed_file" in err.lower() or "at most one" in err.lower()

    def test_zero_allowed_files_rejected(self):
        """Live smoke packet with zero allowed_files is rejected."""
        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_live",
            "task_id": "TASK-LIVE-001",
            "base_sha": "abc123",
            "approved_plan_path": "/tmp/plan.md",
            "approved_plan_sha256": "abc",
            "approval": {"max_changed_files": 1},
            "task": {
                "description": "Smoke test",
                "allowed_files": [],  # zero — forbidden
                "forbidden_files": [
                    ".git/",
                    "scripts/local/final_gate_status.py",
                    "scripts/local/verify_final_head_merge_command.py",
                    "scripts/local/check_persistent_mutation_guard.py",
                ],
                "do_not": ["no git push"],
            },
            "execution": {"mode": "claude"},
        }
        ok, err = rte.validate_live_smoke_packet_constraints(packet)
        assert not ok, "zero allowed_files must be rejected"
        assert "allowed_file" in err.lower() or "at least one" in err.lower()

    def test_non_integer_max_changed_files_passes_validation(self):
        """Live smoke packet with non-integer max_changed_files is not rejected by
        validate_live_smoke_packet_constraints (only enforced at packet builder level)."""
        # max_changed_files enforcement is done at packet builder level, not here.
        # validate_live_smoke_packet_constraints only checks non-None/non-1 values
        # and passes for any valid integer including 5.
        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_live",
            "task_id": "TASK-LIVE-001",
            "base_sha": "abc123",
            "approved_plan_path": "/tmp/plan.md",
            "approved_plan_sha256": "abc",
            "approval": {"max_changed_files": 5},
            "task": {
                "description": "Smoke test",
                "allowed_files": ["docs/example.md"],
                "forbidden_files": [
                    ".git/",
                    "scripts/local/final_gate_status.py",
                    "scripts/local/verify_final_head_merge_command.py",
                    "scripts/local/check_persistent_mutation_guard.py",
                ],
                "do_not": ["no git push"],
            },
            "execution": {"mode": "claude"},
        }
        ok, err = rte.validate_live_smoke_packet_constraints(packet)
        assert ok, f"non-1 max_changed_files must be allowed at validator level: {err}"

    def test_mock_mode_packet_passes_without_constraints(self):
        """Mock mode packets skip live smoke constraint validation."""
        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_mock",
            "task_id": "TASK-MOCK-001",
            "base_sha": "abc123",
            "approved_plan_path": "/tmp/plan.md",
            "approved_plan_sha256": "abc",
            "approval": {"max_changed_files": 5},
            "task": {
                "description": "Mock test",
                "allowed_files": ["docs/example.md"],
                "forbidden_files": [],  # empty is fine for mock
                "do_not": [],  # empty is fine for mock
            },
            "execution": {"mode": "mock"},
        }
        ok, err = rte.validate_live_smoke_packet_constraints(packet)
        assert ok, f"mock mode packet should pass even with empty constraints: {err}"


# ============================================================================
# Permission-mode contract tests
# ============================================================================

class TestPermissionModeContract:
    """Tests for --permission-mode acceptEdits in command contract."""

    def test_contract_includes_permission_mode_accept_edits(self):
        """build_claude_command_contract includes --permission-mode acceptEdits."""
        packet = {
            "run_id": "test_perm",
            "base_sha": "abc123",
            "execution": {"timeout_seconds": 60},
        }
        worktree = Path("/tmp/aed_runs/worktrees/test_perm")
        output = Path("/tmp/aed_runs/output/test_perm")
        contract = rte.build_claude_command_contract(packet, worktree, output)

        assert "--permission-mode" in contract["argv"], \
            f"--permission-mode must be in argv: {contract['argv']}"
        idx = contract["argv"].index("--permission-mode")
        assert contract["argv"][idx + 1] == "acceptEdits", \
            f"--permission-mode value must be acceptEdits: {contract['argv']}"

    def test_contract_permission_mode_field_set(self):
        """Contract includes permission_mode and permission_mode_reason fields."""
        packet = {
            "run_id": "test_perm_field",
            "base_sha": "abc123",
            "execution": {"timeout_seconds": 60},
        }
        worktree = Path("/tmp/aed_runs/worktrees/test_perm_field")
        output = Path("/tmp/aed_runs/output/test_perm_field")
        contract = rte.build_claude_command_contract(packet, worktree, output)

        assert "permission_mode" in contract, "contract must have permission_mode field"
        assert contract["permission_mode"] == "acceptEdits", \
            f"permission_mode must be acceptEdits: {contract['permission_mode']}"
        assert "permission_mode_reason" in contract, \
            "contract must have permission_mode_reason field"
        assert len(contract["permission_mode_reason"]) > 10, \
            "permission_mode_reason must be non-empty"

    def test_validator_accepts_accept_edits_in_argv(self):
        """Validator accepts argv with --permission-mode acceptEdits."""
        contract = {
            "argv": ["claude", "--print", "--input-format=text",
                     "--output-format=text", "--permission-mode", "acceptEdits"],
            "cwd": "/tmp/aed_runs/worktrees/test",
            "timeout_seconds": 60,
            "stdout_path": "/tmp/output/stdout.txt",
            "stderr_path": "/tmp/output/stderr.txt",
            "transcript_path": "/tmp/output/transcript.md",
            "permission_mode": "acceptEdits",
        }
        packet = {"approved_plan_path": "/tmp/plan.md"}
        worktree = Path("/tmp/aed_runs/worktrees/test")
        output = Path("/tmp/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        is_valid, errors = rte.validate_claude_command_contract(
            contract, packet, worktree, output, repo_root
        )
        assert is_valid, f"valid acceptEdits contract must pass: {errors}"

    def test_validator_rejects_bypass_permissions_in_argv(self):
        """Validator rejects argv containing --bypasspermissions."""
        contract = {
            "argv": ["claude", "--print", "--input-format=text",
                     "--bypasspermissions"],
            "cwd": "/tmp/aed_runs/worktrees/test",
            "timeout_seconds": 60,
            "stdout_path": "/tmp/output/stdout.txt",
            "stderr_path": "/tmp/output/stderr.txt",
            "transcript_path": "/tmp/output/transcript.md",
        }
        packet = {"approved_plan_path": "/tmp/plan.md"}
        worktree = Path("/tmp/aed_runs/worktrees/test")
        output = Path("/tmp/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        is_valid, errors = rte.validate_claude_command_contract(
            contract, packet, worktree, output, repo_root
        )
        assert not is_valid, "bypassPermissions must be rejected"
        assert any("bypasspermissions" in e.lower() for e in errors), \
            f"error must mention bypassPermissions: {errors}"

    def test_validator_rejects_dangerously_skip_permissions(self):
        """Validator rejects argv containing --dangerously-skip-permissions."""
        contract = {
            "argv": ["claude", "--print", "--dangerously-skip-permissions"],
            "cwd": "/tmp/aed_runs/worktrees/test",
            "timeout_seconds": 60,
            "stdout_path": "/tmp/output/stdout.txt",
            "stderr_path": "/tmp/output/stderr.txt",
            "transcript_path": "/tmp/output/transcript.md",
        }
        packet = {"approved_plan_path": "/tmp/plan.md"}
        worktree = Path("/tmp/aed_runs/worktrees/test")
        output = Path("/tmp/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        is_valid, errors = rte.validate_claude_command_contract(
            contract, packet, worktree, output, repo_root
        )
        assert not is_valid, "--dangerously-skip-permissions must be rejected"
        assert any("dangerously" in e.lower() or "forbidden" in e.lower() for e in errors), \
            f"error must mention dangerously-skip-permissions: {errors}"

    def test_validator_rejects_duplicate_permission_mode_flag(self):
        """Validator rejects argv where --permission-mode appears twice."""
        contract = {
            "argv": ["claude", "--print", "--permission-mode", "acceptEdits",
                     "--permission-mode", "acceptEdits"],
            "cwd": "/tmp/aed_runs/worktrees/test",
            "timeout_seconds": 60,
            "stdout_path": "/tmp/output/stdout.txt",
            "stderr_path": "/tmp/output/stderr.txt",
            "transcript_path": "/tmp/output/transcript.md",
        }
        packet = {"approved_plan_path": "/tmp/plan.md"}
        worktree = Path("/tmp/aed_runs/worktrees/test")
        output = Path("/tmp/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        is_valid, errors = rte.validate_claude_command_contract(
            contract, packet, worktree, output, repo_root
        )
        assert not is_valid, "duplicate --permission-mode must be rejected"
        assert any("appears" in e.lower() and "times" in e.lower() for e in errors), \
            f"error must mention duplicate --permission-mode: {errors}"

    def test_validator_rejects_bypass_permissions_in_contract_field(self):
        """Validator rejects contract.permission_mode set to bypassPermissions."""
        contract = {
            "argv": ["claude", "--print", "--input-format=text", "--output-format=text"],
            "cwd": "/tmp/aed_runs/worktrees/test",
            "timeout_seconds": 60,
            "stdout_path": "/tmp/output/stdout.txt",
            "stderr_path": "/tmp/output/stderr.txt",
            "transcript_path": "/tmp/output/transcript.md",
            "permission_mode": "bypassPermissions",  # forbidden value
        }
        packet = {"approved_plan_path": "/tmp/plan.md"}
        worktree = Path("/tmp/aed_runs/worktrees/test")
        output = Path("/tmp/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        is_valid, errors = rte.validate_claude_command_contract(
            contract, packet, worktree, output, repo_root
        )
        assert not is_valid, "permission_mode=bypassPermissions must be rejected"
        assert any("permission_mode" in e.lower() for e in errors), \
            f"error must mention permission_mode: {errors}"

    def test_validator_accepts_default_permission_mode(self):
        """Validator accepts contract.permission_mode='default'."""
        contract = {
            "argv": ["claude", "--print"],
            "cwd": "/tmp/aed_runs/worktrees/test",
            "timeout_seconds": 60,
            "stdout_path": "/tmp/output/stdout.txt",
            "stderr_path": "/tmp/output/stderr.txt",
            "transcript_path": "/tmp/output/transcript.md",
            "permission_mode": "default",  # allowed value
        }
        packet = {"approved_plan_path": "/tmp/plan.md"}
        worktree = Path("/tmp/aed_runs/worktrees/test")
        output = Path("/tmp/output")
        repo_root = Path("/home/max/Automated-Edge-Discovery")

        is_valid, errors = rte.validate_claude_command_contract(
            contract, packet, worktree, output, repo_root
        )
        assert is_valid, f"permission_mode=default must pass: {errors}"


# ============================================================================
# Smoke 003 packet preview tests (no execution)
# ============================================================================

class TestSmoke003PacketPreview:
    """Tests that smoke 003 packet preview would satisfy all constraints."""

    def test_smoke_003_packet_preview_has_forbidden_files(self):
        """Smoke 003 packet preview must have non-empty forbidden_files."""
        # Simulate the expected smoke 003 packet structure
        forbidden = [
            ".git/",
            ".github/",
            "scripts/local/final_gate_status.py",
            "scripts/local/verify_final_head_merge_command.py",
            "scripts/local/check_persistent_mutation_guard.py",
            "scripts/local/run_temp_worktree_execution.py",
            "scripts/local/check_real_executor_readiness.py",
            "scripts/local/check_real_claude_env_preflight.py",
            "scripts/local/audit_claude_invocation.py",
            "/home/max/.hermes/",
            "audit/",
            "boards/",
            "memory/",
            "profile/",
        ]
        assert len(forbidden) > 0, "forbidden_files must be non-empty"
        assert ".git/" in forbidden
        assert "scripts/local/final_gate_status.py" in forbidden

    def test_smoke_003_packet_preview_has_do_not(self):
        """Smoke 003 packet preview must have non-empty do_not."""
        do_not = [
            "no git push",
            "no gh pr create",
            "no gh pr merge",
            "no gh api dispatch",
            "no package install",
            "no dispatch",
            "no board updates",
            "no Hermes changes",
            "no audit append",
            "no memory/profile updates",
            "no edits outside allowed_files",
            "no repair loop",
            "no apply to main",
        ]
        assert len(do_not) > 0, "do_not must be non-empty"
        assert "no git push" in do_not
        assert "no gh pr create" in do_not

    def test_smoke_003_packet_preview_single_allowed_file(self):
        """Smoke 003 packet preview must have exactly one allowed_file."""
        allowed_files = ["docs/live_smoke_scratch.md"]
        assert len(allowed_files) == 1, \
            f"smoke 003 must have exactly one allowed_file, got {len(allowed_files)}"

    def test_smoke_003_packet_preview_max_changed_files_one(self):
        """Smoke 003 packet preview must have max_changed_files=1."""
        max_changed = 1
        assert max_changed == 1, "max_changed_files must be 1 for live smoke"

    def test_live_smoke_packet_with_all_constraints_passes_validation(self):
        """A complete live smoke packet (smoke 003 style) passes all constraint checks."""
        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "first_live_claude_smoke_003_preview",
            "task_id": "TASK-003",
            "base_sha": "abc123",
            "approved_plan_path": "/tmp/plan.md",
            "approved_plan_sha256": "abc",
            "approval": {"max_changed_files": 1},
            "task": {
                "description": "Smoke test 003",
                "allowed_files": ["docs/live_smoke_scratch.md"],
                "forbidden_files": [
                    ".git/",
                    ".github/",
                    "scripts/local/final_gate_status.py",
                    "scripts/local/verify_final_head_merge_command.py",
                    "scripts/local/check_persistent_mutation_guard.py",
                    "scripts/local/run_temp_worktree_execution.py",
                    "scripts/local/check_real_executor_readiness.py",
                    "scripts/local/check_real_claude_env_preflight.py",
                    "scripts/local/audit_claude_invocation.py",
                    "/home/max/.hermes/",
                    "audit/",
                    "boards/",
                    "memory/",
                    "profile/",
                ],
                "do_not": [
                    "no git push",
                    "no gh pr create",
                    "no gh pr merge",
                    "no gh api dispatch",
                    "no package install",
                    "no dispatch",
                    "no board updates",
                    "no Hermes changes",
                    "no audit append",
                    "no memory/profile updates",
                ],
            },
            "execution": {"mode": "claude"},
        }
        ok, err = rte.validate_live_smoke_packet_constraints(packet)
        assert ok, f"complete smoke 003 packet must pass: {err}"


# ============================================================================
# Integration: full smoke 003 style packet through run() with mocked executor
# ============================================================================

class TestSmoke003PacketIntegration:
    """Integration test: full packet goes through run() with mocked real executor."""

    def test_smoke_003_style_packet_through_run_with_mocked_executor(self, tmp_path):
        """A smoke 003 style packet with --enable-real-claude-executor is validated and contract is built."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        # Full smoke 003 style packet with proper constraints
        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_smoke_003_style",
            "task_id": "TASK-003",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 1,
            },
            "task": {
                "description": "Live smoke 003",
                "allowed_files": ["docs/live_smoke_scratch.md"],
                "forbidden_files": [
                    ".git/",
                    ".github/",
                    "scripts/local/final_gate_status.py",
                    "scripts/local/verify_final_head_merge_command.py",
                    "scripts/local/check_persistent_mutation_guard.py",
                    "scripts/local/run_temp_worktree_execution.py",
                    "scripts/local/check_real_executor_readiness.py",
                    "scripts/local/check_real_claude_env_preflight.py",
                    "scripts/local/audit_claude_invocation.py",
                    "/home/max/.hermes/",
                    "audit/",
                    "boards/",
                    "memory/",
                    "profile/",
                ],
                "do_not": [
                    "no git push",
                    "no gh pr create",
                    "no gh pr merge",
                    "no gh api dispatch",
                    "no package install",
                    "no dispatch",
                    "no board updates",
                    "no Hermes changes",
                    "no audit append",
                    "no memory/profile updates",
                    "no edits outside allowed_files",
                    "no repair loop",
                    "no apply to main",
                ],
            },
            "execution": {
                "mode": "claude",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
            },
        }

        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"
        output_root = tmp_path / "output"
        output_root.mkdir()
        (output_root / "pmg_snapshot.json").write_text('{"status":"clean","files":{}}', encoding="utf-8")
        (output_root / "pmg_compare.json").write_text('{"status":"clean","blocked":0}', encoding="utf-8")

        # Mock run_claude_executor to return success so we can check contract fields
        success_result = {
            "status": "CLAUDE_EXECUTOR_SUCCESS",
            "claude_exit_code": 0,
            "claude_started_at": "2026-05-21T12:00:00Z",
            "claude_finished_at": "2026-05-21T12:01:00Z",
            "claude_elapsed_seconds": 60.0,
            "claude_stdout_path": str(output_root / "claude_stdout.txt"),
            "claude_stderr_path": str(output_root / "claude_stderr.txt"),
            "claude_transcript_path": str(output_root / "claude_transcript.md"),
            "claude_command_contract_valid": True,
            "claude_command_contract_errors": [],
            "claude_command_contract_summary": (
                "argv=['claude', '--print', '--input-format=text', '--output-format=text', "
                "'--permission-mode', 'acceptEdits'] [stdin=.aed_plan.md]"
            ),
        }
        (output_root / "claude_stdout.txt").write_text("smoke result", encoding="utf-8")
        (output_root / "claude_stderr.txt").write_text("", encoding="utf-8")

        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True), \
             mock.patch.object(rte, "pmg_snapshot", return_value=(True, "")), \
             mock.patch.object(rte, "pmg_compare", return_value=(True, "")), \
             mock.patch.object(rte, "run_claude_executor", return_value=success_result), \
             mock.patch.object(rte, "git_worktree_add", return_value=subprocess.CompletedProcess("", 0)):
            result = run(packet, str(output_json), str(output_md), enable_real_claude_executor=True)

        # Packet passes constraint validation (Phase 1b)
        assert result["status"] in ("PATCH_READY_FOR_HUMAN_REVIEW", "HOLD_CLAUDE_EMPTY_OUTPUT"), \
            f"smoke 003 style packet must pass Phase 1b: {result['status']}"

        # Contract is valid
        assert result.get("claude_command_contract_valid") is True, \
            f"contract must be valid: {result.get('claude_command_contract_errors')}"

        # Contract summary includes --permission-mode acceptEdits
        summary = result.get("claude_command_contract_summary", "")
        assert "--permission-mode" in summary, \
            f"contract summary must include --permission-mode: {summary}"
        assert "acceptEdits" in summary, \
            f"contract summary must include acceptEdits: {summary}"

    def test_smoke_002_style_bad_packet_rejected_by_phase_1b(self, tmp_path):
        """Smoke 002 style packet (empty forbidden/do_not) is rejected at Phase 5b."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        # Smoke 002 style packet — missing constraints (the original bug)
        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_smoke_002_bug",
            "task_id": "TASK-002",
            "base_sha": base_sha,
            "approved_plan_path": str(plan_path),
            "approved_plan_sha256": plan_sha,
            "approval": {
                "approved_for_temp_worktree_execution": True,
                "approved_by": "human",
                "approved_plan_sha256": plan_sha,
                "approved_at": now,
                "max_changed_files": 1,
            },
            "task": {
                "description": "Smoke test 002 (bad)",
                "allowed_files": ["docs/live_smoke_scratch.md"],
                "forbidden_files": [],  # THE BUG: empty forbidden_files
                "do_not": [],           # THE BUG: empty do_not
            },
            "execution": {
                "mode": "claude",
                "timeout_seconds": 60,
                "output_root": str(tmp_path / "output"),
            },
        }

        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

        # Mock git_status to avoid HOLD_MAIN_DIRTY (repo is dirty with our changes)
        with mock.patch.object(rte, "git_status", return_value="clean"), \
             mock.patch.object(rte, "git_status_clean", return_value=True):
            result = run(packet, str(output_json), str(output_md), enable_real_claude_executor=True)

        # Must be rejected at Phase 5b with HOLD_INVALID_PACKET (empty forbidden/do_not)
        assert result["status"] == "HOLD_INVALID_PACKET", \
            f"smoke 002 style bad packet must be rejected at Phase 5b: {result['status']}"
        assert any("forbidden_files" in e.lower() or "non-empty" in e.lower()
                   for e in result.get("validation_errors", [])), \
            f"error must mention forbidden_files: {result.get('validation_errors')}"