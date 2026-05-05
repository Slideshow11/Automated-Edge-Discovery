"""
Runner artifact helpers: exception classes, failure summary builder, and
failure artifact helpers.

No registry writes, no ledger writes, no live trading, no production execution.

These helpers are used by first_thin_real_data_runner.py to construct
schema-compliant RunnerOutput artifacts.
"""

from __future__ import annotations


class GovernanceRejection(Exception):
    """
    Raised when governance validation fails (blocker_count > 0).

    Carries the artifact dict so main() can emit a failed_validation
    RunnerOutput artifact before exiting with a nonzero code.
    """

    def __init__(self, artifact: dict, message: str):
        self.artifact = artifact
        self.message = message
        super().__init__(message)


class UnsupportedConfig(Exception):
    """
    Raised when canonical summary is requested but the dataset format
    does not support it (e.g., non-CSV). Carries the failure_type
    so the caller can set failure_type='unsupported_config' in the
    failure_summary instead of 'validation_error'.
    """

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _build_failure_summary(
    audit_summary: dict,
    status: str,
    observation_missing_columns: list[str] | None = None,
) -> dict:
    """
    Build a failure_summary dict for failed_validation / failed_runtime statuses.

    Collects all failing audit names from audit_summary and formats them into
    a blocker_summary. When observation_missing_columns is provided, appends
    the column names to the blocker_summary string.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    failing_audits = [
        a["audit_name"]
        for a in audit_summary["audits"]
        if a["audit_result"] == "fail"
    ]
    blocker_summary_parts = [f"Validation failed: {', '.join(failing_audits)}."]
    if observation_missing_columns:
        blocker_summary_parts.append(
            f"Missing observation-table columns: {', '.join(observation_missing_columns)}."
        )
    blocker_summary_parts.append(f"Total blockers: {audit_summary['blocker_count']}.")
    blocker_summary = " ".join(blocker_summary_parts)
    return {
        "failure_type": "validation_error",
        "status": status,
        "failed_check": ", ".join(failing_audits) or None,
        "blocker_summary": blocker_summary,
        "missing_data_summary_ref": None,
        "details_ref": None,
        "created_at": now,
    }
