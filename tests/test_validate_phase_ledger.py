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


# -----------------------------------------------------------------------------
# 11. Malformed non-empty JSONL line surfaces as HOLD_PHASE_EVIDENCE_CORRUPTED
#     (Codex P2 finding on PR #390)
# -----------------------------------------------------------------------------


def test_malformed_nonempty_jsonl_line_returns_evidence_corrupted(tmp_path):
    """A non-empty malformed JSONL line forces HOLD_PHASE_EVIDENCE_CORRUPTED.

    Regression guard: previously the validator's strict reader silently
    skipped malformed non-empty lines, so a ledger with a valid line plus
    a corrupted/tampered extra line could validate as HOLD_VALID. The
    fix: surface the parse error in errors[] with kind=EVIDENCE_CORRUPTED,
    which the precedence ladder maps to HOLD_PHASE_EVIDENCE_CORRUPTED.
    """
    ledger = tmp_path / "phase_ledger.jsonl"
    # Valid canonical line on line 1
    append_entry(_canonical_pass_line(tmp_path, phase_id="PHASE_1"), ledger)
    # Malformed non-empty line on line 2
    with ledger.open("a") as f:
        f.write("{this is not valid json\n")

    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["valid"] is False
    assert result["hold_state"] == HOLD_PHASE_EVIDENCE_CORRUPTED
    assert result["hold_state"] != HOLD_VALID
    assert result["malformed_count"] == 1
    # Error detail must include the line number and the error reason
    malformed_errors = [
        e for e in result["errors"]
        if e["kind"] == "EVIDENCE_CORRUPTED" and "malformed JSONL" in e["detail"]
    ]
    assert len(malformed_errors) == 1
    assert malformed_errors[0]["line"] == 2
    assert "line 2" in malformed_errors[0]["detail"]


def test_valid_claimed_pass_plus_malformed_extra_line_returns_evidence_corrupted(tmp_path):
    """A valid claimed PASS plus a malformed extra line must NOT return HOLD_VALID.

    This is the primary regression guard for the Codex P2 finding: the
    silent-skip loophole where the validator returned HOLD_VALID despite
    corrupted ledger content is now closed. The malformed line forces
    HOLD_PHASE_EVIDENCE_CORRUPTED (precedence: EVIDENCE_CORRUPTED >
    RESULT_INCONSISTENT > UNEVIDENCED_PASS > VALID).
    """
    ledger = tmp_path / "phase_ledger.jsonl"
    # A valid canonical PASS line on line 1 — alone, this would be HOLD_VALID
    append_entry(_canonical_pass_line(tmp_path, phase_id="PHASE_1"), ledger)
    # Trailing truncated/corrupted JSON on line 2 (e.g. partial write or
    # tampered extra line)
    with ledger.open("a") as f:
        f.write('{"run_id":"r1","phase_id":"PHASE_2","writer":"script"')

    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["valid"] is False
    assert result["hold_state"] == HOLD_PHASE_EVIDENCE_CORRUPTED
    assert result["hold_state"] != HOLD_VALID
    assert result["malformed_count"] >= 1


def test_blank_lines_are_silently_ignored_not_treated_as_malformed(tmp_path):
    """Blank/whitespace-only lines are not treated as malformed.

    This locks in the convention: only non-empty malformed lines surface
    as EVIDENCE_CORRUPTED. Blank lines (e.g. trailing newlines from
    pretty-printers) must remain a no-op.
    """
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(_canonical_pass_line(tmp_path, phase_id="PHASE_1"), ledger)
    with ledger.open("a") as f:
        f.write("\n   \n\t\n")  # blank and whitespace-only lines

    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["valid"] is True
    assert result["hold_state"] == HOLD_VALID
    assert result["malformed_count"] == 0


def test_malformed_line_preserves_valid_evidence_count(tmp_path):
    """A malformed line does not reduce the count of valid entries.

    The line_count field represents valid entries parsed; malformed_count
    is tracked separately. The validator's reported line_count must
    reflect the valid evidence available for claim matching.
    """
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(_canonical_pass_line(tmp_path, phase_id="PHASE_1"), ledger)
    append_entry(_canonical_pass_line(tmp_path, phase_id="PHASE_2"), ledger)
    with ledger.open("a") as f:
        f.write("garbage line 3\n")
    append_entry(_canonical_pass_line(tmp_path, phase_id="PHASE_3"), ledger)

    result = validate(ledger, claimed_phases=["PHASE_1", "PHASE_2", "PHASE_3"])
    assert result["line_count"] == 3
    assert result["malformed_count"] == 1
    assert result["hold_state"] == HOLD_PHASE_EVIDENCE_CORRUPTED


