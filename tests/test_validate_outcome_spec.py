"""Tests for scripts/local/validate_outcome_spec.py"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(".")
SCRIPT = REPO / "scripts" / "local" / "validate_outcome_spec.py"
FIXTURES = REPO / "fixtures" / "outcome_spec_v1"


def _blockers_for_path(data, path):
    """Extract blocker list from validator JSON output for a given file path."""
    if path in data.get("files", {}):
        return data["files"][path].get("blockers", [])
    basename = Path(path).name
    for k, v in data.get("files", {}).items():
        if k.endswith(basename) or Path(k).name == basename:
            return v.get("blockers", [])
    return []


def run_validator(args):
    """Call validate_outcome_spec.main() in-process, return (code, stdout, stderr)."""
    import io
    old_argv = sys.argv
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    buf_out = io.StringIO()
    buf_err = io.StringIO()
    sys.argv = [str(SCRIPT)] + args
    sys.stdout = buf_out
    sys.stderr = buf_err
    try:
        from scripts.local.validate_outcome_spec import main
        try:
            main()
            code = 0
        except SystemExit as e:
            code = e.code
        stdout = buf_out.getvalue()
        stderr = buf_err.getvalue()
    finally:
        sys.argv = old_argv
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    return code, stdout, stderr


# ----------------------------------------------------------------------
# Valid fixture tests
# ----------------------------------------------------------------------

def test_valid_minimal_text():
    code, out, _ = run_validator([str(FIXTURES / "valid_minimal.json")])
    assert code == 0, f"Expected 0, got {code}: {out}"
    assert "blockers_count: 0" in out


def test_valid_minimal_json():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "valid_minimal.json")])
    assert code == 0
    data = json.loads(out)
    assert data["files"][str(FIXTURES / "valid_minimal.json")]["blockers_count"] == 0
    assert data["files"][str(FIXTURES / "valid_minimal.json")]["blockers"] == []


# ----------------------------------------------------------------------
# Invalid fixture tests
# ----------------------------------------------------------------------

def test_invalid_missing_required():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_missing_required.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_missing_required.json")}
    assert "missing_required_field" in codes


def test_invalid_outcome_spec_id():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_outcome_spec_id.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_outcome_spec_id.json")}
    assert "invalid_id_format" in codes


def test_invalid_metric_direction():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_metric_direction.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_metric_direction.json")}
    assert "invalid_enum" in codes


def test_invalid_window_start_policy():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_window_start_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_window_start_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_window_end_policy():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_window_end_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_window_end_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_window_role():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_window_role.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_window_role.json")}
    assert "invalid_enum" in codes


def test_invalid_window_unit():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_window_unit.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_window_unit.json")}
    assert "invalid_enum" in codes


def test_invalid_labeling_scheme():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_labeling_scheme.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_labeling_scheme.json")}
    assert "invalid_enum" in codes


def test_invalid_return_basis():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_return_basis.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_return_basis.json")}
    assert "invalid_enum" in codes


def test_invalid_benchmark_policy():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_benchmark_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_benchmark_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_outcome_window_field_name():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_outcome_window_field_name.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_outcome_window_field_name.json")}
    assert "invalid_field" in codes


def test_invalid_evidence_role_missing_field():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_evidence_role_missing_field.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_evidence_role_missing_field.json")}
    assert "missing_required_field" in codes


def test_invalid_evidence_role_non_boolean():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_evidence_role_non_boolean.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_evidence_role_non_boolean.json")}
    assert "invalid_boolean" in codes


def test_invalid_purge_gap_days_negative():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_purge_gap_days_negative.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_purge_gap_days_negative.json")}
    assert "invalid_value" in codes


def test_invalid_embargo_fraction_out_of_range():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_embargo_fraction_out_of_range.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_embargo_fraction_out_of_range.json")}
    assert "invalid_value" in codes


def test_invalid_embargo_units():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_embargo_units.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_embargo_units.json")}
    assert "invalid_enum" in codes


def test_invalid_reviewer_type():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_reviewer_type.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_reviewer_type.json")}
    assert "invalid_object" in codes


def test_invalid_model_assessment_ref():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_model_assessment_ref.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_model_assessment_ref.json")}
    assert "invalid_ref_format" in codes


def test_invalid_trial_ledger_ref():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_trial_ledger_ref.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_trial_ledger_ref.json")}
    assert "invalid_ref_format" in codes


def test_invalid_computed_assessment_field():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_computed_assessment_field.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_computed_assessment_field.json")}
    assert "computed_assessment_field" in codes


# ----------------------------------------------------------------------
# Root JSON type tests
# ----------------------------------------------------------------------

def test_root_array_fails():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump([], f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "invalid_object" in codes
    finally:
        Path(f.name).unlink()


def test_root_integer_fails():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(42, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "invalid_object" in codes
    finally:
        Path(f.name).unlink()


def test_root_string_fails():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump("not an object", f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "invalid_object" in codes
    finally:
        Path(f.name).unlink()


def test_root_number_fails():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(3.14, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "invalid_object" in codes
    finally:
        Path(f.name).unlink()


# ----------------------------------------------------------------------
# Parse/read error exit 2 tests
# ----------------------------------------------------------------------

def test_nonexistent_file_exit_2():
    code, out, err = run_validator(["/tmp/does_not_exist_12345.json"])
    assert code == 2, f"Expected 2, got {code}"


def test_invalid_json_exit_2():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    f.write("{invalid json}")
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 2, f"Expected 2, got {code}"
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "invalid_json" in codes
    finally:
        Path(f.name).unlink()


# ----------------------------------------------------------------------
# Helpers for inline tests
# ----------------------------------------------------------------------

def _make_valid_entry(**overrides):
    """Return a fully-valid OutcomeSpec v1 record, with overrides applied."""
    base = {
        "outcome_spec_id": "OUT-2026-0001",
        "outcome_version": 1,
        "outcome_family": "equity_calendar_anomalies",
        "metric_name": "monthly_return",
        "metric_direction": "maximize",
        "outcome_window": {
            "anchor": "calendar_month_start",
            "window_start_days": 1,
            "window_end_days": 21,
            "window_unit": "observations",
        },
        "window_start_policy": "data_start",
        "window_end_policy": "fixed_horizon",
        "window_role": "out_of_sample",
        "labeling_scheme": "forward_return",
        "return_basis": "simple_return",
        "benchmark_policy": "static_benchmark",
        "observation_count_policy": {
            "min_observations": 60,
            "requires_min_observations": True,
        },
        "evidence_role_requirements": {
            "requires_oos": True,
            "requires_live": False,
            "requires_uncertainty": True,
            "requires_benchmark": True,
            "requires_stress_period": False,
            "requires_purge_embargo": True,
            "requires_min_observations": True,
        },
        "purge_embargo_policy": {
            "purge_gap_days": 1,
            "embargo_fraction": 0.1,
            "embargo_units": "fraction",
            "overlap_policy": "non-overlapping monthly windows",
        },
        "created_at": "2026-04-15T00:00:00Z",
        "reviewer": {"name": "dr_elliot_review_2026"},
    }
    # Apply top-level overrides (including fields not in base, e.g. computed-assessment fields)
    for k, v in overrides.items():
        base[k] = v
    return base


def _tmp_json(**overrides):
    entry = _make_valid_entry(**overrides)
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    return Path(f.name)


# ----------------------------------------------------------------------
# Required field tests
# ----------------------------------------------------------------------

def test_missing_required_field():
    entry = {k: v for k, v in _make_valid_entry().items() if k != "outcome_spec_id"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_null_required_field():
    path = _tmp_json(outcome_spec_id=None)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_empty_string_required_field():
    path = _tmp_json(outcome_spec_id="")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_whitespace_only_required_field():
    path = _tmp_json(outcome_spec_id="   ")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_missing_evidence_role_requirements():
    entry = {k: v for k, v in _make_valid_entry().items() if k != "evidence_role_requirements"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_missing_purge_embargo_policy():
    entry = {k: v for k, v in _make_valid_entry().items() if k != "purge_embargo_policy"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


# ----------------------------------------------------------------------
# ID validation tests
# ----------------------------------------------------------------------

def test_invalid_outcome_spec_id_format():
    path = _tmp_json(outcome_spec_id="OUT-PA-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_non_string_outcome_spec_id():
    path = _tmp_json(outcome_spec_id=42)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_malformed_model_assessment_ref():
    path = _tmp_json(model_assessment_refs=["MAS-PA-0001"])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_ref_format" in codes
    finally:
        path.unlink()


def test_malformed_trial_ledger_ref():
    path = _tmp_json(trial_ledger_refs=["TRL-PA-0001"])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_ref_format" in codes
    finally:
        path.unlink()


def test_runner_output_refs_non_string_item():
    path = _tmp_json(runner_output_refs=[42, "valid-ref"])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_ref_type" in codes
    finally:
        path.unlink()


def test_review_packet_refs_non_string_item():
    path = _tmp_json(review_packet_refs=[None, "valid-ref"])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_ref_type" in codes
    finally:
        path.unlink()


# ----------------------------------------------------------------------
# Enum validation tests
# ----------------------------------------------------------------------

def test_enum_invalid_metric_direction():
    path = _tmp_json(metric_direction="optimize")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_enum_invalid_window_start_policy():
    path = _tmp_json(window_start_policy="relative_start")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_enum_invalid_window_end_policy():
    path = _tmp_json(window_end_policy="relative_end")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_enum_invalid_window_role():
    path = _tmp_json(window_role="in_sample_train")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_enum_invalid_labeling_scheme():
    path = _tmp_json(labeling_scheme="cumulative_return")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_enum_invalid_return_basis():
    path = _tmp_json(return_basis="net_return")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_enum_invalid_benchmark_policy():
    path = _tmp_json(benchmark_policy="custom_benchmark")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_enum_falsey_zero_rejected():
    path = _tmp_json(metric_direction=0)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_enum_falsey_empty_list_rejected():
    path = _tmp_json(metric_direction=[])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_enum_falsey_empty_dict_rejected():
    path = _tmp_json(metric_direction={})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_enum_falsey_false_rejected():
    path = _tmp_json(metric_direction=False)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


# ----------------------------------------------------------------------
# outcome_window validation tests
# ----------------------------------------------------------------------

def test_outcome_window_non_object():
    path = _tmp_json(outcome_window="not_an_object")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_outcome_window_array():
    path = _tmp_json(outcome_window=[1, 2, 3])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_outcome_window_missing_anchor():
    entry = _make_valid_entry()
    del entry["outcome_window"]["anchor"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_outcome_window_empty_anchor():
    entry = _make_valid_entry()
    entry["outcome_window"]["anchor"] = ""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_outcome_window_whitespace_anchor():
    entry = _make_valid_entry()
    entry["outcome_window"]["anchor"] = "   "
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_outcome_window_missing_window_start_days():
    entry = _make_valid_entry()
    del entry["outcome_window"]["window_start_days"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_outcome_window_missing_window_end_days():
    entry = _make_valid_entry()
    del entry["outcome_window"]["window_end_days"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_outcome_window_missing_window_unit():
    entry = _make_valid_entry()
    del entry["outcome_window"]["window_unit"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_outcome_window_start_days_bool():
    entry = _make_valid_entry()
    entry["outcome_window"]["window_start_days"] = True
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "invalid_type" in codes
    finally:
        Path(f.name).unlink()


def test_outcome_window_end_days_bool():
    entry = _make_valid_entry()
    entry["outcome_window"]["window_end_days"] = False
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "invalid_type" in codes
    finally:
        Path(f.name).unlink()


def test_outcome_window_window_unit_hours():
    entry = _make_valid_entry()
    entry["outcome_window"]["window_unit"] = "hours"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "invalid_enum" in codes
    finally:
        Path(f.name).unlink()


def test_outcome_window_legacy_start_offset():
    entry = _make_valid_entry()
    entry["outcome_window"]["start_offset"] = 5
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "invalid_field" in codes
    finally:
        Path(f.name).unlink()


def test_outcome_window_legacy_end_offset():
    entry = _make_valid_entry()
    entry["outcome_window"]["end_offset"] = 20
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "invalid_field" in codes
    finally:
        Path(f.name).unlink()


# ----------------------------------------------------------------------
# evidence_role_requirements validation tests
# ----------------------------------------------------------------------

def test_evidence_role_requirements_non_object():
    path = _tmp_json(evidence_role_requirements="not_an_object")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_evidence_role_requirements_missing_requires_oos():
    entry = _make_valid_entry()
    del entry["evidence_role_requirements"]["requires_oos"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_evidence_role_requirements_missing_requires_live():
    entry = _make_valid_entry()
    del entry["evidence_role_requirements"]["requires_live"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_evidence_role_requirements_missing_requires_uncertainty():
    entry = _make_valid_entry()
    del entry["evidence_role_requirements"]["requires_uncertainty"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_evidence_role_requirements_missing_requires_benchmark():
    entry = _make_valid_entry()
    del entry["evidence_role_requirements"]["requires_benchmark"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_evidence_role_requirements_missing_requires_stress_period():
    entry = _make_valid_entry()
    del entry["evidence_role_requirements"]["requires_stress_period"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_evidence_role_requirements_missing_requires_purge_embargo():
    entry = _make_valid_entry()
    del entry["evidence_role_requirements"]["requires_purge_embargo"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_evidence_role_requirements_missing_requires_min_observations():
    entry = _make_valid_entry()
    del entry["evidence_role_requirements"]["requires_min_observations"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_evidence_role_requirements_non_boolean():
    entry = _make_valid_entry()
    entry["evidence_role_requirements"]["requires_oos"] = "true"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "invalid_boolean" in codes
    finally:
        Path(f.name).unlink()


def test_evidence_role_requirements_false_accepted():
    entry = _make_valid_entry()
    entry["evidence_role_requirements"] = {
        "requires_oos": False,
        "requires_live": False,
        "requires_uncertainty": False,
        "requires_benchmark": False,
        "requires_stress_period": False,
        "requires_purge_embargo": False,
        "requires_min_observations": False,
    }
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        Path(f.name).unlink()


def test_evidence_role_requirements_true_accepted():
    entry = _make_valid_entry()
    entry["evidence_role_requirements"] = {
        "requires_oos": True,
        "requires_live": True,
        "requires_uncertainty": True,
        "requires_benchmark": True,
        "requires_stress_period": True,
        "requires_purge_embargo": True,
        "requires_min_observations": True,
    }
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        Path(f.name).unlink()


# ----------------------------------------------------------------------
# purge_embargo_policy validation tests
# ----------------------------------------------------------------------

def test_purge_embargo_policy_non_object():
    path = _tmp_json(purge_embargo_policy="not_an_object")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_purge_embargo_policy_empty_object():
    path = _tmp_json(purge_embargo_policy={})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_purge_embargo_policy_missing_purge_gap_days():
    entry = _make_valid_entry()
    del entry["purge_embargo_policy"]["purge_gap_days"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_purge_embargo_policy_missing_embargo_fraction():
    entry = _make_valid_entry()
    del entry["purge_embargo_policy"]["embargo_fraction"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_purge_embargo_policy_missing_embargo_units():
    entry = _make_valid_entry()
    del entry["purge_embargo_policy"]["embargo_units"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_purge_embargo_policy_missing_overlap_policy():
    entry = _make_valid_entry()
    del entry["purge_embargo_policy"]["overlap_policy"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, f.name)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_purge_embargo_policy_purge_gap_days_negative():
    path = _tmp_json(purge_embargo_policy={
        "purge_gap_days": -1,
        "embargo_fraction": 0.1,
        "embargo_units": "fraction",
        "overlap_policy": "non-overlapping monthly windows",
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_purge_embargo_policy_purge_gap_days_bool():
    path = _tmp_json(purge_embargo_policy={
        "purge_gap_days": True,
        "embargo_fraction": 0.1,
        "embargo_units": "fraction",
        "overlap_policy": "non-overlapping monthly windows",
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_purge_embargo_policy_embargo_fraction_below_zero():
    path = _tmp_json(purge_embargo_policy={
        "purge_gap_days": 1,
        "embargo_fraction": -0.1,
        "embargo_units": "fraction",
        "overlap_policy": "non-overlapping monthly windows",
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_purge_embargo_policy_embargo_fraction_above_one():
    path = _tmp_json(purge_embargo_policy={
        "purge_gap_days": 1,
        "embargo_fraction": 1.5,
        "embargo_units": "fraction",
        "overlap_policy": "non-overlapping monthly windows",
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_purge_embargo_policy_embargo_fraction_bool():
    path = _tmp_json(purge_embargo_policy={
        "purge_gap_days": 1,
        "embargo_fraction": True,
        "embargo_units": "fraction",
        "overlap_policy": "non-overlapping monthly windows",
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_purge_embargo_policy_embargo_units_hours():
    path = _tmp_json(purge_embargo_policy={
        "purge_gap_days": 1,
        "embargo_fraction": 0.1,
        "embargo_units": "hours",
        "overlap_policy": "non-overlapping monthly windows",
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_purge_embargo_policy_overlap_policy_empty_string():
    path = _tmp_json(purge_embargo_policy={
        "purge_gap_days": 1,
        "embargo_fraction": 0.1,
        "embargo_units": "fraction",
        "overlap_policy": "",
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


# ----------------------------------------------------------------------
# Object/list field type tests
# ----------------------------------------------------------------------

def test_observation_count_policy_non_object():
    path = _tmp_json(observation_count_policy="not_an_object")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_reviewer_non_object():
    path = _tmp_json(reviewer="not_an_object")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_model_assessment_refs_non_list():
    path = _tmp_json(model_assessment_refs="not_a_list")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_trial_ledger_refs_non_list():
    path = _tmp_json(trial_ledger_refs=42)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_runner_output_refs_non_list():
    path = _tmp_json(runner_output_refs={"not": "a list"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_review_packet_refs_non_list():
    path = _tmp_json(review_packet_refs=False)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


# ----------------------------------------------------------------------
# Computed-assessment boundary rule tests
# ----------------------------------------------------------------------

COMPUTED_ASSESSMENT_FIELDS = [
    "pbo_estimate",
    "dsr_estimate",
    "backtest_pnl_haircut",
    "overfit_discount_factor",
    "adjusted_expected_oos_sharpe",
    "probability_of_loss",
    "false_discovery_rate_estimate",
    "strategy_complexity_score",
    "factor_exposure_stability_check",
    "null_model_performance",
    "performance_vs_null",
    "selected_variant_id",
    "n_tried",
    "trial_family_id",
]


def test_computed_assessment_pbo_estimate():
    path = _tmp_json(pbo_estimate=0.05)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_assessment_dsr_estimate():
    path = _tmp_json(dsr_estimate=1.2)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_assessment_backtest_pnl_haircut():
    path = _tmp_json(backtest_pnl_haircut=0.9)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_assessment_overfit_discount_factor():
    path = _tmp_json(overfit_discount_factor=0.8)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_assessment_adjusted_expected_oos_sharpe():
    path = _tmp_json(adjusted_expected_oos_sharpe=1.5)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_assessment_probability_of_loss():
    path = _tmp_json(probability_of_loss=0.3)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_assessment_false_discovery_rate_estimate():
    path = _tmp_json(false_discovery_rate_estimate=0.1)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_assessment_strategy_complexity_score():
    path = _tmp_json(strategy_complexity_score=5)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_assessment_factor_exposure_stability_check():
    path = _tmp_json(factor_exposure_stability_check=0.95)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_assessment_null_model_performance():
    path = _tmp_json(null_model_performance=0.02)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_assessment_performance_vs_null():
    path = _tmp_json(performance_vs_null=0.05)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_assessment_selected_variant_id():
    path = _tmp_json(selected_variant_id="EXP-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_assessment_n_tried():
    path = _tmp_json(n_tried=42)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_assessment_trial_family_id():
    path = _tmp_json(trial_family_id="tfamily-001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


# ----------------------------------------------------------------------
# outcome_version type tests
# ----------------------------------------------------------------------

def test_outcome_version_boolean():
    path = _tmp_json(outcome_version=True)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_outcome_version_zero():
    path = _tmp_json(outcome_version=0)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_outcome_version_string():
    path = _tmp_json(outcome_version="1")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()
