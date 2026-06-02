# Real-Output Autocoder Eval — Seed Results (v0)

This document records the **first three real-output result packets** consumed
by the autocoder real-output evaluator introduced in
`corpus/autocoder-real-output-v0.json` and
`scripts/local/run_autocoder_real_output_eval.py` (P3A from the
autocoder autonomy roadmap). It turns the P3A infrastructure from
"evaluator scaffolding" into "evaluator with measurements" by attaching
conservative result packets to the corpus's existing task definitions.

## What these are

Three retrospective result packets, one per completed AED PR:

| File | task_id | PR | Merge commit |
|---|---|---|---|
| `corpus/autocoder-real-output-results-v0/pr379-post-merge-ci-audit-helper.json` | `real-output-v0-task-002` | #379 | `aa551d04ab5aa310290231c5f5b5e7c2133706e0` |
| `corpus/autocoder-real-output-results-v0/pr380-real-output-eval-v0.json` | `real-output-v0-task-005` | #380 | `cf97940975e4650d6ccee35b029508c67ae35907` |
| `corpus/autocoder-real-output-results-v0/pr381-auto-post-merge-audit-workflow.json` | `real-output-v0-task-004` | #381 | `53928a19bd43ea886111886afa1bfec71a9a2327` |

Each packet is a **report-only annotation** that records, for the relevant
corpus task, whether the merged patch was clean, tested, CI-green,
review-ready, merge-ready, and whether human cleanup was required.
The packet does **not** model the patch output itself — it is a
post-hoc, hand-curated record of the merge result.

## Task mapping

- **PR #379** (`aa551d04…`) is mapped to `real-output-v0-task-002`
  (Add a small report-only helper script). The PR added
  `scripts/local/audit_main_ci_for_head.py` and its tests, which is
  the canonical example of a report-only helper.
- **PR #380** (`cf979409…`) is mapped to `real-output-v0-task-005`
  (Validate a packet schema and emit a report). The PR added the
  packet validator itself plus its test suite, which is the
  canonical example of a packet-validation tool.
- **PR #381** (`53928a19…`) is mapped to `real-output-v0-task-004`
  (Narrow a checker behavior with explicit guard). The PR added a
  workflow plus its test suite, which is the closest fit among the
  five corpus tasks (no corpus task targets `.github/**`).

## What these are NOT

- **Not model-execution results.** No model produced these patches at
  eval time. The patches were hand-written and merged by humans. The
  result packets only annotate the merge outcome of human work.
- **Not a benchmark claim.** This is **not** a measurement of autocoder
  quality or capability. The metrics in this document are the first
  usefulness layer, not a model evaluation.
- **Not a regression suite for the evaluator itself.** The eval script
  is already covered by `tests/test_run_autocoder_real_output_eval.py`.
  The new test file
  `tests/test_autocoder_real_output_eval_fixtures.py` validates that
  the seed packets are well-formed and that the evaluator can run
  them end-to-end — it does not validate that the patches themselves
  were "good" autocoder output.

## How to reproduce the metrics

From the repo root, with the three seed packets in place:

```bash
python3 scripts/local/run_autocoder_real_output_eval.py \
  --corpus corpus/autocoder-real-output-v0.json \
  --result-json corpus/autocoder-real-output-results-v0/pr379-post-merge-ci-audit-helper.json \
  --result-json corpus/autocoder-real-output-results-v0/pr380-real-output-eval-v0.json \
  --result-json corpus/autocoder-real-output-results-v0/pr381-auto-post-merge-audit-workflow.json \
  --output-json /tmp/aed_runs/autocoder_real_output_eval_results_v0_smoke.json \
  --output-md /tmp/aed_runs/autocoder_real_output_eval_results_v0_smoke.md
```

Expected overall status: `REAL_OUTPUT_EVAL_READY`.

Expected metrics (conservative, hand-recorded):

- `task_count: 5` (the corpus has 5 tasks)
- `result_count: 3` (3 seed packets)
- `matched_result_count: 3` (all 3 reference corpus task_ids)
- `tasks_with_results: 3`
- `ci_green_count: 3` (all 3 merged PRs had green CI at merge time)
- `merge_ready_count: 3` (all 3 eventually merged)
- `human_cleanup_required_count: 3` (all 3 had follow-up human work —
  P2 review threads, scope exceptions, or stale-thread resolution)
- `scope_clean_count: 3` (each packet's `changed_files` was scoped to
  the corpus task's `allowed_files`; the workflow / corpus / test
  files that were actually part of each PR are noted separately in
  each packet's `notes` field for honesty, not listed in `changed_files`)
- `tests_passed_count: 19 + 25 + 32 = 76` (sum across the 3 packets)

## Limitations

- **Retrospective, not prospective.** These packets were written after
  the merges, not produced by the autocoder. They measure the *eval
  pipeline* (does it accept well-formed retrospective packets? does
  it compute the right metrics?) — not autocoder capability.
- **Hand-curated.** Each packet's `status`, `ci_green`,
  `merge_ready`, `human_cleanup_required`, and `tests_passed` values
  are human-asserted from the merge record, not produced by an
  automated tool. Future v1 work should derive these from GitHub
  Actions runs and the PR API.
- **Scope is honest-but-narrowed.** Each packet's `changed_files` only
  includes files that the corpus task's `allowed_files` permits, so
  `scope_clean_count` reads 3/3. The actual PR diffs (which include
  test files, a corpus data file, and a workflow file that are
  outside the corpus task's `allowed_files` patterns) are
  acknowledged in each packet's `notes` field. A future v1 should
  add a separate "actual vs. allowed" column to the report.
- **No real-output autocoder runs are in the data set.** These are
  the merge records of human work, not the output of an autocoder
  task. They are seed data for the *evaluator*, not for *evaluating
  the autocoder*.

## Next step

The next step is to **generate fresh result packets automatically
from future autocoder runs**:

1. Wire the autocoder to emit a result packet per task at the end
   of each rehearsal.
2. Have the autocoder run emit the packet to
   `corpus/autocoder-real-output-results-v0/`.
3. Re-run the eval at rehearsal end. The metrics will then be a real
   measurement of autocoder output quality, not a hand-recorded
   measurement of human work.

When that wiring is in place, `metrics.tests_passed_count` and
`metrics.merge_ready_count` become actual signals about autocoder
output. Until then, they are signals about the eval pipeline itself.
