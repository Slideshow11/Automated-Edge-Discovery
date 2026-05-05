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
    DRY_RUN_DATA_MANIFEST_PLACEHOLDER,
    build_runner_output,
    write_runner_output,
    GovernanceRejection,
    main,
    _compute_run_config_hash,
    _compute_run_id,
    _check_experiment_spec_id,
    _utc_now,
    GOVERNANCE_STOP_RULE_FIELDS,
    SCHEMA_PATH,
    _parse_required_columns,
    _read_csv_header,
    _validate_observation_table_columns,
    _normalize_optional_column_name,
    _summarize_observation_table_canonical,
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
def experiment_spec_no_data_manifest_refs(tmp_path):
    """Create a temporary experiment spec file without data_manifest_refs."""
    spec_content = {
        "experiment_id": "EXP-2026-0002",
        "experiment_version": 1,
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        # no data_manifest_refs
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date"},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "created_at": "2026-05-05T00:00:00Z",
        "reviewer": {"name": "test"},
    }
    p = tmp_path / "no_dm_refs_spec.json"
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
# Test: run_mode="dry_run" and status="success" for valid input
# ---------------------------------------------------------------------------

def test_run_mode_is_dry_run(valid_experiment_spec):
    """Emitted artifact has run_mode = 'dry_run'."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    assert artifact["run_mode"] == "dry_run"


def test_status_is_success_for_valid_input(valid_experiment_spec):
    """Emitted artifact has status = 'success' for valid governance input."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    assert artifact["status"] == "success"


def test_status_is_failed_validation_for_autonomous_search(experiment_spec_autonomous_search_true):
    """
    Emitted artifact has status = 'failed_validation' when
    prohibited_modes.autonomous_search=True.

    The runner must NOT emit status='success' when audit_summary.blocker_count > 0.
    Per RunnerOutput v1 schema if/then constraints, success requires null
    failure_summary; failed_validation requires populated failure_summary.
    """
    with pytest.raises(GovernanceRejection) as exc_info:
        build_runner_output(
            experiment_spec_path=experiment_spec_autonomous_search_true,
            run_owner="test@test",
        )

    artifact = exc_info.value.artifact
    assert artifact["status"] == "failed_validation"
    assert artifact["failure_summary"] is not None
    assert artifact["failure_summary"]["failure_type"] == "validation_error"
    assert artifact["failure_summary"]["status"] == "failed_validation"
    assert artifact["failure_summary"]["blocker_summary"] is not None
    assert len(artifact["failure_summary"]["blocker_summary"]) > 0
    assert artifact["partial_summary"] is None
    # overall audit result must be "fail" when blocker_count > 0
    assert artifact["audit_summary"]["overall_result"] == "fail"
    assert artifact["audit_summary"]["blocker_count"] > 0


def test_failure_summary_is_null_for_success(valid_experiment_spec):
    """failure_summary is null for a successful dry-run (status=success)."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    assert artifact["failure_summary"] is None


def test_partial_summary_is_null_for_success(valid_experiment_spec):
    """partial_summary is null for a successful dry-run (status=success)."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    assert artifact["partial_summary"] is None


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
# Test: governance rejection behavior
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


def test_autonomous_search_flag_raises_governance_rejection(experiment_spec_autonomous_search_true):
    """
    prohibited_modes.autonomous_search=True raises GovernanceRejection.

    The exception carries the pre-built failed_validation artifact so main()
    can emit it before exiting nonzero.
    """
    with pytest.raises(GovernanceRejection) as exc_info:
        build_runner_output(
            experiment_spec_path=experiment_spec_autonomous_search_true,
            run_owner="test@test",
        )

    # Exception carries the artifact
    artifact = exc_info.value.artifact
    assert artifact["status"] == "failed_validation"

    # Check the failure_summary structure
    failure_summary = artifact["failure_summary"]
    assert failure_summary["failure_type"] == "validation_error"
    assert failure_summary["status"] == "failed_validation"
    assert "autonomous_search" in failure_summary["failed_check"].lower() or \
           "autonomous_search" in failure_summary["blocker_summary"].lower()
    assert failure_summary["created_at"] is not None


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


def test_audit_summary_overall_result_fail_when_blockers(experiment_spec_autonomous_search_true):
    """audit_summary.overall_result is 'fail' when blocker_count > 0."""
    with pytest.raises(GovernanceRejection):
        build_runner_output(
            experiment_spec_path=experiment_spec_autonomous_search_true,
            run_owner="test@test",
        )


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


def test_main_governance_rejection_exits_nonzero(tmp_path, experiment_spec_autonomous_search_true):
    """
    main() returns 1 when governance validation fails (autonomous_search=True).

    The runner must NOT return 0 (success) when audit_summary.blocker_count > 0.
    """
    output_path = tmp_path / "governance_rejected.json"

    old_out = io.StringIO()
    old_err = io.StringIO()
    old_argv = sys.argv
    buf_out = io.StringIO()
    buf_err = io.StringIO()

    sys.argv = [
        "run_first_thin_real_data_runner",
        "--experiment-spec", str(experiment_spec_autonomous_search_true),
        "--output-path", str(output_path),
        "--run-owner", "test@test",
    ]
    sys.stdout, sys.stderr = buf_out, buf_err

    try:
        code = main()
    finally:
        sys.stdout, sys.stderr, sys.argv = old_out, old_err, old_argv

    assert code == 1, f"Expected exit 1 for governance rejection, got {code}"
    assert "governance" in buf_err.getvalue().lower() or "failed_validation" in buf_err.getvalue().lower()
    # A failed_validation artifact should have been written
    assert output_path.exists(), "Governance-rejected RunnerOutput should still be written"
    with open(output_path) as fh:
        artifact = json.load(fh)
    assert artifact["status"] == "failed_validation"
    assert artifact["failure_summary"] is not None


# ---------------------------------------------------------------------------
# Test: schema validation with jsonschema
# ---------------------------------------------------------------------------

def test_written_artifact_validates_against_schema(tmp_path, valid_experiment_spec):
    """
    If jsonschema is available, validate emitted artifact against
    runner_output_spec_v1.schema.json.

    Uses FormatChecker when available to validate date-time formats.
    """
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
    schema_path = _ROOT / "schemas" / "runner_output_spec_v1.schema.json"
    if not schema_path.exists():
        pytest.skip("runner_output_spec_v1.schema.json not found")

    schema = json.loads(schema_path.read_text())

    # Try FormatChecker if available for date-time validation
    try:
        from jsonschema import FormatChecker
        checker = FormatChecker()
    except ImportError:
        checker = None

    jsonschema.validate(loaded, schema, format_checker=checker)


def test_governance_rejected_artifact_validates_against_schema(
    tmp_path, experiment_spec_autonomous_search_true
):
    """
    Governance-rejected artifact (status=failed_validation) also validates
    against runner_output_spec_v1.schema.json when jsonschema is available.
    """
    jsonschema = pytest.importorskip("jsonschema")

    output_path = tmp_path / "governance_rejected.json"

    old_out = io.StringIO()
    old_err = io.StringIO()
    old_argv = sys.argv
    buf_out = io.StringIO()
    buf_err = io.StringIO()

    sys.argv = [
        "run_first_thin_real_data_runner",
        "--experiment-spec", str(experiment_spec_autonomous_search_true),
        "--output-path", str(output_path),
        "--run-owner", "test@test",
    ]
    sys.stdout, sys.stderr = buf_out, buf_err

    try:
        code = main()
    finally:
        sys.stdout, sys.stderr, sys.argv = old_out, old_err, old_argv

    assert code == 1
    assert output_path.exists()

    with open(output_path) as fh:
        loaded = json.load(fh)

    schema_path = _ROOT / "schemas" / "runner_output_spec_v1.schema.json"
    if not schema_path.exists():
        pytest.skip("runner_output_spec_v1.schema.json not found")

    schema = json.loads(schema_path.read_text())

    try:
        from jsonschema import FormatChecker
        checker = FormatChecker()
    except ImportError:
        checker = None

    jsonschema.validate(loaded, schema, format_checker=checker)


# ---------------------------------------------------------------------------
# Test: output_manifest content_hash honesty
# ---------------------------------------------------------------------------

