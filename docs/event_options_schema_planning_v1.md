# EventStudySpec & OptionsEventRiskSpec — Schema Planning v1

## 1. Purpose

EventStudySpec and OptionsEventRiskSpec exist as conceptual protocols in the
repository. This planning document converts those protocols into schema-ready
field definitions, invariants, required diagnostics, and relationships to
TrialLedger, SearchSpaceManifest, ModelAssessmentSpec, ReviewPacket, and the
EdgeHypothesisRegistry. This PR is docs-only: no schemas, validators, or code
are created here.

Event-driven and options-event research require stricter timing and risk
metadata than generic signal tests; this doc captures those requirements for
future schema design.

## 2. Source protocols

Reference source docs:
- docs/event_study_design_protocol.md
- docs/options_event_risk_protocol.md
- docs/trial_ledger_search_space_manifest_v1.md
- docs/model_assessment_spec_v1.md
- docs/edge_hypothesis_registry_jsonl_yaml_v1.md
- docs/mechanism_discovery_report_v1.md

## 3. Shared event timing model

Shared fields (per event):
- event_id
- event_type
- event_source
- event_date
- event_timestamp
- event_timestamp_quality
- event_session
- calendar_id
- trading_day
- event_trading_day
- pre_event_window
- post_event_window
- estimation_window
- blackout_window
- session_anchor
- entry_timestamp
- exit_timestamp
- event_hold_flag
- gap_exposure
- timezone
- holiday_calendar
- data_cutoff_timestamp
- point_in_time_policy

Required event_session values:
- BMO
- AMC
- INTRADAY
- UNKNOWN

Required event_hold_flag values:
- no_event_hold
- partial_event_hold
- full_event_hold
- unknown_event_hold

Required gap_exposure values:
- none
- partial
- full
- unknown

## 4. Trading-day and session invariants

Hard rules:
- No calendar-day approximation for trading-day windows; event windows must be trading-calendar aware. (No calendar-day approximation for trading-day windows)
- BMO and AMC sessions must be handled distinctly; pooling requires explicit stratification. (BMO, AMC)
- UNKNOWN event_session blocks advancement unless sensitivity analysis is demonstrated.
- unknown_event_hold or unknown gap_exposure blocks promotion or advancement.
- entry_timestamp and exit_timestamp must be recorded before measuring performance.
- data_cutoff_timestamp must precede signal construction.
- point_in_time_policy required for all event-driven tests.

## 5. EventStudySpec v1 — schema-planning fields

- event_study_id
- hypothesis_id
- anomaly_id
- candidate_id
- event_type
- event_source
- event_selection_rule
- event_session
- event_timestamp_quality
- calendar_id
- timezone
- sample_start
- sample_end
- event_universe
- event_count
- estimation_window
- pre_event_window
- post_event_window
- blackout_window
- normal_performance_model
- abnormal_performance_measure
- clustering_policy
- standard_error_policy
- multiple_testing_policy
- sample_sufficiency_policy
- bias_checks
- placebo_tests
- matched_control_policy
- event_overlap_policy
- missing_event_policy
- timestamp_uncertainty_policy
- point_in_time_policy
- data_cutoff_timestamp
- trial_ledger_refs
- search_space_refs
- model_assessment_refs
- review_packet_refs
- registry_refs
- promotion_blockers
- notes

## 6. EventStudySpec required diagnostics

- event_count and cohort distribution
- session stratification (BMO vs AMC vs INTRADAY)
- pre/post window sensitivity
- estimation window sensitivity
- placebo event tests
- matched-control analyses
- event overlap checks and de-duplication policies
- timestamp uncertainty audits
- clustered standard errors (or equivalent)
- multiple-testing adjustment (policy)
- sample sufficiency checks
- missing-event audit and reconciliation
- point-in-time data integrity checks

## 7. OptionsEventRiskSpec v1 — schema-planning fields

- options_event_risk_id
- hypothesis_id
- anomaly_id
- candidate_id
- event_study_id
- event_type
- event_session
- event_timestamp_quality
- entry_timestamp
- exit_timestamp
- event_hold_flag
- gap_exposure
- option_type
- option_expiry
- expiry_covers_event
- days_to_expiry_at_entry
- days_to_expiry_at_exit
- delta_bucket
- moneyness
- iv_level
- iv_rank_or_percentile
- iv_ramp_metric
- iv_crush_metric
- skew_metric
- term_structure_metric
- spread_metric
- liquidity_metric
- open_interest_metric
- volume_metric
- stale_quote_policy
- fill_model
- slippage_model
- cost_model
- residual_jump_risk_note
- pnl_decomposition_policy
- delta_pnl
- vega_pnl
- theta_pnl
- skew_pnl
- term_structure_pnl
- spread_pnl
- residual_pnl
- trial_ledger_refs
- search_space_refs
- model_assessment_refs
- review_packet_refs
- registry_refs
- promotion_blockers
- notes

