#!/usr/bin/env bash
set -euo pipefail

# Resolve repo root from scripts/ci/
cd "$(dirname "$0")/../.."

export PYTHONPATH="${PYTHONPATH:-.}"

# --- minimal_fixture_profile checks ---

echo "=== [minimal] valid fixtures ==="
python3 scripts/local/validate_event_options_contract.py \
  --events fixtures/event_options_contract_v1/valid_events_minimal.csv \
  --options fixtures/event_options_contract_v1/valid_options_observations_minimal.csv \
  --profile minimal_fixture_profile

echo "=== [minimal] invalid event fixtures must fail ==="
if python3 scripts/local/validate_event_options_contract.py \
  --events fixtures/event_options_contract_v1/invalid_events_edge_cases.csv \
  --options fixtures/event_options_contract_v1/valid_options_observations_minimal.csv \
  --profile minimal_fixture_profile \
  --format json 2>&1; then
  echo "BLOCKER: invalid minimal event fixtures unexpectedly passed"
  exit 1
fi

echo "=== [minimal] invalid option fixtures must fail ==="
if python3 scripts/local/validate_event_options_contract.py \
  --events fixtures/event_options_contract_v1/valid_events_minimal.csv \
  --options fixtures/event_options_contract_v1/invalid_options_observations_edge_cases.csv \
  --profile minimal_fixture_profile \
  --format json 2>&1; then
  echo "BLOCKER: invalid minimal option fixtures unexpectedly passed"
  exit 1
fi

# --- strict_contract_profile checks ---

echo "=== [strict] valid fixtures ==="
python3 scripts/local/validate_event_options_contract.py \
  --events fixtures/event_options_contract_v1/valid_events_strict.csv \
  --options fixtures/event_options_contract_v1/valid_options_observations_strict.csv \
  --profile strict_contract_profile

echo "=== [strict] invalid event fixtures must fail ==="
if python3 scripts/local/validate_event_options_contract.py \
  --events fixtures/event_options_contract_v1/invalid_events_strict.csv \
  --options fixtures/event_options_contract_v1/valid_options_observations_strict.csv \
  --profile strict_contract_profile \
  --format json 2>&1; then
  echo "BLOCKER: invalid strict event fixtures unexpectedly passed"
  exit 1
fi

echo "=== [strict] invalid option fixtures must fail ==="
if python3 scripts/local/validate_event_options_contract.py \
  --events fixtures/event_options_contract_v1/valid_events_strict.csv \
  --options fixtures/event_options_contract_v1/invalid_options_observations_strict.csv \
  --profile strict_contract_profile \
  --format json 2>&1; then
  echo "BLOCKER: invalid strict option fixtures unexpectedly passed"
  exit 1
fi

echo "=== pytest ==="
python3 -m pytest tests/test_validate_event_options_contract.py -q

echo "Event Options Contract validator checks completed."
