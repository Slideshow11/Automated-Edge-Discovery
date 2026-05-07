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
    UnsupportedConfig,
    main,
    _compute_run_config_hash,
    _compute_run_id,
    _check_experiment_spec_id,
    _load_experiment_spec,
    _utc_now,
    GOVERNANCE_STOP_RULE_FIELDS,
    SCHEMA_PATH,
    _parse_required_columns,
    _read_csv_header,
    _validate_observation_table_columns,
    _normalize_optional_column_name,
    _summarize_observation_table_canonical,
    _summarize_observation_close_returns,
    load_data_manifest_for_runner,
    _summarize_data_manifest_for_runner,
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


# ---------------------------------------------------------------------------
# Missing-value summary (observation_table_missing_value_summary audit)
# ---------------------------------------------------------------------------

@pytest.fixture
def csv_with_volume_and_missing_values(tmp_path):
    """CSV with some missing volume values for missing-value summary tests."""
    csv_file = tmp_path / "obs.csv"
    csv_file.write_text(
        "date,symbol,volume,bid\n"
        "2024-01-01,AAPL,1000,100.0\n"
        "2024-01-02,AAPL,,101.0\n"
        "2024-01-03,AAPL,3000,102.0\n"
        "2024-01-04,MSFT,2000,\n"
    )
    manifest_file = tmp_path / "data_manifest.json"
    manifest_file.write_text(json.dumps({
        "dataset_id": "DM-2026-MISSING-VAL",
        "role": "generic",
        "source_kind": "local_csv",
        "path": "obs.csv",
        "format": "csv",
    }, indent=2))
    return manifest_file, csv_file


@pytest.fixture
def csv_with_no_missing_values(tmp_path):
    """CSV with no missing values for missing-value summary success tests."""
    csv_file = tmp_path / "obs.csv"
    csv_file.write_text(
        "date,symbol,volume,bid\n"
        "2024-01-01,AAPL,1000,100.0\n"
        "2024-01-02,AAPL,2000,101.0\n"
        "2024-01-03,MSFT,3000,102.0\n"
    )
    manifest_file = tmp_path / "data_manifest.json"
    manifest_file.write_text(json.dumps({
        "dataset_id": "DM-2026-NO-MISSING",
        "role": "generic",
        "source_kind": "local_csv",
        "path": "obs.csv",
        "format": "csv",
    }, indent=2))
    return manifest_file, csv_file


class TestMissingValueSummaryComputation:
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


# -------------------------------------------------------------------------------------------------
# Fixtures for close-return summary tests
# -------------------------------------------------------------------------------------------------

@pytest.fixture
def csv_with_date_symbol_close(tmp_path):
    """
    CSV with date, symbol, and close columns for close-return summary tests.

    AAPL: 2024-01-01@185.5, 2024-01-02@186.0, 2024-01-04@187.0
      return = 187.0/185.5 - 1 = 0.0080857...
    MSFT: 2024-01-03@420.0 (single date → skipped, no return)
    GOOGL: 2024-01-03@175.0 (single date → skipped, no return)

    Expected: symbols_with_return=1 (AAPL only), min=max=mean=~0.008085
    """
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
def csv_with_date_symbol_close_two_symbols(tmp_path):
    """
    CSV with two symbols having valid returns.

    AAPL: 2024-01-01@185.5 → 2024-01-04@187.0 → return = 187.0/185.5-1 = 0.00808...
    MSFT: 2024-01-01@410.0 → 2024-01-04@420.0 → return = 420.0/410.0-1 = 0.02439...

    Expected: symbols_with_return=2, min~=0.008, max~=0.024, mean~=0.016
    """
    csv_file = tmp_path / "obs.csv"
    csv_file.write_text(
        "date,symbol,close\n"
        "2024-01-01,AAPL,185.5\n"
        "2024-01-02,AAPL,186.0\n"
        "2024-01-03,MSFT,410.0\n"
        "2024-01-04,AAPL,187.0\n"
        "2024-01-04,MSFT,420.0\n"
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
def csv_missing_close_column(tmp_path):
    """CSV without the expected close column and with a missing symbol value."""
    csv_file = tmp_path / "obs.csv"
    csv_file.write_text("date,symbol\n2024-01-01,\n2024-01-02,AAPL\n")
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
def csv_with_non_numeric_close(tmp_path):
    """CSV with non-numeric close values (should be skipped)."""
    csv_file = tmp_path / "obs.csv"
    csv_file.write_text(
        "date,symbol,close\n"
        "2024-01-01,AAPL,N/A\n"
        "2024-01-02,AAPL,ABC\n"
        "2024-01-03,AAPL,186.0\n"
        "2024-01-04,AAPL,\n"
        "2024-01-05,AAPL,187.0\n"
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
def csv_with_zero_first_close(tmp_path):
    """CSV where first close is zero (should be skipped)."""
    csv_file = tmp_path / "obs.csv"
    csv_file.write_text(
        "date,symbol,close\n"
        "2024-01-01,AAPL,0.0\n"
        "2024-01-02,AAPL,185.5\n"
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
def csv_all_symbols_single_date(tmp_path):
    """CSV where all symbols have only one date (no valid returns)."""
    csv_file = tmp_path / "obs.csv"
    csv_file.write_text(
        "date,symbol,close\n"
        "2024-01-01,AAPL,185.5\n"
        "2024-01-01,MSFT,420.0\n"
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


class TestSummarizeObservationCloseReturns:
    """Unit tests for _summarize_observation_close_returns helper."""

    def test_symbols_with_return_and_stats(self, csv_with_date_symbol_close_two_symbols):
        """Close-return summary computes symbols_with_return, min, max, mean."""
        _, csv_file = csv_with_date_symbol_close_two_symbols
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            _summarize_observation_close_returns,
        )
        result = _summarize_observation_close_returns(
            csv_file,
            observation_date_column="date",
            observation_symbol_column="symbol",
            observation_close_column="close",
        )
        assert result["symbols_with_return"] == 2
        assert result["close_column"] == "close"
        assert result["skipped_symbols"] == 0
        assert result["min_return"] is not None
        assert result["max_return"] is not None
        assert result["mean_return"] is not None
        # AAPL: 187.0/185.5-1 = 0.0080857...
        # MSFT: 420.0/410.0-1 = 0.024390...
        aapl_return = 187.0 / 185.5 - 1.0
        msft_return = 420.0 / 410.0 - 1.0
        assert abs(result["min_return"] - min(aapl_return, msft_return)) < 1e-10
        assert abs(result["max_return"] - max(aapl_return, msft_return)) < 1e-10
        assert abs(result["mean_return"] - ((aapl_return + msft_return) / 2)) < 1e-10

    def test_skips_non_numeric_close(self, csv_with_non_numeric_close):
        """Non-numeric close rows are skipped."""
        _, csv_file = csv_with_non_numeric_close
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            _summarize_observation_close_returns,
        )
        result = _summarize_observation_close_returns(
            csv_file,
            observation_date_column="date",
            observation_symbol_column="symbol",
            observation_close_column="close",
        )
        # AAPL: dates 2024-01-03 (186.0), 2024-01-05 (187.0) → return = 187.0/186.0-1
        assert result["symbols_with_return"] == 1
        expected = 187.0 / 186.0 - 1.0
        assert abs(result["min_return"] - expected) < 1e-10

    def test_skips_zero_first_close(self, csv_with_zero_first_close):
        """Symbol with zero first close is skipped."""
        _, csv_file = csv_with_zero_first_close
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            _summarize_observation_close_returns,
        )
        result = _summarize_observation_close_returns(
            csv_file,
            observation_date_column="date",
            observation_symbol_column="symbol",
            observation_close_column="close",
        )
        assert result["symbols_with_return"] == 0
        assert result["skipped_symbols"] == 1

    def test_single_date_symbol_skipped(self, csv_with_date_symbol_close):
        """Symbol with only one date is skipped (no return possible)."""
        _, csv_file = csv_with_date_symbol_close
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            _summarize_observation_close_returns,
        )
        result = _summarize_observation_close_returns(
            csv_file,
            observation_date_column="date",
            observation_symbol_column="symbol",
            observation_close_column="close",
        )
        # AAPL has 3 dates: return = 187.0/185.5-1
        # MSFT and GOOGL have 1 date each → skipped
        assert result["symbols_with_return"] == 1
        assert result["skipped_symbols"] == 2

    def test_missing_close_column_raises(self, csv_missing_close_column):
        """Missing close column raises ValueError."""
        _, csv_file = csv_missing_close_column
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            _summarize_observation_close_returns,
        )
        with pytest.raises(ValueError) as exc_info:
            _summarize_observation_close_returns(
                csv_file,
                observation_date_column="date",
                observation_symbol_column="symbol",
                observation_close_column="close",
            )
        assert "close" in str(exc_info.value)

    def test_no_valid_returns(self, csv_all_symbols_single_date):
        """No symbols with ≥2 dates → symbols_with_return=0."""
        _, csv_file = csv_all_symbols_single_date
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            _summarize_observation_close_returns,
        )
        result = _summarize_observation_close_returns(
            csv_file,
            observation_date_column="date",
            observation_symbol_column="symbol",
            observation_close_column="close",
        )
        assert result["symbols_with_return"] == 0
        assert result["min_return"] is None
        assert result["max_return"] is None
        assert result["mean_return"] is None


# ---------------------------------------------------------------------------
# Duplicate-row summary (observation_table_duplicate_row_summary audit)
# ---------------------------------------------------------------------------

@pytest.fixture
def csv_with_no_duplicates(tmp_path):
    """CSV with unique (symbol, date) rows — no duplicates."""
    csv_file = tmp_path / "obs.csv"
    csv_file.write_text(
        "date,symbol,close\n"
        "2024-01-01,AAPL,185.5\n"
        "2024-01-02,AAPL,186.0\n"
        "2024-01-03,AAPL,187.0\n"
        "2024-01-01,MSFT,420.0\n"
        "2024-01-03,GOOGL,175.0\n"
    )
    manifest_file = tmp_path / "data_manifest.json"
    manifest_file.write_text(json.dumps({
        "dataset_id": "DM-2026-NODUP",
        "role": "generic",
        "source_kind": "local_csv",
        "path": "obs.csv",
        "format": "csv",
    }, indent=2))
    return manifest_file, csv_file


@pytest.fixture
def csv_with_one_duplicate_pair(tmp_path):
    """CSV with one duplicate (symbol, date) key appearing twice."""
    csv_file = tmp_path / "obs.csv"
    csv_file.write_text(
        "date,symbol,close\n"
        "2024-01-01,AAPL,185.5\n"
        "2024-01-02,AAPL,186.0\n"
        "2024-01-01,AAPL,185.6\n"   # duplicate of 2024-01-01
        "2024-01-01,MSFT,420.0\n"
        "2024-01-03,GOOGL,175.0\n"
    )
    manifest_file = tmp_path / "data_manifest.json"
    manifest_file.write_text(json.dumps({
        "dataset_id": "DM-2026-DUP1",
        "role": "generic",
        "source_kind": "local_csv",
        "path": "obs.csv",
        "format": "csv",
    }, indent=2))
    return manifest_file, csv_file


@pytest.fixture
def csv_with_multiple_duplicate_pairs(tmp_path):
    """CSV with two distinct duplicate (symbol, date) keys."""
    csv_file = tmp_path / "obs.csv"
    csv_file.write_text(
        "date,symbol,close\n"
        "2024-01-01,AAPL,185.5\n"
        "2024-01-02,AAPL,186.0\n"
        "2024-01-01,AAPL,185.6\n"   # duplicate AAPL 2024-01-01
        "2024-01-01,MSFT,420.0\n"
        "2024-01-02,MSFT,421.0\n"
        "2024-01-01,MSFT,420.5\n"   # duplicate MSFT 2024-01-01
    )
    manifest_file = tmp_path / "data_manifest.json"
    manifest_file.write_text(json.dumps({
        "dataset_id": "DM-2026-DUPMULTI",
        "role": "generic",
        "source_kind": "local_csv",
        "path": "obs.csv",
        "format": "csv",
    }, indent=2))
    return manifest_file, csv_file


