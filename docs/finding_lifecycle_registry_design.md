# Finding Lifecycle Registry Design

> **Status**: Design draft. Implementation not yet started.
> **Constraint**: This document describes a design only. No production code, tests,
> workflows, GitHub settings, or branch protection changes are made under this design.

---

## 1. Purpose

The Finding Lifecycle Registry ("the registry") is a durable, append-only record of every
Codex finding, review thread, and resolution event observed by AED across all PRs and runs.

The registry exists so AED can distinguish:

| Term | Meaning |
|------|---------|
| **New blocker** | A finding on the current PR head that must block merge |
| **Stale finding** | A finding on an old commit; the current diff no longer contains the issue |
| **Fixed finding** | A finding whose root cause was eliminated by a code/docs/test patch |
| **Resolved-by-policy** | A stale thread resolved after the stale-thread checker proved eligibility |
| **Waived finding** | A finding intentionally accepted with documented rationale |
| **Escalated finding** | A finding requiring human/operator decision |
| **Superseded finding** | A finding replaced by a newer, more accurate finding |
| **Invalid finding** | A finding factually disproven, with evidence |

Without a registry, AED repeatedly re-discovers the same stale findings and relies too
heavily on prompt memory and ephemeral tool output. The registry provides a shared factual
record that survives individual tool invocations and enables long-horizon reasoning about
the state of any finding.

---

## 2. Non-Goals

This design intentionally does **not** include, authorize, or plan for:

- Auto-resolving review threads (resolution requires policy-checker eligibility proof)
- Dismissing GitHub reviews
- Deleting review comments
- Mutating branch protection or GitHub repository settings
- Running live autocoder remediation campaigns
- Replacing GitHub as the source of truth for review thread state

The registry is a decision-assist tool. Branch protection settings and GitHub's
enforcement of conversation resolution remain the authoritative merge gates.

---

## 3. Finding Lifecycle States

### State Definitions

#### `OPEN`
- **Meaning**: The finding is active on the current PR head.
- **Blocking**: Must block merge if severity (P0/P1/P2) and policy require it.
  Specifically, AED's `review_comment_gate` treats P0, P1, and P2 review-comment
  findings as merge-blocking unless the finding is cleared by one of the permitted
  `OPEN` exit transitions. P2 is **not informational by default**; it blocks
  merge unless explicitly waived, superseded, invalidated with proof, fixed by
  patch, or escalated.
- **Exit**: Transitions to `RESOLVED_BY_PATCH`, `ESCALATED`, `WAIVED`, `SUPERSEDED`,
  or `INVALID`. There is no path from `OPEN` directly to `RESOLVED_BY_POLICY`
  — that transition applies only to `STALE` findings.

#### `STALE`
- **Meaning**: The finding belongs to an old commit or thread. The current diff no longer
  contains the flagged issue.
- **Blocking**: Does not block current-head merge. May be eligible for the stale-thread
  policy checker.
- **Exit**: Transitions to `RESOLVED_BY_POLICY`, `ESCALATED`, or `INVALID`.

#### `RESOLVED_BY_PATCH`
- **Meaning**: The finding's root cause was eliminated by a code, docs, or test change.
  No GitHub thread mutation is required unless the conversation is still open.
- **Exit**: May transition to `RESOLVED_BY_POLICY` if a stale GitHub thread must still be
  resolved and the checker proves eligibility.

#### `RESOLVED_BY_POLICY`
- **Meaning**: A stale thread was resolved after the policy checker returned
  `ELIGIBLE_STALE_THREAD_RESOLUTION`. The finding is closed with audit evidence.
- **Requires**: Audit evidence (see Section 7).

#### `WAIVED`
- **Meaning**: The finding is intentionally accepted.
- **Requires**: Explicit rationale and scope; must be rare.
- **Exit**: None (terminal state unless escalated).

#### `SUPERSEDED`
- **Meaning**: The finding was replaced by a newer, more accurate finding on the same or
  a later commit. The old finding independently blocks no further.
- **Exit**: None (terminal state).

#### `ESCALATED`
- **Meaning**: The finding requires a human or operator decision. No automated
  resolution is permitted.
- **Exit**: None until human resolves.

#### `INVALID`
- **Meaning**: The finding is factually wrong, with proof. The finding does not
  represent a real defect.
