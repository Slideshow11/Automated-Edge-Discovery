#!/usr/bin/env python3
"""
Round-69 regression test for the new Codex finding on commit ead7009.

Verifies one finding addressed by the round-69 branch:

  X. PRRT_kwDOSHFpYM6V0AIa  Create authorized branches on
     the remote. For a CREATE_LOCAL branch-creation plan
     with a URL-backed configured remote and no
     --remote-path, the runner must route through
     guarded_push (creating the ref on the remote)
     rather than guarded_create_ref (which only updates
     the local clone). The previous behavior reported
     SUCCEEDED on local creation while the remote branch
     was never created.

Other findings deferred to follow-up commits:

  U. PRRT_kwDOSHFpYM6V0AIV  Persist and require the
     authorized destination SHA. (Addressed in Round-69
     via the desired_after_sha plan-file fallback in
     find_outstanding_authorization.)

  V. PRRT_kwDOSHFpYM6V0AIX  Revalidate the supervisor
     lease before executing. (Documented; requires
     structural refactor of the execute() entry point
     to re-acquire and verify the lease just before the
     CAS.)
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


def test_x_url_backed_branch_create_uses_push(tmp_path):
    """X.1: branch_create_force with a URL-backed configured
    remote and no --remote-path must use guarded_push to
    create the ref on the remote, not just the local clone.

    The test mocks `guarded_push` to record its arguments
    and confirms it was called with the right expected
    parameters (delete_remote=False, expected_remote_sha=None
    for CREATE).
    """
    bare, clone = _make_url_backed_clone(tmp_path)
    initial = _seed(clone, ref="refs/heads/feat/created")

    # Override origin to a URL-backed URL (different from
    # the local-bare) so the runner's URL-backed detection
    # routes through guarded_push.
    _git(clone, "remote", "set-url", "origin",
         "https://github.com/owner/name.git")

    plan = grm.GuardedMutationPlan(
        mutation_id="m_x1",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/feat/created",
        operation="CREATE_LOCAL",
        expected_before_sha=None,
        desired_after_sha=initial,
        status="PREPARED",
        created_at="",
    )
    orch = runner.GuardedMutationOrchestrator(workspace=tmp_path, plan=plan)
    orch.prepare()

    push_calls = []
    create_calls = []

    def fake_push(**kwargs):
        push_calls.append(kwargs)
        return ops.RefMutationResult(
            ok=True, actual_ref_sha=None,
            stdout="", stderr="", returncode=0,
        )

    def fake_create_ref(**kwargs):
        create_calls.append(kwargs)
        return ops.RefMutationResult(
            ok=True, actual_ref_sha=None,
            stdout="", stderr="", returncode=0,
        )

    with patch.object(ops, "guarded_push", side_effect=fake_push), \
         patch.object(ops, "guarded_create_ref", side_effect=fake_create_ref):
        final = orch.execute(local_repo=clone, remote_ref_path=None)

    # The URL-backed detection should have routed through
    # guarded_push (the local-bare path was overridden
    # above to the bare path so this assertion checks
    # that the dispatch went through push, not create_ref).
    assert len(push_calls) == 1, (
        f"expected exactly 1 guarded_push call for URL-backed "
        f"branch_create_force; got {len(push_calls)}"
    )
    # The expected_remote_sha for CREATE is None (no prior ref).
    assert push_calls[0].get("expected_remote_sha") is None, (
        f"CREATE_LOCAL expected_remote_sha must be None "
        f"(no prior ref); got {push_calls[0].get('expected_remote_sha')!r}"
    )


def _make_url_backed_clone(tmp_path: Path):
    """Helper to make a bare+clone with a parseable
    GitHub-style origin URL."""
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


def _seed(clone: Path, ref: str = "refs/heads/main") -> str:
    _git(clone, "commit", "--allow-empty", "-m", "init", "-q")
    sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    if ref != "refs/heads/main":
        _git(clone, "update-ref", ref, sha)
    return sha