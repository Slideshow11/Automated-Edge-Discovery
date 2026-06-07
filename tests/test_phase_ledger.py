#!/usr/bin/env python3
"""
Tests for the phase execution ledger writer.

Covers:
1. Append one valid canonical PASS line (script writer).
2. Append one FAIL line with nonzero exit_code.
3. Reject missing run_id.
4. Reject invalid status value.
5. Script/phase_exec writer requires argv.
6. Script/phase_exec writer requires absolute stdout/stderr paths.
7. Agent writer is valid as narrative but does not satisfy claimed PASS evidence.
8. Append with task-list linkage fields round-trips.
9. Reader returns all valid lines and skips malformed ones.
10. find_entry returns matching (run_id, phase_id).
11. Duplicate (run_id, phase_id) PASS appends two lines (no silent dedupe).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Ensure scripts/local is importable
SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts" / "local"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from phase_ledger import (
    AUDIT_LOG_VERSION,
    LEDGER_KIND,
    VALID_STATUSES,
    build_entry,
    append_entry,
    read_entries,
    find_entry,
)


# -----------------------------------------------------------------------------
# 1. Append one valid canonical PASS line (script writer)
# -----------------------------------------------------------------------------


def test_append_pass_phase_writes_valid_jsonl(tmp_path):
    """A canonical script-writer PASS line is appended and parses as JSON."""
    ledger = tmp_path / "phase_ledger.jsonl"
    entry = build_entry(
        run_id="run-001",
        phase_id="PHASE_2_CONFIRM_CI",
        phase_index=2,
        writer="script",
        script="scripts/local/check_pr_state.py",
        argv=["--pr-number", "389"],
        exit_code=0,
        stdout_path=str(tmp_path / "phase_2_stdout.txt"),
        stderr_path=str(tmp_path / "phase_2_stderr.txt"),
        observed_summary="5/5 CI checks passed",
        status="PASS",
        timestamp="2026-06-06T17:46:32Z",
    )
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


def test_ledger_line_has_required_fields(tmp_path):
    """All required fields are present in the appended line."""
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(
        build_entry(
            run_id="r1",
            phase_id="PHASE_1",
            writer="script",
            argv=["true"],
            exit_code=0,
            stdout_path=str(tmp_path / "out.txt"),
            stderr_path=str(tmp_path / "err.txt"),
            observed_summary="ok",
            status="PASS",
            timestamp="2026-06-06T00:00:00Z",
        ),
        ledger,
    )
    obj = json.loads(ledger.read_text().strip())
    for required in (
        "audit_log_version",
        "ledger_kind",
        "run_id",
        "phase_id",
        "writer",
        "exit_code",
        "status",
        "timestamp",
    ):
        assert required in obj, f"missing required field: {required}"


# -----------------------------------------------------------------------------
# 2. Append one FAIL line with nonzero exit_code
# -----------------------------------------------------------------------------


def test_append_fail_phase_with_nonzero_exit(tmp_path):
    """FAIL with exit_code=1 round-trips intact."""
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(
        build_entry(
            run_id="r1",
            phase_id="PHASE_3",
            writer="script",
            argv=["false"],
            exit_code=1,
            stdout_path=str(tmp_path / "out.txt"),
            stderr_path=str(tmp_path / "err.txt"),
            observed_summary="command failed",
            status="FAIL",
            timestamp="2026-06-06T00:00:01Z",
        ),
        ledger,
    )
    obj = json.loads(ledger.read_text().strip())
    assert obj["status"] == "FAIL"
    assert obj["exit_code"] == 1


# -----------------------------------------------------------------------------
# 3. Reject missing run_id
# -----------------------------------------------------------------------------


def test_append_rejects_missing_run_id(tmp_path):
    """build_entry raises ValueError when run_id is empty/None."""
    with pytest.raises(ValueError):
        build_entry(
            run_id="",
            phase_id="PHASE_1",
            writer="script",
            argv=["true"],
            exit_code=0,
            stdout_path="/tmp/out",
            stderr_path="/tmp/err",
            observed_summary="ok",
            status="PASS",
            timestamp="2026-06-06T00:00:00Z",
        )


# -----------------------------------------------------------------------------
# 4. Reject invalid status value
# -----------------------------------------------------------------------------


def test_append_rejects_invalid_status(tmp_path):
    """build_entry raises ValueError when status is not in VALID_STATUSES."""
    with pytest.raises(ValueError):
        build_entry(
            run_id="r1",
            phase_id="PHASE_1",
            writer="script",
            argv=["true"],
            exit_code=0,
            stdout_path="/tmp/out",
            stderr_path="/tmp/err",
            observed_summary="ok",
            status="MAYBE",
            timestamp="2026-06-06T00:00:00Z",
        )


def test_all_valid_statuses_accepted():
    """PASS, FAIL, HOLD, SKIP are all accepted by build_entry."""
    for s in ("PASS", "FAIL", "HOLD", "SKIP"):
        e = build_entry(
            run_id="r1",
            phase_id=f"PHASE_{s}",
            writer="script",
            argv=["true"],
            exit_code=0,
            stdout_path="/tmp/o",
            stderr_path="/tmp/e",
            observed_summary="ok",
            status=s,
            timestamp="2026-06-06T00:00:00Z",
        )
        assert e["status"] == s


# -----------------------------------------------------------------------------
# 5. Script/phase_exec writer requires argv
# -----------------------------------------------------------------------------


def test_writer_script_requires_argv(tmp_path):
    """build_entry raises ValueError when writer=script and argv is empty/None."""
    with pytest.raises(ValueError):
        build_entry(
            run_id="r1",
            phase_id="PHASE_1",
            writer="script",
            argv=[],
            exit_code=0,
            stdout_path="/tmp/o",
            stderr_path="/tmp/e",
            observed_summary="ok",
            status="PASS",
            timestamp="2026-06-06T00:00:00Z",
        )


def test_writer_phase_exec_requires_argv(tmp_path):
    """build_entry raises ValueError when writer=phase_exec and argv is empty."""
    with pytest.raises(ValueError):
        build_entry(
            run_id="r1",
            phase_id="PHASE_1",
            writer="phase_exec",
            argv=[],
            exit_code=0,
            stdout_path="/tmp/o",
            stderr_path="/tmp/e",
            observed_summary="ok",
            status="PASS",
            timestamp="2026-06-06T00:00:00Z",
        )


# -----------------------------------------------------------------------------
# 6. Script/phase_exec writer requires absolute stdout/stderr paths
# -----------------------------------------------------------------------------


def test_writer_script_requires_absolute_stdout_path(tmp_path):
    """build_entry raises ValueError when stdout_path is not absolute for script writer."""
    with pytest.raises(ValueError):
        build_entry(
            run_id="r1",
            phase_id="PHASE_1",
            writer="script",
            argv=["true"],
            exit_code=0,
            stdout_path="relative/out.txt",
            stderr_path="/tmp/err.txt",
            observed_summary="ok",
            status="PASS",
            timestamp="2026-06-06T00:00:00Z",
        )


def test_writer_phase_exec_requires_absolute_stderr_path(tmp_path):
    """build_entry raises ValueError when stderr_path is not absolute for phase_exec."""
    with pytest.raises(ValueError):
        build_entry(
            run_id="r1",
            phase_id="PHASE_1",
            writer="phase_exec",
            argv=["true"],
            exit_code=0,
            stdout_path="/tmp/out.txt",
            stderr_path="err.txt",
            observed_summary="ok",
            status="PASS",
            timestamp="2026-06-06T00:00:00Z",
        )


# -----------------------------------------------------------------------------
# 7. Agent writer is valid as narrative but does not satisfy claimed PASS evidence
# -----------------------------------------------------------------------------


def test_writer_agent_accepts_no_argv(tmp_path):
    """writer=agent with no argv is valid (narrative)."""
    e = build_entry(
        run_id="r1",
        phase_id="PHASE_NA",
        writer="agent",
        argv=None,
        exit_code=0,
        stdout_path=None,
        stderr_path=None,
        observed_summary="phase was not executed; reporting narrative only",
        status="SKIP",
        timestamp="2026-06-06T00:00:00Z",
    )
    assert e["writer"] == "agent"
    assert e["status"] == "SKIP"


def test_writer_agent_does_not_satisfy_claimed_pass(tmp_path):
    """An agent-writer PASS line is structurally valid but is_marked_as_canonical_passes() returns False."""
    from phase_ledger import is_canonical_evidence

    e = build_entry(
        run_id="r1",
        phase_id="PHASE_X",
        writer="agent",
        argv=None,
        exit_code=0,
        stdout_path=None,
        stderr_path=None,
        observed_summary="narrative",
        status="PASS",
        timestamp="2026-06-06T00:00:00Z",
    )
    # Structural validity is fine
    assert e["status"] == "PASS"
    # But canonical evidence check fails
    assert is_canonical_evidence(e) is False


def test_writer_script_satisfies_claimed_pass(tmp_path):
    """A script-writer PASS line with absolute paths IS canonical evidence."""
    from phase_ledger import is_canonical_evidence

    e = build_entry(
        run_id="r1",
        phase_id="PHASE_X",
        writer="script",
        argv=["true"],
        exit_code=0,
        stdout_path="/tmp/out.txt",
        stderr_path="/tmp/err.txt",
        observed_summary="ok",
        status="PASS",
        timestamp="2026-06-06T00:00:00Z",
    )
    assert is_canonical_evidence(e) is True


def test_writer_phase_exec_satisfies_claimed_pass(tmp_path):
    """A phase_exec-writer PASS line IS canonical evidence."""
    from phase_ledger import is_canonical_evidence

    e = build_entry(
        run_id="r1",
        phase_id="PHASE_X",
        writer="phase_exec",
        argv=["true"],
        exit_code=0,
        stdout_path="/tmp/out.txt",
        stderr_path="/tmp/err.txt",
        observed_summary="ok",
        status="PASS",
        timestamp="2026-06-06T00:00:00Z",
    )
    assert is_canonical_evidence(e) is True


# -----------------------------------------------------------------------------
# 8. Append with task-list linkage fields round-trips
# -----------------------------------------------------------------------------


def test_task_list_linkage_fields_round_trip(tmp_path):
    """source_task_id, task_packet_id, roadmap_item_id round-trip through append/read."""
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(
        build_entry(
            run_id="r1",
            phase_id="PHASE_1",
            writer="script",
            argv=["true"],
            exit_code=0,
            stdout_path="/tmp/out",
            stderr_path="/tmp/err",
            observed_summary="ok",
            status="PASS",
            timestamp="2026-06-06T00:00:00Z",
            source_task_id="aed.phase-ledger.v0",
            task_packet_id="phase-ledger-pr1",
            roadmap_item_id="roadmap-item-7",
        ),
        ledger,
    )
    obj = json.loads(ledger.read_text().strip())
    assert obj["source_task_id"] == "aed.phase-ledger.v0"
    assert obj["task_packet_id"] == "phase-ledger-pr1"
    assert obj["roadmap_item_id"] == "roadmap-item-7"


def test_task_list_linkage_fields_default_to_none(tmp_path):
    """When optional linkage fields are omitted, they default to None and round-trip as null."""
    ledger = tmp_path / "phase_ledger.jsonl"
    append_entry(
        build_entry(
            run_id="r1",
            phase_id="PHASE_1",
            writer="script",
            argv=["true"],
            exit_code=0,
            stdout_path="/tmp/o",
            stderr_path="/tmp/e",
            observed_summary="ok",
            status="PASS",
            timestamp="2026-06-06T00:00:00Z",
        ),
        ledger,
    )
    obj = json.loads(ledger.read_text().strip())
    assert obj["source_task_id"] is None
    assert obj["task_packet_id"] is None
    assert obj["roadmap_item_id"] is None


# -----------------------------------------------------------------------------
# 9. Reader returns all valid lines and skips malformed ones
# -----------------------------------------------------------------------------


def test_read_entries_returns_valid_lines_skips_malformed(tmp_path):
    """Malformed lines are skipped; valid lines are returned."""
    ledger = tmp_path / "phase_ledger.jsonl"
    ledger.write_text(
        json.dumps(
            build_entry(
                run_id="r1", phase_id="PHASE_1", writer="script",
                argv=["true"], exit_code=0,
                stdout_path="/tmp/o", stderr_path="/tmp/e",
                observed_summary="ok", status="PASS",
                timestamp="2026-06-06T00:00:00Z",
            )
        )
        + "\n"
        + "this is not valid json\n"
        + json.dumps(
            build_entry(
                run_id="r1", phase_id="PHASE_2", writer="script",
                argv=["true"], exit_code=0,
                stdout_path="/tmp/o", stderr_path="/tmp/e",
                observed_summary="ok", status="PASS",
                timestamp="2026-06-06T00:00:01Z",
            )
        )
        + "\n"
    )
    entries = read_entries(ledger)
    assert len(entries) == 2
    assert entries[0]["phase_id"] == "PHASE_1"
    assert entries[1]["phase_id"] == "PHASE_2"


# -----------------------------------------------------------------------------
# 10. find_entry returns matching (run_id, phase_id)
# -----------------------------------------------------------------------------


def test_find_entry_returns_matching(tmp_path):
    """find_entry returns the entry matching both run_id and phase_id."""
    entries = [
        {"run_id": "r1", "phase_id": "PHASE_1", "status": "PASS"},
        {"run_id": "r1", "phase_id": "PHASE_2", "status": "PASS"},
        {"run_id": "r2", "phase_id": "PHASE_1", "status": "PASS"},
    ]
    e = find_entry(entries, "r1", "PHASE_2")
    assert e is not None
    assert e["run_id"] == "r1"
    assert e["phase_id"] == "PHASE_2"


def test_find_entry_returns_none_when_missing():
    """find_entry returns None when no match exists."""
    entries = [{"run_id": "r1", "phase_id": "PHASE_1", "status": "PASS"}]
    assert find_entry(entries, "r9", "PHASE_1") is None
    assert find_entry(entries, "r1", "PHASE_999") is None


# -----------------------------------------------------------------------------
# 11. Duplicate (run_id, phase_id) PASS appends two lines (no silent dedupe)
# -----------------------------------------------------------------------------


def test_duplicate_phase_appends_two_lines(tmp_path):
    """Two appends with same (run_id, phase_id) produce two lines; validator is responsible for warning."""
    ledger = tmp_path / "phase_ledger.jsonl"
    kwargs = dict(
        run_id="r1", phase_id="PHASE_1", writer="script",
        argv=["true"], exit_code=0,
        stdout_path="/tmp/o", stderr_path="/tmp/e",
        observed_summary="ok", status="PASS",
        timestamp="2026-06-06T00:00:00Z",
    )
    append_entry(build_entry(**kwargs), ledger)
    append_entry(build_entry(**kwargs), ledger)
    lines = ledger.read_text().strip().split("\n")
    assert len(lines) == 2


# -----------------------------------------------------------------------------
# 12. phase_exec.py integration smoke tests
# -----------------------------------------------------------------------------


def test_phase_exec_writes_canonical_ledger_line(tmp_path):
    """phase_exec.py runs a command, captures artifacts, appends a canonical ledger line."""
    import subprocess
    import sys as _sys

    ledger = tmp_path / "phase_ledger.jsonl"
    proc = subprocess.run(
        [
            _sys.executable,
            str(SCRIPT_DIR / "phase_exec.py"),
            "--ledger", str(ledger),
            "--run-id", "phase-exec-smoke",
            "--phase-id", "PHASE_SMOKE",
            "--phase-index", "1",
            "--observed-summary", "echo hello produced 6 bytes",
            "--source-task-id", "aed.phase-ledger.v0",
            "--task-packet-id", "phase-ledger-pr1",
            "--", "echo", "hello",
        ],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert ledger.exists()
    lines = ledger.read_text().strip().split("\n")
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["writer"] == "phase_exec"
    assert obj["status"] == "PASS"
    assert obj["exit_code"] == 0
    assert obj["observed_summary"] == "echo hello produced 6 bytes"
    assert obj["source_task_id"] == "aed.phase-ledger.v0"
    assert obj["task_packet_id"] == "phase-ledger-pr1"
    # Artifact files exist
    from pathlib import Path as _P
    assert _P(obj["stdout_path"]).exists()
    assert _P(obj["stderr_path"]).exists()
    assert _P(obj["stdout_path"]).read_text().strip() == "hello"


def test_phase_exec_propagates_nonzero_exit(tmp_path):
    """phase_exec.py propagates the wrapped command's nonzero exit code."""
    import subprocess
    import sys as _sys

    ledger = tmp_path / "phase_ledger.jsonl"
    proc = subprocess.run(
        [
            _sys.executable,
            str(SCRIPT_DIR / "phase_exec.py"),
            "--ledger", str(ledger),
            "--run-id", "phase-exec-fail",
            "--phase-id", "PHASE_FAIL",
            "--", "false",
        ],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0  # `false` returns 1
    obj = json.loads(ledger.read_text().strip())
    assert obj["status"] == "FAIL"
    assert obj["exit_code"] != 0


# -----------------------------------------------------------------------------
# 12b. phase_exec.py rapid-duplicate artifact directory uniqueness
#      (Codex P2 finding on PR #390)
# -----------------------------------------------------------------------------


def test_phase_exec_rapid_duplicate_phase_creates_unique_artifact_dirs(tmp_path):
    """Two rapid invocations of phase_exec with the same phase_id produce distinct artifact dirs.

    Regression guard: previously the directory name used second-precision
    timestamps, so two invocations within the same second collided on
    `<phase>-<timestamp>` and `mkdir(..., exist_ok=True)` silently reused
    the directory. The fix adds microseconds + a uuid nonce to the
    directory name and fails loudly on any pre-existing directory.
    """
    import subprocess
    import sys as _sys

    ledger = tmp_path / "phase_ledger.jsonl"
    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)

    proc1 = subprocess.run(
        [
            _sys.executable, str(SCRIPT_DIR / "phase_exec.py"),
            "--ledger", str(ledger),
            "--run-id", "rapid-dup-1",
            "--phase-id", "PHASE_RAPID_DUP",
            "--", "echo", "first",
        ],
        capture_output=True, text=True,
    )
    assert proc1.returncode == 0, f"stdout={proc1.stdout!r} stderr={proc1.stderr!r}"

    proc2 = subprocess.run(
        [
            _sys.executable, str(SCRIPT_DIR / "phase_exec.py"),
            "--ledger", str(ledger),
            "--run-id", "rapid-dup-2",
            "--phase-id", "PHASE_RAPID_DUP",
            "--", "echo", "second",
        ],
        capture_output=True, text=True,
    )
    assert proc2.returncode == 0, f"stdout={proc2.stdout!r} stderr={proc2.stderr!r}"

    # Two distinct artifact directories under artifacts_root
    subdirs = sorted(p for p in artifacts_root.iterdir() if p.is_dir())
    assert len(subdirs) == 2, f"expected 2 distinct artifact dirs, got {subdirs!r}"
    assert subdirs[0].name != subdirs[1].name

    # The artifact directory names must NOT collide on second-precision timestamps
    # (i.e. they should differ on a microsecond or nonce component, not just
    # the phase slug).
    for sub in subdirs:
        assert sub.name.startswith("PHASE_RAPID_DUP-")


