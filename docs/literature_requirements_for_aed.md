# Literature Requirements for AED

**PR:** #81
**Purpose:** Extract concrete AED implementation requirements from quantitative finance and statistical learning literature before building OutcomeSpec, InstrumentUniverseSpec, ModelAssessmentSpec extensions, runner logic, and review packet design.

**Status:** Requirements capture only. No implementation in this PR.

---

## 1. Purpose

AED needs a structured requirements baseline drawn from peer-reviewed methodology before the next wave of schemas and validators is designed. Without this grounding, OutcomeSpec, InstrumentUniverseSpec, statistical assessment fields, and review packet logic risk missing critical constraints on:

- trial accounting completeness
- backtest overfitting defenses
- financial ML validation hygiene
- experimental design rigor
- hypothesis taxonomy precision
- uncertainty reporting

This document converts ideas from five foundational sources into concrete, artifact-specific requirements for AED schemas, validators, fixtures, and tooling. It does not summarize the literature — it operationalizes it.

---

## 2. Requirements Extraction Table

The table below maps each source to a core idea, the AED risk it addresses, the concrete requirement it generates, the AED artifact most directly affected, and any validator or schema implication.

| Source | Core Idea | AED Risk Addressed | Concrete AED Requirement | Affected Artifact | Validator / Schema Implication |
|--------|-----------|--------------------|---------------------------|-------------------|-------------------------------|
| Bailey, Borwein, López de Prado, Zhu — PBO | Backtest results can be entirely explained by overfitting rather than true signal | False discovery: AED approves a variant that has no real edge | Track `trial_family_id` linking all variants tested against the same hypothesis; require `n_tried` in ExperimentSpec; require failed variants to be preserved in TrialLedger, not deleted after selection | ExperimentSpec, TrialLedger | Schema field `trial_family_id` required; TrialLedger must preserve all variants |
| Bailey et al. — PBO | Probability of backtest overfitting (PBO) is quantifiable via combinatorial enumeration | AED lacks a defensible false-positive rate for approved variants | Compute or estimate PBO for each selected variant; if not computed, record explicit `pbo_not_applicable_reason` free-text field. **Note:** existing `ModelAssessmentSpec v1` uses `metrics.pbo`; future TrialLedger/ReviewPacket requirements use `pbo_estimate` as the canonical name unless a later MAS extension PR chooses an alias. | TrialLedger, ReviewPacket | Validator rule: selected variant must have either `pbo_estimate` (float 0–1) or `pbo_not_applicable_reason` populated |
| Bailey et al. — DSR | Deflated Sharpe Ratio corrects for multiple testing and selection bias | Sharpe ratio inflation from trial selection is undetected | Compute or estimate DSR for each selected variant; if not computed, record explicit `dsr_not_applicable_reason`. **Note:** existing `ModelAssessmentSpec v1` uses `metrics.dsr`; future TrialLedger/ReviewPacket requirements use `dsr_estimate` as the canonical name unless a later MAS extension PR chooses an alias. | TrialLedger, ReviewPacket | Validator rule: selected variant with positive outcome must have either `dsr_estimate` (float) or `dsr_not_applicable_reason` |
| López de Prado — AFML Ch. 4 | Purged cross-validation separates tuning and validation sets with a gap | Feature look-ahead: information from the validation window leaks into the training window | Declare `purge_gap_days` as an integer in OutcomeSpec or experiment config; validator enforces `purge_gap_days >= 0` | OutcomeSpec, ExperimentSpec | Schema field `purge_gap_days` (integer, min 0); embargo field must be declared |
| López de Prado — AFML Ch. 4 | Embargo prevents leakage at the boundary between train and validation windows | Microstructure leakage at feature cutoff boundary | Declare `embargo_fraction` as a float (proportion of lookback window embargoed) in OutcomeSpec | OutcomeSpec | Schema field `embargo_fraction` (float 0–1); `feature_cutoff_policy` must reference this |
| López de Prado — AFML Ch. 6 | Walk-forward analysis uses expanding or rolling windows for realistic performance estimates | In-sample bias from fixed train/test splits | Declare `walk_forward_type` as enum `{expanding, rolling}` and `n_splits` as integer in OutcomeSpec or experiment config | OutcomeSpec | Schema field `walk_forward_type`; `n_splits` (integer >= 2 for rolling, >= 1 for expanding) |
| López de Prado — AFML Ch. 7 | Feature importance must be computed on held-out data to avoid leakage | Feature engineering overfits to in-sample statistics | `feature_importance` declared in ModelAssessmentSpec must flag whether it was computed in-sample or out-of-sample | ModelAssessmentSpec | New field `feature_importance_sample` enum `{in_sample, out_of_sample, not_computed}` |
| López de Prado — AFML Ch. 8 | Labels for classification must be unambiguous; triple-barrier labeling avoids look-ahead | Ambiguous outcome labels cause contradictory training signals | OutcomeSpec must declare `labeling_scheme` and `labeling_horizon_days`; outcome window boundaries must respect `feature_cutoff_policy` | OutcomeSpec | Schema field `labeling_scheme`; validator checks outcome window starts after feature_cutoff |
| López de Prado — AFML Ch. 12 | Ensemble methods must preserve individual model diversity; correlated predictions reduce ensemble benefit | AED approves ensembles that add no diversification | `ensemble_diversity_score` (float 0–1) or `diversity_not_computed_reason` field in ModelAssessmentSpec | ModelAssessmentSpec | Schema field `ensemble_diversity_score` (float) or `diversity_not_computed_reason` |
| Montgomery — DOE Ch. 2 | Factorial designs test all factor combinations simultaneously rather than one-at-a-time | AED variants test only one change at a time, missing interaction effects | Ablation plan must be declared in ExperimentSpec: all factors changed, all factors held constant, expected interactions | ExperimentSpec | Schema field `ablation_plan` (object); `interaction_effects_expected` (boolean) |
| Montgomery — DOE Ch. 3 | Randomized allocation of treatments to experimental units prevents systematic bias | Covariate shift between trial arms is undetected | `randomization_seed` (integer or null) in ExperimentSpec or TrialLedger to make allocation reproducible | ExperimentSpec, TrialLedger | Schema field `randomization_seed` (integer or null) |
| Montgomery — DOE Ch. 5 | Blocking controls for known nuisance factors by grouping experimental units | AED does not control for calendar effects, sector rotation, or vol regime in trial allocation | `blocking_factors` array in ExperimentSpec listing factors held constant across trial arms | ExperimentSpec | Schema field `blocking_factors` (array of strings) |
| Montgomery — DOE Ch. 4 | Power analysis determines minimum sample size to detect a target effect | AED approves underpowered trials that cannot distinguish signal from noise | `required_sample_size` and `achieved_sample_size` fields in TrialLedger or ModelAssessmentSpec; validator flags `achieved_sample_size < required_sample_size` | TrialLedger, ModelAssessmentSpec | Schema fields `required_sample_size` (integer) and `achieved_sample_size` (integer) |
| Ilmanen — Expected Returns | Risk premia are categorized: carry, value, momentum, seasonality, liquidity, event, structural, behavioral | AED hypotheses lack a structured taxonomy; category ambiguity prevents systematic review | `hypothesis_category` enum in EdgeHypothesisRegistry covering: `risk_premium`, `behavioral_anomaly`, `structural_flow`, `carry`, `value`, `momentum`, `seasonality`, `event_risk`, `liquidity_microstructure`, `post_hoc_empirical` | EdgeHypothesisRegistry | Schema field `hypothesis_category` (enum, required); post_hoc_empirical triggers PBO flag |
| Ilmanen — Expected Returns | Anomaly categories have distinct risk profiles and decay characteristics | Anomaly type is not recorded; reviewers cannot apply category-appropriate benchmarks | `anomaly_decay_expected` (boolean) and `decay_half_life_days` (integer or null) in EdgeHypothesisRegistry | EdgeHypothesisRegistry | Schema fields `anomaly_decay_expected`, `decay_half_life_days` |
| Ilmanen — Expected Returns | Strategy expected return decomposes into risk premium + implementation costs + slippage | AED promotes strategies without decomposing expected return components | `expected_return_decomposition` object in ModelAssessmentSpec with fields: `risk_premium_expected`, `transaction_cost_estimate`, `slippage_estimate` | ModelAssessmentSpec | Schema field `expected_return_decomposition` (object with required sub-fields) |
| Efron & Hastie — CASI | Bootstrap provides uncertainty estimates without parametric assumptions | AED reports point estimates only; reviewers cannot assess confidence in results | `bootstrap_n_iterations` (integer) and `bootstrap_ci_level` (float, e.g. 0.95) in ModelAssessmentSpec or OutcomeSpec; if not bootstrapped, `bootstrap_not_applicable_reason` | ModelAssessmentSpec, OutcomeSpec | Schema fields `bootstrap_n_iterations`, `bootstrap_ci_level`, `bootstrap_not_applicable_reason` |
| Efron & Hastie — CASI | Post-selection inference requires accounting for model selection process | AED selects the best-performing variant without adjusting for the selection event | `selection_biased_estimate` (boolean) and `selection_adjustment_method` (string or null) in TrialLedger for selected variants | TrialLedger, ReviewPacket | Schema field `selection_biased_estimate`; ReviewPacket must include selection bias disclosure |
| Efron & Hastie — CASI | Confidence intervals communicate uncertainty more informatively than point estimates alone | AED approves strategies based on point-estimate returns; downside variance is not reported | `return_point_estimate`, `return_ci_lower`, `return_ci_upper` (all float) in ModelAssessmentSpec or OutcomeSpec | ModelAssessmentSpec, OutcomeSpec | Schema fields `return_point_estimate`, `return_ci_lower`, `return_ci_upper` |
| Efron & Hastie — CASI | Cross-validation error bars provide model generalization uncertainty | AED reports in-sample performance only | `cv_generalization_error` (float) and `cv_n_folds` (integer) in ModelAssessmentSpec | ModelAssessmentSpec | Schema fields `cv_generalization_error`, `cv_n_folds` |
| Efron & Hastie — CASI Ch. 15 | Robustness checks compare results across multiple methodological choices | AED does not require robustness verification across alternative specifications | `robustness_checks_passed` (boolean) and `robustness_methods_tried` (array of strings) in ReviewPacket | ReviewPacket, ModelAssessmentSpec | Schema field `robustness_checks_passed`; `robustness_methods_tried` list |
| Efron & Hastie — CASI | Null model comparison establishes baseline; an strategy beating the null is not evidence of a profitable strategy | AED approves strategies that beat a trivial baseline without reporting the comparison | `null_model_description` (string) and `null_model_performance` (float) in ModelAssessmentSpec; selected variant must report `performance_vs_null` (float) | ModelAssessmentSpec | Schema fields `null_model_description`, `null_model_performance`, `performance_vs_null` |

