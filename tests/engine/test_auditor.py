import importlib
from pathlib import Path

import numpy as np
import pytest

# Import modules using package-style imports so monkeypatch modifies the same module
auditor = importlib.import_module('engine.edge_discovery.auditor')
pbo_module = importlib.import_module('engine.edge_discovery.pbo')


def test_run_backtest_audit_pass(monkeypatch):
    # Patch PBO and deflated sharpe to deterministic values
    monkeypatch.setattr(pbo_module, 'compute_pbo', lambda Y, **kw: (0.01, 0.002))
    monkeypatch.setattr(pbo_module, 'deflated_sharpe', lambda Y, **kw: np.array([2.0]))

    Y = np.ones((3, 5))
    per_split_metrics = [{'sharpe': 1.2} for _ in range(5)]

    res = auditor.run_backtest_audit(
        Y=Y,
        per_split_metrics=per_split_metrics,
        pbo_threshold=0.05,
        sharpe_min=1.0,
    )

    assert isinstance(res, dict)
    assert res.get('pass') is True
    assert pytest.approx(res.get('pbo', None), rel=1e-6) == 0.01
    assert pytest.approx(res.get('pbo_std', None), rel=1e-6) == 0.002
    assert np.max(res.get('deflated_sharpe', np.array([-np.inf]))) >= 1.0


def test_run_backtest_audit_fail_pbo(monkeypatch):
    # PBO above threshold -> should fail even if deflated sharpe is high
    monkeypatch.setattr(pbo_module, 'compute_pbo', lambda Y, **kw: (0.20, 0.01))
    monkeypatch.setattr(pbo_module, 'deflated_sharpe', lambda Y, **kw: np.array([2.0]))

    Y = np.ones((3, 5))
    per_split_metrics = [{'sharpe': 1.2} for _ in range(5)]

    res = auditor.run_backtest_audit(
        Y=Y,
        per_split_metrics=per_split_metrics,
        pbo_threshold=0.05,
        sharpe_min=1.0,
    )

    assert isinstance(res, dict)
    assert res.get('pass') is False
    assert pytest.approx(res.get('pbo', None), rel=1e-6) == 0.20


def test_aggregate_wfa_metrics_handles_bad_input():
    # Ensure aggregation is robust to NaNs and missing keys
    per_split = [{'sharpe': np.nan}, {}, {'sharpe': 1.0}]
    agg = auditor.aggregate_wfa_metrics(per_split)
    assert isinstance(agg, dict)


def test_save_audit_report_falls_back_to_local_when_boto3_missing(monkeypatch, tmp_path):
    """When EDGE_DISCOVERY_AUDIT_S3_BUCKET is set but boto3 is not installed,
    save_audit_report must fall back to local disk instead of propagating the
    ImportError."""
    import sys

    # Set the S3 bucket env var so the function attempts S3 first
    monkeypatch.setenv('EDGE_DISCOVERY_AUDIT_S3_BUCKET', 'test-bucket')

    # Block boto3 from being importable by caching None in sys.modules
    # (Python returns the cached value even if None, which triggers AttributeError
    # when the code tries to use it — that is caught by the outer Exception handler)
    monkeypatch.setitem(sys.modules, 'boto3', None)

    report = {'pass': True, 'pbo': 0.03, 'deflated_sharpe': [1.2]}
    result = auditor.save_audit_report(report, run_id='test-run-001', out_dir=str(tmp_path))

    # Must have fallen back to local disk
    assert isinstance(result, (str, Path)), f"expected local path, got {type(result)}"
    path = Path(result)
    assert path.exists(), f"expected local file to exist at {path}"
    assert path.name == 'test-run-001.json'

    # Clean up env
    monkeypatch.delenv('EDGE_DISCOVERY_AUDIT_S3_BUCKET', raising=False)
