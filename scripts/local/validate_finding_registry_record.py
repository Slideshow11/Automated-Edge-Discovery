#!/usr/bin/env python3
"""
validate_finding_registry_record.py

Inspect-only schema validator for finding lifecycle registry records.
No network calls, no GitHub API calls, no mutation paths.

Validates a single JSON record against the finding lifecycle registry schema
defined in docs/finding_lifecycle_registry_design.md.

Usage:
    python3 scripts/local/validate_finding_registry_record.py \
        --input-json <path> \
        --output-json <path> \
        --output-md <path>

Exit codes:
    0 = VALID_FINDING_RECORD
    1 = HOLD_* (validation failed)
    2 = HOLD_UNKNOWN (error)
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------
VALID_FINDING_RECORD = "VALID_FINDING_RECORD"

HOLD_INVALID_JSON = "HOLD_INVALID_JSON"
HOLD_MISSING_REQUIRED_FIELD = "HOLD_MISSING_REQUIRED_FIELD"
HOLD_INVALID_LIFECYCLE_STATE = "HOLD_INVALID_LIFECYCLE_STATE"
HOLD_INVALID_SEVERITY = "HOLD_INVALID_SEVERITY"
HOLD_INVALID_BOOLEAN_FIELD = "HOLD_INVALID_BOOLEAN_FIELD"
HOLD_INVALID_TRANSITION = "HOLD_INVALID_TRANSITION"
HOLD_INVALID_RESOLUTION_METHOD = "HOLD_INVALID_RESOLUTION_METHOD"
HOLD_POLICY_VIOLATION = "HOLD_POLICY_VIOLATION"
HOLD_UNKNOWN = "HOLD_UNKNOWN"

HOLD_STATUSES = (
    HOLD_INVALID_JSON,
    HOLD_MISSING_REQUIRED_FIELD,
    HOLD_INVALID_LIFECYCLE_STATE,
    HOLD_INVALID_SEVERITY,
    HOLD_INVALID_BOOLEAN_FIELD,
    HOLD_INVALID_TRANSITION,
    HOLD_INVALID_RESOLUTION_METHOD,
    HOLD_POLICY_VIOLATION,
    HOLD_UNKNOWN,
)

# ---------------------------------------------------------------------------
# Constants from the registry design
# ---------------------------------------------------------------------------
VALID_LIFECYCLE_STATES = {
    "OPEN",
    "STALE",
    "RESOLVED_BY_PATCH",
    "RESOLVED_BY_POLICY",
    "WAIVED",
    "SUPERSEDED",
    "ESCALATED",
    "INVALID",
}

VALID_SEVERITIES = {"INFO", "P3", "P2", "P1", "P0", "UNSPECIFIED_BLOCKING", "UNSPECIFIED_INFO"}

VALID_RESOLUTION_METHODS = {
    "resolveReviewThread",
    "patch_applied",
    "waiver",
    "manual_override",
    "not_applicable",
    None,  # allowed for non-terminal states
}

# Forbidden resolution methods — never valid under any circumstances
FORBIDDEN_RESOLUTION_METHODS = {
    "deleteReviewComment",
    "deletePullRequestReviewComment",
    "dismissReview",
    "admin_merge",
    "delete_comment",
    "delete_review_comment",
}

# Blocking severities under AED policy
BLOCKING_SEVERITIES = {"P0", "P1", "P2"}

# ---------------------------------------------------------------------------
# Schema definitions
# ---------------------------------------------------------------------------
REQUIRED_FIELDS = {
    "finding_id",
    "pr_number",
    "source",
    "author",
    "severity",
    "title",
    "body_summary",
    "lifecycle_state",
    "status_reason",
    "current_head_sha",
    "created_at",
    "updated_at",
    "merge_blocking",
    "gate_source",
    "gate_policy_version",
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_record(raw: dict) -> tuple[str, list[str], list[str]]:
    """
    Validate a single registry record dict.

    Returns (status, errors, warnings).
    Errors are hard failures; warnings are non-blocking notices.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # --- Input must be a dict -------------------------------------------------
    if not isinstance(raw, dict):
        return HOLD_INVALID_JSON, ["input must be a dict, got: " + type(raw).__name__], []

    # --- Required fields ---------------------------------------------------
    missing = REQUIRED_FIELDS - set(raw.keys())
    if missing:
        for field in sorted(missing):
            errors.append(f"missing_required_field: {field}")
        # Can't proceed without required fields
        return HOLD_MISSING_REQUIRED_FIELD, errors, warnings

    # --- finding_id --------------------------------------------------------
    if not raw.get("finding_id") or not str(raw["finding_id"]).strip():
        errors.append("finding_id must be non-empty string")

    # --- pr_number ---------------------------------------------------------
    pr_num = raw.get("pr_number")
    if not isinstance(pr_num, int) or pr_num <= 0:
        errors.append(f"pr_number must be positive integer, got: {pr_num!r}")

    # --- severity ----------------------------------------------------------
    severity = raw.get("severity")
    if severity not in VALID_SEVERITIES:
        errors.append(f"severity must be one of {sorted(VALID_SEVERITIES)}, got: {severity!r}")
        # Don't return early — continue to catch additional errors

    # --- lifecycle_state ---------------------------------------------------
    lifecycle_state = raw.get("lifecycle_state")
    if lifecycle_state not in VALID_LIFECYCLE_STATES:
        errors.append(
            f"lifecycle_state must be one of {sorted(VALID_LIFECYCLE_STATES)}, got: {lifecycle_state!r}"
        )

    # --- merge_blocking ----------------------------------------------------
    merge_blocking = raw.get("merge_blocking")
    if not isinstance(merge_blocking, bool):
        errors.append(f"merge_blocking must be boolean, got: {merge_blocking!r}")

    # --- created_at --------------------------------------------------------
    created_at = raw.get("created_at")
    if not created_at or not isinstance(created_at, str) or not created_at.strip():
        errors.append("created_at must be non-empty string")

    # --- updated_at --------------------------------------------------------
    updated_at = raw.get("updated_at")
    if not updated_at or not isinstance(updated_at, str) or not updated_at.strip():
        errors.append("updated_at must be non-empty string")

    # --- gate_policy_version -----------------------------------------------
    gpv = raw.get("gate_policy_version")
    if not gpv or not isinstance(gpv, str) or not gpv.strip():
        errors.append("gate_policy_version must be non-empty string")

    # --- gate_source -------------------------------------------------------
    gs = raw.get("gate_source")
    if merge_blocking and (not gs or not isinstance(gs, str) or not gs.strip()):
        errors.append("gate_source must be non-empty when merge_blocking=true")

    # --- severity+merge_blocking consistency --------------------------------
    if severity in BLOCKING_SEVERITIES and lifecycle_state == "OPEN":
        if merge_blocking is False:
            errors.append(
                f"OPEN {severity} finding must have merge_blocking=true "
                "(P2 is blocking by default under AED policy)"
            )
    # UNSPECIFIED_BLOCKING is a documented blocking severity
    if severity == "UNSPECIFIED_BLOCKING" and lifecycle_state == "OPEN":
        if merge_blocking is not True:
            errors.append(
                "OPEN UNSPECIFIED_BLOCKING finding must have merge_blocking=true "
                "(UNSPECIFIED_BLOCKING is a merge-blocking severity per "
                "docs/finding_lifecycle_registry_design.md)"
            )

    # --- policy violations: forbidden resolution methods -------------------
    resolution_method = raw.get("resolution_method")
    if resolution_method in FORBIDDEN_RESOLUTION_METHODS:
        errors.append(
            f"resolution_method {resolution_method!r} is forbidden: "
            "must not use deleteReviewComment, deletePullRequestReviewComment, "
            "dismissReview, or admin_merge"
        )

    # --- OPEN/STALE must not carry resolution_method -----------------------
    # Per docs/finding_lifecycle_registry_design.md lines 186-187,
    # OPEN and STALE are non-terminal states; resolution_method must be null.
    if lifecycle_state in ("OPEN", "STALE"):
        if resolution_method is not None:
            errors.append(
                f"{lifecycle_state} finding must not have resolution_method; "
                f"got: {resolution_method!r} (OPEN/STALE are non-terminal states)"
            )

    # --- State-specific resolution_method validation ---------------------
    # OPEN/STALE must not have resolution_method (checked above).
    # Terminal/transition states must use only documented methods or None.
    VALID_RESOLUTION_METHODS_BY_STATE = {
        "WAIVED": {"waiver", "manual_override", "not_applicable", None},
        "SUPERSEDED": {"not_applicable", None},
        "INVALID": {"not_applicable", None},
        "RESOLVED_BY_PATCH": {"patch_applied", "not_applicable", None},
    }
    if lifecycle_state in VALID_RESOLUTION_METHODS_BY_STATE:
        allowed = VALID_RESOLUTION_METHODS_BY_STATE[lifecycle_state]
        if resolution_method not in allowed:
            errors.append(
                f"{lifecycle_state} resolution_method must be one of "
                f"{sorted(allowed - {None})} or null, got: {resolution_method!r}"
            )

    # --- RESOLVED_BY_POLICY requirements ----------------------------------
    if lifecycle_state == "RESOLVED_BY_POLICY":
        # Required fields for RESOLVED_BY_POLICY
        for field in ("thread_id", "current_head_sha", "evidence_summary",
                      "evidence_commands", "audit_log_path", "resolution_method"):
            if not raw.get(field):
                errors.append(f"RESOLVED_BY_POLICY requires {field}")

        if resolution_method != "resolveReviewThread":
            errors.append(
                "RESOLVED_BY_POLICY resolution_method must be 'resolveReviewThread', "
                f"got: {resolution_method!r}"
            )

        if raw.get("merge_blocking") is not False:
            errors.append("RESOLVED_BY_POLICY must have merge_blocking=false")

    # --- WAIVED requirements -----------------------------------------------
    if lifecycle_state == "WAIVED":
        for field in ("status_reason", "resolved_by", "resolved_at"):
            if not raw.get(field):
                errors.append(f"WAIVED requires {field}")
        has_evidence = bool(raw.get("evidence_summary") or raw.get("audit_log_path"))
        if not has_evidence:
            errors.append("WAIVED requires evidence_summary or audit_log_path")

    # --- INVALID requirements ----------------------------------------------
    if lifecycle_state == "INVALID":
        if not raw.get("evidence_summary"):
            errors.append("INVALID requires evidence_summary proving why the finding is invalid")
        if not raw.get("status_reason"):
            errors.append("INVALID requires status_reason")

    # --- ESCALATED requirements --------------------------------------------
    if lifecycle_state == "ESCALATED":
        if not raw.get("status_reason"):
            errors.append("ESCALATED requires status_reason")
        if raw.get("merge_blocking") is not True:
            es = raw.get("evidence_summary")
            if not es:
                errors.append(
                    "ESCALATED with merge_blocking!=true requires evidence_summary "
                    "documenting why merge is not blocked"
                )

    # --- current_head_sha format ------------------------------------------
    chs = raw.get("current_head_sha")
    if chs and (not isinstance(chs, str) or len(chs) < 6):
        warnings.append(f"current_head_sha looks unusually short: {chs!r}")

    # --- terminal state required fields ---------------------------------
    # resolved_at and resolved_by must be non-None strings for terminal states
    terminal_states = {"WAIVED", "SUPERSEDED", "ESCALATED", "INVALID", "RESOLVED_BY_POLICY"}
    if lifecycle_state in terminal_states:
        for field in ("resolved_at", "resolved_by"):
            val = raw.get(field)
            if val is None or (isinstance(val, str) and not val.strip()):
                errors.append(f"{lifecycle_state} requires {field}")

    # --- Return errors if any ----------------------------------------------
    if errors:
        return HOLD_MISSING_REQUIRED_FIELD, errors, warnings

    return VALID_FINDING_RECORD, [], warnings