- **Note**: If the finding is in an unresolved GitHub thread, thread resolution may still
  be required (via `RESOLVED_BY_POLICY` transition) to satisfy `required_conversation_resolution`.

---

## 4. Required Registry Fields

Each registry record describes one finding across its full lifetime. The record is
append-only: once written, the `lifecycle_state` and `updated_at` may change, but the
original `created_at` record and audit trail must be preserved.

Records are stored as newline-delimited JSON (JSONL) in a local file within the AED
repo (e.g., `.aed/finding_registry.jsonl`) and/or in a per-PR artifact attached to the
CI run.

### Schema

```yaml
# Identity
finding_id:          string   # stable, globally unique: "codex-{sha8}" or "local-{uuid}"
pr_number:           integer  # GitHub PR number
thread_id:           string   # GitHub review thread ID (PRRT_*) or null
comment_id:          string   # GitHub review comment ID or null

# Source
source:              string   # "check_pr_review_comments", "check_stale_review_thread_resolution",
                              # "final_gate_status", "manual_operator", etc.
author:              string   # GitHub actor: user or bot name
severity:            string   # P0 | P1 | P2 | P3 | UNSPECIFIED_BLOCKING | UNSPECIFIED_INFO

# Content
title:               string   # short summary (first line or badge label from comment body)
body_summary:        string   # first 200 chars of comment body, stripped of markdown
path:                string   # file path (if applicable) or "N/A"
line:                integer  # line number (if applicable) or null
flagged_pattern:     string   # exact string that triggered the finding
replacement_pattern: string   # suggested replacement (if applicable) or null

# Provenance
original_commit_sha: string   # commit the finding was raised on
current_head_sha:    string   # PR head SHA at time of record creation
base_sha:            string   # PR base branch SHA

# Lifecycle
lifecycle_state:     string   # OPEN | STALE | RESOLVED_BY_PATCH | RESOLVED_BY_POLICY |
                              #   WAIVED | SUPERSEDED | ESCALATED | INVALID
status_reason:       string   # human-readable explanation of why state was set

# Resolution audit (required for RESOLVED_BY_POLICY)
evidence_commands:   list[string]  # gh/api commands used as evidence
evidence_summary:    string   # one-paragraph summary of the evidence
audit_log_path:      string   # path to per-event audit JSON (if written)

# Timestamps
created_at:          string   # ISO-8601 UTC
updated_at:           string   # ISO-8601 UTC
resolved_at:          string   # ISO-8601 UTC or null
resolved_by:          string   # "policy_checker" | "operator" | "automated_ci" | null
resolution_method:   string   # "resolveReviewThread" | "patch_applied" | "waiver" |
                              #   "manual_override" | "not_applicable" | null

# Cross-run context (for cross-run deduplication)
waiter_status:        string   # last waiter stage result for this finding's PR
ci_status:            string   # last CI result for this finding's PR
pmg_status:           string   # last PMG result for this finding's PR

# Gate decision (records why the finding blocks or does not block merge)
blocking_level:       string   # P0 | P1 | P2 | P3 | UNSPECIFIED_BLOCKING | UNSPECIFIED_INFO |
                              #   null — severity of the finding (same as `severity`)
merge_blocking:       boolean  # true if this finding blocks merge under active policy;
                              #   false if it does not. Derived from `severity` + `lifecycle_state`
                              #   against the active gate policy.
gate_source:          string   # which gate made the blocking decision:
                              #   "review_comment_gate" | "conversation_resolution_check" |
                              #   "pmg" | "final_gate_status" | "ci" | null
gate_policy_version:  string   # version identifier for the gate policy in effect
                              #   at the time merge_blocking was set (e.g., "v1.0", "v2.1")
```

### Field Constraints

- `finding_id` must be stable across runs for the same finding. Derived from thread ID
  + commit SHA + body hash when available.
- `lifecycle_state` must never be null; defaults to `OPEN` on creation.
- `resolved_at`, `resolved_by`, `resolution_method` must be null when `lifecycle_state`
  is `OPEN` or `STALE`.
- `evidence_commands`, `evidence_summary`, `audit_log_path` are required when
  `lifecycle_state` is `RESOLVED_BY_POLICY`.

---

## 5. Allowed Transitions

### State Transition Diagram

