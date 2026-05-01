#!/usr/bin/env python3
"""
Local ExperimentSpec v1 validator.
Validates one or more ExperimentSpec JSON files against the v1 schema and governance rules.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Any, List

# ID patterns
ID_PATTERN_EXP = re.compile(r"^EXP-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_HYP = re.compile(r"^HYP-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_SSM = re.compile(r"^SSM-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_MAS = re.compile(r"^MAS-[0-9]{4}-[0-9]{4}$")

# Enums
STUDY_TYPES = {
    "event_study",
    "calendar_seasonality",
    "regime_conditioned_signal",
    "cross_sectional_ranking",
    "time_series_momentum",
    "literature_replication",
    "options_event_risk",
    "custom",
}
TRIAL_GENERATION_MODES = {
    "manual_grid",
    "fixed_sweep",
    "literature_replication",
    "ablation",
    "falsification",
    "exploratory_agent_assisted",
}
# allowed_trial_lanes must use TrialLedger source_lane taxonomy — NOT generation-mode values
SOURCE_LANE_TAXONOMY = {
    "theory_first",
    "exploratory_anomaly",
    "post_hoc_theory",
    "confirmatory",
}

# Governance stop-rule fields — must be absent or false in prohibited_modes
GOVERNANCE_STOP_RULE_FIELDS = [
    "autonomous_search",
    "bayesian_optimization",
    "genetic_programming",
    "automated_promotion",
    "automated_registry_mutation",
    "live_trading",
    "production_execution",
    "gcru_integration",
]

# Required top-level fields
REQUIRED_TOP_LEVEL = [
    "experiment_id",
    "experiment_version",
    "hypothesis_id",
    "search_space_id",
    "data_manifest_refs",
    "study_type",
    "decision_timestamp_policy",
    "feature_cutoff_policy",
    "trial_generation_mode",
    "allowed_trial_lanes",
    "prohibited_modes",
    "created_at",
    "reviewer",
]

# Pre-earnings-specific fields blocked by domain-neutrality rule
PREEARNS_FIELDS = [
    "earnings_date",
    "event_session",
    "amc_bmo_indicator",
    "entry_dpe",
    "exit_dpe",
    "delta_target",
    "expiry_rank",
    "iv_crush",
    "gap_exposure",
]


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
        description="Validate one or more ExperimentSpec v1 JSON files."
    )
    p.add_argument(
        "files",
        nargs="+",
        help="Path to one or more ExperimentSpec JSON files.",
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
    Validate a single ExperimentSpec record (already parsed from JSON).
    Returns a list of Blocker objects.
    """
    blockers: List[Blocker] = []

    # 0. Root must be an object
    if not isinstance(entry, dict):
        blockers.append(Blocker(
            "invalid_object",
            "experiment_spec",
            "$",
            "ExperimentSpec must be a JSON object"
        ))
        return blockers

    # 1. Required top-level fields — missing, null, or empty/whitespace-only string fails
    for field in REQUIRED_TOP_LEVEL:
        val = entry.get(field)
        if val is None:
            blockers.append(Blocker(
                "missing_required_field",
                "experiment_spec",
                field,
                f"{field} is required"
            ))
        elif isinstance(val, str) and val.strip() == "":
            blockers.append(Blocker(
                "missing_required_field",
                "experiment_spec",
                field,
                f"{field} is required and cannot be empty"
            ))

    # Cannot safely continue if required fields are missing
    if blockers:
        return blockers

    # 2. experiment_id format
    exp_id = entry.get("experiment_id", "")
    if not isinstance(exp_id, str):
        blockers.append(Blocker(
            "invalid_id_format",
            "experiment_spec",
            "experiment_id",
            "experiment_id must be a string"
        ))
    elif not ID_PATTERN_EXP.match(exp_id):
        blockers.append(Blocker(
            "invalid_id_format",
            "experiment_spec",
            "experiment_id",
            f"experiment_id '{exp_id}' does not match EXP-YYYY-NNNN format"
        ))

    # 3. hypothesis_id format
    hyp_id = entry.get("hypothesis_id", "")
    if not isinstance(hyp_id, str):
        blockers.append(Blocker(
            "invalid_id_format",
            "experiment_spec",
            "hypothesis_id",
            "hypothesis_id must be a string"
        ))
    elif not ID_PATTERN_HYP.match(hyp_id):
        blockers.append(Blocker(
            "invalid_id_format",
            "experiment_spec",
            "hypothesis_id",
            f"hypothesis_id '{hyp_id}' does not match HYP-YYYY-NNNN format"
        ))

    # 4. search_space_id format
    ssm_id = entry.get("search_space_id", "")
    if not isinstance(ssm_id, str):
        blockers.append(Blocker(
            "invalid_id_format",
            "experiment_spec",
            "search_space_id",
            "search_space_id must be a string"
        ))
    elif not ID_PATTERN_SSM.match(ssm_id):
        blockers.append(Blocker(
            "invalid_id_format",
            "experiment_spec",
            "search_space_id",
            f"search_space_id '{ssm_id}' does not match SSM-YYYY-NNNN format"
        ))

    # 5. model_assessment_ref format (optional field, but if present must be valid)
    mas_id = entry.get("model_assessment_ref")
    if mas_id is not None:
        if not isinstance(mas_id, str):
            blockers.append(Blocker(
                "invalid_ref_type",
                "experiment_spec",
                "model_assessment_ref",
                "model_assessment_ref must be a string"
            ))
        elif not ID_PATTERN_MAS.match(mas_id):
            blockers.append(Blocker(
                "invalid_ref_format",
                "experiment_spec",
                "model_assessment_ref",
                f"model_assessment_ref '{mas_id}' does not match MAS-YYYY-NNNN format"
            ))

    # 6. experiment_version must be a positive integer
    ver = entry.get("experiment_version")
    if ver is not None:
        if not isinstance(ver, int) or isinstance(ver, bool):
            blockers.append(Blocker(
                "invalid_type",
                "experiment_spec",
                "experiment_version",
                f"experiment_version must be an integer, got {type(ver).__name__}"
            ))
        elif ver < 1:
            blockers.append(Blocker(
                "invalid_value",
                "experiment_spec",
                "experiment_version",
                f"experiment_version must be >= 1, got {ver}"
            ))

    # 7. study_type enum
    study_type = entry.get("study_type")
    if study_type is not None:
        if not isinstance(study_type, str):
            blockers.append(Blocker(
                "invalid_enum",
                "experiment_spec",
                "study_type",
                f"study_type must be a string, got {type(study_type).__name__}"
            ))
        elif study_type not in STUDY_TYPES:
            blockers.append(Blocker(
                "invalid_enum",
                "experiment_spec",
                "study_type",
                f"study_type '{study_type}' not in allowed set"
            ))

    # 8. trial_generation_mode enum
    tgm = entry.get("trial_generation_mode")
    if tgm is not None:
        if not isinstance(tgm, str):
            blockers.append(Blocker(
                "invalid_enum",
                "experiment_spec",
                "trial_generation_mode",
                f"trial_generation_mode must be a string, got {type(tgm).__name__}"
            ))
        elif tgm not in TRIAL_GENERATION_MODES:
            blockers.append(Blocker(
                "invalid_enum",
                "experiment_spec",
                "trial_generation_mode",
                f"trial_generation_mode '{tgm}' not in allowed set"
            ))

    # 9. data_manifest_refs must be a non-empty list of strings
    dmr = entry.get("data_manifest_refs")
    if dmr is not None:
        if not isinstance(dmr, list):
            blockers.append(Blocker(
                "invalid_list",
                "experiment_spec",
                "data_manifest_refs",
                f"data_manifest_refs must be a list, got {type(dmr).__name__}"
            ))
        elif len(dmr) == 0:
            blockers.append(Blocker(
                "invalid_list",
                "experiment_spec",
                "data_manifest_refs",
                "data_manifest_refs must be non-empty"
            ))
        else:
            for i, item in enumerate(dmr):
                if not isinstance(item, str):
                    blockers.append(Blocker(
                        "invalid_ref_type",
                        "experiment_spec",
                        f"data_manifest_refs[{i}]",
                        f"data_manifest_refs items must be strings, got {type(item).__name__}"
                    ))

    # 10. decision_timestamp_policy must be an object
    dtp = entry.get("decision_timestamp_policy")
    if dtp is not None and not isinstance(dtp, dict):
        blockers.append(Blocker(
            "invalid_object",
            "experiment_spec",
            "decision_timestamp_policy",
            f"decision_timestamp_policy must be an object, got {type(dtp).__name__}"
        ))

    # 11. feature_cutoff_policy must be an object
    fcp = entry.get("feature_cutoff_policy")
    if fcp is not None and not isinstance(fcp, dict):
        blockers.append(Blocker(
            "invalid_object",
            "experiment_spec",
            "feature_cutoff_policy",
            f"feature_cutoff_policy must be an object, got {type(fcp).__name__}"
        ))

    # 12. allowed_trial_lanes must be a non-empty list of strings from source_lane taxonomy
    atl = entry.get("allowed_trial_lanes")
    if atl is not None:
        if not isinstance(atl, list):
            blockers.append(Blocker(
                "invalid_list",
                "experiment_spec",
                "allowed_trial_lanes",
                f"allowed_trial_lanes must be a list, got {type(atl).__name__}"
            ))
        elif len(atl) == 0:
            blockers.append(Blocker(
                "invalid_list",
                "experiment_spec",
                "allowed_trial_lanes",
                "allowed_trial_lanes must be non-empty"
            ))
        else:
            for i, item in enumerate(atl):
                if not isinstance(item, str):
                    blockers.append(Blocker(
                        "invalid_lane_type",
                        "experiment_spec",
                        f"allowed_trial_lanes[{i}]",
                        f"allowed_trial_lanes items must be strings, got {type(item).__name__}"
                    ))
                elif item not in SOURCE_LANE_TAXONOMY:
                    blockers.append(Blocker(
                        "invalid_trial_lane",
                        "experiment_spec",
                        f"allowed_trial_lanes[{i}]",
                        f"allowed_trial_lanes item '{item}' is not in TrialLedger source_lane "
                        f"taxonomy (theory_first, exploratory_anomaly, post_hoc_theory, confirmatory)"
                    ))

    # 13. prohibited_modes must be an object; each governance field must be absent or false
    pm = entry.get("prohibited_modes")
    if pm is not None:
        if not isinstance(pm, dict):
            blockers.append(Blocker(
                "invalid_object",
                "experiment_spec",
                "prohibited_modes",
                f"prohibited_modes must be an object, got {type(pm).__name__}"
            ))
        else:
            for field in GOVERNANCE_STOP_RULE_FIELDS:
                val = pm.get(field)
                if val is not None and val is not False:
                    blockers.append(Blocker(
                        "forbidden_governance_field",
                        "experiment_spec",
                        f"prohibited_modes.{field}",
                        f"prohibited_modes.{field} must be absent or false"
                    ))

    # 14. reviewer must be an object
    reviewer = entry.get("reviewer")
    if reviewer is not None and not isinstance(reviewer, dict):
        blockers.append(Blocker(
            "invalid_object",
            "experiment_spec",
            "reviewer",
            f"reviewer must be an object, got {type(reviewer).__name__}"
        ))

    # 15. Domain-neutrality: block pre-earnings-specific fields at top level
    for field in PREEARNS_FIELDS:
        if field in entry:
            blockers.append(Blocker(
                "domain_neutrality_violation",
                "experiment_spec",
                field,
                f"'{field}' is a pre-earnings-specific field and must not appear in "
                f"ExperimentSpec; ExperimentSpec is domain-neutral"
            ))

    return blockers


def validate_file(path: Path) -> List[Blocker]:
    """
    Read a JSON file and validate the record.
    Returns a list of Blocker objects.
    """
    blockers: List[Blocker] = []

    # Read
    try:
        with path.open() as f:
            raw = f.read()
    except Exception as e:
        blockers.append(Blocker(
            "invalid_json",
            "experiment_spec_file",
            "$",
            f"Could not read file: {e}"
        ))
        return blockers

    # Parse
    try:
        entry = json.loads(raw)
    except json.JSONDecodeError as e:
        blockers.append(Blocker(
            "invalid_json",
            "experiment_spec_file",
            "$",
            f"Invalid JSON: {e}"
        ))
        return blockers

    record_blockers = validate_record(entry)
    for b in record_blockers:
        blockers.append(Blocker(
            b.code, b.object_type,
            f"{path}:{b.field}",
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

    # Exit 2 for usage/read/parse errors, exit 1 for blockers, exit 0 for valid
    parse_errors = [b for b in all_blockers if b.code == "invalid_json"]
    sys.exit(2 if parse_errors else (1 if has_blockers else 0))


if __name__ == "__main__":
    main()
