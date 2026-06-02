# P3C-B1: Mock Autocoder Result Packet Emission (v0)

## Purpose

`scripts/local/run_autocoder_single_task.py` (the single-task autocoder
controller introduced earlier in the P3 series) is the AED entry point for
running one task through the verified six-stage pipeline. PR #380 / P3A
added the **real-output evaluator**, and PR #383 / P3C-A added the
**result packet builder** that the evaluator consumes. This P3C-B1 PR
provides the missing link: when the controller reaches a successful
mock-mode terminal state, it can now emit a P3C-A-compatible result packet
that the evaluator can consume end-to-end.

This PR is **report-only** and **mock-only**. It does not execute any
real model, does not push anything, and does not open a PR. It only
writes a JSON packet to a caller-supplied path.

## Why this is needed

Before P3C-B1, the real-output evaluator had no way to receive packet
output from an actual controller run — it only consumed hand-written
fixtures in `corpus/autocoder-real-output-v0.json`. P3C-B1 closes that
gap by giving the controller a `--emit-real-output-result-packet` flag
that triggers packet emission at the end of a successful mock-mode run.

The output is exactly the JSON shape produced by P3C-A's
`build_packet_from_namespace` helper, so the evaluator's existing
acceptance logic applies without any new schema work.

## What this PR does

1. **Adds two new CLI flags to `run_autocoder_single_task.py`:**
   - `--emit-real-output-result-packet PATH`
     — Path to write the result packet to after a successful run.
   - `--real-output-task-id TEXT`
     — The `task_id` to record in the packet's `result.task_id` field.

2. **Wires emission at the controller's terminal success state** (the
   `State.READY` state reached at the end of a successful mock-mode run).
   Emission only happens in `--execution-mode mocked`. Live, real, and
   claude modes are explicitly rejected upstream (the controller already
   rejects all non-mocked modes via `VALID_EXECUTION_MODES = frozenset(["mocked"])`).

3. **Reuses the P3C-A builder's `build_packet_from_namespace` and
   `write_packet` helpers** by importing the builder module at call time
   via `sys.path.insert(0, SCRIPT_DIR)`. The P3C-A builder owns the
   schema; P3C-B1 only populates the namespace.

4. **Sets mock-only defaults** for fields the controller cannot honestly
   populate from a mocked run:
   - `source_pr=0` (unopened PR sentinel)
   - `ci_green=False`
   - `merge_ready=False`
   - `human_cleanup_required=True`
   - `note: "mock-only run; CI not exercised"` added to `notes`
   - `hold_reason: "mock-only run; CI not exercised"` set in the HOLD
     packet variant (the controller still emits a `State.READY`-shaped
     PASS packet because the mock run completed; the mock-only marker is
     conveyed via `notes`).

5. **Validates CLI input**: missing `--real-output-task-id` while
   `--emit-real-output-result-packet` is set exits 1 with a clear
   FATAL message.

## What this PR does NOT do

- **No model execution.** All modes except `mocked` are rejected.
- **No GitHub mutation.** No push, no PR open, no merge, no comment, no
  review, no thread resolution.
- **No subprocess shell invocation.** The controller never invokes
  `subprocess` with the shell-argument form. Existing subprocess calls
  reuse the same argument-list plumbing as before.
- **No real execution mode change.** The
  `VALID_EXECUTION_MODES = frozenset(["mocked"])` guard in the controller
  is unchanged; live/real/claude modes remain rejected.
- **No schema drift.** The packet is built by P3C-A's builder, so the
  schema cannot drift from the evaluator's expectations.

## Mock-only contract

When `--emit-real-output-result-packet` is used in mock mode, the
emitted packet is annotated so downstream consumers can identify it as
mock-sourced:

```json
{
  "result": {
    "task_id": "real-output-v0-task-002",
    "status": "PASS",
    "title": "...",
    "...": "..."
  },
  "schema": {
    "schema_version": "1.0",
    "evaluator": "run_autocoder_real_output_eval"
  },
  "source_pr": 0,
  "ci_green": false,
  "merge_ready": false,
  "human_cleanup_required": true,
  "notes": [
    "mock-only run; CI not exercised"
  ]
}
```

Downstream tools should treat `source_pr=0` and the
`mock-only run; CI not exercised` note as the authoritative mock marker.

## Hard rules (enforced in this block)

- **Mock mode only.** The emission is gated on `execution_mode == "mocked"`.
  Any other mode exits 1.
- **Report-only.** The emission writes a JSON file. It does not call any
  model API, does not invoke `gh`, does not push, does not open a PR.
- **Reuses P3C-A schema.** The packet is built via the P3C-A
  `build_packet_from_namespace` helper. The schema is owned by P3C-A
  (PR #383), and the P3C-B1 emission code does not redefine any field.
- **No live subprocess invocation.** The helper imports the P3C-A
  builder and calls it directly. It does not shell out to anything.

## Integration points

| Component                                  | Role                                 |
| ------------------------------------------ | ------------------------------------ |
| `scripts/local/run_autocoder_single_task.py` | Caller; new emission flag & helper  |
| `scripts/local/build_autocoder_real_output_result_packet.py` | Packet builder (PR #383) |
| `scripts/local/run_autocoder_real_output_eval.py` | Packet consumer (PR #380) |
| `corpus/autocoder-real-output-v0.json`     | Corpus of expected packet shapes     |
| `tests/test_run_autocoder_single_task_result_packet.py` | New test file (17 tests) |

## Next step

With P3C-B1 in place, the next milestone (P3C-B2) is to wire the same
emission flag into the **batch** controller
(`scripts/local/run_autocoder_batch.py`) so that batch runs also produce
evaluator-compatible packets per-task. After P3C-B2 lands, the real-output
evaluator will be able to score batch runs directly, closing the loop
between autocoder runs and real-output evaluation.

Until then, P3C-B1 only exercises the single-task path. PR #380's
hand-written corpus remains the source of truth for evaluator scoring
across all task shapes.
