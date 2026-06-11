# AED Known-Safe Command Cookbook

**Status:** Active v1
**Scope:** Centralized copy/paste-safe command shapes for AED governance workflows.
**Companion:** `docs/aed_whole_workflow_operator_path.md` (operator path), `docs/merge_authorization_guard.md` (merge authorization), `docs/phase_ledger_merge_readiness_wrapper.md` (final-gate wrapper), `docs/pr_review_comment_gate.md` (review comment gate), `docs/merge_action_audit_log.md` (audit log schema), `docs/trace_policy_v1.md` (audit log policy), `docs/stale_review_thread_auto_resolution_policy.md` (thread resolution policy).

## 1. Purpose

This cookbook centralizes known-safe command shapes for AED governance
workflows. It exists to:

- Reduce prompt drift and command invention across runs.
- Give operators and agents a single, scannable reference for the
  command shapes that have already been validated by past closeouts.
- Provide the canonical argument surface for each helper script so
  flags are not invented at run time.
- Sit next to `docs/aed_whole_workflow_operator_path.md`: that doc
  describes the *path* (lifecycle states, authority table, lessons),
  this doc describes the *command shapes* used at each step.

This cookbook is a **command reference**, not an authority grant.
Operators and agents still need the correct lifecycle state and the
appropriate human authorization before invoking any mutating command.

## 2. Scope and non-goals

This document is operator guidance only. It explicitly does **not**:

- Change any script behavior, command shape, gate contract, or audit
  schema. All of those are owned by their own scripts, docs, and PRs.
- Authorize merge, thread resolution, comment edits, branch deletion,
  admin merge, or auto merge by itself. Those require a separate
  human authorization phrase in the same form documented in
  `docs/merge_authorization_guard.md`.
- Provide runnable examples of forbidden commands. Forbidden patterns
  are listed in §13 in prose only, never as a copy-pasteable command
  line.
- Replace the lower-level operator, gate, or audit docs. If anything
  here appears to conflict with a lower-level doc, the lower-level
  doc governs.

## 3. General command rules

These rules apply to every section below.

- Prefer read-only verification first. Every mutating command should
  be preceded by a read-only inspection of the current PR state.
- Use exact head SHAs. A 7-character or 39-character SHA is not
  acceptable. Substitution is forbidden.
- Use bounded polling only. Max 18 polls at 20s for CI, max 10 polls
  at 30s for Codex. No watch commands. No unbounded `while true`.
- Do not mutate the primary worktree
  (`/home/max/Automated-Edge-Discovery`). Update, reset, pull, and
  branch checkout on the primary worktree require separate explicit
  human authorization.
- Use temp worktrees under `/tmp/aed_runs/worktrees/<task-name>` for
  every PR run. The primary worktree remains intentionally stale at
  the post-closeout head of the most recent merged PR.
- Keep guarded PRs read-only unless a separate scoped task
  explicitly authorizes interaction.
- Never add the admin bypass flag or the auto merge enablement flag
  in this governance path. Both are forbidden at every layer of the
  existing merge stack; see `docs/aed_whole_workflow_operator_path.md`
  §5 admin row and `scripts/local/merge_pr_safely.py` `reject_admin`.
- Do not invent command flags. If a script's `--help` does not show
  a flag, do not pass it. The argparse layer will reject unknown
  flags, but the agent should not be inventing them in the first
  place.
- Do not report PASS unless the real command was run and the output
  was inspected. A logical walk-through is not verification.

## 4. PR state inspection commands

Read-only. Safe to re-run.

```
gh pr view <PR> --repo <owner/name> \
  --json state,headRefOid,mergedAt,mergeable,mergeStateStatus,isDraft,baseRefName,headRefName,title,url
```

What to look for:

- `state`: `OPEN` for an unmerged PR; `MERGED` after closeout.
- `headRefOid`: the 40-char PR head SHA. Must equal the expected SHA
  before any merge, audit, or thread-resolution step.
- `mergeable`: `MERGEABLE` is required before a guarded merge.
- `mergeStateStatus`: `CLEAN` is the pre-merge-ready state.
- `mergedAt`: `null` for any unmerged PR; non-null only after merge.
- `baseRefName`: `main` for a normal AED PR.
- `headRefName`: the task branch (e.g.
  `docs/known-safe-command-cookbook-v1`).
- `isDraft`: `false` for a PR eligible for review and merge.

For PR-level comment listing (used to find a prior Codex ping and
gate-safe body before posting another):

```
gh api graphql -f query='
  query($owner:String!,$name:String!,$number:Int!){
    repository(owner:$owner,name:$name){
      pullRequest(number:$number){
        comments(last:10){
          nodes{ id databaseId author{login} createdAt body }
        }
      }
    }
  }' -F owner=<owner> -F name=<repo> -F number=<PR>
```

**Distinguish pre-merge CI from post-merge CI.** Pre-merge CI uses
`gh pr checks <PR>` against the PR head. After squash merge and
branch deletion, the PR's check view is no longer the authoritative
main-side evidence; use `audit_main_ci_for_head.py` (see §10).

## 5. CI polling cookbook

Pre-merge CI status for an open PR:

```
gh pr checks <PR> --repo <owner/name>
```

