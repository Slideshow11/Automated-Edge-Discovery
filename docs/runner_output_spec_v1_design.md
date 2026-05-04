# RunnerOutputSpec v1 Design

**Design date:** 2026-05-04
**PR:** #140
**Type:** Design only — no implementation

---

## 1. Purpose

RunnerOutputSpec v1 defines the durable output contract for an AED runner execution. It is the governance artifact that records what ran, what inputs were consumed, what outputs were produced, which audits passed or failed, and what the terminal status was.

RunnerOutputSpec is an **evidence artifact**, not a promotion artifact. It does not promote runner outputs to the TrialLedger, mutate the EdgeHypothesisRegistry, or trigger any downstream governance action. It records run accountability for future human review or automated audit.

RunnerOutputSpec is declared before any runner implementation exists. It defines the contract that future runner implementations must satisfy.

---

## 2. Relationship to AED Artifacts

### 2a. ExperimentSpec

ExperimentSpec declares the experiment structure, entry/exit modes, and stop rules. RunnerOutputSpec records which ExperimentSpec was used in a run:

```
RunnerOutputSpec.experiment_spec_ref → ExperimentSpec.experiment_spec_id
RunnerOutputSpec.input_artifact_refs contains the ExperimentSpec entry
```

RunnerOutputSpec does not replace ExperimentSpec. It affirms that ExperimentSpec was loaded and used.

### 2b. OutcomeSpec

OutcomeSpec declares outcome metrics and windows. RunnerOutputSpec records which OutcomeSpecs were used:

```
RunnerOutputSpec.outcome_spec_refs → OutcomeSpec.outcome_spec_id[]
```

RunnerOutputSpec does not compute outcome metrics. It records that OutcomeSpec was loaded and applied.

### 2c. InstrumentUniverseSpec

InstrumentUniverseSpec declares eligible instruments. RunnerOutputSpec records which InstrumentUniverseSpecs were used:

```
RunnerOutputSpec.instrument_universe_refs → InstrumentUniverseSpec.instrument_universe_id[]
```

### 2d. EventStudySpec

EventStudySpec declares event-alignment contracts. RunnerOutputSpec records which EventStudySpecs were used:

```
RunnerOutputSpec.event_study_spec_refs → EventStudySpec.event_study_spec_id[]
```

### 2e. OptionsEventRiskSpec

OptionsEventRiskSpec declares options-specific event-risk configuration. RunnerOutputSpec records which OptionsEventRiskSpecs were used:

```
RunnerOutputSpec.options_event_risk_refs → OptionsEventRiskSpec.options_event_risk_spec_id[]
```

### 2f. PreEarningsProfile

PreEarningsProfile declares pre-earnings-specific session semantics, DPE targeting, and IV crush policy. RunnerOutputSpec records which PreEarningsProfiles were used:

```
RunnerOutputSpec.preearnings_profile_refs → PreEarningsProfile.preearnings_profile_id[]
```

### 2g. DataManifest

DataManifest declares data sources, paths, and availability. RunnerOutputSpec records which DataManifests were used and which data paths were successfully resolved:

```
RunnerOutputSpec.data_manifest_refs → DataManifest[]
RunnerOutputSpec.missing_data_summary → list of unresolved or unavailable data refs
```

### 2h. SearchSpaceManifest

RunnerOutputSpec optionally references the SearchSpaceManifest used, if any:

```
RunnerOutputSpec.search_space_manifest_ref → SearchSpaceManifest.search_space_id
```

Note: The first thin runner slice does not use autonomous parameter search. SearchSpaceManifest refs appear in RunnerOutputSpec for completeness and future extensibility.

### 2i. TrialLedger

RunnerOutputSpec does NOT write to the TrialLedger. It is referenced by TrialLedger as a source of run evidence, not as a mutating agent:

```
TrialLedger.entry.run_output_ref → RunnerOutputSpec.runner_output_id
```

### 2j. ModelAssessmentSpec

ModelAssessmentSpec declares model assessment criteria. RunnerOutputSpec records which ModelAssessmentSpecs were loaded:

```
RunnerOutputSpec.model_assessment_refs → ModelAssessmentSpec.model_assessment_spec_id[]
```

RunnerOutputSpec does not compute ModelAssessment scores. It records that ModelAssessmentSpec was loaded.

