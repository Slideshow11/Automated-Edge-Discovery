"""Shared read-only helpers for ledger review scripts.

This module is purely utility code: argument parsing, ledger loading,
run_id lookup, and error formatting. It has no side effects, makes no
network calls, and does not modify any registry or ledger.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from engine.edge_discovery.evaluation import EvaluationResult
from engine.edge_discovery.ledger import Ledger, LedgerEntry


def build_parser(description: str, ledger_required: bool = True) -> argparse.ArgumentParser:
    """Build the standard ledger-review CLI argument parser.

    Parameters
    ----------
    description : str
        Help text for the CLI.
    ledger_required : bool
        If True (default), both --ledger-path and --run-id are required.
        If False, both are optional (for sub-commands that only output).
    """
    p = argparse.ArgumentParser(description=description)
    p.add_argument(
        "--ledger-path",
        required=ledger_required,
        help="Path to the ledger JSONL file.",
    )
    p.add_argument(
        "--run-id",
        required=ledger_required,
        help="Exact run_id to look up.",
    )
    return p


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse standard ledger-review CLI arguments."""
    p = build_parser("Ledger review CLI.")
    return p.parse_args(argv)


def find_ledger_entry(ledger_path: str, run_id: str) -> LedgerEntry:
    """Load a ledger, find exactly one entry by run_id, and return it.

    Raises
    ------
    SystemExit(1)
        If the ledger cannot be read, no entry matches, or multiple entries match.
    """
    ledger = Ledger(ledger_path)
    all_entries = ledger.read()

    if not all_entries:
        print("ERROR: ledger is empty or not found.", file=sys.stderr)
        raise SystemExit(1)

    matches = [e for e in all_entries if e.run_id == run_id]

    if len(matches) == 0:
        print(
            f"ERROR: no ledger entry found for run_id {run_id!r}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if len(matches) > 1:
        print(
            f"ERROR: multiple entries found for run_id {run_id!r} ({len(matches)} matches)",
            file=sys.stderr,
        )
        raise SystemExit(1)

    return matches[0]


def evaluation_to_dict(result: EvaluationResult) -> dict:
    """Convert an EvaluationResult to a JSON-safe dict."""
    return {
        "label": result.label.value,
        "reason": result.reason,
        "warnings": list(result.warnings),
        "source_type": result.source_type,
        "source_id": result.source_id,
    }
