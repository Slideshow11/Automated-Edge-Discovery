# AED Autocoder Run Controller v0

**Version:** 1
**Status:** Experimental — read-only state machine, no automatic execution

---

## Purpose

The AED Autocoder Run Controller v0 is a state machine that records and advances
run state for AED patch sessions. It converts repeated manual prompts into a
durable, machine-readable record of what happened, what is next, and why.

V0 is **read/write only to its own controller state files** under a
user-specified workspace. It does NOT:

- Edit source code
- Run Codex or CI
- Push branches
- Create PRs
- Merge PRs
- Append audit log entries

---

## State File Schema

The controller writes `CONTROLLER_STATE.json` with the following structure:

```json
{
  "controller_version": 1,
  "run_id": "aed-run-001",
  "created_at": "2026-05-17T18:00:00Z",
  "updated_at": "2026-05-17T18:30:00Z",
  "workspace": "/tmp/aed_run",
  "integration_branch": "integration/aed-run-001",
  "overall_status": "RUN_ACTIVE",
  "tasks": [
    {
      "task_id": "docs-example-001",
      "status": "TASK_PENDING",
      "dependency_status": "satisfied",
      "promotion_status": "not_promoted",
      "local_gate_status": "not_run",
      "scope_status": "not_run",
      "repair_attempts": 0,
      "max_repair_attempts": 3,
      "blocker_code": null,
      "blocker_summary": null,
      "bundle_path": null,
      "depends_on": [],
      "blocks": [],
      "promotion_group": "docs-run-summary",
      "pr_group": "autocoder-docs",
      "integration_order": 1,
      "can_run_in_parallel": false,
      "promotion_target": "integration/aed-run-001",
      "repair_history": []
    }
  ],
  "repair_events": [
    {
      "repair_id": "docs-example-001.R1",
      "task_id": "docs-example-001",
      "source": "local_gate",
      "status": "repaired",
      "summary": "Fixed markdown table lint error",
      "recorded_at": "2026-05-17T18:15:00Z"
    }
  ],
  "pr_results": [
    {
      "pr_number": 244,
      "status": "merged",
      "url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/244",
      "head_sha": "a79427badf9d206ae6ab596d1d62a588f8165400",
      "merge_sha": "e0fe1335b8b58821db6a4a9da70ffb3e0caf83e1",
      "recorded_at": "2026-05-17T18:27:22Z"
    }
  ],
  "human_action_required": false,
  "next_action": {
    "action": "run_task",
    "task_id": "docs-example-001",
    "reason": "next dependency-satisfied pending task"
  },
  "safety_invariants": {
    "hermes_touched": false,
    "dispatch_occurred": false,
    "production_board_touched": false,
    "memory_or_profile_updated": false,
    "skills_created": false
  }
}
```

---

## Run Status Enum

| Status | Description |
|--------|-------------|
| `RUN_ACTIVE` | Run is in progress; tasks being processed |
| `RUN_READY_FOR_SUMMARY` | All non-skipped tasks are promoted or ready; run summary should be generated |
| `RUN_BLOCKED` | No runnable tasks, none complete; requires human intervention |
| `RUN_FAILED_SAFETY` | A hard safety invariant was triggered (Hermes, dispatch, or production board) |
| `RUN_COMPLETE` | Run finalized; no further actions |
| `RUN_INVALID` | State corruption or integrity failure; terminal |

---

## Task Status Enum

| Status | Description |
|--------|-------------|
| `TASK_PENDING` | Task is queued; not yet executed |
| `TASK_RUNNING` | Task is currently being executed |
| `TASK_READY` | Task completed and passed gates; awaiting promotion |
| `TASK_BLOCKED` | Task cannot proceed; repair needed or human required |
| `TASK_SKIPPED` | Task explicitly skipped; excluded from run completion check |
| `TASK_FAILED_VALIDATION` | Task failed a hard validation gate; not retried automatically |

---

## Next Action Enum

