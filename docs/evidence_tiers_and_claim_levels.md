# Evidence Tiers and Claim Levels — Design

**Design date:** 2026-05-06
**PR:** #186
**Type:** Design only — no implementation

---

## 1. The Problem

AED must prevent false promotion without killing early ideas.

Trial accounting (PRs #184, #185) is necessary for preventing off-book overfitting, but it is not sufficient on its own. AED also needs a discovery-friendly policy: **weak or early results should be downgraded to the correct evidence tier, not discarded as rejected ideas.**

The failure mode this design addresses is the **rejection trap**: a system that treats any failed robustness test as grounds for discarding an idea, rather than as grounds for limiting the claim that may be made from it. This trap is particularly damaging in exploratory research where early anomalies are often wrong in their initial form but productive when correctly understood.

Trial accounting is not a rejection tool. It is a context and evidence-quality tool. It records what was tried, how hard it was tried, and what the evidence actually shows. The evidence tier system uses that record to decide what level of claim is appropriate.

---

## 2. The Core Rule

**Failed robustness does not delete an idea. It limits the claim that may be made from it.**

Consequences of this rule:

- An exploratory signal that fails a cost-stress test is not "rejected." It is labeled `cost_sensitive` and downgraded to the evidence tier that matches its robustness level.
- A candidate that fails PBO threshold is not "rejected." It is labeled `high_pbo_risk` and downgraded to `exploratory_only` until multiplicity correction is applied or sample is increased.
- A strategy with `complexity_bucket = excessive` is not "rejected." It is flagged `complexity_haircut_required` and its claim is reduced to the tier that matches its complexity-adjusted evidence quality.
- Missing theory is not grounds for rejection. It is grounds for labeling the idea `mechanism_unknown` and restricting claims to the `exploratory` tier until a plausible mechanism is stated.

The system rewards accurate claims about appropriate evidence, not maximal claims about insufficient evidence.

---

## 3. Evidence Tiers

Evidence tiers classify the strength and completeness of an idea's supporting evidence. Each tier has specific requirements and a defined claim level.

### Tier 0 — Captured Idea

**Claim level:** `captured`

A rough idea, market intuition, literature observation, or hypothesis sketch. No statistical burden. No rejection. No promotion.

Characteristics:
- Written as an edge hypothesis card, Slack note, paper margin note, or informal observation log
- No data has been run
- No search has been declared
- No trial has been recorded

Permitted:
- Informal discussion
- Literature review
- Preliminary data inspection

Not permitted:
- Any claim of robustness
- Any claim of edge
- Any TrialLedger entry

### Tier 1 — Exploratory Signal

**Claim level:** `exploratory`

A cheap backtest, anomaly scan, or sanity-check run. Trial count recorded. Not accepted. Not review-ready.

Characteristics:
- A run has been executed (dry-run or real)
- A `RunnerOutput` artifact exists
- `trial_accounting_summary` may be present but is not required
- `complexity_bucket` may be absent or `unknown`
- Basic sanity checks (schema validity, column presence) have passed
- No formal mechanism is stated
- No search space has been pre-declared

Permitted:
- Informal "we saw something interesting" claims
- Planning of a formal confirmatory study
- Literature review to build mechanism

Not permitted:
- Claims of statistical significance
- Claims of edge
- Claims of robustness
- Promotion to `candidate`

### Tier 2 — Candidate Worth Formal Testing

**Claim level:** `candidate`

A hypothesis with a stated mechanism or empirical rationale, a declared search space, implementation rules, and known failure modes listed.

Characteristics:
- `ExperimentSpec` has been created and linked
- Mechanism or empirical rationale is documented in the `ExperimentSpec` or a linked `MechanismDiscoveryReport`
- Search space has been declared via `SearchSpaceManifest`
- Implementation rules are explicitly enumerated
- Known failure modes are listed
- `trial_accounting_summary` is present with at least `status: proposed` or `status: linked`
- `complexity_bucket` is known and not `excessive` without senior sign-off
- `mutation_mode` is `dry_run_reference_only` or `no_mutation`

Permitted:
- Claims of "worth testing formally"
- Claims of "consistent with mechanism X"
- Planning of a formal backtest or confirmatory run

Not permitted:
- Claims of edge
- Claims of robustness
- Claims of PBO below threshold
- Claims of DSR above minimum
- Promotion to `robust_candidate` without further evidence

### Tier 3 — Robust Candidate

**Claim level:** `robust_candidate`

A candidate with leakage checks passed, cost sensitivity tested, and out-of-sample or pseudo-live evidence available.

Characteristics:
- Tier 2 requirements met
- Leakage checks have passed (no look-ahead, no survivorship bias, purge/embargo applied)
- Cost sensitivity tested across zero_cost, mid, mid_with_spread_penalty, conservative, stress
- OOS or pseudo-live evidence exists
- `trial_accounting_summary` is present with full linkage fields (`experiment_id`, `search_space_id`, `trial_id`, `variant_id`)
- `complexity_bucket` is `low` or `medium`; `high` requires documented justification
- `sample_to_trial_ratio` meets minimum threshold or `high_overfitting_risk` flag is documented
- `all_variants_preserved` is `true` or documented reason for incompleteness

Permitted:
- Claims of "leakage-check passing"
- Claims of "cost-sensitive across tested regimes"
- Claims of "out-of-sample consistent"
- Claims of "robust within declared search space"

Not permitted:
- Claims of edge
- Claims of deployment readiness
- Claims of "exceeds DSR minimum without documentation"
- Promotion to `review_ready` without `ModelAssessment` and `ReviewPacket`

### Tier 4 — Review-Ready Candidate

**Claim level:** `review_ready`

A robust candidate with `ModelAssessment` and `ReviewPacket` present, all variants preserved or explained, requiring human review before any promotion.

Characteristics:
- Tier 3 requirements met
- `ModelAssessmentSpec` is present with PBO, DSR, and complexity_bucket results
- `ReviewPacket` is present
- All variants are preserved or documented missing with `not_applicable` reasons
- Trial accounting linkage is complete (no proposed-only IDs; all trial IDs reference real ledger entries)
- `acceptance_gate_satisfied: true` in `trial_accounting_summary`
- `autonomous_search_gate_satisfied` is `false` or documented

Permitted:
- Submission for human review
- Claims of "review-ready" with full linkage chain documented
- Claims of "meets acceptance gate"

Not permitted:
- Automatic promotion
- Registry mutation without manual review
- Deployment
- Claims of edge without human sign-off

### Tier 5 — Deployment Candidate

**Claim level:** `deployable`

Out of scope for current AED. Requires separate production, broker, risk, and human approval gates beyond the current AED scope.

Characteristics:
- Human review has been completed with explicit sign-off
- Production infrastructure, broker connectivity, and risk controls are out of AED scope
- AED does not emit deployment-ready artifacts in v1

Not permitted in AED v1:
- Any automated deployment path
- Any broker API integration
- Any live trading flag

---

## 4. Claim Levels

The claim level is the machine-readable label that determines what a given artifact may claim. It is derived from the evidence tier.

| Claim level | Evidence tier | Permitted claims | Promotion path |
|---|---|---|---|
| `captured` | Tier 0 | None — informal only | Informal |
| `exploratory` | Tier 1 | "Signal observed; worth investigating" | To Tier 2 with mechanism + search space declaration |
| `candidate` | Tier 2 | "Worth formal testing; mechanism plausible" | To Tier 3 with leakage checks + OOS evidence |
| `robust_candidate` | Tier 3 | "Passes leakage checks; cost-sensitive; OOS consistent" | To Tier 4 with ModelAssessment + ReviewPacket |
| `review_ready` | Tier 4 | "Meets acceptance gate; human review required" | Manual review only |
| `deployable` | Tier 5 | Out of AED scope | N/A |

---

## 5. Gate Requirements by Claim Level

Each claim level has mandatory gate requirements. Lower tiers are intentionally permissive; higher tiers are strict.

### Gate: `captured`

- None. Informal idea capture.

### Gate: `exploratory`

- `RunnerOutput` artifact exists and is schema-valid
- `run_mode` is declared
- `trial_accounting_summary.status` is not required; if present, may be `not_applicable`
- `complexity_bucket` is not required
- No `ModelAssessmentSpec` required
- No `ReviewPacket` required
- No ledger entry required

### Gate: `candidate`

- `ExperimentSpec` exists and is linked
- `SearchSpaceManifest` exists and is linked
- Mechanism or empirical rationale is documented
- `trial_accounting_summary` is present with `status` in `[proposed, linked]`
- `mutation_mode` in `[no_mutation, dry_run_reference_only]`
- `complexity_bucket` is not `excessive` without documented senior sign-off
- No ledger mutation

### Gate: `robust_candidate`

- All `candidate` gates satisfied
- Leakage checks documented and passed (or explicitly flagged as not applicable)
- Cost sensitivity results exist across at least `[zero_cost, mid, conservative]`
- OOS or pseudo-live evidence documented
- `trial_accounting_summary` contains full linkage: `experiment_id`, `search_space_id`, `trial_id`
- `variant_id` present if search mode
- `sample_to_trial_ratio` above threshold or `high_overfitting_risk` flag documented
- `all_variants_preserved` is `true` or missing variants documented with `not_applicable` reasons
- `ModelAssessmentSpec` exists (may be partial v1)

### Gate: `review_ready`

- All `robust_candidate` gates satisfied
- `ModelAssessmentSpec` present with PBO and DSR results documented
- `ReviewPacket` present with explicit reviewer rationale
- `trial_accounting_summary.acceptance_gate_satisfied: true`
- `trial_accounting_summary.autonomous_search_gate_satisfied: false`
- No unreported variants
- All linkage IDs reference real (not proposed-only) ledger entries

### Gate: `deployable`

- Out of AED v1 scope

---

## 6. Required Result Preservation

Every `RunnerOutput` that passes a backtest must preserve multiple result variants across cost assumptions. AED must not silently replace raw results with harsh cost results. The degradation across cost assumptions must be visible.

Required result fields to preserve:

| Result field | Description |
|---|---|
| `raw_result` | Unadjusted backtest return or metric |
| `cost_adjusted_result` | Transaction cost applied at zero_cost assumption |
| `stress_cost_result` | Worst-case or high-spread cost assumption |
| `trial_adjusted_result` | multiplicity-corrected result (when PBO available) |
| `regime_split_result` | Results split by market regime (when applicable) |
| `liquidity_adjusted_result` | Liquidity penalty applied (when applicable) |
| `final_review_result` | Reviewer's annotated result with notes |

Policy: The result degradation path (raw → cost_adjusted → stress_cost → trial_adjusted) must be visible in the `ModelAssessmentSpec` artifact. Reviewers must be able to see at what cost assumption the result first becomes marginal or negative.

---

## 7. Downgrade Semantics

Downgrade means: keep the idea, reduce the claim level. The idea is not deleted.

| Failure | Interpretation | Action | New claim level |
|---|---|---|---|
| Failed robustness test | Evidence insufficient for current claim | Downgrade | Match evidence quality |
| Failed cost stress | Cost-sensitive | Downgrade to cost-adjusted result | `exploratory` or `candidate` |
| High PBO (> threshold) | Contaminated by overfitting | Flag `high_pbo_risk`; require multiplicity correction | `exploratory` or `candidate` |
| Missing mechanism | No stated rationale | Downgrade to `exploratory` | `exploratory` |
| Excessive complexity | High overfitting risk | Flag `complexity_haircut_required`; reduce claim | Match complexity-adjusted tier |
| Insufficient sample | Low `sample_to_trial_ratio` | Flag `high_overfitting_risk` | `exploratory` |
| Unreported variants | Selection bias risk | Require `all_variants_preserved: true` or documented | Cannot advance until resolved |
| No OOS evidence | In-sample only | Downgrade to `candidate` | `candidate` |

The downgrade is not a rejection. The idea remains visible in the ledger with the correct evidence tier label.

---

## 8. Rejection Semantics

Rejection is reserved for ideas that are fundamentally invalid and should not be revisited without a material change.

**Rejection** should be used only for:

| Rejection reason | Rationale |
|---|---|
| Data leakage confirmed and not repairable | Irreversibly contaminated evidence |
| Invalid data provenance | Cannot be remediated |
| Impossible execution | Rules cannot be executed in any market |
| Falsified mechanism | Stated mechanism is contradicted by known economics |
| Duplicate of known failed idea | Wastes trial budget |
| Governance violation | Policy breach requiring rejection |

**Not rejection; use downgrade instead:**

- `archived` — idea paused, may be revisited with new evidence
- `needs_retest` — failed current test, valid with different sample or config
- `exploratory_only` — failed confirmatory gate, still useful as exploratory signal
- `insufficient_evidence` — too few trials, data insufficient, needs more work
- `downgraded` — failed a gate at the current tier but passed a lower tier gate

---

## 9. Interaction with Trial Accounting

Trial accounting (PRs #184, #185) records search pressure. Evidence tiers determine what claim may be made from the recorded evidence. These two systems are complementary and must be used together.

Rules:

- **Raw exploratory evidence remains visible.** A `RunnerOutput` with `status: exploratory` remains in the ledger and is not automatically deleted or hidden because a later confirmatory run failed.
- **Adjusted acceptance evidence controls promotion.** Only `trial_accounting_summary` with `acceptance_gate_satisfied: true` may advance a candidate to `review_ready`.
- **No ReviewPacket may mark `review_ready` without trial accounting.** The ReviewPacket acceptance gate (Section 7 of PR #184 design) requires linkage fields and complexity metadata. An exploratory artifact without trial accounting cannot be promoted beyond `exploratory`.
- **Trial burden carries forward if promoted.** If an exploratory idea is later promoted to `candidate` or higher, the original exploratory run count is recorded in `trial_accounting_summary.n_tried` for the promoted run.
- **Search pressure fields are tier-diagnostic.** High `n_tried` with low `candidate_variant_count` indicates heavy selection bias risk. This must be flagged in the `ModelAssessmentSpec` and visible to reviewers.

---

## 10. Cost-Model Policy

AED must not silently replace raw results with harsh cost results. The degradation path must be explicit and visible to reviewers.

**Policy:** For every backtest result, AED must show the result degradation across cost assumptions:

```
raw_result
  → cost_adjusted_result (zero_cost)
  → cost_adjusted_result (mid)
  → cost_adjusted_result (mid_with_spread_penalty)
  → cost_adjusted_result (conservative)
  → cost_adjusted_result (stress)
```

Reviewers must be able to see at which cost level the result first becomes negative or marginal. An idea that is strong at `zero_cost` but fails at `conservative` must not be promoted to `robust_candidate` without explicit documentation of the cost sensitivity.

The `final_review_result` field in the `ReviewPacket` must document the reviewer's assessment of the cost-sensitivity degradation path.

---

## 11. Autonomous-Search Implication

**Autonomous search remains locked.** This design does not unlock it.

Exploratory searches must remain bounded and labeled `exploratory`. An exploratory run that generates 1,000 variants without pre-declared search space and without trial accounting linkage is an off-book execution, regardless of the results.

Exploratory trial burden must carry forward if the idea is later promoted. If an exploratory search tries 100 variants and then a formal confirmatory run is declared, the total trial burden (100 exploratory + confirmatory) must be reflected in the `trial_accounting_summary` for the confirmatory run.

All AED stop rules remain in force:

- No autonomous search
- No Bayesian optimization
- No genetic programming
- No automatic registry mutation
- No automated promotion
- No live trading
- No production execution

---

## 12. Current Implementation Status

This section records the current implementation state of the concepts in this design.

| Concept | Status |
|---|---|
| Evidence tier definitions | Design only (this doc) |
| Claim level definitions | Design only (this doc) |
| Tier-to-claim-level mapping | Design only (this doc) |
| Gate requirements by tier | Design only (this doc) |
| Result preservation fields | Design only (this doc) |
| Downgrade semantics | Design only (this doc) |
| Rejection semantics | Design only (this doc) |
| Trial-accounting interaction | Schema support added in PR #185; no runner emission yet |
| Cost-model policy | Design only |
| `ModelAssessmentSpec` schema | Implemented (PRs #63, #64) |
| `ReviewPacket` schema | Deferred; requirements baseline in PR #81 |
| `trial_accounting_summary` schema | Implemented (PR #185); not yet wired in runner |
| Runner emission of trial accounting | Not yet implemented |
| `acceptance_gate_satisfied` field | Schema field present (PR #185); gate logic not implemented |
| `autonomous_search_gate_satisfied` field | Schema field present (PR #185); gate logic not implemented |
| Registry mutation | Prohibited; append-only manual v1 only |
| Ledger mutation | Prohibited in current AED stop rules |
| Autonomous search | Locked via AED stop rules |

This design does not modify any schema, code, or stop rules. It is a design note that defines the evidence tier and claim-level policy that implementation must respect when it reaches the relevant milestones.
