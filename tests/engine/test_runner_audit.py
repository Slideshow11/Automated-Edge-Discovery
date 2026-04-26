import importlib
import os
import numpy as np


auditor = importlib.import_module('engine.edge_discovery.auditor')
runner = importlib.import_module('engine.edge_discovery.runner')


def test_runner_attaches_audit_report(monkeypatch):
    stub_report = {
        'pass': True,
        'reason': 'stubbed',
        'pbo': 0.01,
        'pbo_std': 0.0,
        'deflated_sharpe': [2.0],
        'aggregated_metrics': {'sharpe_mean': 1.5}
    }

    monkeypatch.setattr(auditor, 'run_backtest_audit', lambda **kw: stub_report)

    Y = np.ones((2, 3))
    per_split_metrics = [{'sharpe': 1.2} for _ in range(3)]

    res = runner.run_backtest(Y=Y, per_split_metrics=per_split_metrics)
    assert isinstance(res, dict)
    assert 'audit_report' in res
    assert res['audit_report']['pass'] is True
    assert res['audit_report']['reason'] == 'stubbed'
    assert 'audit_report_path' in res


def test_runner_saves_audit_report_to_disk(monkeypatch, tmp_path):
    """Audit report path exists on disk after run_backtest."""
    stub_report = {
        'pass': True,
        'reason': 'stubbed',
        'pbo': 0.01,
        'pbo_std': 0.0,
        'deflated_sharpe': [2.0],
        'aggregated_metrics': {'sharpe_mean': 1.5}
    }

    monkeypatch.setattr(auditor, 'run_backtest_audit', lambda **kw: stub_report)

    Y = np.ones((2, 3))
    per_split_metrics = [{'sharpe': 1.2} for _ in range(3)]

    cwd = os.getcwd()
    monkeypatch.chdir(tmp_path)
    try:
        res = runner.run_backtest(Y=Y, per_split_metrics=per_split_metrics)
        assert 'audit_report_path' in res
        audit_report_path = res['audit_report_path']
        assert os.path.exists(audit_report_path), f"Audit report not found at {audit_report_path}"
    finally:
        monkeypatch.chdir(cwd)
