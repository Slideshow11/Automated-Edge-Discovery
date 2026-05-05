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
