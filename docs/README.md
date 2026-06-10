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
- PreEarningsProfile v1 design, schema, fixtures, local validator, tests, and CI wiring complete (PRs #130–#137)
- First thin real-data runner slice v1 design complete (PR #139)
- RunnerOutputSpec v1 design, fixtures, and validator complete (PRs #140, #142, #169)
- Tasker / Executor / PR gate packet architecture design complete (PR #188, design-only; no runtime behavior)
- PR gate watchdog complete (PRs #189, #190, #191)
- Tasker packet scaffold complete (PR #192, design-only; no autonomous Tasker agent)
- PR gate controller complete (PR #199): end-to-end orchestrator chaining classifier → task draft → kanban plan, dry-run by default
- PR gate merge-ready notification packet complete (PR #200): consumes controller output, produces Telegram-ready authorization packet; does not send Telegram
- PR gate controller live-smoke harness complete (PR #201): read-only smoke verifying full chain via 4 synthetic scenarios; prepares future auto-dispatch wiring
- CI workflow trigger invariant checker complete (PR #204): read-only local checker that validates GitHub Actions CI workflow trigger invariants; detects workflow-level paths filters; YAML 1.1 boolean-`on` quirk handled; 17 invariants
- CI now cancels stale PR workflow runs (PR #210): GitHub Actions workflow-level concurrency added to ci.yml; stale in-progress runs on PR branches are cancelled to save Actions minutes; main branch runs are never cancelled; 6 new concurrency invariants in validate_ci_workflow_invariants.py; 9 new tests; PR scope: ci.yml, validate_ci_workflow_invariants.py, test_validate_ci_workflow_invariants.py, docs
- Phase-ledger merge-readiness stack complete (PRs #390, #391, #392, #393): runner now emits `run_summary.json` with phase-ledger fields (PR #391), a leaf adapter consumes the runner's phase ledger (PR #392), and an opt-in wrapper (`scripts/local/merge_readiness_with_phase_ledger.py`) composes the phase-gate adapter with `merge_pr_safely.py` (PR #393). Default-off; never merges; human merge authorization remains required. Operator guide: `docs/phase_ledger_merge_readiness_wrapper.md`. PR scope: `scripts/local/phase_ledger.py`, `scripts/local/phase_exec.py`, `scripts/local/run_autocoder_single_task.py`, `scripts/local/finalize_with_phase_ledger.py`, `scripts/local/merge_readiness_with_phase_ledger.py`, `tests/test_*`, docs
- Whole-workflow operator path guide v1 added: `docs/aed_whole_workflow_operator_path.md` is a docs-only top-level operator map that connects task packet, runner, phase ledger, run summary, final gate, merge-readiness wrapper, Codex review, review-thread state, human merge authorization, guarded merge, post-merge CI, audit log, and worktree cleanup. Defines the v1 reporting lifecycle vocabulary (reporting-only, not a code enum). No script behavior changes. See the per-section references in the new doc for the authoritative lower-level docs at each stage.
- Known-safe command cookbook v1 added: `docs/aed_known_safe_command_cookbook.md` is a docs-only centralized command reference for AED governance workflows. It documents copy/paste-safe command shapes for PR inspection, bounded CI polling, Codex review flow, review-thread inspection, guarded merge, post-merge main CI audit, audit append, and temp worktree cleanup. Companion to the operator path doc (which describes the path; this doc describes the commands). Includes a state-to-command index, a forbidden-command prose list, and a future-tools section. No script behavior changes.
- Lifecycle state registry v1 added: `docs/aed_lifecycle_state_registry.md` introduces a canonical machine-readable registry of AED lifecycle states (schemas/aed_lifecycle_states_v1.json) plus a small stdlib-only reader/validator CLI (scripts/local/aed_lifecycle_states.py) and focused tests (tests/test_aed_lifecycle_states.py). The registry centralizes the 18 canonical states currently referenced across the operator path, command cookbook, and merge authorization guard, with per-state evidence requirements, allowed transitions, allowed/forbidden mutations, and authorization policy. Reporting vocabulary only; no script behavior changes. The CLI enforces structural and policy invariants (terminal states have no further mutations, only MERGE_READY_AWAITING_HUMAN_AUTHORIZATION sets merge_allowed, etc.) and is stdlib-only. Companion to the operator path doc and command cookbook. Cross-references the whole-workflow operator path and known-safe command cookbook.
- Audit log append-only closeout rule codified (2026-06-10): the canonical `AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR` state in `schemas/aed_lifecycle_states_v1.json` now codifies the rule that audit rows, once appended, are not deleted, trimmed, rewritten, or replaced without explicit human authorization. The `AUDIT_APPEND_NEEDS_OPERATOR` alias is documented in the entry's description and notes (the registry stores a single canonical state; aliases are reporting labels, not structural features). The `forbidden_mutations` list gained `comment_delete`, `review_dismiss`, and `force_push` to express the broader repository-side prohibitions while an audit-ambiguity hold is in effect. Docs-only: no script behavior changes. Cross-references added in `docs/aed_whole_workflow_operator_path.md` §7 (new), §5 authority table, §4 lifecycle states table, §3 stage 12 row; `docs/aed_known_safe_command_cookbook.md` §11.1 (new) and §14 state-to-command index; `docs/aed_lifecycle_state_registry.md` §10 (new). 18 new tests in `tests/test_aed_lifecycle_states.py` covering the entry's description, notes, evidence, allowed_next_states, and forbidden_mutations contract.

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
| docs/phase_ledger_merge_readiness_wrapper.md | MergeReadinessLayer | Operator guide for the PR #393 opt-in wrapper `scripts/local/merge_readiness_with_phase_ledger.py`: stack summary (PRs #390–#393), default-off vs opt-in flow, required flags, copyable example, failure modes, guardrails, when-not-to-use. | Active v1 |
| docs/aed_whole_workflow_operator_path.md | OperatorPathLayer | Top-level operator path v1 connecting task packet, runner, phase ledger, run summary, final gate, merge-readiness wrapper, Codex review, review-thread state, human merge authorization, guarded merge, post-merge CI, audit log, and worktree cleanup. Defines the v1 reporting lifecycle vocabulary (NOT_RUN, HOLD_*, MERGE_READY_*, PR_MERGED_*). Operator-vs-agent authority table. Lessons from PR #394. Future work items. Reporting-only; no script behavior changes. | Active v1 |
| docs/aed_known_safe_command_cookbook.md | CommandCookbookLayer | Known-safe command cookbook v1 for AED governance workflows. Centralized copy/paste-safe command shapes for PR inspection, bounded CI polling, Codex review flow, review-thread inspection, guarded merge, post-merge main CI audit, audit append, and temp worktree cleanup. Companion to `docs/aed_whole_workflow_operator_path.md`. Includes a state-to-command index (§14), a forbidden-command prose list (§13), and a future-tools section (§15). Docs-only; no script behavior changes. | Active v1 |
| docs/aed_lifecycle_state_registry.md | LifecycleStateRegistryLayer | Lifecycle state registry v1: canonical machine-readable vocabulary for AED lifecycle states (schemas/aed_lifecycle_states_v1.json) with per-state evidence, transitions, mutations, and authorization. Companion to the operator path and command cookbook. CLI at scripts/local/aed_lifecycle_states.py is stdlib-only and runs --list, --state <NAME>, --validate, --all, --json. Reporting vocabulary; no script behavior changes. | Active v1 |
| docs/post_governance_implementation_roadmap.md | RoadmapLayer | Locks the post-governance pivot toward enforcement, schema-backed artifacts, and trial accounting. | Active |
| docs/domain_neutral_aed_architecture.md | ArchitectureLayer | Defines AED core as domain-neutral: generalized abstractions, domain modules, agent tooling, and stop rules. | Active |
| docs/domain_neutral_modularity_audit.md | ArchitectureLayer | Audit of existing codebase for pre-earnings/event/options coupling. Identifies governance layer as clean; engine/ as expected domain coupling. | Active |
| docs/experiment_spec_v1_design.md | ArchitectureLayer | Domain-neutral experiment declaration schema: entry/exit rule abstractions, study types, trial generation modes, prohibited modes, stop rules, agent tooling constraints. | Active v1 design |
| docs/outcome_spec_v1_design.md | ArchitectureLayer | OutcomeSpec v1: outcome metric declaration, outcome_window, labeling_scheme, return_basis, benchmark_policy, observation_count_policy, evidence_role_requirements, purge_embargo_policy, computed-assessment field restrictions. | Active v1 design |
| docs/instrument_universe_spec_v1_design.md | ArchitectureLayer | InstrumentUniverseSpec v1: domain-neutral instrument eligibility universe, inclusion/exclusion rules, liquidity policy, survivorship policy, multi-asset-class support via domain_profile_refs. | Active v1 design |
| docs/event_study_spec_v1_design.md | ArchitectureLayer | EventStudySpec v1: domain-neutral event-alignment contract, event families, window structures, timing controls, leakage policies, event source priority, collision/dedup rules. | Active v1 design |
| docs/options_event_risk_spec_v1_design.md | ArchitectureLayer | OptionsEventRiskSpec v1: domain-specific options event-risk specialization of EventStudySpec, contract selection, liquidity/pricing policies, gap exposure, domain-neutral pre-earnings profile hook, boundary with EventStudySpec and PreEarningsProfile. | Active v1 design |
| docs/preearnings_profile_v1_design.md | ArchitectureLayer | PreEarningsProfile v1: domain-specific pre-earnings research module, BMO/AMC session semantics, DPE targeting, earnings-specific gap exposure rules, IV crush policy, domain-neutral boundary with EventStudySpec and OptionsEventRiskSpec. | Active v1 complete |
| docs/first_thin_real_data_runner_slice_design.md | ArchitectureLayer | First thin real-data runner slice v1 design: minimal vertical cut connecting governance artifacts to real runner outputs, read-only, non-trading, no autonomous search, no optimization, dry-run and smoke_real_data modes, RunnerOutput contract proposal, audit checks, stop rules, design tensions. | Design v1 complete |
| docs/runner_output_spec_v1_design.md | ArchitectureLayer | RunnerOutputSpec v1 design: domain-neutral RunnerOutput evidence artifact contract, required and optional fields, enums (RunMode, RunnerStatus, RunnerType, OutputRole, FailureType, AuditResult), InputArtifactRef and OutputManifest structures, audit summary, failure behavior, counts reconciliation, leakage checks boundary, boundary exclusions (PBO/DSR/Sharpe/promotion/alpha claims), domain-neutral extensibility, security and data safety. | Design v1 complete |
| docs/runner_trial_accounting_linkage.md | ArchitectureLayer | RunnerOutput trial-accounting linkage design: required identity linkage fields (trial_id, search_space_id, experiment_id, variant_id, model_assessment_id, review_packet_id), dry-run vs. real-execution rules, search pressure fields (n_tried, candidate_variant_count, sample_to_trial_ratio), complexity fields (rule_count, parameter_count, complexity_bucket), ReviewPacket acceptance gate, autonomous-search lock until DSR/PBO/CPCV support exists, artifact map to TrialLedger/SearchSpaceManifest/ModelAssessmentSpec/ReviewPacket, failure mode mitigations (unreported trials, selection bias, backtest overfitting, HARKing, complexity haircuts). | Design v1 |
| docs/evidence_tiers_and_claim_levels.md | ArchitectureLayer | Evidence tier and claim-level design: Tier 0–5 definitions (captured, exploratory, candidate, robust_candidate, review_ready, deployable), gate requirements by claim level, required result preservation (raw through final_review), downgrade vs. rejection semantics, interaction with trial accounting (PRs #184, #185), cost-model policy, autonomous-search lock, implementation status. | Design v1 |
| docs/aed_tasker_executor_design.md | AgentWorkflowLayer | Design-only architecture for AED Tasker, Executor, Specifier, Builder, PR Gate Controller, Reviewer, Human merge authorization, and machine-readable Kanban packet handoffs. No runtime behavior, schemas, or tests. | Design v1 |
| docs/literature_requirements_for_aed.md | RequirementsLayer | Requirements extraction from Bailey/Borwein/López de Prado/Zhu PBO, López de Prado AFML, Montgomery DOE, Ilmanen Expected Returns, Efron & Hastie CASI. Maps literature ideas to AED artifact implications for OutcomeSpec, InstrumentUniverseSpec, EventStudySpec, OptionsEventRiskSpec, ModelAssessmentSpec extensions, and ReviewPacket design. | Active requirements baseline |
| docs/trial_ledger_v1_design.md | EnforcementLayer | Defines TrialLedger v1: append-only trial record, identity fields, source lanes, promotion rules, and governance states. | Active v1 design |
| docs/search_space_manifest_v1_design.md | EnforcementLayer | Defines SearchSpaceManifest v1: pre-declared search boundaries, budget, constraints, forbidden modes, and burden accounting. | Active v1 design |
| docs/trial_ledger_search_space_manifest_v1.md | EnforcementLayer | **Historical combined design note (PR #39).** For v1 authoritative references, use `docs/trial_ledger_v1_design.md` and `docs/search_space_manifest_v1_design.md`. | Historical |

## Local tooling map

| Script | Purpose | Mutation behavior |
|---|---|---|
| scripts/local/pr_readiness_report.py | Produces local branch, diff, changed-file, untracked-file, recent-commit, and optional PR metadata reports. | Read-only |
| scripts/local/classify_pr_gate_state.py | Classifies a GitHub PR's read-only gate state into a structured JSON packet using scope, CI, and current-head Codex evidence. | Read-only |
| scripts/local/validate_edge_hypothesis_card.py | Validates required content and guardrails in the edge hypothesis card doc. | Read-only |
| scripts/local/validate_search_space_manifest.py | Validates a single SearchSpaceManifest v1 JSON entry against the schema and governance rules. | Read-only |
| scripts/local/validate_trial_ledger.py | Validates a single TrialLedger v1 JSON entry against the schema and governance rules. | Read-only |
| scripts/local/validate_event_options_contract.py | Validates event and options observation CSV against the Event/Options contract spec. | Read-only |
| scripts/ci/validate_event_options_contract.sh | CI helper wrapper that runs the Event/Options validator across all fixture profiles and pytest. | CI helper |
| scripts/ci/validate_governance_manifests.sh | CI helper that runs TrialLedger, SearchSpaceManifest, ModelAssessmentSpec, EdgeHypothesisRegistry, ExperimentSpec, OutcomeSpec, InstrumentUniverseSpec, EventStudySpec, OptionsEventRiskSpec, and PreEarningsProfile validators and their pytest suites (918 governance tests total). | CI helper |
| scripts/local/evaluate_ledger_entry.py | Evaluates one manual ledger entry for review-only labels and rationale. | Read-only output |
| scripts/local/make_run_review_packet.py | Builds a manual review packet from ledger/run artifacts. | Writes only requested packet output |
| scripts/local/_ledger_review_shared.py | Shared helper logic for ledger review tooling. | Helper module |
| scripts/local/_smoke_shared.py | Shared helper logic for local smoke workflow scripts. | Helper module |
| scripts/local/smoke_preearn_lifecycle.py | Local smoke workflow for pre-earnings lifecycle artifacts. | Local smoke only |
| scripts/local/smoke_preearn_bridge.py | Local bridge smoke helper for pre-earnings integration. | Local smoke only |
| scripts/local/aed_tasker_packet.py | Validates ROADMAP_PACKET.json v1 and renders AED_ROADMAP_TASKER_MEMO.md. Read-only; no LLM calls, no GitHub mutations, no Kanban ops. | Read-only |
| scripts/local/aed_tasker_collect_context.py | Collects structured internal repo context (HEAD, branch, docs/scripts/tests/schemas presence, recent commits) for a future Tasker agent. No LLM calls, no network, no Kanban ops. | Read-only |
| scripts/local/aed_tasker_prompt_bundle.py | Takes AED_TASKER_CONTEXT.json, produces AED_TASKER_PROMPT.md + AED_TASKER_RUN_CONFIG.json with stop rules, model routing, research instructions, and candidate PR output requirements. No LLM calls, no network, no Kanban ops. | Read-only |
| scripts/local/aed_executor_packet.py | Validates EXECUTOR_PACKET.json v1, renders AED_EXECUTION_PLAN.md, and generates draft executor packet from ROADMAP_PACKET.json via from-roadmap CLI. No LLM calls, no GitHub mutations, no Kanban ops. | Read-only |
| scripts/local/pr_gate_task_draft.py | Reads PR gate classifier packet and optional EXECUTOR_PACKET.json, produces PR_GATE_TASK_DRAFT.json and markdown. No LLM calls, no GitHub API calls, no Kanban ops. | Read-only |
| scripts/local/pr_gate_kanban_task_create.py | Consumes PR_GATE_TASK_DRAFT.json, produces aed.pr_gate.kanban_create_plan.v1. Dry-run by default (no hermes kanban calls). --apply mode calls hermes kanban create once with idempotency-key duplicate check. No auto-dispatch, no auto-merge, no Codex calls. | Read-only |
| scripts/local/build_merge_ready_packet.py | Produces MERGE_READY_PACKET.json/md from PR gate data. No LLM calls, no GitHub mutations, no auto-merge. | Read-only |
| scripts/local/check_merge_authorization.py | Verifies MERGE_READY_PACKET and exact human phrase before merge. Exits 0 (authorized) or 1 (denied). Does not call gh pr merge. | Read-only |
| scripts/local/check_pr_scope.py | Mechanical PR scope diff enforcement: compares changed_files against allowed_files and forbidden_files. Glob patterns supported. Packet: aed.pr_gate.scope_check.v1. Exit 0=clean, 1=violation, 2=bad args. No git, no network, no mutation. | Read-only |
| scripts/local/validate_ci_workflow_invariants.py | Validates GitHub Actions CI workflow trigger invariants: pull_request/push branches, no paths filters, required jobs, concurrency block (group with github.workflow, cancel-in-progress protecting main). Packet: aed.ci.workflow_invariants.v1. Exit 0=pass, 1=fail, 2=parse error. No network, no mutation. | Read-only |
| scripts/local/run_pr_gate_watchdog_once.py | INI-config-aware wrapper for watch_pr_gate_state.py. Supports summary/compact/json output modes. | Read-only |
| scripts/local/pr_gate_controller.py | End-to-end PR gate orchestrator: classify → task draft → kanban plan. Dry-run by default; optional --apply-create-task. Consumes PR data, produces CONTROLLER_RUN_PACKET.json. No hermes kanban, no merge, no dispatch. | Read-only |
| scripts/local/pr_gate_merge_ready_notify.py | Consumes CONTROLLER_RUN_PACKET.json or direct CLI parameters, produces Telegram-ready MERGE_READY_NOTIFICATION.json/md with authorization phrase and merge command. Two input modes. Does NOT send Telegram. | Read-only |
| scripts/local/pr_gate_controller_live_smoke.py | Read-only smoke harness verifying full controller chain (classifier → task draft → kanban plan → merge-ready notification) via 4 synthetic scenarios. Never dispatches, merges, or calls Codex. Prepares future auto-dispatch wiring. | Read-only |

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
- OptionsEventRiskSpec v1 **complete** (PRs #119–#128): design, schema, fixtures, local validator, tests, CI wired
- PreEarningsProfile v1 **complete** (PRs #130–#137): design, schema, fixtures, local validator, tests, CI wired
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
