#!/usr/bin/env python3
"""
ci_check_smoke.py — CI helper to verify PR gate live smoke report.

Exit codes:
  0 = smoke_pass
  1 = smoke_fail or report not found

This script is READ-ONLY. It only reads the smoke report JSON and exits.
"""
from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    if len(argv) != 2:
        print(f"Usage: {argv[0] if argv else 'ci_check_smoke.py'} <smoke_output_dir>", file=sys.stderr)
        return 2

    output_dir = Path(argv[1])
    report_json = output_dir / "PR_GATE_CONTROLLER_LIVE_SMOKE_REPORT.json"

    if not report_json.exists():
        print(f"SMOKE REPORT NOT FOUND: {report_json}", file=sys.stderr)
        return 1

    import json
    with open(report_json) as f:
        report = json.load(f)

    passed = report.get("summary", {}).get("passed", False)
    failed = report.get("summary", {}).get("failed_scenarios", [])

    if not passed:
        print(f"SMOKE FAILED: {failed}", file=sys.stderr)
        return 1

    print("SMOKE PASSED", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))