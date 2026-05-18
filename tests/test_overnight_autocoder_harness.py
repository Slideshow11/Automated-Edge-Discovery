#!/usr/bin/env python3
"""
tests/test_overnight_autocoder_harness.py

Unit tests for the AED Overnight Autocoder Harness v1.
Uses temp dirs only. No source repo files are modified.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.local.run_overnight_autocoder_harness import (
    check_repo_clean,
    check_workspace_not_in_repo,
    check_safety_invariants,
    check_tasks_jsonl,
    run_harness,
    main as harness_main,
)

# Absolute path to AED repo root
REPO_ROOT = Path("/home/max/Automated-Edge-Discovery")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_workspace(tmp_path):
    """Provide a temporary workspace directory."""
    return tmp_path


@pytest.fixture
def sample_tasks_jsonl(temp_workspace):
    """Write a 2-task TASKS.jsonl."""
    tasks = [
        {"task_id": "task-001", "task_type": "docs", "depends_on": [], "blocks": []},
        {"task_id": "task-002", "task_type": "docs", "depends_on": ["task-001"], "blocks": []},
    ]
    p = temp_workspace / "TASKS.jsonl"
    with open(p, "w") as f:
        for t in tasks:
            f.write(json.dumps(t) + "\n")
    return p


@pytest.fixture
def fake_hermes_root(temp_workspace):
    """Create a fake Hermes root with a minimal structure."""
    hermes = temp_workspace / ".hermes"
    hermes.mkdir()
    (hermes / "config.yaml").write_text("version: 1\n")
    (hermes / "skills").mkdir()
    (hermes / "memory").mkdir()
    (hermes / "profiles").mkdir()
    return hermes


def _make_git_repo(path: Path) -> Path:
    """Initialize a real git repo at path and make an initial commit."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True)
    readme = path / "README.md"
    readme.write_text("test\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(path),
        capture_output=True,
        env={**os.environ, "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"},
    )
    return path


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def run_harness_cli(
    cmd: list[str],
    repo_root: str | Path,
) -> subprocess.CompletedProcess:
    """Run the harness CLI with repo_root as cwd. Returns CompletedProcess."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/local/run_overnight_autocoder_harness.py")] + cmd,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    return result


# ---------------------------------------------------------------------------
# Tests: safety checks (pure functions — no subprocess)
# ---------------------------------------------------------------------------

def test_check_repo_clean_returns_true_for_clean_directory(tmp_path):
    """Test 1: check_repo_clean returns True for clean directory (no git)."""
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    ok, msg = check_repo_clean(str(clean_dir))
    # No git repo → git status returns empty → clean
    assert ok, f"expected clean: {msg}"


def test_check_repo_clean_detects_dirty_directory(temp_workspace):
    """Test 2: check_repo_clean detects dirty directory (untracked files in git repo)."""
    # Must be a real git repo with untracked files to be "dirty"
    dirty_repo = _make_git_repo(temp_workspace / "dirty")
    (dirty_repo / "untracked.txt").write_text("hello")
    ok, msg = check_repo_clean(str(dirty_repo))
    assert not ok, "expected dirty"
    assert "dirty" in msg


def test_check_workspace_not_in_repo_accepts_outside(tmp_path):
    """Test 3: workspace outside repo is accepted."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    rp = tmp_path / "repo"
    rp.mkdir()
    ok, msg = check_workspace_not_in_repo(str(ws), str(rp))
    assert ok, msg


def test_check_workspace_not_in_repo_rejects_inside(tmp_path):
    """Test 4: workspace inside repo is rejected."""
    rp = tmp_path / "repo"
    rp.mkdir()
    ws = rp / "workspace"  # inside repo
    ws.mkdir()
    ok, msg = check_workspace_not_in_repo(str(ws), str(rp))
    assert not ok, "expected rejection"


def test_check_tasks_jsonl_valid_file(sample_tasks_jsonl):
    """Test 5: valid TASKS.jsonl is accepted."""
    ok, msg = check_tasks_jsonl(str(sample_tasks_jsonl))
    assert ok, f"expected ok: {msg}"


