# AED Canonical PR-Lifecycle Guide

One short guide for the canonical AED PR-lifecycle controller. This
document replaces the per-step wrapper documentation that previously
described `aed_final_gate.py`, `build_merge_ready_packet.py`,
`check_merge_authorization.py`, `finalize_with_phase_ledger.py`, and
`merge_readiness_with_phase_ledger.py` — all of which have been
absorbed into the controller and deleted.

## The canonical CLI

`scripts/local/aed_pr.py` is the only CLI a human operator needs
from the moment a draft PR exists until the post-merge closeout
finishes. It has three subcommands:

  - `status`   Read live PR state, emit one authoritative JSON report.
               Read-only. Safe to run any number of times.
  - `advance`  Perform every safe mechanical lifecycle step EXCEPT the
               merge itself. Never requires the human authorization
               phrase.
  - `merge`    The only operation that requires the human authorization
               phrase. Re-fetches all live evidence immediately before
               executing the squash merge.

## Workflow

1. Open the draft PR (normal `gh pr create --draft` flow).
2. Run:

       python3 scripts/local/aed_pr.py status --pr-number N

   This is the operator's single read-only check. The JSON report
   contains the lifecycle state, the exact `safe_merge_command`
   preview, and the exact `required_authorization_phrase`.
3. When CI, scope, exact-head Codex, and thread inventory are all
   green, run:

       python3 scripts/local/aed_pr.py advance --pr-number N

   This may request a Codex review for the current head, resolve
   eligible outdated Codex-bot-only threads, mark the draft ready,
   and produce the post-merge closeout plan.
4. Once `status` reports `READY_FOR_MERGE_AUTHORIZATION`, copy the
   `required_authorization_phrase` field byte-exact from the JSON
   report and run:

       python3 scripts/local/aed_pr.py merge --pr-number N \
           --authorization-phrase "<exact phrase>"

   The controller re-fetches the live head immediately before
   executing and rejects any stale phrase. The exact `gh pr merge`
   command is `gh pr merge N --repo owner/name --squash
   --delete-branch --match-head-commit <head>` and nothing else.

## Safety rules (enforced by the controller and the policy engine)

  - The 40-character head SHA in the authorization phrase MUST
    exactly match the live PR head. Short prefixes are rejected.
  - Whitespace in the authorization phrase is significant — even one
    extra or missing space rejects.
  - `--admin` is forbidden at every layer; the controller's `merge`
    subcommand refuses to execute any argv containing `--admin`.
  - Auto-merge is forbidden; the controller rejects argv containing
    `--auto`.
  - Draft PRs cannot be merged; the controller refuses.
  - PR must be `OPEN`, not `MERGED` or `CLOSED`.
  - PR must be `mergeable=true`.

## Lifecycle states (operator-facing)

The controller collapses live state into one primary lifecycle state
plus a `next_human_action` field. Operators never need to know the
history of retired HOLD names.

  - `WAITING`                       CI in flight or Codex in flight.
  - `ACTION_REQUIRED`               A specific human action would unblock.
  - `BLOCKED`                       A deterministic condition is not met.
  - `READY_FOR_MERGE_AUTHORIZATION` All gates green; human speaks the phrase.
  - `MERGED_PENDING_CLOSEOUT`       Merge commit on main; closeout next.
  - `COMPLETE`                      All mechanical steps verified.

## What this replaces

The following scripts are retired and removed from the controller path:

  - `scripts/local/aed_final_gate.py`           (absorbed)
  - `scripts/local/build_merge_ready_packet.py` (absorbed)
  - `scripts/local/check_merge_authorization.py`(absorbed; phrase validator
                                                  is in `aed_pr_lib`)
  - `scripts/local/finalize_with_phase_ledger.py` (absorbed; phase-ledger
                                                  enrollment remains optional)
  - `scripts/local/merge_readiness_with_phase_ledger.py` (absorbed)

The following scripts are kept for now because they are still called
by active scripts in the policy engine and worktree-execution
pipelines. They are NOT the canonical path; future PRs may migrate
their unique logic into the controller.

  - `scripts/local/merge_pr_safely.py`                       (verified-by-CI orchestrator)
  - `scripts/local/wait_for_pr_ready.py`                     (called by `merge_pr_safely`)
  - `scripts/local/aed_continue_pr.py`                       (PR #407 / checkpoint ingestion)
  - `scripts/local/final_gate_status.py`                     (called by `apply_temp_worktree_patch_to_branch`)
  - `scripts/local/verify_final_head_merge_command.py`       (called by many active scripts)

## Historical pointer docs

The following docs are retained but clearly labeled historical. They
are kept only so that prior commit messages, prior PR descriptions,
and prior Codex reviews can still be cross-referenced. Operators
MUST NOT follow their procedural sections; those sections describe
the retired wrappers. The canonical guide above is the only active
operator path.

  - `docs/aed_whole_workflow_operator_path.md`
  - `docs/aed_known_safe_command_cookbook.md`
  - `docs/merge_authorization_guard.md`
  - `docs/phase_ledger_merge_readiness_wrapper.md`

If a workflow step is described only in a historical pointer doc and
not in this canonical guide, that workflow step has been deliberately
removed. Do not re-add it without an explicit decision recorded in a
PR description.

## Tests

The canonical controller has its own focused test module:

  - `tests/test_aed_pr.py`

Existing tests for the surviving tools (notably
`tests/test_merge_pr_safely.py`) remain authoritative for the
verified-by-CI tools they cover.

## Historical policy migrations

The policy content that previously lived in the operator-path and
cookbook docs has been collapsed into the canonical guide under the
sections below. Operators reading the historical pointers can
follow these references to the canonical surface.

### `HOLD_MAIN_HEAD_MISMATCH` evidence contract

The canonical `HOLD_MAIN_HEAD_MISMATCH` state (see
`schemas/aed_lifecycle_states_v1.json`) covers two surfaces: the
`origin/main` HEAD mismatch and the primary worktree mismatch
surface (dirty status, wrong branch, wrong HEAD). The evidence
tokens required to reconcile either surface are:

  - `expected_head_sha`
  - `observed_origin_main_sha`
  - `primary_worktree_path`
  - `primary_status_porcelain`
  - `primary_branch`
  - `primary_expected_head_sha`
  - `primary_observed_head_sha`

The primary worktree is intentionally left stale after merge; the
post-closeout anchor pattern uses the temp worktree as the verified
reference. Read-only verification commands (status `--porcelain`,
`rev-parse HEAD`, `branch --show-current`) confirm the protected
state without mutating it. The `worktree_update` mutation token is
explicitly forbidden on this state and requires explicit human
operator authorization.

### Primary worktree sync policy (codified 2026-06-10)

The primary worktree is intentionally left stale; the temp worktree
holds the verified anchor. To reconcile, operators must run only the
read-only verification commands (`status --porcelain`,
`rev-parse HEAD`, `branch --show-current`) on the primary worktree
and confirm the `HOLD_MAIN_HEAD_MISMATCH` evidence tokens listed
above. Any `worktree_update` requires explicit human operator
authorization.

### Lessons from PR #394

PR #394 first surfaced the lifecycle-state vocabulary collapsing
problem; the canonical 6-state vocabulary in the Lifecycle states
section above is the resolved form. Future contributions must not
re-introduce per-wrapper HOLD_* names.

### Where next work belongs

Future lifecycle extensions belong in `docs/aed_lifecycle_states_v1.md`
plus the canonical operator guide above. Do not re-introduce
per-wrapper workflow docs without an explicit decision recorded in a
PR description.
