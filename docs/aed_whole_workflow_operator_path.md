# AED Whole-Workflow Operator Path

**Status:** Active v1
**Scope:** Operator guide. No script behavior changes are introduced by this document.
**Companion:** `docs/README.md` (document map), `docs/merge_authorization_guard.md` (merge authorization), `docs/phase_ledger_merge_readiness_wrapper.md` (final-gate wrapper), `docs/pr_review_comment_gate.md` (review comment gate), `docs/stale_review_thread_auto_resolution_policy.md` (thread-resolution policy), `docs/trace_policy_v1.md` (audit log policy), `docs/harness_charter_v1.md` (harness charter).

## 1. Purpose

This document is the top-level operator path for safely running an AI-coded
PR through AED governance. It connects the canonical AED stages into a
single read-this-first map:

- task packet
- runner
- phase ledger
- run summary
- final gate
- merge-readiness wrapper
- Codex review
- review-thread state
- human merge authorization
- guarded merge
- post-merge CI
- audit log
- worktree cleanup

It does **not** replace the lower-level wrapper, gate, or audit docs. Those
documents remain the authoritative reference for each stage. This document
exists so an operator can read one page and know which doc opens next, which
script is the next entry point, and which authority decision the human owns
at each step.

## 2. Scope and non-goals

This document is operator guidance only. It explicitly does **not**:

- Authorize any agent to act autonomously at any stage.
- Permit merge without explicit human authorization. The
  `I confirm merge PR #N at <sha>` phrase remains the only authorization
  surface; see `docs/merge_authorization_guard.md`.
- Permit `--admin` or `--auto` merge flags. Both remain separately and
  explicitly forbidden in every AED wrapper; see
  `docs/phase_ledger_merge_readiness_wrapper.md` §Guardrails.
- Change any script behavior, command shape, gate contract, or audit
  schema. All of those are owned by their own docs and PRs.
- Provide a complete command cookbook. A separate cookbook is listed in
  §8 as future work. This document gives only the safe command
  *shapes* an operator needs to recognize.

If anything in this document appears to conflict with a lower-level
operator or gate doc, the lower-level doc governs.

## 3. Whole workflow map

The 13 stages below are the canonical AED operator path. The
`lifecycle state` column is the reporting vocabulary defined in §4; it is
**not** a code enum and is not enforced by any script.

