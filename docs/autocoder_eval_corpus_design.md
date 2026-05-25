# Autocoder Eval Corpus v0 — Design

**Status:** Draft v0 — 2026-05-24
**Branch:** `docs/autocoder-eval-corpus-v0-design`
**Type:** Design-only PR (no implementation)
**Goal:** Define replayable evaluation corpus format, eval runner, example tasks, success metrics, and failure taxonomy for the AED autocoder stack

---

## 1. Objective

The eval corpus is a **regression test suite for the autocoder controller stack** (single-task + batch), not a test of LLM output quality. Each corpus entry is a structured task packet that the controller must process end-to-end in `execution_mode: mocked`. The corpus validates that the controller correctly handles:

- Task packet normalization across all supported fields
- `--repo-root` routing to the correct worktree
- Per-task worktree isolation (task A dirty state never blocks task B)
- All HOLD states are returned correctly and with actionable diagnostics
- Artifact paths are written correctly
- Batch aggregation is correct

The corpus is **not** a performance benchmark. It is a deterministic smoke and edge-case suite. Every corpus task uses `execution_mode: mocked` and known-good `mock_edits` that are guaranteed to produce a valid diff — so failures are always controller failures, not LLM failures.

---

## 2. Non-Goals (Hard Boundaries)

- No live Claude execution
- No `--enable-real-claude-executor`
- No retry logic
- No parallelism in v0
- No autonomous push/PR/merge/commit
- No Hermes memory/profile mutation
- No package installation
- No `shell=True` in any subprocess call
- No mutation of production boards or external systems

---

## 3. Corpus Schema

A corpus is a **JSON file** containing a list of task packets plus metadata.

```json
{
  "corpus_kind": "aed.autocoder.corpus.v0",
  "corpus_id": "string (unique identifier, e.g. 'corpus-001')",
  "corpus_version": "0.1.0",
  "description": "string (human-readable purpose of this corpus)",
  "created_at": "ISO-8601 timestamp",
  "base_sha_policy": "string — 'current_main' or specific SHA",
  "tasks": [
    { /* aed.autocoder.single_task.v0 packet */ }
  ]
}
```

### Corpus Entry Fields

| Field | Rule |
|-------|------|
| `corpus_kind` | Must be `aed.autocoder.corpus.v0` |
| `corpus_id` | Non-empty string, alphanumeric + `-` + `_`, max 64 chars |
| `corpus_version` | Semver string `0.1.0` for v0 |
| `description` | Non-empty string, max 500 chars |
| `created_at` | ISO-8601 timestamp |
| `base_sha_policy` | `"current_main"` (eval runner resolves to current main HEAD at run time) or exact 40-char hex SHA |
| `tasks` | Non-empty list of valid `aed.autocoder.single_task.v0` packets |
| `tasks[].execution_mode` | Must be `"mocked"` — live modes are rejected |
| `tasks[].task_id` | Must be unique within the corpus |
| `tasks[].branch_name` | Must be unique within the corpus |

### Relationship to Batch Packet

The eval runner wraps the corpus in a `aed.autocoder.batch.v0` packet:

```
corpus.tasks[0] → batch.tasks[0]
corpus.tasks[1] → batch.tasks[1]
...
base_sha_policy → batch.base_sha (resolved at run time)
```

The batch `stop_on_first_hold` defaults to `false` for corpus runs (all tasks are attempted regardless of HOLD) so that a single failure does not prevent the rest of the corpus from running and reporting all failures at once.

---

## 4. Example Corpus: `corpus-001`

