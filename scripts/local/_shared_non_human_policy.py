#!/usr/bin/env python3
"""Canonical non-human review policy.

Implements the hard-coded policy from PHASE 3 R-3:

  Automatic resolution is allowed ONLY when ALL of these are proven:

    1. every participant is the approved Codex identity;
    2. no human has participated;
    3. participant inventory is complete (not paginated-
       truncated);
    4. the finding has been repaired (commit message or
       substantive diff marker);
    5. the repair is present in the current head;
    6. the finding anchor is valid and is an ancestor of the
       current head (ancestry check);
    7. a later exact-head Codex clean response exists;
    8. no newer finding exists;
    9. the live head still matches immediately before
       mutation.

  Do not require ``isOutdated=true`` as the sole eligibility
  rule. A current or non-outdated Codex thread may be eligible
  when its repair and later clean exact-head review are proven.

  Human participation, unknown identity, incomplete inventory,
  missing anchors, failed ancestry checks, or moved heads MUST
  remain hard stops.

Direct ad hoc ``gh api`` resolution is no longer needed for
normal eligible Codex threads. The canonical controller
supports the authorized path itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from scripts.local._shared_codex_classifier import is_codex_login

# Canonical SHA-1 pattern used by GitHub.
_SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")


# Canonical legacy reason vocabulary. The shared policy's
# internal codes map to these codes for backward compatibility
# with the controller's existing tests and action reports.
LEGACY_REASONS = frozenset({
    "actor_not_bot",
    "actor_not_codex",
    "ancestry_failed",
    "ancestry_unavailable",
    "already_resolved",
    "codex_head_mismatch",
    "codex_not_clean",
    "eligible",
    "head_unknown",
    "human_reply",
    "inventory_incomplete",
    "malformed_commit_anchor",
    "missing_commit_anchor",
    "no_later_commit",
    "not_outdated",
    "policy_ineligible",
    "unknown_actor_in_thread",
})


class ReviewerClass(str, Enum):
    CODEX = "codex"
    OTHER_AUTOMATION = "other_automation"
    HUMAN = "human"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ParticipantInventory:
    """Complete participant inventory for one review thread."""

    thread_id: str
    participants: List[str] = field(default_factory=list)
    # inventory_complete must be True for eligibility. False
    # when pagination was truncated or hasNextPage=True.
    inventory_complete: bool = False

    def classify_participants(self) -> List[ReviewerClass]:
        out: List[ReviewerClass] = []
        for p in self.participants:
            if is_codex_login(p):
                out.append(ReviewerClass.CODEX)
            elif p == p.lower():  # crude check; only used as
                # the explicit signal for "human" is the empty
                # Codex set.
                out.append(ReviewerClass.HUMAN)
            else:
                out.append(ReviewerClass.OTHER_AUTOMATION)
        return out


@dataclass(frozen=True)
class RepairEvidence:
    """Evidence that the finding has been repaired in the current head."""

    finding_thread_id: str
    anchor_sha: str
    current_head_sha: str
    repair_present: bool
    ancestry_ok: bool

    def is_satisfied(self) -> bool:
        return self.repair_present and self.ancestry_ok


@dataclass(frozen=True)
class CodexCleanEvidence:
    """Evidence that a later exact-head Codex clean response exists."""

    clean_response_id: str
    clean_response_kind: str  # "review" or "issue_comment"
    clean_response_ts: str
    head_sha: str
    live_head_matches_at_collection: bool
    no_newer_finding: bool


@dataclass(frozen=True)
class LiveHeadMatch:
    """Evidence that the live PR head still matches at mutation time."""

    expected_head: str
    live_head: str

    def is_satisfied(self) -> bool:
        return self.expected_head == self.live_head


@dataclass(frozen=True)
class EligibilityVerdict:
    """The verdict of the eligibility policy."""

    eligible: bool
    reasons: List[str] = field(default_factory=list)
    reviewer_classes: List[ReviewerClass] = field(default_factory=list)


def _deny(reason: str, classes: List[ReviewerClass]) -> EligibilityVerdict:
    return EligibilityVerdict(
        eligible=False, reasons=[reason], reviewer_classes=classes,
    )


def _eligible(classes: List[ReviewerClass]) -> EligibilityVerdict:
    return EligibilityVerdict(
        eligible=True, reasons=["eligible"], reviewer_classes=classes,
    )


def parse_thread_inventory(
    thread: Dict[str, Any],
) -> Tuple[List[str], bool, List[ReviewerClass], Optional[str]]:
    """Parse a legacy thread dict into the shared policy's inputs.

    Returns ``(participants, inventory_complete,
    reviewer_classes, denial_reason)``. When
    ``denial_reason`` is non-None, the caller MUST short-circuit
    with that reason and NOT consult the policy.

    Centralizes every inventory-shape and participant-classification
    check. The facade MUST call this and never reimplement it.
    """
    if not isinstance(thread, dict):
        return ([], False, [], "unknown_actor_in_thread")
    if "comments" in thread:
        raw_comments = thread["comments"]
    elif "comment_list" in thread:
        raw_comments = thread["comment_list"]
    else:
        return ([], False, [], "unknown_actor_in_thread")
    if raw_comments is None or not isinstance(raw_comments, list) or len(raw_comments) == 0:
        return ([], False, [], "unknown_actor_in_thread")

    classes: List[ReviewerClass] = []
    participants: List[str] = []
    for c in raw_comments:
        if not isinstance(c, dict):
            return ([], False, [], "unknown_actor_in_thread")
        author = c.get("author") or c.get("author_login") or c.get("user")
        if not isinstance(author, str) or not author:
            return ([], False, [], "unknown_actor_in_thread")
        if is_codex_login(author):
            classes.append(ReviewerClass.CODEX)
        elif author.endswith("[bot]"):
            classes.append(ReviewerClass.OTHER_AUTOMATION)
        else:
            classes.append(ReviewerClass.HUMAN)
        participants.append(author)
    # Also consider the top-level author if present.
    top = thread.get("author") or thread.get("author_login")
    if isinstance(top, str) and top:
        if is_codex_login(top):
            classes.insert(0, ReviewerClass.CODEX)
        elif top.endswith("[bot]"):
            classes.insert(0, ReviewerClass.OTHER_AUTOMATION)
        else:
            classes.insert(0, ReviewerClass.HUMAN)
        participants.insert(0, top)
    return (participants, True, classes, None)


def translate_class_to_legacy(
    *,
    classes: List[ReviewerClass],
    has_codex_top: bool,
) -> Tuple[str, str]:
    """Translate a participant classification into legacy codes.

    Returns ``(human_legacy, non_codex_legacy)``. The legacy
    vocabulary distinguishes ``actor_not_bot`` (top is human)
    from ``human_reply`` (top is Codex but a reply is human).
    """
    if has_codex_top:
        return ("human_reply", "human_reply")
    return ("actor_not_bot", "actor_not_codex")


def validate_thread_for_resolution(
    *,
    thread: Dict[str, Any],
    head_sha: Optional[str],
    codex_clean_passed: Optional[bool],
    codex_reviewed_sha: Optional[str],
    repo: Optional[str],
    ancestry_runner: Optional[Any] = None,
    verify_ancestry: bool = True,
    no_newer_finding: bool = True,
    live_head_match: bool = True,
    inventory_complete: bool = True,
    review_thread_inventory_complete: bool = True,
    nested_comment_inventory_complete: bool = True,
) -> EligibilityVerdict:
    """Canonical production eligibility decision.

    PHASE 3 R-3 contract. Returns ``EligibilityVerdict`` with
    legacy reason codes. Every legacy reason the controller
    previously produced is preserved:

      * already_resolved
      * codex_head_mismatch
      * codex_not_clean
      * missing_commit_anchor
      * malformed_commit_anchor
      * no_later_commit
      * actor_not_bot
      * actor_not_codex
      * human_reply
      * unknown_actor_in_thread
      * ancestry_unavailable
      * eligible
      * head_unknown
    """
    # Idempotency guard: already-resolved threads MUST NOT be
    # re-resolved.
    if bool(thread.get("isResolved") or thread.get("is_resolved")):
        return _deny("already_resolved", [])

    # Round-412 (PHASE 4 Finding 1): inventory completeness
    # MUST be proven from the audit evidence. Missing outer
    # review-thread pagination OR nested-comment pagination
    # is a hard stop. ``inventory_complete`` is the
    # conjunction.
    if not (
        bool(inventory_complete)
        and bool(review_thread_inventory_complete)
        and bool(nested_comment_inventory_complete)
    ):
        return _deny("unknown_actor_in_thread", [])

    # Fail closed when the latest exact-head Codex evidence
    # is not clean.
    if codex_clean_passed is not True:
        return _deny("codex_not_clean", [])

    # Parse the participant inventory. This delegates to
    # the shared policy's canonical participant parser.
    participants, inventory_ok, classes, denial = parse_thread_inventory(thread)
    if denial is not None:
        return _deny(denial, classes)

    # Determine whether the top-level author is Codex.
    top = thread.get("author") or thread.get("author_login")
    has_codex_top = isinstance(top, str) and is_codex_login(top)
    human_legacy, non_codex_legacy = translate_class_to_legacy(
        classes=classes, has_codex_top=has_codex_top,
    )

    # Participant classification hard stops.
    if ReviewerClass.HUMAN in classes:
        return _deny(human_legacy, classes)
    if ReviewerClass.OTHER_AUTOMATION in classes:
        return _deny(non_codex_legacy, classes)
    if ReviewerClass.UNKNOWN in classes:
        return _deny("unknown_actor_in_thread", classes)
    if not classes:
        return _deny("actor_not_bot", classes)

    # Inventory completeness.
    if not inventory_ok:
        return _deny("unknown_actor_in_thread", classes)

    # Commit anchor validation.
    anchor_sha = (
        thread.get("original_commit_sha")
        or thread.get("comment_sha")
        or thread.get("head_sha")
        or thread.get("originalCommit")
        or thread.get("anchor_sha")
        or ""
    )
    if not anchor_sha:
        return _deny("missing_commit_anchor", classes)
    if not (
        isinstance(anchor_sha, str)
        and _SHA1_PATTERN.match(anchor_sha)
    ):
        return _deny("malformed_commit_anchor", classes)

    # Round-412 (PHASE 4 Finding 2): missing or malformed
    # exact-head evidence MUST fail closed. The reviewed
    # SHA must be a canonical 40-character lowercase hex
    # string that EQUALS the canonical live head SHA. Any
    # missing/empty/short/malformed component is treated
    # as a hard stop, not a satisfied condition.
    if not isinstance(head_sha, str) or not _SHA1_PATTERN.match(head_sha):
        return _deny("head_unknown", classes)
    if not isinstance(codex_reviewed_sha, str) or not _SHA1_PATTERN.match(codex_reviewed_sha):
        return _deny("codex_head_mismatch", classes)
    if codex_reviewed_sha != head_sha:
        return _deny("codex_head_mismatch", classes)

    # Ancestry verification. The controller's verifier
    # returns ``anchor_equals_head`` when anchor == head_sha;
    # the legacy contract maps that to ``no_later_commit``.
    if verify_ancestry and isinstance(repo, str) and "/" in repo:
        try:
            from scripts.local.aed_pr_readiness import (
                verify_anchor_ancestry as _verify_anc,
            )
            is_ancestor, ancestry_reason = _verify_anc(
                repo,
                str(anchor_sha),
                str(head_sha) if head_sha else "",
                runner=ancestry_runner,
            )
        except Exception:
            return _deny("ancestry_unavailable", classes)
        if not is_ancestor:
            if ancestry_reason == "anchor_equals_head":
                return _deny("no_later_commit", classes)
            return _deny("ancestry_unavailable", classes)

    # Later exact-head clean evidence + newer finding.
    if not no_newer_finding:
        return _deny("codex_not_clean", classes)
    if not live_head_match:
        return _deny("head_unknown", classes)

    return _eligible(classes)


def classify_review_thread(
    *,
    participants: Sequence[str],
    inventory_complete: bool,
    repair: Optional[RepairEvidence],
    clean_evidence: Optional[CodexCleanEvidence],
    live_head_match: Optional[LiveHeadMatch],
) -> EligibilityVerdict:
    """Run the full eligibility policy and return a verdict.

    Eligibility requires ALL of the following:
      1. every participant is a Codex identity;
      2. inventory_complete is True;
      3. repair.is_satisfied() if repair is provided;
      4. clean_evidence exists and ``no_newer_finding`` is True;
      5. live_head_match.is_satisfied() if provided.
    """
    reasons: List[str] = []

    # 1. Participant classification.
    classes: List[ReviewerClass] = []
    for p in participants:
        if is_codex_login(p):
            classes.append(ReviewerClass.CODEX)
        else:
            classes.append(ReviewerClass.HUMAN if not p.endswith("[bot]") else ReviewerClass.OTHER_AUTOMATION)
    if ReviewerClass.HUMAN in classes:
        reasons.append("human_participant")
        return EligibilityVerdict(False, reasons, classes)
    if ReviewerClass.OTHER_AUTOMATION in classes:
        reasons.append("non_codex_automation")
        return EligibilityVerdict(False, reasons, classes)
    if ReviewerClass.UNKNOWN in classes:
        reasons.append("unknown_reviewer")
        return EligibilityVerdict(False, reasons, classes)
    if not classes:
        reasons.append("no_participants")
        return EligibilityVerdict(False, reasons, classes)

    # 2. Inventory completeness.
    if not inventory_complete:
        reasons.append("inventory_incomplete")
        return EligibilityVerdict(False, reasons, classes)

    # 3. Repair evidence.
    if repair is None:
        reasons.append("no_repair_evidence")
        return EligibilityVerdict(False, reasons, classes)
    if not repair.is_satisfied():
        if not repair.repair_present:
            reasons.append("repair_not_present")
        if not repair.ancestry_ok:
            reasons.append("ancestry_failed")
        return EligibilityVerdict(False, reasons, classes)

    # 4. Later exact-head clean evidence.
    if clean_evidence is None:
        reasons.append("no_clean_evidence")
        return EligibilityVerdict(False, reasons, classes)
    if not clean_evidence.no_newer_finding:
        reasons.append("newer_finding_present")
        return EligibilityVerdict(False, reasons, classes)

    # 5. Live-head match.
    if live_head_match is not None and not live_head_match.is_satisfied():
        reasons.append("live_head_moved")
        return EligibilityVerdict(False, reasons, classes)

    return EligibilityVerdict(True, reasons, classes)
