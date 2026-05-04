# First Thin Real-Data Runner Slice — Design

**Design date:** 2026-05-04
**PR:** #139
**Type:** Design only — no implementation

---

## 1. Purpose

This document defines the first thin real-data runner slice for AED. The slice is a minimal vertical cut that proves the completed AED governance artifacts can be wired together to produce actual runner outputs from real (or simulated-missing) data — without performing autonomous search, optimization, promotion, or live trading.

The slice proves the following capabilities:

- Governance artifacts (ExperimentSpec, OutcomeSpec, InstrumentUniverseSpec, EventStudySpec, OptionsEventRiskSpec, PreEarningsProfile) can be loaded and validated using existing AED validators.
- Data references in the experiment resolve cleanly, or fail with an explicit missing-data report.
- Event windows can be constructed for a small, fixed instrument set.
- The runner emits a RunnerOutput artifact with traceable references back to every input governance artifact.
- No strategy promotion, no automated registry mutation, no autonomous search, and no performance marketing occur.

This is a **wiring validation**, not an alpha discovery exercise. The goal is to confirm that governance artifacts mean something in the context of a real runner before larger parameter-search or autonomous workflows are unlocked.

---

## 2. Non-Goals

This slice explicitly does NOT attempt to:

- Discover alpha or generate trading signals
- Perform parameter optimization (Bayesian or otherwise)
- Conduct autonomous search over instrument universes
- Run genetic programming or evolutionary algorithms
- Execute live trading or production orders
- Perform automated promotion of runner outputs to the TrialLedger
- Mutate the EdgeHypothesisRegistry automatically
- Integrate GCRU (Governance Configuration Review Utility)
- Make benchmark performance claims
- Run broad universe scans
- Process ReviewPacket approval workflows
- Produce performance marketing narratives

---

## 3. Inputs

The runner slice consumes the following input artifacts:

| Artifact | Purpose | Source |
|---|---|---|
| `ExperimentSpec` | Declares the overall experiment structure, entry/exit modes, stop rules | `schemas/experiment_spec_v1.json` or named fixture |
| `OutcomeSpec` | Declares which outcome metric is computed over which window | `schemas/outcome_spec_v1.json` or named fixture |
| `InstrumentUniverseSpec` | Declares eligible instruments and liquidity rules | `schemas/instrument_universe_spec_v1.json` or named fixture |
| `EventStudySpec` | Declares event-alignment contract, window structures, leakage policies | `schemas/event_study_spec_v1.json` or named fixture |
| `OptionsEventRiskSpec` | Declares option contract selection, liquidity, pricing, gap exposure | `schemas/options_event_risk_spec_v1.json` or named fixture |
| `PreEarningsProfile` | Declares BMO/AMC session semantics, DPE targeting, IV crush policy | `schemas/preearnings_profile_v1.json` or named fixture |
| `DataManifest` | Declares data sources, paths, and availability (if present) | Local data lake or fixture manifest |
| `local data files` | Actual OHLCV, earnings dates, options quotes (EOD or EOP) | Local path refs in DataManifest |

All artifacts are read-only inputs. No artifact is created by the runner in this slice.

---

## 4. Minimal Run Configuration

The slice is intentionally tiny and deterministic:

| Parameter | Value |
|---|---|
| Instrument universe size | 3–10 liquid US equities with known earnings events |
| Date window | One historical quarter or 3–10 earnings events |
| Experiment profile IDs | Exactly 1 of each: ExperimentSpec, EventStudySpec, OptionsEventRiskSpec, PreEarningsProfile, OutcomeSpec, InstrumentUniverseSpec |
| DPE policy | Fixed DPE list (e.g., DPE = [3, 7, 14] days before earnings) — no search |
| Option selection rule | Fixed rule (e.g., nearest-delta ATM call or specific delta band) — no search |
| Exit rule | Fixed no-gap exit (e.g., DPE + N days, or earnings session close) — no search |
| Option contract type | EOD closes only; no intraday quotes in this slice |
| Trial generation mode | Single fixed profile, no parameter sweep |
| Run ID | Deterministic hash of fixed configuration |
| Run mode | `smoke_real_data` if data available; `dry_run` if data missing |

No random seeds, no parameter sweeps, no evolutionary operators, no acquisition functions.

---

## 5. Data Requirements and Fallback

### Expected Data Sources

- **Earnings dates:** US equity earnings announcement dates (BMO/AMC session tags).
- **OHLCV:** Daily close prices for underlying equities.
- **Options EOD:** End-of-day option price quotes with expiry, strike, implied volatility.
- **Reference:** Local data lake at paths declared in DataManifest.

