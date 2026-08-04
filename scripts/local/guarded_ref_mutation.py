"""AED guarded-ref mutation plan.

The durable plan record for the Git-native compare-and-swap
correctness mechanism. No secondary supervisor-lease lifecycle.
No cross-scope upgrades. No target-lease recovery.

This module defines:
  - Lifecycle state constants (PREPARED, EXECUTING, RECONCILING,
    SUCCEEDED, NOT_APPLIED, CONFLICT, INDETERMINATE).
  - Allowed transitions (forward only; SUCCEEDED and CONFLICT are
    terminal; INDETERMINATE can re-enter RECONCILING).
  - Plan record with the persisted fields the user specified.
  - Per-operation validation rules.
  - reconcile() classifies the actual remote ref state into the
    outcome enum. Uses None (not "") for an absent ref throughout
    the domain; the Git adapter converts None to the empty-string
    or zero-OID form expected by the underlying git transport.

The reconciliation definition:
  - actual_ref == desired_after -> SUCCEEDED
  - actual_ref == expected_before -> NOT_APPLIED (safe retry)
  - actual_ref differs from both -> CONFLICT (fail closed)
  - cannot read the actual ref -> INDETERMINATE (retry reconciliation
    only; never a blind retry of the mutation)

Layer 2 of the Round-52-fix architectural repair.
"""

from __future__ import annotations

import enum
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 1. Lifecycle state constants
# ---------------------------------------------------------------------------

class LifecycleState(str, enum.Enum):
    PREPARED = "PREPARED"
    EXECUTING = "EXECUTING"
    RECONCILING = "RECONCILING"
    SUCCEEDED = "SUCCEEDED"
    NOT_APPLIED = "NOT_APPLIED"
    CONFLICT = "CONFLICT"
    INDETERMINATE = "INDETERMINATE"


ALLOWED_TRANSITIONS = {
    (LifecycleState.PREPARED, LifecycleState.EXECUTING),
    (LifecycleState.EXECUTING, LifecycleState.RECONCILING),
    (LifecycleState.RECONCILING, LifecycleState.SUCCEEDED),
    (LifecycleState.RECONCILING, LifecycleState.NOT_APPLIED),
    (LifecycleState.RECONCILING, LifecycleState.CONFLICT),
    (LifecycleState.RECONCILING, LifecycleState.INDETERMINATE),
    # After INDETERMINATE, the operator may retry reconciliation only.
    (LifecycleState.INDETERMINATE, LifecycleState.RECONCILING),
    # After NOT_APPLIED, the actual ref is still at expected_before so
    # returning to PREPARED is a safe retry.
    (LifecycleState.NOT_APPLIED, LifecycleState.PREPARED),
}

# Terminal states — no outgoing transitions.
TERMINAL_STATES = frozenset({
    LifecycleState.SUCCEEDED,
    LifecycleState.CONFLICT,
})


# ---------------------------------------------------------------------------
# 2. Plan record
# ---------------------------------------------------------------------------

# Full 40-character lowercase hex SHA. Git itself uses this form.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def is_full_sha(value: str) -> bool:
    """True iff value is a full 40-char lowercase hex SHA."""
    if not isinstance(value, str):
        return False
    return bool(_SHA_RE.match(value))


# GitHub ref names: alphanumeric + . / _ - and : for tags.
# Loose validation: reject empty, NUL, leading '-', control chars.
def is_valid_ref_name(ref: str) -> bool:
    """True iff ref is a syntactically plausible git ref name."""
    if not isinstance(ref, str):
        return False
    if not ref:
        return False
    if "\x00" in ref or any(ord(c) < 32 for c in ref):
        return False
    if ref.startswith("-"):
        return False
    return True


class Operation(str, enum.Enum):
    """The type of ref mutation.

    PUSH_REMOTE: a remote push to a remote (uses
        --force-with-lease=<full-refname>:<exact-sha>).
    UPDATE_LOCAL: a local update (uses git update-ref
        <ref> <new> <expected-old>).
    CREATE_LOCAL: a local create (uses git update-ref
        <ref> <new> <zero-oid>).
    DELETE_LOCAL: a local delete (uses git update-ref
        <ref> <zero-oid> <expected-old>).
    GRAPHQL_UPDATE_REFS: a GitHub GraphQL updateRefs call
        (requires beforeOid + afterOid).
    """

    PUSH_REMOTE = "PUSH_REMOTE"
    UPDATE_LOCAL = "UPDATE_LOCAL"
    CREATE_LOCAL = "CREATE_LOCAL"
    DELETE_LOCAL = "DELETE_LOCAL"
    GRAPHQL_UPDATE_REFS = "GRAPHQL_UPDATE_REFS"


