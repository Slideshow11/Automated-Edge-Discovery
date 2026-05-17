#!/usr/bin/env python3
"""
Audit Log Consistency Validator

Read-only validation of AED merge-action audit log JSONL files.
Detects structural errors, legacy rows, duplicates, and type drift.
Does NOT mutate the input file.

Usage:
    python3 scripts/local/validate_merge_action_audit_log.py \
        --input /path/to/log.jsonl \
        --output-json /path/to/report.json \
        --output-md /path/to/report.md \
        [--strict] \
        [--allow-legacy] \
        [--expected-prs-json '[232,233,234,235]']
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_EVENT_TYPES = frozenset([
    "pr_merge",
    "controlled_smoke_create",
    "external_action",
    "blocked_action",
    "audit_correction",
])

LEGACY_EVENT_TYPES = frozenset([
    "legacy_missing_event_type",
    "legacy_unknown",
])

# Fields required for each event type
REQUIRED_FIELDS_BY_TYPE: dict[str, frozenset[str]] = {
    "pr_merge": frozenset([
        # NOTE: audit_log_version and timestamp are NOT here because they are
        # legacy-row conditions (missing in pre-1.0 rows). They are handled
        # separately with legacy-warning in non-strict mode or error in strict.
        "event_type",
        "pr_number",
        "head_sha",
        "merge_sha",
        "merged_at",
        "ci_status",
        "codex_status",
        "scope_status",
        # NOTE: authorization_phrase is NOT here because it is a legacy-row
        # condition (missing in pre-1.0 rows). It is handled separately with
        # legacy-warning in non-strict mode or error in strict mode.
        "hermes_touched",
        "dispatch_occurred",
        "production_board_touched",
        # NOTE: gate_catches is NOT here because missing gate_catches is a
        # legacy-row condition, handled separately with a warning (not error)
        # in non-strict mode or an error in strict mode.
    ]),
    "controlled_smoke_create": frozenset([
        # Note: real rows use task_id. Legacy rows may have candidate_id.
        # At least one of task_id or candidate_id is required.
        # task_id is preferred; candidate_id-only rows are treated as legacy.
        "event_type",
        "timestamp",
        "board",
        # NOTE: task_id and candidate_id are NOT mandatory here because
        # some legacy rows have candidate_id but not task_id. The actual
        # validation is done in the event-type-specific block below.
    ]),
    "external_action": frozenset([
        "event_type",
        "timestamp",
        "action",
    ]),
    "blocked_action": frozenset([
        "event_type",
        "timestamp",
        "action",
        "reason",
    ]),
    "audit_correction": frozenset([
        "event_type",
        "timestamp",
        "corrects_line",
        "corrects_pr_number",
        "correction_reason",
        "replacement_fields",
        "created_at",
    ]),
}

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
PARTIAL_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,39}$")

SAFETY_BOOLEAN_KEYS = frozenset([
    "hermes_touched",
    "dispatch_occurred",
    "production_board_touched",
    "import_performed",
    "pr_created",
])

LEGACY_LEGACY_FLAGS = frozenset([
    "legacy_missing_event_type",
    "legacy_string_pr_number",
    "legacy_missing_gate_catches",
    "legacy_missing_safety_booleans",
])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_valid_sha(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return bool(SHA_PATTERN.match(value))


def is_partial_sha(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return bool(PARTIAL_SHA_PATTERN.match(value))


def is_boolean(value: Any) -> bool:
    return isinstance(value, bool)


def get_legacy_codes(row: dict[str, Any], line_idx: int) -> list[str]:
    """Return list of legacy codes that apply to this row."""
    codes = []
    if row.get("event_type") is None:
        codes.append("legacy_missing_event_type")
    elif not isinstance(row.get("event_type"), str):
        codes.append("legacy_missing_event_type")
    pr_num = row.get("pr_number")
    if pr_num is not None and not isinstance(pr_num, int):
        codes.append("legacy_string_pr_number")
    if row.get("gate_catches") is None:
        codes.append("legacy_missing_gate_catches")
    safety_keys = [k for k in SAFETY_BOOLEAN_KEYS if k in row]
    if not safety_keys:
        codes.append("legacy_missing_safety_booleans")
    return codes


def build_issue(
    line_idx: int,
    code: str,
    message: str,
    severity: str = "error",
    **extra: Any,
) -> dict[str, Any]:
    result = {
        "line": line_idx,
        "code": code,
        "message": message,
        "severity": severity,
    }
    result.update(extra)
    return result


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------

def validate_log(
    input_path: str,
    strict: bool = False,
    allow_legacy: bool = False,
    expected_prs: list[int] | None = None,
) -> dict[str, Any]:
    """
    Validate an audit log JSONL file.

    Returns a report dict with:
        - valid (bool)
        - strict (bool)
        - input_path
        - line_count
        - events_by_type (dict)
        - expected_prs
        - pr_merge_counts (dict of pr_number -> count)
        - errors
        - warnings
        - duplicates
        - legacy_rows
    """
    path = Path(input_path)
    if not path.exists():
        return {
            "valid": False,
            "strict": strict,
            "input_path": input_path,
            "line_count": 0,
            "events_by_type": {},
            "expected_prs": expected_prs or [],
            "pr_merge_counts": {},
            "errors": [build_issue(0, "file_not_found", f"Input file not found: {input_path}")],
            "warnings": [],
            "duplicates": [],
            "legacy_rows": [],
        }

    lines = path.read_text().splitlines()
    line_count = len(lines)

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    legacy_rows: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []

    events_by_type: dict[str, int] = {}
    pr_merge_counts: dict[str, int] = {}
    pr_merge_seen: dict[str, list[int]] = {}  # pr_number -> [line_indices]
    merge_sha_seen: dict[str, list[int]] = {}
    head_sha_pr_seen: dict[str, dict[str, list[int]]] = {}  # pr -> head_sha -> [lines]
    valid_row_count = 0

    for idx, raw_line in enumerate(lines):
        line_idx = idx + 1  # 1-indexed

        stripped = raw_line.strip()
        if not stripped:
            errors.append(build_issue(
                line_idx, "empty_line",
                "Empty or whitespace-only line",
            ))
            continue

        # Try to parse JSON
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError as e:
            errors.append(build_issue(
                line_idx, "invalid_json",
                f"Invalid JSON: {e}",
            ))
            continue

        if not isinstance(row, dict):
            errors.append(build_issue(
                line_idx, "non_object_line",
                f"Line is a {type(row).__name__}, expected JSON object",
            ))
            continue

        # Detect event_type
        event_type = row.get("event_type")
        if event_type is None:
            # Legacy row — classify what kind
            legacy_codes = get_legacy_codes(row, line_idx)
            if legacy_codes:
                for code in legacy_codes:
                    if code == "legacy_missing_event_type":
                        warnings.append(build_issue(
                            line_idx, code,
                            "event_type is missing or null — legacy row",
                            severity="warning",
                            **row,
                        ))
                    else:
                        warnings.append(build_issue(
                            line_idx, code,
                            f"Legacy row flag: {code}",
                            severity="warning",
                            **row,
                        ))
                # Try to infer event_type
                if "pr_number" in row or "head_sha" in row:
                    inferred = "pr_merge"
                else:
                    inferred = "legacy_unknown"
                legacy_rows.append({
                    "line": line_idx,
                    "inferred_type": inferred,
                    "legacy_codes": legacy_codes,
                    "row": {k: v for k, v in row.items() if k in [
                        "pr_number", "head_sha", "merge_sha", "merged_at", "authorization_phrase"
                    ]},
                })
                events_by_type[inferred] = events_by_type.get(inferred, 0) + 1
            else:
                errors.append(build_issue(
                    line_idx, "missing_event_type",
                    "event_type is missing and no legacy codes determined",
                ))
            continue

        if not isinstance(event_type, str):
            errors.append(build_issue(
                line_idx, "event_type_not_string",
                f"event_type is {type(event_type).__name__}, expected string",
            ))
            continue

        # Classify event type
        if event_type not in VALID_EVENT_TYPES and event_type not in LEGACY_EVENT_TYPES:
            warnings.append(build_issue(
                line_idx, "unknown_event_type",
                f"Unknown event_type: {event_type}",
                severity="warning",
            ))

        events_by_type[event_type] = events_by_type.get(event_type, 0) + 1

        # Collect field errors based on event type
        required = REQUIRED_FIELDS_BY_TYPE.get(event_type, frozenset())
        missing = []
        for field in required:
            if field not in row:
                missing.append(field)

        # For pr_merge — extra validation
        if event_type == "pr_merge":
            # Legacy checks for fields that might be missing in pre-1.0 rows
            row_pr_num = row.get("pr_number")
            if not strict:
                if not row.get("audit_log_version"):
                    warnings.append(build_issue(
                        line_idx, "legacy_missing_audit_log_version",
                        "audit_log_version is missing — legacy row",
                        severity="warning",
                        pr_number=row_pr_num,
                    ))
                if not row.get("timestamp"):
                    warnings.append(build_issue(
                        line_idx, "legacy_missing_timestamp",
                        "timestamp is missing — legacy row",
                        severity="warning",
                        pr_number=row_pr_num,
                    ))
            else:
                if not row.get("audit_log_version"):
                    missing.append("audit_log_version")
                if not row.get("timestamp"):
                    missing.append("timestamp")

            # Validate pr_number
            pr_num = row.get("pr_number")
            if pr_num is None:
                missing.append("pr_number")
            else:
                # Track duplicate PR numbers
                pr_key = str(pr_num)
                if pr_key not in pr_merge_counts:
                    pr_merge_counts[pr_key] = 0
                    pr_merge_seen[pr_key] = []
                pr_merge_counts[pr_key] += 1
                pr_merge_seen[pr_key].append(line_idx)

            # Validate SHA fields
            head_sha = row.get("head_sha")
            if head_sha is None:
                missing.append("head_sha")
            elif not is_valid_sha(head_sha):
                errors.append(build_issue(
                    line_idx, "malformed_head_sha",
                    f"head_sha is not a valid 40-char SHA: {repr(head_sha)}",
                    head_sha=head_sha,
                ))

            merge_sha = row.get("merge_sha")
            if merge_sha is None:
                missing.append("merge_sha")
            elif not is_valid_sha(merge_sha):
                errors.append(build_issue(
                    line_idx, "malformed_merge_sha",
                    f"merge_sha is not a valid 40-char SHA: {repr(merge_sha)}",
                    merge_sha=merge_sha,
                ))
            else:
                # Track duplicate merge_sha
                if merge_sha not in merge_sha_seen:
                    merge_sha_seen[merge_sha] = []
                merge_sha_seen[merge_sha].append(line_idx)

            # Check authorization_phrase
            if not row.get("authorization_phrase"):
                # authorization_phrase is required in schema 1.0+ but may be
                # missing in legacy rows — treat as legacy condition
                if not strict:
                    warnings.append(build_issue(
                        line_idx, "legacy_missing_authorization_phrase",
                        "authorization_phrase is missing — legacy row",
                        severity="warning",
                        pr_number=pr_num,
                    ))
                else:
                    missing.append("authorization_phrase")

            # Check safety booleans
            for key in SAFETY_BOOLEAN_KEYS:
                val = row.get(key)
                if val is not None:
                    if isinstance(val, str):
                        if val.lower() in ("false", "true"):
                            # Legacy row: safety boolean encoded as string "false"/"true"
                            if not strict:
                                warnings.append(build_issue(
                                    line_idx, "legacy_string_safety_boolean",
                                    f"{key} has string value {repr(val)} — legacy boolean encoding",
                                    severity="warning",
                                    **{key: repr(val)},
                                ))
                            else:
                                errors.append(build_issue(
                                    line_idx, "safety_boolean_not_boolean",
                                    f"{key} has non-boolean type {type(val).__name__}: {repr(val)}",
                                    **{key: repr(val)},
                                ))
                        else:
                            errors.append(build_issue(
                                line_idx, "safety_boolean_not_boolean",
                                f"{key} has non-boolean type {type(val).__name__}: {repr(val)}",
                                **{key: repr(val)},
                            ))
                    elif not is_boolean(val):
                        errors.append(build_issue(
                            line_idx, "safety_boolean_not_boolean",
                            f"{key} has non-boolean type {type(val).__name__}: {repr(val)}",
                            **{key: repr(val)},
                        ))

            # Check gate_catches
            gc = row.get("gate_catches")
            if gc is None:
                # gate_catches missing: legacy condition in non-strict, error in strict
                if not strict:
                    warnings.append(build_issue(
                        line_idx, "legacy_missing_gate_catches",
                        "gate_catches is missing — legacy row",
                        severity="warning",
                        pr_number=pr_num,
                    ))
                else:
                    missing.append("gate_catches")
            elif isinstance(gc, str):
                # gate_catches was written as a string (e.g. "{}") in some legacy rows
                if not strict:
                    warnings.append(build_issue(
                        line_idx, "legacy_string_gate_catches",
                        f"gate_catches is a string {repr(gc)} — expected dict",
                        severity="warning",
                        pr_number=pr_num,
                    ))
                else:
                    errors.append(build_issue(
                        line_idx, "gate_catches_not_object",
                        f"gate_catches is {type(gc).__name__}, expected dict",
                    ))
            elif not isinstance(gc, dict):
                errors.append(build_issue(
                    line_idx, "gate_catches_not_object",
                    f"gate_catches is {type(gc).__name__}, expected dict",
                ))

        elif event_type == "controlled_smoke_create":
            # Legacy: real rows have task_id but some older rows may have candidate_id
            has_task_id = "task_id" in row
            has_candidate_id = "candidate_id" in row
            if not has_task_id and not has_candidate_id:
                missing.append("task_id")  # at least one identifier required
            elif has_candidate_id and not has_task_id:
                # Legacy row: candidate_id present, task_id absent
                if not strict:
                    warnings.append(build_issue(
                        line_idx, "legacy_candidate_id_only_smoke_row",
                        "controlled_smoke_create row has candidate_id but no task_id — legacy row",
                        severity="warning",
                        candidate_id=row.get("candidate_id"),
                    ))
                else:
                    missing.append("task_id")

        elif event_type == "audit_correction":
            # Validate correction fields
            for f in ("corrects_line", "corrects_pr_number", "correction_reason",
                      "replacement_fields", "created_at"):
                if f not in row:
                    missing.append(f)

        # Report missing required fields
        for field in missing:
            errors.append(build_issue(
                line_idx, "missing_required_field",
                f"Required field '{field}' missing for event_type '{event_type}'",
                event_type=event_type,
                field=field,
            ))

        valid_row_count += 1

    # Check for duplicate PR merge entries
    for pr_key, line_indices in pr_merge_seen.items():
        if len(line_indices) > 1:
            for line_idx in line_indices[1:]:
                duplicates.append(build_issue(
                    line_idx, "duplicate_pr_merge_entry",
                    f"Duplicate pr_merge entry for PR {pr_key} (first seen on line {line_indices[0]})",
                    pr_number=pr_key,
                    duplicate_of_line=line_indices[0],
                    severity="error",
                ))

    # Check for duplicate merge_sha values
    for merge_sha, line_indices in merge_sha_seen.items():
        if len(line_indices) > 1:
            for line_idx in line_indices[1:]:
                duplicates.append(build_issue(
                    line_idx, "duplicate_merge_sha",
                    f"Duplicate merge_sha {merge_sha} (first seen on line {line_indices[0]})",
                    merge_sha=merge_sha,
                    duplicate_of_line=line_indices[0],
                    severity="error",
                ))

    # Check expected PRs
    if expected_prs is not None:
        for expected_pr in expected_prs:
            pr_key = str(expected_pr)
            count = pr_merge_counts.get(pr_key, 0)
            if count == 0:
                warnings.append(build_issue(
                    0, "expected_pr_not_found",
                    f"Expected PR {expected_pr} not found in audit log",
                    expected_pr=expected_pr,
                    severity="warning",
                ))
            elif count > 1:
                # Already captured in duplicate check
                pass
            # count == 1 is OK

    # Determine validity
    # In non-strict mode: warnings and legacy rows are OK (unless duplicates)
    # In strict mode: warnings and legacy rows are errors
    has_errors = bool(errors)
    has_warnings = bool(warnings)
    has_duplicates = bool(duplicates)
    has_legacy_rows = bool(legacy_rows)

    if strict:
        # Strict mode: legacy rows and warnings are treated as errors
        for lr in legacy_rows:
            errors.append(build_issue(
                lr["line"], "legacy_row_in_strict_mode",
                f"Legacy row (inferred {lr['inferred_type']}) not allowed in strict mode",
                **lr,
            ))
        for w in warnings:
            errors.append({**w, "severity": "error"})
        warnings = []

    # Re-evaluate after strict conversion (errors list may have grown)
    has_errors = bool(errors)
    has_warnings = bool(warnings)
    has_legacy_rows = bool(legacy_rows) and not strict

    valid = not has_errors and not has_duplicates

    return {
        "valid": valid,
        "strict": strict,
        "input_path": str(path.resolve()),
        "line_count": line_count,
        "events_by_type": events_by_type,
        "expected_prs": expected_prs or [],
        "pr_merge_counts": dict(sorted(pr_merge_counts.items())),
        "errors": errors,
        "warnings": warnings,
        "duplicates": duplicates,
        "legacy_rows": legacy_rows,
    }


# ---------------------------------------------------------------------------
# Markdown report generator
# ---------------------------------------------------------------------------

def build_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AED Audit Log Validation Report",
        "",
        f"**Input:** `{report['input_path']}`",
        f"**Lines:** {report['line_count']}",
        f"**Mode:** {'STRICT' if report['strict'] else 'NON-STRICT'}",
        f"**Valid:** {'✅ YES' if report['valid'] else '❌ NO'}",
        "",
    ]

    if report["pr_merge_counts"]:
        lines.append("## PR Merge Counts")
        lines.append("")
        lines.append("| PR | Count |")
        lines.append("|---|---|")
        for pr, count in sorted(report["pr_merge_counts"].items(), key=lambda x: int(x[0])):
            lines.append(f"| #{pr} | {count} |")
        lines.append("")

    if report["legacy_rows"]:
        lines.append("## Legacy Rows")
        lines.append("")
        for lr in report["legacy_rows"]:
            codes = ", ".join(lr["legacy_codes"])
            lines.append(f"- **Line {lr['line']}** — inferred: `{lr['inferred_type']}`, flags: `{codes}`")
            pr_num = lr["row"].get("pr_number", "unknown")
            head = lr["row"].get("head_sha", "unknown")
            lines.append(f"  - pr_number: `{pr_num}`, head_sha: `{head[:7]}...`")
        lines.append("")

    if report["warnings"]:
        lines.append(f"## ⚠️ Warnings ({len(report['warnings'])})")
        lines.append("")
        for w in report["warnings"]:
            lines.append(f"- **Line {w['line']}** `[{w['code']}]` — {w['message']}")
        lines.append("")

    if report["errors"]:
        lines.append(f"## ❌ Errors ({len(report['errors'])})")
        lines.append("")
        for e in report["errors"]:
            lines.append(f"- **Line {e['line']}** `[{e['code']}]` — {e['message']}")
        lines.append("")

    if report["duplicates"]:
        lines.append(f"## 🔁 Duplicates ({len(report['duplicates'])})")
        lines.append("")
        for d in report["duplicates"]:
            lines.append(f"- **Line {d['line']}** `[{d['code']}]` — {d['message']}")
        lines.append("")

    if not report["errors"] and not report["warnings"] and not report["duplicates"]:
        lines.append("✅ No errors, warnings, or duplicates found.")

    # Correction guidance
    lines.append("")
    lines.append("## Correction Strategy")
    lines.append("")
    lines.append("**Do NOT edit or delete existing audit rows.**")
    lines.append("")
    lines.append("To correct a bad row, append a new `audit_correction` event that references the")
    lines.append("original line number and PR number. This preserves append-only integrity.")
    lines.append("")
    lines.append("### audit_correction event format")
    lines.append("```json")
    lines.append('{')
    lines.append('  "event_type": "audit_correction",')
    lines.append('  "timestamp": "<ISO8601>",')
    lines.append('  "corrects_line": <line_number>,')
    lines.append('  "corrects_pr_number": "<PR>",')
    lines.append('  "correction_reason": "<description>",')
    lines.append('  "replacement_fields": { <key>: <value>, ... },')
    lines.append('  "created_at": "<ISO8601>"')
    lines.append('}')
    lines.append("```")
    lines.append("")
    lines.append(f"*Generated at: {datetime.now(timezone.utc).isoformat()}*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="AED Audit Log Consistency Validator — read-only",
    )
    parser.add_argument("--input", required=True, help="Path to input JSONL file")
    parser.add_argument("--output-json", required=True, help="Path to output JSON report")
    parser.add_argument("--output-md", required=True, help="Path to output markdown report")
    parser.add_argument("--strict", action="store_true",
                        help="Treat warnings and legacy rows as errors")
    parser.add_argument("--allow-legacy", action="store_true",
                        help="Allow legacy rows without failing (non-strict default)")
    parser.add_argument("--expected-prs-json", default="[]",
                        help="JSON array of expected PR numbers, e.g. '[232,233,234,235]'")

    args = parser.parse_args()

    # Parse expected PRs
    try:
        expected_prs = json.loads(args.expected_prs_json)
        if not isinstance(expected_prs, list):
            print(f"ERROR: --expected-prs-json must be a list, got {type(expected_prs).__name__}",
                  file=sys.stderr)
            return 1
        # Normalize to integers
        expected_prs = [int(x) for x in expected_prs]
    except json.JSONDecodeError as e:
        print(f"ERROR: --expected-prs-json is not valid JSON: {e}", file=sys.stderr)
        return 1

    report = validate_log(
        input_path=args.input,
        strict=args.strict,
        allow_legacy=args.allow_legacy,
        expected_prs=expected_prs,
    )

    # Write JSON report
    Path(args.output_json).write_text(json.dumps(report, indent=2))
    print(f"JSON report written to: {args.output_json}")

    # Write Markdown report
    md = build_markdown(report)
    Path(args.output_md).write_text(md)
    print(f"Markdown report written to: {args.output_md}")

    if not report["valid"]:
        print("❌ VALIDATION FAILED — see report for details")
        return 1
    else:
        print("✅ VALIDATION PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())