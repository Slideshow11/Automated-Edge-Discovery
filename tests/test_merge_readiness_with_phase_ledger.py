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
from typing import Optional
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
    """Replace ``subprocess.run`` with a MagicMock returning ``returncode``.

    Used by tests that exercise the default-off path (no
    subprocess.run for gh pr view) or that only have the phase gate
    fail (gate returns non-zero so subprocess.run is not called at
    all). For tests that exercise the opt-in path with a successful
    phase gate, use ``_mock_subprocess_dual`` instead.
    """
    mock = MagicMock(return_value=subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout="", stderr="",
    ))
    monkeypatch.setattr(m.subprocess, "run", mock)
    return mock


def _mock_subprocess_dual(
    monkeypatch,
    *,
    gh_stdout: str = "",
    gh_rc: int = 0,
    merge_rc: int = 0,
    report_path: Optional[str] = None,
    report_head_sha: Optional[str] = None,
) -> MagicMock:
    """Mock ``subprocess.run`` for the opt-in path with a successful
    phase gate: first call is the read-only ``gh pr view`` recheck;
    second call is ``merge_pr_safely.py``.

    If ``report_path`` and ``report_head_sha`` are both provided AND
    ``merge_rc == 0``, the helper writes a minimal but well-formed
    merge-readiness JSON report at ``report_path`` with
    ``head_sha == report_head_sha`` before returning the second
    CompletedProcess. This simulates the file that a real
    ``merge_pr_safely.py`` run would produce, and lets the
    wrapper's post-success head-binding check pass.

    If ``merge_rc != 0`` the helper deliberately does NOT write a
    report — that mirrors the real behavior where a failed
    ``merge_pr_safely`` may not write a complete report, and the
    wrapper's contract is to propagate the non-zero exit code
    unchanged without invoking the head-binding check.

    Returns a single MagicMock whose ``side_effect`` is a list of
    two CompletedProcess responses. Tests that exercise this path
    should use this helper instead of ``_mock_subprocess_run``.
    """
    import json

    def _maybe_write_report():
        if merge_rc != 0:
            return
        if not report_path or report_head_sha is None:
            return
        # Minimal report shape that satisfies ``_extract_report_head_sha``
        # via the explicit ``head_sha`` field. ``safe_merge_command_text``
        # is included as a defensive fallback in case a future revision
        # of the wrapper prefers the embedded SHA over the explicit field.
        report = {
            "head_sha": report_head_sha,
            "safe_merge_command_text": "",
            "safe_merge_command_list": [],
        }
        rp = Path(report_path)
        if rp.parent and str(rp.parent) not in ("", "."):
            rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report), encoding="utf-8")

    # Use a callable side_effect so we can run the report-write
    # side effect at the moment the second subprocess call returns.
    call_state = {"n": 0}

    def _side_effect(*call_args, **call_kwargs):
        call_state["n"] += 1
        if call_state["n"] == 1:
            return subprocess.CompletedProcess(
                args=call_args[0] if call_args else [],
                returncode=gh_rc, stdout=gh_stdout, stderr="",
            )
        # Second call: merge_pr_safely.py. Write the report file
        # BEFORE returning the CompletedProcess so the wrapper's
        # post-success head-binding check sees it on disk.
        _maybe_write_report()
        return subprocess.CompletedProcess(
            args=call_args[0] if call_args else [],
            returncode=merge_rc, stdout="", stderr="",
        )

    mock = MagicMock(side_effect=_side_effect)
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
    # Two-call mock: first (gh pr view) returns the expected SHA
    # with rc=0; second (merge_pr_safely.py) returns rc=0. The
    # helper also writes a valid report file at ``report_path``
    # so the wrapper's post-success head-binding check sees a
    # matching head SHA.
    report_path = str(tmp_path / "out.json")
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout="7f7cb30a636036158ceaae32e30bb492bc221ebf",
        gh_rc=0,
        merge_rc=0,
        report_path=report_path,
        report_head_sha="7f7cb30a636036158ceaae32e30bb492bc221ebf",
    )

    args = _opt_in_args(
        output_json=report_path,
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    # Both called exactly once.
    assert mock_gate.call_count == 1
    assert mock_sub.call_count == 2
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
    # Two-call mock: gh pr view returns the same real SHA so the
    # wrapper proceeds to merge_pr_safely. The helper also writes
    # a valid report file with head_sha == real_sha so the
    # post-success head-binding check passes.
    report_path = str(tmp_path / "out.json")
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout=real_sha,
        gh_rc=0,
        merge_rc=0,
        report_path=report_path,
        report_head_sha=real_sha,
    )

    args = _opt_in_args(
        expected_head_sha=real_sha,
        output_json=report_path,
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
    # Two-call mock: gh pr view returns the expected SHA so the
    # wrapper proceeds to merge_pr_safely. The helper also writes
    # a valid report file so the post-success head-binding check
    # passes.
    report_path = str(tmp_path / "out.json")
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout="7f7cb30a636036158ceaae32e30bb492bc221ebf",
        gh_rc=0,
        merge_rc=0,
        report_path=report_path,
        report_head_sha="7f7cb30a636036158ceaae32e30bb492bc221ebf",
    )

    args = _opt_in_args(
        allowed_files="scripts/**,tests/**,docs/**",
        output_json=report_path,
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    assert rc == 0
    # Captured finalize namespace has the exact value.
    ns = mock_gate.call_args.args[0]
    assert ns.allowed_files == "scripts/**,tests/**,docs/**"
    # The merge_pr_safely subprocess command does NOT contain
    # --allowed-files (or any wrapper-only arg). The second
    # subprocess.run call (index 1) is the merge_pr_safely call.
    cmd = mock_sub.call_args_list[1].args[0]
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
    # Two-call mock: gh pr view returns the expected SHA so the
    # wrapper proceeds to merge_pr_safely. The helper also writes
    # a valid report file so the post-success head-binding check
    # passes.
    report_path = str(tmp_path / "out.json")
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout="7f7cb30a636036158ceaae32e30bb492bc221ebf",
        gh_rc=0,
        merge_rc=0,
        report_path=report_path,
        report_head_sha="7f7cb30a636036158ceaae32e30bb492bc221ebf",
    )

    args = _opt_in_args(
        phase_gate_output_json="/tmp/some/FINAL_GATE.json",
        phase_gate_output_md="/tmp/some/FINAL_GATE.md",
        output_json=report_path,
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
    # Two-call mock: gh pr view returns the expected SHA
    # (rc=0); merge_pr_safely returns rc=1.
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout="7f7cb30a636036158ceaae32e30bb492bc221ebf",
        gh_rc=0,
        merge_rc=1,
    )

    args = _opt_in_args(
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    assert rc == 1
    assert mock_gate.call_count == 1
    assert mock_sub.call_count == 2


# ---------------------------------------------------------------------------
# 11. The merge_pr_safely subprocess command uses python and the real
#     script path, and does not include --admin, --auto, gh pr merge,
#     or git push.
# ---------------------------------------------------------------------------


def test_merge_pr_safely_command_uses_python_and_script_path(monkeypatch, tmp_path):
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    # Two-call mock: gh pr view returns the expected SHA so the
    # wrapper proceeds to merge_pr_safely. The helper also writes
    # a valid report file so the post-success head-binding check
    # passes.
    report_path = str(tmp_path / "out.json")
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout="7f7cb30a636036158ceaae32e30bb492bc221ebf",
        gh_rc=0,
        merge_rc=0,
        report_path=report_path,
        report_head_sha="7f7cb30a636036158ceaae32e30bb492bc221ebf",
    )

    args = _opt_in_args(
        output_json=report_path,
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    assert rc == 0
    # The merge_pr_safely subprocess command is the SECOND
    # subprocess.run call (index 1).
    cmd = mock_sub.call_args_list[1].args[0]
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


# ---------------------------------------------------------------------------
# P1 REGRESSION GUARDS (PR #393 — Codex inline comment id 3370199372):
# The wrapper must re-fetch the live PR head after a successful phase
# gate, and must NOT call merge_pr_safely.py if the live head differs
# from args.expected_head_sha, or if the recheck itself fails.
# ---------------------------------------------------------------------------


def test_head_match_proceeds_to_merge_pr_safely_after_phase_gate(
    monkeypatch, tmp_path
):
    """After a successful phase gate, when the live PR head matches
    args.expected_head_sha, the wrapper proceeds to invoke
    merge_pr_safely.py with rc=0 → wrapper exit code 0.
    """
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    # Two-call mock: gh pr view returns the expected SHA
    # with rc=0; merge_pr_safely.py returns rc=0. The helper
    # also writes a valid report file so the post-success
    # head-binding check passes.
    report_path = str(tmp_path / "out.json")
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout="7f7cb30a636036158ceaae32e30bb492bc221ebf",
        gh_rc=0,
        merge_rc=0,
        report_path=report_path,
        report_head_sha="7f7cb30a636036158ceaae32e30bb492bc221ebf",
    )

    args = _opt_in_args(
        output_json=report_path,
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    assert rc == 0
    # Phase gate called once; two subprocess calls (gh pr view + merge_pr_safely).
    assert mock_gate.call_count == 1
    assert mock_sub.call_count == 2


def test_hold_head_changed_blocks_merge_pr_safely(monkeypatch, tmp_path):
    """If gh pr view returns a head different from args.expected_head_sha
    AFTER the phase gate has passed, the wrapper must NOT invoke
    merge_pr_safely.py. It must print HOLD_HEAD_CHANGED to stderr
    and exit 1.
    """
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    # Two-call mock: gh pr view returns a DIFFERENT SHA
    # (rc=0); merge_pr_safely should never be called.
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout="1111111111111111111111111111111111111111",  # different from expected
        gh_rc=0,
        merge_rc=0,  # would be ignored; merge_pr_safely is not called
    )

    args = _opt_in_args(
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        rc = m.run_wrapper(args)

    assert rc == 1
    # Phase gate called once; only the gh pr view subprocess ran
    # (merge_pr_safely was NOT called).
    assert mock_gate.call_count == 1
    assert mock_sub.call_count == 1  # only the gh call
    err = captured_err.getvalue()
    assert "HOLD_HEAD_CHANGED" in err
    assert "7f7cb30a636036158ceaae32e30bb492bc221ebf" in err
    assert "1111111111111111111111111111111111111111" in err
    assert "merge_pr_safely not invoked" in err


def test_head_recheck_failure_exits_2_and_blocks_merge_pr_safely(
    monkeypatch, tmp_path
):
    """If gh pr view fails (non-zero exit or empty stdout), the
    wrapper must treat it as a hard error: exit 2 and do NOT
    invoke merge_pr_safely.py.
    """
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    # Two-call mock: gh pr view fails (rc=1); merge_pr_safely
    # would be ignored but the wrapper must not reach it.
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout="",
        gh_rc=1,
        merge_rc=0,
    )

    args = _opt_in_args(
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        rc = m.run_wrapper(args)

    assert rc == 2
    assert mock_gate.call_count == 1
    assert mock_sub.call_count == 1  # only the failed gh call
    err = captured_err.getvalue()
    assert "unable to recheck PR head" in err
    assert "merge_pr_safely not invoked" in err


def test_head_recheck_failure_with_empty_stdout_exits_2(
    monkeypatch, tmp_path
):
    """If gh pr view returns rc=0 but with empty stdout (a partial
    failure mode), the wrapper must still treat it as a failure
    and exit 2.
    """
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout="",  # empty
        gh_rc=0,        # rc=0 (deceptive — empty output is a failure)
        merge_rc=0,
    )

    args = _opt_in_args(
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    assert rc == 2
    assert mock_sub.call_count == 1


def test_head_recheck_failure_with_malformed_sha_exits_2(
    monkeypatch, tmp_path
):
    """If gh pr view returns a non-SHA value (e.g. an error message
    that happened to be on stdout with rc=0), the wrapper must
    reject it and exit 2.
    """
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout="not a sha",  # malformed
        gh_rc=0,
        merge_rc=0,
    )

    args = _opt_in_args(
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    assert rc == 2
    assert mock_sub.call_count == 1


def test_no_run_summary_does_not_fetch_head(monkeypatch, tmp_path):
    """In the default-off path (no --run-summary), the wrapper must
    NOT make a gh pr view call. It only delegates directly to
    merge_pr_safely.py.
    """
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    mock_sub = _mock_subprocess_run(monkeypatch, returncode=0)

    args = _base_args(
        run_summary=None,
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    assert rc == 0
    # No phase gate, no gh pr view — just one merge_pr_safely call.
    assert mock_gate.call_count == 0
    assert mock_sub.call_count == 1
    # Confirm the single subprocess call is merge_pr_safely, not gh.
    cmd = mock_sub.call_args.args[0]
    assert "gh" not in cmd or "merge_pr_safely.py" in str(cmd)


def test_head_recheck_uses_read_only_gh_pr_view(monkeypatch, tmp_path):
    """The head-recheck subprocess command must be a read-only
    ``gh pr view --json headRefOid --jq .headRefOid`` invocation.
    It must NOT include any mutating gh subcommand, --admin, or
    --auto.
    """
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    # Two-call mock: gh pr view returns the expected SHA so the
    # wrapper proceeds to merge_pr_safely. The helper also writes
    # a valid report file so the post-success head-binding check
    # passes.
    report_path = str(tmp_path / "out.json")
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout="7f7cb30a636036158ceaae32e30bb492bc221ebf",
        gh_rc=0,
        merge_rc=0,
        report_path=report_path,
        report_head_sha="7f7cb30a636036158ceaae32e30bb492bc221ebf",
    )

    args = _opt_in_args(
        repo="Slideshow11/Automated-Edge-Discovery",
        pr_number=393,
        output_json=report_path,
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    assert rc == 0
    # The FIRST subprocess.run call (index 0) is the gh pr view.
    cmd = mock_sub.call_args_list[0].args[0]
    assert "gh" in cmd
    assert "pr" in cmd
    assert "view" in cmd
    assert "393" in cmd
    assert "--repo" in cmd
    assert "Slideshow11/Automated-Edge-Discovery" in cmd
    assert "--json" in cmd
    assert "headRefOid" in cmd
    assert "--jq" in cmd
    assert ".headRefOid" in cmd
    # Negative assertions: no mutating flags, no admin/auto.
    joined = " ".join(cmd)
    assert "merge" not in joined  # no "gh pr merge"
    assert "create" not in joined  # no "gh pr create"
    assert "edit" not in joined   # no "gh pr edit"
    assert "delete" not in joined  # no "gh pr delete" / branch delete
    assert "--admin" not in joined
    assert "--auto" not in joined


def test_expected_head_sha_used_for_comparison_after_gate(
    monkeypatch, tmp_path
):
    """Two scenarios in one test: (a) when gh returns the same
    SHA, merge_pr_safely proceeds; (b) when gh returns a
    different SHA, merge_pr_safely is blocked.
    """
    expected_sha = "abcdef1234567890abcdef1234567890abcdef12"

    # ---- Sub-scenario (a): head matches ----
    expected_sha_a = expected_sha
    report_path_a = str(tmp_path / "a.json")
    mock_gate_a = _mock_run_finalize(monkeypatch, return_value=0)
    mock_sub_a = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout=expected_sha_a,  # matches
        gh_rc=0,
        merge_rc=0,
        report_path=report_path_a,
        report_head_sha=expected_sha_a,
    )
    args_a = _opt_in_args(
        expected_head_sha=expected_sha_a,
        output_json=report_path_a,
        output_md=str(tmp_path / "a.md"),
    )
    rc_a = m.run_wrapper(args_a)
    assert rc_a == 0
    assert mock_sub_a.call_count == 2  # gh + merge_pr_safely

    # ---- Sub-scenario (b): head differs ----
    # Re-mock for the second sub-scenario.
    mock_gate_b = _mock_run_finalize(monkeypatch, return_value=0)
    mock_sub_b = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout="ffffffffffffffffffffffffffffffffffffffff",  # different
        gh_rc=0,
        merge_rc=0,
    )
    args_b = _opt_in_args(
        expected_head_sha=expected_sha,
        output_json=str(tmp_path / "b.json"),
        output_md=str(tmp_path / "b.md"),
    )
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        rc_b = m.run_wrapper(args_b)
    assert rc_b == 1
    assert mock_sub_b.call_count == 1  # only gh, no merge_pr_safely
    assert "HOLD_HEAD_CHANGED" in captured_err.getvalue()