```json
{
  "corpus_kind": "aed.autocoder.corpus.v0",
  "corpus_id": "corpus-001",
  "corpus_version": "0.1.0",
  "description": "Initial smoke and edge-case suite for AED autocoder controller v0. All tasks are deterministic mocked runs targeting existing tracked docs files. Failures indicate controller bugs, not LLM output issues.",
  "created_at": "2026-05-24T00:00:00Z",
  "base_sha_policy": "current_main",
  "tasks": [
    {
      "packet_kind": "aed.autocoder.single_task.v0",
      "task_id": "corpus-001-task-001",
      "goal": "Append validation line to docs/wfa_next_steps.md",
      "allowed_files": ["docs/wfa_next_steps.md"],
      "forbidden_files": [],
      "max_changed_files": 1,
      "output_root": "/tmp/aed_runs/corpus_001/task_001",
      "branch_name": "apply/corpus-001-task-001",
      "execution_mode": "mocked",
      "mock_edits": [
        {
          "path": "docs/wfa_next_steps.md",
          "content": "append",
          "text": "\n## Corpus-001 Validation Line\nThis line confirms the single-task controller handled this task correctly.\n"
        }
      ],
      "suggested_pr_title": "docs: append corpus-001 validation line to wfa_next_steps.md",
      "suggested_pr_body": "Corpus-001 task 001: smoke test for single-task controller path.\n\nGoal: append validation line to docs/wfa_next_steps.md\nExecution: mocked\nExpected: SINGLE_TASK_READY_FOR_HUMAN_REVIEW"
    },
    {
      "packet_kind": "aed.autocoder.single_task.v0",
      "task_id": "corpus-001-task-002",
      "goal": "Append validation line to docs/wfa_run_examples.md",
      "allowed_files": ["docs/wfa_run_examples.md"],
      "forbidden_files": [],
      "max_changed_files": 1,
      "output_root": "/tmp/aed_runs/corpus_001/task_002",
      "branch_name": "apply/corpus-001-task-002",
      "execution_mode": "mocked",
      "mock_edits": [
        {
          "path": "docs/wfa_run_examples.md",
          "content": "append",
          "text": "\n## Corpus-001 Validation Line\nThis line confirms the single-task controller handled this task correctly.\n"
        }
      ],
      "suggested_pr_title": "docs: append corpus-001 validation line to wfa_run_examples.md",
      "suggested_pr_body": "Corpus-001 task 002: smoke test for single-task controller path.\n\nGoal: append validation line to docs/wfa_run_examples.md\nExecution: mocked\nExpected: SINGLE_TASK_READY_FOR_HUMAN_REVIEW"
    },
    {
      "packet_kind": "aed.autocoder.single_task.v0",
      "task_id": "corpus-001-task-003",
      "goal": "Append validation line to docs/pr314_batch_controller_gate_process_gap.md",
      "allowed_files": ["docs/pr314_batch_controller_gate_process_gap.md"],
      "forbidden_files": [],
      "max_changed_files": 1,
      "output_root": "/tmp/aed_runs/corpus_001/task_003",
      "branch_name": "apply/corpus-001-task-003",
      "execution_mode": "mocked",
      "mock_edits": [
        {
          "path": "docs/pr314_batch_controller_gate_process_gap.md",
          "content": "append",
          "text": "\n## Corpus-001 Validation Line\nThis line confirms the single-task controller handled this task correctly.\n"
        }
      ],
      "suggested_pr_title": "docs: append corpus-001 validation line to pr314 batch gap doc",
      "suggested_pr_body": "Corpus-001 task 003: smoke test for single-task controller path.\n\nGoal: append validation line to docs/pr314_batch_controller_gate_process_gap.md\nExecution: mocked\nExpected: SINGLE_TASK_READY_FOR_HUMAN_REVIEW"
    },
    {
      "packet_kind": "aed.autocoder.single_task.v0",
      "task_id": "corpus-001-task-004",
      "goal": "Append validation line to docs/aed_tasker_executor_design.md",
      "allowed_files": ["docs/aed_tasker_executor_design.md"],
      "forbidden_files": [],
      "max_changed_files": 1,
      "output_root": "/tmp/aed_runs/corpus_001/task_004",
      "branch_name": "apply/corpus-001-task-004",
      "execution_mode": "mocked",
      "mock_edits": [
        {
          "path": "docs/aed_tasker_executor_design.md",
          "content": "append",
          "text": "\n## Corpus-001 Validation Line\nThis line confirms the single-task controller handled this task correctly.\n"
        }
      ],
      "suggested_pr_title": "docs: append corpus-001 validation line to aed_tasker_executor_design.md",
      "suggested_pr_body": "Corpus-001 task 004: smoke test for single-task controller path.\n\nGoal: append validation line to docs/aed_tasker_executor_design.md\nExecution: mocked\nExpected: SINGLE_TASK_READY_FOR_HUMAN_REVIEW"
    },
    {
      "packet_kind": "aed.autocoder.single_task.v0",
      "task_id": "corpus-001-task-005",
      "goal": "Append validation line to docs/aed_executor_packet_usage.md",
      "allowed_files": ["docs/aed_executor_packet_usage.md"],
      "forbidden_files": [],
      "max_changed_files": 1,
      "output_root": "/tmp/aed_runs/corpus_001/task_005",
      "branch_name": "apply/corpus-001-task-005",
      "execution_mode": "mocked",
      "mock_edits": [
        {
          "path": "docs/aed_executor_packet_usage.md",
          "content": "append",
          "text": "\n## Corpus-001 Validation Line\nThis line confirms the single-task controller handled this task correctly.\n"
        }
      ],
      "suggested_pr_title": "docs: append corpus-001 validation line to executor packet usage doc",
      "suggested_pr_body": "Corpus-001 task 005: smoke test for single-task controller path.\n\nGoal: append validation line to docs/aed_executor_packet_usage.md\nExecution: mocked\nExpected: SINGLE_TASK_READY_FOR_HUMAN_REVIEW"
    }
  ]
}
```

