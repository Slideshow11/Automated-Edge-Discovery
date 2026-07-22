#!/usr/bin/env python3
"""Cohesive repair batching policy.

Implements the hard-coded policy from PHASE 3 R-4.

Default behavior:

  * group 3 to 6 related findings sharing a root cause or
    subsystem;
  * permit up to 8 when they are tightly coupled and one
    design change addresses them;
  * use 1 to 2 only for P1 defects, protected-boundary
    defects, or changes where isolation materially improves
    safety;
  * close a batch when the next finding crosses into a
    different subsystem, creates an unsafe review surface, or
    makes the patch difficult to reason about;
  * never split findings into one-finding cycles merely
    because they arrived as separate comments;
  * never combine unrelated findings solely to reduce review
    rounds.

Each batch record MUST include:

  * finding IDs;
  * severities;
  * shared root cause;
  * affected subsystem;
  * reason for grouping;
  * reason for any smaller-than-default batch;
  * focused tests selected;
  * whether full validation is required.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


# Default batch size bounds.
DEFAULT_MIN = 3
DEFAULT_MAX = 6
MAX_TIGHTLY_COUPLED = 8
ISOLATION_MIN = 1
ISOLATION_MAX = 2

# Severities that mandate isolation (1-2 per batch).
ISOLATION_SEVERITIES = frozenset({Severity.P0, Severity.P1})


@dataclass(frozen=True)
class FindingRecord:
    """One Codex finding to be batched."""

    finding_id: str
    severity: Severity
    subsystem: str
    root_cause: str
    path: str = ""
    summary: str = ""


@dataclass(frozen=True)
class RepairBatch:
    """One cohesive repair batch."""

    batch_id: str
    finding_ids: List[str]
    severities: List[Severity]
    root_cause: str
    subsystem: str
    grouping_reason: str
    smaller_than_default_reason: str
    focused_tests: List[str]
    requires_full_validation: bool


def _subsystem_for(finding: FindingRecord) -> str:
    """Return a stable subsystem bucket.

    The bucket is the file basename without the ``.py`` suffix
    when the finding carries a path, falling back to the
    caller-supplied ``subsystem`` field.

    This makes closely-coupled findings (same file) group
    together while separating findings in different files.
    """
    if finding.path:
        basename = finding.path.rsplit("/", 1)[-1]
        if basename.endswith(".py"):
            basename = basename[:-3]
        if basename:
            return basename
    return finding.subsystem or "unknown"


def batch_findings(findings: List[FindingRecord]) -> List[RepairBatch]:
    """Group findings into cohesive repair batches.

    The algorithm:
      1. Sort by (subsystem, root_cause, severity).
      2. Greedily pack adjacent findings that share
         (subsystem, root_cause) into batches of 3-6.
      3. Allow up to 8 when root_cause is identical AND the
         subsystem is identical.
      4. Split P0/P1 into 1-2 finding isolation batches.
      5. Close a batch when subsystem or root_cause changes.
    """
    if not findings:
        return []

    # Sort.
    sorted_findings = sorted(
        findings,
        key=lambda f: (_subsystem_for(f), f.root_cause, f.severity.value),
    )

    batches: List[RepairBatch] = []
    current: List[FindingRecord] = []

    def _flush(reason: str = "") -> None:
        nonlocal current
        if not current:
            return
        subsystem = _subsystem_for(current[0])
        root_cause = current[0].root_cause
        severities = [f.severity for f in current]
        is_isolation = any(s in ISOLATION_SEVERITIES for s in severities)
        requires_full = is_isolation
        smaller_reason = ""
        if len(current) < DEFAULT_MIN:
            smaller_reason = (
                f"only {len(current)} related findings in this "
                f"subsystem/root-cause cluster; further splits "
                f"would not isolate by shared root cause."
            )
        # Focused tests: union of paths → nearest unit tests.
        paths = sorted({f.path for f in current if f.path})
        focused_tests = [p.replace("/", ".").replace(".py", "") for p in paths if p.endswith(".py")]
        if requires_full:
            focused_tests = ["FULL_REPOSITORY_SUITE"]
        batch_id = hashlib.sha1(
            f"{subsystem}|{root_cause}|{','.join(sorted(f.finding_id for f in current))}".encode()
        ).hexdigest()[:12]
        batches.append(
            RepairBatch(
                batch_id=batch_id,
                finding_ids=[f.finding_id for f in current],
                severities=severities,
                root_cause=root_cause,
                subsystem=subsystem,
                grouping_reason=reason or "shared subsystem and root cause",
                smaller_than_default_reason=smaller_reason,
                focused_tests=focused_tests,
                requires_full_validation=requires_full,
            )
        )
        current = []

    for f in sorted_findings:
        if current:
            same_subsystem = _subsystem_for(f) == _subsystem_for(current[0])
            same_root_cause = f.root_cause == current[0].root_cause
            is_isolation_in_progress = any(
                s in ISOLATION_SEVERITIES for s in (x.severity for x in current)
            )
            new_is_isolation = f.severity in ISOLATION_SEVERITIES
            max_size = MAX_TIGHTLY_COUPLED if same_subsystem and same_root_cause else DEFAULT_MAX
            if is_isolation_in_progress or new_is_isolation:
                max_size = ISOLATION_MAX
            if not (same_subsystem and same_root_cause):
                _flush(reason="subsystem or root cause changed")
            elif len(current) >= max_size:
                _flush(reason=f"reached max batch size {max_size}")
        current.append(f)
    _flush()
    return batches
