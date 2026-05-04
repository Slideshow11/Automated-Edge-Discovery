"""Tests for scripts/local/validate_model_assessment_spec.py"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(".")
SCRIPT = REPO / "scripts" / "local" / "validate_model_assessment_spec.py"
FIXTURES = REPO / "fixtures" / "model_assessment_spec_v1"


def run_validator(args):
    """Call validate_model_assessment_spec.main() in-process."""
    old_argv = sys.argv
    import io
    buf_out = io.StringIO()
    buf_err = io.StringIO()
    sys.stdout = buf_out
    sys.stderr = buf_err
    sys.argv = [str(SCRIPT)] + args

    try:
        from scripts.local.validate_model_assessment_spec import main
        try:
            main()
            code = 0
        except SystemExit as e:
            code = e.code
        out = buf_out.getvalue()
        err = buf_err.getvalue()
    finally:
        sys.argv = old_argv
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__

    return code, out, err


# ─── Valid fixture ─────────────────────────────────────────────────────────────


def test_valid_entry_text():
    code, out, _ = run_validator([str(FIXTURES / "valid_model_assessment_spec.json")])
    assert code == 0, f"Expected 0, got {code}: {out}"
    assert "blockers_count: 0" in out


def test_valid_entry_json():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "valid_model_assessment_spec.json")])
    assert code == 0
    data = json.loads(out)
    assert data["blockers_count"] == 0
    assert data["blockers"] == []


# ─── Invalid: missing assessment_id ───────────────────────────────────────────


def test_missing_assessment_id():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_missing_assessment_id.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in data["blockers"]}
    assert "missing_required_field" in codes


def test_missing_assessment_id_code():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_missing_assessment_id.json")])
    assert code == 1
    data = json.loads(out)
    codes = {(b["code"], b["field"]) for b in data["blockers"]}
    assert ("missing_required_field", "assessment_id") in codes


# ─── Invalid: bad assessment_status enum ──────────────────────────────────────


def test_bad_status():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_bad_status.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in data["blockers"]}
    assert "invalid_enum" in codes


# ─── Invalid: missing required_checks fields ───────────────────────────────────


def test_missing_required_checks_fields():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_missing_required_checks.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in data["blockers"]}
    assert "missing_required_check" in codes


# ─── Invalid: accepted without required evidence ───────────────────────────────


def test_accepted_without_required_evidence():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_accepted_without_required_evidence.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in data["blockers"]}
    assert "accepted_without_required_evidence" in codes


# ─── Invalid: bad metric value (bool for sample_size) ─────────────────────────


def test_bad_metric_value_bool_sample_size():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_bad_metric_value.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in data["blockers"]}
    assert "invalid_metric" in codes


# ─── Non-object root ──────────────────────────────────────────────────────────


def test_non_object_root_list():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump([], f)
        tmp = Path(f.name)
    try:
        code, out, err = run_validator(["--format", "json", str(tmp)])
        assert code == 1, f"Expected 1, got {code}: {out}"
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_object" in codes
    finally:
        tmp.unlink()


def test_non_object_root_string():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump("not an object", f)
        tmp = Path(f.name)
    try:
        code, out, err = run_validator(["--format", "json", str(tmp)])
        assert code == 1, f"Expected 1, got {code}: {out}"
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_object" in codes
    finally:
        tmp.unlink()


# ─── Missing file ─────────────────────────────────────────────────────────────


def test_missing_file():
    code, out, err = run_validator(["/nonexistent/file.json"])
    assert code == 2
    assert "file not found" in err


# ─── Invalid JSON ─────────────────────────────────────────────────────────────


def test_invalid_json():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{ not valid json")
        tmp = Path(f.name)
    try:
        code, out, err = run_validator([str(tmp)])
        assert code == 2
        assert "invalid JSON" in err or "JSONDecodeError" in err
    finally:
        tmp.unlink()


# ─── ID format validation ─────────────────────────────────────────────────────


def test_trial_id_wrong_format():
    entry = {
        "assessment_id": "MAS-2026-0010",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-017",  # wrong format
        "search_space_id": "SSM-2026-0001",
        "assessment_status": "draft",
        "metrics": {},
        "required_checks": {
            "sample_size_gate_passed": False,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_id_format" in codes
    finally:
        tmp.unlink()


def test_search_space_id_wrong_format():
    entry = {
        "assessment_id": "MAS-2026-0011",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0011",
        "search_space_id": "SSM-2026-017",  # wrong format
        "assessment_status": "draft",
        "metrics": {},
        "required_checks": {
            "sample_size_gate_passed": False,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_id_format" in codes
    finally:
        tmp.unlink()


def test_assessment_id_wrong_format():
    entry = {
        "assessment_id": "MAS-2026-017",  # wrong format
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0012",
        "search_space_id": "SSM-2026-0012",
        "assessment_status": "draft",
        "metrics": {},
        "required_checks": {
            "sample_size_gate_passed": False,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_id_format" in codes
    finally:
        tmp.unlink()


# ─── required_checks must be object ──────────────────────────────────────────


def test_required_checks_not_object():
    entry = {
        "assessment_id": "MAS-2026-0013",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0013",
        "search_space_id": "SSM-2026-0013",
        "assessment_status": "draft",
        "metrics": {},
        "required_checks": "not-an-object",
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_object" in codes
    finally:
        tmp.unlink()


def test_required_checks_is_list():
    entry = {
        "assessment_id": "MAS-2026-0014",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0014",
        "search_space_id": "SSM-2026-0014",
        "assessment_status": "draft",
        "metrics": {},
        "required_checks": [False, False, False, False, False],
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_object" in codes
    finally:
        tmp.unlink()


# ─── required_checks field: non-boolean value ────────────────────────────────


def test_required_check_non_boolean_string():
    entry = {
        "assessment_id": "MAS-2026-0015",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0015",
        "search_space_id": "SSM-2026-0015",
        "assessment_status": "draft",
        "metrics": {},
        "required_checks": {
            "sample_size_gate_passed": "true",
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_boolean" in codes
    finally:
        tmp.unlink()


def test_required_check_non_boolean_int():
    entry = {
        "assessment_id": "MAS-2026-0016",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0016",
        "search_space_id": "SSM-2026-0016",
        "assessment_status": "draft",
        "metrics": {},
        "required_checks": {
            "sample_size_gate_passed": 1,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_boolean" in codes
    finally:
        tmp.unlink()


# ─── metrics: non-object ───────────────────────────────────────────────────────


def test_metrics_not_object():
    entry = {
        "assessment_id": "MAS-2026-0017",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0017",
        "search_space_id": "SSM-2026-0017",
        "assessment_status": "draft",
        "metrics": "not-an-object",
        "required_checks": {
            "sample_size_gate_passed": False,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_object" in codes
    finally:
        tmp.unlink()


# ─── metrics: bad sample_size (non-positive integer) ──────────────────────────


def test_metric_sample_size_zero():
    entry = {
        "assessment_id": "MAS-2026-0018",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0018",
        "search_space_id": "SSM-2026-0018",
        "assessment_status": "draft",
        "metrics": {"sample_size": 0},
        "required_checks": {
            "sample_size_gate_passed": False,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_metric" in codes
    finally:
        tmp.unlink()


def test_metric_sample_size_negative():
    entry = {
        "assessment_id": "MAS-2026-0019",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0019",
        "search_space_id": "SSM-2026-0019",
        "assessment_status": "draft",
        "metrics": {"sample_size": -10},
        "required_checks": {
            "sample_size_gate_passed": False,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_metric" in codes
    finally:
        tmp.unlink()


# ─── metrics: bad pbo (out of range, bool) ─────────────────────────────────────


def test_metric_pbo_too_high():
    entry = {
        "assessment_id": "MAS-2026-0020",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0020",
        "search_space_id": "SSM-2026-0020",
        "assessment_status": "draft",
        "metrics": {"pbo": 1.5},
        "required_checks": {
            "sample_size_gate_passed": False,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_metric" in codes
    finally:
        tmp.unlink()


def test_metric_pbo_negative():
    entry = {
        "assessment_id": "MAS-2026-0021",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0021",
        "search_space_id": "SSM-2026-0021",
        "assessment_status": "draft",
        "metrics": {"pbo": -0.1},
        "required_checks": {
            "sample_size_gate_passed": False,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_metric" in codes
    finally:
        tmp.unlink()


# ─── metrics: bad dsr (non-number) ───────────────────────────────────────────


def test_metric_dsr_string():
    entry = {
        "assessment_id": "MAS-2026-0022",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0022",
        "search_space_id": "SSM-2026-0022",
        "assessment_status": "draft",
        "metrics": {"dsr": "high"},
        "required_checks": {
            "sample_size_gate_passed": False,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_metric" in codes
    finally:
        tmp.unlink()


# ─── Governance: accepted requires all checks true ─────────────────────────────


def test_accepted_all_checks_true_but_missing_confirmatory():
    """accepted with all checks true except confirmatory_evidence_present → fails."""
    entry = {
        "assessment_id": "MAS-2026-0023",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0023",
        "search_space_id": "SSM-2026-0023",
        "assessment_status": "accepted",
        "metrics": {
            "sample_size": 500,
            "pbo": 0.03,
            "dsr": 1.4,
        },
        "required_checks": {
            "sample_size_gate_passed": True,
            "leakage_check_passed": True,
            "pbo_check_passed": True,
            "dsr_check_passed": True,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "accepted_without_required_evidence" in codes
    finally:
        tmp.unlink()


# ─── Governance: accepted missing metrics fields ────────────────────────────────


def test_accepted_missing_sample_size_in_metrics():
    entry = {
        "assessment_id": "MAS-2026-0024",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0024",
        "search_space_id": "SSM-2026-0024",
        "assessment_status": "accepted",
        "metrics": {
            "pbo": 0.03,
            "dsr": 1.4,
        },
        "required_checks": {
            "sample_size_gate_passed": True,
            "leakage_check_passed": True,
            "pbo_check_passed": True,
            "dsr_check_passed": True,
            "confirmatory_evidence_present": True,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "accepted_without_required_evidence" in codes
    finally:
        tmp.unlink()


# ─── Valid accepted entry (all governance requirements met) ─────────────────────


def test_valid_accepted_entry():
    entry = {
        "assessment_id": "MAS-2026-0025",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0025",
        "search_space_id": "SSM-2026-0025",
        "assessment_status": "accepted",
        "metrics": {
            "sample_size": 500,
            "pbo": 0.03,
            "dsr": 1.4,
        },
        "required_checks": {
            "sample_size_gate_passed": True,
            "leakage_check_passed": True,
            "pbo_check_passed": True,
            "dsr_check_passed": True,
            "confirmatory_evidence_present": True,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        tmp.unlink()


# ─── reviewer must be object ──────────────────────────────────────────────────


def test_reviewer_not_object():
    entry = {
        "assessment_id": "MAS-2026-0026",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0026",
        "search_space_id": "SSM-2026-0026",
        "assessment_status": "draft",
        "metrics": {},
        "required_checks": {
            "sample_size_gate_passed": False,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": "human-001",
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_object" in codes
    finally:
        tmp.unlink()


# ─── null required field ───────────────────────────────────────────────────────


def test_null_required_field_assessment_id():
    entry = {
        "assessment_id": None,
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0027",
        "search_space_id": "SSM-2026-0027",
        "assessment_status": "draft",
        "metrics": {},
        "required_checks": {
            "sample_size_gate_passed": False,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {(b["code"], b["field"]) for b in data["blockers"]}
        assert ("missing_required_field", "assessment_id") in codes
    finally:
        tmp.unlink()


# ─── accepted with bool metric values (sample_size=true fails governance) ─────


def test_accepted_with_bool_sample_size_in_metrics():
    entry = {
        "assessment_id": "MAS-2026-0028",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0028",
        "search_space_id": "SSM-2026-0028",
        "assessment_status": "accepted",
        "metrics": {
            "sample_size": True,
            "pbo": 0.03,
            "dsr": 1.4,
        },
        "required_checks": {
            "sample_size_gate_passed": True,
            "leakage_check_passed": True,
            "pbo_check_passed": True,
            "dsr_check_passed": True,
            "confirmatory_evidence_present": True,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        # bool for sample_size → invalid_metric
        # accepted also requires sample_size integer > 0
        assert "invalid_metric" in codes
    finally:
        tmp.unlink()


# ─── accepted with bool pbo in metrics ────────────────────────────────────────


def test_accepted_with_bool_pbo_in_metrics():
    entry = {
        "assessment_id": "MAS-2026-0029",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0029",
        "search_space_id": "SSM-2026-0029",
        "assessment_status": "accepted",
        "metrics": {
            "sample_size": 500,
            "pbo": True,
            "dsr": 1.4,
        },
        "required_checks": {
            "sample_size_gate_passed": True,
            "leakage_check_passed": True,
            "pbo_check_passed": True,
            "dsr_check_passed": True,
            "confirmatory_evidence_present": True,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_metric" in codes
    finally:
        tmp.unlink()


# ─── accepted with bool dsr in metrics ───────────────────────────────────────


def test_accepted_with_bool_dsr_in_metrics():
    entry = {
        "assessment_id": "MAS-2026-0030",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0030",
        "search_space_id": "SSM-2026-0030",
        "assessment_status": "accepted",
        "metrics": {
            "sample_size": 500,
            "pbo": 0.03,
            "dsr": True,
        },
        "required_checks": {
            "sample_size_gate_passed": True,
            "leakage_check_passed": True,
            "pbo_check_passed": True,
            "dsr_check_passed": True,
            "confirmatory_evidence_present": True,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_metric" in codes
    finally:
        tmp.unlink()


# ─── Regression: non-string ID values must not raise TypeError ───────────────────────


def test_assessment_id_non_string_int():
    """assessment_id as integer must emit invalid_id_format, not TypeError."""
    entry = {
        "assessment_id": 1234,
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0031",
        "search_space_id": "SSM-2026-0031",
        "assessment_status": "draft",
        "metrics": {},
        "required_checks": {
            "sample_size_gate_passed": False,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1, f"Expected 1, got {code}: {out}"
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_id_format" in codes
    finally:
        tmp.unlink()


def test_trial_id_non_string_int():
    """trial_id as integer must emit invalid_id_format, not TypeError."""
    entry = {
        "assessment_id": "MAS-2026-0032",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": 1234,
        "search_space_id": "SSM-2026-0032",
        "assessment_status": "draft",
        "metrics": {},
        "required_checks": {
            "sample_size_gate_passed": False,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1, f"Expected 1, got {code}: {out}"
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_id_format" in codes
    finally:
        tmp.unlink()


def test_search_space_id_non_string_int():
    """search_space_id as integer must emit invalid_id_format, not TypeError."""
    entry = {
        "assessment_id": "MAS-2026-0033",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0033",
        "search_space_id": 1234,
        "assessment_status": "draft",
        "metrics": {},
        "required_checks": {
            "sample_size_gate_passed": False,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1, f"Expected 1, got {code}: {out}"
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_id_format" in codes
    finally:
        tmp.unlink()


# ─── Regression: non-string assessment_status must not raise TypeError ───────────────────────


def test_assessment_status_non_string_dict():
    """assessment_status as dict must emit invalid_enum, not TypeError."""
    entry = {
        "assessment_id": "MAS-2026-0034",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0034",
        "search_space_id": "SSM-2026-0034",
        "assessment_status": {"status": "accepted"},
        "metrics": {},
        "required_checks": {
            "sample_size_gate_passed": False,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1, f"Expected 1, got {code}: {out}"
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_enum" in codes
    finally:
        tmp.unlink()


def test_assessment_status_non_string_list():
    """assessment_status as list must emit invalid_enum, not TypeError."""
    entry = {
        "assessment_id": "MAS-2026-0035",
        "hypothesis_id": "HYP-2026-0001",
        "trial_id": "TRL-2026-0035",
        "search_space_id": "SSM-2026-0035",
        "assessment_status": ["accepted"],
        "metrics": {},
        "required_checks": {
            "sample_size_gate_passed": False,
            "leakage_check_passed": False,
            "pbo_check_passed": False,
            "dsr_check_passed": False,
            "confirmatory_evidence_present": False,
        },
        "reviewer": {"reviewer_id": "human-001"},
        "created_at": "2026-04-30T12:00:00Z",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        tmp = Path(f.name)
    try:
        code, out, _ = run_validator(["--format", "json", str(tmp)])
        assert code == 1, f"Expected 1, got {code}: {out}"
        data = json.loads(out)
        codes = {b["code"] for b in data["blockers"]}
        assert "invalid_enum" in codes
    finally:
        tmp.unlink()


def test_schema_additional_properties_false():
    """Schema enforces top-level additionalProperties: false."""
    schema = json.load(open("schemas/model_assessment_spec_v1.schema.json"))
    assert schema.get("additionalProperties") is False, \
        "model_assessment_spec_v1 schema must have top-level additionalProperties: false"


def test_schema_hypothesis_id_pattern():
    """Schema enforces HYP-YYYY-NNNN format for hypothesis_id."""
    schema = json.load(open("schemas/model_assessment_spec_v1.schema.json"))
    hyp = schema.get("properties", {}).get("hypothesis_id", {})
    assert hyp.get("pattern") == "^HYP-[0-9]{4}-[0-9]{4}$", \
        "model_assessment_spec_v1 hypothesis_id must have HYP-YYYY-NNNN pattern"
