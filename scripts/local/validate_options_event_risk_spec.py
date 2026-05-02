#!/usr/bin/env python3
"""
Local OptionsEventRiskSpec v1 validator.
Validates one or more OptionsEventRiskSpec JSON files against the v1 schema and governance rules.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Any, List

# ID patterns
ID_PATTERN_OER = re.compile(r"^OER-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_EVS = re.compile(r"^EVS-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_IUS = re.compile(r"^IUS-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_OUT = re.compile(r"^OUT-[0-9]{4}-[0-9]{4}$")

# Enums
OPTION_UNIVERSE_POLICIES = {
    "listed_equity_options", "index_options", "etf_options",
    "futures_options", "crypto_options", "custom"
}
OPTION_SIDE_POLICIES = {
    "calls_only", "puts_only", "calls_and_puts", "straddle", "strangle",
    "vertical_spread", "calendar_spread", "custom"
}
STRATEGY_STRUCTURE_POLICIES = {
    "single_leg", "two_leg_spread", "multi_leg_spread",
    "delta_neutral", "volatility_structure", "custom"
}
EXECUTION_TIMING_POLICIES = {
    "decision_timestamp", "event_anchor_relative", "session_open",
    "session_close", "next_tradable_quote", "custom"
}
GAP_EXPOSURE_POLICIES = {
    "allow_gap_hold", "prohibit_gap_hold", "exit_before_event_anchor",
    "enter_after_event_anchor", "custom"
}
QUOTE_QUALITY_METHODS = {
    "require_bid_ask", "allow_mid_only", "reject_stale_quotes",
    "require_open_interest", "custom"
}

# Required top-level fields
REQUIRED_TOP_LEVEL = [
    "options_event_risk_spec_id",
    "options_event_risk_version",
    "event_study_spec_ref",
    "instrument_universe_ref",
    "outcome_spec_refs",
    "option_universe_policy",
    "contract_selection_policy",
    "expiry_selection_policy",
    "moneyness_selection_policy",
    "option_side_policy",
    "strategy_structure_policy",
    "liquidity_policy",
    "pricing_policy",
    "execution_timing_policy",
    "gap_exposure_policy",
    "quote_quality_policy",
    "created_at",
    "reviewer",
]

# Required object fields (must be objects, not scalars)
REQUIRED_OBJECT_FIELDS = [
    "contract_selection_policy",
    "expiry_selection_policy",
    "moneyness_selection_policy",
    "liquidity_policy",
    "pricing_policy",
    "quote_quality_policy",
]

# Boundary/computed fields blocked at top level
BOUNDARY_FIELDS = [
    "selected_variant_id",
    "n_tried",
    "trial_family_id",
    "pbo_estimate",
    "dsr_estimate",
    "review_packet_decision",
    "entry_dpe",
    "exit_dpe",
    "bmo_amc_indicator",
    "iv_crush",
    "event_identity",
    "event_timestamp",
    "event_anchor_policy",
    "underlying_universe_membership",
    "outcome_definition",
    "overfit_discount",
]

# Allowed extension_hooks sub-fields
EXTENSION_HOOKS_ALLOWED = {
    "domain_profile_extension_refs",
    "runner_output_extension_refs",
    "review_packet_extension_refs",
    "preearnings_profile_extension_refs",
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
        description="Validate one or more OptionsEventRiskSpec v1 JSON files."
    )
    p.add_argument(
        "files",
        nargs="+",
        help="Path to one or more OptionsEventRiskSpec JSON files.",
    )
    p.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text)",
    )
    return p.parse_args()


def _check_object(entry: Dict[str, Any], field: str, blockers: List[Blocker],
                  object_type: str = "options_event_risk_spec") -> Any:
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
                   object_type: str = "options_event_risk_spec") -> None:
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
                  object_type: str = "options_event_risk_spec",
                  required: bool = False) -> None:
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
                blockers: List[Blocker], object_type: str = "options_event_risk_spec") -> None:
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


def _check_integer(entry: Dict[str, Any], field: str, blockers: List[Blocker],
                   object_type: str = "options_event_risk_spec",
                   required: bool = False,
                   min_value: int = None) -> Any:
    """
    Check that field, if present or required, is an integer (not bool).
    Returns the value or None if missing.
    """
    val = entry.get(field)
    if val is None:
        if required:
            blockers.append(Blocker(
                "missing_required_field",
                object_type,
                field,
                f"{field} is required"
            ))
        return None
    if isinstance(val, bool) or not isinstance(val, int):
        blockers.append(Blocker(
            "invalid_type",
            object_type,
            field,
            f"{field} must be an integer, got {type(val).__name__}"
        ))
        return None
    if min_value is not None and val < min_value:
        blockers.append(Blocker(
            "invalid_value",
            object_type,
            field,
            f"{field} must be >= {min_value}, got {val}"
        ))
    return val


def _check_number(entry: Dict[str, Any], field: str, blockers: List[Blocker],
                  object_type: str = "options_event_risk_spec",
                  required: bool = False,
                  min_value: float = None,
                  max_value: float = None) -> Any:
    """
    Check that field, if present or required, is a number (int or float, not bool).
    Returns the value or None if missing.
    """
    val = entry.get(field)
    if val is None:
        if required:
            blockers.append(Blocker(
                "missing_required_field",
                object_type,
                field,
                f"{field} is required"
            ))
        return None
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        blockers.append(Blocker(
            "invalid_type",
            object_type,
            field,
            f"{field} must be a number, got {type(val).__name__}"
        ))
        return None
    if min_value is not None and val < min_value:
        blockers.append(Blocker(
            "invalid_value",
            object_type,
            field,
            f"{field} must be >= {min_value}, got {val}"
        ))
    if max_value is not None and val > max_value:
        blockers.append(Blocker(
            "invalid_value",
            object_type,
            field,
            f"{field} must be <= {max_value}, got {val}"
        ))
    return val


def _check_list_of_strings(entry: Dict[str, Any], field: str, blockers: List[Blocker],
                           object_type: str = "options_event_risk_spec",
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


def _check_list_of_strings_or_optional_strings(
        entry: Dict[str, Any], field: str, blockers: List[Blocker],
        object_type: str = "options_event_risk_spec",
        min_items: int = 0) -> None:
    """
    Check that field is a list of strings or a list that may contain nulls.
    Rejects non-string, non-null items.
    """
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
        if item is not None and not isinstance(item, str):
            blockers.append(Blocker(
                "invalid_list_item_type",
                object_type,
                f"{field}[{i}]",
                f"{field} items must be strings, got {type(item).__name__}"
            ))


def _check_object_optional_field(obj: Dict[str, Any], field: str, blockers: List[Blocker],
                                 object_type: str = "options_event_risk_spec") -> Any:
    """Check that an optional sub-field within an object is present; return None or the value."""
    val = obj.get(field)
    return val


def _check_object_string_field(obj: Dict[str, Any], field: str, blockers: List[Blocker],
                               object_type: str = "options_event_risk_spec") -> None:
    """Check that an optional string sub-field within an object, if present, is a string."""
    val = obj.get(field)
    if val is not None and not isinstance(val, str):
        blockers.append(Blocker(
            "invalid_type",
            object_type,
            f"{object_type}.{field}" if object_type else field,
            f"{field} must be a string, got {type(val).__name__}"
        ))


def _check_object_boolean_field(obj: Dict[str, Any], field: str, blockers: List[Blocker],
                                 object_type: str = "options_event_risk_spec") -> None:
    """Check that an optional boolean sub-field within an object, if present, is a boolean."""
    val = obj.get(field)
    if val is not None and not isinstance(val, bool):
        blockers.append(Blocker(
            "invalid_boolean",
            object_type,
            f"{object_type}.{field}" if object_type else field,
            f"{field} must be a boolean, got {type(val).__name__}"
        ))


def _check_object_number_field(obj: Dict[str, Any], field: str, blockers: List[Blocker],
                               object_type: str = "options_event_risk_spec",
                               min_value: float = None,
                               max_value: float = None) -> Any:
    """Check that an optional numeric sub-field within an object is a number (not bool)."""
    val = obj.get(field)
    if val is None:
        return None
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        blockers.append(Blocker(
            "invalid_type",
            object_type,
            f"{object_type}.{field}" if object_type else field,
            f"{field} must be a number, got {type(val).__name__}"
        ))
        return None
    if min_value is not None and val < min_value:
        blockers.append(Blocker(
            "invalid_value",
            object_type,
            f"{object_type}.{field}" if object_type else field,
            f"{field} must be >= {min_value}, got {val}"
        ))
    if max_value is not None and val > max_value:
        blockers.append(Blocker(
            "invalid_value",
            object_type,
            f"{object_type}.{field}" if object_type else field,
            f"{field} must be <= {max_value}, got {val}"
        ))
    return val


def _check_object_integer_field(obj: Dict[str, Any], field: str, blockers: List[Blocker],
                                object_type: str = "options_event_risk_spec",
                                min_value: int = None) -> Any:
    """Check that an optional integer sub-field within an object is an integer (not bool)."""
    val = obj.get(field)
    if val is None:
        return None
    if isinstance(val, bool) or not isinstance(val, int):
        blockers.append(Blocker(
            "invalid_type",
            object_type,
            f"{object_type}.{field}" if object_type else field,
            f"{field} must be an integer, got {type(val).__name__}"
        ))
        return None
    if min_value is not None and val < min_value:
        blockers.append(Blocker(
            "invalid_value",
            object_type,
            f"{object_type}.{field}" if object_type else field,
            f"{field} must be >= {min_value}, got {val}"
        ))
    return val


def _check_object_list_of_numbers(obj: Dict[str, Any], field: str, blockers: List[Blocker],
                                  object_type: str = "options_event_risk_spec") -> None:
    """Check that an optional sub-field is a list of numbers."""
    val = obj.get(field)
    if val is None:
        return
    if not isinstance(val, list):
        blockers.append(Blocker(
            "invalid_list",
            object_type,
            f"{object_type}.{field}" if object_type else field,
            f"{field} must be a list, got {type(val).__name__}"
        ))
        return
    for i, item in enumerate(val):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            blockers.append(Blocker(
                "invalid_list_item_type",
                object_type,
                f"{object_type}.{field}[{i}]" if object_type else f"{field}[{i}]",
                f"{field} items must be numbers, got {type(item).__name__}"
            ))


def _check_object_list_of_integers(obj: Dict[str, Any], field: str, blockers: List[Blocker],
                                   object_type: str = "options_event_risk_spec",
                                   min_value: int = None) -> None:
    """Check that an optional sub-field is a list of non-negative integers."""
    val = obj.get(field)
    if val is None:
        return
    if not isinstance(val, list):
        blockers.append(Blocker(
            "invalid_list",
            object_type,
            f"{object_type}.{field}" if object_type else field,
            f"{field} must be a list, got {type(val).__name__}"
        ))
        return
    for i, item in enumerate(val):
        if isinstance(item, bool) or not isinstance(item, int):
            blockers.append(Blocker(
                "invalid_list_item_type",
                object_type,
                f"{object_type}.{field}[{i}]" if object_type else f"{field}[{i}]",
                f"{field} items must be integers, got {type(item).__name__}"
            ))
        elif min_value is not None and item < min_value:
            blockers.append(Blocker(
                "invalid_value",
                object_type,
                f"{object_type}.{field}[{i}]" if object_type else f"{field}[{i}]",
                f"{field}[{i}] must be >= {min_value}, got {item}"
            ))


def validate_record(entry: Dict[str, Any]) -> List[Blocker]:
    """
    Validate a single OptionsEventRiskSpec record (already parsed from JSON).
    Returns a list of Blocker objects.
    """
    blockers: List[Blocker] = []
    ot = "options_event_risk_spec"

    # 0. Root must be an object
    if not isinstance(entry, dict):
        blockers.append(Blocker(
            "invalid_object",
            ot,
            "$",
            "OptionsEventRiskSpec must be a JSON object"
        ))
        return blockers

    # 1. Required top-level fields — missing, null, or empty/whitespace-only string fails.
    for field in REQUIRED_TOP_LEVEL:
        val = entry.get(field)
        if val is None:
            blockers.append(Blocker(
                "missing_required_field",
                ot,
                field,
                f"{field} is required"
            ))
        elif isinstance(val, str) and val.strip() == "":
            blockers.append(Blocker(
                "missing_required_field",
                ot,
                field,
                f"{field} is required and cannot be empty"
            ))
        elif field == "created_at" and not isinstance(val, str):
            blockers.append(Blocker(
                "invalid_type",
                ot,
                field,
                f"{field} must be a string, got {type(val).__name__}"
            ))

    # Cannot safely continue if required fields are missing
    if blockers:
        return blockers

    # 2. options_event_risk_spec_id format
    oer_id = entry.get("options_event_risk_spec_id", "")
    if not isinstance(oer_id, str):
        blockers.append(Blocker(
            "invalid_id_format",
            ot,
            "options_event_risk_spec_id",
            "options_event_risk_spec_id must be a string"
        ))
    elif not ID_PATTERN_OER.match(oer_id):
        blockers.append(Blocker(
            "invalid_id_format",
            ot,
            "options_event_risk_spec_id",
            f"options_event_risk_spec_id '{oer_id}' does not match OER-YYYY-NNNN format"
        ))

    # 3. options_event_risk_version — must be integer >= 1, not boolean
    ver = entry.get("options_event_risk_version")
    if ver is not None:
        if isinstance(ver, bool) or not isinstance(ver, int):
            blockers.append(Blocker(
                "invalid_type",
                ot,
                "options_event_risk_version",
                f"options_event_risk_version must be an integer, got {type(ver).__name__}"
            ))
        elif ver < 1:
            blockers.append(Blocker(
                "invalid_value",
                ot,
                "options_event_risk_version",
                f"options_event_risk_version must be >= 1, got {ver}"
            ))

    # 4. event_study_spec_ref format
    evs_ref = entry.get("event_study_spec_ref", "")
    if not isinstance(evs_ref, str):
        blockers.append(Blocker(
            "invalid_id_format",
            ot,
            "event_study_spec_ref",
            "event_study_spec_ref must be a string"
        ))
    elif not ID_PATTERN_EVS.match(evs_ref):
        blockers.append(Blocker(
            "invalid_id_format",
            ot,
            "event_study_spec_ref",
            f"event_study_spec_ref '{evs_ref}' does not match EVS-YYYY-NNNN format"
        ))

    # 5. instrument_universe_ref format
    ius_ref = entry.get("instrument_universe_ref", "")
    if not isinstance(ius_ref, str):
        blockers.append(Blocker(
            "invalid_id_format",
            ot,
            "instrument_universe_ref",
            "instrument_universe_ref must be a string"
        ))
    elif not ID_PATTERN_IUS.match(ius_ref):
        blockers.append(Blocker(
            "invalid_id_format",
            ot,
            "instrument_universe_ref",
            f"instrument_universe_ref '{ius_ref}' does not match IUS-YYYY-NNNN format"
        ))

    # 6. outcome_spec_refs — non-empty list of OUT-YYYY-NNNN strings
    outcome_refs = entry.get("outcome_spec_refs")
    if outcome_refs is not None:
        if not isinstance(outcome_refs, list):
            blockers.append(Blocker(
                "invalid_list",
                ot,
                "outcome_spec_refs",
                f"outcome_spec_refs must be a list, got {type(outcome_refs).__name__}"
            ))
        elif len(outcome_refs) == 0:
            blockers.append(Blocker(
                "invalid_list",
                ot,
                "outcome_spec_refs",
                "outcome_spec_refs must have at least 1 item, got 0"
            ))
        else:
            for i, item in enumerate(outcome_refs):
                if not isinstance(item, str):
                    blockers.append(Blocker(
                        "invalid_list_item_type",
                        ot,
                        f"outcome_spec_refs[{i}]",
                        f"outcome_spec_refs items must be strings, got {type(item).__name__}"
                    ))
                elif not ID_PATTERN_OUT.match(item):
                    blockers.append(Blocker(
                        "invalid_id_format",
                        ot,
                        f"outcome_spec_refs[{i}]",
                        f"outcome_spec_refs item '{item}' does not match OUT-YYYY-NNNN format"
                    ))

    # 7. option_universe_policy enum
    _check_enum(entry, "option_universe_policy", OPTION_UNIVERSE_POLICIES, blockers)

    # 8. option_side_policy enum
    _check_enum(entry, "option_side_policy", OPTION_SIDE_POLICIES, blockers)

    # 9. strategy_structure_policy enum
    _check_enum(entry, "strategy_structure_policy", STRATEGY_STRUCTURE_POLICIES, blockers)

    # 10. execution_timing_policy enum
    _check_enum(entry, "execution_timing_policy", EXECUTION_TIMING_POLICIES, blockers)

    # 11. gap_exposure_policy enum
    _check_enum(entry, "gap_exposure_policy", GAP_EXPOSURE_POLICIES, blockers)

    # 12. Required policy objects: contract_selection_policy, expiry_selection_policy,
    #     moneyness_selection_policy, liquidity_policy, pricing_policy, quote_quality_policy
    for field in REQUIRED_OBJECT_FIELDS:
        val = entry.get(field)
        if val is None:
            # Already caught by required top-level check above; skip duplicate
            continue
        if not isinstance(val, dict):
            blockers.append(Blocker(
                "invalid_object",
                ot,
                field,
                f"{field} must be an object, got {type(val).__name__}"
            ))

    # 13. contract_selection_policy — required selection_method; optional sub-fields
    csp = entry.get("contract_selection_policy")
    if isinstance(csp, dict):
        # Required nested field
        csp_sm = csp.get("selection_method")
        if csp_sm is None:
            blockers.append(Blocker(
                "missing_required_field",
                ot,
                "contract_selection_policy.selection_method",
                "contract_selection_policy.selection_method is required"
            ))
        elif not isinstance(csp_sm, str):
            blockers.append(Blocker(
                "invalid_type",
                ot,
                "contract_selection_policy.selection_method",
                "contract_selection_policy.selection_method must be a string"
            ))
        elif csp_sm.strip() == "":
            blockers.append(Blocker(
                "missing_required_field",
                ot,
                "contract_selection_policy.selection_method",
                "contract_selection_policy.selection_method cannot be empty"
            ))
        # Optional sub-fields
        _check_object_list_of_numbers(csp, "delta_targets", blockers, f"{ot}.contract_selection_policy")
        _check_object_integer_field(csp, "contract_count_limit", blockers, f"{ot}.contract_selection_policy", min_value=0)
        # selection_priority — must be a list of strings if present (schema type: array)
        sp_val = csp.get("selection_priority")
        if sp_val is not None:
            if not isinstance(sp_val, list):
                blockers.append(Blocker(
                    "invalid_list",
                    ot,
                    "contract_selection_policy.selection_priority",
                    "contract_selection_policy.selection_priority must be a list"
                ))
            else:
                for i, item in enumerate(sp_val):
                    if not isinstance(item, str):
                        blockers.append(Blocker(
                            "invalid_list_item_type",
                            ot,
                            f"contract_selection_policy.selection_priority[{i}]",
                            f"contract_selection_policy.selection_priority items must be strings"
                        ))
        _check_object_string_field(csp, "tie_break_policy", blockers, f"{ot}.contract_selection_policy")

    # 14. expiry_selection_policy — required selection_method; optional sub-fields
    esp = entry.get("expiry_selection_policy")
    if isinstance(esp, dict):
        # Required nested field
        esp_sm = esp.get("selection_method")
        if esp_sm is None:
            blockers.append(Blocker(
                "missing_required_field",
                ot,
                "expiry_selection_policy.selection_method",
                "expiry_selection_policy.selection_method is required"
            ))
        elif not isinstance(esp_sm, str):
            blockers.append(Blocker(
                "invalid_type",
                ot,
                "expiry_selection_policy.selection_method",
                "expiry_selection_policy.selection_method must be a string"
            ))
        elif esp_sm.strip() == "":
            blockers.append(Blocker(
                "missing_required_field",
                ot,
                "expiry_selection_policy.selection_method",
                "expiry_selection_policy.selection_method cannot be empty"
            ))
        # Optional sub-fields
        _check_object_integer_field(esp, "min_dte", blockers, f"{ot}.expiry_selection_policy", min_value=0)
        _check_object_integer_field(esp, "max_dte", blockers, f"{ot}.expiry_selection_policy", min_value=0)
        _check_object_list_of_integers(esp, "expiry_ranks", blockers, f"{ot}.expiry_selection_policy", min_value=0)

    # 15. moneyness_selection_policy — required target_type; optional sub-fields
    msp = entry.get("moneyness_selection_policy")
    if isinstance(msp, dict):
        # Required nested field
        msp_tt = msp.get("target_type")
        if msp_tt is None:
            blockers.append(Blocker(
                "missing_required_field",
                ot,
                "moneyness_selection_policy.target_type",
                "moneyness_selection_policy.target_type is required"
            ))
        elif not isinstance(msp_tt, str):
            blockers.append(Blocker(
                "invalid_type",
                ot,
                "moneyness_selection_policy.target_type",
                "moneyness_selection_policy.target_type must be a string"
            ))
        elif msp_tt.strip() == "":
            blockers.append(Blocker(
                "missing_required_field",
                ot,
                "moneyness_selection_policy.target_type",
                "moneyness_selection_policy.target_type cannot be empty"
            ))
        # Optional sub-fields
        _check_object_number_field(msp, "percent_moneyness", blockers, f"{ot}.moneyness_selection_policy", min_value=0)

    # 16. liquidity_policy — validate known sub-fields if present
    lp = entry.get("liquidity_policy")
    if isinstance(lp, dict):
        _check_object_number_field(lp, "min_option_price", blockers, f"{ot}.liquidity_policy", min_value=0)
        _check_object_number_field(lp, "max_option_price", blockers, f"{ot}.liquidity_policy", min_value=0)
        _check_object_integer_field(lp, "min_open_interest", blockers, f"{ot}.liquidity_policy", min_value=0)
        _check_object_integer_field(lp, "min_volume", blockers, f"{ot}.liquidity_policy", min_value=0)
        _check_object_number_field(lp, "max_bid_ask_spread_abs", blockers, f"{ot}.liquidity_policy", min_value=0)
        _check_object_number_field(lp, "max_bid_ask_spread_pct", blockers, f"{ot}.liquidity_policy", min_value=0, max_value=1)
        _check_object_integer_field(lp, "max_quote_age_seconds", blockers, f"{ot}.liquidity_policy", min_value=0)
        _check_object_boolean_field(lp, "require_nbbo", blockers, f"{ot}.liquidity_policy")
        _check_object_string_field(lp, "stale_quote_policy", blockers, f"{ot}.liquidity_policy")
        _check_object_string_field(lp, "missing_greeks_policy", blockers, f"{ot}.liquidity_policy")
        _check_object_string_field(lp, "liquidity_not_applicable_reason", blockers, f"{ot}.liquidity_policy")

    # 17. pricing_policy — validate known sub-fields if present
    pp = entry.get("pricing_policy")
    if isinstance(pp, dict):
        _check_object_string_field(pp, "fill_price_basis", blockers, f"{ot}.pricing_policy")
        _check_object_number_field(pp, "spread_penalty_bps", blockers, f"{ot}.pricing_policy", min_value=0)
        _check_object_string_field(pp, "commission_model_ref", blockers, f"{ot}.pricing_policy")
        _check_object_string_field(pp, "slippage_model_ref", blockers, f"{ot}.pricing_policy")
        _check_object_string_field(pp, "quote_timestamp_policy", blockers, f"{ot}.pricing_policy")
        _check_object_string_field(pp, "entry_quote_policy", blockers, f"{ot}.pricing_policy")
        _check_object_string_field(pp, "exit_quote_policy", blockers, f"{ot}.pricing_policy")
        _check_object_string_field(pp, "partial_fill_policy", blockers, f"{ot}.pricing_policy")
        _check_object_string_field(pp, "multi_leg_execution_policy", blockers, f"{ot}.pricing_policy")

    # 18. quote_quality_policy — validate known sub-fields if present
    qqp = entry.get("quote_quality_policy")
    if isinstance(qqp, dict):
        # quality_method — optional string enum; reject invalid values
        qm_val = qqp.get("quality_method")
        if qm_val is not None:
            if not isinstance(qm_val, str):
                blockers.append(Blocker(
                    "invalid_type",
                    ot,
                    "quote_quality_policy.quality_method",
                    "quote_quality_policy.quality_method must be a string"
                ))
            elif qm_val.strip() == "":
                blockers.append(Blocker(
                    "missing_required_field",
                    ot,
                    "quote_quality_policy.quality_method",
                    "quote_quality_policy.quality_method cannot be empty"
                ))
            elif qm_val not in QUOTE_QUALITY_METHODS:
                blockers.append(Blocker(
                    "invalid_field",
                    ot,
                    "quote_quality_policy.quality_method",
                    f"quote_quality_policy.quality_method must be one of: {', '.join(sorted(QUOTE_QUALITY_METHODS))}"
                ))
        _check_object_boolean_field(qqp, "require_bid_ask", blockers, f"{ot}.quote_quality_policy")
        _check_object_boolean_field(qqp, "allow_mid_only", blockers, f"{ot}.quote_quality_policy")
        _check_object_boolean_field(qqp, "reject_stale_quotes", blockers, f"{ot}.quote_quality_policy")
        _check_object_boolean_field(qqp, "require_open_interest", blockers, f"{ot}.quote_quality_policy")
        _check_object_number_field(qqp, "min_spread_pct", blockers, f"{ot}.quote_quality_policy", min_value=0, max_value=1)

    # 19. reviewer — required object with name required
    reviewer = entry.get("reviewer")
    if reviewer is not None:
        if not isinstance(reviewer, dict):
            blockers.append(Blocker(
                "invalid_object",
                ot,
                "reviewer",
                f"reviewer must be an object, got {type(reviewer).__name__}"
            ))
        else:
            reviewer_name = reviewer.get("name")
            if reviewer_name is None:
                blockers.append(Blocker(
                    "missing_required_field",
                    ot,
                    "reviewer.name",
                    "reviewer.name is required"
                ))
            elif not isinstance(reviewer_name, str):
                blockers.append(Blocker(
                    "invalid_type",
                    ot,
                    "reviewer.name",
                    f"reviewer.name must be a string, got {type(reviewer_name).__name__}"
                ))
            elif reviewer_name.strip() == "":
                blockers.append(Blocker(
                    "missing_required_field",
                    ot,
                    "reviewer.name",
                    "reviewer.name is required and cannot be empty"
                ))

    # 20. Optional reference arrays
    _check_list_of_strings(entry, "domain_profile_refs", blockers)
    _check_list_of_strings(entry, "preearnings_profile_refs", blockers)
    _check_list_of_strings(entry, "runner_output_refs", blockers)
    _check_list_of_strings(entry, "review_packet_refs", blockers)

    # 21. Optional string refs
    _check_string(entry, "underlying_price_ref", blockers, required=False)
    _check_string(entry, "volatility_surface_ref", blockers, required=False)
    _check_string(entry, "expiration_calendar_ref", blockers, required=False)

    # 22. extension_hooks — optional object, only allowed sub-fields
    ext_hooks = entry.get("extension_hooks")
    if ext_hooks is not None:
        if not isinstance(ext_hooks, dict):
            blockers.append(Blocker(
                "invalid_object",
                ot,
                "extension_hooks",
                f"extension_hooks must be an object, got {type(ext_hooks).__name__}"
            ))
        else:
            for sub_key in ext_hooks:
                if sub_key not in EXTENSION_HOOKS_ALLOWED:
                    blockers.append(Blocker(
                        "invalid_field",
                        ot,
                        f"extension_hooks.{sub_key}",
                        f"extension_hooks may only contain {sorted(EXTENSION_HOOKS_ALLOWED)}; '{sub_key}' is not allowed"
                    ))
            # Validate sub-field values as lists of strings
            for allowed_key in EXTENSION_HOOKS_ALLOWED:
                sub_val = ext_hooks.get(allowed_key)
                if sub_val is not None:
                    if not isinstance(sub_val, list):
                        blockers.append(Blocker(
                            "invalid_list",
                            ot,
                            f"extension_hooks.{allowed_key}",
                            f"extension_hooks.{allowed_key} must be a list, got {type(sub_val).__name__}"
                        ))
                    else:
                        for i, item in enumerate(sub_val):
                            if not isinstance(item, str):
                                blockers.append(Blocker(
                                    "invalid_list_item_type",
                                    ot,
                                    f"extension_hooks.{allowed_key}[{i}]",
                                    f"extension_hooks.{allowed_key} items must be strings, got {type(item).__name__}"
                                ))

    # 23. Root additionalProperties boundary — block undeclared top-level fields
    declared_fields = set(REQUIRED_TOP_LEVEL)
    optional_declared = {
        # Reference arrays
        "domain_profile_refs",
        "preearnings_profile_refs",
        "runner_output_refs",
        "review_packet_refs",
        # String refs
        "underlying_price_ref",
        "volatility_surface_ref",
        "expiration_calendar_ref",
        # Policy object sub-fields — note: these are validated above as objects
        # but are not top-level scalar enums in the schema
        # Boundary/computed fields intentionally absent from this set
        # Extension hooks
        "extension_hooks",
        # Optional hooks
        "greeks_policy",
        "iv_policy",
        "skew_policy",
        "spread_construction_policy",
        "hedge_policy",
        "assignment_exercise_policy",
        "corporate_action_policy",
        "event_session_policy",
        # Notes
        "notes",
    }
    declared_fields |= optional_declared

    for top_key in entry:
        if top_key not in declared_fields:
            blockers.append(Blocker(
                "invalid_field",
                ot,
                top_key,
                f"'{top_key}' is not a declared field in OptionsEventRiskSpec v1; "
                f"boundary fields such as pbo_estimate, selected_variant_id are not allowed"
            ))

    return blockers


def validate_file(path: Path) -> tuple[List[Blocker], int]:
    """
    Validate a single file. Returns (blockers, exit_code).
    Exit codes: 0=valid, 1=blockers, 2=error.
    Does not print — caller handles output based on format.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[ERROR] {path}: could not read file: {e}", file=sys.stderr)
        return ([], 2)

    try:
        entry = json.loads(text)
    except Exception as e:
        print(f"[ERROR] {path}: could not parse JSON: {e}", file=sys.stderr)
        return ([], 2)

    blockers = validate_record(entry)
    return (blockers, 0 if not blockers else 1)


def main():
    args = parse_args()
    per_file: Dict[str, tuple[List[Blocker], int]] = {}
    any_blockers = False
    any_error = False

    for path_str in args.files:
        path = Path(path_str)
        blockers, code = validate_file(path)
        per_file[str(path)] = (blockers, code)
        if code == 1:
            any_blockers = True
        elif code == 2:
            any_error = True

    if args.format == "json":
        out = {
            "files": {
                str(path): {
                    "blockers_count": len(blks),
                    "blockers": [b.to_dict() for b in blks],
                }
                for path, (blks, _) in per_file.items()
            },
            "total_blockers": sum(len(blks) for blks, _ in per_file.values()),
        }
        print(json.dumps(out, indent=2))
    else:
        for path_str, (blks, code) in per_file.items():
            if code == 2:
                continue  # error already printed to stderr
            if not blks:
                print(f"[OK] {path_str}")
            else:
                print(f"[FAIL] {path_str}")
                for b in blks:
                    print(f"  [{b.code}] {b.object_type}.{b.field}")
                    print(f"        {b.message}")

    if any_error:
        sys.exit(2)
    elif any_blockers:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
