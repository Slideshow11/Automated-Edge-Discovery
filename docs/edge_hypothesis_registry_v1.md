# Edge Hypothesis Registry — v1

Purpose

This registry provides a single, deterministic, human-readable location to record candidate "edge" hypotheses for the AED project. It is documentation-first: entries record intent, required data and tests, and links to the more detailed hypothesis card (edge hypothesis card v1). The registry is not an enforcement mechanism and does not change runtime behavior — it exists to make research reproducible, discoverable, and auditable.

Why hypothesis IDs?

Assigning stable IDs (e.g. AED-HYP-0001) before testing prevents accidental re-use of names, enables de-duplication, and makes it possible to refer unambiguously to a hypothesis in review packets, issue trackers, and test results.

Scope and guardrails

- Documentation-only: the registry contains metadata and links to hypothesis cards and required test artifacts. It does not trigger any automated promotion, registry mutation in any runtime, or production deployment.
- IDs and links: every registry entry must link to a hypothesis card (docs/edge_hypothesis_card_v1.md) describing the hypothesis in full.
- Falsification-first: a hypothesis must not be treated as an "edge" until it survives pre-registered falsification checks and out-of-sample testing. Status values and evidence stages enforce this process.

Required fields for each registry entry

Each CSV row in the registry must include the following columns (header names are authoritative):

- hypothesis_id — stable ID, format AED-HYP-0001
- short_name — short human-friendly name
- asset_class — asset class (e.g., equities, options, futures, FX)
- market_universe — description or link to universe definition
- signal_family — e.g., momentum, mean_reversion, implied_vol_spread
- proposed_mechanism — short description of the economic or behavioral mechanism
- data_requirements — brief list of datasets, point-in-time constraints
- leakage_risks — human-readable notes on potential leakage
- test_protocol_link — path or URL to the pre-specified test protocol (paper/hypothesis card/repo path)
- hypothesis_card_link — link to the detailed edge hypothesis card (required)
- status — lifecycle status (see below)
- evidence_stage — evidence stage (see below)
- owner — owner or owner team
- created_date — ISO date YYYY-MM-DD
- last_updated — ISO date YYYY-MM-DD

Status lifecycle (one of)

- proposed — initial entry; hypothesis_id assigned but card may be draft
- specified — hypothesis card exists and protocol is pre-specified
- testing — running tests or collecting evidence (in-sample or OOS)
- falsified — rejected by pre-registered falsification checks
- parked — deferred for future work
- promoted — candidate edge (requires documented mechanism, OOS success, and explicit promotion record)

Evidence stage (one of)

- idea — early idea, literature notes only
- literature_supported — supported by external literature but not yet tested
- in_sample_tested — tested in-sample but not yet falsification-tested
- falsification_tested — pre-registered falsification checks executed
- out_of_sample_tested — passed out-of-sample validation
- production_candidate — considered for production after risk review (still requires separate operational approvals)

Required links

Every registry row must include test_protocol_link and hypothesis_card_link. The registry is intended to be machine-readable but authoritative decisions remain human-driven.

Guardrails

- Registry entries are examples until they reach evidence_stage=out_of_sample_tested and status=promoted. The project does not allow automated transitions from registry -> production.
- Entries must call out leakage risks and required falsification tests before being promoted.

Example entries (clearly marked examples)

The CSV file adjacent to this document contains example placeholder rows. These are intentionally shallow examples for illustration only and must not be treated as proven edges.


Revision history

v1 — initial RFC-style registry (docs-only). A local validator may be added in a later tooling PR.
