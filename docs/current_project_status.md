# AED current project status

## Current state

AED main has completed the manual governance/intake layer v1.

The enforcement-layer design and implementation is complete. All eight governance validators (TrialLedger, SearchSpaceManifest, ModelAssessmentSpec, EdgeHypothesisRegistry, ExperimentSpec, OutcomeSpec, InstrumentUniverseSpec, EventStudySpec) are implemented, tested, and CI-wired. The governance validator milestone is complete through PR #117.

The project is not yet an autonomous discovery engine.

The project is not yet a live trading or production system.

## Completed milestones

- PR #30 manual review workflow
- PR #31 local PR readiness report
- PR #32 research reference map
- PR #33 theory-first protocol plus exploratory anomaly lane
- PR #34 event-study design protocol
- PR #35 options event risk protocol
- PR #36 edge hypothesis card v1 plus local card validator
- PR #37 manual edge hypothesis registry v1 docs plus canonical CSV
- PR #38 post-governance implementation roadmap
- PR #39 TrialLedger and SearchSpaceManifest v1 design
- PR #41 MechanismDiscoveryReport / PostHocTheoryNote v1
- PR #42 EdgeHypothesisRegistry JSONL/YAML v1 design
- PR #43 ModelAssessmentSpec v1
- PR #44 EventStudySpec / OptionsEventRiskSpec schema planning
- PR #45 Event/Options contract spec v1
- PR #46 Event/Options contract fixtures v1
- PR #47 Event/Options contract spec invariant fix
- PR #48 AED status update after Event/Options contract work
- PR #50 Event/Options validator design v1
- PR #51 local Event/Options validator implementation
- PR #52 Event/Options edge-case fixtures
- PR #53 Event/Options data_cutoff_timestamp independent parse fix
- PR #54 Event/Options strict_contract_profile fixtures
- PR #55 Event/Options validator CI wiring
- PR #56 TrialLedger and SearchSpaceManifest v1 design (split docs)
- PR #57 AED status docs update
- PR #58 TrialLedger v1 validator: local validator, JSON schema, fixtures, pytest coverage
- PR #59 SearchSpaceManifest v1 validator: local validator, JSON schema, fixtures, pytest coverage
- PR #60 Governance validators CI-wired: governance-validators job runs TRL, SSM, MAS validators
- PR #61 AED status docs update
- PR #62 Gitignore WFA state cleanup
- PR #63 ModelAssessmentSpec v1 validator: schema, fixtures, tests
- PR #64 ModelAssessmentSpec CI wiring: MAS validator added to governance helper
- PR #65 AED status update after governance validator milestone
- PR #66 EdgeHypothesisRegistry v1 design refresh: MAS linkage, ID format, anti-overfit governance
- PR #67 docs: align EHR v1 ID examples with canonical HYP-YYYY-NNNN format
- PR #68 EdgeHypothesisRegistry v1 JSON schema
- PR #69 fix(schema): enforce manual-only registry_mutation_mode in lifecycle events
- PR #70 fix(schema): close governance prose enforcement gaps
- PR #71 EdgeHypothesisRegistry v1 fixtures and README
- PR #72 EdgeHypothesisRegistry v1 local validator
- PR #73 EdgeHypothesisRegistry v1 validator pytest suite
- PR #74 EdgeHypothesisRegistry v1 CI wiring: governance-validators job now runs EHR validator
- PR #75 AED status update after EHR validator milestone
- PR #76 Domain-neutral AED architecture design note: core/domain boundary, agent tooling layer, stop rules
- PR #77 Domain-neutral modularity audit: governance layer clean, engine/ expected coupling documented
- PR #78 ExperimentSpec v1 design: domain-neutral experiment declaration schema, entry/exit rule abstractions, trial generation modes, stop rules
- PR #79 docs: fix three Codex review issues in ExperimentSpec v1 design
- PR #80 ExperimentSpec v1 JSON schema: domain-neutral schema with required fields, enums, prohibited modes, stop rules
- PR #81 Literature requirements for AED: requirements extraction from Bailey/Borwein/López de Prado/Zhu PBO, López de Prado AFML, Montgomery DOE, Ilmanen Expected Returns, Efron & Hastie CASI; artifact implications for OutcomeSpec, InstrumentUniverseSpec, EventStudySpec, OptionsEventRiskSpec, ModelAssessmentSpec extensions, ReviewPacket
- PR #82 schemas: fix ExperimentSpec allowed trial lanes constraint (theory_first, exploratory_anomaly, post_hoc_theory, confirmatory)
- PR #83 docs: fix literature requirements consistency
- PR #84 docs: fix modularity audit ambiguities
- PR #85 docs: PR #84 follow-up design doc
- PR #86 schemas: fix ExperimentSpec optional fields bug
- PR #87 schemas: align ExperimentSpec contract
- PR #88 tests: add ExperimentSpec v1 validator tests
- PR #89 schemas: align ExperimentSpec prohibited modes
- PR #90 ci: ExperimentSpec governance wiring
- PR #91 docs: literature requirements refinement
- PR #92 docs: fix OutcomeSpec ownership overreach
- PR #93 docs: fix Codex review issues
- PR #94 OutcomeSpec v1 design
- PR #95 OutcomeSpec crypto example window_unit fix
- PR #96 OutcomeSpec v1 schema
- PR #97 OutcomeSpec schema/design window-policy alignment
- PR #98 OutcomeSpec v1 fixtures
- PR #99 OutcomeSpec v1 local validator
- PR #100 OutcomeSpec validator nested object enforcement fix
- PR #101 OutcomeSpec validator tests
- PR #102 OutcomeSpec CI helper wiring
- PR #103 docs: OutcomeSpec v1 milestone status cleanup (PRs #94–#102)
- PR #104 InstrumentUniverseSpec v1 design: domain-neutral instrument eligibility universe declaration, inclusion/exclusion rules, liquidity policy, domain-neutral multi-asset-class support
- PR #105 InstrumentUniverseSpec v1 schema
- PR #106 InstrumentUniverseSpec v1 fixtures
- PR #107 InstrumentUniverseSpec schema boundary/reviewer hardening
- PR #108 InstrumentUniverseSpec v1 local validator
- PR #109 InstrumentUniverseSpec validator tests
- PR #110 InstrumentUniverseSpec CI helper wiring
- PR #112 EventStudySpec v1 design: domain-neutral event-alignment contract, window structures, timing controls, leakage policies, event family taxonomy
- PR #113 EventStudySpec v1 schema
- PR #114 EventStudySpec v1 fixtures
- PR #115 EventStudySpec v1 local validator
- PR #116 EventStudySpec validator tests
- PR #117 EventStudySpec CI helper wiring

