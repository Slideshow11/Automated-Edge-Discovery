# Options event risk protocol

Purpose
-------
This protocol specifies how Automated Edge Discovery (AED) should specify, run, and review options strategies around scheduled events — especially earnings — before any schema or runtime implementation is added. It documents required metadata, safety guardrails, diagnostics, and manual-review gates for options event risk research.

Non-goals
---------
- This is docs-only: no pricing engine implementation, no Levy model calibration, no schema changes.
- It does not authorize registry mutation, automated promotion, or automated accepted/rejected/killed lifecycle actions.
- It does not prescribe runtime behavior or production deployment details.

Core principle
--------------
Options event strategies must explicitly distinguish between the following phenomena:
- pre-event drift (underlying directional movement)
- pre-event IV ramp (implied volatility elevation before the event)
- event jump exposure (large realized move at announcement)
- post-event IV crush (volatility compression after announcement)
- skew movement (put/call or delta skew changes)
- term-structure movement (front-to-forward expiry changes)
- execution and spread artifacts (bid/ask, stale quotes)

OptionsEventRiskSpec
--------------------
Every options event research artifact must declare an OptionsEventRiskSpec with the following required fields:
- options_event_risk_id
- hypothesis_id
- event_study_id
- event_type
- event_session
- event_timestamp_quality
- entry_date
- exit_date
- event_date
- event_hold_flag
- gap_exposure
- option_type
- option_expiry
- expiry_covers_event
- days_to_expiry_at_entry
- days_to_expiry_at_exit
- delta_bucket
- moneyness
- iv_level
- iv_rank_or_percentile
- skew_metric
- term_structure_metric
- spread_metric
- liquidity_metric
- stale_quote_policy
- fill_model
- residual_jump_risk_note
- review_owner

Event hold classification
-------------------------
OptionsEventRiskSpec must classify how the strategy treats event exposure using event_hold_flag. Standard classes:
- no_event_hold — exit (or hedge) to avoid event jump exposure before announcement
- partial_event_hold — reduced exposure through sizing or partial hedging across event
- full_event_hold — unhedged exposure through the event

No_event_hold, partial_event_hold, and full_event_hold must not be pooled without explicit stratification because they carry materially different jump risk profiles.

Gap exposure
------------
OptionsEventRiskSpec must declare gap_exposure:
- none
- partial
- full
- unknown

Unknown gap_exposure must block any promotion or acceptance decisions until resolved. Gap exposure represents realized-discrete-move risk between close and event timestamp and is a primary residual risk.

IV, skew, and term-structure tracking
-------------------------------------
OptionsEventRiskSpec must track and report:
- IV level
- IV rank or percentile (historical context)
- front-to-next expiry term-structure metric
- put/call skew or delta skew metric
- pre-event skew change and post-event skew crush where applicable

IV level alone is not enough to characterize options event risk. Document how skew and term-structure interact with the strategy.

PnL decomposition
-----------------
Reporting should decompose option PnL into interpretable components where possible:
- delta_component
- vega_component
- theta_component
- skew_component
- term_structure_component
- spread_cost_component
- residual_component

Raw option PnL alone is insufficient for acceptance — decomposition and diagnostics are required to attribute sources of returns and risks.

Liquidity and execution
-----------------------
OptionsEventRiskSpec must declare execution and liquidity controls:
- explicit spread handling and spread_metric
- stale_quote_policy
- open interest or volume thresholds (liquidity_metric)
- penny option exclusion rules when applicable
- fill_model (example conservative: MID_WITH_SPREAD_PENALTY)
- cost sensitivity analyses

MID_WITH_SPREAD_PENALTY is a conservative fill model used as a current recommended baseline where direct fill simulation is unavailable.

Pre-earnings options example
----------------------------
- event_type: earnings_announcement
- event_session: AMC/BMO
- strategy goal: capture pre-event IV ramp and possible drift
- avoided risk: scheduled announcement jump risk
- exit before announcement for no_event_hold strategies
- normal model: event-matched control windows or matched non-event historical windows
- diagnostics and failure modes:
  - stale quotes
  - wide spreads
  - wrong event session or event_date anchoring
  - liquidity collapse
  - IV already priced into options
  - post-event exposure when exit anchors incorrectly

Relationship to existing protocols
----------------------------------
- OptionsEventRiskSpec must link to the Theory-first protocol (HypothesisSpec) and to EventStudySpec (or ExploratoryAnomalySpec) from the event-study design protocol.
- OptionsEventRiskSpec is evidence and operational metadata for options strategies — it does not replace a mechanism and cannot be used to bypass theory-first guardrails.

Implementation gates
--------------------
- No options event strategy review without event_hold_flag declared.
- No options event strategy promotion or acceptance when gap_exposure is unknown. "unknown gap exposure blocks promotion"
- Do not pool no_event_hold with full_event_hold results without stratification.
- No raw option PnL acceptance without IV/skew/term-structure decomposition and execution diagnostics. "raw option PnL alone is insufficient"
- No assumption of perfect hedging. "No assumption of perfect hedging"
- No automated promotion from options event results.
- Future JumpRiskReport must disclose residual jump risk. "Future JumpRiskReport must disclose residual jump risk"

Acknowledgements and references
--------------------------------
Read in conjunction with:
- docs/theory_first_research_protocol.md
- docs/event_study_design_protocol.md
- docs/research_reference_map.md

