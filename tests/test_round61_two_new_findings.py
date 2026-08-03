#!/usr/bin/env python3
"""
Round-61 regression tests for the new Codex findings on commit d99cb3d.

Verifies two findings addressed by the round-61 branch:

  H. PRRT_kwDOSHFpYM6VzOP7  Reconcile resumptions against the
     configured remote. For PUSH_REMOTE plans resumed from
     EXECUTING / RECONCILING / INDETERMINATE / NOT_APPLIED
     without --remote-path, the runner must NOT read the
     local clone's ref as authoritative (the local branch
     may already point at desired_after_sha without the
     push having happened). The runner falls back to
     `git ls-remote` over the configured remote URL.

  I. PRRT_kwDOSHFpYM6VzOP8  Preserve restrictive mode when
     rewriting plans. The runner's _persist_plan() helper
     must use safe_restrictive_open (0o600 on POSIX) instead
     of Path.write_text (which creates files at the process
     umask, commonly 0o644 on POSIX).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.local import guarded_ref_mutation as grm
from scripts.local import guarded_ref_mutation_runner as runner
from scripts.local.autocoder_run_controller import main as controller_main


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=check,
    )


def _make_bare_with_clone(tmp_path: Path):
    bare = tmp_path / "name.git"
    clone = tmp_path / "name_clone"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(bare), "-q")
    _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(tmp_path, "clone", str(bare), str(clone), "-q")
    _git(clone, "config", "user.email", "test@local")
    _git(clone, "config", "user.name", "Test")
    _git(clone, "remote", "set-url", "origin",
         "https://github.com/owner/name.git")
    _git(clone, "commit", "--allow-empty", "-m", "init", "-q")
    _git(clone, "push", str(bare), "refs/heads/main", "-q")
    return bare, clone


def _write_workspace(
    workspace: Path,
    *,
    plan: grm.GuardedMutationPlan,
    mutation_id: str,
    repository: str = "owner/name",
):
    """Write the durable plan file and a matching MUTATIONS.jsonl
    authorization record so that mutate-ref's binding succeeds.

    Round-61 P2 fix (PRRT_kwDOSHFpYM6VzOP8): the durable
    plan file is written via safe_restrictive_open so the
    initial mode is 0o600 on POSIX. The runner's
    _persist_plan rewrite preserves this mode across the
    os.replace swap."""
    from scripts.local.aed_run_identity import safe_restrictive_open
    plan_dir = workspace / "GUARDED_REF_MUTATIONS"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / f"{mutation_id}.json"
    fd = safe_restrictive_open(plan_path, "w")
    try:
        fd.write(plan.to_json())
        fd.flush()
        os.fsync(fd.fileno())
    finally:
        fd.close()
    # Round-111 P1 fix: derive mutation_type from the plan's
    # operation so the round-111 binding check
    # (mutation_type vs plan.operation) accepts the fixture.
    # Mapping is the inverse of the round-111 fix in
    # mutation_policy.POLICY_TABLE:
    #   PUSH_REMOTE   -> force_push
    #   UPDATE_LOCAL  -> push
    #   SQUASH_MERGE  -> squash_merge
    #   CREATE_LOCAL  -> branch_create_force
    _OP_TO_MUTATION_TYPE = {
        "PUSH_REMOTE": "force_push",
        "UPDATE_LOCAL": "update_local",
        "SQUASH_MERGE": "squash_merge",
        "CREATE_LOCAL": "branch_create_force",
        "DELETE_LOCAL": "branch_delete",
    }
    _mutation_type = _OP_TO_MUTATION_TYPE.get(plan.operation, "force_push")
    auth_record = {
        "mutation_id": mutation_id,
        "run_id": "r1",
        "repository": repository,
        "target_pr_number": 416,
        "mutation_target": "main",
        "mutation_type": _mutation_type,
        "expected_main_sha": plan.expected_before_sha,
        "expected_target_sha": plan.expected_before_sha,
        "pending_action": _mutation_type,
        "created_at": "2026-08-02T00:00:00Z",
        "authorization_status": "authorized",
        "result": None,
    }
    journal_path = workspace / "MUTATIONS.jsonl"
    fd = safe_restrictive_open(journal_path, "w")
    try:
        fd.write(json.dumps(auth_record) + "\n")
        fd.flush()
        os.fsync(fd.fileno())
    finally:
        fd.close()
    return plan_path


def _file_mode(path: Path) -> int:
    """Return the file's mode bits (last 12 bits for
    POSIX rwx per owner/group/other)."""
    return path.stat().st_mode & 0o7777


# ---------------------------------------------------------------------------
# Finding I — preserve restrictive mode when rewriting plans
# ---------------------------------------------------------------------------

def test_i_persist_plan_preserves_restrictive_mode(tmp_path, monkeypatch):
    """I.1: after _persist_plan runs, the plan file is 0o600
    on POSIX (no world-readable exposure)."""
    # POSIX-only: skip on Windows where mode bits differ.
    if not hasattr(os, "umask") or os.name != "posix":
        pytest.skip("POSIX-only test")

    # Force a permissive process umask to ensure the tmp
    # file's umask-derived mode would leak if not handled.
    original_umask = os.umask(0o022)
    try:
        bare, clone = _make_bare_with_clone(tmp_path)
        initial_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
        new_sha = _git(clone, "commit", "--allow-empty", "-m", "next", "-q")
        new_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
        _git(clone, "push", str(bare), "refs/heads/main", "-q")

        workspace = tmp_path / "ws"
        workspace.mkdir()
        plan = grm.GuardedMutationPlan(
            mutation_id="m_mode_test",
            owner_run_id="r1",
            repository="owner/name",
            target_ref="refs/heads/main",
            operation="PUSH_REMOTE",
            expected_before_sha=initial_sha,
            desired_after_sha=new_sha,
            status="PREPARED",
            created_at="",
        )
        _write_workspace(workspace, plan=plan, mutation_id="m_mode_test")

        # Pre-existing plan file (authorization-time) was
        # written with restrictive_open. Verify the
        # initial mode is 0o600.
        plan_path = workspace / "GUARDED_REF_MUTATIONS" / "m_mode_test.json"
        initial_mode = _file_mode(plan_path)
        assert initial_mode & 0o077 == 0, (
            f"initial plan file must not be world/group readable; "
            f"mode={oct(initial_mode)}"
        )

        # Trigger _persist_plan via the orchestrator.
        orch = runner.GuardedMutationOrchestrator(
            workspace=workspace, plan=plan,
        )
        orch.prepare()

        # After _persist_plan, the mode must STILL be 0o600.
        post_mode = _file_mode(plan_path)
        assert post_mode & 0o077 == 0, (
            f"after _persist_plan the plan file must not be "
            f"world/group readable; mode={oct(post_mode)}"
        )
    finally:
        os.umask(original_umask)


def test_i_persist_plan_uses_restrictive_open(tmp_path):
    """I.2: the runner imports and uses safe_restrictive_open
    (the same helper as the authorization writer) so the
    tmp file is created with 0o600 mode before replace."""
    # Verify by checking the source — the helper must be
    # imported and the tmp file must be opened via the helper.
    import inspect
    source = inspect.getsource(runner._persist_plan)
    assert "safe_restrictive_open" in source, (
        "_persist_plan must use safe_restrictive_open to "
        "create the tmp file with restrictive mode"
    )
    assert "Path(tmp).write_text" not in source, (
        "_persist_plan must not use Path.write_text (creates "
        "with the process umask, often 0o644)"
    )
    assert "os.fsync" in source, (
        "_persist_plan must fsync the tmp file before "
        "os.replace to ensure durability"
    )


# ---------------------------------------------------------------------------
# Finding H — reconcile resumptions against configured remote
# ---------------------------------------------------------------------------

def test_h_push_resume_reconciles_against_remote_not_local(tmp_path):
    """H.1: when a PUSH_REMOTE plan is resumed (status =
    EXECUTING / RECONCILING / INDETERMINATE / NOT_APPLIED)
    without --remote-path, the runner must NOT read the
    local clone's branch as authoritative. It must fall
    back to `git ls-remote` over the configured remote URL.

    Use a non-deterministic remote (unreachable GitHub URL)
    so ls-remote fails → INDETERMINATE. The test would
    have falsely SUCCEEDED before the fix (reading the
    local clone's branch which already points at
    desired_after_sha).
    """
    bare, clone = _make_bare_with_clone(tmp_path)
    initial_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "commit", "--allow-empty", "-m", "next", "-q")
    new_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "push", str(bare), "refs/heads/main", "-q")

    # Override origin with an unreachable GitHub URL that
    # STILL matches the plan's canonical identity. ls-remote
    # fails (no network access) → INDETERMINATE. The clone's
    # local branch is at new_sha, so a PRE-fix implementation
    # that reads from local_repo would mis-classify as
    # SUCCEEDED.
    _git(clone, "remote", "set-url", "origin",
         "https://github.com/owner/name")

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_resume",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=new_sha,
        # Resume from RECONCILING (intermediate state).
        status="RECONCILING",
        created_at="2026-08-02T00:00:00Z",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_resume")

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_resume",
        "--local-repo", str(clone),
        # no --remote-path — runner must use ls-remote
    ])
    # ls-remote to the unreachable URL fails → INDETERMINATE.
    # Pre-fix would have read the local clone branch (at
    # new_sha) and classified as SUCCEEDED. Post-fix:
    # INDETERMINATE.
    assert rc == 32, (
        f"PUSH_REMOTE resume without --remote-path must NOT "
        f"classify as SUCCEEDED by reading the local clone; "
        f"ls-remote against an unreachable URL yields "
        f"INDETERMINATE (32); got rc={rc}"
    )


def test_h_non_push_resume_still_uses_local_repo(tmp_path):
    """H.2: for UPDATE_LOCAL / CREATE_LOCAL / DELETE_LOCAL
    operations, the local clone IS authoritative and the
    resume path correctly uses the local_repo fallback."""
    bare, clone = _make_bare_with_clone(tmp_path)
    initial_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "commit", "--allow-empty", "-m", "next", "-q")
    new_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "push", str(bare), "refs/heads/main", "-q")

    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Resume UPDATE_LOCAL from RECONCILING. The local
    # clone IS the target; reconciliation reads from it.
    plan = grm.GuardedMutationPlan(
        mutation_id="m_update_resume",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="UPDATE_LOCAL",
        expected_before_sha=initial_sha,
        desired_after_sha=new_sha,
        status="RECONCILING",
        created_at="2026-08-02T00:00:00Z",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_update_resume")

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_update_resume",
        "--local-repo", str(clone),
    ])
    # The local clone's main is at new_sha (desired_after).
    # UPDATE_LOCAL uses the local repo as authoritative, so
    # reconcile → SUCCEEDED.
    assert rc == 0, (
        f"UPDATE_LOCAL resume must use the local repo as "
        f"authoritative (where desired_after already matches); "
        f"got rc={rc}"
    )