"""Tests for the pre-earnings candidate batch runner."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path
from unittest import mock

import pytest

from engine.edge_discovery.adapters.preearn_options import (
    CandidateSpec,
    PreearnResult,
)
from engine.edge_discovery.hypotheses import (
    AssetClass,
    BatchResult,
    HypothesisSpec,
    HypothesisStatus,
    ParameterConstraint,
    SourceType,
    StrategyFamily,
    ValidationPlan,
    generate_candidates,
    run_candidate_batch,
)
from engine.edge_discovery.hypotheses.batch import now_utc


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _make_preearn_hypothesis(**overrides) -> HypothesisSpec:
    """Return a valid preearn_options HypothesisSpec for testing."""
    kwargs = {
        "hypothesis_id": "hyp_test_001",
        "version": "1.0",
        "source_type": SourceType.empirical_observation,
        "source_reference": "test ref",
        "market_mechanism": "pre-earnings IV crush",
        "expected_effect": "positive P&L",
        "asset_class": AssetClass.equity_options,
        "strategy_family": StrategyFamily.preearn_options,
        "required_data": ("options_eod",),
        "candidate_constraints": (
            ParameterConstraint(name="entry_dpe", values=(2, 3)),
            ParameterConstraint(name="delta_target", values=(0.3, 0.5)),
            ParameterConstraint(name="expiry_rank", values=(0,)),
        ),
        "validation_plan": ValidationPlan(methods=(" CPCV",)),
        "failure_modes": (),
        "kill_criteria": (),
        "status": HypothesisStatus.draft,
    }
    kwargs.update(overrides)
    return HypothesisSpec(**kwargs)


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class TestBatchResultDataclass:
    def test_batch_result_to_dict(self):
        started = "2024-01-01T00:00:00Z"
        completed = "2024-01-01T00:01:00Z"
        result = BatchResult(
            batch_id="batch_123456_abc",
            hypothesis_id="hyp_001",
            status="success",
            n_candidates_generated=4,
            n_candidates_selected=2,
            n_success=2,
            n_error=0,
            started_at=started,
            completed_at=completed,
        )
        d = result.to_dict()
        assert d["batch_id"] == "batch_123456_abc"
        assert d["status"] == "success"
        assert d["n_candidates_generated"] == 4
        assert d["n_candidates_selected"] == 2
        assert d["n_success"] == 2
        assert d["n_error"] == 0


class TestNowUtc:
    def test_now_utc_format(self):
        ts = now_utc()
        # ISO8601 with T and either Z or +00:00 offset
        assert "T" in ts
        assert ts.endswith("Z") or ts.endswith("+00:00")


class TestDryRun:
    def test_dry_run_returns_dry_run_status(self, tmp_path):
        hyp = _make_preearn_hypothesis()
        outdir = str(tmp_path / "output")
        result = run_candidate_batch(
            hyp,
            options_db_path="/tmp/does_not_exist.db",
            preearn_repo_path="/tmp/does_not_exist",
            ledger_path=str(tmp_path / "ledger.jsonl"),
            output_dir=outdir,
            dry_run=True,
        )
        assert result.status == "dry_run"

    def test_dry_run_returns_correct_candidate_counts(self, tmp_path):
        hyp = _make_preearn_hypothesis()
        outdir = str(tmp_path / "output")
        result = run_candidate_batch(
            hyp,
            options_db_path="/tmp/does_not_exist.db",
            preearn_repo_path="/tmp/does_not_exist",
            ledger_path=str(tmp_path / "ledger.jsonl"),
            output_dir=outdir,
            dry_run=True,
        )
        # 2 entry_dpe × 2 delta_target × 1 expiry_rank = 4
        assert result.n_candidates_generated == 4
        assert result.n_candidates_selected == 4
        assert len(result.results) == 0

    def test_dry_run_no_adapter_calls(self, tmp_path):
        hyp = _make_preearn_hypothesis()
        outdir = str(tmp_path / "output")
        with mock.patch(
            "engine.edge_discovery.hypotheses.batch.run_preearn_backtest",
        ) as mock_run:
            run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=str(tmp_path / "ledger.jsonl"),
                output_dir=outdir,
                dry_run=True,
            )
        mock_run.assert_not_called()

    def test_max_candidates_limits_selected(self, tmp_path):
        hyp = _make_preearn_hypothesis()
        outdir = str(tmp_path / "output")
        result = run_candidate_batch(
            hyp,
            options_db_path="/tmp/does_not_exist.db",
            preearn_repo_path="/tmp/does_not_exist",
            ledger_path=str(tmp_path / "ledger.jsonl"),
            output_dir=outdir,
            max_candidates=2,
            dry_run=True,
        )
        assert result.n_candidates_generated == 4
        assert result.n_candidates_selected == 2


class TestNonDryRun:
    def test_calls_adapter_per_selected_candidate(self, tmp_path):
        hyp = _make_preearn_hypothesis(
            candidate_constraints=(
                ParameterConstraint(name="entry_dpe", values=(2,)),
                ParameterConstraint(name="delta_target", values=(0.3,)),
                ParameterConstraint(name="expiry_rank", values=(0,)),
            ),
        )
        outdir = str(tmp_path / "output")
        mock_result = PreearnResult(
            run_id="run_001",
            candidate_id="preearn_dpe2_delta30_rank0",
            status="success",
            config_hash="abcd1234",
            git_commit=None,
            command="",
            repo_path="/tmp/repo",
            output_artifacts={},
            metrics_summary={},
        )
        with mock.patch(
            "engine.edge_discovery.hypotheses.batch.run_preearn_backtest",
            return_value=mock_result,
        ) as mock_run:
            result = run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=str(tmp_path / "ledger.jsonl"),
                output_dir=outdir,
                dry_run=False,
            )
        assert mock_run.call_count == 1

    def test_timeout_passed_to_adapter(self, tmp_path):
        hyp = _make_preearn_hypothesis(
            candidate_constraints=(
                ParameterConstraint(name="entry_dpe", values=(2,)),
                ParameterConstraint(name="delta_target", values=(0.3,)),
                ParameterConstraint(name="expiry_rank", values=(0,)),
            ),
        )
        outdir = str(tmp_path / "output")
        mock_result = PreearnResult(
            run_id="run_001",
            candidate_id="preearn_dpe2_delta30_rank0",
            status="success",
            config_hash="abcd1234",
            git_commit=None,
            command="",
            repo_path="/tmp/repo",
            output_artifacts={},
            metrics_summary={},
        )
        with mock.patch(
            "engine.edge_discovery.hypotheses.batch.run_preearn_backtest",
            return_value=mock_result,
        ) as mock_run:
            run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=str(tmp_path / "ledger.jsonl"),
                output_dir=outdir,
                timeout=300.0,
                dry_run=False,
            )
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs.get("timeout") == 300.0

    def test_ledger_path_passed_to_adapter(self, tmp_path):
        hyp = _make_preearn_hypothesis(
            candidate_constraints=(
                ParameterConstraint(name="entry_dpe", values=(2,)),
                ParameterConstraint(name="delta_target", values=(0.3,)),
                ParameterConstraint(name="expiry_rank", values=(0,)),
            ),
        )
        outdir = str(tmp_path / "output")
        ledger = str(tmp_path / "ledger.jsonl")
        mock_result = PreearnResult(
            run_id="run_001",
            candidate_id="preearn_dpe2_delta30_rank0",
            status="success",
            config_hash="abcd1234",
            git_commit=None,
            command="",
            repo_path="/tmp/repo",
            output_artifacts={},
            metrics_summary={},
        )
        with mock.patch(
            "engine.edge_discovery.hypotheses.batch.run_preearn_backtest",
            return_value=mock_result,
        ) as mock_run:
            run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                output_dir=outdir,
                ledger_path=ledger,
                dry_run=False,
            )
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs.get("ledger_path") == ledger

    def test_mixed_success_and_error_gives_partial(self, tmp_path):
        hyp = _make_preearn_hypothesis(
            candidate_constraints=(
                ParameterConstraint(name="entry_dpe", values=(2, 3)),
                ParameterConstraint(name="delta_target", values=(0.3,)),
                ParameterConstraint(name="expiry_rank", values=(0,)),
            ),
        )
        outdir = str(tmp_path / "output")
        # First call succeeds, second raises
        success_result = PreearnResult(
            run_id="run_001",
            candidate_id="preearn_dpe2_delta30_rank0",
            status="success",
            config_hash="abcd",
            git_commit=None,
            command="",
            repo_path="/tmp/repo",
            output_artifacts={},
            metrics_summary={},
        )

        def _mock_run(spec, **kwargs):
            if not hasattr(_mock_run, "_called"):
                _mock_run._called = True
                return success_result
            raise RuntimeError("subprocess failed")

        with mock.patch(
            "engine.edge_discovery.hypotheses.batch.run_preearn_backtest",
            side_effect=_mock_run,
        ):
            result = run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=str(tmp_path / "ledger.jsonl"),
                output_dir=outdir,
                dry_run=False,
            )

        assert result.status == "partial"
        assert result.n_success == 1
        assert result.n_error == 1

    def test_all_successes_gives_success_status(self, tmp_path):
        hyp = _make_preearn_hypothesis(
            candidate_constraints=(
                ParameterConstraint(name="entry_dpe", values=(2,)),
                ParameterConstraint(name="delta_target", values=(0.3,)),
                ParameterConstraint(name="expiry_rank", values=(0,)),
            ),
        )
        outdir = str(tmp_path / "output")
        mock_result = PreearnResult(
            run_id="run_001",
            candidate_id="preearn_dpe2_delta30_rank0",
            status="success",
            config_hash="abcd1234",
            git_commit=None,
            command="",
            repo_path="/tmp/repo",
            output_artifacts={},
            metrics_summary={},
        )
        with mock.patch(
            "engine.edge_discovery.hypotheses.batch.run_preearn_backtest",
            return_value=mock_result,
        ):
            result = run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=str(tmp_path / "ledger.jsonl"),
                output_dir=outdir,
                dry_run=False,
            )
        assert result.status == "success"
        assert result.n_success == 1
        assert result.n_error == 0

    def test_unsupported_strategy_raises_from_generator(self, tmp_path):
        """Unsupported strategy family re-raises ValueError after writing ledger entry."""
        hyp = HypothesisSpec(
            hypothesis_id="hyp_bad",
            version="1.0",
            source_type=SourceType.empirical_observation,
            source_reference="test",
            market_mechanism="test",
            expected_effect="test",
            asset_class=AssetClass.equity,
            strategy_family=StrategyFamily.momentum,
            required_data=("returns",),
            candidate_constraints=(
                ParameterConstraint(name="window", values=(20,)),
            ),
            validation_plan=ValidationPlan(methods=("t Test",)),
            failure_modes=(),
            kill_criteria=(),
        )
        with pytest.raises(ValueError, match="Unsupported strategy family"):
            run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=str(tmp_path / "ledger.jsonl"),
                output_dir=str(tmp_path / "out"),
                dry_run=False,
            )

    def test_no_subprocess_in_tests(self, tmp_path):
        """Meta-test: ensure no real subprocess calls leak into test environment."""
        hyp = _make_preearn_hypothesis(
            candidate_constraints=(
                ParameterConstraint(name="entry_dpe", values=(2,)),
                ParameterConstraint(name="delta_target", values=(0.3,)),
                ParameterConstraint(name="expiry_rank", values=(0,)),
            ),
        )
        outdir = str(tmp_path / "output")
        mock_result = PreearnResult(
            run_id="run_001",
            candidate_id="preearn_dpe2_delta30_rank0",
            status="success",
            config_hash="abcd1234",
            git_commit=None,
            command="",
            repo_path="/tmp/repo",
            output_artifacts={},
            metrics_summary={},
        )
        with mock.patch(
            "engine.edge_discovery.hypotheses.batch.run_preearn_backtest",
            return_value=mock_result,
        ):
            run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=str(tmp_path / "ledger.jsonl"),
                output_dir=outdir,
                dry_run=False,
            )


class TestBatchSummaryJson:
    def test_batch_summary_json_is_written_dry_run(self, tmp_path):
        hyp = _make_preearn_hypothesis()
        outdir = str(tmp_path / "output")
        result = run_candidate_batch(
            hyp,
            options_db_path="/tmp/does_not_exist.db",
            preearn_repo_path="/tmp/does_not_exist",
            ledger_path=str(tmp_path / "ledger.jsonl"),
            output_dir=outdir,
            dry_run=True,
        )
        summary_path = Path(outdir) / f"batch_{result.batch_id}.json"
        assert summary_path.exists(), f"Summary file not found: {summary_path}"

    def test_batch_summary_json_contains_expected_keys(self, tmp_path):
        hyp = _make_preearn_hypothesis()
        outdir = str(tmp_path / "output")
        result = run_candidate_batch(
            hyp,
            options_db_path="/tmp/does_not_exist.db",
            preearn_repo_path="/tmp/does_not_exist",
            ledger_path=str(tmp_path / "ledger.jsonl"),
            output_dir=outdir,
            dry_run=True,
        )
        summary_path = Path(outdir) / f"batch_{result.batch_id}.json"
        data = json.loads(summary_path.read_text())
        assert set(data.keys()) == {
            "batch_id",
            "hypothesis_id",
            "status",
            "n_candidates_generated",
            "n_candidates_selected",
            "n_success",
            "n_error",
            "results",
            "output_artifacts",
            "error",
            "started_at",
            "completed_at",
        }
        assert data["status"] == "dry_run"
        assert data["n_candidates_generated"] == 4

    def test_batch_summary_json_written_on_execution(self, tmp_path):
        hyp = _make_preearn_hypothesis(
            candidate_constraints=(
                ParameterConstraint(name="entry_dpe", values=(2,)),
                ParameterConstraint(name="delta_target", values=(0.3,)),
                ParameterConstraint(name="expiry_rank", values=(0,)),
            ),
        )
        outdir = str(tmp_path / "output")
        mock_result = PreearnResult(
            run_id="run_001",
            candidate_id="preearn_dpe2_delta30_rank0",
            status="success",
            config_hash="abcd1234",
            git_commit=None,
            command="",
            repo_path="/tmp/repo",
            output_artifacts={},
            metrics_summary={},
        )
        with mock.patch(
            "engine.edge_discovery.hypotheses.batch.run_preearn_backtest",
            return_value=mock_result,
        ):
            result = run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=str(tmp_path / "ledger.jsonl"),
                output_dir=outdir,
                dry_run=False,
            )
        summary_path = Path(outdir) / f"batch_{result.batch_id}.json"
        assert summary_path.exists()
        data = json.loads(summary_path.read_text())
        assert data["status"] == "success"
        assert len(data["results"]) == 1


# --------------------------------------------------------------------------
# Batch ledger entry tests
# --------------------------------------------------------------------------


class TestBatchLedgerEntry:
    """Tests for the batch-level LedgerEntry written by run_candidate_batch."""

    def test_batch_ledger_entry_written_on_dry_run(self, tmp_path):
        """dry_run=True writes one batch-level ledger entry."""
        from engine.edge_discovery.ledger import Ledger

        hyp = _make_preearn_hypothesis()
        ledger_path = str(tmp_path / "ledger.jsonl")
        result = run_candidate_batch(
            hyp,
            options_db_path="/tmp/does_not_exist.db",
            preearn_repo_path="/tmp/does_not_exist",
            ledger_path=ledger_path,
            output_dir=str(tmp_path / "batch"),
            dry_run=True,
        )
        entries = Ledger(ledger_path).read()
        batch_entries = [e for e in entries if e.run_type == "preearn_candidate_batch"]
        assert len(batch_entries) == 1
        assert batch_entries[0].run_id == result.batch_id
        assert batch_entries[0].status == "dry_run"

    def test_batch_ledger_entry_written_on_success(self, tmp_path):
        """success writes batch ledger entry with status='success'."""
        from engine.edge_discovery.ledger import Ledger

        hyp = _make_preearn_hypothesis(
            candidate_constraints=(
                ParameterConstraint(name="entry_dpe", values=(2,)),
                ParameterConstraint(name="delta_target", values=(0.3,)),
                ParameterConstraint(name="expiry_rank", values=(0,)),
            ),
        )
        ledger_path = str(tmp_path / "ledger.jsonl")
        mock_result = PreearnResult(
            run_id="run_001",
            candidate_id="preearn_dpe2_delta30_rank0",
            status="success",
            config_hash="abcd1234",
            git_commit=None,
            command="",
            repo_path="/tmp/repo",
            output_artifacts={},
            metrics_summary={},
        )
        with mock.patch(
            "engine.edge_discovery.hypotheses.batch.run_preearn_backtest",
            return_value=mock_result,
        ):
            result = run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=ledger_path,
                output_dir=str(tmp_path / "batch"),
                dry_run=False,
            )
        entries = Ledger(ledger_path).read()
        batch_entries = [e for e in entries if e.run_type == "preearn_candidate_batch"]
        assert len(batch_entries) == 1
        assert batch_entries[0].run_id == result.batch_id
        assert batch_entries[0].status == "success"

    def test_batch_ledger_entry_written_on_partial(self, tmp_path):
        """partial status is recorded in the ledger entry."""
        from engine.edge_discovery.ledger import Ledger

        hyp = _make_preearn_hypothesis(
            candidate_constraints=(
                ParameterConstraint(name="entry_dpe", values=(2, 3)),
                ParameterConstraint(name="delta_target", values=(0.3,)),
                ParameterConstraint(name="expiry_rank", values=(0,)),
            ),
        )
        ledger_path = str(tmp_path / "ledger.jsonl")
        success_result = PreearnResult(
            run_id="run_001",
            candidate_id="preearn_dpe2_delta30_rank0",
            status="success",
            config_hash="abcd",
            git_commit=None,
            command="",
            repo_path="/tmp/repo",
            output_artifacts={},
            metrics_summary={},
        )

        def _mock_run(spec, **kwargs):
            if not hasattr(_mock_run, "_called"):
                _mock_run._called = True
                return success_result
            raise RuntimeError("subprocess failed")

        with mock.patch(
            "engine.edge_discovery.hypotheses.batch.run_preearn_backtest",
            side_effect=_mock_run,
        ):
            result = run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=ledger_path,
                output_dir=str(tmp_path / "batch"),
                dry_run=False,
            )
        assert result.status == "partial"
        entries = Ledger(ledger_path).read()
        batch_entries = [e for e in entries if e.run_type == "preearn_candidate_batch"]
        assert batch_entries[0].status == "partial"

    def test_batch_ledger_entry_written_on_all_error(self, tmp_path):
        """all-error status is recorded in the ledger entry."""
        from engine.edge_discovery.ledger import Ledger

        hyp = _make_preearn_hypothesis(
            candidate_constraints=(
                ParameterConstraint(name="entry_dpe", values=(2,)),
                ParameterConstraint(name="delta_target", values=(0.3,)),
                ParameterConstraint(name="expiry_rank", values=(0,)),
            ),
        )
        ledger_path = str(tmp_path / "ledger.jsonl")

        def _mock_run(spec, **kwargs):
            raise RuntimeError("subprocess failed")

        with mock.patch(
            "engine.edge_discovery.hypotheses.batch.run_preearn_backtest",
            side_effect=_mock_run,
        ):
            result = run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=ledger_path,
                output_dir=str(tmp_path / "batch"),
                dry_run=False,
            )
        assert result.status == "error"
        entries = Ledger(ledger_path).read()
        batch_entries = [e for e in entries if e.run_type == "preearn_candidate_batch"]
        assert batch_entries[0].status == "error"

    def test_batch_ledger_entry_has_correct_fields(self, tmp_path):
        """Ledger entry has all required fields populated correctly."""
        from engine.edge_discovery.ledger import Ledger

        hyp = _make_preearn_hypothesis()
        ledger_path = str(tmp_path / "ledger.jsonl")
        result = run_candidate_batch(
            hyp,
            options_db_path="/tmp/does_not_exist.db",
            preearn_repo_path="/tmp/does_not_exist",
            ledger_path=ledger_path,
            output_dir=str(tmp_path / "batch"),
            dry_run=True,
        )
        entries = Ledger(ledger_path).read()
        batch_entries = [e for e in entries if e.run_type == "preearn_candidate_batch"]
        e = batch_entries[0]
        assert e.run_id == result.batch_id
        assert e.run_type == "preearn_candidate_batch"
        assert e.status == "dry_run"
        assert e.config_hash != ""
        assert e.input_artifacts.get("hypothesis_id") == hyp.hypothesis_id
        assert "batch_summary_json" in e.output_artifacts
        assert e.metrics_summary.get("n_candidates_generated") == 4
        assert e.metrics_summary.get("n_candidates_selected") == 4
        assert e.metrics_summary.get("n_success") == 0
        assert e.metrics_summary.get("n_error") == 0

    def test_batch_ledger_entry_honors_custom_ledger_path(self, tmp_path):
        """Custom ledger_path is used for the batch ledger entry."""
        from engine.edge_discovery.ledger import Ledger

        hyp = _make_preearn_hypothesis()
        custom_ledger = str(tmp_path / "custom.jsonl")
        run_candidate_batch(
            hyp,
            options_db_path="/tmp/does_not_exist.db",
            preearn_repo_path="/tmp/does_not_exist",
            ledger_path=custom_ledger,
            output_dir=str(tmp_path / "batch"),
            dry_run=True,
        )
        entries = Ledger(custom_ledger).read()
        batch_entries = [e for e in entries if e.run_type == "preearn_candidate_batch"]
        assert len(batch_entries) == 1

    def test_batch_ledger_failure_is_nonfatal(self, tmp_path):
        """Ledger write failure does not prevent BatchResult from being returned."""
        hyp = _make_preearn_hypothesis()
        ledger_path = str(tmp_path / "ledger.jsonl")
        with mock.patch(
            "engine.edge_discovery.hypotheses.batch.Ledger.write",
            side_effect=RuntimeError("disk full"),
        ):
            result = run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=ledger_path,
                output_dir=str(tmp_path / "batch"),
                dry_run=True,
            )
        assert result.status == "dry_run"
        assert result.batch_id != ""

    def test_batch_ledger_entry_written_on_generation_failure_then_reraises(self, tmp_path):
        """Generation failure writes error ledger entry, then re-raises."""
        from engine.edge_discovery.ledger import Ledger

        hyp = HypothesisSpec(
            hypothesis_id="hyp_bad",
            version="1.0",
            source_type=SourceType.empirical_observation,
            source_reference="test",
            market_mechanism="test",
            expected_effect="test",
            asset_class=AssetClass.equity,
            strategy_family=StrategyFamily.momentum,
            required_data=("returns",),
            candidate_constraints=(
                ParameterConstraint(name="window", values=(20,)),
            ),
            validation_plan=ValidationPlan(methods=("t Test",)),
            failure_modes=(),
            kill_criteria=(),
        )
        ledger_path = str(tmp_path / "ledger.jsonl")
        with pytest.raises(ValueError, match="Unsupported strategy family"):
            run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=ledger_path,
                output_dir=str(tmp_path / "batch"),
                dry_run=False,
            )
        # Ledger entry was written despite the re-raise
        entries = Ledger(ledger_path).read()
        batch_entries = [e for e in entries if e.run_type == "preearn_candidate_batch"]
        assert len(batch_entries) == 1
        assert batch_entries[0].status == "error"
        assert "Unsupported" in (batch_entries[0].error or "")

    def test_batch_ledger_entry_uses_ledger_path_not_hypothesis_registry_path(self, tmp_path):
        """Batch ledger is written to ledger_path, not hypothesis_registry_path."""
        from engine.edge_discovery.ledger import Ledger

        hyp = _make_preearn_hypothesis()
        ledger_path = str(tmp_path / "ledger.jsonl")
        # Explicitly pass ledger_path=None to exercise the config fallback
        with mock.patch(
            "engine.edge_discovery.hypotheses.batch.get_config",
            return_value={
                "ledger_path": ledger_path,
                "hypothesis_registry_path": str(tmp_path / "hypotheses.jsonl"),
            },
        ):
            result = run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=None,
                output_dir=str(tmp_path / "batch"),
                dry_run=True,
            )
        entries = Ledger(ledger_path).read()
        batch_entries = [e for e in entries if e.run_type == "preearn_candidate_batch"]
        assert len(batch_entries) == 1
            # Hypotheses file should not exist
        assert not Path(tmp_path / "hypotheses.jsonl").exists()


# --------------------------------------------------------------------------
# Structured error semantics
# --------------------------------------------------------------------------


from engine.edge_discovery.hypotheses.batch import _build_error_result


class TestStructuredErrorSemantics:
    """Verify that _build_error_result produces structured error metadata."""

    def test_subprocess_error_has_structured_fields(self, tmp_path):
        """CalledProcessError is encoded with error_type=subprocess_error and return_code."""
        spec = CandidateSpec(
            entry_dpe=2,
            delta_target=0.30,
            expiry_rank=0,
            options_db_path=str(tmp_path / "opts.db"),
            preearn_repo_path=str(tmp_path / "repo"),
        )
        exc = subprocess.CalledProcessError(returncode=42, cmd=["python", "fail.py"], stderr=b" Segmentation fault\n")
        result = _build_error_result(spec, exc, str(tmp_path / "repo"))

        assert result.status == "error"
        assert result.error_type == "subprocess_error"
        assert result.return_code == 42
        assert result.timed_out is False
        assert result.timeout_seconds is None
        assert "CalledProcessError" in result.error
        assert "42" in result.error
        assert "Segmentation fault" in result.error

    def test_subprocess_error_no_stderr(self, tmp_path):
        """CalledProcessError with no stderr still has structured return_code."""
        spec = CandidateSpec(
            entry_dpe=2,
            delta_target=0.30,
            expiry_rank=0,
            options_db_path=str(tmp_path / "opts.db"),
            preearn_repo_path=str(tmp_path / "repo"),
        )
        exc = subprocess.CalledProcessError(returncode=1, cmd=["python", "fail.py"])
        result = _build_error_result(spec, exc, str(tmp_path / "repo"))

        assert result.error_type == "subprocess_error"
        assert result.return_code == 1
        assert "CalledProcessError(returncode=1)" in result.error

    def test_timeout_error_has_structured_fields(self, tmp_path):
        """TimeoutExpired is encoded with error_type=timeout and timed_out=True."""
        spec = CandidateSpec(
            entry_dpe=2,
            delta_target=0.30,
            expiry_rank=0,
            options_db_path=str(tmp_path / "opts.db"),
            preearn_repo_path=str(tmp_path / "repo"),
        )
        exc = subprocess.TimeoutExpired(cmd=["python", "slow.py"], timeout=300.0)
        result = _build_error_result(spec, exc, str(tmp_path / "repo"))

        assert result.status == "error"
        assert result.error_type == "timeout"
        assert result.timed_out is True
        assert result.timeout_seconds == 300.0
        assert result.return_code is None
        assert "TimeoutExpired" in result.error
        assert "300" in result.error

    def test_timeout_error_zero_seconds_preserved(self, tmp_path):
        """TimeoutExpired(timeout=0) preserves 0 in timeout_seconds, not None."""
        spec = CandidateSpec(
            entry_dpe=2,
            delta_target=0.30,
            expiry_rank=0,
            options_db_path=str(tmp_path / "opts.db"),
            preearn_repo_path=str(tmp_path / "repo"),
        )
        exc = subprocess.TimeoutExpired(cmd=["python", "instant.py"], timeout=0)
        result = _build_error_result(spec, exc, str(tmp_path / "repo"))

        assert result.error_type == "timeout"
        assert result.timed_out is True
        assert result.timeout_seconds == 0
        assert "0s" in result.error
        assert "TimeoutExpired" in result.error

    def test_timeout_error_zero_float_seconds_preserved(self, tmp_path):
        """TimeoutExpired(timeout=0.0) preserves 0.0 in timeout_seconds."""
        spec = CandidateSpec(
            entry_dpe=2,
            delta_target=0.30,
            expiry_rank=0,
            options_db_path=str(tmp_path / "opts.db"),
            preearn_repo_path=str(tmp_path / "repo"),
        )
        exc = subprocess.TimeoutExpired(cmd=["python", "instant.py"], timeout=0.0)
        result = _build_error_result(spec, exc, str(tmp_path / "repo"))

        assert result.error_type == "timeout"
        assert result.timed_out is True
        assert result.timeout_seconds == 0.0
        assert "0" in result.error

    def test_internal_error_is_structured(self, tmp_path):
        """Unexpected Exception is encoded with error_type=internal_error."""
        spec = CandidateSpec(
            entry_dpe=2,
            delta_target=0.30,
            expiry_rank=0,
            options_db_path=str(tmp_path / "opts.db"),
            preearn_repo_path=str(tmp_path / "repo"),
        )
        exc = ValueError("unexpected config mismatch")
        result = _build_error_result(spec, exc, str(tmp_path / "repo"))

        assert result.status == "error"
        assert result.error_type == "internal_error"
        assert result.timed_out is False
        assert result.return_code is None
        assert result.timeout_seconds is None
        assert "unexpected config mismatch" in result.error

    def test_error_result_is_json_serializable(self, tmp_path):
        """Structured error result can be JSON-serialized."""
        spec = CandidateSpec(
            entry_dpe=2,
            delta_target=0.30,
            expiry_rank=0,
            options_db_path=str(tmp_path / "opts.db"),
            preearn_repo_path=str(tmp_path / "repo"),
        )
        exc = subprocess.CalledProcessError(returncode=1, cmd=["python", "fail.py"])
        result = _build_error_result(spec, exc, str(tmp_path / "repo"))

        json_str = json.dumps(asdict(result))
        restored = json.loads(json_str)
        assert restored["status"] == "error"
        assert restored["error_type"] == "subprocess_error"
        assert restored["return_code"] == 1

    def test_batch_continues_after_subprocess_error(self, tmp_path):
        """Batch continues and records structured error when one candidate fails."""
        hyp = _make_preearn_hypothesis(
            candidate_constraints=(
                ParameterConstraint(name="entry_dpe", values=(2, 3)),
                ParameterConstraint(name="delta_target", values=(0.3,)),
                ParameterConstraint(name="expiry_rank", values=(0,)),
            ),
        )
        outdir = str(tmp_path / "output")
        success_result = PreearnResult(
            run_id="run_001",
            candidate_id="preearn_dpe2_delta30_rank0",
            status="success",
            config_hash="abcd",
            git_commit=None,
            command="",
            repo_path="/tmp/repo",
            output_artifacts={},
            metrics_summary={},
        )

        def _mock_run(spec, **kwargs):
            if not hasattr(_mock_run, "_called"):
                _mock_run._called = True
                return success_result
            raise subprocess.CalledProcessError(returncode=127, cmd=["missing"], stderr=b"command not found")

        with mock.patch(
            "engine.edge_discovery.hypotheses.batch.run_preearn_backtest",
            side_effect=_mock_run,
        ):
            result = run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=str(tmp_path / "ledger.jsonl"),
                output_dir=outdir,
                dry_run=False,
            )

        assert result.status == "partial"
        assert result.n_success == 1
        assert result.n_error == 1
        error_result = next(r for r in result.results if r.error_type == "subprocess_error")
        assert error_result.return_code == 127
        assert "command not found" in error_result.error

    def test_batch_continues_after_timeout(self, tmp_path):
        """Batch continues and records structured error when one candidate times out."""
        hyp = _make_preearn_hypothesis(
            candidate_constraints=(
                ParameterConstraint(name="entry_dpe", values=(2, 3)),
                ParameterConstraint(name="delta_target", values=(0.3,)),
                ParameterConstraint(name="expiry_rank", values=(0,)),
            ),
        )
        outdir = str(tmp_path / "output")
        success_result = PreearnResult(
            run_id="run_001",
            candidate_id="preearn_dpe2_delta30_rank0",
            status="success",
            config_hash="abcd",
            git_commit=None,
            command="",
            repo_path="/tmp/repo",
            output_artifacts={},
            metrics_summary={},
        )

        def _mock_run(spec, **kwargs):
            if not hasattr(_mock_run, "_called"):
                _mock_run._called = True
                return success_result
            raise subprocess.TimeoutExpired(cmd=["python", "slow.py"], timeout=600.0)

        with mock.patch(
            "engine.edge_discovery.hypotheses.batch.run_preearn_backtest",
            side_effect=_mock_run,
        ):
            result = run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=str(tmp_path / "ledger.jsonl"),
                output_dir=outdir,
                dry_run=False,
            )

        assert result.status == "partial"
        assert result.n_error == 1
        error_result = next(r for r in result.results if r.error_type == "timeout")
        assert error_result.timed_out is True
        assert error_result.timeout_seconds == 600.0
        assert "TimeoutExpired" in error_result.error

    def test_success_result_has_no_error_fields(self, tmp_path):
        """Successful PreearnResult has no error_type or return_code set."""
        result = PreearnResult(
            run_id="run_001",
            candidate_id="preearn_dpe2_delta30_rank0",
            status="success",
            config_hash="abcd",
            git_commit=None,
            command="python run.py",
            repo_path="/tmp/repo",
            output_artifacts={},
            metrics_summary={"pnl": 1.5},
        )
        # error_type/return_code/timed_out/timeout_seconds default to None/False/None
        assert result.error_type is None
        assert result.return_code is None
        assert result.timed_out is False
        assert result.timeout_seconds is None

    def test_existing_batch_mixed_success_and_error_test_still_passes(self, tmp_path):
        """The existing mixed_success_and_error test continues to pass unchanged."""
        hyp = _make_preearn_hypothesis(
            candidate_constraints=(
                ParameterConstraint(name="entry_dpe", values=(2, 3)),
                ParameterConstraint(name="delta_target", values=(0.3,)),
                ParameterConstraint(name="expiry_rank", values=(0,)),
            ),
        )
        outdir = str(tmp_path / "output")
        success_result = PreearnResult(
            run_id="run_001",
            candidate_id="preearn_dpe2_delta30_rank0",
            status="success",
            config_hash="abcd",
            git_commit=None,
            command="",
            repo_path="/tmp/repo",
            output_artifacts={},
            metrics_summary={},
        )

        def _mock_run(spec, **kwargs):
            if not hasattr(_mock_run, "_called"):
                _mock_run._called = True
                return success_result
            raise RuntimeError("infra failure")

        with mock.patch(
            "engine.edge_discovery.hypotheses.batch.run_preearn_backtest",
            side_effect=_mock_run,
        ):
            result = run_candidate_batch(
                hyp,
                options_db_path="/tmp/does_not_exist.db",
                preearn_repo_path="/tmp/does_not_exist",
                ledger_path=str(tmp_path / "ledger.jsonl"),
                output_dir=outdir,
                dry_run=False,
            )

        assert result.status == "partial"
        assert result.n_success == 1
        assert result.n_error == 1
        error_result = next(r for r in result.results if r.status == "error")
        assert error_result.error_type == "internal_error"
