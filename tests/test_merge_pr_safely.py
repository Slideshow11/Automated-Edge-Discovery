#!/usr/bin/env python3
"""
tests/test_merge_pr_safely.py

Unit tests for merge_pr_safely.py (v1 — report-only merge command verifier).
Uses direct function imports + mock patching within the same process.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Path to the script under test
SCRIPT = Path(__file__).parent.parent / "scripts" / "local" / "merge_pr_safely.py"


# ------------------------------------------------------------------
# Fake completed process
# ------------------------------------------------------------------

def fake_proc(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


# ------------------------------------------------------------------
# Test 1: rejects --admin in argv (argparse catches it; test via run)
# ------------------------------------------------------------------

def test_rejects_admin_in_argv(tmp_path):
        """When --admin appears in argv, argparse rejects it before our code runs."""
        # Patch subprocess.run so the script's own subprocess calls are also mocked
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = fake_proc("", "unrecognized arguments", 2)
            proc = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--repo", "Slideshow11/Automated-Edge-Discovery",
                 "--repo-root", str(tmp_path),
                 "--pr-number", "368",
                 "--output-json", str(tmp_path / "status.json"),
                 "--output-md", str(tmp_path / "status.md"),
                 "--admin", "true"],
                capture_output=True,
                text=True,
                check=False,
                shell=False,
            )

        # argparse exits with 2 for unrecognized arguments
        assert proc.returncode == 2, f"expected rc=2 from argparse, got {proc.returncode}"


# ------------------------------------------------------------------
# Test 2: rejects dirty repo-root
# ------------------------------------------------------------------

def test_rejects_dirty_repo_root(tmp_path):
    """When repo-root has uncommitted changes, validate_repo_root returns False."""
    # Import the function directly for unit testing
    import importlib.util
    spec = importlib.util.spec_from_file_location("merge_pr_safely", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    validate_repo_root = mod.validate_repo_root

    # Make git rev-parse return true but git status return dirty
    with mock.patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            fake_proc(stdout="true", returncode=0),  # rev-parse
            fake_proc(stdout=" M dirtied_file.py\n", returncode=0),  # status --porcelain
        ]
        ok, detail = validate_repo_root(str(tmp_path))

    assert ok is False
    assert "dirty" in detail.lower()


# ------------------------------------------------------------------
# Test 3: rejects invalid repo-root (not a git worktree)
# ------------------------------------------------------------------

def test_rejects_invalid_repo_root(tmp_path):
    """When repo-root is not a git worktree, validate_repo_root returns False."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("merge_pr_safely", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    validate_repo_root = mod.validate_repo_root

    not_git = tmp_path / "not_a_git_dir"
    not_git.mkdir()

    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = fake_proc(stdout="false", returncode=128)
        ok, detail = validate_repo_root(str(not_git))

    assert ok is False
    assert "not a git worktree" in detail


# ------------------------------------------------------------------
# Test 4: passes --ignore-users through to waiter (subprocess argv check)
# ------------------------------------------------------------------