| # | Stage | Input | Output | Script / Doc | Operator decision | Lifecycle state (after this stage) |
|---|-------|-------|--------|--------------|-------------------|------------------------------------|
| 1 | Task packet | Roadmap / governance plan | PR-scoped task packet | `scripts/local/aed_tasker_packet.py`, `scripts/local/aed_executor_packet.py`, `docs/aed_tasker_executor_design.md` | Approve or reject the packet | `NOT_RUN` |
| 2 | Runner | Task packet + worktree | Branch with code/test changes; per-task bundle artifacts | `scripts/local/run_autocoder_single_task.py` | Inspect bundle; re-run if invalid | `NOT_RUN` |
| 3 | Phase ledger | Runner output | Phase execution trace + run summary with `phase_ledger_*` fields | `scripts/local/phase_ledger.py`, `scripts/local/phase_exec.py` | Verify ledger covers the live head | `NOT_RUN` |
| 4 | Run summary | Per-task bundles + index | `RUN_SUMMARY.json` / `RUN_SUMMARY.md` | `scripts/local/build_autocoder_run_summary.py` | Confirm `overall_status` is `RUN_READY` | `NOT_RUN` |
| 5 | Final gate | Run summary + Codex artifact + local validation | `MERGE_READY` / `WAIT` / `BLOCK` / `HOLD_*` recommendation | `scripts/local/finalize_with_phase_ledger.py`, `scripts/local/aed_final_gate.py` | Investigate any non-`MERGE_READY` outcome | `MERGE_READY_AWAITING_HUMAN_AUTHORIZATION` on `MERGE_READY`; otherwise the relevant `HOLD_*` |
| 6 | Merge-readiness wrapper | Final-gate output + expected head SHA | `MERGE_READY` packet bound to the live head | `scripts/local/merge_readiness_with_phase_ledger.py` (`--run-summary` opt-in) or `scripts/local/merge_pr_safely.py` (default) | Confirm head binding is exact; abort on `HOLD_HEAD_CHANGED` | `MERGE_READY_AWAITING_HUMAN_AUTHORIZATION` on a clean wrapper run |
| 7 | Codex review | PR at the live head | Codex review submission + inline findings | `scripts/local/check_pr_review_comments.py` after a `@codex review` ping | Inspect severity-classified findings | `CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED` on a clean current-head pass with no open P0/P1/P2; `HOLD_NEW_CODEX_THREAD` if Codex raises a new current-head finding |
| 8 | Review-thread state | Codex + reviewer + human comments | Resolved / unresolved / outdated thread map | `docs/stale_review_thread_auto_resolution_policy.md`, `docs/pr_review_comment_gate.md` §13 | Do **not** auto-resolve; the policy gates this | `CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED` while human thread handling is in progress |
| 9 | Human merge authorization | `MERGE_READY` packet + live head SHA + clean Codex | Exact authorization phrase | `scripts/local/build_merge_ready_packet.py`, `scripts/local/check_merge_authorization.py`, `docs/merge_authorization_guard.md` | Issue the exact `I confirm merge PR #N at <sha>` phrase with the live 40-char SHA | `MERGE_READY_AWAITING_HUMAN_AUTHORIZATION` until the phrase is provided |
| 10 | Guarded merge | Authorization phrase + live head | Merged PR | `gh pr merge` invoked manually with `--match-head-commit` | Verify merge succeeded; capture merge SHA | `PR_MERGED_PENDING_CLOSEOUT` |
| 11 | Post-merge CI | Merge commit on `main` | Green required CI jobs on the merge commit | `gh pr checks <PR>` (bounded polling, no `--watch`) | Wait for green; investigate any failure | `HOLD_POST_MERGE_CI_PENDING` while pending; `HOLD_POST_MERGE_CI_FAILED` on any required job failure; `HOLD_POST_MERGE_CI_NOT_OBSERVED` if checks never completed in the polling window |
| 12 | Audit log | All earlier stages | Append-only JSONL row in `~/.hermes/aed/audit/log.jsonl` | `scripts/local/append_merge_action_audit.py`, `docs/trace_policy_v1.md` | Verify the row is present and well-formed | `PR_MERGED_AND_CLOSED_OUT` once the audit row is present and validated |
| 13 | Worktree cleanup | Merged PR + audit row | Temp worktree removed; branch deleted | `git worktree remove --force`, `git push origin --delete <branch>` | Keep the primary worktree intentionally stale; do not reset it | `PR_MERGED_AND_CLOSED_OUT` (terminal) |

The terminal state is `PR_MERGED_AND_CLOSED_OUT`. Every other state is
intermediate and must be either advanced or held with an explicit reason.

## 4. Canonical lifecycle states, v1

This section defines the **reporting vocabulary** used by the workflow
map in §3. It is descriptive, not prescriptive: no script reads or
writes these values, and no gate transitions on them. Their purpose is
to give operators and future agents a stable vocabulary for reporting
where a PR is in the pipeline.

