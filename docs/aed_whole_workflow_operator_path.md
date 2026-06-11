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
  §11 as future work. This document gives only the safe command
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
| 11 | Post-merge CI | Merge commit on `main` (the SHA returned by `mergeCommit.oid`, **not** the PR head SHA) | Green required workflow runs on `main` for the exact merge commit | `scripts/local/audit_main_ci_for_head.py --branch main --head-sha <merge_commit_sha> --required-workflow <name>` (bounded polling) — pre-merge CI uses `gh pr checks <PR>`; post-merge CI verifies main-branch workflow runs against the merge commit because, after a squash merge and branch deletion, the PR's check view is no longer the authoritative main-side evidence. The audit uses **newest-authoritative-per-workflow** semantics: an older cancelled or skipped run for the same workflow and head does not flip the verdict when a later success exists; older superseded runs are reported in `superseded_cancelled_runs` as audit history. | Wait for green; investigate any failure | `HOLD_POST_MERGE_CI_PENDING` while pending; `HOLD_POST_MERGE_CI_FAILED` on any required workflow failure; `HOLD_POST_MERGE_CI_NOT_OBSERVED` if checks never completed in the polling window |
| 12 | Audit log | All earlier stages | Append-only JSONL row in `~/.hermes/aed/audit/log.jsonl` | `scripts/local/append_merge_action_audit.py`, `docs/trace_policy_v1.md` | Verify the row is present and well-formed; do not rewrite any already-appended row | `PR_MERGED_AND_CLOSED_OUT` once the audit row is present and validated |
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
| `AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR` (alias: `AUDIT_APPEND_NEEDS_OPERATOR`) | Audit append/validation could not be completed safely without human operator decision. The audit log is append-only; once an entry is appended, do not delete, trim, rewrite, or replace it unless the human explicitly authorizes that exact audit-log mutation. | Follow the operator decision tree in `docs/aed_lifecycle_state_registry.md` §10. |
| `HOLD_RESUME_CHECKPOINT_NEEDED` | Continuation cannot safely determine the latest verified lifecycle state or the remaining permitted mutations from durable evidence. The previous turn(s) may have been interrupted or out-of-band; the operator must reconstruct state from read-only evidence before any mutation. | Reconstruct the prior verified state using read-only checks (PR number and URL, current head SHA, current lifecycle state, completed phases, remaining permitted mutations, already-performed mutations, protected PR/worktree state). Do not infer readiness from memory. Do not rerun broad workflows when a narrow continuation action is sufficient. See `docs/aed_lifecycle_state_registry.md` §11. |
| `HOLD_MAIN_HEAD_MISMATCH` | The observed `origin/main` HEAD does not match the expected head SHA for this PR run. The canonical scenario is a primary-worktree-state-mismatch at PHASE 1 of a governance PR run; the operator must reconcile before continuing. The state covers both surfaces (origin/main HEAD and primary worktree status/branch/HEAD) and the registry entry in `schemas/aed_lifecycle_states_v1.json` lists the full evidence contract. | Stop. Verify the primary worktree state against the expected post-closeout head (see §9). Do not push, merge, or post a Codex ping in this state. The canonical state's `forbidden_mutations` list already includes `worktree_update`; the primary-worktree sync policy in §9 is the source for the read-only verification pattern. See `docs/aed_lifecycle_state_registry.md` §13. Required evidence: `expected_head_sha`, `observed_origin_main_sha`, `primary_worktree_path`, `primary_status_porcelain`, `primary_branch`, `primary_expected_head_sha`, `primary_observed_head_sha`. |

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
| Codex review ping (`@codex review`) | Allowed only when the PR is open at the expected head, CI is green, the patch is in scope, and the comment body is gate-safe (see §10). One ping per head. |
| Thread resolution | Explicit human authorization required. The policy in `docs/stale_review_thread_auto_resolution_policy.md` defines the one allowed case and its 14 preconditions; it does not grant blanket agent authority. |
| Merge (`gh pr merge`, including `--merge`, `--squash`, `--rebase`) | Explicit human authorization via the `I confirm merge PR #N at <sha>` phrase. The exact 40-character SHA is mandatory; see `docs/merge_authorization_guard.md` §Authorization phrase. |
| `--admin` flag (admin bypass on the merge command) | **Forbidden** at every layer of the existing merge stack. `merge_pr_safely.py` refuses the admin flag always (argparse and defense-in-depth `reject_admin()`); the phase-ledger wrapper (`merge_readiness_with_phase_ledger.py`) hard-rejects the admin flag and never exposes it. No operator phrase, prior token, or one-off authorization in this governance path can grant the admin bypass. A future exception, if any, would require a separate operator policy PR (not a one-off phrase) and its own change to the merge stack; this guide treats the admin bypass as permanently outside the operator path. See `docs/phase_ledger_merge_readiness_wrapper.md` §Guardrails and `scripts/local/merge_pr_safely.py` `reject_admin`. |
| `--auto` flag on `gh pr merge` | Forbidden. No documented exception; a future policy may revisit this and would require its own operator doc and PR. |
| Primary worktree update, reset, or pull | Explicit human authorization required. The primary worktree (`/home/max/Automated-Edge-Discovery`) is intentionally left stale at the post-closeout head of the last merged PR (for example, `0a8cee5d2406c970e02e9e217c7f25b0767459e0` after PR #394; this value is recomputed at the start of every run); agents must not touch it. The primary-worktree sync policy in §9 is the canonical surface for when and how the primary may be advanced. |
| PR #384 and PR #386 | Read-only verification only, unless a separate scoped task explicitly authorizes interaction. |
| Review dismissal (`dismissReview` or equivalent) | Forbidden. |
| Comment deletion | Forbidden. |
| Force-push (`git push --force`, `--force-with-lease`) | Forbidden. |
| Memory or `fact_store` writes during a PR run | Forbidden. The PR is closed out using the audit log only. |
| Skill creation or update during a PR run | Forbidden. New skills are saved in dedicated sessions, not as a side-effect of a PR run. |
| Audit-log row mutation after append (delete, trim, rewrite, replace) | Forbidden. The audit log is append-only. The only allowed action is to append a corrective follow-up entry if the repo audit policy explicitly supports corrective entries; otherwise stop and report `AUDIT_APPEND_NEEDS_OPERATOR` (alias of `AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR`). See `docs/aed_lifecycle_state_registry.md` §10 for the full operator decision tree. Explicit human authorization is required for any audit-log mutation, and the authorization must name the exact audit-log mutation being performed. |
| Continuation turn without durable state evidence | Forbidden. A continuation turn must resume from the latest verified lifecycle state. Before any mutation, the operator must verify current PR number and URL, current head SHA, current lifecycle state, completed phases, remaining permitted mutations, already-performed mutations, and protected PR/worktree state. If the state cannot be reconstructed from durable evidence, stop and report `HOLD_RESUME_CHECKPOINT_NEEDED` (see `docs/aed_lifecycle_state_registry.md` §11). Do not restart broad planning. Do not repeat completed phases. Do not repeat already-authorized mutations. Do not post a duplicate Codex ping for the same head. |

## 6. Safe command references

This section gives only the safe command *shapes* an operator needs to
recognize in this path. A full command cookbook — including ready-to-run
incantations for every gate, every stage, and every known failure mode —
is listed in §11 as future work. Until that cookbook lands, operators
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

The two SHAs have distinct meanings. Operators must keep them separate
throughout the post-merge path:

- **PR head SHA** — the reviewed branch commit passed to
  `--match-head-commit` at authorization time. Used **only** as the
  pre-merge protection; not the SHA of the merge result.
- **Merge commit SHA** — the commit created on `main` by the squash
  merge, returned by `gh pr view --json mergeCommit`. After a squash
  merge this is a brand-new commit on `main` and is **not** equal to
  the PR head SHA. Recorded separately in the audit row as `merge_sha`,
  with the pre-merge PR head recorded as `head_sha` (see
  `docs/merge_action_audit_log.md` §`pr_merge`).

Read both SHAs, then verify the main-side result:

```
gh pr view <PR> --repo <owner/name> \
  --json state,mergedAt,mergeCommit
git -C <repo-root> fetch origin main
git -C <repo-root> rev-parse origin/main
```

- `mergeCommit.oid` (from `gh pr view`) is the new squash commit on
  `main`. The audit row records this as `merge_sha`.
- The pre-merge `--match-head-commit <PR_HEAD_SHA>` value is recorded
  as `head_sha` in the audit row. It is **not** required to equal
  `merge_sha` after a squash merge.
- `origin/main` (after `git fetch origin main`) **must** equal
  `mergeCommit.oid`. If it does not, the merge did not land where the
  audit row claims and the agent must not declare closed out.
- The branch must be deleted by `gh pr merge --delete-branch`; if it
  is not, the agent must not declare closed out.
- See `docs/trace_policy_v1.md` §2.1 (raw trace retention) for what the
  audit row must capture, and `docs/merge_authorization_guard.md`
  §Authorization phrase for the PR #207 failure mode that the
  `--match-head-commit` binding prevents.

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
- Codex-ping body templates (gate-safe, see §10)
- Worktree-cleanup one-liners

## 7. Audit log append-only closeout rule (codified 2026-06-10)

The AED merge-action audit log is append-only. The rule is stated
here in the operator-path vocabulary and is cross-referenced from
`docs/aed_lifecycle_state_registry.md` §10 (the canonical policy
surface) and `docs/aed_known_safe_command_cookbook.md` §11
(known-safe command shape).

**Statement.** Once an audit entry is appended to
`~/.hermes/aed/audit/log.jsonl`, do not delete, trim, rewrite, or
replace it unless the human operator **explicitly** authorizes that
exact audit-log mutation. There is no blanket agent authority to
rewrite audit history. "Looks wrong" is not authorization. This is
true during the closeout of any AED PR, including the audit append
step itself: a malformed, non-canonical, incomplete, or suboptimal
entry stays in the log until a human explicitly authorizes an
amendment, and the standing policy prefers a corrective follow-up
append over a rewrite.

**Operator decision tree.** When an audit entry is suspected to
be malformed, non-canonical, incomplete, or suboptimal after
append, the operator must:

1. **First** run the repo-standard audit validator
   (`scripts/local/validate_merge_action_audit_log.py`). The
   non-strict mode (with `--allow-legacy`) records warnings; the
   strict mode is the gating signal.
2. **If validation fails**, stop and report an audit hold. Do
   not amend the entry. Do not "fix and retry" the closeout.
3. **If validation passes but the entry is non-canonical**, do
   not rewrite it. A non-canonical but valid entry stays in the
   log.
4. **Append a corrective follow-up entry** *only* if the repo
   audit policy explicitly supports corrective entries. The
   current trace policy
   (`docs/trace_policy_v1.md` §6 Trace Completeness Rule)
   requires every entry to be complete at emit time, so a
   corrective append is permitted only when the policy
   explicitly authorizes it for the specific defect.
5. **Otherwise** stop and report `AUDIT_APPEND_NEEDS_OPERATOR`
   (alias of the canonical
   `AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR` state). Human operator
   decision is required.

**While an audit-ambiguity hold is in effect, the following
repository-side actions are also forbidden**, in addition to
`pr_merge`, `admin_merge`, and `auto_merge`:

- `comment_delete` — comments may not be deleted to suppress
  evidence of the ambiguity.
- `review_dismiss` — reviews may not be dismissed for the same
  reason.
- `force_push` — history may not be rewritten on any branch
  involved in the closeout.

These are encoded in the canonical state's `forbidden_mutations`
list. The audit-log-mutation prohibition itself (audit delete,
audit rewrite, audit trim, audit replace) is encoded in the
state's `description` and `notes` because the validator's
`VALID_MUTATIONS` set is a vocabulary of allowed repository-side
actions, not a vocabulary of audit-log row operations; the policy
text is the authoritative surface for the audit-log-mutation
rule.

**Why this rule exists.** PR #397 (`tooling(governance): add
lifecycle state registry`) merged successfully, but its closeout
included an audit-log rewrite/trim after a malformed entry had
already validated. PR #397 is accepted and closed; the rule is
codified so that this pattern does not repeat. The
`AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR` state is the canonical
hold; the alias `AUDIT_APPEND_NEEDS_OPERATOR` is a reporting
label for the same condition.

**Reference.** `docs/merge_action_audit_log.md` §Append-only,
`docs/trace_policy_v1.md` §6, and
`docs/aed_lifecycle_state_registry.md` §10.

---

## 8. Resume checkpoint rule (codified 2026-06-10)

The AED operator path is multi-turn. A continuation turn (a
later turn that picks up an in-progress PR) must resume from the
**latest verified lifecycle state** and must not restart broad
planning, repeat completed phases, or repeat already-authorized
mutations. PR #397 and PR #398 both reached valid intermediate
states, but continuation turns repeated work or re-entered
earlier phases; PR #398 also repeated the already-completed
thread-resolution phase before the merge phase. Both PRs are
accepted and closed. This rule is codified so future
continuation prompts resume from the latest verified lifecycle
state.

**Statement.** Before taking any mutation in a continuation
turn, the operator must verify, using read-only checks only:

1. Current PR number and URL.
2. Current head SHA (the live PR head, not a stale value).
3. Current lifecycle state (the last verified state from a
   durable source, not from memory).
4. Completed phases (which gates have already been passed and
   which are still in progress).
5. Remaining permitted mutations (which actions are still
   authorized for the current state).
6. Already-performed mutations (which actions have already
   happened in this PR run, so they are not repeated).
7. Protected PR/worktree state (the primary worktree, the
   guarded PRs, and the temp worktree are all as expected).
8. Whether the next requested action is a **continuation** of
   the verified state, not a restart of an already-completed
   phase.

**Stop condition.** If the current state cannot be reconstructed
from durable evidence, stop and report a resume checkpoint
hold (`HOLD_RESUME_CHECKPOINT_NEEDED`). Do not infer readiness
from memory alone. Do not rerun broad workflows when a narrow
continuation action is sufficient.

**Continuation rule examples.** These examples are stated once
in `docs/aed_lifecycle_state_registry.md` §11; the cross-
reference is kept in sync.

- If the previous state was `HOLD_PR_CI_PENDING`, only recheck
  CI and continue to the Codex phase if CI is green. Do not
  re-enter earlier gates (scope, scope guard, final gate).
- If the previous state was `HOLD_CODEX_RESPONSE_PENDING`, do
  not post another Codex ping unless the current-head ping is
  missing or the current head has changed. Use bounded polling
  on the existing ping.
- If the previous state was
  `CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED`, verify the target
  threads (already-resolved, outdated, or still active)
  before resolving; do not merge in the same turn unless
  explicit human authorization is present.
- If the previous state was
  `MERGE_READY_AWAITING_HUMAN_AUTHORIZATION`, do not repeat
  thread resolution; perform final pre-merge verification and
  guarded merge only if exact human authorization is present.
  Thread resolution is not part of the merge phase.
- If the previous state was `PR_MERGED_PENDING_CLOSEOUT`, do
  not merge again; verify the merge commit, main CI, audit
  append, and temp-worktree cleanup. Each of those is its
  own sub-step.
- If the previous state was `PR_MERGED_AND_CLOSED_OUT`, do
  not reopen or mutate; report terminal closeout. The PR is
  done.

**Why this rule exists.** PR #397 and PR #398 both reached
valid intermediate states, but continuation turns repeated
work or re-entered earlier phases. PR #398 also repeated the
already-completed thread-resolution phase before the merge
phase. Treating those PRs as accepted and closed, the rule is
codified so this pattern does not repeat.

**Reference.** `docs/aed_lifecycle_state_registry.md` §11
(the canonical policy surface and the new
`HOLD_RESUME_CHECKPOINT_NEEDED` state) and
`docs/aed_known_safe_command_cookbook.md` §11.2 (the
constraint summary in the cookbook).

---

## 9. Primary worktree sync policy (codified 2026-06-10)

The AED governance workflow depends on a stable **primary
worktree** at `/home/max/Automated-Edge-Discovery` that is
intentionally left stale at the post-closeout head of the last
merged PR. Every governance PR run is built in a separate
temp worktree based on `origin/main`; the primary worktree is
read-only with respect to the governance path. This section
restates the canonical policy in operator-path vocabulary; the
canonical surface is `docs/aed_lifecycle_state_registry.md` §13.

**Statement.** The primary worktree is the source of truth for
the expected protected state of the repo between governance PR
runs. It is **not** a mirror of `origin/main` and is **not**
updated as a side effect of any governance PR. Updating the
primary worktree to a new commit (via `git pull`, `git fetch &&
git checkout`, `git reset`, `git merge`, or any equivalent)
requires **explicit human operator authorization** that names
the exact target ref and is delivered out-of-band from the
governance PR run. "Falls behind", "out of date", or "would be
nice to align" are not authorization.

**Where governance work happens.** Every governance PR run is
performed in a dedicated temp worktree at
`/tmp/aed_runs/worktrees/<task-name>`, branched from
`origin/main` at the current head. The temp worktree is the
**mutation surface**. The primary worktree is **never** the
mutation surface. The temp worktree is removed in the closeout
phase; the primary is not.

**Why the primary is left stale.** The primary worktree is the
"ground truth" reference that:

- Holds the post-closeout head of the last merged governance
  PR (for example, `0a8cee5d2406c970e02e9e217c7f25b0767459e0`
  is the post-closeout head of PR #394; this is the value the
  primary is expected to be at for governance PR runs that
  begin after PR #399).
- Holds the protected PRs (`#384`, `#386`, `#397`, `#398`,
  and future guards) in their verified states. The closeout
  of a new PR verifies that the guarded PRs are byte-identical
  to their pre-run state.
