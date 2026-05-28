# Wave 3 Codex Remediation Closeout

**main HEAD:** `529a443715f7d616a9ec4fb3bc3a85e501a3bc5f`  
**Closed:** 2026-05-28

---

## Scope

Wave 3 comprised three candidates from `corpus/codex-remediation-pr314-320.json` that required either repair execution or evidence audit:

| Candidate | Source PR | Severity |
|---|---|---|
| `rgr-314-task-id-path-traversal` | PR #314 | P1 |
| `rgr-314-stop-on-first-hold-bool` | PR #314 | P2 |
| `rgr-320-batch-ok-subprocess-rc` | PR #320 | P1 |

A fourth candidate, `rgr-320-no-newline-marker`, was classified `FALSE_POSITIVE_WITH_EVIDENCE` in Wave 2. A fifth, `rgr-320-base-sha-catfile`, was classified `ALREADY_FIXED_WITH_TEST` in Wave 3 candidate classification and requires no further action.

---

## PR #343 — fix: validate stop_on_first_hold type in batch packets

| Field | Value |
|---|---|
| PR | [#343](https://github.com/Slideshow11/Automated-Edge-Discovery/pull/343) |
| State | **MERGED** |
| Merged at | 2026-05-28T03:01:51Z |
| Merge commit | `b87d91b5ec39cd11baf2c2ba9f71484206513fac` |
| Classification | FIXED_ALREADY (production fix from PR #320 already in place; PR #343 added regression test) |

**What was done:** Production code was already fixed in commit `e60e3b5` (PR #320) — `isinstance(stop_on_first_hold_raw, bool)` with explicit `ValueError` at lines 489–494 of `run_autocoder_batch.py`. PR #343 added regression test `test_stop_on_first_hold_rejects_non_boolean` in `tests/test_run_autocoder_batch.py`.

**Classification:** `ALREADY_FIXED_WITH_TEST`

---

## PR #344 — docs: audit rgr314 task id path traversal evidence

| Field | Value |
|---|---|
| PR | [#344](https://github.com/Slideshow11/Automated-Edge-Discovery/pull/344) |
| State | **MERGED** |
| Merged at | 2026-05-28T11:41:16Z |
| Merge commit | `d47e6ca46806ecd5578cd37b80dca61e7ffc7cec` |
| Docs file | `docs/codex_remediation_rgr314_task_id_path_traversal_evidence.md` |

**Evidence summary:** Production code was already fixed in commit `e60e3b5` (PR #320) — `re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', task_id)` at `run_autocoder_batch.py` line 377. Regression tests exist in `TestTaskIdValidation` (lines 567–640 of `tests/test_run_autocoder_batch.py`) covering `../`, `/tmp/`, slash, backslash, dotdot, absolute paths, and valid cases.

**Classification:** `ALREADY_FIXED_WITH_TEST`

---

## PR #345 — docs: audit rgr320 subprocess return code evidence

| Field | Value |
|---|---|
| PR | [#345](https://github.com/Slideshow11/Automated-Edge-Discovery/pull/345) |
| State | **MERGED** |
| Merged at | 2026-05-28T12:05:00Z |
| Merge commit | `529a443715f7d616a9ec4fb3bc3a85e501a3bc5f` |
| Docs file | `docs/codex_remediation_rgr320_batch_ok_subprocess_rc_evidence.md` |

**Evidence summary:** Production code was already fixed in commit `e60e3b5` (PR #320) — `if rc != 0: return 1` at `run_autocoder_eval_corpus.py` lines 599–612. When the batch subprocess returns non-zero rc, the eval runner writes a failure eval_report and exits `1` without reading `batch_status.json`. Regression test `test_eval_runner_exits_nonzero_on_batch_subprocess_failure` exists in `tests/test_run_autocoder_eval_corpus.py` at line 502.

**Classification:** `ALREADY_FIXED_WITH_TEST`

---

## Final Classification Summary

| Candidate | Classification | PR | Action |
|---|---|---|---|
| `rgr-314-task-id-path-traversal` | `ALREADY_FIXED_WITH_TEST` | #344 (evidence) | Docs-only audit |
| `rgr-314-stop-on-first-hold-bool` | `ALREADY_FIXED_WITH_TEST` | #343 (fix+test) | Production already fixed; regression test added |
| `rgr-320-batch-ok-subprocess-rc` | `ALREADY_FIXED_WITH_TEST` | #345 (evidence) | Docs-only audit |
| `rgr-320-no-newline-marker` | `FALSE_POSITIVE_WITH_EVIDENCE` | Wave 2 | No action |
| `rgr-320-base-sha-catfile` | `ALREADY_FIXED_WITH_TEST` | Wave 3 (classification) | No action; no regression test added |

**All tracked Wave 3 candidates are closed.**

---

## Tests and Checks Run

| Check | Result |
|---|---|
| `tests/test_run_autocoder_batch.py` | 61 passed ✅ |
| `tests/test_run_autocoder_eval_corpus.py::TestInvokeBatchControllerRCGuard` | 1 passed ✅ |
| `tests/test_run_autocoder_eval_corpus.py::TestBuildBatchPacket` + `TestValidateCorpusSchema` + `TestSanitizeRunId` | 9 passed ✅ |
| `tests/test_final_gate_status.py` | 86 passed ✅ |
| `python3 -m compileall scripts/local tests -q` | Clean ✅ |
| `git diff --check` | Clean ✅ |
| Worktree | Clean ✅ |

**Note:** `tests/test_run_autocoder_eval_corpus.py::TestEvalCorpusSmoke::test_runner_produces_eval_pass_true` was failing during this audit. This is a pre-existing issue in `main` unrelated to Wave 3 — it uses a corpus fixture with mock tasks that produce `HOLD_EXECUTION_NOT_PATCH_READY`, causing `eval_pass=False`. The test was not modified as part of this closeout (no test changes permitted). The regression tests for Wave 3 candidates (`TestInvokeBatchControllerRCGuard`, `TestTaskIdValidation`) all pass.

---

## Operational Lesson

During this audit session, using terminal-only commands (`git grep`, `sed`, Python scripts with `sed`-capped output) proved reliable and bounded. The `search_files` and `read_file` tools (Humphry layer) were avoided as prescribed. In this repo's AED codebase (large, mixed Python/shell), terminal commands with explicit `sed` output caps and timeouts provided complete audit coverage without context flooding.

Guideline: For AED repo audits, prefer `git grep` + `sed` over `search_files`/`read_file` to maintain deterministic bounded output and avoid tool-layer freezes on large repos.

---

## Explicit Statements

- No production code was modified in this closeout.
- No tests were added or changed in this closeout.
- No live Claude was invoked.
- No autocoder batch was run.
- No Hermes memory/profile/config was touched.
- No repair was executed during this closeout.

---

*Closeout completed: 2026-05-28*
*main HEAD: 529a443715f7d616a9ec4fb3bc3a85e501a3bc5f*
*No tracked Wave 3 candidate remains open.*
