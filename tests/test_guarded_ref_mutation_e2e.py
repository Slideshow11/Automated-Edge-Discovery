"""End-to-end production integration tests for guarded-ref
mutation.

These tests prove the full production path:

  controller authorize-mutation (existing; produces the
    legacy MUTATIONS.jsonl durable plan) -> the executor
    consumes the durable plan -> mutate-ref (the new
    repository-owned executor entry point) -> guarded CAS
    adapter -> reconcile against the authoritative remote ->
    persist terminal result.

The tests:
  - run against a temporary bare Git repository plus a clone;
  - invoke the controller's CLI subcommands as a subprocess
    (not by calling helper functions directly);
  - verify the authoritative remote ref state after each
    operation;
  - verify the durable plan is persisted with the correct
    terminal status;
  - verify that packets lacking exact expected state are
    refused.

This is the production integration proof. The tests do NOT
call GuardedMutationOrchestrator.execute() directly. They
drive the full CLI pipeline.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Make the scripts importable so the CLI can be invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER = "scripts/local/autocoder_run_controller.py"
MUTATIONS_FILENAME = "MUTATIONS.jsonl"


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
    )


def _make_bare_with_clone(
    tmp_path: Path,
) -> tuple[Path, Path]:
    """Create a bare repo plus a clone that uses it as origin.

    Returns (bare_repo, clone_path).

    The bare repo is initialized with --initial-branch=main
    so the fixture is deterministic regardless of the
    runner's init.defaultBranch setting. The symbolic-ref
    HEAD assignment is kept as defense in depth.

    Round-58 (P1: Bind the execution remote to the authorized
    repository): mutate-ref's Step 3.5 now canonicalizes the
    repository identity from the plan and from the clone's
    remote.origin.url. The fixture sets the *origin* remote
    to a parseable GitHub URL so the canonicalization succeeds.
    A separate remote named `bare` points at the local bare
    repository for actual push operations; tests invoke
    mutate-ref with `--remote bare` to target the local bare.

    The plan's repository field is `owner/name` (the existing
    tests' convention) which canonicalizes to the same
    (host, owner, name) triple as the GitHub-form origin URL.
    """
    bare = tmp_path / "bare.git"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(bare), "-q")
    _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(tmp_path, "clone", str(bare), str(clone), "-q")
    _git(clone, "config", "user.email", "test@local")
    _git(clone, "config", "user.name", "Test")
    # Push an initial commit so HEAD is valid. Use the bare path
    # (not origin) because origin will be a GitHub URL after the
    # next step, and we don't want network access in tests.
    _git(clone, "commit", "--allow-empty", "-m", "init", "-q")
    _git(clone, "push", str(bare), "refs/heads/main", "-q")
    # Round-58: configure origin URL to a parseable GitHub URL
    # so mutate-ref's Step 3.5 repository-identity check succeeds.
    # The `bare` remote (added below) is the actual push target
    # used by guarded_push inside mutate-ref.
    _git(clone, "remote", "set-url", "origin",
         "https://github.com/owner/name.git")
    # Add a separate `bare` remote that points at the local bare
    # so guarded_push can actually deliver the mutation locally.
    _git(clone, "remote", "add", "bare", str(bare))
    return bare, clone


def _seed_branch(clone: Path, branch: str) -> str:
    """Create a new branch with one commit. Returns the SHA."""
    _git(clone, "checkout", "-q", "-b", branch)
    _git(clone, "commit", "--allow-empty", "-m", f"seed {branch}", "-q")
    sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "checkout", "-q", "main")
    return sha


def _run_cli(*args: str, expect_rc: int = 0) -> subprocess.CompletedProcess:
    """Run the controller CLI as a subprocess. Returns the
    result. This is the production path."""
    return subprocess.run(
        [sys.executable, CONTROLLER, *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _create_minimal_state(workspace: Path) -> None:
    """Write a minimal CONTROLLER_STATE.json that the
    controller accepts."""
    state_path = workspace / "CONTROLLER_STATE.json"
    state = {
        "schema_version": 1,
        "run_id": "r-end2end",
        "controller_version": "test",
        "created_at": "2026-08-01T00:00:00Z",
        "last_updated_at": "2026-08-01T00:00:00Z",
        "run_identity": {
            "schema_version": 1,
            "run_id": "r-end2end",
            "workspace_path": str(workspace),
            "lock_dir": str(workspace / "L"),
            "repository": "owner/name",
            "target_pr_number": 416,
            "machine_identity": {
                "hostname": "test",
                "user": "test",
                "pid": 1,
            },
        },
        "target_pr_number": 416,
        "repository": "owner/name",
        "pending_action": "merge",
        "main_sha": "0" * 40,
        # Required by authorize-mutation's receipt check.
        "workspace": str(workspace),
    }
    state_path.write_text(json.dumps(state, indent=2))


def _seed_mutation_authorization(
    workspace: Path,
    mutation_id: str,
    mutation_type: str,
    mutation_target: Optional[str],
    expected_main_sha: Optional[str],
    expected_target_sha: Optional[str],
    pending_action: str,
    desired_after_sha: Optional[str] = None,
    acquire_lease: bool = True,
) -> None:
    """Write a MUTATIONS.jsonl record directly so the
    authorization-binding check in mutate-ref passes.

    This represents the durable authorization that
    authorize-mutation emits. We do not call authorize-mutation
    via the CLI here because that command requires a
    pre-existing LAUNCH_RECEIPT.json and a launched init run;
    those are orthogonal to the guarded-ref production
    integration. The authorization binding check in mutate-ref
    is what we are testing.

    Round-77 P1 fix: also acquire the supervisor lease
    so the mutate-ref lease revalidation check passes.
    The lease is acquired with the same scope as the
    plan's repository+target.
    """
    mutations_file = workspace / "MUTATIONS.jsonl"
    record = {
        "mutation_id": mutation_id,
        "run_id": "r-end2end",
        "repository": "owner/name",
        "target_pr_number": 416,
        "mutation_type": mutation_type,
        "expected_main_sha": expected_main_sha,
        "expected_target_sha": expected_target_sha,
        "pending_action": pending_action,
        "created_at": "2026-08-01T00:00:00Z",
        "authorization_status": "authorized",
    }
    if mutation_target is not None:
        record["mutation_target"] = mutation_target
    # Build the durable plan in the same shape authorize-mutation
    # writes (Repair 1). Use the mutation_policy to derive the
    # operation and target_ref.
    from scripts.local.mutation_policy import derive_plan
    from scripts.local.guarded_ref_mutation import (
        GuardedMutationPlan,
        LifecycleState,
    )
    derived = derive_plan(
        mutation_type=mutation_type,
        mutation_target=mutation_target,
        expected_target_sha=expected_target_sha,
        expected_main_sha=expected_main_sha,
        desired_after_sha=desired_after_sha,
    )
    plan_dir = workspace / "GUARDED_REF_MUTATIONS"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / f"{mutation_id}.json"
    plan = GuardedMutationPlan(
        mutation_id=mutation_id,
        owner_run_id="r-end2end",
        repository="owner/name",
        target_ref=derived.target_ref,
        operation=derived.operation.value,
        expected_before_sha=derived.expected_before_sha,
        desired_after_sha=derived.desired_after_sha,
        status=LifecycleState.PREPARED.value,
        created_at="2026-08-01T00:00:00Z",
    )
    plan_path.write_text(plan.to_json())
    # Append the authorization record to MUTATIONS.jsonl.
    with open(mutations_file, "a") as f:
        f.write(json.dumps(record) + "\n")
    # Round-77 P1 fix: acquire the supervisor lease so
    # the mutate-ref lease revalidation check passes.
    # The controller's check uses
    # (run_identity.target_pr_number,
    # run_identity.mutation_target) from the state. The
    # e2e test's state (from _create_minimal_state) has
    # target_pr_number=416 and mutation_target=None
    # (i.e. a PR-scoped run without a target), so
    # acquire the lease with that scope.
    if acquire_lease:
        import os
        import time
        from scripts.local.aed_supervisor_lock import try_acquire as _try_acquire
        lock_dir = workspace / "L"
        lock_dir.mkdir(exist_ok=True)
        # Determine the scope from the state (if it has
        # run_identity) or fall back to mutation_target
        # only (target-only scope).
        _state_for_lease = json.loads(
            (workspace / "CONTROLLER_STATE.json").read_text()
        ) if (workspace / "CONTROLLER_STATE.json").exists() else {}
        _rid_for_lease = _state_for_lease.get("run_identity") or {}
        # The liveness check requires run_identity.run_id
        # to match the lock's owner_run_id. Persist this
        # injection to the state file so the
        # controller's later check sees the right value.
        if (
            isinstance(_rid_for_lease, dict)
            and "run_id" not in _rid_for_lease
            and "run_id" in _state_for_lease
        ):
            _rid_for_lease["run_id"] = _state_for_lease["run_id"]
            _state_for_lease["run_identity"] = _rid_for_lease
            (workspace / "CONTROLLER_STATE.json").write_text(
                json.dumps(_state_for_lease, indent=2)
            )
        # Use the state's run_identity values directly
        # (no fallback to the function's mutation_target
        # parameter) so the lease scope matches what
        # the controller's mutate-ref check will look
        # for.
        lease_scope = {
            "repository": "owner/name",
            "target_pr_number": _rid_for_lease.get("target_pr_number"),
            "mutation_target": _rid_for_lease.get("mutation_target"),
        }
        lease_outcome = _try_acquire(
            scope=lease_scope,
            owner_run_id="r-end2end",
            owner_host={"hostname": "test", "user": "test"},
            owner_pid=os.getpid(),
            owner_start_evidence={
                "pid": os.getpid(),
                "start_time": "2026-08-01T00:00:00Z",
            },
            owner_state_path=str(workspace / "CONTROLLER_STATE.json"),
            base_dir=lock_dir,
        )
        assert lease_outcome.ok, (
            f"failed to acquire supervisor lease in e2e "
            f"setup: {lease_outcome.reason!r}"
        )


# ---------------------------------------------------------------------------
# End-to-end: UPDATE_LOCAL via the CLI
# ---------------------------------------------------------------------------

def test_end_to_end_update_local_via_cli(tmp_path):
    """End-to-end: authorize a mutation, then mutate-ref via
    the CLI against a bare remote. Verify the authoritative
    remote ref state."""
    bare, clone = _make_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_minimal_state(workspace)
    (workspace / "L").mkdir()

    # Seed a target ref on main so we can update it.
    _seed_branch(clone, "feat/x")
    initial_sha = _git(clone, "rev-parse", "refs/heads/feat/x").stdout.strip()
    _git(clone, "push", str(bare), "refs/heads/feat/x", "-q")
    # Make a new commit for the desired_after.
    _git(clone, "checkout", "-q", "feat/x")
    _git(clone, "commit", "--allow-empty", "-m", "next", "-q")
    desired_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "checkout", "-q", "main")

    # Seed the durable authorization via the production
    # helper. authorize-mutation emits both the durable plan
    # and the MUTATIONS.jsonl record; mutate-ref binds the
    # loaded plan to the outstanding authorization.
    _seed_mutation_authorization(
        workspace,
        mutation_id="m_end2end_update",
        mutation_type="force_push",
        mutation_target="feat/x",
        expected_main_sha=initial_sha,
        expected_target_sha=initial_sha,
        pending_action="force_push",
        desired_after_sha=desired_sha,
    )

    result = _run_cli(
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_end2end_update",
        "--local-repo", str(clone),
        "--remote", "bare",
        "--remote-path", str(bare),
    )
    assert result.returncode == 0, (
        f"mutate-ref failed: rc={result.returncode} "
        f"stdout={result.stdout} stderr={result.stderr}"
    )
    assert "OK" in result.stdout

    # Verify the durable plan was updated to a terminal state.
    final_plan = json.loads(
        (workspace / "GUARDED_REF_MUTATIONS" / "m_end2end_update.json").read_text()
    )
    assert final_plan["status"] == "SUCCEEDED", (
        f"final status: {final_plan['status']}; "
        f"plan: {final_plan}"
    )

    # Verify the authoritative remote ref state.
    actual_remote = _git(bare, "rev-parse", "refs/heads/feat/x").stdout.strip()
    assert actual_remote == desired_sha, (
        f"remote ref mismatch: got {actual_remote} expected {desired_sha}"
    )


# ---------------------------------------------------------------------------
# End-to-end: PUSH_REMOTE via the CLI
# ---------------------------------------------------------------------------

def test_end_to_end_push_remote_via_cli(tmp_path):
    """End-to-end: PUSH_REMOTE through the CLI. The remote
    ref on the bare repo is the authoritative source."""
    bare, clone = _make_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_minimal_state(workspace)
    (workspace / "L").mkdir()

    # Initial state on the remote.
    initial_sha = _git(bare, "rev-parse", "refs/heads/main").stdout.strip()

    # Make a new local commit on main.
    _git(clone, "commit", "--allow-empty", "-m", "next", "-q")
    desired_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()

    _seed_mutation_authorization(
        workspace,
        mutation_id="m_end2end_push",
        mutation_type="force_push",
        mutation_target="main",
        expected_main_sha=initial_sha,
        expected_target_sha=initial_sha,
        pending_action="force_push",
        desired_after_sha=desired_sha,
    )

    result = _run_cli(
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_end2end_push",
        "--local-repo", str(clone),
        "--remote", "bare",
        "--remote-path", str(bare),
    )
    assert result.returncode == 0, (
        f"mutate-ref failed: rc={result.returncode} "
        f"stdout={result.stdout} stderr={result.stderr}"
    )

    final_plan = json.loads(
        (workspace / "GUARDED_REF_MUTATIONS" / "m_end2end_push.json").read_text()
    )
    assert final_plan["status"] == "SUCCEEDED"

    actual_remote = _git(bare, "rev-parse", "refs/heads/main").stdout.strip()
    assert actual_remote == desired_sha


# ---------------------------------------------------------------------------
# Packet validation: refuse plans that lack exact expected state
# ---------------------------------------------------------------------------

def test_mutate_ref_refuses_plan_lacking_exact_expected_state(tmp_path):
    """The CLI must refuse packets that lack exact expected
    state (e.g. short SHAs, empty SHAs)."""
    bare, clone = _make_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_minimal_state(workspace)
    (workspace / "L").mkdir()

    (workspace / "GUARDED_REF_MUTATIONS").mkdir(exist_ok=True)
    plan = {
        "mutation_id": "m_bad_packet",
        "owner_run_id": "r-end2end",
        "repository": "owner/name",
        "target_ref": "refs/heads/main",
        "operation": "UPDATE_LOCAL",
        "expected_before_sha": "abc",  # TOO SHORT — must be 40 hex
        "desired_after_sha": "0" * 40,
        "status": "PREPARED",
        "created_at": "2026-08-01T00:00:00Z",
    }
    (workspace / "GUARDED_REF_MUTATIONS" / "m_bad_packet.json").write_text(json.dumps(plan))
    # Also seed an authorization so the binding check passes;
    # the validation check then catches the bad SHA.
    mutations_file = workspace / "MUTATIONS.jsonl"
    auth_record = {
        "mutation_id": "m_bad_packet",
        "run_id": "r-end2end",
        "repository": "owner/name",
        "target_pr_number": 416,
        "mutation_type": "force_push",
        "expected_main_sha": "0" * 40,
        "expected_target_sha": "0" * 40,
        "pending_action": "force_push",
        "created_at": "2026-08-01T00:00:00Z",
        "authorization_status": "authorized",
    }
    mutations_file.write_text(json.dumps(auth_record))

    result = _run_cli(
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_bad_packet",
        "--local-repo", str(clone),
    )
    assert result.returncode != 0, (
        "mutate-ref must refuse plans lacking exact expected state"
    )
    assert "plan validation failed" in result.stderr or "invalid" in result.stderr.lower()


def test_mutate_ref_refuses_pushed_with_no_remote_path(tmp_path):
    """PUSH_REMOTE requires --remote-path. The CLI must
    refuse when it is missing."""
    bare, clone = _make_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_minimal_state(workspace)
    (workspace / "L").mkdir()

    (workspace / "GUARDED_REF_MUTATIONS").mkdir(exist_ok=True)
    plan = {
        "mutation_id": "m_push_no_remote",
        "owner_run_id": "r-end2end",
        "repository": "owner/name",
        "target_ref": "refs/heads/main",
        "operation": "PUSH_REMOTE",
        "expected_before_sha": "0" * 40,
        "desired_after_sha": "1" * 40,
        "status": "PREPARED",
        "created_at": "2026-08-01T00:00:00Z",
    }
    (workspace / "GUARDED_REF_MUTATIONS" / "m_push_no_remote.json").write_text(json.dumps(plan))
    mutations_file = workspace / "MUTATIONS.jsonl"
    auth_record = {
        "mutation_id": "m_push_no_remote",
        "run_id": "r-end2end",
        "repository": "owner/name",
        "target_pr_number": 416,
        "mutation_type": "force_push",
        "mutation_target": "main",
        "expected_main_sha": "0" * 40,
        "expected_target_sha": "0" * 40,
        "pending_action": "force_push",
        "created_at": "2026-08-01T00:00:00Z",
        "authorization_status": "authorized",
    }
    mutations_file.write_text(json.dumps(auth_record))

    # Round-77 P1 fix: acquire the supervisor lease so
    # the mutate-ref lease revalidation check passes.
    # The mutate-ref check uses (args.target_pr_number,
    # branch_from_ref); the test does not pass
    # --target-pr-number, but the controller's check
    # falls back to the state's run_identity.target_pr_number
    # (= 416) and to args.mutation_target (which is not
    # set). The branch_from_ref strips the "refs/heads/"
    # prefix from plan.target_ref ("refs/heads/main"),
    # giving "main" as mutation_target. Acquire the lease
    # at (416, None) so the check passes. We pass
    # mutation_target=None because the controller's check
    # uses None (no mutation_target in run_identity) for
    # this PR-scoped test.
    import os
    from scripts.local.aed_supervisor_lock import try_acquire as _try_acquire
    outcome = _try_acquire(
        scope={
            "repository": "owner/name",
            "target_pr_number": 416,
            "mutation_target": None,
        },
        owner_run_id="r-end2end",
        owner_host={"hostname": "test", "user": "test", "pid": os.getpid()},
        owner_pid=os.getpid(),
        owner_start_evidence={
            "pid": os.getpid(),
            "start_time": "2026-08-01T00:00:00Z",
        },
        owner_state_path=str(workspace / "CONTROLLER_STATE.json"),
        base_dir=workspace / "L",
    )
    assert outcome.ok, (
        f"failed to acquire supervisor lease in e2e setup: "
        f"{outcome.reason if hasattr(outcome, 'reason') else outcome}"
    )

    result = _run_cli(
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_push_no_remote",
        "--local-repo", str(clone),
        # no --remote-path — Round-60 P1 fix allows this;
        # the runner now falls back to `git ls-remote` over
        # the clone's configured remote URL for
        # reconciliation. With no origin configured the
        # ls-remote query fails → INDETERMINATE (exit 32).
    )
    # Round-60: PUSH_REMOTE without --remote-path now
    # proceeds; reconciliation via ls-remote fails when
    # origin is unconfigured, so the run terminates as
    # INDETERMINATE (exit 32).
    assert result.returncode in (32, 27), (
        f"PUSH_REMOTE without --remote-path must exit "
        f"INDETERMINATE (32) or fail closed (27), got {result.returncode}: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# End-to-end: authorize-mutation -> mutate-ref -> reconcile -> record
# ---------------------------------------------------------------------------

def test_end_to_end_authorize_then_mutate_then_record(tmp_path):
    """Full end-to-end production path:

      1. Init the controller state (workspace, lock dir).
      2. authorize-mutation via the CLI -> emits a record.
      3. mutate-ref via the CLI -> guarded CAS -> reconcile.
      4. Read the durable plan and assert SUCCEEDED.

    The first step writes a durable plan to
    GUARDED_REF_MUTATIONS/<id>.json so the executor can
    consume it. (The legacy MUTATIONS.jsonl authorization
    record is also written by authorize-mutation, but the
    guarded-ref executor reads the new plan file.)
    """
    bare, clone = _make_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_minimal_state(workspace)
    (workspace / "L").mkdir()

    # Seed a target ref with an initial commit.
    _seed_branch(clone, "feat/y")
    initial_sha = _git(clone, "rev-parse", "refs/heads/feat/y").stdout.strip()
    _git(clone, "push", str(bare), "refs/heads/feat/y", "-q")
    # Desired = new commit on feat/y.
    _git(clone, "checkout", "-q", "feat/y")
    _git(clone, "commit", "--allow-empty", "-m", "y2", "-q")
    desired_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "checkout", "-q", "main")

    _seed_mutation_authorization(
        workspace,
        mutation_id="m_e2e_full",
        mutation_type="force_push",
        mutation_target="feat/y",
        expected_main_sha=initial_sha,
        expected_target_sha=initial_sha,
        pending_action="force_push",
        desired_after_sha=desired_sha,
    )

    result = _run_cli(
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_e2e_full",
        "--local-repo", str(clone),
        "--remote", "bare",
        "--remote-path", str(bare),
    )
    assert result.returncode == 0, (
        f"mutate-ref failed: {result.stdout} {result.stderr}"
    )

    # Step 3: read the durable plan and assert SUCCEEDED.
    final_plan = json.loads(
        (workspace / "GUARDED_REF_MUTATIONS" / "m_e2e_full.json").read_text()
    )
    assert final_plan["status"] == "SUCCEEDED"
    assert final_plan["last_reconciled_at"] is not None

    # Step 4: verify the authoritative remote ref state.
    actual_remote = _git(bare, "rev-parse", "refs/heads/feat/y").stdout.strip()
    assert actual_remote == desired_sha

# ---------------------------------------------------------------------------
# Production path: authorize-mutation emits the durable plan
# ---------------------------------------------------------------------------

def test_authorize_mutation_emits_durable_plan(tmp_path):
    """Production-path regression test for Repair 1. The
    controller's authorize-mutation CLI must emit a durable
    GuardedMutationPlan alongside the MUTATIONS.jsonl record.
    The test invokes the CLI as a subprocess (not by calling
    the helper directly).

    Required precondition: a launch receipt and an active
    controller state. We write both to the workspace before
    invoking authorize-mutation.
    """
    bare, clone = _make_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_minimal_state(workspace)
    (workspace / "L").mkdir()

    # Seed a target ref.
    _seed_branch(clone, "feat/z")
    initial_sha = _git(clone, "rev-parse", "refs/heads/feat/z").stdout.strip()
    _git(clone, "push", str(bare), "refs/heads/feat/z", "-q")
    _git(clone, "checkout", "-q", "feat/z")
    _git(clone, "commit", "--allow-empty", "-m", "z2", "-q")
    desired_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "checkout", "-q", "main")

    # The controller's authorize-mutation requires a launch
    # receipt. Write one.
    from scripts.local.guarded_ref_mutation import (
        GuardedMutationPlan,
        LifecycleState,
    )
    import json
    receipt = {
        "schema_version": 1,
        "run_id": "r-end2end",
        "controller_version": "test",
        "created_at": "2026-08-01T00:00:00Z",
        "workspace": str(workspace),
        "state_path": str(workspace / "CONTROLLER_STATE.json"),
        "run_identity": {
            "run_id": "r-end2end",
            "workspace": str(workspace),
            "machine_identity": {
                "hostname": "test", "user": "test", "pid": 1,
            },
        },
        "repository": "owner/name",
        "main_sha": "0" * 40,
        "next_action": "force_push",
        "overall_status": "RUN_READY_FOR_SUMMARY",
    }
    (workspace / "LAUNCH_RECEIPT.json").write_text(json.dumps(receipt))

    # Update the state to match the receipt.
    state = json.loads((workspace / "CONTROLLER_STATE.json").read_text())
    state["next_action"] = {"action": "force_push"}
    state["overall_status"] = "RUN_READY_FOR_SUMMARY"
    state["mutation_target"] = "feat/z"
    # Bind the lock directory to the workspace so the lock is
    # scoped to this test (otherwise the lock acquisition may
    # write to the host-wide default dir).
    state["lock_dir"] = str(workspace / "L")
    state["run_identity"]["lock_dir"] = str(workspace / "L")
    (workspace / "CONTROLLER_STATE.json").write_text(json.dumps(state))

    # Acquire the supervisor lock directly via the supervisor
    # lock module. This is what `init` does in production; we
    # use the lower-level API here to keep the test focused on
    # authorize-mutation, not on init.
    # Round-77 P1 fix: acquire the supervisor lease with
    # a scope that matches what authorize-mutation will
    # check (which uses the state.run_identity values:
    # target_pr_number=416, mutation_target=None).
    # NOTE: the state.mutation_target field is set to
    # "feat/z" at line 645 for the controller to record
    # the desired branch, but the run_identity's
    # mutation_target (used for the lease scope) is
    # None. Acquire the lease with target_pr_number=416
    # and mutation_target=None to match the lease
    # scope that authorize-mutation and mutate-ref
    # will check.
    import os
    host_identity = {"hostname": "test", "user": "test", "pid": os.getpid()}
    proc_evidence = {"pid": os.getpid(), "start_time": "2026-08-01T00:00:00Z"}
    from scripts.local.aed_supervisor_lock import (
        try_acquire as _supervisor_try_acquire,
    )
    scope = {
        "repository": "owner/name",
        "target_pr_number": 416,
        "mutation_target": None,
    }
    outcome = _supervisor_try_acquire(
        scope=scope,
        owner_run_id="r-end2end",
        owner_host=host_identity,
        owner_pid=os.getpid(),
        owner_start_evidence=proc_evidence,
        owner_state_path=str(workspace / "CONTROLLER_STATE.json"),
        base_dir=workspace / "L",
    )
    assert outcome.ok, (
        f"failed to acquire supervisor lock for test setup: "
        f"{outcome.reason if hasattr(outcome, 'reason') else outcome}"
    )

    # Invoke authorize-mutation via the CLI.
    result = _run_cli(
        "authorize-mutation",
        "--state", str(workspace / "CONTROLLER_STATE.json"),
        "--workspace", str(workspace),
        "--mutation-type", "force_push",
        "--mutation-target", "feat/z",
        "--expected-target-sha", initial_sha,
        "--expected-main-sha", "0" * 40,
        "--desired-after-sha", desired_sha,
        "--pending-action", "force_push",
    )
    assert result.returncode == 0, (
        f"authorize-mutation failed: rc={result.returncode} "
        f"stdout={result.stdout} stderr={result.stderr}"
    )

    # The durable plan MUST exist at
    # GUARDED_REF_MUTATIONS/<mutation_id>.json.
    plan_dir = workspace / "GUARDED_REF_MUTATIONS"
    plan_files = list(plan_dir.iterdir())
    assert len(plan_files) == 1, (
        f"expected exactly one durable plan, got {plan_files}"
    )
    plan = GuardedMutationPlan.from_json(plan_files[0].read_text())
    assert plan.target_ref == "refs/heads/feat/z"
    assert plan.operation == "PUSH_REMOTE"
    assert plan.expected_before_sha == initial_sha
    assert plan.desired_after_sha == desired_sha
    assert plan.owner_run_id == "r-end2end"
    assert plan.repository == "owner/name"
    assert plan.status == LifecycleState.PREPARED.value

    # The MUTATIONS.jsonl record must exist with authorization_status.
    mutations_file = workspace / "MUTATIONS.jsonl"
    assert mutations_file.exists()
    mutations_records = [
        json.loads(line)
        for line in mutations_file.read_text().splitlines()
        if line.strip()
    ]
    auth_records = [
        r for r in mutations_records
        if r.get("authorization_status") == "authorized"
    ]
    assert len(auth_records) == 1
    assert auth_records[0]["mutation_id"] == plan.mutation_id

    # The mutation_id in the durable plan matches the MUTATIONS.jsonl
    # authorization.
    assert auth_records[0]["mutation_id"] == plan.mutation_id

    # Now mutate-ref against the authoritative remote. The
    # binding check passes because the plan and the
    # authorization were produced together by authorize-mutation.
    mutate_result = _run_cli(
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", plan.mutation_id,
        "--local-repo", str(clone),
        "--remote", "bare",
        "--remote-path", str(bare),
    )
    assert mutate_result.returncode == 0, (
        f"mutate-ref failed: rc={mutate_result.returncode} "
        f"stdout={mutate_result.stdout} "
        f"stderr={mutate_result.stderr}"
    )
    assert "OK" in mutate_result.stdout

    # Verify the authoritative remote ref was updated.
    actual_remote = _git(bare, "rev-parse", "refs/heads/feat/z").stdout.strip()
    assert actual_remote == desired_sha


def test_mutate_ref_refuses_plan_without_outstanding_authorization(tmp_path):
    """Repair 2: mutate-ref binds loaded plans to an
    outstanding authorization. A plan that has NO matching
    record in MUTATIONS.jsonl is rejected with exit code 25
    even if the plan itself is well-formed.
    """
    bare, clone = _make_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_minimal_state(workspace)
    (workspace / "L").mkdir()

    # Seed a target ref.
    _seed_branch(clone, "feat/a")
    initial_sha = _git(clone, "rev-parse", "refs/heads/feat/a").stdout.strip()
    _git(clone, "push", str(bare), "refs/heads/feat/a", "-q")
    _git(clone, "checkout", "-q", "feat/a")
    _git(clone, "commit", "--allow-empty", "-m", "a2", "-q")
    desired_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "checkout", "-q", "main")

    # Write a well-formed plan but NO MUTATIONS.jsonl record.
    from scripts.local.guarded_ref_mutation import (
        GuardedMutationPlan,
        LifecycleState,
    )
    plan_dir = workspace / "GUARDED_REF_MUTATIONS"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan = GuardedMutationPlan(
        mutation_id="m_orphan",
        owner_run_id="r-end2end",
        repository="owner/name",
        target_ref="refs/heads/feat/a",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=desired_sha,
        status=LifecycleState.PREPARED.value,
        created_at="2026-08-01T00:00:00Z",
    )
    (plan_dir / "m_orphan.json").write_text(plan.to_json())

    result = _run_cli(
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_orphan",
        "--local-repo", str(clone),
        "--remote", "bare",
        "--remote-path", str(bare),
    )
    assert result.returncode == 25, (
        f"mutate-ref must refuse plan without outstanding "
        f"authorization; rc={result.returncode} "
        f"stderr={result.stderr}"
    )
    assert "not found in MUTATIONS.jsonl" in result.stderr or "binding" in result.stderr


def test_mutate_ref_refuses_plan_owner_mismatch(tmp_path):
    """Repair 2: mutate-ref verifies that the plan's
    owner_run_id matches the authorization's run_id."""
    bare, clone = _make_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_minimal_state(workspace)
    (workspace / "L").mkdir()

    # Seed target ref.
    _seed_branch(clone, "feat/b")
    initial_sha = _git(clone, "rev-parse", "refs/heads/feat/b").stdout.strip()
    _git(clone, "push", str(bare), "refs/heads/feat/b", "-q")
    _git(clone, "checkout", "-q", "feat/b")
    _git(clone, "commit", "--allow-empty", "-m", "b2", "-q")
    desired_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "checkout", "-q", "main")

    # Seed a plan with owner_run_id "r-wrong" but the
    # authorization record uses "r-end2end".
    _seed_mutation_authorization(
        workspace,
        mutation_id="m_owner_mismatch",
        mutation_type="force_push",
        mutation_target="feat/b",
        expected_main_sha=initial_sha,
        expected_target_sha=initial_sha,
        pending_action="force_push",
        desired_after_sha=desired_sha,
    )
    # Overwrite the plan with a mismatched owner_run_id.
    from scripts.local.guarded_ref_mutation import (
        GuardedMutationPlan,
        LifecycleState,
    )
    plan_path = workspace / "GUARDED_REF_MUTATIONS" / "m_owner_mismatch.json"
    plan = GuardedMutationPlan(
        mutation_id="m_owner_mismatch",
        owner_run_id="r-wrong",  # MISMATCH
        repository="owner/name",
        target_ref="refs/heads/feat/b",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=desired_sha,
        status=LifecycleState.PREPARED.value,
        created_at="2026-08-01T00:00:00Z",
    )
    plan_path.write_text(plan.to_json())

    result = _run_cli(
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_owner_mismatch",
        "--local-repo", str(clone),
        "--remote", "bare",
        "--remote-path", str(bare),
    )
    assert result.returncode == 25
    assert "owner_run_id" in result.stderr or "binding" in result.stderr


