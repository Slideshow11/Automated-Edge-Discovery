# ModelAssessmentSpec v1

## 1. Purpose

ModelAssessmentSpec defines how AED assesses model, signal, and backtest
evidence. It is a docs-only assessment plan and does NOT validate a strategy
by itself.

Assessment depends on TrialLedger and SearchSpaceManifest because statistical
correction is impossible without trial counts and declared search boundaries.
The spec prevents backtest overfitting, cherry-picking, and hiding failed
trials by requiring explicit diagnostics, linkage, and multiple-testing policy.

## 2. Definitions

ModelAssessmentSpec
: A structured assessment plan for evaluating a CandidateSpec, signal,
  model, anomaly, or BacktestRun.

Assessment target
: Artifact being assessed (CandidateSpec, ExploratoryAnomalySpec,
  BacktestRun, MechanismDiscoveryReport, EdgeHypothesisRegistry record).

Evidence tier
: Strength level of evidence (exploratory, specified_backtest,
  controlled_backtest, confirmatory_holdout, manual_reviewed).

Validation scheme
: Method to estimate forward performance (purged_cv, walk_forward,
  future_window, external_holdout, etc.).

Multiple-testing burden
: The number and scope of trials/variants/parameters/searches that
  must be penalized.

Overfit control
: Diagnostics/procedures (DSR, PBO, CPCV, purged CV, embargo) to reduce
  false discovery risk.

Confirmatory evidence
: Evidence generated after hypothesis and test plan were declared
  (fresh holdout or future-window confirmation).

## 3. Why this exists

Failure modes addressed:
- backtest overfitting
- cherry-picking
- unreported failed trials
- p-hacking and metric-tuning
- in-sample optimization misuse
- leakage via labels/features/event timing/universe selection
- selection bias from inspecting winners only
- weak sample sizes and regime instability
- data-mined TA rules and post-hoc theory laundering
- metrics that hide tail risk

## 4. Evidence tiers

- exploratory
  - early diagnostics; cannot support advancement alone
- specified_backtest
  - backtest after HypothesisSpec & SearchSpaceManifest declared
- controlled_backtest
  - backtest with TrialLedger, SearchSpaceManifest, predeclared validation, costs, leakage controls
- confirmatory_holdout
  - fresh data or future-window confirmatory test after plan declared
- manual_reviewed
  - ReviewPacket assembled and ManualDecision completed

State: exploratory evidence cannot promote; confirmatory_holdout required for strong claims; manual_reviewed required for registry advancement.

## 5. ModelAssessmentSpec v1  required fields

- model_assessment_id
- assessment_target_type
- assessment_target_id
- hypothesis_id
- anomaly_id
- candidate_id
- trial_ledger_refs
- search_space_refs
- mechanism_report_refs
- registry_refs
- review_packet_refs
- evidence_tier
- validation_scheme
- sample_start
- sample_end
- holdout_start
- holdout_end
- universe_id
- dataset_id
- label_id
- feature_set_id
- cost_model
- slippage_model
- fill_model
- primary_metric
- secondary_metrics
- tail_risk_metrics
- drawdown_metrics
- turnover_metrics
- capacity_metrics
- stability_metrics
- regime_splits
- subsample_tests
- placebo_tests
- shuffle_tests
- null_model_tests
- leakage_checks
- multiple_testing_policy
- trial_burden_summary
- overfit_controls
- minimum_sample_policy
- statistical_assumptions
- failure_conditions
- promotion_blockers
- created_at
- review_owner
- notes

## 6. Allowed validation_scheme values

- in_sample_only
- train_test_split
- walk_forward
- rolling_window
- expanding_window
- purged_cv
- combinatorial_purged_cv
- future_window
- external_holdout
- event_time_split

Notes:
- in_sample_only cannot support advancement. (in_sample_only cannot support advancement)
- purged_cv or combinatorial_purged_cv preferred for overlapping labels/events.
- future_window or external_holdout preferred for confirmatory evidence.

## 7. Required diagnostics

Performance:
- mean return
- volatility
- Sharpe
- Sortino
- hit rate
- payoff ratio

Tail risk:
- max drawdown
- expected shortfall
- skew
- kurtosis
- worst-event cohort

Stability:
- regime splits
- rolling performance
- subsample consistency
- parameter sensitivity

Execution:
- costs
- slippage
- spread sensitivity
- turnover
- capacity

