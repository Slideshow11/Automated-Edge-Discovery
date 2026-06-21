"""AED no-stall lifecycle helpers (v1).

This module is the regression guard for the phase-header-only
failure mode that surfaced in PR #404 and prior runs. The
helpers here are pure functions over a small, stdlib-only
vocabulary:

- :data:`TERMINAL_LIFECYCLE_STATES` — the canonical set of
  terminal states a Humphry/Telegram runner can land on.
- :func:`is_terminal_lifecycle_state` — predicate.
- :func:`classify_humphry_message_for_stall` — classifies a
  final-output text into one of six categories that a runner
  can branch on.

This module does not import any AED-internal policy or
harness code. It is intentionally a leaf module so the
runner can call it from any process boundary.

Public API
----------

- :data:`OK_TERMINAL`
- :data:`OK_PROGRESS_WITH_NEXT_ACTION`
- :data:`STALL_PHASE_HEADER_ONLY`
- :data:`STALL_WAITING_FOR_CONTINUE`
- :data:`STALL_NO_TERMINAL_STATE`
- :data:`STALL_NO_CHECKPOINT`
- :func:`is_terminal_lifecycle_state`
- :func:`classify_humphry_message_for_stall`
- :data:`TERMINAL_LIFECYCLE_STATES`
"""
from __future__ import annotations

import re
from typing import FrozenSet, Optional


# ---------------------------------------------------------------------------
# Public classification constants
# ---------------------------------------------------------------------------


OK_TERMINAL = "OK_TERMINAL"
"""The final output carries a recognized terminal lifecycle state."""

OK_PROGRESS_WITH_NEXT_ACTION = "OK_PROGRESS_WITH_NEXT_ACTION"
"""The final output is mid-phase progress and explicitly carries
BOTH a ``next_action`` AND a value-bearing checkpoint path so the
runner can resume from where it left off.

The classifier requires both pieces of evidence. A final output
that has a valid ``next_action`` but no value-bearing checkpoint
falls through to ``STALL_NO_CHECKPOINT`` (or
``STALL_NO_TERMINAL_STATE`` when the message has no checkpoint
mention at all); a final output that has a value-bearing
checkpoint but no ``next_action`` also falls through to
``STALL_NO_TERMINAL_STATE`` (the runner has a resume point but
nothing executable to act on). The strict both-required contract
is documented here for the AED no-stall protocol. Fix L
(Codex 3420442393) explicitly aligned this docstring with the
strict classifier implementation: the previous wording
(``optionally a checkpoint path``) was misleading because the
classifier was already requiring both fields, and a
compliant-looking progress message that omitted the checkpoint
classified as ``STALL_NO_CHECKPOINT`` instead of the
``OK_PROGRESS_WITH_NEXT_ACTION`` a future runner would expect.
"""

STALL_PHASE_HEADER_ONLY = "STALL_PHASE_HEADER_ONLY"
"""The final output is just a phase header (``Starting PHASE 1 —``
or ``Now PHASE 8 — ...``) with no checkpoint, no terminal state,
and no ``next_action``."""

STALL_WAITING_FOR_CONTINUE = "STALL_WAITING_FOR_CONTINUE"
"""The final output is a prompt-style question asking the
operator to type ``Continue`` (or ``yes``) before proceeding."""

STALL_NO_TERMINAL_STATE = "STALL_NO_TERMINAL_STATE"
"""The final output is a generic progress note with no terminal
state and no ``next_action``. The runner cannot tell whether the
work is done, paused, or stuck."""

STALL_NO_CHECKPOINT = "STALL_NO_CHECKPOINT"
"""The final output references a checkpoint but does not name one
explicitly, has no ``next_action``, and no terminal state."""


# ---------------------------------------------------------------------------
# Terminal lifecycle state registry
# ---------------------------------------------------------------------------


# The canonical set of terminal states a Humphry/Telegram runner
# can land on. Frozen at runtime; mutating it would defeat the
# purpose of an explicit registry.
#
# The set is the union of:
#   1. The original PR #405 task spec (the HOLD_* names that the
#      spec asked us to register, plus MERGED, FAILED, and
#      MERGE_READY_AWAITING_HUMAN_AUTHORIZATION).
#   2. Every canonical HOLD_* and ``terminal`` lifecycle state
#      from ``schemas/aed_lifecycle_states_v1.json`` (the
#      canonical AED lifecycle registry), plus the parked-when-
#      ready state ``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION``
#      and the two "parked" informational/mutation-pending
#      states that a runner may legitimately stop on
#      (``CODEX_CLEAN_PASS`` and ``PR_MERGED_PENDING_CLOSEOUT``).
#
# Drift between this set and the canonical registry is caught by
# ``TestCanonicalRegistryCoverage`` in tests/test_aed_no_stall.py.
# The test reads the schema and asserts coverage, so a future
# registry addition fails the suite until the set is updated.
TERMINAL_LIFECYCLE_STATES: FrozenSet[str] = frozenset(
    {
        # ---- Original PR #405 task spec ----
        # Successful close — not in the schema, parked by the
        # no-stall protocol as the runner-done signal.
        "MERGED",
        # Awaiting human authorization — runner is done, work is
        # paused for the operator. A terminal "parked" state.
        # The schema marks this as category=ready (not hold or
        # terminal) so it must be added by the protocol.
        "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
        # Hard holds (per the PR #405 task spec)
        "HOLD_NEW_CODEX_THREAD",
        "HOLD_CODEX_RESPONSE_PENDING",
        "HOLD_PR_CI_PENDING",
        "HOLD_PR_CI_FAILED",
        "HOLD_SCOPE_GUARD_FAILED",
        "HOLD_UNAUTHORIZED_THREAD_INVENTORY",
        "HOLD_BRANCH_POLICY_BLOCKED",
        "HOLD_HEAD_CHANGED",
        "HOLD_ISOLATED_WORKSPACE_DIRTY",
        "HOLD_UNEXPECTED_LOCAL_CHANGES",
        "HOLD_OPERATOR_REQUIRED",
        # Generic failure close — not in the schema.
        "FAILED",
        # ---- Canonical AED lifecycle registry
        #      (schemas/aed_lifecycle_states_v1.json),
        #      category=hold only ----
        "HOLD_MAIN_HEAD_MISMATCH",
        "HOLD_NEW_ACTIVE_THREAD",
        "HOLD_MERGE_STATE_BLOCKED",
        "HOLD_PRE_MERGE_CONDITION_FAILED",
        "HOLD_POST_MERGE_CI_PENDING",
        "HOLD_POST_MERGE_CI_FAILED",
        "HOLD_POST_MERGE_CI_NOT_OBSERVED",
        "AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR",
        "HOLD_RESUME_CHECKPOINT_NEEDED",
        "HOLD_PR_NOT_OPEN",
        # ---- Canonical AED lifecycle registry, category=terminal ----
        "PR_MERGED_AND_CLOSED_OUT",
        # ---- Intentional exclusions (Fix A, Codex 3415107647) ----
        # NOT included as terminal/parked:
        #   "CODEX_CLEAN_PASS" — category=informational; the schema
        #     notes that re-classification with merge/thread state
        #     is required before authorizing merge. Including it
        #     as terminal would let a final message assert it and
        #     cause the runner to stop while closeout or
        #     re-classification is still required.
        #   "CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED" — category=
        #     mutation_pending; resolve-only authorization /
        #     follow-up is still required. The runner must NOT
        #     stop on this state.
        #   "PR_MERGED_PENDING_CLOSEOUT" — category=
        #     mutation_pending; audit_append and worktree_remove
        #     are still allowed. The runner must NOT stop on
        #     this state.
        #   "NOT_RUN" — category=informational; the initial
        #     pre-governance state. Not a parked final state.
    }
)


def is_terminal_lifecycle_state(state: object) -> bool:
    """Return True iff ``state`` is a recognized terminal lifecycle state.

    The function is strict: anything that is not a non-empty string
    in :data:`TERMINAL_LIFECYCLE_STATES` returns False. A non-string
    return value (e.g. ``None``) cannot be a terminal state, and an
    empty string is not a terminal state. Abbreviated state names
    (e.g. ``"MERGE_READY"``) are rejected; the runner must use the
    canonical identifier.

    Fix A (Codex 3415107647): The strict terminal/parked
    vocabulary is the union of (a) every canonical
    ``category=hold`` and ``category=terminal`` state in
    ``schemas/aed_lifecycle_states_v1.json`` and (b) the
    spec-required extras that are NOT in the schema
    (``MERGED``, ``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION``,
    ``FAILED``). ``CODEX_CLEAN_PASS`` (informational),
    ``CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED``
    (mutation_pending), ``PR_MERGED_PENDING_CLOSEOUT``
    (mutation_pending), and ``NOT_RUN`` (informational) are
    intentionally EXCLUDED: those states are not parked
    final states, and treating them as terminal would let a
    final message stop the runner while closeout or
    re-classification is still required.
    """
    if not isinstance(state, str):
        return False
    if not state:
        return False
    return state in TERMINAL_LIFECYCLE_STATES


# Canonical coverage helpers used by the schema-driven tests
# and the strict registry guard. The terminal/parked
# vocabulary is the union of (a) every ``category=hold`` and
# ``category=terminal`` state in the schema and (b) the
# spec-required extras. The non-terminal vocabulary is the
# complement — every other schema state, which must NOT
# classify as terminal.
_SPEC_REQUIRED_EXTRAS: FrozenSet[str] = frozenset(
    {
        # Successful close / failure close / human-authorization
        # parked. These are runner-done signals, not canonical
        # schema states.
        "MERGED",
        "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
        "FAILED",
    }
)


def _load_schema_states() -> dict:
    """Load the canonical lifecycle registry from the schema.

    Lazy / best-effort: reads
    ``schemas/aed_lifecycle_states_v1.json`` relative to the
    repo root. Returns an empty dict if the file is missing
    or unreadable (the calling test is opt-in). This is the
    single canonical entry point so all coverage checks read
    the schema the same way.
    """
    import json
    from pathlib import Path

    try:
        schema_path = (
            Path(__file__).resolve().parent.parent
            / "schemas"
            / "aed_lifecycle_states_v1.json"
        )
        return json.loads(schema_path.read_text()).get("states", {})
    except (OSError, ValueError):
        return {}


def _schema_terminal_or_parked_states() -> FrozenSet[str]:
    """Return the schema states that are category=hold or category=terminal.

    The function is the source of truth for what the schema
    considers a parked / held final state. Used by the
    coverage test to assert every schema hold/terminal state
    is in :data:`TERMINAL_LIFECYCLE_STATES`, and to assert
    every non-hold/non-terminal schema state is NOT in
    :data:`TERMINAL_LIFECYCLE_STATES`.
    """
    states = _load_schema_states()
    return frozenset(
        name
        for name, defn in states.items()
        if defn.get("category") in {"hold", "terminal"}
    )


def _schema_non_terminal_states() -> FrozenSet[str]:
    """Return the schema states that are NOT category=hold or category=terminal.

    The function is the source of truth for what the schema
    considers a non-final, in-progress, or
    re-classification-required state. Used by the coverage
    test to assert none of them are in
    :data:`TERMINAL_LIFECYCLE_STATES`. Includes
    ``NOT_RUN`` (informational), ``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION``
    (ready), ``PR_MERGED_PENDING_CLOSEOUT`` and
    ``CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED``
    (mutation_pending), and ``CODEX_CLEAN_PASS``
    (informational).
    """
    states = _load_schema_states()
    return frozenset(
        name
        for name, defn in states.items()
        if defn.get("category") not in {"hold", "terminal"}
    )


