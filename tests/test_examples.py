"""Tests for the pre-earnings example loader."""

from __future__ import annotations

import pytest

from engine.edge_discovery.examples import (
    list_preearn_examples,
    load_preearn_example,
    preearn_example_path,
)
from engine.edge_discovery.hypotheses import HypothesisSpec
from engine.edge_discovery.hypotheses.generate import generate_candidates

# Convenience — fake paths for generate_candidates (pure function, no I/O needed).
_FAKE_OPTIONS_DB = "/tmp/fake_options.sqlite"
_FAKE_PREEARN_REPO = "/tmp/fake_preearn_repo"


class TestListExamples:
    def test_returns_tuple(self):
        result = list_preearn_examples()
        assert isinstance(result, tuple)

    def test_contains_basic_and_coarse(self):
        result = list_preearn_examples()
        assert "basic" in result
        assert "coarse" in result

    def test_is_sorted(self):
        result = list_preearn_examples()
        assert list(result) == sorted(result)


class TestPreearnExamplePath:
    def test_basic_path_exists(self):
        path = preearn_example_path("basic")
        assert path.exists()
        assert path.is_file()
        assert path.suffix == ".json"

    def test_coarse_path_exists(self):
        path = preearn_example_path("coarse")
        assert path.exists()
        assert path.is_file()
        assert path.suffix == ".json"


class TestLoadBasic:
    def test_returns_hypothesis_spec(self):
        spec = load_preearn_example("basic")
        assert isinstance(spec, HypothesisSpec)

    def test_hypothesis_id(self):
        spec = load_preearn_example("basic")
        assert spec.hypothesis_id == "preearn-iv-ramp-basic-v1"

    def test_roundtrip_to_dict_from_dict(self):
        spec = load_preearn_example("basic")
        reloaded = HypothesisSpec.from_dict(spec.to_dict())
        assert reloaded.hypothesis_id == spec.hypothesis_id
        assert reloaded.asset_class == spec.asset_class
        assert reloaded.strategy_family == spec.strategy_family

    def test_generate_candidates_produces_1(self):
        spec = load_preearn_example("basic")
        candidates = generate_candidates(
            spec,
            options_db_path=_FAKE_OPTIONS_DB,
            preearn_repo_path=_FAKE_PREEARN_REPO,
        )
        assert len(candidates) == 1

    def test_strategy_family_is_preearn_options(self):
        from engine.edge_discovery.hypotheses.spec import StrategyFamily
        spec = load_preearn_example("basic")
        assert spec.strategy_family == StrategyFamily.preearn_options


class TestLoadCoarse:
    def test_returns_hypothesis_spec(self):
        spec = load_preearn_example("coarse")
        assert isinstance(spec, HypothesisSpec)

    def test_hypothesis_id(self):
        spec = load_preearn_example("coarse")
        assert spec.hypothesis_id == "preearn-delta-grid-basic-v1"

    def test_roundtrip_to_dict_from_dict(self):
        spec = load_preearn_example("coarse")
        reloaded = HypothesisSpec.from_dict(spec.to_dict())
        assert reloaded.hypothesis_id == spec.hypothesis_id
        assert reloaded.asset_class == spec.asset_class
        assert reloaded.strategy_family == spec.strategy_family

    def test_generate_candidates_produces_4(self):
        spec = load_preearn_example("coarse")
        candidates = generate_candidates(
            spec,
            options_db_path=_FAKE_OPTIONS_DB,
            preearn_repo_path=_FAKE_PREEARN_REPO,
        )
        assert len(candidates) == 4

    def test_strategy_family_is_preearn_options(self):
        from engine.edge_discovery.hypotheses.spec import StrategyFamily
        spec = load_preearn_example("coarse")
        assert spec.strategy_family == StrategyFamily.preearn_options


class TestErrorHandling:
    def test_unknown_name_raises_valueerror(self):
        with pytest.raises(ValueError) as exc_info:
            load_preearn_example("nonexistent")
        assert "nonexistent" in str(exc_info.value)

    def test_valueerror_lists_available_names(self):
        with pytest.raises(ValueError) as exc_info:
            load_preearn_example("unknown_example")
        msg = str(exc_info.value)
        assert "basic" in msg
        assert "coarse" in msg


# generate_candidates is pure — no subprocess, no I/O, no adapter calls.
