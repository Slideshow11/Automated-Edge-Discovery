"""Tests for run_pr_gate_watchdog_once.py — read-only scheduled PR gate watchdog runner."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_SCRIPT_LOCAL = Path(__file__).parent.parent / "scripts" / "local"
sys.path.insert(0, str(_SCRIPT_LOCAL))

from run_pr_gate_watchdog_once import (
    parse_args,
    load_config,
    merge_args,
    run,
)
from watch_pr_gate_state import EXIT_NETWORK_ERROR, EXIT_ARGUMENT_ERROR

RUNNER = str(_SCRIPT_LOCAL / "run_pr_gate_watchdog_once.py")


class TestParseArgs:
    def test_defaults(self):
        args = parse_args([])
        assert args.repo_owner is None
        assert args.repo_name is None
        assert args.pr_number is None
        assert args.output is None

    def test_full_cli(self):
        args = parse_args([
            "--repo-owner", "TestOwner",
            "--repo-name", "TestRepo",
            "--pr-number", "42",
            "--base-branch", "develop",
            "--output", "compact",
            "--expected-head", "abc123",
            "--allowed-file", "src/a.py",
            "--allowed-file", "src/b.py",
        ])
        assert args.repo_owner == "TestOwner"
        assert args.repo_name == "TestRepo"
        assert args.pr_number == 42
        assert args.base_branch == "develop"
        assert args.output == "compact"
        assert args.expected_head == "abc123"
        assert args.allowed_files == ["src/a.py", "src/b.py"]


class TestLoadConfig:
    def test_load_valid_config(self, tmp_path):
        cfg_file = tmp_path / "watchdog.ini"
        cfg_file.write_text("[watchdog]\nrepo_owner = MyOwner\nrepo_name = MyRepo\npr_number = 99\n")
        config = load_config(str(cfg_file))
        assert config["repo_owner"] == "MyOwner"
        assert config["repo_name"] == "MyRepo"
        assert config["pr_number"] == "99"

    def test_missing_section_raises(self, tmp_path):
        cfg_file = tmp_path / "empty.ini"
        cfg_file.write_text("[other]\nkey = value\n")
        with pytest.raises(ValueError, match="no \\[watchdog\\] section"):
            load_config(str(cfg_file))


class TestMergeArgs:
    def test_cli_overrides_config(self, tmp_path):
        cfg_file = tmp_path / "cfg.ini"
        cfg_file.write_text("[watchdog]\nrepo_owner = ConfigOwner\nrepo_name = ConfigRepo\npr_number = 10\n")
        config = load_config(str(cfg_file))
        cli = argparse.Namespace(repo_owner="CliOwner", repo_name="CliRepo", pr_number=99,
                                  base_branch=None, output=None, expected_head=None,
                                  allowed_files=[], config=None)
        argv = merge_args(cli, config)
        # CLI values should appear; config values should not
        assert "CliOwner" in argv
        assert "ConfigOwner" not in argv
        assert "CliRepo" in argv
        # pr_number 99 takes precedence over config 10
        assert "99" in argv

    def test_allowed_files_from_config(self, tmp_path):
        cfg_file = tmp_path / "cfg.ini"
        cfg_file.write_text("[watchdog]\nallowed_file = a.py, b.py\n")
        config = load_config(str(cfg_file))
        cli = argparse.Namespace(repo_owner=None, repo_name=None, pr_number=None,
                                  base_branch=None, output=None, expected_head=None,
                                  allowed_files=[], config=None)
        argv = merge_args(cli, config)
        assert "--allowed-file" in argv
        assert "a.py" in argv
        assert "b.py" in argv

    def test_empty_config_uses_cli_only(self):
        cli = argparse.Namespace(repo_owner="X", repo_name="Y", pr_number=1,
                                  base_branch=None, output=None, expected_head=None,
                                  allowed_files=[], config=None)
        argv = merge_args(cli, {})
        assert "X" in argv
        assert "Y" in argv
        assert "1" in argv


class TestOutputModes:
    def test_json_flag_passed(self):
        with patch("run_pr_gate_watchdog_once.watchdog_run") as mock_run:
            mock_run.return_value = 0
            run(["--repo-owner", "x", "--repo-name", "y", "--pr-number", "1", "--output", "json"])
            args_passed = mock_run.call_args[0][0]
            assert "--json" in args_passed

    def test_compact_flag_passed(self):
        with patch("run_pr_gate_watchdog_once.watchdog_run") as mock_run:
            mock_run.return_value = 0
            run(["--repo-owner", "x", "--repo-name", "y", "--pr-number", "1", "--output", "compact"])
            args_passed = mock_run.call_args[0][0]
            assert "--compact" in args_passed

    def test_summary_no_extra_flag(self):
        with patch("run_pr_gate_watchdog_once.watchdog_run") as mock_run:
            mock_run.return_value = 0
            run(["--repo-owner", "x", "--repo-name", "y", "--pr-number", "1", "--output", "summary"])
            args_passed = mock_run.call_args[0][0]
            assert "--json" not in args_passed
            assert "--compact" not in args_passed

    def test_default_is_summary(self):
        with patch("run_pr_gate_watchdog_once.watchdog_run") as mock_run:
            mock_run.return_value = 0
            run(["--repo-owner", "x", "--repo-name", "y", "--pr-number", "1"])
            args_passed = mock_run.call_args[0][0]
            assert "--json" not in args_passed
            assert "--compact" not in args_passed


class TestExitCodes:
    def test_network_error_exit_2(self):
        with patch("run_pr_gate_watchdog_once.watchdog_run", side_effect=SystemExit(2)):
            with pytest.raises(SystemExit) as exc:
                run(["--repo-owner", "x", "--repo-name", "y", "--pr-number", "1"])
            assert exc.value.code == 2

    def test_argument_error_exit_3(self):
        with patch("run_pr_gate_watchdog_once.watchdog_run", side_effect=SystemExit(3)):
            with pytest.raises(SystemExit) as exc:
                run(["--repo-owner", "x", "--repo-name", "y", "--pr-number", "1"])
            assert exc.value.code == 3


class TestNoMutation:
    """Prove the runner never calls mutation commands."""

    def test_no_gh_pr_comment(self):
        src = Path(_SCRIPT_LOCAL / "run_pr_gate_watchdog_once.py").read_text()
        assert "gh pr comment" not in src
        assert "gh pr merge" not in src
        assert "gh pr create" not in src

    def test_no_requests_post(self):
        src = Path(_SCRIPT_LOCAL / "run_pr_gate_watchdog_once.py").read_text()
        assert "requests.post" not in src
        assert "requests.patch" not in src
        assert "requests.put" not in src

    def test_no_hermes_kanban(self):
        src = Path(_SCRIPT_LOCAL / "run_pr_gate_watchdog_once.py").read_text()
        assert "hermes kanban" not in src
        assert "kanban_create" not in src
        assert "kanban_dispatch" not in src

    def test_no_subprocess_push(self):
        src = Path(_SCRIPT_LOCAL / "run_pr_gate_watchdog_once.py").read_text()
        assert "subprocess" not in src


class TestSmoke:
    """Smoke tests using real subprocess calls (no mocks)."""

    def test_missing_required_args_exits_3(self):
        result = subprocess.run([sys.executable, RUNNER], capture_output=True)
        assert result.returncode == 3

    def test_help_shows_read_only_notice(self):
        result = subprocess.run([sys.executable, RUNNER, "--help"], capture_output=True)
        assert result.returncode == 0
        stdout = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
        assert "never mutates" in stdout.lower() or "read-only" in stdout.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])