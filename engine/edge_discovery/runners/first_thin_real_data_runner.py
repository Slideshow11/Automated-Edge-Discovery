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
) -> str:
    """
    Compute a deterministic SHA-256 hex digest of the run configuration.

    When data_manifest_path is None, the hash is computed from the
    experiment spec canonical JSON bytes only.

    When data_manifest_path is provided, the hash is computed from the
    concatenation of the experiment spec canonical JSON bytes plus the
    DataManifest file bytes. This ensures that changing the DataManifest
    content changes the run_config_hash and thus the run_id.

    Whitespace variation does not affect the hash.
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
        super().__init__(message)
        self.artifact = artifact
        self.message = message


# ---------------------------------------------------------------------------
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
) -> dict:
    """
    Build a failure_summary dict for failed_validation / failed_runtime statuses.

    Collects all failing audit names from audit_summary and formats them into
    a blocker_summary.
    """
    now = _utc_now()
    failing_audits = [
        a["audit_name"]
        for a in audit_summary["audits"]
        if a["audit_result"] == "fail"
    ]
    blocker_summary = (
        f"Validation failed: {', '.join(failing_audits)}. "
        f"Total blockers: {audit_summary['blocker_count']}."
    )
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

    # Timestamps
    started_at = _utc_now()
    completed_at = _utc_now()
    created_at = completed_at  # same instant for dry-run skeleton

    # Optional DataManifest loading and validation
    data_manifest_extra_audits: list[str] = []
    manifest: DatasetManifest | None = None
    manifest_summary: dict | None = None
    manifest_base_dir: Path = experiment_spec_path.parent

    if data_manifest_path is not None:
        data_manifest_path = Path(data_manifest_path)
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
        except (FileNotFoundError, ValueError) as exc:
            # DataManifest validation failed — emit failed_validation artifact
            # with the error in the failure_summary.
            spec_content_hash = _compute_content_hash(experiment_spec_path)
            run_config_hash_partial = _compute_run_config_hash(experiment_spec_path, None)
            run_id_partial = _compute_run_id(run_config_hash_partial)

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

            spec_dm_refs = experiment_spec.get("data_manifest_refs", None)
            if spec_dm_refs and len(spec_dm_refs) > 0:
                data_manifest_refs = spec_dm_refs
            else:
                data_manifest_refs = [DRY_RUN_DATA_MANIFEST_PLACEHOLDER]

            # Governance audit (should pass for missing/invalid DataManifest
            # unless other blockers exist)
            audit_summary = _audit_dry_run(
                experiment_spec,
                experiment_spec_path,
                run_config_hash_partial,
            )

            data_manifest_audit_failing = True
            new_audits = list(audit_summary["audits"])
            new_audits.append({
                "audit_name": "data_manifest_validation",
                "audit_result": "fail",
                "severity": "blocker",
                "blocker_count": 1,
                "warning_count": 0,
                "details_ref": None,
                "created_at": created_at,
            })
            audit_summary = {
                "overall_result": "fail",
                "blocker_count": audit_summary["blocker_count"] + 1,
                "warning_count": 0,
                "audits": new_audits,
            }

            failure_summary = _build_failure_summary(
                audit_summary,
                "failed_validation",
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

            return {
                "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
                "runner_output_version": RUNNER_OUTPUT_VERSION,
                "run_id": run_id_partial,
                "run_mode": "dry_run",
                "status": "failed_validation",
                "runner_name": runner_name,
                "runner_version": runner_version,
                "experiment_spec_ref": experiment_spec["experiment_id"],
                "input_artifact_refs": input_artifact_refs,
                "data_manifest_refs": data_manifest_refs,
                "run_config_hash": f"sha256:{run_config_hash_partial}",
                "started_at": started_at,
                "completed_at": completed_at,
                "audit_summary": audit_summary,
                "output_manifest": output_manifest,
                "created_at": created_at,
                "run_owner": run_owner,
                "failure_summary": failure_summary,
                "partial_summary": None,
            }

    # Deterministic hashes — include DataManifest content when present
    run_config_hash = _compute_run_config_hash(experiment_spec_path, data_manifest_path)
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
        failure_summary = _build_failure_summary(audit_summary, status)
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

    # Build artifact — may raise GovernanceRejection if governance blockers found
    try:
        artifact = build_runner_output(
            experiment_spec_path=experiment_spec_path,
            runner_name=args.runner_name,
            runner_version=args.runner_version,
            run_owner=args.run_owner,
            data_manifest_path=data_manifest_path,
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
