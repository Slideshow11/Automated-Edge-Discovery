"""
Tests for the first thin real-data runner dry-run CLI skeleton.

Scope:
- Validates governance inputs (experiment spec structural validation).
- Emits a RunnerOutput v1 artifact.
- No real backtest execution, no registry writes, no live trading.

These tests MUST NOT use subprocess to run the CLI script directly.
All tests call main() or build_runner_output() directly.
"""
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

# Ensure the engine package is on the path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.edge_discovery.runners.first_thin_real_data_runner import (
    RUNNER_OUTPUT_ID_DEFAULT,
    RUNNER_OUTPUT_VERSION,
    build_runner_output,
    write_runner_output,
    main,
    _compute_run_config_hash,
    _compute_run_id,
    _check_experiment_spec_id,
    _utc_now,
    GOVERNANCE_STOP_RULE_FIELDS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_SPEC = _ROOT / "fixtures" / "experiment_spec_v1" / "valid_minimal.json"


@pytest.fixture
def valid_experiment_spec(tmp_path):
    """Create a temporary experiment spec file."""
    spec_content = {
        "experiment_id": "EXP-2026-0001",
        "experiment_version": 1,
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": ["DM-2026-0001"],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {
            "timestamp_ref": "reference_date",
            "description": "Decision timestamp is the reference date.",
        },
        "feature_cutoff_policy": {
            "timestamp_ref": "trade_date",
            "offset_direction": "before",
            "offset_unit": "trading_days",
            "offset_value": 1,
            "description": "Feature data cuts off one trading day before.",
        },
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first", "confirmatory"],
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
        "created_at": "2026-05-05T00:00:00Z",
        "reviewer": {
            "name": "test_reviewer",
            "affiliation": "test",
            "date": "2026-05-05",
        },
    }
    p = tmp_path / "experiment_spec.json"
    p.write_text(json.dumps(spec_content, indent=2))
    return p


@pytest.fixture
def experiment_spec_missing_id(tmp_path):
    """Create an experiment spec file missing experiment_id."""
    spec_content = {
        "experiment_version": 1,
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": ["DM-2026-0001"],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date"},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "created_at": "2026-05-05T00:00:00Z",
        "reviewer": {"name": "test"},
    }
    p = tmp_path / "no_id_spec.json"
    p.write_text(json.dumps(spec_content, indent=2))
    return p


@pytest.fixture
def experiment_spec_autonomous_search_true(tmp_path):
    """Create an experiment spec with autonomous_search=True."""
    spec_content = {
        "experiment_id": "EXP-2026-9999",
        "experiment_version": 1,
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": ["DM-2026-0001"],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date"},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {**{k: False for k in GOVERNANCE_STOP_RULE_FIELDS}, "autonomous_search": True},
        "created_at": "2026-05-05T00:00:00Z",
        "reviewer": {"name": "test"},
    }
    p = tmp_path / "autonomous_search_spec.json"
    p.write_text(json.dumps(spec_content, indent=2))
    return p


# ---------------------------------------------------------------------------
# Test: build_runner_output returns required fields
# ---------------------------------------------------------------------------

def test_build_runner_output_returns_required_fields(valid_experiment_spec):
    """build_runner_output returns all 17 required RunnerOutput v1 fields."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        runner_name="test-runner",
        runner_version="0.1.0",
        run_owner="test@test",
    )

    required = {
        "runner_output_id",
        "runner_output_version",
        "run_id",
        "run_mode",
        "status",
        "runner_name",
        "runner_version",
        "experiment_spec_ref",
        "input_artifact_refs",
        "data_manifest_refs",
        "run_config_hash",
        "started_at",
        "completed_at",
        "audit_summary",
        "output_manifest",
        "created_at",
        "run_owner",
    }
    assert required.issubset(artifact.keys()), (
        f"Missing required fields: {required - artifact.keys()}"
    )


# ---------------------------------------------------------------------------
# Test: run_mode="dry_run" and status="success"
# ---------------------------------------------------------------------------

def test_run_mode_is_dry_run(valid_experiment_spec):
    """Emitted artifact has run_mode = 'dry_run'."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    assert artifact["run_mode"] == "dry_run"