# Completed / closed terminal states that mark the
# checkpoint as fully done. The runner stops on these
# states; the operator is NOT required to intervene (Fix
# A, Codex 3415657744).
#
# The set is the union of:
#   - every canonical ``category=terminal`` schema state
#     (``PR_MERGED_AND_CLOSED_OUT``)
#   - the spec-required completed extras that are NOT in
#     the schema: ``MERGED`` (successful close) and
#     ``FAILED`` (generic failure close)
#
# Parked/hold terminal states (e.g.
# ``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION``,
# ``HOLD_OPERATOR_REQUIRED``, all ``HOLD_*`` schema
# states) are EXCLUDED: they still require operator
# attention. The runner does not auto-resume against a
# parked/awaiting-human checkpoint, even if
# ``next_action`` is None.
_COMPLETED_TERMINAL_STATES: FrozenSet[str] = frozenset(
    {
        # Spec-required completed extras (not in schema).
        "MERGED",
        "FAILED",
        # Canonical schema category=terminal state.
        "PR_MERGED_AND_CLOSED_OUT",
    }
)


def is_completed_terminal_state(state: object) -> bool:
    """Return True iff ``state`` is a completed/closed terminal state.

    A completed terminal state marks the checkpoint as
    fully done — the runner stops and the operator is NOT
    required to intervene. This is a strict subset of
    :func:`is_terminal_lifecycle_state`: parked/awaiting
    terminal states (e.g.
    ``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION``,
    ``HOLD_OPERATOR_REQUIRED``, all ``HOLD_*`` states)
    are NOT completed terminal states and still require
    operator attention.

    Fix A (Codex 3415657744): this distinction is what
    ``checkpoint_requires_operator`` uses to decide
    whether a terminal-state checkpoint should require
    operator intervention. ``MERGED`` and
    ``PR_MERGED_AND_CLOSED_OUT`` are completed.
    ``HOLD_OPERATOR_REQUIRED`` is parked — the operator
    must intervene.
    """
    if not isinstance(state, str):
        return False
    if not state:
        return False
    return state in _COMPLETED_TERMINAL_STATES


# ---------------------------------------------------------------------------
# classify_humphry_message_for_stall
# ---------------------------------------------------------------------------


# Substrings that mark a phase header. The classifier is conservative:
# a line that contains a phase header substring is treated as a phase
# header even if the rest of the line is plausible progress text. This
# is the bug the PR is meant to detect.
_PHASE_HEADER_TOKENS = (
    "Starting PHASE ",
    "Now PHASE ",
    "Phase PHASE ",
    "PHASE_STARTING",
)


# Substrings that mark a yes/no continue prompt.
_CONTINUE_PROMPT_TOKENS = (
    "Continue?",
    "(yes/no)",
    "(y/n)",
    "Type continue",
    "Press enter to continue",
    "Awaiting confirmation",
)


# Substrings that mark an explicit checkpoint reference.
#
# Both lowercase and sentence-cased variants are listed so the
# broad ``_contains_any(text, _CHECKPOINT_TOKENS)`` check
# (used by the phase-header branch of the classifier) matches
# agents that emit ``Wrote checkpoint to /tmp/ckpt.json`` or
# ``Checkpoint: /tmp/ckpt.json`` the same way it matches the
# lowercase forms. Without the sentence-cased entries, a
# phase-header message that mentions a checkpoint in
# sentence-cased prose would fall through to
# ``STALL_PHASE_HEADER_ONLY`` instead of the documented
# ``STALL_NO_TERMINAL_STATE`` branch, misclassifying a
# checkpoint-bearing stall as a pure phase-header-only stall.
#
# Fix W (Codex 3442251126): The strict value-bearing
# extractor (``_extract_checkpoint_value``) already accepts
# sentence-cased forms (Fix J / Codex 3420268720), but the
# BROAD ``_CHECKPOINT_TOKENS`` substring scan was
# case-sensitive. Add the sentence-cased variants here so
# the broad check stays in sync with the strict extractor.
_CHECKPOINT_TOKENS = (
    "checkpoint_path=",
    "checkpoint:",
    "checkpoint: ",
    "checkpoint_path:",
    "checkpoint=",
    "wrote checkpoint to",
    "saved checkpoint to",
    "checkpoint file",
    "checkpoint at",
    "checkpoint saved to",
    # Sentence-cased variants — the agent frequently emits
    # these forms at the start of a line, e.g.
    # ``Starting PHASE 3 — Checkpoint: /tmp/ckpt.json`` or
    # ``Wrote checkpoint to /tmp/ckpt.json``. Without these
    # entries, the broad ``has_checkpoint`` check would miss
    # the mention and the message would fall through to
    # ``STALL_PHASE_HEADER_ONLY`` even though a real
    # checkpoint path is present (Fix W, Codex 3442251126).
    "Checkpoint_path=",
    "Checkpoint:",
    "Checkpoint: ",
    "Checkpoint_path:",
    "Checkpoint path=",
    "Checkpoint path:",
    "Checkpoint Path:",
    "Checkpoint Path=",
    "Checkpoint=",
    "checkpoint =",
    "Wrote checkpoint to",
    "Saved checkpoint to",
    "Checkpoint file",
    "Checkpoint at",
    "Checkpoint saved to",
    # Fix AG (Codex 3444118871): spaced ``field = value`` forms.
    # The strict extractor (``_CHECKPOINT_FIELD_MARKERS``) now
    # accepts spaced assignments, so the broad phase-header
    # scan must accept them too — otherwise a phase-header
    # message that names a checkpoint via a spaced assignment
    # (e.g. ``Starting PHASE 3 — checkpoint_path = /tmp/x``)
    # would fall through to ``STALL_PHASE_HEADER_ONLY``
    # despite having a real checkpoint path. The test
    # ``TitleCaseCheckpointPathBroadCheckTests`` enforces
    # this sync between the two lists.
    "checkpoint_path =",
    "Checkpoint_path =",
    "Checkpoint path =",
    "Checkpoint Path =",
    "Checkpoint =",
)


# Substrings that mark an explicit next_action reference.
# Each entry is the literal marker the agent emits. A bare
# ``next_action:`` with no value is intentionally NOT sufficient
# on its own — the classifier requires both the marker AND a
# non-empty, non-placeholder value before classifying as
# OK_PROGRESS_WITH_NEXT_ACTION.
#
# Top-level ``next_action`` markers scanned by
# :func:`_extract_next_action_value`. These are the canonical
# protocol markers — the only forms a real runner is expected
# to emit at the top of a line. The set is intentionally
# NARROW: prose variants like ``"Next action:"``,
# ``"next step:"``, ``"next action:"`` and ``"next step="``
# are NOT included here because they would be misidentified
# as real actions in human-prose text such as
# ``"Recommended next action: None. No repair needed."``
# or ``"Suggested next step: wait for CI."`` whenever a
# checkpoint path is also present. Fix Q (Codex 3439736315):
# those prose variants are needed ONLY for nested-marker
# rejection inside an already-extracted value, so they live
# in :data:`_NESTED_NEXT_ACTION_MARKERS` below and are not
# scanned by the top-level extractor.
#
# Order matters: the colon-style marker (``next_action:``)
# is listed BEFORE the equals-style marker (``next_action=``)
# so the parser prefers the FIRST occurrence of a marker in
# the line. For an ambiguous line such as
# ``"next_action: next_action=poll CI"`` the first
# ``next_action:`` (at position 0) is a real field-assignment
# collision (``next_action`` is followed by ``=``), so the
# parser rejects the whole line. Listing ``next_action=``
# first would have caused the parser to find the sub-marker
# at position 13 (the ``=`` inside the field-assignment value)
# and incorrectly accept ``"poll CI"`` as the action. Fix N
# (Codex 3438828758): the colon-first ordering aligns the
# parser with the persisted validation, which sees the full
# ``"next_action=poll CI"`` value and rejects it as a
# field-assignment collision.
_NEXT_ACTION_TOKENS = (
    "next_action:",
    "next_action=",
)


# Nested-only ``next_action`` markers. These are the prose
# variants that look like markers but are typically used as
# natural language inside a longer sentence. They are NOT
# valid top-level next_action markers (see the rationale
# on :data:`_NEXT_ACTION_TOKENS`) and the top-level extractor
# will not match them. They ARE used by
# :func:`_contains_nested_next_action_marker` to detect
# structured misuse when a value extracted from a real
# marker contains a sub-marker as a substring, e.g.
# ``"next_action=next step: poll CI"`` or
# ``"next_action=Next action: poll CI"``. Fix Q
# (Codex 3439736315): these variants were added to the
# top-level set in Fix P (Codex 3439619609) but the top-level
# scan is too broad for them — they need to be nested-only.
_NESTED_NEXT_ACTION_MARKERS = (
    "next_action:",
    "Next action:",
    "next step:",
    "Next step:",
    "next action:",
    "next_action=",
    "next step=",
)


# Empty-value placeholders that explicitly mean "no action".
# When a ``next_action:`` marker is followed by one of these
# tokens, the classifier must NOT classify as
# OK_PROGRESS_WITH_NEXT_ACTION. The runner cannot execute
# an empty / "none" / "null" action.
_NEXT_ACTION_EMPTY_VALUES = (
    "none",
    "null",
    "nil",
    "n/a",
    "todo",
    "tbd",
    "tba",
)


