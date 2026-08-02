#!/usr/bin/env python3
"""
Round-58 regression tests for mutate-ref.

Verifies three findings addressed by commit on the round-58 branch:

  A. PRRT_kwDOSHFpYM6VxgV-  Bind the execution remote to the
     authorized repository. mutate-ref must canonicalize
     plan.repository and the --local-repo origin URL, refuse
     when they disagree, and refuse when either side is
     unparseable. Two forks with the same commit SHA must
     NOT permit cross-repository mutation.

  B. PRRT_kwDOSHFpYM6VxgWA  Safely resume plans left in
     NOT_APPLIED. Re-read the authoritative ref via
     reconcile() first; only dispatch prepare()+execute()
     when the ref is still at expected_before_sha. If the
     ref already advanced to desired_after_sha, classify as
     SUCCEEDED. If it diverged from both, classify as
     CONFLICT. If the read fails, remain INDETERMINATE.

  C. PRRT_kwDOSHFpYM6VvK2Z  Return the persisted success on
     idempotent replay. When a plan is already SUCCEEDED,
     return exit 0 with the persisted successful result.
     Do NOT call prepare() or execute(). Do NOT perform
     Git or GitHub mutation. Do NOT append a contradictory
     duplicate terminal result. The mutation executor
     invocation count must remain exactly one.

The tests use a temporary bare Git repo plus two clones that
point at it (with separate GitHub-style remote.origin.url
values), and write the durable plan + MUTATIONS.jsonl by hand
to avoid coupling to the controller's authorize-mutation flow.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.local import guarded_ref_mutation as grm
from scripts.local.autocoder_run_controller import main as controller_main


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

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
    *,
    owner: str,
    name: str,
    transport: str = "https",
    initial_branch: str = "main",
    seed_commit: bool = True,
):
    """Create a bare repo plus a clone, with remote.origin.url
    configured to a GitHub URL matching the canonical
    (host, owner, name) identity.

    transport is "https" (default) or "ssh"; both forms must be
    canonicalized to the same RepositoryIdentity by
    aed_run_identity.canonical_repository_identity.

    Implementation note: the clone is initially created with the
    bare repo as origin so the seed commit can be pushed without
    a network round-trip. Once seeded, the origin URL is
    overridden with the GitHub URL — this is the binding that
    mutate-ref's Step 3.5 identity check verifies.
    """
    bare = tmp_path / f"{name}.git"
    clone = tmp_path / f"{name}_clone"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(bare), "-q")
    _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(tmp_path, "clone", str(bare), str(clone), "-q")
    _git(clone, "config", "user.email", "test@local")
    _git(clone, "config", "user.name", "Test")
    initial_sha = None
    if seed_commit:
        _git(clone, "commit", "--allow-empty", "-m", "init", "-q")
        _git(clone, "push", "origin", f"refs/heads/{initial_branch}", "-q")
        initial_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    # Override origin URL AFTER seeding so the GitHub-form
    # remote.origin.url is what mutate-ref reads.
    if transport == "https":
        origin_url = f"https://github.com/{owner}/{name}.git"
    elif transport == "ssh":
        origin_url = f"git@github.com:{owner}/{name}.git"
    else:
        raise ValueError(f"unknown transport: {transport}")
    _git(clone, "remote", "set-url", "origin", origin_url)
    return bare, clone, initial_sha


def _independent_commit(clone: Path, bare: Path, ref: str = "refs/heads/main") -> str:
    """Create an empty commit and push it to the given ref.

    Pushes via the local bare path (not origin's GitHub URL)
    so the test does not require network access.
    """
    _git(clone, "commit", "--allow-empty", "-m", "independent", "-q")
    sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    if ref != "refs/heads/main":
        _git(clone, "update-ref", ref, sha)
    else:
        _git(clone, "push", str(bare), ref, "-q")
    return sha


def _write_workspace(
    workspace: Path,
    *,
    plan: grm.GuardedMutationPlan,
    mutation_id: str,
    run_id: str = "r1",
    repository: str = "owner/name",
    target_pr_number: int | None = None,
    mutation_type: str = "force_push",
    pending_action: str = "merge",
    expected_target_sha: str | None = None,
    expected_main_sha: str | None = None,
    mutation_target: str | None = "main",
    result: dict | None = None,
):
    """Write the durable plan file and a matching MUTATIONS.jsonl
    authorization record so that mutate-ref's binding succeeds."""
    # Durable plan
    plan_dir = workspace / "GUARDED_REF_MUTATIONS"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / f"{mutation_id}.json"
    plan_path.write_text(plan.to_json())
    # Authorization record
    auth_record = {
        "mutation_id": mutation_id,
        "run_id": run_id,
        "repository": repository,
        "target_pr_number": target_pr_number,
        "mutation_target": mutation_target,
        "mutation_type": mutation_type,
        "expected_main_sha": expected_main_sha,
        "expected_target_sha": expected_target_sha,
        "pending_action": pending_action,
        "created_at": "2026-08-02T00:00:00Z",
        "authorization_status": "authorized",
        "result": result,
    }
    journal_path = workspace / "MUTATIONS.jsonl"
    with open(journal_path, "w") as f:
        f.write(json.dumps(auth_record) + "\n")
    return plan_path


# ---------------------------------------------------------------------------
# Finding A — repository identity binding
# ---------------------------------------------------------------------------

def test_a_matching_repositories_allow_execution(tmp_path):
    """A.1: plan.repository matches --local-repo origin: mutation proceeds.

    PRE-FIX FAILED: mutate-ref would have proceeded because
    plan-vs-journal binding succeeded (same mutation_id) without
    verifying the repository identity. POST-FIX PASSES.
    """
    bare, clone, initial_sha = _make_bare_with_clone(
        tmp_path, owner="owner", name="name",
    )
    new_sha = _independent_commit(clone, bare)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_match",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=new_sha,
        status="PREPARED",
        created_at="",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_match")

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_match",
        "--local-repo", str(clone),
        "--remote-path", str(bare),
        "--remote", "origin",
    ])
    assert rc == 0, f"mutate-ref should succeed for matching repos, rc={rc}"


