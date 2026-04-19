PR: feat/wfa-cpcv-claude-code

Title: feat(wfa): integrate WFA runner, Almgren–Chriss cost model, DSR-PBO estimator, CLI, tests

Summary:
- Adds a WFA/CPCV runner wrapper (engine/edge_discovery/runner.py) with a separable helper _run_backtest_for_split. The helper attempts to call a programmatic backtester API if available and otherwise shells out to a CLI.
- Adds a simplified Almgren–Chriss transaction-cost model (engine/edge_discovery/costs.py) for prototype cost adjustments.
- Replaces placeholder PBO estimator with a documented DSR-style empirical surrogate (engine/edge_discovery/auditor.py).
- Adds a CLI wrapper at bin/run_wfa for running WFA jobs from the shell.
- Adds integration/unit tests (tests/test_wfa_cpcv_integration.py) that mock heavy backtester calls so CI is fast and deterministic.

Files changed:
- bin/run_wfa
- engine/edge_discovery/runner.py
- engine/edge_discovery/auditor.py
- engine/edge_discovery/costs.py
- tests/test_wfa_cpcv_integration.py
- docs/wfa_run_examples.md (existing)

Notes for reviewers:
- The Almgren–Chriss model is a prototype and requires calibration (gamma/eta/sigma/V) for production.
- The runner will attempt to import: engine.src.earnings_research.backtest.options_backtest_v1.run_backtest — if your repo exposes a different function name or module path, update runner._run_backtest_for_split to call the correct function. Otherwise the runner falls back to invoking the backtester as a module via `python -m ...` CLI.
- estimate_pbo in auditor.py is an empirical surrogate inspired by DSR. Replace with your preferred PBO estimator if desired.

How to run locally:
- Activate venv: source venv/bin/activate
- Run tests: pytest -q tests/test_wfa_cpcv_integration.py
- Run CLI example: bin/run_wfa --strategies=buy_hold --n-splits=2 --purge=0.01 --out-dir=./wfa_out

Requested reviewers: @your-team
