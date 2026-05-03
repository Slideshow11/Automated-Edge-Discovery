# PreEarningsProfile v1 Design

**Design date:** 2026-05-03
**PR:** #130
**Governing documents:**
- [`docs/domain_neutral_aed_architecture.md`](./domain_neutral_aed_architecture.md) — AED core domain-neutral principles, boundary rule, generalized abstractions, agent tooling, and stop rules
- [`docs/domain_neutral_modularity_audit.md`](./domain_neutral_modularity_audit.md) — modularity audit confirming governance layer is domain-neutral; engine/ is expected pre-earnings coupling
- [`docs/literature_requirements_for_aed.md`](./literature_requirements_for_aed.md) — literature requirements for options event-risk including IV ramp, jump exposure, crush, skew, term structure, and execution realism
- [`docs/event_study_spec_v1_design.md`](./event_study_spec_v1_design.md) — EventStudySpec v1: event-alignment contract that PreEarningsProfile specializes
- [`docs/options_event_risk_spec_v1_design.md`](./options_event_risk_spec_v1_design.md) — OptionsEventRiskSpec v1: options event-risk specialization that PreEarningsProfile further specializes for pre-earnings

---

## 1. Purpose

PreEarningsProfile v1 defines the domain-specific pre-earnings research module for US equity options. It is a domain-specific specialization that provides BMO (Before Market Open) and AMC (After Market Close) session semantics, DPE (Days to Earnings) targeting, earnings-specific gap-exposure rules, and IV crush policy for options event-risk experiments.

PreEarningsProfile answers:
- Which earnings session (BMO or AMC) does the event belong to?
- What is the DPE (Days to Earnings) targeting range for entry and exit?
- How should IV crush be characterized and measured?
- How does the earnings announcement time affect entry and exit timing?
- How should earnings-specific gap exposure be handled relative to the announcement session?

PreEarningsProfile is a **design-time declaration** of pre-earnings-specific configuration. It is committed to the repository before any trial data is generated. It does not contain runtime signals, option selections, greeks values, rankings, or assessment outputs.

PreEarningsProfile is **pre-earnings-specific only**. It does not support macro release events, central bank events, ETF events, or crypto options events. Those use their own domain profiles (MacroEventProfile, ETFEventProfile, CryptoOptionsProfile, etc.). PreEarningsProfile focuses exclusively on US equity earnings announcements and their options implications.

---

## 2. Relationship to AED Artifacts

### 2a. EventStudySpec

EventStudySpec declares the event-alignment structure — event families, window boundaries, anchor timestamps, leakage policies, collision and deduplication rules. PreEarningsProfile **specializes** EventStudySpec for pre-earnings. It does not replace or replicate event-alignment logic:

```
PreEarningsProfile.event_study_spec_ref → EventStudySpec.event_study_spec_id
```

EventStudySpec owns event timing, window construction, and leakage control. PreEarningsProfile owns the earnings-specific session semantics (BMO/AMC), DPE targeting, and IV crush characterization that EventStudySpec does not contain.

One EventStudySpec may be referenced by multiple PreEarningsProfiles covering different earnings session types or different DPE targeting strategies.

### 2b. OptionsEventRiskSpec

OptionsEventRiskSpec declares the options-specific event-risk configuration — contract selection, liquidity policy, pricing, gap exposure. PreEarningsProfile **specializes** OptionsEventRiskSpec for pre-earnings. It does not replace or replicate options event-risk logic:

```
PreEarningsProfile.options_event_risk_ref → OptionsEventRiskSpec.options_event_risk_spec_id
```

OptionsEventRiskSpec owns option universe policy, contract selection, delta targeting, and liquidity handling. PreEarningsProfile owns the earnings-specific session semantics and DPE targeting that OptionsEventRiskSpec does not contain as core fields.

OptionsEventRiskSpec provides the domain-neutral hooks (`preearnings_profile_refs`, `gap_exposure_policy` with domain-neutral values) through which PreEarningsProfile specializes the experiment. The core OptionsEventRiskSpec does not contain BMO/AMC fields, DPE targeting, or earnings-specific gap exposure as core fields — those belong in PreEarningsProfile.