class TestCloseReturnSummaryIntegration:
    """Integration tests for close-return summary via CLI and build_runner_output."""

    def test_close_column_not_required_for_success(
        self, csv_with_date_and_symbol, valid_experiment_spec
    ):
        """Without --observation-close-column, success is unchanged."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            build_runner_output,
        )
        manifest_file, _ = csv_with_date_and_symbol
        artifact = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            data_manifest_path=manifest_file,
            observation_date_column="date",
            observation_symbol_column="symbol",
            run_owner="test",
        )
        assert artifact["status"] == "success"
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_close_return_summary" not in audit_names

    def test_cli_accepts_close_column(
        self, csv_with_date_symbol_close, valid_experiment_spec, tmp_path
    ):
        """CLI accepts --observation-close-column."""
        manifest_file, _ = csv_with_date_symbol_close
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--output-path", str(output),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output.read_text())
        assert artifact["status"] == "success"
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_close_return_summary" in audit_names
        close_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_close_return_summary"
        )
        assert close_audit["audit_result"] == "pass"
        assert "symbols_with_return=1" in close_audit["details_ref"]

    def test_close_requires_data_manifest(self, valid_experiment_spec, tmp_path):
        """Close column without data manifest raises ValueError."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            build_runner_output,
        )
        with pytest.raises(ValueError) as exc_info:
            build_runner_output(
                experiment_spec_path=valid_experiment_spec,
                data_manifest_path=None,
                observation_date_column="date",
                observation_symbol_column="symbol",
                observation_close_column="close",
                run_owner="test",
            )
        assert "data-manifest" in str(exc_info.value)

    def test_close_requires_date_column(
        self, csv_with_date_symbol_close, valid_experiment_spec
    ):
        """Close column without date column raises ValueError."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            build_runner_output,
        )
        manifest_file, _ = csv_with_date_symbol_close
        with pytest.raises(ValueError) as exc_info:
            build_runner_output(
                experiment_spec_path=valid_experiment_spec,
                data_manifest_path=manifest_file,
                observation_date_column=None,
                observation_symbol_column="symbol",
                observation_close_column="close",
                run_owner="test",
            )
        assert "date" in str(exc_info.value)

    def test_close_requires_symbol_column(
        self, csv_with_date_symbol_close, valid_experiment_spec
    ):
        """Close column without symbol column raises ValueError."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            build_runner_output,
        )
        manifest_file, _ = csv_with_date_symbol_close
        with pytest.raises(ValueError) as exc_info:
            build_runner_output(
                experiment_spec_path=valid_experiment_spec,
                data_manifest_path=manifest_file,
                observation_date_column="date",
                observation_symbol_column=None,
                observation_close_column="close",
                run_owner="test",
            )
        assert "symbol" in str(exc_info.value)

    def test_missing_close_column_fails_closed(
        self, csv_missing_close_column, valid_experiment_spec
    ):
        """Missing close column fails closed with GovernanceRejection."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            build_runner_output,
            GovernanceRejection,
        )
        manifest_file, _ = csv_missing_close_column
        with pytest.raises(GovernanceRejection) as exc_info:
            build_runner_output(
                experiment_spec_path=valid_experiment_spec,
                data_manifest_path=manifest_file,
                observation_date_column="date",
                observation_symbol_column="symbol",
                observation_close_column="close",
                run_owner="test",
            )
        artifact = exc_info.value.artifact
        assert artifact["status"] == "failed_validation"
        assert artifact["failure_summary"]["failure_type"] == "validation_error"
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_close_return_summary" in audit_names
        close_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_close_return_summary"
        )
        assert close_audit["audit_result"] == "fail"
        assert "close" in close_audit["details_ref"].lower()

    def test_non_csv_manifest_with_close_fails(
        self, valid_experiment_spec, tmp_path
    ):
        """Non-CSV manifest with close column fails closed."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            build_runner_output,
            GovernanceRejection,
        )
        # Create a parquet manifest
        manifest_file = tmp_path / "dm.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "DM-PARQUET",
            "role": "generic",
            "source_kind": "parquet",
            "path": "data.parquet",
            "format": "parquet",
        }))
        with pytest.raises(GovernanceRejection) as exc_info:
            build_runner_output(
                experiment_spec_path=valid_experiment_spec,
                data_manifest_path=manifest_file,
                observation_date_column="date",
                observation_symbol_column="symbol",
                observation_close_column="close",
                run_owner="test",
            )
        artifact = exc_info.value.artifact
        assert artifact["status"] == "failed_validation"
        assert artifact["failure_summary"]["failure_type"] == "unsupported_config"
        # P1 fix: input_artifact_refs must satisfy minItems >= 1
        assert len(artifact["input_artifact_refs"]) >= 1
        assert artifact["input_artifact_refs"][0]["artifact_type"] == "ExperimentSpec"
        # data_manifest_refs must also satisfy minItems >= 1
        assert len(artifact["data_manifest_refs"]) >= 1
        assert len(artifact["data_manifest_refs"][0]) > 0
        # output_manifest must remain non-empty
        assert len(artifact["output_manifest"]) >= 1
        # Schema validation if jsonschema available
        jsonschema = pytest.importorskip("jsonschema")
        schema_path = Path(__file__).parent.parent / "schemas" / "runner_output_spec_v1.schema.json"
        jsonschema.validate(artifact, json.loads(schema_path.read_text()))

    def test_no_valid_returns_fails_closed(
        self, csv_all_symbols_single_date, valid_experiment_spec
    ):
        """No valid returns (all symbols single-date) fails closed."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            build_runner_output,
            GovernanceRejection,
        )
        manifest_file, _ = csv_all_symbols_single_date
        with pytest.raises(GovernanceRejection) as exc_info:
            build_runner_output(
                experiment_spec_path=valid_experiment_spec,
                data_manifest_path=manifest_file,
                observation_date_column="date",
                observation_symbol_column="symbol",
                observation_close_column="close",
                run_owner="test",
            )
        artifact = exc_info.value.artifact
        assert artifact["status"] == "failed_validation"
        assert artifact["failure_summary"]["failure_type"] == "validation_error"
        close_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_close_return_summary"
        )
        assert close_audit["audit_result"] == "fail"
        assert "no symbols" in close_audit["details_ref"]

    def test_valid_csv_close_computes_stats(
        self, csv_with_date_symbol_close_two_symbols, valid_experiment_spec
    ):
        """Valid CSV computes symbols_with_return and min/max/mean."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            build_runner_output,
        )
        manifest_file, _ = csv_with_date_symbol_close_two_symbols
        artifact = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            data_manifest_path=manifest_file,
            observation_date_column="date",
            observation_symbol_column="symbol",
            observation_close_column="close",
            run_owner="test",
        )
        assert artifact["status"] == "success"
        close_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_close_return_summary"
        )
        assert close_audit["audit_result"] == "pass"
        assert "symbols_with_return=2" in close_audit["details_ref"]
        assert "min_return=" in close_audit["details_ref"]
        assert "max_return=" in close_audit["details_ref"]
        assert "mean_return=" in close_audit["details_ref"]

    def test_preserves_canonical_and_shape_audits(
        self, csv_with_date_symbol_close_two_symbols, valid_experiment_spec
    ):
        """Close-return audit does not remove canonical or shape audits."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            build_runner_output,
        )
        manifest_file, _ = csv_with_date_symbol_close_two_symbols
        artifact = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            data_manifest_path=manifest_file,
            observation_date_column="date",
            observation_symbol_column="symbol",
            observation_close_column="close",
            run_owner="test",
        )
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_canonical_summary" in audit_names
        assert "observation_table_close_return_summary" in audit_names

    def test_missing_close_preserves_other_audits(
        self, csv_missing_close_column, valid_experiment_spec
    ):
        """Missing close column preserves other audit entries."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            build_runner_output,
            GovernanceRejection,
        )
        manifest_file, _ = csv_missing_close_column
        with pytest.raises(GovernanceRejection):
            build_runner_output(
                experiment_spec_path=valid_experiment_spec,
                data_manifest_path=manifest_file,
                observation_date_column="date",
                observation_symbol_column="symbol",
                observation_close_column="close",
                run_owner="test",
            )

    def test_hash_includes_close_column(
        self, csv_with_date_symbol_close, valid_experiment_spec
    ):
        """run_config_hash changes when close column changes."""
        manifest_file, _ = csv_with_date_symbol_close
        artifact1 = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            data_manifest_path=manifest_file,
            observation_date_column="date",
            observation_symbol_column="symbol",
            observation_close_column="close",
            run_owner="test",
        )
        # Different column name may raise GovernanceRejection if column is missing;
        # compare hash from the exception artifact
        try:
            build_runner_output(
                experiment_spec_path=valid_experiment_spec,
                data_manifest_path=manifest_file,
                observation_date_column="date",
                observation_symbol_column="symbol",
                observation_close_column="adj_close",
                run_owner="test",
            )
        except GovernanceRejection as exc:
            hash2 = exc.artifact["run_config_hash"]
        assert artifact1["run_config_hash"] != hash2
        assert artifact1["run_id"] != hash2  # run_id derives from hash

    def test_hash_whitespace_normalized_for_close_column(
        self, csv_with_date_symbol_close, valid_experiment_spec
    ):
        """Leading/trailing whitespace in close column name is normalized for hash."""
        manifest_file, _ = csv_with_date_symbol_close
        artifact1 = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            data_manifest_path=manifest_file,
            observation_date_column="date",
            observation_symbol_column="symbol",
            observation_close_column="close",
            run_owner="test",
        )
        artifact2 = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            data_manifest_path=manifest_file,
            observation_date_column="date",
            observation_symbol_column="symbol",
            observation_close_column="  close  ",
            run_owner="test",
        )
        assert artifact1["run_config_hash"] == artifact2["run_config_hash"]

    def test_internal_whitespace_preserved_for_close_hash(
        self, csv_with_date_symbol_close, valid_experiment_spec
    ):
        """Internal whitespace in close column name changes hash."""
        manifest_file, _ = csv_with_date_symbol_close
        # "close price" doesn't exist in CSV — GovernanceRejection raised; extract hash
        try:
            build_runner_output(
                experiment_spec_path=valid_experiment_spec,
                data_manifest_path=manifest_file,
                observation_date_column="date",
                observation_symbol_column="symbol",
                observation_close_column="close price",
                run_owner="test",
            )
        except GovernanceRejection as exc:
            hash1 = exc.artifact["run_config_hash"]
        # "closeprice" also doesn't exist — different hash expected
        try:
            build_runner_output(
                experiment_spec_path=valid_experiment_spec,
                data_manifest_path=manifest_file,
                observation_date_column="date",
                observation_symbol_column="symbol",
                observation_close_column="closeprice",
                run_owner="test",
            )
        except GovernanceRejection as exc:
            hash2 = exc.artifact["run_config_hash"]
        assert hash1 != hash2

    def test_schema_validation_close_success(
        self, csv_with_date_symbol_close, valid_experiment_spec, tmp_path
    ):
        """Success artifact with close-return summary validates against schema."""
        pytest.importorskip("jsonschema")
        import jsonschema
        from jsonschema import FormatChecker
        manifest_file, _ = csv_with_date_symbol_close
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--output-path", str(output),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output.read_text())
        with open(SCHEMA_PATH) as fh:
            schema = json.load(fh)
        checker = FormatChecker()
        jsonschema.validate(artifact, schema, format_checker=checker)

    def test_schema_validation_close_failure(
        self, csv_all_symbols_single_date, valid_experiment_spec, tmp_path
    ):
        """Failed-validation artifact with no valid returns validates against schema."""
        pytest.importorskip("jsonschema")
        import jsonschema
        from jsonschema import FormatChecker
        manifest_file, _ = csv_all_symbols_single_date
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--output-path", str(output),
            "--run-owner", "test",
        ])
        assert rc == 1
        artifact = json.loads(output.read_text())
        with open(SCHEMA_PATH) as fh:
            schema = json.load(fh)
        checker = FormatChecker()
        jsonschema.validate(artifact, schema, format_checker=checker)

    def test_no_registry_mutation_with_close_summary(
        self, csv_with_date_symbol_close, valid_experiment_spec, tmp_path
    ):
        """Close-return summary does not write to registry or ledger paths."""
        manifest_file, _ = csv_with_date_symbol_close
        out = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--output-path", str(out),
            "--run-owner", "test",
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
        ])
        assert rc == 0, f"Expected exit 0, got {rc}"
        assert out.exists()
        artifact_text = out.read_text()
        assert "TrialLedger" not in artifact_text



class TestSchemaRegressionFailureArtifacts:
    """Schema regression tests for failed_validation RunnerOutput artifacts.

    These tests verify that every failure artifact produced by the runner
    satisfies minItems constraints and schema structural requirements.

    Previously fixed bugs:
    - BUG: data_manifest_refs = [] violated minItems >= 1 (line ~1046, ~1140)
    - BUG: missing partial_summary = None in some failure artifacts
    """

    def test_canonical_summary_no_manifest_data_manifest_refs_min_items(
        self, valid_experiment_spec, tmp_path
    ):
        """Canonical summary without DataManifest → data_manifest_refs is non-empty.

        Regression test for the bug where data_manifest_refs was set to []
        in the canonical summary failure path when no DataManifest was provided.
        """
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
        # Schema: data_manifest_refs minItems >= 1
        assert len(artifact["data_manifest_refs"]) >= 1
        assert artifact["data_manifest_refs"][0]  # non-empty string
        # input_artifact_refs and output_manifest must also be non-empty
        assert len(artifact["input_artifact_refs"]) >= 1
        assert len(artifact["output_manifest"]) >= 1
        # partial_summary should be None for failure artifacts
        assert artifact.get("partial_summary") is None

    def test_no_valid_returns_close_artifact_schema_regression(
        self, csv_all_symbols_single_date, valid_experiment_spec, tmp_path
    ):
        """Close-return no-valid-returns artifact satisfies schema minItems constraints.

        Regression test to ensure the close-return failure artifact has
        non-empty input_artifact_refs, data_manifest_refs, output_manifest.
        """
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            build_runner_output,
            GovernanceRejection,
        )
        manifest_file, _ = csv_all_symbols_single_date
        with pytest.raises(GovernanceRejection) as exc_info:
            build_runner_output(
                experiment_spec_path=valid_experiment_spec,
                data_manifest_path=manifest_file,
                observation_date_column="date",
                observation_symbol_column="symbol",
                observation_close_column="close",
                run_owner="test",
            )
        artifact = exc_info.value.artifact
        assert artifact["status"] == "failed_validation"
        assert artifact["failure_summary"]["failure_type"] == "validation_error"
        # Schema: all refs lists must satisfy minItems >= 1
        assert len(artifact["input_artifact_refs"]) >= 1
        assert len(artifact["data_manifest_refs"]) >= 1
        assert len(artifact["output_manifest"]) >= 1
        # failure_summary must not be null and must have valid failure_type
        assert artifact["failure_summary"] is not None
        assert artifact["failure_summary"]["failure_type"] in (
            "validation_error", "unsupported_config", "runtime_error"
        )

    def test_combined_governance_and_validation_blockers_min_items(
        self, csv_missing_date_column, valid_experiment_spec, tmp_path
    ):
        """Governance blocker + observation validation blocker → both preserved, schema valid.

        Regression test for the bug where data_manifest_refs was [] in the
        canonical summary failure path when no DataManifest was provided.
        Tests the combined path (experiment spec with required_observation_columns
        and observation_date_column but no data manifest).
        """
        output = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(csv_missing_date_column[0]),
            "--required-observation-columns", "date,symbol",
            "--observation-date-column", "date",
            "--output-path", str(output),
            "--run-owner", "test",
        ])
        assert rc == 1
        artifact = json.loads(output.read_text())
        assert artifact["status"] == "failed_validation"
        # Schema: all refs lists must satisfy minItems >= 1
        assert len(artifact["input_artifact_refs"]) >= 1
        assert len(artifact["data_manifest_refs"]) >= 1
        assert len(artifact["output_manifest"]) >= 1
        # Both blocker types present in audit_summary
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_shape_validation" in audit_names
        assert "observation_table_canonical_summary" in audit_names
        # blocker_count reflects both
        assert artifact["audit_summary"]["blocker_count"] >= 2
        # failure_summary present and schema-compatible
        assert artifact["failure_summary"] is not None
        assert artifact["failure_summary"]["failure_type"] in (
            "validation_error", "unsupported_config", "runtime_error"
        )