| Action | Description |
|--------|-------------|
| `run_task` | Execute the indicated task on the integration branch |
| `repair_task` | Attempt a repair on the indicated blocked task |
| `generate_run_summary` | All tasks done; invoke `build_autocoder_run_summary.py` |
| `request_human` | Human intervention required; cannot proceed automatically |
| `stop` | Run is in a terminal state; no further actions |

**Note:** `promote_task`, `skip_task`, and `prepare_pr` are reserved for future use
by the controller state machine and are not produced by v0.

---

## Human Action Reasons

Used when `next_action.action == "request_human"`:

| Reason | Description |
|--------|-------------|
| `scope_expansion_required` | A task requires touching a file outside the allowed scope |
| `forbidden_file_required` | A task requires a forbidden file to be modified |
| `repair_limit_exceeded` | Repair attempts exhausted; human must resolve |
| `safety_invariant_failed` | A hard safety invariant triggered; human review required |
| `merge_authorization_required` | PR is ready but requires explicit human authorization |
| `ambiguous_task_decision` | Controller cannot determine the correct path |
| `external_system_failure` | An external dependency (CI, Codex, etc.) failed |

---

## Repair Limits

V0 currently enforces a single repair limit:

| Limit | Value | Enforced? |
|-------|-------|-----------|
| `max_local_repair_attempts_per_task` | 3 | ✅ Yes — applied to `max_repair_attempts` at init |
| `max_codex_repair_attempts_per_task` | 2 | Defined but not yet wired into task model |
| `max_ci_repair_attempts_per_pr` | 2 | Defined but not yet wired into task model |
| `max_scope_expansion_attempts` | 0 | Defined but not yet wired into task model |

When `repair_attempts >= max_repair_attempts` for a task:
- Task status becomes `TASK_BLOCKED`
- Blocker code is set to `repair_limit_exceeded`
- `next_action` becomes `request_human`

---

## Safety Invariants (Hard Fail)

These three invariants cause `overall_status` to become `RUN_FAILED_SAFETY` when
set to `true`, halting all further automated actions:

| Field | Hard Fail? | Description |
|-------|-----------|-------------|
| `hermes_touched` | ✅ Yes | Hermes create/dispatch was called |
| `dispatch_occurred` | ✅ Yes | Kanban dispatch was triggered |
| `production_board_touched` | ✅ Yes | Production board `aed` was modified |
| `memory_or_profile_updated` | ❌ Report-only | Memory or user profile was modified |
| `skills_created` | ❌ Report-only | A new skill was created |

---

## Dependency Behavior

The controller resolves task dependencies using the `integration_plan` from
`BUNDLE_INDEX.json` if present, falling back to `TASKS.jsonl` order.

A task's `dependency_status` is one of:

- `satisfied` — all `depends_on` tasks are in the completed set (promoted or ready)
- `unsatisfied` — some `depends_on` tasks are not yet complete
- `blocked_by_dependency` — a `depends_on` task is itself blocked or failed

When a task becomes `TASK_BLOCKED`, all tasks that depend on it receive
`dependency_status = "blocked_by_dependency"` until the blocker is resolved.

Run completion requires all **non-skipped, non-failed** tasks to be promoted or
ready. `TASK_SKIPPED` and `TASK_FAILED_VALIDATION` tasks are excluded from the
completion check.

---

## CLI Syntax

### Initialize a run

```bash
python3 scripts/local/autocoder_run_controller.py init \
  --run-id aed-run-001 \
  --tasks-jsonl /tmp/aed_run/TASKS.jsonl \
  --bundle-index /tmp/aed_run/BUNDLE_INDEX.json \
  --workspace /tmp/aed_run \
  --integration-branch integration/aed-run-001 \
  --output-state /tmp/aed_run/CONTROLLER_STATE.json
```

### Show current state

```bash
# JSON to stdout
python3 scripts/local/autocoder_run_controller.py status \
  --state /tmp/aed_run/CONTROLLER_STATE.json

# Markdown report
python3 scripts/local/autocoder_run_controller.py status \
  --state /tmp/aed_run/CONTROLLER_STATE.json \
  --output-md /tmp/aed_run/STATUS.md
```