### 2c. ExperimentSpec

ExperimentSpec declares the overall experiment plan and references an EventStudySpec for event-alignment and an OptionsEventRiskSpec for options-specific configuration. PreEarningsProfile is referenced through OptionsEventRiskSpec's `preearnings_profile_refs` field:

```
ExperimentSpec.event_study_ref → EventStudySpec.event_study_spec_id
ExperimentSpec.options_event_risk_ref → OptionsEventRiskSpec.options_event_risk_spec_id
OptionsEventRiskSpec.preearnings_profile_refs → PreEarningsProfile.preearnings_profile_id
```

ExperimentSpec does not compute event alignment or option selection — it references the declarations. PreEarningsProfile provides the domain-specific enrichment for the options event-risk configuration.

### 2d. InstrumentUniverseSpec

InstrumentUniverseSpec declares which underlying instruments are eligible (equities, ETFs). PreEarningsProfile constrains the instrument universe to US equity options for earnings events:

```
PreEarningsProfile.instrument_universe_ref → InstrumentUniverseSpec.instrument_universe_id
```

PreEarningsProfile does not override InstrumentUniverseSpec instrument eligibility rules. It relies on InstrumentUniverseSpec to declare eligible underlyings, then PreEarningsProfile adds earnings-specific constraints (e.g., restricting to stocks with listed options that have upcoming earnings).

### 2e. OutcomeSpec

OutcomeSpec defines what metric is measured over what window. PreEarningsProfile defines the earnings-specific context for that measurement:

```
PreEarningsProfile.outcome_spec_refs → OutcomeSpec.outcome_spec_id
```

PreEarningsProfile does not own outcome measurement logic. It provides the earnings-specific context (session type, DPE range) that informs how outcomes should be measured.

### 2f. SearchSpaceManifest

SearchSpaceManifest declares trial generation budget and parameter constraints. PreEarningsProfile declares earnings-specific configuration. They are independent:

```
ExperimentSpec.search_space_id → SearchSpaceManifest.search_space_id
PreEarningsProfile.* → (independent pre-earnings configuration)
```

SearchSpaceManifest does not own DPE targeting or session semantics.

### 2g. TrialLedger

TrialLedger records individual trial results. PreEarningsProfile provides the pre-earnings configuration context for those trials:

```
TrialLedger.preearnings_profile_ref → PreEarningsProfile.preearnings_profile_id (informational)
```

TrialLedger records trial execution. PreEarningsProfile declares the pre-earnings contract under which those trials were structured.

### 2h. ModelAssessmentSpec

ModelAssessmentSpec computes statistical assessment outputs (PBO, DSR, Sharpe haircuts, overfit discounts). PreEarningsProfile does not own these outputs — it only provides the pre-earnings context:

```
ModelAssessmentSpec.preearnings_profile_ref → PreEarningsProfile.preearnings_profile_id (informational)
```

PreEarningsProfile does not own `pbo_estimate`, `dsr_estimate`, `sharpe_haircut`, `overfit_discount`, or any computed assessment metric. These belong to ModelAssessmentSpec.

### 2i. EdgeHypothesisRegistry

EdgeHypothesisRegistry holds the hypothesis being tested. PreEarningsProfile constrains the pre-earnings experiment structure but does not advance hypothesis status:

```
PreEarningsProfile.hypothesis_id → EdgeHypothesisRegistry.hypothesis_id (informational)
```

PreEarningsProfile may record which hypothesis motivated the pre-earnings design, but it does not change hypothesis status.

### 2j. Runner Outputs

Runner outputs (equity curves, performance series, greeks ladders, IV crush measurements) are runtime artifacts computed against the pre-earnings configuration declared by PreEarningsProfile. Runner outputs do not own pre-earnings rules:

```
RunnerOutput.preearnings_profile_ref → PreEarningsProfile.preearnings_profile_id
```

Runner outputs reference PreEarningsProfile for provenance; PreEarningsProfile does not reference runner outputs.

### 2k. ReviewPacket

ReviewPacket renders a human judgment on hypothesis advancement. PreEarningsProfile provides the pre-earnings context for that judgment, but does not own the decision:

```
ReviewPacket.preearnings_profile_ref → PreEarningsProfile.preearnings_profile_id (informational)
```

### 2l. Domain Profiles

PreEarningsProfile is itself a domain profile. It is one of several domain profiles that specialize the core AED schemas:

```
EventStudySpec.domain_profile_refs → DomainProfile.domain_profile_id
OptionsEventRiskSpec.domain_profile_refs → DomainProfile.domain_profile_id
PreEarningsProfile → DomainProfile (is a domain profile)
```

Other domain profiles (MacroEventProfile, ETFEventProfile, CryptoOptionsProfile, SeasonalityProfile, etc.) are independent and do not share semantics with PreEarningsProfile.

---

## 3. Proposed Required Fields

These fields define PreEarningsProfile v1.

||| Field | Type | Description |
||-------|------|-------------|
|| `preearnings_profile_id` | string | Canonical ID, format PEP-YYYY-NNNN |
|| `preearnings_profile_version` | integer | Semantic version integer, ≥ 1 |
|| `event_study_spec_ref` | string | Reference to EventStudySpec providing event-alignment, format EVS-YYYY-NNNN |
|| `options_event_risk_ref` | string | Reference to OptionsEventRiskSpec for options configuration, format OER-YYYY-NNNN |
|| `session_anchor_policy` | enum | Which session(s) the earnings announcement applies to: bmo_only, amc_only, bmo_and_amc, intra_day, custom. See §5a. |
|| `earnings_time_reference` | enum | Source of earnings announcement time: after_hours_only, pre_market_only, regular_hours_only, confirmed_after_hours, confirmed_pre_market, unconfirmed, custom. See §5b. |
|| `entry_dpe_policy` | object | DPE targeting policy for entry: target DPE range, anchor day count conventions. See §6. |
|| `exit_dpe_policy` | object | DPE targeting policy for exit: target DPE range, iv_collapse_threshold. See §6. |
|| `iv_crush_policy` | object | IV crush characterization: measurement window, crush magnitude estimate, iv_hierarchy_policy. See §7. |
|| `gap_exposure_policy` | enum | Whether strategy may hold across earnings announcement: allow_gap_hold, prohibit_gap_hold, exit_before_session, enter_after_session, custom. See §5c. |
|| `created_at` | string | ISO 8601 timestamp of pre-earnings profile declaration |
|| `reviewer` | object | Human reviewer metadata. Must contain `name` as a non-empty string. Additional metadata such as `reviewer_id`, `reviewer_name`, `affiliation`, or `review_timestamp` may be included inside the reviewer object. |

---

## 4. Proposed Optional Fields and Hooks

||| Field | Type | Description |
||-------|------|-------------|
|| `instrument_universe_ref` | string | Reference to InstrumentUniverseSpec for underlying instruments, format IUS-YYYY-NNNN |
|| `outcome_spec_refs` | array[string] | References to OutcomeSpecs measuring outcomes in this pre-earnings experiment, format OUT-YYYY-NNNN |
|| `earnings_calendar_ref` | string | Reference to an earnings calendar artifact for confirmed earnings dates |
|| `iv_surface_ref` | string | Reference to a volatility surface artifact for IV/IV percentile lookups |
|| `dpe_calendar_policy` | object | DPE calendar conventions: weekend handling, holiday handling, exchange_calendar_ref |
|| `session_overlap_policy` | enum | How to handle stocks with both BMO and AMC earnings on same day: prioritize_bmo, prioritize_amc, separate_trials, reject |
|| `earnings_revision_policy` | enum | How to handle earnings date revisions or rescheduled announcements: reject_revision, accept_revision, flag_for_review |
|| `minimum_iv_rank` | number | Minimum IV rank required for inclusion (0.0 to 1.0) |
|| `iv_regime_filter` | enum | Filter for IV regime: high_iv_only, low_iv_only, any_iv, custom |
|| `gap_historical_policy` | object | Historical gap analysis: gap_percentile_threshold, gap_direction_filter |
|| `earnings_size_filter` | object | Filter by earnings size or surprise history: eps_surprise_threshold, revenue_behavior |
|| `hypothesis_id` | string | Reference to EdgeHypothesisRegistry hypothesis being tested |
|| `runner_output_refs` | array[string] | References to runner outputs produced under this pre-earnings profile |
|| `review_packet_refs` | array[string] | References to ReviewPackets that evaluated hypotheses using this profile |
|| `extension_hooks` | object | Optional extension object for future domain-specific fields |
|| `notes` | string | Human-readable notes about the pre-earnings design rationale |

