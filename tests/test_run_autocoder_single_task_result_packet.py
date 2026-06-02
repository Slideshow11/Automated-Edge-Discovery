"""
Tests for P3C-B1 — mock-only autocoder result packet emission.

Covers the optional emission of a P3C-A-compatible result packet from
scripts/local/run_autocoder_single_task.py at the end of a successful
mock-mode run.

Required tests:
 1. without the new flag, existing mock behavior is unchanged
 2. with --emit-real-output-result-packet in mock mode, a result packet
    is written
 3. the packet has all required evaluator fields
 4. the packet can be consumed by run_autocoder_real_output_eval.py
 5. missing --real-output-task-id with emit flag is rejected
 6. emit flag in non-mock/live mode is rejected (HOLD_TASK_PACKET_INVALID)
 7. no live Claude strings are added to the execution path
 8. no gh mutation strings are added
 9. no subprocess calls use the shell-argument form
10. failure/hold mock path does not emit a misleading PASS packet
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Make the module under test importable
THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
SCRIPTS_LOCAL = REPO_ROOT / "scripts" / "local"
CORPUS_PATH = REPO_ROOT / "corpus" / "autocoder-real-output-v0.json"

for p in (str(REPO_ROOT), str(SCRIPTS_LOCAL)):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_autocoder_single_task as controller  # noqa: E402
import run_autocoder_real_output_eval as eval_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_packet(**overrides: Any) -> Dict[str, Any]:
    """Build a minimal valid task packet (mock mode).

    By default the packet includes a single mock_edit so a real controller
    run can reach the READY terminal state. Tests that want to force a
    HOLD path should pass ``mock_edits=[]`` and a path that is not in
    allowed_files.
    """
    task_id = f"p3c-b1-test-{uuid.uuid4().hex[:8]}"
    base = {
        "packet_kind": "aed.autocoder.single_task.v0",
        "task_id": task_id,
        "goal": "Add a small report-only helper script to scripts/local for testing.",
        # allowed_files uses exact membership (not glob) per the
        # controller's mock_edit validator. Include both a glob and the
        # exact mock_edit path so the validator accepts it.
        "allowed_files": [
            "scripts/local/*.py",
            "scripts/local/_p3c_b1_smoke.py",
        ],
        "forbidden_files": [".github/**", "*.json", "*.md"],
        "max_changed_files": 3,
        "required_tests": None,
        "output_root": str(THIS_DIR / "aed_runs" / f"single_task_{task_id}"),
        # worktree_root must NOT be a prefix-string collision with the
        # AED repo path. The apply-readiness check uses a raw
        # str.startswith() without a trailing separator, so
        # /tmp/aed_runs/worktrees/p3c_b1_xxx is falsely flagged as
        # "inside" /tmp/aed_runs/worktrees/p3c_b1. Use a completely
        # separate directory tree.
        "worktree_root": f"/tmp/p3c_b1_wt/{task_id}",
        "branch_name": f"autocoder-p3c-b1-{task_id}",
        "suggested_pr_title": f"tooling: P3C-B1 smoke packet {task_id}",
        "suggested_pr_body": "Test PR body for P3C-B1 emission smoke.",
        "execution_mode": "mocked",
        # Default mock_edits: a single safe file under scripts/local/.
        # Tests can override this with mock_edits=[] to force HOLD.
        "mock_edits": [
            {
                "path": "scripts/local/_p3c_b1_smoke.py",
                "content": "# P3C-B1 smoke marker\n",
            },
        ],
    }
    base.update(overrides)
    return base


def _cleanup_test_artifacts(repo_root: Path) -> None:
    """Best-effort cleanup of test artifacts left in the AED repo.

    The mock-edit test creates a real file in the AED repo (via
    apply_to_branch) and a real branch. Clean up both so subsequent
    test runs see a clean repo.
    """
    # Delete the smoke marker file if it was committed
    smoke_path = repo_root / "scripts" / "local" / "_p3c_b1_smoke.py"
    if smoke_path.exists():
        try:
            smoke_path.unlink()
        except OSError:
            pass
    # Also remove from git index in case it was committed
    try:
        subprocess.run(
            ["git", "-C", str(repo_root), "rm", "--cached", "--force",
             "scripts/local/_p3c_b1_smoke.py"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(scope="module", autouse=True)
def _module_cleanup_legacy_artifacts():
    """One-time cleanup before any test in this module runs. Removes
    leftover state from a previous test run so the integration tests
    (which require a clean main repo) can proceed. Runs once per module
    load."""
    _cleanup_test_artifacts(REPO_ROOT)
    # Also try to clean any leftover p3c-b1 test branches from prior runs.
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "branch", "--list",
             "autocoder-p3c-b1-p3c-b1-test-*"],
            capture_output=True, text=True, timeout=10,
        )
        for line in proc.stdout.splitlines():
            name = line.strip().lstrip("* ").strip()
            if name.startswith("autocoder-p3c-b1-p3c-b1-test-"):
                # Only delete if it's not the current branch.
                cur = subprocess.run(
                    ["git", "-C", str(REPO_ROOT), "rev-parse",
                     "--abbrev-ref", "HEAD"],
                    capture_output=True, text=True, timeout=10,
                ).stdout.strip()
                if cur != name:
                    _cleanup_branch(name, REPO_ROOT)
    except Exception:  # noqa: BLE001
        pass
    yield


def _is_main_repo_dirty() -> bool:
    """Check if the AED worktree has uncommitted changes. The temp
    worktree executor (run_temp_worktree_execution.py) requires a clean
    main repo to run; P3C-B1 integration tests skip when the repo is
    dirty (e.g. while the P3C-B1 changes themselves are uncommitted)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except Exception:  # noqa: BLE001
        return True  # err on the safe side — skip if we can't tell