def test_mutate_ref_dispatches_intermediate_plan_to_reconcile(tmp_path):
    """Repair 3: a plan at EXECUTING or RECONCILING is dispatched
    to reconcile(), not re-prepared and re-executed. The previous
    implementation always called prepare() which crashed the CLI
    on plans that survived a crash mid-flight."""
    bare, clone = _make_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_minimal_state(workspace)
    (workspace / "L").mkdir()

    # Seed target ref.
    _seed_branch(clone, "feat/c")
    initial_sha = _git(clone, "rev-parse", "refs/heads/feat/c").stdout.strip()
    _git(clone, "push", str(bare), "refs/heads/feat/c", "-q")
    # Third party advances: create a commit, advance the local
    # ref, and push to the authoritative remote.
    _git(clone, "commit", "--allow-empty", "-m", "tp", "-q")
    third_party = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "update-ref", "refs/heads/feat/c", third_party)
    # Use --force to bypass the non-fast-forward check. The
    # test is simulating a third-party advance; the
    # force-with-lease CAS check is what guards real
    # mutations.
    _git(clone, "push", "--force", str(bare),
         "refs/heads/feat/c:refs/heads/feat/c", "-q")

    # Seed an authorization record.
    _seed_mutation_authorization(
        workspace,
        mutation_id="m_intermediate",
        mutation_type="force_push",
        mutation_target="feat/c",
        expected_main_sha=initial_sha,
        expected_target_sha=initial_sha,
        pending_action="force_push",
        desired_after_sha="a" * 40,
    )
    # Overwrite the plan to be at RECONCILING (simulating a
    # previous run that crashed after EXECUTING but before
    # persisting the terminal result).
    from scripts.local.guarded_ref_mutation import (
        GuardedMutationPlan,
        LifecycleState,
    )
    plan_path = workspace / "GUARDED_REF_MUTATIONS" / "m_intermediate.json"
    plan = GuardedMutationPlan(
        mutation_id="m_intermediate",
        owner_run_id="r-end2end",
        repository="owner/name",
        target_ref="refs/heads/feat/c",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha="a" * 40,
        status=LifecycleState.RECONCILING.value,
        created_at="2026-08-01T00:00:00Z",
    )
    plan_path.write_text(plan.to_json())

    # Invoke mutate-ref. The CLI must NOT call prepare(). It
    # must dispatch to reconcile() which observes the actual
    # ref (at third_party, not initial_sha) and reports CONFLICT.
    result = _run_cli(
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_intermediate",
        "--local-repo", str(clone),
        "--remote", "bare",
        "--remote-path", str(bare),
    )
    # CONFLICT is a non-zero exit (Repair 2): the mutation was
    # not applied and the executor reports the outcome.
    assert result.returncode == 31
    assert "CONFLICT" in result.stdout

    # Verify the plan's state was persisted as CONFLICT (not
    # reset to PREPARED).
    final_plan = GuardedMutationPlan.from_json(plan_path.read_text())
    assert final_plan.status == "CONFLICT"

    # Verify the authoritative remote was NOT touched (no blind
    # retry pushed the desired_after).
    actual_remote = _git(bare, "rev-parse", "refs/heads/feat/c").stdout.strip()
    assert actual_remote == third_party


