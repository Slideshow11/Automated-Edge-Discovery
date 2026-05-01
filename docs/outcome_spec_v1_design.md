# OutcomeSpec v1 Design

**Design date:** 2026-05-01
**PR:** #94
**Governing documents:**
- [`docs/domain_neutral_aed_architecture.md`](./domain_neutral_aed_architecture.md) — AED core domain-neutral principles, boundary rule, generalized abstractions, agent tooling, and stop rules
- [`docs/domain_neutral_modularity_audit.md`](./domain_neutral_modularity_audit.md) — modularity audit confirming governance layer is domain-neutral; engine/ is expected pre-earnings coupling

---

## 1. Purpose

OutcomeSpec v1 defines what outcome is measured, over what window, using what metric and evidence role. It is the measurement target contract referenced by ExperimentSpec.

OutcomeSpec is a **design-time declaration** of the outcome measurement target. It declares the metric, the window, labeling rules, benchmark/reference policy, purge/embargo settings, and required evidence roles. It does not contain runtime trial results, assessment outputs, or overfit corrections.

---

## 2. Relationship to AED Artifacts

### 2a. ExperimentSpec

ExperimentSpec declares an experiment plan and references an OutcomeSpec as its measurement target.

```
ExperimentSpec.outcome_spec_id → OutcomeSpec.outcome_spec_id
```

OutcomeSpec provides the measurement semantics that ExperimentSpec declares it will evaluate. ExperimentSpec does not compute outcomes — it references the outcome definition and provides the data scope within which the outcome will be measured.

### 2b. TrialLedger

TrialLedger records individual trial results against the outcome metric declared by OutcomeSpec.

```
TrialLedger.trial_id → ExperimentSpec.trial_id
TrialLedger.outcome_spec_id → OutcomeSpec.outcome_spec_id
```

TrialLedger is the trial execution log. OutcomeSpec defines what metric each trial is measured against. TrialLedger does not own outcome semantics — it records outcomes that OutcomeSpec defines.

### 2c. ModelAssessmentSpec

ModelAssessmentSpec records statistical assessment outputs computed from TrialLedger trial results against the OutcomeSpec metric.

```
ModelAssessmentSpec.outcome_spec_id → OutcomeSpec.outcome_spec_id
ModelAssessmentSpec.model_assessment_id → TrialLedger.trial_id (for evaluated trials)
```

ModelAssessmentSpec is the owner of computed assessment outputs: PBO estimates, DSR estimates, Sharpe haircuts, adjusted OOS estimates, probability of loss, false discovery rate, strategy complexity scores, factor exposure stability checks, null-model performance comparisons, and performance versus null. OutcomeSpec defines what is being measured; ModelAssessmentSpec computes what was achieved versus expectations and null benchmarks.

OutcomeSpec does not own PBO, DSR, Sharpe haircuts, adjusted_expected_oos_sharpe, probability_of_loss, false_discovery_rate_estimate, strategy_complexity_score, factor_exposure_stability_check, null_model_performance, or performance_vs_null. These belong to ModelAssessmentSpec or its extensions.

### 2d. SearchSpaceManifest

SearchSpaceManifest declares the trial generation budget and search constraints within which trials referenced by ExperimentSpec are generated.

```
ExperimentSpec.search_space_id → SearchSpaceManifest.search_space_id
```

OutcomeSpec and SearchSpaceManifest are independent sibling declarations. OutcomeSpec defines what metric will be evaluated; SearchSpaceManifest defines how many trials will be generated and what parameter space they cover. Neither depends on the other at the schema level.

### 2e. DataManifest

