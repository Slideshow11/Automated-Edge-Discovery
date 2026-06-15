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

The helpers in this module are:

- :func:`validate_checkpoint` — STRUCTURAL only. Verifies that
  the fields are present, well-formed, and consistent with
  the documented schema. It does NOT compare the PR head
  against the primary head (they are intentionally different
  in a normal feature-branch PR). Head-drift detection
  lives in :func:`validate_resume_observations`.
- :func:`validate_resume_observations` — head-drift detector.
  Compares the SHAs the runner just fetched against the
  recorded ``last_verified_pr_head`` and
  ``last_verified_primary_head``.
- :func:`next_action_from_checkpoint` — returns the next
  string the runner should act on, or ``None`` if the
  checkpoint says to stop. Structural / lookups only.
- :func:`checkpoint_requires_operator` — returns ``True``
  iff the checkpoint cannot be safely auto-resumed and a
  human must intervene. Structural / lookups only.

The resume driver in the runner is expected to be:

```python
structural_errors = validate_checkpoint(state)
if structural_errors:
    return "HOLD_OPERATOR_REQUIRED"
head_errors = validate_resume_observations(
    state, observed_pr_head, observed_primary_head,
)
if head_errors:
    return "HOLD_HEAD_CHANGED"
return next_action_from_checkpoint(state)
```
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
    """Return a list of structural error messages for ``state``.

    The validator is structural only: it checks that fields are
    present, well-formed, and internally consistent with the
    documented schema. It does NOT compare the PR head against
    the primary head — those are intentionally different
    SHAs in a normal feature-branch PR, where the PR head
    sits on a feature branch and the primary sits on
    ``origin/main``. Head-drift detection lives in
    :func:`validate_resume_observations`.

    A checkpoint is considered structurally invalid when:

    - any required field is missing or None
    - ``pr_number`` is not a positive int
    - the ``terminal_state`` is set but is not a recognized
      terminal state

    The function is lenient: it emits human-readable error
    strings, not exception types, so callers can collect
    errors and report them in a single message.
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

    # terminal_state, if set, must be a recognized terminal state
    if state.terminal_state is not None and not is_terminal_lifecycle_state(
        state.terminal_state
    ):
        errors.append(
            f"checkpoint terminal_state {state.terminal_state!r} is not a "
            "recognized terminal state"
        )

    return errors


def validate_resume_observations(
    state: CheckpointState,
    observed_pr_head: str,
    observed_primary_head: str,
) -> List[str]:
    """Return a list of head-drift error messages (empty = no drift).

    The function is the head-drift detector that
    :func:`validate_checkpoint` deliberately does NOT do. The
    runner calls this with the SHAs it just fetched from the
    GitHub API (``observed_pr_head``) and from the protected
    primary worktree (``observed_primary_head``).

    Errors:

    - ``"PR head changed"`` if ``observed_pr_head`` differs
      from ``state.last_verified_pr_head``.
    - ``"primary worktree head changed"`` if
      ``observed_primary_head`` differs from
      ``state.last_verified_primary_head``.

    The function compares each recorded head against its own
    observation. It does NOT compare the PR head against the
    primary head — those are different SHAs by design in a
    normal feature-branch PR.

    A ``None`` or empty-string observation is treated as
    "skip this check" so the runner can call this function
    with only one observation populated (e.g. PR head only,
    if the primary worktree is not yet reachable).
    """
    errors: List[str] = []

    if (
        isinstance(observed_pr_head, str)
        and observed_pr_head
        and isinstance(state.last_verified_pr_head, str)
        and state.last_verified_pr_head
    ):
        if observed_pr_head != state.last_verified_pr_head:
            errors.append(
                "PR head changed: "
                f"last_verified_pr_head={state.last_verified_pr_head[:12]} "
                f"but observed={observed_pr_head[:12]}"
            )

    if (
        isinstance(observed_primary_head, str)
        and observed_primary_head
        and isinstance(state.last_verified_primary_head, str)
        and state.last_verified_primary_head
    ):
        if observed_primary_head != state.last_verified_primary_head:
            errors.append(
                "primary worktree head changed: "
                f"last_verified_primary_head={state.last_verified_primary_head[:12]} "
                f"but observed={observed_primary_head[:12]}"
            )

    return errors


def next_action_from_checkpoint(state: CheckpointState) -> Optional[str]:
    """Return the next string the runner should act on, or ``None``.

    The function is the canonical resume helper. A future
    Humphry/Telegram runner that ingests this output will
    drive its next step from this string.

    This helper is STRUCTURAL / lookups only — it does not
    detect head drift. The runner is expected to call
    :func:`validate_resume_observations` first and surface
    ``HOLD_HEAD_CHANGED`` itself.

    Decision tree:

    - If the checkpoint has a recognized ``terminal_state`` →
      return ``None``. The runner is done.
    - If the checkpoint is stale (no phase, no next_action,
      no terminal_state) → return
      ``"HOLD_OPERATOR_REQUIRED"``. The runner must NOT emit
      a silent continuation.
    - If the checkpoint has a ``next_action`` → return
      ``next_action`` verbatim.
    - Otherwise → ``"HOLD_OPERATOR_REQUIRED"``.
    """
    # Terminal state: runner is done.
    if state.terminal_state is not None:
        if is_terminal_lifecycle_state(state.terminal_state):
            return None
        # Unknown terminal_state — treat as a hold.
        return "HOLD_OPERATOR_REQUIRED"

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

    This helper is STRUCTURAL / lookups only — it does not
    detect head drift. The runner is expected to call
    :func:`validate_resume_observations` first and surface
    ``HOLD_HEAD_CHANGED`` itself.

    A checkpoint requires operator intervention when:

    - the checkpoint has a terminal_state the runner does not
      recognize
    - the checkpoint is stale or incomplete (no phase, no
      next_action, no terminal_state)
    - the checkpoint has unresolved threads AND no
      ``next_action`` (the runner cannot auto-resolve threads)
    """
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
