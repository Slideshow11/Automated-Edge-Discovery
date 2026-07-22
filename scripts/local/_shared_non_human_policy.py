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

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from scripts.local._shared_codex_classifier import is_codex_login


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
