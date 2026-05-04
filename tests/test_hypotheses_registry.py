"""Tests for the HypothesisRegistry."""

from __future__ import annotations

import json
import os
import shutil
import tempfile

import pytest

from engine.edge_discovery.hypotheses import HypothesisSpec
from engine.edge_discovery.hypotheses.registry import HypothesisRegistry
from engine.edge_discovery.hypotheses.spec import (
    AssetClass,
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


@pytest.fixture
def tmp_registry(tmp_path, monkeypatch):
    """Provide a HypothesisRegistry backed by a temp file, with PYTHONPATH set."""
    registry_path = tmp_path / "hypotheses.jsonl"
    # Patch get_config so the default path resolves to our temp file
    monkeypatch.setenv("EDGE_DISCOVERY_HYPOTHESIS_REGISTRY_PATH", str(registry_path))
    yield HypothesisRegistry(path=str(registry_path))
    # Clean up any .wfa/ created by tests
    wfa = tmp_path.parent / ".wfa"
    if wfa.exists():
        shutil.rmtree(wfa, ignore_errors=True)


def _make_spec(
    hypothesis_id: str = "test-hyp-v1",
    status: HypothesisStatus = HypothesisStatus.draft,
    **overrides,
) -> HypothesisSpec:
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
        "candidate_constraints": (
            ParameterConstraint(name="entry_dpe", values=(0, 1, 2)),
        ),
        "validation_plan": ValidationPlan(methods=("CPCV",)),
        "failure_modes": ("low_volume",),
        "kill_criteria": (
            KillCriterion(metric="sharpe", op="lt", threshold=0.5),
        ),
        "status": status,
    }
    params.update(overrides)
    return HypothesisSpec(**params)


# ---------------------------------------------------------------------------
# register + read_all
# ---------------------------------------------------------------------------


def test_register_writes_one_hypothesis(tmp_registry):
    hyp = _make_spec("hyp-a-v1")
    tmp_registry.register(hyp)
    results = tmp_registry.read_all()
    assert len(results) == 1
    assert results[0].hypothesis_id == "hyp-a-v1"


def test_read_all_loads_it_back(tmp_registry):
    hyp = _make_spec("hyp-b-v1")
    tmp_registry.register(hyp)
    loaded = tmp_registry.read_all()[0]
    assert loaded.hypothesis_id == hyp.hypothesis_id
    assert loaded.version == hyp.version
    assert loaded.status == hyp.status


