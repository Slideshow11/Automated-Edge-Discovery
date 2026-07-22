# AED Known-Safe Command Cookbook — historical pointer

**Status:** Historical only. Superseded by `docs/aed_pr_canonical_guide.md`.

The safe `gh pr merge` command that the cookbook historically
recommended is now emitted by the canonical controller itself:

    gh pr merge N --repo owner/name \
        --squash --delete-branch \
        --match-head-commit <40-hex HEAD>

The canonical CLI `scripts/local/aed_pr.py merge --pr-number N
--authorization-phrase "<phrase>"` emits this exact argv after
re-fetching live state and validating the phrase byte-for-byte.

Do NOT consult the procedural sections of this cookbook; they
describe a fragmented per-script workflow that is no longer the
canonical path. This file is retained only so prior PR
descriptions and prior commit messages can be cross-referenced.
