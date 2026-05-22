# Temp Worktree Human Apply Workflow

**Status:** Design Draft  
**Created:** 2026-05-22  
**Branch:** `docs/temp-worktree-human-apply-workflow`  
**Based on main HEAD:** `295d74bb9544b7d5f08acc9012feafb12ef24cac`

---

## 1. Purpose

This document specifies the design for a **human-approved apply workflow** for temp-worktree patch artifacts produced by the guarded Claude executor (smoke 005+).

The workflow begins after `verify_temp_worktree_apply_readiness.py` returns `APPLY_READY` for a given `result.json` + `diff.patch` pair. It produces a safe, auditable human-review step that does **not** automatically merge, push, open PRs, or dispatch anything.

---

## 2. Non-Goals

This design does **NOT** specify or implement any of the following:

- `git apply` or any patch application mechanism
- Automatic or semi-automatic PR creation from diff.patch artifacts
- Removal of `--enable-real-claude-executor` guard from the executor harness
- Making `execution.mode="claude"` the default
- Any dispatch path that auto-promotes `PATCH_READY_FOR_HUMAN_REVIEW` to a merge
- Board or Kanban integration
- Hermes skill mutations
- Audit log appends from within the apply tool
- Memory or profile updates from within the apply tool
- Package installation
- Any `shell=True` in command contracts
- Any live Claude invocation

---

## 3. Required Inputs

Before any future apply tool runs, it must receive all of the following as explicit arguments:

| Input | Description |
|-------|-------------|
| `--result-json` | Path to the execution `result.json` (from temp-worktree run) |
| `--diff-patch` | Path to the `diff.patch` file produced by the temp-worktree run |
| `--apply-readiness-json` | Output from `verify_temp_worktree_apply_readiness.py` confirming `APPLY_READY` |
| `--repo-root` | Path to the AED repository root |
| `--expected-head` | Expected main HEAD SHA (prevents apply if main advanced during review) |
| `--approval-token` | Explicit human approval phrase (e.g., `--approval-token="I approve applying this patch"`) |
| `--branch-name` | Optional: desired branch name for the reviewed patch (default: auto-generated) |

---

## 4. Required Pre-Apply Checks

The future apply tool must verify ALL of the following before emitting any apply preview or executing any apply step:

| # | Check | Failure State |
|---|-------|---------------|
| 1 | Repo `git status` is clean (no staged/unstaged changes) | `HOLD_REPO_DIRTY` |
| 2 | Current HEAD SHA matches `--expected-head` | `HOLD_EXPECTED_HEAD_MISMATCH` |
| 3 | `verify_temp_worktree_apply_readiness.py` returned `APPLY_READY` for this exact `result.json` + `diff.patch` pair | `HOLD_READINESS_NOT_APPLY_READY` |
| 4 | `apply-readiness-json` is valid JSON with `status: APPLY_READY` | `HOLD_READINESS_INVALID_JSON` |
| 5 | `changed_files` from `result.json` is non-empty | `HOLD_CHANGED_FILES_EMPTY` |
| 6 | `changed_files` contains no duplicates | `HOLD_CHANGED_FILES_DUPLICATE` |
| 7 | All `changed_files` are within `allowed_files` | `HOLD_OUTSIDE_ALLOWED_FILES` |
| 8 | No `changed_files` are in `forbidden_files` | `HOLD_FORBIDDEN_FILE_TOUCHED` |
| 9 | `changed_files` count ≤ `max_changed_files` | `HOLD_TOO_MANY_FILES_CHANGED` |
| 10 | `.aed_plan.md` is NOT in `changed_files` | `HOLD_AED_PLAN_INCLUDED` |
| 11 | `.aed_plan.md` is NOT in `diff.patch` content | `HOLD_AED_PLAN_INCLUDED` |
| 12 | `diff.patch` is non-empty | `HOLD_DIFF_EMPTY` |
| 13 | `diff.patch` contains every path in `changed_files` | `HOLD_DIFF_MISSING_CHANGED_FILE` |
| 14 | `diff.patch` contains no `forbidden_files` paths | `HOLD_DIFF_CONTAINS_FORBIDDEN_FILE` |
| 15 | PMG status is clean | `HOLD_PMG_NOT_CLEAN` |
| 16 | Worktree path is outside the repo | `HOLD_WORKTREE_INSIDE_REPO` |
| 17 | `diff.patch` path is outside the repo | `HOLD_PATCH_PATH_INSIDE_REPO` |
| 18 | Command contract in `result.json` shows `shell=False` | `HOLD_COMMAND_CONTRACT_UNSAFE` |
| 19 | `approval-token` is present and non-empty | `HOLD_APPROVAL_MISSING` |
| 20 | `real_claude_invoked` is true in `result.json` (when `--require-real-claude` is passed) | `HOLD_CLAUDE_NOT_INVOKED` |
| 21 | No `git push`, `gh pr create`, `gh pr merge`, `dispatch`, `board`, Hermes, audit, memory, profile, or package-install strings in `result.json` command contract | `HOLD_COMMAND_CONTRACT_UNSAFE` |
| 22 | `result.json` has `claude_exit_code: 0` (when present) | `HOLD_CLAUDE_EXIT_NONZERO` |
| 23 | `verify_temp_worktree_apply_readiness.py` is re-run internally with the provided inputs as a sanity check | `HOLD_READINESS_INTERNAL_MISMATCH` |