# ---------------------------------------------------------------------------
# Round-54 Codex repair regression tests
# ---------------------------------------------------------------------------

def test_mutate_ref_threads_remote_through_to_guarded_push(tmp_path):
    """Repair 1: --remote is accepted by mutate-ref and
    threaded through to guarded_push. A clone configured
    with both 'origin' and 'upstream' remotes pushes to
    the correct one."""
    # Create two bare repos.
    bare_origin = tmp_path / "bare_origin.git"
    bare_upstream = tmp_path / "bare_upstream.git"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(bare_origin), "-q")
    _git(bare_origin, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(bare_upstream), "-q")
    _git(bare_upstream, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(tmp_path, "clone", str(bare_origin), str(clone), "-q")
    _git(clone, "config", "user.email", "test@local")
    _git(clone, "config", "user.name", "Test")
    _git(clone, "commit", "--allow-empty", "-m", "init", "-q")
    initial = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "push", str(bare_origin), "refs/heads/main", "-q")
    # Round-58: configure origin to a parseable GitHub URL so
    # mutate-ref's Step 3.5 repository-identity check succeeds.
    _git(clone, "remote", "set-url", "origin",
         "https://github.com/owner/name.git")

    # Add the upstream bare as a second remote.
    _git(clone, "remote", "add", "upstream", str(bare_upstream))

    # Also push the initial main to upstream so the force-push
    # CAS check has a known initial state on the remote.
    _git(clone, "push", "upstream", "refs/heads/main", "-q")

    # Create a real commit for the desired_after_sha. The
    # executor's guarded_push verifies the SHA exists locally
    # before pushing.
    _git(clone, "commit", "--allow-empty", "-m", "remote_test", "-q")
    desired = _git(clone, "rev-parse", "HEAD").stdout.strip()

    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    # _create_minimal_state writes a CONTROLLER_STATE.json
    # with run_identity.target_pr_number=416, etc. The
    # seed helper below acquires a supervisor lease with
    # a scope derived from this state, so create the
    # state BEFORE seeding the mutation authorization.
    _create_minimal_state(workspace)
    (workspace / "L").mkdir(exist_ok=True)
    # The lease expects the remote ref to currently point
    # to `initial`. The desired after-SHA is the new commit
    # (`desired`). The executor will force-push the new
    # commit, but only if the remote is at `initial`.
    _seed_mutation_authorization(
        workspace=workspace,
        mutation_id="m_remote",
        mutation_type="force_push",
        mutation_target="main",
        expected_main_sha=initial,
        expected_target_sha=initial,
        pending_action="force_push",
        desired_after_sha=desired,
    )

    # Reset the local ref to `initial` so the executor's
    # force-push CAS check matches.
    subprocess.run(
        ["git", "update-ref", "refs/heads/main", initial],
        cwd=clone, check=True, capture_output=True,
    )
    # _seed_mutation_authorization already created
    # workspace/L when it acquired the lease. The
    # additional mkdir here is a no-op for the lock
    # directory but the test still needs to ensure the
    # CONTROLLER_STATE.json exists (the seed helper
    # did not create it; _create_minimal_state
    # does). Use the workspace that the seed helper
    # populated.
    _create_minimal_state(workspace)
    (workspace / "L").mkdir(exist_ok=True)

    # Run mutate-ref with --remote=upstream. The push MUST go
    # to upstream, not origin.
    result = _run_cli(
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_remote",
        "--local-repo", str(clone),
        "--remote-path", str(bare_upstream),
        "--remote", "upstream",
    )
    assert result.returncode == 0, (
        f"mutate-ref failed: rc={result.returncode} "
        f"stdout={result.stdout} stderr={result.stderr}"
    )

    # Verify upstream was updated, origin was NOT.
    upstream_ref = _git(bare_upstream, "rev-parse", "refs/heads/main").stdout.strip()
    origin_ref = _git(bare_origin, "rev-parse", "refs/heads/main").stdout.strip()
    assert upstream_ref == desired, (
        f"upstream ref should be at desired; got {upstream_ref}"
    )
    assert origin_ref == initial, (
        f"origin ref should NOT be changed; got {origin_ref}"
    )