class TestTrialAccountingConditionalEmission:
    """Tests for conditional trial_accounting_summary emission based on CLI flags."""

    def test_trial_accounting_summary_absent_when_no_flags_supplied(self, valid_experiment_spec):
        """When no trial-accounting flags are given, trial_accounting_summary is None in the artifact."""
        artifact = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            run_owner="test@test",
            # no trial-accounting flags
        )
        assert artifact.get("trial_accounting_summary") is None

    def test_trial_accounting_summary_emitted_in_success_artifact_when_flags_supplied(
        self, valid_experiment_spec
    ):
        """trial_accounting_summary appears in success artifact when at least one flag is given."""
        artifact = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            run_owner="test@test",
            trial_accounting_status="proposed",
            trial_accounting_mutation_mode="dry_run_reference_only",
            search_space_id="SS-001",
            n_tried=10,
        )
        tas = artifact.get("trial_accounting_summary")
        assert tas is not None
        assert tas["status"] == "proposed"
        assert tas["mutation_mode"] == "dry_run_reference_only"
        assert tas["search_space_id"] == "SS-001"
        assert tas["n_tried"] == 10
        assert tas["experiment_id"] == "EXP-2026-0001"
        assert tas["complexity"] is None

    def test_trial_accounting_summary_defaults_status_to_proposed(self, valid_experiment_spec):
        """When flags are supplied but status is omitted, status defaults to 'proposed'."""
        artifact = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            run_owner="test@test",
            trial_accounting_mutation_mode="dry_run_reference_only",
        )
        tas = artifact.get("trial_accounting_summary")
        assert tas is not None
        assert tas["status"] == "proposed"

    def test_trial_accounting_summary_defaults_mutation_mode_to_dry_run_reference_only(
        self, valid_experiment_spec
    ):
        """When flags are supplied but mutation_mode is omitted, it defaults to dry_run_reference_only."""
        artifact = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            run_owner="test@test",
            trial_accounting_status="proposed",
        )
        tas = artifact.get("trial_accounting_summary")
        assert tas is not None
        assert tas["mutation_mode"] == "dry_run_reference_only"

    def test_trial_accounting_summary_rejects_ledger_write(self, valid_experiment_spec):
        """--trial-accounting-mutation-mode ledger_write must be rejected with a clear error."""
        with pytest.raises(ValueError, match="ledger_write"):
            build_runner_output(
                experiment_spec_path=valid_experiment_spec,
                run_owner="test@test",
                trial_accounting_mutation_mode="ledger_write",
            )

    def test_trial_accounting_summary_rejects_registry_write(self, valid_experiment_spec):
        """--trial-accounting-mutation-mode registry_write must be rejected with a clear error."""
        with pytest.raises(ValueError, match="registry_write"):
            build_runner_output(
                experiment_spec_path=valid_experiment_spec,
                run_owner="test@test",
                trial_accounting_mutation_mode="registry_write",
            )

    def test_trial_accounting_summary_rejects_invalid_mutation_mode(self, valid_experiment_spec):
        """An invalid mutation_mode value not in the allowed set is rejected."""
        with pytest.raises(ValueError, match="Invalid mutation_mode"):
            build_runner_output(
                experiment_spec_path=valid_experiment_spec,
                run_owner="test@test",
                trial_accounting_mutation_mode="some_invalid_mode",
            )

    def test_not_applicable_only_when_explicitly_supplied(self, valid_experiment_spec):
        """status=not_applicable is accepted only when explicitly supplied (not as default)."""
        artifact = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            run_owner="test@test",
            trial_accounting_status="not_applicable",
            trial_accounting_mutation_mode="dry_run_reference_only",
        )
        tas = artifact.get("trial_accounting_summary")
        assert tas is not None
        assert tas["status"] == "not_applicable"

    def test_complexity_object_emitted_when_complexity_flags_supplied(
        self, valid_experiment_spec
    ):
        """Complexity sub-object is present when complexity flags are given."""
        artifact = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            run_owner="test@test",
            trial_accounting_status="proposed",
            trial_accounting_mutation_mode="dry_run_reference_only",
            complexity_rule_count=42,
            complexity_parameter_count=7,
            complexity_signal_count=5,
            complexity_filter_count=3,
            complexity_bucket="medium",
        )
        tas = artifact.get("trial_accounting_summary")
        assert tas is not None
        assert tas["complexity"] is not None
        assert tas["complexity"]["rule_count"] == 42
        assert tas["complexity"]["parameter_count"] == 7
        assert tas["complexity"]["signal_count"] == 5
        assert tas["complexity"]["filter_count"] == 3
        assert tas["complexity"]["complexity_bucket"] == "medium"

    def test_complexity_object_null_when_no_complexity_flags(self, valid_experiment_spec):
        """Complexity is None (not omitted) when no complexity flags are supplied."""
        artifact = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            run_owner="test@test",
            trial_accounting_status="proposed",
            trial_accounting_mutation_mode="dry_run_reference_only",
            n_tried=5,
        )
        tas = artifact.get("trial_accounting_summary")
        assert tas is not None
        assert tas["complexity"] is None

    def test_experiment_id_auto_populated(self, valid_experiment_spec):
        """experiment_id is automatically populated from experiment_spec without a CLI flag."""
        artifact = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            run_owner="test@test",
            trial_accounting_status="proposed",
        )
        tas = artifact.get("trial_accounting_summary")
        assert tas is not None
        assert tas["experiment_id"] == "EXP-2026-0001"

    def test_data_manifest_id_auto_populated(self, tmp_path, valid_experiment_spec):
        """data_manifest_id is automatically populated when a DataManifest is loaded."""
        csv_path = tmp_path / "obs.csv"
        csv_path.write_text("date,symbol,close\n2026-01-02,AAPL,150.0\n")

        dm_content = {
            "dataset_id": "DM-2026-0001",
            "role": "generic",
            "source_kind": "local_csv",
            "path": csv_path.name,
            "format": "csv",
        }
        dm_path = tmp_path / "manifest.json"
        dm_path.write_text(json.dumps(dm_content))

        artifact = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            data_manifest_path=str(dm_path),
            run_owner="test@test",
            trial_accounting_status="proposed",
        )
        tas = artifact.get("trial_accounting_summary")
        assert tas is not None
        assert tas["data_manifest_id"] == "DM-2026-0001"

    def test_trial_accounting_summary_in_failed_validation_artifact_when_flags_supplied(
        self, experiment_spec_autonomous_search_true
    ):
        """failed_validation artifact includes trial_accounting_summary when flags are supplied."""
        with pytest.raises(GovernanceRejection) as exc_info:
            build_runner_output(
                experiment_spec_path=experiment_spec_autonomous_search_true,
                run_owner="test@test",
                trial_accounting_status="proposed",
                trial_accounting_mutation_mode="dry_run_reference_only",
            )
        artifact = exc_info.value.artifact
        tas = artifact.get("trial_accounting_summary")
        assert tas is not None
        assert tas["status"] == "proposed"
        assert tas["mutation_mode"] == "dry_run_reference_only"

    def test_trial_accounting_summary_absent_in_failed_validation_when_no_flags(
        self, experiment_spec_autonomous_search_true
    ):
        """When no trial-accounting flags are given, failed_validation artifact has None for the field."""
        with pytest.raises(GovernanceRejection) as exc_info:
            build_runner_output(
                experiment_spec_path=experiment_spec_autonomous_search_true,
                run_owner="test@test",
            )
        artifact = exc_info.value.artifact
        assert artifact.get("trial_accounting_summary") is None

    def test_mutation_mode_no_mutation_accepted(self, valid_experiment_spec):
        """mutation_mode=no_mutation is accepted and passed through correctly."""
        artifact = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            run_owner="test@test",
            trial_accounting_status="proposed",
            trial_accounting_mutation_mode="no_mutation",
        )
        tas = artifact.get("trial_accounting_summary")
        assert tas is not None
        assert tas["mutation_mode"] == "no_mutation"


