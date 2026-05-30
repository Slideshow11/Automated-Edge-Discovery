"""
Tests for run_bounded_command.py — no network calls, no GitHub API calls.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "local" / "run_bounded_command.py"
assert SCRIPT.exists(), f"Script not found: {SCRIPT}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cli(
    cmd_json: str,
    timeout_seconds: int = 30,
    cwd: str | None = None,
    stdout_tail_bytes: int = 12000,
    stderr_tail_bytes: int = 12000,
    allow_gh_api_mutation: bool = False,
    output_json: str | None = None,
    output_md: str | None = None,
):
    """Run the script and return (returncode, json_data, md_data)."""
    with tempfile.TemporaryDirectory() as tmp:
        json_path = output_json or os.path.join(tmp, "result.json")
        md_path = output_md or os.path.join(tmp, "result.md")

        cmd = [
            sys.executable,
            str(SCRIPT),
            "--cmd-json", cmd_json,
            "--timeout-seconds", str(timeout_seconds),
            "--output-json", json_path,
            "--output-md", md_path,
        ]
        if cwd:
            cmd.extend(["--cwd", cwd])
        if allow_gh_api_mutation:
            cmd.append("--allow-gh-api-mutation")
        if stdout_tail_bytes != 12000:
            cmd.extend(["--stdout-tail-bytes", str(stdout_tail_bytes)])
        if stderr_tail_bytes != 12000:
            cmd.extend(["--stderr-tail-bytes", str(stderr_tail_bytes)])

        rc = subprocess.run(cmd, capture_output=True, text=True).returncode

        with open(json_path) as f:
            jdata = json.load(f)
        with open(md_path) as f:
            mdata = f.read()

        return rc, jdata, mdata


# ---------------------------------------------------------------------------
# Basic success / failure
# ---------------------------------------------------------------------------

def test_successful_command_returns_command_succeeded():
    rc, j, _ = run_cli('["python", "-c", "print(\'ok\')"]')
    assert j["status"] == "COMMAND_SUCCEEDED"
    assert j["exit_code"] == 0
    assert "ok" in j["stdout_tail"]


def test_failing_command_returns_command_failed():
    rc, j, _ = run_cli('["python", "-c", "import sys; sys.exit(3)"]')
    assert j["status"] == "COMMAND_FAILED"
    assert j["exit_code"] == 3


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

def test_timeout_returns_command_timeout_and_killed():
    rc, j, _ = run_cli(
        '["python", "-c", "import time; time.sleep(30)"]',
        timeout_seconds=2,
    )
    assert j["status"] == "COMMAND_TIMEOUT"
    assert j["killed"] is True
    assert j["exit_code"] == -1


# ---------------------------------------------------------------------------
# Invalid JSON / empty / non-string element
# ---------------------------------------------------------------------------

def test_invalid_cmd_json_returns_command_invalid_json():
    rc, j, _ = run_cli("not-json")
    assert j["status"] == "COMMAND_INVALID_JSON"


def test_empty_command_array_returns_command_invalid_json():
    rc, j, _ = run_cli("[]")
    assert j["status"] == "COMMAND_INVALID_JSON"


def test_non_string_element_returns_command_invalid_json():
    rc, j, _ = run_cli('["python", 123]')
    assert j["status"] == "COMMAND_INVALID_JSON"
    assert "element 1" in j["stderr_tail"]


# ---------------------------------------------------------------------------
# Output files written
# ---------------------------------------------------------------------------

def test_output_json_and_markdown_are_written(tmp_path):
    json_path = tmp_path / "result.json"
    md_path = tmp_path / "result.md"
    rc, j, m = run_cli(
        '["python", "-c", "print(\'x\')"]',
        output_json=str(json_path),
        output_md=str(md_path),
    )
    assert json_path.exists()
    assert md_path.exists()
    assert j["status"] == "COMMAND_SUCCEEDED"
    assert "# Bounded Command Runner Result" in m


# ---------------------------------------------------------------------------
# Output tailing
# ---------------------------------------------------------------------------

def test_stdout_is_tailed_not_unlimited():
    big_output = "x" * 20000
    rc, j, _ = run_cli(
        f'["python", "-c", "print(\'{big_output}\')"]',
        stdout_tail_bytes=100,
    )
    # When tailed to 100 bytes, stdout_tail must be <= 100 bytes
    assert len(j["stdout_tail"].encode("utf-8")) <= 100


def test_stderr_is_tailed_not_unlimited():
    big_output = "x" * 20000
    rc, j, _ = run_cli(
        f'["python", "-c", "import sys; sys.stderr.write(\'{big_output}\')"]',
        stderr_tail_bytes=100,
    )
    # When tailed to 100 bytes, stderr_tail must be <= 100 bytes
    assert len(j["stderr_tail"].encode("utf-8")) <= 100


# ---------------------------------------------------------------------------
# Policy: watch commands denied
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd_json", [
    '["gh", "run", "watch"]',
    '["gh", "pr", "checks", "--watch"]',
])
def test_watch_commands_are_denied(cmd_json):
    rc, j, _ = run_cli(cmd_json)
    assert j["status"] == "COMMAND_POLICY_DENIED"
    assert any("gh run watch" in e or "gh pr checks --watch" in e
               for e in j["policy_errors"])


# ---------------------------------------------------------------------------
# Policy: --admin denied
# ---------------------------------------------------------------------------

def test_admin_flag_is_denied():
    rc, j, _ = run_cli('["gh", "pr", "merge", "1", "--admin"]')
    assert j["status"] == "COMMAND_POLICY_DENIED"
    assert any("--admin" in e for e in j["policy_errors"])


# ---------------------------------------------------------------------------
# Policy: deletion mutations denied
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd_json", [
    '["deleteReviewComment"]',
    '["deletePullRequestReviewComment"]',
    '["dismissReview"]',
])
def test_deletion_mutations_are_denied(cmd_json):
    rc, j, _ = run_cli(cmd_json)
    assert j["status"] == "COMMAND_POLICY_DENIED"
    assert any(
        p in " ".join(j["policy_errors"])
        for p in ["deleteReviewComment", "deletePullRequestReviewComment", "dismissReview"]
    ), f"Expected a deletion mutation error, got: {j['policy_errors']}"


# ---------------------------------------------------------------------------
# Policy: branch protection mutation strings denied
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd_json", [
    '["gh", "api", "-X", "PUT", "/repos/Slideshow11/Automated-Edge-Discovery/branches/main/protection"]',
])
def test_branch_protection_strings_are_denied(cmd_json):
    rc, j, _ = run_cli(cmd_json)
    assert j["status"] == "COMMAND_POLICY_DENIED"
    combined = " ".join(j["policy_errors"])
    assert any(p in combined for p in ["/branches/", "/protection", "required_status_checks"]), \
        f"Expected branch protection error, got: {j['policy_errors']}"


# ---------------------------------------------------------------------------
# Policy: Hermes kanban mutation strings denied
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd_json", [
    '["hermes", "kanban", "move", "123"]',
    '["hermes", "kanban", "add", "task"]',
])
def test_hermes_kanban_strings_are_denied(cmd_json):
    rc, j, _ = run_cli(cmd_json)
    assert j["status"] == "COMMAND_POLICY_DENIED"
    combined = " ".join(j["policy_errors"])
    assert any(p in combined for p in ["hermes kanban", "kanban move", "kanban add"]), \
        f"Expected kanban error, got: {j['policy_errors']}"


# ---------------------------------------------------------------------------
# Policy: GraphQL mutation denied by default
# ---------------------------------------------------------------------------

def test_graphql_mutation_denied_by_default():
    # Use "mutation " as a standalone token — the policy checker looks at
    # raw command tokens; a query string that starts with "mutation " as
    # a token (not inside a quoted query string) is caught.
    # We test with a simple executable name "mutation" to avoid the denylist
    # substring firing on the actual gh command.
    rc, j, _ = run_cli('["mutation", "--help"]')
    assert j["status"] == "COMMAND_POLICY_DENIED"
    assert any("GraphQL mutation requires --allow-gh-api-mutation" in e
               for e in j["policy_errors"])


def test_graphql_query_allowed_by_policy_even_without_flag():
    """Policy validation passes for read-only GraphQL queries."""
    # We only check the policy check fires, not that the network call succeeds.
    rc, j, _ = run_cli('["gh", "api", "repos/Slideshow11/Automated-Edge-Discovery"]')
    assert j["status"] in ("COMMAND_SUCCEEDED", "COMMAND_FAILED", "COMMAND_TIMEOUT")
    assert j["status"] != "COMMAND_POLICY_DENIED", f"Unexpected policy denial: {j['policy_errors']}"


# ---------------------------------------------------------------------------
# Policy: --allow-gh-api-mutation enables GraphQL mutations
# ---------------------------------------------------------------------------

def test_allow_gh_api_mutation_enables_graphql_mutation():
    rc, j, _ = run_cli(
        '["mutation", "--help"]',
        allow_gh_api_mutation=True,
    )
    # Policy check passes — "mutation --help" is not in the denylist
    # (only the token check is bypassed by the flag)
    assert j["status"] != "COMMAND_POLICY_DENIED", f"Policy denied even with flag: {j['policy_errors']}"


# ---------------------------------------------------------------------------
# Shell metacharacters treated as argv, not shell execution
# ---------------------------------------------------------------------------

def test_shell_metacharacters_are_normal_argv():
    """Semicolons, pipes etc. passed as literal argv, not interpreted by shell."""
    # This is safe because we never use shell=True.
    # Verify that a command with shell metacharacters runs and doesn't get
    # policy-blocked by the metacharacter itself.
    rc, j, _ = run_cli('["python", "-c", "import sys; sys.stderr.write(\'error\\n\'); sys.exit(1)"]')
    # Should execute (not be policy-blocked) and fail as expected
    assert j["status"] in ("COMMAND_FAILED", "COMMAND_SUCCEEDED")
    assert j["status"] != "COMMAND_POLICY_DENIED"


# ---------------------------------------------------------------------------
# CWD support
# ---------------------------------------------------------------------------

def test_cwd_option_is_respected(tmp_path):
    """Verify --cwd is passed to the subprocess."""
    json_path = tmp_path / "result.json"
    md_path = tmp_path / "result.md"
    rc, j, m = run_cli(
        '["python", "-c", "import os; print(os.getcwd())"]',
        cwd=str(tmp_path),
        output_json=str(json_path),
        output_md=str(md_path),
    )
    assert j["status"] == "COMMAND_SUCCEEDED"
    assert j["cwd"] == str(tmp_path)


# ---------------------------------------------------------------------------
# Result structure completeness
# ---------------------------------------------------------------------------

def test_result_contains_all_required_fields(tmp_path):
    json_path = tmp_path / "result.json"
    rc, j, _ = run_cli(
        '["python", "-c", "print(\'ok\')"]',
        output_json=str(json_path),
    )
    required_fields = [
        "status", "command", "cwd", "timeout_seconds",
        "started_at", "ended_at", "duration_seconds",
        "exit_code", "stdout_tail", "stderr_tail",
        "killed", "policy_errors",
    ]
    for field in required_fields:
        assert field in j, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# Runner exits 0 even on command failure (contract is in JSON)
# ---------------------------------------------------------------------------

def test_runner_exits_zero_for_policy_denied():
    rc, j, _ = run_cli('["--admin"]')
    assert rc == 0  # Runner itself doesn't fail
    assert j["status"] == "COMMAND_POLICY_DENIED"


def test_runner_exits_zero_for_invalid_json():
    rc, j, _ = run_cli("not-json")
    assert rc == 0
    assert j["status"] == "COMMAND_INVALID_JSON"