def test_status_is_success(valid_experiment_spec):
    """Emitted artifact has status = 'success'."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    assert artifact["status"] == "success"


# ---------------------------------------------------------------------------
# Test: run_config_hash is deterministic
# ---------------------------------------------------------------------------

def test_run_config_hash_deterministic(valid_experiment_spec):
    """run_config_hash is identical for the same experiment spec file."""
    hash1 = _compute_run_config_hash(valid_experiment_spec)
    hash2 = _compute_run_config_hash(valid_experiment_spec)
    assert hash1 == hash2


def test_run_config_hash_changes_with_content(valid_experiment_spec):
    """run_config_hash changes if experiment spec content changes."""
    hash1 = _compute_run_config_hash(valid_experiment_spec)

    # Rewrite with different whitespace (still same JSON)
    content = json.loads(valid_experiment_spec.read_text())
    content["experiment_version"] = 2
    valid_experiment_spec.write_text(json.dumps(content, indent=2, sort_keys=True))

    hash2 = _compute_run_config_hash(valid_experiment_spec)
    assert hash1 != hash2


def test_run_id_deterministic(valid_experiment_spec):
    """run_id is identical for the same experiment spec file."""
    run_id1 = _compute_run_id(_compute_run_config_hash(valid_experiment_spec))
    run_id2 = _compute_run_id(_compute_run_config_hash(valid_experiment_spec))
    assert run_id1 == run_id2


def test_run_id_derives_from_config_hash(valid_experiment_spec):
    """run_id is derived from run_config_hash (first 16 hex chars)."""
    config_hash = _compute_run_config_hash(valid_experiment_spec)
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    expected_run_id = config_hash[:16]
    assert artifact["run_id"] == expected_run_id


# ---------------------------------------------------------------------------
# Test: no registry/ledger writes
# ---------------------------------------------------------------------------

def test_no_registry_mutation_flag_set(valid_experiment_spec):
    """audit_summary includes no_autonomous_search_flag_set = pass when prohibited."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )

    audit_names = {a["audit_name"] for a in artifact["audit_summary"]["audits"]}
    assert "no_autonomous_search_flag_set" in audit_names

    autonomous_audit = next(
        a for a in artifact["audit_summary"]["audits"]
        if a["audit_name"] == "no_autonomous_search_flag_set"
    )
    assert autonomous_audit["audit_result"] == "pass"
    assert autonomous_audit["severity"] == "blocker"


def test_autonomous_search_flag_audit_fails_when_true(experiment_spec_autonomous_search_true):
    """no_autonomous_search_flag_set audit fails when prohibited_modes.autonomous_search=True."""
    artifact = build_runner_output(
        experiment_spec_path=experiment_spec_autonomous_search_true,
        run_owner="test@test",
    )

    autonomous_audit = next(
        a for a in artifact["audit_summary"]["audits"]
        if a["audit_name"] == "no_autonomous_search_flag_set"
    )
    assert autonomous_audit["audit_result"] == "fail"
    assert autonomous_audit["blocker_count"] == 1


# ---------------------------------------------------------------------------
# Test: output file creation
# ---------------------------------------------------------------------------

def test_output_file_created(tmp_path, valid_experiment_spec):
    """Output file is created at the requested output path."""
    output_path = tmp_path / "runner_output.json"

    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    write_runner_output(output_path, artifact)

    assert output_path.exists()


def test_output_file_not_overwritten_if_exists(tmp_path, valid_experiment_spec):
    """write_runner_output raises FileExistsError if output already exists."""
    output_path = tmp_path / "runner_output.json"
    output_path.write_text("existing content")

    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )

    with pytest.raises(FileExistsError):
        write_runner_output(output_path, artifact)


def test_missing_experiment_spec_fails(tmp_path):
    """build_runner_output raises FileNotFoundError when experiment spec does not exist."""
    fake_path = tmp_path / "nonexistent.json"

    with pytest.raises(FileNotFoundError):
        build_runner_output(
            experiment_spec_path=fake_path,
            run_owner="test@test",
        )


# ---------------------------------------------------------------------------
# Test: missing experiment_spec_id
# ---------------------------------------------------------------------------

def test_missing_experiment_spec_id_fails(experiment_spec_missing_id):
    """build_runner_output raises ValueError when experiment_id is missing."""
    with pytest.raises(ValueError, match="experiment_id"):
        build_runner_output(
            experiment_spec_path=experiment_spec_missing_id,
            run_owner="test@test",
        )


# ---------------------------------------------------------------------------
# Test: audit_summary contains required dry-run audit checks
# ---------------------------------------------------------------------------

def test_audit_summary_contains_required_checks(valid_experiment_spec):
    """audit_summary contains all required dry-run audit checks."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )

    required_audit_names = {
        "schema_validation_all_inputs",
        "no_registry_mutation",
        "no_autonomous_search_flag_set",
        "deterministic_run_config_hash",
    }

    actual_audit_names = {a["audit_name"] for a in artifact["audit_summary"]["audits"]}
    assert required_audit_names.issubset(actual_audit_names), (
        f"Missing audit checks: {required_audit_names - actual_audit_names}"
    )


def test_audit_summary_overall_result_pass_when_valid(valid_experiment_spec):
    """audit_summary.overall_result is 'pass' for a valid dry-run."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    assert artifact["audit_summary"]["overall_result"] == "pass"
    assert artifact["audit_summary"]["blocker_count"] == 0


# ---------------------------------------------------------------------------
# Test: CLI main() behavior
# ---------------------------------------------------------------------------