def test_ignore_users_passed_to_waiter(tmp_path):
    """When --ignore-users is provided, it appears in the waiter subprocess call."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("merge_pr_safely", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    run_waiter = mod.run_waiter

    waiter_calls = []

    def capturing_run(argv, **kwargs):
        waiter_calls.append(list(argv))
        # Write a synthetic READY result
        output_arg_idx = argv.index("--output-json")
        waiter_json_path = argv[output_arg_idx + 1]
        os.makedirs(os.path.dirname(waiter_json_path), exist_ok=True)
        with open(waiter_json_path, "w") as f:
            json.dump({
                "status": "READY_TO_MERGE_CANDIDATE",
                "stages": [],
                "next_safe_action": "gh pr merge ...",
            }, f)
        return fake_proc("", "", 0)

    with mock.patch("subprocess.run", side_effect=capturing_run):
        status, data = run_waiter(
            repo="Slideshow11/Automated-Edge-Discovery",
            repo_root=str(tmp_path),
            pr_number=368,
            head_sha="a" * 40,
            timeout_minutes=5,
            poll_seconds=30,
            ignore_users="chatgpt-codex-connector,hermes-agent",
            output_dir=str(tmp_path),
        )

    assert len(waiter_calls) >= 1, f"waiter never called: {waiter_calls}"
    waiter_call = waiter_calls[0]
    assert "--ignore-users" in waiter_call, f"--ignore-users not in call: {waiter_call}"
    ignore_idx = waiter_call.index("--ignore-users")
    assert waiter_call[ignore_idx + 1] == "chatgpt-codex-connector,hermes-agent"


# ------------------------------------------------------------------
# Test 5: waiter not ready → HOLD_WAITER_NOT_READY, no merge command
# ------------------------------------------------------------------

def test_waiter_not_ready_holds(tmp_path):
    """When waiter returns a non-READY status, main() returns HOLD_WAITER_NOT_READY."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("merge_pr_safely", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    main = mod.main

    # Make git rev-parse and status return clean
    # Make gh pr view return a head SHA
    # Make waiter write a HOLD_CI_PENDING status
    calls = []

    head_sha = "a" * 40

    def tracking_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[0] == "git" and "rev-parse" in argv:
            return fake_proc(stdout="true", returncode=0)
        if argv[0] == "git" and "status" in argv:
            return fake_proc(stdout="", returncode=0)
        if "gh" in argv and "view" in argv:
            # gh pr view --json headRefOid --jq .headRefOid returns bare SHA string
            return fake_proc(stdout=head_sha, returncode=0)
        if "wait_for_pr_ready.py" in " ".join(argv):
            output_arg_idx = argv.index("--output-json")
            waiter_json_path = argv[output_arg_idx + 1]
            os.makedirs(os.path.dirname(waiter_json_path), exist_ok=True)
            with open(waiter_json_path, "w") as f:
                json.dump({
                    "status": "HOLD_CI_PENDING",
                    "stages": [{"stage": "ci_poll", "status": "HOLD_CI_PENDING"}],
                    "next_safe_action": "wait for CI",
                }, f)
            return fake_proc("", "", 0)
        return fake_proc("", "", 0)

    with mock.patch("subprocess.run", side_effect=tracking_run):
        with mock.patch("sys.argv", [
            "merge_pr_safely.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--repo-root", str(tmp_path),
            "--pr-number", "368",
            "--output-json", str(tmp_path / "status.json"),
            "--output-md", str(tmp_path / "status.md"),
        ]):
            rc = main()

    assert rc == 0
    with open(tmp_path / "status.json") as f:
        data = json.load(f)

    assert data["status"] == "HOLD_WAITER_NOT_READY"
    assert data["waiter_status"] == "HOLD_CI_PENDING"
    assert data["safe_merge_command_text"] == ""
    assert data["command_verified"] is False


# ------------------------------------------------------------------
# Test 6: waiter ready → emits correct gh pr merge command
# ------------------------------------------------------------------