> **P2 is blocking by default.** `OPEN` findings with severity P2 must be resolved
> via `RESOLVED_BY_PATCH`, `WAIVED`, `SUPERSEDED`, `INVALID` (with proof), or
> `ESCALATED`. There is no assumption that P2 is informational or non-blocking.
> The `review_comment_gate` treats P2 as a merge blocker.

```
OPEN → RESOLVED_BY_PATCH      # code/docs/test eliminated root cause
OPEN → ESCALATED              # human decision needed
OPEN → WAIVED                 # explicit waiver granted
OPEN → SUPERSEDED             # newer finding replaces this one
OPEN → INVALID                # finding is factually wrong with proof

STALE → RESOLVED_BY_POLICY    # policy checker proved eligibility; thread resolved
STALE → ESCALATED             # human decision needed
STALE → INVALID               # finding is factually wrong

RESOLVED_BY_PATCH → RESOLVED_BY_POLICY
  # GitHub thread still unresolved AND checker proves current head is clean

INVALID → RESOLVED_BY_POLICY
  # finding is in unresolved GitHub thread AND checker proves current head is safe
  # (INVALID means finding is wrong, not that thread should remain open)
```

### Forbidden Transitions

|| From | To | Reason |
||------|----|--------|
|| `OPEN` | `RESOLVED_BY_POLICY` | Finding is on current head; must be resolved by patch or escalation |
|| `OPEN` | *(deleted)* | Findings are never deleted; state is durable |
|| `OPEN P2` | *(silently non-blocking)* | P2 blocks merge by default; must be explicitly cleared |
|| `OPEN P2` | `STALE` | Stale requires the finding to be on an old commit; current-head P2 cannot self-classify as stale |
|| `STALE` | `RESOLVED_BY_PATCH` | Patch resolution requires finding to still exist on current head |
|| `WAIVED` | *(any)* | Waivers are terminal unless escalated |
|| `SUPERSEDED` | *(any)* | Superseded findings are terminal |
|| `ESCALATED` | *(any)* | Escalated findings require human resolution |
|| `INVALID` | `WAIVED` | Invalid findings are disproven, not waived |
|| `INVALID` | `SUPERSEDED` | Invalid findings are already closed |
|| `*` | `comment deletion` | Never delete review comments |
|| `*` | `review dismissal` | Never dismiss GitHub reviews |
|| `*` | `--admin` merge | Never use admin bypass |
|| `*` | branch protection change | Never mutate GitHub settings |

---

## 6. Relationship to Existing Tools

### `check_pr_review_comments.py`

Emits per-finding records for every finding harvested from GitHub REST API.
Records are written to the registry with `lifecycle_state=OPEN` (current head) or
`lifecycle_state=STALE` (old commit).

The registry allows `check_pr_review_comments.py` to skip re-reporting findings that
were already recorded as `RESOLVED_BY_PATCH` or `WAIVED` in a prior run, reducing noise
in long-running PRs.

### `check_stale_review_thread_resolution.py`

Reads the registry to determine if a thread has already been resolved by policy. If
`thread_id` exists in the registry with `lifecycle_state=RESOLVED_BY_POLICY`, the
checker skips the thread.

Writes to the registry when returning `ELIGIBLE_STALE_THREAD_RESOLUTION`, documenting
the `flagged_pattern`, `replacement_pattern`, `evidence_commands`, and `audit_log_path`.

### `wait_for_pr_ready.py`

Reads the registry at startup to build a view of known findings for the PR. This lets
the `review_comment_gate` distinguish between:
- A new blocker (not in registry) → `HOLD_REVIEW_COMMENTS_BLOCKED`
- A known stale finding (in registry as `STALE`) → `REVIEW_COMMENTS_INCONCLUSIVE` until
  `RESOLVED_BY_POLICY`
- A resolved finding → skips

### `final_gate_status.py`

Writes registry entries for `RESOLVED_BY_PATCH` transitions when a current-head finding
is no longer present in the live diff. The `is_git_clean` and PMG checks implicitly
confirm that the finding's root cause was eliminated by the current patch.

### `verify_final_head_merge_command.py`

Reads the registry to confirm no `OPEN` blockers remain at merge time. If any
`lifecycle_state=OPEN` finding exists for the PR with P0, P1, or P2 severity,
merge is blocked even if GitHub has not yet raised a blocking thread. P2 is
blocking by default under AED's `review_comment_gate` policy; the verifier
must not apply a weaker standard.

### Branch protection `required_conversation_resolution`

