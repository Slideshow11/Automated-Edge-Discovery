#!/usr/bin/env python3
"""Manual smoke script: HypothesisSpec -> Candidate batch -> pre-earnings adapter.

Usage: see docs/preearn_bridge_smoke.md

Defaults to dry-run. Real run requires --real-run and will invoke a local
pre-earnings repository via the adapter. The script writes all outputs under
output_dir and will write a batch-level ledger entry if ledger_path is given.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from engine.edge_discovery.hypotheses.spec import (
    HypothesisSpec,
    ParameterConstraint,
    ValidationPlan,
    SourceType,
    AssetClass,
    StrategyFamily,
)
from engine.edge_discovery.hypotheses.batch import run_candidate_batch, BatchResult


DEFAULT_OUTPUT = ".wfa/preearn_bridge_smoke"


def parse_args(argv: Optional[list[str]] = None):
    p = argparse.ArgumentParser(description="Manual smoke: pre-earnings bridge")
    p.add_argument("--preearn-repo-path", required=True)
    p.add_argument("--options-db-path", required=True)
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    p.add_argument("--ledger-path", default=None)
    p.add_argument("--max-candidates", type=int, default=1)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--dry-run", dest="dry_run", action="store_true", help="Dry run (default)")
    group.add_argument("--real-run", dest="dry_run", action="store_false", help="Invoke local pre-earnings repo (must be explicit)")
    p.set_defaults(dry_run=True)
    p.add_argument("--timeout", type=float, default=60.0)
    return p.parse_args(argv)


def make_smoke_hypothesis() -> HypothesisSpec:
    """Create a small deterministic HypothesisSpec for pre-earnings smoke.

    Requirements per PR: strategy_family preearn_options, asset_class equity_options,
    required_data contains options_db and preearn_repo, and candidate constraints as specified.
    """
    hc = (
        ParameterConstraint(name="entry_dpe", values=(2,)),
        ParameterConstraint(name="delta_target", values=(0.5,)),
        ParameterConstraint(name="expiry_rank", values=(0,)),
    )

    vp = ValidationPlan(methods=("cpcv",), holdout_required=False, notes="smoke")

    hs = HypothesisSpec(
        hypothesis_id="smoke-preearn-0001",
        version="1.0.0",
        source_type=SourceType.internal_research,
        source_reference="local_smoke",
        market_mechanism="example",
        expected_effect="small smoke test",
        asset_class=AssetClass.equity_options,
        strategy_family=StrategyFamily.preearn_options,
        required_data=("options_db", "preearn_repo"),
        candidate_constraints=hc,
        validation_plan=vp,
        failure_modes=(),
        kill_criteria=(),
    )
    return hs


def run_smoke(
    preearn_repo_path: str,
    options_db_path: str,
    output_dir: str = DEFAULT_OUTPUT,
    ledger_path: Optional[str] = None,
    max_candidates: int = 1,
    dry_run: bool = True,
    timeout: float = 60.0,
) -> BatchResult:
    """Execute the smoke path and return the BatchResult.

    This function is safe for dry-run mode; in real-run mode it will invoke
    the local pre-earnings adapter via run_candidate_batch.
    """
    hypothesis = make_smoke_hypothesis()

    result = run_candidate_batch(
        hypothesis,
        options_db_path=str(options_db_path),
        preearn_repo_path=str(preearn_repo_path),
        ledger_path=ledger_path,
        output_dir=output_dir,
        max_candidates=max_candidates,
        dry_run=dry_run,
        timeout=timeout,
    )
    return result


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    if not args.dry_run:
        print("WARNING: --real-run specified. This will invoke the local pre-earnings repo via the adapter.")
        print("Ensure you know what the adapter will execute.")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = run_smoke(
            preearn_repo_path=args.preearn_repo_path,
            options_db_path=args.options_db_path,
            output_dir=str(out_dir),
            ledger_path=args.ledger_path,
            max_candidates=args.max_candidates,
            dry_run=args.dry_run,
            timeout=args.timeout,
        )
    except Exception as exc:
        print("Smoke run failed:", exc, file=sys.stderr)
        return 2

    # Print summary
    print(f"batch_id: {result.batch_id}")
    print(f"status: {result.status}")
    print(f"n_candidates_generated: {result.n_candidates_generated}")
    print(f"n_candidates_selected: {result.n_candidates_selected}")
    print(f"n_success: {result.n_success}")
    print(f"n_error: {result.n_error}")
    print("output_artifacts:")
    for k, v in result.output_artifacts.items():
        print(f"  {k}: {v}")
    if args.ledger_path:
        print(f"ledger_path: {args.ledger_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