def _is_field_assignment_collision_value(value: object) -> bool:
    """Return True iff ``value`` is a real ``field=value`` / ``field: value`` assignment.

    The check combines two conditions:

    1. The first identifier-like token of ``value`` is a
       known protocol field name (i.e. a member of
       :data:`_FIELD_NAME_NEXT_ACTIONS`).
    2. The first non-whitespace character AFTER that token
       is the assignment delimiter ``=`` or ``:`` (with
       optional spaces between the token and the delimiter).

    Both conditions must hold. A value that satisfies only
    (1) — e.g. ``"checkpoint current run state"`` where the
    first word is the field name ``checkpoint`` but the
    remainder is plain executable action text — is NOT a
    field-assignment collision and must be accepted.

    Examples (True = rejected as collision):

    - ``"checkpoint_path=/tmp/ckpt.json"`` → True
    - ``"checkpoint_path = /tmp/ckpt.json"`` → True
    - ``"checkpoint=/tmp/ckpt.json"`` → True
    - ``"terminal_state=MERGED"`` → True
    - ``"terminal_state = MERGED"`` → True
    - ``"state: MERGED"`` → True
    - ``"phase: PHASE_7"`` → True
    - ``"next_action=poll CI"`` → True
    - ``"next_step: continue"`` → True

    Examples (False = accepted as a real action):

    - ``"checkpoint current run state"`` → False
    - ``"state current PR status"`` → False
    - ``"phase current retry window"`` → False
    - ``"next_action review Codex response"`` → False
    - ``"poll CI status"`` → False (first token not a field name)
    - ``"checkpoint"`` → False (handled by the full-string
      field-name check in :func:`is_valid_next_action`, not
      here)

    Fix M (Codex 3438724908): the previous persisted-state
    check (Fix L, Codex 3422779962) rejected any value
    whose first identifier-like token was a known protocol
    field name, even with no assignment delimiter. That was
    too aggressive — it rejected legitimate executable
    actions like ``"checkpoint current run state"`` and
    ``"state current PR status"`` that begin with a
    field-name word but have no ``=`` or ``:`` syntax. The
    canonical :func:`_is_field_assignment_collision_value`
    helper is the single source of truth for persisted
    field-assignment collision detection: it is called by
    :func:`is_valid_next_action`, which is in turn called by
    :func:`validate_checkpoint`,
    :func:`checkpoint_requires_operator`,
    :func:`next_action_from_checkpoint`, and
    :func:`evaluate_watchdog`. Tightening the helper
    tightens all four callers in one place.

    Fix U (Codex 3442047933): when the value starts with
    a leading wrapper character (``"'([{``), the
    :func:`_first_field_name_token` helper treats that
    character as a boundary and returns ``None``, so the
    raw form passes the collision check. The canonical
    helper now also tries the wrapper-stripped form of
    the value when looking for the first identifier-like
    token. A quoted field-assignment value
    (``"checkpoint_path=/tmp/ckpt.json"``) is
    recognised as the same collision it is when bare,
    but a quoted real action whose first word is a
    field-name word (``"checkpoint current run state"``)
    is still accepted because the field-assignment
    check requires both a field-name word AND a
    delimiter (``=``/``:``) after it — the quoted
    real-action form has no delimiter after the first
    word and remains accepted.
    """
    if not isinstance(value, str):
        return False
    # Fix U (Codex 3442047933): try the raw form first,
    # then the wrapper-stripped form. If either form
    # detects a field-assignment collision, the value is
    # rejected. The wrapper-stripped form catches quoted
    # field-assignment values like
    # ``"checkpoint_path=/tmp/ckpt.json"`` whose raw form
    # starts with a leading quote that
    # :func:`_first_field_name_token` would treat as a
    # boundary.
    return _check_field_assignment_on_form(
        value
    ) or _check_field_assignment_on_form(
        value.lstrip(_PLACEHOLDER_LEADING_WRAPPERS)
    )


def _check_field_assignment_on_form(value: object) -> bool:
    """Helper for :func:`_is_field_assignment_collision_value`.

    Runs the field-assignment collision check on a single
    form of the value. Returns True iff the value's first
    identifier-like token is a known protocol field name
    AND the first non-whitespace character after that
    token is ``=`` or ``:``. Returns False for non-string
    inputs and for values where the first token is not a
    known protocol field name.
    """
    if not isinstance(value, str):
        return False
    first_tok = _first_field_name_token(value)
    if first_tok is None:
        return False
    if first_tok.lower() not in _FIELD_NAME_NEXT_ACTIONS:
        return False
    # Find the first non-whitespace character after the
    # token in the original value. The token's start is
    # after the leading whitespace; we re-strip leading
    # whitespace and walk to the token's end so the
    # remainder we examine is the actual post-token span.
    s = value.lstrip()
    if not s:
        return False
    token_end = len(s)
    for j, ch in enumerate(s):
        if ch.isspace() or ch in _FIELD_TOKEN_BOUNDARIES:
            token_end = j
            break
    remainder = s[token_end:]
    for ch in remainder:
        if not ch.isspace():
            return ch in ("=", ":")
    # Token is at the end of the value with nothing after.
    return False


def _contains_nested_next_action_marker(value: object) -> bool:
    """Return True iff ``value`` contains any supported
    :data:`_NESTED_NEXT_ACTION_MARKERS` marker as a substring.

    A value that contains a supported marker is treated
    as a nested-marker collision. The runner has used a
    marker as part of the value (e.g.
    ``"next_action=next step: poll CI"`` contains
    ``"next step:"`` at position 0 of the value), which
    is a structured misuse of the marker vocabulary.
    The parser must not accept such values as
    executable actions.

    Fix P (Codex 3439619609): the previous
    earliest-marker selection (Fix O, Codex 3439399122)
    correctly handled the symmetric field-name
    collision forms ``"next_action: next_action=poll CI"``
    and ``"next_action=next_action: poll CI"`` because
    the first token of the value is a known protocol
    field name (``next_action``). It missed the spaced
    sub-marker forms like ``"next step:"`` and
    ``"Next action:"`` because the first token
    (``next`` / ``Next``) is NOT a protocol field name,
    so the narrowed field-assignment collision check
    did not fire. The canonical :func:`_first_field_name_token`
    check is not appropriate here because the value is
    the full stripped remainder, not a single token.
    The substring check is sufficient: any value
    containing a literal next_action marker is a
    misuse, because ordinary executable text never
    contains a marker token by accident.

    Fix Q (Codex 3439736315): the nested-marker scan
    uses :data:`_NESTED_NEXT_ACTION_MARKERS`, NOT the
    top-level :data:`_NEXT_ACTION_TOKENS`. The two
    sets are intentionally different: the top-level
    set is kept narrow (only ``"next_action:"`` and
    ``"next_action="``) so prose like ``"Recommended
    next action: None. No repair needed."`` is not
    matched by the top-level extractor. The nested
    set still includes the prose variants so the
    nested-marker collision check catches
    ``"next_action=Next action: poll CI"`` and
    similar structured-misuse forms.

    The check is marker-token based (substring of the
    full marker string), NOT word-based. Ordinary text
    like ``"review next steps after CI"`` does NOT
    contain the marker ``"next step:"`` (it has
    ``"next steps"`` with the trailing ``s``), so the
    substring search correctly accepts it.

    Examples (True = nested-marker collision detected):

    - ``"next_action: next_action=poll CI"`` -> True
      (contains ``"next_action="``)
    - ``"next_action=next_action: poll CI"`` -> True
      (contains ``"next_action:"``)
    - ``"next_action=next step: poll CI"`` -> True
      (contains ``"next step:"``)
    - ``"next_action=Next action: poll CI"`` -> True
      (contains ``"Next action:"``)
    - ``"next_action: next step: poll CI"`` -> True
      (contains ``"next step:"``)
    - ``"next_action: Next action: poll CI"`` -> True
      (contains ``"Next action:"``)

    Examples (False = legitimate action accepted):

    - ``"poll CI status"`` -> False
    - ``"checkpoint current run state"`` -> False
    - ``"review next steps after CI"`` -> False
      (``"next steps"`` != ``"next step:"``)
    - ``""`` -> False
    - ``None`` -> False
    """
    if not isinstance(value, str):
        return False
    for marker in _NESTED_NEXT_ACTION_MARKERS:
        if marker in value:
            return True
    return False


def _is_field_assignment_collision(token: str) -> bool:
    """Return True iff ``token`` looks like a field-assignment prefix.

    Fix H (Codex 3417182526): A malformed same-line remainder
    like ``checkpoint_path=/tmp/ckpt.json`` is a
    field-name-as-value collision. After the per-token boundary
    scan in :func:`_extract_next_action_value` has stopped at
    the ``=`` delimiter, the extracted ``first_token`` is the
    bare field name (``checkpoint_path``). The runner is
    misusing a protocol field as an action value, not emitting
    an executable action. Reject it so the scan continues to
    the next marker (or returns ``None`` if no real action is
    present).

    A collision is recognised when ``token.lower()`` is a known
    protocol field name. The set is the same vocabulary used by
    :func:`is_valid_next_action` (``_FIELD_NAME_NEXT_ACTIONS``)
    so the two helpers cannot disagree on which tokens are
    field names.
    """
    if not isinstance(token, str):
        return False
    stripped = token.strip()
    if not stripped:
        return False
    return stripped.lower() in _FIELD_NAME_NEXT_ACTIONS


# Boundary characters that separate the first token of a
# ``next_action`` value from the rest. The set is identical
# to the boundary scan in :func:`_extract_next_action_value`
# so persisted-state validation and final-output extraction
# agree on what counts as the "first field token".
_FIELD_TOKEN_BOUNDARIES = "=:,;]})\n\"'"


def _first_field_name_token(value: str) -> Optional[str]:
    """Return the first identifier-like token in ``value`` or ``None``.

    The token is the longest run of non-boundary characters at
    the start of ``value`` (after leading whitespace is
    stripped). Boundaries are: ``=``, ``:``, ``,``, ``;``,
    ``]``, ``)``, ``}``, whitespace, newline, and the quote
    characters ``"`` and ``'``. The token is returned with its
    original case so the caller can decide whether to
    lowercase it for comparison.

    ``None`` is returned for non-strings, empty / whitespace
    inputs, or inputs that start with a boundary character
    (no first token).

    Fix L (Codex 3422779962): persisted checkpoint /
    watchdog values can contain a full ``field=value``
    assignment, e.g. ``"checkpoint_path=/tmp/ckpt.json"`` or
    ``"terminal_state=MERGED"`` or ``"terminal_state =
    MERGED"``. A bare full-string membership check against
    :data:`_FIELD_NAME_NEXT_ACTIONS` misses these because the
    full string is not exactly a field name. The
    :func:`is_valid_next_action` helper therefore needs to
    extract the first field-name token and reject the value
    when that token is a known protocol field name. This
    keeps the persisted-state validator and the final-output
    extractor (:func:`_extract_next_action_value`) consistent
    — both reject field-name-as-value collisions using the
    same vocabulary.
    """
    if not isinstance(value, str):
        return None
    s = value.lstrip()
    if not s:
        return None
    for j, ch in enumerate(s):
        if ch.isspace() or ch in _FIELD_TOKEN_BOUNDARIES:
            return s[:j] if j > 0 else None
    return s


