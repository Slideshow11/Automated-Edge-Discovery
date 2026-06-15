"""AED no-stall lifecycle skeleton (v1).

This package is a pure, testable no-stall watchdog/checkpoint
skeleton. It is intentionally a leaf package — it does not
import the AED policy engine, the harness, the audit log
appender, the merge guard, or any live tool. The three
submodules are:

- :mod:`aed_lifecycle.no_stall` — terminal-state registry and
  final-output classifier
- :mod:`aed_lifecycle.checkpoint` — checkpoint dataclass and
  pure resume helpers
- :mod:`aed_lifecycle.watchdog` — watchdog dataclass, evaluator,
  and bounded polling helper

This package does not wire into Telegram, Humphry, OpenHands,
GitHub webhooks, or live merge scripts. Wiring comes in a
later PR. The point of v1 is to make phase-header-only final
outputs detectable, testable, and rejection-prone in
downstream harnesses.
"""
from __future__ import annotations

from .checkpoint import (
    CheckpointState,
    checkpoint_requires_operator,
    next_action_from_checkpoint,
    validate_checkpoint,
    validate_resume_observations,
)
from .no_stall import (
    OK_PROGRESS_WITH_NEXT_ACTION,
    OK_TERMINAL,
    STALL_NO_CHECKPOINT,
    STALL_NO_TERMINAL_STATE,
    STALL_PHASE_HEADER_ONLY,
    STALL_WAITING_FOR_CONTINUE,
    TERMINAL_LIFECYCLE_STATES,
    classify_humphry_message_for_stall,
    is_terminal_lifecycle_state,
)
from .watchdog import (
    STALL_RISK,
    WATCHDOG_PROGRESS_REQUIRED,
    WatchdogState,
    evaluate_watchdog,
    should_continue_polling,
)

__all__ = [
    # no_stall
    "OK_PROGRESS_WITH_NEXT_ACTION",
    "OK_TERMINAL",
    "STALL_NO_CHECKPOINT",
    "STALL_NO_TERMINAL_STATE",
    "STALL_PHASE_HEADER_ONLY",
    "STALL_WAITING_FOR_CONTINUE",
    "TERMINAL_LIFECYCLE_STATES",
    "classify_humphry_message_for_stall",
    "is_terminal_lifecycle_state",
    # checkpoint
    "CheckpointState",
    "checkpoint_requires_operator",
    "next_action_from_checkpoint",
    "validate_checkpoint",
    "validate_resume_observations",
    # watchdog
    "STALL_RISK",
    "WATCHDOG_PROGRESS_REQUIRED",
    "WatchdogState",
    "evaluate_watchdog",
    "should_continue_polling",
]
