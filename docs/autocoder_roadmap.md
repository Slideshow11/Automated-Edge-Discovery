# Autocoder Roadmap

**Date:** 2026-05-24
**Based on:** main after PR #315 (672b22d)
**Classification:** Engineering planning document

---

## Theme

The AED autocoder stack has reached v0 maturity: the scaffold works, the safety model is coherent, and the human-in-the-loop boundary is enforced. The focus of the next phase is **evaluation capability** — making the autocoder useful rather than just operational — combined with targeted safety hardening before live Claude is ever considered.

---

## Now (Next PR)

### 1. Fix trusted-script / --repo-root issue (PR #316 candidate)

**Issue:** PR #314's script-path fix (using `task_worktree_path / "scripts/local/run_autocoder_single_task.py"`) means the batch controller executes `run_autocoder_single_task.py` from the task worktree at `base_sha`. If `base_sha` points to an older or untrusted commit, the controller code in that commit runs — bypassing any safety fixes from the current reviewed branch.

**Fix options:**
- (**Recommended — Option A**) Add `--repo-root` parameter to `run_autocoder_single_task.py`. The batch controller passes `--repo-root <task_worktree_path>` explicitly, overriding the `__file__`-based auto-detection. This pins the controller to the reviewed copy from the batch controller's own checkout while still running the task from the worktree.
- (Option B) Always use `SINGLE_TASK_SCRIPT` (parent repo path) with `cwd=task_worktree_path` — but this was the OLD broken model (REPO_ROOT resolving to parent repo), so this would reintroduce `HOLD_MAIN_DIRTY`.
- (Option C) Add a `--trusted-script-sha` flag that validates the script SHA matches expectations before execution.

**Rationale:** Option A is the cleanest: an explicit `--repo-root` is always better than a `__file__`-derived default. It makes the boundary intentional.

**Scope of change:** Add `--repo-root` to `run_autocoder_single_task.py` only. Update batch controller's argv construction. No other stage tools need this.

### 2. Stale worktree cleanup helper

**Issue:** The repo has 70+ orphaned worktrees accumulated over test runs. No automated cleanup.

**Fix:** Add a simple `--cleanup-stale-worktrees` flag to the batch controller or a standalone `scripts/local/cleanup_worktrees.py` that:
- Lists all worktrees via `git worktree list`
- Removes worktrees whose paths are under known test output roots (`/tmp/aed_runs/`, `/tmp/pytest-*`)
- Removes any detached-HEAD worktrees not from the current batch run
- Never removes the main repo worktree.

**Not a safety issue** but improves operator ergonomics significantly.

---

## Next (Next 2–3 PRs)

### 3. Task corpus and evaluator

**What:** Build a library of known-good task packets (pairs of input packets + expected `final_status.json` outcomes) alongside a simple evaluator that runs a task packet and scores the observed result against expectations.

**Why:** The autocoder currently has no way to measure improvement over time except "did tests pass". A task corpus would enable:
- Regression detection when a stage tool changes
- Smoke suite completeness measurement
- "Known bug" fixture testing

**Files to create:**
```
tests/corpus/
  task_001_append_docs.json    # input packet
  task_001_expected.json       # expected final_status
  task_002_forbidden_file.json
  ...
runner_corpus.py               # runs corpus and reports diff vs. expected
```

**Priority:** This should come before more controller features. The system right now is a scaffold without a measuring instrument.

### 4. HOLD diagnosis guide PR

**What:** Expand `docs/autocoder_engineering_handbook.md` section 12 with per-HOLD diagnosis trees. Most HOLD states have exactly one root cause — make it findable.

**Why:** When a batch operator hits `HOLD_APPLY_NOT_READY`, they should be able to look up "HOLD_APPLY_NOT_READY → cause: diff.patch contains a file not in allowed_files → fix: check result.json changed_files against allowed_files".

### 5. Batch status UX improvements

