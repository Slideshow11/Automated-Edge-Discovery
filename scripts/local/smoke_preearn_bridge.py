#!/usr/bin/env python3
"""Manual smoke script: HypothesisSpec -> Candidate batch -> pre-earnings adapter.

Usage: see docs/preearn_bridge_smoke.md

Defaults to dry-run. Real run requires --real-run and will invoke a local
pre-earnings repository via the adapter. The script writes all outputs under
output_dir and will write a batch-level ledger entry if ledger_path is given.

After the batch run, the script evaluates the BatchResult and prints the
evaluation label and reason for human review readiness.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from engine.edge_discovery.evaluation import (
    EvaluationLabel,
    EvaluationResult,
    evaluate_batch_result,
)
from engine.edge_discovery.hypotheses.batch import run_candidate_batch, BatchResult
from scripts.local._smoke_shared import (
    ensure_output_dir,
    print_batch_summary,
    warn_real_run,
)


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
    group.add_argument(
        "--real-run", dest="dry_run", action="store_false", help="Invoke local pre-earnings repo (must be explicit)"
    )
    p.set_defaults(dry_run=True)
    p.add_argument("--timeout", type=float, default=60.0)
    return p.parse_args(argv)


def make_smoke_hypothesis() -> HypothesisSpec:
    """Create a small deterministic HypothesisSpec for pre-earnings smoke.

    Requirements per PR: strategy_family preearn_options, asset_class equity_options,
    required_data contains options_db and preearn_repo, and candidate constraints as specified.
    """
    from engine.edge_discovery.hypotheses.spec import (
        HypothesisSpec,
        ParameterConstraint,
        ValidationPlan,
        SourceType,
        AssetClass,
        StrategyFamily,
    )

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


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    if not args.dry_run:
        warn_real_run("invoke the local pre-earnings repo via the adapter.")
        print("Ensure you know what the adapter will execute.", file=sys.stderr)

    out_dir = ensure_output_dir(args.output_dir)

    hypothesis = make_smoke_hypothesis()

    try:
        result = run_candidate_batch(
            hypothesis,
            options_db_path=str(args.options_db_path),
            preearn_repo_path=str(args.preearn_repo_path),
            ledger_path=args.ledger_path,
            output_dir=str(out_dir),
            max_candidates=args.max_candidates,
            dry_run=args.dry_run,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"Smoke run failed: {exc}", file=sys.stderr)
        return 2

    # Evaluate the batch result for review-readiness
    eval_result = evaluate_batch_result(result)

    # Print batch summary
    print_batch_summary(
        batch_id=result.batch_id,
        status=result.status,
        n_candidates_generated=result.n_candidates_generated,
        n_candidates_selected=result.n_candidates_selected,
        n_success=result.n_success,
        n_error=result.n_error,
        output_artifacts=result.output_artifacts,
        ledger_path=args.ledger_path,
    )

    # Print evaluation
    print()
    _print_evaluation(eval_result)

    return 0


def _print_evaluation(eval_result: EvaluationResult) -> None:
    """Print evaluation label and reason."""
    print(f"evaluation_label: {eval_result.label.value}")
    print(f"evaluation_reason: {eval_result.reason}")
    if eval_result.warnings:
        print("evaluation_warnings:")
        for w in eval_result.warnings:
            print(f"  - {w}")


if __name__ == "__main__":
    raise SystemExit(main())
