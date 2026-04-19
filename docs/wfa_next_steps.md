Next steps (prioritized)

1) Wire runner to programmatic backtester entrypoint
   - Action: Confirm the module path and function signature for the backtester (e.g., engine.src.earnings_research.backtest.options_backtest_v1.run_backtest(strategy, split_index, n_splits, purge, cost_model)). If different, I will update runner._run_backtest_for_split to call it directly and remove the CLI fallback.
   - Why: programmatic calls are faster, easier to debug, and avoid subprocess parsing errors.

2) Calibrate Almgren	6Chriss cost model
   - Action: Provide historical trade or ADV/impact calibration data (or parameters gamma, eta, sigma, V). I will add a calibration script and tests.
   - Why: defaults are prototypical; calibration is required for realistic cost modeling.

3) Replace PBO surrogate with formal DSR/WFA implementation
   - Action: Provide preferred statistical approach or allow me to implement a DSR-style selection and p-value based PBO estimator.
   - Why: more rigorous PBO improves model selection reliability.

4) Add CI gating and PR template
   - Action: The workflow .github/workflows/wfa.yml runs a smoke WFA nightly and uploads outputs. I can add a PR template and recommend CI gates for tests.

5) Documentation & runbook
   - Action: Create runbook with CLI examples, parameter descriptions, and expected outputs. Add to docs/.

If you want, I can start on (1) now: find the exact backtester function and wire it. Otherwise I'll push the branch and open a draft PR upon your confirmation.
