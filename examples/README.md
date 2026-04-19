examples/
========

This folder contains small runnable examples demonstrating parts of the project.

calibrate_demo.py
-----------------
A lightweight script that demonstrates the calibrator end-to-end using
examples/trades.csv. It runs the OLS fit, computes bootstrap percentile CIs,
and (if matplotlib is installed) produces a histogram of the bootstrap
coefficient samples.

Run from the repository root::

    python examples/calibrate_demo.py

Output files created in examples/:
 - calibrate_params_demo.json  (fitted params + CIs)
 - calibrate_bootstrap.png     (histogram of bootstrap samples, if matplotlib available)

Requirements: pandas and numpy are used by the calibrator. matplotlib is
optional for plots.
