# AED Governance Contract Review — Track B Verification

**Date:** 2026-05-04
**Reviewer:** Hermes Agent (focused verification of adversarial review Track B claims)
**Main HEAD:** `fac5e1bf` — fixtures: RunnerOutputSpec v1 JSON fixtures (#142)
**Scope:** Schema/validator governance contract claims only. No engine, statistics, or broader code review.

---

## 1. Purpose

This document verifies the 12 Track B schema/validator governance contract claims made by the adversarial review (spawned 2026-05-04). Each claim is cross-checked against the actual schema JSON and validator Python. Claims are classified as **CONFIRMED**, **FALSE POSITIVE**, **INTENTIONAL_NON_BLOCKING**, or **NEEDS_DESIGN_DECISION**. Follow-up PRs are recommended only for P0/P1 items.

---

## 2. Scope and Non-Scope

**In scope:** Verification of the 12 Track B claims from the adversarial review. Schema vs. validator contract mismatches, missing `additionalProperties: false` gaps, enum mismatches, required-field gaps.

**Out of scope:** Engine code review, statistical correctness, performance issues, CI security, stop-rule implementation in engine (already verified clean in adversarial review), broader architectural concerns.

---

## 3. Summary Table

| # | Schema | Claim | Priority | Classification |
|---|---|---|---|---|
| 1 | experiment_spec_v1 | Missing top-level `additionalProperties: false` | **P0** | **CONFIRMED** |
| 2 | search_space_manifest_v1 | Missing top-level `additionalProperties: false` | **P0** | **CONFIRMED** |
| 3 | event_study_spec_v1 | Missing top-level `additionalProperties: false` | — | **FALSE POSITIVE** (present) |
| 4a | event_study_spec_v1 | `window_role` required by validator, optional in schema | **P1** | **CONFIRMED** |
| 4b | event_study_spec_v1 | `include_event_anchor` required by validator, optional in schema | **P1** | **CONFIRMED** |
| 5 | experiment_spec_v1 | `reviewer.name` described as required but not enforced by schema or validator | **P1** | **CONFIRMED** |
| 6 | edge_hypothesis_registry_v1 | `lifecycle_events.event_timestamp` and `actor` required by schema, not validated | **P1** | **CONFIRMED** |
| 7 | trial_ledger_v1 | Missing top-level `additionalProperties: false` | **P2** | **CONFIRMED** |
| 8 | model_assessment_spec_v1 | Missing top-level `additionalProperties: false` | **P2** | **CONFIRMED** |
| 9 | edge_hypothesis_registry_v1 | Missing top-level `additionalProperties: false` | **P2** | **CONFIRMED** |
| 10 | model_assessment_spec_v1 | `hypothesis_id` has no ID pattern | **P2** | **CONFIRMED** |
| 11 | options_event_risk_spec_v1 | `iv_crush` and `bmo_amc_indicator` in `BOUNDARY_FIELDS` but redundant given `additionalProperties: false` | **P3** | **INTENTIONAL_NON_BLOCKING** |
| 12 | runner_output_spec_v1 | `validated_at` missing `format: date-time` | — | **FALSE POSITIVE** (present) |

---

## 4. Detailed Claim Verification

---

### Claim 1: experiment_spec_v1 — Missing top-level `additionalProperties: false`

**Files checked:** `schemas/experiment_spec_v1.schema.json`

**Evidence:**
```python
# Python check:
>>> import json
>>> schema = json.load(open('schemas/experiment_spec_v1.schema.json'))
>>> schema.get('additionalProperties', 'NOT SET')
'NOT SET'
```

**Schema closing structure:**
```json
    "reviewer": { ... }
  }
}
```
No `additionalProperties` key at top level. Confirmed by JSCHema validation test:
```python
>>> test_exp = { ... 'live_trading_enabled': True, 'extra_evil': 'test' }
>>> jsonschema.validate(test_exp, schema)  # PASSED — extra fields accepted
```

**Classification: CONFIRMED — P0**

A governance artifact with no top-level boundary allows any extra field, including a `live_trading_enabled: True` field, to pass schema validation. The Python validator (`validate_experiment_spec.py`) does not check for extra fields either — it only validates declared fields. This is a genuine governance gap.

**Recommended action:** Add `"additionalProperties": false` to top level of `schemas/experiment_spec_v1.schema.json`. Requires care: must ensure no legitimate extra fields are used in existing valid fixtures.

---

### Claim 2: search_space_manifest_v1 — Missing top-level `additionalProperties: false`

**Files checked:** `schemas/search_space_manifest_v1.schema.json`

**Evidence:**
```python
>>> schema.get('additionalProperties', 'NOT SET')
'NOT SET'
```

**JSCHema validation test:**
```python
>>> test_ssm = { ... 'live_trading_enabled': True, 'extra_evil_field': 'test' }
>>> jsonschema.validate(test_ssm, schema)  # PASSED — extra fields accepted
```

**Classification: CONFIRMED — P0**

Same governance gap as experiment_spec_v1. A `search_space_manifest` with `live_trading_enabled: True` at the top level passes schema validation.

**Recommended action:** Add `"additionalProperties": false` to top level. Requires fixture audit — must verify no existing valid fixture uses extra fields.

---

### Claim 3: event_study_spec_v1 — Missing top-level `additionalProperties: false`

**Files checked:** `schemas/event_study_spec_v1.schema.json`

**Evidence:**
```python
>>> schema.get('additionalProperties', 'NOT SET')
False
```

**Classification: FALSE POSITIVE**

The schema **does** have `additionalProperties: false` at the top level. The adversarial review was incorrect here. The boundary IS enforced by the schema.

---

### Claim 4a: event_study_spec_v1 — `window_role` required by validator, optional in schema

**Files checked:** `schemas/event_study_spec_v1.schema.json`, `scripts/local/validate_event_study_spec.py`

**Schema (pre_event_window):**
```json
"required": ["start_offset", "end_offset", "units"],
"properties": {
  "start_offset": {...}, "end_offset": {...}, "units": {...},
  "include_event_anchor": {...},    // NOT in required
  "window_role": {...}              // NOT in required
}
```

**Validator (lines 346–367):**
```python
role_val = window.get("window_role")
if role_val is None:
    blockers.append(Blocker(..., f"{window_name}.window_role is required"))
if role_val is not None and not isinstance(role_val, str):
    blockers.append(...)
if role_val is not None and role_val == "":
    blockers.append(..., "... cannot be empty")
```

**Classification: CONFIRMED — P1**

Validator enforces `window_role` as required non-empty string for both `pre_event_window` and `post_event_window`. Schema does not require it. Any `EventStudySpec` fixture that omits `window_role` passes schema validation but fails the Python validator. This creates a fixture不一致 gap: valid fixtures that pass the validator may fail schema validation.

**Recommended action:** Add `"window_role"` to the `required` array in both `pre_event_window` and `post_event_window` objects in `schemas/event_study_spec_v1.schema.json`, OR remove the validator enforcement (schema is authoritative). Design decision required — prefer fixing schema since validator was written to enforce spec design intent.

---

### Claim 4b: event_study_spec_v1 — `include_event_anchor` required by validator, optional in schema

**Files checked:** `schemas/event_study_spec_v1.schema.json`, `scripts/local/validate_event_study_spec.py`

**Schema:** `include_event_anchor` is NOT in the `required` array for either window object.

**Validator (lines 329–343):**
```python
anchor_val = window.get("include_event_anchor")
if anchor_val is None:
    blockers.append(Blocker(..., f"{window_name}.include_event_anchor is required"))
if anchor_val is not None and not isinstance(anchor_val, bool):
    blockers.append(...)
```

**Classification: CONFIRMED — P1**

Same pattern as 4a. Validator enforces `include_event_anchor` as required boolean; schema marks it optional. Confirmed together with 4a as part of the same schema/validator parity gap.

**Recommended action:** Same as 4a — add `"include_event_anchor"` to the `required` array in both window objects.

---

### Claim 5: experiment_spec_v1 — `reviewer.name` described as required but not enforced

**Files checked:** `schemas/experiment_spec_v1.schema.json`, `scripts/local/validate_experiment_spec.py`

**Schema (reviewer):**
```json
"reviewer": {
  "description": "Human reviewer metadata for this experiment declaration. Must be an object with at minimum a name field.",
  "type": "object"
  // No required: ["name"]. No properties: {name: ...}. No additionalProperties: false.
}
```

**Validator (lines 392–399):**
```python
reviewer = entry.get("reviewer")
if reviewer is not None and not isinstance(reviewer, dict):
    blockers.append(...)  # Only checks type, not .name field
```

**JSCHema test:**
```python
>>> test_exp['reviewer'] = {'affiliation': 'test', 'extra_field': 'evil'}  # no 'name'
>>> jsonschema.validate(test_exp, schema)  # PASSED
```

**Classification: CONFIRMED — P1**

Schema description says "Must be an object with at minimum a name field" but neither the schema (no `required`, no `properties.name`) nor the validator (only checks `isinstance(dict)`) enforce this. A reviewer object with `affiliation` but no `name` passes both schema and validator.

**Recommended action:** Add `required: ["name"]` and `properties: {name: {type: string, minLength: 1}}` to the `reviewer` object in `schemas/experiment_spec_v1.schema.json`, OR update the description to match actual behavior. The schema description was the design intent; fix the schema to match.

---

### Claim 6: edge_hypothesis_registry_v1 — `lifecycle_events.event_timestamp` and `actor` required by schema, not validated

**Files checked:** `schemas/edge_hypothesis_registry_v1.schema.json`, `scripts/local/validate_edge_hypothesis_registry.py`

**Schema (lifecycle_events items):**
```json
"required": ["event_id", "event_type", "event_timestamp", "actor",
             "to_status", "manual_review_required"]
```

**Validator (lines 298–323):**
```python
for i, evt in enumerate(lce):
    if not isinstance(evt, dict): blockers...; continue
    rmm = evt.get("registry_mutation_mode")
    if rmm is not None and rmm != "manual": blockers...  # Only checks rmm
    # No check for event_timestamp, actor, event_id, event_type, to_status,
    # or manual_review_required
```

**Classification: CONFIRMED — P1**

Schema requires 6 fields per lifecycle event item. Validator only checks that `lifecycle_events` is a list of dicts and that `registry_mutation_mode` is "manual" if present. All other required fields are unvalidated.

**Recommended action:** Add lifecycle event item field validation to `validate_edge_hypothesis_registry.py` — at minimum check that `event_timestamp` (ISO8601), `actor` (non-empty string), `event_type` (non-empty string), `event_id` (ID pattern), `to_status` (enum), and `manual_review_required` (boolean) are all present and correctly typed.

---

### Claims 7–9: trial_ledger_v1, model_assessment_spec_v1, edge_hypothesis_registry_v1 — Missing top-level `additionalProperties: false`

**Files checked:** `schemas/trial_ledger_v1.schema.json`, `schemas/model_assessment_spec_v1.schema.json`, `schemas/edge_hypothesis_registry_v1.schema.json`

**Evidence for all three:**
```python
>>> for fname in ['trial_ledger_v1', 'model_assessment_spec_v1', 'edge_hypothesis_registry_v1']:
...     schema = json.load(open(f'schemas/{fname}.schema.json'))
...     print(fname, schema.get('additionalProperties', 'NOT SET'))
trial_ledger_v1 NOT SET
model_assessment_spec_v1 NOT SET
edge_hypothesis_registry_v1 NOT SET
```

**Classification: CONFIRMED — P2**

All three schemas lack top-level `additionalProperties: false`. This is a lower-severity gap than P0 because these schemas use nested `additionalProperties: true` for flexibility inside specific objects (`data_scope`, `execution_scope`, `results`, `metrics`, `required_checks`), and these objects are already protected at the schema level. However, the top-level boundary is still missing.

**Recommended action:** Add `"additionalProperties": false` to all three schemas. Lower priority than P0 because the nested flexibility objects already use explicit `additionalProperties: true` — meaning the intent IS to allow extra fields in those specific objects. Adding top-level `additionalProperties: false` is safe as long as those nested overrides are preserved.

---

### Claim 10: model_assessment_spec_v1 — `hypothesis_id` has no ID pattern

**Files checked:** `schemas/model_assessment_spec_v1.schema.json`, `scripts/local/validate_model_assessment_spec.py`

**Evidence:**
```json
"hypothesis_id": {
  "description": "Reference to the hypothesis under assessment.",
  "type": "string"
  // No pattern
},
"trial_id": {
  "description": "Reference to the TrialLedger entry.",
  "type": "string",
  "pattern": "^TRL-[0-9]{4}-[0-9]{4}$"
}
```

**Validator:** `hypothesis_id` only appears in `REQUIRED_TOP_LEVEL` (line 23) — checked for presence, not format.

**Classification: CONFIRMED — P2**

All other ID reference fields in governance schemas have pattern constraints. `hypothesis_id` is the only one without a pattern. Not a governance gap per se, but an inconsistency that could allow malformed IDs to pass.

**Recommended action:** Add `"pattern": "^HYP-[0-9]{4}-[0-9]{4}$"` to the `hypothesis_id` field in `schemas/model_assessment_spec_v1.schema.json`, consistent with the `HYP-` prefix pattern used elsewhere.

---

### Claim 11: options_event_risk_spec_v1 — `iv_crush` and `bmo_amc_indicator` in `BOUNDARY_FIELDS` but redundant given `additionalProperties: false`

**Files checked:** `schemas/options_event_risk_spec_v1.schema.json`, `scripts/local/validate_options_event_risk_spec.py`

**Evidence:**
```python
>>> schema['properties'].keys()
['options_event_risk_spec_id', 'options_event_risk_version', ...,
 'iv_crush_model_ref', ...]  # Note: 'iv_crush_model_ref', not 'iv_crush'
>>> 'iv_crush' in schema_str   # True — only as 'iv_crush_model_ref'
True
>>> 'bmo_amc_indicator' in schema_str
False
>>> schema.get('additionalProperties', 'NOT SET')
False
```

`BOUNDARY_FIELDS` in the validator includes `iv_crush` and `bmo_amc_indicator`. However:
- Neither `iv_crush` nor `bmo_amc_indicator` is a top-level property in the OER schema
- The OER schema has `additionalProperties: false` — any unexpected top-level field is already rejected
- `iv_crush` appears only as part of `iv_crush_model_ref` (a legitimate top-level ref field)
- `bmo_amc_indicator` does not appear anywhere in the schema

**Classification: INTENTIONAL_NON_BLOCKING**

These entries in `BOUNDARY_FIELDS` are redundant given `additionalProperties: false`, but not incorrect. They serve as explicit documentation in the validator that these fields must not appear. The redundancy is harmless. However, the presence of `iv_crush` in `BOUNDARY_FIELDS` is potentially misleading since `iv_crush_model_ref` (which IS a legitimate field) contains `iv_crush` as a substring.

**Recommended action:** No schema change needed. Consider removing `iv_crush` and `bmo_amc_indicator` from `BOUNDARY_FIELDS` in `validate_options_event_risk_spec.py` since `additionalProperties: false` already handles them, OR keep them for explicit documentation. Design decision.

---

### Claim 12: runner_output_spec_v1 — `validated_at` missing `format: date-time`

**Files checked:** `schemas/runner_output_spec_v1.schema.json`, `docs/runner_output_spec_v1_design.md`

**Evidence:**
```json
"validated_at": {
  "description": "ISO8601 timestamp when validation ran. Null if validation was not run.",
  "type": ["string", "null"],
  "format": "date-time"
}
```

**Design doc:** `validated_at: ISO8601 timestamp | null  # When validation ran; null if not validated`

**Classification: FALSE POSITIVE**

`validated_at` **does** have `"format": "date-time"` in the schema. The adversarial review was incorrect. The design doc expectation is satisfied by the schema.

**Note:** The `validated_at` field lives inside `audit_summary.audits[].validated_at` (nested), not at the top level of `RunnerOutputSpec`. It is also not in the `required` array for audit items (only `audit_name`, `audit_result`, `severity`, `blocker_count`, `warning_count`, `created_at` are required). The `created_at` field in audit items also has `format: date-time`. The design doc and schema are consistent.

---

## 5. Confirmed P0/P1 Items

### P0 — Governance Boundary Holes (Schema accepts extra/forbidden fields)

**P0-1: `schemas/experiment_spec_v1.schema.json` — No top-level `additionalProperties: false`**
- Evidence: A payload with `live_trading_enabled: True` and arbitrary extra fields passes `jsonschema.validate`
- Risk: Any experiment spec with a forbidden field passes schema validation
- Fix: Add `"additionalProperties": false` at top level

**P0-2: `schemas/search_space_manifest_v1.schema.json` — No top-level `additionalProperties: false`**
- Evidence: Same test — extra forbidden fields pass schema validation
- Risk: Any search space manifest with forbidden mode fields passes schema
- Fix: Add `"additionalProperties": false` at top level

### P1 — Schema/Validator Parity Gaps

**P1-1: `event_study_spec_v1` — `window_role` required by validator, optional in schema**
- Risk: Valid fixtures that pass Python validator may fail schema validation
- Fix: Add `"window_role"` to `required` array in both `pre_event_window` and `post_event_window`

**P1-2: `event_study_spec_v1` — `include_event_anchor` required by validator, optional in schema**
- Same risk and fix approach as P1-1

**P1-3: `experiment_spec_v1` — `reviewer.name` not enforced by schema or validator**
- Schema description says "Must be an object with at minimum a name field" but neither schema nor validator enforces `name`
- Fix: Add `required: ["name"]` and `properties: {name: {...}}` to `reviewer` object in schema

**P1-4: `edge_hypothesis_registry_v1` — `lifecycle_events` item fields not validated**
- Schema requires 6 fields per lifecycle event; validator checks 0 of them (only checks `registry_mutation_mode`)
- Fix: Add lifecycle event field validation to validator

---

## 6. False Positives and Non-Blocking Items

**FALSE POSITIVE — `event_study_spec_v1` additionalProperties: false:**
The schema **does** have `additionalProperties: false` at the top level. The adversarial review was incorrect.

**FALSE POSITIVE — `runner_output_spec_v1` validated_at format:**
`validated_at` **does** have `"format": "date-time"` in the schema. The adversarial review was incorrect.

**INTENTIONAL_NON_BLOCKING — `options_event_risk_spec_v1` BOUNDARY_FIELDS:**
`iv_crush` and `bmo_amc_indicator` in `BOUNDARY_FIELDS` are redundant given `additionalProperties: false` at the schema level, but serve as explicit validator documentation. Not a gap.

**P2 Items (Confirmed but lower priority):**
- `trial_ledger_v1`, `model_assessment_spec_v1`, `edge_hypothesis_registry_v1` all lack top-level `additionalProperties: false` — but these schemas use intentional nested `additionalProperties: true` for flexibility objects, so adding top-level boundary is safer but less urgent than P0
- `model_assessment_spec_v1 hypothesis_id` lacks ID pattern — inconsistency, not a governance gap

---

## 7. Recommended Follow-up PR Queue

### PR-A: Close Root `additionalProperties` Gaps (P0)
**Scope:** Add `"additionalProperties": false` to top level of:
- `schemas/experiment_spec_v1.schema.json` (P0-1)
- `schemas/search_space_manifest_v1.schema.json` (P0-2)

**Prerequisite work:**
1. Audit all valid fixtures for each schema to confirm no intentional extra fields at top level
2. If any valid fixture uses extra fields, either move them into a declared `extension_refs` field or confirm they should be removed
3. Update or confirm that `additionalProperties: true` nested overrides (where intentional, e.g., `reviewer` in instrument_universe_spec) remain and are appropriate

**Risk:** Breaking existing valid fixtures that use extra fields. Must audit fixture set first.

---

### PR-B: event_study_spec_v1 Schema/Validator Parity Fix (P1)
**Scope:**
- Add `"window_role"` to `required` array in both `pre_event_window` and `post_event_window` objects in schema
- Add `"include_event_anchor"` to `required` array in both window objects in schema
- Alternatively, remove the validator enforcement if schema is accepted as authoritative

**Prerequisite work:** Verify no valid EventStudySpec fixture omits these fields intentionally.

---

### PR-C: experiment_spec_v1 reviewer.name and nested policy Field Enforcement (P1)
**Scope:**
- Fix `reviewer` schema: add `required: ["name"]`, `properties: {name: {...}}`, `additionalProperties: false`
- OR update schema description to match actual behavior if `name` is not intended to be required
- Add nested field validation for `decision_timestamp_policy.timestamp_ref` and `feature_cutoff_policy.offset_direction` / `offset_unit` / `offset_value` if they are intended to be required

**Note:** `decision_timestamp_policy` and `feature_cutoff_policy` nested field gaps are LOW (only type-checking is done). Recommend auditing design intent before fixing.

---

### PR-D: edge_hypothesis_registry_v1 lifecycle_events Field Validation Parity (P1)
**Scope:** Add field-level validation in `validate_edge_hypothesis_registry.py` for required lifecycle event item fields: `event_timestamp` (ISO8601), `actor` (non-empty string), `event_type` (non-empty string), `event_id` (HYP-ID pattern), `to_status` (status enum), `manual_review_required` (boolean).

---

### PR-E: model_assessment_spec_v1 hypothesis_id Pattern (P2)
**Scope:** Add `"pattern": "^HYP-[0-9]{4}-[0-9]{4}$"` to `hypothesis_id` field in schema. Trivial change with no fixture impact if all existing hypothesis_id values already match this pattern.

---

### PR-F: P2 schemas — top-level `additionalProperties: false` (P2, low urgency)
**Scope:** Add `"additionalProperties": false` to:
- `schemas/trial_ledger_v1.schema.json`
- `schemas/model_assessment_spec_v1.schema.json`
- `schemas/edge_hypothesis_registry_v1.schema.json`

Safe to do after PR-A since PR-A establishes the pattern. Verify no valid fixtures use top-level extra fields.

---

## 8. Validation Commands

### Governance validators (should all pass before and after this report-only PR)
```bash
bash scripts/ci/validate_governance_manifests.sh
# Expected: 918 passed
bash scripts/ci/validate_event_options_contract.sh
# Expected: 18 passed
```

### Schema validation spot-checks
```python
# P0 governance gap confirmed:
python3 -c "
import json, jsonschema
schema = json.load(open('schemas/experiment_spec_v1.schema.json'))
test_exp = {'experiment_id': 'EXP-0001-0001', 'experiment_version': 1,
  'hypothesis_id': 'HYP-2026-0001', 'search_space_id': 'SSM-0001-0001',
  'data_manifest_refs': ['MAN-TEST-001'], 'study_type': 'calendar_seasonality',
  'experiment_family': 'equity_calendar_anomalies', 'model_assessment_ref': 'MAS-0001-0001',
  'decision_timestamp_policy': {'timestamp_ref': 'reference_date'},
  'feature_cutoff_policy': {'timestamp_ref': 'trade_date', 'offset_direction': 'before',
    'offset_unit': 'trading_days', 'offset_value': 1},
  'trial_generation_mode': 'literature_replication', 'allowed_trial_lanes': ['theory_first'],
  'prohibited_modes': {'autonomous_search': False, 'bayesian_optimization': False,
    'genetic_programming': False, 'automated_promotion': False,
    'automated_registry_mutation': False, 'live_trading': False,
    'production_execution': False, 'gcru_integration': False},
  'created_at': '2026-01-01T00:00:00Z', 'reviewer': {'name': 'test'},
  'live_trading_enabled': True, 'extra_evil': 'test'}  # extra fields
jsonschema.validate(test_exp, schema)
print('VALID — governance gap confirmed (P0)')
"
# Result: VALID — no additionalProperties boundary

python3 -c "
import json, jsonschema
schema = json.load(open('schemas/search_space_manifest_v1.schema.json'))
test_ssm = {'search_space_id': 'SSM-0001-0001', 'search_mode': 'manual_grid',
  'allowed_data_manifests': ['MAN-TEST-001'], 'allowed_features': ['f1'],
  'allowed_labels': ['l1'], 'allowed_parameter_ranges': {}, 'validation_scheme': 'holdout',
  'budget': {'max_trials': 100, 'max_parameter_combinations': 100,
    'max_runtime_minutes': 480, 'max_agent_proposals': 0},
  'forbidden_modes': {'autonomous_search': False, 'bayesian_optimization': False,
    'genetic_programming': False, 'automated_promotion': False, 'live_trading': False},
  'live_trading_enabled': True, 'extra_evil': 'test'}  # extra fields
jsonschema.validate(test_ssm, schema)
print('VALID — governance gap confirmed (P0)')
"
# Result: VALID — no additionalProperties boundary
```

### Confirmed P1 parity gaps
```python
# event_study_spec window_role — schema lacks required, validator enforces
python3 -c "
import json
schema = json.load(open('schemas/event_study_spec_v1.schema.json'))
pew = schema['properties']['pre_event_window']
print('pre_event_window required:', pew.get('required'))
print('window_role in required:', 'window_role' in pew.get('required', []))
print('include_event_anchor in required:', 'include_event_anchor' in pew.get('required', []))
"
# Result: window_role NOT in required array; include_event_anchor NOT in required

# edge_hypothesis_registry lifecycle_events — validator only checks registry_mutation_mode
python3 -c "
import json, sys; sys.path.insert(0, 'scripts/local')
schema = json.load(open('schemas/edge_hypothesis_registry_v1.schema.json'))
lce = schema['properties']['lifecycle_events']['items']
print('lifecycle_events item required:', lce.get('required'))
# Validator lines 298-323 only check registry_mutation_mode
"
# Result: 6 fields required by schema, validator checks 1
```

---

## 9. Stop-Rule and Boundary Note

**Stop-rules:** All stop-rule enforcement in schemas uses `enum: [false]` on prohibited mode fields (experiment_spec, search_space_manifest). Validators enforce `val is False` checks. This mechanism is correct and consistent. The P0 gaps are about `additionalProperties: false` absence — they affect fields NOT declared in the schema, not the declared prohibited mode fields.

**No GCRU implementation found:** `gcru_integration` in schemas uses `enum: [false]` — correctly enforcing no GCRU. The adversarial review confirmed no GCRU implementation exists anywhere in engine or scripts.

**This PR does not modify schemas, validators, tests, fixtures, workflows, engine, or registry CSV.** This is a documentation-only report. No code or governance artifact is changed.
