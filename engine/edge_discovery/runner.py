"""WFA/CPCV runner wrapper.

This module provides run_wfa_cpcv which orchestrates CPCV splits and calls the
backtester for each split. For the purposes of fast unit tests we provide a
separable helper `_run_backtest_for_split` which can be patched/mocked.

Assumptions:
- The real backtester may expose a programmatic `run_backtest(...)` entrypoint.
  We attempt to import and call it; if not available we fall back to invoking a
  CLI via subprocess. Unit tests should mock `_run_backtest_for_split` to avoid
  expensive computation.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from typing import List, Dict, Any, Optional

DEFAULT_OUT_DIR = Path(".wfa/output").resolve()


def _ensure_out_dir(out_dir: Optional[str]) -> Path:
    if out_dir:
        p = Path(out_dir)
    else:
        p = DEFAULT_OUT_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _run_backtest_for_split(strategy: str, split_idx: int, n_splits: int, purge: float, cost_model: Optional[str]) -> Dict[str, Any]:
    """Run the backtester for a single CPCV split.

    This helper tries to call a programmatic API if available, otherwise it
    shells out to a CLI. It returns a dict of metrics. For unit tests this
    function should be patched to return a fast synthetic result.
    """
    start = time.time()

    # Attempt to import a programmatic entrypoint
    try:
        # module path from task description; may not exist in this repo; import
        # only if available.
        from engine.src.earnings_research.backtest.options_backtest_v1 import run_backtest

        # Example call signature -- adapt as needed by the real backtester.
        metrics = run_backtest(strategy=strategy, split_index=split_idx, n_splits=n_splits, purge=purge, cost_model=cost_model)
    except Exception:
        # Fall back to CLI invocation; expect the backtester can accept a
        # --json-output option or print JSON to stdout. This is a best-effort
        # fallback used in real runs; unit tests should mock the helper.
        cmd = [
            "python",
            "-m",
            "engine.src.earnings_research.backtest.options_backtest_v1",
            "--strategy",
            strategy,
            "--split-index",
            str(split_idx),
            "--n-splits",
            str(n_splits),
            "--purge",
            str(purge),
        ]
        if cost_model:
            cmd += ["--cost-model", cost_model]

        # Run and capture stdout
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            # In case of failure return a minimal metrics object with error info.
            metrics = {
                "returns": None,
                "pbo_estimate": None,
                "sharpe": None,
                "max_drawdown": None,
                "trades": 0,
                "error": proc.stderr.strip(),
            }
        else:
            try:
                metrics = json.loads(proc.stdout)
            except Exception:
                # If stdout is not JSON, include raw text
                metrics = {"raw_stdout": proc.stdout.strip()}

    elapsed = time.time() - start
    # Normalize: prefer total_return as canonical key; backtester may emit returns.
    raw = metrics or {}
    if "returns" in raw and "total_return" not in raw:
        raw["total_return"] = raw["returns"]
    metrics_out = {
        "strategy": strategy,
        "split_idx": split_idx,
        "n_splits": n_splits,
        "purge": purge,
        "cost_model": cost_model,
        "execution_time_seconds": elapsed,
        "total_return": raw.get("total_return"),
        "sharpe": raw.get("sharpe"),
        "max_drawdown": raw.get("max_drawdown"),
        "trades": raw.get("trades"),
        "pnl_series": raw.get("pnl_series"),  # optional
    }
    return metrics_out


def run_wfa_cpcv(strategies: List[str], n_splits: int = 2, purge: float = 0.01, cost_model: Optional[str] = None, out_dir: Optional[str] = None) -> Dict[str, Any]:
    """Run CPCV/WFA across strategies and collect per-split metrics.

    Parameters
    - strategies: list of strategy identifiers (strings) to evaluate
    - n_splits: number of CPCV splits (default 2)
    - purge: purge fraction for CPCV
    - cost_model: optional cost model identifier
    - out_dir: directory to write outputs; default is .wfa/output/

    Returns a summary dict and writes a JSON summary file to out_dir.
    """
    out_path = _ensure_out_dir(out_dir)
    # Use timezone-aware UTC timestamp to avoid DeprecationWarning
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    per_split_results = []

    for strategy in strategies:
        for split_idx in range(n_splits):
            metrics = _run_backtest_for_split(strategy=strategy, split_idx=split_idx, n_splits=n_splits, purge=purge, cost_model=cost_model)
            per_split_results.append(metrics)

    # Save raw per-split outputs
    raw_out_file = out_path / f"{timestamp}_wfa_splits.json"
    with raw_out_file.open("w") as f:
        json.dump(per_split_results, f, indent=2, default=str)

    # Aggregate using auditor helper
    try:
        from engine.edge_discovery.auditor import aggregate_wfa_metrics, estimate_pbo

        summary = aggregate_wfa_metrics(per_split_results)
        # Inject DSR-style PBO estimate
        returns = [r.get("total_return") for r in per_split_results if r.get("total_return") is not None]
        if returns:
            summary["pbo_estimate"] = estimate_pbo(returns)
    except Exception:
        # Fallback simple aggregator
        returns = [r.get("total_return") for r in per_split_results if r.get("total_return") is not None]
        summary = {
            "n_splits": n_splits,
            "n_results": len(per_split_results),
            "mean_return": None if not returns else sum(returns) / len(returns),
            "median_return": None,
            "pbo": None,
        }

    summary_out_file = out_path / f"{timestamp}_wfa_summary.json"
    with summary_out_file.open("w") as f:
        json.dump({"summary": summary, "raw_splits_file": str(raw_out_file)}, f, indent=2, default=str)

    return {"summary": summary, "raw_splits_file": str(raw_out_file), "summary_file": str(summary_out_file)}
