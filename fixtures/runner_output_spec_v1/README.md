# RunnerOutputSpec v1 Fixtures

This directory contains JSON fixtures for schema validation of `RunnerOutputSpec v1` as defined in `schemas/runner_output_spec_v1.schema.json`.

## Purpose

`RunnerOutputSpec v1` defines the domain-neutral runner output evidence artifact for AED governance. It records run inputs, outputs, audit outcomes, and terminal status. It is the sole durable terminal artifact emitted by an AED runner for all run outcomes — success, partial, and all failure statuses.

These fixtures exercise the full JSON Schema Draft-07 validation surface of the schema, including:

- All 17 required top-level fields
- All enum constraints (`run_mode`, `status`, `output_role`, `failure_type`, `audit_result`, `severity`, `runner_type`)
- Status-dependent `if/then/else` conditionals enforcing `failure_summary` (for failed/cancelled) and `partial_summary` (for partial)
- `failure_summary` required sub-fields (`failure_type`, `status`, `blocker_summary`, `created_at`)
- ISO8601 `date-time` format on 7 timestamp fields
- `additionalProperties: false` boundary at root level and on all sub-objects
- Nested `additionalProperties: false` on `input_artifact_refs[].items`, `audit_summary`, `audits[].items`, `output_manifest[].items`, `failure_summary`, `partial_summary`

## Valid Fixtures

| File | Status | Summary |
|---|---|---|
| `valid_success_minimal.json` | `success` | Minimal valid `success` run: all 17 required fields, `failure_summary: null`, `partial_summary: null`. |
| `valid_partial_minimal.json` | `partial` | Minimal valid `partial` run: `partial_summary` object present with all 5 properties; `failure_summary: null`. |
| `valid_failed_validation_minimal.json` | `failed_validation` | Minimal valid `failed_validation` run: `failure_summary` object present with all 4 required fields. |
| `valid_failed_missing_data_minimal.json` | `failed_missing_data` | Minimal valid `failed_missing_data` run: `failure_summary` object present with all 4 required fields. |

### Status-Conditional Fixture Matrix

| Fixture | status | failure_summary | partial_summary | Expected Validation |
|---|---|---|---|---|
| `valid_success_minimal.json` | `success` | `null` | `null` | **Valid** |
| `valid_partial_minimal.json` | `partial` | `null` | object | **Valid** |
| `valid_failed_validation_minimal.json` | `failed_validation` | object | `null` | **Valid** |
| `valid_failed_missing_data_minimal.json` | `failed_missing_data` | object | `null` | **Valid** |

## Invalid Fixtures

