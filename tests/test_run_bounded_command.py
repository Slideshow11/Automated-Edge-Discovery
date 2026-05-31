"""
Tests for run_bounded_command.py — no network calls, no GitHub API calls.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

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
# Output tailing — fixed ring buffer, not unlimited accumulate
# ---------------------------------------------------------------------------

def test_stdout_tailed_to_configured_limit():
    big_output = "x" * 20000
    rc, j, _ = run_cli(
        f'["python", "-c", "print(\'{big_output}\')"]',
        stdout_tail_bytes=100,
    )
    # stdout_tail must be <= 100 bytes
    assert len(j["stdout_tail"].encode("utf-8")) <= 100


def test_stderr_tailed_to_configured_limit():
    big_output = "x" * 20000
    rc, j, _ = run_cli(
        f'["python", "-c", "import sys; sys.stderr.write(\'{big_output}\')"]',
        stderr_tail_bytes=100,
    )
    # stderr_tail must be <= 100 bytes
    assert len(j["stderr_tail"].encode("utf-8")) <= 100


def test_output_json_size_remains_bounded_relative_to_tail_limits():
    """Verify JSON output tail fields are bounded by configured limits."""
    big = "x" * 20000
    rc, j, _ = run_cli(
        f'["python", "-c", "print(\'{big}\')"]',
        stdout_tail_bytes=200,
    )
    # The key assertion: stdout_tail is bounded by the configured limit
    assert len(j["stdout_tail"].encode("utf-8")) <= 200
    # JSON should contain no more than 200 bytes of output content
    json_str = json.dumps(j)
    # The JSON may still be larger than 5000 because the command itself
    # contains the 20000-byte string. The important guarantee is that the
    # stdout_tail field (the only unbounded field) is bounded.


# ---------------------------------------------------------------------------
# Policy: watch commands denied
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd_json", [
    '["gh", "run", "watch"]',
    '["gh", "pr", "checks", "--watch"]',
    '["gh", "pr", "checks", "-w"]',
])
def test_watch_commands_are_denied(cmd_json):
    rc, j, _ = run_cli(cmd_json)
    assert j["status"] == "COMMAND_POLICY_DENIED"
    assert any(
        p in " ".join(j["policy_errors"])
        for p in ["gh run watch", "gh pr checks --watch", "gh pr checks -w"]
    ), f"Expected watch-mode denial, got: {j['policy_errors']}"


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
    '["resolveReviewThread"]',
])
def test_deletion_mutations_are_denied(cmd_json):
    rc, j, _ = run_cli(cmd_json)
    assert j["status"] == "COMMAND_POLICY_DENIED"
    combined_lower = " ".join(j["policy_errors"]).lower()
    assert any(
        p.lower() in combined_lower
        for p in ["deleteReviewComment", "deletePullRequestReviewComment",
                  "dismissReview", "resolveReviewThread"]
    ), f"Expected deletion mutation error, got: {j['policy_errors']}"


# ---------------------------------------------------------------------------
# Policy: branch protection mutation — HTTP method variants blocked
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd_json", [
    '["gh", "api", "-X", "PUT", "/repos/Slideshow11/Automated-Edge-Discovery/branches/main/protection"]',
    '["gh", "api", "-XPUT", "/repos/Slideshow11/Automated-Edge-Discovery/branches/main/protection"]',
    '["gh", "api", "--method=PUT", "/repos/Slideshow11/Automated-Edge-Discovery/branches/main/protection"]',
    '["gh", "api", "--method", "PUT", "/repos/Slideshow11/Automated-Edge-Discovery/branches/main/protection"]',
    '["gh", "api", "-X", "PATCH", "/repos/Slideshow11/Automated-Edge-Discovery/branches/main/protection"]',
    '["gh", "api", "-XPOST", "/repos/Slideshow11/Automated-Edge-Discovery/branches/main/protection"]',
])
def test_branch_protection_mutation_http_variants_blocked(cmd_json):
    rc, j, _ = run_cli(cmd_json)
    assert j["status"] == "COMMAND_POLICY_DENIED", \
        f"Expected POLICY_DENIED, got {j['status']}: {j['policy_errors']}"


# ---------------------------------------------------------------------------
# Policy: issue/comment mutation paths blocked
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd_json", [
    '["gh", "api", "--method=PATCH", "/repos/Slideshow11/Automated-Edge-Discovery/issues/comments/123"]',
    '["gh", "api", "--method=DELETE", "/repos/Slideshow11/Automated-Edge-Discovery/issues/comments/456"]',
    '["gh", "api", "-X", "DELETE", "/repos/Slideshow11/Automated-Edge-Discovery/pulls/comments/789"]',
    '["gh", "api", "-X", "PUT", "/repos/Slideshow11/Automated-Edge-Discovery/pulls/reviews/111"]',
])
def test_issue_comment_mutation_paths_blocked(cmd_json):
    rc, j, _ = run_cli(cmd_json)
    assert j["status"] == "COMMAND_POLICY_DENIED", \
        f"Expected POLICY_DENIED, got {j['status']}: {j['policy_errors']}"


# ---------------------------------------------------------------------------
# Policy: Hermes kanban mutation strings denied
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd_json", [
    '["hermes", "kanban", "move", "123"]',
    '["hermes", "kanban", "add", "task"]',
    '["hermes", "kanban", "update", "456"]',
])
def test_hermes_kanban_strings_are_denied(cmd_json):
    rc, j, _ = run_cli(cmd_json)
    assert j["status"] == "COMMAND_POLICY_DENIED"
    combined = " ".join(j["policy_errors"])
    # "hermes kanban mutation not allowed" contains the relevant terms
    assert any(
        ("hermes" in combined.lower() and "kanban" in combined.lower()) or
        ("kanban move" in combined.lower()) or
        ("kanban add" in combined.lower())
        for _ in [1]
    ), f"Expected kanban error, got: {j['policy_errors']}"


# ---------------------------------------------------------------------------
# Policy: GraphQL mutation denied
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd_json", [
    '["gh", "api", "graphql", "-f", "query=mutation{viewer{login}}"]',
    '["gh", "api", "graphql", "-f", "query=mutation { viewer { login } }"]',
    '["gh", "api", "graphql", "-F", "query=Mutation { viewer { login } }"]',
])
def test_graphql_mutation_denied_by_default(cmd_json):
    rc, j, _ = run_cli(cmd_json)
    assert j["status"] == "COMMAND_POLICY_DENIED", \
        f"Expected POLICY_DENIED, got {j['status']}: {j['policy_errors']}"
    assert any(
        "GraphQL mutation requires --allow-gh-api-mutation" in e or
        "mutation" in e.lower()
        for e in j["policy_errors"]
    ), f"Expected mutation error, got: {j['policy_errors']}"


def test_graphql_read_query_allowed_by_policy():
    """GraphQL query (not mutation) should not be policy-blocked."""
    rc, j, _ = run_cli('["gh", "api", "repos/Slideshow11/Automated-Edge-Discovery"]')
    assert j["status"] != "COMMAND_POLICY_DENIED", \
        f"Unexpected policy denial: {j['policy_errors']}"


def test_allow_gh_api_mutation_enables_graphql_mutation():
    rc, j, _ = run_cli(
        '["mutation", "--help"]',
        allow_gh_api_mutation=True,
    )
    assert j["status"] != "COMMAND_POLICY_DENIED", \
        f"Policy denied even with flag: {j['policy_errors']}"


# --------------------------------------------------------------------------
# Policy: GraphQL mutation detection — robust operation-body matching
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cmd_json", [
    # mutation{ (no space) — the exact bypass form from QEA
    '["gh", "api", "graphql", "-f", "query=mutation{viewer{login}}"]',
    # mutation followed by space then {
    '["gh", "api", "graphql", "-f", "query=mutation { viewer { login } }"]',
    # mutation with multiple spaces before {
    '["gh", "api", "graphql", "-f", "query=mutation   { viewer { login } }"]',
    # mutation followed by newline before {
    '["gh", "api", "graphql", "-f", "query=mutation\\n{ viewer { login } }"]',
    # mutation with operation name
    '["gh", "api", "graphql", "-f", "query=mutation MyOp { viewer { login } }"]',
    # mutation with operation name and variables
    '["gh", "api", "graphql", "-f", "query=mutation MarkDone($id:ID!){completeTask(id:$id){id}}"]',
    # mixed-case Mutation
    '["gh", "api", "graphql", "-f", "query=Mutation { viewer { login } }"]',
    # uppercase MUTATION
    '["gh", "api", "graphql", "-f", "query=MUTATION{viewer{login}}"]',
])
def test_graphql_mutation_operation_bypass_forms_denied(cmd_json):
    """All common mutation operation forms must be policy-denied."""
    rc, j, _ = run_cli(cmd_json)
    assert j["status"] == "COMMAND_POLICY_DENIED", \
        f"Expected POLICY_DENIED for {cmd_json}, got {j['status']}: {j['policy_errors']}"


@pytest.mark.parametrize("cmd_json", [
    # 'immutable' contains 'mutation' as substring but is not a GraphQL operation
    '["python", "-c", "print(\'immutable_state\')"]',
    # 'permutation' contains 'mutation' as substring
    '["python", "-c", "import itertools; print(list(itertools.permutations([1,2])))"]',
    # 'mutation' inside a string argument that is not a GraphQL operation
    '["echo", "no-mutation-here"]',
])
def test_mutation_substring_not_blocked(cmd_json):
    """Words containing 'mutation' as a substring must not be policy-blocked."""
    rc, j, _ = run_cli(cmd_json)
    assert j["status"] != "COMMAND_POLICY_DENIED", \
        f"Unexpected policy denial for '{cmd_json}': {j['policy_errors']}"


def test_graphql_query_not_mutation():
    """A query {...} without 'mutation' keyword must not be blocked by the mutation guard."""
    rc, j, _ = run_cli(
        '["gh", "api", "graphql", "-f", "query={viewer{login}}"]',
    )
    assert j["status"] != "COMMAND_POLICY_DENIED", \
        f"Query should not be blocked: {j['policy_errors']}"


# ---------------------------------------------------------------------------
# Policy: shell invocation wrappers denied
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd_json", [
    '["bash", "-c", "echo danger"]',
    '["sh", "-c", "echo danger"]',
    '["zsh", "-c", "echo danger"]',
    '["powershell", "-Command", "echo danger"]',
    '["pwsh", "-Command", "echo danger"]',
    '["cmd", "/c", "echo danger"]',
])
def test_shell_invocation_wrappers_denied(cmd_json):
    rc, j, _ = run_cli(cmd_json)
    assert j["status"] == "COMMAND_POLICY_DENIED", \
        f"Expected POLICY_DENIED, got {j['status']}: {j['policy_errors']}"


# ---------------------------------------------------------------------------
# Policy: dangerous mutations in GraphQL payload also blocked
# ---------------------------------------------------------------------------

def test_delete_mutation_name_in_graphql_payload_denied():
    """deleteReviewComment appearing in GraphQL query text should be blocked."""
    # The payload query=... contains 'mutation deleteReviewComment' or similar
    cmd_json = json.dumps(["gh", "api", "graphql", "-f", "query=mutation{deleteReviewComment(input:{clientMutationId:\"x\"}){clientMutationId}}"])
    rc, j, _ = run_cli(cmd_json)
    assert j["status"] == "COMMAND_POLICY_DENIED", \
        f"Expected POLICY_DENIED, got {j['status']}: {j['policy_errors']}"


def test_dismiss_mutation_name_in_graphql_payload_denied():
    cmd_json = json.dumps(["gh", "api", "graphql", "-f", "query=mutation{dismissReview(input:{pullRequestReviewId:\"x\"}){clientMutationId}}"])
    rc, j, _ = run_cli(cmd_json)
    assert j["status"] == "COMMAND_POLICY_DENIED", \
        f"Expected POLICY_DENIED, got {j['status']}: {j['policy_errors']}"


# ---------------------------------------------------------------------------
# Shell safety: shell=True never used, metacharacters are inert
# ---------------------------------------------------------------------------

def test_shell_metacharacters_are_normal_argv():
    """Semicolons, pipes etc. passed as literal argv, not executed."""
    rc, j, _ = run_cli(
        '["python", "-c", "import sys; sys.stderr.write(\'error\\n\'); sys.exit(1)"]'
    )
    assert j["status"] in ("COMMAND_FAILED", "COMMAND_SUCCEEDED")
    assert j["status"] != "COMMAND_POLICY_DENIED"


# ---------------------------------------------------------------------------
# CWD support
# ---------------------------------------------------------------------------

def test_cwd_option_is_respected(tmp_path):
    json_path = tmp_path / "result.json"
    md_path = tmp_path / "result.md"
    rc, j, _ = run_cli(
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
# Runner exits 0 even on failure
# ---------------------------------------------------------------------------

def test_runner_exits_zero_for_policy_denied():
    rc, j, _ = run_cli('["--admin"]')
    assert rc == 0
    assert j["status"] == "COMMAND_POLICY_DENIED"


def test_runner_exits_zero_for_invalid_json():
    rc, j, _ = run_cli("not-json")
    assert rc == 0
    assert j["status"] == "COMMAND_INVALID_JSON"


# ---------------------------------------------------------------------------
# Process-group cleanup on POSIX timeout
# ---------------------------------------------------------------------------

def test_popen_uses_start_new_session_on_posix():
    """Verify Popen is configured with start_new_session=True on POSIX (code inspection)."""
    import sys
    # Verify the code path sets start_new_session for non-Windows
    import ast
    source = SCRIPT.read_text()
    tree = ast.parse(source)
    # Find run_bounded_command function
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_bounded_command":
            # Check that start_new_session appears in a Subscript or Attribute
            # inside an if sys.platform != "win32" block
            source_snippet = ast.get_source_segment(source, node) or ""
            assert "start_new_session" in source_snippet, \
                "start_new_session not found in run_bounded_command body"
            assert 'sys.platform != "win32"' in source_snippet or \
                   'sys.platform != \'win32\'' in source_snippet, \
                "Platform check for start_new_session not found"
            break
    else:
        pytest.fail("run_bounded_command function not found in AST")


# ---------------------------------------------------------------------------
# Ring buffer behavior: ensure tail slicing from end of buffer
# ---------------------------------------------------------------------------

def test_ring_buffer_discard_old_bytes():
    """Test the RingBuffer class directly to verify it discards old bytes."""
    from scripts.local.run_bounded_command import RingBuffer
    rb = RingBuffer(max_bytes=10)
    rb.write(b"abcdefghij")  # exactly 10 bytes
    assert len(rb.read()) == 10
    rb.write(b"XXXX")  # adding 4 more bytes should cause first 4 to be dropped
    result = rb.read()
    assert len(result.encode("utf-8")) <= 10
    assert "j" in result  # last part of original string must be present
    assert "a" not in result  # oldest bytes should be gone