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


# AED-RULE-021: the canonical set of historical PR numbers that
# the harness treats as read-only by default. The set deliberately
# excludes the current PR (e.g. PR #404) so the default does not
# lock the engine's own PR.
#
# The list is the union of:
#   PR #384 (initial protected historicals list — supersedes
#            nothing here; listed as a baseline)
#   PR #386 (tooling/guarded-pr-closeout-waiter)
#   PR #397 (tooling: add lifecycle state registry)
#   PR #398 (docs: codify audit append-only closeout rule)
#   PR #399 (docs: codify resume checkpoint continuation rule)
#   PR #400 (docs: codify primary worktree sync policy)
#   PR #401 (tooling: handle superseded main CI audit runs)
#   PR #402 (tooling: classify Codex responses across comments and reviews)
#   PR #403 (docs(governance): inventory AED rules and enforcement map)
#
# Creating an ``AEDRunState`` without explicitly supplying
# ``protected_pr_numbers`` will still protect these PRs (AED-RULE-021).
DEFAULT_PROTECTED_PR_NUMBERS: tuple = (
    384,
    386,
    397,
    398,
    399,
    400,
    401,
    402,
    403,
)


def _default_protected_pr_numbers() -> Set[int]:
    """Default factory: returns a fresh copy of the protected PR set.

    A fresh copy is returned so callers who mutate the set
    locally do not affect the canonical constant.
    """
    return set(DEFAULT_PROTECTED_PR_NUMBERS)


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
    # ``mergeable`` carries GitHub's per-PR mergeability status.
    # It is one of ``"MERGEABLE"``, ``"CONFLICTING"``, ``"UNKNOWN"``,
    # or ``None`` (when the value has not been computed yet).
    # The field is intentionally typed as ``Optional[str]`` so the
    # harness can pass the raw GitHub value through without losing
    # the distinction between the three states. The policy engine
    # uses a helper to normalize this value for allow/deny checks.
    mergeable: Optional[str]

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
    # AED-RULE-021: by default, the engine protects the historical
    # PRs in ``DEFAULT_PROTECTED_PR_NUMBERS``. Explicitly passing an
    # empty set is allowed (the policy engine treats it as missing
    # protected-PR evidence and denies).
    protected_pr_numbers: Set[int] = field(
        default_factory=_default_protected_pr_numbers
    )

    # The head SHA that the current Codex clean-pass evidence is
    # tied to. When ``codex_clean_pass_detected`` is True, this
    # field carries the head SHA the classifier observed for the
    # clean pass. The policy engine requires this SHA to equal
    # ``expected_head_sha`` before allowing a merge, so a stale
    # clean pass from a prior head cannot satisfy AED-RULE-011.
    codex_clean_pass_head_sha: Optional[str] = None

    # A derived boolean the harness can populate to record whether
    # the clean pass evidence is tied to the *current* expected
    # head. AED-RULE-011 requires that a merge be backed by
    # current-head clean evidence; the policy engine treats
    # ``codex_clean_pass_for_current_head`` as the canonical
    # current-head indicator and rejects any merge for which it
    # is False. The field is intentionally separate from
    # ``codex_clean_pass_head_sha`` so the classifier can encode
    # the comparison as a single boolean while still exposing the
    # raw head SHA for audit.
    codex_clean_pass_for_current_head: bool = False

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
            "mergeable": self.mergeable,
            "unresolved_thread_count": int(self.unresolved_thread_count),
            "active_thread_count": int(self.active_thread_count),
            "outdated_thread_count": int(self.outdated_thread_count),
            "codex_clean_pass_detected": bool(self.codex_clean_pass_detected),
            "codex_newer_finding_after_clean_pass": bool(
                self.codex_newer_finding_after_clean_pass
            ),
            "codex_clean_pass_head_sha": self.codex_clean_pass_head_sha,
            "codex_clean_pass_for_current_head": bool(
                self.codex_clean_pass_for_current_head
            ),
            "codex_ping_comment_id": self.codex_ping_comment_id,
            "codex_ping_head_sha": self.codex_ping_head_sha,
            "audit_append_available": bool(self.audit_append_available),
            "audit_append_only": bool(self.audit_append_only),
            "explicit_authorization_phrase": self.explicit_authorization_phrase,
            "authorized_thread_ids": list(self.authorized_thread_ids),
            "protected_pr_numbers": sorted(int(p) for p in self.protected_pr_numbers),
        }
