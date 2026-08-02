"""Focused unit tests for scripts/local/mutation_policy.py.

Pure-data tests: no filesystem, no journal sentinel, no
supervisor-lease operations. Covers the policy table, plan
derivation, and authorization binding.
"""

from __future__ import annotations

import pytest

from scripts.local import mutation_policy as mp
from scripts.local.guarded_ref_mutation import Operation as GrdOp


# ---------------------------------------------------------------------------
# Policy table
# ---------------------------------------------------------------------------


def test_policy_table_covers_all_supported_mutation_types():
    expected = {
        "squash_merge",
        "force_push",
        "push",
        "branch_delete",
        "branch_create_force",
    }
    assert set(mp.POLICY_TABLE.keys()) == expected


def test_policy_table_maps_to_correct_operations():
    assert mp.get_policy("squash_merge").operation == GrdOp.GRAPHQL_UPDATE_REFS
    assert mp.get_policy("force_push").operation == GrdOp.PUSH_REMOTE
    assert mp.get_policy("push").operation == GrdOp.PUSH_REMOTE
    assert mp.get_policy("branch_delete").operation == GrdOp.DELETE_LOCAL
    assert mp.get_policy("branch_create_force").operation == GrdOp.CREATE_LOCAL


def test_squash_merge_is_controller_performs():
    """squash_merge is performed by the controller via
    gh pr merge. The durable plan is emitted for audit only;
    the executor does NOT touch the ref."""
    assert mp.get_policy("squash_merge").controller_performs is True


def test_force_push_push_branch_delete_branch_create_force_are_executor_performed():
    for mt in ("force_push", "push", "branch_delete", "branch_create_force"):
        assert mp.get_policy(mt).controller_performs is False, (
            f"{mt} should be executor-performed"
        )


def test_supported_mutation_types_returns_tuple():
    types = mp.supported_mutation_types()
    assert isinstance(types, tuple)
    assert set(types) == set(mp.POLICY_TABLE.keys())


# ---------------------------------------------------------------------------
# derive_target_ref
# ---------------------------------------------------------------------------


def test_derive_target_ref_for_branch_mutations():
    assert mp.derive_target_ref("force_push", "main") == "refs/heads/main"
    assert mp.derive_target_ref("push", "feat/x") == "refs/heads/feat/x"
    assert mp.derive_target_ref("branch_delete", "old") == "refs/heads/old"
    assert (
        mp.derive_target_ref("branch_create_force", "new")
        == "refs/heads/new"
    )


def test_derive_target_ref_requires_mutation_target():
    with pytest.raises(ValueError):
        mp.derive_target_ref("force_push", None)


def test_derive_target_ref_rejects_empty_mutation_target():
    with pytest.raises(ValueError):
        mp.derive_target_ref("force_push", "")


# ---------------------------------------------------------------------------
# derive_plan
# ---------------------------------------------------------------------------

def _full_sha(c: str) -> str:
    return c * 40


def test_derive_plan_force_push():
    plan = mp.derive_plan(
        mutation_type="force_push",
        mutation_target="feat/x",
        expected_target_sha=_full_sha("a"),
        expected_main_sha=None,
        desired_after_sha=_full_sha("b"),
    )
    assert plan.operation == GrdOp.PUSH_REMOTE
    assert plan.target_ref == "refs/heads/feat/x"
    assert plan.expected_before_sha == _full_sha("a")
    assert plan.desired_after_sha == _full_sha("b")
    assert plan.controller_performs is False


def test_derive_plan_push():
    plan = mp.derive_plan(
        mutation_type="push",
        mutation_target="main",
        expected_target_sha=_full_sha("a"),
        expected_main_sha=None,
        desired_after_sha=_full_sha("b"),
    )
    assert plan.operation == GrdOp.PUSH_REMOTE
    assert plan.expected_before_sha == _full_sha("a")
    assert plan.desired_after_sha == _full_sha("b")


def test_derive_plan_branch_delete():
    plan = mp.derive_plan(
        mutation_type="branch_delete",
        mutation_target="old",
        expected_target_sha=_full_sha("a"),
        expected_main_sha=None,
        desired_after_sha=None,
    )
    assert plan.operation == GrdOp.DELETE_LOCAL
    assert plan.expected_before_sha == _full_sha("a")
    assert plan.desired_after_sha is None


