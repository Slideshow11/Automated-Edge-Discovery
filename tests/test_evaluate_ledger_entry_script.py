"""Tests for scripts/local/evaluate_ledger_entry.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.edge_discovery.evaluation import EvaluationLabel
from engine.edge_discovery.ledger import LedgerEntry
from scripts.local.evaluate_ledger_entry import main, parse_args


def _entry(
    run_id: str,
    run_type: str = "preearn_candidate_batch",
    status: str = "success",
    hypothesis_id: str = "hyp_001",
    n_sel: int = 5,
    n_suc: int = 4,
    n_err: int = 1,
) -> LedgerEntry:
    return LedgerEntry(
        run_id=run_id,
        run_type=run_type,
        started_at="2025-01-01T00:00:00Z",
        completed_at="2025-01-01T00:01:00Z",
        status=status,
        config_hash="abcd1234",
        git_commit="abc1234",
        error=None,
        input_artifacts={"hypothesis_id": hypothesis_id},
        output_artifacts={},
        metrics_summary={
            "n_candidates_generated": n_sel,
            "n_candidates_selected": n_sel,
            "n_success": n_suc,
            "n_error": n_err,
        },
    )


def _write_ledger(path: Path, entries: list[LedgerEntry]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for e in entries:
            from dataclasses import asdict

            fh.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


class TestArgumentParsing:
    def test_requires_ledger_path_and_run_id(self):
        with pytest.raises(SystemExit):
            parse_args([])

    def test_ledger_path_required(self):
        with pytest.raises(SystemExit):
            parse_args(["--run-id", "abc"])

    def test_run_id_required(self):
        with pytest.raises(SystemExit):
            parse_args(["--ledger-path", "/fake/ledger.jsonl"])

    def test_both_args_accepted(self):
        args = parse_args([
            "--ledger-path", "/fake/ledger.jsonl",
            "--run-id", "run_001",
        ])
        assert args.ledger_path == "/fake/ledger.jsonl"
        assert args.run_id == "run_001"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestEvaluation:
    def test_evaluates_promising_entry(self, tmp_path: Path):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001", status="success", n_sel=5, n_suc=4, n_err=1)])

        ret = main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        assert ret == 0

    def test_output_contains_run_id(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001")])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert out["run_id"] == "run_001"

    def test_output_contains_label(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001", status="success", n_sel=5, n_suc=4, n_err=1)])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert out["label"] == EvaluationLabel.PROMISING.value

    def test_output_contains_reason(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001", status="success", n_sel=5, n_suc=4, n_err=1)])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert "reason" in out
        assert len(out["reason"]) > 0

    def test_output_contains_hypothesis_id(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001", hypothesis_id="hyp_specific")])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert out["hypothesis_id"] == "hyp_specific"

    def test_output_contains_source_type(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001")])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert out["source_type"] == "ledger_entry"

    def test_output_contains_warnings(self, tmp_path: Path, capsys):
        # High error rate -> warnings
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001", status="partial", n_sel=4, n_suc=1, n_err=3)])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert "warnings" in out
        assert isinstance(out["warnings"], list)

    def test_exec_failed_label(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001", status="error", n_sel=3, n_suc=0, n_err=3)])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert out["label"] == EvaluationLabel.EXECUTION_FAILED.value

    def test_needs_more_data_label(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001", status="partial", n_sel=4, n_suc=1, n_err=3)])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert out["label"] == EvaluationLabel.NEEDS_MORE_DATA.value


# ---------------------------------------------------------------------------
# Error: not found
# ---------------------------------------------------------------------------


class TestNotFound:
    def test_missing_run_id_exits_1(self, tmp_path: Path):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001")])

        ret = main(["--ledger-path", str(ledger_path), "--run-id", "nonexistent_run"])
        assert ret == 1

    def test_missing_run_id_error_message(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001")])

        main(["--ledger-path", str(ledger_path), "--run-id", "nonexistent_run"])
        err = capsys.readouterr().err
        assert "nonexistent_run" in err
        assert "no ledger entry found" in err


# ---------------------------------------------------------------------------
# Error: duplicate run_id
# ---------------------------------------------------------------------------


class TestDuplicate:
    def test_duplicate_run_id_exits_1(self, tmp_path: Path):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001"), _entry("run_001")])

        ret = main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        assert ret == 1

    def test_duplicate_run_id_error_message(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001"), _entry("run_001")])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        err = capsys.readouterr().err
        assert "multiple entries found" in err


# ---------------------------------------------------------------------------
# Error: empty ledger
# ---------------------------------------------------------------------------


class TestEmptyLedger:
    def test_empty_ledger_exits_1(self, tmp_path: Path):
        ledger_path = tmp_path / "ledger.jsonl"
        ledger_path.touch()

        ret = main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        assert ret == 1


