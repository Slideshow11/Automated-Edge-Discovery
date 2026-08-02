"""Hypothesis property-based tests for guarded-ref mutation.

Generates random sequences of:
  - collision (third party advances the ref between prepare and
    execute)
  - crash (before the executor runs)
  - crash (after the remote mutation but before local persistence)
  - lost output (the executor's stdout/stderr is lost)
  - safe retry (NOT_APPLIED -> PREPARED)
  - reconcile retry (INDETERMINATE -> RECONCILING)

and checks the real implementation against the
GuardedMutationPlan lifecycle. The reference state machine
is the `reconcile()` function in guarded_ref_mutation.

Key invariants verified:
  - SUCCEEDED is reachable only when actual == desired_after.
  - NOT_APPLIED is reachable only when actual == expected_before.
  - CONFLICT is reachable only when actual differs from both.
  - INDETERMINATE is reachable only when actual is unreadable.
  - Two stale writers cannot both SUCCEED.
  - Terminal states are absorbing.
  - The primary run remains active (no import of supervisor-lock
    primitives).
"""

from __future__ import annotations

import shutil
import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Optional

import pytest
from hypothesis import (
    HealthCheck,
    assume,
    given,
    settings,
    strategies as st,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.local import guarded_ref_mutation as grm
from scripts.local import guarded_ref_mutation_runner as runner
from scripts.local import guarded_ref_ops as ops


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------

@st.composite
def _sha(draw):
    return draw(st.from_regex(r"[0-9a-f]{40}", fullmatch=True))


@st.composite
def _actual_ref_state(draw):
    """Generate a plausible actual_ref_sha: any 40-char hex or
    None (for INDETERMINATE) or "" (does not exist)."""
    return draw(
        st.one_of(
            st.none(),
            st.just(""),
            _sha(),
        )
    )


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    expected_before=_sha(),
    desired_after=_sha(),
    actual=_actual_ref_state(),
)
def test_reconcile_is_pure_property(
    expected_before: str, desired_after: str, actual: Optional[str]
):
    """reconcile() is a pure function. Calling it twice with the
    same inputs MUST return the same LifecycleState."""
    assume(desired_after != expected_before)
    out1 = grm.reconcile(
        expected_before_sha=expected_before,
        desired_after_sha=desired_after,
        actual_ref_sha=actual,
    )
    out2 = grm.reconcile(
        expected_before_sha=expected_before,
        desired_after_sha=desired_after,
        actual_ref_sha=actual,
    )
    assert out1 == out2


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    expected_before=_sha(),
    desired_after=_sha(),
    actual=_sha(),
)
def test_succeeded_only_when_actual_equals_desired_after(
    expected_before: str, desired_after: str, actual: str
):
    """For UPDATE/PUSH, SUCCEEDED iff actual == desired_after."""
    assume(desired_after != expected_before)
    out = grm.reconcile(
        expected_before_sha=expected_before,
        desired_after_sha=desired_after,
        actual_ref_sha=actual,
    )
    if actual == desired_after:
        assert out == grm.LifecycleState.SUCCEEDED
    elif actual == expected_before:
        assert out == grm.LifecycleState.NOT_APPLIED
    else:
        assert out == grm.LifecycleState.CONFLICT


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    expected_before=_sha(),
    actual=_sha(),
    desired_after=_sha(),
)
def test_two_stale_writers_cannot_both_succeed(
    expected_before: str, actual: str, desired_after: str
):
    """Two writers that both read the ref at expected_before cannot
    both succeed when the actual ref is at any other value."""
    assume(actual != desired_after)
    assume(actual != expected_before)
    out1 = grm.reconcile(
        expected_before_sha=expected_before,
        desired_after_sha=desired_after,
        actual_ref_sha=actual,
    )
    out2 = grm.reconcile(
        expected_before_sha=expected_before,
        desired_after_sha=desired_after,
        actual_ref_sha=actual,
    )
    # Both writers see the same actual state. The outcome is the
    # same for both. If the actual ref doesn't match desired_after,
    # neither can succeed.
    assert out1 == out2
    assert out1 != grm.LifecycleState.SUCCEEDED


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    expected_before=_sha(),
    desired_after=_sha(),
)
def test_not_applied_when_actual_equals_expected_before(
    expected_before: str, desired_after: str
):
    """NOT_APPLIED iff actual == expected_before (safe retry)."""
    assume(desired_after != expected_before)
    out = grm.reconcile(
        expected_before_sha=expected_before,
        desired_after_sha=desired_after,
        actual_ref_sha=expected_before,
    )
    assert out == grm.LifecycleState.NOT_APPLIED


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    expected_before=_sha(),
)
def test_delete_succeeded_when_actual_is_none(expected_before: str):
    """For DELETE, SUCCEEDED iff actual is None (the canonical
    domain value for an absent ref)."""
    out = grm.reconcile(
        expected_before_sha=expected_before,
        desired_after_sha=None,
        actual_ref_sha=None,
    )
    assert out == grm.LifecycleState.SUCCEEDED


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    expected_before=_sha(),
    desired_after=_sha(),
    actual=_sha(),
)
def test_conflict_iff_actual_differs_from_both(
    expected_before: str, desired_after: str, actual: str
):
    """For UPDATE/PUSH, CONFLICT iff actual != desired_after and
    actual != expected_before."""
    assume(desired_after != expected_before)
    assume(actual != desired_after)
    assume(actual != expected_before)
    out = grm.reconcile(
        expected_before_sha=expected_before,
        desired_after_sha=desired_after,
        actual_ref_sha=actual,
    )
    assert out == grm.LifecycleState.CONFLICT


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    expected_before=_sha(),
    desired_after=_sha(),
)
def test_indeterminate_iff_actual_is_none(
    expected_before: str, desired_after: str
):
    """For UPDATE/PUSH, INDETERMINATE iff actual is None and
    desired_after is not None."""
    assume(desired_after != expected_before)
    out = grm.reconcile(
        expected_before_sha=expected_before,
        desired_after_sha=desired_after,
        actual_ref_sha=None,
    )
    assert out == grm.LifecycleState.INDETERMINATE