def build_normalized_summary(record: dict) -> dict:
    """Build a compact normalized view of the record for the output JSON."""
    return {
        "finding_id": record.get("finding_id"),
        "pr_number": record.get("pr_number"),
        "lifecycle_state": record.get("lifecycle_state"),
        "severity": record.get("severity"),
        "merge_blocking": record.get("merge_blocking"),
        "resolution_method": record.get("resolution_method"),
        "blocking_level": record.get("blocking_level"),
        "gate_source": record.get("gate_source"),
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_json(path: str, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def write_md(path: str, status: str, record: dict, errors: list[str], warnings: list[str]) -> None:
    lines = [
        "# Finding Registry Record Validation",
        "",
        f"**Status**: `{status}`",
        "",
        f"**Finding ID**: `{record.get('finding_id', 'N/A')}`",
        f"**PR**: `{record.get('pr_number', 'N/A')}`",
        f"**Lifecycle State**: `{record.get('lifecycle_state', 'N/A')}`",
        f"**Severity**: `{record.get('severity', 'N/A')}`",
        f"**Merge Blocking**: `{record.get('merge_blocking')}`",
        "",
        "## Errors",
        "",
    ]
    if errors:
        for e in errors:
            lines.append(f"- `{e}`")
    else:
        lines.append("_none_")

    lines.extend(["", "## Warnings", ""])
    if warnings:
        for w in warnings:
            lines.append(f"- `{w}`")
    else:
        lines.append("_none_")

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a finding lifecycle registry record against the AED schema."
    )
    parser.add_argument("--input-json", required=True, help="Path to input JSON file")
    parser.add_argument("--output-json", required=True, help="Path to output JSON file")
    parser.add_argument("--output-md", required=True, help="Path to output Markdown file")
    args = parser.parse_args()

    # Read input
    input_path = Path(args.input_json)
    if not input_path.exists():
        status = HOLD_INVALID_JSON
        errors = [f"input file not found: {input_path}"]
        warnings: list[str] = []
        record_sample: dict = {}
    else:
        try:
            with open(input_path) as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            status = HOLD_INVALID_JSON
            errors = [f"JSON parse error: {e}"]
            warnings = []
            raw = {}
        else:
            status, errors, warnings = validate_record(raw)

        record_sample = dict(raw) if isinstance(raw, dict) else {}

    # Build output
    output = dict(
        status=status,
        input_path=str(input_path),
        finding_id=record_sample.get("finding_id"),
        errors=errors,
        warnings=warnings,
        normalized_summary=build_normalized_summary(record_sample),
    )

    write_json(args.output_json, output)
    write_md(args.output_md, status, record_sample, errors, warnings)

    print(f"Status: {status}")
    print(f"Errors: {len(errors)}")
    print(f"Warnings: {len(warnings)}")
    print(f"Output: {args.output_json}")

    if status == VALID_FINDING_RECORD:
        return 0
    if status in HOLD_STATUSES:
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())