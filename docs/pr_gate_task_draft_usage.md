# AED PR Gate Task-Draft Generator — Usage Guide

> PR #197 adds a read-only task-draft generator for the PR gate controller.
> This tool reads classifier output and produces Kanban-ready task drafts.
> It does NOT create Kanban tasks, dispatch workers, call LLMs, or merge PRs.

## 1. What the Task-Draft Generator Does

The **task-draft generator** (`pr_gate_task_draft.py`) is the third bridge in the AED chain:

```
classify_pr_gate_state.py → pr_gate_task_draft.py → Human/Kanban controller
     (classifier)               (task-draft generator)          (action)
```

It reads a `CLASSIFIER_PACKET.json` (from `classify_pr_gate_state.py`) and an optional `EXECUTOR_PACKET.json` (from `aed_executor_packet.py`), then produces a `PR_GATE_TASK_DRAFT.json` and `PR_GATE_TASK_DRAFT.md` describing what should happen next.

## 2. Why This Is Not Auto-Dispatch Yet

This tool generates a task draft — it does not submit it to Kanban. The distinction:

- **Task draft**: a JSON/markdown description of what a worker should do, stored at an explicit output path for human inspection
- **Kanban task**: an actual Hermes Kanban task created by `hermes kanban create-task`

Auto-dispatch is blocked because:
1. **Human review of the draft is required** before any work is dispatched
2. **Idempotency must be verified** — the same PR/head/action combination should not spawn duplicate tasks
3. **Controller rules must be enforced** — `no_auto_dispatch`, `human_merge_authorization_required`, `max_patch_cycles`

Future PRs will wire a controller that submits validated drafts to Hermes Kanban after human review.

## 3. Relationship to classify_pr_gate_state.py and watch_pr_gate_state.py

| Tool | Role | Output |
|---|---|---|
| `classify_pr_gate_state.py` | Reads PR state, classifies gate status | `CLASSIFIER_PACKET.json` |
| `watch_pr_gate_state.py` | Watches PR open, checks CI/Codex state | Compact JSON/summary report |
| `pr_gate_task_draft.py` | Reads classifier output, produces task draft | `PR_GATE_TASK_DRAFT.json` + `.md` |

`pr_gate_task_draft.py` reads the **output of `classify_pr_gate_state.py`** — it does not call GitHub APIs directly. This makes it read-only with respect to GitHub.

## 4. Relationship to Executor Packets

The optional `--executor-packet` argument lets `pr_gate_task_draft.py` read an `EXECUTOR_PACKET.json`:

- If provided, `allowed_files` and `forbidden_files` come from the executor packet rather than the classifier
- `validation_commands` and `goal` are copied into the task draft body
- The executor packet provides the authoritative scope boundary for the Builder patch task

Without an executor packet, the task draft uses `changed_files` from the classifier as the scope.

## 5. How a Future Kanban Controller Will Consume the Task Draft

The controller will:
1. Run `classify_pr_gate_state.py` on the target PR → `CLASSIFIER_PACKET.json`
2. Run `pr_gate_task_draft.py --classifier-json CLASSIFIER_PACKET.json --executor-packet EXECUTOR_PACKET.json --output-json PR_GATE_TASK_DRAFT.json`
3. Human reviews `PR_GATE_TASK_DRAFT.md`
4. If approved, controller reads `idempotency_key` from the draft and checks whether a task with that key already exists in Kanban
5. If no duplicate, controller creates the Kanban task using fields from `task_draft`

This tool does NOT create the Kanban task — it only produces the draft.

## 6. Idempotency Key Policy

Each task draft has an `idempotency_key` of the form:
```
pr196-abc123def456-{sha16_of_pr:head_sha:action}
```

The key encodes three pieces of information:
- PR number (prevents cross-PR contamination)
- Head SHA (prevents tasks for outdated commits from being treated as current)
- Action type (prevents action-type mismatches)

Before dispatching, the controller checks whether a Kanban task with this key already exists. If yes, the duplicate is skipped.

## 7. Duplicate-Task Prevention

The idempotency key alone is not sufficient — the controller must also:
- Check Kanban for an existing open task with the same `idempotency_key` label
- Reject dispatch if a task for the same PR + head + action already exists
- Log the duplicate rather than creating a second task

The task-draft generator enforces this by including `idempotency_key` in the output packet and requiring it to contain all three components (PR number, head SHA, action).

## 8. Why Reviewer Only Runs After Codex Clean

The classifier's `ready_for_reviewer` classification is only emitted when:
- `codex_status = "clean"` (Codex found no blocking issues)
- Codex review is acknowledged by a bot reaction

