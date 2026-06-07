"""
Tests for scripts/local/merge_readiness_with_phase_ledger.py

Covers the leaf wrapper's contract:
- default-off: when --run-summary is omitted, the wrapper
  delegates to merge_pr_safely.py unchanged and the phase-gate
  adapter is never called;
- opt-in: when --run-summary is set, the wrapper invokes the
  phase-gate adapter first; if the adapter returns 0 the
  wrapper then invokes merge_pr_safely.py; if the adapter
  returns non-zero, the wrapper exits with the adapter's
  code and merge_pr_safely.py is NEVER called;
- real --expected-head-sha is REQUIRED when --run-summary is
  set (the wrapper does NOT fabricate or default this value);
- argparse refuses --allow-admin; defense-in-depth _reject_admin
  also catches shimmed allow_admin=True;
- the merge_pr_safely.py subprocess command uses the real
  merge_pr_safely CLI surface and does not include any
  wrapper-only args or persistent-guard args (since
  merge_pr_safely does not support them);
- the module's forbidden-executable-call self-check is enforced.

These tests are pure unit tests: ``finalize_with_phase_ledger.run_finalize``
is monkeypatched, and ``subprocess.run`` is monkeypatched. No
real GitHub or git calls are made. The wrapper is imported as
``merge_readiness_with_phase_ledger`` after prepending the
``scripts/local`` directory to ``sys.path`` (same pattern used
by ``test_finalize_with_phase_ledger.py`` and
``test_aed_final_gate.py``).
"""

import argparse
import io
import subprocess
import sys
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Module under test
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "local"))
import finalize_with_phase_ledger  # noqa: E402
import merge_readiness_with_phase_ledger as m  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_args(
    *,
    repo: str = "Slideshow11/Automated-Edge-Discovery",
    repo_root: str = "/tmp/repo",
    pr_number: int = 393,
    timeout_minutes: int = 15,
    poll_seconds: int = 30,
    ignore_users: str = None,
    output_json: str = "/tmp/merge_status.json",
    output_md: str = "/tmp/merge_status.md",
    run_summary: str = None,
    expected_head_sha: str = None,
    allowed_files: str = None,
    local_validation_path: str = None,
    codex_artifact_path: str = None,
    phase_gate_output_json: str = None,
    phase_gate_output_md: str = None,
    allow_codex_skip: bool = False,
    require_persistent_guard: bool = False,
    persistent_guard_root: str = "/home/max/.hermes",
    persistent_guard_snapshot: str = None,
    persistent_guard_compare_json: str = None,
    persistent_guard_compare_md: str = None,
) -> argparse.Namespace:
    """Build an ``argparse.Namespace`` matching the wrapper's expected shape.

    Defaults are set so that ``run_summary=None`` (the default-off path).
    Tests that exercise the opt-in path set ``run_summary`` and the
    six required phase-gate args.
    """
    return argparse.Namespace(
        repo=repo,
        repo_root=repo_root,
        pr_number=pr_number,
        timeout_minutes=timeout_minutes,
        poll_seconds=poll_seconds,
        ignore_users=ignore_users,
        output_json=output_json,
        output_md=output_md,
        run_summary=run_summary,
        expected_head_sha=expected_head_sha,
        allowed_files=allowed_files,
        local_validation_path=local_validation_path,
        codex_artifact_path=codex_artifact_path,
        phase_gate_output_json=phase_gate_output_json,
        phase_gate_output_md=phase_gate_output_md,
        allow_codex_skip=allow_codex_skip,
        require_persistent_guard=require_persistent_guard,
        persistent_guard_root=persistent_guard_root,
        persistent_guard_snapshot=persistent_guard_snapshot,
        persistent_guard_compare_json=persistent_guard_compare_json,
        persistent_guard_compare_md=persistent_guard_compare_md,
    )


