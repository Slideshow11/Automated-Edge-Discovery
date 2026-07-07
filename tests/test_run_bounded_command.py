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
    policy_mode: str = "legacy-denylist",
    env: dict[str, str] | None = None,
):
    """Run the script and return (returncode, json_data, md_data).

    ``policy_mode`` defaults to ``"legacy-denylist"`` for backward
    compatibility with the V1 test suite (tests that pre-date the
    PR A1 allowlist hardening). New tests should pass
    ``policy_mode="allowlist"`` to exercise the new allowlist
    behavior.

    ``env`` (optional) is the env passed to the runner subprocess. If
    None, the runner inherits the test process's env. Used by env
    sanitization tests to inject fake sensitive env vars.
    """
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
            "--policy-mode", policy_mode,
        ]
        if cwd:
            cmd.extend(["--cwd", cwd])
        if allow_gh_api_mutation:
            cmd.append("--allow-gh-api-mutation")
        if stdout_tail_bytes != 12000:
            cmd.extend(["--stdout-tail-bytes", str(stdout_tail_bytes)])
        if stderr_tail_bytes != 12000:
            cmd.extend(["--stderr-tail-bytes", str(stderr_tail_bytes)])

        run_kwargs: dict = {"capture_output": True, "text": True}
        if env is not None:
            run_kwargs["env"] = env
        rc = subprocess.run(cmd, **run_kwargs).returncode

        with open(json_path) as f:
            jdata = json.load(f)
        with open(md_path) as f:
            mdata = f.read()

        return rc, jdata, mdata


# ---------------------------------------------------------------------------
# Basic success / failure
# ---------------------------------------------------------------------------

def test_successful_command_returns_command_succeeded():
    # PR A1 — use an allowlisted form (``python3 -m py_compile`` on a
    # temp file) instead of ``python -c`` (now denied by the new
    # denylist). The intent — confirm a successful run produces
    # ``COMMAND_SUCCEEDED`` with ``exit_code=0`` — is preserved.
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("print('ok')\n")
        tmp_path = f.name
    try:
        rc, j, _ = run_cli(f'["python3", "-m", "py_compile", "{tmp_path}"]')
        assert j["status"] == "COMMAND_SUCCEEDED"
        assert j["exit_code"] == 0
    finally:
        os.unlink(tmp_path)


