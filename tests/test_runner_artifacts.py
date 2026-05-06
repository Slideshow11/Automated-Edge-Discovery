"""
Unit tests for engine.edge_discovery.runners.runner_artifacts.

Scope: exception classes and failure_summary builder.
No runner orchestration, no registry writes, no ledger writes, no live trading.
"""
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.edge_discovery.runners.runner_artifacts import (
    GovernanceRejection,
    UnsupportedConfig,
    _build_failure_summary,
)


# ---------------------------------------------------------------------------
# GovernanceRejection
# ---------------------------------------------------------------------------

class TestGovernanceRejection:
    def test_stores_artifact(self):
        artifact = {"run_id": "RUN-2026-0001", "status": "failed_validation"}
        exc = GovernanceRejection(artifact, "governance validation failed")
        assert exc.artifact is artifact
        assert exc.artifact["run_id"] == "RUN-2026-0001"
        assert exc.artifact["status"] == "failed_validation"

    def test_message_accessible(self):
        artifact = {"run_id": "RUN-2026-0001"}
        exc = GovernanceRejection(artifact, "blocker found: autonomous_search")
        assert "blocker found" in str(exc)
        assert exc.message == "blocker found: autonomous_search"

    def test_catchable_as_exception(self):
        artifact = {"run_id": "RUN-2026-0001"}
        with pytest.raises(GovernanceRejection) as rec:
            raise GovernanceRejection(artifact, "test")
        assert rec.value.artifact == artifact


# ---------------------------------------------------------------------------
# UnsupportedConfig
# ---------------------------------------------------------------------------

class TestUnsupportedConfig:
    def test_message_accessible(self):
        exc = UnsupportedConfig("CSV format not supported")
        assert "CSV format not supported" in str(exc)
        assert exc.message == "CSV format not supported"

    def test_catchable_as_exception(self):
        with pytest.raises(UnsupportedConfig) as rec:
            raise UnsupportedConfig("test unsupported config")
        assert "test unsupported config" in str(rec.value)

    def test_does_not_subclass_valueerror(self):
        """UnsupportedConfig must NOT accidentally subclass ValueError."""
        exc = UnsupportedConfig("test")
        assert not isinstance(exc, ValueError)


# ---------------------------------------------------------------------------
# _build_failure_summary
# ---------------------------------------------------------------------------

