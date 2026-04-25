#!/usr/bin/env python3
"""Run the Edge Discovery auditor against an existing run's artifacts.

Usage:
  bin/apply_audit_to_run.py --y /path/to/Y.npy --metrics /path/to/per_split_metrics.json --run-id myrun123

This script is a conservative helper for ops: it does not modify existing artifacts
and it writes the audit report JSON to the default audit_reports/ directory via
engine.edge_discovery.auditor.save_audit_report.
"""
import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from engine.edge_discovery import auditor


def load_metrics(path: Path):
    if not path.exists():
        return None
    with path.open('r') as f:
        return json.load(f)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--y', type=Path, help='Path to Y numpy file (.npy)')
    p.add_argument('--metrics', type=Path, help='Path to per-split metrics JSON file')
    p.add_argument('--run-id', type=str, required=True)
    p.add_argument('--out-dir', type=Path, default=Path('audit_reports'))
    p.add_argument('--start-metrics', action='store_true', help='Start prometheus metrics server before audit')
    p.add_argument('--metrics-port', type=int, default=9005, help='Port for metrics server (default: 9005')
    args = p.parse_args()

    Y = None
    if args.y:
        if not args.y.exists():
            raise SystemExit(f'Y file not found: {args.y}')
        Y = np.load(str(args.y))
    if args.start_metrics:
        try:
            from engine.edge_discovery import metrics
            metrics.start_http_server(args.metrics_port)
            print(f'Metrics server started on port {args.metrics_port}')
        except ImportError:
            print(f'Warning: prometheus_client or metrics module not available, skipping metrics server')
        except Exception as e:
            print(f'Warning: could not start metrics server: {e}')

    per_split = None
    if args.metrics:
        per_split = load_metrics(args.metrics)

    report = auditor.run_backtest_audit(Y=Y, per_split_metrics=per_split)
    saved = auditor.save_audit_report(report, run_id=args.run_id, out_dir=str(args.out_dir))
    print('Saved audit report to', saved)


if __name__ == '__main__':
    main()