### 2k. EdgeHypothesisRegistry

RunnerOutputSpec does NOT mutate the EdgeHypothesisRegistry. It is read-only with respect to the registry:

```
RunnerOutputSpec does not write to EdgeHypothesisRegistry
RunnerOutputSpec.artifact_refs may reference registry IDs for informational purposes only
```

### 2l. ReviewPacket

ReviewPacket is the manual review artifact that may be produced after a RunnerOutput is reviewed. RunnerOutputSpec records its own ReviewPacket ref once a review is complete:

```
RunnerOutputSpec.review_packet_refs → ReviewPacket.review_packet_id[]
```

RunnerOutputSpec does not create or approve ReviewPackets.

### 2m. Future Domain Profiles

RunnerOutputSpec is domain-neutral. It records domain profile refs generically:

```
RunnerOutputSpec.domain_profile_refs → domain_profile_id[]
RunnerOutputSpec.extension_hooks → domain-specific extension data
```

Future profiles (CryptoOptionsProfile, MacroEventProfile, SeasonalityProfile, ETFEventProfile, FuturesProfile) all fit within this same structure. No changes to RunnerOutputSpec are required to support new domain profiles.

---

## 3. Required Fields

The following fields are required in every RunnerOutput artifact, regardless of `status`:

```
runner_output_id: string     # Format: RUN-YYYY-NNNN (see §ID format justification)
runner_output_version: string # Fixed: "1.0"
run_id: string               # Deterministic run config hash (hex or base64)
run_mode: RunMode             # Enum — see Section 5
status: RunnerStatus         # Enum — see Section 5
runner_name: string          # Human-readable runner name (e.g., "aed-dry-run-validator")
runner_version: string        # Runner implementation version (e.g., "0.1.0")
experiment_spec_ref: string  # ExperimentSpec ID used for this run
input_artifact_refs: list[InputArtifactRef]  # All governance artifact refs used
data_manifest_refs: list[DataManifestRef]   # DataManifests used or attempted
run_config_hash: string       # Deterministic hash of fixed run configuration
started_at: ISO8601 timestamp # Wall-clock start of run
completed_at: ISO8601 timestamp | null  # Wall-clock completion; null if still running or failed early
audit_summary: AuditSummary  # Summary of all audit results; see Section 8
output_manifest: list[OutputManifestEntry]  # All output files produced; see Section 7
created_at: ISO8601 timestamp # When this RunnerOutput artifact was serialized
run_owner: string             # Declared at run invocation; not authenticated
```

### ID Format Justification: RUN-YYYY-NNNN vs RO-YYYY-NNNN

RunnerOutputSpec uses `RUN-YYYY-NNNN` (e.g., `RUN-2026-0042`) over `RO-YYYY-NNNN` for the following reasons:

1. **Semantic clarity:** "RUN" maps directly to the concept of a runner execution. "RO" is ambiguous — it could mean "report output," "result object," or "run output."
2. **Contrast with TrialLedger:** TrialLedger entries use `TRL-YYYY-NNNN`. RunnerOutput uses `RUN-YYYY-NNNN`. The different prefixes make it immediately clear which artifact type a reviewer is examining.
3. **Consistency with AED naming conventions:** AED artifact types use descriptive prefixes (TRL, SSM, EXP, OES, IUS, EVS, OER, PEP). "RUN" follows this pattern. "RO" does not.
4. **Alphabetical sortability:** RUN sorts adjacent to other R-prefixed artifacts (registry, review) without colliding with existing prefixes.

`YYYY` is the 4-digit year. `NNNN` is a zero-padded sequential integer, reset each year.

---

## 4. Optional Fields and Hooks

The following fields are optional and are included based on run mode, status, or runner configuration:

```
outcome_spec_refs: list[string]       # OutcomeSpec IDs used
instrument_universe_refs: list[string] # InstrumentUniverseSpec IDs used
event_study_spec_refs: list[string]    # EventStudySpec IDs used
options_event_risk_refs: list[string]   # OptionsEventRiskSpec IDs used
preearnings_profile_refs: list[string] # PreEarningsProfile IDs used
domain_profile_refs: list[string]      # Any domain profile IDs used (future profiles)

search_space_manifest_ref: string | null   # SearchSpaceManifest ID, if used
trial_ledger_ref: string | null             # TrialLedger entry ID, if promoted (future)
model_assessment_refs: list[string]         # ModelAssessmentSpec IDs used
review_packet_refs: list[string]            # ReviewPacket IDs, if reviewed (future)

failure_summary: FailureSummary | null   # Present when status is failed_* or cancelled
partial_summary: PartialSummary | null  # Present when status is partial; explains incomplete runs
missing_data_summary: list[MissingDataEntry] | null  # Present when data was unavailable
dropped_rows_summary: list[DroppedRowsEntry] | null  # Present when rows were dropped
leakage_checks_summary: LeakageChecksSummary | null  # Present for all status values

row_counts: RowCounts | null             # Present for success and partial runs
event_counts: EventCounts | null         # Present for success and partial runs
instrument_counts: InstrumentCounts | null  # Present for success and partial runs

execution_environment: ExecutionEnvironment | null  # Runner execution environment info
code_version_ref: string | null          # Version of runner code executed
git_commit: string | null                 # Git commit SHA of runner code
command_line: string | null               # Full command line invocation (secrets redacted)
output_paths: OutputPaths | null          # Top-level output directory or file paths
artifact_refs: list[ArtifactRef] | null  # Explicit artifact references produced

extension_hooks: map[string, any] | null # Domain-specific extensibility hook
notes: string | null                      # Free-text notes from run_owner or reviewer
reviewer: string | null                  # Set after manual review
```

---

## 5. Enums

### RunMode

```
dry_run             — Artifact validation only; no real data required or used
smoke_real_data    — Small real-data smoke test; limited scope, limited instruments
backtest_real_data — Full historical backtest on real data
simulation          — Synthetic or simulated data environment
replay              — Replay of a specific historical event window
custom              — Runner-defined custom mode; runner_name defines semantics
```

### RunnerStatus

```
success              — All stages completed; audit checks passed; results available
partial              — Run completed with warnings; some data was unavailable or some rows dropped; results present but limited
failed_missing_data  — Run halted before execution; required data was unavailable; RunnerOutput emitted with failure_summary populated
failed_validation    — Run halted before execution; governance artifact validation failed; RunnerOutput emitted with failure_summary populated
failed_runtime       — Run halted during execution; runtime error occurred after data was resolved
cancelled            — Run was explicitly cancelled before completion
```

### RunnerType

```
dry_run_validator  — Validates governance artifacts and produces validation report
smoke_runner      — Executes small smoke test on real or simulated data
backtest_runner   — Executes full backtest over declared instrument universe and date range
audit_runner      — Runs audit checks on existing RunnerOutput artifacts
custom            — Runner-defined type; semantics defined by runner_name
```

### OutputRole

```
evidence         — Primary result artifact (RunnerOutput)
audit_report     — Human-readable audit summary
failure_report   — Failure summary document (RunnerOutput with failure_summary populated)
intermediate     — Intermediate computation artifact (not primary output)
debug            — Debug artifact (may contain verbose or non-publishable data)
custom           — Runner-defined role
```

### FailureType

```
missing_data          — Required data was unavailable or unresolved
validation_error      — Governance artifact failed schema or validator check
runtime_error         — Runtime exception during execution
timeout               — Run exceeded declared or configured time limit
cancelled             — Run was explicitly cancelled
unsupported_config     — Runner does not support the declared configuration
custom                — Runner-defined failure type
```

### AuditResult

```
pass   — Audit check passed with no issues
fail   — Audit check failed; treated as blocker for success status
warn   — Audit check passed with warnings; not a blocker
skipped — Audit check was not applicable to this run configuration
```

---

## 6. Input Artifact Refs Structure

Each entry in `input_artifact_refs` identifies a governance artifact consumed by the run:

```
InputArtifactRef:
  artifact_type: string        # e.g., "ExperimentSpec", "OutcomeSpec", "PreEarningsProfile"
  artifact_id: string           # Artifact ID (e.g., "EXP-2026-0001")
  artifact_path: string | null  # Resolved path in local filesystem; null if from registry
  schema_ref: string            # Schema file path or registry ID used for validation
  validator_ref: string | null # Validator script path used; null if no validator run
  content_hash: string          # SHA-256 or equivalent hash of artifact content
  validation_status: AuditResult  # pass | fail | skipped | warn
  validated_at: ISO8601 timestamp | null  # When validation ran; null if not validated
```