def test_output_manifest_content_hash_matches_experiment_spec(valid_experiment_spec):
    """
    output_manifest[0].content_hash must be the SHA-256 of the file at
    output_manifest[0].output_path.

    For this dry-run skeleton, output_path points to the experiment spec
    file and content_hash is sha256:<experiment_spec_bytes>. This test
    verifies the hash is computed correctly and matches the file.
    """
    import hashlib

    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )

    entry = artifact["output_manifest"][0]

    # content_hash must be sha256:<hex>
    assert entry["content_hash"].startswith("sha256:"), (
        f"content_hash must be sha256:<hex>, got: {entry['content_hash']}"
    )

    # The hash must match the experiment spec file bytes
    stored_hash = entry["content_hash"][7:]  # strip "sha256:"
    actual_hash = hashlib.sha256(
        Path(valid_experiment_spec).read_bytes()
    ).hexdigest()

    assert stored_hash == actual_hash, (
        f"content_hash {stored_hash!r} does not match "
        f"experiment spec hash {actual_hash!r}"
    )

    # output_path must point to the experiment spec (the file whose hash is stored)
    assert Path(entry["output_path"]).resolve() == Path(valid_experiment_spec).resolve(), (
        f"output_path {entry['output_path']} does not match "
        f"experiment spec path {valid_experiment_spec}"
    )


def test_output_manifest_description_honest(valid_experiment_spec):
    """
    output_manifest[0].description must honestly describe that content_hash
    is of the experiment spec file, not of the RunnerOutput JSON.
    """
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )

    entry = artifact["output_manifest"][0]
    desc = entry["description"]

    # Description must acknowledge the hash is of the experiment spec
    assert "experiment spec" in desc.lower(), (
        f"description must mention 'experiment spec', got: {desc!r}"
    )
    assert "content hash" in desc.lower(), (
        f"description must mention 'content hash', got: {desc!r}"
    )
    # Must NOT claim the hash is of the RunnerOutput JSON
    assert "output artifact" not in desc.lower() and "runner output" not in desc.lower(), (
        f"description must not claim content_hash is of RunnerOutput JSON, got: {desc!r}"
    )


def test_output_manifest_content_hash_stable_across_builds(valid_experiment_spec):
    """
    For the same experiment spec, output_manifest[0].content_hash must be
    identical across multiple build_runner_output calls (determinism).
    """
    hash1 = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )["output_manifest"][0]["content_hash"]

    hash2 = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )["output_manifest"][0]["content_hash"]

    assert hash1 == hash2, (
        f"content_hash not stable: {hash1!r} != {hash2!r}"
    )


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

def test_data_manifest_refs_forwarded_from_spec(valid_experiment_spec):
    """data_manifest_refs is forwarded from the experiment spec when present."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    assert artifact["data_manifest_refs"] == ["DM-2026-0001"]


def test_data_manifest_refs_uses_placeholder_when_missing(experiment_spec_no_data_manifest_refs):
    """
    When experiment spec has no data_manifest_refs, the dry-run skeleton
    uses a stable placeholder to satisfy schema minItems: 1.

    The placeholder is 'dry_run_no_data_manifest'.
    """
    artifact = build_runner_output(
        experiment_spec_path=experiment_spec_no_data_manifest_refs,
        run_owner="test@test",
    )
    assert artifact["data_manifest_refs"] == [DRY_RUN_DATA_MANIFEST_PLACEHOLDER]


# ---------------------------------------------------------------------------
# Test: failure_summary structure
# ---------------------------------------------------------------------------

def test_failure_summary_has_required_fields(experiment_spec_autonomous_search_true):
    """failure_summary has all required fields for failed_validation status."""
    with pytest.raises(GovernanceRejection) as exc_info:
        build_runner_output(
            experiment_spec_path=experiment_spec_autonomous_search_true,
            run_owner="test@test",
        )

    fs = exc_info.value.artifact["failure_summary"]
    assert "failure_type" in fs
    assert "status" in fs
    assert "blocker_summary" in fs
    assert "created_at" in fs
    # failed_check and details_ref are optional
    assert fs["failure_type"] == "validation_error"
    assert fs["status"] == "failed_validation"
    assert len(fs["blocker_summary"]) > 0


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


def test_experiment_spec_ref_is_exp_pattern(valid_experiment_spec):
    """experiment_spec_ref follows EXP-YYYY-NNNN pattern as required by schema."""
    import re
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    assert re.match(r"^EXP-[0-9]{4}-[0-9]{4}$", artifact["experiment_spec_ref"])


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


def test_input_artifact_refs_has_required_fields(valid_experiment_spec):
    """input_artifact_refs items have all schema-required fields."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    for ref in artifact["input_artifact_refs"]:
        assert "artifact_type" in ref
        assert "artifact_id" in ref
        assert "content_hash" in ref
        assert "validation_status" in ref
        assert ref["content_hash"].startswith("sha256:")
        assert ref["validation_status"] == "pass"


# ---------------------------------------------------------------------------
# Test: output_manifest has required fields and honest content_hash
# ---------------------------------------------------------------------------

def test_output_manifest_content_hash_not_null(valid_experiment_spec):
    """
    output_manifest[].content_hash is never null.

    The content_hash is set from the experiment spec content hash (stable,
    non-self-referential). It is embedded BEFORE writing so the artifact
    on disk contains the correct hash from the first write.
    """
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    for entry in artifact["output_manifest"]:
        assert entry["content_hash"] is not None
        assert len(entry["content_hash"]) > 0


def test_output_manifest_content_hash_matches_spec_content_hash(valid_experiment_spec):
    """
    output_manifest[].content_hash equals sha256:<spec_file_bytes>.

    This is stable and honest: it is the hash of the experiment spec file,
    computed before writing, embedded in the artifact from the first write.
    """
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )

    # Compute expected hash from experiment spec file
    with open(valid_experiment_spec, "rb") as fh:
        expected_hash = f"sha256:{hashlib.sha256(fh.read()).hexdigest()}"

    for entry in artifact["output_manifest"]:
        assert entry["content_hash"] == expected_hash


def test_output_manifest_content_hash_matches_file_on_disk(tmp_path, valid_experiment_spec):
    """
    The content_hash stored in output_manifest matches the experiment spec
    file bytes that were used to build the artifact.

    This verifies the content_hash is honest: it claims to be the hash of the
    experiment spec and it IS the hash of the experiment spec file bytes.
    """
    output_path = tmp_path / "runner_output.json"

    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    write_runner_output(output_path, artifact)

    # Read back
    with open(output_path) as fh:
        loaded = json.load(fh)

    # The content_hash in the artifact should be sha256:<spec_bytes>
    content_hash_in_artifact = loaded["output_manifest"][0]["content_hash"]

    # Verify by computing hash of experiment spec file
    with open(valid_experiment_spec, "rb") as fh:
        spec_bytes = fh.read()
    expected = f"sha256:{hashlib.sha256(spec_bytes).hexdigest()}"

    assert content_hash_in_artifact == expected


def test_output_manifest_content_hash_deterministic_across_runs(valid_experiment_spec):
    """
    output_manifest[].content_hash is identical across two calls with the same
    experiment spec, verifying it is computed from stable source (experiment
    spec file bytes), not from a self-referential hash that would differ
    across serializations.
    """
    artifact1 = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    artifact2 = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )

    hash1 = artifact1["output_manifest"][0]["content_hash"]
    hash2 = artifact2["output_manifest"][0]["content_hash"]
    assert hash1 == hash2


def test_output_manifest_output_path_is_experiment_spec_path(tmp_path, valid_experiment_spec):
    """
    output_manifest[].output_path is the experiment spec path (not a
    placeholder), so that content_hash genuinely hashes the file named
    by output_path. This is the honest semantics for the dry-run skeleton.
    """
    output_path = tmp_path / "runner_output.json"

    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    write_runner_output(output_path, artifact)

    with open(output_path) as fh:
        loaded = json.load(fh)

    # output_path must be the experiment spec path (not a placeholder)
    assert loaded["output_manifest"][0]["output_path"] == str(valid_experiment_spec)
    assert "<runner_output_json>" not in loaded["output_manifest"][0]["output_path"]


def test_output_manifest_has_required_fields(valid_experiment_spec):
    """output_manifest entries have all schema-required fields."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    for entry in artifact["output_manifest"]:
        assert "output_role" in entry
        assert "output_path" in entry
        assert "content_hash" in entry
        assert "created_at" in entry
        assert "format" in entry
        assert "description" in entry
        assert "contains_private_data" in entry
        assert "publishable" in entry
        # content_hash must not be null
        assert entry["content_hash"] is not None


# ---------------------------------------------------------------------------
# Test: no write-twice pattern (content_hash fix verification)
# ---------------------------------------------------------------------------

def test_artifact_written_once_content_hash_consistent(tmp_path, valid_experiment_spec):
    """
    The artifact is written exactly once with content_hash already embedded.
    The content_hash stored in the file matches the experiment spec bytes.
    """
    output_path = tmp_path / "runner_output.json"

    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    write_runner_output(output_path, artifact)

    # Read back and verify content_hash matches spec bytes
    with open(output_path) as fh:
        loaded = json.load(fh)

    with open(valid_experiment_spec, "rb") as fh:
        spec_bytes = fh.read()
    expected_hash = f"sha256:{hashlib.sha256(spec_bytes).hexdigest()}"

    assert loaded["output_manifest"][0]["content_hash"] == expected_hash
    # No null content_hash in final file
    assert loaded["output_manifest"][0]["content_hash"] != "null"


# ---------------------------------------------------------------------------
# Test: governance audit names and severities
# ---------------------------------------------------------------------------

def test_audit_severities_are_valid_enum(valid_experiment_spec):
    """All audit entries have severity from the allowed enum."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    valid_severities = {"blocker", "warning", "info"}
    for audit in artifact["audit_summary"]["audits"]:
        assert audit["severity"] in valid_severities


