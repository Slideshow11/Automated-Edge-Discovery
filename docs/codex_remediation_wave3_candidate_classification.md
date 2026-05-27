# Wave 3 Candidate Classification — Codex Remediation

**Date:** 2026-05-27
**Main HEAD:** `754a66c` (fix: make PR readiness waiter repo-context independent #339)
**Source:** `corpus/codex-remediation-pr314-320.json` (Wave 1, 18 FIXED_ALREADY entries)
**Scope:** Every candidate verified against current main before classification

---

## Classification methodology

1. Identify the corpus task's `test_pattern` (exact test name the corpus expects)
2. Search current main's test files for an exact-match test name
3. If no exact match, search for a similar test that covers the same behavior
4. Classify based on what current main actually has
5. Do not guess — if evidence is inconclusive, mark NEEDS_HUMAN_REVIEW

---

## Candidate 1: rgr-314-task-id-path-traversal

**Corpus claim:** Codex identified a real P1 path traversal vulnerability in task_id sanitization. Fix was added in `e60e3b5` using `re.fullmatch`. A regression test ensures it stays.

**Corpus test pattern:** `test_task_id_sanitization_rejects_path_traversal`

**Current main test search:**
- Exact match: ❌ NOT FOUND — `test_task_id_sanitization_rejects_path_traversal` does not exist in any test file
- Related: `test_task_id_path_traversal_rejected` (line 567) — tests `../`, `/tmp`, etc. rejection in task_id field
- Related: `test_unsafe_path_traversal_in_task_output_root` (line 557) — tests output_root path traversal

**Gap:** The corpus expects `test_task_id_sanitization_rejects_path_traversal`. Current main has no test with this exact name. Related tests cover path traversal for task_id and output_root separately.

**Classification:** `NEEDS_SMALL_REPAIR_PLAN` (add regression test only, no production code change needed)

**Recommended action:** Generate a repair plan for a regression test named `test_task_id_sanitization_rejects_path_traversal` in `tests/test_run_autocoder_batch.py`. Test must pass against current main without modifying production code. The fix (`re.fullmatch` sanitization) is already present in production code.

---

## Candidate 2: rgr-314-stop-on-first-hold-bool

**Corpus claim:** Codex identified a type-safety bug where non-boolean values for `stop_on_first_hold` could cause type errors or be treated as truthy. Fix was added in `e60e3b5` using `isinstance(bool)` check.

**Corpus test pattern:** `test_stop_on_first_hold_rejects_non_boolean`

**Current main test search:**
- Exact match: ❌ NOT FOUND — `test_stop_on_first_hold_rejects_non_boolean` does not exist
- Related: `test_stop_on_first_hold_string_false_rejected` (line 690) — tests that `"false"` string is rejected
- Related: `test_stop_on_first_hold_false_bool_works` (line 643) — tests `False` bool works
- Related: `test_stop_on_first_hold_true_bool_works` (line 667) — tests `True` bool works

**Gap:** The corpus expects a test named `test_stop_on_first_hold_rejects_non_boolean` that covers non-boolean types broadly. Current tests cover string `"false"` and booleans, but not e.g. integer `1`, list `[False]`, or dict `{"stop": true}`.

**Classification:** `NEEDS_SMALL_REPAIR_PLAN` (add regression test only, no production code change needed)

**Recommended action:** Generate a repair plan for a regression test named `test_stop_on_first_hold_rejects_non_boolean` in `tests/test_run_autocoder_batch.py`. Test must pass against current main without modifying production code. The `isinstance(bool)` check is already present in production code.

---

## Candidate 3: rgr-320-batch-ok-subprocess-rc

**Corpus claim:** Codex identified that the eval runner would consume stale batch_status.json on subprocess failure. The rc guard was added in `e60e3b5`.

**Corpus test pattern:** `test_eval_corpus_exits_on_nonzero_subprocess_rc`

**Current main test search:**
- Exact match: ❌ NOT FOUND — `test_eval_corpus_exits_on_nonzero_subprocess_rc` does not exist
- Related: `test_eval_runner_exits_nonzero_on_batch_subprocess_failure` (line 502 in test_run_autocoder_eval_corpus.py) — tests that `run_autocoder_eval_corpus.py` exits non-zero when the batch subprocess fails

**Gap:** The corpus expects `test_eval_corpus_exits_on_nonzero_subprocess_rc`. The current test `test_eval_runner_exits_nonzero_on_batch_subprocess_failure` covers the same behavior (non-zero exit on subprocess failure) but not with the exact name. Also, `TestInvokeBatchControllerRCGuard` (line 490) covers the rc guard in the batch controller.

**Classification:** `NEEDS_SMALL_REPAIR_PLAN` (add regression test only, no production code change needed)

**Recommended action:** Generate a repair plan for a regression test named `test_eval_corpus_exits_on_nonzero_subprocess_rc` in `tests/test_run_autocoder_eval_corpus.py`. The existing `test_eval_runner_exits_nonzero_on_batch_subprocess_failure` already covers the behavior — the new test can reference it as a model. Alternatively, add a rename alias or rename the existing test.

---

## Candidate 4: rgr-320-base-sha-catfile

**Corpus claim:** Codex misidentified `cat-file` usage. `validate_corpus_targets` uses `cat-file -e sha:path` correctly for file existence. `resolve_base_sha` uses `rev-parse --verify` correctly. No bug. Regression test needed.

**Corpus test pattern:** implied — testing that `cat-file -e sha:path` and `rev-parse --verify` are used correctly

**Current main test search:**
- Test exists: `test_catfile_sha_path_correct_format` (line 445, `test_run_autocoder_eval_corpus.py`) — tests that a file absent from a valid SHA fails with expected error, confirming the `cat-file -e sha:path` pattern works

**Assessment:** This test covers the behavior described by the corpus — that sha:path format works correctly. However, the corpus was filed as `FIXED_ALREADY needs_regression_test` and the test was supposed to be added. `test_catfile_sha_path_correct_format` was added as part of an earlier PR (test coverage improved during review). It covers the `cat-file -e sha:path` behavior.

**Classification:** `ALREADY_FIXED_WITH_TEST`

**Recommended action:** No repair needed. Evidence note confirming `test_catfile_sha_path_correct_format` covers the regression. Confirm no duplicate test name gap.

---

## Candidate 5: test_batch_invokes_parent_script_with_repo_root (rgr-317 adjacent)

**Corpus claim:** `--repo-root` must be propagated to the stage-2 executor. Fixed in PR #317, regression test should exist.

**Corpus test pattern:** implied — testing that `--repo-root` is passed with exact equality to `task_worktree_path`

**Current main test search:**
- Test exists: `test_batch_invokes_parent_script_with_repo_root` (line 1185, `test_run_autocoder_batch.py`) — explicitly asserts `--repo-root` in argv and exact equality with `task_worktree_path_str`

**Classification:** `ALREADY_FIXED_WITH_TEST`

**Recommended action:** No repair needed. This was documented in PR #338 (rgr-317 evidence). Confirms the fix has regression coverage.

---

## Candidate 6: wait_for_pr_ready.py — exit code 8 and duplicate CI check handling (PR #337 adjacent)

**Finding source:** PR #337 review-comment (Codex P1 thread `PRRT_kwDOSHFpYM6FLUBP`)

**Claim:** `gh pr checks` exit code 8 (pending checks) was being treated as a fatal error. Also, duplicate CI check runs from old+new SHA caused confusion.

**Current main status:**
- Exit code 8: Fixed in `gh_run()` with `check=False` and `returncode not in (0, 8)` as the error condition
- Duplicate CI checks: Fixed by ignoring duplicate check names (taking the first occurrence)
- Tests: `TestRepoContextAndCwd` (6 tests), `TestFinalGates` (3 tests) in `test_wait_for_pr_ready.py`

**Classification:** `ALREADY_FIXED_WITH_TEST`

**Recommended action:** No repair needed. PR #339 fixed these and added 9 regression tests. This is confirmed fixed with test coverage.

---

## Candidate 7: PMG schema regression (PR #339 adjacent)

**Finding source:** Internal review of `wait_for_pr_ready.py` output files

**Claim:** `final_gate_status.py` expects `status_pmg_compare.json` (with `status: "clean"|"blocked"`), not `status_pmg_before.json` (snapshot, no `status` field). Passing the wrong file causes `HOLD_PMG_DIRTY`.

**Current main status:**
- Fixed in PR #339: `run_pmg_compare()` now passes `pmg_compare_json` (not `pmg_before_json`) to `run_final_gate_status()` and `run_merge_ready_verifier()`
- Tests: `test_pmg_compare_json_is_used_for_final_gate_status`, `test_pmg_dirty_result_blocks_ready_to_merge_candidate` in `TestFinalGates`

**Classification:** `ALREADY_FIXED_WITH_TEST`

**Recommended action:** No repair needed. Confirmed fixed with regression tests.

---

## Candidate 8: output_root null normalization (rgr-319 adjacent)

**Finding source:** PR #319 review-comment + repair-plan flow

**Claim:** `validate_task_constraints` called before `_normalize_task_packet`, causing `HOLD_TASK_PACKET_INVALID` for null output_root.

**Current main status:** Fixed in PR #334. Test: `test_output_root_null_normalized_before_validation` (line 767).

**Classification:** `ALREADY_FIXED_WITH_TEST`

**Recommended action:** No repair needed. Confirmed fixed with regression test.

---

## Wave 3 classification table

| Candidate | Corpus test pattern | Current main test | Classification | Next action |
|---|---|---|---|---|
| `rgr-314-task-id-path-traversal` | `test_task_id_sanitization_rejects_path_traversal` | NOT FOUND (related: `test_task_id_path_traversal_rejected`) | `NEEDS_SMALL_REPAIR_PLAN` | Generate regression test repair plan |
| `rgr-314-stop-on-first-hold-bool` | `test_stop_on_first_hold_rejects_non_boolean` | NOT FOUND (related: `test_stop_on_first_hold_string_false_rejected`) | `NEEDS_SMALL_REPAIR_PLAN` | Generate regression test repair plan |
| `rgr-320-batch-ok-subprocess-rc` | `test_eval_corpus_exits_on_nonzero_subprocess_rc` | NOT FOUND (related: `test_eval_runner_exits_nonzero_on_batch_subprocess_failure`) | `NEEDS_SMALL_REPAIR_PLAN` | Generate regression test repair plan |
| `rgr-320-base-sha-catfile` | implied (cat-file sha:path) | `test_catfile_sha_path_correct_format` ✅ | `ALREADY_FIXED_WITH_TEST` | No action |
| `test_batch_invokes_parent_script_with_repo_root` | `--repo-root exact equality` | `test_batch_invokes_parent_script_with_repo_root` ✅ | `ALREADY_FIXED_WITH_TEST` | No action (PR #338) |
| `wait_for_pr_ready.py` exit-code-8 | gh checks exit code 8 | 9 regression tests in `TestRepoContextAndCwd` + `TestFinalGates` ✅ | `ALREADY_FIXED_WITH_TEST` | No action (PR #339) |
| PMG schema regression | pmg_compare_json vs pmg_before_json | `test_pmg_compare_json_is_used_for_final_gate_status` ✅ | `ALREADY_FIXED_WITH_TEST` | No action (PR #339) |
| `rgr-319` output_root null | null output_root before normalization | `test_output_root_null_normalized_before_validation` ✅ | `ALREADY_FIXED_WITH_TEST` | No action (PR #334) |

---

## Operating rules for Wave 3

1. **No repair until classification is confirmed.** Every candidate in the table above must be verified against current main before a repair plan is generated.

2. **`ALREADY_FIXED_WITH_TEST` means no code change.** The test confirms the fix is live. Document it and move on.

3. **`NEEDS_SMALL_REPAIR_PLAN` means regression test only.** Production code is already correct. The fix may have been added in a different PR than expected. The repair plan adds a test with the exact corpus-expected name.

4. **`FIXED_ALREADY` claims must be verified.** Wave 2's rgr-319 showed that a `FIXED_ALREADY` classification can be wrong in both directions. Every claim must be tested against current main.

5. **Test name exact match matters.** The corpus specifies exact `test_pattern` names. A similar test under a different name is not the same as having the expected test. The gap is the name, not the behavior.

---

## Wave 3 candidates requiring repair-plan generation

Priority order for repair-plan generation:

1. **`rgr-314-task-id-path-traversal`** — `test_task_id_sanitization_rejects_path_traversal` missing, P1 severity
2. **`rgr-314-stop-on-first-hold-bool`** — `test_stop_on_first_hold_rejects_non_boolean` missing, P1 severity
3. **`rgr-320-batch-ok-subprocess-rc`** — `test_eval_corpus_exits_on_nonzero_subprocess_rc` missing, P1 severity

All three are regression-test-only (production code is already correct). Repair plans should be generated in `one-task-repair-plan` mode targeting `tests/test_run_autocoder_batch.py` or `tests/test_run_autocoder_eval_corpus.py` as appropriate.

Do NOT run live Claude, do NOT run autocoder batch, do NOT merge repair PRs without review-comment gate + final gates passing.

---

## Wave 3 confirmed no-action items

The following are confirmed ALREADY_FIXED_WITH_TEST. No repair plan, no evidence note, no further action:

- `rgr-317-repo-root-propagation` ✅ (PR #338)
- `rgr-319-output-root-null-normalization` ✅ (PR #334)
- `wait_for_pr_ready.py` exit-code-8 + duplicate CI handling ✅ (PR #339)
- PMG schema regression ✅ (PR #339)
- `rgr-320-base-sha-catfile` ✅ (test_run_autocoder_eval_corpus.py:445)
- `rgr-320-no-newline-marker` ✅ (FALSE_POSITIVE_WITH_EVIDENCE, docs/codex_remediation_corpus_design.md Section 11)
- `doc-323-applied-status-name` ✅ (FIXED_ALREADY docs-only)
- `doc-323-enable-real-claude-executor-claim` ✅ (FIXED_ALREADY docs-only)