| State | Meaning | Required next step |
|-------|---------|--------------------|
| `NOT_RUN` | No runner output for this PR yet, or runner output is older than the live head. | Re-run the runner against the live head. |
| `HOLD_PR_CI_PENDING` | PR is open, head is correct, but required CI jobs have not yet completed. | Bounded-poll `gh pr checks`; do **not** use `--watch`. |
| `HOLD_PR_CI_FAILED` | A required CI job failed on the live head. | Read the failing job log; push a fix or close the PR. Do not merge. |
| `HOLD_CODEX_RESPONSE_PENDING` | `@codex review` ping posted; Codex has not yet produced a review submission. | Bounded-poll the review endpoints; do not assume silence is clean. |
| `HOLD_NEW_CODEX_THREAD` | Codex raised at least one current-head finding that is not yet resolved/waived. | Read the finding; fix or explicitly waive per the gate rules; do not merge. |
| `CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED` | Codex review is clean on the current head, but the PR has open human-side or resolved-only-allowed threads that still need human attention before merge. | Human inspects threads; thread resolution requires explicit human authorization (see §5). |
| `MERGE_READY_AWAITING_HUMAN_AUTHORIZATION` | All automated gates are clean on the current head; only the human authorization phrase is missing. | Human issues the exact phrase in `docs/merge_authorization_guard.md`. |
| `PR_MERGED_PENDING_CLOSEOUT` | Merge succeeded; post-merge CI and audit log not yet verified. | Verify post-merge CI is green; append and validate the audit row. |
| `PR_MERGED_AND_CLOSED_OUT` | Merge succeeded; post-merge CI green; audit row present and validated; temp worktree removed; branch deleted. | Terminal. |
| `HOLD_POST_MERGE_CI_PENDING` | Merge succeeded; post-merge CI still running. | Bounded-poll; do not declare closed out. |
| `HOLD_POST_MERGE_CI_FAILED` | Merge succeeded; a required post-merge CI job failed. | Investigate; consider a revert PR; do not declare closed out. |
| `HOLD_POST_MERGE_CI_NOT_OBSERVED` | Merge succeeded; post-merge CI was not observed within the bounded polling window. | Re-check with a fresh bounded poll before declaring closed out. |

**Reporting rule.** When in doubt, the conservative state wins: prefer
any `HOLD_*` over `MERGE_READY_AWAITING_HUMAN_AUTHORIZATION`, and prefer
`MERGE_READY_AWAITING_HUMAN_AUTHORIZATION` over `PR_MERGED_PENDING_CLOSEOUT`.
This matches the finalization-guard priority ladder in
`docs/phase_ledger_merge_readiness_wrapper.md` §Run-summary ledger field
set semantics.

## 5. Operator vs agent authority table

The table below lists the actions the operator path anticipates and
identifies the authority required for each. **Authority is explicit
human authorization unless the row is otherwise marked.** "Scoped task"
means a task whose scope contract lists the action as in-scope.