# ---------------------------------------------------------------------------
# POST-SUCCESS HEAD-BINDING (PR #393 — Codex follow-up inline comment
# id 3370258789, thread PRRT_kwDOSHFpYM6HskHa):
# After a successful merge_pr_safely run, the wrapper must verify that
# the report written to args.output_json records the same head SHA the
# phase-ledger gate validated. This closes the residual TOCTOU window
# between merge_pr_safely's internal gh pr view fetch and the wrapper
# returning.
# ---------------------------------------------------------------------------


def test_report_head_matches_expected_propagates_success(
    monkeypatch, tmp_path
):
    """When merge_pr_safely writes a report whose recorded head SHA
    equals args.expected_head_sha, the wrapper returns 0.
    """
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    report_path = str(tmp_path / "out.json")
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout="7f7cb30a636036158ceaae32e30bb492bc221ebf",
        gh_rc=0,
        merge_rc=0,
        report_path=report_path,
        report_head_sha="7f7cb30a636036158ceaae32e30bb492bc221ebf",
    )

    args = _opt_in_args(
        output_json=report_path,
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    assert rc == 0
    assert mock_gate.call_count == 1
    assert mock_sub.call_count == 2


def test_report_head_mismatch_exits_1(monkeypatch, tmp_path):
    """When merge_pr_safely writes a report with a head SHA different
    from args.expected_head_sha, the wrapper returns 1 and prints
    HEAD_MISMATCH_AFTER_MERGE_READINESS to stderr.
    """
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    # Pre-delegation recheck passes (head matches), but the report
    # written by merge_pr_safely shows a different SHA — simulating
    # a commit landing between merge_pr_safely's internal fetch and
    # the wrapper's post-success verification.
    expected = "7f7cb30a636036158ceaae32e30bb492bc221ebf"
    report_path = str(tmp_path / "out.json")
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout=expected,  # pre-recheck passes
        gh_rc=0,
        merge_rc=0,
        report_path=report_path,
        report_head_sha="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",  # different
    )

    args = _opt_in_args(
        expected_head_sha=expected,
        output_json=report_path,
        output_md=str(tmp_path / "out.md"),
    )
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        rc = m.run_wrapper(args)

    assert rc == 1
    err = captured_err.getvalue()
    assert "HEAD_MISMATCH_AFTER_MERGE_READINESS" in err
    assert expected in err
    assert "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef" in err
    assert mock_gate.call_count == 1
    assert mock_sub.call_count == 2