# -----------------------------------------------------------------------------
# 12. Required-field / schema validation for canonical evidence
#     (Codex P1 finding on PR #390)
# -----------------------------------------------------------------------------


def test_required_fields_missing_returns_evidence_corrupted(tmp_path):
    """A canonical-looking entry missing required v1 fields fails validation.

    Regression guard for the Codex P1 finding: previously the validator
    relied only on is_canonical_evidence() (which checks writer/status/
    argv/paths/observed_summary) but never enforced the v1 schema
    (audit_log_version, ledger_kind, run_id, phase_id, writer, exit_code,
    status, timestamp). A hand-written entry that looked like evidence
    but lacked the version/kind/run_id/timestamp fields could pass
    is_canonical_evidence() and validate as HOLD_VALID. The fix: the
    validator now calls validate_entry_shape() per parsed entry and
    surfaces missing/invalid required fields as EVIDENCE_CORRUPTED.
    """
    ledger = tmp_path / "phase_ledger.jsonl"
    # Hand-write a line that is "canonical-looking" (writer=script,
    # status=PASS, exit_code=0, argv, absolute stdout/stderr paths,
    # observed_summary) but is MISSING the v1 schema-required fields:
    # audit_log_version, ledger_kind, run_id, timestamp.
    out, err = _write_artifact_pair(tmp_path, "r1", "PHASE_1")
    bare_entry = {
        # REQUIRED v1 fields deliberately omitted:
        # "audit_log_version": 1,
        # "ledger_kind": "phase_execution_v1",
        # "run_id": "r1",
        # "timestamp": "2026-06-06T00:00:00Z",
        "phase_id": "PHASE_1",
        "writer": "script",
        "argv": ["--do", "thing"],
        "exit_code": 0,
        "stdout_path": str(out),
        "stderr_path": str(err),
        "observed_summary": "looks like evidence",
        "status": "PASS",
    }
    ledger.write_text(json.dumps(bare_entry) + "\n")

    result = validate(ledger, claimed_phases=["PHASE_1"])

    assert result["valid"] is False
    assert result["hold_state"] == HOLD_PHASE_EVIDENCE_CORRUPTED
    assert result["hold_state"] != HOLD_VALID
    # At least one EVIDENCE_CORRUPTED error must mention a missing
    # required field
    schema_failures = [
        e for e in result["errors"]
        if e["kind"] == "EVIDENCE_CORRUPTED"
        and "required-field/schema failure" in e["detail"]
    ]
    assert len(schema_failures) >= 1
    # The detail should mention at least one of the missing field names
    detail_blob = " ".join(e["detail"] for e in schema_failures)
    assert any(
        field in detail_blob
        for field in ("audit_log_version", "ledger_kind", "run_id", "timestamp")
    )


def test_canonical_entry_with_all_required_fields_passes_schema_check(tmp_path):
    """A canonical entry with all v1 required fields passes the schema check.

    Locks in that the new schema check does not regress the happy path:
    a properly built entry (writer=script, status=PASS, full v1 fields,
    absolute paths, non-empty summary) must still validate as HOLD_VALID.
    """
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(_canonical_pass_line(tmp_path, phase_id="PHASE_1"), ledger)
    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["valid"] is True
    assert result["hold_state"] == HOLD_VALID
    # No schema failures recorded for a well-formed entry
    schema_failures = [
        e for e in result["errors"]
        if "required-field/schema failure" in e["detail"]
    ]
    assert schema_failures == []


# -----------------------------------------------------------------------------
# 13. Trailing data after a JSONL object is rejected as corruption
#     (Codex P2 finding on PR #390)
# -----------------------------------------------------------------------------


