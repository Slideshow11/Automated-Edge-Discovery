# Theory-first AED Research Protocol

Purpose
-------
This protocol specifies how Automated Edge Discovery (AED) turns an idea or literature claim into a testable, auditable research hypothesis before any CandidateSpec, backtest, model search, or automation is allowed. The protocol enforces "theory-first" discipline: human-understandable mechanisms and a HypothesisSpec must exist prior to concrete CandidateSpec implementation or automated searching.

Non-goals
---------
- This document does not implement schema or runtime changes.
- It does not prescribe exact code-level APIs (those are delivered in follow-up schema PRs).
- It does not authorize automated promotion, registry mutation, or automated accepted/rejected/killed lifecycle actions.

Research object lifecycle
------------------------
AED research objects move through an explicit lifecycle. Each step has required metadata and constraints.

Research workflow
-----------------
Idea
 LiteratureClaim (optional)
 TheorySketch or ExploratoryAnomalySpec
 HypothesisSpec (if mechanism exists)
 CandidateSpec
 ValidationPlan
 BacktestRun
 ReviewPacket
 ManualDecision

1. Idea
   - A rough research intuition, observation, user note, or literature-derived possibility.

2. LiteratureClaim
   - A claim imported from a paper, book, article, or prior research note.
   - Must record source, claim text, scope, assumptions, conditions, and caveats.
   - LiteratureClaim must not be treated as a direct CandidateSpec.

3. TheorySketch
   - A short, human-readable mechanism explaining why an effect should exist.
   - Should identify the causal reasoning, participants, frictions, and timing assumptions.

4. HypothesisSpec
   - A testable version of the theory.
   - Required fields (every HypothesisSpec must include):
     - hypothesis_id
     - mechanism
     - systematic_mistake_or_risk_premium
     - expected_direction
     - testable_implications
     - counterfactual_implications
     - required_variables
     - label_definition
     - event_window (if event-driven)
     - normal_performance_model (if event-driven)
     - validation_plan
     - known_failure_modes
     - why_edge_might_persist
     - data_requirements
     - leakage_risks
     - review_owner

5. CandidateSpec
   - A concrete strategy/model/filter implementation derived from a HypothesisSpec.
   - A CandidateSpec must not be created before a HypothesisSpec exists.
   - CandidateSpec must record parameterization, data slices, and search boundaries.

6. BacktestRun
   - An execution of one CandidateSpec under a declared validation design.
   - BacktestRun metadata must include the HypothesisSpec and CandidateSpec IDs, validation split, purging/embargo settings, and all hyperparameters.

7. ReviewPacket
   - Human-review artifact which combines HypothesisSpec, CandidateSpec, BacktestRun outputs, diagnostics, and required metadata.
   - ReviewPacket must not mutate registry state. ManualDecision happens after human review.

Exploratory anomaly lane
------------------------
AED supports an explicit exploratory lane for anomalous, technical-analysis-style, or otherwise weakly-mechanized observations. The exploratory lane allows empirical probes that are explicitly labeled exploratory but does not permit acceptance, promotion, or automation without a mechanism and stronger validation.

Key principles
- AED may test ideas without a strong mechanism when the test is explicitly labeled exploratory.
- This lane is intended for TA patterns, seasonality, calendar effects, volatility behavior, flow proxies, microstructure quirks, and other observations where the mechanism is weak or unknown.
- Exploratory tests must be recorded as an ExploratoryAnomalySpec rather than a HypothesisSpec.

ExploratoryAnomalySpec (required fields)
- anomaly_id
- observation
- source
- pattern_definition
- initial_scope
- reason_unknown
- candidate_mechanisms
- baseline_comparisons
- trial_budget
- required_null_tests
- data_snooping_risk
- cost_sensitivity_required
- regime_sensitivity_required
- promotion_locked
- mechanism_required_before_acceptance

Behavior and restrictions
- ExploratoryAnomalySpec may produce BacktestRun(s) and a ReviewPacket for inspection.
- ExploratoryAnomalySpec may not trigger accepted strategy status, automated promotion, production status, or autonomous search expansion.
- An ExploratoryAnomalySpec can graduate into a HypothesisSpec only after a plausible mechanism, a candidate mechanism set, or a robust empirical rationale is written and documented in a TheorySketch.
- TA-style rules and pattern definitions must still be precise (entry/exit, sizing, holding period, and cost assumptions) before running exploratory tests.
- Baseline comparisons should include simple momentum, random entry with the same holding period, shuffled-dates tests where applicable, cost sensitivity, liquidity sensitivity, and regime splits.

