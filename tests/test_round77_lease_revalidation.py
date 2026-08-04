#!/usr/bin/env python3
"""
Round-77 regression test for Repair F.

Repair F: Revalidate the supervisor lease before
executing. The controller's _mutate_ref now calls
is_lease_held_by_run immediately before orch.execute().
If the lease is no longer held, the function exits 11
(fail closed) so a superseded executor cannot race a
replacement run.

Structural verification test (the race requires
concurrent threads and timing):
  - Verify the controller's _mutate_ref function
    calls is_lease_held_by_run before orch.execute().
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.local import autocoder_run_controller as ctrl_mod


def test_f_mutate_ref_revalidates_lease_before_execute():
    """F.1: the controller's _mutate_ref function calls
    is_lease_held_by_run before orch.execute() so a
    superseded executor cannot race a replacement run.
    """
    src = inspect.getsource(ctrl_mod._mutate_ref)
    # Find ALL calls to is_lease_held_by_run (skip
    # comments). Use a regex with "is_lease_held_by_run("
    # to match only call sites.
    lease_check_matches = [
        m.start() for m in re.finditer(
            r"\bis_lease_held_by_run\s*\(", src
        )
    ]
    assert lease_check_matches, (
        "_mutate_ref must call is_lease_held_by_run "
        "(the release-on-return variant) to revalidate "
        "the lease immediately before the executor"
    )
    # The revalidation check must come BEFORE the
    # orch.execute() in the PREPARED branch. The
    # orch.execute() calls appear in the NOT_APPLIED
    # branch (after prepare()), the EXECUTING branch
    # (reconcile), the RECONCILING branch, and the
    # PREPARED branch. The one in the PREPARED branch
    # is the LAST one. Find it.
    orch_matches = [
        m.start() for m in re.finditer(r"orch\.execute\s*\(", src)
    ]
    assert orch_matches, "_mutate_ref must call orch.execute()"
    # The PREPARED branch is the last branch in the
    # chain. The orch.execute in the PREPARED branch is
    # the LAST call (after EXECUTING and RECONCILING).
    prep_orch = orch_matches[-1]
    # Find the LAST is_lease_held_by_run call (in the
    # PREPARED branch).
    last_lease = lease_check_matches[-1]
    # The lease check must come BEFORE the PREPARED
    # branch's executor call.
    assert last_lease < prep_orch, (
        f"is_lease_held_by_run must be called BEFORE "
        f"the PREPARED branch's orch.execute(); last "
        f"lease check is at offset {last_lease}, "
        f"PREPARED branch executor is at offset {prep_orch}"
    )
    # The lease check must be in the PREPARED state
    # branch (only when actually executing the mutation).
    prep_pos = src.find('current_state is LifecycleState.PREPARED')
    assert prep_pos > 0, (
        "_mutate_ref must have a PREPARED branch"
    )
    assert last_lease > prep_pos, (
        f"is_lease_held_by_run must be in the PREPARED "
        f"branch; last lease check is at offset {last_lease}, "
        f"PREPARED branch is at offset {prep_pos}"
    )