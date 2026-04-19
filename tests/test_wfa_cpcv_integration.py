"""Integration tests for WFA/CPCV runner, cost model, and PBO estimator.

These tests mock the heavy backtester via monkeypatching to keep runtime <5s.
Uses pytest fixtures and tmp_path as required.
"""
import json
from pathlib import Path

import pytest

from engine.edge_discovery import runner, auditor, costs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_split_factory():
    """Return a factory for deterministic synthetic split metrics."""
    def make_split(strategy: str, split_idx: int, n_splits: int, purge: float, cost_model: str | None):
        # Deterministic synthetic metrics: vary by split_idx so we can test ranking
        base_return = 0.01 if split_idx == 0 else -0.005
        return {
            "strategy": strategy,
            "split_idx": split_idx,
            "total_return": base_return,
            "sharpe": 1.0 + 0.1 * split_idx,
            "max_drawdown": 0.02 * (split_idx + 1),
            "trades": 10 + split_idx,
            "execution_time_seconds": 0.01,
            "pnl_series": [base_return * i for i in range(1, 4)],
        }
    return make_split


# ---------------------------------------------------------------------------
# Tests: PBO estimator
# ---------------------------------------------------------------------------

class TestEstimatePbo:
    def test_pbo_returns_float_between_0_and_1(self):
        returns = [0.01, -0.005, 0.02, 0.015]
        pbo = auditor.estimate_pbo(returns)
        assert isinstance(pbo, float)
        assert 0.0 <= pbo <= 1.0

    def test_pbo_with_identical_returns_returns_05(self):
        returns = [0.01, 0.01, 0.01]
        pbo = auditor.estimate_pbo(returns)
        assert pbo == 0.5

    def test_pbo_with_two_splits_best_varies(self):
        # When returns are [0.02, -0.01], the best (0.02) is never equal to the
        # held-out OOS value, so PBO = 0. This is expected for a single strategy
        # across splits: with no competing strategies, there is no "selection"
        # that matches between in-sample and OOS.
        returns = [0.02, -0.01]
        pbo = auditor.estimate_pbo(returns)
        assert pbo == 0.0

    def test_pbo_with_two_splits_best_never_holds_out(self):
        # Split 0 is best but also in-sample; split 1 is OOS and is not best → PBO = 0
        returns = [-0.01, 0.02]
        pbo = auditor.estimate_pbo(returns)
        assert pbo == 0.0

    def test_pbo_single_split_returns_05(self):
        returns = [0.01]
        pbo = auditor.estimate_pbo(returns)
        assert pbo == 0.5


# ---------------------------------------------------------------------------
# Tests: Cost model
# ---------------------------------------------------------------------------

class TestAlmgrenChrissCosts:
    @pytest.fixture(autouse=True)
    def require_pandas(self):
        pytest.importorskip("pandas", reason="pandas not available in test env")

    def test_apply_costs_produces_required_columns(self):
        import pandas as pd
        trades = pd.DataFrame({
            "trade_value": [1000.0, -500.0],
            "price": [100.0, 100.0],
        })
        result = costs.apply_costs(trades)
        assert "temp_impact" in result.columns
        assert "perm_impact" in result.columns
        assert "total_cost" in result.columns
        assert "net_proceeds" in result.columns

    def test_apply_costs_with_size_and_price(self):
        import pandas as pd
        trades = pd.DataFrame({
            "size": [10.0, -5.0],
            "price": [100.0, 100.0],
        })
        result = costs.apply_costs(trades)
        assert "temp_impact" in result.columns
        assert result.loc[0, "trade_value"] == 1000.0

    def test_apply_costs_negative_input_raises(self):
        import pandas as pd
        # Missing required columns should raise ValueError
        with pytest.raises(ValueError, match="trade_value"):
            trades = pd.DataFrame({"size": [10.0]})  # no price
            # call apply_costs which should raise when price is missing
            costs.apply_costs(trades)

    def test_costs_are_negative(self):
        import pandas as pd
        trades = pd.DataFrame({
            "trade_value": [1000.0],
            "price": [100.0],
        })
        result = costs.apply_costs(trades)
        assert result.loc[0, "total_cost"] < 0


# ---------------------------------------------------------------------------
# Tests: run_wfa_cpcv integration
# ---------------------------------------------------------------------------

