"""Focused unit tests for scripts/local/guarded_ref_mutation.py.

Pure-data tests. No subprocess, no filesystem mutations beyond
the test-friendly `guarded_ref_mutation_plan_path` (no actual
writes). No controller integration. No Git or GitHub calls.

Covers:
  - Lifecycle state constants and uniqueness.
  - Allowed transitions (forward only, terminal absorbing).
  - Plan validation (per-operation rules, SHA format, ref name).
  - Reconcile rules (success, not_applied, conflict, indeterminate).
  - Round-trip JSON serialization.
  - Edge cases: empty SHAs, identical expected/desired, invalid
    ref names, unknown operations, etc.
"""

from __future__ import annotations

import pytest

from scripts.local import guarded_ref_mutation as grm


# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------

def test_all_seven_states_present():
    expected = {
        "PREPARED",
        "EXECUTING",
        "RECONCILING",
        "SUCCEEDED",
        "NOT_APPLIED",
        "CONFLICT",
        "INDETERMINATE",
    }
    actual = {s.value for s in grm.LifecycleState}
    assert actual == expected
    assert len(grm.LifecycleState) == 7


def test_states_are_unique():
    assert len(grm.LifecycleState) == len({s.value for s in grm.LifecycleState})


def test_terminal_states_are_conflict_and_succeeded():
    assert grm.TERMINAL_STATES == frozenset(
        {grm.LifecycleState.CONFLICT, grm.LifecycleState.SUCCEEDED}
    )


# ---------------------------------------------------------------------------
# Allowed transitions
# ---------------------------------------------------------------------------

def test_forward_path_allowed():
    """PREPARED -> EXECUTING -> RECONCILING -> SUCCEEDED"""
    assert grm.is_allowed_transition(
        grm.LifecycleState.PREPARED, grm.LifecycleState.EXECUTING
    )
    assert grm.is_allowed_transition(
        grm.LifecycleState.EXECUTING, grm.LifecycleState.RECONCILING
    )
    assert grm.is_allowed_transition(
        grm.LifecycleState.RECONCILING, grm.LifecycleState.SUCCEEDED
    )


def test_reconciling_to_other_outcomes():
    """RECONCILING can resolve to any of the four outcomes."""
    for target in (
        grm.LifecycleState.SUCCEEDED,
        grm.LifecycleState.NOT_APPLIED,
        grm.LifecycleState.CONFLICT,
        grm.LifecycleState.INDETERMINATE,
    ):
        assert grm.is_allowed_transition(
            grm.LifecycleState.RECONCILING, target
        ), f"RECONCILING -> {target.value} should be allowed"


def test_indeterminate_can_re_enter_reconciling():
    """INDETERMINATE -> RECONCILING is allowed (reconcile retry)."""
    assert grm.is_allowed_transition(
        grm.LifecycleState.INDETERMINATE, grm.LifecycleState.RECONCILING
    )


def test_not_applied_can_return_to_prepared():
    """NOT_APPLIED -> PREPARED is allowed (safe retry;
    expected_before_sha still matches)."""
    assert grm.is_allowed_transition(
        grm.LifecycleState.NOT_APPLIED, grm.LifecycleState.PREPARED
    )


def test_terminal_states_have_no_outgoing_transitions():
    for terminal in grm.TERMINAL_STATES:
        for target in grm.LifecycleState:
            assert not grm.is_allowed_transition(terminal, target), (
                f"{terminal.value} is terminal; cannot transition to "
                f"{target.value}"
            )


def test_self_loops_are_not_allowed():
    for state in grm.LifecycleState:
        assert not grm.is_allowed_transition(state, state)


def test_cannot_skip_states():
    """PREPARED -> RECONCILING is forbidden (must go through EXECUTING)."""
    assert not grm.is_allowed_transition(
        grm.LifecycleState.PREPARED, grm.LifecycleState.RECONCILING
    )
    assert not grm.is_allowed_transition(
        grm.LifecycleState.PREPARED, grm.LifecycleState.SUCCEEDED
    )
    assert not grm.is_allowed_transition(
        grm.LifecycleState.EXECUTING, grm.LifecycleState.SUCCEEDED
    )


def test_assert_allowed_transition_raises_for_invalid():
    with pytest.raises(grm.LifecycleError):
        grm.assert_allowed_transition(
            grm.LifecycleState.PREPARED, grm.LifecycleState.SUCCEEDED
        )
    with pytest.raises(grm.LifecycleError):
        grm.assert_allowed_transition(
            grm.LifecycleState.CONFLICT, grm.LifecycleState.PREPARED
        )


