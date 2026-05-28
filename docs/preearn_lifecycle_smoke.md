# Lifecycle Smoke Script

Demonstrates the full AED lifecycle pipeline: example loader → HypothesisSpec → HypothesisRegistry → candidate batch → evaluator output.

**Intended use:** Manual smoke testing only. Not part of CI. Not an autonomous loop.

---

## Pipeline

```
load_preearn_example(name)
  → HypothesisSpec
    → register_and_run_batch()
      → HypothesisRegistry (draft → registered → testing)
      → run_candidate_batch() [dry-run by default]
    → BatchResult
  → evaluate_batch_result(BatchResult)
  → EvaluationResult (label, reason, warnings)
```

---

## Dry-Run (default, safe)

```bash
PYTHONPATH=. python3 scripts/local/smoke_preearn_lifecycle.py \
  --example basic \
  --preearn-repo-path /path/to/engine_linux_main \
  --options-db-path /path/to/engine_linux_main/cache/scratch/options_2021_lane_0.sqlite \
  --dry-run \
  --registry-path .wfa/preearn_lifecycle_smoke/registry.jsonl \
  --ledger-path .wfa/preearn_lifecycle_smoke/ledger.jsonl \
  --output-dir .wfa/preearn_lifecycle_smoke
```

### Example: coarse grid

```bash
PYTHONPATH=. python3 scripts/local/smoke_preearn_lifecycle.py \
  --example coarse \
  --preearn-repo-path /path/to/engine_linux_main \
  --options-db-path /path/to/engine_linux_main/cache/scratch/options_2021_lane_0.sqlite \
  --dry-run \
  --output-dir .wfa/preearn_lifecycle_smoke_coarse
```

---

## Optional Real Run (manual only — not in CI)

```bash
PYTHONPATH=. python3 scripts/local/smoke_preearn_lifecycle.py \
  --example basic \
  --preearn-repo-path /path/to/engine_linux_main \
  --options-db-path /path/to/engine_linux_main/cache/scratch/options_2021_lane_0.sqlite \
  --real-run \
  --timeout 60 \
  --output-dir .wfa/preearn_lifecycle_smoke_real
```

Real run is **manual only**. CI does not execute `--real-run`.

---

## Expected Output

The script prints a lifecycle summary table:

```
============================================================
LIFECYCLE SMOKE SUMMARY
============================================================
example:          basic
hypothesis_id:    preearn-iv-ramp-basic-v1
initial_status:   draft
final_status:     registered
batch_id:         <uuid>
batch_status:     dry_run
n_candidates_generated: 1
n_candidates_selected:  1
n_success:        0
n_error:          0
evaluation_label: NEEDS_MORE_DATA
evaluation_reason: Dry-run batch — no real execution occurred. ...
evaluation_warnings: []
registry_path:    /path/to/registry.jsonl
============================================================
```

---

## Evaluator Labels

The evaluator classifies the BatchResult into one of four labels:

| Label | Meaning |
|---|---|
| `INVALID_RUN` | Batch did not run (unknown run_type or malformed result). |
| `EXECUTION_FAILED` | All candidates errored — adapter or environment problem. |
| `NEEDS_MORE_DATA` | Dry-run, or too few successes relative to errors. |
| `PROMISING` | Sufficient successes and low error rate. |

**Labels are review guidance only.** They do not trigger promotion, rejection, or any registry status change. A human must interpret the label in context.

---

## Files Written

| File | Purpose |
|---|---|
| `--output-dir`/ | Working directory for batch output |
| `--registry-path` | HypothesisRegistry JSONL (hypothesis records) |
| `--ledger-path` | Ledger JSONL (batch-level run records) |

All paths are configurable. Default registry and ledger paths come from `get_config()` if not specified.

---

## What This Script Is Not

- **Not a trading signal.** Output is for smoke testing the AED pipeline.
- **Not part of CI.** Only runs manually.
- **Not autonomous.** Does not loop, breed, promote, or reject hypotheses.
- **Not a promotion workflow.** No accepted/rejected/killed transitions.
- **No IVOL API.** Uses configured options DB path only.
- **No real backtests in CI.** `--real-run` is manual only.

---

## Examples Available

| Name | HypothesisSpec | Candidates |
|---|---|---|
| `basic` | `preearn-iv-ramp-basic-v1` | 1 |
| `coarse` | `preearn-delta-grid-basic-v1` | 4 |
