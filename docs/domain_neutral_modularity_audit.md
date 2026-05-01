# Domain-Neutral Modularity Audit

**Audit date:** 2026-05-01
**main HEAD:** `9a488ab5` — `docs: add domain-neutral AED architecture design note (#76)`
**Goal:** Identify pre-earnings-specific, event-specific, or options-specific coupling before designing ExperimentSpec v1

---

## 1. Executive Summary

**Is AED currently mostly domain-neutral?**

The governance layer (schemas, validators, fixtures, CI helpers) is **mostly domain-neutral**. The governance schemas (TrialLedger, SearchSpaceManifest, ModelAssessmentSpec, EdgeHypothesisRegistry) use generalized abstractions (`decision_timestamp`, `feature_cutoff`, `source_lane`) that are not pre-earnings-specific.

**Are any current files hard-coded to pre-earnings?**

Yes. The `engine/` directory contains the pre-earnings-specific backtest orchestration code. This is the primary source of domain coupling. The `hypotheses/generate.py`, `hypotheses/batch.py`, `hypotheses/lifecycle.py`, `adapters/preearn_options.py`, `examples.py`, and `data_manifest.py` are tightly coupled to `preearn_options` strategy family, `entry_dpe`, `delta_target`, `expiry_rank`, and options SQLite databases.

**Are any current files options/event-specific?**

The `scripts/local/validate_event_options_contract.py` and its fixtures/tests are options-specific but documented as domain-specific (Event/Options contract spec and validator design docs). This is **acceptable** — the domain-specific Event/Options contract validator is intentionally scoped to pre-earnings options research.

**Conclusion:** The governance layer is clean. The coupling risk is in `engine/` (pre-earnings backtest orchestration) and in some pre-earnings-specific smoke/hypothesis example docs. No refactoring is needed in this audit — the coupling is documented and contained.

---

## 2. File Inventory

### Governance Schemas (Domain-Neutral — No Coupling Risk)

| File | Classification | Notes |
|------|--------------|-------|
| `schemas/edge_hypothesis_registry_v1.schema.json` | Core governance | Domain-neutral. No pre-earnings fields. |
| `schemas/trial_ledger_v1.schema.json` | Core governance | Domain-neutral. Uses `decision_timestamp`, `source_lane`. |
| `schemas/search_space_manifest_v1.schema.json` | Core governance | Domain-neutral. `search_mode` enum is governance, not domain. |
| `schemas/model_assessment_spec_v1.schema.json` | Core governance | Domain-neutral. |

### Governance Validators (Domain-Neutral — No Coupling Risk)

| File | Classification | Notes |
|------|--------------|-------|
| `scripts/local/validate_edge_hypothesis_registry.py` | Core governance | Domain-neutral. No pre-earnings references. |
| `scripts/local/validate_trial_ledger.py` | Core governance | Domain-neutral. |
| `scripts/local/validate_search_space_manifest.py` | Core governance | Domain-neutral. |
| `scripts/local/validate_model_assessment_spec.py` | Core governance | Domain-neutral. |

### Event/Options Contract Validator (Domain-Specific — Acceptable)

| File | Classification | Notes |
|------|--------------|-------|
| `scripts/local/validate_event_options_contract.py` | Domain-specific | Options event research. `event_session`, `gap_exposure`, `BMO/AMC` are intentionally domain-specific. Covered by `docs/event_options_contract_validator_design_v1.md` and `docs/event_options_contract_spec_v1.md`. |
| `fixtures/event_options_contract_v1/` | Domain-specific | Pre-earnings options fixtures. Validated as correct domain-specific content. |
| `tests/test_validate_event_options_contract.py` | Domain-specific | 18 tests for Event/Options validator. |

### CI Helpers (Domain-Neutral — No Coupling Risk)

| File | Classification | Notes |
|------|--------------|-------|
| `scripts/ci/validate_governance_manifests.sh` | Core governance | Runs TRL, SSM, MAS, EHR validators. Domain-neutral. |
| `scripts/ci/validate_event_options_contract.sh` | Domain-specific | Runs Event/Options validator. Acceptable as domain-specific CI wrapper. |

### Governance Test Suites (Domain-Neutral — No Coupling Risk)

