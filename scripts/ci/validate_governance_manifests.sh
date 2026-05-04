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

# --- ExperimentSpec v1 fixture checks ---

echo "=== ExperimentSpec valid fixture ==="
python3 scripts/local/validate_experiment_spec.py \
  fixtures/experiment_spec_v1/valid_minimal.json

echo "=== ExperimentSpec invalid fixtures must fail ==="
for f in \
  fixtures/experiment_spec_v1/invalid_missing_required.json \
  fixtures/experiment_spec_v1/invalid_experiment_id.json \
  fixtures/experiment_spec_v1/invalid_hypothesis_id.json \
  fixtures/experiment_spec_v1/invalid_search_space_id.json \
  fixtures/experiment_spec_v1/invalid_study_type.json \
  fixtures/experiment_spec_v1/invalid_trial_generation_mode.json \
  fixtures/experiment_spec_v1/invalid_allowed_trial_lane.json \
  fixtures/experiment_spec_v1/invalid_prohibited_mode_true.json \
  fixtures/experiment_spec_v1/invalid_data_manifest_refs_empty.json \
  fixtures/experiment_spec_v1/invalid_model_assessment_ref.json \
  fixtures/experiment_spec_v1/invalid_preearnings_core_field.json \
  fixtures/experiment_spec_v1/invalid_missing_prohibited_mode_field.json
do
  if python3 scripts/local/validate_experiment_spec.py "$f"; then
    echo "BLOCKER: invalid ExperimentSpec fixture unexpectedly passed: $f"
    exit 1
  fi
done

# --- OutcomeSpec v1 fixture checks ---

echo "=== OutcomeSpec valid fixture ==="
python3 scripts/local/validate_outcome_spec.py \
  fixtures/outcome_spec_v1/valid_minimal.json

echo "=== OutcomeSpec invalid fixtures must fail ==="
for f in \
  fixtures/outcome_spec_v1/invalid_benchmark_policy.json \
  fixtures/outcome_spec_v1/invalid_computed_assessment_field.json \
  fixtures/outcome_spec_v1/invalid_embargo_fraction_out_of_range.json \
  fixtures/outcome_spec_v1/invalid_embargo_units.json \
  fixtures/outcome_spec_v1/invalid_evidence_role_missing_field.json \
  fixtures/outcome_spec_v1/invalid_evidence_role_non_boolean.json \
  fixtures/outcome_spec_v1/invalid_labeling_scheme.json \
  fixtures/outcome_spec_v1/invalid_metric_direction.json \
  fixtures/outcome_spec_v1/invalid_missing_required.json \
  fixtures/outcome_spec_v1/invalid_model_assessment_ref.json \
  fixtures/outcome_spec_v1/invalid_outcome_spec_id.json \
  fixtures/outcome_spec_v1/invalid_outcome_window_field_name.json \
  fixtures/outcome_spec_v1/invalid_purge_gap_days_negative.json \
  fixtures/outcome_spec_v1/invalid_return_basis.json \
  fixtures/outcome_spec_v1/invalid_reviewer_type.json \
  fixtures/outcome_spec_v1/invalid_trial_ledger_ref.json \
  fixtures/outcome_spec_v1/invalid_window_end_policy.json \
  fixtures/outcome_spec_v1/invalid_window_role.json \
  fixtures/outcome_spec_v1/invalid_window_start_policy.json \
  fixtures/outcome_spec_v1/invalid_window_unit.json
do
  if python3 scripts/local/validate_outcome_spec.py "$f"; then
    echo "BLOCKER: invalid OutcomeSpec fixture unexpectedly passed: $f"
    exit 1
  fi
done

# --- InstrumentUniverseSpec v1 fixture checks ---

echo "=== InstrumentUniverseSpec valid fixture ==="
python3 scripts/local/validate_instrument_universe_spec.py \
  fixtures/instrument_universe_spec_v1/valid_minimal.json

