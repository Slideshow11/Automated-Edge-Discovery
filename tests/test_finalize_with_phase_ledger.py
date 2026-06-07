"""
Tests for scripts/local/finalize_with_phase_ledger.py

Covers the leaf adapter's contract:
- reads aed.run_summary.v0 run_summary.json
- forwards phase_ledger fields into aed_final_gate.run_final_gate()
- default-off when no phase_ledger_* keys are present
- fail-closed (via aed_final_gate) when ANY phase_ledger_* key is
  present but evidence is missing, empty, or malformed
- no subprocess, no gh/git/merges, no --allow-admin

These tests are pure unit tests: ``aed_final_gate.run_final_gate`` is
monkeypatched so no GitHub or git calls are made. The adapter is
imported as ``finalize_with_phase_ledger`` after prepending the
``scripts/local`` directory to ``sys.path`` (same pattern used by
``test_aed_final_gate.py``).
"""

import argparse
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

# Module under test
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "local"))
import aed_final_gate  # noqa: E402
import finalize_with_phase_ledger as fwpl  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_args(
    *,
    run_summary: str = "/tmp/run_summary.json",
    pr_number: int = 392,
    expected_head_sha: str = "bf4420084c1c2f1f0e7ff078bcac14c01f8f109a",
    allowed_files: str = "scripts/**,tests/**",
    local_validation_path: str = "/tmp/validation.json",
    codex_artifact_path: str = "/tmp/codex.md",
    output_json: str = "/tmp/FINAL_GATE.json",
    output_md: str = "/tmp/FINAL_GATE.md",
    allow_codex_skip: bool = False,
    require_persistent_guard: bool = False,
    persistent_guard_root: str = "/home/max/.hermes",
    persistent_guard_snapshot: Optional[str] = None,
    persistent_guard_compare_json: Optional[str] = None,
    persistent_guard_compare_md: Optional[str] = None,
) -> argparse.Namespace:
    """Build an ``argparse.Namespace`` matching the adapter's expected shape.

    Only the fields actually consumed by ``run_finalize`` are populated;
    extra fields are added here so the namespace can also be fed to
    ``argparse.ArgumentParser`` for the
    ``test_admin_flag_hard_rejected`` test.
    """
    return argparse.Namespace(
        run_summary=run_summary,
        pr_number=pr_number,
        expected_head_sha=expected_head_sha,
        allowed_files=allowed_files,
        local_validation_path=local_validation_path,
        codex_artifact_path=codex_artifact_path,
        output_json=output_json,
        output_md=output_md,
        allow_codex_skip=allow_codex_skip,
        require_persistent_guard=require_persistent_guard,
        persistent_guard_root=persistent_guard_root,
        persistent_guard_snapshot=persistent_guard_snapshot,
        persistent_guard_compare_json=persistent_guard_compare_json,
        persistent_guard_compare_md=persistent_guard_compare_md,
    )


def _write_run_summary(
    path: Path,
    *,
    phase_ledger_path=None,
    phase_ledger_claimed_phases=None,
    phase_ledger_expected_run_id=None,
    run_summary_version: str = "aed.run_summary.v0",
) -> None:
    """Write a minimal aed.run_summary.v0 fixture to ``path``."""
    data: dict = {
        "run_summary_version": run_summary_version,
        "controller": "run_autocoder_single_task.py",
        "task_id": "fixture-task-id",
        "status": "READY",
        "stage": "done",
    }
    if phase_ledger_path is not None:
        data["phase_ledger_path"] = phase_ledger_path
    if phase_ledger_claimed_phases is not None:
        data["phase_ledger_claimed_phases"] = phase_ledger_claimed_phases
    if phase_ledger_expected_run_id is not None:
        data["phase_ledger_expected_run_id"] = phase_ledger_expected_run_id
    path.write_text(json.dumps(data), encoding="utf-8")


def _mock_run_final_gate(monkeypatch, return_value: dict) -> MagicMock:
    """Replace ``aed_final_gate.run_final_gate`` with a MagicMock and return it."""
    mock = MagicMock(return_value=return_value)
    monkeypatch.setattr(aed_final_gate, "run_final_gate", mock)
    return mock


