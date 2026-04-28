"""Shared pure-helper utilities for pre-earnings smoke scripts."""
from __future__ import annotations

import sys
from pathlib import Path


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