---

## 3. Backtest Overfitting Requirements

These requirements address the Probability of Backtest Overfitting (PBO) as described in Bailey et al. They are the primary defenses against false discovery in AED's trial accounting system.

### 3a. Trial Family Tracking

Every hypothesis tested across multiple variants must be assigned a `trial_family_id`. This ID links all TrialLedger entries that belong to the same hypothesis search effort. A selected variant's `trial_family_id` must reference the family it belongs to.

- **Schema field:** `trial_family_id` in TrialLedger entry (string, `^TRIALFAM-[0-9]{4}-[0-9]{4}$`)
- **Validator rule:** Every TrialLedger entry with `governance_state: selected` must have a `trial_family_id` referencing an existing family ID in the registry

### 3b. Number of Tried Variants

`n_tried` (integer) records how many distinct variants were evaluated within a trial family before a selection decision was made. This is required for PBO and DSR computation.

- **Schema field:** `n_tried` in TrialLedger or ExperimentSpec (integer >= 1)
- **Validator rule:** `n_tried >= 1`; `n_tried` must be declared before selection is recorded

### 3c. Exploratory vs. Confirmatory Separation

AED must distinguish between exploratory searches (generating hypotheses) and confirmatory trials (testing pre-registered hypotheses). These are subject to different statistical standards.

- **Schema field:** `study_phase` enum `{exploratory, confirmatory}` in ExperimentSpec
- **Validator rule:** Confirmatory trials require `hypothesis_preregistered_at` (ISO 8601 timestamp); exploratory trials require `anomaly_category_post_hoc` (boolean, true)
- **Cross-rule:** `study_phase: confirmatory` requires `blocking_factors` to be declared

### 3d. Search Space Declaration Before Results

The SearchSpaceManifest must be declared and committed before any trial runs begin. The `ssm_declaration_timestamp` in ExperimentSpec must precede the earliest `trial_start_time` in TrialLedger.

- **Schema field:** `ssm_declaration_timestamp` in ExperimentSpec (ISO 8601); `search_space_id` references SSM
- **Validator rule:** `ssm_declaration_timestamp` must exist and precede all `trial_start_time` entries for the experiment

### 3e. Selected Result Disclosure

When a variant is selected, AED must record which variant was chosen and what its unadjusted performance was. This enables reviewers to apply their own PBO corrections.

- **Schema field:** `selected_variant_id` (string), `unadjusted_return` (float), `selection_timestamp` (ISO 8601) in TrialLedger
- **Validator rule:** Selected entries must have all three fields populated

### 3f. Failed Variant Preservation

Failed or rejected variants must be preserved in TrialLedger with `governance_state: rejected` or `governance_state: abandoned`. They must not be deleted after a selection is made. This is the combinatorial basis for PBO enumeration.

- **Validator rule:** TrialLedger must retain all entries with `trial_family_id` matching the selected variant's family; entries with `governance_state: deleted` are prohibited
- **Fixture implication:** Invalid fixture `invalid_trial_ledger_delivered_after_selection.jsonl` tests this rule

### 3g. PBO or Explicit Not Applicable Reason

For each selected variant, either a PBO estimate or an explicit `pbo_not_applicable_reason` must be recorded.

- **Schema fields:** `pbo_estimate` (float 0–1) OR `pbo_not_applicable_reason` (string, min 20 chars) in TrialLedger
- **Validator rule:** Selected variants must have one and only one of `pbo_estimate` or `pbo_not_applicable_reason`
- **Legitimate not-applicable reasons:** `insufficient_sample_for_enumeration`, `non_combinatorial_search_method`, `confirmatory_trial_no_multiple_testing`

### 3h. DSR or Explicit Not Applicable Reason

For each selected variant, either a Deflated Sharpe Ratio estimate or an explicit `dsr_not_applicable_reason` must be recorded.

- **Schema fields:** `dsr_estimate` (float) OR `dsr_not_applicable_reason` (string, min 20 chars) in TrialLedger
- **Validator rule:** Selected variants with `performance_return != null` must have one of `dsr_estimate` or `dsr_not_applicable_reason`
- **Legitimate not-applicable reasons:** `non_return_based_metric`, `insufficient_sample_for_sharpe`, `regime_switching_unstable`

### 3i. Bailey, Borwein, López de Prado, Zhu — PBO Mathematical Foundations

This paper provides the mathematical framework underlying Probability of Backtest Overfitting (PBO). It demonstrates that when a strategy is selected from a large combinatorial space of variants based on backtest performance, the probability that its apparent performance is entirely due to overfitting rather than true signal is close to 1 for reasonable sample sizes. The combinatorial enumeration approach requires that all tried variants be preserved in the trial ledger for a complete PBO computation.