---

## 5. Proposed Enums

### 5a. session_anchor_policy

Defines which session(s) the earnings announcement applies to. This is the primary policy for determining whether an earnings announcement is a BMO (Before Market Open) event or an AMC (After Market Close) event.

||| Value | Description |
||-------|-------------|
|| `bmo_only` | Earnings are confirmed to be released before market open; entry and exit are anchored to the pre-market session |
|| `amc_only` | Earnings are confirmed to be released after market close; entry and exit are anchored to the after-hours session |
|| `bmo_and_amc` | Earnings could be released in either session; separate trials for each confirmed session type |
|| `intra_day` | Earnings are intraday announcements; no pre/after session anchor applies |
|| `unconfirmed` | Session type is not yet confirmed; requires calendar verification before trial execution |
|| `custom` | Domain-specific session anchor defined in extension_hooks |

### 5b. earnings_time_reference

Defines the source and reliability of the earnings announcement time.

||| Value | Description |
||-------|-------------|
|| `after_hours_only` | Confirmed after-hours announcement only |
|| `pre_market_only` | Confirmed pre-market announcement only |
|| `regular_hours_only` | Confirmed regular-hours announcement only (intraday) |
|| `confirmed_after_hours` | After-hours confirmed by calendar source |
|| `confirmed_pre_market` | Pre-market confirmed by calendar source |
|| `unconfirmed` | Announcement time not confirmed; requires verification |
|| `custom` | Domain-specific timing defined in extension_hooks |

### 5c. gap_exposure_policy

Defines whether the strategy may hold across the earnings announcement and how gap risk is handled. This is the earnings-specific specialization of OptionsEventRiskSpec's domain-neutral gap policy.

||| Value | Description |
||-------|-------------|
|| `allow_gap_hold` | Strategy may hold across the earnings announcement; gap risk is accepted |
|| `prohibit_gap_hold` | Strategy must not hold across the announcement; exit before session close (AMC) or before market open (BMO) |
|| `exit_before_session` | All positions must be closed before the session (pre-market for BMO, after-hours for AMC) |
|| `enter_after_session` | Entry only after the session; pre-session position must be flat |
|| `custom` | Domain-specific gap policy defined in extension_hooks |

This policy specializes the domain-neutral `gap_exposure_policy` from OptionsEventRiskSpec:
- `exit_before_event_anchor` → `exit_before_session` (earnings-specific)
- `enter_after_event_anchor` → `enter_after_session` (earnings-specific)
- `allow_gap_hold` → `allow_gap_hold` (same semantics)
- `prohibit_gap_hold` → `prohibit_gap_hold` (same semantics)

### 5d. iv_regime_filter

Defines the IV regime filter for pre-earnings inclusion.

||| Value | Description |
||-------|-------------|
|| `high_iv_only` | Include only high-IV periods (IV rank > 0.70) |
|| `low_iv_only` | Include only low-IV periods (IV rank < 0.30) |
|| `any_iv` | No IV regime filter applied |
|| `iv_expand_only` | Include only periods where IV is expanding ahead of earnings |
|| `iv_collapse_only` | Include only periods approaching IV collapse |
|| `custom` | Domain-specific filter defined in extension_hooks |

### 5e. session_overlap_policy

Defines how to handle stocks with both BMO and AMC earnings on the same calendar day.

||| Value | Description |
||-------|-------------|
|| `prioritize_bmo` | Prioritize BMO earnings; treat AMC as secondary |
|| `prioritize_amc` | Prioritize AMC earnings; treat BMO as secondary |
|| `separate_trials` | Run separate trials for BMO and AMC as independent events |
|| `reject` | Reject overlapping sessions; do not run trials for same-day BMO and AMC |

---

## 6. DPE Targeting Policies

### 6a. entry_dpe_policy

