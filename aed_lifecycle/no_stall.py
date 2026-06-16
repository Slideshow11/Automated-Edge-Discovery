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
"""The final output is mid-phase progress, but explicitly carries
a ``next_action`` (and optionally a checkpoint path) so the runner
can resume from where it left off."""

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
_CHECKPOINT_TOKENS = (
    "checkpoint_path=",
    "checkpoint: ",
    "checkpoint_path:",
    "checkpoint=",
    "wrote checkpoint to",
    "saved checkpoint to",
    "checkpoint file",
    "checkpoint at",
)


# Substrings that mark an explicit next_action reference.
# Each entry is the literal marker the agent emits. A bare
# ``next_action:`` with no value is intentionally NOT sufficient
# on its own — the classifier requires both the marker AND a
# non-empty, non-placeholder value before classifying as
# OK_PROGRESS_WITH_NEXT_ACTION.
_NEXT_ACTION_TOKENS = (
    "next_action=",
    "next_action:",
    "Next action:",
    "next step:",
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
    """
    for raw_line in text.splitlines():
        line = raw_line
        for marker in _NEXT_ACTION_TOKENS:
            idx = line.find(marker)
            if idx < 0:
                continue
            after = line[idx + len(marker):]
            stripped = after.lstrip()
            if not stripped:
                # Marker is the last token on the line — empty
                # value, keep scanning for other markers.
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
            if _is_field_assignment_collision(first_token):
                # The remainder is a field=value assignment
                # (e.g. ``checkpoint_path=/tmp/ckpt.json``).
                # The agent used a protocol field name as
                # a value; this is not an executable action.
                # Skip this marker and keep scanning.
                continue
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
# The marker tuple mixes field-style markers (colon/equals
# delimited) and prose-style markers (space delimited) so
# that a final output like ``wrote checkpoint to
# /tmp/ckpt.json`` parses the same way as ``checkpoint:
# /tmp/ckpt.json``. Prose markers require a path-shaped
# value (see :func:`is_valid_checkpoint_path`); a marker
# like ``wrote checkpoint to pending`` is rejected because
# ``pending`` is not path-shaped. The previous bare-marker
# bug (``Checkpoint pending`` slipping through as a real
# value) is still pinned: bare ``Checkpoint `` /
# ``checkpoint `` / ``Checkpoint file`` markers without a
# colon, equals, or trailing-space path-suffix are not
# in this tuple.
_CHECKPOINT_MARKERS = (
    # Field-style markers (colon or equals delimited).
    "checkpoint_path=",
    "checkpoint_path:",
    "Checkpoint path:",
    "Checkpoint Path:",
    "checkpoint=",
    "checkpoint:",
    "Checkpoint:",
    "checkpoint =",
    # Prose-style markers (space delimited). Each entry is
    # the literal marker followed by a trailing space so
    # that ``checkpoint file`` does NOT match
    # ``checkpoint file: /tmp/ckpt.json`` (the colon breaks
    # the space-suffix match — the explicit
    # ``checkpoint file: `` entry below handles that
    # form).
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
    :data:`_CHECKPOINT_MARKERS` entries. The extractor
    walks each line of the message, finds the first
    marker occurrence, and pulls the first whitespace-
    delimited token on the same line — bounded so that
    the value never consumes the next field name (e.g.
    ``"next_action: poll CI"`` is not consumed when
    parsing ``"checkpoint_path=/tmp/ckpt.json"``).

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
    """
    for raw_line in text.splitlines():
        line = raw_line
        for marker in _CHECKPOINT_MARKERS:
            idx = line.find(marker)
            if idx < 0:
                continue
            after = line[idx + len(marker):]
            stripped = after.lstrip()
            if not stripped:
                # Marker is the last token on the line —
                # empty value, keep scanning.
                continue
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
)


# Placeholder next_action values that the validator rejects.
# Mirrors ``_NEXT_ACTION_EMPTY_VALUES`` in
# :mod:`aed_lifecycle.no_stall` for cross-module consistency.
_PLACEHOLDER_NEXT_ACTIONS = frozenset(
    {"none", "null", "n/a", "na", "todo", "tbd", "tba", "nil"}
)


def is_valid_next_action(value: object) -> bool:
    """Return True iff ``value`` is a usable ``next_action``.

    Fix D (Codex 3414948261) + Fix B (Codex 3415107653): The
    single canonical next-action validity check used by every
    helper that needs to decide whether a ``next_action``
    field is safe to act on. A ``next_action`` is valid iff:

    - it is a string (``isinstance(value, str)``)
    - after ``str.strip()`` it is non-empty
    - its lowercased, stripped form is NOT a placeholder
      (``none``, ``null``, ``nil``, ``n/a``, ``na``, ``todo``,
      ``tbd``, ``tba``)
    - its lowercased, stripped form is NOT itself a field
      name. The set of rejected field names is the documented
      no-stall-protocol field set: ``next_action``,
      ``next_step``, ``checkpoint``, ``phase``, ``terminal``,
      ``state``, ``lifecycle``, ``next_phase``,
      ``pending_actions``, ``updated_at``. A value that
      matches one of these is treated as a field-name
      collision (``next_action: checkpoint: /tmp/ckpt.json``)
      and is rejected. This catches the case where the agent
      misuses a field as a value rather than treating the
      marker as a no-value placeholder.

    Anything else — ``None``, ``""``, ``"   "``, ``"none"``,
    ``"todo"``, ``"checkpoint"``, ``123``, ``[]``, ``{}`` —
    is invalid.

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
    lower = stripped.lower()
    if lower in _PLACEHOLDER_NEXT_ACTIONS:
        return False
    if lower in _FIELD_NAME_NEXT_ACTIONS:
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

    # Check 1: line is exactly a terminal state. A bare state
    # mention is always ambiguous — any negation/future token
    # anywhere in the line disqualifies it.
    if is_terminal_lifecycle_state(s):
        return _has_no_disqualifying_tokens(s)

    # Check 2: explicit assertion prefix. The state token is the
    # first word after the prefix; the explanation that follows
    # is allowed to contain disqualifying tokens.
    for prefix in _ASSERTION_PREFIXES:
        if s.startswith(prefix):
            rest = s[len(prefix):].strip()
            if not rest:
                continue
            # First token is the candidate state. Trailing
            # punctuation (e.g. trailing comma) is stripped.
            first_token = rest.split(None, 1)[0].rstrip(",;:.—")
            if is_terminal_lifecycle_state(first_token):
                # The ambiguous portion is the prefix-and-state
                # span only; the explanation is ignored.
                ambiguous = s[: len(prefix) + len(first_token)]
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
    has_checkpoint = _contains_any(text, _CHECKPOINT_TOKENS)
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
