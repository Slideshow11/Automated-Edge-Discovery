"""Tests for RunnerOutputSpec v1 local validator."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "local" / "validate_runner_output_spec.py"
FIXTURES = ROOT / "fixtures" / "runner_output_spec_v1"
VALID_FIXTURES = [
    FIXTURES / "valid_success_minimal.json",
    FIXTURES / "valid_failed_validation_minimal.json",
    FIXTURES / "valid_partial_minimal.json",
    FIXTURES / "valid_failed_missing_data_minimal.json",
]
ALL_FIXTURES = sorted(FIXTURES.glob("*.json"))


class TestRunnerOutputSpecValidFixtures:
    """Valid fixtures must pass (exit 0)."""

    @pytest.mark.parametrize("fixture", VALID_FIXTURES, ids=lambda f: f.name)
    def test_valid_fixture_exits_zero(self, fixture):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(fixture)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"valid fixture {fixture.name} failed: {result.stderr}"


class TestRunnerOutputSpecInvalidFixtures:
    """Invalid fixtures must fail (exit 1)."""

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.name)
    def test_invalid_fixture_exits_nonzero(self, fixture):
        if fixture in VALID_FIXTURES:
            pytest.skip("valid fixture")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(fixture)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, f"invalid fixture {fixture.name} should have failed: stdout={result.stdout}"


class TestRunnerOutputSpecCliErrors:
    """CLI errors must exit 2."""

    def test_missing_file_exits_two(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(tmp_path / "nonexistent.json")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "ERROR" in result.stderr
        assert "not found" in result.stderr

    def test_invalid_json_exits_two(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(bad)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "ERROR" in result.stderr
        assert "invalid JSON" in result.stderr


class TestRunnerOutputSpecJsonOutput:
    """--format json produces parseable JSON output."""

    def test_valid_fixture_json_output_parseable(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(VALID_FIXTURES[0]), "--format", "json"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert "file" in parsed
        assert "blockers" in parsed
        assert isinstance(parsed["blockers"], list)

    def test_invalid_fixture_json_output_parseable(self):
        # Find an invalid fixture
        invalid = next(f for f in ALL_FIXTURES if f not in VALID_FIXTURES)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(invalid), "--format", "json"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        parsed = json.loads(result.stdout)
        assert "file" in parsed
        assert "blockers" in parsed
        assert len(parsed["blockers"]) > 0


class TestRunnerOutputSpecSchemaEnums:
    """Schema enum fields are enforced."""

    def test_invalid_status_enum(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["status"] = "completed"  # not in enum
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "status" in result.stdout or "invalid_enum" in result.stdout

    def test_invalid_run_mode_enum(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["run_mode"] = "live"  # not in enum
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "run_mode" in result.stdout or "invalid_enum" in result.stdout

    def test_invalid_runner_output_version(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["runner_output_version"] = "2.0"  # must be "1.0"
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "runner_output_version" in result.stdout or "invalid_const" in result.stdout


class TestRunnerOutputSpecRequiredFields:
    """Required fields are enforced."""

    @pytest.mark.parametrize(
        "field,value",
        [
            ("runner_output_id", None),
            ("runner_output_version", None),
            ("run_id", None),
            ("run_mode", None),
            ("status", None),
            ("runner_name", None),
            ("runner_version", None),
            ("experiment_spec_ref", None),
            ("input_artifact_refs", None),
            ("data_manifest_refs", None),
            ("run_config_hash", None),
            ("started_at", None),
            ("audit_summary", None),
            ("output_manifest", None),
            ("created_at", None),
            ("run_owner", None),
        ],
    )
    def test_missing_required_field(self, field, value, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        del artifact[field]
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert field in result.stdout or "missing_required_field" in result.stdout


class TestRunnerOutputSpecMinItems:
    """minItems constraints are enforced."""

    def test_empty_input_artifact_refs(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["input_artifact_refs"] = []
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "input_artifact_refs" in result.stdout or "min_items" in result.stdout

    def test_empty_output_manifest(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["output_manifest"] = []
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "output_manifest" in result.stdout or "min_items" in result.stdout

    def test_empty_data_manifest_refs(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["data_manifest_refs"] = []
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "data_manifest_refs" in result.stdout or "min_items" in result.stdout

    def test_empty_audits(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["audit_summary"]["audits"] = []
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "audits" in result.stdout or "min_items" in result.stdout


class TestRunnerOutputSpecFailureSummary:
    """failure_summary consistency with status is enforced."""

    def test_failed_validation_requires_failure_summary(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["status"] = "failed_validation"
        artifact["failure_summary"] = None
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "failure_summary" in result.stdout or "missing_required_field" in result.stdout

    def test_success_cannot_have_failure_summary(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["status"] = "success"
        artifact["failure_summary"] = {"failure_type": "validation_error", "status": "success", "blocker_summary": "x", "created_at": "2026-01-01T00:00:00Z"}
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "failure_summary" in result.stdout or "unexpected_field" in result.stdout

    def test_failed_validation_missing_failure_type(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["status"] = "failed_validation"
        artifact["failure_summary"] = {
            "status": "failed_validation",
            "blocker_summary": "x",
            "created_at": "2026-01-01T00:00:00Z",
        }
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "failure_type" in result.stdout or "missing_required_field" in result.stdout

    def test_partial_requires_partial_summary(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["status"] = "partial"
        artifact["partial_summary"] = None
        artifact["failure_summary"] = None
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "partial_summary" in result.stdout or "missing_required_field" in result.stdout

    def test_success_cannot_have_partial_summary(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["status"] = "success"
        artifact["partial_summary"] = {"partial_reason": "x", "completed_stages": [], "incomplete_stages": [], "affected_outputs": []}
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "partial_summary" in result.stdout or "unexpected_field" in result.stdout


class TestRunnerOutputSpecIdFormats:
    """ID format patterns are enforced."""

    def test_invalid_runner_output_id(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["runner_output_id"] = "RUN-PA-0001"
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "runner_output_id" in result.stdout or "invalid_id_format" in result.stdout

    def test_invalid_experiment_spec_ref(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["experiment_spec_ref"] = "EXP-PA-0001"
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "experiment_spec_ref" in result.stdout or "invalid_ref_format" in result.stdout


class TestRunnerOutputSpecDatetime:
    """ISO8601 datetime fields are validated."""

    def test_invalid_started_at(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["started_at"] = "not-a-datetime"
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "started_at" in result.stdout or "invalid_datetime" in result.stdout

    def test_valid_iso8601_with_z_suffix(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["started_at"] = "2026-01-01T00:00:00Z"
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_valid_iso8601_with_offset(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["started_at"] = "2026-01-01T00:00:00+00:00"
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0


class TestRunnerOutputSpecOutputManifest:
    """output_manifest items are validated."""

    def test_missing_output_role(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        del artifact["output_manifest"][0]["output_role"]
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "output_role" in result.stdout or "missing_required_field" in result.stdout

    def test_invalid_output_role(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["output_manifest"][0]["output_role"] = "live_trade"
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "output_role" in result.stdout or "invalid_enum" in result.stdout


class TestRunnerOutputSpecAuditSummary:
    """audit_summary structure is validated."""

    def test_missing_audit_name(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        del artifact["audit_summary"]["audits"][0]["audit_name"]
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "audit_name" in result.stdout or "missing_required_field" in result.stdout

    def test_invalid_audit_result(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["audit_summary"]["audits"][0]["audit_result"] = "invalid_result"
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "audit_result" in result.stdout or "invalid_enum" in result.stdout

    def test_invalid_severity(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["audit_summary"]["audits"][0]["severity"] = "critical"
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "severity" in result.stdout or "invalid_enum" in result.stdout


class TestRunnerOutputSpecGovernance:
    """Governance constraints are enforced."""

    def test_audit_result_pass_allows_zero_blockers(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["audit_summary"]["overall_result"] = "pass"
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0


class TestRunnerOutputSpecRefArrays:
    """Typed reference arrays are validated."""

    def test_invalid_outcome_spec_ref_format(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["outcome_spec_refs"] = ["OUT-PA-0001"]
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "outcome_spec_refs" in result.stdout or "invalid_ref_format" in result.stdout

    def test_valid_outcome_spec_ref(self, tmp_path):
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["outcome_spec_refs"] = ["OUT-2026-0001"]
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0


class TestRunnerOutputSpecNullableCompletedAt:
    """P1 #1: completed_at is nullable — null is valid, missing is invalid, date-only is invalid."""

    def test_completed_at_null_is_valid(self, tmp_path):
        """completed_at: null must be accepted (schema type: ['string', 'null'])."""
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["completed_at"] = None
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"completed_at: null should be valid: {result.stdout}"

    def test_completed_at_missing_is_invalid(self, tmp_path):
        """completed_at key must be present even though null value is allowed."""
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        del artifact["completed_at"]
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "completed_at" in result.stdout or "missing_required_field" in result.stdout

    def test_completed_at_date_only_is_invalid(self, tmp_path):
        """completed_at with date-only string (no time component) must be rejected."""
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["completed_at"] = "2026-01-01"
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "completed_at" in result.stdout or "invalid_datetime" in result.stdout