| File | Classification | Notes |
|------|--------------|-------|
| `tests/test_validate_edge_hypothesis_registry.py` | Core governance | 27 tests. Domain-neutral. |
| `tests/test_validate_trial_ledger.py` | Core governance | 21 tests. Domain-neutral. |
| `tests/test_validate_search_space_manifest.py` | Core governance | 29 tests. Domain-neutral. |
| `tests/test_validate_model_assessment_spec.py` | Core governance | 38 tests. Domain-neutral. |

### Docs (Mixed — See Notes)

| File | Classification | Notes |
|------|--------------|-------|
| `docs/domain_neutral_aed_architecture.md` | Core governance | Architecture design note. Explicitly domain-neutral. |
| `docs/trial_ledger_v1_design.md` | Core governance | Domain-neutral. |
| `docs/search_space_manifest_v1_design.md` | Core governance | Domain-neutral. |
| `docs/model_assessment_spec_v1.md` | Core governance | Domain-neutral. |
| `docs/edge_hypothesis_registry_v1.md` | Core governance | Domain-neutral. |
| `docs/edge_hypothesis_registry_jsonl_yaml_v1.md` | Core governance | Domain-neutral. |
| `docs/event_options_contract_spec_v1.md` | Domain-specific | Pre-earnings options spec. Explicitly scoped. |
| `docs/event_options_contract_validator_design_v1.md` | Domain-specific | Pre-earnings options validator design. |
| `docs/event_study_design_protocol.md` | Domain-specific | References `event_session` (AMC/BMO). Acceptable as domain research protocol. |
| `docs/options_event_risk_protocol.md` | Domain-specific | Options event risk protocol. References `delta_target`, `event_session`. |
| `docs/preearn_bridge_smoke.md` | Domain-specific | Pre-earnings bridge smoke. Potentially stale. |
| `docs/preearn_hypothesis_examples.md` | Domain-specific | Pre-earnings hypothesis examples. Potentially stale. |
| `docs/preearn_lifecycle_smoke.md` | Domain-specific | Pre-earnings lifecycle smoke. Potentially stale. |

### Engine/ (HIGH COUPLING — Pre-Earnings Backtest Orchestration)

| File | Classification | Notes |
|------|--------------|-------|
| `engine/edge_discovery/adapters/preearn_options.py` | Pre-earnings backtest adapter | **High coupling.** Hard-coded to pre-earnings options: `entry_dpe`, `delta_target`, `expiry_rank`, `options_db_path`, `earnings_event_id`. References `preearn_repo_path`. |
| `engine/edge_discovery/hypotheses/generate.py` | Pre-earnings candidate generation | **High coupling.** Only supports `StrategyFamily.preearn_options`. Generates Cartesian product of `entry_dpe × delta_target × expiry_rank`. |
| `engine/edge_discovery/hypotheses/batch.py` | Pre-earnings batch processing | **High coupling.** Depends on `preearn_options` adapter. `options_db_path` required. |
| `engine/edge_discovery/hypotheses/lifecycle.py` | Pre-earnings lifecycle | **High coupling.** `options_db_path` and `preearn_repo_path` required. |
| `engine/edge_discovery/hypotheses/spec.py` | Hypothesis spec | **Medium coupling.** `StrategyFamily.preearn_options` hard-coded. `equity_options`, `index_options` also present but unused. |
| `engine/edge_discovery/examples.py` | Pre-earnings example loader | **High coupling.** `preearn_example_path`, `load_preearn_example`, `list_preearn_examples`. All pre-earnings-specific. |
| `engine/edge_discovery/data_manifest.py` | Data manifest | **Low coupling.** `options_backtest_db` enum value. `options_2021_lane_0` example. Acceptable as optional field. |
| `engine/edge_discovery/auditor.py` | Auditor | `deflated_sharpe_with_options` function name. Acceptable — "options" here means financial options, not coupling to pre-earnings. |
| `engine/edge_discovery/runner.py` | Runner | Generic. `audit_config` with pbo_threshold/sharpe_min. No domain coupling. |
| `engine/edge_discovery/pbo.py` | PBO calculation | Generic. No domain coupling. |
| `engine/edge_discovery/costs.py` | Cost models | Generic. No domain coupling. |
| `engine/edge_discovery/features.py` | Feature extraction | Generic column names. No domain coupling. |
| `engine/edge_discovery/calibrate_costs.py` | Cost calibration | Generic OLS/bootstrap. "optional" used in Python sense, not domain. No coupling. |

