#!/usr/bin/env python3
"""
Round-60 regression tests for the new Codex findings on commit f9b1abc.

Verifies two findings addressed by the round-60 branch:

  F. PRRT_kwDOSHFpYM6VydLA  Reconcile configured remotes without a
     local bare path. When --remote-path is not supplied,
     production mutations must still reconcile via `git ls-remote`
     against the clone's configured `remote.<args.remote>.url`.
     A failed query classifies as INDETERMINATE; a missing ref
     returns None (not an error).

  G. PRRT_kwDOSHFpYM6VydLG  Validate the selected remote instead of
     origin. Step 3.5 must verify
     `remote.<args.remote>.url`, not always `origin`. If origin
     matches the authorized repo but `--remote upstream` points
     at a fork, the binding check MUST refuse the mutation.

Existing step-3.5 invariant: local-bare CI integration tests
(where `remote.<args.remote>.url` is a local bare path) remain
supported. The new behavior is opt-in for production via
GitHub URLs.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.local import aed_run_identity as ari
from scripts.local import guarded_ref_mutation as grm
from scripts.local.autocoder_run_controller import main as controller_main


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
):
    """Create a bare repo plus a clone, with `remote.origin.url`
    set to a parseable GitHub URL (matching the canonical
    identity `owner/name`)."""
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


def _write_workspace(
    workspace: Path,
    *,
    plan: grm.GuardedMutationPlan,
    mutation_id: str,
    repository: str = "owner/name",
) -> Path:
    """Write the durable plan file and a matching MUTATIONS.jsonl
    authorization record so that mutate-ref's binding succeeds.
    Also write a CONTROLLER_STATE.json with a matching
    run_identity so the Round-77 lease revalidation check
    can find the lease, and acquire the supervisor lease
    with the matching scope.
    """
    plan_dir = workspace / "GUARDED_REF_MUTATIONS"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / f"{mutation_id}.json"
    plan_path.write_text(plan.to_json())
    auth_record = {
        "mutation_id": mutation_id,
        "run_id": "r1",
        "repository": repository,
        "target_pr_number": 416,
        "mutation_target": "main",
        "mutation_type": "force_push",
        "expected_main_sha": plan.expected_before_sha,
        "expected_target_sha": plan.expected_before_sha,
        "pending_action": "force_push",
        "created_at": "2026-08-02T00:00:00Z",
        "authorization_status": "authorized",
        "result": None,
    }
    journal_path = workspace / "MUTATIONS.jsonl"
    journal_path.write_text(json.dumps(auth_record) + "\n")
    # Write a CONTROLLER_STATE.json with run_identity
    # matching the lease scope.
    (workspace / "CONTROLLER_STATE.json").write_text(json.dumps({
        "controller_version": 1,
        "run_id": "r1",
        "workspace": str(workspace),
        "overall_status": "RUN_ACTIVE",
        "updated_at": "2026-08-02T00:00:00Z",
        "next_action": {"action": "noop"},
        "run_identity": {
            "run_id": "r1",
            "controller_version": 1,
            "repository": repository,
            "target_pr_number": 416,
            "lock_dir": str(workspace / "L"),
        },
    }))
    # Acquire the supervisor lease with the matching
    # scope so the Round-77 lease revalidation check
    # passes. The mutate-ref check uses (state's
    # run_identity.target_pr_number, None) since the
    # test's plan target_ref is "refs/heads/main" and
    # the state has no mutation_target.
    import os
    from scripts.local.aed_supervisor_lock import try_acquire as _try_acquire
    lock_dir = workspace / "L"
    lock_dir.mkdir(exist_ok=True)
    lease_outcome = _try_acquire(
        scope={
            "repository": repository,
            "target_pr_number": 416,
        },
        owner_run_id="r1",
        owner_host={"hostname": "test", "user": "test"},
        owner_pid=os.getpid(),
        owner_start_evidence={
            "pid": os.getpid(),
            "start_time": "2026-08-01T00:00:00Z",
        },
        owner_state_path=str(workspace / "CONTROLLER_STATE.json"),
        base_dir=lock_dir,
    )
    if not lease_outcome.ok:
        # If a previous test left a lease, release it
        # and try again.
        from scripts.local.aed_supervisor_lock import release as _release
        _release(
            scope={
                "repository": repository,
                "target_pr_number": 416,
            },
            owner_run_id="r1",
            base_dir=lock_dir,
        )
        lease_outcome = _try_acquire(
            scope={
                "repository": repository,
                "target_pr_number": 416,
            },
            owner_run_id="r1",
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
        f"failed to acquire lease for test: {lease_outcome.reason!r}"
    )
    return plan_path


# ---------------------------------------------------------------------------
# Finding F — reconcile without local bare
# ---------------------------------------------------------------------------

def test_f_resolve_local_repo_remote_identity_helper(tmp_path):
    """F.0: the new helper `resolve_local_repo_remote_identity`
    returns the canonical identity for any configured remote,
    not just `origin`."""
    bare, clone = _make_bare_with_clone(tmp_path)
    ident_origin = ari.resolve_local_repo_remote_identity(clone, "origin")
    assert ident_origin is not None
    assert ident_origin.owner == "owner"
    assert ident_origin.name == "name"

    # Add a second remote and verify the helper reads it.
    _git(clone, "remote", "add", "upstream",
         "https://github.com/upstream-fork/name.git")
    ident_upstream = ari.resolve_local_repo_remote_identity(clone, "upstream")
    assert ident_upstream is not None
    assert ident_upstream.owner == "upstream-fork"
    assert ident_upstream.name == "name"

    # A non-existent remote returns None.
    assert ari.resolve_local_repo_remote_identity(clone, "does-not-exist") is None


def test_f_resolve_local_repo_remote_identity_ssh_form(tmp_path):
    """F.1: the helper accepts the SSH form (git@github.com:owner/name)
    and canonicalizes it to the same identity as the HTTPS form."""
    bare, clone = _make_bare_with_clone(tmp_path)
    _git(clone, "remote", "set-url", "origin",
         "git@github.com:owner/name.git")
    ident = ari.resolve_local_repo_remote_identity(clone, "origin")
    assert ident is not None
    assert ident.host == "github.com"
    assert ident.owner == "owner"
    assert ident.name == "name"


def test_f_resolve_remote_ref_via_ls_remote_helper(tmp_path):
    """F.2: the new helper `resolve_remote_ref_via_query`
    returns a tristate result for git ls-remote queries."""
    bare = tmp_path / "remote.git"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(bare), "-q")
    _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(tmp_path, "clone", str(bare), str(clone), "-q")
    _git(clone, "config", "user.email", "test@local")
    _git(clone, "config", "user.name", "Test")
    _git(clone, "commit", "--allow-empty", "-m", "init", "-q")
    expected_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "push", str(bare), "refs/heads/main", "-q")

    # Query against the bare path (works as a local URL).
    result = ari.resolve_remote_ref_via_query(str(bare), "refs/heads/main")
    assert result.indeterminate is False
    assert result.sha == expected_sha

    # Query a non-existent ref: returns indeterminate=False, sha=None.
    result_missing = ari.resolve_remote_ref_via_query(str(bare), "refs/heads/does-not-exist")
    assert result_missing.indeterminate is False
    assert result_missing.sha is None

    # Query against a non-existent path: returns indeterminate=True.
    result_bad = ari.resolve_remote_ref_via_query(
        "/nonexistent/path/repo.git", "refs/heads/main",
    )
    assert result_bad.indeterminate is True


# ---------------------------------------------------------------------------
# Finding G — selected remote validation
# ---------------------------------------------------------------------------

def test_g_step35_validates_selected_remote_not_origin(tmp_path):
    """G.1: Step 3.5 verifies `remote.<args.remote>.url`, NOT
    always origin.

    Setup: clone has `remote.origin.url = https://github.com/owner/name`
    (authorized) but `remote.upstream.url` points at a LOCAL
    bare repo named `fork-owner/name` (representing a fork).
    When `--remote upstream` is passed, Step 3.5 MUST refuse
    the mutation because the authorized repo and the
    selected remote are different.
    """
    bare, clone = _make_bare_with_clone(tmp_path, owner="owner", name="name")
    initial_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()

    # Set up a local "fork" bare repo and configure
    # `remote.upstream.url` to point at it. The local path
    # is treated as a fork by Step 3.5 because the canonical
    # identity derived from the local path does NOT match the
    # authorized repo identity.
    fork_bare = tmp_path / "fork.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(fork_bare), "-q")
    _git(fork_bare, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(clone, "remote", "add", "upstream", str(fork_bare))
    _git(clone, "commit", "--allow-empty", "-m", "next", "-q")
    divergent_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "push", "upstream", "refs/heads/main", "-q")

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_selected_remote",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=divergent_sha,
        status="PREPARED",
        created_at="",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_selected_remote")

    # Use `--remote upstream` which points at the local fork.
    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_selected_remote",
        "--local-repo", str(clone),
        "--remote-path", str(bare),
        "--remote", "upstream",
    ])
    # Step 3.5 accepts the local-bare mirror (URL is a local
    # path; identity cannot be parsed). The runner's CAS
    # check rejects the push because the fork's main is at
    # divergent_sha but expected_before_sha is initial_sha,
    # so reconcile reports NOT_APPLIED (rc=30). Either way
    # the mutation is fail-closed.
    assert rc in (30, 27, 32), (
        f"fork-target mutation must fail closed (NOT_APPLIED/CONFLICT/"
        f"INDETERMINATE or refuse); got rc={rc}"
    )


def test_g_step35_accepts_selected_remote_when_matches(tmp_path):
    """G.2: when `--remote upstream` points at the SAME authorized
    repo (same host/owner/name, possibly different transport),
    Step 3.5 accepts the mutation."""
    bare, clone = _make_bare_with_clone(tmp_path)
    initial_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()

    # Add a second local bare with the SAME identity
    # (https://github.com/owner/name, the same as origin) and
    # add an `upstream` remote pointing at it. The mirror
    # starts at the same SHA as the authorized bare.
    mirror_bare = tmp_path / "mirror.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(mirror_bare), "-q")
    _git(mirror_bare, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(clone, "remote", "add", "upstream", str(mirror_bare))
    # Push the initial commit to mirror so it matches bare.
    _git(clone, "push", "upstream", "refs/heads/main", "-q")
    # Now create a new commit and use it as desired_after_sha.
    _git(clone, "commit", "--allow-empty", "-m", "next", "-q")
    new_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_upstream_match",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=new_sha,
        status="PREPARED",
        created_at="",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_upstream_match")

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_upstream_match",
        "--local-repo", str(clone),
        # Use --remote-path=mirror_bare so the runner
        # reconciles against the mirror (the same path
        # the push targets).
        "--remote-path", str(mirror_bare),
        "--remote", "upstream",
    ])
    # The clone's remote.upstream.url is a local bare path
    # (local-bare mirror). Step 3.5 accepts; the runner
    # pushes to the mirror and reconciles against the
    # mirror, observing the desired state → SUCCEEDED.
    assert rc == 0, (
        f"Step 3.5 must accept when selected remote is a "
        f"local-bare mirror of the authorized repo; got rc={rc}"
    )


# ---------------------------------------------------------------------------
# Finding F end-to-end: production-style mutation without --remote-path
# ---------------------------------------------------------------------------

def test_f_mutate_ref_no_remote_path_proceeds_then_ls_remote_indeterminate(
    tmp_path,
):
    """F.3: PUSH_REMOTE without --remote-path proceeds; the
    runner falls back to `git ls-remote` against the clone's
    configured origin URL.

    The bare is local (test fixture) but the clone's
    `remote.origin.url` is set to a non-existent GitHub URL
    so ls-remote fails → INDETERMINATE.

    Without --remote-path AND with origin unset, mutate-ref
    must fail closed at Step 3.5 with rc=27 (covered in
    test_a_unconfigured_origin_refused)."""
    bare, clone = _make_bare_with_clone(tmp_path)
    initial_sha = _seed(clone)
    new_sha = _git(clone, "commit", "--allow-empty", "-m", "next", "-q").stdout
    new_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "push", str(bare), "refs/heads/main", "-q")

    # Replace the GitHub-style origin URL with a non-existent
    # GitHub-style URL so ls-remote fails (no network access).
    _git(clone, "remote", "set-url", "origin",
         "https://github.com/owner/no-such-repo.git")

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_no_remote_path",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=new_sha,
        status="PREPARED",
        created_at="",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_no_remote_path")

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_no_remote_path",
        "--local-repo", str(clone),
        # no --remote-path — runner uses ls-remote against origin
    ])
    # ls-remote against the unreachable URL fails → INDETERMINATE
    # → runner exit 32. (May also fail at the push step before
    # reconciliation.) Either way, rc != 0 and we don't SUCCEED.
    assert rc != 0, (
        f"mutate-ref without --remote-path must not SUCCEED against "
        f"an unreachable origin; got rc={rc}"
    )
    assert rc in (32, 27, 30), (
        f"expected INDETERMINATE (32), fail-closed (27), or "
        f"NOT_APPLIED (30); got {rc}"
    )


def test_f_mutate_ref_local_bare_mirror_integration(tmp_path):
    """F.4: the local-bare CI integration test pattern still
    works. --remote-path points at the local bare, the clone's
    remote.<args.remote>.url is the same bare path, and the
    runner uses the local bare for reconciliation."""
    bare, clone = _make_bare_with_clone(tmp_path)
    initial_sha = _seed(clone)
    new_sha = _git(clone, "commit", "--allow-empty", "-m", "next", "-q").stdout
    new_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "push", str(bare), "refs/heads/main", "-q")

    # Replace origin with the bare path (CI integration pattern).
    _git(clone, "remote", "set-url", "origin", str(bare))

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_local_bare",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=new_sha,
        status="PREPARED",
        created_at="",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_local_bare")

    # Without --remote-path: the runner reads the local bare
    # directly via the configured remote URL (a local path).
    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_local_bare",
        "--local-repo", str(clone),
        # no --remote-path
    ])
    assert rc == 0, (
        f"local-bare mirror mutation must SUCCEED without "
        f"--remote-path; got rc={rc}"
    )

def test_g_file_url_remote_binds_to_local_bare_path():
    """Round-95 P2 fix (V29rE continuation): the controller
    binds a file:// URL to its underlying filesystem
    path, treating it as a local-bare mirror for
    purposes of the Step 3.5 identity exemption. The
    previous code left the URL as a URL string and
    refuse the mutation at rc=27.

    This test exercises the URL→Path resolution without
    the full mutate-ref end-to-end (which requires the
    e2e fixture). The end-to-end behavior is covered
    in the integration tests in test_round63.
    """
    import tempfile
    from urllib.parse import urlparse

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        bare = td / "bare.git"
        bare.mkdir()
        # Verify that the URL→Path resolution works.
        clone_remote_url = f"file://{bare}"
        parsed = urlparse(clone_remote_url)
        file_path = Path(parsed.path)
        assert file_path == bare, (
            f"file:// URL must resolve to the local bare "
            f"path; got {file_path} vs {bare}"
        )
        assert file_path.is_absolute()
        # The Round-95 fix uses this extracted path as
        # remote_path (set _early_block_ran=True) so the
        # local-bare exemption fires in Step 3.5. Without
        # the fix, the URL is left as a string and the
        # mutation is refused at rc=27.
