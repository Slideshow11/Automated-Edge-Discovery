# Autocoder Real-Output Result-Packet Builder (v0)

## Purpose

A small, **report-only** local tool that writes evaluator-compatible result
packets for completed AED/autocoder PRs. This is the bridge between the
manually-written seed result packets introduced in PR #382
(`corpus/autocoder-real-output-v0.json`) and future automatic packet
emission. The builder is intentionally minimal: it does not call models,
does not mutate GitHub, and does not require `gh`.

The output packet is consumed by
[`run_autocoder_real_output_eval.py`](../scripts/local/run_autocoder_real_output_eval.py),
which aggregates it with the corpus and emits a metrics report.

## Script

- `scripts/local/build_autocoder_real_output_result_packet.py`

## CLI

```
python3 scripts/local/build_autocoder_real_output_result_packet.py \
  --task-id real-output-v0-task-002 \
  --source-pr 999 \
  --source-commit <sha> \
  --source-head-sha <sha> \
  --title "<short title>" \
  --status PASS \
  --changed-file scripts/local/example.py \
  --allowed-file 'scripts/local/*.py' \
  --tests-passed 1 \
  --ci-green true \
  --scope-clean true \
  --review-ready true \
  --merge-ready true \
  --human-cleanup-required false \
  [--scoped-file <path>]   (repeatable, optional) \
  [--hold-reason <text>]   (optional) \
  [--error-reason <text>]  (optional) \
  [--note <text>]          (repeatable, optional) \
  --output-json <path>
```

## Behavior

- **No model execution.** The script does not call any model API.
- **No GitHub mutation.** No `gh` invocations, no `git push`, no API writes.
- No `subprocess` import. The stdlib `subprocess` module is never imported.
- No shell-mode-True-style process invocation. Not present in the source file (per source-safety tests).
- **Strict boolean parsing.** Boolean flags (`--ci-green`, `--scope-clean`,
  `--review-ready`, `--merge-ready`, `--human-cleanup-required`) only
  accept lowercase `true` or `false`. Any other value (including `True`,
  `TRUE`, `1`, `0`, `yes`, `no`, `off`, `false ` with trailing whitespace)
  is rejected by argparse with exit code 2.
- **Strict status validation.** `--status` is restricted to
  `PASS|HOLD|ERROR|UNKNOWN` via argparse `choices`. `FAIL` is not a
  builder-allowed value (the eval accepts `FAIL` from third-party packet
  writers, but the builder does not emit it).
- **Required lists.** `--changed-file` and `--allowed-file` must each be
  provided at least once.
- **Positive PR.** `--source-pr` must be a positive integer (`> 0`).
- **Non-negative tests.** `--tests-passed` must be `>= 0`.
- **Timestamps.** A `result_packet_generated_at` ISO 8601 UTC timestamp is
  added to every successful packet.
- **Trailing newline.** The output file ends with exactly one trailing
  newline.

## Status taxonomy

| Status | Meaning | Exit code |
| --- | --- | --- |
| `RESULT_PACKET_READY` | Packet was built and written. | 0 |
| `ERROR_INVALID_ARGS` | CLI args failed validation. | 2 |
| `ERROR_TOOL_FAILURE` | Unexpected internal error (e.g. write failure). | 1 |

`ERROR_INVALID_ARGS` is also reflected in the exit code; no packet file is
written in that case.

## Output packet fields