### Compute next action

```bash
# JSON to stdout
python3 scripts/local/autocoder_run_controller.py next \
  --state /tmp/aed_run/CONTROLLER_STATE.json

# Markdown report
python3 scripts/local/autocoder_run_controller.py next \
  --state /tmp/aed_run/CONTROLLER_STATE.json \
  --output-md /tmp/aed_run/NEXT_ACTION.md
```

### Record a task result

```bash
python3 scripts/local/autocoder_run_controller.py record-task-result \
  --state /tmp/aed_run/CONTROLLER_STATE.json \
  --task-id docs-example-001 \
  --status TASK_READY \
  --promotion-status promoted_to_integration \
  --local-gate passed \
  --scope-status clean \
  --bundle-path /tmp/aed_run/bundles/docs-example-001
```

For a blocked task:

```bash
python3 scripts/local/autocoder_run_controller.py record-task-result \
  --state /tmp/aed_run/CONTROLLER_STATE.json \
  --task-id docs-example-001 \
  --status TASK_BLOCKED \
  --promotion-status not_promoted \
  --blocker-code scope_violation \
  --blocker-summary "Task touched file outside allowed scope: src/core.rs"
```

### Record a repair result

```bash
python3 scripts/local/autocoder_run_controller.py record-repair-result \
  --state /tmp/aed_run/CONTROLLER_STATE.json \
  --task-id docs-example-001 \
  --repair-id docs-example-001.R1 \
  --source local_gate \
  --status repaired \
  --summary "Fixed markdown table formatting and reran local gate."
```

### Record a PR result

```bash
python3 scripts/local/autocoder_run_controller.py record-pr-result \
  --state /tmp/aed_run/CONTROLLER_STATE.json \
  --pr-number 244 \
  --status merged \
  --url https://github.com/Slideshow11/Automated-Edge-Discovery/pull/244 \
  --head-sha a79427badf9d206ae6ab596d1d62a588f8165400 \
  --merge-sha e0fe1335b8b58821db6a4a9da70ffb3e0caf83e1
```

### Finalize a run

```bash
python3 scripts/local/autocoder_run_controller.py finalize-run \
  --state /tmp/aed_run/CONTROLLER_STATE.json
```

---

## NEXT_ACTION.md Output Format

When `--output-md` is specified, `next` emits a Telegram-readable markdown report:

```markdown
# AED Run Controller: Next Action

**Run:** `aed-run-001`
**Status:** `RUN_ACTIVE`
**Next action:** `run_task`
**Task:** `docs-example-001`
**Reason:** next dependency-satisfied pending task

## Operator Instruction

Run task `docs-example-001` on the `integration/aed-run-001` branch.
After completion, call `record-task-result`.
```

---

## State Transition Diagram

```
                      ┌──────────────────────────────────────┐
                      │                                      │
                      ▼                                      │
RUN_ACTIVE ──────► RUN_READY_FOR_SUMMARY ───────────► RUN_COMPLETE
    │                    │                            (finalize-run)
    │                    │
    │                    └── all non-skipped tasks promoted/ready
    │
    ├── TASK_BLOCKED ──► RUN_BLOCKED ─────────────────────► RUN_ACTIVE
    │   (repair resolves)                                     (repair succeeds)
    │
    └── safety invariant ──► RUN_FAILED_SAFETY (terminal)
         triggered
                              │
                              └── hermes_touched | dispatch_occurred |
                                                  production_board_touched = true
```

---

## v0 Scope Boundaries

| Allowed | Forbidden |
|---------|-----------|
| Write CONTROLLER_STATE.json | Edit source code |
| Read BUNDLE_INDEX.json | Run Codex |
| Read TASKS.jsonl | Run CI |
| Write NEXT_ACTION.md | Push branches |
| Emit status/markdown reports | Create PRs |
| Record repair and PR events | Merge PRs |
| Track safety invariants | Append audit log |
| | Dispatch Hermes/kanban |
| | Modify production board |

