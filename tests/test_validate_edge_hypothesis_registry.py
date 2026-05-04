"""Tests for scripts/local/validate_edge_hypothesis_registry.py"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(".")
SCRIPT = REPO / "scripts" / "local" / "validate_edge_hypothesis_registry.py"
FIXTURES = REPO / "fixtures" / "edge_hypothesis_registry_v1"


def _blockers_for_path(data, path):
    """Extract blocker list from validator JSON output for a given file path.

    The validator normalises all paths via abspath(), so we first try exact string
    match (works when path is already absolute), then fall back to basename match
    (works when path is relative or temp-file path).
    """
    # Try exact key match first (handles absolute /tmp paths and fixture paths)
    if path in data.get("files", {}):
        return data["files"][path].get("blockers", [])
    # Fallback: match by basename (handles temp files where validator normalises path)
    basename = Path(path).name
    for k, v in data.get("files", {}).items():
        if k.endswith(basename) or Path(k).name == basename:
            return v.get("blockers", [])
    return []


def run_validator(args):
    """Call validate_edge_hypothesis_registry.main() in-process, return (code, stdout, stderr)."""
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
        from scripts.local.validate_edge_hypothesis_registry import main
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
    code, out, _ = run_validator([str(FIXTURES / "valid_minimal.jsonl")])
    assert code == 0, f"Expected 0, got {code}: {out}"
    assert "blockers_count: 0" in out


def test_valid_minimal_json():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "valid_minimal.jsonl")])
    assert code == 0
    data = json.loads(out)
    assert data["files"][str(FIXTURES / "valid_minimal.jsonl")]["blockers_count"] == 0
    assert data["files"][str(FIXTURES / "valid_minimal.jsonl")]["blockers"] == []


# ----- invalid fixture tests -----

def test_invalid_missing_required():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_missing_required.jsonl")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_missing_required.jsonl")}
    assert "missing_required_field" in codes


def test_invalid_hypothesis_id():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_hypothesis_id.jsonl")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_hypothesis_id.jsonl")}
    assert "invalid_id_format" in codes


def test_invalid_status():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_status.jsonl")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_status.jsonl")}
    assert "invalid_enum" in codes


def test_invalid_trial_ledger_ref():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_trial_ledger_ref.jsonl")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_trial_ledger_ref.jsonl")}
    assert "invalid_ref_format" in codes


def test_invalid_search_space_ref():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_search_space_ref.jsonl")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_search_space_ref.jsonl")}
    assert "invalid_ref_format" in codes


def test_invalid_model_assessment_ref():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_model_assessment_ref.jsonl")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_model_assessment_ref.jsonl")}
    assert "invalid_ref_format" in codes


def test_invalid_governance_true():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_governance_true.jsonl")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_governance_true.jsonl")}
    assert "forbidden_governance_field" in codes


def test_invalid_registry_mutation_mode():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_registry_mutation_mode.jsonl")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_registry_mutation_mode.jsonl")}
    assert "forbidden_governance_field" in codes


def test_invalid_approved_missing_review_refs():
    code, out, _ = run_validator(["--format", "json", str(FIXTURES / "invalid_approved_missing_review_refs.jsonl")])
    assert code == 1
    data = json.loads(out)
    codes = {b["code"] for b in _blockers_for_path(data, FIXTURES / "invalid_approved_missing_review_refs.jsonl")}
    assert "approved_missing_required_refs" in codes


# ----- missing file -----

def test_missing_file():
    code, out, err = run_validator(["/nonexistent/file.jsonl"])
    assert code == 2
    assert "file not found" in err


# ----- invalid JSON -----

def test_invalid_json():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write("{ not valid json")
        path = f.name
    try:
        code, out, err = run_validator(["--format", "json", path])
        assert code == 2, f"Expected exit 2, got {code}: {out}"
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_json" in codes
    finally:
        Path(path).unlink()


# ----- required field handling -----

def _make_entry(**overrides):
    base = {
        "hypothesis_id": "HYP-2026-0001",
        "registry_version": "edge_registry_v1",
        "title": "Test hypothesis",
        "status": "specified",
        "status_reason": "Test reason",
        "evidence_stage": "exploratory",
        "source_type": "theory_first",
        "source_lane": "theory_first",
        "theory_timing": "pre_registration",
        "manual_review_required": True,
        "created_at": "2026-01-01T00:00:00Z",
        "lifecycle_events": [
            {
                "event_id": "EVT-2026-0001",
                "event_type": "status_change",
                "event_timestamp": "2026-01-01T00:00:00Z",
                "actor": "test-user",
                "to_status": "specified",
                "manual_review_required": False
            }
        ],
    }
    base.update(overrides)
    return base


def _tmp_jsonl(**overrides):
    entry = _make_entry(**overrides)
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    json.dump(entry, f)
    f.close()
    return Path(f.name)


def test_missing_required_field():
    # omit hypothesis_id entirely
    entry = {k: v for k, v in _make_entry().items() if k != "hypothesis_id"}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
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
    path = _tmp_jsonl(hypothesis_id=None)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_empty_string_required_field():
    path = _tmp_jsonl(title="")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_whitespace_only_required_text_field():
    path = _tmp_jsonl(title="   ")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


# ----- enum handling -----

def test_invalid_status_string():
    path = _tmp_jsonl(status="promoted")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_empty_status_string():
    path = _tmp_jsonl(status="")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, str(path))}
        # Empty string for required field is caught as missing_required_field, not invalid_enum
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_status_zero():
    path = _tmp_jsonl(status=0)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_status_list():
    path = _tmp_jsonl(status=[])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_status_dict():
    path = _tmp_jsonl(status={})
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


def test_status_false():
    path = _tmp_jsonl(status=False)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_enum" in codes
    finally:
        path.unlink()


# ----- manual_review_required -----

def test_manual_review_required_true():
    path = _tmp_jsonl(manual_review_required=True)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_manual_review_required_false():
    path = _tmp_jsonl(manual_review_required=False)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        path.unlink()


def test_manual_review_required_null():
    path = _tmp_jsonl(manual_review_required=None)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "missing_required_field" in codes
    finally:
        path.unlink()


def test_manual_review_required_zero():
    path = _tmp_jsonl(manual_review_required=0)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_boolean" in codes
    finally:
        path.unlink()


def test_manual_review_required_one():
    path = _tmp_jsonl(manual_review_required=1)
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_boolean" in codes
    finally:
        path.unlink()


def test_manual_review_required_string_true():
    path = _tmp_jsonl(manual_review_required="true")
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_boolean" in codes
    finally:
        path.unlink()


def test_manual_review_required_list():
    path = _tmp_jsonl(manual_review_required=[])
    try:
        code, out, _ = run_validator(["--format", "json", str(path)])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_boolean" in codes
    finally:
        path.unlink()


# ----- governance stop-rule fields -----

_GOV_FIELDS = [
    "automated_promotion_allowed",
    "live_trading_allowed",
    "production_execution_allowed",
    "automated_registry_mutation_allowed",
]


def test_governance_field_absent_passes():
    # absent — no field at all
    entry = {k: v for k, v in _make_entry().items() if k not in _GOV_FIELDS}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        Path(f.name).unlink()


def test_governance_field_false_passes():
    for field in _GOV_FIELDS:
        path = _tmp_jsonl(**{field: False})
        try:
            code, out, _ = run_validator(["--format", "json", str(path)])
            assert code == 0, f"{field}=false: Expected 0, got {code}: {out}"
        finally:
            path.unlink()


def test_governance_field_true_fails():
    for field in _GOV_FIELDS:
        path = _tmp_jsonl(**{field: True})
        try:
            code, out, _ = run_validator(["--format", "json", str(path)])
            assert code == 1, f"{field}=true: Expected 1, got {code}: {out}"
            data = json.loads(out)
            codes = {b["code"] for b in _blockers_for_path(data, path)}
            assert "forbidden_governance_field" in codes, f"{field}=true: {codes}"
        finally:
            path.unlink()


# ----- lifecycle_events -----

def test_lifecycle_event_mutation_mode_manual_passes():
    entry = _make_entry(lifecycle_events=[
        {
            "event_id": "EVT-2026-0001",
            "event_type": "created",
            "event_timestamp": "2026-01-01T00:00:00Z",
            "actor": "test-user",
            "to_status": "specified",
            "manual_review_required": False,
            "registry_mutation_mode": "manual"
        }
    ])
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        Path(f.name).unlink()


def test_lifecycle_event_mutation_mode_automated_fails():
    entry = _make_entry(lifecycle_events=[
        {
            "event_id": "EVT-2026-0001",
            "event_type": "updated",
            "event_timestamp": "2026-01-01T00:00:00Z",
            "actor": "test-user",
            "to_status": "specified",
            "manual_review_required": False,
            "registry_mutation_mode": "automated"
        }
    ])
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    json.dump(entry, f)
    f.close()
    path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "forbidden_governance_field" in codes
    finally:
        Path(f.name).unlink()


def test_lifecycle_events_not_list():
    entry = _make_entry(lifecycle_events="not-a-list")
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    json.dump(entry, f)
    f.close()
    path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_list" in codes
    finally:
        Path(f.name).unlink()


def test_lifecycle_event_entry_not_object():
    entry = _make_entry(lifecycle_events=["not-an-object"])
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    json.dump(entry, f)
    f.close()
    path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_object" in codes
    finally:
        Path(f.name).unlink()


def test_lifecycle_event_missing_event_timestamp():
    entry = _make_entry(lifecycle_events=[{
        "event_id": "EVT-2026-0001",
        "event_type": "status_change",
        "actor": "test-user",
        "to_status": "specified",
        "manual_review_required": False
    }])
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
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


def test_lifecycle_event_missing_actor():
    entry = _make_entry(lifecycle_events=[{
        "event_id": "EVT-2026-0001",
        "event_type": "status_change",
        "event_timestamp": "2026-01-01T00:00:00Z",
        "to_status": "specified",
        "manual_review_required": False
    }])
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
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


def test_lifecycle_event_missing_event_type():
    entry = _make_entry(lifecycle_events=[{
        "event_id": "EVT-2026-0001",
        "event_timestamp": "2026-01-01T00:00:00Z",
        "actor": "test-user",
        "to_status": "specified",
        "manual_review_required": False
    }])
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
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


def test_lifecycle_event_missing_to_status():
    entry = _make_entry(lifecycle_events=[{
        "event_id": "EVT-2026-0001",
        "event_type": "status_change",
        "event_timestamp": "2026-01-01T00:00:00Z",
        "actor": "test-user",
        "manual_review_required": False
    }])
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
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


def test_lifecycle_event_missing_manual_review_required():
    entry = _make_entry(lifecycle_events=[{
        "event_id": "EVT-2026-0001",
        "event_type": "status_change",
        "event_timestamp": "2026-01-01T00:00:00Z",
        "actor": "test-user",
        "to_status": "specified"
    }])
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
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


# ----- approved_for_next_stage cross-field rule -----

def test_approved_for_next_stage_all_refs_present_passes():
    entry = _make_entry(
        status="approved_for_next_stage",
        review_packet_refs=["RP-2026-0001"],
        trial_ledger_refs=["TRL-2026-0001"],
        search_space_refs=["SSM-2026-0001"],
        model_assessment_refs=["MAS-2026-0001"],
    )
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    json.dump(entry, f)
    f.close()
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 0, f"Expected 0, got {code}: {out}"
    finally:
        Path(f.name).unlink()


def test_approved_for_next_stage_missing_review_packet_refs():
    entry = _make_entry(
        status="approved_for_next_stage",
        trial_ledger_refs=["TRL-2026-0001"],
        search_space_refs=["SSM-2026-0001"],
        model_assessment_refs=["MAS-2026-0001"],
    )
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    json.dump(entry, f)
    f.close()
    path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "approved_missing_required_refs" in codes
    finally:
        Path(f.name).unlink()


def test_approved_for_next_stage_missing_trial_ledger_refs():
    entry = _make_entry(
        status="approved_for_next_stage",
        review_packet_refs=["RP-2026-0001"],
        search_space_refs=["SSM-2026-0001"],
        model_assessment_refs=["MAS-2026-0001"],
    )
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    json.dump(entry, f)
    f.close()
    path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "approved_missing_required_refs" in codes
    finally:
        Path(f.name).unlink()


def test_approved_for_next_stage_missing_search_space_refs():
    entry = _make_entry(
        status="approved_for_next_stage",
        review_packet_refs=["RP-2026-0001"],
        trial_ledger_refs=["TRL-2026-0001"],
        model_assessment_refs=["MAS-2026-0001"],
    )
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    json.dump(entry, f)
    f.close()
    path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "approved_missing_required_refs" in codes
    finally:
        Path(f.name).unlink()


def test_approved_for_next_stage_missing_model_assessment_refs():
    entry = _make_entry(
        status="approved_for_next_stage",
        review_packet_refs=["RP-2026-0001"],
        trial_ledger_refs=["TRL-2026-0001"],
        search_space_refs=["SSM-2026-0001"],
    )
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    json.dump(entry, f)
    f.close()
    path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", f.name])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "approved_missing_required_refs" in codes
    finally:
        Path(f.name).unlink()


# ----- parse/read error exits 2 -----

def test_parse_error_exits_2():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write("{ not valid json")
        path = f.name
    try:
        code, out, err = run_validator([path])
        assert code == 2, f"Expected exit 2, got {code}: {err}"
    finally:
        Path(path).unlink()


# ----- non-object root -----

def test_non_object_root_list():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        json.dump([], f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_object" in codes
    finally:
        Path(path).unlink()


def test_non_object_root_number():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        json.dump(42, f)
        path = f.name
    try:
        code, out, _ = run_validator(["--format", "json", path])
        assert code == 1
        data = json.loads(out)
        codes = {b["code"] for b in _blockers_for_path(data, path)}
        assert "invalid_object" in codes
    finally:
        Path(path).unlink()


def test_schema_additional_properties_false():
    """Schema enforces top-level additionalProperties: false."""
    schema = json.load(open("schemas/edge_hypothesis_registry_v1.schema.json"))
    assert schema.get("additionalProperties") is False, \
        "edge_hypothesis_registry_v1 schema must have top-level additionalProperties: false"
