#!/usr/bin/env bash
set -euo pipefail

# Resolve repo root from scripts/ci/
cd "$(dirname "$0")/../.."

export PYTHONPATH="${PYTHONPATH:-.}"

# --- TrialLedger v1 fixture checks ---

echo "=== TrialLedger valid fixture ==="
python3 scripts/local/validate_trial_ledger.py \
  fixtures/trial_ledger_v1/valid_trial_ledger_entry.json

echo "=== TrialLedger invalid fixtures must fail ==="
for f in \
  fixtures/trial_ledger_v1/invalid_missing_trial_id.json \
  fixtures/trial_ledger_v1/invalid_bad_source_lane.json \
  fixtures/trial_ledger_v1/invalid_bad_promotion_acceptance.json \
  fixtures/trial_ledger_v1/invalid_bad_search_space_id.json
do
  if python3 scripts/local/validate_trial_ledger.py "$f"; then
    echo "BLOCKER: invalid TrialLedger fixture unexpectedly passed: $f"
    exit 1
  fi
done

# --- SearchSpaceManifest v1 fixture checks ---

echo "=== SearchSpaceManifest valid fixture ==="
python3 scripts/local/validate_search_space_manifest.py \
  fixtures/search_space_manifest_v1/valid_search_space_manifest.json

echo "=== SearchSpaceManifest invalid fixtures must fail ==="
for f in \
  fixtures/search_space_manifest_v1/invalid_missing_search_space_id.json \
  fixtures/search_space_manifest_v1/invalid_bad_search_mode.json \
  fixtures/search_space_manifest_v1/invalid_forbidden_mode_enabled.json \
  fixtures/search_space_manifest_v1/invalid_bad_budget.json \
  fixtures/search_space_manifest_v1/invalid_empty_data_manifests.json
do
  if python3 scripts/local/validate_search_space_manifest.py "$f"; then
    echo "BLOCKER: invalid SearchSpaceManifest fixture unexpectedly passed: $f"
    exit 1
  fi
done

# --- ModelAssessmentSpec v1 fixture checks ---

echo "=== ModelAssessmentSpec valid fixture ==="
python3 scripts/local/validate_model_assessment_spec.py \
  fixtures/model_assessment_spec_v1/valid_model_assessment_spec.json

echo "=== ModelAssessmentSpec invalid fixtures must fail ==="
for f in \
  fixtures/model_assessment_spec_v1/invalid_missing_assessment_id.json \
  fixtures/model_assessment_spec_v1/invalid_bad_status.json \
  fixtures/model_assessment_spec_v1/invalid_missing_required_checks.json \
  fixtures/model_assessment_spec_v1/invalid_accepted_without_required_evidence.json \
  fixtures/model_assessment_spec_v1/invalid_bad_metric_value.json
do
  if python3 scripts/local/validate_model_assessment_spec.py "$f"; then
    echo "BLOCKER: invalid ModelAssessmentSpec fixture unexpectedly passed: $f"
    exit 1
  fi
done

# --- EdgeHypothesisRegistry v1 fixture checks ---

echo "=== EdgeHypothesisRegistry valid fixture ==="
python3 scripts/local/validate_edge_hypothesis_registry.py \
  fixtures/edge_hypothesis_registry_v1/valid_minimal.jsonl

echo "=== EdgeHypothesisRegistry invalid fixtures must fail ==="
for f in \
  fixtures/edge_hypothesis_registry_v1/invalid_approved_missing_review_refs.jsonl \
  fixtures/edge_hypothesis_registry_v1/invalid_governance_true.jsonl \
  fixtures/edge_hypothesis_registry_v1/invalid_hypothesis_id.jsonl \
  fixtures/edge_hypothesis_registry_v1/invalid_missing_required.jsonl \
  fixtures/edge_hypothesis_registry_v1/invalid_model_assessment_ref.jsonl \
  fixtures/edge_hypothesis_registry_v1/invalid_registry_mutation_mode.jsonl \
  fixtures/edge_hypothesis_registry_v1/invalid_search_space_ref.jsonl \
  fixtures/edge_hypothesis_registry_v1/invalid_status.jsonl \
  fixtures/edge_hypothesis_registry_v1/invalid_trial_ledger_ref.jsonl
do
  if python3 scripts/local/validate_edge_hypothesis_registry.py "$f"; then
    echo "BLOCKER: invalid EdgeHypothesisRegistry fixture unexpectedly passed: $f"
    exit 1
  fi
done

# --- pytest governance validator tests ---

echo "=== pytest governance validators ==="
python3 -m pytest \
  tests/test_validate_trial_ledger.py \
  tests/test_validate_search_space_manifest.py \
  tests/test_validate_model_assessment_spec.py \
  tests/test_validate_edge_hypothesis_registry.py \
  -q

echo "Governance manifests validator checks completed."