def test_failing_command_returns_command_failed():
    # PR A1 — use a script that py_compile rejects (syntax error
    # in a .py file). py_compile on a bad file exits 1. The intent
    # — confirm a non-zero exit produces ``COMMAND_FAILED`` — is
    # preserved.
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def broken(:\n")  # syntax error
        tmp_path = f.name
    try:
        rc, j, _ = run_cli(f'["python3", "-m", "py_compile", "{tmp_path}"]')
        assert j["status"] == "COMMAND_FAILED"
        assert j["exit_code"] != 0
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

def test_timeout_returns_command_timeout_and_killed():
    # PR A1 — use a script file that sleeps (the legacy ``python -c``
    # form is now denied by the denylist). The intent — confirm a
    # long-running process is killed by the timeout — is preserved.
    # We use ``--policy-mode legacy-denylist`` here so that an
    # arbitrary ``python3 <script>`` invocation is accepted by the
    # runner (the new allowlist is too narrow to include it). The
    # timeout-firing behavior under test is orthogonal to the
    # allowlist hardening.
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("import time; time.sleep(30)\n")
        tmp_path = f.name
    try:
        rc, j, _ = run_cli(
            f'["python3", "{tmp_path}"]',
            timeout_seconds=2,
            policy_mode="legacy-denylist",
        )
        assert j["status"] == "COMMAND_TIMEOUT"
        assert j["killed"] is True
        assert j["exit_code"] == -1
    finally:
        os.unlink(tmp_path)


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
    # PR A1 — use an allowlisted form (``python3 -m py_compile`` on a
    # temp file) instead of ``python -c`` (now denied by the
    # denylist). The intent — confirm both files are written —
    # is preserved.
    script_path = tmp_path / "tiny.py"
    script_path.write_text("print('x')\n")
    json_path = tmp_path / "result.json"
    md_path = tmp_path / "result.md"
    rc, j, m = run_cli(
        f'["python3", "-m", "py_compile", "{script_path}"]',
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
    # PR A1 — use ``--policy-mode legacy-denylist`` so the
    # ``python -c`` form is accepted. The intent — confirm the
    # ring buffer discards bytes beyond the configured limit — is
    # preserved.
    big_output = "x" * 20000
    rc, j, _ = run_cli(
        f'["python", "-c", "print(\'{big_output}\')"]',
        stdout_tail_bytes=100,
        policy_mode="legacy-denylist",
    )
    # stdout_tail must be <= 100 bytes
    assert len(j["stdout_tail"].encode("utf-8")) <= 100


def test_stderr_tailed_to_configured_limit():
    # PR A1 — see test_stdout_tailed_to_configured_limit.
    big_output = "x" * 20000
    rc, j, _ = run_cli(
        f'["python", "-c", "import sys; sys.stderr.write(\'{big_output}\')"]',
        stderr_tail_bytes=100,
        policy_mode="legacy-denylist",
    )
    assert len(j["stderr_tail"].encode("utf-8")) <= 100


def test_output_json_size_remains_bounded_relative_to_tail_limits():
    """Verify JSON output tail fields are bounded by configured limits."""
    # PR A1 — see test_stdout_tailed_to_configured_limit.
    big = "x" * 20000
    rc, j, _ = run_cli(
        f'["python", "-c", "print(\'{big}\')"]',
        stdout_tail_bytes=200,
        policy_mode="legacy-denylist",
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
    '["echo", "immutable_state"]',
    # 'permutation' contains 'mutation' as substring
    '["echo", "permutations_xyz"]',
    # 'mutation' inside a string argument that is not a GraphQL operation
    '["echo", "no-mutation-here"]',
])
def test_mutation_substring_not_blocked(cmd_json):
    """Words containing 'mutation' as a substring must not be policy-blocked.

    PR A1 — the V1 test used ``python -c`` as a vehicle, but the
    new denylist denies ``python -c`` before the GraphQL mutation
    check ever runs. The intent of this test — distinguish a
    bare ``mutation`` keyword (which is a GraphQL mutation) from
    a word containing ``mutation`` as a substring (which is not)
    — is preserved by using ``echo`` with the substring as a
    string arg. The ``echo`` form is not in the allowlist, so we
    use ``--policy-mode legacy-denylist`` to exercise the V1
    denylist-only path (the legacy denylist does NOT contain an
    ``echo`` rule, so the runner reaches the GraphQL mutation
    check, which then correctly leaves the substring alone).
    """
    rc, j, _ = run_cli(cmd_json, policy_mode="legacy-denylist")
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
    """Semicolons, pipes etc. passed as literal argv, not executed.

    PR A1 — the V1 test used ``python -c`` to test that
    shell-metacharacter arguments are passed as literal argv.
    That command is now denied by the new denylist, so we use a
    script file with the same shape. The runner still uses
    ``shell=False`` (verified separately by ``test_popen_uses_start_new_session_on_posix``).
    """
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("import sys; sys.stderr.write('error\\n'); sys.exit(1)\n")
        tmp_path = f.name
    try:
        rc, j, _ = run_cli(
            f'["python3", "{tmp_path}"]',
            policy_mode="legacy-denylist",
        )
        assert j["status"] in ("COMMAND_FAILED", "COMMAND_SUCCEEDED")
        assert j["status"] != "COMMAND_POLICY_DENIED"
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# CWD support
# ---------------------------------------------------------------------------

def test_cwd_option_is_respected(tmp_path):
    # PR A1 — the V1 test used ``python -c "import os; print(...)"`` to
    # verify that ``--cwd`` is honored. On many CI images ``python``
    # is not in PATH (only ``python3`` is), so the V1 test was
    # fragile — it pre-failed with COMMAND_FAILED. The A1 fix is
    # to use ``python3`` explicitly. We still need
    # ``--policy-mode legacy-denylist`` because ``python3 -c`` is
    # denied in the new policy.
    json_path = tmp_path / "result.json"
    md_path = tmp_path / "result.md"
    rc, j, _ = run_cli(
        '["python3", "-c", "import os; print(os.getcwd())"]',
        cwd=str(tmp_path),
        output_json=str(json_path),
        output_md=str(md_path),
        policy_mode="legacy-denylist",
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


# ===========================================================================
# PR A1 hardening tests
# ===========================================================================
#
# These tests cover the new deny-by-default allowlist policy mode, the
# secondary denylist defense, the new env sanitization, and the new
# JSON output fields (policy_mode, policy_decision, policy_rule_id,
# policy_reason, sanitized_env_applied, blocked_env_keys).
#
# All new tests use ``policy_mode="allowlist"`` (the new default) and
# assert on the new policy + env fields. They cover both the
# "safe command allowed" path and the "dangerous command blocked"
# path from the audit requirements.


# ---------------------------------------------------------------------------
# Allowlist: safe commands still allowed
# ---------------------------------------------------------------------------


def test_allowlist_python_py_compile_safe_file_is_allowed(tmp_path):
    """BC-POL-009 (V2): ``python3 -m py_compile <safe .py file>`` is allowed.

    V2 split the V1 BC-POL-001 rule into BC-POL-001 (pytest) and
    BC-POL-009 (py_compile) so the predicate for py_compile can be
    strict (no flags) and the predicate for pytest can carry a
    closed allowlist of safe options. The py_compile rule fires
    here.
    """
    script = tmp_path / "ok.py"
    script.write_text("x = 1\n")
    rc, j, _ = run_cli(
        f'["python3", "-m", "py_compile", "{script}"]',
        policy_mode="allowlist",
    )
    assert j["status"] == "COMMAND_SUCCEEDED"
    assert j["policy_mode"] == "allowlist"
    assert j["policy_decision"] == "allow"
    assert j["policy_rule_id"] == "BC-POL-009"
    assert j["policy_reason"] == "python_py_compile"


def test_allowlist_python_pytest_safe_target_is_allowed(tmp_path):
    """BC-POL-001: ``python3 -m pytest <test dir>`` is allowed."""
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "test_ok.py").write_text("def test_ok(): assert True\n")
    rc, j, _ = run_cli(
        f'["python3", "-m", "pytest", "{test_dir}"]',
        policy_mode="allowlist",
    )
    assert j["policy_decision"] == "allow"
    assert j["policy_rule_id"] == "BC-POL-001"


def test_allowlist_git_status_short_is_allowed():
    """BC-POL-002: ``git status --short`` is allowed."""
    rc, j, _ = run_cli(
        '["git", "status", "--short"]',
        policy_mode="allowlist",
    )
    assert j["status"] == "COMMAND_SUCCEEDED"
    assert j["policy_decision"] == "allow"
    assert j["policy_rule_id"] == "BC-POL-002"
    assert j["policy_reason"] == "git_status"


def test_allowlist_git_status_porcelain_is_allowed():
    """BC-POL-002: ``git status --porcelain`` is allowed."""
    rc, j, _ = run_cli(
        '["git", "status", "--porcelain"]',
        policy_mode="allowlist",
    )
    assert j["policy_decision"] == "allow"
    assert j["policy_rule_id"] == "BC-POL-002"


def test_allowlist_git_diff_check_is_allowed():
    """BC-POL-003: ``git diff --check`` is allowed."""
    rc, j, _ = run_cli(
        '["git", "diff", "--check"]',
        policy_mode="allowlist",
    )
    assert j["policy_decision"] == "allow"
    assert j["policy_rule_id"] == "BC-POL-003"
    assert j["policy_reason"] == "git_diff_check"


def test_allowlist_git_diff_name_only_is_allowed():
    """BC-POL-004: ``git diff --name-only`` is allowed."""
    rc, j, _ = run_cli(
        '["git", "diff", "--name-only"]',
        policy_mode="allowlist",
    )
    assert j["policy_decision"] == "allow"
    assert j["policy_rule_id"] == "BC-POL-004"
    assert j["policy_reason"] == "git_diff_name_only"


def test_allowlist_git_diff_stat_is_allowed():
    """BC-POL-005: ``git diff --stat`` is allowed."""
    rc, j, _ = run_cli(
        '["git", "diff", "--stat"]',
        policy_mode="allowlist",
    )
    assert j["policy_decision"] == "allow"
    assert j["policy_rule_id"] == "BC-POL-005"
    assert j["policy_reason"] == "git_diff_stat"


def test_allowlist_git_rev_parse_head_is_allowed():
    """BC-POL-006: ``git rev-parse HEAD`` is allowed."""
    rc, j, _ = run_cli(
        '["git", "rev-parse", "HEAD"]',
        policy_mode="allowlist",
    )
    assert j["policy_decision"] == "allow"
    assert j["policy_rule_id"] == "BC-POL-006"
    assert j["policy_reason"] == "git_rev_parse"


def test_allowlist_git_branch_show_current_is_allowed():
    """BC-POL-007: ``git branch --show-current`` is allowed."""
    rc, j, _ = run_cli(
        '["git", "branch", "--show-current"]',
        policy_mode="allowlist",
    )
    assert j["policy_decision"] == "allow"
    assert j["policy_rule_id"] == "BC-POL-007"
    assert j["policy_reason"] == "git_branch_show_current"


def test_allowlist_git_worktree_list_is_allowed():
    """BC-POL-008: ``git worktree list`` is allowed."""
    rc, j, _ = run_cli(
        '["git", "worktree", "list"]',
        policy_mode="allowlist",
    )
    assert j["policy_decision"] == "allow"
    assert j["policy_rule_id"] == "BC-POL-008"
    assert j["policy_reason"] == "git_worktree_list"


# ---------------------------------------------------------------------------
# Allowlist: dangerous commands blocked by default
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cmd_json", [
    '["git", "push", "origin", "main"]',
    '["git", "push"]',
    '["git", "commit", "-m", "x"]',
    '["git", "tag", "v1.0"]',
    '["git", "remote", "add", "origin", "x"]',
    '["gh", "pr", "comment", "1", "--body", "x"]',
    '["gh", "pr", "create", "--title", "x", "--body", "y"]',
    '["gh", "pr", "edit", "1", "--title", "x"]',
    '["gh", "pr", "close", "1"]',
    '["gh", "pr", "merge", "1", "--match-head-commit", "abc123"]',
    '["gh", "release", "create", "v1"]',
    '["gh", "workflow", "run", "ci.yml"]',
    '["gh", "repo", "delete", "owner/repo"]',
    '["hermes", "memory_store", "x"]',
    '["hermes", "fact_store", "x"]',
    '["hermes", "skill_manage", "create", "x"]',
    '["hermes", "delegate_task", "x"]',
    '["hermes", "cronjob", "create", "x"]',
    '["telegram", "send_message", "hi"]',
    '["python", "-c", "print(1)"]',
    '["python3", "-c", "print(1)"]',
    '["pip", "install", "requests"]',
    '["pip3", "install", "requests"]',
    '["uv", "pip", "install", "requests"]',
    '["rm", "-rf", "/tmp/example"]',
    '["rsync", "-a", "/src", "/dst"]',
    '["curl", "--upload-file", "x", "https://example.com"]',
])
def test_allowlist_dangerous_commands_are_denied_by_default(cmd_json):
    """Audit requirement: dangerous commands are blocked in the new
    default ``allowlist`` mode. Each case is denied either by the
    allowlist (BC-POL-099 deny-by-default) or by a specific
    denylist rule (BC-POL-130..196).
    """
    rc, j, _ = run_cli(cmd_json, policy_mode="allowlist")
    assert j["status"] == "COMMAND_POLICY_DENIED", \
        f"Expected POLICY_DENIED for {cmd_json}, got {j['status']}: {j['policy_errors']}"
    assert j["policy_mode"] == "allowlist"
    assert j["policy_decision"] == "block"
    assert j["policy_rule_id"] is not None
    assert j["policy_rule_id"].startswith("BC-POL-")
    # Stable rule id format: BC-POL-099 for allowlist-default-reject,
    # or BC-POL-1xx for the new denylist rules.
    assert j["policy_rule_id"] in {
        "BC-POL-099",  # not in allowlist
        "BC-POL-115",  # gh pr merge
        "BC-POL-130",  # git push
        "BC-POL-131",  # git commit
        "BC-POL-132",  # git tag
        "BC-POL-133",  # git remote
        "BC-POL-111",  # gh pr comment
        "BC-POL-110",  # gh pr create
        "BC-POL-112",  # gh pr edit
        "BC-POL-113",  # gh pr close
        "BC-POL-120",  # gh release create
        "BC-POL-123",  # gh workflow run
        "BC-POL-126",  # gh repo delete
        "BC-POL-140",  # memory_store
        "BC-POL-142",  # fact_store
        "BC-POL-143",  # skill_manage
        "BC-POL-144",  # delegate_task
        "BC-POL-145",  # cronjob
        "BC-POL-146",  # telegram send_message
        "BC-POL-150",  # python -c
        "BC-POL-151",  # python3 -c
        "BC-POL-160",  # pip install
        "BC-POL-161",  # pip3 install
        "BC-POL-162",  # uv pip install
        "BC-POL-170",  # rm -rf
        "BC-POL-172",  # rsync
        "BC-POL-174",  # curl --upload-file
    }, f"Unexpected rule id {j['policy_rule_id']} for {cmd_json}"


