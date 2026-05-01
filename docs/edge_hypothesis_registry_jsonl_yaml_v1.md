# EdgeHypothesisRegistry JSONL/YAML v1 


## 1. Purpose

The manual CSV registry v1 is a useful human-editable and historical artifact but
is not a long-term machine-readable system of record. The EdgeHypothesisRegistry
JSONL/YAML v1 is the planned long-term registry format designed for per-record
provenance, nested fields, and explicit links to other AED artifacts.

This PR is a docs-only design. It preserves manual review and does not
authorize any automated registry mutation. No migration of the CSV is performed
in this PR. (No migration happens in this PR)

Important: docs/edge_hypothesis_registry.csv is not modified in this PR 

(docs/edge_hypothesis_registry.csv is not modified in this PR)

## 2. Current registry v1 (CSV summary)

The current CSV fields found in docs/edge_hypothesis_registry.csv are:

- hypothesis_id
- title
- status
- evidence_stage
- source_type
- mechanism
- primary_dataset
- point_in_time_controls
- leakage_risks
- falsification_tests
- promotion_restrictions
- owner
- created_at
- updated_at
- notes

State: registry CSV is manual v1 only. It remains valid as a manual snapshot and
may later be exported from JSONL/YAML if needed.

## 3. Why JSONL/YAML

Benefits of a structured JSONL/YAML registry:

- stable per-record diffs and append-only histories
- nested provenance and arrays for evidence and references
- explicit lifecycle events and audit trails
- links to other AED artifacts (TrialLedger, SearchSpaceManifest, MechanismDiscoveryReport, ReviewPacket, ManualDecision)
- support for optional fields without widening CSV columns
- easier later schema validation and CI enforcement
- better handling of arrays, evidence lists, and nested objects
- improved review-packet integration and machine processing

JSONL vs YAML guidance:
- JSONL is preferable as a canonical, append-only, machine-friendly format.
- YAML is preferable for human-authored review records and richer comments.
- v1 design supports both, but JSONL should be chosen as canonical for tooling.
(JSONL should be canonical)

Recommended stance:
- JSONL should be canonical for future machine processing; YAML may be used
  as a human-authoring/export format. YAML may be accepted as a review export.
- CSV remains manual v1 and historical.

## 4a. Hypothesis ID format

The hypothesis_id format is `HYP-YYYY-NNNN`:

- `HYP-` prefix (uppercase, fixed)
- `YYYY` = four-digit year
- `NNNN` = four-digit zero-padded sequential number within the year

Examples: `HYP-2026-0001`, `HYP-2026-0042`, `HYP-2025-0099`

Sequential numbers are assigned manually at creation. There is no automated ID generation.

Legacy IDs from the CSV registry (e.g. `HYP-0001`, `HYP-0002`) are preserved as-is for backward compatibility. New records must use the `HYP-YYYY-NNNN` format.

Version field: `registry_version` must be set to `edge_registry_v1` for all new records.

Each EdgeHypothesisRecord (JSON object or YAML document) SHOULD include:

- hypothesis_id 
- registry_version 
- title 
- status 
- status_reason 
- evidence_stage 
- source_type 
- source_lane 
- theory_timing 

(see full doc for complete field list)

Key reference fields (must exist as ids/refs): hypothesis_card_ref, trial_ledger_refs, search_space_refs, model_assessment_refs, mechanism_report_refs, posthoc_theory_note_refs, review_packet_refs, manual_decision_refs

## 5. Status model

Current CSV allowed statuses (historical): proposed, specified, testing, parked, falsified, promoted

Design guidance:
- "promoted" should be renamed or deprecated in favor of explicit review outcomes. (promoted should be renamed or deprecated)
- Prefer explicit review-stage statuses: proposed, specified, testing, parked, falsified, review_ready, approved_for_next_stage, superseded