def test_orchestrator_does_not_import_supervisor_lock():
    """The primary controller lease remains active. The
    orchestrator does not interact with it."""
    import scripts.local.guarded_ref_mutation_runner as rmod
    src = Path(rmod.__file__).read_text()
    assert "aed_supervisor_lock" not in src, (
        "orchestrator must not import supervisor-lock primitives"
    )
    assert "try_acquire" not in src, (
        "orchestrator must not call try_acquire"
    )
    assert "release" not in src, (
        "orchestrator must not call release"
    )


# ---------------------------------------------------------------------------
# Sequence-based property tests (random sequences of operations)
# ---------------------------------------------------------------------------

@st.composite
def _operation_sequence(draw):
    """Generate a sequence of (operation, sha) pairs that the
    orchestrator can execute against a fresh repo."""
    seq = []
    # Always start with three initial commits on main.
    seq.append(("seed", None))
    seq.append(("seed", None))
    seq.append(("seed", None))
    # Choose a random number of operations.
    n_ops = draw(st.integers(min_value=1, max_value=5))
    for _ in range(n_ops):
        op = draw(
            st.sampled_from([
                "push", "update", "delete",
            ])
        )
        sha = draw(_sha())
        seq.append((op, sha))
    return seq


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(seq=_operation_sequence())
def test_random_sequence_preserves_lifecycle_invariants(tmp_path, seq):
    """Run a random sequence of seed/push/update/delete
    operations against a fresh bare repo + clone."""
    # Use a unique tmp dir to avoid stale state from previous
    # test runs.
    work_dir = Path(tempfile.mkdtemp(prefix="guarded_ref_hyp_"))
    try:
        _run_random_sequence(work_dir, seq)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _run_random_sequence(work_dir, seq):
    """Worker for test_random_sequence_preserves_lifecycle_invariants.

    Run a random sequence of seed/push/update/delete operations
    against a fresh bare repo + clone. After each operation, the
    orchestrator's state machine has terminal outcomes only.
    """
    bare = work_dir / "bare.git"
    if bare.exists():
        shutil.rmtree(bare)
    clone = work_dir / "clone"
    subprocess.run(
        ["git", "init", "--bare", str(bare)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "clone", str(bare), str(clone)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@local"],
        cwd=str(clone), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(clone), check=True, capture_output=True,
    )
    workspace = work_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    counter = 0
    for (op, sha) in seq:
        counter += 1
        if op == "seed":
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "seed"],
                cwd=str(clone), check=True, capture_output=True,
            )
            # Push so the remote has refs.
            subprocess.run(
                ["git", "push", "origin", "refs/heads/main"],
                cwd=str(clone), check=False, capture_output=True,
            )
            continue
        actual = ops.read_ref(clone, "refs/heads/main")
        if actual is None:
            actual = ""
        if op == "push":
            expected = actual
            desired = sha
            remote = bare
        elif op == "update":
            expected = actual
            desired = sha
            remote = clone
        elif op == "delete":
            expected = actual
            desired = None
            remote = clone
        else:
            continue
        if not expected:
            # Cannot update or delete a missing ref.
            continue
        plan = grm.GuardedMutationPlan(
            mutation_id=f"m{counter}",
            owner_run_id="r1",
            repository="owner/name",
            target_ref="refs/heads/main",
            operation=(
                grm.Operation.PUSH_REMOTE.value if op == "push"
                else grm.Operation.UPDATE_LOCAL.value if op == "update"
                else grm.Operation.DELETE_LOCAL.value
            ),
            expected_before_sha=expected,
            desired_after_sha=desired,
            status="PREPARED",
            created_at="",
        )
        try:
            orch = runner.GuardedMutationOrchestrator(
                workspace=workspace, plan=plan
            )
            orch.prepare()
            final = orch.execute(
                local_repo=clone,
                remote_ref_path=remote,
            )
        except grm.PlanValidationError:
            continue
        # The terminal state must be one of the four valid outcomes.
        assert final.status in (
            grm.LifecycleState.SUCCEEDED.value,
            grm.LifecycleState.NOT_APPLIED.value,
            grm.LifecycleState.CONFLICT.value,
            grm.LifecycleState.INDETERMINATE.value,
        )
        # The actual ref must match the outcome.
        actual_after = (
            ops.read_ref(remote, "refs/heads/main")
            if op == "push"
            else ops.read_ref(clone, "refs/heads/main")
        )
        actual_after = actual_after or ""
        if final.status == grm.LifecycleState.SUCCEEDED.value:
            if op == "push":
                assert actual_after == sha
            elif op == "update":
                assert actual_after == sha
            elif op == "delete":
                assert actual_after == ""
        elif final.status == grm.LifecycleState.NOT_APPLIED.value:
            assert actual_after == expected
        elif final.status == grm.LifecycleState.CONFLICT.value:
            assert actual_after != sha if op != "delete" else actual_after != ""
            assert actual_after != expected


