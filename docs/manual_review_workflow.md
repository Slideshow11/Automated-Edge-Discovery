# Manual AED Review Workflow

Purpose
-------
This document describes the manual, local review workflow for Automated Edge Discovery (AED). It is a human-driven, read-only review path that demonstrates how to run local smoke flows, locate generated artifacts, evaluate a single ledger entry, and assemble a review packet for manual inspection.

Safety boundaries
----------------
- This workflow is strictly manual and review-only. It does not perform any automated promotions, registry mutations, or lifecycle automation.
- Evaluator labels are review-only and must not be interpreted as automatic promotions.
- Review packets produced by this workflow set `manual_review_required = true` and `registry_mutation = false` to emphasize the manual nature of decisions.
- AED remains data-source agnostic: it consumes local, pre-provisioned datasets and does not download, scrape, or fetch data.
- Do not run real backtests in CI. Real runs are permitted only locally and must be explicitly requested by the operator.

Inputs
------
- Example manifests (DataManifest v1) in `examples/data_manifests/` (e.g. `preearn_options_2021_local.json`, `preearn_repo_local.json`).
- Direct path arguments: `--options-db-path` and `--preearn-repo-path`.
- Manifest arguments (optional): `--options-db-manifest` and `--preearn-repo-manifest`.

Notes on precedence
- Direct path arguments (`--options-db-path`, `--preearn-repo-path`) take precedence over manifest-derived paths. If both are provided, AED uses the explicit direct path.
- Manifests are portable examples. Tests that rely on positive-manifest behavior should copy and rewrite manifests to `tmp_path` to avoid machine-specific absolute paths.

Workflow overview
-----------------
1. Prepare a manifest or local paths for the options DB and the pre-earnings repo.
2. Run the lifecycle smoke script in dry-run mode (default) using either direct paths or the DataManifest examples.
3. Locate the generated batch summary JSON and the ledger entry (JSONL). Note the `run_id`.
4. Evaluate a single ledger entry with `scripts/local/evaluate_ledger_entry.py`.
5. Build a manual review packet with `scripts/local/make_run_review_packet.py` (stdout or file).
6. Perform a human review using the review packet and artifacts.
7. Cleanup `.wfa/` artifacts when finished.

Run lifecycle smoke with direct paths
------------------------------------
Dry-run by default (safe):

```bash
PYTHONPATH=. python3 scripts/local/smoke_preearn_lifecycle.py \
  --example basic \
  --options-db-path /path/to/options.sqlite \
  --preearn-repo-path /path/to/preearn_repo \
  --dry-run \
  --output-dir .wfa/preearn_lifecycle_smoke 
```

- Inspect `.wfa/preearn_lifecycle_smoke/` for a `batch_{batch_id}.json` file and optionally the ledger entry in the ledger path if `--ledger-path` was provided.

Run lifecycle smoke with DataManifest paths
------------------------------------------
You can pass DataManifest JSON files instead of direct paths. Direct paths still take precedence if provided.

```bash
PYTHONPATH=. python3 scripts/local/smoke_preearn_lifecycle.py \
  --example basic \
  --options-db-manifest examples/data_manifests/preearn_options_2021_local.json \
  --preearn-repo-manifest examples/data_manifests/preearn_repo_local.json \
  --dry-run \
  --output-dir .wfa/preearn_lifecycle_smoke
```

Find the ledger path and run_id
------------------------------
- The lifecycle smoke prints a summary including `batch_id` and `batch_status`.
- The batch summary JSON is written to `<output_dir>/batch_{batch_id}.json`.
- The AED ledger (JSONL) path is either the configured default or the `--ledger-path` you provided; ledger entries will contain `run_id` equal to the `batch_id`.

Evaluate one ledger entry
-------------------------
Use the evaluator CLI to print a compact JSON evaluation of a single ledger entry.

```bash
PYTHONPATH=. python3 scripts/local/evaluate_ledger_entry.py \
  --ledger-path .wfa/preearn_lifecycle_smoke/ledger.jsonl \
  --run-id batch_123456_abc
```

