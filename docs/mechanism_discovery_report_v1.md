# MechanismDiscoveryReport v1 & PostHocTheoryNote v1

## 1. Purpose

AED allows "theory-after" because researchers and LLMs do not know every
possible mechanism before discovery. Theory-after is useful for documenting
hypotheses that emerge after an anomaly is observed, but it is also dangerous
because it can enable HARKing, narrative laundering, and the disappearance of
exploratory trial burden.

This document defines two docs-only artifacts that make theory-after auditable
and governance-friendly: MechanismDiscoveryReport and PostHocTheoryNote. These
artifacts do not validate a strategy by themselves. They preserve exploratory
origin, search burden, provenance, and the requirement for fresh confirmatory
testing before any promotion.

## 2. Definitions

MechanismDiscoveryReport
: A structured report that lists candidate mechanisms explaining an
  observed anomaly, with supporting_evidence, conflicting_evidence, provenance,
  and required_confirmatory_tests. It is a first-class doc linking to TrialLedger
  entries and SearchSpaceManifest records.

PostHocTheoryNote
: A shorter note attached to a hypothesis, anomaly, or review packet when
  the mechanism was written after empirical results were already observed.

Mechanism candidate
: One possible causal, behavioral, institutional, microstructure,
  risk-premium, options-market, or data-artifact explanation.

Theory timing
: Whether the mechanism was generated pre_discovery, post_discovery, mixed,
  or unknown.

LLM-assisted mechanism discovery
: Use of an LLM to search, synthesize, or draft possible mechanisms or
  literature links. LLM-assisted mechanism discovery may accelerate drafting and
  literature summarization but must follow strict provenance and review rules.

## 3. Why this exists

Absent structured records, the following failure modes occur:
- HARKing (hypothesizing after results known)
- narrative laundering where plausible-sounding stories replace evidence
- data-mined technical-analysis rules acquiring fake explanations
- LLM-generated plausible stories being mistaken for evidence
- exploratory anomalies being promoted as theory-first without inherited burden
- trial burden disappearing during graduation from ExploratoryAnomalySpec to HypothesisSpec
- literature cherry-picking and confirmatory tests contaminated by already-seen data

MechanismDiscoveryReport and PostHocTheoryNote exist to make post-hoc theory
transparent, auditable, and linked to TrialLedger and SearchSpaceManifest records.

## 4. MechanismDiscoveryReport v1  required fields

- mechanism_report_id  stable unique id
- anomaly_id  linked ExploratoryAnomalySpec id (if any)
- hypothesis_id  linked HypothesisSpec id (if any)
- search_space_id  reference to the SearchSpaceManifest used during investigation
- trial_ledger_refs  references to TrialLedger artifacts
- originating_trial_ids  list of trial ids that produced the anomaly
- source_lane  one of: theory_first, exploratory_anomaly, post_hoc_theory, confirmatory
- theory_timing  pre_discovery|post_discovery|mixed|unknown
- llm_assisted  boolean
- llm_model_or_agent  string describing model/agent used (optional)
- human_reviewer  reviewer name or team responsible for human validation
- created_at, updated_at  ISO timestamps
- observed_pattern  concise description of observed anomaly
- candidate_mechanisms  list of Mechanism candidates (each with id and summary)
- preferred_mechanism  id of the mechanistic explanation the author favors (optional)
- supporting_evidence  structured list (literature, empirical, theoretical)
- conflicting_evidence  structured list of known contradictory findings
- literature_refs  list of canonical literature links (DOI, url, citation)
- data_artifact_risks  notes about possible data issues (e.g., lookahead, mislabel)
- microstructure_risks  market-microstructure explanation possibilities
- options_market_risks  options-specific risks and mechanics
- behavioral_risks  behavioral explanations and selection biases
- institutional_risks  institutional flows, desk hedging, or reporting artifacts
- risk_premium_risks  plausible risk-premium explanations
- alternative_explanations  other mechanisms that should be tested
- required_confirmatory_tests  explicit list of tests that would falsify the mechanism
- fresh_data_requirement  statement that confirmatory tests must use fresh data
- holdout_or_future_window_policy  policy for reserved windows or future cohorts
- trial_burden_inherited  succinct summary of inherited trial counts and search effort
- statistical_penalty_note  guidance on multiple-testing correction and penalties
- promotion_blockers  explicit blockers preventing promotion until met
- review_status  draft|needs_evidence|ready_for_review|rejected|superseded
- notes  free-form additional context

Allowed theory_timing values: pre_discovery, post_discovery, mixed, unknown

Allowed source_lane values: theory_first, exploratory_anomaly, post_hoc_theory, confirmatory

Allowed review_status values: draft, needs_evidence, ready_for_review, rejected, superseded

## 5. PostHocTheoryNote v1  required fields

