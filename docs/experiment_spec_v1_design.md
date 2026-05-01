# ExperimentSpec v1 Design

**Design date:** 2026-05-01
**PR:** #78
**Governing documents:**
- [`docs/domain_neutral_aed_architecture.md`](./domain_neutral_aed_architecture.md) — AED core domain-neutral principles, boundary rule, generalized abstractions, agent tooling, and stop rules
- [`docs/domain_neutral_modularity_audit.md`](./domain_neutral_modularity_audit.md) — modularity audit confirming governance layer is domain-neutral; engine/ is expected pre-earnings coupling

---

## 1. Purpose

ExperimentSpec is the **domain-neutral parent contract** that turns an approved or proposed hypothesis into a reproducible experiment plan. It bridges the governance intake layer (EdgeHypothesisRegistry, SearchSpaceManifest, TrialLedger, ModelAssessmentSpec) to the execution layer.

ExperimentSpec declares:
- What hypothesis is being tested
- What data scope is allowed
- What entry and exit rules apply
- What outcome window is being measured
- What instruments are in scope
- What trial generation mode is used
- What lanes are permitted

ExperimentSpec is a **design-time declaration**, not a runtime execution record. It is committed to the repository before any trial data is generated.

---

## 2. Relationship to Existing Governance Artifacts

### 2a. EdgeHypothesisRegistry

The hypothesis being tested must exist in the EdgeHypothesisRegistry with status `proposed` or `approved_for_next_stage`.

```
ExperimentSpec.hypothesis_id → EdgeHypothesisRegistry.hypothesis_id
```

ExperimentSpec does not advance the hypothesis status. A human-authored ReviewPacket is required to advance a hypothesis to the next stage.

### 2b. SearchSpaceManifest

The trial generation budget and search constraints must be declared in a SearchSpaceManifest. ExperimentSpec constrains trial generation against this pre-declared search space.

```
ExperimentSpec.search_space_id → SearchSpaceManifest.search_space_id
ExperimentSpec.trial_generation_mode → bounded by SearchSpaceManifest.search_mode
ExperimentSpec.allowed_trial_lanes → constrained by SearchSpaceManifest.allowed_data_manifests
```

### 2c. TrialLedger

Each trial generated under this ExperimentSpec is recorded in the TrialLedger with a `trial_id` linked back to the `experiment_id`.

```
TrialLedger.experiment_id → ExperimentSpec.experiment_id
TrialLedger.search_space_id → ExperimentSpec.search_space_id
```

TrialLedger entries are append-only. The `source_lane` of each trial must appear in `ExperimentSpec.allowed_trial_lanes`.

### 2d. ModelAssessmentSpec

ModelAssessmentSpec declares the confirmatory assessment criteria for trials produced by this experiment. Each trial or trial batch links to a ModelAssessmentSpec.

```
ExperimentSpec.model_assessment_ref or ExperimentSpec.model_assessment_inline
```

ModelAssessmentSpec is not authored inside ExperimentSpec — it is a separate governance artifact. ExperimentSpec can reference it by ID or include it inline.

### 2e. DataManifest

DataManifest describes the data used to form the hypothesis and conduct the experiment. ExperimentSpec references one or more DataManifests to declare the data scope.

```
ExperimentSpec.data_manifest_refs → DataManifest.data_manifest_id
```

ExperimentSpec does not duplicate DataManifest fields. It declares which data scopes are in scope for this experiment.

### 2f. Future: OutcomeSpec

OutcomeSpec declares the primary metric, null result definition, and success criteria **before testing**. It is a future AED core spec.

```
ExperimentSpec.outcome_spec_ref or ExperimentSpec.outcome_spec_inline → OutcomeSpec
```

ExperimentSpec v1 may include `outcome_spec_ref` as an optional reference for forward compatibility, with full validation deferred to when OutcomeSpec is implemented.

### 2g. Future: InstrumentUniverseSpec

InstrumentUniverseSpec declares the universe of instruments and inclusion/exclusion rules. It is a future AED core spec.

```
ExperimentSpec.instrument_universe_ref or ExperimentSpec.instrument_universe_inline → InstrumentUniverseSpec
```

ExperimentSpec v1 may include `instrument_universe_ref` as an optional reference for forward compatibility, with full validation deferred to when InstrumentUniverseSpec is implemented.

