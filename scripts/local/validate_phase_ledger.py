#!/usr/bin/env python3
"""
validate_phase_ledger.py — validator for the phase execution ledger.

Reads a phase_ledger.jsonl and a list of claimed phase IDs. Reports:

  valid: bool
  hold_state: HOLD_VALID | HOLD_UNEVIDENCED_PASS | HOLD_PHASE_EVIDENCE_CORRUPTED
              | HOLD_PHASE_RESULT_INCONSISTENT
  errors: list of {phase_id, line, kind, detail}
  warnings: list of strings
  exit_code: int
  ledger_path: Path
  line_count: int
  claimed_count: int

Hold state precedence (most specific wins):
  1. HOLD_PHASE_EVIDENCE_CORRUPTED  — a ledger line points to a missing
     artifact, or a canonical-writer line is missing required fields.
  2. HOLD_PHASE_RESULT_INCONSISTENT — exit_code != 0 with status PASS, or
     status PASS with empty observed_summary.
  3. HOLD_UNEVIDENCED_PASS          — claimed phases have no canonical
     ledger line, or the ledger is missing/empty, or only writer=agent
     evidence exists.
  4. HOLD_VALID                     — no problems found.

Usage:
  python3 scripts/local/validate_phase_ledger.py \\
      --ledger /path/phase_ledger.jsonl \\
      [--claimed-phases P1,P2,P3] \\
      [--output-json /path/report.json] \\
      [--allow-legacy]

Exit codes:
  0  — valid
  2  — HOLD_UNEVIDENCED_PASS
  3  — HOLD_PHASE_EVIDENCE_CORRUPTED
  4  — HOLD_PHASE_RESULT_INCONSISTENT
  1  — fatal error (bad args, IO error, etc.)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from phase_ledger import (
    CANONICAL_WRITERS,
    read_entries,
    is_canonical_evidence,
)


# -----------------------------------------------------------------------------
# Hold-state constants
# -----------------------------------------------------------------------------

HOLD_VALID = "HOLD_VALID"
HOLD_UNEVIDENCED_PASS = "HOLD_UNEVIDENCED_PASS"
HOLD_PHASE_EVIDENCE_CORRUPTED = "HOLD_PHASE_EVIDENCE_CORRUPTED"
HOLD_PHASE_RESULT_INCONSISTENT = "HOLD_PHASE_RESULT_INCONSISTENT"

EXIT_VALID = 0
EXIT_FATAL = 1
EXIT_UNEVIDENCED = 2
EXIT_EVIDENCE_CORRUPTED = 3
EXIT_RESULT_INCONSISTENT = 4

EXIT_CODE_BY_HOLD = {
    HOLD_VALID: EXIT_VALID,
    HOLD_UNEVIDENCED_PASS: EXIT_UNEVIDENCED,
    HOLD_PHASE_EVIDENCE_CORRUPTED: EXIT_EVIDENCE_CORRUPTED,
    HOLD_PHASE_RESULT_INCONSISTENT: EXIT_RESULT_INCONSISTENT,
}


# -----------------------------------------------------------------------------
# Per-line internal-consistency checks (always run, even with no claim)
# -----------------------------------------------------------------------------


def _check_line_consistency(
    line_no: int,
    entry: dict[str, Any],
) -> list[dict[str, str]]:
    """
    Check a single ledger line for internal consistency.
    Returns a list of error dicts {phase_id, line, kind, detail}.

    Checks:
      - status=PASS with exit_code != 0 → RESULT_INCONSISTENT
      - status=PASS with empty observed_summary → RESULT_INCONSISTENT
      - canonical writer with no argv → EVIDENCE_CORRUPTED
      - canonical writer with relative/missing stdout_path → EVIDENCE_CORRUPTED
      - canonical writer with relative/missing stderr_path → EVIDENCE_CORRUPTED
      - canonical writer with stdout_path pointing to a missing file
        → EVIDENCE_CORRUPTED
    """
    errors: list[dict[str, str]] = []
    phase_id = str(entry.get("phase_id", f"<line_{line_no}>"))
    writer = entry.get("writer")
    status = entry.get("status")
    exit_code = entry.get("exit_code")
    argv = entry.get("argv")
    stdout_path = entry.get("stdout_path")
    stderr_path = entry.get("stderr_path")
    observed_summary = entry.get("observed_summary", "")

    # Inconsistency checks (status=PASS claims)
    if status == "PASS":
        if exit_code != 0:
            errors.append({
                "phase_id": phase_id,
                "line": line_no,
                "kind": "RESULT_INCONSISTENT",
                "detail": (
                    f"status=PASS but exit_code={exit_code} for phase "
                    f"{phase_id!r} on line {line_no}"
                ),
            })
        if not isinstance(observed_summary, str) or observed_summary == "":
            errors.append({
                "phase_id": phase_id,
                "line": line_no,
                "kind": "RESULT_INCONSISTENT",
                "detail": (
                    f"status=PASS with empty observed_summary for phase "
                    f"{phase_id!r} on line {line_no}"
                ),
            })

    # Canonical-writer evidence corruption
    if writer in CANONICAL_WRITERS:
        if not isinstance(argv, list) or len(argv) == 0:
            errors.append({
                "phase_id": phase_id,
                "line": line_no,
                "kind": "EVIDENCE_CORRUPTED",
                "detail": (
                    f"writer={writer!r} for phase {phase_id!r} on line "
                    f"{line_no} has empty argv"
                ),
            })
        if not (isinstance(stdout_path, str) and os.path.isabs(stdout_path)):
            errors.append({
                "phase_id": phase_id,
                "line": line_no,
                "kind": "EVIDENCE_CORRUPTED",
                "detail": (
                    f"writer={writer!r} for phase {phase_id!r} on line "
                    f"{line_no} is missing absolute stdout_path "
                    f"(got {stdout_path!r})"
                ),
            })
        if not (isinstance(stderr_path, str) and os.path.isabs(stderr_path)):
            errors.append({
                "phase_id": phase_id,
                "line": line_no,
                "kind": "EVIDENCE_CORRUPTED",
                "detail": (
                    f"writer={writer!r} for phase {phase_id!r} on line "
                    f"{line_no} is missing absolute stderr_path "
                    f"(got {stderr_path!r})"
                ),
            })
        # File existence check
        if (
            isinstance(stdout_path, str)
            and os.path.isabs(stdout_path)
            and not os.path.exists(stdout_path)
        ):
            errors.append({
                "phase_id": phase_id,
                "line": line_no,
                "kind": "EVIDENCE_CORRUPTED",
                "detail": (
                    f"stdout artifact for phase {phase_id!r} on line "
                    f"{line_no} does not exist on disk: {stdout_path}"
                ),
            })
        if (
            isinstance(stderr_path, str)
            and os.path.isabs(stderr_path)
            and not os.path.exists(stderr_path)
        ):
            errors.append({
                "phase_id": phase_id,
                "line": line_no,
                "kind": "EVIDENCE_CORRUPTED",
                "detail": (
                    f"stderr artifact for phase {phase_id!r} on line "
                    f"{line_no} does not exist on disk: {stderr_path}"
                ),
            })

    return errors


# -----------------------------------------------------------------------------
# Per-claim evidence check
# -----------------------------------------------------------------------------


def _check_claim_evidence(
    claimed: list[str],
    entries: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """
    For each claimed phase_id, check whether at least one canonical
    (script|phase_exec, PASS) ledger line exists.

    Returns a list of error dicts.
    """
    errors: list[dict[str, str]] = []
    # Build a map: phase_id → list of (line_no, entry)
    by_phase: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for idx, e in enumerate(entries, start=1):
        pid = e.get("phase_id")
        if isinstance(pid, str):
            by_phase.setdefault(pid, []).append((idx, e))

    for claim in claimed:
        candidates = by_phase.get(claim, [])
        if not candidates:
            errors.append({
                "phase_id": claim,
                "line": 0,
                "kind": "UNCLAIMED_PHASE",
                "detail": (
                    f"claimed phase {claim!r} has no ledger entry"
                ),
            })
            continue
        # At least one canonical PASS entry required
        if not any(is_canonical_evidence(e) for _, e in candidates):
            # Find a representative line to describe the failure
            rep_line, rep_entry = candidates[0]
            writer = rep_entry.get("writer")
            errors.append({
                "phase_id": claim,
                "line": rep_line,
                "kind": "UNCLAIMED_PHASE",
                "detail": (
                    f"claimed phase {claim!r} has {len(candidates)} ledger "
                    f"line(s) but none are canonical PASS evidence "
                    f"(writer={writer!r}); writer=agent never satisfies "
                    f"proof for claimed PASS"
                ),
            })
    return errors


# -----------------------------------------------------------------------------
# Duplicate detection
# -----------------------------------------------------------------------------


def _find_duplicate_phase_ids(
    entries: list[dict[str, Any]],
) -> list[str]:
    """Return a sorted list of phase_ids that appear more than once."""
    counts: dict[str, int] = {}
    for e in entries:
        pid = e.get("phase_id")
        if isinstance(pid, str):
            counts[pid] = counts.get(pid, 0) + 1
    return sorted(pid for pid, n in counts.items() if n > 1)


# -----------------------------------------------------------------------------
# Top-level validate()
# -----------------------------------------------------------------------------


def validate(
    ledger_path: Path,
    claimed_phases: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Validate a phase ledger and (optionally) a claim set.

    Returns a dict with: valid, hold_state, errors, warnings, exit_code,
    ledger_path, line_count, claimed_count.
    """
    result: dict[str, Any] = {
        "valid": True,
        "hold_state": HOLD_VALID,
        "errors": [],
        "warnings": [],
        "exit_code": EXIT_VALID,
        "ledger_path": str(ledger_path),
        "line_count": 0,
        "claimed_count": 0,
    }

    # Read entries
    if not ledger_path.exists():
        if claimed_phases:
            result["valid"] = False
            result["hold_state"] = HOLD_UNEVIDENCED_PASS
            result["exit_code"] = EXIT_UNEVIDENCED
            result["claimed_count"] = len(claimed_phases)
            result["errors"].append({
                "phase_id": "<ledger>",
                "line": 0,
                "kind": "LEDGER_FILE_MISSING",
                "detail": f"phase ledger file does not exist: {ledger_path}",
            })
        return result

    entries = read_entries(ledger_path)
    result["line_count"] = len(entries)
    result["claimed_count"] = len(claimed_phases) if claimed_phases else 0

    # Per-line internal-consistency checks (always)
    consistency_errors: list[dict[str, str]] = []
    for idx, e in enumerate(entries, start=1):
        consistency_errors.extend(_check_line_consistency(idx, e))
    result["errors"].extend(consistency_errors)

    # Duplicate phase_id warnings
    dupes = _find_duplicate_phase_ids(entries)
    for pid in dupes:
        result["warnings"].append(
            f"duplicate phase_id {pid!r} appears more than once in the ledger"
        )

    # Claim-evidence checks
    if claimed_phases:
        claim_errors = _check_claim_evidence(claimed_phases, entries)
        result["errors"].extend(claim_errors)

    # Determine hold state by precedence
    if not result["errors"]:
        result["valid"] = True
        result["hold_state"] = HOLD_VALID
        result["exit_code"] = EXIT_VALID
        return result

    # Precedence: EVIDENCE_CORRUPTED > RESULT_INCONSISTENT > UNEVIDENCED_PASS
    kinds = {e["kind"] for e in result["errors"]}
    if "EVIDENCE_CORRUPTED" in kinds:
        result["hold_state"] = HOLD_PHASE_EVIDENCE_CORRUPTED
        result["exit_code"] = EXIT_EVIDENCE_CORRUPTED
    elif "RESULT_INCONSISTENT" in kinds:
        result["hold_state"] = HOLD_PHASE_RESULT_INCONSISTENT
        result["exit_code"] = EXIT_RESULT_INCONSISTENT
    elif "UNCLAIMED_PHASE" in kinds or "LEDGER_FILE_MISSING" in kinds:
        result["hold_state"] = HOLD_UNEVIDENCED_PASS
        result["exit_code"] = EXIT_UNEVIDENCED
    else:
        # Unknown kind — fall back to unevidenced
        result["hold_state"] = HOLD_UNEVIDENCED_PASS
        result["exit_code"] = EXIT_UNEVIDENCED

    result["valid"] = False
    return result


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Validate a phase execution ledger and an optional claim set. "
            "Exits 0 on valid, 2/3/4 on unevidenced/corrupted/inconsistent."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--ledger", required=True,
        help="Path to the phase_ledger.jsonl file",
    )
    p.add_argument(
        "--claimed-phases",
        help=(
            "Comma-separated list of phase IDs the agent/workflow claims "
            "as PASS. If omitted, only internal-consistency checks run."
        ),
    )
    p.add_argument(
        "--output-json",
        help="Optional path to write a JSON validation report",
    )
    p.add_argument(
        "--allow-legacy", action="store_true",
        help=(
            "Reserved for forward compat — currently a no-op. "
            "Use to mark the invocation as forward-compatible."
        ),
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)

    ledger_path = Path(args.ledger)
    claimed: Optional[list[str]] = None
    if args.claimed_phases:
        claimed = [p.strip() for p in args.claimed_phases.split(",") if p.strip()]

    result = validate(ledger_path, claimed_phases=claimed)

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2))

    # Always print a one-line summary on stdout for log readability
    print(json.dumps({
        "valid": result["valid"],
        "hold_state": result["hold_state"],
        "line_count": result["line_count"],
        "claimed_count": result["claimed_count"],
        "error_count": len(result["errors"]),
        "warning_count": len(result["warnings"]),
    }))

    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
