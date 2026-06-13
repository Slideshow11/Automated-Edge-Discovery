# AED Rules Inventory

> Canonical list of Automated Edge Discovery (AED) governance rules.
> This document is the authoritative single-page index of every rule the
> AED harness enforces, where it lives, and how strongly it is enforced.
> It exists to enable a clean migration to OpenHands without drift,
> duplication, or forgotten governance rules.

## 1. Scope and purpose

This document:

- Lists every AED governance rule with a stable `AED-RULE-NNN` identifier.
- Identifies the **current source of truth** (doc, script, schema, prompt) and
  the **current enforcement location** (CI check, validator, runtime guard,
  test fixture, operator prompt, or "missing").
- Identifies the **OpenHands migration target** (which OpenHands construct
  will own the rule post-migration).
- Calls out **prompt-only / manual-only** rules explicitly so the migration
  team knows which rules need hardening.

This document does **not**:

- Change any rule. Rules live in their source-of-truth docs and scripts.
- Authorize any action. Authorization is owned by
  `docs/merge_authorization_guard.md` and the human authorization phrase.
- Replace lower-level docs. If this index conflicts with a source-of-truth
  doc, the source-of-truth doc governs.

## 2. Source baseline

The inventory is grounded in the following merged PRs and their docs/scripts:

- **PR #386** `tooling/guarded-pr-closeout-waiter` — closeout waiters
- **PR #390** `feat: add phase execution ledger guard` — phase ledger
- **PR #391** `feat: wire phase ledger into single-task runner`
- **PR #392** `feat: consume runner phase ledger in final gate`
- **PR #393** `feat: wire phase ledger into merge readiness`
- **PR #394** `docs: document phase-ledger merge readiness wrapper`
- **PR #395** `docs: add whole-workflow operator path`
- **PR #396** `docs: add known-safe command cookbook`
- **PR #397** `tooling: add lifecycle state registry`
- **PR #398** `docs: codify audit append-only closeout rule`
- **PR #399** `docs: codify resume checkpoint continuation rule`
- **PR #400** `docs: codify primary worktree sync policy`
- **PR #401** `tooling: handle superseded main CI audit runs`
- **PR #402** `tooling: classify Codex responses across comments and reviews`

Key existing governance docs that this index references (canonical sources):

- `docs/aed_whole_workflow_operator_path.md` — operator path
- `docs/aed_known_safe_command_cookbook.md` — command shapes
- `docs/aed_lifecycle_state_registry.md` — lifecycle state definitions
- `docs/merge_authorization_guard.md` — merge authorization phrase
- `docs/merge_action_audit_log.md` — audit log schema
- `docs/phase_ledger_merge_readiness_wrapper.md` — phase ledger wrapper
- `docs/pr_review_comment_gate.md` — review-comment gate
- `docs/stale_review_thread_auto_resolution_policy.md` — thread resolution policy
- `docs/trace_policy_v1.md` — trace/audit policy

Key enforcement scripts (canonical locations):

- `scripts/local/audit_codex_response_for_pr.py` — Codex response classifier
- `scripts/local/audit_main_ci_for_head.py` — main CI audit
- `scripts/local/aed_lifecycle_states.py` — lifecycle state registry CLI
- `scripts/local/check_pr_review_comments.py` — review-comment gate
- `scripts/local/check_pr_scope.py` — scope guard CLI
- `scripts/local/scope_guard.py` — scope auditor
- `scripts/local/merge_pr_safely.py` — guarded merge entry point
- `scripts/local/merge_readiness_with_phase_ledger.py` — phase-ledger wrapper
- `scripts/local/phase_ledger.py` / `phase_exec.py` — phase ledger
- `scripts/local/append_merge_action_audit.py` — audit append
- `scripts/local/check_stale_review_thread_resolution.py` — thread policy check
- `scripts/local/verify_final_head_merge_command.py` — final-head guard

## 3. Rule taxonomy

Rules are grouped into the following categories. Each rule carries a
`Category:` field referencing one of these:

1. **PW** — Primary worktree protection
2. **TW** — Temp worktree / isolated workspace rules
3. **EH** — Exact-head verification
4. **PS** — PR state and merge-state verification
5. **MA** — Merge authorization
6. **TR** — Thread resolution authorization
7. **CR** — Codex response classification
8. **PD** — Codex ping de-duplication
9. **CP** — Codex clean-pass detection
10. **RI** — Review-thread inventory completeness
11. **II** — Issue-comment inventory completeness
12. **RS** — Review-submission inventory completeness
13. **PN** — Nested review-thread comment pagination
14. **TT** — Timestamp and timezone validation
15. **CI** — CI gating
16. **SG** — Scope guard
17. **AA** — Audit append-only / audit non-mutation
18. **PH** — Protected historical PRs
19. **RC** — Resume checkpoint / lifecycle continuation
20. **GL** — Gate-safe comment language
21. **FO** — Forbidden operations
22. **FR** — Final report requirements
23. **OM** — OpenHands migration requirements