This sequencing exists because:
1. Codex is faster and cheaper than human review
2. Most issues can be resolved at the Codex stage
3. Human reviewer time is conserved for decisions that require judgment
4. Reviewer drafts include the specific `latest_reviewed_head` from the classifier to prevent re-reviewing stale commits

## 9. Why Builder Patch Task on codex_suggestions

`codex_suggestions` means Codex found issues that should be addressed before merge. The Builder patch task is the appropriate response because:
- Codex issues are typically fixable implementation changes
- Builder can address specific file/line suggestions
- The task draft constrains Builder to `allowed_files` from the executor packet
- Builder task is NOT the same as autonomous Builder — Builder operates within the bounded PR plan

## 10. Why Merge Remains Human-Authorized

`gate_config.require_human_merge_authorization = true` is set in all task drafts. This means:
- The merge step requires a human to type the exact phrase `I confirm`
- `check_merge_authorization.py` verifies this phrase before allowing merge
- Auto-merge is always disabled in the task draft's `controller_rules`

No amount of CI green, Codex clean, or reviewer approval bypasses the human authorization requirement.

## 11. CLI Reference

```bash
# Generate task draft from classifier packet
python3 scripts/local/pr_gate_task_draft.py generate \
  --classifier-json /tmp/CLASSIFIER_PACKET.json \
  --executor-packet /tmp/EXECUTOR_PACKET.json \
  --output-json /tmp/PR_GATE_TASK_DRAFT.json \
  --output-md /tmp/PR_GATE_TASK_DRAFT.md

# Validate a task draft
python3 scripts/local/pr_gate_task_draft.py validate /tmp/PR_GATE_TASK_DRAFT.json

# Render task draft as markdown
python3 scripts/local/pr_gate_task_draft.py render-md /tmp/PR_GATE_TASK_DRAFT.json \
  --output /tmp/PR_GATE_TASK_DRAFT.md
```

## 12. Task Draft Actions

| Action | When triggered | Assignee |
|---|---|---|
| `no_action_wait` | `ci_pending`, `codex_pending` | None |
| `create_codex_request_task_draft` | `codex_request_needed` | `aed-reviewer` |
| `create_builder_patch_task_draft` | `codex_suggestions`, `ci_failed` | `aed-builder` |
| `create_reviewer_task_draft` | `ready_for_reviewer` | `aed-reviewer` |
| `create_human_escalation_task_draft` | `blocked_scope`, `blocked_wrong_base`, `unknown`, `blocked_pr_closed`, `blocked_pr_merged` | `human` |

## 13. Example Task Draft

```json
{
  "packet_kind": "aed.pr_gate.task_draft.v1",
  "schema_version": 1,
  "generated_at": "2026-05-11T00:00:00Z",
  "source": {
    "pr_number": "196",
    "pr_url": "https://github.com/Slideshow11/Automated-Edge-Discovery/pull/196",
    "head_sha": "31d7c891ceed86f733654607f02036ce0423f468",
    "classification": "codex_suggestions",
    "ci_status": "passed",
    "codex_status": "suggestions",
    "changed_files": ["scripts/local/aed_executor_packet.py"]
  },
  "task_draft": {
    "action": "create_builder_patch_task_draft",
    "title": "Patch PR #196 (Codex suggestions)",
    "assignee": "aed-builder",
    "status": "todo",
    "idempotency_key": "pr196-abc123def456-create_builder_patch_task_draft",
    "allowed_files": ["scripts/local/aed_executor_packet.py"],
    "forbidden_files": ["engine/"],
    "body": "## Builder Patch Task for PR #196...",
    "stop_rules": [
      "Stop if PR is closed or merged.",
      "Stop if base branch changes.",
      "Do not broaden scope beyond allowed_files."
    ],
    "validation_commands": [
      "python3 -m compileall scripts/local tests",
      "PYTHONPATH=. python3 -m pytest -q"
    ],
    "expected_return_fields": ["patch_applied", "files_changed", "validation_passed"]
  },
  "controller_rules": {
    "no_auto_dispatch": true,
    "no_auto_merge": true,
    "human_merge_authorization_required": true,
    "max_patch_cycles": 3,
    "codex_cooldown_minutes": 5
  },
  "blockers_or_uncertainty": []
}
```

## 14. Safety Rules

- ❌ Do not call LLM APIs from `pr_gate_task_draft.py`
- ❌ Do not call GitHub APIs directly
- ❌ Do not create Kanban tasks
- ❌ Do not dispatch workers
- ❌ Do not merge PRs
- ❌ Do not update memory or use `skill_manage`
- ❌ Do not start PR #198 from inside this tooling
- ✅ Do validate task drafts before dispatch
- ✅ Do check idempotency key before creating Kanban task
- ✅ Do require human authorization before merge

## 15. Related Documents