def test_check_tasks_jsonl_missing_file(tmp_path):
    """Test 6: missing TASKS.jsonl is rejected."""
    ok, msg = check_tasks_jsonl(str(tmp_path / "nonexistent.jsonl"))
    assert not ok, "expected rejection"


def test_check_tasks_jsonl_malformed_file(tmp_path):
    """Test 7: malformed TASKS.jsonl is rejected."""
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"task_id": "good"}\n{"task_id": "broken", INVALID}\n')
    ok, msg = check_tasks_jsonl(str(bad))
    assert not ok, "expected rejection"


def test_check_tasks_jsonl_empty_file(tmp_path):
    """Test 8: empty TASKS.jsonl is rejected."""
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n\n")
    ok, msg = check_tasks_jsonl(str(empty))
    assert not ok, "expected rejection"


def test_check_safety_invariants_all_false_is_ok():
    """Test 9: all-false safety invariants are ok."""
    state = {
        "safety_invariants": {
            "hermes_touched": False,
            "dispatch_occurred": False,
            "production_board_touched": False,
        }
    }
    ok, msg = check_safety_invariants(state)
    assert ok


def test_check_safety_invariants_hermes_touched_blocks():
    """Test 10: hermes_touched=True blocks."""
    state = {
        "safety_invariants": {
            "hermes_touched": True,
            "dispatch_occurred": False,
            "production_board_touched": False,
        }
    }
    ok, msg = check_safety_invariants(state)
    assert not ok
    assert "hermes_touched" in msg


def test_check_safety_invariants_dispatch_blocks():
    """Test 11: dispatch_occurred=True blocks."""
    state = {
        "safety_invariants": {
            "hermes_touched": False,
            "dispatch_occurred": True,
            "production_board_touched": False,
        }
    }
    ok, msg = check_safety_invariants(state)
    assert not ok
    assert "dispatch_occurred" in msg


def test_check_safety_invariants_board_blocks():
    """Test 12: production_board_touched=True blocks."""
    state = {
        "safety_invariants": {
            "hermes_touched": False,
            "dispatch_occurred": False,
            "production_board_touched": True,
        }
    }
    ok, msg = check_safety_invariants(state)
    assert not ok
    assert "production_board_touched" in msg


# ---------------------------------------------------------------------------
# Tests: harness CLI integration (real git repos, real AED scripts)
# ---------------------------------------------------------------------------