**Corpus files must exist at `base_sha_policy` resolution time.** The eval runner resolves `base_sha_policy: "current_main"` to `git rev-parse main` at run time. All five target files (`docs/wfa_next_steps.md`, `docs/wfa_run_examples.md`, `docs/pr314_batch_controller_gate_process_gap.md`, `docs/aed_tasker_executor_design.md`, `docs/aed_executor_packet_usage.md`) must exist at that SHA.

---

## 5. Eval Runner Design

### Runner Script
`scripts/local/run_autocoder_eval_corpus.py`

### Command Shape
```bash
python3 scripts/local/run_autocoder_eval_corpus.py \
  --corpus-json /path/to/corpus-001.json \
  --output-root /tmp/aed_runs/eval_corpus_001 \
  --report-json /tmp/aed_runs/eval_corpus_001/report.json \
  --report-md /tmp/aed_runs/eval_corpus_001/report.md
```

### Runner Flow

```
1. Parse and validate corpus JSON (corpus_kind, corpus_version, tasks non-empty)
2. Resolve base_sha:
     if base_sha_policy == "current_main":
         base_sha = git rev-parse main
     else:
         base_sha = base_sha_policy  # must be valid 40-char hex
3. Validate each task packet:
     - packet_kind must be aed.autocoder.single_task.v0
     - execution_mode must be "mocked"
     - task_id unique within corpus
     - branch_name unique within corpus
     - output_root outside REPO_ROOT
     - allowed_files non-null, non-empty
     - mock_edits present and valid
   → On failure: write eval error artifact, exit 1
4. Construct batch_packet from corpus:
     batch_id = corpus_id
     base_sha = resolved base_sha
     output_root = runner --output-root argument
     max_tasks = null (no cap in v0)
     stop_on_first_hold = false  (always run all tasks)
     summary_title = f"Eval corpus {corpus_id} ({corpus_version})"
     operator_notes = f"Auto-generated from corpus {corpus_id} by eval runner"
     tasks = corpus.tasks (passed through directly)
5. Invoke run_autocoder_batch.py with batch_packet JSON:
     python3 scripts/local/run_autocoder_batch.py \
       --batch-packet-json /tmp/.../batch_packet.json \
       --output-json /tmp/.../batch_output.json \
       --output-md /tmp/.../batch_output.md
   → Capture stdout/stderr, exit code
6. Read batch_status.json from output-root
7. Produce eval_report.json and eval_report.md:
     - corpus_id, corpus_version
     - base_sha used
     - total_tasks, passed, failed, held
     - per-task results from batch_status.tasks
     - eval_pass: true if all tasks passed
     - failure_summary: list of task_id + status + diagnostics
8. Write eval_report.json and eval_report.md to --output-root
9. Exit 0 if eval_pass, exit 1 if eval_fail
```