class TestRunnerOutputSpecAuditSummaryAdditionalProperties:
    """P1 #2: audit_summary and audits[] items enforce additionalProperties: false."""

    def test_extra_field_in_audit_summary_fails(self, tmp_path):
        """Extra field inside audit_summary must be rejected (additionalProperties: false)."""
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["audit_summary"]["extra_governance_field"] = "invalid"
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "unknown_field" in result.stdout or "audit_summary" in result.stdout

    def test_extra_field_in_audits_item_fails(self, tmp_path):
        """Extra field inside audits[] item must be rejected (additionalProperties: false)."""
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["audit_summary"]["audits"][0]["extra_audit_field"] = "invalid"
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "unknown_field" in result.stdout or "audits[0]" in result.stdout


class TestRunnerOutputSpecFailureSummaryAdditionalProperties:
    """P1 #3: failure_summary enforces additionalProperties: false."""

    def test_extra_field_in_failure_summary_fails(self, tmp_path):
        """Extra field inside failure_summary must be rejected (additionalProperties: false)."""
        artifact = json.loads(VALID_FIXTURES[0].read_text())
        artifact["status"] = "failed_validation"
        artifact["failure_summary"] = {
            "failure_type": "validation_error",
            "status": "failed_validation",
            "blocker_summary": "test failure",
            "created_at": "2026-01-01T00:00:00Z",
            "extra_failure_field": "invalid",
        }
        p = tmp_path / "output.json"
        p.write_text(json.dumps(artifact))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "unknown_field" in result.stdout or "failure_summary" in result.stdout
