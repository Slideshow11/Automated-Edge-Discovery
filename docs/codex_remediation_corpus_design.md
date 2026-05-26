# Codex-Remediation Corpus Design

**Corpus ID**: `codex-remediation-pr314-320`
**Source**: `docs/codex_note_retrospective_audit_pr314_320.md`
**Base SHA**: `03b66632e8a2ab3cbadc342d87e4d6bc5b9c8211`
**Corpus file**: `corpus/codex-remediation-pr314-320.json`
**Authored**: 2026-05-26
**Status**: PLANNING — not yet run against live autocoder

---

## 1. Purpose

This corpus converts findings from the PR #314–#320 retrospective audit into structured, executable remediation tasks for the AED batch autocoder. It is a **planning and data artifact** — not a fix PR, not a live run. It defines what should be done, by what method, under what safety constraints.

The corpus serves two goals:
1. **Regression evidence**: confirm that bugs flagged by Codex in PRs #314–#320 are actually fixed in current main and stay fixed.
2. **Documentation hygiene**: confirm that governance gaps (false audit claims, wrong status names) are addressed.

---

## 2. Input Source

`docs/codex_note_retrospective_audit_pr314_320.md` audited PRs #314–#320 and found **26 unique deduped P0/P1/P2 findings** (18 P1 + 8 P2).

Classification breakdown:

| Classification | Count | Notes |
|---|---|---|
| FIXED_ALREADY | 18 | Bugs fixed pre-merge by later PRs |
| STILL_PRESENT_GOVERNANCE_GAP | 2 | Fixed in PR #323 after audit |
| INCONCLUSIVE | 4 | Could not confirm bug or false-positive at head |
| FALSE_POSITIVE_WITH_EVIDENCE | 0 | No runtime false positives found |
| STILL_PRESENT_RUNTIME_BUG | 0 | None — all runtime bugs are fixed |

**Key fact**: The two governance gaps (wrong `--enable-real-claude-executor` claim, wrong `APPLY_COMPLETE_LOCAL_BRANCH` status name) were fixed in PR #323 **after** the audit was committed. They are recorded as FIXED_ALREADY in current main but were open at audit time.

---

## 3. Corpus Schema

```json
{
  "corpus_kind": "aed.codex_remediation.corpus.v0",
  "corpus_id": "codex-remediation-pr314-320",
  "source_audit_doc": "docs/codex_note_retrospective_audit_pr314_320.md",
  "base_sha": "<full 40-char SHA>",
  "scope": {
    "prs": [314, 315, 316, 317, 318, 319, 320],
    "finding_count": 26,
    "p1_count": 18,
    "p2_count": 8,
    "unique_classifications": {
      "FIXED_ALREADY": 18,
      "STILL_PRESENT_GOVERNANCE_GAP": 2,
      "INCONCLUSIVE": 4,
      "FALSE_POSITIVE_WITH_EVIDENCE": 0,
      "STILL_PRESENT_RUNTIME_BUG": 0
    }
  },
  "tasks": [ ... ]
}
```

### Task Schema

```json
{
  "task_id": "string — lowercase hyphenated",
  "wave": "int — execution wave (1=near-term, 9=far-term)",
  "source_pr": int,
  "finding_id": "string — Codex finding ID or derived",
  "severity": "P0 | P1 | P2",
  "classification": "FIXED_ALREADY | STILL_PRESENT_GOVERNANCE_GAP | INCONCLUSIVE | FALSE_POSITIVE_WITH_EVIDENCE | STILL_PRESENT_RUNTIME_BUG",
  "finding_summary": "string — short description",
  "current_main_status": "string — what the code looks like at base_sha",
  "task_category": "already_fixed_needs_regression_test | docs_only_fixed | inconclusive_needs_manual_audit | still_present_bug | false_positive_with_evidence",
  "action": {
    "type": "add_regression_test | verify_existing_test_and_document | add_evidence_note | add_design_doc | manual_review",
    "target_file": "string or null",
    "allowed_files": ["list of file patterns"],
    "forbidden_files": ["list of patterns"],
    "success_criteria": "string",
    "deliverable": "string"
  },
  "safety_notes": ["list of constraints specific to this task"],
  "notes": "string — additional context"
}
```

---

## 4. Task Categories

### `already_fixed_needs_regression_test`
**Definition**: A real bug existed and was fixed. The fix may not have accompanying regression tests. Codex correctly identified the bug.

**Desired outcome**: Add targeted regression tests that would have caught the bug, without re-fixing the already-fixed code.

**Constraint**: Do NOT rewrite the existing fix. Only add tests that verify the fix is present and effective.

### `docs_only_fixed`
**Definition**: A governance gap in documentation was identified and subsequently fixed (e.g., wrong status name, false audit claim). No runtime code was involved.

**Desired outcome**: Confirm the doc fix is present and add an evidence note or reference to the fixing commit.

**Constraint**: Do NOT touch runtime code. Only docs.

### `inconclusive_needs_manual_audit`
**Definition**: Codex flagged a concern but the audit could not confirm a bug or a false positive. Requires human expert review.

**Desired outcome**: Produce a written audit note with evidence concluding either "confirmed bug" or "false positive" with rationale.

**Constraint**: Do NOT write code until classification is resolved.

### `still_present_bug`
**Definition**: A real bug that exists in current main. (Currently zero in this corpus — all were fixed.)

**Desired outcome**: Fix the bug following normal AED patch workflow.