| Requirement | Type | AED Artifact | Rationale |
|-------------|------|---------------|-----------|
| `trial_family_id` | string | TrialLedger | PBO is computed over the combinatorial space of all variants in a trial family; all must share this ID |
| `all_variants_preserved` | boolean | TrialLedger | true = all n_tried variants are present in ledger with governance_state preserved; false = some were deleted (PBO enumeration is incomplete) |
| `n_tried` | integer >= 1 | ExperimentSpec / TrialLedger | total number of variants enumerated; required for PBO combinatorial base |
| `selected_variant_id` | string | TrialLedger | which variant was selected from the family |
| `pbo_estimate` | float [0, 1] | TrialLedger | probability of backtest overfitting for selected variant |
| `pbo_method` | enum {combinatorial_enumeration, mc_simulation, cscv, not_applicable} | TrialLedger | method used to compute pbo_estimate |
| `pbo_not_applicable_reason` | string | TrialLedger | required if pbo_estimate is absent; legitimate values: insufficient_sample, non_combinatorial_search, confirmatory_no_multiple_testing, method_not_implemented |
| `sample_length` | integer >= 2 | TrialLedger / ModelAssessmentSpec | number of in-sample periods used in PBO computation |
| `number_of_trials` | integer >= 1 | TrialLedger | total number of trials in the backtest (synonym for n_tried in combinatorial context) |
| `sample_to_trial_ratio` | float | TrialLedger | sample_length / n_tried; warning if < threshold (e.g., < 20) indicating underpowered search |
| `degrees_of_freedom_warning` | boolean | TrialLedger | true if sample_length < n_tried (negative degrees of freedom) |
| `cscv_n_bags` | integer >= 2 | TrialLedger | number of CSCV bags if method is cscv |
| `cscv_prob_s_overfit` | float [0, 1] | TrialLedger | CSCV probability of selecting an overfit model |

### 3j. Rej, Seager, Bouchaud — Overfit Discount and Haircut Method

This paper introduces the "overfit discount" or "haircut" methodology for adjusting reported backtest performance to account for selection bias and multiple testing. Rather than computing a formal PBO, it proposes applying a multiplicative haircut to the backtest PnL based on the number of trials conducted, the accepted threshold metric, and the tightness of the selection criterion. The haircut provides a practical, defensible adjustment that can be applied even when a full combinatorial PBO enumeration is infeasible.

| Requirement | Type | AED Artifact | Rationale |
|-------------|------|---------------|-----------|
| `backtest_pnl_haircut` | float [0, 1] | ModelAssessmentSpec | fraction of backtest PnL to discount to account for overfitting; 0.7 = haircut 30% |
| `overfit_discount_factor` | float [0, 1] | ModelAssessmentSpec | synonym for backtest_pnl_haircut; preference is to use backtest_pnl_haircut as canonical |
| `overfit_freedom_score` | float [0, 1] | TrialLedger | ratio of live edge to backtest edge (higher = less overfit); also called tweak_freedom_score |
| `tweak_freedom_score` | float [0, 1] | TrialLedger | alias for overfit_freedom_score |
| `accepted_threshold_metric` | enum {sharpe_ratio, sortino_ratio, calmar_ratio, total_return, information_ratio, custom} | ModelAssessmentSpec | which metric is used as the selection criterion |
| `accepted_threshold_value` | float | ModelAssessmentSpec | the cutoff value of accepted_threshold_metric |
| `original_strategy_ref` | string | ModelAssessmentSpec | reference to the strategy as originally specified before parameter tuning |
| `modified_strategy_ref` | string | ModelAssessmentSpec | reference to the strategy after parameter tuning (has same logical strategy, different parameters) |
| `strategy_correlation_to_original` | float [-1, 1] | ModelAssessmentSpec | correlation between original and modified strategy returns; low correlation indicates the tuning substantially changed behavior |
| `overfit_assumption_note` | string | ModelAssessmentSpec | free-text disclosure of what assumptions underlie the haircut estimate |
| `haircut_method` | string | ModelAssessmentSpec | description of method used to compute haircut (e.g., "Wiggle Room method", "minimum investment horizon adjustment") |
| `haircut_not_applicable_reason` | string | ModelAssessmentSpec | required if backtest_pnl_haircut is absent |

### 3k. Witzany — Bayesian Approach to Backtest Overfitting

This paper applies Bayesian inference to the problem of backtest overfitting, treating strategy selection as a model selection problem with a prior over the space of possible strategies. It computes a posterior probability that a strategy is a false discovery (i.e., has no real out-of-sample edge) by combining a prior on strategy quality with the likelihood of the observed backtest performance. The approach yields an adjusted expected out-of-sample Sharpe ratio and a posterior probability of loss that directly incorporate multiple testing corrections.

| Requirement | Type | AED Artifact | Rationale |
|-------------|------|---------------|-----------|
| `adjusted_expected_oos_sharpe` | float | ModelAssessmentSpec | Sharpe ratio adjusted for selection bias and multiple testing |
| `probability_of_loss` | float [0, 1] | ModelAssessmentSpec | Bayesian posterior probability that the strategy loses money out-of-sample |
| `expected_oos_rank` | float [0, 1] | ModelAssessmentSpec | expected rank of the strategy's OOS performance relative to a benchmark distribution |
| `false_discovery_rate_estimate` | float [0, 1] | ModelAssessmentSpec | FDR-adjusted probability that the strategy is a false discovery |
| `adjusted_p_value` | float [0, 1] | ModelAssessmentSpec | p-value adjusted for multiple testing (Bonferroni, Benjamini-Hochberg, or other) |
| `overfit_adjustment_method` | string | ModelAssessmentSpec | name of adjustment method used (e.g., "Bayesian hierarchical model", "Benjamini-Hochberg FDR", "Bonferroni") |
| `bootstrap_method` | string | ModelAssessmentSpec | bootstrap method used for uncertainty quantification (e.g., "stationary bootstrap", "block bootstrap", "pair bootstrap") |
| `stationary_bootstrap_block_parameter` | float > 0 | ModelAssessmentSpec | block length parameter for Politis-Romano stationary bootstrap; required if bootstrap_method is stationary |
| `bayesian_overfit_model` | string | ModelAssessmentSpec | name of Bayesian model used (e.g., "normal with conjugate prior", "hierarchical mixture model"); required for probability_of_loss computation |
| `oos_performance_distribution_ref` | string | ModelAssessmentSpec | artifact reference to stored OOS performance distribution (for reproducibility) |

### 3l. Suhonen, Lennkh, Perez — Backtest/Live Split Analysis

This paper analyzes the empirical relationship between backtest performance and live trading results by comparing strategy performance before and after a defined backtest/live split date. It introduces metrics such as realized Sharpe haircut (the ratio of live Sharpe to backtest Sharpe) and factor exposure stability checks to detect whether factor loadings have drifted between the backtest period and live period. The methodology provides a practical framework for assessing the credibility of backtest results based on forward performance.

| Requirement | Type | AED Artifact | Rationale |
|-------------|------|---------------|-----------|
| `backtest_live_split_date` | ISO 8601 date | TrialLedger / ModelAssessmentSpec | date separating backtest period from live/forward period |
| `backtest_period_length` | integer >= 1 | TrialLedger / ModelAssessmentSpec | number of calendar days or bars in the backtest period |
| `live_period_length` | integer >= 1 | TrialLedger / ModelAssessmentSpec | number of calendar days or bars in the live/forward period since split date |
| `live_performance_required` | boolean | TrialLedger / ModelAssessmentSpec | true = live performance must be reported; false = live period not yet available |
| `realized_sharpe_haircut` | float | ModelAssessmentSpec | ratio of realized Sharpe to backtest Sharpe (analogous to overfit_freedom_score but for Sharpe metric) |
| `strategy_complexity_score` | float >= 0 | ModelAssessmentSpec | measure of strategy complexity (number of parameters, signals, or rule count normalized) |
| `number_of_signals` | integer >= 0 | ModelAssessmentSpec | count of distinct signals used in the strategy |
| `number_of_parameters` | integer >= 0 | ModelAssessmentSpec | total count of tunable parameters in the strategy |
| `rule_count` | integer >= 0 | ModelAssessmentSpec | count of if-then rules or decision rules in the strategy |
| `factor_exposure_stability_check` | boolean | ModelAssessmentSpec | true = factor exposures were tested for stability between backtest and live periods |
| `factor_loading_backtest` | object | ModelAssessmentSpec | factor loadings computed on backtest period |
| `factor_loading_live` | object | ModelAssessmentSpec | factor loadings computed on live period |
| `factor_exposure_drift_flag` | boolean | ModelAssessmentSpec | true = factor loadings differ significantly between backtest and live; requires disclosure |

---

## 4. Financial ML Validation Requirements

These requirements implement purged cross-validation, embargo policy, walk-forward analysis, and leakage controls from López de Prado's AFML.

### 4a. Purged Validation

The purge gap between tuning and validation windows must be declared and enforced.

- **Schema field:** `purge_gap_days` (integer >= 0) in OutcomeSpec or experiment config
- **Validator rule:** If `purge_gap_days > 0`, the outcome window start must be at least `purge_gap_days` after the feature window end