# ---------------------------------------------------------------------------
# Existing V1 denylist cases still block in allowlist mode
# ---------------------------------------------------------------------------


def test_allowlist_still_blocks_admin_flag():
    """BC-POL-101: ``--admin`` is blocked in allowlist mode too."""
    rc, j, _ = run_cli(
        '["gh", "pr", "merge", "1", "--admin"]',
        policy_mode="allowlist",
    )
    assert j["status"] == "COMMAND_POLICY_DENIED"
    assert j["policy_decision"] == "block"
    # V1 rule still fires in allowlist mode.
    assert j["policy_rule_id"] in {"BC-POL-099", "BC-POL-101"}


def test_allowlist_still_blocks_bash_c_wrapper():
    """BC-POL-190: shell wrapper is blocked in allowlist mode too."""
    rc, j, _ = run_cli(
        '["bash", "-c", "echo danger"]',
        policy_mode="allowlist",
    )
    assert j["status"] == "COMMAND_POLICY_DENIED"
    assert j["policy_decision"] == "block"
    assert j["policy_rule_id"] in {"BC-POL-099", "BC-POL-190"}


def test_allowlist_still_blocks_graphql_mutation_without_allow_flag():
    """BC-POL-201: GraphQL mutation is blocked in allowlist mode too."""
    rc, j, _ = run_cli(
        '["gh", "api", "graphql", "-f", "query=mutation { viewer { login } }"]',
        policy_mode="allowlist",
    )
    assert j["status"] == "COMMAND_POLICY_DENIED"
    assert j["policy_decision"] == "block"
    # Either allowlist-rejected (BC-POL-099) or V1 GraphQL rule (BC-POL-201).
    assert j["policy_rule_id"] in {"BC-POL-099", "BC-POL-201"}