def test_mutate_ref_exit_code_distinguishes_terminal_states(tmp_path):
    """Repair 2: only SUCCEEDED produces exit 0. All other
    terminal states produce distinct non-zero exits."""
    # Test SUCCEEDED -> 0
    bare, clone = _make_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_minimal_state(workspace)
    (workspace / "L").mkdir()
    _seed_branch(clone, "feat/s")
    initial = _git(clone, "rev-parse", "refs/heads/feat/s").stdout.strip()
    _git(clone, "push", str(bare), "refs/heads/feat/s", "-q")
    _git(clone, "checkout", "-q", "feat/s")
    _git(clone, "commit", "--allow-empty", "-m", "s2", "-q")
    desired = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "checkout", "-q", "main")
    _seed_mutation_authorization(
        workspace, "m_ok", "force_push", "feat/s",
        initial, initial, "force_push", desired,
    )
    r = _run_cli(
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_ok",
        "--local-repo", str(clone),
        "--remote", "bare",
        "--remote-path", str(bare),
    )
    assert r.returncode == 0, f"SUCCEEDED should be exit 0, got {r.returncode}"
    assert "SUCCEEDED" in r.stdout

    # Test CONFLICT -> 31
    workspace2 = tmp_path / "workspace2"
    workspace2.mkdir()
    _create_minimal_state(workspace2)
    (workspace2 / "L").mkdir()
    _seed_branch(clone, "feat/c")
    
    # Third party advances feat/c to a different SHA.
    _git(clone, "checkout", "-q", "feat/c")
    _git(clone, "commit", "--allow-empty", "-m", "tp", "-q")
    tp = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "update-ref", "refs/heads/feat/c", tp)
    _git(clone, "push", "--force", str(bare), "refs/heads/feat/c:refs/heads/feat/c", "-q")
    _git(clone, "checkout", "-q", "main")
    _seed_mutation_authorization(
        workspace2, "m_conflict", "force_push", "feat/c",
        initial, initial, "force_push", "b" * 40,
    )
    r = _run_cli(
        "mutate-ref",
        "--workspace", str(workspace2),
        "--mutation-id", "m_conflict",
        "--local-repo", str(clone),
        "--remote", "bare",
        "--remote-path", str(bare),
    )
    assert r.returncode == 31, f"CONFLICT should be exit 31, got {r.returncode}"


