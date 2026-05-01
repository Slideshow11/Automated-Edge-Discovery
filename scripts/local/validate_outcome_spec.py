#!/usr/bin/env python3
"""
Local OutcomeSpec v1 validator.
Validates one or more OutcomeSpec JSON files against the v1 schema and governance rules.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Any, List

# ID patterns
ID_PATTERN_OUT = re.compile(r"^OUT-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_MAS = re.compile(r"^MAS-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_TRL = re.compile(r"^TRL-[0-9]{4}-[0-9]{4}$")

# Enums
METRIC_DIRECTIONS = {"maximize", "minimize", "target_range"}
WINDOW_START_POLICIES = {"absolute_date", "relative_event", "data_start", "lookback_start"}
WINDOW_END_POLICIES = {"absolute_date", "relative_event", "data_end", "fixed_horizon"}
WINDOW_ROLES = {"in_sample", "validation", "out_of_sample", "pseudo_live", "live", "holdout", "stress"}
LABELING_SCHEMES = {
    "forward_return", "event_window_return", "drawdown", "volatility",
    "hit_rate", "sharpe_like", "information_ratio_like", "option_pnl", "custom"
}
RETURN_BASES = {"simple_return", "log_return", "excess_return", "pnl", "risk_adjusted_return", "custom"}
BENCHMARK_POLICIES = {"none", "static_benchmark", "dynamic_benchmark", "matched_universe", "factor_model", "custom"}
WINDOW_UNITS = {"days", "observations", "periods"}
EMBARGO_UNITS = {"fraction", "days", "observations"}

# Required top-level fields
REQUIRED_TOP_LEVEL = [
    "outcome_spec_id",
    "outcome_version",
    "outcome_family",
    "metric_name",
    "metric_direction",
    "outcome_window",
    "window_start_policy",
    "window_end_policy",
    "window_role",
    "labeling_scheme",
    "return_basis",
    "benchmark_policy",
    "observation_count_policy",
    "evidence_role_requirements",
    "purge_embargo_policy",
    "created_at",
    "reviewer",
]

# Computed-assessment/search-pressure fields blocked at top level
COMPUTED_ASSESSMENT_FIELDS = [
    "pbo_estimate",
    "dsr_estimate",
    "backtest_pnl_haircut",
    "overfit_discount_factor",
    "adjusted_expected_oos_sharpe",
    "probability_of_loss",
    "false_discovery_rate_estimate",
    "strategy_complexity_score",
    "factor_exposure_stability_check",
    "null_model_performance",
    "performance_vs_null",
    "selected_variant_id",
    "n_tried",
    "trial_family_id",
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
        description="Validate one or more OutcomeSpec v1 JSON files."
    )
    p.add_argument(
        "files",
        nargs="+",
        help="Path to one or more OutcomeSpec JSON files.",
    )
    p.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text)",
    )
    return p.parse_args()


def _check_object(entry: Dict[str, Any], field: str, blockers: List[Blocker],
                  object_type: str = "outcome_spec") -> Any:
    """Check that field is present and is an object (not None). Returns the value or None."""
    val = entry.get(field)
    if val is None:
        blockers.append(Blocker(
            "missing_required_field",
            object_type,
            field,
            f"{field} is required"
        ))
        return None
    if not isinstance(val, dict):
        blockers.append(Blocker(
            "invalid_object",
            object_type,
            field,
            f"{field} must be an object, got {type(val).__name__}"
        ))
        return None
    return val


def _check_boolean(entry: Dict[str, Any], field: str, blockers: List[Blocker],
                   object_type: str = "outcome_spec") -> None:
    """Check that field, if present, is a strict boolean."""
    val = entry.get(field)
    if val is not None and not isinstance(val, bool):
        blockers.append(Blocker(
            "invalid_boolean",
            object_type,
            field,
            f"{field} must be a boolean, got {type(val).__name__}"
        ))


def _check_string(entry: Dict[str, Any], field: str, blockers: List[Blocker],
                  object_type: str = "outcome_spec", required: bool = False) -> None:
    """Check that field, if present or required, is a non-empty string."""
    val = entry.get(field)
    if val is None:
        if required:
            blockers.append(Blocker(
                "missing_required_field",
                object_type,
                field,
                f"{field} is required"
            ))
        return
    if not isinstance(val, str):
        blockers.append(Blocker(
            "invalid_type",
            object_type,
            field,
            f"{field} must be a string, got {type(val).__name__}"
        ))
        return
    if required and val.strip() == "":
        blockers.append(Blocker(
            "missing_required_field",
            object_type,
            field,
            f"{field} is required and cannot be empty"
        ))


def _check_enum(entry: Dict[str, Any], field: str, allowed: set,
                blockers: List[Blocker], object_type: str = "outcome_spec") -> None:
    """Check that field is a string and in the allowed enum set."""
    val = entry.get(field)
    if val is None:
        return
    if not isinstance(val, str):
        blockers.append(Blocker(
            "invalid_enum",
            object_type,
            field,
            f"{field} must be a string, got {type(val).__name__}"
        ))
        return
    if val not in allowed:
        blockers.append(Blocker(
            "invalid_enum",
            object_type,
            field,
            f"{field} '{val}' not in allowed set"
        ))


def validate_record(entry: Dict[str, Any]) -> List[Blocker]:
    """
    Validate a single OutcomeSpec record (already parsed from JSON).
    Returns a list of Blocker objects.
    """
    blockers: List[Blocker] = []

    # 0. Root must be an object
    if not isinstance(entry, dict):
        blockers.append(Blocker(
            "invalid_object",
            "outcome_spec",
            "$",
            "OutcomeSpec must be a JSON object"
        ))
        return blockers

    # 1. Required top-level fields — missing, null, or empty/whitespace-only string fails
    for field in REQUIRED_TOP_LEVEL:
        val = entry.get(field)
        if val is None:
            blockers.append(Blocker(
                "missing_required_field",
                "outcome_spec",
                field,
                f"{field} is required"
            ))
        elif isinstance(val, str) and val.strip() == "":
            blockers.append(Blocker(
                "missing_required_field",
                "outcome_spec",
                field,
                f"{field} is required and cannot be empty"
            ))

    # Cannot safely continue if required fields are missing
    if blockers:
        return blockers

    # 2. outcome_spec_id format
    out_id = entry.get("outcome_spec_id", "")
    if not isinstance(out_id, str):
        blockers.append(Blocker(
            "invalid_id_format",
            "outcome_spec",
            "outcome_spec_id",
            "outcome_spec_id must be a string"
        ))
    elif not ID_PATTERN_OUT.match(out_id):
        blockers.append(Blocker(
            "invalid_id_format",
            "outcome_spec",
            "outcome_spec_id",
            f"outcome_spec_id '{out_id}' does not match OUT-YYYY-NNNN format"
        ))

    # 3. outcome_version — must be integer >= 1, not boolean
    ver = entry.get("outcome_version")
    if ver is not None:
        if isinstance(ver, bool) or not isinstance(ver, int):
            blockers.append(Blocker(
                "invalid_type",
                "outcome_spec",
                "outcome_version",
                f"outcome_version must be an integer, got {type(ver).__name__}"
            ))
        elif ver < 1:
            blockers.append(Blocker(
                "invalid_value",
                "outcome_spec",
                "outcome_version",
                f"outcome_version must be >= 1, got {ver}"
            ))

    # 4. outcome_family and metric_name — must be non-empty strings
    for field in ("outcome_family", "metric_name"):
        val = entry.get(field)
        if val is not None and not isinstance(val, str):
            blockers.append(Blocker(
                "invalid_type",
                "outcome_spec",
                field,
                f"{field} must be a string, got {type(val).__name__}"
            ))
        elif val is not None and val.strip() == "":
            blockers.append(Blocker(
                "missing_required_field",
                "outcome_spec",
                field,
                f"{field} cannot be empty"
            ))

    # 5. metric_direction enum
    _check_enum(entry, "metric_direction", METRIC_DIRECTIONS, blockers)

    # 6. outcome_window must be an object
    ow = entry.get("outcome_window")
    if ow is not None and isinstance(ow, dict):
        # 6a. Required nested fields in outcome_window
        for field in ("anchor",):
            val = ow.get(field)
            if val is None:
                blockers.append(Blocker(
                    "missing_required_field",
                    "outcome_spec",
                    f"outcome_window.{field}",
                    f"outcome_window.{field} is required"
                ))
            elif isinstance(val, str) and val.strip() == "":
                blockers.append(Blocker(
                    "missing_required_field",
                    "outcome_spec",
                    f"outcome_window.{field}",
                    f"outcome_window.{field} cannot be empty"
                ))
            elif val is not None and not isinstance(val, str):
                blockers.append(Blocker(
                    "invalid_type",
                    "outcome_spec",
                    f"outcome_window.{field}",
                    f"outcome_window.{field} must be a string, got {type(val).__name__}"
                ))

        for field in ("window_start_days", "window_end_days"):
            val = ow.get(field)
            if val is not None:
                if isinstance(val, bool) or not isinstance(val, int):
                    blockers.append(Blocker(
                        "invalid_type",
                        "outcome_spec",
                        f"outcome_window.{field}",
                        f"outcome_window.{field} must be an integer, got {type(val).__name__}"
                    ))

        # window_unit enum
        wu_val = ow.get("window_unit")
        if wu_val is not None:
            if not isinstance(wu_val, str):
                blockers.append(Blocker(
                    "invalid_enum",
                    "outcome_spec",
                    "outcome_window.window_unit",
                    f"outcome_window.window_unit must be a string, got {type(wu_val).__name__}"
                ))
            elif wu_val == "hours":
                blockers.append(Blocker(
                    "invalid_enum",
                    "outcome_spec",
                    "outcome_window.window_unit",
                    "outcome_window.window_unit 'hours' is not allowed"
                ))
            elif wu_val not in WINDOW_UNITS:
                blockers.append(Blocker(
                    "invalid_enum",
                    "outcome_spec",
                    "outcome_window.window_unit",
                    f"outcome_window.window_unit '{wu_val}' not in allowed set"
                ))

        # Block legacy/wrong keys
        for legacy in ("start_offset", "end_offset"):
            if legacy in ow:
                blockers.append(Blocker(
                    "invalid_field",
                    "outcome_spec",
                    f"outcome_window.{legacy}",
                    f"outcome_window.{legacy} is not allowed; use window_start_days/window_end_days"
                ))

    # 7. window_start_policy and window_end_policy enums
    _check_enum(entry, "window_start_policy", WINDOW_START_POLICIES, blockers)
    _check_enum(entry, "window_end_policy", WINDOW_END_POLICIES, blockers)

    # 8. window_role enum
    _check_enum(entry, "window_role", WINDOW_ROLES, blockers)

    # 9. labeling_scheme enum
    _check_enum(entry, "labeling_scheme", LABELING_SCHEMES, blockers)

    # 10. return_basis enum
    _check_enum(entry, "return_basis", RETURN_BASES, blockers)

    # 11. benchmark_policy enum
    _check_enum(entry, "benchmark_policy", BENCHMARK_POLICIES, blockers)

    # 12. observation_count_policy — must be an object
    ocp = entry.get("observation_count_policy")
    if ocp is not None and isinstance(ocp, dict):
        val = ocp.get("min_observations")
        if val is not None:
            if isinstance(val, bool) or not isinstance(val, int):
                blockers.append(Blocker(
                    "invalid_type",
                    "outcome_spec",
                    "observation_count_policy.min_observations",
                    f"observation_count_policy.min_observations must be an integer, got {type(val).__name__}"
                ))
        val = ocp.get("max_observations")
        if val is not None:
            if isinstance(val, bool) or not isinstance(val, int):
                blockers.append(Blocker(
                    "invalid_type",
                    "outcome_spec",
                    "observation_count_policy.max_observations",
                    f"observation_count_policy.max_observations must be an integer, got {type(val).__name__}"
                ))
        val = ocp.get("max_overlap_fraction")
        if val is not None:
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                blockers.append(Blocker(
                    "invalid_type",
                    "outcome_spec",
                    "observation_count_policy.max_overlap_fraction",
                    f"observation_count_policy.max_overlap_fraction must be a number, got {type(val).__name__}"
                ))
            elif isinstance(val, (int, float)) and not isinstance(val, bool):
                if val < 0 or val > 1:
                    blockers.append(Blocker(
                        "invalid_value",
                        "outcome_spec",
                        "observation_count_policy.max_overlap_fraction",
                        f"observation_count_policy.max_overlap_fraction must be in [0, 1], got {val}"
                    ))
        _check_boolean(ocp, "requires_min_observations", blockers)
    elif ocp is not None:
        blockers.append(Blocker(
            "invalid_object",
            "outcome_spec",
            "observation_count_policy",
            f"observation_count_policy must be an object, got {type(ocp).__name__}"
        ))

    # 13. evidence_role_requirements — must be an object with all 7 required boolean fields
    err = entry.get("evidence_role_requirements")
    if err is not None and isinstance(err, dict):
        for field in (
            "requires_oos", "requires_live", "requires_uncertainty",
            "requires_benchmark", "requires_stress_period",
            "requires_purge_embargo", "requires_min_observations"
        ):
            val = err.get(field)
            if val is None:
                blockers.append(Blocker(
                    "missing_required_field",
                    "outcome_spec",
                    f"evidence_role_requirements.{field}",
                    f"evidence_role_requirements.{field} is required"
                ))
            elif not isinstance(val, bool):
                blockers.append(Blocker(
                    "invalid_boolean",
                    "outcome_spec",
                    f"evidence_role_requirements.{field}",
                    f"evidence_role_requirements.{field} must be a boolean, got {type(val).__name__}"
                ))
    elif err is not None:
        blockers.append(Blocker(
            "invalid_object",
            "outcome_spec",
            "evidence_role_requirements",
            f"evidence_role_requirements must be an object, got {type(err).__name__}"
        ))

    # 14. purge_embargo_policy — must be an object with required nested fields
    pep = entry.get("purge_embargo_policy")
    if pep is not None and isinstance(pep, dict):
        # purge_gap_days — integer >= 0
        pgd = pep.get("purge_gap_days")
        if pgd is not None:
            if isinstance(pgd, bool) or not isinstance(pgd, int):
                blockers.append(Blocker(
                    "invalid_type",
                    "outcome_spec",
                    "purge_embargo_policy.purge_gap_days",
                    f"purge_embargo_policy.purge_gap_days must be an integer, got {type(pgd).__name__}"
                ))
            elif pgd < 0:
                blockers.append(Blocker(
                    "invalid_value",
                    "outcome_spec",
                    "purge_embargo_policy.purge_gap_days",
                    f"purge_embargo_policy.purge_gap_days must be >= 0, got {pgd}"
                ))
        # embargo_fraction — number in [0, 1]
        ef = pep.get("embargo_fraction")
        if ef is not None:
            if isinstance(ef, bool) or not isinstance(ef, (int, float)):
                blockers.append(Blocker(
                    "invalid_type",
                    "outcome_spec",
                    "purge_embargo_policy.embargo_fraction",
                    f"purge_embargo_policy.embargo_fraction must be a number, got {type(ef).__name__}"
                ))
            elif not isinstance(ef, bool):
                if ef < 0 or ef > 1:
                    blockers.append(Blocker(
                        "invalid_value",
                        "outcome_spec",
                        "purge_embargo_policy.embargo_fraction",
                        f"purge_embargo_policy.embargo_fraction must be in [0, 1], got {ef}"
                    ))
        # embargo_units enum
        eu_val = pep.get("embargo_units")
        if eu_val is not None:
            if not isinstance(eu_val, str):
                blockers.append(Blocker(
                    "invalid_enum",
                    "outcome_spec",
                    "purge_embargo_policy.embargo_units",
                    f"purge_embargo_policy.embargo_units must be a string, got {type(eu_val).__name__}"
                ))
            elif eu_val not in EMBARGO_UNITS:
                blockers.append(Blocker(
                    "invalid_enum",
                    "outcome_spec",
                    "purge_embargo_policy.embargo_units",
                    f"purge_embargo_policy.embargo_units '{eu_val}' not in allowed set"
                ))
        # overlap_policy — string
        op_val = pep.get("overlap_policy")
        if op_val is not None and not isinstance(op_val, str):
            blockers.append(Blocker(
                "invalid_type",
                "outcome_spec",
                "purge_embargo_policy.overlap_policy",
                f"purge_embargo_policy.overlap_policy must be a string, got {type(op_val).__name__}"
            ))
    elif pep is not None:
        blockers.append(Blocker(
            "invalid_object",
            "outcome_spec",
            "purge_embargo_policy",
            f"purge_embargo_policy must be an object, got {type(pep).__name__}"
        ))

    # 15. model_assessment_refs — list of MAS-YYYY-NNNN strings
    mas_refs = entry.get("model_assessment_refs")
    if mas_refs is not None:
        if not isinstance(mas_refs, list):
            blockers.append(Blocker(
                "invalid_list",
                "outcome_spec",
                "model_assessment_refs",
                f"model_assessment_refs must be a list, got {type(mas_refs).__name__}"
            ))
        else:
            for i, item in enumerate(mas_refs):
                if not isinstance(item, str):
                    blockers.append(Blocker(
                        "invalid_ref_type",
                        "outcome_spec",
                        f"model_assessment_refs[{i}]",
                        f"model_assessment_refs items must be strings, got {type(item).__name__}"
                    ))
                elif not ID_PATTERN_MAS.match(item):
                    blockers.append(Blocker(
                        "invalid_ref_format",
                        "outcome_spec",
                        f"model_assessment_refs[{i}]",
                        f"model_assessment_refs item '{item}' does not match MAS-YYYY-NNNN format"
                    ))

    # 16. trial_ledger_refs — list of TRL-YYYY-NNNN strings
    trl_refs = entry.get("trial_ledger_refs")
    if trl_refs is not None:
        if not isinstance(trl_refs, list):
            blockers.append(Blocker(
                "invalid_list",
                "outcome_spec",
                "trial_ledger_refs",
                f"trial_ledger_refs must be a list, got {type(trl_refs).__name__}"
            ))
        else:
            for i, item in enumerate(trl_refs):
                if not isinstance(item, str):
                    blockers.append(Blocker(
                        "invalid_ref_type",
                        "outcome_spec",
                        f"trial_ledger_refs[{i}]",
                        f"trial_ledger_refs items must be strings, got {type(item).__name__}"
                    ))
                elif not ID_PATTERN_TRL.match(item):
                    blockers.append(Blocker(
                        "invalid_ref_format",
                        "outcome_spec",
                        f"trial_ledger_refs[{i}]",
                        f"trial_ledger_refs item '{item}' does not match TRL-YYYY-NNNN format"
                    ))

    # 17. runner_output_refs and review_packet_refs — list of strings (any format)
    for ref_field in ("runner_output_refs", "review_packet_refs"):
        ref_list = entry.get(ref_field)
        if ref_list is not None:
            if not isinstance(ref_list, list):
                blockers.append(Blocker(
                    "invalid_list",
                    "outcome_spec",
                    ref_field,
                    f"{ref_field} must be a list, got {type(ref_list).__name__}"
                ))
            else:
                for i, item in enumerate(ref_list):
                    if not isinstance(item, str):
                        blockers.append(Blocker(
                            "invalid_ref_type",
                            "outcome_spec",
                            f"{ref_field}[{i}]",
                            f"{ref_field} items must be strings, got {type(item).__name__}"
                        ))

    # 18. reviewer — must be an object
    reviewer = entry.get("reviewer")
    if reviewer is not None and not isinstance(reviewer, dict):
        blockers.append(Blocker(
            "invalid_object",
            "outcome_spec",
            "reviewer",
            f"reviewer must be an object, got {type(reviewer).__name__}"
        ))

    # 19. created_at — must be a non-empty string
    _check_string(entry, "created_at", blockers, required=True)

    # 20. extension_hooks — object if present
    ext = entry.get("extension_hooks")
    if ext is not None and not isinstance(ext, dict):
        blockers.append(Blocker(
            "invalid_object",
            "outcome_spec",
            "extension_hooks",
            f"extension_hooks must be an object, got {type(ext).__name__}"
        ))

    # 21. Computed-assessment/search-pressure fields — blocked at top level
    for field in COMPUTED_ASSESSMENT_FIELDS:
        if field in entry:
            blockers.append(Blocker(
                "computed_assessment_field",
                "outcome_spec",
                field,
                f"'{field}' is a computed-assessment/search-pressure field and must not appear "
                f"in OutcomeSpec; it belongs in ModelAssessmentSpec or runner output"
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
            "outcome_spec_file",
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
            "outcome_spec_file",
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
