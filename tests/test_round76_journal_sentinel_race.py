#!/usr/bin/env python3
"""
Round-76 regression test for Repair E.

Repair E: Hold the journal sentinel through lease
release. Without this, an authorize-mutation that starts
AFTER the outstanding-mutations check but BEFORE the
lease release can append a new journal record while the
run is being finalized; finalize would persist
RUN_COMPLETE without re-checking the journal, leaving
an outstanding executable mutation on a completed run.

The Round-76 fix extends the journal sentinel's lifetime
in finalize-run: it is acquired at the top of the
workspace branch and held through the
outstanding-mutations check, the lease release, and the
terminal state save. It is released at the END of the
function (after the "Run finalized" print).

Structural verification test (the race requires
concurrent threads and timing):
  - Verify the controller's _finalize_run function
    acquires the journal sentinel at the start (in the
    workspace.is_dir() branch) and releases it at the
    END of the function (after the lease release and
    the terminal state save).
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.local import autocoder_run_controller as ctrl_mod


def test_e_journal_sentinel_released_at_end_of_finalize():
    """E.1: the controller's _finalize_run function
    releases the journal sentinel at the END of the
    function (after the lease release and the terminal
    state save). The release is the LAST sentinel
    operation in the function.
    """
    src = inspect.getsource(ctrl_mod._finalize_run)
    # The function must end with a sentinel release
    # (not a finally: block that releases — the release
    # is now at the function's end after the "Run
    # finalized" print). Find the LAST call site of the
    # release, not the import statement.
    release_call = src.rfind("_release_journal_sentinel(")
    last_print = src.rfind('"Run finalized"')
    # The release must come AFTER the "Run finalized"
    # print, indicating the sentinel is held through the
    # entire function body.
    assert release_call > last_print, (
        f"the journal sentinel release must be at the "
        f"END of _finalize_run (after the 'Run finalized' "
        f"print); the release is at offset {release_call} "
        f"and the print is at offset {last_print}"
    )


def test_e_journal_sentinel_held_through_lease_release():
    """E.2: the journal sentinel release comes AFTER
    the supervisor lock release loop in _finalize_run.
    Without this, the lease can be released while a
    concurrent authorize-mutation is appending to the
    journal (the sentinel is held by authorize-mutation
    but the lease is gone).
    """
    src = inspect.getsource(ctrl_mod._finalize_run)
    # The supervisor release() loop ends with the
    # `if not released: sys.exit(13)` block. After that
    # block, the function persists RUN_COMPLETE and
    # prints "Run finalized". The sentinel release must
    # come AFTER the release loop and the RUN_COMPLETE
    # save.
    release_loop_end = src.find("if not released:")
    sentinel_release = src.rfind("_release_journal_sentinel(")
    run_complete_save = src.find("RUN_COMPLETE", release_loop_end)
    assert sentinel_release > release_loop_end, (
        f"sentinel release ({sentinel_release}) must come "
        f"after the lease release loop ({release_loop_end})"
    )
    assert sentinel_release > run_complete_save, (
        f"sentinel release ({sentinel_release}) must come "
        f"after the RUN_COMPLETE save ({run_complete_save})"
    )