| File | Intent |
|---|---|
| `invalid_missing_required.json` | Missing top-level `run_id` entirely (required field absent) |
| `invalid_runner_output_id.json` | `runner_output_id: "RUN-PA-0001"` — does not match pattern `^RUN-[0-9]{4}-[0-9]{4}$` |
| `invalid_experiment_spec_ref.json` | `experiment_spec_ref: "EXP-PA-0001"` — does not match pattern `^EXP-[0-9]{4}-[0-9]{4}$` |
| `invalid_run_mode.json` | `run_mode: "live_trading"` — not in enum `[dry_run, smoke_real_data, backtest_real_data, simulation, replay, custom]` |
| `invalid_status.json` | `status: "completed"` — not in enum |
| `invalid_success_with_failure_summary.json` | `status: success` but `failure_summary` is a non-null object — caught by `if/then/else` |
| `invalid_success_with_partial_summary.json` | `status: success` but `partial_summary` is a non-null object — caught by `if/then/else` |
| `invalid_partial_missing_partial_summary.json` | `status: partial` but `partial_summary` is `null` — caught by `if/then/else` |
| `invalid_partial_with_failure_summary.json` | `status: partial` but `failure_summary` is a non-null object — caught by `if/then/else` |
| `invalid_failed_validation_missing_failure_summary.json` | `status: failed_validation` but `failure_summary` is `null` — caught by `if/then/else` |
| `invalid_failed_validation_empty_failure_summary.json` | `status: failed_validation` but `failure_summary` is `{}` — missing required sub-fields |
| `invalid_failed_missing_data_missing_failure_summary.json` | `status: failed_missing_data` but `failure_summary` is `null` — caught by `if/then/else` |
| `invalid_failure_summary_missing_required_field.json` | `failure_summary` present but `blocker_summary` missing — required sub-field absent |
| `invalid_failure_summary_bad_status.json` | `failure_summary.status` is `success` but top-level status is `failed_validation` — self-consistency (not schema-enforced) |
| `invalid_started_at_not_datetime.json` | `started_at: "not-a-datetime"` — violates `format: "date-time"` |
| `invalid_completed_at_not_datetime.json` | `completed_at: "2026-01-01"` — violates `format: "date-time"` |
| `invalid_created_at_not_datetime.json` | `created_at: "Jan 1 2026"` — violates `format: "date-time"` |
| `invalid_input_artifact_refs_empty.json` | `input_artifact_refs: []` — violates `minItems: 1` |
| `invalid_input_artifact_ref_missing_required.json` | Artifact item missing `artifact_id` — required sub-field absent |
| `invalid_input_artifact_ref_extra_field.json` | Artifact item has `extra_forbidden_field` — `additionalProperties: false` on artifact item |
| `invalid_data_manifest_refs_empty.json` | `data_manifest_refs: []` — violates `minItems: 1` |
| `invalid_audit_summary_missing_required.json` | `audit_summary` missing `overall_result` — required sub-field absent |
| `invalid_audit_summary_bad_audit_result.json` | Audit item `audit_result: "invalid_result"` — not in enum |
| `invalid_output_manifest_empty.json` | `output_manifest: []` — violates `minItems: 1` |
| `invalid_output_manifest_bad_output_role.json` | `output_role: "invalid_role"` — not in enum |
| `invalid_output_manifest_private_publishable_type.json` | `contains_private_data: "true"` (string, not boolean) — type mismatch |
| `invalid_extension_hooks_unknown_field.json` | `extension_hooks` has `pbo_estimate` — unknown field, `additionalProperties: false` violated |
| `invalid_extension_hooks_empty_ref_list.json` | `domain_profile_extension_refs: []` — violates `minItems: 1` |
| `invalid_boundary_field.json` | Root has `pbo_estimate` and `promoted_strategy_id` — `additionalProperties: false` at root violated |
| `invalid_failureoutput_field.json` | Root has `FailureOutput` object — non-existent artifact; `additionalProperties: false` at root violated |

## Schema-Enforceable vs Future-Validator-Only

### Schema-Enforceable Now (all 30 invalid fixtures)

These fixtures fail against the current JSON Schema without any Python validation:

| Fixture | Schema Check That Catches It |
|---|---|
| `invalid_missing_required.json` | `required` array — `run_id` absent |
| `invalid_runner_output_id.json` | `pattern ^RUN-[0-9]{4}-[0-9]{4}$` on `runner_output_id` |
| `invalid_experiment_spec_ref.json` | `pattern ^EXP-[0-9]{4}-[0-9]{4}$` on `experiment_spec_ref` |
| `invalid_run_mode.json` | `enum` constraint on `run_mode` |
| `invalid_status.json` | `enum` constraint on `status` |
| `invalid_success_with_failure_summary.json` | `if/then/else` — `status=success` requires `failure_summary: null` |
| `invalid_success_with_partial_summary.json` | `if/then/else` — `status=success` requires `partial_summary: null` |
| `invalid_partial_missing_partial_summary.json` | `if/then/else` — `status=partial` requires `partial_summary` object |
| `invalid_partial_with_failure_summary.json` | `if/then/else` — `status=partial` requires `failure_summary: null` |
| `invalid_failed_validation_missing_failure_summary.json` | `if/then/else` — `status=failed_validation` requires `failure_summary` object |
| `invalid_failed_validation_empty_failure_summary.json` | `required` in `failure_summary` — missing `failure_type`, `status`, `blocker_summary`, `created_at` |
| `invalid_failed_missing_data_missing_failure_summary.json` | `if/then/else` — `status=failed_missing_data` requires `failure_summary` object |
| `invalid_failure_summary_missing_required_field.json` | `required` in `failure_summary` — `blocker_summary` absent |
| `invalid_failure_summary_bad_status.json` | `enum` on `failure_summary.status` — `success` not in enum for a failed status |
| `invalid_started_at_not_datetime.json` | `format: "date-time"` on `started_at` |
| `invalid_completed_at_not_datetime.json` | `format: "date-time"` on `completed_at` |
| `invalid_created_at_not_datetime.json` | `format: "date-time"` on `created_at` |
| `invalid_input_artifact_refs_empty.json` | `minItems: 1` on `input_artifact_refs` |
| `invalid_input_artifact_ref_missing_required.json` | `required` on artifact item — `artifact_id` absent |
| `invalid_input_artifact_ref_extra_field.json` | `additionalProperties: false` on artifact item |
| `invalid_data_manifest_refs_empty.json` | `minItems: 1` on `data_manifest_refs` |
| `invalid_audit_summary_missing_required.json` | `required` in `audit_summary` — `overall_result` absent |
| `invalid_audit_summary_bad_audit_result.json` | `enum` on `audit_result` inside `audits` |
| `invalid_output_manifest_empty.json` | `minItems: 1` on `output_manifest` |
| `invalid_output_manifest_bad_output_role.json` | `enum` on `output_role` |
| `invalid_output_manifest_private_publishable_type.json` | `type: boolean` on `contains_private_data` |
| `invalid_extension_hooks_unknown_field.json` | `additionalProperties: false` inside `extension_hooks` |
| `invalid_extension_hooks_empty_ref_list.json` | `minItems: 1` on `domain_profile_extension_refs` |
| `invalid_boundary_field.json` | `additionalProperties: false` at root — `pbo_estimate`, `promoted_strategy_id` not declared |
| `invalid_failureoutput_field.json` | `additionalProperties: false` at root — `FailureOutput` not declared |

### Future Python Validator Only (0 fixtures)

All known invalid cases are caught by JSON Schema Draft-07 constraints. No Python-layer validation fixtures are defined in this directory at this time.

### Note on Date-Time Format Fixtures

`invalid_started_at_not_datetime.json`, `invalid_completed_at_not_datetime.json`, and `invalid_created_at_not_datetime.json` require a format-aware JSON Schema validator (e.g., jsonschema with `FormatChecker`) to be rejected. The Python `jsonschema` library treats `format` as non-blocking by default unless `FormatChecker` is explicitly used. These fixtures are included to document the format requirement for future validator enforcement.

## Fixture Count

- 4 valid fixtures
- 30 invalid fixtures
- **34 total fixtures**

## Boundary Summary

`RunnerOutputSpec v1` is the **sole durable terminal artifact** for an AED runner. It records evidence but does not perform any computation, optimization, or execution.

**`RunnerOutputSpec v1` does not own:**
- **`pbo_estimate`, `dsr_estimate`, `sharpe_haircut`, `overfit_discount`** — statistical assessment, belong in ModelAssessmentSpec
- **`review_packet_decision`** — review outcome, belongs in ReviewPacket/EdgeHypothesisRegistry
- **`promoted_strategy_id`** — promotion state, belongs in TrialLedger
- **`live_order_id`, `broker_order_id`** — live execution references, belong in execution layer (not AED)
- **`production_execution_endpoint`** — production execution, belongs in execution infrastructure
- **`registry_mutation_status`, `trial_ledger_mutation_status`** — mutation records, belong in their respective registries
- **`FailureOutput`** — there is no separate failure artifact; all terminal states produce exactly one `RunnerOutput`
- **`alpha_claim`** — performance claims, belong in ModelAssessmentSpec or ReviewPacket

These fields are explicitly excluded by the `additionalProperties: false` declaration at the root of the schema.

`RunnerOutputSpec v1` also does not own:
- **Event identity/timestamps** — belong in EventStudySpec
- **Option contract selection** — belong in OptionsEventRiskSpec
- **Pre-earnings timing/DPE policy** — belong in PreEarningsProfile
- **Outcome definitions** — belong in OutcomeSpec
- **Instrument universe** — belong in InstrumentUniverseSpec
- **Data resolution** — belongs in data manifests and resolver implementations