### 2h. Future: ReviewPacket

Every status change in the EdgeHypothesisRegistry requires a ReviewPacket with human approval. ExperimentSpec does not create or approve ReviewPackets. It is a design-time artifact that precedes the review process.

---

## 3. Proposed Fields (ExperimentSpec v1)

### 3a. Identity Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `experiment_id` | string | Yes | Canonical ID. Format: `EXP-YYYY-NNNN`. Year from `decision_timestamp_policy.decision_point`. Sequential per year. |
| `experiment_version` | integer | Yes | Monotonically increasing version number. Start at 1. Increment on any field change after initial commit. |
| `hypothesis_id` | string | Yes | Reference to EdgeHypothesisRegistry.hypothesis_id. Format: `HYP-YYYY-NNNN`. |
| `search_space_id` | string | Yes | Reference to SearchSpaceManifest.search_space_id. Format: `SSM-YYYY-NNNN`. |
| `created_at` | ISO8601 | Yes | Timestamp of experiment declaration. |
| `reviewer` | string | Yes | Human reviewer who approved this experiment declaration. |

### 3b. Data Scope Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `data_manifest_refs` | array[string] | Yes | List of DataManifest.data_manifest_id values in scope for this experiment. At least one required. |
| `feature_cutoff_policy` | object | Yes | Policy for determining the feature/data cutoff timestamp. See Section 5. |
| `decision_timestamp_policy` | object | Yes | Policy for determining the primary decision timestamp. See Section 5. |

### 3c. Study Configuration Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `study_type` | enum | Yes | The class of study. Values: `event_study`, `calendar_seasonality`, `regime_conditioned_signal`, `cross_sectional_ranking`, `time_series_momentum`, `literature_replication`, `options_event_risk`, `custom`. |
| `experiment_family` | string | No | Human-readable family label for grouping related experiments. Free text. |
| `entry_rule_ref` | string | No | Reference URI to the entry rule definition. Abstract — resolved by domain profile. |
| `exit_rule_ref` | string | No | Reference URI to the exit rule definition. Abstract — resolved by domain profile. |
| `outcome_spec_ref` | string | No | Reference to OutcomeSpec (future). Reserved for forward compatibility. |
| `instrument_universe_ref` | string | No | Reference to InstrumentUniverseSpec (future). Reserved for forward compatibility. |
| `risk_profile_ref` | string | No | Reference URI to the risk profile. Abstract — resolved by domain profile. |

### 3d. Trial Generation Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `trial_generation_mode` | enum | Yes | How trials are generated. Values: `manual_grid`, `fixed_sweep`, `literature_replication`, `ablation`, `falsification`, `exploratory_agent_assisted`. |
| `allowed_trial_lanes` | array[string] | Yes | List of permitted `source_lane` values for TrialLedger entries generated under this experiment. Must be a subset of lanes declared in the referenced SearchSpaceManifest. |
| `prohibited_modes` | array[enum] | No | Explicitly prohibited modes. Values: `autonomous_search`, `bayesian_optimization`, `genetic_programming`. If omitted, defaults to prohibiting all three. |

---

## 4. Study Types

The `study_type` field classifies the experiment methodology. It does not determine domain specificity — the domain is determined by the domain profile referenced via `entry_rule_ref`, `exit_rule_ref`, and `risk_profile_ref`.

| study_type | Description | Example domain profile |
|------------|-------------|----------------------|
| `event_study` | Statistical study of return behavior around event timestamps | PreEarningsProfile, MacroAnnouncementProfile |
| `calendar_seasonality` | Calendar-pattern-based signal research | SeasonalityProfile |
| `regime_conditioned_signal` | Signal conditioned on detected market regime | MacroRegimeProfile |
| `cross_sectional_ranking` | Cross-sectional factor ranking | CrossSectionalEquityProfile |
| `time_series_momentum` | Time-series momentum or mean reversion | CryptoRegimeProfile, CommodityTermStructureProfile |
| `literature_replication` | Direct replication of published academic findings | LiteratureReplicationProfile |
| `options_event_risk` | Options strategy around event risk | OptionsEventRiskProfile |
| `custom` | Any study type not covered by the above |

---

## 5. Generic Policy Abstractions

ExperimentSpec uses generic policy objects instead of domain-specific fields.

### 5a. decision_timestamp_policy