def test_assert_allowed_transition_passes_for_valid():
    grm.assert_allowed_transition(
        grm.LifecycleState.PREPARED, grm.LifecycleState.EXECUTING
    )
    grm.assert_allowed_transition(
        grm.LifecycleState.NOT_APPLIED, grm.LifecycleState.PREPARED
    )


# ---------------------------------------------------------------------------
# SHA and ref-name validation
# ---------------------------------------------------------------------------

def test_is_full_sha_accepts_40_lowercase_hex():
    assert grm.is_full_sha("0" * 40)
    assert grm.is_full_sha("a" * 40)
    assert grm.is_full_sha("0123456789abcdef0123456789abcdef01234567")


def test_is_full_sha_rejects_wrong_length():
    assert not grm.is_full_sha("0" * 39)
    assert not grm.is_full_sha("0" * 41)
    assert not grm.is_full_sha("")
    assert not grm.is_full_sha("abc")


def test_is_full_sha_rejects_uppercase():
    assert not grm.is_full_sha("A" * 40)


def test_is_full_sha_rejects_non_string():
    assert not grm.is_full_sha(None)
    assert not grm.is_full_sha(42)


def test_is_valid_ref_name_accepts_typical():
    assert grm.is_valid_ref_name("refs/heads/main")
    assert grm.is_valid_ref_name("refs/heads/feat/x")
    assert grm.is_valid_ref_name("refs/tags/v1.0.0")
    assert grm.is_valid_ref_name("HEAD")
    assert grm.is_valid_ref_name("main")


def test_is_valid_ref_name_rejects_empty():
    assert not grm.is_valid_ref_name("")
    assert not grm.is_valid_ref_name(None)


def test_is_valid_ref_name_rejects_control_chars():
    assert not grm.is_valid_ref_name("refs/heads/\x00")
    assert not grm.is_valid_ref_name("refs/heads/\n")


def test_is_valid_ref_name_rejects_leading_dash():
    assert not grm.is_valid_ref_name("-foo")


# ---------------------------------------------------------------------------
# Plan validation
# ---------------------------------------------------------------------------

def _valid_push_plan():
    return grm.GuardedMutationPlan(
        mutation_id="m1",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        operation="PUSH_REMOTE",
        expected_before_sha="0" * 40,
        desired_after_sha="a" * 40,
        status="PREPARED",
        created_at="2026-08-01T00:00:00Z",
    )


def _valid_create_plan():
    return grm.GuardedMutationPlan(
        mutation_id="m2",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/feat/new",
        operation="CREATE_LOCAL",
        expected_before_sha=None,
        desired_after_sha="b" * 40,
        status="PREPARED",
        created_at="2026-08-01T00:00:00Z",
    )


def _valid_delete_plan():
    return grm.GuardedMutationPlan(
        mutation_id="m3",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/feat/old",
        operation="DELETE_LOCAL",
        expected_before_sha="c" * 40,
        desired_after_sha=None,
        status="PREPARED",
        created_at="2026-08-01T00:00:00Z",
    )


def test_validate_accepts_valid_push_plan():
    grm.validate_plan(_valid_push_plan())


def test_validate_accepts_valid_create_plan():
    grm.validate_plan(_valid_create_plan())


def test_validate_accepts_valid_delete_plan():
    grm.validate_plan(_valid_delete_plan())


def test_validate_rejects_empty_mutation_id():
    p = _valid_push_plan()
    p.mutation_id = ""
    with pytest.raises(grm.PlanValidationError):
        grm.validate_plan(p)


def test_validate_rejects_empty_owner_run_id():
    p = _valid_push_plan()
    p.owner_run_id = ""
    with pytest.raises(grm.PlanValidationError):
        grm.validate_plan(p)


def test_validate_rejects_invalid_target_ref():
    p = _valid_push_plan()
    p.target_ref = ""
    with pytest.raises(grm.PlanValidationError):
        grm.validate_plan(p)
    p.target_ref = "refs/heads/\x00"
    with pytest.raises(grm.PlanValidationError):
        grm.validate_plan(p)


def test_validate_rejects_invalid_sha():
    p = _valid_push_plan()
    p.expected_before_sha = "abc"
    with pytest.raises(grm.PlanValidationError):
        grm.validate_plan(p)
    p.expected_before_sha = "0" * 40
    p.desired_after_sha = "XYZ"
    with pytest.raises(grm.PlanValidationError):
        grm.validate_plan(p)


def test_validate_rejects_unknown_operation():
    p = _valid_push_plan()
    p.operation = "FAKE_OP"
    with pytest.raises(grm.PlanValidationError):
        grm.validate_plan(p)


def test_validate_rejects_unknown_status():
    p = _valid_push_plan()
    p.status = "FAKE_STATE"
    with pytest.raises(grm.PlanValidationError):
        grm.validate_plan(p)