Core rules (theory-first guardrails)
-----------------------------------
- Backtests are evidence against bad hypotheses, not proof of good hypotheses.
- No strategy acceptance from backtest alone.
- Every candidate must tie back to a theory or mechanism. "Every candidate must tie back to a theory or mechanism".
- Every broad search must report all trials (report all trials).
- CandidateSpec must not precede HypothesisSpec.
- LiteratureClaim must not become CandidateSpec directly.
- ML may help discover variables, but a human-readable mechanism must exist before promotion.
- A good backtest with no mechanism is not sufficient for acceptance.
- A plausible mechanism with failed tests must be revised or rejected.
- Manual review remains mandatory before any registry mutation.
- Automated promotion remains locked.

ValidationPlan rules
--------------------
- ValidationPlan must state the validation design explicitly: train/validation/test splits, purging, embargo, CPCV, Monte Carlo synthetic data (if used), and statistical corrections (DSR/FWER) when applicable.
- ValidationPlan must enumerate leakage_risks and how they are mitigated.
- ValidationPlan must require that every broad automated search records TrialLedger and SearchSpaceManifest metadata (but automation of Ledger/Manifest is gated until infrastructure exists).

ReviewPacket rules
------------------
- ReviewPacket assembles HypothesisSpec, CandidateSpec, BacktestRun(s), diagnostics (predictability diagnostics, microstructure controls), and a human review checklist.
- ReviewPacket must include FeatureImportanceReport expectations (if ML used) and a statement on why feature importance does or does not imply monetizable edge.
- ReviewPacket must explicitly state whether any locked-until-safe features are required to safely accept the candidate.

Manual decision rules
---------------------
- Only humans may make ManualDecision about promotion, registry mutation, or deployment.
- ManualDecision must be recorded with reviewer, timestamp, rationale, and explicit action (accept, revise, reject, table).
- Automated promotion remains locked and cannot be triggered by ReviewPacket content alone.

Examples
--------

Example A: pre-earnings options IV ramp
- Idea: observed IV expansion in front-month options before earnings.
- Mechanism (TheorySketch): Market makers and traders reprice event uncertainty into front-month options before earnings.
- HypothesisSpec highlights:
  - Edge target: pre-event IV expansion and possible drift while avoiding scheduled announcement jump risk.
  - Counterfactual: if IV ramp is fully priced or spreads absorb it, option PnL should vanish after costs.
  - Required variables: earnings date/session, option IV, delta bucket, expiry, spread, open interest, underlying return, event timing.
  - Known failure modes: stale quotes, wide spreads, event date errors, IV crush before exit, crowding, liquidity collapse.

Example B: Return predictability claim
- Idea: short-horizon return predictability observed in raw data.
- Mechanism (TheorySketch): delayed information diffusion or risk-premium explanation.
- HypothesisSpec highlights:
  - Required baseline diagnostics: autocorrelation, variance ratio, horizon sensitivity, regime sensitivity.
  - ValidationPlan must show these diagnostics and sampling-power analysis.

Example C: TA moving average crossover as exploratory anomaly
- Idea: a simple moving average crossover appears to correlate with short-term returns in some universes.
- Mechanism: unknown or weak; several candidate_mechanisms (trend-following, microstructure inertia) may be hypothesized.
- Protocol: run ExploratoryAnomalySpec with precise pattern_definition and trial_budget; require baselines (simple momentum, random entry with same holding period) and cost-sensitivity before any further review.
- Promotion: locked until a plausible mechanism or robust empirical rationale is documented.

Failure modes and red flags
---------------------------
- Data-snooping and undisclosed multiple-testing (addressed by DSR/FWER and report all trials).
- Label leakage and time-series leakage.
- Microstructure-driven signals that vanish under realistic transaction-cost models.
- Event-date uncertainty and calendar misalignment.

Implementation gates
--------------------
- No EventStudySpec without TheorySketch and HypothesisSpec.
- No OptionsEventRiskSpec without explicit event jump exposure statement.
- No ModelAssessmentSpec without declared label and loss function.
- No TrialLedger/SearchSpaceManifest automation until theories and candidates are separable and the TrialLedger schema exists.
- No Bayesian optimization or genetic programming until the theory-first protocol, TrialLedger, SearchSpaceManifest, and overfit controls exist.

Relationship to roadmap
-----------------------
This document implements PR #33 in the research roadmap and provides the protocol that gates later schema and automation PRs.

Acknowledgements
----------------
This protocol follows the research_reference_map and reviewed references listed there, including machine-learning and econometrics texts.