## 4. Canonical rule list

### AED-RULE-001 — No primary worktree mutation

- **Category:** PW
- **Rule statement:** The primary worktree at `/home/max/Automated-Edge-Discovery`
  on `main` at HEAD `0a8cee5d2406c970e02e9e217c7f25b0767459e0` (pre-PR #402
  closeout) MUST NOT be mutated, reset, pulled, checked-out, or synced except
  by a separate explicit human authorization.
- **Current source of truth:** `docs/aed_known_safe_command_cookbook.md` §3;
  `docs/aed_whole_workflow_operator_path.md` §11; `docs/merge_authorization_guard.md`.
- **Current enforcement location:** Operator prompt / cookbook.
- **Current enforcement strength:** Soft (operator must consciously read).
- **Current test coverage:** None — no automated test verifies primary HEAD
  is unchanged.
- **Failure mode if missed:** Primary worktree HEAD advances unintentionally;
  subsequent local runs and operator actions operate on a different tree
  than the rest of the governance stack expects.
- **OpenHands migration target:** `AEDGitTool` PreToolUse hook refuses any
  `git` mutation when `cwd` matches the primary worktree path and
  `auth.phrase` is absent.
- **Priority:** High.

### AED-RULE-002 — No primary sync unless explicitly authorized

- **Category:** PW
- **Rule statement:** `git pull`, `git fetch` (with merge/rebase), `git reset`,
  and `git checkout` against the primary worktree require a separate
  explicit human authorization phrase in the form documented in
  `docs/merge_authorization_guard.md`.
- **Current source of truth:** `docs/aed_known_safe_command_cookbook.md` §3;
  PR #400.
- **Current enforcement location:** Operator prompt / cookbook / registry
  constraint.
- **Current enforcement strength:** Soft.
- **Current test coverage:** Schema-level only (`schemas/aed_lifecycle_states_v1.json`
  marks the primary-sync mutation as `human_authorization_required`).
- **Failure mode if missed:** Silent primary worktree drift; phase-ledger
  evidence from a different main tip; audit log entries that disagree with
  the operator's actual local state.
- **OpenHands migration target:** `AEDGitTool` refuses primary worktree
  mutating commands without `auth.phrase` in tool call payload.
- **Priority:** High.

### AED-RULE-003 — Use temp worktrees under `/tmp/aed_runs/worktrees/<task-name>`

- **Category:** TW
- **Rule statement:** Every PR run uses a fresh isolated clone or worktree
  under `/tmp/aed_runs/worktrees/<task-name>` and operates on a feature
  branch.
- **Current source of truth:** `docs/aed_known_safe_command_cookbook.md` §3;
  `docs/temp_worktree_human_apply_workflow.md`.
- **Current enforcement location:** Operator prompt / cookbook.
- **Current enforcement strength:** Soft.
- **Current test coverage:** None.
- **Failure mode if missed:** Work performed in the primary worktree; risk
  of accidentally committing to `main`.
- **OpenHands migration target:** `AEDWorkspaceProvisioner` automatically
  creates an isolated temp worktree per task; rejects execution in any
  directory not under `/tmp/aed_runs/worktrees/`.
- **Priority:** High.

### AED-RULE-004 — Use exact 40-character head SHAs

- **Category:** EH
- **Rule statement:** Any reference to a commit SHA in commands, packets,
  audit entries, or authorization phrases MUST be the full 40-character
  hex form. 7-character or 39-character SHAs are forbidden.
- **Current source of truth:** `docs/aed_known_safe_command_cookbook.md` §3.
- **Current enforcement location:** Operator prompt; `merge_pr_safely.py`
  matches `--match-head-commit` against the full SHA.
- **Current enforcement strength:** Medium (some scripts enforce; others
  rely on the operator).
- **Current test coverage:** `tests/test_merge_pr_safely.py` and similar.
- **Failure mode if missed:** Mismatched SHA, wrong-merge, or false-positive
  CI green.
- **OpenHands migration target:** `AEDPolicy` validates SHA length and
  hex format on every payload; `AEDReportTool` formats all SHAs as 40-char.
- **Priority:** High.

### AED-RULE-005 — Verify live PR head before any merge-authorizing action

- **Category:** EH
- **Rule statement:** Before resolving threads, posting a Codex ping, or
  invoking `gh pr merge`, the agent MUST re-read `gh pr view
  --json headRefOid` and confirm the live head matches the expected head.
  If the head changed, the agent MUST stop and report `HOLD_HEAD_CHANGED`.
- **Current source of truth:** `docs/aed_known_safe_command_cookbook.md` §13;
  `docs/phase_ledger_merge_readiness_wrapper.md`.