# A canonical MERGE_READY gate payload, used by the happy-path tests.
_MERGE_READY_GATE: dict = {
    "pr_number": 392,
    "head_sha": "bf4420084c1c2f1f0e7ff078bcac14c01f8f109a",
    "final_recommendation": "MERGE_READY",
    "phase_ledger": {
        "required": True,
        "valid": True,
        "hold_state": "HOLD_VALID",
        "claimed_phases": ["PHASE_1"],
        "claimed_count": 1,
        "line_count": 5,
        "error_count": 0,
        "warning_count": 0,
        "errors": [],
        "warnings": [],
        "expected_run_id": "fixture-task-id",
        "ledger_path": "/abs/phase_ledger.jsonl",
        "message": "phase ledger valid (5 lines, 0 errors)",
    },
}

_HOLD_UNEVIDENCED_GATE: dict = {
    "pr_number": 392,
    "head_sha": "bf4420084c1c2f1f0e7ff078bcac14c01f8f109a",
    "final_recommendation": "HOLD_UNEVIDENCED_PASS",
    "phase_ledger": {
        "required": True,
        "valid": False,
        "hold_state": "HOLD_UNEVIDENCED_PASS",
        "claimed_phases": None,
        "claimed_count": 0,
        "line_count": 0,
        "error_count": 1,
        "warning_count": 0,
        "errors": [
            {
                "phase_id": "<phase_ledger>",
                "line": 0,
                "kind": "LEDGER_PATH_MISSING",
                "detail": "no --phase-ledger path supplied",
            }
        ],
        "warnings": [],
        "expected_run_id": None,
        "ledger_path": None,
        "message": "require_phase_ledger is set but no --phase-ledger path was provided",
    },
}

_BLOCK_GATE: dict = {
    "pr_number": 392,
    "head_sha": "bf4420084c1c2f1f0e7ff078bcac14c01f8f109a",
    "final_recommendation": "BLOCK",
    "phase_ledger": {
        "required": False,
        "valid": True,
        "hold_state": "not_required",
        "claimed_phases": [],
        "claimed_count": 0,
        "line_count": 0,
        "error_count": 0,
        "warning_count": 0,
        "errors": [],
        "warnings": [],
        "expected_run_id": None,
        "ledger_path": None,
        "message": "phase ledger not required",
    },
}


# ---------------------------------------------------------------------------
# 1. Happy path: all 3 ledger fields present, gate returns MERGE_READY.
# ---------------------------------------------------------------------------


def test_happy_path_with_all_ledger_fields(tmp_path, monkeypatch):
    ledger_path = "/abs/path/from/runner.jsonl"
    summary_path = tmp_path / "run_summary.json"
    _write_run_summary(
        summary_path,
        phase_ledger_path=ledger_path,
        phase_ledger_claimed_phases=["PHASE_1", "PHASE_2"],
        phase_ledger_expected_run_id="fixture-task-id",
    )
    mock_run = _mock_run_final_gate(monkeypatch, _MERGE_READY_GATE)

    args = _base_args(run_summary=str(summary_path))
    rc = fwpl.run_finalize(args)

    assert rc == 0
    assert mock_run.call_count == 1
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["require_phase_ledger"] is True
    assert call_kwargs["phase_ledger_path"] == ledger_path  # pass-through unchanged
    assert call_kwargs["claimed_phases"] == ["PHASE_1", "PHASE_2"]
    assert call_kwargs["phase_ledger_expected_run_id"] == "fixture-task-id"
    assert call_kwargs["allow_admin"] is False  # hard-coded
    # Standard pass-through args also preserved
    assert call_kwargs["pr_number"] == 392
    assert call_kwargs["expected_head_sha"] == "bf4420084c1c2f1f0e7ff078bcac14c01f8f109a"


# ---------------------------------------------------------------------------
# 2. Default-off: no phase_ledger_* keys => require_phase_ledger=False.
# ---------------------------------------------------------------------------