# Sentinel constant for the zero-OID (the all-zeros SHA). Git uses
# this to mean "no value" for the corresponding position. The
# domain uses None; the Git adapter translates to/from this
# constant when invoking git update-ref or push.
ZERO_OID = "0" * 40


def oid_to_zero(oid: Optional[str]) -> str:
    """Translate None to the zero-OID. Used at the Git adapter."""
    if oid is None:
        return ZERO_OID
    return oid


def oid_from_git(raw: Optional[str]) -> Optional[str]:
    """Translate an empty string from git rev-parse to None.
    Other values pass through."""
    if raw == "":
        return None
    return raw


@dataclass
class GuardedMutationPlan:
    """The durable plan record.

    expected_before_sha: None means "the ref must not exist".
      The Git adapter translates None to the zero-OID when invoking
      git update-ref CREATE.

    desired_after_sha: None means "delete the ref". The Git adapter
      translates None to the zero-OID when invoking git update-ref
      DELETE or to an empty refspec when invoking git push.
    """

    mutation_id: str
    owner_run_id: str
    repository: str
    target_ref: str
    operation: str
    expected_before_sha: Optional[str]
    desired_after_sha: Optional[str]
    status: str
    created_at: str
    last_reconciled_at: Optional[str] = None
    terminal_evidence: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "GuardedMutationPlan":
        data = json.loads(raw)
        return cls(**data)


# ---------------------------------------------------------------------------
# 3. Validation
# ---------------------------------------------------------------------------

class PlanValidationError(ValueError):
    """Raised when a GuardedMutationPlan is invalid."""


def validate_plan(plan: GuardedMutationPlan) -> None:
    """Validate the plan. Raises PlanValidationError on any
    invalid field. Pure function. No I/O.

    Domain conventions:
      - expected_before_sha is None for CREATE (the ref must not
        exist), or a full SHA otherwise.
      - desired_after_sha is None for DELETE, or a full SHA
        otherwise.
      - For UPDATE/PUSH/GRAPHQL, both SHAs are full SHAs and they
        MUST differ.
    """
    errors: list = []

    if not isinstance(plan.mutation_id, str) or not plan.mutation_id:
        errors.append("mutation_id must be a non-empty string")
    if not isinstance(plan.owner_run_id, str) or not plan.owner_run_id:
        errors.append("owner_run_id must be a non-empty string")
    if not isinstance(plan.repository, str) or not plan.repository:
        errors.append("repository must be a non-empty string")
    if not isinstance(plan.target_ref, str) or not is_valid_ref_name(
        plan.target_ref
    ):
        errors.append(
            f"target_ref must be a valid ref name; got {plan.target_ref!r}"
        )

    try:
        op = Operation(plan.operation)
    except ValueError:
        errors.append(
            f"operation must be one of "
            f"{[o.value for o in Operation]}; got {plan.operation!r}"
        )
        op = None

    # expected_before_sha: None OR full SHA. Empty string is
    # NOT a valid domain value.
    if plan.expected_before_sha is not None and not is_full_sha(
        plan.expected_before_sha
    ):
        errors.append(
            "expected_before_sha must be a full 40-char lowercase "
            f"hex SHA or None; got {plan.expected_before_sha!r}"
        )

    # desired_after_sha: None OR full SHA.
    if plan.desired_after_sha is not None and not is_full_sha(
        plan.desired_after_sha
    ):
        errors.append(
            "desired_after_sha must be a full 40-char lowercase "
            f"hex SHA or None; got {plan.desired_after_sha!r}"
        )

    try:
        LifecycleState(plan.status)
    except ValueError:
        errors.append(
            f"status must be a LifecycleState value; got {plan.status!r}"
        )

    # Operation-specific rules.
    if op is Operation.CREATE_LOCAL:
        if plan.expected_before_sha is not None:
            errors.append(
                "CREATE requires expected_before_sha to be None "
                "(the target must NOT exist)"
            )
        if plan.desired_after_sha is None:
            errors.append(
                "CREATE requires desired_after_sha to be a full SHA"
            )
    elif op is Operation.DELETE_LOCAL:
        if plan.expected_before_sha is None:
            errors.append(
                "DELETE requires expected_before_sha to be a full SHA"
            )
        if plan.desired_after_sha is not None:
            errors.append(
                "DELETE requires desired_after_sha to be None"
            )
    elif op is Operation.UPDATE_LOCAL or op is Operation.PUSH_REMOTE:
        if plan.expected_before_sha is None:
            errors.append(
                f"{op.value} requires expected_before_sha to be a full SHA"
            )
        if plan.desired_after_sha is None:
            errors.append(
                f"{op.value} requires desired_after_sha to be a full SHA"
            )
        if (
            (plan.expected_before_sha is not None and is_full_sha(plan.expected_before_sha))
            and (plan.desired_after_sha is not None and is_full_sha(plan.desired_after_sha))
            and plan.expected_before_sha == plan.desired_after_sha
        ):
            errors.append(
                f"{op.value} requires expected_before_sha != desired_after_sha"
            )
    elif op is Operation.GRAPHQL_UPDATE_REFS:
        if plan.expected_before_sha is None:
            errors.append(
                "GRAPHQL_UPDATE_REFS requires beforeOid full SHA"
            )
        # desired_after_sha is OPTIONAL: for squash_merge the
        # controller records the post-merge SHA via
        # record-mutation-result. The durable plan records the
        # PRE-merge state; the POST-merge state is recorded
        # later.
        if plan.desired_after_sha is not None and not is_full_sha(
            plan.desired_after_sha
        ):
            errors.append(
                "GRAPHQL_UPDATE_REFS desired_after_sha must be "
                "a full 40-char lowercase hex SHA when supplied"
            )

    if errors:
        raise PlanValidationError(
            f"plan {plan.mutation_id} invalid: " + "; ".join(errors)
        )