def test_report_missing_head_exits_2(monkeypatch, tmp_path):
    """When merge_pr_safely returns 0 but the report lacks any usable
    head SHA, the wrapper returns 2 and prints the unable-to-verify
    error to stderr.
    """
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    # Build a custom side_effect so the second call writes a
    # report with NO head_sha field and NO --match-head-commit.
    expected = "7f7cb30a636036158ceaae32e30bb492bc221ebf"
    report_path = str(tmp_path / "out.json")
    import json as _json

    call_state = {"n": 0}

    def _side_effect(*call_args, **call_kwargs):
        call_state["n"] += 1
        if call_state["n"] == 1:
            return subprocess.CompletedProcess(
                args=call_args[0] if call_args else [],
                returncode=0,
                stdout=expected,
                stderr="",
            )
        # Second call: write a report with no usable head SHA.
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(
            _json.dumps({"some_other_field": "irrelevant"}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            args=call_args[0] if call_args else [],
            returncode=0, stdout="", stderr="",
        )

    mock_sub = MagicMock(side_effect=_side_effect)
    monkeypatch.setattr(m.subprocess, "run", mock_sub)

    args = _opt_in_args(
        expected_head_sha=expected,
        output_json=report_path,
        output_md=str(tmp_path / "out.md"),
    )
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        rc = m.run_wrapper(args)

    assert rc == 2
    err = captured_err.getvalue()
    assert "unable to verify" in err
    assert "merge-readiness report head" in err
    assert mock_gate.call_count == 1
    assert mock_sub.call_count == 2


def test_report_malformed_json_exits_2(monkeypatch, tmp_path):
    """When merge_pr_safely returns 0 but the report file is invalid
    JSON, the wrapper returns 2 and the merge_pr_safely subprocess
    was indeed called (this is a post-success failure mode).
    """
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    expected = "7f7cb30a636036158ceaae32e30bb492bc221ebf"
    report_path = str(tmp_path / "out.json")

    call_state = {"n": 0}

    def _side_effect(*call_args, **call_kwargs):
        call_state["n"] += 1
        if call_state["n"] == 1:
            return subprocess.CompletedProcess(
                args=call_args[0] if call_args else [],
                returncode=0,
                stdout=expected,
                stderr="",
            )
        # Second call: write malformed JSON to the report.
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text("this is not { valid json", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=call_args[0] if call_args else [],
            returncode=0, stdout="", stderr="",
        )

    mock_sub = MagicMock(side_effect=_side_effect)
    monkeypatch.setattr(m.subprocess, "run", mock_sub)

    args = _opt_in_args(
        expected_head_sha=expected,
        output_json=report_path,
        output_md=str(tmp_path / "out.md"),
    )
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        rc = m.run_wrapper(args)

    assert rc == 2
    err = captured_err.getvalue()
    assert "unable to verify" in err
    # merge_pr_safely was indeed called.
    assert mock_sub.call_count == 2
    # No HEAD_MISMATCH_AFTER_MERGE_READINESS — this is a different
    # failure mode (parse failure, not head mismatch).
    assert "HEAD_MISMATCH_AFTER_MERGE_READINESS" not in err


def test_report_head_not_checked_when_merge_pr_safely_fails(
    monkeypatch, tmp_path
):
    """When merge_pr_safely returns non-zero, the wrapper must
    propagate that exit code unchanged. The post-success
    head-binding check must NOT run (the report may be missing
    or partial in that case).
    """
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    expected = "7f7cb30a636036158ceaae32e30bb492bc221ebf"
    report_path = str(tmp_path / "out.json")
    # merge_pr_safely returns 1; helper does NOT write a report.
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout=expected,
        gh_rc=0,
        merge_rc=1,
        report_path=report_path,
        report_head_sha=expected,  # ignored because merge_rc != 0
    )

    args = _opt_in_args(
        expected_head_sha=expected,
        output_json=report_path,
        output_md=str(tmp_path / "out.md"),
    )
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        rc = m.run_wrapper(args)

    # Wrapper returns merge_pr_safely's exit code unchanged.
    assert rc == 1
    # No post-success report-head verification error.
    err = captured_err.getvalue()
    assert "HEAD_MISMATCH_AFTER_MERGE_READINESS" not in err
    assert "unable to verify" not in err


