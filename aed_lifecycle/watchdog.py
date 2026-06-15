"""AED watchdog dataclass, evaluator, and bounded polling helper (v1).

A :class:`WatchdogState` is a pure data record for a single
phase of a Humphry/Telegram run. The :func:`evaluate_watchdog`
helper takes a state and a ``now`` timestamp and returns a
verdict. The :func:`should_continue_polling` helper enforces
bounded polling — it can never return ``True`` past the max
budget.

This module does not import the AED policy engine, the
harness, the GitHub API, or any clock. All time inputs are
floats passed in by the caller. The point is to make the
watchdog deterministic and unit-testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .no_stall import (
    is_terminal_lifecycle_state,
    is_valid_checkpoint_path,
    is_valid_next_action,
)


# Verdict constants
STALL_RISK = "STALL_RISK"
"""No terminal_state, no next_action, and no checkpoint_path —
the runner is at risk of stalling and the watchdog recommends
the operator stop the run."""

WATCHDOG_PROGRESS_REQUIRED = "WATCHDOG_PROGRESS_REQUIRED"
"""Idle time exceeded ``max_idle_seconds`` but the run is not
yet at ``max_phase_seconds``. The runner should emit a progress
update or a checkpoint."""

OK_PROGRESS_WITH_NEXT_ACTION = "OK_PROGRESS_WITH_NEXT_ACTION"
"""The phase is mid-progress with an explicit ``next_action``
and a ``checkpoint_path``. The watchdog recommends the runner
continue."""

OK_TERMINAL = "OK_TERMINAL"
"""The phase has a recognized terminal state. The runner should
stop and report the terminal state."""


# When the phase-time budget is exhausted, the watchdog
# recommends a HOLD_* state rather than a stall. The specific
# HOLD state depends on the pending_action token; the
# fallback is HOLD_OPERATOR_REQUIRED.
HOLD_HEAD_CHANGED = "HOLD_HEAD_CHANGED"
HOLD_PR_CI_PENDING = "HOLD_PR_CI_PENDING"
HOLD_CODEX_RESPONSE_PENDING = "HOLD_CODEX_RESPONSE_PENDING"
HOLD_POST_MERGE_CI_PENDING = "HOLD_POST_MERGE_CI_PENDING"
HOLD_OPERATOR_REQUIRED = "HOLD_OPERATOR_REQUIRED"


@dataclass
class WatchdogState:
    """Pure data record for a single phase of a Humphry/Telegram run.

    All time values are floats in seconds. ``started_at`` and
    ``last_progress_at`` are passed in by the caller; the
    watchdog does not read the system clock.

    Field semantics:

    - ``phase_name`` — the name of the current phase (e.g.
      ``"PHASE_1"`` or ``"PHASE_5_CI_POLL"``).
    - ``started_at`` — float seconds since the epoch when the
      phase began.
    - ``last_progress_at`` — float seconds since the epoch of
      the most recent progress event (file write, API call,
      checkpoint update, etc.). Updated whenever the runner
      emits a checkpoint, a poll result, or a state transition.
    - ``max_idle_seconds`` — the watchdog's per-phase idle
      budget. If ``now - last_progress_at`` exceeds this, the
      watchdog emits :data:`WATCHDOG_PROGRESS_REQUIRED`.
    - ``max_phase_seconds`` — the watchdog's per-phase total
      budget. If ``now - started_at`` exceeds this, the
      watchdog recommends a HOLD_* state (never a stall).
    - ``next_action`` — the next string the runner is acting
      on. ``None`` means the runner has nothing queued.
    - ``checkpoint_path`` — the path to the most recent
      checkpoint file. ``None`` means the runner has never
      emitted a checkpoint.
    - ``terminal_state`` — if set, the phase is parked and
      the watchdog should recommend :data:`OK_TERMINAL`.
    """

    phase_name: str
    started_at: float
    last_progress_at: float
    max_idle_seconds: float
    max_phase_seconds: float
    next_action: Optional[str] = None
    checkpoint_path: Optional[str] = None
    terminal_state: Optional[str] = None


def _recommend_hold_for_phase_exhausted(state: WatchdogState) -> str:
    """Pick the most appropriate HOLD_* state for a phase that
    has exceeded its time budget.

    The watchdog never recommends a stall when the phase time
    is exhausted — that is a known, bounded hold, not an
    unexpected stall. The runner's last known ``next_action``
    is the strongest signal of what is being held.

    The CI and Codex detectors use word-boundary / token
    patterns rather than substring matches so that words
    containing the substring "ci" (e.g. "decide", "reconcile",
    "policy", "lifecycle", "suspicious") or "codex" (e.g.
    "codex_response_poll" is a true Codex match, but
    "codexia" would not be) are not misclassified.

    Fix C (Codex 3414948257): The CI detector requires stronger
    CI-specific tokens. Generic English verbs like "check" /
    "checks" alone are NOT classified as CI — they could refer
    to docs checks, thread-inventory checks, or any other
    non-CI verification. Only the following tokens are
    considered CI:

    - ``ci``, ``CI``
    - ``pr ci``
    - ``github actions``
    - ``workflow run``
    - ``test (3.11)`` / ``test 3.11``
    - ``status check`` / ``status checks``
    - ``required check`` / ``required checks``

    Generic phrases like "check docs", "check thread inventory",
    "run checks", "reconcile threads", "decide whether to merge",
    "policy review", "lifecycle state", "suspicious activity"
    are NOT CI and fall through to the Codex detector (or the
    operator fallback).
    """
    action = (state.next_action or "").lower()
    phase = (state.phase_name or "").lower()
    # Post-merge / main-CI closeout detection. When the
    # watchdog is in a post-merge closeout phase, the
    # correct exhaustion hold is one of the dedicated
    # post-merge main-CI holds, not the pre-merge PR-CI
    # pending state. A pre-merge PR-CI pending state would
    # send the runner down the wrong recovery path.
    #
    # Fix D (Codex 3415861213): Exhausted phases whose
    # next_action or phase_name indicates post-merge / main
    # CI closeout must report HOLD_POST_MERGE_CI_PENDING
    # rather than the pre-merge HOLD_PR_CI_PENDING. The
    # schema has three distinct post-merge closeout holds
    # (HOLD_POST_MERGE_CI_PENDING,
    # HOLD_POST_MERGE_CI_FAILED,
    # HOLD_POST_MERGE_CI_NOT_OBSERVED); pending-timeout maps
    # to HOLD_POST_MERGE_CI_PENDING. The runner distinguishes
    # failed vs not-observed at the closeout pipeline layer
    # using actual main-CI evidence; the watchdog only knows
    # whether the phase is exhausted.
    #
    # Fix F (Codex 3416424655): The post-merge branch
    # now also fires when the next_action matches a
    # post-merge token (``remote_main_ci``, ``main_ci``,
    # ``post_merge_ci``) even when the regular CI pattern
    # does not match, so that an exhausted post-merge
    # phase with action ``poll remote_main_ci`` routes to
    # HOLD_POST_MERGE_CI_PENDING instead of falling
    # through to HOLD_OPERATOR_REQUIRED.
    is_post_merge = _is_post_merge_closeout_phase(phase, action)
    if is_post_merge and _POST_MERGE_CI_ACTION_PATTERN.search(action):
        return HOLD_POST_MERGE_CI_PENDING
    if _CI_TOKEN_PATTERN.search(action):
        if is_post_merge:
            return HOLD_POST_MERGE_CI_PENDING
        return HOLD_PR_CI_PENDING
    if _CODEX_TOKEN_PATTERN.search(action):
        return HOLD_CODEX_RESPONSE_PENDING
    return HOLD_OPERATOR_REQUIRED


# Post-merge / main-CI closeout detection tokens. The
# watchdog uses these to distinguish a pre-merge PR CI hold
# from a post-merge main CI closeout hold when CI is the
# exhausted-next-action signal. The phase_name and
# next_action fields are both lowercased before matching.
#
# Fix D (Codex 3415861213): The post-merge / closeout
# detector must fire on context words ("post-merge",
# "closeout", "main CI", "remote main CI", "post merge
# main") and not on generic CI verbs like "check",
# "checks", "run checks", "wait for required checks",
# "reconcile threads", or "decide whether to merge". The
# detector is checked BEFORE the generic CI detector so
# that an exhausted phase with the word "ci" in a
# post-merge context correctly maps to
# HOLD_POST_MERGE_CI_PENDING.
#
# Fix E (Codex 3416424655): Identifier-style names like
# ``PHASE_POST_MERGE_CI``, ``post_merge_ci``,
# ``post_merge_main_ci``, ``post_merge_closeout``,
# ``remote_main_ci``, ``closeout_ci`` must also match.
# Python's ``\b`` word boundary treats ``_`` as a word
# character, so ``\bpost_merge_ci\b`` never matches
# ``PHASE_POST_MERGE_CI``. The detector now uses explicit
# identifier-style token-boundary logic that treats
# underscores, hyphens, and spaces as separators, while
# still rejecting partial-word matches like
# ``postpone``, ``mergeable``, ``mainframe``, ``claim_ci``,
# ``domain_ci`` (the prefix char is a word char so the
# boundary fails).
#
# Match the documented canonical state names from
# schemas/aed_lifecycle_states_v1.json: HOLD_POST_MERGE_CI_PENDING,
# HOLD_POST_MERGE_CI_FAILED, HOLD_POST_MERGE_CI_NOT_OBSERVED,
# and PR_MERGED_PENDING_CLOSEOUT.
_POST_MERGE_PHASE_TOKENS = (
    # Prose / multi-word forms.
    "post-merge",
    "post merge",
    "postmerge",
    "closeout",
    "close-out",
    "close out",
    "main ci",
    "remote main ci",
    "post-merge main",
    "post merge main",
    "postmerge main",
    "merged pending closeout",
    # Identifier-style (snake_case) forms. Underscores are
    # treated as token separators in the boundary class
    # below, so these are detected in phase names like
    # ``PHASE_POST_MERGE_CI``.
    "post_merge_ci",
    "post_merge_main_ci",
    "post_merge_closeout",
    "main_ci",
    "remote_main_ci",
    "closeout_ci",
)
_POST_MERGE_NEXT_ACTION_TOKENS = (
    # Prose / multi-word forms.
    "post-merge ci",
    "post merge ci",
    "postmerge ci",
    "main ci",
    "remote main ci",
    "audit post-merge",
    "audit post merge",
    "audit main ci",
    "post-merge main",
    "post merge main",
    "postmerge main",
    "post-merge main ci",
    "post merge main ci",
    # Identifier-style (snake_case) forms.
    "post_merge_ci",
    "post_merge_main_ci",
    "remote_main_ci",
    "audit_post_merge_main_ci",
)
# Identifier-aware token boundary. Standard ``\b`` is a
# transition between ``\w`` (``[A-Za-z0-9_]``) and
# non-``\w``; that fails when both sides of the match are
# word characters and one of them is an underscore
# (e.g. ``\bpost_merge_ci\b`` does not match inside
# ``phase_post_merge_ci`` because ``_`` is ``\w``). The
# post-merge detector instead requires the match to be
# bounded on both sides by any character that is NOT a
# letter, digit, or underscore. This treats underscores
# (and hyphens and spaces) as separators while still
# rejecting partial-word matches against prose words like
# ``postpone`` (the ``post`` inside ``postpone`` is followed
# by ``p``, a word char) or ``claim_ci`` (the ``main_ci``
# token would have to start with a non-word boundary, but
# the prefix char ``i`` in ``claim_ci`` is a word char).
#
# Fix E (Codex 3416424655): The post-merge detector must
# use this identifier-aware boundary so that
# ``PHASE_POST_MERGE_CI`` and ``post_merge_ci`` match
# while ``postpone``, ``mergeable``, ``mainframe``,
# ``claim_ci``, ``domain_ci`` do not.
# Identifier-aware token boundary. Standard ``\b`` is a
# transition between ``\w`` (``[A-Za-z0-9_]``) and
# non-``\w``; that fails when both sides of the match are
# word characters and one of them is an underscore
# (e.g. ``\bpost_merge_ci\b`` does not match inside
# ``phase_post_merge_ci`` because ``_`` is ``\w``). The
# post-merge detector instead requires the match to be
# bounded on both sides by any character that is NOT a
# letter or digit. This treats underscores (and hyphens
# and spaces) as separators while still rejecting
# partial-word matches against prose words like
# ``postpone`` (the ``post`` inside ``postpone`` is
# followed by ``p``, a letter) or ``claim_ci`` (the
# ``main_ci`` token would have to start with a
# non-alphanumeric boundary, but the prefix char ``i`` in
# ``claim_ci`` is a letter).
#
# The boundary class excludes ONLY ``[A-Za-z0-9]`` (NOT
# ``_``) so that snake_case identifiers like
# ``PHASE_POST_MERGE_CI`` and ``phase_post_merge_ci`` are
# treated as having underscore-separated tokens. A pure
# ``\b`` boundary treats ``_`` as a word character and
# would reject these; the identifier-aware boundary
# accepts them.
#
# Fix E (Codex 3416424655): The post-merge detector must
# use this identifier-aware boundary so that
# ``PHASE_POST_MERGE_CI`` and ``post_merge_ci`` match
# while ``postpone``, ``mergeable``, ``mainframe``,
# ``claim_ci``, ``domain_ci`` do not.
_NON_WORD_BOUNDARY = r"(?<![A-Za-z0-9])"
_NON_WORD_BOUNDARY_END = r"(?![A-Za-z0-9])"
_POST_MERGE_PHASE_PATTERN = re.compile(
    _NON_WORD_BOUNDARY
    + r"("
    + "|".join(re.escape(t) for t in _POST_MERGE_PHASE_TOKENS)
    + r")"
    + _NON_WORD_BOUNDARY_END
)
_POST_MERGE_NEXT_ACTION_PATTERN = re.compile(
    _NON_WORD_BOUNDARY
    + r"("
    + "|".join(re.escape(t) for t in _POST_MERGE_NEXT_ACTION_TOKENS)
    + r")"
    + _NON_WORD_BOUNDARY_END
)
# Post-merge / main-CI identifier-style CI-action tokens.
# These are the snake_case closeout tokens that imply a
# CI action even though the regular ``\bci\b`` pattern
# does not match (because ``_`` is a word character).
# The pattern is only consulted in the post-merge branch
# of the hold selector, so partial-word traps like
# ``claim_ci`` and ``domain_ci`` are safely rejected by
# the ``(?<![A-Za-z0-9])`` boundary: the prefix char
# before any ``main_ci`` substring inside ``claim_ci`` is
# a letter (``i``) or a letter (``o``), so the boundary
# fails.
#
# Fix F (Codex 3416424655): exhausted phases whose
# next_action contains ``remote_main_ci``, ``main_ci``,
# ``post_merge_ci``, etc. now correctly route to
# HOLD_POST_MERGE_CI_PENDING via the post-merge branch
# (gated by ``is_post_merge``) rather than falling
# through to HOLD_OPERATOR_REQUIRED.
_POST_MERGE_CI_ACTION_TOKENS = (    "post_merge_ci",
    "post_merge_main_ci",
    "post_merge_closeout",
    "main_ci",
    "remote_main_ci",
    "closeout_ci",
)
_POST_MERGE_CI_ACTION_PATTERN = re.compile(
    _NON_WORD_BOUNDARY
    + r"("
    + "|".join(re.escape(t) for t in _POST_MERGE_CI_ACTION_TOKENS)
    + r")"
    + _NON_WORD_BOUNDARY_END
)


def _is_post_merge_closeout_phase(phase: str, action: str) -> bool:
    """Return True if the phase or next action clearly
    indicates a post-merge / main-CI closeout audit.

    The detector scans for the documented post-merge
    context words in either the phase name or the next
    action. Generic English verbs like "check", "checks",
    "run checks", "wait for required checks", "reconcile
    threads", and "decide whether to merge" are NOT
    sufficient; the post-merge / closeout detector only
    fires on explicit post-merge or main-CI context words.

    Fix D (Codex 3415861213): The CI token detector uses
    \b word boundaries so that "ci" inside "decide" /
    "reconcile" / "policy" / "lifecycle" / "suspicious"
    does not match. The post-merge detector also uses
    \b word boundaries so that a phase like
    "policy review" or a next action like "check docs"
    does not falsely match.
    """
    if _POST_MERGE_PHASE_PATTERN.search(phase):
        return True
    if _POST_MERGE_NEXT_ACTION_PATTERN.search(action):
        return True
    return False


# Word-boundary patterns for CI and Codex detection. Built
# once at module load; the patterns use the ``\b`` anchor so
# that the substring "ci" inside "decide" / "reconcile" /
# "policy" / "lifecycle" / "suspicious" does not match.
#
# Fix C (Codex 3414948257): ``\bchecks?\b`` was too broad
# (matched the bare verb "check"). The CI detector now
# requires explicit CI-context tokens: the noun "ci" / "CI",
# "pr ci", "github actions", "workflow run", the GitHub-Actions
# test-3.11 job name, or the noun phrase "status check" /
# "required check". Generic "check" / "checks" alone is
# rejected.
# Identifier-aware CI token boundary. Standard ``\b`` is a
# transition between ``\w`` (``[A-Za-z0-9_]``) and
# non-``\w``; that fails when both sides of the match are
# word characters and one of them is an underscore
# (e.g. ``\bci\b`` does not match inside ``remote_main_ci``
# because ``_`` is ``\w``). The CI detector uses the same
# identifier-aware boundary as the post-merge detector for
# the ``ci`` token only, so that an exhausted next_action
# like ``poll remote_main_ci`` is recognized as a CI action
# (and then re-routed to the post-merge hold by the
# post-merge detector). Other CI context words
# (``pr ci``, ``github actions``, ``test 3.11``, etc.)
# still use the original ``\b`` boundaries because they
# already match at word boundaries in prose.
#
# Fix E (Codex 3416424655): ``\bci\b`` must also match
# ``ci`` in identifier context so that post-merge / main
# CI closeout actions like ``poll remote_main_ci`` and
# ``poll main_ci`` route to the post-merge hold, not the
# operator fallback. The boundary uses
# ``(?<![A-Za-z0-9_])...(?![A-Za-z0-9_])`` so that
# ``claim_ci`` and ``domain_ci`` still do not match (the
# prefix char before the ``ci`` substring is a word char).
_CI_IDENTIFIER_BOUNDARY = r"(?<![A-Za-z0-9_])ci(?![A-Za-z0-9_])"
_CI_TOKEN_PATTERN = re.compile(
    r"\bci\b"
    r"|\bpr\s+ci\b"
    r"|\bgithub\s+actions\b"
    r"|\bworkflow\s+run\b"
    r"|\btest\s+3\.11\b"
    r"|\btest\s*\(\s*3\.11\s*\)"
    r"|\btest\s+run\b"
    r"|\bstatus\s+checks?\b"
    r"|\brequired\s+checks?\b"
    r"|"
    + _CI_IDENTIFIER_BOUNDARY
)
_CODEX_TOKEN_PATTERN = re.compile(
    r"\bcodex(?:_response)?\b"
    r"|(?<![A-Za-z0-9_])codex(?:_response)?(?:_(?:poll|pending))?(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])(?:poll|wait_for)_codex(?:_response)?(?![A-Za-z0-9_])"
)


def evaluate_watchdog(state: WatchdogState, now: float) -> str:
    """Return a watchdog verdict for ``state`` at time ``now``.

    Decision tree (in priority order):

    1. ``terminal_state`` is set and recognized →
       :data:`OK_TERMINAL`.
    2. ``now - started_at`` exceeds ``max_phase_seconds`` →
       a HOLD_* state (the phase has hit its time budget).
    3. ``now - last_progress_at`` exceeds ``max_idle_seconds``
       → :data:`WATCHDOG_PROGRESS_REQUIRED`.
    4. ``terminal_state`` is set but unrecognized → treat as
       a hold. Recommend :data:`HOLD_OPERATOR_REQUIRED`.
    5. No terminal_state AND no valid next_action AND no
       checkpoint_path → :data:`STALL_RISK`.
    6. Otherwise (valid next_action + checkpoint_path) →
       :data:`OK_PROGRESS_WITH_NEXT_ACTION`.

    Fix C (Codex 3415107657): ``evaluate_watchdog`` must
    use the canonical :func:`is_valid_next_action` helper
    instead of a truthiness check. A ``next_action`` of
    ``""``, ``"   "``, ``"none"``, ``"null"``, ``"todo"``,
    or any other placeholder is treated as INVALID and
    produces :data:`STALL_RISK` rather than
    :data:`OK_PROGRESS_WITH_NEXT_ACTION`. ``None`` and
    non-string values are also invalid. The watchdog is
    therefore consistent with ``validate_checkpoint``,
    ``next_action_from_checkpoint``, and
    ``checkpoint_requires_operator`.
    """
    # 1. Recognized terminal state
    if state.terminal_state is not None and is_terminal_lifecycle_state(
        state.terminal_state
    ):
        return OK_TERMINAL

    # 4. Unrecognized terminal state — treat as hold. (Checked
    # before the budget check so the runner does not see
    # OK_PROGRESS_WITH_NEXT_ACTION after a typo in the state
    # name.)
    if state.terminal_state is not None and not is_terminal_lifecycle_state(
        state.terminal_state
    ):
        return HOLD_OPERATOR_REQUIRED

    # 2. Phase time exhausted → HOLD_* (never a stall).
    elapsed_phase = now - state.started_at
    if elapsed_phase > state.max_phase_seconds:
        return _recommend_hold_for_phase_exhausted(state)

    # 3. Idle time exceeded
    elapsed_idle = now - state.last_progress_at
    if elapsed_idle > state.max_idle_seconds:
        return WATCHDOG_PROGRESS_REQUIRED

    # 5. No checkpoint_path OR no valid next_action — runner is
    # at risk of stalling. OK_PROGRESS_WITH_NEXT_ACTION
    # requires BOTH fields to be present AND the next_action
    # must be a valid executable action (canonical
    # is_valid_next_action check). A checkpoint without a
    # valid next_action is the checkpoint-without-continuation
    # stall case the protocol is trying to catch, and a
    # next_action without a checkpoint has no resume point.
    # A placeholder / empty / whitespace / non-string
    # next_action is treated the same as a missing one.
    #
    # Fix H (Codex 3417011624): ``bool(state.checkpoint_path)``
    # used to mark a whitespace-only path as valid. The
    # watchdog now uses the canonical
    # :func:`is_valid_checkpoint_path` helper from
    # :mod:`aed_lifecycle.no_stall` so that ``None``,
    # ``""``, ``"   "``, ``"none"``, ``"todo"`` and other
    # placeholder strings are rejected. A blank checkpoint
    # path is not a valid resume point and must not satisfy
    # the OK_PROGRESS_WITH_NEXT_ACTION branch.
    has_valid_checkpoint = is_valid_checkpoint_path(
        state.checkpoint_path
    )
    has_valid_next_action = is_valid_next_action(state.next_action)
    if not has_valid_checkpoint or not has_valid_next_action:
        return STALL_RISK

    # 6. Mid-progress with a valid next_action and a
    # checkpoint_path
    return OK_PROGRESS_WITH_NEXT_ACTION


def should_continue_polling(
    started_at: float,
    now: float,
    max_wait_seconds: float,
    poll_count: int,
    max_polls: int,
    pending_state: str,
) -> object:
    """Bounded polling helper.

    Returns either ``True`` (continue polling) or a HOLD_*
    state name (stop and report the hold). The helper never
    returns ``False`` and never returns ``True`` past the
    budget — the runner cannot accidentally enter an
    unbounded loop by reading this helper's return value.

    The ``pending_state`` argument is the lifecycle state
    that motivated the polling (e.g. ``"HOLD_PR_CI_PENDING"``,
    ``"HOLD_CODEX_RESPONSE_PENDING"``). It is returned
    verbatim when polling must stop, so the runner can pass
    the verdict directly to the operator.

    Behavior:

    - ``poll_count >= max_polls`` → ``pending_state`` (hit the
      poll-count budget)
    - ``(now - started_at) >= max_wait_seconds`` →
      ``pending_state`` (hit the wall-clock budget)
    - both exceeded → ``pending_state`` (whichever fires first;
      in practice both produce the same verdict)
    - otherwise → ``True`` (continue)
    """
    if poll_count >= max_polls:
        return pending_state
    elapsed = now - started_at
    if elapsed >= max_wait_seconds:
        return pending_state
    return True
