"""Tests for scripts/local/validate_instrument_universe_spec.py"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(".")
SCRIPT = REPO / "scripts" / "local" / "validate_instrument_universe_spec.py"
FIXTURES = REPO / "fixtures" / "instrument_universe_spec_v1"


def _blockers_for_path(data, path):
    """Extract blocker list from validator JSON output for a given file path.

    The validator normalises all paths via abspath(), so we first try exact string
    match (works when path is already absolute), then fall back to basename match
    (works when path is relative or temp-file path).
    """
    if path in data.get("files", {}):
        return data["files"][path].get("blockers", [])
    basename = Path(path).name
    for k, v in data.get("files", {}).items():
        if k.endswith(basename) or Path(k).name == basename:
            return v.get("blockers", [])
    return []


def run_validator(args):
    """Call validate_instrument_universe_spec.main() in-process, return (code, stdout, stderr)."""
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
        from scripts.local.validate_instrument_universe_spec import main
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
    assert "blockers_count: 0" in out


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


def test_invalid_instrument_universe_id():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_instrument_universe_id.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_instrument_universe_id.json")}
    assert "invalid_id_format" in codes


def test_invalid_asset_classes_empty():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_asset_classes_empty.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_asset_classes_empty.json")}
    assert "invalid_list" in codes


def test_invalid_asset_class_enum():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_asset_class_enum.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_asset_class_enum.json")}
    assert "invalid_enum" in codes


def test_invalid_data_manifest_refs_empty():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_data_manifest_refs_empty.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_data_manifest_refs_empty.json")}
    assert "invalid_list" in codes


def test_invalid_universe_construction_policy():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_universe_construction_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_universe_construction_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_membership_timing_policy():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_membership_timing_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_membership_timing_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_survivorship_policy():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_survivorship_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_survivorship_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_tradability_policy():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_tradability_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_tradability_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_corporate_action_policy():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_corporate_action_policy.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_corporate_action_policy.json")}
    assert "invalid_enum" in codes


def test_invalid_rule_id():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_rule_id.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_rule_id.json")}
    assert "invalid_id_format" in codes


def test_invalid_rule_operator():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_rule_operator.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_rule_operator.json")}
    assert "invalid_enum" in codes


def test_invalid_liquidity_negative_min_price():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_liquidity_negative_min_price.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_liquidity_negative_min_price.json")}
    assert "invalid_value" in codes


def test_invalid_liquidity_spread_out_of_range():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_liquidity_spread_out_of_range.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_liquidity_spread_out_of_range.json")}
    assert "invalid_value" in codes


def test_invalid_liquidity_open_interest_type():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_liquidity_open_interest_type.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_liquidity_open_interest_type.json")}
    assert "invalid_type" in codes


def test_invalid_data_availability_coverage_out_of_range():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_data_availability_coverage_out_of_range.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_data_availability_coverage_out_of_range.json")}
    assert "invalid_value" in codes


def test_invalid_reference_array_type():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_reference_array_type.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_reference_array_type.json")}
    assert "invalid_list" in codes


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


def test_invalid_computed_field():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_computed_field.json")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_computed_field.json")}
    assert "computed_assessment_field" in codes


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
    code, out, err = run_validator(["/tmp/does_not_exist_12345_ius.json"])
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


# ---------------------------------------------------------------------------
# Helpers for inline tests
# ---------------------------------------------------------------------------

def _make_valid_entry(**overrides):
    """Return a fully-valid InstrumentUniverseSpec v1 record, with overrides applied."""
    base = {
        "instrument_universe_id": "IUS-2026-0001",
        "universe_version": 1,
        "universe_family": "us_liquid_equity",
        "asset_classes": ["equity"],
        "data_manifest_refs": ["DM-2026-0001"],
        "universe_construction_policy": "rule_based_filter",
        "membership_timing_policy": "fixed_snapshot",
        "inclusion_rules": [
            {
                "rule_id": "IRL-2026-0001",
                "field": "exchange",
                "operator": "in",
                "value": ["NYSE", "NASDAQ"],
                "timing": "decision_time",
                "reason": "Limit to major U.S. listed exchanges",
            }
        ],
        "exclusion_rules": [
            {
                "rule_id": "IRL-2026-0002",
                "field": "market_cap_usd",
                "operator": "lt",
                "value": 1000000000,
                "timing": "decision_time",
                "reason": "Exclude micro-cap instruments",
            }
        ],
        "liquidity_policy": {
            "min_price": 5.0,
            "min_dollar_volume": 5000000,
            "max_bid_ask_spread": 0.01,
            "liquidity_lookback_days": 20,
            "liquidity_measure_timing": "decision_time",
        },
        "survivorship_policy": "point_in_time",
        "tradability_policy": "tradable_through_window",
        "corporate_action_policy": "total_return_adjusted",
        "created_at": "2026-05-01T00:00:00Z",
        "reviewer": {"name": "dr_elliot_review_2026"},
    }
    for k, v in overrides.items():
        base[k] = v
    return base


def _tmp_json(**overrides):
    entry = _make_valid_entry(**overrides)
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entry, f)
    f.close()
    return Path(f.name)


# ---------------------------------------------------------------------------
# Required field tests
# ---------------------------------------------------------------------------

def test_missing_required_field():
    entry = {k: v for k, v in _make_valid_entry().items() if k != "instrument_universe_id"}
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
    path = _tmp_json(instrument_universe_id=None)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_empty_string_required_field():
    path = _tmp_json(instrument_universe_id="")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_whitespace_only_required_field():
    path = _tmp_json(instrument_universe_id="   ")
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

def test_invalid_instrument_universe_id_format():
    path = _tmp_json(instrument_universe_id="IUS-PA-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_non_string_instrument_universe_id():
    path = _tmp_json(instrument_universe_id=42)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_invalid_rule_id_format():
    path = _tmp_json(
        inclusion_rules=[{"rule_id": "IRL-PA-0001", "field": "exchange", "operator": "in", "value": ["NYSE"]}]
    )
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_id_format" in codes
    finally:
        path.unlink()


def test_non_string_rule_id():
    path = _tmp_json(
        inclusion_rules=[{"rule_id": 42, "field": "exchange", "operator": "in", "value": ["NYSE"]}]
    )
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# Enum validation
# ---------------------------------------------------------------------------

def test_invalid_asset_classes_item():
    path = _tmp_json(asset_classes=["stock"])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_universe_construction_policy_string():
    path = _tmp_json(universe_construction_policy="manual_selection")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_membership_timing_policy_string():
    path = _tmp_json(membership_timing_policy="execution_time")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_survivorship_policy_string():
    path = _tmp_json(survivorship_policy="no_survivor_bias")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_tradability_policy_string():
    path = _tmp_json(tradability_policy="fully_liquid")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_corporate_action_policy_string():
    path = _tmp_json(corporate_action_policy="fully_adjusted")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_invalid_rule_operator():
    path = _tmp_json(
        inclusion_rules=[{"field": "exchange", "operator": "between", "value": ["NYSE", "NASDAQ"]}]
    )
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_falsey_enum_zero():
    path = _tmp_json(universe_construction_policy=0)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_falsey_enum_empty_list():
    path = _tmp_json(universe_construction_policy=[])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_falsey_enum_empty_dict():
    path = _tmp_json(universe_construction_policy={})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_falsey_enum_false():
    path = _tmp_json(universe_construction_policy=False)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# Array/list field tests
# ---------------------------------------------------------------------------

def test_asset_classes_non_list():
    path = _tmp_json(asset_classes="equity")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_asset_classes_empty():
    path = _tmp_json(asset_classes=[])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_asset_classes_item_non_string():
    path = _tmp_json(asset_classes=[42])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list_item_type" in codes
    finally:
        path.unlink()


def test_data_manifest_refs_non_list():
    path = _tmp_json(data_manifest_refs="DM-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_data_manifest_refs_empty():
    path = _tmp_json(data_manifest_refs=[])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_data_manifest_refs_item_non_string():
    path = _tmp_json(data_manifest_refs=[42])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list_item_type" in codes
    finally:
        path.unlink()


def test_inclusion_rules_non_list():
    path = _tmp_json(inclusion_rules={"rule_id": "IRL-2026-0001"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_exclusion_rules_non_list():
    path = _tmp_json(exclusion_rules={"rule_id": "IRL-2026-0001"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_universe_snapshot_refs_non_list():
    path = _tmp_json(universe_snapshot_refs="IUS-SNAP-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_runner_output_refs_non_list():
    path = _tmp_json(runner_output_refs="RUN-OUTPUT-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_domain_profile_refs_non_list():
    path = _tmp_json(domain_profile_refs="DSP-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list" in codes
    finally:
        path.unlink()


def test_universe_snapshot_refs_item_non_string():
    path = _tmp_json(universe_snapshot_refs=[42])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_list_item_type" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# Rule object tests
# ---------------------------------------------------------------------------

def test_inclusion_rules_item_non_object():
    path = _tmp_json(inclusion_rules=["not an object"])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_exclusion_rules_item_non_object():
    path = _tmp_json(exclusion_rules=["not an object"])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_rule_field_non_string():
    path = _tmp_json(
        inclusion_rules=[{"field": 42, "operator": "in", "value": ["NYSE"]}]
    )
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_rule_timing_non_string():
    path = _tmp_json(
        inclusion_rules=[{"field": "exchange", "operator": "in", "value": ["NYSE"], "timing": 42}]
    )
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_rule_data_manifest_ref_non_string():
    path = _tmp_json(
        inclusion_rules=[{"field": "exchange", "operator": "in", "value": ["NYSE"], "data_manifest_ref": 42}]
    )
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_rule_reason_non_string():
    path = _tmp_json(
        inclusion_rules=[{"field": "exchange", "operator": "in", "value": ["NYSE"], "reason": 42}]
    )
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


# value is not overconstrained: string, number, boolean, array, object should not fail by itself
def test_rule_value_string_passes():
    path = _tmp_json(
        inclusion_rules=[{"field": "ticker", "operator": "eq", "value": "AAPL"}]
    )
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_rule_value_number_passes():
    path = _tmp_json(
        inclusion_rules=[{"field": "market_cap", "operator": "gt", "value": 1000000000}]
    )
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_rule_value_boolean_passes():
    path = _tmp_json(
        inclusion_rules=[{"field": "is_active", "operator": "eq", "value": True}]
    )
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_rule_value_array_passes():
    path = _tmp_json(
        inclusion_rules=[{"field": "exchange", "operator": "in", "value": ["NYSE", "NASDAQ"]}]
    )
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_rule_value_object_passes():
    path = _tmp_json(
        inclusion_rules=[{"field": "metadata", "operator": "eq", "value": {"key": "val"}}]
    )
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# liquidity_policy tests
# ---------------------------------------------------------------------------

def test_liquidity_policy_non_object():
    path = _tmp_json(liquidity_policy="not an object")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_liquidity_min_price_negative():
    path = _tmp_json(liquidity_policy={"min_price": -1.0})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_liquidity_max_price_negative():
    path = _tmp_json(liquidity_policy={"max_price": -1.0})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_liquidity_min_dollar_volume_negative():
    path = _tmp_json(liquidity_policy={"min_dollar_volume": -1.0})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_liquidity_min_average_volume_negative():
    path = _tmp_json(liquidity_policy={"min_average_volume": -1.0})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_liquidity_min_open_interest_negative():
    path = _tmp_json(liquidity_policy={"min_open_interest": -1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_liquidity_min_open_interest_bool():
    path = _tmp_json(liquidity_policy={"min_open_interest": False})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_liquidity_max_bid_ask_spread_below_0():
    path = _tmp_json(liquidity_policy={"max_bid_ask_spread": -0.01})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_liquidity_max_bid_ask_spread_above_1():
    path = _tmp_json(liquidity_policy={"max_bid_ask_spread": 1.5})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_liquidity_max_bid_ask_spread_bool():
    path = _tmp_json(liquidity_policy={"max_bid_ask_spread": True})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_liquidity_min_days_listed_negative():
    path = _tmp_json(liquidity_policy={"min_days_listed": -1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_liquidity_lookback_days_negative():
    path = _tmp_json(liquidity_policy={"liquidity_lookback_days": -1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_liquidity_measure_timing_non_string():
    path = _tmp_json(liquidity_policy={"liquidity_measure_timing": 42})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_liquidity_not_applicable_reason_non_string():
    path = _tmp_json(liquidity_policy={"liquidity_not_applicable_reason": 42})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# data_availability_policy tests
# ---------------------------------------------------------------------------

def test_data_availability_policy_non_object():
    path = _tmp_json(data_availability_policy="not an object")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_object" in codes
    finally:
        path.unlink()


def test_data_availability_required_history_days_negative():
    path = _tmp_json(data_availability_policy={"required_history_days": -1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_data_availability_required_history_days_bool():
    path = _tmp_json(data_availability_policy={"required_history_days": True})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_data_availability_required_feature_coverage_below_0():
    path = _tmp_json(data_availability_policy={"required_feature_coverage": -0.1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_data_availability_required_feature_coverage_above_1():
    path = _tmp_json(data_availability_policy={"required_feature_coverage": 1.5})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_data_availability_required_feature_coverage_bool():
    path = _tmp_json(data_availability_policy={"required_feature_coverage": True})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_data_availability_required_outcome_coverage_below_0():
    path = _tmp_json(data_availability_policy={"required_outcome_coverage": -0.1})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_data_availability_required_outcome_coverage_above_1():
    path = _tmp_json(data_availability_policy={"required_outcome_coverage": 1.5})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_value" in codes
    finally:
        path.unlink()


def test_data_availability_required_outcome_coverage_bool():
    path = _tmp_json(data_availability_policy={"required_outcome_coverage": False})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_data_availability_point_in_time_required_non_boolean():
    path = _tmp_json(data_availability_policy={"point_in_time_required": "yes"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_boolean" in codes
    finally:
        path.unlink()


def test_data_availability_feature_cutoff_alignment_required_non_boolean():
    path = _tmp_json(data_availability_policy={"feature_cutoff_alignment_required": "yes"})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_boolean" in codes
    finally:
        path.unlink()


def test_data_availability_missing_data_policy_non_string():
    path = _tmp_json(data_availability_policy={"missing_data_policy": 42})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "invalid_type" in codes
    finally:
        path.unlink()


def test_data_availability_stale_data_policy_non_string():
    path = _tmp_json(data_availability_policy={"stale_data_policy": 42})
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
    path = _tmp_json(reviewer="dr_elliot_review_2026")
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
# Computed-assessment/run-output field tests
# ---------------------------------------------------------------------------

def test_computed_field_signals():
    path = _tmp_json(signals=[{"instrument_id": "AAPL", "signal_type": "momentum"}])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_field_rankings():
    path = _tmp_json(rankings=[{"instrument_id": "AAPL", "rank": 1}])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_field_factor_scores():
    path = _tmp_json(factor_scores=[{"instrument_id": "AAPL", "factor": "momentum", "score": 0.75}])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_field_selected_variant_id():
    path = _tmp_json(selected_variant_id="VAR-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_field_n_tried():
    path = _tmp_json(n_tried=42)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_field_trial_family_id():
    path = _tmp_json(trial_family_id="TF-2026-0001")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_field_pnl():
    path = _tmp_json(pnl=12345.67)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_field_realized_returns():
    path = _tmp_json(realized_returns=[0.05, 0.12])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_field_pbo_estimate():
    path = _tmp_json(pbo_estimate=0.23)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_field_dsr_estimate():
    path = _tmp_json(dsr_estimate=0.18)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_field_strategy_complexity_score():
    path = _tmp_json(strategy_complexity_score=7)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()


def test_computed_field_review_packet_decision():
    path = _tmp_json(review_packet_decision="approved")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        assert "computed_assessment_field" in codes
    finally:
        path.unlink()
