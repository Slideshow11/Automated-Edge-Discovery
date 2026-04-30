"""Tests for scripts/local/validate_search_space_manifest.py"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(".")
SCRIPT = REPO / "scripts" / "local" / "validate_search_space_manifest.py"
FIXTURES = REPO / "fixtures" / "search_space_manifest_v1"


def run_validator(args):
    """Call validate_search_space_manifest.main() in-process."""
    old_argv = sys.argv
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    captured_stdout = sys.stdout = sys.stderr = open(sys.executable, "r", encoding="utf-8", errors="replace") \
        if False else type("Capture", (), {"getvalue": lambda self: "", "read": lambda self: ""})()

    buf_out = type("Buf", (), {"getvalue": lambda self: "", "read": lambda self: ""})()
    import io
    buf_out = io.StringIO()
    buf_err = io.StringIO()
    sys.stdout = buf_out
    sys.stderr = buf_err
    sys.argv = [str(SCRIPT)] + args

    try:
        from scripts.local.validate_search_space_manifest import main
        try:
            main()
            code = 0
        except SystemExit as e:
            code = e.code
        out = buf_out.getvalue()
        err = buf_err.getvalue()
    finally:
        sys.argv = old_argv
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return code, out, err


# ----- valid fixture tests -----

def test_valid_entry_text():
    code, out, _ = run_validator([str(FIXTURES / "valid_search_space_manifest.json")])
    assert code == 0, f"Expected 0, got {code}: {out}"
    assert "blockers_count: 0" in out

def test_valid_entry_json():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "valid_search_space_manifest.json")])
    assert code == 0
    data = json.loads(out)
    assert data["blockers_count"] == 0


# ----- missing required field -----

def test_missing_search_space_id():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_missing_search_space_id.json")])
    assert code == 1
    data = json.loads(out)
    codes = [b["code"] for b in data["blockers"]]
    assert "missing_required_field" in codes


# ----- invalid enum -----

def test_bad_search_mode():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_bad_search_mode.json")])
    assert code == 1
    data = json.loads(out)
    codes = [b["code"] for b in data["blockers"]]
    assert "invalid_enum" in codes


# ----- forbidden mode enabled -----

def test_forbidden_mode_enabled():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_forbidden_mode_enabled.json")])
    assert code == 1
    data = json.loads(out)
    codes = [b["code"] for b in data["blockers"]]
    assert "forbidden_mode_enabled" in codes


# ----- invalid budget -----

def test_bad_budget():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_bad_budget.json")])
    assert code == 1
    data = json.loads(out)
    codes = [b["code"] for b in data["blockers"]]
    assert "invalid_budget" in codes


# ----- empty required list -----

def test_empty_data_manifests():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_empty_data_manifests.json")])
    assert code == 1
    data = json.loads(out)
    codes = [b["code"] for b in data["blockers"]]
    assert "empty_required_list" in codes


# ----- missing file -----

def test_missing_file():
    code, out, err = run_validator(["/nonexistent/file.json"])
    assert code == 2


# ----- invalid JSON -----

def test_invalid_json():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{invalid json")
        path = f.name
    try:
        code, out, err = run_validator(["--format", "json", path])
        assert code == 2
    finally:
        Path(path).unlink()


# ----- object shape tests -----

def test_non_object_root_list():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump([], f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_object" in codes
    finally:
        Path(path).unlink()

def test_non_object_root_number():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(123, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_object" in codes
    finally:
        Path(path).unlink()

def test_data_manifests_not_a_list():
    entry = {
        "search_space_id": "SSM-2026-0018",
        "search_mode": "manual_grid",
        "allowed_data_manifests": "not-a-list",
        "allowed_features": [],
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": {"max_trials": 100},
        "forbidden_modes": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_list" in codes
    finally:
        Path(path).unlink()

def test_forbidden_modes_not_an_object():
    entry = {
        "search_space_id": "SSM-2026-0018",
        "search_mode": "manual_grid",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": [],
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": {"max_trials": 100},
        "forbidden_modes": "not-an-object",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_object" in codes
    finally:
        Path(path).unlink()

def test_budget_not_an_object():
    entry = {
        "search_space_id": "SSM-2026-0018",
        "search_mode": "manual_grid",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": [],
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": "not-an-object",
        "forbidden_modes": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_object" in codes
    finally:
        Path(path).unlink()


# ----- budget validation tests -----

def test_max_trials_not_integer():
    entry = {
        "search_space_id": "SSM-2026-0018",
        "search_mode": "manual_grid",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": [],
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": {"max_trials": "fifty"},
        "forbidden_modes": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_budget" in codes
    finally:
        Path(path).unlink()

def test_max_trials_zero():
    entry = {
        "search_space_id": "SSM-2026-0018",
        "search_mode": "manual_grid",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": [],
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": {"max_trials": 0},
        "forbidden_modes": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_budget" in codes
    finally:
        Path(path).unlink()

def test_max_agent_proposals_negative():
    entry = {
        "search_space_id": "SSM-2026-0018",
        "search_mode": "manual_grid",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": [],
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": {"max_trials": 100, "max_agent_proposals": -1},
        "forbidden_modes": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_budget" in codes
    finally:
        Path(path).unlink()


# ----- ID format tests -----

def test_search_space_id_wrong_format():
    entry = {
        "search_space_id": "SSM-2026-18",
        "search_mode": "manual_grid",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": [],
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": {"max_trials": 100},
        "forbidden_modes": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_id_format" in codes
    finally:
        Path(path).unlink()


# ----- budget boolean trap tests -----

def test_max_trials_bool_true():
    entry = {
        "search_space_id": "SSM-2026-0018",
        "search_mode": "manual_grid",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": [],
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": {"max_trials": True},
        "forbidden_modes": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_budget" in codes
    finally:
        Path(path).unlink()

def test_max_trials_bool_false():
    entry = {
        "search_space_id": "SSM-2026-0018",
        "search_mode": "manual_grid",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": [],
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": {"max_trials": False},
        "forbidden_modes": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_budget" in codes
    finally:
        Path(path).unlink()

def test_max_agent_proposals_bool_false():
    # False >= 0 numerically, but must be integer, not bool
    entry = {
        "search_space_id": "SSM-2026-0018",
        "search_mode": "manual_grid",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": [],
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": {"max_trials": 100, "max_agent_proposals": False},
        "forbidden_modes": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_budget" in codes
    finally:
        Path(path).unlink()


# ----- forbidden modes non-boolean tests -----

def test_forbidden_mode_string_value():
    entry = {
        "search_space_id": "SSM-2026-0018",
        "search_mode": "manual_grid",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": [],
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": {"max_trials": 100},
        "forbidden_modes": {"autonomous_search": "true"},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_forbidden_mode" in codes
    finally:
        Path(path).unlink()

def test_forbidden_mode_integer_value():
    entry = {
        "search_space_id": "SSM-2026-0018",
        "search_mode": "manual_grid",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": [],
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": {"max_trials": 100},
        "forbidden_modes": {"live_trading": 1},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_forbidden_mode" in codes
    finally:
        Path(path).unlink()


# ----- allowed_features / allowed_labels non-list tests -----

def test_allowed_features_not_list():
    entry = {
        "search_space_id": "SSM-2026-0018",
        "search_mode": "manual_grid",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": "feature_a",
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": {"max_trials": 100},
        "forbidden_modes": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_list" in codes
    finally:
        Path(path).unlink()

def test_allowed_labels_not_list():
    entry = {
        "search_space_id": "SSM-2026-0018",
        "search_mode": "manual_grid",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": [],
        "allowed_labels": "label_a",
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": {"max_trials": 100},
        "forbidden_modes": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_list" in codes
    finally:
        Path(path).unlink()


# ----- required field null-value regression tests -----

def test_required_field_null_search_space_id():
    entry = {
        "search_space_id": None,
        "search_mode": "manual_grid",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": [],
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": {"max_trials": 100},
        "forbidden_modes": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "missing_required_field" in codes
    finally:
        Path(path).unlink()


def test_required_field_null_budget():
    entry = {
        "search_space_id": "SSM-2026-0018",
        "search_mode": "manual_grid",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": [],
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": None,
        "forbidden_modes": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "missing_required_field" in codes
    finally:
        Path(path).unlink()


# ----- empty budget object regression test -----

def test_budget_missing_max_trials():
    entry = {
        "search_space_id": "SSM-2026-0018",
        "search_mode": "manual_grid",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": [],
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": {},
        "forbidden_modes": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "missing_required_field" in codes
        fields = [b["field"] for b in data["blockers"]]
        assert "budget.max_trials" in fields
    finally:
        Path(path).unlink()


# ----- multiple blockers in one entry -----

def test_multiple_blockers():
    # missing search_space_id + bad search_mode + forbidden mode enabled
    entry = {
        "search_space_id": "SSM-2026-18",
        "search_mode": "autonomous_search",
        "allowed_data_manifests": ["DM-1"],
        "allowed_features": [],
        "allowed_labels": [],
        "allowed_parameter_ranges": {},
        "validation_scheme": "purged_cpcv",
        "budget": {"max_trials": 100},
        "forbidden_modes": {"autonomous_search": True},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = [b["code"] for b in data["blockers"]]
        assert "invalid_id_format" in codes
        assert "invalid_enum" in codes
        assert "forbidden_mode_enabled" in codes
        assert data["blockers_count"] >= 3
    finally:
        Path(path).unlink()
