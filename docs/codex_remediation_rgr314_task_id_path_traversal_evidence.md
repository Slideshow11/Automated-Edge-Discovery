# rgr-314: task_id path traversal — Evidence Audit

## 1. Candidate ID

**rgr-314-task-id-path-traversal**

## 2. Corpus Source File

`corpus/codex-remediation-pr314-320.json`
Corpus kind: `aed.codex_remediation.corpus.v0`
Corpus version: `0.1.0`
Created: 2026-05-26T00:00:00Z

## 3. Original Claim (from corpus entry)

> **finding_summary:** task_id used directly in path construction without sanitization — vulnerable to path traversal
>
> **current_main_status:** Fixed in commit e60e3b5 (PR #320). run_autocoder_batch.py line 377 now uses `re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', task_id)` before path use.
>
> **classification:** FIXED_ALREADY
>
> **task_category:** already_fixed_needs_regression_test
>
> **finding_id:** codex-f23c1e3c82d9
>
> **severity:** P1

## 4. Production Validation Evidence

**File:** `scripts/local/run_autocoder_batch.py`
**Function:** `validate_task_constraints` (lines ~358–420)

The validation runs at lines 377–383:

```python
# --- task_id path-traversal sanitization ---
import re
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", task_id):
    return False, (
        f"tasks[{i}] has invalid task_id: '{task_id}'. "
        f"Must match ^[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}$ — "
        f"no path separators, no '..', no absolute paths."
    )
```

The regex requires:
- First character: `[A-Za-z0-9]` — must be alphanumeric (no slash, no dot, no hyphen start)
- Remaining up to 127 chars: `[A-Za-z0-9._-]` — alphanumeric, dot, underscore, hyphen only
- Total max length: 128 chars

This is applied **before** any path construction or output_root use. The check is the first validation after packet_kind, before uniqueness checks or output_root checks.

**Also:** The `not task_id` check (lines 390–391) catches empty string separately:
```python
if not task_id:
    return False, f"tasks[{i}] is missing task_id"
```

## 5. Existing Test Evidence

**File:** `tests/test_run_autocoder_batch.py`
**Test class:** `TestTaskIdValidation`

Tests at lines 567–640:

| Test | Input | Expected status |
|---|---|---|
| `test_task_id_path_traversal_rejected` | `../../../tmp/aed_escaped` | HOLD_TASK_PACKET_INVALID |
| `test_task_id_dotdot_rejected` | `../escape` | HOLD_TASK_PACKET_INVALID |
| `test_task_id_absolute_path_rejected` | `/tmp/escape` | HOLD_TASK_PACKET_INVALID |
| `test_task_id_valid_still_works` | `task-valid-001.a_b`, `Task-Valid-002` | NOT HOLD_TASK_PACKET_INVALID |

All four tests exist and pass on current main.

## 6. Behavior Matrix for Invalid task_id Examples

| task_id | Rejected? | Why | Status returned |
|---|---|---|---|
| `../evil` | ✅ Yes | fails `re.fullmatch` — dot not in `[A-Za-z0-9._-]` | HOLD_TASK_PACKET_INVALID |
| `/tmp/evil` | ✅ Yes | fails `re.fullmatch` — slash not in allowed charset | HOLD_TASK_PACKET_INVALID |
| `a/b` | ✅ Yes | fails `re.fullmatch` — slash not allowed | HOLD_TASK_PACKET_INVALID |
| `a\b` | ✅ Yes | fails `re.fullmatch` — backslash not in charset | HOLD_TASK_PACKET_INVALID |
| `.` | ✅ Yes | fails `re.fullmatch` — dot only is not alphanumeric first | HOLD_TASK_PACKET_INVALID |
| `..` | ✅ Yes | fails `re.fullmatch` — dotdot not alphanumeric first | HOLD_TASK_PACKET_INVALID |
| `""` (empty) | ✅ Yes | caught by `not task_id` check before regex | HOLD_TASK_PACKET_INVALID |

## 7. Classification

**ALREADY_FIXED_WITH_TEST**

The fix was introduced in commit `e60e3b5` (PR #320). A regression test `test_task_id_path_traversal_rejected` exists in `tests/test_run_autocoder_batch.py` at line 567. The test passes on current main. The exact corpus test pattern `test_task_id_sanitization_rejects_path_traversal` is not the name of any single test, but the coverage provided by the existing four-test suite (path traversal, dotdot, absolute path, valid happy-path) fully covers the vulnerability and would fail if the `re.fullmatch` guard were removed.

## 8. Recommendation

**No repair needed.** The vulnerability is fixed and the fix has regression test coverage. The corpus task `rgr-314-task-id-path-traversal` is satisfied by the existing state of the codebase.

## 9. Proposed Repair Scope (if repair were needed)

Not applicable — no repair executed.

If a future regression required a fix, the one-PR scope would be:
- Add regression test(s) in `tests/test_run_autocoder_batch.py` — already done
- Verify `validate_task_constraints` contains the `re.fullmatch` guard — already done
- No production code changes required

## 10. Explicit Statement

**No repair executed.** This is a docs-only evidence audit. No production code was modified. No tests were added or changed. No live Claude was invoked. No autocoder batch was run. No Hermes memory/profile/config was touched.

---

*Audit completed: 2026-05-28*
*main HEAD: b87d91b5ec39cd11baf2c2ba9f71484206513fac*