The `validation_status` field records whether the artifact passed its AED validator during this run. An artifact may be present in `input_artifact_refs` even if `validation_status = fail` — in that case the run sets `status = failed_validation` and the RunnerOutput is populated with `failure_summary`.

---

## 7. Output Manifest Structure

Each entry in `output_manifest` describes a file or artifact produced by the run:

```
OutputManifestEntry:
  output_role: OutputRole       # evidence | audit_report | failure_report | intermediate | debug | custom
  output_path: string           # Relative or absolute path to output file
  row_count: int | null         # Number of data rows in this output file; null for non-row artifacts
  content_hash: string          # SHA-256 or equivalent hash of output file content
  created_at: ISO8601 timestamp # When this output file was written
  format: string                 # e.g., "json", "csv", "parquet", "yaml", "txt"
  description: string            # Human-readable description of this output
  contains_private_data: boolean  # true if output may contain PII, vendor data, or proprietary content
  publishable: boolean           # true only if contains_private_data = false and reviewer has approved
```

`publishable` defaults to `false`. An output file must not be treated as publishable unless explicitly approved by a reviewer.

---

## 8. Audit Summary Structure

```
AuditSummary:
  overall_result: AuditResult  # fail if any required audit is fail; pass if all pass; warn if any warn
  blocker_count: int          # Number of audit checks with result = fail
  warning_count: int           # Number of audit checks with result = warn
  audits: list[AuditEntry]     # One entry per audit check run
```

```
AuditEntry:
  audit_name: string           # e.g., "schema_validation_all_inputs", "no_lookahead_in_pre_event"
  audit_result: AuditResult   # pass | fail | warn | skipped
  severity: string            # "blocker" | "warning" | "info"
  blocker_count: int          # Number of failures within this audit
  warning_count: int          # Number of warnings within this audit
  details_ref: string | null   # Path or ID to detailed audit output; null if no details
  created_at: ISO8601 timestamp
```

The set of `audit_name` values is defined by the runner's audit check catalog. For the first thin runner slice, the audit names are those defined in `docs/first_thin_real_data_runner_slice_design.md` Section 8. Future runners may add additional audit names.

---

## 9. Failure Behavior

This section is consistent with `docs/first_thin_real_data_runner_slice_design.md` Stage i (audit gate) and Section 8 (audit checks).

### Single-Artifact Model

RunnerOutputSpec uses a **single artifact type** — `RunnerOutput` — with a `status` discriminator. There is no separate `FailureOutput` schema. A failed run produces a `RunnerOutput` with `status = failed_*` or `cancelled`, containing a `failure_summary` instead of `row_counts` / `event_counts` / `instrument_counts`.

This design is simpler than maintaining two parallel artifact types and makes it straightforward to write generic consumers that handle all statuses uniformly.

### Failure on `failed_validation` or `failed_missing_data`

When the runner halts before producing result rows:

- `RunnerOutput.runner_output_id` is assigned (sequential or hash-based; determinism is a runner concern).
- `RunnerOutput.status` is set to `failed_validation` or `failed_missing_data`.
- `RunnerOutput.failure_summary` is populated:
  ```
  FailureSummary:
    failure_type: FailureType  # validation_error | missing_data
    status: RunnerStatus        # Must match top-level status
    failed_check: string | null # The specific audit or validation check that failed
    blocker_summary: string     # Human-readable summary of what blocked the run
    input_artifact_refs: list[InputArtifactRef]  # All refs available at time of failure
    run_config_hash: string | null  # Computable if failure was late enough in pipeline
    failed_at: ISO8601 timestamp
  ```
- `RunnerOutput.row_counts`, `event_counts`, `instrument_counts` are `null`.
- `RunnerOutput.output_manifest` may contain partial outputs (e.g., a validation error report) if the runner wrote any artifacts before halting.
- No `TrialLedger` entry is created. No `EdgeHypothesisRegistry` mutation occurs. No promoted artifacts are emitted.

### Failure on `failed_runtime`

When the runner halts during execution after data has been resolved:

- All required fields are populated as normal.
- `RunnerOutput.status = failed_runtime`.
- `RunnerOutput.failure_summary` is populated with `failure_type = runtime_error`.
- `RunnerOutput.row_counts` may reflect partial row counts at time of failure.
- `RunnerOutput.completed_at` reflects the time of failure.