- **Current enforcement location:** Operator prompt + `verify_final_head_merge_command.py`.
- **Current enforcement strength:** Medium.
- **Current test coverage:** Phase-ledger wrapper tests; final-gate
  verifier tests.
- **Failure mode if missed:** A force-push or out-of-band commit between
  authorize and execute can merge the wrong tree.
- **OpenHands migration target:** `AEDGitHubTool` re-checks head on every
  mutating call; refuses if head drifted.
- **Priority:** High.

### AED-RULE-006 — Reject the admin-bypass and auto-merge flags at every layer

- **Category:** FO + MA
- **Rule statement:** No `gh pr merge` may be invoked with the
  admin-bypass flag and no `gh pr merge` may be invoked with the
  auto-merge enablement flag. Both flags are forbidden by argparse,
  by the wrapper, and by the operator prompt.
- **Current source of truth:** `docs/phase_ledger_merge_readiness_wrapper.md` §Operator
  model invariants; `docs/aed_whole_workflow_operator_path.md` §5; `scripts/local/merge_pr_safely.py::reject_admin`.
- **Current enforcement location:** Hard-coded argparse + `_reject_admin`
  defense-in-depth + operator prompt.
- **Current enforcement strength:** Hard.
- **Current test coverage:** `tests/test_merge_pr_safely.py` and
  `tests/test_merge_readiness_with_phase_ledger.py`.
- **Failure mode if missed:** Admin merge can override branch protection;
  auto-merge removes the human authorization gate.
- **OpenHands migration target:** `AEDMergeTool` argparse + `AEDPolicy`
  forbidden-flag list.
- **Priority:** High.

### AED-RULE-007 — Human merge authorization phrase required

- **Category:** MA
- **Rule statement:** A merge may only be performed after a human
  authorization phrase is issued in the form documented in
  `docs/merge_authorization_guard.md` (the canonical phrase is
  `I authorize guarded squash merge of PR #N at exact head <40-char SHA>`).
- **Current source of truth:** `docs/merge_authorization_guard.md`.
- **Current enforcement location:** Operator prompt; registry constraint
  (`human_authorization_required: true` for merge-allowed states).
- **Current enforcement strength:** Soft (no automated check that the
  phrase was actually issued; only a registry declaration).
- **Current test coverage:** None for the phrase itself; schema-level
  coverage for `human_authorization_required`.
- **Failure mode if missed:** Agent merges without human authorization.
- **OpenHands migration target:** `AEDPolicy` requires `auth.phrase`
  field in tool call payload to issue any merge command.
- **Priority:** High.

### AED-RULE-008 — Thread resolution requires explicit human authorization

- **Category:** TR
- **Rule statement:** A thread may only be resolved after a human
  authorization phrase names the exact list of thread IDs to resolve.
  Outdated thread auto-resolution follows the
  `docs/stale_review_thread_auto_resolution_policy.md` policy and is
  subject to `check_stale_review_thread_resolution.py`.
- **Current source of truth:** `docs/stale_review_thread_auto_resolution_policy.md`;
  `check_stale_review_thread_resolution.py`.
- **Current enforcement location:** `check_stale_review_thread_resolution.py`
  script + operator prompt.
- **Current enforcement strength:** Medium.
- **Current test coverage:** `tests/test_check_stale_review_thread_resolution.py`.
- **Failure mode if missed:** Unintended thread resolution; a `CHANGES_REQUESTED`
  review dismissed.
- **OpenHands migration target:** `AEDGitHubTool` refuses the
  GitHub GraphQL thread-resolution mutation without a per-thread
  `auth.phrase` and stale-policy check.
- **Priority:** High.

### AED-RULE-009 — Codex response classifier drives lifecycle state

- **Category:** CR
- **Rule statement:** AED lifecycle transitions on the Codex-response path
  MUST be driven by `audit_codex_response_for_pr.py` and never by hand
  inference from raw comment data.
- **Current source of truth:** `docs/aed_lifecycle_state_registry.md`;
  PR #402.
- **Current enforcement location:** The classifier script and its
  `--output-json` packet contract.
- **Current enforcement strength:** Hard (CI runs the classifier via
  `audit-edge-discovery.yml`).
- **Current test coverage:** `tests/test_audit_codex_response_for_pr.py`
  (122 tests as of the latest merged head).
- **Failure mode if missed:** Hand-inferred status misses edge cases
  (nested pagination, stale commits, ping-window filtering, expected-head
  review filtering, formal clean-pass detection).
- **OpenHands migration target:** `AEDCodexClassifierTool` (a thin
  wrapper over the existing classifier with a stable packet contract).
- **Priority:** High.

### AED-RULE-010 — Exactly one Codex ping per head

- **Category:** PD
- **Rule statement:** For each PR head SHA, there must be exactly one
  Codex ping comment authored with `@codex review` in the body. A new
  head MUST have a new ping before any Codex response is interpreted as
  authoritative.