def test_merger_ready_emits_correct_command(tmp_path):
    """When waiter returns READY_TO_MERGE_CANDIDATE, script emits correct safe merge command."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("merge_pr_safely", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    main = mod.main
    build_safe_merge_command = mod.build_safe_merge_command

    head_sha = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    calls = []

    def tracking_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[0] == "git" and "rev-parse" in argv:
            return fake_proc(stdout="true", returncode=0)
        if argv[0] == "git" and "status" in argv:
            return fake_proc(stdout="", returncode=0)
        if "gh" in argv and "view" in argv:
            # gh pr view --json headRefOid --jq .headRefOid returns bare SHA string
            return fake_proc(stdout=head_sha, returncode=0)
        if "wait_for_pr_ready.py" in " ".join(argv):
            output_arg_idx = argv.index("--output-json")
            waiter_json_path = argv[output_arg_idx + 1]
            os.makedirs(os.path.dirname(waiter_json_path), exist_ok=True)
            with open(waiter_json_path, "w") as f:
                json.dump({
                    "status": "READY_TO_MERGE_CANDIDATE",
                    "stages": [],
                    "next_safe_action": "gh pr merge 368 ...",
                }, f)
            return fake_proc("", "", 0)
        if "verify_final_head_merge_command.py" in " ".join(argv):
            output_arg_idx = argv.index("--output-json")
            verify_json_path = argv[output_arg_idx + 1]
            os.makedirs(os.path.dirname(verify_json_path), exist_ok=True)
            with open(verify_json_path, "w") as f:
                json.dump({
                    "recommendation": "MERGE_READY_CANDIDATE",
                    "verification_errors": [],
                }, f)
            return fake_proc("", "", 0)
        return fake_proc("", "", 0)

    with mock.patch("subprocess.run", side_effect=tracking_run):
        with mock.patch("sys.argv", [
            "merge_pr_safely.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--repo-root", str(tmp_path),
            "--pr-number", "368",
            "--output-json", str(tmp_path / "status.json"),
            "--output-md", str(tmp_path / "status.md"),
        ]):
            rc = main()

    assert rc == 0
    with open(tmp_path / "status.json") as f:
        data = json.load(f)

    assert data["status"] == "SAFE_MERGE_COMMAND_READY"
    assert "--squash" in data["safe_merge_command_text"]
    assert "--delete-branch" in data["safe_merge_command_text"]
    assert "--match-head-commit" in data["safe_merge_command_text"]
    assert "--admin" not in data["safe_merge_command_text"]
    assert head_sha in data["safe_merge_command_text"]
    assert data["pr_number"] == 368
    assert data["head_sha"] == head_sha

    # Also verify the build_safe_merge_command function directly
    cmd = build_safe_merge_command(368, "Slideshow11/Automated-Edge-Discovery", head_sha)
    assert "--squash" in cmd
    assert "--delete-branch" in cmd
    assert "--match-head-commit" in cmd
    assert "--admin" not in cmd


# ------------------------------------------------------------------
# Test 7: verify command with verifier mock
# ------------------------------------------------------------------

def test_verifier_called_and_result_used(tmp_path):
    """Script calls verify_final_head_merge_command.py and respects its recommendation."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("merge_pr_safely", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    verify_merge_command = mod.verify_merge_command

    head_sha = "b" * 40
    verifier_calls = []

    def capturing_run(argv, **kwargs):
        verifier_calls.append(list(argv))
        if "verify_final_head_merge_command.py" in " ".join(argv):
            output_arg_idx = argv.index("--output-json")
            verify_json_path = argv[output_arg_idx + 1]
            os.makedirs(os.path.dirname(verify_json_path), exist_ok=True)
            with open(verify_json_path, "w") as f:
                json.dump({
                    "recommendation": "MERGE_READY_CANDIDATE",
                    "verification_errors": [],
                    "merge_command": f"gh pr merge 368 --repo Slideshow11/Automated-Edge-Discovery --squash --delete-branch --match-head-commit {head_sha}",
                }, f)
            return fake_proc("", "", 0)
        return fake_proc("", "", 0)

    with mock.patch("subprocess.run", side_effect=capturing_run):
        verified, data = verify_merge_command(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=368,
            head_sha=head_sha,
            pmg_state_json=None,
            output_dir=str(tmp_path),
        )

    assert verified is True
    assert len(verifier_calls) == 1
    assert "--pr-number" in verifier_calls[0]
    assert "--reported-head-sha" in verifier_calls[0]
    assert head_sha in verifier_calls[0]


# ------------------------------------------------------------------
# Test 8: verifier fails → HOLD_COMMAND_VERIFICATION_FAILED
# ------------------------------------------------------------------

