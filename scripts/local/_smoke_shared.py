"""Shared pure-helper utilities for pre-earnings smoke scripts."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from engine.edge_discovery.data_manifest import (
    DatasetRole,
    load_dataset_manifest,
    validate_dataset_manifest,
)


def ensure_output_dir(output_dir: str) -> Path:
    """Create output directory, returning the Path object."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def warn_real_run(capability: str) -> None:
    """Print WARNING to stderr when --real-run is being used."""
    print(
        f"WARNING: --real-run specified. This will {capability}",
        file=sys.stderr,
    )


def print_batch_summary(
    batch_id: str,
    status: str,
    n_candidates_generated: int,
    n_candidates_selected: int,
    n_success: int,
    n_error: int,
    output_artifacts: dict,
    ledger_path: str | None,
    indent: str = "",
) -> None:
    """Print the shared batch-result summary lines."""
    print(f"{indent}batch_id: {batch_id}")
    print(f"{indent}status: {status}")
    print(f"{indent}n_candidates_generated: {n_candidates_generated}")
    print(f"{indent}n_candidates_selected: {n_candidates_selected}")
    print(f"{indent}n_success: {n_success}")
    print(f"{indent}n_error: {n_error}")
    if output_artifacts:
        print(f"{indent}output_artifacts:")
        for k, v in output_artifacts.items():
            print(f"{indent}  {k}: {v}")
    if ledger_path:
        print(f"{indent}ledger_path: {ledger_path}")


def resolve_path_from_manifest(
    manifest_path: str,
    expected_role: DatasetRole | str,
) -> str:
    """Load a DatasetManifest, validate it, and return its local path.

    Parameters
    ----------
    manifest_path : str
        Path to the JSON manifest file.
    expected_role : DatasetRole | str
        Role the manifest must declare (e.g. options_backtest_db or preearn_repo).
        String values are converted to DatasetRole enum members.

    Returns
    -------
    str
        The declared local path from the manifest.

    Raises
    ------
    SystemExit
        If the manifest file cannot be loaded, cannot be parsed, or declares a
        role that does not match the expected role.
    """
    if isinstance(expected_role, str):
        expected_role = DatasetRole(expected_role)

    try:
        manifest = load_dataset_manifest(manifest_path)
    except FileNotFoundError:
        print(f"ERROR: manifest file not found: {manifest_path}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as e:
        print(f"ERROR: failed to parse manifest {manifest_path}: {e}", file=sys.stderr)
        raise SystemExit(1)

    if manifest.role != expected_role:
        print(
            f"ERROR: manifest {manifest_path} declares role {manifest.role.value!r} "
            f"but {expected_role.value!r} is required",
            file=sys.stderr,
        )
        raise SystemExit(1)

    validation = validate_dataset_manifest(manifest)
    if not validation.ok:
        for err in validation.errors:
            print(f"ERROR: manifest validation failed: {err}", file=sys.stderr)
        raise SystemExit(1)

    return manifest.path
