"""Minimal runner for Edge Discovery backtests used for integration and tests.

This runner is intentionally small: it exposes run_backtest(Y, per_split_metrics)
which runs a lightweight "backtest" (placeholder) and then invokes the auditor
if enabled in config. The goal is to provide a safe integration point for the
auditor and a testable API.
"""
from typing import Any, Dict, List, Optional
import logging
import time

from . import config as ed_config
from . import auditor


try:
    from . import metrics
except Exception:
    metrics = None

logger = logging.getLogger(__name__)


def run_backtest(Y: Optional[Any], per_split_metrics: Optional[List[Dict[str, Any]]] = None, audit_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run a minimal backtest and optionally run the audit.

    Parameters
    - Y: candidate x split performance matrix (or None)
    - per_split_metrics: list of per-split metric dicts

    Returns a result dict with keys:
    - 'result': placeholder metrics
    - 'audit_report': present if audit ran (may be None)
    - 'audit_error': present if audit errored
    - audit_config: optional dict with overrides for pbo_threshold / sharpe_min keys
    """
    # Placeholder backtest result
    result: Dict[str, Any] = {
        'result': {
            'n_candidates': int(Y.shape[0]) if hasattr(Y, 'shape') else None,
            'n_splits': int(Y.shape[1]) if hasattr(Y, 'shape') else None,
        }
    }

    audit_report = None
    audit_error = None

    audit_start_time = time.time()

    if ed_config.AUDIT_ENABLED:
        try:
            report = auditor.run_backtest_audit(
                Y=Y,
                per_split_metrics=per_split_metrics,
                pbo_threshold=audit_config.get('pbo_threshold') if audit_config else ed_config.PBO_THRESHOLD_DEFAULT,
                sharpe_min=audit_config.get('sharpe_min') if audit_config else ed_config.SHARPE_MIN_DEFAULT,
            )
            audit_report = report
            result['audit_report'] = report
            # If configured to block on fail, raise
            if not report.get('pass', False) and ed_config.AUDIT_ON_FAIL == 'block':
                reason = report.get('reason', 'audit failure')
                raise RuntimeError(f'Audit failed: {reason}')
        except Exception as e:
            logger.exception('Audit failed')
            audit_error = str(e)
            result['audit_error'] = audit_error

    audit_duration = time.time() - audit_start_time

    # Persist audit report to disk when available
    if audit_report is not None:
        try:
            run_id = result.get('result', {}).get('run_id') or str(int(time.time() * 1000))
            saved = auditor.save_audit_report(audit_report, run_id=run_id)
            result['audit_report_path'] = str(saved)
        except Exception as e:
            logger.exception('Failed to save audit report')
            result['audit_save_error'] = str(e)

    # Structured audit summary logging
    try:
        audit_summary = {
            'run_id': result.get('result', {}).get('run_id', run_id),
            'pass': bool(result.get('audit_report', {}).get('pass', False)),
            'pbo': result.get('audit_report', {}).get('pbo'),
            'pbo_std': result.get('audit_report', {}).get('pbo_std'),
            'max_deflated_sharpe': (max(result.get('audit_report', {}).get('deflated_sharpe', []))
                                    if result.get('audit_report', {}).get('deflated_sharpe') else None),
            'audit_report_path': result.get('audit_report_path')
        }
        logger.info('audit_summary: %s', audit_summary)
    except Exception:
        logger.exception('Failed to log audit summary')

    # Record metrics
    try:
        if metrics is not None and audit_report is not None:
            metrics.record_audit(run_id=run_id, passed=bool(audit_report.get('pass', False)), duration_seconds=audit_duration)
    except Exception:
        logger.exception('Failed to record metrics')

    return result