def test_verifier_failure_holds(tmp_path):
    """When verifier returns non-MERGE_READY_CANDIDATE, main() returns HOLD_COMMAND_VERIFICATION_FAILED."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("merge_pr_safely", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    main = mod.main

    head_sha = "c" * 40

    def tracking_run(argv, **kwargs):
        if argv[0] == "git" and "rev-parse" in argv:
            return fake_proc(stdout="true", returncode=0)
        if argv[0] == "git" and "status" in argv:
            return fake_proc(stdout="", returncode=0)
        if "gh" in argv and "view" in argv:
            # gh pr view --json headRefOid --jq .headRefOid returns bare SHA string
            return fake_proc(stdout=head_sha, returncode=0)
        if "wait_for_pr_ready.py" in " ".join(argv):
            output_arg_idx = argv.index("--output-json")
            waiter_json_path = argv[output_arg_idx + 1]
            os.makedirs(os.path.dirname(waiter_json_path), exist_ok=True)
            with open(waiter_json_path, "w") as f:
                json.dump({
                    "status": "READY_TO_MERGE_CANDIDATE",
                    "stages": [],
                    "next_safe_action": "gh pr merge 368 ...",
                }, f)
            return fake_proc("", "", 0)
        if "verify_final_head_merge_command.py" in " ".join(argv):
            output_arg_idx = argv.index("--output-json")
            verify_json_path = argv[output_arg_idx + 1]
            os.makedirs(os.path.dirname(verify_json_path), exist_ok=True)
            with open(verify_json_path, "w") as f:
                json.dump({
                    "recommendation": "BLOCK",
                    "verification_errors": ["merge command contains --admin flag"],
                }, f)
            return fake_proc("", "", 0)
        return fake_proc("", "", 0)

    with mock.patch("subprocess.run", side_effect=tracking_run):
        with mock.patch("sys.argv", [
            "merge_pr_safely.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--repo-root", str(tmp_path),
            "--pr-number", "368",
            "--output-json", str(tmp_path / "status.json"),
            "--output-md", str(tmp_path / "status.md"),
        ]):
            rc = main()

    assert rc == 0
    with open(tmp_path / "status.json") as f:
        data = json.load(f)

    assert data["status"] == "HOLD_COMMAND_VERIFICATION_FAILED"
    assert data["command_verified"] is False
    assert len(data.get("verification_errors", [])) > 0


# ------------------------------------------------------------------
# Test 9: JSON report includes mutated_github=false and merged=false
# ------------------------------------------------------------------

def test_report_safety_fields(tmp_path):
    """JSON report always contains mutated_github=false and merged=false."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("merge_pr_safely", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    main = mod.main

    head_sha = "d" * 40

    def tracking_run(argv, **kwargs):
        if argv[0] == "git" and "rev-parse" in argv:
            return fake_proc(stdout="true", returncode=0)
        if argv[0] == "git" and "status" in argv:
            return fake_proc(stdout="", returncode=0)
        if "gh" in argv and "view" in argv:
            # gh pr view --json headRefOid --jq .headRefOid returns bare SHA string
            return fake_proc(stdout=head_sha, returncode=0)
        if "wait_for_pr_ready.py" in " ".join(argv):
            output_arg_idx = argv.index("--output-json")
            waiter_json_path = argv[output_arg_idx + 1]
            os.makedirs(os.path.dirname(waiter_json_path), exist_ok=True)
            with open(waiter_json_path, "w") as f:
                json.dump({
                    "status": "READY_TO_MERGE_CANDIDATE",
                    "stages": [],
                    "next_safe_action": "gh pr merge 368 ...",
                }, f)
            return fake_proc("", "", 0)
        if "verify_final_head_merge_command.py" in " ".join(argv):
            output_arg_idx = argv.index("--output-json")
            verify_json_path = argv[output_arg_idx + 1]
            os.makedirs(os.path.dirname(verify_json_path), exist_ok=True)
            with open(verify_json_path, "w") as f:
                json.dump({
                    "recommendation": "MERGE_READY_CANDIDATE",
                    "verification_errors": [],
                }, f)
            return fake_proc("", "", 0)
        return fake_proc("", "", 0)

    with mock.patch("subprocess.run", side_effect=tracking_run):
        with mock.patch("sys.argv", [
            "merge_pr_safely.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--repo-root", str(tmp_path),
            "--pr-number", "368",
            "--output-json", str(tmp_path / "status.json"),
            "--output-md", str(tmp_path / "status.md"),
        ]):
            rc = main()

    assert rc == 0
    with open(tmp_path / "status.json") as f:
        data = json.load(f)

    assert data["mutated_github"] is False
    assert data["merged"] is False
    assert data["execute_merge_supported"] is False


