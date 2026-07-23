#!/usr/bin/env python3
"""Autocoder impact-selected test runner (PHASE 6).

Invoked by the autonomous repair cycle to execute the test
plan produced by :mod:`aed_repair_planner`. Uses the SHARED
test selector and records machine-readable evidence.

Usage::

  python3 scripts/local/aed_test_runner.py \
      --changed-paths-file paths.txt \
      --output-log run.json

``paths.txt`` is one changed path per line. ``run.json``
contains the test plan, the exact command executed, the
return code, the duration, and the selection reason.

Dependency injection: ``run_selected_tests`` is imported
into the module namespace so production callers (the CLI
``main()``) use the real implementation. Tests inject a
fake executor via ``main(argv=..., executor=fake)`` to
avoid actually running the repository's five-minute test
suite.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Dict, List, Optional

from scripts.local._production_facade import (
    select_tests_with_invocation,
    run_selected_tests,
)
from scripts.local._shared_test_selection import ValidationTier


# Type alias for the executor dependency.
ExecutorFn = Callable[..., Dict[str, Any]]


def _read_paths(path: str) -> List[str]:
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def main(
    argv: Optional[List[str]] = None,
    *,
    executor: Optional[ExecutorFn] = None,
) -> int:
    """Test-runner entrypoint.

    ``executor`` is the production seam: tests inject a fake
    executor to avoid running the real pytest suite. When
    ``executor`` is None, the production
    :func:`run_selected_tests` from the facade is used.
    """
    if executor is None:
        executor = run_selected_tests

    p = argparse.ArgumentParser(
        description="Impact-selected test runner (PHASE 6)."
    )
    p.add_argument(
        "--changed-paths-file",
        required=True,
        help="One changed path per line.",
    )
    p.add_argument(
        "--tier",
        choices=["tier_1_inner_repair", "tier_2_cohesive_batch",
                 "tier_3_final_candidate"],
        default="tier_2_cohesive_batch",
        help="Validation tier (default: tier_2_cohesive_batch).",
    )
    p.add_argument(
        "--final-candidate",
        action="store_true",
        help="Mark this run as final-candidate (full "
             "validation required).",
    )
    p.add_argument(
        "--output-log",
        required=True,
        help="Destination JSON file for the run log.",
    )
    p.add_argument(
        "--cwd",
        default=None,
        help="Working directory (default: current directory).",
    )
    args = p.parse_args(argv)

    changed_paths = _read_paths(args.changed_paths_file)
    tier = ValidationTier(args.tier)
    plan = select_tests_with_invocation(
        changed_paths=changed_paths,
        tier=tier,
        final_candidate=bool(args.final_candidate),
    )

    result = executor(
        plan=plan,
        cwd=args.cwd,
        log_path=args.output_log,
    )
    # Print a one-line machine-readable summary.
    print(json.dumps({
        "tool": "aed_test_runner",
        "selected": result.get("selected"),
        "tier": result.get("tier"),
        "requires_full_validation": result.get(
            "requires_full_validation"
        ),
        "returncode": result.get("returncode"),
        "duration_seconds": result.get("duration_seconds"),
    }))
    return 0 if result.get("returncode", 1) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
