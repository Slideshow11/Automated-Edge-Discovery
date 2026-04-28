#!/usr/bin/env python3
"""Manual ledger review CLI: find a ledger entry by run_id and print its evaluation.

Usage:
    python scripts/local/evaluate_ledger_entry.py \
        --ledger-path .wfa/ledger.jsonl \
        --run-id <run_id>

Output is JSON (one line) on success, plain-text errors to stderr on failure.
Exit codes: 0 = evaluated, 1 = error (not found, duplicate, etc.)
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from engine.edge_discovery.evaluation import evaluate_ledger_entry
from engine.edge_discovery.ledger import Ledger


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Evaluate a single ledger entry by run_id and print the review result."
    )
    p.add_argument(
        "--ledger-path",
        required=True,
        help="Path to the ledger JSONL file.",
    )
    p.add_argument(
        "--run-id",
        required=True,
        help="Exact run_id to look up.",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    ledger = Ledger(args.ledger_path)
    all_entries = ledger.read()

    matches = [e for e in all_entries if e.run_id == args.run_id]

    if len(matches) == 0:
        print(
            f"ERROR: no ledger entry found for run_id {args.run_id!r}",
            file=sys.stderr,
        )
        return 1

    if len(matches) > 1:
        print(
            f"ERROR: multiple entries found for run_id {args.run_id!r} ({len(matches)} matches)",
            file=sys.stderr,
        )
        return 1

    entry = matches[0]
    result = evaluate_ledger_entry(entry)

    output = {
        "run_id": result.source_id,
        "label": result.label.value,
        "reason": result.reason,
        "hypothesis_id": result.hypothesis_id,
        "source_type": result.source_type,
        "warnings": list(result.warnings),
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
