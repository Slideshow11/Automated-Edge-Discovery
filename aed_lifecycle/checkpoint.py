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

from .no_stall import (
    is_completed_terminal_state,
    is_terminal_lifecycle_state,
    is_valid_next_action,
)


# Required fields. A checkpoint missing any of these is invalid.
# A "required" field is one that the dataclass types as
# non-Optional and that the protocol needs to make any
# meaningful use of the checkpoint. Optional fields (those
# typed ``Optional[...]`` in the dataclass) may be None.
# The validator emits a structural error only for missing
# required fields, empty required strings, bad pr_number, or
# an unrecognized terminal_state.
_REQUIRED_STRING_FIELDS: Tuple[str, ...] = (
    "repo",
    "branch",
    "current_head",
)
_REQUIRED_LIST_FIELDS: Tuple[str, ...] = (
    "completed_phases",
    "pending_actions",
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
    unresolved_thread_ids: List[str] = field(default_factory=list)
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

    - any required string field (``repo``, ``branch``,
      ``current_head``) is missing or empty
    - ``pr_number`` is missing, not an int, or not positive
    - any required list field is missing or contains
      non-string items
    - the ``terminal_state`` is set but is not a recognized
      terminal state

    The function is lenient: it emits human-readable error
    strings, not exception types, so callers can collect
    errors and report them in a single message. Optional
    fields (``phase``, ``next_phase``, ``next_action``,
    ``pending_actions``, ``last_verified_primary_head``,
    ``last_verified_pr_head``, ``authorized_thread_ids``,
    ``unresolved_thread_ids``, ``terminal_state``,
    ``updated_at``) may be None — the dataclass marks them
    as ``Optional[...]`` with sensible defaults.
    """
    errors: List[str] = []

    for fname in _REQUIRED_STRING_FIELDS:
        if not hasattr(state, fname):
            errors.append(f"checkpoint missing required field {fname!r}")
            continue
        val = getattr(state, fname)
        if val is None:
            errors.append(f"checkpoint field {fname!r} is None")
            continue
        # Fix A (Codex 3414948246): required string fields must be
        # actual strings. Other types (int, float, bool, list, dict,
        # tuple) are rejected with a type-mismatch error so a typo
        # or a non-string serialization cannot silently slip through
        # and become a non-resumable checkpoint at runtime.
        if not isinstance(val, str):
            errors.append(
                f"checkpoint field {fname!r} must be a string, "
                f"got {type(val).__name__}"
            )
            continue
        if not val.strip():
            errors.append(f"checkpoint field {fname!r} is empty string")
            continue

    for fname in _REQUIRED_LIST_FIELDS:
        if not hasattr(state, fname):
            errors.append(f"checkpoint missing required list field {fname!r}")
            continue
        val = getattr(state, fname)
        if not isinstance(val, list):
            errors.append(f"checkpoint field {fname!r} must be list[str]")
            continue
        if not all(isinstance(x, str) for x in val):
            errors.append(
                f"checkpoint field {fname!r} must be list[str]"
            )

    # pr_number must be a positive int. Guard with hasattr
    # first because a partially deserialized checkpoint
    # may be missing this attribute entirely; the other
    # required fields already use the hasattr pattern
    # above. Raising AttributeError here would crash
    # before the runner can surface HOLD_OPERATOR_REQUIRED.
    if not hasattr(state, "pr_number"):
        errors.append("checkpoint missing required field 'pr_number'")
    elif not isinstance(state.pr_number, int) or isinstance(state.pr_number, bool):
        errors.append("checkpoint pr_number must be a positive int")
    elif state.pr_number <= 0:
        errors.append("checkpoint pr_number must be a positive int")

    # next_action, if present, must be a non-empty, non-placeholder
    # string. The runner consumes this value directly; a non-string
    # would be an unsafe resume path. The validity check uses the
    # canonical :func:`is_valid_next_action` helper so this validator
    # and :func:`next_action_from_checkpoint` and
    # :func:`checkpoint_requires_operator` all agree.
    #
    # Fix AC (Codex 3443570411): Guard the ``next_action``
    # attribute access with ``getattr(..., None)`` because
    # a partially deserialized checkpoint may be missing
    # this attribute entirely; the other required fields
    # already use the ``hasattr`` / guarded pattern. Raising
    # ``AttributeError`` here would crash before the runner
    # can surface ``HOLD_OPERATOR_REQUIRED``.
    next_action_value = getattr(state, "next_action", None)
    if next_action_value is not None and not is_valid_next_action(
        next_action_value
    ):
        if not isinstance(next_action_value, str):
            errors.append(
                "checkpoint field 'next_action' must be a string, "
                f"got {type(next_action_value).__name__}"
            )
        elif not next_action_value.strip():
            errors.append(
                "checkpoint field 'next_action' is empty or "
                "whitespace-only"
            )
        else:
            errors.append(
                f"checkpoint field 'next_action' is a placeholder "
                f"value: {next_action_value!r}"
            )

    # terminal_state, if set, must be a recognized terminal
    # state. Same ``getattr`` guard pattern as
    # ``next_action`` above (Fix AC, Codex 3443570411).
    terminal_state_value = getattr(state, "terminal_state", None)
    if terminal_state_value is not None and not is_terminal_lifecycle_state(
        terminal_state_value
    ):
        errors.append(
            f"checkpoint terminal_state {terminal_state_value!r} is not a "
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

    A ``None`` or empty-string RECORDED head (i.e. the
    checkpoint was persisted without a recorded PR head or
    primary head) is treated as a missing-recorded-head
    error, with one exception: a checkpoint that has a
    RECOGNIZED ``terminal_state`` (e.g. ``"MERGED"``,
    ``"HOLD_OPERATOR_REQUIRED"``) is parked. The runner
    cannot continue, and the recorded-head-missing error
    would force a spurious ``HOLD_HEAD_CHANGED`` instead of
    the documented terminal-state verdict. The recorded-head
    requirement is therefore SKIPPED for checkpoints with a
    recognized terminal state. Fix for Codex 3415335299:
    a terminal checkpoint persisted without
    ``last_verified_*_head`` fields must not surface
    ``HOLD_HEAD_CHANGED``. Resuming a non-terminal
    checkpoint that lost its recorded heads is still
    unsafe and is reported as
    ``"recorded PR head missing"`` /
    ``"recorded primary head missing"``, which the runner
    surfaces as ``HOLD_OPERATOR_REQUIRED``.

    Terminal-state check order:

    1. If ``state.terminal_state`` is set but NOT
       recognized, return ``["unknown terminal state"]``
       so the runner surfaces ``HOLD_OPERATOR_REQUIRED``.
    2. If ``state.terminal_state`` is a recognized
       COMPLETED terminal state (``MERGED``, ``FAILED``,
       ``PR_MERGED_AND_CLOSED_OUT``), skip the
       recorded-head checks entirely and return ``[]``.
       The runner stops on the completed terminal state
       and the operator has already authorized the
       closeout; no head-drift detection is needed.
    3. If ``state.terminal_state`` is a recognized but
       NON-COMPLETED (parked/hold) terminal state
       (``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION``,
       ``HOLD_OPERATOR_REQUIRED``, all ``HOLD_*`` schema
       states), FALL THROUGH to the recorded-head checks
       below. Fix G (Codex 3417849218): a parked
       checkpoint such as
       ``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION`` must
       still re-verify the observed PR/primary heads
       against the recorded values; otherwise a future
       runner could surface a stale
       "merge ready / awaiting authorization" verdict
       while the PR head or protected primary head has
       moved. The terminal-state verdict is still
       authoritative (the operator must acknowledge the
       hold or authorize the closeout), but the head
       checks must run first so a moved head produces
       ``HOLD_HEAD_CHANGED`` instead of a stale
       terminal-state verdict.
    4. Otherwise (no ``terminal_state``), run the
       recorded-head checks as documented.
    """
    errors: List[str] = []

    # Terminal-state handling.
    # Fix AD (Codex 3443646997): guard optional
    # attribute access with ``getattr(..., None)`` so a
    # partially deserialized checkpoint cannot raise
    # ``AttributeError`` here. Read each optional field
    # once at the top and use the local variable
    # throughout. ``validate_checkpoint`` already ran
    # before this point and guarantees the required
    # string fields (``repo``, ``branch``,
    # ``current_head``) are present; the ``getattr``
    # guards here are belt-and-braces for
    # namespace/dict-like objects that bypass the
    # validator.
    terminal_state_value = getattr(state, "terminal_state", None)
    last_verified_pr_head_value = getattr(
        state, "last_verified_pr_head", None
    )
    last_verified_primary_head_value = getattr(
        state, "last_verified_primary_head", None
    )
    if terminal_state_value is not None:
        if not is_terminal_lifecycle_state(terminal_state_value):
            # Unknown terminal_state — surface as a hold, do
            # not fall through to the head-drift checks (the
            # runner would otherwise report HOLD_HEAD_CHANGED
            # instead of the unknown-terminal hold).
            errors.append(
                f"unknown terminal_state {terminal_state_value!r} "
                "is not a recognized terminal state"
            )
            return errors
        # Recognized terminal_state. Completed terminal
        # states short-circuit to [] — the runner stops on
        # the terminal state and the operator is not
        # required to intervene. Parked/hold terminal states
        # fall through to the head checks below.
        if is_completed_terminal_state(terminal_state_value):
            return []
        # else: parked/hold terminal state — continue to
        # the recorded-head checks.

    # Recorded PR head must be present and non-empty before
    # we can compare it against the observation. A missing
    # recorded head is a hard error, not a silent skip.
    if not (
        isinstance(last_verified_pr_head_value, str)
        and last_verified_pr_head_value
    ):
        errors.append(
            "recorded PR head missing: "
            "last_verified_pr_head is None or empty"
        )
    elif (
        isinstance(observed_pr_head, str)
        and observed_pr_head
        and observed_pr_head != last_verified_pr_head_value
    ):
        errors.append(
            "PR head changed: "
            f"last_verified_pr_head={last_verified_pr_head_value[:12]} "
            f"but observed={observed_pr_head[:12]}"
        )

    # Recorded primary head must be present and non-empty
    # before we can compare it against the observation.
    if not (
        isinstance(last_verified_primary_head_value, str)
        and last_verified_primary_head_value
    ):
        errors.append(
            "recorded primary head missing: "
            "last_verified_primary_head is None or empty"
        )
    elif (
        isinstance(observed_primary_head, str)
        and observed_primary_head
        and observed_primary_head != last_verified_primary_head_value
    ):
        errors.append(
            "primary worktree head changed: "
            f"last_verified_primary_head={last_verified_primary_head_value[:12]} "
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

    Fix AD (Codex 3443646997): read ``terminal_state`` and
    ``next_action`` via ``getattr(..., None)`` at the top so
    a partially deserialized checkpoint object that lacks
    these attributes (e.g. a decoded namespace/dict-like
    object rather than a ``CheckpointState`` instance with
    dataclass defaults) does NOT raise ``AttributeError``.
    The previous direct ``state.terminal_state`` /
    ``state.next_action`` accesses would crash before the
    runner could surface ``HOLD_OPERATOR_REQUIRED`` for a
    corrupt checkpoint, even though
    :func:`validate_checkpoint` was already safe (Fix Z +
    Fix AC). This brings the downstream resume helpers
    into line with the validator.
    """
    # Fix AD (Codex 3443646997): guard optional
    # attribute access with ``getattr(..., None)`` so a
    # partially deserialized checkpoint cannot raise
    # ``AttributeError`` here.
    terminal_state_value = getattr(state, "terminal_state", None)
    next_action_value = getattr(state, "next_action", None)
    phase_value = getattr(state, "phase", None)

    # Terminal state: runner is done.
    if terminal_state_value is not None:
        if is_terminal_lifecycle_state(terminal_state_value):
            return None
        # Unknown terminal_state — treat as a hold.
        return "HOLD_OPERATOR_REQUIRED"

    # Stale / empty checkpoint — no phase, no next_action, no
    # terminal_state. The runner must NOT emit a silent
    # continuation; it must surface the hold to the operator.
    if not phase_value and not next_action_value:
        return "HOLD_OPERATOR_REQUIRED"

    if next_action_value:
        # Defensive: a non-string / empty / placeholder
        # next_action must never be returned to the runner. If
        # validation was skipped or bypassed, this is the
        # last-line guard. The validity check is delegated to
        # the canonical :func:`is_valid_next_action` helper.
        if not is_valid_next_action(next_action_value):
            return "HOLD_OPERATOR_REQUIRED"
        return next_action_value

    return "HOLD_OPERATOR_REQUIRED"


def checkpoint_requires_operator(state: CheckpointState) -> bool:
    """Return True iff the checkpoint cannot be safely auto-resumed.

    This helper is STRUCTURAL / lookups only — it does not
    detect head drift. The runner is expected to call
    :func:`validate_resume_observations` first and surface
    ``HOLD_HEAD_CHANGED`` itself.

    Fix I (Codex 3417410596): A checkpoint with structurally
    invalid required fields (``repo``, ``branch``,
    ``current_head``, ``pr_number``, list fields) must never
    auto-resume, regardless of ``next_action`` validity or
    ``terminal_state``. The previous implementation fell
    through to ``False`` when ``next_action`` was valid even
    when the structural fields were missing, empty, or
    wrong-typed — telling the caller that operator
    intervention was NOT required for a checkpoint the
    runner could not actually use. This is dangerous: a
    runner that trusts ``checkpoint_requires_operator(...) ==
    False`` would attempt to resume a checkpoint with
    ``repo=""`` and crash. The new implementation calls
    :func:`validate_checkpoint` at the top, before any other
    branch, and returns True on any structural error. This
    gate precedes the completed-terminal short-circuit, the
    parked-terminal branch, and the valid-next-action
    fast path. There is no exception for completed
    terminal states: a checkpoint with ``terminal_state=
    "MERGED"`` and ``repo=""`` is still structurally
    broken, and the operator must acknowledge the broken
    closeout before the runner can act on the checkpoint.

    A checkpoint requires operator intervention when:

    - structural validation fails (any of ``repo``,
      ``branch``, ``current_head``, ``pr_number``,
      required list fields, ``next_action`` when present).
      Fix I (Codex 3417410596). This check runs FIRST so a
      structurally broken checkpoint never falls through
      to the no-operator path.
    - the checkpoint has a terminal_state the runner does not
      recognize
    - the checkpoint is parked on a recognized-but-non-completed
      terminal state (e.g. ``HOLD_OPERATOR_REQUIRED``,
      ``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION``, any
      ``HOLD_*`` schema state) — the runner stops on the
      terminal state but the operator must acknowledge
      the hold or authorize the closeout, even when
      ``next_action`` is non-empty
    - the checkpoint is stale or incomplete (no phase, no
      next_action, no terminal_state)
    - the checkpoint has unresolved threads AND no
      ``next_action`` (the runner cannot auto-resolve threads)
    - the checkpoint has a ``next_action`` that is present but
      not a valid executable action (None / non-string / empty
      / whitespace / placeholder). The same
      :func:`is_valid_next_action` helper used by
      :func:`validate_checkpoint` and
      :func:`next_action_from_checkpoint` is used here so the
      three helpers cannot disagree on what counts as a usable
      next action. Fix D (Codex 3414948261 + 3415107663): this
      case must trigger ``HOLD_OPERATOR_REQUIRED`` rather than
      fall through to ``False`` and allow silent auto-resume.
      ``next_action=None`` with a populated ``phase`` (e.g.
      ``"PHASE_5_CI_POLL"``) is also an absent-action case
      and must require the operator — the runner cannot
      auto-resume a phase that has no executable next step.

    Fix A (Codex 3415657744 + 3415785873): a checkpoint
    that is parked on a RECOGNIZED COMPLETED terminal
    state (``"MERGED"``, ``"FAILED"``,
    ``"PR_MERGED_AND_CLOSED_OUT"``) does NOT require
    operator intervention, PROVIDED the checkpoint is
    structurally valid. The runner stops on the
    terminal state and the operator has already
    authorized the close. The check is short-circuited
    AFTER the structural-validity gate (Fix I) and
    BEFORE the parked-terminal check. A checkpoint that
    is parked on a recognized but NON-COMPLETED terminal
    state (``"HOLD_OPERATOR_REQUIRED"``,
    ``"MERGE_READY_AWAITING_HUMAN_AUTHORIZATION"``, any
    ``HOLD_*`` schema state) DOES require operator
    intervention, even when ``next_action`` is non-empty,
    because the operator must acknowledge the hold or
    authorize the closeout.
    """
    # Fix I (Codex 3417410596): structural validation is a
    # hard prerequisite for auto-resume. Run
    # ``validate_checkpoint`` first; any error means the
    # checkpoint is unusable and the operator must
    # acknowledge the broken state before the runner
    # acts on it. This gate precedes the
    # completed-terminal short-circuit, the
    # parked-terminal branch, and the valid-next-action
    # fast path — no exceptions for completed terminal
    # states, no exceptions for non-empty next_action.
    if validate_checkpoint(state):
        return True

    # Fix AD (Codex 3443646997): guard optional
    # attribute access with ``getattr(..., None)`` so a
    # partially deserialized checkpoint cannot raise
    # ``AttributeError`` here. Read each optional field
    # once at the top and use the local variable
    # throughout.
    terminal_state_value = getattr(state, "terminal_state", None)
    next_action_value = getattr(state, "next_action", None)
    phase_value = getattr(state, "phase", None)
    unresolved_thread_ids_value = getattr(
        state, "unresolved_thread_ids", None
    )

    # Unknown terminal state is a hold.
    if terminal_state_value is not None and not is_terminal_lifecycle_state(
        terminal_state_value
    ):
        return True

    # Fix A (Codex 3415657744): completed terminal
    # states short-circuit to False (provided the
    # checkpoint is structurally valid — see Fix I
    # above). The runner stops on the terminal state
    # and the operator is NOT required to intervene.
    # Completed states: ``MERGED``, ``FAILED``,
    # ``PR_MERGED_AND_CLOSED_OUT``.
    if is_completed_terminal_state(terminal_state_value):
        return False

    # Fix A (Codex 3415785873): recognized but
    # non-completed (parked/hold) terminal states
    # ALWAYS require operator intervention, even when
    # ``next_action`` is non-empty. The runner stops on
    # the terminal state via ``next_action_from_checkpoint``,
    # but the operator must acknowledge the hold or
    # authorize the closeout. Parked states:
    # ``HOLD_OPERATOR_REQUIRED``,
    # ``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION``, all
    # ``HOLD_*`` schema states. This branch fires
    # AFTER the completed short-circuit and BEFORE
    # the next-action validity check, so a parked
    # terminal state with a non-empty next_action
    # correctly returns True.
    if terminal_state_value is not None and is_terminal_lifecycle_state(
        terminal_state_value
    ):
        return True

    # next_action is required to be present AND valid. The
    # canonical :func:`is_valid_next_action` helper returns
    # False for None, non-strings, empty / whitespace strings,
    # and placeholders. ``is_valid_next_action(None)`` is
    # False, so a ``next_action=None`` with a populated
    # ``phase`` requires the operator (Fix D, Codex
    # 3415107663).
    if not is_valid_next_action(next_action_value):
        return True

    # Stale / empty checkpoint.
    if (
        terminal_state_value is None
        and not phase_value
        and not next_action_value
    ):
        return True

    # Unresolved threads with no next_action: the runner cannot
    # auto-resolve threads; the operator must.
    if unresolved_thread_ids_value and not next_action_value:
        return True

    return False