---

## 3. Coupling Risk Table

| File | Term/Pattern Found | Why It Matters | Severity | Recommended Action |
|------|-------------------|----------------|----------|-------------------|
| `engine/edge_discovery/adapters/preearn_options.py` | `preearn_options`, `entry_dpe`, `delta_target`, `expiry_rank`, `earnings_event_id`, `options_db_path`, `preearn_repo_path` | Hard-codes entire pre-earnings backtest orchestration. No abstraction layer. If AED adds a new domain, this file cannot be reused. | **High** | Keep as-is. Separate `engine/` domain adapter. Do not modify governance layer. |
| `engine/edge_discovery/hypotheses/generate.py` | `StrategyFamily.preearn_options`, `entry_dpe`, `delta_target`, `expiry_rank`, Cartesian product grid | Pre-earnings candidate generation is hard-coded. Cannot generate candidates for, e.g., SeasonalityProfile without rewrite. | **High** | Accept as pre-earnings-specific. Future ExperimentSpec should decouple `entry_rule` and `exit_rule` from DPE/delta/rank. |
| `engine/edge_discovery/hypotheses/batch.py` | `preearn_options` adapter, `options_db_path` | Pre-earnings batch is hard-coded. Cannot batch-process SeasonalityProfile trials. | **High** | Accept as pre-earnings-specific. Batch logic needs abstraction for multi-domain use. |
| `engine/edge_discovery/hypotheses/lifecycle.py` | `options_db_path`, `preearn_repo_path` | Pre-earnings lifecycle is hard-coded. | **High** | Accept as pre-earnings-specific. Lifecycle should eventually dispatch by `strategy_family`. |
| `engine/edge_discovery/hypotheses/spec.py` | `StrategyFamily.preearn_options`, `equity_options`, `index_options` | Strategy family enum mixes domain-specific names. `preearn_options` should move to a domain profile. | **Medium** | When adding ExperimentSpec, make `strategy_family` reference a domain profile URI rather than a hard-coded enum. |
| `engine/edge_discovery/examples.py` | `preearn_example_path`, `load_preearn_example`, `preearn_hypotheses/` | All examples are pre-earnings. No domain-neutral example loader. | **Medium** | Keep as-is for now. Add domain-neutral example loader when other domain profiles have examples. |
| `engine/edge_discovery/data_manifest.py` | `options_backtest_db` | `options_backtest_db` is domain-specific but exists as an optional type in a generic data manifest schema. | **Low** | Acceptable. When InstrumentUniverseSpec is designed, clarify that `options_backtest_db` is a PreEarningsProfile-specific data source. |
| `docs/preearn_bridge_smoke.md` | Pre-earnings bridge smoke | Appears to be stale local smoke documentation. | **Low** | Review whether this doc should be removed or updated. Not in scope for this audit. |
| `docs/preearn_hypothesis_examples.md` | Pre-earnings hypothesis examples | Appears to be stale local smoke documentation. | **Low** | Review whether this doc should be removed or updated. Not in scope for this audit. |
| `docs/preearn_lifecycle_smoke.md` | Pre-earnings lifecycle smoke | Appears to be stale local smoke documentation. | **Low** | Review whether this doc should be removed or updated. Not in scope for this audit. |
| `scripts/local/validate_event_options_contract.py` | `event_session`, `gap_exposure`, `BMO/AMC` | Intentionally domain-specific. Validates pre-earnings options contracts. Not a coupling risk — this IS the domain-specific validator. | **None** | Keep as domain-specific validator. Covered by its own design doc. |

---

## 4. Current Architecture Assessment

### 4a. Governance Schemas

**Status: Clean and domain-neutral.**

All four governance schemas (TrialLedger, SearchSpaceManifest, ModelAssessmentSpec, EdgeHypothesisRegistry) use generalized abstractions:

- `decision_timestamp` (not `earnings_announcement_time`)
- `feature_cutoff` (not `pre_event_cutoff`)
- `source_lane` (not `preearn_lane`)
- `search_mode` enum (governance-constrained, not domain-specific)

No governance schema contains `earnings_date`, `entry_dpe`, `delta_target`, `gap_exposure`, or `event_session`.

