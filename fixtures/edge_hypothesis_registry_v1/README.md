# EdgeHypothesisRegistry v1 Fixtures

## Overview

This directory contains JSONL fixtures for EdgeHypothesisRegistry v1 validation.

## Expected Valid Fixtures

| File | Description |
|------|-------------|
| `valid_minimal.jsonl` | Minimal valid EHR entry with all required fields, canonical HYP ID, and governance fields absent or false |

## Expected Invalid Fixtures

| File | Defect | Enforced By |
|------|--------|-------------|
| `invalid_missing_required.jsonl` | Missing `status_reason` | JSON Schema — required field |
| `invalid_hypothesis_id.jsonl` | Non-canonical ID `HYP-PA-0001` | JSON Schema — `hypothesis_id` pattern |
| `invalid_status.jsonl` | `status: "promoted"` not in enum | JSON Schema — `status` enum |
| `invalid_trial_ledger_ref.jsonl` | Malformed TRL ref `TRL-PA-9999` | JSON Schema — `trial_ledger_refs` item pattern |
| `invalid_search_space_ref.jsonl` | Malformed SSM ref `SSM-PA-0001` | JSON Schema — `search_space_refs` item pattern |
| `invalid_model_assessment_ref.jsonl` | Malformed MAS ref `MAS-99-001` | JSON Schema — `model_assessment_refs` item pattern |
| `invalid_governance_true.jsonl` | `live_trading_allowed: true` | JSON Schema — `enum: [false]` |
| `invalid_registry_mutation_mode.jsonl` | `registry_mutation_mode: "automated"` | JSON Schema — `enum: ["manual"]` |
| `invalid_approved_missing_review_refs.jsonl` | `status: approved_for_next_stage` without `review_packet_refs` or `model_assessment_refs` | **Future Python validator** — JSON Schema cannot enforce cross-field dependencies |

## Schema Enforcement Summary

### JSON Schema-Enforceable (pass/fail via JSON Schema validation)
- Required field presence
- `hypothesis_id` pattern `^HYP-[0-9]{4}-[0-9]{4}$`
- `registry_version` const `edge_registry_v1`
- `status` enum
- `evidence_stage` enum
- `source_type` enum
- `source_lane` enum
- `theory_timing` enum
- `trial_ledger_refs` item pattern `^TRL-[0-9]{4}-[0-9]{4}$`
- `search_space_refs` item pattern `^SSM-[0-9]{4}-[0-9]{4}$`
- `model_assessment_refs` item pattern `^MAS-[0-9]{4}-[0-9]{4}$`
- `automated_promotion_allowed` → `enum: [false]`
- `live_trading_allowed` → `enum: [false]`
- `production_execution_allowed` → `enum: [false]`
- `automated_registry_mutation_allowed` → `enum: [false]`
- `registry_mutation_mode` in `lifecycle_events[]` → `enum: ["manual"]`

### Future Python Validator (cross-field rules JSON Schema cannot enforce)
- `approved_for_next_stage` requires `review_packet_refs` (non-empty)
- `approved_for_next_stage` requires `model_assessment_refs` (non-empty)
- `approved_for_next_stage` requires `trial_ledger_refs` (non-empty)
- `approved_for_next_stage` requires `search_space_refs` (non-empty)
- Lifecycle events are append-only (no past event modified)
- No ex-post hypothesis without `posthoc_theory_note_refs`
- Legacy ID `HYP-000N` only allowed for grandfathered entries