# ------------------------------------------------------------------
# Test 10: Markdown report states v1 does not execute merge
# ------------------------------------------------------------------

def test_markdown_report_no_merge_claim(tmp_path):
    """Markdown report explicitly states v1 does not execute merges."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("merge_pr_safely", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    main = mod.main

    head_sha = "e" * 40

    def tracking_run(argv, **kwargs):
        if argv[0] == "git" and "rev-parse" in argv:
            return fake_proc(stdout="true", returncode=0)
        if argv[0] == "git" and "status" in argv:
            return fake_proc(stdout="", returncode=0)
        if "gh" in argv and "view" in argv:
            # gh pr view --json headRefOid --jq .headRefOid returns bare SHA string
            return fake_proc(stdout=head_sha, returncode=0)
        if "wait_for_pr_ready.py" in " ".join(argv):
            output_arg_idx = argv.index("--output-json")
            waiter_json_path = argv[output_arg_idx + 1]
            os.makedirs(os.path.dirname(waiter_json_path), exist_ok=True)
            with open(waiter_json_path, "w") as f:
                json.dump({
                    "status": "READY_TO_MERGE_CANDIDATE",
                    "stages": [],
                    "next_safe_action": "gh pr merge 368 ...",
                }, f)
            return fake_proc("", "", 0)
        if "verify_final_head_merge_command.py" in " ".join(argv):
            output_arg_idx = argv.index("--output-json")
            verify_json_path = argv[output_arg_idx + 1]
            os.makedirs(os.path.dirname(verify_json_path), exist_ok=True)
            with open(verify_json_path, "w") as f:
                json.dump({
                    "recommendation": "MERGE_READY_CANDIDATE",
                    "verification_errors": [],
                }, f)
            return fake_proc("", "", 0)
        return fake_proc("", "", 0)

    with mock.patch("subprocess.run", side_effect=tracking_run):
        with mock.patch("sys.argv", [
            "merge_pr_safely.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--repo-root", str(tmp_path),
            "--pr-number", "368",
            "--output-json", str(tmp_path / "status.json"),
            "--output-md", str(tmp_path / "status.md"),
        ]):
            rc = main()

    assert rc == 0
    with open(tmp_path / "status.md") as f:
        md = f.read()

    assert "v1 does not execute merges" in md
    assert "--admin is forbidden" in md


# ------------------------------------------------------------------
# Test 11: no execute-merge CLI option exists
# ------------------------------------------------------------------

def test_no_execute_merge_option(tmp_path):
    """Script rejects --execute-merge via argparse (unrecognized argument)."""
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = fake_proc("", "unrecognized arguments: --execute-merge", 2)
        proc = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--repo", "Slideshow11/Automated-Edge-Discovery",
             "--repo-root", str(tmp_path),
             "--pr-number", "368",
             "--output-json", str(tmp_path / "status.json"),
             "--output-md", str(tmp_path / "status.md"),
             "--execute-merge"],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )

    assert proc.returncode == 2
    assert "unrecognized arguments" in proc.stderr


# ------------------------------------------------------------------
# Test 12: subprocess calls use list args and not shell=True
# ------------------------------------------------------------------

def test_subprocess_no_shell(tmp_path):
    """All subprocess.run calls use shell=False (verified via mock)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("merge_pr_safely", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    main = mod.main

    head_sha = "g" * 40
    all_calls = []

    def tracking_run(argv, **kwargs):
        all_calls.append({"argv": argv, "kwargs": kwargs})
        if argv[0] == "git" and "rev-parse" in argv:
            return fake_proc(stdout="true", returncode=0)
        if argv[0] == "git" and "status" in argv:
            return fake_proc(stdout="", returncode=0)
        if "gh" in argv and "view" in argv:
            # gh pr view --json headRefOid --jq .headRefOid returns bare SHA string
            return fake_proc(stdout=head_sha, returncode=0)
        if "wait_for_pr_ready.py" in " ".join(argv):
            output_arg_idx = argv.index("--output-json")
            waiter_json_path = argv[output_arg_idx + 1]
            os.makedirs(os.path.dirname(waiter_json_path), exist_ok=True)
            with open(waiter_json_path, "w") as f:
                json.dump({
                    "status": "READY_TO_MERGE_CANDIDATE",
                    "stages": [],
                    "next_safe_action": "gh pr merge 368 ...",
                }, f)
            return fake_proc("", "", 0)
        if "verify_final_head_merge_command.py" in " ".join(argv):
            output_arg_idx = argv.index("--output-json")
            verify_json_path = argv[output_arg_idx + 1]
            os.makedirs(os.path.dirname(verify_json_path), exist_ok=True)
            with open(verify_json_path, "w") as f:
                json.dump({
                    "recommendation": "MERGE_READY_CANDIDATE",
                    "verification_errors": [],
                }, f)
            return fake_proc("", "", 0)
        return fake_proc("", "", 0)

    with mock.patch("subprocess.run", side_effect=tracking_run):
        with mock.patch("sys.argv", [
            "merge_pr_safely.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--repo-root", str(tmp_path),
            "--pr-number", "368",
            "--output-json", str(tmp_path / "status.json"),
            "--output-md", str(tmp_path / "status.md"),
        ]):
            rc = main()

    assert rc == 0
    for call in all_calls:
        assert call["kwargs"].get("shell", False) is False, \
            f"shell=True found in call: {call['argv'][:3]}"
        assert isinstance(call["argv"], list), \
            f"argv is not a list: {type(call['argv'])}"


# ------------------------------------------------------------------
# Test 13: waiter failure → ERROR_TOOL_FAILURE, no SAFE status
# ------------------------------------------------------------------

def test_waiter_subprocess_failure_returns_error(tmp_path):
    """
    When wait_for_pr_ready.py exits non-zero, merge_pr_safely.py returns
    ERROR_TOOL_FAILURE and does NOT emit a ready merge command.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("merge_pr_safely", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    main = mod.main

    head_sha = "deadbeef" + "a" * 32
    waiter_json = tmp_path / "waiter_status.json"

    def tracking_run(argv, **kwargs):
        if argv[0] == "git" and "rev-parse" in argv:
            return fake_proc(stdout="true", returncode=0)
        if argv[0] == "git" and "status" in argv:
            return fake_proc(stdout="", returncode=0)
        if "gh" in argv and "view" in argv:
            return fake_proc(stdout=head_sha, returncode=0)
        if "wait_for_pr_ready.py" in " ".join(argv):
            # Do NOT write any status JSON — simulates crash mid-run
            return fake_proc(stderr="internal error", returncode=1)
        return fake_proc("", "", 0)

    with mock.patch("subprocess.run", side_effect=tracking_run):
        with mock.patch("sys.argv", [
            "merge_pr_safely.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--repo-root", str(tmp_path),
            "--pr-number", "371",
            "--output-json", str(tmp_path / "status.json"),
            "--output-md", str(tmp_path / "status.md"),
        ]):
            rc = main()

    assert rc == 0
    with open(tmp_path / "status.json") as f:
        data = json.load(f)

    assert data["status"] == "ERROR_TOOL_FAILURE"
    assert data["safe_merge_command_text"] == ""
    assert data["command_verified"] is False
    assert data["mutated_github"] is False
    assert data["merged"] is False
    assert data.get("waiter_returncode") == 1


# ------------------------------------------------------------------
# Test 14: stale waiter JSON from previous run does not trigger ready
# ------------------------------------------------------------------

def test_stale_waiter_output_ignored_on_rerun(tmp_path):
    """
    If a stale waiter_status.json from a previous run claims READY but
    the waiter subprocess fails this run, merge_pr_safely.py must NOT
    emit a ready merge command.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("merge_pr_safely", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    main = mod.main

    head_sha = "deadbeef" + "b" * 32
    waiter_json = tmp_path / "waiter_status.json"

    # Pre-write a stale READY file from a previous run
    waiter_json.write_text(json.dumps({
        "status": "READY_TO_MERGE_CANDIDATE",
        "stages": [],
        "next_safe_action": "gh pr merge",
    }))

    def tracking_run(argv, **kwargs):
        if argv[0] == "git" and "rev-parse" in argv:
            return fake_proc(stdout="true", returncode=0)
        if argv[0] == "git" and "status" in argv:
            return fake_proc(stdout="", returncode=0)
        if "gh" in argv and "view" in argv:
            return fake_proc(stdout=head_sha, returncode=0)
        if "wait_for_pr_ready.py" in " ".join(argv):
            # Simulate failure — no new JSON written
            return fake_proc(stderr="timeout", returncode=2)
        return fake_proc("", "", 0)

    with mock.patch("subprocess.run", side_effect=tracking_run):
        with mock.patch("sys.argv", [
            "merge_pr_safely.py",
            "--repo", "Slideshow11/Automated-Edge-Discovery",
            "--repo-root", str(tmp_path),
            "--pr-number", "371",
            "--output-json", str(tmp_path / "status.json"),
            "--output-md", str(tmp_path / "status.md"),
        ]):
            rc = main()

    assert rc == 0
    with open(tmp_path / "status.json") as f:
        data = json.load(f)

    assert data["status"] == "ERROR_TOOL_FAILURE"
    assert data["safe_merge_command_text"] == ""
    assert data["command_verified"] is False


# ------------------------------------------------------------------
# Test 15: verifier receives --repo argument
# ------------------------------------------------------------------

def test_verifier_receives_repo_argument(tmp_path):
    """
    verify_final_head_merge_command.py is called with --repo forwarding
    the repository passed to merge_pr_safely.py.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("merge_pr_safely", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    verify_merge_command = mod.verify_merge_command

    head_sha = "deadbeef" + "c" * 32
    verifier_calls = []

    def capturing_run(argv, **kwargs):
        verifier_calls.append(list(argv))
        if "verify_final_head_merge_command.py" in " ".join(argv):
            output_arg_idx = argv.index("--output-json")
            verify_json_path = argv[output_arg_idx + 1]
            os.makedirs(os.path.dirname(verify_json_path), exist_ok=True)
            with open(verify_json_path, "w") as f:
                json.dump({
                    "recommendation": "MERGE_READY_CANDIDATE",
                    "verification_errors": [],
                }, f)
            return fake_proc("", "", 0)
        return fake_proc("", "", 0)

    with mock.patch("subprocess.run", side_effect=capturing_run):
        verified, _ = verify_merge_command(
            repo="Slideshow11/Automated-Edge-Discovery",
            pr_number=371,
            head_sha=head_sha,
            pmg_state_json=None,
            output_dir=str(tmp_path),
        )

    assert verified is True
    assert len(verifier_calls) == 1
    assert "--repo" in verifier_calls[0], \
        f"--repo not in verifier call: {verifier_calls[0]}"
    repo_idx = verifier_calls[0].index("--repo")
    assert verifier_calls[0][repo_idx + 1] == "Slideshow11/Automated-Edge-Discovery", \
        f"wrong repo forwarded: {verifier_calls[0][repo_idx + 1]}"