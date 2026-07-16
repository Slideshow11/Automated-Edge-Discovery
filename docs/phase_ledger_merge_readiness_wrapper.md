# Phase-Ledger Merge-Readiness Wrapper — historical pointer

**Status:** Historical only. Superseded by `docs/aed_pr_canonical_guide.md`.

The `merge_readiness_with_phase_ledger.py` and
`finalize_with_phase_ledger.py` wrappers are RETIRED. Their
phase-ledger enrollment behavior is absorbed into the canonical
controller `scripts/local/aed_pr.py advance`; the canonical
controller invokes the surviving live-readiness surface
(`scripts/local/merge_pr_safely.py`) directly without spawning
another wrapper.

Do NOT consult the procedural sections of this wrapper document;
they describe wrappers that no longer exist in the repository.
This file is retained only so prior PR descriptions and prior
commit messages can be cross-referenced.