Read-only. **Never** use `--watch`. Always use bounded polling.

Bounded polling shape (run inline; do not background it; do not use
`while true`):

```
for i in $(seq 1 18); do
  out=$(gh pr checks <PR> --repo <owner/name> 2>&1)
  if echo "$out" | grep -qiE "fail|FAIL"; then
    echo "POLL $i: FAILED"; echo "$out"; break
  fi
  if ! echo "$out" | grep -qE "pending|PENDING"; then
    echo "POLL $i: ALL PASS"; echo "$out"; break
  fi
  sleep 20
done
```

Total: 18 polls × 20s = 360s = 6 minutes max.

Final states (do not invent new ones):

- All required checks pass → proceed to the next lifecycle step.
- `HOLD_PR_CI_PENDING` → at the bounded poll limit, some check is
  still pending. Do not patch; do not merge; report.
- `HOLD_PR_CI_FAILED` → a required check failed. Read the failing
  job log; push a fix or close the PR. Do not merge.

## 6. Codex review ping cookbook

### 6.1 Preconditions

Post a `@codex review` ping only when all of the following hold:

- PR is `OPEN` at the expected head SHA.
- Pre-merge CI is green on the current head.
- The patch is in scope (docs-only or code-only per the task packet).
- The planned ping body is gate-safe (see §6.3).
- No duplicate Codex ping already exists for the current head.
- Bounded polling for an earlier ping has elapsed (no fast
  re-pinging).

### 6.2 Safe ping template

A short, neutral ping template is the only known-safe body. Do not
add severity markers, do not add policy prose, do not add findings
language. Replace `<NEW_HEAD>` with the current head SHA; do not
edit the template.

```
@codex review

Please review current head <NEW_HEAD>.

This is a docs-only command cookbook for AED governance workflows. It centralizes known-safe command shapes for PR inspection, bounded CI polling, Codex review flow, review-thread inspection, guarded merge, post-merge main CI review, audit append, and temp worktree cleanup.

No script behavior changes are included.
```

### 6.3 Pre-flight body scan

Before posting, scan the exact body for:

- Standalone severity marker text — the text `P` immediately
  followed by a digit `0` through `3` (for example `P0`, `P1`, `P2`,
  `P3`). This pattern is reserved for findings, not for review
  prompts.
- The canonical list of gate-triggering words and phrases, including
  (but not limited to) the following: `stale`, `must fix`, `can fail`,
  `security`, `path traversal`, `malformed`, `nonzero`, `unsafe`. A
  short, neutral `@codex review` prompt with the current head SHA
  and a one-paragraph scope description is the only known-safe body.
  See `docs/pr_review_comment_gate.md` §5 (severity rules) and §3
  (required endpoints) for what the gate is sensitive to.

If the scan fails, do not post. Do not "fix and retry" silently;
report and request a human rephrase.

### 6.4 Duplicate-ping avoidance

For each head, post at most one Codex ping. Inspect the PR's
PR-level comment list (see §4) and look for a chatgpt-codex-connector
clean-pass comment that is *after* the most recent ping for the
current head. If one exists, do not post another.

### 6.5 Post-ping bounded poll

Bounded poll for Codex response:

- Max 10 polls, 30 seconds between polls.
- Inspect: PR-level comments, review submissions, inline review
  comments, review threads (including `isResolved` and `isOutdated`),
  and reactions on PR-level and review comments.

Classify the result:

- Codex raises a new current-head finding → `HOLD_NEW_CODEX_THREAD`.
  Do not resolve threads; do not merge.
- Codex clean-passes the current head and all prior threads are
  outdated or resolved → `CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED` if
  any outdated threads remain unresolved, or
  `MERGE_READY_AWAITING_HUMAN_AUTHORIZATION` if `mergeStateStatus`
  is also `CLEAN`.
- Codex does not respond within the bounded poll → `HOLD_CODEX_RESPONSE_PENDING`.

## 7. Review-thread inspection cookbook

A review-thread inspection is **complete** only when **every page
of review threads** for the PR has been enumerated. A short or
single-page query is **diagnostic only** and **must not** be used
as the pre-merge evidence for the §8 guarded-merge precondition
that "all current-head threads are resolved or outdated."

This is a hard requirement, not a recommendation. GitHub's PR
review-thread connection paginates by default; a fixed-window
`first:N` or `last:N` query silently drops older threads beyond
the window, and any current-head blocker hidden in that dropped
range would be missed. Codex findings on prior PRs have
demonstrated this gap concretely: when the cookbook presented a
non-paginated query as sufficient, current-head P1 blockers on
PRs with more than the window size of review threads could be
omitted from the evidence used to justify a guarded merge.

### 7.1 Preferred path: repository-standard helpers

Inspect the full review-thread set using repository-standard
helpers that already implement pagination and fail-closed
behavior. Two helpers are relevant; both are preferred over raw
GraphQL.

**`scripts/local/wait_for_pr_ready.py`** is the primary helper.
It issues a paginated query (`reviewThreads(first:100)` plus a
`pageInfo { hasNextPage }` check). If `hasNextPage` is true on
any page, the helper returns
`HOLD_CONVERSATION_CHECK_PAGINATION_REQUIRED` and does not
proceed, which is the correct fail-closed behavior. Treat the
helper's exit code and status as the authoritative read for the
"all current-head threads are resolved or outdated" precondition.