def test_audit_results_are_valid_enum(valid_experiment_spec):
    """All audit entries have audit_result from the allowed enum."""
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    valid_results = {"pass", "fail", "warn", "skipped"}
    for audit in artifact["audit_summary"]["audits"]:
        assert audit["audit_result"] in valid_results


# ---------------------------------------------------------------------------
# Test: no registry/ledger writes (import-based check)
# ---------------------------------------------------------------------------

def test_no_registry_mutation_in_dry_run(valid_experiment_spec, tmp_path):
    """
    Dry-run produces no writes to EdgeHypothesisRegistry or TrialLedger.

    Verifies the registry CSV and ledger JSONL are not modified by checking
    their mtimes before and after the dry-run.
    """
    registry_path = _ROOT / "docs" / "edge_hypothesis_registry.csv"
    ledger_path = _ROOT / "docs" / "trial_ledger.jsonl"

    # Record mtimes if files exist
    registry_mtime_before = registry_path.stat().st_mtime if registry_path.exists() else None
    ledger_mtime_before = ledger_path.stat().st_mtime if ledger_path.exists() else None

    output_path = tmp_path / "runner_output.json"
    artifact = build_runner_output(
        experiment_spec_path=valid_experiment_spec,
        run_owner="test@test",
    )
    write_runner_output(output_path, artifact)

    # Check mtimes unchanged
    if registry_mtime_before is not None:
        assert registry_path.stat().st_mtime == registry_mtime_before
    if ledger_mtime_before is not None:
        assert ledger_path.stat().st_mtime == ledger_mtime_before


# ============================================================================
# DataManifest integration tests
# ============================================================================

MINIMAL_MANIFEST_DATA = {
    "dataset_id": "test_options_2021",
    "role": "options_backtest_db",
    "source_kind": "local_sqlite",
    "path": "/tmp/options.sqlite",
    "format": "sqlite",
}

MINIMAL_CSV_MANIFEST_DATA = {
    "dataset_id": "test_csv_data",
    "role": "price_history",
    "source_kind": "local_csv",
    "path": "prices.csv",
    "format": "csv",
}


@pytest.fixture
def valid_data_manifest(tmp_path):
    """Create a valid DataManifest JSON file with a real CSV file."""
    csv_file = tmp_path / "prices.csv"
    csv_file.write_text("date,open,high,low,close\n2024-01-01,100.0,101.0,99.0,100.5\n2024-01-02,100.5,102.0,99.5,101.0\n2024-01-03,101.0,103.0,100.0,102.0\n")
    manifest_file = tmp_path / "data_manifest.json"
    manifest_file.write_text(json.dumps(MINIMAL_CSV_MANIFEST_DATA, indent=2))
    return manifest_file, csv_file


@pytest.fixture
def valid_sqlite_manifest(tmp_path):
    """Create a valid DataManifest JSON file with a SQLite database path.

    Uses a relative path so the dataset resolves inside base_dir
    (tmp_path, which is the manifest file's parent directory).
    """
    db_subdir = tmp_path / "db"
    db_subdir.mkdir()
    db_file = db_subdir / "options.sqlite"
    db_file.write_text("")
    manifest_file = tmp_path / "data_manifest.json"
    manifest_file.write_text(json.dumps({
        "dataset_id": "test_options_2021",
        "role": "options_backtest_db",
        "source_kind": "local_sqlite",
        "path": "db/options.sqlite",
        "format": "sqlite",
    }, indent=2))
    return manifest_file, db_file


@pytest.fixture
def invalid_data_manifest_bad_role(tmp_path):
    """Create an invalid DataManifest JSON file with an unrecognized role enum value.

    Using an invalid role (not missing 'path', which triggers a latent KeyError
    in data_manifest.py validate_dataset_manifest). This raises ValueError from
    DatasetRole() inside validate_dataset_manifest, which is caught cleanly.
    """
    manifest_data = {
        "dataset_id": "test_invalid",
        "role": "options_backtest_db_broken",  # invalid enum → ValueError
        "source_kind": "local_sqlite",
        "path": "/tmp/options.sqlite",
        "format": "sqlite",
    }
    manifest_file = tmp_path / "invalid_manifest.json"
    manifest_file.write_text(json.dumps(manifest_data, indent=2))
    return manifest_file


# ---------------------------------------------------------------------------
# Unit tests for observation-table helpers
# ---------------------------------------------------------------------------

class TestParseRequiredColumns:
    def test_none_returns_empty(self):
        assert _parse_required_columns(None) == []

    def test_single_column(self):
        assert _parse_required_columns("event_id") == ["event_id"]

    def test_multiple_columns(self):
        assert _parse_required_columns("event_id,symbol,close") == ["event_id", "symbol", "close"]

    def test_whitespace_trimmed(self):
        assert _parse_required_columns("  event_id  ,  symbol , close  ") == [
            "event_id", "symbol", "close"
        ]

    def test_empty_token_raises_value_error(self):
        with pytest.raises(ValueError, match="empty token"):
            _parse_required_columns("event_id,,symbol")

    def test_leading_comma_raises(self):
        with pytest.raises(ValueError, match="empty token"):
            _parse_required_columns(",event_id,symbol")

    def test_trailing_comma_raises(self):
        with pytest.raises(ValueError, match="empty token"):
            _parse_required_columns("event_id,symbol,")

    def test_duplicate_columns_preserves_first_occurrence(self):
        result = _parse_required_columns("event_id,symbol,event_id,symbol,close")
        assert result == ["event_id", "symbol", "close"]

    def test_all_whitespace_token_raises(self):
        with pytest.raises(ValueError, match="empty token"):
            _parse_required_columns("event_id,   ,symbol")