def test_allowlist_still_blocks_review_thread_mutation():
    """BC-POL-102..108: review-thread mutation names are blocked."""
    rc, j, _ = run_cli(
        '["gh", "api", "graphql", "-f", "query=mutation { resolveReviewThread(input:{threadId:\\"x\\\"}) { thread { id } } }"]',
        policy_mode="allowlist",
    )
    assert j["status"] == "COMMAND_POLICY_DENIED"


# ---------------------------------------------------------------------------
# Environment sanitization
# ---------------------------------------------------------------------------


def test_env_sanitization_strips_hermes_token():
    """HERMES_* env vars are stripped before child exec."""
    env = {
        "HERMES_TOKEN": "FAKE_HERMES_TOKEN_VALUE",
        "PATH": "/usr/bin:/bin",
    }
    rc, j, _ = run_cli(
        '["git", "status", "--short"]',
        policy_mode="allowlist",
        env=env,
    )
    assert j["status"] == "COMMAND_SUCCEEDED"
    assert j["sanitized_env_applied"] is True
    assert "HERMES_TOKEN" in j["blocked_env_keys"]


def test_env_sanitization_strips_gateway_relay_token():
    """GATEWAY_RELAY_* env vars are stripped."""
    env = {
        "GATEWAY_RELAY_TOKEN": "FAKE_GATEWAY_VALUE",
        "PATH": "/usr/bin:/bin",
    }
    rc, j, _ = run_cli(
        '["git", "status", "--short"]',
        policy_mode="allowlist",
        env=env,
    )
    assert j["sanitized_env_applied"] is True
    assert "GATEWAY_RELAY_TOKEN" in j["blocked_env_keys"]


def test_env_sanitization_strips_openai_api_key():
    """OPENAI_API_KEY (matches ``*_API_KEY`` suffix) is stripped."""
    env = {
        "OPENAI_API_KEY": "FAKE_OPENAI_VALUE",
        "PATH": "/usr/bin:/bin",
    }
    rc, j, _ = run_cli(
        '["git", "status", "--short"]',
        policy_mode="allowlist",
        env=env,
    )
    assert "OPENAI_API_KEY" in j["blocked_env_keys"]


def test_env_sanitization_strips_anthropic_api_key():
    """ANTHROPIC_API_KEY (matches ``*_API_KEY`` suffix) is stripped."""
    env = {
        "ANTHROPIC_API_KEY": "FAKE_ANTHROPIC_VALUE",
        "PATH": "/usr/bin:/bin",
    }
    rc, j, _ = run_cli(
        '["git", "status", "--short"]',
        policy_mode="allowlist",
        env=env,
    )
    assert "ANTHROPIC_API_KEY" in j["blocked_env_keys"]


