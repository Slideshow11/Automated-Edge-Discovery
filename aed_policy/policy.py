"""Pure policy logic for the AED policy engine skeleton.

The policy engine is a pure function ``evaluate_action`` that maps
a proposed action and the current run state to a structured
:class:`AEDDecision`. It does not perform shell execution, does
not call the GitHub API, and does not mutate anything. Every rule
references the canonical ``AED-RULE-NNN`` identifier from
``docs/governance/aed_rules_inventory.md`` so the policy
engine's output is auditable end-to-end.

This is the v1 skeleton. Later PRs may add more rules, more
``AEDActionType`` entries, and richer state fields, but the API
shape (``evaluate_action`` -> ``AEDDecision``) is stable.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from .action_types import (
    AEDActionType,
    MUTATING_LOCAL_ACTIONS,
    READ_ONLY_ACTIONS,
)
from .decisions import AEDDecision, AEDDecisionCode
from .run_state import AEDRunState


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _deny(
    code: AEDDecisionCode,
    reason: str,
    rule_ids: Optional[List[str]] = None,
    required_evidence: Optional[List[str]] = None,
) -> AEDDecision:
    """Build a denial decision with the standard shape."""
    return AEDDecision(
        allowed=False,
        code=code,
        reason=reason,
        required_evidence=list(required_evidence or []),
        matched_rule_ids=list(rule_ids or []),
    )


def _allow(rule_ids: Optional[List[str]] = None) -> AEDDecision:
    """Build an allow decision with the standard shape."""
    return AEDDecision(
        allowed=True,
        code=AEDDecisionCode.ALLOW,
        reason="Action permitted under current policy.",
        required_evidence=[],
        matched_rule_ids=list(rule_ids or []),
    )


def _head_matches(state: AEDRunState) -> bool:
    """True iff both SHAs are present and equal."""
    if not state.current_head_sha or not state.expected_head_sha:
        return False
    return state.current_head_sha == state.expected_head_sha


def _has_explicit_authorization(state: AEDRunState) -> bool:
    """True iff the run state carries a non-empty authorization phrase."""
    return bool(
        state.explicit_authorization_phrase
        and state.explicit_authorization_phrase.strip()
    )


def _in_isolated_workspace(state: AEDRunState) -> bool:
    """True iff the isolated workspace path is a non-primary /tmp/aed_runs/... path."""
    if not state.isolated_workspace_path:
        return False
    if state.isolated_workspace_path == state.primary_worktree_path:
        return False
    if not state.isolated_workspace_path.startswith("/tmp/aed_runs/worktrees/"):
        return False
    return True


def _is_mergeable(value) -> bool:
    """Normalize GitHub's ``mergeable`` value to a boolean.

    Accepts the raw GitHub strings (``"MERGEABLE"`` /
    ``"CONFLICTING"`` / ``"UNKNOWN"``) and ``None`` (when the
    value has not been computed yet). A Python ``True`` is NOT
    accepted as a substitute for an explicit GitHub ``"MERGEABLE"``
    string: the safer skeleton default is to require explicit
    GitHub evidence, since a weak truthy value would otherwise
    pass for unverified or synthetic states. Only the literal
    string ``"MERGEABLE"`` (case-insensitive) is treated as
    mergeable.
    """
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value.strip().upper() == "MERGEABLE"
    return False


def _normalize_phrase(s: Optional[str]) -> str:
    """Collapse runs of whitespace to a single space and strip.

    Used to compare the operator-supplied authorization phrase
    against the expected phrase without allowing trivial
    whitespace differences to slip through.
    """
    if not s:
        return ""
    return " ".join(s.split())


def expected_merge_authorization_phrase(state: AEDRunState) -> str:
    """Build the canonical exact-head merge authorization phrase.

    The format is::

        I authorize guarded squash merge of PR #<pr_number>
        at exact head <40-hex expected_head_sha>.

    Whitespace inside the phrase is single-spaced. The phrase
    ends with a single period.
    """
    return (
        f"I authorize guarded squash merge of PR #{state.pr_number} "
        f"at exact head {state.expected_head_sha}."
    )


def has_exact_merge_authorization(state: AEDRunState) -> bool:
    """True iff ``state.explicit_authorization_phrase`` matches the canonical phrase.

    A phrase that is empty, None, an arbitrary string (``"ok"``,
    ``"merge"``), missing the PR number, missing the head SHA, or
    carrying a different head SHA all fail this check. Whitespace
    is normalized before comparison so a phrase with extra spaces
    or newlines is rejected only when the normalized form differs.
    """
    if not state.explicit_authorization_phrase:
        return False
    if not state.expected_head_sha:
        return False
    # The expected head SHA must be a 40-character hex string. If
    # it isn't, the engine cannot accept any authorization phrase
    # because the canonical phrase would itself be malformed.
    if not (
        isinstance(state.expected_head_sha, str)
        and len(state.expected_head_sha) == 40
        and all(c in "0123456789abcdefABCDEF" for c in state.expected_head_sha)
    ):
        return False
    expected = expected_merge_authorization_phrase(state)
    return _normalize_phrase(state.explicit_authorization_phrase) == _normalize_phrase(expected)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_action(
    action: AEDActionType,
    state: AEDRunState,
    *,
    target_thread_ids: Sequence[str] = (),
) -> AEDDecision:
    """Evaluate a proposed action against the current run state.

    The function is pure: it does not read the filesystem, call
    the network, or mutate any external state. It returns an
    :class:`AEDDecision` describing the verdict, the reason, the
    rule IDs that matched, and (for denials) the evidence the
    caller must supply to flip the decision to allow.

    For ``GITHUB_THREAD_RESOLVE`` actions, ``target_thread_ids``
    is the list of thread IDs the action is attempting to
    resolve. The engine requires this to be non-empty and a
    subset of ``state.authorized_thread_ids``. For all other
    actions, the parameter is ignored.
    """
    # AED-RULE-024: unknown action is denied by default.
    if action == AEDActionType.UNKNOWN:
        return _deny(
            AEDDecisionCode.DENY,
            "Action type UNKNOWN is denied by default; classify the action before evaluation.",
            rule_ids=["AED-RULE-024"],
        )

    # Pure read-only status checks are always allowed.
    if action in READ_ONLY_ACTIONS:
        return _allow(rule_ids=["AED-RULE-009", "AED-RULE-019"])

    # AED-RULE-001: primary worktree mutation requires explicit authorization
    # and a clean primary worktree.
    if action == AEDActionType.PRIMARY_WORKTREE_MUTATION:
        if not _has_explicit_authorization(state):
            return _deny(
                AEDDecisionCode.REQUIRE_EXPLICIT_AUTHORIZATION,
                "Primary worktree mutation requires an explicit human authorization phrase.",
                rule_ids=["AED-RULE-001"],
                required_evidence=["explicit_authorization_phrase"],
            )
        if not state.primary_worktree_clean:
            return _deny(
                AEDDecisionCode.REQUIRE_NO_PRIMARY_MUTATION,
                "Primary worktree is not clean; explicit authorization does not override a dirty primary worktree.",
                rule_ids=["AED-RULE-001"],
                required_evidence=["primary_worktree_clean=true"],
            )
        return _allow(rule_ids=["AED-RULE-001"])

    # AED-RULE-002: primary worktree sync requires explicit authorization.
    if action == AEDActionType.PRIMARY_WORKTREE_SYNC:
        if not _has_explicit_authorization(state):
            return _deny(
                AEDDecisionCode.REQUIRE_EXPLICIT_AUTHORIZATION,
                "Primary worktree sync (git pull / fetch+merge / reset / checkout) requires an explicit authorization phrase.",
                rule_ids=["AED-RULE-002"],
                required_evidence=["explicit_authorization_phrase"],
            )
        return _allow(rule_ids=["AED-RULE-002"])

    # AED-RULE-005, -007, -011, -012, -018, -019, -008, -021: merge rules.
    if action == AEDActionType.GITHUB_MERGE:
        # AED-RULE-021: protected historical PRs are read-only. Fail
        # closed when ``protected_pr_numbers`` is empty: an empty
        # set is treated as missing protected-PR evidence, so the
        # policy engine cannot confirm the current PR is *not*
        # protected and must deny.
        if not state.protected_pr_numbers:
            return _deny(
                AEDDecisionCode.DENY,
                "Merge denied: no protected-PR evidence available (AED-RULE-021); ``protected_pr_numbers`` is empty, so the engine cannot evaluate whether the current PR is protected. Supply a non-empty ``protected_pr_numbers`` set (the AED-RULE-021 default is the AED canonical historical PR list).",
                rule_ids=["AED-RULE-021"],
            )
        if state.pr_number in state.protected_pr_numbers:
            return _deny(
                AEDDecisionCode.DENY,
                f"PR #{state.pr_number} is a protected historical PR; merge is denied by default.",
                rule_ids=["AED-RULE-021"],
            )
        # AED-RULE-005: live head must match expected head.
        if not _head_matches(state):
            return _deny(
                AEDDecisionCode.REQUIRE_EXACT_HEAD_AUTHORIZATION,
                "Merge denied: current head SHA does not match expected head SHA; re-verify the live PR head before merge.",
                rule_ids=["AED-RULE-005"],
                required_evidence=["current_head_sha==expected_head_sha"],
            )
        # AED-RULE-007: human authorization phrase required, and it
        # must match the canonical exact-head phrase.
        if not has_exact_merge_authorization(state):
            return _deny(
                AEDDecisionCode.REQUIRE_EXPLICIT_AUTHORIZATION,
                f"Merge denied: explicit_authorization_phrase does not match the canonical exact-head phrase. Expected: I authorize guarded squash merge of PR #{state.pr_number} at exact head {state.expected_head_sha}.",
                rule_ids=["AED-RULE-007", "AED-RULE-005"],
                required_evidence=[
                    f"explicit_authorization_phrase == I authorize guarded squash merge of PR #{state.pr_number} at exact head {state.expected_head_sha}."
                ],
            )
        # AED-RULE-018: all required CI checks must pass.
        if state.ci_status != "pass":
            return _deny(
                AEDDecisionCode.REQUIRE_CLEAN_CI,
                f"Merge denied: CI status is '{state.ci_status}', expected 'pass'.",
                rule_ids=["AED-RULE-018"],
                required_evidence=["ci_status=pass"],
            )
        # AED-RULE-019: scope guard must report SCOPE_CLEAN.
        if state.scope_status != "clean":
            return _deny(
                AEDDecisionCode.REQUIRE_CLEAN_SCOPE,
                f"Merge denied: scope status is '{state.scope_status}', expected 'clean'.",
                rule_ids=["AED-RULE-019"],
                required_evidence=["scope_status=clean"],
            )
        # AED-RULE-019: merge_state_status must be CLEAN.
        if state.merge_state_status != "CLEAN":
            return _deny(
                AEDDecisionCode.REQUIRE_CLEAN_MERGE_STATE,
                f"Merge denied: merge state status is '{state.merge_state_status}', expected 'CLEAN'.",
                rule_ids=["AED-RULE-019"],
                required_evidence=["merge_state_status=CLEAN"],
            )
        # AED-RULE-019: GitHub's mergeable field must explicitly
        # indicate MERGEABLE. The mergeable field is GitHub's per-PR
        # mergeability value; it can be one of "MERGEABLE" /
        # "CONFLICTING" / "UNKNOWN" or None. Only the literal string
        # "MERGEABLE" (case-insensitive) is acceptable for merge.
        # A Python True is NOT accepted as a substitute for
        # explicit GitHub evidence.
        if not _is_mergeable(state.mergeable):
            return _deny(
                AEDDecisionCode.REQUIRE_CLEAN_MERGE_STATE,
                f"Merge denied: GitHub mergeable status is {state.mergeable!r}, expected 'MERGEABLE'.",
                rule_ids=["AED-RULE-019"],
                required_evidence=["mergeable == 'MERGEABLE'"],
            )
        # AED-RULE-008: no unresolved review threads.
        if state.unresolved_thread_count > 0:
            return _deny(
                AEDDecisionCode.REQUIRE_NO_UNRESOLVED_THREADS,
                f"Merge denied: {state.unresolved_thread_count} unresolved review thread(s) remain.",
                rule_ids=["AED-RULE-008"],
                required_evidence=["unresolved_thread_count=0"],
            )
        # AED-RULE-011: current-head Codex clean-pass evidence
        # required, and the clean pass must be tied to the current
        # expected head (so a stale clean pass from a prior head
        # cannot satisfy AED-RULE-011).
        if not state.codex_clean_pass_detected:
            return _deny(
                AEDDecisionCode.HOLD,
                "Merge denied: no current-head Codex clean-pass evidence; the policy engine does not authorize a merge until the classifier reports a clean pass tied to the current head.",
                rule_ids=["AED-RULE-011"],
                required_evidence=["codex_clean_pass_detected=true"],
            )
        if (
            state.codex_clean_pass_head_sha is None
            or state.codex_clean_pass_head_sha != state.expected_head_sha
        ):
            return _deny(
                AEDDecisionCode.HOLD,
                f"Merge denied: Codex clean-pass evidence is not tied to the current expected head (clean-pass head = {state.codex_clean_pass_head_sha!r}, expected head = {state.expected_head_sha!r}); a stale clean pass from a prior head does not satisfy AED-RULE-011.",
                rule_ids=["AED-RULE-005", "AED-RULE-011"],
                required_evidence=["codex_clean_pass_head_sha == expected_head_sha"],
            )
        # AED-RULE-012: no newer non-clean finding after the clean pass.
        if state.codex_newer_finding_after_clean_pass:
            return _deny(
                AEDDecisionCode.HOLD,
                "Merge denied: a newer non-clean Codex finding appeared after the current-head clean pass.",
                rule_ids=["AED-RULE-011", "AED-RULE-012"],
            )
        return _allow(
            rule_ids=[
                "AED-RULE-005",
                "AED-RULE-007",
                "AED-RULE-008",
                "AED-RULE-011",
                "AED-RULE-012",
                "AED-RULE-018",
                "AED-RULE-019",
                "AED-RULE-021",
            ]
        )

    # AED-RULE-008: thread resolution requires (a) a live-head check,
    # (b) an explicit non-empty target thread set carried by the
    # action, and (c) every target thread ID to be included in the
    # authorized thread list. AED-RULE-005 applies to thread
    # resolution as well as merge.
    if action == AEDActionType.GITHUB_THREAD_RESOLVE:
        # AED-RULE-005: live head must match expected head before resolving threads.
        if not _head_matches(state):
            return _deny(
                AEDDecisionCode.REQUIRE_EXACT_HEAD_AUTHORIZATION,
                "Thread resolution denied: current head SHA does not match expected head SHA; re-verify the live PR head before resolving threads (AED-RULE-005 applies to thread resolution as well as merge).",
                rule_ids=["AED-RULE-005"],
                required_evidence=["current_head_sha==expected_head_sha"],
            )
        # AED-RULE-008: target thread IDs must be supplied by the
        # action. A non-empty ``authorized_thread_ids`` is not by
        # itself authorization to resolve a thread: the action must
        # name the threads it intends to resolve so the policy
        # engine can verify the target set is a subset of the
        # authorization.
        target_set = tuple(target_thread_ids or ())
        if not target_set:
            return _deny(
                AEDDecisionCode.REQUIRE_THREAD_LIST_AUTHORIZATION,
                "Thread resolution denied: no target thread ID supplied with the action. Supply ``target_thread_ids`` to the policy engine so the action can be checked against the authorized set.",
                rule_ids=["AED-RULE-008"],
                required_evidence=["target_thread_ids (non-empty list)"],
            )
        # AED-RULE-008: an authorized thread list is required.
        if not state.authorized_thread_ids:
            return _deny(
                AEDDecisionCode.REQUIRE_THREAD_LIST_AUTHORIZATION,
                "Thread resolution denied: no authorized thread list provided; supply the exact list of thread IDs the operator has approved for resolution.",
                rule_ids=["AED-RULE-008"],
                required_evidence=["authorized_thread_ids (non-empty list)"],
            )
        # AED-RULE-008: every target thread ID must be in the
        # authorized set. Resolving PRRT_other with authorization
        # for PRRT_target must be denied.
        authorized_set = set(state.authorized_thread_ids)
        unauthorized_targets = [t for t in target_set if t not in authorized_set]
        if unauthorized_targets:
            return _deny(
                AEDDecisionCode.REQUIRE_THREAD_LIST_AUTHORIZATION,
                f"Thread resolution denied: target thread ID(s) {sorted(unauthorized_targets)!r} are not in the authorized set {sorted(authorized_set)!r}; the action may only resolve threads explicitly named in the authorization.",
                rule_ids=["AED-RULE-008"],
                required_evidence=[
                    f"target_thread_ids is a subset of {sorted(state.authorized_thread_ids)!r}"
                ],
            )
        return _allow(rule_ids=["AED-RULE-005", "AED-RULE-008"])

    # AED-RULE-021: PR reopen is a state mutation; protected PRs are read-only.
    # Fail closed when ``protected_pr_numbers`` is empty: an empty
    # set is treated as missing protected-PR evidence.
    if action == AEDActionType.GITHUB_REOPEN:
        if not state.protected_pr_numbers:
            return _deny(
                AEDDecisionCode.DENY,
                "Reopen denied: no protected-PR evidence available (AED-RULE-021); ``protected_pr_numbers`` is empty, so the engine cannot evaluate whether the current PR is protected. Supply a non-empty ``protected_pr_numbers`` set.",
                rule_ids=["AED-RULE-021"],
            )
        if state.pr_number in state.protected_pr_numbers:
            return _deny(
                AEDDecisionCode.DENY,
                f"PR #{state.pr_number} is a protected historical PR; reopen is denied by default.",
                rule_ids=["AED-RULE-021"],
            )
        if not _has_explicit_authorization(state):
            return _deny(
                AEDDecisionCode.REQUIRE_EXPLICIT_AUTHORIZATION,
                "PR reopen is a GitHub PR state mutation; requires explicit operator authorization and lifecycle validation before invocation.",
                rule_ids=["AED-RULE-021"],
                required_evidence=["explicit_authorization_phrase"],
            )
        return _allow(rule_ids=["AED-RULE-021"])

    # GITHUB_COMMENT is intentionally permissive in this skeleton; the
    # gate-safe-comment-language rule (AED-RULE-023) is enforced at the
    # review-comment gate, not here.
    if action == AEDActionType.GITHUB_COMMENT:
        return _allow(rule_ids=[])

    # AED-RULE-010: exactly one Codex ping per head. Plus AED-RULE-005:
    # the live head must be verified before posting a ping.
    if action == AEDActionType.CODEX_PING:
        # AED-RULE-005: live head must match expected head before posting a ping.
        if not _head_matches(state):
            return _deny(
                AEDDecisionCode.REQUIRE_EXACT_HEAD_AUTHORIZATION,
                "Codex ping denied: current head SHA does not match expected head SHA; re-verify the live PR head before posting a Codex ping (AED-RULE-005 applies to Codex pings as well as merge).",
                rule_ids=["AED-RULE-005"],
                required_evidence=["current_head_sha==expected_head_sha"],
            )
        if (
            state.codex_ping_comment_id
            and state.codex_ping_head_sha
            and state.codex_ping_head_sha == state.expected_head_sha
        ):
            return _deny(
                AEDDecisionCode.REQUIRE_NO_DUPLICATE_CODEX_PING,
                f"Codex ping denied: a ping comment already exists for the current expected head ({state.codex_ping_head_sha}); duplicate pings are refused.",
                rule_ids=["AED-RULE-010"],
                required_evidence=["codex_ping_head_sha != expected_head_sha"],
            )
        return _allow(rule_ids=["AED-RULE-005", "AED-RULE-010"])

    # AED-RULE-020: audit append requires an append-only mechanism evidence.
    if action == AEDActionType.AUDIT_APPEND:
        if not state.audit_append_available or not state.audit_append_only:
            return _deny(
                AEDDecisionCode.REQUIRE_APPEND_ONLY_AUDIT,
                "Audit append denied: append-only audit mechanism evidence is missing or not append-only.",
                rule_ids=["AED-RULE-020"],
                required_evidence=[
                    "audit_append_available=true",
                    "audit_append_only=true",
                ],
            )
        return _allow(rule_ids=["AED-RULE-020"])

    # AED-RULE-003: file writes and local mutating actions require an
    # isolated workspace.
    if action in MUTATING_LOCAL_ACTIONS:
        if not _in_isolated_workspace(state):
            return _deny(
                AEDDecisionCode.REQUIRE_ISOLATED_WORKSPACE,
                f"Action {action.value} denied: not inside an isolated workspace under /tmp/aed_runs/worktrees/.",
                rule_ids=["AED-RULE-003"],
                required_evidence=[
                    "isolated_workspace_path under /tmp/aed_runs/worktrees/",
                ],
            )
        return _allow(rule_ids=["AED-RULE-003"])

    # AED-RULE-024: catch-all deny.
    return _deny(
        AEDDecisionCode.DENY,
        f"Action {action.value} is not covered by the current policy skeleton and is denied by default.",
        rule_ids=["AED-RULE-024"],
    )
