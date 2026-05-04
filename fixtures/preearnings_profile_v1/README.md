# PreEarningsProfile v1 Fixtures

This directory contains JSON fixtures for schema validation of `PreEarningsProfile v1` as defined in `schemas/preearnings_profile_v1.schema.json`.

## Purpose

`PreEarningsProfile v1` defines domain-specific pre-earnings research configuration for US equity options: BMO/AMC session semantics, DPE (Days to Earnings) targeting, earnings-specific gap-exposure rules, and IV crush policy for options event-risk experiments. These fixtures exercise the full JSON Schema Draft-07 validation surface of the profile.

## Valid Fixture

### `valid_minimal.json`

A valid `PreEarningsProfile` that passes the JSON Schema directly. Contains all 12 required fields and represents a **conservative AMC pre-earnings no-gap IV-ramp profile**:

- **Profile ID:** `PEP-2026-0001` (format: `PEP-YYYY-NNNN`)
- **Session anchor:** `amc_only` — After Market Close earnings
- **Earnings time reference:** `after_hours_only`
- **Entry DPE policy:** DPE 1–5, trading days, earnings date anchor
- **Exit DPE policy:** DPE 0–2, trading days, earnings date anchor
- **IV crush policy:** measurement window DPE −1 to +5, `percent_iv_drop` definition
- **Gap exposure policy:** `prohibit_gap_hold` — no overnight/announcement-gap hold

This fixture is a valid, reviewable, commit-ready declaration suitable for an AMC-only conservative pre-earnings experiment design.

## Invalid Fixture Inventory

| Fixture File | One-Line Description |
|---|---|
| `invalid_missing_required.json` | Missing `preearnings_profile_id` entirely (required top-level field) |
| `invalid_preearnings_profile_id.json` | `preearnings_profile_id` is `"PEP-PA-0001"` — wrong format (pattern requires `PEP-YYYY-NNNN`) |
| `invalid_preearnings_profile_version.json` | `preearnings_profile_version` is `0` — must be ≥ 1 |
| `invalid_event_study_spec_ref.json` | `event_study_spec_ref` is `"EVS-PA-0001"` — wrong format |
| `invalid_options_event_risk_ref.json` | `options_event_risk_ref` is `"OER-PA-0001"` — wrong format |
| `invalid_session_anchor_policy.json` | `session_anchor_policy` is `"bm_announcement"` — not in enum |
| `invalid_earnings_time_reference.json` | `earnings_time_reference` is `"extended_hours_only"` — not in enum |
| `invalid_entry_dpe_policy_type.json` | `entry_dpe_policy` is `"not_an_object"` — must be object |
| `invalid_exit_dpe_policy_type.json` | `exit_dpe_policy` is `"not_an_object"` — must be object |
| `invalid_iv_crush_policy_type.json` | `iv_crush_policy` is `"not_an_object"` — must be object |
| `invalid_iv_crush_measurement_window_missing_field.json` | `iv_crush_measurement_window` has only `start` — missing `end` and `unit` (both required) |
| `invalid_iv_crush_measurement_window_unit.json` | `iv_crush_measurement_window.unit` is `"hours"` — not in enum (allowed: `dpe`, `sessions`, `calendar_days`) |
| `invalid_gap_exposure_policy.json` | `gap_exposure_policy` is `"hold_overnight"` — not in enum |
| `invalid_reviewer_type.json` | `reviewer` is `"not_an_object"` — must be object |
| `invalid_reviewer_empty_object.json` | `reviewer` is `{}` — missing required `name` field |
| `invalid_instrument_universe_ref.json` | `instrument_universe_ref` is `"IUS-PA-0001"` — wrong format (optional field failure) |
| `invalid_outcome_spec_ref.json` | `outcome_spec_refs` contains `"OUT-PA-0001"` — wrong format (optional array item failure) |
| `invalid_outcome_spec_refs_empty.json` | `outcome_spec_refs` is `[]` — violates `minItems: 1` (schema requires at least one outcome spec ref) |
| `invalid_minimum_iv_rank_out_of_range.json` | `minimum_iv_rank` is `1.5` — exceeds maximum of `1.0` |
| `invalid_iv_regime_filter.json` | `iv_regime_filter` is `"iv_mid_only"` — not in enum |
| `invalid_extension_hooks_unknown_field.json` | `extension_hooks` contains `"pbo_estimate": 0.05` — unknown field (extension_hooks uses `additionalProperties: false`) |
| `invalid_boundary_field.json` | Top-level has `"pbo_estimate": 0.05` — boundary/computed field forbidden at root |
| `invalid_live_execution_field.json` | Top-level has `"live_trading_enabled": true` — live/prod execution field not in schema |
| `invalid_provider_table_field.json` | Top-level has `"provider_table_name": "ivol_db.dbo.earnings"` — provider storage field not in schema |

