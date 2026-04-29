Event and Options Contract Spec v1

1. Purpose

This document defines a concrete, versioned, docs-only contract for event and options data used by Automated Edge Discovery (AED). It is a canonical, human-readable specification: precise field names, types, canonical join keys, time semantics, and invariants that future validators must enforce. This PR adds only documentation; no JSON Schemas, validators, tests, or runtime code are included.

2. Scope and non scope

Scope
- Define EventDatasetSpec v1, OptionsObservationSpec v1, and OptionsSurfaceSpec v1 as polished contracts for storage, exchange, and validation.
- Specify canonical join keys, time semantics, anti lookahead rules, and handling for corporate actions and symbol changes.

Non-scope
- No runtime code, no validators, no JSON Schema files, and no tests are added by this PR.
- This document does not mutate docs/edge_hypothesis_registry.csv.

3. EventDatasetSpec v1

EventDatasetSpec v1: A row-level record describing a single event identity and its authoritative metadata. Each row represents an event (not an observation). Event identity is separated from observation timestamps.

Required fields (examples and types):
- event_id (string): globally unique identifier for the event.
- event_ticker (string): canonical ticker at event time.
- event_time_utc (ISO 8601 timestamp with timezone): canonical event timestamp for decision-time alignment.
- event_session (enum): {BMO, AMC, INTRA, UNKNOWN}
- event_hold_flag (enum): {no_event_hold, partial_event_hold, full_event_hold, unknown_event_hold}
- gap_exposure (float or enum): measure of expected price gap exposure; can be UNKNOWN.
- event_class (string): short label describing event type (e.g., "earnings", "merger")

Optional fields:
- event_source (string)
- event_description (string)

4. OptionsObservationSpec v1

OptionsObservationSpec v1: A row-level record describing a single option observation at a specific quote timestamp. Observations are separate from event rows and must reference event identity by join key (see section 6).

Required fields (examples and types):
- option_observation_id (string): unique id for the observation row.
- option_ticker (string): option root symbol as listed by exchange at observation time.
- option_contract_symbol (string): full option contract identifier (convention: underlying-YYYYMMDD-C/P-strike).
- option_observation_date (ISO 8601 timestamp with timezone): timestamp of the quote/observation. (See time semantics and anti lookahead rules.)
- bid (numeric), ask (numeric), mid (numeric)
- size_bid (integer), size_ask (integer)
- last_trade_price (numeric) optional
- implied_volatility (float) 
- delta (float) 
- expiry_date (ISO date)
- open_interest (integer)

5. OptionsSurfaceSpec v1

OptionsSurfaceSpec v1: A document-level or file-level artifact describing the options surface generation details applied to a set of OptionsObservationSpec v1 rows. It is not the runtime surface; it is a contract describing how to construct and interpret the surface for that dataset.

Required fields:
- surface_id (string)
- surface_timestamp_utc (ISO 8601)
- underlying_ticker (string)
- method (string): brief name of interpolation/iv estimation technique
- iv_ramp_metric (string/description)
- iv_crush_metric (string/description)
- skew_metric, term_structure_metric (descriptions)
- stale_quote_policy (string)
- MID_WITH_SPREAD_PENALTY (description of how mid is computed when spread present)

6. Canonical join keys

Canonical join keys separate event identity from observation rows to avoid lookahead and cohort selection mistakes.

- event join key: (event_id) 
  - the canonical event join key: the unique event identifier. Implementers may augment with (event_ticker, event_time_utc) for convenience but event_id is canonical.
- observation join key to event: (event_id) present on OptionsObservationSpec v1 rows to indicate the observation belongs to the event cohort.
- option contract canonical key: (option_contract_symbol, expiry_date) and optionally (underlying_ticker) for disambiguation.

Research cohorts and filters must be defined by event identity or event date. Cohorts are selected by event identity or event date; cohorts are selected by event identity, not by raw option observation date alone. See Time semantics below.

7. Time semantics and anti lookahead rules

Deterministic anti lookahead constraints (enforced by future validators):
- Decision time = event_time_utc from EventDatasetSpec v1.
- Features used at decision time must have timestamps <= decision time. Any feature computed using an option_observation_date > decision time is forbidden for that decision.
- Option observations may be collected well before or after the event_time_utc (e.g., pre-earnings observations weeks before, or long-dated options that remain part of cohort). The membership of an observation in an event cohort is governed by event identity, not strictly by calendar year.
- Research cohorts MUST be selected by event_id or event_time_utc window. Selecting by option_observation_date only is not equivalent and may introduce lookahead.
- Anti lookahead: when constructing labels or backtests, ensure that no data with timestamp > label time influences features.

8. Field categories

Required
- From EventDatasetSpec v1: event_id, event_time_utc, event_session, event_hold_flag
- From OptionsObservationSpec v1: option_observation_id, option_contract_symbol, option_observation_date, mid or (bid and ask), expiry_date

Optional
- implied_volatility, delta, size_bid, size_ask, last_trade_price, open_interest, event_description

Derived
- mid (if not provided) computed via MID_WITH_SPREAD_PENALTY policy described in OptionsSurfaceSpec v1
- implied_volatility_surface points 
- derived by interpolation methods (documented in OptionsSurfaceSpec v1)

