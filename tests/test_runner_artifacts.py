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