The `entry_dpe_policy` object defines the Days to Earnings targeting for entry:

||| Field | Type | Description |
||-------|------|-------------|
|| `entry_dpe_min` | integer | Minimum DPE for entry (inclusive), e.g., 1 = 1 day before earnings |
|| `entry_dpe_max` | integer | Maximum DPE for entry (inclusive), e.g., 5 = 5 days before earnings |
|| `dpe_counting_convention` | enum | Calendar days vs. trading days: calendar_days, trading_days, session_days. See §6c. |
|| `anchor_day_policy` | enum | How the earnings date anchors the DPE count: earnings_date_anchor, announcement_time_anchor, custom |
|| `entry_window_start` | string | When the entry window opens relative to DPE (e.g., "session_open", "decision_time") |
|| `entry_window_end` | string | When the entry window closes relative to DPE (e.g., "session_close", "last_trade_before_earnings") |
|| `dpe_tolerance` | integer | Tolerance in DPE units for dynamic adjustment |

### 6b. exit_dpe_policy

The `exit_dpe_policy` object defines the Days to Earnings targeting for exit and IV crush measurement:

||| Field | Type | Description |
||-------|------|-------------|
|| `exit_dpe_min` | integer | Minimum DPE for exit (inclusive), e.g., 0 = on earnings date |
|| `exit_dpe_max` | integer | Maximum DPE for exit (inclusive), e.g., 30 = 30 days after earnings |
|| `dpe_counting_convention` | enum | Calendar days vs. trading days: calendar_days, trading_days, session_days. See §6c. |
|| `anchor_day_policy` | enum | How the earnings date anchors the DPE count: earnings_date_anchor, announcement_time_anchor, custom |
|| `iv_collapse_threshold` | number | IV crush threshold for early exit (e.g., 0.50 = exit when IV collapses 50%) |
|| `post_earnings_window_unit` | enum | Unit for post-earnings window: dpe, sessions, calendar_days |
|| `exit_trigger_policy` | enum | What triggers exit: dpe_exit, iv_collapse, time_exit, profit_target, stop_loss, custom |

### 6c. dpe_counting_convention

Defines how DPE (Days to Earnings) is counted.

||| Value | Description |
||-------|-------------|
|| `calendar_days` | Count all calendar days including weekends and holidays |
|| `trading_days` | Count only trading days (NYSE/NASdaq business days) |
|| `session_days` | Count only regular trading sessions |

---

## 7. IV Crush Policy

### 7a. iv_crush_policy

The `iv_crush_policy` object defines how IV crush is characterized and measured:

||| Field | Type | Description |
||-------|------|-------------|
|| `iv_crush_measurement_window` | object | Window for measuring IV crush: `start` (DPE), `end` (DPE), `unit` (dpe/sessions/calendar_days) |
|| `iv_crush_definition` | enum | How crush is defined: absolute_iv_drop, percent_iv_drop, iv_rank_collapse, iv_percentile_collapse, custom |
|| `iv_crush_magnitude_estimate` | number | Estimated crush magnitude for planning (e.g., 0.35 = 35% IV drop) |
|| `iv_hierarchy_policy` | object | Which IV source to use: `primary` (IV raw, IV rank, IV percentile), `fallback` sources |
|| `iv_pre_event_source` | enum | Source for pre-event IV: iv_at_entry, iv_rank_at_entry, iv_percentile_at_entry, custom |
|| `iv_post_event_source` | enum | Source for post-event IV: iv_at_exit, iv_rank_at_exit, iv_percentile_at_exit, custom |
|| `crush_confirm_window` | object | Window for confirming crush occurred: `start`, `end`, `unit` |
|| `iv_surface_ref` | string | Reference to volatility surface artifact for IV/IV percentile lookups |

### 7b. iv_crush_definition

Defines how IV crush is quantified.

||| Value | Description |
||-------|-------------|
|| `absolute_iv_drop` | IV drops by an absolute amount (e.g., IV goes from 80% to 40%) |
|| `percent_iv_drop` | IV drops by a percentage (e.g., IV drops 50% from baseline) |
|| `iv_rank_collapse` | IV rank collapses (e.g., rank goes from 0.90 to 0.30) |
|| `iv_percentile_collapse` | IV percentile collapses (e.g., percentile goes from 95 to 20) |
|| `custom` | Domain-specific crush definition in extension_hooks |