### Required Columns (Abstract Level)

- `event_date`: earnings announcement date
- `session`: BMO | AMC
- `underlying_symbol`: ticker
- `expiry_date`: option expiry
- `strike_price`: option strike
- `option_type`: call | put
- `settlement_price`: closing price for option or underlying
- `implied_volatility`: IV if available
- `delta`, `gamma`, `theta`, `vega` (optional for this slice)

### Point-in-Time Requirement

All data used in pre-event evidence windows must have a `data_cutoff_timestamp` no later than the event anchor timestamp. The runner must verify this for every row.

### Anti-Lookahead Requirement

The runner must confirm that no row in a pre-event evidence window uses data with a timestamp that is on or after the event anchor. This is checked by the audit stage.

### Fallback: Missing Data

If local data is unavailable for the declared instruments or date range:

1. Runner sets `run_mode = dry_run`.
2. Runner emits a `missing_data_report` as part of the RunnerOutput.
3. `missing_data_report` lists every unresolved data ref, the declared path, and the reason (file not found, columns missing, date range out of scope).
4. No partial runner output is emitted unless the missing data is non-essential to the declared slice scope.
5. The dry-run path still validates all governance artifacts and confirms schema validity.

---

## 6. Runner Stages

The runner executes the following stages in order:

**Stage a. Load artifacts**
- Load all six governance artifacts (ExperimentSpec, OutcomeSpec, InstrumentUniverseSpec, EventStudySpec, OptionsEventRiskSpec, PreEarningsProfile).
- Load DataManifest if present; otherwise initialize empty manifest.
- Compute and record a deterministic `run_config_hash`.

**Stage b. Validate artifacts**
- Run each governance artifact through its existing AED validator:
  - `validate_experiment_spec.py`
  - `validate_outcome_spec.py`
  - `validate_instrument_universe_spec.py`
  - `validate_event_study_spec.py`
  - `validate_options_event_risk_spec.py`
  - `validate_preearnings_profile.py`
- Abort run on any validation failure; emit `status = failed_validation`.

**Stage c. Resolve data refs**
- For each `data_ref` in the experiment configuration, attempt to resolve to local file paths.
- If any required ref cannot be resolved: emit `missing_data_report`, set `status = failed_missing_data`, halt.
- If all refs resolve: proceed.

**Stage d. Construct eligible instrument universe**
- Apply `InstrumentUniverseSpec` inclusion/exclusion rules to the declared ticker list.
- Filter to instruments meeting liquidity thresholds declared in the spec.
- Produce an `eligible_instruments` list with instrument counts.

**Stage e. Load earnings events**
- Load earnings events from the resolved data for each eligible instrument.
- Apply BMO/AMC session tagging from `PreEarningsProfile`.
- Tag each event with its `dpe` (days to earnings) relative to the announcement session.

**Stage f. Apply DPE timing via PreEarningsProfile**
- Filter events to the declared DPE list (fixed, no search).
- Apply any BMO/AMC entry window constraints from `PreEarningsProfile`.
- Tag each filtered event with its DPE bucket.

**Stage g. Select option observations via OptionsEventRiskSpec**
- For each tagged event, apply the fixed option selection rule (e.g., nearest ATM call at fixed delta).
- Apply liquidity filters from `OptionsEventRiskSpec`.
- Record selected option contracts with expiry, strike, type.

**Stage h. Compute outcomes via OutcomeSpec**
- For each selected option observation, compute the declared outcome metric over the declared outcome window.
- The outcome metric is fixed by `OutcomeSpec` — no search, no alternate metric computation.
- Tag post-event anchor rows separately from pre-event evidence rows.

**Stage i. Emit RunnerOutput artifact**
- Serialize the RunnerOutput with all required fields (see Section 7).
- Write to local output path.
- Do not promote, register, or upsert the output to any ledger, registry, or production system.

**Stage j. Emit validation/audit report**
- Run all audit checks (see Section 8).
- Append audit results to the RunnerOutput.
- Emit a human-readable audit summary.

---

## 7. Runner Output Contract

The `RunnerOutput` artifact is a new artifact type emitted by the runner. Its schema is not implemented in this PR — this section defines the proposed contract for future implementation.

### Proposed Fields

