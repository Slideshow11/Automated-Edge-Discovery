#!/usr/bin/env python3
"""
Local ModelAssessmentSpec v1 validator.
Validates a single ModelAssessmentSpec JSON entry against the v1 schema and governance rules.
"""
import argparse
import json
import re
import sys
from pathlib import Path

# ID patterns
ID_PATTERN_MAS = re.compile(r"^MAS-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_TRL = re.compile(r"^TRL-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_SSM = re.compile(r"^SSM-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_HYP = re.compile(r"^HYP-[0-9]{4}-[0-9]{4}$")

# Allowed enum values
ASSESSMENT_STATUSES = {"draft", "reviewed", "rejected", "provisional", "accepted", "killed"}

# Required top-level fields
REQUIRED_TOP_LEVEL = [
    "assessment_id",
    "hypothesis_id",
    "trial_id",
    "search_space_id",
    "assessment_status",
    "metrics",
    "required_checks",
    "reviewer",
    "created_at",
]

# All permitted top-level fields (for additionalProperties: false enforcement)
ALLOWED_ROOT_FIELDS = {
    "assessment_id",
    "hypothesis_id",
    "trial_id",
    "search_space_id",
    "assessment_status",
    "metrics",
    "required_checks",
    "reviewer",
    "created_at",
}

# Required boolean checks
REQUIRED_CHECKS_FIELDS = [
    "sample_size_gate_passed",
    "leakage_check_passed",
    "pbo_check_passed",
    "dsr_check_passed",
    "confirmatory_evidence_present",
]


class Blocker:
    """Structured validation blocker."""
    __slots__ = ("code", "object_type", "field", "message")

    def __init__(self, code: str, object_type: str, field: str, message: str):
        self.code = code
        self.object_type = object_type
        self.field = field
        self.message = message

    def to_dict(self):
        return {
            "code": self.code,
            "object_type": self.object_type,
            "field": self.field,
            "message": self.message,
        }


def parse_args():
    p = argparse.ArgumentParser(description="Validate a ModelAssessmentSpec v1 JSON file.")
    p.add_argument("file_path", help="Path to ModelAssessmentSpec JSON file.")
    p.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="Output format (default: text)"
    )
    return p.parse_args()


def validate(entry):
    """
    Validate a ModelAssessmentSpec entry.
    Returns a list of Blocker objects.
    Exit code is determined separately by main().
    """
    blockers = []

    # 0. Root must be an object
    if not isinstance(entry, dict):
        blockers.append(Blocker(
            "invalid_object",
            "model_assessment_spec",
            "$",
            "ModelAssessmentSpec entry must be a JSON object"
        ))
        return blockers

    # 1. Required top-level fields
    for field in REQUIRED_TOP_LEVEL:
        val = entry.get(field)
        if val is None or val == "":
            blockers.append(Blocker(
                "missing_required_field",
                "model_assessment_spec_entry",
                field,
                f"{field} is required"
            ))

    if blockers:
        return blockers  # cannot continue safely if required fields missing

    # 1b. Reject unknown top-level fields
    for field in entry:
        if field not in ALLOWED_ROOT_FIELDS:
            blockers.append(Blocker(
                "unknown_root_field",
                "model_assessment_spec_entry",
                field,
                f"unknown root field '{field}' is not permitted"
            ))

    # 2. assessment_id format (MAS-YYYY-NNNN)
    assessment_id = entry.get("assessment_id", "")
    if not isinstance(assessment_id, str):
        blockers.append(Blocker(
            "invalid_id_format",
            "model_assessment_spec_entry",
            "assessment_id",
            "assessment_id must be a string matching MAS-YYYY-NNNN format"
        ))
    elif not ID_PATTERN_MAS.match(assessment_id):
        blockers.append(Blocker(
            "invalid_id_format",
            "model_assessment_spec_entry",
            "assessment_id",
            f"assessment_id '{assessment_id}' does not match MAS-YYYY-NNNN format"
        ))

    # 3. trial_id format (TRL-YYYY-NNNN)
    trial_id = entry.get("trial_id", "")
    if not isinstance(trial_id, str):
        blockers.append(Blocker(
            "invalid_id_format",
            "model_assessment_spec_entry",
            "trial_id",
            "trial_id must be a string matching TRL-YYYY-NNNN format"
        ))
    elif not ID_PATTERN_TRL.match(trial_id):
        blockers.append(Blocker(
            "invalid_id_format",
            "model_assessment_spec_entry",
            "trial_id",
            f"trial_id '{trial_id}' does not match TRL-YYYY-NNNN format"
        ))

    # 4. search_space_id format (SSM-YYYY-NNNN)
    search_space_id = entry.get("search_space_id", "")
    if not isinstance(search_space_id, str):
        blockers.append(Blocker(
            "invalid_id_format",
            "model_assessment_spec_entry",
            "search_space_id",
            "search_space_id must be a string matching SSM-YYYY-NNNN format"
        ))
    elif not ID_PATTERN_SSM.match(search_space_id):
        blockers.append(Blocker(
            "invalid_id_format",
            "model_assessment_spec_entry",
            "search_space_id",
            f"search_space_id '{search_space_id}' does not match SSM-YYYY-NNNN format"
        ))

    # 4b. hypothesis_id format (HYP-YYYY-NNNN)
    hypothesis_id = entry.get("hypothesis_id", "")
    if not isinstance(hypothesis_id, str):
        blockers.append(Blocker(
            "invalid_id_format",
            "model_assessment_spec_entry",
            "hypothesis_id",
            "hypothesis_id must be a string matching HYP-YYYY-NNNN format"
        ))
    elif not ID_PATTERN_HYP.match(hypothesis_id):
        blockers.append(Blocker(
            "invalid_id_format",
            "model_assessment_spec_entry",
            "hypothesis_id",
            f"hypothesis_id '{hypothesis_id}' does not match HYP-YYYY-NNNN format"
        ))

    # 5. assessment_status enum
    assessment_status = entry.get("assessment_status", "")
    if not isinstance(assessment_status, str):
        blockers.append(Blocker(
            "invalid_enum",
            "model_assessment_spec_entry",
            "assessment_status",
            f"assessment_status must be a string, got {type(assessment_status).__name__}"
        ))
    elif assessment_status and assessment_status not in ASSESSMENT_STATUSES:
        blockers.append(Blocker(
            "invalid_enum",
            "model_assessment_spec_entry",
            "assessment_status",
            f"assessment_status '{assessment_status}' not in allowed set"
        ))

    # 6. metrics — must be an object
    metrics = entry.get("metrics")
    if metrics is not None and not isinstance(metrics, dict):
        blockers.append(Blocker(
            "invalid_object",
            "model_assessment_spec_entry",
            "metrics",
            "metrics must be an object"
        ))

    # 7. required_checks — must be an object
    required_checks = entry.get("required_checks")
    if required_checks is not None and not isinstance(required_checks, dict):
        blockers.append(Blocker(
            "invalid_object",
            "model_assessment_spec_entry",
            "required_checks",
            "required_checks must be an object"
        ))
        return blockers  # cannot validate sub-fields if not an object

    # 8. required_checks fields — all must be present and be booleans
    if required_checks is not None:
        for field in REQUIRED_CHECKS_FIELDS:
            val = required_checks.get(field)
            if val is None:
                blockers.append(Blocker(
                    "missing_required_check",
                    "model_assessment_spec_entry",
                    f"required_checks.{field}",
                    f"required_checks.{field} is required"
                ))
            elif not isinstance(val, bool):
                blockers.append(Blocker(
                    "invalid_boolean",
                    "model_assessment_spec_entry",
                    f"required_checks.{field}",
                    f"required_checks.{field} must be a boolean"
                ))

    # 9. reviewer — must be an object
    reviewer = entry.get("reviewer")
    if reviewer is not None and not isinstance(reviewer, dict):
        blockers.append(Blocker(
            "invalid_object",
            "model_assessment_spec_entry",
            "reviewer",
            "reviewer must be an object"
        ))

    # 10. General metric validation
    if metrics is not None and isinstance(metrics, dict):
        # sample_size: integer > 0, bool rejected
        sample_size = metrics.get("sample_size")
        if sample_size is not None:
            if isinstance(sample_size, bool):
                blockers.append(Blocker(
                    "invalid_metric",
                    "model_assessment_spec_entry",
                    "metrics.sample_size",
                    "sample_size must be an integer > 0, not a boolean"
                ))
            elif not isinstance(sample_size, int) or sample_size <= 0:
                blockers.append(Blocker(
                    "invalid_metric",
                    "model_assessment_spec_entry",
                    "metrics.sample_size",
                    "sample_size must be an integer > 0"
                ))

        # pbo: number in [0, 1], bool rejected
        pbo = metrics.get("pbo")
        if pbo is not None:
            if isinstance(pbo, bool):
                blockers.append(Blocker(
                    "invalid_metric",
                    "model_assessment_spec_entry",
                    "metrics.pbo",
                    "pbo must be a number between 0 and 1 inclusive, not a boolean"
                ))
            elif not isinstance(pbo, (int, float)) or pbo < 0 or pbo > 1:
                blockers.append(Blocker(
                    "invalid_metric",
                    "model_assessment_spec_entry",
                    "metrics.pbo",
                    "pbo must be a number between 0 and 1 inclusive"
                ))

        # dsr: number, bool rejected
        dsr = metrics.get("dsr")
        if dsr is not None:
            if isinstance(dsr, bool):
                blockers.append(Blocker(
                    "invalid_metric",
                    "model_assessment_spec_entry",
                    "metrics.dsr",
                    "dsr must be a number, not a boolean"
                ))
            elif not isinstance(dsr, (int, float)):
                blockers.append(Blocker(
                    "invalid_metric",
                    "model_assessment_spec_entry",
                    "metrics.dsr",
                    "dsr must be a number"
                ))

    # 11. Governance rule: accepted requires all required_checks == true
    if assessment_status == "accepted":
        if required_checks is not None and isinstance(required_checks, dict):
            for field in REQUIRED_CHECKS_FIELDS:
                val = required_checks.get(field)
                if val is not True:
                    blockers.append(Blocker(
                        "accepted_without_required_evidence",
                        "model_assessment_spec_entry",
                        f"required_checks.{field}",
                        f"assessment_status=accepted requires required_checks.{field} == true"
                    ))

        # accepted also requires metrics contain sample_size, pbo, dsr
        if metrics is None or not isinstance(metrics, dict):
            blockers.append(Blocker(
                "accepted_without_required_evidence",
                "model_assessment_spec_entry",
                "metrics",
                "assessment_status=accepted requires metrics to be present"
            ))
        else:
            for metric_name in ("sample_size", "pbo", "dsr"):
                if metric_name not in metrics:
                    blockers.append(Blocker(
                        "accepted_without_required_evidence",
                        "model_assessment_spec_entry",
                        f"metrics.{metric_name}",
                        f"assessment_status=accepted requires metrics.{metric_name}"
                    ))

    return blockers


def main():
    args = parse_args()
    path = Path(args.file_path)

    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(2)

    try:
        with path.open() as f:
            entry = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in {path}: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    blockers = validate(entry)

    if args.format == "json":
        out = {
            "file": str(path),
            "blockers_count": len(blockers),
            "blockers": [b.to_dict() for b in blockers],
        }
        print(json.dumps(out, indent=2))
    else:
        print(f"file: {path}")
        print(f"blockers_count: {len(blockers)}")
        if blockers:
            for b in blockers:
                print(f"  [{b.code}] {b.object_type}.{b.field}: {b.message}")

    if blockers:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
