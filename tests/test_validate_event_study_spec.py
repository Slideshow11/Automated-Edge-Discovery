"""Tests for scripts/local/validate_event_study_spec.py"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(".")
SCRIPT = REPO / "scripts" / "local" / "validate_event_study_spec.py"
FIXTURES = REPO / "fixtures" / "event_study_spec_v1"


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
    """Call validate_event_study_spec.main() in-process, return (code, stdout, stderr)."""
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
        from scripts.local.validate_event_study_spec import main
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


# ---------------------------------------------------------------------------
# Helpers for inline tests
# ---------------------------------------------------------------------------

def _make_valid_entry(**overrides):
    """Return a fully-valid EventStudySpec v1 record, with overrides applied."""
    base = {
        "event_study_spec_id": "EVS-2026-0001",
        "event_study_version": 1,
        "event_family": "earnings",
        "event_source_refs": ["DM-2026-0001"],
        "event_anchor_policy": "event_timestamp",
        "event_timestamp_policy": "date_only_allowed",
        "decision_timestamp_policy": "before_event_publication",
        "pre_event_window": {
            "start_offset": -5,
            "end_offset": -1,
            "units": "calendar_days",
            "include_event_anchor": False,
            "window_role": "baseline",
        },
        "post_event_window": {
            "start_offset": 0,
            "end_offset": 20,
            "units": "calendar_days",
            "include_event_anchor": True,
            "window_role": "measurement",
        },
        "leakage_policy": "strict_no_lookahead",
        "event_deduplication_policy": "keep_first",
        "event_collision_policy": "allow_overlapping_windows",
        "missing_event_time_policy": "reject_event",
        "calendar_policy": "trading_days",
        "created_at": "2026-05-01T00:00:00Z",
        "reviewer": {"name": "dr_elliot_review_2026"},
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
# Valid fixture tests
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
# Invalid fixture tests — one per fixture
# ---------------------------------------------------------------------------

def test_invalid_missing_required():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_missing_required.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_missing_required.json")}
    assert "missing_required_field" in codes


def test_invalid_event_study_spec_id():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_event_study_spec_id.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_event_study_spec_id.json")}
    assert "invalid_id_format" in codes


def test_invalid_event_family():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_event_family.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_event_family.json")}
    assert "invalid_enum" in codes


def test_invalid_event_source_refs_empty():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_event_source_refs_empty.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_event_source_refs_empty.json")}
    assert "invalid_list" in codes


def test_invalid_event_anchor_policy():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_event_anchor_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_event_anchor_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_event_timestamp_policy():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_event_timestamp_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_event_timestamp_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_decision_timestamp_policy():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_decision_timestamp_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_decision_timestamp_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_leakage_policy():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_leakage_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_leakage_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_event_deduplication_policy():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_event_deduplication_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_event_deduplication_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_event_collision_policy():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_event_collision_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_event_collision_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_missing_event_time_policy():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_missing_event_time_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_missing_event_time_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_calendar_policy():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_calendar_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_calendar_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_pre_event_window_missing_field():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_pre_event_window_missing_field.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_pre_event_window_missing_field.json")}
    assert "missing_required_field" in codes


def test_invalid_post_event_window_missing_field():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_post_event_window_missing_field.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_post_event_window_missing_field.json")}
    assert "missing_required_field" in codes


def test_invalid_window_units():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_window_units.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_window_units.json")}
    assert "invalid_enum" in codes


def test_invalid_window_include_anchor_type():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_window_include_anchor_type.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_window_include_anchor_type.json")}
    assert "invalid_boolean" in codes


def test_invalid_reviewer_type():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_reviewer_type.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_reviewer_type.json")}
    assert "invalid_object" in codes


def test_invalid_reviewer_empty_object():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_reviewer_empty_object.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_reviewer_empty_object.json")}
    assert "missing_required_field" in codes


def test_invalid_outcome_spec_ref():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_outcome_spec_ref.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_outcome_spec_ref.json")}
    assert "invalid_id_format" in codes


def test_invalid_instrument_universe_ref():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_instrument_universe_ref.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_instrument_universe_ref.json")}
    assert "invalid_id_format" in codes


def test_invalid_extension_hooks_unknown_field():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_extension_hooks_unknown_field.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_extension_hooks_unknown_field.json")}
    assert "invalid_field" in codes


def test_invalid_boundary_field():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_boundary_field.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_boundary_field.json")}
    assert "invalid_field" in codes


# ---------------------------------------------------------------------------
# Parse / read error exit 2
# ---------------------------------------------------------------------------

def test_invalid_json_exit_2():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    f.write("{invalid json}")
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 2, f"Expected 2, got {code}"
        # JSON parse errors produce exit 2 but no blockers are added
        data = json.loads(out)
        blockers = _blockers_for_path(data, f.name)
        assert blockers == []
    finally:
        Path(f.name).unlink()


# ---------------------------------------------------------------------------
# Required field tests
# ---------------------------------------------------------------------------

def test_missing_required_field():
    entry = {k: v for k, v in _make_valid_entry().items() if k != "event_study_spec_id"}
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
    path = _tmp_json(event_study_spec_id=None)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_empty_string_required_field():
    path = _tmp_json(event_study_spec_id="")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_whitespace_only_required_field():
    path = _tmp_json(event_study_spec_id="   ")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# ID validation tests
# ---------------------------------------------------------------------------

def test_invalid_event_study_spec_id_format():
    path = _tmp_json(event_study_spec_id="EVS-PA-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_non_string_event_study_spec_id():
    path = _tmp_json(event_study_spec_id=42)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_invalid_outcome_spec_refs_item():
    path = _tmp_json(outcome_spec_refs=["OUT-PA-0001"])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_invalid_instrument_universe_refs_item():
    path = _tmp_json(instrument_universe_refs=["IUS-PA-0001"])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# event_study_version tests
# ---------------------------------------------------------------------------

def test_event_study_version_zero():
    path = _tmp_json(event_study_version=0)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_event_study_version_negative():
    path = _tmp_json(event_study_version=-1)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_event_study_version_non_integer():
    path = _tmp_json(event_study_version=1.5)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_event_study_version_boolean():
    path = _tmp_json(event_study_version=False)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# Enum validation tests
# ---------------------------------------------------------------------------

def test_invalid_event_family():
    path = _tmp_json(event_family="pre_earnings_volatility")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_event_anchor_policy():
    path = _tmp_json(event_anchor_policy="pre_event_close")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_event_timestamp_policy():
    path = _tmp_json(event_timestamp_policy="time_required")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_decision_timestamp_policy():
    path = _tmp_json(decision_timestamp_policy="post_event_entry")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_leakage_policy():
    path = _tmp_json(leakage_policy="no_lookahead")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_event_deduplication_policy():
    path = _tmp_json(event_deduplication_policy="earliest_wins")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_event_collision_policy():
    path = _tmp_json(event_collision_policy="reject_collisions")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_missing_event_time_policy():
    path = _tmp_json(missing_event_time_policy="discard_ambiguous")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_calendar_policy():
    path = _tmp_json(calendar_policy="business_days")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


# Falsey non-string enum values
def test_falsey_enum_zero():
    path = _tmp_json(event_family=0)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
    finally:
        path.unlink()


def test_falsey_enum_empty_list():
    path = _tmp_json(event_family=[])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
    finally:
        path.unlink()


def test_falsey_enum_empty_object():
    path = _tmp_json(event_family={})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
    finally:
        path.unlink()


def test_falsey_enum_false():
    path = _tmp_json(event_family=False)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# Array / list field tests
# ---------------------------------------------------------------------------

def test_event_source_refs_non_list():
    path = _tmp_json(event_source_refs="DM-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_event_source_refs_empty():
    path = _tmp_json(event_source_refs=[])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_event_source_refs_item_non_string():
    path = _tmp_json(event_source_refs=[42])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list_item_type" in codes
    finally:
        path.unlink()


def test_domain_profile_refs_non_list():
    path = _tmp_json(domain_profile_refs="DMP-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_runner_output_refs_non_list():
    path = _tmp_json(runner_output_refs="RO-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_review_packet_refs_non_list():
    path = _tmp_json(review_packet_refs="RP-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_outcome_spec_refs_non_list():
    path = _tmp_json(outcome_spec_refs="OUT-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_instrument_universe_refs_non_list():
    path = _tmp_json(instrument_universe_refs="IUS-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_optional_ref_array_item_non_string():
    path = _tmp_json(domain_profile_refs=[42])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list_item_type" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# Window object tests
# ---------------------------------------------------------------------------

def test_pre_event_window_non_object():
    path = _tmp_json(pre_event_window="pre_event")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_post_event_window_non_object():
    path = _tmp_json(post_event_window="post_event")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_pre_event_window_missing_start_offset():
    win = _make_valid_entry()["pre_event_window"].copy()
    del win["start_offset"]
    path = _tmp_json(pre_event_window=win)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_pre_event_window_missing_end_offset():
    win = _make_valid_entry()["pre_event_window"].copy()
    del win["end_offset"]
    path = _tmp_json(pre_event_window=win)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_pre_event_window_missing_units():
    win = _make_valid_entry()["pre_event_window"].copy()
    del win["units"]
    path = _tmp_json(pre_event_window=win)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_pre_event_window_missing_include_event_anchor():
    win = _make_valid_entry()["pre_event_window"].copy()
    del win["include_event_anchor"]
    path = _tmp_json(pre_event_window=win)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_pre_event_window_missing_window_role():
    win = _make_valid_entry()["pre_event_window"].copy()
    del win["window_role"]
    path = _tmp_json(pre_event_window=win)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_window_start_offset_non_integer():
    win = _make_valid_entry()["pre_event_window"].copy()
    win["start_offset"] = "5"
    path = _tmp_json(pre_event_window=win)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_window_end_offset_non_integer():
    win = _make_valid_entry()["pre_event_window"].copy()
    win["end_offset"] = "1"
    path = _tmp_json(pre_event_window=win)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_window_boolean_offset():
    win = _make_valid_entry()["pre_event_window"].copy()
    win["start_offset"] = True
    path = _tmp_json(pre_event_window=win)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_window_units_hours():
    win = _make_valid_entry()["pre_event_window"].copy()
    win["units"] = "hours"
    path = _tmp_json(pre_event_window=win)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_window_include_event_anchor_string():
    win = _make_valid_entry()["pre_event_window"].copy()
    win["include_event_anchor"] = "true"
    path = _tmp_json(pre_event_window=win)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_boolean" in codes
    finally:
        path.unlink()


def test_window_role_empty_string():
    win = _make_valid_entry()["pre_event_window"].copy()
    win["window_role"] = ""
    path = _tmp_json(pre_event_window=win)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_window_start_gt_end():
    win = _make_valid_entry()["pre_event_window"].copy()
    win["start_offset"] = -1
    win["end_offset"] = -5
    path = _tmp_json(pre_event_window=win)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_post_event_window_start_offset_zero_valid():
    """post_event_window.start_offset = 0 is allowed."""
    path = _tmp_json(post_event_window={
        "start_offset": 0,
        "end_offset": 20,
        "units": "calendar_days",
        "include_event_anchor": True,
        "window_role": "measurement",
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# Reviewer tests
# ---------------------------------------------------------------------------

def test_reviewer_non_object():
    path = _tmp_json(reviewer="dr_elliot")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_reviewer_missing_name():
    path = _tmp_json(reviewer={})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_reviewer_name_empty_string():
    path = _tmp_json(reviewer={"name": ""})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_reviewer_name_whitespace_only():
    path = _tmp_json(reviewer={"name": "   "})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_reviewer_name_non_string():
    path = _tmp_json(reviewer={"name": 42})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# extension_hooks tests
# ---------------------------------------------------------------------------

def test_extension_hooks_non_object():
    path = _tmp_json(extension_hooks=["extension_ref"])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_extension_hooks_unknown_field():
    path = _tmp_json(extension_hooks={"pbo_estimate": 0.05})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_extension_hooks_domain_profile_extension_refs_valid():
    path = _tmp_json(extension_hooks={
        "domain_profile_extension_refs": ["DMP-2026-0001"],
        "runner_output_extension_refs": [],
        "review_packet_extension_refs": [],
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_extension_hooks_runner_output_extension_refs_valid():
    path = _tmp_json(extension_hooks={
        "domain_profile_extension_refs": [],
        "runner_output_extension_refs": ["RO-2026-0001"],
        "review_packet_extension_refs": [],
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_extension_hooks_review_packet_extension_refs_valid():
    path = _tmp_json(extension_hooks={
        "domain_profile_extension_refs": [],
        "runner_output_extension_refs": [],
        "review_packet_extension_refs": ["RP-2026-0001"],
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_extension_hooks_ref_array_non_list():
    path = _tmp_json(extension_hooks={
        "domain_profile_extension_refs": "DMP-2026-0001",
        "runner_output_extension_refs": [],
        "review_packet_extension_refs": [],
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_extension_hooks_ref_array_item_non_string():
    path = _tmp_json(extension_hooks={
        "domain_profile_extension_refs": [42],
        "runner_output_extension_refs": [],
        "review_packet_extension_refs": [],
    })
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list_item_type" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# Boundary / additionalProperties tests
# ---------------------------------------------------------------------------

def test_boundary_option_contract_selection():
    path = _tmp_json(option_contract_selection={"delta_target": 0.25})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_delta_target():
    path = _tmp_json(delta_target=0.25)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_expiry_rank():
    path = _tmp_json(expiry_rank=30)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_entry_dpe():
    path = _tmp_json(entry_dpe=0.45)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_exit_dpe():
    path = _tmp_json(exit_dpe=0.55)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_iv_crush():
    path = _tmp_json(iv_crush=0.30)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_gap_exposure():
    path = _tmp_json(gap_exposure=0.10)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_directional_signal():
    path = _tmp_json(directional_signal="bullish")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_ranking_score():
    path = _tmp_json(ranking_score=0.85)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_selected_variant_id():
    path = _tmp_json(selected_variant_id="VAR-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_n_tried():
    path = _tmp_json(n_tried=100)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_trial_family_id():
    path = _tmp_json(trial_family_id="TF-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_pnl():
    path = _tmp_json(pnl=0.025)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_pbo_estimate():
    path = _tmp_json(pbo_estimate=0.05)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_dsr_estimate():
    path = _tmp_json(dsr_estimate=0.03)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_review_packet_decision():
    path = _tmp_json(review_packet_decision="approved")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# created_at tests
# ---------------------------------------------------------------------------

def test_created_at_non_string_integer():
    path = _tmp_json(created_at=123)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_created_at_object():
    path = _tmp_json(created_at={})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_created_at_false():
    path = _tmp_json(created_at=False)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_created_at_empty_string():
    path = _tmp_json(created_at="")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_created_at_whitespace_only():
    path = _tmp_json(created_at="   ")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# --format json tests
# ---------------------------------------------------------------------------

def test_format_json_valid_emits_valid_json():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "valid_minimal.json")])
    assert code == 0
    data = json.loads(out)
    assert "files" in data
    assert "total_blockers" in data
    assert data["total_blockers"] == 0


def test_format_json_invalid_emits_valid_json():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_event_family.json")])
    assert code == 1
    data = json.loads(out)
    assert "files" in data
    assert "total_blockers" in data
    assert data["total_blockers"] > 0


def test_format_json_structure_has_files_and_total_blockers():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "valid_minimal.json")])
    assert code == 0
    data = json.loads(out)
    file_key = str(FIXTURES / "valid_minimal.json")
    assert file_key in data["files"]
    assert "blockers_count" in data["files"][file_key]
    assert "blockers" in data["files"][file_key]


def test_format_text_valid_emits_ok():
    code, out, _ = run_validator([str(FIXTURES / "valid_minimal.json")])
    assert code == 0
    assert "[OK]" in out


def test_format_text_invalid_emits_fail():
    code, out, _ = run_validator([str(FIXTURES / "invalid_event_family.json")])
    assert code == 1
    assert "[FAIL]" in out


# ---------------------------------------------------------------------------
# Root JSON type tests
# ---------------------------------------------------------------------------

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