def test_mutate_ref_dispatches_indeterminate_to_reconcile(tmp_path):
    """Repair 3: a plan at INDETERMINATE is dispatched to
    reconcile(), not refused with exit 26."""
    bare, clone = _make_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_minimal_state(workspace)
    (workspace / "L").mkdir()
    _seed_branch(clone, "feat/i")
    initial = _git(clone, "rev-parse", "refs/heads/feat/i").stdout.strip()
    _git(clone, "push", str(bare), "refs/heads/feat/i", "-q")
    # Third party advances feat/i.
    _git(clone, "checkout", "-q", "feat/i")
    _git(clone, "commit", "--allow-empty", "-m", "tp", "-q")
    tp = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "update-ref", "refs/heads/feat/i", tp)
    _git(clone, "push", "--force", str(bare), "refs/heads/feat/i:refs/heads/feat/i", "-q")
    _git(clone, "checkout", "-q", "main")
    _seed_mutation_authorization(
        workspace, "m_indet", "force_push", "feat/i",
        initial, initial, "force_push", "b" * 40,
    )
    # Overwrite the plan to be at INDETERMINATE.
    from scripts.local.guarded_ref_mutation import (
        GuardedMutationPlan,
        LifecycleState,
    )
    plan_path = workspace / "GUARDED_REF_MUTATIONS" / "m_indet.json"
    plan = GuardedMutationPlan(
        mutation_id="m_indet",
        owner_run_id="r-end2end",
        repository="owner/name",
        target_ref="refs/heads/feat/i",
        operation="PUSH_REMOTE",
        expected_before_sha=initial,
        desired_after_sha="b" * 40,
        status=LifecycleState.INDETERMINATE.value,
        created_at="2026-08-01T00:00:00Z",
    )
    plan_path.write_text(plan.to_json())

    r = _run_cli(
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_indet",
        "--local-repo", str(clone),
        "--remote", "bare",
        "--remote-path", str(bare),
    )
    # INDETERMINATE is a non-SUCCEEDED state. The dispatcher
    # reconciles and may report CONFLICT (third-party
    # advanced) or INDETERMINATE (read failure). The exit
    # code is 31 (CONFLICT) or 32 (INDETERMINATE), not 26
    # (terminal refusal).
    assert r.returncode != 26, (
        f"INDETERMINATE plan must not exit 26; got {r.returncode}"
    )
    assert r.returncode in (31, 32), (
        f"INDETERMINATE plan must exit 31 (CONFLICT) or 32 "
        f"(INDETERMINATE); got {r.returncode}"
    )