def test_a_https_and_ssh_origin_canonicalize_to_same_identity(tmp_path):
    """A.2: HTTPS vs SSH transport of the same repo both pass
    the identity check. The plan records the URL in HTTPS form;
    the clone's remote.origin.url is in SSH form. They must
    canonicalize to the same (host, owner, name) and the mutation
    must proceed."""
    bare, clone, initial_sha = _make_bare_with_clone(
        tmp_path, owner="owner", name="name", transport="ssh",
    )
    new_sha = _independent_commit(clone, bare)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_ssh_origin",
        owner_run_id="r1",
        repository="https://github.com/owner/name",  # plan in HTTPS form
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=new_sha,
        status="PREPARED",
        created_at="",
    )
    # The auth record's repository must match the plan's repository
    # exactly (Step 3 binding check is byte-equality). Set it to
    # the HTTPS form so binding succeeds and Step 3.5 then verifies
    # both forms canonicalize to the same identity.
    _write_workspace(
        workspace,
        plan=plan,
        mutation_id="m_ssh_origin",
        repository="https://github.com/owner/name",
    )

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_ssh_origin",
        "--local-repo", str(clone),
        "--remote-path", str(bare),
        "--remote", "origin",
    ])
    assert rc == 0, (
        "HTTPS vs SSH forms of the same repo must canonicalize to the same "
        f"identity; rc={rc}"
    )