def test_phase_exec_rapid_duplicate_phase_does_not_overwrite_stdout_stderr(tmp_path):
    """Two rapid invocations of phase_exec preserve their own stdout/stderr evidence.

    Regression guard: previously a second invocation would overwrite the
    first invocation's stdout.txt / stderr.txt while the first ledger
    entry still pointed at those paths. With unique artifact directories
    the two invocations now keep distinct evidence files.
    """
    import subprocess
    import sys as _sys

    ledger = tmp_path / "phase_ledger.jsonl"

    proc1 = subprocess.run(
        [
            _sys.executable, str(SCRIPT_DIR / "phase_exec.py"),
            "--ledger", str(ledger),
            "--run-id", "dup-evidence-1",
            "--phase-id", "PHASE_RAPID",
            "--", "echo", "alpha",
        ],
        capture_output=True, text=True,
    )
    assert proc1.returncode == 0, f"stdout={proc1.stdout!r} stderr={proc1.stderr!r}"

    proc2 = subprocess.run(
        [
            _sys.executable, str(SCRIPT_DIR / "phase_exec.py"),
            "--ledger", str(ledger),
            "--run-id", "dup-evidence-2",
            "--phase-id", "PHASE_RAPID",
            "--", "echo", "beta",
        ],
        capture_output=True, text=True,
    )
    assert proc2.returncode == 0, f"stdout={proc2.stdout!r} stderr={proc2.stderr!r}"

    # Both ledger entries should point to distinct artifact files,
    # and each artifact should contain its own invocation's output.
    lines = ledger.read_text().strip().split("\n")
    assert len(lines) == 2

    obj1 = json.loads(lines[0])
    obj2 = json.loads(lines[1])
    assert obj1["stdout_path"] != obj2["stdout_path"]
    assert obj1["stderr_path"] != obj2["stderr_path"]

    assert Path(obj1["stdout_path"]).read_text().strip() == "alpha"
    assert Path(obj2["stdout_path"]).read_text().strip() == "beta"


