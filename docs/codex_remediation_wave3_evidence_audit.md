# Wave 3 Evidence Audit — AED Codex Remediation

**Date:** 2026-05-28
**Main HEAD:** `4c0b3431b2d0faad04ddf961d232a73df012a179`
**Audit scope:** Three Wave 3 candidates classified `NEEDS_SMALL_REPAIR_PLAN` in
`docs/codex_remediation_wave3_candidate_classification.md` — verified against
current main before any repair plan is generated.
**Constraint:** Evidence-only. No repair plans, no code changes, no live Claude.

---

## Candidate A: rgr-314-task-id-path-traversal

### Corpus claim

Codex identified a real P1 path-traversal vulnerability in task_id
sanitization. The fix (`re.fullmatch`) was added in commit `e60e3b5` (PR #320).
Corpus expects regression test: `test_task_id_sanitization_rejects_path_traversal`.

### Production code inspected

`scripts/local/run_autocoder_batch.py` lines 375–382:

```python
import re
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", task_id):
    return False, (
        f"tasks[{i}] has invalid task_id: '{task_id}'. "
        f"Must match ^[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}$ — "
        f"no path separators, no '..', no absolute paths."
    )
```

Positive-assertion regex. All of `../`, `/tmp/escape`, `../../../tmp/aed_escaped`,
`/absolute/path` fail the match → `HOLD_TASK_PACKET_INVALID`. Fix is live.

### Existing tests inspected

`tests/test_run_autocoder_batch.py`:

| Line | Test name | Input | Expected |
|---|---|---|---|
| 567 | `test_task_id_path_traversal_rejected` | `../../../tmp/aed_escaped` | `HOLD_TASK_PACKET_INVALID` |
| 581 | `test_task_id_dotdot_rejected` | `../escape` | `HOLD_TASK_PACKET_INVALID` |
| 595 | `test_task_id_absolute_path_rejected` | `/tmp/escape` | `HOLD_TASK_PACKET_INVALID` |
| 609 | `test_task_id_valid_still_works` | `task-valid-001.a_b` | Pass |

All four tests exist and pass. The behavior described by the corpus
(`task_id path traversal rejected`) is fully covered under four different
test names.

### Coverage gap analysis

| Aspect | Corpus expectation | Current main |
|---|---|---|
| Test name | `test_task_id_sanitization_rejects_path_traversal` | NOT FOUND |
| Behavior: path separators rejected | `../`, `..`, `/` in task_id | ✅ Covered by 3 tests |
| Behavior: valid task_id still works | alphanumeric with dots/underscores/hyphens | ✅ Covered |
| Production fix (re.fullmatch) | Present in batch.py:377 | ✅ Present |

The behavior is exhaustively tested. The gap is the **test name**, not the
behavior. The corpus expects a single test with a specific name; current main
has four tests covering the same behavior under different names.

### Classification

**`ALREADY_FIXED_WITH_TEST`**

Confidence: HIGH. The production fix (`re.fullmatch` at batch.py:377) is live.
Four tests cover path-traversal rejection for task_id. The corpus's single
expected test name does not exist, but behavior is covered.

### Recommended next action

No repair plan needed for behavior. Option to add a test named
`test_task_id_sanitization_rejects_path_traversal` as an alias/naming
convergence, but the behavior is already protected. If the corpus author
intended a specific test name as an anchor, a docs-only evidence note
("test exists under different name, behavior confirmed") is sufficient.
Do NOT add redundant tests that assert the same behavior.

---

## Candidate B: rgr-314-stop-on-first-hold-bool

### Corpus claim

Codex identified a type-safety bug where non-boolean `stop_on_first_hold`
values could cause type errors or be treated as truthy. The fix
(`isinstance(bool)` check) was added in commit `e60e3b5` (PR #320).
Corpus expects: `test_stop_on_first_hold_rejects_non_boolean`.

### Production code inspected

`scripts/local/run_autocoder_batch.py` lines 489–494:

```python
stop_on_first_hold_raw = batch_packet.get("stop_on_first_hold", True)
if isinstance(stop_on_first_hold_raw, bool):
    stop_on_first_hold: bool = stop_on_first_hold_raw
else:
    raise ValueError(
        f"stop_on_first_hold must be a bool, got {type(stop_on_first_hold_raw).__name__}: "
        f"{stop_on_first_hold_raw!r}. "
        f'String "false" is not accepted; use boolean false explicitly.'
    )
```

`isinstance(bool)` check. Any non-bool type (string, int, list, dict, None)
raises `ValueError` (lines 492–496) *inside* `run_autocoder_batch()`, *after*
`validate_batch_packet()` returns. Because the `ValueError` is raised in
`run_autocoder_batch()` (line 941–945) and not inside `validate_batch_packet()`,
it is not caught by any local `return False, "..."`. Instead it propagates
to `main()`'s `except Exception as e:` (line 950), which writes
`status: State.HOLD_UNKNOWN`. The actual result is `HOLD_UNKNOWN`,
not `HOLD_BATCH_PACKET_INVALID`.

### Existing tests inspected

`tests/test_run_autocoder_batch.py`:

| Line | Test name | Input | Expected |
|---|---|---|---|
| 643 | `test_stop_on_first_hold_false_bool_works` | `False` (bool) | Pass |
| 667 | `test_stop_on_first_hold_true_bool_works` | `True` (bool) | Pass |
| 690 | `test_stop_on_first_hold_string_false_rejected` | `"false"` (string) | `HOLD_BATCH_PACKET_INVALID` with "bool" in error |

String `"false"` is tested as rejected. However, the rejection produces
`HOLD_UNKNOWN` (via `main()`'s `except Exception`), not the structured
`HOLD_BATCH_PACKET_INVALID` that `validate_batch_packet` returns for other
batch-level validation failures. The `ValueError` is raised *after*
`validate_batch_packet()` in the `run_autocoder_batch()` function body
(around line 487), so it bypasses the validation-return path and falls
into the broad exception handler.

### Coverage gap analysis

| Aspect | Corpus expectation | Current main |
|---|---|---|
| Test name | `test_stop_on_first_hold_rejects_non_boolean` | NOT FOUND |
| Bool `False` works | boolean `False` | ✅ Covered |
| Bool `True` works | boolean `True` | ✅ Covered |
| String `"false"` rejected | `"false"` | ⚠️ Covered but produces `HOLD_UNKNOWN`, not `HOLD_BATCH_PACKET_INVALID` |
| Integer `1` rejected | `1` (int) | NOT tested explicitly |
| List `[False]` rejected | `[False]` (list) | NOT tested explicitly |
| Dict `{"stop": true}` rejected | `{"stop": true}` (dict) | NOT tested explicitly |
| Production fix (`isinstance(bool)`) | Present in batch.py:489 | ✅ Present, but raises ValueError not captured by validate_batch_packet |

The `isinstance(bool)` check at batch.py:489 raises `ValueError` for non-bool
types. This `ValueError` propagates to `main()`'s `except Exception` (line 950),
producing `HOLD_UNKNOWN`. The test at line 690 accepts `HOLD_UNKNOWN` as a
pass, which masks the fact that the structured `HOLD_BATCH_PACKET_INVALID`
status (used by `validate_batch_packet` for other batch-level errors) is
not returned. This is a behavioral inconsistency: most batch validation
failures return `HOLD_BATCH_PACKET_INVALID`; this one returns `HOLD_UNKNOWN`.

### Classification

**`NEEDS_SMALL_REPAIR_PLAN`**

The production code correctly raises `ValueError` for non-bool `stop_on_first_hold`,
but the status returned is `HOLD_UNKNOWN` (via the broad exception handler) rather
than the structured `HOLD_BATCH_PACKET_INVALID` used by `validate_batch_packet()`
for other batch-level errors. This is a behavioral inconsistency: non-bool
`stop_on_first_hold` is rejected, but the status code does not match the pattern
used for every other batch validation failure.

The existing test accepts `HOLD_UNKNOWN`, so it does not catch this inconsistency.
The test asserts the wrong status as acceptable — it should assert
`HOLD_BATCH_PACKET_INVALID` specifically, not the full tuple of
`("HOLD_BATCH_PACKET_INVALID", "HOLD_UNKNOWN", "ERROR", "NO_OUTPUT")`.

A repair plan is needed to normalize this code path — either move the
`isinstance(bool)` check into `validate_batch_packet()` so it returns a
structured `HOLD_BATCH_PACKET_INVALID`, or catch the `ValueError` in
`run_autocoder_batch()` and convert it to `HOLD_BATCH_PACKET_INVALID` before
it propagates to `main()`'s exception handler.

Confidence: HIGH (Codex P2 blocker confirmed the gap).

### Recommended next action

Generate a repair plan in `one-task-repair-plan` mode targeting:
1. Move `stop_on_first_hold` type check into `validate_batch_packet()`
   OR wrap the `ValueError` in `run_autocoder_batch()` to produce
   `HOLD_BATCH_PACKET_INVALID`; and
2. Add regression test `test_stop_on_first_hold_rejects_non_boolean`
   that asserts the result is specifically `HOLD_BATCH_PACKET_INVALID`
   (not `HOLD_UNKNOWN`).

---

## Candidate C: rgr-320-batch-ok-subprocess-rc

### Corpus claim

Codex identified that the eval runner would consume stale `batch_status.json`
on subprocess failure. The rc guard was added in commit `e60e3b5` (PR #320).
Corpus expects: `test_eval_corpus_exits_on_nonzero_subprocess_rc`.

### Production code inspected

`scripts/local/run_autocoder_eval_corpus.py` lines 582–601:

```python
batch_ok, combined, rc = invoke_batch_controller(batch_packet, out_root)
if rc != 0:
    print(f"FATAL: batch subprocess exited nonzero (rc={rc}), cannot read stale batch_status.json as success", file=sys.stderr)
    eval_report["batch_subprocess_rc"] = rc
    eval_report["batch_subprocess_failure_reason"] = (
        f"batch subprocess exited rc={rc}; "
        "stale batch_status.json was NOT treated as success"
    )
    sys.exit(1)  # exit code 1
```

Non-zero rc is caught, explicit error message, exit is 1. The stale
`batch_status.json` is explicitly named in the error message — the guard
is unambiguous.

### Existing tests inspected

`tests/test_run_autocoder_eval_corpus.py` line 502:

```python
def test_eval_runner_exits_nonzero_on_batch_subprocess_failure(self, tmp_path):
    # ... corpus + batch_status setup ...
    def fake_invoke_batch_controller(batch_packet, output_root):
        return False, "batch subprocess died", 42

    # patch + run ...
    assert report.get("eval_pass") is False
    assert report.get("batch_subprocess_rc") == 42
    assert "stale batch_status.json was NOT treated as success" in \
        report.get("batch_subprocess_failure_reason", "")
```

The test mocks `invoke_batch_controller` returning rc=42, asserts `eval_pass=False`,
asserts `batch_subprocess_rc=42`, and asserts the stale-data guard message.
Behavior is fully covered.

### Coverage gap analysis

| Aspect | Corpus expectation | Current main |
|---|---|---|
| Test name | `test_eval_corpus_exits_on_nonzero_subprocess_rc` | NOT FOUND |
| Behavior: nonzero rc → eval_pass=False | rc=42 → exit 1 | ✅ Covered |
| Behavior: stale batch_status.json not consumed | explicit guard message | ✅ Covered |
| Production fix (rc guard at line 582) | Present | ✅ Present |

The behavior is fully covered under the test name
`test_eval_runner_exits_nonzero_on_batch_subprocess_failure`. The expected
test name does not exist, but the behavior is tested.

### Classification

**`ALREADY_FIXED_WITH_TEST`**

Confidence: HIGH. The rc guard (eval_corpus.py:582–601) is live.
`test_eval_runner_exits_nonzero_on_batch_subprocess_failure` proves the
non-zero rc path exits with code 1 and does not consume stale batch_status.json.
Behavior fully covered.

### Recommended next action

No repair plan needed. Behavior is covered. Optional naming convergence:
add `test_eval_corpus_exits_on_nonzero_subprocess_rc` as an alias or note
in the existing test's docstring referencing the corpus-expected name.
Not required.

---

## Evidence summary

| Candidate | Corpus test name | Current main test name | Behavior covered? | Production fix live? | Classification |
|---|---|---|---|---|---|
| `rgr-314-task-id-path-traversal` | `test_task_id_sanitization_rejects_path_traversal` | NOT FOUND (4 related tests) | ✅ Exhaustively | ✅ batch.py:377 `re.fullmatch` | `ALREADY_FIXED_WITH_TEST` |
| `rgr-314-stop-on-first-hold-bool` | `test_stop_on_first_hold_rejects_non_boolean` | NOT FOUND (3 related tests) | ⚠️ Covered but produces `HOLD_UNKNOWN` not `HOLD_BATCH_PACKET_INVALID` | ✅ Present but ValueError falls to broad handler | `NEEDS_SMALL_REPAIR_PLAN` |
| `rgr-320-batch-ok-subprocess-rc` | `test_eval_corpus_exits_on_nonzero_subprocess_rc` | NOT FOUND (1 related test) | ✅ Fully | ✅ eval_corpus.py:582 rc guard | `ALREADY_FIXED_WITH_TEST` |

---

## Classification methodology notes

**Rules applied:**

1. **Behavior, not name.** Classification is based on whether the production
   fix is live and whether existing tests cover the behavior — not on whether
   the exact corpus-expected test name exists. Naming gaps are real but distinct
   from behavior gaps.

2. **Status code consistency matters.** For `rgr-314-stop-on-first-hold-bool`,
   the code correctly rejects non-bool values, but the resulting status is
   `HOLD_UNKNOWN` (via broad exception handler) rather than
   `HOLD_BATCH_PACKET_INVALID` (used by `validate_batch_packet()` for other
   batch-level errors). A behavioral fix that produces the wrong status code
   is a partial fix — the rejection works but the observability is wrong.

3. **Production fix verification.** All three candidates have production fixes
   present in current main at the lines referenced by the corpus. For Candidates
   A and C, no further action needed. For Candidate B, the fix is partial —
   ValueError bypasses `validate_batch_packet()` and produces `HOLD_UNKNOWN`.

4. **No repair without evidence.** Per `docs/autonomy_friction_log.md` Entry 1
   and Entry 7: a `FIXED_ALREADY` classification is a hypothesis to verify,
   not a fact to accept. For Candidate B, the hypothesis was WRONG — the
   rejection works but the status code is wrong. This is exactly the kind of
   gap verification catches.

5. **No patch without evidence.** Candidate B triggered `FIXED_ALREADY`
   + zero changes in the original audit, which was the false signal. The
   Codex P2 blocker correctly identified that `ALREADY_FIXED_WITH_TEST`
   was wrong for Candidate B.

---

## Recommended actions

| Priority | Action | Type |
|---|---|---|
| 1 | Update `docs/codex_remediation_wave3_candidate_classification.md` — reclassify `rgr-314-stop-on-first-hold-bool` from `NEEDS_SMALL_REPAIR_PLAN` → confirm `NEEDS_SMALL_REPAIR_PLAN` (production fix is partial); reclassify A and C to `ALREADY_FIXED_WITH_TEST` | DOCS_EVIDENCE |
| 2 | Generate repair plan for `rgr-314-stop-on-first-hold-bool` in `one-task-repair-plan` mode — normalize ValueError path to produce `HOLD_BATCH_PACKET_INVALID` and add regression test | REPAIR_PRODUCTION + REPAIR_TEST_ONLY |
| 3 | Add evidence note for Candidates A and C confirming behavior is covered | DOCS_EVIDENCE |
| 4 | Optional: add naming-convergence test for Candidate A and C (exact corpus test names) if corpus author requires name-level alignment | REPAIR_TEST_ONLY (if requested) |

**Wave 3 repair execution is NEEDED for Candidate B only.** Candidates A and C are fully covered. Candidate B requires a targeted production fix to normalize the ValueError path and a regression test asserting `HOLD_BATCH_PACKET_INVALID` specifically.

---

*Audit completed 2026-05-28. Evidence-first. No repair execution.*