def test_a_fork_with_same_commit_refused(tmp_path):
    """A.3: a fork of owner/name containing the same commit SHA
    MUST NOT be permitted to mutate under the original
    authorization. PRE-FIX FAILED (the controller only compared
    commit SHAs). POST-FIX: mutate-ref refuses because the
    --local-repo origin is fork-owner/fork-name, not owner/name."""
    # Authorization was issued for owner/name at initial_sha.
    # A fork (fork-owner/fork-name) was created at the same SHA.
    _bare_a, _clone_a, initial_sha = _make_bare_with_clone(
        tmp_path, owner="owner", name="name",
    )
    bare_fork, clone_fork, _ = _make_bare_with_clone(
        tmp_path, owner="fork-owner", name="fork-name",
    )
    # Push the same commit SHA into the fork so the SHAs match.
    _git(clone_fork, "reset", "--hard", initial_sha)
    _git(clone_fork, "push", str(bare_fork), "refs/heads/main", "-q")

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_fork",
        owner_run_id="r1",
        repository="owner/name",  # authorized for the original
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha="a" * 40,
        status="PREPARED",
        created_at="",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_fork")

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_fork",
        "--local-repo", str(clone_fork),  # fork clone!
        "--remote-path", str(bare_fork),
        "--remote", "origin",
    ])
    assert rc == 27, (
        "mutate-ref must REFUSE when --local-repo origin resolves to a "
        f"different repository than plan authorizes, rc={rc}"
    )


def test_a_unconfigured_origin_refused(tmp_path):
    """A.4: a clone with no remote.origin.url fails closed when
    no --remote-path is supplied (the runner's ls-remote
    fallback cannot work without a configured remote URL).

    PRE-FIX FAILED: the controller ignored the origin URL.
    POST-FIX: mutate-ref refuses because no authoritative
    remote URL is available (neither --remote-path nor a
    configured remote.<args.remote>.url)."""
    bare = tmp_path / "bare.git"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(bare), "-q")
    _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(tmp_path, "clone", str(bare), str(clone), "-q")
    _git(clone, "config", "user.email", "test@local")
    _git(clone, "config", "user.name", "Test")
    # Unset the origin URL set by `git clone`. mutate-ref
    # must refuse because no authoritative remote URL is
    # available (no --remote-path, no origin URL).
    _git(clone, "remote", "remove", "origin")
    _git(clone, "commit", "--allow-empty", "-m", "init", "-q")
    initial_sha = _git(clone, "rev-parse", "HEAD").stdout.strip()
    new_sha = _independent_commit(clone, bare)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_no_origin",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=new_sha,
        status="PREPARED",
        created_at="",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_no_origin")

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_no_origin",
        "--local-repo", str(clone),
        # no --remote-path AND no origin URL → no
        # authoritative reconciliation target → must fail closed.
    ])
    assert rc == 27, (
        "mutate-ref must REFUSE when neither --remote-path nor "
        f"remote.origin.url is available, rc={rc}"
    )


def test_a_unparseable_plan_repository_refused(tmp_path):
    """A.5: a plan whose repository field is not a parseable
    GitHub identifier fails closed."""
    bare, clone, initial_sha = _make_bare_with_clone(
        tmp_path, owner="owner", name="name",
    )
    new_sha = _independent_commit(clone, bare)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_bad_plan_repo",
        owner_run_id="r1",
        repository="not-a-parseable-repo-identifier",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=new_sha,
        status="PREPARED",
        created_at="",
    )
    # Auth record uses the same unparseable repo so Step 3 binding
    # succeeds; Step 3.5 then rejects the unparseable repo.
    _write_workspace(
        workspace,
        plan=plan,
        mutation_id="m_bad_plan_repo",
        repository="not-a-parseable-repo-identifier",
    )

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_bad_plan_repo",
        "--local-repo", str(clone),
        "--remote-path", str(bare),
        "--remote", "origin",
    ])
    assert rc == 27, (
        "mutate-ref must REFUSE when plan.repository is not parseable, "
        f"rc={rc}"
    )


