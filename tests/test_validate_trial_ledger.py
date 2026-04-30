"""Tests for scripts/local/validate_trial_ledger.py"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(".")
SCRIPT = REPO / "scripts" / "local" / "validate_trial_ledger.py"
FIXTURES = REPO / "fixtures" / "trial_ledger_v1"


def run_cli(args):
    cmd = [sys.executable, str(SCRIPT)] + args
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res


def test_valid_entry_text():
    res = run_cli([str(FIXTURES / "valid_trial_ledger_entry.json")])
    assert res.returncode == 0, f"expected 0, got {res.returncode}: {res.stderr}"
    assert "blockers_count: 0" in res.stdout


def test_valid_entry_json():
    res = run_cli(["--format", "json", str(FIXTURES / "valid_trial_ledger_entry.json")])
    assert res.returncode == 0
    out = json.loads(res.stdout)
    assert out["blockers"] == []


def test_missing_trial_id():
    res = run_cli([
        "--format", "json",
        str(FIXTURES / "invalid_missing_trial_id.json"),
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    codes = {b["code"] for b in out["blockers"]}
    assert "missing_required_field" in codes


def test_bad_source_lane():
    res = run_cli([
        "--format", "json",
        str(FIXTURES / "invalid_bad_source_lane.json"),
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    codes = {b["code"] for b in out["blockers"]}
    assert "invalid_enum" in codes


def test_bad_promotion_acceptance():
    res = run_cli([
        "--format", "json",
        str(FIXTURES / "invalid_bad_promotion_acceptance.json"),
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    codes = {b["code"] for b in out["blockers"]}
    assert "missing_confirmatory_link" in codes


def test_bad_search_space_id():
    res = run_cli([
        "--format", "json",
        str(FIXTURES / "invalid_bad_search_space_id.json"),
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    codes = {b["code"] for b in out["blockers"]}
    assert "invalid_id_format" in codes


def test_missing_file():
    res = run_cli(["/nonexistent/file.json"])
    assert res.returncode == 2
    assert "file not found" in res.stderr


def test_invalid_json():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{ not valid json")
        tmp = Path(f.name)
    try:
        res = run_cli([str(tmp)])
        assert res.returncode == 2
        assert "invalid JSON" in res.stderr
    finally:
        tmp.unlink()


def test_confirmatory_trial_id_wrong_format():
    entry = {
        "trial_id": "TRL-2026-0005",
        "search_space_id": "SSM-2026-0017",
        "hypothesis_id": "EHH-2026-0012",
        "source_lane": "theory_first",
        "promotion_status": "provisional",
        "status": "completed",
        "data_scope": {"dataset_id": "DS-2026-Q1"},
        "execution_scope": {"runner_id": "runner-local-v1"},
        "results": {},
        "confirmatory_trial_id": "TRL-2026-017",  # wrong format
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        res = run_cli(["--format", "json", str(tmp)])
        assert res.returncode == 1
        out = json.loads(res.stdout)
        codes = {b["code"] for b in out["blockers"]}
        assert "invalid_id_format" in codes
    finally:
        tmp.unlink()


def test_accepted_with_full_confirmatory_link():
    """accepted with full confirmatory link should pass."""
    entry = {
        "trial_id": "TRL-2026-0006",
        "search_space_id": "SSM-2026-0017",
        "hypothesis_id": "EHH-2026-0012",
        "source_lane": "exploratory_anomaly",
        "promotion_status": "accepted",
        "status": "completed",
        "data_scope": {
            "dataset_id": "DS-2026-Q1",
            "sample_start": "2024-01-01",
            "sample_end": "2024-06-30",
        },
        "execution_scope": {"runner_id": "runner-local-v1"},
        "results": {"metrics_summary": {"sharpe": 1.8}},
        "confirmatory_trial_id": "TRL-2026-0007",
        "confirmatory_source_lane": "confirmatory",
        "confirmatory_data_scope": {
            "dataset_id": "DS-2026-Q2",
            "sample_start": "2024-07-01",
            "sample_end": "2024-12-31",
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        res = run_cli(["--format", "json", str(tmp)])
        assert res.returncode == 0, f"expected 0, got {res.returncode}: {res.stdout}"
    finally:
        tmp.unlink()


def test_confirmatory_source_lane_not_confirmatory():
    entry = {
        "trial_id": "TRL-2026-0008",
        "search_space_id": "SSM-2026-0017",
        "hypothesis_id": "EHH-2026-0012",
        "source_lane": "exploratory_anomaly",
        "promotion_status": "accepted",
        "status": "completed",
        "data_scope": {"dataset_id": "DS-2026-Q1"},
        "execution_scope": {"runner_id": "runner-local-v1"},
        "results": {},
        "confirmatory_trial_id": "TRL-2026-0007",
        "confirmatory_source_lane": "theory_first",  # wrong
        "confirmatory_data_scope": {"dataset_id": "DS-2026-Q2"},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        res = run_cli(["--format", "json", str(tmp)])
        assert res.returncode == 1
        out = json.loads(res.stdout)
        codes = {b["code"] for b in out["blockers"]}
        assert "invalid_confirmatory_link" in codes
    finally:
        tmp.unlink()


def test_confirmatory_data_scope_reused():
    entry = {
        "trial_id": "TRL-2026-0009",
        "search_space_id": "SSM-2026-0017",
        "hypothesis_id": "EHH-2026-0012",
        "source_lane": "exploratory_anomaly",
        "promotion_status": "accepted",
        "status": "completed",
        "data_scope": {
            "dataset_id": "DS-2026-Q1",
            "sample_start": "2024-01-01",
            "sample_end": "2024-12-31",
        },
        "execution_scope": {"runner_id": "runner-local-v1"},
        "results": {},
        "confirmatory_trial_id": "TRL-2026-0010",
        "confirmatory_source_lane": "confirmatory",
        "confirmatory_data_scope": {
            "dataset_id": "DS-2026-Q1",  # same dataset — reused
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        res = run_cli(["--format", "json", str(tmp)])
        assert res.returncode == 1
        out = json.loads(res.stdout)
        codes = {b["code"] for b in out["blockers"]}
        assert "confirmatory_data_scope_reused" in codes
    finally:
        tmp.unlink()


def test_status_enum_rejected():
    entry = {
        "trial_id": "TRL-2026-0011",
        "search_space_id": "SSM-2026-0017",
        "hypothesis_id": "EHH-2026-0012",
        "source_lane": "theory_first",
        "promotion_status": "raw_result",
        "status": "abandoned",
        "data_scope": {"dataset_id": "DS-2026-Q1"},
        "execution_scope": {"runner_id": "runner-local-v1"},
        "results": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        res = run_cli(["--format", "json", str(tmp)])
        assert res.returncode == 0
    finally:
        tmp.unlink()
