# rgr-317 — Existing Coverage Evidence

## Task

- **Task ID:** `rgr-317-repo-root-propagation`
- **Wave:** 2
- **Corpus:** `codex-remediation-wave2-pr314-320.json`
- **Severity:** P1
- **Classification:** `FIXED_ALREADY` → `CORPUS_TASK_ALREADY_COVERED`

## Generated Repair Plan

Path: `/tmp/aed_runs/repair_plan_rgr_317_repo_root_propagation/`

The repair plan was generated in `one-task-repair-plan` mode and was ready to hand to Claude Code. However, before executing, a coverage audit was performed.

## Corpus Finding

> Add a regression test confirming that the batch controller passes --repo-root <task_worktree_path> to the stage-2 single-task controller subprocess when executing a task.

## Repair Plan Suggested Test

- **Name:** `test_repo_root_passed_to_stage2_executor`
- **Target file:** `tests/test_run_autocoder_batch.py`
- **Pattern:** Mock `subprocess.Popen`, call `_execute_single_task` with a real task dict, assert `--repo-root` in argv with correct `task_worktree_path` value.

## Existing Test Found

- **Name:** `test_batch_invokes_parent_script_with_repo_root`
- **Class:** `TestScriptSource`
- **File:** `tests/test_run_autocoder_batch.py` (lines 1182–1328)
- **Added by:** PR #317, commit `91bad01` ("fix: add --repo-root to single-task controller for trusted-script isolation")

## Evidence: Repair Plan Success Criteria vs. Existing Assertions

| # | Success Criterion | Existing Test Assertion | Location | Pass? |
|---|---|---|---|---|
| 1 | Mock `subprocess.Popen` | Uses `_real_subprocess_run` hook on `run_autocoder_batch` module — captures all subprocess calls in the batch run path | lines 1235–1271 | ✅ Equivalent |
| 2 | Call `_execute_single_task` with real task dict | Calls `run_autocoder_batch()` which exercises the full batch loop → `_execute_single_task` → subprocess pipeline | lines 1263–1270 | ✅ Covered (full path) |
| 3 | Assert `--repo-root` present in argv | `repo_root_args = [a for a in captured_argv if a == "--repo-root"]; assert len(repo_root_args) >= 1` | lines 1274–1276 | ✅ Exact match |
| 4 | Assert `--repo-root` value equals `task_worktree_path` | `assert repo_root_value == task_worktree_path_str` — exact equality | lines 1291–1297 | ✅ Exact match |
| 5 | Exercises correct production path | Production path: `run_autocoder_batch.py:596–610` passes `--repo-root`, `str(task_worktree_path)` in argv and `cwd=str(task_worktree_path)` — existing test captures this via `_real_subprocess_run` | lines 603–610 | ✅ Same path |

## Test Execution Results

```
pytest tests/test_run_autocoder_batch.py::TestScriptSource::test_batch_invokes_parent_script_with_repo_root -q
. 1 passed

pytest tests/test_run_autocoder_batch.py -q
........................................................... 59 passed
```

## Coverage Gap Conclusion

**No new test is needed.** Every assertion required by the corpus success criteria is already present in `test_batch_invokes_parent_script_with_repo_root`. The existing test:
- Uses the same production path (`run_autocoder_batch.py:596–610`)
- Asserts `--repo-root` present and equal to the exact `task_worktree_path`
- Passes on current main `82a36d1` with no modifications

## Why the Gap Wasn't Caught Earlier

The corpus entry for `rgr-317-repo-root-propagation` was authored before PR #317 was merged (PR #317 merged May 24, wave 2 corpus authored May 23). At corpus authoring time, the regression test did not yet exist. The repair plan generator correctly identified that the fix was already merged but a regression test was missing at the time.

However, PR #317 itself included a comprehensive regression test (`TestScriptSource.test_batch_invokes_parent_script_with_repo_root`) as part of the fix. The repair plan generator was not aware of this test when generating the plan, because it inspects current main state (where the test already exists) rather than the historical state at the time of the corpus finding.

## Lesson

The `FIXED_ALREADY` classification in the wave 2 corpus does not distinguish between:
1. **Already fixed + regression test added in the same PR** (rgr-317: no new test needed)
2. **Already fixed + no regression test** (rgr-319 at start: test needed)

A follow-on improvement to the corpus processing pipeline could cross-reference the source PR's test additions when classifying `FIXED_ALREADY` tasks, to avoid generating redundant repair plans for tasks whose PRs already included adequate regression coverage.

## Recommendation

- **Do not add `test_repo_root_passed_to_stage2_executor`** — it would be redundant with `test_batch_invokes_parent_script_with_repo_root`.
- **Close rgr-317 as `CORPUS_TASK_ALREADY_COVERED`** — no repair execution needed.
- **Update the corpus entry** for `rgr-317-repo-root-propagation` to reflect the resolution status, if the corpus schema supports a `resolution_status` field.

## Safety Confirmations (for this docs-only PR)

- ✅ No live Claude through AED
- ✅ No `--enable-real-claude-executor`
- ✅ No autocoder batch run
- ✅ No repair execution
- ✅ No production code modified
- ✅ No Hermes memory/profile/config touched
- ✅ No GitHub review thread resolution
- ✅ No merge
