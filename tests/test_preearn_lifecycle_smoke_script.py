"""Tests for the lifecycle smoke script."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from engine.edge_discovery.evaluation import EvaluationLabel, EvaluationResult
from engine.edge_discovery.hypotheses.batch import BatchResult
from engine.edge_discovery.hypotheses.lifecycle import LifecycleResult
from engine.edge_discovery.hypotheses.spec import (
    AssetClass,
    HypothesisSpec,
    ParameterConstraint,
    SourceType,
    StrategyFamily,
    ValidationPlan,
)
from scripts.local.smoke_preearn_lifecycle import main as lifecycle_main, parse_args


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(hypothesis_id: str = "preearn-iv-ramp-basic-v1") -> HypothesisSpec:
    """Create a minimal HypothesisSpec for smoke tests."""
    return HypothesisSpec(
        hypothesis_id=hypothesis_id,
        version="1.0.0",
        source_type=SourceType.internal_research,
        source_reference="smoke_test",
        market_mechanism="iv_ramp",
        expected_effect="test",
        asset_class=AssetClass.equity_options,
        strategy_family=StrategyFamily.preearn_options,
        required_data=("options_db", "preearn_repo"),
        candidate_constraints=(
            ParameterConstraint(name="entry_dpe", values=(2,)),
            ParameterConstraint(name="delta_target", values=(0.5,)),
            ParameterConstraint(name="expiry_rank", values=(0,)),
        ),
        validation_plan=ValidationPlan(methods=("cpcv",), holdout_required=False),
        failure_modes=(),
        kill_criteria=(),
    )


def _make_batch_result(
    hypothesis_id: str = "preearn-iv-ramp-basic-v1",
    status: str = "dry_run",
    n_gen: int = 1,
    n_sel: int = 1,
    n_suc: int = 0,
    n_err: int = 0,
) -> BatchResult:
    """Create a minimal BatchResult for smoke tests."""
    return BatchResult(
        batch_id="batch_test_001",
        hypothesis_id=hypothesis_id,
        status=status,
        n_candidates_generated=n_gen,
        n_candidates_selected=n_sel,
        n_success=n_suc,
        n_error=n_err,
        results=[],
        output_artifacts={},
        error=None,
        started_at="2025-01-01T00:00:00Z",
        completed_at="2025-01-01T00:00:01Z",
    )


def _make_evaluation(
    label: EvaluationLabel = EvaluationLabel.NEEDS_MORE_DATA,
    reason: str = "Dry-run — no real execution.",
) -> EvaluationResult:
    """Create a minimal EvaluationResult for smoke tests."""
    return EvaluationResult(
        label=label,
        reason=reason,
        metrics={"n_success": 0, "n_error": 0, "n_selected": 1},
        warnings=(),
        source_type="batch_result",
        source_id="batch_test_001",
        hypothesis_id="preearn-iv-ramp-basic-v1",
    )


# ---------------------------------------------------------------------------
# Test argument parsing
# ---------------------------------------------------------------------------

class TestArgumentParsing:
    def test_example_required_choices_basic_and_coarse(self):
        args = parse_args([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-path", "/fake/db",
        ])
        assert args.example == "basic"

        args2 = parse_args([
            "--example", "coarse",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-path", "/fake/db",
        ])
        assert args2.example == "coarse"

    def test_unknown_example_raises(self):
        with pytest.raises(SystemExit):
            parse_args([
                "--example", "nonexistent",
                "--preearn-repo-path", "/fake/repo",
                "--options-db-path", "/fake/db",
            ])

    def test_defaults_are_dry_run_and_output_dir(self):
        args = parse_args([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-path", "/fake/db",
        ])
        assert args.dry_run is True
        assert args.output_dir == ".wfa/preearn_lifecycle_smoke"

    def test_real_run_sets_dry_run_false(self):
        args = parse_args([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-path", "/fake/db",
            "--real-run",
        ])
        assert args.dry_run is False

    def test_timeout_default_is_60(self):
        args = parse_args([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-path", "/fake/db",
        ])
        assert args.timeout == 60.0

    def test_custom_registry_and_ledger_paths(self, tmp_path):
        reg = str(tmp_path / "registry.jsonl")
        led = str(tmp_path / "ledger.jsonl")
        args = parse_args([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-path", "/fake/db",
            "--registry-path", reg,
            "--ledger-path", led,
        ])
        assert args.registry_path == reg
        assert args.ledger_path == led


# ---------------------------------------------------------------------------
# Test dry-run flow with mocks
# ---------------------------------------------------------------------------

class TestDryRunFlow:
    @mock.patch("scripts.local.smoke_preearn_lifecycle.evaluate_batch_result")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.register_and_run_batch")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.load_preearn_example")
    def test_loads_example_and_calls_register_and_run_batch(
        self, mock_load, mock_register, mock_eval, capsys
    ):
        spec = _make_spec()
        mock_load.return_value = spec
        mock_batch = _make_batch_result()
        mock_lifecycle = LifecycleResult(
            hypothesis_id=spec.hypothesis_id,
            initial_status="draft",
            final_status="registered",
            batch_result=mock_batch,
            registry_path="/tmp/fake_registry.jsonl",
            error=None,
        )
        mock_register.return_value = mock_lifecycle
        mock_eval.return_value = _make_evaluation()

        ret = lifecycle_main([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-path", "/fake/db",
            "--output-dir", "/tmp/fake_output",
            "--dry-run",
        ])

        assert ret == 0
        mock_load.assert_called_once_with("basic")
        mock_register.assert_called_once()
        call_kwargs = mock_register.call_args.kwargs
        assert call_kwargs["hypothesis"] == spec
        assert call_kwargs["dry_run"] is True
        assert call_kwargs["options_db_path"] == "/fake/db"
        assert call_kwargs["preearn_repo_path"] == "/fake/repo"

    @mock.patch("scripts.local.smoke_preearn_lifecycle.evaluate_batch_result")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.register_and_run_batch")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.load_preearn_example")
    def test_evaluate_batch_result_called_with_lifecycle_batch_result(
        self, mock_load, mock_register, mock_eval, capsys
    ):
        spec = _make_spec()
        mock_load.return_value = spec
        mock_batch = _make_batch_result()
        mock_lifecycle = LifecycleResult(
            hypothesis_id=spec.hypothesis_id,
            initial_status="draft",
            final_status="registered",
            batch_result=mock_batch,
            registry_path="/tmp/fake",
            error=None,
        )
        mock_register.return_value = mock_lifecycle
        mock_eval.return_value = _make_evaluation()

        lifecycle_main([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-path", "/fake/db",
        ])

        mock_eval.assert_called_once_with(mock_batch)

    @mock.patch("scripts.local.smoke_preearn_lifecycle.evaluate_batch_result")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.register_and_run_batch")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.load_preearn_example")
    def test_output_contains_lifecycle_fields(
        self, mock_load, mock_register, mock_eval, capsys
    ):
        spec = _make_spec("preearn-iv-ramp-basic-v1")
        mock_load.return_value = spec
        mock_batch = _make_batch_result(hypothesis_id="preearn-iv-ramp-basic-v1")
        mock_lifecycle = LifecycleResult(
            hypothesis_id="preearn-iv-ramp-basic-v1",
            initial_status="draft",
            final_status="registered",
            batch_result=mock_batch,
            registry_path="/tmp/fake_registry.jsonl",
            error=None,
        )
        mock_register.return_value = mock_lifecycle
        mock_eval.return_value = _make_evaluation()

        lifecycle_main([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-path", "/fake/db",
        ])

        out = capsys.readouterr().out
        assert "preearn-iv-ramp-basic-v1" in out
        assert "initial_status:" in out
        assert "final_status:" in out
        assert "registered" in out

    @mock.patch("scripts.local.smoke_preearn_lifecycle.evaluate_batch_result")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.register_and_run_batch")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.load_preearn_example")
    def test_output_contains_evaluation_fields(
        self, mock_load, mock_register, mock_eval, capsys
    ):
        spec = _make_spec()
        mock_load.return_value = spec
        mock_batch = _make_batch_result()
        mock_lifecycle = LifecycleResult(
            hypothesis_id=spec.hypothesis_id,
            initial_status="draft",
            final_status="registered",
            batch_result=mock_batch,
            registry_path="/tmp/fake",
            error=None,
        )
        mock_register.return_value = mock_lifecycle
        mock_eval.return_value = _make_evaluation(
            EvaluationLabel.NEEDS_MORE_DATA,
            "Dry-run — no real execution.",
        )

        lifecycle_main([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-path", "/fake/db",
        ])

        out = capsys.readouterr().out
        assert "evaluation_label:" in out
        assert "needs_more_data" in out
        assert "evaluation_reason:" in out
        assert "Dry-run" in out

    @mock.patch("scripts.local.smoke_preearn_lifecycle.evaluate_batch_result")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.register_and_run_batch")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.load_preearn_example")
    def test_evaluation_warnings_printed_as_list(
        self, mock_load, mock_register, mock_eval, capsys
    ):
        spec = _make_spec()
        mock_load.return_value = spec
        mock_batch = _make_batch_result()
        mock_lifecycle = LifecycleResult(
            hypothesis_id=spec.hypothesis_id,
            initial_status="draft",
            final_status="registered",
            batch_result=mock_batch,
            registry_path="/tmp/fake",
            error=None,
        )
        mock_register.return_value = mock_lifecycle
        mock_eval.return_value = EvaluationResult(
            label=EvaluationLabel.NEEDS_MORE_DATA,
            reason="Dry-run.",
            metrics={},
            warnings=(),
            source_type="batch_result",
            source_id="batch_001",
            hypothesis_id=spec.hypothesis_id,
        )

        lifecycle_main([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-path", "/fake/db",
        ])

        out = capsys.readouterr().out
        assert "evaluation_warnings:" in out
        assert "[]" in out  # empty tuple serialises to []


# ---------------------------------------------------------------------------
# Test no-batch-result path
# ---------------------------------------------------------------------------

class TestNoBatchResult:
    @mock.patch("scripts.local.smoke_preearn_lifecycle.evaluate_batch_result")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.register_and_run_batch")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.load_preearn_example")
    def test_no_batch_result_skips_evaluation(
        self, mock_load, mock_register, mock_eval, capsys
    ):
        spec = _make_spec()
        mock_load.return_value = spec
        mock_lifecycle = LifecycleResult(
            hypothesis_id=spec.hypothesis_id,
            initial_status="draft",
            final_status="registered",
            batch_result=None,
            registry_path="/tmp/fake",
            error=None,
        )
        mock_register.return_value = mock_lifecycle

        lifecycle_main([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-path", "/fake/db",
        ])

        mock_eval.assert_not_called()
        out = capsys.readouterr().out
        assert "evaluation_label: none" in out
        assert "no_batch_result" in out


# ---------------------------------------------------------------------------
# Test --real-run safety
# ---------------------------------------------------------------------------

class TestRealRunSafety:
    @mock.patch("scripts.local.smoke_preearn_lifecycle.evaluate_batch_result")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.register_and_run_batch")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.load_preearn_example")
    def test_real_run_sets_dry_run_false_and_passes_to_register(
        self, mock_load, mock_register, mock_eval, capsys
    ):
        spec = _make_spec()
        mock_load.return_value = spec
        mock_batch = _make_batch_result(status="success", n_suc=1)
        mock_lifecycle = LifecycleResult(
            hypothesis_id=spec.hypothesis_id,
            initial_status="registered",
            final_status="testing",
            batch_result=mock_batch,
            registry_path="/tmp/fake",
            error=None,
        )
        mock_register.return_value = mock_lifecycle
        mock_eval.return_value = _make_evaluation(EvaluationLabel.PROMISING, "Looks good.")

        lifecycle_main([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-path", "/fake/db",
            "--real-run",
        ])

        call_kwargs = mock_register.call_args.kwargs
        assert call_kwargs["dry_run"] is False

    @mock.patch("scripts.local.smoke_preearn_lifecycle.evaluate_batch_result")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.register_and_run_batch")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.load_preearn_example")
    def test_real_run_prints_warning(
        self, mock_load, mock_register, mock_eval, capsys
    ):
        spec = _make_spec()
        mock_load.return_value = spec
        mock_batch = _make_batch_result()
        mock_lifecycle = LifecycleResult(
            hypothesis_id=spec.hypothesis_id,
            initial_status="draft",
            final_status="registered",
            batch_result=mock_batch,
            registry_path="/tmp/fake",
            error=None,
        )
        mock_register.return_value = mock_lifecycle
        mock_eval.return_value = _make_evaluation()

        lifecycle_main([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-path", "/fake/db",
            "--real-run",
        ])

        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "--real-run" in err


# ---------------------------------------------------------------------------
# Test --options-db-manifest and --preearn-repo-manifest
# ---------------------------------------------------------------------------

_EXAMPLES_DIR = (
    Path(__file__).resolve().parents[1] / "examples" / "data_manifests"
)


class TestManifestPathResolution:
    def test_options_db_manifest_arg_is_accepted(self):
        manifest = str(_EXAMPLES_DIR / "preearn_options_2021_local.json")
        args = parse_args([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-manifest", manifest,
        ])
        assert args.options_db_manifest == manifest
        assert args.options_db_path is None

    def test_preearn_repo_manifest_arg_is_accepted(self):
        manifest = str(_EXAMPLES_DIR / "preearn_repo_local.json")
        args = parse_args([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-path", "/fake/db",
            "--preearn-repo-manifest", manifest,
        ])
        assert args.preearn_repo_manifest == manifest
        assert args.preearn_repo_path == "/fake/repo"

    @mock.patch("scripts.local.smoke_preearn_lifecycle.evaluate_batch_result")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.register_and_run_batch")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.load_preearn_example")
    def test_manifest_options_db_path_resolved_to_register(
        self, mock_load, mock_register, mock_eval, capsys
    ):
        spec = _make_spec()
        mock_load.return_value = spec
        mock_batch = _make_batch_result()
        mock_lifecycle = LifecycleResult(
            hypothesis_id=spec.hypothesis_id,
            initial_status="draft",
            final_status="registered",
            batch_result=mock_batch,
            registry_path="/tmp/fake",
            error=None,
        )
        mock_register.return_value = mock_lifecycle
        mock_eval.return_value = _make_evaluation()

        manifest = str(_EXAMPLES_DIR / "preearn_options_2021_local.json")
        lifecycle_main([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-manifest", manifest,
            "--dry-run",
        ])

        call_kwargs = mock_register.call_args.kwargs
        # Manifest declares a path; resolve_path_from_manifest extracts it
        assert call_kwargs["options_db_path"] is not None
        assert isinstance(call_kwargs["options_db_path"], str)

    @mock.patch("scripts.local.smoke_preearn_lifecycle.evaluate_batch_result")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.register_and_run_batch")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.load_preearn_example")
    def test_manifest_preearn_repo_path_resolved_to_register(
        self, mock_load, mock_register, mock_eval, capsys
    ):
        spec = _make_spec()
        mock_load.return_value = spec
        mock_batch = _make_batch_result()
        mock_lifecycle = LifecycleResult(
            hypothesis_id=spec.hypothesis_id,
            initial_status="draft",
            final_status="registered",
            batch_result=mock_batch,
            registry_path="/tmp/fake",
            error=None,
        )
        mock_register.return_value = mock_lifecycle
        mock_eval.return_value = _make_evaluation()

        manifest = str(_EXAMPLES_DIR / "preearn_repo_local.json")
        lifecycle_main([
            "--example", "basic",
            "--preearn-repo-manifest", manifest,
            "--options-db-path", "/fake/db",
            "--dry-run",
        ])

        call_kwargs = mock_register.call_args.kwargs
        assert call_kwargs["preearn_repo_path"] is not None
        assert isinstance(call_kwargs["preearn_repo_path"], str)

    @mock.patch("scripts.local.smoke_preearn_lifecycle.evaluate_batch_result")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.register_and_run_batch")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.load_preearn_example")
    def test_explicit_path_overrides_manifest(
        self, mock_load, mock_register, mock_eval, capsys
    ):
        spec = _make_spec()
        mock_load.return_value = spec
        mock_batch = _make_batch_result()
        mock_lifecycle = LifecycleResult(
            hypothesis_id=spec.hypothesis_id,
            initial_status="draft",
            final_status="registered",
            batch_result=mock_batch,
            registry_path="/tmp/fake",
            error=None,
        )
        mock_register.return_value = mock_lifecycle
        mock_eval.return_value = _make_evaluation()

        manifest = str(_EXAMPLES_DIR / "preearn_options_2021_local.json")
        lifecycle_main([
            "--example", "basic",
            "--preearn-repo-path", "/fake/repo",
            "--options-db-path", "/my/explicit/db.sqlite",
            "--options-db-manifest", manifest,
            "--dry-run",
        ])

        call_kwargs = mock_register.call_args.kwargs
        assert call_kwargs["options_db_path"] == "/my/explicit/db.sqlite"

    @mock.patch("scripts.local.smoke_preearn_lifecycle.evaluate_batch_result")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.register_and_run_batch")
    @mock.patch("scripts.local.smoke_preearn_lifecycle.load_preearn_example")
    def test_wrong_role_manifest_exits_with_error(
        self, mock_load, mock_register, mock_eval, capsys
    ):
        spec = _make_spec()
        mock_load.return_value = spec
        mock_batch = _make_batch_result()
        mock_lifecycle = LifecycleResult(
            hypothesis_id=spec.hypothesis_id,
            initial_status="draft",
            final_status="registered",
            batch_result=mock_batch,
            registry_path="/tmp/fake",
            error=None,
        )
        mock_register.return_value = mock_lifecycle
        mock_eval.return_value = _make_evaluation()

        # preearn_options manifest has role options_backtest_db, not preearn_repo
        manifest = str(_EXAMPLES_DIR / "preearn_options_2021_local.json")
        # Omit --preearn-repo-path so the manifest path resolution is triggered
        with pytest.raises(SystemExit):
            lifecycle_main([
                "--example", "basic",
                "--options-db-path", "/fake/db",
                "--preearn-repo-manifest", manifest,
                "--dry-run",
            ])


# ---------------------------------------------------------------------------
# Test no subprocess calls (generate_candidates is pure — no mock needed)
# ---------------------------------------------------------------------------
# register_and_run_batch is mocked, so run_candidate_batch is never called.
# load_preearn_example is file-only read, no subprocess.
# No assertions needed — test isolation via mocking ensures no subprocess.
