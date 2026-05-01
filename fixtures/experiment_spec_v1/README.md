# ExperimentSpec v1 Fixtures

**Purpose:** Support development and testing of the ExperimentSpec v1 validator.

## Taxonomy Distinction

ExperimentSpec has two independent, easily conflated fields:

| Field | Values | Meaning |
|-------|--------|---------|
| `trial_generation_mode` | `manual_grid`, `fixed_sweep`, `literature_replication`, `ablation`, `falsification`, `exploratory_agent_assisted` | **How** trials are generated |
| `allowed_trial_lanes` | `theory_first`, `exploratory_anomaly`, `post_hoc_theory`, `confirmatory` | **Which** TrialLedger `source_lane` taxonomy values are permitted |

These are **not** interchangeable. `allowed_trial_lanes` must always use the TrialLedger `source_lane` enum, NOT generation-mode values. See `docs/experiment_spec_v1_design.md` §2b for the full rationale.

---

## Valid Fixture

| File | Description |
|------|-------------|
| `valid_minimal.json` | Minimal valid ExperimentSpec v1. Uses `calendar_seasonality` study type, `literature_replication` generation mode, and `allowed_trial_lanes` correctly using the `theory_first` / `confirmatory` source-lane values. All required fields present. `prohibited_modes` fully enumerated with all fields `false`. |

---

## Invalid Fixtures

| File | Defect | Schema-Enforceable? |
|------|--------|---------------------|
| `invalid_missing_required.json` | Missing top-level required field `experiment_id` | **Yes** — JSON Schema `required` |
| `invalid_experiment_id.json` | `experiment_id` uses non-canonical format (`EXP-PA-0001` instead of `EXP-YYYY-NNNN`) | **Yes** — JSON Schema `pattern` |
| `invalid_hypothesis_id.json` | `hypothesis_id` uses non-canonical format (`HYP-PA-0001`) | **Yes** — JSON Schema `pattern` |
| `invalid_search_space_id.json` | `search_space_id` uses non-canonical format (`SSM-PA-0001`) | **Yes** — JSON Schema `pattern` |
| `invalid_study_type.json` | `study_type` is `pre_earnings_momentum`, outside the allowed enum | **Yes** — JSON Schema `enum` |
| `invalid_trial_generation_mode.json` | `trial_generation_mode` is `mechanism_discovery`, outside the allowed enum | **Yes** — JSON Schema `enum` |
| `invalid_allowed_trial_lane.json` | `allowed_trial_lanes` contains `manual_grid`, which is a generation-mode value — not a TrialLedger `source_lane` | **Yes** — JSON Schema `enum` on array items |
| `invalid_prohibited_mode_true.json` | `prohibited_modes.live_trading` is `true` — stop-rule violation | **Yes** — JSON Schema `enum: [false]` |
| `invalid_data_manifest_refs_empty.json` | `data_manifest_refs` is empty array — violates `minItems: 1` | **Yes** — JSON Schema `minItems` |
| `invalid_model_assessment_ref.json` | `model_assessment_ref` is `MAS-PA-0001`, non-canonical format | **Yes** — JSON Schema `pattern` |
| `invalid_preearnings_core_field.json` | Contains pre-earnings-specific fields `entry_dpe` and `delta_target`. ExperimentSpec is domain-neutral; these fields must not appear. | **No** — JSON Schema allows `additionalProperties: true` by default. Enforced only by the future Python validator. |

---

## Schema Enforcement Summary

**Schema-enforceable now (10 fixtures):**
All invalid fixtures except `invalid_preearnings_core_field.json` are enforceable via JSON Schema Draft-07 against `schemas/experiment_spec_v1.schema.json`.

**Future validator-only (1 fixture):**
`invalid_preearnings_core_field.json` — requires the Python validator to check for domain-neutrality (absence of pre-earnings-specific fields like `entry_dpe`, `delta_target`, `event_date`, `earnings_window`, etc.). This check is out of scope for JSON Schema alone.
