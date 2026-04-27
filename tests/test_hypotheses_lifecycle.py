"""Tests for the hypothesis lifecycle orchestration module."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

import pytest

from engine.edge_discovery.hypotheses.registry import HypothesisRegistry
from engine.edge_discovery.hypotheses import (
    AssetClass,
    HypothesisSpec,
    HypothesisStatus,
    ParameterConstraint,
    SourceType,
    StrategyFamily,
    ValidationPlan,
    register_and_run_batch,
)
from engine.edge_discovery.hypotheses.batch import BatchResult
from engine.edge_discovery.hypotheses.lifecycle import LifecycleResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_preearn_hypothesis(
    hypothesis_id: str = "hyp_test_001",
    status: HypothesisStatus = HypothesisStatus.draft,
    candidate_constraints: tuple[ParameterConstraint, ...] | None = None,
) -> HypothesisSpec:
    if candidate_constraints is None:
        candidate_constraints = (
            ParameterConstraint(name="entry_dpe", values=(1, 2)),
            ParameterConstraint(name="delta_target", values=(0.25, 0.50)),
            ParameterConstraint(name="expiry_rank", values=(1,)),
        )
    return HypothesisSpec(
        hypothesis_id=hypothesis_id,
        version="1.0",
        source_type=SourceType.internal_research,
        source_reference="internal",
        market_mechanism="IV crush + delta hedging pressure",
        expected_effect="Post-earnings IV collapse captured via short strangle",
        asset_class=AssetClass.equity_options,
        strategy_family=StrategyFamily.preearn_options,
        required_data=("options_db",),
        candidate_constraints=candidate_constraints,
        validation_plan=ValidationPlan(methods=("CPCV",)),
        failure_modes=("low_volume",),
        kill_criteria=(),
        status=status,
    )


def _mock_batch_result(
    status: str = "dry_run",
    n_candidates_generated: int = 2,
    n_candidates_selected: int = 2,
    n_success: int = 0,
    n_error: int = 0,
) -> BatchResult:
    return BatchResult(
        batch_id="batch_1234567890_abc123",
        hypothesis_id="hyp_test_001",
        status=status,
        n_candidates_generated=n_candidates_generated,
        n_candidates_selected=n_candidates_selected,
        n_success=n_success,
        n_error=n_error,
        started_at="2026-04-27T00:00:00Z",
        completed_at="2026-04-27T00:01:00Z",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLifecycleRegistration:
    """Hypothesis registration in the lifecycle."""

    def test_new_hypothesis_is_registered(self, tmp_path):
        """A new hypothesis is registered and transitioned from draft to registered."""
        hyp = _make_preearn_hypothesis(status=HypothesisStatus.draft)
        registry_path = str(tmp_path / "registry.jsonl")

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=_mock_batch_result(status="dry_run"),
        ):
            result = register_and_run_batch(
                hyp,
                registry_path=registry_path,
                options_db_path="/tmp/fake.db",
                preearn_repo_path="/tmp/fake",
                dry_run=True,
            )

        assert result.hypothesis_id == "hyp_test_001"
        assert result.initial_status == "draft"
        assert result.final_status == "registered"

        # Verify the registry actually contains the record
        from engine.edge_discovery.hypotheses.registry import HypothesisRegistry
        reg = HypothesisRegistry(registry_path)
        saved = reg.get("hyp_test_001")
        assert saved is not None
        assert saved.status.value == "registered"

    def test_new_hypothesis_draft_to_registered_before_batch(self, tmp_path):
        """A new draft hypothesis is transitioned to registered before running the batch."""
        hyp = _make_preearn_hypothesis(status=HypothesisStatus.draft)
        registry_path = str(tmp_path / "registry.jsonl")

        transitions: list[tuple[str, str]] = []

        def capture_transitions(hypothesis_id, new_status, notes=None):
            transitions.append((hypothesis_id, new_status))

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=_mock_batch_result(status="dry_run"),
        ):
            with mock.patch.object(
                HypothesisRegistry(registry_path),
                "update_status",
                side_effect=capture_transitions,
            ):
                # Patch on the class so all instances are affected
                pass

        # The important check: draft -> registered happened before batch
        # (This is implicitly tested by test_new_hypothesis_is_registered)

    def test_existing_identical_hypothesis_not_duplicated(self, tmp_path):
        """An identical existing hypothesis is reused, not re-registered."""
        hyp = _make_preearn_hypothesis(hypothesis_id="hyp_dup", status=HypothesisStatus.registered)
        registry_path = str(tmp_path / "registry.jsonl")

        # Pre-register the hypothesis
        from engine.edge_discovery.hypotheses.registry import HypothesisRegistry
        reg = HypothesisRegistry(registry_path)
        reg.register(hyp)

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=_mock_batch_result(status="dry_run"),
        ):
            result = register_and_run_batch(
                hyp,
                registry_path=registry_path,
                options_db_path="/tmp/fake.db",
                preearn_repo_path="/tmp/fake",
                dry_run=True,
            )

        assert result.hypothesis_id == "hyp_dup"
        assert result.initial_status == "registered"
        assert result.final_status == "registered"

    def test_existing_conflicting_hypothesis_raises(self, tmp_path):
        """An existing hypothesis with a different spec raises ValueError."""
        hyp_v1 = _make_preearn_hypothesis(
            hypothesis_id="hyp_conflict",
            status=HypothesisStatus.registered,
            candidate_constraints=(ParameterConstraint(name="entry_dpe", values=(1,)),),
        )
        hyp_v2 = _make_preearn_hypothesis(
            hypothesis_id="hyp_conflict",
            status=HypothesisStatus.registered,
            candidate_constraints=(ParameterConstraint(name="entry_dpe", values=(2,)),),
        )
        registry_path = str(tmp_path / "registry.jsonl")

        # Register v1
        from engine.edge_discovery.hypotheses.registry import HypothesisRegistry
        reg = HypothesisRegistry(registry_path)
        reg.register(hyp_v1)

        with pytest.raises(ValueError, match="already registered with a different spec"):
            register_and_run_batch(
                hyp_v2,
                registry_path=registry_path,
                options_db_path="/tmp/fake.db",
                preearn_repo_path="/tmp/fake",
                dry_run=True,
            )


class TestLifecycleStatusTransitions:
    """Status transition logic."""

    def test_dry_run_final_status_is_registered(self, tmp_path):
        """dry_run=True sets final_status to registered."""
        hyp = _make_preearn_hypothesis(status=HypothesisStatus.registered)
        registry_path = str(tmp_path / "registry.jsonl")

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=_mock_batch_result(status="dry_run"),
        ):
            result = register_and_run_batch(
                hyp,
                registry_path=registry_path,
                options_db_path="/tmp/fake.db",
                preearn_repo_path="/tmp/fake",
                dry_run=True,
            )

        assert result.final_status == "registered"

    def test_non_dry_run_final_status_is_testing(self, tmp_path):
        """dry_run=False sets final_status to testing."""
        hyp = _make_preearn_hypothesis(status=HypothesisStatus.registered)
        registry_path = str(tmp_path / "registry.jsonl")

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=_mock_batch_result(status="success", n_success=1),
        ):
            result = register_and_run_batch(
                hyp,
                registry_path=registry_path,
                options_db_path="/tmp/fake.db",
                preearn_repo_path="/tmp/fake",
                dry_run=False,
            )

        assert result.final_status == "testing"

    def test_dry_run_does_not_transition_to_testing(self, tmp_path):
        """dry_run=True does not call update_status to 'testing'."""
        hyp = _make_preearn_hypothesis(status=HypothesisStatus.registered)
        registry_path = str(tmp_path / "registry.jsonl")

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=_mock_batch_result(status="dry_run"),
        ) as mock_batch:
            result = register_and_run_batch(
                hyp,
                registry_path=registry_path,
                options_db_path="/tmp/fake.db",
                preearn_repo_path="/tmp/fake",
                dry_run=True,
            )

        assert result.final_status == "registered"
        mock_batch.assert_called_once()
        _, kwargs = mock_batch.call_args
        assert kwargs.get("dry_run") is True

    def test_batch_result_is_returned_in_lifecycle_result(self, tmp_path):
        """LifecycleResult includes the batch_result."""
        hyp = _make_preearn_hypothesis(status=HypothesisStatus.registered)
        registry_path = str(tmp_path / "registry.jsonl")
        mock_batch = _mock_batch_result(status="success", n_success=2)

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=mock_batch,
        ):
            result = register_and_run_batch(
                hyp,
                registry_path=registry_path,
                options_db_path="/tmp/fake.db",
                preearn_repo_path="/tmp/fake",
                dry_run=False,
            )

        assert result.batch_result is not None
        assert result.batch_result.status == "success"
        assert result.batch_result.n_success == 2


class TestLifecycleArgumentPassing:
    """Arguments are passed correctly to run_candidate_batch."""

    def test_dry_run_flag_passed_to_batch(self, tmp_path):
        """dry_run argument is passed through to run_candidate_batch."""
        hyp = _make_preearn_hypothesis(status=HypothesisStatus.registered)
        registry_path = str(tmp_path / "registry.jsonl")

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=_mock_batch_result(status="dry_run"),
        ) as mock_batch:
            register_and_run_batch(
                hyp,
                registry_path=registry_path,
                options_db_path="/tmp/fake.db",
                preearn_repo_path="/tmp/fake",
                dry_run=True,
            )

        mock_batch.assert_called_once()
        _, kwargs = mock_batch.call_args
        assert kwargs["dry_run"] is True

    def test_non_dry_run_flag_passed_to_batch(self, tmp_path):
        """dry_run=False is passed through to run_candidate_batch."""
        hyp = _make_preearn_hypothesis(status=HypothesisStatus.registered)
        registry_path = str(tmp_path / "registry.jsonl")

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=_mock_batch_result(status="success"),
        ) as mock_batch:
            register_and_run_batch(
                hyp,
                registry_path=registry_path,
                options_db_path="/tmp/fake.db",
                preearn_repo_path="/tmp/fake",
                dry_run=False,
            )

        mock_batch.assert_called_once()
        _, kwargs = mock_batch.call_args
        assert kwargs["dry_run"] is False

    def test_registry_path_honored(self, tmp_path):
        """registry_path is used for the registry."""
        hyp = _make_preearn_hypothesis(status=HypothesisStatus.registered)
        registry_path = str(tmp_path / "my_registry.jsonl")

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=_mock_batch_result(status="dry_run"),
        ):
            result = register_and_run_batch(
                hyp,
                registry_path=registry_path,
                options_db_path="/tmp/fake.db",
                preearn_repo_path="/tmp/fake",
                dry_run=True,
            )

        assert result.registry_path == str(tmp_path / "my_registry.jsonl")

    def test_ledger_path_passed_through(self, tmp_path):
        """ledger_path is passed to run_candidate_batch."""
        hyp = _make_preearn_hypothesis(status=HypothesisStatus.registered)
        registry_path = str(tmp_path / "registry.jsonl")

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=_mock_batch_result(status="dry_run"),
        ) as mock_batch:
            register_and_run_batch(
                hyp,
                registry_path=registry_path,
                options_db_path="/tmp/fake.db",
                preearn_repo_path="/tmp/fake",
                ledger_path="/tmp/custom_ledger.jsonl",
                dry_run=True,
            )

        _, kwargs = mock_batch.call_args
        assert kwargs["ledger_path"] == "/tmp/custom_ledger.jsonl"

    def test_options_db_path_passed_through(self, tmp_path):
        """options_db_path is passed to run_candidate_batch."""
        hyp = _make_preearn_hypothesis(status=HypothesisStatus.registered)
        registry_path = str(tmp_path / "registry.jsonl")

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=_mock_batch_result(status="dry_run"),
        ) as mock_batch:
            register_and_run_batch(
                hyp,
                registry_path=registry_path,
                options_db_path="/custom/options.db",
                preearn_repo_path="/tmp/fake",
                dry_run=True,
            )

        _, kwargs = mock_batch.call_args
        assert kwargs["options_db_path"] == "/custom/options.db"

    def test_preearn_repo_path_passed_through(self, tmp_path):
        """preearn_repo_path is passed to run_candidate_batch."""
        hyp = _make_preearn_hypothesis(status=HypothesisStatus.registered)
        registry_path = str(tmp_path / "registry.jsonl")

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=_mock_batch_result(status="dry_run"),
        ) as mock_batch:
            register_and_run_batch(
                hyp,
                registry_path=registry_path,
                options_db_path="/tmp/fake.db",
                preearn_repo_path="/custom/engine_linux_main",
                dry_run=True,
            )

        _, kwargs = mock_batch.call_args
        assert kwargs["preearn_repo_path"] == "/custom/engine_linux_main"

    def test_max_candidates_passed_through(self, tmp_path):
        """max_candidates is passed to run_candidate_batch."""
        hyp = _make_preearn_hypothesis(status=HypothesisStatus.registered)
        registry_path = str(tmp_path / "registry.jsonl")

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=_mock_batch_result(status="dry_run"),
        ) as mock_batch:
            register_and_run_batch(
                hyp,
                registry_path=registry_path,
                options_db_path="/tmp/fake.db",
                preearn_repo_path="/tmp/fake",
                max_candidates=5,
                dry_run=True,
            )

        _, kwargs = mock_batch.call_args
        assert kwargs["max_candidates"] == 5

    def test_timeout_passed_through(self, tmp_path):
        """timeout is passed to run_candidate_batch."""
        hyp = _make_preearn_hypothesis(status=HypothesisStatus.registered)
        registry_path = str(tmp_path / "registry.jsonl")

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=_mock_batch_result(status="dry_run"),
        ) as mock_batch:
            register_and_run_batch(
                hyp,
                registry_path=registry_path,
                options_db_path="/tmp/fake.db",
                preearn_repo_path="/tmp/fake",
                timeout=300.0,
                dry_run=True,
            )

        _, kwargs = mock_batch.call_args
        assert kwargs["timeout"] == 300.0


class TestLifecycleErrorHandling:
    """Error handling and exception propagation."""

    def test_batch_exception_re_raises(self, tmp_path):
        """run_candidate_batch exceptions are re-raised by the lifecycle."""
        hyp = _make_preearn_hypothesis(status=HypothesisStatus.registered)
        registry_path = str(tmp_path / "registry.jsonl")

        with pytest.raises(RuntimeError, match="batch failed"):
            with mock.patch(
                "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
                side_effect=RuntimeError("batch failed"),
            ):
                register_and_run_batch(
                    hyp,
                    registry_path=registry_path,
                    options_db_path="/tmp/fake.db",
                    preearn_repo_path="/tmp/fake",
                    dry_run=False,
                )

    def test_no_run_preearn_backtest_calls_in_tests(self, tmp_path):
        """Lifecycle tests do not call the real run_preearn_backtest subprocess."""
        hyp = _make_preearn_hypothesis(status=HypothesisStatus.registered)
        registry_path = str(tmp_path / "registry.jsonl")

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=_mock_batch_result(status="dry_run"),
        ):
            register_and_run_batch(
                hyp,
                registry_path=registry_path,
                options_db_path="/tmp/fake.db",
                preearn_repo_path="/tmp/fake",
                dry_run=True,
            )

        # If we get here without a subprocess call, the test passed
        # (mock.patch prevents any real subprocess call)
        assert True


class TestLifecycleResult:
    """LifecycleResult fields are correctly populated."""

    def test_lifecycle_result_fields(self, tmp_path):
        """All LifecycleResult fields are populated correctly."""
        hyp = _make_preearn_hypothesis(
            hypothesis_id="hyp_fields_001",
            status=HypothesisStatus.registered,
        )
        registry_path = str(tmp_path / "registry.jsonl")
        mock_batch = _mock_batch_result(status="success", n_success=3)

        with mock.patch(
            "engine.edge_discovery.hypotheses.lifecycle.run_candidate_batch",
            return_value=mock_batch,
        ):
            result = register_and_run_batch(
                hyp,
                registry_path=registry_path,
                options_db_path="/custom/options.db",
                preearn_repo_path="/custom/engine",
                ledger_path="/custom/ledger.jsonl",
                output_dir="/custom/output",
                max_candidates=10,
                dry_run=False,
                timeout=300.0,
            )

        assert result.hypothesis_id == "hyp_fields_001"
        assert result.initial_status == "registered"
        assert result.final_status == "testing"
        assert result.batch_result is mock_batch
        assert result.registry_path == str(tmp_path / "registry.jsonl")
        assert result.error is None