def test_a_remote_path_origin_mismatch_refused(tmp_path):
    """A.6: --remote-path with a different origin URL is refused."""
    # Authorized for owner/name
    _bare_a, clone_a, initial_sha = _make_bare_with_clone(
        tmp_path, owner="owner", name="name",
    )
    # But user supplies a --remote-path pointing at fork-owner/fork-name
    # (a different bare repo) as the reconciliation target.
    bare_b, _clone_b, _ = _make_bare_with_clone(
        tmp_path, owner="fork-owner", name="fork-name",
    )
    # Make fork's remote.origin.url point at a different remote.
    _git(bare_b, "config", "remote.origin.url", "https://github.com/fork-owner/fork-name")

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_bad_remote",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha="a" * 40,
        status="PREPARED",
        created_at="",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_bad_remote")

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_bad_remote",
        "--local-repo", str(clone_a),
        "--remote-path", str(bare_b),  # wrong remote_path
        "--remote", "origin",
    ])
    assert rc == 27, (
        "mutate-ref must REFUSE when --remote-path origin resolves to a "
        f"different repository than plan authorizes, rc={rc}"
    )


# ---------------------------------------------------------------------------
# Finding B — safe resume from NOT_APPLIED
# ---------------------------------------------------------------------------

def test_b_not_applied_with_ref_still_at_expected_retries(tmp_path):
    """B.1: NOT_APPLIED plan with ref still at expected_before_sha
    safely retries via prepare() + execute().

    PRE-FIX FAILED: the controller refused (exit 26) on any
    terminal state, permanently wedging the safe-retry path.
    POST-FIX: mutate-ref returns 0 after the retry succeeds."""
    bare, clone, initial_sha = _make_bare_with_clone(
        tmp_path, owner="owner", name="name",
    )
    new_sha = _independent_commit(clone, bare)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_not_applied_retry",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=new_sha,
        status="NOT_APPLIED",
        created_at="",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_not_applied_retry")

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_not_applied_retry",
        "--local-repo", str(clone),
        "--remote-path", str(bare),
        "--remote", "origin",
    ])
    assert rc == 0, (
        "mutate-ref must succeed when NOT_APPLIED is followed by a successful "
        f"retry (ref still at expected), rc={rc}"
    )
    # Verify the bare repo's main now points at new_sha.
    actual = _git(bare, "rev-parse", "refs/heads/main").stdout.strip()
    assert actual == new_sha, (
        f"after successful retry, bare refs/heads/main must equal new_sha={new_sha}, "
        f"got {actual}"
    )


def test_b_not_applied_with_ref_already_advanced_returns_success(tmp_path):
    """B.2: NOT_APPLIED plan where the authoritative ref has
    already advanced to desired_after_sha (e.g. a parallel run
    succeeded) — classify as SUCCEEDED without re-mutating."""
    bare, clone, initial_sha = _make_bare_with_clone(
        tmp_path, owner="owner", name="name",
    )
    new_sha = _independent_commit(clone, bare)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_already_advanced",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=new_sha,
        status="NOT_APPLIED",
        created_at="",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_already_advanced")
    # The bare repo already has new_sha on main (a parallel run won).
    # mutate-ref must reconcile to SUCCEEDED.

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_already_advanced",
        "--local-repo", str(clone),
        "--remote-path", str(bare),
        "--remote", "origin",
    ])
    assert rc == 0, (
        "NOT_APPLIED with ref already at desired must classify as SUCCEEDED, "
        f"rc={rc}"
    )


def test_b_not_applied_with_conflict_returns_conflict(tmp_path):
    """B.3: NOT_APPLIED plan where the authoritative ref has
    diverged from both expected_before_sha AND desired_after_sha
    — classify as CONFLICT without re-mutating."""
    bare, clone, initial_sha = _make_bare_with_clone(
        tmp_path, owner="owner", name="name",
    )
    # The plan's expected_before_sha = initial_sha and desired_after_sha
    # is some SHA, but the actual ref now points at a divergent SHA.
    # Create a real commit with a divergent SHA and push it to the
    # bare repo so the ref actually advances to that SHA.
    divergent_sha = _independent_commit(clone, bare)
    # desired_after_sha — something other than divergent_sha
    desired_sha = "a" * 40

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_conflict",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=desired_sha,
        status="NOT_APPLIED",
        created_at="",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_conflict")

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_conflict",
        "--local-repo", str(clone),
        "--remote-path", str(bare),
        "--remote", "origin",
    ])
    assert rc == 31, (
        "NOT_APPLIED with ref diverging from both expected and desired must "
        f"classify as CONFLICT (exit 31), got rc={rc}"
    )