**`scripts/local/check_pr_review_comments.py`** is the
review-comment gate. It already covers inline review comments,
issue comments, top-level reviews, and per-review comments, and
it fails closed (`REVIEW_COMMENTS_INCONCLUSIVE`, exit 2) on any
endpoint error or thread-metadata ambiguity. Its thread fetch
uses `reviewThreads(first:100)` for thread-resolution context
only; the helper treats thread metadata as a secondary signal
backing the four comment endpoints. The gate does **not** by
itself enforce pagination, so on its own it is not a complete
thread inventory — but combined with the waiter's
`hasNextPage` check, the two together cover the full thread
set with fail-closed semantics on both metadata and
pagination errors.

**`scripts/local/guarded_pr_closeout_waiter.py`** is the
closeout-time helper that classifies threads into
`current_head_unresolved` and `outdated_unresolved` and treats
any current-head unresolved thread as a blocker. Its thread
fetch is `reviewThreads(first: 100)` with **no** `pageInfo`
check, so on a PR with more than 100 review threads it can
also miss current-head blockers. It must not be relied on as a
standalone thread inventory for the §8 guarded-merge
precondition; treat it as one signal among several.

The recommended pre-merge sequence is:

- run `wait_for_pr_ready.py` for the PR; record its
  `unresolved_thread_count` and verify it is zero (a
  non-zero count, or a
  `HOLD_CONVERSATION_CHECK_PAGINATION_REQUIRED` return, blocks
  the pre-merge path)
- run `check_pr_review_comments.py` with the live
  `--reported-head-sha`; verify exit 0
- cross-check `guarded_pr_closeout_waiter.py`'s
  `current_head_unresolved_count`; verify it is zero
- only then proceed toward the §8 guarded merge

All three signals must agree. A single helper is not
sufficient on its own; the waiter's pagination check, the
gate's fail-closed endpoint coverage, and the closeout
waiter's current-head classification are complementary.

### 7.2 Raw GraphQL (advanced fallback)

If the repository-standard helpers are unavailable or the operator
needs a direct read for a documented reason, a raw GraphQL
inspection is acceptable **only** if it is fully paginated and
treated as a stand-in for the helpers' combined output. The
following shape is the minimum acceptable advanced fallback. It
is **not** interchangeable with the non-paginated examples that
appear in older drafts of this section.

**The recommended raw GraphQL approach is `--paginate`.** The
`gh api graphql --paginate` flag tells `gh` to walk the
connection itself, supplying `endCursor` from
`pageInfo.endCursor` on each subsequent request. The operator
does not pass a cursor variable; `gh` handles it. The query
must still declare a nullable `$endCursor` variable for
compatibility with the first page (where no cursor exists yet).

A complete advanced-fallback inspection must:

- use `gh api graphql --paginate` so `gh` walks every page
  automatically; do not implement an unbounded loop in shell,
  Python, or any other host language
- declare a nullable `$endCursor: String` variable (no `!`)
  inside the query
- select `reviewThreads(first:100)` and include the
  `pageInfo { hasNextPage endCursor }` block
- on every page, record `id`, `isResolved`, `isOutdated`,
  `path`, `line` (or `originalLine` if present), and
  `commit { oid }` on the first comment, then check
  `originalCommit { oid }` if present
- treat the inspection as incomplete if any page fails to
  return, if `hasNextPage` is true but the follow-up page was
  not requested, if the GraphQL response contains an `errors`
  block, if `endCursor` cannot be parsed, or if the live
  `headRefOid` differs from the head used in the earlier
  pre-merge verification
- count **all** unresolved active threads across **all** pages
  before declaring the inspection complete
- never present a one-page slice as complete evidence
- never pass an empty string (`""`) as a cursor; `gh api -F`
  passes `""` through as a literal empty-string cursor, which
  is not a valid GitHub GraphQL cursor. The first request must
  use `after: null` (literal `null`, which `gh -F` converts to
  JSON `null`) or omit `after` entirely; subsequent requests
  use the `endCursor` from the previous page's `pageInfo`

The minimum acceptable advanced-fallback query shape is:

```
gh api graphql --paginate -f query='
  query($owner:String!,$name:String!,$number:Int!,$endCursor:String){
    repository(owner:$owner,name:$name){
      pullRequest(number:$number){
        headRefOid
        reviewThreads(first:100, after:$endCursor){
          pageInfo{ hasNextPage endCursor }
          totalCount
          nodes{
            id isResolved isOutdated
            path line originalLine
            comments(first:1){
              nodes{ id body author{login} commit{oid} originalCommit{oid} path line }
            }
          }
        }
      }
    }
  }' -F owner=<owner> -F name=<repo> -F number=<PR>
```

`gh api graphql --paginate` returns one JSON object per page on
stdout, separated by newlines; the operator must inspect every
page, not only the first. Aggregate the results across pages
before counting unresolved active threads.

If `--paginate` cannot be used (for example, a custom host
blocks the flag, or a wrapper requires explicit cursor
handling), an acceptable manual two-shape pattern is:

- first request: `gh api graphql -f query='...reviewThreads(first:100){...}' ...`
  with no `after` variable, or with `after: null` passed via
  `-F endCursor=null`
- follow-up requests: re-issue the same query, this time with
  `-F endCursor="<returned endCursor from pageInfo>"`, until
  the response's `pageInfo.hasNextPage` is `false`
- an unbounded `while true` loop around these requests is
  **forbidden**; if a fixed maximum of follow-up requests is
  used, the maximum must be documented and respected, and any
  truncation of pagination is a fail-closed block

Either pattern must inspect every page. A non-paginated or
single-page query is **not acceptable** as the pre-merge thread
inventory and must not be used as justification to proceed to
§8.

### 7.3 How to interpret a thread

The interpretation rules are unchanged from prior versions and
apply per thread, after the full inventory is built:

- `isResolved: true` → the thread is closed; not a blocker.
- `isOutdated: true` → the thread is anchored to a different
  commit than the current head; not a blocker on the current
  head, but the agent must not assume the underlying issue is
  gone.
- `commit.oid` (or `originalCommit.oid` when present) on the
  thread's first comment equals the current `headRefOid` AND
  `isResolved: false` AND `isOutdated: false` → current-head
  active blocker. Do not merge.
- Thread metadata retrieval errors, missing `isResolved` /
  `isOutdated` fields, GraphQL `errors` blocks, `hasNextPage`
  not exhausted, `endCursor` parse failures, or `after`
  cursors that the server rejects as malformed → treat as a
  blocker. Do not merge.

### 7.4 Resolve-only preconditions

Resolve-only preconditions (per
`docs/stale_review_thread_auto_resolution_policy.md`) — 14
preconditions must all be true before resolving a single stale
thread — apply to any individual resolution action. A complete
thread inventory (§7.1 or §7.2) is a **prerequisite** for any
resolve-only decision, not a substitute for the 14 preconditions.
The agent does not gain blanket authority from the inventory.

The resolve mutation shape is **not** reproduced in this
cookbook. It is a separate concern documented in
`docs/stale_review_thread_auto_resolution_policy.md`, and it
must never be issued without an explicit human authorization
in a later turn and verification of all 14 preconditions from
that policy. A complete thread inventory (§7.1 or §7.2) is a
prerequisite for the resolve-only authorization prompt but is
not a substitute for those 14 preconditions.

Never:

- Resolve a thread that is `isOutdated: false` AND on the current
  head. The underlying issue may still be present in the diff.
- Dismiss a review (no `dismissReview` or equivalent).
- Resolve multiple unrelated threads in one pass.
- Use a one-page or non-paginated query as the basis for a
  resolve-only authorization prompt. The inventory must be
  complete first.

## 8. Guarded merge cookbook

The guarded merge command shape:

```
gh pr merge <PR> \
  --repo <owner/name> \
  --squash \
  --delete-branch \
  --match-head-commit <EXPECTED_HEAD_SHA>
```

Preconditions (all must hold immediately before merge):

- Lifecycle state is `MERGE_READY_AWAITING_HUMAN_AUTHORIZATION`.
- CI is green on the current head.
- Codex has clean-passed the current head (or is not in scope).
- All current-head review threads are resolved or outdated.
- `mergeStateStatus` is `CLEAN`.
- The exact human authorization phrase has been issued by the human
  in the form documented in `docs/merge_authorization_guard.md`
  §Authorization phrase, with the exact 40-character live head SHA.
- Final pre-merge verification has just been re-run (do not rely on
  stale pre-merge state).

What the command does:

- The pre-merge SHA passed to `--match-head-commit` is the reviewed
  branch commit at the moment of authorization. GitHub rejects the
  merge if the PR head has changed since.
- After a squash merge, the commit returned by `mergeCommit.oid`
  (via `gh pr view --json mergeCommit`) is a brand-new commit on
  `main` and is **not** equal to the PR head SHA. The audit row
  records both values separately as `head_sha` and `merge_sha`.
- `--delete-branch` removes the task branch from the remote. The
  agent must not declare closeout complete until branch deletion is
  confirmed (see §9).

Do not add the admin bypass flag or the auto merge enablement flag
in this governance path. Both are forbidden at every layer of the
existing merge stack; see `docs/aed_whole_workflow_operator_path.md`
§5 admin row and `scripts/local/merge_pr_safely.py` `reject_admin`.

Do not pass `--admin` or `--auto` to `gh pr merge`. If a future policy
ever permits one, that requires a separate operator policy PR and
its own change to the merge stack; this cookbook treats both flags
as permanently outside the operator path.

## 9. Post-merge verification cookbook

Read the merge result and verify it landed on `main`:

```
gh pr view <PR> --repo <owner/name> \
  --json state,mergedAt,mergeCommit
git -C <repo-root> fetch origin main --prune
git -C <repo-root> rev-parse origin/main
```

Checks:

- `state` must be `MERGED`.
- `mergedAt` must be non-null.
- `mergeCommit.oid` (from `gh pr view`) is the new commit on `main`.
- `origin/main` (after `git fetch origin main`) must equal
  `mergeCommit.oid`. If it does not, the merge did not land where
  the audit row claims; do not declare closeout.
