# Batch Autocoder Controller — Design v0

## 1. Objective

`run_autocoder_batch.py` processes a list of strict single-task packets sequentially by invoking `run_autocoder_single_task.py` for each task. It aggregates results, writes batch-level artifacts, and stops at `BATCH_READY_FOR_HUMAN_REVIEW` without pushing, opening PRs, merging, committing, staging files, or mutating main.

It is a **read-only batch orchestrator** that invokes `run_autocoder_single_task.py` via explicit `subprocess.run` calls with no `shell=True`.

## 2. Non-Goals for v0 (Hard Boundaries)

- No live Claude execution
- No parallelism (sequential only)
- No retries
- No auto push
- No auto PR creation
- No auto merge
- No auto commit
- No staging / `git add`
- No dispatch
- No board mutation
- No Hermes mutation
- No external audit log appends
- No memory/profile updates
- No package installation
- No `shell=True` subprocess calls

## 3. Batch Packet Schema

```json
{
  "packet_kind": "aed.autocoder.batch.v0",
  "batch_id": "string (unique identifier, used in output_root naming)",
  "base_sha": "string (40-char hex, the git SHA the batch is based on)",
  "output_root": "/tmp/aed_runs/autocoder_batch_<batch_id>",
  "max_tasks": "integer (null = no limit, cap at 10 for v0)",
  "stop_on_first_hold": true,
  "summary_title": "string (human-readable batch summary)",
  "operator_notes": "string (optional context for human reviewer)",
  "tasks": [
    { /* aed.autocoder.single_task.v0 packet object */ }
  ]
}
```

### Batch Packet Validation Rules

| Field | Rule |
|---|---|
| `packet_kind` | Must be `aed.autocoder.batch.v0` |
| `batch_id` | Non-empty string, alphanumeric + `-` + `_`, max 64 chars |
| `base_sha` | Valid 40-char hex SHA, must exist in repo |
| `output_root` | Must be outside the repo (`output_root` must not be inside REPO_ROOT) |
| `max_tasks` | Null or integer, cap at 10 in v0 |
| `stop_on_first_hold` | Boolean, defaults to true |
| `tasks` | Non-empty list of valid `aed.autocoder.single_task.v0` packets |

## 4. Task Constraints (v0)

Each task in `tasks` must satisfy the single-task schema (`aed.autocoder.single_task.v0`) with these additional v0 constraints:

- `execution_mode` must be `"mocked"` — `claude` or any other execution mode is rejected with `HOLD_UNSUPPORTED_EXECUTION_MODE`
- `mock_edits` must be present (null or empty list is allowed but the field must exist)
- `branch_name` must be unique within the batch (no two tasks share a branch name)
- `task_id` must be unique within the batch
- `output_root` must be outside the repo
- `allowed_files` must not be null and must not contain broad paths that cover more than 5 files in the repo

If any task fails validation, the batch returns `HOLD_BATCH_PACKET_INVALID` immediately.

## 5. Controller Flow

```
validate_batch_packet(batch_packet) → HOLD_BATCH_PACKET_INVALID or proceeds
validate_task_constraints(tasks) → HOLD_TASK_PACKET_INVALID or proceeds
create_output_dirs(output_root, tasks)

for each task in tasks (sequential order):
    task_output = output_root / "tasks" / task.task_id
    argv = [
        "python3", str(SINGLE_TASK_SCRIPT),
        "--task-packet-json", str(task_packet_path),
        "--output-json", str(task_output / "final_status.json"),
        "--output-md", str(task_output / "final_status.md"),
    ]
    rc, stdout, stderr = subprocess.run(argv, cwd=REPO_ROOT)
    task_result = read_json(task_output / "final_status.json")

    if task_result.status == "SINGLE_TASK_READY_FOR_HUMAN_REVIEW":
        record success, continue
    elif task_result.status.startswith("HOLD_"):
        if stop_on_first_hold:
            batch_status = HOLD_TASK_FAILED
            record failing task_id and status
            stop loop
        else:
            record HOLD and continue
    else:
        batch_status = HOLD_UNKNOWN
        stop loop

write batch_status.json and batch_status.md
stop (no push, no PR, no merge)
```

## 6. Batch Status Taxonomy

| Status | Meaning |
|---|---|
| `BATCH_READY_FOR_HUMAN_REVIEW` | All tasks completed with `SINGLE_TASK_READY_FOR_HUMAN_REVIEW` |
| `HOLD_BATCH_PACKET_INVALID` | Batch packet failed validation (missing fields, invalid SHA, etc.) |
| `HOLD_TASK_PACKET_INVALID` | One or more task packets failed validation |
| `HOLD_TASK_FAILED` | A task returned a HOLD status and `stop_on_first_hold=true` |
| `HOLD_TASK_BRANCH_COLLISION` | Two or more tasks share the same `branch_name` |
| `HOLD_DUPLICATE_TASK_ID` | Two or more tasks share the same `task_id` |
| `HOLD_OUTPUT_INSIDE_REPO` | A task's `output_root` is inside the repo |
| `HOLD_BATCH_SIZE_EXCEEDED` | Number of tasks exceeds `max_tasks` or v0 cap of 10 |
| `HOLD_UNSUPPORTED_EXECUTION_MODE` | A task has `execution_mode` other than `"mocked"` |
| `HOLD_UNKNOWN` | Unexpected exception or unhandled status |

