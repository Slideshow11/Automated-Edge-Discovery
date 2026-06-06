#!/usr/bin/env python3
"""
Tests for the phase execution ledger validator.

Covers the validator's HOLD-state matrix:
- valid → 0
- HOLD_UNEVIDENCED_PASS — missing ledger, empty ledger, claim without
  canonical-writer evidence, claim with only writer=agent
- HOLD_PHASE_EVIDENCE_CORRUPTED — missing stdout/stderr artifact files,
  missing paths for canonical writer
- HOLD_PHASE_RESULT_INCONSISTENT — exit_code != 0 with status PASS,
  empty observed_summary with status PASS
- duplicate canonical PASS for same phase_id → warning, not hard error
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts" / "local"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from phase_ledger import build_entry, append_entry  # noqa: E402
from validate_phase_ledger import (  # noqa: E402
    validate,
    HOLD_VALID,
    HOLD_UNEVIDENCED_PASS,
    HOLD_PHASE_EVIDENCE_CORRUPTED,
    HOLD_PHASE_RESULT_INCONSISTENT,
    EXIT_VALID,
    EXIT_UNEVIDENCED,
    EXIT_EVIDENCE_CORRUPTED,
    EXIT_RESULT_INCONSISTENT,
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _write_artifact_pair(tmp_path, run_id, phase_id, observed="ok"):
    """Write a real stdout/stderr artifact pair and return (stdout, stderr)."""
    out = tmp_path / f"{run_id}_{phase_id}_stdout.txt"
    err = tmp_path / f"{run_id}_{phase_id}_stderr.txt"
    out.write_text(observed + "\n")
    err.write_text("")
    return out, err


def _canonical_pass_line(tmp_path, run_id="r1", phase_id="PHASE_1", phase_index=1,
                        observed="ok", exit_code=0, status="PASS"):
    """Build a canonical (script-writer) PASS line whose artifacts exist on disk."""
    out, err = _write_artifact_pair(tmp_path, run_id, phase_id, observed=observed)
    return build_entry(
        run_id=run_id,
        phase_id=phase_id,
        phase_index=phase_index,
        writer="script",
        script="scripts/local/some_script.py",
        argv=["--do", "thing"],
        exit_code=exit_code,
        stdout_path=str(out),
        stderr_path=str(err),
        observed_summary=observed,
        status=status,
        timestamp="2026-06-06T00:00:00Z",
    )


# -----------------------------------------------------------------------------
# 1. Valid ledger with all canonical evidence returns 0 / valid
# -----------------------------------------------------------------------------


def test_valid_ledger_returns_zero(tmp_path):
    """All canonical evidence for claimed phases → valid."""
    ledger = tmp_path / "phase_ledger.jsonl"
    for i, pid in enumerate(["PHASE_1", "PHASE_2", "PHASE_3"], start=1):
        append_entry(_canonical_pass_line(tmp_path, phase_id=pid, phase_index=i), ledger)

    result = validate(ledger, claimed_phases=["PHASE_1", "PHASE_2", "PHASE_3"])
    assert result["valid"] is True
    assert result["hold_state"] == HOLD_VALID
    assert result["errors"] == []


def test_valid_ledger_with_no_claim_only_checks_consistency(tmp_path):
    """Without claimed_phases, validator still checks internal consistency of every line."""
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(_canonical_pass_line(tmp_path), ledger)
    result = validate(ledger, claimed_phases=None)
    assert result["valid"] is True


# -----------------------------------------------------------------------------
# 2. HOLD_UNEVIDENCED_PASS — missing or empty ledger
# -----------------------------------------------------------------------------


def test_missing_ledger_with_claim_returns_unevidenced(tmp_path):
    """Ledger file does not exist + claimed phases → HOLD_UNEVIDENCED_PASS."""
    ledger = tmp_path / "does_not_exist.jsonl"
    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["valid"] is False
    assert result["hold_state"] == HOLD_UNEVIDENCED_PASS


def test_empty_ledger_with_claim_returns_unevidenced(tmp_path):
    """Empty ledger + claimed phases → HOLD_UNEVIDENCED_PASS."""
    ledger = tmp_path / "empty.jsonl"
    ledger.write_text("")
    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["valid"] is False
    assert result["hold_state"] == HOLD_UNEVIDENCED_PASS


def test_ledger_with_lines_but_no_claim_match_returns_unevidenced(tmp_path):
    """Ledger has lines but none match the claimed phase_id → HOLD_UNEVIDENCED_PASS."""
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(_canonical_pass_line(tmp_path, phase_id="PHASE_OTHER"), ledger)
    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["valid"] is False
    assert result["hold_state"] == HOLD_UNEVIDENCED_PASS


# -----------------------------------------------------------------------------
# 3. HOLD_UNEVIDENCED_PASS — claimed phase with only writer=agent
# -----------------------------------------------------------------------------


def test_claimed_phase_with_only_agent_writer_returns_unevidenced(tmp_path):
    """writer=agent does NOT satisfy proof for claimed PASS phase → HOLD_UNEVIDENCED_PASS."""
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(
        build_entry(
            run_id="r1",
            phase_id="PHASE_1",
            writer="agent",
            argv=None,
            exit_code=0,
            stdout_path=None,
            stderr_path=None,
            observed_summary="agent narrative",
            status="PASS",
            timestamp="2026-06-06T00:00:00Z",
        ),
        ledger,
    )
    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["valid"] is False
    assert result["hold_state"] == HOLD_UNEVIDENCED_PASS
    # Error message should mention writer=agent
    msg = " ".join(e["detail"] for e in result["errors"])
    assert "agent" in msg.lower()


# -----------------------------------------------------------------------------
# 4. Canonical writer=phase_exec PASS satisfies proof
# -----------------------------------------------------------------------------


def test_claimed_phase_with_phase_exec_writer_returns_valid(tmp_path):
    """writer=phase_exec with artifacts → valid."""
    ledger = tmp_path / "phase_ledger.jsonl"
    out, err = _write_artifact_pair(tmp_path, "r1", "PHASE_1")
    append_entry(
        build_entry(
            run_id="r1",
            phase_id="PHASE_1",
            writer="phase_exec",
            argv=["gh", "pr", "checks", "389"],
            exit_code=0,
            stdout_path=str(out),
            stderr_path=str(err),
            observed_summary="ok",
            status="PASS",
            timestamp="2026-06-06T00:00:00Z",
        ),
        ledger,
    )
    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["valid"] is True


# -----------------------------------------------------------------------------
# 5. HOLD_PHASE_EVIDENCE_CORRUPTED — missing artifact files
# -----------------------------------------------------------------------------


def test_missing_stdout_file_returns_evidence_corrupted(tmp_path):
    """Ledger line points to a stdout path that does not exist on disk → HOLD_PHASE_EVIDENCE_CORRUPTED."""
    ledger = tmp_path / "phase_ledger.jsonl"
    fake_out = tmp_path / "non_existent_stdout.txt"
    fake_err = tmp_path / "non_existent_stderr.txt"
    append_entry(
        build_entry(
            run_id="r1",
            phase_id="PHASE_1",
            writer="script",
            argv=["true"],
            exit_code=0,
            stdout_path=str(fake_out),
            stderr_path=str(fake_err),
            observed_summary="ok",
            status="PASS",
            timestamp="2026-06-06T00:00:00Z",
        ),
        ledger,
    )
    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["valid"] is False
    assert result["hold_state"] == HOLD_PHASE_EVIDENCE_CORRUPTED


def test_missing_stdout_path_field_returns_evidence_corrupted(tmp_path):
    """Hand-crafted ledger with stdout_path=None for script writer → HOLD_PHASE_EVIDENCE_CORRUPTED.

    This simulates a hand-edited or hand-crafted ledger file that bypasses
    build_entry's strictness. The validator must defend against it.
    """
    ledger = tmp_path / "phase_ledger.jsonl"
    # Construct entry dict manually to bypass build_entry's strictness
    hand_crafted = {
        "audit_log_version": 1,
        "ledger_kind": "phase_execution_v1",
        "run_id": "r1",
        "phase_id": "PHASE_1",
        "writer": "script",
        "argv": ["true"],
        "exit_code": 0,
        "stdout_path": None,  # missing — bypassed build_entry
        "stderr_path": str(tmp_path / "e.txt"),
        "observed_summary": "ok",
        "status": "PASS",
        "timestamp": "2026-06-06T00:00:00Z",
    }
    ledger.write_text(json.dumps(hand_crafted) + "\n")
    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["valid"] is False
    assert result["hold_state"] == HOLD_PHASE_EVIDENCE_CORRUPTED


# -----------------------------------------------------------------------------
# 6. HOLD_PHASE_RESULT_INCONSISTENT — exit_code != 0 with status PASS
# -----------------------------------------------------------------------------


def test_nonzero_exit_with_status_pass_returns_inconsistent(tmp_path):
    """exit_code != 0 with status PASS is inconsistent → HOLD_PHASE_RESULT_INCONSISTENT."""
    ledger = tmp_path / "phase_ledger.jsonl"
    out, err = _write_artifact_pair(tmp_path, "r1", "PHASE_1")
    append_entry(
        build_entry(
            run_id="r1",
            phase_id="PHASE_1",
            writer="script",
            argv=["false"],
            exit_code=1,
            stdout_path=str(out),
            stderr_path=str(err),
            observed_summary="ok",
            status="PASS",  # inconsistent with exit_code=1
            timestamp="2026-06-06T00:00:00Z",
        ),
        ledger,
    )
    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["valid"] is False
    assert result["hold_state"] == HOLD_PHASE_RESULT_INCONSISTENT


# -----------------------------------------------------------------------------
# 7. HOLD_PHASE_RESULT_INCONSISTENT — empty observed_summary with status PASS
# -----------------------------------------------------------------------------


def test_empty_observed_summary_with_status_pass_returns_inconsistent(tmp_path):
    """status=PASS with empty observed_summary → HOLD_PHASE_RESULT_INCONSISTENT."""
    ledger = tmp_path / "phase_ledger.jsonl"
    out, err = _write_artifact_pair(tmp_path, "r1", "PHASE_1")
    append_entry(
        build_entry(
            run_id="r1",
            phase_id="PHASE_1",
            writer="script",
            argv=["true"],
            exit_code=0,
            stdout_path=str(out),
            stderr_path=str(err),
            observed_summary="",  # empty
            status="PASS",
            timestamp="2026-06-06T00:00:00Z",
        ),
        ledger,
    )
    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["valid"] is False
    assert result["hold_state"] == HOLD_PHASE_RESULT_INCONSISTENT


# -----------------------------------------------------------------------------
# 8. Duplicate canonical PASS for same phase_id is a warning, not a hard error
# -----------------------------------------------------------------------------


def test_duplicate_phase_id_is_warning_not_error(tmp_path):
    """Two canonical PASS lines for the same phase_id → valid, but warning recorded."""
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(_canonical_pass_line(tmp_path, phase_id="PHASE_1"), ledger)
    append_entry(_canonical_pass_line(tmp_path, phase_id="PHASE_1"), ledger)
    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["valid"] is True
    assert any("duplicate" in w.lower() for w in result["warnings"])


# -----------------------------------------------------------------------------
# 9. Exit-code mapping
# -----------------------------------------------------------------------------


def test_exit_code_mapping(tmp_path):
    """validate() returns the documented exit code on each hold state."""
    # Valid
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(_canonical_pass_line(tmp_path), ledger)
    assert validate(ledger, claimed_phases=["PHASE_1"])["exit_code"] == EXIT_VALID

    # Unevidenced
    ledger.write_text("")
    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["exit_code"] == EXIT_UNEVIDENCED

    # Evidence corrupted
    fake_out = tmp_path / "x.txt"
    fake_err = tmp_path / "y.txt"
    ledger.write_text("")
    append_entry(
        build_entry(
            run_id="r1", phase_id="PHASE_1", writer="script", argv=["true"],
            exit_code=0, stdout_path=str(fake_out), stderr_path=str(fake_err),
            observed_summary="ok", status="PASS",
            timestamp="2026-06-06T00:00:00Z",
        ),
        ledger,
    )
    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["exit_code"] == EXIT_EVIDENCE_CORRUPTED


# -----------------------------------------------------------------------------
# 10. CLI integration
# -----------------------------------------------------------------------------


def test_cli_returns_zero_for_valid_ledger(tmp_path):
    """The validate CLI exits 0 on a valid ledger."""
    import subprocess

    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(_canonical_pass_line(tmp_path), ledger)

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "validate_phase_ledger.py"),
            "--ledger", str(ledger),
            "--claimed-phases", "PHASE_1",
        ],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"


def test_cli_returns_unevidenced_exit_code_for_missing_ledger(tmp_path):
    """The validate CLI exits EXIT_UNEVIDENCED for missing ledger with claim."""
    import subprocess

    ledger = tmp_path / "does_not_exist.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "validate_phase_ledger.py"),
            "--ledger", str(ledger),
            "--claimed-phases", "PHASE_1",
        ],
        capture_output=True, text=True,
    )
    assert proc.returncode == EXIT_UNEVIDENCED
