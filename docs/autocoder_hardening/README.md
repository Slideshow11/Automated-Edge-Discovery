# Autocoder Control-Plane Hardening — worklog

Tracks the five hard-coded requirements from PHASE 3 of the
operator authorization for PR #411 follow-up:

1. Complete evidence pagination
2. One shared Codex classifier
3. Hard-coded non-human review policy
4. Cohesive repair batching
5. Impact-based test selection

All five are addressed inside `scripts/local/` without extracting
the autocoder into a separate repository.