def test_trailing_data_after_jsonl_object_returns_evidence_corrupted(tmp_path):
    """A JSONL line with trailing garbage after a valid object is corruption.

    Regression guard for the Codex P2 finding: json.JSONDecoder.raw_decode
    returns successfully if the leading substring is a complete JSON object,
    even when there is non-whitespace garbage after it. Previously the
    strict reader ignored `_end` and would silently accept the trailing
    garbage; the validator then returned HOLD_VALID with malformed_count=0.
    The fix: the strict reader now checks stripped[end:].strip() and
    records a parse error if any non-whitespace data follows the object.
    """
    ledger = tmp_path / "phase_ledger.jsonl"
    # One line: a valid canonical JSONL object followed by trailing
    # garbage on the same physical line. The raw_decode call succeeds
    # for the leading substring, so the strict reader must explicitly
    # detect the trailing "GARBAGE" and treat it as corruption.
    ledger.write_text(
        '{"audit_log_version": 1, "ledger_kind": "phase_execution_v1", '
        '"run_id": "r1", "phase_id": "PHASE_1", "writer": "script", '
        '"exit_code": 0, "status": "PASS", "timestamp": "2026-06-06T00:00:00Z"} '
        'GARBAGE\n'
    )

    result = validate(ledger, claimed_phases=["PHASE_1"])

    assert result["valid"] is False
    assert result["hold_state"] == HOLD_PHASE_EVIDENCE_CORRUPTED
    assert result["hold_state"] != HOLD_VALID
    assert result["malformed_count"] == 1
    # Error detail must reference trailing data
    trailing_errors = [
        e for e in result["errors"]
        if e["kind"] == "EVIDENCE_CORRUPTED"
        and "malformed JSONL" in e["detail"]
        and "trailing data" in e["detail"]
    ]
    assert len(trailing_errors) == 1
    assert trailing_errors[0]["line"] == 1


def test_valid_claimed_pass_plus_trailing_garbage_extra_line_returns_evidence_corrupted(tmp_path):
    """A valid claimed PASS plus a trailing-garbage extra line must NOT return HOLD_VALID.

    This is the canonical-corruption scenario: a single well-formed PASS
    line is enough to satisfy a claim, but a second physical line with a
    valid object followed by trailing garbage must force
    HOLD_PHASE_EVIDENCE_CORRUPTED (not HOLD_VALID). Without this, a
    tampered/partial-write ledger could pass validation.
    """
    ledger = tmp_path / "phase_ledger.jsonl"
    # Valid canonical PASS line on line 1 — alone, this would be HOLD_VALID
    append_entry(_canonical_pass_line(tmp_path, phase_id="PHASE_1"), ledger)
    # Line 2: a valid JSON object followed by trailing garbage
    with ledger.open("a") as f:
        f.write(
            '{"audit_log_version": 1, "ledger_kind": "phase_execution_v1", '
            '"run_id": "r1", "phase_id": "PHASE_2", "writer": "script", '
            '"exit_code": 0, "status": "PASS", "timestamp": "2026-06-06T00:00:01Z"} '
            'tail-of-line-2\n'
        )

    result = validate(ledger, claimed_phases=["PHASE_1"])

    assert result["valid"] is False
    assert result["hold_state"] == HOLD_PHASE_EVIDENCE_CORRUPTED
    assert result["hold_state"] != HOLD_VALID
    assert result["malformed_count"] == 1


def test_trailing_whitespace_only_is_accepted(tmp_path):
    """Whitespace-only trailing data after a JSON object is NOT corruption.

    This locks in the trailing-data fix's convention: only non-whitespace
    trailing data forces EVIDENCE_CORRUPTED. Pure trailing whitespace
    (spaces, tabs) is benign and must not be treated as evidence
    corruption — that matches the JSON spec and avoids false positives
    on hand-formatted ledgers.
    """
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(_canonical_pass_line(tmp_path, phase_id="PHASE_1"), ledger)
    # Trailing whitespace-only on the canonical line is fine: append
    # a wholly-whitespace line to be sure.
    with ledger.open("a") as f:
        f.write("   \t   \n")

    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["valid"] is True
    assert result["hold_state"] == HOLD_VALID
    assert result["malformed_count"] == 0


# -----------------------------------------------------------------------------
# 14. Claimed evidence is bound to expected_run_id (P1 fix from PR #390 round 4)
# -----------------------------------------------------------------------------