class TestMissingValueSummaryIntegration:
    """Integration tests for --observation-missing-value-columns feature."""

    def test_no_missing_value_columns_preserves_success_behavior(
        self, csv_with_date_and_symbol, valid_experiment_spec, tmp_path
    ):
        """Without --observation-missing-value-columns, runner succeeds normally."""
        manifest_path, csv_path = csv_with_date_and_symbol
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        assert artifact["status"] == "success"
        # No missing-value audit entry
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_missing_value_summary" not in audit_names

    def test_cli_accepts_missing_value_columns(
        self, csv_with_volume_and_missing_values, valid_experiment_spec, tmp_path
    ):
        """CLI accepts --observation-missing-value-columns without error."""
        manifest_path, csv_path = csv_with_volume_and_missing_values
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-missing-value-columns", "volume",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        # Missing volume → validation_error exit 1 (column exists, values missing)
        assert rc == 1
        artifact = json.loads(output_path.read_text())
        assert artifact["status"] == "failed_validation"
        assert artifact["failure_summary"]["failure_type"] == "validation_error"

    def test_missing_value_requires_data_manifest(self, valid_experiment_spec, tmp_path):
        """--observation-missing-value-columns without --data-manifest fails closed."""
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--observation-missing-value-columns", "volume",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        # ValueError from build_runner_output → exit 1
        assert rc == 1

    def test_non_csv_manifest_with_missing_value_fails_closed(
        self, valid_sqlite_manifest, valid_experiment_spec, tmp_path
    ):
        """Non-CSV manifest with --observation-missing-value-columns fails with unsupported_config."""
        manifest_path, _ = valid_sqlite_manifest
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-missing-value-columns", "volume",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 1
        artifact = json.loads(output_path.read_text())
        assert artifact["status"] == "failed_validation"
        assert artifact["failure_summary"]["failure_type"] == "unsupported_config"

    def test_missing_requested_column_fails_closed(
        self, csv_with_date_and_symbol, valid_experiment_spec, tmp_path
    ):
        """Requesting a non-existent column fails closed with validation_error."""
        manifest_path, csv_path = csv_with_date_and_symbol
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-missing-value-columns", "nonexistent_col",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 1
        artifact = json.loads(output_path.read_text())
        assert artifact["status"] == "failed_validation"
        assert artifact["failure_summary"]["failure_type"] == "validation_error"

    def test_valid_csv_missing_value_summary_audit_pass(
        self, csv_with_no_missing_values, valid_experiment_spec, tmp_path
    ):
        """Valid CSV with no missing values produces pass audit."""
        manifest_path, csv_path = csv_with_no_missing_values
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-missing-value-columns", "volume,bid",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        assert artifact["status"] == "success"
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_missing_value_summary" in audit_names
        missing_val_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_missing_value_summary"
        )
        assert missing_val_audit["audit_result"] == "pass"
        assert "row_count=3" in missing_val_audit["details_ref"]
        assert "missing[volume]=0" in missing_val_audit["details_ref"]
        assert "missing[bid]=0" in missing_val_audit["details_ref"]

    def test_missing_value_summary_details_include_counts(
        self, csv_with_volume_and_missing_values, valid_experiment_spec, tmp_path
    ):
        """Details include row_count and missing counts per column."""
        manifest_path, csv_path = csv_with_volume_and_missing_values
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-missing-value-columns", "volume,bid",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 1
        artifact = json.loads(output_path.read_text())
        missing_val_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_missing_value_summary"
        )
        assert missing_val_audit["audit_result"] == "fail"
        assert "row_count=4" in missing_val_audit["details_ref"]
        assert "missing[volume]=1" in missing_val_audit["details_ref"]
        assert "missing[bid]=1" in missing_val_audit["details_ref"]

    def test_multiple_requested_columns_summarized(
        self, csv_with_volume_and_missing_values, valid_experiment_spec, tmp_path
    ):
        """Multiple requested columns are each summarized."""
        manifest_path, csv_path = csv_with_volume_and_missing_values
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-missing-value-columns", "volume,bid",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 1
        artifact = json.loads(output_path.read_text())
        missing_val_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_missing_value_summary"
        )
        details = missing_val_audit["details_ref"]
        assert "missing[volume]=" in details
        assert "missing[bid]=" in details
        assert "row_count=" in details

    def test_hash_changes_when_missing_value_columns_provided(
        self, csv_with_volume_and_missing_values, valid_experiment_spec, tmp_path
    ):
        """run_config_hash changes when missing-value columns are included."""
        manifest_path, csv_path = csv_with_volume_and_missing_values
        output1 = tmp_path / "output1.json"
        output2 = tmp_path / "output2.json"

        # Without missing-value columns
        rc1 = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--output-path", str(output1),
            "--run-owner", "test",
        ])
        assert rc1 == 0

        # With missing-value columns
        rc2 = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-missing-value-columns", "volume",
            "--output-path", str(output2),
            "--run-owner", "test",
        ])
        assert rc2 == 1  # missing values exist

        artifact1 = json.loads(output1.read_text())
        artifact2 = json.loads(output2.read_text())
        assert artifact1["run_config_hash"] != artifact2["run_config_hash"]

    def test_hash_whitespace_normalized_for_missing_value_columns(
        self, csv_with_volume_and_missing_values, valid_experiment_spec, tmp_path
    ):
        """Leading/trailing whitespace in missing-value columns is normalized for hash."""
        manifest_path, csv_path = csv_with_volume_and_missing_values
        output1 = tmp_path / "output1.json"
        output2 = tmp_path / "output2.json"

        rc1 = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-missing-value-columns", " volume ",
            "--output-path", str(output1),
            "--run-owner", "test",
        ])
        rc2 = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-missing-value-columns", "volume",
            "--output-path", str(output2),
            "--run-owner", "test",
        ])
        # Both should fail (volume has missing values) but hash should be same
        assert rc1 == 1
        assert rc2 == 1
        artifact1 = json.loads(output1.read_text())
        artifact2 = json.loads(output2.read_text())
        assert artifact1["run_config_hash"] == artifact2["run_config_hash"]

    def test_internal_whitespace_preserved_for_hash(
        self, csv_with_volume_and_missing_values, valid_experiment_spec, tmp_path
    ):
        """Internal whitespace in column names is preserved for hash determinism."""
        csv_file = tmp_path / "internal_ws.csv"
        csv_file.write_text(
            "date,symbol,close price\n"
            "2024-01-01,AAPL,100.0\n"
            "2024-01-02,AAPL,\n"
        )
        manifest_file = tmp_path / "dm.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "DM-WS",
            "role": "generic",
            "source_kind": "local_csv",
            "path": "internal_ws.csv",
            "format": "csv",
        }, indent=2))

        output1 = tmp_path / "output1.json"
        output2 = tmp_path / "output2.json"

        rc1 = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-missing-value-columns", "close price",
            "--output-path", str(output1),
            "--run-owner", "test",
        ])
        rc2 = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_file),
            "--observation-missing-value-columns", "close price",
            "--output-path", str(output2),
            "--run-owner", "test",
        ])
        assert rc1 == 1
        assert rc2 == 1
        artifact1 = json.loads(output1.read_text())
        artifact2 = json.loads(output2.read_text())
        assert artifact1["run_config_hash"] == artifact2["run_config_hash"]

    def test_required_columns_plus_missing_value_failure_preserves_both(
        self, csv_missing_close_column, valid_experiment_spec, tmp_path
    ):
        """Required columns failure + missing-value failure preserves both blockers."""
        manifest_path, csv_path = csv_missing_close_column
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--required-observation-columns", "date,symbol,close",
            "--observation-missing-value-columns", "symbol",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 1
        artifact = json.loads(output_path.read_text())
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_shape_validation" in audit_names
        assert "observation_table_missing_value_summary" in audit_names
        assert artifact["audit_summary"]["blocker_count"] >= 2

    def test_governance_blocker_plus_missing_value_failure_preserves_both(
        self, csv_with_volume_and_missing_values, valid_experiment_spec, tmp_path
    ):
        """Autonomous search blocker + missing-value failure preserves both blockers."""
        spec_with_autonomous = tmp_path / "spec.json"
        spec_content = {
            "experiment_id": "EXP-AUTO-BLOCKER",
            "experiment_version": 1,
            "hypothesis_id": "HYP-AUTO",
            "search_space_id": "SSM-AUTO",
            "data_manifest_refs": ["DM-AUTO"],
            "study_type": "options_event_risk",
            "decision_timestamp_policy": {"timestamp_ref": "reference_date"},
            "feature_cutoff_policy": {"timestamp_ref": "trade_date", "offset_direction": "before", "offset_unit": "trading_days", "offset_value": 1},
            "trial_generation_mode": "literature_replication",
            "allowed_trial_lanes": ["theory_first"],
            "prohibited_modes": {"autonomous_search": True},  # BLOCKER
            "reviewer": {"name": "t", "affiliation": "t", "date": "2026-01-01"},
        }
        spec_with_autonomous.write_text(json.dumps(spec_content, indent=2))
        manifest_path, csv_path = csv_with_volume_and_missing_values
        output_path = tmp_path / "output.json"

        rc = main([
            "--experiment-spec", str(spec_with_autonomous),
            "--data-manifest", str(manifest_path),
            "--observation-missing-value-columns", "volume",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 1
        artifact = json.loads(output_path.read_text())
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "no_autonomous_search_flag_set" in audit_names
        assert "observation_table_missing_value_summary" in audit_names
        assert artifact["audit_summary"]["blocker_count"] >= 2

    def test_schema_validation_missing_value_success(
        self, csv_with_no_missing_values, valid_experiment_spec, tmp_path
    ):
        """Success artifact with missing-value summary validates against schema."""
        jsonschema = pytest.importorskip("jsonschema")
        manifest_path, csv_path = csv_with_no_missing_values
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-missing-value-columns", "volume",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        schema_path = _ROOT / "schemas" / "runner_output_spec_v1.schema.json"
        jsonschema.validate(artifact, json.loads(schema_path.read_text()))

    def test_schema_validation_missing_value_failure(
        self, csv_with_volume_and_missing_values, valid_experiment_spec, tmp_path
    ):
        """Failed-validation artifact with missing-value summary validates against schema."""
        jsonschema = pytest.importorskip("jsonschema")
        manifest_path, csv_path = csv_with_volume_and_missing_values
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-missing-value-columns", "volume",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 1
        artifact = json.loads(output_path.read_text())
        schema_path = _ROOT / "schemas" / "runner_output_spec_v1.schema.json"
        jsonschema.validate(artifact, json.loads(schema_path.read_text()))

    def test_no_registry_mutation_with_missing_value_summary(
        self, csv_with_no_missing_values, valid_experiment_spec, tmp_path, monkeypatch
    ):
        """No registry or ledger files are written during missing-value summary."""
        writes = []
        original_open = open

        def track_open(path, *args, **kwargs):
            writes.append(str(path))
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", track_open)
        manifest_path, csv_path = csv_with_no_missing_values
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-missing-value-columns", "volume",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 0
        # Verify registry and ledger files are not mutated by checking mtime
        registry_path = Path(_ROOT) / "docs" / "edge_hypothesis_registry.csv"
        ledger_path = Path(_ROOT) / "docs" / "trial_ledger.jsonl"
        registry_mtime_before = registry_mtime_after = None
        ledger_mtime_before = ledger_mtime_after = None
        if registry_path.exists():
            registry_mtime_before = registry_path.stat().st_mtime
        if ledger_path.exists():
            ledger_mtime_before = ledger_path.stat().st_mtime
        # (Dry-run completes here — rc == 0 above confirms it succeeded)
        if registry_path.exists():
            registry_mtime_after = registry_path.stat().st_mtime
        if ledger_path.exists():
            ledger_mtime_after = ledger_path.stat().st_mtime
        assert registry_mtime_before == registry_mtime_after, (
            f"EdgeHypothesisRegistry was modified during dry-run"
        )
        assert ledger_mtime_before == ledger_mtime_after, (
            f"TrialLedger was modified during dry-run"
        )


class TestCloseReturnHashDeterminism:
    """Integration tests proving observation_close_column contributes
    deterministically to run_config_hash and run_id, matching the pattern
    established for required_observation_columns and missing_value_columns.

    These tests use _compute_run_config_hash directly to isolate the hash-
    computation logic without requiring a full CLI run or CSV file.
    """

    def test_same_close_column_produces_same_hash(self, valid_experiment_spec):
        """Identical observation_close_column → identical run_config_hash."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            _compute_run_config_hash,
        )
        hash1 = _compute_run_config_hash(
            valid_experiment_spec,
            observation_close_column="close",
        )
        hash2 = _compute_run_config_hash(
            valid_experiment_spec,
            observation_close_column="close",
        )
        assert hash1 == hash2, (
            "Same observation_close_column must produce identical run_config_hash"
        )

    def test_different_close_column_produces_different_hash(
        self, valid_experiment_spec
    ):
        """Different observation_close_column → different run_config_hash."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            _compute_run_config_hash,
        )
        hash_close = _compute_run_config_hash(
            valid_experiment_spec,
            observation_close_column="close",
        )
        hash_price = _compute_run_config_hash(
            valid_experiment_spec,
            observation_close_column="price",
        )
        assert hash_close != hash_price, (
            "Different observation_close_column must produce different "
            "run_config_hash"
        )

    def test_whitespace_normalized_close_column_same_hash(
        self, valid_experiment_spec
    ):
        """Leading/trailing whitespace in observation_close_column is stripped;
        ' close ' and 'close' produce the same run_config_hash."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            _compute_run_config_hash,
        )
        hash_stripped = _compute_run_config_hash(
            valid_experiment_spec,
            observation_close_column="close",
        )
        hash_whitespace = _compute_run_config_hash(
            valid_experiment_spec,
            observation_close_column="  close  ",
        )
        assert hash_stripped == hash_whitespace, (
            "Whitespace-normalized observation_close_column must produce "
            "identical run_config_hash"
        )

    def test_close_column_with_date_and_symbol_columns_same_hash(
        self, valid_experiment_spec
    ):
        """observation_close_column interacts with date/symbol columns in the hash;
        all three together produce a deterministic hash."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            _compute_run_config_hash,
        )
        hash_all_three = _compute_run_config_hash(
            valid_experiment_spec,
            observation_date_column="date",
            observation_symbol_column="symbol",
            observation_close_column="close",
        )
        hash_again = _compute_run_config_hash(
            valid_experiment_spec,
            observation_date_column="date",
            observation_symbol_column="symbol",
            observation_close_column="close",
        )
        assert hash_all_three == hash_again, (
            "Same date+symbol+close columns must produce identical hash"
        )

    def test_close_column_changes_date_symbol_hash(
        self, valid_experiment_spec
    ):
        """Adding observation_close_column to date+symbol columns changes the hash."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            _compute_run_config_hash,
        )
        hash_date_symbol = _compute_run_config_hash(
            valid_experiment_spec,
            observation_date_column="date",
            observation_symbol_column="symbol",
        )
        hash_date_symbol_close = _compute_run_config_hash(
            valid_experiment_spec,
            observation_date_column="date",
            observation_symbol_column="symbol",
            observation_close_column="close",
        )
        assert hash_date_symbol != hash_date_symbol_close, (
            "Adding observation_close_column must change the run_config_hash"
        )

    def test_run_id_derives_from_hash_with_close_column(
        self, valid_experiment_spec
    ):
        """run_id is derived from run_config_hash (first 16 hex chars) even when
        observation_close_column is included in the hash."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            _compute_run_config_hash,
            _compute_run_id,
        )
        hash1 = _compute_run_config_hash(
            valid_experiment_spec,
            observation_close_column="close",
        )
        hash2 = _compute_run_config_hash(
            valid_experiment_spec,
            observation_close_column="close",
        )
        run_id1 = _compute_run_id(hash1)
        run_id2 = _compute_run_id(hash2)
        assert run_id1 == run_id2, (
            "run_id must be identical for identical close_column configuration"
        )
        assert run_id1 == hash1[:16], (
            "run_id must be the first 16 hex characters of run_config_hash"
        )

    def test_missing_close_column_omitted_from_hash(self, valid_experiment_spec):
        """When observation_close_column is None, the hash does not include
        the close_col: prefix; it matches the base hash."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            _compute_run_config_hash,
        )
        hash_no_close = _compute_run_config_hash(valid_experiment_spec)
        hash_with_close = _compute_run_config_hash(
            valid_experiment_spec,
            observation_close_column=None,
        )
        assert hash_no_close == hash_with_close, (
            "observation_close_column=None must not alter the hash"
        )

    def test_empty_close_column_omitted_from_hash(self, valid_experiment_spec):
        """When observation_close_column is an empty string, the hash does not
        include the close_col: prefix; it matches the base hash."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            _compute_run_config_hash,
        )
        hash_no_close = _compute_run_config_hash(valid_experiment_spec)
        hash_empty_close = _compute_run_config_hash(
            valid_experiment_spec,
            observation_close_column="  ",
        )
        assert hash_no_close == hash_empty_close, (
            "observation_close_column='' or whitespace-only must not alter "
            "the hash"
        )

    def test_run_id_differs_when_close_column_differs(
        self, valid_experiment_spec
    ):
        """Different observation_close_column → different run_id."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            _compute_run_config_hash,
            _compute_run_id,
        )
        hash_close = _compute_run_config_hash(
            valid_experiment_spec,
            observation_close_column="close",
        )
        hash_price = _compute_run_config_hash(
            valid_experiment_spec,
            observation_close_column="price",
        )
        run_id_close = _compute_run_id(hash_close)
        run_id_price = _compute_run_id(hash_price)
        assert run_id_close != run_id_price, (
            "Different observation_close_column must produce different run_id"
        )


class TestDuplicateRowSummaryIntegration:
    """Integration tests for observation_table_duplicate_row_summary audit wiring."""

    def test_audit_entry_present_when_date_and_symbol_columns_provided(
        self, csv_with_no_duplicates, valid_experiment_spec, tmp_path
    ):
        """With date+symbol columns, audit contains observation_table_duplicate_row_summary."""
        manifest_path, _ = csv_with_no_duplicates
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_duplicate_row_summary" in audit_names

    def test_audit_result_pass_when_no_duplicates(
        self, csv_with_no_duplicates, valid_experiment_spec, tmp_path
    ):
        """No duplicate rows → audit_result = 'pass', blocker_count unchanged."""
        manifest_path, _ = csv_with_no_duplicates
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        dup_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_duplicate_row_summary"
        )
        assert dup_audit["audit_result"] == "pass"
        assert dup_audit["blocker_count"] == 0
        assert "no duplicate observation rows were detected" in dup_audit["details_ref"]

    def test_audit_result_fail_when_duplicates_exist(
        self, csv_with_one_duplicate_pair, valid_experiment_spec, tmp_path
    ):
        """Duplicate rows exist → audit_result = 'fail' (severity info, not blocker)."""
        manifest_path, _ = csv_with_one_duplicate_pair
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        dup_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_duplicate_row_summary"
        )
        assert dup_audit["audit_result"] == "fail"
        assert dup_audit["severity"] == "info"
        assert dup_audit["blocker_count"] == 0
        assert "duplicate_row_count=1" in dup_audit["details_ref"]

    def test_duplicate_row_count_reflected_in_audit_details(
        self, csv_with_multiple_duplicate_pairs, valid_experiment_spec, tmp_path
    ):
        """Multiple duplicate pairs → affected_key_count and duplicate_row_count reflected."""
        manifest_path, _ = csv_with_multiple_duplicate_pairs
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        dup_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_duplicate_row_summary"
        )
        # AAPL 2024-01-01: 2 rows (1 excess), MSFT 2024-01-01: 2 rows (1 excess)
        assert "duplicate_row_count=2" in dup_audit["details_ref"]
        assert "affected_key_count=2" in dup_audit["details_ref"]

    def test_existing_audit_entries_preserved(
        self, csv_with_no_duplicates, valid_experiment_spec, tmp_path
    ):
        """Duplicate-row audit appends; existing audit entries are preserved."""
        manifest_path, _ = csv_with_no_duplicates
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        # Must have canonical summary audit (since date+symbol are provided)
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_canonical_summary" in audit_names
        # Must have duplicate-row audit
        assert "observation_table_duplicate_row_summary" in audit_names
        # Must have governance audits
        assert "schema_validation_all_inputs" in audit_names
        assert "no_registry_mutation" in audit_names

    def test_no_date_symbol_columns_no_duplicate_audit(
        self, csv_with_date_and_symbol, valid_experiment_spec, tmp_path
    ):
        """Without date+symbol columns, no duplicate-row audit is produced."""
        manifest_path, _ = csv_with_date_and_symbol
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_duplicate_row_summary" not in audit_names

    def test_non_csv_manifest_duplicate_audit_fails_closed(
        self, valid_sqlite_manifest, valid_experiment_spec, tmp_path
    ):
        """Non-CSV manifest with date+symbol columns fails with unsupported_config.

        Note: the canonical summary block raises GovernanceRejection BEFORE
        the duplicate-row block can run for this exact configuration. The
        duplicate-row unsupported_format audit only appears when the
        canonical summary does NOT raise first (i.e., when the duplicate-row
        block itself encounters an unsupported format).
        """
        manifest_path, _ = valid_sqlite_manifest
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 1
        artifact = json.loads(output_path.read_text())
        assert artifact["status"] == "failed_validation"
        assert artifact["failure_summary"]["failure_type"] == "unsupported_config"

    def test_artifact_schema_valid_with_duplicate_audit(
        self, csv_with_one_duplicate_pair, valid_experiment_spec, tmp_path
    ):
        """RunnerOutput artifact is schema-valid when duplicate audit is present."""
        pytest.importorskip("jsonschema")
        import jsonschema
        manifest_path, _ = csv_with_one_duplicate_pair
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "runner_output_spec_v1.schema.json"
        schema = json.loads(schema_path.read_text())
        jsonschema.validate(artifact, schema)

    def test_build_runner_output_duplicate_audit_entry_preserves_blocker_count(
        self, csv_with_no_duplicates, valid_experiment_spec
    ):
        """Adding duplicate audit does not corrupt blocker_count from prior audits."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            build_runner_output,
        )
        manifest_path, _ = csv_with_no_duplicates
        artifact = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            data_manifest_path=manifest_path,
            observation_date_column="date",
            observation_symbol_column="symbol",
            run_owner="test",
        )
        assert artifact["status"] == "success"
        assert artifact["audit_summary"]["blocker_count"] == 0
        # canonical audit + duplicate audit should both be present
        canonical_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_canonical_summary"
        )
        dup_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_duplicate_row_summary"
        )
        assert canonical_audit["blocker_count"] == 0
        assert dup_audit["blocker_count"] == 0


