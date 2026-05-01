# OutcomeSpec v1 Fixtures

## Valid Fixture

| File | Description |
|------|-------------|
| `valid_minimal.json` | Valid OutcomeSpec v1. Calendar seasonality monthly return outcome. Domain-neutral. All required fields present and correct. |

## Invalid Fixtures

| File | Invalid Case |
|------|-------------|
| `invalid_missing_required.json` | Missing `outcome_spec_id` |
| `invalid_outcome_spec_id.json` | Malformed ID `OUT-PA-0001` (does not match `^OUT-[0-9]{4}-[0-9]{4}$`) |
| `invalid_metric_direction.json` | `metric_direction: optimize` (not in enum) |
| `invalid_window_start_policy.json` | `window_start_policy: relative_start` (not in enum) |
| `invalid_window_end_policy.json` | `window_end_policy: relative_end` (not in enum) |
| `invalid_window_role.json` | `window_role: in_sample_train` (not in enum) |
| `invalid_window_unit.json` | `window_unit: hours` (not in enum — `days \| observations \| periods` only) |
| `invalid_outcome_window_field_name.json` | Uses `start_offset`/`end_offset` instead of `window_start_days`/`window_end_days` |
| `invalid_labeling_scheme.json` | `labeling_scheme: cumulative_return` (not in enum) |
| `invalid_return_basis.json` | `return_basis: net_return` (not in enum) |
| `invalid_benchmark_policy.json` | `benchmark_policy: custom_benchmark` (not in enum) |
| `invalid_evidence_role_missing_field.json` | Missing required field `requires_benchmark` inside `evidence_role_requirements` |
| `invalid_evidence_role_non_boolean.json` | `requires_oos: "true"` (string instead of boolean) |
| `invalid_purge_gap_days_negative.json` | `purge_gap_days: -1` (below minimum 0) |
| `invalid_embargo_fraction_out_of_range.json` | `embargo_fraction: 1.5` (above maximum 1) |
| `invalid_embargo_units.json` | `embargo_units: hours` (not in enum) |
| `invalid_reviewer_type.json` | `reviewer: "dr_elliot_review_2026"` (string instead of object) |
| `invalid_model_assessment_ref.json` | `MAS-PA-0001` in `model_assessment_refs` (does not match `^MAS-[0-9]{4}-[0-9]{4}$`) |
| `invalid_trial_ledger_ref.json` | `TRL-PA-0001` in `trial_ledger_refs` (does not match `^TRL-[0-9]{4}-[0-9]{4}$`) |
| `invalid_computed_assessment_field.json` | Contains `pbo_estimate` and `dsr_estimate` — computed assessment outputs that belong to ModelAssessmentSpec, not OutcomeSpec |

## JSON Schema Enforceability

These fixtures target the future OutcomeSpec v1 Python validator. The following table indicates which cases are enforceable via JSON Schema validation alone vs. require the future Python validator:

| Fixture | JSON Schema | Future Python Validator |
|---------|-------------|------------------------|
| `invalid_missing_required.json` | ✅ | |
| `invalid_outcome_spec_id.json` | ✅ | |
| `invalid_metric_direction.json` | ✅ | |
| `invalid_window_start_policy.json` | ✅ | |
| `invalid_window_end_policy.json` | ✅ | |
| `invalid_window_role.json` | ✅ | |
| `invalid_window_unit.json` | ✅ | |
| `invalid_outcome_window_field_name.json` | ❌ (schema allows `additionalProperties`) | ✅ |
| `invalid_labeling_scheme.json` | ✅ | |
| `invalid_return_basis.json` | ✅ | |
| `invalid_benchmark_policy.json` | ✅ | |
| `invalid_evidence_role_missing_field.json` | ❌ (nested `required` not validated by JSON Schema) | ✅ |
| `invalid_evidence_role_non_boolean.json` | ✅ | |
| `invalid_purge_gap_days_negative.json` | ✅ | |
| `invalid_embargo_fraction_out_of_range.json` | ✅ | |
| `invalid_embargo_units.json` | ✅ | |
| `invalid_reviewer_type.json` | ✅ | |
| `invalid_model_assessment_ref.json` | ✅ | |
| `invalid_trial_ledger_ref.json` | ✅ | |
| `invalid_computed_assessment_field.json` | ❌ (schema allows `additionalProperties`) | ✅ |

## Boundary: What OutcomeSpec Owns vs. Does Not Own

**OutcomeSpec owns:** measurement/window/evidence-role declarations.

**OutcomeSpec does NOT own** computed overfit, assessment, search-pressure, or ReviewPacket decision outputs. Specifically:

| Field | Owner |
|-------|-------|
| `pbo_estimate` | ModelAssessmentSpec |
| `dsr_estimate` | ModelAssessmentSpec |
| `backtest_pnl_haircut` | ModelAssessmentSpec |
| `overfit_discount_factor` | ModelAssessmentSpec |
| `adjusted_expected_oos_sharpe` | ModelAssessmentSpec |
| `probability_of_loss` | ModelAssessmentSpec |
| `false_discovery_rate_estimate` | ModelAssessmentSpec |
| `strategy_complexity_score` | ModelAssessmentSpec |
| `factor_exposure_stability_check` | ModelAssessmentSpec |
| `null_model_performance` | ModelAssessmentSpec or Runner |
| `performance_vs_null` | ModelAssessmentSpec or Runner |
| `n_tried` | TrialLedger or ExperimentSpec |
| `trial_family_id` | TrialLedger |
| `selected_variant_id` | TrialLedger or ExperimentSpec |
| ReviewPacket `decision` | ReviewPacket |

`invalid_computed_assessment_field.json` demonstrates this boundary by including `pbo_estimate` and `dsr_estimate`, which must be rejected by the future validator.

## Naming Contract: `outcome_window`

The `outcome_window` object **must** use:
- `window_start_days` (integer)
- `window_end_days` (integer)
- `window_unit` (enum: `days | observations | periods`)
- `anchor` (string)

**Must NOT use:**
- `start_offset` / `end_offset` — these are not defined in the schema

`invalid_outcome_window_field_name.json` demonstrates this violation. The schema does not yet enforce this via JSON Schema (no `additionalProperties: false`), but the Python validator must catch it.

## Enum Contract: `window_unit`

`window_unit` enum values: `days | observations | periods`

**`hours` is not a valid core enum value.** `invalid_window_unit.json` includes `window_unit: hours` and must fail validation. Intraday windows use `periods` with the understanding that one period represents one bar as defined by the data manifest or domain profile.
