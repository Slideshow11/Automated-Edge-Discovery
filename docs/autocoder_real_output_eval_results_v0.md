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

| File | task_id | PR | Merge commit | changed_files count |
|---|---|---|---|---|
| `corpus/autocoder-real-output-results-v0/pr379-post-merge-ci-audit-helper.json` | `real-output-v0-task-002` | #379 | `aa551d04ab5aa310290231c5f5b5e7c2133706e0` | 2 (full actual diff) |
| `corpus/autocoder-real-output-results-v0/pr380-real-output-eval-v0.json` | `real-output-v0-task-005` | #380 | `cf97940975e4650d6ccee35b029508c67ae35907` | 3 (full actual diff) |
| `corpus/autocoder-real-output-results-v0/pr381-auto-post-merge-audit-workflow.json` | `real-output-v0-task-004` | #381 | `53928a19bd43ea886111886afa1bfec71a9a2327` | 2 (full actual diff) |

Each packet is a **report-only annotation** that records, for the relevant
corpus task, whether the merged patch was clean, tested, CI-green,
review-ready, merge-ready, and whether human cleanup was required.
The packet does **not** model the patch output itself — it is a
post-hoc, hand-curated record of the merge result.

## Packet schema

Each result packet has these fields (in addition to the standard
`packet_kind`, `schema_version`, and metadata):

