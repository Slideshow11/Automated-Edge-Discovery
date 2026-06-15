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

from .no_stall import is_terminal_lifecycle_state


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
    if _CI_TOKEN_PATTERN.search(action):
        return HOLD_PR_CI_PENDING
    if _CODEX_TOKEN_PATTERN.search(action):
        return HOLD_CODEX_RESPONSE_PENDING
    return HOLD_OPERATOR_REQUIRED


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
)
_CODEX_TOKEN_PATTERN=re.compile(
    r"\bcodex(?:_response)?\b"
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
    5. No terminal_state AND no next_action AND no
       checkpoint_path → :data:`STALL_RISK`.
    6. Otherwise (next_action + checkpoint_path) →
       :data:`OK_PROGRESS_WITH_NEXT_ACTION`.
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

    # 5. No checkpoint_path OR no next_action — runner is at risk
    # of stalling. OK_PROGRESS_WITH_NEXT_ACTION requires BOTH
    # fields to be present: a checkpoint without a next_action
    # is the checkpoint-without-continuation stall case the
    # protocol is trying to catch, and a next_action without
    # a checkpoint has no resume point.
    if not state.checkpoint_path or not state.next_action:
        return STALL_RISK

    # 6. Mid-progress with a next_action and a checkpoint_path
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
