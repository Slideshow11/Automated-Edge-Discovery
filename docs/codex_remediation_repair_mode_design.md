# Codex Remediation Repair Mode — Design

## Status

**Design only. Not implemented. Not approved for execution.**

---

## 1. Why Full Autonomous Repair Is Not Approved

The current loop (`mock-plan-only`, PRs #328/#329) produces task packets and status reports without modifying any code. This is the approved baseline. Full autonomous repair — where the loop modifies production code, runs tests, and opens PRs — requires resolving the following open questions before any approval:

| Concern | Status |
|---|---|
| Stop-condition enforcement in execution mode | Not implemented |
| PMG snapshot comparison after task execution | Not wired as an execution gate |
| CLAUDE.md / AGENTS.md conflict when Claude Code edits files | No resolution protocol |
| Regression test authorship quality gate | No automated standard |
| Human review checkpoint after task execution but before PR | Not designed |
| Wave-level approval gate (human signs off wave before execution) | Not designed |
| Rollback protocol if post-task PMG shows Hermes mutation | Not designed |

This design does **not** resolve these concerns. It documents a constrained, single-task, plan-only mode that stays within the current safety baseline while producing the artifacts needed for human review of a repair plan.

---

## 2. One-Task Repair-Plan Mode Overview

`repair-plan-only` is a new execution mode for `run_codex_remediation_loop.py`. It processes **exactly one task** from a corpus and produces a self-contained repair-plan artifact set — but does **not** execute the repair, invoke Claude Code, or modify any file outside the output root.

It is a strict subset of what a full `live-repair` mode would do, with the execution step replaced by artifact generation.

### Design Goals

1. **Stay within approved boundaries** — no live Claude, no batch controller, no repo mutation
2. **Produce human-reviewable repair plans** — markdown prompt, JSON context, safety checklist, suggested tests, stop-condition rationale
3. **Make the handoff to Claude Code explicit and auditable** — the generated `repair_prompt.md` is the only interface; no隐式 state
4. **Be one-task-at-a-time** — prevents accidental wide-scale changes; each task gets independent review
5. **Make stop conditions checkable** — document which stop conditions pass/fail for this task before generating the plan

---

## 3. Proposed CLI

```bash
python3 scripts/local/run_codex_remediation_loop.py \
  --corpus corpus/codex-remediation-pr314-320.json \
  --output-root /tmp/aed_runs/repair-plan-rgr-314-task-id-path-traversal \
  --mode repair-plan-only \
  --task-id rgr-314-task-id-path-traversal
```

### New Arguments

| Argument | Required | Description |
|---|---|---|
| `--task-id` | Yes (in `repair-plan-only` mode) | Exact `task_id` to process |
| `--skip-stop-condition-checks` | No | Skip stop-condition documentation (for testing) |

### Modes Summary

| Mode | Description | Changes Repo? | Invokes Claude? |
|---|---|---|---|
| `mock-plan-only` | Full corpus, task packets + status | No | No |
| `repair-plan-only` | Single task, repair-plan artifact set | No | No |
| `live-repair` | *(future)* Single task, execute repair | Yes (allowed files only) | Yes |
| `live-full` | *(future)* Full loop, auto-PR | Yes | Yes |

---

## 4. Generated Files

For each `repair-plan-only` run, the following files are written under `<output_root>/`:

### 4a. `task_context.json`

Machine-readable task context for tooling and CI validation.

```json
{
  "context_kind": "aed.codex_remediation.repair_plan.task_context.v0",
  "loop_runner_version": "0.1.0",
  "task_id": "rgr-314-task-id-path-traversal",
  "wave": 1,
  "source_pr": 314,
  "finding_id": "codex-f23c1e3c82d9",
  "severity": "P1",
  "classification": "already_fixed_needs_regression_test",
  "task_category": "already_fixed_needs_regression_test",
  "action_type": "add_regression_test",
  "target_file": "tests/test_run_autocoder_batch.py",
  "allowed_files": [
    "tests/test_run_autocoder_batch.py",
    "tests/test_persistent_mutation_guard.py",
    "tests/test_aed_final_gate.py"
  ],
  "forbidden_files": [
    "scripts/local/run_autocoder_batch.py",
    "scripts/local/run_autocoder_single_task.py",
    ".hermes/**",
    "skills/**"
  ],
  "safety_notes": [
    "No live Claude execution",
    "No Hermes mutation",
    "No git push/merge"
  ],
  "finding_summary": "task_id used directly in path construction without sanitization",
  "current_main_status": "Fixed in commit e60e3b5 (PR #320)",
  "stop_condition_checks": {
    "current_head_has_blocking_finding": false,
    "unresolved_stale_p1_p2": false,
    "review_comments_blocked": false,
    "ci_green": true,
    "pmg_clean_pre_execution": true,
    "final_gate_ready": true
  },
  "generated_at": "2026-05-26T00:00:00Z"
}
```

### 4b. `repair_prompt.md`

The prompt to hand to Claude Code (or equivalent) for executing the repair. This is the primary human-facing artifact.

```markdown
# Repair Plan — rgr-314-task-id-path-traversal

## Task Summary

- **Task ID:** `rgr-314-task-id-path-traversal`
- **Wave:** 1
- **Severity:** P1
- **Finding:** task_id used directly in path construction without sanitization — vulnerable to path traversal
- **Status in current main:** Fixed in commit e60e3b5 (PR #320). `run_autocoder_batch.py` line 377 now uses `re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', task_id)` before path use.
- **Classification:** `already_fixed_needs_regression_test`

## Goal

Add a regression test that verifies the task_id path-traversal sanitization is present and working. The test must PASS on current main and FAIL if the `re.fullmatch` guard is removed.

## Target File

`tests/test_run_autocoder_batch.py`

## Allowed Files (read-only references)

- `tests/test_run_autocoder_batch.py` (where to add the test)
- `tests/test_persistent_mutation_guard.py`
- `tests/test_aed_final_gate.py`

## Forbidden Files (must not be modified)

- `scripts/local/run_autocoder_batch.py`
- `scripts/local/run_autocoder_single_task.py`
- `scripts/local/run_temp_worktree_execution.py`
- `scripts/local/run_autocoder_eval_corpus.py`
- `.hermes/**`
- `skills/**`
- `memory/**`
- `profiles/**`

## Deliverable

New test function in `tests/test_run_autocoder_batch.py`:
`test_task_id_sanitization_rejects_path_traversal`

The test must:
1. Verify that task IDs matching the traversal pattern (`../`, `/etc/passwd`, `..\\windows\\system32`) are rejected by the sanitization check
2. Verify that valid task IDs are accepted
3. Pass on current main without modifying existing code

## Success Criteria

Test exists and PASSES in current main. Test would FAIL if the `re.fullmatch` sanitization is removed from `run_autocoder_batch.py`.

## Safety Requirements

- Do NOT modify `scripts/local/run_autocoder_batch.py` or any other production script
- Do NOT enable live Claude execution mode
- Do NOT attempt to merge, push, or commit changes
- Do NOT write to `.hermes/`, `skills/`, `memory/`, or `profiles/`
- Scope changes to `tests/test_run_autocoder_batch.py` only

## Execution Boundary

This plan authorizes only the changes described above. Any other file changes require a new task and new review.
```

### 4c. `safety_checklist.md`

Human-readable pre-flight safety checks before a human approves handing this plan to Claude Code.

```markdown
# Safety Checklist — rgr-314-task-id-path-traversal

## Pre-Execution Safety Checks

| Check | Result | Notes |
|---|---|---|
| Task ID is safe path component | ✅ | No `/`, `\`, or `..` |
| allowed_files are relative paths | ✅ | No absolute paths |
| allowed_files exist at current main | ✅ | All three files present |
| forbidden_files unchanged vs main | ✅ | No changes detected |
| safety_notes contain no forbidden patterns | ✅ | No live_claude, --enable-real-claude-executor, gh pr merge, git push, etc. |
| Stop condition: current head has no blocking finding | ✅ | HEAD is clean |
| Stop condition: CI green | ✅ | All checks pass |
| Stop condition: PMG clean (pre-execution) | ✅ | No Hermes mutations detected |
| Stop condition: final_gate_status READY_TO_MERGE | ✅ | All gates green |

## Execution Boundaries

- **May modify:** `tests/test_run_autocoder_batch.py`
- **Must not modify:** Any file not in `allowed_files`
- **Must not invoke:** Live Claude, autocoder batch controller, git push/merge, GitHub API mutation
- **Must not write to:** `.hermes/`, `skills/`, `memory/`, `profiles/`

## Post-Execution Required Checks

After the repair is executed (in a future `live-repair` pass):

1. Run `pytest tests/test_run_autocoder_batch.py::test_task_id_sanitization_rejects_path_traversal -v` — must pass
2. Run `pytest tests/test_run_autocoder_batch.py -q` — no regressions
3. Run `python3 scripts/local/check_pmg.py --snapshot` — PMG must be clean (no Hermes mutations)
4. Review `git diff` — only `tests/test_run_autocoder_batch.py` changed
5. Open a draft PR for human review before merge
```

### 4d. `suggested_tests.md`

Documents what test patterns should be used, based on the corpus `action` block.

```markdown
# Suggested Test — rgr-314-task-id-path-traversal

## Test Function Name

`test_task_id_sanitization_rejects_path_traversal`

## Suggested Pattern

Use `pytest.mark.parametrize` to test both positive and negative cases:

```python
import pytest

# Import the production batch controller module to exercise its actual validation
import sys
sys.path.insert(0, str(REPO_ROOT / "scripts" / "local"))
import run_autocoder_batch as batch_module

@pytest.mark.parametrize("task_id,expected_valid", [
    # Valid task IDs
    ("rgr-314-task-id-path-traversal", True),
    ("abc123", True),
    ("task_with.dots_and_underscores", True),
    # Invalid — path traversal
    ("../etc/passwd", False),
    ("foo/../../../bar", False),
    ("foo\\windows\\system32", False),
    # Invalid — too long
    ("a" * 200, False),
    # Note: "foo..bar" IS valid per production regex [A-Za-z0-9][A-Za-z0-9._-]{0,127}
    # because '.' is in the character class [A-Za-z0-9._-]; it is not a path traversal.
])
def test_task_id_sanitization_rejects_path_traversal(task_id, expected_valid):
    # Call the production batch controller's inline validation (run_autocoder_batch.py ~line 377)
    # by building a minimal batch packet and passing it through the controller's
    # validate_task_constraints path. The exact interface (function vs. subprocess call)
    # should be determined by inspecting the current main HEAD of run_autocoder_batch.py.
    valid, _reason = batch_module._validate_task_id_safety(task_id)
    assert valid == expected_valid, (
        f"task_id {task_id!r}: production validation returned {valid}, "
        f"expected {expected_valid}"
    )
```

## Notes

- The test must exercise the **production** validation function from `run_autocoder_batch.py`,
  not a local copy of the regex. If the production regex is removed, the test must fail.
- The function name `_validate_task_id_safety` (or equivalent) should be confirmed by
  inspecting the current main HEAD: `git show main:scripts/local/run_autocoder_batch.py | grep -A5 'fullmatch'`
- If no separate function exists (validation is inline), the test should call the batch
  controller entry point with a task packet containing the test task_id and assert rejection.
- The `foo..bar` case is intentionally absent — it is valid per the production regex
  `[A-Za-z0-9][A-Za-z0-9._-]{0,127}` since `.` is in the character class, and adding it
  as `("foo..bar", True)` would be redundant with the existing `task_with.dots_and_underscores`
  positive case.
```

### 4e. `stop_conditions.md`

Documents which stop conditions pass, fail, or are not applicable for this task.

```markdown
# Stop Conditions — rgr-314-task-id-path-traversal

## Stop Condition Status

| # | Stop Condition | Status | Detail |
|---|---|---|---|
| 1 | current-head P0/P1/P2 review finding | ✅ PASS | No active blocking finding on HEAD `6a0f77f` |
| 2 | unresolved stale P0/P1/P2 | ✅ PASS | No stale findings unresolved |
| 3 | REVIEW_COMMENTS_BLOCKED | ✅ PASS | `check_pr_review_comments.py` returns CLEAN |
| 4 | REVIEW_COMMENTS_INCONCLUSIVE | ✅ PASS | Status is conclusive |
| 5 | CI not green | ✅ PASS | All checks green at HEAD |
| 6 | PMG dirty (pre-execution) | ✅ PASS | Snapshot clean before plan generation |
| 7 | final_gate_status not READY_TO_MERGE | ✅ PASS | All gates green |
| 8 | verify_merge_command invalid | ✅ PASS | Merge command structure valid |
| 9 | changed files outside allowed_files | N/A | No execution in plan-only mode |
| 10 | task requests GitHub thread resolution | ✅ PASS | No such request in task safety_notes |
| 11 | task requests live Claude | ✅ PASS | No such request in task safety_notes |
| 12 | task attempts Hermes mutation | ✅ PASS | No such request in task safety_notes |

## Conclusion

All applicable stop conditions pass. This task is eligible for `live-repair` execution after human review of this plan.
```

---

## 5. Validation Rules (repair-plan-only)

In addition to all v0 hard stops, `repair-plan-only` adds:

### 5.1 Task Existence
`--task-id` must match a `task_id` in the corpus. Exit 1 if not found.

### 5.2 Wave Execution Mode Gate
If the task's wave has `execution_mode: "mocked"`, the loop exits 1 with:
```
repair-plan-only: wave {N} has execution_mode="mocked". 
Generate a new wave corpus with execution_mode="repair-plan" to use repair-plan-only.
```

This prevents accidentally generating repair plans for waves that are not yet approved for any execution.

### 5.3 Output Root Isolation
The output root must not overlap with any known protected path. The loop exits 1 if `output_root` resolves to a path inside `.hermes/`, `skills/`, `memory/`, or `profiles/`.

---

## 6. Execution Boundaries (repair-plan-only)

`repair-plan-only` generates artifacts **only**. It does not:

- Execute any repair
- Run Claude Code
- Modify any file in the repo
- Create commits or branches
- Call any GitHub API
- Invoke the autocoder batch controller
- Write to Hermes memory, profiles, or config

The generated `repair_prompt.md` is the only artifact that would be handed to a human or automated tool for execution review.

---

## 7. Claude Code Handoff Model

The `repair_prompt.md` is designed to be passed directly to Claude Code:

```bash
claude --print --input repair_prompt.md
# OR for interactive:
claude < repair_prompt.md
```

### Handoff Contract

When a human hands `repair_prompt.md` to Claude Code (or any equivalent tool):

1. **Scope is explicit** — only the files and changes described in the prompt are authorized
2. **Safety requirements are listed** — Claude Code can read the safety requirements and self-enforce where possible
3. **Success criteria are concrete** — a test name and pattern is suggested; passing that test is the success criterion
4. **Forbidden files are listed** — Claude Code should refuse to edit any forbidden file

### Human Review Contract

After Claude Code produces output:

1. Human reviews `git diff` — must be within `allowed_files` scope
2. Human runs `pytest tests/test_run_autocoder_batch.py -q` — no regressions
3. Human runs `PMG` snapshot check — no Hermes mutations
4. Human opens a draft PR for final review
5. Only after all gates pass does merge happen

---

## 8. Required Gates After Any Repair PR

Any PR that results from a repair-plan execution must pass all of:

| Gate | Tool/Script | Pass Condition |
|---|---|---|
| Review-comment clean | `check_pr_review_comments.py` | `blocked == 0` |
| Tests pass | `pytest tests/test_run_autocoder_batch.py -q` | exit 0 |
| No Hermes mutations | PMG snapshot compare | `memory_or_profile_updated: false` |
| Final gate green | `final_gate_status.py` | `READY_TO_MERGE` |
| Only allowed files changed | `git diff --name-only` | all paths in task `allowed_files` |
| Draft PR for human review | Manual | Human approves before merge |

---

## 9. Stop Conditions (repair-plan-only generation)

Same as v0 hard stops (enforced before generating any artifact):

1. `corpus_kind` is not `aed.codex_remediation.corpus.v0`
2. `execution_mode` for the task's wave is not `"repair-plan"` or `"mocked"`
3. `task_id` contains `/`, `\`, or `..`
4. `allowed_file` is absolute
5. `allowed_file` does not exist at current main (unless declared `new_file`)
6. Any `forbidden_file` has changed vs current main
7. `safety_notes` contains any forbidden pattern
8. `--output-root` is null/empty or inside a protected path

Additional for `repair-plan-only`:

9. `--task-id` does not exist in corpus tasks
10. Wave `execution_mode` is `"mocked"` (must be `"repair-plan"` for generation)

---

## 10. Future Phases

### Phase 1: repair-plan-only (this design)
- Generate artifact set for one task
- No execution
- Human reviews artifacts
- Human approves/rejects

### Phase 2: live-repair (requires separate approval)
- Execute one task per invocation
- Claude Code invoked with `repair_prompt.md`
- Strict file-scope enforcement
- PMG snapshot before and after
- Human review before merge
- One task per PR

### Phase 3: wave-execute (requires separate approval)
- Execute all tasks in an approved wave
- Wave must have `execution_mode: "live-repair"`
- Human signs off wave plan before execution
- All Phase 2 gates per task
- Wave-level PMG clean required

### Phase 4: live-full (requires separate approval)
- Full autonomous loop
- All prior phase gates
- Explicit `--allow-live-mode` flag
- PMG clean pre- and post-merge
- All stop conditions enforced as execution gates

---

## 11. Relationship to Prior Work

| PR | Content | Mode |
|---|---|---|
| #328 | Mock loop v0, `mock-plan-only` | mock |
| #329 | Wave 2 corpus, runner fixes | mock |
| This design | `repair-plan-only` design | design-only |

`repair-plan-only` does not modify `run_codex_remediation_loop.py` (implementation is out of scope for this PR). The design exists so that human review can assess whether the proposed execution model is safe and appropriate before any implementation begins.

---

## 12. Open Questions for Human Review

1. Is `repair-plan-only` the right next step, or should a full `live-repair` be designed instead?
2. Should the generated `repair_prompt.md` include the actual `re.fullmatch` pattern copied from current main HEAD, or just a reference to copy it?
3. Should there be a CI-gated "repair plan check" that validates `repair_prompt.md` structure before the plan is considered complete?
4. Should `stop_condition_checks` in `task_context.json` be enforced as hard stops (exit 1) or just documented?
5. Should the wave-level `execution_mode` be `repair-plan` (new) or should we reuse an existing value?
