#!/usr/bin/env python3
"""
Local InstrumentUniverseSpec v1 validator.
Validates one or more InstrumentUniverseSpec JSON files against the v1 schema and governance rules.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Any, List

# ID patterns
ID_PATTERN_IUS = re.compile(r"^IUS-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_IRL = re.compile(r"^IRL-[0-9]{4}-[0-9]{4}$")

# Enums
ASSET_CLASSES = {"equity", "etf", "option", "future", "fx", "crypto", "commodity", "rate", "index", "custom"}
UNIVERSE_CONSTRUCTION_POLICIES = {
    "static_list", "point_in_time_membership", "rolling_membership",
    "rule_based_filter", "external_index_membership", "custom"
}
MEMBERSHIP_TIMING_POLICIES = {
    "decision_time", "entry_time", "rebalance_time", "event_time", "fixed_snapshot", "custom"
}
SURVIVORSHIP_POLICIES = {
    "point_in_time", "current_constituents_only", "survivor_bias_allowed_for_smoke_test", "custom"
}
TRADABILITY_POLICIES = {
    "tradable_at_decision_time", "tradable_at_entry_time", "tradable_through_window", "custom"
}
CORPORATE_ACTION_POLICIES = {
    "adjusted", "raw", "split_adjusted", "dividend_adjusted", "total_return_adjusted", "custom"
}
RULE_OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "regex"}
RULE_TIMINGS = {"decision_time", "entry_time", "rebalance_time"}

# Required top-level fields
REQUIRED_TOP_LEVEL = [
    "instrument_universe_id",
    "universe_version",
    "universe_family",
    "asset_classes",
    "data_manifest_refs",
    "universe_construction_policy",
    "membership_timing_policy",
    "inclusion_rules",
    "exclusion_rules",
    "liquidity_policy",
    "survivorship_policy",
    "tradability_policy",
    "corporate_action_policy",
    "created_at",
    "reviewer",
]

# Computed-assessment/run-output fields blocked at top level
COMPUTED_ASSESSMENT_FIELDS = [
    "signals",
    "rankings",
    "factor_scores",
    "selected_variant_id",
    "n_tried",
    "trial_family_id",
    "pnl",
    "realized_returns",
    "pbo_estimate",
    "dsr_estimate",
    "strategy_complexity_score",
    "review_packet_decision",
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
        description="Validate one or more InstrumentUniverseSpec v1 JSON files."
    )
    p.add_argument(
        "files",
        nargs="+",
        help="Path to one or more InstrumentUniverseSpec JSON files.",
    )
    p.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text)",
    )
    return p.parse_args()


def _check_object(entry: Dict[str, Any], field: str, blockers: List[Blocker],
                  object_type: str = "instrument_universe_spec") -> Any:
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
                   object_type: str = "instrument_universe_spec") -> None:
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
                  object_type: str = "instrument_universe_spec", required: bool = False) -> None:
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
                blockers: List[Blocker], object_type: str = "instrument_universe_spec") -> None:
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


def _check_list_of_strings(entry: Dict[str, Any], field: str, blockers: List[Blocker],
                           object_type: str = "instrument_universe_spec",
                           min_items: int = 0) -> None:
    """Check that field is a list of strings, optionally with min_items requirement."""
    val = entry.get(field)
    if val is None:
        return
    if not isinstance(val, list):
        blockers.append(Blocker(
            "invalid_list",
            object_type,
            field,
            f"{field} must be a list, got {type(val).__name__}"
        ))
        return
    if min_items > 0 and len(val) < min_items:
        blockers.append(Blocker(
            "invalid_list",
            object_type,
            field,
            f"{field} must have at least {min_items} item(s), got {len(val)}"
        ))
        return
    for i, item in enumerate(val):
        if not isinstance(item, str):
            blockers.append(Blocker(
                "invalid_list_item_type",
                object_type,
                f"{field}[{i}]",
                f"{field} items must be strings, got {type(item).__name__}"
            ))


def _check_rule_object(rule: Dict[str, Any], rule_list_name: str,
                       rule_index: int, blockers: List[Blocker]) -> None:
    """Validate a single rule object from inclusion_rules or exclusion_rules."""
    obj_type = "instrument_universe_spec"

    # rule_id is optional but if present must match IRL-YYYY-NNNN
    rule_id = rule.get("rule_id")
    if rule_id is not None:
        if not isinstance(rule_id, str):
            blockers.append(Blocker(
                "invalid_type",
                obj_type,
                f"{rule_list_name}[{rule_index}].rule_id",
                f"rule_id must be a string, got {type(rule_id).__name__}"
            ))
        elif not ID_PATTERN_IRL.match(rule_id):
            blockers.append(Blocker(
                "invalid_id_format",
                obj_type,
                f"{rule_list_name}[{rule_index}].rule_id",
                f"rule_id '{rule_id}' does not match IRL-YYYY-NNNN format"
            ))

    # operator is optional but if present must be a valid enum
    operator = rule.get("operator")
    if operator is not None:
        if not isinstance(operator, str):
            blockers.append(Blocker(
                "invalid_enum",
                obj_type,
                f"{rule_list_name}[{rule_index}].operator",
                f"operator must be a string, got {type(operator).__name__}"
            ))
        elif operator not in RULE_OPERATORS:
            blockers.append(Blocker(
                "invalid_enum",
                obj_type,
                f"{rule_list_name}[{rule_index}].operator",
                f"operator '{operator}' not in allowed set"
            ))

    # field, timing, data_manifest_ref, reason are optional strings
    for opt_field in ("field", "timing", "data_manifest_ref", "reason"):
        val = rule.get(opt_field)
        if val is not None and not isinstance(val, str):
            blockers.append(Blocker(
                "invalid_type",
                obj_type,
                f"{rule_list_name}[{rule_index}].{opt_field}",
                f"{opt_field} must be a string, got {type(val).__name__}"
            ))


def validate_record(entry: Dict[str, Any]) -> List[Blocker]:
    """
    Validate a single InstrumentUniverseSpec record (already parsed from JSON).
    Returns a list of Blocker objects.
    """
    blockers: List[Blocker] = []

    # 0. Root must be an object
    if not isinstance(entry, dict):
        blockers.append(Blocker(
            "invalid_object",
            "instrument_universe_spec",
            "$",
            "InstrumentUniverseSpec must be a JSON object"
        ))
        return blockers

    # 1. Required top-level fields — missing, null, or empty/whitespace-only string fails
    for field in REQUIRED_TOP_LEVEL:
        val = entry.get(field)
        if val is None:
            blockers.append(Blocker(
                "missing_required_field",
                "instrument_universe_spec",
                field,
                f"{field} is required"
            ))
        elif isinstance(val, str) and val.strip() == "":
            blockers.append(Blocker(
                "missing_required_field",
                "instrument_universe_spec",
                field,
                f"{field} is required and cannot be empty"
            ))

    # Cannot safely continue if required fields are missing
    if blockers:
        return blockers

    # 2. instrument_universe_id format
    ius_id = entry.get("instrument_universe_id", "")
    if not isinstance(ius_id, str):
        blockers.append(Blocker(
            "invalid_id_format",
            "instrument_universe_spec",
            "instrument_universe_id",
            "instrument_universe_id must be a string"
        ))
    elif not ID_PATTERN_IUS.match(ius_id):
        blockers.append(Blocker(
            "invalid_id_format",
            "instrument_universe_spec",
            "instrument_universe_id",
            f"instrument_universe_id '{ius_id}' does not match IUS-YYYY-NNNN format"
        ))

    # 3. universe_version — must be integer >= 1, not boolean
    ver = entry.get("universe_version")
    if ver is not None:
        if isinstance(ver, bool) or not isinstance(ver, int):
            blockers.append(Blocker(
                "invalid_type",
                "instrument_universe_spec",
                "universe_version",
                f"universe_version must be an integer, got {type(ver).__name__}"
            ))
        elif ver < 1:
            blockers.append(Blocker(
                "invalid_value",
                "instrument_universe_spec",
                "universe_version",
                f"universe_version must be >= 1, got {ver}"
            ))

    # 4. universe_family — must be a non-empty string if present
    val = entry.get("universe_family")
    if val is not None and not isinstance(val, str):
        blockers.append(Blocker(
            "invalid_type",
            "instrument_universe_spec",
            "universe_family",
            f"universe_family must be a string, got {type(val).__name__}"
        ))
    elif val is not None and val.strip() == "":
        blockers.append(Blocker(
            "missing_required_field",
            "instrument_universe_spec",
            "universe_family",
            "universe_family cannot be empty"
        ))

    # 5. asset_classes — non-empty list of valid enum strings
    asset_classes = entry.get("asset_classes")
    if asset_classes is not None:
        if not isinstance(asset_classes, list):
            blockers.append(Blocker(
                "invalid_list",
                "instrument_universe_spec",
                "asset_classes",
                f"asset_classes must be a list, got {type(asset_classes).__name__}"
            ))
        elif len(asset_classes) == 0:
            blockers.append(Blocker(
                "invalid_list",
                "instrument_universe_spec",
                "asset_classes",
                "asset_classes must have at least 1 item, got 0"
            ))
        else:
            for i, item in enumerate(asset_classes):
                if not isinstance(item, str):
                    blockers.append(Blocker(
                        "invalid_list_item_type",
                        "instrument_universe_spec",
                        f"asset_classes[{i}]",
                        f"asset_classes items must be strings, got {type(item).__name__}"
                    ))
                elif item not in ASSET_CLASSES:
                    blockers.append(Blocker(
                        "invalid_enum",
                        "instrument_universe_spec",
                        f"asset_classes[{i}]",
                        f"asset_classes value '{item}' not in allowed set"
                    ))

    # 6. data_manifest_refs — non-empty list of strings
    dmr = entry.get("data_manifest_refs")
    if dmr is not None:
        if not isinstance(dmr, list):
            blockers.append(Blocker(
                "invalid_list",
                "instrument_universe_spec",
                "data_manifest_refs",
                f"data_manifest_refs must be a list, got {type(dmr).__name__}"
            ))
        elif len(dmr) == 0:
            blockers.append(Blocker(
                "invalid_list",
                "instrument_universe_spec",
                "data_manifest_refs",
                "data_manifest_refs must have at least 1 item, got 0"
            ))
        else:
            for i, item in enumerate(dmr):
                if not isinstance(item, str):
                    blockers.append(Blocker(
                        "invalid_list_item_type",
                        "instrument_universe_spec",
                        f"data_manifest_refs[{i}]",
                        f"data_manifest_refs items must be strings, got {type(item).__name__}"
                    ))

    # 7. universe_construction_policy enum
    _check_enum(entry, "universe_construction_policy", UNIVERSE_CONSTRUCTION_POLICIES, blockers)

    # 8. membership_timing_policy enum
    _check_enum(entry, "membership_timing_policy", MEMBERSHIP_TIMING_POLICIES, blockers)

    # 9. survivorship_policy enum
    _check_enum(entry, "survivorship_policy", SURVIVORSHIP_POLICIES, blockers)

    # 10. tradability_policy enum
    _check_enum(entry, "tradability_policy", TRADABILITY_POLICIES, blockers)

    # 11. corporate_action_policy enum
    _check_enum(entry, "corporate_action_policy", CORPORATE_ACTION_POLICIES, blockers)

    # 12. inclusion_rules — list of rule objects
    inc_rules = entry.get("inclusion_rules")
    if inc_rules is not None:
        if not isinstance(inc_rules, list):
            blockers.append(Blocker(
                "invalid_list",
                "instrument_universe_spec",
                "inclusion_rules",
                f"inclusion_rules must be a list, got {type(inc_rules).__name__}"
            ))
        else:
            for i, rule in enumerate(inc_rules):
                if not isinstance(rule, dict):
                    blockers.append(Blocker(
                        "invalid_object",
                        "instrument_universe_spec",
                        f"inclusion_rules[{i}]",
                        f"inclusion_rules items must be objects, got {type(rule).__name__}"
                    ))
                else:
                    _check_rule_object(rule, "inclusion_rules", i, blockers)

    # 13. exclusion_rules — list of rule objects
    exc_rules = entry.get("exclusion_rules")
    if exc_rules is not None:
        if not isinstance(exc_rules, list):
            blockers.append(Blocker(
                "invalid_list",
                "instrument_universe_spec",
                "exclusion_rules",
                f"exclusion_rules must be a list, got {type(exc_rules).__name__}"
            ))
        else:
            for i, rule in enumerate(exc_rules):
                if not isinstance(rule, dict):
                    blockers.append(Blocker(
                        "invalid_object",
                        "instrument_universe_spec",
                        f"exclusion_rules[{i}]",
                        f"exclusion_rules items must be objects, got {type(rule).__name__}"
                    ))
                else:
                    _check_rule_object(rule, "exclusion_rules", i, blockers)

    # 14. liquidity_policy — object with specific numeric constraints
    lp = entry.get("liquidity_policy")
    if lp is not None:
        if not isinstance(lp, dict):
            blockers.append(Blocker(
                "invalid_object",
                "instrument_universe_spec",
                "liquidity_policy",
                f"liquidity_policy must be an object, got {type(lp).__name__}"
            ))
        else:
            # min_price: number >= 0, not boolean
            mp = lp.get("min_price")
            if mp is not None:
                if isinstance(mp, bool) or not isinstance(mp, (int, float)):
                    blockers.append(Blocker(
                        "invalid_type",
                        "instrument_universe_spec",
                        "liquidity_policy.min_price",
                        f"min_price must be a number, got {type(mp).__name__}"
                    ))
                elif mp < 0:
                    blockers.append(Blocker(
                        "invalid_value",
                        "instrument_universe_spec",
                        "liquidity_policy.min_price",
                        f"min_price must be >= 0, got {mp}"
                    ))

            # max_price: number >= 0, not boolean
            mp2 = lp.get("max_price")
            if mp2 is not None:
                if isinstance(mp2, bool) or not isinstance(mp2, (int, float)):
                    blockers.append(Blocker(
                        "invalid_type",
                        "instrument_universe_spec",
                        "liquidity_policy.max_price",
                        f"max_price must be a number, got {type(mp2).__name__}"
                    ))
                elif mp2 < 0:
                    blockers.append(Blocker(
                        "invalid_value",
                        "instrument_universe_spec",
                        "liquidity_policy.max_price",
                        f"max_price must be >= 0, got {mp2}"
                    ))

            # min_dollar_volume: number >= 0, not boolean
            mdv = lp.get("min_dollar_volume")
            if mdv is not None:
                if isinstance(mdv, bool) or not isinstance(mdv, (int, float)):
                    blockers.append(Blocker(
                        "invalid_type",
                        "instrument_universe_spec",
                        "liquidity_policy.min_dollar_volume",
                        f"min_dollar_volume must be a number, got {type(mdv).__name__}"
                    ))
                elif mdv < 0:
                    blockers.append(Blocker(
                        "invalid_value",
                        "instrument_universe_spec",
                        "liquidity_policy.min_dollar_volume",
                        f"min_dollar_volume must be >= 0, got {mdv}"
                    ))

            # min_average_volume: number >= 0, not boolean
            mav = lp.get("min_average_volume")
            if mav is not None:
                if isinstance(mav, bool) or not isinstance(mav, (int, float)):
                    blockers.append(Blocker(
                        "invalid_type",
                        "instrument_universe_spec",
                        "liquidity_policy.min_average_volume",
                        f"min_average_volume must be a number, got {type(mav).__name__}"
                    ))
                elif mav < 0:
                    blockers.append(Blocker(
                        "invalid_value",
                        "instrument_universe_spec",
                        "liquidity_policy.min_average_volume",
                        f"min_average_volume must be >= 0, got {mav}"
                    ))

            # min_open_interest: integer >= 0, not boolean
            moi = lp.get("min_open_interest")
            if moi is not None:
                if isinstance(moi, bool) or not isinstance(moi, int):
                    blockers.append(Blocker(
                        "invalid_type",
                        "instrument_universe_spec",
                        "liquidity_policy.min_open_interest",
                        f"min_open_interest must be an integer, got {type(moi).__name__}"
                    ))
                elif moi < 0:
                    blockers.append(Blocker(
                        "invalid_value",
                        "instrument_universe_spec",
                        "liquidity_policy.min_open_interest",
                        f"min_open_interest must be >= 0, got {moi}"
                    ))

            # max_bid_ask_spread: number in [0, 1], not boolean
            mbas = lp.get("max_bid_ask_spread")
            if mbas is not None:
                if isinstance(mbas, bool) or not isinstance(mbas, (int, float)):
                    blockers.append(Blocker(
                        "invalid_type",
                        "instrument_universe_spec",
                        "liquidity_policy.max_bid_ask_spread",
                        f"max_bid_ask_spread must be a number, got {type(mbas).__name__}"
                    ))
                elif mbas < 0 or mbas > 1:
                    blockers.append(Blocker(
                        "invalid_value",
                        "instrument_universe_spec",
                        "liquidity_policy.max_bid_ask_spread",
                        f"max_bid_ask_spread must be in [0, 1], got {mbas}"
                    ))

            # min_days_listed: integer >= 0, not boolean
            mdl = lp.get("min_days_listed")
            if mdl is not None:
                if isinstance(mdl, bool) or not isinstance(mdl, int):
                    blockers.append(Blocker(
                        "invalid_type",
                        "instrument_universe_spec",
                        "liquidity_policy.min_days_listed",
                        f"min_days_listed must be an integer, got {type(mdl).__name__}"
                    ))
                elif mdl < 0:
                    blockers.append(Blocker(
                        "invalid_value",
                        "instrument_universe_spec",
                        "liquidity_policy.min_days_listed",
                        f"min_days_listed must be >= 0, got {mdl}"
                    ))

            # liquidity_lookback_days: integer >= 0, not boolean
            llb = lp.get("liquidity_lookback_days")
            if llb is not None:
                if isinstance(llb, bool) or not isinstance(llb, int):
                    blockers.append(Blocker(
                        "invalid_type",
                        "instrument_universe_spec",
                        "liquidity_policy.liquidity_lookback_days",
                        f"liquidity_lookback_days must be an integer, got {type(llb).__name__}"
                    ))
                elif llb < 0:
                    blockers.append(Blocker(
                        "invalid_value",
                        "instrument_universe_spec",
                        "liquidity_policy.liquidity_lookback_days",
                        f"liquidity_lookback_days must be >= 0, got {llb}"
                    ))

            # liquidity_measure_timing: string if present
            lmt = lp.get("liquidity_measure_timing")
            if lmt is not None and not isinstance(lmt, str):
                blockers.append(Blocker(
                    "invalid_type",
                    "instrument_universe_spec",
                    "liquidity_policy.liquidity_measure_timing",
                    f"liquidity_measure_timing must be a string, got {type(lmt).__name__}"
                ))

            # liquidity_not_applicable_reason: string if present
            lnar = lp.get("liquidity_not_applicable_reason")
            if lnar is not None and not isinstance(lnar, str):
                blockers.append(Blocker(
                    "invalid_type",
                    "instrument_universe_spec",
                    "liquidity_policy.liquidity_not_applicable_reason",
                    f"liquidity_not_applicable_reason must be a string, got {type(lnar).__name__}"
                ))

    # 15. data_availability_policy — object with specific constraints
    dap = entry.get("data_availability_policy")
    if dap is not None:
        if not isinstance(dap, dict):
            blockers.append(Blocker(
                "invalid_object",
                "instrument_universe_spec",
                "data_availability_policy",
                f"data_availability_policy must be an object, got {type(dap).__name__}"
            ))
        else:
            # required_history_days: integer >= 0, not boolean
            rhd = dap.get("required_history_days")
            if rhd is not None:
                if isinstance(rhd, bool) or not isinstance(rhd, int):
                    blockers.append(Blocker(
                        "invalid_type",
                        "instrument_universe_spec",
                        "data_availability_policy.required_history_days",
                        f"required_history_days must be an integer, got {type(rhd).__name__}"
                    ))
                elif rhd < 0:
                    blockers.append(Blocker(
                        "invalid_value",
                        "instrument_universe_spec",
                        "data_availability_policy.required_history_days",
                        f"required_history_days must be >= 0, got {rhd}"
                    ))

            # required_feature_coverage: number in [0, 1], not boolean
            rfc = dap.get("required_feature_coverage")
            if rfc is not None:
                if isinstance(rfc, bool) or not isinstance(rfc, (int, float)):
                    blockers.append(Blocker(
                        "invalid_type",
                        "instrument_universe_spec",
                        "data_availability_policy.required_feature_coverage",
                        f"required_feature_coverage must be a number, got {type(rfc).__name__}"
                    ))
                elif rfc < 0 or rfc > 1:
                    blockers.append(Blocker(
                        "invalid_value",
                        "instrument_universe_spec",
                        "data_availability_policy.required_feature_coverage",
                        f"required_feature_coverage must be in [0, 1], got {rfc}"
                    ))

            # required_outcome_coverage: number in [0, 1], not boolean
            roc = dap.get("required_outcome_coverage")
            if roc is not None:
                if isinstance(roc, bool) or not isinstance(roc, (int, float)):
                    blockers.append(Blocker(
                        "invalid_type",
                        "instrument_universe_spec",
                        "data_availability_policy.required_outcome_coverage",
                        f"required_outcome_coverage must be a number, got {type(roc).__name__}"
                    ))
                elif roc < 0 or roc > 1:
                    blockers.append(Blocker(
                        "invalid_value",
                        "instrument_universe_spec",
                        "data_availability_policy.required_outcome_coverage",
                        f"required_outcome_coverage must be in [0, 1], got {roc}"
                    ))

            # missing_data_policy: string if present
            mdp = dap.get("missing_data_policy")
            if mdp is not None and not isinstance(mdp, str):
                blockers.append(Blocker(
                    "invalid_type",
                    "instrument_universe_spec",
                    "data_availability_policy.missing_data_policy",
                    f"missing_data_policy must be a string, got {type(mdp).__name__}"
                ))

            # stale_data_policy: string if present
            sdp = dap.get("stale_data_policy")
            if sdp is not None and not isinstance(sdp, str):
                blockers.append(Blocker(
                    "invalid_type",
                    "instrument_universe_spec",
                    "data_availability_policy.stale_data_policy",
                    f"stale_data_policy must be a string, got {type(sdp).__name__}"
                ))

            # point_in_time_required: boolean if present
            _check_boolean(dap, "point_in_time_required", blockers)

            # feature_cutoff_alignment_required: boolean if present
            _check_boolean(dap, "feature_cutoff_alignment_required", blockers)

    # 16. universe_snapshot_refs, runner_output_refs, domain_profile_refs — lists of strings if present
    for ref_field in ("universe_snapshot_refs", "runner_output_refs", "domain_profile_refs"):
        _check_list_of_strings(entry, ref_field, blockers)

    # 17. reviewer — must be an object with name required and non-empty
    reviewer = entry.get("reviewer")
    if reviewer is not None:
        if not isinstance(reviewer, dict):
            blockers.append(Blocker(
                "invalid_object",
                "instrument_universe_spec",
                "reviewer",
                f"reviewer must be an object, got {type(reviewer).__name__}"
            ))
        else:
            rev_name = reviewer.get("name")
            if rev_name is None:
                blockers.append(Blocker(
                    "missing_required_field",
                    "instrument_universe_spec",
                    "reviewer.name",
                    "reviewer.name is required"
                ))
            elif not isinstance(rev_name, str):
                blockers.append(Blocker(
                    "invalid_type",
                    "instrument_universe_spec",
                    "reviewer.name",
                    f"reviewer.name must be a string, got {type(rev_name).__name__}"
                ))
            elif rev_name.strip() == "":
                blockers.append(Blocker(
                    "missing_required_field",
                    "instrument_universe_spec",
                    "reviewer.name",
                    "reviewer.name cannot be empty"
                ))

    # 18. created_at — must be a non-empty string (checked in required loop, but ensure type)
    _check_string(entry, "created_at", blockers, required=True)

    # 19. extension_hooks — object if present
    ext = entry.get("extension_hooks")
    if ext is not None and not isinstance(ext, dict):
        blockers.append(Blocker(
            "invalid_object",
            "instrument_universe_spec",
            "extension_hooks",
            f"extension_hooks must be an object, got {type(ext).__name__}"
        ))

    # 20. Computed-assessment/run-output fields — blocked at top level
    for field in COMPUTED_ASSESSMENT_FIELDS:
        if field in entry:
            blockers.append(Blocker(
                "computed_assessment_field",
                "instrument_universe_spec",
                field,
                f"'{field}' is a computed-assessment/run-output field and must not appear "
                f"in InstrumentUniverseSpec; it belongs in ModelAssessmentSpec or runner output"
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
            "instrument_universe_spec_file",
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
            "instrument_universe_spec_file",
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
