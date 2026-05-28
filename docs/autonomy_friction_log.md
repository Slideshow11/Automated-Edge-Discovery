# Autonomy Friction Log — AED Codex Remediation

> Each entry documents a friction point discovered during AED Codex
> remediation waves 2 and 3 — moments where the repair-plan flow, corpus
> classification, or gate system produced wrong results due to methodology
> gaps rather than code defects. Entries are factual, evidence-first, and
> include a preventive rule.

---

## Entry 1: FIXED_ALREADY classification can be wrong in both directions

**What happened**

Wave 2 corpus classified `rgr-319-output-root-null-normalization` as
`FIXED_ALREADY`. The repair-plan generator accepted this and produced a
no-op plan. During PR #333 review, a controller-path test proved the bug
was still live: `validate_task_constraints` was called on raw task packets
(with null `output_root`) before normalization ran.

Root cause: `FIXED_ALREADY` in the corpus is a claim from the author, not
a verified fact about current main. The repair-plan generator treated it
as a guarantee rather than a hypothesis to verify.

**Correct behavior**

Every `FIXED_ALREADY` entry must be verified against current main's
production code and test file before the repair-plan flow closes it as
no-op. Verification requires either a passing regression test or direct
code inspection. The corpus classification is a hypothesis to verify,
not a fact to accept.

**Rule**

> A repair plan that shows zero production code changes against a
> `FIXED_ALREADY` task is a classification signal, not a closed case.
> Any repair-plan output with no production changes against a `FIXED_ALREADY`
> entry must trigger reclassification review before the task is marked
> complete.

**Reference**

- `docs/codex_remediation_wave2_closeout.md` — Section: rgr-319
- PR #333: `fix: align repair plan suggested test names` — root-cause fix
- `scripts/local/run_autocoder_batch.py:515` — pre-loop normalization added

---

## Entry 2: Exact test-name matching causes false-positive gaps

**What happened**

Wave 3 candidate `rgr-314-task-id-path-traversal` was marked
`NEEDS_SMALL_REPAIR_PLAN` because the corpus expected the test name
`test_task_id_sanitization_rejects_path_traversal` but current main has
`test_task_id_path_traversal_rejected`. The behavior is identical and
exhaustively tested — only the name differs. The "gap" was nomenclature,
not behavior.

The same pattern appeared in `rgr-314-stop-on-first-hold-bool` (corpus
expects `test_stop_on_first_hold_rejects_non_boolean`, current main has
`test_stop_on_first_hold_string_false_rejected`) and
`rgr-320-batch-ok-subprocess-rc` (expects `test_eval_corpus_exits_on_nonzero_subprocess_rc`,
current main has `test_eval_runner_exits_nonzero_on_batch_subprocess_failure`).

Root cause: The repair-plan generator and classification system used
exact test-name matching as the completeness criterion. A test under a
different name that covers the same behavior was treated as absent.

**Correct behavior**

Classification must be based on behavior coverage — does the test assert
the right thing about the right production code path? — not filename or
test-name string equality.

**Rule**

> Missing exact test name is not a behavior gap. Classification must be
> based on behavior coverage. A corpus-authored test name that does not
> exist in current main is a naming gap, not a missing test. Report
> COVERED / NAMING_GAP / BEHAVIOR_GAP, not YES / NO.

**Reference**

- `docs/codex_remediation_wave3_candidate_classification.md` — Section:
  "Candidate 1 — rgr-314-task-id-path-traversal"
- `tests/test_run_autocoder_batch.py:567-640` — all path-traversal behaviors
  tested with different names
- `tests/test_run_autocoder_batch.py:642-728` — bool coercion tested with
  different names

---

## Entry 3: PMG before-snapshot vs compare artifact contract

**What happened**

PR #339 repair-plan runs generated two PMG artifacts:
- `<output>_pmg_before.json` — before-snapshot, no `status` field
- `<output>_pmg_compare.json` — compare result, has `status: "clean"|"blocked"`

`final_gate_status.py` expects the compare result (has `status` field).
Passing the before-snapshot caused `HOLD_PMG_DIRTY` in an early attempt.

Root cause: The distinction between the two artifacts was not documented
as a clear integration contract. The before-snapshot is used as PMG compare
*input*, not as the guard state passed to final gates.

**Correct behavior**

- PMG before-snapshot (no `status` field) is the PMG compare *input*.
- PMG compare result (has `status: clean|blocked`) is the guard state for
  `final_gate_status.py` and `verify_final_head_merge_command.py`.
- The repair-plan generator must pass the correct file to each consumer.

**Rule**