- The audit row records `head_sha` (the pre-merge PR head, used
  for `--match-head-commit`) and `merge_sha` (the post-merge
  commit) as distinct fields. See `docs/merge_action_audit_log.md`
  §`pr_merge`.

Verify the task branch was deleted:

```
git -C <repo-root> ls-remote --heads origin <task-branch>
```

No output means the branch was successfully deleted.

## 10. Post-merge main CI audit cookbook

The repo-standard post-merge CI verifier is
`scripts/local/audit_main_ci_for_head.py`. It polls GitHub Actions
workflow runs for an exact main-branch head SHA and classifies the
result.

```
python3 scripts/local/audit_main_ci_for_head.py \
  --repo <owner/name> \
  --head-sha <MERGE_COMMIT_SHA> \
  --branch main \
  --required-workflow <name> \
  --max-polls 18 \
  --poll-seconds 20 \
  --output-json /tmp/audit.json \
  --output-md /tmp/audit.md
```

The argument surface comes from the script's own `--help`. Do not
invent flags. `--required-workflow` is optional but recommended.

After the audit, read the JSON output and inspect `status`:

- `MAIN_CI_AUDIT_GREEN` → proceed with audit append and closeout.
- `HOLD_MAIN_CI_PENDING` → at the bounded poll limit, runs were
  still in flight. Re-check later; do not declare closeout yet.
- `HOLD_MAIN_CI_FAILED` → a required workflow failed on the merge
  commit. Investigate; do not declare closeout.
- `HOLD_MAIN_CI_MISSING_REQUIRED_WORKFLOW` → no run found for the
  required workflow at this head. Investigate; do not declare
  closeout.
- `HOLD_MAIN_CI_NO_RUNS_FOR_HEAD` → no runs at all for the head.
  Investigate; do not declare closeout.

Map to operator-path lifecycle states (per
`docs/aed_whole_workflow_operator_path.md` §4):

- `MAIN_CI_AUDIT_GREEN` → `PR_MERGED_PENDING_CLOSEOUT` advances to
  `PR_MERGED_AND_CLOSED_OUT` once the audit row is appended.
- `HOLD_MAIN_CI_PENDING` → `HOLD_POST_MERGE_CI_PENDING`.
- `HOLD_MAIN_CI_FAILED` → `HOLD_POST_MERGE_CI_FAILED`.
- `HOLD_MAIN_CI_MISSING_REQUIRED_WORKFLOW` and
  `HOLD_MAIN_CI_NO_RUNS_FOR_HEAD` → `HOLD_POST_MERGE_CI_NOT_OBSERVED`.

### 10.1 Superseded and cancelled runs policy

GitHub Actions re-triggers a workflow when its source files change
after a push, or when an upstream event re-fires. Each re-trigger
produces a new workflow run; the prior run is marked
`cancelled` (or `skipped`) but its databaseId remains visible for the
same head SHA. The audit tool uses **newest-authoritative-per-workflow**
semantics for exact head SHA matching:

- Runs are grouped by `workflowName` (case-sensitive exact match).
- Within each workflow group, runs are sorted newest-first by
  `createdAt` (with `updatedAt` / `databaseId` as tiebreakers).
- The **newest run overall** for that workflow on the exact head is
  inspected first. If that newest run is still in flight (queued,
  in_progress, requested, waiting), the workflow is **PENDING** — an
  older completed success on the same workflow/head must NOT shadow
  the newer in-flight attempt. The in-flight rerun is the authoritative
  attempt and the audit must wait for it to finish. Older terminal and
  in-flight runs are listed in `superseded_cancelled_runs` as audit
  history.
- If the newest run is terminal, its conclusion becomes the
  authoritative verdict for that workflow. Older cancelled/skipped
  runs for the same workflow and head are not classified as failures.
  They appear in `superseded_cancelled_runs` as audit history when a
  later authoritative success exists.
- A cancelled or skipped run is only a failure if it is the newest
  terminal run for that workflow and head (no later success exists).
- A `failure`, `timed_out`, `action_required`, or `startup_failure`
  conclusion is always a failure when it is the newest terminal
  verdict for a workflow, even if an older success exists.

This policy means a re-triggered workflow that produced
`conclusion=cancelled` for the first attempt and `conclusion=success`
for the re-trigger on the same exact head is reported as GREEN; the
older cancelled run is listed in `superseded_cancelled_runs` and
appears in the markdown report under the "Superseded cancelled
runs" section. Inspect that section to confirm the supersession
chain if the audit report is unexpected.

## 11. Audit append cookbook

The repo-standard audit appender is
`scripts/local/append_merge_action_audit.py`. The audit log lives
at `~/.hermes/aed/audit/log.jsonl`.

```
python3 scripts/local/append_merge_action_audit.py \
  --event-type pr_merge \
  --pr-number <PR> \
  --branch <task-branch> \
  --head-sha <PR_HEAD_SHA> \
  --merge-sha <MERGE_COMMIT_SHA> \
  --merged-at <MERGED_AT> \
  --ci-status success \
  --codex-status clean \
  --scope-status clean \
  --authorization-phrase "I confirm merge PR #<PR> at <PR_HEAD_SHA>." \
  --gate-catches-json '{"merge_method":"squash","files_added":"...","files_modified":"...","codex_findings_fixed":"..."}' \
  --no-hermes-touched \
  --no-dispatch-occurred \
  --no-production-board-touched
```

