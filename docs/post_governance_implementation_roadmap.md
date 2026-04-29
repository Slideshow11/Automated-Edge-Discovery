# Post-Governance Implementation Roadmap

Purpose
-------
This document records a short, deliberate implementation roadmap that locks the
project's post-governance pivot following the manual governance/intake layer v1
milestone (PR #37) and the external critique phase. The intention is to move
from documentation-first governance to a careful, staged implementation plan
that preserves safety guardrails, accountability, and auditability.

Completed governance milestone
-----------------------------
As of PR #37 the governance/intake layer v1 is complete and documented. Key
deliverables in this milestone include:

- research reference map
- theory-first protocol
- exploratory anomaly lane
- event-study design protocol
- options event risk protocol
- edge hypothesis card v1
- manual edge hypothesis registry v1

Lessons from external critique
-----------------------------
External review (Gemini critique and peer review) produced these clear lessons:

- governance-first direction is sound and should be preserved as the default
  operating principle.
- markdown and CSV are not sufficient long-term for enforcement or tooling; the
  CSV is temporary and will be migrated to a structured registry format (JSONL
  or YAML) in a later PR. (registry CSV is temporary)
- TrialLedger and SearchSpaceManifest are required before any autonomous
  search, promotion automation, or broad parameter search is allowed.
- the exploratory anomaly lane must carry an inherited trial burden and be
  auditable: exploratory trials and search burden must carry forward to any
  subsequent confirmatory workflow.
- promoted status should be renamed or deprecated in favor of an explicit,
  auditable promotion record that requires fresh confirmatory evidence.
- the registry validator was deferred (registry validator deferred) to a later
  clean tooling PR so validators are introduced in a controlled CI path.

Theory-after policy
-------------------
We adopt a permissive but controlled "theory-after" policy to allow discovery
work while preserving provenance and evidence requirements.

- theory-after is allowed for exploratory work, but theory-after must be labeled
  clearly on any artifacts derived from exploratory probes. (theory-after must be labeled)
- LLMs may help discover mechanisms and literature, and may assist authors in
  drafting candidate mechanisms; these discoveries must be human-reviewed and
  linked to the original exploratory artifact. (LLMs may help discover mechanisms)
- when post-hoc mechanisms are discovered, the post-discovery theory must be
  explicitly linked to the original ExploratoryAnomalySpec so provenance is
  preserved.
- all exploratory trials and search burden must carry forward: any parameters,
  search space, and trial counts performed during exploration must be
  documented in TrialLedger records before confirmatory efforts begin.
- promotion from exploratory status requires fresh confirmatory evidence after
  the mechanism is written (fresh confirmatory evidence), not retrospective
  re-labeling.

Stop rules (what must not be implemented yet)
--------------------------------------------
Until the TrialLedger, SearchSpaceManifest, ModelAssessmentSpec, and other
core schemas are in place and validated, AED must not implement the following:

- No autonomous search
- No Bayesian optimization
- No genetic programming
- No automatic registry mutation
- No automated promotion
- No live trading
- No production execution
- No broad parameter search without TrialLedger and SearchSpaceManifest

Next implementation sequence (high-level)
-----------------------------------------
The roadmap below lists the minimal sequence of docs/schema PRs that will
enable a safe transition from docs-first governance to schema-backed
implementation and tooling. Each item should be executed as a focused, reviewable PR.

- PR #39 1 TrialLedger and SearchSpaceManifest v1 design
  - TrialLedger: canonical trial accounting (trial_id, search_space_id, params,
    result pointers, timestamps)
  - SearchSpaceManifest: compact search space schema and provenance fields
- PR #40 1 MechanismDiscoveryReport / PostHocTheoryNote v1
  - record of mechanisms discovered after exploratory runs, linking to
    ExploratoryAnomalySpec and TrialLedger entries
- PR #41 1 EdgeHypothesisRegistry JSONL/YAML v1 (migrate registry CSV 1 structured)
  - phased migration: CSV remains read-only until JSONL/YAML schema is stable
- PR #42 1 ModelAssessmentSpec v1
  - spec for declared labels, loss functions, selection rules, and assessment
- Later 1 EventStudySpec schema, OptionsEventRiskSpec schema, JumpRiskReport schema
- Later 1 registry validator introduced in a clean CI path after schema PRs

Current non-goals
-----------------
This PR is docs-only and intentionally avoids implementation changes:

- No runtime behavior changes
- No schema enforcement in this PR
- No registry mutation
- No validator included in this PR
- No production or live trading behavior
- No automated promotion

Validation
----------
This document includes references to the elements required by the governance
milestone and the implementation roadmap: TrialLedger, SearchSpaceManifest,
MechanismDiscoveryReport (or PostHocTheoryNote), ModelAssessmentSpec, and the
explicit stop rules above.

Revision history
----------------
- v0.1 1 initial draft: post-governance implementation roadmap (docs-only)

Acknowledgements
----------------
This roadmap follows the governance artifacts: research_reference_map.md,
 theory_first_research_protocol.md, event_study_design_protocol.md,
 options_event_risk_protocol.md, edge_hypothesis_card_v1.md, and
 the manual registry (docs/edge_hypothesis_registry_v1.md and CSV).

