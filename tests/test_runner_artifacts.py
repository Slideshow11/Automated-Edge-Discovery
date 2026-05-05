"""
Unit tests for engine.edge_discovery.runners.runner_artifacts.

Scope: exception classes and failure_summary builder.
No runner orchestration, no registry writes, no ledger writes, no live trading.
"""
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
