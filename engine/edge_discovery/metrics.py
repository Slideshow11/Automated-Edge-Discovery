"""Prometheus metrics for audit runs."""

from prometheus_client import Counter, Histogram, start_http_server

# Total number of audit runs.
audit_runs_total = Counter(
    'audit_runs_total',
    'Total number of audit runs',
    ['run_id'],
)

# Total number of audit failures.
audit_failures_total = Counter(
    'audit_failures_total',
    'Total number of audit failures',
    ['run_id'],
)

# Duration of audit runs in seconds.
audit_duration_seconds = Histogram(
    'audit_duration_seconds',
    'Audit duration in seconds',
    ['run_id'],
)


def record_audit(run_id: str, passed: bool, duration_seconds: float) -> None:
    """Record metrics for a single audit run.

    Args:
        run_id: Unique identifier for the audit run.
        passed: Whether the audit passed.
        duration_seconds: Duration of the audit run in seconds.
    """
    audit_runs_total.labels(run_id=run_id).inc()
    if not passed:
        audit_failures_total.labels(run_id=run_id).inc()
    audit_duration_seconds.labels(run_id=run_id).observe(duration_seconds)


def start_http_server(port: int = 8000) -> None:
    """Start the Prometheus HTTP exporter on the specified port.

    Args:
        port: Port number to listen on (default: 8000).
    """
    start_http_server(port)


if __name__ == '__main__':
    # Example usage
    start_http_server(port=8000)
    record_audit('run_001', passed=True, duration_seconds=1.23)
    record_audit('run_002', passed=False, duration_seconds=4.56)
