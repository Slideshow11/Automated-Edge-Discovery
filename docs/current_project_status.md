# AED current project status

## Current state

AED main has completed the manual governance/intake layer v1.

The enforcement-layer design has started with TrialLedger/SearchSpaceManifest.

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

## Current stop rules

- No autonomous search
- No Bayesian optimization
- No genetic programming
- No automatic registry mutation
- No automated promotion
- No live trading
- No production execution

## Known deferred items

- registry validator deferred
- schema validators deferred
- EdgeHypothesisRegistry JSONL/YAML migration deferred
- ModelAssessmentSpec deferred
- MechanismDiscoveryReport / PostHocTheoryNote deferred
- EventStudySpec schema deferred
- OptionsEventRiskSpec schema deferred
- JumpRiskReport deferred

## Next planned PRs

- PR #41 MechanismDiscoveryReport / PostHocTheoryNote v1
- PR #42 EdgeHypothesisRegistry JSONL/YAML v1
- PR #43 ModelAssessmentSpec v1
- PR #44 EventStudySpec / OptionsEventRiskSpec schema planning
- PR #45 validator/tooling cleanup

## Operational notes

- run commands from /home/max/aed_audit_clean or use git -C /home/max/aed_audit_clean
- duplicate helper files may exist outside the repo under /home/max and should not be treated as repo files
- do not remove files outside the repo during normal PR work
- registry CSV is manual v1 only