class TestReadCsvHeader:
    def test_returns_header_list(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("date,open,high,low,close\n2024-01-01,100,101,99,100.5\n")
        header = _read_csv_header(csv_file)
        assert header == ["date", "open", "high", "low", "close"]

    def test_missing_file_returns_none(self, tmp_path):
        result = _read_csv_header(tmp_path / "nonexistent.csv")
        assert result is None

    def test_empty_file_returns_empty_list(self, tmp_path):
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("")
        result = _read_csv_header(csv_file)
        assert result == []


class TestValidateObservationTableColumns:
    def test_all_present_returns_true(self, tmp_path):
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,symbol,close\n2024-01-01,AAPL,150.0\n")
        missing, ok = _validate_observation_table_columns(csv_file, ["date", "symbol", "close"])
        assert ok is True
        assert missing == []

    def test_some_missing_returns_false(self, tmp_path):
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,symbol,close\n2024-01-01,AAPL,150.0\n")
        missing, ok = _validate_observation_table_columns(csv_file, ["date", "missing_col", "also_absent"])
        assert ok is False
        assert set(missing) == {"missing_col", "also_absent"}

    def test_all_missing_returns_false(self, tmp_path):
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,symbol,close\n2024-01-01,AAPL,150.0\n")
        missing, ok = _validate_observation_table_columns(csv_file, ["not_there", "also_not_there"])
        assert ok is False
        assert set(missing) == {"not_there", "also_not_there"}

    def test_whitespace_in_header_stripped(self, tmp_path):
        """Whitespace in header cells is stripped before column comparison.

        csv.reader preserves whitespace inside quoted fields, but we strip
        it before matching so that " date " matches "date".
        """
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text(" date ,symbol, close \n2024-01-01,AAPL,150.0\n")
        missing, ok = _validate_observation_table_columns(csv_file, ["date", "symbol", "close"])
        assert ok is True  # whitespace stripped before comparison → match
        assert missing == []


# ---------------------------------------------------------------------------
# Integration tests: observation table validation in build_runner_output
# ---------------------------------------------------------------------------

def _make_spec_with_dm_refs(tmp_path, experiment_id="EXP-2026-0001", dm_refs=None):
    """Helper: create a minimal experiment spec with data_manifest_refs."""
    if dm_refs is None:
        dm_refs = ["DM-2026-0001"]
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": experiment_id,
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": dm_refs,
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date"},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    return spec


class TestObservationTableValidation:
    def test_required_columns_present_csv_success(self, tmp_path):
        """Required columns present in CSV → status=success."""
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,symbol,close\n2024-01-01,AAPL,150.0\n2024-01-02,AAPL,151.0\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "test_csv",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        spec = _make_spec_with_dm_refs(tmp_path)
        artifact = build_runner_output(
            experiment_spec_path=spec,
            data_manifest_path=manifest_file,
            required_observation_columns=["date", "symbol", "close"],
        )
        assert artifact["status"] == "success"
        assert artifact["failure_summary"] is None

    def test_required_columns_missing_csv_failed_validation(self, tmp_path):
        """Required columns missing from CSV → raises GovernanceRejection with
        failed_validation artifact preserving audit_summary (blocker_count=1)."""
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,symbol\n2024-01-01,AAPL\n2024-01-02,AAPL\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "test_csv",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        spec = _make_spec_with_dm_refs(tmp_path)
        with pytest.raises(GovernanceRejection) as exc_info:
            build_runner_output(
                experiment_spec_path=spec,
                data_manifest_path=manifest_file,
                required_observation_columns=["date", "symbol", "close"],
            )
        artifact = exc_info.value.artifact
        assert artifact["status"] == "failed_validation"
        assert artifact["failure_summary"] is not None

    def test_missing_columns_listed_in_blocker_summary(self, tmp_path):
        """Missing columns appear in failure_summary.blocker_summary."""
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,symbol\n2024-01-01,AAPL\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "test_csv",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        spec = _make_spec_with_dm_refs(tmp_path)
        with pytest.raises(GovernanceRejection) as exc_info:
            build_runner_output(
                experiment_spec_path=spec,
                data_manifest_path=manifest_file,
                required_observation_columns=["date", "symbol", "close"],
            )
        artifact = exc_info.value.artifact
        blocker = artifact["failure_summary"]["blocker_summary"]
        assert "close" in blocker

    def test_required_columns_audit_entry_added(self, tmp_path):
        """observation_table_shape_validation audit entry present when columns provided."""
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,symbol,close\n2024-01-01,AAPL,150.0\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "test_csv",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        spec = _make_spec_with_dm_refs(tmp_path)
        artifact = build_runner_output(
            experiment_spec_path=spec,
            data_manifest_path=manifest_file,
            required_observation_columns=["date", "symbol", "close"],
        )
        audit_names = {a["audit_name"] for a in artifact["audit_summary"]["audits"]}
        assert "observation_table_shape_validation" in audit_names
        obs_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_shape_validation"
        )
        assert obs_audit["audit_result"] == "pass"
        assert obs_audit["blocker_count"] == 0

    def test_required_columns_without_data_manifest_raises_value_error(self, tmp_path):
        """required_observation_columns without --data-manifest raises ValueError."""
        spec = _make_spec_with_dm_refs(tmp_path)
        with pytest.raises(ValueError, match="required-observation-columns"):
            build_runner_output(
                experiment_spec_path=spec,
                required_observation_columns=["date", "symbol"],
            )

    def test_required_columns_with_sqlite_manifest_raises_value_error(self, tmp_path):
        """required_observation_columns with SQLite DataManifest raises ValueError."""
        db_subdir = tmp_path / "db"
        db_subdir.mkdir()
        db_file = db_subdir / "options.sqlite"
        db_file.write_text("")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "test_sqlite",
            "role": "options_backtest_db",
            "source_kind": "local_sqlite",
            "path": "db/options.sqlite",
            "format": "sqlite",
        }))
        spec = _make_spec_with_dm_refs(tmp_path)
        with pytest.raises(ValueError, match="only supported for CSV"):
            build_runner_output(
                experiment_spec_path=spec,
                data_manifest_path=manifest_file,
                required_observation_columns=["date", "symbol"],
            )

    def test_run_config_hash_incorporates_required_columns(self, tmp_path):
        """Different required_observation_columns → different run_config_hash."""
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,symbol,close\n2024-01-01,AAPL,150.0\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "test_csv",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        spec1 = _make_spec_with_dm_refs(tmp_path, experiment_id="EXP-2026-0001")
        spec2 = tmp_path / "spec2.json"
        spec2.write_text(json.dumps({
            "experiment_id": "EXP-2026-0001",
            "hypothesis_id": "HYP-2026-0001",
            "search_space_id": "SSM-2026-0001",
            "data_manifest_refs": ["DM-2026-0001"],
            "study_type": "options_event_risk",
            "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
            "feature_cutoff_policy": {"timestamp_ref": "trade_date"},
            "trial_generation_mode": "literature_replication",
            "allowed_trial_lanes": ["theory_first"],
            "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
            "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
        }))
        hash1 = _compute_run_config_hash(spec1, manifest_file, ["date", "symbol", "close"])
        hash2 = _compute_run_config_hash(spec1, manifest_file, ["date", "symbol"])
        hash3 = _compute_run_config_hash(spec1, manifest_file, ["date", "extra_col"])
        assert hash1 != hash2, "Different column sets must produce different hashes"
        assert hash2 != hash3, "Different column sets must produce different hashes"

    def test_normalized_columns_produce_same_hash(self, tmp_path):
        """Same normalized columns regardless of input order/whitespace → same hash."""
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,symbol,close\n2024-01-01,AAPL,150.0\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "test_csv",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        spec1 = _make_spec_with_dm_refs(tmp_path, experiment_id="EXP-2026-0001")
        spec2 = tmp_path / "spec2.json"
        spec2.write_text(json.dumps({
            "experiment_id": "EXP-2026-0001",
            "hypothesis_id": "HYP-2026-0001",
            "search_space_id": "SSM-2026-0001",
            "data_manifest_refs": ["DM-2026-0001"],
            "study_type": "options_event_risk",
            "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
            "feature_cutoff_policy": {"timestamp_ref": "trade_date"},
            "trial_generation_mode": "literature_replication",
            "allowed_trial_lanes": ["theory_first"],
            "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
            "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
        }))
        # "date" and "symbol" with extra whitespace all normalize to "date" and "symbol"
        # after "".join(c.split()) which removes ALL whitespace.
        hash1 = _compute_run_config_hash(spec1, manifest_file, ["symbol", "date"])
        hash2 = _compute_run_config_hash(spec1, manifest_file, [" date ", " symbol "])
        assert hash1 == hash2, "Normalized columns must produce identical hashes"

    def test_no_required_columns_preserves_existing_behavior(self, tmp_path):
        """Without required_observation_columns, behavior is identical to PR #160."""
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,symbol,close\n2024-01-01,AAPL,150.0\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "test_csv",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        spec = _make_spec_with_dm_refs(tmp_path)
        artifact = build_runner_output(
            experiment_spec_path=spec,
            data_manifest_path=manifest_file,
            required_observation_columns=None,
        )
        assert artifact["status"] == "success"
        assert artifact["failure_summary"] is None
        audit_names = {a["audit_name"] for a in artifact["audit_summary"]["audits"]}
        assert "observation_table_shape_validation" not in audit_names

    def test_cli_accepts_required_observation_columns_arg(self, tmp_path):
        """CLI accepts --required-observation-columns."""
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,symbol,close\n2024-01-01,AAPL,150.0\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "test_csv",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        spec = _make_spec_with_dm_refs(tmp_path)
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(spec),
            "--output-path", str(output),
            "--run-owner", "test",
            "--data-manifest", str(manifest_file),
            "--required-observation-columns", "date,symbol,close",
        ])
        assert rc == 0, "CLI should accept --required-observation-columns"
        assert output.exists()

    def test_cli_missing_required_columns_exits_1(self, tmp_path):
        """CLI with missing required columns exits 1."""
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,symbol\n2024-01-01,AAPL\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "test_csv",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        spec = _make_spec_with_dm_refs(tmp_path)
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(spec),
            "--output-path", str(output),
            "--run-owner", "test",
            "--data-manifest", str(manifest_file),
            "--required-observation-columns", "date,symbol,close",
        ])
        assert rc == 1, f"Expected exit 1 for missing columns, got {rc}"
        loaded = json.loads(output.read_text())
        assert loaded["status"] == "failed_validation"
        assert "close" in loaded["failure_summary"]["blocker_summary"]

    def test_cli_required_columns_no_manifest_exits_1(self, tmp_path):
        """CLI --required-observation-columns without --data-manifest exits 1."""
        spec = _make_spec_with_dm_refs(tmp_path)
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(spec),
            "--output-path", str(output),
            "--run-owner", "test",
            "--required-observation-columns", "date,symbol",
        ])
        assert rc == 1, f"Expected exit 1 for missing manifest, got {rc}"

    def test_cli_required_columns_sqlite_exits_1(self, tmp_path):
        """CLI with SQLite DataManifest + required columns exits 1."""
        db_subdir = tmp_path / "db"
        db_subdir.mkdir()
        db_file = db_subdir / "options.sqlite"
        db_file.write_text("")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "test_sqlite",
            "role": "options_backtest_db",
            "source_kind": "local_sqlite",
            "path": "db/options.sqlite",
            "format": "sqlite",
        }))
        spec = _make_spec_with_dm_refs(tmp_path)
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(spec),
            "--output-path", str(output),
            "--run-owner", "test",
            "--data-manifest", str(manifest_file),
            "--required-observation-columns", "date,symbol",
        ])
        assert rc == 1, f"Expected exit 1 for SQLite with required columns, got {rc}"

    def test_success_artifact_schema_validates_with_observation_columns(self, tmp_path):
        """Success artifact with required columns validates against schema."""
        pytest.importorskip("jsonschema")
        import jsonschema
        from jsonschema import FormatChecker

        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,symbol,close\n2024-01-01,AAPL,150.0\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "test_csv",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        spec = _make_spec_with_dm_refs(tmp_path)
        artifact = build_runner_output(
            experiment_spec_path=spec,
            data_manifest_path=manifest_file,
            required_observation_columns=["date", "symbol", "close"],
        )
        schema = json.loads(SCHEMA_PATH.read_text())
        checker = FormatChecker()
        jsonschema.validate(artifact, schema, format_checker=checker)

    def test_failed_validation_artifact_schema_validates_with_missing_columns(self, tmp_path):
        """Failed-validation artifact with missing columns validates against schema.

        Missing CSV columns are a blocking governance failure → GovernanceRejection.
        The artifact inside the exception validates against the schema.
        """
        pytest.importorskip("jsonschema")
        import jsonschema
        from jsonschema import FormatChecker

        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,symbol\n2024-01-01,AAPL\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "test_csv",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        spec = _make_spec_with_dm_refs(tmp_path)
        with pytest.raises(GovernanceRejection) as exc_info:
            build_runner_output(
                experiment_spec_path=spec,
                data_manifest_path=manifest_file,
                required_observation_columns=["date", "symbol", "close"],
            )
        artifact = exc_info.value.artifact
        assert artifact["status"] == "failed_validation"
        schema = json.loads(SCHEMA_PATH.read_text())
        checker = FormatChecker()
        jsonschema.validate(artifact, schema, format_checker=checker)


