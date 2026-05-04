"""Tests for scripts/local/validate_preearnings_profile.py"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(".")
SCRIPT = REPO / "scripts" / "local" / "validate_preearnings_profile.py"
FIXTURES = REPO / "fixtures" / "preearnings_profile_v1"


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
    """Call validate_preearnings_profile.main() in-process, return (code, stdout, stderr)."""
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
        from scripts.local.validate_preearnings_profile import main
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


def _make_valid_entry(**overrides):
    """Return a fully-valid PreEarningsProfile v1 record, with overrides applied."""
    base = {
        "preearnings_profile_id": "PEP-2026-0001",
        "preearnings_profile_version": 1,
        "event_study_spec_ref": "EVS-2026-0001",
        "options_event_risk_ref": "OER-2026-0001",
        "session_anchor_policy": "amc_only",
        "earnings_time_reference": "after_hours_only",
        "entry_dpe_policy": {
            "entry_dpe_min": 1,
            "entry_dpe_max": 5,
            "dpe_counting_convention": "trading_days",
            "anchor_day_policy": "earnings_date_anchor",
        },
        "exit_dpe_policy": {
            "exit_dpe_min": 0,
            "exit_dpe_max": 2,
            "dpe_counting_convention": "trading_days",
            "anchor_day_policy": "earnings_date_anchor",
        },
        "iv_crush_policy": {
            "iv_crush_measurement_window": {"start": -1, "end": 5, "unit": "dpe"},
            "iv_crush_definition": "percent_iv_drop",
        },
        "gap_exposure_policy": "prohibit_gap_hold",
        "created_at": "2026-05-04T00:00:00Z",
        "reviewer": {"name": "Reviewer"},
    }
    for k, v in overrides.items():
        base[k] = v
    return base


def _tmp_json(**overrides):
    """Write a valid entry (with overrides) to a temp file and return the path."""
    entry = _make_valid_entry(**overrides)
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    return Path(f.name)


# ---------------------------------------------------------------------------
# 1. Valid fixture tests
# ---------------------------------------------------------------------------

def test_valid_minimal_text():
    code, out, _ = run_validator([str(FIXTURES / "valid_minimal.json")])
    assert code == 0, f"Expected 0, got {code}: {out}"
    assert "[OK]" in out


def test_valid_minimal_json():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "valid_minimal.json")])
    assert code == 0
    data = json.loads(out)
    assert data["files"][str(FIXTURES / "valid_minimal.json")]["blockers_count"] == 0
    assert data["files"][str(FIXTURES / "valid_minimal.json")]["blockers"] == []


# ---------------------------------------------------------------------------
# 2. Invalid fixture tests (24)
# ---------------------------------------------------------------------------

def test_invalid_boundary_field():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_boundary_field.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_boundary_field.json")}
    assert "invalid_field" in codes


def test_invalid_earnings_time_reference_fixture():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_earnings_time_reference.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_earnings_time_reference.json")}
    assert "invalid_enum" in codes


def test_invalid_entry_dpe_policy_type():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_entry_dpe_policy_type.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_entry_dpe_policy_type.json")}
    assert "invalid_object" in codes


def test_invalid_event_study_spec_ref():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_event_study_spec_ref.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_event_study_spec_ref.json")}
    assert "invalid_id_format" in codes


def test_invalid_exit_dpe_policy_type():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_exit_dpe_policy_type.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_exit_dpe_policy_type.json")}
    assert "invalid_object" in codes


def test_invalid_extension_hooks_unknown_field():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_extension_hooks_unknown_field.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_extension_hooks_unknown_field.json")}
    assert "invalid_field" in codes


def test_invalid_gap_exposure_policy_fixture():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_gap_exposure_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_gap_exposure_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_instrument_universe_ref():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_instrument_universe_ref.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_instrument_universe_ref.json")}
    assert "invalid_id_format" in codes


def test_invalid_iv_crush_measurement_window_missing_field():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_iv_crush_measurement_window_missing_field.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_iv_crush_measurement_window_missing_field.json")}
    assert "missing_required_field" in codes


def test_invalid_iv_crush_measurement_window_unit():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_iv_crush_measurement_window_unit.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_iv_crush_measurement_window_unit.json")}
    assert "invalid_enum" in codes


def test_invalid_iv_crush_policy_type():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_iv_crush_policy_type.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_iv_crush_policy_type.json")}
    assert "invalid_object" in codes


def test_invalid_iv_regime_filter_fixture():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_iv_regime_filter.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_iv_regime_filter.json")}
    assert "invalid_enum" in codes


def test_invalid_live_execution_field():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_live_execution_field.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_live_execution_field.json")}
    assert "invalid_field" in codes


def test_invalid_minimum_iv_rank_out_of_range():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_minimum_iv_rank_out_of_range.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_minimum_iv_rank_out_of_range.json")}
    assert "invalid_value" in codes


def test_invalid_missing_required():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_missing_required.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_missing_required.json")}
    assert "missing_required_field" in codes


def test_invalid_options_event_risk_ref():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_options_event_risk_ref.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_options_event_risk_ref.json")}
    assert "invalid_id_format" in codes


def test_invalid_outcome_spec_ref():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_outcome_spec_ref.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_outcome_spec_ref.json")}
    assert "invalid_id_format" in codes


def test_invalid_outcome_spec_refs_empty():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_outcome_spec_refs_empty.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_outcome_spec_refs_empty.json")}
    assert "invalid_list" in codes


def test_invalid_preearnings_profile_id():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_preearnings_profile_id.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_preearnings_profile_id.json")}
    assert "invalid_id_format" in codes


def test_invalid_preearnings_profile_version():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_preearnings_profile_version.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_preearnings_profile_version.json")}
    assert "invalid_value" in codes


def test_invalid_provider_table_field():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_provider_table_field.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_provider_table_field.json")}
    assert "invalid_field" in codes


def test_invalid_reviewer_empty_object():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_reviewer_empty_object.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_reviewer_empty_object.json")}
    assert "missing_required_field" in codes


def test_invalid_reviewer_type():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_reviewer_type.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_reviewer_type.json")}
    assert "invalid_object" in codes


def test_invalid_session_anchor_policy_fixture():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_session_anchor_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_session_anchor_policy.json")}
    assert "invalid_enum" in codes


# ---------------------------------------------------------------------------
# 3. Root JSON type tests
# ---------------------------------------------------------------------------

def test_root_array_fails():
    path = _tmp_json()
    path.write_text("[]")
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_object" in codes


def test_root_integer_fails():
    path = _tmp_json()
    path.write_text("42")
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_object" in codes


def test_root_string_fails():
    path = _tmp_json()
    path.write_text('"bad"')
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_object" in codes


# ---------------------------------------------------------------------------
# 4. Parse/read error tests
# ---------------------------------------------------------------------------

def test_nonexistent_file_exit_2():
    code, out, err = run_validator(["/nonexistent/path/PEP-2026-0001.json"])
    assert code == 2
    assert "could not read file" in err or "[ERROR]" in err


def test_invalid_json_exit_2():
    path = _tmp_json()
    path.write_text("{invalid json}")
    code, out, err = run_validator(["--format", "json", str(path)])
    assert code == 2
    assert "could not parse JSON" in err or "[ERROR]" in err


# ---------------------------------------------------------------------------
# 5. Required field tests
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = [
    "preearnings_profile_id",
    "preearnings_profile_version",
    "event_study_spec_ref",
    "options_event_risk_ref",
    "session_anchor_policy",
    "earnings_time_reference",
    "entry_dpe_policy",
    "exit_dpe_policy",
    "iv_crush_policy",
    "gap_exposure_policy",
    "created_at",
    "reviewer",
]

for field in REQUIRED_FIELDS:

    def make_test_missing(field):
        def test_missing_field():
            entry = _make_valid_entry()
            del entry[field]
            path = _tmp_json(**{"__replace": entry})
            # Rebuild without the field
            entry = _make_valid_entry()
            del entry[field]
            f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            json.dump(entry, f)
            f.close()
            p = Path(f.name)
            code, out, _ = run_validator(["--format", "json", str(p)])
            assert code == 1
            data = json.loads(out)
            codes = {b["code"] for b in _blockers_for_path(data, str(p))}
            assert "missing_required_field" in codes
        return test_missing_field

    def make_test_null(field):
        def test_null_field():
            entry = _make_valid_entry()
            entry[field] = None
            f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            json.dump(entry, f)
            f.close()
            p = Path(f.name)
            code, out, _ = run_validator(["--format", "json", str(p)])
            assert code == 1
            data = json.loads(out)
            codes = {b["code"] for b in _blockers_for_path(data, str(p))}
            assert "missing_required_field" in codes
        return test_null_field

    # Text fields that can be empty/whitespace
    text_fields = {"preearnings_profile_id", "event_study_spec_ref", "options_event_risk_ref",
                   "session_anchor_policy", "earnings_time_reference", "gap_exposure_policy", "created_at"}

    if field in text_fields:

        def make_test_empty(field):
            def test_empty_string():
                entry = _make_valid_entry()
                entry[field] = ""
                f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
                json.dump(entry, f)
                f.close()
                p = Path(f.name)
                code, out, _ = run_validator(["--format", "json", str(p)])
                assert code == 1
            return test_empty_string

        def make_test_whitespace(field):
            def test_whitespace_only():
                entry = _make_valid_entry()
                entry[field] = "   "
                f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
                json.dump(entry, f)
                f.close()
                p = Path(f.name)
                code, out, _ = run_validator(["--format", "json", str(p)])
                assert code == 1
            return test_whitespace_only

        globals()[f"test_missing_{field}_required"] = make_test_missing(field)
        globals()[f"test_null_{field}_required"] = make_test_null(field)
        globals()[f"test_empty_string_{field}"] = make_test_empty(field)
        globals()[f"test_whitespace_only_{field}"] = make_test_whitespace(field)
    else:
        globals()[f"test_missing_{field}_required"] = make_test_missing(field)
        globals()[f"test_null_{field}_required"] = make_test_null(field)


# ---------------------------------------------------------------------------
# 6. ID/ref validation tests
# ---------------------------------------------------------------------------

def test_invalid_preearnings_profile_id_format():
    path = _tmp_json(preearnings_profile_id="PEP-PA-0001")
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_id_format" in codes


def test_non_string_preearnings_profile_id():
    path = _tmp_json()
    entry = json.loads(path.read_text())
    entry["preearnings_profile_id"] = 123
    path.write_text(json.dumps(entry))
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_id_format" in codes


def test_invalid_event_study_spec_ref_format():
    path = _tmp_json(event_study_spec_ref="EVS-PA-0001")
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_id_format" in codes


def test_invalid_options_event_risk_ref_format():
    path = _tmp_json(options_event_risk_ref="OER-PA-0001")
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_id_format" in codes


def test_invalid_instrument_universe_ref_format():
    entry = _make_valid_entry()
    entry["instrument_universe_ref"] = "IUS-PA-0001"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_id_format" in codes


def test_invalid_outcome_spec_refs_item():
    entry = _make_valid_entry()
    entry["outcome_spec_refs"] = ["OUT-PA-0001"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_id_format" in codes


def test_empty_outcome_spec_refs():
    entry = _make_valid_entry()
    entry["outcome_spec_refs"] = []
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list" in codes


def test_non_list_outcome_spec_refs():
    entry = _make_valid_entry()
    entry["outcome_spec_refs"] = "not-a-list"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list" in codes


def test_non_string_outcome_spec_refs_item():
    entry = _make_valid_entry()
    entry["outcome_spec_refs"] = [42]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list_item_type" in codes


# ---------------------------------------------------------------------------
# 7. Version tests
# ---------------------------------------------------------------------------

def test_version_zero():
    path = _tmp_json(preearnings_profile_version=0)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_value" in codes


def test_version_negative():
    path = _tmp_json(preearnings_profile_version=-1)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_value" in codes


def test_version_non_integer():
    path = _tmp_json(preearnings_profile_version=1.5)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_type" in codes


def test_version_boolean_false():
    path = _tmp_json(preearnings_profile_version=False)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_type" in codes


def test_version_string():
    path = _tmp_json(preearnings_profile_version="1")
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_type" in codes


# ---------------------------------------------------------------------------
# 8. Enum tests
# ---------------------------------------------------------------------------

def test_invalid_session_anchor_policy():
    path = _tmp_json(session_anchor_policy="bm_announcement")
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_enum" in codes


def test_invalid_earnings_time_reference():
    path = _tmp_json(earnings_time_reference="extended_hours_only")
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_enum" in codes


def test_invalid_gap_exposure_policy():
    path = _tmp_json(gap_exposure_policy="hold_overnight")
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_enum" in codes


def test_invalid_iv_regime_filter():
    entry = _make_valid_entry()
    entry["iv_regime_filter"] = "iv_mid_only"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_enum" in codes


def test_invalid_session_overlap_policy():
    entry = _make_valid_entry()
    entry["session_overlap_policy"] = "first_wins"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_enum" in codes


def test_invalid_earnings_revision_policy():
    entry = _make_valid_entry()
    entry["earnings_revision_policy"] = "ignore"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_enum" in codes


# Valid edge enums (exit 0)
def test_valid_session_anchor_policy_unconfirmed():
    path = _tmp_json(session_anchor_policy="unconfirmed")
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_valid_iv_regime_filter_iv_expand_only():
    entry = _make_valid_entry()
    entry["iv_regime_filter"] = "iv_expand_only"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_valid_iv_regime_filter_iv_collapse_only():
    entry = _make_valid_entry()
    entry["iv_regime_filter"] = "iv_collapse_only"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


# Falsey non-string enum values
def test_enum_zero():
    entry = _make_valid_entry()
    entry["session_anchor_policy"] = 0
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1


def test_enum_empty_list():
    entry = _make_valid_entry()
    entry["session_anchor_policy"] = []
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1


def test_enum_empty_object():
    entry = _make_valid_entry()
    entry["session_anchor_policy"] = {}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1


def test_enum_false():
    entry = _make_valid_entry()
    entry["session_anchor_policy"] = False
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1


# ---------------------------------------------------------------------------
# 9. Required object field tests
# ---------------------------------------------------------------------------

def test_entry_dpe_policy_string():
    entry = _make_valid_entry()
    entry["entry_dpe_policy"] = "string"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_object" in codes


def test_exit_dpe_policy_string():
    entry = _make_valid_entry()
    entry["exit_dpe_policy"] = "string"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_object" in codes


def test_iv_crush_policy_string():
    entry = _make_valid_entry()
    entry["iv_crush_policy"] = "string"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_object" in codes


def test_reviewer_string():
    entry = _make_valid_entry()
    entry["reviewer"] = "string"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_object" in codes


# ---------------------------------------------------------------------------
# 10. entry_dpe_policy tests
# ---------------------------------------------------------------------------

def test_entry_dpe_policy_missing_min():
    entry = _make_valid_entry()
    del entry["entry_dpe_policy"]["entry_dpe_min"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_entry_dpe_policy_missing_max():
    entry = _make_valid_entry()
    del entry["entry_dpe_policy"]["entry_dpe_max"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_entry_dpe_policy_missing_convention():
    entry = _make_valid_entry()
    del entry["entry_dpe_policy"]["dpe_counting_convention"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_entry_dpe_policy_missing_anchor():
    entry = _make_valid_entry()
    del entry["entry_dpe_policy"]["anchor_day_policy"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_entry_dpe_min_negative():
    entry = _make_valid_entry()
    entry["entry_dpe_policy"]["entry_dpe_min"] = -1
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_value" in codes


def test_entry_dpe_max_negative():
    entry = _make_valid_entry()
    entry["entry_dpe_policy"]["entry_dpe_max"] = -1
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_value" in codes


def test_entry_dpe_min_false():
    entry = _make_valid_entry()
    entry["entry_dpe_policy"]["entry_dpe_min"] = False
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_type" in codes


def test_entry_dpe_max_false():
    entry = _make_valid_entry()
    entry["entry_dpe_policy"]["entry_dpe_max"] = False
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_type" in codes


def test_entry_dpe_invalid_convention():
    entry = _make_valid_entry()
    entry["entry_dpe_policy"]["dpe_counting_convention"] = "invalid"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_enum" in codes


def test_entry_dpe_invalid_anchor():
    entry = _make_valid_entry()
    entry["entry_dpe_policy"]["anchor_day_policy"] = "invalid"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_enum" in codes


# ---------------------------------------------------------------------------
# 11. exit_dpe_policy tests
# ---------------------------------------------------------------------------

def test_exit_dpe_policy_missing_min():
    entry = _make_valid_entry()
    del entry["exit_dpe_policy"]["exit_dpe_min"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_exit_dpe_policy_missing_max():
    entry = _make_valid_entry()
    del entry["exit_dpe_policy"]["exit_dpe_max"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_exit_dpe_policy_missing_convention():
    entry = _make_valid_entry()
    del entry["exit_dpe_policy"]["dpe_counting_convention"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_exit_dpe_policy_missing_anchor():
    entry = _make_valid_entry()
    del entry["exit_dpe_policy"]["anchor_day_policy"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_exit_dpe_min_invalid_type():
    entry = _make_valid_entry()
    entry["exit_dpe_policy"]["exit_dpe_min"] = "bad"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_type" in codes


def test_exit_dpe_max_invalid_type():
    entry = _make_valid_entry()
    entry["exit_dpe_policy"]["exit_dpe_max"] = "bad"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_type" in codes


def test_exit_dpe_invalid_convention():
    entry = _make_valid_entry()
    entry["exit_dpe_policy"]["dpe_counting_convention"] = "invalid"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_enum" in codes


def test_exit_dpe_invalid_anchor():
    entry = _make_valid_entry()
    entry["exit_dpe_policy"]["anchor_day_policy"] = "invalid"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_enum" in codes


# ---------------------------------------------------------------------------
# 12. iv_crush_policy tests
# ---------------------------------------------------------------------------

def test_iv_crush_policy_missing_window():
    entry = _make_valid_entry()
    del entry["iv_crush_policy"]["iv_crush_measurement_window"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_iv_crush_policy_missing_definition():
    entry = _make_valid_entry()
    del entry["iv_crush_policy"]["iv_crush_definition"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_iv_crush_window_string():
    entry = _make_valid_entry()
    entry["iv_crush_policy"]["iv_crush_measurement_window"] = "bad"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_object" in codes


def test_iv_crush_window_empty_object():
    entry = _make_valid_entry()
    entry["iv_crush_policy"]["iv_crush_measurement_window"] = {}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_iv_crush_window_missing_start():
    entry = _make_valid_entry()
    del entry["iv_crush_policy"]["iv_crush_measurement_window"]["start"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_iv_crush_window_missing_end():
    entry = _make_valid_entry()
    del entry["iv_crush_policy"]["iv_crush_measurement_window"]["end"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_iv_crush_window_missing_unit():
    entry = _make_valid_entry()
    del entry["iv_crush_policy"]["iv_crush_measurement_window"]["unit"]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_iv_crush_window_unit_invalid():
    entry = _make_valid_entry()
    entry["iv_crush_policy"]["iv_crush_measurement_window"]["unit"] = "hours"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_enum" in codes


def test_iv_crush_window_unit_dpe_valid():
    entry = _make_valid_entry()
    entry["iv_crush_policy"]["iv_crush_measurement_window"]["unit"] = "dpe"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_iv_crush_window_unit_sessions_valid():
    entry = _make_valid_entry()
    entry["iv_crush_policy"]["iv_crush_measurement_window"]["unit"] = "sessions"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_iv_crush_window_unit_calendar_days_valid():
    entry = _make_valid_entry()
    entry["iv_crush_policy"]["iv_crush_measurement_window"]["unit"] = "calendar_days"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_iv_crush_window_start_false():
    entry = _make_valid_entry()
    entry["iv_crush_policy"]["iv_crush_measurement_window"]["start"] = False
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_type" in codes


def test_iv_crush_window_end_false():
    entry = _make_valid_entry()
    entry["iv_crush_policy"]["iv_crush_measurement_window"]["end"] = False
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_type" in codes


# ---------------------------------------------------------------------------
# 13. Optional object tests
# ---------------------------------------------------------------------------

def test_dpe_calendar_policy_string():
    entry = _make_valid_entry()
    entry["dpe_calendar_policy"] = "oops"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_object" in codes


def test_gap_historical_policy_string():
    entry = _make_valid_entry()
    entry["gap_historical_policy"] = "oops"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_object" in codes


def test_earnings_size_filter_string():
    entry = _make_valid_entry()
    entry["earnings_size_filter"] = "oops"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_object" in codes


def test_dpe_calendar_weekend_handling_invalid():
    entry = _make_valid_entry()
    entry["dpe_calendar_policy"] = {"weekend_handling": "bad"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_enum" in codes


def test_dpe_calendar_holiday_handling_invalid():
    entry = _make_valid_entry()
    entry["dpe_calendar_policy"] = {"holiday_handling": "bad"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_enum" in codes


def test_gap_historical_direction_invalid():
    entry = _make_valid_entry()
    entry["gap_historical_policy"] = {"gap_direction_filter": "bad"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_enum" in codes


def test_earnings_size_revenue_invalid():
    entry = _make_valid_entry()
    entry["earnings_size_filter"] = {"revenue_behavior": "bad"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_enum" in codes


# Valid nested enum values
def test_valid_dpe_calendar_weekend_handling_skip():
    entry = _make_valid_entry()
    entry["dpe_calendar_policy"] = {"weekend_handling": "skip"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_valid_dpe_calendar_weekend_handling_adjust():
    entry = _make_valid_entry()
    entry["dpe_calendar_policy"] = {"weekend_handling": "adjust"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_valid_gap_historical_direction_up():
    entry = _make_valid_entry()
    entry["gap_historical_policy"] = {"gap_direction_filter": "up"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_valid_gap_historical_direction_down():
    entry = _make_valid_entry()
    entry["gap_historical_policy"] = {"gap_direction_filter": "down"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_valid_gap_historical_direction_both():
    entry = _make_valid_entry()
    entry["gap_historical_policy"] = {"gap_direction_filter": "both"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_valid_earnings_size_revenue_beat():
    entry = _make_valid_entry()
    entry["earnings_size_filter"] = {"revenue_behavior": "beat"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_valid_earnings_size_revenue_miss():
    entry = _make_valid_entry()
    entry["earnings_size_filter"] = {"revenue_behavior": "miss"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_valid_earnings_size_revenue_in_line():
    entry = _make_valid_entry()
    entry["earnings_size_filter"] = {"revenue_behavior": "in_line"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_valid_earnings_size_revenue_any():
    entry = _make_valid_entry()
    entry["earnings_size_filter"] = {"revenue_behavior": "any"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


# ---------------------------------------------------------------------------
# 14. Numeric bounds tests
# ---------------------------------------------------------------------------

def test_minimum_iv_rank_too_low():
    entry = _make_valid_entry()
    entry["minimum_iv_rank"] = -0.1
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_value" in codes


def test_minimum_iv_rank_too_high():
    entry = _make_valid_entry()
    entry["minimum_iv_rank"] = 1.5
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_value" in codes


def test_minimum_iv_rank_boolean():
    entry = _make_valid_entry()
    entry["minimum_iv_rank"] = False
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_type" in codes


def test_minimum_iv_rank_zero_valid():
    entry = _make_valid_entry()
    entry["minimum_iv_rank"] = 0
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_minimum_iv_rank_one_valid():
    entry = _make_valid_entry()
    entry["minimum_iv_rank"] = 1
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


# ---------------------------------------------------------------------------
# 15. Optional reference array tests
# ---------------------------------------------------------------------------

def test_runner_output_refs_empty():
    entry = _make_valid_entry()
    entry["runner_output_refs"] = []
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list" in codes


def test_runner_output_refs_non_list():
    entry = _make_valid_entry()
    entry["runner_output_refs"] = "bad"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list" in codes


def test_runner_output_refs_non_string_item():
    entry = _make_valid_entry()
    entry["runner_output_refs"] = [42]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list_item_type" in codes


def test_review_packet_refs_empty():
    entry = _make_valid_entry()
    entry["review_packet_refs"] = []
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list" in codes


def test_review_packet_refs_non_list():
    entry = _make_valid_entry()
    entry["review_packet_refs"] = "bad"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list" in codes


def test_review_packet_refs_non_string_item():
    entry = _make_valid_entry()
    entry["review_packet_refs"] = [42]
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list_item_type" in codes


# ---------------------------------------------------------------------------
# 16. extension_hooks tests
# ---------------------------------------------------------------------------

def test_extension_hooks_string():
    entry = _make_valid_entry()
    entry["extension_hooks"] = "bad"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_object" in codes


def test_extension_hooks_unknown_field():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"pbo_estimate": 1}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_field" in codes


def test_extension_hooks_domain_profile_empty():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"domain_profile_extension_refs": []}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list" in codes


def test_extension_hooks_runner_output_empty():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"runner_output_extension_refs": []}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list" in codes


def test_extension_hooks_review_packet_empty():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"review_packet_extension_refs": []}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list" in codes


def test_extension_hooks_options_event_risk_empty():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"options_event_risk_extension_refs": []}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list" in codes


def test_extension_hooks_event_study_empty():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"event_study_extension_refs": []}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list" in codes


def test_extension_hooks_domain_profile_non_list():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"domain_profile_extension_refs": "bad"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list" in codes


def test_extension_hooks_runner_output_non_list():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"runner_output_extension_refs": "bad"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list" in codes


def test_extension_hooks_review_packet_non_list():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"review_packet_extension_refs": "bad"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list" in codes


def test_extension_hooks_options_event_risk_non_list():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"options_event_risk_extension_refs": "bad"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list" in codes


def test_extension_hooks_event_study_non_list():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"event_study_extension_refs": "bad"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list" in codes


def test_extension_hooks_domain_profile_non_string_item():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"domain_profile_extension_refs": [42]}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list_item_type" in codes


def test_extension_hooks_runner_output_non_string_item():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"runner_output_extension_refs": [42]}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list_item_type" in codes


def test_extension_hooks_review_packet_non_string_item():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"review_packet_extension_refs": [42]}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list_item_type" in codes


def test_extension_hooks_options_event_risk_non_string_item():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"options_event_risk_extension_refs": [42]}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list_item_type" in codes


def test_extension_hooks_event_study_non_string_item():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"event_study_extension_refs": [42]}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_list_item_type" in codes


def test_extension_hooks_domain_profile_valid():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"domain_profile_extension_refs": ["DPEP-1"]}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_extension_hooks_runner_output_valid():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"runner_output_extension_refs": ["RUNO-1"]}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_extension_hooks_review_packet_valid():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"review_packet_extension_refs": ["RVPK-1"]}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_extension_hooks_options_event_risk_valid():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"options_event_risk_extension_refs": ["OERE-1"]}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


def test_extension_hooks_event_study_valid():
    entry = _make_valid_entry()
    entry["extension_hooks"] = {"event_study_extension_refs": ["EVSE-1"]}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 0


# ---------------------------------------------------------------------------
# 17. reviewer tests
# ---------------------------------------------------------------------------

def test_reviewer_empty_object():
    entry = _make_valid_entry()
    entry["reviewer"] = {}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_reviewer_name_missing():
    entry = _make_valid_entry()
    entry["reviewer"] = {"name": None}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_reviewer_name_empty():
    entry = _make_valid_entry()
    entry["reviewer"] = {"name": ""}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_reviewer_name_whitespace():
    entry = _make_valid_entry()
    entry["reviewer"] = {"name": "   "}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "missing_required_field" in codes


def test_reviewer_name_number():
    entry = _make_valid_entry()
    entry["reviewer"] = {"name": 123}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, str(path))}
    assert "invalid_type" in codes


# ---------------------------------------------------------------------------
# 18. Boundary/additionalProperties tests
# ---------------------------------------------------------------------------

BOUNDARY_TESTS = [
    ("test_boundary_pbo_estimate", {"pbo_estimate": 0.5}),
    ("test_boundary_dsr_estimate", {"dsr_estimate": 0.5}),
    ("test_boundary_sharpe_haircut", {"sharpe_haircut": 0.1}),
    ("test_boundary_overfit_discount", {"overfit_discount": 0.1}),
    ("test_boundary_selected_variant_id", {"selected_variant_id": "V-1"}),
    ("test_boundary_n_tried", {"n_tried": 10}),
    ("test_boundary_trial_family_id", {"trial_family_id": "TF-1"}),
    ("test_boundary_review_packet_decision", {"review_packet_decision": "accept"}),
    ("test_boundary_live_trading_enabled", {"live_trading_enabled": True}),
    ("test_boundary_production_execution_endpoint", {"production_execution_endpoint": "https://"}),
    ("test_boundary_ivolatility_table_name", {"ivolatility_table_name": "iv_table"}),
    ("test_boundary_provider_table_name", {"provider_table_name": "prov_table"}),
]

for test_name, extra in BOUNDARY_TESTS:
    def make_test(extra):
        def test_boundary():
            entry = _make_valid_entry()
            entry.update(extra)
            f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            json.dump(entry, f)
            f.close()
            path = Path(f.name)
            code, out, _ = run_validator(["--format", "json", str(path)])
            assert code == 1
            data = json.loads(out)
            codes = {b["code"] for b in _blockers_for_path(data, str(path))}
            assert "invalid_field" in codes
        return test_boundary
    globals()[test_name] = make_test(extra)


# ---------------------------------------------------------------------------
# 19. created_at tests
# ---------------------------------------------------------------------------

def test_created_at_number():
    entry = _make_valid_entry()
    entry["created_at"] = 123
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1


def test_created_at_object():
    entry = _make_valid_entry()
    entry["created_at"] = {}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1


def test_created_at_boolean():
    entry = _make_valid_entry()
    entry["created_at"] = False
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1


def test_created_at_empty_string():
    entry = _make_valid_entry()
    entry["created_at"] = ""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1


def test_created_at_whitespace():
    entry = _make_valid_entry()
    entry["created_at"] = "   "
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    path = Path(f.name)
    code, out, _ = run_validator(["--format", "json", str(path)])
    assert code == 1


# ---------------------------------------------------------------------------
# 20. --format json tests
# ---------------------------------------------------------------------------

def test_json_format_valid_emits_valid_json():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "valid_minimal.json")])
    assert code == 0
    data = json.loads(out)  # Must be valid JSON
    assert "files" in data


def test_json_format_invalid_emits_valid_json():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_missing_required.json")])
    assert code == 1
    data = json.loads(out)  # Must be valid JSON even when invalid
    assert "files" in data


def test_json_output_has_required_fields():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "valid_minimal.json")])
    assert code == 0
    data = json.loads(out)
    assert "files" in data
    assert "blockers_count" in data["files"][str(FIXTURES / "valid_minimal.json")]
    assert "blockers" in data["files"][str(FIXTURES / "valid_minimal.json")]
    assert "total_blockers" in data


def test_text_mode_includes_ok_fail():
    code, out, _ = run_validator([str(FIXTURES / "valid_minimal.json")])
    assert "[OK]" in out
    code2, out2, _ = run_validator([str(FIXTURES / "invalid_missing_required.json")])
    assert "[FAIL]" in out2