**Constraint**: Must go through patch-ready-for-human-review stop.

### `false_positive_with_evidence`
**Definition**: Codex finding is factually wrong. The evidence shows the code is correct.

**Desired outcome**: Document the false positive with evidence so it is not re-flagged.

**Constraint**: Do NOT change working code to "fix" a false positive.

---

## 5. Safety Constraints

All tasks in this corpus MUST respect these constraints regardless of task category:

1. **Mock/dry first**: Run with `execution_mode=mocked` before any real execution.
2. **No live Claude**: `--enable-real-claude-executor` must never be passed.
3. **Stop at patch-ready**: Controller stops at `HOLD_PATCH_READY_FOR_HUMAN_REVIEW`. Do not auto-apply.
4. **No push/merge/staging**: Controller never runs `git push`, `gh pr merge`, or `git stash push`.
5. **No Hermes mutation**: No `skill_manage`, `memory`, `fact_store`, or profile writes.
6. **No GitHub review-thread mutation**: No `resolveReviewThread` or `addPullRequestReviewComment`.
7. **Narrow allowed_files**: Each task lists only the files it may touch. Anything else requires a new task.
8. **Forbidden patterns**: Never touch `scripts/local/run_pmg_*`, `.hermes/`, `skills/`, `memory/`, `profiles/`.
9. **No new package installs**: Use only stdlib and already-installed deps.
10. **No `shell=True`**: All subprocess calls use explicit argv lists.

---

## 6. How the Batch Autocoder Should Consume This

1. Load `corpus/codex-remediation-pr314-320.json` as a task corpus.
2. For each task, the controller:
   a. Reads `task.allowed_files` and `task.forbidden_files`.
   b. Sets `execution_mode=mocked` for the first pass.
   c. Runs the relevant script with task-specific arguments.
   d. Evaluates `success_criteria` against the mock run output.
   e. If mock pass succeeds, optionally runs a live pass (requires explicit `--execution-mode=claude` override).
   f. Stops at `HOLD_PATCH_READY_FOR_HUMAN_REVIEW` with the patch as the deliverable.
3. The batch controller MUST NOT execute tasks with `task_category=still_present_bug` unless a human explicitly authorizes the live pass.
4. The corpus runner MUST skip tasks where `task.classification=FIXED_ALREADY` and `task.action.type=add_regression_test` if a regression test for the same concern already exists (verify by grep for the finding_id or equivalent check).

---

## 7. Why Tasks Are Not All Blindly "Fix This"

Many Codex findings in this audit were **correctly identified real bugs** that were subsequently fixed by other PRs before the audit was conducted. Blindly "fixing" them would:
- Re-introduce bugs already fixed.
- Waste compute on duplicate work.
- Risk introducing new bugs in already-correct code.

Instead, each task is classified by what actually needs to happen:
- `already_fixed_needs_regression_test`: The bug is gone; add a test so it stays gone.
- `docs_only_fixed`: No runtime impact; confirm the doc fix is in place.
- `inconclusive_needs_manual_audit`: Human review required before any action.
- `false_positive_with_evidence`: Document why Codex was wrong to prevent re-flagging.

---

## 8. Initial Wave

Wave 1 contains **7 tasks** selected by these criteria:
- Low risk (docs-only or regression-test additions)
- Already-fixed bugs where a regression test would provide permanent protection
- Inconclusive findings requiring written manual-audit conclusions
- No tasks requiring live Claude execution or runtime rewrites

| task_id | category | severity | action |
|---|---|---|---|
| `rgr-314-task-id-path-traversal` | already_fixed_needs_regression_test | P1 | add_regression_test |
| `rgr-314-stop-on-first-hold-bool` | already_fixed_needs_regression_test | P2 | add_regression_test |
| `rgr-320-batch-ok-subprocess-rc` | already_fixed_needs_regression_test | P1 | add_regression_test |
| `rgr-320-no-newline-marker` | inconclusive_needs_manual_audit | P1 | manual_review |
| `rgr-320-base-sha-catfile` | inconclusive_needs_manual_audit | P2 | manual_review |
| `doc-323-applied-status-name` | docs_only_fixed | P2 | verify_existing_test_and_document |
| `doc-323-enable-real-claude-executor-claim` | docs_only_fixed | P2 | verify_existing_test_and_document |

Future waves (not yet in this file) will cover:
- Wave 2: Remaining regression tests for output_root null handling, repo-root propagation
- Wave 3: any remaining inconclusive items that are resolved by manual audit
- Wave N: `still_present_bug` tasks if any are ever identified in this corpus

---

## 9. Wave Execution Rules

1. Wave 1 tasks may be executed in any order; the batch controller handles parallelization.
2. A task MUST NOT modify files outside its `allowed_files` list.
3. If a task's `success_criteria` cannot be met in mock mode, do not escalate to live — escalate to human review.
4. After all Wave 1 tasks complete, the corpus runner outputs a Wave 1 completion report.
5. Wave 2 tasks are unlocked only after Wave 1 completion report is reviewed and approved.

---

## 10. Relationship to corpus-001

This corpus is **orthogonal** to `corpus/corpus-001.json`:
- `corpus-001.json`: tasks for building new autocoder features (eval corpus runner, batch controller v1)
- `codex-remediation-pr314-320.json`: tasks for confirming past Codex findings are resolved and protected

They can be run independently or in sequence. They do not conflict.
