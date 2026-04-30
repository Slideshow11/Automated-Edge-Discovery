"""Tests for scripts/local/validate_trial_ledger.py"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(".")
SCRIPT = REPO / "scripts" / "local" / "validate_trial_ledger.py"


def run_validator(args, stdin_data=None):
    """Call the validator main() function directly, returning exit code and stdout."""
    import io
    old_stdout = io.StringIO()
    old_stderr = io.StringIO()
    old_argv = sys.argv
    try:
        sys.argv = [SCRIPT.name] + args
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        from scripts.local.validate_trial_ledger import main
        try:
            main()
        except SystemExit as e:
            code = e.code
        else:
            code = 0
        stdout = old_stdout.getvalue()
        stderr = old_stderr.getvalue()
    finally:
        sys.argv = old_argv
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
    return code, stdout, stderr


def test_valid_entry_text():
    code, stdout, stderr = run_validator(["fixtures/trial_ledger_v1/valid_trial_ledger_entry.json"])
    assert code == 0, f"expected 0, got {code}: {stderr}"
    assert "blockers_count: 0" in stdout


def test_valid_entry_json():
    code, stdout, stderr = run_validator(["--format", "json", "fixtures/trial_ledger_v1/valid_trial_ledger_entry.json"])
    assert code == 0
    out = json.loads(stdout)
    assert out["blockers"] == []


def test_missing_trial_id():
    code, stdout, stderr = run_validator([
        "--format", "json",
        "fixtures/trial_ledger_v1/invalid_missing_trial_id.json",
    ])
    assert code == 1
    out = json.loads(stdout)
    codes = {b["code"] for b in out["blockers"]}
    assert "missing_required_field" in codes


def test_bad_source_lane():
    code, stdout, stderr = run_validator([
        "--format", "json",
        "fixtures/trial_ledger_v1/invalid_bad_source_lane.json",
    ])
    assert code == 1
    out = json.loads(stdout)
    codes = {b["code"] for b in out["blockers"]}
    assert "invalid_enum" in codes


def test_bad_promotion_acceptance():
    code, stdout, stderr = run_validator([
        "--format", "json",
        "fixtures/trial_ledger_v1/invalid_bad_promotion_acceptance.json",
    ])
    assert code == 1
    out = json.loads(stdout)
    codes = {b["code"] for b in out["blockers"]}
    assert "missing_confirmatory_link" in codes


def test_bad_search_space_id():
    code, stdout, stderr = run_validator([
        "--format", "json",
        "fixtures/trial_ledger_v1/invalid_bad_search_space_id.json",
    ])
    assert code == 1
    out = json.loads(stdout)
    codes = {b["code"] for b in out["blockers"]}
    assert "invalid_id_format" in codes


def test_missing_file():
    code, stdout, stderr = run_validator(["/nonexistent/file.json"])
    assert code == 2
    assert "file not found" in stderr


def test_invalid_json():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{ not valid json")
        tmp = Path(f.name)
    try:
        code, stdout, stderr = run_validator([str(tmp)])
        assert code == 2
        assert "invalid JSON" in stderr
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
        code, stdout, stderr = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        out = json.loads(stdout)
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
        code, stdout, stderr = run_validator(["--format", "json", str(tmp)])
        assert code == 0, f"expected 0, got {code}: {stdout}"
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
        code, stdout, stderr = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        out = json.loads(stdout)
        codes = {b["code"] for b in out["blockers"]}
        assert "invalid_confirmatory_link" in codes
    finally:
        tmp.unlink()


def test_confirmatory_data_scope_reused():
    """confirmatory_data_scope identical to data_scope → rejected."""
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
        # identical to data_scope → must be rejected
        "confirmatory_data_scope": {
            "dataset_id": "DS-2026-Q1",
            "sample_start": "2024-01-01",
            "sample_end": "2024-12-31",
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, stdout, stderr = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        out = json.loads(stdout)
        codes = {b["code"] for b in out["blockers"]}
        assert "confirmatory_data_scope_reused" in codes
    finally:
        tmp.unlink()


def test_confirmatory_data_scope_differs_by_sample_dates():
    """same dataset_id but different sample dates → should pass."""
    entry = {
        "trial_id": "TRL-2026-0011",
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
        "results": {},
        "confirmatory_trial_id": "TRL-2026-0012",
        "confirmatory_source_lane": "confirmatory",
        "confirmatory_data_scope": {
            "dataset_id": "DS-2026-Q1",  # same dataset
            "sample_start": "2024-07-01",  # different window
            "sample_end": "2024-12-31",
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, stdout, stderr = run_validator(["--format", "json", str(tmp)])
        assert code == 0, f"expected 0, got {code}: {stdout}"
    finally:
        tmp.unlink()


def test_status_enum_rejected():
    entry = {
        "trial_id": "TRL-2026-0013",
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
        code, stdout, stderr = run_validator(["--format", "json", str(tmp)])
        assert code == 0
    finally:
        tmp.unlink()
