#!/usr/bin/env python3
"""
Round-73 regression tests for the target-exclusion race.

Repair C: Enforce target exclusion before PR-to-target
upgrade.

The Round-52 fix allowed a PR-scoped run to upgrade its
scope to PR+target without checking that another
controller already holds the target-scoped lock.
_check_cross_scope_conflict only conflicts
repository-wide leases with narrower leases (PR < repo,
target < repo); it does NOT conflict PR and target scopes
(because they have different scope_keys). Another
controller can therefore hold the target-scoped lock while
this PR-scoped run authorizes a mutation of the same head
branch.

The Round-73 fix explicitly checks that no other run
holds the target-scoped lock for the same (repository,
target_pr_number, mutation_target) tuple. If another run
holds it, the upgrade is refused (fail closed).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.local import aed_supervisor_lock as sl


def _git(cwd: Path, *args: str, check: bool = True):
    import subprocess
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=check,
    )


def _make_state(tmp_path: Path) -> Path:
    """Create a minimal state file AND a LAUNCH_RECEIPT.json
    so authorize-mutation's launch-receipt check passes.
    Returns the state file path.
    """
    state_path = tmp_path / "ws" / "CONTROLLER_STATE.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "controller_version": 1,
        "run_id": "run-A",
        "workspace": str(tmp_path / "ws"),
        "overall_status": "RUN_ACTIVE",
        "updated_at": "2026-08-02T00:00:00Z",
        "next_action": {"action": "push", "task_id": None, "reason": "test"},
        "run_identity": {
            "run_id": "run-A",
            "controller_version": 1,
            "repository": "Slideshow11/Automated-Edge-Discovery",
            "target_pr_number": 416,
            "lock_dir": str(tmp_path / "locks"),
        },
    }))
    # LAUNCH_RECEIPT.json is required by the authorize-mutation
    # receipt check.
    receipt_path = state_path.parent / "LAUNCH_RECEIPT.json"
    receipt_path.write_text(json.dumps({
        "run_identity": {
            "run_id": "run-A",
            "controller_version": 1,
            "repository": "Slideshow11/Automated-Edge-Discovery",
            "target_pr_number": 416,
            "state_path": str(state_path),
        },
        "state_path": str(state_path),
    }))
    return state_path


def test_c_target_exclusion_blocks_pr_to_target_upgrade(
    monkeypatch, tmp_path
):
    """C.1: a PR-scoped run attempting to upgrade to
    PR+target must be refused when another run already
    holds the target-scoped lock.

    Setup:
      1. Run A acquires the PR-scoped lock (so
         authorize-mutation's lease check passes).
      2. Run B acquires the target-scoped lock
         (repo + pr + target).
      3. Run A attempts authorize-mutation
         with --mutation-target.
      4. Round-73 fix detects Run B's lock and refuses
         the upgrade.

    Without the fix, the target-scope is NOT checked
    during the PR-to-target upgrade; both controllers
    can authorize mutations of the same head branch.
    The Round-73 fix explicitly acquires the target
    scope and fails closed if another run holds it.
    """
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setenv("AED_LOCK_DIR", str(lock_dir))
    state_path = _make_state(tmp_path)

    # Clean up any stale journal sentinels from prior tests.
    journal = state_path.parent / "MUTATIONS.jsonl"
    if journal.exists():
        journal.unlink()
    journal_sentinel = journal.with_suffix(
        journal.suffix + ".auth-sentinel"
    )
    if journal_sentinel.exists():
        journal_sentinel.unlink()

    # Run A acquires the PR-scoped lock.
    pr_scope = {
        "repository": "Slideshow11/Automated-Edge-Discovery",
        "target_pr_number": 416,
    }
    state_a_path = tmp_path / "ws-A" / "state.json"
    state_a_path.parent.mkdir(parents=True, exist_ok=True)
    state_a_path.write_text(json.dumps({
        "controller_version": 1,
        "run_id": "run-A",
        "workspace": str(tmp_path / "ws-A"),
        "overall_status": "RUN_ACTIVE",
        "updated_at": "2026-08-02T00:00:00Z",
        "next_action": {"action": "noop"},
        "run_identity": {
            "run_id": "run-A",
            "controller_version": 1,
            "repository": "Slideshow11/Automated-Edge-Discovery",
            "target_pr_number": 416,
        },
    }))
    outcome_a = sl.try_acquire(
        scope=pr_scope,
        owner_run_id="run-A",
        owner_host={"hostname": "host-a", "pid": os.getpid()},
        owner_pid=os.getpid(),
        owner_start_evidence={"start_time": time.time()},
        owner_state_path=str(state_a_path),
        base_dir=lock_dir,
    )
    assert outcome_a.ok

    # Run B acquires the target-scoped lock.
    target_scope = {
        "repository": "Slideshow11/Automated-Edge-Discovery",
        "target_pr_number": 416,
        "mutation_target": "main",
    }
    state_b = tmp_path / "ws-B" / "state.json"
    state_b.parent.mkdir(parents=True, exist_ok=True)
    state_b.write_text(json.dumps({
        "controller_version": 1,
        "run_id": "run-B",
        "workspace": str(tmp_path / "ws-B"),
        "overall_status": "RUN_ACTIVE",
        "updated_at": "2026-08-02T00:00:00Z",
        "next_action": {"action": "noop"},
        "run_identity": {
            "run_id": "run-B",
            "controller_version": 1,
            "repository": "Slideshow11/Automated-Edge-Discovery",
            "target_pr_number": 416,
        },
    }))
    outcome_b = sl.try_acquire(
        scope=target_scope,
        owner_run_id="run-B",
        owner_host={"hostname": "host-b", "pid": os.getpid()},
        owner_pid=os.getpid(),
        owner_start_evidence={"start_time": time.time()},
        owner_state_path=str(state_b),
        base_dir=lock_dir,
    )
    assert outcome_b.ok

    # Now we test the upgrade logic via the controller's
    # authorize-mutation. We need to set up the state file
    # to be a PR-scoped run (no mutation_target).
    # The state_path was created as PR-scoped above.

    from scripts.local.autocoder_run_controller import main as controller_main

    rc = controller_main([
        "authorize-mutation",
        "--state", str(state_path),
        "--workspace", str(tmp_path / "ws"),
        "--mutation-type", "push",
        "--expected-main-sha", "e4ef77400000000000000000000000000000abcd",
        "--expected-target-sha", "c973fa6c0718293a4b5c6d70e0f781d67a0c0a1b",
        "--desired-after-sha", "1234567890abcdef1234567890abcdef12345678",
        "--pending-action", "push",
        "--mutation-target", "main",
    ])
    # Without the fix, this would succeed (rc=0). With
    # the fix, it should refuse with rc=11.
    assert rc == 11, (
        f"PR-to-target upgrade must be refused when another "
        f"run holds the target scope; got rc={rc}"
    )


def test_c_target_exclusion_allows_when_no_other_run_holds_target(
    monkeypatch, tmp_path
):
    """C.2 (positive): a PR-scoped run attempting to
    upgrade to PR+target SUCCEEDS when no other run holds
    the target-scoped lock."""
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setenv("AED_LOCK_DIR", str(lock_dir))

    # Run A acquires the PR-scoped lock (not the target).
    pr_scope = {
        "repository": "Slideshow11/Automated-Edge-Discovery",
        "target_pr_number": 416,
    }
    # Create a minimal state file at the owner_state_path
    # so the liveness check passes (the supervisor lock
    # requires a live state file).
    state_a = tmp_path / "ws-A" / "state.json"
    state_a.parent.mkdir(parents=True, exist_ok=True)
    state_a.write_text(json.dumps({
        "controller_version": 1,
        "run_id": "run-A",
        "workspace": str(tmp_path / "ws-A"),
        "overall_status": "RUN_ACTIVE",
        "updated_at": "2026-08-02T00:00:00Z",
        "next_action": {"action": "noop"},
        "run_identity": {
            "run_id": "run-A",
            "controller_version": 1,
            "repository": "Slideshow11/Automated-Edge-Discovery",
            "target_pr_number": 416,
        },
    }))
    outcome_a = sl.try_acquire(
        scope=pr_scope,
        owner_run_id="run-A",
        owner_host={"hostname": "host-a", "pid": os.getpid()},
        owner_pid=os.getpid(),
        owner_start_evidence={"start_time": time.time()},
        owner_state_path=str(state_a),
        base_dir=lock_dir,
    )
    assert outcome_a.ok

    # Set up state file.
    state_path = tmp_path / "ws-A" / "CONTROLLER_STATE.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "controller_version": 1,
        "run_id": "run-A",
        "workspace": str(tmp_path / "ws-A"),
        "overall_status": "RUN_ACTIVE",
        "updated_at": "2026-08-02T00:00:00Z",
        "next_action": {"action": "push", "task_id": None, "reason": "test"},
        "run_identity": {
            "run_id": "run-A",
            "controller_version": 1,
            "repository": "Slideshow11/Automated-Edge-Discovery",
            "target_pr_number": 416,
            "lock_dir": str(lock_dir),
        },
    }))
    receipt_path = state_path.parent / "LAUNCH_RECEIPT.json"
    receipt_path.write_text(json.dumps({
        "run_identity": {
            "run_id": "run-A",
            "controller_version": 1,
            "repository": "Slideshow11/Automated-Edge-Discovery",
            "target_pr_number": 416,
            "state_path": str(state_path),
        },
        "state_path": str(state_path),
    }))

    from scripts.local.autocoder_run_controller import main as controller_main

    rc = controller_main([
        "authorize-mutation",
        "--state", str(state_path),
        "--workspace", str(tmp_path / "ws-A"),
        "--mutation-type", "push",
        "--expected-main-sha", "e4ef77400000000000000000000000000000abcd",
        "--expected-target-sha", "c973fa6c0718293a4b5c6d70e0f781d67a0c0a1b",
        "--desired-after-sha", "1234567890abcdef1234567890abcdef12345678",
        "--pending-action", "push",
        "--mutation-target", "main",
    ])
    # The fix checks the target exclusion but does not
    # block when no other run holds the target.
    assert rc == 0, (
        f"PR-to-target upgrade must succeed when no other "
        f"run holds the target; got rc={rc}"
    )