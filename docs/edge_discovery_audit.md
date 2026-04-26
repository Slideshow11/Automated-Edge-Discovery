Edge Discovery — Backtest Audit

Overview
--------
This document describes the conservative backtest audit added to Edge Discovery.
The audit computes a Probability of Backtest Overfitting (PBO) via bootstrap on
split-level performance (Y matrix), computes a deflated Sharpe proxy, and
applies a pass/fail gate based on configurable thresholds.

Files
-----
- engine/edge_discovery/auditor.py — run_backtest_audit, save_audit_report
- engine/edge_discovery/pbo.py — compute_pbo, deflated_sharpe[_dspr]
- engine/edge_discovery/config.py — defaults and get_config()
- engine/edge_discovery/runner.py — minimal runner; safe wiring example
- engine/edge_discovery/cleanup_audit_reports.py — retention utility
- audit_reports/ — default local persistence dir (created on demand)

Config keys (engine/edge_discovery/config.py)
---------------------------------------------
- AUDIT_ENABLED (bool): enable/disable audit calls. Default: True
- PBO_THRESHOLD_DEFAULT (float): PBO threshold for passing. Default: 0.05
- SHARPE_MIN_DEFAULT (float): min deflated Sharpe required. Default: 1.0
- AUDIT_ON_FAIL (str): behavior on audit fail: 'log' (default) | 'block'

Audit report JSON schema (summary)
----------------------------------
- pass (bool)
- reason (str)
- pbo (float)
- pbo_std (float)
- deflated_sharpe (array[float])
- aggregated_metrics (object)
- config (object)

Usage
-----
- For local runs, the runner saves JSON reports under ./audit_reports/<run_id>.json
- The minimal runner demonstrates wiring; to integrate into production runners,
  call engine.edge_discovery.auditor.run_backtest_audit(...) after backtest completion,
  save the report (auditor.save_audit_report), attach path to run metadata, and
  log a structured audit_summary.

Retention
---------
Use engine.edge_discovery.cleanup_audit_reports.cleanup() to delete old/excess
reports. A system cron, CI job, or scheduled task should invoke it periodically.

Notes & Next steps
------------------
- Current deflated Sharpe implementation is a practical proxy (deflated_sharpe_dspr).
  For strict statistical requirements, replace with a formal Lopez de Prado DSR
  implementation and validate with domain experts.
- Consider shipping audit reports to central storage (S3/DB) if required for
  observability and long-term retention.
