# AED document map

## Purpose

This file is the navigation map for AED governance, research protocols, roadmap docs, and local tooling docs.

AED currently uses a governance-first research workflow. The document map helps future reviewers and agents find the canonical files before starting new implementation work.

## Current milestone

- governance/intake layer v1 complete at PR #37
- post-governance implementation roadmap merged at PR #38
- TrialLedger/SearchSpaceManifest v1 design merged at PR #39
- next planned work is MechanismDiscoveryReport / PostHocTheoryNote v1
- autonomous search remains locked

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
| docs/trial_ledger_search_space_manifest_v1.md | EnforcementLayer | Defines TrialLedger, SearchSpaceManifest, TrialBudget, SearchRun, ParameterHash, and trial burden rules. | Design v1 |

## Local tooling map

| Script | Purpose | Mutation behavior |
|---|---|---|
| scripts/local/pr_readiness_report.py | Produces local branch, diff, changed-file, untracked-file, recent-commit, and optional PR metadata reports. | Read-only |
| scripts/local/validate_edge_hypothesis_card.py | Validates required content and guardrails in the edge hypothesis card doc. | Read-only |
| scripts/local/evaluate_ledger_entry.py | Evaluates one manual ledger entry for review-only labels and rationale. | Read-only output |
| scripts/local/make_run_review_packet.py | Builds a manual review packet from ledger/run artifacts. | Writes only requested packet output |
| scripts/local/_ledger_review_shared.py | Shared helper logic for ledger review tooling. | Helper module |
| scripts/local/_smoke_shared.py | Shared helper logic for local smoke workflow scripts. | Helper module |
| scripts/local/smoke_preearn_lifecycle.py | Local smoke workflow for pre-earnings lifecycle artifacts. | Local smoke only |
| scripts/local/smoke_preearn_bridge.py | Local bridge smoke helper for pre-earnings integration. | Local smoke only |

If a script listed above is not present in a checkout, treat it as "not present in current checkout" rather than inferring behavior from duplicate files outside the repo.

## Deferred tooling

- registry validator is deferred to a later tooling PR
- schema validators are deferred
- TrialLedger/SearchSpaceManifest validators are deferred
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
- EventStudySpec
- OptionsEventRiskSpec
- JumpRiskReport
- ReviewPacket
- ManualDecision
