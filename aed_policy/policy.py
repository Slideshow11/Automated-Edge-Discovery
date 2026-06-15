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

import os
from typing import Iterable, List, Optional, Sequence, Tuple

from .action_types import (
    AEDActionType,
    MUTATING_LOCAL_ACTIONS,
    READ_ONLY_ACTIONS,
)
from .decisions import AEDDecision, AEDDecisionCode
from .run_state import AEDRunState


# AED-RULE-003 / canonical-path hardening: the canonical root for
# isolated workspaces. All workspace paths must resolve to a path
# strictly inside this directory tree. The constant is a
# module-private string (not a function) so test code can use it
# directly and so the policy engine never accepts a path that
# escapes the root via dot-segment or symlink manipulation.
_ISOLATED_WORKSPACE_ROOT = "/tmp/aed_runs/worktrees"


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


_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _is_full_sha(value: object) -> bool:
    """True iff ``value`` is a 40-character hexadecimal SHA string.

    Abbreviated SHAs (e.g. ``"abc1234"``), non-string values, and
    40-character strings containing non-hex characters all return
    False. This is the strict variant required by AED-RULE-004
    for exact-head guarded actions, and by the AED-RULE-005 / -011
    / -019 / -008 checks in this skeleton.
    """
    if not isinstance(value, str):
        return False
    if len(value) != 40:
        return False
    return all(c in _HEX_DIGITS for c in value)


def _head_matches(state: AEDRunState) -> bool:
    """True iff both SHAs are full 40-character hex strings and equal.

    The strict variant requires exact, full, hex SHAs. Abbreviated
    SHAs (matching or otherwise) are rejected, because the
    AED-RULE-004 / -005 exact-head checks operate on full SHAs
    and abbreviating the head is a common input-validation
    pitfall: callers could otherwise pass matching abbreviated
    SHAs and slip past the live-head check on CODEX_PING,
    GITHUB_THREAD_RESOLVE, and GITHUB_MERGE.
    """
    if not _is_full_sha(state.current_head_sha):
        return False
    if not _is_full_sha(state.expected_head_sha):
        return False
    return state.current_head_sha == state.expected_head_sha


def _has_explicit_authorization(state: AEDRunState) -> bool:
    """True iff the run state carries a non-empty authorization phrase."""
    return bool(
        state.explicit_authorization_phrase
        and state.explicit_authorization_phrase.strip()
    )


def _canonicalize_path(path: object) -> Optional[str]:
    """Canonicalize ``path`` to an absolute, symlink-resolved string.

    Returns the canonicalized absolute path, or ``None`` if the
    path is not a non-empty string. The function uses
    ``os.path.realpath`` so dot-segment escapes
    (``/tmp/aed_runs/worktrees/../../home/max/...``) and symlink
    escapes resolve to the actual on-disk location before
    comparison. Pure policy logic: no shell execution, no
    mutation.
    """
    if not isinstance(path, str):
        return None
    if not path:
        return None
    return os.path.realpath(path)


def _is_under(path: str, root: str) -> bool:
    """True iff the canonicalized ``path`` is strictly below ``root``.

    Both ``path`` and ``root`` are expected to already be
    canonicalized via ``_canonicalize_path`` (or otherwise
    absolute and symlink-resolved). The function requires the
    path to start with ``root + os.sep`` (with a trailing
    separator) so a path like
    ``/tmp/aed_runs/worktrees_evil/x`` is rejected even
    though it shares a textual prefix with
    ``/tmp/aed_runs/worktrees``. Equality with ``root`` is
    explicitly rejected: the policy engine requires a
    per-task isolated worktree under
    ``/tmp/aed_runs/worktrees/<task-name>``, never the
    shared worktree parent itself.
    """
    if not path or not root:
        return False
    if path == root:
        return False
    root_with_sep = root if root.endswith(os.sep) else root + os.sep
    return path.startswith(root_with_sep)