def test_derive_plan_branch_create_force():
    plan = mp.derive_plan(
        mutation_type="branch_create_force",
        mutation_target="new",
        expected_target_sha=None,
        expected_main_sha=None,
        desired_after_sha=_full_sha("b"),
    )
    assert plan.operation == GrdOp.CREATE_LOCAL
    assert plan.expected_before_sha is None
    assert plan.desired_after_sha == _full_sha("b")


def test_derive_plan_squash_merge():
    plan = mp.derive_plan(
        mutation_type="squash_merge",
        mutation_target="main",
        expected_target_sha=None,
        expected_main_sha=_full_sha("a"),
        desired_after_sha=None,
    )
    assert plan.operation == GrdOp.GRAPHQL_UPDATE_REFS
    assert plan.expected_before_sha == _full_sha("a")
    assert plan.desired_after_sha is None
    assert plan.controller_performs is True


def test_derive_plan_rejects_short_sha():
    with pytest.raises(ValueError):
        mp.derive_plan(
            mutation_type="force_push",
            mutation_target="feat/x",
            expected_target_sha="abc",
            expected_main_sha=None,
            desired_after_sha=_full_sha("b"),
        )


def test_derive_plan_rejects_empty_string_sha():
    """The domain uses None for missing SHAs, not empty
    string. Empty string is explicitly rejected."""
    with pytest.raises(ValueError):
        mp.derive_plan(
            mutation_type="force_push",
            mutation_target="feat/x",
            expected_target_sha="",
            expected_main_sha=None,
            desired_after_sha=_full_sha("b"),
        )


def test_derive_plan_rejects_unsupported_mutation_type():
    with pytest.raises(ValueError):
        mp.derive_plan(
            mutation_type="unknown",
            mutation_target="main",
            expected_target_sha=None,
            expected_main_sha=None,
            desired_after_sha=None,
        )


def test_derive_plan_force_push_requires_both_shas():
    with pytest.raises(ValueError):
        mp.derive_plan(
            mutation_type="force_push",
            mutation_target="feat/x",
            expected_target_sha=None,
            expected_main_sha=None,
            desired_after_sha=_full_sha("b"),
        )
    with pytest.raises(ValueError):
        mp.derive_plan(
            mutation_type="force_push",
            mutation_target="feat/x",
            expected_target_sha=_full_sha("a"),
            expected_main_sha=None,
            desired_after_sha=None,
        )


def test_derive_plan_branch_create_force_rejects_existing_target_sha():
    with pytest.raises(ValueError):
        mp.derive_plan(
            mutation_type="branch_create_force",
            mutation_target="new",
            expected_target_sha=_full_sha("a"),  # wrong: must be None
            expected_main_sha=None,
            desired_after_sha=_full_sha("b"),
        )


def test_derive_plan_branch_delete_rejects_desired_after_sha():
    with pytest.raises(ValueError):
        mp.derive_plan(
            mutation_type="branch_delete",
            mutation_target="old",
            expected_target_sha=_full_sha("a"),
            expected_main_sha=None,
            desired_after_sha=_full_sha("b"),  # wrong: must be None
        )


# ---------------------------------------------------------------------------
# find_outstanding_authorization
# ---------------------------------------------------------------------------


def test_find_outstanding_authorization_succeeds_for_matching_record():
    records = [
        {
            "mutation_id": "m1",
            "run_id": "r1",
            "repository": "owner/name",
            "mutation_type": "force_push",
            "mutation_target": "feat/x",
            "expected_main_sha": _full_sha("a"),
            "expected_target_sha": _full_sha("a"),
            "pending_action": "force_push",
            "authorization_status": "AUTHORIZED",
        }
    ]
    out = mp.find_outstanding_authorization(
        records,
        mutation_id="m1",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/feat/x",
        expected_before_sha=_full_sha("a"),
    )
    assert out.mutation_id == "m1"
    assert out.owner_run_id == "r1"
    assert out.mutation_target == "feat/x"


def test_find_outstanding_authorization_uses_main_sha_when_target_missing():
    """When the authorization has no expected_target_sha, the
    matcher falls back to expected_main_sha."""
    records = [
        {
            "mutation_id": "m1",
            "run_id": "r1",
            "repository": "owner/name",
            "mutation_type": "squash_merge",
            "mutation_target": "main",
            "expected_main_sha": _full_sha("a"),
            "expected_target_sha": None,
            "pending_action": "merge",
            "authorization_status": "AUTHORIZED",
        }
    ]
    out = mp.find_outstanding_authorization(
        records,
        mutation_id="m1",
        owner_run_id="r1",
        repository="owner/name",
        target_ref="refs/heads/main",
        expected_before_sha=_full_sha("a"),
    )
    assert out.expected_main_sha == _full_sha("a")


