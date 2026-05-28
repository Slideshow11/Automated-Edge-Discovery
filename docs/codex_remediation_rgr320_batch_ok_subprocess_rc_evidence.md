# rgr-320-batch-ok-subprocess-rc — Evidence Audit

## 1. Candidate ID

**rgr-320-batch-ok-subprocess-rc**

## 2. Corpus Source File

`corpus/codex-remediation-pr314-320.json`
Corpus kind: `aed.codex_remediation.corpus.v0`
Corpus version: `0.1.0`
Created: 2026-05-26T00:00:00Z

## 3. Original Claim (from corpus entry)

> **finding_summary:** Eval runner logs batch_ok but reads batch_status.json regardless of subprocess rc — stale status consumed
>
> **current_main_status:** Fixed in commit e60e3b5 (PR #320). run_autocoder_eval_corpus.py line 599 now exits immediately on non-zero rc: `if rc != 0: return 1`
>
> **classification:** FIXED_ALREADY
>
> **task_category:** already_fixed_needs_regression_test
>
> **finding_id:** codex-2cc61202297f
>
> **severity:** P1

## 4. Production subprocess/return-code Evidence

**File:** `scripts/local/run_autocoder_eval_corpus.py`

### `invoke_batch_controller` (lines 261–280)

```python
result = subprocess.run(
    argv,
    capture_output=True,
    text=True,
    cwd=str(REPO_ROOT),
    timeout=600,
)
combined = result.stdout + result.stderr
return result.returncode == 0, combined, result.returncode
```

Returns: `(batch_exited_zero, combined_stdout_stderr, exit_code)`

### Guard at Step 7 (lines 585–599)

```python
batch_ok, combined, rc = invoke_batch_controller(batch_packet, out_root)
if rc != 0:
    print(f"FATAL: batch subprocess exited nonzero (rc={rc}), cannot read stale batch_status.json as success", file=sys.stderr)
    # ... write failure eval_report ...
    eval_report["batch_subprocess_failure_reason"] = (
        f"batch subprocess exited rc={rc}; "
        f"stale batch_status.json was NOT treated as success"
    )
    write_eval_run_metadata(out_root, corpus, base_sha, False, run_id)
    print(f"❌ eval_pass=False — batch_subprocess_rc={rc}", file=sys.stderr)
    return 1   # <-- exits 1 immediately, does NOT read batch_status.json
```

**Critical behavior:** When `rc != 0`, the function returns `1` immediately. `batch_status.json` is **not read** — no stale OK status is consumed.

## 5. Existing Test Evidence

**File:** `tests/test_run_autocoder_eval_corpus.py`
**Test class:** `TestInvokeBatchControllerRCGuard` (lines 491–609)

**Test:** `test_eval_runner_exits_nonzero_on_batch_subprocess_failure`

Verifies:
1. `main()` returns `1` when `invoke_batch_controller` returns nonzero rc (42)
2. `report["eval_pass"]` is `False`
3. `report["batch_subprocess_rc"]` is `42`
4. `report["batch_subprocess_failure_reason"]` contains "stale batch_status.json was NOT treated as success"
5. Markdown report contains "Batch Subprocess Failure" section

The test is structured to mock `invoke_batch_controller` to return `(False, "batch subprocess died", 42)`, then verifies the full failure path including the stale-data guard message.

## 6. Current Behavior Matrix

| Scenario | Behavior | Exit code |
|---|---|---|
| `rc == 0` (batch subprocess succeeds) | Reads `batch_status.json`, continues to Step 8 | 0 on eval_pass, 1 on eval_fail |
| `rc != 0` (batch subprocess fails/crashes) | Writes failure eval_report, marks `eval_pass=False`, **returns 1 immediately** | `1` |
| `rc != 0` + stale `batch_status.json` with `status: BATCH_READY` | Stale `batch_status.json` **not consumed** — guard fires first | `1` |
| `rc == 0` + `batch_status.json` readable | Normal path: evaluates `batch_status` and computes `eval_pass` | 0 or 1 |

## 7. Classification

**ALREADY_FIXED_WITH_TEST**

The fix was introduced in commit `e60e3b5` (PR #320). A regression test `test_eval_runner_exits_nonzero_on_batch_subprocess_failure` exists in `tests/test_run_autocoder_eval_corpus.py` at line 502. The test passes on current main and fully covers the vulnerability.

The corpus requested test pattern `test_eval_corpus_exits_on_nonzero_subprocess_rc` — the actual test is named `test_eval_runner_exits_nonzero_on_batch_subprocess_failure` (line 502) which provides equivalent coverage and actually has more comprehensive assertions than the corpus pattern specified.

## 8. Recommendation

**No repair needed.** The stale-status consumption bug is fixed and the fix has regression test coverage. The corpus task `rgr-320-batch-ok-subprocess-rc` is satisfied by the existing state of the codebase.

## 9. Proposed Repair Scope (if repair were needed)

Not applicable — no repair executed.

If a future regression required a fix, the one-PR scope would be:
- Regression test in `tests/test_run_autocoder_eval_corpus.py` — already done
- Verify `invoke_batch_controller` rc guard at Step 7 of `run_autocoder_eval_corpus.py` — already present
- No production code changes required

## 10. Explicit Statement

**No repair executed.** This is a docs-only evidence audit. No production code was modified. No tests were added or changed. No live Claude was invoked. No autocoder batch was run. No Hermes memory/profile/config was touched.

---

*Audit completed: 2026-05-28*
*main HEAD: d47e6ca46806ecd5578cd37b80dca61e7ffc7cec*
