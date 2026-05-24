# PR #314 Gate Process Gap Record

**Date:** 2026-05-24
**Classification:** PROCESS_GAP_ONLY
**PR:** #314 — feat: add mock batch autocoder controller

---

## 1. Summary

PR #314 merged safely. All checks passed. Post-merge verification succeeded. However, the explicit final-gate commands (`final_gate_status.py` and `verify_final_head_merge_command.py`) were **not run as standalone pre-merge authorization steps** before `gh pr merge`.

This docs-only PR records the gap and restores the canonical explicit gate checklist for future PRs.

---

## 2. Classification

**PROCESS_GAP_ONLY**

PR #314 code is safe. The gap is purely process-level: the human/agent merged without running the documented pre-merge gate commands. No runtime behavior was affected. No code changes are made by this record.

---

## 3. What Happened

- PR #314 implemented a mock-only sequential batch autocoder controller with per-task git worktree isolation.
- Four commits on branch `feat/autocoder-batch-controller-v0`:

| SHA | Message |
|-----|---------|
| `a244de9` | fix: isolate batch autocoder task runs in per-task worktrees |
| `c18caa5` | fix: derive suggested_pr_title/pr_body from goal in batch task normalization |
| `5647295` | fix: invoke task worktree script path for correct REPO_ROOT isolation |
| `226c2df` | squash-merged into main |

- Feature-branch smoke reached `BATCH_READY_FOR_HUMAN_REVIEW` on `5647295`.
- CI was green on the feature branch.
- Two Codex reviews completed (safety/correctness + test quality/smoke evidence).
- Post-merge verification on main passed (50 + 141 tests).
- Post-merge smoke on main reached `BATCH_READY_FOR_HUMAN_REVIEW`.
- No live Claude was run.
- No `--enable-real-claude-executor` was used.
- No Hermes memory/profile was touched.
- No git push, PR creation, merge, commit, staging, or `git add` from any controller.

---

## 4. What Did Not Happen

- Gate scripts were **not missing**.
- PMG was **not absent**.
- Code was **not found unsafe**.
- No behavioral constraint was violated.
- No live Claude was run.
- No `--enable-real-claude-executor` was used.
- No Hermes memory/profile was touched.

---

## 5. Canonical Gate Scripts

These scripts are AED infrastructure and were present during PR #314:

| Script | Path | First commit |
|--------|------|-------------|
| `final_gate_status.py` | `scripts/local/final_gate_status.py` | `ca8c469` (#269) |
| `verify_final_head_merge_command.py` | `scripts/local/verify_final_head_merge_command.py` | `854f7e8` (#228) |
| `check_persistent_mutation_guard.py` | `scripts/local/check_persistent_mutation_guard.py` | pre-exists main |

All three scripts exist in the repo. They were not invoked as explicit pre-merge steps.

---

## 6. Evidence

| Item | Value |
|------|-------|
| PR head SHA | `564729540472d9491c566a17653d1938f2e744fc` |
| PR #314 merge SHA | `226c2df0983311a0ec8387c21d3d3b1c93063a60` |
| Post-merge smoke path | `/tmp/aed_runs/autocoder_batch_v0_post314/` |
| Post-merge smoke status | `BATCH_READY_FOR_HUMAN_REVIEW` |
| Post-merge smoke task count | 2 / 2 completed |
| Post-merge tests | 191 / 191 passed |
| CI | green |
| PMG | `final_gate_status.py` checks `pmg_clean: true` via governance-validators CI; post-merge PMG compare also clean (`status=clean, blocked=0`) |
| Codex reviews | safety/correctness + test quality/smoke evidence — both completed |
| Live Claude | none |
| `--enable-real-claude-executor` | none |
| Hermes memory/profile | untouched |

---

## 7. Future Required Explicit Pre-Merge Sequence

For every AED PR (including docs-only), run these steps **before `gh pr merge`**:

### Step 1 — PMG compare

Generate a PMG compare result before merging. This compares current Hermes state against a baseline snapshot:

```bash
# Option A — if you have a prior baseline snapshot:
python3 scripts/local/check_persistent_mutation_guard.py compare \
  --root ~/.hermes \
  --before /tmp/pmg_baseline.json \
  --output-json /tmp/pmg_compare_pr<PR_NUMBER>.json \
  --output-md /tmp/pmg_compare_pr<PR_NUMBER>.md

# Option B — fresh baseline from current clean state:
python3 scripts/local/check_persistent_mutation_guard.py snapshot \
  --root ~/.hermes \
  --output /tmp/pmg_baseline.json
python3 scripts/local/check_persistent_mutation_guard.py compare \
  --root ~/.hermes \
  --before /tmp/pmg_baseline.json \
  --output-json /tmp/pmg_compare_pr<PR_NUMBER>.json \
  --output-md /tmp/pmg_compare_pr<PR_NUMBER>.md
```

**Note:** Use the `compare` subcommand (not `snapshot`) — `final_gate_status.py` and `verify_final_head_merge_command.py` require PMG compare output with `status=clean`, not snapshot output.

### Step 2 — Final gate status

```bash
python3 scripts/local/final_gate_status.py \
  --pr-number <PR_NUMBER> \
  --reported-head-sha <HEAD_SHA> \
  --codex-reviewed-sha <HEAD_SHA> \
  --pmg-guard-state-json /tmp/pmg_compare_pr<PR_NUMBER>.json
```

**Expected output:** `status: READY_TO_MERGE`

If the output is `HOLD_*`, resolve the blocker before proceeding.

### Step 3 — Merge command verifier

```bash
python3 scripts/local/verify_final_head_merge_command.py \
  --pr-number <PR_NUMBER> \
  --require-pmg \
  --pmg-guard-state-json /tmp/pmg_compare_pr<PR_NUMBER>.json \
  --reported-head-sha <HEAD_SHA>
```

**Expected output:** `MERGE_READY_CANDIDATE` with an `authorization_phrase`

If the output is `HOLD_*`, resolve the blocker before proceeding.

### Step 4 — Merge

Only after steps 2 and 3 return the expected ready statuses:

```bash
gh pr merge <PR_NUMBER> --squash --delete-branch --match-head-commit <HEAD_SHA>
```

**Note:** `--match-head-commit` is a `gh pr merge` argument, not a verifier input. Use `--reported-head-sha` to feed the head SHA to the verifier script.

---

## 8. Remediation Status

This PR closes the process gap by recording it. It does **not** change any runtime behavior, controller logic, PMG configuration, gate code, Hermes configs, memory/profile files, or smoke artifacts. It is purely a governance record.

---

## 9. Safety Boundary

This docs-only PR must **not**:
- Alter controller behavior (`scripts/local/run_autocoder_batch.py`)
- Change PMG behavior (`scripts/local/check_persistent_mutation_guard.py`)
- Modify gate code (`scripts/local/final_gate_status.py`, `scripts/local/verify_final_head_merge_command.py`)
- Touch Hermes configs, skills, memory, or profile files
- Add or modify test code
- Change smoke artifacts or output paths
- Apply patches to main branch
- Run `git apply --check` with weakened validation
- Dispatch, touch boards, or update external audit logs

---

## 11. PR #317 Post-Merge Gate Process Gap (PROCESS_GAP_ONLY)

**Classification:** PROCESS_GAP_ONLY — no runtime remediation required.

**What happened:**
PR #317 (fix: add --repo-root to single-task controller for trusted-script isolation) was squash-merged to main at merge SHA `91bad0112068c0c075a0753ca05a22eb75b55d58` from PR head `3e85e936df88d092a85a3fd717bffb37ac117cdd`. The PR #317 final report incorrectly stated that `final_gate_status.py`, `verify_final_head_merge_command.py`, and PMG compare were "N/A or absent" — this was wrong. Those scripts exist and were required by this very governance document (Section 10, Cross-Reference).

The explicit final-gate commands documented in Section 5 (Step 2: `final_gate_status.py` + Step 3: `verify_final_head_merge_command.py` before `gh pr merge`) were **skipped** before PR #317's merge. The gate scripts were not run and the process gap was not reported.

**Post-merge structural validation (all passed):**
- CI: all checks green (governance-validators × 2, pr-gate-live-smoke × 2, test × 2, validator × 2)
- Focused test suite: 302/302 passed
- Feature-branch smoke: `BATCH_READY_FOR_HUMAN_REVIEW`, both tasks `SINGLE_TASK_READY_FOR_HUMAN_REVIEW`, no `HOLD_MAIN_DIRTY`
- Codex reviews: clean (both non-blocking findings documented below)
- Post-merge `final_gate_status.py` re-run: all structural checks pass (`head_matches: True`, `ci_green: True`, `codex_exact_head: True`, `pmg_clean: True`, `git_status_clean: True`) — only `pr_open` fails because PR is already merged (expected)
- PMG compare: clean

**Codex non-blocking findings:**
1. Docs conflict: `docs/autocoder_batch_controller_design.md` sections around line 243 and 293 still describe the pre-PR #317 model where `run_autocoder_single_task.py` is invoked from the task worktree and `REPO_ROOT` derives from `__file__`. The new PR #317 section (line 325+) correctly documents the `--repo-root` trusted-script model, creating a conflict. Non-blocking — the new section is authoritative.
2. Test-strength gap: `TestScriptSource.test_batch_invokes_parent_script_with_repo_root` checks `--repo-root` is present and not the script path, but does not assert the value equals the task worktree path. Implementation is correct; test could be stronger.

**No runtime remediation required.** The PR #317 code is safe — no live Claude, no `--enable-real-claude-executor`, no `shell=True`, no push/PR/merge/commit/stage/git-add behavior, no Hermes memory/profile touched, all tests pass.

**This PR (PR remediation) addresses:**
- Records the process gap formally
- Fixes the docs conflict in `docs/autocoder_batch_controller_design.md` (lines 243, 293)
- Strengthens `TestScriptSource` to assert `--repo-root` value equals task worktree path
- Removes accidental untracked noise files (`docs/test.md`, `test_apply_output.json`)

**Future requirement:** All AED PRs must run the explicit gate sequence (`final_gate_status.py` → `verify_final_head_merge_command.py`) before `gh pr merge`. The PR #315 / PR #317 process gap is not a license to skip gates for future PRs.

---

## 12. Cross-Reference

- PR #314: https://github.com/Slideshow11/Automated-Edge-Discovery/pull/314
- PR #315: https://github.com/Slideshow11/Automated-Edge-Discovery/pull/315 (records PR #314 gate process gap)
- PR #317: https://github.com/Slideshow11/Automated-Edge-Discovery/pull/317
- Batch controller design: `docs/autocoder_batch_controller_design.md`
- Gate infrastructure: `scripts/local/final_gate_status.py`, `scripts/local/verify_final_head_merge_command.py`, `scripts/local/check_persistent_mutation_guard.py`