def test_claimed_phase_requires_matching_run_id_when_expected_run_id_set(tmp_path):
    """When expected_run_id is set, stale evidence from another run is rejected.

    Regression guard for the Codex P1 finding: previously the validator
    selected claim candidates only by phase_id, so a canonical PASS from
    run_old could satisfy the same phase claim for run_new. The fix:
    when expected_run_id is set, candidates are filtered by
    entry.run_id == expected_run_id; non-matching run_ids are recorded
    as a warning and do NOT satisfy the claim.
    """
    ledger = tmp_path / "phase_ledger.jsonl"
    # Build a canonical PASS line for run_old only
    entry = _canonical_pass_line(tmp_path, run_id="run_old", phase_id="PHASE_1")
    append_entry(entry, ledger)

    # Claim PHASE_1 for run_new — the run_old evidence must NOT satisfy
    result = validate(
        ledger,
        claimed_phases=["PHASE_1"],
        expected_run_id="run_new",
    )

    assert result["valid"] is False
    assert result["hold_state"] != HOLD_VALID
    # Per spec: either HOLD_UNEVIDENCED_PASS or HOLD_PHASE_EVIDENCE_CORRUPTED
    assert result["hold_state"] in (HOLD_UNEVIDENCED_PASS, HOLD_PHASE_EVIDENCE_CORRUPTED)
    # Error detail must mention stale/nonmatching run_id
    unclaim_errors = [
        e for e in result["errors"]
        if e["kind"] == "UNCLAIMED_PHASE"
    ]
    assert len(unclaim_errors) >= 1
    assert "run_new" in unclaim_errors[0]["detail"]
    assert "run_old" in unclaim_errors[0]["detail"]
    # A warning should mention the stale run_id
    stale_warnings = [w for w in result["warnings"] if "stale" in w and "run_old" in w]
    assert len(stale_warnings) >= 1


def test_claimed_phase_valid_when_run_id_matches_expected_run_id(tmp_path):
    """When expected_run_id matches, the canonical PASS line is accepted.

    Locks in the happy path: a canonical PASS line for PHASE_1 with
    run_id=run_new validates as HOLD_VALID when expected_run_id=run_new
    is set.
    """
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(
        _canonical_pass_line(tmp_path, run_id="run_new", phase_id="PHASE_1"),
        ledger,
    )
    result = validate(
        ledger,
        claimed_phases=["PHASE_1"],
        expected_run_id="run_new",
    )
    assert result["valid"] is True
    assert result["hold_state"] == HOLD_VALID
    assert result["errors"] == []


def test_no_expected_run_id_preserves_phase_only_matching(tmp_path):
    """When expected_run_id is omitted, behavior is unchanged.

    Backward-compat: a canonical PASS for PHASE_1 still satisfies the
    claim regardless of which run_id produced it. This is the existing
    behavior from the original PR — the new run_id filter is purely
    additive and must not regress.
    """
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(
        _canonical_pass_line(tmp_path, run_id="some_run", phase_id="PHASE_1"),
        ledger,
    )
    # No expected_run_id
    result = validate(ledger, claimed_phases=["PHASE_1"])
    assert result["valid"] is True
    assert result["hold_state"] == HOLD_VALID
    assert result["errors"] == []
    assert result.get("expected_run_id") is None


def test_expected_run_id_with_matching_and_stale_mixed_evidence(tmp_path):
    """When expected_run_id is set and stale evidence is present, the
    matching canonical line satisfies the claim and the stale line is
    reported as a warning.

    This locks in that the warning system does not pollute the error
    stream with stale-evidence issues when matching evidence is present.
    """
    ledger = tmp_path / "phase_ledger.jsonl"
    # run_old has its own canonical PASS
    append_entry(
        _canonical_pass_line(tmp_path, run_id="run_old", phase_id="PHASE_1"),
        ledger,
    )
    # run_new has its own canonical PASS
    append_entry(
        _canonical_pass_line(tmp_path, run_id="run_new", phase_id="PHASE_1"),
        ledger,
    )
    result = validate(
        ledger,
        claimed_phases=["PHASE_1"],
        expected_run_id="run_new",
    )
    assert result["valid"] is True
    assert result["hold_state"] == HOLD_VALID
    # No UNCLAIMED_PHASE errors — the run_new canonical PASS satisfies it
    unclaim_errors = [
        e for e in result["errors"] if e["kind"] == "UNCLAIMED_PHASE"
    ]
    assert unclaim_errors == []
    # Stale-evidence warning is present
    stale_warnings = [w for w in result["warnings"] if "stale" in w and "run_old" in w]
    assert len(stale_warnings) >= 1
