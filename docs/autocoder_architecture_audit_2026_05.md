# AED Autocoder Architecture Audit

**Date:** 2026-05-24
**Auditor:** Hermes Agent
**Repository:** Automated-Edge-Discovery, main after PR #315 (`672b22d`)
**Branch:** feat/autocoder-batch-controller-v0 / main
**Status:** Audit complete — draft for review

---

## 1. Executive Verdict

**Is the autocoder going in the right direction?**

Yes, with two conditions. The scaffold is fundamentally sound — fail-closed pipeline, worktree isolation, PMG guard, human review boundary — but it is a scaffold without a measuring instrument. The risk is building more controller features before establishing whether those features produce useful output.

**Drift classification: STRONG_BUT_NEEDS_EVAL_LAYER**

---

## 2. Architecture Map

### Stack Layers (bottom to top)

```
Layer 0: REPO_ROOT self-location
  SCRIPT_DIR = Path(__file__).parent.resolve()
  REPO_ROOT  = SCRIPT_DIR.parent.parent.resolve()
  All scripts derive this from __file__, not from cwd or args
  
Layer 1: Stage Tools (stateless verification scripts)
  - verify_temp_worktree_apply_readiness.py  (diff safety)
  - preview_temp_worktree_apply.py            (apply preview)
  - verify_temp_worktree_applied_branch.py     (branch consistency)
  - preview_applied_branch_pr.py               (PR preview)
  - check_persistent_mutation_guard.py         (Hermes state)
  
Layer 2: Execution Engine
  - run_temp_worktree_execution.py  (worktree creation + mock executor)
  
Layer 3: Orchestrators
  - run_autocoder_single_task.py   (6-stage pipeline chain, stateless wrapper)
  - run_autocoder_batch.py         (multiple tasks via worktree-per-task)
  
Layer 4: Governance / Gates
  - final_gate_status.py            (PR readiness + PMG + CI + Codex)
  - verify_final_head_merge_command.py  (head SHA + authorization phrase)
```

### Data Flow

```
Task Packet (aed.autocoder.single_task.v0)
  └─► Single-Task Controller [run_autocoder_single_task.py]
        └─► Stage 1: build_execution_packet()
              └─► Stage 2: run_temp_worktree_execution.py
                    ├─► Temp worktree @ base_sha
                    ├─► Mock executor → diff.patch + result.json
                    └─► PMG compare ← pre/post snapshot
        └─► Stage 3: verify_temp_worktree_apply_readiness.py
              └─► apply_readiness.json (APPLY_READY / HOLD_*)
        └─► Stage 4: preview_temp_worktree_apply.py
              └─► apply_preview.json (APPLY_PREVIEW_READY / HOLD_*)
        └─► Stage 5: apply_temp_worktree_patch_to_branch.py
              └─► apply_to_branch.json (APPLY_TO_BRANCH_APPLIED / HOLD_*)
              └─► Creates apply/ branch with dirty local changes
        └─► Stage 6: verify_temp_worktree_applied_branch.py
              └─► applied_branch_verification.json (APPLIED_BRANCH_READY / HOLD_*)
        └─► Stage 7: preview_applied_branch_pr.py
              └─► pr_preview.json (PR_PREVIEW_READY / HOLD_*)
        └─► SINGLE_TASK_READY_FOR_HUMAN_REVIEW
  
Batch Controller [run_autocoder_batch.py]
  └─► For each task:
        ├─► create worktree @ batch_start_head
        ├─► invoke run_autocoder_single_task.py FROM WITHIN worktree
        ├─► aggregate final_status.json per task
        └─► BATCH_READY_FOR_HUMAN_REVIEW or HOLD_*
```

---

## 3. Best-Practice Comparison Matrix

