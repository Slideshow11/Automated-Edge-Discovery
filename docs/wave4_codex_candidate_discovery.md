# Wave 4 Codex Candidate Discovery

**Scan date:** 2026-05-28T10:50:20-04:00
**main HEAD:** `27e62f0e5566f6ef9ff78a48d0e9187f9826c724`
**Method:** Terminal-only bounded commands (git grep, python3 JSON, sed). No search_files, no read_file.

---

## 1. Closed Backlog Baseline

All Wave 1/2/3 Codex remediation candidates are closed:

| Closed Candidate | Classification | PR |
|---|---|---|
| `rgr-314-stop-on-first-hold-bool` | CLOSED_FIXED_WITH_TEST | PR #343 |
| `rgr-314-task-id-path-traversal` | CLOSED_ALREADY_FIXED_WITH_TEST | PR #344 |
| `rgr-320-batch-ok-subprocess-rc` | CLOSED_ALREADY_FIXED_WITH_TEST | PR #345 |
| `rgr-320-base-sha-catfile` | CLOSED_ALREADY_FIXED_WITH_TEST | Wave 3 closeout |
| `rgr-320-no-newline-marker` | CLOSED_FALSE_POSITIVE_WITH_EVIDENCE | Wave 2 closeout |
| `rgr-319-output-root-null-normalization` | CLOSED_FIXED_WITH_TEST | PR #334 |
| `rgr-317-repo-root-propagation` | CLOSED_ALREADY_FIXED_WITH_TEST | PR #338 |
| `doc-323-applied-status-name` | CLOSED_DOCS_ONLY_FIXED | PR #323 |
| `doc-323-enable-real-claude-executor-claim` | CLOSED_DOCS_ONLY_FIXED | PR #323 |

