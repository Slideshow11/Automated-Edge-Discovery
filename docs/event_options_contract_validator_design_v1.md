# Event/Options contract validator design v1

## 1. Purpose

This document is a design-only specification for the Event/Options contract validator v1. It is intentionally docs-only: it defines the future validator's behavior, invariants, validation phases, expected fixture coverage, error categories, and a test strategy — but it does not implement any code, JSON schemas, runtime artifacts, tests, or fixture edits.

> **Status note (post-PRs #50–#55):** Implementation now exists. The validator is at `scripts/local/validate_event_options_contract.py` and is wired into CI via `scripts/ci/validate_event_options_contract.sh` (PRs #50–#55). The design doc below is retained as historical context for the rationale behind the validator's behavior. All historical design-PR-only language about not yet implementing a validator was accurate at the time and is now superseded.

The validator design targets records described by docs/event_options_contract_spec_v1.md and references fixtures under fixtures/event_options_contract_v1 as canonical test inputs. The design supports future schema and validator work while preserving manual review, non-production status, and the AED stop-rules.

## 2. Source documents and fixtures

Primary source documents and fixtures to reference when implementing the validator:

- docs/event_options_contract_spec_v1.md
- docs/event_options_schema_planning_v1.md
- docs/model_assessment_spec_v1.md
- docs/trial_ledger_search_space_manifest_v1.md
- fixtures/event_options_contract_v1/README.md
- fixtures/event_options_contract_v1/valid_events_minimal.csv
- fixtures/event_options_contract_v1/valid_options_observations_minimal.csv
- fixtures/event_options_contract_v1/invalid_events_examples.csv
- fixtures/event_options_contract_v1/invalid_options_observations_examples.csv

Implementation note (docs-only): do not edit fixtures/event_options_contract_v1/* in this PR.

## 3. Validator inputs

Future validator inputs (grouped):

- Event records (CSV v1 fixtures)
- Options observation records (CSV v1 fixtures)
- Optional review metadata (free-text fields supplied by reviewers)
- Optional TrialLedger references (trial identifiers, audit metadata)
- Optional SearchSpaceManifest references (declared search parameters)
- Optional ModelAssessmentSpec references (linkage to model assessment artifacts)

State and constraints:
- v1 validator SHOULD accept CSV fixtures first (fixtures above). JSONL/YAML support is deferred.
- Registry mutation and automated promotion are out of scope for v1 validator design.

### Validation profiles

To avoid forcing current fixtures to conform immediately to the full canonical contract, the design distinguishes two validation profiles:

- strict_contract_profile:
  - Future full contract validation.
  - Expects canonical field names exactly as defined in docs/event_options_contract_spec_v1.md.
  - Intended for full production records and formal JSON Schema validation.

- minimal_fixture_profile:
  - Current fixture compatibility profile.
  - Used for fixtures/event_options_contract_v1/*_minimal.csv to validate the intentionally small fixture set.
  - May normalize fixture header aliases (see Fixture alias mapping) before applying semantic checks.

Notes:
- Implementations SHOULD support minimal_fixture_profile to validate existing fixtures. The strict_contract_profile represents the eventual canonical enforcement.
- Registry mutation and automated promotion remain out of scope.

## 4. Event record validation

Required EventRecord checks (v1):

- event_id required
- event_type required
- event_date required
- event_time_utc required
- event_session required
- event_timestamp_quality required
- calendar_id required
- timezone required
- event_source required
- point_in_time_policy required

Allowed event_session values (explicit):
- BMO
- AMC
- INTRA
- UNKNOWN

Allowed event_timestamp_quality values:
- exact
- date_only
- estimated
- unknown

Checks and semantics:
- event_id must be non-empty and unique within an EventRecord set.
- event_time_utc must parse as a UTC timestamp (ISO 8601 preferred).
- event_date must match or be consistent with the event_time_utc date when resolved under the declared timezone and calendar policy.
- UNKNOWN event_session is allowed for raw ingestion but should be flagged and blocks advancement/promotion until reviewed.
- unknown or weak timestamp quality (estimated, date_only, unknown) should be surfaced as warnings; strong enforcement may block advancement depending on review context.

## 5. Options observation validation

Required OptionsObservationSpec checks (v1):

- option_observation_id required
- option_contract_symbol required
- option_observation_date required
- event_id required
- event_time_utc required
- option_type required (call/put)
- option_expiry required
- expiry_covers_event required
- event_hold_flag required
- gap_exposure required
- fill_model required
- quote_timestamp required or explicitly absent under a documented stale_quote_policy
- stale_quote_policy required
- spread_metric required
- liquidity_metric required

Allowed event_hold_flag values:
- no_event_hold
- partial_event_hold
- full_event_hold
- unknown_event_hold

Allowed gap_exposure values:
- none
- partial
- full
- unknown

Checks and semantics:
- event_id must link to a valid EventRecord (exists and is resolvable).
- Observations without event_id are invalid for event-cohort research and must be treated as ingestion-only or flagged for manual review.
- event identity is the canonical cohort and join key; option_observation_date alone must not be used to define cohorts for event-cohort analyses.
- expiry_covers_event must be boolean-like; when true, option_expiry must be on or after the relevant event_date (inclusive policy documented in spec).
- unknown_event_hold should block advancement; unknown gap_exposure should block promotion or advancement until clarified.

## 6. Anti-lookahead validation

Corrected invariant (design):

Decision-time feature timestamps must be <= the applicable decision timestamp.

Design requirements:
- Future validators MUST reject any feature or annotation whose timestamp is after the applicable decision timestamp.
- Checks to include:
  - feature_timestamp <= decision_timestamp
  - data_cutoff_timestamp <= decision_timestamp
  - event_time_utc should not be assumed to be the decision timestamp for every record; the applicable decision timestamp must be explicit or derivable from point_in_time_policy.
  - option observations whose quote or feature timestamps are after the decision timestamp cannot be used as decision-time features.
  - event_id linkage must not introduce future information (no implicit backfill of decision-time features using post-decision data).

- Future validators must reject any feature whose timestamp is after the applicable decision timestamp

## 7. Event identity and cohort validation

State and rules:
- Cohorts are selected by event identity or event date.
- Cohorts are selected by event identity, not by raw option observation date alone.
- event_id is the canonical event join key.
- Multiple option observations may link to one event_id (many-to-one).
- Event cohort membership must be reproducible from EventRecord fields alone.

Checks:
- every valid option observation's event_id must exist in the EventRecord set
- no blank event_id in valid observations for event-cohort research
- duplicate event_id handling must be deterministic and documented (e.g., first-seen or explicit dedupe rule)
- invalid fixture rows should include missing_event_id or unknown_event_id examples for testing

## 8. Trading calendar and session validation

State and design guidance:
- No calendar-day approximation for trading-day windows: validators should rely on trading-calendar-aware logic (deferred to runtime implementations).
- Docs-only validator design defines expected behavior; runtime validators must use a proper trading calendar.

Checks:
- event_session must be explicit and one of the allowed enumerations.
- BMO and AMC must not be pooled without stratification; validations should flag pooling as a risk.
- UNKNOWN event_session blocks advancement unless sensitivity analysis or documented handling is present.
- trading calendar identifier (calendar_id) must be present.
- timezone must be present and used to reconcile event_time_utc vs event_date.
- session_anchor or explicit timing policy must be present if entry/exit timing is represented.

## 9. Advancement blockers

Blocker categories (explicit):

- missing_required_field
- invalid_enum
- bad_timestamp_order
- future_feature_timestamp
- missing_event_link
- invalid_event_link
- unknown_event_session
- unknown_event_hold
- unknown_gap_exposure
- raw_option_date_cohorting
- calendar_day_approximation
- missing_point_in_time_policy
- missing_stale_quote_policy
- missing_fill_model
- missing_spread_or_liquidity_metric

State and semantics:
- Blockers prevent advancement or promotion of artifacts but do not automatically translate to accepted/rejected/killed lifecycle actions.
- Manual review remains required to adjudicate blockers.

## 10. Warning categories

Warnings (non-blocking by default):

- weak_timestamp_quality
- estimated_event_time
- date_only_event_time
- low_liquidity
- wide_spread
- stale_quote_risk
- missing_optional_review_refs
- incomplete_trial_refs
- incomplete_search_space_refs

State:
- Warnings should not automatically block raw ingestion.
- Warnings may be escalated to blockers by manual review depending on context.

## 11. Fixture mapping

How existing fixtures map to expected validator outcomes (profiles and aliasing):

Minimal fixture compatibility profile (minimal_fixture_profile):

- fixtures/event_options_contract_v1/valid_events_minimal.csv
  - should pass minimal_fixture_profile EventRecord validation after normalization (see alias mapping below)
  - valid_events_minimal.csv should pass minimal_fixture_profile

- fixtures/event_options_contract_v1/valid_options_observations_minimal.csv
  - should pass minimal_fixture_profile OptionsObservationSpec validation and event_id linkage after normalization
  - valid_options_observations_minimal.csv should pass minimal_fixture_profile

- fixtures/event_options_contract_v1/invalid_events_examples.csv
  - should fail minimal_fixture_profile EventRecord checks (missing fields, bad timestamps, invalid enums)

- fixtures/event_options_contract_v1/invalid_options_observations_examples.csv
  - should fail minimal_fixture_profile OptionsObservationSpec checks (missing event_id, invalid event link, unknown gap_exposure, unknown event_hold, timestamp anomalies)

State:
- This PR does NOT edit fixtures. Future fixture PRs may add more edge cases. Fixtures must remain deterministic for tests.

### Fixture alias mapping (minimal_fixture_profile)

To accommodate the current, intentionally-small fixture headers, the minimal_fixture_profile MAY apply a deterministic header alias mapping before running semantic checks. Alias mapping is for fixture compatibility only; canonical records should use the contract names from docs/event_options_contract_spec_v1.md.

Example alias mappings (apply in normalization step):

- event_time -> event_time_utc
- option_symbol -> option_contract_symbol
- observation_date -> option_observation_date

Notes:
- Alias mapping is ONLY for fixture compatibility under minimal_fixture_profile.
- strict_contract_profile requires canonical field names; normalization is not applied under strict profile.
- The current PR does not edit fixtures; future fixture expansion should add full canonical examples when available.

## 12. Future validator execution plan (CLI design, illustrative only)

Illustrative CLI (no script added in this PR):

python3 scripts/local/validate_event_options_contract.py \
  --events fixtures/event_options_contract_v1/valid_events_minimal.csv \
  --options fixtures/event_options_contract_v1/valid_options_observations_minimal.csv \
  --format json

Possible flags (illustrative):
- --strict         # fail on warnings
- --allow-warnings # treat warnings as non-fatal
- --format json    # machine-readable output
- --contract-version v1

Note: the filename validate_event_options_contract.py is referenced here as the intended future CLI shape; no script is added in this PR.

## 13. Relationship to AED artifacts

- TrialLedger:
  - Validation failures, runs, and audit artifacts may be recorded as TrialLedger entries or audit metadata in future work.

- SearchSpaceManifest:
  - Event types, sessions, windows, option filters, and metrics should be declared in the SearchSpaceManifest before broad search runs.

- ModelAssessmentSpec:
  - Validator outputs will feed leakage checks, sample sufficiency analysis, and promotion blockers described in ModelAssessmentSpec.

- ReviewPacket:
  - Review packets should include validation status, unresolved warnings, and blockers so manual reviewers can make informed decisions.

- EdgeHypothesisRegistry:
  - Registry advancement remains manual and should reference validation artifacts (reports, warnings, and blockers) when available.

## 14. Invariants (hard rules — all still apply)

> The following were invariants defined by the design PR. "No validator implementation" language was accurate for the design PR only — validator was implemented in PRs #50–#55.

- Validator implementation completed in PRs #50–#55; invariant rules below remain in force.
- No JSON schema in this design PR.
- No fixture edits in this PR.
- event_id is required for OptionsObservationSpec event-cohort research.
- event identity is the canonical cohort and join key.
- Decision-time feature timestamps must be <= applicable decision timestamp.
- Future validators must reject features after the applicable decision timestamp.
- Observations without event_id are invalid for event-cohort research.
- Cohorts must not be selected by raw option observation date alone.
- No calendar-day approximation for trading-day windows.
- unknown gap_exposure blocks promotion or advancement.
- unknown_event_hold blocks promotion or advancement.
- No automated promotion.
- No automated registry mutation.
- No live trading.
- No production execution.
- Human review remains required.

## 15. Non-goals (design PR scope — historical)

> These were the non-goals of the design PR (#50). Implementation now exists at `scripts/local/validate_event_options_contract.py`.

- No code implementation in this design PR.
- Validator implemented in PRs #50–#55.
- No JSON schema for Event/Options contract (deferred).
- No fixture edits in this design PR.
- No runtime behavior changes.
- No registry mutation or automated promotion.
- No accepted/rejected/killed automation.
- No autonomous search or optimization.
- No Bayesian optimization.
- No genetic programming.
- No live trading or production execution.

## 16. Follow-up roadmap (suggested PR numbering)

- PR #50: Implement local Event/Options contract validator (code + tests)
- PR #51: Add additional invalid fixtures for timestamp and session edge cases
- PR #52: Add Event/Options contract JSON schema
- PR #53: Wire validator into CI (unit, integration checks)
- PR #54: Add ReviewPacket linkage for validation artifacts

---

Validation checklist (docs-only references included):
- This design references docs/event_options_contract_spec_v1.md and fixtures/event_options_contract_v1
- Fixtures: valid_events_minimal.csv, valid_options_observations_minimal.csv, invalid_events_examples.csv, invalid_options_observations_examples.csv
- CLI hint includes validate_event_options_contract.py as illustrative
- Relationships include TrialLedger, SearchSpaceManifest, ModelAssessmentSpec, ReviewPacket, EdgeHypothesisRegistry

No implementation artifacts are included in this PR.