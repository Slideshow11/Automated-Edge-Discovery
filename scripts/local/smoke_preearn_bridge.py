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
import json
import sys
from pathlib import Path
from typing import Optional

from engine.edge_discovery.evaluation import (
    EvaluationLabel,
    EvaluationResult,
    evaluate_batch_result,
    evaluate_ledger_entry,
)
from engine.edge_discovery.hypotheses.batch import run_candidate_batch, BatchResult
from engine.edge_discovery.ledger import LedgerEntry


DEFAULT_OUTPUT = ".wfa/preearn_bridge_smoke"


def parse_args(argv: Optional[list[str]] = None):
    p = argparse.ArgumentParser(description="Manual smoke: pre-earnings bridge")
    p.add_argument("--preearn-repo-path", required=True)
    p.add_argument("--options-db-path", required=True)
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    p.add_argument("--ledger-path", default=None)
    p.add_argument("--max-candidates", type=int, default=1)
    p.add_argument(
        "--evaluate-only",
        dest="evaluate_only",
        action="store_true",
        help=(
            "Skip batch run. Load a ledger entry from --ledger-path and print "
            "its evaluation. Requires --ledger-path and --options-db-path."
        ),
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument("--dry-run", dest="dry_run", action="store_true", help="Dry run (default)")
    group.add_argument(
        "--real-run", dest="dry_run", action="store_false", help="Invoke local pre-earnings repo (must be explicit)"
    )
    p.set_defaults(dry_run=True, evaluate_only=False)
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


def _build_batch_result_from_ledger(ledger_entry: LedgerEntry) -> BatchResult:
    """Reconstruct a BatchResult from a ledger entry's metrics_summary."""
    ms = ledger_entry.metrics_summary
    return BatchResult(
        batch_id=ledger_entry.run_id,
        hypothesis_id=ms.get("hypothesis_id", ""),
        status=ms.get("batch_status", "error"),
        n_candidates_generated=ms.get("n_candidates_generated", 0),
        n_candidates_selected=ms.get("n_candidates_selected", 0),
        n_success=ms.get("n_success", 0),
        n_error=ms.get("n_error", 0),
        results=[],
        output_artifacts=ledger_entry.output_artifacts,
    )


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

    The returned BatchResult will have a _evaluation attribute set if
    evaluate_batch_result was able to produce a label.

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

    # Evaluate the batch result for review-readiness
    eval_result = evaluate_batch_result(result)
    result._evaluation = eval_result  # type: ignore[attr-defined]

    return result


def run_evaluate_only(ledger_path: str) -> EvaluationResult:
    """Load a ledger entry and evaluate it for review-readiness.

    Returns the EvaluationResult. Raises FileNotFoundError if the ledger
    does not exist or contains no entries.
    """
    entries = []
    with open(ledger_path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))

    if not entries:
        raise FileNotFoundError(f"No ledger entries found in {ledger_path}")

    # Evaluate the most recent entry
    latest = entries[-1]
    entry = LedgerEntry(**latest)
    return evaluate_ledger_entry(entry)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    if args.evaluate_only:
        if not args.ledger_path:
            print("--evaluate-only requires --ledger-path", file=sys.stderr)
            return 1
        try:
            eval_result = run_evaluate_only(args.ledger_path)
        except FileNotFoundError as exc:
            print(f"Evaluate-only failed: {exc}", file=sys.stderr)
            return 1
        _print_evaluation(eval_result)
        return 0

    if not args.dry_run:
        print(
            "WARNING: --real-run specified. This will invoke the local pre-earnings repo via the adapter.",
            file=sys.stderr,
        )
        print("Ensure you know what the adapter will execute.", file=sys.stderr)

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
        print(f"Smoke run failed: {exc}", file=sys.stderr)
        return 2

    # Print batch summary
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

    # Print evaluation
    eval_result: Optional[EvaluationResult] = getattr(result, "_evaluation", None)
    if eval_result is not None:
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