---

## 5. Implementation Path: Option A (Read-Only Preview) First

### Why Option A First

Option A (read-only preview) is the safer starting point because:
- It never modifies the repo
- It produces an exact `git apply` command for human review
- It creates an audit trail without risk
- It can be extended to Option B later without changing the safety model

### Option A: `preview_temp_worktree_apply.py`

```
Purpose:
  Read-only preview tool that produces exact apply commands and checklists.
  Does NOT run git apply. Does NOT mutate the repo. Does NOT invoke Claude.

CLI:
  python3 scripts/local/preview_temp_worktree_apply.py \
    --result-json /tmp/aed_runs/first_live_claude_smoke_005/result.json \
    --diff-patch /tmp/aed_runs/first_live_claude_smoke_005/diff.patch \
    --apply-readiness-json /tmp/aed_runs/first_live_claude_smoke_005/apply_readiness.json \
    --repo-root /home/max/Automated-Edge-Discovery \
    --expected-head 295d74bb9544b7d5f08acc9012feafb12ef24cac \
    --approval-token "I approve applying this patch" \
    --output-json /tmp/apply_preview.json \
    --output-md /tmp/apply_preview.md
```

**Pre-flight:** Runs all 23 checks from Section 4. Fails at the first failure, returns `HOLD_*` state, writes no apply commands.

**On success (APPLY_PREVIEW_READY):**
1. Reads `diff.patch` and `result.json`
2. Computes the exact `git apply` command with `--check` preview
3. Computes the non-check apply command for reference
4. Writes a checklist to `--output-md` for human review
5. Sets `real_apply_allowed: false` in the output JSON

**Output JSON fields:**
```json
{
  "status": "APPLY_PREVIEW_READY",
  "preview": {
    "git_apply_check_command": "cd /home/max/Automated-Edge-Discovery && git apply --check /tmp/aed_runs/first_live_claude_smoke_005/diff.patch",
    "git_apply_command": "cd /home/max/Automated-Edge-Discovery && git apply /tmp/aed_runs/first_live_claude_smoke_005/diff.patch",
    "branch_name": "apply/first_live_claude_smoke_005-2026-05-22",
    "expected_head": "295d74bb9544b7d5f08acc9012feafb12ef24cac",
    "changed_files": ["docs/live_smoke_scratch.md"],
    "diff_size_bytes": 198
  },
  "pre_apply_checklist": [
    "Verify git status is clean before running git apply",
    "Verify HEAD is 295d74bb9544b7d5f08acc9012feafb12ef24cac",
    "Run: git apply --check /tmp/aed_runs/first_live_claude_smoke_005/diff.patch",
    "Review the diff at /tmp/aed_runs/first_live_claude_smoke_005/diff.patch",
    "Run: git apply /tmp/aed_runs/first_live_claude_smoke_005/diff.patch",
    "Run: pytest tests/ -q",
    "Run: python3 scripts/local/check_persistent_mutation_guard.py",
    "Push and open PR if all checks pass"
  ],
  "post_apply_checklist": [
    "Run pytest tests/ -q",
    "Run PMG snapshot",
    "Review git status",
    "Open PR via normal workflow"
  ],
  "real_apply_allowed": false,
  "warnings": [
    "This tool did NOT run git apply. Human must execute the apply command.",
    "Verify the diff contains only intended changes before applying.",
    "Do not apply if main has advanced from expected HEAD."
  ],
  "checks_passed": 23,
  "checks_failed": 0
}
```

**Markdown output** contains the full checklist, the exact commands, safety warnings, and confirmation that the tool did not apply the patch.

---