echo "=== InstrumentUniverseSpec invalid fixtures must fail ==="
for f in \
  fixtures/instrument_universe_spec_v1/invalid_asset_class_enum.json \
  fixtures/instrument_universe_spec_v1/invalid_asset_classes_empty.json \
  fixtures/instrument_universe_spec_v1/invalid_computed_field.json \
  fixtures/instrument_universe_spec_v1/invalid_corporate_action_policy.json \
  fixtures/instrument_universe_spec_v1/invalid_data_availability_coverage_out_of_range.json \
  fixtures/instrument_universe_spec_v1/invalid_data_manifest_refs_empty.json \
  fixtures/instrument_universe_spec_v1/invalid_instrument_universe_id.json \
  fixtures/instrument_universe_spec_v1/invalid_liquidity_negative_min_price.json \
  fixtures/instrument_universe_spec_v1/invalid_liquidity_open_interest_type.json \
  fixtures/instrument_universe_spec_v1/invalid_liquidity_spread_out_of_range.json \
  fixtures/instrument_universe_spec_v1/invalid_membership_timing_policy.json \
  fixtures/instrument_universe_spec_v1/invalid_missing_required.json \
  fixtures/instrument_universe_spec_v1/invalid_reference_array_type.json \
  fixtures/instrument_universe_spec_v1/invalid_reviewer_empty_object.json \
  fixtures/instrument_universe_spec_v1/invalid_reviewer_type.json \
  fixtures/instrument_universe_spec_v1/invalid_rule_id.json \
  fixtures/instrument_universe_spec_v1/invalid_rule_operator.json \
  fixtures/instrument_universe_spec_v1/invalid_survivorship_policy.json \
  fixtures/instrument_universe_spec_v1/invalid_tradability_policy.json \
  fixtures/instrument_universe_spec_v1/invalid_universe_construction_policy.json
do
  if python3 scripts/local/validate_instrument_universe_spec.py "$f"; then
    echo "BLOCKER: invalid InstrumentUniverseSpec fixture unexpectedly passed: $f"
    exit 1
  fi
done

# --- EventStudySpec v1 fixture checks ---

echo "=== EventStudySpec valid fixture ==="
python3 scripts/local/validate_event_study_spec.py \
  fixtures/event_study_spec_v1/valid_minimal.json

echo "=== EventStudySpec invalid fixtures must fail ==="
for f in \
  fixtures/event_study_spec_v1/invalid_boundary_field.json \
  fixtures/event_study_spec_v1/invalid_calendar_policy.json \
  fixtures/event_study_spec_v1/invalid_decision_timestamp_policy.json \
  fixtures/event_study_spec_v1/invalid_event_anchor_policy.json \
  fixtures/event_study_spec_v1/invalid_event_collision_policy.json \
  fixtures/event_study_spec_v1/invalid_event_deduplication_policy.json \
  fixtures/event_study_spec_v1/invalid_event_family.json \
  fixtures/event_study_spec_v1/invalid_event_source_refs_empty.json \
  fixtures/event_study_spec_v1/invalid_event_study_spec_id.json \
  fixtures/event_study_spec_v1/invalid_event_timestamp_policy.json \
  fixtures/event_study_spec_v1/invalid_extension_hooks_unknown_field.json \
  fixtures/event_study_spec_v1/invalid_instrument_universe_ref.json \
  fixtures/event_study_spec_v1/invalid_leakage_policy.json \
  fixtures/event_study_spec_v1/invalid_missing_event_time_policy.json \
  fixtures/event_study_spec_v1/invalid_missing_required.json \
  fixtures/event_study_spec_v1/invalid_outcome_spec_ref.json \
  fixtures/event_study_spec_v1/invalid_post_event_window_missing_field.json \
  fixtures/event_study_spec_v1/invalid_pre_event_window_missing_field.json \
  fixtures/event_study_spec_v1/invalid_reviewer_empty_object.json \
  fixtures/event_study_spec_v1/invalid_reviewer_type.json \
  fixtures/event_study_spec_v1/invalid_window_include_anchor_type.json \
  fixtures/event_study_spec_v1/invalid_window_units.json
do
  if python3 scripts/local/validate_event_study_spec.py "$f"; then
    echo "BLOCKER: invalid EventStudySpec fixture unexpectedly passed: $f"
    exit 1
  fi
done

# --- OptionsEventRiskSpec v1 fixture checks ---

echo "=== OptionsEventRiskSpec valid fixture ==="
python3 scripts/local/validate_options_event_risk_spec.py \
  fixtures/options_event_risk_spec_v1/valid_minimal.json