def test_main_missing_experiment_spec_exits(tmp_path):
    """main() returns 1 when experiment spec path does not exist."""
    old_out = io.StringIO()
    old_err = io.StringIO()
    old_argv = sys.argv
    buf_out = io.StringIO()
    buf_err = io.StringIO()

    sys.argv = ["run_first_thin_real_data_runner", "--experiment-spec", "/nonexistent.json",
                "--output-path", str(tmp_path / "out.json"), "--run-owner", "test"]
    sys.stdout, sys.stderr = buf_out, buf_err

    try:
        code = main()
    finally:
        sys.stdout, sys.stderr, sys.argv = old_out, old_err, old_argv

    assert code == 1
    assert "not found" in buf_err.getvalue().lower()


def test_main_existing_output_exits(tmp_path, valid_experiment_spec):
    """main() returns 1 when output path already exists."""
    output_path = tmp_path / "existing.json"
    output_path.write_text("already exists")

    old_out = io.StringIO()
    old_err = io.StringIO()
    old_argv = sys.argv
    buf_out = io.StringIO()
    buf_err = io.StringIO()

    sys.argv = [
        "run_first_thin_real_data_runner",
        "--experiment-spec", str(valid_experiment_spec),
        "--output-path", str(output_path),
        "--run-owner", "test",
    ]
    sys.stdout, sys.stderr = buf_out, buf_err

    try:
        code = main()
    finally:
        sys.stdout, sys.stderr, sys.argv = old_out, old_err, old_argv

    assert code == 1
    assert "already exists" in buf_err.getvalue()


def test_main_success(tmp_path, valid_experiment_spec):
    """main() returns 0 and writes output when all inputs are valid."""
    output_path = tmp_path / "runner_output.json"

    old_out = io.StringIO()
    old_err = io.StringIO()
    old_argv = sys.argv
    buf_out = io.StringIO()
    buf_err = io.StringIO()

    sys.argv = [
        "run_first_thin_real_data_runner",
        "--experiment-spec", str(valid_experiment_spec),
        "--output-path", str(output_path),
        "--run-owner", "test@test",
        "--runner-name", "test-runner",
        "--runner-version", "1.0.0",
    ]
    sys.stdout, sys.stderr = buf_out, buf_err

    try:
        code = main()
    finally:
        sys.stdout, sys.stderr, sys.argv = old_out, old_err, old_argv

    assert code == 0
    assert output_path.exists()

    # Verify content is valid JSON
    with open(output_path) as fh:
        artifact = json.load(fh)
    assert artifact["run_mode"] == "dry_run"
    assert artifact["status"] == "success"


# ---------------------------------------------------------------------------
# Test: written artifact validates against schema if jsonschema available
# ---------------------------------------------------------------------------

def test_written_artifact_validates_against_schema(tmp_path, valid_experiment_spec):
    """If jsonschema is available, validate emitted artifact against runner_output_spec_v1 schema."""
    jsonschema = pytest.importorskip("jsonschema")

    output_path = tmp_path / "runner_output.json"

    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    write_runner_output(output_path, artifact)

    # Re-read from disk
    with open(output_path) as fh:
        loaded = json.load(fh)

    # Load schema
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "runner_output_spec_v1.schema.json"
    if not schema_path.exists():
        pytest.skip("runner_output_spec_v1.schema.json not found")

    schema = json.loads(schema_path.read_text())
    jsonschema.validate(loaded, schema)


# ---------------------------------------------------------------------------
# Test: runner_output_id is stable format
# ---------------------------------------------------------------------------

def test_runner_output_id_format(valid_experiment_spec):
    """runner_output_id follows RUN-YYYY-NNNN format."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    import re
    assert re.match(r"^RUN-[0-9]{4}-[0-9]{4}$", artifact["runner_output_id"])


# ---------------------------------------------------------------------------
# Test: data_manifest_refs forwarded from experiment spec
# ---------------------------------------------------------------------------

def test_data_manifest_refs_forwarded(valid_experiment_spec):
    """data_manifest_refs is forwarded from the experiment spec."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    assert artifact["data_manifest_refs"] == ["DM-2026-0001"]


# ---------------------------------------------------------------------------
# Test: failure_summary is null for success dry-run
# ---------------------------------------------------------------------------

def test_failure_summary_is_null(valid_experiment_spec):
    """failure_summary is null for a successful dry-run."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    assert artifact["failure_summary"] is None


# ---------------------------------------------------------------------------
# Test: experiment_spec_ref matches experiment_id
# ---------------------------------------------------------------------------

def test_experiment_spec_ref_matches_experiment_id(valid_experiment_spec):
    """experiment_spec_ref matches the experiment_id from the experiment spec."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    assert artifact["experiment_spec_ref"] == "EXP-2026-0001"


# ---------------------------------------------------------------------------
# Test: input_artifact_refs has ExperimentSpec entry
# ---------------------------------------------------------------------------

def test_input_artifact_refs_has_experiment_spec(valid_experiment_spec):
    """input_artifact_refs contains an ExperimentSpec entry."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    artifact_types = {ref["artifact_type"] for ref in artifact["input_artifact_refs"]}
    assert "ExperimentSpec" in artifact_types
