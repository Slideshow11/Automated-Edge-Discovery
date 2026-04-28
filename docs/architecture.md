# AED Architecture Guide

Automated Edge Discovery (AED) is a hypothesis-driven options strategy development framework. This document is the authoritative top-level reference for AED's purpose, design, boundaries, and roadmap.

For script-specific usage, see:
- `docs/preearn_bridge_smoke.md`
- `docs/preearn_lifecycle_smoke.md`
- `docs/preearn_hypothesis_examples.md`

---

## 1. What AED Is

AED is a structured framework for declaring options trading hypotheses, generating candidate strategies systematically, running backtest batches against a pre-provisioned local dataset, recording results in an append-only ledger, and classifying outcomes for human review.

Core capabilities:
- Declare a hypothesis as a typed `HypothesisSpec` (strategy family, candidate constraints, validation plan, failure modes)
- Generate all candidate combinations via cartesian product of constraint dimensions
- Run candidates through the pre-earnings backtester via a subprocess adapter
- Record every run in an append-only JSONL ledger
- Classify batch results with evaluator labels for review readiness
- Track hypothesis lifecycle (draft → registered → testing) in a registry

AED is built for **local, deterministic, reproducible** workflow. All inputs are local files or paths; all outputs are local artifacts.

---

## 2. What AED Is Not

AED is **not**:

- **A data acquisition system.** AED does not download, scrape, or ingest data from any vendor. It consumes pre-downloaded, locally provisioned datasets. Data acquisition is upstream of AED.
- **A real-time trading system.** AED is a backtest and research framework. It does not place trades, manage positions, or connect to brokers.
- **A pre-earnings backtester.** AED calls the pre-earnings repo's script interface; it does not own or replace the backtest engine (`run_options_backtest_v1.py` lives in the pre-earnings repo, not AED).
- **An automated promotion machine.** AED does not automatically accept, reject, or kill hypotheses. Evaluator labels are review-only. Promotion decisions require human judgment.
- **A vendor-specific product.** AED has no core dependency on IVOL, Bloomberg, FactSet, or any premium data service.
- **A scraper-first system.** AED does not scrape websites, earnings calendars, or news feeds.

---

## 3. Core Pipeline

```
HypothesisSpec  [engine/edge_discovery/hypotheses/spec.py]
      │
      ▼
CandidateSpec[]  [engine/edge_discovery/hypotheses/generate.py]
  (cartesian product of entry_dpe × delta_target × expiry_rank)
      │
      ▼
BatchResult  [engine/edge_discovery/hypotheses/batch.py]
  (runs each CandidateSpec via PreearnOptionsAdapter subprocess)
      │
      ▼
LedgerEntry  [engine/edge_discovery/ledger.py]
  (appended to .wfa/ledger.jsonl)
      │
      ▼
EvaluationResult  [engine/edge_discovery/evaluation.py]
  (label: invalid_run / execution_failed / needs_more_data / promising_for_review)
```

The full **lifecycle** coordinates these steps:

```
load_preearn_example(name)          [engine/edge_discovery/examples.py]
      │
      ▼
register_and_run_batch()           [engine/edge_discovery/hypotheses/lifecycle.py]
  1. Register (draft → registered)
  2. Transition (registered → testing) [non-dry-run only]
  3. run_candidate_batch()
  4. Set final_status
      │
      ▼
LifecycleResult
  ├── batch_result: BatchResult | None
  ├── hypothesis_id
  ├── initial_status / final_status
  └── registry_path
```

### 3.1 HypothesisSpec

Defined in `engine/edge_discovery/hypotheses/spec.py`. A frozen dataclass declaring:

- `hypothesis_id`: unique identifier
- `asset_class`, `strategy_family`: classification
- `candidate_constraints`: ParameterConstraint list (entry_dpe, delta_target, expiry_rank ranges)
- `validation_plan`: methods (cpcv), holdout requirements
- `failure_modes`, `kill_criteria`: boundary conditions
- `required_data`: what inputs are needed (options_db, preearn_repo)

