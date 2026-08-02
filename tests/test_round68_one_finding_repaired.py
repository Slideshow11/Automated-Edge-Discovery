#!/usr/bin/env python3
"""
Round-68 regression test for the new Codex finding on commit f9156c2.

Verifies one finding addressed by the round-68 branch:

  T. PRRT_kwDOSHFpYM6Vz6RI  Reconcile URL-backed deletions
     against the remote. For a DELETE_LOCAL branch-deletion
     plan with an HTTPS, SSH, or file:// configured remote
     and no --remote-path, the runner must use the URL
     fallback (ls-remote on the configured remote URL) for
     reconciliation, not read from the local clone. A
     successful remote deletion is then reported as
     SUCCEEDED, not NOT_APPLIED.

Other findings deferred to follow-up commits:

  U. PRRT_kwDOSHFpYM6Vz6RG  Preserve artifacts when rejecting
     a repeated init. (Documented; rollback path touches
     many sites.)

  V. PRRT_kwDOSHFpYM6Vz6RH  Hold the journal sentinel
     until the lease is released. (Documented; requires
     controller-wide restructuring.)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.local import guarded_ref_mutation as grm
from scripts.local import guarded_ref_mutation_runner as runner
from scripts.local import guarded_ref_ops as ops


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=check,
    )


def _make_bare_with_clone(
    tmp_path: Path,
    *,
    owner: str = "owner",
    name: str = "name",
):
    bare = tmp_path / f"{name}.git"
    clone = tmp_path / f"{name}_clone"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(bare), "-q")
    _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(tmp_path, "clone", str(bare), str(clone), "-q")
    _git(clone, "config", "user.email", "test@local")
    _git(clone, "config", "user.name", "Test")
    _git(clone, "remote", "set-url", "origin",
         f"https://github.com/{owner}/{name}.git")
    _git(clone, "commit", "--allow-empty", "-m", "init", "-q")
    _git(clone, "push", str(bare), "refs/heads/main", "-q")
    return bare, clone


def _seed(clone: Path, ref: str = "refs/heads/main") -> str:
    _git(clone, "commit", "--allow-empty", "-m", "init", "-q")
    sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    if ref != "refs/heads/main":
        _git(clone, "update-ref", ref, sha)
    return sha


def test_t_url_backed_branch_delete_reconciles_via_ls_remote(
    tmp_path,
):
    """T.1: branch_delete with a URL-backed remote
    (HTTPS) and no --remote-path must reconcile via
    ls-remote on the configured URL. A successful remote
    deletion is then reported as SUCCEEDED.

    The test mocks `guarded_push` to simulate a successful
    remote push-delete and `_read_remote_ref_via_ls_remote`
    to return None (ref deleted) on the URL. After
    execute(), the final status is SUCCEEDED.
    """
    bare, clone = _make_bare_with_clone(tmp_path)
    initial = _seed(clone, ref="refs/heads/feat/url")

    # The test doesn't actually push to GitHub (no network).
    # We rely on the mocked guarded_push + mocked ls-remote.
    # The key assertion is that reconciliation uses
    # ls-remote on the configured URL, not the local clone.

    plan = grm.GuardedMutationPlan(
        mutation_id="m_url_del",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/feat/url",
        operation="DELETE_LOCAL",
        expected_before_sha=initial,
        desired_after_sha=None,
        status="PREPARED",
        created_at="",
    )
    orch = runner.GuardedMutationOrchestrator(workspace=tmp_path, plan=plan)
    orch.prepare()

    ls_remote_calls = []

    def fake_push(**kwargs):
        return ops.RefMutationResult(
            ok=True, actual_ref_sha=None,
            stdout="", stderr="", returncode=0,
        )

    def fake_ls_remote(remote_url: str, ref: str):
        ls_remote_calls.append((remote_url, ref))
        # Simulate a successful remote deletion: the ref
        # is no longer present.
        return runner._ReadResult(sha=None, indeterminate=False)

    with patch.object(ops, "guarded_push", side_effect=fake_push), \
         patch.object(runner, "_read_remote_ref_via_ls_remote",
                      side_effect=fake_ls_remote):
        final = orch.execute(local_repo=clone, remote_ref_path=None)

    # Reconciliation used ls-remote on the configured URL.
    assert len(ls_remote_calls) == 1, (
        f"expected exactly 1 ls-remote call; got {len(ls_remote_calls)}"
    )
    assert ls_remote_calls[0][0] == "https://github.com/owner/name.git", (
        f"ls-remote must use the configured origin URL; "
        f"got {ls_remote_calls[0][0]!r}"
    )
    # The final status is SUCCEEDED (not NOT_APPLIED)
    # because the ls-remote read returned None (ref
    # deleted), and DELETE with desired_after=None and
    # actual=None returns SUCCEEDED.
    assert final.status == grm.LifecycleState.SUCCEEDED.value, (
        f"URL-backed branch_delete reconcile must report "
        f"SUCCEEDED; got {final.status}"
    )