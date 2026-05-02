# OptionsEventRiskSpec v1 Design

**Design date:** 2026-05-02
**PR:** #119
**Governing documents:**
- [`docs/domain_neutral_aed_architecture.md`](./domain_neutral_aed_architecture.md) — AED core domain-neutral principles, boundary rule, generalized abstractions, agent tooling, and stop rules
- [`docs/domain_neutral_modularity_audit.md`](./domain_neutral_modularity_audit.md) — modularity audit confirming governance layer is domain-neutral; engine/ is expected pre-earnings coupling
- [`docs/literature_requirements_for_aed.md`](./literature_requirements_for_aed.md) — literature requirements for options event-risk including IV ramp, jump exposure, crush, skew, term structure, and execution realism
- [`docs/event_study_spec_v1_design.md`](./event_study_spec_v1_design.md) — EventStudySpec v1: event-alignment contract that OptionsEventRiskSpec specializes

---

## 1. Purpose

OptionsEventRiskSpec v1 defines the options-specific event-risk experiment configuration. It is a domain-specific specialization of EventStudySpec for listed equity options, index options, ETF options, futures options, and crypto options event-risk experiments.

OptionsEventRiskSpec answers:
- Which option contracts are eligible for the event-risk experiment?
- How are expiry, moneyness, delta, option side, and spread structures selected?
- How are liquidity, quote quality, stale prices, and bid-ask spreads handled in the options context?
- How are option-specific outcomes connected to OutcomeSpec and ModelAssessmentSpec?
- How does event alignment derive from EventStudySpec?

OptionsEventRiskSpec is a **design-time declaration** of options event-risk constraints. It is committed to the repository before any trial data is generated. It does not contain runtime signals, greeks values, option selections, rankings, or assessment outputs.

OptionsEventRiskSpec is **options-domain-specific but not pre-earnings-only**. It supports multiple options event-risk families including earnings options, macro release options, central bank event options, index rebalance options, regulatory event options, ETF event options, crypto options event risk, and custom options event risk. Pre-earnings-specific semantics live in PreEarningsProfile, not in this core document.

---

## 2. Relationship to AED Artifacts

### 2a. EventStudySpec

EventStudySpec declares the event-alignment structure — event families, window boundaries, anchor timestamps, leakage policies, collision and deduplication rules. OptionsEventRiskSpec **specializes** EventStudySpec for options. It does not replace or replicate event-alignment logic:

```
OptionsEventRiskSpec.event_study_spec_ref → EventStudySpec.event_study_spec_id
```

OptionsEventRiskSpec holds the options-specific layer that sits above EventStudySpec. EventStudySpec owns event timing, window construction, and leakage control. OptionsEventRiskSpec owns option universe policy, contract selection, pricing, liquidity, and gap-exposure handling for the options context.

One EventStudySpec may be referenced by multiple OptionsEventRiskSpecs covering different option structures (e.g., ATM straddles around earnings vs. OTM puts around macro releases).

### 2b. InstrumentUniverseSpec

InstrumentUniverseSpec declares which underlying instruments are eligible (equities, ETFs, futures, crypto). OptionsEventRiskSpec declares how option contracts are selected from those underlyings. They are independent layers:

```
ExperimentSpec.instrument_universe_id → InstrumentUniverseSpec.instrument_universe_id
OptionsEventRiskSpec.instrument_universe_ref → InstrumentUniverseSpec.instrument_universe_id
```

InstrumentUniverseSpec does not own option contract selection. OptionsEventRiskSpec does not own instrument eligibility.

### 2c. OutcomeSpec

OutcomeSpec defines what metric is measured over what window. OptionsEventRiskSpec defines which option contracts enter the measurement and how pricing and liquidity are handled. They are independent sibling declarations under ExperimentSpec:

```
ExperimentSpec.outcome_spec_id → OutcomeSpec.outcome_spec_id
OptionsEventRiskSpec.outcome_spec_refs → OutcomeSpec.outcome_spec_id
```

OutcomeSpec does not own option contract selection or options pricing logic. OptionsEventRiskSpec provides the options context for outcome measurement.

### 2d. ExperimentSpec

ExperimentSpec declares the overall experiment plan and references an EventStudySpec for event-alignment and an OptionsEventRiskSpec for options-specific configuration:

```
ExperimentSpec.event_study_ref → EventStudySpec.event_study_spec_id
ExperimentSpec.options_event_risk_ref → OptionsEventRiskSpec.options_event_risk_spec_id
```

ExperimentSpec does not compute event alignment or option selection — it references the declarations.

### 2e. SearchSpaceManifest

SearchSpaceManifest declares trial generation budget and parameter constraints. OptionsEventRiskSpec declares options-specific configuration. They are independent:

```
ExperimentSpec.search_space_id → SearchSpaceManifest.search_space_id
OptionsEventRiskSpec.* → (independent options configuration)
```

SearchSpaceManifest does not own option selection or options pricing.

### 2f. TrialLedger

TrialLedger records individual trial results. OptionsEventRiskSpec provides the options configuration context for those trials:

```
TrialLedger.options_event_risk_ref → OptionsEventRiskSpec.options_event_risk_spec_id (informational)
```

TrialLedger records trial execution. OptionsEventRiskSpec declares the options event-risk contract under which those trials were structured.

### 2g. ModelAssessmentSpec

ModelAssessmentSpec computes statistical assessment outputs (PBO, DSR, Sharpe haircuts, overfit discounts). OptionsEventRiskSpec does not own these outputs — it only provides the options context:

```
ModelAssessmentSpec.options_event_risk_ref → OptionsEventRiskSpec.options_event_risk_spec_id (informational)
```

OptionsEventRiskSpec does not own `pbo_estimate`, `dsr_estimate`, `sharpe_haircut`, `overfit_discount`, or any computed assessment metric. These belong to ModelAssessmentSpec.

### 2h. EdgeHypothesisRegistry

EdgeHypothesisRegistry holds the hypothesis being tested. OptionsEventRiskSpec constrains the options event-risk experiment structure but does not advance hypothesis status:

```
OptionsEventRiskSpec.hypothesis_id → EdgeHypothesisRegistry.hypothesis_id (informational)
```

OptionsEventRiskSpec may record which hypothesis motivated the options event-risk design, but it does not change hypothesis status.

### 2i. Runner Outputs

Runner outputs (equity curves, performance series, greeks ladders, null-model comparisons) are runtime artifacts computed against the option contracts and event windows declared by OptionsEventRiskSpec. Runner outputs do not own options event-risk rules:

```
RunnerOutput.options_event_risk_ref → OptionsEventRiskSpec.options_event_risk_spec_id
```

Runner outputs reference OptionsEventRiskSpec for provenance; OptionsEventRiskSpec does not reference runner outputs.

### 2j. ReviewPacket

ReviewPacket renders a human judgment on hypothesis advancement. OptionsEventRiskSpec provides the options event-risk context for that judgment, but does not own the decision:

```
ReviewPacket.options_event_risk_ref → OptionsEventRiskSpec.options_event_risk_spec_id (informational)
```

### 2k. PreEarningsProfile

PreEarningsProfile is a domain-specific profile that provides pre-earnings-specific URI resolutions and semantics for abstract references declared in EventStudySpec and OptionsEventRiskSpec. PreEarningsProfile specializes BMO (Before Market Open) and AMC (After Market Close) session semantics, DPE (Days to Earnings) targeting, and earnings-specific gap-exposure rules:

```
OptionsEventRiskSpec.preearnings_profile_refs → PreEarningsProfile.preearnings_profile_id
```

PreEarningsProfile does not modify OptionsEventRiskSpec. It provides domain-specific enrichments that sit above the core OptionsEventRiskSpec boundary. The core OptionsEventRiskSpec does not contain BMO/AMC fields or earnings-specific DPE targeting as core fields — those belong in PreEarningsProfile.

### 2l. Domain Profiles

Domain profiles (PreEarningsProfile, MacroEventProfile, ETFEventProfile, CryptoOptionsProfile, etc.) provide domain-specific URI resolutions for abstract references. OptionsEventRiskSpec supports `domain_profile_refs` and `preearnings_profile_refs` to allow domain-specific options event-risk logic without hard-coding any single domain into the core schema:

```
OptionsEventRiskSpec.domain_profile_refs → DomainProfile.domain_profile_id
OptionsEventRiskSpec.preearnings_profile_refs → PreEarningsProfile.preearnings_profile_id
```

Domain profiles do not modify OptionsEventRiskSpec. They provide domain-specific enrichments.

---

## 3. Proposed Required Fields

These fields define OptionsEventRiskSpec v1. Implementation is deferred to a later schema PR.

|| Field | Type | Description |
|-------|------|-------------|
| `options_event_risk_spec_id` | string | Canonical ID, format OER-YYYY-NNNN |
| `options_event_risk_version` | integer | Semantic version integer, ≥ 1 |
| `event_study_spec_ref` | string | Reference to EventStudySpec providing event-alignment, format EVS-YYYY-NNNN |
| `instrument_universe_ref` | string | Reference to InstrumentUniverseSpec for underlying instruments, format IUS-YYYY-NNNN |
| `outcome_spec_refs` | array[string] | References to OutcomeSpecs measuring outcomes in this options experiment, format OUT-YYYY-NNNN |
| `option_universe_policy` | enum | Asset class of options: listed_equity_options, index_options, etf_options, futures_options, crypto_options, custom. See §5a. |
| `contract_selection_policy` | object | How option contracts are selected (delta, moneyness, strike, premium). See §6. |
| `expiry_selection_policy` | object | How option expiry is selected relative to event anchor. See §6. |
| `moneyness_selection_policy` | object | How moneyness is targeted and bounded. See §6. |
| `option_side_policy` | enum | Which option sides are included: calls_only, puts_only, calls_and_puts, straddle, strangle, vertical_spread, calendar_spread, custom. See §5e. |
| `strategy_structure_policy` | enum | Strategy structure: single_leg, two_leg_spread, multi_leg_spread, delta_neutral, volatility_structure, custom. See §5f. |
| `liquidity_policy` | object | Minimum option price, max spread, min open interest, min volume, stale quote handling. See §7. |
| `pricing_policy` | object | Fill price basis, spread penalty, slippage, quote timestamp policy. See §8. |
| `execution_timing_policy` | enum | When fills are evaluated: decision_timestamp, event_anchor_relative, session_open, session_close, next_tradable_quote, custom. See §5h. |
| `gap_exposure_policy` | enum | Whether strategy may hold across event anchor: allow_gap_hold, prohibit_gap_hold, exit_before_event_anchor, enter_after_event_anchor, custom. See §5i. |
| `quote_quality_policy` | object | NBBO requirement, stale quote handling, missing greeks policy. See §5j. |
| `created_at` | string | ISO 8601 timestamp of options event-risk declaration |
| `reviewer` | object | Reviewer identity with `reviewer_id` (string) and optional `reviewer_name` (string) |