Notes:

- `head_sha` and `merge_sha` are recorded as **distinct** fields, per
  `docs/merge_action_audit_log.md` §`pr_merge`. The agent must not
  substitute one for the other.
- `--gate-catches-json` accepts a JSON object with string values.
  Lists are not accepted for fields like `files_added` /
  `files_modified`; use a single string (or `+`-separated paths if
  multiple) instead.
- Do **not** pass `--output <file>` to redirect the entry to a
  separate file. The script appends to the canonical audit log
  path by default; passing `--output` writes the entry to a
  separate file and bypasses the main log.
- The append script's event type is one of `pr_merge`,
  `controlled_smoke_create`, `external_action`, `blocked_action`.
  For a normal AED closeout, the event is `pr_merge`.

After the append, validate the audit log:

```
python3 scripts/local/validate_merge_action_audit_log.py \
  --input ~/.hermes/aed/audit/log.jsonl \
  --allow-legacy \
  --expected-prs-json "[<list-of-prs-in-the-log>]" \
  --output-json /tmp/audit_validation.json \
  --output-md /tmp/audit_validation.md
```

The expected-prs list must match the actual PRs already present in
the log plus the PR just appended. If the script is unavailable or
its schema is ambiguous, do not invent arguments; report
`AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR` and stop.

### 11.1 Append-only constraint (codified 2026-06-10)

The audit log at `~/.hermes/aed/audit/log.jsonl` is append-only. The
constraint is the policy surface for the registry's
`AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR` state.

- **Do not delete, trim, rewrite, or replace** any audit row that
  has already been appended, even if the row is malformed,
  non-canonical, incomplete, or suboptimal. "Looks wrong" is not
  authorization.
- **If a previously-appended row is found to be malformed,
  non-canonical, incomplete, or suboptimal**, the operator must
  follow the decision tree in
  `docs/aed_lifecycle_state_registry.md` §10:
  1. Run the audit validator first.
  2. If validation fails, stop and report an audit hold.
  3. If validation passes but the row is non-canonical, do not
     rewrite it.
  4. Append a corrective follow-up row only if the repo audit
     policy explicitly supports corrective entries.
  5. Otherwise stop and report `AUDIT_APPEND_NEEDS_OPERATOR`
     (alias of the canonical
     `AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR`).
- **Do not delete comments or dismiss reviews** to suppress
  evidence of an audit-ambiguity hold. While the hold is in
  effect, `comment_delete` and `review_dismiss` are forbidden
  repository-side actions.
- **Do not force-push** any branch involved in the closeout
  while the hold is in effect.

The alias name `AUDIT_APPEND_NEEDS_OPERATOR` is a reporting
label for the same canonical state. Use either name in operator
prompts; the registry stores a single canonical entry.

### 11.2 Resume checkpoint constraint (codified 2026-06-10)

A continuation turn (a later turn that picks up an in-progress
PR) must resume from the latest verified lifecycle state and
must not restart broad planning, repeat completed phases, or
repeat already-authorized mutations. The full rule is the
canonical policy in `docs/aed_lifecycle_state_registry.md` §11.

**Pre-mutation read-only verification.** Before any mutation in
a continuation turn, the operator must verify:

1. Current PR number and URL.
2. Current head SHA (live, not stale).
3. Current lifecycle state (durable, not from memory).
4. Completed phases.
5. Remaining permitted mutations.
6. Already-performed mutations.
7. Protected PR/worktree state.
8. Whether the next action is a continuation, not a restart.

**Stop condition.** If the state cannot be reconstructed from
durable evidence, stop and report `HOLD_RESUME_CHECKPOINT_NEEDED`.
Do not infer readiness from memory. Do not rerun broad
workflows when a narrow continuation action is sufficient.

**Continuation rule examples.** The full list of examples is in
`docs/aed_lifecycle_state_registry.md` §11. The cookbook
captures the most common ones:

- `HOLD_PR_CI_PENDING` -> recheck CI only; do not re-enter
  earlier gates.
- `HOLD_CODEX_RESPONSE_PENDING` -> bounded poll the existing
  ping; do not post a duplicate.
- `CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED` -> verify target
  threads; do not merge in the same turn without explicit
  human authorization.
- `MERGE_READY_AWAITING_HUMAN_AUTHORIZATION` -> do not repeat
  thread resolution; perform final pre-merge verification and
  guarded merge only with exact human authorization.
- `PR_MERGED_PENDING_CLOSEOUT` -> do not merge again; verify
  merge commit, main CI, audit append, and temp-worktree
  cleanup.
- `PR_MERGED_AND_CLOSED_OUT` -> do not reopen or mutate;
  report terminal closeout.

**While in `HOLD_RESUME_CHECKPOINT_NEEDED`, the following are
forbidden in addition to the canonical mutation prohibitions
already encoded in the registry state:**

- Post a duplicate Codex ping for the same head.
- Rewrite any already-appended audit row (append-only closeout
  rule from §11.1 governs).
- Repeat an already-completed mutation.

