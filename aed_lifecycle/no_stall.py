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
            # brackets, paren, colon) so a value like
            # ``"checkpoint: /tmp/ckpt.json"`` extracts as
            # ``"checkpoint"`` rather than the full string.
            token_end = len(stripped)
            for j, ch in enumerate(stripped):
                if ch.isspace() or ch in ",;]})\n:":
                    token_end = j
                    break
            first_token = stripped[:token_end]
            return first_token
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
_ASSERTION_PREFIXES = (
    "Final lifecycle state:",
    "Final state:",
    "Terminal state:",
    "Lifecycle state:",
    "State:",
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
        "phase",
        "terminal",
        "state",
        "lifecycle",
        "next_phase",
        "pending_actions",
        "updated_at",
    }
)


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
    has_checkpoint = _contains_any(text, _CHECKPOINT_TOKENS)
    has_next_action = _has_next_action_with_value(text)

    # 2. Continue-prompt stall — outranks phase-header because the
    # user explicitly asked to stop.
    if has_continue_prompt:
        return STALL_WAITING_FOR_CONTINUE

    # 3. Phase-header classification. A phase header is only a
    # bare STALL_PHASE_HEADER_ONLY when nothing else is in the
    # message. When next_action and a checkpoint reference are
    # both present, the message is a resumable progress update
    # and the runner should treat it as OK_PROGRESS_WITH_NEXT_ACTION.
    if has_phase_header:
        if has_next_action and has_checkpoint:
            return OK_PROGRESS_WITH_NEXT_ACTION
        # Next action but no checkpoint: there is something for
        # the runner to do, but no resume point — STALL_NO_CHECKPOINT.
        if has_next_action and not has_checkpoint:
            return STALL_NO_CHECKPOINT
        # Checkpoint but no next_action: there is a resume point
        # but nothing to act on. STALL_NO_TERMINAL_STATE flags the
        # generic "no next action" case.
        if has_checkpoint and not has_next_action:
            return STALL_NO_TERMINAL_STATE
        # Pure phase header.
        return STALL_PHASE_HEADER_ONLY

    # 4. Progress with explicit next_action (no phase header)
    if has_next_action:
        return OK_PROGRESS_WITH_NEXT_ACTION

    # 5. Checkpoint mentioned but no next_action and no terminal
    if has_checkpoint:
        return STALL_NO_CHECKPOINT

    # 6. Default: generic progress with no terminal
    return STALL_NO_TERMINAL_STATE