---

## 4. Proposed Optional Fields and Hooks

|| Field | Type | Description |
|-------|------|-------------|
| `underlying_price_ref` | string | Reference to a price data manifest for underlying spot/futures prices |
| `volatility_surface_ref` | string | Reference to a volatility surface artifact for IV/IV percentile lookups |
| `greeks_policy` | object | Delta, gamma, theta, vega, rho computation and reporting requirements |
| `iv_policy` | object | Implied volatility handling: IV rank, IV percentile, IV crush modeling |
| `skew_policy` | object | Volatility skew handling across moneyness |
| `spread_construction_policy` | object | Bid-ask spread construction, spread penalty application |
| `hedge_policy` | object | Delta hedging, gamma scalping, portfolio-level hedging instructions |
| `assignment_exercise_policy` | enum | How early assignment or exercise risk is handled: auto_hedge, allow_assignment, exercised_only, custom |
| `corporate_action_policy` | enum | How corporate actions affect option contracts: adjust_strikes, adjust_quantity, use_adjusted, reject_on_ca, custom |
| `expiration_calendar_ref` | string | Reference to an expiration calendar artifact for index and equity options |
| `event_session_policy` | enum | Which session(s) apply to option event-risk: regular, extended, pre_market, after_hours, overnight, all_sessions. Relates to EventStudySpec.market_session_policy |
| `domain_profile_refs` | array[string] | References to domain profiles providing domain-specific options enrichments |
| `preearnings_profile_refs` | array[string] | References to PreEarningsProfile for pre-earnings-specific options semantics |
| `runner_output_refs` | array[string] | References to runner outputs produced under this options event-risk spec |
| `review_packet_refs` | array[string] | References to ReviewPackets that evaluated hypotheses using this spec |
| `extension_hooks` | object | Optional extension object for future domain-specific fields |
| `notes` | string | Human-readable notes about the options event-risk design rationale |

---

## 5. Proposed Enums

### 5a. option_universe_policy

Defines the asset class of the options universe.

|| Value | Description |
|-------|-------------|
| `listed_equity_options` | Listed equity options (individual stock options) |
| `index_options` | Index options (SPX, RUT, etc.) |
| `etf_options` | ETF options (SPY, QQQ, IWM, etc.) |
| `futures_options` | Options on futures contracts |
| `crypto_options` | Listed crypto options where data supports it |
| `custom` | Domain-specific options universe defined in a domain profile |

### 5b. contract_selection_policy

Defines how individual option contracts are selected from the eligible universe.

|| Value | Description |
|-------|-------------|
| `delta_bucket` | Select contracts by delta range (e.g., Δ = 0.50, Δ = 0.25) |
| `moneyness_bucket` | Select contracts by moneyness (ATM, OTM, ITM at fixed percentiles) |
| `strike_offset` | Select strikes at fixed offset from spot or forward |
| `premium_range` | Select contracts within a premium range |
| `nearest_liquid_contract` | Select the nearest liquid contract by open interest or volume |
| `custom` | Domain-specific contract selection defined in a domain profile |

### 5c. expiry_selection_policy

Defines how option expiry is selected relative to the event anchor.

|| Value | Description |
|-------|-------------|
| `nearest_after_event` | Select the nearest expiry after the event anchor |
| `nearest_before_event` | Select the nearest expiry before the event anchor |
| `fixed_dte_range` | Select expiries within a fixed DTE range around the event |
| `expiry_rank` | Select by expiry rank (e.g., monthly, weekly) relative to event |
| `monthly_only` | Restrict to monthly expiries only |
| `weekly_allowed` | Allow weekly expiries in addition to monthlies |
| `custom` | Domain-specific expiry selection defined in a domain profile |

### 5d. moneyness_selection_policy

Defines how moneyness is targeted and bounded.

|| Value | Description |
|-------|-------------|
| `atm` | At-the-money contracts (spot or forward price) |
| `otm` | Out-of-the-money contracts (put: strike < spot; call: strike > spot) |
| `itm` | In-the-money contracts (put: strike > spot; call: strike < spot) |
| `delta_targeted` | Select by target delta (e.g., Δ = 0.30, Δ = 0.10) |
| `percent_moneyness` | Select by percent moneyness (e.g., 95%, 105% of spot) |
| `custom` | Domain-specific moneyness selection defined in a domain profile |

