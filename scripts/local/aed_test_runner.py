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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Round-412 (PHASE 6): script-local import path setup.
# When this CLI is run as ``python3 scripts/local/aed_test_runner.py``
# Python puts ``scripts/local`` rather than the repository root
# on ``sys.path``. Add the repo root so the ``scripts.local``
# package is importable without setting PYTHONPATH externally.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

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


def run_impact_selected_tests(
    changed_paths: List[str],
    *,
    tier: str = "tier_2_cohesive_batch",
    final_candidate: bool = False,
    cwd: Optional[str] = None,
    log_path: Optional[str] = None,
    executor: Optional[ExecutorFn] = None,
) -> Dict[str, Any]:
    """Importable test-runner API (Round-70 PHASE 3-P1).

    Round-70 exact-head repair: the autonomous
    ``autocoder_run_controller`` repaired transition needs an
    importable function that invokes the shared test-selection
    and executor and returns a machine-readable result
    including ``return_code``, ``duration``, ``selected_tests``,
    ``selection_reason``, ``tier``, ``command`` and ``capped``.
    Performs no GitHub mutation. Writes only when ``log_path``
    is provided.
    """
    plan = select_tests_with_invocation(
        changed_paths=changed_paths,
        tier=ValidationTier(tier),
        final_candidate=final_candidate,
    )
    return run_selected_tests(
        plan=plan,
        cwd=cwd,
        log_path=log_path,
        pytest_args=None,
        executor=executor,
    )




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
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Round-70 PHASE 5: deterministically return a stub "
             "validation result without launching pytest. "
             "Intended for thin-wrapper tests that must not "
             "execute the repository suite. The output log is "
             "still written so callers can read the selected "
             "tests and tier.",
    )
    args = p.parse_args(argv)

    changed_paths = _read_paths(args.changed_paths_file)
    if args.dry_run:
        # Round-70 PHASE 5 dry-run: build the plan, write the
        # log, return a stub result with returncode=0. No
        # subprocess is launched. This is the only CI-safe
        # way to verify the CLI wrapper without invoking
        # the full repository test suite.
        tier = ValidationTier(args.tier)
        plan = select_tests_with_invocation(
            changed_paths=changed_paths,
            tier=tier,
            final_candidate=bool(args.final_candidate),
        )
        log_payload = {
            "selected_tests": plan.selected_tests,
            "selection_reason": plan.selection_reason,
            "tier": tier.value,
            "requires_full_validation": plan.requires_full_validation,
            "duration_seconds": 0.0,
            "returncode": 0,
            "command": ["pytest", "-q"] + plan.selected_tests,
            "capped": False,
            "dry_run": True,
        }
        Path(str(args.output_log)).parent.mkdir(
            parents=True, exist_ok=True
        )
        with open(args.output_log, "w") as _lh:
            json.dump(log_payload, _lh, indent=2)
        print(json.dumps({
            "tool": "aed_test_runner",
            "selected": plan.selected_tests,
            "tier": tier.value,
            "requires_full_validation": plan.requires_full_validation,
            "returncode": 0,
            "duration_seconds": 0.0,
            "dry_run": True,
        }))
        return 0

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
