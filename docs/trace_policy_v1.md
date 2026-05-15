# AED Trace Policy V1

**Effective:** 2026-05-15
**Status:** Active
**Companion:** `harness_charter_v1.md`
**Supersedes:** None (initial)

---

## 1. Purpose

This policy defines what must be logged, in what form, and why. Traces are the execution memory of the AED harness. They allow the human operator to reconstruct what happened, validate that authorization rules were followed, and understand which gate caught a real issue.

Traces are not summaries. A summary is a compressed interpretation of events; a trace is the raw record. Raw traces are the source of truth. When traces and summaries disagree, the trace governs.

---

## 2. Principles

### 2.1 Raw Trace Retention

Every significant action in the AED system produces one or more trace entries. These entries record:
- Exact SHAs, not approximate references
- Exact CI run IDs and their resulting status
- Exact Codex finding text for any clean or dirty designation
- Exact command strings or script paths used
- Exact task IDs and their board assignments
- Complete JSONL audit entries for every logged action

Summaries are useful for human review and for weekly reports. They are not a substitute for raw trace data. The audit log must preserve the raw entries.

### 2.2 Gate Value Tracking

Every PR merge trace must identify which gate caught a real issue (if any). This is not about assigning blame — it is about evaluating whether each gate is worth its cost. Gates that never catch anything are candidates for removal under the subtraction principle.

| Gate | What it catches |
|---|---|
| Scope gate | Files changed outside declared scope; unauthorized file mutations |
| Codex | Surface defects: style, clarity, obvious bugs, security surface |
| CI | Compilation failures; test failures; lint failures |
| Smoke test | Integration-level failures under controlled conditions |
| Audit validation | Malformed trace entries; missing required fields; policy violations |
| Human review | Anything the automated gates miss; judgment calls; authorization verification |

### 2.3 Subtraction Principle

Gates are not added by default. Every gate has a cost: latency, token burn, developer friction. A gate earns its place by catching real failures that would otherwise reach production.

The subtraction principle says:
- Keep gates that catch real failures.
- Reassess gates that burn time without catching defects.
- Do not add a gate "just in case" without evidence it would catch something.

This policy does not specify which gates exist. The harness charter defines the components; the PR gate workflow defines the gate sequence. This policy governs what gets traced and evaluated.

---

## 3. Required Trace Fields: PR Merge

Every PR merge trace entry (event type: `pr_merge`) must include all of the following fields:

| Field | Type | Description |
|---|---|---|
| `audit_log_version` | integer | Must be `1`. Schema version for forward compatibility. |
| `event_type` | string | Must be `pr_merge`. |
| `timestamp` | ISO-8601 string | UTC time the entry is written, e.g. `2026-05-15T02:39:47Z`. |
| `pr_number` | integer | GitHub PR number. |
| `branch` | string | Source branch name of the PR. |
| `head_sha` | 40-character hex string | Exact SHA of the branch head at merge time. |
| `merge_sha` | 40-character hex string | Exact SHA of the merge commit on the base branch. |
| `merged_at` | ISO-8601 string | UTC time of the GitHub merge event, from the GitHub API. |
| `ci_status` | string | CI status on `merge_sha`: `success`, `failure`, or `pending`. |
| `codex_status` | string | Codex review result on `head_sha`: `clean`, `dirty`, or `not_run`. |
| `scope_status` | string | Scope gate result: `clean` or `violation`. |
| `hermes_touched` | boolean | Whether a Hermes kanban operation (create, move, comment) occurred as part of this PR's lifecycle. |
| `dispatch_occurred` | boolean | Whether `hermes kanban dispatch` was called in connection with this PR. |
| `production_board_touched` | boolean | Whether the `aed` board was modified. Must be `false` for all PRs unless explicitly authorized and documented. |
| `authorization_phrase` | string | Exact phrase from the human operator authorizing the merge, or `dry_run` for test entries. |
| `blocker_or_exception` | string | If any stop rule was triggered or any gate failed, describe. Otherwise empty string. |
| `gate_catches` | object | Which gate caught a real issue (if any), keyed by gate name with a brief description of the caught defect. Empty object `{}` if no gate caught anything. |

---

## 4. Required Trace Fields: Controlled Smoke Create

Every controlled smoke artifact creation (event type: `controlled_smoke_create`) must include:

| Field | Type | Description |
|---|---|---|
| `audit_log_version` | integer | Must be `1`. |
| `event_type` | string | Must be `controlled_smoke_create`. |
| `timestamp` | ISO-8601 string | UTC time the entry is written. |
| `hermes_touched` | boolean | Always `true` for smoke creates — the artifact was created via Hermes. |
| `dispatch_occurred` | boolean | Whether a builder run was dispatched for this smoke artifact. |
| `board` | string | Board name: `aed-test` (required for smoke). |
| `task_id` | string | The Hermes task ID, e.g. `t_58d1338c`. |
| `status` | string | Task status at creation: `triage` or equivalent staging status. |
| `assignee` | string | Assignee username or empty string if unassigned. |
| `idempotency_key` | string | If the create was idempotent (same parameters idempotently recreating), the key used. Empty if not applicable. |
| `command_shape` | string | The command string or script path used to create the task, with sensitive values redacted. |
| `worker_run_spawned` | boolean | Whether a worker run was spawned for this artifact. Must be `false` for smoke artifacts. |
| `production_board_touched` | boolean | Always `false` for smoke creates — `aed-test` is not the production board. |
| `blocker_or_exception` | string | If create failed or was blocked, describe. Otherwise empty string. |
| `smoke_recommendation` | string | Recommendation for the smoke artifact outcome: `approve`, `reject`, `recheck`, or `none`. |

---

## 5. Required Trace Fields: Blocked Actions

Every blocked action trace entry (event type: `blocked_action`) must include:

| Field | Type | Description |
|---|---|---|
| `audit_log_version` | integer | Must be `1`. |
| `event_type` | string | Must be `blocked_action`. |
| `timestamp` | ISO-8601 string | UTC time the block occurred. |
| `action_requested` | string | The action that was requested, as described by the agent. |
| `blocked_reason` | string | Human-readable description of why the action was blocked. |
| `stop_rule_triggered` | string | The name of the stop rule that fired, from the list in `harness_charter_v1.md` Section 5. |
| `files_or_boards_involved` | array of strings | File paths or board names that would have been affected. |
| `remediation_path` | string | What the agent should do to unblock the action, if anything. |

---

## 6. Trace Completeness Rule

No trace entry may be emitted with a mandatory field missing, null, or empty (except where the schema explicitly permits empty). If a required field is unknown at the time the entry would be written, the entry must be held until the field is available. In-flight actions that cannot produce a complete trace entry must be logged as `blocked_action` entries with the missing field identified in `blocked_reason`.

---

## 7. Relationship to the Harness Charter

The trace policy is the logging layer of the harness charter. The harness charter defines:
- Which components exist and what they may do (components and action categories)
- What requires authorization (authorization rules)
- What must halt execution (stop rules)

This policy defines:
- What gets recorded when those rules are applied
- What format the record takes
- How to evaluate whether each gate is earning its cost

Together they form the complete operating contract. The charter says what you may do. This policy says what you must record when you do it.

---

## 8. Amendment

This policy may be amended by any PR that:
1. Modifies this file (`docs/trace_policy_v1.md`) or the companion `harness_charter_v1.md`
2. Passes all gates
3. Receives explicit merge authorization from the human operator naming the exact SHA

The `audit_log_version` field provides forward compatibility. A version bump to `2` signals a breaking schema change requiring a coordinated update to the append script and all consuming tooling.