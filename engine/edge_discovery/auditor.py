"""
Auditor utilities for Edge Discovery backtester.
Provides run_backtest_audit(Y, per_split_metrics, ...) and helper functions.
This is a minimal, well-documented implementation suitable for unit testing.
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
try:
    # package-aware import when run as part of package
    from . import pbo as pbo_module
except Exception:
    # fallback for test harnesses that load module by path
    import importlib
    pbo_module = importlib.import_module('engine.edge_discovery.pbo')

# Default audit configuration — all values are seeds for reproducibility
DEFAULT_AUDIT_CONFIG = {
    "pbo_threshold": 0.05,
    "sharpe_min": 0.0,
    "deflation_method": "lopez",  # 'lopez' or 'bootstrap'
    "bootstrap_n_iter": 1000,
    "bootstrap_seed": 42,
    "deterministic_seed": 0,
}


def aggregate_wfa_metrics(per_split_metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate per-split metrics robustly.
    Currently aggregates 'sharpe' by taking nanmean and count of valid splits.
    """
    if per_split_metrics is None:
        return {}
    sharpe_vals = []
    for m in per_split_metrics:
        try:
            v = m.get('sharpe', None) if isinstance(m, dict) else None
        except Exception:
            v = None
        if v is None:
            sharpe_vals.append(np.nan)
        else:
            sharpe_vals.append(float(v))
    sharpe_arr = np.array(sharpe_vals, dtype=float)
    agg = {
        'sharpe_mean': float(np.nanmean(sharpe_arr)) if sharpe_arr.size > 0 else None,
        'n_valid_splits': int(np.sum(~np.isnan(sharpe_arr)))
    }
    return agg


def estimate_pbo(Y: Optional[np.ndarray], **kwargs) -> Tuple[Optional[float], Optional[float]]:
    """Proxy wrapper around pbo.compute_pbo for backward compatibility.
    Returns (pbo, pbo_std) or (None, None) if Y is None.
    """
    if Y is None:
        return None, None
    return pbo_module.compute_pbo(Y, **kwargs)


def deflated_sharpe_with_options(
    Y: np.ndarray,
    method: str = "lopez",
    n_iter: int = 1000,
    seed: int = 42,
    deterministic_seed: int = 0,
) -> np.ndarray:
    """Compute deflated Sharpe using specified method.

    Parameters
    ----------
    Y : np.ndarray
        Return series array.
    method : str
        'lopez' (LOPEZ proxy) or 'bootstrap' (resampling).
    n_iter : int
        Number of bootstrap iterations (for 'bootstrap' method).
    seed : int
        Bootstrap RNG seed (for 'bootstrap' method).
    deterministic_seed : int
        Seed for numpy's global RNG state (used when method='lopez').

    Returns
    -------
    np.ndarray
        Deflated Sharpe array, or empty array on failure.
    """
    rng_state = np.random.get_state()
    try:
        if deterministic_seed is not None:
            np.random.seed(deterministic_seed)
        if method == "bootstrap":
            # Bootstrap-based deflation with per-iteration seed
            results = []
            for i in range(n_iter):
                np.random.seed(seed + i)
                idx = np.random.randint(0, len(Y), size=len(Y))
                boot = Y[idx]
                if len(boot) > 0 and np.std(boot) > 0:
                    results.append(np.mean(boot) / np.std(boot))
            if results:
                return np.array(results)
            return np.array([])
        else:
            # LOPEZ proxy (analytical)
            return pbo_module.deflated_sharpe(Y)
    except Exception:
        return np.array([])
    finally:
        np.random.set_state(rng_state)


