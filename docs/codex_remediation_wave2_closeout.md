# Wave 2 Closeout — Codex Remediation

**Date:** 2026-05-27
**Main HEAD:** `754a66c` (fix: make PR readiness waiter repo-context independent #339)
**Wave 1 corpus:** `corpus/codex-remediation-pr314-320.json` (18 FIXED_ALREADY entries)
**Wave 2 corpus:** `corpus/codex-remediation-wave2-pr314-320.json` (2 FIXED_ALREADY entries)

---

## rgr-319: output-root-null-normalization

**Classification (original):** `FIXED_ALREADY`
**Classification (corrected):** REAL_BUG_STILL_PRESENT → fixed in PR #334

### What happened

The Wave 2 corpus entry for `rgr-319-output-root-null-normalization` classified the finding as `FIXED_ALREADY`. The repair-plan generator accepted that claim and produced a no-op repair packet — under the assumption the bug had been fixed before the corpus was authored.

During review, the controller-path test (`test_output_root_null_normalized_before_validation`) proved the classification was wrong: the bug was live in production. When a task packet arrives with `output_root: null`, `validate_task_constraints` rejects it as `HOLD_TASK_PACKET_INVALID` before normalization runs.

### The fix (PR #334)

The fix adds a pre-validation normalization loop in `run_autocoder_batch.py`:

```python
# Before validation: normalize all task output_roots
for i, task in enumerate(tasks):
    if not task.get("output_root"):
        tasks[i]["output_root"] = str(batch_output_root / f"task_{task['task_id']}")
normalized_tasks = tasks

# Then validate the normalized list, not the raw list
validate_task_constraints(normalized_tasks)
```

The per-task loop was updated to use `normalized_tasks[i]` instead of re-normalizing, eliminating any risk of path drift between validation and execution.

### Test added (PR #334)

`test_output_root_null_normalized_before_validation` in `tests/test_run_autocoder_batch.py:767`:
- Sends a task with `output_root: null`
- Asserts status is NOT `HOLD_TASK_PACKET_INVALID` after normalization runs before validation

### Lesson

`FIXED_ALREADY` is a claim, not a guarantee. The repair-plan flow must treat it as a hypothesis to verify, not a fact to accept. A corpus entry can be wrong in either direction:

| Classification | Actual state | Consequence |
|---|---|---|
| `FIXED_ALREADY` (wrong) | Bug still live | False confidence, bug ships |
| `FIXED_ALREADY` (correct) | Bug truly fixed | No action needed — correct |

**Rule:** Every `FIXED_ALREADY` entry in the corpus must be verified against current main before the repair-plan flow closes it. Verification requires either a passing regression test (preferred) or direct code inspection.

---

## rgr-317: repo-root-propagation

**Classification (original):** `FIXED_ALREADY`
**Classification (corrected):** ALREADY_FIXED_WITH_TEST → closed in PR #338

### What happened

The Wave 2 corpus suggested adding `test_repo_root_passed_to_stage2_executor` as a regression test. Code inspection during review showed the fix was already present in current main (`run_autocoder_batch.py:603`, `--repo-root str(task_worktree_path)`), and a test for the parent-script invocation already existed.

### Evidence

`test_batch_invokes_parent_script_with_repo_root` (line 1185) already covers:
- `--repo-root` present in argv
- Value equals `task_worktree_path` exactly
- Script used is the parent (reviewed) script, not a worktree copy

Adding the suggested `test_repo_root_passed_to_stage2_executor` would have been a duplicate.

### Lesson

Always verify current main before writing regression tests. The corpus was authored during active development; some fixes landed in the same PR cycle as the corpus.

**Rule:** Before generating a repair-plan, verify against current main's test file. A test that would pass on current main is not a gap — it's evidence of good coverage.

---

## PR #339: PR readiness waiter repo-context independence

**Not a corpus finding** — infrastructure hardening.

### What was fixed

The `wait_for_pr_ready.py` script (PR #337) was invoked by the repair-plan runner from `/tmp`. Without explicit `--repo` arguments on gh calls, `gh pr ...` defaults to the git remote of the cwd. When cwd is not a git repo, gh fails with "not a git repository".

Fixes in PR #339:
1. Added `--repo` and `--repo-root` CLI arguments with auto-detection for `REPO_ROOT`
2. All gh calls now include `--repo Slideshow11/Automated-Edge-Discovery` after the subcommand
3. `run_external_script()` uses `cwd=REPO_ROOT` so PMG and final_gate_status run from the AED repo root
4. Exit code 8 from `gh pr checks` (pending checks) handled with `check=False`
5. Duplicate CI check run handling (old SHA vs new SHA)
6. Missing check polling support added

### PMG schema regression fix

Two files produced by `run_pmg_compare()`:
- `<output>_pmg_before.json` — before-snapshot (no `status` field) — used as PMG compare input, **not** passed to final gates
- `<output>_pmg_compare.json` — compare result (`status: "clean"|"blocked"`) — passed to `final_gate_status.py` and `verify_final_head_merge_command.py`

New regression tests in `TestFinalGates`:
- `test_pmg_compare_json_is_used_for_final_gate_status` — asserts `pmg_compare_json` is passed to `--pmg-guard-state-json`
- `test_pmg_dirty_result_blocks_ready_to_merge_candidate` — asserts `HOLD_PMG_DIRTY` blocks merge and no merge command is emitted

### Lessons

1. **Gate infrastructure must be reliable before scaling repairs.** If the waiter itself has bugs (cwd-dependency, exit-code-8 mishandling, duplicate CI check confusion), it cannot reliably gate repairs. PR #339 hardened the gate before more repairs are added.

2. **No merge automation in the waiter.** The waiter writes a `next_safe_action` recommendation but never executes it. Merge is always manual, confirmed by human review of the full gate output.

3. **PMG schema is the integration contract.** `final_gate_status.py` expects `status: "clean"|"blocked"` in the PMG guard state file. Using the wrong file (before-snapshot vs compare-result) causes `HOLD_PMG_DIRTY`. The regression tests catch this.

---

## Operating rule: classify every finding against current main

Before any repair is executed, every Codex finding must be classified against current main. The classification is not:

- What the corpus says
- What the original PR said
- What the repair-plan generator outputs

The classification is: **what does current main actually do?**

Steps:
1. Identify the finding from corpus or GitHub review thread
2. Check current main's production code for the claimed bug
3. Check current main's test file for a covering regression test
4. Classify: REAL_BUG_STILL_PRESENT, ALREADY_FIXED_WITH_TEST, ALREADY_FIXED_NO_TEST, STALE_FALSE_POSITIVE, NEEDS_EVIDENCE_NOTE, NEEDS_SMALL_REPAIR_PLAN, NEEDS_HUMAN_REVIEW
5. Generate repair only for classifications that warrant code changes

---

## Wave 2 summary table

| Task ID | Original classification | Corrected classification | Action | PR |
|---|---|---|---|---|
| `rgr-319-output-root-null-normalization` | FIXED_ALREADY | REAL_BUG_STILL_PRESENT | Production fix + regression test | #334 |
| `rgr-317-repo-root-propagation` | FIXED_ALREADY | ALREADY_FIXED_WITH_TEST | Evidence-only PR | #338 |
| (waiter infrastructure) | new | hardened | Production fix + regression tests | #339 |

---

## Wave 2 key metrics

- Corpus entries reviewed: 2 (wave2) + all wave1 that had test gaps
- Real bugs found and fixed: 1 (rgr-319)
- False positives identified and documented: 1 (rgr-317 as duplicate)
- Production code changes: 3 PRs (#334, #338, #339)
- Regression tests added: 6 new tests across #334 and #339
- No live Claude execution, no autocoder batch, no Hermes mutation