def _opt_in_args(**overrides) -> argparse.Namespace:
    """Build args for the opt-in path (run_summary + all 6 required)."""
    base = dict(
        run_summary="/tmp/run_summary.json",
        expected_head_sha="7f7cb30a636036158ceaae32e30bb492bc221ebf",
        allowed_files="scripts/**,tests/**",
        local_validation_path="/tmp/validation.json",
        codex_artifact_path="/tmp/codex.md",
        phase_gate_output_json="/tmp/FINAL_GATE.json",
        phase_gate_output_md="/tmp/FINAL_GATE.md",
    )
    base.update(overrides)
    return _base_args(**base)


def _mock_run_finalize(monkeypatch, return_value: int) -> MagicMock:
    """Replace ``finalize_with_phase_ledger.run_finalize`` with a MagicMock."""
    mock = MagicMock(return_value=return_value)
    monkeypatch.setattr(finalize_with_phase_ledger, "run_finalize", mock)
    return mock


def _mock_subprocess_run(monkeypatch, returncode: int = 0) -> MagicMock:
    """Replace ``subprocess.run`` with a MagicMock returning ``returncode``."""
    mock = MagicMock(return_value=subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout="", stderr="",
    ))
    monkeypatch.setattr(m.subprocess, "run", mock)
    return mock


# ---------------------------------------------------------------------------
# 1. Default-off: no --run-summary skips the phase gate.
# ---------------------------------------------------------------------------


