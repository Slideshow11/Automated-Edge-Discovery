# Autocoder Engineering Handbook

**Status:** Draft v1 — 2026-05-24
**Scope:** AED autocoder stack after PR #315

---

## 1. What AED Autocoder Is

AED (Automated Edge Discovery) autocoder is a human-in-the-loop coding agent system. It takes a structured task packet, runs it through a six-stage verified pipeline, produces a local branch candidate with dirty changes, and stops — leaving all mutating decisions to a human.

It is not:
- An autonomous code reviewer or fixer
- A CI replacement
- A deployment tool
- A live trading or production system

It IS a structured way to generate code candidates from task packets, with every stage producing a checkpoint artifact and a fail-closed HOLD state if anything suspicious occurs.

---

## 2. System Architecture

```
Task Packet (aed.autocoder.single_task.v0)
       │
       ▼
┌─────────────────────────────────────┐
│  Single-Task Controller Chain        │
│  scripts/local/run_autocoder_        │
│  single_task.py                     │
│  (orchestrator, no REPO_ROOT arg)   │
└─────────────────────────────────────┘
       │ build_execution_packet()
       ▼
┌─────────────────────────────────────┐
│  Stage 1: Execution Packet Build     │
│  output: execution_packet.json       │
└─────────────────────────────────────┘
       │ run_temp_worktree_execution
       ▼
┌─────────────────────────────────────┐
│  Stage 2: Temp-Worktree Executor     │
│  run_temp_worktree_execution.py      │
│  output: result.json + diff.patch     │
│  status: PATCH_READY_FOR_HUMAN_REVIEW │
│  or HOLD_*                           │
└─────────────────────────────────────┘
       │ verify_temp_worktree_apply_readiness
       ▼
┌─────────────────────────────────────┐
│  Stage 3: Apply Readiness Verifier   │
│  output: apply_readiness.json         │
│  status: APPLY_READY or HOLD_*        │
└─────────────────────────────────────┘
       │ preview_temp_worktree_apply
       ▼
┌─────────────────────────────────────┐
│  Stage 4: Apply Preview               │
│  output: apply_preview.json + cmd    │
│  status: APPLY_PREVIEW_READY         │
└─────────────────────────────────────┘
       │ apply_temp_worktree_patch_to_branch
       ▼
┌─────────────────────────────────────┐
│  Stage 5: Apply to Local Branch      │
│  output: apply_to_branch.json         │
│  status: APPLY_TO_BRANCH_APPLIED │
│  (DOES NOT PUSH)                    │
└─────────────────────────────────────┘
       │ verify_temp_worktree_applied_branch
       ▼
┌─────────────────────────────────────┐
│  Stage 6: Applied Branch Verifier     │
│  output: applied_branch_verification  │
│  status: APPLIED_BRANCH_READY        │
└─────────────────────────────────────┘
       │ preview_applied_branch_pr
       ▼
┌─────────────────────────────────────┐
│  Stage 7: PR Preview                 │
│  output: pr_preview.json + gh cmd   │
│  status: PR_PREVIEW_READY           │
└─────────────────────────────────────┘
       ▼
  SINGLE_TASK_READY_FOR_HUMAN_REVIEW
  (stops — no push, no PR, no merge)

Batch Controller:
  run_autocoder_batch.py
  For each task:
    - creates isolated git worktree
    - invokes run_autocoder_single_task.py from WITHIN that worktree
    - aggregates final_status.json per task
    - produces batch_status.json + batch_status.md
  status: BATCH_READY_FOR_HUMAN_REVIEW or HOLD_*
```

---

## 3. Safety Model

Every stage operates under the same non-negotiable contract:

**Never:** `shell=True` · live Claude (v0) · `git push` · `gh pr create/merge` · `git commit/stage/add` · package install · dispatch · board mutation · Hermes skill/memory/profile mutation · audit append

**Always:** explicit argv lists · fail-closed HOLD states · artifact preservation at every stage · no auto-cleanup of successful task artifacts

