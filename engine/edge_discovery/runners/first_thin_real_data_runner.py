"""
First thin real-data runner slice — dry-run CLI skeleton.

Scope:
- Reads an ExperimentSpec JSON file.
- Optionally reads a DataManifest JSON file and validates it.
- Validates governance inputs (experiment spec structural validation only; no
  real backtest execution, no registry writes, no live trading).
- Emits a RunnerOutput v1 artifact as JSON.

No autonomous search, no Bayesian optimization, no genetic programming,
no live trading, no production execution, no registry mutation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.edge_discovery.data_manifest import (
    load_dataset_manifest,
    validate_dataset_manifest,
    DatasetManifest,
    DatasetValidationResult,
)
from engine.edge_discovery.runners.observation_table import (
    _count_csv_rows,
    _parse_required_columns,
    _normalize_optional_column_name,
    _summarize_observation_table_canonical,
    _summarize_observation_close_returns,
    _summarize_observation_missing_values,
    _summarize_observation_duplicate_rows,
    _summarize_observation_date_coverage,
    _read_csv_header,
    _validate_observation_table_columns,
)
from engine.edge_discovery.runners.runner_artifacts import (
    GovernanceRejection,
    UnsupportedConfig,
    _build_failure_summary,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RUNNER_NAME_DEFAULT = "first_thin_real_data_runner"
RUNNER_VERSION_DEFAULT = "0.1.0"
RUNNER_OUTPUT_ID_DEFAULT = "RUN-2026-0001"
RUNNER_OUTPUT_VERSION = "1.0"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "runner_output_spec_v1.schema.json"

# Stable placeholder for data_manifest_refs when no real DataManifest exists
# in this dry-run skeleton. Satisfies schema minItems: 1 requirement.
DRY_RUN_DATA_MANIFEST_PLACEHOLDER = "dry_run_no_data_manifest"

# Governance stop-rule field names — must all be false in prohibited_modes
GOVERNANCE_STOP_RULE_FIELDS = (
    "autonomous_search",
    "bayesian_optimization",
    "genetic_programming",
    "automated_promotion",
    "automated_registry_mutation",
    "live_trading",
    "production_execution",
    "gcru_integration",
)


# ---------------------------------------------------------------------------
# Artifact building
# ---------------------------------------------------------------------------

def _compute_run_config_hash(
    experiment_spec_path: Path,
    data_manifest_path: Path | None = None,
    required_observation_columns: list[str] | None = None,
    observation_date_column: str | None = None,
    observation_symbol_column: str | None = None,
    observation_close_column: str | None = None,
    observation_missing_value_columns: list[str] | None = None,
    trial_accounting_summary: dict | None = None,
) -> str:
    """
    Compute a deterministic SHA-256 hex digest of the run configuration.

    When data_manifest_path is None, the hash is computed from the
    experiment spec canonical JSON bytes only.

    When data_manifest_path is provided, the hash is computed from the
    concatenation of the experiment spec canonical JSON bytes plus the
    DataManifest file bytes. This ensures that changing the DataManifest
    content changes the run_config_hash and thus the run_id.

    When required_observation_columns is provided, it is normalized
    (sorted, stripped) and appended to the hash input so that different
    column requirements produce different run_config_hash values.

    When observation_date_column is provided, it is stripped and appended
    to the hash input. When observation_symbol_column is provided, it is
    When observation_close_column is provided, it is stripped and appended
    to the hash input.

    When observation_missing_value_columns is provided, it is normalized
    (sorted, stripped) and appended to the hash input so that different
    column sets produce different run_config_hash values.

    When trial_accounting_summary is provided, its schema-normalized JSON is
    appended to the hash input so trial-accounting metadata changes produce
    distinct run_config_hash/run_id values.

    Whitespace variation does not affect the hash.

    ``observation_date_column``, ``observation_symbol_column``, and
    ``observation_close_column`` participate in the hash so that changing
    which columns are used for the canonical or return summary produces a
    different run_id.
    """
    with open(experiment_spec_path, "rb") as fh:
        spec_raw = json.loads(fh.read().decode("utf-8"))
    spec_canonical = json.dumps(spec_raw, sort_keys=True, separators=(",", ":")).encode("utf-8")

    if data_manifest_path is not None:
        with open(data_manifest_path, "rb") as fh:
            dm_bytes = fh.read()
        combined = spec_canonical + b"\n" + dm_bytes
    else:
        combined = spec_canonical

    # Incorporate required_observation_columns into hash when present.
    # Strip leading/trailing whitespace per token; preserve internal whitespace.
    # "close price" and "closeprice" are distinct. " close " and "close" are same.
    if required_observation_columns is not None and len(required_observation_columns) > 0:
        normalized = json.dumps(
            sorted(c.strip() for c in required_observation_columns),
            separators=(",", ":"),
        ).encode("utf-8")
        combined = combined + b"\n" + normalized

    # observation_date_column: strip, include in hash if non-None
    if observation_date_column is not None and observation_date_column.strip():
        date_col_normalized = observation_date_column.strip()
        combined = combined + b"\ndate_col:" + date_col_normalized.encode("utf-8")

    # observation_symbol_column: strip, include in hash if non-None
    if observation_symbol_column is not None and observation_symbol_column.strip():
        symbol_col_normalized = observation_symbol_column.strip()
        combined = combined + b"\nsymbol_col:" + symbol_col_normalized.encode("utf-8")

    # observation_close_column: strip, include in hash if non-None
    if observation_close_column is not None and observation_close_column.strip():
        close_col_normalized = observation_close_column.strip()
        combined = combined + b"\nclose_col:" + close_col_normalized.encode("utf-8")

    # observation_missing_value_columns: strip each, sorted, include in hash
    if (
        observation_missing_value_columns is not None
        and len(observation_missing_value_columns) > 0
    ):
        normalized = json.dumps(
            sorted(c.strip() for c in observation_missing_value_columns),
            separators=(",", ":"),
        ).encode("utf-8")
        combined = combined + b"\nmissing_val_cols:" + normalized

    # trial_accounting_summary: canonical JSON, include in hash if emitted
    if trial_accounting_summary is not None:
        normalized = json.dumps(
            trial_accounting_summary,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        combined = combined + b"\ntrial_accounting_summary:" + normalized

    return hashlib.sha256(combined).hexdigest()


def _compute_run_id(run_config_hash: str) -> str:
    """Derive a stable run_id from the run_config_hash (first 16 hex chars)."""
    return run_config_hash[:16]


def _compute_content_hash(file_path: Path) -> str:
    """Compute SHA-256 hex digest of a file's contents."""
    with open(file_path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _utc_now() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_experiment_spec(path: Path) -> dict:
    """Load and parse an experiment spec JSON file."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _check_experiment_spec_id(experiment_spec: dict) -> None:
    """
    Validate that experiment_spec contains a top-level experiment_id field.

    Raises ValueError if the field is missing or empty.
    """
    exp_id = experiment_spec.get("experiment_id", "")
    if not isinstance(exp_id, str) or not exp_id.strip():
        raise ValueError(
            "experiment_spec is missing required field 'experiment_id' or it is empty"
        )


# ---------------------------------------------------------------------------
# DataManifest resolution
# ---------------------------------------------------------------------------

def load_data_manifest_for_runner(
    data_manifest_path: Path,
    base_dir: Path | None = None,
) -> DatasetManifest:
    """
    Load and validate a DataManifest JSON file for the runner.

    Parameters
    ----------
    data_manifest_path : Path
        Path to the DataManifest JSON file.
    base_dir : Path | None
        Base directory for path containment validation. If None, the
        parent directory of the DataManifest file is used.

    Returns
    -------
    DatasetManifest
        The loaded manifest.

    Raises
    ------
    FileNotFoundError
        If the DataManifest file does not exist.
    ValueError
        If the DataManifest JSON is malformed or fails validation
        (e.g. path escape, missing required fields, invalid type).
    """
    manifest = load_dataset_manifest(data_manifest_path)

    # Use parent directory of manifest file as base_dir if not provided
    effective_base_dir = base_dir if base_dir is not None else data_manifest_path.parent

    validation_result = validate_dataset_manifest(manifest, base_dir=effective_base_dir)

    if not validation_result.ok:
        errors_str = "; ".join(validation_result.errors)
        raise ValueError(
            f"DataManifest validation failed for dataset_id="
            f"'{manifest.dataset_id}': {errors_str}"
        )
    return manifest


# ---------------------------------------------------------------------------
# Observation-table column validation helpers — moved to observation_table.py
# ---------------------------------------------------------------------------
# (functions removed: _count_csv_rows, _parse_required_columns,
#  _normalize_optional_column_name, _summarize_observation_table_canonical,
#  _summarize_observation_close_returns, _read_csv_header,
#  _validate_observation_table_columns — imported from observation_table)


# ---------------------------------------------------------------------------
# DataManifest summarization
# ---------------------------------------------------------------------------

def _summarize_data_manifest_for_runner(
    manifest: DatasetManifest,
    manifest_path: Path,
    base_dir: Path,
) -> dict:
    """
    Build a minimal metadata dict for a DataManifest, suitable for inclusion
    in ``input_artifact_refs``.

    Parameters
    ----------
    manifest : DatasetManifest
        The validated DataManifest.
    manifest_path : Path
        Path to the DataManifest JSON file on disk.
    base_dir : Path
        The base directory used for path containment validation.

    Returns
    -------
    dict
        A dict with artifact_type, artifact_id, content_hash, validation_status,
        format, description, and dataset_path metadata. No output_manifest entry
        is produced here; the DataManifest metadata lives in input_artifact_refs.
    """
    manifest_content_hash = _compute_content_hash(manifest_path)

    # Resolve the dataset path against base_dir (same policy as validate_dataset_manifest)
    raw_path = Path(manifest.path)
    if raw_path.is_absolute():
        dataset_resolved = raw_path
    else:
        dataset_resolved = (base_dir / raw_path).resolve()

    dataset_exists = dataset_resolved.exists()
    dataset_format = manifest.format or "unknown"

    # Populate row_count only for CSV (cheap, safe, standard library only)
    row_count: int | None = None
    if manifest.source_kind.value == "local_csv" and dataset_exists and dataset_resolved.is_file():
        row_count = _count_csv_rows(dataset_resolved)

    # Build description string with metadata (safe — description is schema-valid string field)
    description_parts = [
        f"DataManifest dataset_id={manifest.dataset_id}",
        f"role={manifest.role.value}",
        f"source_kind={manifest.source_kind.value}",
        f"dataset_path={str(dataset_resolved)}",
        f"dataset_exists={dataset_exists}",
        f"format={dataset_format}",
    ]
    if row_count is not None:
        description_parts.append(f"row_count={row_count}")
    else:
        description_parts.append("row_count=None")

    if manifest.date_range_start or manifest.date_range_end:
        date_range = f"{manifest.date_range_start or '?'} to {manifest.date_range_end or '?'}"
        description_parts.append(f"date_range={date_range}")

    if manifest.symbols:
        description_parts.append(f"symbols={list(manifest.symbols)}")

    return {
        "artifact_type": "DataManifest",
        "artifact_id": manifest.dataset_id,
        "artifact_path": str(manifest_path.resolve()),
        "schema_ref": "N/A",  # DataManifest validated via dataclass, not JSON Schema; "N/A" satisfies minLength:1
        "validator_ref": None,  # nullable: type ["string", "null"]
        "content_hash": f"sha256:{manifest_content_hash}",
        "validation_status": "pass",
        # NOTE: description, validated_at, and other non-schema fields are omitted.
        # input_artifact_refs.items has additionalProperties: false.
        # Only the required + explicitly-listed optional properties are included.
    }


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
# Exception classes — moved to runner_artifacts.py
# ---------------------------------------------------------------------------
# (GovernanceRejection, UnsupportedConfig — imported from runner_artifacts)


# ---------------------------------------------------------------------------

def _handle_data_manifest_validation_failure(
    experiment_spec: dict,
    experiment_spec_path: Path,
    created_at: str,
    required_observation_columns: list[str] | None,
    failure_type: str,
    exc: Exception,
    trial_accounting_summary: dict | None = None,
) -> tuple[dict, int]:
    """
    Build and return a (partial_failed_validation_artifact, governance_blocker_count)
    tuple when DataManifest loading fails (file missing, KeyError, or unsupported
    format error).

    Appends data_manifest_validation audit to governance audits and
    builds failure_summary with the caller-supplied failure_type
    (e.g. "validation_error" for missing files, "unsupported_config" for
    unsupported format + required_observation_columns).

    Returns
    -------
    tuple[dict, int]
        (artifact_dict, governance_blocker_count).
        The caller should:
        - If governance_blocker_count > 0: raise GovernanceRejection with artifact.
        - If governance_blocker_count == 0: return artifact directly (failed_validation).
    """
    now = _utc_now()
    spec_content_hash = _compute_content_hash(experiment_spec_path)
    run_config_hash_partial = _compute_run_config_hash(
        experiment_spec_path,
        None,
        required_observation_columns,
        trial_accounting_summary=trial_accounting_summary,
    )
    run_id_partial = _compute_run_id(run_config_hash_partial)

    spec_dm_refs = experiment_spec.get("data_manifest_refs", None)
    if spec_dm_refs and len(spec_dm_refs) > 0:
        data_manifest_refs_list = spec_dm_refs
    else:
        data_manifest_refs_list = [DRY_RUN_DATA_MANIFEST_PLACEHOLDER]

    input_artifact_refs = [
        {
            "artifact_type": "ExperimentSpec",
            "artifact_id": experiment_spec["experiment_id"],
            "artifact_path": (
                str(experiment_spec_path.resolve())
                if experiment_spec_path.is_absolute()
                else str(experiment_spec_path)
            ),
            "schema_ref": "schemas/experiment_spec_v1.schema.json",
            "validator_ref": None,
            "content_hash": f"sha256:{spec_content_hash}",
            "validation_status": "pass",
            "validated_at": created_at,
        }
    ]

    # Governance audit — run it here so blockers (e.g. autonomous_search) are
    # preserved in the artifact even when DataManifest loading fails.
    # data_manifest_validation is appended to audit_summary.audits as a non-blocker
    # (blocker_count=0) so it appears in audit output but does NOT inflate
    # the governance blocker_count that triggers GovernanceRejection.
    governance_audit_summary = _audit_dry_run(
        experiment_spec,
        experiment_spec_path,
        run_config_hash_partial,
    )

    # Append data_manifest_validation failure as non-blocker entry
    # (blocker_count=0 so it doesn't affect governance blocker_count)
    new_audits = list(governance_audit_summary["audits"])
    new_audits.append({
        "audit_name": "data_manifest_validation",
        "audit_result": "fail",
        "severity": "blocker",
        "blocker_count": 0,  # NOT a governance blocker — user validation failure
        "warning_count": 0,
        "details_ref": None,
        "created_at": now,
    })
    governance_audit_summary = {
        "overall_result": governance_audit_summary["overall_result"],
        "blocker_count": governance_audit_summary["blocker_count"],  # unchanged
        "warning_count": governance_audit_summary["warning_count"],
        "audits": new_audits,
    }

    # Build failure_summary with schema-compliant failure_type
    # data_manifest_validation failure goes into blocker_summary (not audit_summary)
    failing_checks = [a["audit_name"] for a in governance_audit_summary["audits"] if a["audit_result"] == "fail"]
    blocker_parts = [f"Validation failed: {', '.join(failing_checks)}."]
    if required_observation_columns is not None:
        blocker_parts.append(
            f"required_observation_columns could not be validated "
            f"(format unsupported for column checks)."
        )
    blocker_summary_str = " ".join(blocker_parts)

    failure_summary = {
        "failure_type": failure_type,
        "status": "failed_validation",
        "failed_check": ", ".join(failing_checks) or None,
        "blocker_summary": blocker_summary_str,
        "missing_data_summary_ref": None,
        "details_ref": None,
        "created_at": now,
    }

    output_manifest = [
        {
            "output_role": "evidence",
            "output_path": (
                str(experiment_spec_path.resolve())
                if experiment_spec_path.is_absolute()
                else str(experiment_spec_path)
            ),
            "row_count": None,
            "content_hash": f"sha256:{spec_content_hash}",
            "created_at": created_at,
            "format": "json",
            "description": (
                "Input experiment spec referenced by this dry-run artifact. "
                "Content hash is of the experiment spec JSON file, providing "
                "a stable content identifier for the input governance document."
            ),
            "contains_private_data": False,
            "publishable": False,
        }
    ]

    failed_artifact = {
        "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
        "runner_output_version": RUNNER_OUTPUT_VERSION,
        "run_id": run_id_partial,
        "run_mode": "dry_run",
        "status": "failed_validation",
        "runner_name": RUNNER_NAME_DEFAULT,
        "runner_version": RUNNER_VERSION_DEFAULT,
        "experiment_spec_ref": experiment_spec["experiment_id"],
        "input_artifact_refs": input_artifact_refs,
        "data_manifest_refs": data_manifest_refs_list,
        "run_config_hash": f"sha256:{run_config_hash_partial}",
        "started_at": created_at,
        "completed_at": now,
        "audit_summary": governance_audit_summary,
        "output_manifest": output_manifest,
        "created_at": created_at,
        "run_owner": "unknown",  # Not available at partial-artifact stage
        "failure_summary": failure_summary,
        "partial_summary": None,
        "trial_accounting_summary": trial_accounting_summary,
    }

    return (failed_artifact, governance_audit_summary["blocker_count"])


# -----------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def _audit_dry_run(
    experiment_spec: dict,
    experiment_spec_path: Path,
    run_config_hash: str,
) -> dict:
    """
    Build the audit_summary block for a dry-run.

    Checks performed:
    - schema_validation_all_inputs : experiment spec has required experiment_id
    - no_registry_mutation          : dry-run makes no registry writes by definition
    - no_autonomous_search_flag_set : prohibited_modes.autonomous_search is false
    - deterministic_run_config_hash : run_config_hash is stable from file content

    Returns a dict with overall_result, blocker_count, warning_count, and audits.

    Raises GovernanceRejection if any blocker_count > 0.
    """
    now = _utc_now()

    # 1. schema_validation_all_inputs
    try:
        _check_experiment_spec_id(experiment_spec)
        schema_validation_result = "pass"
        schema_validation_blocker = 0
    except ValueError:
        schema_validation_result = "fail"
        schema_validation_blocker = 1

    # 2. no_registry_mutation — dry-run never touches registries
    no_registry_mutation_result = "pass"
    no_registry_mutation_blocker = 0

    # 3. no_autonomous_search_flag_set
    prohibited = experiment_spec.get("prohibited_modes", {})
    autonomous_search_value = prohibited.get("autonomous_search", None)
    if autonomous_search_value is True:
        no_autonomous_search_result = "fail"
        no_autonomous_search_blocker = 1
    else:
        no_autonomous_search_result = "pass"
        no_autonomous_search_blocker = 0

    # 4. deterministic_run_config_hash — always deterministic from file content
    deterministic_result = "pass"
    deterministic_blocker = 0

    blocker_total = (
        schema_validation_blocker
        + no_registry_mutation_blocker
        + no_autonomous_search_blocker
        + deterministic_blocker
    )

    overall = "fail" if blocker_total > 0 else "pass"

    audits = [
        {
            "audit_name": "schema_validation_all_inputs",
            "audit_result": schema_validation_result,
            "severity": "blocker",
            "blocker_count": schema_validation_blocker,
            "warning_count": 0,
            "details_ref": None,
            "created_at": now,
        },
        {
            "audit_name": "no_registry_mutation",
            "audit_result": no_registry_mutation_result,
            "severity": "blocker",
            "blocker_count": no_registry_mutation_blocker,
            "warning_count": 0,
            "details_ref": None,
            "created_at": now,
        },
        {
            "audit_name": "no_autonomous_search_flag_set",
            "audit_result": no_autonomous_search_result,
            "severity": "blocker",
            "blocker_count": no_autonomous_search_blocker,
            "warning_count": 0,
            "details_ref": None,
            "created_at": now,
        },
        {
            "audit_name": "deterministic_run_config_hash",
            "audit_result": deterministic_result,
            "severity": "blocker",
            "blocker_count": deterministic_blocker,
            "warning_count": 0,
            "details_ref": None,
            "created_at": now,
        },
    ]

    return {
        "overall_result": overall,
        "blocker_count": blocker_total,
        "warning_count": 0,
        "audits": audits,
    }


# ---------------------------------------------------------------------------
# _build_failure_summary — moved to runner_artifacts.py
# ---------------------------------------------------------------------------
# (imported from runner_artifacts)


# ---------------------------------------------------------------------------
# Core artifact builder
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Trial accounting helpers
# ---------------------------------------------------------------------------

ALLOWED_MUTATION_MODES = {"no_mutation", "dry_run_reference_only"}
REJECTED_MUTATION_MODES = {"ledger_write", "registry_write"}
STATUS_ENUM_VALUES = {
    "not_applicable",
    "proposed",
    "linked",
    "blocked",
}
COMPLEXITY_BUCKET_ENUM_VALUES = {
    "low",
    "medium",
    "high",
    "excessive",
    "unknown",
}


def _non_negative_int_arg(value: Any, field_name: str) -> int:
    """Parse and validate that an integer field is non-negative."""
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer; got boolean {value!r}")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError(f"{field_name} must be an integer; got {value!r}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer; got {value!r}") from exc
    if parsed < 0:
        raise ValueError(f"{field_name} must be non-negative; got {parsed}")
    return parsed


def _non_negative_float_arg(value: Any, field_name: str) -> float:
    """Parse and validate that a float field is non-negative and finite."""
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number; got boolean {value!r}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number; got {value!r}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be finite; got {parsed}")
    if parsed < 0:
        raise ValueError(f"{field_name} must be non-negative; got {parsed}")
    return parsed


def _non_negative_int_cli_arg(field_name: str):
    """Return an argparse converter for a named non-negative integer field."""
    def _converter(value: str) -> int:
        try:
            return _non_negative_int_arg(value, field_name)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    return _converter


def _non_negative_float_cli_arg(field_name: str):
    """Return an argparse converter for a named non-negative float field."""
    def _converter(value: str) -> float:
        try:
            return _non_negative_float_arg(value, field_name)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    return _converter


class _TrialAccountingFlags:
    """
    Lightweight container for trial-accounting CLI flags.
    Mirrors the fields used by _any_trial_accounting_flag and
    _build_trial_accounting_summary so a single object can be passed
    to those helpers without requiring argparse at the call site.
    """

    def __init__(
        self,
        trial_accounting_status=None,
        trial_accounting_mutation_mode=None,
        search_space_id=None,
        trial_family_id=None,
        trial_id=None,
        proposed_trial_id=None,
        variant_id=None,
        selected_variant_id=None,
        model_assessment_id=None,
        review_packet_id=None,
        n_tried=None,
        candidate_variant_count=None,
        failed_variant_count=None,
        all_variants_preserved=None,
        sample_length=None,
        sample_to_trial_ratio=None,
        trial_accounting_notes=None,
        complexity_rule_count=None,
        complexity_parameter_count=None,
        complexity_signal_count=None,
        complexity_filter_count=None,
        complexity_bucket=None,
    ):
        self.trial_accounting_status = trial_accounting_status
        self.trial_accounting_mutation_mode = trial_accounting_mutation_mode
        self.search_space_id = search_space_id
        self.trial_family_id = trial_family_id
        self.trial_id = trial_id
        self.proposed_trial_id = proposed_trial_id
        self.variant_id = variant_id
        self.selected_variant_id = selected_variant_id
        self.model_assessment_id = model_assessment_id
        self.review_packet_id = review_packet_id
        self.n_tried = n_tried
        self.candidate_variant_count = candidate_variant_count
        self.failed_variant_count = failed_variant_count
        self.all_variants_preserved = all_variants_preserved
        self.sample_length = sample_length
        self.sample_to_trial_ratio = sample_to_trial_ratio
        self.trial_accounting_notes = trial_accounting_notes
        self.complexity_rule_count = complexity_rule_count
        self.complexity_parameter_count = complexity_parameter_count
        self.complexity_signal_count = complexity_signal_count
        self.complexity_filter_count = complexity_filter_count
        self.complexity_bucket = complexity_bucket


def _any_trial_accounting_flag(flags: _TrialAccountingFlags) -> bool:
    """
    Return True if any trial-accounting or complexity CLI flag was supplied.
    Used to determine whether to emit trial_accounting_summary at all.
    """
    trial_accounting_fields = [
        flags.trial_accounting_status,
        flags.trial_accounting_mutation_mode,
        flags.search_space_id,
        flags.trial_family_id,
        flags.trial_id,
        flags.proposed_trial_id,
        flags.variant_id,
        flags.selected_variant_id,
        flags.model_assessment_id,
        flags.review_packet_id,
        flags.n_tried,
        flags.candidate_variant_count,
        flags.failed_variant_count,
        flags.all_variants_preserved,
        flags.sample_length,
        flags.sample_to_trial_ratio,
        flags.trial_accounting_notes,
        # complexity sub-object flags
        flags.complexity_rule_count,
        flags.complexity_parameter_count,
        flags.complexity_signal_count,
        flags.complexity_filter_count,
        flags.complexity_bucket,
    ]
    return any(f is not None for f in trial_accounting_fields)


def _build_complexity(flags: _TrialAccountingFlags) -> dict | None:
    """
    Build the complexity sub-object if any complexity flag was supplied.
    Returns None if no complexity flags were provided (not a missing key —
    the field is present with value None per schema).
    """
    complexity_fields = [
        flags.complexity_rule_count,
        flags.complexity_parameter_count,
        flags.complexity_signal_count,
        flags.complexity_filter_count,
        flags.complexity_bucket,
    ]
    if not any(f is not None for f in complexity_fields):
        return None

    if flags.complexity_bucket is not None and flags.complexity_bucket not in COMPLEXITY_BUCKET_ENUM_VALUES:
        raise ValueError(
            f"Invalid complexity_bucket='{flags.complexity_bucket}'. "
            f"Allowed values: {sorted(COMPLEXITY_BUCKET_ENUM_VALUES)}"
        )

    rule_count = (
        _non_negative_int_arg(flags.complexity_rule_count, "complexity_rule_count")
        if flags.complexity_rule_count is not None
        else None
    )
    parameter_count = (
        _non_negative_int_arg(flags.complexity_parameter_count, "complexity_parameter_count")
        if flags.complexity_parameter_count is not None
        else None
    )
    signal_count = (
        _non_negative_int_arg(flags.complexity_signal_count, "complexity_signal_count")
        if flags.complexity_signal_count is not None
        else None
    )
    filter_count = (
        _non_negative_int_arg(flags.complexity_filter_count, "complexity_filter_count")
        if flags.complexity_filter_count is not None
        else None
    )

    return {
        "rule_count": rule_count,
        "parameter_count": parameter_count,
        "signal_count": signal_count,
        "filter_count": filter_count,
        "complexity_bucket": flags.complexity_bucket,
    }


def _build_trial_accounting_summary(
    experiment_spec: dict,
    manifest: "DatasetManifest | None",
    flags: _TrialAccountingFlags,
) -> dict:
    """
    Build a trial_accounting_summary dict.

    Must only be called when at least one trial-accounting CLI flag was supplied.

    Parameters
    ----------
    experiment_spec : dict
        The loaded experiment spec dict.
    manifest : DatasetManifest | None
        The validated DataManifest, or None if no manifest was loaded.
    flags : _TrialAccountingFlags
        Parsed CLI flags for trial accounting.

    Returns
    -------
    dict
        A trial_accounting_summary dict. Matches RunnerOutputSpec v1 schema.

    Raises
    ------
    ValueError
        If mutation_mode is not in ALLOWED_MUTATION_MODES.
    """
    # Resolve mutation_mode with default
    mutation_mode = flags.trial_accounting_mutation_mode
    if mutation_mode is None:
        mutation_mode = "dry_run_reference_only"

    # Reject explicitly disallowed modes
    if mutation_mode in REJECTED_MUTATION_MODES:
        raise ValueError(
            f"mutation_mode='{mutation_mode}' is not supported by this runner. "
            f"Allowed values: {sorted(ALLOWED_MUTATION_MODES)}"
        )
    if mutation_mode not in ALLOWED_MUTATION_MODES:
        raise ValueError(
            f"Invalid mutation_mode='{mutation_mode}'. "
            f"Allowed values: {sorted(ALLOWED_MUTATION_MODES)}"
        )

    # Resolve status with default
    status = flags.trial_accounting_status
    if status is None:
        status = "proposed"  # not_applicable only when explicitly supplied

    if status not in STATUS_ENUM_VALUES:
        raise ValueError(
            f"Invalid trial_accounting_status='{status}'. "
            f"Allowed values: {sorted(STATUS_ENUM_VALUES)}"
        )

    n_tried = (
        _non_negative_int_arg(flags.n_tried, "n_tried")
        if flags.n_tried is not None
        else None
    )
    candidate_variant_count = (
        _non_negative_int_arg(flags.candidate_variant_count, "candidate_variant_count")
        if flags.candidate_variant_count is not None
        else None
    )
    failed_variant_count = (
        _non_negative_int_arg(flags.failed_variant_count, "failed_variant_count")
        if flags.failed_variant_count is not None
        else None
    )
    sample_length = (
        _non_negative_int_arg(flags.sample_length, "sample_length")
        if flags.sample_length is not None
        else None
    )
    sample_to_trial_ratio = (
        _non_negative_float_arg(flags.sample_to_trial_ratio, "sample_to_trial_ratio")
        if flags.sample_to_trial_ratio is not None
        else None
    )

    return {
        "status": status,
        "mutation_mode": mutation_mode,
        "experiment_id": experiment_spec.get("experiment_id"),
        "data_manifest_id": manifest.dataset_id if manifest is not None else None,
        "search_space_id": flags.search_space_id,
        "trial_family_id": flags.trial_family_id,
        "trial_id": flags.trial_id,
        "proposed_trial_id": flags.proposed_trial_id,
        "variant_id": flags.variant_id,
        "selected_variant_id": flags.selected_variant_id,
        "model_assessment_id": flags.model_assessment_id,
        "review_packet_id": flags.review_packet_id,
        "n_tried": n_tried,
        "candidate_variant_count": candidate_variant_count,
        "failed_variant_count": failed_variant_count,
        "all_variants_preserved": flags.all_variants_preserved,
        "sample_length": sample_length,
        "sample_to_trial_ratio": sample_to_trial_ratio,
        "complexity": _build_complexity(flags),
        "notes": flags.trial_accounting_notes,
    }


def build_runner_output(
    experiment_spec_path: str | Path,
    runner_name: str = RUNNER_NAME_DEFAULT,
    runner_version: str = RUNNER_VERSION_DEFAULT,
    run_owner: str = "unknown",
    data_manifest_path: str | Path | None = None,
    required_observation_columns: list[str] | None = None,
    observation_date_column: str | None = None,
    observation_symbol_column: str | None = None,
    observation_close_column: str | None = None,
    observation_missing_value_columns: list[str] | None = None,
    # Trial-accounting flags
    trial_accounting_status: str | None = None,
    trial_accounting_mutation_mode: str | None = None,
    search_space_id: str | None = None,
    trial_family_id: str | None = None,
    trial_id: str | None = None,
    proposed_trial_id: str | None = None,
    variant_id: str | None = None,
    selected_variant_id: str | None = None,
    model_assessment_id: str | None = None,
    review_packet_id: str | None = None,
    n_tried: int | None = None,
    candidate_variant_count: int | None = None,
    failed_variant_count: int | None = None,
    all_variants_preserved: bool | None = None,
    sample_length: int | None = None,
    sample_to_trial_ratio: float | None = None,
    trial_accounting_notes: str | None = None,
    # Complexity flags
    complexity_rule_count: int | None = None,
    complexity_parameter_count: int | None = None,
    complexity_signal_count: int | None = None,
    complexity_filter_count: int | None = None,
    complexity_bucket: str | None = None,
) -> dict:
    """
    Build a complete RunnerOutput v1 artifact for a dry-run.

    Parameters
    ----------
    experiment_spec_path : str | Path
        Path to the experiment spec JSON file.
    runner_name : str
        Human-readable runner name.
    runner_version : str
        Runner implementation version.
    run_owner : str
        Identity declared at run invocation.
    data_manifest_path : str | Path | None
        Optional path to a DataManifest JSON file. When provided, the manifest
        is loaded and validated. If validation fails, a failed_validation
        artifact is returned. If validation passes, the manifest is included
        in input_artifact_refs and data_manifest_refs.
    required_observation_columns : list[str] | None
        Optional list of column names that must be present in the observation
        table CSV referenced by the DataManifest. Only supported for CSV format.
        If provided without a DataManifest, emits failed_validation. If provided
        for a non-CSV DataManifest format, emits failed_validation.
    observation_date_column : str | None
        Optional name of the date column in the observation table CSV used for
        canonical summary (min/max date). Only supported for CSV format.
        Requires --data-manifest. If the column is absent from the CSV, emits
        failed_validation.
    observation_symbol_column : str | None
        Optional name of the symbol column in the observation table CSV used for
        canonical summary (unique symbol count). Only supported for CSV format.
        Requires --data-manifest. If the column is absent from the CSV, emits
        failed_validation.
    observation_close_column : str | None
        Optional name of the close price column in the observation table CSV used for
        per-symbol first/last close return summary (symbols_with_return, min_return,
        max_return, mean_return). Only supported for CSV format.
        Requires --data-manifest. Requires --observation-date-column and
        --observation-symbol-column. If the column is absent, emits failed_validation.
        If no symbols have valid returns (zero first close or <2 dates), emits
        failed_validation.
    observation_missing_value_columns : list[str] | None
        Optional list of column names to check for missing values in the
        observation table CSV. Only supported for CSV format.
        Requires --data-manifest.
        If any requested column is absent from the CSV header, emits
        failed_validation with validation_error.
        If source_kind is not CSV, emits failed_validation with unsupported_config.

    Returns
    -------
    dict
        A RunnerOutput v1 compatible artifact (dry_run / success OR
        dry_run / failed_validation if governance blockers or DataManifest
        validation fails).

    Raises
    ------
    FileNotFoundError
        If experiment_spec_path does not exist.
    ValueError
        If experiment_spec is missing required experiment_id field,
        or if DataManifest validation fails (returns failed_validation
        artifact instead of raising when data_manifest_path is provided).
    GovernanceRejection
        If governance validation fails (blocker_count > 0). The exception
        carries the pre-built artifact with status="failed_validation" so
        main() can emit it before exiting nonzero.
    """
    experiment_spec_path = Path(experiment_spec_path)

    # Load
    experiment_spec = _load_experiment_spec(experiment_spec_path)

    # Validate structural requirement
    _check_experiment_spec_id(experiment_spec)

    # Build _TrialAccountingFlags from the kwargs
    _ta_flags = _TrialAccountingFlags(
        trial_accounting_status=trial_accounting_status,
        trial_accounting_mutation_mode=trial_accounting_mutation_mode,
        search_space_id=search_space_id,
        trial_family_id=trial_family_id,
        trial_id=trial_id,
        proposed_trial_id=proposed_trial_id,
        variant_id=variant_id,
        selected_variant_id=selected_variant_id,
        model_assessment_id=model_assessment_id,
        review_packet_id=review_packet_id,
        n_tried=n_tried,
        candidate_variant_count=candidate_variant_count,
        failed_variant_count=failed_variant_count,
        all_variants_preserved=all_variants_preserved,
        sample_length=sample_length,
        sample_to_trial_ratio=sample_to_trial_ratio,
        trial_accounting_notes=trial_accounting_notes,
        complexity_rule_count=complexity_rule_count,
        complexity_parameter_count=complexity_parameter_count,
        complexity_signal_count=complexity_signal_count,
        complexity_filter_count=complexity_filter_count,
        complexity_bucket=complexity_bucket,
    )

    # Build trial_accounting_summary early — before DataManifest loading — so it
    # is present in failed_validation artifacts when manifest loading/validation
    # fails but trial-accounting flags were supplied.  data_manifest_id will be
    # populated from the manifest after a successful load (see below).
    if _any_trial_accounting_flag(_ta_flags):
        trial_accounting_summary = _build_trial_accounting_summary(
            experiment_spec, None, _ta_flags
        )
    else:
        trial_accounting_summary = None

    # Early validation: required_observation_columns needs a DataManifest to validate against
    if required_observation_columns is not None and data_manifest_path is None:
        raise ValueError(
            "--required-observation-columns was supplied but no --data-manifest was provided. "
            "Column validation requires a DataManifest to determine the dataset path."
        )

    # Early validation: date/symbol summary columns need a DataManifest
    has_canonical_summary_requested = (
        (observation_date_column is not None and observation_date_column.strip())
        or (observation_symbol_column is not None and observation_symbol_column.strip())
    )

    # Early validation: close-return summary requires date, symbol, AND data manifest
    has_close_return_summary_requested = (
        observation_close_column is not None and observation_close_column.strip()
    )
    has_missing_value_summary_requested = (
        observation_missing_value_columns is not None
        and len(observation_missing_value_columns) > 0
    )

    # Timestamps
    started_at = _utc_now()
    completed_at = _utc_now()
    created_at = completed_at  # same instant for dry-run skeleton

    # Early validation: close-return summary requires a DataManifest
    if has_close_return_summary_requested and data_manifest_path is None:
        raise ValueError(
            "--observation-close-column was supplied but no --data-manifest was provided. "
            "Close-return summary requires a DataManifest to determine the dataset path."
        )

    # Early validation: missing-value summary requires a DataManifest
    if has_missing_value_summary_requested and data_manifest_path is None:
        raise ValueError(
            "--observation-missing-value-columns was supplied but no --data-manifest was provided. "
            "Missing-value summary requires a DataManifest to determine the dataset path."
        )

    # Early validation: observation_date_column and observation_symbol_column required
    if has_close_return_summary_requested:
        date_ok = observation_date_column is not None and observation_date_column.strip()
        symbol_ok = observation_symbol_column is not None and observation_symbol_column.strip()
        if not date_ok or not symbol_ok:
            raise ValueError(
                "--observation-close-column was supplied but one or both of "
                "--observation-date-column and --observation-symbol-column are missing. "
                "Close-return summary requires date, symbol, and close columns."
            )

    # Early validation: missing-value columns need a data manifest
    has_missing_value_summary_requested = (
        observation_missing_value_columns is not None
        and len(observation_missing_value_columns) > 0
    )
    if has_missing_value_summary_requested and data_manifest_path is None:
        raise ValueError(
            "--observation-missing-value-columns was supplied but no --data-manifest "
            "was provided. Missing-value summary requires a DataManifest."
        )

    # Optional DataManifest loading and validation
    data_manifest_extra_audits: list[str] = []
    manifest: DatasetManifest | None = None
    manifest_summary: dict | None = None
    manifest_base_dir: Path = experiment_spec_path.parent
    observation_validation_passed = False
    observation_missing_columns: list[str] = []
    # Initialize canonical_summary_audit_entry before the manifest loading block.
    # It will be populated by the canonical summary section below (when manifest
    # is present) or by the no-manifest + canonical-summary path (when manifest
    # is absent but date/symbol columns were requested).
    canonical_summary_audit_entry: dict | None = None

    if data_manifest_path is not None:
        data_manifest_path = Path(data_manifest_path)
        # Resolve relative dataset paths from the DataManifest file's directory,
        # not the experiment spec's directory. Use manifest's parent as base_dir.
        manifest_base_dir = data_manifest_path.resolve(strict=False).parent

        # --- Pre-check source_kind before loading to get a clean unsupported_config path ---
        # We use yaml.safe_load here (without full DataManifest validation) to read
        # only the source_kind field. This avoids a DataSourceKind ValueError from
        # load_data_manifest_for_runner which would be caught as validation_error
        # instead of unsupported_config.
        try:
            import yaml as _yaml
            with open(data_manifest_path, encoding="utf-8") as _fh:
                _raw_manifest = _yaml.safe_load(_fh) or {}
            _raw_source_kind = _raw_manifest.get("source_kind", "")
        except Exception:
            _raw_source_kind = ""

        # Unsupported source_kind for close-return summary → raise GovernanceRejection
        # directly with unsupported_config failure_type (before attempting to load)
        if has_close_return_summary_requested and _raw_source_kind not in ("local_csv", ""):
            _now = _utc_now()
            _cfg_hash = _compute_run_config_hash(
                experiment_spec_path,
                data_manifest_path,
                required_observation_columns,
                observation_date_column,
                observation_symbol_column,
                observation_close_column,
                observation_missing_value_columns,
                trial_accounting_summary=trial_accounting_summary,
            )
            _close_fail_audit = {
                "audit_name": "observation_table_close_return_summary",
                "audit_result": "fail",
                "severity": "blocker",
                "blocker_count": 1,
                "warning_count": 0,
                "details_ref": (
                    f"source_kind '{_raw_source_kind}' is not supported "
                    f"for --observation-close-column (only CSV is supported)."
                ),
                "created_at": _now,
            }
            _all_audits = [_close_fail_audit]
            _total_blockers = 1
            _failing_checks = ["observation_table_close_return_summary"]
            _spec_content_hash = _compute_content_hash(experiment_spec_path)
            _experiment_id = experiment_spec.get("experiment_id", "unknown")
            _spec_artifact_ref = {
                "artifact_type": "ExperimentSpec",
                "artifact_id": _experiment_id,
                "artifact_path": (
                    str(experiment_spec_path.resolve())
                    if experiment_spec_path.is_absolute()
                    else str(experiment_spec_path)
                ),
                "schema_ref": "schemas/experiment_spec_v1.schema.json",
                "validator_ref": None,
                "content_hash": f"sha256:{_spec_content_hash}",
                "validation_status": "pass",
                "validated_at": created_at,
            }
            _dm_id = _raw_manifest.get("dataset_id")
            _data_manifest_ref = (
                _dm_id if (_dm_id and _dm_id.strip()) else DRY_RUN_DATA_MANIFEST_PLACEHOLDER
            )
            _spec_entry = {
                "output_role": "evidence",
                "output_path": (
                    str(Path(experiment_spec_path).resolve())
                    if Path(experiment_spec_path).is_absolute()
                    else str(experiment_spec_path)
                ),
                "row_count": None,
                "content_hash": f"sha256:{_spec_content_hash}",
                "created_at": created_at,
                "format": "json",
                "description": "Input experiment spec referenced by this dry-run artifact.",
                "contains_private_data": False,
                "publishable": False,
            }
            _failure_summary = {
                "failure_type": "unsupported_config",
                "status": "failed_validation",
                "failed_check": ", ".join(_failing_checks),
                "blocker_summary": (
                    f"observation_table_close_return_summary: "
                    f"source_kind '{_raw_source_kind}' is not supported "
                    f"for --observation-close-column (only CSV is supported)."
                ),
                "missing_data_summary_ref": None,
                "details_ref": None,
                "created_at": _now,
            }
            _failed_artifact = {
                "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
                "runner_output_version": RUNNER_OUTPUT_VERSION,
                "run_id": _compute_run_id(_cfg_hash),
                "run_mode": "dry_run",
                "status": "failed_validation",
                "runner_name": runner_name,
                "runner_version": runner_version,
                "experiment_spec_ref": _experiment_id,
                "input_artifact_refs": [_spec_artifact_ref],
                "data_manifest_refs": [_data_manifest_ref],
                "run_config_hash": f"sha256:{_cfg_hash}",
                "started_at": _now,
                "completed_at": _now,
                "audit_summary": {
                    "overall_result": "fail",
                    "blocker_count": _total_blockers,
                    "warning_count": 0,
                    "audits": _all_audits,
                },
                "output_manifest": [_spec_entry],
                "created_at": _now,
                "run_owner": run_owner,
                "failure_summary": _failure_summary,
                "partial_summary": None,
                "trial_accounting_summary": trial_accounting_summary,
            }
            raise GovernanceRejection(
                _failed_artifact,
                f"Run blocked: --observation-close-column requires CSV datasets "
                f"(source_kind='{_raw_source_kind}' is not supported).",
            )

        # Unsupported source_kind for missing-value summary → unsupported_config
        if has_missing_value_summary_requested and _raw_source_kind not in ("local_csv", ""):
            _now = _utc_now()
            _cfg_hash = _compute_run_config_hash(
                experiment_spec_path,
                data_manifest_path,
                required_observation_columns,
                observation_date_column,
                observation_symbol_column,
                observation_close_column,
                observation_missing_value_columns,
                trial_accounting_summary=trial_accounting_summary,
            )
            _missing_val_fail_audit = {
                "audit_name": "observation_table_missing_value_summary",
                "audit_result": "fail",
                "severity": "blocker",
                "blocker_count": 1,
                "warning_count": 0,
                "details_ref": (
                    f"source_kind '{_raw_source_kind}' is not supported "
                    f"for --observation-missing-value-columns (only CSV is supported)."
                ),
                "created_at": _now,
            }
            _all_audits = [_missing_val_fail_audit]
            _total_blockers = 1
            _failing_checks = ["observation_table_missing_value_summary"]
            _spec_content_hash = _compute_content_hash(experiment_spec_path)
            _experiment_id = experiment_spec.get("experiment_id", "unknown")
            _spec_artifact_ref = {
                "artifact_type": "ExperimentSpec",
                "artifact_id": _experiment_id,
                "artifact_path": (
                    str(experiment_spec_path.resolve())
                    if experiment_spec_path.is_absolute()
                    else str(experiment_spec_path)
                ),
                "schema_ref": "schemas/experiment_spec_v1.schema.json",
                "validator_ref": None,
                "content_hash": f"sha256:{_spec_content_hash}",
                "validation_status": "pass",
                "validated_at": created_at,
            }
            _dm_id = _raw_manifest.get("dataset_id")
            _data_manifest_ref = (
                _dm_id if (_dm_id and _dm_id.strip()) else DRY_RUN_DATA_MANIFEST_PLACEHOLDER
            )
            _spec_entry = {
                "output_role": "evidence",
                "output_path": (
                    str(Path(experiment_spec_path).resolve())
                    if Path(experiment_spec_path).is_absolute()
                    else str(experiment_spec_path)
                ),
                "row_count": None,
                "content_hash": f"sha256:{_spec_content_hash}",
                "created_at": created_at,
                "format": "json",
                "description": "Input experiment spec referenced by this dry-run artifact.",
                "contains_private_data": False,
                "publishable": False,
            }
            _failure_summary = {
                "failure_type": "unsupported_config",
                "status": "failed_validation",
                "failed_check": ", ".join(_failing_checks),
                "blocker_summary": (
                    f"observation_table_missing_value_summary: "
                    f"source_kind '{_raw_source_kind}' is not supported "
                    f"for --observation-missing-value-columns (only CSV is supported)."
                ),
                "missing_data_summary_ref": None,
                "details_ref": None,
                "created_at": _now,
            }
            _failed_artifact = {
                "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
                "runner_output_version": RUNNER_OUTPUT_VERSION,
                "run_id": _compute_run_id(_cfg_hash),
                "run_mode": "dry_run",
                "status": "failed_validation",
                "runner_name": runner_name,
                "runner_version": runner_version,
                "experiment_spec_ref": _experiment_id,
                "input_artifact_refs": [_spec_artifact_ref],
                "data_manifest_refs": [_data_manifest_ref],
                "run_config_hash": f"sha256:{_cfg_hash}",
                "started_at": _now,
                "completed_at": _now,
                "audit_summary": {
                    "overall_result": "fail",
                    "blocker_count": _total_blockers,
                    "warning_count": 0,
                    "audits": _all_audits,
                },
                "output_manifest": [_spec_entry],
                "created_at": _now,
                "run_owner": run_owner,
                "failure_summary": _failure_summary,
                "partial_summary": None,
                "trial_accounting_summary": trial_accounting_summary,
            }
            raise GovernanceRejection(
                _failed_artifact,
                f"Run blocked: --observation-missing-value-columns requires CSV datasets "
                f"(source_kind='{_raw_source_kind}' is not supported).",
            )

        try:
            manifest = load_data_manifest_for_runner(
                data_manifest_path=data_manifest_path,
                base_dir=manifest_base_dir,
            )
            manifest_summary = _summarize_data_manifest_for_runner(
                manifest=manifest,
                manifest_path=data_manifest_path,
                base_dir=manifest_base_dir,
            )
        except (FileNotFoundError, KeyError) as exc:
            # DataManifest file missing or schema KeyError → validation_error
            failure_type = "validation_error"
            partial_artifact, governance_blocker_count = (
                _handle_data_manifest_validation_failure(
                    experiment_spec, experiment_spec_path, created_at,
                    required_observation_columns, failure_type, exc,
                    trial_accounting_summary=trial_accounting_summary,
                )
            )
            if governance_blocker_count > 0:
                raise GovernanceRejection(
                    partial_artifact,
                    f"DataManifest validation failed ({failure_type}): {exc}",
                )
            return partial_artifact
        except ValueError as exc:
            # ValueError may be from unsupported format + required_observation_columns,
            # or from other DataManifest validation errors. Distinguish by message.
            if (
                required_observation_columns is not None
                and "required_observation_columns is only supported for CSV"
                in str(exc)
            ):
                failure_type = "unsupported_config"
            else:
                failure_type = "validation_error"
            partial_artifact, governance_blocker_count = (
                _handle_data_manifest_validation_failure(
                    experiment_spec, experiment_spec_path, created_at,
                    required_observation_columns, failure_type, exc,
                    trial_accounting_summary=trial_accounting_summary,
                )
            )
            if governance_blocker_count > 0:
                raise GovernanceRejection(
                    partial_artifact,
                    f"DataManifest validation failed ({failure_type}): {exc}",
                )
            return partial_artifact

    # Populate data_manifest_id in the early-built trial_accounting_summary
    # (built before DataManifest loading) now that manifest has loaded successfully.
    if trial_accounting_summary is not None and manifest is not None:
        trial_accounting_summary["data_manifest_id"] = manifest.dataset_id

    # Deterministic hashes — include DataManifest content and required columns when present
    run_config_hash = _compute_run_config_hash(
        experiment_spec_path,
        data_manifest_path,
        required_observation_columns,
        observation_date_column,
        observation_symbol_column,
        observation_close_column,
        observation_missing_value_columns,
        trial_accounting_summary=trial_accounting_summary,
    )
    run_id = _compute_run_id(run_config_hash)
    experiment_id = experiment_spec["experiment_id"]

    # Content hash of experiment spec
    spec_content_hash = _compute_content_hash(experiment_spec_path)

    # Build input_artifact_refs — ExperimentSpec entry
    input_artifact_refs: list[dict[str, Any]] = [
        {
            "artifact_type": "ExperimentSpec",
            "artifact_id": experiment_id,
            "artifact_path": (
                str(experiment_spec_path.resolve())
                if experiment_spec_path.is_absolute()
                else str(experiment_spec_path)
            ),
            "schema_ref": "schemas/experiment_spec_v1.schema.json",
            "validator_ref": None,
            "content_hash": f"sha256:{spec_content_hash}",
            "validation_status": "pass",
            "validated_at": created_at,
        }
    ]

    # Append DataManifest entry when provided (description carries metadata, no extra fields)
    if manifest is not None and manifest_summary is not None:
        input_artifact_refs.append(manifest_summary)

    # data_manifest_refs
    if manifest is not None:
        # Use the real dataset_id from the validated DataManifest
        data_manifest_refs = [manifest.dataset_id]
    else:
        # Forward from experiment spec, or use stable dry-run placeholder
        spec_dm_refs = experiment_spec.get("data_manifest_refs", None)
        if spec_dm_refs and len(spec_dm_refs) > 0:
            data_manifest_refs = spec_dm_refs
        else:
            data_manifest_refs = [DRY_RUN_DATA_MANIFEST_PLACEHOLDER]

    # Audit summary — this may raise GovernanceRejection
    audit_summary = _audit_dry_run(
        experiment_spec,
        experiment_spec_path,
        run_config_hash,
    )

    # Missing-value summary (observation_table_missing_value_summary audit).
    # Runs BEFORE column/canonical validation so both audits appear in the
    # GovernanceRejection artifact if multiple validations fail.
    if has_missing_value_summary_requested and manifest is not None:
        if manifest.source_kind.value != "local_csv":
            # Unsupported format → raise unsupported_config before any other validation
            now = _utc_now()
            missing_val_fail_audit = {
                "audit_name": "observation_table_missing_value_summary",
                "audit_result": "fail",
                "severity": "blocker",
                "blocker_count": 1,
                "warning_count": 0,
                "details_ref": (
                    f"source_kind '{manifest.source_kind.value}' is not supported "
                    f"for --observation-missing-value-columns (only CSV is supported)."
                ),
                "created_at": now,
            }
            new_audits = list(audit_summary["audits"])
            new_audits.append(missing_val_fail_audit)
            total_blockers = sum(
                a["blocker_count"] for a in new_audits if a["audit_result"] == "fail"
            )
            _spec_artifact_ref = {
                "artifact_type": "ExperimentSpec",
                "artifact_id": experiment_id,
                "artifact_path": (
                    str(experiment_spec_path.resolve())
                    if experiment_spec_path.is_absolute()
                    else str(experiment_spec_path)
                ),
                "schema_ref": "schemas/experiment_spec_v1.schema.json",
                "validator_ref": None,
                "content_hash": f"sha256:{spec_content_hash}",
                "validation_status": "pass",
                "validated_at": created_at,
            }
            _data_manifest_ref = (
                manifest.dataset_id if manifest.dataset_id else DRY_RUN_DATA_MANIFEST_PLACEHOLDER
            )
            _spec_entry = {
                "output_role": "evidence",
                "output_path": (
                    str(experiment_spec_path.resolve())
                    if experiment_spec_path.is_absolute()
                    else str(experiment_spec_path)
                ),
                "row_count": None,
                "content_hash": f"sha256:{spec_content_hash}",
                "created_at": created_at,
                "format": "json",
                "description": "Input experiment spec referenced by this dry-run artifact.",
                "contains_private_data": False,
                "publishable": False,
            }
            _failure_summary = {
                "failure_type": "unsupported_config",
                "status": "failed_validation",
                "failed_check": "observation_table_missing_value_summary",
                "blocker_summary": (
                    f"observation_table_missing_value_summary: "
                    f"source_kind '{manifest.source_kind.value}' is not supported "
                    f"for --observation-missing-value-columns (only CSV is supported)."
                ),
                "missing_data_summary_ref": None,
                "details_ref": None,
                "created_at": now,
            }
            _failed_artifact = {
                "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
                "runner_output_version": RUNNER_OUTPUT_VERSION,
                "run_id": run_id,
                "run_mode": "dry_run",
                "status": "failed_validation",
                "runner_name": runner_name,
                "runner_version": runner_version,
                "experiment_spec_ref": experiment_id,
                "input_artifact_refs": [_spec_artifact_ref],
                "data_manifest_refs": [_data_manifest_ref],
                "run_config_hash": f"sha256:{run_config_hash}",
                "started_at": created_at,
                "completed_at": now,
                "audit_summary": {
                    "overall_result": "fail",
                    "blocker_count": total_blockers,
                    "warning_count": 0,
                    "audits": new_audits,
                },
                "output_manifest": [_spec_entry],
                "created_at": now,
                "run_owner": run_owner,
                "failure_summary": _failure_summary,
                "partial_summary": None,
                "trial_accounting_summary": trial_accounting_summary,
            }
            raise GovernanceRejection(
                _failed_artifact,
                f"Run blocked: --observation-missing-value-columns requires CSV datasets "
                f"(source_kind='{manifest.source_kind.value}' is not supported).",
            )
        # CSV dataset — compute missing-value summary
        raw_path = Path(manifest.path)
        dataset_resolved = (
            raw_path if raw_path.is_absolute()
            else (manifest_base_dir / raw_path).resolve()
        )
        now = _utc_now()
        try:
            missing_val_result = _summarize_observation_missing_values(
                dataset_resolved,
                observation_missing_value_columns,
            )
            missing_val_details = missing_val_result["details"]
            any_missing = any(
                count > 0 for count in missing_val_result["missing"].values()
            )
            if any_missing:
                missing_val_passed = False
                missing_val_fail_audit = {
                    "audit_name": "observation_table_missing_value_summary",
                    "audit_result": "fail",
                    "severity": "blocker",
                    "blocker_count": 1,
                    "warning_count": 0,
                    "details_ref": f"missing_value_summary_error: {missing_val_details}",
                    "created_at": now,
                }
            else:
                missing_val_passed = True
                missing_val_fail_audit = {
                    "audit_name": "observation_table_missing_value_summary",
                    "audit_result": "pass",
                    "severity": "blocker",
                    "blocker_count": 0,
                    "warning_count": 0,
                    "details_ref": missing_val_details,
                    "created_at": now,
                }
        except ValueError as exc:
            missing_val_passed = False
            missing_val_details = str(exc)
            missing_val_fail_audit = {
                "audit_name": "observation_table_missing_value_summary",
                "audit_result": "fail",
                "severity": "blocker",
                "blocker_count": 1,
                "warning_count": 0,
                "details_ref": f"missing_value_summary_error: {missing_val_details}",
                "created_at": now,
            }
        # Append missing-value audit to audit_summary
        new_audits = list(audit_summary["audits"])
        new_audits.append(missing_val_fail_audit)
        total_blockers = sum(
            a["blocker_count"] for a in new_audits if a["audit_result"] == "fail"
        )
        audit_summary = {
            "overall_result": (
                "fail" if total_blockers > 0 else audit_summary["overall_result"]
            ),
            "blocker_count": total_blockers,
            "warning_count": audit_summary["warning_count"],
            "audits": new_audits,
        }
        # If missing-value summary found violations, set status/failure_summary
        # but do NOT raise — let column validation also add its audit.
        # The rejection is raised by column validation (or "Determine terminal status")
        # with all accumulated audits.
        if not missing_val_passed:
            status = "failed_validation"
            failing_checks = [
                a["audit_name"] for a in audit_summary["audits"]
                if a["audit_result"] == "fail"
            ]
            failure_summary = {
                "failure_type": "validation_error",
                "status": "failed_validation",
                "failed_check": ", ".join(failing_checks),
                "blocker_summary": (
                    f"observation_table_missing_value_summary: {missing_val_details}. "
                    f"Total blockers: {audit_summary['blocker_count']}."
                ),
                "missing_data_summary_ref": None,
                "details_ref": None,
                "created_at": now,
            }

    # Determine terminal status based on audit result
    if audit_summary["blocker_count"] > 0:
        status = "failed_validation"
        failure_summary = _build_failure_summary(
            audit_summary,
            status,
            observation_missing_columns=(observation_missing_columns if not observation_validation_passed else None),
        )
    else:
        status = "success"
        failure_summary = None

    # output_manifest — entry describing the input experiment spec.
    #
    # content_hash is the experiment spec file hash (computed above).
    # output_path points to the experiment spec file so that content_hash
    # genuinely hashes the file it names — satisfying the schema's
    # "hash of output file content" semantics for this dry-run skeleton.
    # This is NOT the RunnerOutput JSON path (that artifact IS the run
    # output; output_manifest describes referenced artifacts).
    # -------------------------------------------------------------------------
    # Canonical observation-table summary (optional).
    # Computed BEFORE required-column validation so both audits appear in
    # the GovernanceRejection artifact if required columns also fail.
    # - If CSV dataset exists and columns are present → pass audit entry.
    # - If CSV dataset missing or column absent → fail audit entry.
    # - Non-CSV dataset → raise GovernanceRejection with failed_validation artifact.
    # - No DataManifest at all → raise GovernanceRejection with failed_validation artifact.
    # -------------------------------------------------------------------------
    if has_canonical_summary_requested:
        if data_manifest_path is None:
            # Canonical summary requested but no DataManifest provided — raise
            # GovernanceRejection so the artifact is written by main().
            now = _utc_now()
            spec_content_hash = _compute_content_hash(experiment_spec_path)
            canonical_audit = {
                "audit_name": "observation_table_canonical_summary",
                "audit_result": "fail",
                "severity": "blocker",
                "blocker_count": 1,
                "warning_count": 0,
                "details_ref": (
                    "canonical_summary_error: Cannot compute canonical summary: "
                    "--data-manifest was not provided."
                ),
                "created_at": now,
            }
            new_audits_list = list(audit_summary["audits"])
            new_audits_list.append(canonical_audit)
            total_b = sum(
                a["blocker_count"] for a in new_audits_list
                if a["audit_result"] == "fail"
            )
            spec_entry = {
                "output_role": "evidence",
                "output_path": (
                    str(experiment_spec_path.resolve())
                    if experiment_spec_path.is_absolute()
                    else str(experiment_spec_path)
                ),
                "row_count": None,
                "content_hash": f"sha256:{spec_content_hash}",
                "created_at": created_at,
                "format": "json",
                "description": (
                    "Input experiment spec referenced by this dry-run artifact."
                ),
            }
            partial_artifact = {
                "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
                "runner_output_version": RUNNER_OUTPUT_VERSION,
                "run_id": _compute_run_id(run_config_hash),
                "run_mode": "dry_run",
                "status": "failed_validation",
                "runner_name": RUNNER_NAME_DEFAULT,
                "runner_version": RUNNER_VERSION_DEFAULT,
                "experiment_spec_ref": experiment_spec.get("experiment_id"),
                "input_artifact_refs": input_artifact_refs,
                "data_manifest_refs": (
                    experiment_spec.get("data_manifest_refs") or [DRY_RUN_DATA_MANIFEST_PLACEHOLDER]
                ),
                "run_config_hash": run_config_hash,
                "started_at": started_at,
                "completed_at": now,
                "audit_summary": {
                    "overall_result": "fail",
                    "blocker_count": total_b,
                    "warning_count": 0,
                    "audits": new_audits_list,
                },
                "output_manifest": [spec_entry],
                "failure_summary": {
                    "failure_type": "validation_error",
                    "status": "failed_validation",
                    "failed_check": "observation_table_canonical_summary",
                    "blocker_summary": (
                        f"observation_table_canonical_summary; "
                        f"Total blockers: {total_b}."
                    ),
                    "missing_data_summary_ref": None,
                    "details_ref": None,
                    "created_at": now,
                },
                "partial_summary": None,
                "created_at": created_at,
                "run_owner": run_owner,
            }
            raise GovernanceRejection(
                partial_artifact,
                "--observation-date-column/--observation-symbol-column was supplied "
                "but no --data-manifest was provided. "
                "Canonical summary requires a DataManifest.",
            )
        elif manifest is not None:
            if manifest.source_kind.value != "local_csv":
                # Non-CSV with canonical summary args → raise GovernanceRejection
                # Build a minimal failed_validation artifact with canonical audit
                now = _utc_now()
                canonical_audit = {
                    "audit_name": "observation_table_canonical_summary",
                    "audit_result": "fail",
                    "severity": "blocker",
                    "blocker_count": 1,
                    "warning_count": 0,
                    "details_ref": (
                        f"canonical_summary_error: --observation-date-column/"
                        f"--observation-symbol-column is only supported for CSV "
                        f"(dataset has source_kind='{manifest.source_kind.value}')."
                    ),
                    "created_at": now,
                }
                new_audits_list = list(audit_summary["audits"])
                new_audits_list.append(canonical_audit)
                total_b = sum(
                    a["blocker_count"] for a in new_audits_list
                    if a["audit_result"] == "fail"
                )
                canonical_failure_summary = {
                    "failure_type": "unsupported_config",
                    "status": "failed_validation",
                    "failed_check": "observation_table_canonical_summary",
                    "blocker_summary": (
                        f"observation_table_canonical_summary; "
                        f"Total blockers: {total_b}."
                    ),
                    "missing_data_summary_ref": None,
                    "details_ref": None,
                    "created_at": now,
                }
                spec_content_hash = _compute_content_hash(experiment_spec_path)
                spec_entry = {
                    "output_role": "evidence",
                    "output_path": (
                        str(experiment_spec_path.resolve())
                        if experiment_spec_path.is_absolute()
                        else str(experiment_spec_path)
                    ),
                    "row_count": None,
                    "content_hash": f"sha256:{spec_content_hash}",
                    "created_at": created_at,
                    "format": "json",
                    "description": (
                        "Input experiment spec referenced by this dry-run artifact."
                    ),
                    "contains_private_data": False,
                    "publishable": False,
                }
                partial_artifact = {
                    "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
                    "runner_output_version": RUNNER_OUTPUT_VERSION,
                    "run_id": _compute_run_id(run_config_hash),
                    "run_mode": "dry_run",
                    "status": "failed_validation",
                    "runner_name": RUNNER_NAME_DEFAULT,
                    "runner_version": RUNNER_VERSION_DEFAULT,
                    "experiment_spec_ref": experiment_spec.get("experiment_id"),
                    "input_artifact_refs": input_artifact_refs,
                    "data_manifest_refs": (
                        experiment_spec.get("data_manifest_refs") or [DRY_RUN_DATA_MANIFEST_PLACEHOLDER]
                    ),
                    "run_config_hash": run_config_hash,
                    "started_at": started_at,
                    "completed_at": now,
                    "audit_summary": {
                        "overall_result": "fail",
                        "blocker_count": total_b,
                        "warning_count": 0,
                        "audits": new_audits_list,
                    },
                    "output_manifest": [spec_entry],
                    "failure_summary": canonical_failure_summary,
                    "partial_summary": None,
                    "created_at": created_at,
                    "run_owner": run_owner,
                }
                raise GovernanceRejection(
                    partial_artifact,
                    f"--observation-date-column/--observation-symbol-column is only "
                    f"supported for CSV datasets (dataset has "
                    f"source_kind='{manifest.source_kind.value}').",
                )
            # CSV dataset — attempt canonical summary computation
            raw_path = Path(manifest.path)
            dataset_resolved = (
                raw_path if raw_path.is_absolute()
                else (manifest_base_dir / raw_path).resolve()
            )
            try:
                summary_result = _summarize_observation_table_canonical(
                    dataset_resolved,
                    observation_date_column=observation_date_column,
                    observation_symbol_column=observation_symbol_column,
                )
                canonical_summary_audit_entry = {
                    "audit_name": "observation_table_canonical_summary",
                    "audit_result": "pass",
                    "severity": "info",
                    "blocker_count": 0,
                    "warning_count": 0,
                    "details_ref": summary_result["details"],
                    "created_at": _utc_now(),
                }
            except ValueError as exc:
                canonical_summary_audit_entry = {
                    "audit_name": "observation_table_canonical_summary",
                    "audit_result": "fail",
                    "severity": "blocker",
                    "blocker_count": 1,
                    "warning_count": 0,
                    "details_ref": f"canonical_summary_error: {exc}",
                    "created_at": _utc_now(),
                }

    # Observation-table column validation (only for CSV format).
    # Performed AFTER successful DataManifest loading, AFTER output_manifest is built.
    # - ValueError for non-CSV format propagates to caller (tests assert on this).
    # - Missing columns → append to audit_summary, raise GovernanceRejection.
    # - Columns present → add pass audit entry to audit_summary (non-blocker).
    observation_validation_passed = False
    observation_missing_columns: list[str] = []
    if required_observation_columns is not None and manifest is not None:
        if manifest.source_kind.value != "local_csv":
            raise ValueError(
                f"required_observation_columns is only supported for CSV datasets "
                f"(DataManifest dataset_id='{manifest.dataset_id}' has "
                f"source_kind='{manifest.source_kind.value}')."
            )
        raw_path = Path(manifest.path)
        dataset_resolved = raw_path if raw_path.is_absolute() else (manifest_base_dir / raw_path).resolve()
        missing, ok = _validate_observation_table_columns(dataset_resolved, required_observation_columns)
        observation_missing_columns = missing
        observation_validation_passed = ok

        # Missing columns → append observation_table_shape_validation failure
        # to the existing audit_summary (preserving any prior governance blockers
        # such as autonomous_search). blocker_count is recalculated from all audits.
        if not observation_validation_passed:
            now = _utc_now()
            obs_audit_entry = {
                "audit_name": "observation_table_shape_validation",
                "audit_result": "fail",
                "severity": "blocker",
                "blocker_count": 1,
                "warning_count": 0,
                "details_ref": None,
                "created_at": now,
            }
            new_audits = list(audit_summary["audits"])
            new_audits.append(obs_audit_entry)
            # Append canonical summary audit if it was computed (may be fail or pass)
            if canonical_summary_audit_entry is not None:
                new_audits.append(canonical_summary_audit_entry)
            # Recalculate blocker_count from all audits (governance + observation)
            total_blockers = sum(
                a["blocker_count"] for a in new_audits if a["audit_result"] == "fail"
            )
            audit_summary = {
                "overall_result": "fail",
                "blocker_count": total_blockers,
                "warning_count": audit_summary["warning_count"],
                "audits": new_audits,
            }
            # Build failure_summary with all blockers (governance + observation)
            blocker_parts = [
                f"observation_table_column_validation: missing columns {observation_missing_columns!r}"
            ]
            for a in new_audits:
                if a["audit_result"] == "fail" and a["audit_name"] != "observation_table_shape_validation":
                    blocker_parts.append(f"{a['audit_name']}")
            failure_summary = {
                "failure_type": "validation_error",
                "status": "failed_validation",
                "failed_check": ", ".join(failing for failing in [a["audit_name"] for a in new_audits if a["audit_result"] == "fail"]),
                "blocker_summary": "; ".join(blocker_parts) + f"; Total blockers: {total_blockers}.",
                "missing_data_summary_ref": None,
                "details_ref": None,
                "created_at": now,
            }
            # Build full runner output artifact for GovernanceRejection
            # Include experiment spec in output_manifest (schema requires minItems: 1)
            spec_entry = {
                "output_role": "evidence",
                "output_path": (
                    str(experiment_spec_path.resolve())
                    if experiment_spec_path.is_absolute()
                    else str(experiment_spec_path)
                ),
                "row_count": None,
                "content_hash": f"sha256:{spec_content_hash}",
                "created_at": created_at,
                "format": "json",
                "description": (
                    "Input experiment spec referenced by this dry-run artifact. "
                    "Content hash is of the experiment spec JSON file, providing "
                    "a stable content identifier for the input governance document."
                ),
                "contains_private_data": False,
                "publishable": False,
            }
            failed_artifact = {
                "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
                "runner_output_version": RUNNER_OUTPUT_VERSION,
                "run_id": run_id,
                "run_mode": "dry_run",
                "status": "failed_validation",
                "runner_name": runner_name,
                "runner_version": runner_version,
                "experiment_spec_ref": experiment_id,
                "input_artifact_refs": input_artifact_refs,
                "data_manifest_refs": [manifest.dataset_id],
                "run_config_hash": f"sha256:{run_config_hash}",
                "started_at": started_at,
                "completed_at": completed_at,
                "audit_summary": audit_summary,
                "output_manifest": [spec_entry],
                "created_at": created_at,
                "run_owner": run_owner,
                "failure_summary": failure_summary,
                "partial_summary": None,
                "trial_accounting_summary": trial_accounting_summary,
            }
            raise GovernanceRejection(
                failed_artifact,
                f"Run blocked by {total_blockers} blocker(s): "
                f"observation_table_column_validation: missing columns "
                f"{observation_missing_columns!r}.",
            )
        # Columns present → add pass audit entry (non-blocker; no GovernanceRejection)
        now = _utc_now()
        obs_audit_entry = {
            "audit_name": "observation_table_shape_validation",
            "audit_result": "pass",
            "severity": "blocker",
            "blocker_count": 0,
            "warning_count": 0,
            "details_ref": None,
            "created_at": now,
        }
        new_audits = list(audit_summary["audits"])
        new_audits.append(obs_audit_entry)
        audit_summary = {
            "overall_result": audit_summary["overall_result"],
            "blocker_count": audit_summary["blocker_count"],
            "warning_count": audit_summary["warning_count"],
            "audits": new_audits,
        }

    # -------------------------------------------------------------------------
    # Canonical summary computation (observation_table_canonical_summary audit)
    # Runs after required_observation_columns validation (which may raise).
    # Only applies when a DataManifest is present and date/symbol columns requested.
    # -------------------------------------------------------------------------
    canonical_summary_passed = False
    canonical_summary_details: str | None = None
    if (
        has_canonical_summary_requested
        and manifest is not None
    ):
        if manifest.source_kind.value != "local_csv":
            raise UnsupportedConfig(
                f"observation date/symbol column summary is only supported for CSV datasets "
                f"(DataManifest dataset_id='{manifest.dataset_id}' has "
                f"source_kind='{manifest.source_kind.value}')."
            )
        raw_path = Path(manifest.path)
        dataset_resolved = (
            raw_path if raw_path.is_absolute()
            else (manifest_base_dir / raw_path).resolve()
        )
        now = _utc_now()
        try:
            summary = _summarize_observation_table_canonical(
                dataset_resolved,
                observation_date_column,
                observation_symbol_column,
            )
            canonical_summary_passed = True
            canonical_summary_details = summary["details"]
            canon_audit_entry = {
                "audit_name": "observation_table_canonical_summary",
                "audit_result": "pass",
                "severity": "blocker",
                "blocker_count": 0,
                "warning_count": 0,
                "details_ref": canonical_summary_details,
                "created_at": now,
            }
        except (ValueError, UnsupportedConfig) as exc:
            canonical_summary_failure_type = (
                "unsupported_config" if isinstance(exc, UnsupportedConfig) else "validation_error"
            )
            canonical_summary_details = str(exc)
            canon_audit_entry = {
                "audit_name": "observation_table_canonical_summary",
                "audit_result": "fail",
                "severity": "blocker",
                "blocker_count": 1,
                "warning_count": 0,
                "details_ref": canonical_summary_details,
                "created_at": now,
            }

        new_audits = list(audit_summary["audits"])
        new_audits.append(canon_audit_entry)
        total_blockers = sum(
            a["blocker_count"] for a in new_audits if a["audit_result"] == "fail"
        )
        overall_result = "fail" if total_blockers > 0 else audit_summary["overall_result"]
        audit_summary = {
            "overall_result": overall_result,
            "blocker_count": total_blockers,
            "warning_count": audit_summary["warning_count"],
            "audits": new_audits,
        }

        # If canonical summary failed and no governance blockers existed before,
        # we still need to emit a failed_validation artifact. Do not raise
        # GovernanceRejection — just mark status failed and build failure_summary.
        if not canonical_summary_passed and audit_summary["blocker_count"] > 0:
            # Build failure_summary since we now have blockers
            failing_checks = [
                a["audit_name"] for a in audit_summary["audits"]
                if a["audit_result"] == "fail"
            ]
            failure_summary = {
                "failure_type": canonical_summary_failure_type,
                "status": "failed_validation",
                "failed_check": ", ".join(failing_checks),
                "blocker_summary": (
                    f"observation_table_canonical_summary: {canonical_summary_details}. "
                    f"Total blockers: {audit_summary['blocker_count']}."
                ),
                "missing_data_summary_ref": None,
                "details_ref": None,
                "created_at": now,
            }
            # Build artifact with updated audit_summary and failure_summary
            spec_entry = {
                "output_role": "evidence",
                "output_path": (
                    str(experiment_spec_path.resolve())
                    if experiment_spec_path.is_absolute()
                    else str(experiment_spec_path)
                ),
                "row_count": None,
                "content_hash": f"sha256:{spec_content_hash}",
                "created_at": created_at,
                "format": "json",
                "description": (
                    "Input experiment spec referenced by this dry-run artifact."
                ),
                "contains_private_data": False,
                "publishable": False,
            }
            failed_artifact = {
                "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
                "runner_output_version": RUNNER_OUTPUT_VERSION,
                "run_id": run_id,
                "run_mode": "dry_run",
                "status": "failed_validation",
                "runner_name": runner_name,
                "runner_version": runner_version,
                "experiment_spec_ref": experiment_id,
                "input_artifact_refs": input_artifact_refs,
                "data_manifest_refs": [manifest.dataset_id],
                "run_config_hash": f"sha256:{run_config_hash}",
                "started_at": started_at,
                "completed_at": completed_at,
                "audit_summary": audit_summary,
                "output_manifest": [spec_entry],
                "created_at": created_at,
                "run_owner": run_owner,
                "failure_summary": failure_summary,
                "partial_summary": None,
                "trial_accounting_summary": trial_accounting_summary,
            }
            raise GovernanceRejection(
                failed_artifact,
                f"Run blocked by observation_table_canonical_summary failure: "
                f"{canonical_summary_details}.",
            )

    # -------------------------------------------------------------------------
    # Close-return summary (observation_table_close_return_summary audit)
    # Runs after canonical summary (which validates date/symbol columns).
    # Only applies when --observation-close-column is provided and manifest is
    # present (early validation ensures manifest + date + symbol columns exist).
    # -------------------------------------------------------------------------
    close_return_summary_passed = False
    close_return_summary_details: str | None = None
    if has_close_return_summary_requested and manifest is not None:
        # Unsupported format check
        if manifest.source_kind.value != "local_csv":
            now = _utc_now()
            close_fail_audit = {
                "audit_name": "observation_table_close_return_summary",
                "audit_result": "fail",
                "severity": "blocker",
                "blocker_count": 1,
                "warning_count": 0,
                "details_ref": (
                    f"close_return_summary_error: --observation-close-column is only "
                    f"supported for CSV datasets "
                    f"(source_kind='{manifest.source_kind.value}')."
                ),
                "created_at": now,
            }
            new_audits = list(audit_summary["audits"])
            new_audits.append(close_fail_audit)
            total_b = sum(
                a["blocker_count"] for a in new_audits if a["audit_result"] == "fail"
            )
            failing_checks = [a["audit_name"] for a in new_audits if a["audit_result"] == "fail"]
            close_failure_summary = {
                "failure_type": "unsupported_config",
                "status": "failed_validation",
                "failed_check": ", ".join(failing_checks),
                "blocker_summary": (
                    f"observation_table_close_return_summary: "
                    f"unsupported format; Total blockers: {total_b}."
                ),
                "missing_data_summary_ref": None,
                "details_ref": None,
                "created_at": now,
            }
            spec_entry = {
                "output_role": "evidence",
                "output_path": (
                    str(experiment_spec_path.resolve())
                    if experiment_spec_path.is_absolute()
                    else str(experiment_spec_path)
                ),
                "row_count": None,
                "content_hash": f"sha256:{spec_content_hash}",
                "created_at": created_at,
                "format": "json",
                "description": "Input experiment spec referenced by this dry-run artifact.",
                "contains_private_data": False,
                "publishable": False,
            }
            failed_artifact = {
                "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
                "runner_output_version": RUNNER_OUTPUT_VERSION,
                "run_id": run_id,
                "run_mode": "dry_run",
                "status": "failed_validation",
                "runner_name": runner_name,
                "runner_version": runner_version,
                "experiment_spec_ref": experiment_id,
                "input_artifact_refs": input_artifact_refs,
                "data_manifest_refs": [manifest.dataset_id],
                "run_config_hash": f"sha256:{run_config_hash}",
                "started_at": started_at,
                "completed_at": completed_at,
                "audit_summary": {
                    "overall_result": "fail",
                    "blocker_count": total_b,
                    "warning_count": 0,
                    "audits": new_audits,
                },
                "output_manifest": [spec_entry],
                "created_at": created_at,
                "run_owner": run_owner,
                "failure_summary": close_failure_summary,
                "partial_summary": None,
                "trial_accounting_summary": trial_accounting_summary,
            }
            raise GovernanceRejection(
                failed_artifact,
                f"--observation-close-column is only supported for CSV datasets "
                f"(source_kind='{manifest.source_kind.value}').",
            )

        # CSV: resolve dataset path
        raw_path = Path(manifest.path)
        dataset_resolved = (
            raw_path if raw_path.is_absolute()
            else (manifest_base_dir / raw_path).resolve()
        )
        now = _utc_now()
        try:
            close_result = _summarize_observation_close_returns(
                dataset_resolved,
                observation_date_column=observation_date_column,
                observation_symbol_column=observation_symbol_column,
                observation_close_column=observation_close_column,
            )
            # No valid returns → fail closed
            if close_result["symbols_with_return"] == 0:
                close_return_summary_details = close_result["details"]
                close_fail_audit = {
                    "audit_name": "observation_table_close_return_summary",
                    "audit_result": "fail",
                    "severity": "blocker",
                    "blocker_count": 1,
                    "warning_count": 0,
                    "details_ref": (
                        f"close_return_summary_error: no symbols with valid returns: "
                        f"{close_return_summary_details}."
                    ),
                    "created_at": now,
                }
                close_return_summary_passed = False
            else:
                close_return_summary_passed = True
                close_return_summary_details = close_result["details"]
                close_fail_audit = {
                    "audit_name": "observation_table_close_return_summary",
                    "audit_result": "pass",
                    "severity": "blocker",
                    "blocker_count": 0,
                    "warning_count": 0,
                    "details_ref": close_return_summary_details,
                    "created_at": now,
                }
        except ValueError as exc:
            close_return_summary_details = str(exc)
            close_fail_audit = {
                "audit_name": "observation_table_close_return_summary",
                "audit_result": "fail",
                "severity": "blocker",
                "blocker_count": 1,
                "warning_count": 0,
                "details_ref": f"close_return_summary_error: {close_return_summary_details}",
                "created_at": now,
            }
            close_return_summary_passed = False

        # Append close-return audit to audit_summary
        new_audits = list(audit_summary["audits"])
        new_audits.append(close_fail_audit)
        total_blockers = sum(
            a["blocker_count"] for a in new_audits if a["audit_result"] == "fail"
        )
        overall_result = (
            "fail" if total_blockers > 0 else audit_summary["overall_result"]
        )
        audit_summary = {
            "overall_result": overall_result,
            "blocker_count": total_blockers,
            "warning_count": audit_summary["warning_count"],
            "audits": new_audits,
        }

        # If close-return summary failed → emit failed_validation artifact
        if not close_return_summary_passed and audit_summary["blocker_count"] > 0:
            failing_checks = [
                a["audit_name"] for a in audit_summary["audits"]
                if a["audit_result"] == "fail"
            ]
            failure_summary = {
                "failure_type": "validation_error",
                "status": "failed_validation",
                "failed_check": ", ".join(failing_checks),
                "blocker_summary": (
                    f"observation_table_close_return_summary: "
                    f"{close_return_summary_details}. "
                    f"Total blockers: {audit_summary['blocker_count']}."
                ),
                "missing_data_summary_ref": None,
                "details_ref": None,
                "created_at": now,
            }
            spec_entry = {
                "output_role": "evidence",
                "output_path": (
                    str(experiment_spec_path.resolve())
                    if experiment_spec_path.is_absolute()
                    else str(experiment_spec_path)
                ),
                "row_count": None,
                "content_hash": f"sha256:{spec_content_hash}",
                "created_at": created_at,
                "format": "json",
                "description": "Input experiment spec referenced by this dry-run artifact.",
                "contains_private_data": False,
                "publishable": False,
            }
            failed_artifact = {
                "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
                "runner_output_version": RUNNER_OUTPUT_VERSION,
                "run_id": run_id,
                "run_mode": "dry_run",
                "status": "failed_validation",
                "runner_name": runner_name,
                "runner_version": runner_version,
                "experiment_spec_ref": experiment_id,
                "input_artifact_refs": input_artifact_refs,
                "data_manifest_refs": [manifest.dataset_id],
                "run_config_hash": f"sha256:{run_config_hash}",
                "started_at": started_at,
                "completed_at": completed_at,
                "audit_summary": audit_summary,
                "output_manifest": [spec_entry],
                "created_at": created_at,
                "run_owner": run_owner,
                "failure_summary": failure_summary,
                "partial_summary": None,
                "trial_accounting_summary": trial_accounting_summary,
            }
            raise GovernanceRejection(
                failed_artifact,
                f"Run blocked by observation_table_close_return_summary failure: "
                f"{close_return_summary_details}.",
            )

    # -------------------------------------------------------------------------
    # Duplicate-row summary (observation_table_duplicate_row_summary audit).
    # Runs when BOTH date and symbol columns are provided along with a CSV
    # manifest. Requires both columns since duplicate key is (symbol, date).
    # -------------------------------------------------------------------------
    has_dup_summary_requested = (
        observation_date_column is not None
        and observation_date_column.strip()
        and observation_symbol_column is not None
        and observation_symbol_column.strip()
    )
    if (
        has_dup_summary_requested
        and manifest is not None
    ):
        # Unsupported format check — mirror canonical/close-return pattern
        if manifest.source_kind.value != "local_csv":
            now = _utc_now()
            dup_fail_audit = {
                "audit_name": "observation_table_duplicate_row_summary",
                "audit_result": "fail",
                "severity": "blocker",
                "blocker_count": 1,
                "warning_count": 0,
                "details_ref": (
                    f"duplicate_row_summary_error: --observation-date-column/ "
                    f"--observation-symbol-column is only supported for CSV "
                    f"(dataset has source_kind='{manifest.source_kind.value}')."
                ),
                "created_at": now,
            }
            new_audits = list(audit_summary["audits"])
            new_audits.append(dup_fail_audit)
            total_b = sum(
                a["blocker_count"] for a in new_audits
                if a["audit_result"] == "fail"
            )
            failing_checks = [a["audit_name"] for a in new_audits if a["audit_result"] == "fail"]
            dup_failure_summary = {
                "failure_type": "unsupported_config",
                "status": "failed_validation",
                "failed_check": ", ".join(failing_checks),
                "blocker_summary": (
                    f"observation_table_duplicate_row_summary: "
                    f"unsupported format; Total blockers: {total_b}."
                ),
                "missing_data_summary_ref": None,
                "details_ref": None,
                "created_at": now,
            }
            spec_entry = {
                "output_role": "evidence",
                "output_path": (
                    str(experiment_spec_path.resolve())
                    if experiment_spec_path.is_absolute()
                    else str(experiment_spec_path)
                ),
                "row_count": None,
                "content_hash": f"sha256:{spec_content_hash}",
                "created_at": created_at,
                "format": "json",
                "description": "Input experiment spec referenced by this dry-run artifact.",
                "contains_private_data": False,
                "publishable": False,
            }
            failed_artifact = {
                "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
                "runner_output_version": RUNNER_OUTPUT_VERSION,
                "run_id": run_id,
                "run_mode": "dry_run",
                "status": "failed_validation",
                "runner_name": runner_name,
                "runner_version": runner_version,
                "experiment_spec_ref": experiment_id,
                "input_artifact_refs": input_artifact_refs,
                "data_manifest_refs": [manifest.dataset_id],
                "run_config_hash": f"sha256:{run_config_hash}",
                "started_at": started_at,
                "completed_at": completed_at,
                "audit_summary": {
                    "overall_result": "fail",
                    "blocker_count": total_b,
                    "warning_count": 0,
                    "audits": new_audits,
                },
                "output_manifest": [spec_entry],
                "created_at": created_at,
                "run_owner": run_owner,
                "failure_summary": dup_failure_summary,
                "partial_summary": None,
                "trial_accounting_summary": trial_accounting_summary,
            }
            raise GovernanceRejection(
                failed_artifact,
                f"--observation-date-column/--observation-symbol-column "
                f"duplicate-row check is only supported for CSV datasets "
                f"(source_kind='{manifest.source_kind.value}').",
            )

        # CSV: resolve dataset path
        raw_path = Path(manifest.path)
        dataset_resolved = (
            raw_path if raw_path.is_absolute()
            else (manifest_base_dir / raw_path).resolve()
        )
        now = _utc_now()
        try:
            dup_result = _summarize_observation_duplicate_rows(
                dataset_resolved,
                observation_date_column=observation_date_column,
                observation_symbol_column=observation_symbol_column,
            )
            dup_passed = True
            dup_details = dup_result["details"]
        except ValueError as exc:
            dup_passed = False
            dup_details = f"duplicate_row_summary_error: {exc}"

        if dup_passed:
            dup_audit = {
                "audit_name": "observation_table_duplicate_row_summary",
                "audit_result": "pass" if not dup_result["has_duplicates"] else "fail",
                "severity": "info",
                "blocker_count": 0,
                "warning_count": 0,
                "details_ref": dup_details,
                "created_at": now,
            }
        else:
            dup_audit = {
                "audit_name": "observation_table_duplicate_row_summary",
                "audit_result": "fail",
                "severity": "blocker",
                "blocker_count": 1,
                "warning_count": 0,
                "details_ref": dup_details,
                "created_at": now,
            }

        new_audits = list(audit_summary["audits"])
        new_audits.append(dup_audit)
        total_blockers = sum(
            a["blocker_count"] for a in new_audits if a["audit_result"] == "fail"
        )
        overall_result = (
            "fail" if total_blockers > 0 else audit_summary["overall_result"]
        )
        audit_summary = {
            "overall_result": overall_result,
            "blocker_count": total_blockers,
            "warning_count": audit_summary["warning_count"],
            "audits": new_audits,
        }

        # If duplicate check failed and we now have blockers, build failure_summary
        # but do NOT raise — let the terminal status block handle rejection
        if not dup_passed and audit_summary["blocker_count"] > 0:
            failing_checks = [
                a["audit_name"] for a in audit_summary["audits"]
                if a["audit_result"] == "fail"
            ]
            failure_summary = {
                "failure_type": "validation_error",
                "status": "failed_validation",
                "failed_check": ", ".join(failing_checks),
                "blocker_summary": (
                    f"observation_table_duplicate_row_summary: {dup_details}. "
                    f"Total blockers: {audit_summary['blocker_count']}."
                ),
                "missing_data_summary_ref": None,
                "details_ref": None,
                "created_at": now,
            }
            spec_entry = {
                "output_role": "evidence",
                "output_path": (
                    str(experiment_spec_path.resolve())
                    if experiment_spec_path.is_absolute()
                    else str(experiment_spec_path)
                ),
                "row_count": None,
                "content_hash": f"sha256:{spec_content_hash}",
                "created_at": created_at,
                "format": "json",
                "description": "Input experiment spec referenced by this dry-run artifact.",
                "contains_private_data": False,
                "publishable": False,
            }
            failed_artifact = {
                "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
                "runner_output_version": RUNNER_OUTPUT_VERSION,
                "run_id": run_id,
                "run_mode": "dry_run",
                "status": "failed_validation",
                "runner_name": runner_name,
                "runner_version": runner_version,
                "experiment_spec_ref": experiment_id,
                "input_artifact_refs": input_artifact_refs,
                "data_manifest_refs": [manifest.dataset_id],
                "run_config_hash": f"sha256:{run_config_hash}",
                "started_at": started_at,
                "completed_at": completed_at,
                "audit_summary": audit_summary,
                "output_manifest": [spec_entry],
                "created_at": created_at,
                "run_owner": run_owner,
                "failure_summary": failure_summary,
                "partial_summary": None,
                "trial_accounting_summary": trial_accounting_summary,
            }
            raise GovernanceRejection(
                failed_artifact,
                f"Run blocked by observation_table_duplicate_row_summary failure: "
                f"{dup_details}.",
            )

    # -------------------------------------------------------------------------
    # Date-coverage summary (observation_table_date_coverage_summary audit).
    # Runs when BOTH date and symbol columns are provided along with a CSV
    # manifest. Reuses the same has_dup_summary_requested guard.
    # -------------------------------------------------------------------------
    if (
        has_dup_summary_requested
        and manifest is not None
    ):
        # Unsupported format check — mirror duplicate-row pattern
        if manifest.source_kind.value != "local_csv":
            now = _utc_now()
            dc_fail_audit = {
                "audit_name": "observation_table_date_coverage_summary",
                "audit_result": "fail",
                "severity": "blocker",
                "blocker_count": 1,
                "warning_count": 0,
                "details_ref": (
                    f"date_coverage_summary_error: --observation-date-column/ "
                    f"--observation-symbol-column is only supported for CSV "
                    f"(dataset has source_kind='{manifest.source_kind.value}')."
                ),
                "created_at": now,
            }
            new_audits = list(audit_summary["audits"])
            new_audits.append(dc_fail_audit)
            total_b = sum(
                a["blocker_count"] for a in new_audits
                if a["audit_result"] == "fail"
            )
            failing_checks = [
                a["audit_name"] for a in new_audits
                if a["audit_result"] == "fail"
            ]
            dc_failure_summary = {
                "failure_type": "unsupported_config",
                "status": "failed_validation",
                "failed_check": ", ".join(failing_checks),
                "blocker_summary": (
                    f"observation_table_date_coverage_summary: "
                    f"unsupported format; Total blockers: {total_b}."
                ),
                "missing_data_summary_ref": None,
                "details_ref": None,
                "created_at": now,
            }
            spec_entry = {
                "output_role": "evidence",
                "output_path": (
                    str(experiment_spec_path.resolve())
                    if experiment_spec_path.is_absolute()
                    else str(experiment_spec_path)
                ),
                "row_count": None,
                "content_hash": f"sha256:{spec_content_hash}",
                "created_at": created_at,
                "format": "json",
                "description": (
                    "Input experiment spec referenced by this dry-run artifact. "
                    "Content hash is of the experiment spec JSON file, providing "
                    "a stable content identifier for the input governance document."
                ),
                "contains_private_data": False,
                "publishable": False,
            }
            failed_artifact = {
                "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
                "runner_output_version": RUNNER_OUTPUT_VERSION,
                "run_id": run_id,
                "run_mode": "dry_run",
                "status": "failed_validation",
                "runner_name": runner_name,
                "runner_version": runner_version,
                "experiment_spec_ref": experiment_id,
                "input_artifact_refs": input_artifact_refs,
                "data_manifest_refs": [manifest.dataset_id],
                "run_config_hash": f"sha256:{run_config_hash}",
                "started_at": started_at,
                "completed_at": completed_at,
                "audit_summary": {
                    "overall_result": "fail",
                    "blocker_count": total_b,
                    "warning_count": 0,
                    "audits": new_audits,
                },
                "output_manifest": [spec_entry],
                "created_at": created_at,
                "run_owner": run_owner,
                "failure_summary": dc_failure_summary,
                "partial_summary": None,
                "trial_accounting_summary": trial_accounting_summary,
            }
            raise GovernanceRejection(
                failed_artifact,
                f"--observation-date-column/--observation-symbol-column "
                f"date-coverage check is only supported for CSV datasets "
                f"(source_kind='{manifest.source_kind.value}').",
            )

        # CSV: resolve dataset path
        raw_path = Path(manifest.path)
        dataset_resolved = (
            raw_path if raw_path.is_absolute()
            else (manifest_base_dir / raw_path).resolve()
        )
        now = _utc_now()
        try:
            dc_result = _summarize_observation_date_coverage(
                dataset_resolved,
                observation_date_column=observation_date_column,
                observation_symbol_column=observation_symbol_column,
            )
            dc_passed = True
            dc_details = dc_result["details"]
        except ValueError as exc:
            dc_passed = False
            dc_details = f"date_coverage_summary_error: {exc}"

        if dc_passed:
            dc_audit = {
                "audit_name": "observation_table_date_coverage_summary",
                "audit_result": "pass",
                "severity": "info",
                "blocker_count": 0,
                "warning_count": 0,
                "details_ref": dc_details,
                "created_at": now,
            }
        else:
            dc_audit = {
                "audit_name": "observation_table_date_coverage_summary",
                "audit_result": "fail",
                "severity": "blocker",
                "blocker_count": 1,
                "warning_count": 0,
                "details_ref": dc_details,
                "created_at": now,
            }
            dc_passed = False

        # Append date-coverage audit; recompute blocker_count from full audit list
        new_audits = list(audit_summary["audits"])
        new_audits.append(dc_audit)
        total_blockers = sum(
            a["blocker_count"] for a in new_audits if a["audit_result"] == "fail"
        )
        overall_result = (
            "fail" if total_blockers > 0 else audit_summary["overall_result"]
        )
        audit_summary = {
            "overall_result": overall_result,
            "blocker_count": total_blockers,
            "warning_count": audit_summary["warning_count"],
            "audits": new_audits,
        }

        # If date-coverage summary failed with blockers → emit failed_validation
        if not dc_passed and audit_summary["blocker_count"] > 0:
            failing_checks = [
                a["audit_name"] for a in audit_summary["audits"]
                if a["audit_result"] == "fail"
            ]
            failure_summary = {
                "failure_type": "validation_error",
                "status": "failed_validation",
                "failed_check": ", ".join(failing_checks),
                "blocker_summary": (
                    f"observation_table_date_coverage_summary: {dc_details}. "
                    f"Total blockers: {audit_summary['blocker_count']}."
                ),
                "missing_data_summary_ref": None,
                "details_ref": None,
                "created_at": now,
            }
            spec_entry = {
                "output_role": "evidence",
                "output_path": (
                    str(experiment_spec_path.resolve())
                    if experiment_spec_path.is_absolute()
                    else str(experiment_spec_path)
                ),
                "row_count": None,
                "content_hash": f"sha256:{spec_content_hash}",
                "created_at": created_at,
                "format": "json",
                "description": (
                    "Input experiment spec referenced by this dry-run artifact. "
                    "Content hash is of the experiment spec JSON file, providing "
                    "a stable content identifier for the input governance document."
                ),
                "contains_private_data": False,
                "publishable": False,
            }
            failed_artifact = {
                "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
                "runner_output_version": RUNNER_OUTPUT_VERSION,
                "run_id": run_id,
                "run_mode": "dry_run",
                "status": "failed_validation",
                "runner_name": runner_name,
                "runner_version": runner_version,
                "experiment_spec_ref": experiment_id,
                "input_artifact_refs": input_artifact_refs,
                "data_manifest_refs": [manifest.dataset_id],
                "run_config_hash": f"sha256:{run_config_hash}",
                "started_at": started_at,
                "completed_at": completed_at,
                "audit_summary": audit_summary,
                "output_manifest": [spec_entry],
                "created_at": created_at,
                "run_owner": run_owner,
                "failure_summary": failure_summary,
                "partial_summary": None,
                "trial_accounting_summary": trial_accounting_summary,
            }
            raise GovernanceRejection(
                failed_artifact,
                f"Run blocked by observation_table_date_coverage_summary failure: "
                f"{dc_details}.",
            )

    output_manifest = [
        {
            "output_role": "evidence",
            "output_path": (
                str(experiment_spec_path.resolve())
                if experiment_spec_path.is_absolute()
                else str(experiment_spec_path)
            ),
            "row_count": None,
            "content_hash": f"sha256:{spec_content_hash}",
            "created_at": created_at,
            "format": "json",
            "description": (
                "Input experiment spec referenced by this dry-run artifact. "
                "Content hash is of the experiment spec JSON file, providing "
                "a stable content identifier for the input governance document."
            ),
            "contains_private_data": False,
            "publishable": False,
        }
    ]

    artifact = {
        "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
        "runner_output_version": RUNNER_OUTPUT_VERSION,
        "run_id": run_id,
        "run_mode": "dry_run",
        "status": status,
        "runner_name": runner_name,
        "runner_version": runner_version,
        "experiment_spec_ref": experiment_id,
        "input_artifact_refs": input_artifact_refs,
        "data_manifest_refs": data_manifest_refs,
        "run_config_hash": f"sha256:{run_config_hash}",
        "started_at": started_at,
        "completed_at": completed_at,
        "audit_summary": audit_summary,
        "output_manifest": output_manifest,
        "created_at": created_at,
        "run_owner": run_owner,
        "failure_summary": failure_summary,
        "partial_summary": None,
        "trial_accounting_summary": trial_accounting_summary,
    }

    # Raise GovernanceRejection if blockers exist so main() can emit
    # the failed_validation artifact before exiting nonzero.
    if audit_summary["blocker_count"] > 0:
        raise GovernanceRejection(
            artifact,
            f"Governance validation failed: {audit_summary['blocker_count']} blocker(s) found. "
            f"Refusing to emit success artifact. "
            f"Run status set to 'failed_validation'."
        )

    return artifact


def write_runner_output(
    output_path: str | Path,
    artifact: dict,
) -> None:
    """
    Serialize artifact to output_path as sorted, indented JSON.

    Fails closed if the output file already exists (no overwrite without --force).

    Parameters
    ----------
    output_path : str | Path
        Destination file path.
    artifact : dict
        RunnerOutput artifact to serialize.

    Raises
    ------
    FileExistsError
        If output_path already exists.
    OSError
        If the output parent directory cannot be created.
    """
    output_path = Path(output_path)

    if output_path.exists():
        raise FileExistsError(
            f"Output path already exists: {output_path}. "
            "Remove it or use an explicit --force flag to overwrite."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write ONCE. content_hash is already correct (computed from experiment
    # spec bytes in build_runner_output) so no re-write is needed.
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """
    Dry-run runner CLI entry point.

    Exits 0 on success, 1 on user error (missing file, missing field,
    existing output path, governance rejection), 2 on unexpected errors.
    """
    parser = argparse.ArgumentParser(
        prog="run_first_thin_real_data_runner",
        description=(
            "AED first thin real-data runner dry-run CLI. "
            "Reads an ExperimentSpec, optionally reads a DataManifest, "
            "validates governance inputs, and emits a RunnerOutput v1 artifact. "
            "No real backtest execution, no registry writes, no live trading."
        ),
    )
    parser.add_argument(
        "--experiment-spec",
        required=True,
        help="Path to ExperimentSpec JSON file.",
    )
    parser.add_argument(
        "--output-path",
        required=True,
        help="Path to write RunnerOutput JSON artifact.",
    )
    parser.add_argument(
        "--run-owner",
        required=True,
        help="Identity declaring ownership of this run.",
    )
    parser.add_argument(
        "--runner-name",
        default=RUNNER_NAME_DEFAULT,
        help=f"Runner name (default: {RUNNER_NAME_DEFAULT}).",
    )
    parser.add_argument(
        "--runner-version",
        default=RUNNER_VERSION_DEFAULT,
        help=f"Runner version (default: {RUNNER_VERSION_DEFAULT}).",
    )
    parser.add_argument(
        "--data-manifest",
        required=False,
        default=None,
        help=(
            "Optional path to a DataManifest JSON file. "
            "When provided, the manifest is loaded and validated. "
            "If absent, uses dry_run_no_data_manifest placeholder."
        ),
    )
    parser.add_argument(
        "--required-observation-columns",
        required=False,
        default=None,
        help=(
            "Optional comma-separated list of column names that must be present "
            "in the observation table CSV referenced by the DataManifest. "
            "Only supported for CSV datasets. "
            "Example: --required-observation-columns event_id,symbol,close_price"
        ),
    )
    parser.add_argument(
        "--observation-date-column",
        required=False,
        default=None,
        help=(
            "Optional name of the date column in the observation table CSV. "
            "Used for canonical summary (min/max date). "
            "Requires --data-manifest pointing to a CSV. "
            "Lexicographic min/max is used (not date parsing). "
            "Example: --observation-date-column date"
        ),
    )
    parser.add_argument(
        "--observation-symbol-column",
        required=False,
        default=None,
        help=(
            "Optional name of the symbol/ticker column in the observation table CSV. "
            "Used for canonical summary (unique symbol count). "
            "Requires --data-manifest pointing to a CSV. "
            "Example: --observation-symbol-column symbol"
        ),
    )
    parser.add_argument(
        "--observation-close-column",
        required=False,
        default=None,
        help=(
            "Optional name of the close price column in the observation table CSV. "
            "Used for per-symbol first/last close return summary (symbols_with_return, "
            "min_return, max_return, mean_return). "
            "Requires --data-manifest pointing to a CSV. "
            "Requires --observation-date-column and --observation-symbol-column. "
            "Example: --observation-close-column close"
        ),
    )
    parser.add_argument(
        "--observation-missing-value-columns",
        required=False,
        default=None,
        help=(
            "Optional comma-separated list of column names to summarize missing values for "
            "in the observation table CSV (row_count, missing count per column). "
            "Requires --data-manifest pointing to a CSV. "
            "Only supported for CSV datasets. "
            "Example: --observation-missing-value-columns volume,bid,ask"
        ),
    )
    # Trial-accounting flags
    parser.add_argument(
        "--trial-accounting-status",
        required=False,
        default=None,
        choices=sorted(STATUS_ENUM_VALUES),
        help=(
            "Trial accounting status enum value. "
            "Allowed: not_applicable, proposed, linked, blocked. "
            "Defaults to 'proposed' when any trial-accounting flag is supplied."
        ),
    )
    parser.add_argument(
        "--trial-accounting-mutation-mode",
        required=False,
        default=None,
        help=(
            "Trial accounting mutation mode. "
            "Allowed: no_mutation, dry_run_reference_only. "
            "Defaults to 'dry_run_reference_only'. "
            "REJECTED: ledger_write, registry_write (raises error)."
        ),
    )
    parser.add_argument(
        "--search-space-id",
        required=False,
        default=None,
        help="Search space ID for trial accounting.",
    )
    parser.add_argument(
        "--trial-family-id",
        required=False,
        default=None,
        help="Trial family ID for trial accounting.",
    )
    parser.add_argument(
        "--trial-id",
        required=False,
        default=None,
        help="Trial ID for trial accounting.",
    )
    parser.add_argument(
        "--proposed-trial-id",
        required=False,
        default=None,
        help="Proposed trial ID for trial accounting.",
    )
    parser.add_argument(
        "--variant-id",
        required=False,
        default=None,
        help="Variant ID for trial accounting.",
    )
    parser.add_argument(
        "--selected-variant-id",
        required=False,
        default=None,
        help="Selected variant ID for trial accounting.",
    )
    parser.add_argument(
        "--model-assessment-id",
        required=False,
        default=None,
        help="Model assessment ID for trial accounting.",
    )
    parser.add_argument(
        "--review-packet-id",
        required=False,
        default=None,
        help="Review packet ID for trial accounting.",
    )
    parser.add_argument(
        "--n-tried",
        required=False,
        type=_non_negative_int_cli_arg("n_tried"),
        default=None,
        help="Number of trials tried.",
    )
    parser.add_argument(
        "--candidate-variant-count",
        required=False,
        type=_non_negative_int_cli_arg("candidate_variant_count"),
        default=None,
        help="Candidate variant count.",
    )
    parser.add_argument(
        "--failed-variant-count",
        required=False,
        type=_non_negative_int_cli_arg("failed_variant_count"),
        default=None,
        help="Failed variant count.",
    )
    parser.add_argument(
        "--all-variants-preserved",
        required=False,
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Whether all variants were preserved. Use --all-variants-preserved for True, --no-all-variants-preserved for False.",
    )
    parser.add_argument(
        "--sample-length",
        required=False,
        type=_non_negative_int_cli_arg("sample_length"),
        default=None,
        help="Sample length for trial accounting.",
    )
    parser.add_argument(
        "--sample-to-trial-ratio",
        required=False,
        type=_non_negative_float_cli_arg("sample_to_trial_ratio"),
        default=None,
        help="Sample-to-trial ratio.",
    )
    parser.add_argument(
        "--trial-accounting-notes",
        required=False,
        default=None,
        help="Notes for trial accounting.",
    )
    # Complexity flags
    parser.add_argument(
        "--complexity-rule-count",
        required=False,
        type=int,
        default=None,
        help="Complexity: rule count.",
    )
    parser.add_argument(
        "--complexity-parameter-count",
        required=False,
        type=int,
        default=None,
        help="Complexity: parameter count.",
    )
    parser.add_argument(
        "--complexity-signal-count",
        required=False,
        type=int,
        default=None,
        help="Complexity: signal count.",
    )
    parser.add_argument(
        "--complexity-filter-count",
        required=False,
        type=int,
        default=None,
        help="Complexity: filter count.",
    )
    parser.add_argument(
        "--complexity-bucket",
        required=False,
        default=None,
        choices=sorted(COMPLEXITY_BUCKET_ENUM_VALUES),
        help="Complexity bucket label.",
    )

    args = parser.parse_args(argv)

    # Validate experiment spec path exists
    experiment_spec_path = Path(args.experiment_spec)
    if not experiment_spec_path.exists():
        print(f"ERROR: experiment spec not found: {experiment_spec_path}", file=sys.stderr)
        return 1

    # Validate data manifest path exists early (before building artifact)
    data_manifest_path: Path | None = None
    if args.data_manifest is not None:
        data_manifest_path = Path(args.data_manifest)
        if not data_manifest_path.exists():
            print(f"ERROR: data manifest not found: {data_manifest_path}", file=sys.stderr)
            return 1

    # Parse required observation columns
    required_observation_columns: list[str] | None = None
    if args.required_observation_columns is not None:
        try:
            required_observation_columns = _parse_required_columns(args.required_observation_columns)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    # Parse optional date/symbol columns for canonical summary
    observation_date_column = _normalize_optional_column_name(args.observation_date_column)
    observation_symbol_column = _normalize_optional_column_name(args.observation_symbol_column)
    observation_close_column = _normalize_optional_column_name(args.observation_close_column)

    # Parse optional missing-value columns
    observation_missing_value_columns: list[str] | None = None
    if args.observation_missing_value_columns is not None:
        try:
            observation_missing_value_columns = _parse_required_columns(
                args.observation_missing_value_columns
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    # Build artifact — may raise GovernanceRejection if governance blockers found
    try:
        artifact = build_runner_output(
            experiment_spec_path=experiment_spec_path,
            runner_name=args.runner_name,
            runner_version=args.runner_version,
            run_owner=args.run_owner,
            data_manifest_path=data_manifest_path,
            required_observation_columns=required_observation_columns,
            observation_date_column=observation_date_column,
            observation_symbol_column=observation_symbol_column,
            observation_close_column=observation_close_column,
            observation_missing_value_columns=observation_missing_value_columns,
            trial_accounting_status=args.trial_accounting_status,
            trial_accounting_mutation_mode=args.trial_accounting_mutation_mode,
            search_space_id=args.search_space_id,
            trial_family_id=args.trial_family_id,
            trial_id=args.trial_id,
            proposed_trial_id=args.proposed_trial_id,
            variant_id=args.variant_id,
            selected_variant_id=args.selected_variant_id,
            model_assessment_id=args.model_assessment_id,
            review_packet_id=args.review_packet_id,
            n_tried=args.n_tried,
            candidate_variant_count=args.candidate_variant_count,
            failed_variant_count=args.failed_variant_count,
            all_variants_preserved=args.all_variants_preserved,
            sample_length=args.sample_length,
            sample_to_trial_ratio=args.sample_to_trial_ratio,
            trial_accounting_notes=args.trial_accounting_notes,
            complexity_rule_count=args.complexity_rule_count,
            complexity_parameter_count=args.complexity_parameter_count,
            complexity_signal_count=args.complexity_signal_count,
            complexity_filter_count=args.complexity_filter_count,
            complexity_bucket=args.complexity_bucket,
        )
    except GovernanceRejection as exc:
        # Governance rejection: emit the failed_validation artifact and exit 1
        print(f"ERROR: {exc.message}", file=sys.stderr)
        try:
            write_runner_output(args.output_path, exc.artifact)
            print(f"Governance-rejected RunnerOutput written to: {args.output_path}", file=sys.stderr)
        except FileExistsError:
            print(f"ERROR: output path already exists: {args.output_path}", file=sys.stderr)
            return 1
        except OSError as write_exc:
            print(f"ERROR writing governance-rejected output: {write_exc}", file=sys.stderr)
            return 2
        return 1
    except ValueError as exc:
        # Expected user validation error (e.g. missing experiment_spec_id).
        # Emit a meaningful message and exit 1.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR building runner output: {exc}", file=sys.stderr)
        return 2

    # Check returned artifact status — DataManifest validation failures return
    # a failed_validation artifact without raising an exception.
    if artifact["status"] == "failed_validation":
        failure_msg = (
            f"Dry-run validation failed: "
            f"blocker_count={artifact['audit_summary']['blocker_count']}. "
            f"Run status set to 'failed_validation'."
        )
        print(f"ERROR: {failure_msg}", file=sys.stderr)
        try:
            write_runner_output(args.output_path, artifact)
            print(f"Failed-validation RunnerOutput written to: {args.output_path}", file=sys.stderr)
        except FileExistsError:
            print(f"ERROR: output path already exists: {args.output_path}", file=sys.stderr)
            return 1
        except OSError as write_exc:
            print(f"ERROR writing failed-validation output: {write_exc}", file=sys.stderr)
            return 2
        return 1

    # Write output
    try:
        write_runner_output(args.output_path, artifact)
    except FileExistsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR writing output: {exc}", file=sys.stderr)
        return 2

    print(f"Dry-run complete. RunnerOutput written to: {args.output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
