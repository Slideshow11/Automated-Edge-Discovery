#!/usr/bin/env python3
"""Manual run review packet generator.

Reads a ledger entry and its evaluation, and produces a compact review packet
as JSON to stdout (or to a file if --output-path is specified).

Usage:
    python scripts/local/make_run_review_packet.py \
        --ledger-path .wfa/ledger.jsonl \
        --run-id <run_id> \
        [--output-path /path/to/packet.json]

Output is a JSON packet to stdout by default.
With --output-path: writes the same packet to the path and prints a confirmation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from engine.edge_discovery.evaluation import evaluate_ledger_entry

from scripts.local._ledger_review_shared import (
    build_parser,
    evaluation_to_dict,
    find_ledger_entry,
)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = build_parser(
        "Build a manual review packet for a ledger run.",
        ledger_required=True,
    )
    p.add_argument(
        "--output-path",
        default=None,
        help="If provided, write the packet to this path instead of stdout.",
    )
    return p.parse_args(argv)


def build_packet(entry, evaluation) -> dict:
    """Assemble a review packet from a LedgerEntry and its EvaluationResult."""
    return {
        "run_id": entry.run_id,
        "hypothesis_id": entry.input_artifacts.get("hypothesis_id", ""),
        "timestamp": entry.started_at,
        "evaluation": evaluation_to_dict(evaluation),
        "ledger": {
            "run_type": entry.run_type,
            "status": entry.status,
            "config_hash": entry.config_hash,
            "metrics_summary": entry.metrics_summary,
            "output_artifacts": entry.output_artifacts,
        },
        "review_guidance": {
            "manual_review_required": True,
            "registry_mutation": False,
        },
    }


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    entry = find_ledger_entry(args.ledger_path, args.run_id)
    evaluation = evaluate_ledger_entry(entry)
    packet = build_packet(entry, evaluation)

    if args.output_path:
        out_path = Path(args.output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(packet, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Review packet written to {args.output_path}")
        return 0

    print(json.dumps(packet, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