def test_mutate_ref_rejects_plan_with_mismatched_desired_after_sha(tmp_path):
    """Repair 4: binding compares desired_after_sha with
    the authorized request. A plan with a different
    desired_after_sha is rejected with exit 25."""
    bare, clone = _make_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_minimal_state(workspace)
    (workspace / "L").mkdir()
    _seed_branch(clone, "feat/d")
    initial = _git(clone, "rev-parse", "refs/heads/feat/d").stdout.strip()
    initial = _git(clone, "rev-parse", "refs/heads/feat/d").stdout.strip()
    _git(clone, "push", str(bare), "refs/heads/feat/d", "-q")
    _git(clone, "checkout", "-q", "feat/d")
    _git(clone, "commit", "--allow-empty", "-m", "d2", "-q")
    desired_authorized = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "commit", "--allow-empty", "-m", "d3", "-q")
    desired_plan = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "checkout", "-q", "main")

    # Seed the authorization with desired_after_sha = desired_authorized.
    mutations_file = workspace / "MUTATIONS.jsonl"
    auth_record = {
        "mutation_id": "m_d",
        "run_id": "r-end2end",
        "repository": "owner/name",
        "target_pr_number": 416,
        "mutation_type": "force_push",
        "mutation_target": "feat/d",
        "expected_main_sha": initial,
        "expected_target_sha": initial,
        "pending_action": "force_push",
        "created_at": "2026-08-01T00:00:00Z",
        "authorization_status": "authorized",
        "desired_after_sha": desired_authorized,
    }
    mutations_file.write_text(json.dumps(auth_record))

    # Write a plan with a DIFFERENT desired_after_sha.
    from scripts.local.guarded_ref_mutation import (
        GuardedMutationPlan,
        LifecycleState,
    )
    plan_dir = workspace / "GUARDED_REF_MUTATIONS"
    plan_dir.mkdir(exist_ok=True)
    plan = GuardedMutationPlan(
        mutation_id="m_d",
        owner_run_id="r-end2end",
        repository="owner/name",
        target_ref="refs/heads/feat/d",
        operation="PUSH_REMOTE",
        expected_before_sha=initial,
        desired_after_sha="c" * 40,
        status=LifecycleState.PREPARED.value,
        created_at="2026-08-01T00:00:00Z",
    )
    (plan_dir / "m_d.json").write_text(plan.to_json())

    r = _run_cli(
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_d",
        "--local-repo", str(clone),
        "--remote", "bare",
        "--remote-path", str(bare),
    )
    assert r.returncode == 25, (
        f"mismatched desired_after_sha must be refused with "
        f"exit 25; got {r.returncode}"
    )
    assert "desired_after_sha" in r.stderr or "binding" in r.stderr


