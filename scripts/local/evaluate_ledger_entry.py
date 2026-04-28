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

import json
import sys
from typing import Optional

from engine.edge_discovery.evaluation import evaluate_ledger_entry

from scripts.local._ledger_review_shared import (
    evaluation_to_dict,
    find_ledger_entry,
)


def parse_args(argv: Optional[list[str]] = None):
    from scripts.local._ledger_review_shared import build_parser

    return build_parser(
        "Evaluate a single ledger entry by run_id and print the review result.",
        ledger_required=True,
    ).parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    entry = find_ledger_entry(args.ledger_path, args.run_id)
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