- Anchors the protected-state check pattern: at the start of
  every governance PR run, the operator must verify
  (read-only) that the primary worktree is clean, on the
  `main` branch, and at the expected post-closeout head. If
  the primary has changed unexpectedly, the run must stop.

**Forbidden actions in the primary worktree during a
governance PR run** (encoded in the canonical
`HOLD_MAIN_HEAD_MISMATCH` state's `forbidden_mutations` list
and in the §5 authority table above):

- `git pull` in the primary worktree.
- `git fetch` in the primary worktree followed by any
  checkout, reset, or fast-forward.
- `git reset` in the primary worktree (any mode:
  `--hard`, `--soft`, `--mixed`).
- `git checkout` to a new ref in the primary worktree.
- Any branch switch in the primary worktree.
- Removing, renaming, or recreating the primary worktree.
- Any write to the primary worktree's working tree or index
  that is not a documented, scoped, human-authorized action.

The `HOLD_MAIN_HEAD_MISMATCH` state's `forbidden_mutations`
list already includes the canonical `worktree_update` token
for this purpose. The validator's `VALID_MUTATIONS` set is a
vocabulary of allowed repository-side actions; the
primary-worktree-update prohibition is encoded in prose in
this section and in the registry §13 because a primary-
worktree update is not a governance PR mutation but a
meta-decision about the protected state surface.