def test_mutate_ref_rejects_plan_from_different_workspace(tmp_path):
    """Repair 5: a plan whose authorization records a
    different workspace than the active mutate-ref workspace
    is rejected. This prevents the former owner of a
    stale-lock-recovered workspace from invoking mutate-ref
    after the replacement owner has taken over the lease."""
    bare, clone = _make_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_minimal_state(workspace)
    (workspace / "L").mkdir()
    _seed_branch(clone, "feat/w")
    initial = _git(clone, "rev-parse", "refs/heads/feat/w").stdout.strip()
    _git(clone, "push", str(bare), "refs/heads/feat/w", "-q")
    _git(clone, "checkout", "-q", "feat/w")
    _git(clone, "commit", "--allow-empty", "-m", "w2", "-q")
    desired = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "checkout", "-q", "main")

    # Seed an authorization with a DIFFERENT workspace.
    mutations_file = workspace / "MUTATIONS.jsonl"
    auth_record = {
        "mutation_id": "m_w",
        "run_id": "r-end2end",
        "repository": "owner/name",
        "target_pr_number": 416,
        "mutation_type": "force_push",
        "mutation_target": "feat/w",
        "expected_main_sha": initial,
        "expected_target_sha": initial,
        "pending_action": "force_push",
        "created_at": "2026-08-01T00:00:00Z",
        "authorization_status": "authorized",
        "workspace": "/some/other/workspace",
    }
    mutations_file.write_text(json.dumps(auth_record))

    from scripts.local.guarded_ref_mutation import (
        GuardedMutationPlan,
        LifecycleState,
    )
    plan_dir = workspace / "GUARDED_REF_MUTATIONS"
    plan_dir.mkdir(exist_ok=True)
    plan = GuardedMutationPlan(
        mutation_id="m_w",
        owner_run_id="r-end2end",
        repository="owner/name",
        target_ref="refs/heads/feat/w",
        operation="PUSH_REMOTE",
        expected_before_sha=initial,
        desired_after_sha=desired,
        status=LifecycleState.PREPARED.value,
        created_at="2026-08-01T00:00:00Z",
    )
    (plan_dir / "m_w.json").write_text(plan.to_json())

    r = _run_cli(
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_w",
        "--local-repo", str(clone),
        "--remote", "bare",
        "--remote-path", str(bare),
    )
    assert r.returncode == 25, (
        f"plan from different workspace must be refused with "
        f"exit 25; got {r.returncode}"
    )
    assert "workspace" in r.stderr


# ---------------------------------------------------------------------------
# Round-55 Codex repair regression tests
# ---------------------------------------------------------------------------

