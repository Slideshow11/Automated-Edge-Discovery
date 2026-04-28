# AED Research Reference Map

Purpose
-------
This document ties reviewed methodology references to Automated Edge Discovery (AED) framework layers, roadmap modules, implementation gates, and locked-until-safe features. It is a research-first map that documents how classic and modern references inform AED design decisions, required metadata, and explicit implementation gates.

AED framework layers
--------------------
- TheoryLayer
- EconometricLayer
- StatisticalLearningLayer
- JumpRiskLayer
- AuditLayer
- ManualReviewLayer

Reference table
---------------
Each reviewed reference below is paired with AED implications and the AED layers it primarily affects.

Reviewed references and AED implications
----------------------------------------

1. Machine Learning for Asset Managers
   - Status: reviewed.
   - AED implications:
     - theory-first research (theory-first)
     - backtests are not research tools (No strategy acceptance from backtest alone)
     - ML helps discover variables and theories
     - train-set versus test-set overfitting
     - report all trials
     - DSR/FWER
     - CPCV / purged validation
     - Monte Carlo synthetic data
     - labels and meta-labeling
     - feature importance
     - denoising/detoning and clustering later
     - portfolio construction later
   - Layers: StatisticalLearningLayer, AuditLayer, ManualReviewLayer

2. The Econometrics of Financial Markets
   - Status: reviewed.
   - AED implications:
     - financial econometrics is model-based inference under uncertainty
     - empirical tests must be tied to the financial model being tested
     - event-study design
     - normal-return models
     - abnormal-return measurement
     - event-window and estimation-window metadata
     - cluster-aware event inference
     - power, sampling interval, event-date uncertainty, and bias checks
     - return predictability diagnostics
     - market microstructure controls
     - derivative-pricing econometrics
     - nonlinear volatility diagnostics
     - data-snooping warnings
   - Layers: EconometricLayer, AuditLayer, ManualReviewLayer

3. The Elements of Statistical Learning
   - Status: reviewed.
   - AED implications:
     - add ModelAssessmentSpec before serious ML modules (ModelAssessmentSpec)
     - separate model assessment from strategy assessment
     - declared label and loss function for every predictive model
     - train, validation, and test error tracking
     - model complexity and selection rule tracking
     - conservative selection rules such as one standard error where appropriate
     - regularization and explicit complexity controls
     - feature importance as a report, not proof of monetizable edge
     - ensemble diagnostics and caution around OOB for time-dependent labels
     - high-dimensional guardrails
     - multiple testing and FDR
     - unsupervised learning for taxonomy, clustering, and redundancy maps
   - Layers: StatisticalLearningLayer, AuditLayer

4. Financial Modelling with Jump Processes
   - Status: reviewed.
   - AED implications:
     - add explicit JumpRiskLayer for event-driven and options strategies (JumpRiskLayer)
     - treat earnings announcements as scheduled jump risk
     - distinguish pre-event drift and IV ramp from event jump exposure
     - add OptionsEventRiskSpec before serious pre-earnings options review (OptionsEventRiskSpec)
     - add JumpRiskReport later (JumpRiskReport)
     - do not assume options can be perfectly hedged
     - disclose residual hedge risk
     - track skew, term structure, short-dated option behavior, and expected move error
     - decompose option PnL into delta, vega, theta, spread cost, skew, and residual components where possible
     - use jump scenario simulation later
     - do not implement full Levy pricing or calibration yet
   - Layers: JumpRiskLayer, EconometricLayer, ManualReviewLayer

Queued references and implementation gates
-----------------------------------------
These references are queued for review. Their implementation is gated until required infrastructure exists.

1. Advances in Financial Machine Learning
   - Status: queued.
   - Gate:
     - Review before implementing formal PSR, DSR, PBO, CPCV, purged CV, embargo, labeling, and sample weighting.

2. Bayesian Optimization and Data Science
   - Status: queued.
   - Gate:
     - Do not implement Bayesian optimization until TrialLedger, SearchSpaceManifest, DSR/FWER, and CPCV exist.

