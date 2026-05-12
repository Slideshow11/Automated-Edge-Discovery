# AED current project status

## Current state

AED main has completed the manual governance/intake layer v1 and the first thin real-data runner milestone.

The enforcement-layer design and implementation is complete. All ten governance validators (TrialLedger, SearchSpaceManifest, ModelAssessmentSpec, EdgeHypothesisRegistry, ExperimentSpec, OutcomeSpec, InstrumentUniverseSpec, EventStudySpec, OptionsEventRiskSpec, PreEarningsProfile) are implemented, tested, and CI-wired. The governance validator milestone is complete through PR #137.

The first thin real-data runner exists and emits RunnerOutput v1 success and failed_validation artifacts. All five observation table audits are implemented. Smoke coverage is present for local CSV success, governance rejection, ambiguous headers, missing-value, unsupported source kind, and schema-valid artifact paths. The RunnerOutputSpec v1 validator is CI-wired. The runner milestone is complete through PR #182.

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
- PR #119 OptionsEventRiskSpec v1 design: domain-specific options event-risk configuration, contract selection, liquidity/pricing policies, gap exposure, pre-earnings profile hook, domain-neutral boundary
- PR #120 OptionsEventRiskSpec v1 schema
- PR #121 OptionsEventRiskSpec v1 reviewer contract design alignment
- PR #122 OptionsEventRiskSpec v1 pricing_policy and strategy_structure_policy design alignment
- PR #123 OptionsEventRiskSpec v1 fixtures (1 valid, 23 invalid)
- PR #124 OptionsEventRiskSpec v1 local validator
- PR #125 OptionsEventRiskSpec v1 validator schema-alignment fixes
- PR #126 OptionsEventRiskSpec v1 validator tests
- PR #127 OptionsEventRiskSpec v1 duplicate test names audit/fix
- PR #128 OptionsEventRiskSpec v1 CI helper wiring
- PR #130 PreEarningsProfile v1 design: domain-specific pre-earnings research module, BMO/AMC session semantics, DPE targeting, earnings-specific gap exposure rules, IV crush policy, domain-neutral boundary with EventStudySpec and OptionsEventRiskSpec
- PR #131 PreEarningsProfile enum-summary doc fix
- PR #132 PreEarningsProfile v1 JSON schema
- PR #133 PreEarningsProfile v1 fixtures
- PR #134 PreEarningsProfile v1 local validator
- PR #135 PreEarningsProfile v1 validator schema-parity fix
- PR #136 PreEarningsProfile v1 validator tests
- PR #137 PreEarningsProfile v1 CI wiring: governance helper now runs PEP validator and pytest
- PR #138 docs: PreEarningsProfile v1 milestone status cleanup
- PR #139 docs: first thin real-data runner slice v1 design
- PR #140 docs: RunnerOutputSpec v1 design
- PR #142 fixtures: RunnerOutputSpec v1 JSON fixtures
- PR #159 feat: first thin real-data runner dry-run CLI skeleton
- PR #160 feat: first thin runner DataManifest resolution
- PR #161 feat: first thin runner observation-table column validation
- PR #162 feat: first thin runner canonical observation-table summary
- PR #163 feat: first thin runner observation close-return summary
- PR #164 refactor: deep-module split of first_thin_real_data_runner
- PR #168 feat: observation-table missing-value summary
- PR #169 feat(governance): add RunnerOutputSpec v1 validator
- PR #170 test: cover close-return run ID determinism
- PR #171 feat: audit duplicate observation rows
- PR #172 fix: normalize duplicate-row CSV headers
- PR #173 feat: audit observation date coverage
- PR #174 test: first thin local smoke coverage
- PR #175 test: governance rejection smoke coverage
- PR #176 test: cover experiment spec loading
- PR #177 test: cover data manifest runner helpers
- PR #178 test: cover ambiguous observation headers
- PR #179 fix: normalize close-return CSV headers
- PR #180 test: cover missing-value smoke path
- PR #181 test: cover unsupported observation source kind
- PR #182 fix: schema-valid unsupported observation failure artifacts
- PR #184 docs: design runner trial-accounting linkage
- PR #185 schema: add optional RunnerOutput trial_accounting_summary field (backward-compatible, no runtime behavior)
- PR #186 docs: define evidence tiers and claim levels (design-only companion to PRs #184, #185; no schema or runtime changes)
- PR #187 feat: emit optional RunnerOutput trial_accounting_summary in dry-run mode with conditional CLI-driven emission (no schema change, no mutation)
- PR #188 docs: design AED Tasker, Executor, and PR gate packet architecture (design-only; no runtime behavior, schemas, tests, auto-merge, or autonomous search)
- PR #189 tooling: add read-only local PR gate state classifier for scope, CI, and current-head Codex evidence packets (no comments, tasks, dispatch, patching, or merge behavior)
- PR #199 tooling: add PR gate controller — end-to-end orchestrator chaining classifier → task draft → kanban plan, dry-run by default, optional --apply-create-task
- PR #200 tooling: add PR gate merge-ready notification packet — consumes controller output, produces Telegram-ready authorization packet; does not send Telegram
- PR #201 tooling: add PR gate controller live-smoke harness — read-only smoke harness that verifies the full controller chain (classifier → task draft → kanban plan → merge-ready notification) using 4 synthetic scenarios; never dispatches, merges, or calls Codex; prepares future auto-dispatch wiring

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
- OptionsEventRiskSpec v1 **complete** (PRs #119–#128): design, schema, fixtures, local validator, tests, CI wired
- ExperimentSpec v1 **complete** (PRs #78, #79, #80, #82, #86, #87, #88, #89, #90): design, schema, fixtures, local validator, tests, CI wired
- OutcomeSpec v1 **complete** (PRs #94–#102): design, schema, fixtures, local validator, tests, CI wired
- InstrumentUniverseSpec v1 **complete** (PRs #104–#110): design, schema, fixtures, local validator, tests, CI wired
- Event/Options contract validator **complete** (PRs #50–#55): local validator, edge-case fixtures, strict_contract_profile, CI job
- TrialLedger validator **complete** (PR #58): schema, fixtures, tests, CI wired
- SearchSpaceManifest validator **complete** (PR #59): schema, fixtures, tests, CI wired
- Governance validators CI-wired **complete** (PRs #60, #64, #74, #90, #102, #117)
- EdgeHypothesisRegistry v1 **complete** (PRs #66, #68, #71, #72, #73, #74): JSON schema, fixtures, local validator, pytest, CI wired
- PreEarningsProfile v1 **complete** (PRs #130–#137): design, schema, fixtures, local validator, tests, CI wired
- RunnerOutputSpec v1 **complete** (PRs #140, #142, #169): design, fixtures, validator, CI wired
- First thin real-data runner **complete** (PRs #159–#182): dry-run CLI skeleton, DataManifest resolution, observation table audits (canonical, close-return, missing-value, duplicate-row, date-coverage), smoke coverage, schema-valid failure artifacts

## First thin runner milestone — completed through PR #182

### RunnerOutput v1 status

The first thin runner emits `RunnerOutput` v1 artifacts for two status values:

- `success` — all requested observation table audits passed or produced info-level summaries
- `failed_validation` — governance input validation failed or a blocker-level audit check failed

The `failed_validation` artifact is schema-valid and includes `contains_private_data: False` and `publishable: False` on the experiment spec output manifest entry (PR #182).

`partial_summary` is `None` in all failure artifacts. `data_manifest_refs` always satisfies `minItems >= 1` via a placeholder when no manifest is available.

### Observation table audits implemented

| Audit name | Severity | Blocker on failure | Notes |
|---|---|---|---|
| `observation_table_canonical_summary` | info | No | Column set validation against declared columns |
| `observation_table_close_return_summary` | info | No | Close-return column resolution via shared resolver |
| `observation_table_missing_value_summary` | info | No | Missing-value count per column |
| `observation_table_duplicate_row_summary` | info | No | Duplicate (date, symbol) pair detection |
| `observation_table_date_coverage_summary` | info | No | Per-symbol min/max date and symbol count |

All five audits use a shared CSV header resolver with the following policy:

- **Exact match wins** — header string equality is checked first
- **Single stripped fallback allowed** — if exactly one column name strip()s to the expected name, it is used
- **Ambiguous stripped fallback fails closed** — if two or more column names strip() to the same value, the runner raises `GovernanceRejection`

### Helpers using the shared resolver

- `close-return summary` — normalizes close-return CSV column names deterministically
- `duplicate-row summary` — normalizes duplicate-row CSV column names deterministically
- `date-coverage summary` — normalizes date-coverage CSV column names deterministically

### Smoke coverage present

- Local CSV success path (CSV + DataManifest + all declared column types)
- Governance failure path (`autonomous_search=True` triggers `GovernanceRejection`)
- Ambiguous stripped header failure path (closed on ambiguity)
- Missing-value success path (`--observation-missing-value-columns` with CSV source)
- Missing-value blocking failure path (non-CSV source_kind with missing-value request)
- Unsupported source kind failure paths (non-CSV source_kind for close-return, missing-value, canonical audits — all schema-valid)
- Schema-valid `success` and `failed_validation` `RunnerOutput` artifact paths

### Helper unit coverage present

- `ExperimentSpec` loader and ID helper (PR #176)
- `DataManifest` loader and summary helper (PR #177)
- Observation table summary helpers (PRs #168, #171, #173)
- Runner artifact helpers (`build_runner_output`, `GovernanceRejection`, `UnsupportedConfig`)

### Unsupported source kind schema status

- `close-return` unsupported format: schema-valid (PR #181)
- `missing-value` unsupported format: schema-valid (PR #180)
- `canonical` unsupported format: schema-valid (PR #182)

## Next planned PRs

- PR #191 tooling: add read-only PR gate watchdog (watch_pr_gate_state.py + tests) — watch an open PR, report CI/Codex/blocker state via compact JSON/summary, exit codes only; no GitHub mutations, no Kanban ops, no Codex requests, no merge — **complete PRs #189, #190, #191**
- PR #192 tooling: add read-only AED Tasker packet scaffold — defines ROADMAP_PACKET.json v1 schema, validates and renders memos, no autonomous Tasker agent — **complete (merged)**
- PR #193 tooling: add read-only AED Tasker input collector — collects structured internal repo context (HEAD, branch, docs/scripts/tests/schemas presence, recent commits) for future Tasker agent; no LLM calls, no network, no Kanban ops — **complete (merged)**
- PR #194 tooling: add merge authorization packet (build_merge_ready_packet.py) and guard (check_merge_authorization.py) — explicit human phrase required before merge, no auto-merge, no GitHub mutations — **merged (41d4323)**
- PR #195 tooling: add read-only AED Tasker prompt bundle — takes AED_TASKER_CONTEXT.json, produces AED_TASKER_PROMPT.md + AED_TASKER_RUN_CONFIG.json with stop rules, model routing, research instructions, and candidate PR output requirements — **merged (de83dae4)**
- PR #196 tooling: add read-only AED Executor packet scaffold — defines EXECUTOR_PACKET.json v1 schema, validates and renders execution plans, generates draft from ROADMAP_PACKET.json via from-roadmap CLI; does NOT call LLMs, does NOT dispatch Builder, does NOT create Kanban tasks — **merged (31d7c89)**
- PR #197 tooling: add PR gate task-draft generator — reads classifier packet and optional EXECUTOR_PACKET.json, produces PR_GATE_TASK_DRAFT.json/md with idempotent action mapping; does NOT call LLMs, does NOT call GitHub APIs directly, does NOT create Kanban tasks — **merged (d7b5711)**
- PR #198 tooling: add PR gate Kanban task creation dry-run — consumes PR_GATE_TASK_DRAFT.json, produces aed.pr_gate.kanban_create_plan.v1 in dry-run mode by default, --apply mode calls hermes kanban create once with idempotency-key duplicate prevention; does NOT auto-dispatch, does NOT auto-merge, does NOT call Codex, does NOT update memory — **open**
- Leakage checks stub wiring (schema field exists in RunnerOutput; not yet wired in runner)
- RunnerOutput trial-accounting linkage schema update (add experiment_id, search_space_id, trial_id, variant_id, n_tried, sample_to_trial_ratio, complexity_bucket fields)
- TrialLedger linkage extension (carry search_space_id, variant_id, complexity_bucket, all_variants_preserved)
- ReviewPacket acceptance gate (require linkage fields and complexity before marking review_ready)
- Autonomous search readiness gate (autonomous_search blocked until DSR/PBO/CPCV support exists)
- Registry write path (append-only EHR update after manual review)

Longer-horizon deferred work:

- MechanismDiscoveryReport schema
- PostHocTheoryNote schema
- PreEarningsProfile v1 as a domain-specific research module
- Real backtest execution path (beyond dry-run skeleton)

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

All ten governance validators are implemented, tested, and CI-wired:

- **TrialLedger** (PR #58): `scripts/local/validate_trial_ledger.py`, schema, 5 fixtures, 21 tests. CI: `governance-validators` job.
- **SearchSpaceManifest** (PR #59): `scripts/local/validate_search_space_manifest.py`, schema, 6 fixtures, 29 tests. CI: `governance-validators` job.
- **ModelAssessmentSpec** (PRs #63, #64): `scripts/local/validate_model_assessment_spec.py`, schema, 6 fixtures, 38 tests. CI: `governance-validators` job.
- **EdgeHypothesisRegistry** (PRs #68, #71, #72, #73, #74): `scripts/local/validate_edge_hypothesis_registry.py`, schema, 10 fixtures, 31 tests. CI: `governance-validators` job.
- **ExperimentSpec** (PRs #78, #79, #80, #82, #86, #87, #88, #89, #90): `scripts/local/validate_experiment_spec.py`, schema, 12 fixtures, 77 tests. CI: `governance-validators` job.
- **OutcomeSpec** (PRs #94–#102): `scripts/local/validate_outcome_spec.py`, schema, 21 fixtures, 111 tests. CI: `governance-validators` job.
- **InstrumentUniverseSpec** (PRs #104–#110): `scripts/local/validate_instrument_universe_spec.py`, schema, 21 fixtures, 126 tests. CI: `governance-validators` job.
- **EventStudySpec** (PRs #112–#117): `scripts/local/validate_event_study_spec.py`, schema, 22 fixtures, 106 tests. CI: `governance-validators` job.
- **OptionsEventRiskSpec** (PRs #119–#128): `scripts/local/validate_options_event_risk_spec.py`, schema, 24 fixtures, 173 tests. CI: `governance-validators` job.
- **PreEarningsProfile** (PRs #130–#137): `scripts/local/validate_preearnings_profile.py`, schema, 25 fixtures, 206 tests. CI: `governance-validators` job.

Total CI-enforced governance validator tests: **918** via `governance-validators` job. Event/Options contract validator adds **18** via `validator` job.

## Operational notes
