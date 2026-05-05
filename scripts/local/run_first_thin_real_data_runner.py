#!/usr/bin/env python3
"""
CLI wrapper for the first thin real-data runner dry-run skeleton.

Usage:
    python scripts/local/run_first_thin_real_data_runner.py \
        --experiment-spec fixtures/experiment_spec_v1/valid_minimal.json \
        --output-path /tmp/dry_run_output.json \
        --run-owner "researcher@Cambridge"

No real backtest execution, no registry writes, no live trading.
"""
import sys
from pathlib import Path

# Ensure the engine package is on the path
_sys_path_insert = str(Path(__file__).resolve().parents[2])
if _sys_path_insert not in sys.path:
    sys.path.insert(0, _sys_path_insert)

from engine.edge_discovery.runners.first_thin_real_data_runner import main

if __name__ == "__main__":
    sys.exit(main())