### 5e. option_side_policy

Defines which option sides are included in the event-risk experiment.

|| Value | Description |
|-------|-------------|
| `calls_only` | Only long call positions |
| `puts_only` | Only long put positions |
| `calls_and_puts` | Both calls and puts included as separate observations |
| `straddle` | Long straddle (one call + one put at same strike) |
| `strangle` | Long strangle (OTM call + OTM put) |
| `vertical_spread` | Vertical spread (one long + one short at adjacent strikes) |
| `calendar_spread` | Calendar spread (short near-term + long far-term at same strike) |
| `custom` | Domain-specific structure defined in a domain profile |

### 5f. strategy_structure_policy

Defines the overall strategy structure.

|| Value | Description |
|-------|-------------|
| `single_leg` | Single option position |
| `two_leg_spread` | Two-leg spread (vertical, straddle, strangle) |
| `multi_leg_spread` | Three or more legs (butterfly, iron condor, calendar) |
| `delta_neutral` | Delta-neutral structure with dynamic delta hedging |
| `volatility_structure` | Structure targeting volatility exposure (VIX event, IV crush) |
| `custom` | Domain-specific structure defined in a domain profile |

### 5g. pricing_policy

Defines how option prices are determined for fill estimation.

|| Value | Description |
|-------|-------------|
| `mid` | Midpoint of bid and ask |
| `bid` | Bid price (conservative) |
| `ask` | Ask price (conservative) |
| `conservative_fill` | Worst-case within spread for the direction of trade |
| `spread_penalized_mid` | Midpoint minus a spread penalty in basis points |
| `custom` | Domain-specific pricing defined in a domain profile |

### 5h. execution_timing_policy

Defines when fills are evaluated relative to the event anchor and decision timestamp.

|| Value | Description |
|-------|-------------|
| `decision_timestamp` | Fill evaluated at the decision timestamp declared in EventStudySpec |
| `event_anchor_relative` | Fill evaluated relative to the event anchor (e.g., at anchor ± N seconds) |
| `session_open` | Fill evaluated at the session open price |
| `session_close` | Fill evaluated at the session close price |
| `next_tradable_quote` | Fill evaluated at the next available quote after the trigger |
| `custom` | Domain-specific timing defined in a domain profile |

### 5i. gap_exposure_policy

Defines whether the strategy may hold across the event anchor and how gap risk is handled. This is the primary policy for controlling whether an options position is held through the event or exited before it.

|| Value | Description |
|-------|-------------|
| `allow_gap_hold` | Strategy may hold across the event anchor; gap risk is accepted |
| `prohibit_gap_hold` | Strategy must not hold across the event anchor; exit before anchor is required |
| `exit_before_event_anchor` | All positions must be closed before the event anchor timestamp |
| `enter_after_event_anchor` | Entry only after event anchor; pre-event position must be flat at anchor |
| `custom` | Domain-specific gap policy defined in a domain profile |

The domain-neutral form of this policy (`exit_before_event_anchor`, `enter_after_event_anchor`) does not use pre-earnings-specific language like "exit before BMO" or "no overnight hold into earnings." PreEarningsProfile specializes this with BMO/AMC session semantics when needed.

### 5j. quote_quality_policy

Defines quote quality requirements for option data.

|| Value | Description |
|-------|-------------|
| `require_bid_ask` | Both bid and ask must be present and valid |
| `allow_mid_only` | Midpoint quotes acceptable when bid-ask is wide |
| `reject_stale_quotes` | Quotes older than `max_quote_age_seconds` are rejected |
| `require_open_interest` | Minimum open interest threshold must be met |
| `custom` | Domain-specific quote quality defined in a domain profile |

---

## 6. Contract Selection Structures

### 6a. contract_selection_policy fields

The `contract_selection_policy` object defines how individual option contracts are selected:

|| Field | Type | Description |
|-------|------|-------------|
| `selection_method` | enum | Method from §5b |
| `delta_targets` | array[number] | Target delta values (e.g., [0.50, 0.30, 0.10]) |
| `moneyness_bands` | object | Moneyness bands: `otm_pct` (e.g., 5, 10, 15), `itm_pct` (e.g., 5, 10) |
| `dte_range` | object | Min and max DTE: `min_dte`, `max_dte` |
| `expiry_ranks` | array[integer] | Expiry ranks to include (1 = nearest, 2 = next, etc.) |
| `strike_selection_rule` | object | Strike selection: `offset_type` (from spot, from forward), `offset_value` |
| `premium_range` | object | Min and max premium: `min_premium`, `max_premium` |
| `option_side` | enum | From §5e |
| `contract_count_limit` | integer | Maximum number of contracts per observation |
| `selection_priority` | array[string] | Priority order when multiple contracts meet criteria (e.g., ["nearest_expiry", "highest_oi"]) |
| `tie_break_policy` | enum | How ties are broken: highest_oi, nearest_expiry, widest_spread, custom |