class TestBuildFailureSummary:
    def _make_audit_summary(self, audits: list[dict], blocker_count: int) -> dict:
        return {"audits": audits, "blocker_count": blocker_count}

    def test_returns_validation_error_failure_type(self):
        audit_summary = self._make_audit_summary(
            [{"audit_name": "no_autonomous_search_flag_set", "audit_result": "fail"}],
            blocker_count=1,
        )
        result = _build_failure_summary(audit_summary, "failed_validation")
        assert result["failure_type"] == "validation_error"

    def test_status_passed_through(self):
        audit_summary = self._make_audit_summary([], blocker_count=0)
        result = _build_failure_summary(audit_summary, "failed_runtime")
        assert result["status"] == "failed_runtime"

    def test_failed_check_contains_failing_audit_names(self):
        audit_summary = self._make_audit_summary(
            [
                {"audit_name": "no_autonomous_search_flag_set", "audit_result": "fail"},
                {"audit_name": "schema_validation_all_inputs", "audit_result": "fail"},
            ],
            blocker_count=2,
        )
        result = _build_failure_summary(audit_summary, "failed_validation")
        assert "no_autonomous_search_flag_set" in result["failed_check"]
        assert "schema_validation_all_inputs" in result["failed_check"]

    def test_blocker_summary_contains_audit_names(self):
        audit_summary = self._make_audit_summary(
            [{"audit_name": "no_registry_mutation", "audit_result": "fail"}],
            blocker_count=1,
        )
        result = _build_failure_summary(audit_summary, "failed_validation")
        assert "no_registry_mutation" in result["blocker_summary"]
        assert "Total blockers: 1" in result["blocker_summary"]

    def test_observation_missing_columns_appended(self):
        audit_summary = self._make_audit_summary(
            [{"audit_name": "schema_validation_all_inputs", "audit_result": "fail"}],
            blocker_count=1,
        )
        result = _build_failure_summary(
            audit_summary,
            "failed_validation",
            observation_missing_columns=["obs_date", "obs_symbol"],
        )
        assert "obs_date" in result["blocker_summary"]
        assert "obs_symbol" in result["blocker_summary"]
        assert "Missing observation-table columns" in result["blocker_summary"]

    def test_created_at_present_and_non_empty(self):
        audit_summary = self._make_audit_summary([], blocker_count=0)
        result = _build_failure_summary(audit_summary, "failed_validation")
        assert "created_at" in result
        assert result["created_at"] != ""
        assert result["created_at"] is not None

    def test_created_at_iso_format(self):
        audit_summary = self._make_audit_summary([], blocker_count=0)
        result = _build_failure_summary(audit_summary, "failed_validation")
        # ISO 8601 UTC: YYYY-MM-DDTHH:MM:SSZ
        assert result["created_at"].endswith("Z")
        assert len(result["created_at"]) == 20  # YYYY-MM-DDTHH:MM:SSZ = 20 chars

    def test_empty_blocker_list(self):
        audit_summary = self._make_audit_summary([], blocker_count=0)
        result = _build_failure_summary(audit_summary, "failed_validation")
        assert result["failure_type"] == "validation_error"
        assert result["failed_check"] is None
        assert "Total blockers: 0" in result["blocker_summary"]

    def test_only_schema_expected_keys(self):
        """Output should use only schema-expected keys."""
        audit_summary = self._make_audit_summary([], blocker_count=0)
        result = _build_failure_summary(audit_summary, "failed_validation")
        expected_keys = {
            "failure_type",
            "status",
            "failed_check",
            "blocker_summary",
            "missing_data_summary_ref",
            "details_ref",
            "created_at",
        }
        assert set(result.keys()) == expected_keys

    def test_missing_data_summary_ref_is_none(self):
        audit_summary = self._make_audit_summary([], blocker_count=0)
        result = _build_failure_summary(audit_summary, "failed_validation")
        assert result["missing_data_summary_ref"] is None

    def test_details_ref_is_none(self):
        audit_summary = self._make_audit_summary([], blocker_count=0)
        result = _build_failure_summary(audit_summary, "failed_validation")
        assert result["details_ref"] is None

    def test_unsupported_config_via_runner_artifact(self, tmp_path):
        """unsupported_config failure_type comes from the runner's non-CSV path.

        The _build_failure_summary helper only produces 'validation_error'.
        unsupported_config is produced by the runner when it encounters a
        non-CSV DataManifest (source_kind != local_csv). This test verifies
        the runner produces a schema-compatible artifact for that path.
        """
        import json
        from pathlib import Path
        from engine.edge_discovery.runners.first_thin_real_data_runner import (
            build_runner_output,
            GovernanceRejection,
        )
        # Create a minimal experiment spec
        spec_file = tmp_path / "experiment_spec.json"
        spec_file.write_text(json.dumps({
            "experiment_id": "EXP-2026-0001",
            "experiment_version": 1,
            "hypothesis_id": "HYP-2026-0001",
            "search_space_id": "SSM-2026-0001",
            "data_manifest_refs": ["DM-PARQUET"],
            "study_type": "options_event_risk",
        }))
        # Create a parquet manifest (non-CSV) with close-return requested
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
                experiment_spec_path=spec_file,
                data_manifest_path=manifest_file,
                observation_date_column="date",
                observation_symbol_column="symbol",
                observation_close_column="close",
                run_owner="test",
            )
        artifact = exc_info.value.artifact
        assert artifact["failure_summary"]["failure_type"] == "unsupported_config"
        assert artifact["failure_summary"]["status"] == "failed_validation"
        # jsonschema validation if available
        jsonschema = pytest.importorskip("jsonschema")
        schema_path = (
            Path(__file__).parent.parent
            / "schemas"
            / "runner_output_spec_v1.schema.json"
        )
        jsonschema.validate(artifact, json.loads(schema_path.read_text()))

    def test_blocker_summary_is_always_string(self):
        """blocker_summary is a string even with empty or single-item audit list."""
        # Empty audits → deterministic string
        audit_summary_empty = self._make_audit_summary([], blocker_count=0)
        result_empty = _build_failure_summary(audit_summary_empty, "failed_validation")
        assert isinstance(result_empty["blocker_summary"], str)
        assert len(result_empty["blocker_summary"]) > 0

        # Single failing audit
        single_fail = self._make_audit_summary(
            [{"audit_name": "test_audit", "audit_result": "fail", "blocker_count": 1}],
            blocker_count=1
        )
        result_single = _build_failure_summary(single_fail, "failed_validation")
        assert isinstance(result_single["blocker_summary"], str)
        assert "test_audit" in result_single["blocker_summary"]

    def test_failed_check_from_audits(self):
        """failed_check is constructed from failing audit names, not a caller-supplied value.

        The _build_failure_summary function does not accept a failed_check argument.
        It derives failed_check from the audit_summary's failing audit names.
        """
        failing_audits = [
            {"audit_name": "test_audit_one", "audit_result": "fail", "blocker_count": 1},
            {"audit_name": "test_audit_two", "audit_result": "fail", "blocker_count": 1},
        ]
        audit_summary = self._make_audit_summary(failing_audits, blocker_count=2)
        result = _build_failure_summary(audit_summary, "failed_validation")
        # failed_check is derived from failing audit names
        assert isinstance(result["failed_check"], str)
        assert len(result["failed_check"]) > 0
        assert "test_audit_one" in result["failed_check"]
        assert "test_audit_two" in result["failed_check"]

    def test_schema_jsonschema_validation_when_available(self):
        """_build_failure_summary output validates against failure_summary sub-schema."""
        jsonschema = pytest.importorskip("jsonschema")
        schema_path = (
            Path(__file__).parent.parent
            / "schemas"
            / "runner_output_spec_v1.schema.json"
        )
        schema = json.loads(schema_path.read_text())

        audit_summary = self._make_audit_summary([], blocker_count=0)
        result = _build_failure_summary(audit_summary, "failed_validation")
        # Extract the failure_summary sub-schema for validation
        failure_summary_schema = schema["properties"]["failure_summary"]
        jsonschema.validate(result, failure_summary_schema)


