#!/usr/bin/env python3
"""
Local RunnerOutputSpec v1 validator.
Validates a RunnerOutput JSON artifact against the v1 schema and governance rules.
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Constants
RUNNER_OUTPUT_ID_PATTERN = re.compile(r"^RUN-[0-9]{4}-[0-9]{4}$")
EXP_ID_PATTERN = re.compile(r"^EXP-[0-9]{4}-[0-9]{4}$")
OUT_ID_PATTERN = re.compile(r"^OUT-[0-9]{4}-[0-9]{4}$")
IUS_ID_PATTERN = re.compile(r"^IUS-[0-9]{4}-[0-9]{4}$")
EVS_ID_PATTERN = re.compile(r"^EVS-[0-9]{4}-[0-9]{4}$")
OER_ID_PATTERN = re.compile(r"^OER-[0-9]{4}-[0-9]{4}$")
PEP_ID_PATTERN = re.compile(r"^PEP-[0-9]{4}-[0-9]{4}$")
SSM_ID_PATTERN = re.compile(r"^SSM-[0-9]{4}-[0-9]{4}$")
TRL_ID_PATTERN = re.compile(r"^TRL-[0-9]{4}-[0-9]{4}$")
MAS_ID_PATTERN = re.compile(r"^MAS-[0-9]{4}-[0-9]{4}$")

RUN_MODES = {"dry_run", "smoke_real_data", "backtest_real_data", "simulation", "replay", "custom"}
STATUSES = {"success", "partial", "failed_missing_data", "failed_validation", "failed_runtime", "cancelled"}
VALIDATION_STATUSES = {"pass", "fail", "warn", "skipped"}
AUDIT_RESULTS = {"pass", "fail", "warn", "skipped"}
SEVERITIES = {"blocker", "warning", "info"}
OUTPUT_ROLES = {"evidence", "audit_report", "failure_report", "intermediate", "debug", "custom"}
FAILURE_TYPES = {"missing_data", "validation_error", "runtime_error", "timeout", "cancelled", "unsupported_config", "custom"}

# Required top-level fields
REQUIRED_FIELDS = [
    "runner_output_id",
    "runner_output_version",
    "run_id",
    "run_mode",
    "status",
    "runner_name",
    "runner_version",
    "experiment_spec_ref",
    "input_artifact_refs",
    "data_manifest_refs",
    "run_config_hash",
    "started_at",
    # completed_at is nullable per schema (type ["string", "null"]),
    # so it is checked separately below — do NOT include in REQUIRED_FIELDS
    # "completed_at",
    "audit_summary",
    "output_manifest",
    "created_at",
    "run_owner",
]

# Nullable top-level fields — present key is required but null value is valid
NULLABLE_FIELDS = {"completed_at"}

# All allowed top-level fields (additionalProperties: false at root)
ALLOWED_ROOT_FIELDS = {
    "runner_output_id",
    "runner_output_version",
    "run_id",
    "run_mode",
    "status",
    "runner_name",
    "runner_version",
    "experiment_spec_ref",
    "input_artifact_refs",
    "data_manifest_refs",
    "run_config_hash",
    "started_at",
    "completed_at",
    "audit_summary",
    "output_manifest",
    "created_at",
    "run_owner",
    # Optional
    "reviewer",
    "outcome_spec_refs",
    "instrument_universe_refs",
    "event_study_spec_refs",
    "options_event_risk_refs",
    "preearnings_profile_refs",
    "domain_profile_refs",
    "search_space_manifest_ref",
    "trial_ledger_ref",
    "model_assessment_refs",
    "review_packet_refs",
    "failure_summary",
    "partial_summary",
    "missing_data_summary",
    "dropped_rows_summary",
    "leakage_checks_summary",
    "row_counts",
    "event_counts",
    "instrument_counts",
    "execution_environment",
    "code_version_ref",
    "git_commit",
    "command_line",
    "output_paths",
    "artifact_refs",
    "extension_hooks",
    "notes",
}

# Allowed fields in input_artifact_refs items
ALLOWED_IAR_FIELDS = {
    "artifact_type",
    "artifact_id",
    "artifact_path",
    "schema_ref",
    "validator_ref",
    "content_hash",
    "validation_status",
    "validated_at",
}

# Allowed fields in output_manifest items
ALLOWED_OM_FIELDS = {
    "output_role",
    "output_path",
    "row_count",
    "content_hash",
    "created_at",
    "format",
    "description",
    "contains_private_data",
    "publishable",
}

# Allowed fields in extension_hooks
ALLOWED_EXT_HOOKS_FIELDS = {
    "domain_profile_extension_refs",
    "runner_extension_refs",
    "audit_extension_refs",
    "output_extension_refs",
    "review_extension_refs",
}


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
    p = argparse.ArgumentParser(description="Validate a RunnerOutputSpec v1 JSON artifact.")
    p.add_argument("file_path", help="Path to RunnerOutput JSON file.")
    p.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="Output format (default: text)"
    )
    return p.parse_args()


def is_iso8601_datetime(value: str) -> bool:
    """Check if value is a valid ISO8601 datetime string (not date-only).

    Requires a time component (T or HH:MM) to distinguish from date-only strings.
    """
    if not isinstance(value, str):
        return False
    # Must contain a time separator (T or space+time) to be a full datetime
    if "T" not in value and not re.search(r" \d{2}:\d{2}", value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (ValueError, TypeError):
        return False


def validate_artifact_ref(item: Dict[str, Any], index: int, blockers: List[Blocker]) -> None:
    """Validate a single input_artifact_refs item."""
    required_sub = ["artifact_type", "artifact_id", "content_hash", "validation_status"]
    for field in required_sub:
        if field not in item or item.get(field) is None or item.get(field) == "":
            blockers.append(Blocker(
                "missing_required_field",
                f"input_artifact_refs[{index}]",
                field,
                f"{field} is required in input_artifact_refs[{index}]"
            ))

    if item.get("validation_status") and item["validation_status"] not in VALIDATION_STATUSES:
        blockers.append(Blocker(
            "invalid_enum",
            f"input_artifact_refs[{index}]",
            "validation_status",
            f"validation_status {repr(item['validation_status'])} not in {VALIDATION_STATUSES}"
        ))

    if item.get("validated_at") is not None and not is_iso8601_datetime(item["validated_at"]):
        blockers.append(Blocker(
            "invalid_datetime",
            f"input_artifact_refs[{index}]",
            "validated_at",
            "validated_at must be ISO8601 datetime"
        ))

    # Check for unknown fields
    for field in item:
        if field not in ALLOWED_IAR_FIELDS:
            blockers.append(Blocker(
                "unknown_field",
                f"input_artifact_refs[{index}]",
                field,
                f"unknown field '{field}' is not permitted in input_artifact_refs[{index}]"
            ))


def validate_audit(item: Dict[str, Any], index: int, blockers: List[Blocker]) -> None:
    """Validate a single audit in audit_summary.audits."""
    # Check for unknown fields first (additionalProperties: false)
    AUDIT_ALLOWED_FIELDS = {
        "audit_name", "audit_result", "severity",
        "blocker_count", "warning_count", "details_ref", "created_at",
    }
    for field in item:
        if field not in AUDIT_ALLOWED_FIELDS:
            blockers.append(Blocker(
                "unknown_field",
                f"audits[{index}]",
                field,
                f"unknown field '{field}' is not permitted in audits[{index}]"
            ))

    required_sub = ["audit_name", "audit_result", "severity", "blocker_count", "warning_count", "created_at"]
    for field in required_sub:
        if field not in item or item.get(field) is None:
            blockers.append(Blocker(
                "missing_required_field",
                f"audits[{index}]",
                field,
                f"{field} is required in audits[{index}]"
            ))

    if item.get("audit_result") and item["audit_result"] not in AUDIT_RESULTS:
        blockers.append(Blocker(
            "invalid_enum",
            f"audits[{index}]",
            "audit_result",
            f"audit_result {repr(item['audit_result'])} not in {AUDIT_RESULTS}"
        ))

    if item.get("severity") and item["severity"] not in SEVERITIES:
        blockers.append(Blocker(
            "invalid_enum",
            f"audits[{index}]",
            "severity",
            f"severity {repr(item['severity'])} not in {SEVERITIES}"
        ))

    if item.get("created_at") and not is_iso8601_datetime(item["created_at"]):
        blockers.append(Blocker(
            "invalid_datetime",
            f"audits[{index}]",
            "created_at",
            "created_at must be ISO8601 datetime"
        ))


def validate_output_manifest_item(item: Dict[str, Any], index: int, blockers: List[Blocker]) -> None:
    """Validate a single output_manifest item."""
    required_sub = ["output_role", "output_path", "content_hash", "created_at", "format", "description", "contains_private_data", "publishable"]
    for field in required_sub:
        if field not in item or item.get(field) is None or (isinstance(item.get(field), str) and item.get(field) == ""):
            blockers.append(Blocker(
                "missing_required_field",
                f"output_manifest[{index}]",
                field,
                f"{field} is required in output_manifest[{index}]"
            ))

    if item.get("output_role") and item["output_role"] not in OUTPUT_ROLES:
        blockers.append(Blocker(
            "invalid_enum",
            f"output_manifest[{index}]",
            "output_role",
            f"output_role {repr(item['output_role'])} not in {OUTPUT_ROLES}"
        ))

    if item.get("created_at") and not is_iso8601_datetime(item["created_at"]):
        blockers.append(Blocker(
            "invalid_datetime",
            f"output_manifest[{index}]",
            "created_at",
            "created_at must be ISO8601 datetime"
        ))

    # Boolean type check
    if "contains_private_data" in item and not isinstance(item["contains_private_data"], bool):
        blockers.append(Blocker(
            "invalid_type",
            f"output_manifest[{index}]",
            "contains_private_data",
            "contains_private_data must be a boolean"
        ))
    if "publishable" in item and not isinstance(item["publishable"], bool):
        blockers.append(Blocker(
            "invalid_type",
            f"output_manifest[{index}]",
            "publishable",
            "publishable must be a boolean"
        ))

    # Check for unknown fields
    for field in item:
        if field not in ALLOWED_OM_FIELDS:
            blockers.append(Blocker(
                "unknown_field",
                f"output_manifest[{index}]",
                field,
                f"unknown field '{field}' is not permitted in output_manifest[{index}]"
            ))


def validate_extension_hooks(ext: Dict[str, Any], blockers: List[Blocker]) -> None:
    """Validate extension_hooks object."""
    for field in ext:
        if field not in ALLOWED_EXT_HOOKS_FIELDS:
            blockers.append(Blocker(
                "unknown_field",
                "extension_hooks",
                field,
                f"unknown field '{field}' is not permitted in extension_hooks"
            ))

    ext_arrays = {
        "domain_profile_extension_refs": 1,
        "runner_extension_refs": 1,
        "audit_extension_refs": 1,
        "output_extension_refs": 1,
        "review_extension_refs": 1,
    }
    for name, min_items in ext_arrays.items():
        arr = ext.get(name)
        if arr is not None:
            if not isinstance(arr, list) or len(arr) < min_items:
                blockers.append(Blocker(
                    "min_items_violated",
                    "extension_hooks",
                    name,
                    f"{name} must be an array with at least {min_items} item(s)"
                ))


def validate_refs_array(field_name: str, items: List[str], pattern, min_items: int, blockers: List[Blocker]) -> None:
    """Validate a typed-reference array field."""
    if not isinstance(items, list):
        blockers.append(Blocker(
            "invalid_type",
            "runner_output",
            field_name,
            f"{field_name} must be an array"
        ))
        return
    if len(items) < min_items:
        blockers.append(Blocker(
            "min_items_violated",
            "runner_output",
            field_name,
            f"{field_name} must have at least {min_items} item(s)"
        ))
    for i, item in enumerate(items):
        if not isinstance(item, str) or item == "":
            blockers.append(Blocker(
                "invalid_ref_format",
                field_name,
                f"{field_name}[{i}]",
                f"{field_name}[{i}] must be a non-empty string"
            ))
        elif not pattern.match(item):
            blockers.append(Blocker(
                "invalid_ref_format",
                field_name,
                f"{field_name}[{i}]",
                f"{field_name}[{i}] value {repr(item)} does not match required format"
            ))


def validate(entry: Dict[str, Any]) -> List[Blocker]:
    blockers: List[Blocker] = []

    # 0. Root must be an object
    if not isinstance(entry, dict):
        blockers.append(Blocker(
            "invalid_object",
            "runner_output",
            "$",
            "RunnerOutput must be a JSON object"
        ))
        return blockers

    # 0b. Reject unknown top-level fields (additionalProperties: false)
    for field in entry:
        if field not in ALLOWED_ROOT_FIELDS:
            blockers.append(Blocker(
                "unknown_field",
                "runner_output",
                field,
                f"unknown field '{field}' is not permitted at root level"
            ))

    # 1. Required top-level fields
    for field in REQUIRED_FIELDS:
        if field not in entry or entry.get(field) is None or entry.get(field) == "":
            blockers.append(Blocker(
                "missing_required_field",
                "runner_output",
                field,
                f"{field} is required"
            ))

    # 1b. Nullable fields must be present (key must exist) but null is valid
    for field in NULLABLE_FIELDS:
        if field not in entry:
            blockers.append(Blocker(
                "missing_required_field",
                "runner_output",
                field,
                f"{field} is required"
            ))

    if blockers:
        return blockers  # cannot continue safely if required fields are missing

    # 2. runner_output_id format
    rid = entry.get("runner_output_id", "")
    if not RUNNER_OUTPUT_ID_PATTERN.match(rid):
        blockers.append(Blocker(
            "invalid_id_format",
            "runner_output",
            "runner_output_id",
            f"runner_output_id {repr(rid)} does not match RUN-YYYY-NNNN format"
        ))

    # 3. runner_output_version must be "1.0"
    if entry.get("runner_output_version") != "1.0":
        blockers.append(Blocker(
            "invalid_const",
            "runner_output",
            "runner_output_version",
            f"runner_output_version must be '1.0', got {repr(entry.get('runner_output_version'))}"
        ))

    # 4. run_id non-empty string
    run_id = entry.get("run_id", "")
    if not isinstance(run_id, str) or run_id == "":
        blockers.append(Blocker(
            "missing_required_field",
            "runner_output",
            "run_id",
            "run_id must be a non-empty string"
        ))

    # 5. run_mode enum
    run_mode = entry.get("run_mode", "")
    if run_mode and run_mode not in RUN_MODES:
        blockers.append(Blocker(
            "invalid_enum",
            "runner_output",
            "run_mode",
            f"run_mode {repr(run_mode)} not in {RUN_MODES}"
        ))

    # 6. status enum
    status = entry.get("status", "")
    if status and status not in STATUSES:
        blockers.append(Blocker(
            "invalid_enum",
            "runner_output",
            "status",
            f"status {repr(status)} not in {STATUSES}"
        ))

    # 7. runner_name non-empty
    if not entry.get("runner_name"):
        blockers.append(Blocker(
            "missing_required_field",
            "runner_output",
            "runner_name",
            "runner_name is required"
        ))

    # 8. runner_version non-empty
    if not entry.get("runner_version"):
        blockers.append(Blocker(
            "missing_required_field",
            "runner_output",
            "runner_version",
            "runner_version is required"
        ))

    # 9. experiment_spec_ref format
    exp_ref = entry.get("experiment_spec_ref", "")
    if exp_ref and not EXP_ID_PATTERN.match(exp_ref):
        blockers.append(Blocker(
            "invalid_ref_format",
            "runner_output",
            "experiment_spec_ref",
            f"experiment_spec_ref {repr(exp_ref)} does not match EXP-YYYY-NNNN format"
        ))

    # 10. input_artifact_refs — non-empty array of objects
    iar = entry.get("input_artifact_refs")
    if iar is None:
        blockers.append(Blocker(
            "missing_required_field",
            "runner_output",
            "input_artifact_refs",
            "input_artifact_refs is required"
        ))
    elif not isinstance(iar, list) or len(iar) == 0:
        blockers.append(Blocker(
            "min_items_violated",
            "runner_output",
            "input_artifact_refs",
            "input_artifact_refs must be a non-empty array"
        ))
    else:
        for i, item in enumerate(iar):
            if not isinstance(item, dict):
                blockers.append(Blocker(
                    "invalid_object",
                    "runner_output",
                    f"input_artifact_refs[{i}]",
                    f"input_artifact_refs[{i}] must be an object"
                ))
            else:
                validate_artifact_ref(item, i, blockers)

    # 11. data_manifest_refs — non-empty array of non-empty strings
    dmr = entry.get("data_manifest_refs")
    if dmr is None:
        blockers.append(Blocker(
            "missing_required_field",
            "runner_output",
            "data_manifest_refs",
            "data_manifest_refs is required"
        ))
    elif not isinstance(dmr, list) or len(dmr) == 0:
        blockers.append(Blocker(
            "min_items_violated",
            "runner_output",
            "data_manifest_refs",
            "data_manifest_refs must be a non-empty array"
        ))
    else:
        for i, item in enumerate(dmr):
            if not isinstance(item, str) or item == "":
                blockers.append(Blocker(
                    "invalid_ref_format",
                    "runner_output",
                    f"data_manifest_refs[{i}]",
                    f"data_manifest_refs[{i}] must be a non-empty string"
                ))

    # 12. run_config_hash non-empty string
    if not entry.get("run_config_hash"):
        blockers.append(Blocker(
            "missing_required_field",
            "runner_output",
            "run_config_hash",
            "run_config_hash is required"
        ))

    # 13. started_at ISO8601 datetime
    started_at = entry.get("started_at", "")
    if started_at and not is_iso8601_datetime(started_at):
        blockers.append(Blocker(
            "invalid_datetime",
            "runner_output",
            "started_at",
            "started_at must be an ISO8601 datetime"
        ))

    # 14. completed_at ISO8601 datetime (nullable)
    completed_at = entry.get("completed_at")
    if completed_at is not None and not is_iso8601_datetime(completed_at):
        blockers.append(Blocker(
            "invalid_datetime",
            "runner_output",
            "completed_at",
            "completed_at must be an ISO8601 datetime or null"
        ))

    # 15. audit_summary
    audit_summary = entry.get("audit_summary")
    if audit_summary is None:
        blockers.append(Blocker(
            "missing_required_field",
            "runner_output",
            "audit_summary",
            "audit_summary is required"
        ))
    elif not isinstance(audit_summary, dict):
        blockers.append(Blocker(
            "invalid_object",
            "runner_output",
            "audit_summary",
            "audit_summary must be an object"
        ))
    else:
        # Check for unknown fields in audit_summary (additionalProperties: false)
        AUDIT_SUMMARY_ALLOWED_FIELDS = {
            "overall_result", "blocker_count", "warning_count", "audits",
        }
        for field in audit_summary:
            if field not in AUDIT_SUMMARY_ALLOWED_FIELDS:
                blockers.append(Blocker(
                    "unknown_field",
                    "audit_summary",
                    field,
                    f"unknown field '{field}' is not permitted in audit_summary"
                ))

        for field in ["overall_result", "blocker_count", "warning_count", "audits"]:
            if field not in audit_summary or audit_summary.get(field) is None:
                blockers.append(Blocker(
                    "missing_required_field",
                    "audit_summary",
                    field,
                    f"{field} is required in audit_summary"
                ))

        if audit_summary.get("overall_result") not in AUDIT_RESULTS:
            blockers.append(Blocker(
                "invalid_enum",
                "audit_summary",
                "overall_result",
                f"overall_result {repr(audit_summary.get('overall_result'))} not in {AUDIT_RESULTS}"
            ))

        bc = audit_summary.get("blocker_count")
        if bc is not None and (not isinstance(bc, int) or bc < 0):
            blockers.append(Blocker(
                "invalid_type",
                "audit_summary",
                "blocker_count",
                "blocker_count must be a non-negative integer"
            ))

        wc = audit_summary.get("warning_count")
        if wc is not None and (not isinstance(wc, int) or wc < 0):
            blockers.append(Blocker(
                "invalid_type",
                "audit_summary",
                "warning_count",
                "warning_count must be a non-negative integer"
            ))

        audits = audit_summary.get("audits", [])
        if not isinstance(audits, list) or len(audits) == 0:
            blockers.append(Blocker(
                "min_items_violated",
                "audit_summary",
                "audits",
                "audits must be a non-empty array"
            ))
        else:
            for i, audit in enumerate(audits):
                if not isinstance(audit, dict):
                    blockers.append(Blocker(
                        "invalid_object",
                        "audit_summary",
                        f"audits[{i}]",
                        f"audits[{i}] must be an object"
                    ))
                else:
                    validate_audit(audit, i, blockers)

    # 16. output_manifest
    om = entry.get("output_manifest")
    if om is None:
        blockers.append(Blocker(
            "missing_required_field",
            "runner_output",
            "output_manifest",
            "output_manifest is required"
        ))
    elif not isinstance(om, list) or len(om) == 0:
        blockers.append(Blocker(
            "min_items_violated",
            "runner_output",
            "output_manifest",
            "output_manifest must be a non-empty array"
        ))
    else:
        for i, item in enumerate(om):
            if not isinstance(item, dict):
                blockers.append(Blocker(
                    "invalid_object",
                    "runner_output",
                    f"output_manifest[{i}]",
                    f"output_manifest[{i}] must be an object"
                ))
            else:
                validate_output_manifest_item(item, i, blockers)

    # 17. created_at ISO8601 datetime
    created_at = entry.get("created_at", "")
    if created_at and not is_iso8601_datetime(created_at):
        blockers.append(Blocker(
            "invalid_datetime",
            "runner_output",
            "created_at",
            "created_at must be an ISO8601 datetime"
        ))

    # 18. run_owner non-empty
    if not entry.get("run_owner"):
        blockers.append(Blocker(
            "missing_required_field",
            "runner_output",
            "run_owner",
            "run_owner is required"
        ))

    # 19. failure_summary consistency with status
    failure_summary = entry.get("failure_summary")
    failure_status = failure_summary.get("status") if isinstance(failure_summary, dict) else None
    if status in {"failed_missing_data", "failed_validation", "failed_runtime", "cancelled"}:
        if not isinstance(failure_summary, dict):
            blockers.append(Blocker(
                "missing_required_field",
                "runner_output",
                "failure_summary",
                f"failure_summary is required when status={repr(status)}"
            ))
        else:
            # Check for unknown fields in failure_summary (additionalProperties: false)
            FAILURE_SUMMARY_ALLOWED_FIELDS = {
                "failure_type", "status", "failed_check",
                "blocker_summary", "missing_data_summary_ref",
                "details_ref", "created_at",
            }
            for field in failure_summary:
                if field not in FAILURE_SUMMARY_ALLOWED_FIELDS:
                    blockers.append(Blocker(
                        "unknown_field",
                        "failure_summary",
                        field,
                        f"unknown field '{field}' is not permitted in failure_summary"
                    ))

            for field in ["failure_type", "status", "blocker_summary", "created_at"]:
                if failure_summary.get(field) is None or failure_summary.get(field) == "":
                    blockers.append(Blocker(
                        "missing_required_field",
                        "failure_summary",
                        field,
                        f"{field} is required in failure_summary when status={repr(status)}"
                    ))
            ft = failure_summary.get("failure_type")
            if ft and ft not in FAILURE_TYPES:
                blockers.append(Blocker(
                    "invalid_enum",
                    "failure_summary",
                    "failure_type",
                    f"failure_type {repr(ft)} not in {FAILURE_TYPES}"
                ))
            if failure_status and failure_status not in STATUSES:
                blockers.append(Blocker(
                    "invalid_enum",
                    "failure_summary",
                    "status",
                    f"failure_summary.status {repr(failure_status)} not in {STATUSES}"
                ))
            # failure_summary.created_at datetime
            fs_created = failure_summary.get("created_at")
            if fs_created and not is_iso8601_datetime(fs_created):
                blockers.append(Blocker(
                    "invalid_datetime",
                    "failure_summary",
                    "created_at",
                    "failure_summary.created_at must be ISO8601 datetime"
                ))
    else:
        if isinstance(failure_summary, dict):
            blockers.append(Blocker(
                "unexpected_field",
                "runner_output",
                "failure_summary",
                f"failure_summary must be null when status={repr(status)}, not an object"
            ))

    # 20. partial_summary consistency with status
    partial_summary = entry.get("partial_summary")
    if status == "partial":
        if not isinstance(partial_summary, dict):
            blockers.append(Blocker(
                "missing_required_field",
                "runner_output",
                "partial_summary",
                "partial_summary is required when status=partial"
            ))
        else:
            ps_created = partial_summary.get("created_at") if isinstance(partial_summary, dict) else None
            if ps_created and not is_iso8601_datetime(ps_created):
                blockers.append(Blocker(
                    "invalid_datetime",
                    "partial_summary",
                    "created_at",
                    "partial_summary.created_at must be ISO8601 datetime"
                ))
    else:
        if isinstance(partial_summary, dict):
            blockers.append(Blocker(
                "unexpected_field",
                "runner_output",
                "partial_summary",
                f"partial_summary must be null when status={repr(status)}, not an object"
            ))

    # 21. Ref arrays
    ref_arrays = {
        "outcome_spec_refs": (OUT_ID_PATTERN, 1),
        "instrument_universe_refs": (IUS_ID_PATTERN, 1),
        "event_study_spec_refs": (EVS_ID_PATTERN, 1),
        "options_event_risk_refs": (OER_ID_PATTERN, 1),
        "preearnings_profile_refs": (PEP_ID_PATTERN, 1),
        "model_assessment_refs": (MAS_ID_PATTERN, 1),
    }
    for field_name, (pattern, min_items) in ref_arrays.items():
        items = entry.get(field_name)
        if items is not None:
            validate_refs_array(field_name, items, pattern, min_items, blockers)

    # 22. search_space_manifest_ref format (nullable)
    ssm_ref = entry.get("search_space_manifest_ref")
    if ssm_ref is not None and not SSM_ID_PATTERN.match(ssm_ref):
        blockers.append(Blocker(
            "invalid_ref_format",
            "runner_output",
            "search_space_manifest_ref",
            f"search_space_manifest_ref {repr(ssm_ref)} does not match SSM-YYYY-NNNN format"
        ))

    # 23. trial_ledger_ref format (nullable)
    trl_ref = entry.get("trial_ledger_ref")
    if trl_ref is not None and not TRL_ID_PATTERN.match(trl_ref):
        blockers.append(Blocker(
            "invalid_ref_format",
            "runner_output",
            "trial_ledger_ref",
            f"trial_ledger_ref {repr(trl_ref)} does not match TRL-YYYY-NNNN format"
        ))

    # 24. extension_hooks validation
    ext_hooks = entry.get("extension_hooks")
    if ext_hooks is not None:
        if not isinstance(ext_hooks, dict):
            blockers.append(Blocker(
                "invalid_object",
                "runner_output",
                "extension_hooks",
                "extension_hooks must be an object or null"
            ))
        else:
            validate_extension_hooks(ext_hooks, blockers)

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