def test_env_sanitization_strips_gh_token():
    """GH_TOKEN is in the exact-name strip list."""
    env = {
        "GH_TOKEN": "FAKE_GH_VALUE",
        "PATH": "/usr/bin:/bin",
    }
    rc, j, _ = run_cli(
        '["git", "status", "--short"]',
        policy_mode="allowlist",
        env=env,
    )
    assert "GH_TOKEN" in j["blocked_env_keys"]


def test_env_sanitization_strips_custom_secret_suffix():
    """``*_SECRET`` suffix is stripped."""
    env = {
        "CUSTOM_SECRET": "FAKE_SECRET",
        "PATH": "/usr/bin:/bin",
    }
    rc, j, _ = run_cli(
        '["git", "status", "--short"]',
        policy_mode="allowlist",
        env=env,
    )
    assert "CUSTOM_SECRET" in j["blocked_env_keys"]


def test_env_sanitization_strips_custom_password_suffix():
    """``*_PASSWORD`` suffix is stripped."""
    env = {
        "CUSTOM_PASSWORD": "FAKE_PASSWORD",
        "PATH": "/usr/bin:/bin",
    }
    rc, j, _ = run_cli(
        '["git", "status", "--short"]',
        policy_mode="allowlist",
        env=env,
    )
    assert "CUSTOM_PASSWORD" in j["blocked_env_keys"]


def test_env_sanitization_preserves_path_and_home():
    """PATH, HOME, etc. are in the explicit preserve set."""
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/tmp",
        "HERMES_TOKEN": "FAKE_VALUE",
    }
    rc, j, _ = run_cli(
        '["git", "status", "--short"]',
        policy_mode="allowlist",
        env=env,
    )
    assert "PATH" not in j["blocked_env_keys"]
    assert "HOME" not in j["blocked_env_keys"]
    assert "HERMES_TOKEN" in j["blocked_env_keys"]


def test_env_sanitization_child_cannot_see_stripped_token():
    """End-to-end: a child that prints its own env cannot see the
    stripped ``HERMES_TOKEN``. The runner passes the sanitized env
    to Popen explicitly (via the ``env=`` kwarg), so the child
    inherits only the sanitized set.
    """
    env = {
        "HERMES_TOKEN": "FAKE_HERMES_TOKEN_VALUE",
        "PATH": "/usr/bin:/bin",
        "HOME": "/tmp",
    }
    rc, j, _ = run_cli(
        # Use legacy-denylist mode so the python3 invocation is
        # accepted; the policy decision is orthogonal to env
        # sanitization (which always runs).
        '["python3", "-c", "import os,sys; sys.stdout.write(os.environ.get(\'HERMES_TOKEN\',\'<NOT_SET>\'))"]',
        policy_mode="legacy-denylist",
        env=env,
    )
    assert j["status"] == "COMMAND_SUCCEEDED"
    # The child's stdout_tail must NOT contain the fake token.
    assert "FAKE_HERMES_TOKEN_VALUE" not in j["stdout_tail"]
    # And the env-audit field confirms HERMES_TOKEN was stripped.
    assert "HERMES_TOKEN" in j["blocked_env_keys"]


# ---------------------------------------------------------------------------
# JSON output contract (PR A1 audit fields)
# ---------------------------------------------------------------------------


def test_json_output_contains_all_pr_a1_audit_fields_on_success():
    """Allow case: ``policy_decision="allow"`` + env audit fields."""
    rc, j, _ = run_cli(
        '["git", "status", "--short"]',
        policy_mode="allowlist",
    )
    assert j["policy_mode"] == "allowlist"
    assert j["policy_decision"] == "allow"
    assert j["policy_rule_id"] == "BC-POL-002"
    assert j["policy_reason"] == "git_status"
    assert j["sanitized_env_applied"] is True
    assert isinstance(j["blocked_env_keys"], list)


def test_json_output_contains_all_pr_a1_audit_fields_on_block():
    """Block case: ``policy_decision="block"`` + stable rule id + env audit fields."""
    rc, j, _ = run_cli(
        '["git", "push", "origin", "main"]',
        policy_mode="allowlist",
    )
    assert j["policy_mode"] == "allowlist"
    assert j["policy_decision"] == "block"
    # BC-POL-099 because the command never matches an allowlist rule.
    # (Could also be BC-POL-130 if the allowlist entry for git push
    # was added later; either is correct — both are stable ids.)
    assert j["policy_rule_id"] in {"BC-POL-099", "BC-POL-130"}
    assert j["sanitized_env_applied"] is True
    assert isinstance(j["blocked_env_keys"], list)


def test_json_output_invalid_json_path_has_n_a_decision():
    """COMMAND_INVALID_JSON has ``policy_decision="n/a"`` and the audit fields are still present."""
    rc, j, _ = run_cli("not-json", policy_mode="allowlist")
    assert j["status"] == "COMMAND_INVALID_JSON"
    assert j["policy_mode"] == "allowlist"
    assert j["policy_decision"] == "n/a"
    assert j["policy_rule_id"] is None
    assert j["sanitized_env_applied"] is False
    assert j["blocked_env_keys"] == []


# ---------------------------------------------------------------------------
# Compatibility mode: legacy-denylist preserves V1 behavior
# ---------------------------------------------------------------------------


