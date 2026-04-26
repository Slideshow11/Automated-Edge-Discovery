"""Minimal runner for Edge Discovery backtests used for integration and tests.

This runner is intentionally small: it exposes run_backtest(Y, per_split_metrics)
which runs a lightweight "backtest" (placeholder) and then invokes the auditor
if enabled in config. The goal is to provide a safe integration point for the
auditor and a testable API.
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging
import time

from . import config as ed_config
from . import auditor
from . import ledger as ledger_module


try:
    from . import metrics
except Exception:
    metrics = None

logger = logging.getLogger(__name__)


def _run_backtest_for_split(strategy: str, split_idx: int, n_splits: int, purge: float, cost_model: Optional[str]) -> Dict[str, Any]:
    """Run a single CPCV split for a strategy. Override via monkeypatch in tests."""
    # Minimal placeholder - real implementation calls the backtester
    return {
        "strategy": strategy,
        "split_idx": split_idx,
        "total_return": 0.0,
        "sharpe": 0.0,
        "max_drawdown": 0.0,
        "trades": 0,
        "execution_time_seconds": 0.0,
    }


def run_wfa_cpcv(
    strategies: List[str],
    n_splits: int,
    purge: float,
    cost_model: Optional[str],
    out_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Run WFA/CPCV across strategies and collect split results.

    Parameters
    - strategies: list of strategy identifiers
    - n_splits: number of CPCV splits
    - purge: purge fraction
    - cost_model: cost model identifier (passed to _run_backtest_for_split)
    - out_dir: output directory for JSON files

    Returns a dict with keys:
    - 'summary': summary dict with n_splits and pbo_estimate
    - 'raw_splits_file': path to JSON file containing per-split results
    - 'summary_file': path to JSON file containing the summary

    A JSONL ledger entry is appended to the configured ledger path (see
    ``ed_config.LEDGER_PATH_DEFAULT``) on every invocation.
    """
    started_at = ledger_module.now_utc()
    run_id = str(int(time.time() * 1000))

    if out_dir is None:
        out_dir = ".wfa/output"
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Collect run configuration for the ledger entry's config_hash
    run_config = {
        "strategies": strategies,
        "n_splits": n_splits,
        "purge": purge,
        "cost_model": cost_model,
        "out_dir": out_dir,
    }

    # Build output-artifacts and metrics in a dict so the finally block
    # can capture them regardless of how the function exits.
    _ledger_output_artifacts: Dict[str, str] = {}
    _ledger_metrics: Dict[str, Any] = {}
    _ledger_error: Optional[str] = None
    _result: Dict[str, Any] = {}

    try:
        # Collect all split results
        raw_splits: List[Dict[str, Any]] = []
        for strategy in strategies:
            for split_idx in range(n_splits):
                split_result = _run_backtest_for_split(
                    strategy=strategy,
                    split_idx=split_idx,
                    n_splits=n_splits,
                    purge=purge,
                    cost_model=cost_model,
                )
                raw_splits.append(split_result)

        # Write raw splits file
        raw_splits_file = out_path / f"raw_splits_{run_id}.json"
        with raw_splits_file.open("w") as f:
            json.dump(raw_splits, f)

        # Compute summary with pbo_estimate and aggregate stats
        import numpy as np

        # Build Y matrix: rows=strategies, cols=splits
        # Handle both 'total_return' (integration tests) and 'returns' (unit tests)
        def _get_return(s):
            return s.get("total_return", s.get("returns", 0.0))

        Y = np.array([
            [_get_return(s) for s in raw_splits if s["strategy"] == strat]
            for strat in strategies
        ], dtype=float)

        pbo_estimate = None
        pbo_std = None
        try:
            pbo_estimate, pbo_std = auditor.estimate_pbo(Y)
        except Exception:
            pass

        # Aggregate per-split metrics; handle both field name variants
        def _get_field(s, key, default):
            return s.get(key, default)

        all_returns = [_get_return(s) for s in raw_splits]
        all_sharpe = [s.get("sharpe", 0.0) for s in raw_splits]
        all_mdd = [s.get("max_drawdown", 0.0) for s in raw_splits]
        all_trades = [s.get("trades", 0) for s in raw_splits]

        summary_data = {
            "n_splits": len(raw_splits),
            "pbo_estimate": pbo_estimate,
            "pbo_std": pbo_std,
            "mean_return": float(np.mean(all_returns)) if all_returns else None,
            "median_return": float(np.median(all_returns)) if all_returns else None,
            "mean_sharpe": float(np.mean(all_sharpe)) if all_sharpe else None,
            "mean_max_drawdown": float(np.mean(all_mdd)) if all_mdd else None,
            "total_trades": sum(all_trades) if all_trades else 0,
        }
        summary_file = out_path / f"summary_{run_id}.json"
        with summary_file.open("w") as f:
            json.dump({"summary": summary_data}, f)

        _ledger_output_artifacts = {
            "raw_splits": str(raw_splits_file),
            "summary": str(summary_file),
        }
        _ledger_metrics = summary_data
        _result = {
            "summary": summary_data,
            "raw_splits_file": str(raw_splits_file),
            "summary_file": str(summary_file),
        }
    except Exception as e:
        _ledger_error = str(e)
        raise
    finally:
        completed_at = ledger_module.now_utc()
        entry = ledger_module.LedgerEntry(
            run_id=run_id,
            run_type="wfa_cpcv",
            started_at=started_at,
            completed_at=completed_at,
            status="error" if _ledger_error else "success",
            config_hash=ledger_module.config_hash(run_config),
            git_commit=ledger_module.git_commit(),
            error=_ledger_error,
            input_artifacts={},
            output_artifacts=_ledger_output_artifacts,
            metrics_summary=_ledger_metrics,
        )
        cfg = ed_config.get_config()
        ledger_path = cfg.get("ledger_path", ed_config.LEDGER_PATH_DEFAULT)
        try:
            ledger_module.Ledger(path=ledger_path).write(entry)
        except Exception:
            # Ledger failure must never mask or alter the function result
            logger.exception("Failed to write experiment ledger entry")

    return _result


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