def test_ledger_fields_omitted_does_not_require(tmp_path, monkeypatch):
    summary_path = tmp_path / "run_summary.json"
    _write_run_summary(summary_path)  # no ledger keys at all
    mock_run = _mock_run_final_gate(monkeypatch, _MERGE_READY_GATE)

    args = _base_args(run_summary=str(summary_path))
    rc = fwpl.run_finalize(args)

    assert rc == 0
    assert mock_run.call_count == 1
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["require_phase_ledger"] is False
    assert call_kwargs["phase_ledger_path"] is None
    assert call_kwargs["claimed_phases"] is None
    assert call_kwargs["phase_ledger_expected_run_id"] is None


# ---------------------------------------------------------------------------
# 3. phase_ledger_path present but claimed_phases OMITTED:
#    require_phase_ledger=True (fail-closed), claimed_phases=None is forwarded
#    so aed_final_gate's fail-closed guards fire.
# ---------------------------------------------------------------------------


def test_ledger_path_present_but_claimed_phases_omitted_requires_fail_closed(
    tmp_path, monkeypatch
):
    summary_path = tmp_path / "run_summary.json"
    _write_run_summary(
        summary_path,
        phase_ledger_path="/abs/x.jsonl",
        phase_ledger_expected_run_id="tid",
        # phase_ledger_claimed_phases OMITTED
    )
    mock_run = _mock_run_final_gate(monkeypatch, _HOLD_UNEVIDENCED_GATE)

    args = _base_args(run_summary=str(summary_path))
    rc = fwpl.run_finalize(args)

    # Gate returns HOLD_UNEVIDENCED_PASS => adapter exit 1
    assert rc == 1
    assert mock_run.call_count == 1
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["require_phase_ledger"] is True
    assert call_kwargs["phase_ledger_path"] == "/abs/x.jsonl"
    assert call_kwargs["claimed_phases"] is None  # OMITTED in summary
    assert call_kwargs["phase_ledger_expected_run_id"] == "tid"


# ---------------------------------------------------------------------------
# 4. phase_ledger_claimed_phases: [] (empty list): require_phase_ledger=True,
#    but the empty list is forwarded so aed_final_gate's empty-claim guard
#    fires (per Codex round 4 P2 fix on PR #390).
# ---------------------------------------------------------------------------


def test_empty_claimed_phases_requires_fail_closed(tmp_path, monkeypatch):
    summary_path = tmp_path / "run_summary.json"
    _write_run_summary(
        summary_path,
        phase_ledger_path="/abs/x.jsonl",
        phase_ledger_claimed_phases=[],
        phase_ledger_expected_run_id="tid",
    )
    mock_run = _mock_run_final_gate(monkeypatch, _HOLD_UNEVIDENCED_GATE)

    args = _base_args(run_summary=str(summary_path))
    rc = fwpl.run_finalize(args)

    assert rc == 1
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["require_phase_ledger"] is True
    assert call_kwargs["claimed_phases"] == []  # empty list passed through
    assert call_kwargs["phase_ledger_path"] == "/abs/x.jsonl"
    assert call_kwargs["phase_ledger_expected_run_id"] == "tid"