### Failure on `cancelled`

- `RunnerOutput.status = cancelled`.
- `RunnerOutput.failure_summary` is populated with `failure_type = cancelled`.
- `RunnerOutput.completed_at` reflects the cancellation time.

### Traceability Guarantee

Every run — success, partial, or failed — produces exactly one `RunnerOutput` artifact. The `runner_output_id` is assigned and the artifact is serialized before the run exits. There is no run that produces no artifact. This ensures full traceability for post-run auditing and review.

### Partial Runs (`status = partial`)

When a run completes with warnings but still produces result rows:

- `RunnerOutput.status = partial`.
- `RunnerOutput.partial_summary` is populated:
  ```
  PartialSummary:
    partial_reason: string            # Why the run is partial, e.g., "missing_data", "dropped_rows"
    completed_stages: list[string]    # Stages that completed before warnings
    incomplete_stages: list[string]   # Stages that were skipped or truncated
    affected_outputs: list[string]     # Output paths affected by warnings
    reconciliation_notes: string       # Free-text explanation of any reconciliation discrepancies
  ```
- `row_counts`, `event_counts`, `instrument_counts` are present but may not reconcile fully.
- `failure_summary` is `null` (partial is not a failure).
- No `TrialLedger` entry is created. No promoted artifacts are emitted.

---

## 10. Counts and Reconciliation

### RowCounts

```
RowCounts:
  total_observations: int           # All rows in primary output
  pre_event_evidence_rows: int      # Rows tagged as pre-event evidence window
  post_event_anchor_rows: int      # Rows tagged as post-event anchor
  dropped_rows: int                 # Rows dropped by inclusion/exclusion rules
  filtered_rows: int | null         # Rows filtered by BMO/AMC or DPE constraints
```

### EventCounts

```
EventCounts:
  total_events: int               # All earnings events in eligible universe
  events_with_options: int         # Events that had matching option contracts
  events_missing_data: int          # Events excluded due to missing data
  events_filtered: int | null       # Events filtered by DPE or session constraints
```

### InstrumentCounts

```
InstrumentCounts:
  total_instruments: int           # Instruments in eligible universe before filtering
  instruments_with_events: int     # Instruments that had at least one qualifying event
  instruments_filtered: int        # Instruments filtered by universe or liquidity rules
  instruments_missing_data: int    # Instruments excluded due to missing data
```

### Reconciliation Expectations

For `status = success`:
- `total_observations = pre_event_evidence_rows + post_event_anchor_rows + dropped_rows`
- `events_with_options ≤ total_events`
- `instruments_with_events ≤ total_instruments`

For `status = partial`:
- Reconciliation invariants may not hold if failure occurred mid-execution.
- `partial_summary.reconciliation_notes` should explain the discrepancy.

For `status = failed_*` or `cancelled`:
- `row_counts`, `event_counts`, `instrument_counts` are `null`.
- Reconciliation is not applicable.

### No Performance Statistics

RunnerOutputSpec does not compute PBO, DSR, Sharpe ratios, overfit discounts, win rates, or any statistical performance summary. Those computations — if desired — belong in a downstream ModelAssessmentSpec run or a ReviewPacket. RunnerOutputSpec records run metadata and audit outcomes only.

---

## 11. Leakage and Anti-Lookahead Checks

Leakage checks are the responsibility of the domain profile (e.g., PreEarningsProfile) and the EventStudySpec. RunnerOutputSpec records the outcomes:

```
LeakageChecksSummary:
  no_lookahead_in_pre_event: AuditResult  # pass | fail | warn | skipped
  post_event_rows_tagged: AuditResult      # pass | fail | warn | skipped
  no_gap_exit_for_pre_event: AuditResult  # pass | fail | warn | skipped
  data_timestamp_policy: AuditResult       # pass | fail | warn | skipped
  feature_cutoff_policy: AuditResult      # pass | fail | warn | skipped
  details_ref: string | null               # Path to detailed leakage report
```

RunnerOutputSpec records audit outcomes; it does not define event timing semantics. The semantics of pre-event vs. post-event, no-gap exit, and data_cutoff_timestamp are owned by EventStudySpec and the domain profile.

