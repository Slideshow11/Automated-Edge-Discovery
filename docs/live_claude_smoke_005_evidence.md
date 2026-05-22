# Live Claude Smoke 005 Evidence and Apply Boundary

**Run ID:** first_live_claude_smoke_005
**Date:** 2026-05-22
**Main HEAD before run:** a1e8bec02e63e2e20efb511ab3fa973f8327703f
**Status:** PATCH_READY_FOR_HUMAN_REVIEW ✅

---

## 1. Smoke 005 Purpose

Validate the guarded live Claude temp-worktree executor after the following PRs were merged to main:

| PR | SHA | Description |
|-----|-----|-------------|
| #291 | 4f8dd30 | Detect untracked temp worktree files |
| #292 | 79634d1 | Repair audit test import on clean checkout |
| #293 | a1e8bec | Dedupe temp worktree changed files |

Specific validation goals:
- Confirmed isolated temp-worktree execution produces a durable human-review diff artifact
- Confirmed allowed untracked file detection works (docs/live_smoke_scratch.md)
- Confirmed changed_files deduplication works (single entry, no duplicate)
- Confirmed PMG stays clean through a live Claude invocation
- Confirmed main checkout remains clean before and after

---

## 2. Exact Smoke 005 Result

| Field | Value |
|-------|-------|
| Packet path | `/tmp/aed_runs/first_live_claude_smoke_005/execution_packet.json` |
| Output root | `/tmp/aed_runs/first_live_claude_smoke_005` |
| Worktree path | `/tmp/aed_runs/worktrees/first_live_claude_smoke_005` |
| Base SHA | a1e8bec02e63e2e20efb511ab3fa973f8327703f |
| Plan SHA256 | 10e5ede305e5ccf5bd43820ce38da30fc7e5b22d2aed7ec9977a1b1cafcba7e2 |

**Command contract (from build_claude_command_contract):**
```
argv = ["claude", "--print", "--input-format=text", "--output-format=text", "--permission-mode", "acceptEdits"]
cwd = /tmp/aed_runs/worktrees/first_live_claude_smoke_005
timeout_seconds = 60
input_mode = stdin (plan passed via worktree/.aed_plan.md)
shell = False
```

**Harness result:**

| Field | Value |
|-------|-------|
| Status | PATCH_READY_FOR_HUMAN_REVIEW |
| changed_files | ["docs/live_smoke_scratch.md"] (single entry, no duplicate ✅) |
| diff.patch path | `/tmp/aed_runs/first_live_claude_smoke_005/diff.patch` |
| diff.patch size | 198 bytes |
| diff.patch contains docs/live_smoke_scratch.md | Yes ✅ |
| diff.patch excludes .aed_plan.md | Yes ✅ |
| claude_exit_code | 0 |
| claude_elapsed_seconds | 7.23 |
| PMG status | clean |
| PMG blocked files | 0 |
| main_git_status_before | clean |
| main_git_status_after | clean |
| validation_errors | [] |
| patch_ready | true |

**Audit result:**

| Field | Value |
|-------|-------|
| audit status | CLAUDE_INVOCATION_DETECTED |
| real_claude_invoked | true |
| run_kind | claude |
| pmg_status | clean |
| diff_size | 198 |
| packet_execution_mode | claude |

---

## 3. Safety Properties Confirmed

The following safety properties were confirmed by smoke 005:

1. **Live Claude only ran under explicit --enable-real-claude-executor flag.**
   The harness blocks `execution.mode="claude"` without this flag.

2. **Work happened in isolated temp worktree.**
   Worktree at `/tmp/aed_runs/worktrees/first_live_claude_smoke_005`; main checkout at `a1e8bec` was never modified.

3. **Main checkout remained clean.**
   `git status --short` was empty before and after the smoke.

4. **diff.patch excluded .aed_plan.md.**
   The internal harness-managed plan file was correctly filtered from the diff artifact.

5. **PMG stayed clean.**
   `pmg_compare.json` reported `status=clean`, `blocked=0`.

6. **No side-effects occurred.**
   No dispatch, board touch, Hermes mutation, audit append, memory/profile update, package install, PR creation, or merge occurred.

---

## 4. Boundary That Remains

### The Guard Is Still Required

`PATCH_READY_FOR_HUMAN_REVIEW` is **not** permission to apply the patch to main.

The status means the harness has produced a validated diff artifact that passes all safety checks inside the isolated worktree. It does not mean the diff is ready to be applied to the main repository. A human must still:

1. Inspect the diff artifact at `/tmp/aed_runs/first_live_claude_smoke_005/diff.patch`
2. Verify it contains only intended changes
3. Verify `changed_files` is within `allowed_files`
4. Verify `forbidden_files` were not touched
5. Manually apply the diff to main if approved

### Requirements for Any Future Apply Step

Before applying any temp-worktree diff.patch to main, the following must ALL be true:

| Requirement | Verification |
|-------------|-------------|
| Exact result.json path is known and unchanged | Compare SHA256 of result.json |
| diff.patch is non-empty and contains only allowed files | Inspect diff.patch content |
| .aed_plan.md excluded from diff.patch | grep diff.patch for .aed_plan.md |
| changed_files within allowed_files | Compare changed_files to allowed_files list |
| forbidden_files not touched | Compare forbidden_files list against worktree diff |
| PMG clean before apply | Check pmg_compare.json status=clean |
| PMG clean after apply | Re-run PMG snapshot on main checkout |
| main checkout clean before apply | git status --short returns empty |
| No live Claude invocation during apply | Audit log shows no CLAUDE_INVOCATION_DETECTED during apply |
| Post-apply tests pass | Run pytest test suite on main after apply |
| PR review and normal guarded merge | Normal PR workflow, not automated apply |

### Do NOT Remove the Guard

- Do NOT remove `--enable-real-claude-executor` from the harness
- Do NOT make `execution.mode="claude"` the default
- Do NOT add automatic patch application
- Do NOT create any dispatch path that applies diff.patch to main without explicit human approval

---

## 5. Recommendation for Next Step

The next implementation step should be a human-reviewed apply-readiness verifier that:

1. Takes the execution packet path, result.json path, and diff.patch path as inputs
2. Verifies all requirements in the table above (Section 4)
3. Produces a human-readable report of pass/fail for each requirement
4. Does NOT automatically apply the patch
5. Does NOT invoke live Claude
6. Does NOT merge or dispatch

This verifier should be committed to `scripts/local/` so it can be run as part of the normal PR workflow before any apply step.

---

## 6. Smoke History Summary

| Smoke | Status | Notes |
|-------|--------|-------|
| smoke 003 | not run | Design phase |
| smoke 004 | HOLD_TOO_MANY_FILES_CHANGED (duplicate detection bug) | Fixed by PR #293 |
| smoke 005 | PATCH_READY_FOR_HUMAN_REVIEW ✅ | Post-PR-293 validation |