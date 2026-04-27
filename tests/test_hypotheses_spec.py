"""Tests for the HypothesisSpec schema."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest
from engine.edge_discovery.hypotheses.spec import (
    AssetClass,
    HypothesisSpec,
    HypothesisStatus,
    KillCriterion,
    ParameterConstraint,
    SourceType,
    StrategyFamily,
    ValidationPlan,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_valid_spec(**overrides) -> HypothesisSpec:
    """Minimal valid HypothesisSpec for use as a base in override tests."""
    params = {
        "hypothesis_id": "preearn-dpe2-delta50-v1",
        "version": "1.0.0",
        "source_type": SourceType.empirical_observation,
        "source_reference": "internal",
        "market_mechanism": "IV crush",
        "expected_effect": "Positive P&L from buying options pre-earnings",
        "asset_class": AssetClass.equity_options,
        "strategy_family": StrategyFamily.preearn_options,
        "required_data": ("options_db",),
        "candidate_constraints": (
            ParameterConstraint(name="entry_dpe", values=(0, 1, 2)),
            ParameterConstraint(name="delta_target", min_value=0.3, max_value=0.7, step=0.1),
        ),
        "validation_plan": ValidationPlan(methods=("CPCV",)),
        "failure_modes": ("low_volume",),
        "kill_criteria": (
            KillCriterion(metric="sharpe", op="lt", threshold=0.5),
        ),
    }
    params.update(overrides)
    return HypothesisSpec(**params)


# ---------------------------------------------------------------------------
# Construction — valid
# ---------------------------------------------------------------------------


def test_construct_valid_hypothesis_spec():
    hyp = _make_valid_spec()
    assert hyp.hypothesis_id == "preearn-dpe2-delta50-v1"
    assert hyp.version == "1.0.0"
    assert hyp.status == HypothesisStatus.draft
    assert hyp.notes == ""


def test_construct_with_all_fields():
    hyp = HypothesisSpec(
        hypothesis_id="test-v1",
        version="1.0.0",
        source_type=SourceType.academic_paper,
        source_reference="https://doi.org/10.某",
        market_mechanism="delta hedging pressure",
        expected_effect="Skew mean-reversion after rebalance",
        asset_class=AssetClass.index_options,
        strategy_family=StrategyFamily.volatility,
        required_data=("options_db", "short_interest"),
        candidate_constraints=(
            ParameterConstraint(name="entry_dpe", values=(1, 2, 3)),
        ),
        validation_plan=ValidationPlan(
            methods=("CPCV", "walk_forward"),
            holdout_required=True,
            min_trades=100,
            min_years=2,
            notes="Use 2020 as holdout",
        ),
        failure_modes=("wide_spread", "low_volume"),
        kill_criteria=(
            KillCriterion(metric="sharpe", op="lt", threshold=1.0, reason="below hurdle"),
            KillCriterion(metric="max_drawdown", op="gt", threshold=-0.20),
        ),
        status=HypothesisStatus.registered,
        notes="Initial hypothesis",
    )
    assert hyp.status == HypothesisStatus.registered
    assert hyp.notes == "Initial hypothesis"
    assert hyp.validation_plan.min_trades == 100


# ---------------------------------------------------------------------------
# Construction — invalid
# ---------------------------------------------------------------------------


def test_invalid_empty_hypothesis_id():
    with pytest.raises(ValueError, match="hypothesis_id"):
        _make_valid_spec(hypothesis_id="")


def test_invalid_whitespace_hypothesis_id():
    with pytest.raises(ValueError, match="hypothesis_id"):
        _make_valid_spec(hypothesis_id="   ")


def test_invalid_empty_version():
    with pytest.raises(ValueError, match="version"):
        _make_valid_spec(version="")


def test_invalid_empty_market_mechanism():
    with pytest.raises(ValueError, match="market_mechanism"):
        _make_valid_spec(market_mechanism="")


def test_invalid_empty_expected_effect():
    with pytest.raises(ValueError, match="expected_effect"):
        _make_valid_spec(expected_effect="")


def test_invalid_empty_required_data():
    with pytest.raises(ValueError, match="required_data"):
        _make_valid_spec(required_data=())


def test_invalid_empty_candidate_constraints():
    with pytest.raises(ValueError, match="candidate_constraints"):
        _make_valid_spec(candidate_constraints=())


# ---------------------------------------------------------------------------
# ParameterConstraint — invalid
# ---------------------------------------------------------------------------


def test_parameter_constraint_no_values_no_bounds():
    with pytest.raises(ValueError, match="must have either"):
        ParameterConstraint(name="entry_dpe")


def test_parameter_constraint_min_gt_max():
    with pytest.raises(ValueError, match="min_value .* > max_value"):
        ParameterConstraint(name="entry_dpe", min_value=5, max_value=2)


# ---------------------------------------------------------------------------
# KillCriterion — invalid
# ---------------------------------------------------------------------------


def test_kill_criterion_invalid_op():
    with pytest.raises(ValueError, match="Invalid kill criterion op"):
        KillCriterion(metric="sharpe", op="not_an_op", threshold=0.5)


@pytest.mark.parametrize("op", ["lt", "lte", "gt", "gte", "eq", "neq"])
def test_kill_criterion_valid_ops(op):
    kc = KillCriterion(metric="sharpe", op=op, threshold=0.5)
    assert kc.op == op


# ---------------------------------------------------------------------------
# Serialization round-trips
# ---------------------------------------------------------------------------


def test_to_dict_from_dict_roundtrip():
    hyp = _make_valid_spec()
    recon = HypothesisSpec.from_dict(hyp.to_dict())
    assert recon.hypothesis_id == hyp.hypothesis_id
    assert recon.version == hyp.version
    assert recon.source_type == hyp.source_type
    assert recon.asset_class == hyp.asset_class
    assert recon.strategy_family == hyp.strategy_family
    assert recon.required_data == hyp.required_data
    assert recon.candidate_constraints == hyp.candidate_constraints
    assert recon.validation_plan == hyp.validation_plan
    assert recon.failure_modes == hyp.failure_modes
    assert recon.kill_criteria == hyp.kill_criteria
    assert recon.status == hyp.status
    assert recon.notes == hyp.notes


def test_to_json_from_json_roundtrip():
    hyp = _make_valid_spec()
    text = hyp.to_json()
    recon = HypothesisSpec.from_json(text)
    assert recon.hypothesis_id == hyp.hypothesis_id
    assert recon.to_json() == text  # canonical, so identical


def test_json_parseable_by_stdlib():
    hyp = _make_valid_spec()
    data = json.loads(hyp.to_json())
    assert data["hypothesis_id"] == hyp.hypothesis_id
    assert data["source_type"] == "empirical_observation"


# ---------------------------------------------------------------------------
# stable_hash
# ---------------------------------------------------------------------------


def test_stable_hash_identical_for_identical_objects():
    hyp1 = _make_valid_spec()
    hyp2 = HypothesisSpec.from_dict(hyp1.to_dict())
    assert hyp1.stable_hash() == hyp2.stable_hash()


def test_stable_hash_changes_when_field_changes():
    hyp1 = _make_valid_spec()
    hyp2 = _make_valid_spec(hypothesis_id="preearn-dpe3-delta50-v1")
    assert hyp1.stable_hash() != hyp2.stable_hash()


def test_stable_hash_format():
    hyp = _make_valid_spec()
    h = hyp.stable_hash()
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# Frozen dataclass
# ---------------------------------------------------------------------------


def test_hypothesis_spec_is_frozen():
    hyp = _make_valid_spec()
    with pytest.raises(FrozenInstanceError):
        hyp.hypothesis_id = "changed"


def test_parameter_constraint_is_frozen():
    pc = ParameterConstraint(name="entry_dpe", values=(0, 1, 2))
    with pytest.raises(FrozenInstanceError):
        pc.name = "changed"


def test_kill_criterion_is_frozen():
    kc = KillCriterion(metric="sharpe", op="lt", threshold=0.5)
    with pytest.raises(FrozenInstanceError):
        kc.reason = "changed"


def test_validation_plan_is_frozen():
    vp = ValidationPlan(methods=("CPCV",))
    with pytest.raises(FrozenInstanceError):
        vp.notes = "changed"


# ---------------------------------------------------------------------------
# to_dict / from_dict on supporting types
# ---------------------------------------------------------------------------


def test_parameter_constraint_to_dict_from_dict():
    pc = ParameterConstraint(name="delta_target", values=(0.3, 0.5, 0.7))
    recon = ParameterConstraint.from_dict(pc.to_dict())
    assert recon == pc


def test_kill_criterion_to_dict_from_dict():
    kc = KillCriterion(metric="sharpe", op="gte", threshold=1.5, reason="hurdle")
    recon = KillCriterion.from_dict(kc.to_dict())
    assert recon == kc


def test_validation_plan_to_dict_from_dict():
    vp = ValidationPlan(methods=("CPCV", "walk_forward"), holdout_required=False, min_trades=50)
    recon = ValidationPlan.from_dict(vp.to_dict())
    assert recon == vp


# ---------------------------------------------------------------------------
# Enum values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "EnumCls,expected",
    [
        (SourceType, ["academic_paper", "empirical_observation", "market_mechanic", "internal_research", "llm_generated"]),
        (AssetClass, ["equity", "equity_options", "index_options", "futures", "crypto", "macro"]),
        (StrategyFamily, ["preearn_options", "momentum", "mean_reversion", "news_sentiment", "volatility", "execution_cost"]),
        (HypothesisStatus, ["draft", "registered", "testing", "accepted", "rejected", "killed"]),
    ],
)
def test_enum_members(EnumCls, expected):
    assert [e.value for e in EnumCls] == expected