## Current stop rules

- No autonomous search
- No Bayesian optimization
- No genetic programming
- No automatic registry mutation
- No automated promotion
- No live trading
- No production execution
- No GCRU integration into AED yet

## Known deferred implementation items

- ModelAssessmentSpec validator **complete** (PRs #63, #64): schema, fixtures, tests, CI wired
- MechanismDiscoveryReport JSON schema deferred
- PostHocTheoryNote JSON schema deferred
- EventStudySpec v1 **complete** (PRs #112–#117): design, schema, fixtures, local validator, tests, CI wired
- OptionsEventRiskSpec JSON schema deferred
- ExperimentSpec v1 **complete** (PRs #78, #79, #80, #82, #86, #87, #88, #89, #90): design, schema, fixtures, local validator, tests, CI wired
- OutcomeSpec v1 **complete** (PRs #94–#102): design, schema, fixtures, local validator, tests, CI wired
- InstrumentUniverseSpec v1 **complete** (PRs #104–#110): design, schema, fixtures, local validator, tests, CI wired
- Event/Options contract validator **complete** (PRs #50–#55): local validator, edge-case fixtures, strict_contract_profile, CI job
- TrialLedger validator **complete** (PR #58): schema, fixtures, tests, CI wired
- SearchSpaceManifest validator **complete** (PR #59): schema, fixtures, tests, CI wired
- Governance validators CI-wired **complete** (PRs #60, #64, #74, #90, #102, #117)
- EdgeHypothesisRegistry v1 **complete** (PRs #66, #68, #71, #72, #73, #74): JSON schema, fixtures, local validator, pytest, CI wired

## Next planned PRs

- InstrumentUniverseSpec v1 **complete** (PRs #104–#110): design, schema, fixtures, local validator, tests, CI wired
- EventStudySpec v1 **complete** (PRs #112–#117): design, schema, fixtures, local validator, tests, CI wired
- OptionsEventRiskSpec v1 design
- OptionsEventRiskSpec v1 schema
- OptionsEventRiskSpec fixtures
- OptionsEventRiskSpec validator, tests, and CI wiring
- PreEarningsProfile v1 as a domain-specific research module
- First thin real-data runner slice

Longer-horizon deferred work:
- MechanismDiscoveryReport schema
- PostHocTheoryNote schema
- PreEarningsProfile v1 as a domain-specific research module

## AED architecture note

AED core is domain-neutral. It enforces governance, provenance, and trial accounting without assuming any specific asset class, strategy type, or research domain. PreEarningsProfile v1 is one supported domain-specific research module — it is not the identity of the system.

See [docs/domain_neutral_aed_architecture.md](./domain_neutral_aed_architecture.md) for the full architecture design note covering:
- Core AED concepts and their generalized abstractions
- Domain modules and profiles (PreEarningsProfile, SeasonalityProfile, MacroRegimeProfile, etc.)
- Boundary rule for core vs. domain-specific fields
- Agent and tooling layer (Hermes, OpenClaw as suggestion engines)
- Stop rules and manual review rule

See [docs/domain_neutral_modularity_audit.md](./domain_neutral_modularity_audit.md) for the modularity audit covering:
- Governance layer is clean and domain-neutral (schemas, validators, fixtures, CI helpers)
- engine/ contains expected pre-earnings backtest orchestration coupling
- Event/Options validator is intentionally domain-specific
- Design implications for ExperimentSpec v1 (boundary rule, generalized abstractions)
- Recommended next PRs: ExperimentSpec → OutcomeSpec → InstrumentUniverseSpec → EventStudySpec → OptionsEventRiskSpec → PreEarningsProfile

## Operational notes

- run commands from /home/max/aed_audit_clean or use git -C /home/max/aed_audit_clean
- duplicate helper files may exist outside the repo under /home/max and should not be treated as repo files
- do not remove files outside the repo during normal PR work
- registry CSV is manual v1 only

## Event/Options current state

**Event/Options contract validator is complete (PRs #50–#55):**

- Local validator implemented (`scripts/local/validate_event_options_contract.py`)
- Edge-case invalid fixtures added (PR #52)
- data_cutoff_timestamp parsed independently of feature_timestamp (PR #53)
- strict_contract_profile fixtures and tests added (PR #54)
- Validator CI job wired into `.github/workflows/ci.yml` (PR #55)
- Decision-time anti-lookahead invariant confirmed
- event_id required for OptionsObservationSpec event-cohort research
- event identity is the canonical cohort and join key
- Fixture examples exist for valid and invalid event/options records

**Still deferred:**
- Event/Options JSON schemas deferred
- OptionsEventRiskSpec JSON schema

## Governance validators

All eight governance validators are implemented, tested, and CI-wired:

- **TrialLedger** (PR #58): `scripts/local/validate_trial_ledger.py`, schema, 5 fixtures, 21 tests. CI: `governance-validators` job.
- **SearchSpaceManifest** (PR #59): `scripts/local/validate_search_space_manifest.py`, schema, 6 fixtures, 29 tests. CI: `governance-validators` job.
- **ModelAssessmentSpec** (PRs #63, #64): `scripts/local/validate_model_assessment_spec.py`, schema, 6 fixtures, 38 tests. CI: `governance-validators` job.
- **EdgeHypothesisRegistry** (PRs #68, #71, #72, #73, #74): `scripts/local/validate_edge_hypothesis_registry.py`, schema, 10 fixtures, 31 tests. CI: `governance-validators` job.
- **ExperimentSpec** (PRs #78, #79, #80, #82, #86, #87, #88, #89, #90): `scripts/local/validate_experiment_spec.py`, schema, 12 fixtures, 77 tests. CI: `governance-validators` job.
- **OutcomeSpec** (PRs #94–#102): `scripts/local/validate_outcome_spec.py`, schema, 21 fixtures, 111 tests. CI: `governance-validators` job.
- **InstrumentUniverseSpec** (PRs #104–#110): `scripts/local/validate_instrument_universe_spec.py`, schema, 21 fixtures, 126 tests. CI: `governance-validators` job.
- **EventStudySpec** (PRs #112–#117): `scripts/local/validate_event_study_spec.py`, schema, 22 fixtures, 106 tests. CI: `governance-validators` job.

Total CI-enforced validator tests: 539 governance via `governance-validators` job + 18 Event/Options via `validator` job = **557 total**.

## Operational notes
