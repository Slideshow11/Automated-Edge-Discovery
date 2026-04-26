import json
from pathlib import Path
from engine.edge_discovery import runner


def test_run_wfa_cpcv_uses_backtest_helper(tmp_path, monkeypatch):
    # Prepare a fake backtest result
    def fake_run(strategy, split_idx, n_splits, purge, cost_model):
        return {"strategy": strategy, "total_return": 0.1 + 0.01 * split_idx, "sharpe": 0.8, "trades": 5}

    monkeypatch.setattr(runner, "_run_backtest_for_split", fake_run)

    out = tmp_path / "wfa_out"
    res = runner.run_wfa_cpcv(["stratA"], n_splits=3, purge=0.01, cost_model=None, out_dir=str(out))

    assert "raw_splits_file" in res and "summary_file" in res
    raw_path = Path(res["raw_splits_file"])
    assert raw_path.exists()

    # Validate content length matches number of splits
    with raw_path.open() as f:
        data = json.load(f)
    assert isinstance(data, list) and len(data) == 3
