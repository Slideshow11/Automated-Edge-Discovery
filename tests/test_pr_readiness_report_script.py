import json
import types
from unittest import mock

import pytest

from scripts.local import pr_readiness_report as pr


class DummyCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def make_cp(out="", err="", rc=0):
    cp = types.SimpleNamespace()
    cp.stdout = out
    cp.stderr = err
    cp.returncode = rc
    return cp


@mock.patch("scripts.local.pr_readiness_report.run_cmd")
def test_default_mode_no_gh(run_cmd):
    # Setup git responses. First call: git rev-parse --show-toplevel
    run_cmd.side_effect = [
        make_cp("/home/max/aed_audit_clean\n"),  # rev-parse --show-toplevel
        make_cp("main\n"),  # branch --show-current
        make_cp("abcd1234\n"),  # rev-parse HEAD
        make_cp("" , rc=1),  # rev-parse @{u} fails
        make_cp(""),  # git status --short -> clean
        make_cp(""),  # diff --stat
        make_cp(""),  # diff --name-only
        make_cp("c1 commit1\nc2 commit2\n"),  # git log
    ]

    rc = pr.main(["--format", "json", "--max-commits", "2"])  # should not raise
    assert rc == 0


@mock.patch("scripts.local.pr_readiness_report.run_cmd")
def test_include_pr_calls_gh(run_cmd):
    run_cmd.side_effect = [
        make_cp("/home/max/aed_audit_clean\n"),  # rev-parse --show-toplevel
        make_cp("feature/x\n"),
        make_cp("abcd1234\n"),
        make_cp("origin/feature/x\n"),
        make_cp(""),
        make_cp(""),
        make_cp("file1.py\nfile2.py\n"),
        make_cp("c1 commit1\n"),
        # gh pr view
        make_cp(json.dumps({"state": "OPEN"}), "", 0),
        # gh pr checks
        make_cp(json.dumps({"status": "PASS"}), "", 0),
    ]
    rc = pr.main(["--format", "json", "--include-pr"])
    assert rc == 0


@mock.patch("scripts.local.pr_readiness_report.run_cmd")
def test_dirty_worktree_sets_flag(run_cmd):
    run_cmd.side_effect = [
        make_cp("/home/max/aed_audit_clean\n"),
        make_cp("main\n"),
        make_cp("abcd1234\n"),
        make_cp("origin/main\n"),
        make_cp(" M scripts/local/pr_readiness_report.py\n"),
        make_cp(""),
        make_cp("file1.py\n"),
        make_cp("c1 commit1\n"),
    ]
    rc = pr.main(["--format", "json"])
    assert rc == 0


@mock.patch("scripts.local.pr_readiness_report.run_cmd")
def test_text_output_includes_branch_and_files(run_cmd):
    run_cmd.side_effect = [
        make_cp("/home/max/aed_audit_clean\n"),
        make_cp("branch/1\n"),
        make_cp("abcd1234\n"),
        make_cp("origin/branch/1\n"),
        make_cp(""),
        make_cp(""),
        make_cp("a.py\nb.py\n"),
        make_cp("c1 commit1\n"),
    ]
    # Capture stdout
    with mock.patch("sys.stdout") as fake_out:
        rc = pr.main(["--format", "text"])  # prints text
    assert rc == 0
