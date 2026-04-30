# TrialLedger v1 — Design

**Provenance:** This document is split from `docs/trial_ledger_search_space_manifest_v1.md` (PR #39). It is the authoritative v1 design for TrialLedger alone. `docs/trial_ledger_search_space_manifest_v1.md` is superseded by this doc and `docs/search_space_manifest_v1_design.md`.

---

## 1. Purpose

TrialLedger is an **append-only record of every attempted research trial**, including failed, abandoned, interrupted, and rejected runs. It is the canonical log for trial counting and is required for defensible multiple-testing corrections (DSR, PBO, FWER).

**What TrialLedger does:**
- Records every trial attempt with full provenance
- Enables PBO/DSR/FWER computation by providing accurate trial counts
- Makes cherry-picking and unreported failures visible
- Links exploratory anomaly attempts to confirmatory tests
- Provides audit trail for human review packets

**What TrialLedger does not do:**
- It does not trigger any automated promotion or registry mutation
- It is not a hypothesis registry — it records runs, not reviewed hypotheses
- It does not enforce SearchSpaceManifest boundaries (that is the runner's job)
- It does not make accept/reject decisions

---

## 2. Non-Goals

- No automated registry mutation
- No automated promotion
- No live trading or production execution
- No autonomous search, Bayesian optimization, or genetic programming
- No code implementation in this design doc

---

## 3. Relationship to Other AED Components

### EdgeHypothesisRegistry
- The registry records reviewed hypotheses with stable IDs
- TrialLedger records every run attempt against a hypothesis
- A hypothesis may have many TrialLedger entries (one per trial or batch)
- Registry status (`proposed`, `specified`, `testing`, `falsified`, `parked`, `promoted`) is a human review label; TrialLedger status is an execution record

### HypothesisSpec / CandidateSpec
- `HypothesisSpec` declares the strategy family, constraints, and validation plan
- `CandidateSpec` is one generated candidate from that spec
- TrialLedger entries link to `hypothesis_id` and optionally `candidate_id`
- One `hypothesis_id` may produce many `candidate_id` runs, each with its own TrialLedger entry

### DataManifest
- TrialLedger records which `dataset_id` or `manifest_id` was used for each trial
- DataManifest defines the data boundary; TrialLedger enforces that every use is recorded

### Event/Options Contract
- Event/Options contract validation is a pre-condition for clean trial data
- TrialLedger entries reference the contract version or manifest ID used
- Contract violations discovered post-run are recorded as failure reasons

### Audit Reports
- Audit reports consume TrialLedger data to compute trial burden
- PBO, DSR, and sample-size checks attach to TrialLedger entries
- Review packets must reference TrialLedger entries to be valid

### Evaluator Labels
- Evaluator labels (`invalid_run`, `execution_failed`, `needs_more_data`, `promising_for_review`) are outputs attached to TrialLedger entries
- Labels are **review signals, not proof** — a `promising_for_review` label does not change registry status
- All evaluator labels are recorded in the TrialLedger entry that generated them

---

## 4. Required Fields

Each TrialLedger entry must include the following fields:

### Identity
| Field | Type | Description |
|-------|------|-------------|
| `trial_id` | string | Stable unique identifier for this trial |
| `run_id` | string | Concrete execution process or job identifier |
| `search_run_id` | string | Groups trials under a single SearchRun |
| `parent_trial_id` | string (optional) | Links nested or follow-up experiments |
| `trial_sequence_number` | integer | Incremental count within a SearchRun |

### Hypothesis linkage
| Field | Type | Description |
|-------|------|-------------|
| `hypothesis_id` | string (optional) | Linked HypothesisSpec ID |
| `anomaly_id` | string (optional) | Linked ExploratoryAnomalySpec ID |
| `candidate_id` | string (optional) | CandidateSpec identifier |
| `search_space_id` | string (optional) | Reference to SearchSpaceManifest used |

### Source and type
| Field | Type | Description |
|-------|------|-------------|
| `trial_type` | enum | `grid_search`, `randomized_search`, `single_trial`, `probe`, `ablation`, `falsification`, `replication` |
| `source_lane` | enum | `theory_first`, `exploratory_anomaly`, `post_hoc_theory`, `confirmatory` |
| `theory_timing` | enum | `pre_discovery`, `post_discovery`, `mixed`, `unknown` |
| `llm_assisted` | boolean | Whether LLMs assisted in parameter generation or theory |

### Parameter identity
| Field | Type | Description |
|-------|------|-------------|
| `parameter_hash` | string | Stable SHA-256 hash (first 16 hex) of the parameter set |
| `parameters` | JSON | Full parameter set used (compact serialization) |

### Data scope
| Field | Type | Description |
|-------|------|-------------|
| `dataset_id` | string | Dataset or DataManifest pointer used |
| `label_id` | string (optional) | Declared label definition ID (if ML used) |
| `universe_id` | string | Universe or asset set used |
| `event_study_id` | string (optional) | Linked EventStudySpec ID |
| `options_event_risk_id` | string (optional) | Linked OptionsEventRiskSpec ID |
| `sample_start` | ISO date | Sample window start |
| `sample_end` | ISO date | Sample window end |

### Execution scope
| Field | Type | Description |
|-------|------|-------------|
| `runner_id` | string | Identifier for the runner that executed this trial |
| `config_hash` | string | SHA-256 hash (first 16 hex) of the runner config |
| `git_commit` | string | Git commit SHA at execution time |
| `environment_fingerprint` | string | Environment identifier (e.g., Python version + key package versions) |
| `random_seed` | integer (optional) | Seed used for random number generation |
| `validation_scheme` | enum | `purged_cpcv`, `k_fold`, `holdout`, `purged_cv`, `none` |
| `split_id` | string (optional) | Which validation split or fold |

### Timestamps
| Field | Type | Description |
|-------|------|-------------|
| `entry_timestamp` | ISO 8601 | When trial was scheduled/entered |
| `started_at` | ISO 8601 | Runtime start |
| `completed_at` | ISO 8601 | Runtime completion |
| `exit_timestamp` | ISO 8601 | When trial completed or stopped |

### Status and outcome
| Field | Type | Description |
|-------|------|-------------|
| `status` | enum | `planned`, `running`, `completed`, `failed`, `abandoned`, `invalidated`, `excluded_from_review`, `reported` |
| `failure_reason` | string (optional) | Structured short code + human note |
| `metrics_summary` | JSON | Compact metrics (e.g., `{"net_return": 0.6, "sharpe": 1.2}`) |
| `artifact_paths` | JSON | Pointers to output artifacts (read-only paths) |

### Review and governance
| Field | Type | Description |
|-------|------|-------------|
| `created_by` | string | User or service that created the trial |
| `review_required` | boolean | Whether manual review is required |
| `reported_in_review_packet` | boolean | Whether included in a ReviewPacket |
| `evaluator_label` | enum | `invalid_run`, `execution_failed`, `needs_more_data`, `promising_for_review` |
| `evaluator_reason` | string | Human-readable reason for the label |

### Promotion state
| Field | Type | Description |
|-------|------|-------------|
| `promotion_status` | enum | `raw_result`, `reviewed`, `rejected`, `provisional`, `accepted`, `killed`, `promoted_to_confirmatory` |

---

## 5. Trial Identity and Deterministic IDs

### trial_id
A stable, unique identifier assigned at trial creation. Format recommendation: `TRL-YYYY-NNNN` where `YYYY` is the year and `NNNN` is a zero-padded sequential number within that year.

### parameter_hash
Computed as the first 16 hex characters of `SHA-256(canonical_json(sorted(parameters)))`. Two runs with identical parameters on identical data in identical environment **should** produce the same hash. This enables detection of silent re-runs.

### environment_fingerprint
A string capturing the key environment variables that affect numerical results. Minimum: Python version, key package versions (`numpy`, `pandas`). Format is not standardized; implementers should be conservative and include enough context to reproduce.

---

## 6. Search Origin (source_lane)

Every trial must declare its source lane. This determines how trial burden is counted for multiple-testing corrections.

| source_lane | Description | Trial burden counted for PBO/DSR? |
|-------------|-------------|----------------------------------|
| `theory_first` | Hypothesis declared before any data was examined | Yes, fully |
| `exploratory_anomaly` | Anomaly discovered in data, then investigated | Yes, inherits to post_hoc_theory |
| `post_hoc_theory` | Theory constructed after results were known | Yes, inherits from exploratory_anomaly |
| `confirmatory` | Pre-registered test on held-out data | Counted separately |

**Key rule:** Exploratory trials cannot be "promoted" to `accepted` without a confirmatory trial on untouched data. The `promotion_status` field enforces this.

---

## 7. Data Scope

Data scope fields record exactly what data a trial consumed. These are critical for:
- Detecting data leakage (same data used for exploratory and confirmatory)
- Ensuring reproducibility
- Enforcing point-in-time constraints

`dataset_id` points to a DataManifest or raw dataset identifier. `sample_start` and `sample_end` define the temporal window. `universe_id` defines the asset universe.

---

## 8. Execution Scope

Execution scope captures the runtime environment so any trial can be reproduced:
- `runner_id`: which runner executed this (for audit and reproducibility)
- `config_hash`: a hash of the runner configuration
- `git_commit`: the code version at execution time
- `environment_fingerprint`: Python version + package versions
- `random_seed`: for stochastic algorithms

---

## 9. Results

Results are stored in `metrics_summary` (structured JSON) and `artifact_paths` (pointers to files). No result data is embedded in the TrialLedger entry itself — artifacts are referenced by path and are read-only.

`evaluator_label` records the automated label from `evaluate_ledger_entry()`. This label is a review signal only.

---

## 10. Review and Governance

### promotion_status values

| Value | Meaning |
|-------|---------|
| `raw_result` | Newly recorded; no review yet |
| `reviewed` | Human has examined this result |
| `rejected` | Human review determined this result does not support advancement |
| `provisional` |暂定 — some evidence but not yet conclusive |
| `accepted` | Human review accepted this as valid confirmatory evidence |
| `killed` | Human review killed this hypothesis |
| `promoted_to_confirmatory` | This exploratory result has been used as the basis for a confirmatory trial |

### Promotion Rule (hard rule)

> **Exploratory results cannot be promoted to `accepted` without a confirmatory trial on untouched data.**

This rule is enforced by the promotion_status transition logic:
- `exploratory_anomaly` lane results may only reach `provisional` without a subsequent `confirmatory` lane result
- A `confirmatory` result on untouched data can elevate an earlier `provisional` to `accepted`
- The `anomaly_id` must be preserved in the confirmatory trial's TrialLedger entry

### PBO, DSR, Sample-Size, and Leakage Checks

These attach to TrialLedger entries as follows:
- **PBO (Probability of Backtest Overfitting):** computed across all trials with the same `hypothesis_id` and `search_space_id`. Requires trial count.
- **DSR (Deflated Sharpe Ratio):** deflates realized Sharpe by the trial count (including failed and abandoned trials).
- **Sample-size gates:** a trial with fewer than the minimum required observations must be flagged in `failure_reason`.
- **Leakage checks:** if the same `dataset_id` + overlapping `sample_start/sample_end` appears in both `exploratory_anomaly` and `confirmatory` lanes for the same `hypothesis_id`, the confirmatory trial must be flagged as potentially leaked and its `promotion_status` set to `rejected` until reviewed.

---

## 11. JSON Example

```json
{
  "trial_id": "TRL-2026-0042",
  "run_id": "R-2026-042-01",
  "search_run_id": "SR-2026-042",
  "hypothesis_id": "AED-HYP-0017",
  "anomaly_id": null,
  "candidate_id": "CAND-017-003",
  "search_space_id": "SSM-2026-0017",
  "parent_trial_id": null,
  "trial_sequence_number": 3,
  "trial_type": "grid_search",
  "source_lane": "theory_first",
  "theory_timing": "pre_discovery",
  "llm_assisted": false,
  "parameter_hash": "a3f1c9d72e4b0538",
  "parameters": {
    "entry_dpe": 5,
    "delta_target": 0.25,
    "expiry_rank": 1,
    "fill_policy": "MID",
    "lookback": 10
  },
  "dataset_id": "OPTIONS-2021-LANE0",
  "label_id": null,
  "universe_id": "US_OPTIONS_FRONT_MONTH",
  "event_study_id": null,
  "options_event_risk_id": null,
  "sample_start": "2021-01-01",
  "sample_end": "2021-12-31",
  "split_id": "fold-0",
  "validation_scheme": "purged_cpcv",
  "runner_id": "wfa-cpcv-v1",
  "config_hash": "b7e2a110f93c456d",
  "git_commit": "35bbc714",
  "environment_fingerprint": "python3.11-numpy1.24-pandas2.0",
  "random_seed": 42,
  "entry_timestamp": "2026-04-30T10:00:00Z",
  "started_at": "2026-04-30T10:00:01Z",
  "completed_at": "2026-04-30T10:15:32Z",
  "exit_timestamp": "2026-04-30T10:15:32Z",
  "status": "completed",
  "failure_reason": null,
  "metrics_summary": {
    "net_return": 0.063,
    "sharpe_ratio": 1.42,
    "max_drawdown": -0.08,
    "pbo_estimate": 0.04,
    "dsr": 1.18
  },
  "artifact_paths": {
    "backtest_csv": ".wfa/artifacts/trl-2026-0042/backtest.csv",
    "log": ".wfa/artifacts/trl-2026-0042/run.log"
  },
  "created_by": "alice",
  "review_required": true,
  "reported_in_review_packet": false,
  "evaluator_label": "promising_for_review",
  "evaluator_reason": "sharpe > 1.0, pbo < 0.1, drawdown within kill threshold",
  "promotion_status": "raw_result"
}
```

---

## 12. Failure Modes and Validation Rules

### Required field enforcement
- Any TrialLedger entry missing a required field is invalid
- Entries without `trial_id` or with duplicate `trial_id` must be rejected

### Deterministic ID stability
- `trial_id` must not change after creation
- `parameter_hash` must be recomputed from canonical sorted JSON — two identical parameter sets must produce identical hashes

### Status transition rules
- `planned` → `running` → `completed` is the happy path
- `running` → `failed` or `abandoned` is allowed
- `planned` → `invalidated` is allowed (pre-execution invalidation)
- Backward transitions (e.g., `completed` → `running`) must be rejected

### Promotion transition rules
- `raw_result` → `reviewed` is always allowed
- `reviewed` → `rejected` or `provisional` is allowed
- `provisional` → `accepted` requires an explicitly linked confirmatory trial with `source_lane=confirmatory` on untouched data. The linkage is recorded through `confirmatory_trial_id` or an equivalent field on the exploratory trial. `promoted_to_confirmatory` is a `promotion_status`, not a `source_lane`.
- `provisional` → `rejected` is always allowed
- `accepted` → `killed` is allowed (reversal)

### Exploratory-to-confirmatory linkage
- For any `source_lane=post_hoc_theory` entry, a corresponding `anomaly_id` must exist in a prior `source_lane=exploratory_anomaly` entry
- Failure to link is a validation error

---

## 13. Open Questions

1. **ID format standardization:** Should `trial_id` use a namespaced format (e.g., `AED-TRL-2026-0042`) to avoid collisions if the registry is exported or shared?
2. **Artifact GC policy:** Who is responsible for cleaning up old artifact paths? TrialLedger records paths but does not delete them.
3. **Cross-dataset trials:** If one trial uses multiple datasets, should `dataset_id` be an array? Currently defined as a single string.
4. **Runner attribution for CI:** When a trial runs in CI (not local), how should `runner_id` and `environment_fingerprint` be populated?
5. **Append-only enforcement:** This design requires append-only semantics. Who enforces this at the file system level?
6. **Failed trial metrics:** When `status=failed`, should `metrics_summary` be required to be empty/null, or can partial metrics be recorded?
7. **parameter_hash scope:** Should `parameter_hash` include the `dataset_id` and `universe_id`, or only the algorithm parameters?