### 4b. Embargo Policy

A fraction of the training lookback window is embargoed to prevent microstructure leakage at the boundary.

- **Schema field:** `embargo_fraction` (float in range [0, 1]) in OutcomeSpec; `embargo_days` (integer, computed) derived from `embargo_fraction * training_window_days`
- **Validator rule:** `embargo_fraction` must be in range [0, 1]; a stricter cap (e.g., [0, 0.5]) may be imposed by a future policy profile or validator profile but is not part of the base literature requirement

### 4c. Walk Forward Splits

Walk-forward validation must declare the split type and number of splits for reproducibility.

- **Schema field:** `walk_forward_type` enum `{expanding, rolling}` in OutcomeSpec; `n_splits` (integer >= 2 for rolling, >= 1 for expanding); `walk_forward_rebalance_policy` enum `{fixed_holdings, rebalanced}`
- **Validator rule:** `n_splits >= 2` for `walk_forward_type: rolling`; `n_splits >= 1` for `expanding`

### 4d. Leakage Controls

AED must enforce that features are computed only from data available before the `feature_cutoff_timestamp`.

- **Validator rule:** Every feature column used in a model must have its latest timestamp checked against the `feature_cutoff_policy.declaration_timestamp`; features with future timestamps are rejected
- **Schema field:** `feature_cutoff_policy` object in ExperimentSpec with `declaration_timestamp` (ISO 8601) and `enforcement_mode` enum `{strict, advisory}`

### 4e. Time-Aware Cross Validation

Cross-validation splits must respect temporal ordering; no future data may appear in a training or validation fold.

- **Validator rule:** For any `cv_n_folds > 0`, each fold's validation end date must be <= the next fold's validation start date
- **Schema field:** `cv_n_folds` (integer) and `cv_fold_definitions` (array of objects with `train_start`, `train_end`, `val_start`, `val_end`) in ModelAssessmentSpec or OutcomeSpec

### 4f. Feature Cutoff Enforcement

`feature_cutoff_policy` is declared at the experiment level and enforced at the trial level.

- **Schema field:** `feature_cutoff_policy` in ExperimentSpec containing `declaration_timestamp`, `enforcement_mode`, and optional `cutoff_sources` (array of data manifest IDs)
- **Validator rule:** All features used in a trial must have timestamps <= `declaration_timestamp`; advisory mode logs violations but does not block

### 4g. Outcome Window Separation

The outcome measurement window must be clearly separated from the feature window and from any subsequent feature updates.

- **Schema field:** `outcome_window_start` (ISO 8601), `outcome_window_end` (ISO 8601) in OutcomeSpec; `labeling_horizon_days` (integer) as alternative shorthand
- **Validator rule:** `outcome_window_start` >= `feature_cutoff_policy.declaration_timestamp` + `purge_gap_days` + `embargo_days`

### 4h. Sample Uniqueness or Overlap Warning

When the same instrument appears in multiple outcome windows or across multiple experiments, AED must flag potential sample contamination.

- **Schema field:** `sample_overlap_warning` (string or null) in OutcomeSpec or ModelAssessmentSpec; `overlapping_experiments` (array of experiment IDs) when known
- **Validator rule:** Advisory warning; if `sample_overlap_warning` is populated, ReviewPacket must address the overlap in its analysis section

---

## 5. Experimental Design Requirements

These requirements implement factorial design, blocking, randomization, and power analysis from Montgomery's DOE.

### 5a. Ablation Plans

When a variant involves multiple changes from a baseline, an ablation plan enumerates which factors changed and which were held constant.

- **Schema field:** `ablation_plan` object in ExperimentSpec with `base_variant_id`, `changed_factors` (array of factor names), `held_constant_factors` (array of factor names), `expected_interaction_effects` (boolean)
- **Validator rule:** `changed_factors` and `held_constant_factors` must be disjoint; union must cover all factors in the search space

### 5b. Factorial Grids

If the experiment uses a full or partial factorial design, the grid must be declared.

- **Schema field:** `factorial_grid_type` enum `{full, fractional, plackett_burman, none}` in ExperimentSpec; `grid_n_levels` (array of integers, one per factor); `fractional_generator` (string or null, for fractional designs)
- **Validator rule:** If `factorial_grid_type != none`, `grid_n_levels` must be declared and `n_tried` must match or be a multiple of the grid size

### 5c. One Change at a Time Controls

When applicable, AED must include OCAT (one change at a time) controls to isolate individual factor effects.

- **Schema field:** `ocat_controls_included` (boolean) in ExperimentSpec; `ocat_variant_ids` (array of variant IDs) listing which TrialLedger entries are OCAT control runs
- **Validator rule:** If `ocat_controls_included == false`, `abatement_plan.interaction_effects_expected` must be `true` (interaction effects are expected and disclosed)

### 5d. Interaction Effect Disclosure

Any experiment with multiple factors must disclose whether interaction effects were analyzed.

- **Schema field:** `interaction_effects_analyzed` (boolean) in ExperimentSpec; `interaction_effects_found` (boolean or null); `interaction_effects_description` (string or null)
- **Validator rule:** If `interaction_effects_analyzed == false`, `interaction_effects_expected` must be `false`; if `interaction_effects_found == true`, ReviewPacket must include interaction analysis

### 5e. Controlled Search Budgets

The total number of trials permitted within a trial family must be declared in advance to prevent unconstrained search.

- **Schema field:** `trial_budget` (integer >= 1) in ExperimentSpec; `trial_budget_type` enum `{hard_limit, advisory}`; `trial_budget_spent` (integer, computed from TrialLedger)
- **Validator rule:** `trial_budget_spent <= trial_budget` for `trial_budget_type: hard_limit`; warning generated for advisory budgets exceeded

### 5f. Preregistered Factors

For confirmatory trials, the factors and their levels must be declared before data collection begins.

- **Schema field:** `preregistered_factors` (array of factor objects with `name`, `levels`, `unit`) in ExperimentSpec; `preregistered_at` (ISO 8601 timestamp)
- **Validator rule:** `preregistered_factors` must be declared when `study_phase: confirmatory`; `preregistered_at` must precede first trial start time

### 5g. Factor Level Tracking

Each variant's factor levels must be fully recorded to enable replication and ablation reconstruction.

- **Schema field:** `factor_levels` object in TrialLedger entry mapping factor names to level values; `variant_fingerprint` (string, hash of factor levels) for deduplication
- **Validator rule:** `variant_fingerprint` must be unique within a `trial_family_id`; duplicate fingerprints across different `variant_id` values are blocked

---

## 6. Expected Return and Hypothesis Taxonomy Requirements

These requirements implement Ilmanen's risk premia classification framework for structured hypothesis categorization in EdgeHypothesisRegistry.

### 6a. Risk Premium

The strategy's expected return is primarily attributable to compensation for bearing systematic risk.

- **EdgeHypothesisRegistry field:** `hypothesis_category: risk_premium`; `risk_premium_type` enum `{equity_risk_premium, interest_rate_risk_premium, credit_risk_premium, foreign_exchange_risk_premium, commodity_risk_premium, inflation_risk_premium}`
- **ModelAssessmentSpec field:** `risk_premium_expected` (float, annualized); `risk_adjusted_return` (float)

### 6b. Behavioral Anomaly

The strategy exploits predictable investor behavior or cognitive biases ( Prospect Theory, disposition effect, herding).

- **EdgeHypothesisRegistry field:** `hypothesis_category: behavioral_anomaly`; `behavioral_mechanism` enum `{disposition_effect, herding, prospect_theory, anchoring, mental_accounting, overconfidence, availability_bias}`
- **Validator implication:** Behavioral anomaly hypotheses require a `mechanism_description` free-text field (min 50 chars)

### 6c. Structural Flow

The strategy exploits institutional flow dynamics, index rebalancing, or capital supply/demand imbalances.

- **EdgeHypothesisRegistry field:** `hypothesis_category: structural_flow`; `flow_trigger` enum `{index_rebalancing, options_expiry, futures_roll, ETF_creation_redemption, margin_call_cascade, central_bank_intervention}`
- **ModelAssessmentSpec field:** `structural_flow_decay_rate` (float, annualized); `structural_flow_half_life_days` (integer or null)

