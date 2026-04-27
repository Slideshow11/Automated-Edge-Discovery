"""Tests for the pre-earnings options subprocess adapter."""
import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from engine.edge_discovery.adapters import preearn_options as preearn_module
from engine.edge_discovery.adapters.preearn_options import (
    CandidateSpec,
    PreearnResult,
    build_command,
    candidate_id,
    config_hash,
    get_git_commit,
    run_preearn_backtest,
    summarize_trades_csv,
)


# ---------------------------------------------------------------------------
# A. CandidateSpec validation and candidate_id
# ---------------------------------------------------------------------------

def test_candidate_spec_valid():
    spec = CandidateSpec(
        entry_dpe=2,
        delta_target=0.30,
        expiry_rank=0,
        options_db_path="/data/options_2025.sqlite",
        preearn_repo_path="/repo/engine_linux_main",
    )
    assert spec.entry_dpe == 2
    assert spec.delta_target == 0.30
    assert spec.fill_policy == "MID"


def test_candidate_spec_defaults():
    spec = CandidateSpec(
        entry_dpe=3,
        delta_target=0.50,
        expiry_rank=1,
        options_db_path="/db/opts.db",
        preearn_repo_path="/repo/engine",
    )
    assert spec.fill_policy == "MID"
    assert spec.spread_penalty_k == 0.5
    assert spec.contract_multiplier == 100.0
    assert spec.run_id_prefix == "preearn"
    assert spec.output_dir == ".wfa/preearn"


def test_candidate_spec_invalid_dpe_negative():
    with pytest.raises(ValueError, match="entry_dpe must be >= 0"):
        CandidateSpec(
            entry_dpe=-1,
            delta_target=0.30,
            expiry_rank=0,
            options_db_path="/db/opts.db",
            preearn_repo_path="/repo/engine",
        )


def test_candidate_spec_invalid_delta_too_high():
    with pytest.raises(ValueError, match="delta_target must be in"):
        CandidateSpec(
            entry_dpe=2,
            delta_target=1.0,
            expiry_rank=0,
            options_db_path="/db/opts.db",
            preearn_repo_path="/repo/engine",
        )


def test_candidate_spec_invalid_delta_zero():
    with pytest.raises(ValueError, match="delta_target must be in"):
        CandidateSpec(
            entry_dpe=2,
            delta_target=0.0,
            expiry_rank=0,
            options_db_path="/db/opts.db",
            preearn_repo_path="/repo/engine",
        )


def test_candidate_spec_invalid_expiry_rank():
    with pytest.raises(ValueError, match="expiry_rank must be >= 0"):
        CandidateSpec(
            entry_dpe=2,
            delta_target=0.30,
            expiry_rank=-1,
            options_db_path="/db/opts.db",
            preearn_repo_path="/repo/engine",
        )


def test_candidate_spec_invalid_fill_policy():
    with pytest.raises(ValueError, match="fill_policy must be one of"):
        CandidateSpec(
            entry_dpe=2,
            delta_target=0.30,
            expiry_rank=0,
            options_db_path="/db/opts.db",
            preearn_repo_path="/repo/engine",
            fill_policy="INVALID",
        )


def test_candidate_spec_empty_options_db():
    with pytest.raises(ValueError, match="options_db_path must be a non-empty"):
        CandidateSpec(
            entry_dpe=2,
            delta_target=0.30,
            expiry_rank=0,
            options_db_path="",
            preearn_repo_path="/repo/engine",
        )


def test_candidate_id_format():
    spec = CandidateSpec(
        entry_dpe=2,
        delta_target=0.30,
        expiry_rank=0,
        options_db_path="/db/opts.db",
        preearn_repo_path="/repo/engine",
    )
    assert candidate_id(spec) == "preearn_dpe2_delta30_rank0"


def test_candidate_id_delta_50():
    spec = CandidateSpec(
        entry_dpe=3,
        delta_target=0.50,
        expiry_rank=1,
        options_db_path="/db/opts.db",
        preearn_repo_path="/repo/engine",
    )
    assert candidate_id(spec) == "preearn_dpe3_delta50_rank1"


# ---------------------------------------------------------------------------
# B. build_command
# ---------------------------------------------------------------------------

