#!/usr/bin/env python3
"""Autocoder repair-cycle batch planner (PHASE 5).

Invoked by the autonomous repair cycle to group Codex findings
into cohesive repair batches. Emits a machine-readable plan
that the runner consumes.

Usage::

  python3 scripts/local/aed_repair_planner.py \
      --findings-file findings.json \
      --output-plan plan.json

``findings.json`` is a JSON list of finding records::

  [
    {"finding_id": "...", "severity": "P2",
     "subsystem": "...", "root_cause": "...",
     "path": "scripts/local/..."},
    ...
  ]

``plan.json`` is a machine-readable batch plan::

  {
    "tier": "tier_2_cohesive_batch",
    "batches": [
      {
        "batch_id": "...",
        "finding_ids": [...],
        "severities": [...],
        "root_cause": "...",
        "subsystem": "...",
        "grouping_reason": "...",
        "smaller_than_default_reason": "...",
        "focused_tests": [...],
        "requires_full_validation": bool,
      },
      ...
    ],
    "selection_reason": "...",
    "changed_paths": [...],
  }
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# Round-412 (PHASE 6): script-local import path setup.
# When this CLI is run as ``python3 scripts/local/aed_repair_planner.py``
# Python puts ``scripts/local`` rather than the repository root
# on ``sys.path``. Add the repo root so the ``scripts.local``
# package is importable without setting PYTHONPATH externally.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.local._shared_batching import (
    FindingRecord,
    Severity,
    RepairBatch,
    batch_findings,
)
from scripts.local._production_facade import (
    select_tests_with_invocation,
)
from scripts.local._shared_test_selection import (
    ValidationTier,
    classify_paths,
)


def _parse_severity(s: str) -> Severity:
    if not isinstance(s, str):
        raise ValueError(f"severity must be a string, got {type(s)}")
    s_up = s.upper().strip()
    if s_up not in ("P0", "P1", "P2", "P3"):
        raise ValueError(
            f"unknown severity {s!r}; expected P0/P1/P2/P3"
        )
    return Severity(s_up)


def parse_findings(findings: List[Dict[str, Any]]) -> List[FindingRecord]:
    out: List[FindingRecord] = []
    for f in findings:
        if not isinstance(f, dict):
            raise ValueError(f"finding must be a dict, got {type(f)}")
        out.append(FindingRecord(
            finding_id=str(f.get("finding_id") or f.get("id") or ""),
            severity=_parse_severity(f.get("severity") or "P2"),
            subsystem=str(f.get("subsystem") or ""),
            root_cause=str(f.get("root_cause") or ""),
            path=str(f.get("path") or ""),
            summary=str(f.get("summary") or ""),
        ))
    return out


def batches_to_dict(batches: List[RepairBatch]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for b in batches:
        out.append({
            "batch_id": b.batch_id,
            "finding_ids": list(b.finding_ids),
            "severities": [s.value for s in b.severities],
            "root_cause": b.root_cause,
            "subsystem": b.subsystem,
            "grouping_reason": b.grouping_reason,
            "smaller_than_default_reason": b.smaller_than_default_reason,
            "focused_tests": list(b.focused_tests),
            "requires_full_validation": bool(b.requires_full_validation),
        })
    return out


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Cohesive repair-batch planner (PHASE 5)."
    )
    p.add_argument(
        "--findings-file",
        required=True,
        help="JSON file with a list of finding records.",
    )
    p.add_argument(
        "--output-plan",
        required=True,
        help="Destination JSON file for the batch plan.",
    )
    p.add_argument(
        "--tier",
        choices=["tier_1_inner_repair", "tier_2_cohesive_batch",
                 "tier_3_final_candidate"],
        default="tier_2_cohesive_batch",
        help="Validation tier (default: tier_2_cohesive_batch).",
    )
    p.add_argument(
        "--changed-paths-file",
        default=None,
        help="Optional file with one changed path per line. "
             "Used to build the focused-tests plan.",
    )
    p.add_argument(
        "--final-candidate",
        action="store_true",
        help="Mark this plan as a final-candidate run (full "
             "validation required).",
    )
    args = p.parse_args(argv)

    with open(args.findings_file) as f:
        findings_raw = json.load(f)
    if not isinstance(findings_raw, list):
        print(
            f"ERROR: {args.findings_file} must contain a JSON list",
            file=sys.stderr,
        )
        return 2
    findings = parse_findings(findings_raw)

    changed_paths: List[str] = []
    if args.changed_paths_file:
        with open(args.changed_paths_file) as f:
            changed_paths = [
                line.strip() for line in f if line.strip()
            ]

    batches = batch_findings(findings)

    # Build a focused-test plan from the changed paths.
    tier = ValidationTier(args.tier)
    test_plan = select_tests_with_invocation(
        changed_paths=changed_paths,
        tier=tier,
        final_candidate=bool(args.final_candidate),
    )

    plan = {
        "tier": tier.value,
        "batches": batches_to_dict(batches),
        "selection_reason": test_plan.selection_reason,
        "changed_paths": changed_paths,
        "test_plan": test_plan.to_machine_readable(),
        "finding_count": len(findings),
        "batch_count": len(batches),
    }
    with open(args.output_plan, "w") as f:
        json.dump(plan, f, indent=2)
    print(
        f"[aed_repair_planner] findings={len(findings)} "
        f"batches={len(batches)} plan={args.output_plan}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
