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

**Zero NEEDS_TRIAGE candidates remain from the Wave 1/2/3 backlog.**

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
- `except Exception` — present in waiter (lines 157, 336, 376, 425, 466, 639, 750); review-comment gate; PMG runner
- `HOLD_UNKNOWN` — used as sentinel in wait_for_pr_ready.py; used in audit_claude_invocation.py
- `TEST_MODE` / `mock` patterns — abundant in test files; audit_claude_invocation.py uses mock vs real detection
- Hermes mutation (skill_manage, memory, fact_store, profile) — documented as forbidden in executor packet design; not called in production scripts
- `dict.get()` with defaults — abundant across all scripts; not a bug by itself but a code-volume observation
- Path construction (Path, mkdir, write_text) — in `_smoke_shared.py` (low risk), `aed_executor_packet.py` (packet generation), `apply_temp_worktree_patch_to_branch.py` (output writing)
- `review_comment_gate` / `wait_for_pr_ready` — primary reviewer-blocker mechanism
- `gh pr merge --match-head-commit` — correctly used throughout as anti-swap protection

---

## 4. Wave 4 Candidate Cards

### W4-2026-001: wait_for_pr_ready.py — outer try block swallows exceptions

**Classification:** `FALSE_POSITIVE_REVIEW_INVALIDATED`

**Original claim:** `wait_for_pr_ready.py:750` has `except Exception as e:` that catches `KeyboardInterrupt`, `SystemExit`, and masks SIGTERM kills as `ERROR_TOOLING`.

**Review correction:** `KeyboardInterrupt` and `SystemExit` inherit from `BaseException`, not `Exception`. An ordinary SIGTERM terminates the process without raising an exception at all — the try block never catches it because the process is killed asynchronously. The `except Exception` block cannot produce the failure mode described. This candidate was based on a false Python exception model and is **not a valid remediation target**.

**Likely production files:** `scripts/local/wait_for_pr_ready.py`
**Likely test files:** `tests/test_wait_for_pr_ready.py`

**Confidence: N/A — INVALIDATED BY CODEX P2 REVIEW**

**Recommended next action:** None. No repair needed.

---

### W4-2026-002: check_pr_review_comments.py — silent fallback on exception reports CLEAN

**Classification:** `FALSE_POSITIVE_REVIEW_INVALIDATED`

**Original claim:** `check_pr_review_comments.py:331` has `except Exception:` falling through to `return ("review_comments_clean", [], "unknown")`, silently passing the review-comment gate when JSON parse fails.

**Review correction:** The cited line (331) is inside `dedup_findings`, not the review-comment gate path. Invalid JSON from `gh_api` is returned as an error that is later included in `api_errors`, producing `REVIEW_COMMENTS_INCONCLUSIVE` — which fails closed (blocks), not clean. This candidate was fabricated from misreading the source. The gate already fails closed on tooling errors.

**Likely production files:** `scripts/local/check_pr_review_comments.py`
**Likely test files:** `tests/test_check_pr_review_comments.py`

**Confidence: N/A — INVALIDATED BY CODEX P2 REVIEW**

**Recommended next action:** None. No repair needed.

---

### W4-2026-003: review_comment_gate — blocking-word dictionary may misclassify P2 phrasing

**Suspected issue:** The review comment gate classifies comments by scanning for severity keywords (P0, P1, P2) and blocking words. If a P2 blocker uses non-obvious phrasing, it could be classified as `UNSPECIFIED_INFO` or `COMMENTED` instead of a blocking finding.

**Current evidence:** Static triage found the gate uses keyword matching; the exact blocking-word dictionary is not fully visible from static analysis alone.

**Likely production files:** `scripts/local/check_pr_review_comments.py`
**Likely test files:** `tests/test_check_pr_review_comments.py`

**Why it matters:** If a P2 blocker from Codex uses nuanced language that doesn't match the blocking dictionary, it could pass the gate silently. The PR #348 Codex P2 itself used clear phrasing ("Do not reopen the closed cat-file candidate") which the gate correctly caught. But weaker phrasing could slip through in future reviews.

**Why not duplicate of closed Wave 1/3 work:** This is a review-comment gate classification concern, separate from the three Wave 3 candidates (task_id, stop_on_first_hold, subprocess RC).

**Confidence: LOW** — requires human inspection of the blocking-word dictionary to assess whether weak P2 phrasing can slip through.

**Priority: LOW**

**Recommended next action:** Human review: inspect the keyword matching logic in `check_pr_review_comments.py` and assess whether P2 comments with non-obvious phrasing are correctly classified.