def _in_isolated_workspace(state: AEDRunState) -> bool:
    """True iff the canonicalized isolated workspace path is under ``/tmp/aed_runs/worktrees``.

    Strict variant: the candidate workspace path is canonicalized
    via ``os.path.realpath`` before any comparison, so
    dot-segment escapes such as
    ``/tmp/aed_runs/worktrees/../../home/max/Automated-Edge-Discovery``
    resolve to the actual primary worktree and are rejected.
    Symlink escapes that resolve outside the allowed root are
    also rejected. Non-string / empty / ``None`` paths deny. The
    primary worktree path is rejected even if it is also a
    sub-path of the allowed root.
    """
    raw = state.isolated_workspace_path
    canonical = _canonicalize_path(raw)
    if canonical is None:
        return False
    primary_canonical = _canonicalize_path(state.primary_worktree_path)
    if primary_canonical is not None and canonical == primary_canonical:
        return False
    if not _is_under(canonical, _ISOLATED_WORKSPACE_ROOT):
        return False
    return True


def _is_mergeable(value) -> bool:
    """True iff ``value`` is the exact raw GitHub string ``"MERGEABLE"``.

    The strict AED-RULE-019 variant requires the literal raw
    GitHub value. No whitespace stripping, no case folding, no
    Python ``True`` compatibility, no normalization of any
    kind. ``"mergeable"`` / ``" MERGEABLE "`` / ``"MERGEABLE\n"``
    are all denied. ``None`` / ``False`` / ``"UNKNOWN"`` /
    ``"CONFLICTING"`` / unsupported types are all denied.
    """
    return value == "MERGEABLE"


def _normalize_phrase(s: Optional[str]) -> str:
    """Collapse runs of whitespace to a single space and strip.

    Used to compare the operator-supplied authorization phrase
    against the expected phrase without allowing trivial
    whitespace differences to slip through.
    """
    if not s:
        return ""
    return " ".join(s.split())


def _is_valid_thread_id(value: object) -> bool:
    """True iff ``value`` is a non-empty string usable as a thread ID.

    The strict variant requires a ``str`` instance of length
    >= 1. ``None``, ``int``, ``bool``, ``list``, ``dict``,
    ``tuple``, and any other non-string types are rejected.
    Empty strings are also rejected because a thread reference
    with no characters is not a valid thread identity.
    """
    if not isinstance(value, str):
        return False
    if not value:
        return False
    return True


def _validate_thread_ids(
    values: object, label: str
) -> Optional[AEDDecision]:
    """Validate that ``values`` is a non-empty list of non-empty string thread IDs.

    Returns ``None`` when all values are valid. Returns an
    :class:`AEDDecision` denial citing ``AED-RULE-008`` when
    the values are not a sequence, are empty, or contain any
    entry that is not a non-empty string. The function never
    raises: malformed IDs fail closed with a policy denial
    rather than a Python exception, so the policy engine
    cannot leak a ``TypeError`` from
    ``sorted(...)`` / ``set(...)`` / ``in`` checks downstream.
    """
    if not isinstance(values, (list, tuple)):
        return _deny(
            AEDDecisionCode.REQUIRE_THREAD_LIST_AUTHORIZATION,
            f"Thread resolution denied: {label} must be a list of thread ID strings; got {type(values).__name__}.",
            rule_ids=["AED-RULE-008"],
            required_evidence=[f"{label} (non-empty list of non-empty strings)"],
        )
    if len(values) == 0:
        return _deny(
            AEDDecisionCode.REQUIRE_THREAD_LIST_AUTHORIZATION,
            f"Thread resolution denied: {label} is empty; supply a non-empty list of thread ID strings.",
            rule_ids=["AED-RULE-008"],
            required_evidence=[f"{label} (non-empty list of non-empty strings)"],
        )
    for i, v in enumerate(values):
        if not _is_valid_thread_id(v):
            return _deny(
                AEDDecisionCode.REQUIRE_THREAD_LIST_AUTHORIZATION,
                f"Thread resolution denied: {label}[{i}]={v!r} is not a non-empty string thread ID; the action must supply a list of non-empty string thread IDs.",
                rule_ids=["AED-RULE-008"],
                required_evidence=[f"{label} (all entries are non-empty strings)"],
            )
    return None