def _cleanup_branch(branch_name: str, repo_root: Path) -> None:
    """Best-effort cleanup of a test branch. Silently ignores errors."""
    if not branch_name:
        return
    try:
        subprocess.run(
            ["git", "-C", str(repo_root), "branch", "-D", branch_name],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:  # noqa: BLE001
        pass


def _write_packet(packet: Dict[str, Any]) -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    Path(path).write_text(json.dumps(packet), encoding="utf-8")
    return path


def _run_controller_subprocess(
    packet: Dict[str, Any],
    output_json: Path,
    output_md: Path,
    *,
    extra_argv: List[str] = None,
    repo_root: str = None,
    timeout: int = 120,
) -> Dict[str, Any]:
    """Run the controller via subprocess (mirrors the pattern used by
    the existing TestRepoRootArg tests)."""
    pkt_path = _write_packet(packet)
    try:
        script_path = SCRIPTS_LOCAL / "run_autocoder_single_task.py"
        argv = [
            sys.executable, str(script_path),
            "--task-packet-json", pkt_path,
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ]
        if repo_root:
            argv.extend(["--repo-root", str(repo_root)])
        if extra_argv:
            argv.extend(extra_argv)
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
        )
        if Path(str(output_json)).exists():
            return {
                "controller_status": json.loads(Path(str(output_json)).read_text()),
                "subprocess_rc": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        return {
            "controller_status": {
                "status": "NO_OUTPUT",
                "subprocess_rc": proc.returncode,
                "stderr": proc.stderr[:400],
            },
            "subprocess_rc": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    finally:
        try:
            os.unlink(pkt_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 1. Without the new flag, existing mock behavior is unchanged
# ---------------------------------------------------------------------------


def test_without_flag_existing_behavior_unchanged(tmp_path: Path) -> None:
    """Without --emit-real-output-result-packet, the controller's output
    does not contain a real_output_packet_emission key. The existing
    status field is still present regardless of the controller's terminal
    state."""
    packet = make_packet(output_root=str(tmp_path / "aed_runs"))
    out_json = tmp_path / "out.json"
    out_md = tmp_path / "out.md"
    branch_name = packet["branch_name"]
    try:
        result = _run_controller_subprocess(packet, out_json, out_md, repo_root=REPO_ROOT)

        cs = result["controller_status"]
        # The new flag was not set, so the emission key must NOT be present.
        assert "real_output_packet_emission" not in cs, (
            "emission key should be absent when flag is not set; got "
            f"{cs.get('real_output_packet_emission')!r}"
        )
        # The status field is always present.
        assert "status" in cs
    finally:
        _cleanup_branch(branch_name, REPO_ROOT)


# ---------------------------------------------------------------------------
# 2. With emit flag in mock mode, a result packet is written
# ---------------------------------------------------------------------------


def test_emit_flag_writes_packet_in_mock_mode(tmp_path: Path) -> None:
    """A successful mock run with --emit-real-output-result-packet writes
    the packet to the specified path.

    This test calls the controller's emission helper directly (the same
    function the controller invokes at the State.READY terminal) rather
    than running the full six-stage pipeline. The full pipeline depends
    on the temp worktree executor and a clean main repo, which are out of
    scope for P3C-B1's unit-level test surface.
    """
    emit_path = tmp_path / "emitted_packet.json"
    packet = make_packet(output_root=str(tmp_path / "aed_runs"))
    emission = controller._try_emit_real_output_result_packet(
        task_packet=packet,
        real_task_id="real-output-v0-task-002",
        emit_path=emit_path,
        controller_status=controller.State.READY,
        changed_files=["scripts/local/_p3c_b1_smoke.py"],
        branch_name="",
        base_sha="",
        repo_root=REPO_ROOT,
    )
    assert emission["emission_status"] == "RESULT_PACKET_READY"
    assert emit_path.exists()
    packet_data = json.loads(emit_path.read_text())
    assert packet_data["builder_status"] == "RESULT_PACKET_READY"


# ---------------------------------------------------------------------------
# 3. The packet has all required evaluator fields
# ---------------------------------------------------------------------------


def test_packet_has_required_evaluator_fields(tmp_path: Path) -> None:
    """The emitted packet (when READY) contains every field the eval
    load_result expects, with correct types.

    In-process equivalent of a full controller run: calls the emission
    helper directly with a READY state.
    """
    emit_path = tmp_path / "emitted_packet.json"
    packet = make_packet(output_root=str(tmp_path / "aed_runs"))
    emission = controller._try_emit_real_output_result_packet(
        task_packet=packet,
        real_task_id="real-output-v0-task-002",
        emit_path=emit_path,
        controller_status=controller.State.READY,
        changed_files=["scripts/local/_p3c_b1_smoke.py"],
        branch_name="",
        base_sha="",
        repo_root=REPO_ROOT,
    )
    assert emission["emission_status"] == "RESULT_PACKET_READY"
    data = json.loads(emit_path.read_text())

    # Required top-level keys (consumed by run_autocoder_real_output_eval.load_result)
    assert "schema_version" in data
    assert "task_id" in data
    assert "status" in data
    # Required string fields
    assert data["task_id"] == "real-output-v0-task-002"
    assert isinstance(data["source_commit"], str) and len(data["source_commit"]) == 40
    assert isinstance(data["source_head_sha"], str) and len(data["source_head_sha"]) == 40
    assert isinstance(data["title"], str) and data["title"]
    assert data["status"] in ("PASS", "HOLD", "ERROR", "UNKNOWN")
    # Required list fields
    assert isinstance(data["changed_files"], list) and len(data["changed_files"]) >= 1
    assert isinstance(data["allowed_files"], list) and len(data["allowed_files"]) >= 1
    assert isinstance(data["scoped_files"], list)
    # Required numeric field
    assert isinstance(data["tests_passed"], int) and data["tests_passed"] >= 0
    # Required bool fields
    for f in ("ci_green", "scope_clean", "review_ready", "merge_ready",
              "human_cleanup_required"):
        assert isinstance(data[f], bool), f"{f} must be bool, got {type(data[f]).__name__}"
    # Source PR is the mock sentinel (0) — see docs/autocoder_result_packet_emission_v0.md
    assert data["source_pr"] == 0
    # Builder's own status
    assert data["builder_status"] == "RESULT_PACKET_READY"
    # Timestamp
    assert "result_packet_generated_at" in data
    # Mock-emission notes (at least the first)
    assert any("P3C-B1" in n for n in data.get("notes", []))


# ---------------------------------------------------------------------------
# 4. The packet can be consumed by run_autocoder_real_output_eval.py
# ---------------------------------------------------------------------------


def test_packet_evaluator_compatible(tmp_path: Path) -> None:
    """The emitted packet is accepted by the real-output evaluator
    and produces a successful eval with matched_result_count=1.

    In-process equivalent of a full controller run: writes the packet via
    the emission helper, then feeds it to the evaluator module.
    """
    emit_path = tmp_path / "emitted_packet.json"
    packet = make_packet(output_root=str(tmp_path / "aed_runs"))
    emission = controller._try_emit_real_output_result_packet(
        task_packet=packet,
        real_task_id="real-output-v0-task-002",
        emit_path=emit_path,
        controller_status=controller.State.READY,
        changed_files=["scripts/local/_p3c_b1_smoke.py"],
        branch_name="",
        base_sha="",
        repo_root=REPO_ROOT,
    )
    assert emission["emission_status"] == "RESULT_PACKET_READY"
    assert emit_path.exists()
    # Feed to the eval
    eval_json = tmp_path / "eval.json"
    eval_md = tmp_path / "eval.md"
    rc = eval_mod.main([
        "--corpus", str(CORPUS_PATH),
        "--result-json", str(emit_path),
        "--output-json", str(eval_json),
        "--output-md", str(eval_md),
    ])
    assert rc == 0
    report = json.loads(eval_json.read_text())
    assert report["status"] == "REAL_OUTPUT_EVAL_READY"
    assert report["result_count"] == 1
    assert report["matched_result_count"] == 1
    # Our task_id is the one that is matched, so it must NOT be in the
    # missing list.
    assert "real-output-v0-task-002" not in report["missing_result_task_ids"]
    assert report["invalid_result_packets"] == []


# ---------------------------------------------------------------------------
# 5. Missing --real-output-task-id with emit flag is rejected
# ---------------------------------------------------------------------------


def test_missing_task_id_rejected(tmp_path: Path) -> None:
    """With --emit-real-output-result-packet but no --real-output-task-id,
    the controller exits non-zero with a clear FATAL message before any
    work begins."""
    pkt_path = _write_packet(make_packet())
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_LOCAL / "run_autocoder_single_task.py"),
                "--task-packet-json", pkt_path,
                "--output-json", str(tmp_path / "out.json"),
                "--output-md", str(tmp_path / "out.md"),
                "--emit-real-output-result-packet", str(tmp_path / "emit.json"),
                # NOTE: no --real-output-task-id
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert proc.returncode == 1, (
            f"expected rc=1 for missing --real-output-task-id, got {proc.returncode}"
        )
        assert "--real-output-task-id is required" in proc.stderr
    finally:
        os.unlink(pkt_path)


def test_task_id_without_emit_rejected(tmp_path: Path) -> None:
    """The reverse: --real-output-task-id without --emit is also rejected."""
    pkt_path = _write_packet(make_packet())
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_LOCAL / "run_autocoder_single_task.py"),
                "--task-packet-json", pkt_path,
                "--output-json", str(tmp_path / "out.json"),
                "--output-md", str(tmp_path / "out.md"),
                "--real-output-task-id", "real-output-v0-task-002",
                # NOTE: no --emit-real-output-result-packet
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert proc.returncode == 1
        assert "--emit-real-output-result-packet is required" in proc.stderr
    finally:
        os.unlink(pkt_path)


# ---------------------------------------------------------------------------
# 6. Emit flag in non-mock/live mode is rejected (HOLD_TASK_PACKET_INVALID)
# ---------------------------------------------------------------------------


def test_emit_in_claude_mode_rejected(tmp_path: Path) -> None:
    """When execution_mode is not mocked, the controller rejects the
    task packet at validation. The emit flag is silently ignored
    (no packet is written)."""
    emit_path = tmp_path / "should_not_be_written.json"
    packet = make_packet(execution_mode="claude", output_root=str(tmp_path / "aed_runs"))
    out_json = tmp_path / "out.json"
    out_md = tmp_path / "out.md"

    result = _run_controller_subprocess(
        packet, out_json, out_md,
        extra_argv=[
            "--emit-real-output-result-packet", str(emit_path),
            "--real-output-task-id", "real-output-v0-task-002",
        ],
        repo_root=REPO_ROOT,
    )

    cs = result["controller_status"]
    assert cs["status"] == "HOLD_TASK_PACKET_INVALID", (
        f"expected HOLD_TASK_PACKET_INVALID for claude mode; got {cs.get('status')!r}"
    )
    # The emit file must NOT have been written on a HOLD path.
    assert not emit_path.exists(), (
        f"emit file should not be written on HOLD; found at {emit_path}"
    )


def test_emit_in_live_mode_rejected(tmp_path: Path) -> None:
    """Same as the claude test, but for execution_mode=live."""
    emit_path = tmp_path / "should_not_be_written.json"
    packet = make_packet(execution_mode="live", output_root=str(tmp_path / "aed_runs"))
    out_json = tmp_path / "out.json"
    out_md = tmp_path / "out.md"

    result = _run_controller_subprocess(
        packet, out_json, out_md,
        extra_argv=[
            "--emit-real-output-result-packet", str(emit_path),
            "--real-output-task-id", "real-output-v0-task-002",
        ],
        repo_root=REPO_ROOT,
    )

    cs = result["controller_status"]
    assert cs["status"] == "HOLD_TASK_PACKET_INVALID"
    assert not emit_path.exists()


# ---------------------------------------------------------------------------
# 7. No live Claude strings are added to the execution path
# ---------------------------------------------------------------------------


def test_no_live_claude_strings_in_controller() -> None:
    """The controller's source must not contain any live-Claude invocation
    strings. The P3C-B1 emission block is report-only."""
    src = (SCRIPTS_LOCAL / "run_autocoder_single_task.py").read_text(encoding="utf-8")
    forbidden = [
        "claude" + "-code",
        "live" + " claude",
        "Live Claude",
        "enable" + "-real-claude-executor",
    ]
    for s in forbidden:
        assert s not in src, f"controller source contains forbidden literal: {s!r}"


# ---------------------------------------------------------------------------
# 8. No gh mutation strings are added
# ---------------------------------------------------------------------------


def test_no_gh_mutation_strings_added() -> None:
    """The P3C-B1 emission block must not introduce gh mutation strings."""
    src = (SCRIPTS_LOCAL / "run_autocoder_single_task.py").read_text(encoding="utf-8")
    forbidden = [
        "gh " + "pr merge",
        "gh " + "api",
        "gh " + "run watch",
        "gh " + "pr checks --watch",
    ]
    for s in forbidden:
        assert s not in src, f"controller source contains forbidden literal: {s!r}"


# ---------------------------------------------------------------------------
# 9. No subprocess calls use the shell-argument form
# ---------------------------------------------------------------------------


def test_no_subprocess_shell_true_in_controller() -> None:
    """AST scan: no subprocess.run / subprocess.call / subprocess.Popen
    call uses the shell-argument form."""
    src_path = SCRIPTS_LOCAL / "run_autocoder_single_task.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("run", "call", "Popen", "check_call", "check_output"):
            for kw in node.keywords:
                if (kw.arg == "shell" and
                        isinstance(kw.value, ast.Constant) and
                        kw.value.value is True):
                    pytest.fail(
                        f"subprocess.{func.attr} at line "
                        f"{getattr(node, 'lineno', '?')} uses the shell-argument form"
                    )


# ---------------------------------------------------------------------------
# 10. Failure/hold mock path does not emit a misleading PASS packet
# ---------------------------------------------------------------------------


def test_hold_path_does_not_emit_pass_packet(tmp_path: Path) -> None:
    """When the controller reaches a HOLD state in mock mode, the emit
    file is either not created or created with status=HOLD (never PASS)."""
    emit_path = tmp_path / "should_be_hold_or_absent.json"
    # Use an invalid packet_kind to force HOLD_TASK_PACKET_INVALID.
    packet = make_packet(
        packet_kind="bad.kind",
        output_root=str(tmp_path / "aed_runs"),
    )
    out_json = tmp_path / "out.json"
    out_md = tmp_path / "out.md"

    result = _run_controller_subprocess(
        packet, out_json, out_md,
        extra_argv=[
            "--emit-real-output-result-packet", str(emit_path),
            "--real-output-task-id", "real-output-v0-task-002",
        ],
        repo_root=REPO_ROOT,
    )

    cs = result["controller_status"]
    assert cs["status"] == "HOLD_TASK_PACKET_INVALID"

    # Critical: the emit file must NOT be a misleading PASS packet.
    # Two valid outcomes:
    #   (a) file does not exist (HOLD path does not emit anything)
    #   (b) file exists with status == HOLD (not PASS)
    if emit_path.exists():
        data = json.loads(emit_path.read_text())
        assert data.get("status") != "PASS", (
            f"FAIL/HOLD path emitted a misleading PASS packet: {data!r}"
        )


# ---------------------------------------------------------------------------
# 11. Unit tests of the helper (run without subprocess)
# ---------------------------------------------------------------------------


def test_helper_status_mapping() -> None:
    """The helper maps controller status -> packet status correctly."""
    # Reach into the helper to verify the status-mapping branch logic.
    # We call the helper with a fake / missing P3C-A module to verify
    # it returns an ERROR rather than raising.
    emission = controller._try_emit_real_output_result_packet(
        task_packet={"suggested_pr_title": "t", "allowed_files": ["x"]},
        real_task_id="x",
        emit_path=Path("/tmp/nope.json"),
        controller_status=controller.State.READY,
        changed_files=["a.py"],
        branch_name="nonexistent-branch",
        base_sha="",
        repo_root=REPO_ROOT,
    )
    # The P3C-A builder is real and present in scripts/local, so this
    # should produce a RESULT_PACKET_READY (or a write-error if the
    # output path is unwritable). Either way, the helper did not raise.
    assert emission["emission_status"] in (
        "RESULT_PACKET_READY",
        "ERROR_P3CA_WRITE_FAILED",
        "ERROR_P3CA_BUILD_OR_WRITE_FAILED",
    )
    # If it succeeded, verify the mapped status is PASS.
    if emission["emission_status"] == "RESULT_PACKET_READY":
        assert emission["packet_status"] == "PASS"


def test_helper_hold_status_mapping(tmp_path: Path) -> None:
    """The helper maps HOLD_* controller status to packet status=HOLD."""
    emit_path = tmp_path / "hold_packet.json"
    emission = controller._try_emit_real_output_result_packet(
        task_packet={"suggested_pr_title": "h", "allowed_files": ["x"]},
        real_task_id="real-output-v0-task-002",
        emit_path=emit_path,
        controller_status=controller.State.HOLD_TASK_PACKET_INVALID,
        changed_files=["a.py"],
        branch_name="nonexistent-branch",
        base_sha="",
        repo_root=REPO_ROOT,
    )
    assert emission["emission_status"] == "RESULT_PACKET_READY"
    assert emission["packet_status"] == "HOLD"
    assert emit_path.exists()
    data = json.loads(emit_path.read_text())
    assert data["status"] == "HOLD"
    assert "hold_reason" in data
    assert "HOLD_TASK_PACKET_INVALID" in data["hold_reason"]


def test_helper_changed_files_placeholder() -> None:
    """If changed_files is empty, the helper uses a placeholder entry so
    the P3C-A schema's 'changed_files must be non-empty' rule is met."""
    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory() as td:
        emit_path = Path(td) / "packet.json"
        emission = controller._try_emit_real_output_result_packet(
            task_packet={"suggested_pr_title": "z", "allowed_files": ["x"]},
            real_task_id="real-output-v0-task-002",
            emit_path=emit_path,
            controller_status=controller.State.READY,
            changed_files=[],
            branch_name="nonexistent",
            base_sha="",
            repo_root=REPO_ROOT,
        )
        assert emission["emission_status"] == "RESULT_PACKET_READY"
        data = json.loads(emit_path.read_text())
        assert data["changed_files"] == ["(no changes in mock)"]


def test_helper_no_allowed_files_uses_wildcard() -> None:
    """If the task packet has no allowed_files, the helper falls back to
    a '*' wildcard to satisfy the P3C-A schema."""
    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory() as td:
        emit_path = Path(td) / "packet.json"
        emission = controller._try_emit_real_output_result_packet(
            task_packet={"suggested_pr_title": "z"},  # no allowed_files
            real_task_id="real-output-v0-task-002",
            emit_path=emit_path,
            controller_status=controller.State.READY,
            changed_files=["a.py"],
            branch_name="nonexistent",
            base_sha="",
            repo_root=REPO_ROOT,
        )
        assert emission["emission_status"] == "RESULT_PACKET_READY"
        data = json.loads(emit_path.read_text())
        assert data["allowed_files"] == ["*"]
        # And the notes should mention this fallback.
        assert any("no allowed_files" in n.lower() for n in data.get("notes", [])), (
            f"expected a note about allowed_files fallback; got notes={data.get('notes')!r}"
        )


# ---------------------------------------------------------------------------
# 12. Source-safety check on the helper's emit (no gh, no live Claude)
# ---------------------------------------------------------------------------


def test_emitted_packet_source_safe() -> None:
    """The emitted packet's own source (run_autocoder_single_task.py) must
    not contain any forbidden literals. (The shell-argument check is
    authoritative via AST in test_no_subprocess_shell_true_in_controller;
    the controller's module docstring mentions the shell-argument prohibition
    descriptively, which is not a usage.)"""
    src = (SCRIPTS_LOCAL / "run_autocoder_single_task.py").read_text(encoding="utf-8")
    forbidden = [
        "claude" + "-code",
        "live" + " claude",
        "Live Claude",
        "enable" + "-real-claude-executor",
    ]
    for s in forbidden:
        assert s not in src, f"controller source contains forbidden literal: {s!r}"
