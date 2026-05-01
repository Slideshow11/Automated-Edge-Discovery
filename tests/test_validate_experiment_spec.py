"""Tests for scripts/local/validate_experiment_spec.py"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(".")
SCRIPT = REPO / "scripts" / "local" / "validate_experiment_spec.py"
FIXTURES = REPO / "fixtures" / "experiment_spec_v1"


def _blockers_for_path(data, path):
    """Extract blocker list from validator JSON output for a given file path.

    The validator normalises all paths via abspath(), so we first try exact string
    match, then fall back to basename match.
    """
    if path in data.get("files", {}):
        return data["files"][path].get("blockers", [])
    basename = Path(path).name
    for k, v in data.get("files", {}).items():
        if k.endswith(basename) or Path(k).name == basename:
            return v.get("blockers", [])
    return []


def run_validator(args):
    """Call validate_experiment_spec.main() in-process, return (code, stdout, stderr)."""
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
        from scripts.local.validate_experiment_spec import main
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


# ----- valid fixture tests -----

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


# ----- invalid fixture tests -----

def test_invalid_missing_required():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_missing_required.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_missing_required.json")}
    assert "missing_required_field" in codes


def test_invalid_experiment_id():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_experiment_id.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_experiment_id.json")}
    assert "invalid_id_format" in codes


def test_invalid_hypothesis_id():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_hypothesis_id.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_hypothesis_id.json")}
    assert "invalid_id_format" in codes


def test_invalid_search_space_id():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_search_space_id.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_search_space_id.json")}
    assert "invalid_id_format" in codes


def test_invalid_model_assessment_ref():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_model_assessment_ref.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_model_assessment_ref.json")}
    assert "invalid_ref_format" in codes


def test_invalid_study_type():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_study_type.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_study_type.json")}
    assert "invalid_enum" in codes


def test_invalid_trial_generation_mode():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_trial_generation_mode.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_trial_generation_mode.json")}
    assert "invalid_enum" in codes


def test_invalid_allowed_trial_lane():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_allowed_trial_lane.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_allowed_trial_lane.json")}
    assert "invalid_trial_lane" in codes


def test_invalid_data_manifest_refs_empty():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_data_manifest_refs_empty.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_data_manifest_refs_empty.json")}
    assert "invalid_list" in codes


def test_invalid_prohibited_mode_true():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_prohibited_mode_true.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_prohibited_mode_true.json")}
    assert "forbidden_governance_field" in codes


def test_invalid_preearnings_core_field():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_preearnings_core_field.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_preearnings_core_field.json")}
    assert "domain_neutrality_violation" in codes


# ----- missing file -----

def test_missing_file():
    code, out, err = run_validator(["/nonexistent/file.json"])
    assert code == 2
    assert "file not found" in err


# ----- invalid JSON -----

def test_invalid_json():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{ not valid json")
        path = f.name
    try:
        code, out, err = run_validator(["--format", "json", path])
        assert code == 2, f"Expected exit 2, got {code}: {err}"
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_json" in codes
    finally:
        Path(path).unlink()


# ----- required field handling -----

def _make_entry(**overrides):
    base = {
        "experiment_id": "EXP-2026-0001",
        "experiment_version": 1,
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": ["REF-2026-0001"],
        "study_type": "event_study",
        "decision_timestamp_policy": {"policy": "fixed_lag", "lag_days": 5},
        "feature_cutoff_policy": {"cutoff_days": 30},
        "trial_generation_mode": "manual_grid",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {
            "autonomous_search": False,
            "bayesian_optimization": False,
            "genetic_programming": False,
            "automated_promotion": False,
            "automated_registry_mutation": False,
            "live_trading": False,
            "production_execution": False,
            "gcru_integration": False,
        },
        "created_at": "2026-01-01T00:00:00Z",
        "reviewer": {"name": "Reviewer"},
    }
    base.update(overrides)
    return base


def _tmp_json(**overrides):
    entry = _make_entry(**overrides)
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    return Path(f.name)


def test_missing_required_field():
    # omit experiment_id entirely
    entry = {k: v for k, v in _make_entry().items() if k != "experiment_id"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "missing_required_field" in codes
    finally:
        Path(f.name).unlink()


def test_null_required_field():
    path = _tmp_json(experiment_id=None)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_empty_string_required_field():
    path = _tmp_json(experiment_id="")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_whitespace_only_required_text_field():
    path = _tmp_json(experiment_id="   ")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


# ----- ID validation -----

def test_invalid_experiment_id_format():
    path = _tmp_json(experiment_id="EXP-26-1")  # wrong format
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_non_string_experiment_id():
    path = _tmp_json(experiment_id=42)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_invalid_hypothesis_id_format():
    path = _tmp_json(hypothesis_id="HYP-26-1")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_non_string_hypothesis_id():
    path = _tmp_json(hypothesis_id=42)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_invalid_search_space_id_format():
    path = _tmp_json(search_space_id="SSM-26-1")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_non_string_search_space_id():
    path = _tmp_json(search_space_id=42)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_invalid_model_assessment_ref_format():
    path = _tmp_json(model_assessment_ref="MAS-26-1")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_ref_format" in codes
    finally:
        path.unlink()


def test_non_string_model_assessment_ref():
    path = _tmp_json(model_assessment_ref=42)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_ref_type" in codes
    finally:
        path.unlink()


# ----- enum validation -----

def test_invalid_study_type_string():
    path = _tmp_json(study_type="not_a_study_type")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_study_type_zero():
    path = _tmp_json(study_type=0)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_study_type_list():
    path = _tmp_json(study_type=[])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_study_type_dict():
    path = _tmp_json(study_type={})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_study_type_false():
    path = _tmp_json(study_type=False)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_trial_generation_mode_string():
    path = _tmp_json(trial_generation_mode="not_a_mode")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_trial_generation_mode_zero():
    path = _tmp_json(trial_generation_mode=0)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_trial_generation_mode_list():
    path = _tmp_json(trial_generation_mode=[])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_trial_generation_mode_dict():
    path = _tmp_json(trial_generation_mode={})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_trial_generation_mode_false():
    path = _tmp_json(trial_generation_mode=False)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


# ----- allowed_trial_lanes -----

def test_invalid_allowed_trial_lane_item():
    # manual_grid is a generation-mode value, not a source_lane taxonomy value
    path = _tmp_json(allowed_trial_lanes=["manual_grid"])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_trial_lane" in codes
    finally:
        path.unlink()


def test_allowed_trial_lane_non_string_item():
    path = _tmp_json(allowed_trial_lanes=[42])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_lane_type" in codes
    finally:
        path.unlink()


def test_allowed_trial_lanes_empty():
    path = _tmp_json(allowed_trial_lanes=[])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_allowed_trial_lanes_not_list():
    path = _tmp_json(allowed_trial_lanes="theory_first")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_list" in codes
    finally:
        path.unlink()


# ----- required object/list fields -----

def test_data_manifest_refs_empty():
    path = _tmp_json(data_manifest_refs=[])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_data_manifest_refs_not_list():
    path = _tmp_json(data_manifest_refs="REF-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_data_manifest_refs_item_not_string():
    path = _tmp_json(data_manifest_refs=[42])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_ref_type" in codes
    finally:
        path.unlink()


def test_decision_timestamp_policy_not_object():
    path = _tmp_json(decision_timestamp_policy="not_an_object")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_feature_cutoff_policy_not_object():
    path = _tmp_json(feature_cutoff_policy="not_an_object")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_prohibited_modes_not_object():
    path = _tmp_json(prohibited_modes="not_an_object")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_reviewer_not_object():
    path = _tmp_json(reviewer="not_an_object")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_object" in codes
    finally:
        path.unlink()


# ----- prohibited_modes -----

def test_prohibited_modes_all_false_passes():
    entry = _make_entry(prohibited_modes={
        "autonomous_search": False,
        "bayesian_optimization": False,
        "genetic_programming": False,
        "automated_promotion": False,
        "automated_registry_mutation": False,
        "live_trading": False,
        "production_execution": False,
        "gcru_integration": False,
    })
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        Path(f.name).unlink()


def test_prohibited_mode_autonomous_search_true():
    path = _tmp_json(prohibited_modes={
        "autonomous_search": True,
        "bayesian_optimization": False,
        "genetic_programming": False,
        "automated_promotion": False,
        "automated_registry_mutation": False,
        "live_trading": False,
        "production_execution": False,
        "gcru_integration": False,
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "forbidden_governance_field" in codes
    finally:
        path.unlink()


def test_prohibited_mode_bayesian_optimization_true():
    path = _tmp_json(prohibited_modes={
        "autonomous_search": False,
        "bayesian_optimization": True,
        "genetic_programming": False,
        "automated_promotion": False,
        "automated_registry_mutation": False,
        "live_trading": False,
        "production_execution": False,
        "gcru_integration": False,
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "forbidden_governance_field" in codes
    finally:
        path.unlink()


def test_prohibited_mode_genetic_programming_true():
    path = _tmp_json(prohibited_modes={
        "autonomous_search": False,
        "bayesian_optimization": False,
        "genetic_programming": True,
        "automated_promotion": False,
        "automated_registry_mutation": False,
        "live_trading": False,
        "production_execution": False,
        "gcru_integration": False,
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "forbidden_governance_field" in codes
    finally:
        path.unlink()


def test_prohibited_mode_automated_promotion_true():
    path = _tmp_json(prohibited_modes={
        "autonomous_search": False,
        "bayesian_optimization": False,
        "genetic_programming": False,
        "automated_promotion": True,
        "automated_registry_mutation": False,
        "live_trading": False,
        "production_execution": False,
        "gcru_integration": False,
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "forbidden_governance_field" in codes
    finally:
        path.unlink()


def test_prohibited_mode_automated_registry_mutation_true():
    path = _tmp_json(prohibited_modes={
        "autonomous_search": False,
        "bayesian_optimization": False,
        "genetic_programming": False,
        "automated_promotion": False,
        "automated_registry_mutation": True,
        "live_trading": False,
        "production_execution": False,
        "gcru_integration": False,
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "forbidden_governance_field" in codes
    finally:
        path.unlink()


def test_prohibited_mode_live_trading_true():
    path = _tmp_json(prohibited_modes={
        "autonomous_search": False,
        "bayesian_optimization": False,
        "genetic_programming": False,
        "automated_promotion": False,
        "automated_registry_mutation": False,
        "live_trading": True,
        "production_execution": False,
        "gcru_integration": False,
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "forbidden_governance_field" in codes
    finally:
        path.unlink()


def test_prohibited_mode_production_execution_true():
    path = _tmp_json(prohibited_modes={
        "autonomous_search": False,
        "bayesian_optimization": False,
        "genetic_programming": False,
        "automated_promotion": False,
        "automated_registry_mutation": False,
        "live_trading": False,
        "production_execution": True,
        "gcru_integration": False,
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "forbidden_governance_field" in codes
    finally:
        path.unlink()


def test_prohibited_mode_gcru_integration_true():
    path = _tmp_json(prohibited_modes={
        "autonomous_search": False,
        "bayesian_optimization": False,
        "genetic_programming": False,
        "automated_promotion": False,
        "automated_registry_mutation": False,
        "live_trading": False,
        "production_execution": False,
        "gcru_integration": True,
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "forbidden_governance_field" in codes
    finally:
        path.unlink()


def test_prohibited_mode_zero_fails():
    """Strict false-only semantics: 0 is neither None nor False, so it triggers forbidden_governance_field."""
    path = _tmp_json(prohibited_modes={
        "autonomous_search": 0,
        "bayesian_optimization": False,
        "genetic_programming": False,
        "automated_promotion": False,
        "automated_registry_mutation": False,
        "live_trading": False,
        "production_execution": False,
        "gcru_integration": False,
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "forbidden_governance_field" in codes
    finally:
        path.unlink()


def test_prohibited_mode_empty_string_fails():
    """Strict false-only semantics: '' is neither None nor False, so it triggers forbidden_governance_field."""
    path = _tmp_json(prohibited_modes={
        "autonomous_search": "",
        "bayesian_optimization": False,
        "genetic_programming": False,
        "automated_promotion": False,
        "automated_registry_mutation": False,
        "live_trading": False,
        "production_execution": False,
        "gcru_integration": False,
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "forbidden_governance_field" in codes
    finally:
        path.unlink()


def test_prohibited_mode_nested_field_missing():
    """
    All eight prohibited_modes nested stop-rule fields are required.
    Each must be present and exactly False.
    Omitting any one (gcru_integration in this case) fails validation.
    """
    path = _tmp_json(prohibited_modes={
        "autonomous_search": False,
        "bayesian_optimization": False,
        "genetic_programming": False,
        "automated_promotion": False,
        "automated_registry_mutation": False,
        "live_trading": False,
        "production_execution": False,
        # gcru_integration deliberately omitted
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "forbidden_governance_field" in codes
        # Confirm the missing field is reported
        field_names = {b["field"] for b in _blockers_for_path(data, path)}
        assert any("gcru_integration" in f for f in field_names), f"Expected gcru_integration in fields, got {field_names}"
    finally:
        path.unlink()


def test_prohibited_mode_all_eight_fields_required():
    """
    Parameterized across all eight prohibited_modes nested fields.
    Each must be present and exactly False — omitting any one fails.
    """
    all_fields = [
        "autonomous_search",
        "bayesian_optimization",
        "genetic_programming",
        "automated_promotion",
        "automated_registry_mutation",
        "live_trading",
        "production_execution",
        "gcru_integration",
    ]
    for missing_field in all_fields:
        present = {f: False for f in all_fields}
        del present[missing_field]
        path = _tmp_json(prohibited_modes=present)
        try:
            code, out, _ = run_validator(["--format", "json", str(path)])
            assert code == 1, f"Expected failure when {missing_field} is missing"
            data = json.loads(out)
            codes = {b["code"] for b in _blockers_for_path(data, path)}
            assert "forbidden_governance_field" in codes, f"Expected forbidden_governance_field when {missing_field} missing"
        finally:
            path.unlink()


# ----- domain-neutrality violations -----

_PREEARNS_FIELDS = [
    "earnings_date",
    "event_session",
    "amc_bmo_indicator",
    "entry_dpe",
    "exit_dpe",
    "delta_target",
    "expiry_rank",
    "iv_crush",
    "gap_exposure",
]


def test_domain_neutrality_earnings_date():
    path = _tmp_json(earnings_date="2026-01-15")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "domain_neutrality_violation" in codes
    finally:
        path.unlink()


def test_domain_neutrality_event_session():
    path = _tmp_json(event_session="after_close")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "domain_neutrality_violation" in codes
    finally:
        path.unlink()


def test_domain_neutrality_amc_bmo_indicator():
    path = _tmp_json(amc_bmo_indicator="AMC")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "domain_neutrality_violation" in codes
    finally:
        path.unlink()


def test_domain_neutrality_entry_dpe():
    path = _tmp_json(entry_dpe=5)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "domain_neutrality_violation" in codes
    finally:
        path.unlink()


def test_domain_neutrality_exit_dpe():
    path = _tmp_json(exit_dpe=30)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "domain_neutrality_violation" in codes
    finally:
        path.unlink()


def test_domain_neutrality_delta_target():
    path = _tmp_json(delta_target=0.25)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "domain_neutrality_violation" in codes
    finally:
        path.unlink()


def test_domain_neutrality_expiry_rank():
    path = _tmp_json(expiry_rank=30)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "domain_neutrality_violation" in codes
    finally:
        path.unlink()


def test_domain_neutrality_iv_crush():
    path = _tmp_json(iv_crush=True)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "domain_neutrality_violation" in codes
    finally:
        path.unlink()


def test_domain_neutrality_gap_exposure():
    path = _tmp_json(gap_exposure=0.05)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "domain_neutrality_violation" in codes
    finally:
        path.unlink()


# ----- root JSON type -----

def test_root_array_fails():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump([], f)
    f.close()
    path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_object" in codes
    finally:
        Path(path).unlink()


def test_root_number_fails():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(42, f)
    f.close()
    path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_object" in codes
    finally:
        Path(path).unlink()


def test_root_string_fails():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump("not an object", f)
    f.close()
    path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_object" in codes
    finally:
        Path(path).unlink()


# ----- experiment_version -----

def test_experiment_version_non_integer():
    path = _tmp_json(experiment_version="1")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_experiment_version_zero():
    path = _tmp_json(experiment_version=0)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_experiment_version_negative():
    path = _tmp_json(experiment_version=-1)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_experiment_version_bool_fails():
    # bool is a subclass of int in Python, so True==1, False==0 — validator should catch this
    path = _tmp_json(experiment_version=True)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_type" in codes
    finally:
        path.unlink()