### 6b. moneyness_selection_policy fields

The `moneyness_selection_policy` object defines moneyness targeting:

|| Field | Type | Description |
|-------|------|-------------|
| `target_type` | enum | From §5d |
| `delta_target` | number | Target delta when `delta_targeted` is selected |
| `percent_moneyness` | number | Percent of spot/forward (e.g., 1.05 for 105% moneyness) |
| `moneyness_bounds` | object | Optional min/max moneyness: `min_moneyness`, `max_moneyness` |
| `reference_price` | enum | Price used for moneyness: `spot`, `forward`, `nearest_future` |

### 6c. expiry_selection_policy fields

The `expiry_selection_policy` object defines expiry selection:

|| Field | Type | Description |
|-------|------|-------------|
| `selection_method` | enum | From §5c |
| `dte_range` | object | DTE bounds: `min_dte`, `max_dte` (applied when `fixed_dte_range` is selected) |
| `expiry_ranks` | array[integer] | Which expiry ranks (applied when `expiry_rank` is selected) |
| `exclude_expiry_ranks` | array[integer] | Expiry ranks to exclude |
| `weekly_allowed` | boolean | Whether weekly expiries are permitted |
| `monthly_only` | boolean | Restrict to monthly expiries only |

---

## 7. Liquidity and Quote-Quality Policies

The `liquidity_policy` object defines minimum liquidity requirements for option contracts:

|| Field | Type | Description |
|-------|------|-------------|
| `min_option_price` | number | Minimum option price (in dollars); reject below |
| `max_option_price` | number | Maximum option price (in dollars); reject above |
| `min_open_interest` | integer | Minimum open interest contracts |
| `min_volume` | integer | Minimum average daily volume |
| `max_bid_ask_spread_abs` | number | Maximum absolute bid-ask spread in dollars |
| `max_bid_ask_spread_pct` | number | Maximum spread as a fraction of mid-price (e.g., 0.10 = 10%) |
| `max_quote_age_seconds` | integer | Maximum age of a valid quote in seconds |
| `require_nbbo` | boolean | Whether NBBO must be respected |
| `stale_quote_policy` | enum | How stale quotes are handled: reject, use_last_valid, interpolate, custom |
| `missing_greeks_policy` | enum | How missing greeks values are handled: reject, use_model, interpolate, custom |
| `liquidity_not_applicable_reason` | string | Human-readable explanation when liquidity policy is waived |

The `quote_quality_policy` object defines quote quality enforcement:

|| Field | Type | Description |
|-------|------|-------------|
| `quality_method` | enum | From §5j |
| `max_quote_age_seconds` | integer | Maximum quote age before rejection |
| `min_spread_pct` | number | Minimum spread as fraction of mid (to detect locked markets) |
| `require_open_interest` | boolean | Whether open interest check is required |
| `min_open_interest_contracts` | integer | Open interest threshold |

---

## 8. Pricing and Execution Policy

The `pricing_policy` object defines how option fills are priced:

|| Field | Type | Description |
|-------|------|-------------|
| `fill_price_basis` | enum | From §5g |
| `spread_penalty_bps` | number | Spread penalty in basis points (when `spread_penalized_mid` is selected) |
| `commission_model_ref` | string | Reference to a commission model artifact |
| `slippage_model_ref` | string | Reference to a slippage model artifact |
| `quote_timestamp_policy` | enum | Which timestamp is used for quote lookup: decision_time, anchor_time, settle_time, custom |
| `entry_quote_policy` | enum | Quote policy for entry: mid, conservative, aggressive, custom |
| `exit_quote_policy` | enum | Quote policy for exit: mid, conservative, aggressive, custom |
| `partial_fill_policy` | enum | How partial fills are handled: fill_available, reject, scale_quantity, custom |
| `multi_leg_execution_policy` | enum | How multi-leg orders are executed: legs_simultaneous, legs_sequential, net_price, custom |

---

## 9. Gap Exposure and Event-Session Policy

### 9a. Holding across the event anchor

The `gap_exposure_policy` field is the primary mechanism for controlling whether an options strategy holds through the event anchor. The enum values `exit_before_event_anchor` and `enter_after_event_anchor` are domain-neutral — they do not assume any specific event type or session structure.

For pre-earnings experiments, PreEarningsProfile specializes this with BMO/AMC session awareness. For macro events, the domain profile handles the specific announcement schedule. The core OptionsEventRiskSpec does not encode any event-specific session knowledge.

### 9b. Domain-neutral gap policy

The gap exposure policy operates on anchor timestamps, not on calendar dates or session names:

- `exit_before_event_anchor`: The position must be flat before the event anchor timestamp. This is expressed in event time, not in pre-earnings-specific terms.
- `enter_after_event_anchor`: The position may only be established after the event anchor. Pre-event exposure is prohibited.
- `allow_gap_hold`: The position may persist across the anchor. Gap risk is explicitly accepted by the strategy design.

PreEarningsProfile later translates `exit_before_event_anchor` into "exit before the BMO session open on the earnings date" for US equity options. The core policy does not need to know this.