### 3.2 CandidateSpec Generation

`engine/edge_discovery/hypotheses/generate.py`. **Pure function** — no I/O, no subprocess. Takes a `HypothesisSpec` and returns a sorted tuple of `CandidateSpec` objects via cartesian product of constraint values.

### 3.3 Batch Runner

`engine/edge_discovery/hypotheses/batch.py`. `run_candidate_batch()` orchestrates:
1. Calls `generate_candidates()`
2. Calls `PreearnOptionsAdapter` for each candidate (subprocess call to pre-earnings script)
3. Aggregates results into `BatchResult`
4. Writes a batch-level `LedgerEntry`

Dry-run mode (`dry_run=True`) generates candidates and writes a batch summary without invoking the subprocess.

### 3.4 Ledger

`engine/edge_discovery/ledger.py`. `Ledger` is an append-only JSONL writer. Each `LedgerEntry` records: run_id, run_type, timestamps, status, config_hash, git_commit, metrics_summary, input/output artifacts.

### 3.5 Lifecycle

`engine/edge_discovery/hypotheses/lifecycle.py`. `register_and_run_batch()` coordinates:
- Resolving or creating a registry entry
- Enforcing status transitions
- Calling `run_candidate_batch`
- Returning a `LifecycleResult`

### 3.6 Evaluator

`engine/edge_discovery/evaluation.py`. `evaluate_batch_result()` and `evaluate_ledger_entry()` are **pure read-only** functions. They inspect a `BatchResult` or `LedgerEntry` and return an `EvaluationResult` with a label and reason. They do not write files, update registry, or call subprocesses.

### 3.7 Manual Smoke Scripts

Two scripts exist for manual developer testing only (not CI):
- `scripts/local/smoke_preearn_bridge.py` — loads an inline `HypothesisSpec`, runs it through batch, evaluates
- `scripts/local/smoke_preearn_lifecycle.py` — loads an example via `examples.py`, runs through lifecycle, evaluates

Both default to `dry-run`. Both require `--real-run` explicitly for actual backtest execution.

---

## 4. Pre-Earnings Repo Boundary

AED treats `/home/max/engine_linux_main` as an external system. The boundary is intentional and enforced by the adapter pattern.

**The only AED code that touches the pre-earnings repo is:**

`engine/edge_discovery/adapters/preearn_options.py`

This module calls **one script** and nothing else:

```
python3 /home/max/engine_linux_main/scripts/run_options_backtest_v1.py \
  --options-db PATH \
  --run-id ID \
  --entry-dpe N \
  --delta-target F \
  --expiry-rank N \
  --fill-policy MID \
  --spread-penalty-k 0.5 \
  --contract-multiplier 100.0 \
  --out-csv /path/to/output.csv
```

AED never:
- Imports any Python module from `engine_linux_main`
- Modifies any file in `engine_linux_main`
- Calls any internal API, class, or function of the pre-earnings system

**Responsibility split:**
- AED owns: hypothesis lifecycle, candidate generation, evaluation, ledger, registry
- Pre-earnings repo owns: options backtest execution, options data semantics, event/session logic, DPE semantics, delta/expiry selection, execution assumptions

---

## 5. Data Philosophy

**AED is data-source agnostic by design.**

AED primarily consumes pre-downloaded, cleaned, local datasets. Data acquisition is upstream and entirely optional from AED's perspective.

Core rules:
- AED does **not** download data as core behavior
- AED must **not** depend on IVOL or any single data vendor as a core dependency
- The options SQLite database is a **pre-provisioned local file** — AED receives its path as an argument; it does not acquire or validate the underlying data
- Data acquisition (download, scrape, purchase, clean) happens **before** AED runs

**Future data work** (see Roadmap) should use manifest/adapter patterns — not vendor-specific core modules. Local SQLite, CSV, Parquet, DuckDB, and remote URI sources may all be upstream providers, but AED should consume cleaned local representations through a declared interface.

