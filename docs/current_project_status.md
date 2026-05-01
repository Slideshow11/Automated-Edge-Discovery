# AED current project status

## Current state

AED main has completed the manual governance/intake layer v1.

The enforcement-layer design and implementation is complete. All four governance validators (TrialLedger, SearchSpaceManifest, ModelAssessmentSpec, EdgeHypothesisRegistry) are implemented, tested, and CI-wired. The governance validator milestone is complete.

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
- EventStudySpec JSON schema deferred
- OptionsEventRiskSpec JSON schema deferred
- ExperimentSpec JSON schema deferred
- OutcomeSpec JSON schema deferred
- InstrumentUniverseSpec JSON schema deferred
- Event/Options contract validator **complete** (PRs #50–#55): local validator, edge-case fixtures, strict_contract_profile, CI job
- TrialLedger validator **complete** (PR #58): schema, fixtures, tests, CI wired
- SearchSpaceManifest validator **complete** (PR #59): schema, fixtures, tests, CI wired
- Governance validators CI-wired **complete** (PRs #60, #64, #74)
- EdgeHypothesisRegistry v1 **complete** (PRs #66, #68, #71, #72, #73, #74): JSON schema, fixtures, local validator, pytest, CI wired

## Next planned PRs

Conservative next steps:
- ExperimentSpec v1 schema and validator design
- OutcomeSpec v1 schema and validator design
- InstrumentUniverseSpec v1 schema and validator design
- EventStudySpec JSON schema
- OptionsEventRiskSpec JSON schema

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
- EventStudySpec JSON schema
- OptionsEventRiskSpec JSON schema

## Governance validators

All four governance validators are implemented, tested, and CI-wired:

- **TrialLedger** (PR #58): `scripts/local/validate_trial_ledger.py`, schema, 5 fixtures, 21 tests. CI: `governance-validators` job.
- **SearchSpaceManifest** (PR #59): `scripts/local/validate_search_space_manifest.py`, schema, 6 fixtures, 29 tests. CI: `governance-validators` job.
- **ModelAssessmentSpec** (PR #63, #64): `scripts/local/validate_model_assessment_spec.py`, schema, 6 fixtures, 38 tests. CI: `governance-validators` job.
- **EdgeHypothesisRegistry** (PRs #68, #71, #72, #73, #74): `scripts/local/validate_edge_hypothesis_registry.py`, schema, 10 fixtures, 27 tests. CI: `governance-validators` job.

Total CI-enforced validator tests: 151 (18 Event/Options via `validator` job + 133 governance via `governance-validators` job).

## Operational notes