- **Current source of truth:** `audit_codex_response_for_pr.py` ping
  tracking + the operator prompt.
- **Current enforcement location:** Classifier packet tracks
  `ping_comment_id` and rejects new pings that duplicate the same head.
- **Current enforcement strength:** Medium.
- **Current test coverage:** Tests for ping-window filtering in
  `tests/test_audit_codex_response_for_pr.py`.
- **Failure mode if missed:** Codex may re-review an already-reviewed
  head; agent may post duplicate pings and confuse the classifier.
- **OpenHands migration target:** `AEDCodexPingTool` de-duplicates pings
  by `(pr_number, expected_head_sha)`.
- **Priority:** High.

### AED-RULE-011 — Clean pass must be tied to current head

- **Category:** CP
- **Rule statement:** A Codex clean pass (issue comment containing
  `Codex Review: Didn't find any major issues`, or an
  `APPROVED`/`COMMENTED` formal review with the clean-pass phrase) is only
  accepted as a current-head clean pass if it was authored after the
  ping and either has no `commit_id` or its `commit_id` matches
  `expected_head_sha`.
- **Current source of truth:** `audit_codex_response_for_pr.py`
  `clean_pass_*` logic.
- **Current enforcement location:** Classifier.
- **Current enforcement strength:** Hard.
- **Current test coverage:** `test_p2_formal_clean_pass_on_other_head_not_accepted`,
  `test_p2_formal_clean_pass_then_later_review_on_other_head_still_hold_new`,
  and many others.
- **Failure mode if missed:** Stale clean pass from a prior head drives
  a false MERGE_READY.
- **OpenHands migration target:** `AEDCodexClassifierTool` (existing
  classifier).
- **Priority:** High.

### AED-RULE-012 — Formal reviews on non-head commits must not downgrade a current-head clean pass

- **Category:** CP
- **Rule statement:** When the classifier scans formal Codex reviews
  after a clean pass to detect a newer non-clean review
  (`newer_finding_after_clean_pass`), it MUST ignore any review whose
  `commit_id` is set and differs from `expected_head_sha`. Reviews with
  no `commit_id` (legacy / GitHub-emitted without a commit anchor) are
  treated as authoritative, matching the `latest_review` convention.
- **Current source of truth:** `audit_codex_response_for_pr.py`
  `extract_review_commit_oid` filter inside the
  `newer_finding_after_clean_pass` loop.
- **Current enforcement location:** Classifier.
- **Current enforcement strength:** Hard.
- **Current test coverage:** `test_p2_stale_review_*` (3 tests) +
  `test_p2_formal_clean_pass_then_later_review_on_other_head_still_hold_new`.
- **Failure mode if missed:** A non-head formal `CHANGES_REQUESTED` /
  `COMMENTED` review downgrades a valid current-head clean pass to
  `HOLD_NEW_CODEX_THREAD`.
- **OpenHands migration target:** `AEDCodexClassifierTool` (existing
  classifier).
- **Priority:** High.

### AED-RULE-013 — Review-thread inventory must be complete

- **Category:** RI
- **Rule statement:** The classifier MUST verify that the GraphQL
  `reviewThreads.pageInfo.hasNextPage` is `false` before trusting an
  `active_blocker` count. An incomplete review-thread inventory MUST
  fail-closed to `HOLD_CODEX_RESPONSE_PENDING` (if no visible active
  finding) or `HOLD_NEW_CODEX_THREAD` (if a visible active finding was
  already harvested).
- **Current source of truth:** `audit_codex_response_for_pr.py`
  `review_thread_inventory_complete` flag.
- **Current enforcement location:** Classifier.
- **Current enforcement strength:** Hard.
- **Current test coverage:** `test_p2_partial_inventory_*` and
  `test_paginated_*` tests.
- **Failure mode if missed:** A false CLEAN with hidden active threads.
- **OpenHands migration target:** `AEDCodexClassifierTool` (existing
  classifier).
- **Priority:** High.

### AED-RULE-014 — Issue-comment inventory must be complete

- **Category:** II
- **Rule statement:** REST `gh api .../issues/{pr}/comments` and the
  `--slurp --paginate` shape MUST yield a parseable JSON array of every
  Codex-authored issue comment. If the fetch fails or pagination is
  incomplete, the classifier MUST fail-closed.
- **Current source of truth:** `audit_codex_response_for_pr.py`
  `issue_comment_inventory_complete` flag.
- **Current enforcement location:** Classifier.
- **Current enforcement strength:** Hard.
- **Current test coverage:** `test_p1_reviews_fetch_failure_fails_closed_no_merge_ready`
  and related `test_p1_*` tests.
- **Failure mode if missed:** A hidden new Codex issue comment is missed;
  a `CHANGES_REQUESTED` review is invisible to the gate.