### 4b. Validators

**Status: Clean and domain-neutral for governance validators.**

The four governance validators (`validate_trial_ledger.py`, `validate_search_space_manifest.py`, `validate_model_assessment_spec.py`, `validate_edge_hypothesis_registry.py`) are fully domain-neutral. They check governance rules, not domain rules.

The Event/Options contract validator (`validate_event_options_contract.py`) is intentionally domain-specific. It validates the pre-earnings options event research contract. This is correct and documented — the validator's design doc explicitly states it is for the Event/Options contract.

**No validator couples pre-earnings concepts to governance schemas.**

### 4c. Fixtures

**Status: Clean. Governance fixtures are domain-neutral.**

- `fixtures/trial_ledger_v1/`: Domain-neutral. Uses `TRL-YYYY-NNNN` IDs, generic source lanes.
- `fixtures/search_space_manifest_v1/`: Domain-neutral. No pre-earnings fields.
- `fixtures/model_assessment_spec_v1/`: Domain-neutral. No pre-earnings fields.
- `fixtures/edge_hypothesis_registry_v1/`: Domain-neutral. `HYP-YYYY-NNNN` IDs, generic lifecycle events.

`fixtures/event_options_contract_v1/` is pre-earnings-specific but correctly scoped as the domain-specific fixture directory for the Event/Options validator.

### 4d. CI Helpers

**Status: Clean and domain-neutral.**

`scripts/ci/validate_governance_manifests.sh` runs all four governance validators and their pytest suites. No domain-specific coupling. `scripts/ci/validate_event_options_contract.sh` is the domain-specific CI wrapper — correct and documented.

### 4e. Docs

**Status: Mixed. Governance docs are domain-neutral. Domain-specific docs are clearly marked.**

Governance docs (`trial_ledger_v1_design.md`, `search_space_manifest_v1_design.md`, `model_assessment_spec_v1.md`, `edge_hypothesis_registry_v1.md`, `edge_hypothesis_registry_jsonl_yaml_v1.md`, `domain_neutral_aed_architecture.md`) are domain-neutral.

Domain-specific docs (`event_options_contract_spec_v1.md`, `event_options_contract_validator_design_v1.md`, `event_study_design_protocol.md`, `options_event_risk_protocol.md`) are clearly scoped to pre-earnings/options research.

Three docs (`preearn_bridge_smoke.md`, `preearn_hypothesis_examples.md`, `preearn_lifecycle_smoke.md`) appear to be stale local smoke/hypothesis documentation — low priority cleanup.

### 4f. Engine / Example Code

**Status: Pre-earnings coupling is concentrated here. This is expected and acceptable.**

The `engine/` directory contains the pre-earnings backtest orchestration. This is the domain-specific implementation of AED for the pre-earnings use case. The coupling is:

1. **Expected** — AED was built to support pre-earnings options research first. The engine IS the pre-earnings backtester.
2. **Contained** — The governance layer (schemas, validators, fixtures, CI) is clean. The coupling is isolated to `engine/`.
3. **Not a bug** — The architecture doc explicitly states that PreEarningsProfile is a domain module that builds on AED core. The engine IS that module's implementation.

The risk is not that the engine is coupled — it MUST be coupled to do its job. The risk is that a future developer might accidentally extend pre-earnings coupling into the governance layer. This audit confirms **the governance layer is clean** and that risk is not present.

---

## 5. Design Implications for ExperimentSpec

### 5a. What ExperimentSpec Must Avoid

ExperimentSpec must **not** contain any of the following fields directly:
- `earnings_date` — belongs in PreEarningsProfile
- `entry_dpe` — belongs in PreEarningsProfile or domain profile
- `delta_target` — belongs in OptionsEventRiskProfile
- `expiry_rank` — belongs in PreEarningsProfile
- `iv_crush` — belongs in PreEarningsProfile
- `gap_exposure` — belongs in PreEarningsProfile or OptionsEventRiskProfile
- `event_session` (BMO/AMC/INTRA) — belongs in PreEarningsProfile

ExperimentSpec must use **generalized abstractions**:
- `entry_rule` — describes when to enter (not `entry_dpe`)
- `exit_rule` — describes when to exit (not `exit_dpe`)
- `feature_cutoff` — describes the data cutoff timestamp
- `outcome_window` — describes the post-entry observation window
- `instrument_universe` — describes what instruments are in scope
- `risk_profile` — describes acceptable risk characteristics

