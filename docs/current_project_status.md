# AED current project status

## Current state

AED main has completed the manual governance/intake layer v1.

The enforcement-layer design is complete for TrialLedger and SearchSpaceManifest (PR #56). Implementation of their validators and schemas is next.

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

- ModelAssessmentSpec JSON schema deferred
- ModelAssessmentSpec validator deferred
- MechanismDiscoveryReport JSON schema deferred
- PostHocTheoryNote JSON schema deferred
- EventStudySpec JSON schema deferred
- OptionsEventRiskSpec JSON schema deferred
- Event/Options contract validator **complete** (PRs #50–#55): local validator, edge-case fixtures, strict_contract_profile, CI job
- TrialLedger / SearchSpaceManifest validators: both **complete** (PRs #58 and #59)
- EdgeHypothesisRegistry JSON schema and validator deferred
- registry validator deferred

## Next planned PRs

Next PR #60: wire TrialLedger and SearchSpaceManifest validators into CI.

Recommended future work:
- CI wiring for TrialLedger and SearchSpaceManifest validators
- EdgeHypothesisRegistry JSONL migration
- EventStudySpec / OptionsEventRiskSpec schemas

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
- SearchSpaceManifest validator **complete** (PR #59): local validator, JSON schema, fixtures
- Event/Options JSON schemas deferred
- EventStudySpec JSON schema
- OptionsEventRiskSpec JSON schema

## Operational notes