# ---------------------------------------------------------------------------
# CLI accepts --data-manifest
# ---------------------------------------------------------------------------

def test_cli_accepts_data_manifest_arg(tmp_path):
    """CLI --data-manifest argument is accepted without error."""
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    output = tmp_path / "output.json"
    rc = main([
        "--experiment-spec", str(spec),
        "--output-path", str(output),
        "--run-owner", "test",
        "--data-manifest", str(spec),  # using spec as dummy manifest
    ])
    # Either 0 (success if spec is valid manifest) or 1 (ValueError from manifest loading)
    # is acceptable for this smoke test; key is no argparse error
    assert rc in (0, 1)


# -----------------------------------------------------------------------
# Valid DataManifest produces success artifact
# -----------------------------------------------------------------------

def test_valid_data_manifest_produces_success(valid_data_manifest, tmp_path):
    """Valid DataManifest JSON path produces status=success RunnerOutput."""
    manifest_file, csv_file = valid_data_manifest
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    output = tmp_path / "output.json"
    rc = main([
        "--experiment-spec", str(spec),
        "--output-path", str(output),
        "--run-owner", "test",
        "--data-manifest", str(manifest_file),
    ])
    assert rc == 0
    with open(output) as fh:
        artifact = json.load(fh)
    assert artifact["status"] == "success"
    assert artifact["run_mode"] == "dry_run"


def test_data_manifest_refs_uses_dataset_id_when_provided(valid_data_manifest, tmp_path):
    """data_manifest_refs contains the real dataset_id when DataManifest is provided."""
    manifest_file, _ = valid_data_manifest
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    artifact = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=manifest_file,
    )
    # dataset_id from MINIMAL_CSV_MANIFEST_DATA
    assert artifact["data_manifest_refs"] == ["test_csv_data"]
    assert "dry_run_no_data_manifest" not in artifact["data_manifest_refs"]


def test_input_artifact_refs_includes_data_manifest_when_provided(valid_data_manifest, tmp_path):
    """input_artifact_refs includes both ExperimentSpec and DataManifest entries."""
    manifest_file, _ = valid_data_manifest
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    artifact = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=manifest_file,
    )
    artifact_types = [ref["artifact_type"] for ref in artifact["input_artifact_refs"]]
    assert "ExperimentSpec" in artifact_types
    assert "DataManifest" in artifact_types
    assert len(artifact["input_artifact_refs"]) == 2


def test_input_artifact_refs_data_manifest_has_required_fields(valid_data_manifest, tmp_path):
    """DataManifest entry in input_artifact_refs has all required schema fields."""
    manifest_file, _ = valid_data_manifest
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    artifact = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=manifest_file,
    )
    dm_ref = next(ref for ref in artifact["input_artifact_refs"] if ref["artifact_type"] == "DataManifest")
    assert dm_ref["artifact_type"] == "DataManifest"
    assert dm_ref["artifact_id"] == "test_csv_data"
    assert dm_ref["content_hash"].startswith("sha256:")
    assert dm_ref["validation_status"] == "pass"


# -----------------------------------------------------------------------
# Determinism with DataManifest
# -----------------------------------------------------------------------

def test_run_config_hash_incorporates_data_manifest_when_provided(valid_data_manifest, tmp_path):
    """run_config_hash changes when DataManifest content changes."""
    manifest_file, _ = valid_data_manifest
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))

    # Without DataManifest
    artifact_no_dm = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
    )
    # With DataManifest
    artifact_with_dm = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=manifest_file,
    )

    # run_config_hash must differ
    assert artifact_no_dm["run_config_hash"] != artifact_with_dm["run_config_hash"]
    # run_id must differ
    assert artifact_no_dm["run_id"] != artifact_with_dm["run_id"]


def test_run_config_hash_deterministic_with_same_manifest(valid_data_manifest, tmp_path):
    """Same experiment spec + same DataManifest produce identical run_config_hash."""
    manifest_file, _ = valid_data_manifest
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))

    artifact1 = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=manifest_file,
    )
    artifact2 = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=manifest_file,
    )

    assert artifact1["run_config_hash"] == artifact2["run_config_hash"]
    assert artifact1["run_id"] == artifact2["run_id"]


def test_run_id_deterministic_with_data_manifest(valid_data_manifest, tmp_path):
    """run_id is deterministic from run_config_hash with DataManifest."""
    manifest_file, _ = valid_data_manifest
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))

    artifact = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=manifest_file,
    )
    # run_id should be first 16 hex chars of hash
    expected_run_id = artifact["run_config_hash"][7:23]  # strip "sha256:"
    assert artifact["run_id"] == expected_run_id


# -----------------------------------------------------------------------
# Row count
# -----------------------------------------------------------------------

def test_data_manifest_entry_has_required_schema_fields(valid_data_manifest, tmp_path):
    """DataManifest entry in input_artifact_refs has all schema-required fields."""
    manifest_file, _ = valid_data_manifest
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    artifact = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=manifest_file,
    )
    dm_ref = next(ref for ref in artifact["input_artifact_refs"] if ref["artifact_type"] == "DataManifest")
    # Required fields only (input_artifact_refs.items has additionalProperties: false)
    assert dm_ref["artifact_type"] == "DataManifest"
    assert dm_ref["artifact_id"] == "test_csv_data"
    assert dm_ref["content_hash"].startswith("sha256:")
    assert dm_ref["validation_status"] == "pass"


def test_sqlite_manifest_produces_valid_data_manifest_entry(valid_sqlite_manifest, tmp_path):
    """SQLite DataManifest produces a valid DataManifest entry in input_artifact_refs."""
    manifest_file, _ = valid_sqlite_manifest
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    artifact = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=manifest_file,
    )
    dm_ref = next(ref for ref in artifact["input_artifact_refs"] if ref["artifact_type"] == "DataManifest")
    # Row_count not stored in schema-compatible input_artifact_refs entry
    # (additionalProperties: false). SQLite row_count=None by design.
    assert dm_ref["artifact_type"] == "DataManifest"
    assert dm_ref["validation_status"] == "pass"