For the first thin runner slice (pre-earnings), the leakage checks verify:
- No pre-event evidence row uses data with `data_timestamp >= event_anchor` (no lookahead).
- All post-event rows are tagged as `post_event_anchor`.
- Pre-event evidence windows do not extend past the no-gap exit boundary.
- `data_timestamp` on every row satisfies the declared `data_cutoff_timestamp` policy.
- `feature_cutoff_timestamp` is respected for all feature columns.

---

## 12. Boundary: What RunnerOutputSpec Does Not Own

The following are explicitly out of scope for RunnerOutputSpec:

| Excluded Concept | Reason |
|---|---|
| PBO (Probability of Backtest Overfitting) | Performance assessment; belongs in ModelAssessmentSpec or ReviewPacket |
| DSR (Degree of Superstitious Reasoning) | Performance assessment; belongs in ModelAssessmentSpec |
| Sharpe ratio / risk-adjusted performance | Performance assessment; belongs in ModelAssessmentSpec |
| Overfit discount | Performance assessment; belongs in ModelAssessmentSpec |
| ModelAssessmentSpec final scoring | ModelAssessmentSpec owns its own output artifact |
| ReviewPacket decision | ReviewPacket owns its own output artifact |
| EdgeHypothesisRegistry mutation | RunnerOutput is read-only with respect to registry |
| TrialLedger mutation | Promotion requires explicit ReviewPacket approval; RunnerOutput does not self-promote |
| Automated promotion | Stop rule; RunnerOutput is an evidence artifact, not a promotion artifact |
| Live trading | Stop rule; RunnerOutput is never produced by a live trading system in this design |
| Production execution | Stop rule; RunnerOutput is an evidence artifact for research, not execution |
| Strategy selection | RunnerOutput records what ran; strategy selection belongs to ExperimentSpec or domain profile |
| Parameter search | The first thin runner slice uses fixed configurations; search is out of scope |
| Alpha claims | RunnerOutput is an evidence artifact; it makes no performance or alpha claims |

---

## 13. Examples

All examples are conceptual and abstract. No committed JSON fixtures are included in this design.

### Example A: dry_run with missing data

```
runner_output_id: RUN-2026-0017
runner_output_version: "1.0"
run_id: "a3f8c1d2e4b5..."
run_mode: dry_run
status: failed_missing_data
runner_name: aed-dry-run-validator
runner_version: "0.1.0"
experiment_spec_ref: EXP-2026-0001
input_artifact_refs: [
  { artifact_type: "ExperimentSpec", artifact_id: "EXP-2026-0001",
    validation_status: "pass" },
  { artifact_type: "PreEarningsProfile", artifact_id: "PEP-2026-0003",
    validation_status: "pass" }
]
data_manifest_refs: [
  { manifest_id: "DM-2026-0010", resolved: false,
    reason: "file not found: /data/earnings/2026-Q1.csv" }
]
run_config_hash: "c7d9e0f1..."
started_at: "2026-05-04T10:00:00Z"
completed_at: "2026-05-04T10:00:05Z"
audit_summary: {
  overall_result: fail,
  blocker_count: 1,
  warning_count: 0,
  audits: [
    { audit_name: "schema_validation_all_inputs", audit_result: pass,
      severity: "blocker", blocker_count: 0, warning_count: 0 },
    { audit_name: "no_unresolved_refs", audit_result: fail,
      severity: "blocker", blocker_count: 1, warning_count: 0 }
  ]
}
failure_summary: {
  failure_type: missing_data,
  status: failed_missing_data,
  failed_check: "no_unresolved_refs",
  blocker_summary: "DataManifest declares /data/earnings/2026-Q1.csv which does not exist",
  failed_at: "2026-05-04T10:00:05Z"
}
output_manifest: [
  { output_role: failure_report,
    output_path: "runs/RUN-2026-0017/failure_report.json",
    row_count: null,
    format: "json",
    description: "Missing data failure report",
    contains_private_data: false,
    publishable: true }
]
created_at: "2026-05-04T10:00:05Z"
run_owner: "researcher@Cambridge"
```

### Example B: failed_validation