- **OpenHands migration target:** `AEDCodexClassifierTool` (existing
  classifier).
- **Priority:** High.

### AED-RULE-015 — Review-submission inventory must be complete

- **Category:** RS
- **Rule statement:** REST `gh api .../pulls/{pr}/reviews` and
  `--slurp --paginate` MUST yield a parseable JSON array of every
  Codex-authored review submission. Incomplete inventory fails closed.
- **Current source of truth:** `audit_codex_response_for_pr.py`
  `review_submission_inventory_complete` flag.
- **Current enforcement location:** Classifier.
- **Current enforcement strength:** Hard.
- **Current test coverage:** Same as AED-RULE-014.
- **Failure mode if missed:** A hidden formal `CHANGES_REQUESTED` is
  missed.
- **OpenHands migration target:** `AEDCodexClassifierTool`.
- **Priority:** High.

### AED-RULE-016 — Visible findings from incomplete nested pagination must be preserved

- **Category:** PN
- **Rule statement:** When a review thread has more than 50 comments
  (`comments.pageInfo.hasNextPage=true`), the classifier MUST preserve
  the visible Codex-authored findings already returned on the first
  page, mark the thread `nested_incomplete=true`, and route to
  `HOLD_NEW_CODEX_THREAD` if any visible active finding exists.
- **Current source of truth:** `audit_codex_response_for_pr.py` nested
  pagination block.
- **Current enforcement location:** Classifier.
- **Current enforcement strength:** Hard.
- **Current test coverage:** `test_p2_partial_inventory_*` tests.
- **Failure mode if missed:** A visible P1 finding is dropped because
  pagination didn't complete, and the gate returns a false CLEAN.
- **OpenHands migration target:** `AEDCodexClassifierTool`.
- **Priority:** High.

### AED-RULE-017 — Timestamps must be timezone-aware and ISO-8601

- **Category:** TT
- **Rule statement:** All ping timestamps, clean-pass timestamps, and
  review timestamps MUST be parseable as ISO 8601 with explicit
  timezone (`Z` or `±HH:MM`). A naive `datetime.fromisoformat` that
  produces an unaware datetime MUST be rejected — comparing naive to
  aware `datetime`s raises `TypeError`.
- **Current source of truth:** `audit_codex_response_for_pr.py`
  `parse_iso_utc` and the ping-window filter.
- **Current enforcement location:** Classifier.
- **Current enforcement strength:** Hard.
- **Current test coverage:** `test_p2_partial_inventory_hold_pending_renders_pending_status`,
  `test_valid_*_ping_timestamp_*` tests.
- **Failure mode if missed:** `TypeError` crash; or a silently-wrong
  ping-window comparison.
- **OpenHands migration target:** `AEDPolicy` validates ISO 8601 + tz on
  every timestamp field.
- **Priority:** Medium.

### AED-RULE-018 — All five required CI checks must pass on the current head

- **Category:** CI
- **Rule statement:** A PR is merge-eligible only if all five required
  CI checks pass on the exact current head: `governance-validators`,
  `pr-gate-live-smoke`, `review-comment-gate`, `test (3.11)`,
  `validator`. A pending check is not a pass.
- **Current source of truth:** `docs/aed_whole_workflow_operator_path.md`
  §5; `.github/workflows/ci.yml`; `docs/audit-edge-discovery.yml`.
- **Current enforcement location:** CI workflow + `audit_main_ci_for_head.py`.
- **Current enforcement strength:** Hard.
- **Current test coverage:** `tests/test_audit_main_ci_for_head.py` (45
  tests) and `tests/test_check_pr_review_comments.py`.
- **Failure mode if missed:** Merge with a failing or pending CI check.
- **OpenHands migration target:** `AEDCI` reads `gh pr checks` and
  refuses to issue `gh pr merge` while any check is non-pass.
- **Priority:** High.

### AED-RULE-019 — Scope guard must report SCOPE_CLEAN

- **Category:** SG
- **Rule statement:** Every PR turn MUST run `scope_guard.py` against
  the turn's isolated diff (`HEAD~1..HEAD` for the temp worktree) with
  the appropriate `--allow-file` / `--allow-glob` flags. The result
  MUST be `SCOPE_CLEAN`; otherwise the turn stops with
  `HOLD_SCOPE_GUARD_FAILED`.
- **Current source of truth:** `scripts/local/scope_guard.py` and the
  known-safe command cookbook.
- **Current enforcement location:** `scope_guard.py` (CI / local).
- **Current enforcement strength:** Hard.
- **Current test coverage:** `tests/test_scope_guard.py` and friends.
- **Failure mode if missed:** A code-fix PR touches files outside its
  declared scope.
- **OpenHands migration target:** `AEDPolicy` invokes scope guard after
  every diff write; refuses to advance lifecycle on `HOLD_*` status.