## 7. Aggregation Logic

- Batch is **ready** only if **all** tasks return `SINGLE_TASK_READY_FOR_HUMAN_REVIEW`
- If any task returns a HOLD status and `stop_on_first_hold=true` (default), batch returns `HOLD_TASK_FAILED` with the failing `task_id` and its status
- If `stop_on_first_hold=false`, all tasks are attempted and the batch reports the worst status
- Partial artifacts are preserved — `tasks/<task_id>/final_status.json` is written for every task, including failed ones

## 8. Safety Invariants

The batch controller **must never**:
- Run with `--enable-real-claude-executor` or live Claude in v0
- Call `git push` directly or via tool
- Call `gh pr create`
- Call `gh pr merge`
- Call `git commit` or `git stage`
- Call `git add`
- Modify main branch
- Dispatch work items
- Touch boards
- Mutate Hermes skills
- Append to external audit logs
- Update memory/profile
- Install packages via pip/apt/etc.
- Use `shell=True` in any subprocess call
- Execute tasks in parallel (sequential only in v0)

## 9. Output Layout

```
/tmp/aed_runs/autocoder_batch_<batch_id>/
├── batch_packet.json                # input (validated copy)
├── batch_status.json                # batch-level result
├── batch_status.md                  # human-readable summary
└── tasks/
    ├── <task_id_1>/
    │   ├── task_packet.json         # input (validated copy)
    │   ├── final_status.json        # from single-task controller
    │   ├── final_status.md
    │   └── artifacts/
    │       ├── execution_packet.json
    │       ├── result.json
    │       ├── diff.patch
    │       ├── apply_readiness.json
    │       ├── apply_preview.json
    │       ├── apply_to_branch.json
    │       ├── applied_branch_verification.json
    │       └── pr_preview.json
    ├── <task_id_2>/
    │   └── ...
    └── <task_id_N>/
        └── ...
```

## 10. Human Review Workflow

The batch controller prepares multiple reviewed local branch candidates, but the human still decides:
- Which task branches to keep or discard
- Which PRs to open (and when)
- Whether to delete branches or leave them for later inspection
- Whether to convert results into issues, next tasks, or other artifacts

The batch controller produces `batch_status.json/md` which the human uses to decide next steps. No automated follow-on action is taken after `BATCH_READY_FOR_HUMAN_REVIEW`.

## 11. v0 Smoke Plan

Smoke test with two mocked tasks:

**Task 1** (`batch-smoke-task-001`):
- `goal`: "Append validation line A to docs/wfa_next_steps.md"
- `allowed_files`: `["docs/wfa_next_steps.md"]`
- `mock_edits`: one append edit on `docs/wfa_next_steps.md`
- `branch_name`: `apply/batch-smoke-001`
- `output_root`: `/tmp/aed_runs/autocoder_batch_<batch_id>/tasks/batch-smoke-task-001`

**Task 2** (`batch-smoke-task-002`):
- `goal`: "Append validation line B to docs/apply_tool_direct_main_process_gap_3841710.md"
- `allowed_files`: `["docs/apply_tool_direct_main_process_gap_3841710.md"]`
- `mock_edits`: one append edit on that file
- `branch_name`: `apply/batch-smoke-002`
- `output_root`: within the batch output root

**Constraints**:
- Both files must exist at `base_sha` with zero `.aed_plan` occurrences
- Both tasks use `execution_mode: mocked`
- Unique `branch_name` per task
- `base_sha` = current main HEAD

**Expected result**: `BATCH_READY_FOR_HUMAN_REVIEW`

## 12. Future Phases

| Phase | Content |
|---|---|
| v0 | Mock-only, sequential, no push/PR/merge/commit |
| v1 | Optional `stop_on_first_hold=false`, full aggregation |
| v2 | Controlled single-task live-Claude with human approval gate |
| v3 | Batch live-Claude with one-at-a-time execution and per-task human approval |
| v4 | Optional PR creation preview (human reviews and runs `gh pr create`) |
| Future | Auto-merge requires a separate explicit governance design |

Live Claude execution is out of scope for v0. When it becomes available in a future phase, it must be gated behind an explicit human approval step before any mutating operation.

## 13. Relationship to Single-Task Controller

The batch controller is a **thin orchestrator** that:
1. Validates the batch packet and task constraints
2. Calls `run_autocoder_single_task.py` for each task with explicit argv
3. Reads the resulting `final_status.json` from each single-task run
4. Aggregates statuses into `batch_status.json/md`
5. Stops without any mutating operations

The single-task controller (`run_autocoder_single_task.py`) handles all six stages (execution → apply → verification → PR preview) for each task. The batch controller does not duplicate that logic — it reuses it by subprocess invocation.

## 14. Exit Codes

| Exit Code | Meaning |
|---|---|
| 0 | Batch completed (READY or HOLD), artifacts written |
| 1 | Fatal error (batch packet unparseable, no tasks, etc.) |

On exit 0, check `batch_status.json` for the actual status.