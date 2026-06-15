"""Typed action categories for the AED policy engine.

These constants represent the kinds of operations the harness may
later broker. They are stable identifiers used as keys in the
decision tables and as references in rule IDs.

Adding a new action type here is the way to add a new
``AEDActionType``; downstream rules in :mod:`aed_policy.policy`
are matched against these strings.
"""
from __future__ import annotations

from enum import Enum


class AEDActionType(str, Enum):
    """Stable identifiers for actions the AED harness may broker."""

    # --- Pure read / status checks (always allow) ---
    READ_ONLY_STATUS = "READ_ONLY_STATUS"
    FILE_READ = "FILE_READ"
    GIT_READ_ONLY = "GIT_READ_ONLY"
    GITHUB_READ_ONLY = "GITHUB_READ_ONLY"

    # --- Local file / shell / git mutations ---
    FILE_WRITE = "FILE_WRITE"
    TERMINAL_READ_ONLY = "TERMINAL_READ_ONLY"
    TERMINAL_MUTATING = "TERMINAL_MUTATING"
    GIT_MUTATING = "GIT_MUTATING"

    # --- GitHub operations ---
    GITHUB_COMMENT = "GITHUB_COMMENT"
    GITHUB_THREAD_RESOLVE = "GITHUB_THREAD_RESOLVE"
    GITHUB_MERGE = "GITHUB_MERGE"
    GITHUB_REOPEN = "GITHUB_REOPEN"

    # --- Codex / harness / audit operations ---
    CODEX_PING = "CODEX_PING"
    AUDIT_APPEND = "AUDIT_APPEND"

    # --- Primary worktree protections ---
    PRIMARY_WORKTREE_SYNC = "PRIMARY_WORKTREE_SYNC"
    PRIMARY_WORKTREE_MUTATION = "PRIMARY_WORKTREE_MUTATION"

    # --- Catch-all (deny by default) ---
    UNKNOWN = "UNKNOWN"


# Convenience sets used by the policy rules.
READ_ONLY_ACTIONS = frozenset(
    {
        AEDActionType.READ_ONLY_STATUS,
        AEDActionType.FILE_READ,
        AEDActionType.GIT_READ_ONLY,
        AEDActionType.GITHUB_READ_ONLY,
        AEDActionType.TERMINAL_READ_ONLY,
    }
)

MUTATING_LOCAL_ACTIONS = frozenset(
    {
        AEDActionType.FILE_WRITE,
        AEDActionType.TERMINAL_MUTATING,
        AEDActionType.GIT_MUTATING,
    }
)
