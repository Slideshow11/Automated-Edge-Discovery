#!/usr/bin/env python3
"""
Local TrialLedger v1 validator.
Validates a single TrialLedger JSON entry against the v1 schema and governance rules.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Any, List

# Constants
SOURCE_LANES = {"theory_first", "exploratory_anomaly", "post_hoc_theory", "confirmatory"}
PROMOTION_STATUSES = {"raw_result", "reviewed", "rejected", "provisional", "accepted", "killed", "promoted_to_confirmatory"}
TRIAL_STATUSES = {"planned", "running", "completed", "failed", "abandoned", "invalidated"}

ID_PATTERN_TRL = re.compile(r"^TRL-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_SSM = re.compile(r"^SSM-[0-9]{4}-[0-9]{4}$")

REQUIRED_TOP_LEVEL = [
    "trial_id",
    "search_space_id",
    "source_lane",
    "promotion_status",
    "status",
    "hypothesis_id",
    "data_scope",
    "execution_scope",
    "results",
]

DATA_SCOPE_REQUIRED = ["dataset_id"]


class Blocker:
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
    p = argparse.ArgumentParser(description="Validate a TrialLedger v1 JSON entry.")
    p.add_argument("file_path", help="Path to TrialLedger JSON entry file.")
    p.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="Output format (default: text)"
    )
    return p.parse_args()


def validate(entry: Dict[str, Any]) -> List[Blocker]:
    blockers: List[Blocker] = []

    # 1. Required top-level fields
    for field in REQUIRED_TOP_LEVEL:
        if field not in entry or entry.get(field) is None or entry.get(field) == "":
            blockers.append(Blocker(
                "missing_required_field",
                "trial_ledger_entry",
                field,
                field + " is required"
            ))

    if blockers:
        return blockers  # cannot continue if required fields missing

    # 2. trial_id format
    trial_id = entry.get("trial_id", "")
    if not ID_PATTERN_TRL.match(trial_id):
        blockers.append(Blocker(
            "invalid_id_format",
            "trial_ledger_entry",
            "trial_id",
            "trial_id " + repr(trial_id) + " does not match TRL-YYYY-NNNN format"
        ))

    # 3. search_space_id format
    ssm_id = entry.get("search_space_id", "")
    if not ID_PATTERN_SSM.match(ssm_id):
        blockers.append(Blocker(
            "invalid_id_format",
            "trial_ledger_entry",
            "search_space_id",
            "search_space_id " + repr(ssm_id) + " does not match SSM-YYYY-NNNN format"
        ))

    # 4. source_lane enum
    source_lane = entry.get("source_lane", "")
    if source_lane and source_lane not in SOURCE_LANES:
        blockers.append(Blocker(
            "invalid_enum",
            "trial_ledger_entry",
            "source_lane",
            "source_lane " + repr(source_lane) + " not in allowed set"
        ))

    # 5. promotion_status enum
    prom_status = entry.get("promotion_status", "")
    if prom_status and prom_status not in PROMOTION_STATUSES:
        blockers.append(Blocker(
            "invalid_enum",
            "trial_ledger_entry",
            "promotion_status",
            "promotion_status " + repr(prom_status) + " not in allowed set"
        ))

    # 6. status enum
    status = entry.get("status", "")
    if status and status not in TRIAL_STATUSES:
        blockers.append(Blocker(
            "invalid_enum",
            "trial_ledger_entry",
            "status",
            "status " + repr(status) + " not in allowed set"
        ))

    # 7. data_scope required fields
    ds = entry.get("data_scope")
    if ds is None:
        blockers.append(Blocker(
            "missing_required_field",
            "data_scope",
            "data_scope",
            "data_scope is required"
        ))
    elif isinstance(ds, dict):
        for field in DATA_SCOPE_REQUIRED:
            if not ds.get(field):
                blockers.append(Blocker(
                    "missing_required_field",
                    "data_scope",
                    field,
                    field + " is required within data_scope"
                ))

    # 8. execution_scope required (presence check)
    es = entry.get("execution_scope")
    if es is None:
        blockers.append(Blocker(
            "missing_required_field",
            "execution_scope",
            "execution_scope",
            "execution_scope is required"
        ))

    # 9. results required (presence check)
    res = entry.get("results")
    if res is None:
        blockers.append(Blocker(
            "missing_required_field",
            "results",
            "results",
            "results is required"
        ))

    # 10. confirmatory_trial_id format if present
    ctri = entry.get("confirmatory_trial_id")
    if ctri and not ID_PATTERN_TRL.match(ctri):
        blockers.append(Blocker(
            "invalid_id_format",
            "trial_ledger_entry",
            "confirmatory_trial_id",
            "confirmatory_trial_id " + repr(ctri) + " does not match TRL-YYYY-NNNN format"
        ))

    # 11. Governance rule: accepted requires confirmatory link
    if prom_status == "accepted":
        ctri = entry.get("confirmatory_trial_id")
        clane = entry.get("confirmatory_source_lane")
        cscope = entry.get("confirmatory_data_scope")
        missing_links: List[str] = []
        if not ctri:
            missing_links.append("confirmatory_trial_id")
        if not clane:
            missing_links.append("confirmatory_source_lane")
        if not cscope:
            missing_links.append("confirmatory_data_scope")
        if missing_links:
            links_str = ", ".join(missing_links)
            blockers.append(Blocker(
                "missing_confirmatory_link",
                "trial_ledger_entry",
                links_str,
                "promotion_status=accepted requires: " + links_str
            ))
        else:
            if clane != "confirmatory":
                blockers.append(Blocker(
                    "invalid_confirmatory_link",
                    "trial_ledger_entry",
                    "confirmatory_source_lane",
                    "confirmatory_source_lane must be 'confirmatory', got " + repr(clane)
                ))
            # Governance rule: confirmatory_data_scope must not be identical to data_scope
            if isinstance(cscope, dict) and isinstance(ds, dict):
                if cscope == ds:
                    blockers.append(Blocker(
                        "confirmatory_data_scope_reused",
                        "trial_ledger_entry",
                        "confirmatory_data_scope",
                        "confirmatory_data_scope must not be identical to data_scope"
                    ))

    return blockers


def main():
    args = parse_args()
    path = Path(args.file_path)

    if not path.exists():
        print("ERROR: file not found: " + str(path), file=sys.stderr)
        sys.exit(2)

    try:
        with path.open() as f:
            entry = json.load(f)
    except json.JSONDecodeError as e:
        print("ERROR: invalid JSON in " + str(path) + ": " + str(e), file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print("ERROR: " + str(e), file=sys.stderr)
        sys.exit(2)

    blockers = validate(entry)

    if args.format == "json":
        out = {
            "file": str(path),
            "blockers": [b.to_dict() for b in blockers],
        }
        print(json.dumps(out, indent=2))
    else:
        print("file: " + str(path))
        print("blockers_count: " + str(len(blockers)))
        if blockers:
            print("Blockers:")
            for b in blockers:
                print("  - " + b.code + " | " + b.object_type + " | " + b.field + " | " + b.message)

    if blockers:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