**Zero NEEDS_TRIAGE candidates remain from the Wave 1/2/3 backlog.** The last closeout (PR #347) confirmed all 9 corpus entries are fully closed.

---

## 2. Search Areas Inspected

| Area | Files found | Pattern |
|---|---|---|
| High-risk scripts | 38 .py files in scripts/local + tests | gate, merge, review, mutation, PMG, hermes, kanban, codex, validation |
| Waiter / review gate | `wait_for_pr_ready.py`, `check_pr_review_comments.py` | blocking patterns, subprocess returns |
| Subprocess calls | `aed_final_gate.py`, `apply_temp_worktree_patch_to_branch.py` | returncode handling, broad exception handling |
| Hermes mutation risk | `autocoder_run_controller.py`, `build_merge_ready_packet.py` | forbidden patterns, safety invariants |
| Path construction | `_smoke_shared.py`, `aed_executor_packet.py` | Path()/mkdir/write_text in low-level helpers |
| Mock/test patterns | `audit_claude_invocation.py` | mock-only detection, MOCK_ONLY_RUN_DETECTED |
| Gate status | `final_gate_status.py`, `wait_for_pr_ready.py` | READY_TO_MERGE, HOLD_UNKNOWN, review comment gate |

---

## 3. Risk Categories Inspected

- `shell=True` — no occurrences in production scripts (protected via design)
- `subprocess.returncode` handling — present in aed_final_gate.py, apply_temp_worktree_patch_to_branch.py, wait_for_pr_ready.py; well-structured with explicit exit-code checks
- `except Exception` — present in wait_for_pr_ready.py (lines 157, 336, 376, 425, 466, 639, 750); review-comment gate (lines 331, 373); PMG runner; broad coverage in waiter outer-try block
- `HOLD_UNKNOWN` — used as sentinel in wait_for_pr_ready.py; used in audit_claude_invocation.py
- `TEST_MODE` / `mock` patterns — abundant in test files; audit_claude_invocation.py uses mock vs real detection
- Hermes mutation (skill_manage, memory, fact_store, profile) — documented as forbidden in executor packet design; not called in production scripts
- `dict.get()` with defaults — abundant across all scripts; not a bug by itself but a code-volume observation
- Path construction (Path, mkdir, write_text) — in `_smoke_shared.py` (low risk), `aed_executor_packet.py` (packet generation), `apply_temp_worktree_patch_to_branch.py` (output writing)
- `review_comment_gate` / `wait_for_pr_ready` — primary reviewer-blocker mechanism
- `gh pr merge --match-head-commit` — correctly used throughout as anti-swap protection

---

## 4. Fresh Wave 4 Candidate Cards

### W4-2026-001: wait_for_pr_ready.py — outer try block swallows all exceptions including KeyboardInterrupt

**Suspected issue:** `wait_for_pr_ready.py:750` has `except Exception as e:` in the outer try block of `main()`. This catches **all** exceptions including `KeyboardInterrupt`, `SystemExit`, and `BrokenPipeError`. If the waiter is interrupted by SIGTERM (e.g. external 240s timeout kill), the script writes a partial JSON with `STATUS_ERROR_TOOLING` and exits cleanly — masking the real cause.

**Current evidence:**
```python
# wait_for_pr_ready.py:748-755
try:
    ...
except Exception as e:
    report["fatal_error"] = str(e)
    report["status"] = STATUS_ERROR_TOOLING  # "ERROR_TOOLING"
    report["next_safe_action"] = next_action_for_status(STATUS_ERROR_TOOLING, ...)
    ...
    sys.exit(0)  # clean exit despite fatal error
```

Also: lines 336, 376, 425, 466 all catch `Exception as e` and return `STATUS_ERROR_TOOLING`.

**Likely production files:** `scripts/local/wait_for_pr_ready.py`
**Likely test files:** `tests/test_wait_for_pr_ready.py`

**Why it matters:** When the waiter is killed by an external timeout, the operator sees `ERROR_TOOLING` with no distinction between "script had a bug" and "script was killed externally." This confounds post-mortem analysis of waiter failures.

**Why not duplicate of closed Wave 1/3 work:** This is a new finding about `wait_for_pr_ready.py` behavior under external SIGTERM, not related to task_id validation, stop_on_first_hold, subprocess RC guards, or path traversal. The Wave 3 evidence audit covered `run_autocoder_batch.py` and `run_autocoder_eval_corpus.py`, not the waiter itself.

**Confidence: MEDIUM** — the pattern is observable; whether it causes real harm in practice depends on whether external kills actually happen in normal operation.

**Priority: MEDIUM**

**Recommended next action:** Evidence-only audit: run the waiter under SIGTERM and verify whether the JSON output is useful or misleading. Document whether `ERROR_TOOLING` is distinguishable from a real tooling error under kill conditions.

---

### W4-2026-002: check_pr_review_comments.py — bare `except Exception` hides JSON parse failures as CLEAN

**Suspected issue:** `check_pr_review_comments.py:331` has `except Exception:` that falls through to `return ("review_comments_clean", [], "unknown")` — meaning any exception (including JSONDecodeError from malformed API responses) is reported as CLEAN, silently passing a review-comment gate that may not have been evaluated.

**Current evidence:**
```python
# check_pr_review_comments.py:329-333
except Exception:
    # silently pass — review comment gate cannot block on tooling error
    return ("review_comments_clean", [], "unknown")
```

Compare this with wait_for_pr_ready.py:336 which correctly returns `STATUS_ERROR_TOOLING` on exception. The inconsistency means `check_pr_review_comments.py` can report CLEAN while actually being unable to parse comments.

**Likely production files:** `scripts/local/check_pr_review_comments.py`
**Likely test files:** `tests/test_check_pr_review_comments.py`

**Why it matters:** If the GitHub API returns an unexpectedly formatted response (e.g., a new comment type, a reaction instead of a review, an unusually long comment body), the script silently reports CLEAN and the PR proceeds to merge without actual review-comment gate evaluation.

**Why not duplicate of closed Wave 1/3 work:** This is a waiter-gate concern, not a task_id/path/bool/RC concern from Wave 1/2/3. The Wave 3 audit did not cover `check_pr_review_comments.py`.

**Confidence: MEDIUM** — the silent-fallback pattern is clearly present. The risk is conditional on unexpected API responses, which may or may not have occurred in practice.

**Priority: MEDIUM**

**Recommended next action:** Evidence-only audit: add a synthetic malformed JSON response to the API mock and confirm whether the gate reports CLEAN or ERROR_TOOLING. Document the gap between expected and actual behavior.

---

### W4-2026-003: review_comment_gate — severity keyword scanning may misclassify P2 blockers as UNSPECIFIED_INFO when they contain certain words

**Suspected issue:** The review comment gate classifies comments by scanning for severity keywords (P0, P1, P2) and blocking words. If a P2 blocker uses words that don't match the blocking dictionary, it may be classified as `UNSPECIFIED_INFO` or `COMMENTED` instead of a blocking finding. This would cause a real P2 to pass the gate silently.

**Current evidence:** The gate logic in `check_pr_review_comments.py` uses keyword matching. The exact blocking-word dictionary is not visible in static triage output — but the pattern of `severity P2 + non-blocking words → COMMENTED` is structurally possible.

**Likely production files:** `scripts/local/check_pr_review_comments.py`
**Likely test files:** `tests/test_check_pr_review_comments.py`

**Why it matters:** If a P2 blocker from Codex uses nuanced language (e.g., "recommend not reopening" vs "do not reopen"), it could be classified as non-blocking when it should block. The PR #347 Codex P2 (which correctly flagged the duplicate `rgr-320-base-sha-catfile` classification) used the phrase "Do not reopen" — which the gate correctly caught as blocking. But less explicit phrasing could slip through.

**Why not duplicate of closed Wave 1/3 work:** This is a review-comment gate classification concern, separate from the three Wave 3 candidates (task_id, stop_on_first_hold, subprocess RC). The Wave 3 audit did not inspect the blocking-word dictionary in detail.

**Confidence: LOW** — the blocking-word dictionary is not fully visible from static analysis alone; the concern is structurally possible but not confirmed.

**Priority: LOW** — requires reading the gate source to confirm whether weak P2 phrasing can slip through.

**Recommended next action:** Human review: inspect the blocking-word dictionary in `check_pr_review_comments.py` and assess whether P2 comments with non-obvious phrasing are correctly classified.

---

### W4-2026-004: aed_executor_packet.py — `dict.get()` with mutable default arguments could produce unexpected behavior

**Suspected issue:** Multiple uses of `dict.get(key, [])` or `dict.get(key, {})` where the mutable default is a list or dict. In Python, default mutable arguments are shared across calls. However, for `dict.get()` specifically (unlike function default args), this is safe — `dict.get()` evaluates the default at call time, not at definition time. So this pattern is **not a bug** in Python 3.

**Current evidence:** `aed_executor_packet.py:139` — `forbidden = pr_plan.get("forbidden_files", [])` — safe due to Python dict.get semantics.

**Confidence: LOW** — Python dict.get is safe for mutable defaults. Marking as LOW since the pattern was scanned but does not constitute a bug.

**Priority: LOW**

**Recommended next action:** No action needed. This is a static-triage false positive. The pattern is safe.

---

### W4-2026-005: apply_temp_worktree_patch_to_branch.py — path containment check uses `.startswith()` which is fragile for directory traversal edge cases

**Suspected issue:** The path containment check uses `str(Path(path).resolve()).startswith(str(repo_root.resolve()))`. While `resolve()` resolves symlinks and removes `..` components, the `startswith` check is not the same as a true containment check. For example, if `repo_root` is `/home/user/repo` and a path resolves to `/home/user/repo2/subdir`, `startswith` would incorrectly treat it as contained (since `/home/user/repo2` starts with `/home/user/repo`). However, `resolve()` on the repo_root would canonicalize the path, so this may be safe in practice.

**Current evidence:**
```python
# apply_temp_worktree_patch_to_branch.py:248
return str(Path(path).resolve()).startswith(str(repo_root.resolve()))
```

**Likely production files:** `scripts/local/apply_temp_worktree_patch_to_branch.py`
**Likely test files:** None confirmed

**Why it matters:** If the path containment check can be bypassed, a malicious patch could write to files outside the intended repository root.

**Why not duplicate of closed Wave 1/3 work:** This is about path containment in the temp-worktree apply script, different from `run_autocoder_batch.py:377` path traversal concern (rgr-314). The containment logic uses `startswith` rather than `re.fullmatch`.

**Confidence: LOW** — the `resolve()` canonicalization likely closes the gap; requires testing with adversarial paths to confirm.

**Priority: LOW**

**Recommended next action:** Evidence-only audit: construct adversarial paths (e.g., symlinks, `..` components, unusual unicode) and verify whether the containment check correctly rejects them.

---

## 5. Top 3 Recommended Evidence Audits

1. **W4-2026-001** (MEDIUM confidence) — `wait_for_pr_ready.py` outer try block swallows `KeyboardInterrupt`/`SIGTERM` and reports `ERROR_TOOLING` instead. Evidence-only: run waiter under SIGTERM and confirm JSON output is diagnostic or misleading.

2. **W4-2026-002** (MEDIUM confidence) — `check_pr_review_comments.py` silent-fallback on exception reports `CLEAN` when unable to parse. Evidence-only: inject malformed JSON via mock and confirm gate behavior.

3. **W4-2026-003** (LOW confidence) — review-comment gate blocking-word dictionary may misclassify certain P2 phrasing. Human review: inspect the keyword matching logic in `check_pr_review_comments.py`.

---

## 6. Duplicate-Exclusion List

These closed items from Wave 1/2/3 are NOT being reopened:

- `rgr-314-stop-on-first-hold-bool` → PR #343 (type validation)
- `rgr-314-task-id-path-traversal` → PR #344 (re.fullmatch)
- `rgr-320-batch-ok-subprocess-rc` → PR #345 (rc guard)
- `rgr-320-base-sha-catfile` → Wave 3 closeout (test exists)
- `rgr-320-no-newline-marker` → Wave 2 closeout (false positive with evidence)
- `rgr-319-output-root-null-normalization` → PR #334 (normalization ordering)
- `rgr-317-repo-root-propagation` → PR #338 (existing coverage evidence)
- `doc-323-applied-status-name` → PR #323 (docs only)
- `doc-323-enable-real-claude-executor-claim` → PR #323 (docs only)

---

## 7. Operational Note

For AED repo audits, use terminal-only commands (git grep, python3, sed) instead of search_files/read_file. This avoids potential blocking when the AED workspace is in a certain state. All inspection in this scan used bounded terminal commands with explicit timeouts and output caps.

---

## 8. Explicit Statements

- **No production code changed.**
- **No tests changed.**
- **No repair executed.**
- **No search_files or read_file used** — all inspection via git grep, python3 JSON parsing, and sed with output caps.
- **No live Claude used.**
- **No autocoder batch executed.**
- **Hermes memory/profile/config not touched.**
- **No GitHub review threads resolved by script or API.**
- **No modification of production code or tests.**
- **No repair execution occurred.**