## Schema-Enforceable Invalid Fixtures (JSON Schema Draft-07)

The following fixtures are caught **directly by JSON Schema Draft-07** validation rules (no Python/enrichment validator needed):

- `invalid_missing_required` — missing required field
- `invalid_preearnings_profile_id` — `pattern` mismatch
- `invalid_preearnings_profile_version` — `minimum` constraint
- `invalid_event_study_spec_ref` — `pattern` mismatch
- `invalid_options_event_risk_ref` — `pattern` mismatch
- `invalid_session_anchor_policy` — `enum` mismatch
- `invalid_earnings_time_reference` — `enum` mismatch
- `invalid_entry_dpe_policy_type` — `type` mismatch (expected object)
- `invalid_exit_dpe_policy_type` — `type` mismatch (expected object)
- `invalid_iv_crush_policy_type` — `type` mismatch (expected object)
- `invalid_iv_crush_measurement_window_missing_field` — missing required sub-fields in nested object
- `invalid_iv_crush_measurement_window_unit` — `enum` mismatch in nested object
- `invalid_gap_exposure_policy` — `enum` mismatch
- `invalid_reviewer_type` — `type` mismatch (expected object)
- `invalid_reviewer_empty_object` — missing required `name` within `reviewer` object
- `invalid_instrument_universe_ref` — `pattern` mismatch
- `invalid_outcome_spec_ref` — `pattern` mismatch on array item
- `invalid_outcome_spec_refs_empty` — `minItems: 1` constraint
- `invalid_minimum_iv_rank_out_of_range` — `maximum: 1` constraint
- `invalid_iv_regime_filter` — `enum` mismatch
- `invalid_extension_hooks_unknown_field` — `additionalProperties: false` in extension_hooks
- `invalid_boundary_field` — `additionalProperties: false` at root level
- `invalid_live_execution_field` — `additionalProperties: false` at root level
- `invalid_provider_table_field` — `additionalProperties: false` at root level

## Future Python Validator (Cross-Field Semantic Rules)

All invalid fixtures listed above are enforceable directly via JSON Schema Draft-07. There are **no fixtures in this directory intended for a future Python-layer validator only** at this time. Cross-field semantic constraints (e.g., `entry_dpe_min ≤ entry_dpe_max`, `exit_dpe_max ≥ exit_dpe_min`, anchor consistency between `entry_dpe_policy` and `exit_dpe_policy`) are not yet encoded in the JSON Schema and would be candidates for a future Python-level enrichment validator.

## Boundary Summary

`PreEarningsProfile v1` is a **design-time declaration**. It does not own computed assessment outputs (`pbo_estimate`, `dsr_estimate`, `sharpe_haircut`, `overfit_discount`), runtime signals, option selections, greeks values, rankings, live execution controls, or provider storage references. These boundary fields are explicitly forbidden by the `additionalProperties: false` declaration at the root of the schema.

`PreEarningsProfile v1` also does not own event identity/timestamp resolution (delegated to `EventStudySpec`), option contract selection (delegated to `OptionsEventRiskSpec`), or outcome measurement (delegated to `OutcomeSpec`).

## Note: `iv_crush_measurement_window`

The `iv_crush_measurement_window` sub-object requires **all three fields**: `start` (integer, DPE at window start), `end` (integer, DPE at window end), and `unit` (enum: `dpe`, `sessions`, or `calendar_days`). Omitting any of these fields or using an invalid unit value will fail schema validation.