### 6d. Carry

The strategy earns returns from the time value of money, typically by holding high-yielding positions.

- **EdgeHypothesisRegistry field:** `hypothesis_category: carry`; `carry_type` enum `{currency_triangle, futures_curve, dividend_yield, interest_rate_differential, option_triangle}`
- **ModelAssessmentSpec field:** `carry_rate_expected` (float, annualized); `carry_rate_realized` (float or null)

### 6e. Value

The strategy is long cheap assets and short expensive assets relative to a fundamental measure.

- **EdgeHypothesisRegistry field:** `hypothesis_category: value`; `value_measure` enum `{book_to_market, earnings_yield, sales_yield, free_cash_flow_yield, dividend_yield, GDP_deflator}`
- **ModelAssessmentSpec field:** `value_spread_initial` (float); `value_spread_decay_rate` (float or null)

### 6f. Momentum

The strategy is long recent winners and short recent losers, betting on continuation.

- **EdgeHypothesisRegistry field:** `hypothesis_category: momentum`; `momentum_formation_period` enum `{intraday, daily, weekly, monthly, quarterly}`; `momentum_reversal_threshold` (float or null)
- **ModelAssessmentSpec field:** `momentum_half_life_days` (integer); `momentum_decay_function` enum `{exponential, linear, step}`

### 6g. Seasonality

The strategy exploits calendar-based patterns in returns (day-of-week, month-of-year, holiday effects, earnings cycle).

- **EdgeHypothesisRegistry field:** `hypothesis_category: seasonality`; `seasonality_type` enum `{day_of_week, month_of_year, holiday, earnings_cycle, fed_meeting_cycle, options_expiry_week}`
- **Validator implication:** Seasonality hypotheses require `seasonality_calendar` field declaring the specific calendar effect; `seasonality_confidence_level` (float 0–1) if estimated

### 6h. Event Risk

The strategy exploits price reactions to discrete macroeconomic or corporate events.

- **EdgeHypothesisRegistry field:** `hypothesis_category: event_risk`; `event_type` enum `{earnings, macroeconomic, Fed_announcement, commodity_shock, geopolitical, regulatory, natural_disaster}`
- **OutcomeSpec field:** `event_window_spec` object with `pre_event_window_days`, `post_event_window_days`, `normal_performance_estimation_window`; aligned with EventStudySpec

### 6i. Liquidity or Microstructure Effect

The strategy exploits bid-ask spread patterns, market impact costs, or liquidity premia.

- **EdgeHypothesisRegistry field:** `hypothesis_category: liquidity_microstructure`; `liquidity_effect_type` enum `{bid_ask_spread, market_impact, illiquidity_premium, informed_trader_advantage, settlement_delay}`
- **ModelAssessmentSpec field:** `implementation_shortfall_estimate` (float, bps); `market_impact_model` (string describing the model used)

### 6j. Post Hoc Empirical Anomaly

The strategy is based on a pattern observed in historical data without a prior theoretical justification.

- **EdgeHypothesisRegistry field:** `hypothesis_category: post_hoc_empirical`; `proposed_mechanism` (string or null, min 50 chars if provided); `replication_sample_start` and `replication_sample_end` required for post-hoc claims
- **Validator implication:** `post_hoc_empirical` category sets `anomaly_decay_expected: true` by default; PBO estimate is required (not optional) for this category
- **ReviewPacket implication:** Post-hoc hypotheses must include a dedicated replication analysis section

---

## 7. Statistical Inference Requirements

These requirements implement uncertainty quantification, bootstrap inference, and robustness checks from Efron and Hastie's CASI.

### 7a. Uncertainty Reporting

Every model assessment must include a quantified uncertainty estimate alongside point estimates.

- **Schema fields:** `return_point_estimate` (float), `return_ci_lower` (float), `return_ci_upper` (float), `ci_coverage_level` (float, e.g. 0.95) in ModelAssessmentSpec
- **Validator rule:** If `return_point_estimate` is present, `return_ci_lower` and `return_ci_upper` must also be present or `ci_not_applicable_reason` must be provided

### 7b. Bootstrap Support

Bootstrap resampling provides non-parametric uncertainty estimates when analytic distributions are unavailable.

- **Schema fields:** `bootstrap_n_iterations` (integer >= 100), `bootstrap_ci_level` (float 0.9–0.99), `bootstrap_ci_method` enum `{percentile, bias_corrected_accelerated, basic}` in ModelAssessmentSpec or OutcomeSpec; `bootstrap_not_applicable_reason` (string) if not bootstrapped
- **Validator rule:** If `bootstrap_n_iterations >= 100`, `bootstrap_ci_lower` and `bootstrap_ci_upper` must be declared; bootstrap results must be stored as artifacts referenced by `bootstrap_artifact_ref`

### 7c. Post-Selection Bias Disclosure

When a variant is selected from a set of candidates, the selection process introduces bias that must be disclosed.

- **Schema fields:** `selection_method` enum `{best_performer, lowest_drawdown, Sharpe_ratio, custom}` in TrialLedger; `selection_biased_estimate` (boolean, default true); `selection_adjustment_method` string describing any applied correction (e.g. "Bonferroni correction", "cross-validation selector", "none")
- **ReviewPacket implication:** ReviewPacket must contain a "Selection Bias Analysis" section for any `selection_biased_estimate == true` selected variant

### 7d. Confidence Interval or Uncertainty Band Support

Uncertainty bands must be available for any time-series performance metric.

- **Schema fields:** `equity_curve_ci_lower` (array of floats), `equity_curve_ci_upper` (array of floats), `equity_curve_ci_coverage` (float) in ModelAssessmentSpec; alternatively `performance_uncertainty_band artifact_ref`
- **Validator implication:** If walk-forward validation is declared, uncertainty bands should span the walk-forward equity curves

### 7e. Robustness and Fragility Checks

Strategies must be tested across alternative methodological choices to verify they are not fragile to specification choices.

- **Schema fields:** `robustness_checks_passed` (boolean); `robustness_methods_tried` (array of strings, e.g. `["alternative_lookback_windows", "alternative_out_of_sample", "transaction_cost_scenarios", "alternative_labeling_scheme"]`); `fragility_identified` (boolean); `fragility_description` (string or null)
- **ReviewPacket implication:** ReviewPacket must include a "Robustness Analysis" section enumerating which checks passed or failed

### 7f. Null Model or Benchmark Comparison

A strategy's performance is meaningless without a baseline comparison.

- **Schema fields:** `null_model_name` (string, e.g. "buy_and_hold_SPY", "fixed_income_benchmark", "risk_free_rate"); `null_model_performance` (float); `performance_vs_null` (float = selected_return - null_return); `null_model_source` (string describing data source for null)
- **Validator rule:** `performance_vs_null` must be computed and non-null for any selected variant with a return-based metric; null model must be independently sourced (not derived from the same backtest run)

---

## 8. Concrete Artifact Implications

This section maps the requirements above to specific AED artifacts, indicating which already exist, which need extension, and which are entirely new.

### 8a. ExperimentSpec (existing, PRs #78, #79, #80)

ExperimentSpec v1 is largely complete for required identity fields, study type, trial generation mode, prohibited modes, and canonical IDs. The following fields need to be added in a future PR:

| New Field | Type | Source Requirement |
|-----------|------|--------------------|
| `trial_family_id` | string | Backtest overfitting §3a |
| `n_tried` | integer >= 1 | Backtest overfitting §3b |
| `study_phase` | enum {exploratory, confirmatory} | Backtest overfitting §3c |
| `hypothesis_preregistered_at` | ISO 8601 | Backtest overfitting §3c |
| `ssm_declaration_timestamp` | ISO 8601 | Backtest overfitting §3d |
| `purge_gap_days` | integer >= 0 | Financial ML §4a |
| `embargo_fraction` | float [0, 1] | Financial ML §4b |
| `walk_forward_type` | enum {expanding, rolling} | Financial ML §4c |
| `n_splits` | integer >= 1 | Financial ML §4c |
| `walk_forward_rebalance_policy` | enum {fixed_holdings, rebalanced} | Financial ML §4c |
| `feature_cutoff_policy` object | object | Financial ML §4d, §4f |
| `ablation_plan` object | object | Experimental design §5a |
| `factorial_grid_type` | enum | Experimental design §5b |
| `grid_n_levels` | array of integers | Experimental design §5b |
| `ocat_controls_included` | boolean | Experimental design §5c |
| `interaction_effects_analyzed` | boolean | Experimental design §5d |
| `interaction_effects_found` | boolean | Experimental design §5d |
| `interaction_effects_expected` | boolean | Experimental design §5d |
| `trial_budget` | integer >= 1 | Experimental design §5e |
| `trial_budget_type` | enum {hard_limit, advisory} | Experimental design §5e |
| `preregistered_factors` | array | Experimental design §5f |
| `preregistered_at` | ISO 8601 | Experimental design §5f |