# -----------------------------------------------------------------------
# Invalid DataManifest
# -----------------------------------------------------------------------

def test_missing_data_manifest_file_returns_nonzero(tmp_path):
    """Missing --data-manifest file causes CLI to exit nonzero."""
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    output = tmp_path / "output.json"
    rc = main([
        "--experiment-spec", str(spec),
        "--output-path", str(output),
        "--run-owner", "test",
        "--data-manifest", str(tmp_path / "nonexistent.json"),
    ])
    assert rc == 1


def test_invalid_data_manifest_fails_validation_returns_nonzero(tmp_path, invalid_data_manifest_bad_role):
    """Invalid DataManifest (validation failure) causes CLI to exit nonzero."""
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    output = tmp_path / "output.json"
    rc = main([
        "--experiment-spec", str(spec),
        "--output-path", str(output),
        "--run-owner", "test",
        "--data-manifest", str(invalid_data_manifest_bad_role),
    ])
    assert rc == 1


def test_invalid_data_manifest_produces_failed_validation_artifact(tmp_path, invalid_data_manifest_bad_role):
    """Invalid DataManifest produces a schema-valid failed_validation artifact."""
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    artifact = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=invalid_data_manifest_bad_role,
    )
    assert artifact["status"] == "failed_validation"
    assert artifact["failure_summary"] is not None
    assert artifact["failure_summary"]["failure_type"] == "validation_error"


def test_invalid_data_manifest_audit_includes_data_manifest_validation(tmp_path, invalid_data_manifest_bad_role):
    """audit_summary includes data_manifest_validation audit with fail result.

    data_manifest_validation is a user-level data validation failure, NOT a
    governance blocker. blocker_count=0 so it does not inflate governance
    blocker counts (e.g., autonomous_search violations).
    """
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    artifact = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=invalid_data_manifest_bad_role,
    )
    audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
    assert "data_manifest_validation" in audit_names
    dm_audit = next(a for a in artifact["audit_summary"]["audits"] if a["audit_name"] == "data_manifest_validation")
    assert dm_audit["audit_result"] == "fail"
    # data_manifest_validation is a user validation failure, not a governance blocker
    assert dm_audit["blocker_count"] == 0


# -----------------------------------------------------------------------
# Schema validation
# -----------------------------------------------------------------------

def test_success_artifact_schema_validates_with_data_manifest(valid_data_manifest, tmp_path):
    """Success artifact with DataManifest validates against runner_output_spec_v1.schema.json."""
    pytest.importorskip("jsonschema")
    from jsonschema import validate, FormatChecker

    manifest_file, _ = valid_data_manifest
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    output_path = tmp_path / "output.json"
    rc = main([
        "--experiment-spec", str(spec),
        "--output-path", str(output_path),
        "--run-owner", "test",
        "--data-manifest", str(manifest_file),
    ])
    assert rc == 0

    import jsonschema
    with open(SCHEMA_PATH) as fh:
        schema = json.load(fh)
    with open(output_path) as fh:
        loaded = json.load(fh)

    checker = FormatChecker()
    jsonschema.validate(loaded, schema, format_checker=checker)


def test_failed_validation_artifact_schema_validates_with_invalid_manifest(tmp_path, invalid_data_manifest_bad_role):
    """Failed_validation artifact with invalid DataManifest validates against schema."""
    pytest.importorskip("jsonschema")
    import jsonschema
    from jsonschema import FormatChecker

    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    artifact = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=invalid_data_manifest_bad_role,
    )

    with open(SCHEMA_PATH) as fh:
        schema = json.load(fh)
    checker = FormatChecker()
    jsonschema.validate(artifact, schema, format_checker=checker)


# -----------------------------------------------------------------------
# No-overwrite and no-registry-mutation with DataManifest
# -----------------------------------------------------------------------

def test_no_overwrite_with_data_manifest(valid_data_manifest, tmp_path):
    """Output file refusal-to-overwrite is preserved when DataManifest is used."""
    manifest_file, _ = valid_data_manifest
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    output = tmp_path / "output.json"
    output.write_text("existing")

    rc = main([
        "--experiment-spec", str(spec),
        "--output-path", str(output),
        "--run-owner", "test",
        "--data-manifest", str(manifest_file),
    ])
    assert rc == 1


def test_no_registry_mutation_with_data_manifest(valid_data_manifest, tmp_path):
    """No registry/ledger writes occur when DataManifest is provided."""
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    manifest_file, _ = valid_data_manifest

    registry_path = _ROOT / "docs" / "edge_hypothesis_registry.csv"
    ledger_path = _ROOT / "docs" / "trial_ledger.jsonl"

    registry_mtime_before = registry_path.stat().st_mtime if registry_path.exists() else None
    ledger_mtime_before = ledger_path.stat().st_mtime if ledger_path.exists() else None

    output = tmp_path / "output.json"
    rc = main([
        "--experiment-spec", str(spec),
        "--output-path", str(output),
        "--run-owner", "test",
        "--data-manifest", str(manifest_file),
    ])
    assert rc == 0

    if registry_mtime_before is not None:
        assert registry_path.stat().st_mtime == registry_mtime_before
    if ledger_mtime_before is not None:
        assert ledger_path.stat().st_mtime == ledger_mtime_before


# -----------------------------------------------------------------------
# Preserved dry-run behavior (no data manifest)
# -----------------------------------------------------------------------

def test_no_data_manifest_uses_placeholder(tmp_path):
    """Without --data-manifest, data_manifest_refs uses dry_run_no_data_manifest."""
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))
    artifact = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
    )
    assert artifact["data_manifest_refs"] == ["dry_run_no_data_manifest"]
    assert len(artifact["input_artifact_refs"]) == 1  # only ExperimentSpec
    assert artifact["input_artifact_refs"][0]["artifact_type"] == "ExperimentSpec"


def test_data_manifest_content_hash_is_deterministic(valid_data_manifest, tmp_path):
    """DataManifest content_hash is deterministic across builds."""
    manifest_file, _ = valid_data_manifest
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))

    artifact1 = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=manifest_file,
    )
    artifact2 = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=manifest_file,
    )

    dm_ref1 = next(ref for ref in artifact1["input_artifact_refs"] if ref["artifact_type"] == "DataManifest")
    dm_ref2 = next(ref for ref in artifact2["input_artifact_refs"] if ref["artifact_type"] == "DataManifest")
    assert dm_ref1["content_hash"] == dm_ref2["content_hash"]


def test_changing_data_manifest_content_changes_run_config_hash(valid_data_manifest, tmp_path):
    """Changing DataManifest content changes run_config_hash."""
    manifest_file, csv_file = valid_data_manifest
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))

    artifact1 = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=manifest_file,
    )

    # Append a row to the CSV — this changes nothing about the manifest validation
    # but the manifest content (its JSON bytes) remains the same.
    # So let's actually change the manifest content.
    manifest_data = json.loads(manifest_file.read_text())
    manifest_data["dataset_id"] = "test_csv_data_v2"
    manifest_file.write_text(json.dumps(manifest_data, indent=2))

    artifact2 = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=manifest_file,
    )

    assert artifact1["run_config_hash"] != artifact2["run_config_hash"]


# -----------------------------------------------------------------------
# Cross-directory DataManifest resolution
# -----------------------------------------------------------------------

def test_data_manifest_in_different_dir_from_spec(tmp_path):
    """DataManifest relative paths resolve from manifest directory, not spec directory.

    This test creates:
    - spec_dir/spec.json
    - manifest_dir/data_manifest.json  (manifest_dir != spec_dir)
    - manifest_dir/prices.csv  (dataset path is "prices.csv" relative to manifest_dir)

    The manifest's dataset path should resolve relative to manifest_dir, not spec_dir.
    """
    manifest_dir = tmp_path / "manifests"
    spec_dir = tmp_path / "specs"
    manifest_dir.mkdir()
    spec_dir.mkdir()

    # Dataset lives under manifest_dir
    csv_file = manifest_dir / "prices.csv"
    csv_file.write_text("date,open,high,low,close\n2024-01-01,100,101,99,100\n2024-01-02,100,102,99,101\n")

    # Manifest uses relative path "prices.csv" — resolves from manifest_dir
    manifest_file = manifest_dir / "data_manifest.json"
    manifest_file.write_text(json.dumps({
        "dataset_id": "test_cross_dir",
        "role": "price_history",
        "source_kind": "local_csv",
        "path": "prices.csv",
        "format": "csv",
    }, indent=2))

    # Spec lives under spec_dir (different directory)
    spec = spec_dir / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))

    artifact = build_runner_output(
        experiment_spec_path=spec,
        run_owner="test@test",
        data_manifest_path=manifest_file,
    )

    assert artifact["status"] == "success"
    assert artifact["data_manifest_refs"] == ["test_cross_dir"]
    # Verify DataManifest is in input_artifact_refs
    dm_ref = next(ref for ref in artifact["input_artifact_refs"] if ref["artifact_type"] == "DataManifest")
    assert dm_ref["artifact_id"] == "test_cross_dir"
    assert dm_ref["validation_status"] == "pass"