3. Genetic Programming Theory and Practice II
   - Status: queued.
   - Gate:
     - Do not implement genetic programming until TrialLedger, SearchSpaceManifest, DSR/FWER, CPCV, and complexity/bloat controls exist.

4. Time Series Analysis: Forecasting and Control
   - Status: queued.
   - Gate:
     - Review before implementing deeper time-series diagnostics beyond basic checks.

5. Stochastic Volatility Modeling
   - Status: queued.
   - Gate:
     - Review before implementing advanced smile/skew/stochastic volatility feature models.

Locked-until-safe features
--------------------------
The following features are locked until explicit infrastructure and audit controls exist:
- TrialLedger / TrialManifest (TrialLedger)
- SearchSpaceManifest (SearchSpaceManifest)
- Formal DSR and FWER workflows (DSR, FWER)
- CPCV and purged cross-validation (CPCV)
- ModelAssessmentSpec and LabelSpec (ModelAssessmentSpec, LabelSpec)
- OptionsEventRiskSpec and EventStudySpec (OptionsEventRiskSpec)
- JumpRiskReport and FeatureImportanceReport (JumpRiskReport, FeatureImportanceReport)

Implementation gates (explicit rules)
------------------------------------
- No strategy acceptance from backtest alone.
- Every candidate must tie back to a theory or mechanism.
- Every broad search must report all trials (report all trials).
- No Bayesian optimization before TrialLedger, SearchSpaceManifest, DSR/FWER, and CPCV.
- No genetic programming before TrialLedger, SearchSpaceManifest, DSR/FWER, CPCV, and complexity/bloat controls.
- No automated promotion before review packets include trial/search-space metadata.
- No options event strategy should be reviewed without event jump exposure metadata.
- No ML model should enter review without declared label, loss, validation split, model complexity controls, and selection rule (ModelAssessmentSpec, LabelSpec).
- No return predictability claim should be reviewed without baseline diagnostics.
- No event strategy should be reviewed without event window, estimation window, normal-performance model, abnormal-performance measure, sampling interval, event-date uncertainty handling, and bias checklist.

Updated roadmap
----------------
Planned follow-ups (docs and schemas):
- PR #33 docs: add theory-first AED research protocol
- PR #34 docs: add event-study design protocol
- PR #35 docs: add options event risk protocol
- PR #36 docs: add model assessment and selection protocol
- PR #37 schema: add EventStudySpec v1
- PR #38 schema: add OptionsEventRiskSpec v1
- PR #39 schema: add ModelAssessmentSpec v1
- PR #40 schema: add LabelSpec v1
- PR #41 schema: add TrialLedger or TrialManifest v1
- PR #42 schema: add SearchSpaceManifest v1
- PR #43 review packet includes trial, search-space, model assessment, event study, and jump risk metadata
- PR #44 audit: formal PSR, DSR, FWER design doc
- PR #45 validation: CPCV and purged validation design skeleton
- PR #46 diagnostics: FeatureImportanceReport schema
- PR #47 diagnostics: PredictabilityDiagnostics design doc
- PR #48 diagnostics: MicrostructureQualityReport schema
- PR #49 diagnostics: EventStudyReport schema
- PR #50 diagnostics: JumpRiskReport schema

Immediate next tasks
--------------------
1. Publish this research_reference_map as a docs-only PR that documents reviewed references and gates.
2. Draft PR #33 (theory-first AED research protocol) and begin specification for ModelAssessmentSpec and LabelSpec.
3. Draft PR #34 (event-study design protocol) with example EventStudySpec v1.

Non-goals
---------
- This document does not implement schema changes, runtime logic, registry mutations, promotions, or backtest runs.
- It intentionally avoids prescribing exact code-level APIs  those belong in follow-up schema PRs listed in the roadmap.

Acknowledgements
----------------
Reviewed references (selected):
- Machine Learning for Asset Managers
- The Econometrics of Financial Markets
- The Elements of Statistical Learning
- Financial Modelling with Jump Processes

