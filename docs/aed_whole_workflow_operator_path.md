# AED Whole-Workflow Operator Path — historical pointer

**Status:** Historical only. Superseded by `docs/aed_pr_canonical_guide.md`.

This document described the per-step AED workflow that required
operators to wire together `aed_final_gate.py`,
`build_merge_ready_packet.py`, `check_merge_authorization.py`, and
the phase-ledger wrappers. That workflow is RETIRED.

The canonical active operator path lives in
`docs/aed_pr_canonical_guide.md` and uses the single CLI
`scripts/local/aed_pr.py` with `status` / `advance` / `merge`
subcommands.

Do NOT follow the procedural sections of this document; they
describe wrappers that no longer exist in the repository. This
file is retained only so prior PR descriptions and prior commit
messages can be cross-referenced.