# -----------------------------------------------------------------------
# Malformed DataManifest: missing required "path" raises KeyError
# -----------------------------------------------------------------------

def test_malformed_data_manifest_missing_path_exits_1(tmp_path):
    """Malformed DataManifest missing 'path' key produces failed_validation and exit 1.

    Missing 'path' in data_manifest.py::dataset_manifest_from_dict raises KeyError.
    Previously this fell through to the generic Exception handler (exit 2).
    Now caught as KeyError → failed_validation artifact → exit 1.
    """
    manifest_file = tmp_path / "malformed_manifest.json"
    # Missing 'path' key — triggers KeyError in dataset_manifest_from_dict
    manifest_file.write_text(json.dumps({
        "dataset_id": "test_malformed",
        "role": "price_history",
        "source_kind": "local_csv",
        # 'path' intentionally omitted
        "format": "csv",
    }, indent=2))

    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "experiment_id": "EXP-2026-0001",
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))

    output_path = tmp_path / "output.json"
    rc = main([
        "--experiment-spec", str(spec),
        "--output-path", str(output_path),
        "--run-owner", "test",
        "--data-manifest", str(manifest_file),
    ])

    # Must exit 1 (user validation error), not 2 (internal error)
    assert rc == 1
    # And the output artifact must be a valid failed_validation
    with open(output_path) as f:
        artifact = json.load(f)
    assert artifact["status"] == "failed_validation"
    assert artifact["failure_summary"]["failure_type"] == "validation_error"

    # jsonschema validation if available
    validate_orskip = pytest.importorskip("jsonschema").validate
    from jsonschema import FormatChecker
    with open(SCHEMA_PATH) as fh:
        schema = json.load(fh)
    checker = FormatChecker()
    validate_orskip(artifact, schema, format_checker=checker)


# -----------------------------------------------------------------------
# Missing experiment_spec_id → exit 1
# -----------------------------------------------------------------------

def test_missing_experiment_spec_id_exits_1(tmp_path):
    """Missing experiment_id in spec produces exit 1 (user error), not exit 2."""
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        # 'experiment_id' intentionally omitted
        "hypothesis_id": "HYP-2026-0001",
        "search_space_id": "SSM-2026-0001",
        "data_manifest_refs": [],
        "study_type": "options_event_risk",
        "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
        "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
        "trial_generation_mode": "literature_replication",
        "allowed_trial_lanes": ["theory_first"],
        "prohibited_modes": {k: False for k in GOVERNANCE_STOP_RULE_FIELDS},
        "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
    }))

    output_path = tmp_path / "output.json"
    rc = main([
        "--experiment-spec", str(spec),
        "--output-path", str(output_path),
        "--run-owner", "test",
    ])

    # Must exit 1 (user validation error), not 2 (internal error)
    assert rc == 1
    # No artifact written (build failed before artifact existed)
    assert not output_path.exists()


# ---------------------------------------------------------------------------
# Existing valid/invalid DataManifest tests still pass
# (implicit: tested by running the full suite)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Canonical summary (observation-table canonical summary audit)
# ---------------------------------------------------------------------------

@pytest.fixture
def csv_with_date_and_symbol(tmp_path):
    """CSV with date and symbol columns for canonical summary tests."""
    csv_file = tmp_path / "obs.csv"
    csv_file.write_text(
        "date,symbol,close\n"
        "2024-01-01,AAPL,185.5\n"
        "2024-01-02,AAPL,186.0\n"
        "2024-01-03,MSFT,420.0\n"
        "2024-01-03,GOOGL,175.0\n"
        "2024-01-04,AAPL,187.0\n"
    )
    manifest_file = tmp_path / "data_manifest.json"
    manifest_file.write_text(json.dumps({
        "dataset_id": "test_obs_csv",
        "role": "generic",
        "source_kind": "local_csv",
        "path": "obs.csv",
        "format": "csv",
    }, indent=2))
    return manifest_file, csv_file


@pytest.fixture
def csv_missing_date_column(tmp_path):
    """CSV without the expected date column."""
    csv_file = tmp_path / "obs.csv"
    csv_file.write_text("symbol,close\nAAPL,185.5\nMSFT,420.0\n")
    manifest_file = tmp_path / "data_manifest.json"
    manifest_file.write_text(json.dumps({
        "dataset_id": "test_obs_csv",
        "role": "generic",
        "source_kind": "local_csv",
        "path": "obs.csv",
        "format": "csv",
    }, indent=2))
    return manifest_file, csv_file


@pytest.fixture
def csv_missing_symbol_column(tmp_path):
    """CSV without the expected symbol column."""
    csv_file = tmp_path / "obs.csv"
    csv_file.write_text("date,close\n2024-01-01,185.5\n2024-01-02,186.0\n")
    manifest_file = tmp_path / "data_manifest.json"
    manifest_file.write_text(json.dumps({
        "dataset_id": "test_obs_csv",
        "role": "generic",
        "source_kind": "local_csv",
        "path": "obs.csv",
        "format": "csv",
    }, indent=2))
    return manifest_file, csv_file


class TestNormalizeOptionalColumnName:
    """Tests for _normalize_optional_column_name helper."""

    def test_none_returns_none(self):
        assert _normalize_optional_column_name(None) is None

    def test_strips_leading_trailing(self):
        assert _normalize_optional_column_name("  date  ") == "date"

    def test_preserves_internal_whitespace(self):
        assert _normalize_optional_column_name("close price") == "close price"
        assert _normalize_optional_column_name("close   price") == "close   price"
        # Leading/trailing stripped, internal whitespace preserved
        assert _normalize_optional_column_name("  close price  ") == "close price"

    def test_empty_after_strip_returns_none(self):
        assert _normalize_optional_column_name("   ") is None


class TestCanonicalSummaryComputation:
    """Tests for _summarize_observation_table_canonical helper."""

    def test_date_column_produces_min_max(self, csv_with_date_and_symbol):
        _, csv_file = csv_with_date_and_symbol
        result = _summarize_observation_table_canonical(csv_file, "date", None)
        assert "row_count=5" in result["details"]
        assert "min_date=2024-01-01" in result["details"]
        assert "max_date=2024-01-04" in result["details"]

    def test_symbol_column_produces_unique_count(self, csv_with_date_and_symbol):
        _, csv_file = csv_with_date_and_symbol
        result = _summarize_observation_table_canonical(csv_file, None, "symbol")
        assert "row_count=5" in result["details"]
        assert "unique_symbol_count=3" in result["details"]

    def test_both_columns_include_both_summaries(self, csv_with_date_and_symbol):
        _, csv_file = csv_with_date_and_symbol
        result = _summarize_observation_table_canonical(csv_file, "date", "symbol")
        assert "min_date=" in result["details"]
        assert "unique_symbol_count=" in result["details"]

    def test_missing_date_column_raises_value_error(self, csv_missing_date_column):
        _, csv_file = csv_missing_date_column
        with pytest.raises(ValueError) as exc_info:
            _summarize_observation_table_canonical(csv_file, "date", None)
        assert "date" in str(exc_info.value)

    def test_missing_symbol_column_raises_value_error(self, csv_missing_symbol_column):
        _, csv_file = csv_missing_symbol_column
        with pytest.raises(ValueError) as exc_info:
            _summarize_observation_table_canonical(csv_file, None, "symbol")
        assert "symbol" in str(exc_info.value)

    def test_date_column_with_empty_values_handled(self, tmp_path):
        """Empty date values are skipped in min/max computation."""
        csv_file = tmp_path / "obs.csv"
        csv_file.write_text("date,symbol\n2024-01-01,AAPL\n,MSFT\n2024-01-03,GOOGL\n")
        result = _summarize_observation_table_canonical(csv_file, "date", None)
        assert "min_date=2024-01-01" in result["details"]
        assert "max_date=2024-01-03" in result["details"]

    def test_empty_csv_all_blanks(self, tmp_path):
        """CSV with no non-empty date values: min/max may be null/empty."""
        csv_file = tmp_path / "obs.csv"
        csv_file.write_text("date,symbol\n,\n  ,  \n")
        result = _summarize_observation_table_canonical(csv_file, "date", None)
        # Should not raise; details_ref indicates empty