| Area | Current AED | Best Practice | Match | Evidence |
|------|------------|--------------|-------|----------|
| **Task specification** | Strict JSON packet schema with allowed/forbidden files, max_changed_files | Structured packet with explicit scope constraints | Strong | packet_kind validation, allowed_files enforcement, mock_edits |
| **Prompt/context** | Controlled via task_packet.goal field | Minimize context leakage, narrow task focus | Strong | goal is narrow, no raw context injection |
| **Sandboxing** | Git worktree for each execution, main repo never touched | Separate working environment per task | Strong | worktree-per-task, main repo clean |
| **Validation loops** | 6 stage gates, each fail-closed with HOLD states | Multi-stage validation before mutation | Strong | Every stage has a distinct HOLD |
| **Worktree isolation** | Detached HEAD per task worktree | Separate branch/worktree per task | Strong | `git worktree add --detach` |
| **Diff capture** | diff.patch from worktree diff | Capture after execution | Partial | No signed/attested diff capture |
| **Command allowlist** | build_claude_command_contract() for future use | Explicit allowlist before live execution | Partial | Only built, not enforced in v0 |
| **Artifact preservation** | All stages write JSON+MD, worktrees kept for human review | Full artifact preservation | Strong | Every stage outputs, worktrees preserved |
| **Human-in-loop** | Stops at SINGLE_TASK_READY_FOR_HUMAN_REVIEW + PR preview as TEXT | Human approves before any mutation | Strong | No push/PR/merge/commit |
| **PMG governance** | PMG compare post-execution, required for all gates | Hermes state not mutated | Strong | PMG clean check in all gates |
| **Failure taxonomy** | 15 distinct HOLD states across stages | Specific, actionable failure modes | Strong | HOLD states are specific and early |
| **Test strategy** | 191 unit tests (50 batch, 141 single-task) | Unit + smoke + regression | Partial | Unit tests cover logic; smoke uses mocked execution |
| **Batch orchestration** | Sequential, worktree-per-task, stop_on_first_hold | Sequential isolation | Strong | Sequential only, worktree isolation |
| **CI/gates** | governance-validators + final_gate_status.py + verify_final_head | Gate checks before merge | Strong | Two independent gate tools |
| **Observability** | JSON+MD artifacts per stage, batch_status.json | Structured logging | Partial | Good artifacts, no structured log stream |
| **Recovery** | Partial artifacts preserved for failed tasks | Replay from artifacts | Partial | Artifacts available; no formal replay |
| **Codex review** | Codex review before merge on PR head | Agent review before merge | Strong | Explicit `codex review --commit` on feature branches |
| **Parallelism policy** | None in v0; sequential only | Avoid until isolation is proven | Strong | No parallelism in v0 |
| **Retry policy** | No retries in v0 | No blind retries; fix root cause | Strong | No retries |
| **Command contract safety** | subprocess.run with explicit argv lists, no shell=True | Shell=False by default | Strong | All subprocess.run use list-form argv |
| **Trusted script** | Controller runs from task worktree at base_sha | Controller from trusted SHA | **Missing** | PR #314 P2 concern: script from base_sha |
| **Repo root derivation** | From __file__, not from cwd or --repo-root | Intentionally derived | Partial | Works; but not parametrizable |
| **Evaluation corpus** | No task corpus or scoring harness | Evaluated against known-good tasks | **Missing** | Feature gap |
| **Benchmark realism** | No benchmark realism testing | Benchmark against SWE-bench/Apollo style tasks | **Missing** | Out of scope for v0 but untracked |

---

## 4. Gap Analysis

### A. Missing Capability

**The autocoder is still proving scaffolding.** It can run a mock task end-to-end, but we cannot answer the question "does the output patch actually fix the issue it was given?" The minimum useful loop from issue → patch → review requires a regression test of the patch against pre-existing unit tests.

**Minimum viable loop:** run task → apply patch to test suite → verify tests pass.

This is not currently possible because:
- The apply path creates a local branch, not a testable head
- No automated regression step in the pipeline

### B. Missing Evaluation Layer

**No task corpus.** The 191 tests confirm the scaffold is structurally correct, not that it produces correct output for a given task. There are no "known-good" or "known-bad" task fixtures.

**What is needed:**
- 10–20 task packets paired with expected outcomes
- A runner that scores observed vs. expected
- A regression gate that blocks PRs when corpus score degrades

### C. Overbuilt Safety

**The safety model is appropriate for v0.** One potential overbuild: requiring `APPLY_READY` from `verify_temp_worktree_apply_readiness.py` before running `preview` — but both are sequential read-only operations, so this is correct separation. The gated apply is not overbuilt.

**Redundant check (harmless):** The combined `HOLD_REPO_DIRTY` check in both the executor and the verifier. Not redundant — different layers. OK.