def _extract_next_action_value(text: str) -> Optional[str]:
    """Return the first real ``next_action`` value found in ``text``.

    The function is the canonical extractor for a
    ``next_action:`` (or similar) marker. It scans ``text``
    line-by-line and, for each marker that is present, returns
    the first whitespace-delimited token immediately after the
    marker on the SAME line. The scan stops at the end of the
    marker's line — a marker followed by a newline is
    considered empty, even if the next line is a different
    field (e.g. ``checkpoint: /tmp/ckpt.json``).

    The return value is the raw string token, with no
    stripping applied (the caller is expected to call
    :func:`is_valid_next_action` on it). The token is
    lowercased only for placeholder comparison; the
    placeholder check itself is delegated to
    :func:`is_valid_next_action`. ``None`` is returned iff
    no marker is found, or every marker is followed by an
    empty / placeholder / no-value line.

    Fix B (Codex 3415107653): The previous implementation
    used ``str.lstrip()`` which consumes newlines and
    treated the next field name as the action value. For
    example, ``"next_action:\\ncheckpoint: /tmp/ckpt.json"``
    yielded ``has_next_action=True`` because ``lstrip()``
    walked past the newline. The new implementation splits
    on newlines and only inspects the same-line remainder
    after each marker.

    Fix C (Codex 3415861210): The previous implementation
    returned the first extracted token from the first
    marker found, even if that token was a placeholder
    (``"none"``, ``"todo"``, empty). This caused a runner
    that emitted a placeholder first (``next_action: none``)
    and a real action later (``next_action: poll CI status``)
    to be misclassified as having no valid next action. The
    new implementation scans ALL markers across the text and
    returns the first marker whose value passes
    :func:`is_valid_next_action`. Placeholder/empty markers
    are skipped instead of short-circuiting the search.

    Fix H (Codex 3417182526): The per-token boundary scan
    must also stop at ``=`` so a malformed same-line
    remainder like ``"checkpoint_path=/tmp/ckpt.json"``
    extracts as ``"checkpoint_path"`` rather than the whole
    ``"checkpoint_path=/tmp/ckpt.json"`` string. After
    extraction, if the first token is a known protocol field
    name (``checkpoint``, ``checkpoint_path``,
    ``terminal_state``, ``phase``, ``state``, etc.) the
    marker is treated as a field-assignment collision and
    is skipped — the runner mis-used a field name as a
    value, not as an action. The scan continues to the next
    marker so a real action on a later line is still found.

    Fix N (Codex 3438828758): The field-assignment
    collision check must use the narrowed semantics shared
    with :func:`is_valid_next_action` — the
    :func:`_is_field_assignment_collision_value` helper
    that rejects a marker only if the first identifier-like
    token is a known field name AND the first
    non-whitespace character after that token is ``=`` or
    ``:``. The previous per-token call
    :func:`_is_field_assignment_collision` only checked
    whether the bare first token was a field name, so a
    legitimate action like
    ``"next_action: checkpoint current run state"`` was
    wrongly rejected: the first token ``"checkpoint"`` IS
    a field name, but the next character is a space (not
    ``=`` or ``:``), so the value is a real executable
    action and the marker must be accepted. The narrowed
    helper is given the FULL ``stripped`` remainder
    (including the ``=`` / ``:`` delimiter if any) so the
    check can see the post-token context. ``first_token``
    is still extracted for the
    :func:`is_valid_next_action` call that returns the
    action value to the runner. This keeps final-output
    parsing and persisted validation aligned: both use
    the same vocabulary and the same narrowed rule.

    Fix O (Codex 3439399122): The parser must select the
    EARLIEST marker occurrence in the line by ``idx`` (with
    longer-marker tiebreak for the same ``idx``), NOT
    whichever marker appears first in the
    :data:`_NEXT_ACTION_TOKENS` tuple. Tuple-order
    priority is brittle and asymmetric: reordering the
    tuple to fix ``"next_action: next_action=poll CI"``
    introduces the symmetric failure
    ``"next_action=next_action: poll CI"``. The fix is
    to walk the line ONCE per marker, collect the lowest
    ``idx`` (and the longest marker for ties), and parse
    that earliest occurrence only. Sub-markers inside an
    invalid earliest marker value CANNOT rescue the line
    (Fix N's ``break`` is no longer needed because there
    is only one marker per line now). The across-line
    ``scan past invalid markers`` behavior is preserved
    by the outer ``for raw_line in text.splitlines()``
    loop: an invalid earliest marker on line N causes the
    line to be skipped, and a valid marker on line N+1 is
    accepted.
    """
    for raw_line in text.splitlines():
        line = raw_line
        # Fix O (Codex 3439399122): find the earliest marker
        # occurrence in the line by ``idx`` (with longer-marker
        # tiebreak for the same ``idx``), NOT by tuple-order
        # priority. Walking all markers and selecting the lowest
        # ``idx`` ensures the parser always picks the
        # first-encountered marker regardless of which marker
        # string is found first. This is symmetric for both
        # ``"next_action: next_action=poll CI"`` and
        # ``"next_action=next_action: poll CI"`` — in both cases
        # the first marker is the field-assignment collision
        # that must be rejected.
        best_idx = -1
        best_marker = None
        for marker in _NEXT_ACTION_TOKENS:
            idx = line.find(marker)
            if idx < 0:
                continue
            if (
                best_marker is None
                or idx < best_idx
                or (idx == best_idx and len(marker) > len(best_marker))
            ):
                best_idx = idx
                best_marker = marker
        if best_marker is None:
            # No next_action marker on this line. Move on to
            # the next line.
            continue
        after = line[best_idx + len(best_marker):]
        stripped = after.lstrip()
        if not stripped:
            # Marker is the last token on the line — empty
            # value. Skip this line (the across-line scan-past
            # behavior in the outer loop will pick up a valid
            # marker on a later line).
            continue
        # Pull the first whitespace-delimited token on
        # the same line. Stop at any non-identifier
        # delimiter (whitespace, comma, semicolon, close
        # brackets, paren, colon, EQUALS) so a value like
        # ``"checkpoint_path=/tmp/ckpt.json"`` extracts
        # as ``"checkpoint_path"`` rather than the full
        # string, and so ``"checkpoint: /tmp/ckpt.json"``
        # extracts as ``"checkpoint"`` rather than the
        # full string.
        token_end = len(stripped)
        for j, ch in enumerate(stripped):
            if ch.isspace() or ch in ",;]})\n:=":
                token_end = j
                break
        first_token = stripped[:token_end]
        if not first_token:
            # Delimiter was the first character; keep
            # scanning for other markers.
            continue
        if _contains_nested_next_action_marker(stripped):
            # The value contains another supported
            # :data:`_NEXT_ACTION_TOKENS` marker (e.g.
            # ``"next step:"`` in
            # ``"next step: poll CI"``). The runner
            # has used a marker as part of the value,
            # which is a structured misuse of the
            # marker vocabulary. The narrowed
            # field-assignment collision check
            # (below) does not fire here because the
            # first token of the value (``next`` /
            # ``Next``) is not a protocol field
            # name, so the canonical check is the
            # nested-marker substring check. The
            # line is rejected (skipped) and the
            # across-line ``scan past invalid
            # markers`` behavior in the outer
            # ``for raw_line in text.splitlines()``
            # loop will pick up a valid marker on a
            # later line. Fix P (Codex 3439619609).
            continue
        if _is_field_assignment_collision_value(stripped):
            # The remainder is a real field=value /
            # field: value assignment (e.g.
            # ``checkpoint_path=/tmp/ckpt.json`` or
            # ``terminal_state = MERGED`` or
            # ``state: MERGED`` or
            # ``next_action=poll CI``). The agent
            # used a protocol field name as a value
            # with the assignment delimiter (``=`` or
            # ``:``) — this is not an executable
            # action. The structured field-assignment
            # value ``consumes`` the rest of the line
            # (it is a deliberate misuse, not an
            # empty/placeholder marker), so the parser
            # stops looking for sub-markers in the
            # same line and advances to the next line.
            # The across-line ``scan past invalid
            # markers`` behavior is preserved by the
            # outer ``for raw_line in
            # text.splitlines()`` loop. Fix N (Codex
            # 3438828758): the narrowed helper takes
            # the FULL ``stripped`` remainder (not
            # just the bare ``first_token``) so it
            # can see whether the field-name token is
            # actually followed by an assignment
            # delimiter. Fix O (Codex 3439399122):
            # sub-markers inside the value of the
            # earliest marker cannot rescue this line;
            # the parser uses the earliest marker only.
            continue
        # Fix N (Codex 3438828758): when the first
        # word is a field name but the value is not a
        # field assignment (per the narrowed check
        # above), the value is a real executable
        # action that starts with a field-name word
        # (e.g. ``"checkpoint current run state"``).
        # The extractor must return the FULL value in
        # this case so the caller's
        # :func:`is_valid_next_action` can validate
        # the multi-word action. Returning just the
        # bare first word would cause
        # :func:`is_valid_next_action` to reject it
        # (the bare word is a field name, so the
        # full-string field-name check would fire).
        # For single-word values where ``stripped ==
        # first_token``, we fall through to the
        # :func:`is_valid_next_action` check below —
        # a bare field name as the action value is
        # not a valid action and the scan continues.
        if (
            first_token.lower() in _FIELD_NAME_NEXT_ACTIONS
            and stripped != first_token
        ):
            return stripped
        if is_valid_next_action(first_token):
            return first_token
        # else: placeholder/empty marker — keep scanning
            # the rest of the text for a real action.
    return None


def _has_next_action_with_value(text: str) -> bool:
    """Return True iff ``text`` contains a ``next_action:`` (or
    similar) marker followed by a non-empty, non-placeholder
    value.

    The check is per-marker, per-line: each ``next_action:``
    occurrence must be followed by at least one non-whitespace,
    non-placeholder character ON THE SAME LINE. This prevents
    the no-continuation stall case the classifier is meant to
    reject, where the agent emits ``next_action:`` with no
    concrete value (e.g. ``next_action:`` or ``next_action:
    none``), or where a bare ``next_action:`` is followed by a
    newline and the next field name is the value
    (``"next_action:\\ncheckpoint: /tmp/ckpt.json"``).

    The actual extraction is delegated to
    :func:`_extract_next_action_value`, which is the single
    canonical extractor. The validity check is delegated to
    :func:`is_valid_next_action`.
    """
    value = _extract_next_action_value(text)
    if value is None:
        return False
    return is_valid_next_action(value)


# Substrings that mark an explicit checkpoint marker in a
# final message. Each entry is the literal marker the agent
# emits before a checkpoint path value. A bare marker with
# no value (e.g. ``checkpoint_path=`` followed by a newline,
# or ``checkpoint:`` with nothing) is NOT sufficient on its
# own — the classifier now requires both the marker AND a
# valid path value before treating the message as
# OK_PROGRESS_WITH_NEXT_ACTION.
#
# The marker vocabulary is split into two tuples:
#
# - :data:`_CHECKPOINT_FIELD_MARKERS` — field-style markers
#   (colon / equals delimited). Matching is CASE-SENSITIVE
#   so the strict field-style behavior is preserved. A few
#   mixed-case entries (``Checkpoint:``, ``Checkpoint
#   path:``) are explicit so the agent can use
#   sentence-cased field names without losing precision.
# - :data:`_CHECKPOINT_PROSE_MARKERS` — prose-style markers
#   (space delimited, sometimes colon). Matching is
#   CASE-INSENSITIVE so a sentence-cased ``Wrote checkpoint
#   to /tmp/ckpt.json`` parses the same way as the
#   lowercase form. Fix J (Codex 3420268720): a case-sensitive
#   prose-marker search let a perfectly valid
#   sentence-cased final output fall through to
#   ``STALL_NO_CHECKPOINT`` even though both a continuation
#   action and a concrete resume path were present.
#
# Both marker sets still require a path-shaped value (see
# :func:`is_valid_checkpoint_path`); a marker like
# ``wrote checkpoint to pending`` is rejected because
# ``pending`` is not path-shaped. The previous bare-marker
# bug (``Checkpoint pending`` slipping through as a real
# value) is still pinned: bare ``Checkpoint `` /
# ``checkpoint `` / ``Checkpoint file`` markers without a
# colon, equals, or trailing-space path-suffix are not in
# either tuple, so the strict value-bearing path must still
# be present after the marker.
_CHECKPOINT_FIELD_MARKERS = (
    "checkpoint_path=",
    "checkpoint_path:",
    "Checkpoint path:",
    "Checkpoint Path:",
    "Checkpoint_path=",
    # Fix AM (Codex 3448488545): the broad ``_CHECKPOINT_TOKENS``
    # scan advertises the sentence-cased underscore colon form
    # ``Checkpoint_path:`` (it appears alongside ``Checkpoint_path=``
    # in the broad tuple), but the strict value-bearing
    # extractor only knew the equals variant. A resumable message
    # like::
    #
    #     next_action: poll CI status
    #     Checkpoint_path: /tmp/aed_checkpoint.json
    #
    # was therefore downgraded to ``STALL_NO_CHECKPOINT`` instead
    # of ``OK_PROGRESS_WITH_NEXT_ACTION`` despite having both
    # required pieces of resume data. Adding the colon variant
    # here keeps the strict extractor in sync with the broad
    # scan.
    "Checkpoint_path:",
    "Checkpoint path=",
    "Checkpoint Path=",
    "checkpoint=",
    "Checkpoint=",
    "checkpoint:",
    "Checkpoint:",
    "checkpoint =",
    # Fix AG (Codex 3444118871): spaced ``field = value`` forms.
    # The terminal-state parser already accepts spaced
    # ``field = value`` assignments, but the strict
    # checkpoint extractor used a case-sensitive ``find``
    # against this tuple, so a message like
    # ``next_action: poll CI status`` plus
    # ``checkpoint_path = /tmp/aed/checkpoint.json`` would
    # miss every marker here (the no-space ``checkpoint_path=``
    # entry does not match because of the space before ``=``)
    # and ``_has_checkpoint_with_value`` would stay False,
    # causing ``classify_humphry_message_for_stall`` to
    # return ``STALL_NO_CHECKPOINT`` despite having both
    # required pieces of resume data. Adding the spaced
    # variants below keeps the strict extractor in sync with
    # the terminal-state parser's spaced-assignment behavior.
    "checkpoint_path =",
    "Checkpoint_path =",
    "Checkpoint path =",
    "Checkpoint Path =",
    "Checkpoint =",
)