The canonical mutation vocabulary covers `pr_merge`,
`thread_resolve`, `comment_delete`, `review_dismiss`, and
`force_push`; the three policy-level prohibitions above are
documented here for the operator's reference.

### 11.3 Primary worktree sync constraint (codified 2026-06-10)

The primary worktree at `/home/max/Automated-Edge-Discovery`
is the source of truth for the expected protected state of
the repo between governance PR runs. It is **not** a mirror
of `origin/main` and is **not** updated as a side effect of
any governance PR. The constraint is the policy surface for
the canonical `HOLD_MAIN_HEAD_MISMATCH` state's
`worktree_update` prohibition and is the canonical reference
for the read-only verification pattern at PHASE 1 of every
governance PR run.

**Verification pattern (PHASE 1 of every governance PR run).**
Before any other action in a governance PR run, the operator
must perform the following read-only checks against the
primary worktree and stop on any mismatch:

```
git -C /home/max/Automated-Edge-Discovery status --porcelain
git -C /home/max/Automated-Edge-Discovery rev-parse HEAD
git -C /home/max/Automated-Edge-Discovery branch --show-current
```

Requirements:

- `status --porcelain` is empty.
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

**Forbidden primary-worktree operations during a governance PR
run.** In keeping with §2, this section does **not** list any
copy-pasteable forbidden command lines. The following
categories of primary-worktree operation are forbidden during a
governance PR run, unless exact primary-sync authorization
exists:

- Pulling from a remote in the primary worktree is forbidden
  unless exact primary-sync authorization exists.
- Fetching followed by any checkout, reset, or merge in the
  primary worktree is forbidden unless exact primary-sync
  authorization exists.
- Resetting the primary worktree (in any mode) is forbidden
  unless exact primary-sync authorization exists.
- Checking out a new ref in the primary worktree is forbidden
  unless exact primary-sync authorization exists.
- Switching branches in the primary worktree is forbidden
  unless exact primary-sync authorization exists.
- Any other write to the primary worktree's working tree or
  index that is not a documented, scoped, human-authorized
  action is forbidden.

These prohibitions are encoded in the canonical
`HOLD_MAIN_HEAD_MISMATCH` state's `forbidden_mutations` list
as the canonical `worktree_update` token, and are restated in
prose in this section, in `docs/aed_whole_workflow_operator_path.md`
§9, and in `docs/aed_lifecycle_state_registry.md` §13. The
full prose-only forbidden-pattern list for the governance path
is in §13 of this cookbook; the canonical `worktree_update`
token references that list. The primary-worktree-update
prohibition is a policy-level constraint, not a governance PR
mutation; the canonical validator's `VALID_MUTATIONS` set is a
vocabulary of allowed repository-side actions, and a
primary-worktree update is intentionally not in that set.

**When the primary may be synced.** Only with explicit human
operator authorization delivered out-of-band from any
governance PR run. The authorization phrase must name the
exact target ref and the reason. A future agent must not
infer "sync the primary" from a generic "make sure things
are up to date" instruction.

**Where governance work happens.** Every governance PR run
is performed in a dedicated temp worktree at
`/tmp/aed_runs/worktrees/<task-name>`, branched from
`origin/main` at the current head. The temp worktree is the
**mutation surface**; the primary worktree is **never** the
mutation surface. The temp worktree is removed in the
closeout phase; the primary is not.

## 12. Worktree cleanup cookbook

Before removing the temp worktree, verify it is clean:

```
git -C /tmp/aed_runs/worktrees/<task-name> status --porcelain
```

An empty status is required.

Remove only the temp worktree (the primary worktree is never
touched in this governance path):

```
git -C /home/max/Automated-Edge-Discovery worktree remove --force \
  /tmp/aed_runs/worktrees/<task-name>
git -C /home/max/Automated-Edge-Discovery worktree prune
```

Verify removal:

- The temp worktree path no longer exists.
- `git worktree list` no longer includes the temp worktree path.

Do not remove or mutate `/home/max/Automated-Edge-Discovery` in
this turn. The primary worktree remains intentionally stale at the
post-closeout head of the last merged PR.

## 13. Forbidden command patterns (prose only)

This section lists forbidden categories in prose. It does **not**
include any runnable bad command. Forbidden patterns are
categorized below; the agent must not invoke any of them in this
governance path.

- The admin bypass flag on the merge command is forbidden at every
  layer of the existing merge stack
  (`scripts/local/merge_pr_safely.py` argparse + `reject_admin()`,
  `scripts/local/merge_readiness_with_phase_ledger.py` hard-reject).
  No operator phrase, prior token, or one-off authorization can
  grant the admin bypass in this governance path.
- The auto merge enablement flag on the merge command is forbidden
  in this governance path.
- Force-push (`git push --force`, `--force-with-lease`) is
  forbidden.
- Watch commands (`--watch`, `while true` loops) are forbidden.
  Use bounded polling.
- Update, reset, and pull of the primary worktree
  (`/home/max/Automated-Edge-Discovery`) are forbidden in this
  governance path. They require separate explicit human
  authorization. The canonical `HOLD_MAIN_HEAD_MISMATCH`
  state's `forbidden_mutations` list already includes
  `worktree_update`; the full list of forbidden primary-
  worktree commands is in §11.3 and the operator path §9.