# ---------------------------------------------------------------------------
# 5. Partial ledger fields (parameterized) — any one present flips the
#    gate into require_phase_ledger=True. The other two are forwarded
#    as None (omitted in summary), and aed_final_gate's fail-closed
#    guards produce HOLD_UNEVIDENCED_PASS.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ledger_kwargs,description",
    [
        (
            {
                "phase_ledger_claimed_phases": ["PHASE_1"],
                "phase_ledger_expected_run_id": "tid",
                # phase_ledger_path OMITTED
            },
            "claims + run_id, no path",
        ),
        (
            {
                "phase_ledger_path": "/abs/x.jsonl",
                "phase_ledger_claimed_phases": ["PHASE_1"],
                # phase_ledger_expected_run_id OMITTED
            },
            "path + claims, no run_id",
        ),
        (
            {
                "phase_ledger_path": "/abs/x.jsonl",
                "phase_ledger_expected_run_id": "tid",
                # phase_ledger_claimed_phases OMITTED
            },
            "path + run_id, no claims (covered by #3 too but locked here)",
        ),
    ],
    ids=["no_path", "no_run_id", "no_claims"],
)
def test_partial_ledger_fields_fail_closed(
    tmp_path, monkeypatch, ledger_kwargs, description
):
    summary_path = tmp_path / "run_summary.json"
    _write_run_summary(summary_path, **ledger_kwargs)
    mock_run = _mock_run_final_gate(monkeypatch, _HOLD_UNEVIDENCED_GATE)

    args = _base_args(run_summary=str(summary_path))
    rc = fwpl.run_finalize(args)

    # Fail-closed: any partial ledger data => require=True => gate emits
    # HOLD_UNEVIDENCED_PASS => adapter exit 1.
    assert rc == 1, description
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["require_phase_ledger"] is True, description


# ---------------------------------------------------------------------------
# 6. Missing run_summary file => exit 2, no run_final_gate call.
# ---------------------------------------------------------------------------


def test_missing_run_summary_file_exits_2(tmp_path, monkeypatch):
    missing = tmp_path / "does_not_exist.json"
    mock_run = _mock_run_final_gate(monkeypatch, _MERGE_READY_GATE)

    args = _base_args(run_summary=str(missing))
    captured = io.StringIO()
    with redirect_stderr(captured):
        rc = fwpl.run_finalize(args)

    assert rc == 2
    assert "not found" in captured.getvalue()
    assert mock_run.call_count == 0


# ---------------------------------------------------------------------------
# 7. Malformed run_summary JSON => exit 2, no run_final_gate call.
# ---------------------------------------------------------------------------


def test_malformed_run_summary_json_exits_2(tmp_path, monkeypatch):
    summary_path = tmp_path / "run_summary.json"
    summary_path.write_text("not valid json at all", encoding="utf-8")
    mock_run = _mock_run_final_gate(monkeypatch, _MERGE_READY_GATE)

    args = _base_args(run_summary=str(summary_path))
    captured = io.StringIO()
    with redirect_stderr(captured):
        rc = fwpl.run_finalize(args)

    assert rc == 2
    assert "malformed JSON" in captured.getvalue()
    assert mock_run.call_count == 0


# ---------------------------------------------------------------------------
# 8. Wrong run_summary_version => warn but continue, gate still called.
# ---------------------------------------------------------------------------


def test_wrong_run_summary_version_warns_but_continues(tmp_path, monkeypatch):
    summary_path = tmp_path / "run_summary.json"
    _write_run_summary(
        summary_path,
        phase_ledger_path="/abs/x.jsonl",
        phase_ledger_claimed_phases=["PHASE_1"],
        phase_ledger_expected_run_id="tid",
        run_summary_version="aed.run_summary.v999",
    )
    mock_run = _mock_run_final_gate(monkeypatch, _MERGE_READY_GATE)

    args = _base_args(run_summary=str(summary_path))
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        rc = fwpl.run_finalize(args)

    # Processing continues
    assert rc == 0
    assert mock_run.call_count == 1
    # Warning was emitted
    assert "unexpected run_summary_version" in captured_err.getvalue()
    # The actual ledger kwargs were still forwarded (forward-compat)
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["require_phase_ledger"] is True


# ---------------------------------------------------------------------------
# 9. --allow-admin is hard-rejected (argparse level + adapter level).
# ---------------------------------------------------------------------------


