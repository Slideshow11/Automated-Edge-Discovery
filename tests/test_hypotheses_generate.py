"""Tests for the HypothesisSpec -> CandidateSpec generator."""

from __future__ import annotations

from unittest import mock

import pytest

from engine.edge_discovery.adapters.preearn_options import CandidateSpec
from engine.edge_discovery.hypotheses import (
    HypothesisSpec,
    StrategyFamily,
    generate_candidates,
)
from engine.edge_discovery.hypotheses.spec import (
    AssetClass,
    HypothesisStatus,
    KillCriterion,
    ParameterConstraint,
    SourceType,
    ValidationPlan,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _preearn_hyp(
    hypothesis_id: str = "test-hyp-v1",
    constraints: tuple[ParameterConstraint, ...] | None = None,
    **overrides,
) -> HypothesisSpec:
    if constraints is None:
        constraints = (
            ParameterConstraint(name="entry_dpe", values=(2, 3)),
            ParameterConstraint(name="delta_target", values=(0.3, 0.5)),
            ParameterConstraint(name="expiry_rank", values=(0,)),
        )
    params = {
        "hypothesis_id": hypothesis_id,
        "version": "1.0.0",
        "source_type": SourceType.empirical_observation,
        "source_reference": "internal",
        "market_mechanism": "IV crush",
        "expected_effect": "Positive P&L from buying options pre-earnings",
        "asset_class": AssetClass.equity_options,
        "strategy_family": StrategyFamily.preearn_options,
        "required_data": ("options_db",),
        "candidate_constraints": constraints,
        "validation_plan": ValidationPlan(methods=("CPCV",)),
        "failure_modes": ("low_volume",),
        "kill_criteria": (
            KillCriterion(metric="sharpe", op="lt", threshold=0.5),
        ),
        "status": HypothesisStatus.registered,
    }
    params.update(overrides)
    return HypothesisSpec(**params)


# ---------------------------------------------------------------------------
# Basic generation
# ---------------------------------------------------------------------------


def test_generate_candidates_produces_correct_count():
    hyp = _preearn_hyp(
        constraints=(
            ParameterConstraint(name="entry_dpe", values=(2, 3)),
            ParameterConstraint(name="delta_target", values=(0.3, 0.5)),
            ParameterConstraint(name="expiry_rank", values=(0,)),
        )
    )
    result = generate_candidates(
        hyp,
        options_db_path="/db",
        preearn_repo_path="/repo",
    )
    # 2 entry_dpe * 2 delta * 1 rank = 4
    assert len(result) == 4


def test_generate_candidates_returns_tuple():
    hyp = _preearn_hyp()
    result = generate_candidates(hyp, options_db_path="/db", preearn_repo_path="/repo")
    assert isinstance(result, tuple)


def test_generate_candidates_all_are_candidatespec():
    hyp = _preearn_hyp()
    result = generate_candidates(hyp, options_db_path="/db", preearn_repo_path="/repo")
    assert all(isinstance(c, CandidateSpec) for c in result)


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_generate_candidates_stable_ordering():
    hyp = _preearn_hyp(
        constraints=(
            ParameterConstraint(name="entry_dpe", values=(2, 3)),
            ParameterConstraint(name="delta_target", values=(0.3, 0.5)),
            ParameterConstraint(name="expiry_rank", values=(0, 1)),
        )
    )
    result = generate_candidates(hyp, options_db_path="/db", preearn_repo_path="/repo")
    # entry_dpe=(2,3), delta=(0.3,0.5), rank=(0,1) → product order is:
    # (2, 0.3, 0), (2, 0.3, 1), (2, 0.5, 0), (2, 0.5, 1), (3, 0.3, 0), ...
    assert result[0].entry_dpe == 2
    assert result[0].delta_target == 0.3
    assert result[0].expiry_rank == 0
    assert result[-1].entry_dpe == 3
    assert result[-1].delta_target == 0.5
    assert result[-1].expiry_rank == 1
    # Confirm sorted by (entry_dpe, delta_target, expiry_rank)
    for i in range(len(result) - 1):
        a, b = result[i], result[i + 1]
        assert (a.entry_dpe, a.delta_target, a.expiry_rank) <= (
            b.entry_dpe, b.delta_target, b.expiry_rank
        ), f"Out of order at index {i}: {a} then {b}"


# ---------------------------------------------------------------------------
# Adapter defaults
# ---------------------------------------------------------------------------


def test_generate_candidates_adapter_defaults_applied():
    hyp = _preearn_hyp()
    result = generate_candidates(
        hyp,
        options_db_path="/db/options.sqlite",
        preearn_repo_path="/repo/engine",
        fill_policy="CROSS",
        spread_penalty_k=0.75,
        contract_multiplier=50.0,
        output_dir=".wfa/test",
    )
    for c in result:
        assert c.options_db_path == "/db/options.sqlite"
        assert c.preearn_repo_path == "/repo/engine"
        assert c.fill_policy == "CROSS"
        assert c.spread_penalty_k == 0.75
        assert c.contract_multiplier == 50.0
        assert c.output_dir == ".wfa/test"


def test_generate_candidates_defaults():
    hyp = _preearn_hyp()
    result = generate_candidates(hyp, options_db_path="/db", preearn_repo_path="/repo")
    assert all(c.fill_policy == "MID" for c in result)
    assert all(c.spread_penalty_k == 0.5 for c in result)
    assert all(c.contract_multiplier == 100.0 for c in result)
    assert all(c.output_dir == ".wfa/preearn" for c in result)


# ---------------------------------------------------------------------------
# Required constraint errors
# ---------------------------------------------------------------------------


def test_generate_candidates_missing_entry_dpe_raises():
    hyp = _preearn_hyp(
        constraints=(
            ParameterConstraint(name="delta_target", values=(0.3,)),
            ParameterConstraint(name="expiry_rank", values=(0,)),
        )
    )
    with pytest.raises(ValueError, match="entry_dpe"):
        generate_candidates(hyp, options_db_path="/db", preearn_repo_path="/repo")


def test_generate_candidates_missing_delta_target_raises():
    hyp = _preearn_hyp(
        constraints=(
            ParameterConstraint(name="entry_dpe", values=(2,)),
            ParameterConstraint(name="expiry_rank", values=(0,)),
        )
    )
    with pytest.raises(ValueError, match="delta_target"):
        generate_candidates(hyp, options_db_path="/db", preearn_repo_path="/repo")


def test_generate_candidates_missing_expiry_rank_raises():
    hyp = _preearn_hyp(
        constraints=(
            ParameterConstraint(name="entry_dpe", values=(2,)),
            ParameterConstraint(name="delta_target", values=(0.3,)),
        )
    )
    with pytest.raises(ValueError, match="expiry_rank"):
        generate_candidates(hyp, options_db_path="/db", preearn_repo_path="/repo")


# ---------------------------------------------------------------------------
# Strategy family
# ---------------------------------------------------------------------------


def test_generate_candidates_unsupported_strategy_family_raises():
    hyp = _preearn_hyp(
        strategy_family=StrategyFamily.momentum,
        hypothesis_id="mom-test",
    )
    with pytest.raises(ValueError, match="Unsupported strategy family"):
        generate_candidates(hyp, options_db_path="/db", preearn_repo_path="/repo")


# ---------------------------------------------------------------------------
# Range constraint error
# ---------------------------------------------------------------------------


def test_generate_candidates_range_constraint_without_values_raises():
    hyp = _preearn_hyp(
        constraints=(
            ParameterConstraint(name="entry_dpe", values=(2,)),
            ParameterConstraint(name="delta_target", min_value=0.3, max_value=0.5),
            ParameterConstraint(name="expiry_rank", values=(0,)),
        )
    )
    with pytest.raises(ValueError, match="explicit values"):
        generate_candidates(hyp, options_db_path="/db", preearn_repo_path="/repo")


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_generate_candidates_deterministic():
    hyp = _preearn_hyp()
    result1 = generate_candidates(hyp, options_db_path="/db", preearn_repo_path="/repo")
    result2 = generate_candidates(hyp, options_db_path="/db", preearn_repo_path="/repo")
    assert result1 == result2


# ---------------------------------------------------------------------------
# No execution — verify run/build functions are not called
# ---------------------------------------------------------------------------


def test_generate_candidates_no_subprocess_or_backtest_called():
    hyp = _preearn_hyp()

    with mock.patch("engine.edge_discovery.adapters.preearn_options.run_preearn_backtest") as mock_run:
        with mock.patch("engine.edge_discovery.adapters.preearn_options.build_command") as mock_build:
            mock_run.side_effect = AssertionError("run_preearn_backtest should not be called")
            mock_build.side_effect = AssertionError("build_command should not be called")

            generate_candidates(
                hyp,
                options_db_path="/db",
                preearn_repo_path="/repo",
            )

    # If we get here without exceptions, neither mock was called
    assert True