def test_no_run_summary_does_not_verify_merge_report_head(
    monkeypatch, tmp_path
):
    """In the default-off path (no --run-summary), the wrapper must
    NOT verify the merge-readiness report head. It only delegates
    to merge_pr_safely.py. Any report state (missing, malformed,
    head-mismatched) is irrelevant.
    """
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    # Only one subprocess.run call (merge_pr_safely), returning 0.
    # The "report" path is intentionally never created.
    mock_sub = _mock_subprocess_run(monkeypatch, returncode=0)

    args = _base_args(
        run_summary=None,
        output_json=str(tmp_path / "never_written.json"),
        output_md=str(tmp_path / "out.md"),
    )
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        rc = m.run_wrapper(args)

    # Default-off: no phase gate, no head check, no report check.
    assert rc == 0
    assert mock_gate.call_count == 0
    assert mock_sub.call_count == 1
    err = captured_err.getvalue()
    assert "unable to verify" not in err
    assert "HEAD_MISMATCH_AFTER_MERGE_READINESS" not in err


def test_match_head_commit_extracted_from_merge_command_if_needed(
    monkeypatch, tmp_path
):
    """When the report has no explicit ``head_sha`` field but does
    contain a ``safe_merge_command_text`` with ``--match-head-commit
    <sha>``, the wrapper must extract the SHA from the command and
    use it for binding. This is the defensive fallback path.
    """
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    expected = "7f7cb30a636036158ceaae32e30bb492bc221ebf"
    report_path = str(tmp_path / "out.json")
    # Build a report that omits the explicit ``head_sha`` field
    # but embeds the expected SHA inside safe_merge_command_text.
    report_content = (
        "{\n"
        '  "safe_merge_command_text": "gh pr merge 393 '
        '--repo Slideshow11/Automated-Edge-Discovery --squash '
        f'--delete-branch --match-head-commit {expected}",\n'
        '  "safe_merge_command_list": []\n'
        "}\n"
    )

    call_state = {"n": 0}

    def _side_effect(*call_args, **call_kwargs):
        call_state["n"] += 1
        if call_state["n"] == 1:
            return subprocess.CompletedProcess(
                args=call_args[0] if call_args else [],
                returncode=0, stdout=expected, stderr="",
            )
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(report_content, encoding="utf-8")
        return subprocess.CompletedProcess(
            args=call_args[0] if call_args else [],
            returncode=0, stdout="", stderr="",
        )

    mock_sub = MagicMock(side_effect=_side_effect)
    monkeypatch.setattr(m.subprocess, "run", mock_sub)

    args = _opt_in_args(
        expected_head_sha=expected,
        output_json=report_path,
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    # SHA extracted from the merge command and matched expected.
    assert rc == 0
    assert mock_sub.call_count == 2


def test_report_head_binding_uses_expected_head_sha_not_live_recheck_sha(
    monkeypatch, tmp_path
):
    """The post-success head-binding check uses args.expected_head_sha
    (the operator-supplied, ledger-validated value), NOT the
    pre-delegation live recheck SHA. If the report's recorded head
    matches the live recheck SHA but NOT expected_head_sha, the
    wrapper must still block based on the expected vs. report
    comparison.
    """
    expected = "1111111111111111111111111111111111111111"
    live_recheck = "2222222222222222222222222222222222222222"
    report_head = "3333333333333333333333333333333333333333"  # matches neither

    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    report_path = str(tmp_path / "out.json")
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout=live_recheck,  # live recheck differs from expected
        gh_rc=0,
        merge_rc=0,
        report_path=report_path,
        report_head_sha=report_head,
    )

    args = _opt_in_args(
        expected_head_sha=expected,
        output_json=report_path,
        output_md=str(tmp_path / "out.md"),
    )
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        rc = m.run_wrapper(args)

    # Pre-delegation recheck catches the live vs. expected mismatch
    # first — the wrapper returns 1 with HOLD_HEAD_CHANGED before
    # it ever invokes merge_pr_safely or verifies the report.
    assert rc == 1
    err = captured_err.getvalue()
    assert "HOLD_HEAD_CHANGED" in err
    # No post-success head binding was attempted (merge_pr_safely
    # was not even called).
    assert mock_sub.call_count == 1
    assert "HEAD_MISMATCH_AFTER_MERGE_READINESS" not in err