def test_no_run_summary_skips_phase_ledger_gate(monkeypatch, tmp_path):
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    mock_sub = _mock_subprocess_run(monkeypatch, returncode=0)

    args = _base_args(
        run_summary=None,
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        rc = m.run_wrapper(args)

    # Phase gate was NOT called.
    assert mock_gate.call_count == 0
    # merge_pr_safely subprocess was called once.
    assert mock_sub.call_count == 1
    # Exit code equals subprocess return code.
    assert rc == 0
    # A clear stderr note was emitted.
    assert "phase-ledger gate skipped" in captured_err.getvalue()


# ---------------------------------------------------------------------------
# 2. Opt-in: pass-through when phase gate returns 0.
# ---------------------------------------------------------------------------


def test_run_summary_pass_proceeds_to_merge_pr_safely(monkeypatch, tmp_path):
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    mock_sub = _mock_subprocess_run(monkeypatch, returncode=0)

    args = _opt_in_args(
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    # Both called exactly once.
    assert mock_gate.call_count == 1
    assert mock_sub.call_count == 1
    # Exit code 0.
    assert rc == 0


# ---------------------------------------------------------------------------
# 3. Opt-in: HOLD from phase gate blocks merge_pr_safely.
# ---------------------------------------------------------------------------


def test_run_summary_hold_blocks_merge_pr_safely(monkeypatch, tmp_path):
    mock_gate = _mock_run_finalize(monkeypatch, return_value=1)
    mock_sub = _mock_subprocess_run(monkeypatch, returncode=0)

    args = _opt_in_args(
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        rc = m.run_wrapper(args)

    # Phase gate called; subprocess NOT called.
    assert mock_gate.call_count == 1
    assert mock_sub.call_count == 0
    # Exit code is the gate's 1.
    assert rc == 1
    # Clear stderr note.
    err = captured_err.getvalue()
    assert "blocked merge-readiness" in err
    assert "merge_pr_safely not invoked" in err


# ---------------------------------------------------------------------------
# 4. Opt-in: ERROR from phase gate blocks merge_pr_safely.
# ---------------------------------------------------------------------------


def test_run_summary_error_blocks_merge_pr_safely(monkeypatch, tmp_path):
    mock_gate = _mock_run_finalize(monkeypatch, return_value=2)
    mock_sub = _mock_subprocess_run(monkeypatch, returncode=0)

    args = _opt_in_args(
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        rc = m.run_wrapper(args)

    assert mock_gate.call_count == 1
    assert mock_sub.call_count == 0
    assert rc == 2
    assert "blocked merge-readiness" in captured_err.getvalue()


# ---------------------------------------------------------------------------
# 5. Opt-in: missing required phase-gate args → exit 2, no calls.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_field",
    [
        "expected_head_sha",
        "allowed_files",
        "local_validation_path",
        "codex_artifact_path",
        "phase_gate_output_json",
        "phase_gate_output_md",
    ],
    ids=[
        "missing_expected_head_sha",
        "missing_allowed_files",
        "missing_local_validation_path",
        "missing_codex_artifact_path",
        "missing_phase_gate_output_json",
        "missing_phase_gate_output_md",
    ],
)
def test_missing_required_phase_gate_args_exits_2(
    monkeypatch, tmp_path, missing_field
):
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    mock_sub = _mock_subprocess_run(monkeypatch, returncode=0)

    # Build opt-in args, then set one required field to None.
    overrides = {missing_field: None}
    args = _opt_in_args(
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
        **overrides,
    )
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        rc = m.run_wrapper(args)

    assert rc == 2
    assert mock_gate.call_count == 0
    assert mock_sub.call_count == 0
    assert "missing or empty" in captured_err.getvalue()
    assert missing_field.replace("_", "-") in captured_err.getvalue()


# ---------------------------------------------------------------------------
# 6. The real operator-supplied --expected-head-sha is passed unchanged.
#    No dummy SHA is used.
# ---------------------------------------------------------------------------


def test_real_expected_head_sha_passed_to_finalize(monkeypatch, tmp_path):
    real_sha = "abcdef1234567890abcdef1234567890abcdef12"
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    mock_sub = _mock_subprocess_run(monkeypatch, returncode=0)

    args = _opt_in_args(
        expected_head_sha=real_sha,
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    assert rc == 0
    assert mock_gate.call_count == 1
    ns = mock_gate.call_args.args[0]
    assert ns.expected_head_sha == real_sha
    # The forbidden default/dummy values must NOT appear.
    forbidden_dummies = [
        "0000000000000000000000000000000000000000",
        "deadbeef",
        "0" * 40,
    ]
    for dummy in forbidden_dummies:
        assert ns.expected_head_sha != dummy


# ---------------------------------------------------------------------------
# 7. --allowed-files is forwarded to the phase-gate namespace and is NOT
#    added to the merge_pr_safely subprocess (since merge_pr_safely
#    does not support that arg).
# ---------------------------------------------------------------------------


def test_allowed_files_passed_to_finalize_not_merge_pr_safely(monkeypatch, tmp_path):
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    mock_sub = _mock_subprocess_run(monkeypatch, returncode=0)

    args = _opt_in_args(
        allowed_files="scripts/**,tests/**,docs/**",
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    assert rc == 0
    # Captured finalize namespace has the exact value.
    ns = mock_gate.call_args.args[0]
    assert ns.allowed_files == "scripts/**,tests/**,docs/**"
    # The merge_pr_safely subprocess command does NOT contain
    # --allowed-files (or any wrapper-only arg).
    cmd = mock_sub.call_args.args[0]
    joined = " ".join(cmd)
    assert "--allowed-files" not in joined
    assert "--run-summary" not in joined
    assert "--expected-head-sha" not in joined
    assert "--local-validation-path" not in joined
    assert "--codex-artifact-path" not in joined
    assert "--phase-gate-output" not in joined


# ---------------------------------------------------------------------------
# 8. --phase-gate-output-json and --phase-gate-output-md are mapped
#    to output_json/output_md on the finalize namespace.
# ---------------------------------------------------------------------------


def test_phase_gate_output_paths_passed_to_finalize(monkeypatch, tmp_path):
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    mock_sub = _mock_subprocess_run(monkeypatch, returncode=0)

    args = _opt_in_args(
        phase_gate_output_json="/tmp/some/FINAL_GATE.json",
        phase_gate_output_md="/tmp/some/FINAL_GATE.md",
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    assert rc == 0
    ns = mock_gate.call_args.args[0]
    # The phase-gate output paths are mapped to output_json/output_md
    # on the finalize namespace (not the wrapper-level output_json
    # which is for merge_pr_safely).
    assert ns.output_json == "/tmp/some/FINAL_GATE.json"
    assert ns.output_md == "/tmp/some/FINAL_GATE.md"


# ---------------------------------------------------------------------------
# 9. --allow-admin is hard-rejected (argparse + defense-in-depth).
# ---------------------------------------------------------------------------


def test_admin_flag_hard_rejected(monkeypatch, tmp_path):
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    mock_sub = _mock_subprocess_run(monkeypatch, returncode=0)

    # 9a. argparse refuses --allow-admin at the CLI level.
    parser = m._build_parser()
    argv = [
        "--repo", "r", "--repo-root", "/rr", "--pr-number", "393",
        "--output-json", str(tmp_path / "out.json"),
        "--allow-admin",  # MUST be rejected
    ]
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(argv)
    assert exc.value.code == 2
    assert mock_gate.call_count == 0
    assert mock_sub.call_count == 0

    # 9b. If a caller shims allow_admin=True onto the namespace,
    # the wrapper's _reject_admin guard fires.
    args = _base_args(
        run_summary=None,
        output_json=str(tmp_path / "out2.json"),
    )
    args.allow_admin = True
    with pytest.raises(SystemExit) as exc:
        m.run_wrapper(args)
    assert exc.value.code == 2
    assert mock_gate.call_count == 0
    assert mock_sub.call_count == 0


# ---------------------------------------------------------------------------
# 10. merge_pr_safely exit code is propagated after a phase-gate pass.
# ---------------------------------------------------------------------------


def test_merge_pr_safely_exit_code_propagates_after_phase_gate_pass(
    monkeypatch, tmp_path
):
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    mock_sub = _mock_subprocess_run(monkeypatch, returncode=1)

    args = _opt_in_args(
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    assert rc == 1
    assert mock_gate.call_count == 1
    assert mock_sub.call_count == 1


# ---------------------------------------------------------------------------
# 11. The merge_pr_safely subprocess command uses python and the real
#     script path, and does not include --admin, --auto, gh pr merge,
#     or git push.
# ---------------------------------------------------------------------------


def test_merge_pr_safely_command_uses_python_and_script_path(monkeypatch, tmp_path):
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    mock_sub = _mock_subprocess_run(monkeypatch, returncode=0)

    args = _opt_in_args(
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    assert rc == 0
    cmd = mock_sub.call_args.args[0]
    # First two elements: [python_executable, scripts/local/merge_pr_safely.py]
    assert cmd[0] == sys.executable
    assert cmd[1].endswith("merge_pr_safely.py")
    assert "scripts/local/" in cmd[1]
    # No forbidden patterns in the constructed command.
    joined = " ".join(cmd)
    assert "--admin" not in joined
    assert "--auto" not in joined
    assert "gh pr merge" not in joined
    assert "git push" not in joined


# ---------------------------------------------------------------------------
# 12. The module's forbidden-executable-call self-check is enforced.
# ---------------------------------------------------------------------------


def test_no_forbidden_patterns_live_in_source():
    """Direct unit test of the self-check helper.

    The check is also enforced at import time; if the helper has
    a regression, importing the module would raise. This test
    makes the contract explicit: passing the module's own source
    to the self-check must return an empty list (all forbidden
    patterns live in docstrings / comments / the constant tuple,
    which the check correctly skips).
    """
    src = Path(m.__file__).read_text(encoding="utf-8")
    violations = m._forbidden_self_check(src)
    assert violations == [], (
        f"self-check found live forbidden patterns: {violations}"
    )


# ---------------------------------------------------------------------------
# 13. Optional: end-to-end style test that confirms the default-off
#     branch prints the right stderr note (mirrors test 1's check
#     but in isolation).
# ---------------------------------------------------------------------------


def test_default_off_stderr_note_is_clear(monkeypatch, tmp_path):
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    mock_sub = _mock_subprocess_run(monkeypatch, returncode=0)

    args = _base_args(
        run_summary=None,
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        m.run_wrapper(args)
    err = captured_err.getvalue()
    assert "no --run-summary provided" in err
    assert "phase-ledger gate skipped" in err


# ---------------------------------------------------------------------------
# 14. Sanity: the module imports cleanly (smoke test for the
#     import-time self-check).
# ---------------------------------------------------------------------------


def test_module_imports_cleanly():
    """The module's import-time self-check must not raise."""
    # If we got here, the import succeeded — the self-check
    # already passed at import. Re-invoke it explicitly.
    src = Path(m.__file__).read_text(encoding="utf-8")
    assert m._forbidden_self_check(src) == []
