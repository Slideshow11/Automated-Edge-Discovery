#!/usr/bin/env python3
"""
Round-64 regression test for the P2 finding on commit f5114eb.

Verifies one finding addressed by the round-64 branch:

  N. PRRT_kwDOSHFpYM6VzmE1  Permit repeatable mutations after
     head changes. For repeatable mutation types (e.g.
     `pr_body_update`) the authorization must NOT reject a
     fresh authorization merely because the expected main
     or target SHA has changed since a prior SUCCESS. The
     unconditional `duplicate_authorization_with_drifted_heads`
     rejection contradicts the repeatable-mutation exception.

Findings deferred to follow-up commits:

  L. PRRT_kwDOSHFpYM6VzmEz  Hold the scope sentinel through
     authorization. The scope sentinel must be held through
     the journal append so a concurrent recover_stale cannot
     transfer the lease mid-call. The full fix requires
     wrapping `_authorize_mutation_locked` in a try/finally
     that holds the lease sentinel; that refactor is
     documented as a follow-up.

  M. PRRT_kwDOSHFpYM6VzmE0  Enforce target exclusion before
     upgrading a PR authorization. PR-to-target upgrades
     should acquire a target-level exclusion so two
     controllers cannot mutate the same branch. The full
     fix requires cross-scope conflict checks for the
     target scope; that refactor is documented as a
     follow-up.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.local import aed_mutation_authorization as auth_mod
from scripts.local.aed_mutation_authorization import (
    AUTHORIZED,
    AuthorizationRequest,
    authorize,
)


def _write_journal(workspace: Path, *records: dict) -> None:
    """Append records to the MUTATIONS.jsonl journal."""
    journal = workspace / "MUTATIONS.jsonl"
    with open(journal, "a") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def test_n_repeatable_mutation_succeeds_after_head_drift(tmp_path):
    """N.1: a repeatable mutation (e.g. `pr_body_update`) that
    previously SUCCEEDED can be re-authorized even when the
    expected main/target SHA has drifted.

    The previous code unconditionally rejected re-authorization
    with `duplicate_authorization_with_drifted_heads` when
    the prior record's heads did not match the new request's
    heads. For repeatable types this is the common case
    (new commits land between PR-body updates); the
    unconditional rejection defeats the repeatable-mutation
    exception.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()

    prior_main = "a" * 40
    new_main = "b" * 40
    prior_target = "c" * 40
    new_target = "d" * 40

    # Prior SUCCESS record for a repeatable mutation type
    # with the OLD heads.
    _write_journal(workspace, {
        "mutation_id": "m_prior",
        "run_id": "r1",
        "repository": "owner/name",
        "target_pr_number": 416,
        "mutation_target": "main",
        "mutation_type": "pr_body_update",
        "expected_main_sha": prior_main,
        "expected_target_sha": prior_target,
        "pending_action": "pr_body_update",
        "created_at": "2026-08-01T00:00:00Z",
        "authorization_status": AUTHORIZED,
        "result": {
            "status": "success",
            "recorded_at": "2026-08-01T00:00:00Z",
            "evidence": "first update succeeded",
            "actual_main_sha": prior_main,
            "actual_target_sha": prior_target,
            "error_detail": None,
        },
    })

    # New authorization request with the NEW heads (drift).
    req = AuthorizationRequest(
        run_id="r1",
        repository="owner/name",
        target_pr_number=416,
        mutation_target="main",
        mutation_type="pr_body_update",
        expected_main_sha=new_main,
        expected_target_sha=new_target,
        pending_action="pr_body_update",
    )
    outcome = authorize(workspace, req)
    assert outcome.ok, (
        f"repeatable mutation after head drift must be "
        f"re-authorizable; got reason={outcome.reason!r}"
    )
    assert outcome.mutation_id is not None
    assert outcome.mutation_id != "m_prior", (
        "a new mutation_id must be generated for the new "
        "authorization"
    )


def test_n_non_repeatable_mutation_still_rejected_after_drift(tmp_path):
    """N.2: a NON-repeatable mutation (e.g. `force_push`) that
    previously SUCCEEDED is STILL rejected with
    `duplicate_authorization_with_drifted_heads` when the
    heads drift. The Round-64 fix only relaxes the
    drift rejection for repeatable types.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()

    prior_main = "a" * 40
    new_main = "b" * 40

    _write_journal(workspace, {
        "mutation_id": "m_prior",
        "run_id": "r1",
        "repository": "owner/name",
        "target_pr_number": 416,
        "mutation_target": "main",
        "mutation_type": "force_push",
        "expected_main_sha": prior_main,
        "expected_target_sha": prior_main,
        "pending_action": "force_push",
        "created_at": "2026-08-01T00:00:00Z",
        "authorization_status": AUTHORIZED,
        "result": {
            "status": "success",
            "recorded_at": "2026-08-01T00:00:00Z",
            "evidence": "first push succeeded",
            "actual_main_sha": prior_main,
            "actual_target_sha": prior_main,
            "error_detail": None,
        },
    })

    req = AuthorizationRequest(
        run_id="r1",
        repository="owner/name",
        target_pr_number=416,
        mutation_target="main",
        mutation_type="force_push",
        expected_main_sha=new_main,
        expected_target_sha=new_main,
        pending_action="force_push",
    )
    outcome = authorize(workspace, req)
    assert not outcome.ok, (
        "non-repeatable mutation after head drift must still "
        "be rejected"
    )
    assert outcome.reason == "duplicate_authorization_with_drifted_heads", (
        f"non-repeatable drift rejection must use the original "
        f"reason; got {outcome.reason!r}"
    )