The registry does not override branch protection. If GitHub reports an unresolved
conversation thread, merge is blocked regardless of registry state. The registry helps
AED *anticipate* and *explain* these blocks, not bypass them.

---

## 7. Audit Rules

Every `RESOLVED_BY_POLICY` transition must include all of the following audit evidence:

| Requirement | Description |
|-------------|-------------|
| `checker_status` | Must be `ELIGIBLE_STALE_THREAD_RESOLUTION` from `check_stale_review_thread_resolution.py` |
| `thread_id` | Exact GitHub thread ID (e.g., `PRRT_kwDOSHFpYM6FvDDE`) |
| `current_head_sha` | The PR head SHA at time of resolution (must match latest SHA or the checker proves no regression) |
| `diff_clean` | Proof that `flagged_pattern` is not present in `current_head_sha` diff |
| `replacement_exists` | If a replacement pattern was suggested, proof that it is present in the current diff |
| `ci_green` | All required CI checks passed at `current_head_sha` |
| `pmg_clean` | PMG snapshot comparison showed no regression |
| `waiter_rerun` | `wait_for_pr_ready.py` was run after resolution and returned `READY_TO_MERGE_CANDIDATE` |
| `no_deletion` | No `DELETE /repos/.../pulls/comments` was called |
| `no_dismissal` | No `dismissReview` was called |
| `no_admin` | No `--admin` flag was used in the merge command |

The `audit_log_path` field points to a machine-readable JSON file that contains the
actual command outputs (gh api responses, diff snippets, CI check results) that
constitute the evidence.

---

## 8. Example Records

### Example 1: Stale Codex thread resolved by policy

```json
{
  "finding_id": "codex-c5f6c8f38d16",
  "pr_number": 363,
  "thread_id": "PRRT_kwDOSHFpYM6FvDDE",
  "comment_id": "3325085459",
  "source": "check_pr_review_comments",
  "author": "chatgpt-codex-connector[bot]",
  "severity": "P1",
  "title": "Pass pullRequest number as an Int literal",
  "body_summary": "In the final conversation check, this query quotes the PR number...",
  "path": "scripts/local/wait_for_pr_ready.py",
  "line": 201,
  "flagged_pattern": "pullRequest(number:\"123\")",
  "replacement_pattern": "pullRequest(number:123)",
  "original_commit_sha": "3f9ab95a35d0",
  "current_head_sha": "722d99f80569463bb89d90b9e51d612a30d968b7",
  "base_sha": "18e70b5",
  "lifecycle_state": "RESOLVED_BY_POLICY",
  "status_reason": "Stale thread on old commit; current head uses correct variable (query pulls $number:Int! from --pr-number flag). Policy checker returned ELIGIBLE_STALE_THREAD_RESOLUTION.",
  "evidence_commands": [
    "gh api graphql -f query='{repository(owner:\"Slideshow11\",name:\"Automated-Edge-Discovery\"){pullRequest(number:363){reviewThreads(first:50){nodes{id isResolved isOutdated}}}}}'",
    "gh api repos/Slideshow11/Automated-Edge-Discovery/compare/main...722d99f | python3 -c \"import sys,json; d=json.load(sys.stdin); [print(f['filename'], f.get('patch','')[:200]) for c in d['commits'] for f in c.get('files',[])]\""
  ],
  "evidence_summary": "Thread PRRT_kwDOSHFpYM6FvDDE is outdated (isOutdated=True, isResolved=False). Current diff at 722d99f shows variable $number declared as Int in the GraphQL query; flagged pattern not present. Policy checker confirmed ELIGIBLE_STALE_THREAD_RESOLUTION.",
  "audit_log_path": "/tmp/aed_runs/pr363_thread_check3.json",
  "created_at": "2026-05-29T16:53:36Z",
  "updated_at": "2026-05-29T17:44:55Z",
  "resolved_at": "2026-05-29T17:44:55Z",
  "resolved_by": "policy_checker",
  "resolution_method": "resolveReviewThread",
  "waiter_status": "READY_TO_MERGE_CANDIDATE",
  "ci_status": "SUCCESS",
  "pmg_status": "CLEAN"
}
```

### Example 2: Current-head P1 blocker

