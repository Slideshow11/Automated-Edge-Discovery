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
    stripped and appended. When observation_close_column is provided, it is
    stripped and appended.

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


def _count_csv_rows(file_path: Path) -> int | None:
    """
    Count data rows in a CSV file using streaming line iteration.

    Excludes the header row (first line) from the count.

    Returns None if the file cannot be read as CSV.
    """
    try:
        with open(file_path, "r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header is None:
                return 0
            count = sum(1 for _ in reader)
            return count
    except Exception:
        # If any error occurs (encoding, malformed CSV, etc.), return None
        # rather than propagating the error.
        return None


# ---------------------------------------------------------------------------
# Observation-table column validation
# ---------------------------------------------------------------------------

def _parse_required_columns(raw: str | None) -> list[str]:
    """
    Parse a comma-separated required-column string into a normalized list.

    - Trims whitespace from each token.
    - Rejects empty tokens (e.g. "a,,b" → ValueError).
    - De-duplicates preserving first occurrence.
    - Returns list in order of first occurrence (stable for hashing: caller
      normalizes to sorted for deterministic run_config_hash).

    Raises ValueError if any token is empty after trimming.
    """
    if raw is None:
        return []

    tokens = [t.strip() for t in raw.split(",")]
    # Check for empty tokens
    for t in tokens:
        if t == "":
            raise ValueError(
                "required_observation_columns contains an empty token; "
                "each column name must be non-empty"
            )

    # De-duplicate preserving first occurrence
    seen: set[str] = set()
    normalized: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            normalized.append(t)

    return normalized


def _normalize_optional_column_name(raw: str | None) -> str | None:
    """
    Normalize an optional column name: strip leading/trailing whitespace.

    Returns None if raw is None or empty string.
    Internal whitespace is preserved.
    """
    if raw is None:
        return None
    stripped = raw.strip()
    if stripped == "":
        return None
    return stripped


def _summarize_observation_table_canonical(
    dataset_path: Path,
    observation_date_column: str | None,
    observation_symbol_column: str | None,
) -> dict:
    """
    Compute a canonical summary of a CSV observation table in a single pass
    using csv.DictReader.

    Parameters
    ----------
    dataset_path : Path
        Path to the CSV observation table file.
    observation_date_column : str | None
        Column name to use for min/max date computation. If None, skipped.
    observation_symbol_column : str | None
        Column name to use for unique symbol count. If None, skipped.

    Returns
    -------
    dict
        Canonical summary dict with keys:
        - row_count (int): total non-header rows read
        - min_date (str | None): lexicographic minimum of non-empty date values
        - max_date (str | None): lexicographic maximum of non-empty date values
        - unique_symbol_count (int | None): count of distinct non-empty symbol values
        - date_column (str | None): the date column name used
        - symbol_column (str | None): the symbol column name used
        - details (str): human-readable summary string for audit details_ref

    Raises
    ------
    FileNotFoundError
        If dataset_path does not exist.
    ValueError
        If observation_date_column or observation_symbol_column is not in the CSV header.
    """
    row_count = 0
    date_values: list[str] = []
    symbol_values: set[str] = set()
    date_col = observation_date_column.strip() if observation_date_column else None
    symbol_col = observation_symbol_column.strip() if observation_symbol_column else None

    with open(dataset_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        # Validate requested columns are present in header
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header row: {dataset_path}")
        header_set = {f.strip() for f in reader.fieldnames}
        if date_col is not None and date_col not in header_set:
            raise ValueError(
                f"observation_date_column '{observation_date_column}' "
                f"not found in CSV header: {list(reader.fieldnames)}"
            )
        if symbol_col is not None and symbol_col not in header_set:
            raise ValueError(
                f"observation_symbol_column '{observation_symbol_column}' "
                f"not found in CSV header: {list(reader.fieldnames)}"
            )

        for row in reader:
            row_count += 1
            if date_col is not None:
                val = row.get(date_col, "").strip()
                if val:
                    date_values.append(val)
            if symbol_col is not None:
                val = row.get(symbol_col, "").strip()
                if val:
                    symbol_values.add(val)

    min_date = min(date_values) if date_values else None
    max_date = max(date_values) if date_values else None
    unique_symbol_count = len(symbol_values) if symbol_values else None

    # Build details string for audit details_ref
    parts = [f"row_count={row_count}"]
    if date_col is not None:
        if min_date is not None and max_date is not None:
            parts.append(f"date_column={date_col}")
            parts.append(f"min_date={min_date}")
            parts.append(f"max_date={max_date}")
        else:
            parts.append(f"date_column={date_col}")
            parts.append("min_date=None")
            parts.append("max_date=None")
    if symbol_col is not None:
        parts.append(f"symbol_column={symbol_col}")
        parts.append(f"unique_symbol_count={unique_symbol_count}")

    return {
        "row_count": row_count,
        "min_date": min_date,
        "max_date": max_date,
        "unique_symbol_count": unique_symbol_count,
        "date_column": date_col,
        "symbol_column": symbol_col,
        "details": "; ".join(parts),
    }


def _summarize_observation_close_returns(
    dataset_path: Path,
    observation_date_column: str,
    observation_symbol_column: str,
    observation_close_column: str,
) -> dict:
    """
    Compute per-symbol first/last close return summary from a CSV observation table
    in a single pass using csv.DictReader.

    Parameters
    ----------
    dataset_path : Path
        Path to the CSV observation table file.
    observation_date_column : str
        Column name for date values (used for first/last ordering).
    observation_symbol_column : str
        Column name for symbol/ticker values.
    observation_close_column : str
        Column name for close price values.

    Returns
    -------
    dict
        Close-return summary dict with keys:
        - symbols_with_return (int): number of symbols with ≥2 distinct dates
          and valid non-zero first close
        - min_return (float | None): minimum simple return across symbols
        - max_return (float | None): maximum simple return across symbols
        - mean_return (float | None): mean simple return across symbols
        - close_column (str): the close column name used
        - skipped_symbols (int): number of symbols skipped (no valid return)
        - details (str): human-readable summary string for audit details_ref

    Raises
    ------
    ValueError
        If observation_close_column is not in the CSV header.
        (Missing date/symbol columns raise in _summarize_observation_table_canonical
        which is called before this function in build_runner_output.)
    """
    # Per-symbol state: track first and last (by lexicographic date) close.
    # We store first_valid_date and first_valid_close for the earliest date seen,
    # and last_valid_close for the latest date seen.
    # A symbol needs at least 2 distinct dates with valid close to have a return.
    symbol_data: dict[str, dict] = {}

    date_col = observation_date_column.strip()
    symbol_col = observation_symbol_column.strip()
    close_col = observation_close_column.strip()

    with open(dataset_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header row: {dataset_path}")
        header_set = {f.strip() for f in reader.fieldnames}
        if close_col not in header_set:
            raise ValueError(
                f"observation_close_column '{observation_close_column}' "
                f"not found in CSV header: {list(reader.fieldnames)}"
            )
        # Date/symbol columns are expected to have been validated by
        # _summarize_observation_table_canonical already; check them too.
        if date_col not in header_set:
            raise ValueError(
                f"observation_date_column '{observation_date_column}' "
                f"not found in CSV header: {list(reader.fieldnames)}"
            )
        if symbol_col not in header_set:
            raise ValueError(
                f"observation_symbol_column '{observation_symbol_column}' "
                f"not found in CSV header: {list(reader.fieldnames)}"
            )

        for row in reader:
            date_val = row.get(date_col, "").strip()
            symbol_val = row.get(symbol_col, "").strip()
            close_raw = row.get(close_col, "").strip()

            # Skip rows with missing essential fields
            if not date_val or not symbol_val:
                continue

            # Parse close: skip empty or non-numeric
            if not close_raw:
                continue
            try:
                close_val = float(close_raw)
            except ValueError:
                continue

            # Skip non-finite
            if not (0 < abs(close_val) < float("inf")):
                # float("inf") or nan — skip
                continue

            # Initialize symbol entry if first time seen
            if symbol_val not in symbol_data:
                symbol_data[symbol_val] = {
                    "first_date": date_val,
                    "first_close": close_val,
                    "last_date": date_val,
                    "last_close": close_val,
                }
            else:
                # Update first if earlier (lexicographic)
                if date_val < symbol_data[symbol_val]["first_date"]:
                    symbol_data[symbol_val]["first_date"] = date_val
                    symbol_data[symbol_val]["first_close"] = close_val
                # Update last if later (lexicographic)
                if date_val > symbol_data[symbol_val]["last_date"]:
                    symbol_data[symbol_val]["last_date"] = date_val
                    symbol_data[symbol_val]["last_close"] = close_val

    # Compute returns for symbols with ≥2 distinct dates and non-zero first close
    returns: list[float] = []
    for symbol, data in symbol_data.items():
        # Require at least 2 distinct dates
        if data["first_date"] == data["last_date"]:
            continue
        # Require non-zero first close
        if data["first_close"] == 0:
            continue
        simple_return = (data["last_close"] / data["first_close"]) - 1.0
        returns.append(simple_return)

    symbols_with_return = len(returns)
    skipped_symbols = len(symbol_data) - symbols_with_return

    if returns:
        min_return = min(returns)
        max_return = max(returns)
        mean_return = sum(returns) / len(returns)
    else:
        min_return = None
        max_return = None
        mean_return = None

    details_parts = [
        f"symbols_with_return={symbols_with_return}",
        f"close_column={close_col}",
        f"skipped_symbols={skipped_symbols}",
    ]
    if min_return is not None:
        details_parts.append(f"min_return={min_return!r}")
        details_parts.append(f"max_return={max_return!r}")
        details_parts.append(f"mean_return={mean_return!r}")

    return {
        "symbols_with_return": symbols_with_return,
        "min_return": min_return,
        "max_return": max_return,
        "mean_return": mean_return,
        "close_column": close_col,
        "skipped_symbols": skipped_symbols,
        "details": "; ".join(details_parts),
    }


def _read_csv_header(file_path: Path) -> list[str] | None:
    """
    Read the header row (first line) of a CSV file using csv.reader.

    Returns None if the file cannot be opened or parsed as CSV.
    """
    try:
        with open(file_path, "r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header is None:
                return []
            return header
    except Exception:
        return None


def _validate_observation_table_columns(
    dataset_path: Path,
    required_columns: list[str],
) -> tuple[list[str], bool]:
    """
    Check that all required columns are present in a CSV file's header row.

    Parameters
    ----------
    dataset_path : Path
        Path to the CSV observation table file.
    required_columns : list[str]
        Column names that must be present in the CSV header.

    Returns
    -------
    tuple[list[str], bool]
        (missing_columns, all_present).
        missing_columns is the list of required columns not found in the header.
        all_present is True when missing_columns is empty.
    """
    header = _read_csv_header(dataset_path)
    if header is None:
        # Could not read CSV; treat all required columns as missing
        return list(required_columns), False

    header_set = {col.strip() for col in header}
    missing = [col for col in required_columns if col.strip() not in header_set]
    return missing, len(missing) == 0


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

class GovernanceRejection(Exception):
    """
    Raised when governance validation fails (blocker_count > 0).

    Carries the artifact dict so main() can emit a failed_validation
    RunnerOutput artifact before exiting with a nonzero code.
    """

    def __init__(self, artifact: dict, message: str):
        self.artifact = artifact
        self.message = message
        super().__init__(message)


class UnsupportedConfig(Exception):
    """
    Raised when canonical summary is requested but the dataset format
    does not support it (e.g., non-CSV). Carries the failure_type
    so the caller can set failure_type='unsupported_config' in the
    failure_summary instead of 'validation_error'.
    """

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


# --------------------------------------------------------------------------
# DataManifest validation failure handler
# --------------------------------------------------------------------------

def _handle_data_manifest_validation_failure(
    experiment_spec: dict,
    experiment_spec_path: Path,
    created_at: str,
    required_observation_columns: list[str] | None,
    failure_type: str,
    exc: Exception,
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
        experiment_spec_path, None, required_observation_columns
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


def _build_failure_summary(
    audit_summary: dict,
    status: str,
    observation_missing_columns: list[str] | None = None,
) -> dict:
    """
    Build a failure_summary dict for failed_validation / failed_runtime statuses.

    Collects all failing audit names from audit_summary and formats them into
    a blocker_summary. When observation_missing_columns is provided, appends
    the column names to the blocker_summary string.
    """
    now = _utc_now()
    failing_audits = [
        a["audit_name"]
        for a in audit_summary["audits"]
        if a["audit_result"] == "fail"
    ]
    blocker_summary_parts = [f"Validation failed: {', '.join(failing_audits)}."]
    if observation_missing_columns:
        blocker_summary_parts.append(
            f"Missing observation-table columns: {', '.join(observation_missing_columns)}."
        )
    blocker_summary_parts.append(f"Total blockers: {audit_summary['blocker_count']}.")
    blocker_summary = " ".join(blocker_summary_parts)
    return {
        "failure_type": "validation_error",
        "status": status,
        "failed_check": ", ".join(failing_audits) or None,
        "blocker_summary": blocker_summary,
        "missing_data_summary_ref": None,
        "details_ref": None,
        "created_at": now,
    }


# ---------------------------------------------------------------------------
# Core artifact builder
# ---------------------------------------------------------------------------

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

    # Early validation: close-return summary requires date and symbol columns
    if has_close_return_summary_requested:
        date_ok = observation_date_column is not None and observation_date_column.strip()
        symbol_ok = observation_symbol_column is not None and observation_symbol_column.strip()
        if not date_ok or not symbol_ok:
            raise ValueError(
                "--observation-close-column was supplied but one or both of "
                "--observation-date-column and --observation-symbol-column are missing. "
                "Close-return summary requires date, symbol, and close columns."
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
                experiment_spec_path, data_manifest_path,
                required_observation_columns,
                observation_date_column, observation_symbol_column,
                observation_close_column,
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
            }
            raise GovernanceRejection(
                _failed_artifact,
                f"Run blocked: --observation-close-column requires CSV datasets "
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
                )
            )
            if governance_blocker_count > 0:
                raise GovernanceRejection(
                    partial_artifact,
                    f"DataManifest validation failed ({failure_type}): {exc}",
                )
            return partial_artifact

    # Deterministic hashes — include DataManifest content and required columns when present
    run_config_hash = _compute_run_config_hash(
        experiment_spec_path, data_manifest_path,
        required_observation_columns,
        observation_date_column,
        observation_symbol_column,
        observation_close_column,
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
                "data_manifest_refs": [],
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
                    "data_manifest_refs": [],
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
            }
            raise GovernanceRejection(
                failed_artifact,
                f"Run blocked by observation_table_close_return_summary failure: "
                f"{close_return_summary_details}.",
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