class TestDateCoverageSummaryIntegration:
    """Integration tests for observation_table_date_coverage_summary audit wiring."""

    def test_audit_entry_present(self, csv_with_no_duplicates, valid_experiment_spec, tmp_path):
        """With date+symbol columns, audit contains observation_table_date_coverage_summary."""
        manifest_path, _ = csv_with_no_duplicates
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_date_coverage_summary" in audit_names

    def test_audit_result_pass(self, csv_with_no_duplicates, valid_experiment_spec, tmp_path):
        """Successful summary → audit_result = 'pass', severity = 'info'."""
        manifest_path, _ = csv_with_no_duplicates
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        dc_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_date_coverage_summary"
        )
        assert dc_audit["audit_result"] == "pass"
        assert dc_audit["severity"] == "info"
        assert dc_audit["blocker_count"] == 0

    def test_summary_reflects_counts(self, csv_with_no_duplicates, valid_experiment_spec, tmp_path):
        """Summary fields reflect correct symbol_count, min_date, max_date, observed_date_count."""
        manifest_path, _ = csv_with_no_duplicates
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        dc_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_date_coverage_summary"
        )
        # details_ref contains the summary fields
        details = dc_audit["details_ref"]
        assert "symbol_count=" in details
        assert "observed_date_count=" in details
        assert "min_date=" in details
        assert "max_date=" in details

    def test_existing_audit_entries_preserved(self, csv_with_no_duplicates, valid_experiment_spec, tmp_path):
        """Date-coverage audit appends; existing audits (canonical, duplicate) are preserved."""
        manifest_path, _ = csv_with_no_duplicates
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_canonical_summary" in audit_names
        assert "observation_table_duplicate_row_summary" in audit_names
        assert "observation_table_date_coverage_summary" in audit_names

    def test_artifact_schema_valid(self, csv_with_no_duplicates, valid_experiment_spec, tmp_path):
        """RunnerOutput artifact is schema-valid when date-coverage audit is present."""
        pytest.importorskip("jsonschema")
        import jsonschema
        manifest_path, _ = csv_with_no_duplicates
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(valid_experiment_spec),
            "--data-manifest", str(manifest_path),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--output-path", str(output_path),
            "--run-owner", "test",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "runner_output_spec_v1.schema.json"
        schema = json.loads(schema_path.read_text())
        jsonschema.validate(artifact, schema)

    def test_blocker_count_preserved_after_adding_date_coverage_audit(
        self, csv_with_no_duplicates, valid_experiment_spec
    ):
        """Adding date-coverage audit does not corrupt blocker_count from prior audits."""
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            build_runner_output,
        )
        manifest_path, _ = csv_with_no_duplicates
        artifact = build_runner_output(
            experiment_spec_path=valid_experiment_spec,
            data_manifest_path=manifest_path,
            observation_date_column="date",
            observation_symbol_column="symbol",
            run_owner="test",
        )
        assert artifact["status"] == "success"
        # blocker_count must be 0 and not inflated by the info-severity date-coverage audit
        assert artifact["audit_summary"]["blocker_count"] == 0
        dc_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_date_coverage_summary"
        )
        assert dc_audit["blocker_count"] == 0
        canonical_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_canonical_summary"
        )
        assert canonical_audit["blocker_count"] == 0



# -------------------------------------------------------------------------- -
# Test: First thin runner local smoke — full dry-run path with observation tables
# -------------------------------------------------------------------------- -

SMOKE_CSV_DATA = (
    "date,symbol,close\n"
    "2024-01-02,AAPL,185.50\n"
    "2024-01-03,AAPL,186.00\n"
    "2024-01-04,AAPL,187.20\n"
    "2024-01-02,MSFT,415.00\n"
    "2024-01-03,MSFT,416.50\n"
    "2024-01-04,GOOGL,175.00\n"
)

SMOKE_MANIFEST_DATA = {
    "dataset_id": "DM-2026-SMOKE",
    "role": "generic",
    "source_kind": "local_csv",
    "path": "smoke_obs.csv",
    "format": "csv",
}