## 8. OptionsEventRiskSpec required diagnostics

- classification of event_hold (no/partial/full)
- gap exposure classification
- expiry coverage analysis (expiry_covers_event)
- delta bucket distribution
- moneyness distribution
- IV ramp and IV crush metrics
- skew and term-structure diagnostics
- spread and liquidity sensitivity
- stale-quote and quote-quality audits
- open interest and volume audits
- fill-model and slippage sensitivity analyses
- PnL decomposition into delta/vega/theta/skew/term/spread/residual
- residual jump-risk analysis and worst-event cohort
- session stratification (BMO vs AMC)

## 9. PnL decomposition requirements

- Raw option PnL is insufficient. (Raw option PnL is insufficient)
- PnL must be decomposed into delta, vega, theta, skew, term_structure, spread, and residual components where possible.
- residual_pnl must be explained or explicitly flagged.
- residual jump risk must be disclosed. (residual jump risk)
- No assumption of perfect hedging unless explicitly modeled.

## 10. Relationship to TrialLedger / SearchSpaceManifest

- EventStudySpec and OptionsEventRiskSpec must link to TrialLedger entries. (TrialLedger)
- SearchSpaceManifest must declare allowed event types, windows, session handling, options filters, and allowed metrics before broad search.
- TrialLedger records every attempted event-window or options-risk test.
- Broad event-window or delta/expiry searches require TrialBudget and explicit SearchSpaceManifest.
- Failed and abandoned event or options trials must be recorded and preserved.

## 11. Relationship to ModelAssessmentSpec

- ModelAssessmentSpec consumes EventStudySpec and OptionsEventRiskSpec references.
- Event diagnostics inform sample sufficiency, clustering policy, and multiple-testing adjustments.
- Options diagnostics inform cost/slippage modeling, capacity, tail risk, and residual jump risk.
- confirmatory_holdout designs should use fresh event cohorts, future windows, or external holdouts.

## 12. Relationship to registry and ReviewPacket

- Registry records should link event_study_refs and options_event_risk_refs in future schema versions.
- ReviewPacket must disclose event_session, event_hold_flag, gap_exposure, timestamp_quality, and point-in-time policy.
- Advancement requires manual review; approved_for_next_stage is not production approval.
- No automated registry mutation, no automated promotion.

## 13. Examples

### Example A: pre-earnings options IV ramp, no event hold
- event_session: AMC and BMO stratified
- event_hold_flag: no_event_hold
- gap_exposure: none
- exit before announcement
- fill_model: MID_WITH_SPREAD_PENALTY
- requires IV ramp, IV crush, skew, term-structure, spread sensitivity, and residual jump risk diagnostics
- links: TrialLedger, SearchSpaceManifest, ModelAssessmentSpec, ReviewPacket, registry

### Example B: full event hold earnings gap strategy
- event_hold_flag: full_event_hold
- gap_exposure: full
- requires residual jump risk disclosure and capacity analysis
- cannot be pooled with no_event_hold strategies
- requires separate ModelAssessmentSpec and careful fill/slippage modeling
- not live trading or production

## 14. Invariants (hard rules)

- No EventStudySpec advancement without trading-calendar event windows and explicit session handling. (trading-calendar aware)
- No OptionsEventRiskSpec advancement with unknown gap_exposure or unknown_event_hold.
- No pooling of BMO and AMC without stratification.
- No acceptance of raw option PnL without decomposition. (Raw option PnL is insufficient)
- No advancement without point-in-time data policy.
- No advancement without TrialLedger references and SearchSpaceManifest for broad search.
- No automated promotion. (No automated promotion)
- No automated registry mutation. (No automated registry mutation)
- No live trading. (No live trading)
- No production execution. (No production execution)
- Human review remains required.

## 15. Non-goals

- No code implementation.
- No JSON schema yet.
- No validator yet.
- No migration yet.
- No automated registry mutation.
- No automated promotion.
- No live trading.
- No production execution.
- No autonomous search.
- No Bayesian optimization.
- No genetic programming.

## 16. Implementation roadmap

- PR #45: validator/tooling cleanup
- PR #46: MechanismDiscoveryReport JSON schema
- PR #47: EdgeHypothesisRegistry JSON schema and validator
- PR #48: ModelAssessmentSpec JSON schema
- PR #49: EventStudySpec and OptionsEventRiskSpec JSON schemas