```json
{
  "finding_id": "codex-abc12345",
  "pr_number": 370,
  "thread_id": "PRRT_kwDOSHFpYM6Fxyz",
  "comment_id": "4444444444",
  "source": "check_pr_review_comments",
  "author": "chatgpt-codex-connector[bot]",
  "severity": "P1",
  "title": "Hardcoded credentials in production module",
  "body_summary": "P1: This module contains hardcoded AWS credentials in global scope...",
  "path": "src/prod/auth.py",
  "line": 42,
  "flagged_pattern": "AWS_SECRET_KEY = \"AKIA...\"",
  "replacement_pattern": "import os; AWS_SECRET_KEY = os.environ['AWS_SECRET_KEY']",
  "original_commit_sha": "abc123def456",
  "current_head_sha": "abc123def456",
  "base_sha": "177b387",
  "lifecycle_state": "OPEN",
  "status_reason": "Active P1 finding on current head. Must be resolved by patch before merge.",
  "evidence_commands": [],
  "evidence_summary": "Hardcoded credential found in src/prod/auth.py line 42. No replacement pattern applied yet.",
  "audit_log_path": null,
  "created_at": "2026-05-30T10:00:00Z",
  "updated_at": "2026-05-30T10:00:00Z",
  "resolved_at": null,
  "resolved_by": null,
  "resolution_method": null,
  "waiter_status": "HOLD_REVIEW_COMMENTS_BLOCKED",
  "ci_status": "SUCCESS",
  "pmg_status": "CLEAN"
}
```

### Example 3: Current-head P2 docs/tooling finding (merge-blocking)

```json
{
  "finding_id": "codex-p2-design-gap",
  "pr_number": 364,
  "thread_id": "PRRT_kwDOSHFpYM6FwTqS",
  "comment_id": "8888888888",
  "source": "check_pr_review_comments",
  "author": "chatgpt-codex-connector[bot]",
  "severity": "P2",
  "title": "Document P2 severity as merge-blocking in lifecycle schema",
  "body_summary": "The registry design must record P2 findings as merge-blocking, not informational...",
  "path": "docs/finding_lifecycle_registry_design.md",
  "line": 281,
  "flagged_pattern": "verify_final_head_merge_command.py only blocks P0/P1, not P2",
  "replacement_pattern": null,
  "original_commit_sha": "4cf8ae8b20cd",
  "current_head_sha": "6755b0c08ffd0c2316c7152702809e909e9467b0",
  "base_sha": "177b387",
  "lifecycle_state": "OPEN",
  "status_reason": "Active P2 finding on current head. The design doc must explicitly model P2 merge-blockers in the registry schema and safety rules. Finding is blocking merge.",
  "evidence_commands": [],
  "evidence_summary": "P2 finding on docs/finding_lifecycle_registry_design.md. Current-head, not stale. Blocks merge unless cleared by patch, waiver, invalidation, supersession, or escalation.",
  "audit_log_path": null,
  "created_at": "2026-05-29T18:00:00Z",
  "updated_at": "2026-05-29T18:45:00Z",
  "resolved_at": null,
  "resolved_by": null,
  "resolution_method": null,
  "waiter_status": "HOLD_REVIEW_COMMENTS_BLOCKED",
  "ci_status": "SUCCESS",
  "pmg_status": "CLEAN",
  "merge_blocking": true,
  "blocking_level": "P2",
  "gate_source": "review_comment_gate",
  "gate_policy_version": "v1.0"
}
```

### Example 4: Invalid Codex finding disproven by gh api proof

