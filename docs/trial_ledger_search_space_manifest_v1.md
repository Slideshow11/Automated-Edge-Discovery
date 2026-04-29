# TrialLedger and SearchSpaceManifest v1 — Design

Purpose
-------
TrialLedger and SearchSpaceManifest are the first enforcement-layer artifacts
following the governance/intake layer (PR #37) and the post-governance
implementation roadmap (PR #38). They are required before any broad search,
Bayesian optimization, genetic programming, autonomous discovery, or strategy
ranking is allowed. Their purpose is to prevent unreported trials, silent search
space expansion, HARKing, and backtest overfitting by making trial accounting
and search boundaries explicit, auditable, and append-only.

Definitions
-----------
TrialLedger
  - An append-only record of every attempted research trial, including failed,
    abandoned, interrupted, and rejected runs. TrialLedger entries are the
    canonical log for trial counting and are required for defensible multiple-
    testing corrections.

SearchSpaceManifest
  - A pre-declared boundary of what a search is allowed to test before
    execution begins. A SearchSpaceManifest declares allowed parameter ranges,
    model classes, transforms, datasets, and stopping rules — it cannot be
    modified retroactively for an ongoing SearchRun.

TrialBudget
  - The maximum number of trials, parameter combinations, or model attempts
    authorized for a hypothesis, anomaly, or SearchRun. TrialBudget enforces
    resource limits and is checked before execution.

SearchRun
  - One execution batch operating under a specific SearchSpaceManifest and
    TrialBudget. A SearchRun groups trial attempts and records the execution
    context.

ParameterHash
  - A stable hash of the tested parameter set so duplicate or near-duplicate
    trials can be detected and tracked across runs and machines.

Trial burden
  - The accumulated number and scope of tests that must be carried into model
    assessment and statistical penalties (DSR/PBO/FWER). Trial burden includes
    exploratory and confirmatory trials and is required to compute valid
    multiple-testing corrections.

Why this exists
----------------
TrialLedger and SearchSpaceManifest block the following failure modes:

- cherry-picking of successful trials without reporting failures
- unreported failed trials and abandoned runs
- silent parameter expansion after seeing promising results
- repeated local reruns that are not accounted for in multiplicity corrections
- HARKing (hypothesizing after results are known) without provenance
- overfit search and false discovery due to untracked trial counts
- laundering exploratory anomalies into theory-first hypotheses without
  inherited trial burden
- inability to compute DSR/PBO/FWER because trial counts are missing or opaque

TrialLedger v1 required fields
-----------------------------
Each TrialLedger entry (row) must include at minimum the following fields.
Fields are strongly typed and descriptive; implementers should keep them small
and machine-friendly while preserving human-readable context.

- trial_id — stable unique identifier (string)
- run_id — identifier for the concrete execution process or job
- search_run_id — the SearchRun grouping identifier
- hypothesis_id — linked HypothesisSpec ID (if any)
- anomaly_id — linked ExploratoryAnomalySpec ID (if any)
- candidate_id — CandidateSpec identifier (if any)
- search_space_id — reference to the SearchSpaceManifest used
- parent_trial_id — optional link to a parent trial for nested experiments
- trial_sequence_number — incremental count within a SearchRun
- trial_type — e.g., grid_search, randomized_search, single_trial, probe
- source_lane — source lane (see allowed values)
- theory_timing — pre_discovery|post_discovery|mixed|unknown (see below)
- llm_assisted — boolean flag if LLMs assisted in parameter generation or theory
- parameter_hash — stable hash of parameters (ParameterHash)
- parameters — JSON or compact serialization of parameters used
- dataset_id — dataset or DataManifest pointer used
- label_id — declared label definition id (if ML used)
- universe_id — universe or asset set used
- event_study_id — linked EventStudySpec id (if event-driven)
- options_event_risk_id — linked OptionsEventRiskSpec id (if options)
- entry_timestamp — when trial was scheduled/entered
- exit_timestamp — when trial completed or stopped
- sample_start — sample window start date
- sample_end — sample window end date
- split_id — which validation split or fold (if applicable)
- validation_scheme — e.g., purged_cpcv, k_fold, holdout
- status — planned|running|completed|failed|abandoned|invalidated|excluded_from_review|reported
- failure_reason — structured short code + human note (if failed)
- metrics_summary — compact metrics (e.g., net_return, cost_adjusted_sharpe)
- artifact_paths — pointers to artifacts (backtest outputs, logs) — read-only
- started_at — runtime start timestamp
- completed_at — runtime completion timestamp
- created_by — user or service who created the trial
- review_required — boolean: whether manual review is required
- reported_in_review_packet — boolean: whether included in a ReviewPacket

Allowed source_lane values
- theory_first
- exploratory_anomaly
- post_hoc_theory
- confirmatory

Allowed theory_timing values
- pre_discovery
- post_discovery
- mixed
- unknown

Allowed status values
- planned
- running
- completed
- failed
- abandoned
- invalidated
- excluded_from_review
- reported

SearchSpaceManifest v1 required fields
-------------------------------------
A SearchSpaceManifest is a pre-declaration of allowed search degrees-of-freedom
and constraints. Required fields:

- search_space_id — stable unique identifier
- hypothesis_id — linked HypothesisSpec (if any)
- anomaly_id — linked ExploratoryAnomalySpec (if any)
- created_at — ISO timestamp
- created_by — author/owner
- review_owner — reviewer or owning team
- source_lane — source lane (same allowed values as TrialLedger)
- purpose — short human-readable purpose
- allowed_universe — pointer or description of allowed securities/universe
- allowed_date_range — earliest/latest allowed sample dates
- allowed_event_types — allowed event types (if event-driven)
- allowed_event_windows — pre/post event window constraints
- allowed_features — list of allowed features (names or fingerprints)
- allowed_labels — allowed label definitions
- allowed_model_classes — e.g., linear, tree, mlp, custom_class
- allowed_transforms — allowed data transforms and sanitizers
- allowed_parameter_ranges — parameter name -> allowed ranges or enumerations
- allowed_hyperparameters — declarations for hyperparameter spaces
- allowed_validation_schemes — supported validation schemes (list)
- allowed_metrics — list of metrics allowed for optimization
- forbidden_features — explicit blacklist of features
- forbidden_transforms — forbidden transforms (e.g., lookahead-augmentations)
- forbidden_data_sources — forbidden raw data sources or vendors
- forbidden_degrees_of_freedom — e.g., daily-rolling-smoothing-injection
- max_trials — soft maximum number of trials allowed
- trial_budget — TrialBudget assigned to this manifest
- stopping_rules — pre-declared stopping rules (time, budget, monotone performance)
- multiple_testing_policy — declared statistical correction policy for this search
- expected_trial_burden — human estimate of expected number of trials
- confirmatory_holdout_policy — how confirmatory holdouts are reserved
- promotion_blockers — reasons this search cannot lead to promotion without extra steps
- notes — free-form notes and references

Invariants (hard rules)
-----------------------
The following are enforcement invariants for the AED project and must be
adhered to by any tooling that later enforces these artifacts:

- No BacktestRun without SearchSpaceManifest.
- No broad parameter search without TrialBudget.
- No CandidateSpec search outside its SearchSpaceManifest.
- Every attempted trial must be recorded, including failed and abandoned trials.
- Local reruns count as trials unless they are exact deterministic reproductions
  with parameter_hash equality and identical environment artifacts.
- Exploratory trials must not disappear when an anomaly graduates into a HypothesisSpec.
- Post-hoc theory must inherit exploratory trial burden; trial counts remain bound
  to the original anomaly_id and search_space_id.
- A ReviewPacket must include TrialLedger and SearchSpaceManifest references before
  any advancement decision is recorded.
- Automated promotion remains locked until an approved automation PR exists.
- Registry mutation remains manual unless a later approved tooling PR explicitly
  implements safe mutation rules.

Exploratory anomaly inheritance
-------------------------------
When an ExploratoryAnomalySpec later becomes a HypothesisSpec the following must
occur:

- original anomaly_id must remain linked in the HypothesisSpec and TrialLedger
  entries.
- original search_space_id(s) used during exploration must remain linked.
- all exploratory trial_ids must carry forward and be included in confirmatory
  trial burden calculations.
- theory_timing for inherited trials must be set to post_discovery or mixed.
- any LLM-generated theory must be labeled as such (llm_assisted or explicit tag).
- confirmatory tests must use fresh untouched data, future windows, or a
  pre-declared holdout; retrospective re-use of exploratory windows does not
  count as confirmatory evidence.
- statistical penalties applied during assessment must include the inherited
  exploratory trial burden.

Theory-after and LLM role
-------------------------
- LLMs may help discover mechanisms and literature and may draft
  MechanismDiscoveryReport or PostHocTheoryNote artifacts.
- LLM-generated theory after empirical discovery must not reset trial count or
  erase exploratory provenance.
- LLM-generated explanations are not evidence by themselves and require human
  review and explicit linkage to TrialLedger and SearchSpaceManifest records.

Relationship to future ModelAssessmentSpec
-----------------------------------------
TrialLedger and SearchSpaceManifest are prerequisites for robust model- and
strategy-level assessment. They enable correct application of:

- Deflated Sharpe Ratio
- Probability of Backtest Overfitting (PBO)
- multiple-testing correction and FWER/FDR control
- CPCV, purged CV, and embargo procedures
- confirmatory holdout and pre-registered testing

These assessment methods are NOT implemented here; this document merely
records dependency relationships.

Relationship to existing docs
-----------------------------
This design is intentionally coordinated with the governance and protocol docs:

- docs/post_governance_implementation_roadmap.md
- docs/theory_first_research_protocol.md
- docs/event_study_design_protocol.md
- docs/options_event_risk_protocol.md
- docs/edge_hypothesis_card_v1.md
- docs/edge_hypothesis_registry_v1.md

Non-goals
---------
This PR and document are docs-only. No code, validators, or schemas are included.
Specifically:

- No code implementation
- No JSON schema created in this PR
- No validator added in this PR
- No automated registry mutation
- No automated promotion
- No live trading or production execution
- No autonomous search, Bayesian optimization, or genetic programming

Example records
---------------
Example TrialLedger row (human readable table):

| trial_id | run_id | search_run_id | hypothesis_id | anomaly_id | candidate_id | search_space_id | parameter_hash | status    | metrics_summary |
|----------|--------|---------------|---------------|------------|--------------|-----------------|----------------|-----------|-----------------|
| TRL-0001 | R-2026 | SR-0001       | HYP-0002      |            | CAND-042     | SSM-PA-0001     | abc123def456   | completed | net_return: 0.6 |

Example SearchSpaceManifest (YAML-style):

```yaml
search_space_id: SSM-PA-0001
hypothesis_id: HYP-0002
anomaly_id:
created_at: 2026-05-01T12:00:00Z
created_by: alice
review_owner: quant-research
source_lane: exploratory_anomaly
purpose: "Probe pre-earnings IV ramp parameter grid, conservative budget"
allowed_universe: 'US_OPTIONS_FRONT_MONTH'
allowed_date_range: '2018-01-01..2025-12-31'
allowed_event_types: [earnings]
allowed_event_windows: {pre: 5, post: 2}
allowed_features: [iv, implied_vol_slope, underlying_return]
allowed_labels: [next_day_iv_change]
allowed_model_classes: [rule_based, linear]
allowed_transforms: [log_return, winsorize]
allowed_parameter_ranges:
  lookback: [5, 20]
  threshold: [0.01, 0.05]
max_trials: 100
trial_budget: 100
stopping_rules:
  - time_limit_hours: 24
  - budget_exhausted: true
multiple_testing_policy: "bonferroni | DSR notes pending"
confirmatory_holdout_policy: "reserve 2026-01-01..2026-06-30 for confirmatory"
promotion_blockers:
  - insufficient_mechanism_documentation
  - missing_confirmatory_holdout
notes: "Conservative exploratory grid for pre-earnings IV ramp"
```

Conservative example names used above: pre-earnings options IV ramp, MID_WITH_SPREAD_PENALTY, and an exploratory TA moving-average crossover trial that is quarantined and trial-limited.

Implementation roadmap (follow-ups)
----------------------------------
- PR #40: MechanismDiscoveryReport / PostHocTheoryNote v1
- PR #41: EdgeHypothesisRegistry JSONL/YAML v1 (migrate registry CSV)
- PR #42: ModelAssessmentSpec v1
- PR #43: TrialLedger/SearchSpaceManifest JSON schema (machine-readable schema)
- PR #44: Local validators for TrialLedger/SearchSpaceManifest (CI path)
- PR #45: ReviewPacket integration (include TrialLedger and SearchSpaceManifest refs)



post-hoc theory
No Bayesian optimization
No genetic programming
