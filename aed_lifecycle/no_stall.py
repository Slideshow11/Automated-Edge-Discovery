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
from typing import FrozenSet


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
        # Successful close
        "MERGED",
        # Awaiting human authorization — runner is done, work is
        # paused for the operator. A terminal "parked" state.
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
        # Generic failure close
        "FAILED",
        # ---- Canonical AED lifecycle registry
        #      (schemas/aed_lifecycle_states_v1.json) ----
        # category=hold
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
        # category=terminal
        "PR_MERGED_AND_CLOSED_OUT",
        # category=ready but parked awaiting human
        # (already listed above: MERGE_READY_AWAITING_HUMAN_AUTHORIZATION)
        # category=mutation_pending — parked
        "PR_MERGED_PENDING_CLOSEOUT",
        # category=informational — parked on a clean Codex pass
        "CODEX_CLEAN_PASS",
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
    """
    if not isinstance(state, str):
        return False
    if not state:
        return False
    return state in TERMINAL_LIFECYCLE_STATES


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


def _has_next_action_with_value(text: str) -> bool:
    """Return True iff ``text`` contains a ``next_action:`` (or
    similar) marker followed by a non-empty, non-placeholder
    value.

    The check is per-marker: each ``next_action:`` occurrence
    must be followed by at least one non-whitespace,
    non-placeholder character. This prevents the
    no-continuation stall case the classifier is meant to
    reject, where the agent emits ``next_action:`` with no
    concrete value (e.g. ``next_action:`` or
    ``next_action: none``).
    """
    for marker in _NEXT_ACTION_TOKENS:
        idx = 0
        while True:
            pos = text.find(marker, idx)
            if pos < 0:
                break
            after = text[pos + len(marker):]
            stripped = after.lstrip()
            if not stripped:
                idx = pos + len(marker)
                continue
            # Pull the first whitespace-delimited token.
            token_end = len(stripped)
            for j, ch in enumerate(stripped):
                if ch.isspace() or ch in ",;]})\n":
                    token_end = j
                    break
            first_token = stripped[:token_end].lower()
            if first_token and first_token not in _NEXT_ACTION_EMPTY_VALUES:
                return True
            idx = pos + len(marker)
    return False


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
    {"none", "null", "n/a", "todo", "tbd", "tba"}
)


def _line_has_explicit_terminal_assertion(line: str) -> bool:
    """Return True iff ``line`` is an explicit terminal-state assertion.

    A line is an explicit assertion when one of the following
    holds AND the line does not contain a disqualifying
    negation / future / uncertainty token:

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
    """
    s = line.strip()
    if not s:
        return False

    # Disqualify any line that contains a negation, future,
    # or uncertainty token. The check is case-insensitive.
    lower = s.lower()
    for tok in _DISQUALIFYING_TOKENS:
        if tok in lower:
            return False

    # Check 1: line is exactly a terminal state.
    if is_terminal_lifecycle_state(s):
        return True

    # Check 2: explicit assertion prefix.
    for prefix in _ASSERTION_PREFIXES:
        if s.startswith(prefix):
            rest = s[len(prefix):].strip()
            if not rest:
                continue
            # First token is the candidate state.
            first_token = rest.split(None, 1)[0].rstrip(",;:.—")
            if is_terminal_lifecycle_state(first_token):
                return True

    # Check 3: state at start of line, followed by an em-dash
    # separator. This handles lines like
    # ``"HOLD_PR_CI_PENDING — bounded polling reached limit"``.
    for state in TERMINAL_LIFECYCLE_STATES:
        for sep in (" — ", "—"):
            if s.startswith(state + sep):
                return True

    return False


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