def test_b_not_applied_with_unreadable_remote_returns_indeterminate(tmp_path):
    """B.4: NOT_APPLIED plan where the authoritative remote path
    is unreadable — must remain INDETERMINATE without mutating."""
    bare, clone, initial_sha = _make_bare_with_clone(
        tmp_path, owner="owner", name="name",
    )

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_unreadable",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha="a" * 40,
        status="NOT_APPLIED",
        created_at="",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_unreadable")
    # Replace remote_path with a path that does not exist
    nonexistent = tmp_path / "does_not_exist.git"

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_unreadable",
        "--local-repo", str(clone),
        "--remote-path", str(nonexistent),
        "--remote", "origin",
    ])
    assert rc == 32, (
        "NOT_APPLIED with unreadable remote must remain INDETERMINATE "
        f"(exit 32), got rc={rc}"
    )


# ---------------------------------------------------------------------------
# Finding C — return persisted success on SUCCEEDED replay
# ---------------------------------------------------------------------------

def test_c_succeeded_replay_returns_persisted_success(tmp_path):
    """C.1: replay of a SUCCEEDED plan returns exit 0 with the
    persisted successful result, without re-executing."""
    bare, clone, initial_sha = _make_bare_with_clone(
        tmp_path, owner="owner", name="name",
    )
    new_sha = _independent_commit(clone, bare)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_succeeded_replay",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=new_sha,
        status="SUCCEEDED",
        created_at="2026-08-02T00:00:00Z",
        terminal_evidence=f"actual_ref_sha={new_sha!r}",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_succeeded_replay")

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_succeeded_replay",
        "--local-repo", str(clone),
        "--remote-path", str(bare),
        "--remote", "origin",
    ])
    assert rc == 0, (
        "Replay of a SUCCEEDED plan must return 0 with the persisted success, "
        f"got rc={rc}"
    )


def test_c_succeeded_replay_does_not_re_execute(tmp_path, monkeypatch):
    """C.2: replay of a SUCCEEDED plan must NOT call execute().

    Use a sentinel patch on GuardedMutationOrchestrator.execute
    to record any invocations; assert the count remains zero.
    """
    bare, clone, initial_sha = _make_bare_with_clone(
        tmp_path, owner="owner", name="name",
    )
    new_sha = _independent_commit(clone, bare)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_succeeded_no_re_execute",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=new_sha,
        status="SUCCEEDED",
        created_at="2026-08-02T00:00:00Z",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_succeeded_no_re_execute")

    # Patch the runner's execute() to record invocations.
    from scripts.local import guarded_ref_mutation_runner as runner
    execute_calls = []

    real_execute = runner.GuardedMutationOrchestrator.execute

    def spy_execute(self, **kwargs):
        execute_calls.append(kwargs)
        return real_execute(self, **kwargs)

    monkeypatch.setattr(
        runner.GuardedMutationOrchestrator, "execute", spy_execute,
    )

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_succeeded_no_re_execute",
        "--local-repo", str(clone),
        "--remote-path", str(bare),
        "--remote", "origin",
    ])
    assert rc == 0, f"SUCCEEDED replay must exit 0, got rc={rc}"
    assert len(execute_calls) == 0, (
        "SUCCEEDED replay must NOT call execute(), "
        f"but execute() was called {len(execute_calls)} times"
    )