| Field | Type | Meaning |
|---|---|---|
| `task_id` | string | Which corpus task this packet maps to. Must exist in the corpus. |
| `source_pr` | int | The PR number the packet annotates. |
| `source_commit` | string | The merge commit SHA on main. |
| `source_head_sha` | string | The pre-merge head SHA of the PR branch. |
| `title` | string | The PR title. |
| `status` | string | `PASS` / `HOLD` / `FAIL` / `ERROR` (uppercased). Controls whether the packet counts toward metrics. |
| **`changed_files`** | list[str] | **The full actual source PR diff. This is the field the evaluator passes to `scope_violation()`.** Honest, even when out-of-corpus-scope. |
| `scoped_files` | list[str] | **Descriptive only.** A narrower list of files that fit inside the corpus task's `allowed_files` pattern. NOT used by the evaluator. |
| `allowed_files` | list[str] | The full allowlist (informational; matches the corpus task's `allowed_files` for traceability). |
| `forbidden_files` | list[str] | Always empty in these seeds (informational only). |
| `tests_passed`, `tests_failed`, `tests_total` | int | Test counts. |
| `ci_green` | bool | Whether CI was green at merge time. |
| `scope_clean` | bool | **Reflects the honest evaluator calculation against `changed_files`. Will be `false` for these seeds because each PR's actual diff includes files outside the corpus task's `allowed_files`.** |
| `review_ready` | bool | Whether review approval was obtained. |
| `merge_ready` | bool | Whether the PR was eventually merged. |
| `human_cleanup_required` | bool | Whether follow-up human work was needed (P2 threads, scope exceptions, etc.). |
| `hold_reason`, `error_reason` | string | Empty in these seeds. |
| `notes` | string | Free-text annotation. |

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

## How `changed_files` relates to `scope_clean`

The evaluator's `compute_task_record()` passes `changed_files` to
`scope_violation()`, which compares each file against the corpus
task's `allowed_files` and `forbidden_files` patterns. A file is a
violation if it matches any forbidden pattern OR if it doesn't match
any allowed pattern.

**`changed_files` is the field the evaluator uses.** It must be the
full actual source PR diff, not a curated subset chosen to make the
scope check look better. This is what makes `scope_clean_count`
honest.

**`scoped_files` is descriptive only.** It is the narrower view
("which files in the actual diff fit inside this corpus task's
`allowed_files`?"). It is useful for documentation but is NOT
consulted by the evaluator. Future v1 might surface `scoped_files` in
the Markdown report as a "scope-at-a-glance" column, but it will
remain out of the scope-check logic.

**`scope_clean_count` reflects the honest calculation against
`changed_files`.** For these 3 seeds, the count is 0/3, because each
PR's actual diff includes at least one file outside the mapped
corpus task's `allowed_files`:

| PR | changed_files violation |
|---|---|
| #379 | `tests/test_audit_main_ci_for_head.py` matches `tests/**` (forbidden by task-002) |
| #380 | `corpus/autocoder-real-output-v0.json` matches `*.json` (forbidden by task-005) |
| #381 | `.github/workflows/post-merge-main-ci-audit.yml` matches `.github/**` (forbidden by task-004 and all other corpus tasks) |

This is the honest reading. Each PR was a scope exception relative to
the corpus task it is mapped to — that's the realistic retrospective
finding, and the eval should report it as such.

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
- **Not scope-clean claims.** `scope_clean_count = 0/3` for these
  seeds. That is the honest reading, not a defect.

## How to reproduce the metrics

From the repo root, with the three seed packets in place:

```bash
python3 scripts/local/run_autocoder_real_output_eval.py \
  --corpus corpus/autocoder-real-output-v0.json \
  --result-json corpus/autocoder-real-output-results-v0/pr379-post-merge-ci-audit-helper.json \
  --result-json corpus/autocoder-real-output-results-v0/pr380-real-output-eval-v0.json \
  --result-json corpus/autocoder-real-output-results-v0/pr381-auto-post-merge-audit-workflow.json \
  --output-json /tmp/aed_runs/autocoder_real_output_eval_results_v0_after_p2.json \
  --output-md /tmp/aed_runs/autocoder_real_output_eval_results_v0_after_p2.md
```

Expected overall status: `REAL_OUTPUT_EVAL_READY`.

Expected metrics (honest, derived from full `changed_files`):

- `task_count: 5` (the corpus has 5 tasks)
- `result_count: 3` (3 seed packets)
- `matched_result_count: 3` (all 3 reference corpus task_ids)
- `tasks_with_results: 3`
- `ci_green_count: 3` (all 3 merged PRs had green CI at merge time)
- `merge_ready_count: 3` (all 3 eventually merged)
- `human_cleanup_required_count: 3` (all 3 had follow-up human work —
  P2 review threads, scope exceptions, or stale-thread resolution)
- **`scope_clean_count: 0`** (each PR's actual diff has files outside
  the mapped corpus task's `allowed_files`; this is the honest reading)
- `tests_passed_count: 19 + 25 + 32 = 76` (sum across the 3 packets)
- `missing_result_task_ids: [real-output-v0-task-001, real-output-v0-task-003]`
  (2 of 5 corpus tasks intentionally not exercised by these seeds)

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
- **Scope is honestly reported, but a 0/3 score.** Because the
  corpus tasks do not perfectly match the structure of the
  historical PRs, scope violations are the realistic retrospective
  finding. Future v1 should add a corpus task that *does* permit
  workflow edits, test-only additions, and corpus data additions,
  so the score can move toward 3/3 for real autocoder output.
- **No real-output autocoder runs are in the data set.** These are
  the merge records of human work, not the output of an autocoder
  task. They are seed data for the *evaluator*, not for *evaluating
  the autocoder*.

## Next step

The next step is to **generate fresh result packets automatically
from future autocoder runs**:

1. Wire the autocoder to emit a result packet per task at the end
   of each rehearsal, with `changed_files` set to the actual diff.
2. Have the autocoder run emit the packet to
   `corpus/autocoder-real-output-results-v0/`.
3. Re-run the eval at rehearsal end. The metrics will then be a real
   measurement of autocoder output quality, not a hand-recorded
   measurement of human work.

When that wiring is in place, `metrics.tests_passed_count`,
`metrics.merge_ready_count`, and `metrics.scope_clean_count` all
become actual signals about autocoder output. Until then, they are
signals about the eval pipeline itself and the corpus task's fit to
the historical PR shape.