Null tests:
- shuffled labels
- shuffled dates
- random-entry baseline
- matched-control baseline
- placebo events

Leakage:
- point-in-time data checks
- event timestamp checks
- label overlap checks
- survivorship checks
- universe construction checks

## 8. Overfit controls

- Deflated Sharpe Ratio
- Probability of Backtest Overfitting
- multiple-testing correction
- FWER
- FDR
- CPCV
- purged CV
- embargo
- parameter sensitivity analysis
- holdout confirmation
- pre-registered failure conditions

State:
- DSR/PBO cannot be computed honestly without TrialLedger and SearchSpaceManifest. (Deflated Sharpe Ratio)
- Multiple-testing correction must include inherited exploratory trial burden.

## 9. Multiple-testing policy

Required fields:
- declared_trial_count
- observed_trial_count
- inherited_trial_count
- search_space_size
- parameter_grid_size
- model_family_count
- feature_family_count
- label_variant_count
- correction_method
- adjusted_metric_threshold
- unreported_trial_risk

Notes:
- inherited_trial_count must include exploratory anomaly work.
- post_hoc_theory cannot reset multiple-testing burden. (post_hoc_theory cannot reset multiple-testing burden)
- omitted failed trials are blockers.

## 10. Sample sufficiency policy

Require:
- minimum number of trades/events
- minimum number of independent clusters
- minimum number of regimes
- minimum out-of-sample period
- event cohort counts for event studies
- caution for overlapping samples, high turnover, or rare-event strategies

## 11. Invariants (hard rules)

- No model assessment without TrialLedger references. (No model assessment without TrialLedger references)
- No model assessment without SearchSpaceManifest references for broad search. (No model assessment without SearchSpaceManifest references)
- No advancement from in_sample_only evidence. (No advancement from in_sample_only evidence)
- No advancement when failed or abandoned trials are omitted.
- No advancement when leakage checks fail.
- No advancement when costs/slippage are omitted for tradable strategies.
- No advancement when post_hoc_theory resets trial burden.
- No automated promotion. (No automated promotion)
- No automated registry mutation. (No automated registry mutation)
- No live trading. (No live trading)
- No production execution. (No production execution)
- Human review remains required.

## 12. Relationship to existing AED artifacts

References:
- TrialLedger
- SearchSpaceManifest
- MechanismDiscoveryReport
- PostHocTheoryNote
- EdgeHypothesisRegistry JSONL/YAML
- ReviewPacket
- ManualDecision
- EventStudySpec
- OptionsEventRiskSpec
- JumpRiskReport

State:
- ModelAssessmentSpec consumes trial and search-space metadata and informs ReviewPacket.
- ModelAssessmentSpec does not mutate registry state.
- Registry advancement remains manual.

## 13. Examples

Example A: pre-earnings options IV ramp
- assessment_target_type: CandidateSpec
- validation_scheme: event_time_split or future_window
- cost_model: MID_WITH_SPREAD_PENALTY
- fill_model: MID_WITH_SPREAD_PENALTY
- diagnostics: IV ramp, gap exposure, spread sensitivity, event cohort count, worst-event cohort
- must link TrialLedger and SearchSpaceManifest
- cannot advance without confirmatory evidence

Example B: moving average crossover exploratory anomaly
- assessment_target_type: ExploratoryAnomalySpec
- validation_scheme: walk_forward or future_window
- inherited_trial_count required
- shuffle_tests and random entry baseline required
- post_hoc_theory cannot reset burden
- cannot become theory-first retroactively

## 14. Non-goals

- No code implementation.
- No JSON schema yet. (JSON schema is deferred)
- No validator yet. (registry validator is deferred)
- No automated registry mutation. (No automated registry mutation)
- No automated promotion. (No automated promotion)
- No live trading. (No live trading)
- No production execution. (No production execution)
- No autonomous search. (No autonomous search)
- No Bayesian optimization. (No Bayesian optimization)
- No genetic programming. (No genetic programming)

## 15. Implementation roadmap

- PR #44: EventStudySpec / OptionsEventRiskSpec schema planning
- PR #45: validator/tooling cleanup
- PR #46: MechanismDiscoveryReport JSON schema
- PR #47: EdgeHypothesisRegistry JSON schema and validator
- PR #48: ModelAssessmentSpec JSON schema