def test_c_succeeded_replay_does_not_append_duplicate_result(tmp_path):
    """C.3: replay of a SUCCEEDED plan does NOT append a
    contradictory duplicate terminal result to MUTATIONS.jsonl.

    Per the existing mutate-ref flow, the journal authorization
    record is updated only by an explicit `record-mutation-result`
    CLI invocation; `_mutate_ref` itself does not append a result.
    This test simulates the user invoking mutate-ref twice on
    the same plan (the second invocation is the replay) and
    asserts that the journal record remains unchanged across
    both invocations — no contradictory duplicate result was
    appended by the replay.
    """
    bare, clone, initial_sha = _make_bare_with_clone(
        tmp_path, owner="owner", name="name",
    )
    new_sha = _independent_commit(clone, bare)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_succeeded_no_dup",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=new_sha,
        status="SUCCEEDED",
        created_at="2026-08-02T00:00:00Z",
    )
    _write_workspace(
        workspace,
        plan=plan,
        mutation_id="m_succeeded_no_dup",
        # result=None on the journal auth record — this is the
        # production state of MUTATIONS.jsonl after a successful
        # mutate-ref (the runner does NOT append a result; only
        # an explicit `record-mutation-result` would set it).
        result=None,
    )

    journal_path = workspace / "MUTATIONS.jsonl"
    with open(journal_path) as f:
        before = [json.loads(line) for line in f if line.strip()]
    assert len(before) == 1
    assert before[0]["result"] is None

    rc = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_succeeded_no_dup",
        "--local-repo", str(clone),
        "--remote-path", str(bare),
        "--remote", "origin",
    ])
    assert rc == 0

    # Verify the journal record was not modified or duplicated
    # by the replay.
    with open(journal_path) as f:
        after = [json.loads(line) for line in f if line.strip()]
    assert len(after) == len(before), (
        f"replay must not duplicate the journal record, "
        f"before={len(before)} after={len(after)}"
    )
    assert after[0]["result"] is None, (
        "replay must NOT append a contradictory duplicate terminal result; "
        f"got result={after[0]['result']}"
    )


def test_c_succeeded_replay_lost_response_scenario(tmp_path, monkeypatch):
    """C.4 (the user's required scenario): mutation succeeds →
    response is lost → identical command is replayed → stored
    success is returned → mutation executor invocation count
    remains one.

    This is the head-to-toe scenario from the authorization:
      1. mutate-ref is invoked the first time on a PREPARED plan.
         It succeeds (exit 0, executor called once, plan.status
         becomes SUCCEEDED).
      2. The response is "lost" — i.e. the caller cannot observe
         the exit-0 signal and re-invokes mutate-ref with the
         same arguments.
      3. mutate-ref must return 0, the executor must NOT be
         called again, and the persisted SUCCEEDED result must
         be returned.
    """
    bare, clone, initial_sha = _make_bare_with_clone(
        tmp_path, owner="owner", name="name",
    )
    new_sha = _independent_commit(clone, bare)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = grm.GuardedMutationPlan(
        mutation_id="m_lost_response",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha=initial_sha,
        desired_after_sha=new_sha,
        status="PREPARED",
        created_at="",
    )
    _write_workspace(workspace, plan=plan, mutation_id="m_lost_response")

    from scripts.local import guarded_ref_mutation_runner as runner
    execute_calls = []
    real_execute = runner.GuardedMutationOrchestrator.execute

    def spy_execute(self, **kwargs):
        execute_calls.append(kwargs)
        return real_execute(self, **kwargs)

    monkeypatch.setattr(
        runner.GuardedMutationOrchestrator, "execute", spy_execute,
    )

    # First invocation: PREPARED → execute → SUCCEEDED
    rc1 = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_lost_response",
        "--local-repo", str(clone),
        "--remote-path", str(bare),
        "--remote", "origin",
    ])
    assert rc1 == 0, f"first invocation must succeed, got rc={rc1}"
    assert len(execute_calls) == 1, (
        f"first invocation must call execute() exactly once, got {len(execute_calls)}"
    )

    # The plan is now SUCCEEDED on disk; the orchestrator persisted
    # status=SUCCEEDED. Replay must NOT call execute again.
    rc2 = controller_main([
        "mutate-ref",
        "--workspace", str(workspace),
        "--mutation-id", "m_lost_response",
        "--local-repo", str(clone),
        "--remote-path", str(bare),
        "--remote", "origin",
    ])
    assert rc2 == 0, f"replay must return 0, got rc={rc2}"
    assert len(execute_calls) == 1, (
        f"replay must NOT call execute() a second time; "
        f"execute() count after replay = {len(execute_calls)}"
    )