_CHECKPOINT_PROSE_MARKERS = (
    # Prose-style markers (space delimited). Each entry is
    # the literal marker followed by a trailing space so
    # that ``checkpoint file`` does NOT match
    # ``checkpoint file: /tmp/ckpt.json`` (the colon breaks
    # the space-suffix match — the explicit
    # ``checkpoint file: `` entry below handles that
    # form). Matching is CASE-INSENSITIVE so
    # ``Wrote checkpoint to /tmp/ckpt.json`` and
    # ``Checkpoint file: /tmp/ckpt.json`` parse the same
    # way as the lowercase forms.
    "wrote checkpoint to ",
    "saved checkpoint to ",
    "checkpoint file: ",
    "checkpoint file ",
    "checkpoint at ",
    "checkpoint saved to ",
)


def _extract_checkpoint_value(text: str) -> "Optional[str]":
    """Return the first real ``checkpoint`` value found in ``text``.

    A "real" value is a non-empty, non-placeholder string
    captured from the same line as one of the
    :data:`_CHECKPOINT_FIELD_MARKERS` or
    :data:`_CHECKPOINT_PROSE_MARKERS` entries. The extractor
    walks each line of the message, finds the first marker
    occurrence, and pulls the first whitespace-
    delimited token on the same line — bounded so that
    the value never consumes the next field name (e.g.
    ``"next_action: poll CI"`` is not consumed when
    parsing ``"checkpoint_path=/tmp/ckpt.json"``).

    Field-style markers are matched CASE-SENSITIVELY so the
    strict field-style behavior is preserved. Prose-style
    markers are matched CASE-INSENSITIVELY so a
    sentence-cased ``Wrote checkpoint to /tmp/ckpt.json``
    parses the same way as the lowercase form (Fix J,
    Codex 3420268720). The case-insensitive search is
    performed on a lower-cased copy of the line; the
    original-case `idx` is preserved so the returned
    value-bearing token still has the same surface form
    the agent wrote.

    The return value is the raw string token, with no
    stripping applied (the caller is expected to call
    :func:`is_valid_checkpoint_path` on it). ``None`` is
    returned iff no marker is found, every marker is
    followed by an empty / placeholder / no-value line, or
    the extracted token fails the canonical
    :func:`is_valid_checkpoint_path` check.

    Fix G (Codex 3417011620): The previous
    ``_contains_any(text, _CHECKPOINT_TOKENS)`` check
    matched any substring like ``"checkpoint_path="``
    even when no value followed. The new extractor
    captures the same-line value and validates it via
    :func:`is_valid_checkpoint_path`, so a marker with
    no real value (e.g. ``checkpoint_path=`` followed
    by a newline, or ``checkpoint: none``) does not
    satisfy the "has a real checkpoint" predicate.

    Fix J (Codex 3420268720): the previous
    case-sensitive ``line.find(marker)`` for prose
    markers let a sentence-cased final output like
    ``Wrote checkpoint to /tmp/ckpt.json`` slip through
    to ``STALL_NO_CHECKPOINT``. The new extractor
    case-folds the line and the prose marker for the
    search, but the original-case ``idx`` is used to
    slice the value, so the returned token still matches
    the agent's surface casing.
    """
    for raw_line in text.splitlines():
        line = raw_line
        line_lower = line.lower()
        # Field-style markers: case-sensitive search so
        # strict field-style behavior is preserved. The
        # mixed-case field-style entries (``Checkpoint:``,
        # ``Checkpoint path:``) are explicit and remain
        # case-sensitive.
        for marker in _CHECKPOINT_FIELD_MARKERS:
            idx = line.find(marker)
            if idx < 0:
                continue
            value = _extract_value_after_marker(line, idx, len(marker))
            if value is not None:
                return value
        # Prose-style markers: case-insensitive search so
        # a sentence-cased ``Wrote checkpoint to`` parses
        # the same way as the lowercase form. The
        # lower-cased marker is searched in the
        # lower-cased line, but the original-case
        # ``idx`` is used to slice the value.
        for marker in _CHECKPOINT_PROSE_MARKERS:
            marker_lower = marker.lower()
            idx = line_lower.find(marker_lower)
            if idx < 0:
                continue
            value = _extract_value_after_marker(line, idx, len(marker))
            if value is not None:
                return value
    return None


def _extract_value_after_marker(
    line: str, idx: int, marker_len: int
) -> "Optional[str]":
    """Return the first whitespace-delimited token after the
    marker at ``idx`` in ``line`` if it is a valid
    checkpoint path; otherwise ``None``.

    Helper extracted from :func:`_extract_checkpoint_value`
    so the field-style and prose-style marker passes can
    share the same per-line value-extraction logic. The
    caller is expected to have already verified that the
    marker is present at ``idx``.
    """
    after = line[idx + marker_len:]
    stripped = after.lstrip()
    if not stripped:
        # Marker is the last token on the line —
        # empty value, keep scanning.
        return None
    # Pull the first whitespace-delimited token on
    # the same line. Stop at any non-identifier
    # delimiter (whitespace, comma, semicolon, close
    # brackets, paren, colon) so a value like
    # ``"checkpoint: /tmp/ckpt.json, resume: yes"``
    # extracts as ``"/tmp/ckpt.json,"`` (then we
    # strip trailing punctuation when validating)
    # rather than the full string. We also stop at
    # a comma to prevent the extractor from
    # consuming the next field when the agent
    # accidentally lists multiple paths.
    token_end = len(stripped)
    for j, ch in enumerate(stripped):
        if ch.isspace() or ch in ",;]})\n":
            token_end = j
            break
        if ch == ":":
            # Allow the colon to terminate the
            # token so that ``"checkpoint:
            # /tmp/ckpt.json: extra"`` extracts
            # ``/tmp/ckpt.json`` cleanly.
            token_end = j
            break
    first_token = stripped[:token_end]
    if is_valid_checkpoint_path(first_token):
        return first_token
    # else: placeholder/empty marker — keep
    # scanning the rest of the text for a real
    # value.
    return None


def _has_checkpoint_with_value(text: str) -> bool:
    """Return True iff ``text`` contains a ``checkpoint:`` (or
    similar) marker followed by a valid path value.

    The check is per-marker, per-line: each
    ``checkpoint_path=`` (or similar) occurrence must be
    followed by at least one non-whitespace, non-placeholder
    character ON THE SAME LINE. This prevents the
    "marker-without-path" stall case the classifier is
    meant to reject, where the agent emits a bare marker
    like ``checkpoint_path=`` with no concrete value, or
    a placeholder like ``checkpoint: none``.

    The actual extraction is delegated to
    :func:`_extract_checkpoint_value`, which is the single
    canonical extractor. The validity check is delegated to
    :func:`is_valid_checkpoint_path`.

    Fix G (Codex 3417011620): The classifier previously
    checked ``_contains_any(text, _CHECKPOINT_TOKENS)``
    which matched any substring containing the literal
    ``"checkpoint_path="`` regardless of what followed. A
    final message like ``Starting PHASE 2 — next_action:
    poll CI, checkpoint_path=`` would set both
    ``has_next_action`` and ``has_checkpoint`` to True and
    be classified as OK_PROGRESS_WITH_NEXT_ACTION despite
    having no real checkpoint path. The new
    ``_has_checkpoint_with_value`` requires a real value.
    """
    value = _extract_checkpoint_value(text)
    if value is None:
        return False
    return is_valid_checkpoint_path(value)


def _contains_any(text: str, needles: tuple) -> bool:
    return any(n in text for n in needles)


def _contains_any_ci(text: str, needles: tuple) -> bool:
    """Case-insensitive variant of :func:`_contains_any`.

    Used by the broad ``has_checkpoint`` scan so that a
    case-varied prose marker that the strict
    ``_extract_checkpoint_value`` already accepts (e.g.
    ``Wrote Checkpoint To`` or ``Checkpoint File:``) is
    also recognized by the broad phase-header scan. The
    strict extractor uses case-insensitive matching for
    prose markers; the broad scan now matches the same
    contract so the two vocabularies stay in sync (Fix AB,
    Codex 3443570407).

    The check is performed on a lower-cased copy of the
    text against lower-cased needles. The original-case
    text is not needed because the broad scan only answers
    a boolean "does the message mention a checkpoint?"
    question — it does not extract or return the matched
    surface form.
    """
    if not text:
        return False
    text_lower = text.lower()
    return any(n.lower() in text_lower for n in needles)


# A whole-token (word-boundary) match pattern built from
# TERMINAL_LIFECYCLE_STATES. The classifier previously used
# this as the primary detector, but a sentence that merely
# *mentions* a terminal state in a negated or future-looking
# context (e.g. "Not MERGED yet") was still classified as
# OK_TERMINAL. The classifier now uses
# ``_has_explicit_terminal_assertion`` instead, which
# requires an explicit assertion pattern. The pattern is
# kept for any caller that still needs a fast whole-token
# scanner; it is not used by the classifier.
_TERMINAL_TOKEN_PATTERN: "re.Pattern[str] | None" = None


def _terminal_token_pattern() -> "re.Pattern[str]":
    """Return the compiled whole-token regex for terminal states.

    Built lazily and cached on the module. The pattern is
    anchored on word boundaries (``\\b``).
    """
    global _TERMINAL_TOKEN_PATTERN
    if _TERMINAL_TOKEN_PATTERN is None:
        body = "|".join(re.escape(s) for s in TERMINAL_LIFECYCLE_STATES)
        _TERMINAL_TOKEN_PATTERN = re.compile(r"\b(?:" + body + r")\b")
    return _TERMINAL_TOKEN_PATTERN


# Negation / future / uncertainty tokens that, when present
# in the same line as a terminal state, disqualify the line
# from being treated as an explicit terminal-state assertion.
# The runner must not stop on a negated, future, or uncertain
# mention of a terminal state.
_DISQUALIFYING_TOKENS = (
    "not ",
    "no ",
    "n't ",
    "won't ",
    "will not ",
    "will be ",
    "going to be ",
    "after ",
    "before ",
    "might be ",
    "may be ",
    "could be ",
    "would be ",
    "should be ",
    "maybe ",
    "perhaps ",
    "isn't ",
    "aren't ",
    "wasn't ",
    "weren't ",
    "doesn't ",
    "don't ",
    "didn't ",
)