# ---------------------------------------------------------------------------
# trial_accounting_summary schema
# ---------------------------------------------------------------------------


class TestTrialAccountingSummarySchema:
    """Tests for the optional trial_accounting_summary field in RunnerOutput."""

    SCHEMA_PATH = (
        Path(__file__).parent.parent
        / "schemas"
        / "runner_output_spec_v1.schema.json"
    )

    @pytest.fixture
    def schema(self):
        return json.loads(self.SCHEMA_PATH.read_text())

    def _make_minimal_valid_runner_output(self) -> dict:
        """Minimal valid RunnerOutput that passes schema validation."""
        return {
            "runner_output_id": "RUN-2026-0001",
            "runner_output_version": "1.0",
            "run_id": "test-run-id",
            "run_mode": "dry_run",
            "status": "success",
            "runner_name": "aed-dry-run-validator",
            "runner_version": "0.1.0",
            "experiment_spec_ref": "EXP-2026-0001",
            "input_artifact_refs": [
                {
                    "artifact_type": "ExperimentSpec",
                    "artifact_id": "EXP-2026-0001",
                    "content_hash": "abc123",
                    "validation_status": "pass",
                }
            ],
            "data_manifest_refs": ["DM-2026-0001"],
            "run_config_hash": "hash123",
            "started_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T00:01:00Z",
            "run_owner": "test-owner",
            "created_at": "2026-01-01T00:00:00Z",
            "audit_summary": {
                "overall_result": "pass",
                "audits": [
                    {
                        "audit_name": "smoke_audit",
                        "audit_result": "pass",
                        "severity": "info",
                        "blocker_count": 0,
                        "warning_count": 0,
                        "created_at": "2026-01-01T00:00:00Z",
                    }
                ],
                "blocker_count": 0,
                "warning_count": 0,
            },
            "output_manifest": [
                {
                    "output_role": "evidence",
                    "output_path": "output.json",
                    "content_hash": "abc123",
                    "created_at": "2026-01-01T00:00:00Z",
                    "format": "json",
                    "description": "Test output",
                    "contains_private_data": False,
                    "publishable": False,
                }
            ],
        }

    def _get_validation_error(self):
        """Return jsonschema.ValidationError, guarding optional jsonschema."""
        jsonschema = pytest.importorskip("jsonschema")
        return jsonschema.ValidationError

    def _validate_with_schema(self, instance, schema):
        """Validate instance against the runner output schema (guards optional jsonschema)."""
        jsonschema = pytest.importorskip("jsonschema")
        jsonschema.validate(instance, schema)

    def test_runner_output_accepts_missing_trial_accounting_summary(self, schema):
        """Existing RunnerOutput artifacts without trial_accounting_summary remain valid."""
        artifact = self._make_minimal_valid_runner_output()
        self._validate_with_schema(artifact, schema)

    def test_runner_output_accepts_trial_accounting_summary_not_applicable(self, schema):
        """trial_accounting_summary with status=not_applicable and no_mutation is valid."""
        artifact = self._make_minimal_valid_runner_output()
        artifact["trial_accounting_summary"] = {
            "status": "not_applicable",
            "mutation_mode": "no_mutation",
        }
        self._validate_with_schema(artifact, schema)

    def test_runner_output_accepts_dry_run_reference_trial_accounting_summary(self, schema):
        """Full trial_accounting_summary with proposed IDs and dry_run_reference_only is valid."""
        artifact = self._make_minimal_valid_runner_output()
        artifact["trial_accounting_summary"] = {
            "status": "proposed",
            "mutation_mode": "dry_run_reference_only",
            "experiment_id": "EXP-2026-0001",
            "data_manifest_id": "DM-2026-0001",
            "search_space_id": "SSM-2026-0001",
            "trial_family_id": "TRF-2026-0001",
            "proposed_trial_id": "TRL-2026-PROPOSED-0001",
            "variant_id": "VAR-2026-0001",
            "n_tried": 10,
            "candidate_variant_count": 8,
            "failed_variant_count": 2,
            "all_variants_preserved": False,
            "sample_length": 120,
            "sample_to_trial_ratio": 12.0,
            "complexity": {
                "rule_count": 3,
                "parameter_count": 5,
                "signal_count": 2,
                "filter_count": 4,
                "complexity_bucket": "low",
            },
        }
        self._validate_with_schema(artifact, schema)

    def test_runner_output_rejects_unknown_trial_accounting_field(self, schema):
        """Unknown field inside trial_accounting_summary must fail validation."""
        artifact = self._make_minimal_valid_runner_output()
        artifact["trial_accounting_summary"] = {
            "status": "not_applicable",
            "mutation_mode": "no_mutation",
            "unknown_field": "should-reject",
        }
        with pytest.raises(self._get_validation_error()):
            self._validate_with_schema(artifact, schema)

    def test_runner_output_rejects_negative_n_tried(self, schema):
        """n_tried of -1 must fail validation (minimum: 0)."""
        artifact = self._make_minimal_valid_runner_output()
        artifact["trial_accounting_summary"] = {
            "status": "proposed",
            "mutation_mode": "dry_run_reference_only",
            "n_tried": -1,
        }
        with pytest.raises(self._get_validation_error()):
            self._validate_with_schema(artifact, schema)

    def test_runner_output_rejects_negative_candidate_variant_count(self, schema):
        """candidate_variant_count of -1 must fail validation."""
        artifact = self._make_minimal_valid_runner_output()
        artifact["trial_accounting_summary"] = {
            "status": "proposed",
            "mutation_mode": "dry_run_reference_only",
            "candidate_variant_count": -1,
        }
        with pytest.raises(self._get_validation_error()):
            self._validate_with_schema(artifact, schema)

    def test_runner_output_rejects_mutating_mode_ledger_write(self, schema):
        """mutation_mode=ledger_write must fail — not a permitted value."""
        artifact = self._make_minimal_valid_runner_output()
        artifact["trial_accounting_summary"] = {
            "status": "linked",
            "mutation_mode": "ledger_write",
        }
        with pytest.raises(self._get_validation_error()):
            self._validate_with_schema(artifact, schema)

    def test_runner_output_rejects_mutating_mode_registry_write(self, schema):
        """mutation_mode=registry_write must fail — not a permitted value."""
        artifact = self._make_minimal_valid_runner_output()
        artifact["trial_accounting_summary"] = {
            "status": "linked",
            "mutation_mode": "registry_write",
        }
        with pytest.raises(self._get_validation_error()):
            self._validate_with_schema(artifact, schema)

    def test_runner_output_rejects_unknown_complexity_field(self, schema):
        """Unknown field inside complexity sub-object must fail validation."""
        artifact = self._make_minimal_valid_runner_output()
        artifact["trial_accounting_summary"] = {
            "status": "proposed",
            "mutation_mode": "dry_run_reference_only",
            "complexity": {
                "rule_count": 3,
                "unknown_complexity_field": "reject",
            },
        }
        with pytest.raises(self._get_validation_error()):
            self._validate_with_schema(artifact, schema)

    def test_trial_accounting_summary_complexity_bucket_unknown(self, schema):
        """complexity_bucket=unknown is a valid value."""
        artifact = self._make_minimal_valid_runner_output()
        artifact["trial_accounting_summary"] = {
            "status": "proposed",
            "mutation_mode": "dry_run_reference_only",
            "complexity": {
                "complexity_bucket": "unknown",
            },
        }
        self._validate_with_schema(artifact, schema)

    def test_trial_accounting_summary_complexity_bucket_excessive(self, schema):
        """complexity_bucket=excessive is valid (blocks promotion at review time, not at schema level)."""
        artifact = self._make_minimal_valid_runner_output()
        artifact["trial_accounting_summary"] = {
            "status": "proposed",
            "mutation_mode": "dry_run_reference_only",
            "complexity": {
                "rule_count": 20,
                "parameter_count": 50,
                "complexity_bucket": "excessive",
            },
        }
        self._validate_with_schema(artifact, schema)

    def test_trial_accounting_summary_rejects_missing_required_status(self, schema):
        """status field is required — omitting it must fail."""
        artifact = self._make_minimal_valid_runner_output()
        artifact["trial_accounting_summary"] = {
            "mutation_mode": "no_mutation",
        }
        with pytest.raises(self._get_validation_error()):
            self._validate_with_schema(artifact, schema)

    def test_trial_accounting_summary_rejects_missing_required_mutation_mode(self, schema):
        """mutation_mode field is required — omitting it must fail."""
        artifact = self._make_minimal_valid_runner_output()
        artifact["trial_accounting_summary"] = {
            "status": "not_applicable",
        }
        with pytest.raises(self._get_validation_error()):
            self._validate_with_schema(artifact, schema)

    def test_trial_accounting_summary_null_is_valid(self, schema):
        """trial_accounting_summary can be explicitly null."""
        artifact = self._make_minimal_valid_runner_output()
        artifact["trial_accounting_summary"] = None
        self._validate_with_schema(artifact, schema)