def test_register_writes_to_disk(tmp_registry):
    hyp = _make_spec("hyp-c-v1")
    tmp_registry.register(hyp)
    assert tmp_registry._path.exists()
    with open(tmp_registry._path) as fh:
        lines = [l.strip() for l in fh if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["hypothesis_id"] == "hyp-c-v1"


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def test_get_returns_by_id(tmp_registry):
    hyp1 = _make_spec("hyp-get-1")
    hyp2 = _make_spec("hyp-get-2")
    tmp_registry.register(hyp1)
    tmp_registry.register(hyp2)
    result = tmp_registry.get("hyp-get-1")
    assert result is not None
    assert result.hypothesis_id == "hyp-get-1"


def test_get_returns_none_for_missing(tmp_registry):
    result = tmp_registry.get("does-not-exist")
    assert result is None


# ---------------------------------------------------------------------------
# Duplicate registration
# ---------------------------------------------------------------------------


def test_duplicate_register_raises_value_error(tmp_registry):
    hyp = _make_spec("hyp-dup")
    tmp_registry.register(hyp)
    with pytest.raises(ValueError, match="already registered"):
        tmp_registry.register(hyp)


# ---------------------------------------------------------------------------
# update_status
# ---------------------------------------------------------------------------


def test_update_status_valid_transition_works(tmp_registry):
    hyp = _make_spec("hyp-status", status=HypothesisStatus.draft)
    tmp_registry.register(hyp)
    updated = tmp_registry.update_status("hyp-status", "registered")
    assert updated.status == HypothesisStatus.registered
    assert tmp_registry.get("hyp-status").status == HypothesisStatus.registered


def test_update_status_with_notes(tmp_registry):
    hyp = _make_spec("hyp-notes", status=HypothesisStatus.draft)
    tmp_registry.register(hyp)
    updated = tmp_registry.update_status("hyp-notes", "registered", notes="approved by system")
    assert "approved by system" in updated.notes


def test_update_status_invalid_transition_raises_value_error(tmp_registry):
    hyp = _make_spec("hyp-bad", status=HypothesisStatus.draft)
    tmp_registry.register(hyp)
    # draft -> accepted is not allowed
    with pytest.raises(ValueError, match="Invalid status transition"):
        tmp_registry.update_status("hyp-bad", "accepted")


def test_update_status_missing_id_raises_value_error(tmp_registry):
    with pytest.raises(ValueError, match="not found"):
        tmp_registry.update_status("does-not-exist", "registered")


# ---------------------------------------------------------------------------
# latest state wins
# ---------------------------------------------------------------------------


def test_latest_state_wins_multiple_records(tmp_registry):
    hyp = _make_spec("hyp-latest", status=HypothesisStatus.draft)
    tmp_registry.register(hyp)
    # Simulate appending a second record directly to test the latest-wins logic
    updated_dict = hyp.to_dict()
    updated_dict["status"] = "registered"
    updated_dict["notes"] = "updated manually"
    with tmp_registry._path.open("a") as fh:
        fh.write(json.dumps(updated_dict) + "\n")

    # get() should return the latest
    latest = tmp_registry.get("hyp-latest")
    assert latest.status == HypothesisStatus.registered
    assert latest.notes == "updated manually"

    # read_all() should still have all records
    all_records = tmp_registry.read_all()
    assert len(all_records) == 2


# ---------------------------------------------------------------------------
# blank / malformed lines
# ---------------------------------------------------------------------------


def test_blank_lines_are_skipped(tmp_registry):
    hyp = _make_spec("hyp-blank")
    tmp_registry.register(hyp)
    # Append a blank line and another record
    with tmp_registry._path.open("a") as fh:
        fh.write("\n")
        fh.write(json.dumps(_make_spec("hyp-blank-2").to_dict()) + "\n")

    records = tmp_registry.read_all()
    ids = [r.hypothesis_id for r in records]
    assert "hyp-blank" in ids
    assert "hyp-blank-2" in ids


def test_malformed_json_raises_value_error(tmp_registry):
    hyp = _make_spec("hyp-malformed")
    tmp_registry.register(hyp)
    with tmp_registry._path.open("a") as fh:
        fh.write("not valid json\n")

    with pytest.raises(ValueError, match="Malformed JSON"):
        tmp_registry.read_all()


# ---------------------------------------------------------------------------
# env path config
# ---------------------------------------------------------------------------


def test_env_path_config_works_via_get_config(tmp_registry):
    from engine.edge_discovery import config as ed_config
    cfg = ed_config.get_config()
    assert cfg["hypothesis_registry_path"] == str(tmp_registry._path)


def test_default_path_when_no_env(tmp_registry, monkeypatch):
    monkeypatch.delenv("EDGE_DISCOVERY_HYPOTHESIS_REGISTRY_PATH", raising=False)
    from engine.edge_discovery import config as ed_config
    cfg = ed_config.get_config()
    assert cfg["hypothesis_registry_path"] == ed_config.HYPOTHESIS_REGISTRY_PATH_DEFAULT


# ---------------------------------------------------------------------------
# missing registry file
# ---------------------------------------------------------------------------


def test_missing_registry_file_returns_empty_list(tmp_registry):
    # File doesn't exist yet — this should not raise
    result = tmp_registry.read_all()
    assert result == []


def test_get_on_missing_file_returns_none(tmp_registry):
    assert tmp_registry._path.exists() is False
    result = tmp_registry.get("any-id")
    assert result is None


# ---------------------------------------------------------------------------
# JSONL append correctness
# ---------------------------------------------------------------------------


def test_sequential_appends_produce_valid_jsonl(tmp_registry):
    """Multiple sequential register() calls produce one JSON object per line."""
    for i in range(5):
        hyp = _make_spec(f"hyp-seq-{i}-v1")
        tmp_registry.register(hyp)

    raw = tmp_registry._path.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    assert len(lines) == 5, f"expected 5 lines, got {len(lines)}"

    # Every line must be valid JSON
    for i, ln in enumerate(lines, start=1):
        json.loads(ln)  # raises if malformed

    # read_all() must recover all 5
    assert len(tmp_registry.read_all()) == 5


def test_update_status_appends_valid_jsonl(tmp_registry):
    """update_status() appends a new line rather than modifying the file in-place."""
    hyp = _make_spec("hyp-status-v1", status=HypothesisStatus.draft)
    tmp_registry.register(hyp)

    tmp_registry.update_status("hyp-status-v1", "registered")

    raw = tmp_registry._path.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    assert len(lines) == 2, f"expected 2 lines, got {len(lines)}"

    for ln in lines:
        json.loads(ln)  # raises if malformed

    # Latest state should be "registered"
    latest = tmp_registry.get("hyp-status-v1")
    assert latest.status == HypothesisStatus.registered


def _worker_register(path: str, worker_id: int, count: int) -> None:
    """Worker function for concurrent register() test (runs in subprocess)."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from engine.edge_discovery.hypotheses.registry import HypothesisRegistry
    from engine.edge_discovery.hypotheses.spec import HypothesisStatus
    reg = HypothesisRegistry(path=path)
    for i in range(count):
        hyp = _make_spec(f"hyp-w{worker_id}-{i}-v1", status=HypothesisStatus.draft)
        reg.register(hyp)


def test_concurrent_register_produces_correct_line_count(tmp_path):
    """Concurrent register() calls from multiple processes produce valid JSONL.

    Uses multiprocessing to genuinely exercise the file lock across process
    boundaries on Linux/WSL.
    """
    import multiprocessing
    import sys
    from pathlib import Path

    registry_path = tmp_path / "concurrent.jsonl"
    n_workers = 4
    n_per_worker = 10

    ctx = multiprocessing.get_context("spawn")
    workers = [
        ctx.Process(target=_worker_register, args=(str(registry_path), wid, n_per_worker))
        for wid in range(n_workers)
    ]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=30)
        assert not w.exitcode, f"worker {w.pid} exited with {w.exitcode}"

    # Every process registered n_per_worker entries; count lines
    lines = [ln.strip() for ln in registry_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    expected = n_workers * n_per_worker
    assert len(lines) == expected, f"expected {expected} lines, got {len(lines)}"

    # Every line must be valid JSON
    for ln in lines:
        json.loads(ln)

    # read_all() should recover all
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from engine.edge_discovery.hypotheses.registry import HypothesisRegistry
    reg = HypothesisRegistry(path=str(registry_path))
    assert len(reg.read_all()) == expected