### 5b. Which Abstractions Need to Be Generic

| Abstraction | Must Support | PreEarningsProfile Instantiation |
|-------------|-------------|--------------------------------|
| `entry_rule` | Any entry condition | `{dpe: 2, delta_target: 0.5, expiry_rank: 0}` |
| `exit_rule` | Any exit condition | `{dpe: 30, iv_collapse_threshold: 0.3}` |
| `feature_cutoff` | Any data cutoff | `decision_timestamp - 1d` |
| `outcome_window` | Any observation window | `{start: 0, end: 30, unit: "DPE"}` |
| `instrument_universe` | Any instrument set | `{type: "equity_options", ticker: "AAPL"}` |
| `risk_profile` | Any risk constraints | `{gap_risk: true, delta_exposure: 0.5}` |

### 5c. Which Domain-Specific Details Should Move into Profiles

When ExperimentSpec is designed, the following should be **domain profile parameters**, not core AED fields:

- **PreEarningsProfile parameters**: `earnings_date`, `event_session` (AMC/BMO), `entry_dpe`, `exit_dpe`, `delta_target`, `expiry_rank`, `iv_crush`, `gap_exposure`
- **SeasonalityProfile parameters**: `seasonality_pattern`, `roll_dates`, `expiry_calendar`
- **OptionsEventRiskProfile parameters**: `delta_target`, `iv_term_structure`, `risk_reversal`, `put_call_ratio`

### 5d. Whether PreEarningsProfile Should Be Separate from EventStudySpec

**Yes — they serve different purposes:**

- **EventStudySpec** is a *design protocol* for how to conduct an event study. It defines timing windows, normal-performance models, inference methods, and bias checks. It should be domain-neutral — applicable to earnings events, macroeconomic announcements, or split announcements.

- **PreEarningsProfile** is a *domain instantiation* of AED concepts for the pre-earnings options research domain. It maps `entry_rule` → `entry_dpe/delta_target/expiry_rank` and `risk_profile` → `gap_exposure/iv_crush`.

**Recommendation:** Design EventStudySpec as a domain-neutral framework. Design PreEarningsProfile as a specific instantiation of EventStudySpec for pre-earnings options. Do not mix them.

---

## 6. Recommended Next PRs

In priority order:

| PR | Title | Scope |
|----|-------|-------|
| #1 | **ExperimentSpec v1 design** | Domain-neutral experiment declaration schema. Entry/exit rules, data scope, outcome windows, feature cutoffs. No domain-specific fields. |
| #2 | **OutcomeSpec v1 design** | Primary metric, null result definition, success criteria declared before testing. Domain-neutral. |
| #3 | **InstrumentUniverseSpec v1 design** | Universe declaration with inclusion/exclusion rules. Domain-neutral. Should support equity, options, crypto, futures. |
| #4 | **EventStudySpec v1 design** | Event study design framework: timing, windows, normal-performance model, inference, bias checks. Domain-neutral. |
| #5 | **OptionsEventRiskSpec v1 design** | Options event risk profile: delta targets, risk reversals, term structure. Domain-specific (options). |
| #6 | **PreEarningsProfile v1 design** | Pre-earnings domain module: maps ExperimentSpec abstractions to `entry_dpe`, `delta_target`, `expiry_rank`, `event_session`, `gap_exposure`. |

---

## 7. Explicit Conclusion

**No code refactoring should be performed based on this audit.**

The pre-earnings coupling in `engine/` is **expected and acceptable**. AED was built to support pre-earnings options research. The engine IS the pre-earnings backtester. The governance layer is clean.

Any future refactoring to support additional domains must:
1. Be **separate from this audit PR**
2. Be **narrow in scope** — refactor one file or concern at a time
3. Be **reviewed against the boundary rule** in `docs/domain_neutral_aed_architecture.md` before merging
4. Not add domain-specific fields to governance schemas
5. Not add domain-specific coupling to validators or CI helpers

The next design work (ExperimentSpec v1) should begin with the architecture note (`docs/domain_neutral_aed_architecture.md`) as its governing document, ensuring that any new schema adheres to the domain-neutral core principles established there.