# Explicit assertion prefixes the runner emits when reporting
# a terminal state. A line that starts (after stripping
# leading whitespace) with one of these prefixes and a
# recognized terminal state is treated as an explicit
# assertion.
#
# Fix B (Codex 3415657751): the documented
# checkpoint-protocol field prefix ``terminal_state:`` /
# ``terminal_state=`` is added so a final-output signal
# like ``terminal_state: MERGED`` or
# ``terminal_state=HOLD_PR_CI_PENDING`` classifies as
# ``OK_TERMINAL`` instead of being rejected as a stall.
# The field-name variants are accepted with or without
# surrounding whitespace around the ``=`` separator
# (``terminal_state=MERGED`` and ``terminal_state = MERGED``
# are both valid).
#
# Fix K (Codex 3420442396): the prefix-match is
# case-insensitive AND the prefix set covers every
# capitalization and spacing variant the runner can
# pretty-print, including the previously-missing
# uppercase-with-spaced-equals form ``TERMINAL_STATE =
# MERGED``. The matching loop in
# :func:`_line_has_explicit_terminal_assertion`
# lowercases both the line and the candidate prefix for
# the ``startswith`` check, so this tuple only needs to
# hold one canonical case per logical prefix; mixed-case
# lines (``Terminal_State = MERGED``) are accepted via
# the lowercased comparison. Underscore and space
# variants of the field name are both accepted because
# both forms appear in the AED no-stall protocol and
# the agent may pretty-print either way.
_ASSERTION_PREFIXES = (
    "Final lifecycle state:",
    "Final state:",
    "Terminal state:",
    "Lifecycle state:",
    "State:",
    "terminal_state:",
    "terminal_state=",
    "terminal_state =",
    "Terminal State:",
    "TERMINAL_STATE:",
    "TERMINAL_STATE=",
    "TERMINAL_STATE =",
    "terminal state:",
    "terminal state=",
    "terminal state =",
    "Terminal_State:",
    "Terminal_State=",
    "Terminal_State =",
    "LIFECYCLE_STATE:",
    "LIFECYCLE_STATE=",
    "LIFECYCLE_STATE =",
    "lifecycle_state:",
    "lifecycle_state=",
    "lifecycle_state =",
    "Lifecycle State:",
    "Lifecycle_State:",
    "Lifecycle_State=",
    "Lifecycle_State =",
    "FINAL LIFECYCLE STATE:",
    "FINAL STATE:",
    "FINAL_LIFECYCLE_STATE:",
    "FINAL_STATE:",
)


# Placeholder next_action values that the validator rejects.
# Mirrors ``_NEXT_ACTION_EMPTY_VALUES`` in
# :mod:`aed_lifecycle.no_stall` for cross-module consistency.
_PLACEHOLDER_NEXT_ACTIONS = frozenset(
    {"none", "null", "n/a", "na", "todo", "tbd", "tba", "nil"}
)


# Sentence-ending delimiters used by :func:`_first_placeholder_token`
# to split a value into sentences when checking for
# placeholder-led explanatory sentences (Fix AL, Codex 3444118871).
# A runner that writes ``"None. No repair needed."`` produces an
# extracted value whose first SENTENCE is ``"None"`` (a placeholder)
# even though the full value is not an exact placeholder token.
# The delimiter set is intentionally narrow: only standard
# sentence-ending marks plus the colon (which often introduces
# an explanatory clause after a placeholder like
# ``"N/A: waiting for operator"``).
_PLACEHOLDER_SENTENCE_DELIMITERS = ".!?:;"


# Trailing sentence-ending punctuation stripped from the
# value before the placeholder / field-name check in
# :func:`is_valid_next_action`. Fix R (Codex 3440952035):
# a runner that writes ``"next_action: None. No repair
# needed."`` produces an extracted value of ``"None."`` that
# the placeholder set did not contain, so the validator
# accepted it as a real action. The set is intentionally
# narrow (sentence-end marks and a small set of structural
# terminators) so a legitimate action like ``"poll CI
# status."`` is rejected (the trailing period is a real
# sentence-ender) but ``"poll CI status"`` continues to be
# accepted. If a real action needs a trailing period, the
# runner should not be using
# :func:`_extract_next_action_value` to dispatch it; the
# classifier contract is to surface a hold when the action
# value looks like a punctuated placeholder.
_PLACEHOLDER_TRAILING_PUNCTUATION = ".,;:!?'\")}]}"


# Leading wrapper characters stripped from the value before
# the placeholder / field-name check in
# :func:`is_valid_next_action`. Fix S (Codex 3441855393):
# when a runner quotes or brackets a placeholder —
# ``next_action: "None."`` or ``next_action: [none.]`` —
# :func:`_extract_next_action_value` passes the wrapped
# token to :func:`is_valid_next_action`; the previous
# ``rstrip`` (Fix R) removed only the closing punctuation
# and left the leading quote/bracket, so the placeholder
# check missed and the classifier still returned
# OK_PROGRESS_WITH_NEXT_ACTION. Final agent output is
# free-form text and quoted field values are common, so
# the canonical validator must also strip matching
# leading wrappers before checking the placeholder set.
# The set is intentionally narrow: only the standard
# ASCII opening delimiters and quote characters that a
# runner is likely to wrap a placeholder in.
_PLACEHOLDER_LEADING_WRAPPERS = "\"'([{"


def _first_placeholder_token(value: str) -> "str | None":
    """Return the lowercased first token of the first sentence
    in ``value`` if it matches a known placeholder vocabulary
    entry; otherwise return ``None``.

    Fix AL (Codex 3444118871): the canonical placeholder
    check in :func:`is_valid_next_action` only matched
    exact placeholder tokens (``"none"``, ``"tbd"``, etc.).
    A persisted checkpoint or watchdog state that stores a
    placeholder plus explanatory text — e.g.
    ``"None. No repair needed."`` — bypassed the placeholder
    filter because the full sentence is not an exact
    placeholder token. The canonical validator now also
    checks the first normalized token of the first sentence:
    if it is a placeholder, the entire value is rejected as a
    placeholder-led explanatory sentence.

    The check is intentionally narrow:

    - Only the FIRST sentence is inspected. A real action like
      ``"poll none of the failing CI checks"`` (which contains
      ``none`` mid-sentence) is NOT rejected because the
      first sentence's first token is ``poll``, not a
      placeholder.
    - Leading wrapper characters and trailing punctuation are
      stripped from the first token before comparison, so
      wrapped placeholders (``"None."``, ``"[none.]"``) are
      recognised the same way as bare placeholders.
    - Only the documented placeholder vocabulary
      (:data:`_PLACEHOLDER_NEXT_ACTIONS`) is matched. New
      placeholder tokens are added there, not here.

    Returns the lowercased placeholder token if the first
    sentence starts with a placeholder, or ``None`` if the
    first sentence starts with a real word.
    """
    if not value:
        return None
    # Split on the first sentence-ending delimiter.
    # re.split is used so we can split on multiple delimiters
    # in one pass.
    import re as _re
    parts = _re.split(
        "[" + _re.escape(_PLACEHOLDER_SENTENCE_DELIMITERS) + "]",
        value,
        maxsplit=1,
    )
    first_sentence = parts[0] if parts else ""
    # ``.split(None)`` splits on whitespace and removes empty
    # tokens, so a value that is all whitespace (e.g. ``"   "``)
    # or contains only a delimiter (e.g. ``"."``) yields an
    # empty list. Guard with a truthiness check.
    tokens = first_sentence.split()
    if not tokens:
        return None
    first_token = tokens[0]
    # Strip leading wrappers and trailing punctuation (same
    # logic as :func:`is_valid_next_action`) so wrapped /
    # punctuated placeholders are normalised before comparison.
    normalized = first_token.lstrip(_PLACEHOLDER_LEADING_WRAPPERS)
    normalized = normalized.rstrip(_PLACEHOLDER_TRAILING_PUNCTUATION)
    normalized_lower = normalized.lower()
    if normalized_lower in _PLACEHOLDER_NEXT_ACTIONS:
        return normalized_lower
    return None