### Output Layout

```
/tmp/aed_runs/eval_corpus_<corpus_id>/
├── corpus.json                      # input (validated copy)
├── batch_packet.json                # generated batch packet
├── batch_status.json                # from batch controller
├── batch_status.md
├── eval_report.json                 # runner's assessment
├── eval_report.md
└── tasks/
    ├── <task_id_1>/
    │   ├── task_packet.json
    │   ├── final_status.json        # from single-task controller
    │   └── ... (all single-task artifacts)
    └── ...
```

---

## 6. Success Metrics

### Corpus-Level Pass Criteria

| Metric | Value |
|--------|-------|
| `eval_pass` | `true` only if ALL of below are satisfied |
| `batch_status` | `BATCH_READY_FOR_HUMAN_REVIEW` |
| All task statuses | `SINGLE_TASK_READY_FOR_HUMAN_REVIEW` |
| No HOLD states | Any HOLD = eval_fail |
| No exceptions | Unhandled exception = eval_fail |
| Main repo worktree | Clean after run (`git status --short` empty) |
| All task worktrees | Created and preserved |
| All `final_status.json` | Written for every task |
| All `diff.patch` | Non-empty and applies cleanly via `git apply --check` |

### Per-Task Pass Criteria

A task passes if:
1. `final_status.json` exists at `tasks/<task_id>/final_status.json`
2. `final_status.status == "SINGLE_TASK_READY_FOR_HUMAN_REVIEW"`
3. `apply_to_branch.json` shows a branch was created
4. `applied_branch_verification.json` shows the branch is consistent
5. `pr_preview.json` shows a PR preview was generated

---

## 7. Failure Taxonomy

### Failure Levels

**Level 1 — Eval Runner Failure (runner bug)**
The eval runner itself crashed or produced malformed output. This is a bug in the runner, not the controller.

**Level 2 — Batch Controller Failure (controller bug)**
The batch controller returned a HOLD status that should not occur in mocked execution. This indicates a regression in task packet handling, worktree isolation, `--repo-root` routing, or batch aggregation.

**Level 3 — Single-Task Controller Failure (controller bug)**
The batch completed but one or more individual tasks returned a HOLD status. The task's `final_status.json` will show which stage produced the HOLD.

**Level 4 — Corpus Definition Failure (test data bug)**
The corpus references a file that does not exist at `base_sha`, or a `mock_edit` path is not in `allowed_files`, or `branch_name` already exists. This is a corpus authoring error, not a controller failure.

### Failure Status Codes

