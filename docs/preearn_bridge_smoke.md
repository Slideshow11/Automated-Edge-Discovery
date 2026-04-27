# Pre-earnings bridge smoke script

What this tests
---------------
This manual smoke script proves the local AED bridge end-to-end in a safe,
controlled way without running heavy CI backtests.

It exercises:
- HypothesisSpec → CandidateSpec generation
- Candidate batch runner (dry-run)
- Pre-earnings adapter invocation in real-run mode (local only)
- Batch summary JSON creation
- Batch-level ledger entry writing (when ledger path provided)

Safety and modes
----------------
- Default mode is --dry-run. Dry-run generates candidates, writes a batch
  summary JSON, and writes a batch-level ledger entry if a ledger path is
  provided. Dry-run does NOT execute the pre-earnings repo subprocess.
- To invoke the local pre-earnings repo, pass --real-run explicitly. This
  prints a clear warning. Do NOT run --real-run in CI.
- The script does not download data, does not call IVOL API, and does not
  modify the pre-earnings repo.
- All outputs are written under --output-dir and ledger entries to
  --ledger-path. Nothing is written outside these locations.

Example dry-run
---------------
PYTHONPATH=. python3 scripts/local/smoke_preearn_bridge.py \
  --preearn-repo-path /home/max/engine_linux_main \
  --options-db-path /home/max/engine_linux_main/cache/scratch/options_2021_lane_0.sqlite \
  --dry-run \
  --output-dir .wfa/preearn_bridge_smoke \
  --ledger-path .wfa/preearn_bridge_smoke/ledger.jsonl

Example real-run (local only)
----------------------------
PYTHONPATH=. python3 scripts/local/smoke_preearn_bridge.py \
  --preearn-repo-path /home/max/engine_linux_main \
  --options-db-path /home/max/engine_linux_main/cache/scratch/options_2021_lane_0.sqlite \
  --real-run \
  --max-candidates 1 \
  --timeout 60 \
  --output-dir .wfa/preearn_bridge_smoke \
  --ledger-path .wfa/preearn_bridge_smoke/ledger.jsonl

What not to do
-------------
- Do not run --real-run in CI.
- Do not point --options-db-path to production or large databases during
  local smoke unless you intend to run real backtests.
- Do not use this script as an automated search. It is a manual developer
  tool to verify local wiring.

Expected output
---------------
- A JSON file: {output_dir}/batch_{batch_id}.json
- If ledger_path provided: ledger file contains a batch-level entry
- Console prints batch_id, status, counts, artifact paths

CI
--
CI does not run real pre-earnings backtests. The repo's CI runs unit tests
only. This script is manual-only and will not be executed by CI unless an
explicit workflow author decides to do so (not recommended).
