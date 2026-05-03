"""Tests for scripts/local/validate_options_event_risk_spec.py"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(".")
SCRIPT = REPO / "scripts" / "local" / "validate_options_event_risk_spec.py"
FIXTURES = REPO / "fixtures" / "options_event_risk_spec_v1"


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
    """Call validate_options_event_risk_spec.main() in-process, return (code, stdout, stderr)."""
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
        from scripts.local.validate_options_event_risk_spec import main
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

def test_invalid_boundary_field():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_boundary_field.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_boundary_field.json")}
    assert "invalid_field" in codes


def test_invalid_contract_selection_policy_type():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_contract_selection_policy_type.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_contract_selection_policy_type.json")}
    assert "invalid_object" in codes


def test_invalid_event_study_spec_ref_fixture():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_event_study_spec_ref.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_event_study_spec_ref.json")}
    assert "invalid_id_format" in codes


def test_invalid_execution_timing_policy_fixture():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_execution_timing_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_execution_timing_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_expiry_selection_policy_type():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_expiry_selection_policy_type.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_expiry_selection_policy_type.json")}
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


def test_invalid_instrument_universe_ref_fixture():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_instrument_universe_ref.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_instrument_universe_ref.json")}
    assert "invalid_id_format" in codes


def test_invalid_liquidity_policy_type():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_liquidity_policy_type.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_liquidity_policy_type.json")}
    assert "invalid_object" in codes


def test_invalid_missing_required():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_missing_required.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_missing_required.json")}
    assert "missing_required_field" in codes


def test_invalid_moneyness_selection_policy_type():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_moneyness_selection_policy_type.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_moneyness_selection_policy_type.json")}
    assert "invalid_object" in codes


def test_invalid_negative_numeric_threshold():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_negative_numeric_threshold.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_negative_numeric_threshold.json")}
    assert "invalid_value" in codes


def test_invalid_option_side_policy_fixture():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_option_side_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_option_side_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_option_universe_policy_fixture():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_option_universe_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_option_universe_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_options_event_risk_spec_id():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_options_event_risk_spec_id.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_options_event_risk_spec_id.json")}
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


def test_invalid_pricing_policy_type():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_pricing_policy_type.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_pricing_policy_type.json")}
    assert "invalid_object" in codes


def test_invalid_quote_quality_policy_type():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_quote_quality_policy_type.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_quote_quality_policy_type.json")}
    assert "invalid_object" in codes


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


def test_invalid_spread_pct_out_of_range():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_spread_pct_out_of_range.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_spread_pct_out_of_range.json")}
    assert "invalid_value" in codes


def test_invalid_strategy_structure_policy_fixture():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_strategy_structure_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_strategy_structure_policy.json")}
    assert "invalid_enum" in codes


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


# ---------------------------------------------------------------------------
# Parse/read error exit 2 tests
# ---------------------------------------------------------------------------

def test_nonexistent_file_exit_2():
    code, out, err = run_validator(["/tmp/does_not_exist_12345_oer.json"])
    assert code == 2, f"Expected 2, got {code}"


def test_invalid_json_exit_2():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    f.write("{invalid json}")
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 2, f"Expected 2, got {code}"
    finally:
        Path(f.name).unlink()


# ---------------------------------------------------------------------------
# Helpers for inline tests
# ---------------------------------------------------------------------------

def _make_valid_entry(**overrides):
    """Return a fully-valid OptionsEventRiskSpec v1 record, with overrides applied."""
    base = {
        "options_event_risk_spec_id": "OER-2026-0001",
        "options_event_risk_version": 1,
        "event_study_spec_ref": "EVS-2026-0001",
        "instrument_universe_ref": "IUS-2026-0001",
        "outcome_spec_refs": ["OUT-2026-0001"],
        "option_universe_policy": "listed_equity_options",
        "contract_selection_policy": {
            "selection_method": "delta_bucket",
            "delta_targets": [0.50, 0.30, 0.10],
            "option_side": "puts_only",
            "contract_count_limit": 3,
        },
        "expiry_selection_policy": {
            "selection_method": "nearest_after_event",
            "dte_range": {
                "min_dte": 5,
                "max_dte": 60,
            },
        },
        "moneyness_selection_policy": {
            "target_type": "delta_targeted",
            "delta_target": 0.30,
        },
        "option_side_policy": "puts_only",
        "strategy_structure_policy": "single_leg",
        "liquidity_policy": {
            "min_option_price": 0.05,
            "max_bid_ask_spread_pct": 0.25,
            "min_open_interest": 50,
            "require_nbbo": True,
            "stale_quote_policy": "reject",
        },
        "pricing_policy": {
            "fill_price_basis": "conservative_fill",
            "spread_penalty_bps": 25,
            "quote_timestamp_policy": "decision_time",
        },
        "execution_timing_policy": "decision_timestamp",
        "gap_exposure_policy": "exit_before_event_anchor",
        "quote_quality_policy": {
            "quality_method": "require_bid_ask",
            "max_quote_age_seconds": 300,
            "require_open_interest": True,
        },
        "created_at": "2026-05-02T00:00:00Z",
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
# Required field tests
# ---------------------------------------------------------------------------

def test_missing_required_field():
    entry = {k: v for k, v in _make_valid_entry().items() if k != "options_event_risk_spec_id"}
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
    path = _tmp_json(options_event_risk_spec_id=None)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_empty_string_required_field():
    path = _tmp_json(options_event_risk_spec_id="")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_whitespace_only_required_field():
    path = _tmp_json(options_event_risk_spec_id="   ")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# ID/ref validation tests
# ---------------------------------------------------------------------------

def test_invalid_options_event_risk_spec_id_format():
    path = _tmp_json(options_event_risk_spec_id="OER-PA-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_non_string_options_event_risk_spec_id():
    path = _tmp_json(options_event_risk_spec_id=42)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_invalid_event_study_spec_ref():
    path = _tmp_json(event_study_spec_ref="EVS-PA-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_invalid_instrument_universe_ref():
    path = _tmp_json(instrument_universe_ref="IUS-PA-0001")
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


def test_outcome_spec_refs_empty():
    path = _tmp_json(outcome_spec_refs=[])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_outcome_spec_refs_item_non_string():
    path = _tmp_json(outcome_spec_refs=[42])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list_item_type" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# options_event_risk_version tests
# ---------------------------------------------------------------------------

def test_options_event_risk_version_zero():
    path = _tmp_json(options_event_risk_version=0)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_options_event_risk_version_negative():
    path = _tmp_json(options_event_risk_version=-1)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_options_event_risk_version_non_integer():
    path = _tmp_json(options_event_risk_version=1.5)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_options_event_risk_version_boolean():
    path = _tmp_json(options_event_risk_version=False)
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

def test_invalid_option_universe_policy():
    path = _tmp_json(option_universe_policy="manual")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_option_side_policy():
    path = _tmp_json(option_side_policy="put_spread")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_strategy_structure_policy():
    path = _tmp_json(strategy_structure_policy="straddle")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_execution_timing_policy():
    path = _tmp_json(execution_timing_policy="close_only")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_gap_exposure_policy():
    path = _tmp_json(gap_exposure_policy="hedge_through")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_falsey_non_string_option_universe_policy_0():
    path = _tmp_json(option_universe_policy=0)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
    finally:
        path.unlink()


def test_falsey_non_string_option_universe_policy_empty_list():
    path = _tmp_json(option_universe_policy=[])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
    finally:
        path.unlink()


def test_falsey_non_string_option_universe_policy_false():
    path = _tmp_json(option_universe_policy=False)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# Required policy object type tests
# ---------------------------------------------------------------------------

def test_contract_selection_policy_non_object():
    path = _tmp_json(contract_selection_policy="delta_bucket")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_expiry_selection_policy_non_object():
    path = _tmp_json(expiry_selection_policy="nearest")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_moneyness_selection_policy_non_object():
    path = _tmp_json(moneyness_selection_policy="delta_targeted")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_liquidity_policy_non_object():
    path = _tmp_json(liquidity_policy=[])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_pricing_policy_non_object():
    path = _tmp_json(pricing_policy="mid")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_quote_quality_policy_non_object():
    path = _tmp_json(quote_quality_policy=True)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# PR #125 focus tests: selection_priority, required nested fields, quality_method enum
# ---------------------------------------------------------------------------

def test_selection_priority_list_of_strings():
    """selection_priority as list of strings — valid."""
    path = _tmp_json(
        contract_selection_policy={
            "selection_method": "delta_bucket",
            "selection_priority": ["nearest_expiry", "highest_oi"],
        }
    )
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_selection_priority_scalar_string():
    """selection_priority as scalar string — invalid."""
    path = _tmp_json(
        contract_selection_policy={
            "selection_method": "delta_bucket",
            "selection_priority": "nearest_expiry",
        }
    )
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_selection_priority_non_string_item():
    """selection_priority list with non-string item — invalid."""
    path = _tmp_json(
        contract_selection_policy={
            "selection_method": "delta_bucket",
            "selection_priority": ["nearest_expiry", 123],
        }
    )
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list_item_type" in codes
    finally:
        path.unlink()


def test_contract_selection_policy_selection_method_required():
    """Empty contract_selection_policy — missing required nested selection_method."""
    path = _tmp_json(contract_selection_policy={})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_expiry_selection_policy_selection_method_required():
    """Empty expiry_selection_policy — missing required nested selection_method."""
    path = _tmp_json(expiry_selection_policy={})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_moneyness_selection_policy_target_type_required():
    """Empty moneyness_selection_policy — missing required nested target_type."""
    path = _tmp_json(moneyness_selection_policy={})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_quality_method_require_bid_ask():
    path = _tmp_json(quote_quality_policy={"quality_method": "require_bid_ask"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_quality_method_allow_mid_only():
    path = _tmp_json(quote_quality_policy={"quality_method": "allow_mid_only"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_quality_method_reject_stale_quotes():
    path = _tmp_json(quote_quality_policy={"quality_method": "reject_stale_quotes"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_quality_method_require_open_interest():
    path = _tmp_json(quote_quality_policy={"quality_method": "require_open_interest"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_quality_method_custom():
    path = _tmp_json(quote_quality_policy={"quality_method": "custom"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_quality_method_invalid():
    path = _tmp_json(quote_quality_policy={"quality_method": "foo"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_quality_method_non_string():
    path = _tmp_json(quote_quality_policy={"quality_method": 123})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_quality_method_empty_string():
    path = _tmp_json(quote_quality_policy={"quality_method": ""})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_quality_method_whitespace_only():
    path = _tmp_json(quote_quality_policy={"quality_method": "   "})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# contract_selection_policy sub-field tests
# ---------------------------------------------------------------------------

def test_csp_selection_method_non_string():
    path = _tmp_json(contract_selection_policy={"selection_method": 42})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_csp_selection_method_empty():
    path = _tmp_json(contract_selection_policy={"selection_method": ""})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_csp_selection_method_whitespace():
    path = _tmp_json(contract_selection_policy={"selection_method": "   "})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_csp_delta_targets_non_list():
    path = _tmp_json(contract_selection_policy={"selection_method": "delta_bucket", "delta_targets": "0.5"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_csp_delta_targets_item_non_number():
    path = _tmp_json(contract_selection_policy={"selection_method": "delta_bucket", "delta_targets": [0.5, "high"]})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list_item_type" in codes
    finally:
        path.unlink()


def test_csp_delta_targets_boolean_item():
    path = _tmp_json(contract_selection_policy={"selection_method": "delta_bucket", "delta_targets": [True, 0.3]})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list_item_type" in codes
    finally:
        path.unlink()


def test_csp_contract_count_limit_negative():
    path = _tmp_json(contract_selection_policy={"selection_method": "delta_bucket", "contract_count_limit": -1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_csp_contract_count_limit_bool():
    path = _tmp_json(contract_selection_policy={"selection_method": "delta_bucket", "contract_count_limit": True})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_csp_tie_break_policy_non_string():
    path = _tmp_json(contract_selection_policy={"selection_method": "delta_bucket", "tie_break_policy": 1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# expiry_selection_policy sub-field tests
# ---------------------------------------------------------------------------

def test_esp_selection_method_non_string():
    path = _tmp_json(expiry_selection_policy={"selection_method": 42})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_esp_selection_method_empty():
    path = _tmp_json(expiry_selection_policy={"selection_method": ""})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_esp_min_dte_negative():
    path = _tmp_json(expiry_selection_policy={"selection_method": "nearest_after_event", "min_dte": -1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_esp_min_dte_bool():
    path = _tmp_json(expiry_selection_policy={"selection_method": "nearest_after_event", "min_dte": True})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_esp_max_dte_negative():
    path = _tmp_json(expiry_selection_policy={"selection_method": "nearest_after_event", "max_dte": -5})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_esp_max_dte_bool():
    path = _tmp_json(expiry_selection_policy={"selection_method": "nearest_after_event", "max_dte": False})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_esp_expiry_ranks_non_list():
    path = _tmp_json(expiry_selection_policy={"selection_method": "nearest_after_event", "expiry_ranks": "nearest"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_esp_expiry_ranks_item_negative():
    path = _tmp_json(expiry_selection_policy={"selection_method": "nearest_after_event", "expiry_ranks": [1, -3, 5]})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_esp_expiry_ranks_item_bool():
    path = _tmp_json(expiry_selection_policy={"selection_method": "nearest_after_event", "expiry_ranks": [1, True, 3]})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list_item_type" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# moneyness_selection_policy sub-field tests
# ---------------------------------------------------------------------------

def test_msp_target_type_non_string():
    path = _tmp_json(moneyness_selection_policy={"target_type": 42})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_msp_target_type_empty():
    path = _tmp_json(moneyness_selection_policy={"target_type": ""})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_msp_percent_moneyness_negative():
    path = _tmp_json(moneyness_selection_policy={"target_type": "delta_targeted", "percent_moneyness": -0.1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_msp_percent_moneyness_bool():
    path = _tmp_json(moneyness_selection_policy={"target_type": "delta_targeted", "percent_moneyness": True})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# liquidity_policy sub-field tests
# ---------------------------------------------------------------------------

def test_lp_min_option_price_negative():
    path = _tmp_json(liquidity_policy={"min_option_price": -0.01})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_lp_max_option_price_negative():
    path = _tmp_json(liquidity_policy={"max_option_price": -1.0})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_lp_min_open_interest_negative():
    path = _tmp_json(liquidity_policy={"min_open_interest": -10})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_lp_min_open_interest_bool():
    path = _tmp_json(liquidity_policy={"min_open_interest": True})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_lp_min_volume_negative():
    path = _tmp_json(liquidity_policy={"min_volume": -5})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_lp_min_volume_bool():
    path = _tmp_json(liquidity_policy={"min_volume": False})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_lp_max_bid_ask_spread_abs_negative():
    path = _tmp_json(liquidity_policy={"max_bid_ask_spread_abs": -0.01})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_lp_max_bid_ask_spread_abs_bool():
    path = _tmp_json(liquidity_policy={"max_bid_ask_spread_abs": True})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_lp_max_bid_ask_spread_pct_below_0():
    path = _tmp_json(liquidity_policy={"max_bid_ask_spread_pct": -0.01})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_lp_max_bid_ask_spread_pct_above_1():
    path = _tmp_json(liquidity_policy={"max_bid_ask_spread_pct": 1.5})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_lp_max_bid_ask_spread_pct_bool():
    path = _tmp_json(liquidity_policy={"max_bid_ask_spread_pct": True})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_lp_max_quote_age_seconds_negative():
    path = _tmp_json(liquidity_policy={"max_quote_age_seconds": -1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_lp_max_quote_age_seconds_bool():
    path = _tmp_json(liquidity_policy={"max_quote_age_seconds": False})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_lp_require_nbbo_non_boolean():
    path = _tmp_json(liquidity_policy={"require_nbbo": "yes"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_boolean" in codes
    finally:
        path.unlink()


def test_lp_stale_quote_policy_non_string():
    path = _tmp_json(liquidity_policy={"stale_quote_policy": 1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_lp_missing_greeks_policy_non_string():
    path = _tmp_json(liquidity_policy={"missing_greeks_policy": 2})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_lp_liquidity_not_applicable_reason_non_string():
    path = _tmp_json(liquidity_policy={"liquidity_not_applicable_reason": []})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# pricing_policy sub-field tests
# ---------------------------------------------------------------------------

def test_pp_fill_price_basis_non_string():
    path = _tmp_json(pricing_policy={"fill_price_basis": 1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_pp_fill_price_basis_mid():
    path = _tmp_json(pricing_policy={"fill_price_basis": "mid"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_pp_spread_penalty_bps_negative():
    path = _tmp_json(pricing_policy={"spread_penalty_bps": -1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_pp_spread_penalty_bps_bool():
    path = _tmp_json(pricing_policy={"spread_penalty_bps": True})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_pp_commission_model_ref_non_string():
    path = _tmp_json(pricing_policy={"commission_model_ref": 1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_pp_slippage_model_ref_non_string():
    path = _tmp_json(pricing_policy={"slippage_model_ref": 2})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_pp_quote_timestamp_policy_non_string():
    path = _tmp_json(pricing_policy={"quote_timestamp_policy": 3})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_pp_entry_quote_policy_non_string():
    path = _tmp_json(pricing_policy={"entry_quote_policy": 4})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_pp_exit_quote_policy_non_string():
    path = _tmp_json(pricing_policy={"exit_quote_policy": 5})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_pp_partial_fill_policy_non_string():
    path = _tmp_json(pricing_policy={"partial_fill_policy": 6})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_pp_multi_leg_execution_policy_non_string():
    path = _tmp_json(pricing_policy={"multi_leg_execution_policy": 7})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# quote_quality_policy sub-field tests
# ---------------------------------------------------------------------------

def test_qqp_require_bid_ask_non_boolean():
    path = _tmp_json(quote_quality_policy={"require_bid_ask": "true"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_boolean" in codes
    finally:
        path.unlink()


def test_qqp_allow_mid_only_non_boolean():
    path = _tmp_json(quote_quality_policy={"allow_mid_only": 1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_boolean" in codes
    finally:
        path.unlink()


def test_qqp_reject_stale_quotes_non_boolean():
    path = _tmp_json(quote_quality_policy={"reject_stale_quotes": "yes"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_boolean" in codes
    finally:
        path.unlink()


def test_qqp_require_open_interest_non_boolean():
    path = _tmp_json(quote_quality_policy={"require_open_interest": 0})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_boolean" in codes
    finally:
        path.unlink()


def test_qqp_min_spread_pct_below_0():
    path = _tmp_json(quote_quality_policy={"min_spread_pct": -0.01})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_qqp_min_spread_pct_above_1():
    path = _tmp_json(quote_quality_policy={"min_spread_pct": 1.1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_qqp_min_spread_pct_bool():
    path = _tmp_json(quote_quality_policy={"min_spread_pct": True})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# reviewer tests
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
# Optional ref arrays tests
# ---------------------------------------------------------------------------

def test_domain_profile_refs_non_list():
    path = _tmp_json(domain_profile_refs="DP-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_preearnings_profile_refs_non_list():
    path = _tmp_json(preearnings_profile_refs="PEP-2026-0001")
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
# extension_hooks tests
# ---------------------------------------------------------------------------

def test_extension_hooks_non_object():
    path = _tmp_json(extension_hooks=["list"])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_extension_hooks_pbo_estimate():
    path = _tmp_json(extension_hooks={"pbo_estimate": 0.05})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_extension_hooks_domain_profile_list_of_strings():
    path = _tmp_json(extension_hooks={"domain_profile_extension_refs": ["DP-2026-0001"]})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_extension_hooks_non_list():
    path = _tmp_json(extension_hooks={"domain_profile_extension_refs": "DP-2026-0001"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_extension_hooks_item_non_string():
    path = _tmp_json(extension_hooks={"domain_profile_extension_refs": [42]})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list_item_type" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# Boundary/additionalProperties tests
# ---------------------------------------------------------------------------

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
    path = _tmp_json(n_tried=10)
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


def test_boundary_entry_dpe():
    path = _tmp_json(entry_dpe=0.04)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_exit_dpe():
    path = _tmp_json(exit_dpe=0.06)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_bmo_amc_indicator():
    path = _tmp_json(bmo_amc_indicator="BMO")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_iv_crush():
    path = _tmp_json(iv_crush=True)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_event_identity():
    path = _tmp_json(event_identity="evt-001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_event_timestamp():
    path = _tmp_json(event_timestamp="2026-05-01T00:00:00Z")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_event_anchor_policy():
    path = _tmp_json(event_anchor_policy="close")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_underlying_universe_membership():
    path = _tmp_json(underlying_universe_membership=["SPY"])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_outcome_definition():
    path = _tmp_json(outcome_definition={"metric": "pnl"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_field" in codes
    finally:
        path.unlink()


def test_boundary_overfit_discount():
    path = _tmp_json(overfit_discount=0.02)
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
    path = _tmp_json(created_at=42)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_created_at_object():
    path = _tmp_json(created_at={"date": "2026-05-01"})
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


def test_created_at_whitespace_only_string():
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
# Format JSON tests
# ---------------------------------------------------------------------------

def test_format_json_valid():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "valid_minimal.json")])
    assert code == 0
    data = json.loads(out)
    assert "files" in data
    assert "total_blockers" in data
    assert data["total_blockers"] == 0


def test_format_json_invalid():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_missing_required.json")])
    assert code == 1
    data = json.loads(out)
    assert data["total_blockers"] > 0


def test_format_json_emits_one_document():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "valid_minimal.json")])
    assert code == 0
    # Count top-level JSON objects — should be exactly 1
    count = 0
    for char in out.lstrip():
        if char == "{":
            count += 1
            break
    # Verify it parses as a single JSON document
    data = json.loads(out)
    assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Text format tests
# ---------------------------------------------------------------------------

def test_text_mode_valid_emits_ok():
    code, out, _ = run_validator([str(FIXTURES / "valid_minimal.json")])
    assert code == 0
    assert "[OK]" in out


def test_text_mode_invalid_emits_fail():
    code, out, _ = run_validator([str(FIXTURES / "invalid_missing_required.json")])
    assert code == 1
    assert "[FAIL]" in out