### 8b. OutcomeSpec (new, deferred)

OutcomeSpec v1 declares the outcome labeling scheme, window boundaries, purged/embargo configuration, and required evidence roles. It defines what the measurement target is, when the window starts and ends, how labels are assigned, and what evidence (OOS, live, uncertainty quantification) must be provided by ModelAssessmentSpec or runner outputs. It does NOT own computed statistics — those belong to ModelAssessmentSpec or TrialLedger.

|| Field | Type | Source Requirement |
||-------|------|--------------------|
|| `outcome_id` | string | Core identity |
|| `experiment_id` | string (ref) | Core identity |
|| `labeling_scheme` | enum {triple_barrier, fixed_horizon, return_threshold, custom} | Financial ML §4g |
|| `labeling_horizon_days` | integer | Financial ML §4g |
|| `outcome_window_start` | ISO 8601 | Financial ML §4g |
|| `outcome_window_end` | ISO 8601 | Financial ML §4g |
|| `purge_gap_days` | integer >= 0 | Financial ML §4a |
|| `embargo_fraction` | float [0, 1] | Financial ML §4b |
|| `embargo_days` | integer (computed) | Financial ML §4b |
|| `walk_forward_type` | enum {expanding, rolling} | Financial ML §4c |
|| `n_splits` | integer >= 1 | Financial ML §4c |
|| `cv_n_folds` | integer | Financial ML §4e |
|| `cv_fold_definitions` | array | Financial ML §4e |
|| `sample_overlap_warning` | string or null | Financial ML §4h |
|| `overlapping_experiments` | array of experiment IDs | Financial ML §4h |
|| `event_window_spec` object | object | Hypothesis taxonomy §6h |
|| `null_model_name` | string | Statistical inference §7f |
|| `null_model_performance` | float | Statistical inference §7f |
|| `performance_vs_null` | float | Statistical inference §7f |
|| `is_oos_required` | boolean | Evidence role declaration |
|| `is_live_required` | boolean | Evidence role declaration |
|| `uncertainty_required` | boolean | Evidence role declaration |
|| `model_assessment_ref` | string (ref) | Links to ModelAssessmentSpec providing evidence |
|| `runner_output_refs` | array of strings (refs) | Links to runner outputs with computed metrics |

### 8c. InstrumentUniverseSpec (new, deferred)

InstrumentUniverseSpec v1 will declare tradable instruments, eligibility criteria, and data source constraints.

| Field | Type | Source Requirement |
|-------|------|--------------------|
| `universe_id` | string | Core identity |
| `instrument_list` | array of instrument objects | Core scope |
| `eligibility_criteria` | object | Scope control |
| `data_source_refs` | array of data manifest IDs | Data provenance |
| `sample_overlap_warning` | string or null | Financial ML §4h |
| `liquidity_filter` object | object | Hypothesis taxonomy §6i |
| `market_impact_model` | string | Hypothesis taxonomy §6i |

### 8d. SearchSpaceManifest (existing, PR #59)

SSM v1 is complete for search boundaries, budget, constraints, and forbidden modes. No new fields are required from this document — existing fields are sufficient.

### 8e. TrialLedger (existing, PR #58)

TrialLedger v1 is complete for append-only trial accounting, promotion rules, and governance states. The following fields need to be added in a future PR:

| New Field | Type | Source Requirement |
|-----------|------|--------------------|
| `trial_family_id` | string | Backtest overfitting §3a |
| `n_tried` | integer >= 1 | Backtest overfitting §3b |
| `selected_variant_id` | string | Backtest overfitting §3e |
| `unadjusted_return` | float | Backtest overfitting §3e |
| `selection_timestamp` | ISO 8601 | Backtest overfitting §3e |
| `pbo_estimate` | float 0–1 | Backtest overfitting §3g |
| `pbo_not_applicable_reason` | string | Backtest overfitting §3g |
| `dsr_estimate` | float | Backtest overfitting §3h |
| `dsr_not_applicable_reason` | string | Backtest overfitting §3h |
| `factor_levels` | object | Experimental design §5g |
| `variant_fingerprint` | string | Experimental design §5g |
| `selection_method` | enum | Statistical inference §7c |
| `selection_biased_estimate` | boolean | Statistical inference §7c |
| `selection_adjustment_method` | string | Statistical inference §7c |
| `required_sample_size` | integer | Experimental design §5d |
| `achieved_sample_size` | integer | Experimental design §5d |
| `randomization_seed` | integer or null | Experimental design §5c |

### 8f. ModelAssessmentSpec (existing, PRs #63, #64)

ModelAssessmentSpec v1 is complete for assessment identity, metrics, population stability, and feature statistics. The following fields need to be added in a future PR:

| New Field | Type | Source Requirement |
|-----------|------|--------------------|
| `feature_importance_sample` | enum {in_sample, out_of_sample, not_computed} | Financial ML §4d |
| `ensemble_diversity_score` | float 0–1 | Financial ML §4d |
| `diversity_not_computed_reason` | string | Financial ML §4d |
| `expected_return_decomposition` object | object | Hypothesis taxonomy §6c |
| `carry_rate_expected` | float | Hypothesis taxonomy §6d |
| `carry_rate_realized` | float | Hypothesis taxonomy §6d |
| `value_spread_initial` | float | Hypothesis taxonomy §6e |
| `value_spread_decay_rate` | float | Hypothesis taxonomy §6e |
| `momentum_half_life_days` | integer | Hypothesis taxonomy §6f |
| `momentum_decay_function` | enum | Hypothesis taxonomy §6f |
| `structural_flow_decay_rate` | float | Hypothesis taxonomy §6c |
| `structural_flow_half_life_days` | integer | Hypothesis taxonomy §6c |
| `implementation_shortfall_estimate` | float | Hypothesis taxonomy §6i |
| `market_impact_model` | string | Hypothesis taxonomy §6i |
| `risk_adjusted_return` | float | Hypothesis taxonomy §6a |
| `cv_generalization_error` | float | Statistical inference §7a |
| `cv_n_folds` | integer | Statistical inference §7a |
| `return_point_estimate` | float | Statistical inference §7a |
| `return_ci_lower` | float | Statistical inference §7a |
| `return_ci_upper` | float | Statistical inference §7a |
| `ci_coverage_level` | float | Statistical inference §7a |
| `null_model_description` | string | Statistical inference §7f |
| `null_model_performance` | float | Statistical inference §7f |
| `performance_vs_null` | float | Statistical inference §7f |
| `bootstrap_n_iterations` | integer >= 100 | Statistical inference §7b |
| `bootstrap_ci_level` | float 0.9–0.99 | Statistical inference §7b |
| `bootstrap_ci_method` | enum | Statistical inference §7b |
| `bootstrap_not_applicable_reason` | string | Statistical inference §7b |
| `robustness_checks_passed` | boolean | Statistical inference §7e |
| `robustness_methods_tried` | array of strings | Statistical inference §7e |
| `fragility_identified` | boolean | Statistical inference §7e |
| `fragility_description` | string | Statistical inference §7e |

### 8g. EdgeHypothesisRegistry (existing, PRs #66–#74)

EHR v1 is complete for hypothesis identity, lifecycle events, canonical IDs, and governance. The following fields need to be added in a future PR:

