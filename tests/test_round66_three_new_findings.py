#!/usr/bin/env python3
"""
Round-66 regression tests for the three new Codex findings on commit 6e3dca6.

Verifies three findings addressed by the round-66 branch:

  O. PRRT_kwDOSHFpYM6VzuVB  Canonicalize repository identities before
     building lock keys. Two controllers identifying the same
     repository using different accepted forms
     (e.g. `owner/repo` vs `https://github.com/owner/repo.git`)
     must produce the SAME supervisor scope key.

  P. PRRT_kwDOSHFpYM6VzuVD  Refuse to reset an existing active run.
     A second `init` call with the same run_id targeting an
     active run must be rejected unless
     --replace-stale-state is set.

  Q. PRRT_kwDOSHFpYM6VzuVF  Preserve the plan creation timestamp
     during prepare. The plan's `created_at` must be set on
     the first prepare() call and preserved across
     subsequent calls (NOT overwritten).

Findings deferred to follow-up commits:

  L. PRRT_kwDOSHFpYM6VzXFA  Hold the repository sentinel
     through lease publication. (Documented in Round-62.)

  M. PRRT_kwDOSHFpYM6VzmEz  Hold the scope sentinel through
     authorization. (Documented in Round-64.)

  N. PRRT_kwDOSHFpYM6VzmE0  Enforce target exclusion before
     upgrading a PR authorization. (Documented in Round-64.)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.local import aed_run_identity as ari
from scripts.local import aed_supervisor_lock as sl


# ---------------------------------------------------------------------------
# Finding O — canonicalize repository identities before building lock keys
# ---------------------------------------------------------------------------

def test_o_lock_key_canonicalizes_different_repository_forms(tmp_path):
    """O.1: two scope keys built from different accepted forms
    of the same repository resolve to the same string."""
    monkeypatch_form_a = "owner/repo"
    monkeypatch_form_b = "https://github.com/owner/repo.git"
    monkeypatch_form_c = "git@github.com:owner/repo.git"

    key_a = sl.build_scope_key(
        repository=monkeypatch_form_a, target_pr_number=416,
    )
    key_b = sl.build_scope_key(
        repository=monkeypatch_form_b, target_pr_number=416,
    )
    key_c = sl.build_scope_key(
        repository=monkeypatch_form_c, target_pr_number=416,
    )
    assert key_a == key_b == key_c, (
        f"scope keys must be identical for the same repository "
        f"in different forms; got {key_a!r}, {key_b!r}, {key_c!r}"
    )
    assert "owner/repo" in key_a, (
        f"canonical key should contain owner/repo; got {key_a!r}"
    )


def test_o_lock_key_normalizes_case():
    """O.2: case differences in the repository name are
    normalized to the same scope key."""
    key_lower = sl.build_scope_key(
        repository="owner/repo", target_pr_number=416,
    )
    key_upper = sl.build_scope_key(
        repository="OWNER/Repo", target_pr_number=416,
    )
    assert key_lower == key_upper, (
        f"case differences must not produce different keys; "
        f"got {key_lower!r} vs {key_upper!r}"
    )


# ---------------------------------------------------------------------------
# Finding P — refuse to reset an existing active run
# ---------------------------------------------------------------------------

def test_p_init_rejects_existing_active_run(monkeypatch, tmp_path):
    """P.1: a second `init` call with the same run_id targeting
    an ACTIVE run (overall_status=RUN_ACTIVE) must be rejected
    unless --replace-stale-state is set.

    The supervisor lock is released only at finalize-run, so
    the test releases it manually between the two inits to
    exercise the artifact-ownership check (which is the
    path controlled by the Round-66 P1 fix)."""
    from scripts.local.autocoder_run_controller import main as controller_main
    from scripts.local import aed_supervisor_lock as sl

    # Isolate the supervisor lock directory.
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setenv("AED_LOCK_DIR", str(lock_dir))

    workspace = tmp_path / "ws"
    workspace.mkdir()
    tasks_jsonl = workspace / "TASKS.jsonl"
    tasks_jsonl.write_text(
        json.dumps({"task_id": "t1", "task_type": "docs_consistency",
                    "integration_order": 1, "depends_on": [], "blocks": []})
        + "\n"
    )

    common_args = [
        "init",
        "--run-id", "r1",
        "--tasks-jsonl", str(tasks_jsonl),
        "--workspace", str(workspace),
        "--output-state", str(workspace / "CONTROLLER_STATE.json"),
        "--integration-branch", "fix/test-branch",
        "--repository", "owner/name",
        "--target-pr-number", "416",
    ]
    # First init: succeeds.
    rc1 = controller_main(common_args)
    assert rc1 == 0, f"first init should succeed; got rc={rc1}"

    # Release the supervisor lock manually so the second init
    # reaches the artifact-ownership check.
    scope_key = sl.build_scope_key(
        repository="owner/name", target_pr_number=416,
    )
    lock_path = sl.lock_path_for(scope_key, base_dir=lock_dir)
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass
    # Also remove the .recovery-sentinel
    sentinel_path = lock_path.with_suffix(lock_path.suffix + ".recovery-sentinel")
    try:
        sentinel_path.unlink()
    except FileNotFoundError:
        pass
    # Also remove the .repo index
    repo_path = lock_path.with_suffix(lock_path.suffix + ".repo")
    try:
        repo_path.unlink()
    except FileNotFoundError:
        pass

    # Second init with the same run_id: the existing run is
    # now RUN_ACTIVE. Without --replace-stale-state, init
    # must reject.
    rc2 = controller_main(common_args)
    assert rc2 == 16, (
        f"second init of an ACTIVE run without --replace-stale-state "
        f"must be rejected with rc=16; got rc={rc2}"
    )


# ---------------------------------------------------------------------------
# Finding Q — preserve the plan creation timestamp during prepare
# ---------------------------------------------------------------------------

def test_q_prepare_preserves_creation_timestamp(tmp_path):
    """Q.1: the runner's prepare() must not overwrite an
    already-set `created_at` on the plan. The timestamp must
    be set on the first call and preserved across subsequent
    calls.
    """
    from scripts.local import guarded_ref_mutation as grm
    from scripts.local import guarded_ref_mutation_runner as runner

    workspace = tmp_path / "ws"
    workspace.mkdir()

    plan = grm.GuardedMutationPlan(
        mutation_id="m_q1",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha="a" * 40,
        desired_after_sha="b" * 40,
        status="PREPARED",
        created_at="2026-08-02T00:00:00Z",  # pre-set
    )
    orch = runner.GuardedMutationOrchestrator(workspace=workspace, plan=plan)
    orch.prepare()
    plan_after = orch.plan
    assert plan_after.created_at == "2026-08-02T00:00:00Z", (
        f"prepare() must NOT overwrite a pre-set created_at; "
        f"got {plan_after.created_at!r}"
    )

    # Second prepare() also must not change created_at.
    orch.prepare()
    assert orch.plan.created_at == "2026-08-02T00:00:00Z", (
        f"second prepare() must NOT change created_at; "
        f"got {orch.plan.created_at!r}"
    )


def test_q_prepare_sets_created_at_when_unset(tmp_path):
    """Q.2: the runner's prepare() must set created_at on
    the first call when the plan was created without a
    timestamp.
    """
    from scripts.local import guarded_ref_mutation as grm
    from scripts.local import guarded_ref_mutation_runner as runner

    workspace = tmp_path / "ws"
    workspace.mkdir()

    plan = grm.GuardedMutationPlan(
        mutation_id="m_q2",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha="a" * 40,
        desired_after_sha="b" * 40,
        status="PREPARED",
        created_at="",  # unset
    )
    orch = runner.GuardedMutationOrchestrator(workspace=workspace, plan=plan)
    orch.prepare()
    assert orch.plan.created_at, (
        f"prepare() must set created_at when unset; "
        f"got {orch.plan.created_at!r}"
    )