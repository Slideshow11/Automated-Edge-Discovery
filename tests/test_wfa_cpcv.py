import json
import os
from pathlib import Path
import tempfile
import time

import pytest

# Import the runner we created
from engine.edge_discovery import runner


def fake_split_return(strategy, split_idx, n_splits, purge, cost_model):
    # produce deterministic synthetic metrics
    return {
        "strategy": strategy,
        "split_idx": split_idx,
        "returns": 0.01 * (1 if split_idx == 0 else -0.005),
        "pbo_estimate": 0.5,
        "sharpe": 1.0 + 0.1 * split_idx,
        "max_drawdown": 0.02 * (split_idx + 1),
        "trades": 10 + split_idx,
        "execution_time": 0.01,
    }


def test_run_wfa_cpcv_smoke(tmp_path, monkeypatch):
    out_dir = str(tmp_path)

    # Patch the internal helper to avoid calling a heavy backtester
    monkeypatch.setattr(runner, "_run_backtest_for_split", fake_split_return)

    # Run with 2 splits and 1 strategy
    res = runner.run_wfa_cpcv(strategies=["test-strat"], n_splits=2, purge=0.01, cost_model=None, out_dir=out_dir)

    # Validate returned structure
    assert "summary" in res
    assert "raw_splits_file" in res
    assert "summary_file" in res

    # Files exist
    raw = Path(res["raw_splits_file"])
    summary = Path(res["summary_file"])
    assert raw.exists()
    assert summary.exists()

    # Validate content schema
    with raw.open() as f:
        splits = json.load(f)
    assert isinstance(splits, list)
    assert len(splits) == 2
    for i, s in enumerate(splits):
        assert s["strategy"] == "test-strat"
        assert s["split_idx"] == i
        assert "returns" in s
        assert "sharpe" in s

    with summary.open() as f:
        summ = json.load(f)
    assert "summary" in summ
    assert summ["summary"]["n_splits"] == 2