```json
{
  "finding_id": "codex-xyz789",
  "pr_number": 365,
  "thread_id": "PRRT_kwDOSHFpYM6Finvalid",
  "comment_id": "5555555555",
  "source": "check_pr_review_comments",
  "author": "chatgpt-codex-connector[bot]",
  "severity": "P2",
  "title": "Unused import causes runtime error",
  "body_summary": "P2: The import of module X on line 10 is never used and will...",
  "path": "engine/run.py",
  "line": 10,
  "flagged_pattern": "import module_x",
  "replacement_pattern": null,
  "original_commit_sha": "def456abc789",
  "current_head_sha": "ghi789abc123",
  "base_sha": "177b387",
  "lifecycle_state": "INVALID",
  "status_reason": "Finding is factually wrong. gh api repos/Slideshow11/Automated-Edge-Discovery/pulls/365/files shows module_x IS used on line 87. Codex misread the file due to truncation.",
  "evidence_commands": [
    "gh api repos/Slideshow11/Automated-Edge-Discovery/pulls/365/files --jq '.[].filename'",
    "gh api repos/Slideshow11/Automated-Edge-Discovery/commits/ghi789abc123/files --jq '.[] | select(.filename==\"engine/run.py\")' | python3 -c \"import sys,json; d=json.load(sys.stdin); print(d['patch'])\" | grep -n 'module_x'"
  ],
  "evidence_summary": "The import on line 10 IS used on line 87 via dynamic import. Codex incorrectly flagged this as unused. The finding is disproven but the thread remains open in GitHub.",
  "audit_log_path": null,
  "created_at": "2026-05-29T14:00:00Z",
  "updated_at": "2026-05-29T14:30:00Z",
  "resolved_at": null,
  "resolved_by": "operator",
  "resolution_method": "not_applicable",
  "waiter_status": "HOLD_REVIEW_COMMENTS_BLOCKED",
  "ci_status": "SUCCESS",
  "pmg_status": "CLEAN"
}
```

### Example 4: Waived low-risk docs suggestion

```json
{
  "finding_id": "codex-low-priority-docs",
  "pr_number": 367,
  "thread_id": null,
  "comment_id": "6666666666",
  "source": "check_pr_review_comments",
  "author": "chatgpt-codex-connector[bot]",
  "severity": "UNSPECIFIED_INFO",
  "title": "Consider adding example to README",
  "body_summary": "Consider adding a usage example to the README for clarity...",
  "path": "README.md",
  "line": 1,
  "flagged_pattern": "Consider adding example to README",
  "replacement_pattern": null,
  "original_commit_sha": "jkl012mno345",
  "current_head_sha": "jkl012mno345",
  "base_sha": "177b387",
  "lifecycle_state": "WAIVED",
  "status_reason": "Docs suggestion is valid but low priority. PR is targeted at a different scope and the author explicitly waived the suggestion. Not blocking.",
  "evidence_commands": [],
  "evidence_summary": "P3/low-priority suggestion. Operator waived with rationale: out of PR scope, follow-up issue filed.",
  "audit_log_path": null,
  "created_at": "2026-05-29T15:00:00Z",
  "updated_at": "2026-05-29T15:15:00Z",
  "resolved_at": "2026-05-29T15:15:00Z",
  "resolved_by": "operator",
  "resolution_method": "waiver",
  "waiter_status": "READY_TO_MERGE_CANDIDATE",
  "ci_status": "SUCCESS",
  "pmg_status": "CLEAN"
}
```

### Example 5: Escalated ambiguous production safety finding

```json
{
  "finding_id": "codex-ambiguous-safety",
  "pr_number": 368,
  "thread_id": "PRRT_kwDOSHFpYM6Fescalate",
  "comment_id": "7777777777",
  "source": "check_pr_review_comments",
  "author": "chatgpt-codex-connector[bot]",
  "severity": "P0",
  "title": "Possible memory corruption in buffer handling",
  "body_summary": "P0: The memcpy on line 200 may overflow if input exceeds MAX_BUFFER...",
  "path": "engine/buffer.c",
  "line": 200,
  "flagged_pattern": "memcpy(dst, src, len)",
  "replacement_pattern": "safe_memcpy(dst, src, len, MAX_BUFFER)",
  "original_commit_sha": "pqr345stu678",
  "current_head_sha": "pqr345stu678",
  "base_sha": "177b387",
  "lifecycle_state": "ESCALATED",
  "status_reason": "Codex detected a possible buffer overflow. Analysis requires deep C expertise and knowledge of the runtime environment. Automated resolution cannot verify safety. Escalated to human operator.",
  "evidence_commands": [],
  "evidence_summary": "Escalated. Need human review of buffer.c line 200 in context of MAX_BUFFER and runtime environment before any resolution.",
  "audit_log_path": null,
  "created_at": "2026-05-29T16:00:00Z",
  "updated_at": "2026-05-29T16:30:00Z",
  "resolved_at": null,
  "resolved_by": null,
  "resolution_method": null,
  "waiter_status": "HOLD_REVIEW_COMMENTS_BLOCKED",
  "ci_status": "SUCCESS",
  "pmg_status": "CLEAN"
}
```

---

## 9. Future Implementation Plan

The registry is designed for incremental implementation. Each PR adds one layer
without disrupting existing tooling.