```
runner_output_id: RUN-2026-0018
runner_output_version: "1.0"
run_id: "b4e1a2d3f5c6..."
run_mode: dry_run
status: failed_validation
runner_name: aed-dry-run-validator
experiment_spec_ref: EXP-2026-0002
input_artifact_refs: [
  { artifact_type: "PreEarningsProfile", artifact_id: "PEP-2026-0004",
    validation_status: fail, validated_at: "2026-05-04T11:00:01Z" }
]
run_config_hash: "d8f2b3c4..."
audit_summary: {
  overall_result: fail, blocker_count: 1, warning_count: 0,
  audits: [
    { audit_name: "schema_validation_all_inputs", audit_result: fail,
      severity: "blocker", blocker_count: 1 }
  ]
}
failure_summary: {
  failure_type: validation_error,
  status: failed_validation,
  failed_check: "schema_validation_all_inputs",
  blocker_summary: "PreEarningsProfile PEP-2026-0004 failed AED validator: session_anchor_policy value 'bm_announcement' not in allowed set",
  failed_at: "2026-05-04T11:00:02Z"
}
created_at: "2026-05-04T11:00:02Z"
run_owner: "researcher@Cambridge"
```

### Example C: small successful smoke_real_data output

```
runner_output_id: RUN-2026-0019
runner_output_version: "1.0"
run_id: "e5c3d4a6..."
run_mode: smoke_real_data
status: success
runner_name: aed-smoke-runner
experiment_spec_ref: EXP-2026-0003
input_artifact_refs: [
  { artifact_type: "ExperimentSpec", artifact_id: "EXP-2026-0003", validation_status: pass },
  { artifact_type: "PreEarningsProfile", artifact_id: "PEP-2026-0005", validation_status: pass },
  { artifact_type: "OutcomeSpec", artifact_id: "OES-2026-0007", validation_status: pass }
]
run_config_hash: "f6d4e5b7..."
started_at: "2026-05-04T12:00:00Z"
completed_at: "2026-05-04T12:03:22Z"
audit_summary: {
  overall_result: pass, blocker_count: 0, warning_count: 0,
  audits: [
    { audit_name: "schema_validation_all_inputs", audit_result: pass, severity: "blocker" },
    { audit_name: "no_lookahead_in_pre_event", audit_result: pass, severity: "blocker" },
    { audit_name: "deterministic_run_config_hash", audit_result: pass, severity: "blocker" },
    { audit_name: "row_counts_reconcile", audit_result: pass, severity: "blocker" }
  ]
}
row_counts: {
  total_observations: 84,
  pre_event_evidence_rows: 60,
  post_event_anchor_rows: 24,
  dropped_rows: 0
}
event_counts: {
  total_events: 7,
  events_with_options: 5,
  events_missing_data: 2
}
instrument_counts: {
  total_instruments: 8,
  instruments_with_events: 5,
  instruments_filtered: 1,
  instruments_missing_data: 2
}
output_manifest: [
  { output_role: evidence,
    output_path: "runs/RUN-2026-0019/runner_output.json",
    row_count: 1, format: "json",
    description: "Primary RunnerOutput artifact",
    contains_private_data: false, publishable: true },
  { output_role: audit_report,
    output_path: "runs/RUN-2026-0019/audit_summary.txt",
    row_count: null, format: "txt",
    description: "Human-readable audit summary",
    contains_private_data: false, publishable: true }
]
created_at: "2026-05-04T12:03:22Z"
run_owner: "researcher@Cambridge"
reviewer: null
```

---

## 14. Validation Roadmap

RunnerOutputSpec is design-only in this PR. Implementation follows the standard AED validator pattern:

1. **PR #141:** RunnerOutputSpec v1 schema — JSON schema for RunnerOutput using existing schema conventions
2. **PR #142:** RunnerOutputSpec v1 fixtures — valid and invalid fixtures for all terminal statuses and run modes
3. **PR #143:** RunnerOutputSpec v1 validator — local validator using existing AED validator pattern
4. **PR #144:** RunnerOutputSpec v1 validator tests — pytest suite following existing AED test conventions
5. **PR #145:** RunnerOutputSpec v1 CI wiring — add to governance-validators CI job
6. **PR #146:** Docs status cleanup — update current_project_status.md and README.md
7. **PR #147:** runner dry-run CLI skeleton — invoke RunnerOutputSpec validator as first runner stage
8. Subsequent PRs: real-data resolver skeleton → first smoke run → audit report fixtures

---

## 15. Stop Rules