| Code | Level | Meaning |
|------|-------|---------|
| `EVAL_RUNNER_PARSE_ERROR` | 1 | Corpus JSON unparseable or failed schema validation |
| `EVAL_RUNNER_BASE_SHA_UNRESOLVABLE` | 1 | `base_sha_policy` resolves to invalid SHA |
| `EVAL_RUNNER_BATCH_INVOCATION_FAILED` | 1 | `run_autocoder_batch.py` exited non-zero |
| `BATCH_HOLD_BATCH_PACKET_INVALID` | 2 | Batch packet validation failed (should not happen with valid corpus) |
| `BATCH_HOLD_TASK_PACKET_INVALID` | 2 | Task packet validation failed in batch |
| `BATCH_HOLD_TASK_FAILED` | 2 | A task returned HOLD and `stop_on_first_hold=true` (should not occur with `stop_on_first_hold=false`) |
| `BATCH_HOLD_WORKTREE_SETUP_FAILED` | 2 | `git worktree add` failed for a task |
| `TASK_HOLD_MAIN_DIRTY` | 3 | Main repo worktree dirty before task (worktree isolation failure) |
| `TASK_HOLD_UNSUPPORTED_EXECUTION_MODE` | 3 | Task had `execution_mode` other than `mocked` (should be caught in validation) |
| `TASK_HOLD_EXECUTION_NOT_PATCH_READY` | 3 | Stage 2 (temp-worktree executor) failed |
| `TASK_HOLD_APPLY_NOT_READY` | 3 | Stage 3 (apply readiness) failed |
| `TASK_HOLD_GIT_APPLY_CHECK_FAILED` | 3 | `git apply --check` failed on diff.patch |
| `TASK_HOLD_APPLY_TO_BRANCH_FAILED` | 3 | Stage 5 (apply to branch) failed |
| `TASK_HOLD_APPLIED_BRANCH_NOT_READY` | 3 | Stage 6 (applied branch verification) failed |
| `TASK_HOLD_PMG_NOT_CLEAN` | 3 | Hermes mutation detected during task |
| `CORPUS_TASK_FILE_NOT_FOUND` | 4 | Target file in `allowed_files` does not exist at `base_sha` |
| `CORPUS_MOCK_EDIT_PATH_INVALID` | 4 | `mock_edit.path` not in `allowed_files` |
| `CORPUS_BRANCH_NAME_COLLISION` | 4 | `branch_name` already exists at `base_sha` |
| `CORPUS_TASK_ID_DUPLICATE` | 4 | Duplicate `task_id` in corpus |
| `CORPUS_BRANCH_NAME_DUPLICATE` | 4 | Duplicate `branch_name` in corpus |

---

## 8. Regression Detection

The corpus is specifically designed to catch regressions in five areas:

### 8.1 Task Packet Normalization

The batch controller's `_normalize_task_packet()` fills in missing fields (`goal`, `suggested_pr_title`, `suggested_pr_body`) from the batch-level values. Corpus tasks include tasks with and without these fields to test normalization.

**Regression that would be caught:** If `_normalize_task_packet()` stops filling `goal` from the task's own `goal` field (e.g., always uses the batch-level default), the task would either get a wrong `goal` or a validation error. The corpus has tasks where task-level `goal` differs from batch-level `summary_title`, so a normalization bug would produce a mismatched `final_status.json`.

### 8.2 `--repo-root` Routing

The batch controller passes `--repo-root <task_worktree_path>` to the single-task controller. The single-task controller sets `effective_repo_root` from `--repo-root` or falls back to `_DEFAULT_REPO_ROOT` (self-locating from `__file__`). All stage tool subprocess calls use `effective_repo_root`.

**Regression that would be caught:** If `--repo-root` is not passed, or if `effective_repo_root` is not used in stage tool calls, the stage tools would operate on the main repo instead of the task worktree. The corpus runs multiple tasks with overlapping file scopes but different `output_root` values, so a routing failure would produce duplicate file modifications visible in the main worktree.

### 8.3 Per-Task Worktree Isolation

The batch controller creates a detached-HEAD worktree per task from `batch_start_head`. Task A's apply branch dirty state stays in task A's worktree. Task B runs in its own worktree and is unaffected.

**Regression that would be caught:** If the batch controller stops using per-task worktrees (e.g., reverts to in-repo execution), task A's dirty state would cause `HOLD_MAIN_DIRTY` before task B runs, producing `BATCH_HOLD_TASK_FAILED`. The corpus always uses `stop_on_first_hold=false` so ALL tasks run even if one fails — if task A dirty-handed the main repo, task B would get `HOLD_MAIN_DIRTY` and the corpus would report it as a Level 3 failure.

### 8.4 HOLD Behavior

