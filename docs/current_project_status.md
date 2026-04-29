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
- PR #41 MechanismDiscoveryReport / PostHocTheoryNote v1
- PR #42 EdgeHypothesisRegistry JSONL/YAML v1 design
- PR #43 ModelAssessmentSpec v1
- PR #44 EventStudySpec / OptionsEventRiskSpec schema planning
- PR #45 Event/Options contract spec v1
- PR #46 Event/Options contract fixtures v1
- PR #47 Event/Options contract spec invariant fix
- PR #48 AED status update after Event/Options contract work

## Current stop rules

- No autonomous search
- No Bayesian optimization
- No genetic programming
- No automatic registry mutation
- No automated promotion
- No live trading
- No production execution

## Known deferred implementation items

- ModelAssessmentSpec JSON schema deferred
- ModelAssessmentSpec validator deferred
- MechanismDiscoveryReport JSON schema deferred
- PostHocTheoryNote JSON schema deferred
- EventStudySpec JSON schema deferred
- OptionsEventRiskSpec JSON schema deferred
- Event/Options contract validator deferred
- TrialLedger / SearchSpaceManifest validators deferred
- EdgeHypothesisRegistry JSON schema and validator deferred
- registry validator deferred

## Next planned PRs

Next new PR likely #49: choose between Event/Options contract validator design, MechanismDiscoveryReport JSON schema, EdgeHypothesisRegistry JSON schema and validator, or TrialLedger/SearchSpaceManifest validator design.

## Operational notes

- run commands from /home/max/aed_audit_clean or use git -C /home/max/aed_audit_clean
- duplicate helper files may exist outside the repo under /home/max and should not be treated as repo files
- do not remove files outside the repo during normal PR work
- registry CSV is manual v1 only

## Event/Options current state

- Decision-time anti-lookahead invariant is corrected.
- event_id is required for OptionsObservationSpec event-cohort research.
- event identity is the canonical cohort and join key.
- Fixture examples exist for valid and invalid event/options records.
- Event/Options validators and JSON schemas are deferred.
- Event/Options contract validator deferred.
- EventStudySpec and OptionsEventRiskSpec JSON schemas deferred.
- Next new PR likely #49.
