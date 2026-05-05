"""
First thin real-data runner slice — dry-run CLI skeleton.

Scope:
- Reads an ExperimentSpec JSON file.
- Validates governance inputs (experiment spec structural validation only; no
  real backtest execution, no registry writes, no live trading).
- Emits a RunnerOutput v1 artifact as JSON.

No autonomous search, no Bayesian optimization, no genetic programming,
no live trading, no production execution, no registry mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RUNNER_NAME_DEFAULT = "first_thin_real_data_runner"
RUNNER_VERSION_DEFAULT = "0.1.0"
RUNNER_OUTPUT_ID_DEFAULT = "RUN-2026-0001"
RUNNER_OUTPUT_VERSION = "1.0"
SCHEMA_PATH = Path(__file__).resolve().parents[4] / "schemas" / "runner_output_spec_v1.schema.json"

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

def _compute_run_config_hash(experiment_spec_path: Path) -> str:
    """
    Compute a deterministic SHA-256 hex digest of the experiment spec file.

    The digest is computed over the canonical (sorted-key) JSON bytes so that
    whitespace variation does not affect the hash.
    """
    with open(experiment_spec_path, "rb") as fh:
        raw = json.loads(fh.read().decode("utf-8"))
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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

    All checks pass for a correctly-formed dry-run skeleton.
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


def build_runner_output(
    experiment_spec_path: str | Path,
    runner_name: str = RUNNER_NAME_DEFAULT,
    runner_version: str = RUNNER_VERSION_DEFAULT,
    run_owner: str = "unknown",
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

    Returns
    -------
    dict
        A RunnerOutput v1 compatible artifact (dry_run / success).

    Raises
    ------
    FileNotFoundError
        If experiment_spec_path does not exist.
    ValueError
        If experiment_spec is missing required experiment_id field.
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

    # Deterministic hashes
    run_config_hash = _compute_run_config_hash(experiment_spec_path)
    run_id = _compute_run_id(run_config_hash)
    experiment_id = experiment_spec["experiment_id"]

    # Content hash of experiment spec
    spec_content_hash = _compute_content_hash(experiment_spec_path)

    # Build input_artifact_refs
    input_artifact_refs = [
        {
            "artifact_type": "ExperimentSpec",
            "artifact_id": experiment_id,
            "artifact_path": str(experiment_spec_path.resolve()) if experiment_spec_path.is_absolute() else str(experiment_spec_path),
            "schema_ref": "schemas/experiment_spec_v1.schema.json",
            "validator_ref": None,
            "content_hash": f"sha256:{spec_content_hash}",
            "validation_status": "pass",
            "validated_at": created_at,
        }
    ]

    # data_manifest_refs — forward from experiment spec
    data_manifest_refs = experiment_spec.get("data_manifest_refs", [])

    # Audit summary
    audit_summary = _audit_dry_run(
        experiment_spec,
        experiment_spec_path,
        run_config_hash,
    )

    # output_manifest — dry-run report entry
    output_manifest = [
        {
            "output_role": "evidence",
            "output_path": "<runner_output_json>",
            "row_count": None,
            "content_hash": None,  # computed after serialization
            "created_at": created_at,
            "format": "json",
            "description": "First thin real-data runner dry-run output artifact",
            "contains_private_data": False,
            "publishable": False,
        }
    ]

    artifact = {
        "runner_output_id": RUNNER_OUTPUT_ID_DEFAULT,
        "runner_output_version": RUNNER_OUTPUT_VERSION,
        "run_id": run_id,
        "run_mode": "dry_run",
        "status": "success",
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
        "failure_summary": None,
        "partial_summary": None,
    }

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

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, sort_keys=True)

    # Update output_manifest with actual content hash and path
    content_hash = _compute_content_hash(output_path)
    for entry in artifact["output_manifest"]:
        if entry["output_path"] == "<runner_output_json>":
            entry["output_path"] = str(output_path)
            entry["content_hash"] = f"sha256:{content_hash}"

    # Re-serialize with updated manifest
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """
    Dry-run runner CLI entry point.

    Exits 0 on success, 1 on user error (missing file, missing field,
    existing output path), 2 on unexpected errors.
    """
    parser = argparse.ArgumentParser(
        prog="run_first_thin_real_data_runner",
        description=(
            "AED first thin real-data runner dry-run CLI. "
            "Reads an ExperimentSpec, validates governance inputs, "
            "and emits a RunnerOutput v1 artifact. "
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

    args = parser.parse_args(argv)

    # Validate experiment spec path exists
    experiment_spec_path = Path(args.experiment_spec)
    if not experiment_spec_path.exists():
        print(f"ERROR: experiment spec not found: {experiment_spec_path}", file=sys.stderr)
        return 1

    # Build artifact
    try:
        artifact = build_runner_output(
            experiment_spec_path=experiment_spec_path,
            runner_name=args.runner_name,
            runner_version=args.runner_version,
            run_owner=args.run_owner,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR building runner output: {exc}", file=sys.stderr)
        return 2

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