# ---------------------------------------------------------------------------
# 4. Allowed transitions
# ---------------------------------------------------------------------------

class LifecycleError(ValueError):
    """Raised when a state transition is not allowed."""


def assert_allowed_transition(
    from_state: LifecycleState, to_state: LifecycleState
) -> None:
    """Raise LifecycleError if the transition is not allowed.

    Terminal states (SUCCEEDED, CONFLICT) are absorbing.
    """
    if from_state in TERMINAL_STATES:
        raise LifecycleError(
            f"{from_state.value} is terminal; cannot transition to "
            f"{to_state.value}"
        )
    if (from_state, to_state) not in ALLOWED_TRANSITIONS:
        raise LifecycleError(
            f"transition {from_state.value} -> {to_state.value} is not allowed"
        )


def is_allowed_transition(
    from_state: LifecycleState, to_state: LifecycleState
) -> bool:
    """Return True iff the transition is allowed."""
    if from_state in TERMINAL_STATES:
        return False
    return (from_state, to_state) in ALLOWED_TRANSITIONS


# ---------------------------------------------------------------------------
# 5. Reconciliation
# ---------------------------------------------------------------------------
#
# The user specified the reconciliation rules:
#
#   - actual == desired_after -> SUCCEEDED
#   - actual == expected_before -> NOT_APPLIED (safe retry)
#   - actual differs from both -> CONFLICT (fail closed)
#   - cannot read -> INDETERMINATE (retry reconciliation only;
#     do NOT blind-retry the mutation)
#
# The domain uses None to mean "the ref does not exist". This
# applies uniformly to CREATE (actual == "" in git, but None in
# the domain), DELETE (expected_after == "" in git, but None in
# the domain), and the underlying read_ref() return value.

