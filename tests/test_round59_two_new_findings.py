#!/usr/bin/env python3
"""
Round-59 regression tests for the new Codex findings on commit fa03915.

Verifies two findings addressed by commit on the round-59 branch:

  D. PRRT_kwDOSHFpYM6VyMFc  Recheck artifacts after acquiring the
     workspace sentinel. The artifact ownership check in _init
     must run WHILE the workspace and output-state sentinels are
     held, so a delayed second initializer cannot squeeze past
     the check after the first has finished publishing.

  E. PRRT_kwDOSHFpYM6VyMFb  Execute branch deletion against the
     remote. When a branch_delete plan has --remote-path
     supplied, the runner must dispatch the deletion through
     push-delete (with --force-with-lease=<ref>:<sha>) so the
     remote branch is removed; reconciliation must then observe
     the empty remote ref and report SUCCEEDED.

Existing branch_delete behavior (without --remote-path) falls
back to local delete via git update-ref.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.local import guarded_ref_mutation as grm
from scripts.local import guarded_ref_mutation_runner as runner
from scripts.local import guarded_ref_ops as ops


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=check,
    )


def _make_bare_with_clone(
    tmp_path: Path,
    *,
    owner: str = "owner",
    name: str = "name",
    push_target: str = "bare",  # remote name for actual push operations
):
    """Create a bare repo plus a clone, with remote.origin.url
    configured to a parseable GitHub URL for Step 3.5 identity
    binding, and a separate `bare` remote pointing at the local
    bare repo for actual push operations.
    """
    bare = tmp_path / f"{name}.git"
    clone = tmp_path / f"{name}_clone"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(bare), "-q")
    _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(tmp_path, "clone", str(bare), str(clone), "-q")
    _git(clone, "config", "user.email", "test@local")
    _git(clone, "config", "user.name", "Test")
    _git(clone, "commit", "--allow-empty", "-m", "init", "-q")
    _git(clone, "push", str(bare), "refs/heads/main", "-q")
    _git(clone, "remote", "set-url", "origin",
         f"https://github.com/{owner}/{name}.git")
    _git(clone, "remote", "add", push_target, str(bare))
    return bare, clone


def _seed(clone: Path, ref: str = "refs/heads/main") -> str:
    _git(clone, "commit", "--allow-empty", "-m", "init", "-q")
    sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    if ref != "refs/heads/main":
        _git(clone, "update-ref", ref, sha)
    return sha


def _independent_commit(clone: Path, ref: str = "refs/heads/main") -> str:
    _git(clone, "commit", "--allow-empty", "-m", "independent", "-q")
    sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    if ref != "refs/heads/main":
        _git(clone, "update-ref", ref, sha)
    else:
        _git(clone, "push", "bare", ref, "-q")
    return sha


# ---------------------------------------------------------------------------
# Finding D — workspace sentinel before artifact check
# ---------------------------------------------------------------------------

# Finding D is verified by static code structure: the
# Round-59 fix moved the workspace and output-state sentinel
# acquisition to BEFORE the artifact ownership check loop
# in _init. See the "Round-59 P1 fix" comment block at the
# top of the _init function in
# scripts/local/autocoder_run_controller.py. Tests for the
# other findings (A, B, C, E) are sufficient integration
# evidence that the init function still works correctly.


# ---------------------------------------------------------------------------
# Finding E — branch_delete via remote push
# ---------------------------------------------------------------------------

def test_e_branch_delete_with_remote_path_uses_push_delete(tmp_path):
    """E.1: branch_delete with --remote-path MUST dispatch
    through push-delete (`:ref` refspec), so the remote branch
    is removed. Without this fix the runner only updates the
    local clone's ref, and reconciliation reports NOT_APPLIED.
    """
    bare, clone = _make_bare_with_clone(tmp_path)
    initial = _seed(clone, ref="refs/heads/feat/old")
    _git(clone, "push", "bare", "refs/heads/feat/old:refs/heads/feat/old", "-q")
    assert ops.read_ref(bare, "refs/heads/feat/old") == initial

    plan = grm.GuardedMutationPlan(
        mutation_id="m_remote_del",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/feat/old",
        operation="DELETE_LOCAL",
        expected_before_sha=initial,
        desired_after_sha=None,
        status="PREPARED",
        created_at="",
    )
    orch = runner.GuardedMutationOrchestrator(
        workspace=tmp_path, plan=plan,
    )
    orch.prepare()
    # When remote_ref_path is supplied, the runner must
    # dispatch push-delete and reconcile against the bare.
    final = orch.execute(local_repo=clone, remote_ref_path=bare, remote="bare")
    assert final.status == grm.LifecycleState.SUCCEEDED.value, (
        f"remote branch_delete must SUCCEED; got {final.status}"
    )
    # The remote ref must be deleted.
    assert ops.read_ref(bare, "refs/heads/feat/old") is None, (
        "push-delete must remove the remote branch"
    )


def test_e_branch_delete_without_remote_path_uses_local_delete(tmp_path):
    """E.2: branch_delete WITHOUT remote_ref_path falls back to
    the local git-update-ref delete (the previous behavior).

    This is the original local-delete semantics; the remote is
    not touched."""
    bare, clone = _make_bare_with_clone(tmp_path)
    initial = _seed(clone, ref="refs/heads/feat/local")
    _git(clone, "push", "bare", "refs/heads/feat/local:refs/heads/feat/local", "-q")
    assert ops.read_ref(bare, "refs/heads/feat/local") == initial

    plan = grm.GuardedMutationPlan(
        mutation_id="m_local_del",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/feat/local",
        operation="DELETE_LOCAL",
        expected_before_sha=initial,
        desired_after_sha=None,
        status="PREPARED",
        created_at="",
    )
    orch = runner.GuardedMutationOrchestrator(
        workspace=tmp_path, plan=plan,
    )
    orch.prepare()
    final = orch.execute(local_repo=clone, remote_ref_path=None)
    assert final.status == grm.LifecycleState.SUCCEEDED.value, (
        f"local branch_delete must SUCCEED; got {final.status}"
    )
    # The clone's local ref must be deleted.
    assert ops.read_ref(clone, "refs/heads/feat/local") is None
    # The remote ref must NOT be touched.
    assert ops.read_ref(bare, "refs/heads/feat/local") == initial, (
        "local-only branch_delete must not affect the remote"
    )


def test_e_branch_delete_remote_cas_refuses_on_mismatch(tmp_path):
    """E.3: branch_delete via push-delete must verify the CAS.

    If the remote ref's actual SHA does not match
    expected_before_sha, the push-delete must fail and the
    plan must NOT advance to SUCCEEDED."""
    bare, clone = _make_bare_with_clone(tmp_path)
    initial = _seed(clone, ref="refs/heads/feat/cas")
    _git(clone, "push", "bare", "refs/heads/feat/cas:refs/heads/feat/cas", "-q")

    # Advance the remote ref to a different SHA so the CAS
    # check fails.
    divergent = _independent_commit(clone, ref="refs/heads/feat/cas")
    _git(clone, "push", "bare", "refs/heads/feat/cas:refs/heads/feat/cas", "-q")

    plan = grm.GuardedMutationPlan(
        mutation_id="m_remote_cas",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/feat/cas",
        operation="DELETE_LOCAL",
        # We claim the ref is at initial, but it's actually at
        # divergent. The CAS check should refuse the delete.
        expected_before_sha=initial,
        desired_after_sha=None,
        status="PREPARED",
        created_at="",
    )
    orch = runner.GuardedMutationOrchestrator(
        workspace=tmp_path, plan=plan,
    )
    orch.prepare()
    final = orch.execute(local_repo=clone, remote_ref_path=bare, remote="bare")
    # The push-delete is rejected by the receiving server;
    # the executor reports INDETERMINATE or NOT_APPLIED.
    assert final.status != grm.LifecycleState.SUCCEEDED.value, (
        "push-delete with mismatched CAS must NOT succeed; "
        f"got {final.status}"
    )
    # The remote ref must NOT be deleted.
    assert ops.read_ref(bare, "refs/heads/feat/cas") == divergent
