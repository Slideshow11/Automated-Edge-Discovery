# Batch Autocoder Controller — Design v0

## 1. Objective

`run_autocoder_batch.py` processes a list of strict single-task packets sequentially by invoking `run_autocoder_single_task.py` for each task inside its own isolated git worktree. It aggregates results, writes batch-level artifacts, and stops at `BATCH_READY_FOR_HUMAN_REVIEW`.

**The batch controller is not read-only.** For each task it creates a dedicated git worktree outside the main repo and runs the single-task controller from within that worktree. This isolates each task's apply-branch dirty state (human-review artifact) from the next task's execution environment.

v0 is mock-only, sequential, and produces only local branch candidates — no push, no PR creation, no merge, no commit, no staging.

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

batch_start_branch = git branch --show-current ( feat/autocoder-batch-controller-v0 or main )
batch_start_head   = git rev-parse HEAD          ( base SHA for this batch run )

for each task in tasks (sequential order):
    task_worktree_path = output_root / "task_worktrees" / task.task_id

    # Create isolated worktree just for this task (outside main repo)
    git worktree add --detach <task_worktree_path> <batch_start_head>
    # Now the main repo is untouched; the task runs in its own worktree.

    argv = [
        "python3", str(SINGLE_TASK_SCRIPT),
        "--task-packet-json", str(task_packet_path),
        "--output-json", str(task_output / "final_status.json"),
        "--output-md", str(task_output / "final_status.md"),
        "--repo-root", str(task_worktree_path),
    ]
    # Run single-task controller from the reviewed parent checkout, with
    # --repo-root pointing to the task worktree so stage tools operate on
    # the correct files. The task's apply-branch dirty state stays inside
    # the worktree and does NOT pollute the main repo for the next task.
    rc, stdout, stderr = subprocess.run(argv, cwd=str(task_worktree_path))

    task_result = read_json(task_output / "final_status.json")

    # On WORKTREE_SETUP_FAILURE: remove worktree, record failure, stop if stop_on_first_hold
    # On success: keep worktree (preserves apply-branch artifact for human review), continue
    # On HOLD status: record task, stop if stop_on_first_hold, keep worktree

