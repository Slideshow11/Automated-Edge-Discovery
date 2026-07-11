# `aed continue-pr --dry-run` — Continuation Workflow Planner

## Purpose

`aed continue-pr --dry-run` is a **read-only** CLI command that, given a PR number, computes and emits a structured continuation plan. It tells the operator what state the PR is in, what mutations are still permitted, and what `continue-pr` *would* do next — **without mutating GitHub, the worktree, or any repo state**.

It is the operator-facing counterpart to the planned `aed continue-pr --execute` command (a future PR #407+). The dry-run command never executes any mutation; it only proposes a structured plan for human review.

## Safety guarantees

This script is **read-only by design**. It guarantees:

- ✅ **No `--execute`, `--no-dry-run`, `--force`, or `--admin` flag is accepted.** All four are hard-rejected at argument-parse time with exit code 2.
- ✅ **`--dry-run` is mandatory.** The script refuses to run without it.
- ✅ **All GitHub API calls are GET-only.** The script invokes `gh api /repos/{owner}/{repo}{endpoint}` against read-only endpoints; it never POSTs, PUTs, PATCHes, or DELETEs.
- ✅ **No subprocess call mutates the repo.** The script does not invoke `git push`, `git commit`, `gh pr merge`, `gh pr close`, `gh pr comment`, `gh pr review`, `resolve_stale_threads_for_pr.py`, `merge_pr_safely.py`, or any other tool that mutates GitHub.
- ✅ **The B2 review-comment gate is invoked via subprocess**, never imported or modified.
- ✅ **Output is written only to the user-specified `--output-json` and `--output-md` paths.** No implicit writes to the repo or worktree.
- ✅ **Proposed merge commands include `--match-head-commit <HEAD>`** for exact-head protection (the lesson learned in PR #405).

## Usage

```bash
python3 scripts/local/aed_continue_pr.py \
    --pr-number 407 \
    --dry-run \
    --output-json /tmp/aed_runs/plan_407.json \
    --output-md /tmp/aed_runs/plan_407.md
```

### Optional flags

| Flag | Default | Description |
|------|---------|-------------|
| `--repo` | `Slideshow11/Automated-Edge-Discovery` | GitHub repository in `owner/name` form |
| `--repo-root` | (none) | Absolute path to AED repo root (for gate subprocess) |
| `--check-codex` | enabled | Query both Codex endpoints (disable with `--no-check-codex`) |
| `--max-poll-seconds` | 30 (cap 300) | Timeout for the gate subprocess |
| `--last-known-codex-ts` | (none) | ISO timestamp for filtering stale Codex signals |

### Forbidden flags (hard-rejected with exit code 2)

- `--execute`
- `--force`
- `--admin`
- `--no-dry-run`

### CWD independence

The script computes the repo root from its own `__file__` and inserts it into `sys.path` at module load time, so `aed_lifecycle` is importable regardless of the current working directory. The documented form (`python3 scripts/local/aed_continue_pr.py ...`) works from `/tmp`, a different worktree, or any parent directory — no `cd` into the repo root is required.

## Required `--dry-run`

The `--dry-run` flag is **mandatory**. The script refuses to run without it. A future PR (#407+) will introduce a separate `aed continue-pr --execute` command that consumes the JSON plan emitted here.

## Refusal behavior

| Situation | Exit code | Behavior |
|-----------|-----------|----------|
| Missing `--dry-run` | 2 | argparse error |
| Missing `--pr-number` | 2 | argparse error |
| Missing `--output-json` | 2 | argparse error |
| Missing `--output-md` | 2 | argparse error |
| `--execute` / `--force` / `--admin` / `--no-dry-run` | 2 | hard-rejected with explicit error message |
| GitHub API error | 3 | RuntimeError caught and printed |
| Gate subprocess error | 4 | subprocess failure recorded in plan; exit code 0 (plan is still emitted for review) |
| Output write error | 5 | OSError caught and printed |
| Success (any plan status) | 0 | plan emitted |

## JSON output fields

The `--output-json` path receives a JSON object with this schema:

```json
{
  "schema_version": 1,
  "plan_kind": "aed.continue_pr.dry_run",
  "generated_at": "2026-06-21T15:00:00Z",
  "dry_run": true,
  "pr": {
    "number": 407,
    "url": "...",
    "title": "...",
    "head_sha": "abc123...",
    "head_ref": "tooling/aed-continue-pr-dry-run-v1",
    "base_ref": "main",
    "base_sha": "def456...",
    "state": "OPEN",
    "is_draft": false,
    "is_mergeable": true,
    "merge_state_status": "clean",
    "author_login": "Slideshow11",
    "created_at": "...",
    "updated_at": "..."
  },
  "lifecycle": {
    "current_state": "READY_FOR_FINAL_PREFLIGHT",
    "source": "inferred_from_pr_open_and_checks_green",
    "completed_phases": ["PHASE_1_PROTECTED_STATE_VERIFICATION", "PHASE_2_CI_PROTECTION_GATE"],
    "remaining_permitted_mutations": ["codex_re_request_if_idle", "thread_resolve_if_safe", "pr_merge"],
    "already_performed_mutations": [],
    "blocked_mutations": ["worktree_update"]
  },
  "checks": {
    "all_required_green": true,
    "per_check_status": {"review-comment-gate": "success", ...}
  },
  "gate": {
    "status": "REVIEW_COMMENTS_CLEAN",
    "head_sha_mismatch": false,
    "blockers": 0,
    "stale_blockers": 0,
    "p0_count": 0,
    "p1_count": 0,
    "p2_count": 0,
    "p3_count": 0,
    "current_unresolved_threads": 0
  },
  "codex": {
    "verdict": "clean",
    "source": "issue_comment_clean_signal",
    "last_receipt_at": "2026-06-21T15:42:47Z",
    "last_review_id": null,
    "last_comment_id": 4762471478,
    "would_ping_codex": false,
    "duplicate_ping_detected": false,
    "dual_endpoint_check": {
      "formal_review_endpoint": {"checked": true, "found_fresh_review": false},
      "issue_comment_endpoint": {"checked": true, "found_clean_comment": true}
    }
  },
  "branch_protection": {
    "base_branch": "main",
    "is_protected": true,
    "required_status_checks": ["review-comment-gate", ...],
    "strict_status_checks": true,
    "required_conversation_resolution": true,
    "required_linear_history": true,
    "required_approving_review_count": 0,
    "enforce_admins": false,
    "allow_force_pushes": false,
    "violations": []
  },
  "proposed_actions": [
    {
      "order": 1,
      "action_kind": "merge",
      "rationale": "PR is mergeable with mergeStateStatus=CLEAN; gate is REVIEW_COMMENTS_CLEAN with 0 blockers; Codex verdict is clean from issue_comment_clean_signal; all required checks green.",
      "command_preview": "gh pr merge 407 --repo Slideshow11/Automated-Edge-Discovery --squash --delete-branch --match-head-commit abc123...",
      "mutates_github": true,
      "requires_human_authorization": true
    }
  ],
  "blockers_for_merge": [],
  "mutations_proposed": 1,
  "warnings": [],
  "recommendation": "READY_TO_AUTHORIZE_HUMAN_MERGE"
}
```

### Recommendation values

| Value | Meaning |
|-------|---------|
| `READY_TO_AUTHORIZE_HUMAN_MERGE` | All preconditions met. Operator can execute the proposed merge command from their terminal. |
| `WAITING_FOR_CODEX_VERDICT` | PR is clean but Codex has not responded yet. Wait for Codex before merging. |
| `NOT_READY_PR_IS_DRAFT` | PR is a draft; convert to ready-for-review before merging. |
| `NOT_READY_PR_STATE_<state>` | PR is not OPEN (e.g., closed, merged). |
| `NOT_READY_BLOCKERS_PRESENT` | One or more blockers are listed in `blockers_for_merge`. |
| `NOT_READY_UNKNOWN` | Unknown / unexpected state. |

## Markdown memo output

The `--output-md` path receives a human-readable memo with these sections:

- PR status (number, title, URL, state, draft, head SHA, base SHA, mergeable, merge_state_status)
- Lifecycle state (current state, completed phases, remaining mutations)
- Checks (per-check status)
- Review-comment gate (B2 source-aware status, blockers, P0/P1/P2/P3 breakdown)
- Codex verdict (dual-endpoint — see below)
- Branch protection (required checks, conversation resolution, linear history)
- Proposed actions (preview only — never executed)
- Blockers for merge (if any)
- Warnings (if any)
- Operator action

The proposed merge command is included as a **preview** in the memo (inside a code block). It is never executed by this script.

## Safety model

| Gate | Enforcement |
|------|-------------|
| No GitHub mutation | All API calls are GET; subprocess calls are read-only by design |
| No `--force` or `--admin` | Hard-rejected at arg parse; prints error and exits 2 |
| Mandatory `--dry-run` | Refuses to run without it (argparse error, exit 2) |
| Bounded API calls | `--max-poll-seconds` default 30s; cap at 300s |
| No output to non-specified paths | `--output-json` and `--output-md` are mandatory |
| Reuses B2 gate without modification | Invokes `check_pr_review_comments.py` via subprocess (never imports or modifies) |
| Reuses lifecycle helpers without modification | Reads `aed_lifecycle` constants only; never invokes mutating helpers |

## Dual-endpoint Codex detection lesson (from PR #405)

**Lesson:** Codex's clean signal often arrives as a PR-level **issue comment**, not a formal **review**. Single-endpoint polling missed the PR #405 clean signal at comment id `4762471478`.

This script implements **dual-endpoint detection**:

1. **`/pulls/{N}/reviews`** — formal reviews submitted by Codex (via the formal reviews API)
2. **`/issues/{N}/comments`** — PR-level issue comments by Codex (via the issue comments API)

Both endpoints are queried. The verdict is resolved by priority:

1. If any formal review has `state = CHANGES_REQUESTED` → `blocked` (source: `review_CHANGES_REQUESTED`)
2. If any issue comment matches the BLOCKED_PATTERN → `blocked` (source: `issue_comment_BLOCKED_pattern`)
3. If formal review has `state = COMMENTED` with clean body → `clean` (source: `review_clean_signal`)
4. **If only issue comment matches the clean pattern → `clean` (source: `issue_comment_clean_signal`)** ← the PR #405 lesson
5. If both endpoints have activity but signals conflict → `conflicting`
6. If neither endpoint has Codex activity → `pending`

The clean-signal pattern is documented as:
- `Codex Review: <verdict>. <body>` where verdict contains `[Nn]o major issues|✅|Swish|👍|[Dd]id(?:n't| not)? find any major`
- Or the body contains `[Nn]o major issues|✅|Swish|👍|looks good|[Dd]id(?:n't| not)? find any major`

The blocked-signal pattern is documented as:
- `[Cc]hanges [Rr]equested` or `CHANGES_REQUESTED`
- Or body contains `\bblocking issue\b|\bblocking merge\b|\bcritical issue\b|\bmerge blocked\b|\baction required\b`

If a future Codex format emerges that doesn't match these patterns, the CLI falls back to `verdict: pending` and emits a warning rather than silently assuming a verdict.

## Exact-head preview semantics

Every proposed merge action includes `--match-head-commit <HEAD_SHA>` for exact-head protection. This is the lesson learned in PR #405: a merge command without exact-head protection can silently merge a stale head if the PR is force-pushed between plan generation and merge execution.

If the PR head has changed between plan generation and merge execution, GitHub's merge API will refuse the merge with a clear error, and the operator should re-run this dry-run to refresh the plan.

## Optional checkpoint ingestion (PR #407)

The dry-run command can optionally consume an AED checkpoint snapshot to cross-reference runner-recorded evidence against the live PR state. This is gated behind the optional `--checkpoint-json <path>` flag.

### What it does

When `--checkpoint-json` is provided, the script:

1. **Loads** the checkpoint JSON file read-only (the file is never written back).
2. **Validates** it through the canonical `aed_lifecycle.checkpoint.validate_checkpoint` and `validate_resume_observations` helpers.
3. **Cross-references** the checkpoint's recorded `pr_number`, `current_head`, `branch`, `last_verified_primary_head`, `phase`, `next_action`, and `terminal_state` against the live GitHub PR state.
4. **Surfaces** any disagreement as a fail-closed `blockers_for_merge` entry. The merge command preview is **never** emitted when a checkpoint disagreement is present.

When `--checkpoint-json` is **omitted**, the plan is byte-equivalent to PR #406 for any fixed live input (the dry-run command's behavior is otherwise unchanged).

### Fail-closed blockers

The following disagreement types each produce a distinct `kind` in `plan.blockers_for_merge`:

| Blocker kind | Trigger |
|---|---|
| `CHECKPOINT_LOAD_FAILED` | file missing / malformed JSON / unreadable |
| `CHECKPOINT_VALIDATION_INVALID` | `validate_checkpoint` returned errors; or required fields missing |
| `CHECKPOINT_PR_NUMBER_MISMATCH` | checkpoint `pr_number` does not match the requested PR |
| `CHECKPOINT_HEAD_MISMATCH` | checkpoint `current_head` does not match live PR `head_sha` |
| `CHECKPOINT_OBSERVATION_DRIFT` | `validate_resume_observations` reports head drift (recorded vs observed) |
| `CHECKPOINT_LIVE_GATE_DISAGREEMENT` | checkpoint `terminal_state=MERGE_READY_AWAITING_HUMAN_AUTHORIZATION` but live `merge_state_status` is not `clean` |
| `CHECKPOINT_NEXT_ACTION_UNSAFE` | checkpoint `next_action=pr_merge` but live `merge_state_status` is not `clean` |

A base/main SHA drift (`last_verified_primary_head` differs from live primary HEAD) is recorded as a **warning**, not a blocker, because base updates mid-PR are a normal workflow event.

### Combination: `merge_ready_both_sides`

The plan JSON's `checkpoint.combination.merge_ready_both_sides` field is the single boolean a future `--execute` command should consume:

- `true` only when the checkpoint is loaded, structurally valid, cross-references cleanly with the live PR state, **and** the checkpoint `terminal_state` is `MERGE_READY_AWAITING_HUMAN_AUTHORIZATION`.
- `false` otherwise.

The completed terminal state `PR_MERGED_AND_CLOSED_OUT` is **excluded** from the merge-ready set. A closed-out checkpoint paired with a live OPEN PR surfaces a `CHECKPOINT_CLOSED_OUT_LIVE_OPEN` blocker; it cannot satisfy `merge_ready_both_sides` under any circumstance. The canonical schema marks `PR_MERGED_AND_CLOSED_OUT` as `merge_allowed=false`, so treating it as merge-ready would be inconsistent.

This is the canonical "both the runner's recorded evidence and the live GitHub state agree the PR is ready to authorize" signal.

### JSON envelope schema

```jsonc
{
  "checkpoint": {
    "present": true,
    "path": "/tmp/aed_runs/pr407_checkpoint.json",
    "load_status": "loaded",
    "schema_version": 1,
    "errors": [],
    "warnings": [],
    "validation": {
      "status": "clean",
      "errors": [],
      "warnings": [],
      "state_summary": {
        "repo": "Slideshow11/Automated-Edge-Discovery",
        "pr_number": 407,
        "branch": "tooling/...",
        "current_head": "abcdef12...",
        "phase": "PHASE_2_CI_PROTECTION_GATE",
        "terminal_state": "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
        ...
      }
    },
    "cross_reference": {
      "status": "clean",  // or "disagreement"
      "blockers": [],
      "warnings": []
    },
    "combination": {
      "live_state_agrees": true,
      "merge_ready_both_sides": true,
      "blockers": [],
      "warnings": []
    }
  }
}
```

When `--checkpoint-json` is omitted, the envelope reduces to `{"present": false, "path": null, "load_status": "not_provided", ...}`.

### Markdown output

When a checkpoint is provided, the memo gains a `## Checkpoint` section between `## Lifecycle state` and `## Checks`. It records path, load status, validation status, cross-reference status, `live_state_agrees`, `merge_ready_both_sides`, blockers, warnings, and the recorded `current_head` / `phase` / `terminal_state` / `next_action` / `updated_at`.

When omitted, the section renders a single "Present: no" line so the markdown shape stays consistent.

### Future `--execute` note

A future PR will introduce a separate `aed continue-pr --execute` command. That executor must consume both the live evidence (PR state, merge_state_status, gate status, Codex verdict, branch protection) **and** `checkpoint.combination.merge_ready_both_sides` to authorize a merge. Live execution remains out of scope here.

## Out-of-scope (deferred to PR #408+)

- **`aed continue-pr --execute`**: the actual executor that consumes the JSON plan emitted here. PR #407+ will introduce a separate command with explicit per-mutation authorization prompts.
- **Checkpoint file reading**: `next_action_from_checkpoint(state)` is intentionally NOT consumed by this CLI. That is the runner's responsibility (a separate future component).
- **Auto-pinging Codex**: the CLI may *propose* a Codex re-request as an action, but it does NOT execute the ping. This requires explicit human authorization via the future `--execute` command.
- **Auto-resolving threads**: the CLI may *propose* thread resolution as an action, but it does NOT execute the resolution.
- **Auto-merging**: the CLI may *propose* a merge command, but it does NOT execute the merge. The operator must run the proposed command from their own terminal.
- **Telegram/Humphry/OpenHands integration**: explicit roadmap stop rule. Future harnesses may consume this CLI's output as input to their own state machines.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Plan emitted (any `status` value) |
| 1 | Mandatory arguments missing |
| 2 | Forbidden flag present (`--execute` / `--force` / `--admin` / `--no-dry-run`) |
| 3 | GitHub API error |
| 4 | Gate subprocess error |
| 5 | Output write error |

## Tests

Stdlib-only tests at `tests/test_aed_continue_pr.py` (~58 tests) cover:

- Argparse (mandatory `--dry-run`, forbidden flags rejected)
- JSON schema (required fields, stable shape, proposed actions preview-only)
- Markdown rendering (all major sections present)
- CLI dry-run scenarios (clean PR, blocked PR, draft PR, mergeable=False PR, pending CI, etc.)
- No-mutation audit (only GET-style API calls; no `gh pr merge` / `gh pr comment` / `git commit` / `git push` / worktree mutation)
- Idempotency (two identical runs produce identical output)
- Dual-endpoint Codex waiter (PR #405 lesson)
- Stale-head detection
- Blocker detection (gate BLOCKED, gate INCONCLUSIVE, CODEX_BLOCKED, UNRESOLVED_THREADS, MERGE_CONFLICT, REQUIRED_CHECKS_NOT_GREEN)
- Lifecycle inference (open PR + green checks → READY_FOR_FINAL_PREFLIGHT; open PR + failing checks → HOLD_PR_CI_PENDING)

Run with:

```bash
pytest tests/test_aed_continue_pr.py
```

## Operator action

This script is a **planner**, not an executor. The operator must:

1. Run the dry-run command.
2. Review the JSON plan and markdown memo.
3. Verify the state matches reality (e.g., manually check the PR on GitHub).
4. If the recommendation is `READY_TO_AUTHORIZE_HUMAN_MERGE`, copy the proposed merge command from the memo and execute it from a separate terminal.

- **The CLI never performs the merge. The operator is always the human-in-the-loop.**

## V7 changes (PR #407 round 7)

V7 addresses three Codex findings on the V6 head (`9142af69`):

### 1. Reject outputs that overwrite checkpoints (Codex 3471551408, P2)

`--checkpoint-json` is documented as a read-only file. A invocation that points
`--checkpoint-json` at the same resolved path as `--output-json` or
`--output-md` would silently overwrite the checkpoint after it is loaded. The
fix rejects these collisions in `parse_args` BEFORE any file write, PR fetch,
or checkpoint load, so the rejected invocation never touches the checkpoint or
any output path. The check uses `Path.resolve()` so relative paths, `./`
prefixes, and symlinked equivalents are normalized to the same canonical path.

| Invocation | Result |
|---|---|
| `--checkpoint-json <p>` + `--output-json <p>` (same resolved path) | `exit 2`, error: "must not be the same path as --output-json (the checkpoint file is read-only and would be overwritten by the generated plan)" |
| `--checkpoint-json <p>` + `--output-md <p>` (same resolved path) | `exit 2`, error: "must not be the same path as --output-md ..." |
| `--checkpoint-json <p>` + distinct `--output-json` / `--output-md` | pass |
| `--checkpoint-json` omitted | not affected; rejection block is gated on `args.checkpoint_json is not None` |

### 2. Block merges unless the provided checkpoint is merge-ready (Codex 3471569930, P1)

A structurally valid checkpoint that cross-references cleanly but that records
a non-merge-ready `terminal_state` (e.g. `HOLD_OPERATOR_REQUIRED`, `FAILED`,
`PR_MERGED_AND_CLOSED_OUT`) must NOT be silently ignored when the live gates
are otherwise clean. The fix adds an explicit `CHECKPOINT_NOT_MERGE_READY`
blocker when:

- The checkpoint was loaded + structurally validated + cross-referenced, AND
- `merge_ready_both_sides=False` because `terminal_state` is not the
  authorization state, AND
- The checkpoint has a recorded `terminal_state` (not absent).

The blocker prevents both the merge recommendation (`NOT_READY_BLOCKERS_PRESENT`
via the `elif blockers_for_merge` branch) and the merge preview in
`proposed_actions` (gated by `plan.recommendation == "READY_TO_AUTHORIZE_HUMAN_MERGE"`).

The merge-ready terminal state `MERGE_READY_AWAITING_HUMAN_AUTHORIZATION` with
clean live evidence continues to recommend a merge. The existing
cross-reference disagreement blockers (`CHECKPOINT_MERGED_LIVE_OPEN`,
`CHECKPOINT_CLOSED_OUT_LIVE_OPEN`, `CHECKPOINT_REPO_MISMATCH`, etc.) continue
to fire on top of this gate because they are pushed into `blockers_for_merge`
first, independently of the merge-ready check.

### 3. Validate `pr_number` before coercing strings to ints (Codex 3471569932, P2)

The previous coercion (`_coerce_optional_int`) silently turned a numeric
string like `"407"` into the integer `407` before the canonical
`validate_checkpoint` saw it. `CheckpointState.pr_number` is declared as `int`
and the canonical validator rejects a string `pr_number` — but after the silent
string-to-int coercion the validator saw `407` (int) and the checkpoint
validated clean, hiding the structural defect from the operator.

The fix mirrors the same raw-validation-before-coercion pattern used for
`phase`, `next_action`, and `terminal_state`:

| `pr_number` value | Result |
|---|---|
| `407` (int) | accepted |
| `"407"` (numeric string) | fail closed: `validation.raw_pr_number_errors` + `CHECKPOINT_VALIDATION_INVALID` blocker |
| `407.0` (float) | fail closed |
| `True` / `False` (bool) | fail closed |
| `[]` / `{}` / other non-int | fail closed |
| `None` | no error here; existing optional policy applies |
| missing | no error here; existing optional policy applies (`CHECKPOINT_PR_NUMBER_MISMATCH` later if unable to match live) |

The structural error is recorded in `envelope["validation"]["raw_pr_number_errors"]`
and rendered in the markdown `## Checkpoint` section.

## V9 changes (PR #407 round 9)

V9 addresses two Codex findings raised on exact head
`6aa29b131f27508c10d5509baf47471556ff597e` (the post-branch-update
head). Both fixes are additive on top of the V1–V8 hardening; no
prior validation behavior is loosened.

### 1. Require live base evidence before checkpoint merge-ready (Codex 3562681189, P1)

When `--checkpoint-json` was supplied but `pr['base_sha']` was
missing, `main()` forwarded `live_main_sha=None` to
`_cross_reference_checkpoint`, which in turn called
`validate_resume_observations(state, observed_pr_head=..., observed_primary_head="")`.
The canonical helper deliberately treats an empty observed primary
head as "skip this check", so a checkpoint parked at
`MERGE_READY_AWAITING_HUMAN_AUTHORIZATION` could still set
`merge_ready_both_sides=true` even though it was never
cross-checked against a real primary/base SHA.

The V9 fix:

- Forces `merge_ready_both_sides` to `false` whenever the supplied
  `live_main_sha` is `None` or empty and the checkpoint is at a
  merge-ready terminal state.
- Emits a distinct fail-closed blocker,
  `CHECKPOINT_LIVE_BASE_EVIDENCE_MISSING`, so the operator can
  distinguish this case ("live primary evidence is missing") from
  the older `CHECKPOINT_MERGE_READY_MISSING_PRIMARY_EVIDENCE`
  ("the checkpoint recorded `terminal_state` but no
  `last_verified_primary_head`").
- Non-merge-ready terminal states (e.g.
  `HOLD_OPERATOR_REQUIRED`) combined with missing `live_main_sha`
  do NOT trigger this new kind (no false positive); the existing
  `CHECKPOINT_NOT_MERGE_READY` (V7) blocker handles those cases.

| Checkpoint terminal state | `live_main_sha` | `merge_ready_both_sides` | New blocker |
|---|---|---|---|
| merge-ready | `None` / `""` / `"   "` | **false** | `CHECKPOINT_LIVE_BASE_EVIDENCE_MISSING` |
| merge-ready | valid non-empty SHA | true (existing pass-through) | (none) |
| non-merge-ready | `None` / empty | false (existing rules) | (none — V7 `CHECKPOINT_NOT_MERGE_READY` may fire) |

### 2. Reject blank checkpoint `phase` before coercion (Codex 3562681190, P2)

V4's `_validate_raw_phase` rejected non-string non-`None` values
(`[]`, `{}`, `42`, etc.) but silently accepted a *present-but-blank*
string (`""`, `"   "`, `"\t"`, `"\n\n"`). The next step
(`_coerce_optional_str`) would normalize the blank string to
`None`, hiding the malformed value. With otherwise merge-ready
evidence, the present-but-blank `phase` would cross-reference as
clean instead of producing a fail-closed
`CHECKPOINT_VALIDATION_INVALID` blocker.

The V9 fix extends `_validate_raw_phase` to fail-closed on blank
strings — mirroring the V3 blank-`next_action` and V8
blank-`terminal_state` validators — so `phase` is now consistent
with the rest of the V3-V8 raw-validation policy.

| `phase` value | Result |
|---|---|
| `""` | fail closed: `validation.raw_phase_errors` + `CHECKPOINT_VALIDATION_INVALID` |
| `"   "` (whitespace only) | fail closed |
| `"\n\t"` (mixed whitespace) | fail closed |
| Valid non-blank string | accepted (canonical validator decides whether known) |
| `None` | no error (existing optional policy) |
| Absent | no error (existing optional policy) |
| Non-string non-`None` (e.g. `[]`, `42`) | fail closed (V4 behavior, preserved) |

The structural error is recorded in
`envelope["validation"]["raw_phase_errors"]` and rendered in the
markdown `## Checkpoint` section.
