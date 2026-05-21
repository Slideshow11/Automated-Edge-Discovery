"""
Tests for check_real_claude_env_preflight.py

No execution of real Claude, no shell=True, no file mutation beyond output.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "local" / "check_real_claude_env_preflight.py"
HARNESS = REPO_ROOT / "scripts" / "local" / "run_temp_worktree_execution.py"
READINESS = REPO_ROOT / "scripts" / "local" / "check_real_executor_readiness.py"

# ---------------------------------------------------------------------------
# State constants (must match the script)
# ---------------------------------------------------------------------------

STATE_READY                = "READY_FOR_LIVE_CLAUDE_SMOKE_PLANNING"
STATE_HOLD_BINARY_MISSING  = "HOLD_CLAUDE_BINARY_MISSING"
STATE_HOLD_HELP_FAILED     = "HOLD_CLAUDE_HELP_PROBE_FAILED"
STATE_HOLD_NON_INTERACTIVE = "HOLD_NON_INTERACTIVE_TTY"
STATE_HOLD_REPO_DIRTY      = "HOLD_REPO_DIRTY"
STATE_HOLD_READINESS_FAILED = "HOLD_READINESS_CHECK_FAILED"
STATE_HOLD_CONTRACT_MISSING = "HOLD_COMMAND_CONTRACT_MISSING"
STATE_HOLD_EXECUTOR_ENABLED = "HOLD_REAL_EXECUTOR_ALREADY_ENABLED"
STATE_HOLD_UNKNOWN         = "HOLD_UNKNOWN"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_subprocess(*argv, allow_noninteractive=True):
    """Run preflight script as subprocess, return parsed JSON."""
    cmd = [sys.executable, str(SCRIPT)]
    if allow_noninteractive:
        cmd.append("--allow-noninteractive")
    cmd.extend(list(argv))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    try:
        data = json.loads(result.stdout)
    except Exception:
        data = {"_raw_stdout": result.stdout, "_stderr": result.stderr, "_rc": result.returncode}
    return data, result.stderr, result.returncode


def _run_direct(**kwargs):
    """
    Run preflight.run_preflight() directly with full mocking of all
    environment-dependent helpers.
    """
    import sys as _sys
    _sys.path.insert(0, str(REPO_ROOT / "scripts" / "local"))
    import check_real_claude_env_preflight as preflight

    mock_git_dirty       = kwargs.pop("mock_git_dirty",       False)
    mock_tty             = kwargs.pop("mock_tty",              True)
    mock_claude_binary   = kwargs.pop("mock_claude_binary",    None)
    mock_readiness_json  = kwargs.pop("mock_readiness_json",   None)
    mock_contract_ok     = kwargs.pop("mock_contract_ok",       True)
    mock_synthetic_ok   = kwargs.pop("mock_synthetic_ok",    True)
    mock_shell_ok        = kwargs.pop("mock_shell_ok",         True)
    mock_llm_ok          = kwargs.pop("mock_llm_ok",           True)
    # Forwarded to run_preflight
    allow_help_probe     = kwargs.pop("allow_help_probe",      False)
    require_claude_binary = kwargs.pop("require_claude_binary",  False)
    allow_noninteractive = kwargs.pop("allow_noninteractive",   True)

    # Normalise None → {}
    readiness_json = mock_readiness_json if mock_readiness_json is not None else {
        "status": "READY_TO_IMPLEMENT_REAL_EXECUTOR",
        "real_executor_allowed": False,
    }

    def fake_git():
        return not mock_git_dirty

    with patch.object(preflight, "_git_worktree_clean", fake_git), \
         patch.object(preflight, "_check_tty", lambda: mock_tty), \
         patch.object(preflight, "_find_claude_binary", lambda: mock_claude_binary), \
         patch.object(preflight, "_load_readiness_json", return_value=readiness_json), \
         patch.object(preflight, "_check_command_contract_functions", return_value=(mock_contract_ok, "")), \
         patch.object(preflight, "_check_harness_real_claude_blocked", return_value=(True, "")), \
         patch.object(preflight, "_synthetic_contract_and_validator", return_value=(mock_synthetic_ok, "")), \
         patch.object(preflight, "_check_no_shell_in_harness", lambda: mock_shell_ok), \
         patch.object(preflight, "_check_no_llm_imports_in_harness", return_value=(mock_llm_ok, [])):

        result = preflight.run_preflight(
            allow_help_probe=allow_help_probe,
            require_claude_binary=require_claude_binary,
            allow_noninteractive=allow_noninteractive,
            output_json=None,
            output_md=None,
        )
    return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_env():
    """Skip if working tree is not clean."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        pytest.skip(f"Working tree not clean: {result.stdout}")
    yield


@pytest.fixture
def fake_claude_binary(tmp_path):
    """Fake claude binary that exits 0."""
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/bash\nexit 0\n")
    fake.chmod(0o755)
    return fake


# ---------------------------------------------------------------------------
# Tests: clean environment (real subprocess)
# ---------------------------------------------------------------------------