def reconcile(
    *,
    expected_before_sha: Optional[str],
    desired_after_sha: Optional[str],
    actual_ref_sha: Optional[str],
    actual_ref_indeterminate: bool = False,
) -> LifecycleState:
    """Classify the actual remote ref state into the outcome
    LifecycleState.

    Pure function. No I/O. The caller is responsible for
    reading the actual ref via git rev-parse and passing it
    here. If the caller cannot read the ref, pass
    actual_ref_sha=None and actual_ref_indeterminate=True. The
    function returns INDETERMINATE in that case for ALL
    operation types (DELETE/CREATE/UPDATE/PUSH), since the
    caller cannot distinguish a missing ref from a read failure.

    The domain uses None to mean "the ref does not exist"
    (only when actual_ref_indeterminate=False). Empty-string
    values from git rev-parse are translated to None by the
    Git adapter before reaching this function.
    """
    # Cannot read the actual ref -> INDETERMINATE for ALL ops.
    # Round-112 P2 fix (CodeRabbit finding WJXAB): the previous
    # implementation returned SUCCEEDED for DELETE and
    # NOT_APPLIED for CREATE when actual_ref_sha was None, even
    # when the caller passed None BECAUSE the read failed.
    # That conflated a missing ref with a read failure — for
    # DELETE, "missing ref" is the desired terminal state; for
    # CREATE, "missing ref" means the create did not happen.
    # In both cases, if the caller flagged the read as
    # indeterminate, the correct classification is INDETERMINATE
    # (a known-unknown) rather than a definitive terminal
    # state. The new actual_ref_indeterminate parameter
    # disambiguates these cases.
    if actual_ref_indeterminate or actual_ref_sha is None:
        # Round-112 P2 fix (CodeRabbit finding WJXAB): the
        # legacy behavior treated DELETE-with-missing-ref as
        # SUCCEEDED and CREATE-with-missing-ref as
        # NOT_APPLIED without an explicit indeterminate flag.
        # That semantic is preserved ONLY when the read was
        # definitely successful (actual_ref_indeterminate=False)
        # AND the operation's domain semantics specifically say
        # "missing ref == done".
        if actual_ref_indeterminate:
            # The caller couldn't read the ref at all. We
            # cannot tell missing from present.
            return LifecycleState.INDETERMINATE
        # The read succeeded and the ref is genuinely absent.
        if desired_after_sha is None:
            # DELETE: ref absent == target successfully deleted.
            return LifecycleState.SUCCEEDED
        if expected_before_sha is None:
            # CREATE: target was supposed to not exist and
            # still does not. The create did not happen.
            return LifecycleState.NOT_APPLIED
        # UPDATE/PUSH: ref absent cannot be classified
        # definitively (could be a clean checkout, a deleted
        # upstream, or a read failure that the caller didn't
        # flag). INDETERMINATE.
        return LifecycleState.INDETERMINATE

    # CREATE: actual must equal desired_after for success.
    if expected_before_sha is None:
        if actual_ref_sha == desired_after_sha:
            return LifecycleState.SUCCEEDED
        # The target existed at an unrelated SHA -> CONFLICT.
        return LifecycleState.CONFLICT

    # DELETE: actual must be None (ref does not exist) for success.
    if desired_after_sha is None:
        if actual_ref_sha is None:
            return LifecycleState.SUCCEEDED
        if actual_ref_sha == expected_before_sha:
            return LifecycleState.NOT_APPLIED
        return LifecycleState.CONFLICT

    # UPDATE or PUSH: actual must equal desired_after for
    # success, expected_before for NOT_APPLIED, or anything else
    # for CONFLICT.
    if actual_ref_sha == desired_after_sha:
        return LifecycleState.SUCCEEDED
    if actual_ref_sha == expected_before_sha:
        return LifecycleState.NOT_APPLIED
    return LifecycleState.CONFLICT


# ---------------------------------------------------------------------------
# 6. Plan file path
# ---------------------------------------------------------------------------


def guarded_ref_mutation_plan_path(
    workspace: Path, mutation_id: str
) -> Path:
    """Return the path to the durable plan file for a mutation."""
    return Path(workspace) / "GUARDED_REF_MUTATIONS" / f"{mutation_id}.json"


__all__ = (
    "LifecycleState",
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATES",
    "Operation",
    "GuardedMutationPlan",
    "PlanValidationError",
    "LifecycleError",
    "validate_plan",
    "assert_allowed_transition",
    "is_allowed_transition",
    "reconcile",
    "is_full_sha",
    "is_valid_ref_name",
    "guarded_ref_mutation_plan_path",
    "ZERO_OID",
    "oid_to_zero",
    "oid_from_git",
)