---

## 8. BMO/AMC Session Semantics

### 8a. Why BMO/AMC Belong in PreEarningsProfile

BMO (Before Market Open) and AMC (After Market Close) are US equity session conventions for earnings announcements. They determine:
- When the earnings announcement is released
- When the pre-event entry window opens and closes
- When positions must be flat relative to the announcement
- How the gap risk is calculated (overnight gap vs. session gap)

BMO and AMC are not universal conventions:
- Index options (SPX) have different expiration and session structures
- Macro release events (CPI, FOMC) have their own announcement schedules
- Crypto options operate on 24/7 markets with no BMO/AMC distinction
- ETF options inherit the underlying's session structure

By placing BMO/AMC semantics in PreEarningsProfile rather than in the core EventStudySpec or OptionsEventRiskSpec, those core schemas remain reusable for non-pre-earnings options experiments.

### 8b. BMO Session Anchor

For BMO earnings (announcement before market open):

```
Session timeline (relative to earnings date T):
  T-1 session close:  Pre-event entry window may open
  T morning (pre-market):  Earnings announcement released
  T market open:  Gap risk begins; position may gap
  T session close:  Post-event measurement window begins
  T+DPE:  Exit window based on DPE targeting
```

BMO-specific PreEarningsProfile policies:
- `exit_before_session`: Exit before pre-market session ends (market open)
- `enter_after_session`: Enter after market open; no pre-market entry
- `gap_exposure_policy`: Gap risk is overnight + session gap combined

### 8c. AMC Session Anchor

For AMC earnings (announcement after market close):

```
Session timeline (relative to earnings date T):
  T morning:  Pre-event entry window open
  T session close:  Pre-event entry window closes
  T after-hours:  Earnings announcement released
  T+1 morning:  Gap risk begins; position may gap at open
  T+1 session close:  Post-event measurement window ends
  T+1+DPE:  Exit window based on DPE targeting
```

AMC-specific PreEarningsProfile policies:
- `exit_before_session`: Exit before after-hours session (market close of T)
- `enter_after_session`: Enter after market open on T+1; no overnight hold
- `gap_exposure_policy`: Gap risk is session gap only (no overnight component if exited before close)

### 8d. Session Anchor to Domain-Neutral Translation

PreEarningsProfile translates between BMO/AMC session semantics and the domain-neutral `event_anchor_timestamp` used by EventStudySpec:

```
EventStudySpec.event_anchor_timestamp
  → PreEarningsProfile.session_anchor_policy resolves to:
    → BMO: anchor = pre-market announcement time (e.g., 06:30 ET)
    → AMC: anchor = after-hours announcement time (e.g., 16:00 ET)
```

The domain-neutral `gap_exposure_policy` values from OptionsEventRiskSpec are specialized:
- `exit_before_event_anchor` → `exit_before_session` (BMO: before open, AMC: before close)
- `enter_after_event_anchor` → `enter_after_session` (BMO: after open, AMC: after open next day)

---

## 9. Relationship to OptionsEventRiskSpec Gap Policy

### 9a. Specialization Chain

PreEarningsProfile sits above OptionsEventRiskSpec in the specialization hierarchy:

```
EventStudySpec (domain-neutral event-alignment)
  └── OptionsEventRiskSpec (domain-neutral options event-risk)
        └── PreEarningsProfile (pre-earnings specialization)
```

OptionsEventRiskSpec provides the domain-neutral hooks:
- `gap_exposure_policy` with values: `allow_gap_hold`, `prohibit_gap_hold`, `exit_before_event_anchor`, `enter_after_event_anchor`
- `preearnings_profile_refs` → PreEarningsProfile.preearnings_profile_id

PreEarningsProfile specializes these hooks with earnings-specific semantics:
- `gap_exposure_policy` (earnings-specific): `allow_gap_hold`, `prohibit_gap_hold`, `exit_before_session`, `enter_after_session`
- Own fields: `session_anchor_policy`, `entry_dpe_policy`, `exit_dpe_policy`, `iv_crush_policy`