| New Field | Type | Source Requirement |
|-----------|------|--------------------|
| `hypothesis_category` | enum (10 values) | Hypothesis taxonomy §6a–§6j |
| `risk_premium_type` | enum | Hypothesis taxonomy §6a |
| `behavioral_mechanism` | enum | Hypothesis taxonomy §6b |
| `mechanism_description` | string (min 50 chars) | Hypothesis taxonomy §6b |
| `flow_trigger` | enum | Hypothesis taxonomy §6c |
| `carry_type` | enum | Hypothesis taxonomy §6d |
| `value_measure` | enum | Hypothesis taxonomy §6e |
| `momentum_formation_period` | enum | Hypothesis taxonomy §6f |
| `momentum_reversal_threshold` | float | Hypothesis taxonomy §6f |
| `seasonality_type` | enum | Hypothesis taxonomy §6g |
| `seasonality_calendar` | string | Hypothesis taxonomy §6g |
| `event_type` | enum | Hypothesis taxonomy §6h |
| `liquidity_effect_type` | enum | Hypothesis taxonomy §6i |
| `proposed_mechanism` | string (min 50 chars) | Hypothesis taxonomy §6j |
| `replication_sample_start` | ISO 8601 | Hypothesis taxonomy §6j |
| `replication_sample_end` | ISO 8601 | Hypothesis taxonomy §6j |
| `anomaly_decay_expected` | boolean | Hypothesis taxonomy §6 |
| `decay_half_life_days` | integer or null | Hypothesis taxonomy §6 |
| `post_hoc_category_flag` | boolean | Hypothesis taxonomy §6j |

### 8h. ReviewPacket (new, deferred)

ReviewPacket is the manual review output artifact. Its design must incorporate selection bias disclosures, robustness analysis, PBO/Dsr findings, and uncertainty quantification from the statistical inference requirements.

| Field | Type | Source Requirement |
|-------|------|--------------------|
| `packet_id` | string | Core identity |
| `experiment_id` | string (ref) | Core identity |
| `reviewer` | string | Manual review |
| `selection_bias_analysis` | section | Statistical inference §7c |
| `robustness_analysis` | section | Statistical inference §7e |
| `pbo_findings` | section | Backtest overfitting §3g |
| `dsr_findings` | section | Backtest overfitting §3h |
| `replication_analysis` | section | Hypothesis taxonomy §6j (post-hoc) |
| `interaction_analysis` | section | Experimental design §5d |
| `null_model_comparison` | section | Statistical inference §7f |
| `recommended_action` | enum {approve, reject, revise, defer} | Manual review |
| `review_rationale` | string (min 100 chars) | Manual review |

### 8i. Runner Outputs (future runner logic)

The runner produces intermediate artifacts consumed by validators and ReviewPacket. The literature requirements imply the following runner output constraints:

| Output | Constraint | Source Requirement |
|--------|-----------|--------------------|
| Trial run log | Must record `variant_fingerprint` and all `factor_levels` | Experimental design §5g |
| Equity curve | Must support `equity_curve_ci_lower/upper` output arrays | Statistical inference §7d |
| PBO estimate | Runner should support PBO computation via combinatorial enumeration of `trial_family_id` variants | Backtest overfitting §3g |
| DSR estimate | Runner should support DSR computation given `n_tried` and per-variant returns | Backtest overfitting §3h |
| Walk-forward report | Must produce per-split performance metrics for `n_splits` folds | Financial ML §4c |
| Bootstrap distribution | Must store `bootstrap_artifact_ref` if `bootstrap_n_iterations >= 100` | Statistical inference §7b |
| Null model baseline | Must produce and store null model performance before variant selection | Statistical inference §7f |

### 8j. Artifact Ownership Clarification

The uploaded literature packet (§3i-§3l) is a requirements backlog, not a mandate to add every listed field to OutcomeSpec v1. Each field belongs to exactly one owning artifact based on whether it is a declaration (owned by OutcomeSpec, ExperimentSpec, SearchSpaceManifest), a recorded trial fact (owned by TrialLedger), an assessment output (owned by ModelAssessmentSpec), a computed intermediate artifact (owned by Runner Outputs), an aggregated review conclusion (owned by ReviewPacket), or a search-space declaration (owned by SearchSpaceManifest).

|| Artifact | Owns | Does NOT Own |
||----------|------|--------------|
| **OutcomeSpec** | outcome_id, experiment_id, labeling_scheme, labeling_horizon_days, outcome_window_start, outcome_window_end, purge_gap_days, embargo_fraction, embargo_days, walk_forward_type, n_splits, cv_n_folds, cv_fold_definitions, sample_overlap_warning, overlapping_experiments, event_window_spec, is_oos_required, is_live_required, uncertainty_required, model_assessment_refs, runner_output_refs | pbo_estimate, backtest_pnl_haircut, Sharpe haircut, complexity score, probability_of_loss, factor exposure stability results, bootstrap distributions, CSCV outputs |
| **TrialLedger** | trial_family_id, all_variants_preserved, n_tried, selected_variant_id, pbo_estimate, pbo_method, pbo_not_applicable_reason, sample_length, number_of_trials, sample_to_trial_ratio, degrees_of_freedom_warning, cscv_n_bags, cscv_prob_s_overfit, backtest_live_split_date, backtest_period_length, live_period_length, live_performance_required, overfit_freedom_score, tweak_freedom_score | outcome window declarations, labeling schemes |
| **ModelAssessmentSpec** | pbo_estimate, pbo_method, pbo_not_applicable_reason, backtest_pnl_haircut, overfit_discount_factor, haircut_method, haircut_not_applicable_reason, accepted_threshold_metric, accepted_threshold_value, original_strategy_ref, modified_strategy_ref, strategy_correlation_to_original, overfit_assumption_note, adjusted_expected_oos_sharpe, probability_of_loss, expected_oos_rank, false_discovery_rate_estimate, adjusted_p_value, overfit_adjustment_method, bootstrap_method, stationary_bootstrap_block_parameter, bayesian_overfit_model, realized_sharpe_haircut, strategy_complexity_score, number_of_signals, number_of_parameters, rule_count, factor_exposure_stability_check, factor_loading_backtest, factor_loading_live, factor_exposure_drift_flag, feature_importance_sample, ensemble_diversity_score, expected_return_decomposition, null_model_description, null_model_performance, performance_vs_null, return_point_estimate, return_ci_lower, return_ci_upper, ci_coverage_level, cv_generalization_error, cv_n_folds, bootstrap_n_iterations, bootstrap_ci_level, robustness_checks_passed, robustness_methods_tried, fragility_identified, fragility_description | outcome window declarations, labeling schemes, trial family identity |
| **SearchSpaceManifest** | search_space_id, search_budget, max_variants_per_family, trial_family_scope, search_mode, forbidden_modes | trial accounting, assessment outputs |
| **ExperimentSpec** | experiment_id, hypothesis_ref, search_space_ref, data_manifest_refs, trial_generation_mode, prohibited_modes, feature_cutoff_policy, decision_timestamp_policy, trial_family_id (link), n_tried (link) | computed PBO, haircut outputs, live performance |
| **Runner Outputs** | equity_curve arrays, bootstrap distributions, CSCV bag results, walk-forward per-split metrics, null model baseline performance, factor regression outputs, OOS performance distribution artifact | outcome declarations, trial accounting |
| **ReviewPacket** | selection_bias_analysis, robustness_analysis, pbo_findings, dsr_findings, replication_analysis, interaction_analysis, null_model_comparison, recommended_action, review_rationale | trial accounting, assessment computations |

---

## 9. Agent Requirements

Hermes (this agent) and OpenClaw may assist with hypothesis extraction, falsification test proposal, and review packet preparation under the following hard constraints.

### 9a. No Registry Status Mutation Without Manual Review

Agent-suggested hypothesis status changes must require explicit manual review and approval. Agents may draft proposed status transitions but may not commit them to EdgeHypothesisRegistry.

- **Rule:** `lifecycle_events[].registry_mutation_mode` must remain `manual` unless explicitly unlocked via a separate governance proposal
- **Agent role:** Draft proposed `CandidateSpec` and `EdgeHypothesisCard` content; submit for manual review

### 9b. No Approval of Promotion

Agents must not approve or recommend promotion of a trial variant to a higher governance state. Promotion recommendations may be generated, but approval authority is exclusively human.

- **Rule:** `governance_state: selected` transitions require a `ManualDecision` artifact signed by a human reviewer
- **Agent role:** Generate the analysis and recommendation; human makes the final decision