- Output is a single JSON line describing `run_id`, `label`, `reason`, `hypothesis_id`, `source_type`, and `warnings`.
- Evaluator labels are review-only. They do not cause registry changes and do not promote or reject hypotheses automatically.

Generate a review packet to stdout
---------------------------------
To produce a compact review packet to stdout (JSON):

```bash
PYTHONPATH=. python3 scripts/local/make_run_review_packet.py \
  --ledger-path .wfa/preearn_lifecycle_smoke/ledger.jsonl \
  --run-id batch_123456_abc
```

Generate a review packet file
----------------------------
To write the packet to a file instead:

```bash
PYTHONPATH=. python3 scripts/local/make_run_review_packet.py \
  --ledger-path .wfa/preearn_lifecycle_smoke/ledger.jsonl \
  --run-id batch_123456_abc \
  --output-path .wfa/review_packet_batch_123456_abc.json
```

- Review packets include explicit metadata: `manual_review_required = true` and `registry_mutation = false` to emphasize manual handling.

How to interpret outputs
------------------------
- `batch_{batch_id}.json` — summary of the batch run (candidate counts, status, and per-candidate results if executed).
- Ledger (JSONL) entry — one line per run recording run_id, run_type, status, artifacts, and metrics summary.
- Evaluator JSON — a human-oriented label and reason; use as guidance, not as an automated decision.
- Review packet — combines ledger and evaluator to a single file for reviewer distribution.

Human review checklist
---------------------
- Confirm the packet `hypothesis_id` is the intended test subject.
- Inspect `metrics_summary` (n_candidates_generated, n_success, n_error).
- Check for obvious data issues (truncated artifacts, missing files).
- Verify `evaluation.reason` and `warnings`; escalate if the warnings indicate systemic problems.
- Confirm `manual_review_required` is true before taking any automated action.

Troubleshooting
---------------
- Missing run_id: ensure you used the `batch_id` printed by the smoke script and that you specified the correct `--ledger-path`.
- Duplicate run_id: inspect the ledger file for multiple lines with the same `run_id` — this indicates repeated records; open them and compare timestamps/metrics.
- Empty ledger: ensure the ledger path points to the ledger JSONL file and not an output directory.- Invalid manifest role: the manifest contains a `role` field which must match the CLI argument being used. For example:
  - `--options-db-manifest` expects a manifest whose role is `options_backtest_db`.
  - `--preearn-repo-manifest` expects a manifest whose role is `preearn_repo`.
  If the role is incorrect, either copy the appropriate example manifest and adjust its fields to match your dataset, or use the direct path arguments (`--options-db-path`, `--preearn-repo-path`) instead.
- Missing manifest path: ensure the manifest file path exists and is readable. Also validate that the local dataset path declared inside the manifest (e.g. the filesystem path to the options DB or repo) exists and is accessible. Check the manifest path, verify the JSON `path` field(s), or fallback to direct path arguments if the manifest cannot be validated.


- Manifest portability: example manifests contain machine-specific absolute paths. For portable tests, copy and rewrite manifest paths under `tmp_path` in pytest.

Cleanup
-------
Remove temporary artifacts when done:

```bash
rm -rf .wfa/
```

Notes and best practices
------------------------
- Do not run real pre-earnings backtests in CI. Any `--real-run` invocation is local-only and requires explicit operator confirmation.
- The evaluator and review packet are explicit review artifacts. They do not modify AED registry state.
- Direct path arguments (`--options-db-path`, `--preearn-repo-path`) always override manifest-derived paths.
- If you extend tests that rely on example manifests, rewrite paths to `tmp_path` to keep tests portable.

Acknowledgements
----------------
This workflow builds on existing AED utilities:
- `scripts/local/smoke_preearn_lifecycle.py` — lifecycle smoke driver
- `scripts/local/evaluate_ledger_entry.py` — evaluate a single ledger entry
- `scripts/local/make_run_review_packet.py` — construct review packets
- `examples/data_manifests/*` — DataManifest v1 examples

