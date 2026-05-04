#!/usr/bin/env python3
"""
Local PreEarningsProfile v1 validator.
Validates one or more PreEarningsProfile JSON files against the v1 schema and governance rules.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Any, List

# ID patterns
ID_PATTERN_PEP = re.compile(r"^PEP-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_EVS = re.compile(r"^EVS-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_OER = re.compile(r"^OER-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_IUS = re.compile(r"^IUS-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_OUT = re.compile(r"^OUT-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_HYP = re.compile(r"^HYP-[0-9]{4}-[0-9]{4}$")

# Enums
SESSION_ANCHOR_POLICIES = {
    "bmo_only", "amc_only", "bmo_and_amc", "intra_day", "unconfirmed", "custom"
}
EARNINGS_TIME_REFERENCES = {
    "after_hours_only", "pre_market_only", "regular_hours_only",
    "confirmed_after_hours", "confirmed_pre_market", "unconfirmed", "custom"
}
GAP_EXPOSURE_POLICIES = {
    "allow_gap_hold", "prohibit_gap_hold", "exit_before_session",
    "enter_after_session", "custom"
}
DPE_COUNTING_CONVENTIONS = {
    "calendar_days", "trading_days", "session_days", "custom"
}
ANCHOR_DAY_POLICIES = {
    "earnings_date_anchor", "announcement_time_anchor", "custom"
}
IV_CRUSH_DEFINITIONS = {
    "absolute_iv_drop", "percent_iv_drop", "iv_rank_collapse",
    "iv_percentile_collapse", "custom"
}
IV_MEASUREMENT_WINDOW_UNITS = {"dpe", "sessions", "calendar_days"}
IV_REGIME_FILTERS = {
    "high_iv_only", "low_iv_only", "iv_expand_only",
    "iv_collapse_only", "any_iv", "custom"
}
SESSION_OVERLAP_POLICIES = {"prioritize_bmo", "prioritize_amc", "separate_trials", "reject"}
EARNINGS_REVISION_POLICIES = {"reject_revision", "accept_revision", "flag_for_review"}
POST_EARNINGS_WINDOW_UNITS = {"dpe", "sessions", "calendar_days"}
EXIT_TRIGGER_POLICIES = {
    "dpe_exit", "iv_collapse", "time_exit", "profit_target", "stop_loss", "custom"
}
IV_PRE_EVENT_SOURCES = {
    "iv_at_entry", "iv_rank_at_entry", "iv_percentile_at_entry", "custom"
}
IV_POST_EVENT_SOURCES = {
    "iv_at_exit", "iv_rank_at_exit", "iv_percentile_at_exit", "custom"
}
IV_HIERARCHY_PRIMARY = {"iv_raw", "iv_rank", "iv_percentile"}

# Required top-level fields (12)
REQUIRED_TOP_LEVEL = [
    "preearnings_profile_id",
    "preearnings_profile_version",
    "event_study_spec_ref",
    "options_event_risk_ref",
    "session_anchor_policy",
    "earnings_time_reference",
    "entry_dpe_policy",
    "exit_dpe_policy",
    "iv_crush_policy",
    "gap_exposure_policy",
    "created_at",
    "reviewer",
]

# Allowed extension_hooks sub-fields (additionalProperties: false)
EXTENSION_HOOKS_ALLOWED = {
    "domain_profile_extension_refs",
    "runner_output_extension_refs",
    "review_packet_extension_refs",
    "options_event_risk_extension_refs",
    "event_study_extension_refs",
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
        description="Validate one or more PreEarningsProfile v1 JSON files."
    )
    p.add_argument(
        "files",
        nargs="+",
        help="Path to one or more PreEarningsProfile JSON files.",
    )
    p.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text)",
    )
    return p.parse_args()


def _check_object(entry: Dict[str, Any], field: str, blockers: List[Blocker],
                  object_type: str = "preearnings_profile") -> Any:
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


def _check_string(entry: Dict[str, Any], field: str, blockers: List[Blocker],
                  object_type: str = "preearnings_profile",
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
                blockers: List[Blocker], object_type: str = "preearnings_profile") -> None:
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
                   object_type: str = "preearnings_profile",
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
                  object_type: str = "preearnings_profile",
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
                           object_type: str = "preearnings_profile",
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


def _check_object_integer_field(obj: Dict[str, Any], field: str, blockers: List[Blocker],
                                object_type: str = "preearnings_profile",
                                min_value: int = None) -> Any:
    """Check that an optional integer sub-field within an object is an integer (not bool)."""
    val = obj.get(field)
    if val is None:
        return None
    if isinstance(val, bool) or not isinstance(val, int):
        blockers.append(Blocker(
            "invalid_type",
            object_type,
            f"{object_type}.{field}",
            f"{field} must be an integer, got {type(val).__name__}"
        ))
        return None
    if min_value is not None and val < min_value:
        blockers.append(Blocker(
            "invalid_value",
            object_type,
            f"{object_type}.{field}",
            f"{field} must be >= {min_value}, got {val}"
        ))
    return val


def _check_object_number_field(obj: Dict[str, Any], field: str, blockers: List[Blocker],
                               object_type: str = "preearnings_profile",
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
            f"{object_type}.{field}",
            f"{field} must be a number, got {type(val).__name__}"
        ))
        return None
    if min_value is not None and val < min_value:
        blockers.append(Blocker(
            "invalid_value",
            object_type,
            f"{object_type}.{field}",
            f"{field} must be >= {min_value}, got {val}"
        ))
    if max_value is not None and val > max_value:
        blockers.append(Blocker(
            "invalid_value",
            object_type,
            f"{object_type}.{field}",
            f"{field} must be <= {max_value}, got {val}"
        ))
    return val


def _check_object_string_field(obj: Dict[str, Any], field: str, blockers: List[Blocker],
                               object_type: str = "preearnings_profile") -> None:
    """Check that an optional string sub-field within an object, if present, is a string."""
    val = obj.get(field)
    if val is not None and not isinstance(val, str):
        blockers.append(Blocker(
            "invalid_type",
            object_type,
            f"{object_type}.{field}",
            f"{field} must be a string, got {type(val).__name__}"
        ))


def _check_object_enum_field(obj: Dict[str, Any], field: str, allowed: set,
                             blockers: List[Blocker],
                             object_type: str = "preearnings_profile") -> None:
    """Check that an optional string enum sub-field within an object, if present, is in allowed set."""
    val = obj.get(field)
    if val is None:
        return
    if not isinstance(val, str):
        blockers.append(Blocker(
            "invalid_enum",
            object_type,
            f"{object_type}.{field}",
            f"{field} must be a string, got {type(val).__name__}"
        ))
        return
    if val not in allowed:
        blockers.append(Blocker(
            "invalid_enum",
            object_type,
            f"{object_type}.{field}",
            f"{field} '{val}' not in allowed set"
        ))


def validate_record(entry: Dict[str, Any]) -> List[Blocker]:
    """
    Validate a single PreEarningsProfile record (already parsed from JSON).
    Returns a list of Blocker objects.
    """
    blockers: List[Blocker] = []
    ot = "preearnings_profile"

    # 0. Root must be an object (handle [] gracefully)
    if not isinstance(entry, dict):
        blockers.append(Blocker(
            "invalid_object",
            ot,
            "$",
            "PreEarningsProfile must be a JSON object"
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

    # 2. preearnings_profile_id format (PEP-YYYY-NNNN)
    pep_id = entry.get("preearnings_profile_id", "")
    if not isinstance(pep_id, str):
        blockers.append(Blocker(
            "invalid_id_format",
            ot,
            "preearnings_profile_id",
            "preearnings_profile_id must be a string"
        ))
    elif not ID_PATTERN_PEP.match(pep_id):
        blockers.append(Blocker(
            "invalid_id_format",
            ot,
            "preearnings_profile_id",
            f"preearnings_profile_id '{pep_id}' does not match PEP-YYYY-NNNN format"
        ))

    # 3. preearnings_profile_version — must be integer >= 1, not boolean
    ver = entry.get("preearnings_profile_version")
    if ver is not None:
        if isinstance(ver, bool) or not isinstance(ver, int):
            blockers.append(Blocker(
                "invalid_type",
                ot,
                "preearnings_profile_version",
                f"preearnings_profile_version must be an integer, got {type(ver).__name__}"
            ))
        elif ver < 1:
            blockers.append(Blocker(
                "invalid_value",
                ot,
                "preearnings_profile_version",
                f"preearnings_profile_version must be >= 1, got {ver}"
            ))

    # 4. event_study_spec_ref format (EVS-YYYY-NNNN)
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

    # 5. options_event_risk_ref format (OER-YYYY-NNNN)
    oer_ref = entry.get("options_event_risk_ref", "")
    if not isinstance(oer_ref, str):
        blockers.append(Blocker(
            "invalid_id_format",
            ot,
            "options_event_risk_ref",
            "options_event_risk_ref must be a string"
        ))
    elif not ID_PATTERN_OER.match(oer_ref):
        blockers.append(Blocker(
            "invalid_id_format",
            ot,
            "options_event_risk_ref",
            f"options_event_risk_ref '{oer_ref}' does not match OER-YYYY-NNNN format"
        ))

    # 6. session_anchor_policy enum
    _check_enum(entry, "session_anchor_policy", SESSION_ANCHOR_POLICIES, blockers)

    # 7. earnings_time_reference enum
    _check_enum(entry, "earnings_time_reference", EARNINGS_TIME_REFERENCES, blockers)

    # 8. gap_exposure_policy enum
    _check_enum(entry, "gap_exposure_policy", GAP_EXPOSURE_POLICIES, blockers)

    # 9. entry_dpe_policy — object with required subfields
    entry_dpe = entry.get("entry_dpe_policy")
    if entry_dpe is not None:
        if not isinstance(entry_dpe, dict):
            blockers.append(Blocker(
                "invalid_object",
                ot,
                "entry_dpe_policy",
                f"entry_dpe_policy must be an object, got {type(entry_dpe).__name__}"
            ))
        else:
            # Required subfields
            for req_field in ("entry_dpe_min", "entry_dpe_max", "dpe_counting_convention", "anchor_day_policy"):
                sub_val = entry_dpe.get(req_field)
                if sub_val is None:
                    blockers.append(Blocker(
                        "missing_required_field",
                        ot,
                        f"entry_dpe_policy.{req_field}",
                        f"entry_dpe_policy.{req_field} is required"
                    ))
                elif req_field in ("entry_dpe_min", "entry_dpe_max"):
                    if isinstance(sub_val, bool) or not isinstance(sub_val, int):
                        blockers.append(Blocker(
                            "invalid_type",
                            ot,
                            f"entry_dpe_policy.{req_field}",
                            f"entry_dpe_policy.{req_field} must be an integer, got {type(sub_val).__name__}"
                        ))
                    elif sub_val < 0:
                        blockers.append(Blocker(
                            "invalid_value",
                            ot,
                            f"entry_dpe_policy.{req_field}",
                            f"entry_dpe_policy.{req_field} must be >= 0, got {sub_val}"
                        ))
                elif req_field in ("dpe_counting_convention", "anchor_day_policy"):
                    if not isinstance(sub_val, str):
                        blockers.append(Blocker(
                            "invalid_type",
                            ot,
                            f"entry_dpe_policy.{req_field}",
                            f"entry_dpe_policy.{req_field} must be a string, got {type(sub_val).__name__}"
                        ))
                    elif req_field == "dpe_counting_convention" and sub_val not in DPE_COUNTING_CONVENTIONS:
                        blockers.append(Blocker(
                            "invalid_enum",
                            ot,
                            f"entry_dpe_policy.{req_field}",
                            f"entry_dpe_policy.{req_field} '{sub_val}' not in allowed set"
                        ))
                    elif req_field == "anchor_day_policy" and sub_val not in ANCHOR_DAY_POLICIES:
                        blockers.append(Blocker(
                            "invalid_enum",
                            ot,
                            f"entry_dpe_policy.{req_field}",
                            f"entry_dpe_policy.{req_field} '{sub_val}' not in allowed set"
                        ))
            # Optional subfields
            _check_object_string_field(entry_dpe, "entry_window_start", blockers, f"{ot}.entry_dpe_policy")
            _check_object_string_field(entry_dpe, "entry_window_end", blockers, f"{ot}.entry_dpe_policy")
            _check_object_integer_field(entry_dpe, "dpe_tolerance", blockers, f"{ot}.entry_dpe_policy", min_value=0)

    # 10. exit_dpe_policy — object with required subfields
    exit_dpe = entry.get("exit_dpe_policy")
    if exit_dpe is not None:
        if not isinstance(exit_dpe, dict):
            blockers.append(Blocker(
                "invalid_object",
                ot,
                "exit_dpe_policy",
                f"exit_dpe_policy must be an object, got {type(exit_dpe).__name__}"
            ))
        else:
            # Required subfields
            for req_field in ("exit_dpe_min", "exit_dpe_max", "dpe_counting_convention", "anchor_day_policy"):
                sub_val = exit_dpe.get(req_field)
                if sub_val is None:
                    blockers.append(Blocker(
                        "missing_required_field",
                        ot,
                        f"exit_dpe_policy.{req_field}",
                        f"exit_dpe_policy.{req_field} is required"
                    ))
                elif req_field in ("exit_dpe_min", "exit_dpe_max"):
                    if isinstance(sub_val, bool) or not isinstance(sub_val, int):
                        blockers.append(Blocker(
                            "invalid_type",
                            ot,
                            f"exit_dpe_policy.{req_field}",
                            f"exit_dpe_policy.{req_field} must be an integer, got {type(sub_val).__name__}"
                        ))
                    elif sub_val < 0:
                        blockers.append(Blocker(
                            "invalid_value",
                            ot,
                            f"exit_dpe_policy.{req_field}",
                            f"exit_dpe_policy.{req_field} must be >= 0, got {sub_val}"
                        ))
                elif req_field in ("dpe_counting_convention", "anchor_day_policy"):
                    if not isinstance(sub_val, str):
                        blockers.append(Blocker(
                            "invalid_type",
                            ot,
                            f"exit_dpe_policy.{req_field}",
                            f"exit_dpe_policy.{req_field} must be a string, got {type(sub_val).__name__}"
                        ))
                    elif req_field == "dpe_counting_convention" and sub_val not in DPE_COUNTING_CONVENTIONS:
                        blockers.append(Blocker(
                            "invalid_enum",
                            ot,
                            f"exit_dpe_policy.{req_field}",
                            f"exit_dpe_policy.{req_field} '{sub_val}' not in allowed set"
                        ))
                    elif req_field == "anchor_day_policy" and sub_val not in ANCHOR_DAY_POLICIES:
                        blockers.append(Blocker(
                            "invalid_enum",
                            ot,
                            f"exit_dpe_policy.{req_field}",
                            f"exit_dpe_policy.{req_field} '{sub_val}' not in allowed set"
                        ))
            # Optional subfields
            _check_object_number_field(exit_dpe, "iv_collapse_threshold", blockers, f"{ot}.exit_dpe_policy", min_value=0, max_value=1)
            _check_object_enum_field(exit_dpe, "post_earnings_window_unit", POST_EARNINGS_WINDOW_UNITS, blockers, f"{ot}.exit_dpe_policy")
            _check_object_enum_field(exit_dpe, "exit_trigger_policy", EXIT_TRIGGER_POLICIES, blockers, f"{ot}.exit_dpe_policy")

    # 11. iv_crush_policy — object with required iv_crush_measurement_window and iv_crush_definition
    iv_crush = entry.get("iv_crush_policy")
    if iv_crush is not None:
        if not isinstance(iv_crush, dict):
            blockers.append(Blocker(
                "invalid_object",
                ot,
                "iv_crush_policy",
                f"iv_crush_policy must be an object, got {type(iv_crush).__name__}"
            ))
        else:
            # Required: iv_crush_measurement_window
            iv_mw = iv_crush.get("iv_crush_measurement_window")
            if iv_mw is None:
                blockers.append(Blocker(
                    "missing_required_field",
                    ot,
                    "iv_crush_policy.iv_crush_measurement_window",
                    "iv_crush_policy.iv_crush_measurement_window is required"
                ))
            elif not isinstance(iv_mw, dict):
                blockers.append(Blocker(
                    "invalid_object",
                    ot,
                    "iv_crush_policy.iv_crush_measurement_window",
                    f"iv_crush_policy.iv_crush_measurement_window must be an object, got {type(iv_mw).__name__}"
                ))
            else:
                # Required subfields within iv_crush_measurement_window: start, end, unit
                for req_field in ("start", "end", "unit"):
                    sub_val = iv_mw.get(req_field)
                    if sub_val is None:
                        blockers.append(Blocker(
                            "missing_required_field",
                            ot,
                            f"iv_crush_policy.iv_crush_measurement_window.{req_field}",
                            f"iv_crush_policy.iv_crush_measurement_window.{req_field} is required"
                        ))
                    elif req_field in ("start", "end"):
                        if isinstance(sub_val, bool) or not isinstance(sub_val, int):
                            blockers.append(Blocker(
                                "invalid_type",
                                ot,
                                f"iv_crush_policy.iv_crush_measurement_window.{req_field}",
                                f"iv_crush_policy.iv_crush_measurement_window.{req_field} must be an integer, got {type(sub_val).__name__}"
                            ))
                        elif req_field == "start" and sub_val < -30:
                            blockers.append(Blocker(
                                "invalid_value",
                                ot,
                                f"iv_crush_policy.iv_crush_measurement_window.{req_field}",
                                f"iv_crush_policy.iv_crush_measurement_window.{req_field} must be >= -30, got {sub_val}"
                            ))
                        elif req_field == "end" and sub_val > 90:
                            blockers.append(Blocker(
                                "invalid_value",
                                ot,
                                f"iv_crush_policy.iv_crush_measurement_window.{req_field}",
                                f"iv_crush_policy.iv_crush_measurement_window.{req_field} must be <= 90, got {sub_val}"
                            ))
                    elif req_field == "unit":
                        if not isinstance(sub_val, str):
                            blockers.append(Blocker(
                                "invalid_type",
                                ot,
                                f"iv_crush_policy.iv_crush_measurement_window.{req_field}",
                                f"iv_crush_policy.iv_crush_measurement_window.{req_field} must be a string, got {type(sub_val).__name__}"
                            ))
                        elif sub_val not in IV_MEASUREMENT_WINDOW_UNITS:
                            blockers.append(Blocker(
                                "invalid_enum",
                                ot,
                                f"iv_crush_policy.iv_crush_measurement_window.{req_field}",
                                f"iv_crush_policy.iv_crush_measurement_window.{req_field} '{sub_val}' not in allowed set"
                            ))

            # Required: iv_crush_definition
            iv_def = iv_crush.get("iv_crush_definition")
            if iv_def is None:
                blockers.append(Blocker(
                    "missing_required_field",
                    ot,
                    "iv_crush_policy.iv_crush_definition",
                    "iv_crush_policy.iv_crush_definition is required"
                ))
            elif not isinstance(iv_def, str):
                blockers.append(Blocker(
                    "invalid_type",
                    ot,
                    "iv_crush_policy.iv_crush_definition",
                    f"iv_crush_policy.iv_crush_definition must be a string, got {type(iv_def).__name__}"
                ))
            elif iv_def not in IV_CRUSH_DEFINITIONS:
                blockers.append(Blocker(
                    "invalid_enum",
                    ot,
                    "iv_crush_policy.iv_crush_definition",
                    f"iv_crush_policy.iv_crush_definition '{iv_def}' not in allowed set"
                ))

            # Optional subfields
            _check_object_number_field(iv_crush, "iv_crush_magnitude_estimate", blockers, f"{ot}.iv_crush_policy", min_value=0, max_value=1)
            _check_object_string_field(iv_crush, "iv_surface_ref", blockers, f"{ot}.iv_crush_policy")
            _check_object_enum_field(iv_crush, "iv_pre_event_source", IV_PRE_EVENT_SOURCES, blockers, f"{ot}.iv_crush_policy")
            _check_object_enum_field(iv_crush, "iv_post_event_source", IV_POST_EVENT_SOURCES, blockers, f"{ot}.iv_crush_policy")

            # iv_hierarchy_policy — object with optional primary/fallback
            iv_hier = iv_crush.get("iv_hierarchy_policy")
            if iv_hier is not None and isinstance(iv_hier, dict):
                _check_object_enum_field(iv_hier, "primary", IV_HIERARCHY_PRIMARY, blockers, f"{ot}.iv_crush_policy.iv_hierarchy_policy")
                _check_object_enum_field(iv_hier, "fallback", IV_HIERARCHY_PRIMARY, blockers, f"{ot}.iv_crush_policy.iv_hierarchy_policy")

            # crush_confirm_window — object with optional start, end, unit
            crush_cw = iv_crush.get("crush_confirm_window")
            if crush_cw is not None and isinstance(crush_cw, dict):
                _check_object_integer_field(crush_cw, "start", blockers, f"{ot}.iv_crush_policy.crush_confirm_window")
                _check_object_integer_field(crush_cw, "end", blockers, f"{ot}.iv_crush_policy.crush_confirm_window")
                _check_object_enum_field(crush_cw, "unit", IV_MEASUREMENT_WINDOW_UNITS, blockers, f"{ot}.iv_crush_policy.crush_confirm_window")

    # 12. reviewer — object with required name (non-empty string)
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

    # 13. outcome_spec_refs — non-empty array of OUT-YYYY-NNNN strings
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

    # 14. instrument_universe_ref format (IUS-YYYY-NNNN) — optional
    ius_ref = entry.get("instrument_universe_ref")
    if ius_ref is not None:
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

    # 15. hypothesis_id format (HYP-YYYY-NNNN) — optional
    hyp_id = entry.get("hypothesis_id")
    if hyp_id is not None:
        if not isinstance(hyp_id, str):
            blockers.append(Blocker(
                "invalid_id_format",
                ot,
                "hypothesis_id",
                "hypothesis_id must be a string"
            ))
        elif not ID_PATTERN_HYP.match(hyp_id):
            blockers.append(Blocker(
                "invalid_id_format",
                ot,
                "hypothesis_id",
                f"hypothesis_id '{hyp_id}' does not match HYP-YYYY-NNNN format"
            ))

    # 16. Optional string fields
    _check_string(entry, "earnings_calendar_ref", blockers, required=False)
    _check_string(entry, "iv_surface_ref", blockers, required=False)
    _check_string(entry, "notes", blockers, required=False)

    # 17. Optional enum fields
    _check_enum(entry, "session_overlap_policy", SESSION_OVERLAP_POLICIES, blockers)
    _check_enum(entry, "earnings_revision_policy", EARNINGS_REVISION_POLICIES, blockers)
    _check_enum(entry, "iv_regime_filter", IV_REGIME_FILTERS, blockers)

    # 18. minimum_iv_rank — number 0.0-1.0
    _check_number(entry, "minimum_iv_rank", blockers, min_value=0.0, max_value=1.0)

    # 19. Optional reference arrays
    _check_list_of_strings(entry, "runner_output_refs", blockers)
    _check_list_of_strings(entry, "review_packet_refs", blockers)

    # 20. dpe_calendar_policy — optional object
    dpe_cal = entry.get("dpe_calendar_policy")
    if dpe_cal is not None and isinstance(dpe_cal, dict):
        _check_object_string_field(dpe_cal, "exchange_calendar_ref", blockers, f"{ot}.dpe_calendar_policy")
        # weekend_handling and holiday_handling are enums but not defined in schema summary as explicit enums; accept strings

    # 21. gap_historical_policy — optional object
    gap_hist = entry.get("gap_historical_policy")
    if gap_hist is not None and isinstance(gap_hist, dict):
        _check_object_number_field(gap_hist, "gap_percentile_threshold", blockers, f"{ot}.gap_historical_policy", min_value=0, max_value=1)
        _check_object_string_field(gap_hist, "gap_direction_filter", blockers, f"{ot}.gap_historical_policy")

    # 22. earnings_size_filter — optional object
    earn_size = entry.get("earnings_size_filter")
    if earn_size is not None and isinstance(earn_size, dict):
        _check_object_number_field(earn_size, "eps_surprise_threshold", blockers, f"{ot}.earnings_size_filter")
        _check_object_string_field(earn_size, "revenue_behavior", blockers, f"{ot}.earnings_size_filter")

    # 23. extension_hooks — optional object, only allowed sub-fields
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

    # 24. Root additionalProperties boundary — block undeclared top-level fields
    declared_fields = set(REQUIRED_TOP_LEVEL)
    optional_declared = {
        # Reference arrays
        "outcome_spec_refs",
        "runner_output_refs",
        "review_packet_refs",
        # String refs
        "instrument_universe_ref",
        "earnings_calendar_ref",
        "iv_surface_ref",
        "notes",
        # Enum fields
        "session_overlap_policy",
        "earnings_revision_policy",
        "iv_regime_filter",
        # Number fields
        "minimum_iv_rank",
        "hypothesis_id",
        # Policy objects
        "dpe_calendar_policy",
        "gap_historical_policy",
        "earnings_size_filter",
        # Extension hooks
        "extension_hooks",
    }
    declared_fields |= optional_declared

    for top_key in entry:
        if top_key not in declared_fields:
            blockers.append(Blocker(
                "invalid_field",
                ot,
                top_key,
                f"'{top_key}' is not a declared field in PreEarningsProfile v1; "
                f"boundary fields such as pbo_estimate, dsr_estimate, sharpe_haircut, "
                f"overfit_discount, selected_variant_id, n_tried, trial_family_id, "
                f"review_packet_decision, live_trading_enabled, production_execution_endpoint, "
                f"ivolatility_table_name, provider_table_name are not allowed"
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