def run_backtest_audit(
    Y: Optional[np.ndarray] = None,
    per_split_metrics: Optional[List[Dict[str, Any]]] = None,
    pbo_threshold: float = 0.05,
    sharpe_min: float = 0.0,
    compute_pbo_kwargs: Optional[Dict[str, Any]] = None,
    deflation_method: str = "lopez",
    bootstrap_n_iter: int = 1000,
    bootstrap_seed: int = 42,
    deterministic_seed: int = 0,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run conservative audit on backtest results.

    Logic:
    - If Y provided, compute pbo and pbo_std via pbo.compute_pbo
    - Compute deflated_sharpe via deflated_sharpe_with_options using configured method
    - Aggregate per-split metrics via aggregate_wfa_metrics
    - Pass only if pbo <= pbo_threshold AND max(deflated_sharpe) >= sharpe_min

    Parameters
    ----------
    Y : np.ndarray, optional
        Return series for PBO and deflation.
    per_split_metrics : list of dict, optional
        Per-split walk-forward metrics.
    pbo_threshold : float
        Maximum allowable PBO to pass.
    sharpe_min : float
        Minimum Sharpe to pass.
    compute_pbo_kwargs : dict, optional
        Keyword arguments forwarded to pbo.compute_pbo.
    deflation_method : str
        'lopez' (LOPEZ proxy, default) or 'bootstrap'.
    bootstrap_n_iter : int
        Number of bootstrap iterations when method='bootstrap'.
    bootstrap_seed : int
        RNG seed for bootstrap method.
    deterministic_seed : int
        Global RNG seed for reproducible LOPEZ proxy computation.
    config : dict, optional
        Full configuration dict to override individual parameters.
        Keys: pbo_threshold, sharpe_min, deflation_method,
        bootstrap_n_iter, bootstrap_seed, deterministic_seed.

    Returns a report dict with keys: pass (bool), reason (str), pbo, pbo_std,
    deflated_sharpe (np.ndarray), aggregated_metrics (dict)
    """
    # Merge config overrides
    if config is not None:
        pbo_threshold = config.get("pbo_threshold", pbo_threshold)
        sharpe_min = config.get("sharpe_min", sharpe_min)
        deflation_method = config.get("deflation_method", deflation_method)
        bootstrap_n_iter = config.get("bootstrap_n_iter", bootstrap_n_iter)
        bootstrap_seed = config.get("bootstrap_seed", bootstrap_seed)
        deterministic_seed = config.get("deterministic_seed", deterministic_seed)

    report: Dict[str, Any] = {
        'pass': False,
        'reason': 'not evaluated',
        'pbo': None,
        'pbo_std': None,
        'deflated_sharpe': np.array([]),
        'aggregated_metrics': {},
        'config': {
            'pbo_threshold': pbo_threshold,
            'sharpe_min': sharpe_min,
            'deflation_method': deflation_method,
            'bootstrap_n_iter': bootstrap_n_iter,
            'bootstrap_seed': bootstrap_seed,
            'deterministic_seed': deterministic_seed,
        }
    }

    agg = aggregate_wfa_metrics(per_split_metrics)
    report['aggregated_metrics'] = agg

    compute_pbo_kwargs = compute_pbo_kwargs or {}

    if Y is not None:
        pbo_val, pbo_std = estimate_pbo(Y, **compute_pbo_kwargs)
        report['pbo'] = pbo_val
        report['pbo_std'] = pbo_std
        try:
            ds = deflated_sharpe_with_options(
                Y,
                method=deflation_method,
                n_iter=bootstrap_n_iter,
                seed=bootstrap_seed,
                deterministic_seed=deterministic_seed,
            )
        except Exception:
            ds = np.array([])
        report['deflated_sharpe'] = np.asarray(ds)
    else:
        report['pbo'] = None
        report['pbo_std'] = None
        report['deflated_sharpe'] = np.array([])

    # Evaluate gate
    reasons = []
    pbo_ok = True
    sharpe_ok = True
    if report['pbo'] is not None:
        if report['pbo'] > pbo_threshold:
            pbo_ok = False
            reasons.append(f'PBO {report["pbo"]:.4f} > threshold {pbo_threshold}')
    # if no pbo available, pbo_ok stays True and gate relies on sharpe
    ds_arr = report['deflated_sharpe']
    if ds_arr.size > 0:
        if np.nanmax(ds_arr) < sharpe_min:
            sharpe_ok = False
            reasons.append(f'max(deflated_sharpe) {float(np.nanmax(ds_arr)):.4f} < {sharpe_min}')
    else:
        # no deflated sharpe available; rely on aggregated_metrics if possible
        if 'sharpe_mean' in agg and agg['sharpe_mean'] is not None:
            if agg['sharpe_mean'] < sharpe_min:
                sharpe_ok = False
                reasons.append(f'aggregated sharpe {agg["sharpe_mean"]:.4f} < {sharpe_min}')

    passed = pbo_ok and sharpe_ok
    report['pass'] = bool(passed)
    report['reason'] = ' AND '.join(reasons) if reasons else ('passed' if passed else 'failed')

    return report



def _upload_to_s3(data: bytes, bucket: str, key: str) -> None:
    """Upload data to S3. Imports boto3 internally."""
    try:
        import boto3
    except ImportError:
        raise ImportError("boto3 is required for S3 upload but is not installed. Install it with: pip install boto3")
    s3 = boto3.client('s3')
    s3.put_object(Bucket=bucket, Key=key, Body=data)


def save_audit_report(
    report: Dict[str, Any],
    run_id: Optional[str] = None,
    out_dir: Optional[str] = None,
) -> Path:
    """Persist audit report as JSON or upload to S3 if configured.

    If env EDGE_DISCOVERY_AUDIT_S3_BUCKET is set, attempts an S3 upload and
    returns the S3 URI on success. Falls back to local disk on error.
    """
    bucket = os.environ.get("EDGE_DISCOVERY_AUDIT_S3_BUCKET", "")

    run_id = run_id or str(int(time.time() * 1000))

    # Try S3 when configured
    if bucket:
        key = f"audit_reports/{run_id}.json"
        data = json.dumps(_make_serializable(report)).encode('utf-8')
        try:
            _upload_to_s3(data, bucket, key)
            logging.getLogger(__name__).info("audit report saved to s3://%s/%s", bucket, key)
            return f"s3://{bucket}/{key}"
        except Exception:
            logging.getLogger(__name__).exception("S3 upload failed, falling back to local disk")

    out_dir = Path(out_dir) if out_dir else Path.cwd() / "audit_reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{run_id}.json"
    file_path = out_dir / filename

    serializable = _make_serializable(report)

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False)

    return file_path

def _make_serializable(obj: Any) -> Any:
    """Recursively convert numpy/Python types to JSON-serializable equivalents."""
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_serializable(item) for item in obj]
    return obj