def test_report_head_mismatch_with_match_in_live_recheck_exits_1(
    monkeypatch, tmp_path
):
    """Companion to the previous test: the pre-delegation recheck
    passes (live == expected), but the report's head differs.
    Wrapper returns 1 with HEAD_MISMATCH_AFTER_MERGE_READINESS.
    """
    expected = "1111111111111111111111111111111111111111"
    different_report_head = "2222222222222222222222222222222222222222"

    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    report_path = str(tmp_path / "out.json")
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout=expected,  # pre-recheck passes
        gh_rc=0,
        merge_rc=0,
        report_path=report_path,
        report_head_sha=different_report_head,  # different from expected
    )

    args = _opt_in_args(
        expected_head_sha=expected,
        output_json=report_path,
        output_md=str(tmp_path / "out.md"),
    )
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        rc = m.run_wrapper(args)

    assert rc == 1
    err = captured_err.getvalue()
    assert "HEAD_MISMATCH_AFTER_MERGE_READINESS" in err
    assert expected in err
    assert different_report_head in err
    # Both subprocess calls happened (gh pr view + merge_pr_safely).
    assert mock_sub.call_count == 2


# ---------------------------------------------------------------------------
# P2 REGRESSION GUARDS (PR #393 — Codex inline comment PRRC_kwDOSHFpYM7I44yF,
# thread PRRT_kwDOSHFpYM6Hs2BD):
# The wrapper's read-only ``gh pr view`` recheck must have a bounded
# timeout. If ``gh`` stalls (auth prompt, network I/O), ``subprocess.run``
# raises ``subprocess.TimeoutExpired``; the wrapper must catch it and
# take the existing "unable to recheck PR head" path (exit 2, do not
# invoke ``merge_pr_safely``).
# ---------------------------------------------------------------------------


