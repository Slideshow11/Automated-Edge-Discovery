import importlib
import types
import json

import pytest

auditor = importlib.import_module('engine.edge_discovery.auditor')
integrations = importlib.import_module('engine.edge_discovery.integrations')


def test_audit_wrapper_runs_and_attaches(monkeypatch, tmp_path):
    # stub report
    stub = {'pass': True, 'reason': 'ok', 'pbo': 0.01, 'pbo_std': 0.0, 'deflated_sharpe': [1.2]}

    monkeypatch.setattr(auditor, 'run_backtest_audit', lambda **kw: stub)

    # stub save to write to tmp_path
    def fake_save(report, run_id=None, out_dir='audit_reports'):
        p = tmp_path / f"{run_id}.json"
        p.write_text(json.dumps(report))
        return str(p)

    monkeypatch.setattr(auditor, 'save_audit_report', fake_save)

    @integrations.audit_wrapper()
    def fake_backtest():
        # Return minimal structure expected by wrapper
        return {'result': {'run_id': 'r1'}, 'Y': [[1, 2], [3, 4]], 'per_split_metrics': [{'sharpe': 1.2}, {'sharpe': 1.1}]}

    res = fake_backtest()
    assert 'audit_report' in res
    assert res['audit_report']['pass'] is True
    assert 'audit_report_path' in res


def test_audit_wrapper_skips_when_disabled(monkeypatch):
    # Make get_config return AUDIT_ENABLED False
    cfg_mod = importlib.import_module('engine.edge_discovery.config')
    monkeypatch.setattr(cfg_mod, 'get_config', lambda prefix='EDGE_DISCOVERY': {'AUDIT_ENABLED': False})

    called = {'ran': False}

    @integrations.audit_wrapper()
    def fake_backtest2():
        called['ran'] = True
        return {'result': {'run_id': 'r2'}, 'Y': [[1, 2]]}

    res = fake_backtest2()
    assert called['ran'] is True
    assert 'audit_report' not in res