Every stage in the single-task controller returns a HOLD with a specific status code if the stage fails. The batch controller propagates task HOLDs into `batch_status.tasks[].status`.

**Regression that would be caught:** If a stage silently continues after a failure (e.g., does not check the status of `verify_temp_worktree_apply_readiness.py`), the task would produce a `final_status.json` that does not match the expected artifact structure. The eval runner's artifact checks (`apply_readiness.json` must exist, `apply_to_branch.json` must exist) would detect a missing artifact and report `EVAL_RUNNER_BATCH_INVOCATION_FAILED` or Level 3 failure.

### 8.5 Batch Aggregation

The batch controller aggregates per-task `final_status.json` into `batch_status.json`. The eval runner reads `batch_status.json` and checks that the task count matches, each task's status is correct, and `BATCH_READY_FOR_HUMAN_REVIEW` is set only when all tasks passed.

**Regression that would be caught:** If the batch controller drops a task result or sets `BATCH_READY_FOR_HUMAN_REVIEW` when some tasks failed, the eval runner's `eval_pass` check would fail. The corpus has 5 tasks, and the runner checks that `len(batch_status.tasks) == 5` and all 5 have `SINGLE_TASK_READY_FOR_HUMAN_REVIEW`.

---

## 9. Artifact Paths and Outputs

### Eval Runner Outputs

```
/tmp/aed_runs/eval_corpus_<corpus_id>/
├── eval_run_metadata.json     # runner version, timestamps, base_sha
├── corpus.json                # validated input copy
├── batch_packet.json          # generated batch packet (for reproducibility)
├── batch_status.json          # from batch controller
├── batch_status.md
├── eval_report.json            # pass/fail with per-task breakdown
├── eval_report.md
└── tasks/
    ├── corpus-001-task-001/
    │   ├── task_packet.json
    │   ├── final_status.json
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
    ├── corpus-001-task-002/
    │   └── ...
    └── ...
```

### Eval Report Schema

```json
{
  "report_kind": "aed.autocoder.eval_report.v0",
  "corpus_id": "corpus-001",
  "corpus_version": "0.1.0",
  "eval_runner_version": "0.1.0",
  "base_sha": "40-char hex",
  "batch_status": "BATCH_READY_FOR_HUMAN_REVIEW | HOLD_*",
  "total_tasks": 5,
  "passed_tasks": 5,
  "failed_tasks": 0,
  "held_tasks": 0,
  "eval_pass": true,
  "failure_summary": [],
  "task_results": [
    {
      "task_id": "corpus-001-task-001",
      "status": "SINGLE_TASK_READY_FOR_HUMAN_REVIEW",
      "task_worktree_path": "/tmp/aed_runs/eval_corpus_001/task_worktrees/corpus-001-task-001",
      "artifacts_present": true,
      "branch_name": "apply/corpus-001-task-001",
      "diff_patch_applies_cleanly": true
    }
  ]
}
```

---

## 10. Hard Constraints (enforced in runner code)

The eval runner **must never**:
- Invoke `run_autocoder_single_task.py` or `run_autocoder_batch.py` with `--enable-real-claude-executor`
- Pass `execution_mode` values other than `"mocked"` to the batch/single-task controllers
- Call `git push`, `gh pr create`, `gh pr merge`, `git commit`, `git stage`, `git add`
- Use `shell=True` in any `subprocess.run` call
- Write to `~/.hermes/` (skills, memory, profile)
- Append to external audit logs
- Dispatch work items or touch boards
- Install packages
- Use Hermes memory or fact_store

The eval runner **must**:
- Use `stop_on_first_hold=false` in the batch packet (run all tasks)
- Resolve `base_sha_policy: "current_main"` to `git rev-parse main` before invoking the batch controller
- Validate corpus schema before building the batch packet
- Check that all target files exist at `base_sha` before building the batch packet (Level 4 failure)
- Produce both JSON and Markdown eval reports
- Exit 0 on `eval_pass`, exit 1 on `eval_fail`