```yaml
decision_timestamp_policy:
  timestamp_ref: "data_manifest.feature_cutoff_timestamp"
  description: "Decision point is the feature/data cutoff timestamp"
```

Not: `earnings_announcement_time`, `premarket_open`, `AMC`, `BMO`.

### 5b. feature_cutoff_policy

```yaml
feature_cutoff_policy:
  timestamp_ref: "data_manifest.feature_timestamp"
  offset_direction: "before"
  offset_unit: "days"
  offset_value: 1
  description: "Feature data cutoff is 1 day before the decision timestamp"
```

Not: `pre_event_data_cutoff`, `quote_closes_before_event`.

### 5c. entry_rule_ref

```yaml
entry_rule_ref: "domain_profile://preearnings/entry_rule_v1"
```

The URI is resolved by the domain profile. The PreEarningsProfile maps this to `entry_dpe`, `delta_target`, and `expiry_rank`. A SeasonalityProfile maps this to `calendar_trigger_session`.

### 5d. exit_rule_ref

```yaml
exit_rule_ref: "domain_profile://preearnings/exit_rule_v1"
```

The URI is resolved by the domain profile. The PreEarningsProfile maps this to `exit_dpe` and `iv_collapse_threshold`.

### 5e. outcome_window_ref

```yaml
outcome_window_ref: "domain_profile://preearnings/outcome_window_v1"
```

The PreEarningsProfile maps this to `{start: 0, end: 30, unit: "DPE"}`. A cross-sectional profile maps to `{start: 0, end: 20, unit: "sessions"}`.

---

## 6. Fields That Must Not Appear in ExperimentSpec Core

The following fields are domain-specific. They belong in domain profiles, not in ExperimentSpec:

| Forbidden field | Correct location |
|----------------|-----------------|
| `earnings_date` | PreEarningsProfile |
| `event_session` (BMO/AMC/INTRA/UNKNOWN) | PreEarningsProfile |
| `entry_dpe` | PreEarningsProfile or domain profile |
| `exit_dpe` | PreEarningsProfile or domain profile |
| `delta_target` | OptionsEventRiskProfile |
| `expiry_rank` | PreEarningsProfile |
| `iv_crush` | PreEarningsProfile |
| `gap_exposure` | PreEarningsProfile or OptionsEventRiskProfile |
| `iv_term_structure` | OptionsEventRiskProfile |
| `risk_reversal` | OptionsEventRiskProfile |
| `put_call_ratio` | OptionsEventRiskProfile |
| `seasonality_pattern` | SeasonalityProfile |
| `roll_dates` | SeasonalityProfile |
| `regime_indicator` | MacroRegimeProfile |
| `paper_doi` | LiteratureReplicationProfile |

---

## 7. Trial Generation Rules

### 7a. Permitted Modes

| Mode | Description |
|------|-------------|
| `manual_grid` | Human-defined parameter grid. All combinations run. |
| `fixed_sweep` | Pre-declared parameter sweep. No exploration. |
| `literature_replication` | Parameter values sourced from published academic paper. |
| `ablation` | Systematic removal of components from a baseline configuration. |
| `falsification` | Systematic variation designed to falsify the hypothesis, not confirm it. |
| `exploratory_agent_assisted` | Hermes or OpenClaw proposes candidates within the declared search space. Human reviews and approves. |

### 7b. Prohibited Modes (Default Locked)

Unless explicitly unlocked by a future governance amendment, the following modes are prohibited:

| Mode | Stop rule |
|------|-----------|
| `autonomous_search` | No automated exploration of the strategy space without pre-declared SearchSpaceManifest and human review. |
| `bayesian_optimization` | No automated hyperparameter optimization. |
| `genetic_programming` | No evolutionary algorithm-driven strategy generation. |

These are enforced by `ExperimentSpec.prohibited_modes` and validated by the ExperimentSpec validator.

### 7c. Mode Constraints

- `exploratory_agent_assisted` requires that the SearchSpaceManifest has `search_mode: exploratory` and that the agent's proposals are logged and human-approved before trial generation begins.
- No mode may produce trials that fall outside `allowed_trial_lanes`.
- No mode may modify `EdgeHypothesisRegistry` records — trial generation is read-only with respect to the registry.

---

## 8. Agent and Tooling Layer

Hermes and OpenClaw may participate in the ExperimentSpec workflow as **assistance layers**:

**Permitted agent activities:**
- Draft an ExperimentSpec from a hypothesis ID and SearchSpaceManifest ID
- Propose `entry_rule_ref`, `exit_rule_ref`, and `risk_profile_ref` URIs from a domain profile
- Validate an ExperimentSpec against the ExperimentSpec schema
- Identify missing references (e.g., DataManifest not yet created)
- Propose `falsification` or `ablation` trial configurations
- Prepare draft ReviewPacket entries

**Agent constraints:**
- Agents **can draft**. Humans **approve** and **commit**.
- Agents must not bypass governance validation.
- Agents must not approve their own proposed ExperimentSpecs.
- Agent activity is logged; all drafts are audit-trailed.
- AED stop rules apply to all agents operating within the system.

> **"Agents can suggest. Validators can block. Humans approve."** — AED Manual Review Rule

---

## 9. Validation Roadmap

ExperimentSpec v1 will be implemented in phases:

| Phase | Step | Description |
|-------|------|-------------|
| 1 | Design doc | This document. Establishes field set, abstractions, and constraints. |
| 2 | JSON schema | `schemas/experiment_spec_v1.schema.json`. Enforces required fields, ID formats, enum values, prohibited_modes defaults. |
| 3 | Fixtures | `fixtures/experiment_spec_v1/`. Valid and invalid fixtures covering all field combinations. |
| 4 | Local validator | `scripts/local/validate_experiment_spec.py`. Validates one ExperimentSpec entry against schema and governance rules. |
| 5 | Pytest coverage | `tests/test_validate_experiment_spec.py`. Tests for each required field, enum value, ID format, cross-reference, and prohibited_modes enforcement. |
| 6 | CI wiring | Add `scripts/ci/validate_experiment_spec.sh` and wire into `governance-validators` job. |
| 7 | Docs status update | Update `docs/current_project_status.md` and `docs/README.md`. |

Phases 2–7 are **separate implementation PRs**. This document covers Phase 1 only.

---

## 10. Stop Rules

ExperimentSpec v1 design incorporates all AED core stop rules:

- **No autonomous search** — `autonomous_search` is in `prohibited_modes` by default and cannot be enabled without a governance amendment.
- **No Bayesian optimization** — `bayesian_optimization` is in `prohibited_modes` by default.
- **No genetic programming** — `genetic_programming` is in `prohibited_modes` by default.
- **No automated promotion** — ExperimentSpec does not advance hypothesis status. Human ReviewPacket required.
- **No automated registry mutation** — ExperimentSpec is read-only with respect to EdgeHypothesisRegistry.
- **No live trading** — ExperimentSpec is a design-time declaration for backtest research only.
- **No production execution** — No simulated production with real market impact.
- **No GCRU integration** — ExperimentSpec declares data scope via DataManifest references, not live feed connections.

These rules apply regardless of whether they are invoked by humans, scripts, or AI agents.

---

## 11. Relationship to PreEarningsProfile

PreEarningsProfile is the **domain-specific instantiation** of ExperimentSpec for pre-earnings options research.

```
ExperimentSpec (generic)
  └── PreEarningsProfile (pre-earnings options instantiation)
        ├── entry_rule_ref → entry_dpe, delta_target, expiry_rank
        ├── exit_rule_ref  → exit_dpe, iv_collapse_threshold
        ├── risk_profile_ref → gap_exposure, iv_crush
        └── study_type: options_event_risk or event_study
```

PreEarningsProfile does not modify ExperimentSpec. It provides the domain-specific URI resolutions for ExperimentSpec's abstract references.

---

## 12. Explicit Non-Scope

This design document does not:
- Implement a JSON schema for ExperimentSpec
- Implement a validator for ExperimentSpec
- Create fixtures or tests for ExperimentSpec
- Modify any governance validator, schema, fixture, or CI helper
- Modify the EdgeHypothesisRegistry, SearchSpaceManifest, TrialLedger, ModelAssessmentSpec, or DataManifest schemas
- Design OutcomeSpec, InstrumentUniverseSpec, EventStudySpec, or PreEarningsProfile (these are separate future PRs)
- Change any code in `engine/`, `schemas/`, `scripts/`, `tests/`, or `fixtures/`
- Modify `docs/edge_hypothesis_registry.csv`