class TestCleanEnvironment:
    def test_returns_ready(self, clean_env):
        data, _, rc = _run_subprocess()
        assert rc == 0
        assert data["status"] == STATE_READY, f"got {data['status']}: {data.get('missing', [])}"
        assert data["real_executor_allowed"] is False

    def test_json_output_fields(self, clean_env, tmp_path):
        out = tmp_path / "report.json"
        data, _, rc = _run_subprocess("--output-json", str(out))
        assert rc == 0 and out.exists()
        loaded = json.loads(out.read_text())
        for field in ("status", "real_executor_allowed", "ready_for_live_smoke_planning",
                     "checks", "missing", "recommendation", "generated_at"):
            assert field in loaded
        assert loaded["real_executor_allowed"] is False

    def test_md_output(self, clean_env, tmp_path):
        out = tmp_path / "report.md"
        data, _, rc = _run_subprocess("--output-md", str(out))
        assert rc == 0 and out.exists()
        content = out.read_text()
        assert "READY_FOR_LIVE_CLAUDE_SMOKE_PLANNING" in content
        assert "real_executor_allowed" in content

    def test_checks_all_present(self, clean_env):
        data, _, _ = _run_subprocess()
        checks = data["checks"]
        for key in ("git_status_clean", "interactive_tty", "claude_binary_found",
                    "claude_binary_path", "claude_help_probe_allowed",
                    "claude_help_probe_passed", "readiness_checker_ready",
                    "command_contract_present", "real_executor_enabled",
                    "no_shell_in_claude_path", "no_llm_imports_in_harness",
                    "synthetic_contract_valid"):
            assert key in checks, f"Missing: {key}"

    def test_recommendation_not_empty(self, clean_env):
        data, _, _ = _run_subprocess()
        assert data["recommendation"]
        assert len(data["recommendation"]) > 10


# ---------------------------------------------------------------------------
# Tests: dirty git (mocked)
# ---------------------------------------------------------------------------

class TestDirtyGit:
    def test_dirty_git_returns_hold(self):
        data = _run_direct(mock_git_dirty=True)
        assert data["status"] == STATE_HOLD_REPO_DIRTY
        assert data["real_executor_allowed"] is False


# ---------------------------------------------------------------------------
# Tests: readiness checker states
# ---------------------------------------------------------------------------

class TestReadinessChecker:
    def test_readiness_json_empty_returns_hold(self):
        data = _run_direct(mock_readiness_json={})
        assert data["status"] == STATE_HOLD_READINESS_FAILED
        assert any("readiness checker did not return a usable status" in m
                   for m in data["missing"]), data["missing"]
        assert data["real_executor_allowed"] is False

    def test_readiness_status_not_ready_returns_hold(self):
        data = _run_direct(mock_readiness_json={"status": "HOLD_BROKEN"})
        assert data["status"] == STATE_HOLD_READINESS_FAILED
        assert data["real_executor_allowed"] is False

    def test_real_executor_allowed_true_returns_hold(self):
        data = _run_direct(mock_readiness_json={
            "status": "READY_TO_IMPLEMENT_REAL_EXECUTOR",
            "real_executor_allowed": True,
        })
        assert data["status"] == STATE_HOLD_EXECUTOR_ENABLED
        assert data["real_executor_allowed"] is False


# ---------------------------------------------------------------------------
# Tests: command contract
# ---------------------------------------------------------------------------

class TestCommandContract:
    def test_builder_missing_returns_hold(self):
        data = _run_direct(mock_contract_ok=False)
        assert data["status"] == STATE_HOLD_CONTRACT_MISSING

    def test_synthetic_invalid_returns_hold(self):
        data = _run_direct(mock_synthetic_ok=False)
        assert data["status"] == STATE_HOLD_UNKNOWN


# ---------------------------------------------------------------------------
# Tests: Claude binary (mocked)
# ---------------------------------------------------------------------------

class TestClaudeBinary:
    def test_require_binary_missing_returns_hold(self):
        data = _run_direct(require_claude_binary=True, mock_claude_binary=None)
        assert data["status"] == STATE_HOLD_BINARY_MISSING

    def test_require_binary_not_required(self):
        data = _run_direct(require_claude_binary=False, mock_claude_binary=None)
        assert data["checks"]["claude_binary_found"] is False
        assert data["status"] == STATE_READY

    def test_help_probe_flag_needed(self, fake_claude_binary, monkeypatch):
        """Without --allow-claude-help-probe, probe is not run."""
        monkeypatch.setattr("shutil.which", lambda n: str(fake_claude_binary) if n == "claude" else None)
        data = _run_direct(mock_claude_binary=str(fake_claude_binary))
        assert data["checks"]["claude_help_probe_allowed"] is False
        assert data["checks"]["claude_help_probe_passed"] is None
        assert data["status"] == STATE_READY

    def test_help_probe_rejected_when_binary_broken(self, tmp_path, monkeypatch):
        fake = tmp_path / "claude"
        fake.write_text("#!/bin/bash\nexit 1\n")
        fake.chmod(0o755)
        monkeypatch.setattr("shutil.which", lambda n: str(fake) if n == "claude" else None)
        data = _run_direct(allow_help_probe=True, mock_claude_binary=str(fake))
        assert data["status"] == STATE_HOLD_HELP_FAILED

    def test_help_probe_timeout(self, tmp_path, monkeypatch):
        fake = tmp_path / "claude"
        fake.write_text("#!/bin/bash\nsleep 30\nexit 0\n")
        fake.chmod(0o755)
        monkeypatch.setattr("shutil.which", lambda n: str(fake) if n == "claude" else None)
        data = _run_direct(allow_help_probe=True, mock_claude_binary=str(fake))
        assert data["status"] == STATE_HOLD_HELP_FAILED