class TestRunWfaCpcv:
    def test_wfa_cpcv_smoke(self, tmp_path, synthetic_split_factory, monkeypatch):
        out_dir = str(tmp_path)

        # Patch the internal helper to avoid calling a heavy backtester
        monkeypatch.setattr(runner, "_run_backtest_for_split", synthetic_split_factory)

        res = runner.run_wfa_cpcv(
            strategies=["strat-a", "strat-b"],
            n_splits=2,
            purge=0.01,
            cost_model=None,
            out_dir=out_dir,
        )

        # Validate returned structure
        assert "summary" in res
        assert "raw_splits_file" in res
        assert "summary_file" in res

        # Files exist
        raw = Path(res["raw_splits_file"])
        summary = Path(res["summary_file"])
        assert raw.exists(), f"Raw file not found: {raw}"
        assert summary.exists(), f"Summary file not found: {summary}"

        # Validate raw splits content
        with raw.open() as f:
            splits = json.load(f)
        assert isinstance(splits, list)
        assert len(splits) == 4  # 2 strategies × 2 splits

        for s in splits:
            assert "strategy" in s
            assert "split_idx" in s
            assert "total_return" in s
            assert "sharpe" in s
            assert "max_drawdown" in s
            assert "trades" in s
            assert "execution_time_seconds" in s

        # Validate summary content
        with summary.open() as f:
            summ = json.load(f)
        assert "summary" in summ
        summary_data = summ["summary"]
        assert summary_data["n_splits"] == 4

        # pbo_estimate should be injected by runner (via estimate_pbo)
        assert "pbo_estimate" in summary_data
        assert isinstance(summary_data["pbo_estimate"], float)
        assert 0.0 <= summary_data["pbo_estimate"] <= 1.0

    def test_wfa_cpcv_single_strategy(self, tmp_path, synthetic_split_factory, monkeypatch):
        monkeypatch.setattr(runner, "_run_backtest_for_split", synthetic_split_factory)

        res = runner.run_wfa_cpcv(
            strategies=["single-strat"],
            n_splits=2,
            purge=0.01,
            cost_model=None,
            out_dir=str(tmp_path),
        )

        assert "summary" in res
        with Path(res["raw_splits_file"]).open() as f:
            splits = json.load(f)
        assert len(splits) == 2

    def test_wfa_cpcv_summary_keys(self, tmp_path, synthetic_split_factory, monkeypatch):
        monkeypatch.setattr(runner, "_run_backtest_for_split", synthetic_split_factory)

        res = runner.run_wfa_cpcv(
            strategies=["test-strat"],
            n_splits=2,
            purge=0.01,
            cost_model=None,
            out_dir=str(tmp_path),
        )

        with Path(res["summary_file"]).open() as f:
            summ = json.load(f)
        summary_data = summ["summary"]

        expected_keys = {"n_splits", "mean_return", "median_return", "mean_sharpe", "mean_max_drawdown", "total_trades"}
        assert expected_keys.issubset(summary_data.keys()), f"Missing keys: {expected_keys - summary_data.keys()}"


# ---------------------------------------------------------------------------
# Tests: CLI wrapper (basic import/metadata check)
# ---------------------------------------------------------------------------

class TestCliWrapper:
    def test_cli_script_exists_and_executable(self):
        bin_path = Path(__file__).resolve().parents[1] / "bin" / "run_wfa"
        assert bin_path.exists(), f"CLI script not found: {bin_path}"
        assert bin_path.stat().st_mode & 0o111, "CLI script is not executable"

    def test_cli_parse_arguments(self):
        # Verify CLI argument parsing works by importing and calling parse_args
        import sys
        from pathlib import Path

        # Save and modify sys.argv temporarily
        old_argv = sys.argv
        try:
            sys.argv = ["run_wfa", "--strategies=strat1,strat2", "--n-splits=3", "--purge=0.02"]

            # Import the module directly
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "run_wfa_module",
                Path(__file__).resolve().parents[1] / "bin" / "run_wfa"
            )
            # spec may be None for extensionless files; use source loader
            if spec is None:
                # Fall back to exec for extensionless scripts
                cli_path = Path(__file__).resolve().parents[1] / "bin" / "run_wfa"
                code = cli_path.read_text()
                ctx = {"__name__": "run_wfa"}
                exec(compile(code, "run_wfa", "exec"), ctx)
                args_obj = ctx["parse_args"]()
            else:
                module = importlib.util.module_from_spec(spec)
                # Don't run main(), just verify parse_args works
                args_obj = module.parse_args()
                assert args_obj.strategies == "strat1,strat2"
                assert args_obj.n_splits == 3
                assert args_obj.purge == 0.02
        finally:
            sys.argv = old_argv
