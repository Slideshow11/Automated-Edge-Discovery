#!/usr/bin/env python3
"""Manual lifecycle smoke script: example → spec → registry → batch → evaluation.

Usage: see docs/preearn_lifecycle_smoke.md

Defaults to dry-run. Optional --real-run enables real batch execution.

This script demonstrates the full AED lifecycle pipeline:
  1. Load a HypothesisSpec from the example loader
  2. Register/validate it via HypothesisRegistry
  3. Run a candidate batch (dry-run by default)
  4. Evaluate the BatchResult

WARNING: This script is for manual smoke testing only. It does not perform
promotion, rejection, or any accepted/rejected/killed automation.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from engine.edge_discovery.evaluation import (
    EvaluationResult,
    evaluate_batch_result,
)
from engine.edge_discovery.examples import load_preearn_example
from engine.edge_discovery.hypotheses.lifecycle import (
    LifecycleResult,
    register_and_run_batch,
)
from engine.edge_discovery.data_manifest import DatasetRole
from scripts.local._smoke_shared import (
    ensure_output_dir,
    resolve_path_from_manifest,
    warn_real_run,
)


DEFAULT_OUTPUT = ".wfa/preearn_lifecycle_smoke"


def parse_args(argv: Optional[list[str]] = None):
    p = argparse.ArgumentParser(
        description="Manual lifecycle smoke: example → spec → registry → batch → evaluation."
    )
    p.add_argument(
        "--example",
        required=True,
        choices=["basic", "coarse"],
        help="Example hypothesis to load.",
    )
    p.add_argument("--preearn-repo-path", default=None)
    p.add_argument(
        "--preearn-repo-manifest",
        default=None,
        help="Path to a DataManifest JSON for the pre-earnings repo. "
        "May be used alone or alongside --preearn-repo-path; "
        "explicit --preearn-repo-path takes precedence.",
    )
    p.add_argument("--options-db-path", default=None)
    p.add_argument(
        "--options-db-manifest",
        default=None,
        help="Path to a DataManifest JSON for the options DB. "
        "May be used alone or alongside --options-db-path; "
        "explicit --options-db-path takes precedence.",
    )
    p.add_argument(
        "--registry-path",
        default=None,
        help="Path to HypothesisRegistry JSONL. Default: uses get_config().",
    )
    p.add_argument(
        "--ledger-path",
        default=None,
        help="Path to Ledger JSONL. Default: uses get_config().",
    )
    p.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT,
    )
    p.add_argument(
        "--max-candidates",
        type=int,
        default=1,
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Dry run (default).",
    )
    group.add_argument(
        "--real-run",
        dest="dry_run",
        action="store_false",
        help="Explicit flag: enable real batch execution.",
    )
    p.set_defaults(dry_run=True)
    p.add_argument(
        "--timeout",
        type=float,
        default=60.0,
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    if not args.dry_run:
        warn_real_run("run the pre-earnings backtest adapter for real. Ensure you know what will be executed.")

    out_dir = ensure_output_dir(args.output_dir)

    # Resolve effective paths: explicit arg > manifest-derived path
    options_db_path: str
    if args.options_db_path:
        options_db_path = args.options_db_path
    elif args.options_db_manifest:
        options_db_path = resolve_path_from_manifest(
            args.options_db_manifest, DatasetRole.options_backtest_db
        )
    else:
        print(
            "ERROR: either --options-db-path or --options-db-manifest is required",
            file=sys.stderr,
        )
        raise SystemExit(1)

    preearn_repo_path: str
    if args.preearn_repo_path:
        preearn_repo_path = args.preearn_repo_path
    elif args.preearn_repo_manifest:
        preearn_repo_path = resolve_path_from_manifest(
            args.preearn_repo_manifest, DatasetRole.preearn_repo
        )
    else:
        print(
            "ERROR: either --preearn-repo-path or --preearn-repo-manifest is required",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Step 1: Load example
    spec = load_preearn_example(args.example)

    # Step 2: Run lifecycle
    lifecycle_result = register_and_run_batch(
        hypothesis=spec,
        registry_path=args.registry_path,
        options_db_path=options_db_path,
        preearn_repo_path=preearn_repo_path,
        ledger_path=args.ledger_path,
        output_dir=str(out_dir),
        max_candidates=args.max_candidates,
        dry_run=args.dry_run,
        timeout=args.timeout,
    )

    # Step 3: Evaluate
    if lifecycle_result.batch_result is not None:
        evaluation: EvaluationResult | None = evaluate_batch_result(
            lifecycle_result.batch_result
        )
    else:
        evaluation = None

    # Step 4: Print summary
    _print_lifecycle_summary(args.example, lifecycle_result, evaluation)

    return 0


def _print_lifecycle_summary(
    example_name: str,
    result: LifecycleResult,
    evaluation: EvaluationResult | None,
) -> None:
    print("=" * 60)
    print("LIFECYCLE SMOKE SUMMARY")
    print("=" * 60)

    print(f"example:          {example_name}")
    print(f"hypothesis_id:    {result.hypothesis_id}")
    print(f"initial_status:   {result.initial_status}")
    print(f"final_status:     {result.final_status}")

    if result.batch_result is not None:
        br = result.batch_result
        print(f"batch_id:         {br.batch_id}")
        print(f"batch_status:     {br.status}")
        print(f"n_candidates_generated: {br.n_candidates_generated}")
        print(f"n_candidates_selected:  {br.n_candidates_selected}")
        print(f"n_success:        {br.n_success}")
        print(f"n_error:          {br.n_error}")
    else:
        print("batch_id:         none")
        print("batch_status:     none")
        print("n_candidates_generated: 0")
        print("n_candidates_selected:  0")
        print("n_success:        0")
        print("n_error:          0")

    if evaluation is not None:
        print(f"evaluation_label: {evaluation.label.value}")
        print(f"evaluation_reason: {evaluation.reason}")
        print(f"evaluation_warnings: {list(evaluation.warnings)}")
    else:
        print("evaluation_label: none")
        print("evaluation_reason: no_batch_result")
        print("evaluation_warnings: []")

    print(f"registry_path:    {result.registry_path}")
    print("=" * 60)


if __name__ == "__main__":
    raise SystemExit(main())