All AED stop rules apply to RunnerOutputSpec and the runners that emit it:

| Stop Rule | Enforced in RunnerOutputSpec |
|---|---|
| `autonomous_search` disabled | `trial_generation_mode` in ExperimentSpec must not be `autonomous_search`; enforced by audit check |
| `bayesian_optimization` disabled | Not applicable to RunnerOutputSpec; enforced by ExperimentSpec audit check |
| `genetic_programming` disabled | Not applicable to RunnerOutputSpec; enforced by ExperimentSpec audit check |
| `automated_promotion` disabled | RunnerOutputSpec does not upsert to TrialLedger; promotion requires ReviewPacket approval |
| `automated_registry_mutation` disabled | RunnerOutputSpec does not write to EdgeHypothesisRegistry |
| `live_trading` disabled | RunnerOutputSpec is an evidence artifact for research; `run_mode = live_trading` is not defined |
| `production_execution` disabled | RunnerOutputSpec is an evidence artifact; `run_mode = production_execution` is not defined |
| `GCRU_integration` disabled | RunnerOutputSpec does not invoke GCRU; GCRU integration is future work |

---

## 16. Security and Data Safety

- **No secrets in output artifacts:** `command_line` must have secrets redacted before inclusion in RunnerOutput. Runners must not emit API keys, access tokens, or credentials to stdout, stderr, or RunnerOutput fields.
- **No API keys in logs:** Runner implementations must use environment variables or secret management for API credentials. Log output must not contain raw API keys.
- **Local absolute paths:** Absolute local filesystem paths in `output_paths`, `data_manifest_refs`, or `artifact_refs` may be included in RunnerOutput for traceability. However, these paths are considered private and should not be published or shared outside the local secure environment.
- **Vendor data not committed:** If options or earnings data is sourced from a vendor, RunnerOutput must not include raw vendor payloads. It may include derived hashes, summaries, or references.
- **`publishable = false` by default:** Every `OutputManifestEntry` has `publishable: false` by default. `publishable: true` requires both `contains_private_data = false` and explicit reviewer approval.
- **Private data in examples:** Conceptual examples in this document use synthetic ticker symbols and generic paths. No real instrument identifiers or proprietary data paths appear.
- **Output artifact hygiene:** RunnerOutput itself should not embed raw price data, full option chain snapshots, or proprietary vendor content. Row counts and summary statistics are acceptable; raw market data is not.

---

## 17. Domain-Neutrality Note

RunnerOutputSpec is designed to be domain-neutral. The first implementation is the pre-earnings thin runner slice, but the spec itself does not assume pre-earnings or options:

**Why pre-earnings first:** The first thin runner slice uses PreEarningsProfile because PreEarningsProfile v1 governance artifacts are complete and tested. This is a pragmatic sequencing decision, not a architectural constraint.

**Why RunnerOutputSpec remains domain-neutral:** The core RunnerOutputSpec fields (`runner_output_id`, `run_id`, `run_mode`, `status`, `experiment_spec_ref`, `input_artifact_refs`, `audit_summary`, `output_manifest`) are applicable to any domain. Domain-specific fields are carried via optional refs (`domain_profile_refs`, `preearnings_profile_refs`, `extension_hooks`) or are absent.

**Future domain slices that reuse RunnerOutputSpec:**
- **CryptoOptionsProfile** — Uses the same `ExperimentSpec` + `EventStudySpec` + domain-specific crypto profile structure. RunnerOutputSpec records the crypto profile ref instead of PreEarningsProfile ref. All other fields are identical.
- **MacroEventProfile** — Uses the same structure with macro event anchors instead of earnings anchors.
- **SeasonalityProfile** — Uses the same structure with calendar-based event windows.
- **FuturesProfile** — Uses the same structure with futures contract roll dates as event anchors.

No changes to RunnerOutputSpec are required to support these future profiles. A new domain profile spec is authored, the runner is configured with the new profile, and the same RunnerOutput artifact is produced.

**Extensibility:** The `extension_hooks: map[string, any]` field allows domain-specific runners to attach additional structured data without modifying the RunnerOutputSpec schema. Extension data is opaque to the core RunnerOutput validator and is intended for domain-specific downstream consumers (e.g., a crypto-specific review tool that reads `extension_hooks.on_chain_data_ref`).