def test_validate_create_requires_empty_expected_before():
    p = _valid_create_plan()
    p.expected_before_sha = "0" * 40
    with pytest.raises(grm.PlanValidationError):
        grm.validate_plan(p)


def test_validate_create_requires_desired_after_sha():
    p = _valid_create_plan()
    p.desired_after_sha = None
    with pytest.raises(grm.PlanValidationError):
        grm.validate_plan(p)
    p.desired_after_sha = "abc"
    with pytest.raises(grm.PlanValidationError):
        grm.validate_plan(p)


def test_validate_delete_requires_expected_before_sha():
    p = _valid_delete_plan()
    p.expected_before_sha = ""
    with pytest.raises(grm.PlanValidationError):
        grm.validate_plan(p)


def test_validate_delete_forbids_desired_after_sha():
    p = _valid_delete_plan()
    p.desired_after_sha = "a" * 40
    with pytest.raises(grm.PlanValidationError):
        grm.validate_plan(p)


def test_validate_update_requires_different_shas():
    p = _valid_push_plan()
    p.expected_before_sha = "a" * 40
    p.desired_after_sha = "a" * 40
    with pytest.raises(grm.PlanValidationError):
        grm.validate_plan(p)


def test_validate_accumulates_multiple_errors():
    p = _valid_push_plan()
    p.mutation_id = ""
    p.owner_run_id = ""
    p.target_ref = ""
    p.expected_before_sha = "abc"
    with pytest.raises(grm.PlanValidationError) as exc:
        grm.validate_plan(p)
    msg = str(exc.value)
    assert "mutation_id" in msg
    assert "owner_run_id" in msg
    assert "target_ref" in msg
    assert "expected_before_sha" in msg


# ---------------------------------------------------------------------------
# Round-trip JSON
# ---------------------------------------------------------------------------

def test_plan_to_json_round_trip():
    p = _valid_push_plan()
    raw = p.to_json()
    p2 = grm.GuardedMutationPlan.from_json(raw)
    assert p2 == p


def test_plan_to_json_round_trip_with_optional_fields():
    p = _valid_push_plan()
    p.last_reconciled_at = "2026-08-01T01:00:00Z"
    p.terminal_evidence = "stdout:success"
    raw = p.to_json()
    p2 = grm.GuardedMutationPlan.from_json(raw)
    assert p2.last_reconciled_at == p.last_reconciled_at
    assert p2.terminal_evidence == p.terminal_evidence


def test_plan_from_json_rejects_missing_field():
    import json
    raw = json.dumps({"mutation_id": "m1"})
    with pytest.raises(TypeError):
        grm.GuardedMutationPlan.from_json(raw)


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

def test_reconcile_success_actual_equals_desired_after():
    out = grm.reconcile(
        expected_before_sha="0" * 40,
        desired_after_sha="a" * 40,
        actual_ref_sha="a" * 40,
    )
    assert out == grm.LifecycleState.SUCCEEDED


def test_reconcile_not_applied_actual_equals_expected_before():
    out = grm.reconcile(
        expected_before_sha="0" * 40,
        desired_after_sha="a" * 40,
        actual_ref_sha="0" * 40,
    )
    assert out == grm.LifecycleState.NOT_APPLIED


def test_reconcile_conflict_actual_differs_from_both():
    out = grm.reconcile(
        expected_before_sha="0" * 40,
        desired_after_sha="a" * 40,
        actual_ref_sha="b" * 40,
    )
    assert out == grm.LifecycleState.CONFLICT


def test_reconcile_indeterminate_actual_is_none():
    out = grm.reconcile(
        expected_before_sha="0" * 40,
        desired_after_sha="a" * 40,
        actual_ref_sha=None,
    )
    assert out == grm.LifecycleState.INDETERMINATE


def test_reconcile_create_success():
    out = grm.reconcile(
        expected_before_sha=None,
        desired_after_sha="a" * 40,
        actual_ref_sha="a" * 40,
        
    )
    assert out == grm.LifecycleState.SUCCEEDED


def test_reconcile_create_not_applied_when_target_still_missing():
    """The target didn't exist before AND didn't exist after.
    Create failed."""
    out = grm.reconcile(
        expected_before_sha=None,
        desired_after_sha="a" * 40,
        actual_ref_sha=None,
    )
    assert out == grm.LifecycleState.NOT_APPLIED


def test_reconcile_create_conflict_when_target_existed_at_unrelated_sha():
    """The target existed before at an unrelated SHA. Create
    must have collided; fail closed."""
    out = grm.reconcile(
        expected_before_sha=None,
        desired_after_sha="a" * 40,
        actual_ref_sha="b" * 40,
        
    )
    assert out == grm.LifecycleState.CONFLICT