```
runner_output_id: string  # Format: RUN-<YYYYMMDD>-<HASH8>
run_id: string            # Deterministic run config hash
experiment_spec_ref: string  # ExperimentSpec ID
input_artifact_refs: list[string]  # All governance artifact IDs used
data_manifest_refs: list[string]  # DataManifest IDs or paths used
profile_refs: list[string]  # PreEarningsProfile, OptionsEventRiskSpec IDs
run_mode: enum[dry_run, smoke_real_data]
status: enum[success, partial, failed_missing_data, failed_validation]
row_counts:
  total_observations: int
  pre_event_evidence_rows: int
  post_event_anchor_rows: int
  dropped_rows: int
event_counts:
  total_events: int
  events_with_options: int
  events_missing_data: int
instrument_counts:
  total_instruments: int
  instruments_with_events: int
  instruments_filtered: int
dropped_rows_summary: list[{reason: string, count: int}]
missing_data_summary: list[{data_ref: string, path: string, reason: string}]
leakage_checks_summary:
  pre_event_lookahead_detected: bool
  post_event_anchor_tagged: bool
  no_gap_exit_enforced: bool
output_paths:
  runner_output_file: string
  audit_report_file: string
  missing_data_report_file: string | null
created_at: ISO8601 timestamp
run_owner: string  # Declared at run invocation
reviewer: string | null  # Set if/when reviewed manually
```

The `RunnerOutput` is an evidence artifact, not a promoted strategy. It is not upserted to any ledger or registry in this slice.

---

## 8. Audit Checks

The runner performs the following audit checks on every run (success or dry-run):

| Check | Description | Enforced |
|---|---|---|
| `schema_validation_all_inputs` | Every input governance artifact passes its AED validator | Required |
| `no_unresolved_refs` | All declared data refs resolve to existing files or dry-run mode is active | Required |
| `no_lookahead_in_pre_event` | No row in any pre-event evidence window has `data_timestamp >= event_anchor` | Required |
| `post_event_rows_tagged` | All rows after the event anchor are tagged as `post_event_anchor` | Required |
| `no_gap_exit_for_pre_event` | Evidence windows do not extend past no-gap exit boundary for pre-event rows | Required |
| `deterministic_run_config_hash` | `run_config_hash` is reproducible for identical configuration | Required |
| `row_counts_reconcile` | `total_observations = pre_event + post_event + dropped` | Required |
| `no_root_additionalProperties` | All governance artifacts have no unexpected fields at root | Required |
| `no_registry_mutation` | No EdgeHypothesisRegistry or TrialLedger is written by the runner | Required |
| `no_autonomous_search_flag_set` | ExperimentSpec `trial_generation_mode` is not `autonomous_search` | Required |

Audit failures cause the runner to emit `status = failed_validation` and halt before producing any output artifact.

---

## 9. Stop Rules

All AED stop rules are enforced in this slice:

| Stop Rule | Status in This Slice |
|---|---|
| `autonomous_search` disabled | Enforced: `trial_generation_mode` is fixed to `confirmatory` or `theory_first`; `autonomous_search` is not permitted |
| `bayesian_optimization` disabled | Enforced: no acquisition functions, no surrogate models |
| `genetic_programming` disabled | Enforced: no evolutionary operators |
| `automated_promotion` disabled | Enforced: RunnerOutput is not upserted to any ledger or registry |
| `automated_registry_mutation` disabled | Enforced: EdgeHypothesisRegistry is read-only in this slice |
| `live_trading` disabled | Enforced: no order execution, no broker API calls |
| `production_execution` disabled | Enforced: `run_mode` is `smoke_real_data` or `dry_run` only |
| `GCRU_integration` disabled | Enforced: no GCRU calls in this slice |

The runner aborts if any governance artifact declares a prohibited `trial_generation_mode` or if any stop-rule-violating configuration is detected.

---

## 10. Relationship to Future AED System

This slice is a **thin vertical cut**, not the final runner architecture.

**What this slice proves:** Governance artifacts have runtime meaning. They can be loaded, validated, and connected to data and output artifacts in a controlled, traceable way.

**What this slice does not prove:** Scalability, autonomous discovery, parameter search, multi-domain support, or production reliability.

Future work may extend the system along these axes:

- **RunnerOutputSpec schema:** Formalize the RunnerOutput artifact with its own JSON schema, validator, and fixtures.
- **Real runner implementation:** Implement the runner stages as a CLI tool or Python package.
- **Data resolver abstraction:** Abstract data access behind a resolver interface that supports multiple data backends (local files, API, parquet, etc.).
- **Dataset adapters:** Adapters for crypto, macro, fixed income, and other non-options domains.
- **Broader domain profiles:** SeasonalityProfile, MacroRegimeProfile, CryptoOptionsProfile — each swaps the domain profile while reusing core governance artifacts.
- **Controlled search after governance unlock:** Autonomous search is unlocked only after trial accounting and manual review gate exist.
- **ReviewPacket integration:** After RunnerOutput is emitted, a manual review step produces a ReviewPacket that can approve or reject promotion.

