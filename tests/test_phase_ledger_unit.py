"""Direct unit tests for the bare phase_ledger.py module.

These tests restore coverage that lived in the deleted
``tests/test_phase_ledger.py`` (removed in PR #411 along with the
finalization wrappers). The bare ``phase_ledger.py`` module is NOT
deleted — it remains actively used by ``phase_exec.py`` and
``validate_phase_ledger.py``. Integration coverage lives in
``tests/test_validate_phase_ledger.py``; this module adds direct
writer/reader coverage for the bare module.

The brief calls for migrating "meaningful assertions into surviving
test modules". These tests cover the load-bearing writer behavior
that downstream consumers depend on:

  1. Canonical PASS line append (script writer)
  2. FAIL line with nonzero exit_code
  3. Reject missing required fields (run_id, status, etc.)
  4. Reject invalid status value
  5. Script/phase_exec writer requires argv
  6. Script/phase_exec writer requires absolute stdout/stderr paths
  7. Reader skips malformed lines
  8. find_entry returns the matching (run_id, phase_id)
  9. Duplicate (run_id, phase_id) PASS appends two lines (no dedupe)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_LOCAL = REPO / "scripts" / "local"
if str(SCRIPTS_LOCAL) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_LOCAL))

from phase_ledger import (  # noqa: E402
    AUDIT_LOG_VERSION,
    LEDGER_KIND,
    VALID_STATUSES,
    build_entry,
    append_entry,
    read_entries,
    find_entry,
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _abs(tmp_path: Path, name: str) -> str:
    """Return an absolute file path under tmp_path (the bare module
    requires absolute stdout/stderr paths for canonical writers)."""
    return str((tmp_path / name).resolve())


def _pass_entry(tmp_path: Path, run_id="run-001",
                phase_id="PHASE_2_CONFIRM_CI",
                phase_index=2, **overrides):
    base = dict(
        run_id=run_id,
        phase_id=phase_id,
        phase_index=phase_index,
        writer="script",
        script="scripts/local/check_pr_state.py",
        argv=["--pr-number", "389"],
        exit_code=0,
        stdout_path=_abs(tmp_path, "phase_stdout.txt"),
        stderr_path=_abs(tmp_path, "phase_stderr.txt"),
        observed_summary="5/5 CI checks passed",
        status="PASS",
        timestamp="2026-06-06T17:46:32Z",
    )
    base.update(overrides)
    return build_entry(**base)


# -----------------------------------------------------------------------------
# 1. PASS line append
# -----------------------------------------------------------------------------

def test_append_pass_phase_writes_valid_jsonl(tmp_path: Path) -> None:
    ledger = tmp_path / "phase_ledger.jsonl"
    entry = _pass_entry(tmp_path)
    append_entry(entry, ledger)
    assert ledger.exists()
    lines = ledger.read_text().strip().split("\n")
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["audit_log_version"] == AUDIT_LOG_VERSION
    assert obj["ledger_kind"] == LEDGER_KIND
    assert obj["run_id"] == "run-001"
    assert obj["phase_id"] == "PHASE_2_CONFIRM_CI"
    assert obj["writer"] == "script"
    assert obj["status"] == "PASS"
    assert obj["exit_code"] == 0


# -----------------------------------------------------------------------------
# 2. FAIL line with nonzero exit_code
# -----------------------------------------------------------------------------

def test_append_fail_phase_records_nonzero_exit(tmp_path: Path) -> None:
    ledger = tmp_path / "phase_ledger.jsonl"
    entry = _pass_entry(tmp_path, status="FAIL", exit_code=2)
    append_entry(entry, ledger)
    obj = json.loads(ledger.read_text().strip())
    assert obj["status"] == "FAIL"
    assert obj["exit_code"] == 2


# -----------------------------------------------------------------------------
# 3. Reject missing required fields
# -----------------------------------------------------------------------------

def test_build_entry_rejects_missing_run_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_entry(
            run_id=None,
            phase_id="PHASE_2",
            phase_index=2,
            writer="script",
            argv=["x"],
            exit_code=0,
            stdout_path=_abs(tmp_path, "o.txt"),
            stderr_path=_abs(tmp_path, "e.txt"),
            observed_summary="",
            status="PASS",
        )


# -----------------------------------------------------------------------------
# 4. Reject invalid status value
# -----------------------------------------------------------------------------

def test_build_entry_rejects_invalid_status(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_entry(
            run_id="run-001",
            phase_id="PHASE_2",
            phase_index=2,
            writer="script",
            argv=["x"],
            exit_code=0,
            stdout_path=_abs(tmp_path, "o.txt"),
            stderr_path=_abs(tmp_path, "e.txt"),
            observed_summary="",
            status="NOT_A_VALID_STATUS",
        )


def test_valid_statuses_includes_pass_fail_skip(tmp_path: Path) -> None:
    # The canonical status vocabulary must include at minimum PASS,
    # FAIL, and SKIP — these are the values the controller emits.
    assert "PASS" in VALID_STATUSES
    assert "FAIL" in VALID_STATUSES
    assert "SKIP" in VALID_STATUSES


# -----------------------------------------------------------------------------
# 5. Canonical writer requires argv
# -----------------------------------------------------------------------------

def test_build_entry_rejects_canonical_writer_without_argv(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError):
        build_entry(
            run_id="run-001",
            phase_id="PHASE_2",
            phase_index=2,
            writer="script",
            argv=None,
            exit_code=0,
            stdout_path=_abs(tmp_path, "o.txt"),
            stderr_path=_abs(tmp_path, "e.txt"),
            observed_summary="",
            status="PASS",
        )


# -----------------------------------------------------------------------------
# 6. Canonical writer requires absolute stdout/stderr paths
# -----------------------------------------------------------------------------

def test_build_entry_rejects_relative_stdout_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_entry(
            run_id="run-001",
            phase_id="PHASE_2",
            phase_index=2,
            writer="script",
            argv=["x"],
            exit_code=0,
            stdout_path="relative/out.txt",
            stderr_path=_abs(tmp_path, "e.txt"),
            observed_summary="",
            status="PASS",
        )


def test_build_entry_rejects_relative_stderr_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_entry(
            run_id="run-001",
            phase_id="PHASE_2",
            phase_index=2,
            writer="script",
            argv=["x"],
            exit_code=0,
            stdout_path=_abs(tmp_path, "o.txt"),
            stderr_path="relative/err.txt",
            observed_summary="",
            status="PASS",
        )


# -----------------------------------------------------------------------------
# 7. Reader skips malformed lines
# -----------------------------------------------------------------------------

def test_read_entries_skips_malformed_lines(tmp_path: Path) -> None:
    ledger = tmp_path / "phase_ledger.jsonl"
    # Write one valid line followed by a malformed one.
    valid = _pass_entry(tmp_path)
    append_entry(valid, ledger)
    with ledger.open("a") as f:
        f.write("{not valid json\n")
    # Add another valid line.
    append_entry(_pass_entry(tmp_path, run_id="run-002"), ledger)
    entries = read_entries(ledger)
    run_ids = {e["run_id"] for e in entries}
    assert run_ids == {"run-001", "run-002"}


# -----------------------------------------------------------------------------
# 8. find_entry returns matching (run_id, phase_id)
# -----------------------------------------------------------------------------

def test_find_entry_returns_matching_run_phase(tmp_path: Path) -> None:
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(_pass_entry(tmp_path, run_id="r1", phase_id="p1"), ledger)
    append_entry(_pass_entry(tmp_path, run_id="r1", phase_id="p2"), ledger)
    append_entry(_pass_entry(tmp_path, run_id="r2", phase_id="p1"), ledger)
    entries = read_entries(ledger)
    hit = find_entry(entries, "r1", "p2")
    assert hit is not None
    assert hit["run_id"] == "r1"
    assert hit["phase_id"] == "p2"
    miss = find_entry(entries, "r9", "p9")
    assert miss is None


# -----------------------------------------------------------------------------
# 9. Duplicate (run_id, phase_id) appends two lines (no silent dedupe)
# -----------------------------------------------------------------------------

def test_duplicate_run_phase_appends_two_lines(tmp_path: Path) -> None:
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(_pass_entry(tmp_path, run_id="r1", phase_id="p1"), ledger)
    append_entry(_pass_entry(tmp_path, run_id="r1", phase_id="p1"), ledger)
    lines = ledger.read_text().strip().split("\n")
    assert len(lines) == 2
    obj1, obj2 = (json.loads(l) for l in lines)
    assert obj1["run_id"] == obj2["run_id"] == "r1"
    assert obj1["phase_id"] == obj2["phase_id"] == "p1"
    # No silent dedupe: both entries are present.