- **Priority:** High.

### AED-RULE-020 — Audit log is append-only and external-audit is never mutated

- **Category:** AA
- **Rule statement:** `audit_reports/*.json` and any other
  externally-appended audit log are append-only and never edited,
  trimmed, normalized, or rewritten. If a repo-standard
  audit-append mechanism exists, an AED turn may append one closeout
  entry; otherwise the operator handles the closeout and the turn
  reports `AUDIT_APPEND_NEEDS_OPERATOR`.
- **Current source of truth:** `docs/merge_action_audit_log.md`;
  `docs/trace_policy_v1.md`; PR #398.
- **Current enforcement location:** `append_merge_action_audit.py`
  append-only; `validate_merge_action_audit_log.py`; operator prompt.
- **Current enforcement strength:** Hard (append-only CLI) + Soft
  (operator prompt forbids re-normalization).
- **Current test coverage:** `tests/test_append_merge_action_audit.py`,
  `tests/test_validate_merge_action_audit_log.py`.
- **Failure mode if missed:** Silent audit-log rewrite hides prior
  closeouts; phase ledger evidence becomes unrecoverable.
- **OpenHands migration target:** `AEDAuditTool` opens the audit file
  in append-only mode; refuses any write that would change byte length
  of an existing entry.
- **Priority:** High.

### AED-RULE-021 — Protected historical PRs are read-only

- **Category:** PH
- **Rule statement:** PRs #384, #386, #397, #398, #399, #400, #401,
  #402 (and any other merged PR whose closeout is in the audit chain)
  are protected historical PRs. They MUST NOT be reopened, re-merged,
  force-pushed, or have their branches deleted by any AED turn.
- **Current source of truth:** `docs/aed_known_safe_command_cookbook.md`
  §3; closeout entries in `audit_reports/`.
- **Current enforcement location:** Operator prompt.
- **Current enforcement strength:** Soft.
- **Current test coverage:** None.
- **Failure mode if missed:** A historical PR is force-pushed or branch
  deleted, breaking audit chain.
- **OpenHands migration target:** `AEDGitHubTool` refuses
  re-opening a protected PR (via `gh pr edit --state open`) or
  branch-deletion (via the GitHub API `DELETE` method on
  `/git/refs`) on protected PR numbers.
- **Priority:** Medium.

### AED-RULE-022 — Resume checkpoint continuation rule

- **Category:** RC
- **Rule statement:** On a `HOLD_RESUME_CHECKPOINT_NEEDED` state, the
  agent reconstructs state from read-only checks (PR number, PR URL,
  current head SHA, current lifecycle state, completed phases,
  remaining permitted mutations, already-performed mutations,
  protected PR/worktree state) and resumes without re-doing completed
  steps.
- **Current source of truth:** `docs/aed_lifecycle_state_registry.md` §11;
  PR #399.
- **Current enforcement location:** Lifecycle state schema +
  `aed_lifecycle_states.py` CLI.
- **Current enforcement strength:** Medium.
- **Current test coverage:** `tests/test_aed_lifecycle_states.py`
  resume-checkpoint rules.
- **Failure mode if missed:** Agent repeats a completed mutation
  (e.g., posts a duplicate Codex ping or re-resolves a thread).
- **OpenHands migration target:** `AEDLifecycleStateStore` persists
  per-task ledger and refuses mutations already marked done.
- **Priority:** High.

### AED-RULE-023 — Gate-safe comment language for Codex pings

- **Category:** GL
- **Rule statement:** Every Codex ping MUST be scanned for standalone
  P0–P3 severity markers and for known review-comment-gate trigger
  terms (e.g., `must fix`, `security`, `stale`, the Python
  `subprocess` `shell`-`True` anti-pattern, `live claude`,
  `hermes mutation`, `bypass`) before posting. If the
  scan fails, the body MUST be rewritten.
- **Current source of truth:** `docs/aed_known_safe_command_cookbook.md`
  §13; `check_pr_review_comments.py` `BLOCKING_WORDS`.
- **Current enforcement location:** Operator prompt + the
  review-comment-gate's own post-hoc check.
- **Current enforcement strength:** Soft (pre-post scan is operator
  responsibility; gate is the backstop).
- **Current test coverage:** None for the pre-post scan itself; gate
  tests cover the backstop.
- **Failure mode if missed:** The review-comment-gate runs and
  misclassifies the ping as a finding.
- **OpenHands migration target:** `AEDCodexPingTool` runs the pre-post
  scan automatically and refuses to post on a hit.
- **Priority:** Medium.

### AED-RULE-024 — Forbidden operations are hard-rejected

