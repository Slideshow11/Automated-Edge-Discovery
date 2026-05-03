# AED document map

## Purpose

This file is the navigation map for AED governance, research protocols, roadmap docs, and local tooling docs.

AED currently uses a governance-first research workflow. The document map helps future reviewers and agents find the canonical files before starting new implementation work.

## Current milestone

- governance/intake layer v1 complete at PR #37
- post-governance implementation roadmap merged at PR #38
- Event/Options contract validator complete (PRs #50–#55)
- TrialLedger and SearchSpaceManifest v1 design complete (PR #56)
- TrialLedger validator complete (PR #58)
- SearchSpaceManifest validator complete (PR #59)
- Governance validators CI-wired (PR #60)
- ModelAssessmentSpec v1 schema, validator, fixtures, and CI wiring complete (PRs #63, #64)
- Governance validator milestone complete: all three manifests (TRL, SSM, MAS) enforced in CI
- EdgeHypothesisRegistry v1 schema, fixtures, local validator, pytest, and CI wiring complete (PRs #66, #68, #71, #72, #73, #74)
- ExperimentSpec v1 design, JSON schema, fixtures, local validator, tests, and CI wiring complete (PRs #78, #79, #80, #82, #86, #87, #88, #89, #90)
- Literature requirements baseline established (PR #81)
- OutcomeSpec v1 design, schema, fixtures, local validator, tests, and CI wiring complete (PRs #94–#102)
- InstrumentUniverseSpec v1 design, schema, fixtures, local validator, tests, and CI wiring complete (PRs #104–#110)
- EventStudySpec v1 design, schema, fixtures, local validator, tests, and CI wiring complete (PRs #112–#117)
- OptionsEventRiskSpec v1 design, schema, fixtures, local validator, tests, and CI wiring complete (PRs #119–#128)

## Document groups

| File | Layer | Purpose | Status |
|---|---|---|---|
| docs/manual_review_workflow.md | ManualReviewLayer | Describes the manual lifecycle review workflow from local smoke artifacts to ledger evaluation and review packets. | Active |
| docs/research_reference_map.md | ResearchReferenceLayer | Maps methodology references into AED framework layers and implementation gates. | Active |
| docs/theory_first_research_protocol.md | TheoryLayer | Defines theory-first workflow, ExploratoryAnomalySpec, HypothesisSpec, CandidateSpec, BacktestRun, ReviewPacket, and ManualDecision rules. | Active |
| docs/event_study_design_protocol.md | EventStudyLayer | Defines event-study design requirements, timing, windows, normal-performance model, inference, and bias checks. | Active |
| docs/options_event_risk_protocol.md | JumpRiskLayer | Defines options event risk requirements around IV ramp, jump exposure, crush, skew, term structure, and execution realism. | Active |
| docs/edge_hypothesis_card_v1.md | IntakeLayer | Defines the manual hypothesis intake card and required fields before testing. | Active |
| docs/edge_hypothesis_registry_v1.md | RegistryLayer | Defines the manual edge hypothesis registry v1 and lifecycle constraints. | Active |
| docs/edge_hypothesis_registry.csv | RegistryLayer | Manual v1 registry CSV using the canonical hypothesis registry columns. | Manual v1 |
| docs/post_governance_implementation_roadmap.md | RoadmapLayer | Locks the post-governance pivot toward enforcement, schema-backed artifacts, and trial accounting. | Active |
| docs/domain_neutral_aed_architecture.md | ArchitectureLayer | Defines AED core as domain-neutral: generalized abstractions, domain modules, agent tooling, and stop rules. | Active |
| docs/domain_neutral_modularity_audit.md | ArchitectureLayer | Audit of existing codebase for pre-earnings/event/options coupling. Identifies governance layer as clean; engine/ as expected domain coupling. | Active |
| docs/experiment_spec_v1_design.md | ArchitectureLayer | Domain-neutral experiment declaration schema: entry/exit rule abstractions, study types, trial generation modes, prohibited modes, stop rules, agent tooling constraints. | Active v1 design |
| docs/outcome_spec_v1_design.md | ArchitectureLayer | OutcomeSpec v1: outcome metric declaration, outcome_window, labeling_scheme, return_basis, benchmark_policy, observation_count_policy, evidence_role_requirements, purge_embargo_policy, computed-assessment field restrictions. | Active v1 design |
| docs/instrument_universe_spec_v1_design.md | ArchitectureLayer | InstrumentUniverseSpec v1: domain-neutral instrument eligibility universe, inclusion/exclusion rules, liquidity policy, survivorship policy, multi-asset-class support via domain_profile_refs. | Active v1 design |
| docs/event_study_spec_v1_design.md | ArchitectureLayer | EventStudySpec v1: domain-neutral event-alignment contract, event families, window structures, timing controls, leakage policies, event source priority, collision/dedup rules. | Active v1 design |
| docs/options_event_risk_spec_v1_design.md | ArchitectureLayer | OptionsEventRiskSpec v1: domain-specific options event-risk specialization of EventStudySpec, contract selection, liquidity/pricing policies, gap exposure, domain-neutral pre-earnings profile hook, boundary with EventStudySpec and PreEarningsProfile. | Active v1 design |
| docs/literature_requirements_for_aed.md | RequirementsLayer | Requirements extraction from Bailey/Borwein/López de Prado/Zhu PBO, López de Prado AFML, Montgomery DOE, Ilmanen Expected Returns, Efron & Hastie CASI. Maps literature ideas to AED artifact implications for OutcomeSpec, InstrumentUniverseSpec, EventStudySpec, OptionsEventRiskSpec, ModelAssessmentSpec extensions, and ReviewPacket design. | Active requirements baseline |
| docs/trial_ledger_v1_design.md | EnforcementLayer | Defines TrialLedger v1: append-only trial record, identity fields, source lanes, promotion rules, and governance states. | Active v1 design |
| docs/search_space_manifest_v1_design.md | EnforcementLayer | Defines SearchSpaceManifest v1: pre-declared search boundaries, budget, constraints, forbidden modes, and burden accounting. | Active v1 design |
| docs/trial_ledger_search_space_manifest_v1.md | EnforcementLayer | **Historical combined design note (PR #39).** For v1 authoritative references, use `docs/trial_ledger_v1_design.md` and `docs/search_space_manifest_v1_design.md`. | Historical |

## Local tooling map

| Script | Purpose | Mutation behavior |
|---|---|---|
| scripts/local/pr_readiness_report.py | Produces local branch, diff, changed-file, untracked-file, recent-commit, and optional PR metadata reports. | Read-only |
| scripts/local/validate_edge_hypothesis_card.py | Validates required content and guardrails in the edge hypothesis card doc. | Read-only |
| scripts/local/validate_search_space_manifest.py | Validates a single SearchSpaceManifest v1 JSON entry against the schema and governance rules. | Read-only |
| scripts/local/validate_trial_ledger.py | Validates a single TrialLedger v1 JSON entry against the schema and governance rules. | Read-only |
| scripts/local/validate_event_options_contract.py | Validates event and options observation CSV against the Event/Options contract spec. | Read-only |
| scripts/ci/validate_event_options_contract.sh | CI helper wrapper that runs the Event/Options validator across all fixture profiles and pytest. | CI helper |
| scripts/ci/validate_governance_manifests.sh | CI helper that runs TrialLedger, SearchSpaceManifest, ModelAssessmentSpec, EdgeHypothesisRegistry, ExperimentSpec, OutcomeSpec, InstrumentUniverseSpec, EventStudySpec, and OptionsEventRiskSpec validators and their pytest suites. | CI helper |
| scripts/local/evaluate_ledger_entry.py | Evaluates one manual ledger entry for review-only labels and rationale. | Read-only output |
| scripts/local/make_run_review_packet.py | Builds a manual review packet from ledger/run artifacts. | Writes only requested packet output |
| scripts/local/_ledger_review_shared.py | Shared helper logic for ledger review tooling. | Helper module |
| scripts/local/_smoke_shared.py | Shared helper logic for local smoke workflow scripts. | Helper module |
| scripts/local/smoke_preearn_lifecycle.py | Local smoke workflow for pre-earnings lifecycle artifacts. | Local smoke only |
| scripts/local/smoke_preearn_bridge.py | Local bridge smoke helper for pre-earnings integration. | Local smoke only |

If a script listed above is not present in a checkout, treat it as "not present in current checkout" rather than inferring behavior from duplicate files outside the repo.

## Deferred tooling

- Event/Options contract validator **complete** (CI job in `.github/workflows/ci.yml`)
- TrialLedger validator **complete** (PR #58): local validator, JSON schema, fixtures
- SearchSpaceManifest validator **complete** (PR #59): local validator, JSON schema, fixtures
- ModelAssessmentSpec validator **complete** (PRs #63, #64): local validator, JSON schema, fixtures, CI wired
- EdgeHypothesisRegistry v1 validator **complete** (PRs #68, #71, #72, #73, #74): JSON schema, fixtures, local validator, pytest, CI wired
- ExperimentSpec v1 **complete** (PRs #78, #79, #80, #82, #86, #87, #88, #89, #90): JSON schema, fixtures, local validator, tests, CI wired
- OutcomeSpec v1 **complete** (PRs #94–#102): design, JSON schema, fixtures, local validator, tests, CI wired
- InstrumentUniverseSpec v1 **complete** (PRs #104–#110): design, JSON schema, fixtures, local validator, tests, CI wired
- EventStudySpec v1 **complete** (PRs #112–#117): design, JSON schema, fixtures, local validator, tests, CI wired
- OptionsEventRiskSpec v1 **complete** (PRs #119–#128): design, JSON schema, fixtures, local validator, tests, CI wired
- MechanismDiscoveryReport schema deferred
- PostHocTheoryNote schema deferred
- PreEarningsProfile v1 as a domain-specific research module deferred
- ModelAssessmentSpec extensions deferred (uncertainty quantification, bootstrap, robustness, null model — requirements baseline in PR #81)
- ReviewPacket design deferred (requirements baseline in PR #81)
- autonomous search and optimization tooling are locked until trial accounting exists

## Canonical terms

- TrialLedger
- SearchSpaceManifest
- TrialBudget
- SearchRun
- ParameterHash
- ExploratoryAnomalySpec
- MechanismDiscoveryReport
- PostHocTheoryNote
- ModelAssessmentSpec
- ExperimentSpec
- EventStudySpec
- OptionsEventRiskSpec
- JumpRiskReport
- ReviewPacket
- ManualDecision
- OutcomeSpec
- InstrumentUniverseSpec
- TrialFamilyID
- PBO
- DSR
- purged_cross_validation
- embargo_policy
- walk_forward_analysis

## Current Event/Options milestone

- Event/Options schema planning is complete.
- Event/Options contract spec v1 is present.
- Event/Options contract validator is **complete** (PRs #50–#55): local validator, CI job, edge-case fixtures, strict_contract_profile.
- Contract invariant fix is merged.
- Event/Options contract fixtures v1 are present.
- Registry CSV remains manual v1 only.
- Event/Options JSON schemas deferred.

See the following canonical docs and fixtures:

- docs/event_options_schema_planning_v1.md
- docs/event_options_contract_spec_v1.md
- docs/event_options_contract_validator_design_v1.md
- fixtures/event_options_contract_v1/README.md
