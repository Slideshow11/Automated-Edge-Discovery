#!/usr/bin/env python3
"""
Local EdgeHypothesisRegistry v1 validator.
Validates one or more EdgeHypothesisRegistry JSONL files against the v1 schema and governance rules.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Any, List

# ID patterns
ID_PATTERN_HYP = re.compile(r"^HYP-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_TRL = re.compile(r"^TRL-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_SSM = re.compile(r"^SSM-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_MAS = re.compile(r"^MAS-[0-9]{4}-[0-9]{4}$")

# Enums
STATUSES = {
    "proposed", "specified", "testing", "parked",
    "falsified", "review_ready", "approved_for_next_stage", "superseded"
}
EVIDENCE_STAGES = {"exploratory", "confirmatory", "ablated"}
SOURCE_TYPES = {"theory_first", "exploratory_anomaly", "post_hoc_theory"}
SOURCE_LANES = {"theory_first", "exploratory_anomaly", "post_hoc_theory", "confirmatory"}
THEORY_TIMINGS = {"pre_registration", "post_discovery"}

# Required top-level fields
REQUIRED_TOP_LEVEL = [
    "hypothesis_id",
    "registry_version",
    "title",
    "status",
    "status_reason",
    "evidence_stage",
    "source_type",
    "source_lane",
    "theory_timing",
    "manual_review_required",
    "created_at",
]

# All permitted top-level fields (for additionalProperties: false enforcement)
ALLOWED_ROOT_FIELDS = {
    "hypothesis_id",
    "registry_version",
    "title",
    "status",
    "status_reason",
    "evidence_stage",
    "source_type",
    "source_lane",
    "theory_timing",
    "manual_review_required",
    "live_trading_allowed",
    "automated_promotion_allowed",
    "production_execution_allowed",
    "automated_registry_mutation_allowed",
    "created_at",
    "updated_at",
    "data_manifest_refs",
    "search_space_refs",
    "trial_ledger_refs",
    "model_assessment_refs",
    "review_packet_refs",
    "mechanism_report_refs",
    "posthoc_theory_note_refs",
    "manual_decision_refs",
    "promotion_restrictions",
    "notes",
    "hypothesis_card_ref",
    "lifecycle_events",
    "mechanism_summary",
}

# Governance stop-rule fields — must be absent or false
GOVERNANCE_STOP_RULE_FIELDS = [
    "automated_promotion_allowed",
    "live_trading_allowed",
    "production_execution_allowed",
    "automated_registry_mutation_allowed",
]

# Ref arrays with ID format requirements
REF_FIELDS = {
    "trial_ledger_refs": ID_PATTERN_TRL,
    "search_space_refs": ID_PATTERN_SSM,
    "model_assessment_refs": ID_PATTERN_MAS,
}


class Blocker:
    """Structured validation blocker."""
    __slots__ = ("code", "object_type", "field", "message")

    def __init__(self, code: str, object_type: str, field: str, message: str):
        self.code = code
        self.object_type = object_type
        self.field = field
        self.message = message

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "object_type": self.object_type,
            "field": self.field,
            "message": self.message,
        }


def parse_args():
    p = argparse.ArgumentParser(
        description="Validate one or more EdgeHypothesisRegistry v1 JSONL files."
    )
    p.add_argument(
        "files",
        nargs="+",
        help="Path to one or more EdgeHypothesisRegistry JSONL files.",
    )
    p.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text)",
    )
    return p.parse_args()


def validate_record(entry: Dict[str, Any]) -> List[Blocker]:
    """
    Validate a single EHR record (already parsed from JSONL).
    Returns a list of Blocker objects.
    """
    blockers: List[Blocker] = []

    # 0. Root must be an object
    if not isinstance(entry, dict):
        blockers.append(Blocker(
            "invalid_object",
            "edge_hypothesis_registry_entry",
            "$",
            "EHR entry must be a JSON object"
        ))
        return blockers

    # 1. Required top-level fields — missing, null, or empty/whitespace-only string fails
    TEXTUAL_REQUIRED = {"title", "status_reason", "created_at"}
    for field in REQUIRED_TOP_LEVEL:
        val = entry.get(field)
        if val is None:
            blockers.append(Blocker(
                "missing_required_field",
                "edge_hypothesis_registry_entry",
                field,
                f"{field} is required"
            ))
        elif isinstance(val, str) and val.strip() == "":
            blockers.append(Blocker(
                "missing_required_field",
                "edge_hypothesis_registry_entry",
                field,
                f"{field} is required and cannot be empty"
            ))

    # Cannot safely continue if required fields are missing
    if blockers:
        return blockers

    # 1b. Reject unknown top-level fields
    for field in entry:
        if field not in ALLOWED_ROOT_FIELDS:
            blockers.append(Blocker(
                "unknown_root_field",
                "edge_hypothesis_registry_entry",
                field,
                f"unknown root field '{field}' is not permitted"
            ))

    # 2. hypothesis_id format
    hyp_id = entry.get("hypothesis_id", "")
    if not isinstance(hyp_id, str):
        blockers.append(Blocker(
            "invalid_id_format",
            "edge_hypothesis_registry_entry",
            "hypothesis_id",
            "hypothesis_id must be a string"
        ))
    elif not ID_PATTERN_HYP.match(hyp_id):
        blockers.append(Blocker(
            "invalid_id_format",
            "edge_hypothesis_registry_entry",
            "hypothesis_id",
            f"hypothesis_id '{hyp_id}' does not match HYP-YYYY-NNNN format"
        ))

    # 3. registry_version
    reg_ver = entry.get("registry_version", "")
    if reg_ver != "edge_registry_v1":
        blockers.append(Blocker(
            "invalid_const",
            "edge_hypothesis_registry_entry",
            "registry_version",
            f"registry_version must be 'edge_registry_v1', got '{reg_ver}'"
        ))

    # 4. status enum — must be a string and in allowed set
    status = entry.get("status")
    if not isinstance(status, str):
        blockers.append(Blocker(
            "invalid_enum",
            "edge_hypothesis_registry_entry",
            "status",
            f"status must be a string, got {type(status).__name__}"
        ))
    elif status not in STATUSES:
        blockers.append(Blocker(
            "invalid_enum",
            "edge_hypothesis_registry_entry",
            "status",
            f"status '{status}' not in allowed set"
        ))

    # 5. evidence_stage enum — must be a string and in allowed set
    evidence_stage = entry.get("evidence_stage")
    if not isinstance(evidence_stage, str):
        blockers.append(Blocker(
            "invalid_enum",
            "edge_hypothesis_registry_entry",
            "evidence_stage",
            f"evidence_stage must be a string, got {type(evidence_stage).__name__}"
        ))
    elif evidence_stage not in EVIDENCE_STAGES:
        blockers.append(Blocker(
            "invalid_enum",
            "edge_hypothesis_registry_entry",
            "evidence_stage",
            f"evidence_stage '{evidence_stage}' not in allowed set"
        ))

    # 6. source_type enum — must be a string and in allowed set
    source_type = entry.get("source_type")
    if not isinstance(source_type, str):
        blockers.append(Blocker(
            "invalid_enum",
            "edge_hypothesis_registry_entry",
            "source_type",
            f"source_type must be a string, got {type(source_type).__name__}"
        ))
    elif source_type not in SOURCE_TYPES:
        blockers.append(Blocker(
            "invalid_enum",
            "edge_hypothesis_registry_entry",
            "source_type",
            f"source_type '{source_type}' not in allowed set"
        ))

    # 7. source_lane enum — must be a string and in allowed set
    source_lane = entry.get("source_lane")
    if not isinstance(source_lane, str):
        blockers.append(Blocker(
            "invalid_enum",
            "edge_hypothesis_registry_entry",
            "source_lane",
            f"source_lane must be a string, got {type(source_lane).__name__}"
        ))
    elif source_lane not in SOURCE_LANES:
        blockers.append(Blocker(
            "invalid_enum",
            "edge_hypothesis_registry_entry",
            "source_lane",
            f"source_lane '{source_lane}' not in allowed set"
        ))

    # 8. theory_timing enum — must be a string and in allowed set
    theory_timing = entry.get("theory_timing")
    if not isinstance(theory_timing, str):
        blockers.append(Blocker(
            "invalid_enum",
            "edge_hypothesis_registry_entry",
            "theory_timing",
            f"theory_timing must be a string, got {type(theory_timing).__name__}"
        ))
    elif theory_timing not in THEORY_TIMINGS:
        blockers.append(Blocker(
            "invalid_enum",
            "edge_hypothesis_registry_entry",
            "theory_timing",
            f"theory_timing '{theory_timing}' not in allowed set"
        ))

    # 9. manual_review_required must be a strict boolean (true or false only)
    mrr = entry.get("manual_review_required")
    if mrr is not None and not isinstance(mrr, bool):
        blockers.append(Blocker(
            "invalid_boolean",
            "edge_hypothesis_registry_entry",
            "manual_review_required",
            f"manual_review_required must be a boolean, got {type(mrr).__name__}"
        ))

    # 10. Governance stop-rule fields — must be absent or false
    for field in GOVERNANCE_STOP_RULE_FIELDS:
        val = entry.get(field)
        if val is not None and val is not False:
            blockers.append(Blocker(
                "forbidden_governance_field",
                "edge_hypothesis_registry_entry",
                field,
                f"{field} must be absent or false"
            ))

    # 11. Ref arrays — type check + ID format
    for ref_field, id_pattern in REF_FIELDS.items():
        ref_list = entry.get(ref_field)
        if ref_list is not None:
            if not isinstance(ref_list, list):
                blockers.append(Blocker(
                    "invalid_list",
                    "edge_hypothesis_registry_entry",
                    ref_field,
                    f"{ref_field} must be a list"
                ))
            else:
                for i, item in enumerate(ref_list):
                    if not isinstance(item, str):
                        blockers.append(Blocker(
                            "invalid_ref_type",
                            "edge_hypothesis_registry_entry",
                            f"{ref_field}[{i}]",
                            f"{ref_field} items must be strings, got {type(item).__name__}"
                        ))
                    elif not id_pattern.match(item):
                        blockers.append(Blocker(
                            "invalid_ref_format",
                            "edge_hypothesis_registry_entry",
                            f"{ref_field}[{i}]",
                            f"{ref_field} item '{item}' does not match required format"
                        ))

    # 12. lifecycle_events — type check, required item fields, and registry_mutation_mode enforcement
    lce = entry.get("lifecycle_events")
    if lce is not None:
        if not isinstance(lce, list):
            blockers.append(Blocker(
                "invalid_list",
                "edge_hypothesis_registry_entry",
                "lifecycle_events",
                "lifecycle_events must be a list"
            ))
        else:
            for i, evt in enumerate(lce):
                if not isinstance(evt, dict):
                    blockers.append(Blocker(
                        "invalid_object",
                        "edge_hypothesis_registry_entry",
                        f"lifecycle_events[{i}]",
                        "lifecycle_events items must be objects"
                    ))
                    continue
                # Required fields per schema: event_id, event_type, event_timestamp,
                # actor, to_status, manual_review_required
                REQUIRED_LCE_FIELDS = [
                    "event_id", "event_type", "event_timestamp",
                    "actor", "to_status", "manual_review_required"
                ]
                for field in REQUIRED_LCE_FIELDS:
                    val = evt.get(field)
                    if val is None:
                        blockers.append(Blocker(
                            "missing_required_field",
                            "edge_hypothesis_registry_entry",
                            f"lifecycle_events[{i}].{field}",
                            f"lifecycle_events item field '{field}' is required"
                        ))
                    elif field in ("event_id", "event_type", "event_timestamp", "actor", "to_status"):
                        if not isinstance(val, str):
                            blockers.append(Blocker(
                                "invalid_type",
                                "edge_hypothesis_registry_entry",
                                f"lifecycle_events[{i}].{field}",
                                f"lifecycle_events[{i}].{field} must be a string, got {type(val).__name__}"
                            ))
                        elif val.strip() == "":
                            blockers.append(Blocker(
                                "missing_required_field",
                                "edge_hypothesis_registry_entry",
                                f"lifecycle_events[{i}].{field}",
                                f"lifecycle_events[{i}].{field} is required and cannot be empty"
                            ))
                    elif field == "manual_review_required":
                        if not isinstance(val, bool):
                            blockers.append(Blocker(
                                "invalid_boolean",
                                "edge_hypothesis_registry_entry",
                                f"lifecycle_events[{i}].{field}",
                                f"lifecycle_events[{i}].{field} must be a boolean, got {type(val).__name__}"
                            ))
                rmm = evt.get("registry_mutation_mode")
                if rmm is not None and rmm != "manual":
                    blockers.append(Blocker(
                        "forbidden_governance_field",
                        "edge_hypothesis_registry_entry",
                        f"lifecycle_events[{i}].registry_mutation_mode",
                        f"registry_mutation_mode must be 'manual', got '{rmm}'"
                    ))

    # 13. Cross-field rule: approved_for_next_stage requires all four ref arrays non-empty
    if status == "approved_for_next_stage":
        required_refs = ["review_packet_refs", "trial_ledger_refs", "search_space_refs", "model_assessment_refs"]
        for ref_field in required_refs:
            ref_list = entry.get(ref_field)
            if not ref_list or not isinstance(ref_list, list) or len(ref_list) == 0:
                blockers.append(Blocker(
                    "approved_missing_required_refs",
                    "edge_hypothesis_registry_entry",
                    ref_field,
                    f"status=approved_for_next_stage requires non-empty {ref_field}"
                ))

    return blockers


def validate_file(path: Path) -> List[Blocker]:
    """
    Read a JSONL file and validate each record.
    Returns a flat list of Blocker objects for all records.
    """
    blockers: List[Blocker] = []

    # Read
    try:
        with path.open() as f:
            raw = f.read()
    except Exception as e:
        blockers.append(Blocker(
            "invalid_json",
            "edge_hypothesis_registry_file",
            "$",
            f"Could not read file: {e}"
        ))
        return blockers

    # Parse each non-empty line as JSON
    for line_no, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as e:
            blockers.append(Blocker(
                "invalid_json",
                "edge_hypothesis_registry_file",
                f"line {line_no}",
                f"Invalid JSON on line {line_no}: {e}"
            ))
            continue

        record_blockers = validate_record(entry)
        for b in record_blockers:
            # Prefix field with line number for traceability
            blockers.append(Blocker(
                b.code, b.object_type,
                f"line {line_no}:{b.field}",
                b.message
            ))

    return blockers


def main():
    args = parse_args()

    all_blockers: List[Blocker] = []
    per_file: Dict[str, List[Blocker]] = {}

    for file_path_str in args.files:
        path = Path(file_path_str)
        if not path.exists():
            print(f"ERROR: file not found: {path}", file=sys.stderr)
            sys.exit(2)
        blockers = validate_file(path)
        per_file[str(path)] = blockers
        all_blockers.extend(blockers)

    has_blockers = bool(all_blockers)

    if args.format == "json":
        out = {
            "files": {
                str(path): {
                    "blockers_count": len(blks),
                    "blockers": [b.to_dict() for b in blks],
                }
                for path, blks in per_file.items()
            },
            "total_blockers": len(all_blockers),
        }
        print(json.dumps(out, indent=2))
    else:
        for path_str, blks in per_file.items():
            print(f"file: {path_str}")
            print(f"blockers_count: {len(blks)}")
            if blks:
                for b in blks:
                    print(f"  [{b.code}] {b.object_type}.{b.field}: {b.message}")

    # Exit 2 for usage/read/parse errors (invalid_json code), exit 1 for blockers, exit 0 for valid
    parse_errors = [b for b in all_blockers if b.code == "invalid_json"]
    sys.exit(2 if parse_errors else (1 if has_blockers else 0))


if __name__ == "__main__":
    main()