def is_valid_next_action(value: object) -> bool:
    """Return True iff ``value`` is a usable ``next_action``.

    Fix D (Codex 3414948261) + Fix B (Codex 3415107653): The
    single canonical next-action validity check used by every
    helper that needs to decide whether a ``next_action``
    field is safe to act on. A ``next_action`` is valid iff:

    - it is a string (``isinstance(value, str)``)
    - after ``str.strip()`` it is non-empty
    - its lowercased, stripped form, with leading
      wrapper characters and trailing sentence-ending
      punctuation removed, is NOT a placeholder
      (``none``, ``null``, ``nil``, ``n/a``, ``na``,
      ``todo``, ``tbd``, ``tba``). Fix R
      (Codex 3440952035): the previous version rejected
      only the bare placeholder form ``"none"``, so a
      runner that wrote ``"next_action: None. No repair
      needed."`` produced an extracted value of
      ``"None."`` that the placeholder set did not
      contain, and the validator accepted it as a real
      action. Fix S (Codex 3441855393): the previous
      Fix R version stripped only the closing
      punctuation, so a runner that wrote
      ``"next_action: \"None.\""`` or
      ``"next_action: [none.]"`` produced an extracted
      value of ``"\"None."`` or ``"[none."`` whose
      leading wrapper prevented the placeholder set
      from matching. The canonical validator now also
      strips the leading wrapper characters
      (``"'([{``) so wrapped or quoted placeholders are
      recognised as the same placeholder they are when
      bare. The runner would then try to resume with
      a punctuated placeholder instead of surfacing a
      stall/hold.
    - its lowercased, stripped, punctuation-trimmed form
      is NOT itself a field name. The set of rejected
      field names is the documented no-stall-protocol
      field set: ``next_action``, ``next_step``,
      ``checkpoint``, ``phase``, ``terminal``, ``state``,
      ``lifecycle``, ``next_phase``,
      ``pending_actions``, ``updated_at``. A value that
      matches one of these is treated as a field-name
      collision (``next_action: checkpoint: /tmp/ckpt.json``)
      and is rejected. This catches the case where the agent
      misuses a field as a value rather than treating the
      marker as a no-value placeholder.

    Anything else — ``None``, ``""``, ``"   "``, ``"none"``,
    ``"none."``, ``"todo"``, ``"checkpoint"``, ``123``,
    ``[]``, ``{}`` — is invalid.

    Used by :func:`validate_checkpoint` (in checkpoint.py),
    :func:`next_action_from_checkpoint`,
    :func:`checkpoint_requires_operator`, and
    :func:`evaluate_watchdog` (in watchdog.py) so the four
    helpers cannot disagree on what counts as a usable
    next action. Used by the message classifier as a sanity
    check via :func:`_has_next_action_with_value`, since the
    classifier is text-shaped and operates on substrings, not
    on field values.
    """
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return False
    # Fix R (Codex 3440952035) + Fix S (Codex 3441855393) +
    # Fix T (Codex 3441956963): strip trailing
    # sentence-ending punctuation AND leading wrapper
    # characters (R, S) so a runner that writes
    # ``"next_action: None. No repair needed."`` (R) or
    # ``"next_action: \"None.\""`` / ``"next_action:
    # [none.]"`` (S) produces an extracted value that the
    # placeholder check still recognises. Fix T regression
    # guard: the wrapper-stripped form is used ONLY for the
    # placeholder check. The field-name check uses the RAW
    # stripped value (no wrapper strip) so a real action
    # starting with a field-name word — e.g. a quoted
    # action ``"next_action: \"checkpoint current run
    # state\""`` whose extracted first token is
    # ``"checkpoint`` — is not rejected just because its
    # first token starts with a quote. Without Fix T, the
    # unconditional lstrip turns ``"checkpoint`` into
    # ``checkpoint`` and the field-name check rejects the
    # quoted action even though the unquoted action and
    # the full persisted quoted value are both valid. The
    # canonical validator now distinguishes two cases:
    # (a) ``stripped`` after wrapper-strip is in the
    # placeholder set — reject (wrapped placeholder,
    # cannot be a real action); (b) ``stripped`` (raw,
    # no wrapper strip) is a bare field name — reject
    # (bare field name, not an action). Everything else
    # is a real action and is accepted. The wrapper set
    # is ``"'([{``; the trailing-punctuation set is
    # ``.,;:!?'\")}]}``.
    trimmed = stripped.lstrip(
        _PLACEHOLDER_LEADING_WRAPPERS
    ).rstrip(_PLACEHOLDER_TRAILING_PUNCTUATION)
    if not trimmed:
        return False
    lower = trimmed.lower()
    if lower in _PLACEHOLDER_NEXT_ACTIONS:
        return False
    # Fix AL (Codex 3444118871): reject placeholder-led
    # explanatory sentences. A value like
    # ``"None. No repair needed."`` or
    # ``"N/A. Waiting for operator."`` is not itself an
    # exact placeholder token (the full sentence is not in
    # :data:`_PLACEHOLDER_NEXT_ACTIONS`), so the canonical
    # validator now also checks the first normalized token of
    # the first sentence. If that token is a placeholder,
    # the entire value is rejected as a placeholder-led
    # explanatory sentence. The check is intentionally narrow:
    # only the FIRST sentence is inspected, and the placeholder
    # vocabulary is the same documented set. Real actions that
    # contain placeholder words mid-sentence
    # (``"poll none of the failing CI checks"``) are NOT
    # rejected because the first sentence's first token is
    # ``poll``, not a placeholder.
    if _first_placeholder_token(trimmed) is not None:
        return False
    # Fix T (Codex 3441956963): field-name check uses the
    # RAW stripped value (no wrapper strip). This is the
    # regression guard against Fix S stripping a quote off
    # a real action's first token and then matching the
    # bare field name. A real quoted action whose first
    # word is a field name but has no field-assignment
    # form (no ``=``/``:``) — e.g.
    # ``"checkpoint current run state"`` — is accepted
    # because the raw form is not an exact field name.
    # The Fix U wrapper-stripped field-assignment check
    # (below) catches the quoted field-assignment
    # collision form (``"checkpoint_path=/tmp/ckpt.json"``)
    # because the full value (passed by the extractor to
    # :func:`_is_field_assignment_collision_value`) still
    # contains the ``=`` after the first token.
    if stripped.lower() in _FIELD_NAME_NEXT_ACTIONS:
        return False
    # Fix AN (Codex Finding AN / PRRT_kwDOSHFpYM6K9TsN):
    # reject FULLY-WRAPPED bare field names. A value like
    # ``"checkpoint"`` (both leading and trailing wrapper
    # characters, single word) passes the raw-stripped check
    # because ``'"checkpoint"'`` is not in
    # :data:`_FIELD_NAME_NEXT_ACTIONS`, but its
    # wrapper-stripped form ``'checkpoint'`` IS a bare field
    # name and must be rejected.
    #
    # The check is restricted to FULLY-WRAPPED single-word
    # forms so:
    # - Partial quotes (``"checkpoint`` with only a leading
    #   quote) are still accepted (Fix T regression guard).
    # - Multi-word actions that begin with a field-name word
    #   — e.g. ``"checkpoint current run state"`` — continue
    #   to be accepted (Fix T regression guard).
    # - Bare field names (without any wrapping) are still
    #   rejected by the raw-stripped check above.
    trimmed_lower = trimmed.lower()
    if (
        " " not in trimmed_lower
        and "\t" not in trimmed_lower
        and trimmed_lower in _FIELD_NAME_NEXT_ACTIONS
        and len(stripped) >= 2
        and stripped[0] in _PLACEHOLDER_LEADING_WRAPPERS
        and stripped[-1] in _PLACEHOLDER_TRAILING_PUNCTUATION
    ):
        return False
    # Fix M (Codex 3438724908): reject only real field
    # assignments, not legitimate actions that merely begin
    # with a field-name word. A value like
    # ``"checkpoint current run state"`` is a valid executable
    # action that contains the field-name word ``checkpoint``
    # but has no assignment delimiter (``=`` or ``:``); it
    # must be accepted. A value like
    # ``"checkpoint_path=/tmp/ckpt.json"`` is a true
    # field-assignment collision (the field name is followed
    # by ``=``) and must be rejected. The canonical
    # :func:`_is_field_assignment_collision_value` helper is
    # the single source of truth for persisted
    # field-assignment collision detection and is consistent
    # with the final-output extractor
    # (:func:`_extract_next_action_value`) which uses the
    # same ``_FIELD_NAME_NEXT_ACTIONS`` vocabulary.
    #
    # Fix U (Codex 3442047933): the field-assignment
    # collision check must also run on the
    # WRAPPER-STRIPPED form. When a runner quotes a
    # field-assignment value — e.g.
    # ``next_action: "checkpoint_path=/tmp/ckpt.json"`` —
    # the extracted first token is
    # ``"checkpoint_path=/tmp/ckpt.json`` (with the
    # leading quote still attached). The
    # :func:`_first_field_name_token` helper treats the
    # leading quote as a boundary character and returns
    # ``None``, so the raw stripped form passes the
    # collision check and the runner could try to resume
    # with a quoted field-assignment value. The
    # wrapper-stripped form (``trimmed``) starts with
    # the bare field name and is the same form Fix M
    # already rejects for the unquoted case, so the
    # canonical validator now runs the collision check on
    # BOTH the raw and the wrapper-stripped forms.
    if (
        _is_field_assignment_collision_value(stripped)
        or _is_field_assignment_collision_value(trimmed)
    ):
        return False
    return True


# Field-name tokens that must not appear as a next_action
# value. A next_action value that looks like another protocol
# field name is almost certainly a misuse (``next_action:
# checkpoint: /tmp/ckpt.json``) and is rejected. This set is
# the documented no-stall-protocol field vocabulary; new
# field names added to the protocol should be added here.
_FIELD_NAME_NEXT_ACTIONS: FrozenSet[str] = frozenset(
    {
        "next_action",
        "next_step",
        "checkpoint",
        "checkpoint_path",
        "phase",
        "state",
        "terminal",
        "terminal_state",
        "lifecycle",
        "next_phase",
        "pending_actions",
        "updated_at",
    }
)


# Empty-value placeholders for a checkpoint marker value.
# A checkpoint marker with one of these tokens as the value
# (``checkpoint_path=`` with nothing, ``checkpoint:``,
# ``checkpoint: none``, ``checkpoint_path: todo``) is NOT
# valid evidence of a resume point. The runner cannot
# resume from a placeholder.
#
# Fix G (Codex 3417011620) + Fix H (Codex 3417011624):
# The classifier and the watchdog evaluator must both use
# the canonical :func:`is_valid_checkpoint_path` helper so
# that a placeholder, empty, or whitespace-only value is
# rejected. ``None``, ``""``, ``"   "``, ``"none"``,
# ``"todo"``, ``"tbd"``, ``"tba"``, ``"null"``, ``"nil"``,
# ``"n/a"``, ``"na"`` are all invalid.
_CHECKPOINT_EMPTY_VALUES: FrozenSet[str] = frozenset(
    {"none", "null", "nil", "n/a", "na", "todo", "tbd", "tba"}
)


def is_valid_checkpoint_path(value: object) -> bool:
    """Return True iff ``value`` is a usable checkpoint path.

    A checkpoint path is valid iff:

    - it is a string (``isinstance(value, str)``)
    - after ``str.strip()`` it is non-empty
    - its lowercased, stripped form is NOT a placeholder
      (``none``, ``null``, ``nil``, ``n/a``, ``na``,
      ``todo``, ``tbd``, ``tba``)
    - its stripped form is path-shaped: it must start with
      a recognized path prefix
      (``/`` for absolute Unix paths,
      ``~/`` for home-relative paths,
      ``./`` or ``../`` for explicit relative paths).

    The single canonical validity check used by every
    helper that needs to decide whether a checkpoint
    field is safe to act on — both the message classifier
    (in :func:`_has_checkpoint_with_value`) and the
    watchdog evaluator (in :func:`evaluate_watchdog` in
    :mod:`aed_lifecycle.watchdog`) call this helper so the
    two cannot disagree on what counts as a usable
    checkpoint.

    Fix G (Codex 3417011620): A bare ``checkpoint_path=``
    with no value, or a ``checkpoint: none`` placeholder,
    used to slip through the classifier because
    ``_contains_any`` only checked for the substring
    ``"checkpoint_path="``. The classifier now requires
    a real value via :func:`_has_checkpoint_with_value`
    which delegates to this helper.

    Fix H (Codex 3417011624): ``bool(state.checkpoint_path)``
    used to mark a whitespace-only path as valid. The
    watchdog now uses this helper instead.

    Fix I (Codex 3417849222): a prose marker such as
    ``wrote checkpoint to pending`` previously slipped
    through the placeholder-only check because
    ``pending`` is not in :data:`_CHECKPOINT_EMPTY_VALUES`.
    The new path-shape check rejects any value that
    does not start with ``/``, ``~/``, ``./`` or
    ``../``, so a status word like ``pending``,
    ``later``, ``missing``, or ``written`` cannot
    satisfy OK_PROGRESS_WITH_NEXT_ACTION even when
    emitted after a prose marker.
    """
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.lower() in _CHECKPOINT_EMPTY_VALUES:
        return False
    # Path-shape check: the value must look like an actual
    # file path. We accept absolute Unix paths (``/``),
    # home-relative paths (``~/``), and explicit relative
    # paths (``./`` or ``../``). A bare status word or
    # placeholder like ``pending`` / ``later`` /
    # ``written`` does not start with any of these prefixes
    # and is rejected even when it follows a prose marker.
    if not (
        stripped.startswith("/")
        or stripped.startswith("~/")
        or stripped.startswith("./")
        or stripped.startswith("../")
    ):
        return False
    return True