### PR A — Design only (this PR)

- Adds `docs/finding_lifecycle_registry_design.md`
- No code, no schema, no tool changes
- Establishes shared vocabulary and design constraints

### PR B — Schema validator

- Adds `validate_finding_registry_record.py`
- Validates that any new registry entry has required fields and valid state transitions
- Adds unit tests for forbidden transition detection
- No emission from existing tools yet; runs standalone on manual JSONL entries

### PR C — Append-only registry writer

- Adds `findings/append_registry.py`
- Provides a `write_record(record)` function that appends to `.aed/finding_registry.jsonl`
- Validates each record before appending
- Creates `.aed/` directory if missing
- No reads, no queries — write-only

### PR D — Wire review-comment gate to emit registry records

- Modifies `check_pr_review_comments.py` to write a registry record for each finding
- Records written with `lifecycle_state=OPEN` (current head) or `STALE` (old commit)
- Uses existing output JSON; no new dependencies

### PR E — Wire stale-thread checker to read registry context

- Modifies `check_stale_review_thread_resolution.py` to read registry before checking
- Skips threads already `RESOLVED_BY_POLICY`
- Writes new `RESOLVED_BY_POLICY` record after successful resolution
- Adds `audit_log_path` pointing to the policy checker's JSON output

### PR F — Add post-merge audit summarizer

- Adds `findings/summarize_merge_audit.py`
- Run as a post-merge step in CI
- Reads `.aed/finding_registry.jsonl` for the merged PR
- Produces a human-readable audit summary (Markdown) as a GitHub Actions artifact
- Verifies all `RESOLVED_BY_POLICY` records have required audit evidence

---

## 10. Safety Rules

The registry is a decision-assist tool. The following rules are absolute and override
any optimization goal:

1. **Never delete review comments.** Comment deletion destroys audit history. Use
   `resolveReviewThread` for stale threads; do not use `DELETE /repos/.../pulls/comments`.

2. **Never dismiss reviews.** Dismissal bypasses human review records. Only resolve
   individual threads via `resolveReviewThread`.

3. **Never use `--admin`.** The `--admin` flag bypasses branch protection. It must never
   appear in a merge command generated or verified by AED tooling.

4. **Never resolve active findings.** `OPEN` findings on the current head must be
   resolved by a code/docs/test patch, not by GitHub thread resolution. Thread
   resolution is only for `STALE` findings where the current diff is clean.

5. **Resolve only eligible stale threads.** Before calling `resolveReviewThread`,
   the stale-thread policy checker must return `ELIGIBLE_STALE_THREAD_RESOLUTION`.
   Resolve one thread at a time; rerun the waiter after each resolution.

6. **GitHub remains the source of truth.** If GitHub reports an unresolved conversation
   thread, merge is blocked regardless of what the registry says. The registry assists
   decisions but does not override branch protection.

7. **Registry assists decisions but does not override branch protection.** A `RESOLVED_BY_POLICY`
   finding means AED believes the thread is safe to close; GitHub's enforcement is
   authoritative.

8. **Audit every policy resolution.** Every `RESOLVED_BY_POLICY` transition must include
   `checker_status`, `thread_id`, `current_head_sha`, `diff_clean` evidence,
   `ci_green` confirmation, and `pmg_clean` confirmation.

9. **Current-head P0/P1/P2 findings block merge.** Any `OPEN` finding with severity
   P0, P1, or P2 on the current PR head must be cleared before merge. Clearing
   requires a patch that eliminates the root cause, an explicit waiver with
   rationale, invalidation with factual proof, supersession by a more accurate
   finding, or escalation to human decision. There is no default assumption that
   P2 is non-blocking or informational.

10. **The registry must mirror the `review_comment_gate`'s blocking policy.** The
    registry records gate decisions — it does not override them. If the
    `review_comment_gate` treats P2 as blocking, the registry must record P2
    findings with `merge_blocking: true`. The registry must not silently drop
    or deprioritize P2 findings to make merge appear cleaner than the gate
    actually allows.

11. **The registry records gate decisions; it does not override them.** A
    `RESOLVED_BY_POLICY` record means the policy checker found the stale thread
    eligible for resolution. GitHub's `required_conversation_resolution` is
    authoritative; the registry cannot override it.

---

*Design version: 1.0*
*Last updated: 2026-05-29*
*Status: Draft — implementation not yet started*