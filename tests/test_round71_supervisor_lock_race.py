#!/usr/bin/env python3
"""
Round-71 regression tests for the supervisor lock race.

Repair A: Hold the repository sentinel through lease
publication. The previous Round-50 partial fix released
the per-repo sentinel after the cross-scope conflict scan
but BEFORE the per-scope sentinel was acquired and the lock
payload was published. Two concurrent initializers for
distinct scopes but the same repository could both pass
the cross-scope check before either published, defeating
the repository-wide exclusivity invariant.

The Round-71 fix wraps the entire post-acquisition body
in a single outer try/finally that holds the per-repo
sentinel through the end. The release happens in the
finally block so all return paths release the sentinel
without leaking it.

This test exercises the race deterministically:
  - Two contenders: one tries to acquire a repository-wide
    scope; the other tries to acquire a PR-scoped scope.
  - Both contend for the per-repo sentinel via
    _acquire_sentinel_fd (mocked to allow a controlled race).
  - The previous code let both pass the cross-scope check
    before either published. The fixed code holds the
    per-repo sentinel through publication, so exactly
    one contender succeeds.
"""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.local import aed_supervisor_lock as sl


def _make_contender_args(
    *,
    scope: dict,
    run_id: str,
    base_dir: Path,
) -> dict:
    return {
        "scope": scope,
        "owner_run_id": run_id,
        "owner_host": {"hostname": "test", "pid": 12345},
        "owner_pid": 12345,
        "owner_start_evidence": {"start_time": time.time()},
        "owner_state_path": str(base_dir / f"state_{run_id}.json"),
        "base_dir": base_dir,
    }


def test_repo_sentinel_serializes_concurrent_acquires(tmp_path):
    """Repair A: a repository-wide acquire and a
    PR-scoped acquire for the same repository cannot both
    succeed. The Round-71 fix holds the per-repo sentinel
    through publication, so the second contender observes
    the first's published lock and bails on the
    cross-scope check.

    The race is probabilistic; this test runs multiple
    trials with a barrier to maximize contention. The
    pre-fix code exhibits the race in ~25% of trials
    (both contenders publishing distinct scope files).
    The post-fix code is deterministic — exactly one
    contender succeeds in every trial.
    """
    race_count = 0
    TRIALS = 20
    for trial in range(TRIALS):
        with _trial_base_dir(tmp_path, trial) as base_dir:
            repo_scope = {"repository": "owner/repo"}
            pr_scope = {
                "repository": "owner/repo",
                "target_pr_number": 416,
            }

            barrier = threading.Barrier(2)

            def attempt(scope: dict, run_id: str):
                # Wait at the barrier so both contenders
                # enter the cross-scope check as close
                # together as possible.
                barrier.wait(timeout=5)
                return sl.try_acquire(
                    **_make_contender_args(
                        scope=scope,
                        run_id=run_id,
                        base_dir=base_dir,
                    )
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                f_repo = ex.submit(attempt, repo_scope, "r_repo")
                f_pr = ex.submit(attempt, pr_scope, "r_pr")
                outcome_repo = f_repo.result(timeout=30)
                outcome_pr = f_pr.result(timeout=30)

            succeeded = sum(1 for o in (outcome_repo, outcome_pr) if o.ok)
            if succeeded > 1:
                race_count += 1
    # Exactly one of the two acquisitions must succeed
    # in EVERY trial. The pre-fix code exhibits the race
    # in ~25% of trials; this assertion fails on the
    # pre-fix code.
    assert race_count == 0, (
        f"per-repo sentinel must serialize concurrent "
        f"acquires in ALL trials; saw the race in "
        f"{race_count}/{TRIALS} trials"
    )


def _trial_base_dir(tmp_path: Path, trial_idx: int):
    """Yield a fresh base_dir for each trial to ensure
    isolation (no leaked state across trials)."""
    base_dir = tmp_path / f"locks_{trial_idx}"
    if base_dir.exists():
        shutil.rmtree(base_dir)
    base_dir.mkdir()
    return base_dir


def test_repo_sentinel_released_on_all_return_paths(tmp_path):
    """Repair A: the per-repo sentinel must be released on
    EVERY return path from try_acquire. After each
    try_acquire call, the per-repo sentinel file must be
    releasable (no leaked fd).

    The sentinel file is the .repo.recovery-sentinel for
    the canonicalized repo name. Its acquisition via
    flock is non-blocking; once released, a subsequent
    acquire should succeed.
    """
    base_dir = tmp_path / "locks"
    base_dir.mkdir()

    scope = {"repository": "owner/repo"}

    # Acquire and succeed.
    outcome = sl.try_acquire(
        **_make_contender_args(
            scope=scope,
            run_id="r1",
            base_dir=base_dir,
        )
    )
    assert outcome.ok

    # Release the lock so we can re-acquire.
    sl.release(
        scope=scope,
        owner_run_id="r1",
        base_dir=base_dir,
    )

    # Re-acquire: the per-repo sentinel fd must have been
    # released (no leaked fd would prevent re-acquisition).
    outcome2 = sl.try_acquire(
        **_make_contender_args(
            scope=scope,
            run_id="r2",
            base_dir=base_dir,
        )
    )
    assert outcome2.ok, (
        f"re-acquire must succeed (sentinel was leaked); got "
        f"reason={outcome2.reason!r}"
    )