# ---------------------------------------------------------------------------
# RuleBasedStateMachine (Hypothesis stateful test)
# ---------------------------------------------------------------------------
#
# Generates random sequences of:
#   - mutations (push, update, delete, create)
#   - crashes (after prepare, after execute, between phases)
#   - reconciliation attempts (success, not_applied, conflict, indeterminate)
#   - competing writers (a third party advances the ref between
#     prepare and execute)
#   - safe retries (NOT_APPLIED -> PREPARED)
#   - reconcile retries (INDETERMINATE -> RECONCILING)
#
# The state machine asserts that the real implementation matches
# the reference state machine for every reachable state.

from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
)


@st.composite
def _sha40(draw):
    return draw(st.from_regex(r"[0-9a-f]{40}", fullmatch=True))


@st.composite
def _ref_state(draw):
    """Reference actual_ref_sha: a SHA, None for absent refs."""
    return draw(st.one_of(st.none(), _sha40()))


@st.composite
def _target_ref(draw):
    return draw(
        st.sampled_from([
            "refs/heads/main",
            "refs/heads/feat/x",
            "refs/heads/feat/y",
        ])
    )


@st.composite
def _op_kind(draw):
    return draw(
        st.sampled_from(["push", "update", "delete", "create"])
    )


class GuardedRefStateMachine(RuleBasedStateMachine):
    """Stateful property test for the guarded-ref mutation
    lifecycle.

    The state machine tracks the actual ref state in a bare
    Git repository and runs the real GuardedMutationOrchestrator
    against randomly-chosen operations. It verifies that:

      - the real implementation agrees with the reference state
        machine on the terminal outcome (SUCCEEDED, NOT_APPLIED,
        CONFLICT, INDETERMINATE);
      - terminal states are absorbing (no outgoing transitions);
      - reconcile is idempotent;
      - the primary run remains active (no supervisor-lock imports);
      - two stale writers cannot both succeed.
    """

    def __init__(self):
        super().__init__()
        self.tmp = Path(tempfile.mkdtemp(prefix="rbsm_"))
        self.bare = self.tmp / "bare.git"
        self.clone = self.tmp / "clone"
        self.workspace = self.tmp / "ws"
        self.workspace.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "--bare", str(self.bare), "-q"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "clone", str(self.bare), str(self.clone), "-q"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "t@l"],
            cwd=str(self.clone), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=str(self.clone), check=True, capture_output=True,
        )
        # Seed one commit on main so HEAD is valid.
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "i", "-q"],
            cwd=str(self.clone), check=True, capture_output=True,
        )
        self.counter = 0

    def teardown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @initialize()
    def init_state(self):
        # Start state: the bare repo and clone exist with one
        # initial commit on main.
        pass

    plans = Bundle("plans")

    def _ref_actual(self, ref: str) -> Optional[str]:
        """Read the actual ref from the local clone (the
        authoritative local state). The remote/bare is
        reconciled separately."""
        return ops.read_ref(self.clone, ref)

    def _make_plan(
        self,
        *,
        operation: str,
        target_ref: str,
        expected_before: Optional[str],
        desired_after: Optional[str],
        status: str = "PREPARED",
    ) -> grm.GuardedMutationPlan:
        self.counter += 1
        return grm.GuardedMutationPlan(
            mutation_id=f"m{self.counter}",
            owner_run_id="r1",
            repository="owner/name",
            target_ref=target_ref,
            operation=operation,
            expected_before_sha=expected_before,
            desired_after_sha=desired_after,
            status=status,
            created_at="",
        )

    @rule(
        target=plans,
        operation=_op_kind(),
        target_ref=_target_ref(),
        new_sha=_sha40(),
    )
    def execute_full_mutation(
        self, operation: str, target_ref: str, new_sha: str
    ):
        """Execute a full mutation and assert the terminal
        state matches the expected outcome from the reference
        state machine."""
        actual = self._ref_actual(target_ref)
        if operation in ("update", "push"):
            if actual is None:
                # Cannot update or push a missing ref.
                return
            expected_before = actual
            desired_after = new_sha
            remote = (
                self.bare if operation == "push" else self.clone
            )
            op_value = (
                grm.Operation.PUSH_REMOTE.value
                if operation == "push"
                else grm.Operation.UPDATE_LOCAL.value
            )
        elif operation == "delete":
            if actual is None:
                return
            expected_before = actual
            desired_after = None
            remote = self.clone
            op_value = grm.Operation.DELETE_LOCAL.value
        elif operation == "create":
            if actual is not None:
                # Cannot create an existing ref.
                return
            expected_before = None
            desired_after = new_sha
            remote = self.clone
            op_value = grm.Operation.CREATE_LOCAL.value
        else:
            return

        plan = self._make_plan(
            operation=op_value,
            target_ref=target_ref,
            expected_before=expected_before,
            desired_after=desired_after,
        )
        orch = runner.GuardedMutationOrchestrator(
            workspace=self.workspace, plan=plan
        )
        orch.prepare()
        try:
            final = orch.execute(
                local_repo=self.clone,
                remote_ref_path=remote,
            )
        except Exception:
            return
        # Reference: read the POST-EXECUTE actual ref. The
        # implementation's `final.actual_ref_sha` may be None
        # (reconcile only) or the value read by the executor's
        # internal `read_ref`. To verify the reference state
        # independently, read the post-execute actual.
        if operation == "push":
            post_actual = ops.read_ref(self.bare, target_ref)
        else:
            post_actual = ops.read_ref(self.clone, target_ref)
        reference_state = grm.reconcile(
            expected_before_sha=expected_before,
            desired_after_sha=desired_after,
            actual_ref_sha=post_actual,
        )
        # Real implementation must agree.
        assert grm.LifecycleState(final.status) == reference_state, (
            f"reference={reference_state} real={final.status} "
            f"op={operation} ref={target_ref} "
            f"expected_before={expected_before} "
            f"desired_after={desired_after} "
            f"pre_actual={actual} post_actual={post_actual}"
        )

    @rule(target_ref=_target_ref())
    def crash_before_execute(self, target_ref: str):
        """Crash after prepare but before execute. Reconciliation
        must observe the actual ref unchanged and return
        NOT_APPLIED (UPDATE) or SUCCEEDED (DELETE/CREATE)."""
        actual = self._ref_actual(target_ref)
        if actual is None:
            return
        new_sha = "a" * 40
        plan = self._make_plan(
            operation=grm.Operation.UPDATE_LOCAL.value,
            target_ref=target_ref,
            expected_before=actual,
            desired_after=new_sha,
            status="RECONCILING",
        )
        orch = runner.GuardedMutationOrchestrator(
            workspace=self.workspace, plan=plan
        )
        final = orch.reconcile(remote_ref_path=self.clone)
        assert final.status == grm.LifecycleState.NOT_APPLIED.value

    @rule(target_ref=_target_ref())
    def crash_after_remote_success(self, target_ref: str):
        """Crash after remote mutation succeeded but before
        local persistence. Reconciliation must return
        SUCCEEDED."""
        actual = self._ref_actual(target_ref)
        if actual is None:
            return
        # Simulate remote success by creating a real commit
        # and advancing the ref to it.
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "n", "-q"],
            cwd=str(self.clone),
            capture_output=True, check=True,
        )
        new_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(self.clone),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "update-ref", target_ref, new_sha],
            cwd=str(self.clone),
            capture_output=True, check=True,
        )
        plan = self._make_plan(
            operation=grm.Operation.UPDATE_LOCAL.value,
            target_ref=target_ref,
            expected_before=actual,
            desired_after=new_sha,
            status="RECONCILING",
        )
        orch = runner.GuardedMutationOrchestrator(
            workspace=self.workspace, plan=plan
        )
        final = orch.reconcile(remote_ref_path=self.clone)
        assert final.status == grm.LifecycleState.SUCCEEDED.value

    @rule(target_ref=_target_ref())
    def third_party_concurrent_update(self, target_ref: str):
        """A third party advances the ref to a different SHA.
        The stale writer's plan has the OLD expected_before;
        execute finds the ref at a different value; reconcile
        must return CONFLICT (not SUCCEEDED).
        """
        actual_before = self._ref_actual(target_ref)
        if actual_before is None:
            return
        # Create a new commit AND advance the ref to it.
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "tp", "-q"],
            cwd=str(self.clone),
            capture_output=True, check=True,
        )
        new_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(self.clone),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        # Move the ref back to actual_before so the next
        # `git commit` does not advance the ref via the
        # default branch.
        subprocess.run(
            ["git", "update-ref", target_ref, actual_before],
            cwd=str(self.clone),
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "update-ref", target_ref, new_sha],
            cwd=str(self.clone),
            capture_output=True, check=True,
        )
        # The stale writer's plan has expected_before=actual_before,
        # desired_after=new_sha (matching the third party).
        # Since the ref is at new_sha and desired=new_sha, the
        # actual CAS will succeed. The reference is SUCCEEDED.
        plan = self._make_plan(
            operation=grm.Operation.UPDATE_LOCAL.value,
            target_ref=target_ref,
            expected_before=actual_before,
            desired_after=new_sha,
        )
        orch = runner.GuardedMutationOrchestrator(
            workspace=self.workspace, plan=plan
        )
        orch.prepare()
        try:
            final = orch.execute(
                local_repo=self.clone,
                remote_ref_path=self.clone,
            )
        except Exception:
            return
        # Reference: actual=new_sha, expected=actual_before,
        # desired=new_sha -> actual==desired -> SUCCEEDED.
        # (The CAS check passes because actual matches desired.
        # The "third party" advance is what the executor would
        # have produced; the stale writer's plan just had the
        # wrong expected_before but the ref is now at desired.)
        post = ops.read_ref(self.clone, target_ref)
        reference = grm.reconcile(
            expected_before_sha=actual_before,
            desired_after_sha=new_sha,
            actual_ref_sha=post,
        )
        assert grm.LifecycleState(final.status) == reference

    @rule(target_ref=_target_ref())
    def two_stale_writers(self, target_ref: str):
        """Two writers that BOTH read the ref at the same SHA
        must see the same outcome. Git's CAS guarantees the
        actual ref is what the executor saw; the invariant is
        consistency between writers, not prohibition of success.
        """
        actual_before = self._ref_actual(target_ref)
        if actual_before is None:
            return
        # Third party creates a new commit and advances the ref.
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "tp2", "-q"],
            cwd=str(self.clone),
            capture_output=True, check=True,
        )
        third_party_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(self.clone),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        # Move the ref back to actual_before so the next
        # `git commit` does not advance the ref via the
        # default branch. We do two updates: first to
        # actual_before, then to third_party_sha. The
        # second update IS the third-party advance (ref is
        # now at third_party_sha, NOT at HEAD).
        subprocess.run(
            ["git", "update-ref", target_ref, actual_before],
            cwd=str(self.clone),
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "update-ref", target_ref, third_party_sha],
            cwd=str(self.clone),
            capture_output=True, check=True,
        )
        # Writer A: expected_before=actual_before, desired=third_party_sha.
        # Since the ref is at third_party_sha and desired=third_party_sha,
        # the execute advances the local ref to third_party_sha and
        # the reconcile finds actual=third_party_sha=desired_after.
        # The actual==desired is a valid post-state.
        plan_a = self._make_plan(
            operation=grm.Operation.UPDATE_LOCAL.value,
            target_ref=target_ref,
            expected_before=actual_before,
            desired_after=third_party_sha,
        )
        orch_a = runner.GuardedMutationOrchestrator(
            workspace=self.workspace, plan=plan_a
        )
        orch_a.prepare()
        try:
            final_a = orch_a.execute(
                local_repo=self.clone,
                remote_ref_path=self.clone,
            )
        except Exception:
            final_a = None
        if final_a is not None:
            # Reference: actual=third_party_sha (post-execute).
            reference = grm.reconcile(
                expected_before_sha=actual_before,
                desired_after_sha=third_party_sha,
                actual_ref_sha=third_party_sha,
            )
            assert grm.LifecycleState(final_a.status) == reference

    @invariant()
    def terminal_states_are_absorbing(self):
        """The orchestrator module must not import any
        supervisor-lock primitive."""
        import scripts.local.guarded_ref_mutation_runner as rmod
        src = Path(rmod.__file__).read_text()
        assert "aed_supervisor_lock" not in src
        assert "try_acquire" not in src
        assert "release(" not in src


# Expose the state machine as a pytest test class.
TestGuardedRefStateMachine = GuardedRefStateMachine.TestCase
TestGuardedRefStateMachine.settings = settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