def test_build_command_basic(tmp_path):
    spec = CandidateSpec(
        entry_dpe=2,
        delta_target=0.30,
        expiry_rank=0,
        options_db_path="/data/options.sqlite",
        preearn_repo_path="/repo/engine_linux_main",
        fill_policy="MID",
        spread_penalty_k=0.5,
        contract_multiplier=100.0,
    )
    out_csv = str(tmp_path / "trades.csv")
    cmd = build_command(spec, out_csv)

    assert cmd[0] == sys.executable
    # Script must be a single path token ending with scripts/run_options_backtest_v1.py
    script_token = cmd[1]
    assert script_token.endswith("scripts/run_options_backtest_v1.py"), script_token
    assert "scripts" not in cmd[2:], cmd  # no separate "scripts" token
    assert "--options-db" in cmd
    assert "/data/options.sqlite" in cmd
    assert "--entry-dpe" in cmd
    assert "2" in cmd
    assert "--delta-target" in cmd
    assert "0.3" in cmd
    assert "--expiry-rank" in cmd
    assert "0" in cmd
    assert "--out-csv" in cmd
    assert out_csv in cmd


def test_build_command_with_run_id(tmp_path):
    spec = CandidateSpec(
        entry_dpe=3,
        delta_target=0.50,
        expiry_rank=1,
        options_db_path="/db/opts.db",
        preearn_repo_path="/repo/engine",
        _run_id="my_run_123",
    )
    cmd = build_command(spec, "/tmp/out.csv")
    assert "my_run_123" in cmd


def test_build_command_preserves_fill_policy(tmp_path):
    spec = CandidateSpec(
        entry_dpe=2,
        delta_target=0.30,
        expiry_rank=0,
        options_db_path="/db/opts.db",
        preearn_repo_path="/repo/engine",
        fill_policy="CROSS",
    )
    cmd = build_command(spec, "/tmp/out.csv")
    assert "--fill-policy" in cmd
    assert "CROSS" in cmd


# ---------------------------------------------------------------------------
# C. summarize_trades_csv
# ---------------------------------------------------------------------------

def test_summarize_trades_csv_empty(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("")
    result = summarize_trades_csv(str(path))
    assert result["n_trades"] == 0
    assert result["n_columns"] == 0


def test_summarize_trades_csv_with_data(tmp_path):
    path = tmp_path / "trades.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["earnings_event_id", "symbol", "entry_date", "total_return"],
        )
        writer.writeheader()
        writer.writerow(
            {"earnings_event_id": "E1", "symbol": "AAPL", "entry_date": "2025-01-01", "total_return": "0.05"}
        )
        writer.writerow(
            {"earnings_event_id": "E1", "symbol": "AAPL", "entry_date": "2025-01-01", "total_return": "0.03"}
        )
        writer.writerow(
            {"earnings_event_id": "E2", "symbol": "MSFT", "entry_date": "2025-01-02", "total_return": "0.02"}
        )

    result = summarize_trades_csv(str(path))
    assert result["n_trades"] == 3
    assert result["n_columns"] == 4
    assert result["n_events"] == 2  # E1, E2
    assert result["n_symbols"] == 2  # AAPL, MSFT


def test_summarize_trades_csv_missing_file():
    result = summarize_trades_csv("/nonexistent/path/trades.csv")
    assert result["n_trades"] == 0
    assert result["n_columns"] == 0


# ---------------------------------------------------------------------------
# D. run_preearn_backtest success
# ---------------------------------------------------------------------------