Forbidden
- Any field that directly encodes a future label (for example: future_profit_next_30d) must be excluded from observation rows. Labels must be derived in downstream label generation steps with anti lookahead checks.

9. Event identity rules

- event_id must be immutable and globally unique within the registry.
- Changing event metadata requires creating a new event_id with an explicit versioning annotation; do not mutate previous event row values.
- event_session must be one of the enumerated values. Use UNKNOWN only when session cannot be determined.

10. Option contract identity rules

- option_contract_symbol must follow a strict canonical format documented in this repo (underlying-YYYYMMDD-C/P-strike). When exchanges use different encodings, map to this canonical form in a preprocessing step (document the mapping in OptionsSurfaceSpec v1).
- expiry_date is the authoritative expiry; if the contract symbol embeds expiry, both must match.
- Delta and implied_volatility, when reported, must state the model or estimator used in metadata (OptionsSurfaceSpec v1).

11. Observation date rules

- option_observation_date timestamps must be timezone-aware ISO 8601 strings in UTC (use Z suffix).
- Observations that occur exactly at the event_time_utc are permitted and considered available at decision time; observations with timestamp > event_time_utc are not allowed to feed decision-time features for that event.
- Option observations may be included in the event cohort even if their observation dates fall outside the calendar year referenced by event_time_utc; cohort membership is by event_id.

12. Price, volume, open interest, implied volatility, delta, and expiry fields

- price fields: bid, ask, mid, last_trade_price 
- numeric. mid must be derived consistently; specify MID_WITH_SPREAD_PENALTY in surface metadata.
- volume fields: size_bid, size_ask, volume 
- integers where available.
- open_interest 
- integer.
- implied_volatility 
- decimal between 0 and 3.0 (validators should enforce reasonable bounds).
- delta 
- float between -1 and 1; sign convention: call positive.
- expiry_date 
- ISO date; clarify timezoneless date (expiry is market date, not timestamp).

13. Corporate action and symbol change handling

- All corporate actions (splits, dividends, ticker changes) must be captured in metadata rows and applied to event and observation rows in pre-processing before submission under this contract.
- If a symbol changes between event_time_utc and observation times, supply both canonical_underlying_ticker and observed_ticker fields; the canonical underlying_ticker is preferred for joins.

14. Missing data policy

- Missing numeric fields must be encoded as NULL and recorded in surface metadata whether they were imputable.
- Downstream validators should flag rows with missing critical fields (event_id, option_contract_symbol, option_observation_date) as invalid.

15. Duplicate handling policy

- Duplicate observation rows (identical option_observation_id) must be rejected.
- Multiple quotes for the same option_contract_symbol at the same option_observation_date are allowed only if size or source differs; canonical deduplication rules (e.g., prefer exchange feed, prefer larger size) must be documented in OptionsSurfaceSpec v1 and enforced by validators.

16. Minimal valid examples

Valid rows (EventDatasetSpec v1):

| event_id | event_ticker | event_time_utc | event_session | event_hold_flag |
|---|---:|---|---|---|
| EV-0001 | AAPL | 2026-07-28T13:30:00Z | BMO | no_event_hold |

Valid rows (OptionsObservationSpec v1):

| option_observation_id | option_contract_symbol | option_observation_date | bid | ask | mid | expiry_date | implied_volatility | delta | event_id |
|---|---|---|---:|---:|---:|---|---:|---:|---|
| OBS-1001 | AAPL-20260820-C-150 | 2026-07-27T14:30:00Z | 1.20 | 1.40 | 1.30 | 2026-08-20 | 0.32 | 0.45 | EV-0001 |

17. Invalid examples

Invalid because of lookahead (option_observation_date after event_time_utc):

| option_observation_id | option_contract_symbol | option_observation_date | event_id | event_time_utc |
|---|---|---|---|---|
| OBS-2001 | AAPL-20260820-C-150 | 2026-07-28T14:30:00Z | EV-0001 | 2026-07-28T13:30:00Z |

Invalid because missing canonical join key:

| option_observation_id | option_contract_symbol | option_observation_date | event_id |
|---|---|---|---|
| OBS-3001 | AAPL-20260820-C-150 | 2026-07-27T14:30:00Z | NULL |

18. Future validator invariants

This PR documents invariants that future validators must enforce (but does not implement them):
- event_id uniqueness
- Decision-time feature timestamps must be <= the applicable decision timestamp (e.g., event_time_utc when event_time_utc is the decision timestamp). Future validators must reject any feature whose timestamp is after the applicable decision timestamp.
- option_observation_date timezone-aware and <= decision time for features
- implied_volatility and delta bounds as specified
- canonical mapping of option_contract_symbol to expiry_date

19. Relationship to existing AED specs

This contract refines and formalizes concepts from docs/event_options_schema_planning_v1.md and links to ModelAssessmentSpec v1, EdgeHypothesisRegistry design, MechanismDiscoveryReport, and PostHocTheoryNote documents for provenance and downstream workflows.

20. Open questions

- Should event_id names be globally namespaced with repository-origin to avoid cross-repo collisions? (Recommendation: yes 
- future validator invariant.)
- Recommended tolerance for stale_quote_policy and how many seconds of staleness constitute a stale quote; surface-level policy required.

Provenance
- Based on docs/event_options_schema_planning_v1.md and related AED docs.

Change log
- v1: initial contract spec; docs-only.