### 9c. No Bypass of Validators

All artifacts must pass through AED validators before entering the governance system. Agents must not suppress, bypass, or override validator outputs.

- **Rule:** Any artifact failing a validator must be corrected before proceeding; validator blocker output is authoritative
- **Agent role:** Help interpret validator output and suggest corrections; cannot suppress or override

### 9d. No Live Trading

Agents must not submit, approve, or execute live trading orders through any mechanism.

- **Rule:** `trial_generation_mode` values of `autonomous_search`, `bayesian_optimization`, `genetic_programming` are prohibited unless explicitly unlocked later via separate governance proposal
- **Agent role:** Propose trial configurations for manual review and execution

### 9e. No Unlocked Autonomous Search

Autonomous search, Bayesian optimization, and genetic programming remain locked until trial accounting, PBO estimation, and statistical inference requirements from this document are implemented and verified.

- **Rule:** `search_mode: autonomous` is prohibited in SearchSpaceManifest; `trial_generation_mode: bayesian_optimization` and `trial_generation_mode: genetic_programming` are prohibited in ExperimentSpec until AED implements the full statistical inference pipeline in §7
- **Agent role:** Propose hypotheses, trial designs, and analysis methods within the locked constraints

---

## 10. Near-Term Design Implications

The requirements in this document directly guide the next batch of AED design and implementation PRs.

### 10a. OutcomeSpec v1

OutcomeSpec v1 should be the next schema designed. Its core job is declaring the outcome labeling scheme and implementing the financial ML validation requirements from §4 and the statistical inference requirements from §7.

**The literature packet is a requirements backlog. OutcomeSpec v1 should NOT attempt to absorb all 49 new fields from §3i-§3l. Priority for OutcomeSpec v1: window semantics, labeling scheme, purge/embargo config, and required evidence role declarations only. All overfit statistics, search pressure metrics, Sharpe haircuts, Bayesian adjusted estimates, complexity scores, and factor exposure results belong in ModelAssessmentSpec extensions or TrialLedger — not OutcomeSpec.**

Priority fields: `labeling_scheme`, `labeling_horizon_days`, `outcome_window_start/end`, `purge_gap_days`, `embargo_fraction`, `walk_forward_type`, `n_splits`, `cv_n_folds`, `cv_fold_definitions`, `is_oos_required`, `is_live_required`, `uncertainty_required`, `model_assessment_ref`, `runner_output_refs`.

Design sequence: OutcomeSpec v1 design doc → JSON schema → fixtures → local validator → pytest → CI wiring.

### 10b. InstrumentUniverseSpec v1

InstrumentUniverseSpec v1 should follow OutcomeSpec. Its primary job is declaring tradable instruments and their eligibility constraints, with fields for liquidity filters and market impact models from §6i.

Priority fields: `universe_id`, `instrument_list`, `eligibility_criteria`, `data_source_refs`, `liquidity_filter`, `market_impact_model`.

### 10c. EventStudySpec v1

EventStudySpec v1 (already deferred) is directly implicated by §6h. The `event_window_spec` in OutcomeSpec references EventStudySpec, so they should be designed together. The event risk taxonomy in §6h provides the basis for the event type enum.

Priority fields: `event_id`, `event_type` (aligned with §6h enum), `normal_performance_estimation_window`, `pre_event_window_days`, `post_event_window_days`, `inference_method`.

### 10d. OptionsEventRiskSpec v1

OptionsEventRiskSpec v1 (already deferred) extends EventStudySpec for options-specific risk around IV ramp, jump exposure, and term structure. Its requirements are largely defined in the existing `docs/options_event_risk_protocol.md`.

The literature requirements add: `implementation_shortfall_estimate`, `market_impact_model`, and the event type alignment with §6h.

### 10e. PreEarningsProfile v1

PreEarningsProfile v1 is a domain-specific research module. Its design should explicitly satisfy the domain-neutral AED architecture boundary: all fields specific to pre-earnings analysis (IV ramp patterns, earnings cycle effects, implied move vs. realized move) must live in PreEarningsProfile, not in core schemas.

The hypothesis taxonomy in §6h maps directly: `event_type: earnings` and `seasonality_type: earnings_cycle` are the primary PreEarningsProfile mechanisms.

### 10f. Future ModelAssessmentSpec Extensions

ModelAssessmentSpec will require a significant extension pass to add all the fields in §8f (uncertainty quantification, bootstrap, robustness, null model comparison, decomposition fields). This should be done as a coordinated batch after OutcomeSpec is complete, since OutcomeSpec's walk-forward outputs feed into ModelAssessmentSpec's uncertainty estimates.

### 10g. Future ReviewPacket Design

ReviewPacket design should begin after ModelAssessmentSpec extensions are complete, since ReviewPacket is the consumer artifact. The section structure in §8h maps directly to requirements from §3 (PBO/Dsr findings), §5d (interaction analysis), §6j (replication analysis for post-hoc hypotheses), §7c (selection bias analysis), and §7e (robustness analysis).

ReviewPacket design should include a mandatory field for `recommended_action` with enum values and a free-text `review_rationale` requiring at least 100 characters to prevent boilerplate approvals.

---

## 11. Stop Rules

The following stop rules are explicitly preserved from prior AED governance decisions. They are not changed by this document and remain in effect until separately modified via explicit governance proposal.

| Stop Rule | Source | Status |
|-----------|--------|--------|
| No `autonomous_search` in SearchSpaceManifest `search_mode` | PR #65, PR #69 | Active |
| No `bayesian_optimization` in ExperimentSpec `trial_generation_mode` | PR #65, PR #69 | Active |
| No `genetic_programming` in ExperimentSpec `trial_generation_mode` | PR #65, PR #69 | Active |
| No `automated_promotion` in TrialLedger governance transitions | PR #65 | Active |
| No `automated_registry_mutation` in EHR lifecycle events | PR #69 | Active |
| No `live_trading` — no runner may submit live orders | PR #65 | Active |
| No `production_execution` — runner may not execute in live accounts | PR #65 | Active |
| No GCRU integration until separately designed and approved | PR #65 | Active |

---

## 13. Uploaded Source Packet Summary

The following table summarizes the four uploaded backtest-overfitting papers that were used to derive additional AED requirements. These papers were uploaded as source material and their requirements have been incorporated into Sections 3i–3l above.

| Source | Core Mechanism | AED Risk | Concrete Requirement Count | Future Artifact |
|--------|----------------|----------|----------------------------|-----------------|
| Bailey, Borwein, López de Prado, Zhu — PBO Mathematical | Combinatorial enumeration of PBO over all tried variants; CSCV approximation | False discovery from underpowered search; PBO enumeration incomplete if variants deleted | 13 | TrialLedger PBO fields, CSCV bagging fields |
| Rej, Seager, Bouchaud — Overfit Discount/Haircut | Multiplicative haircut to backtest PnL based on trial count and selection threshold | Backtest PnL inflation from selection bias | 12 | ModelAssessmentSpec haircut fields |
| Witzany — Bayesian Approach | Bayesian posterior probability of loss; FDR-adjusted p-values; bootstrap-based uncertainty | Overestimates of OOS Sharpe from selection bias | 10 | ModelAssessmentSpec Bayesian fields |
| Suhonen, Lennkh, Perez — Backtest/Live Split | Backtest/live split date analysis; factor exposure stability; realized Sharpe haircut | Factor loading drift; live performance divergence from backtest | 14 | TrialLedger/ModelAssessmentSpec split fields |

---

## 12. Conclusion

This PR (#81) makes no implementation changes. It creates a structured requirements baseline for the next phase of AED schema and validator development.

The requirements in this document are converted from five peer-reviewed sources into concrete, artifact-specific fields and rules. No new theory is introduced; all requirements are traceable to specific methodological risks identified in the literature.

The next design PRs should use this document as their requirements source. Before writing a schema or validator for OutcomeSpec, InstrumentUniverseSpec, EventStudySpec, OptionsEventRiskSpec, or any ModelAssessmentSpec extension, the relevant section of this document should be consulted and any gaps between this requirements baseline and the proposed design should be explicitly resolved or deferred with documented rationale.

**No implementation in this PR. Future PRs will convert these requirements into schemas, fixtures, validators, and review packet fields.**