echo "=== OptionsEventRiskSpec invalid fixtures must fail ==="
for f in \
  fixtures/options_event_risk_spec_v1/invalid_boundary_field.json \
  fixtures/options_event_risk_spec_v1/invalid_contract_selection_policy_type.json \
  fixtures/options_event_risk_spec_v1/invalid_event_study_spec_ref.json \
  fixtures/options_event_risk_spec_v1/invalid_execution_timing_policy.json \
  fixtures/options_event_risk_spec_v1/invalid_expiry_selection_policy_type.json \
  fixtures/options_event_risk_spec_v1/invalid_extension_hooks_unknown_field.json \
  fixtures/options_event_risk_spec_v1/invalid_gap_exposure_policy.json \
  fixtures/options_event_risk_spec_v1/invalid_instrument_universe_ref.json \
  fixtures/options_event_risk_spec_v1/invalid_liquidity_policy_type.json \
  fixtures/options_event_risk_spec_v1/invalid_missing_required.json \
  fixtures/options_event_risk_spec_v1/invalid_moneyness_selection_policy_type.json \
  fixtures/options_event_risk_spec_v1/invalid_negative_numeric_threshold.json \
  fixtures/options_event_risk_spec_v1/invalid_option_side_policy.json \
  fixtures/options_event_risk_spec_v1/invalid_option_universe_policy.json \
  fixtures/options_event_risk_spec_v1/invalid_options_event_risk_spec_id.json \
  fixtures/options_event_risk_spec_v1/invalid_outcome_spec_ref.json \
  fixtures/options_event_risk_spec_v1/invalid_outcome_spec_refs_empty.json \
  fixtures/options_event_risk_spec_v1/invalid_pricing_policy_type.json \
  fixtures/options_event_risk_spec_v1/invalid_quote_quality_policy_type.json \
  fixtures/options_event_risk_spec_v1/invalid_reviewer_empty_object.json \
  fixtures/options_event_risk_spec_v1/invalid_reviewer_type.json \
  fixtures/options_event_risk_spec_v1/invalid_spread_pct_out_of_range.json \
  fixtures/options_event_risk_spec_v1/invalid_strategy_structure_policy.json
do
  if python3 scripts/local/validate_options_event_risk_spec.py "$f"; then
    echo "BLOCKER: invalid OptionsEventRiskSpec fixture unexpectedly passed: $f"
    exit 1
  fi
done

# --- PreEarningsProfile v1 fixture checks ---

echo "=== PreEarningsProfile valid fixture ==="
python3 scripts/local/validate_preearnings_profile.py \
  fixtures/preearnings_profile_v1/valid_minimal.json

echo "=== PreEarningsProfile invalid fixtures must fail ==="
for f in \
  fixtures/preearnings_profile_v1/invalid_boundary_field.json \
  fixtures/preearnings_profile_v1/invalid_earnings_time_reference.json \
  fixtures/preearnings_profile_v1/invalid_entry_dpe_policy_type.json \
  fixtures/preearnings_profile_v1/invalid_event_study_spec_ref.json \
  fixtures/preearnings_profile_v1/invalid_exit_dpe_policy_type.json \
  fixtures/preearnings_profile_v1/invalid_extension_hooks_unknown_field.json \
  fixtures/preearnings_profile_v1/invalid_gap_exposure_policy.json \
  fixtures/preearnings_profile_v1/invalid_instrument_universe_ref.json \
  fixtures/preearnings_profile_v1/invalid_iv_crush_measurement_window_missing_field.json \
  fixtures/preearnings_profile_v1/invalid_iv_crush_measurement_window_unit.json \
  fixtures/preearnings_profile_v1/invalid_iv_crush_policy_type.json \
  fixtures/preearnings_profile_v1/invalid_iv_regime_filter.json \
  fixtures/preearnings_profile_v1/invalid_live_execution_field.json \
  fixtures/preearnings_profile_v1/invalid_minimum_iv_rank_out_of_range.json \
  fixtures/preearnings_profile_v1/invalid_missing_required.json \
  fixtures/preearnings_profile_v1/invalid_options_event_risk_ref.json \
  fixtures/preearnings_profile_v1/invalid_outcome_spec_ref.json \
  fixtures/preearnings_profile_v1/invalid_outcome_spec_refs_empty.json \
  fixtures/preearnings_profile_v1/invalid_preearnings_profile_id.json \
  fixtures/preearnings_profile_v1/invalid_preearnings_profile_version.json \
  fixtures/preearnings_profile_v1/invalid_provider_table_field.json \
  fixtures/preearnings_profile_v1/invalid_reviewer_empty_object.json \
  fixtures/preearnings_profile_v1/invalid_reviewer_type.json \
  fixtures/preearnings_profile_v1/invalid_session_anchor_policy.json
do
  if python3 scripts/local/validate_preearnings_profile.py "$f"; then
    echo "BLOCKER: invalid PreEarningsProfile fixture unexpectedly passed: $f"
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
  tests/test_validate_experiment_spec.py \
  tests/test_validate_outcome_spec.py \
  tests/test_validate_instrument_universe_spec.py \
  tests/test_validate_event_study_spec.py \
  tests/test_validate_options_event_risk_spec.py \
  tests/test_validate_preearnings_profile.py \
  -q

echo "Governance manifests validator checks completed."