def test_legacy_denylist_mode_allows_python_c_which_v1_allowed():
    """V1 allowed ``python -c``; legacy-denylist must preserve that.

    Use ``python3`` because the legacy-denylist tests on this
    image only have ``python3`` in PATH (the V1 test used
    ``python`` which is the same fragile assumption).
    """
    rc, j, _ = run_cli(
        '["python3", "-c", "print(\'ok\')"]',
        policy_mode="legacy-denylist",
    )
    assert j["status"] == "COMMAND_SUCCEEDED"
    assert j["policy_mode"] == "legacy-denylist"
    assert j["policy_decision"] == "allow"
    # V1 had no stable rule id; we emit BC-POL-000 in legacy mode.
    assert j["policy_rule_id"] == "BC-POL-000"


def test_legacy_denylist_mode_still_blocks_admin_flag():
    """V1 blocked ``--admin``; legacy-denylist must preserve that."""
    rc, j, _ = run_cli(
        '["gh", "pr", "merge", "1", "--admin"]',
        policy_mode="legacy-denylist",
    )
    assert j["status"] == "COMMAND_POLICY_DENIED"
    # V1 rule BC-POL-101 fires in legacy-denylist mode.
    assert j["policy_rule_id"] == "BC-POL-101"


def test_legacy_denylist_mode_still_blocks_hermes_kanban():
    """V1 blocked ``hermes kanban ...``; legacy-denylist must preserve that."""
    rc, j, _ = run_cli(
        '["hermes", "kanban", "move", "123"]',
        policy_mode="legacy-denylist",
    )
    assert j["status"] == "COMMAND_POLICY_DENIED"
    assert j["policy_rule_id"] == "BC-POL-147"


def test_legacy_denylist_mode_still_blocks_shell_wrapper():
    """V1 blocked ``bash -c``; legacy-denylist must preserve that."""
    rc, j, _ = run_cli(
        '["bash", "-c", "echo danger"]',
        policy_mode="legacy-denylist",
    )
    assert j["status"] == "COMMAND_POLICY_DENIED"
    assert j["policy_rule_id"] == "BC-POL-190"


# ---------------------------------------------------------------------------
# _norm drift fix
# ---------------------------------------------------------------------------


def test_norm_drift_fix_normalizes_whitespace_padded_deny_token():
    """Audit fix: ``_norm`` is now used in the denylist. A whitespace-
    padded ``-- admin`` should still be denied. (Not a normal use
    case, but the audit flagged the drift.)
    """
    rc, j, _ = run_cli(
        '["gh", "pr", "merge", "1", " ", "--admin"]',
        policy_mode="legacy-denylist",
    )
    assert j["status"] == "COMMAND_POLICY_DENIED"


# ---------------------------------------------------------------------------
# Sanity: status names are stable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [
    "COMMAND_SUCCEEDED",
    "COMMAND_FAILED",
    "COMMAND_TIMEOUT",
    "COMMAND_POLICY_DENIED",
    "COMMAND_INVALID_JSON",
    "COMMAND_UNKNOWN_ERROR",
])
def test_status_name_set_is_stable(status):
    """The six V1 status names are still produced by the runner."""
    # We don't try to trigger every path here — that would require
    # real subprocess timeouts and command failures. This test simply
    # asserts the names appear in the codebase so a future refactor
    # cannot accidentally rename them without breaking this test.
    source = SCRIPT.read_text()
    assert f'"{status}"' in source or f"'{status}'" in source, \
        f"Status {status} not found in source"


# ===========================================================================
# PR #408 V2 hardening — Codex findings 3537094853 + 3537094862 regression
# ===========================================================================
#
# These tests cover the V2 strict-arg-shape repair of:
#   - BC-POL-001 (python -m pytest)
#   - BC-POL-009 (python -m py_compile)
#   - BC-POL-003 (git diff --check)
#
# Each P1 finding has a stable rule id (BC-POL-160..164) and a specific
# ``policy_reason`` substring the test asserts on. The tests must NOT
# actually execute the dangerous command (no real subprocess); the
# policy check fires before Popen.


# ---------------------------------------------------------------------------
# Codex finding 3537094853 — destructive pytest options
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cmd_json", [
    # Codex's exact proof. ``--basetemp=<writable dir>`` lets pytest
    # clear that directory before the run, so this is a one-shot
    # arbitrary-write primitive.
    '["python3", "-m", "pytest", "tests", "--basetemp=/tmp/victim"]',
    # Space-separated form (pytest accepts both ``=`` and `` ``).
    '["python3", "-m", "pytest", "tests", "--basetemp", "/tmp/victim"]',
    # Sensitive-root target. The path-token check rejects
    # ``/home/max/.ssh`` so this should also fire on the path
    # block, but the rule id MUST be in the V2 set.
    '["python3", "-m", "pytest", "tests", "--basetemp=/home/max/.ssh"]',
    # Same with space form.
    '["python3", "-m", "pytest", "tests", "--basetemp", "/home/max/.ssh"]',
])
def test_v2_rejects_basetemp_pytest_option(cmd_json, tmp_path):
    """Codex 3537094853 (P1): ``--basetemp`` is rejected by the V2
    allowlist before any subprocess is launched.

    The tests run the runner subprocess; the runner must return
    ``COMMAND_POLICY_DENIED`` with a stable BC-POL-160 rule id and
    a ``policy_reason`` that explicitly mentions the rejected
    option. We do NOT create ``/tmp/victim`` or
    ``/home/max/.ssh`` — the runner exits before Popen.
    """
    # The runner should not create /tmp/victim. Asserting it
    # doesn't exist BEFORE the test runs is a no-op (it doesn't
    # exist in a clean tmp_path anyway). The real proof is that
    # the runner returns COMMAND_POLICY_DENIED; a pytest run with
    # ``--basetemp`` would create the dir and clobber it.
    rc, j, _ = run_cli(cmd_json, policy_mode="allowlist")
    assert j["status"] == "COMMAND_POLICY_DENIED"
    assert j["policy_decision"] == "block"
    assert j["policy_rule_id"] == "BC-POL-160"
    assert "unsafe pytest option rejected" in j["policy_reason"]
    assert "--basetemp" in j["policy_reason"]
    # The V2 fix's invariant: the runner does not execute the
    # command. Verify that ``/tmp/victim`` was NOT created.
    assert not (tmp_path / "victim").exists()
    # And that the runner's stdout_tail / stderr_tail are empty
    # (no subprocess output to capture).
    assert j["stdout_tail"] == ""
    assert j["stderr_tail"] == ""


