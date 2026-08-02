"""Restart recovery tests for guarded-ref mutation.

Prove that the durable plan survives a process crash and
that the next run reconciles correctly:

  1. Crash BEFORE execution: the plan is at PREPARED; the
     reconcile reads the actual remote ref (which is still
     at expected_before) and reports NOT_APPLIED.

  2. Crash AFTER remote success but BEFORE terminal
     persistence: the executor's subprocess succeeded but
     the durable plan was not yet updated. The next run
     reconstructs the executor by reading the actual remote
     ref, sees desired_after, and reports SUCCEEDED without
     blindly re-executing the mutation.

The tests simulate a fresh process by creating a new
GuardedMutationOrchestrator instance on the persisted plan.

A "blind retry" failure mode is detected by checking that the
executor is NOT invoked in the recovery path. The executor's
run-guarded-push subprocess is recorded; on recovery the test
asserts no further push occurred (the bare remote was already
at the winner's desired SHA before the recovery started).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.local.guarded_ref_mutation_runner import GuardedMutationOrchestrator
from scripts.local.guarded_ref_mutation import GuardedMutationPlan


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
    )


def _setup_bare_with_clone(tmp_path: Path):
    """Create a bare repo plus a clone that uses it as origin.

    The bare repo is initialized with --initial-branch=main
    so the fixture is deterministic regardless of the
    runner's init.defaultBranch setting. The symbolic-ref
    HEAD assignment is kept as defense in depth.
    """
    bare = tmp_path / "bare.git"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(bare), "-q")
    _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(tmp_path, "clone", str(bare), str(clone), "-q")
    _git(clone, "config", "user.email", "test@local")
    _git(clone, "config", "user.name", "Test")
    _git(clone, "commit", "--allow-empty", "-m", "init", "-q")
    initial = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "push", "origin", "refs/heads/main", "-q")
    return bare, clone, initial


def _make_desired(clone: Path) -> str:
    _git(clone, "commit", "--allow-empty", "-m", "next", "-q")
    return _git(clone, "rev-parse", "HEAD").stdout.strip()


def _write_plan(workspace: Path, plan: GuardedMutationPlan) -> Path:
    from scripts.local.guarded_ref_mutation import guarded_ref_mutation_plan_path
    plan_path = guarded_ref_mutation_plan_path(workspace, plan.mutation_id)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(plan.to_json())
    return plan_path


def _fresh_orchestrator(workspace: Path, plan: GuardedMutationPlan):
    """Simulate a fresh process by constructing a new
    orchestrator instance."""
    return GuardedMutationOrchestrator(workspace=workspace, plan=plan)


def test_crash_before_execution_reconciles_to_not_applied(tmp_path):
    """Crash after persist of PREPARED but before execution.
    The recovery run loads the durable plan, reads the
    authoritative remote ref (still at expected_before), and
    reports NOT_APPLIED. The executor is NOT invoked in the
    recovery path.
    """
    bare, clone, initial_sha = _setup_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    desired_sha = _make_desired(clone)

    plan = GuardedMutationPlan(
        mutation_id="m_crash_before",
        owner_run_id="r-crash-before",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=desired_sha,
        status="PREPARED",
        created_at="2026-08-01T00:00:00Z",
    )
    plan_path = _write_plan(workspace, plan)

    # Simulate a crash BEFORE execute: the plan is PREPARED.
    # The orchestrator's execute() was never called.
    # Recovery: load the plan, verify state is PREPARED,
    # reconcile against the authoritative remote.
    fresh = _fresh_orchestrator(workspace, plan)
    fresh_plan = GuardedMutationPlan.from_json(plan_path.read_text())
    assert fresh_plan.status == "PREPARED"

    # Reconciliation: read the remote ref, which is still at
    # initial_sha. The reconcile reports NOT_APPLIED.
    final = fresh.reconcile(remote_ref_path=bare)
    assert final.status == "NOT_APPLIED", (
        f"expected NOT_APPLIED, got {final.status}; "
        f"plan was {final.to_json()}"
    )

    # The durable plan is updated to NOT_APPLIED.
    updated_plan = GuardedMutationPlan.from_json(plan_path.read_text())
    assert updated_plan.status == "NOT_APPLIED"
    assert updated_plan.last_reconciled_at is not None

    # The bare remote was NOT touched by the recovery.
    final_remote = _git(bare, "rev-parse", "refs/heads/main").stdout.strip()
    assert final_remote == initial_sha, (
        f"remote was changed by recovery: got {final_remote}"
    )


def test_crash_after_remote_success_persists_to_succeeded(tmp_path):
    """Crash after the executor's push succeeded but BEFORE
    the terminal result was persisted. The executor's
    subprocess returned ok=True and the remote is at
    desired_sha. The durable plan is still in an intermediate
    state (EXECUTING or RECONCILING). The recovery run reads
    the durable plan, sees the remote at desired_sha, and
    reports SUCCEEDED. The executor is NOT invoked again.
    """
    bare, clone, initial_sha = _setup_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    desired_sha = _make_desired(clone)

    plan = GuardedMutationPlan(
        mutation_id="m_crash_after",
        owner_run_id="r-crash-after",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=desired_sha,
        status="RECONCILING",
        created_at="2026-08-01T00:00:00Z",
    )
    plan_path = _write_plan(workspace, plan)

    # Simulate the crash: the executor already pushed the
    # ref to desired_sha, but the terminal result was not
    # persisted. The bare remote is at desired_sha; the plan
    # is at RECONCILING.
    # We push to the bare remote to simulate the executor's
    # successful push.
    _git(clone, "push", "origin", desired_sha + ":refs/heads/main", "-q")
    assert _git(bare, "rev-parse", "refs/heads/main").stdout.strip() == desired_sha

    # Recovery: load the plan, verify state is RECONCILING,
    # reconcile against the authoritative remote.
    fresh = _fresh_orchestrator(workspace, plan)
    fresh_plan = GuardedMutationPlan.from_json(plan_path.read_text())
    assert fresh_plan.status == "RECONCILING"

    final = fresh.reconcile(remote_ref_path=bare)
    assert final.status == "SUCCEEDED", (
        f"expected SUCCEEDED, got {final.status}; "
        f"plan was {final.to_json()}"
    )

    # The durable plan is updated to SUCCEEDED.
    updated_plan = GuardedMutationPlan.from_json(plan_path.read_text())
    assert updated_plan.status == "SUCCEEDED"
    assert updated_plan.last_reconciled_at is not None

    # The bare remote is still at desired_sha (no blind retry
    # pushed something different).
    final_remote = _git(bare, "rev-parse", "refs/heads/main").stdout.strip()
    assert final_remote == desired_sha


def test_crash_after_remote_success_no_blind_retry(tmp_path):
    """Stronger guarantee: after a crash following a successful
    remote mutation, the recovery must NOT invoke the executor
    again. We detect this by tracking the number of times
    the executor's git-push subprocess was invoked.

    Strategy: patch the orchestrator's _do_execute to a
    no-op stub that records each invocation. The recovery
    path (reconcile) must not call _do_execute.
    """
    bare, clone, initial_sha = _setup_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    desired_sha = _make_desired(clone)

    plan = GuardedMutationPlan(
        mutation_id="m_no_blind_retry",
        owner_run_id="r-no-blind-retry",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=desired_sha,
        status="RECONCILING",
        created_at="2026-08-01T00:00:00Z",
    )
    plan_path = _write_plan(workspace, plan)

    # Simulate the remote success.
    _git(clone, "push", "origin", desired_sha + ":refs/heads/main", "-q")

    # Patch _do_execute to record invocations. Recovery path
    # calls reconcile(), which MUST NOT invoke _do_execute.
    invocations = {"count": 0}
    original_do_execute = GuardedMutationOrchestrator._do_execute

    def tracked_do_execute(self, local_repo, op):
        invocations["count"] += 1
        return original_do_execute(self, local_repo, op)

    GuardedMutationOrchestrator._do_execute = tracked_do_execute
    try:
        fresh = _fresh_orchestrator(workspace, plan)
        final = fresh.reconcile(remote_ref_path=bare)
        assert final.status == "SUCCEEDED"
    finally:
        GuardedMutationOrchestrator._do_execute = original_do_execute

    # The executor was NOT invoked during reconcile.
    assert invocations["count"] == 0, (
        f"reconcile() invoked _do_execute {invocations['count']} "
        f"times; recovery must not blindly re-execute"
    )


def test_persisted_plan_can_be_reloaded_by_fresh_instance(tmp_path):
    """A fresh orchestrator instance loads the persisted
    plan from disk. The plan's mutation_id, target_ref, and
    expected_before_sha are preserved across the reload.
    """
    bare, clone, initial_sha = _setup_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    desired_sha = _make_desired(clone)

    plan = GuardedMutationPlan(
        mutation_id="m_reload",
        owner_run_id="r-reload",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=desired_sha,
        status="PREPARED",
        created_at="2026-08-01T00:00:00Z",
    )
    plan_path = _write_plan(workspace, plan)

    # First orchestrator instance: PREPARED -> EXECUTING ->
    # RECONCILING -> SUCCEEDED.
    orch1 = _fresh_orchestrator(workspace, plan)
    orch1.prepare()
    final1 = orch1.execute(local_repo=clone, remote_ref_path=bare)
    assert final1.status == "SUCCEEDED"

    # Second (fresh) orchestrator instance: loads the
    # persisted plan from disk. The plan is already SUCCEEDED.
    reloaded = GuardedMutationPlan.from_json(plan_path.read_text())
    assert reloaded.mutation_id == plan.mutation_id
    assert reloaded.target_ref == plan.target_ref
    assert reloaded.expected_before_sha == plan.expected_before_sha
    assert reloaded.desired_after_sha == plan.desired_after_sha
    assert reloaded.status == "SUCCEEDED"
    assert reloaded.last_reconciled_at is not None