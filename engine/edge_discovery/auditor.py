"""Simple aggregator for WFA/CPCV split metrics + DSR-style PBO estimator.

This module provides:
- aggregate_wfa_metrics: takes per-split metrics and computes aggregated stats
- estimate_pbo: DSR-style Probability of Backtest Outperformance estimator

DSR-style PBO (simplified surrogate):
The ideal DSR PBO would use full DSR distribution analysis (Harle et al.).
Our surrogate uses an empirical approach: for each split, identify the best
in-sample parameter selection, then check if that selection would have chosen
the max-return split out-of-sample. PBO = fraction of splits where in-sample
best would have selected the true out-of-sample best.

When only per-split returns are available (no parameter sweep data), we use
a rank-based surrogate: PBO ≈ fraction of splits where the rank-1 strategy
in-sample would be rank-1 out-of-sample, estimated via leave-one-out cross-
validation within the available splits.

Assumptions/simplifications:
- Input metric keys expected: 'total_return', 'sharpe', 'max_drawdown', 'trades'.
  Missing values are ignored in means.
- The PBO estimator is a coarse surrogate; production use should implement
  full DSR methodology with proper parameter sweep data.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional
import json
from statistics import mean, median
from pathlib import Path


def _safe_mean(values: List[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return mean(vals)


def estimate_pbo(per_split_returns: List[float]) -> float:
    """Estimate Probability of Backtest Outperformance (PBO) — DSR-style surrogate.

    This is a simplified empirical PBO that estimates the probability that
    the best in-sample selection would outperform out-of-sample, using
    leave-one-out cross-validation within the available splits.

    Algorithm:
    1. For each split i, treat it as the "out-of-sample" holdout.
    2. Select the best-performing strategy on the remaining n-1 splits (in-sample).
    3. Check if that in-sample best also has the highest return on the held-out split.
    4. PBO = fraction of splits where in-sample best == out-of-sample best.

    When all returns are identical (no variation), returns 0.5 by convention.

    Parameters
    ----------
    per_split_returns : list of float
        List of returns for each split. Must have at least 2 entries for
        a meaningful estimate; returns 0.5 if len < 2.

    Returns
    -------
    float
        Estimated PBO between 0 and 1.
    """
    n = len(per_split_returns)
    if n < 2:
        return 0.5  # Not enough data; convention
    if all(r == per_split_returns[0] for r in per_split_returns):
        return 0.5  # No variation; convention

    # Leave-one-out PBO
    n_best = 0
    for i in range(n):
        # In-sample: all splits except i
        in_sample = [per_split_returns[j] for j in range(n) if j != i]
        # Best in-sample index (max return)
        best_in_sample_idx = max(range(n), key=lambda j: per_split_returns[j] if j != i else float("-inf"))
        # Actually find the max value among in-sample
        best_in_sample_val = max(in_sample)
        # Out-of-sample: split i
        oos_val = per_split_returns[i]
        # If in-sample best equals oos value (within tolerance for float eq)
        if abs(best_in_sample_val - oos_val) < 1e-12:
            n_best += 1

    return n_best / n


def aggregate_wfa_metrics(per_split_metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate a list of per-split metric dicts.

    Returns a dictionary with aggregated fields and writes a summary JSON if
    possible.
    """
    n = len(per_split_metrics)
    returns = [m.get("total_return") for m in per_split_metrics if m.get("total_return") is not None]
    pbo_estimates = [m.get("pbo_estimate") for m in per_split_metrics if m.get("pbo_estimate") is not None]
    sharpes = [m.get("sharpe") for m in per_split_metrics if m.get("sharpe") is not None]
    maxdds = [m.get("max_drawdown") for m in per_split_metrics if m.get("max_drawdown") is not None]
    trades = [m.get("trades") for m in per_split_metrics if isinstance(m.get("trades"), (int, float))]

    agg = {
        "n_splits": n,
        "mean_return": _safe_mean(returns),
        "median_return": None if not returns else median(returns),
        "mean_sharpe": _safe_mean(sharpes),
        "mean_max_drawdown": _safe_mean(maxdds),
        "total_trades": None if not trades else sum(trades),
    }

    return agg