- **Category:** FO
- **Rule statement:** The following operations are forbidden at every
  layer of the AED stack and MUST be hard-rejected:

  - `gh pr merge` invoked with the admin-bypass flag
  - `gh pr merge` invoked with the auto-merge enablement flag
  - The GitHub GraphQL mutation that deletes a review comment
    (the comment-deletion mutation referenced in
    `docs/pr_review_comment_gate.md`)
  - The GitHub GraphQL mutation that deletes an issue comment
    (the issue-comment-deletion mutation)
  - Thread resolution via the GitHub GraphQL mutation without
    per-thread authorization
  - Review dismissal via the GitHub GraphQL mutation (the
    review-dismissal mutation referenced in
    `docs/stale_review_thread_auto_resolution_policy.md`)
  - Force-push to any protected branch (`main`, `tooling/*`, `docs/*`,
    `feat/*`, `fix/*` in the AED governance path)
  - Direct push to `main` (bypassing PR)
  - Watch commands / unbounded `while-true` polling loops
  - Audit log mutation (truncate, normalize, rewrite)
  - Memory / `fact_store` / skill create-or-update calls inside a
    governance run
  - Invented command flags (`--help`-unknown flags)
- **Current source of truth:** `scope_guard.py` `BUILTIN_FORBIDDEN_DIFF_REGEXES`;
  `merge_pr_safely.py` argparse; the cookbook.
- **Current enforcement location:** Hard-coded regex + argparse + operator
  prompt.
- **Current enforcement strength:** Hard.
- **Current test coverage:** `tests/test_check_pr_scope.py`,
  `tests/test_scope_guard.py`.
- **Failure mode if missed:** A forbidden operation slips through and
  mutates state in a way the governance stack did not anticipate.
- **OpenHands migration target:** `AEDPolicy` forbidden-operation list
  + `AEDGitHubTool` / `AEDGitTool` PreToolUse hook rejection.
- **Priority:** High.

### AED-RULE-025 — Bounded polling only

- **Category:** FO
- **Rule statement:** All CI and Codex-response polling MUST be bounded
  with explicit `max_polls` and `poll_seconds` parameters. Watch
  commands and unbounded `while true` loops are forbidden.
- **Current source of truth:** `docs/aed_known_safe_command_cookbook.md` §3.
- **Current enforcement location:** `audit_codex_response_for_pr.py`,
  `audit_main_ci_for_head.py` argparse; operator prompt.
- **Current enforcement strength:** Hard (script-enforced).
- **Current test coverage:** `tests/test_audit_codex_response_for_pr.py`
  bounded-polling tests.
- **Failure mode if missed:** A runaway loop blocks the operator and
  burns rate-limit budget.
- **OpenHands migration target:** `AEDPolicy` requires `bounded_poll: true`
  on every polling tool call.
- **Priority:** Medium.

### AED-RULE-026 — Final report distinguishes post-ping findings from older anchored findings

- **Category:** FR
- **Rule statement:** Any final report on a `HOLD_NEW_CODEX_THREAD`
  state MUST separate post-ping active findings (genuinely new) from
  older anchored active findings (pre-existing). The agent MUST NOT
  propose a fix-and-resubmit unless there is a genuinely new or
  reissued post-ping finding.
- **Current source of truth:** Operator prompt (per-turn workflow
  rules).
- **Current enforcement location:** Operator prompt.
- **Current enforcement strength:** Soft.
- **Current test coverage:** None.
- **Failure mode if missed:** Agent fixes older anchored findings that
  Codex has not reissued, scope-creeping the diff.
- **OpenHands migration target:** `AEDReportTool` template requires a
  `post_ping_findings` array separate from `older_anchored_findings`.
- **Priority:** Medium.

### AED-RULE-027 — Classifier returns a precise lifecycle state

- **Category:** FR
- **Rule statement:** The classifier MUST return exactly one of the
  canonical lifecycle states defined in
  `docs/aed_lifecycle_state_registry.md`. The agent MUST NOT invent
  ad-hoc state names; the packet's `status` field is the only
  authoritative state string.
