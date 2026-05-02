# OptionsEventRiskSpec v1 Fixtures

## Purpose

This directory contains JSON fixtures for validating OptionsEventRiskSpec v1 records against `schemas/options_event_risk_spec_v1.schema.json`.

## Expected Valid Fixture

| File | Expected Result | Description |
|------|----------------|-------------|
| `valid_minimal.json` | **Valid** | Minimal valid OptionsEventRiskSpec v1 record with all 18 required fields using canonical enum values and valid ID formats. Uses `listed_equity_options`, `delta_bucket` contract selection, `single_leg` strategy structure, and `exit_before_event_anchor` gap policy. |

## Expected Invalid Fixtures

| File | Expected Result | Description |
|------|----------------|-------------|
| `invalid_missing_required.json` | **Invalid** | Missing `options_event_risk_spec_id` field entirely. |
| `invalid_options_event_risk_spec_id.json` | **Invalid** | Malformed ID `OER-PA-0001`; must match `^OER-[0-9]{4}-[0-9]{4}$`. |
| `invalid_event_study_spec_ref.json` | **Invalid** | Malformed EVS ref `EVS-PA-0001`; must match `^EVS-[0-9]{4}-[0-9]{4}$`. |
| `invalid_instrument_universe_ref.json` | **Invalid** | Malformed IUS ref `IUS-PA-0001`; must match `^IUS-[0-9]{4}-[0-9]{4}$`. |
| `invalid_outcome_spec_refs_empty.json` | **Invalid** | `outcome_spec_refs` is an empty array; `minItems: 1` violated. |
| `invalid_outcome_spec_ref.json` | **Invalid** | `outcome_spec_refs` contains malformed `OUT-PA-0001`; must match `^OUT-[0-9]{4}-[0-9]{4}$`. |
| `invalid_option_universe_policy.json` | **Invalid** | `option_universe_policy` is `listed_stock_options`; not in the enum. |
| `invalid_contract_selection_policy_type.json` | **Invalid** | `contract_selection_policy` is a string `delta_bucket`; must be an object. |
| `invalid_expiry_selection_policy_type.json` | **Invalid** | `expiry_selection_policy` is a string `nearest_after_event`; must be an object. |
| `invalid_moneyness_selection_policy_type.json` | **Invalid** | `moneyness_selection_policy` is a string `delta_targeted`; must be an object. |
| `invalid_option_side_policy.json` | **Invalid** | `option_side_policy` is `options_only`; not in the enum. |
| `invalid_strategy_structure_policy.json` | **Invalid** | `strategy_structure_policy` is `straddle`; not in the `strategy_structure_policy` enum — straddle belongs under `option_side_policy`. |
| `invalid_liquidity_policy_type.json` | **Invalid** | `liquidity_policy` is a string; must be an object. |
| `invalid_pricing_policy_type.json` | **Invalid** | `pricing_policy` is a string `"mid"`; must be an object. `fill_price_basis` carries pricing values like `mid` inside the `pricing_policy` object. |
| `invalid_quote_quality_policy_type.json` | **Invalid** | `quote_quality_policy` is a string; must be an object. |
| `invalid_execution_timing_policy.json` | **Invalid** | `execution_timing_policy` is `at_settlement`; not in the enum. |
| `invalid_gap_exposure_policy.json` | **Invalid** | `gap_exposure_policy` is `overnight_hold_allowed`; not in the enum. |
| `invalid_reviewer_type.json` | **Invalid** | `reviewer` is a string; must be an object per schema. |
| `invalid_reviewer_empty_object.json` | **Invalid** | `reviewer` is an empty object `{}`; `name` field is required. |
| `invalid_negative_numeric_threshold.json` | **Invalid** | Multiple numeric thresholds are negative (`contract_count_limit: -3`, `min_option_price: -0.05`, `min_open_interest: -50`, `spread_penalty_bps: -10`, `max_quote_age_seconds: -30`); `minimum: 0` violated. |
| `invalid_spread_pct_out_of_range.json` | **Invalid** | `liquidity_policy.max_bid_ask_spread_pct` is `1.5` and `quote_quality_policy.min_spread_pct` is `-0.05`; bounds `[0, 1]` violated. |
| `invalid_extension_hooks_unknown_field.json` | **Invalid** | `extension_hooks` contains `pbo_estimate` which is not a declared property; `additionalProperties: false` inside `extension_hooks` violated. |
| `invalid_boundary_field.json` | **Invalid** | Contains forbidden top-level fields (`selected_variant_id`, `pbo_estimate`, `dsr_estimate`, `entry_dpe`, `exit_dpe`, `bmo_amc_indicator`); `additionalProperties: false` at root violated. |

## Schema-Enforceable vs Future-Validator-Only

### JSON Schema-Enforceable Now (all 23 invalid fixtures)

These fixtures fail against the current JSON Schema without any Python validation:

| Fixture | Schema Check That Catches It |
|---------|------------------------------|
| `invalid_missing_required.json` | `required` array — `options_event_risk_spec_id` absent |
| `invalid_options_event_risk_spec_id.json` | `pattern ^OER-[0-9]{4}-[0-9]{4}$` on `options_event_risk_spec_id` |
| `invalid_event_study_spec_ref.json` | `pattern ^EVS-[0-9]{4}-[0-9]{4}$` on `event_study_spec_ref` |
| `invalid_instrument_universe_ref.json` | `pattern ^IUS-[0-9]{4}-[0-9]{4}$` on `instrument_universe_ref` |
| `invalid_outcome_spec_refs_empty.json` | `minItems: 1` on `outcome_spec_refs` |
| `invalid_outcome_spec_ref.json` | `pattern ^OUT-[0-9]{4}-[0-9]{4}$` on items in `outcome_spec_refs` |
| `invalid_option_universe_policy.json` | `enum` constraint on `option_universe_policy` |
| `invalid_contract_selection_policy_type.json` | `type: object` on `contract_selection_policy` |
| `invalid_expiry_selection_policy_type.json` | `type: object` on `expiry_selection_policy` |
| `invalid_moneyness_selection_policy_type.json` | `type: object` on `moneyness_selection_policy` |
| `invalid_option_side_policy.json` | `enum` constraint on `option_side_policy` |
| `invalid_strategy_structure_policy.json` | `enum` constraint on `strategy_structure_policy` — `straddle` is not a valid value |
| `invalid_liquidity_policy_type.json` | `type: object` on `liquidity_policy` |
| `invalid_pricing_policy_type.json` | `type: object` on `pricing_policy` |
| `invalid_quote_quality_policy_type.json` | `type: object` on `quote_quality_policy` |
| `invalid_execution_timing_policy.json` | `enum` constraint on `execution_timing_policy` |
| `invalid_gap_exposure_policy.json` | `enum` constraint on `gap_exposure_policy` |
| `invalid_reviewer_type.json` | `type: object` on `reviewer` |
| `invalid_reviewer_empty_object.json` | `required: ["name"]` on `reviewer` |
| `invalid_negative_numeric_threshold.json` | `minimum: 0` on `contract_count_limit`, `min_option_price`, `min_open_interest`, `spread_penalty_bps`, `max_quote_age_seconds` |
| `invalid_spread_pct_out_of_range.json` | `minimum: 0, maximum: 1` on `max_bid_ask_spread_pct` and `min_spread_pct` |
| `invalid_extension_hooks_unknown_field.json` | `additionalProperties: false` inside `extension_hooks` — `pbo_estimate` not declared |
| `invalid_boundary_field.json` | `additionalProperties: false` at root — forbidden fields not declared |

### Future Python Validator Only (0 fixtures)

*No fixtures require Python-level validation at this time. All known invalid cases are caught by JSON Schema constraints.*

## OptionsEventRiskSpec Boundary

Per [docs/options_event_risk_spec_v1_design.md §10](./docs/options_event_risk_spec_v1_design.md#10-boundary-what-optionseventriskspec-does-not-own), OptionsEventRiskSpec **owns**:
- Option universe policy (asset class)
- Contract selection (delta, moneyness, expiry, strike, premium)
- Option side and strategy structure
- Liquidity requirements and quote-quality policies
- Pricing and execution policy
- Gap exposure policy
- Greeks, IV, skew, hedge policies
- Corporate action, assignment/exercise policies

OptionsEventRiskSpec **does not own**:
- **Event identity, timestamps, anchor policies** — belong in EventStudySpec
- **Underlying instrument eligibility** — belong in InstrumentUniverseSpec
- **Final outcome definitions** — belong in OutcomeSpec
- **`selected_variant_id`, `n_tried`, `trial_family_id`** — trial accounting, belong in TrialLedger/ExperimentSpec
- **`pbo_estimate`, `dsr_estimate`, `sharpe_haircut`, `overfit_discount`** — statistical assessment, belong in ModelAssessmentSpec
- **ReviewPacket decisions** — belong in ReviewPacket/EdgeHypothesisRegistry
- **BMO/AMC session semantics, DPE targeting** — belong in PreEarningsProfile
- **iVolatility or provider-specific table names** — belong in data manifests/domain profiles

## Strategy/Policy Structure Notes

### straddle belongs under option_side_policy, not strategy_structure_policy

`straddle` is a valid `option_side_policy` value (it describes which option sides are included). It is **not** a valid `strategy_structure_policy` value. `strategy_structure_policy` describes the broader construction family:

- `strategy_structure_policy` enum values: `single_leg`, `two_leg_spread`, `multi_leg_spread`, `delta_neutral`, `volatility_structure`, `custom`
- `option_side_policy` enum values: `calls_only`, `puts_only`, `calls_and_puts`, `straddle`, `strangle`, `vertical_spread`, `calendar_spread`, `custom`

The macro example (11b) in the design doc was corrected to use `option_side_policy: straddle` and `strategy_structure_policy: volatility_structure`.

### pricing_policy is an object — fill_price_basis carries the enum values

`pricing_policy` is a **top-level object** (required), not an enum. The pricing basis values (`mid`, `bid`, `ask`, `conservative_fill`, `spread_penalized_mid`, `custom`) are declared as the `fill_price_basis` field **inside** the `pricing_policy` object. The `pricing_policy` object may also carry `spread_penalty_bps`, `commission_model_ref`, `slippage_model_ref`, `quote_timestamp_policy`, `entry_quote_policy`, `exit_quote_policy`, `partial_fill_policy`, and `multi_leg_execution_policy` as optional fields.

This distinction matters: `invalid_pricing_policy_type.json` has `pricing_policy: "mid"` which is caught by `type: object`. The correct form is `pricing_policy: { "fill_price_basis": "mid" }`.

### All policy objects must be objects

`contract_selection_policy`, `expiry_selection_policy`, `moneyness_selection_policy`, `liquidity_policy`, `pricing_policy`, and `quote_quality_policy` are all **required objects** per the schema. Fixtures like `invalid_contract_selection_policy_type.json`, `invalid_pricing_policy_type.json`, etc. test this contract.

## Fixture Count

- 1 valid fixture
- 23 invalid fixtures
- 24 total fixtures
