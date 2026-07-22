# Merge Authorization Guard — historical pointer

**Status:** Historical only. Superseded by `docs/aed_pr_canonical_guide.md`.

The `check_merge_authorization.py` standalone guard is RETIRED.
Its phrase-validity logic now lives in `scripts/local/aed_pr_lib.py`
and is invoked by the canonical controller
`scripts/local/aed_pr.py merge`.

The canonical phrase remains:

    I confirm merge PR #<N> at <40-hex HEAD>
    using final-head reviewed clean state.

Use the controller to obtain the phrase and to perform the merge:

    python3 scripts/local/aed_pr.py status --pr-number N
    python3 scripts/local/aed_pr.py merge --pr-number N \
        --authorization-phrase "<exact phrase from status>"

Do NOT consult the procedural sections of this guard document;
they describe a wrapper that no longer exists in the repository.
This file is retained only so prior PR descriptions and prior
commit messages can be cross-referenced.