def expected_merge_authorization_phrase(state: AEDRunState) -> str:
    """Build the canonical merge authorization phrase.

    The format is the live canonical phrase produced by both
    ``scripts/local/verify_final_head_merge_command.py``
    (``build_authorization_phrase``) and
    ``scripts/local/aed_final_gate.py``
    (``build_authorization_phrase``), with character-for-character
    alignment::

        I confirm merge PR #<pr_number> at <40-hex expected_head_sha>
        using final-head reviewed clean state.

    The phrase is single-spaced, ends with a single period after
    ``clean state``, and the head SHA must be a full 40-character
    hex string. Earlier policy-engine drafts used a different
    ``I authorize guarded squash merge of PR #<n> at exact head <sha>.``
    form, but the live final gate and merge-command verifier
    both produce the form above; aligning the policy engine
    with the live producers is required for the skeleton to be
    consistent with the live canonical phrase.
    """
    return (
        f"I confirm merge PR #{state.pr_number} at {state.expected_head_sha} "
        f"using final-head reviewed clean state."
    )


def has_exact_merge_authorization(state: AEDRunState) -> bool:
    """True iff ``state.explicit_authorization_phrase`` is the live canonical phrase.

    The strict AED-RULE-007 variant compares the operator-supplied
    phrase to the live canonical exact-head phrase
    character-for-character. No whitespace stripping, no
    whitespace collapsing, no newline tolerance, no case
    folding. The expected head SHA must itself be a
    40-character hex string; if it is not, the engine cannot
    construct a valid canonical phrase and so cannot accept
    any authorization text.

    Acceptance rules:
    - phrase must be a non-empty string
    - expected_head_sha must be a full 40-character hex SHA
    - phrase must equal the canonical phrase exactly, with no
      leading/trailing whitespace allowed, no double spaces
      allowed, no embedded newlines allowed
    - the phrase is the LIVE form produced by both
      ``scripts/local/verify_final_head_merge_command.py`` and
      ``scripts/local/aed_final_gate.py``: the old
      ``I authorize guarded squash merge of PR #<n> at exact
      head <sha>.`` policy-only form is no longer accepted.
    """
    if not state.explicit_authorization_phrase:
        return False
    if not _is_full_sha(state.expected_head_sha):
        return False
    expected = expected_merge_authorization_phrase(state)
    return state.explicit_authorization_phrase == expected


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_action(
    action: AEDActionType,
    state: AEDRunState,
    *,
    target_thread_ids: Sequence[object] = (),
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
                f"Merge denied: explicit_authorization_phrase does not match the live canonical merge phrase. Expected: I confirm merge PR #{state.pr_number} at {state.expected_head_sha} using final-head reviewed clean state.",
                rule_ids=["AED-RULE-007", "AED-RULE-005"],
                required_evidence=[
                    f"explicit_authorization_phrase == I confirm merge PR #{state.pr_number} at {state.expected_head_sha} using final-head reviewed clean state."
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
        # AED-RULE-011: the harness's derived current-head indicator
        # must also be true. This is the strict variant: even if
        # ``codex_clean_pass_head_sha`` happens to match
        # ``expected_head_sha`` by coincidence, the harness must
        # have affirmatively marked the pass as current-head.
        if not state.codex_clean_pass_for_current_head:
            return _deny(
                AEDDecisionCode.HOLD,
                f"Merge denied: codex_clean_pass_for_current_head is False; the clean-pass evidence is not affirmatively tied to the current expected head (clean-pass head = {state.codex_clean_pass_head_sha!r}, expected head = {state.expected_head_sha!r}).",
                rule_ids=["AED-RULE-005", "AED-RULE-011"],
                required_evidence=["codex_clean_pass_for_current_head=true"],
            )
        # AED-RULE-005 / -011: the raw clean-pass head SHA must also
        # be a full 40-character hex SHA equal to the current
        # expected head. A None / abbreviated / non-hex value, or a
        # value pointing at a different head, must deny.
        if not _is_full_sha(state.codex_clean_pass_head_sha):
            return _deny(
                AEDDecisionCode.HOLD,
                f"Merge denied: Codex clean-pass head SHA {state.codex_clean_pass_head_sha!r} is not a full 40-character hex SHA; a stale clean pass from a prior head does not satisfy AED-RULE-011.",
                rule_ids=["AED-RULE-005", "AED-RULE-011"],
                required_evidence=[
                    "codex_clean_pass_head_sha is a 40-character hex SHA equal to expected_head_sha"
                ],
            )
        if state.codex_clean_pass_head_sha != state.expected_head_sha:
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

    # AED-RULE-008: thread resolution requires (a) a live-head check
    # using the full 40-character SHA, (b) an explicit non-empty
    # target thread set carried by the action, (c) every target
    # thread ID to be a non-empty string, (d) every authorized
    # thread ID to be a non-empty string, and (e) the target set
    # to be a subset of the authorized thread list. AED-RULE-005
    # applies to thread resolution as well as merge. The action
    # must name the threads it intends to resolve so the policy
    # engine can verify the target set is a subset of the
    # authorization. All malformed IDs fail closed with a
    # policy denial citing AED-RULE-008 — the policy engine
    # never raises TypeError from set/sorted membership checks.
    if action == AEDActionType.GITHUB_THREAD_RESOLVE:
        # AED-RULE-005: live head must match expected head before resolving threads.
        if not _head_matches(state):
            return _deny(
                AEDDecisionCode.REQUIRE_EXACT_HEAD_AUTHORIZATION,
                "Thread resolution denied: current head SHA does not match expected head SHA; re-verify the live PR head before resolving threads (AED-RULE-005 applies to thread resolution as well as merge).",
                rule_ids=["AED-RULE-005"],
                required_evidence=["current_head_sha==expected_head_sha (full 40-character hex)"],
            )
        # AED-RULE-008: target thread IDs must be supplied by the
        # action, every entry must be a non-empty string, and
        # the list must be non-empty. A non-empty
        # ``authorized_thread_ids`` is not by itself authorization
        # to resolve a thread: the action must name the threads
        # it intends to resolve so the policy engine can verify
        # the target set is a subset of the authorization. All
        # validation runs through ``_validate_thread_ids`` so a
        # malformed payload (None / int / list / dict / empty
        # string) fails closed with a denial rather than
        # raising a TypeError.
        target_validation = _validate_thread_ids(
            target_thread_ids, "target_thread_ids"
        )
        if target_validation is not None:
            return target_validation
        target_set = set(target_thread_ids)
        authorized_validation = _validate_thread_ids(
            state.authorized_thread_ids, "authorized_thread_ids"
        )
        if authorized_validation is not None:
            return authorized_validation
        # All IDs are validated non-empty strings at this point;
        # building sets, sorting, and ``in`` checks are safe.
        authorized_set = set(state.authorized_thread_ids)
        unauthorized_targets = sorted(
            t for t in target_set if t not in authorized_set
        )
        if unauthorized_targets:
            return _deny(
                AEDDecisionCode.REQUIRE_THREAD_LIST_AUTHORIZATION,
                f"Thread resolution denied: target thread ID(s) {unauthorized_targets!r} are not in the authorized set {sorted(authorized_set)!r}; the action may only resolve threads explicitly named in the authorization.",
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
