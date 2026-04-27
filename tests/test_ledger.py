"""Tests for the experiment ledger and its integration with run_wfa_cpcv."""
import json
from pathlib import Path

import pytest

from engine.edge_discovery import ledger as ledger_module
from engine.edge_discovery import config as ed_config
from engine.edge_discovery import runner


# ---------------------------------------------------------------------------
# A. Ledger write/read round-trip
# ---------------------------------------------------------------------------

def test_ledger_write_read_roundtrip(tmp_path):
    entry = ledger_module.LedgerEntry(
        run_id="test-123",
        run_type="wfa_cpcv",
        started_at="2026-04-26T00:00:00+00:00",
        completed_at="2026-04-26T00:01:00+00:00",
        status="success",
        config_hash="abcd12345678efgh",
        git_commit="abc1234",
        error=None,
        input_artifacts={"orders": "/data/orders.csv"},
        output_artifacts={"splits": "/out/splits.json"},
        metrics_summary={"pbo_estimate": 0.04, "mean_return": 0.12},
    )
    path = tmp_path / "ledger.jsonl"
    ledger_module.Ledger(path=str(path)).write(entry)
    entries = ledger_module.Ledger(path=str(path)).read()
    assert len(entries) == 1
    e = entries[0]
    assert e.run_id == "test-123"
    assert e.run_type == "wfa_cpcv"
    assert e.status == "success"
    assert e.config_hash == "abcd12345678efgh"
    assert e.git_commit == "abc1234"
    assert e.error is None
    assert e.input_artifacts == {"orders": "/data/orders.csv"}
    assert e.output_artifacts == {"splits": "/out/splits.json"}
    assert e.metrics_summary["pbo_estimate"] == 0.04


def test_ledger_read_skips_blank_lines(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"run_id":"a"}\n\n{"run_id":"b"}\n\n')
    entries = ledger_module.Ledger(path=str(path)).read()
    assert len(entries) == 2
    assert entries[0].run_id == "a"
    assert entries[1].run_id == "b"


def test_ledger_read_missing_file_returns_empty():
    entries = ledger_module.Ledger(path="/nonexistent/path/ledger.jsonl").read()
    assert entries == []


def test_config_hash_deterministic():
    cfg1 = {"strategies": ["A"], "n_splits": 3, "purge": 0.01, "cost_model": None, "out_dir": ".wfa/output"}
    cfg2 = {"strategies": ["A"], "n_splits": 3, "purge": 0.01, "cost_model": None, "out_dir": ".wfa/output"}
    h1 = ledger_module.config_hash(cfg1)
    h2 = ledger_module.config_hash(cfg2)
    assert h1 == h2
    assert len(h1) == 16


def test_config_hash_different_inputs_different_hash():
    cfg_a = {"strategies": ["A"], "n_splits": 3, "purge": 0.01, "cost_model": None, "out_dir": ".wfa/output"}
    cfg_b = {"strategies": ["B"], "n_splits": 3, "purge": 0.01, "cost_model": None, "out_dir": ".wfa/output"}
    assert ledger_module.config_hash(cfg_a) != ledger_module.config_hash(cfg_b)


# ---------------------------------------------------------------------------
# B. run_wfa_cpcv writes a success ledger entry
# ---------------------------------------------------------------------------

def test_run_wfa_cpcv_writes_success_ledger_entry(tmp_path, monkeypatch):
    ledger_file = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("EDGE_DISCOVERY_LEDGER_PATH", str(ledger_file))

    def fake_split(strategy, split_idx, n_splits, purge, cost_model):
        return {
            "strategy": strategy,
            "split_idx": split_idx,
            "total_return": 0.10 + 0.01 * split_idx,
            "sharpe": 0.8,
            "max_drawdown": 0.05,
            "trades": 5,
        }

    monkeypatch.setattr(runner, "_run_backtest_for_split", fake_split)

    out_dir = tmp_path / "wfa_out"
    res = runner.run_wfa_cpcv(
        ["stratA"], n_splits=2, purge=0.01, cost_model=None, out_dir=str(out_dir)
    )

    # Ledger file must exist
    assert ledger_file.exists(), "ledger file not created"

    lines = [ln.strip() for ln in ledger_file.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly 1 ledger line, got {len(lines)}"

    record = json.loads(lines[0])
    assert record["run_type"] == "wfa_cpcv"
    assert record["status"] == "success"
    assert record["error"] is None
    assert record["run_id"] is not None and record["run_id"] != ""
    assert record["started_at"] != ""
    assert record["completed_at"] != ""
    assert "pbo_estimate" in record["metrics_summary"] or "mean_return" in record["metrics_summary"]
    assert "raw_splits" in record["output_artifacts"]
    assert "summary" in record["output_artifacts"]

    # Return value must be unchanged
    assert "summary" in res
    assert "raw_splits_file" in res
    assert "summary_file" in res


# ---------------------------------------------------------------------------
# C. run_wfa_cpcv writes an error ledger entry then re-raises
# ---------------------------------------------------------------------------

def test_run_wfa_cpcv_writes_error_ledger_entry_then_raises(tmp_path, monkeypatch):
    ledger_file = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("EDGE_DISCOVERY_LEDGER_PATH", str(ledger_file))

    def failing_split(strategy, split_idx, n_splits, purge, cost_model):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "_run_backtest_for_split", failing_split)

    out_dir = tmp_path / "wfa_out"
    with pytest.raises(RuntimeError, match="boom"):
        runner.run_wfa_cpcv(
            ["stratA"], n_splits=2, purge=0.01, cost_model=None, out_dir=str(out_dir)
        )

    # Ledger entry must be written even on error
    assert ledger_file.exists(), "ledger file not created on error"
    lines = [ln.strip() for ln in ledger_file.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly 1 ledger line on error, got {len(lines)}"

    record = json.loads(lines[0])
    assert record["run_type"] == "wfa_cpcv"
    assert record["status"] == "error"
    assert "boom" in record["error"]
    assert record["run_id"] is not None
    assert record["started_at"] != ""
    assert record["completed_at"] != ""


# --------------------------------------------------------------------------/
# D. EDGE_DISCOVERY_LEDGER_PATH env var is honored
# --------------------------------------------------------------------------/

def test_ledger_path_env_var_is_honored(tmp_path, monkeypatch):
    """Setting EDGE_DISCOVERY_LEDGER_PATH must direct the ledger there."""
    ledger_file = tmp_path / "custom" / "ledger.jsonl"
    monkeypatch.setenv("EDGE_DISCOVERY_LEDGER_PATH", str(ledger_file))

    def fake_split(strategy, split_idx, n_splits, purge, cost_model):
        return {
            "strategy": strategy,
            "split_idx": split_idx,
            "total_return": 0.05,
            "sharpe": 0.6,
            "max_drawdown": 0.03,
            "trades": 3,
        }

    monkeypatch.setattr(runner, "_run_backtest_for_split", fake_split)

    out_dir = tmp_path / "wfa_out"
    res = runner.run_wfa_cpcv(
        ["stratX"], n_splits=1, purge=0.01, cost_model=None, out_dir=str(out_dir)
    )

    # Ledger must be at the env-var path, not the default
    assert ledger_file.exists(), f"ledger not at env-var path {ledger_file}"

    # Default path must not be created in the test root
    default_path = Path(".wfa/ledger.jsonl")
    assert not default_path.exists(), "default ledger path was created despite env var"

    lines = [ln.strip() for ln in ledger_file.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["status"] == "success"
    assert record["run_type"] == "wfa_cpcv"

    # Return value unchanged
    assert "summary" in res
    assert "raw_splits_file" in res
    assert "summary_file" in res
