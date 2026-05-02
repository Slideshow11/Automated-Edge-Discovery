# EventStudySpec v1 Fixtures

## Purpose

This directory contains JSON fixtures for validating EventStudySpec v1 records against `schemas/event_study_spec_v1.schema.json`.

## Expected Valid Fixture

| File | Expected Result | Description |
|------|----------------|-------------|
| `valid_minimal.json` | **Valid** | Minimal valid EventStudySpec v1 record with all required fields using canonical enum values and valid ID formats. Uses `earnings` event family with `before_event_publication` decision mode. |

## Expected Invalid Fixtures

| File | Expected Result | Description |
|------|----------------|-------------|
| `invalid_missing_required.json` | **Invalid** | Missing `event_study_spec_id` field entirely. |
| `invalid_event_study_spec_id.json` | **Invalid** | Malformed ID `EVS-PA-0001`; must match `^EVS-[0-9]{4}-[0-9]{4}$`. |
| `invalid_event_family.json` | **Invalid** | `event_family` is `"pre_earnings_volatility"` which is not in the enum. |
| `invalid_event_source_refs_empty.json` | **Invalid** | `event_source_refs` is an empty array; `minItems: 1` violated. |
| `invalid_event_anchor_policy.json` | **Invalid** | `"pre_event_close"` is not a valid `event_anchor_policy` enum value. |
| `invalid_event_timestamp_policy.json` | **Invalid** | `"time_required"` is not a valid `event_timestamp_policy` enum value. |
| `invalid_decision_timestamp_policy.json` | **Invalid** | `"post_event_entry"` is not a valid `decision_timestamp_policy` enum value. |
| `invalid_leakage_policy.json` | **Invalid** | `"no_lookahead"` is not a valid `leakage_policy` enum value. |
| `invalid_event_deduplication_policy.json` | **Invalid** | `"earliest_wins"` is not a valid `event_deduplication_policy` enum value. |
| `invalid_event_collision_policy.json` | **Invalid** | `"reject_collisions"` is not a valid `event_collision_policy` enum value. |
| `invalid_missing_event_time_policy.json` | **Invalid** | `"discard_ambiguous"` is not a valid `missing_event_time_policy` enum value. |
| `invalid_calendar_policy.json` | **Invalid** | `"business_days"` is not a valid `calendar_policy` enum value. |
| `invalid_pre_event_window_missing_field.json` | **Invalid** | `pre_event_window` is missing `units`; nested `required` violated. |
| `invalid_post_event_window_missing_field.json` | **Invalid** | `post_event_window` is missing `units`; nested `required` violated. |
| `invalid_window_units.json` | **Invalid** | `pre_event_window.units` is `"hours"` which is not in the enum. |
| `invalid_window_include_anchor_type.json` | **Invalid** | `pre_event_window.include_event_anchor` is `"true"` (string); must be boolean. |
| `invalid_reviewer_type.json` | **Invalid** | `reviewer` is a string; must be an object per schema. |
| `invalid_reviewer_empty_object.json` | **Invalid** | `reviewer` is an empty object `{}`; `name` field is required. |
| `invalid_outcome_spec_ref.json` | **Invalid** | `outcome_spec_refs[0]` is `"OUT-PA-0001"`; must match `^OUT-[0-9]{4}-[0-9]{4}$`. |
| `invalid_instrument_universe_ref.json` | **Invalid** | `instrument_universe_refs[0]` is `"IUS-PA-0001"`; must match `^IUS-[0-9]{4}-[0-9]{4}$`. |
| `invalid_extension_hooks_unknown_field.json` | **Invalid** | `extension_hooks` contains `pbo_estimate`; blocked by `additionalProperties: false` on extension_hooks. |
| `invalid_boundary_field.json` | **Invalid** | Contains forbidden top-level fields `delta_target`, `entry_dpe`, `pnl`, `pbo_estimate`, `review_packet_decision`; blocked by `additionalProperties: false` at schema root. |

## Schema-Enforceable vs Future-Validator-Only

### JSON Schema-Enforceable Now (22 fixtures)

These fixtures fail against the current JSON Schema without any Python validation:

| Fixture | Schema Check That Catches It |
|---------|------------------------------|
| `invalid_missing_required.json` | `required` array — `event_study_spec_id` absent |
| `invalid_event_study_spec_id.json` | `pattern ^EVS-[0-9]{4}-[0-9]{4}$` on `event_study_spec_id` |
| `invalid_event_family.json` | `enum` constraint on `event_family` |
| `invalid_event_source_refs_empty.json` | `minItems: 1` on `event_source_refs` |
| `invalid_event_anchor_policy.json` | `enum` on `event_anchor_policy` |
| `invalid_event_timestamp_policy.json` | `enum` on `event_timestamp_policy` |
| `invalid_decision_timestamp_policy.json` | `enum` on `decision_timestamp_policy` |
| `invalid_leakage_policy.json` | `enum` on `leakage_policy` |
| `invalid_event_deduplication_policy.json` | `enum` on `event_deduplication_policy` |
| `invalid_event_collision_policy.json` | `enum` on `event_collision_policy` |
| `invalid_missing_event_time_policy.json` | `enum` on `missing_event_time_policy` |
| `invalid_calendar_policy.json` | `enum` on `calendar_policy` |
| `invalid_pre_event_window_missing_field.json` | nested `required` on `pre_event_window` — `units` absent |
| `invalid_post_event_window_missing_field.json` | nested `required` on `post_event_window` — `units` absent |
| `invalid_window_units.json` | `enum` on `units` in window objects |
| `invalid_window_include_anchor_type.json` | `type: boolean` on `include_event_anchor` |
| `invalid_reviewer_type.json` | `type: object` on `reviewer` |
| `invalid_reviewer_empty_object.json` | `required: ["name"]` on `reviewer` |
| `invalid_outcome_spec_ref.json` | `pattern ^OUT-[0-9]{4}-[0-9]{4}$` on `outcome_spec_refs` items |
| `invalid_instrument_universe_ref.json` | `pattern ^IUS-[0-9]{4}-[0-9]{4}$` on `instrument_universe_refs` items |
| `invalid_extension_hooks_unknown_field.json` | `additionalProperties: false` on `extension_hooks` — `pbo_estimate` not declared |
| `invalid_boundary_field.json` | `additionalProperties: false` at root — forbidden fields not declared |

### Future Python Validator Only (0 fixtures)

*No fixtures require Python-level validation at this time. All known invalid cases are now caught by JSON Schema constraints.*

## EventStudySpec Boundary

Per [docs/event_study_spec_v1_design.md §9](./docs/event_study_spec_v1_design.md#9-boundary-what-eventstudyspec-does-not-own), EventStudySpec **owns**:
- Event identity and family declarations
- Event anchor policies and timestamp policies
- Decision timestamp policies
- Pre-event and post-event window structures
- Leakage policies
- Event deduplication, collision, and missing-timestamp policies
- Calendar policy
- Event source references and quality filters
- Domain profile hooks

EventStudySpec **does not own**:
- **Options contract selection** (`expiry_rank`, `delta_target`) — belongs in OptionsEventRiskSpec
- **Pre-earnings-specific settings** (`entry_dpe`, `exit_dpe`, `iv_crush`, `gap_exposure`) — belong in PreEarningsProfile
- **Directional signals** — runtime outputs from runners
- **Ranking scores** — runtime outputs from runners
- **`selected_variant_id`, `n_tried`, `trial_family_id`** — trial accounting, belong in TrialLedger
- **PnL** — computed by runners, belongs in TrialLedger/ModelAssessmentSpec
- **`pbo_estimate`, `dsr_estimate`** — belong in ModelAssessmentSpec
- **ReviewPacket decisions** — belong in ReviewPacket/EdgeHypothesisRegistry

The `invalid_boundary_field.json` and `invalid_extension_hooks_unknown_field.json` fixtures document this boundary. Both are now enforced by `additionalProperties: false` at the schema root and on `extension_hooks`.

## Timing Note

JSON Schema does not enforce cross-field timing inequalities. The following are the responsibility of the future Python validator:

- **Pre-event decision modes** (`before_event_publication`, `prior_session_close`, `same_session_open`): `Feature cutoff ≤ Decision timestamp < Event anchor`
- **Post-publication/post-event modes** (`after_event_publication`, `next_session_open`): `Feature cutoff ≤ Event anchor ≤ Decision timestamp`
- `start_offset` sign (pre: negative, post: ≥0) and ordering (start < end)
- `include_event_anchor` semantics and consistency
- `event_source_priority` resolution behavior
- `event_collision_policy` and `event_deduplication_policy` resolution behavior
- Event-family-specific timestamp requirements

The `valid_minimal.json` fixture uses `before_event_publication` with a pre-event window that satisfies the pre-event inequality by design. A future Python validator should test both pre-event and post-event decision modes against the respective inequalities.
