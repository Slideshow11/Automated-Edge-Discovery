"""Tests for the batch result evaluator module."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest import mock

import pytest

from engine.edge_discovery.evaluation import (
    EvaluationLabel,
    EvaluationResult,
    evaluate_batch_result,
    evaluate_ledger_entry,
)
from engine.edge_discovery.hypotheses.batch import BatchResult
from engine.edge_discovery.ledger import LedgerEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _br(
    status: str,
    n_sel: int = 0,
    n_suc: int = 0,
    n_err: int = 0,
    batch_id: str = "batch_001",
    hypothesis_id: str = "hyp_001",
) -> BatchResult:
    """Construct a minimal BatchResult for testing."""
    return BatchResult(
        batch_id=batch_id,
        hypothesis_id=hypothesis_id,
        status=status,
        n_candidates_generated=n_sel,
        n_candidates_selected=n_sel,
        n_success=n_suc,
        n_error=n_err,
    )


# ---------------------------------------------------------------------------
# EvaluationLabel enum
# ---------------------------------------------------------------------------


class TestEvaluationLabel:
    def test_all_labels_present(self):
        assert EvaluationLabel.INVALID_RUN.value == "invalid_run"
        assert EvaluationLabel.EXECUTION_FAILED.value == "execution_failed"
        assert EvaluationLabel.NEEDS_MORE_DATA.value == "needs_more_data"
        assert EvaluationLabel.PROMISING.value == "promising_for_review"


# ---------------------------------------------------------------------------
# evaluate_batch_result — core classification
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_is_needs_more_data(self):
        result = _br(status="dry_run", n_sel=0, n_suc=0, n_err=0)
        ev = evaluate_batch_result(result)
        assert ev.label == EvaluationLabel.NEEDS_MORE_DATA
        assert "dry_run" in ev.reason
        assert ev.metrics == {}
        assert ev.warnings == ()
        assert ev.source_type == "batch_result"
        assert ev.source_id == "batch_001"
        assert ev.hypothesis_id == "hyp_001"


class TestInvalidRun:
    def test_zero_selected_is_invalid_run(self):
        result = _br(status="success", n_sel=0, n_suc=0, n_err=0)
        ev = evaluate_batch_result(result)
        assert ev.label == EvaluationLabel.INVALID_RUN
        assert "no_candidates_selected" in ev.reason

    def test_zero_selected_with_other_status_also_invalid(self):
        result = _br(status="error", n_sel=0, n_suc=0, n_err=0)
        ev = evaluate_batch_result(result)
        assert ev.label == EvaluationLabel.INVALID_RUN


class TestExecutionFailed:
    def test_all_errors_is_execution_failed(self):
        result = _br(status="error", n_sel=3, n_suc=0, n_err=3)
        ev = evaluate_batch_result(result)
        assert ev.label == EvaluationLabel.EXECUTION_FAILED
        assert "all_candidates_errored" in ev.reason
        assert ev.warnings == ("zero_successful_runs",)

    def test_mixed_with_some_success_not_execution_failed(self):
        # With at least one success, it's not EXECUTION_FAILED
        result = _br(status="partial", n_sel=3, n_suc=1, n_err=2)
        ev = evaluate_batch_result(result)
        assert ev.label != EvaluationLabel.EXECUTION_FAILED


class TestNeedsMoreData:
    def test_high_error_rate_is_needs_more_data(self):
        # 3 error, 1 success = 75% error rate > 50% threshold
        result = _br(status="partial", n_sel=4, n_suc=1, n_err=3)
        ev = evaluate_batch_result(result, max_error_rate=0.5)
        assert ev.label == EvaluationLabel.NEEDS_MORE_DATA
        assert "error_rate" in ev.reason
        assert any("75" in w for w in ev.warnings)

    def test_error_rate_at_threshold_is_promising(self):
        # 1 error, 4 success = 20% error rate, not NEEDS_MORE_DATA
        result = _br(status="success", n_sel=5, n_suc=4, n_err=1)
        ev = evaluate_batch_result(result, max_error_rate=0.5)
        assert ev.label == EvaluationLabel.PROMISING

    def test_insufficient_successes_is_needs_more_data(self):
        # 1 success, 1 error, below min_successes=2
        result = _br(status="partial", n_sel=2, n_suc=1, n_err=1)
        ev = evaluate_batch_result(result, min_successes=2)
        assert ev.label == EvaluationLabel.NEEDS_MORE_DATA
        assert "insufficient" in ev.reason

    def test_insufficient_candidates_is_needs_more_data(self):
        result = _br(status="success", n_sel=1, n_suc=1, n_err=0)
        ev = evaluate_batch_result(result, min_candidates=2)
        assert ev.label == EvaluationLabel.NEEDS_MORE_DATA
        assert "insufficient_candidates" in ev.reason

    def test_zero_success_zero_errors_is_needs_more_data(self):
        # Edge case: n_sel > 0 but both n_suc and n_err are 0
        result = _br(status="partial", n_sel=3, n_suc=0, n_err=0)
        ev = evaluate_batch_result(result)
        assert ev.label == EvaluationLabel.NEEDS_MORE_DATA
        assert "unusual_execution_state" in ev.warnings


class TestPromising:
    def test_all_thresholds_met_is_promising(self):
        result = _br(status="success", n_sel=5, n_suc=4, n_err=1)
        ev = evaluate_batch_result(result, min_candidates=1, min_successes=1, max_error_rate=0.5)
        assert ev.label == EvaluationLabel.PROMISING
        assert "execution_thresholds_met" in ev.reason

    def test_partial_acceptable_is_promising(self):
        result = _br(status="partial", n_sel=3, n_suc=2, n_err=1)
        ev = evaluate_batch_result(result)
        assert ev.label == EvaluationLabel.PROMISING

    def test_zero_error_rate_is_promising(self):
        result = _br(status="success", n_sel=10, n_suc=10, n_err=0)
        ev = evaluate_batch_result(result)
        assert ev.label == EvaluationLabel.PROMISING


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


class TestParameterValidation:
    def test_max_error_rate_below_0_raises(self):
        result = _br(status="success", n_sel=5, n_suc=5, n_err=0)
        with pytest.raises(ValueError, match="max_error_rate must be in"):
            evaluate_batch_result(result, max_error_rate=-0.1)

    def test_max_error_rate_above_1_raises(self):
        result = _br(status="success", n_sel=5, n_suc=5, n_err=0)
        with pytest.raises(ValueError, match="max_error_rate must be in"):
            evaluate_batch_result(result, max_error_rate=1.1)

    def test_max_error_rate_exactly_0_ok(self):
        result = _br(status="success", n_sel=5, n_suc=5, n_err=0)
        ev = evaluate_batch_result(result, max_error_rate=0.0)
        assert ev.label == EvaluationLabel.PROMISING

    def test_max_error_rate_exactly_1_ok(self):
        result = _br(status="success", n_sel=5, n_suc=1, n_err=4)
        ev = evaluate_batch_result(result, max_error_rate=1.0)
        assert ev.label == EvaluationLabel.PROMISING


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_input_gives_same_output(self):
        result = _br(status="partial", n_sel=5, n_suc=3, n_err=2)
        ev1 = evaluate_batch_result(result)
        ev2 = evaluate_batch_result(result)
        assert ev1.label == ev2.label
        assert ev1.reason == ev2.reason
        assert ev1.metrics == ev2.metrics

    def test_different_batches_different_labels(self):
        r1 = _br(status="success", n_sel=5, n_suc=5, n_err=0)
        r2 = _br(status="partial", n_sel=5, n_suc=0, n_err=5)
        ev1 = evaluate_batch_result(r1)
        ev2 = evaluate_batch_result(r2)
        assert ev1.label != ev2.label


# ---------------------------------------------------------------------------
# Field population
# ---------------------------------------------------------------------------


class TestResultFields:
    def test_source_id_populated(self):
        result = _br(status="success", n_sel=5, n_suc=5, n_err=0, batch_id="batch_xyz")
        ev = evaluate_batch_result(result)
        assert ev.source_id == "batch_xyz"

    def test_hypothesis_id_populated(self):
        result = _br(status="success", n_sel=5, n_suc=5, n_err=0, hypothesis_id="hyp_test")
        ev = evaluate_batch_result(result)
        assert ev.hypothesis_id == "hyp_test"

    def test_metrics_include_counts(self):
        result = _br(status="success", n_sel=4, n_suc=3, n_err=1)
        ev = evaluate_batch_result(result)
        assert ev.metrics["n_candidates_selected"] == 4
        assert ev.metrics["n_success"] == 3
        assert ev.metrics["n_error"] == 1
        assert "error_rate" in ev.metrics

    def test_warnings_tuple_not_list(self):
        # error rate warning
        result = _br(status="partial", n_sel=4, n_suc=1, n_err=3)
        ev = evaluate_batch_result(result, max_error_rate=0.5)
        assert isinstance(ev.warnings, tuple)


# ---------------------------------------------------------------------------
# evaluate_ledger_entry
# ---------------------------------------------------------------------------


class TestEvaluateLedgerEntry:
    def _entry(
        self,
        run_type: str = "preearn_candidate_batch",
        status: str = "success",
        hypothesis_id: str = "hyp_ledger",
        n_sel: int = 0,
        n_suc: int = 0,
        n_err: int = 0,
        run_id: str = "run_ledger_001",
    ) -> LedgerEntry:
        return LedgerEntry(
            run_id=run_id,
            run_type=run_type,
            started_at="2025-01-01T00:00:00Z",
            completed_at="2025-01-01T00:01:00Z",
            status=status,
            config_hash="abc123",
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

    def test_unsupported_run_type_is_invalid_run(self):
        entry = self._entry(run_type="wfa_cpcv")
        ev = evaluate_ledger_entry(entry)
        assert ev.label == EvaluationLabel.INVALID_RUN
        assert "unsupported_run_type" in ev.reason
        assert ev.source_type == "ledger_entry"
        assert ev.source_id == "run_ledger_001"

    def test_unknown_run_type_raises_nothing(self):
        # Should return INVALID_RUN, not raise
        entry = self._entry(run_type="unknown_type")
        ev = evaluate_ledger_entry(entry)
        assert ev.label == EvaluationLabel.INVALID_RUN

    def test_preearn_candidate_batch_success_is_promising(self):
        entry = self._entry(
            status="success",
            n_sel=5,
            n_suc=4,
            n_err=1,
        )
        ev = evaluate_ledger_entry(entry)
        assert ev.label == EvaluationLabel.PROMISING
        assert ev.source_type == "ledger_entry"
        assert ev.hypothesis_id == "hyp_ledger"

    def test_preearn_candidate_batch_all_errors_is_execution_failed(self):
        entry = self._entry(
            status="error",
            n_sel=3,
            n_suc=0,
            n_err=3,
        )
        ev = evaluate_ledger_entry(entry)
        assert ev.label == EvaluationLabel.EXECUTION_FAILED

    def test_preearn_candidate_batch_high_error_rate_is_needs_more_data(self):
        entry = self._entry(
            status="partial",
            n_sel=4,
            n_suc=1,
            n_err=3,
        )
        ev = evaluate_ledger_entry(entry, max_error_rate=0.5)
        assert ev.label == EvaluationLabel.NEEDS_MORE_DATA

    def test_entry_status_used_as_batch_status(self):
        # entry.status="error" with n_sel=0 should be INVALID_RUN (no candidates ran)
        entry = self._entry(status="error", n_sel=0, n_suc=0, n_err=0)
        ev = evaluate_ledger_entry(entry)
        assert ev.label == EvaluationLabel.INVALID_RUN

    def test_ledger_entry_respects_max_error_rate_param(self):
        entry = self._entry(status="partial", n_sel=5, n_suc=4, n_err=1)
        # 1/5 = 20% error rate, passes at 0.5, fails at 0.1
        ev_fail = evaluate_ledger_entry(entry, max_error_rate=0.1)
        assert ev_fail.label == EvaluationLabel.NEEDS_MORE_DATA
        ev_pass = evaluate_ledger_entry(entry, max_error_rate=0.5)
        assert ev_pass.label == EvaluationLabel.PROMISING


# ---------------------------------------------------------------------------
# No side effects
# ---------------------------------------------------------------------------


class TestNoSideEffects:
    def test_no_registry_update_occurs(self):
        """Verify evaluate_batch_result does not call HypothesisRegistry."""
        with mock.patch("engine.edge_discovery.hypotheses.registry.HypothesisRegistry") as m:
            result = _br(status="success", n_sel=5, n_suc=5, n_err=0)
            evaluate_batch_result(result)
            m.assert_not_called()

    def test_no_ledger_write_occurs(self):
        """Verify evaluate_batch_result does not write to ledger."""
        with mock.patch.object(
            __import__("engine.edge_discovery.ledger", fromlist=["Ledger"]).Ledger,
            "write",
        ) as m:
            result = _br(status="success", n_sel=5, n_suc=5, n_err=0)
            evaluate_batch_result(result)
            m.assert_not_called()

    def test_no_subprocess_occurs(self):
        """Verify evaluate_batch_result does not call subprocess."""
        with mock.patch("subprocess.run") as m:
            result = _br(status="success", n_sel=5, n_suc=5, n_err=0)
            evaluate_batch_result(result)
            m.assert_not_called()

    def test_no_adapter_called(self):
        """Verify evaluate_batch_result does not call the pre-earnings adapter."""
        with mock.patch(
            "engine.edge_discovery.adapters.preearn_options.run_preearn_backtest"
        ) as m:
            result = _br(status="success", n_sel=5, n_suc=5, n_err=0)
            evaluate_batch_result(result)
            m.assert_not_called()