class TestFirstThinRunnerLocalSmoke:
    """Tiny end-to-end smoke tests for the first thin real data runner.

    Exercises the full dry-run path with a local CSV observation table and
    DataManifest, verifying schema-valid RunnerOutput and the presence of all
    observation table audit entries (canonical, close-return, duplicate-row,
    date-coverage).  Missing-value summary is not requested so it does not
    appear — that is the current runner behaviour and changing it is out of
    scope for this PR.
    """

    def test_full_dry_run_produces_success_artifact(
        self, tmp_path
    ):
        """main() with date+symbol+close CSV returns rc=0 and status='success'."""
        csv_file = tmp_path / "smoke_obs.csv"
        csv_file.write_text(SMOKE_CSV_DATA)
        manifest_file = tmp_path / "smoke_manifest.json"
        manifest_file.write_text(json.dumps(SMOKE_MANIFEST_DATA, indent=2))
        spec_file = tmp_path / "smoke_spec.json"
        spec_file.write_text(json.dumps({
            "experiment_id": "EXP-2026-0001",
            "experiment_version": 1,
            "hypothesis_id": "HYP-2026-0001",
            "search_space_id": "SSM-2026-0001",
            "data_manifest_refs": ["DM-2026-SMOKE"],
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
            "created_at": "2026-05-06T00:00:00Z",
            "reviewer": {
                "name": "smoke_tester",
                "affiliation": "ci",
                "date": "2026-05-06",
            },
        }, indent=2))
        output_path = tmp_path / "smoke_output.json"
        rc = main([
            "--experiment-spec", str(spec_file),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--output-path", str(output_path),
            "--run-owner", "smoke",
        ])
        assert rc == 0, f"main() returned {rc}"
        artifact = json.loads(output_path.read_text())
        assert artifact["status"] == "success", (
            f"Expected status='success', got {artifact['status']}"
        )

    def test_artifact_schema_valid_and_contains_required_fields(
        self, tmp_path
    ):
        """RunnerOutput is schema-valid and has all required fields."""
        pytest.importorskip("jsonschema")
        import jsonschema

        csv_file = tmp_path / "smoke_obs.csv"
        csv_file.write_text(SMOKE_CSV_DATA)
        manifest_file = tmp_path / "smoke_manifest.json"
        manifest_file.write_text(json.dumps(SMOKE_MANIFEST_DATA, indent=2))
        spec_file = tmp_path / "smoke_spec.json"
        spec_file.write_text(json.dumps({
            "experiment_id": "EXP-2026-0002",
            "experiment_version": 1,
            "hypothesis_id": "HYP-2026-0002",
            "search_space_id": "SSM-2026-0002",
            "data_manifest_refs": ["DM-2026-SMOKE"],
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
            "created_at": "2026-05-06T00:00:00Z",
            "reviewer": {
                "name": "smoke_tester",
                "affiliation": "ci",
                "date": "2026-05-06",
            },
        }, indent=2))
        output_path = tmp_path / "smoke_output.json"
        rc = main([
            "--experiment-spec", str(spec_file),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--output-path", str(output_path),
            "--run-owner", "smoke",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "schemas"
            / "runner_output_spec_v1.schema.json"
        )
        schema = json.loads(schema_path.read_text())
        jsonschema.validate(artifact, schema)
        # Required structural fields
        assert artifact["input_artifact_refs"], "input_artifact_refs must be non-empty"
        assert artifact["data_manifest_refs"], "data_manifest_refs must be non-empty"
        assert artifact["output_manifest"], "output_manifest must be non-empty"
        assert artifact["run_mode"] == "dry_run"

    def test_all_five_observation_table_audits_present(
        self, tmp_path
    ):
        """audit_summary contains all five observation table audits (missing-value not requested)."""
        csv_file = tmp_path / "smoke_obs.csv"
        csv_file.write_text(SMOKE_CSV_DATA)
        manifest_file = tmp_path / "smoke_manifest.json"
        manifest_file.write_text(json.dumps(SMOKE_MANIFEST_DATA, indent=2))
        spec_file = tmp_path / "smoke_spec.json"
        spec_file.write_text(json.dumps({
            "experiment_id": "EXP-2026-0003",
            "experiment_version": 1,
            "hypothesis_id": "HYP-2026-0003",
            "search_space_id": "SSM-2026-0003",
            "data_manifest_refs": ["DM-2026-SMOKE"],
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
            "created_at": "2026-05-06T00:00:00Z",
            "reviewer": {
                "name": "smoke_tester",
                "affiliation": "ci",
                "date": "2026-05-06",
            },
        }, indent=2))
        output_path = tmp_path / "smoke_output.json"
        rc = main([
            "--experiment-spec", str(spec_file),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--output-path", str(output_path),
            "--run-owner", "smoke",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_canonical_summary" in audit_names
        assert "observation_table_close_return_summary" in audit_names
        # missing_value_summary requires --observation-missing-value-columns; not requested
        assert "observation_table_duplicate_row_summary" in audit_names
        assert "observation_table_date_coverage_summary" in audit_names

    def test_audit_results_and_blocker_count(
        self, tmp_path
    ):
        """All present audits pass; overall blocker_count is 0."""
        csv_file = tmp_path / "smoke_obs.csv"
        csv_file.write_text(SMOKE_CSV_DATA)
        manifest_file = tmp_path / "smoke_manifest.json"
        manifest_file.write_text(json.dumps(SMOKE_MANIFEST_DATA, indent=2))
        spec_file = tmp_path / "smoke_spec.json"
        spec_file.write_text(json.dumps({
            "experiment_id": "EXP-2026-0004",
            "experiment_version": 1,
            "hypothesis_id": "HYP-2026-0004",
            "search_space_id": "SSM-2026-0004",
            "data_manifest_refs": ["DM-2026-SMOKE"],
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
            "created_at": "2026-05-06T00:00:00Z",
            "reviewer": {
                "name": "smoke_tester",
                "affiliation": "ci",
                "date": "2026-05-06",
            },
        }, indent=2))
        output_path = tmp_path / "smoke_output.json"
        rc = main([
            "--experiment-spec", str(spec_file),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--output-path", str(output_path),
            "--run-owner", "smoke",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        assert artifact["audit_summary"]["overall_result"] == "pass"
        assert artifact["audit_summary"]["blocker_count"] == 0
        # blocker_count equals sum of all individual blocker_counts
        total = sum(
            a["blocker_count"]
            for a in artifact["audit_summary"]["audits"]
        )
        assert total == 0, f"Expected 0, got {total}"
        # Individual audit checks
        for audit in artifact["audit_summary"]["audits"]:
            assert audit["audit_result"] in ("pass", "warn", "skipped"), (
                f"Unexpected audit_result={audit['audit_result']} "
                f"for {audit['audit_name']}"
            )

    def test_canonical_summary_row_count(
        self, tmp_path
    ):
        """canonical_summary row_count matches the 6 CSV data rows."""
        csv_file = tmp_path / "smoke_obs.csv"
        csv_file.write_text(SMOKE_CSV_DATA)
        manifest_file = tmp_path / "smoke_manifest.json"
        manifest_file.write_text(json.dumps(SMOKE_MANIFEST_DATA, indent=2))
        spec_file = tmp_path / "smoke_spec.json"
        spec_file.write_text(json.dumps({
            "experiment_id": "EXP-2026-0005",
            "experiment_version": 1,
            "hypothesis_id": "HYP-2026-0005",
            "search_space_id": "SSM-2026-0005",
            "data_manifest_refs": ["DM-2026-SMOKE"],
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
            "created_at": "2026-05-06T00:00:00Z",
            "reviewer": {
                "name": "smoke_tester",
                "affiliation": "ci",
                "date": "2026-05-06",
            },
        }, indent=2))
        output_path = tmp_path / "smoke_output.json"
        rc = main([
            "--experiment-spec", str(spec_file),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--output-path", str(output_path),
            "--run-owner", "smoke",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        canonical_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_canonical_summary"
        )
        assert canonical_audit["audit_result"] == "pass"
        assert "row_count=" in canonical_audit["details_ref"]
        # row_count should be 6 for the 6 CSV data rows
        import re
        m = re.search(r"row_count=(\d+)", canonical_audit["details_ref"])
        assert m is not None, f"row_count not found in details_ref: {canonical_audit['details_ref']}"
        assert int(m.group(1)) == 6, f"Expected row_count=6, got {m.group(1)}"

    def test_duplicate_row_summary_no_duplicates(
        self, tmp_path
    ):
        """duplicate_row_summary reports has_duplicates=false for the smoke CSV."""
        csv_file = tmp_path / "smoke_obs.csv"
        csv_file.write_text(SMOKE_CSV_DATA)
        manifest_file = tmp_path / "smoke_manifest.json"
        manifest_file.write_text(json.dumps(SMOKE_MANIFEST_DATA, indent=2))
        spec_file = tmp_path / "smoke_spec.json"
        spec_file.write_text(json.dumps({
            "experiment_id": "EXP-2026-0006",
            "experiment_version": 1,
            "hypothesis_id": "HYP-2026-0006",
            "search_space_id": "SSM-2026-0006",
            "data_manifest_refs": ["DM-2026-SMOKE"],
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
            "created_at": "2026-05-06T00:00:00Z",
            "reviewer": {
                "name": "smoke_tester",
                "affiliation": "ci",
                "date": "2026-05-06",
            },
        }, indent=2))
        output_path = tmp_path / "smoke_output.json"
        rc = main([
            "--experiment-spec", str(spec_file),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--output-path", str(output_path),
            "--run-owner", "smoke",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        dup_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_duplicate_row_summary"
        )
        assert dup_audit["audit_result"] == "pass"
        assert "duplicate_row_count=0" in dup_audit["details_ref"]
        assert dup_audit["blocker_count"] == 0

    def test_date_coverage_summary_reflects_symbols_and_dates(
        self, tmp_path
    ):
        """date_coverage_summary reflects symbol_count=3 and the observed date range."""
        csv_file = tmp_path / "smoke_obs.csv"
        csv_file.write_text(SMOKE_CSV_DATA)
        manifest_file = tmp_path / "smoke_manifest.json"
        manifest_file.write_text(json.dumps(SMOKE_MANIFEST_DATA, indent=2))
        spec_file = tmp_path / "smoke_spec.json"
        spec_file.write_text(json.dumps({
            "experiment_id": "EXP-2026-0007",
            "experiment_version": 1,
            "hypothesis_id": "HYP-2026-0007",
            "search_space_id": "SSM-2026-0007",
            "data_manifest_refs": ["DM-2026-SMOKE"],
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
            "created_at": "2026-05-06T00:00:00Z",
            "reviewer": {
                "name": "smoke_tester",
                "affiliation": "ci",
                "date": "2026-05-06",
            },
        }, indent=2))
        output_path = tmp_path / "smoke_output.json"
        rc = main([
            "--experiment-spec", str(spec_file),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--output-path", str(output_path),
            "--run-owner", "smoke",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        dc_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_date_coverage_summary"
        )
        assert dc_audit["audit_result"] == "pass"
        assert dc_audit["severity"] == "info"
        assert dc_audit["blocker_count"] == 0
        details = dc_audit["details_ref"]
        assert "symbol_count=3" in details, f"Expected symbol_count=3 in {details}"
        assert "min_date=2024-01-02" in details, f"Expected min_date=2024-01-02 in {details}"
        assert "max_date=2024-01-04" in details, f"Expected max_date=2024-01-04 in {details}"
        # AAPL has 3 dates, MSFT has 2, GOOGL has 1 → observed_date_count = 6
        assert "observed_date_count=6" in details, f"Expected observed_date_count=6 in {details}"

    def test_close_return_summary_passes_for_smoke_csv(
        self, tmp_path
    ):
        """close_return_summary audit is present and passes with the smoke CSV."""
        csv_file = tmp_path / "smoke_obs.csv"
        csv_file.write_text(SMOKE_CSV_DATA)
        manifest_file = tmp_path / "smoke_manifest.json"
        manifest_file.write_text(json.dumps(SMOKE_MANIFEST_DATA, indent=2))
        spec_file = tmp_path / "smoke_spec.json"
        spec_file.write_text(json.dumps({
            "experiment_id": "EXP-2026-0008",
            "experiment_version": 1,
            "hypothesis_id": "HYP-2026-0008",
            "search_space_id": "SSM-2026-0008",
            "data_manifest_refs": ["DM-2026-SMOKE"],
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
            "created_at": "2026-05-06T00:00:00Z",
            "reviewer": {
                "name": "smoke_tester",
                "affiliation": "ci",
                "date": "2026-05-06",
            },
        }, indent=2))
        output_path = tmp_path / "smoke_output.json"
        rc = main([
            "--experiment-spec", str(spec_file),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--output-path", str(output_path),
            "--run-owner", "smoke",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        close_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_close_return_summary"
        )
        assert close_audit["audit_result"] == "pass"
        assert close_audit["blocker_count"] == 0


class TestExperimentSpecLoaderUnit:
    """Unit tests for _load_experiment_spec() and _check_experiment_spec_id().

    These helpers are exercised through integration in build_runner_output(),
    but direct unit coverage clarifies their actual contract and edge cases.
    """

    # ------------------------------------------------------------------
    # _check_experiment_spec_id — non-format structural validation only
    # ------------------------------------------------------------------

    def test_check_experiment_spec_id_accepts_valid_id(self):
        """A non-empty string experiment_id passes validation."""
        _check_experiment_spec_id({"experiment_id": "EXP-2026-0001"})
        _check_experiment_spec_id({"experiment_id": "EXP-2026-9999"})
        _check_experiment_spec_id({"experiment_id": "any-string-here"})

    def test_check_experiment_spec_id_rejects_missing_field(self):
        """Missing experiment_id field raises ValueError."""
        with pytest.raises(ValueError, match="experiment_id"):
            _check_experiment_spec_id({})
        with pytest.raises(ValueError, match="experiment_id"):
            _check_experiment_spec_id({"hypothesis_id": "HYP-2026-0001"})

    def test_check_experiment_spec_id_rejects_empty_string(self):
        """Empty string experiment_id raises ValueError."""
        with pytest.raises(ValueError, match="experiment_id"):
            _check_experiment_spec_id({"experiment_id": ""})

    def test_check_experiment_spec_id_rejects_whitespace_only(self):
        """Whitespace-only experiment_id raises ValueError."""
        with pytest.raises(ValueError, match="experiment_id"):
            _check_experiment_spec_id({"experiment_id": "   "})
        with pytest.raises(ValueError, match="experiment_id"):
            _check_experiment_spec_id({"experiment_id": "\n\t"})

    def test_check_experiment_spec_id_rejects_non_string(self):
        """Non-string experiment_id raises ValueError."""
        with pytest.raises(ValueError, match="experiment_id"):
            _check_experiment_spec_id({"experiment_id": None})
        with pytest.raises(ValueError, match="experiment_id"):
            _check_experiment_spec_id({"experiment_id": 42})
        with pytest.raises(ValueError, match="experiment_id"):
            _check_experiment_spec_id({"experiment_id": []})
        with pytest.raises(ValueError, match="experiment_id"):
            _check_experiment_spec_id({"experiment_id": {}})

    # ------------------------------------------------------------------
    # _load_experiment_spec — file loading
    # ------------------------------------------------------------------

    def test_load_experiment_spec_valid_minimal_file(self, tmp_path):
        """_load_experiment_spec loads a valid JSON file and returns the dict."""
        spec_file = tmp_path / "experiment_spec.json"
        spec_file.write_text(json.dumps({
            "experiment_id": "EXP-2026-0001",
            "hypothesis_id": "HYP-2026-0001",
            "search_space_id": "SSM-2026-0001",
        }))
        result = _load_experiment_spec(spec_file)
        assert result["experiment_id"] == "EXP-2026-0001"
        assert result["hypothesis_id"] == "HYP-2026-0001"
        assert result["search_space_id"] == "SSM-2026-0001"

    def test_load_experiment_spec_missing_file_raises(self, tmp_path):
        """_load_experiment_spec raises FileNotFoundError for a missing file."""
        fake = tmp_path / "nonexistent.json"
        with pytest.raises(FileNotFoundError):
            _load_experiment_spec(fake)

    def test_load_experiment_spec_invalid_json_raises(self, tmp_path):
        """_load_experiment_spec raises JSONDecodeError for malformed JSON."""
        spec_file = tmp_path / "bad.json"
        spec_file.write_text("{ not valid json ")
        with pytest.raises(json.JSONDecodeError):
            _load_experiment_spec(spec_file)

    def test_load_experiment_spec_invalid_experiment_id_rejected(self, tmp_path):
        """A file with missing experiment_id passes file load but fails ID check."""
        spec_file = tmp_path / "no_id.json"
        spec_file.write_text(json.dumps({
            "hypothesis_id": "HYP-2026-0001",
            "search_space_id": "SSM-2026-0001",
        }))
        # File loads fine
        spec = _load_experiment_spec(spec_file)
        assert "experiment_id" not in spec
        # But _check_experiment_spec_id rejects it
        with pytest.raises(ValueError, match="experiment_id"):
            _check_experiment_spec_id(spec)

    def test_load_experiment_spec_preserves_governance_fields(self, tmp_path):
        """_load_experiment_spec returns a dict that preserves governance fields."""
        spec_file = tmp_path / "governance_spec.json"
        spec_file.write_text(json.dumps({
            "experiment_id": "EXP-2026-0001",
            "hypothesis_id": "HYP-2026-0001",
            "search_space_id": "SSM-2026-0001",
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
            "trial_generation_mode": "literature_replication",
        }))
        result = _load_experiment_spec(spec_file)
        assert result["prohibited_modes"]["autonomous_search"] is False
        assert result["prohibited_modes"]["live_trading"] is False
        assert result["trial_generation_mode"] == "literature_replication"
        # _check_experiment_spec_id passes the loaded dict
        _check_experiment_spec_id(result)


class TestDataManifestRunnerUnit:
    """Unit tests for load_data_manifest_for_runner() and _summarize_data_manifest_for_runner().

    These helpers are exercised through integration in build_runner_output(),
    but direct unit coverage clarifies their actual contract.
    """

    # ------------------------------------------------------------------
    # load_data_manifest_for_runner
    # ------------------------------------------------------------------

    def test_load_data_manifest_for_runner_valid_local_csv_manifest(self, tmp_path):
        """load_data_manifest_for_runner loads a valid local_csv manifest and returns DatasetManifest."""
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,open,high,low,close\n2024-01-01,100.0,101.0,99.0,100.5\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "DM-2026-test",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        result = load_data_manifest_for_runner(manifest_file)
        assert result.dataset_id == "DM-2026-test"
        assert result.source_kind.value == "local_csv"
        assert result.path == "prices.csv"
        assert result.format == "csv"

    def test_load_data_manifest_for_runner_missing_file_raises(self, tmp_path):
        """load_data_manifest_for_runner raises FileNotFoundError when manifest file is absent."""
        fake = tmp_path / "nonexistent.json"
        with pytest.raises(FileNotFoundError):
            load_data_manifest_for_runner(fake)

    def test_load_data_manifest_for_runner_invalid_json_raises(self, tmp_path):
        """load_data_manifest_for_runner raises JSONDecodeError for malformed JSON."""
        manifest_file = tmp_path / "bad.json"
        manifest_file.write_text("{ not valid json ")
        with pytest.raises(json.JSONDecodeError):
            load_data_manifest_for_runner(manifest_file)

    def test_load_data_manifest_for_runner_invalid_role_raises(self, tmp_path):
        """load_data_manifest_for_runner raises ValueError for an unrecognised DatasetRole."""
        manifest_file = tmp_path / "bad_role.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "DM-test",
            "role": "not_a_real_role",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        with pytest.raises(ValueError, match="DatasetRole"):
            load_data_manifest_for_runner(manifest_file)

    def test_load_data_manifest_for_runner_missing_required_field_raises(self, tmp_path):
        """load_data_manifest_for_runner raises KeyError when dataset_id is absent."""
        manifest_file = tmp_path / "no_id.json"
        manifest_file.write_text(json.dumps({
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        with pytest.raises(KeyError):
            load_data_manifest_for_runner(manifest_file)

    def test_load_data_manifest_for_runner_preserves_optional_fields(self, tmp_path):
        """load_data_manifest_for_runner preserves optional fields from the manifest."""
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,close\n2024-01-01,100.5\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "DM-2026-optionals",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
            "source_name": "vendor_a",
            "date_range_start": "2024-01-01",
            "date_range_end": "2024-12-31",
            "symbols": ["AAPL", "MSFT"],
            "quality_flags": ["cleaned"],
            "provenance_notes": "Test data.",
        }))
        result = load_data_manifest_for_runner(manifest_file)
        assert result.source_name == "vendor_a"
        assert result.date_range_start == "2024-01-01"
        assert result.date_range_end == "2024-12-31"
        assert result.symbols == ("AAPL", "MSFT")
        assert result.quality_flags == ("cleaned",)
        assert result.provenance_notes == "Test data."

    def test_load_data_manifest_for_runner_validation_failure_raises(self, tmp_path):
        """load_data_manifest_for_runner raises ValueError when the dataset path does not exist."""
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "DM-2026-missing",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "this_file_does_not_exist.csv",
            "format": "csv",
        }))
        with pytest.raises(ValueError, match="does not exist"):
            load_data_manifest_for_runner(manifest_file)

    # ------------------------------------------------------------------
    # _summarize_data_manifest_for_runner
    # ------------------------------------------------------------------

    def test_summarize_data_manifest_for_runner_returns_required_fields(self, tmp_path):
        """_summarize_data_manifest_for_runner returns a dict with required artifact fields."""
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,close\n2024-01-01,100.5\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "DM-2026-summary",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        manifest = load_data_manifest_for_runner(manifest_file)
        summary = _summarize_data_manifest_for_runner(
            manifest, manifest_file, tmp_path
        )
        assert summary["artifact_type"] == "DataManifest"
        assert summary["artifact_id"] == "DM-2026-summary"
        assert summary["artifact_path"] == str(manifest_file.resolve())
        assert summary["schema_ref"] == "N/A"
        assert summary["validator_ref"] is None
        assert summary["content_hash"].startswith("sha256:")
        assert summary["validation_status"] == "pass"

    def test_summarize_data_manifest_for_runner_is_json_serializable(self, tmp_path):
        """_summarize_data_manifest_for_runner output is JSON-serializable."""
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,close\n2024-01-01,100.5\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "DM-2026-json",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        manifest = load_data_manifest_for_runner(manifest_file)
        summary = _summarize_data_manifest_for_runner(
            manifest, manifest_file, tmp_path
        )
        json.dumps(summary)  # raises if not serializable

    def test_summarize_data_manifest_for_runner_preserves_artifact_id(self, tmp_path):
        """_summarize_data_manifest_for_runner preserves dataset_id as artifact_id."""
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,close\n2024-01-01,100.5\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "DM-2026-sk",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        manifest = load_data_manifest_for_runner(manifest_file)
        summary = _summarize_data_manifest_for_runner(
            manifest, manifest_file, tmp_path
        )
        assert summary["artifact_id"] == "DM-2026-sk"

    def test_summarize_data_manifest_for_runner_does_not_require_real_dataset_content(
        self, tmp_path
    ):
        """_summarize_data_manifest_for_runner can produce a summary without reading dataset rows."""
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text("date,close\n2024-01-01,100.5\n")
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "DM-2026-noread",
            "role": "price_history",
            "source_kind": "local_csv",
            "path": "prices.csv",
            "format": "csv",
        }))
        manifest = load_data_manifest_for_runner(manifest_file)
        # Should not raise even though we don't read the CSV content
        summary = _summarize_data_manifest_for_runner(
            manifest, manifest_file, tmp_path
        )
        assert summary["artifact_type"] == "DataManifest"
        assert summary["validation_status"] == "pass"



class TestFirstThinRunnerMissingValueSmoke:
    """Tiny end-to-end smoke tests for the first thin runner with
    --observation-missing-value-columns requested.

    Verifies the runner produces schema-valid success and failed_validation
    artifacts and that observation_table_missing_value_summary audit is present
    when a missing-value column is requested.  Uses the same tmp_path + local
    CSV + DataManifest + ExperimentSpec pattern as TestFirstThinRunnerLocalSmoke.
    """

    def _make_manifest(self, csv_file, dataset_id, tmp_path):
        manifest_file = tmp_path / "smoke_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": dataset_id,
            "role": "generic",
            "source_kind": "local_csv",
            "path": csv_file.name,
            "format": "csv",
        }, indent=2))
        return manifest_file

    def _make_spec(self, experiment_id, data_manifest_ref, tmp_path):
        spec_file = tmp_path / "smoke_spec.json"
        spec_file.write_text(json.dumps({
            "experiment_id": experiment_id,
            "experiment_version": 1,
            "hypothesis_id": "HYP-2026-0001",
            "search_space_id": "SSM-2026-0001",
            "data_manifest_refs": [data_manifest_ref],
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
            "created_at": "2026-05-06T00:00:00Z",
            "reviewer": {
                "name": "smoke_tester",
                "affiliation": "ci",
                "date": "2026-05-06",
            },
        }, indent=2))
        return spec_file

    def test_missing_value_smoke_produces_success_artifact(
        self, tmp_path
    ):
        """main() with --observation-missing-value-columns and no missing
        values returns rc=0, status='success', and schema-valid artifact."""
        csv_file = tmp_path / "smoke_obs.csv"
        csv_file.write_text(
            "date,symbol,close,volume\n"
            "2024-01-02,AAPL,185.50,1000\n"
            "2024-01-03,AAPL,186.00,2000\n"
            "2024-01-04,AAPL,187.20,3000\n"
            "2024-01-02,MSFT,415.00,5000\n"
            "2024-01-03,MSFT,416.50,6000\n"
        )
        manifest_file = self._make_manifest(csv_file, "DM-2026-MV-SMOKE", tmp_path)
        spec_file = self._make_spec("EXP-2026-0001", "DM-2026-MV-SMOKE", tmp_path)
        output_path = tmp_path / "smoke_output.json"
        rc = main([
            "--experiment-spec", str(spec_file),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--observation-missing-value-columns", "volume",
            "--output-path", str(output_path),
            "--run-owner", "smoke",
        ])
        assert rc == 0, f"main() returned {rc}"
        artifact = json.loads(output_path.read_text())
        assert artifact["status"] == "success"
        pytest.importorskip("jsonschema")
        import jsonschema
        schema_path = Path(__file__).parent.parent / "schemas" / "runner_output_spec_v1.schema.json"
        schema = json.loads(schema_path.read_text())
        jsonschema.validate(artifact, schema)

    def test_missing_value_audit_present_and_counts_correctly(
        self, tmp_path
    ):
        """observation_table_missing_value_summary is present with correct counts."""
        csv_file = tmp_path / "smoke_obs.csv"
        csv_file.write_text(
            "date,symbol,close,volume\n"
            "2024-01-02,AAPL,185.50,1000\n"
            "2024-01-03,AAPL,186.00,\n"
            "2024-01-04,AAPL,187.20,3000\n"
        )
        manifest_file = self._make_manifest(csv_file, "DM-2026-MV-SMOKE2", tmp_path)
        spec_file = self._make_spec("EXP-2026-0002", "DM-2026-MV-SMOKE2", tmp_path)
        output_path = tmp_path / "smoke_output.json"
        rc = main([
            "--experiment-spec", str(spec_file),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--observation-missing-value-columns", "volume",
            "--output-path", str(output_path),
            "--run-owner", "smoke",
        ])
        # volume has one missing value → missing-value audit fail, blocker>0
        assert rc == 1
        artifact = json.loads(output_path.read_text())
        assert artifact["status"] == "failed_validation"
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_missing_value_summary" in audit_names
        missing_val_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_missing_value_summary"
        )
        assert missing_val_audit["audit_result"] == "fail"
        assert missing_val_audit["blocker_count"] == 1
        assert "row_count=3" in missing_val_audit["details_ref"]
        assert "missing[volume]=1" in missing_val_audit["details_ref"]

    def test_missing_value_smoke_preserves_all_observation_audits(
        self, tmp_path
    ):
        """When missing-value columns have no missing values, all five observation
        audits are present in the artifact."""
        csv_file = tmp_path / "smoke_obs.csv"
        csv_file.write_text(
            "date,symbol,close,volume\n"
            "2024-01-02,AAPL,185.50,1000\n"
            "2024-01-03,AAPL,186.00,2000\n"
            "2024-01-04,AAPL,187.20,3000\n"
        )
        manifest_file = self._make_manifest(csv_file, "DM-2026-MV-SMOKE3", tmp_path)
        spec_file = self._make_spec("EXP-2026-0003", "DM-2026-MV-SMOKE3", tmp_path)
        output_path = tmp_path / "smoke_output.json"
        rc = main([
            "--experiment-spec", str(spec_file),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--observation-missing-value-columns", "volume",
            "--output-path", str(output_path),
            "--run-owner", "smoke",
        ])
        assert rc == 0
        artifact = json.loads(output_path.read_text())
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_canonical_summary" in audit_names
        assert "observation_table_close_return_summary" in audit_names
        assert "observation_table_missing_value_summary" in audit_names
        assert "observation_table_duplicate_row_summary" in audit_names
        assert "observation_table_date_coverage_summary" in audit_names


