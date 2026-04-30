# SearchSpaceManifest v1 — Design

**Provenance:** This document is split from `docs/trial_ledger_search_space_manifest_v1.md` (PR #39). It is the authoritative v1 design for SearchSpaceManifest alone. `docs/trial_ledger_search_space_manifest_v1.md` is superseded by this doc and `docs/trial_ledger_v1_design.md`.

---

## 1. Purpose

SearchSpaceManifest is a **pre-declared boundary of what a search is allowed to test** before execution begins. It declares allowed parameter ranges, model classes, transforms, datasets, and stopping rules. It cannot be modified retroactively for an ongoing SearchRun.

**What SearchSpaceManifest does:**
- Prevents silent parameter expansion after seeing promising results
- Makes search burden explicit and auditable before any trial runs
- Enables reproducibility by recording the exact search boundaries
- Separates exploratory search from confirmatory search
- Provides a machine-readable record for future validator enforcement

**What SearchSpaceManifest does not do:**
- It does not execute any trials
- It does not record trial results (that is TrialLedger's job)
- It does not enforce its own constraints — enforcement is the runner's responsibility
- It does not make promotion decisions

---

## 2. Non-Goals

- No autonomous search
- No Bayesian optimization
- No genetic programming
- No automated promotion
- No live trading or production execution
- No GCRU integration
- No code implementation in this design doc

---

## 3. Relationship to Other AED Components

### TrialLedger
- SearchSpaceManifest defines what was **allowed** to happen before the run
- TrialLedger records **what actually happened** during the run
- Every TrialLedger entry should reference a SearchSpaceManifest
- Post-hoc modification of a SearchSpaceManifest after a TrialLedger entry exists is a governance violation

### DataManifest
- SearchSpaceManifest declares data requirements via `allowed_data_manifests`
- DataManifest defines the actual data interface and schema
- SearchSpaceManifest does not duplicate DataManifest content

### HypothesisSpec / CandidateSpec
- HypothesisSpec declares the strategy family and candidate constraints
- SearchSpaceManifest declares the **runtime boundaries** for a search run
- One HypothesisSpec may generate many CandidateSpecs; SearchSpaceManifest bounds the search over that candidate space

### EdgeHypothesisRegistry
- EdgeHypothesisRegistry stores reviewed hypotheses, not every exploratory attempt
- SearchSpaceManifest bounds the search that leads to hypothesis candidates
- When an exploratory anomaly graduates to a registered hypothesis, the SearchSpaceManifest records are preserved and linked

### Evaluator
- Evaluator labels are computed **after** trials complete under a SearchSpaceManifest
- Evaluator does not modify SearchSpaceManifest
- SearchSpaceManifest provides the `multiple_testing_policy` that the evaluator must apply

---

## 4. Required Fields

### Identity
| Field | Type | Description |
|-------|------|-------------|
| `search_space_id` | string | Stable unique identifier |
| `hypothesis_id` | string (optional) | Linked HypothesisSpec |
| `anomaly_id` | string (optional) | Linked ExploratoryAnomalySpec |
| `created_at` | ISO 8601 | When manifest was created |
| `created_by` | string | Author/owner |
| `review_owner` | string | Reviewer or owning team |
| `source_lane` | enum | `theory_first`, `exploratory_anomaly`, `post_hoc_theory`, `confirmatory` |

### Purpose
| Field | Type | Description |
|-------|------|-------------|
| `purpose` | string | Short human-readable purpose statement |

### Data constraints
| Field | Type | Description |
|-------|------|-------------|
| `allowed_data_manifests` | array of strings | DataManifest IDs that may be used |
| `allowed_date_range` | object | `{start: ISO date, end: ISO date}` |
| `allowed_universe` | string | Allowed universe description |
| `required_columns` | array of strings | Required column names in the dataset |
| `point_in_time_policy` | string | Policy for ensuring point-in-time correctness |
| `coverage_requirements` | string (optional) | Minimum data coverage required |

### Event constraints (if event-driven)
| Field | Type | Description |
|-------|------|-------------|
| `allowed_event_types` | array of strings | e.g., `["earnings", "macro"]` |
| `allowed_event_windows` | object | `{pre: integer days, post: integer days}` |

### Feature and label constraints
| Field | Type | Description |
|-------|------|-------------|
| `allowed_features` | array of strings | Feature names or fingerprints that may be used |
| `forbidden_features` | array of strings | Explicit feature blacklist |
| `allowed_labels` | array of strings | Allowed label definition IDs |

### Model and transform constraints
| Field | Type | Description |
|-------|------|-------------|
| `allowed_model_classes` | array of strings | e.g., `["rule_based", "linear", "tree"]` |
| `forbidden_model_classes` | array of strings | Explicit model class blacklist |
| `allowed_transforms` | array of strings | Allowed data transforms |
| `forbidden_transforms` | array of strings | e.g., `["future_augment", "lookahead"]` |

### Parameter constraints
| Field | Type | Description |
|-------|------|-------------|
| `allowed_parameter_ranges` | object | Parameter name → allowed ranges or enumerations |
| `allowed_hyperparameters` | object | Hyperparameter space declarations |
| `forbidden_degrees_of_freedom` | array of strings | e.g., `["daily_rolling_smoothing_injection"]` |

### Validation constraints
| Field | Type | Description |
|-------|------|-------------|
| `allowed_validation_schemes` | array of strings | e.g., `["purged_cpcv", "k_fold", "holdout"]` |
| `forbidden_validation_schemes` | array of strings | e.g., `[" vanilla_kfold"]` |
| `allowed_metrics` | array of strings | Metrics allowed for optimization |
| `confirmatory_holdout_policy` | string | How confirmatory holdout is reserved |

### Search budget
| Field | Type | Description |
|-------|------|-------------|
| `max_trials` | integer | Soft maximum number of trials allowed |
| `trial_budget` | object | Budget object (see TrialBudget below) |
| `expected_trial_burden` | integer | Human estimate of expected trials |
| `max_parameter_combinations` | integer | Maximum grid combinations to try |
| `max_runtime_minutes` | integer | Maximum wall-clock time |
| `max_data_reads` | integer | Maximum number of dataset reads |
| `max_agent_proposals` | integer | Maximum LLM/agent proposals allowed |

### Stopping rules
| Field | Type | Description |
|-------|------|-------------|
| `stopping_rules` | array of objects | Pre-declared stopping conditions |

### Governance
| Field | Type | Description |
|-------|------|-------------|
| `search_mode` | enum | `manual_grid`, `fixed_sweep`, `literature_replication`, `ablation`, `falsification`, `exploratory_agent_assisted` |
| `multiple_testing_policy` | string | Declared statistical correction policy |
| `promotion_blockers` | array of strings | Reasons this search cannot lead to promotion without extra steps |
| `notes` | string | Free-form notes and references |

---

## 5. TrialBudget Object

`trial_budget` is a sub-object within SearchSpaceManifest:

```json
{
  "total": 100,
  "reserved_for_confirmatory": 20,
  "used_by_exploratory": 0,
  "remaining": 100
}
```

- `total`: maximum trials authorized for this search
- `reserved_for_confirmatory`: trials reserved for confirmatory holdout (must not be consumed by exploratory)
- `used_by_exploratory`: trials consumed by exploratory search (tracked by TrialLedger)
- `remaining`: trials still available

---

## 6. Search Space Identity and Deterministic IDs

### search_space_id
A stable, unique identifier. Format recommendation: `SSM-YYYY-NNNN` where `YYYY` is the year and `NNNN` is a zero-padded sequential number.

The `search_space_id` must be assigned **before** any trial begins. Creating a SearchSpaceManifest retroactively (after trials have run) is a governance violation and must be flagged.

### Relationship to git commit
SearchSpaceManifest should record the `git_commit` at creation time so the exact code state that bounded the search is preserved.

---

## 7. Search Bounds

### Parameter ranges
Each key in `allowed_parameter_ranges` is a parameter name. Values may be:
- An array of discrete values: `[5, 10, 20]`
- An object with `min`, `max`, `step`: `{"min": 5, "max": 20, "step": 5}`
- An object with `distribution`: `{"type": "uniform", "min": 0.01, "max": 0.05}`

### Constraints
Constraints are expressed as JSON logic or natural language in `forbidden_degrees_of_freedom`. Example: `"do_not_use_daily_rolling_mean_as_feature"`.

### Forbidden modes (hardcoded list)
The following are **explicitly forbidden** in SearchSpaceManifest for now and must be rejected by any validator that processes SearchSpaceManifest:

| Forbidden value | Reason |
|-----------------|--------|
| `autonomous_search` | No runner isolation; no circuit breakers |
| `bayesian_optimization` | Requires live execution context |
| `genetic_programming` | No sandboxed evaluation environment |
| `automated_promotion` | No promotion governance in place |
| `live_trading` | No execution layer safety |

Any SearchSpaceManifest declaring one of these modes must be rejected.

---

## 8. Search Mode

| Mode | Description |
|------|-------------|
| `manual_grid` | Human-defined grid; no automated exploration |
| `fixed_sweep` | Pre-specified sweep over fixed parameter combinations |
| `literature_replication` | Replication of published strategy with declared parameters |
| `ablation` | Systematic removal of components from a baseline |
| `falsification` | Attempt to falsify an existing hypothesis |
| `exploratory_agent_assisted` | LLM/agent proposes candidates within declared bounds |

---

## 9. Data Requirements

### manifest IDs
`allowed_data_manifests` lists DataManifest IDs that this search is permitted to use. If a runner attempts to use a dataset not in this list, it is a violation.

### required columns
`required_columns` lists column names that must be present in the dataset. If a dataset is missing a required column, the search cannot proceed.

### point_in_time_policy
Declares how the search ensures no future data leaks into past decisions. Example: `"all_features_must_have_timestamp_le_completion_time"`.

### coverage_requirements
Minimum acceptable coverage for the dataset (e.g., "≥90% non-null for required columns").

---

## 10. Search Burden Accounting

Search burden is the accumulated number and scope of tests that must be carried into model assessment and statistical penalties (DSR/PBO/FWER). SearchSpaceManifest contributes to burden tracking as follows:

### Burden components tracked
| Component | Field in SearchSpaceManifest | Field in TrialLedger |
|-----------|------------------------------|---------------------|
| Trials attempted | `expected_trial_burden` | `trial_id` per entry |
| Trials rejected (before running) | `max_trials` limit | TrialLedger with `status=abandoned` |
| Parameter combinations tried | `allowed_parameter_ranges` keys | `parameter_hash` per TrialLedger entry |
| Post-hoc theory revisions | `notes` | TrialLedger `source_lane=post_hoc_theory` entries |
| Hidden manual attempts | Not declared (risk!) | Should appear as `status=abandoned` |

### Preventing "cherry-picking by memory"
The primary defense against cherry-picking by memory is:
1. SearchSpaceManifest is declared **before** any trial runs
2. `expected_trial_burden` is a human estimate of how many trials will be run
3. TrialLedger records every trial including failed ones
4. Any trial not in TrialLedger is not counted in burden calculations
5. Promotion requires a ReviewPacket that references the SearchSpaceManifest and TrialLedger

If a researcher runs trials outside of TrialLedger (e.g., manual ad-hoc runs), those trials **must** be entered as `status=abandoned` or `status=reported` entries to be counted in burden. Untracked trials are a governance violation.

---

## 11. JSON Example

```yaml
search_space_id: SSM-2026-0017
hypothesis_id: AED-HYP-0017
anomaly_id: null
created_at: "2026-04-01T09:00:00Z"
created_by: alice
review_owner: quant-research
source_lane: theory_first
purpose: "Grid search over pre-earnings IV ramp parameters, conservative budget"
allowed_data_manifests:
  - MAN-OPTIONS-2021-LANE0
  - MAN-OPTIONS-2022-LANE0
allowed_date_range:
  start: "2018-01-01"
  end: "2025-12-31"
allowed_universe: US_OPTIONS_FRONT_MONTH
required_columns:
  - event_time_utc
  - option_observation_date
  - mid
  - implied_volatility
  - delta
point_in_time_policy: "all_features_have_timestamp_le_event_time_utc"
coverage_requirements: ">=90% non-null for required columns"
allowed_event_types:
  - earnings
allowed_event_windows:
  pre: 5
  post: 2
allowed_features:
  - iv
  - implied_vol_slope
  - underlying_return
  - iv_rank
forbidden_features:
  - future_return
  - next_day_iv_change
allowed_labels:
  - next_day_iv_change
allowed_model_classes:
  - rule_based
  - linear
forbidden_model_classes:
  - neural_net
  - ensemble_stacking
allowed_transforms:
  - log_return
  - winsorize
  - zscore
forbidden_transforms:
  - future_augment
  - lookahead
allowed_parameter_ranges:
  lookback: [5, 10, 20]
  threshold: [0.01, 0.03, 0.05]
  entry_dpe: [5, 10]
allowed_hyperparameters: {}
forbidden_degrees_of_freedom:
  - "use_intraday_quote_after_event_time_as_feature"
  - "train_on_future_event"
allowed_validation_schemes:
  - purged_cpcv
  - holdout
forbidden_validation_schemes:
  - vanilla_kfold
allowed_metrics:
  - sharpe_ratio
  - net_return
  - deflated_sharpe
confirmatory_holdout_policy: "reserve_2026_q1_q2_for_confirmatory_only"
trial_budget:
  total: 100
  reserved_for_confirmatory: 20
  used_by_exploratory: 0
  remaining: 100
max_trials: 100
expected_trial_burden: 80
max_parameter_combinations: 100
max_runtime_minutes: 480
max_data_reads: 1000
max_agent_proposals: 0
stopping_rules:
  - condition: time_limit
    value: 480
    unit: minutes
  - condition: budget_exhausted
    value: true
search_mode: manual_grid
multiple_testing_policy: "bonferroni_with_dsr_notes"
promotion_blockers:
  - insufficient_mechanism_documentation
  - missing_confirmatory_holdout
notes: "Conservative grid; no autonomous search. Confirmatory holdout reserved."
```

---

## 12. Failure Modes and Validation Rules

### Pre-run validation
- SearchSpaceManifest must be created and assigned an ID **before** any TrialLedger entry references it
- Referencing a `search_space_id` that does not exist is a validation error

### Field completeness
- Any missing required field must cause validation failure
- `search_space_id`, `created_at`, `source_lane`, and `purpose` are required
- If `max_trials` is declared, `trial_budget.total` must match

### Forbidden mode rejection
- Any SearchSpaceManifest with `search_mode` in the forbidden list must be rejected by a validator before execution
- Any attempt to declare `autonomous_search`, `bayesian_optimization`, `genetic_programming`, `automated_promotion`, or `live_trading` is a hard error

### Budget enforcement
- The runner must enforce that `trial_budget.remaining > 0` before starting a new trial
- If `trial_budget.remaining == 0`, no new trials may start under this SearchSpaceManifest

### Retroactive modification
- Modifying a SearchSpaceManifest after the first TrialLedger entry referencing it has been created is a governance violation
- Validators should reject or warn on such modifications

### Date range consistency
- `allowed_date_range.end` must be ≥ `allowed_date_range.start`
- If `confirmatory_holdout_policy` declares specific dates, those dates must not overlap with `allowed_date_range` for exploratory trials

### Parameter range consistency
- For any `allowed_parameter_ranges` entry with `min`/`max`, `min` must be ≤ `max`
- If `step` is declared, `step` must be positive

---

## 13. Open Questions

1. **Schema serialization format:** Should SearchSpaceManifest be stored as JSON, YAML, or TOML? The existing design examples use YAML; JSON may be better for tooling. Decision deferred to later implementation PR.
2. **Versioning strategy:** If a SearchSpaceManifest needs to be updated (e.g., correcting a parameter range error before any trial runs), should a new ID be created or should a `version` field be incremented?
3. **Dynamic budget reallocation:** If exploratory trials are exhausted but confirmatory holdout remains unused, can the researcher reallocate budget? This would require a new SearchSpaceManifest entry and a governance review.
4. **SearchSpaceManifest registry:** Should there be a central index of all SearchSpaceManifests? Currently implied to be a flat file per manifest.
5. **Multiple simultaneous searches:** Can one `hypothesis_id` have multiple active SearchSpaceManifests? If so, how is burden shared across them?
6. **Agent proposal tracking:** If `search_mode=exploratory_agent_assisted`, should `max_agent_proposals` be tracked in TrialLedger as a separate burden category?
7. **Integration with DataManifest:** How does SearchSpaceManifest reference DataManifests that themselves change over time? Should DataManifest have a version or fingerprint?
