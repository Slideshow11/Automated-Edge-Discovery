import json
import subprocess
from pathlib import Path
import sys
import tempfile

REPO = Path('.')
SCRIPTS = REPO / 'scripts' / 'local' / 'validate_event_options_contract.py'
FIXTURES = REPO / 'fixtures' / 'event_options_contract_v1'


def run_cli(args):
    cmd = [sys.executable, str(SCRIPTS)] + args
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res


def test_valid_minimal_fixtures_text():
    res = run_cli([
        '--events', str(FIXTURES / 'valid_events_minimal.csv'),
        '--options', str(FIXTURES / 'valid_options_observations_minimal.csv'),
        '--profile', 'minimal_fixture_profile',
        '--format', 'text',
    ])
    assert res.returncode == 0
    assert 'events_count' in res.stdout


def test_invalid_fixtures_json():
    res = run_cli([
        '--events', str(FIXTURES / 'invalid_events_examples.csv'),
        '--options', str(FIXTURES / 'invalid_options_observations_examples.csv'),
        '--profile', 'minimal_fixture_profile',
        '--format', 'json',
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    assert 'blockers' in out
    assert isinstance(out['blockers'], list)


def test_intra_vs_intraday():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as f:
        f.write('event_id,symbol,event_date,event_time,event_session,event_timezone\n')
        f.write('E1,A,2026-01-01,2026-01-01T09:30:00Z,INTRA,UTC\n')
        f.write('E2,B,2026-01-02,2026-01-02T09:30:00Z,INTRADAY,UTC\n')
        tmp_events = Path(f.name)

    res = run_cli([
        '--events', str(tmp_events),
        '--options', str(FIXTURES / 'valid_options_observations_minimal.csv'),
        '--profile', 'minimal_fixture_profile',
        '--format', 'json',
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    codes = {b['code'] for b in out['blockers']}
    assert 'invalid_enum' in codes


def test_missing_event_id_in_option():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as f:
        f.write('event_id,option_id,option_symbol,observation_date\n')
        f.write(',O1,OPT1,2026-01-01T09:30:00Z\n')
        tmp_opts = Path(f.name)

    res = run_cli([
        '--events', str(FIXTURES / 'valid_events_minimal.csv'),
        '--options', str(tmp_opts),
        '--profile', 'minimal_fixture_profile',
        '--format', 'json',
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    codes = {b['code'] for b in out['blockers']}
    assert 'missing_event_link' in codes or 'missing_required_field' in codes


def test_unknown_event_link():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as f:
        f.write('event_id,option_id,option_symbol,observation_date\n')
        f.write('UNKNOWN_EVT,O1,OPT1,2026-01-01T09:30:00Z\n')
        tmp_opts = Path(f.name)

    res = run_cli([
        '--events', str(FIXTURES / 'valid_events_minimal.csv'),
        '--options', str(tmp_opts),
        '--profile', 'minimal_fixture_profile',
        '--format', 'json',
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    codes = {b['code'] for b in out['blockers']}
    assert 'invalid_event_link' in codes


def test_json_output_contains_arrays():
    res = run_cli([
        '--events', str(FIXTURES / 'invalid_events_examples.csv'),
        '--options', str(FIXTURES / 'invalid_options_observations_examples.csv'),
        '--profile', 'minimal_fixture_profile',
        '--format', 'json',
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    assert isinstance(out.get('blockers'), list)
    assert isinstance(out.get('warnings'), list)


def test_strict_profile_requires_more_fields():
    res = run_cli([
        '--events', str(FIXTURES / 'valid_events_minimal.csv'),
        '--options', str(FIXTURES / 'valid_options_observations_minimal.csv'),
        '--profile', 'strict_contract_profile',
        '--format', 'json',
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    codes = {b['code'] for b in out['blockers']}
    assert 'missing_required_field' in codes


def test_no_forbidden_runtime_imports():
    text = Path(SCRIPTS).read_text()
    forbidden = ['pandas', 'pyarrow', 'sqlite3', 'requests', 'httpx', 'urllib', 'subprocess', 'os.system', 'Popen']
    for f in forbidden:
        assert f not in text

# New tests for edge-case fixtures

def test_invalid_event_edge_cases_emit_expected_blockers():
    res = run_cli([
        '--events', str(FIXTURES / 'invalid_events_edge_cases.csv'),
        '--options', str(FIXTURES / 'valid_options_observations_minimal.csv'),
        '--profile', 'minimal_fixture_profile',
        '--format', 'json',
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    codes = {b['code'] for b in out['blockers']}
    expected = {'missing_required_field','duplicate_event_id','invalid_enum','unknown_event_session','invalid_timestamp'}
    assert expected.intersection(codes)


def test_invalid_option_edge_cases_emit_expected_blockers():
    res = run_cli([
        '--events', str(FIXTURES / 'valid_events_minimal.csv'),
        '--options', str(FIXTURES / 'invalid_options_observations_edge_cases.csv'),
        '--profile', 'minimal_fixture_profile',
        '--format', 'json',
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    codes = {b['code'] for b in out['blockers']}
    expected = {'missing_event_link','invalid_event_link','unknown_event_hold','unknown_gap_exposure','future_feature_timestamp'}
    assert expected.intersection(codes)


def test_edge_case_fixtures_are_documented_in_readme():
    text = Path(FIXTURES / 'README.md').read_text()
    assert 'invalid_events_edge_cases.csv' in text
    assert 'invalid_options_observations_edge_cases.csv' in text


# Tests for strict_contract_profile fixture coverage

def test_valid_strict_fixtures_pass_under_strict_profile():
    """Valid strict fixtures (with all strict-only required fields) must pass
    strict_contract_profile without blockers."""
    res = run_cli([
        '--events', str(FIXTURES / 'valid_events_strict.csv'),
        '--options', str(FIXTURES / 'valid_options_observations_strict.csv'),
        '--profile', 'strict_contract_profile',
    ])
    assert res.returncode == 0, f"expected strict fixtures to pass, got stdout: {res.stdout}"
    assert 'blockers_count: 0' in res.stdout


def test_minimal_fixtures_fail_under_strict_profile():
    """Minimal fixtures must fail strict_contract_profile because they lack
    all strict-only required fields (event_timestamp_quality, calendar_id,
    timezone, point_in_time_policy, option_type, option_expiry, etc.)."""
    res = run_cli([
        '--events', str(FIXTURES / 'valid_events_minimal.csv'),
        '--options', str(FIXTURES / 'valid_options_observations_minimal.csv'),
        '--profile', 'strict_contract_profile',
        '--format', 'json',
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    codes = {b['code'] for b in out['blockers']}
    assert 'missing_required_field' in codes


def test_strict_unknown_session_rejected():
    """UNKNOWN event_session is blocked under strict_contract_profile
    (unknown_event_session), even when all other strict fields are present."""
    res = run_cli([
        '--events', str(FIXTURES / 'invalid_events_strict.csv'),
        '--options', str(FIXTURES / 'valid_options_observations_strict.csv'),
        '--profile', 'strict_contract_profile',
        '--format', 'json',
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    codes = {b['code'] for b in out['blockers']}
    assert 'unknown_event_session' in codes


def test_strict_missing_option_fields_rejected():
    """Rows missing strict-only option fields (option_type, option_expiry,
    expiry_covers_event, event_hold_flag, gap_exposure, fill_model,
    stale_quote_policy, spread_metric, liquidity_metric) must fail
    strict_contract_profile with missing_required_field for each absent field."""
    res = run_cli([
        '--events', str(FIXTURES / 'valid_events_strict.csv'),
        '--options', str(FIXTURES / 'invalid_options_observations_strict.csv'),
        '--profile', 'strict_contract_profile',
        '--format', 'json',
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    codes = {b['code'] for b in out['blockers']}
    missing = {b['field'] for b in out['blockers'] if b['code'] == 'missing_required_field'}
    strict_fields = {
        'option_type', 'option_expiry', 'expiry_covers_event',
        'event_hold_flag', 'gap_exposure', 'fill_model',
        'stale_quote_policy', 'spread_metric', 'liquidity_metric',
    }
    assert strict_fields.issubset(missing), f"expected all strict fields missing, got {missing}"


def test_strict_fixtures_documented_in_readme():
    text = Path(FIXTURES / 'README.md').read_text()
    assert 'valid_events_strict.csv' in text
    assert 'valid_options_observations_strict.csv' in text
    assert 'invalid_events_strict.csv' in text
    assert 'invalid_options_observations_strict.csv' in text


# Regression tests for data_cutoff_timestamp independent parse (Codex finding)

def test_cutoff_future_timestamp_when_feature_timestamp_missing():
    """data_cutoff_timestamp > decision_ts must emit future_feature_timestamp
    even when feature_timestamp is absent from the row.

    This is a regression test: previously the cutoff check reused the dts
    variable set inside the feature_ts block, so it silently skipped when
    feature_timestamp was missing.
    """
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as f:
        f.write('event_id,option_id,option_symbol,observation_date,event_hold_flag,gap_exposure,feature_timestamp,decision_timestamp,data_cutoff_timestamp\n')
        # feature_timestamp is absent; data_cutoff is after decision_ts
        f.write('EV-0001,opt-X,EDGE-OPT-X,2026-05-04,full_event_hold,none,,2026-05-04T09:00:00Z,2026-05-05T08:00:00Z\n')
        tmp_opts = Path(f.name)

    res = run_cli([
        '--events', str(FIXTURES / 'valid_events_minimal.csv'),
        '--options', str(tmp_opts),
        '--profile', 'minimal_fixture_profile',
        '--format', 'json',
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    codes = {b['code'] for b in out['blockers']}
    assert 'future_feature_timestamp' in codes, f"expected future_feature_timestamp, got {codes}"


def test_cutoff_future_timestamp_when_feature_timestamp_unparsable():
    """data_cutoff_timestamp > decision_ts must emit future_feature_timestamp
    even when feature_timestamp is present but unparsable.

    This verifies the cutoff check does not depend on feature_timestamp being
    successfully parsed.
    """
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as f:
        f.write('event_id,option_id,option_symbol,observation_date,event_hold_flag,gap_exposure,feature_timestamp,decision_timestamp,data_cutoff_timestamp\n')
        # feature_timestamp is unparsable; data_cutoff is after decision_ts
        f.write('EV-0001,opt-Y,EDGE-OPT-Y,2026-05-04,full_event_hold,none,not-a-timestamp,2026-05-04T09:00:00Z,2026-05-05T08:00:00Z\n')
        tmp_opts = Path(f.name)

    res = run_cli([
        '--events', str(FIXTURES / 'valid_events_minimal.csv'),
        '--options', str(tmp_opts),
        '--profile', 'minimal_fixture_profile',
        '--format', 'json',
    ])
    assert res.returncode == 1
    out = json.loads(res.stdout)
    codes = {b['code'] for b in out['blockers']}
    assert 'future_feature_timestamp' in codes, f"expected future_feature_timestamp, got {codes}"