def _line_has_explicit_terminal_assertion(line: str) -> bool:
    """Return True iff ``line`` is an explicit terminal-state assertion.

    A line is an explicit assertion when one of the following
    holds AND the line does not contain a disqualifying
    negation / future / uncertainty token in the AMBIGUOUS
    PART of the line (i.e. before the asserted state, or in
    a bare-state mention):

    - The line, after stripping leading whitespace, is
      exactly a terminal state name.
    - The line starts with one of the ``_ASSERTION_PREFIXES``
      (e.g. ``"Final lifecycle state:"``, ``"Terminal state:"``)
      followed by a recognized terminal state. Trailing
      punctuation and the em-dash-separated explanation
      (e.g. ``"Final lifecycle state: HOLD_PR_CI_PENDING —
      bounded polling reached limit"``) are accepted.
    - The line starts with a terminal state name followed by
      an em-dash (e.g. ``"HOLD_PR_CI_PENDING — bounded polling
      reached limit"``).

    Fix B (Codex 3414948252): The disqualifying-token scan is
    scoped to the ambiguous portion of the line (the part
    BEFORE an explicit assertion prefix or em-dash separator),
    not the entire line. The explanation after a valid
    assertion is allowed to contain words like ``"no"``,
    ``"not"``, ``"missing"``, or ``"after"`` without
    invalidating the assertion. For example::

        "Final lifecycle state: HOLD_RESUME_CHECKPOINT_NEEDED — no next_action/checkpoint"
        "Terminal state: HOLD_OPERATOR_REQUIRED — missing checkpoint"
        "Lifecycle state: HOLD_PR_CI_PENDING — no final check result"
        "MERGED after operator merge"

    are all valid explicit assertions, and the disqualifier
    must not fire on the explanatory text.

    The disqualifier still applies in full to BARE / AMBIGUOUS
    mentions like::

        "Not MERGED yet"
        "No MERGED state yet"
        "will be MERGED after review"
        "next state might be HOLD_PR_CI_PENDING"

    where the line is a generic statement about the state of
    the run rather than a structured assertion.
    """
    s = line.strip()
    if not s:
        return False
    # Fix K (Codex 3420442396): the prefix match is
    # case-insensitive. Both the line and each candidate
    # prefix are lower-cased for the ``startswith`` check;
    # the original-case ``s`` is preserved for state-token
    # extraction so :func:`is_terminal_lifecycle_state`
    # (which expects canonical case like ``MERGED``) keeps
    # working unchanged. ASCII case-folding preserves
    # length so the position used to slice the rest of
    # the line is the same in ``s`` and ``s_lower``.
    s_lower = s.lower()

    # Check 1: line is exactly a terminal state. A bare state
    # mention is always ambiguous — any negation/future token
    # anywhere in the line disqualifies it.
    if is_terminal_lifecycle_state(s):
        return _has_no_disqualifying_tokens(s)

    # Check 2: explicit assertion prefix. The state token is the
    # first word after the prefix; the explanation that follows
    # is allowed to contain disqualifying tokens.
    for prefix in _ASSERTION_PREFIXES:
        prefix_lower = prefix.lower()
        if s_lower.startswith(prefix_lower):
            rest = s[len(prefix_lower):].strip()
            if not rest:
                continue
            # First token is the candidate state. Trailing
            # punctuation (e.g. trailing comma) is stripped.
            first_token = rest.split(None, 1)[0].rstrip(",;:.—")
            if is_terminal_lifecycle_state(first_token):
                # The ambiguous portion is the prefix-and-state
                # span only; the explanation is ignored.
                ambiguous = s[: len(prefix_lower) + len(first_token)]
                return _has_no_disqualifying_tokens(ambiguous)

    # Check 3: state at start of line, followed by an em-dash
    # separator. The em-dash and everything after it is the
    # explanation; the disqualifier is scoped to the
    # state-and-anything-before-em-dash span.
    for state in TERMINAL_LIFECYCLE_STATES:
        for sep in (" — ", "—"):
            if s.startswith(state + sep):
                # The ambiguous portion is the state token plus
                # any leading context BEFORE the state. We
                # conservatively scope to just the state span
                # since lines of this form are typically
                # ``"STATE — explanation"`` with no leading
                # context.
                ambiguous = state
                return _has_no_disqualifying_tokens(ambiguous)

    return False


def _has_no_disqualifying_tokens(text: str) -> bool:
    """Return True iff ``text`` contains no negation / future / uncertainty token.

    Used by :func:`_line_has_explicit_terminal_assertion` to
    scope the disqualifier to the ambiguous portion of a line.
    The check is case-insensitive and uses substring matching
    against :data:`_DISQUALIFYING_TOKENS`.
    """
    lower = text.lower()
    for tok in _DISQUALIFYING_TOKENS:
        if tok in lower:
            return False
    return True


def _has_explicit_terminal_assertion(text: str) -> bool:
    """Return True iff any line in ``text`` is an explicit terminal-state assertion.

    The function splits ``text`` into lines and checks each
    line. A single line is sufficient — the runner can put
    the assertion on its own line, or include a trailing
    explanation.
    """
    for line in text.splitlines():
        if _line_has_explicit_terminal_assertion(line):
            return True
    return False


def classify_humphry_message_for_stall(text: object) -> str:
    """Classify a Humphry final-output message into a stall category.

    The classifier is the primary regression guard for the
    phase-header-only failure mode. The decision tree is:

    1. If the text contains any terminal lifecycle state token
       (substring match against the registered terminal set) →
       :data:`OK_TERMINAL`. The check is intentionally a substring
       match so a sentence like "I see HOLD_PR_CI_PENDING in the
       harness output" still classifies as terminal.

    2. If the text contains a continue-prompt token →
       :data:`STALL_WAITING_FOR_CONTINUE`.

    3. If the text contains a phase-header token (and no terminal
       state, no ``next_action``, and no checkpoint reference) →
       :data:`STALL_PHASE_HEADER_ONLY`.

    4. If the text contains a ``next_action`` token → either
       :data:`OK_PROGRESS_WITH_NEXT_ACTION` (when the run is
       mid-phase) or :data:`OK_TERMINAL` (already handled above).

    5. If the text mentions a checkpoint but no next_action and
       no terminal state → :data:`STALL_NO_CHECKPOINT`.

    6. Otherwise → :data:`STALL_NO_TERMINAL_STATE`.

    The classifier is pure and deterministic. It does not read
    from disk, the network, or any global state. Empty / non-string
    inputs return :data:`STALL_NO_TERMINAL_STATE`.
    """
    if not isinstance(text, str) or not text:
        return STALL_NO_TERMINAL_STATE

    # 1. Terminal-state detection — explicit assertion. The
    # check requires a line that is exactly a terminal state,
    # or one of the documented assertion prefixes followed by
    # a terminal state, or a terminal state followed by an
    # em-dash separator. Negated / future / uncertain
    # mentions of a terminal state are explicitly rejected.
    # This is the only way a message can become OK_TERMINAL.
    if isinstance(text, str) and _has_explicit_terminal_assertion(text):
        return OK_TERMINAL

    has_continue_prompt = _contains_any(text, _CONTINUE_PROMPT_TOKENS)
    has_phase_header = _contains_any(text, _PHASE_HEADER_TOKENS)
    # ``has_checkpoint`` is the broad check: the message
    # references a checkpoint in any form (prose mention
    # like "wrote checkpoint to" or a value-bearing marker
    # like "checkpoint: /tmp/ckpt.json"). It is used for
    # the STALL_* branches that just need to know whether
    # the runner wrote a checkpoint at all.
    #
    # The scan is case-insensitive (Fix AB, Codex
    # 3443570407) so that case-varied prose markers the
    # strict ``_extract_checkpoint_value`` already accepts
    # (e.g. ``Wrote Checkpoint To /tmp/ckpt.json`` or
    # ``Checkpoint File: /tmp/ckpt.json``) are also
    # recognized by the broad phase-header scan. Without
    # this, the two checkpoint vocabularies would be out
    # of sync for prose markers and the phase-header
    # branch would misclassify a case-varied
    # checkpoint-bearing stall as
    # ``STALL_PHASE_HEADER_ONLY``.
    has_checkpoint = _contains_any_ci(text, _CHECKPOINT_TOKENS)
    # ``has_checkpoint_with_value`` is the strict check:
    # the message contains a value-bearing checkpoint
    # marker (e.g. ``checkpoint_path=``, ``checkpoint:``)
    # followed by a real, non-empty, non-placeholder path
    # value on the same line. It is required for
    # OK_PROGRESS_WITH_NEXT_ACTION. A prose-only
    # checkpoint mention like "wrote checkpoint to"
    # satisfies ``has_checkpoint`` but NOT
    # ``has_checkpoint_with_value``, so the message still
    # falls through to STALL_NO_CHECKPOINT (because the
    # runner has not given a value-bearing resume point)
    # rather than misclassifying as OK_PROGRESS_WITH_NEXT_ACTION.
    #
    # Fix G (Codex 3417011620): The classifier used to
    # treat any substring containing ``"checkpoint_path="``
    # as evidence of a real resume point, even with no
    # value. A final message like ``Starting PHASE 2 —
    # next_action: poll CI, checkpoint_path=`` would set
    # ``has_checkpoint`` to True despite having no real
    # checkpoint path. The new ``has_checkpoint_with_value``
    # requires the marker to be followed by a valid path
    # value on the same line; the broad ``has_checkpoint``
    # still covers prose mentions for the STALL_* paths.
    has_checkpoint_with_value = _has_checkpoint_with_value(text)
    has_next_action = _has_next_action_with_value(text)

    # 2. Continue-prompt stall — outranks phase-header because the
    # user explicitly asked to stop.
    if has_continue_prompt:
        return STALL_WAITING_FOR_CONTINUE

    # 3. Phase-header classification. A phase header is only a
    # bare STALL_PHASE_HEADER_ONLY when nothing else is in the
    # message. When next_action and a value-bearing checkpoint
    # marker are both present, the message is a resumable
    # progress update and the runner should treat it as
    # OK_PROGRESS_WITH_NEXT_ACTION.
    #
    # Fix G (Codex 3417011620): The OK_PROGRESS branch
    # requires ``has_checkpoint_with_value`` (strict) rather
    # than the broad ``has_checkpoint`` so a phase-header
    # message with ``checkpoint_path=`` (no value) does NOT
    # count as having a resume point. The broad
    # ``has_checkpoint`` still drives the STALL_NO_CHECKPOINT
    # branch below so prose-only checkpoint mentions
    # continue to flag the runner.
    if has_phase_header:
        if has_next_action and has_checkpoint_with_value:
            return OK_PROGRESS_WITH_NEXT_ACTION
        # Next action but no (value-bearing) checkpoint: there
        # is something for the runner to do, but no resume
        # point — STALL_NO_CHECKPOINT.
        if has_next_action and not has_checkpoint_with_value:
            return STALL_NO_CHECKPOINT
        # Checkpoint mentioned (broad) but no next_action:
        # there is a resume point but nothing to act on.
        # STALL_NO_TERMINAL_STATE flags the generic
        # "no next action" case.
        if has_checkpoint and not has_next_action:
            return STALL_NO_TERMINAL_STATE
        # Pure phase header.
        return STALL_PHASE_HEADER_ONLY

    # 4. Progress with explicit next_action (no phase header).
    # Requires a value-bearing checkpoint marker so a bare
    # ``checkpoint_path=`` (no value) does not count.
    if has_next_action and has_checkpoint_with_value:
        return OK_PROGRESS_WITH_NEXT_ACTION
    if has_next_action and not has_checkpoint_with_value:
        # The runner named an action but provided no
        # value-bearing resume point — same stall case as
        # the phase-header branch above.
        return STALL_NO_CHECKPOINT

    # 5. Checkpoint mentioned but no next_action and no terminal
    if has_checkpoint:
        return STALL_NO_CHECKPOINT

    # 6. Default: generic progress with no terminal
    return STALL_NO_TERMINAL_STATE
