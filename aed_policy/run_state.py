"""Minimal run-state model for the AED policy engine.

The state object is the single input that the policy engine
evaluates. It is intentionally a flat dataclass with primitive
fields so it can be serialized to JSON, persisted, and
reconstructed without coupling to the harness internals.

Persistence is the caller's responsibility; this module does
not write to disk, the network, or the GitHub API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set


@dataclass
class AEDRunState:
    """The minimal inputs the policy engine needs to evaluate an action.

    Field semantics:

    - ``current_head_sha`` is the live PR head (from
      ``gh pr view --json headRefOid``); ``expected_head_sha`` is
      the SHA the agent was instructed to operate on. They must
      be equal for any merge-authorizing action to be allowed.
    - ``ci_status`` is one of ``"pass"`` / ``"fail"`` / ``"pending"``
      / ``"unknown"`` — only ``"pass"`` is acceptable for merge.
    - ``scope_status`` is one of ``"clean"`` / ``"dirty"`` /
      ``"unknown"`` — only ``"clean"`` is acceptable for merge.
    - ``merge_state_status`` is the raw GitHub value
      (``"CLEAN"`` / ``"BLOCKED"`` / ``"DIRTY"`` / ``"UNKNOWN"``).
    - ``explicit_authorization_phrase`` is the human merge
      authorization string (or None if not yet issued).
    - ``authorized_thread_ids`` is the exact list of thread IDs
      the operator has authorized for resolution; an empty list
      means no thread resolution is currently permitted.
    - ``protected_pr_numbers`` is the set of historical PR
      numbers that the harness treats as read-only by default.
    """

    # --- repo / PR identity ---
    repo: str
    pr_number: int
    branch: str
    base_branch: str

    # --- SHA fields ---
    current_head_sha: str
    expected_head_sha: str

    # --- lifecycle ---
    lifecycle_state: str

    # --- worktree state ---
    primary_worktree_path: str
    primary_worktree_head: str
    primary_worktree_branch: str
    primary_worktree_clean: bool
    isolated_workspace_path: str
    isolated_workspace_head: str
    isolated_workspace_clean: bool

    # --- PR status fields ---
    ci_status: str
    scope_status: str
    merge_state_status: str
    mergeable: bool

    # --- review-thread inventory ---
    unresolved_thread_count: int
    active_thread_count: int
    outdated_thread_count: int

    # --- Codex classifier-derived booleans ---
    codex_clean_pass_detected: bool
    codex_newer_finding_after_clean_pass: bool

    # --- Codex ping evidence ---
    codex_ping_comment_id: Optional[str]
    codex_ping_head_sha: Optional[str]

    # --- Audit append mechanism evidence ---
    audit_append_available: bool
    audit_append_only: bool

    # --- Human authorization evidence ---
    explicit_authorization_phrase: Optional[str]
    authorized_thread_ids: List[str] = field(default_factory=list)

    # --- Protected PR set ---
    protected_pr_numbers: Set[int] = field(default_factory=set)

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict with a stable shape."""
        return {
            "repo": self.repo,
            "pr_number": int(self.pr_number),
            "branch": self.branch,
            "base_branch": self.base_branch,
            "current_head_sha": self.current_head_sha,
            "expected_head_sha": self.expected_head_sha,
            "lifecycle_state": self.lifecycle_state,
            "primary_worktree_path": self.primary_worktree_path,
            "primary_worktree_head": self.primary_worktree_head,
            "primary_worktree_branch": self.primary_worktree_branch,
            "primary_worktree_clean": bool(self.primary_worktree_clean),
            "isolated_workspace_path": self.isolated_workspace_path,
            "isolated_workspace_head": self.isolated_workspace_head,
            "isolated_workspace_clean": bool(self.isolated_workspace_clean),
            "ci_status": self.ci_status,
            "scope_status": self.scope_status,
            "merge_state_status": self.merge_state_status,
            "mergeable": bool(self.mergeable),
            "unresolved_thread_count": int(self.unresolved_thread_count),
            "active_thread_count": int(self.active_thread_count),
            "outdated_thread_count": int(self.outdated_thread_count),
            "codex_clean_pass_detected": bool(self.codex_clean_pass_detected),
            "codex_newer_finding_after_clean_pass": bool(
                self.codex_newer_finding_after_clean_pass
            ),
            "codex_ping_comment_id": self.codex_ping_comment_id,
            "codex_ping_head_sha": self.codex_ping_head_sha,
            "audit_append_available": bool(self.audit_append_available),
            "audit_append_only": bool(self.audit_append_only),
            "explicit_authorization_phrase": self.explicit_authorization_phrase,
            "authorized_thread_ids": list(self.authorized_thread_ids),
            "protected_pr_numbers": sorted(int(p) for p in self.protected_pr_numbers),
        }