- **Current source of truth:** `schemas/aed_lifecycle_states_v1.json`
  (PR #397).
- **Current enforcement location:** Lifecycle state registry CLI;
  classifier packet contract.
- **Current enforcement strength:** Hard.
- **Current test coverage:** `tests/test_aed_lifecycle_states.py`
  (73 tests).
- **Failure mode if missed:** Agent invents `MERGE_GO` or `WAIT_BUT`
  and the registry cannot validate the state transition.
- **OpenHands migration target:** `AEDLifecycleStateStore` validates
  every transition against the registry.
- **Priority:** High.

### AED-RULE-028 — Formal clean-pass detection scans all post-ping formal reviews

- **Category:** CP
- **Rule statement:** The formal-review clean-pass branch in
  `audit_codex_response_for_pr.py` MUST scan all post-ping Codex
  formal review submissions (not just `latest_review`), apply the
  expected-head commit-scope filter, and select the most recent
  qualifying review as the clean-pass reference. State must be
  `APPROVED` or `COMMENTED`; body must contain the clean-pass phrase.
- **Current source of truth:** `audit_codex_response_for_pr.py` formal
  clean-pass branch; PR #402.
- **Current enforcement location:** Classifier.
- **Current enforcement strength:** Hard.
- **Current test coverage:** 8 tests including
  `test_p2_formal_clean_pass_then_later_commented_non_clean_routes_hold_new`.
- **Failure mode if missed:** A clean pass is missed when Codex clean-passes
  via a formal review and then submits a later non-clean review on the
  same head.
- **OpenHands migration target:** `AEDCodexClassifierTool` (existing
  classifier).
- **Priority:** High.

### AED-RULE-029 — Per-poll raw/derived reset on each polling pass

- **Category:** CR
- **Rule statement:** On every polling pass, the classifier MUST reset
  the raw `pr_issue_comments` / `pr_reviews` / `review_threads` lists
  before refetching, and reset the derived `active_threads` /
  `outdated_threads` / `resolved_threads` buckets before appending.
  A failed fetch on a later poll must NOT inherit raw data from a
  prior poll.
- **Current source of truth:** `audit_codex_response_for_pr.py` per-poll
  reset block; PR #402.
- **Current enforcement location:** Classifier.
- **Current enforcement strength:** Hard.
- **Current test coverage:** `test_raw_poll_snapshot_reset_reviews_fetch_failure`,
  `test_stale_stop_state_cleared_*`.
- **Failure mode if missed:** Stale data from a prior poll silently
  drives a wrong status.
- **OpenHands migration target:** `AEDCodexClassifierTool`.
- **Priority:** Medium.

### AED-RULE-030 — Operator-prompt rules that are not yet hard-enforced

- **Category:** OM (migration debt)
- **Rule statement:** Several rules above (AED-RULE-001, -002, -003,
  -005, -007, -008, -021, -023, -026) are enforced only via operator
  prompt. The OpenHands migration MUST harden each of these by
  promoting the operator prompt to a PreToolUse hook or by adding a
  dedicated validator. Prompts are not final enforcement.
- **Current source of truth:** This document and the cookbook.
- **Current enforcement location:** Operator prompt only.
- **Current enforcement strength:** Soft.
- **Current test coverage:** None.
- **Failure mode if missed:** A prompt is misread and the rule is
  violated. A future "AI prompt-injection" or model regression would
  cause silent governance failure.
- **OpenHands migration target:** All soft rules in the OpenHands
  migration.
- **Priority:** Critical (defines the migration scope).

## 5. Known non-rules / out-of-scope items

The following are intentionally NOT rules and are out of scope for the
AED governance inventory:

- **No admin-bypass flag on `gh pr close`** — `gh pr close` does
  not have an admin flag; this is a non-rule.
- **No mutating `audit_reports/*.json` is forbidden unless an
  append-only mechanism exists** — see AED-RULE-020. Outside the audit
  scope, `audit_reports` files are read-only test fixtures for the
  backtest pipeline.
- **No "no commits per minute" rate limit on agent runs** — the AED
  harness does not impose a per-minute commit rate.
- **No requirement to use a specific shell, terminal multiplexer, or
  terminal type** — the cookbook recommends but does not mandate.

## 6. Drift risks

The following are the highest-likelihood drift sources as the
OpenHands migration proceeds:

1. **New rules added during OpenHands development without updating this
   index.** Mitigation: every PR that adds a new `AED-RULE-NNN` MUST
   also update this doc.
2. **Soft rules hardening into hard rules without changing the rule
   ID.** Mitigation: when a rule's enforcement is promoted, add a
   `Migration change log` entry.
3. **Prompt-only rules drifting away from the registry.** Mitigation:
   the lifecycle state registry
   (`schemas/aed_lifecycle_states_v1.json`) is the canonical machine
   source; this doc is the human-readable mirror.
4. **Rules landing in two different docs that disagree.** Mitigation:
   the lower-level doc governs; this doc defers.
5. **Test-only rule artifacts becoming orphan.** Mitigation: any rule
   whose only test is deleted should be flagged for review.

## 7. Open questions

1. Should soft rules (operator-prompt only) be promoted to hard rules
   one-by-one during the OpenHands migration, or all at once in a
   single transition PR?
2. Should `AED-RULE-NNN` IDs be stable across renames (i.e., a renamed
   rule keeps its old ID), or should renames mint a new ID?
3. Should the rule taxonomy grow a `Security` category distinct from
   `FO` (forbidden operations)?
4. Should rule-violation events be emitted to the audit log as a
   first-class event type (`rule_violation` alongside `pr_merge`)?

## 8. Migration change log

| Date | PR | Author | Change |
|------|----|--------|--------|
| 2026-06-13 | (PR #403) | initial | Created `aed_rules_inventory.md` with 30 rules. |