The architecture remains domain-neutral. PreEarningsProfile is one domain profile; it is not the identity of AED.

---

## 11. Proposed PR Sequence After This Design

This design PR (#139) is followed by:

1. **PR #140:** RunnerOutputSpec v1 design — formalize the RunnerOutput artifact contract.
2. **PR #141:** RunnerOutputSpec v1 schema — JSON schema for RunnerOutput.
3. **PR #142:** RunnerOutputSpec v1 fixtures — valid and invalid fixtures.
4. **PR #143:** runner dry-run CLI skeleton — Python CLI with artifact loading and validation stages.
5. **PR #144:** real-data resolver skeleton — data resolver abstraction with local-file backend.
6. **PR #145:** first smoke run on tiny local sample — run the dry-run CLI on a real local dataset.
7. **PR #146:** audit report fixtures — fixtures for all audit check outcomes.
8. **PR #147:** docs update — update current_project_status.md and README.md with runner milestones.

---

## 12. Explicit Non-Scope for This PR

This PR does NOT include:

- **No code:** No Python, no engine/ changes, no runner implementation
- **No schema changes:** schemas/ is untouched
- **No script changes:** scripts/ is untouched
- **No test changes:** tests/ is untouched
- **No fixture changes:** fixtures/ is untouched
- **No CI workflow changes:** .github/workflows/ is untouched
- **No engine/ changes:** engine/ is untouched
- **No registry CSV changes:** docs/edge_hypothesis_registry.csv is untouched
- **No implementation:** This is design only

---

## 13. Security and Data Safety

- **No secrets in logs:** The runner must not emit API keys, access tokens, or credentials to stdout, stderr, or output artifacts.
- **No API keys committed:** No hard-coded API keys appear in any governance artifact or data manifest.
- **No vendor data committed:** If options or earnings data is sourced from a vendor, only local-derived summaries or hashes appear in output artifacts — never raw vendor payloads.
- **Local paths not hard-coded:** DataManifest uses relative paths or environment-variable references; absolute local paths are not committed into schemas.
- **Output artifact hygiene:** RunnerOutput and audit reports must not contain raw price data, personal data, or proprietary vendor content.
- **Read-only data access:** The runner only reads data; it does not write to data sources.

---

## 14. Design Tension Notes

### Why Pre-Earnings Despite Domain Neutrality

AED is domain-neutral. PreEarningsProfile is one domain specialization, not the system's identity. This slice uses pre-earnings because:

1. **Specs are complete:** PreEarningsProfile v1 (PRs #130–#137), OptionsEventRiskSpec v1 (PRs #119–#128), EventStudySpec v1 (PRs #112–#117), OutcomeSpec v1 (PRs #94–#102), InstrumentUniverseSpec v1 (PRs #104–#110), and ExperimentSpec v1 (PRs #78–#90) are all complete, tested, and CI-wired.
2. **Domain profiles are additive:** A future crypto or macro slice would reuse the same governance artifacts but swap the domain profile. The runner design is domain-neutral at the governance layer.
3. **Earnigns-specific wiring is well-specified:** BMO/AMC session semantics, DPE targeting, and IV crush are well-defined research problems that are easy to validate in a thin slice.

### Why Deterministic and Tiny

The first slice should be boring because:

1. **Failure modes are loud in small runs:** If wiring is broken, a 3-instrument run fails fast and clearly. A 500-instrument run may produce partial outputs that mask wiring bugs.
2. **Deterministic config makes debugging tractable:** No random search means the same run config always produces the same output, making failures reproducible.
3. **Claims are honest:** A tiny run makes no performance claims. It only validates that the governance artifacts can produce an output.

### Why Not First Slice for Other Domains

Crypto, macro, and fixed-income domain profiles do not yet have complete governance artifact sets in this repo. PreEarningsProfile is the only fully implemented domain specialization. Once RunnerOutputSpec and the runner skeleton exist, adding a crypto slice would follow the same pattern: complete the crypto domain profiles, then run the existing runner with a different profile.

### Scope of Claims

This slice produces evidence artifacts. The RunnerOutput does not claim:

- Strategy profitability
- Edge existence
- Sharpe ratio or risk-adjusted performance
- Benchmark outperformance

The only claim is: "the governance artifacts can be loaded, validated, and connected to data to produce a RunnerOutput."