---

## Relationship to Other Tools

- **`build_autocoder_run_summary.py`** — invoked by the operator after
  `next_action == generate_run_summary`. The controller does NOT invoke it
  automatically in v0.

- **`append_merge_action_audit.py`** — invoked by the operator after PR merge.
  The controller tracks `pr_results` but does NOT call the append script.

- **`verify_final_head_merge_command.py`** — invoked by the operator before
  merge. The controller's `record-pr-result` stores the SHA evidence for later
  reference but does NOT call the verifier.

- **`build_quarantine_bundle_index.py`** — produces `BUNDLE_INDEX.json` which
  the controller consumes as input via `--bundle-index`.

---

## Usage Examples

### Example: Running a 3-Task Docs Session

This example shows how the controller tracks state across a 3-task chained docs session.

**Initialize:**
```bash
python3 scripts/local/autocoder_run_controller.py init \
  --run-id aed-run-001 \
  --tasks-jsonl /tmp/aed_run/TASKS.jsonl \
  --bundle-index /tmp/aed_run/BUNDLE_INDEX.json \
  --workspace /tmp/aed_run \
  --integration-branch integration/aed-run-001 \
  --output-state /tmp/aed_run/CONTROLLER_STATE.json
```

**Check next action:**
```bash
python3 scripts/local/autocoder_run_controller.py next \
  --state /tmp/aed_run/CONTROLLER_STATE.json \
  --output-md /tmp/aed_run/NEXT_ACTION.md
```

Initial output:
```
Next action: run_task
Task: docs-example-001
Reason: next dependency-satisfied pending task
```

**After completing task 1 and recording:**
```bash
python3 scripts/local/autocoder_run_controller.py record-task-result \
  --state /tmp/aed_run/CONTROLLER_STATE.json \
  --task-id docs-example-001 \
  --status TASK_READY \
  --promotion-status promoted_to_integration \
  --local-gate passed \
  --scope-status clean
```

Controller automatically advances to task 2. Repeat for each task.

**When all tasks are promoted:**
```
Next action: generate_run_summary
Reason: all non-skipped tasks are promoted or ready
```

### Example: Repair Loop

When a task fails its local gate:

```bash
# Task blocked — record the failure
python3 scripts/local/autocoder_run_controller.py record-task-result \
  --state /tmp/aed_run/CONTROLLER_STATE.json \
  --task-id docs-example-001 \
  --status TASK_BLOCKED \
  --promotion-status not_promoted \
  --blocker-code local_gate_failed \
  --blocker-summary "Markdown table lint error at line 42"

# Controller returns: repair_task for docs-example-001
# ... fix the issue ...
python3 scripts/local/autocoder_run_controller.py record-repair-result \
  --state /tmp/aed_run/CONTROLLER_STATE.json \
  --task-id docs-example-001 \
  --repair-id docs-example-001.R1 \
  --source local_gate \
  --status repaired \
  --summary "Fixed markdown table alignment"

# Task reset to TASK_PENDING — rerun
```

If repair limit is exceeded:
```
Next action: request_human
Reason: repair limit exceeded for docs-example-001 (3/3 attempts)
```

### Example: Status Report During Run

```bash
python3 scripts/local/autocoder_run_controller.py status \
  --state /tmp/aed_run/CONTROLLER_STATE.json \
  --output-md /tmp/aed_run/STATUS.md
```

Generates a markdown table showing all tasks, their statuses, promotion states,
repair counts, and blockers.

---

## Future V1 Directions

- Automatic promotion of `TASK_READY` tasks to integration branch
- Automatic PR creation when all tasks are `promoted_to_integration`
- Automatic `build_autocoder_run_summary.py` invocation
- Configurable repair limit policy per task type
- Integration with `append_merge_action_audit.py` for fully automated append
- Safety-invariant bypass with human override token