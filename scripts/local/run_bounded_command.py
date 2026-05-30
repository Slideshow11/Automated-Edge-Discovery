#!/usr/bin/env python3
"""
Bounded command runner — model-agnostic safeguard for local shell commands.

Enforces:
- Command timeouts
- Denylisted unsafe operations (--admin, deletion mutations, watch mode, etc.)
- Tailed stdout/stderr (no unlimited storage)
- Structured JSON + Markdown output
- No shell=True ever

Usage:
    python3 scripts/local/run_bounded_command.py \
        --cmd-json '["python","--version"]' \
        --timeout-seconds 30 \
        --output-json /tmp/result.json \
        --output-md /tmp/result.md

Optional flags:
    --cwd <path>                     Working directory for the command
    --stdout-tail-bytes <int>        Default 12000
    --stderr-tail-bytes <int>        Default 12000
    --allow-gh-api-mutation           Allow GraphQL mutations (default: false)
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Policy denylist
# ---------------------------------------------------------------------------

DENYLIST_PATTERNS_ALWAYS = [
    # Watch mode — stalls
    "gh run watch",
    "gh pr checks --watch",
    # Admin bypass — dangerous
    "--admin",
    # Deletion mutations — destroys audit history
    "deleteReviewComment",
    "deletePullRequestReviewComment",
    "dismissReview",
    # PUT/PATCH mutations to branch protection API
    "-X PUT",
    "-X PATCH",
    "/branches/main/protection",
    "/required_status_checks",
    "/enforce_admins",
    "/required_pull_request_reviews",
    # Hermes kanban mutation strings (heuristic)
    "hermes kanban",
    "kanban move",
    "kanban add",
]

DENYLIST_PATTERNS_GATED = [
    # GraphQL mutations — require --allow-gh-api-mutation
    "mutation ",
]


def _check_policy(command: list[str], allow_gh_api_mutation: bool) -> list[str]:
    """
    Return list of policy errors. Empty list means command is allowed.

    Checks:
    - Always-active denylisted patterns
    - GraphQL mutation detection (requires --allow-gh-api-mutation)
    """
    errors = []
    cmd_str = " ".join(command)

    for pattern in DENYLIST_PATTERNS_ALWAYS:
        if pattern in cmd_str:
            errors.append(f"Deny-listed pattern in command: {pattern!r}")

    # GraphQL mutation detection — gated by flag
    if not allow_gh_api_mutation:
        for pattern in DENYLIST_PATTERNS_GATED:
            if pattern in cmd_str:
                errors.append(f"GraphQL mutation requires --allow-gh-api-mutation")
                break

    return errors


def _tail(data: str, max_bytes: int) -> str:
    """Return last at most max_bytes of data, preserving newlines."""
    if len(data.encode("utf-8")) <= max_bytes:
        return data
    # Encode to get byte length, then decode back
    encoded = data.encode("utf-8")
    truncated = encoded[-max_bytes:]
    # Find nearest newline to avoid splitting a line
    newline_idx = truncated.find(b"\n")
    if newline_idx >= 0:
        return truncated[newline_idx + 1:].decode("utf-8", errors="replace")
    return truncated.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bounded command runner with policy enforcement.",
    )
    parser.add_argument(
        "--cmd-json",
        required=True,
        help="JSON array of command strings, e.g. '[\"git\",\"status\"]'",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="Hard timeout in seconds. Default 300.",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory for the command. Defaults to cwd.",
    )
    parser.add_argument(
        "--stdout-tail-bytes",
        type=int,
        default=12000,
        help="Max bytes of stdout to retain. Default 12000.",
    )
    parser.add_argument(
        "--stderr-tail-bytes",
        type=int,
        default=12000,
        help="Max bytes of stderr to retain. Default 12000.",
    )
    parser.add_argument(
        "--allow-gh-api-mutation",
        action="store_true",
        default=False,
        help="Allow GraphQL mutation commands. Default false.",
    )
    parser.add_argument(
        "--output-json",
        required=True,
        help="Path to write JSON result.",
    )
    parser.add_argument(
        "--output-md",
        required=True,
        help="Path to write Markdown result.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Command validation
# ---------------------------------------------------------------------------

def validate_command(cmd_json: str) -> tuple[list[str] | None, str | None]:
    """
    Parse and validate command from JSON.

    Returns (command_list, None) on success.
    Returns (None, error_message) on failure.
    """
    try:
        parsed = json.loads(cmd_json)
    except json.JSONDecodeError as e:
        return None, f"COMMAND_INVALID_JSON: not valid JSON — {e}"

    if not isinstance(parsed, list):
        return None, "COMMAND_INVALID_JSON: --cmd-json must be a JSON array"

    if len(parsed) == 0:
        return None, "COMMAND_INVALID_JSON: command array is empty"

    for i, element in enumerate(parsed):
        if not isinstance(element, str):
            return None, (
                f"COMMAND_INVALID_JSON: element {i} is {type(element).__name__}, "
                "expected string"
            )

    return parsed, None


# ---------------------------------------------------------------------------
# Result writing
# ---------------------------------------------------------------------------

def write_json_output(path: str, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def write_md_output(path: str, data: dict) -> None:
    lines = [
        f"# Bounded Command Runner Result",
        f"",
        f"**Status**: {data['status']}",
        f"**Command**: `{' '.join(data['command'])}`",
        f"**cwd**: {data['cwd']}",
        f"**Timeout**: {data['timeout_seconds']}s",
        f"**Duration**: {data['duration_seconds']:.3f}s",
        f"**Exit code**: {data['exit_code']}",
        f"**Killed**: {data['killed']}",
        f"",
    ]
    if data["policy_errors"]:
        lines.append("**Policy Errors**:")
        for err in data["policy_errors"]:
            lines.append(f"- `{err}`")
        lines.append("")
    lines.append("## stdout (tail)")
    lines.append("```")
    lines.append(data["stdout_tail"])
    lines.append("```")
    lines.append("")
    lines.append("## stderr (tail)")
    lines.append("```")
    lines.append(data["stderr_tail"])
    lines.append("```")

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def build_result(
    command: list[str],
    cwd: str,
    timeout_seconds: int,
    started_at: datetime,
    ended_at: datetime,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    killed: bool,
    policy_errors: list[str],
    status: str,
) -> dict:
    duration = (ended_at - started_at).total_seconds()
    return {
        "status": status,
        "command": command,
        "cwd": cwd,
        "timeout_seconds": timeout_seconds,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": round(duration, 3),
        "exit_code": exit_code,
        "stdout_tail": _tail(stdout, 12000),
        "stderr_tail": _tail(stderr, 12000),
        "killed": killed,
        "policy_errors": policy_errors,
    }


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------

def run_bounded_command(
    command: list[str],
    timeout_seconds: int,
    cwd: str | None,
    stdout_tail_bytes: int,
    stderr_tail_bytes: int,
    allow_gh_api_mutation: bool,
) -> dict:
    """
    Run a bounded command with policy checks and tailed output.

    Returns a result dict (same schema as JSON output).
    """
    # Policy check
    policy_errors = _check_policy(command, allow_gh_api_mutation)

    if policy_errors:
        started = datetime.now(timezone.utc)
        ended = datetime.now(timezone.utc)
        result = build_result(
            command=command,
            cwd=cwd or os.getcwd(),
            timeout_seconds=timeout_seconds,
            started_at=started,
            ended_at=ended,
            exit_code=None,
            stdout="",
            stderr="",
            killed=False,
            policy_errors=policy_errors,
            status="COMMAND_POLICY_DENIED",
        )
        return result

    # Execute
    started = datetime.now(timezone.utc)
    killed = False
    exit_code: int | None = None
    stdout_bytes: bytes = b""
    stderr_bytes: bytes = b""
    status = "COMMAND_UNKNOWN_ERROR"

    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            shell=False,  # never shell=True
        )
        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout_seconds)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout_bytes, stderr_bytes = proc.communicate()
            killed = True
            exit_code = -1
            status = "COMMAND_TIMEOUT"
        ended = datetime.now(timezone.utc)
    except FileNotFoundError:
        ended = datetime.now(timezone.utc)
        stdout_bytes = f"Command not found: {command[0]}".encode()
        stderr_bytes = b""
        exit_code = 127
        status = "COMMAND_UNKNOWN_ERROR"
    except Exception as e:
        ended = datetime.now(timezone.utc)
        stdout_bytes = b""
        stderr_bytes = str(e).encode()
        exit_code = 1
        status = "COMMAND_UNKNOWN_ERROR"

    if status == "COMMAND_UNKNOWN_ERROR":
        if killed:
            status = "COMMAND_TIMEOUT"
        elif exit_code == 0:
            status = "COMMAND_SUCCEEDED"
        else:
            status = "COMMAND_FAILED"

    # Apply byte limits (already applied via _tail in build_result, but
    # we want to apply the per-output-type limit here)
    stdout = _tail(stdout_bytes.decode("utf-8", errors="replace"), stdout_tail_bytes)
    stderr = _tail(stderr_bytes.decode("utf-8", errors="replace"), stderr_tail_bytes)

    return build_result(
        command=command,
        cwd=cwd or os.getcwd(),
        timeout_seconds=timeout_seconds,
        started_at=started,
        ended_at=ended,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        killed=killed,
        policy_errors=[],
        status=status,
    )


def main() -> None:
    args = parse_args()

    # Validate command
    command, validation_error = validate_command(args.cmd_json)
    if command is None:
        result = {
            "status": "COMMAND_INVALID_JSON",
            "command": [],
            "cwd": args.cwd or os.getcwd(),
            "timeout_seconds": args.timeout_seconds,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": 0.0,
            "exit_code": None,
            "stdout_tail": "",
            "stderr_tail": validation_error or "unknown error",
            "killed": False,
            "policy_errors": [],
        }
        write_json_output(args.output_json, result)
        write_md_output(args.output_md, result)
        sys.exit(0)  # Exit 0 so the runner itself doesn't fail on invalid input

    # Run
    result = run_bounded_command(
        command=command,
        timeout_seconds=args.timeout_seconds,
        cwd=args.cwd,
        stdout_tail_bytes=args.stdout_tail_bytes,
        stderr_tail_bytes=args.stderr_tail_bytes,
        allow_gh_api_mutation=args.allow_gh_api_mutation,
    )

    write_json_output(args.output_json, result)
    write_md_output(args.output_md, result)

    # Exit code reflects command result
    sys.exit(0)  # Runner always exits 0; status in JSON is the contract


if __name__ == "__main__":
    main()