def test_reconcile_delete_success():
    out = grm.reconcile(
        expected_before_sha="a" * 40,
        desired_after_sha=None,
        actual_ref_sha=None,
    )
    assert out == grm.LifecycleState.SUCCEEDED


def test_reconcile_delete_not_applied():
    out = grm.reconcile(
        expected_before_sha="a" * 40,
        desired_after_sha=None,
        actual_ref_sha="a" * 40,
    )
    assert out == grm.LifecycleState.NOT_APPLIED


def test_reconcile_delete_conflict_at_unrelated_sha():
    out = grm.reconcile(
        expected_before_sha="a" * 40,
        desired_after_sha=None,
        actual_ref_sha="b" * 40,
    )
    assert out == grm.LifecycleState.CONFLICT


def test_reconcile_create_with_no_actual_ref_returns_not_applied():
    """CREATE: if the actual ref is None (still missing), the
    create did not happen -> NOT_APPLIED."""
    out = grm.reconcile(
        expected_before_sha=None,
        desired_after_sha="a" * 40,
        actual_ref_sha=None,
    )
    assert out == grm.LifecycleState.NOT_APPLIED


def test_reconcile_is_pure_function():
    """reconcile must not depend on any global state."""
    args = dict(
        expected_before_sha="0" * 40,
        desired_after_sha="a" * 40,
        actual_ref_sha="a" * 40,
    )
    first = grm.reconcile(**args)
    second = grm.reconcile(**args)
    assert first == second


# ---------------------------------------------------------------------------
# Plan path
# ---------------------------------------------------------------------------

def test_guarded_ref_mutation_plan_path(tmp_path):
    p = grm.guarded_ref_mutation_plan_path(tmp_path, "abc123")
    assert str(p).endswith("GUARDED_REF_MUTATIONS/abc123.json")
    assert p.parent.name == "GUARDED_REF_MUTATIONS"


# ---------------------------------------------------------------------------
# Three-semantics interaction
# ---------------------------------------------------------------------------

def test_success_is_terminal_for_that_mutation():
    """A SUCCEEDED plan does not transition to anything else."""
    for target in grm.LifecycleState:
        if target == grm.LifecycleState.SUCCEEDED:
            continue
        assert not grm.is_allowed_transition(
            grm.LifecycleState.SUCCEEDED, target
        )


def test_conflict_is_terminal_for_that_mutation():
    """A CONFLICT plan does not transition to anything else."""
    for target in grm.LifecycleState:
        if target == grm.LifecycleState.CONFLICT:
            continue
        assert not grm.is_allowed_transition(
            grm.LifecycleState.CONFLICT, target
        )


def test_conflict_must_fail_closed():
    """Per the contract: an intervening third-party update
    produces CONFLICT and the user can retry only by issuing a
    NEW mutation plan with a fresh expected_before_sha."""
    out = grm.reconcile(
        expected_before_sha="0" * 40,
        desired_after_sha="a" * 40,
        actual_ref_sha="b" * 40,
    )
    assert out == grm.LifecycleState.CONFLICT
    # CONFLICT is terminal; no auto-retry.
    for target in grm.LifecycleState:
        if target == grm.LifecycleState.CONFLICT:
            continue
        assert not grm.is_allowed_transition(
            grm.LifecycleState.CONFLICT, target
        )


def test_indeterminate_can_only_re_enter_reconciling():
    """INDETERMINATE -> RECONCILING is allowed (re-read
    actual_ref). INDETERMINATE cannot go directly to SUCCEEDED
    or NOT_APPLIED without another reconcile."""
    assert grm.is_allowed_transition(
        grm.LifecycleState.INDETERMINATE, grm.LifecycleState.RECONCILING
    )
    assert not grm.is_allowed_transition(
        grm.LifecycleState.INDETERMINATE, grm.LifecycleState.SUCCEEDED
    )
    assert not grm.is_allowed_transition(
        grm.LifecycleState.INDETERMINATE, grm.LifecycleState.NOT_APPLIED
    )
    assert not grm.is_allowed_transition(
        grm.LifecycleState.INDETERMINATE, grm.LifecycleState.PREPARED
    )
    assert not grm.is_allowed_transition(
        grm.LifecycleState.INDETERMINATE, grm.LifecycleState.EXECUTING
    )


def test_not_applied_safe_retry_path():
    """NOT_APPLIED -> PREPARED is the safe retry path; the
    actual ref is still at expected_before so the retry is
    guaranteed safe."""
    assert grm.is_allowed_transition(
        grm.LifecycleState.NOT_APPLIED, grm.LifecycleState.PREPARED
    )
