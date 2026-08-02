"""AED mutation policy: maps mutation_type to Operation and
derives target_ref, expected_before_sha, desired_after_sha
for the durable GuardedMutationPlan.

Single source of truth for the controller -> executor
pipeline. Used by:
  - scripts/local/autocoder_run_controller.py
    (authorize-mutation emits the durable plan; mutate-ref
     binds to the active authorization)
  - scripts/local/guarded_ref_mutation.py (validate_plan
    consumes the same fields)

The mapping is hardcoded; it is the durable contract
between the controller and the executor. The mutation_type
strings come from the existing CLI vocabulary
(force_push, push, squash_merge, branch_delete,
branch_create_force, pr_body_update, pr_comment_update,
issue_comment_update, branch_label_update, label_change).
The Operation values come from guarded_ref_mutation.Operation.

The target_ref is derived from the mutation_target (the
branch name or tag name). Expected_before_sha and
desired_after_sha come from the CLI flags; this module
validates that both are full 40-char lowercase hex SHAs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from scripts.local.guarded_ref_mutation import (
    Operation as GrdOp,
    ZERO_OID,
    is_full_sha,
)


# ---------------------------------------------------------------------------
# 1. Policy table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MutationPolicyEntry:
    """Hardcoded properties of a single mutation type."""

    mutation_type: str
    operation: GrdOp
    # The full refname for the executor target.
    # "refs/heads/<branch>" for branches,
    # "refs/tags/<tag>" for tags,
    # "refs/pull/<N>/head" for PR refs.
    target_ref_template: str  # e.g. "refs/heads/{branch}"
    # Whether the controller itself performs the operation
    # (e.g. squash_merge via gh pr merge). When True, the
    # durable plan is emitted for audit but the executor
    # does NOT touch the ref; the controller's own result
    # is recorded via record-mutation-result.
    controller_performs: bool


POLICY_TABLE = {
    "squash_merge": MutationPolicyEntry(
        mutation_type="squash_merge",
        operation=GrdOp.GRAPHQL_UPDATE_REFS,
        # squash_merge targets the PR head, not a branch.
        # target_ref is derived from target_pr_number in
        # derive_target_ref; mutation_target is optional.
        target_ref_template="refs/pull/{branch}/head",
        controller_performs=True,
    ),
    "force_push": MutationPolicyEntry(
        mutation_type="force_push",
        operation=GrdOp.PUSH_REMOTE,
        target_ref_template="refs/heads/{branch}",
        controller_performs=False,
    ),
    "push": MutationPolicyEntry(
        mutation_type="push",
        operation=GrdOp.PUSH_REMOTE,
        target_ref_template="refs/heads/{branch}",
        controller_performs=False,
    ),
    "branch_delete": MutationPolicyEntry(
        mutation_type="branch_delete",
        operation=GrdOp.DELETE_LOCAL,
        target_ref_template="refs/heads/{branch}",
        controller_performs=False,
    ),
    "branch_create_force": MutationPolicyEntry(
        mutation_type="branch_create_force",
        operation=GrdOp.CREATE_LOCAL,
        target_ref_template="refs/heads/{branch}",
        controller_performs=False,
    ),
}


def get_policy(mutation_type: str) -> MutationPolicyEntry:
    """Return the hardcoded policy entry for the mutation_type."""
    return POLICY_TABLE[mutation_type]


def supported_mutation_types() -> tuple:
    """Return the supported mutation types in registration order."""
    return tuple(POLICY_TABLE.keys())


# ---------------------------------------------------------------------------
# 2. Plan derivation
# ---------------------------------------------------------------------------


def derive_target_ref(mutation_type: str, mutation_target: Optional[str]) -> str:
    """Derive the full refname from the mutation_target.

    For squash_merge, the mutation_target is the PR number;
    the target ref is refs/pull/<N>/head. If mutation_target
    is None, derive from the policy table's target_ref_template
    with an empty branch placeholder.
    For force_push/push/branch_delete/branch_create_force,
    the mutation_target is a branch name; the target ref is
    refs/heads/<branch>.

    Returns the full refname string.
    """
    policy = POLICY_TABLE[mutation_type]
    if not mutation_target:
        if mutation_type == "squash_merge":
            # squash_merge without a specific PR: the durable
            # plan uses a sentinel ref. The executor (Layer 4+)
            # resolves this against the active PR.
            return "refs/pull//head"
        raise ValueError(
            f"mutation_target is required for {mutation_type}"
        )
    return policy.target_ref_template.format(branch=mutation_target)


@dataclass(frozen=True)
class DerivedPlan:
    """The derived fields of a durable GuardedMutationPlan.

    The controller's authorize-mutation uses this to emit the
    plan alongside the existing MUTATIONS.jsonl append.
    """

    operation: GrdOp
    target_ref: str
    expected_before_sha: Optional[str]
    desired_after_sha: Optional[str]
    controller_performs: bool


def derive_plan(
    *,
    mutation_type: str,
    mutation_target: Optional[str],
    expected_target_sha: Optional[str],
    expected_main_sha: Optional[str],
    desired_after_sha: Optional[str],
) -> DerivedPlan:
    """Derive the durable plan fields from the CLI inputs.

    - For UPDATE/PUSH/DELETE (force_push, push, branch_delete):
      expected_before_sha = expected_target_sha (the remote ref
        must currently point to expected_target_sha).
      desired_after_sha = desired_after_sha (must be a full SHA).
    - For CREATE (branch_create_force):
      expected_before_sha = None (the ref must not exist).
      desired_after_sha = desired_after_sha (must be a full SHA).
    - For GRAPHQL_UPDATE_REFS (squash_merge):
      expected_before_sha = expected_main_sha (the base SHA).
      desired_after_sha = desired_after_sha (the merged SHA,
        optional; not required by policy because the squash
        merge target is whatever GitHub resolves).

    All SHA fields must be full 40-character lowercase hex
    (or None for CREATE expected_before_sha). Empty strings
    are NOT valid domain values.
    """
    if mutation_type not in POLICY_TABLE:
        raise ValueError(
            f"unsupported mutation_type: {mutation_type!r}; "
            f"supported: {supported_mutation_types()}"
        )
    policy = POLICY_TABLE[mutation_type]

    # Validate SHA format upfront.
    if expected_target_sha is not None and not is_full_sha(expected_target_sha):
        raise ValueError(
            f"expected_target_sha must be a full 40-char "
            f"lowercase hex SHA or None; got {expected_target_sha!r}"
        )
    if expected_main_sha is not None and not is_full_sha(expected_main_sha):
        raise ValueError(
            f"expected_main_sha must be a full 40-char "
            f"lowercase hex SHA or None; got {expected_main_sha!r}"
        )
    if desired_after_sha is not None and not is_full_sha(desired_after_sha):
        raise ValueError(
            f"desired_after_sha must be a full 40-char "
            f"lowercase hex SHA or None; got {desired_after_sha!r}"
        )

    target_ref = derive_target_ref(mutation_type, mutation_target)

    if policy.operation == GrdOp.PUSH_REMOTE:
        # The remote ref must currently point to expected_target_sha.
        # desired_after_sha must be a full SHA.
        if expected_target_sha is None:
            raise ValueError(
                f"{mutation_type} requires --expected-target-sha"
            )
        if desired_after_sha is None:
            raise ValueError(
                f"{mutation_type} requires --desired-after-sha"
            )
        return DerivedPlan(
            operation=GrdOp.PUSH_REMOTE,
            target_ref=target_ref,
            expected_before_sha=expected_target_sha,
            desired_after_sha=desired_after_sha,
            controller_performs=policy.controller_performs,
        )

    if policy.operation == GrdOp.DELETE_LOCAL:
        if expected_target_sha is None:
            raise ValueError(
                f"{mutation_type} requires --expected-target-sha"
            )
        if desired_after_sha is not None:
            raise ValueError(
                f"{mutation_type} must NOT have --desired-after-sha"
            )
        return DerivedPlan(
            operation=GrdOp.DELETE_LOCAL,
            target_ref=target_ref,
            expected_before_sha=expected_target_sha,
            desired_after_sha=None,
            controller_performs=policy.controller_performs,
        )

    if policy.operation == GrdOp.CREATE_LOCAL:
        # expected_before_sha is None (the ref must not exist).
        if expected_target_sha is not None:
            raise ValueError(
                f"{mutation_type} must NOT have --expected-target-sha"
            )
        if desired_after_sha is None:
            raise ValueError(
                f"{mutation_type} requires --desired-after-sha"
            )
        return DerivedPlan(
            operation=GrdOp.CREATE_LOCAL,
            target_ref=target_ref,
            expected_before_sha=None,
            desired_after_sha=desired_after_sha,
            controller_performs=policy.controller_performs,
        )

    if policy.operation == GrdOp.GRAPHQL_UPDATE_REFS:
        # squash_merge: the controller performs the merge
        # itself via gh pr merge. The durable plan is emitted
        # for audit. expected_before_sha comes from
        # expected_target_sha (the pre-merge target SHA, which
        # is the PR head the controller will merge into main).
        # If neither is supplied, the controller cannot
        # authorize the merge (refuse).
        before = expected_target_sha or expected_main_sha
        if not before:
            raise ValueError(
                f"{mutation_type} requires --expected-target-sha "
                f"or --expected-main-sha (the pre-merge PR head SHA)"
            )
        # The selected SHA must be a full 40-char lowercase hex.
        # The controller's HEAD_CHANGING_MUTATION_TYPES check
        # only validates --expected-target-sha, so a short
        # --expected-main-sha alone is rejected here too.
        if not is_full_sha(before):
            raise ValueError(
                f"{mutation_type} expected_before_sha must be a "
                f"full 40-char lowercase hex SHA; got {before!r}"
            )
        # desired_after_sha is optional: the controller
        # records the post-merge SHA via record-mutation-result.
        return DerivedPlan(
            operation=GrdOp.GRAPHQL_UPDATE_REFS,
            target_ref=target_ref,
            expected_before_sha=before,
            desired_after_sha=desired_after_sha,
            controller_performs=policy.controller_performs,
        )

    # Defensive: unsupported operation should have been
    # caught at the policy table lookup.
    raise ValueError(f"unhandled operation: {policy.operation}")


# ---------------------------------------------------------------------------
# 3. Authorization binding (used by mutate-ref)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OutstandingAuthorization:
    """An outstanding authorization record from MUTATIONS.jsonl.

    Returned by `find_outstanding_authorization` after
    validating that the loaded plan matches the active
    authorization. Used by mutate-ref to bind the loaded
    plan to the active authorization.
    """

    mutation_id: str
    owner_run_id: str
    repository: str
    mutation_type: str
    mutation_target: Optional[str]
    expected_main_sha: Optional[str]
    expected_target_sha: Optional[str]
    pending_action: str
    authorization_status: str  # "AUTHORIZED"


class AuthorizationBindingError(ValueError):
    """Raised when a loaded plan cannot be bound to an active
    authorization."""


def find_outstanding_authorization(
    mutations_path_records: list,
    *,

    mutation_id: str,
    owner_run_id: str,
    repository: str,
    target_ref: str,
    expected_before_sha: Optional[str],
    desired_after_sha: Optional[str] = None,
    active_workspace: Optional[str] = None,
    workspace: Optional["Path"] = None,
) -> OutstandingAuthorization:
    """Find the outstanding authorization for the given
    mutation_id and verify the loaded plan matches it.

    Returns the OutstandingAuthorization. Raises
    AuthorizationBindingError on any mismatch:
      - mutation_id not found in MUTATIONS.jsonl
      - owner_run_id mismatch
      - repository mismatch
      - target_ref mismatch
      - expected_before_sha mismatch (when the authorization
        has a non-None expected SHA)
      - desired_after_sha mismatch (when the authorization
        has a non-None expected SHA; Repair 4)
      - authorization_status not AUTHORIZED
      - active_workspace mismatch (Repair 5: prevents the
        former owner of a stale-lock-recovered workspace
        from invoking mutate-ref after the replacement
        owner has taken over the lease)

    The desired_after_sha check is essential: without it, a
    force_push plan could substitute any other valid local
    commit, pass validation, and push that unauthorized SHA.
    """
    for rec in mutations_path_records:
        if rec.get("mutation_id") != mutation_id:
            continue
        if rec.get("run_id") != owner_run_id:
            raise AuthorizationBindingError(
                f"authorization owner_run_id={rec.get('run_id')!r} "
                f"does not match plan owner_run_id={owner_run_id!r}"
            )
        if rec.get("repository") != repository:
            raise AuthorizationBindingError(
                f"authorization repository={rec.get('repository')!r} "
                f"does not match plan repository={repository!r}"
            )
        # The authorization stores mutation_target as the
        # branch name; the plan stores the full refname. The
        # full refname ends with /<branch>; compare suffix.
        rec_target = rec.get("mutation_target") or ""
        if target_ref != f"refs/heads/{rec_target}":
            raise AuthorizationBindingError(
                f"authorization mutation_target={rec_target!r} "
                f"does not match plan target_ref={target_ref!r}"
            )
        rec_expected = rec.get("expected_target_sha") or rec.get("expected_main_sha")
        if rec_expected and expected_before_sha and rec_expected != expected_before_sha:
            raise AuthorizationBindingError(
                f"authorization expected_sha={rec_expected!r} "
                f"does not match plan expected_before_sha="
                f"{expected_before_sha!r}"
            )
        # Round-69 P1 fix (Persist and require the authorized
        # destination SHA): the legacy authorize() emits the
        # journal record WITHOUT a desired_after_sha field
        # (the desired SHA is in the durable plan file
        # instead). The previous check skipped the comparison
        # when rec_desired was None, allowing a durable plan
        # to be modified post-authorization to name another
        # valid commit, which the runner would then push —
        # a SHA that was never recorded in the authorization.
        # The fix: when the journal record has no
        # desired_after_sha, load the plan file from the
        # workspace and verify the plan's desired_after_sha
        # matches the caller's. This binds the durable plan
        # to the journal record.
        rec_desired = rec.get("desired_after_sha")
        if rec_desired is None and desired_after_sha is not None and workspace is not None:
            from scripts.local.guarded_ref_mutation import (
                guarded_ref_mutation_plan_path as _plan_path,
            )
            from scripts.local.guarded_ref_mutation import (
                GuardedMutationPlan as _GMP,
            )
            plan_path = _plan_path(workspace, mutation_id)
            if plan_path.is_file():
                try:
                    plan_obj = _GMP.from_json(plan_path.read_text())
                    rec_desired = plan_obj.desired_after_sha
                except (OSError, ValueError):
                    rec_desired = None
        if (
            rec_desired
            and desired_after_sha
            and rec_desired != desired_after_sha
        ):
            raise AuthorizationBindingError(
                f"authorization desired_after_sha={rec_desired!r} "
                f"does not match plan desired_after_sha="
                f"{desired_after_sha!r}"
            )
        # Repair 5: the active workspace must match the
        # authorization's recorded workspace. After a
        # stale-lock recovery, the replacement owner has a
        # different workspace and supervisor lease. The
        # former owner's outstanding journal record must
        # not be executable by them.
        rec_workspace = rec.get("workspace")
        if (
            active_workspace
            and rec_workspace
            and active_workspace != rec_workspace
        ):
            raise AuthorizationBindingError(
                f"authorization workspace={rec_workspace!r} "
                f"does not match active workspace="
                f"{active_workspace!r}; the former owner of a "
                f"stale-lock-recovered workspace cannot invoke "
                f"mutate-ref"
            )
        # The legacy authorize() emits lowercase "authorized"
        # (see aed_mutation_authorization.AUTHORIZED). Some
        # older fixtures and the test seed use uppercase
        # "AUTHORIZED". Compare case-insensitively.
        rec_status = (rec.get("authorization_status") or "").lower()
        if rec_status != "authorized":
            raise AuthorizationBindingError(
                f"authorization status={rec.get('authorization_status')!r} "
                f"is not AUTHORIZED"
            )
        return OutstandingAuthorization(
            mutation_id=rec["mutation_id"],
            owner_run_id=rec["run_id"],
            repository=rec["repository"],
            mutation_type=rec["mutation_type"],
            mutation_target=rec_target,
            expected_main_sha=rec.get("expected_main_sha"),
            expected_target_sha=rec.get("expected_target_sha"),
            pending_action=rec.get("pending_action", ""),
            authorization_status=rec["authorization_status"],
        )
    raise AuthorizationBindingError(
        f"mutation_id={mutation_id!r} not found in MUTATIONS.jsonl"
    )


__all__ = (
    "MutationPolicyEntry",
    "POLICY_TABLE",
    "get_policy",
    "supported_mutation_types",
    "derive_target_ref",
    "DerivedPlan",
    "derive_plan",
    "OutstandingAuthorization",
    "AuthorizationBindingError",
    "find_outstanding_authorization",
)