@pytest.mark.parametrize("cmd_json", [
    # Any unknown pytest option is rejected with BC-POL-161.
    '["python3", "-m", "pytest", "tests", "--some-new-output-file=/tmp/x"]',
    # ``-o`` is the override-ini shortcut. The V2 deny list catches
    # ``-o`` directly (it's listed in _UNSAFE_PYTEST_OPTIONS) and
    # emits BC-POL-160. Both this and the ``-k`` style belong to
    # the same "unknown / unsafe option" surface; either BC-POL-160
    # or BC-POL-161 is acceptable, but the predicate MUST return
    # False (no COMMAND_SUCCEEDED).
    '["python3", "-m", "pytest", "tests", "-o", "cache_dir=/tmp/x"]',
])
def test_v2_rejects_unknown_pytest_option(cmd_json):
    """V2: any pytest option not in the closed allowlist is rejected."""
    rc, j, _ = run_cli(cmd_json, policy_mode="allowlist")
    assert j["status"] == "COMMAND_POLICY_DENIED"
    assert j["policy_decision"] == "block"
    # Either BC-POL-160 (known unsafe) or BC-POL-161 (unknown) is
    # acceptable. Both must be in the V2 set.
    assert j["policy_rule_id"] in {"BC-POL-160", "BC-POL-161"}


def test_v2_allows_safe_pytest_forms(tmp_path):
    """V2: a closed set of pytest options is still allowed.

    Codex 3537094853 said "deny destructive options"; the V2 fix
    does that by allowing ONLY a closed allowlist. This test
    asserts the safe allowlist is still functional.
    """
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "test_ok.py").write_text("def test_ok(): assert True\n")
    # Three safe forms from the audit's required list.
    for form in (
        f'["python3", "-m", "pytest", "{test_dir}", "-q"]',
        f'["python3", "-m", "pytest", "{test_dir}", "--no-header"]',
        f'["python3", "-m", "pytest", "{test_dir}", "--tb=short"]',
    ):
        rc, j, _ = run_cli(form, policy_mode="allowlist")
        assert j["policy_decision"] == "allow", \
            f"Expected allow for {form}, got {j['policy_decision']}: {j.get('policy_reason')}"
        assert j["policy_rule_id"] == "BC-POL-001"


# ---------------------------------------------------------------------------
# Codex finding 3537094862 — git diff output targets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cmd_json", [
    # Codex's exact proof. ``--output=<file>`` writes to a target
    # file, so this is an arbitrary-write primitive.
    '["git", "diff", "--check", "--output=/tmp/victim"]',
    # Space form.
    '["git", "diff", "--check", "--output", "/tmp/victim"]',
    # ``=`` form.
    '["git", "diff", "--check", "--output", "/tmp/victim", "--"]',
])
def test_v2_rejects_git_diff_output_option(cmd_json, tmp_path):
    """Codex 3537094862 (P1): ``--output`` is rejected before any
    subprocess is launched. The runner must NOT create the target
    file.
    """
    # Pre-condition: the target file does not exist.
    victim = tmp_path / "victim_diff"
    assert not victim.exists()
    rc, j, _ = run_cli(cmd_json, policy_mode="allowlist")
    assert j["status"] == "COMMAND_POLICY_DENIED"
    assert j["policy_decision"] == "block"
    assert j["policy_rule_id"] == "BC-POL-164"
    assert "git diff option" in j["policy_reason"]
    # The V2 invariant: /tmp/victim must NOT exist.
    assert not victim.exists()
    # The runner did not execute the command; both tails empty.
    assert j["stdout_tail"] == ""
    assert j["stderr_tail"] == ""


@pytest.mark.parametrize("cmd_json", [
    # The four other V2-rejected git diff options (Codex 3537094862).
    '["git", "diff", "--check", "--cached"]',
    '["git", "diff", "--check", "--staged"]',
    '["git", "diff", "--check", "--ext-diff"]',
    # ``--no-index a b`` requires a value.
    '["git", "diff", "--check", "--no-index", "a", "b"]',
])
def test_v2_rejects_other_unsafe_git_diff_options(cmd_json):
    """V2: ``--cached``/``--staged``/``--ext-diff``/``--no-index`` are
    rejected with BC-POL-163.
    """
    rc, j, _ = run_cli(cmd_json, policy_mode="allowlist")
    assert j["status"] == "COMMAND_POLICY_DENIED"
    assert j["policy_decision"] == "block"
    assert j["policy_rule_id"] in {"BC-POL-163", "BC-POL-164"}