**What:** The `batch_status.json` is functional but minimal. Add:
- `task_errors` list (diagnostic strings per failed task)
- `task_warnings` list (non-fatal warnings)
- `batch_duration_seconds` (wall-clock time)
- `task_worktree_urls` (if any worktrees have a known remote path)

This doesn't change behavior but makes the human review phase faster.

---

## Later (Before Live Claude)

### 6. Live Claude through single-task controller only

**Conditions required before considering:**
- `--repo-root` parameterization is done and tested
- `enable_real_claude_executor` flag is removed from `run_temp_worktree_execution.py` (use explicit `--mode claude` instead)
- Command contract validation is enforced (no `shell=True`, no direct `pip`/`curl` calls)
- Timeout policy is defined (per-command and per-run)
- Failure taxonomy is expanded (specific HOLD for timeout, non-zero exit, empty output)
- PMG is re-run after live execution and must be clean

**Even then:** Live Claude only through the single-task controller. Batch live-Claude is out until single-task live path is proven.

### 7. Batch v1: stop_on_first_hold=false

**What:** Add full aggregation mode where all tasks are attempted regardless of early failures, and batch result reflects the worst outcome with per-task diagnostics.

**Why:** `stop_on_first_hold=true` is correct for v0 smoke, but real batch runs should attempt all tasks and let the human review a complete status report.

### 8. Command contract allowlist

**What:** `run_temp_worktree_execution.py` has `build_claude_command_contract()` that enumerates the safe commands for live Claude execution. Currently it's advisory. Before live Claude, make it enforceable: reject any live Claude command whose argv is not in the allowlist.

### 9. Optional parallelism research

**What:** Investigate whether task worktrees could safely run in parallel without:
- File lock conflicts between worktrees
- Shared Hermes state pollution
- Artifact path collisions

**Do not implement yet.** Research only.

---

## Never Without Separate Design

These are not engineering tasks — they are product/governance decisions requiring explicit design and approval before any work begins:

- **Autonomous push** — any tool that auto-runs `git push`
- **Autonomous PR creation** — any tool that runs `gh pr create` without human action
- **Autonomous merge** — any tool that runs `gh pr merge`
- **Production board mutation** — updating external systems based on autocoder output
- **Hermes memory writes** — autocoder writing to Hermes persistent memory
- **Live trading or production execution** — using autocoder output to drive production systems

---

## Proposed Next 5 PRs

The following is an ordered implementation plan. Exact PR numbers will be assigned at creation time based on current branch state.

| Title | Changes |
|-------|---------|
| **Next implementation PR:** fix: add --repo-root arg to single-task controller for trusted-script isolation | Add `--repo-root` to `run_autocoder_single_task.py`; update batch controller argv |
| **Following PR:** feat: clean stale git worktrees | Add cleanup tool + optional `--cleanup` flag |
| **Following PR:** feat: AED task corpus v0 | `tests/corpus/`, runner script, 5–10 fixture packets |
| **Following PR:** feat: batch status UX expansion | Add duration, errors, warnings to batch_status.json |
| **Following PR:** docs: expand HOLD diagnosis handbook supplement | Per-HOLD diagnosis trees |

---

## Drift Classification

**STRONG_BUT_NEEDS_EVAL_LAYER**

The autocoder architecture is sound: fail-closed pipeline, worktree isolation, PMG guard, explicit human review boundary. No fundamental design errors. The safety model is more conservative than most production coding agents in 2025 — which is correct for an unproven system.

**The gap:** We have no way to measure whether the autocoder is getting better or worse across PRs. 191 tests pass but they're testing the scaffold, not the output quality. A task corpus or evaluation harness is the single most important missing capability before more controller features are added.

**Risk:** Building more controller features (batch v1 parallelism, live Claude) before having an evaluation layer is building on a foundation without a measuring instrument. Feature velocity without evaluation feedback leads to overfit/gaming.

**Action:** PR #316 → PR #318 (fix root issue, then build evaluation layer) before advancing batch features or live Claude.