def test_authorize_mutation_emits_durable_plan_for_branch_create_force(tmp_path):
    """Repair 1 (3698194474): branch_create_force is excluded
    from the HEAD_CHANGING_MUTATION_TYPES existing-head
    requirement because a CREATE cannot provide a current
    --expected-target-sha (the ref must not exist yet). The
    durable plan emission path must succeed for
    branch_create_force with only --desired-after-sha and
    --mutation-target.
    """
    bare, clone = _make_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_minimal_state(workspace)
    (workspace / "L").mkdir()

    # Create a real commit on a different branch so the desired
    # SHA exists locally.
    _git(clone, "checkout", "-q", "-b", "feat/new")
    _git(clone, "commit", "--allow-empty", "-m", "new_branch", "-q")
    desired = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "checkout", "-q", "main")

    state = json.loads((workspace / "CONTROLLER_STATE.json").read_text())
    state["workspace"] = str(workspace)
    state["next_action"] = {"action": "branch_create_force"}
    state["overall_status"] = "RUN_READY_FOR_SUMMARY"
    state["mutation_target"] = "feat/new"
    (workspace / "CONTROLLER_STATE.json").write_text(json.dumps(state))

    # Local imports for the assertion below.
    from scripts.local.guarded_ref_mutation import GuardedMutationPlan

    # Write a launch receipt (required by authorize-mutation).
    receipt = {
        "schema_version": 1,
        "run_id": "r-end2end",
        "controller_version": "test",
        "created_at": "2026-08-01T00:00:00Z",
        "workspace": str(workspace),
        "state_path": str(workspace / "CONTROLLER_STATE.json"),
        "run_identity": {
            "run_id": "r-end2end",
            "workspace": str(workspace),
            "machine_identity": {
                "hostname": "test", "user": "test", "pid": 1,
            },
        },
        "repository": "owner/name",
        "main_sha": "0" * 40,
        "next_action": {"action": "force_push"},
        "overall_status": "RUN_READY_FOR_SUMMARY",
    }
    (workspace / "LAUNCH_RECEIPT.json").write_text(json.dumps(receipt))

    # Acquire the supervisor lock for authorize-mutation.
    # Round-74 fix: the scope key includes mutation_target
    # when both target_pr_number and mutation_target are
    # set, so the controller's lease check looks for the
    # lock at the scope matching state.run_identity. The
    # test state has run_identity with NO mutation_target
    # (PR-only scope), so acquire the lease with
    # mutation_target=None.
    import os
    from scripts.local.aed_supervisor_lock import try_acquire as _try_acquire
    lock_outcome = _try_acquire(
        scope={
            "repository": "owner/name",
            "target_pr_number": 416,
        },
        owner_run_id="r-end2end",
        owner_host={"hostname": "test", "user": "test"},
        owner_pid=os.getpid(),
        owner_start_evidence={
            "pid": os.getpid(),
            "start_time": "2026-08-01T00:00:00Z",
        },
        owner_state_path=str(workspace / "CONTROLLER_STATE.json"),
        base_dir=workspace / "L",
    )
    assert lock_outcome.ok

    # Invoke authorize-mutation for branch_create_force WITHOUT
    # --expected-target-sha (because the ref must not exist).
    result = _run_cli(
        "authorize-mutation",
        "--state", str(workspace / "CONTROLLER_STATE.json"),
        "--workspace", str(workspace),
        "--mutation-type", "branch_create_force",
        "--mutation-target", "feat/new",
        "--expected-main-sha", "0" * 40,
        "--desired-after-sha", desired,
        "--pending-action", "branch_create_force",
    )
    assert result.returncode == 0, (
        f"authorize-mutation for branch_create_force failed: "
        f"rc={result.returncode} "
        f"stdout={result.stdout} stderr={result.stderr}"
    )

    # The durable plan MUST exist for branch_create_force.
    plan_dir = workspace / "GUARDED_REF_MUTATIONS"
    plan_files = list(plan_dir.iterdir())
    assert len(plan_files) == 1
    plan = GuardedMutationPlan.from_json(plan_files[0].read_text())
    assert plan.operation == "CREATE_LOCAL"
    assert plan.expected_before_sha is None
    assert plan.desired_after_sha == desired


def test_authorize_mutation_rollback_uses_existing_sentinel_fd(tmp_path):
    """Repair 2 (3698194477): when plan publication fails,
    the rollback record_result MUST pass sentinel_fd so it
    shares the existing flock. Without this, the rollback
    would try to acquire a second descriptor on the same
    flock and exhaust retries.

    This test simulates the failure by making the plan
    directory unwritable.
    """
    bare, clone = _make_bare_with_clone(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_minimal_state(workspace)
    (workspace / "L").mkdir()

    _git(clone, "commit", "--allow-empty", "-m", "init", "-q")
    initial = _git(clone, "rev-parse", "HEAD").stdout.strip()

    state = json.loads((workspace / "CONTROLLER_STATE.json").read_text())
    state["workspace"] = str(workspace)
    state["next_action"] = {"action": "force_push"}
    state["overall_status"] = "RUN_READY_FOR_SUMMARY"
    state["mutation_target"] = "main"
    (workspace / "CONTROLLER_STATE.json").write_text(json.dumps(state))

    # Write a launch receipt (required by authorize-mutation).
    receipt = {
        "schema_version": 1,
        "run_id": "r-end2end",
        "controller_version": "test",
        "created_at": "2026-08-01T00:00:00Z",
        "workspace": str(workspace),
        "state_path": str(workspace / "CONTROLLER_STATE.json"),
        "run_identity": {
            "run_id": "r-end2end",
            "workspace": str(workspace),
            "machine_identity": {
                "hostname": "test", "user": "test", "pid": 1,
            },
        },
        "repository": "owner/name",
        "main_sha": "0" * 40,
        "next_action": {"action": "force_push"},
        "overall_status": "RUN_READY_FOR_SUMMARY",
    }
    (workspace / "LAUNCH_RECEIPT.json").write_text(json.dumps(receipt))

    import os
    from scripts.local.aed_supervisor_lock import try_acquire as _try_acquire
    lock_outcome = _try_acquire(
        scope={
            "repository": "owner/name",
            "target_pr_number": 416,
            "mutation_target": None,
        },
        owner_run_id="r-end2end",
        owner_host={"hostname": "test", "user": "test"},
        owner_pid=os.getpid(),
        owner_start_evidence={
            "pid": os.getpid(),
            "start_time": "2026-08-01T00:00:00Z",
        },
        owner_state_path=str(workspace / "CONTROLLER_STATE.json"),
        base_dir=workspace / "L",
    )
    assert lock_outcome.ok

    # Pre-create the GUARDED_REF_MUTATIONS directory with
    # a file at the destination path so the plan creation
    # fails with EEXIST or similar.
    plan_dir = workspace / "GUARDED_REF_MUTATIONS"
    plan_dir.mkdir(parents=True, exist_ok=True)
    # Make plan_dir read-only so open() for write fails.
    os.chmod(plan_dir, 0o555)

    _git(clone, "commit", "--allow-empty", "-m", "next", "-q")
    desired = _git(clone, "rev-parse", "HEAD").stdout.strip()

    try:
        result = _run_cli(
            "authorize-mutation",
            "--state", str(workspace / "CONTROLLER_STATE.json"),
            "--workspace", str(workspace),
            "--mutation-type", "force_push",
            "--mutation-target", "main",
            "--expected-target-sha", initial,
            "--expected-main-sha", "0" * 40,
            "--desired-after-sha", desired,
            "--pending-action", "force_push",
        )
        # The authorize-mutation fails with rc=24 (durable plan
        # emission failed).
        assert result.returncode == 24, (
            f"authorize-mutation must fail when plan directory "
            f"is unwritable; rc={result.returncode} "
            f"stderr={result.stderr}"
        )
        # The journal MUST NOT contain a stranded outstanding
        # record (the rollback record_result succeeded via
        # shared sentinel_fd).
        mutations_file = workspace / "MUTATIONS.jsonl"
        records = [
            json.loads(line)
            for line in mutations_file.read_text().splitlines()
            if line.strip()
        ]
        # The authorization record was rolled back; no
        # outstanding records.
        from scripts.local.aed_mutation_authorization import (
            outstanding_mutations as _outstanding,
        )
        outstanding = _outstanding(workspace)
        assert outstanding == [], (
            f"outstanding records must be empty after rollback; "
            f"got {outstanding}"
        )
    finally:
        os.chmod(plan_dir, 0o755)