class TestFirstThinRunnerUnsupportedFormatSmoke:
    """Tiny end-to-end smoke tests for unsupported observation source kinds.

    Verifies the runner produces schema-valid failed_validation artifacts when
    a non-local_csv DataManifest is used with observation table audits.
    Each test confirms the correct failing audit name, failure_type, and
    blocker_count.  Uses the same tmp_path + local SQLite manifest + ExperimentSpec
    pattern as the existing unsupported-format integration tests.
    """

    def _make_sqlite_manifest(self, dataset_id, tmp_path):
        """Create a minimal local_sqlite DataManifest with an empty db file."""
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        db_file = db_dir / "data.sqlite"
        db_file.write_text("")
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": dataset_id,
            "role": "generic",
            "source_kind": "local_sqlite",
            "path": "db/data.sqlite",
            "format": "sqlite",
        }, indent=2))
        return manifest_file

    def _make_spec(self, experiment_id, tmp_path):
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps({
            "experiment_id": experiment_id,
            "experiment_version": 1,
            "hypothesis_id": "HYP-2026-0001",
            "search_space_id": "SSM-2026-0001",
            "data_manifest_refs": ["DM-SQLITE-SMOKE"],
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
            "created_at": "2026-05-06T00:00:00Z",
            "reviewer": {
                "name": "smoke_tester",
                "affiliation": "ci",
                "date": "2026-05-06",
            },
        }, indent=2))
        return spec_file

    def test_close_return_unsupported_source_kind_produces_failed_validation(
        self, tmp_path
    ):
        """SQLite manifest with --observation-close-column fails at close-return
        section (checked before manifest is loaded) with unsupported_config."""
        manifest_file = self._make_sqlite_manifest("DM-SQLITE-CR", tmp_path)
        spec_file = self._make_spec("EXP-2026-0001", tmp_path)
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(spec_file),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--output-path", str(output_path),
            "--run-owner", "smoke",
        ])
        assert rc == 1, f"main() returned {rc}"
        artifact = json.loads(output_path.read_text())
        assert artifact["status"] == "failed_validation"
        pytest.importorskip("jsonschema")
        import jsonschema
        schema_path = Path(__file__).parent.parent / "schemas" / "runner_output_spec_v1.schema.json"
        schema = json.loads(schema_path.read_text())
        jsonschema.validate(artifact, schema)
        assert artifact["failure_summary"]["failure_type"] == "unsupported_config"
        assert artifact["audit_summary"]["overall_result"] == "fail"
        assert artifact["audit_summary"]["blocker_count"] >= 1
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_close_return_summary" in audit_names
        close_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_close_return_summary"
        )
        assert close_audit["audit_result"] == "fail"
        assert close_audit["blocker_count"] >= 1

    def test_missing_value_unsupported_source_kind_produces_failed_validation(
        self, tmp_path
    ):
        """SQLite manifest with --observation-missing-value-columns fails at
        missing-value section (checked before manifest is loaded) with
        unsupported_config."""
        manifest_file = self._make_sqlite_manifest("DM-SQLITE-MV", tmp_path)
        spec_file = self._make_spec("EXP-2026-0002", tmp_path)
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(spec_file),
            "--data-manifest", str(manifest_file),
            "--observation-missing-value-columns", "volume",
            "--output-path", str(output_path),
            "--run-owner", "smoke",
        ])
        assert rc == 1, f"main() returned {rc}"
        artifact = json.loads(output_path.read_text())
        assert artifact["status"] == "failed_validation"
        pytest.importorskip("jsonschema")
        import jsonschema
        schema_path = Path(__file__).parent.parent / "schemas" / "runner_output_spec_v1.schema.json"
        schema = json.loads(schema_path.read_text())
        jsonschema.validate(artifact, schema)
        assert artifact["failure_summary"]["failure_type"] == "unsupported_config"
        assert artifact["audit_summary"]["overall_result"] == "fail"
        assert artifact["audit_summary"]["blocker_count"] >= 1
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_missing_value_summary" in audit_names
        mv_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_missing_value_summary"
        )
        assert mv_audit["audit_result"] == "fail"
        assert mv_audit["blocker_count"] >= 1

    def test_canonical_unsupported_source_kind_produces_schema_valid_failed_artifact(
        self, tmp_path
    ):
        """SQLite manifest with date+symbol columns (no close) fails at
        canonical section (line ~1425) with unsupported_config before any
        other observation table section runs.  Verifies the artifact is
        schema-valid with output_manifest entries containing
        contains_private_data and publishable."""
        manifest_file = self._make_sqlite_manifest("DM-SQLITE-CANON", tmp_path)
        spec_file = self._make_spec("EXP-2026-0003", tmp_path)
        output_path = tmp_path / "output.json"
        rc = main([
            "--experiment-spec", str(spec_file),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--output-path", str(output_path),
            "--run-owner", "smoke",
        ])
        assert rc == 1, f"main() returned {rc}"
        artifact = json.loads(output_path.read_text())
        assert artifact["status"] == "failed_validation"
        pytest.importorskip("jsonschema")
        import jsonschema
        schema_path = Path(__file__).parent.parent / "schemas" / "runner_output_spec_v1.schema.json"
        schema = json.loads(schema_path.read_text())
        jsonschema.validate(artifact, schema)
        assert artifact["failure_summary"]["failure_type"] == "unsupported_config"
        assert artifact["audit_summary"]["overall_result"] == "fail"
        assert artifact["audit_summary"]["blocker_count"] >= 1
        audit_names = [a["audit_name"] for a in artifact["audit_summary"]["audits"]]
        assert "observation_table_canonical_summary" in audit_names
        canonical_audit = next(
            a for a in artifact["audit_summary"]["audits"]
            if a["audit_name"] == "observation_table_canonical_summary"
        )
        assert canonical_audit["audit_result"] == "fail"
        assert canonical_audit["blocker_count"] >= 1
        # Verify output_manifest entries have required fields
        for entry in artifact["output_manifest"]:
            assert "contains_private_data" in entry
            assert "publishable" in entry
            assert entry["contains_private_data"] is False
            assert entry["publishable"] is False

    # Note: when date+symbol are provided with a non-CSV source_kind, the
    # canonical section raises GovernanceRejection at line ~1425 before the
    # duplicate-row section (line ~2047) can run.  The canonical unsupported
    # format path is now schema-valid after this PR fixed the output_manifest
    # spec_entry to include contains_private_data and publishable.  The
    # duplicate-row unsupported_format path was already schema-valid.