### 9b. Gap Policy Alignment

The PreEarningsProfile `gap_exposure_policy` values map to OptionsEventRiskSpec `gap_exposure_policy` values:

| PreEarningsProfile (earnings-specific) | OptionsEventRiskSpec (domain-neutral) | Semantics |
|---------------------------------------|---------------------------------------|-----------|
| `allow_gap_hold` | `allow_gap_hold` | Hold through announcement; accept gap risk |
| `prohibit_gap_hold` | `prohibit_gap_hold` | Exit before announcement; prohibit holding |
| `exit_before_session` | `exit_before_event_anchor` | Exit before session anchor (earnings-specific) |
| `enter_after_session` | `enter_after_event_anchor` | Enter after session anchor (earnings-specific) |
| `custom` | `custom` | Domain-specific extension |

### 9c. What OptionsEventRiskSpec Does Not Know About Pre-Earnings

OptionsEventRiskSpec core does not know:
- Whether an event is BMO or AMC
- What DPE means or how to count it
- What IV crush is
- How earnings announcements affect gap risk timing

PreEarningsProfile provides this specialization without modifying OptionsEventRiskSpec.

---

## 10. Boundary: What PreEarningsProfile Does Not Own

PreEarningsProfile declares pre-earnings-specific configuration. It does **not** own any of the following:

### 10a. Event Identity and Timestamp Resolution

PreEarningsProfile does not own:
- Earnings date determination (relies on EventStudySpec)
- Event anchor timestamp (relies on EventStudySpec)
- Event deduplication or collision resolution (relies on EventStudySpec)

### 10b. Option Contract Selection

PreEarningsProfile does not own:
- Delta targeting (relies on OptionsEventRiskSpec)
- Moneyness selection (relies on OptionsEventRiskSpec)
- Expiry selection beyond DPE (relies on OptionsEventRiskSpec)
- Contract count limits (relies on OptionsEventRiskSpec)

### 10c. Liquidity and Pricing

PreEarningsProfile does not own:
- Minimum option price requirements (relies on OptionsEventRiskSpec)
- Bid-ask spread requirements (relies on OptionsEventRiskSpec)
- Fill price basis (relies on OptionsEventRiskSpec)
- Slippage and commission (relies on OptionsEventRiskSpec)

### 10d. Outcome Measurement

PreEarningsProfile does not own:
- Return calculation methodology (relies on OutcomeSpec)
- Benchmark policy (relies on OutcomeSpec)
- Observation counting (relies on OutcomeSpec)
- Purge/embargo policy (relies on OutcomeSpec)

### 10e. Statistical Assessment

PreEarningsProfile does not own:
- PBO estimation (relies on ModelAssessmentSpec)
- DSR calculation (relies on ModelAssessmentSpec)
- Sharpe haircut (relies on ModelAssessmentSpec)
- Overfit discount (relies on ModelAssessmentSpec)

### 10f. Trial Accounting

PreEarningsProfile does not own:
- Trial budget (relies on SearchSpaceManifest)
- Parameter constraints (relies on SearchSpaceManifest)
- Trial promotion rules (relies on TrialLedger)

### 10g. Hypothesis Advancement

PreEarningsProfile does not own:
- Hypothesis status changes (relies on EdgeHypothesisRegistry)
- Review packet decisions (relies on ReviewPacket)

---

## 11. Design Consistency with OptionsEventRiskSpec

### 11a. Mirror Structure

PreEarningsProfile mirrors the specialization structure of OptionsEventRiskSpec:

```
OptionsEventRiskSpec                    PreEarningsProfile
─────────────────────                   ───────────────────
options_event_risk_spec_id              preearnings_profile_id
options_event_risk_version              preearnings_profile_version
event_study_spec_ref                    event_study_spec_ref
instrument_universe_ref                 instrument_universe_ref
outcome_spec_refs                       outcome_spec_refs
option_universe_policy                  (inherited from OER)
contract_selection_policy               (inherited from OER)
expiry_selection_policy                 (specialized via entry_dpe_policy, exit_dpe_policy)
moneyness_selection_policy              (inherited from OER)
option_side_policy                      (inherited from OER)
liquidity_policy                        (inherited from OER)
pricing_policy                          (inherited from OER)
gap_exposure_policy                     gap_exposure_policy (specialized for BMO/AMC)
quote_quality_policy                    (inherited from OER)
hypothesis_id                           hypothesis_id
created_at                             created_at
reviewer                               reviewer
preearnings_profile_refs                ─────────────────────────────────────
domain_profile_refs                     (PreEarningsProfile IS a domain profile)
```