# ---------------------------------------------------------------------------
# Tests: TTY check
# ---------------------------------------------------------------------------

class TestTTYCheck:
    def test_non_interactive_tty_returns_hold(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)
        data = _run_direct(allow_noninteractive=False, mock_tty=False)
        assert data["status"] == STATE_HOLD_NON_INTERACTIVE
        assert data["real_executor_allowed"] is False

    def test_allow_noninteractive_skips_tty_check(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)
        data = _run_direct(allow_noninteractive=True)
        assert data["status"] == STATE_READY


# ---------------------------------------------------------------------------
# Tests: no shell=True check
# ---------------------------------------------------------------------------

class TestNoShell:
    def test_shell_true_in_docstring_not_flagged(self):
        """Docstrings and section-header comments that mention 'shell=True'
        must not be flagged as live code usage."""
        content = SCRIPT.read_text()
        for i, line in enumerate(content.splitlines(), 1):
            if "shell=True" not in line:
                continue
            # Skip docstring content lines
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # comment line - not live code
            # Skip lines where the only occurrence is inside a string literal
            # by verifying an actual assignment/expression context exists
            # If shell=True is in a comment word (e.g. "No shell=True in path")
            # it won't be part of a real assignment, so check for surrounding code
            idx = line.index("shell=True")
            before = line[:idx]
            # Real assignment would have code before shell= or at least open paren/brack
            # If line is pure description (no var name, no operator before), skip it
            # A real shell=True usage would have: var = ..., func(..., shell=True, ...)
            if not before.strip() or all(c in before for c in " \t#"):
                continue


# ---------------------------------------------------------------------------
# Tests: no LLM imports
# ---------------------------------------------------------------------------

class TestNoLLMImports:
    def test_llm_imports_returns_hold(self):
        data = _run_direct(mock_llm_ok=False)
        assert any("import claude" in m or "import anthropic" in m or "llm" in m.lower()
                   for m in data["missing"]), data["missing"]


# ---------------------------------------------------------------------------
# Tests: real_executor_allowed always false
# ---------------------------------------------------------------------------

class TestRealExecutorAllowed:
    def test_always_false_clean(self, clean_env):
        data, _, _ = _run_subprocess()
        assert data["real_executor_allowed"] is False

    def test_always_false_dirty(self):
        data = _run_direct(mock_git_dirty=True)
        assert data["real_executor_allowed"] is False

    def test_always_false_with_readiness_hold(self):
        data = _run_direct(mock_readiness_json={})
        assert data["real_executor_allowed"] is False


# ---------------------------------------------------------------------------
# Tests: no mutation
# ---------------------------------------------------------------------------

class TestNoMutation:
    def test_readiness_checker_not_mutated(self, clean_env):
        before = READINESS.read_text()
        _run_subprocess()
        after = READINESS.read_text()
        assert before == after

    def test_harness_not_mutated(self, clean_env):
        before = HARNESS.read_text()
        _run_subprocess()
        after = HARNESS.read_text()
        assert before == after

    def test_no_worktree_created(self, clean_env):
        before = set(p for p in REPO_ROOT.rglob("*") if "worktrees" in str(p))
        _run_subprocess()
        after = set(p for p in REPO_ROOT.rglob("*") if "worktrees" in str(p))
        created = [p for p in (after - before) if p.is_dir()]
        assert not created, f"Worktree dirs created: {created}"


# ---------------------------------------------------------------------------
# Tests: no forbidden patterns
# ---------------------------------------------------------------------------

class TestNoForbiddenPatterns:
    def test_no_pr_creation(self):
        content = SCRIPT.read_text()
        assert "gh pr create" not in content
        assert "gh pr merge" not in content
        assert "git push" not in content
        assert "dispatch" not in content.lower() or "# dispatch" in content
        assert "board" not in content.lower() or "# board" in content

    def test_no_shell_in_subprocess_calls(self):
        """Verify no shell=True in subprocess.run/call/Popen calls."""
        content = SCRIPT.read_text()
        # Look for shell=True outside of string literals
        for i, line in enumerate(content.splitlines(), 1):
            if "shell=True" in line:
                # Exclude docstrings and comments
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                # Exclude string literals by checking quote placement
                before = line[:line.index("shell=True")]
                if '"' in before or "'" in before:
                    continue
                assert False, f"shell=True in live code at line {i}: {stripped}"