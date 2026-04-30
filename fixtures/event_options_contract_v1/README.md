Fixtures: Event & Options Contract v1

Purpose

This directory contains small, synthetic fixture examples that illustrate the EventDatasetSpec v1 and OptionsObservationSpec v1 contract defined in docs/event_options_contract_spec_v1.md. The fixtures are intentionally minimal and deterministic to support future validator development and documentation examples.

Relationship to docs/event_options_contract_spec_v1.md

These fixtures are concrete example records that exercise the contract semantics in docs/event_options_contract_spec_v1.md. They are not exhaustive; they are intended to make expected behaviors explicit for human reviewers and for future validator test cases.

Files included

- valid_events_minimal.csv: Minimal, valid event rows (AMC, BMO, and differing fiscal period examples).
- valid_options_observations_minimal.csv: Minimal, valid option observation rows linked to the valid events.
- invalid_events_examples.csv: Small set of invalid event rows with an invalid_reason column explaining why each row is invalid.
- invalid_options_observations_examples.csv: Small set of invalid option observation rows with invalid_reason explaining the failure mode.
- invalid_events_edge_cases.csv: Edge-case invalid events designed to trigger validators for missing, duplicate, enum, and timestamp failures.
- invalid_options_observations_edge_cases.csv: Edge-case invalid option observations designed to trigger missing link, invalid link, unknown hold/gap, and future-timestamp blockers.

Expected future validator behavior

Future validators (NOT included in this PR) are expected to:
- Enforce event_id uniqueness and immutability
- Verify ISO-8601 timestamp formats and timezone awareness
- Enforce anti lookahead: option observations with observation_date > event_time must not be allowed as decision-time features
- Enforce price/quote sanity (bid >= 0, bid <= ask), expiry >= observation_date, and required fields present

Notes

- These are fixtures only and are NOT production data. They are intentionally small, synthetic, and hand-crafted.
- The edge-case fixtures exercise explicit blocker categories for the validator and are intentionally invalid.
- Human review remains required; fixtures do not imply registry lifecycle actions (accepted/rejected/killed).

These fixtures exercise event identity semantics: each option observation is linked to event identity (event_id), and cohorts are selected by event identity or event date.

Provenance

Derived from docs/event_options_contract_spec_v1.md and docs/event_options_contract_validator_design_v1.md.