Notes:
- approved_for_next_stage is not production approval. (approved_for_next_stage)
- approved_for_next_stage is not live trading approval.
- approved_for_next_stage is not automated promotion.
- Any production or live trading decision is outside the AED v1 registry scope.

## 6. Lifecycle events

Each record MAY include an append-only lifecycle_events array. Each event:

- event_id
- event_type
- event_timestamp
- actor
- reason
- from_status
- to_status
- related_artifacts
- manual_review_required
- registry_mutation_mode

State:
- lifecycle events are append-only in v1 design
- manual review remains required
- automated mutation remains locked

## 7. Links to other AED artifacts

Fields linking to other AED artifacts:

- hypothesis_card_ref (HypothesisCard)
- trial_ledger_refs (TrialLedger)
- search_space_refs (SearchSpaceManifest)
- model_assessment_refs (ModelAssessmentSpec) — added after MAS validator milestone (PRs #63, #64)
- mechanism_report_refs (MechanismDiscoveryReport)
- posthoc_theory_note_refs (PostHocTheoryNote)
- review_packet_refs (ReviewPacket)
- manual_decision_refs (ManualDecision)

State & rules:
- hypothesis cannot advance without review packet references later
- theory-after records must link MechanismDiscoveryReport or PostHocTheoryNote
- trial accounting must be linked before any broad search advancement
- SearchSpaceManifest refs are required before broad search
- ModelAssessmentSpec refs are required before hypothesis can move to approved_for_next_stage

## 8. Migration from CSV v1 (phased)

Phase 1: manual CSV remains canonical current artifact (CSV registry v1)
Phase 2: JSONL/YAML design approved
Phase 3: one-time manual conversion from CSV rows to JSONL records
Phase 4: validator introduced in separate tooling PR (registry validator is deferred)
Phase 5: registry JSONL becomes canonical system of record
Phase 6: CSV becomes export view only

State:
- No migration happens in this PR. (No migration happens in this PR)
- docs/edge_hypothesis_registry.csv is not modified in this PR. (docs/edge_hypothesis_registry.csv is not modified in this PR)
- historical CSV rows must preserve hypothesis_id

## 9. JSONL example (pre-earnings options IV ramp)

```jsonl
{"hypothesis_id":"HYP-PA-0001","registry_version":"edge_registry_v1","title":"Pre-earnings options IV ramp","status":"specified","evidence_stage":"exploratory","source_lane":"exploratory_anomaly","theory_timing":"post_discovery","mechanism_summary":"dealer hedging and temporary uncertainty demand around earnings","promotion_restrictions":["requires_fresh_event_cohorts"],"hypothesis_card_ref":"card://edge_hypothesis_card_v1/HYP-PA-0001","trial_ledger_refs":["TRL-2026-0007","TRL-2026-0008"],"search_space_refs":["SSM-PA-0001"],"mechanism_report_refs":["MDR-0003"],"lifecycle_events":[{"event_id":"EV-1","event_type":"created","event_timestamp":"2026-04-29T12:00:00Z","actor":"alice","from_status":null,"to_status":"specified","manual_review_required":true}],"created_at":"2026-04-29T12:00:00Z"}
```

## 10. YAML example (moving average crossover)

```yaml
hypothesis_id: HYP-MA-0002
registry_version: edge_registry_v1
title: Moving average crossover exploratory anomaly
status: testing
source_lane: exploratory_anomaly
theory_timing: post_discovery
mechanism_summary: |
  Possible explanations include trend following, slow information diffusion,
  volatility-regime filtering, transaction-cost artifacts, or data-mined artifacts.
posthoc_theory_note_refs:
  - PHN-0007
trial_ledger_refs:
  - TRL-2026-0042
search_space_refs:
  - SSM-MA-0002
promotion_restrictions:
  - requires_fresh_holdout
  - no_promotion_without_confirmatory_evidence
# approved_for_next_stage is not allowed without a review packet and explicit human approval
notes: |
  This YAML record is human-editable but the canonical format should be
  JSONL for machine processing.
```

## 11. Invariants (hard rules)

- No automated registry mutation. (No automated registry mutation)
- No automated promotion. (No automated promotion)
- No live trading. (No live trading)
- No production execution. (No production execution)
- Registry status changes require manual review.
- promoted status should be renamed or deprecated. (promoted should be renamed or deprecated)
- approved_for_next_stage is not production approval.
- theory-after records must preserve theory_timing.
- post_discovery hypotheses must link trial burden.
- broad search advancement requires TrialLedger and SearchSpaceManifest references.
- CSV v1 remains manual until a separate migration PR.
- Validators are deferred to later tooling PRs. (registry validator is deferred)

## 12. Relationship to future validators and schemas

- JSON schema is deferred. (JSON schema is deferred)
- YAML schema is deferred.
- registry validator is deferred. (registry validator is deferred)
- migration tooling is deferred.
- CI enforcement is deferred.

Future validators should check:
- required field presence
- allowed status values
- hypothesis_id format: HYP-YYYY-NNNN (legacy IDs grandfathered)
- lifecycle event integrity (append-only)
- link integrity: trial_ledger_refs, search_space_refs, model_assessment_refs must reference valid IDs with correct format (TRL-YYYY-NNNN, SSM-YYYY-NNNN, MAS-YYYY-NNNN)
- stop-rule restrictions
- no ex-post hypothesis formation without PostHocTheoryNote

## 13. Anti-overfit and anti-lookahead governance

Registry entries must reflect and enforce point-in-time integrity standards from the AED governance framework:

**Data freshness constraints:**
- hypothesis_card entries must record the data_cutoff_timestamp and feature_timestamp used to form the hypothesis
- Any record linking to TrialLedger or SearchSpaceManifest must reference entries that respect decision-time anti-lookahead (feature_timestamp / data_cutoff_timestamp ≤ decision_timestamp_utc)

**Prohibition on ex-post rationalization:**
- The hypothesis statement must be recorded before seeing the target test result
- The registry record must include the pre-specified primary metric and null result definition
- Post-hoc explanations must be flagged via `theory_timing: post_discovery` and linked to a MechanismDiscoveryReport or PostHocTheoryNote

**Multiple testing controls:**
- Broad exploratory searches that produce multiple TrialLedger entries must be reflected in the registry via `trial_ledger_refs`
- FWER or DSR adjustments applied to trials must be documented in the lifecycle event or notes field
- Registry validator (deferred) should warn when a hypothesis has more than N trial refs without a confirmatory assessment

**No ex-post hypothesis formation:**
- Records with `evidence_stage: exploratory` that later become `specified` without a corresponding TheoryBefore discovery trail must include a PostHocTheoryNote ref
- The registry does not store optimized hyperparameter sets or in-sample fit artifacts as evidence

- No code implementation.
- No JSON schema yet. (JSON schema is deferred)
- No YAML schema yet.
- No validator yet. (registry validator is deferred)
- No migration yet. (No migration happens in this PR)
- No automated registry mutation. (No automated registry mutation)
- No automated promotion. (No automated promotion)
- No live trading. (No live trading)
- No production execution. (No production execution)
- No autonomous search. (No autonomous search)
- No Bayesian optimization. (No Bayesian optimization)
- No genetic programming. (No genetic programming)

## 14. Implementation roadmap (recommended follow-ups)

- PR #43: ModelAssessmentSpec v1 ✅ (completed PRs #63, #64)
- PR #44: EventStudySpec / OptionsEventRiskSpec schema planning
- PR #45: validator/tooling cleanup
- PR #46: MechanismDiscoveryReport JSON schema
- PR #47: EdgeHypothesisRegistry JSON schema and validator — **this PR** (design refresh, PR #66)
- PR #66: EdgeHypothesisRegistry v1 design refresh: add MAS linkage, ID format, anti-overfit/anti-lookahead governance

