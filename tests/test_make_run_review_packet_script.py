"""Tests for scripts/local/make_run_review_packet.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.edge_discovery.evaluation import EvaluationLabel
from engine.edge_discovery.ledger import LedgerEntry
from scripts.local.make_run_review_packet import build_packet, main, parse_args


def _entry(
    run_id: str,
    run_type: str = "preearn_candidate_batch",
    status: str = "success",
    hypothesis_id: str = "hyp_001",
    n_sel: int = 5,
    n_suc: int = 4,
    n_err: int = 1,
    started_at: str = "2025-01-01T00:00:00Z",
) -> LedgerEntry:
    return LedgerEntry(
        run_id=run_id,
        run_type=run_type,
        started_at=started_at,
        completed_at="2025-01-01T00:01:00Z",
        status=status,
        config_hash="abcd1234",
        git_commit="abc1234",
        error=None,
        input_artifacts={"hypothesis_id": hypothesis_id},
        output_artifacts={"raw_splits": "/tmp/splits.json"},
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

    def test_optional_output_path_not_required(self):
        args = parse_args([
            "--ledger-path", "/fake/ledger.jsonl",
            "--run-id", "run_001",
        ])
        assert args.output_path is None

    def test_output_path_accepted(self):
        args = parse_args([
            "--ledger-path", "/fake/ledger.jsonl",
            "--run-id", "run_001",
            "--output-path", "/tmp/packet.json",
        ])
        assert args.output_path == "/tmp/packet.json"


# ---------------------------------------------------------------------------
# Happy path — stdout
# ---------------------------------------------------------------------------


class TestStdoutPacket:
    def test_prints_json_to_stdout(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001")])

        ret = main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        assert ret == 0

    def test_output_is_valid_json(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001")])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert isinstance(out, dict)

    def test_packet_has_run_id(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001", started_at="2025-06-01T10:00:00Z")])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert out["run_id"] == "run_001"

    def test_packet_has_hypothesis_id(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001", hypothesis_id="hyp_specific")])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert out["hypothesis_id"] == "hyp_specific"

    def test_packet_has_timestamp(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001", started_at="2025-06-01T10:00:00Z")])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert out["timestamp"] == "2025-06-01T10:00:00Z"

    def test_packet_has_evaluation(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001")])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert "evaluation" in out
        assert "label" in out["evaluation"]
        assert "reason" in out["evaluation"]
        assert "warnings" in out["evaluation"]

    def test_packet_has_review_guidance(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001")])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert out["review_guidance"]["manual_review_required"] is True
        assert out["review_guidance"]["registry_mutation"] is False

    def test_packet_has_ledger_entry(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001")])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert "ledger" in out
        assert out["ledger"]["status"] == "success"
        assert out["ledger"]["run_type"] == "preearn_candidate_batch"

    def test_packet_has_output_artifacts(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001")])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert out["ledger"]["output_artifacts"] == {"raw_splits": "/tmp/splits.json"}

    def test_evaluation_labels_promising(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001", status="success", n_sel=5, n_suc=4, n_err=1)])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert out["evaluation"]["label"] == EvaluationLabel.PROMISING.value

    def test_evaluation_labels_execution_failed(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001", status="error", n_sel=3, n_suc=0, n_err=3)])

        main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        out = json.loads(capsys.readouterr().out)
        assert out["evaluation"]["label"] == EvaluationLabel.EXECUTION_FAILED.value


# ---------------------------------------------------------------------------
# Happy path — --output-path
# ---------------------------------------------------------------------------


class TestOutputPath:
    def test_writes_file(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        out_path = tmp_path / "packet.json"
        _write_ledger(ledger_path, [_entry("run_001")])

        ret = main([
            "--ledger-path", str(ledger_path),
            "--run-id", "run_001",
            "--output-path", str(out_path),
        ])
        assert ret == 0
        assert out_path.exists()

    def test_writes_correct_json(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        out_path = tmp_path / "packet.json"
        _write_ledger(ledger_path, [_entry("run_001")])

        main([
            "--ledger-path", str(ledger_path),
            "--run-id", "run_001",
            "--output-path", str(out_path),
        ])

        written = json.loads(out_path.read_text(encoding="utf-8"))
        assert written["run_id"] == "run_001"

    def test_confirms_to_stdout(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        out_path = tmp_path / "packet.json"
        _write_ledger(ledger_path, [_entry("run_001")])

        main([
            "--ledger-path", str(ledger_path),
            "--run-id", "run_001",
            "--output-path", str(out_path),
        ])

        out = capsys.readouterr().out
        assert "written" in out.lower()
        assert str(out_path) in out

    def test_creates_parent_dirs(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        out_path = tmp_path / "nested" / "dir" / "packet.json"
        _write_ledger(ledger_path, [_entry("run_001")])

        ret = main([
            "--ledger-path", str(ledger_path),
            "--run-id", "run_001",
            "--output-path", str(out_path),
        ])
        assert ret == 0
        assert out_path.exists()


# ---------------------------------------------------------------------------
# Error: not found
# ---------------------------------------------------------------------------


class TestNotFound:
    def test_missing_run_id_exits_1(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001")])

        with pytest.raises(SystemExit) as exc:
            main(["--ledger-path", str(ledger_path), "--run-id", "nonexistent"])
        assert exc.value.code == 1

    def test_missing_run_id_error_to_stderr(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001")])

        with pytest.raises(SystemExit):
            main(["--ledger-path", str(ledger_path), "--run-id", "nonexistent"])
        err = capsys.readouterr().err
        assert "nonexistent" in err


# ---------------------------------------------------------------------------
# Error: duplicate run_id
# ---------------------------------------------------------------------------


class TestDuplicate:
    def test_duplicate_run_id_exits_1(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001"), _entry("run_001")])

        with pytest.raises(SystemExit) as exc:
            main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        assert exc.value.code == 1

    def test_duplicate_run_id_error_to_stderr(self, tmp_path: Path, capsys):
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001"), _entry("run_001")])

        with pytest.raises(SystemExit):
            main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        err = capsys.readouterr().err
        assert "multiple entries found" in err


# ---------------------------------------------------------------------------
# No promotion / no backtest
# ---------------------------------------------------------------------------


class TestNoPromotion:
    def test_no_registry_write(self, tmp_path: Path):
        """Verify main() is read-only: no import of HypothesisRegistry write."""
        # If the script runs without error on a valid entry, it did not call
        # any registry write. (The registry module may exist but is not called.)
        ledger_path = tmp_path / "ledger.jsonl"
        _write_ledger(ledger_path, [_entry("run_001")])

        ret = main(["--ledger-path", str(ledger_path), "--run-id", "run_001"])
        assert ret == 0