**When the primary may be synced.** Only the following
conditions permit a primary-worktree update, and each
requires explicit human operator authorization delivered
out-of-band from any governance PR run:

- The operator has accepted the closeout of the most recent
  governance PR and decides that the primary's post-closeout
  head should be advanced to a new baseline.
- The primary worktree is being re-anchored to a specific
  `origin/main` SHA as part of a deliberate re-baseline
  operation; the operator names the exact target SHA in the
  authorization phrase.

In every case, the operator's authorization phrase must name
the exact target ref and the reason. A future agent must not
infer "sync the primary" from a generic "make sure things
are up to date" instruction.

**Verification pattern (PHASE 1 of every governance PR run).**
At the start of every governance PR run, the operator
performs the following read-only checks against the primary
worktree and stops on any mismatch:

```
git -C /home/max/Automated-Edge-Discovery status --porcelain
git -C /home/max/Automated-Edge-Discovery rev-parse HEAD
git -C /home/max/Automated-Edge-Discovery branch --show-current
```

Requirements:

- `status --porcelain` is empty (no uncommitted or untracked
  changes).
- `HEAD` equals the expected post-closeout head of the last
  merged PR (for the run that begins after PR #399, the
  expected primary HEAD is
  `0a8cee5d2406c970e02e9e217c7f25b0767459e0`, which is the
  post-closeout head of PR #394; this value is recomputed at
  the start of every run).
- `branch --show-current` is `main`.

If any check fails, the run enters
`HOLD_MAIN_HEAD_MISMATCH` and stops. The operator must
reconcile the primary before continuing. Do not push, merge,
or post a Codex ping in this state.

**Why this rule exists.** The primary-worktree-as-anchor
pattern is what makes the protected-state check possible. If
the primary is allowed to drift freely, the protected-state
check loses its meaning and a misaligned primary could be
silently treated as "the expected state" while the repo is
actually in a different configuration. Treating the primary
as a stable, read-only anchor between runs preserves the
invariant that "the primary holds what the last closeout
produced."

**Reference.** `docs/aed_lifecycle_state_registry.md` §13
(the canonical policy surface),
`docs/aed_known_safe_command_cookbook.md` §11.3 (the
constraint summary in the cookbook), and the §5 authority
table above.

---

## 10. Lessons from PR #394

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

## 11. Where next work belongs

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
