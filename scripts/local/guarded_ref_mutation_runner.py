"""AED guarded-ref mutation orchestrator.

The controller-level orchestrator that ties the durable plan
(guarded_ref_mutation) to the actual git operations
(guarded_ref_ops). The orchestrator:

  1. Initializes a GuardedMutationPlan (PREPARED).
  2. Performs the guarded git operation (EXECUTING).
  3. Reads the actual remote ref (RECONCILING).
  4. Classifies the outcome via reconcile() (terminal state).
  5. Updates the plan at each transition.

The orchestrator is a thin layer; it does NOT make policy
decisions. The mutations list (input to the orchestrator) is
already validated by the controller's authorize-mutation path.

The orchestrator is idempotent. Calling execute() twice on a
SUCCEEDED plan returns ok with the existing actual_ref_sha. The
reconcile() function handles the safety:
  - actual == desired_after -> SUCCEEDED
  - actual == expected_before -> NOT_APPLIED (safe retry)
  - actual differs from both -> CONFLICT
  - cannot read -> INDETERMINATE (reconcile retry only)

The primary controller lease remains active. This module does
not introduce a secondary target-lease lifecycle.
"""

from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path
from typing import Optional

from scripts.local import guarded_ref_mutation as grm
from scripts.local import guarded_ref_ops as ops


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _persist_plan(plan: grm.GuardedMutationPlan, workspace: Path) -> None:
    """Rewrite the durable plan file atomically.

    Uses a tmp-file + os.replace for atomicity. The controller
    and the executor both call this at every state transition.
    """
    path = grm.guarded_ref_mutation_plan_path(workspace, plan.mutation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(plan.to_json())
    tmp.replace(path)


def _read_remote_ref_via_query(
    remote_ref_path: Path, ref: str
) -> Optional[str]:
    """Read the ref's current SHA from a local path that points
    to the remote bare repo.

    Returns None if the path does not exist, the ref does not
    exist, or the ref cannot be read for any reason. The
    caller (reconcile) classifies None as INDETERMINATE.
    """
    if not remote_ref_path.exists():
        return None
    try:
        return ops.read_ref(remote_ref_path, ref)
    except ops.GuardedRefError:
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class GuardedMutationOrchestrator:
    """Coordinate the lifecycle of a GuardedMutationPlan.

    The orchestrator owns the durable plan file. Each public
    method updates the plan and persists it. The primary run
    lease is held by the controller's existing supervisor-lock
    primitive; this module does not touch it.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        plan: grm.GuardedMutationPlan,
    ):
        self.workspace = Path(workspace)
        self.plan = plan
        grm.validate_plan(self.plan)
        self.plan_path = grm.guarded_ref_mutation_plan_path(
            self.workspace, plan.mutation_id
        )

    # ------------------------------------------------------------------
    # Prepare (write the PREPARED plan)
    # ------------------------------------------------------------------

    def prepare(self) -> grm.GuardedMutationPlan:
        """Persist the plan in PREPARED state. Must be called
        before any other operation. Idempotent: if the plan is
        already PREPARED, returns it unchanged."""
        if self.plan.status != grm.LifecycleState.PREPARED.value:
            grm.assert_allowed_transition(
                grm.LifecycleState(self.plan.status),
                grm.LifecycleState.PREPARED,
            )
        self.plan.status = grm.LifecycleState.PREPARED.value
        self.plan.created_at = _utcnow_iso()
        _persist_plan(self.plan, self.workspace)
        return self.plan

    # ------------------------------------------------------------------
    # Execute (run the guarded mutation)
    # ------------------------------------------------------------------

    def execute(
        self,
        *,
        local_repo: Path,
        remote_ref_path: Optional[Path] = None,
    ) -> grm.GuardedMutationPlan:
        """Execute the guarded mutation against `local_repo`.

        local_repo is the path of the LOCAL repo from which
        the mutation is performed. remote_ref_path is the path
        of the REMOTE repo (e.g. a local bare repo) used for
        reconciliation. If None, the local repo is used as
        the remote (useful for update_ref and delete_ref).

        The plan transitions PREPARED -> EXECUTING -> RECONCILING
        -> terminal state. The plan is persisted at each
        transition.
        """
        # PREPARED -> EXECUTING
        if self.plan.status != grm.LifecycleState.PREPARED.value:
            raise grm.LifecycleError(
                f"execute() requires PREPARED; current status is "
                f"{self.plan.status!r}"
            )
        self.plan.status = grm.LifecycleState.EXECUTING.value
        _persist_plan(self.plan, self.workspace)

        # Perform the guarded mutation.
        op = grm.Operation(self.plan.operation)
        result = self._do_execute(local_repo, op)

        # EXECUTING -> RECONCILING
        self.plan.status = grm.LifecycleState.RECONCILING.value
        _persist_plan(self.plan, self.workspace)

        # Read the actual ref for reconciliation.
        remote = remote_ref_path or local_repo
        actual = _read_remote_ref_via_query(remote, self.plan.target_ref)

        # Reconcile. The domain uses None to mean "the ref does
        # not exist"; the adapter has already translated any
        # empty-string from git rev-parse to None.
        outcome = grm.reconcile(
            expected_before_sha=self.plan.expected_before_sha,
            desired_after_sha=self.plan.desired_after_sha,
            actual_ref_sha=actual,
        )
        self.plan.status = outcome.value
        self.plan.last_reconciled_at = _utcnow_iso()
        self.plan.terminal_evidence = (
            f"actual_ref_sha={actual!r}, "
            f"stdout={result.stdout[:200]!r}, "
            f"stderr={result.stderr[:200]!r}, "
            f"returncode={result.returncode}"
        )
        _persist_plan(self.plan, self.workspace)
        return self.plan

    def _do_execute(
        self, local_repo: Path, op: grm.Operation
    ) -> ops.RefMutationResult:
        """Perform the actual git operation."""
        if op == grm.Operation.PUSH_REMOTE:
            # Push to the remote. The remote is the local_repo
            # (used as a local bare repo for integration tests).
            # In production, the controller passes a remote URL.
            return ops.guarded_push(
                repo=local_repo,
                remote="origin",
                ref=self.plan.target_ref,
                expected_remote_sha=self.plan.expected_before_sha,
                new_local_sha=self.plan.desired_after_sha,
            )
        elif op == grm.Operation.UPDATE_LOCAL:
            return ops.guarded_update_ref(
                repo=local_repo,
                ref=self.plan.target_ref,
                new_sha=self.plan.desired_after_sha or "",
                expected_old_sha=self.plan.expected_before_sha,
            )
        elif op == grm.Operation.CREATE_LOCAL:
            return ops.guarded_create_ref(
                repo=local_repo,
                ref=self.plan.target_ref,
                new_sha=self.plan.desired_after_sha or "",
            )
        elif op == grm.Operation.DELETE_LOCAL:
            return ops.guarded_delete_ref(
                repo=local_repo,
                ref=self.plan.target_ref,
                expected_old_sha=self.plan.expected_before_sha,
            )
        elif op == grm.Operation.GRAPHQL_UPDATE_REFS:
            raise NotImplementedError(
                "GRAPHQL_UPDATE_REFS is not implemented in Layer 4; "
                "Layer 5+ replaces the executor packet call."
            )
        else:
            raise ValueError(f"unknown operation: {op}")

    # ------------------------------------------------------------------
    # Reconcile (re-read the actual ref and update the plan)
    # ------------------------------------------------------------------

    def reconcile(
        self,
        *,
        remote_ref_path: Path,
    ) -> grm.GuardedMutationPlan:
        """Re-read the actual ref and update the plan with the
        new outcome.

        Allowed from PREPARED, EXECUTING, RECONCILING,
        NOT_APPLIED, or INDETERMINATE. NOT_APPLIED can
        transition to RECONCILING (the actual is still at
        expected_before so the retry is safe). INDETERMINATE
        can transition to RECONCILING (reconcile retry only).
        """
        current = grm.LifecycleState(self.plan.status)
        if current not in TERMINAL_STATES_FOR_RECONCILE:
            grm.assert_allowed_transition(
                current, grm.LifecycleState.RECONCILING
            )
        # Transition to RECONCILING.
        self.plan.status = grm.LifecycleState.RECONCILING.value
        _persist_plan(self.plan, self.workspace)

        # Re-read the actual ref.
        actual = _read_remote_ref_via_query(
            remote_ref_path, self.plan.target_ref
        )
        outcome = grm.reconcile(
            expected_before_sha=self.plan.expected_before_sha,
            desired_after_sha=self.plan.desired_after_sha,
            actual_ref_sha=actual,
        )
        self.plan.status = outcome.value
        self.plan.last_reconciled_at = _utcnow_iso()
        self.plan.terminal_evidence = (
            f"actual_ref_sha={actual!r}"
        )
        _persist_plan(self.plan, self.workspace)
        return self.plan


# States from which reconcile() can be called.
TERMINAL_STATES_FOR_RECONCILE = frozenset({
    grm.LifecycleState.PREPARED,
    grm.LifecycleState.EXECUTING,
    grm.LifecycleState.RECONCILING,
    grm.LifecycleState.NOT_APPLIED,
    grm.LifecycleState.INDETERMINATE,
})


__all__ = (
    "GuardedMutationOrchestrator",
)