### 9c. event_session_policy and EventStudySpec.market_session_policy

`OptionsEventRiskSpec.event_session_policy` records which session(s) the options experiment applies to. It is informational and relates to EventStudySpec's `market_session_policy`:

```
OptionsEventRiskSpec.event_session_policy → Informational context for EventStudySpec.market_session_policy
```

This linkage allows reviewers to see that the options experiment is consistent with the event study's session definition. OptionsEventRiskSpec.event_session_policy does not override EventStudySpec.market_session_policy.

### 9d. Why BMO/AMC and DPE targeting belong in PreEarningsProfile

BMO (Before Market Open) and AMC (After Market Close) are US equity session conventions for earnings announcements. They are not universal — index options, macro event options, and crypto options have different session structures.

Similarly, DPE (Days to Earnings) targeting is a pre-earnings-specific concept. There is no equivalent "Days to FOMC" or "Days to CPI" that uses the same semantics.

By keeping BMO/AMC and DPE targeting out of OptionsEventRiskSpec core fields and placing them in PreEarningsProfile:
- OptionsEventRiskSpec remains reusable for macro release options, index options, ETF options, and crypto options
- PreEarningsProfile can specialize the generic gap policy with pre-earnings-specific session semantics
- No pre-earnings-specific language pollutes the domain-neutral options layer

### 9e. Post-event entry and post-event outcomes

OptionsEventRiskSpec supports post-event entry through the `enter_after_event_anchor` gap exposure policy. The `post_event_window` from EventStudySpec defines the measurement window for post-event outcomes. OutcomeSpec references the same post-event window for return measurement.

For post-event continuation studies (entering after the event anchor and holding through the crush/recovery period), OptionsEventRiskSpec uses `enter_after_event_anchor` combined with an appropriate `post_event_window` from EventStudySpec.

---

## 10. Boundary: What OptionsEventRiskSpec Does Not Own

OptionsEventRiskSpec declares options event-risk configuration. It does **not** own any of the following:

### 10a. Event Identity and Timestamp Resolution

OptionsEventRiskSpec does not own:
- Event identity (earnings date, CPI release date, FOMC date)
- Event anchor timestamp determination
- Event deduplication or collision resolution
- Leakage controls around event timing

These belong to EventStudySpec.

### 10b. Instrument Universe Membership

OptionsEventRiskSpec does not own:
- Which underlyings are eligible
- Corporate action adjustments on the underlying
- Inclusion/exclusion rules for instruments
- Liquidity requirements on the underlying

These belong to InstrumentUniverseSpec.

### 10c. Final Outcome Definitions

OptionsEventRiskSpec does not own:
- Return calculation methodology (arithmetic vs. geometric, simple vs. logarithmic)
- Benchmark comparison method
- Holding period return definition
- Risk-adjusted performance metrics

These belong to OutcomeSpec.

### 10d. Trial Accounting

OptionsEventRiskSpec does not own:
- `selected_variant_id` — which trial variant was selected
- `n_tried` — number of trial variants attempted
- `trial_family_id` — which trial family a trial belongs to
- Promotion and acceptance logic

These belong to TrialLedger and ExperimentSpec.

### 10e. Statistical Assessment Outputs

OptionsEventRiskSpec does not own:
- `pbo_estimate` — probability of backtest overfitting
- `dsr_estimate` — degree of statistical significance
- `sharpe_haircut` — Sharpe ratio overfit adjustment
- `overfit_discount` — general overfit discount factor
- Any other model assessment output

These belong to ModelAssessmentSpec.

### 10f. ReviewPacket Decisions

OptionsEventRiskSpec does not own:
- ReviewPacket or any hypothesis advancement decision
- Approval or rejection rationale
- Status changes in EdgeHypothesisRegistry

These belong to ReviewPacket and EdgeHypothesisRegistry.

### 10g. Pre-Earnings-Specific Semantics as Core Fields

OptionsEventRiskSpec does not own:
- BMO (Before Market Open) session semantics as a core field
- AMC (After Market Close) session semantics as a core field
- DPE (Days to Earnings) targeting as a core field
- Earnings-specific entry/exit DPE rules
- Pre-earnings gap exposure rules

These belong to PreEarningsProfile. OptionsEventRiskSpec provides the domain-neutral hooks (`preearnings_profile_refs`, `gap_exposure_policy` with domain-neutral values) through which PreEarningsProfile specializes the experiment.

### 10h. Provider-Specific Data References as Core Fields

OptionsEventRiskSpec does not own:
- iVolatility or iVol table names
- OptionMetrics or Bloomberg OPT data field names
- Provider-specific pricing model references

These belong to data manifest artifacts and domain profiles. Core OptionsEventRiskSpec uses abstract policy enums, not provider-specific field names.

---

## 11. Conceptual Examples

These examples illustrate how OptionsEventRiskSpec declarations work. All examples use domain-neutral language where possible; pre-earnings-specific details are provided via `preearnings_profile_refs`.

### 11a. Generic Earnings Options Event Risk