## 6. Option B: Gated Apply to Local Branch (Later, After Option A)

### Option B: `apply_temp_worktree_patch_to_branch.py` (Future)

Option B goes further than Option A: it creates a local branch and applies the patch to it, but **still does not push or open a PR**.

```
Purpose:
  Creates a local branch from expected HEAD, applies the patch, runs validations.
  Does NOT push. Does NOT open PRs. Does NOT merge. Does NOT invoke Claude.

CLI:
  python3 scripts/local/apply_temp_worktree_patch_to_branch.py \
    --result-json /tmp/aed_runs/first_live_claude_smoke_005/result.json \
    --diff-patch /tmp/aed_runs/first_live_claude_smoke_005/diff.patch \
    --apply-readiness-json /tmp/aed_runs/first_live_claude_smoke_005/apply_readiness.json \
    --repo-root /home/max/Automated-Edge-Discovery \
    --expected-head 295d74bb9544b7d5f08acc9012feafb12ef24cac \
    --approval-token "I approve applying this patch" \
    --branch-name apply/first_live_claude_smoke_005-2026-05-22 \
    --output-json /tmp/apply_result.json \
    --output-md /tmp/apply_result.md
```

**Additional requirements beyond Option A checks:**

| # | Requirement |
|---|-------------|
| B1 | Re-run `verify_temp_worktree_apply_readiness.py` internally before applying |
| B2 | Create a new branch via `git checkout -b <branch-name>` from expected HEAD |
| B3 | Run `git apply --check <diff.patch>` and fail with `HOLD_PATCH_APPLY_FAILED` if it returns non-zero |
| B4 | Run `git apply <diff.patch>` |
| B5 | Run `git status --short` and verify only `changed_files` appear as modified |
| B6 | Run `pytest tests/ -q` and fail with `HOLD_POST_APPLY_TESTS_FAILED` if non-zero |
| B7 | Run PMG snapshot and fail with `HOLD_PMG_NOT_CLEAN` if dirty |
| B8 | Run `final_gate_status.py` and require `READY_TO_MERGE` |
| B9 | Run `verify_final_head_merge_command.py` with `--require-pmg` |
| B10 | **Never** run `git push` |
| B11 | **Never** run `gh pr create` |
| B12 | **Never** run `gh pr merge` |
| B13 | Provide an explicit message saying the branch was created and validated locally, and human must push and open PR manually |

**Output JSON fields:**
```json
{
  "status": "APPLY_COMPLETE_LOCAL_BRANCH_READY",
  "branch_name": "apply/first_live_claude_smoke_005-2026-05-22",
  "applied_from_head": "295d74bb9544b7d5f08acc9012feafb12ef24cac",
  "changed_files": ["docs/live_smoke_scratch.md"],
  "local_tests_passed": true,
  "pmg_clean": true,
  "final_gate": "READY_TO_MERGE",
  "git_push_required": true,
  "gh_pr_create_required": true,
  "real_apply_allowed": false,
  "warnings": [
    "Branch created locally only. Human must push and open PR.",
    "Do not force-push to main.",
    "Verify PR review before merging."
  ]
}
```

---

## 7. Status Taxonomy

### Option A Statuses (`preview_temp_worktree_apply.py`)

| Status | Meaning |
|--------|---------|
| `APPLY_PREVIEW_READY` | All checks passed; preview written to output JSON/MD |
| `HOLD_EXPECTED_HEAD_MISMATCH` | Current HEAD does not match expected HEAD |
| `HOLD_REPO_DIRTY` | Repo has unstaged/staged changes |
| `HOLD_READINESS_NOT_APPLY_READY` | apply-readiness.json does not show APPLY_READY |
| `HOLD_READINESS_INVALID_JSON` | apply-readiness.json is not valid JSON |
| `HOLD_CHANGED_FILES_EMPTY` | changed_files is empty |
| `HOLD_CHANGED_FILES_DUPLICATE` | changed_files has duplicates |
| `HOLD_OUTSIDE_ALLOWED_FILES` | A changed file is not in allowed_files |
| `HOLD_FORBIDDEN_FILE_TOUCHED` | A changed file is in forbidden_files |
| `HOLD_TOO_MANY_FILES_CHANGED` | changed_files count > max_changed_files |
| `HOLD_AED_PLAN_INCLUDED` | .aed_plan.md in changed_files or diff.patch |
| `HOLD_DIFF_EMPTY` | diff.patch is empty |
| `HOLD_DIFF_MISSING_CHANGED_FILE` | diff.patch does not contain a changed file |
| `HOLD_DIFF_CONTAINS_FORBIDDEN_FILE` | diff.patch contains a forbidden file |
| `HOLD_PMG_NOT_CLEAN` | PMG is not clean |
| `HOLD_WORKTREE_INSIDE_REPO` | worktree path is inside the repo |
| `HOLD_PATCH_PATH_INSIDE_REPO` | diff.patch path is inside the repo |
| `HOLD_COMMAND_CONTRACT_UNSAFE` | shell=True or forbidden strings in command contract |
| `HOLD_APPROVAL_MISSING` | approval-token is missing or empty |
| `HOLD_CLAUDE_NOT_INVOKED` | real Claude was not invoked (when --require-real-claude) |
| `HOLD_CLAUDE_EXIT_NONZERO` | claude_exit_code is non-zero |
| `HOLD_READINESS_INTERNAL_MISMATCH` | internal re-run of apply-readiness verifier disagrees |
| `HOLD_UNKNOWN` | Unexpected error |