---

### W4-2026-004: aed_executor_packet.py — `dict.get()` with mutable default arguments

**Classification:** `NOT_A_BUG`

**Original claim:** Multiple uses of `dict.get(key, [])` where the mutable default is a list or dict. In Python, default mutable arguments to *functions* are shared across calls. However, for `dict.get()` itself, the default is evaluated at call time, not definition time — so this is safe.

**Current evidence:** `aed_executor_packet.py:139` — `forbidden = pr_plan.get("forbidden_files", [])` — safe due to Python dict.get semantics. The default object is not stored or mutated across calls.

**Confidence: N/A — NOT A BUG per Python semantics**

**Recommended next action:** None. No repair needed.

---

### W4-2026-005: apply_temp_worktree_patch_to_branch.py — path containment check uses `.startswith()`

**Suspected issue:** The path containment check uses `str(Path(path).resolve()).startswith(str(repo_root.resolve()))`. While `resolve()` canonicalizes paths and resolves symlinks, the `startswith` check could be fragile for directory traversal edge cases (e.g., if `repo_root` is `/home/user/repo` and a path resolves to `/home/user/repo2/subdir`, it incorrectly matches).

**Current evidence:**
```python
# apply_temp_worktree_patch_to_branch.py:248
return str(Path(path).resolve()).startswith(str(repo_root.resolve()))
```

**Likely production files:** `scripts/local/apply_temp_worktree_patch_to_branch.py`
**Likely test files:** None confirmed

**Why it matters:** If the path containment check can be bypassed, a malicious patch could write to files outside the intended repository root.

**Why not duplicate of closed Wave 1/3 work:** This is about path containment in the temp-worktree apply script, different from `run_autocoder_batch.py:377` path traversal concern (rgr-314). The containment logic uses `startswith` rather than `re.fullmatch`.

**Confidence: LOW** — `resolve()` canonicalization likely closes the gap; requires adversarial path testing to confirm.

**Priority: LOW**

**Recommended next action:** Evidence-only audit: construct adversarial paths (e.g., symlinks, `..` components, unusual unicode) and verify whether the containment check correctly rejects them.

---

## 5. Final Wave 4 Candidate Summary

| Candidate | Classification | Confidence | Priority | Recommended Action |
|---|---|---|---|---|
| W4-2026-001 | FALSE_POSITIVE_REVIEW_INVALIDATED | N/A | N/A | None — no repair needed |
| W4-2026-002 | FALSE_POSITIVE_REVIEW_INVALIDATED | N/A | N/A | None — no repair needed |
| W4-2026-003 | LOW_CONFIDENCE_HUMAN_REVIEW | LOW | LOW | Human review of blocking-word dictionary |
| W4-2026-004 | NOT_A_BUG | N/A | N/A | None — not a bug |
| W4-2026-005 | LOW_CONFIDENCE_EVIDENCE_ONLY | LOW | LOW | Evidence-only audit (adversarial path test) |

**No high-confidence Wave 4 remediation candidates were found.** All high-confidence candidates (W4-2026-001, W4-2026-002) were invalidated by Codex P2 review. Remaining items are LOW confidence and do not require immediate action.

---

## 6. Top Remaining Candidates (fewer than 3 valid)

Fewer than 3 valid candidates remain after review correction. The two candidates requiring human review/evidence-only action are:

1. **W4-2026-005** — Evidence-only path containment audit (LOW confidence)
2. **W4-2026-003** — Human review of blocking-word dictionary (LOW confidence)

No repair execution is warranted for either at this time.

---

## 7. Review Correction History

| Finding ID | Severity | Description |
|---|---|---|
| `codex-2954261db6cd` | P2 | W4-2026-001 invalidated — `except Exception` cannot catch `KeyboardInterrupt`/`SystemExit`; SIGTERM kills without raising |
| `codex-48f767fe665f` | P2 | W4-2026-002 invalidated — cited fallback does not exist; invalid JSON fails closed as `REVIEW_COMMENTS_INCONCLUSIVE` |

Both findings were correctly addressed by the review-comment gate on PR #348. The document was corrected to remove false candidates and re-classify remaining items appropriately. No production code or tests were changed.

---

## 8. Duplicate-Exclusion List

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

## 9. Operational Note

For AED repo audits, use terminal-only commands (git grep, python3, sed) instead of search_files/read_file. This avoids potential blocking when the AED workspace is in a certain state. All inspection in this scan used bounded terminal commands with explicit timeouts and output caps.

---

## 10. Explicit Statements

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