def test_head_recheck_timeout_exits_2_and_blocks_merge_pr_safely(
    monkeypatch, tmp_path
):
    """If the ``gh pr view`` recheck raises ``subprocess.TimeoutExpired``,
    the wrapper must treat it as a failed recheck: exit 2, print the
    existing "unable to recheck PR head" stderr message, and do NOT
    invoke ``merge_pr_safely.py``.
    """
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)

    def _raise_timeout(*call_args, **call_kwargs):
        raise subprocess.TimeoutExpired(
            cmd=call_args[0] if call_args else [],
            timeout=30,
        )

    mock_sub = MagicMock(side_effect=_raise_timeout)
    monkeypatch.setattr(m.subprocess, "run", mock_sub)

    args = _opt_in_args(
        output_json=str(tmp_path / "out.json"),
        output_md=str(tmp_path / "out.md"),
    )
    captured_err = io.StringIO()
    with redirect_stderr(captured_err):
        rc = m.run_wrapper(args)

    assert rc == 2
    # Phase gate called once; only the timed-out gh call ran
    # (merge_pr_safely was NOT called).
    assert mock_gate.call_count == 1
    assert mock_sub.call_count == 1
    err = captured_err.getvalue()
    assert "unable to recheck PR head" in err
    assert "merge_pr_safely not invoked" in err