### Option B Additional Statuses

| Status | Meaning |
|--------|---------|
| `APPLY_COMPLETE_LOCAL_BRANCH_READY` | Patch applied to local branch, tests pass, PMG clean |
| `HOLD_PATCH_APPLY_FAILED` | `git apply --check` returned non-zero |
| `HOLD_POST_APPLY_TESTS_FAILED` | pytest returned non-zero after apply |
| `HOLD_BRANCH_CREATION_FAILED` | `git checkout -b` returned non-zero |

---

## 8. Safety Rule

### The Boundary

**`PATCH_READY_FOR_HUMAN_REVIEW` + `APPLY_READY` is still NOT permission for automated application.**

The combined state means:
1. The temp-worktree executor produced a validated diff artifact
2. The apply-readiness verifier confirmed all safety properties
3. A human must still explicitly review and apply the patch

**No tool, script, or automation may convert `APPLY_READY` into an automatic `git apply` without an explicit human approval token that is verified by the apply tool itself.**

---

## 9. Recommended Implementation Order

1. **Now (this PR):** Design doc only. No code.
2. **Next PR:** Implement `scripts/local/preview_temp_worktree_apply.py` (Option A, read-only).
3. **Next PR:** Add tests for `preview_temp_worktree_apply.py`.
4. **After Option A is validated:** Implement `apply_temp_worktree_patch_to_branch.py` (Option B).
5. **After Option B is validated:** Design the human-review-to-PR workflow.

---

## 10. Relationship to Existing Tools

| Tool | Role |
|------|------|
| `run_temp_worktree_execution.py` | Produces `PATCH_READY_FOR_HUMAN_REVIEW` artifacts |
| `verify_temp_worktree_apply_readiness.py` | Confirms diff is safe to apply (APPLY_READY / HOLD_*) |
| `preview_temp_worktree_apply.py` (proposed) | Read-only preview of apply commands and checklist |
| `apply_temp_worktree_patch_to_branch.py` (proposed) | Gated apply to local branch (Option B) |
| `final_gate_status.py` | Verifies PR is ready to merge |
| `verify_final_head_merge_command.py` | Verifies HEAD SHA for safe merge |

---

## 12. Process Gap Record (2026-05-22)

**Commit `3841710`** ("feat: gated temp-worktree patch apply-to-local-branch tool") implemented Option B and landed on `main` without a GitHub PR review due to a recovery workflow that caused GitHub to see zero diff between the feature branch and main.

The full process gap record, safety audit evidence, validation results, and forward guardrail are documented in:
**`docs/apply_tool_direct_main_process_gap_3841710.md`**

On merge of that record: `PROCESS_HOLD_REMEDIATION_REQUIRED` → `PROCESS_GAP_RECORDED_CODE_SAFE`.

---

## 13. Open Questions

1. Should `--approval-token` be a specific phrase or any non-empty string? (Recommended: any non-empty string, but documented minimum length.)
2. Should Option A also run `git apply --check` as part of the preview, so the preview itself confirms the patch is applicable? (Recommended: yes, run `git apply --check` and include the output.)
3. Should the apply preview tool create a local branch as an alternative to applying directly to main? (Option B addresses this; Option A does not.)
4. Should `verify_temp_worktree_apply_readiness.py` be re-run inside the apply tool automatically, or should the apply tool trust the provided `apply-readiness-json`? (Recommended: re-run internally as a sanity check, report discrepancy as `HOLD_READINESS_INTERNAL_MISMATCH`.)