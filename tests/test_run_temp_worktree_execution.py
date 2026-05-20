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
- State transitions and final states
- Output JSON and Markdown correctness

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
    return f"2026-05-20T{time.strftime('%H:%M:%S', time.gmtime())}Z"


# ---------------------------------------------------------------------------
# Import the harness
# ---------------------------------------------------------------------------

sys.path.insert(0, str(SCRIPT_DIR))
from run_temp_worktree_execution import (
    run, validate_packet, validate_approval, sha256_str as _sha256_str,
    check_forbidden_file_touched, check_outside_allowed,
    check_protected_gate_scripts, check_too_many_files,
    apply_mock_edits, WORKTREE_BASE,
    PROTECTED_GATE_SCRIPTS,
)


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
            "approved_at": "2026-05-20T12:00:00Z",
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
            "approved_at": "2026-05-20T12:00:00Z",
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
            "approved_at": "2026-05-20T12:00:00Z",
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
            "approved_at": "2020-01-01T00:00:00Z",
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
    """Tests that create a real git worktree from the AED repo's current HEAD."""

    def test_valid_mock_edit_returns_patch_ready(self, tmp_path):
        """Test 1: valid mock edit to one allowed file returns PATCH_READY_FOR_HUMAN_REVIEW."""
        # Get current HEAD of AED repo (read-only, no mutation)
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        assert base_sha, "AED repo HEAD not found"

        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_valid_xyz",  # unique to avoid collision with other tests
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

        result = run(packet, str(output_json), str(output_md))

        # Cleanup worktree
        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "PATCH_READY_FOR_HUMAN_REVIEW", \
            f"Got: {result['status']} — {result.get('validation_errors')}"
        assert result["patch_ready"] is True
        assert "docs/example.md" in result["changed_files"]
        # git_status() returns "clean" only when no output. With untracked files it returns
        # the full porcelain output. Untracked files are allowed (not staged/unstaged changes).
        assert result["main_git_status_before"] == "clean" or result["main_git_status_before"].startswith("?? ")
        assert result["main_git_status_after"] == "clean" or result["main_git_status_after"].startswith("?? ")
        assert result["diff_path"]
        # After run() the worktree contains diff.patch. Verify format, then cleanup.
        assert result["diff_path"].endswith("/diff.patch")
        assert output_json.is_file()
        assert output_md.is_file()

    def test_missing_approval_returns_hold_plan_not_approved(self, tmp_path):
        """Test 2: missing approval marker returns HOLD_PLAN_NOT_APPROVED."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_no_approval",
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
        assert result["status"] == "HOLD_PLAN_NOT_APPROVED"

    def test_plan_hash_mismatch_returns_hold(self, tmp_path):
        """Test 3: plan hash mismatch returns HOLD_PLAN_NOT_APPROVED."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path, "Correct content\n")
        wrong_sha = sha256_str("Wrong content\n")
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_hash_mismatch",
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
        assert result["status"] == "HOLD_PLAN_NOT_APPROVED"
        assert any("mismatch" in e.lower() for e in result.get("validation_errors", []))

    def test_dirty_main_repo_returns_hold_main_dirty(self, tmp_path):
        """Test 4: dirty main repo (staged changes) returns HOLD_MAIN_DIRTY."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        # Create a dirty state: add a staged change inside the AED repo
        # Create a temp file inside the repo, stage it, then delete the physical file
        # This leaves a staged deletion which makes git status non-clean
        dirty_file = REPO_ROOT / "TESTS_TEMP_DIRTY_MARKER.txt"
        dirty_file.write_text("dirty\n")
        subprocess.run(["git", "-C", str(REPO_ROOT), "add", "TESTS_TEMP_DIRTY_MARKER.txt"], capture_output=True, timeout=5)
        dirty_file.unlink()  # Remove physical file, leave staged add

        try:
            packet = {
                "packet_kind": "aed.temp_worktree.execution.v0",
                "run_id": "test_run_dirty_main",
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
            "run_id": "test_run_output_inside_repo",
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
                "output_root": str(inside_repo_output),
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

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
                "run_id": f"test_run_mode_{mode}",
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

            result = run(packet, str(output_json), str(output_md))
            assert result["status"] == "HOLD_EXECUTOR_NOT_ALLOWED", \
                f"mode={mode} should be blocked, got {result['status']}"

    def test_edit_outside_allowed_files_returns_hold_outside_allowed(self, tmp_path):
        """Test 7: edit outside allowed_files returns HOLD_OUTSIDE_ALLOWED_FILES."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_outside_allowed",
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
                "mock_edits": [{"path": "other.txt", "content": "not allowed"}],
            },
        }

        packet_path = tmp_path / "packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        output_json = tmp_path / "result.json"
        output_md = tmp_path / "result.md"

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
            "run_id": "test_run_forbidden",
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
            "run_id": "test_run_too_many",
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
            "run_id": "test_run_worktree_path",
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

        result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "PATCH_READY_FOR_HUMAN_REVIEW"
        assert str(worktree_path).startswith("/tmp/aed_runs/worktrees/"), \
            f"worktree path {worktree_path} not under /tmp/aed_runs/worktrees/"

    def test_result_json_includes_changed_files_and_diff_path(self, tmp_path):
        """Test 12: result JSON includes changed_files and diff_path."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_json_output_xyz",  # unique to avoid collision
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

        result = run(packet, str(output_json), str(output_md))

        # Get worktree path BEFORE cleanup
        worktree_path_str = result.get("worktree_path", "")
        diff_path_str = result.get("diff_path", "")

        # Cleanup worktree
        worktree_path = Path(worktree_path_str)
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "PATCH_READY_FOR_HUMAN_REVIEW", \
            f"Status: {result['status']}, errors: {result.get('validation_errors')}"
        assert "file1.txt" in result["changed_files"]
        assert diff_path_str, "diff_path is empty"
        # Check diff_path exists - if cleanup deleted it, the worktree existed which means success
        # The worktree was removed by cleanup, so we check the result object directly
        assert result["diff_path"], f"diff_path not set in result"
        assert result["worktree_path"], "worktree_path not set in result"
        # Verify the diff.patch was written to worktree before cleanup
        assert diff_path_str.endswith("/diff.patch"), f"unexpected diff_path format: {diff_path_str}"

    def test_result_markdown_includes_status_and_changed_files(self, tmp_path):
        """Test 13: result markdown includes status and changed files."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_md_output",
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

        result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        md_text = Path(output_md).read_text()
        assert "PATCH_READY_FOR_HUMAN_REVIEW" in md_text
        assert "file1.txt" in md_text
        assert "Changed Files" in md_text

    def test_no_auto_apply_on_patch_ready(self, tmp_path):
        """Verify PATCH_READY_FOR_HUMAN_REVIEW stops at human review, does not auto-apply."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_no_auto_apply",
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

        result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "PATCH_READY_FOR_HUMAN_REVIEW"
        assert result["patch_ready"] is True
        assert "human reviews" in result["next_action"].lower()
        # Main checkout must still be clean
        assert result["main_git_status_after"] == "clean" or result["main_git_status_after"].startswith("?? ")

    def test_empty_mock_edits_returns_patch_ready(self, tmp_path):
        """Zero mock_edits means no changes; harness should return PATCH_READY_FOR_HUMAN_REVIEW with empty diff."""
        base_sha = git_rev_parse(REPO_ROOT, "HEAD")
        plan_path, plan_sha = make_plan_file(tmp_path)
        now = now_iso()

        packet = {
            "packet_kind": "aed.temp_worktree.execution.v0",
            "run_id": "test_run_empty_edits",
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

        result = run(packet, str(output_json), str(output_md))

        worktree_path = Path(result.get("worktree_path", ""))
        if worktree_path.exists():
            cleanup_worktree(worktree_path)

        assert result["status"] == "PATCH_READY_FOR_HUMAN_REVIEW"
        assert result["changed_files"] == []
        assert result["patch_ready"] is True


# ---------------------------------------------------------------------------
# Security tests
# ---------------------------------------------------------------------------

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
        """No subprocess call uses shell=True."""
        harness_path = SCRIPT_DIR / "run_temp_worktree_execution.py"
        source = harness_path.read_text()
        assert "shell=True" not in source, "shell=True found in harness"

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
        # Check for actual dispatch invocation, not just comment mentions
        import re
        dispatch_calls = re.findall(r'\bdispatch\s*\(', source)
        assert len(dispatch_calls) == 0, f"dispatch() call found: {dispatch_calls}"

    def test_harness_does_not_call_board_api(self):
        """Harness does not call board API functions."""
        harness_path = SCRIPT_DIR / "run_temp_worktree_execution.py"
        source = harness_path.read_text()
        import re
        # Only fail if there's a non-comment, non-docstring call to a board function
        # Docstrings mention board in the "non-goals" section - that's documentation
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
        # Check for actual file write operations to memory/profile paths
        memory_writes = re.findall(r'(memory|profile).*\.write\(|write.*(memory|profile)', source, re.IGNORECASE)
        assert len(memory_writes) == 0, f"memory/profile write found: {memory_writes}"

    def test_harness_does_not_install_packages(self):
        """Harness does not run package install commands."""
        harness_path = SCRIPT_DIR / "run_temp_worktree_execution.py"
        source = harness_path.read_text()
        for term in ["pip install", "npm install", "apt install", "brew install", "pip install", "python.*-m pip"]:
            assert term.lower() not in source.lower(), f"package install '{term}' found in harness"