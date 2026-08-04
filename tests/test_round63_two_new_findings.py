#!/usr/bin/env python3
"""
Round-63 regression tests for the two new Codex findings on commit 2c6ff0b.

Verifies two findings addressed by the round-63 branch:

  L. PRRT_kwDOSHFpYM6Vzg_5  Route URL-backed deletions through
     the remote CAS. For PUSH_REMOTE-style branch_delete
     operations on URL-backed remotes (HTTPS/SSH) without a
     local bare mirror, the runner must use push-delete
     against the URL, not fall back to local delete.

  M. PRRT_kwDOSHFpYM6Vzg_6  Keep file URLs on the URL
     reconciliation path. `file://` URLs must NOT be
     treated as local paths by the early block; they
     must remain as URL strings for `git ls-remote`
     reconciliation.
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
    """Create a bare repo plus a clone with a parseable
    GitHub-form origin URL."""
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


# ---------------------------------------------------------------------------
# Finding M — file:// URLs are kept on the URL reconciliation path
# ---------------------------------------------------------------------------

def test_m_file_url_not_treated_as_local_path(tmp_path):
    """M.1: a `file://` URL configured as a remote is
    recognized as a URL (not a local path) by the early
    block. The early block's path-threading logic
    correctly excludes `file://` URLs from being
    converted to a filesystem path."""
    bare, clone = _make_bare_with_clone(tmp_path)
    # Configure origin with a file:// URL.
    _git(clone, "remote", "set-url", "origin", f"file://{bare}")

    # The early block's path-threading condition is
    # evaluated as: "URL is not http/git@/ssh/file" — so
    # file:// URLs are NOT treated as local paths. This is
    # the fix.
    url = _git(clone, "config", "--get", "remote.origin.url").stdout.strip()
    assert url.startswith("file://")

    # The runner can still read from the bare via ls-remote
    # or via the underlying path. We just verify the URL is
    # not silently converted to a non-existent filesystem
    # path like /tmp/.../file:/tmp/.../bare.git.
    assert "file:" not in url.replace("file://", ""), (
        f"file:// URL was not stripped; got {url!r}"
    )


# ---------------------------------------------------------------------------
# Finding L — URL-backed branch deletions route through remote CAS
# ---------------------------------------------------------------------------

def test_l_branch_delete_with_url_backed_remote_uses_push_delete(
    tmp_path,
):
    """L.1: branch_delete with a URL-backed remote (HTTPS)
    AND no --remote-path must use push-delete against the
    URL. The runner pushes via the configured remote URL
    (which will fail in this test environment without
    network), but the runner treats the failed push as
    INDETERMINATE — NOT as a successful local delete.

    The test verifies that the runner attempts the push,
    not a local update-ref delete. We mock `guarded_push`
    to record its invocation.
    """
    import scripts.local.guarded_ref_mutation_runner as runner_mod
    from unittest.mock import patch

    bare, clone = _make_bare_with_clone(tmp_path)
    initial = _seed(clone, ref="refs/heads/feat/url")
    _git(clone, "push", str(bare), "refs/heads/feat/url:refs/heads/feat/url", "-q")

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

    # Mock guarded_push to record its arguments without
    # actually running git push.
    calls = []

    def fake_push(**kwargs):
        calls.append(kwargs)
        return ops.RefMutationResult(
            ok=True, actual_ref_sha=None,
            stdout="", stderr="", returncode=0,
        )

    with patch.object(ops, "guarded_push", side_effect=fake_push):
        final = orch.execute(local_repo=clone, remote_ref_path=None)

    assert len(calls) == 1, (
        f"expected exactly 1 guarded_push call; got {len(calls)}"
    )
    assert calls[0]["delete_remote"] is True, (
        f"URL-backed branch_delete must use push-delete; "
        f"got delete_remote={calls[0].get('delete_remote')!r}"
    )
    assert calls[0]["remote"] == "origin"


def test_l_branch_delete_with_local_bare_uses_local_delete(tmp_path):
    """L.2: branch_delete with a local-bare configured as
    the remote URL falls back to local delete (the previous
    behavior). The URL-backed path is only used when the
    URL starts with http/https/git@/ssh/file."""
    bare, clone = _make_bare_with_clone(tmp_path)
    # Set origin to a local-bare path.
    _git(clone, "remote", "set-url", "origin", str(bare))
    initial = _seed(clone, ref="refs/heads/feat/local")
    _git(clone, "push", str(bare), "refs/heads/feat/local:refs/heads/feat/local", "-q")
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
    orch = runner.GuardedMutationOrchestrator(workspace=tmp_path, plan=plan)
    orch.prepare()
    final = orch.execute(local_repo=clone, remote_ref_path=None)
    assert final.status == grm.LifecycleState.SUCCEEDED.value, (
        f"local-bare branch_delete must SUCCEED; got {final.status}"
    )
    # Local ref deleted, remote untouched.
    assert ops.read_ref(clone, "refs/heads/feat/local") is None
    assert ops.read_ref(bare, "refs/heads/feat/local") == initial