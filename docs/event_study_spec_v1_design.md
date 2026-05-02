# EventStudySpec v1 Design

**Design date:** 2026-05-02
**PR:** #112
**Governing documents:**
- [`docs/domain_neutral_aed_architecture.md`](./domain_neutral_aed_architecture.md) — AED core domain-neutral principles, boundary rule, generalized abstractions, agent tooling, and stop rules
- [`docs/domain_neutral_modularity_audit.md`](./domain_neutral_modularity_audit.md) — modularity audit confirming governance layer is domain-neutral; engine/ is expected pre-earnings coupling
- [`docs/literature_requirements_for_aed.md`](./literature_requirements_for_aed.md) — §10c: EventStudySpec event-alignment fields including `event_study_spec_id`, `event_family`, `event_timestamp`, `pre_event_window`, `post_event_window`, `leakage_policy`, `event_anchor_policy`

---

## 1. Purpose

EventStudySpec v1 defines the event-alignment contract for experiments. It answers:
- What event set is being studied?
- What timestamp defines the event anchor?
- How are pre-event and post-event windows defined?
- What information is available at each decision point?
- What leakage controls apply around event timing?
- How are event collisions, duplicate events, and missing timestamps handled?
- What trading calendar or observation schedule applies?

EventStudySpec is a **design-time declaration** of event-alignment constraints. It is committed to the repository before any trial data is generated. It does not contain runtime signals, selections, rankings, or assessment outputs.

EventStudySpec is **domain-neutral**. It declares event-alignment rules that apply across any asset class or research domain — equities, options, futures, FX, crypto, commodities, rates, macro indicators, or custom event types.

---

## 2. Relationship to AED Artifacts

### 2a. ExperimentSpec

ExperimentSpec declares the overall experiment plan and references an EventStudySpec to define the event-alignment structure:

```
ExperimentSpec.event_study_ref → EventStudySpec.event_study_spec_id
```

ExperimentSpec does not compute event alignment — it references the EventStudySpec declaration. The `event_study_spec_id` links to a named event-alignment contract. One EventStudySpec may be referenced by multiple ExperimentSpecs.

### 2b. OutcomeSpec

OutcomeSpec defines what metric is measured over what window. EventStudySpec defines the temporal structure around the event anchor — the windows within which outcomes are measured. They are independent sibling declarations:

```
ExperimentSpec.event_study_ref → EventStudySpec.event_study_spec_id
ExperimentSpec.outcome_spec_id → OutcomeSpec.outcome_spec_id
```

OutcomeSpec does not own event-alignment logic. EventStudySpec provides the temporal framing; OutcomeSpec provides the measurement declaration.

### 2c. InstrumentUniverseSpec

InstrumentUniverseSpec declares which instruments are eligible. EventStudySpec declares how observations are aligned around events. They operate independently:

```
ExperimentSpec.instrument_universe_id → InstrumentUniverseSpec.instrument_universe_id
ExperimentSpec.event_study_ref → EventStudySpec.event_study_spec_id
```

Neither depends on the other. A single experiment can reference one InstrumentUniverseSpec and one EventStudySpec.

### 2d. SearchSpaceManifest

SearchSpaceManifest declares trial generation budget and parameter constraints. EventStudySpec declares event-alignment timing. They are independent:

```
ExperimentSpec.search_space_id → SearchSpaceManifest.search_space_id
ExperimentSpec.event_study_ref → EventStudySpec.event_study_spec_id
```

SearchSpaceManifest does not own event timing; EventStudySpec does not own parameter constraints.

### 2e. TrialLedger

TrialLedger records individual trial results. The event-alignment context determines how trials are windowed around event timestamps, but TrialLedger does not own event-alignment declarations:

```
TrialLedger.event_study_ref → EventStudySpec.event_study_spec_id (informational)
```

TrialLedger records trial execution. EventStudySpec declares the event-alignment contract under which those trials were structured.

