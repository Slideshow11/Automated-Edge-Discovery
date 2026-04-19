Automated Edge Discovery
=======================

A small research utility for walk-forward analysis (WFA) / CPCV orchestration and simple Almgren–Chriss cost modeling utilities.

Quick start
-----------

1. Create and activate a virtual environment (recommended):

   python -m venv venv
   . venv/bin/activate

2. Install in editable mode:

   pip install -e .

3. Run the test suite:

   make test

4. Example usage (python):

   from engine.edge_discovery.runner import run_wfa_cpcv
   run_wfa_cpcv(["buy_hold"], n_splits=2, out_dir=".wfa/output")

Development notes
-----------------

- The package is small and intentionally kept lightweight for fast CI runs.
- To run tests locally use `make test` which sets PYTHONPATH and runs pytest for
  the key tests. After `pip install -e .` PYTHONPATH is not necessary but the
  make target sets it for convenience.

Files added/changed
- README.md: this file
- Makefile: convenience targets (make test)
- setup.cfg / setup.py: editable install support
- engine/edge_discovery/calibrate_costs.py: CLI skeleton for cost calibration
- .github/workflows/wfa.yml: CI updated to install editable package & run tests
- engine/edge_discovery/runner.py: fixed timezone-aware timestamp to silence
  DeprecationWarning

Notes
-----
- The calibration CLI is a lightweight helper that reads a CSV of trades and
  derives placeholder Almgren–Chriss parameters. It includes a unit test using
  synthetic data.
- If you add heavy dependencies (pandas) they are only required for specific
  tests and are installed in CI/test venv as needed.