DataManifest declares the data scope available for the experiment. OutcomeSpec references a DataManifest (implicitly through ExperimentSpec's data scope) to define the temporal boundary of the outcome measurement window.

```
ExperimentSpec.data_manifest_id → DataManifest.data_manifest_id
OutcomeSpec.outcome_window → bounded by DataManifest.data_range
```

OutcomeSpec outcome windows must fall within the DataManifest declared data range. OutcomeSpec does not own data declarations.

### 2f. EdgeHypothesisRegistry

EdgeHypothesisRegistry holds the hypothesis being tested by ExperimentSpec. OutcomeSpec defines the measurement semantics for that hypothesis.

```
ExperimentSpec.hypothesis_id → EdgeHypothesisRegistry.hypothesis_id
```

OutcomeSpec does not advance hypothesis status. A human-authored ReviewPacket is required to advance a hypothesis. OutcomeSpec measures whether the hypothesis-produced strategy achieves its target outcome.

### 2g. Runner Outputs

Runner outputs are runtime artifacts produced by the execution engine. They compute trial results against the OutcomeSpec metric and produce intermediate artifacts (backtest equity curves, performance series, null-model comparisons) that ModelAssessmentSpec consumes.

```
RunnerOutput.outcome_spec_id → OutcomeSpec.outcome_spec_id
ModelAssessmentSpec.runner_output_refs → RunnerOutput.runner_output_id
```

OutcomeSpec defines the measurement target; runner outputs produce the raw measurements. OutcomeSpec references runner outputs via extension_hooks.runner_output_extension_refs but does not own them.

### 2h. ReviewPacket

ReviewPacket is the human-authored synthesis document that advances or rejects a hypothesis after reviewing TrialLedger results, ModelAssessmentSpec assessments, and other governance artifacts.

```
ReviewPacket.outcome_spec_id → OutcomeSpec.outcome_spec_id
ReviewPacket.decision → references ModelAssessmentSpec outputs, not OutcomeSpec directly
```

OutcomeSpec defines what was measured. ReviewPacket renders a judgment on whether the hypothesis is supported. OutcomeSpec does not make promotion decisions; it provides the measurement framework that ReviewPacket evaluates.

---

## 3. Proposed Required Fields

These fields are required for OutcomeSpec v1. They define the core measurement target. Implementation is deferred to a later schema PR.

| Field | Type | Description |
|-------|------|-------------|
| `outcome_spec_id` | string | Canonical ID, format OUT-YYYY-NNNN |
| `outcome_version` | string | Semantic version, e.g. "1.0.0" |
| `outcome_family` | string | Outcome category, e.g. "event_study", "forward_return", "option_pnl" |
| `metric_name` | string | Metric being optimized, e.g. "sharpe_ratio", "total_return", "max_drawdown" |
| `metric_direction` | enum | maximize \| minimize \| target_range |
| `outcome_window` | object | Window definition (see outcome_window object) |
| `window_start_policy` | enum | Window start rule: absolute_date, relative_event, data_start, lookback_start |
| `window_end_policy` | enum | Window end rule: absolute_date, relative_event, data_end, fixed_horizon |
| `window_role` | enum | in_sample \| validation \| out_of_sample \| pseudo_live \| live \| holdout \| stress |
| `labeling_scheme` | enum | forward_return \| event_window_return \| drawdown \| volatility \| hit_rate \| sharpe_like \| information_ratio_like \| option_pnl \| custom |
| `return_basis` | enum | simple_return \| log_return \| excess_return \| pnl \| risk_adjusted_return \| custom |
| `benchmark_policy` | enum | none \| static_benchmark \| dynamic_benchmark \| matched_universe \| factor_model \| custom |
| `observation_count_policy` | object | Minimum/maximum observation count policy for window validity |
| `evidence_role_requirements` | object | Declarations of required evidence roles |
| `purge_embargo_policy` | object | Purge and embargo gap settings |
| `created_at` | string | ISO 8601 creation timestamp |
| `reviewer` | string | Human reviewer name or alias |

**outcome_window object:**

| Field | Type | Description |
|-------|------|-------------|
| `window_start_days` | integer | Days relative to event or anchor (negative = before) |
| `window_end_days` | integer | Days relative to event or anchor |
| `window_unit` | enum | days \| observations \| periods |
| `anchor` | string | Event field name to anchor the window, e.g. "event_time_utc" |

**observation_count_policy object:**

| Field | Type | Description |
|-------|------|-------------|
| `min_observations` | integer | Minimum observations required for valid window |
| `max_observations` | integer | Maximum observations (0 = unlimited) |
| `max_overlap_fraction` | float | Maximum fraction of overlapping observations [0, 1] |

**evidence_role_requirements object:**

| Field | Type | Description |
|-------|------|-------------|
| `requires_oos` | boolean | Requires out-of-sample evidence |
| `requires_live` | boolean | Requires pseudo-live or live evidence |
| `requires_uncertainty` | boolean | Requires uncertainty/interval estimates |
| `requires_benchmark` | boolean | Requires a benchmark comparison |
| `requires_stress_period` | boolean | Requires stress period testing |
| `requires_purge_embargo` | boolean | Requires purge and embargo enforcement |
| `requires_min_observations` | boolean | Requires minimum observation count |

**purge_embargo_policy object:**

| Field | Type | Description |
|-------|------|-------------|
| `purge_gap_days` | integer | Purge gap in days, >= 0 |
| `embargo_fraction` | float | Embargo as fraction of window [0, 1] |
| `embargo_units` | enum | fraction \| days \| observations |
| `overlap_policy` | string | Description of overlap resolution |

**Base bound rationale for embargo_fraction [0, 1]:** The base constraint is [0, 1]. This means the embargo cannot exceed 100% of the measurement window, which would eliminate all usable data. Tighter caps (e.g., max 0.5 for certain profiles) may be added as future profile policy, but the schema enforces only the base bound. Tighter caps belong in domain profile validators, not the core schema.

---

## 4. Proposed Optional Fields and Extension Hooks

These fields are optional in v1. They provide reference paths to downstream artifacts and extension points for future profiles.

| Field | Type | Description |
|-------|------|-------------|
| `benchmark_ref` | string | Reference to a static benchmark artifact |
| `benchmark_inline` | object | Inline benchmark definition (name, ticker, series) |
| `event_alignment_policy` | string | How events are aligned within the window |
| `sample_start_policy` | string | Sample start rule if different from window_start_policy |
| `sample_end_policy` | string | Sample end rule if different from window_end_policy |
| `min_observation_count` | integer | Alias for observation_count_policy.min_observations |
| `uncertainty_required` | boolean | Requires uncertainty quantification |
| `model_assessment_refs` | array[string] | References to ModelAssessmentSpec assessments that evaluate this outcome |
| `runner_output_refs` | array[string] | References to runner outputs computed against this outcome |
| `trial_ledger_refs` | array[string] | References to TrialLedger trial records for this outcome |
| `review_packet_refs` | array[string] | References to ReviewPackets that evaluated this outcome |
| `extension_hooks` | object | Reserved future artifact reference hooks |
| `notes` | string | Human notes on the outcome specification |

**extension_hooks object (optional, reserved for future artifact references only. Must not contain computed statistics directly.):**

| Field | Type | Description |
|-------|------|-------------|
| `model_assessment_extension_refs` | array[string] | Future extension assessments |
| `runner_output_extension_refs` | array[string] | Future runner output artifacts |
| `review_packet_extension_refs` | array[string] | Future review packet artifacts |
| `domain_profile_refs` | array[string] | Domain profile references for domain-specific overrides |

---

## 5. Enums

### metric_direction

| Value | Description |
|-------|-------------|
| `maximize` | Higher metric values are better |
| `minimize` | Lower metric values are better |
| `target_range` | Target is a range [min, max] |

### window_role

| Value | Description |
|-------|-------------|
| `in_sample` | Training sample outcome |
| `validation` | Validation sample outcome |
| `out_of_sample` | Out-of-sample outcome |
| `pseudo_live` | Simulated live conditions |
| `live` | Live trading conditions |
| `holdout` | Holdout sample outcome |
| `stress` | Stress period outcome |

### return_basis

| Value | Description |
|-------|-------------|
| `simple_return` | Simple discrete return |
| `log_return` | Logarithmic return |
| `excess_return` | Return minus risk-free rate |
| `pnl` | Profit and loss in currency units |
| `risk_adjusted_return` | Return adjusted for risk (e.g. Sharpe) |
| `custom` | Custom return definition in domain profile |

### labeling_scheme

| Value | Description |
|-------|-------------|
| `forward_return` | Future return over a fixed horizon |
| `event_window_return` | Return over an event-defined window |
| `drawdown` | Drawdown-based labeling |
| `volatility` | Volatility-based labeling |
| `hit_rate` | Binary hit/miss labeling |
| `sharpe_like` | Risk-adjusted ratio labeling |
| `information_ratio_like` | Active return / tracking error labeling |
| `option_pnl` | Option profit and loss labeling |
| `custom` | Custom labeling in domain profile |

### benchmark_policy

| Value | Description |
|-------|-------------|
| `none` | No benchmark comparison |
| `static_benchmark` | Static benchmark (e.g. index) |
| `dynamic_benchmark` | Dynamic benchmark (e.g. rolling window) |
| `matched_universe` | Universe-matched comparison |
| `factor_model` | Factor model-based benchmark |
| `custom` | Custom benchmark policy in domain profile |

---

## 6. Evidence Role Requirements

OutcomeSpec declares required evidence roles as a contract. This allows ExperimentSpec to verify that the outcome measurement plan has sufficient evidence before trials are generated.

Each evidence role is declared as a boolean flag. An experiment plan that requires `requires_live: true` must provide live or pseudo-live evidence before ReviewPacket can render a favorable decision.

| Role | Flag | Description |
|------|------|-------------|
| Out-of-sample required | `requires_oos` | Outcome must be evaluated on OOS data |
| Live evidence required | `requires_live` | Outcome must have pseudo-live or live validation |
| Uncertainty required | `requires_uncertainty` | Outcome must report uncertainty intervals |
| Benchmark required | `requires_benchmark` | Outcome must compare against a benchmark |
| Stress period required | `requires_stress_period` | Outcome must include stress testing |
| Purge/embargo required | `requires_purge_embargo` | Outcome window must enforce purge and embargo |
| Minimum observations required | `requires_min_observations` | Outcome window must meet min observation count |

Evidence role requirements are declared at the OutcomeSpec level. ExperimentSpec inherits these requirements and includes them in its trial generation constraints.

---

## 7. Purge and Embargo Policy

The purge and embargo policy prevents look-ahead bias and information leakage between the training window and the measurement window.

### purge_gap_days

Integer >= 0. The gap in days between the end of the training window and the start of the measurement window. A purge gap of 0 means no gap; adjacent windows share a boundary.

### embargo_fraction

Float in [0, 1]. The embargo as a fraction of the measurement window. An embargo of 0.2 means 20% of the most recent measurement window data is held back from model training.

**Base bound:** [0, 1]. The base constraint enforces that the embargo cannot exceed 100% of the window (which would eliminate all data). Stricter caps (e.g., max 0.5 for conservative profiles) may be imposed as domain profile policy in a future extension. The core schema enforces only the [0, 1] bound.

### embargo_units

| Value | Description |
|-------|-------------|
| `fraction` | embargo_fraction is expressed as a fraction of the window |
| `days` | embargo expressed as an absolute number of days |
| `observations` | embargo expressed as a number of observations |

### overlap_policy

Free-text description of how window overlap is resolved when multiple outcomes share the same anchor. For example: "non-overlapping walk-forward windows" or "rolling origin with 1-day step".

---

## 8. Boundary: What OutcomeSpec Does Not Own

OutcomeSpec is the **measurement target declaration**. It does not own the outputs of measurement.

The following are explicitly **NOT** part of OutcomeSpec. They belong to ModelAssessmentSpec, TrialLedger extensions, runner outputs, or ReviewPacket:

| Excluded Field | Owner |
|---------------|-------|
| `pbo_estimate` | ModelAssessmentSpec |
| `dsr_estimate` | ModelAssessmentSpec |
| `backtest_pnl_haircut` | ModelAssessmentSpec |
| `overfit_discount_factor` | ModelAssessmentSpec |
| `adjusted_expected_oos_sharpe` | ModelAssessmentSpec |
| `probability_of_loss` | ModelAssessmentSpec |
| `false_discovery_rate_estimate` | ModelAssessmentSpec |
| `strategy_complexity_score` | ModelAssessmentSpec |
| `factor_exposure_stability_check` | ModelAssessmentSpec |
| `null_model_performance` | ModelAssessmentSpec or Runner outputs |
| `performance_vs_null` | ModelAssessmentSpec or Runner outputs |
| `selected_variant_id` | TrialLedger or ExperimentSpec |
| `n_tried` | TrialLedger or ExperimentSpec |
| `trial_family_id` | TrialLedger |
| ReviewPacket decision | ReviewPacket |

OutcomeSpec defines the **what** (what metric, what window, what evidence is required). ModelAssessmentSpec computes the **how much** (how did the strategy perform versus the target, versus null, versus benchmark). OutcomeSpec provides the contract; ModelAssessmentSpec evaluates against it.

OutcomeSpec may reference ModelAssessmentSpec outputs via `model_assessment_refs`, but it does not contain them.

---

## 9. Conceptual Examples

### 9a. Calendar Seasonality Monthly Return Outcome

```
outcome_spec_id: OUT-2026-0042
outcome_family: calendar_effect
metric_name: monthly_return
metric_direction: maximize
outcome_window:
  window_start_days: 1
  window_end_days: 21
  window_unit: observations
  anchor: calendar_month_start
window_role: out_of_sample
labeling_scheme: forward_return
return_basis: simple_return
benchmark_policy: static_benchmark
benchmark_ref: "benchmark/SPX"
evidence_role_requirements:
  requires_oos: true
  requires_benchmark: true
  requires_min_observations: true
purge_embargo_policy:
  purge_gap_days: 1
  embargo_fraction: 0.0
  embargo_units: fraction
  overlap_policy: "non-overlapping monthly windows"
```

Domain profile (separate artifact) would add: specific calendar event definitions, sector-specific benchmarks, or custom seasonality filters.

### 9b. Event Study Forward Return Outcome

```
outcome_spec_id: OUT-2026-0043
outcome_family: event_study
metric_name: cumulative_abnormal_return
metric_direction: maximize
outcome_window:
  window_start_days: -1
  window_end_days: 1
  window_unit: days
  anchor: event_time_utc
window_role: out_of_sample
labeling_scheme: event_window_return
return_basis: excess_return
benchmark_policy: matched_universe
evidence_role_requirements:
  requires_oos: true
  requires_benchmark: true
  requires_purge_embargo: true
  requires_min_observations: true
purge_embargo_policy:
  purge_gap_days: 0
  embargo_fraction: 0.1
  embargo_units: fraction
  overlap_policy: "only events separated by >= purge_gap_days"
```

Domain profile (separate artifact) would add: specific event type taxonomies, earnings date anchors, or option-implied volatility adjustments.

### 9c. Options Event Risk Option PnL Outcome

```
outcome_spec_id: OUT-2026-0044
outcome_family: options_event_risk
metric_name: option_pnl
metric_direction: maximize
outcome_window:
  window_start_days: 0
  window_end_days: 0
  window_unit: days
  anchor: event_time_utc
window_role: pseudo_live
labeling_scheme: option_pnl
return_basis: pnl
benchmark_policy: none
evidence_role_requirements:
  requires_live: true
  requires_oos: true
  requires_uncertainty: true
purge_embargo_policy:
  purge_gap_days: 0
  embargo_fraction: 0.0
  embargo_units: fraction
```

Domain profile (separate artifact) would add: specific option strategy (straddle, strangle, put), strike selection rules, or delta/vega hedge parameters.

### 9d. Crypto Regime Forward Return Outcome

```
outcome_spec_id: OUT-2026-0045
outcome_family: crypto_regime
metric_name: regime_adjusted_return
metric_direction: maximize
outcome_window:
  window_start_days: 1
  window_end_days: 24
  window_unit: periods
  anchor: regime_identification_time
window_role: out_of_sample
labeling_scheme: forward_return
return_basis: log_return
benchmark_policy: dynamic_benchmark
benchmark_inline:
  name: "BTC realized volatility regime benchmark"
  regime_classifier: "rv_quantile_20"
evidence_role_requirements:
  requires_oos: true
  requires_benchmark: true
  requires_stress_period: true
  requires_min_observations: true
purge_embargo_policy:
  purge_gap_days: 1
  embargo_fraction: 0.15
  embargo_units: fraction
```

In this example, one period represents a one-hour bar as defined by the data manifest or domain profile. OutcomeSpec core uses `periods` rather than adding an intraday-specific hours enum.

Domain profile (separate artifact) would add: specific regime definitions (high/low volatility, bull/bear), exchange-specific factors, or custody and slippage models.

---

## 10. Agent/Tooling Layer

Hermes and OpenClaw agents may assist with:

- Drafting OutcomeSpec JSON from a design doc or domain profile description
- Suggesting missing `evidence_role_requirements` fields based on the outcome family
- Validating that an OutcomeSpec is self-consistent (window bounds, embargo_fraction range)
- Checking that an OutcomeSpec's `model_assessment_refs` point to compatible ModelAssessmentSpecs
- Generating fixture examples for validator testing

Hermes and OpenClaw **may not**:

- Approve or promote a hypothesis
- Bypass or disable any validator
- Run unlocked autonomous search, Bayesian optimization, or genetic programming
- Advance hypothesis status in EdgeHypothesisRegistry
- Render a ReviewPacket decision
- Access live trading systems or production execution

---

## 11. Validation Roadmap

OutcomeSpec v1 will be validated through the standard AED validator pipeline:

1. **Design doc** (this document, PR #94) — describes the schema and field semantics
2. **Schema** (future PR) — JSON schema for OutcomeSpec v1
3. **Fixtures** (future PR) — valid and invalid JSON fixtures covering all required fields, enums, and boundary conditions
4. **Validator** (future PR) — `scripts/local/validate_outcome_spec.py` implementing the schema rules
5. **Tests** (future PR) — pytest coverage of all validator paths
6. **CI wiring** (future PR) — add to `scripts/ci/validate_governance_manifests.sh`
7. **Docs status update** (future PR) — update `docs/current_project_status.md` and `docs/README.md`

This roadmap follows the same pattern used for TrialLedger, SearchSpaceManifest, ModelAssessmentSpec, EdgeHypothesisRegistry, and ExperimentSpec.

---

## 12. Stop Rules

OutcomeSpec v1 design respects the AED stop rules:

OutcomeSpec does **not** enable, unlock, or activate any of the following without an explicit, separately designed governance extension:

- **Autonomous search** — OutcomeSpec does not trigger or authorize autonomous strategy search. `autonomous_search` remains prohibited. Manual hypothesis generation and review is required.
- **Bayesian optimization** — No Bayesian optimization of strategy parameters. `bayesian_optimization` remains prohibited. Manual grid or fixed sweep only.
- **Genetic programming** — No genetic programming of strategy structure. `genetic_programming` remains prohibited. Manual hypothesis only.
- **Automated promotion** — No automated advancement of hypotheses. Human-authored ReviewPacket required.
- **Automated registry mutation** — No automated changes to EdgeHypothesisRegistry status.
- **Live trading** — OutcomeSpec is a design-time declaration only. It does not authorize live trading.
- **Production execution** — No production system execution.
- **GCRU integration** — GCRU integration requires a separately designed governance extension. OutcomeSpec does not include GCRU-specific fields.
