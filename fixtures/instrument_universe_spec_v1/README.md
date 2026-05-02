# InstrumentUniverseSpec v1 Fixtures

## Purpose

This directory contains JSON fixtures for validating InstrumentUniverseSpec v1 records against `schemas/instrument_universe_spec_v1.schema.json`.

## Expected Valid Fixture

| File | Expected Result | Description |
|------|----------------|-------------|
| `valid_minimal.json` | **Valid** | Minimal valid InstrumentUniverseSpec v1 record with all required fields using canonical enum values and valid ID formats. |

## Expected Invalid Fixtures

| File | Expected Result | Description |
|------|----------------|-------------|
| `invalid_missing_required.json` | **Invalid** | Missing `instrument_universe_id` field entirely. |
| `invalid_instrument_universe_id.json` | **Invalid** | Malformed ID `IUS-PA-0001`; must match `^IUS-[0-9]{4}-[0-9]{4}$`. |
| `invalid_asset_classes_empty.json` | **Invalid** | `asset_classes` is an empty array; `minItems: 1` violated. |
| `invalid_asset_class_enum.json` | **Invalid** | `asset_classes` contains `"stock"` which is not in the enum. |
| `invalid_data_manifest_refs_empty.json` | **Invalid** | `data_manifest_refs` is an empty array; `minItems: 1` violated. |
| `invalid_universe_construction_policy.json` | **Invalid** | `"manual_selection"` is not a valid `universe_construction_policy` enum value. |
| `invalid_membership_timing_policy.json` | **Invalid** | `"execution_time"` is not a valid `membership_timing_policy` enum value. |
| `invalid_survivorship_policy.json` | **Invalid** | `"no_survivor_bias"` is not a valid `survivorship_policy` enum value. |
| `invalid_tradability_policy.json` | **Invalid** | `"fully_liquid"` is not a valid `tradability_policy` enum value. |
| `invalid_corporate_action_policy.json` | **Invalid** | `"fully_adjusted"` is not a valid `corporate_action_policy` enum value. |
| `invalid_rule_id.json` | **Invalid** | `inclusion_rules[0].rule_id` is `"IRL-PA-0001"`; must match `^IRL-[0-9]{4}-[0-9]{4}$`. |
| `invalid_rule_operator.json` | **Invalid** | `inclusion_rules[0].operator` is `"between"` which is not in the operator enum. |
| `invalid_liquidity_negative_min_price.json` | **Invalid** | `liquidity_policy.min_price` is `-1.0`; `minimum: 0` violated. |
| `invalid_liquidity_spread_out_of_range.json` | **Invalid** | `liquidity_policy.max_bid_ask_spread` is `1.5`; `maximum: 1` violated. |
| `invalid_liquidity_open_interest_type.json` | **Invalid** | `liquidity_policy.min_open_interest` is `false` (boolean); `type: number` violated. |
| `invalid_data_availability_coverage_out_of_range.json` | **Invalid** | `data_availability_policy.required_feature_coverage` is `1.5` and `required_outcome_coverage` is `-0.5`; bounds `[0, 1]` violated. |
| `invalid_reviewer_type.json` | **Invalid** | `reviewer` is a string; must be an object per schema. |
| `invalid_reference_array_type.json` | **Invalid** | `universe_snapshot_refs`, `runner_output_refs`, and `domain_profile_refs` are strings; must be arrays. |
| `invalid_computed_field.json` | **Invalid** (future validator) | Contains `signals` field which is a forbidden computed field per design §9. Currently passes JSON Schema due to no `additionalProperties: false` restriction; requires future Python validator to enforce. |

## Schema-Enforceable vs Future-Validator-Only

### JSON Schema-Enforceable Now (17 fixtures)

These fixtures fail against the current JSON Schema without any Python validation:

| Fixture | Schema Check That Catches It |
|---------|------------------------------|
| `invalid_missing_required.json` | `required` array — `instrument_universe_id` absent |
| `invalid_instrument_universe_id.json` | `pattern ^IUS-[0-9]{4}-[0-9]{4}$` on `instrument_universe_id` |
| `invalid_asset_classes_empty.json` | `minItems: 1` on `asset_classes` |
| `invalid_asset_class_enum.json` | `enum` constraint on `asset_classes` items |
| `invalid_data_manifest_refs_empty.json` | `minItems: 1` on `data_manifest_refs` |
| `invalid_universe_construction_policy.json` | `enum` on `universe_construction_policy` |
| `invalid_membership_timing_policy.json` | `enum` on `membership_timing_policy` |
| `invalid_survivorship_policy.json` | `enum` on `survivorship_policy` |
| `invalid_tradability_policy.json` | `enum` on `tradability_policy` |
| `invalid_corporate_action_policy.json` | `enum` on `corporate_action_policy` |
| `invalid_rule_id.json` | `pattern ^IRL-[0-9]{4}-[0-9]{4}$` on `rule_id` |
| `invalid_rule_operator.json` | `enum` on `operator` in rule objects |
| `invalid_liquidity_negative_min_price.json` | `minimum: 0` on `liquidity_policy.min_price` |
| `invalid_liquidity_spread_out_of_range.json` | `maximum: 1` on `liquidity_policy.max_bid_ask_spread` |
| `invalid_liquidity_open_interest_type.json` | `type: number` on `liquidity_policy.min_open_interest` |
| `invalid_data_availability_coverage_out_of_range.json` | `minimum: 0, maximum: 1` on coverage fields |
| `invalid_reviewer_type.json` | `type: object` on `reviewer` |
| `invalid_reference_array_type.json` | `type: array` on ref array fields |

### Future Python Validator Only (1 fixture)

| Fixture | Why Schema Doesn't Catch It |
|---------|----------------------------|
| `invalid_computed_field.json` | The schema has no `additionalProperties: false`. Extra fields like `signals` are accepted by JSON Schema validation. A future Python validator implementing the boundary rules from design §9 will catch this. |

## InstrumentUniverseSpec Boundary

Per [docs/instrument_universe_spec_v1_design.md §9](./docs/instrument_universe_spec_v1_design.md#9-boundary-what-instrumentuniversespec-does-not-own), InstrumentUniverseSpec **owns**:
- Instrument eligibility rules and universe membership declarations
- Inclusion/exclusion rules
- Liquidity, survivorship, tradability, and corporate action policies
- Data availability requirements
- Domain profile hooks

InstrumentUniverseSpec **does not own**:
- **Signals, rankings, factor scores** — runtime outputs from runners
- **PnL, realized returns** — computed by runners, belong in TrialLedger/ModelAssessmentSpec
- **`pbo_estimate`, `dsr_estimate`, `strategy_complexity_score`** — belong in ModelAssessmentSpec
- **`selected_variant_id`, `n_tried`, `trial_family_id`** — trial accounting, belong in TrialLedger
- **ReviewPacket decisions** — belong in ReviewPacket/EdgeHypothesisRegistry

The `invalid_computed_field.json` fixture exists to document this boundary and will be enforced by the future Python validator once implemented.
