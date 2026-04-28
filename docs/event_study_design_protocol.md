# Event-study design protocol

Purpose
-------
This protocol specifies how Automated Edge Discovery (AED) should design, declare, run, and review event-driven research ("event studies") before any schema or runtime implementation is introduced. It focuses on event-driven strategies and hypotheses (for example, pre-earnings research) and establishes auditable metadata, safe non-goals, and human-review gates.

Non-goals
---------
- This document is docs-only. It does not implement runtime behavior, schemas, or acceptance logic.
- It does not authorize registry mutation, automated promotion, or automated accepted/rejected/killed lifecycle actions.
- It does not prescribe exact code-level APIs; those belong in follow-up schema PRs.

EventStudySpec
--------------
Event-driven research must be declared via an EventStudySpec. The EventStudySpec is a human-readable, auditable contract that must be attached to any event-driven CandidateSpec, BacktestRun, or ReviewPacket.

Required EventStudySpec fields (every EventStudySpec must include)
- event_study_id
- hypothesis_id
- event_type                 # e.g. earnings_announcement, merger, guidance_change
- event_source               # vendor or canonical source for event timestamps and session
- event_timestamp            # canonical timestamp (with timezone) for the event observation
- event_session              # AMC / BMO / INTRADAY session indicator
- event_timestamp_quality    # quality flag: exact, estimated, vendor_disagrees, ambiguous
- event_window               # explicit pre-event / event / post-event windows (trading-day aware)
- estimation_window          # window used to estimate normal performance model
- sampling_interval         
- event_date_uncertainty_policy
- normal_performance_model   # declared normal model (see section below)
- abnormal_performance_measure
- aggregation_policy         # how to aggregate across assets / events (mean, median, weighted)
- clustering_policy         
- inference_policy           # planned inference approach (robust, clustered, bootstrap)
- power_diagnostic           # sample-size / effect-size diagnostics planned
- bias_checklist             # checklist of known biases and mitigation steps
- data_requirements
- leakage_risks
- review_owner

Event windows
-------------
- Event windows must explicitly declare pre-event, event, and post-event windows. Use trading-day and session-aware windows where possible.
- Examples: pre-event window = D-5..D-1, event window = [announcement timestamp +/- session rule], post-event window = D+1..D+5.
- AMC/BMO timing: event_session must state AMC or BMO (or intra-day session) and the analysis must respect session boundaries. AMC/BMO timing requirements must be explicit in EventStudySpec.
- Where a trading calendar exists (exchange trading days, holidays), calendar-day approximations are insufficient; use trading-day-aware windows.
- For intraday or session-aware analyses, define session boundaries (open, close, pre-market, post-market) explicitly.

Normal-performance models
-------------------------
The EventStudySpec must declare one or more normal-performance models. Abnormal performance is undefined unless a normal-performance model is declared.
Common options (examples):
- raw return
- constant mean
- market adjusted (market model)
- sector adjusted
- factor adjusted (e.g. Fama-French or custom factor list)
- matched control (matched non-event windows)
- event matched control (matched on similar events)

Abnormal-performance measures
-----------------------------
Declare the abnormal-performance measures that will be reported. Examples:
- abnormal return
- cumulative abnormal return (CAR)
- abnormal IV change
- abnormal option PnL
- abnormal skew change
- abnormal term-structure change

Inference and clustering
------------------------
- Event clustering by date must be declared: many events occur on the same calendar date and cannot be treated as independent draws without justification.
- Repeated events by issuer require special handling (issuer-level clustering or repeated-measures diagnostics).
- Sector clustering, overlapping windows, and dependence between events must be considered.
- Robust or clustered inference (e.g. cluster by date, issuer, sector) is required in the inference_policy when dependence is likely; this is a future implementation requirement but must be declared now.
- Same-date events and repeated issuer events must not be treated as fully independent without clear justification.

Power and sensitivity diagnostics
---------------------------------
EventStudySpec must include power and sensitivity diagnostics: planned sample size, detectable effect size at chosen power, event-window length sensitivity, sampling interval sensitivity, cost assumptions, liquidity filters, normal-model sensitivity, and session timing sensitivity.

Bias checklist
--------------
An EventStudySpec must include a bias_checklist that addresses common event study pitfalls, including:
- event-date errors
- AMC/BMO misclassification
- survivorship bias
- lookahead bias
- stale quotes
- wide spreads
- missing delisted names
- overlapping event windows
- data revisions
- vendor timestamp inconsistencies

Pre-earnings options example
----------------------------
- event_type: earnings_announcement
- event_session: AMC/BMO
- pre-event window: DPE N to exit before announcement (explicit D-#..D-1 specification)
- target: IV ramp and option PnL before event
- avoided risk: scheduled announcement jump risk
- normal model: historical non-event matched windows or event-matched controls
- abnormal measures: abnormal IV ramp, abnormal option PnL, abnormal skew change
- failure modes: stale quotes, spread costs, event date/session error, liquidity collapse, post-event exposure

Relationship to theory-first protocol
-------------------------------------
- EventStudySpec must reference the linked HypothesisSpec (hypothesis_id). The event-study is evidence collection for a hypothesis; it does not replace a mechanism.
- ExploratoryAnomalySpec may use limited event diagnostics but cannot by itself trigger acceptance, promotion, or production status.

Implementation gates
--------------------
- No EventStudySpec without an existing HypothesisSpec or an explicit ExploratoryAnomalySpec (for exploratory probes).
- No event strategy review without declared event_window and estimation_window.
- No abnormal-performance claim without declared normal-performance model. "No abnormal-performance claim without declared normal-performance model"
- No event strategy review without event_timestamp and event_session quality declared.
- No event strategy acceptance from raw PnL alone. "No event strategy acceptance from raw PnL alone"
- No automated promotion from event-study results.
- Future EventStudyReport must include assumptions, bias_checklist, and detailed diagnostics.

Acknowledgements and references
--------------------------------
This protocol should be read in conjunction with docs/research_reference_map.md and docs/theory_first_research_protocol.md. It inherits the theory-first guardrails and manual-review requirements described there.

Appendix: minimal metadata example
----------------------------------
An EventStudySpec example (illustrative only):
{
  "event_study_id": "evstd-2026-0001",
  "hypothesis_id": "hyp-2026-012",
  "event_type": "earnings_announcement",
  "event_source": "vendor-x",
  "event_timestamp": "2026-07-20T20:00:00-04:00",
  "event_session": "AMC",
  "event_timestamp_quality": "exact",
  "event_window": {"pre": "D-5..D-1","event": "[ts]","post": "D+1..D+5"},
  "estimation_window": "-120..-30",
  "sampling_interval": "1D",
  "event_date_uncertainty_policy": "reject-if-vendor-disagrees",
  "normal_performance_model": "event_matched_control",
  "abnormal_performance_measure": ["abnormal IV change","abnormal option PnL"],
  "aggregation_policy": "median-weighted",
  "clustering_policy": "cluster_by_date_and_issuer",
  "inference_policy": "clustered_robust_se",
  "power_diagnostic": "min_samples=50,detectable=0.05 at 80%",
  "bias_checklist": ["event-date errors","AMC/BMO misclassification"],
  "data_requirements": ["option_iv","underlying_price","volume","bid_ask"],
  "leakage_risks": ["label_leakage","event_announcements_in_future"],
  "review_owner": "alice@example.com"
}