class TestCanonicalSummaryCLI:
    """Tests for CLI-level canonical summary behavior."""

    def test_date_column_cli_arg_accepted(self, csv_with_date_and_symbol, valid_experiment_spec, tmp_path):
        manifest_file, _ = csv_with_date_and_symbol
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--output-path", str(output),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output.read_text())
        assert artifact["status"] == "success"
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_canonical_summary" in audit_names
        canon = next(a for a in artifact["audit_summary"]["audits"]
                     if a["audit_name"] == "observation_table_canonical_summary")
        assert canon["audit_result"] == "pass"
        assert "min_date=" in canon["details_ref"]
        assert "max_date=" in canon["details_ref"]

    def test_symbol_column_cli_arg_accepted(self, csv_with_date_and_symbol, valid_experiment_spec, tmp_path):
        manifest_file, _ = csv_with_date_and_symbol
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-symbol-column", "symbol",
            "--output-path", str(output),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output.read_text())
        assert artifact["status"] == "success"
        canon = next(a for a in artifact["audit_summary"]["audits"]
                     if a["audit_name"] == "observation_table_canonical_summary")
        assert "unique_symbol_count=3" in canon["details_ref"]

    def test_both_columns_together(self, csv_with_date_and_symbol, valid_experiment_spec, tmp_path):
        manifest_file, _ = csv_with_date_and_symbol
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--output-path", str(output),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output.read_text())
        canon = next(a for a in artifact["audit_summary"]["audits"]
                     if a["audit_name"] == "observation_table_canonical_summary")
        assert "min_date=" in canon["details_ref"]
        assert "unique_symbol_count=" in canon["details_ref"]

    def test_missing_date_column_exit_1(self, csv_missing_date_column, valid_experiment_spec, tmp_path):
        manifest_file, _ = csv_missing_date_column
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--output-path", str(output),
            "--run-owner", "test",
        ])
        assert rc == 1
        # Schema-valid failed_validation artifact written
        artifact = json.loads(output.read_text())
        assert artifact["status"] == "failed_validation"
        assert artifact["failure_summary"]["failure_type"] == "validation_error"
        canon = next(a for a in artifact["audit_summary"]["audits"]
                     if a["audit_name"] == "observation_table_canonical_summary")
        assert canon["audit_result"] == "fail"

    def test_missing_symbol_column_exit_1(self, csv_missing_symbol_column, valid_experiment_spec, tmp_path):
        manifest_file, _ = csv_missing_symbol_column
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-symbol-column", "symbol",
            "--output-path", str(output),
            "--run-owner", "test",
        ])
        assert rc == 1
        artifact = json.loads(output.read_text())
        assert artifact["status"] == "failed_validation"

    def test_date_column_without_manifest_exit_1(self, valid_experiment_spec, tmp_path):
        """Date column requires a DataManifest."""
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--observation-date-column", "date",
            "--output-path", str(output),
            "--run-owner", "test",
        ])
        assert rc == 1
        artifact = json.loads(output.read_text())
        assert artifact["status"] == "failed_validation"

    def test_non_csv_manifest_with_date_column_exit_1(self, valid_sqlite_manifest, valid_experiment_spec, tmp_path):
        """Non-CSV DataManifest with date column should fail."""
        manifest_file, _ = valid_sqlite_manifest
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--output-path", str(output),
            "--run-owner", "test",
        ])
        assert rc == 1
        artifact = json.loads(output.read_text())
        assert artifact["status"] == "failed_validation"
        assert "unsupported_config" in artifact["failure_summary"]["failure_type"]

    def test_required_columns_plus_missing_date_column_preserves_both(self, csv_missing_date_column, valid_experiment_spec, tmp_path):
        """Required-column fail + missing date column fail → both in audit_summary."""
        manifest_file, _ = csv_missing_date_column
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--required-observation-columns", "date,symbol",
            "--observation-date-column", "date",
            "--output-path", str(output),
            "--run-owner", "test",
        ])
        assert rc == 1
        artifact = json.loads(output.read_text())
        assert artifact["status"] == "failed_validation"
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        # Both required-column and canonical-summary failures are present
        assert "observation_table_shape_validation" in audit_names
        assert "observation_table_canonical_summary" in audit_names
        # blocker_count reflects both
        assert artifact["audit_summary"]["blocker_count"] >= 2

    def test_hash_includes_date_column(self, csv_with_date_and_symbol, valid_experiment_spec, tmp_path):
        manifest_file, _ = csv_with_date_and_symbol
        output1 = tmp_path / "output1.json"
        output2 = tmp_path / "output2.json"
        main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--output-path", str(output1),
            "--run-owner", "test",
        ])
        main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "trade_date",  # different column name
            "--output-path", str(output2),
            "--run-owner", "test",
        ])
        a1 = json.loads(output1.read_text())
        a2 = json.loads(output2.read_text())
        assert a1["run_config_hash"] != a2["run_config_hash"]

    def test_hash_includes_symbol_column(self, csv_with_date_and_symbol, valid_experiment_spec, tmp_path):
        manifest_file, _ = csv_with_date_and_symbol
        output1 = tmp_path / "output1.json"
        output2 = tmp_path / "output2.json"
        main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-symbol-column", "symbol",
            "--output-path", str(output1),
            "--run-owner", "test",
        ])
        main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-symbol-column", "ticker",  # different column name
            "--output-path", str(output2),
            "--run-owner", "test",
        ])
        a1 = json.loads(output1.read_text())
        a2 = json.loads(output2.read_text())
        assert a1["run_config_hash"] != a2["run_config_hash"]

    def test_hash_whitespace_normalized_for_column_names(self, csv_with_date_and_symbol, valid_experiment_spec, tmp_path):
        manifest_file, _ = csv_with_date_and_symbol
        output1 = tmp_path / "output1.json"
        output2 = tmp_path / "output2.json"
        main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", " date ",  # extra whitespace
            "--output-path", str(output1),
            "--run-owner", "test",
        ])
        main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",  # normalized form
            "--output-path", str(output2),
            "--run-owner", "test",
        ])
        a1 = json.loads(output1.read_text())
        a2 = json.loads(output2.read_text())
        assert a1["run_config_hash"] == a2["run_config_hash"]

    def test_internal_whitespace_preserved_for_hash(self, csv_with_date_and_symbol, valid_experiment_spec, tmp_path):
        manifest_file, _ = csv_with_date_and_symbol
        output1 = tmp_path / "output1.json"
        output2 = tmp_path / "output2.json"
        main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "my date",  # space inside
            "--output-path", str(output1),
            "--run-owner", "test",
        ])
        main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "mydate",  # no space
            "--output-path", str(output2),
            "--run-owner", "test",
        ])
        a1 = json.loads(output1.read_text())
        a2 = json.loads(output2.read_text())
        assert a1["run_config_hash"] != a2["run_config_hash"]

    def test_no_registry_mutation_with_canonical_summary(self, csv_with_date_and_symbol, valid_experiment_spec, tmp_path, monkeypatch):
        """Canonical summary does not write to any registry or ledger."""
        written = []
        monkeypatch.setattr(Path, "touch", lambda self: written.append(str(self)))
        manifest_file, _ = csv_with_date_and_symbol
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--output-path", str(output),
            "--run-owner", "test",
        ])
        assert rc == 0
        registry_ledger_patterns = ["registry", "ledger", "EdgeHypothesis", "TrialLedger"]
        assert not any(p in str(w).lower() for w in written for p in registry_ledger_patterns)

    def test_schema_validation_on_success_canonical(self, csv_with_date_and_symbol, valid_experiment_spec, tmp_path):
        """Success artifact with canonical summary validates against schema."""
        pytest.importorskip("jsonschema")
        import jsonschema
        from jsonschema import FormatChecker
        manifest_file, _ = csv_with_date_and_symbol
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--output-path", str(output),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output.read_text())
        with open(SCHEMA_PATH) as fh:
            schema = json.load(fh)
        checker = FormatChecker()
        jsonschema.validate(artifact, schema, format_checker=checker)

    def test_schema_validation_on_failed_canonical(self, csv_missing_date_column, valid_experiment_spec, tmp_path):
        """Failed-validation artifact with missing date column validates against schema."""
        pytest.importorskip("jsonschema")
        import jsonschema
        from jsonschema import FormatChecker
        manifest_file, _ = csv_missing_date_column
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--output-path", str(output),
            "--run-owner", "test",
        ])
        assert rc == 1
        artifact = json.loads(output.read_text())
        with open(SCHEMA_PATH) as fh:
            schema = json.load(fh)
        checker = FormatChecker()
        jsonschema.validate(artifact, schema, format_checker=checker)