The packet is a strict superset of what the evaluator's `load_result()`
needs. The evaluator ignores unknown fields.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `task_id` | string | yes | Must match a `task_id` in the corpus. |
| `source_pr` | int > 0 | yes | PR number that produced the result. |
| `source_commit` | string | yes | Commit SHA (40-char hex recommended). |
| `source_head_sha` | string | yes | HEAD SHA (40-char hex recommended). |
| `title` | string | yes | Human-readable title. |
| `status` | string | yes | One of `PASS\|HOLD\|ERROR\|UNKNOWN`. |
| `changed_files` | list[string] | yes | Non-empty. |
| `allowed_files` | list[string] | yes | Non-empty. |
| `scoped_files` | list[string] | optional | Builder's own descriptive view. |
| `tests_passed` | int >= 0 | yes | |
| `ci_green` | bool | yes | |
| `scope_clean` | bool | yes | |
| `review_ready` | bool | yes | |
| `merge_ready` | bool | yes | |
| `human_cleanup_required` | bool | yes | |
| `hold_reason` | string | optional | Emitted only if `--hold-reason` is set. |
| `error_reason` | string | optional | Emitted only if `--error-reason` is set. |
| `notes` | list[string] | optional | Emitted only if `--note` was used. |
| `result_packet_generated_at` | string | always | ISO 8601 UTC timestamp. |
| `builder_status` | string | always | `RESULT_PACKET_READY` on success. |
| `packet_kind` | string | always | `aed.autocoder.real_output_result_packet_builder.v0` |
| `schema_version` | int | always | `1` |

## Tests

- `tests/test_build_autocoder_real_output_result_packet.py` — 33 tests
  covering required lists, strict status, strict booleans, positive PR,
  non-negative tests, optional fields, ISO 8601 timestamp, source safety
  (no `subprocess` import, no shell-mode-True flag, no gh mutation
  strings, no live-claude strings), CLI exit codes, end-to-end
  compatibility with the evaluator.

Run:

```
python3 -m pytest tests/test_build_autocoder_real_output_result_packet.py -q
```

## Smoke

Build a packet and feed it to the evaluator:

```
python3 scripts/local/build_autocoder_real_output_result_packet.py \
  --task-id real-output-v0-task-002 \
  --source-pr 999 \
  --source-commit 1111111111111111111111111111111111111111 \
  --source-head-sha 2222222222222222222222222222222222222222 \
  --title "smoke result packet" \
  --status PASS \
  --changed-file scripts/local/example.py \
  --allowed-file 'scripts/local/*.py' \
  --tests-passed 1 \
  --ci-green true \
  --scope-clean true \
  --review-ready true \
  --merge-ready true \
  --human-cleanup-required false \
  --note "smoke packet only" \
  --output-json /tmp/aed_runs/autocoder_result_packet_builder_smoke.json

python3 scripts/local/run_autocoder_real_output_eval.py \
  --corpus corpus/autocoder-real-output-v0.json \
  --result-json /tmp/aed_runs/autocoder_result_packet_builder_smoke.json \
  --output-json /tmp/aed_runs/autocoder_result_packet_builder_eval_smoke.json \
  --output-md   /tmp/aed_runs/autocoder_result_packet_builder_eval_smoke.md
```

Expected:

- builder exit code `0`, stdout contains
  `RESULT_PACKET_READY task_id=real-output-v0-task-002 source_pr=999 ...`
- evaluator exit code `0`, `status: REAL_OUTPUT_EVAL_READY`,
  `result_count: 1`, `matched_result_count: 1`.

## Why this exists

The P3B real-output evaluator (PR #382) is currently fed by hand-written
seed result packets. Once a complete AED/autocoder PR is produced, an
operator must transcribe its outcome into a JSON packet. This builder
standardizes that transcription into a single, validated CLI invocation
and produces a packet the evaluator already understands.

The next step (out of scope here) is automatic emission: have the
autocoder run controller call this builder — or a programmatic
equivalent — for every promoted task, eliminating the manual step.
This PR makes that step possible by giving the controller a
guaranteed-compatible, well-typed packet format.

## What this PR is NOT

- Not a model. Does not generate patches, plans, or test cases.
- Not a GitHub tool. Does not open, comment on, or close anything.
- Not a runner. Does not execute any other script.
- Not a merge gate. Does not change `run_autocoder_real_output_eval.py`.

## Constraints honored

- `RESULT_PACKET_READY` packet is report-only.
- No model execution.
- No GitHub mutation.
- No `gh` required.
- No subprocess (the stdlib `subprocess` module is never imported).
- No shell-mode-True-style process invocation (verified by source-safety test).
- Source contains none of the forbidden literals
  (`gh pr merge`, `gh api`, `gh run watch`, `gh pr checks --watch`,
  `git push`, shell-equals-True, `claude-code`, `live claude`, `Live Claude`,
  `enable-real-claude-executor`).
