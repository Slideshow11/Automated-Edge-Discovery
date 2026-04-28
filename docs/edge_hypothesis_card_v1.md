# Edge Hypothesis Card v1

Purpose
-------
The Edge Hypothesis Card v1 is a standardized, lightweight intake artifact that every future candidate edge must complete before manual testing or automated discovery workflows may consider running experiments. The card captures the declarative hypothesis, mechanism, data and universe constraints, testable predictions, diagnostics, and explicit safety boundaries.

When this card is required
-------------------------
- Before creating any CandidateSpec or TestRun associated with an idea intended for AED experimentation.
- Before large-scale automated searches that could generate many CandidateSpecs.
- For both human-originated hypotheses (literature, research note) and exploratory probes.

Required fields
---------------
Every card must include the following top-level fields (fill with concise, human-readable values):
- Card ID (edge_card_id)
- Author(s)
- Date (YYYY-MM-DD)
- Status (draft | review | test_ready)
- Hypothesis statement (see below)
- Economic mechanism
- Instrument universe
- Data sources (point-in-time where applicable)
- Point-in-time constraints
- Testable prediction
- Primary metric
- Secondary diagnostics
- Null result definition
- Multiple testing controls
- Leakage risks
- Execution realism assumptions
- Required falsification checks
- Promotion restrictions
- Review owner / contact

Hypothesis statement
--------------------
Write a single clear, testable sentence that describes the expected effect and direction. Example: "Short-term implied volatility in front-month options increases by X% in the D-3..D-1 window before earnings for small-cap issuers, holding cost assumptions constant." The hypothesis must be written before seeing the target test result.

Economic mechanism
------------------
Describe the causal story or economic friction that would explain the effect (liquidity, delayed information diffusion, risk premium, market maker repricing, flow-driven demand, microstructure, etc.). Candidate mechanisms should be explicit and falsifiable where possible.

Instrument universe
-------------------
State the asset classes and filters (e.g., US equities, options on listed equities, delta bucket 0.25-0.35, market-cap 500M-2B). Be precise about inclusion/exclusion rules.

Data sources
------------
List vendors, internal feeds, and point-in-time constraints. If the variable can leak forward, document how point-in-time snapshots will be constructed.

Point-in-time constraints
------------------------
Declare how timestamps, event sessions (AMC/BMO), and exchange trading calendars are handled. State whether sources are considered exact, estimated, or disputed.

Testable prediction
-------------------
Specify the empirical prediction (direction, horizon, holding period, and expected effect size). Include the primary metric and the pre-specified time windows for measurement.

Primary metric
--------------
Define the primary performance metric (e.g., mean cumulative abnormal return over D-1..D+1 adjusted for costs at 10bps per side; abnormal IV ramp measured in percentile points). This metric drives acceptance decisions in the review packet but does not alone authorize promotion.

Secondary diagnostics
---------------------
List additional diagnostics required for interpretation: decomposition (delta/vega/theta), liquidity sensitivity, cost sensitivity, regime splits, feature importance, robustness to normal-performance model.

Null result definition
----------------------
Define what counts as a null or failed result (e.g., effect smaller than detectable threshold at 80% power, or effect disappears after cost and liquidity adjustments).

Multiple testing controls
-------------------------
Declare planned multiple-testing adjustments (DSR, FWER, Benjamini-Hochberg, holdout splits, pre-registered search space). All broad searches must report TrialLedger-style metadata and all trials.

Leakage risks
-------------
Enumerate likely leakage channels (label leakage, time-series leakage, event-date leakage) and mitigation steps.

Execution realism assumptions
-----------------------------
State execution model (MID_WITH_SPREAD_PENALTY, conservative fill model, slippage assumptions), liquidity thresholds, and whether hedging is assumed or simulated.

Required falsification checks
----------------------------
At minimum include: shuffled-dates, random-entry baselines, matched non-event controls, cost-sensitivity, and schedule-announcement robustness checks.

Promotion restrictions
---------------------
Firms and projects must treat this card as documentation only. This card explicitly DOES NOT authorize automated promotion, production use, registry mutation, or live trading. In particular: the card does not and must not contain language that authorizes automatic promotion, automatic registry writes, or production trading deployment.

Drafts and completeness
-----------------------
Incomplete cards are permitted to be saved as drafts (Status = draft) but must not be marked test_ready or used to run experiments until all required fields are completed and the review_owner signs off.

Paper-to-variable rule
----------------------
Ideas derived from literature or external claims must be translated into measurable variables and sampling rules before testing. "LiteratureClaim" text alone is insufficient to run a CandidateSpec.

Example card
------------
- edge_card_id: evcard-2026-0001
- Author(s): Alice Research
- Date: 2026-04-28
- Status: draft
- Hypothesis statement: "Front-month implied volatility percentile increases by 10 points in D-3..D-1 before earnings for small-cap stocks (market cap < 2B)."
- Economic mechanism: market maker repricing due to asymmetric information about earnings.
- Instrument universe: US equity options, front-month, delta 0.25-0.35.
- Data sources: vendor-x option IV time series (point-in-time), exchange trade calendar.
- Point-in-time constraints: use vendor-x point-in-time snapshots; event_session = AMC/BMO as supplied by vendor.
- Testable prediction: IV percentile increases by >=10 points in D-3..D-1 vs matched non-event windows.
- Primary metric: median abnormal IV percentile change, cost-adjusted.
- Secondary diagnostics: random entry, shuffled-dates, liquidity sensitivity, delta decomposition.
- Null result: detectable effect size < 10 percentile points at 80% power after costs.
- Multiple testing: pre-register universe and control FWER across N tests.
- Leakage risks: event_date mislabels, vendor timestamp inconsistencies.
- Execution assumptions: MID_WITH_SPREAD_PENALTY, min open-interest > 1000.
- Falsification checks: matched controls, shuffled-dates.
- Promotion restrictions: no automated promotion; manual review required.