- posthoc_note_id  unique id
- mechanism_report_id  optional pointer to a MechanismDiscoveryReport
- anomaly_id  linked ExploratoryAnomalySpec id
- hypothesis_id  linked HypothesisSpec id (if present)
- written_after_results  boolean (true if written after empirical observations)
- llm_assisted  boolean
- created_at  ISO timestamp
- author_or_agent  name of author or agent used to draft
- summary  short human-friendly summary of the post-hoc theory
- candidate_mechanism  id or text of mechanism suggested
- known_seen_results  concise list of results that were already visible when theory written
- why_theory_was_not_predeclared  free-text justification
- linked_trial_ids  originating_trial_ids (must reference TrialLedger entries)
- linked_search_space_id  reference to SearchSpaceManifest used during exploration
- required_confirmatory_test  at least one explicit test required to advance
- trial_burden_statement  declaration of inherited trial burden
- promotion_restriction  explicit flag preventing promotion until satisfied
- human_review_required  boolean (true)

## 6. Invariants (hard rules)

- Post-hoc theory must not reset trial count. (Post-hoc theory must not reset trial count)
- LLM-generated theory is not evidence by itself. (LLM-generated theory is not evidence by itself)
- ExploratoryAnomalySpec graduation must preserve anomaly_id.
- ExploratoryAnomalySpec graduation must preserve originating_trial_ids.
- ExploratoryAnomalySpec graduation must preserve search_space_id.
- A post-discovery mechanism cannot be treated as theory-first.
- A MechanismDiscoveryReport cannot on its own promote a hypothesis.
- A PostHocTheoryNote cannot on its own promote a hypothesis.
- Fresh confirmatory evidence is required before advancement. (fresh confirmatory evidence)
- A ReviewPacket must disclose theory_timing and llm_assisted status.
- Human review remains required. (Human review remains required)
- Automated promotion remains locked. (Automated promotion remains locked)
- Registry mutation remains manual. (Registry mutation remains manual)

## 7. LLM role and limits

- LLMs may help discover mechanisms. (LLM-assisted mechanism discovery)
- LLMs may summarize literature and propose falsification tests.
- LLMs may propose alternative explanations and draft MechanismDiscoveryReport or PostHocTheoryNote drafts.
- LLMs must not declare evidence sufficient for promotion. (LLM-generated theory is not evidence by itself)
- LLMs must not hide post-hoc timing or remove conflicting_evidence.
- LLMs must not decide promotion or mutate the registry.

## 8. Evidence standards

- supporting_evidence must distinguish literature support, empirical support, and theoretical plausibility.
- conflicting_evidence must be recorded and preserved.
- candidate mechanisms must list falsification implications (required_confirmatory_tests).
- data artifacts must be treated as candidate mechanisms until ruled out (data_artifact_risks).
- mechanism plausibility does not replace out-of-sample confirmation.
- Confirmatory tests require fresh data, future windows, or untouched holdouts. (fresh confirmatory evidence)

## 9. Relationship to TrialLedger / SearchSpaceManifest

- originating_trial_ids must point to TrialLedger entries (TrialLedger).
- search_space_id must point to the original SearchSpaceManifest (SearchSpaceManifest).
- trial_burden_inherited must summarize inherited search effort used in multiple-testing correction.
- Confirmatory tests must create new TrialLedger entries and operate under a new or explicitly linked SearchSpaceManifest.
- ModelAssessmentSpec later uses trial_burden_inherited for multiple-testing correction, Deflated Sharpe Ratio, Probability of Backtest Overfitting, CPCV, purged CV, and embargo.

## 10. Relationship to existing docs

See also:
- docs/post_governance_implementation_roadmap.md
- docs/theory_first_research_protocol.md
- docs/trial_ledger_search_space_manifest_v1.md
- docs/edge_hypothesis_card_v1.md
- docs/edge_hypothesis_registry_v1.md
- docs/current_project_status.md

## 11. Non-goals

- No code implementation.
- No JSON schema yet.
- No validator yet.
- No automated registry mutation. (No automated registry mutation)
- No automated promotion. (No automated promotion)
- No live trading or production execution. (No live trading)
- No autonomous search. (No autonomous search)
- No Bayesian optimization. (No Bayesian optimization)
- No genetic programming. (No genetic programming)

## 12. Examples

### Example A: pre-earnings options IV ramp

- observed anomaly: pre-earnings options IV ramp and pre-event drift (pre-earnings options IV ramp)
- candidate mechanisms: uncertainty demand, dealer hedging, analyst revision flow, earnings risk premium, liquidity artifact
- options_market_risks included
- requires fresh event cohorts or future earnings windows
- linked to TrialLedger/SearchSpaceManifest
- cannot promote without confirmatory evidence

### Example B: moving average crossover anomaly

- observed anomaly: TA moving average crossover works in a sample (moving average crossover)
- candidate mechanisms: trend following, slow information diffusion, volatility regime filter, transaction-cost artifact, data-mined artifact
- marked post_discovery if theory came after results
- requires inherited trial burden (trial_burden_inherited)
- requires fresh untouched data or future windows
- cannot become theory-first retroactively

## 13. Implementation roadmap (recommended follow-ups)

- PR #42: EdgeHypothesisRegistry JSONL/YAML v1
- PR #43: ModelAssessmentSpec v1
- PR #44: EventStudySpec / OptionsEventRiskSpec schema planning
- PR #45: validator/tooling cleanup
- PR #46: MechanismDiscoveryReport JSON schema (future  not part of this PR)