### 2f. DataManifest

DataManifest declares the data scope available for the experiment. EventStudySpec defines event timing and window boundaries against that data:

```
EventStudySpec.event_source_refs → DataManifest.data_manifest_id
```

EventStudySpec event timestamps and windows are evaluated against data described by DataManifest. DataManifest does not own event-alignment logic.

### 2g. ModelAssessmentSpec

ModelAssessmentSpec computes statistical assessment outputs from trial results. EventStudySpec provides the event-alignment context, but assessment outputs are owned by ModelAssessmentSpec:

```
ModelAssessmentSpec.event_study_ref → EventStudySpec.event_study_spec_id (informational)
```

EventStudySpec does not own PBO estimates, DSR estimates, Sharpe haircuts, false discovery rates, strategy complexity scores, or any computed assessment output. These belong to ModelAssessmentSpec.

### 2h. EdgeHypothesisRegistry

EdgeHypothesisRegistry holds the hypothesis being tested. EventStudySpec constrains the temporal structure of event-alignment experiments but does not advance hypothesis status:

```
EventStudySpec.hypothesis_id → EdgeHypothesisRegistry.hypothesis_id (informational)
```

EventStudySpec may record which hypothesis motivated the event study design, but it does not change hypothesis status.

### 2i. Runner Outputs

Runner outputs (equity curves, performance series, null-model comparisons) are runtime artifacts computed against the event-aligned windows declared by EventStudySpec. Runner outputs do not own event-alignment rules:

```
RunnerOutput.event_study_ref → EventStudySpec.event_study_spec_id
```

Runner outputs reference EventStudySpec for provenance; EventStudySpec does not reference runner outputs.

### 2j. ReviewPacket

ReviewPacket renders a human judgment on hypothesis advancement. EventStudySpec provides the event-alignment context for that judgment, but does not own the decision:

```
ReviewPacket.event_study_ref → EventStudySpec.event_study_spec_id (informational)
```

### 2k. Domain Profiles

Domain profiles (PreEarningsProfile, OptionsEventRiskProfile, MacroEventProfile, CryptoEventProfile, etc.) provide domain-specific URI resolutions for abstract references. EventStudySpec supports `domain_profile_refs` and domain-specific `event_family` values to allow domain-specific event-alignment logic without hard-coding it into the core schema:

```
EventStudySpec.domain_profile_refs → DomainProfile.domain_profile_id
```

Domain profiles do not modify EventStudySpec. They provide domain-specific event-alignment enrichments that sit above the core EventStudySpec boundary.

---

## 3. Proposed Required Fields