The single most important safety rule: `PATCH_READY_FOR_HUMAN_REVIEW` + `APPLY_READY` is still NOT permission for automated application. Human must explicitly review and run the apply command.

---

## 4. Task Packet Design

```json
{
  "packet_kind": "aed.autocoder.single_task.v0",
  "task_id": "unique-id",
  "goal": "human-readable description",
  "allowed_files": ["path/to/file.md"],
  "forbidden_files": ["bin/", "examples/"],
  "max_changed_files": 1,
  "output_root": "/tmp/aed_runs/task_<id>",
  "branch_name": "apply/my-task-branch",
  "execution_mode": "mocked",
  "mock_edits": [{"path": "path/to/file.md", "content": "append content"}],
  "suggested_pr_title": "...",
  "suggested_pr_body": "..."
}
```

**Field rules:**
- `packet_kind` must exactly match `aed.autocoder.single_task.v0`
- `goal` must be 10–1000 chars
- `branch_name` must not already exist locally
- `output_root` must be outside the repo
- `execution_mode` must be `mocked` (v0); `claude`/`live`/`real` → `HOLD_UNSUPPORTED_EXECUTION_MODE`
- `allowed_files` must be non-null and non-empty
- Each `mock_edit.path` must be in `allowed_files` and not in `forbidden_files`

Batch packet (`aed.autocoder.batch.v0`) wraps a list of task packets with additional `base_sha`, `batch_id`, `output_root`, `stop_on_first_hold`, and task-uniqueness constraints.

---

## worktree Model

Each task in a batch runs in a **detached-HEAD git worktree** created via `git worktree add --detach <path> <base_sha>`. This provides:

- **Isolation:** task A's dirty apply-branch working-tree state is invisible to task B
- **Artifact preservation:** successful task worktrees are kept after task completion
- **Clean main repo:** the main repo worktree is never modified during a batch

The detached HEAD at `batch_start_head` means no branch is active in the worktree — the single-task controller creates its own branch inside the worktree for the apply artifact.

**Worktree lifecycle:**
- Created before each task via `git worktree add --detach`
- On worktree creation failure: removed immediately, task gets `HOLD_WORKTREE_SETUP_FAILED`
- On task HOLD: worktree kept (artifact inspectable)
- On task success: worktree kept (artifact inspectable)
- Never auto-deleted by the batch controller

**REPO_ROOT resolution:** All scripts use `SCRIPT_DIR = Path(__file__).parent.resolve()` → `REPO_ROOT = SCRIPT_DIR.parent.parent.resolve()`. When a script runs from a task worktree, `__file__` resolves inside that worktree, so `REPO_ROOT` correctly points to the worktree itself — never the main repo.

---

## 6. Single-Task Flow

1. Validate task packet against schema and constraints
2. Write approved plan file (empty stub in v0)
3. Build execution packet (`aed.temp_worktree.execution.v0`)
4. Run `run_temp_worktree_execution.py` — produces `result.json` + `diff.patch`
5. Block if status ≠ `PATCH_READY_FOR_HUMAN_REVIEW` → `HOLD_EXECUTION_NOT_PATCH_READY`
6. Run `verify_temp_worktree_apply_readiness.py` — produces `apply_readiness.json`
7. Block if status ≠ `APPLY_READY` → `HOLD_APPLY_NOT_READY`
8. Run `preview_temp_worktree_apply.py` — produces apply preview and command checklist
9. Block if status ≠ `APPLY_PREVIEW_READY` → `HOLD_APPLY_PREVIEW_NOT_READY`
10. Run `apply_temp_worktree_patch_to_branch.py` — applies patch to local branch
11. Block if status ≠ `APPLY_TO_BRANCH_APPLIED` → `HOLD_APPLY_TO_BRANCH_FAILED`
12. Run `verify_temp_worktree_applied_branch.py` — verifies branch consistency
13. Block if status ≠ `APPLIED_BRANCH_READY` → `HOLD_APPLIED_BRANCH_NOT_READY`
14. Run `preview_applied_branch_pr.py` — produces PR preview
15. Write `final_status.json` with `SINGLE_TASK_READY_FOR_HUMAN_REVIEW`