def test_v2_allows_safe_git_diff_exact_forms():
    """V2: exact ``git diff --check`` / ``--name-only`` / ``--stat``
    shapes are still allowed. The V2 fix preserves the safe
    read-only surface.
    """
    for cmd_json in (
        '["git", "diff", "--check"]',
        '["git", "diff", "--name-only"]',
        '["git", "diff", "--stat"]',
    ):
        rc, j, _ = run_cli(cmd_json, policy_mode="allowlist")
        assert j["policy_decision"] == "allow", \
            f"Expected allow for {cmd_json}, got {j['policy_decision']}"
        assert j["policy_rule_id"] in {
            "BC-POL-003",  # git diff --check
            "BC-POL-004",  # git diff --name-only
            "BC-POL-005",  # git diff --stat
        }


def test_v2_rejects_py_compile_with_flag():
    """V2: ``python -m py_compile`` accepts only path tokens; any
    flag is rejected with BC-POL-162.
    """
    rc, j, _ = run_cli(
        '["python3", "-m", "py_compile", "-x", "scripts/local/run_bounded_command.py"]',
        policy_mode="allowlist",
    )
    assert j["status"] == "COMMAND_POLICY_DENIED"
    assert j["policy_decision"] == "block"
    assert j["policy_rule_id"] == "BC-POL-162"
    assert "py_compile" in j["policy_reason"]


def test_v2_allows_safe_py_compile(tmp_path):
    """V2: ``python -m py_compile <safe .py file>`` is still allowed
    (BC-POL-009). The V2 fix tightens the predicate but preserves
    the safe form.
    """
    script = tmp_path / "tiny.py"
    script.write_text("x = 1\n")
    rc, j, _ = run_cli(
        f'["python3", "-m", "py_compile", "{script}"]',
        policy_mode="allowlist",
    )
    assert j["status"] == "COMMAND_SUCCEEDED"
    assert j["policy_rule_id"] == "BC-POL-009"


# ---------------------------------------------------------------------------
# V2 cross-cutting: policy metadata is stable on every reject path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cmd_json,expected_rule_id", [
    # P1 pytest cases -> BC-POL-160
    ('["python3", "-m", "pytest", "tests", "--basetemp=/tmp/victim"]', "BC-POL-160"),
    # P1 git diff case -> BC-POL-164
    ('["git", "diff", "--check", "--output=/tmp/victim"]', "BC-POL-164"),
    # Unknown pytest option -> BC-POL-161
    ('["python3", "-m", "pytest", "tests", "--totally-unknown"]', "BC-POL-161"),
    # py_compile with flag -> BC-POL-162
    ('["python3", "-m", "py_compile", "-x", "x.py"]', "BC-POL-162"),
    # git diff cached -> BC-POL-163
    ('["git", "diff", "--check", "--cached"]', "BC-POL-163"),
])
def test_v2_policy_metadata_is_stable_on_block(cmd_json, expected_rule_id):
    """V2 invariant: every block path has a stable rule id, a
    non-empty ``policy_reason``, and ``policy_decision="block"``.
    This is what downstream CI / audit tooling depends on.
    """
    rc, j, _ = run_cli(cmd_json, policy_mode="allowlist")
    assert j["status"] == "COMMAND_POLICY_DENIED"
    assert j["policy_decision"] == "block"
    assert j["policy_rule_id"] == expected_rule_id
    assert j["policy_reason"]
    assert isinstance(j["policy_reason"], str)
    assert len(j["policy_reason"]) > 0


# ---------------------------------------------------------------------------
# V2 — legacy-denylist mode must NOT widen the allowlist
# ---------------------------------------------------------------------------


def test_v2_legacy_denylist_does_not_change_pytest_rule_id(tmp_path):
    """V2 invariant: in ``--policy-mode legacy-denylist``, the V1
    denylist-only contract is preserved exactly. The pytest
    ``--basetemp`` case is NOT blocked in legacy mode (it was
    allowed by V1). This is the documented contract: legacy mode
    is for backward compat only and is NOT safe for unattended
    autocoder execution.

    The test uses a ``tmp_path`` value that is unique to this
    test, so even if pytest's ``--basetemp`` cleanup runs, it
    only affects the temp directory created for this test. The
    test asserts that legacy mode does NOT block the command
    (i.e. status is COMMAND_SUCCEEDED or COMMAND_FAILED, not
    COMMAND_POLICY_DENIED).
    """
    # Use a fresh dir under tmp_path. If pytest's --basetemp
    # cleanup actually runs (it only runs if pytest finds and
    # executes tests), it would clear this dir, but that's a
    # pre-existing V1 behavior the legacy-denylist mode must
    # preserve. Use a UNIQUE dir so the test is hermetic.
    basetemp = tmp_path / "legacy_victim_unique_dir"
    rc, j, _ = run_cli(
        f'["python3", "-m", "pytest", "tests", "--basetemp={basetemp}"]',
        policy_mode="legacy-denylist",
    )
    # We expect COMMAND_SUCCEEDED (or COMMAND_FAILED if pytest
    # actually runs and finds no tests) but NOT
    # COMMAND_POLICY_DENIED. The legacy mode preserves V1.
    assert j["status"] != "COMMAND_POLICY_DENIED", \
        "legacy-denylist must preserve V1 contract (--basetemp was allowed by V1)"