These fields define EventStudySpec v1. Implementation is complete (PRs #112–#117).

| Field | Type | Description |
|-------|------|-------------|
| `event_study_spec_id` | string | Canonical ID, format EVS-YYYY-NNNN |
| `event_study_version` | integer | Semantic version integer, ≥ 1 |
| `event_family` | enum | Category of event. See §5a. |
| `event_source_refs` | array[string] | References to DataManifest entries providing event timestamps and metadata |
| `event_anchor_policy` | enum | How the event anchor timestamp is determined. See §5b. |
| `event_timestamp_policy` | enum | Acceptable timestamp precision for event times. See §5c. |
| `decision_timestamp_policy` | enum | When the decision timestamp falls relative to event publication. See §5d. |
| `pre_event_window` | object | Pre-event observation window definition. See §6. |
| `post_event_window` | object | Post-event observation window definition. See §6. |
| `leakage_policy` | enum | Controls to prevent lookahead bias. See §5e. |
| `event_deduplication_policy` | enum | How duplicate events in the same window are resolved. See §5f. |
| `event_collision_policy` | enum | How overlapping events from different sources are resolved. See §5g. |
| `missing_event_time_policy` | enum | How events with missing or ambiguous timestamps are handled. See §5h. |
| `calendar_policy` | enum | How window units map to calendar or trading time. See §5i. |
| `created_at` | string | ISO 8601 timestamp of event study declaration |
| `reviewer` | object | Reviewer identity with `reviewer_id` (string) and optional `reviewer_name` (string) |

---

## 4. Proposed Optional Fields and Hooks

| Field | Type | Description |
|-------|------|-------------|
| `event_type_filter` | array[string] | Restrict events to specific subtypes within `event_family` |
| `event_importance_filter` | object | Minimum importance or significance threshold for events to include |
| `event_source_priority` | array[string] | Priority order for event sources when multiple sources report the same event |
| `event_quality_filter` | object | Filters on event data quality metrics (e.g., revision_likelihood, source_reliability_score) |
| `timezone_policy` | enum | Timezone convention for event timestamps: `utc`, `exchange_local`, `macro_release_country`, `custom` |
| `trading_calendar_ref` | string | Reference to a trading calendar artifact for session-bound event studies |
| `market_session_policy` | enum | Which session(s) an event applies to: `regular`, `extended`, `pre_market`, `after_hours`, `overnight`, `all_sessions` |
| `event_lag_policy` | object | How to handle events discovered after the fact (late announcements, revisions) |
| `announcement_status_policy` | enum | How to handle preliminary, revised, or confirmed event announcements |
| `domain_profile_refs` | array[string] | References to domain profiles providing domain-specific event-alignment enrichments |
| `outcome_spec_refs` | array[string] | References to OutcomeSpecs that measure outcomes within this event study's windows |
| `instrument_universe_refs` | array[string] | References to InstrumentUniverseSpecs applicable to events in this study |
| `runner_output_refs` | array[string] | References to runner outputs produced under this event study |
| `review_packet_refs` | array[string] | References to ReviewPackets that evaluated hypotheses using this event study |
| `extension_hooks` | object | Optional extension object for future domain-specific fields |
| `notes` | string | Human-readable notes about the event study design rationale |

---

## 5. Proposed Enums

### 5a. event_family

Event families define the broad category of event being studied. The core schema is intentionally general.

| Value | Description |
|-------|-------------|
| `earnings` | Corporate earnings announcements |
| `macro_release` | Macroeconomic data releases (CPI, GDP, NFP, etc.) |
| `central_bank_decision` | Central bank rate decisions, FOMC statements, ECB announcements |
| `dividend` | Dividend announcements, ex-dates, payment dates |
| `split` | Stock splits, reverse splits, spin-offs |
| `index_rebalance` | Index addition, removal, or reweighting |
| `product_launch` | New product introduction events |
| `crypto_protocol_event` | Protocol upgrades, halvings, hard forks, token events |
| `commodity_inventory` | Supply/inventory reports (e.g., EIA petroleum status) |
| `regulatory_event` | Regulatory decisions, FDA approvals, legal rulings |
| `custom` | Domain-specific event type requiring custom handling |

### 5b. event_anchor_policy

Defines how the event anchor timestamp is determined from the raw event data.

| Value | Description |
|-------|-------------|
| `event_timestamp` | Use the raw event timestamp as the anchor directly |
| `first_tradable_session_after_event` | Anchor to the first tradable session open after the event |
| `last_tradable_session_before_event` | Anchor to the last tradable session close before the event |
| `next_observation_after_event` | Anchor to the next scheduled observation point after the event |
| `previous_observation_before_event` | Anchor to the previous scheduled observation point before the event |
| `custom` | Domain-specific anchor policy defined in a domain profile |

### 5c. event_timestamp_policy

Defines acceptable timestamp precision for event times.

| Value | Description |
|-------|-------------|
| `exact_timestamp_required` | Full timestamp (date + time + timezone) required |
| `date_only_allowed` | Calendar date only; time of day is not used |
| `session_only_allowed` | Trading session label only (e.g., BMO, REG, AMC); no time-of-day |
| `inferred_timestamp_allowed` | Timestamps may be inferred from session context when exact time unavailable |
| `custom` | Domain-specific timestamp policy |

### 5d. decision_timestamp_policy

Defines when the decision timestamp falls relative to event publication.

| Value | Description |
|-------|-------------|
| `before_event_publication` | Decision timestamp is strictly before event is publicly known |
| `after_event_publication` | Decision timestamp is after event has been publicly announced |
| `prior_session_close` | Decision timestamp is the prior session's official close |
| `same_session_open` | Decision timestamp is the same session's official open |
| `next_session_open` | Decision timestamp is the next session's official open |
| `custom` | Domain-specific decision timing |

### 5e. leakage_policy

Defines the lookahead and information-availability controls around event timing.

| Value | Description |
|-------|-------------|
| `strict_no_lookahead` | No post-event information available at any pre-event decision point. Feature cutoff must precede event anchor. |
| `allow_known_calendar_only` | Only calendar-date information available before the event; time-of-day and content not available until publication |
| `allow_public_timestamp_only` | Event timestamp is publicly known in advance (e.g., scheduled macro releases); content embargoed until release |
| `custom` | Domain-specific leakage policy |

The core schema enforces that `strict_no_lookahead` is the default for pre-event windows. Tighter domain-specific policies belong in domain profiles.

### 5f. event_deduplication_policy

Defines how duplicate events for the same instrument within the same study window are resolved.

| Value | Description |
|-------|-------------|
| `keep_first` | Retain only the first event; discard subsequent duplicates |
| `keep_last` | Retain only the last event; discard earlier duplicates |
| `merge_same_day` | Merge events occurring on the same calendar day into a single event |
| `merge_same_timestamp` | Merge events with identical timestamps |
| `reject_duplicates` | Any duplicate event causes the study to reject the observation |
| `custom` | Domain-specific deduplication policy |

### 5g. event_collision_policy

Defines how overlapping events from potentially different sources are resolved.

| Value | Description |
|-------|-------------|
| `allow_overlapping_windows` | Multiple events with overlapping windows are all included |
| `reject_overlapping_windows` | Any overlapping event windows cause rejection of the collision |
| `keep_highest_priority_event` | Retain the event with the highest priority (per `event_source_priority`); discard others |
| `merge_event_cluster` | Merge all overlapping events into a single cluster with a combined anchor |
| `custom` | Domain-specific collision policy |

### 5h. missing_event_time_policy

Defines how events with missing, ambiguous, or inferred timestamps are handled.

| Value | Description |
|-------|-------------|
| `reject_event` | Any event with a missing or ambiguous timestamp is excluded |
| `use_date_close` | Use the session close of the event date when exact time is missing |
| `use_date_open` | Use the session open of the event date when exact time is missing |
| `infer_from_session` | Infer the timestamp from the session label and trading calendar |
| `custom` | Domain-specific missing-time policy |

### 5i. calendar_policy

Defines how window offsets are measured — calendar days, trading days, or observation count.

| Value | Description |
|-------|-------------|
| `calendar_days` | Offsets are measured in absolute calendar days |
| `trading_days` | Offsets are measured in trading-day units per the referenced trading calendar |
| `observations` | Offsets are measured in observation-frequency units (e.g., daily bars = 1 observation per day) |
| `custom` | Domain-specific calendar policy |

---

## 6. Window Structures

The `pre_event_window` and `post_event_window` objects define the observation windows around the event anchor. Both use the same structure:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `start_offset` | integer | Yes | Offset from the event anchor to the window start (negative for pre-event windows, zero or positive for post-event windows) |
| `end_offset` | integer | Yes | Offset from the event anchor to the window end |
| `units` | enum | Yes | Unit of measurement: `calendar_days`, `trading_days`, `observations`, `periods` |
| `include_event_anchor` | boolean | No | Whether the event anchor itself is included in the window. Default: `false` for pre-event, `false` for post-event |
| `window_role` | string | No | Semantic role of this window: `pre_event`, `post_event`, `estimation`, `baseline`, `control` |

**Example pre-event window (T-5 to T-1 trading days):**
```json
{
  "start_offset": -5,
  "end_offset": -1,
  "units": "trading_days",
  "include_event_anchor": false,
  "window_role": "pre_event"
}
```

**Example post-event window (T+0 to T+20 trading days):**
```json
{
  "start_offset": 0,
  "end_offset": 20,
  "units": "trading_days",
  "include_event_anchor": true,
  "window_role": "post_event"
}
```

**Example observation-frequency window (T-20 to T-1 observations):**
```json
{
  "start_offset": -20,
  "end_offset": -1,
  "units": "observations",
  "include_event_anchor": false,
  "window_role": "pre_event"
}
```

**Note:** `start_offset: 0` is allowed for post-event windows when the event anchor observation is included in the post-event measurement window. Whether the anchor is included is controlled by `include_event_anchor`.

---

## 7. Event Source and Quality Rules

### 7a. event_source_refs

EventStudySpec references DataManifest entries that provide event timestamps and event metadata:

```
EventStudySpec.event_source_refs → DataManifest.data_manifest_id
```

Each event source may have different timestamp precision, revision history, and publication timing. The `event_timestamp_policy` and `timezone_policy` fields define how to normalize across sources.

### 7b. event_source_priority

When multiple event sources report the same event (e.g., Bloomberg and Refinitiv both reporting an FOMC decision), `event_source_priority` determines which source takes precedence:

```
event_source_priority: ["primary_calendar", "exchange_calendar", "broker_data"]
```

Priority is a simple ordered list. The first source that provides a valid timestamp for a given event is used.

### 7c. event_quality_filter

Event data quality varies by source. `event_quality_filter` defines minimum quality thresholds:

| Field | Type | Description |
|-------|------|-------------|
| `min_source_reliability_score` | number | Minimum source reliability score [0, 1] |
| `allow_revisions` | boolean | Whether revised event times are allowed |
| `max_revisions` | integer | Maximum number of revisions allowed before exclusion |
| `require_announcement_time` | boolean | Whether the exact announcement time must be known |

### 7d. event_importance_filter

Some event studies require filtering to significant events only:

```
event_importance_filter: {
  "importance_min": "high",
  "exclude_pre_scheduled": false
}
```

Importance levels (`high`, `medium`, `low`, `all`) are domain-specific and defined in domain profiles.

---

## 8. Leakage and Timing Controls

EventStudySpec enforces strict separation between pre-event decision information and post-event outcome information.

### 8a. When event timestamps are known

Under `strict_no_lookahead`:
- The event timestamp is known only after the event occurs
- Pre-event feature cutoff must precede the event anchor
- Post-event features are unavailable before the cutoff

Under `allow_known_calendar_only`:
- The calendar date of the event may be known in advance (e.g., FOMC meeting schedule)
- The exact time and content are embargoed until announcement
- Decision timestamp must precede the announcement

Under `allow_public_timestamp_only`:
- The exact event timestamp is publicly known (scheduled macro releases at 8:30 AM ET)
- Only the event content (e.g., actual CPI vs. forecast) is embargoed
- Decision timestamp must precede the release time

### 8b. Session labels

Session labels (BMO = Before Market Open, AMC = After Market Close, REG = Regular Session) indicate when an event is publicly confirmed:

- BMO events: publicly confirmed at session open; content available after open
- AMC events: publicly confirmed at session close; content available after close
- REG events: confirmed within regular session hours

EventStudySpec's `market_session_policy` records which session an event applies to. The `decision_timestamp_policy` determines whether decisions can be made in the same session before the event is confirmed.

### 8c. Inferred timestamps

When exact timestamps are unavailable (`inferred_timestamp_allowed`), EventStudySpec requires:

- The inference method is recorded in the event data
- Inferred timestamps are flagged so they can be excluded from strict leakage controls
- `missing_event_time_policy` governs what happens when inference is not possible

### 8d. Feature cutoff and decision timestamp

EventStudySpec enforces that pre-event feature data ends before the decision timestamp. The constraint depends on the decision mode:

**For pre-event decision modes** (`before_event_publication`, `prior_session_close`, `same_session_open`):

```
Feature cutoff ≤ Decision timestamp < Event anchor
```

**For post-publication or post-event decision modes** (`after_event_publication`, `next_session_open`):

```
Feature cutoff ≤ Event anchor ≤ Decision timestamp
```

The `leakage_policy` field specifies what information is available at each point. Window boundaries alone do not guarantee leakage control — the feature cutoff must be explicitly declared.

**Note:** The validator must apply the correct timing relation based on `decision_timestamp_policy`. `after_event_publication` is valid only when the experiment is explicitly post-publication/post-event and outcome windows do not leak unavailable information into the decision.

### 8e. Calendar-days vs. trading-days vs. observations

`calendar_policy` determines how window offsets are interpreted:

- `calendar_days`: T+1 means one calendar day after the event, regardless of weekends or holidays
- `trading_days`: T+1 means the next trading day after the event, per the referenced trading calendar
- `observations`: T+1 means one observation-frequency unit (e.g., one daily bar) after the event

For event studies in liquid markets with daily data, `trading_days` is typically appropriate. For sparse events (e.g., earnings, which occur on specific dates), `calendar_days` may be more natural. The choice depends on the event family and data frequency.

---

## 9. Boundary: What EventStudySpec Does Not Own

EventStudySpec declares event-alignment, timing, window, and leakage constraints. It does **not** own any of the following:

### 9a. Option Contract Selection

EventStudySpec does not own:
- Option contract expiration selection
- Strike selection relative to event
- Expiry rank (`expiry_rank`)
- Delta targeting (`delta_target`)
- Entry DPE (`entry_dpe`) or exit DPE (`exit_dpe`)

These belong to domain profiles (e.g., OptionsEventRiskProfile) or ExperimentSpec extensions.

### 9b. Volatility and Risk Fields

EventStudySpec does not own:
- Implied volatility at event (`iv_crush`)
- Gap exposure (`gap_exposure`)
- Post-event volatility regime

These belong to domain profiles or risk modeling extensions.

### 9c. Directional Signals and Rankings

EventStudySpec does not own:
- Directional signals (long, short, long/short)
- Entry or exit signals of any kind
- Ranking scores or percentile ranks
- Factor loadings or alpha signals

These are runtime outputs produced by runners executing ExperimentSpecs.

### 9d. Trial Accounting

EventStudySpec does not own:
- `selected_variant_id` — which trial variant was selected
- `n_tried` — number of trial variants attempted
- `trial_family_id` — which trial family a trial belongs to

Trial accounting belongs to TrialLedger and ModelAssessmentSpec.

### 9e. PnL and Assessment Outputs

EventStudySpec does not own:
- PnL of any kind (realized, unrealized, gross, net)
- Returns attribution
- `pbo_estimate` — probability of backtest overfitting
- `dsr_estimate` — degree of statistical significance

These belong to ModelAssessmentSpec.

### 9f. ReviewPacket Decisions

EventStudySpec does not own:
- ReviewPacket or any hypothesis advancement decision
- Approval or rejection rationale
- Status changes in EdgeHypothesisRegistry

These belong to ReviewPacket and EdgeHypothesisRegistry.

---

## 10. Conceptual Examples

These examples illustrate how EventStudySpec declarations work across domains. All examples use domain-neutral language; domain-specific details sit in optional filters or `domain_profile_refs`.

### 10a. Earnings Event Study (no pre-earnings specialization)

```
event_study_spec_id: EVS-2026-0001
event_family: earnings
event_anchor_policy: first_tradable_session_after_event
event_timestamp_policy: date_only_allowed
decision_timestamp_policy: prior_session_close
pre_event_window:
  start_offset: -5
  end_offset: -1
  units: trading_days
  window_role: pre_event
post_event_window:
  start_offset: 0
  end_offset: 20
  units: trading_days
  include_event_anchor: true
  window_role: post_event
leakage_policy: strict_no_lookahead
event_deduplication_policy: keep_first
event_collision_policy: keep_highest_priority_event
missing_event_time_policy: use_date_close
calendar_policy: trading_days
event_quality_filter:
  require_announcement_time: false
domain_profile_refs: [EPS-2026-0001]
```

Note: Entry/exit rules for options expiring at a specific rank around earnings are defined in the domain profile, not in EventStudySpec.

### 10b. Macro CPI Release Event Study

```
event_study_spec_id: EVS-2026-0002
event_family: macro_release
event_anchor_policy: event_timestamp
event_timestamp_policy: exact_timestamp_required
decision_timestamp_policy: before_event_publication
pre_event_window:
  start_offset: -10
  end_offset: -1
  units: observations
  window_role: pre_event
post_event_window:
  start_offset: 0
  end_offset: 5
  units: observations
  include_event_anchor: false
  window_role: post_event
leakage_policy: allow_public_timestamp_only
event_deduplication_policy: keep_first
event_collision_policy: allow_overlapping_windows
missing_event_time_policy: reject_event
calendar_policy: observations
timezone_policy: macro_release_country
domain_profile_refs: [MACRO-2026-0001]
```

### 10c. Central Bank Decision Event Study

```
event_study_spec_id: EVS-2026-0003
event_family: central_bank_decision
event_anchor_policy: event_timestamp
event_timestamp_policy: exact_timestamp_required
decision_timestamp_policy: prior_session_close
pre_event_window:
  start_offset: -1
  end_offset: 0
  units: trading_days
  window_role: pre_event
post_event_window:
  start_offset: 0
  end_offset: 5
  units: trading_days
  include_event_anchor: true
  window_role: post_event
leakage_policy: allow_known_calendar_only
event_deduplication_policy: keep_first
event_collision_policy: keep_highest_priority_event
missing_event_time_policy: infer_from_session
calendar_policy: trading_days
market_session_policy: regular
domain_profile_refs: [CB-2026-0001]
```

### 10d. Commodity Inventory Release Event Study

```
event_study_spec_id: EVS-2026-0004
event_family: commodity_inventory
event_anchor_policy: event_timestamp
event_timestamp_policy: exact_timestamp_required
decision_timestamp_policy: before_event_publication
pre_event_window:
  start_offset: -5
  end_offset: -1
  units: observations
  window_role: pre_event
post_event_window:
  start_offset: 0
  end_offset: 3
  units: observations
  include_event_anchor: false
  window_role: post_event
leakage_policy: strict_no_lookahead
event_deduplication_policy: keep_last
event_collision_policy: allow_overlapping_windows
missing_event_time_policy: reject_event
calendar_policy: observations
timezone_policy: utc
domain_profile_refs: [COM-2026-0001]
```

### 10e. Crypto Protocol Event Study

```
event_study_spec_id: EVS-2026-0005
event_family: crypto_protocol_event
event_anchor_policy: next_observation_after_event
event_timestamp_policy: inferred_timestamp_allowed
decision_timestamp_policy: before_event_publication
pre_event_window:
  start_offset: -7
  end_offset: -1
  units: observations
  window_role: pre_event
post_event_window:
  start_offset: 0
  end_offset: 14
  units: observations
  include_event_anchor: true
  window_role: post_event
leakage_policy: strict_no_lookahead
event_deduplication_policy: keep_first
event_collision_policy: merge_event_cluster
missing_event_time_policy: infer_from_session
calendar_policy: observations
timezone_policy: utc
domain_profile_refs: [CRYPTO-2026-0001]
```

---

## 11. Agent/Tooling Layer

EventStudySpec is a governance artifact. Hermes and OpenClaw may draft EventStudySpecs and suggest missing timing or leakage controls, but operate under the following constraints:

Hermes and OpenClaw **may**:
- Draft EventStudySpecs following domain-neutral construction patterns
- Suggest window offsets and leakage policies based on event family conventions
- Validate EventStudySpecs against the schema once implemented
- Reference existing DataManifests and domain profiles

Hermes and OpenClaw **may not**:
- Approve or advance a hypothesis
- Bypass or disable any validator
- Run unlocked autonomous search, Bayesian optimization, or genetic programming
- Advance hypothesis status in EdgeHypothesisRegistry
- Render a ReviewPacket decision
- Access live trading systems or production execution
- Select option contracts, strikes, or expiry ranks based on runtime signals

---

## 12. Validation Roadmap

EventStudySpec v1 implementation is complete through PR #117:

1. **Design doc** (PR #112) — describes the field set, enums, window structures, and timing controls (this document)
2. **Schema** (PR #113) — JSON schema for EventStudySpec v1
3. **Fixtures** (PR #114) — valid and invalid JSON fixtures covering all required fields, enums, and boundary conditions
4. **Validator** (PR #115) — `scripts/local/validate_event_study_spec.py` implementing the schema rules
5. **Tests** (PR #116) — pytest coverage of all validator paths
6. **CI wiring** (PR #117) — added to `scripts/ci/validate_governance_manifests.sh`
7. **Docs status update** (PR #118) — updated `docs/current_project_status.md` and `docs/README.md`

This roadmap follows the same pattern used for TrialLedger, SearchSpaceManifest, ModelAssessmentSpec, EdgeHypothesisRegistry, ExperimentSpec, OutcomeSpec, and InstrumentUniverseSpec.

---

## 13. Stop Rules

EventStudySpec v1 design respects the AED stop rules:

EventStudySpec does **not** enable, unlock, or activate any of the following without an explicit, separately designed governance extension:

- **Autonomous search** — EventStudySpec does not trigger or authorize autonomous event selection or window construction. `autonomous_search` remains prohibited.
- **Bayesian optimization** — No Bayesian optimization of event windows or leakage policies. `bayesian_optimization` remains prohibited.
- **Genetic programming** — No genetic programming of event-alignment rule generation. `genetic_programming` remains prohibited.
- **Automated promotion** — No automated advancement of hypotheses. Human-authored ReviewPacket required.
- **Automated registry mutation** — No automated changes to EdgeHypothesisRegistry status.
- **Live trading** — EventStudySpec is a design-time declaration only. It does not authorize live trading or production execution.
- **Production execution** — No production system execution.
- **GCRU integration** — GCRU integration requires a separately designed governance extension. EventStudySpec does not include GCRU-specific fields or live feed connections.

These rules apply regardless of whether they are invoked by humans, scripts, or AI agents.

---

## 14. Explicit Non-Scope

This design document does not:
- Implement a JSON schema for EventStudySpec
- Implement a validator for EventStudySpec
- Create fixtures or tests for EventStudySpec
- Modify any governance validator, schema, fixture, or CI helper
- Modify the EdgeHypothesisRegistry, ExperimentSpec, OutcomeSpec, InstrumentUniverseSpec, SearchSpaceManifest, TrialLedger, ModelAssessmentSpec, or DataManifest schemas
- Design OptionsEventRiskSpec, PreEarningsProfile, or other domain profiles (these are separate future PRs)
- Change any code in `engine/`, `schemas/`, `scripts/`, `tests/`, or `fixtures/`
- Modify `docs/edge_hypothesis_registry.csv`