def test_admin_flag_hard_rejected(tmp_path, monkeypatch):
    summary_path = tmp_path / "run_summary.json"
    _write_run_summary(summary_path)  # default-off
    mock_run = _mock_run_final_gate(monkeypatch, _MERGE_READY_GATE)

    # Approach A: argparse should refuse --allow-admin outright.
    parser = fwpl._build_parser()
    argv = [
        "--run-summary", str(summary_path),
        "--pr-number", "392",
        "--expected-head-sha", "bf4420084c1c2f1f0e7ff078bcac14c01f8f109a",
        "--allowed-files", "scripts/**,tests/**",
        "--local-validation-path", "/tmp/validation.json",
        "--codex-artifact-path", "/tmp/codex.md",
        "--output-json", "/tmp/FINAL_GATE.json",
        "--output-md", "/tmp/FINAL_GATE.md",
        "--allow-admin",  # MUST be rejected
    ]
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(argv)
    assert exc.value.code == 2
    assert mock_run.call_count == 0

    # Approach B: if a caller shims allow_admin=True onto the
    # namespace, the adapter's _reject_admin guard fires.
    bad_args = _base_args(run_summary=str(summary_path))
    bad_args.allow_admin = True
    with pytest.raises(SystemExit) as exc:
        fwpl.run_finalize(bad_args)
    assert exc.value.code == 2
    assert mock_run.call_count == 0


# ---------------------------------------------------------------------------
# 10. Exit code propagation from final gate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "recommendation,expected_rc",
    [
        ("MERGE_READY", 0),
        ("BLOCK", 1),
        ("HOLD_UNEVIDENCED_PASS", 1),
        ("WAIT", 1),
    ],
    ids=["merge_ready", "block", "hold_unevidenced", "wait"],
)
def test_exit_code_propagates_from_final_gate(
    tmp_path, monkeypatch, recommendation, expected_rc
):
    summary_path = tmp_path / "run_summary.json"
    _write_run_summary(summary_path)  # default-off
    gate = dict(_MERGE_READY_GATE)
    gate["final_recommendation"] = recommendation
    mock_run = _mock_run_final_gate(monkeypatch, gate)

    args = _base_args(run_summary=str(summary_path))
    captured_out = io.StringIO()
    with redirect_stdout(captured_out):
        rc = fwpl.run_finalize(args)

    assert rc == expected_rc
    # The gate JSON was emitted to stdout
    emitted = json.loads(captured_out.getvalue())
    assert emitted["final_recommendation"] == recommendation


# ---------------------------------------------------------------------------
# 11. phase_ledger_path passed through UNCHANGED (no resolve/abspath).
# ---------------------------------------------------------------------------


def test_phase_ledger_path_passed_through_unchanged(tmp_path, monkeypatch):
    # Deliberately a non-normalized absolute path to catch any
    # resolve() / abspath() / normpath() the adapter might do.
    weird_path = "/abs/../weird//path/from/runner.jsonl"
    summary_path = tmp_path / "run_summary.json"
    _write_run_summary(
        summary_path,
        phase_ledger_path=weird_path,
        phase_ledger_claimed_phases=["PHASE_1"],
        phase_ledger_expected_run_id="tid",
    )
    mock_run = _mock_run_final_gate(monkeypatch, _MERGE_READY_GATE)

    args = _base_args(run_summary=str(summary_path))
    fwpl.run_finalize(args)

    call_kwargs = mock_run.call_args.kwargs
    # The path is forwarded byte-for-byte — no normalization, no
    # resolve(), no abspath(). The final gate itself is the
    # authority on path handling.
    assert call_kwargs["phase_ledger_path"] == weird_path


# ---------------------------------------------------------------------------
# 12. output_json_path / output_md_path passed through exactly as CLI args.
# ---------------------------------------------------------------------------


def test_adapter_does_not_modify_output_paths(tmp_path, monkeypatch):
    summary_path = tmp_path / "run_summary.json"
    _write_run_summary(summary_path)
    mock_run = _mock_run_final_gate(monkeypatch, _MERGE_READY_GATE)

    out_json = str(tmp_path / "nested" / "FINAL_GATE.json")
    out_md = str(tmp_path / "nested" / "FINAL_GATE.md")
    args = _base_args(run_summary=str(summary_path),
                      output_json=out_json, output_md=out_md)
    fwpl.run_finalize(args)

    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["output_json_path"] == out_json
    assert call_kwargs["output_md_path"] == out_md