# -----------------------------------------------------------------------------
# 12c. phase_exec.py observed_summary synthesis on PASS
#      (Codex P2 finding on PR #390, thread PRRT_kwDOSHFpYM6HnuOi)
# -----------------------------------------------------------------------------


def test_phase_exec_synthesizes_observed_summary_when_omitted(tmp_path):
    """phase_exec.py must never write a PASS ledger entry with an empty observed_summary.

    Regression guard: previously, when --observed-summary was omitted and
    the wrapped command exited 0, phase_exec still wrote status=PASS with
    observed_summary="". The round-5 validator rejects that combination
    as HOLD_PHASE_RESULT_INCONSISTENT, so the wrapper's default
    successful path produced evidence that the validator would not
    accept. The fix synthesizes a deterministic, non-empty summary from
    the command and exit code.
    """
    import subprocess
    import sys as _sys

    ledger = tmp_path / "phase_ledger.jsonl"
    # Use a temp helper script whose argv (a single file path) is
    # completely disjoint from the marker it prints. The synthesized
    # summary includes the command's argv (so reviewers can tell what
    # was run) but MUST NOT include captured stdout (to avoid leaking
    # secrets printed by the wrapped command).
    helper = tmp_path / "print_marker.sh"
    helper.write_text("#!/bin/sh\necho STDOUT_MARKER\n")
    helper.chmod(0o755)
    proc = subprocess.run(
        [
            _sys.executable,
            str(SCRIPT_DIR / "phase_exec.py"),
            "--ledger", str(ledger),
            "--run-id", "summary-synth-1",
            "--phase-id", "PHASE_SYNTH",
            "--phase-index", "1",
            "--source-task-id", "aed.phase-ledger.v0",
            "--task-packet-id", "phase-ledger-pr1",
            # No --observed-summary flag at all.
            "--", str(helper),
        ],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"

    obj = json.loads(ledger.read_text().strip())
    assert obj["status"] == "PASS"
    assert obj["exit_code"] == 0
    # The synthesized summary must be a non-empty string.
    assert isinstance(obj["observed_summary"], str)
    assert obj["observed_summary"] != ""
    # It must be deterministic: same shape every time for the same input.
    assert "exit_code=0" in obj["observed_summary"]
    # It must reference the actual command argv that was run (the path
    # of the helper script).
    assert "print_marker.sh" in obj["observed_summary"]
    # It must NOT include captured stdout content (no secret leak).
    # STDOUT_MARKER is a string printed by the wrapped command to its
    # own stdout; it never appears in argv. If the summary did include
    # stdout, the marker would be present.
    assert "STDOUT_MARKER" not in obj["observed_summary"], (
        f"synthesized summary leaked stdout content: {obj['observed_summary']!r}"
    )


def test_phase_exec_preserves_explicit_observed_summary(tmp_path):
    """An explicit --observed-summary value must be recorded verbatim.

    The fix only synthesizes a summary when the user did not pass one
    (or passed an empty string). A non-empty --observed-summary must
    be preserved exactly.
    """
    import subprocess
    import sys as _sys

    ledger = tmp_path / "phase_ledger.jsonl"
    proc = subprocess.run(
        [
            _sys.executable,
            str(SCRIPT_DIR / "phase_exec.py"),
            "--ledger", str(ledger),
            "--run-id", "summary-explicit-1",
            "--phase-id", "PHASE_EXPLICIT",
            "--phase-index", "1",
            "--observed-summary", "custom operator-supplied summary",
            "--", "echo", "hello",
        ],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"

    obj = json.loads(ledger.read_text().strip())
    assert obj["status"] == "PASS"
    assert obj["exit_code"] == 0
    assert obj["observed_summary"] == "custom operator-supplied summary"


def test_phase_exec_synthesized_summary_validates_cleanly(tmp_path):
    """A PASS entry produced by phase_exec without --observed-summary must validate.

    Drives the actual validate_phase_ledger.validate() against a ledger
    written by phase_exec with the default-successful path. The
    claimed phase must validate as valid (i.e. NOT
    HOLD_PHASE_RESULT_INCONSISTENT due to an empty summary). This is
    the end-to-end regression guard for thread PRRT_kwDOSHFpYM6HnuOi.
    """
    import subprocess
    import sys as _sys

    from validate_phase_ledger import (
        HOLD_VALID,
        HOLD_PHASE_RESULT_INCONSISTENT,
        validate,
    )

    ledger = tmp_path / "phase_ledger.jsonl"
    helper = tmp_path / "print_marker.sh"
    helper.write_text("#!/bin/sh\necho STDOUT_MARKER\n")
    helper.chmod(0o755)
    proc = subprocess.run(
        [
            _sys.executable,
            str(SCRIPT_DIR / "phase_exec.py"),
            "--ledger", str(ledger),
            "--run-id", "validate-cleanly-1",
            "--phase-id", "PHASE_VALIDATE_CLEAN",
            "--phase-index", "1",
            # No --observed-summary flag.
            "--", str(helper),
        ],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"

    result = validate(
        ledger_path=ledger,
        claimed_phases=["PHASE_VALIDATE_CLEAN"],
        expected_run_id="validate-cleanly-1",
    )

    # The validator must NOT reject the synthesized summary as
    # RESULT_INCONSISTENT (the round-5 bug).
    for err in result["errors"]:
        assert err.get("kind") != "RESULT_INCONSISTENT", (
            f"validator rejected synthesized summary: {err}"
        )
    # And the phase claim must validate cleanly.
    assert result["hold_state"] == HOLD_VALID, (
        f"expected HOLD_VALID, got {result['hold_state']!r} "
        f"with errors={result['errors']!r}"
    )
    assert result["valid"] is True


# -----------------------------------------------------------------------------
# 12d. phase_exec.py preserves exit code on non-UTF-8 output
#      (Codex P2 finding on PR #390, thread PRRT_kwDOSHFpYM6Hn2SJ)
# -----------------------------------------------------------------------------


def test_phase_exec_preserves_exit_code_on_non_utf8_output(tmp_path):
    """A successful command that prints non-UTF-8 bytes must record PASS, not FAIL.

    Regression guard: previously phase_exec used
    ``subprocess.run(..., text=True)``, which raises ``UnicodeDecodeError``
    when the wrapped command writes bytes that are not decodable as
    UTF-8. The broad ``except Exception`` path then caught that error
    and recorded ``exit_code=-1, status=FAIL, stdout=""`` — so a
    successful phase that happened to print non-UTF-8 bytes was
    falsely recorded as failed and could block ledger validation. The
    fix drops ``text=True`` and decodes the captured bytes with
    ``errors="replace"`` to preserve the real exit code.
    """
    import subprocess
    import sys as _sys

    # Helper script that writes a non-UTF-8 byte sequence to stdout
    # and exits 0. We use Python so the test does not depend on
    # platform-specific shell behavior with binary data.
    helper = tmp_path / "print_non_utf8.py"
    helper.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(b'\\xff\\xfe\\x00\\x01NON_UTF8_OUTPUT\\n')\n"
        "sys.exit(0)\n"
    )
    ledger = tmp_path / "phase_ledger.jsonl"
    proc = subprocess.run(
        [
            _sys.executable,
            str(SCRIPT_DIR / "phase_exec.py"),
            "--ledger", str(ledger),
            "--run-id", "non-utf8-1",
            "--phase-id", "PHASE_NON_UTF8",
            "--phase-index", "1",
            "--source-task-id", "aed.phase-ledger.v0",
            "--task-packet-id", "phase-ledger-pr1",
            # No --observed-summary; we want to also exercise the
            # round-5 summary-synthesis path on the real exit code.
            "--", _sys.executable, str(helper),
        ],
        capture_output=True, text=True,
    )
    # phase_exec itself should exit 0 because the wrapped command
    # exited 0; the previous bug would have made phase_exec record
    # FAIL and propagate a nonzero exit code.
    assert proc.returncode == 0, (
        f"phase_exec propagated wrong exit: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )

    obj = json.loads(ledger.read_text().strip())
    # The real exit code must be preserved.
    assert obj["exit_code"] == 0
    # And the status must be PASS, not FAIL.
    assert obj["status"] == "PASS"
    # The synthesized summary is still non-empty (round-5 invariant).
    assert isinstance(obj["observed_summary"], str)
    assert obj["observed_summary"] != ""
    # The artifact file must exist and contain at least the readable
    # suffix of the wrapped command's output (the undecodable bytes
    # are replaced with U+FFFD, but the readable tail survives).
    from pathlib import Path as _P
    captured = _P(obj["stdout_path"]).read_text()
    assert "NON_UTF8_OUTPUT" in captured


def test_phase_exec_synthesized_summary_validates_for_non_utf8_phase(tmp_path):
    """A PASS entry produced by a non-UTF-8-emitting command must validate cleanly.

    End-to-end regression guard: a phase that exits 0 and writes
    non-UTF-8 bytes must (a) be recorded as PASS, (b) carry a
    non-empty observed_summary, and (c) satisfy the round-5 validator
    with no RESULT_INCONSISTENT error.
    """
    import subprocess
    import sys as _sys

    from validate_phase_ledger import (
        HOLD_VALID,
        validate,
    )

    helper = tmp_path / "print_non_utf8_validate.py"
    helper.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(b'\\xff\\xfeVALIDATE_NON_UTF8\\n')\n"
        "sys.exit(0)\n"
    )
    ledger = tmp_path / "phase_ledger.jsonl"
    proc = subprocess.run(
        [
            _sys.executable,
            str(SCRIPT_DIR / "phase_exec.py"),
            "--ledger", str(ledger),
            "--run-id", "non-utf8-validate-1",
            "--phase-id", "PHASE_NON_UTF8_VALIDATE",
            "--phase-index", "1",
            "--", _sys.executable, str(helper),
        ],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        f"phase_exec propagated wrong exit: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )

    result = validate(
        ledger_path=ledger,
        claimed_phases=["PHASE_NON_UTF8_VALIDATE"],
        expected_run_id="non-utf8-validate-1",
    )

    for err in result["errors"]:
        assert err.get("kind") != "RESULT_INCONSISTENT", (
            f"validator rejected non-UTF-8 PASS: {err}"
        )
    assert result["hold_state"] == HOLD_VALID, (
        f"expected HOLD_VALID, got {result['hold_state']!r} "
        f"with errors={result['errors']!r}"
    )
    assert result["valid"] is True


def test_phase_exec_real_invocation_error_still_records_fail(tmp_path):
    """A genuine invocation error (FileNotFoundError) must still record FAIL.

    Negative regression guard: the previous bug fix uses a broad
    ``except Exception`` path for genuine invocation errors. We want
    to make sure the new bytes-capture path does not mask FileNotFoundError
    or other real OS-level errors — a missing command must still be
    recorded as FAIL with exit_code=127.
    """
    import subprocess
    import sys as _sys

    ledger = tmp_path / "phase_ledger.jsonl"
    proc = subprocess.run(
        [
            _sys.executable,
            str(SCRIPT_DIR / "phase_exec.py"),
            "--ledger", str(ledger),
            "--run-id", "missing-1",
            "--phase-id", "PHASE_MISSING",
            "--phase-index", "1",
            # No such binary on PATH.
            "--", "definitely-not-a-real-binary-xyz123",
        ],
        capture_output=True, text=True,
    )
    # phase_exec must propagate the FileNotFoundError as a nonzero exit.
    assert proc.returncode != 0
    obj = json.loads(ledger.read_text().strip())
    assert obj["status"] == "FAIL"
    assert obj["exit_code"] != 0
    # Stderr artifact must record the not-found message.
    from pathlib import Path as _P
    captured_stderr = _P(obj["stderr_path"]).read_text()
    assert "command not found" in captured_stderr or "not found" in captured_stderr


# -----------------------------------------------------------------------------
# 13. aed_final_gate.py integration tests
# -----------------------------------------------------------------------------


def _make_minimal_gate_mocks(sha="46f3bf2b4fc490f3991409c33448c678c2f6ea10"):
    """Build the minimal mocks needed to call run_final_gate() without hitting GitHub."""
    from unittest.mock import MagicMock

    def fake_subprocess_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if isinstance(cmd, list):
            if cmd[0] == "git" and len(cmd) >= 4 and cmd[2] == "get-url" and cmd[3] == "origin":
                return MagicMock(
                    stdout="https://github.com/Slideshow11/Automated-Edge-Discovery.git",
                    returncode=0,
                )
            if cmd[0] == "gh" and "api" in cmd:
                return MagicMock(stdout="{}", returncode=0)
        return MagicMock(stdout="{}", returncode=0)

    def fake_gh_pr_info(pr_number, repo):
        return {
            "number": 389,
            "state": "open",
            "mergeable": "MERGEABLE",
            "head": {"sha": sha},
            "headRefOid": sha,
            "changed_files": [
                "scripts/local/run_autocoder_single_task.py",
            ],
            "base": {"sha": "a844c1a1a95e584220bd16b33a58da549e62e228"},
        }

    def fake_gh_runs_for_sha(s, repo):
        return [{"head_sha": sha, "name": "CI", "conclusion": "success"}]

    def fake_gh(query, *args):
        # Minimal: return an empty file list for the changed-files query
        return {"files": []}

    return fake_subprocess_run, fake_gh_pr_info, fake_gh_runs_for_sha, fake_gh


def test_aed_final_gate_without_new_flags_preserves_behavior(tmp_path):
    """Without --require-phase-ledger, run_final_gate output keeps the same shape.

    The new phase_ledger field is added (matches persistent_mutation_guard
    style) but with required=False, status=not_required. final_recommendation
    is unaffected.
    """
    from unittest.mock import patch as _patch

    sha = "46f3bf2b4fc490f3991409c33448c678c2f6ea10"
    validation_file = tmp_path / "validation.json"
    validation_file.write_text(json.dumps({
        "tests_collected": 153, "passed": 153, "exit_code": 0,
    }))
    codex_file = tmp_path / "codex.md"
    codex_file.write_text(f"Codex review of commit {sha}\nCLEAN — no issues.\n")

    fake_run, fake_pr, fake_runs, fake_gh = _make_minimal_gate_mocks(sha=sha)

    with _patch("subprocess.run", side_effect=fake_run), \
         _patch("aed_final_gate.gh_pr_info", side_effect=fake_pr), \
         _patch("aed_final_gate.gh_runs_for_sha", side_effect=fake_runs), \
         _patch("aed_final_gate.gh", side_effect=fake_gh):
        from aed_final_gate import run_final_gate
        gate = run_final_gate(
            pr_number=389,
            expected_head_sha=sha,
            allowed_files=["scripts/**", "tests/**"],
            local_validation_path=str(validation_file),
            codex_artifact_path=str(codex_file),
            output_json_path=str(tmp_path / "FINAL_GATE.json"),
            output_md_path=str(tmp_path / "FINAL_GATE.md"),
            allow_admin=False,
        )

    # Default-off phase_ledger field is present but inactive
    assert "phase_ledger" in gate
    pl = gate["phase_ledger"]
    assert pl["required"] is False
    assert pl["hold_state"] == "not_required"
    # Final recommendation is unaffected
    assert gate["final_recommendation"] == "MERGE_READY"


def test_aed_final_gate_require_phase_ledger_empty_ledger_returns_hold(tmp_path):
    """With --require-phase-ledger and an empty/missing ledger + claimed phase, returns HOLD_UNEVIDENCED_PASS."""
    from unittest.mock import patch as _patch

    sha = "46f3bf2b4fc490f3991409c33448c678c2f6ea10"
    validation_file = tmp_path / "validation.json"
    validation_file.write_text(json.dumps({
        "tests_collected": 153, "passed": 153, "exit_code": 0,
    }))
    codex_file = tmp_path / "codex.md"
    codex_file.write_text(f"Codex review of commit {sha}\nCLEAN — no issues.\n")

    # Empty ledger file (exists but no lines)
    empty_ledger = tmp_path / "empty_phase_ledger.jsonl"
    empty_ledger.write_text("")

    fake_run, fake_pr, fake_runs, fake_gh = _make_minimal_gate_mocks(sha=sha)

    with _patch("subprocess.run", side_effect=fake_run), \
         _patch("aed_final_gate.gh_pr_info", side_effect=fake_pr), \
         _patch("aed_final_gate.gh_runs_for_sha", side_effect=fake_runs), \
         _patch("aed_final_gate.gh", side_effect=fake_gh):
        from aed_final_gate import run_final_gate
        gate = run_final_gate(
            pr_number=389,
            expected_head_sha=sha,
            allowed_files=["scripts/**", "tests/**"],
            local_validation_path=str(validation_file),
            codex_artifact_path=str(codex_file),
            output_json_path=str(tmp_path / "FINAL_GATE.json"),
            output_md_path=str(tmp_path / "FINAL_GATE.md"),
            allow_admin=False,
            phase_ledger_path=str(empty_ledger),
            claimed_phases=["PHASE_1", "PHASE_2"],
            require_phase_ledger=True,
            phase_ledger_expected_run_id="r1",
        )

    assert gate["final_recommendation"] == "HOLD_UNEVIDENCED_PASS"
    pl = gate["phase_ledger"]
    assert pl["required"] is True
    assert pl["valid"] is False
    assert pl["hold_state"] == "HOLD_UNEVIDENCED_PASS"
    assert pl["line_count"] == 0
    assert pl["claimed_count"] == 2
    assert pl["error_count"] >= 1


def test_aed_final_gate_require_phase_ledger_with_valid_evidence_keeps_merge_ready(tmp_path):
    """With --require-phase-ledger and canonical evidence, MERGE_READY is preserved."""
    from unittest.mock import patch as _patch

    sha = "46f3bf2b4fc490f3991409c33448c678c2f6ea10"
    validation_file = tmp_path / "validation.json"
    validation_file.write_text(json.dumps({
        "tests_collected": 153, "passed": 153, "exit_code": 0,
    }))
    codex_file = tmp_path / "codex.md"
    codex_file.write_text(f"Codex review of commit {sha}\nCLEAN — no issues.\n")

    # Build a ledger with two canonical evidence lines for PHASE_1 and PHASE_2
    ledger = tmp_path / "phase_ledger.jsonl"
    for pid in ("PHASE_1", "PHASE_2"):
        out, err = tmp_path / f"{pid}_out.txt", tmp_path / f"{pid}_err.txt"
        out.write_text("ok\n")
        err.write_text("")
        append_entry(
            build_entry(
                run_id="r1", phase_id=pid, writer="script",
                script="scripts/local/check_pr_state.py",
                argv=["--phase", pid], exit_code=0,
                stdout_path=str(out), stderr_path=str(err),
                observed_summary=f"{pid} ok", status="PASS",
                timestamp="2026-06-06T00:00:00Z",
            ),
            ledger,
        )

    fake_run, fake_pr, fake_runs, fake_gh = _make_minimal_gate_mocks(sha=sha)

    with _patch("subprocess.run", side_effect=fake_run), \
         _patch("aed_final_gate.gh_pr_info", side_effect=fake_pr), \
         _patch("aed_final_gate.gh_runs_for_sha", side_effect=fake_runs), \
         _patch("aed_final_gate.gh", side_effect=fake_gh):
        from aed_final_gate import run_final_gate
        gate = run_final_gate(
            pr_number=389,
            expected_head_sha=sha,
            allowed_files=["scripts/**", "tests/**"],
            local_validation_path=str(validation_file),
            codex_artifact_path=str(codex_file),
            output_json_path=str(tmp_path / "FINAL_GATE.json"),
            output_md_path=str(tmp_path / "FINAL_GATE.md"),
            allow_admin=False,
            phase_ledger_path=str(ledger),
            claimed_phases=["PHASE_1", "PHASE_2"],
            require_phase_ledger=True,
            phase_ledger_expected_run_id="r1",
        )

    assert gate["final_recommendation"] == "MERGE_READY"
    pl = gate["phase_ledger"]
    assert pl["required"] is True
    assert pl["valid"] is True
    assert pl["hold_state"] == "HOLD_VALID"
    assert pl["line_count"] == 2