| Action | Authority |
|--------|-----------|
| Read-only audits (e.g. `gh pr view`, `gh pr checks`, `git status`) | Allowed in any scoped task. |
| Docs patch (this document and any other `docs/*.md`) | Allowed only in a docs-scoped task. |
| Code patch (any `scripts/**`, `tests/**`, `engine/**`, `schemas/**`, `.github/**`) | Allowed only in a code-scoped task; separate PR; this guide does not authorize it. |
| Branch push to a task branch | Allowed only to the task branch; never to `main` from a worktree. |
| Codex review ping (`@codex review`) | Allowed only when the PR is open at the expected head, CI is green, the patch is in scope, and the comment body is gate-safe (see §7). One ping per head. |
| Thread resolution | Explicit human authorization required. The policy in `docs/stale_review_thread_auto_resolution_policy.md` defines the one allowed case and its 14 preconditions; it does not grant blanket agent authority. |
| Merge (`gh pr merge`, including `--merge`, `--squash`, `--rebase`) | Explicit human authorization via the `I confirm merge PR #N at <sha>` phrase. The exact 40-character SHA is mandatory; see `docs/merge_authorization_guard.md` §Authorization phrase. |
| `--admin` flag on `gh pr merge` | Separate explicit human authorization required, and only for cases where a non-admin merge is technically blocked. The AED merge-readiness wrapper hard-rejects `--admin` at argparse time; see `docs/phase_ledger_merge_readiness_wrapper.md` §Guardrails. |
| `--auto` flag on `gh pr merge` | Forbidden. No documented exception; a future policy may revisit this and would require its own operator doc and PR. |
| Primary worktree update, reset, or pull | Explicit human authorization required. The primary worktree is intentionally left stale at the post-closeout head of the last merged PR (for example, `0a8cee5d2406c970e02e9e217c7f25b0767459e0` after PR #394); agents must not touch it. |
| PR #384 and PR #386 | Read-only verification only, unless a separate scoped task explicitly authorizes interaction. |
| Review dismissal (`dismissReview` or equivalent) | Forbidden. |
| Comment deletion | Forbidden. |
| Force-push (`git push --force`, `--force-with-lease`) | Forbidden. |
| Memory or `fact_store` writes during a PR run | Forbidden. The PR is closed out using the audit log only. |
| Skill creation or update during a PR run | Forbidden. New skills are saved in dedicated sessions, not as a side-effect of a PR run. |

## 6. Safe command references

This section gives only the safe command *shapes* an operator needs to
recognize in this path. A full command cookbook — including ready-to-run
incantations for every gate, every stage, and every known failure mode —
is listed in §8 as future work. Until that cookbook lands, operators
should consult the per-stage doc referenced in §3 and copy the example
command from that doc, replacing placeholders.

### 6.1 Read-only PR inspection

`gh pr view <PR> --repo <owner/name> --json state,headRefOid,mergedAt,mergeable,mergeStateStatus,isDraft,baseRefName,headRefName,title,url`

- Read-only. Safe to re-run.
- Always inspect at least `state`, `headRefOid`, and `mergeStateStatus` together.
- `mergedAt` should be `null` for any PR not yet merged; an unexpected
  non-null `mergedAt` is a state-correction signal, not authorization.

`gh pr checks <PR> --repo <owner/name>`

- Read-only. **Never** use `--watch`; always use bounded polling.
- The supported bounded-poll pattern is: poll at most 18 times with a
  20-second sleep between polls, then stop and report the current
  state. Do not loop indefinitely.

### 6.2 Exact-head merge command shape

The merge command shape is the only safe pattern. It is repeated here
for visibility; the canonical reference is
`docs/merge_authorization_guard.md` §Recommended merge command.

```
gh pr merge <PR> \
  --repo <owner/name> \
  --squash --delete-branch \
  --match-head-commit <40-hex-sha>
```

- `<40-hex-sha>` is the live PR head SHA at the moment of authorization.
- A 7-character or 39-character SHA is **not** acceptable.
- One wrong character in the SHA must abort the merge; the agent must
  never substitute, infer, or correct.
- See `docs/merge_authorization_guard.md` §Exact SHA requirement for the
  PR #207 failure mode this prevents.

### 6.3 Post-merge verification shape

```
gh pr view <PR> --repo <owner/name> \
  --json state,mergedAt,mergeCommit
git -C <repo-root> log -1 --format=%H
```

- The merge commit SHA returned by `gh pr view` must equal the
  `--match-head-commit` argument used at authorization.
- The branch must be deleted by `gh pr merge --delete-branch`; if it is
  not, the agent must not declare closed out.
- See `docs/trace_policy_v1.md` §2.1 (raw trace retention) for what the
  audit row must capture.

### 6.4 Audit append shape

`scripts/local/append_merge_action_audit.py` writes one JSONL row per
merge. The audit log lives at `~/.hermes/aed/audit/log.jsonl`. After
every append, validate the log with
`scripts/local/validate_merge_action_audit_log.py --allow-legacy
--expected-prs-json '[<list-of-expected-prs>]'`. The full append-and-
validate pattern is in `docs/trace_policy_v1.md` and the post-merge
hygiene section of recent governance PRs.

### 6.5 Future cookbook (deferred)

The following command shapes are intentionally **not** expanded here.
They will be centralized in a separate `docs/aed_command_cookbook.md`
in a future PR:

- Per-wrapper invocation templates with placeholder fill-in
- Per-gate failure-mode recipes
- Per-stage rerun-and-resume patterns
- Codex-ping body templates (gate-safe, see §7)
- Worktree-cleanup one-liners

## 7. Lessons from PR #394

The full closeout of PR #394 surfaced a small number of operational
lessons that the operator path should keep in view. Each lesson is
stated once, in operational terms, and is referenced (not duplicated) in
the relevant lower-level doc.

- **Exact head binding matters at every binding step.** Phase-gate
  validation, pre-delegation recheck, post-delegation recheck, and
  final live-head recheck must all bind to the same `expected_head_sha`.
  A new commit landing at any of those checkpoints must refuse
  success. See `docs/phase_ledger_merge_readiness_wrapper.md` §Guardrails.
- **A Codex clean-pass must be tied to the current head or to a
  current clean-pass signal.** A "clean" review attached to an
  ancestor SHA is not valid evidence for the live head. See
  `docs/merge_authorization_guard.md` §Stale review behavior.
- **Review threads can be outdated but still unresolved.** Outdatedness
  is a GitHub-side property, not a fix. The agent must not assume an
  outdated thread is resolved; the policy in
  `docs/stale_review_thread_auto_resolution_policy.md` is the only
  permitted auto-resolution path, and it is gated on 14 preconditions.
- **Thread resolution requires explicit human authorization.** No
  blanket agent authority exists. The closeout of PR #394 required
  human decisions at every step.
- **PR comments can trip the review-comment-gate.** Any comment body
  posted by an agent must avoid the gate's known sensitivity patterns.
  Two specific patterns must be checked before posting:

  1. **Standalone severity marker text** — the text `P` immediately
     followed by a digit `0` through `3` (for example `P0`, `P1`,
     `P2`, `P3`). This pattern is reserved for findings, not for
     review-request prompts.
  2. **Known gate-triggering words or phrases**, including (but not
     limited to) the canonical list: `stale`, `must fix`, `can fail`,
     `security`, `path traversal`, `malformed`, `nonzero`, `unsafe`.
     A short, neutral `@codex review` prompt with the current head
     SHA and a one-paragraph scope description is the only known-safe
     body. See `docs/pr_review_comment_gate.md` §5 (severity rules) and
     §3 (required endpoints) for what the gate is sensitive to.

- **Pings should be sanitized before posting.** Local pre-flight check
  on the exact body is mandatory. If the scan fails, do not post; do
  not "fix and retry" silently — report and request a human rephrase.

## 8. Where next work belongs

The following items are explicitly out of scope for this document and
should be tracked as separate future PRs. They are listed in
descriptive prose only, not implemented here.

- A known-safe command cookbook that centralizes every read-only
  invocation, every wrapper invocation, every gate-failure recipe, and
  every Codex-ping body template. The current doc points at
  per-wrapper examples but does not centralize them.
- A review-thread resolver helper that codifies the 14 preconditions
  in `docs/stale_review_thread_auto_resolution_policy.md` and produces
  the required audit record. The helper must remain read-only at the
  API level; thread resolution must still be a separate human action.
- A PR closeout checklist generator that emits the exact
  post-merge verification shape and the audit row schema from §6.3
  and §6.4, parameterized by PR number and merge SHA.
- A lifecycle state enum in code, keyed to §4, with transitions
  derived from gate outputs and merge authorization. The current doc
  is the reporting vocabulary; the enum would be the enforcement
  surface and is intentionally not introduced here.
- Structured output schemas for each stage's "output" column in §3
  (for example, a single schema per row in §3) so that future agents
  can validate the chain mechanically.
- A stale-PR auditor that scans the repo for `HOLD_*` lifecycle states
  that have not advanced within an expected window and reports them
  for human review. The auditor is read-only; the closeout decision
  remains human.

---

*This document is the v1 of the operator path. Revisions should be
docs-only PRs that update this file in place, with a CHANGELOG-style
note appended at the bottom of each section that changes.*