**Potentially overbuilt for v0:** The `allowed_files` per-task constraint checking cross-task duplicates in the batch controller. This is correct and necessary.

### D. Underbuilt Safety

**1. Trusted-script issue (PR #314 P2 - must fix before live Claude):** If `base_sha` points to an older commit, `run_autocoder_single_task.py` from that commit runs instead of the reviewed copy. In v0 this is **acceptable risk** (all tasks use `execution_mode: mocked`, so the blast radius is a spurious `HOLD_MAIN_DIRTY` in the fake execution). Before live Claude, this must be fixed.

**2. Stale worktree cleanup:** 70+ orphaned worktrees accumulate over test runs. None are destructive, but they clutter `/tmp/aed_runs/` and `git worktree list`. Low priority but easy to fix.

**3. Untracked file in batch packet:** The batch controller's `validate_task_constraints()` checks `task_packet_inside_repo`, `duplicate IDs`, `duplicate branches`, `duplicate allowed_files` — but does not check that a task's `output_root` is not inside another task's `task_worktrees/` directory. Overlapping output roots would conflict but this is caught at the filesystem level by the single-task controller. Acceptable.

### E. Direction Risk

**The system is building a controller instead of an evaluator.** This is the most important strategic risk. The autocoder can produce a branch candidate, but we cannot yet answer "did the candidate correctly solve the original issue?" without a human reviewing it.

Building batch v1 parallelism before establishing evaluation feedback means: feature velocity / difficulty measuring, no regression signal, no way to catch broken commands.

**This is the correct direction:** Evaluate the output, not the scaffold. The scaffold is working. The output quality is unknown.

### F. Human Factors

**The architecture is mostly readable from docs.** The core design documents are accurate (`autocoder_single_task_controller_design.md`, `autocoder_batch_controller_design.md`, `temp_worktree_human_apply_workflow.md`). The `pr314_batch_controller_gate_process_gap.md` correctly records the process gap and gap-fixing steps.

**Gaps in human-facing docs:**
- No single document that explains what each HOLD state means and how to diagnose it (handbook section 12 covers this but is a draft)
- No operator quick-start guide
- No troubleshooting tree for batch-specific failures

---

## 5. PR #314 P2 Deep Special Review

### Concern

"When a batch packet names any existing base_sha, the batch controller executes run_autocoder_single_task.py from that worktree's commit rather than the reviewed controller copy. If base_sha points to an older or unreviewed commit, the batch controller bypasses safety guarantees enforced by the current script."

### Audit Results

| Question | Answer |
|----------|--------|
| What script is executed? | `<task_worktree_path>/scripts/local/run_autocoder_single_task.py` — the worktree's copy at `base_sha` |
| What commit supplies that script? | `base_sha` (the commit the worktree was created from) |
| Could an older `base_sha` alter controller behavior? | Yes — the single-task controller code IS the script at `base_sha`. An older commit could have a broken `REPO_ROOT` computation, a missing HOLD state, or a removed safety check. |
| Is `base_sha` always the current trusted HEAD? | In practice, yes — batch packets use the current main HEAD for PR smoke runs. But the system does not enforce this. A malicious or buggy batch packet can supply any valid SHA. |
| Should controller code always come from the parent repo? | **Yes**, for the controller logic. `REPO_ROOT` should come from the worktree (the code-under-test). The controller code should come from the reviewed checkout. |
| Would `--repo-root` be safer? | **Yes** — an explicit `--repo-root <worktree_path>` parameter overrides `__file__`-based detection without requiring the script to be from the worktree. The controller code stays on the reviewed branch; only the repo-under-test comes from the worktree. |
| Is this must-fix before live Claude? | **Yes** — the blast radius of an untrusted controller script is larger when real execution (not mock) is involved. |

### Classification

Fix before `'execution_mode: claude'` is ever permitted through the single-task controller.

**Not critical in v0** (mocked execution limits blast radius to spurious HOLDs) but **must-fix before the live Claude path is activated**.

### Recommended Fix

Add `--repo-root <worktree_path>` to `run_autocoder_single_task.py`. The batch controller passes the worktree path explicitly. The script computes `REPO_ROOT` as `Path(--repo-root)` if passed, else falls back to `__file__`-derivation for backward compatibility.

Effect: Controller code always from the reviewed parent repo checkout. Repo-under-test (the files being edited) from the worktree at `base_sha`. Correct separation of concerns.

---

## 6. Risk Register

| Risk | Severity | Category | Mitigation |
|------|----------|----------|------------|
| Untrusted controller script from arbitrary `base_sha` | Medium | Safety — before live Claude | Add `--repo-root`; never run controller from worktree |
| Untracked temp files polluting test runs | Low | Operability | Stale worktree cleanup tool |
| No evaluation corpus — cannot measure output quality | High | Capability | Build task corpus + evaluator |
| No regression gate — scaffold changes can break output silently | High | Process | Add corpus runner to CI |
| Hermes state not protected from batch scripts themselves | Low | Safety | PMG in CI gate; no Hermes write calls in scripts |
| Orphaned worktrees accumulating without cleanup | Low | Operability | `cleanup_worktrees.py` |
| `stop_on_first_hold=false` not implemented in v0 | Low | Capability | Roadmapped for batch v1 |
| PMG baseline must be manually managed per-session | Low | Operability | Document Option B (fresh snapshot) in gate doc |
| Codex review not automated in CI gate | Medium | Process | Run Codex explicitly; document in gate workflow |
| `apply_temp_worktree_patch_to_branch.py` requires `--allow-real-apply` | N/A | Design | Correct — human must pass this flag to enable apply |

---

## 7. Specific Next Implementation Recommendation

### Next: Fix trusted-script / --repo-root issue

**What to change:**

1. **`run_autocoder_single_task.py`:** Add `--repo-root` argument. If provided, use it for `REPO_ROOT`. Fall back to `__file__`-derivation for backward compatibility.

2. **`run_autocoder_batch.py`:** Change argv construction to pass `--repo-root <task_worktree_path>` when invoking the single-task controller.

3. **Tests:** Add test verifying that `--repo-root` override works; add test verifying that script still fails with `HOLD_MAIN_DIRTY` when the passing `--repo-root` points at a dirty parent repo (this test already implicitly exists via the smoke, but formalize it).

**What NOT to change:**
- No other stage tool needs `--repo-root` in this PR
- No `__file__`-based default behavior change for standalone use
- No batch controller logic change beyond the argv construction

**Rationale:** This closes the PR #314 P2 concern before live Claude is ever considered. It is a minimal, targeted fix. It makes the REPO_ROOT boundary intentional rather than relying on `__file__`-derivation.

**Files changed:** `scripts/local/run_autocoder_single_task.py`, `scripts/local/run_autocoder_batch.py`, `tests/test_run_autocoder_single_task.py`, `tests/test_run_autocoder_batch.py`

**Test coverage:** Add test case in `test_run_autocoder_batch.py` that verifies the `argv` passes `--repo-root` with the correct worktree path; add test in `test_run_autocoder_single_task.py` that verifies `--repo-root` takes precedence over `__file__`-derivation.

**Gate:** `final_gate_status.py` + `verify_final_head_merge_command.py` must return ready before merge.

---

## 8. Sources Reviewed

| Source | Relevance | Key Takeaway |
|--------|-----------|-------------|
| AED existing docs (`autocoder_single_task_controller_design.md`, `autocoder_batch_controller_design.md`, `pr314_batch_controller_gate_process_gap.md`) | Primary — what was actually built | Pipeline model confirmed, design stable |
| AED temp-worktree design (`temp_worktree_human_apply_workflow.md`) | Primary — apply layer design | 6-stage design followed exactly; apply boundary correct |
| AED stage scripts (each ~2000–200 lines) | Primary — implementation vs. spec | REPO_ROOT self-location matches design; command contract built for future use |
| AED test files (test_run_autocoder_batch.py 1013L, test_run_autocoder_single_task.py 444L) | Primary — testing strategy | Hybrid CLI + module-level testing; `fake_run` monkeypatch only covers controller subprocess, not git operations |
| SWE-agent design (inferred 2024 public design notes) | Secondary — agent harness patterns | Sandboxing via bash language wrapper; AED's worktree approach is more isolated |
| OpenAI Codex AGENTS.md public guidance (inferred) | Secondary — command allowlist pattern | Command contract approach (`build_claude_command_contract`) is aligned; needs enforcement layer |

---

## 9. Full 20-Field Final Report

| # | Field | Value |
|---|-------|-------|
| 1 | **Executive verdict** | Audited: scaffold is sound and well-tested. Missing evaluation layer is the key gap. |
| 2 | **Correct direction?** | Yes, but the system is "proven scaffolding without output measurement". Not yet demonstrably useful at the top-level task. |
| 3 | **Biggest strength** | Clean fail-closed pipeline with 15+ specific HOLD states, worktree-per-task isolation, PMG integration, human-review boundary enforced in code. |
| 4 | **Biggest missing layer** | **Task corpus + evaluation harness.** Cannot measure output quality; no regression signal. |
| 5 | **Biggest safety risk** | **Trusted-script issue (PR #314 P2):** controller code from worktree at arbitrary `base_sha`; must-fix before live Claude. |
| 6 | **Biggest overbuild risk** | **None yet.** Safety model is appropriate. No redundant gate. Command contract is built but not enforced — correct for v0. |
| 7 | **Trusted for mock-only use?** | ✅ Yes. Mock execution, sequential, worktree isolation, no mutating operations. Safe for v0. |
| 8 | **--repo-root / trusted-script next?** | **Yes.** Must fix before live Claude. Minimal, targeted change. |
| 9 | **Task/evaluation corpus before more features?** | **Yes.** Build measuring instrument before building more features. |
| 10 | **Fix before live Claude** | Fix `--repo-root` + add command contract enforcement + expand failure taxonomy + define timeout policy. |
| 11 | **Fix before batch v1** | Task corpus + HOLD diagnosis guide + batch status UX expansion. |
| 12 | **Leave alone** | 6-stage design. Apply-to-local-branch model. PMG integration. Command contract design. `_real_subprocess_run` pattern. |
| 13 | **Proposed next implementation** | Add `--repo-root` to `run_autocoder_single_task.py`, pass it from batch controller's argv. |
| 14 | **Proposed next 5 implementations** | `--repo-root` → stale worktree cleanup → task corpus + evaluator → batch status UX → HOLD diagnosis handbook |
| 15 | **Book/handbook draft status** | ✅ `docs/autocoder_engineering_handbook.md` drafted (19k chars, 18 sections) |
| 16 | **Sources reviewed** | AED docs (3 design docs, 1 process gap doc), AED stage scripts (7 scripts), AED test files (2 test files), SWE-agent design patterns, OpenAI Codex AGENTS.md public guidance principles. |
| 17 | **Drift classification** | **STRONG_BUT_NEEDS_EVAL_LAYER** — architecture sound, safety conservative and correct, scaffold proven, but no measuring instrument for output quality. |
| 18 | **No live Claude** | ✅ Confirmed for batch controller pipeline. `execution_mode` is `frozenset(["mocked"])` in batch controller (`run_autocoder_batch.py`); `claude`/`live`/`real` mode rejected at the controller level. Stage-2 executor (`run_temp_worktree_execution.py`) does accept `execution.mode="claude"` but gates it behind `if not enable_real_claude_executor: raise "claude mode not yet ready"`. The safety property (no unintended live-Claude invocation) holds because: (a) batch controller REJECTOR is correct, (b) `--enable-real-claude-executor` is opt-in and False by default. |
| 19 | **No --enable-real-claude-executor** | ⚠️ Correction. The original audit stated zero occurrences across all stage scripts — this was incorrect. `run_temp_worktree_execution.py` (stage-2 executor) contains `--enable-real-claude-executor` at lines 621, 998, 1015, 1189, 1199, 1319, 1321 (7 occurrences, not 8 as previously stated — the prior count included an extra line reference). The flag is defined as a CLI arg (`--enable-real-claude-executor`) and used as a boolean guard. The batch controller (`run_autocoder_batch.py`) does not contain this flag. This remediation corrects the audit record. |
| 20 | **Hermes memory/profile not touched** | ✅ Confirmed. Zero `skill_manage`, `memory.*`, `fact_store`, `memory_store`, `MEMORY.md`, `USER.md` calls in any autocoder script. PMG compare returns `blocked=0` after all post-merge verification runs. |