```
options_event_risk_spec_id: OER-2026-0001
options_event_risk_version: 1
event_study_spec_ref: EVS-2026-0001
instrument_universe_ref: IUS-2026-0001
outcome_spec_refs: [OUT-2026-0001]
option_universe_policy: listed_equity_options
contract_selection_policy:
  selection_method: delta_bucket
  delta_targets: [0.50, 0.30, 0.10]
  option_side: puts_only
  contract_count_limit: 3
expiry_selection_policy:
  selection_method: nearest_after_event
  min_dte: 5
  max_dte: 60
moneyness_selection_policy:
  target_type: delta_targeted
  delta_target: 0.30
option_side_policy: puts_only
strategy_structure_policy: single_leg
gap_exposure_policy: exit_before_event_anchor
liquidity_policy:
  min_option_price: 0.05
  max_bid_ask_spread_pct: 0.25
  min_open_interest: 50
  require_nbbo: true
pricing_policy:
  fill_price_basis: conservative_fill
  spread_penalty_bps: 25
execution_timing_policy: decision_timestamp
preearnings_profile_refs: [PEP-2026-0001]
domain_profile_refs: [OPTIONS-2026-0001]
```

Note: BMO/AMC entry/exit rules and DPE targeting are defined in `preearnings_profile_refs: [PEP-2026-0001]`, not in this core declaration.

### 11b. Macro-Release Index Options Event Risk

```
options_event_risk_spec_id: OER-2026-0002
options_event_risk_version: 1
event_study_spec_ref: EVS-2026-0002
instrument_universe_ref: IUS-2026-0002
outcome_spec_refs: [OUT-2026-0002]
option_universe_policy: index_options
contract_selection_policy:
  selection_method: moneyness_bucket
  moneyness_bands:
    otm_pct: [5, 10]
    itm_pct: [5]
  option_side: calls_and_puts
  contract_count_limit: 4
expiry_selection_policy:
  selection_method: nearest_after_event
  min_dte: 1
  max_dte: 14
moneyness_selection_policy:
  target_type: percent_moneyness
  percent_moneyness: 1.0
option_side_policy: calls_and_puts
strategy_structure_policy: straddle
gap_exposure_policy: allow_gap_hold
liquidity_policy:
  min_option_price: 0.10
  max_bid_ask_spread_abs: 2.00
  min_open_interest: 500
  require_nbbo: true
pricing_policy:
  fill_price_basis: spread_penalized_mid
  spread_penalty_bps: 15
execution_timing_policy: event_anchor_relative
domain_profile_refs: [MACRO-2026-0001]
```

### 11c. ETF Event Options Event Risk

```
options_event_risk_spec_id: OER-2026-0003
options_event_risk_version: 1
event_study_spec_ref: EVS-2026-0003
instrument_universe_ref: IUS-2026-0003
outcome_spec_refs: [OUT-2026-0003]
option_universe_policy: etf_options
contract_selection_policy:
  selection_method: strike_offset
  strike_selection_rule:
    offset_type: from_spot
    offset_value: 5
  option_side: puts_only
expiry_selection_policy:
  selection_method: fixed_dte_range
  dte_range:
    min_dte: 7
    max_dte: 35
  monthly_only: true
moneyness_selection_policy:
  target_type: percent_moneyness
  percent_moneyness: 0.95
option_side_policy: puts_only
strategy_structure_policy: single_leg
gap_exposure_policy: prohibit_gap_hold
liquidity_policy:
  min_option_price: 0.10
  max_bid_ask_spread_pct: 0.20
  min_open_interest: 200
pricing_policy:
  fill_price_basis: conservative_fill
execution_timing_policy: session_close
domain_profile_refs: [ETF-2026-0001]
```

### 11d. Post-Event Options Continuation Study

```
options_event_risk_spec_id: OER-2026-0004
options_event_risk_version: 1
event_study_spec_ref: EVS-2026-0001
instrument_universe_ref: IUS-2026-0001
outcome_spec_refs: [OUT-2026-0004]
option_universe_policy: listed_equity_options
contract_selection_policy:
  selection_method: delta_bucket
  delta_targets: [0.50]
  option_side: calls_only
expiry_selection_policy:
  selection_method: nearest_after_event
  min_dte: 30
  max_dte: 60
moneyness_selection_policy:
  target_type: delta_targeted
  delta_target: 0.50
option_side_policy: calls_only
strategy_structure_policy: single_leg
gap_exposure_policy: enter_after_event_anchor
liquidity_policy:
  min_option_price: 0.05
  max_bid_ask_spread_pct: 0.30
  require_nbbo: true
pricing_policy:
  fill_price_basis: mid
execution_timing_policy: event_anchor_relative
domain_profile_refs: [POSTEVENT-2026-0001]
```

### 11e. Pre-Earnings Profile Hook Example

This example shows how PreEarningsProfile specializes a generic OptionsEventRiskSpec. The PreEarningsProfile referenced by `preearnings_profile_refs` contains the BMO/AMC session rules and DPE targeting specifics:

```
OptionsEventRiskSpec (core, domain-neutral):
  gap_exposure_policy: exit_before_event_anchor
  expiry_selection_policy:
    selection_method: nearest_after_event
    min_dte: 5
    max_dte: 60
  preearnings_profile_refs: [PEP-2026-0001]

PreEarningsProfile PEP-2026-0001 (specializes core):
  profile_type: pre_earnings
  session_anchor_policy: bmo   # Before Market Open for US equity earnings
  exit_before_session: bmo     # Translates exit_before_event_anchor to BMO exit
  entry_allowed_sessions: [amo, reg]  # Can enter After Market Close or Regular session
  dpe_target_range: [5, 45]    # Days to earnings at entry
  iv_rank_min: 30             # Only enter when IV rank is above 30
  crush_sensitivity: high      # High sensitivity to IV crush post-event
```

The core OptionsEventRiskSpec does not contain `session_anchor_policy`, `dpe_target_range`, or `iv_rank_min`. These are pre-earnings-specific and live in PreEarningsProfile.

---

## 12. Agent/Tooling Layer

OptionsEventRiskSpec is a governance artifact. Hermes and OpenClaw may draft OptionsEventRiskSpecs and suggest missing options liquidity or pricing controls, but operate under the following constraints:

Hermes and OpenClaw **may**:
- Draft OptionsEventRiskSpecs following options-domain-specific construction patterns
- Suggest delta, moneyness, or expiry selections based on event family conventions
- Validate OptionsEventRiskSpecs against the schema once implemented
- Reference existing InstrumentUniverseSpecs, EventStudySpecs, and domain profiles

Hermes and OpenClaw **may not**:
- Approve or advance a hypothesis
- Bypass or disable any validator
- Run unlocked autonomous search, Bayesian optimization, or genetic programming
- Advance hypothesis status in EdgeHypothesisRegistry
- Render a ReviewPacket decision
- Access live trading systems or production execution
- Select specific option contracts, strikes, or expiry ranks based on runtime signals
- Override the gap_exposure_policy or execution_timing_policy with runtime overrides

---

## 13. Validation Roadmap

OptionsEventRiskSpec v1 follows the same implementation pattern as EventStudySpec v1:

1. **Design doc** (PR #119) — describes the field set, enums, contract selection structures, liquidity and pricing policies, gap exposure policy, and boundary (this document)
2. **Schema** (future PR) — JSON schema for OptionsEventRiskSpec v1
3. **Fixtures** (future PR) — valid and invalid JSON fixtures covering all required fields, enums, and boundary conditions
4. **Validator** (future PR) — `scripts/local/validate_options_event_risk_spec.py` implementing the schema rules
5. **Tests** (future PR) — pytest coverage of all validator paths
6. **CI wiring** (future PR) — add to `scripts/ci/validate_governance_manifests.sh`
7. **Docs status update** (future PR) — update `docs/current_project_status.md` and `docs/README.md`

This roadmap follows the same pattern used for TrialLedger, SearchSpaceManifest, ModelAssessmentSpec, EdgeHypothesisRegistry, ExperimentSpec, OutcomeSpec, InstrumentUniverseSpec, and EventStudySpec.

---

## 14. Stop Rules

OptionsEventRiskSpec v1 design respects the AED stop rules:

OptionsEventRiskSpec does **not** enable, unlock, or activate any of the following without an explicit, separately designed governance extension:

- **Autonomous search** — OptionsEventRiskSpec does not trigger or authorize autonomous contract selection or gap exposure construction. `autonomous_search` remains prohibited.
- **Bayesian optimization** — No Bayesian optimization of delta, moneyness, or expiry selection. `bayesian_optimization` remains prohibited.
- **Genetic programming** — No genetic programming of options strategy structure. `genetic_programming` remains prohibited.
- **Automated promotion** — No automated advancement of hypotheses. Human-authored ReviewPacket required.
- **Automated registry mutation** — No automated changes to EdgeHypothesisRegistry status.
- **Live trading** — OptionsEventRiskSpec is a design-time declaration only. It does not authorize live trading or production execution.
- **Production execution** — No production system execution.
- **GCRU integration** — GCRU integration requires a separately designed governance extension. OptionsEventRiskSpec does not include GCRU-specific fields or live feed connections.

These rules apply regardless of whether they are invoked by humans, scripts, or AI agents.

---

## 15. Explicit Non-Scope

This design document does not:
- Implement a JSON schema for OptionsEventRiskSpec
- Implement a validator for OptionsEventRiskSpec
- Create fixtures or tests for OptionsEventRiskSpec
- Modify any governance validator, schema, fixture, or CI helper
- Modify the EventStudySpec, InstrumentUniverseSpec, OutcomeSpec, ExperimentSpec, SearchSpaceManifest, TrialLedger, ModelAssessmentSpec, or EdgeHypothesisRegistry schemas
- Design PreEarningsProfile (this is a separate future PR)
- Change any code in `engine/`, `schemas/`, `scripts/`, `tests/`, or `fixtures/`
- Modify `docs/edge_hypothesis_registry.csv`
