"""Integration helpers for wiring the Edge Discovery auditor into external runners.

Provide a conservative decorator `audit_wrapper` that can be applied to existing
backtest/runner functions without invasive edits. The wrapper:

- Calls the wrapped function and expects a result dict and optionally Y/per_split_metrics
  to be present in the return or as part of the function's local context.
- If engine.edge_discovery.config.get_config()['AUDIT_ENABLED'] is True, it calls
  auditor.run_backtest_audit with available data and attaches 'audit_report' and
  'audit_report_path' to the returned dict.
- Swallows audit exceptions and logs them so the wrapped function's behavior is
  unchanged on audit failures.

Usage example:

    from engine.edge_discovery.integrations import audit_wrapper

    @audit_wrapper()
    def run_production_backtest(...):
        # perform backtest
        return {'result': {...}, 'Y': Y, 'per_split_metrics': per_split_metrics}

Notes:
- The wrapper is intentionally conservative: it never raises on audit errors.
- For runners that already call auditor directly, do not double-wrap.
"""
from typing import Any, Callable, Dict, Optional
import functools
import logging

from . import auditor
from . import config as ed_config

logger = logging.getLogger(__name__)


def audit_wrapper(*, pbo_threshold: Optional[float] = None, sharpe_min: Optional[float] = None):
    """Return a decorator that runs the audit after the wrapped backtest function.

    The wrapped function should return a dict-like result. The wrapper will look
    for 'Y' and 'per_split_metrics' keys in the returned dict and pass them to
    auditor.run_backtest_audit. If the wrapped function returns None or a
    non-dict, the wrapper will still attempt to run the audit only if explicit
    'Y'/'per_split_metrics' were provided via kwargs (not typical).

    Parameters
    - pbo_threshold, sharpe_min: optional overrides; if not provided, uses
      engine.edge_discovery.config defaults.
    """

    def decorator(fn: Callable[..., Dict[str, Any]]):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> Dict[str, Any]:
            result = fn(*args, **kwargs)

            try:
                cfg = ed_config.get_config() if hasattr(ed_config, 'get_config') else {}
                if not cfg.get('audit_enabled', getattr(ed_config, 'AUDIT_ENABLED', True)):
                    return result

                # Extract inputs for audit
                Y = None
                per_split_metrics = None
                if isinstance(result, dict):
                    if "Y" in result:
                        Y = result["Y"]
                    elif isinstance(result.get("result"), dict) and "Y" in result["result"]:
                        Y = result["result"]["Y"]
                    per_split_metrics = result.get('per_split_metrics')

                # Allow explicit kwargs to override
                Y = kwargs.get('Y', Y)
                per_split_metrics = kwargs.get('per_split_metrics', per_split_metrics)

                # Only run audit if we have at least one of the expected inputs
                if Y is None and (per_split_metrics is None or len(per_split_metrics) == 0):
                    logger.debug('audit_wrapper: no Y or per_split_metrics available; skipping audit')
                    return result

                # Resolve thresholds
                pbo_t = pbo_threshold if pbo_threshold is not None else cfg.get('pbo_threshold', getattr(ed_config, 'PBO_THRESHOLD_DEFAULT', 0.05))
                sharpe_m = sharpe_min if sharpe_min is not None else cfg.get('sharpe_min', getattr(ed_config, 'SHARPE_MIN_DEFAULT', 1.0))

                report = auditor.run_backtest_audit(Y=Y, per_split_metrics=per_split_metrics, pbo_threshold=pbo_t, sharpe_min=sharpe_m)
                if isinstance(result, dict):
                    result['audit_report'] = report
                else:
                    # If result is not a dict, create a wrapper
                    result = {'result': result, 'audit_report': report}

                # Persist report and attach path when possible
                try:
                    run_id = (result.get('result', {}).get('run_id') if isinstance(result, dict) else None) or str(int(__import__('time').time() * 1000))
                    saved = auditor.save_audit_report(report, run_id=run_id)
                    result['audit_report_path'] = str(saved)
                except Exception:
                    logger.exception('audit_wrapper: failed to save audit report')

            except Exception:
                # Audit errors must not break the wrapped function
                logger.exception('audit_wrapper: audit execution failed')

            return result

        return wrapper

    return decorator