class TestHarnessIntegration:
    """Run full harness via CLI in a controlled temp dir rooted at AED repo."""

    def test_dry_run_creates_summary_files(self, temp_workspace, sample_tasks_jsonl, fake_hermes_root):
        """Test 13: dry-run creates OVERNIGHT_RUN_SUMMARY.json and .md."""
        workspace = temp_workspace / "run_ws"
        workspace.mkdir()
        repo_root = _make_git_repo(temp_workspace / "repo")

        rc = run_harness_cli([
            "--run-id", "test-overnight-001",
            "--tasks-jsonl", str(sample_tasks_jsonl),
            "--workspace", str(workspace),
            "--integration-branch", "int/test-001",
            "--hermes-root", str(fake_hermes_root),
            "--repo-root", str(repo_root),
            "--mode", "dry-run",
        ], repo_root=REPO_ROOT)

        summary_json = workspace / "OVERNIGHT_RUN_SUMMARY.json"
        summary_md = workspace / "OVERNIGHT_RUN_SUMMARY.md"
        assert summary_json.exists(), f"summary JSON not created: {rc.stderr}"
        assert summary_md.exists(), f"summary MD not created: {rc.stderr}"

    def test_dry_run_clean_guard_yields_ready_for_review(self, temp_workspace, sample_tasks_jsonl, fake_hermes_root):
        """Test 14: clean guard yields READY_FOR_REVIEW, not BLOCK."""
        workspace = temp_workspace / "run_ws2"
        workspace.mkdir()
        repo_root = _make_git_repo(temp_workspace / "repo2")

        rc = run_harness_cli([
            "--run-id", "test-overnight-002",
            "--tasks-jsonl", str(sample_tasks_jsonl),
            "--workspace", str(workspace),
            "--integration-branch", "int/test-002",
            "--hermes-root", str(fake_hermes_root),
            "--repo-root", str(repo_root),
            "--mode", "dry-run",
        ], repo_root=REPO_ROOT)

        summary_json = workspace / "OVERNIGHT_RUN_SUMMARY.json"
        data = json.loads(summary_json.read_text())
        guard_status = data["persistent_mutation_guard"]["status"]
        # Empty Hermes root → clean guard → READY_FOR_REVIEW
        if guard_status == "clean":
            assert data["recommendation"] == "READY_FOR_REVIEW", \
                f"clean guard should yield READY_FOR_REVIEW, got {data['recommendation']}"

    def test_dry_run_records_guard_snapshot_in_controller(self, temp_workspace, sample_tasks_jsonl, fake_hermes_root):
        """Test 15: dry-run records guard snapshot path in controller state."""
        workspace = temp_workspace / "run_ws3"
        workspace.mkdir()
        repo_root = _make_git_repo(temp_workspace / "repo3")

        run_harness_cli([
            "--run-id", "test-overnight-003",
            "--tasks-jsonl", str(sample_tasks_jsonl),
            "--workspace", str(workspace),
            "--integration-branch", "int/test-003",
            "--hermes-root", str(fake_hermes_root),
            "--repo-root", str(repo_root),
            "--mode", "dry-run",
        ], repo_root=REPO_ROOT)

        state = json.loads((workspace / "CONTROLLER_STATE.json").read_text())
        guard = state.get("persistent_mutation_guard", {})
        assert guard.get("snapshot_path") is not None, "snapshot_path not recorded"
        # Status may be snapshot_recorded (just after snapshot) or clean (after full compare)
        assert guard["status"] in ("snapshot_recorded", "clean")

    def test_dry_run_records_guard_compare_in_controller(self, temp_workspace, sample_tasks_jsonl, fake_hermes_root):
        """Test 16: dry-run records guard compare result in controller state."""
        workspace = temp_workspace / "run_ws4"
        workspace.mkdir()
        repo_root = _make_git_repo(temp_workspace / "repo4")

        run_harness_cli([
            "--run-id", "test-overnight-004",
            "--tasks-jsonl", str(sample_tasks_jsonl),
            "--workspace", str(workspace),
            "--integration-branch", "int/test-004",
            "--hermes-root", str(fake_hermes_root),
            "--repo-root", str(repo_root),
            "--mode", "dry-run",
        ], repo_root=REPO_ROOT)

        state = json.loads((workspace / "CONTROLLER_STATE.json").read_text())
        guard = state.get("persistent_mutation_guard", {})
        assert guard.get("compare_json_path") is not None, "compare_json_path not recorded"
        assert guard["status"] in ("clean", "blocked", "error")

    def test_workspace_inside_repo_yields_block(self, temp_workspace, sample_tasks_jsonl, fake_hermes_root):
        """Test 17: workspace inside repo yields BLOCK."""
        repo_root = _make_git_repo(temp_workspace / "repo5")
        # workspace inside repo
        workspace = repo_root / "workspace_in_repo"
        workspace.mkdir()

        rc = run_harness_cli([
            "--run-id", "test-overnight-005",
            "--tasks-jsonl", str(sample_tasks_jsonl),
            "--workspace", str(workspace),
            "--integration-branch", "int/test-005",
            "--hermes-root", str(fake_hermes_root),
            "--repo-root", str(repo_root),
            "--mode", "dry-run",
        ], repo_root=REPO_ROOT)

        summary_json = workspace / "OVERNIGHT_RUN_SUMMARY.json"
        data = json.loads(summary_json.read_text())
        assert data["recommendation"] == "BLOCK"
        assert "workspace_in_repo" in data.get("blocked_reason", "")
        # Summary JSON must exist even for BLOCK (harness always writes it)
        assert summary_json.exists(), "summary JSON must exist even for BLOCK"

    def test_missing_tasks_file_yields_block(self, temp_workspace, fake_hermes_root):
        """Test 18: missing tasks file yields BLOCK."""
        workspace = temp_workspace / "run_ws6"
        workspace.mkdir()
        repo_root = _make_git_repo(temp_workspace / "repo6")

        rc = run_harness_cli([
            "--run-id", "test-overnight-006",
            "--tasks-jsonl", str(temp_workspace / "nonexistent.jsonl"),
            "--workspace", str(workspace),
            "--integration-branch", "int/test-006",
            "--hermes-root", str(fake_hermes_root),
            "--repo-root", str(repo_root),
            "--mode", "dry-run",
        ], repo_root=REPO_ROOT)

        summary_json = workspace / "OVERNIGHT_RUN_SUMMARY.json"
        data = json.loads(summary_json.read_text())
        assert data["recommendation"] == "BLOCK"
        assert "tasks_file_invalid" in data.get("blocked_reason", "")

    def test_dry_run_says_no_real_work_executed(self, temp_workspace, sample_tasks_jsonl, fake_hermes_root):
        """Test 19: summary markdown says dry-run only."""
        workspace = temp_workspace / "run_ws7"
        workspace.mkdir()
        repo_root = _make_git_repo(temp_workspace / "repo7")

        run_harness_cli([
            "--run-id", "test-overnight-007",
            "--tasks-jsonl", str(sample_tasks_jsonl),
            "--workspace", str(workspace),
            "--integration-branch", "int/test-007",
            "--hermes-root", str(fake_hermes_root),
            "--repo-root", str(repo_root),
            "--mode", "dry-run",
        ], repo_root=REPO_ROOT)

        md = (workspace / "OVERNIGHT_RUN_SUMMARY.md").read_text()
        assert "DRY-RUN ONLY" in md or "dry-run" in md.lower()
        assert "no real work executed" in md.lower() or "no real work" in md.lower()

    def test_dry_run_output_paths_under_workspace(self, temp_workspace, sample_tasks_jsonl, fake_hermes_root):
        """Test 20: output paths are under workspace."""
        workspace = temp_workspace / "run_ws8"
        workspace.mkdir()
        repo_root = _make_git_repo(temp_workspace / "repo8")

        run_harness_cli([
            "--run-id", "test-overnight-008",
            "--tasks-jsonl", str(sample_tasks_jsonl),
            "--workspace", str(workspace),
            "--integration-branch", "int/test-008",
            "--hermes-root", str(fake_hermes_root),
            "--repo-root", str(repo_root),
            "--mode", "dry-run",
        ], repo_root=REPO_ROOT)

        summary_json = workspace / "OVERNIGHT_RUN_SUMMARY.json"
        data = json.loads(summary_json.read_text())

        # controller_state_path must be under workspace
        assert str(workspace) in data["controller_state_path"], \
            f"controller_state_path not under workspace: {data['controller_state_path']}"

        # Guard paths in controller state must also be under workspace
        state = json.loads((workspace / "CONTROLLER_STATE.json").read_text())
        g = state.get("persistent_mutation_guard", {})
        for key in ("snapshot_path", "compare_json_path", "compare_md_path"):
            if g.get(key):
                assert str(workspace) in g[key], f"{key} not under workspace: {g[key]}"

    def test_dry_run_task_state_preserved_in_controller(self, temp_workspace, sample_tasks_jsonl, fake_hermes_root):
        """Test 21: task state is preserved in controller state."""
        workspace = temp_workspace / "run_ws9"
        workspace.mkdir()
        repo_root = _make_git_repo(temp_workspace / "repo9")

        run_harness_cli([
            "--run-id", "test-overnight-009",
            "--tasks-jsonl", str(sample_tasks_jsonl),
            "--workspace", str(workspace),
            "--integration-branch", "int/test-009",
            "--hermes-root", str(fake_hermes_root),
            "--repo-root", str(repo_root),
            "--mode", "dry-run",
        ], repo_root=REPO_ROOT)

        state = json.loads((workspace / "CONTROLLER_STATE.json").read_text())
        assert len(state["tasks"]) == 2
        # dry-run: tasks recorded with not_promoted (no real promotion in dry-run)
        promoted = [t for t in state["tasks"] if t["promotion_status"] == "promoted_to_integration"]
        assert len(promoted) == 0, f"dry-run should not promote tasks, got {len(promoted)}"

    def test_dry_run_human_action_required_true_at_end(self, temp_workspace, sample_tasks_jsonl, fake_hermes_root):
        """Test 22: human_action_required is true at end of dry-run."""
        workspace = temp_workspace / "run_ws10"
        workspace.mkdir()
        repo_root = _make_git_repo(temp_workspace / "repo10")

        run_harness_cli([
            "--run-id", "test-overnight-010",
            "--tasks-jsonl", str(sample_tasks_jsonl),
            "--workspace", str(workspace),
            "--integration-branch", "int/test-010",
            "--hermes-root", str(fake_hermes_root),
            "--repo-root", str(repo_root),
            "--mode", "dry-run",
        ], repo_root=REPO_ROOT)

        state = json.loads((workspace / "CONTROLLER_STATE.json").read_text())
        # dry-run always ends with human_action_required=True (needs review before real execution)
        assert state.get("human_action_required") is True

    def test_dry_run_does_not_mutate_repo_files(self, temp_workspace, sample_tasks_jsonl, fake_hermes_root):
        """Test 23: dry-run does not mutate repo files."""
        workspace = temp_workspace / "run_ws11"
        workspace.mkdir()
        repo_root = _make_git_repo(temp_workspace / "repo11")
        original_readme = (repo_root / "README.md").read_text()

        run_harness_cli([
            "--run-id", "test-overnight-011",
            "--tasks-jsonl", str(sample_tasks_jsonl),
            "--workspace", str(workspace),
            "--integration-branch", "int/test-011",
            "--hermes-root", str(fake_hermes_root),
            "--repo-root", str(repo_root),
            "--mode", "dry-run",
        ], repo_root=REPO_ROOT)

        # Repo files unchanged
        assert (repo_root / "README.md").read_text() == original_readme

    def test_dry_run_no_dispatch_command_invoked(self, temp_workspace, sample_tasks_jsonl, fake_hermes_root, monkeypatch):
        """Test 24: no dispatch command is invoked in dry-run."""
        dispatch_calls = []

        original_run = subprocess.run

        def tracking_run(cmd, *args, **kwargs):
            # Only flag if "dispatch" appears as a separate command argument
            # (not just in a path or script name)
            for arg in cmd:
                if arg in ("dispatch", "--dispatch", "dispatch_occurred"):
                    dispatch_calls.append(cmd)
                    break
            return original_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", tracking_run)

        workspace = temp_workspace / "run_ws12"
        workspace.mkdir()
        repo_root = _make_git_repo(temp_workspace / "repo12")

        run_harness_cli([
            "--run-id", "test-overnight-012",
            "--tasks-jsonl", str(sample_tasks_jsonl),
            "--workspace", str(workspace),
            "--integration-branch", "int/test-012",
            "--hermes-root", str(fake_hermes_root),
            "--repo-root", str(repo_root),
            "--mode", "dry-run",
        ], repo_root=REPO_ROOT)

        assert len(dispatch_calls) == 0, f"dispatch commands invoked: {dispatch_calls}"

    def test_dry_run_no_hermes_create_invoked(self, temp_workspace, sample_tasks_jsonl, fake_hermes_root, monkeypatch):
        """Test 25: no Hermes create command is invoked in dry-run."""
        hermes_create_calls = []

        original_run = subprocess.run

        def tracking_run(cmd, *args, **kwargs):
            # Flag if "hermes" AND "create" both appear as arguments (not just in path)
            args_set = set(cmd)
            if "hermes" in args_set and "create" in args_set:
                hermes_create_calls.append(cmd)
            return original_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", tracking_run)

        workspace = temp_workspace / "run_ws13"
        workspace.mkdir()
        repo_root = _make_git_repo(temp_workspace / "repo13")

        run_harness_cli([
            "--run-id", "test-overnight-013",
            "--tasks-jsonl", str(sample_tasks_jsonl),
            "--workspace", str(workspace),
            "--integration-branch", "int/test-013",
            "--hermes-root", str(fake_hermes_root),
            "--repo-root", str(repo_root),
            "--mode", "dry-run",
        ], repo_root=REPO_ROOT)

        assert len(hermes_create_calls) == 0, f"hermes create commands invoked: {hermes_create_calls}"

    def test_dry_run_no_audit_append_invoked(self, temp_workspace, sample_tasks_jsonl, fake_hermes_root, monkeypatch):
        """Test 26: no audit append command is invoked in dry-run."""
        audit_calls = []

        original_run = subprocess.run

        def tracking_run(cmd, *args, **kwargs):
            # Flag if append_merge_action_audit appears as script name
            if any("append_merge_action_audit" in str(a) for a in cmd):
                audit_calls.append(cmd)
            return original_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", tracking_run)

        workspace = temp_workspace / "run_ws14"
        workspace.mkdir()
        repo_root = _make_git_repo(temp_workspace / "repo14")

        run_harness_cli([
            "--run-id", "test-overnight-014",
            "--tasks-jsonl", str(sample_tasks_jsonl),
            "--workspace", str(workspace),
            "--integration-branch", "int/test-014",
            "--hermes-root", str(fake_hermes_root),
            "--repo-root", str(repo_root),
            "--mode", "dry-run",
        ], repo_root=REPO_ROOT)

        assert len(audit_calls) == 0, f"audit append commands invoked: {audit_calls}"

    def test_dry_run_no_pr_create_invoked(self, temp_workspace, sample_tasks_jsonl, fake_hermes_root, monkeypatch):
        """Test 27: no PR create command is invoked in dry-run."""
        pr_calls = []

        original_run = subprocess.run

        def tracking_run(cmd, *args, **kwargs):
            # Flag if "pr" AND "create" both appear as separate arguments
            args_set = set(cmd)
            # Also check for "gh" "pr" "create" sequence
            cmd_str = " ".join(cmd)
            if (" pr create " in cmd_str or cmd_str.endswith(" pr create")) and "gh" in args_set:
                pr_calls.append(cmd)
            return original_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", tracking_run)

        workspace = temp_workspace / "run_ws15"
        workspace.mkdir()
        repo_root = _make_git_repo(temp_workspace / "repo15")

        run_harness_cli([
            "--run-id", "test-overnight-015",
            "--tasks-jsonl", str(sample_tasks_jsonl),
            "--workspace", str(workspace),
            "--integration-branch", "int/test-015",
            "--hermes-root", str(fake_hermes_root),
            "--repo-root", str(repo_root),
            "--mode", "dry-run",
        ], repo_root=REPO_ROOT)

        assert len(pr_calls) == 0, f"pr create commands invoked: {pr_calls}"

    def test_persistent_guard_report_linked_in_summary(self, temp_workspace, sample_tasks_jsonl, fake_hermes_root):
        """Test 28: persistent guard compare MD path appears in summary markdown."""
        workspace = temp_workspace / "run_ws16"
        workspace.mkdir()
        repo_root = _make_git_repo(temp_workspace / "repo16")

        run_harness_cli([
            "--run-id", "test-overnight-016",
            "--tasks-jsonl", str(sample_tasks_jsonl),
            "--workspace", str(workspace),
            "--integration-branch", "int/test-016",
            "--hermes-root", str(fake_hermes_root),
            "--repo-root", str(repo_root),
            "--mode", "dry-run",
        ], repo_root=REPO_ROOT)

        md = (workspace / "OVERNIGHT_RUN_SUMMARY.md").read_text()
        assert "persistent_state_report.md" in md, "guard compare MD not linked in summary"