def test_find_outstanding_authorization_rejects_owner_mismatch():
    records = [
        {
            "mutation_id": "m1",
            "run_id": "r1",
            "repository": "owner/name",
            "mutation_type": "force_push",
            "mutation_target": "feat/x",
            "expected_target_sha": _full_sha("a"),
            "authorization_status": "AUTHORIZED",
        }
    ]
    with pytest.raises(mp.AuthorizationBindingError):
        mp.find_outstanding_authorization(
            records,
            mutation_id="m1",
            owner_run_id="r2",  # wrong
            repository="owner/name",
            target_ref="refs/heads/feat/x",
            expected_before_sha=_full_sha("a"),
        )


def test_find_outstanding_authorization_rejects_repository_mismatch():
    records = [
        {
            "mutation_id": "m1",
            "run_id": "r1",
            "repository": "owner/name",
            "mutation_type": "force_push",
            "mutation_target": "feat/x",
            "expected_target_sha": _full_sha("a"),
            "authorization_status": "AUTHORIZED",
        }
    ]
    with pytest.raises(mp.AuthorizationBindingError):
        mp.find_outstanding_authorization(
            records,
            mutation_id="m1",
            owner_run_id="r1",
            repository="different/repo",  # wrong
            target_ref="refs/heads/feat/x",
            expected_before_sha=_full_sha("a"),
        )


def test_find_outstanding_authorization_rejects_target_ref_mismatch():
    records = [
        {
            "mutation_id": "m1",
            "run_id": "r1",
            "repository": "owner/name",
            "mutation_type": "force_push",
            "mutation_target": "feat/x",
            "expected_target_sha": _full_sha("a"),
            "authorization_status": "AUTHORIZED",
        }
    ]
    with pytest.raises(mp.AuthorizationBindingError):
        mp.find_outstanding_authorization(
            records,
            mutation_id="m1",
            owner_run_id="r1",
            repository="owner/name",
            target_ref="refs/heads/feat/y",  # wrong
            expected_before_sha=_full_sha("a"),
        )


def test_find_outstanding_authorization_rejects_expected_sha_mismatch():
    records = [
        {
            "mutation_id": "m1",
            "run_id": "r1",
            "repository": "owner/name",
            "mutation_type": "force_push",
            "mutation_target": "feat/x",
            "expected_target_sha": _full_sha("a"),
            "authorization_status": "AUTHORIZED",
        }
    ]
    with pytest.raises(mp.AuthorizationBindingError):
        mp.find_outstanding_authorization(
            records,
            mutation_id="m1",
            owner_run_id="r1",
            repository="owner/name",
            target_ref="refs/heads/feat/x",
            expected_before_sha=_full_sha("b"),  # wrong
        )


def test_find_outstanding_authorization_rejects_non_AUTHORIZED_status():
    records = [
        {
            "mutation_id": "m1",
            "run_id": "r1",
            "repository": "owner/name",
            "mutation_type": "force_push",
            "mutation_target": "feat/x",
            "expected_target_sha": _full_sha("a"),
            "authorization_status": "TERMINAL",  # wrong
        }
    ]
    with pytest.raises(mp.AuthorizationBindingError):
        mp.find_outstanding_authorization(
            records,
            mutation_id="m1",
            owner_run_id="r1",
            repository="owner/name",
            target_ref="refs/heads/feat/x",
            expected_before_sha=_full_sha("a"),
        )


def test_find_outstanding_authorization_raises_for_missing_mutation_id():
    records = [
        {
            "mutation_id": "m1",
            "run_id": "r1",
            "repository": "owner/name",
            "mutation_type": "force_push",
            "mutation_target": "feat/x",
            "expected_target_sha": _full_sha("a"),
            "authorization_status": "AUTHORIZED",
        }
    ]
    with pytest.raises(mp.AuthorizationBindingError):
        mp.find_outstanding_authorization(
            records,
            mutation_id="m_unknown",  # wrong
            owner_run_id="r1",
            repository="owner/name",
            target_ref="refs/heads/feat/x",
            expected_before_sha=_full_sha("a"),
        )