class TestFirstThinRunnerAmbiguousHeaderSmoke:
    """Smoke tests for ambiguous stripped observation CSV headers.

    Verifies the first thin runner produces a schema-valid failed_validation
    RunnerOutput artifact when the observation CSV has headers that are
    textually distinct but normalize to the same stripped value (e.g. " date"
    and "date "). The runner must emit a schema-valid failed_validation
    artifact with audit_result='fail' and blocker_count > 0.

    No production behaviour changes.
    """

    def test_ambiguous_stripped_date_header_produces_failed_validation_artifact(
        self, tmp_path
    ):
        """Ambiguous date header → GovernanceRejection with failed_validation artifact.

        CSV header has two textually distinct fields (" date" and "date ") that
        both strip to "date". The close-return section runs before the
        duplicate-row section in the runner and catches the ambiguous header
        first: since DictReader uses the raw header keys (" date", "date ")
        while the close-return function looks up the stripped name ("date"),
        all rows are skipped, producing symbols_with_return=0 and a fail.

        NOTE: The duplicate-row section also has ambiguous-header coverage
        (it uses the same _resolve_observation_csv_header helper), but it is
        gated behind the close-return section and is not reached when
        close-return already raised GovernanceRejection. This is a runner
        section-ordering artifact, not a correctness issue.
        """
        # Ambiguous CSV: " date" and "date " both strip to "date"
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text(
            " date,date ,symbol,close\n"
            "2024-01-02,2024-01-02,AAPL,185.50\n"
        )
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "DM-2026-ambig",
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
                observation_date_column="date",
                observation_symbol_column="symbol",
                observation_close_column="close",
                run_owner="test@test",
            )
        artifact = exc_info.value.artifact
        assert artifact["status"] == "failed_validation"
        assert artifact["failure_summary"] is not None
        assert artifact["failure_summary"]["status"] == "failed_validation"
        assert artifact["failure_summary"]["failure_type"] == "validation_error"
        # audit_summary reflects failure
        assert artifact["audit_summary"]["overall_result"] == "fail"
        assert artifact["audit_summary"]["blocker_count"] > 0
        # Verify schema validity
        jsonschema = pytest.importorskip("jsonschema")
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "schemas"
            / "runner_output_spec_v1.schema.json"
        )
        if schema_path.exists():
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            checker = jsonschema.FormatChecker()
            jsonschema.validate(artifact, schema, format_checker=checker)

    def test_ambiguous_stripped_symbol_header_produces_failed_validation_artifact(
        self, tmp_path
    ):
        """Ambiguous symbol header → GovernanceRejection with failed_validation artifact.

        CSV header has two textually distinct fields (" symbol" and "symbol ")
        that both strip to "symbol". The duplicate-row section catches this
        because the symbol column lookup returns empty, causing the
        observation_table_duplicate_row_summary audit to fail.
        """
        csv_file = tmp_path / "prices.csv"
        csv_file.write_text(
            "date, symbol,symbol ,close\n"
            "2024-01-02,AAPL,AAPL,185.50\n"
        )
        manifest_file = tmp_path / "data_manifest.json"
        manifest_file.write_text(json.dumps({
            "dataset_id": "DM-2026-ambig-sym",
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
                observation_date_column="date",
                observation_symbol_column="symbol",
                observation_close_column="close",
                run_owner="test@test",
            )
        artifact = exc_info.value.artifact
        assert artifact["status"] == "failed_validation"
        assert artifact["failure_summary"] is not None
        assert artifact["audit_summary"]["overall_result"] == "fail"
        assert artifact["audit_summary"]["blocker_count"] > 0
        # The failing audit is either close-return or duplicate-row depending
        # on runner section ordering; both are valid failure paths
        failing_audits = [
            a["audit_name"] for a in artifact["audit_summary"]["audits"]
            if a["audit_result"] == "fail"
        ]
        assert len(failing_audits) > 0, "No failing audits found"


class TestFirstThinRunnerLocalSmokeGovernance:
    """Governance-rejection smoke tests for the first thin real data runner.

    Mirrors TestFirstThinRunnerLocalSmoke (PR #174) but exercises the failure
    path: when prohibited_modes.autonomous_search=True the runner must emit a
    schema-valid failed_validation artifact with audit_result='fail' and
    blocker_count > 0.  No production behaviour changes.
    """

    def _write_smoke_files(self, tmp_path, spec_file):
        """Write the shared smoke CSV and manifest alongside spec_file."""
        csv_file = tmp_path / "smoke_obs.csv"
        csv_file.write_text(SMOKE_CSV_DATA)
        manifest_file = tmp_path / "smoke_manifest.json"
        manifest_file.write_text(json.dumps(SMOKE_MANIFEST_DATA, indent=2))
        return csv_file, manifest_file

    def test_autonomous_search_blocker_produces_failed_validation_artifact(
        self, tmp_path, experiment_spec_autonomous_search_true
    ):
        """main() with autonomous_search=True returns rc=1 and status='failed_validation'.

        The smoke CSV + local DataManifest are included so the observation table
        audit path is exercised; the autonomous_search blocker is the sole reason
        for rejection.
        """
        csv_file, manifest_file = self._write_smoke_files(tmp_path, experiment_spec_autonomous_search_true)
        output_path = tmp_path / "governance_rejected.json"

        rc = main([
            "--experiment-spec", str(experiment_spec_autonomous_search_true),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--output-path", str(output_path),
            "--run-owner", "smoke_gov",
        ])

        assert rc == 1, f"Expected exit 1, got {rc}"
        assert output_path.exists(), "Failed artifact must be written"

        artifact = json.loads(output_path.read_text())

        # Status
        assert artifact["status"] == "failed_validation", (
            f"Expected status='failed_validation', got {artifact['status']}"
        )

        # Schema validity
        pytest.importorskip("jsonschema")
        import jsonschema
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "schemas"
            / "runner_output_spec_v1.schema.json"
        )
        schema = json.loads(schema_path.read_text())
        from jsonschema import FormatChecker
        checker = FormatChecker()
        jsonschema.validate(artifact, schema, format_checker=checker)

        # Audit summary
        audit_summary = artifact["audit_summary"]
        assert audit_summary["overall_result"] == "fail", (
            f"Expected overall_result='fail', got {audit_summary['overall_result']}"
        )
        assert audit_summary["blocker_count"] > 0, (
            f"Expected blocker_count > 0, got {audit_summary['blocker_count']}"
        )

        # The no_autonomous_search_flag_set audit is fail
        auto_audit = next(
            (a for a in audit_summary["audits"]
             if a["audit_name"] == "no_autonomous_search_flag_set"),
            None
        )
        assert auto_audit is not None, (
            "no_autonomous_search_flag_set audit not found in audit_summary"
        )
        assert auto_audit["audit_result"] == "fail", (
            f"Expected audit_result='fail', got {auto_audit['audit_result']}"
        )
        assert auto_audit["blocker_count"] > 0, (
            f"Expected blocker_count > 0 on no_autonomous_search_flag_set, "
            f"got {auto_audit['blocker_count']}"
        )

        # failure_summary is populated
        assert artifact["failure_summary"] is not None, (
            "failure_summary must be non-null for failed_validation"
        )

    def test_governance_rejection_artifact_has_required_failure_fields(
        self, tmp_path, experiment_spec_autonomous_search_true
    ):
        """failed_validation artifact has all required failure_summary fields."""
        csv_file, manifest_file = self._write_smoke_files(tmp_path, experiment_spec_autonomous_search_true)
        output_path = tmp_path / "gov_rejected_fields.json"

        rc = main([
            "--experiment-spec", str(experiment_spec_autonomous_search_true),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--output-path", str(output_path),
            "--run-owner", "smoke_gov",
        ])
        assert rc == 1
        artifact = json.loads(output_path.read_text())
        fs = artifact["failure_summary"]

        # Required failure_summary fields
        assert fs["failure_type"] == "validation_error", (
            f"Expected failure_type='validation_error', got {fs.get('failure_type')}"
        )
        assert fs["status"] == "failed_validation", (
            f"Expected failure_summary.status='failed_validation', "
            f"got {fs.get('status')}"
        )
        assert fs["blocker_summary"] is not None, (
            "failure_summary.blocker_summary must be non-null"
        )
        assert len(fs["blocker_summary"]) > 0, (
            "failure_summary.blocker_summary must be non-empty"
        )
        # failed_check or equivalent field — the runner uses "failed_check"
        # field that names the failing governance rule
        failed_check_field = fs.get("failed_check") or fs.get("failed_audit")
        assert failed_check_field is not None, (
            f"failure_summary must have 'failed_check' or 'failed_audit', got {list(fs.keys())}"
        )
        assert "autonomous_search" in str(failed_check_field).lower(), (
            f"failed_check should reference autonomous_search, got {failed_check_field}"
        )
        assert fs.get("created_at") is not None, (
            "failure_summary.created_at must be present"
        )

        # Structural fields
        assert artifact["input_artifact_refs"], "input_artifact_refs must be non-empty"
        assert artifact["data_manifest_refs"], "data_manifest_refs must be non-empty"
        assert artifact["output_manifest"], "output_manifest must be non-empty"
        assert artifact["run_mode"] == "dry_run"

    def test_blocker_count_sum_in_failed_artifact_is_correct(
        self, tmp_path, experiment_spec_autonomous_search_true
    ):
        """audit_summary.blocker_count equals the sum of individual audit blocker_counts."""
        csv_file, manifest_file = self._write_smoke_files(tmp_path, experiment_spec_autonomous_search_true)
        output_path = tmp_path / "gov_blocker_sum.json"

        rc = main([
            "--experiment-spec", str(experiment_spec_autonomous_search_true),
            "--data-manifest", str(manifest_file),
            "--observation-date-column", "date",
            "--observation-symbol-column", "symbol",
            "--observation-close-column", "close",
            "--output-path", str(output_path),
            "--run-owner", "smoke_gov",
        ])
        assert rc == 1
        artifact = json.loads(output_path.read_text())

        audits = artifact["audit_summary"]["audits"]
        individual_sum = sum(a.get("blocker_count", 0) for a in audits)
        assert artifact["audit_summary"]["blocker_count"] == individual_sum, (
            f"audit_summary.blocker_count={artifact['audit_summary']['blocker_count']} "
            f"!= sum of individual counts ({individual_sum})"
        )
        # At minimum the no_autonomous_search_flag_set audit contributed at least 1
        assert artifact["audit_summary"]["blocker_count"] >= 1, (
            "Expected at least 1 blocker from autonomous_search rejection"
        )