### 11b. Example PreEarningsProfile Specialization

This example shows how PreEarningsProfile specializes a generic OptionsEventRiskSpec. The PreEarningsProfile referenced by `preearnings_profile_refs` contains the BMO/AMC session rules and DPE targeting specifics:

```yaml
# OptionsEventRiskSpec OER-2026-0001 (domain-neutral core)
options_event_risk_spec_id: OER-2026-0001
event_study_spec_ref: EVS-2026-0001
option_universe_policy: listed_equity_options
contract_selection_policy:
  selection_method: delta_bucket
  delta_targets: [0.30, 0.50]
gap_exposure_policy: exit_before_event_anchor
preearnings_profile_refs: [PEP-2026-0001]

# PreEarningsProfile PEP-2026-0001 (specializes core for pre-earnings)
preearnings_profile_id: PEP-2026-0001
event_study_spec_ref: EVS-2026-0001
options_event_risk_ref: OER-2026-0001
session_anchor_policy: amc_only
earnings_time_reference: confirmed_after_hours
entry_dpe_policy:
  entry_dpe_min: 2
  entry_dpe_max: 5
  dpe_counting_convention: trading_days
  anchor_day_policy: earnings_date_anchor
  entry_window_start: session_open
  entry_window_end: session_close
exit_dpe_policy:
  exit_dpe_min: 0
  exit_dpe_max: 30
  dpe_counting_convention: trading_days
  anchor_day_policy: earnings_date_anchor
  iv_collapse_threshold: 0.50
  post_earnings_window_unit: dpe
  exit_trigger_policy: dpe_exit
iv_crush_policy:
  iv_crush_measurement_window:
    start: 0
    end: 5
    unit: dpe
  iv_crush_definition: percent_iv_drop
  iv_crush_magnitude_estimate: 0.35
  iv_pre_event_source: iv_rank_at_entry
  iv_post_event_source: iv_percentile_at_exit
gap_exposure_policy: exit_before_session
```

The core OptionsEventRiskSpec does not contain `session_anchor_policy`, `entry_dpe_policy`, `exit_dpe_policy`, or `iv_crush_policy`. These are pre-earnings-specific and live in PreEarningsProfile.

---

## 12. Deferred Items (Not in v1)

The following are deferred to future PRs:

- PreEarningsProfile v1 schema (PR #131)
- PreEarningsProfile v1 fixtures (PR #132)
- PreEarningsProfile v1 local validator, tests, and CI wiring (PRs #133–#134)
- PreEarningsProfile v2 extensions (IV surface integration, earnings surprise weighting, analyst revision integration)

---

## 13. Stop Rules Alignment

PreEarningsProfile v1 is subject to the AED stop rules:

- **No autonomous search**: PreEarningsProfile is a design-time declaration, not an autonomous agent
- **No automatic registry mutation**: PreEarningsProfile does not change EdgeHypothesisRegistry status
- **No automated promotion**: PreEarningsProfile does not promote trials without human review
- **No live trading**: PreEarningsProfile is a research module, not a trading system

---

## 14. Literature Alignment

PreEarningsProfile v1 design is informed by the literature requirements documented in `docs/literature_requirements_for_aed.md`:

- López de Prado (AFML): Stationarity testing, CV-based assessment, minimum trials
- Bailey/Borwein/López de Prado/Zhu (PBO): PBO threshold alignment
- Ilmanen (Expected Returns): Regime awareness, earnings IV term structure
- Montgomery (DOE): Experiment design for earnings IV experiments

PreEarningsProfile does not implement these — it provides the pre-earnings-specific configuration context for experiments that implement them via ModelAssessmentSpec and OutcomeSpec.