def test_run_preearn_backtest_success(tmp_path, monkeypatch):
    ledger_file = tmp_path / "ledger.jsonl"

    # Create fake pre-earnings script structure
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (script_dir / "run_options_backtest_v1.py").write_text("# fake pre-earnings script\nimport sys\nsys.exit(0)\n")
    # options_db must exist (preflight validation)
    options_db = tmp_path / "options.sqlite"
    options_db.touch()

    def fake_run(cmd, cwd, capture_output, text, timeout):
        # Parse --out-csv from cmd to know what path the adapter expects.
        out_csv_idx = cmd.index("--out-csv")
        actual_out_csv = Path(cmd[out_csv_idx + 1])

        # Write fake trades CSV to the adapter's output path
        actual_out_csv.parent.mkdir(parents=True, exist_ok=True)
        with actual_out_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["earnings_event_id", "symbol", "total_return"],
            )
            writer.writeheader()
            writer.writerow({"earnings_event_id": "EVT1", "symbol": "AAPL", "total_return": "0.05"})
            writer.writerow({"earnings_event_id": "EVT2", "symbol": "MSFT", "total_return": "0.03"})

        class FakeResult:
            returncode = 0
            stdout = f"options backtest v1 complete trades=2 out={actual_out_csv}\n"
            stderr = ""
        return FakeResult()

    monkeypatch.setenv("EDGE_DISCOVERY_LEDGER_PATH", str(ledger_file))
    monkeypatch.setattr(subprocess, "run", fake_run)

    spec = CandidateSpec(
        entry_dpe=2,
        delta_target=0.30,
        expiry_rank=0,
        options_db_path=str(options_db),
        preearn_repo_path=str(tmp_path),  # temp dir as fake repo
        output_dir=str(tmp_path),
    )

    result = run_preearn_backtest(spec, timeout=60)

    assert result.status == "success"
    assert result.run_id.startswith("preearn_")
    assert result.candidate_id == "preearn_dpe2_delta30_rank0"
    assert result.error is None
    assert result.output_artifacts.get("trades_csv", "").endswith(".csv")
    assert result.metrics_summary.get("n_trades") == 2
    assert result.metrics_summary.get("n_events") == 2
    assert result.metrics_summary.get("n_symbols") == 2

    # Ledger entry written
    assert ledger_file.exists()
    lines = [ln.strip() for ln in ledger_file.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["status"] == "success"
    assert record["run_type"] == "preearn_options"
    assert record["run_id"] == result.run_id


# ---------------------------------------------------------------------------
# E. run_preearn_backtest error
# ---------------------------------------------------------------------------

def test_run_preearn_backtest_error_raises_and_ledger_written(tmp_path, monkeypatch):
    ledger_file = tmp_path / "ledger.jsonl"

    # Create fake pre-earnings script structure
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (script_dir / "run_options_backtest_v1.py").write_text("# fake\nimport sys\nsys.exit(1)\n")
    # options_db must exist (preflight validation)
    options_db = tmp_path / "options.sqlite"
    options_db.touch()

    def fake_run_failure(cmd, cwd, capture_output, text, timeout):
        class FakeResult:
            returncode = 1
            stdout = ""
            stderr = "Database connection failed"
        return FakeResult()

    monkeypatch.setenv("EDGE_DISCOVERY_LEDGER_PATH", str(ledger_file))
    monkeypatch.setattr(subprocess, "run", fake_run_failure)

    spec = CandidateSpec(
        entry_dpe=2,
        delta_target=0.30,
        expiry_rank=0,
        options_db_path=str(options_db),
        preearn_repo_path=str(tmp_path),
        output_dir=str(tmp_path),
    )

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        run_preearn_backtest(spec, timeout=60)

    assert exc_info.value.stderr == "Database connection failed"

    # Ledger entry written even on error
    assert ledger_file.exists()
    lines = [ln.strip() for ln in ledger_file.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["status"] == "error"
    assert record["run_type"] == "preearn_options"
    assert "Database connection failed" in record["error"]


def test_run_preearn_backtest_timeout_raises_and_ledger_written(tmp_path, monkeypatch):
    ledger_file = tmp_path / "ledger.jsonl"

    # Create fake pre-earnings script structure
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (script_dir / "run_options_backtest_v1.py").write_text("# fake\nimport sys\nsys.exit(0)\n")
    # options_db must exist (preflight validation)
    options_db = tmp_path / "options.sqlite"
    options_db.touch()

    def fake_run_timeout(cmd, cwd, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout or 600)

    monkeypatch.setenv("EDGE_DISCOVERY_LEDGER_PATH", str(ledger_file))
    monkeypatch.setattr(subprocess, "run", fake_run_timeout)

    spec = CandidateSpec(
        entry_dpe=2,
        delta_target=0.30,
        expiry_rank=0,
        options_db_path=str(options_db),
        preearn_repo_path=str(tmp_path),
        output_dir=str(tmp_path),
    )

    with pytest.raises(subprocess.TimeoutExpired):
        run_preearn_backtest(spec, timeout=60)

    assert ledger_file.exists()
    lines = [ln.strip() for ln in ledger_file.read_text().splitlines() if ln.strip()]
    record = json.loads(lines[0])
    assert record["status"] == "error"
    assert "timeout" in record["error"]


def test_run_preearn_backtest_missing_repo_raises(tmp_path, monkeypatch):
    ledger_file = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("EDGE_DISCOVERY_LEDGER_PATH", str(ledger_file))

    spec = CandidateSpec(
        entry_dpe=2,
        delta_target=0.30,
        expiry_rank=0,
        options_db_path="/data/options.sqlite",
        preearn_repo_path="/nonexistent/repo",
        output_dir=str(tmp_path),
    )

    with pytest.raises(FileNotFoundError, match="preearn_repo_path does not exist"):
        run_preearn_backtest(spec)


def test_run_preearn_backtest_missing_script_raises(tmp_path, monkeypatch):
    ledger_file = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("EDGE_DISCOVERY_LEDGER_PATH", str(ledger_file))

    # preearn_repo_path exists but has no backtest script
    spec = CandidateSpec(
        entry_dpe=2,
        delta_target=0.30,
        expiry_rank=0,
        options_db_path="/data/options.sqlite",
        preearn_repo_path=str(tmp_path),  # tmp_path exists but script does not
        output_dir=str(tmp_path),
    )

    with pytest.raises(FileNotFoundError, match="backtest script not found"):
        run_preearn_backtest(spec)


def test_run_preearn_backtest_missing_options_db_raises(tmp_path, monkeypatch):
    ledger_file = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("EDGE_DISCOVERY_LEDGER_PATH", str(ledger_file))

    # Script exists if we create it
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (script_dir / "run_options_backtest_v1.py").write_text("# fake")

    spec = CandidateSpec(
        entry_dpe=2,
        delta_target=0.30,
        expiry_rank=0,
        options_db_path="/nonexistent/options.sqlite",
        preearn_repo_path=str(tmp_path),
        output_dir=str(tmp_path),
    )

    with pytest.raises(FileNotFoundError, match="options_db_path does not exist"):
        run_preearn_backtest(spec)


# ---------------------------------------------------------------------------
# F. No earnings_research import
# ---------------------------------------------------------------------------

def test_no_earnings_research_import():
    """Verify the adapter module does not import earnings_research."""
    import inspect
    source = inspect.getsource(preearn_module)
    # Must not have any import statements referencing earnings_research
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") and "earnings_research" in stripped:
            pytest.fail(f"Found earnings_research import: {line!r}")
        if stripped.startswith("from ") and "earnings_research" in stripped:
            pytest.fail(f"Found earnings_research import: {line!r}")


# ---------------------------------------------------------------------------
# G. config_hash determinism
# ---------------------------------------------------------------------------

def test_config_hash_deterministic():
    spec1 = CandidateSpec(
        entry_dpe=2,
        delta_target=0.30,
        expiry_rank=0,
        options_db_path="/db/opts.db",
        preearn_repo_path="/repo/engine",
    )
    spec2 = CandidateSpec(
        entry_dpe=2,
        delta_target=0.30,
        expiry_rank=0,
        options_db_path="/db/opts.db",
        preearn_repo_path="/repo/engine",
    )
    assert config_hash(spec1) == config_hash(spec2)
    assert len(config_hash(spec1)) == 16


def test_config_hash_different_if_param_changes():
    spec1 = CandidateSpec(
        entry_dpe=2,
        delta_target=0.30,
        expiry_rank=0,
        options_db_path="/db/opts.db",
        preearn_repo_path="/repo/engine",
    )
    spec2 = CandidateSpec(
        entry_dpe=3,  # different
        delta_target=0.30,
        expiry_rank=0,
        options_db_path="/db/opts.db",
        preearn_repo_path="/repo/engine",
    )
    assert config_hash(spec1) != config_hash(spec2)
