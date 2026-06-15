"""AED checkpoint dataclass and pure resume helpers (v1).

A checkpoint is a serializable snapshot of the run state for a
single Humphry/Telegram PR run. The shape is pinned by the
PR #405 spec:

- ``repo``
- ``pr_number``
- ``branch``
- ``current_head``
- ``phase``
- ``completed_phases``
- ``next_phase``
- ``next_action``
- ``pending_actions``
- ``last_verified_primary_head``
- ``last_verified_pr_head``
- ``authorized_thread_ids``
- ``unresolved_thread_ids``
- ``terminal_state``
- ``updated_at``

The three pure helpers in this module are:

- :func:`validate_checkpoint` — returns a list of human-readable
  error strings (empty list = valid).
- :func:`next_action_from_checkpoint` — returns the next
  string the runner should act on, or ``None`` if the
  checkpoint says to stop.
- :func:`checkpoint_requires_operator` — returns ``True`` iff
  the checkpoint cannot be safely auto-resumed and a human
  must intervene.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .no_stall import is_terminal_lifecycle_state


# Required fields. A checkpoint missing any of these is invalid.
# Optional fields (next_action, terminal_state, updated_at) are
# allowed to be None — the validator will flag a fully empty
# checkpoint as operator-required, but does not flag the field
# itself.
_REQUIRED_CHECKPOINT_FIELDS: Tuple[str, ...] = (
    "repo",
    "pr_number",
    "branch",
    "current_head",
    "phase",
    "completed_phases",
    "next_phase",
    "pending_actions",
    "last_verified_primary_head",
    "last_verified_pr_head",
    "authorized_thread_ids",
    "unresolved_thread_ids",
)


@dataclass
class CheckpointState:
    """Pure data snapshot for a single PR run.

    All fields are primitives or lists of primitives. The
    dataclass is JSON-round-trippable; the harness may persist
    it via ``dataclasses.asdict`` and re-load it via the
    constructor.

    Field semantics:

    - ``current_head`` — the SHA the runner last *observed* for
      the PR head. Used for head-changed detection.
    - ``last_verified_pr_head`` — the SHA the runner last
      *verified* against the canonical PR headRefOid. If the
      observed ``current_head`` differs from this, the PR head
      moved and the checkpoint must not auto-resume.
    - ``last_verified_primary_head`` — the SHA the runner last
      verified for the protected primary worktree (typically
      ``/home/max/Automated-Edge-Discovery`` on
      ``origin/main``). A mismatch means origin/main moved and
      the runner must rebase / re-verify before continuing.
    - ``authorized_thread_ids`` — list of review-thread IDs the
      operator has explicitly authorized the runner to resolve.
      The runner must not resolve threads outside this list.
    - ``unresolved_thread_ids`` — list of review-thread IDs that
      were open on the verified head. The runner must not merge
      while this list is non-empty.
    - ``terminal_state`` — if set, the checkpoint is parked;
      ``next_action_from_checkpoint`` returns ``None`` and the
      runner reports the terminal state to the operator.
    """

    repo: str
    pr_number: int
    branch: str
    current_head: str
    phase: Optional[str]
    completed_phases: List[str] = field(default_factory=list)
    next_phase: Optional[str] = None
    next_action: Optional[str] = None
    pending_actions: List[str] = field(default_factory=list)
    last_verified_primary_head: Optional[str] = None
    last_verified_pr_head: Optional[str] = None
    authorized_thread_ids: List[str] = field(default_factory=list)
    unresolved_thread_ids: List[str] = None  # type: ignore[assignment]
    terminal_state: Optional[str] = None
    updated_at: Optional[str] = None


def _is_sha_like(value: object) -> bool:
    """Lenient SHA shape check (40 hex chars or shorter)."""
    if not isinstance(value, str) or not value:
        return False
    if len(value) < 7:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in value)


def validate_checkpoint(state: CheckpointState) -> List[str]:
    """Return a list of error messages for ``state`` (empty = valid).

    The validator is intentionally lenient: it emits
    human-readable error strings, not exception types, so
    callers can collect errors and report them in a single
    message. A checkpoint is considered invalid when:

    - any required field is missing or None
    - the PR head moved (current_head != last_verified_pr_head)
    - the primary worktree head moved
      (current_head == last_verified_pr_head but the primary
      SHA differs from current_head — this is the "primary
      moved out from under us" case)
    - the terminal_state is set but is not a recognized
      terminal state
    - pr_number is not a positive int
    """
    errors: List[str] = []

    for fname in _REQUIRED_CHECKPOINT_FIELDS:
        if not hasattr(state, fname):
            errors.append(f"checkpoint missing required field {fname!r}")
            continue
        val = getattr(state, fname)
        if val is None:
            errors.append(f"checkpoint field {fname!r} is None")
            continue
        if isinstance(val, str) and not val.strip():
            errors.append(f"checkpoint field {fname!r} is empty string")
            continue
        if isinstance(val, list) and len(val) == 0 and fname in {
            "completed_phases",
            "pending_actions",
        }:
            # Empty completed_phases is fine (first run); empty
            # pending_actions is fine (work is done).
            continue
        if isinstance(val, list) and fname in {
            "authorized_thread_ids",
            "unresolved_thread_ids",
        }:
            # Empty is fine — the runner still needs to verify
            # both lists are lists of strings.
            if not all(isinstance(x, str) for x in val):
                errors.append(
                    f"checkpoint field {fname!r} must be list[str]"
                )
            continue

    # pr_number must be a positive int
    if not isinstance(state.pr_number, int) or isinstance(state.pr_number, bool):
        errors.append("checkpoint pr_number must be a positive int")
    elif state.pr_number <= 0:
        errors.append("checkpoint pr_number must be a positive int")

    # PR head change: current_head must match last_verified_pr_head
    if (
        isinstance(state.current_head, str)
        and isinstance(state.last_verified_pr_head, str)
        and state.current_head
        and state.last_verified_pr_head
    ):
        if state.current_head != state.last_verified_pr_head:
            errors.append(
                "PR head changed: "
                f"last_verified_pr_head={state.last_verified_pr_head[:12]} "
                f"but current_head={state.current_head[:12]}"
            )

    # Primary head change: last_verified_primary_head is the
    # SHA of the protected primary. If it differs from
    # current_head, the protected primary moved.
    if (
        isinstance(state.current_head, str)
        and isinstance(state.last_verified_primary_head, str)
        and state.current_head
        and state.last_verified_primary_head
    ):
        if state.last_verified_primary_head != state.current_head:
            errors.append(
                "primary worktree head changed: "
                f"last_verified_primary_head={state.last_verified_primary_head[:12]} "
                f"but current_head={state.current_head[:12]}"
            )

    # terminal_state, if set, must be a recognized terminal state
    if state.terminal_state is not None and not is_terminal_lifecycle_state(
        state.terminal_state
    ):
        errors.append(
            f"checkpoint terminal_state {state.terminal_state!r} is not a "
            "recognized terminal state"
        )

    return errors


def next_action_from_checkpoint(state: CheckpointState) -> Optional[str]:
    """Return the next string the runner should act on, or ``None``.

    The function is the canonical resume helper. A future
    Humphry/Telegram runner that ingests this output will
    drive its next step from this string.

    Decision tree:

    - If the checkpoint has a recognized ``terminal_state`` →
      return ``None``. The runner is done.
    - If the checkpoint has a ``next_action`` and there are
      no validation errors that would force a hold → return
      ``next_action`` verbatim.
    - If the checkpoint has unresolved_thread_ids and a
      ``next_action`` → return the next_action (the runner
      should pause on the thread gate, but the next_action
      still describes the next step).
    - If the checkpoint is stale (no phase, no next_action,
      no terminal_state) → return
      ``"HOLD_OPERATOR_REQUIRED"``. The runner must NOT emit
      a silent continuation.
    - If the PR head changed or the primary head changed →
      return ``"HOLD_HEAD_CHANGED"``. The runner must NOT
      silently continue on a stale head.
    """
    # Terminal state: runner is done.
    if state.terminal_state is not None:
        if is_terminal_lifecycle_state(state.terminal_state):
            return None
        # Unknown terminal_state — treat as a hold.
        return "HOLD_OPERATOR_REQUIRED"

    # Head changes take priority over next_action emission.
    if (
        isinstance(state.current_head, str)
        and isinstance(state.last_verified_pr_head, str)
        and state.current_head
        and state.last_verified_pr_head
        and state.current_head != state.last_verified_pr_head
    ):
        return "HOLD_HEAD_CHANGED"

    if (
        isinstance(state.current_head, str)
        and isinstance(state.last_verified_primary_head, str)
        and state.current_head
        and state.last_verified_primary_head
        and state.last_verified_primary_head != state.current_head
    ):
        return "HOLD_HEAD_CHANGED"

    # Stale / empty checkpoint — no phase, no next_action, no
    # terminal_state. The runner must NOT emit a silent
    # continuation; it must surface the hold to the operator.
    if not state.phase and not state.next_action:
        return "HOLD_OPERATOR_REQUIRED"

    if state.next_action:
        return state.next_action

    return "HOLD_OPERATOR_REQUIRED"


def checkpoint_requires_operator(state: CheckpointState) -> bool:
    """Return True iff the checkpoint cannot be safely auto-resumed.

    A checkpoint requires operator intervention when:

    - the PR head moved (current_head != last_verified_pr_head)
    - the primary worktree head moved
    - the checkpoint has a terminal_state the runner does not
      recognize
    - the checkpoint is stale or incomplete (no phase, no
      next_action, no terminal_state)
    - the checkpoint has unresolved threads AND no
      ``next_action`` (the runner cannot auto-resolve threads)
    """
    # Head changes always require operator intervention.
    if (
        isinstance(state.current_head, str)
        and isinstance(state.last_verified_pr_head, str)
        and state.current_head
        and state.last_verified_pr_head
        and state.current_head != state.last_verified_pr_head
    ):
        return True
    if (
        isinstance(state.current_head, str)
        and isinstance(state.last_verified_primary_head, str)
        and state.current_head
        and state.last_verified_primary_head
        and state.last_verified_primary_head != state.current_head
    ):
        return True

    # Unknown terminal state is a hold.
    if state.terminal_state is not None and not is_terminal_lifecycle_state(
        state.terminal_state
    ):
        return True

    # Stale / empty checkpoint.
    if (
        state.terminal_state is None
        and not state.phase
        and not state.next_action
    ):
        return True

    # Unresolved threads with no next_action: the runner cannot
    # auto-resolve threads; the operator must.
    if state.unresolved_thread_ids and not state.next_action:
        return True

    return False