---

## 6. Module Reference

| Module | Responsibility | I/O |
|---|---|---|
| `hypotheses/spec.py` | HypothesisSpec schema + HypothesisStatus enum | No I/O |
| `hypotheses/generate.py` | Cartesian candidate generation | No I/O (pure) |
| `hypotheses/batch.py` | Orchestrates candidate execution via adapter | Filesystem + subprocess |
| `hypotheses/lifecycle.py` | Coordinates registration + batch execution | Registry JSONL + batch |
| `hypotheses/registry.py` | HypothesisRegistry read/write | JSONL files |
| `adapters/preearn_options.py` | Subprocess wrapper for pre-earnings script | subprocess only |
| `evaluation.py` | Batch/ledger evaluation (read-only) | No I/O |
| `examples.py` | HypothesisSpec fixture loader | JSON file read |
| `ledger.py` | Append-only run ledger | JSONL append |
| `config.py` | Configuration + env-var overrides | No I/O |
| `runner.py` | WFA/CPCV runner (pre-hypothesis infra, separate) | Filesystem |

Legacy modules (not part of the hypothesis pipeline): `auditor.py`, `benchmarks.py`, `calibrate_ac_v2.py`, `calibrate_costs.py`, `costs.py`, `diagnostics.py`, `features.py`, `inference.py`, `integrations.py`, `metrics.py`, `pbo.py`, `schema.py`, `stationarity.py`.

---

## 7. Scripts

### `scripts/local/smoke_preearn_bridge.py`

Manual smoke: builds an inline `HypothesisSpec`, runs `run_candidate_batch`, then `evaluate_batch_result`. Default `dry-run`.

```bash
PYTHONPATH=. python3 scripts/local/smoke_preearn_bridge.py \
  --preearn-repo-path /home/max/engine_linux_main \
  --options-db-path /home/max/engine_linux_main/cache/scratch/options_2021_lane_0.sqlite \
  --dry-run \
  --output-dir .wfa/preearn_bridge_smoke
```

### `scripts/local/smoke_preearn_lifecycle.py`

Manual smoke: loads an example via `load_preearn_example`, runs `register_and_run_batch`, then `evaluate_batch_result`. Default `dry-run`.

```bash
PYTHONPATH=. python3 scripts/local/smoke_preearn_lifecycle.py \
  --example basic \
  --preearn-repo-path /home/max/engine_linux_main \
  --options-db-path /home/max/engine_linux_main/cache/scratch/options_2021_lane_0.sqlite \
  --dry-run \
  --output-dir .wfa/preearn_lifecycle_smoke
```

### `scripts/ci/audit_edge_discovery.sh`

CI audit: checks that hypothesis modules import only from allowed packages. Runs in CI on every PR.

---

## 8. Evaluation Labels

The evaluator (`evaluate_batch_result`, `evaluate_ledger_entry`) produces one of four labels:

| Label | Meaning |
|---|---|
| `invalid_run` | Nothing ran; no execution occurred |
| `execution_failed` | All candidates errored |
| `needs_more_data` | Execution occurred but thresholds not met |
| `promising_for_review` | Execution passed thresholds; ready for human review |

**Labels are review-only.** They describe execution quality — not hypothesis truth.

- `promising_for_review` does **not** mean the hypothesis is correct, profitable, or should be traded
- `needs_more_data` does **not** mean the hypothesis is wrong
- `needs_more_data` does **not** trigger any registry status change
- No label automatically updates registry status

A human must interpret evaluation labels in the context of the full hypothesis, market mechanism, and strategy risk. AED does not make promotion, acceptance, or rejection decisions.

---

## 9. Registry Lifecycle

The hypothesis registry (`HypothesisRegistry`) tracks status per hypothesis:

| Status | Meaning |
|---|---|
| `draft` | Declared but not yet registered |
| `registered` | Committed to the registry |
| `testing` | Batch execution attempted |
| `accepted` | Reserved — future promotion workflow |
| `rejected` | Reserved — future promotion workflow |
| `killed` | Reserved — future promotion workflow |

**Current behavior:** AED writes only `draft`, `registered`, and `testing`.

The values `accepted`, `rejected`, and `killed` are **reserved for a future promotion workflow that has not been designed**. Do not implement automation that transitions a hypothesis to any of these three values based on evaluator output or any other automated signal.

`register_and_run_batch()` transitions:
- New hypothesis: `draft → registered → testing` (before non-dry-run batch)
- Existing hypothesis: `registered → testing` (before non-dry-run batch)
- Dry-run final status: `registered` (no evaluation occurred)
- Non-dry-run final status: `testing` (evaluation occurred, awaiting human review)

---

## 10. What Is Safe in CI

**Safe in CI:**
- Fast, mocked unit tests (no subprocess, no real backtests)
- `scripts/ci/audit_edge_discovery.sh` (import correctness check)
- Schema validation tests
- Roundtrip tests
- Mocked smoke script tests

**Not safe in CI (manual-only):**
- Real pre-earnings backtest execution
- `--real-run` on either smoke script
- Any test that calls `run_preearn_backtest` without mocking
- Any test that writes to the pre-earnings repo

**Explicitly prohibited in CI:**
- IVOL API calls
- Pre-earnings repo modification
- Real backtests of any kind

---

## 11. Do-Not-Build-Yet List

The following are explicitly out of scope until stated otherwise:

| Item | Reason |
|---|---|
| IVOL or any vendor API as core AED dependency | Data-source agnostic rule; no API key in repo |
| Real backtests in CI | Test suite must remain fast and deterministic |
| Promotion workflow (accepted/rejected/killed automation) | Not designed yet; evaluator labels are review-only |
| Autonomous hypothesis search or breeding | Not designed yet |
| Ledger compaction or indexed query | JSONL append-only is sufficient for v1 |
| Parallel batch execution | Single-threaded is correct for v1 |
| Pre-earnings repo modifications | AED only calls the script interface |
| Multi-dataset heterogeneous backtests | v1 is single pre-earnings dataset only |
| HypothesisSpec schema versioning/migration | Acceptable for v1; no migration needed yet |
| Real-time or production trading | AED is a backtest and research framework only |

---

## 12. Future Roadmap

The following are **possible future work**, listed roughly in priority order. Items are not committed until designed and approved via PR.

### DataManifest v1
Declare what datasets AED requires as explicit interfaces rather than bare CLI string arguments. A `DatasetManifest` dataclass with source type, path/URI, schema version, and validation method. Default implementation checks file existence. Enables future data-source adapters without core AED changes.

### Local Dataset Resolver
A utility that resolves dataset paths from a manifest or config, checks existence, and validates basic schema. Could support SQLite, CSV, Parquet, and DuckDB through a common interface.

### Shared Smoke Utilities
Extract common evaluation printing and argument parsing from `smoke_preearn_bridge.py` and `smoke_preearn_lifecycle.py` into a shared module (`scripts/local/_shared.py`). Reduces duplication and ensures consistent output format across smoke tools.

### Evaluator CLI
A command-line tool to load a past batch result or ledger entry and print its evaluation classification:

```bash
python3 scripts/evaluate_cli.py --batch-id <run_id> --ledger-path .wfa/ledger.jsonl
```

Reads the ledger, extracts the entry, calls `evaluate_ledger_entry()`, prints label and reason. Useful for reviewing past runs without running a full smoke.

### Promotion Workflow (much later)
Human-in-the-loop workflow for accepting, rejecting, or killing hypotheses based on evaluation results and manual review. This requires:
- Explicit human approval step
- Registry transition to `accepted`/`rejected`/`killed`
- Audit trail
- Not automated — AED's role ends at "ready for review"

This is the last item on the roadmap and requires a full design before any implementation.