> PMG before-snapshot is the PMG compare INPUT. PMG compare result is the
> guard state for final gates. Never pass the before-snapshot to
> final_gate_status.py — it has no `status` field and will produce
> `HOLD_PMG_DIRTY`.

**Reference**

- `scripts/local/check_persistent_mutation_guard.py` — PMG core
- `scripts/local/final_gate_status.py:837` — expects `--pmg-guard-state-json`
  with `status` field
- `tests/test_final_gate_status.py:86` — `test_pmg_dirty_result_blocks_ready_to_merge_candidate`

---

## Entry 4: Duplicate CI check names from old/new SHA runs

**What happened**

When a PR is force-pushed during CI, both the old SHA and new SHA run
checks, producing duplicate check names in `gh pr checks` output.
`wait_for_pr_ready.py` initially took the first occurrence of each check
name, which in some race conditions gave the old (stale) check result.

Root cause: `gh` does not deduplicate by name across SHA runs. A PR can
have two "test (3.11)" checks from different commit SHAs.

**Rule**

> Duplicate check handling must be current-head aware. A successful check
> from an old SHA must not satisfy a required check for the current PR head.
> If the current-head check is pending, missing, absent, or cannot be
> distinguished from stale duplicate records, the waiter must keep polling
> during the timeout window and fail closed after timeout. Completed-time
> ordering is insufficient by itself because pending current-head checks
> may not have completedAt values.

**Note**

> This log records the safer requirement. It must not be read as
> authorization to implement timestamp-only deduplication.

**Reference**

- `scripts/local/wait_for_pr_ready.py` — `poll_ci_checks` function, PR #339 fix
- `tests/test_wait_for_pr_ready.py` — duplicate check handling tests

---

## Entry 5: Stale GitHub review threads create INCONCLUSIVE state

**What happened**

PR #339 had two stale Codex P1 review comments from old SHA `0553216`.
`check_pr_review_comments.py` returned `REVIEW_COMMENTS_INCONCLUSIVE`
(`head_sha_mismatch=True` in standalone mode, but `REVIEW_COMMENTS_CLEAN`
from the waiter which passed `--repo`). Human GitHub UI resolution was
required to clear the threads.

Root cause: `check_pr_review_comments.py` fetches from multiple endpoints
(pulls/comments, pulls/reviews, issues/comments). When called from a
non-git directory without `--repo`, `gh` fails with "not a git repository".
The waiter (with `--repo`) works around this, but the standalone script
does not. This caused inconsistent `INCONCLUSIVE` vs `CLEAN` results.

**Correct behavior**

GitHub review threads must not be resolved by script or API. The policy
is: report the exact thread, the SHA it refers to, and the human UI
actions required to clear it.

**Rule**

> Do not resolve GitHub review threads via API or script. When a stale
> thread blocks the review-comment gate, report: PR number, thread ID,
> SHA it references, and step-by-step human UI cleanup instructions.
> The gate must wait for human confirmation of resolution before
> transitioning to `CLEAN`.

**Reference**

- `scripts/local/check_pr_review_comments.py` — multi-endpoint fetching
- `scripts/local/wait_for_pr_ready.py` — `REPO_CONTEXT` global, PR #339 fix
- PR #339 review-comment gate: `REVIEW_COMMENTS_CLEAN` after human cleanup

---

## Entry 6: Missing checks should poll until timeout, then fail closed

**What happened**

`wait_for_pr_ready.py` polls CI checks. Early implementation treated
any missing check as an immediate `HOLD_MISSING_CHECKS` failure. This
was wrong — during startup, checks may be pending and not yet reported.

Root cause: `gh pr checks` returns only checks that have started running.
A check that has not started yet is absent from the list, not "failed."

**Correct behavior**

- Missing checks during poll = keep polling (check may not have started).
- Missing checks at timeout = `HOLD_TIMEOUT` (fail closed — the PR has
  not confirmed CI success within the allocated time).

**Rule**

> Missing checks at timeout = hard stop. The PR must not be merged on
> an unconfirmed state. Exit with `HOLD_TIMEOUT` even if all visible
> checks passed.

**Reference**

- `scripts/local/wait_for_pr_ready.py` — `poll_ci_checks`, `STATUS_HOLD_TIMEOUT`
- `tests/test_wait_for_pr_ready.py` — timeout behavior tests

---

## Entry 7: No patch without evidence

**What happened**

The `rgr-319` repair plan was generated from a corpus classification
(`FIXED_ALREADY`) without verification. The "no-op" repair plan was
itself a signal that the classification might be wrong — but the system
did not treat this as a flag.

Root cause: The repair-plan flow had no checkpoint that compared the
corpus classification against the generated output. A zero-change plan
for a `FIXED_ALREADY` entry was silently accepted.

**Correct behavior**