def test_head_recheck_uses_bounded_timeout(monkeypatch, tmp_path):
    """The ``subprocess.run`` call for the read-only ``gh pr view``
    recheck must pass a finite ``timeout`` kwarg. The value must be
    a positive number (we check it is in the reasonable range 1-600s).
    """
    mock_gate = _mock_run_finalize(monkeypatch, return_value=0)
    # Return a successful CompletedProcess for the gh call so the
    # wrapper proceeds; we do not care about the rest of the flow
    # here — we only need to capture the kwarg passed to subprocess.run.
    expected_sha = "7f7cb30a636036158ceaae32e30bb492bc221ebf"
    report_path = str(tmp_path / "out.json")
    mock_sub = _mock_subprocess_dual(
        monkeypatch,
        gh_stdout=expected_sha,
        gh_rc=0,
        merge_rc=0,
        report_path=report_path,
        report_head_sha=expected_sha,
    )

    args = _opt_in_args(
        output_json=report_path,
        output_md=str(tmp_path / "out.md"),
    )
    rc = m.run_wrapper(args)

    assert rc == 0
    # The FIRST subprocess.run call (index 0) is the gh pr view.
    gh_call = mock_sub.call_args_list[0]
    # The timeout may be passed as a positional arg or as a kwarg
    # depending on the Python version. Accept either form.
    passed_timeout = None
    if "timeout" in gh_call.kwargs:
        passed_timeout = gh_call.kwargs["timeout"]
    else:
        # subprocess.run(cmd, ..., timeout=N) — timeout is the
        # 6th positional arg after check, capture_output, text,
        # input, encoding (varies by version). Inspect all
        # positional args for a number.
        for a in gh_call.args[1:]:
            if isinstance(a, (int, float)) and 1 <= a <= 600:
                passed_timeout = a
                break
    assert passed_timeout is not None, (
        f"subprocess.run for gh pr view did not receive a timeout kwarg: "
        f"args={gh_call.args}, kwargs={gh_call.kwargs}"
    )
    assert 1 <= passed_timeout <= 600, (
        f"timeout value {passed_timeout} out of reasonable range"
    )
    # And specifically: the module-level constant must be 30.
    assert m.GH_PR_VIEW_TIMEOUT_SECONDS == 30
    assert passed_timeout == m.GH_PR_VIEW_TIMEOUT_SECONDS
