"""Tests for the pre-earnings candidate batch runner."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
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
                output_dir=outdir,
                dry_run=False,
            )
        assert result.status == "success"
        assert result.n_success == 1
        assert result.n_error == 0

    def test_unsupported_strategy_raises_from_generator(self, tmp_path):
        """Unsupported strategy family propagates as BatchResult status='error'."""
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
        result = run_candidate_batch(
            hyp,
            options_db_path="/tmp/does_not_exist.db",
            preearn_repo_path="/tmp/does_not_exist",
            output_dir=str(tmp_path / "out"),
            dry_run=False,
        )
        assert result.status == "error"
        assert "Unsupported strategy family" in (result.error or "")

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
                output_dir=outdir,
                dry_run=False,
            )
        summary_path = Path(outdir) / f"batch_{result.batch_id}.json"
        assert summary_path.exists()
        data = json.loads(summary_path.read_text())
        assert data["status"] == "success"
        assert len(data["results"]) == 1