---

## 11. Next Implementation PR Recommendation

The implementation should be split into two PRs:

### PR #319 — Eval Corpus Data + Docs (design + corpus JSON only)
**Branch:** `docs/autocoder-eval-corpus-v0-design`
**Type:** Docs + data only (this PR)
**Contents:**
- `docs/autocoder_eval_corpus_design.md` — this document (design, schema, runner design, success metrics, failure taxonomy, regression detection)
- `corpus/corpus-001.json` — initial corpus with 5 smoke tasks targeting existing tracked docs files
- `docs/autocoder_eval_corpus_runbook.md` — how to run the corpus, interpret reports, add new tasks

**No Python implementation in this PR.** The design document describes the intended runner behavior so the implementation can be reviewed separately.

### PR #320 — Eval Runner Implementation
**Branch:** `feat/autocoder-eval-corpus-v0`
**Type:** Implementation
**Contents:**
- `scripts/local/run_autocoder_eval_corpus.py` — eval runner (parsing, validation, batch invocation, report generation)
- `tests/test_run_autocoder_eval_corpus.py` — unit tests for corpus validation, base_sha resolution, report generation
- Smoke test: run `corpus-001.json` through the runner and confirm `eval_pass: true`
- Explicit gate commands before merge (Section 9 of the handbook)

### Rationale for Split

The design PR can be reviewed and merged independently. The corpus format and failure taxonomy are stable even if the runner implementation takes multiple iterations. Separating design from implementation prevents the design from being held hostage by implementation complexity.

The initial corpus (`corpus-001.json`) is intentionally small (5 tasks) to serve as a smoke test. The corpus format supports gradual expansion — new tasks can be added to `corpus-001.json` or new corpus files created (`corpus-002.json`, etc.) without changing the runner.

---

## 12. Extensibility (future corpus entries)

### corpus-002: HOLD State Coverage

`corpus-002` would add tasks specifically designed to trigger HOLD states in the controller (not in stage execution, but in validation and isolation):

- Task with `task_id` collision (should produce `HOLD_DUPLICATE_TASK_ID` in batch)
- Task with `branch_name` collision (should produce `HOLD_TASK_BRANCH_COLLISION` in batch)
- Task with `output_root` inside the repo (should produce `HOLD_OUTPUT_INSIDE_REPO`)
- Task with `execution_mode: claude` (should produce `HOLD_UNSUPPORTED_EXECUTION_MODE`)
- Task with missing `goal` (tests `_normalize_task_packet()` fills it correctly)

For these tasks, the **expected** result is a HOLD (not a pass). The eval runner should check that the correct HOLD is returned, making this a negative test corpus.

### corpus-003: Multi-file Edit Coverage

`corpus-003` would test `max_changed_files > 1` with multiple `mock_edits` across files, verifying that the controller correctly aggregates multiple file changes into a single diff.patch and applies them cleanly.

### corpus-004: Long-running Batch

`corpus-004` would run 10 tasks (the v0 cap) to verify that the batch controller correctly handles the maximum task count and that all 10 task worktrees are created and preserved.

---

## 13. Safety Confirmation

| Constraint | Status |
|-----------|--------|
| No live Claude | Confirmed — corpus uses `execution_mode: mocked` only |
| No `--enable-real-claude-executor` | Confirmed — runner does not pass this flag |
| No smoke 006 | N/A — design-only PR |
| No package install | Confirmed — runner uses stdlib only |
| No `shell=True` | Confirmed — all subprocess calls use explicit argv |
| No push/PR/merge/commit/stage/git add | Confirmed — runner is read-only after batch invocation |
| No Hermes memory/profile | Confirmed — runner does not write to `~/.hermes/` |
| No dispatch/boards | Confirmed — runner does not call any external system |
| Explicit gates before merge | Required — PMG compare + `final_gate_status.py` + `verify_final_head_merge_command.py` |