- Comment deletion is forbidden.
- Review dismissal (`dismissReview` or equivalent) is forbidden.
- Thread resolution without explicit human authorization in a
  later turn is forbidden.
- `memory` and `fact_store` writes during a PR run are forbidden.
- Skill creation and skill update during a PR run are forbidden.

## 14. State-to-command index

A compact map from lifecycle state to the next safe command.

| Lifecycle state | Next command (read this cookbook's section) |
|---|---|
| `NOT_RUN` | Run the runner against the live head. No cookbook section. |
| `HOLD_PR_CI_PENDING` | §5 bounded CI poll. |
| `HOLD_PR_CI_FAILED` | Read failing job log; push a fix; close. Do not merge. |
| `HOLD_CODEX_RESPONSE_PENDING` | §6.5 bounded Codex poll. |
| `HOLD_NEW_CODEX_THREAD` | Patch or hold. Do not resolve; do not merge. |
| `CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED` | §7 build a **complete** thread inventory (full pagination or the repository-standard helpers); then resolve-only authorization prompt to human. A non-paginated or one-page query is **not** an acceptable inventory. |
| `MERGE_READY_AWAITING_HUMAN_AUTHORIZATION` | §4 final pre-merge; §8 guarded merge with human authorization phrase. |
| `PR_MERGED_PENDING_CLOSEOUT` | §9 post-merge verification; §10 main CI audit; §11 audit append; §12 worktree cleanup. |
| `PR_MERGED_AND_CLOSED_OUT` | Terminal. |
| `HOLD_POST_MERGE_CI_PENDING` | Re-check audit with a fresh bounded poll. |
| `HOLD_POST_MERGE_CI_FAILED` | Investigate; consider revert; do not declare closeout. |
| `HOLD_POST_MERGE_CI_NOT_OBSERVED` | Re-check with a fresh bounded poll. |
| `HOLD_MAIN_HEAD_MISMATCH` | Stop. `origin/main` does not match the expected SHA, or the primary worktree has changed unexpectedly. Run the read-only verification pattern in §11.3 and the operator path §9; reconcile the primary before continuing. Do not push, merge, or post a Codex ping in this state. |
| `AUDIT_APPEND_SKIPPED_NEEDS_OPERATOR` (alias: `AUDIT_APPEND_NEEDS_OPERATOR`) | Stop. Audit script or schema usage was ambiguous, or a previously appended audit row is malformed / non-canonical / incomplete / suboptimal. Follow the append-only constraint in §11.1 and the operator decision tree in `docs/aed_lifecycle_state_registry.md` §10. |
| `HOLD_RESUME_CHECKPOINT_NEEDED` | Stop. Continuation lacks enough durable evidence to determine the next action. Reconstruct state using read-only checks (PR number and URL, current head SHA, current lifecycle state, completed phases, remaining permitted mutations, already-performed mutations, protected PR/worktree state). Follow the resume checkpoint constraint in §11.2 and the canonical policy in `docs/aed_lifecycle_state_registry.md` §11. |

## 15. Relationship to future tools

This cookbook is intentionally a doc, not a tool. Future helpers
may reference it as the source of truth for the canonical command
shapes. The future-work items that would naturally build on this
cookbook are:

- A PR closeout checklist generator that emits the exact post-merge
  verification shape and the audit row schema from §9 and §11,
  parameterized by PR number and merge SHA.
- A review-thread resolver helper that codifies the 14
  preconditions in
  `docs/stale_review_thread_auto_resolution_policy.md` and produces
  the required audit record. The helper must remain read-only at
  the API level; thread resolution must still be a separate human
  action. The helper's thread inventory must use full pagination
  (exhausting `pageInfo.hasNextPage`) or one of the
  repository-standard paginated helpers (`wait_for_pr_ready.py`'s
  `hasNextPage` check, or `check_pr_review_comments.py` combined
  with the waiter); a non-paginated `first:N` or `last:N` query is
  **not** an acceptable thread inventory for the helper to base
  resolve decisions on.
- A forbidden-command scanner that runs over a planned command
  surface and rejects any pattern from §13 before it can be
  executed. This cookbook's §13 prose list is the reference
  vocabulary.
- A lifecycle-state enum in code, keyed to the reporting
  vocabulary in `docs/aed_whole_workflow_operator_path.md` §4, with
  transitions derived from gate outputs and merge authorization.
  This cookbook's §14 table is the reference vocabulary.
- A guarded-flow orchestrator that composes the §5 bounded CI
  poll, §6 Codex review flow, §7 thread inspection, §8 guarded
  merge, §9 post-merge verification, §10 main CI audit, §11 audit
  append, and §12 worktree cleanup into a single driver — but
  never as a single mutating command. The driver still requires
  the same human authorization phrase at the §8 step.
- A structured-output schema for each §3 command block, so future
  agents can validate the chain mechanically.

---

*This document is the v1 of the known-safe command cookbook.
Revisions should be docs-only PRs that update this file in place,
with a CHANGELOG-style note appended at the bottom of each section
that changes. The lifecycle vocabulary in §14 is reporting-only;
no script reads or writes these values, and no gate transitions
on them.*