Before any repair plan is finalized, compare corpus classification against
generated output:
- `FIXED_ALREADY` + zero production changes → reclassify, do not close
- `NEEDS_REPAIR` + zero production changes → flag as evidence of
  classification error

**Rule**

> No patch without evidence. Before closing a repair task, verify that
> the generated output matches the corpus classification. If it does not,
> treat the mismatch as a reclassification signal.

**Reference**

- `docs/codex_remediation_wave2_closeout.md` — Section: Operating rule
- `scripts/local/run_codex_remediation_loop.py` — repair-plan generator

---

## Entry 8: Docs-only PRs still need full gates

**What happened**

PR #338 was a docs-only evidence PR for `rgr-317` existing coverage.
CI failed (`test (3.11)` exit code 1) due to infrastructure issue,
not a code defect. `final_gate_status.py` returned `HOLD_CI_RED`.
The PR was merged manually as a process deviation.

Root cause: The infrastructure failure was not a code defect, but the
gate returned `HOLD_CI_RED` regardless of root cause. The decision to
override was made without a written decision record.

**Correct behavior**

`HOLD_CI_RED` is a hard stop. Override requires a written decision record
in the PR or a linked issue. The override must document why the CI
failure is not a code defect.

**Rule**

> Docs-only is not a gate bypass. `HOLD_CI_RED` is a hard stop for all
> PR types. Override requires a written decision record. Process
> deviation for PR #338 is documented in
> `docs/pr_readiness_waiter_design.md` Section 10.

**Reference**

- `docs/pr_readiness_waiter_design.md` — Section 10: Gate semantics
- PR #338: `docs: record rgr317 existing coverage evidence`
- `scripts/local/final_gate_status.py` — `HOLD_CI_RED` hard stop

---

## Entry 9: Codex finding lifecycle should be machine-readable

**What happened**

The corpus classifies findings with free-text strings like
`already_fixed_needs_regression_test` and `FIXED_ALREADY`. There is no
formal state machine for a finding's lifecycle:
`REPORTED → REVIEWED → STALE → RESOLVED → RECLASSIFIED`.

This caused:
- Stale findings to block merges incorrectly (`HOLD_REVIEW_COMMENTS_BLOCKED`
  on resolved threads)
- `FIXED_ALREADY` entries to be accepted without verification
- Wave 3 candidates to be classified `NEEDS_SMALL_REPAIR_PLAN` despite
  covered behavior

**Correct behavior**

A Codex finding lifecycle registry is needed — a structured document or
JSON schema that tracks each finding's state, source SHA, current-main
verification, evidence, and next action.

**Rule**

> Every Codex finding must have a machine-readable lifecycle state.
> The registry must track: finding ID, source corpus SHA, current-main
> SHA at classification, classification type, evidence (test name or
> code reference), lifecycle state, and next action. State transitions
> must be explicit and auditable.

**Reference**

- `docs/codex_remediation_wave3_candidate_classification.md` — Section:
  Operating rules for Wave 3
- `docs/codex_remediation_rgr317_existing_coverage.md` — example evidence
  format
- Future: `docs/codex_finding_lifecycle_registry.md` (planned)

---

## Future Fixes

The following improvements address gaps identified in this log:

| Item | Description | Type |
|---|---|---|
| Current-main evidence audit helper | Takes corpus JSON + task_id, inspects production code + tests, reports `COVERED` / `NAMING_GAP` / `BEHAVIOR_GAP` / `NOT_FOUND`. Prevents false `FIXED_ALREADY` and false `NEEDS_SMALL_REPAIR_PLAN`. | INFRA_GATE |
| Codex finding lifecycle registry | Structured JSON schema tracking `REPORTED → REVIEWED → STALE → RESOLVED → RECLASSIFIED` states. Enables automated classification against current main. | REGISTRY_UPDATE |
| Stale-thread human waiver protocol | Documents exact UI actions required to clear stale Codex P1 threads. Integrates into review-comment gate as a reported-but-waivable flag. | DOCS_EVIDENCE |
| Repair-plan preflight gate | Before repair-plan generation: verify corpus classification against current-main evidence. Fail-fast on `FIXED_ALREADY` + zero changes. | INFRA_GATE |
| PR type taxonomy | Formal taxonomy: `DOCS_EVIDENCE`, `INFRA_GATE`, `REPAIR_TEST_ONLY`, `REPAIR_PRODUCTION`, `REGISTRY_UPDATE`. Enables type-specific gate policies (e.g., `DOCS_EVIDENCE` still needs `HOLD_CI_RED`). | DOCS_EVIDENCE |

---

*Log maintained as part of AED Codex remediation. Entries are added
after each wave closes. Do not delete historical entries.*