write batch_status.json and batch_status.md
stop (no push, no PR, no merge, worktrees preserved for human review)
```

**Per-task worktree isolation** is the core mechanism that prevents task A's dirty apply branch from blocking task B. Each task runs in a detached-HEAD worktree created from `batch_start_head`. The main repo remains clean throughout the batch. Successful task worktrees are **kept** (not deleted) so the human reviewer can inspect the apply branches.

v0 does not use `git restore` or `git checkout` in the main worktree between tasks — the worktree isolation model makes that unnecessary.

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

## 12. Per-Task Worktree Isolation

Each task is executed inside its own git worktree created via `git worktree add --detach <worktree_path> <batch_start_head>`. This is the core mechanism for task isolation.

**Why worktrees instead of restore/cleanup?**

The single-task controller intentionally leaves its apply branch with dirty uncommitted changes as a human-review artifact. Switching away from that branch with `git restore .` would silently discard or obscure the uncommitted working-tree state. True per-task worktree isolation keeps each task's artifact fully intact in its own worktree, invisible to all other tasks and to the main repo worktree.

**Worktree layout:**
```
/tmp/aed_runs/autocoder_batch_<batch_id>/task_worktrees/
├── <task_id_1>/           # detached HEAD at batch_start_head
│   └── [repo clone at that SHA]
├── <task_id_2>/           # another detached HEAD at batch_start_head
└── ...
```

**Worktree lifecycle:**
- Created before each task via `git worktree add --detach`
- On worktree creation failure: removed immediately, task recorded as `HOLD_WORKTREE_SETUP_FAILED`
- On task failure or HOLD: worktree is kept so artifacts are inspectable
- On task success: worktree is kept; human reviewer can `git worktree list` to find all task branches
- Worktrees are never auto-deleted by the batch controller

**Trusted-script model and `--repo-root`:**
`run_autocoder_single_task.py` runs the **reviewed parent checkout's** script (`SINGLE_TASK_SCRIPT`), not a script copied into the task worktree. The batch controller passes `--repo-root <task_worktree_path>` so stages 3–7 operate on the correct worktree files. `effective_repo_root` (module-level, set after argparse validation) flows to all stage tool subprocess calls. Stage 2 (`run_temp_worktree_execution.py`) operates on the **parent repo** for pre-flight checks and worktree creation — acceptable for mocked v0 only; before live Claude, stage 2 must be re-reviewed or receive `--repo-root` explicitly. The self-locating `SCRIPT_DIR.parent.parent` derivation is the **fallback** when `--repo-root` is omitted (standalone mode).

**What is preserved per task:**
- The task worktree itself (detached HEAD at `batch_start_head`)
- Any local branch created by the single-task controller inside that worktree (e.g., `apply/<task_id>`)
- Uncommitted dirty changes on that branch (the human-review artifact)
- All task output artifacts under `tasks/<task_id>/`

**What the main repo worktree remains:**
- Clean (no dirty files, no stage files) throughout the batch
- On `batch_start_branch` (e.g., `feat/autocoder-batch-controller-v0`) throughout
- `HEAD` is never moved by the batch controller

## 13. Future Phases
|---|---|
| v0 | Mock-only, sequential, no push/PR/merge/commit |
| v1 | Optional `stop_on_first_hold=false`, full aggregation |
| v2 | Controlled single-task live-Claude with human approval gate |
| v3 | Batch live-Claude with one-at-a-time execution and per-task human approval |
| v4 | Optional PR creation preview (human reviews and runs `gh pr create`) |
| Future | Auto-merge requires a separate explicit governance design |

Live Claude execution is out of scope for v0. When it becomes available in a future phase, it must be gated behind an explicit human approval step before any mutating operation.

## 14. Relationship to Single-Task Controller

The batch controller is a **thin orchestrator** that:
1. Validates the batch packet and task constraints
2. Calls `run_autocoder_single_task.py` for each task with explicit argv
3. Reads the resulting `final_status.json` from each single-task run
4. Aggregates statuses into `batch_status.json/md`
5. Stops without any mutating operations

The single-task controller (`run_autocoder_single_task.py`) handles all six stages (execution → apply → verification → PR preview) for each task. The batch controller does not duplicate that logic — it reuses it by subprocess invocation.

## 15. Exit Codes

| Exit Code | Meaning |
|---|---|
| 0 | Batch completed (READY or HOLD), artifacts written |
| 1 | Fatal error (batch packet unparseable, no tasks, etc.) |

On exit 0, check `batch_status.json` for the actual status.
## 16. Implementation Notes (v0)

### Implementation Status
The v0 implementation exists at `scripts/local/run_autocoder_batch.py` with companion tests at `tests/test_run_autocoder_batch.py`.

### Core Isolation Mechanism
Each task runs inside a dedicated git worktree (via `git worktree add --detach`) created from `batch_start_head`. The worktree path is `<output_root>/task_worktrees/<task_id>`. The batch controller invokes the **reviewed parent checkout's** `SINGLE_TASK_SCRIPT` with `--repo-root <task_worktree_path>`, so stages 3–7 operate on the correct worktree files. `effective_repo_root` (set from `--repo-root` or `_DEFAULT_REPO_ROOT` fallback) routes all stage tool subprocess calls to the worktree. This means:
- Each task's apply-branch dirty state stays in its own worktree
- The main repo worktree is never touched between tasks
- Task worktrees are kept after task completion (preserving artifacts for human review)
- Only on worktree creation failure is the worktree removed (failed setup only)

### Scope
- Mock-only execution (execution_mode="mocked" required; live execution is `HOLD_UNSUPPORTED_EXECUTION_MODE`)
- Sequential task processing (no parallelism)
- No retries
- Invokes `run_autocoder_single_task.py` via explicit argv list with `_real_subprocess_run`
- Validates batch and task constraint packets before execution
- Aggregates `final_status.json` from each single-task run into `batch_status.json` and `batch_status.md`
- Stops at `BATCH_READY_FOR_HUMAN_REVIEW` or first HOLD when `stop_on_first_hold=true`

### Safety Invariants (enforced in code)
- No `shell=True` in any subprocess call
- No `--enable-real-claude-executor` flag passed to single-task controller
- No `gh pr create`, `gh pr merge`, `git push`, `git merge`, `git commit`, `git add`, `git stage`
- No `dispatch()`, no board mutation, no Hermes skill/profile/memory/audit mutation
- No package installation
- `_real_subprocess_run` (saved reference) used for all internal git checks and single-task invocation

### Happy-Path Smoke (two-task)
The happy-path smoke uses two existing tracked docs files (e.g. `docs/wfa_next_steps.md`, `docs/wfa_run_examples.md`) with append-only mock edits. Both files must exist at `base_sha` with zero `.aed_plan` occurrences. Smoke passes when both single-task runs return `SINGLE_TASK_READY_FOR_HUMAN_REVIEW`, producing `BATCH_READY_FOR_HUMAN_REVIEW` with both task worktrees preserved and inspectable.

### Process Note
PR #314 was post-merge verified (191/191 tests pass, smoke passes), but its explicit final-gate invocation was recorded separately in `docs/pr314_batch_controller_gate_process_gap.md`. PR #317 (trusted-script / `--repo-root` fix) was merged without running explicit gate commands; the process gap is recorded in the same document (Section 11). The canonical explicit pre-merge gate sequence (Step 2: `final_gate_status.py` + Step 3: `verify_final_head_merge_command.py` before `gh pr merge`) is now documented there for all future AED PRs.

### What Remains Out of Scope
Live-Claude execution, parallelism, retries, push/PR/merge/commit/staging, dispatch, board/Hermes mutation, audit append, memory/profile update, package installation.

### Stage 2 `--repo-root` Boundary (PR #317)

PR #317 separates trusted controller code from repo content under test. The batch controller invokes the reviewed parent `run_autocoder_single_task.py` and passes `--repo-root <task_worktree_path>` so stages 3–7 operate on the correct worktree files. `effective_repo_root` flows to all downstream stage tools.

Stage 2 (`run_temp_worktree_execution.py`) now accepts `--repo-root` and uses `effective_repo_root` for all parent-repo checks. When the batch controller invokes single-task with `--repo-root <task_worktree_path>`, that value flows to stage 2's `run()` via `--repo-root <worktree_path>`. All Phase 3 checks (git_status_clean, git_rev_parse HEAD vs base_sha), path safety checks, worktree create/remove, and post-execution status checks operate on `effective_repo_root`. The parent AED checkout can remain on a feature branch; the batch controller does not globally switch to `main`.

The temp worktree is created from `base_sha` (a commit SHA, not a file path), and mock execution runs inside it with `cwd=<worktree>`. This model holds for both mock v0 and future live-Claude execution.