- `docs/aed_tasker_executor_design.md` — AED Tasker, Executor, Specimen design
- `docs/aed_executor_packet_usage.md` — Executor packet format and CLI
- `docs/merge_authorization_guard.md` — Merge gate guard design
- `docs/current_project_status.md` — Current project state and PR tracking

---

## PR #198: Kanban Task Creation Dry-Run

> PR #198 adds a safe bridge from `PR_GATE_TASK_DRAFT.json` to Hermes Kanban.

### Overview

`pr_gate_kanban_task_create.py` consumes a `PR_GATE_TASK_DRAFT.json` (from `pr_gate_task_draft.py`) and produces a **Kanban creation plan** — either as a read-only dry-run report, or as an actual `hermes kanban create` call via `--apply`.

**Default mode is always dry-run.** No Kanban mutation happens without `--apply`.

### Why Dry-Run First?

The PR gate controller decides what task should exist next. Before creating anything, operators need to:

1. **Inspect** what the draft proposes (dry-run output)
2. **Verify** the idempotency key will prevent duplicates
3. **Confirm** the task title, assignee, and board are correct
4. **Decide** whether `--apply` is appropriate

This mirrors the PR merge authorization pattern: "I confirm" is required before merge. `--apply` is required before Kanban mutation.

### CLI

Dry-run (default — read-only, no `hermes kanban` calls):

```bash
python3 scripts/local/pr_gate_kanban_task_create.py \
  --task-draft /tmp/PR_GATE_TASK_DRAFT.json \
  --board aed \
  --output-json /tmp/KANBAN_CREATE_PLAN.json \
  --output-md /tmp/KANBAN_CREATE_PLAN.md
```

Explicit apply (creates task once):

```bash
python3 scripts/local/pr_gate_kanban_task_create.py \
  --task-draft /tmp/PR_GATE_TASK_DRAFT.json \
  --board aed \
  --apply
```

### Output Schema

The output (`KANBAN_CREATE_PLAN.json`) uses `aed.pr_gate.kanban_create_plan.v1`:

| Field | Description |
|---|---|
| `packet_kind` | `aed.pr_gate.kanban_create_plan.v1` |
| `dry_run` | `true` (dry-run) or `false` (`--apply`) |
| `source_task_draft` | Path, packet_kind, action, idempotency_key, pr_number, head_sha |
| `kanban_task` | title, assignee, status, body, idempotency_key, parent_task_id, depends_on |
| `duplicate_check` | method: `idempotency_key_tag`, duplicate_found, existing_task_id |
| `apply_result` | applied, created_task_id, command_used, stdout, stderr |
| `stop_rules` | Always: no_dispatch, no_merge, no_pr_patch, no_codex_request, no_memory_update, no_skill_manage |
| `recommended_action` | no_action / skip_duplicate / apply_failed |

### Duplicate Prevention

Each task is tagged with the `idempotency_key` from the source `PR_GATE_TASK_DRAFT.json` (format: `pr{N}-{head8?}-{hash}-{action}`).

- **Dry-run**: reports the idempotency key and explains how duplicate check works
- **--apply**: calls `hermes kanban search --tag idempotency_key={key}` before creating. If an existing task is found, creation is skipped and the existing task ID is returned.

### Stop Rules

The output plan always includes these stop rules regardless of mode:

- `no_dispatch` — does not call `hermes kanban dispatch` or any worker dispatcher
- `no_merge` — does not call `gh pr merge`
- `no_pr_patch` — does not patch PRs
- `no_codex_request` — does not call Codex
- `no_memory_update` — does not update Hermes memory
- `no_skill_manage` — does not call `skill_manage`

### Safety

The script validates the task draft body against safety patterns before processing. Rejected patterns include: `gh pr merge`, `gh pr comment`, `gh pr create`, `git push`, `hermes kanban dispatch`, `memory.update`, `fact_store`, `skill_manage`, `delegate_task`, `cronjob`, `live trading`, `broker`.

### Relationship to Future Controller Automation

`pr_gate_kanban_task_create.py` is the final bridge in the AED PR gate pipeline:

```
classify_pr_gate_state.py → pr_gate_task_draft.py → pr_gate_kanban_task_create.py
     (classifier)               (task-draft generator)       (kanban creator)

Human reviews dry-run → approves → --apply creates task
```

Future automation may wire a controller that:
1. Runs the full chain in dry-run mode
2. Presents the plan to human for authorization
3. Runs with `--apply` on explicit confirmation

This keeps human-in-the-loop for all Kanban task creation, just as the merge guard keeps human-in-the-loop for all PR merges.

### See Also

- `docs/pr_gate_task_draft_usage.md` — Task draft generator documentation
- `docs/merge_authorization_guard.md` — Merge gate guard design
- `docs/current_project_status.md` — PR tracking