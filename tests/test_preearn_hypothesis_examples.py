"""Tests for pre-earnings HypothesisSpec example fixtures."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from engine.edge_discovery.hypotheses import (
    AssetClass,
    HypothesisSpec,
    StrategyFamily,
)
from engine.edge_discovery.hypotheses.generate import generate_candidates


EXAMPLES_DIR = Path("examples/preearn_hypotheses")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_example(name: str) -> dict:
    path = EXAMPLES_DIR / name
    with path.open() as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Test: loading
# ---------------------------------------------------------------------------


class TestExampleLoading:
    def test_basic_example_loads_as_hypothesis_spec(self):
        data = _load_example("basic_preearn_dpe2_delta50.json")
        spec = HypothesisSpec.from_dict(data)
        assert spec.hypothesis_id == "preearn-iv-ramp-basic-v1"

    def test_coarse_grid_example_loads_as_hypothesis_spec(self):
        data = _load_example("coarse_grid_preearn.json")
        spec = HypothesisSpec.from_dict(data)
        assert spec.hypothesis_id == "preearn-delta-grid-basic-v1"


# ---------------------------------------------------------------------------
# Test: roundtrip
# ---------------------------------------------------------------------------


class TestRoundtrip:
    def test_basic_example_roundtrip(self):
        data = _load_example("basic_preearn_dpe2_delta50.json")
        spec = HypothesisSpec.from_dict(data)
        recon = HypothesisSpec.from_dict(spec.to_dict())
        assert recon.hypothesis_id == spec.hypothesis_id
        assert recon.version == spec.version
        assert recon.source_type == spec.source_type
        assert recon.asset_class == spec.asset_class
        assert recon.strategy_family == spec.strategy_family
        assert recon.required_data == spec.required_data
        assert len(recon.candidate_constraints) == len(spec.candidate_constraints)

    def test_coarse_grid_example_roundtrip(self):
        data = _load_example("coarse_grid_preearn.json")
        spec = HypothesisSpec.from_dict(data)
        recon = HypothesisSpec.from_dict(spec.to_dict())
        assert recon.hypothesis_id == spec.hypothesis_id
        assert recon.version == spec.version
        assert recon.asset_class == spec.asset_class
        assert recon.strategy_family == spec.strategy_family
        assert len(recon.candidate_constraints) == len(spec.candidate_constraints)


# ---------------------------------------------------------------------------
# Test: candidate generation
# ---------------------------------------------------------------------------


class TestCandidateGeneration:
    def test_basic_example_produces_exactly_one_candidate(self):
        data = _load_example("basic_preearn_dpe2_delta50.json")
        spec = HypothesisSpec.from_dict(data)
        candidates = generate_candidates(
            spec,
            options_db_path="/tmp/fake.db",
            preearn_repo_path="/tmp/fake",
        )
        assert len(candidates) == 1

    def test_coarse_grid_example_produces_exactly_four_candidates(self):
        data = _load_example("coarse_grid_preearn.json")
        spec = HypothesisSpec.from_dict(data)
        candidates = generate_candidates(
            spec,
            options_db_path="/tmp/fake.db",
            preearn_repo_path="/tmp/fake",
        )
        assert len(candidates) == 4

    def test_basic_example_candidates_have_correct_constraint_values(self):
        data = _load_example("basic_preearn_dpe2_delta50.json")
        spec = HypothesisSpec.from_dict(data)
        candidates = generate_candidates(
            spec,
            options_db_path="/tmp/fake.db",
            preearn_repo_path="/tmp/fake",
        )
        assert len(candidates) == 1
        c = candidates[0]
        assert c.entry_dpe == 2
        assert c.delta_target == 0.5
        assert c.expiry_rank == 0

    def test_coarse_grid_candidates_have_correct_constraint_values(self):
        data = _load_example("coarse_grid_preearn.json")
        spec = HypothesisSpec.from_dict(data)
        candidates = generate_candidates(
            spec,
            options_db_path="/tmp/fake.db",
            preearn_repo_path="/tmp/fake",
        )
        assert len(candidates) == 4
        dpes = {c.entry_dpe for c in candidates}
        deltas = {c.delta_target for c in candidates}
        assert dpes == {2, 3}
        assert deltas == {0.3, 0.5}


# ---------------------------------------------------------------------------
# Test: schema compliance
# ---------------------------------------------------------------------------


class TestSchemaCompliance:
    @pytest.mark.parametrize("example", [
        "basic_preearn_dpe2_delta50.json",
        "coarse_grid_preearn.json",
    ])
    def test_strategy_family_is_preearn_options(self, example):
        data = _load_example(example)
        spec = HypothesisSpec.from_dict(data)
        assert spec.strategy_family == StrategyFamily.preearn_options

    @pytest.mark.parametrize("example", [
        "basic_preearn_dpe2_delta50.json",
        "coarse_grid_preearn.json",
    ])
    def test_asset_class_is_equity_options(self, example):
        data = _load_example(example)
        spec = HypothesisSpec.from_dict(data)
        assert spec.asset_class == AssetClass.equity_options

    @pytest.mark.parametrize("example", [
        "basic_preearn_dpe2_delta50.json",
        "coarse_grid_preearn.json",
    ])
    def test_required_data_contains_options_db_and_preearn_repo(self, example):
        data = _load_example(example)
        spec = HypothesisSpec.from_dict(data)
        assert "options_db" in spec.required_data
        assert "preearn_repo" in spec.required_data

    @pytest.mark.parametrize("example", [
        "basic_preearn_dpe2_delta50.json",
        "coarse_grid_preearn.json",
    ])
    def test_status_is_draft(self, example):
        data = _load_example(example)
        spec = HypothesisSpec.from_dict(data)
        assert spec.status.value == "draft"

    @pytest.mark.parametrize("example", [
        "basic_preearn_dpe2_delta50.json",
        "coarse_grid_preearn.json",
    ])
    def test_validation_plan_has_cpcv(self, example):
        data = _load_example(example)
        spec = HypothesisSpec.from_dict(data)
        assert "cpcv" in spec.validation_plan.methods

    @pytest.mark.parametrize("example", [
        "basic_preearn_dpe2_delta50.json",
        "coarse_grid_preearn.json",
    ])
    def test_notes_say_fixture(self, example):
        data = _load_example(example)
        spec = HypothesisSpec.from_dict(data)
        assert "fixture" in spec.notes.lower() or "example" in spec.notes.lower()


# ---------------------------------------------------------------------------
# Test: directory contents
# ---------------------------------------------------------------------------


class TestDirectoryContents:
    def test_examples_directory_contains_exactly_two_json_files(self):
        json_files = list(EXAMPLES_DIR.glob("*.json"))
        assert len(json_files) == 2

    def test_example_files_have_expected_names(self):
        names = {p.name for p in EXAMPLES_DIR.glob("*.json")}
        assert names == {"basic_preearn_dpe2_delta50.json", "coarse_grid_preearn.json"}


# ---------------------------------------------------------------------------
# Test: no subprocess calls
# ---------------------------------------------------------------------------


class TestNoSubprocess:
    """Verify generate_candidates is a pure function with no I/O or subprocess calls."""

    def test_generate_candidates_is_pure_no_db_io(self):
        """generate_candidates does not open or validate the options DB path."""
        data = _load_example("basic_preearn_dpe2_delta50.json")
        spec = HypothesisSpec.from_dict(data)
        # If generate_candidates tried to open/validate the DB path,
        # this would fail with the fake path. It doesn't — it's pure.
        candidates = generate_candidates(
            spec,
            options_db_path="/tmp/nonexistent_fake_path_12345.db",
            preearn_repo_path="/tmp/nonexistent_fake_path_12345",
        )
        assert len(candidates) == 1

    def test_no_run_preearn_backtest_in_generate_module(self):
        """Verify run_preearn_backtest is not imported in generate.py."""
        import engine.edge_discovery.hypotheses.generate as gen_module
        assert not hasattr(gen_module, "run_preearn_backtest")
        assert not hasattr(gen_module, "run_candidate_batch")
