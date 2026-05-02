#!/usr/bin/env python3
"""
Local EventStudySpec v1 validator.
Validates one or more EventStudySpec JSON files against the v1 schema and governance rules.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Any, List

# ID patterns
ID_PATTERN_EVS = re.compile(r"^EVS-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_OUT = re.compile(r"^OUT-[0-9]{4}-[0-9]{4}$")
ID_PATTERN_IUS = re.compile(r"^IUS-[0-9]{4}-[0-9]{4}$")

# Enums
EVENT_FAMILIES = {
    "earnings", "macro_release", "central_bank_decision", "dividend", "split",
    "index_rebalance", "product_launch", "crypto_protocol_event", "commodity_inventory",
    "regulatory_event", "custom"
}
EVENT_ANCHOR_POLICIES = {
    "event_timestamp", "first_tradable_session_after_event",
    "last_tradable_session_before_event", "next_observation_after_event",
    "previous_observation_before_event", "custom"
}
EVENT_TIMESTAMP_POLICIES = {
    "exact_timestamp_required", "date_only_allowed", "session_only_allowed",
    "inferred_timestamp_allowed", "custom"
}
DECISION_TIMESTAMP_POLICIES = {
    "before_event_publication", "after_event_publication", "prior_session_close",
    "same_session_open", "next_session_open", "custom"
}
LEAKAGE_POLICIES = {
    "strict_no_lookahead", "allow_known_calendar_only", "allow_public_timestamp_only", "custom"
}
EVENT_DEDUPLICATION_POLICIES = {
    "keep_first", "keep_last", "merge_same_day", "merge_same_timestamp",
    "reject_duplicates", "custom"
}
EVENT_COLLISION_POLICIES = {
    "allow_overlapping_windows", "reject_overlapping_windows",
    "keep_highest_priority_event", "merge_event_cluster", "custom"
}
MISSING_EVENT_TIME_POLICIES = {
    "reject_event", "use_date_close", "use_date_open", "infer_from_session", "custom"
}
CALENDAR_POLICIES = {
    "calendar_days", "trading_days", "observations", "custom"
}
WINDOW_UNITS = {
    "calendar_days", "trading_days", "observations", "periods"
}

# Required top-level fields
REQUIRED_TOP_LEVEL = [
    "event_study_spec_id",
    "event_study_version",
    "event_family",
    "event_source_refs",
    "event_anchor_policy",
    "event_timestamp_policy",
    "decision_timestamp_policy",
    "pre_event_window",
    "post_event_window",
    "leakage_policy",
    "event_deduplication_policy",
    "event_collision_policy",
    "missing_event_time_policy",
    "calendar_policy",
    "created_at",
    "reviewer",
]

# Boundary/computed fields blocked at top level
BOUNDARY_FIELDS = [
    "option_contract_selection",
    "delta_target",
    "expiry_rank",
    "entry_dpe",
    "exit_dpe",
    "iv_crush",
    "gap_exposure",
    "directional_signal",
    "ranking_score",
    "selected_variant_id",
    "n_tried",
    "trial_family_id",
    "pnl",
    "pbo_estimate",
    "dsr_estimate",
    "review_packet_decision",
]

# Allowed extension_hooks sub-fields
EXTENSION_HOOKS_ALLOWED = {
    "domain_profile_extension_refs",
    "runner_output_extension_refs",
    "review_packet_extension_refs",
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
        description="Validate one or more EventStudySpec v1 JSON files."
    )
    p.add_argument(
        "files",
        nargs="+",
        help="Path to one or more EventStudySpec JSON files.",
    )
    p.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text)",
    )
    return p.parse_args()


def _check_object(entry: Dict[str, Any], field: str, blockers: List[Blocker],
                  object_type: str = "event_study_spec") -> Any:
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
                   object_type: str = "event_study_spec") -> None:
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
                  object_type: str = "event_study_spec", required: bool = False) -> None:
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
                blockers: List[Blocker], object_type: str = "event_study_spec") -> None:
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
                   object_type: str = "event_study_spec", required: bool = False) -> Any:
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
    return val


def _check_list_of_strings(entry: Dict[str, Any], field: str, blockers: List[Blocker],
                           object_type: str = "event_study_spec",
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


def _check_window_object(window: Dict[str, Any], window_name: str,
                          blockers: List[Blocker]) -> None:
    """
    Validate a pre_event_window or post_event_window object.
    All fields are required within the window object.
    """
    obj_type = "event_study_spec"

    # start_offset — required integer (not bool)
    start_offset = _check_integer(window, "start_offset", blockers, obj_type, required=True)
    # end_offset — required integer (not bool)
    end_offset = _check_integer(window, "end_offset", blockers, obj_type, required=True)

    # units — required enum
    units_val = window.get("units")
    if units_val is None:
        blockers.append(Blocker(
            "missing_required_field",
            obj_type,
            f"{window_name}.units",
            f"{window_name}.units is required"
        ))
    elif not isinstance(units_val, str):
        blockers.append(Blocker(
            "invalid_enum",
            obj_type,
            f"{window_name}.units",
            f"{window_name}.units must be a string, got {type(units_val).__name__}"
        ))
    elif units_val not in WINDOW_UNITS:
        blockers.append(Blocker(
            "invalid_enum",
            obj_type,
            f"{window_name}.units",
            f"{window_name}.units '{units_val}' not in allowed set"
        ))

    # include_event_anchor — required boolean
    anchor_val = window.get("include_event_anchor")
    if anchor_val is None:
        blockers.append(Blocker(
            "missing_required_field",
            obj_type,
            f"{window_name}.include_event_anchor",
            f"{window_name}.include_event_anchor is required"
        ))
    elif not isinstance(anchor_val, bool):
        blockers.append(Blocker(
            "invalid_boolean",
            obj_type,
            f"{window_name}.include_event_anchor",
            f"{window_name}.include_event_anchor must be a boolean, got {type(anchor_val).__name__}"
        ))

    # window_role — required non-empty string
    role_val = window.get("window_role")
    if role_val is None:
        blockers.append(Blocker(
            "missing_required_field",
            obj_type,
            f"{window_name}.window_role",
            f"{window_name}.window_role is required"
        ))
    elif not isinstance(role_val, str):
        blockers.append(Blocker(
            "invalid_type",
            obj_type,
            f"{window_name}.window_role",
            f"{window_name}.window_role must be a string, got {type(role_val).__name__}"
        ))
    elif role_val.strip() == "":
        blockers.append(Blocker(
            "missing_required_field",
            obj_type,
            f"{window_name}.window_role",
            f"{window_name}.window_role is required and cannot be empty"
        ))

    # Basic ordering: if both offsets are integers, reject start > end
    if start_offset is not None and end_offset is not None:
        if isinstance(start_offset, int) and isinstance(end_offset, int):
            if start_offset > end_offset:
                blockers.append(Blocker(
                    "invalid_value",
                    obj_type,
                    f"{window_name}",
                    f"{window_name}.start_offset ({start_offset}) must not exceed end_offset ({end_offset})"
                ))

    # TODO: enforce sign rules based on pre/post window role:
    #   pre_event_window: start_offset should be negative, end_offset should be <= 0
    #   post_event_window: start_offset may be 0 or positive, end_offset should be positive
    #   These checks are deferred to a future validator extension.
    # TODO: enforce include_event_anchor semantics and consistency across pre/post windows.


def validate_record(entry: Dict[str, Any]) -> List[Blocker]:
    """
    Validate a single EventStudySpec record (already parsed from JSON).
    Returns a list of Blocker objects.
    """
    blockers: List[Blocker] = []

    # 0. Root must be an object
    if not isinstance(entry, dict):
        blockers.append(Blocker(
            "invalid_object",
            "event_study_spec",
            "$",
            "EventStudySpec must be a JSON object"
        ))
        return blockers

    # 1. Required top-level fields — missing, null, or empty/whitespace-only string fails
    for field in REQUIRED_TOP_LEVEL:
        val = entry.get(field)
        if val is None:
            blockers.append(Blocker(
                "missing_required_field",
                "event_study_spec",
                field,
                f"{field} is required"
            ))
        elif isinstance(val, str) and val.strip() == "":
            blockers.append(Blocker(
                "missing_required_field",
                "event_study_spec",
                field,
                f"{field} is required and cannot be empty"
            ))

    # Cannot safely continue if required fields are missing
    if blockers:
        return blockers

    # 2. event_study_spec_id format
    evs_id = entry.get("event_study_spec_id", "")
    if not isinstance(evs_id, str):
        blockers.append(Blocker(
            "invalid_id_format",
            "event_study_spec",
            "event_study_spec_id",
            "event_study_spec_id must be a string"
        ))
    elif not ID_PATTERN_EVS.match(evs_id):
        blockers.append(Blocker(
            "invalid_id_format",
            "event_study_spec",
            "event_study_spec_id",
            f"event_study_spec_id '{evs_id}' does not match EVS-YYYY-NNNN format"
        ))

    # 3. event_study_version — must be integer >= 1, not boolean
    ver = entry.get("event_study_version")
    if ver is not None:
        if isinstance(ver, bool) or not isinstance(ver, int):
            blockers.append(Blocker(
                "invalid_type",
                "event_study_spec",
                "event_study_version",
                f"event_study_version must be an integer, got {type(ver).__name__}"
            ))
        elif ver < 1:
            blockers.append(Blocker(
                "invalid_value",
                "event_study_spec",
                "event_study_version",
                f"event_study_version must be >= 1, got {ver}"
            ))

    # 4. event_family enum
    _check_enum(entry, "event_family", EVENT_FAMILIES, blockers)

    # 5. event_source_refs — non-empty list of strings
    dmr = entry.get("event_source_refs")
    if dmr is not None:
        if not isinstance(dmr, list):
            blockers.append(Blocker(
                "invalid_list",
                "event_study_spec",
                "event_source_refs",
                f"event_source_refs must be a list, got {type(dmr).__name__}"
            ))
        elif len(dmr) == 0:
            blockers.append(Blocker(
                "invalid_list",
                "event_study_spec",
                "event_source_refs",
                "event_source_refs must have at least 1 item, got 0"
            ))
        else:
            for i, item in enumerate(dmr):
                if not isinstance(item, str):
                    blockers.append(Blocker(
                        "invalid_list_item_type",
                        "event_study_spec",
                        f"event_source_refs[{i}]",
                        f"event_source_refs items must be strings, got {type(item).__name__}"
                    ))

    # 6. event_anchor_policy enum
    _check_enum(entry, "event_anchor_policy", EVENT_ANCHOR_POLICIES, blockers)

    # 7. event_timestamp_policy enum
    _check_enum(entry, "event_timestamp_policy", EVENT_TIMESTAMP_POLICIES, blockers)

    # 8. decision_timestamp_policy enum
    _check_enum(entry, "decision_timestamp_policy", DECISION_TIMESTAMP_POLICIES, blockers)

    # 9. leakage_policy enum
    _check_enum(entry, "leakage_policy", LEAKAGE_POLICIES, blockers)

    # 10. event_deduplication_policy enum
    _check_enum(entry, "event_deduplication_policy", EVENT_DEDUPLICATION_POLICIES, blockers)

    # 11. event_collision_policy enum
    _check_enum(entry, "event_collision_policy", EVENT_COLLISION_POLICIES, blockers)

    # 12. missing_event_time_policy enum
    _check_enum(entry, "missing_event_time_policy", MISSING_EVENT_TIME_POLICIES, blockers)

    # 13. calendar_policy enum
    _check_enum(entry, "calendar_policy", CALENDAR_POLICIES, blockers)

    # 14. pre_event_window — required object with validated nested fields
    pre_window = entry.get("pre_event_window")
    if pre_window is not None:
        if not isinstance(pre_window, dict):
            blockers.append(Blocker(
                "invalid_object",
                "event_study_spec",
                "pre_event_window",
                f"pre_event_window must be an object, got {type(pre_window).__name__}"
            ))
        else:
            _check_window_object(pre_window, "pre_event_window", blockers)

    # 15. post_event_window — required object with validated nested fields
    post_window = entry.get("post_event_window")
    if post_window is not None:
        if not isinstance(post_window, dict):
            blockers.append(Blocker(
                "invalid_object",
                "event_study_spec",
                "post_event_window",
                f"post_event_window must be an object, got {type(post_window).__name__}"
            ))
        else:
            _check_window_object(post_window, "post_event_window", blockers)

    # TODO: Cross-window timing inequality checks (deferred to future validator extension):
    #   - Pre-event modes (before_event_publication, prior_session_close, same_session_open):
    #       Feature cutoff ≤ Decision timestamp < Event anchor
    #   - Post-publication/post-event modes (after_event_publication, next_session_open):
    #       Feature cutoff ≤ Event anchor ≤ Decision timestamp
    #   These checks require timestamp fields not yet present in the schema, and are
    #   intentionally deferred to a future validator extension that can access event data.
    # TODO: event_source_priority resolution behavior (deferred).
    # TODO: event_collision_policy and event_deduplication_policy resolution behavior (deferred).
    # TODO: event_family-specific timestamp requirements (deferred).

    # 16. reviewer — required object with name required
    reviewer = entry.get("reviewer")
    if reviewer is not None:
        if not isinstance(reviewer, dict):
            blockers.append(Blocker(
                "invalid_object",
                "event_study_spec",
                "reviewer",
                f"reviewer must be an object, got {type(reviewer).__name__}"
            ))
        else:
            reviewer_name = reviewer.get("name")
            if reviewer_name is None:
                blockers.append(Blocker(
                    "missing_required_field",
                    "event_study_spec",
                    "reviewer.name",
                    "reviewer.name is required"
                ))
            elif not isinstance(reviewer_name, str):
                blockers.append(Blocker(
                    "invalid_type",
                    "event_study_spec",
                    "reviewer.name",
                    f"reviewer.name must be a string, got {type(reviewer_name).__name__}"
                ))
            elif reviewer_name.strip() == "":
                blockers.append(Blocker(
                    "missing_required_field",
                    "event_study_spec",
                    "reviewer.name",
                    "reviewer.name is required and cannot be empty"
                ))

    # 17. Optional reference arrays
    _check_list_of_strings(entry, "domain_profile_refs", blockers)
    _check_list_of_strings(entry, "runner_output_refs", blockers)
    _check_list_of_strings(entry, "review_packet_refs", blockers)

    # outcome_spec_refs — list of OUT-YYYY-NNNN strings
    outcome_refs = entry.get("outcome_spec_refs")
    if outcome_refs is not None:
        if not isinstance(outcome_refs, list):
            blockers.append(Blocker(
                "invalid_list",
                "event_study_spec",
                "outcome_spec_refs",
                f"outcome_spec_refs must be a list, got {type(outcome_refs).__name__}"
            ))
        else:
            for i, item in enumerate(outcome_refs):
                if not isinstance(item, str):
                    blockers.append(Blocker(
                        "invalid_list_item_type",
                        "event_study_spec",
                        f"outcome_spec_refs[{i}]",
                        f"outcome_spec_refs items must be strings, got {type(item).__name__}"
                    ))
                elif not ID_PATTERN_OUT.match(item):
                    blockers.append(Blocker(
                        "invalid_id_format",
                        "event_study_spec",
                        f"outcome_spec_refs[{i}]",
                        f"outcome_spec_refs item '{item}' does not match OUT-YYYY-NNNN format"
                    ))

    # instrument_universe_refs — list of IUS-YYYY-NNNN strings
    ius_refs = entry.get("instrument_universe_refs")
    if ius_refs is not None:
        if not isinstance(ius_refs, list):
            blockers.append(Blocker(
                "invalid_list",
                "event_study_spec",
                "instrument_universe_refs",
                f"instrument_universe_refs must be a list, got {type(ius_refs).__name__}"
            ))
        else:
            for i, item in enumerate(ius_refs):
                if not isinstance(item, str):
                    blockers.append(Blocker(
                        "invalid_list_item_type",
                        "event_study_spec",
                        f"instrument_universe_refs[{i}]",
                        f"instrument_universe_refs items must be strings, got {type(item).__name__}"
                    ))
                elif not ID_PATTERN_IUS.match(item):
                    blockers.append(Blocker(
                        "invalid_id_format",
                        "event_study_spec",
                        f"instrument_universe_refs[{i}]",
                        f"instrument_universe_refs item '{item}' does not match IUS-YYYY-NNNN format"
                    ))

    # 18. extension_hooks — optional object, only allowed sub-fields
    ext_hooks = entry.get("extension_hooks")
    if ext_hooks is not None:
        if not isinstance(ext_hooks, dict):
            blockers.append(Blocker(
                "invalid_object",
                "event_study_spec",
                "extension_hooks",
                f"extension_hooks must be an object, got {type(ext_hooks).__name__}"
            ))
        else:
            for sub_key in ext_hooks:
                if sub_key not in EXTENSION_HOOKS_ALLOWED:
                    blockers.append(Blocker(
                        "invalid_field",
                        "event_study_spec",
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
                            "event_study_spec",
                            f"extension_hooks.{allowed_key}",
                            f"extension_hooks.{allowed_key} must be a list, got {type(sub_val).__name__}"
                        ))
                    else:
                        for i, item in enumerate(sub_val):
                            if not isinstance(item, str):
                                blockers.append(Blocker(
                                    "invalid_list_item_type",
                                    "event_study_spec",
                                    f"extension_hooks.{allowed_key}[{i}]",
                                    f"extension_hooks.{allowed_key} items must be strings, got {type(item).__name__}"
                                ))

    # 19. Root additionalProperties boundary — block undeclared top-level fields
    declared_fields = set(REQUIRED_TOP_LEVEL)
    optional_declared = {
        "event_type_filter", "event_importance_filter", "event_source_priority",
        "event_quality_filter", "timezone_policy", "trading_calendar_ref",
        "market_session_policy", "event_lag_policy", "announcement_status_policy",
        "domain_profile_refs", "outcome_spec_refs", "instrument_universe_refs",
        "runner_output_refs", "review_packet_refs", "extension_hooks", "notes"
    }
    declared_fields |= optional_declared

    for top_key in entry:
        if top_key not in declared_fields:
            blockers.append(Blocker(
                "invalid_field",
                "event_study_spec",
                top_key,
                f"'{top_key}' is not a declared field in EventStudySpec v1; "
                f"boundary fields such as delta_target, pnl, pbo_estimate are not allowed"
            ))

    return blockers


def validate_file(path: Path, format: str = "text") -> int:
    """
    Validate a single file. Returns 0 if valid, 1 if blockers found, 2 on error.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[ERROR] {path}: could not read file: {e}", file=sys.stderr)
        return 2

    try:
        entry = json.loads(text)
    except Exception as e:
        print(f"[ERROR] {path}: could not parse JSON: {e}", file=sys.stderr)
        return 2

    blockers = validate_record(entry)

    if not blockers:
        print(f"[OK] {path}")
        return 0

    print(f"[FAIL] {path}")
    for b in blockers:
        if format == "json":
            print(json.dumps(b.to_dict(), indent=2))
        else:
            print(f"  [{b.code}] {b.object_type}.{b.field}")
            print(f"        {b.message}")
    return 1


def main():
    args = parse_args()
    any_blockers = False
    any_error = False

    for path_str in args.files:
        path = Path(path_str)
        result = validate_file(path, args.format)
        if result == 1:
            any_blockers = True
        elif result == 2:
            any_error = True

    if any_error:
        sys.exit(2)
    elif any_blockers:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