FAILS at the first non-ready status. All artifacts from passed stages are preserved.

---

## 7. Batch Flow

1. Validate batch packet + each task packet + cross-task constraints
2. For each task (sequential order):
   a. Create task worktree at `output_root/task_worktrees/<task_id>` from `batch_start_head`
   b. Write normalized task packet to `tasks/<task_id>/task_packet.json`
   c. Invoke `run_autocoder_single_task.py` from **inside** the task worktree (critical fix from PR #314)
   d. Read `final_status.json` from the task output
   e. Record task result with `task_worktree_path` preserved
   f. If `stop_on_first_hold=true` and task returned HOLD → stop batch
3. Write `batch_status.json/md`
4. Stop — no push, no PR, no merge

**Each task runs from its own worktree.** The main repo worktree is never the cwd for a task subprocess.

---

## 8. Human-Review Boundary

The autocoder stops at `SINGLE_TASK_READY_FOR_HUMAN_REVIEW` or `BATCH_READY_FOR_HUMAN_REVIEW`. What the human receives:

- `final_status.json` with all stage results
- `apply_to_branch.json` with local branch name
- `pr_preview.json` with `gh pr create` command as TEXT
- The local branch with dirty uncommitted changes

What the human must do manually:
```bash
# Review the branch
git worktree list
git log --oneline -5 apply/my-branch
git diff apply/my-branch main -- path/to/changed_file.md

# Push and open PR
git push origin apply/my-branch
gh pr create --base main --head apply/my-branch --title "..."

# Or discard
git worktree remove /tmp/aed_runs/.../task_worktrees/my-task
```

The autocoder does not push, does not open PRs, does not merge.

---

## 9. Gate and Merge Workflow

For every PR (including docs-only), run before `gh pr merge`:

**Step 1 — PMG compare:**
```bash
# Baseline snapshot if no prior clean baseline exists:
python3 scripts/local/check_persistent_mutation_guard.py snapshot \
  --root ~/.hermes --output /tmp/pmg_baseline.json

# Compare:
python3 scripts/local/check_persistent_mutation_guard.py compare \
  --root ~/.hermes --before /tmp/pmg_baseline.json \
  --output-json /tmp/pmg_compare_pr315.json \
  --output-md /tmp/pmg_compare_pr315.md
# Expected: status=clean, blocked=0
```

**Step 2 — Final gate status:**
```bash
python3 scripts/local/final_gate_status.py \
  --pr-number 315 \
  --reported-head-sha <HEAD_SHA> \
  --codex-reviewed-sha <HEAD_SHA> \
  --pmg-guard-state-json /tmp/pmg_compare_pr315.json
# Expected: READY_TO_MERGE
```

**Step 3 — Merge command verifier:**
```bash
python3 scripts/local/verify_final_head_merge_command.py \
  --pr-number 315 \
  --require-pmg \
  --pmg-guard-state-json /tmp/pmg_compare_pr315.json \
  --reported-head-sha <HEAD_SHA>
# Expected: MERGE_READY_CANDIDATE + authorization_phrase
```

**Step 4 — Merge:**
```bash
gh pr merge 315 --squash --delete-branch --match-head-commit <HEAD_SHA>
```

**Never merge without running Steps 2 and 3 explicitly.**

---

## 10. PMG and Hermes Memory Policy

`check_persistent_mutation_guard.py` snapshots your Hermes home (`~/.hermes`) before any run. After execution, `compare` detects whether any file in Hermes was created, modified, or deleted — including skills, memory, and profile files.

**What PMG detects:**
- New/modified/deleted skill files
- Memory snapshots or memory store writes
- Profile file changes
- Hermes config changes

**What PMG does NOT detect:** Changes outside `~/.hermes`.

**Policy:** AED autocoder tools must not write to `~/.hermes`. If PMG compare returns `blocked > 0` after a run, that run touched Hermes impermissibly and must not be merged.

**PMG in the gate workflow:** Both `final_gate_status.py` and `verify_final_head_merge_command.py` require a PMG compare with `status=clean` before returning `READY_TO_MERGE`.

---

## 11. Smoke Testing Guide

A smoke test for the autocoder uses `execution_mode: mocked` with `mock_edits` that append content to existing tracked files. It validates the entire pipeline without invoking any real LLM.

**Smoke requirements:**
- Use ONLY existing tracked files at `base_sha`
- No `.aed_plan.md` files in the target files
- Append-only `mock_edits` (no destructive edits)
- `max_changed_files: 1` per task
- Unique `branch_name` per task
- `stop_on_first_hold: true`

**Happy-path smoke:** Two tasks, both `SINGLE_TASK_READY_FOR_HUMAN_REVIEW` → `BATCH_READY_FOR_HUMAN_REVIEW`

**Smoke fails if:**
- `batch_status.json` has HOLD status
- Any task returned a HOLD status
- Test count differs from expected
- A worktree path is not recorded in `batch_status.json`

---

## 12. Debugging Common HOLD States

| HOLD State | Likely Cause | Fix |
|-----------|-------------|-----|
| `HOLD_MAIN_DIRTY` | Main repo has uncommitted changes | `git restore .` in main repo |
| `HOLD_OUTPUT_PATH_INSIDE_REPO` | `output_root` is inside the repo | Move `output_root` to `/tmp/` |
| `HOLD_TASK_PACKET_INVALID` | Missing fields or wrong `packet_kind` | Check schema |
| `HOLD_UNSUPPORTED_EXECUTION_MODE` | `execution_mode` is `claude`/live/real | Change to `mocked` |
| `HOLD_EXECUTION_NOT_PATCH_READY` | Stage 2 (temp-worktree executor) failed | Check `result.json` status |
| `HOLD_APPLY_NOT_READY` | `verify_temp_worktree_apply_readiness` failed | Check `apply_readiness.json` |
| `HOLD_GIT_APPLY_CHECK_FAILED` | `git apply --check` failed on diff | Review `diff.patch` |
| `HOLD_BRANCH_ALREADY_EXISTS` | Branch name already exists | Use a unique `branch_name` |
| `HOLD_PMG_NOT_CLEAN` | Hermes was mutated during run | Do not run Hermes-writing operations |

---

## 13. How to Write a Good Autocoder Task

**Good task characteristics:**
- Single, specific goal (e.g., "append a line to `docs/wfa_next_steps.md`")
- One `allowed_files` entry (narrow scope)
- `forbidden_files` excludes test files and build artifacts
- `mock_edits` with exact append content to a real tracked file
- `max_changed_files: 1`
- No live-Claude (`execution_mode: mocked`)

**Bad task characteristics:**
- Multi-file refactor in one task (too broad)
- No `allowed_files` or overly broad paths
- `execution_mode: claude` in v0
- Overly long `goal` description mixing planning and execution

**Batch tasks:**
- Each task must have a unique `task_id` and `branch_name`
- Target different files per task (no two tasks editing the same file)
- Use `stop_on_first_hold: true` in v0
- `max_tasks: 2` for smoke; up to 10 for real batches

---

## 14. How to Review Autocoder Output

After a successful run, inspect the worktree artifacts:

```bash
# 1. Find all task worktrees from the batch run
ls /tmp/aed_runs/autocoder-batch-*/task_worktrees/

# 2. Inspect task A's worktree
cd /tmp/aed_runs/autocoder-batch-*/task_worktrees/<task_id>
git log --oneline -3
git diff HEAD~1 --stat

# 3. Verify the changed files match what was expected
git diff --name-only

# 4. Review the diff
git diff -- docs/target_file.md

# 5. Check the PR preview for suggested title/body
cat /tmp/aed_runs/.../tasks/<task_id>/pr_preview.json | python3 -m json.tool
```

The human decides: which branches to push, which PRs to open, which to discard.

---

## 15. When to Stop

**Do not proceed to live Claude in v0.** The architecture is designed for mock-only execution in this phase. Live Claude introduces:
- Non-deterministic output
- Shell command injection risk
- Non-reversible mutations
- Requires command contract validation, timeout handling, and a much stronger failure taxonomy

**What is safe to add before live Claude:**
- Additional mock-packet validators
- HOLD state taxonomy expansion
- Batch aggregation improvements (stop_on_first_hold, task ordering)
- Smoke test coverage for edge cases
- Better error messages in HOLD states
- Documentation

**What requires a separate design before live Claude:**
- `--repo-root` parameterization of all stage tools
- Trusted-script verification (controller code from a fixed SHA)
- Command contract enforcement with allowlists
- Timeout and retry policy
- Long-horizon task budgets

---

## 16. Future Roadmap

See `docs/autocoder_roadmap.md` for full detail.

**Short version:**

Now: Fix `--repo-root` trusted-script issue in batch controller, add `mock_edits` smoke for batch edge cases, clean up stale worktrees.

Next: Task corpus + evaluator. Batch status UX. Better HOLD diagnosis.

Later: Live Claude through single-task controller only. Batch v1 with stop_on_first_hold=false.

Never (without separate design): Autonomous push/PR/merge, production board mutation, Hermes write access.

---

## 17. Glossary

| Term | Meaning |
|------|---------|
| **HOLD** | Fail-closed state — the pipeline stopped and the human must intervene |
| **PATCH_READY_FOR_HUMAN_REVIEW** | Temp-worktree execution produced a valid diff |
| **APPLY_READY** | Verified diff is safe to apply (correct files, no forbidden paths, clean main) |
| **APPLIED_BRANCH_READY** | Branch artifact verified as consistent with execution artifacts |
| **PR_PREVIEW_READY** | PR preview generated; human must take action |
| **SINGLE_TASK_READY_FOR_HUMAN_REVIEW** | All stages passed; human reviews and manually pushes |
| **BATCH_READY_FOR_HUMAN_REVIEW** | All tasks complete; batch artifacts ready for review |
| **worktree** | Disposable git clone at a specific SHA, isolated from main repo |
| **PMG** | Persistent Mutation Guard — snapshots and compares Hermes state |
| **command contract** | Enumerated list of allowed shell commands (defense for live Claude) |
| **batch controller** | `run_autocoder_batch.py` — orchestrator for multiple tasks |
| **single-task controller** | `run_autocoder_single_task.py` — six-stage pipeline orchestrator |
| **stage tools** | Individual scripts: executor, verifier, preview, apply, PR preview |
| **base_sha** | Git SHA the worktree/batch is based on |
| **apply branch** | Local branch created by the apply tool, holding dirty changes |

---

## 18. Command Reference

All scripts use `python3 scripts/local/<script>` and require specific `--output-json` / `--output-md` paths outside the repo. Key scripts:

| Script | Purpose |
|--------|---------|
| `run_autocoder_batch.py` | Batch orchestrator |
| `run_autocoder_single_task.py` | Single-task orchestrator |
| `run_temp_worktree_execution.py` | Execute task in isolated worktree |
| `verify_temp_worktree_apply_readiness.py` | Verify diff is safe to apply |
| `preview_temp_worktree_apply.py` | Preview apply commands |
| `apply_temp_worktree_patch_to_branch.py` | Apply diff to local branch |
| `verify_temp_worktree_applied_branch.py` | Verify applied branch |
| `preview_applied_branch_pr.py` | Preview PR creation |
| `check_persistent_mutation_guard.py` | Snapshot/compare Hermes state |
| `final_gate_status.py` | Report final gate status |
| `verify_final_head_merge_command.py` | Verify head SHA for merge |
