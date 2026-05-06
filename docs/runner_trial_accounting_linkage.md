# RunnerOutput Trial-Accounting Linkage — Design

**Design date:** 2026-05-06
**PR:** #184
**Type:** Design only — no implementation

---

## 1. The Problem

A schema-valid `RunnerOutput` artifact proves only that the runner followed its internal contract. It does not prove that the run was authorized, accounted for, or free from off-book execution. Recent AED work on schema-valid success and failure artifacts (PRs #174–#182) hardened the artifact surface, but the overfitting and HARKing failure modes identified in the architecture and governance literature are not mitigated by artifact schema alone.

The failure modes that require trial accounting linkage are:

- **Unreported trials** — a runner variant or configuration that produces output but is never written to the TrialLedger, allowing side-execution of strategy variants without review.
- **Selection bias** — only profitable or interesting runs are promoted to review, while failed or unprofitable runs are discarded silently, biasing the reviewed set.
- **Backtest overfitting** — excessive parameter or rule variation across multiple runs on the same dataset without proper multiplicity correction or PBO accounting.
- **Exploratory anomaly HARKing** — an observed anomaly is used to construct the hypothesis that is then \"confirmed\" by the same data that suggested it, without trial-family accounting.
- **Strategy complexity haircuts** — strategies with excessive rule or parameter counts are not flagged as high-complexity or high-overfitting-risk without explicit complexity accounting.

The `RunnerOutput` artifact, standing alone, does not address any of these. Linkage to `SearchSpaceManifest` and `TrialLedger` is required.

---

## 2. Required Linkage Fields

Every `RunnerOutput` artifact must carry the following identity linkage fields. These fields form the audit trail from a run to the governance artifacts that authorized it.

| Field | Type | Required | Description |
|--------|------|----------|-------------|
| `experiment_id` | string | Always | ExperimentSpec ID or fixture path hash that declared the experiment |
| `data_manifest_id` | string | When data used | DataManifest ID or path; absent when `run_mode = dry_run` with no manifest |
| `search_space_id` | string | When search is declared | SearchSpaceManifest ID that declared the parameter space; absent for fixed-config runs |
| `trial_family_id` | string | When part of a family | Groups related trials (confirmatory, exploratory, follow-up) under a shared hypothesis |
| `trial_id` | string | When trial is recorded | TrialLedger entry ID for this specific run; absent for dry-run or smoke runs |
| `proposed_trial_id` | string | For dry-run proposals | Proposed `trial_id` for a future real execution, not yet written to TrialLedger |
| `variant_id` | string | When multiple variants run | Identifies this variant within a parameterized search space |
| `selected_variant_id` | string | When variant selection applies | ID of the selected variant after search; absent for fixed-config runs |
| `model_assessment_id` | string | When model assessment exists | ModelAssessmentSpec ID associated with this run or variant |
| `review_packet_id` | string | When reviewed | ReviewPacket ID produced by manual review of this RunnerOutput |

All IDs must reference existing governance artifact IDs. Forward references (e.g., a `proposed_trial_id` not yet in the ledger) are permitted for dry-run mode only.

---

## 3. Dry-Run Rule

**Dry-run mode may reference proposed IDs but must not mutate the TrialLedger or EdgeHypothesisRegistry.**

Rationale: A dry-run validates that the runner can construct a valid `RunnerOutput` artifact without executing any real trial. The dry-run path should be able to propose a `proposed_trial_id` and include it in the artifact for traceability, but it must not write to the ledger. The ledger is the system of record for what trials actually ran; off-book dry-runs that claim a `trial_id` they did not actually record are a governance violation.

Explicit rules for dry-run:

- `run_mode = dry_run` artifacts may carry `proposed_trial_id` and `experiment_id`.
- `run_mode = dry_run` artifacts must NOT carry a `trial_id` that references an actual TrialLedger entry.
- `run_mode = dry_run` artifacts must NOT emit `status = success` in a way that implies real execution occurred.
- `run_mode = dry_run` artifacts with `status = failed_validation` or `status = failed_missing_data` are acceptable and do not require ledger linkage.
- A `trial_id` in a non-dry-run artifact must match an existing `trial_id` in the TrialLedger.

---

## 4. Future Execution Rule

**Real execution must record every attempted variant or emit an explicit `not_applicable` reason for each unrecorded variant.**

Rationale: If a search runs 500 variants but only records 1 to the ledger, the unreported 499 are off-book trials. The search space is the authorization boundary; every variant that is evaluated against data must be either (a) recorded in the TrialLedger with its full metadata or (b) explicitly marked with a `not_applicable` reason (e.g., data unavailable, configuration invalid before execution).

Explicit rules for real execution:

- Each variant evaluation that produces a `RunnerOutput` must link to a unique `variant_id`.
- Each `variant_id` must appear in the TrialLedger or carry a documented `not_applicable` reason in the `RunnerOutput`.
- If a search terminates early (e.g., manual stop, resource limit), the partial set of evaluated variants must still be recorded or documented.
- The `all_variants_preserved` field (see Section 5) must be `true` for the TrialLedger entry to be considered complete.

---

## 5. Search Pressure Fields

Every `RunnerOutput` that originates from a parameterized search must carry the following fields to support overfitting diagnostics and PBO computation.

| Field | Type | Description |
|--------|------|-------------|
| `n_tried` | integer | Total number of variants attempted in this search |
| `candidate_variant_count` | integer | Number of variants that produced a valid RunnerOutput |
| `failed_variant_count` | integer | Number of variants that produced a failed or invalid output |
| `all_variants_preserved` | boolean | `true` if every attempted variant is recorded in the TrialLedger or has a `not_applicable` reason; `false` if some are missing |
| `sample_length` | integer | Length of the sample (e.g., number of events, rows, or time steps) used for this trial |
| `sample_to_trial_ratio` | float | Ratio of `sample_length` to `n_tried`; low values (e.g., < 10) indicate high overfitting risk |

The `sample_to_trial_ratio` is a crude but useful early warning. López de Prado's PBO estimator requires `n_trials >= 2^d` where `d` is the number of effective degrees of freedom; the ratio flag provides a simpler pre-registration signal. A ratio below 1:1 (fewer samples than trials) should trigger a mandatory `high_overfitting_risk` flag.

---

## 6. Complexity Fields

Every `RunnerOutput` that could be considered for promotion to review must carry complexity metadata to support strategy complexity haircuts and overfitting risk classification.

| Field | Type | Description |
|--------|------|-------------|
| `rule_count` | integer | Number of entry/exit/filter rules declared in the experiment configuration |
| `parameter_count` | integer | Total number of free parameters across all rules |
| `signal_count` | integer | Number of distinct signal or indicator computations performed |
| `filter_count` | integer | Number of filter or condition checks applied before a signal fires |
| `complexity_bucket` | enum | One of: `low`, `medium`, `high`, `excessive` |

Complexity bucket thresholds should be established empirically, but initial guidance:

- `low`: rule_count <= 3, parameter_count <= 5
- `medium`: rule_count 4–8, parameter_count 6–15
- `high`: rule_count 9–15, parameter_count 16–30
- `excessive`: rule_count > 15 or parameter_count > 30

The `excessive` bucket should block promotion to review without explicit senior reviewer sign-off. Complexity is a first-order overfitting risk indicator; strategies that are excessively complex relative to their sample are prime candidates for backtest overfitting.

---

## 7. Acceptance Gate

**No ReviewPacket should mark a candidate as review-ready without search-space and trial accounting metadata.**

Rationale: The ReviewPacket is the gate before a strategy can be considered for promotion or publication. If the ReviewPacket does not require linkage metadata, reviewers cannot verify that the candidate was authorized, that its trial count is consistent with the claims made, or that the sample-to-trial ratio is sufficient for the complexity of the strategy.

Required fields in a ReviewPacket before it can mark a candidate as `review_ready`:

- `runner_output_id` — must reference a valid RunnerOutput
- `trial_id` — must reference a TrialLedger entry or document `not_applicable`
- `search_space_id` — must be present for any search-mode experiment
- `experiment_id` — must match the ExperimentSpec used
- `complexity_bucket` — must be present and not `excessive` without senior sign-off
- `all_variants_preserved` — must be `true` or `not_applicable`; if `false`, the ReviewPacket must document the missing variants
- `sample_to_trial_ratio` — must be documented; if below threshold, a `high_overfitting_risk` flag must be present

If any required field is absent or fails its gate condition, the ReviewPacket must set `review_status = incomplete` with a specific deficiency reason.

---

## 8. Autonomous-Search Gate

**Bayesian optimization, genetic programming, and autonomous search remain locked until this linkage design plus DSR/PBO/CPCV support is implemented.**

Rationale: Autonomous search amplifies all five failure modes listed in Section 1. It generates trials at machine speed, each trial being a potential off-book execution. Without trial accounting linkage, autonomous search can produce thousands of unreported trials on the same dataset, creating massive selection bias and backtest overfitting. The only safe path to autonomous search is:

1. Full trial-accounting linkage (this design) — every trial ID recorded
2. DSR (Deflated Sharpe Ratio) or equivalent performance metric that corrects for multiple testing
3. PBO (Probability of Backtest Overfitting) — at minimum, a combinatorial purge cross-validation estimate
4. CPCV (Combinatorial Paired Cross-Validation) — as described in López de Prado and Erlinger (2020)

Current AED stop rules lock autonomous search. This design does not unlock it. It provides the accounting infrastructure that would make unlocking safer.

The following AED stop rules remain in force and are not modified by this design:

- No autonomous search
- No Bayesian optimization
- No genetic programming
- No automated promotion
- No automated registry mutation

---

## 9. Artifact Map

This section maps each governance artifact to its role in the trial-accounting linkage.

### RunnerOutput

`RunnerOutput` is the primary evidence artifact emitted by the runner. It is extended by this design to carry linkage fields (Section 2), search pressure fields (Section 5), and complexity fields (Section 6). The `RunnerOutput` must reference the `SearchSpaceManifest` that authorized the search, if any. It must not be promoted automatically to the TrialLedger; promotion requires a manual ReviewPacket.

Schema changes required (future PR): Add the fields in Sections 2, 5, and 6 to the `RunnerOutputSpec v1` schema.

### TrialLedger

`TrialLedger` is the append-only system of record for all trials that actually ran. Each `RunnerOutput` with `run_mode != dry_run` must have a corresponding `TrialLedger` entry. The ledger entry must include the linkage fields from Section 2 and the search pressure fields from Section 5. The ledger does not store the full `RunnerOutput` artifact — it stores a summary with references.

The ledger is read-only in the current AED stop rules. This design does not modify ledger write permissions.

### SearchSpaceManifest

`SearchSpaceManifest` declares the parameter space before any search begins. It is the authorization boundary: any variant that is evaluated must fall within the declared space. A `RunnerOutput` with `search_space_id` set must have that ID reference an existing `SearchSpaceManifest`. The manifest must be created before any trial in the search begins; retroactive creation is a governance violation.

The `SearchSpaceManifest` `candidate_count` field (declared upfront) is compared against the `n_tried` and `candidate_variant_count` in the `RunnerOutput` at review time. Discrepancies between declared and actual candidate counts must be documented.

### ModelAssessmentSpec

`ModelAssessmentSpec` declares the assessment framework for a strategy. The `model_assessment_id` in the `RunnerOutput` links the run to its assessment metadata. Assessment results (PBO estimates, DSR, complexity scores) are stored in the `ModelAssessmentSpec` and referenced by `RunnerOutput`.

The `ModelAssessmentSpec` schema should be extended in a future PR to include fields for PBO, DSR, and complexity_bucket results produced by the runner or by post-run analysis.

### ReviewPacket

`ReviewPacket` is the manual review artifact that approves or rejects promotion of a candidate. Section 7 defines the acceptance gate: the ReviewPacket must not mark a candidate as `review_ready` without the linkage and accounting fields specified. The ReviewPacket collects the full linkage chain: `runner_output_id` → `trial_id` → `search_space_id` → `experiment_id` → `model_assessment_id`.

---

## 10. How This Design Addresses Specific Failure Modes

### Unreported trials

The `all_variants_preserved` field requires that every attempted variant be either recorded in the TrialLedger or explicitly marked with a `not_applicable` reason. The `trial_id` in the `RunnerOutput` must match a ledger entry for non-dry-run runs. Without this, a runner that produces 500 outputs but only records 1 is detectable by comparing `candidate_variant_count` (from `RunnerOutput`) against ledger entries (from `TrialLedger`).

### Selection bias

The `sample_to_trial_ratio` field flags low-sample searches where only the best-performing variant is promoted. The ReviewPacket acceptance gate (Section 7) requires that `sample_to_trial_ratio` be documented and that a `high_overfitting_risk` flag be present when the ratio is low. The `all_variants_preserved` requirement prevents silent discarding of failed variants that might otherwise bias the reviewed set toward survivors.

### Backtest overfitting

The search pressure fields (`n_tried`, `candidate_variant_count`, `sample_length`, `sample_to_trial_ratio`) provide the inputs for PBO computation. The `SearchSpaceManifest` linkage provides the `d` (degrees of freedom) needed for the PBO formula. The `complexity_bucket` field ensures that overly complex strategies — which are more prone to overfitting — are flagged at review time. The autonomous-search gate (Section 8) locks Bayesian optimization and GP until PBO/DSR/CPCV support exists.

### Exploratory anomaly HARKing

The `trial_family_id` field groups exploratory and confirmatory trials. An exploratory trial that \"discovers\" an anomaly and then runs a confirmatory trial on the same data must be detectable by comparing `trial_family_id` and `source_lane` across the linked `TrialLedger` entries. The `hypothesis_id` in the `TrialLedger` entry provides an additional link to the original edge hypothesis. The ReviewPacket must verify that confirmatory trials are on independent or holdout data relative to the exploratory trial.

### Strategy complexity haircuts

The `complexity_bucket` field provides an explicit classification of strategy complexity. The `excessive` bucket blocks promotion without senior reviewer sign-off. The `rule_count`, `parameter_count`, `signal_count`, and `filter_count` fields provide fine-grained complexity detail that reviewers can use to assess whether the strategy complexity is justified by the sample size and the research question.

---

## 11. Relationship to First Thin Runner Milestone

This design extends the first thin runner milestone (PRs #159–#182). The first thin runner currently emits schema-valid `RunnerOutput` artifacts but does not carry trial-accounting linkage fields. The fields defined in this design are required for any AED instance that will:

- Run parameterized or search-mode experiments
- Produce multiple `RunnerOutput` artifacts for the same `experiment_id`
- Seek to promote any `RunnerOutput` to review
- Unlock autonomous search pathways in the future

The first thin runner in its current form is a fixed-configuration smoke test. It does not yet use `SearchSpaceManifest` or produce multi-variant runs. This design provides the contract that those future capabilities must implement.

---

## 12. Implementation Sequence

This design is prerequisite to the following future PRs:

1. **Schema update:** Add linkage, search pressure, and complexity fields to `RunnerOutputSpec v1`.
2. **TrialLedger update:** Extend `TrialLedger` entries to carry `search_space_id`, `variant_id`, `complexity_bucket`, and `all_variants_preserved`.
3. **SearchSpaceManifest update:** Add `candidate_count` comparison against `RunnerOutput.n_tried`.
4. **ReviewPacket update:** Add acceptance gate checks for linkage fields, complexity, and overfitting risk flags.
5. **ModelAssessmentSpec update:** Add fields for PBO, DSR, and complexity_bucket results.
6. **Runner implementation:** Wire linkage fields into the runner's `RunnerOutput` emission path.
7. **Autonomous search unlock:** Unlock Bayesian optimization and GP only after items 1–6 are complete and DSR/PBO/CPCV support exists.

---

## 13. Explicit Non-Scope for This PR

This PR does NOT include:

- **No code:** No Python, no engine/ changes, no runner implementation
- **No schema changes:** schemas/ is untouched
- **No test changes:** tests/ is untouched
- **No script changes:** scripts/ is untouched
- **No fixture changes:** fixtures/ is untouched
- **No CI workflow changes:** .github/workflows/ is untouched
- **No registry CSV changes:** docs/edge_hypothesis_registry.csv is untouched
- **No ledger writes:** TrialLedger is read-only per current AED stop rules
- **No autonomous search unlock:** Bayesian optimization, GP, and autonomous search remain locked
- **No implementation:** This is design only

---

## 14. Security and Data Safety

- **No trial ID forgery:** `trial_id` in a non-dry-run `RunnerOutput` must match an existing ledger entry. A mismatch is a governance violation, not a schema error.
- **No retroactive search space creation:** `search_space_id` must be assigned before any variant runs. Retroactive creation must be flagged by the validator.
- **No unreported variants:** `all_variants_preserved = false` without a documented `not_applicable` reason must block promotion.
- **No off-book autonomous search:** Autonomous search pathways are locked until Sections 1–12 of this design are implemented and DSR/PBO/CPCV support exists.
