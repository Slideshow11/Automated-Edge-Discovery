#!/usr/bin/env python3
"""
Bounded command runner — model-agnostic safeguard for local shell commands.

Enforces:
- Command timeouts with process-group cleanup
- Denylisted unsafe operations (--admin, deletion mutations, watch mode, etc.)
- Streaming bounded stdout/stderr (fixed ring buffer, no unlimited storage)
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
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Streaming ring buffer
# ---------------------------------------------------------------------------

class RingBuffer:
    """Fixed-size ring buffer keeping the last max_bytes."""

    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self._buf = bytearray()

    def write(self, data: bytes) -> None:
        """Append data, discarding oldest bytes if over max_bytes."""
        self._buf.extend(data)
        if len(self._buf) > self.max_bytes:
            # Keep only the last max_bytes
            self._buf = self._buf[-self.max_bytes:]

    def read(self) -> str:
        """Return decoded content, errors replaced."""
        return self._buf.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Background reader threads
# ---------------------------------------------------------------------------

def _reader_thread(fd, buffer: RingBuffer, closed_event: threading.Event):
    """Drain fd until EOF, writing chunks into buffer."""
    try:
        while True:
            chunk = fd.read(8192)
            if not chunk:
                break
            buffer.write(chunk)
    except Exception:
        pass
    finally:
        try:
            fd.close()
        except Exception:
            pass
        closed_event.set()


# ---------------------------------------------------------------------------
# Policy denylist helpers
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """Normalize a string for policy matching: strip and collapse whitespace."""
    return " ".join(s.strip().split())


def _lower_args(args: list[str]) -> list[str]:
    return [a.lower() for a in args]


def _cmd_str(args: list[str]) -> str:
    return " ".join(args)


def _has_shell_wrapper(args: list[str]) -> bool:
    """Check for shell invocation wrappers that spawn a new shell process."""
    wrapper_patterns = [
        "bash -c", "sh -c", "zsh -c", "fish -c",
        "powershell -command", "pwsh -command", "cmd /c",
    ]
    cmd = _cmd_str(args).lower()
    for p in wrapper_patterns:
        if p in cmd:
            return True
    return False


def _is_gh_api_command(args: list[str]) -> bool:
    return len(args) >= 3 and args[0] == "gh" and args[1] == "api"


def _extract_gh_api_method(args: list[str]) -> str | None:
    """Extract the HTTP method from a gh api command."""
    # gh api [--method <METHOD>] <endpoint>
    # gh api -X<METHOD> <endpoint>
    # gh api --method=<METHOD> <endpoint>
    for i, arg in enumerate(args):
        if arg in ("--method", "-X"):
            if i + 1 < len(args):
                return args[i + 1].upper()
        if arg.startswith("--method="):
            return arg.split("=", 1)[1].upper()
        if arg.startswith("-X"):
            return arg[2:].upper()
        if arg.startswith("-X"):
            return arg[2:].upper()
    # Also check standalone REST verbs as first positional after gh api
    methods = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
    for arg in args[2:]:
        if arg.upper() in methods and not arg.startswith("-"):
            return arg.upper()
    return None


def _contains_graphql_mutation_operation(text: str) -> bool:
    """
    Detect a GraphQL mutation keyword followed by an operation body.

    Handles all common forms:
      mutation{...}
      mutation { ... }
      mutation    {
      mutation<whitespace>{
      mutation OperationName { ... }
      mutation OperationName($id:ID!){...}
      Mutation{...}  (case-insensitive)

    Does NOT match:
      - words that merely contain 'mutation' as a substring
        (immutable, permutation, etc.)
      - GraphQL queries: query { ... } or { viewer { ... } }
    """
    # GraphQL operation keyword (\bmutation\b), then any chars except opening brace,
    # then the opening brace — catches mutation{ mutation { mutation name(args){ etc.
    # The [^{]* is non-greedy; the DOTALL flag is not needed since we stop at {.
    return bool(re.search(r'\bmutation\b[^{]*\{', text, flags=re.IGNORECASE))


def _is_gh_graphql_mutation(args: list[str]) -> bool:
    """Check if gh api graphql command contains a mutation."""
    if not (_is_gh_api_command(args) and "graphql" in args):
        return False
    cmd = _cmd_str(args)
    return _contains_graphql_mutation_operation(cmd)


def _is_mutation_denylist_pattern(args: list[str]) -> bool:
    """Check for dangerous GraphQL mutation names regardless of keyword."""
    # Even if the word 'mutation' is somehow bypassed, certain mutation
    # names in the command are unambiguously dangerous.
    mutation_names_lower = [
        "deletereviewcomment",
        "deletepullrequestreviewcomment",
        "dismissreview",
        "resolvesreviewthread",
        "resolvereviewthread",
        "addcomment",
        "addpullrequestreview",
    ]
    cmd_lower = _cmd_str(args).lower()
    for name in mutation_names_lower:
        if name in cmd_lower:
            return True
    return False


# ---------------------------------------------------------------------------
# Policy check
# ---------------------------------------------------------------------------

def _check_policy(command: list[str], allow_gh_api_mutation: bool) -> list[str]:
    """
    Return list of policy errors. Empty list means command is allowed.
    """
    errors = []
    cmd_lower = _cmd_str(command).lower()
    args = command

    # ---- Always-active denylist ----
    always_blocked = {
        # Admin bypass
        "--admin",
        # Deletion mutations
        "deletereviewcomment",
        "deletepullrequestreviewcomment",
        "dismissreview",
        "resolvesreviewthread",
        "resolvereviewthread",
        # Watch mode — stalls
        "gh run watch",
        "gh pr checks --watch",
        "gh pr checks -w",
        # Shell invocation wrappers
        "bash -c",
        "sh -c",
        "zsh -c",
        "fish -c",
        "powershell -command",
        "pwsh -command",
        "cmd /c",
    }
    for pattern in always_blocked:
        if pattern in cmd_lower:
            errors.append(f"Deny-listed pattern in command: {pattern!r}")

    # ---- GitHub API mutation detection ----
    if _is_gh_api_command(args):
        method = _extract_gh_api_method(args)
        if method and method in {"PUT", "PATCH", "POST", "DELETE"}:
            # Block mutation-worthy methods on certain paths
            endpoint = _cmd_str(args)
            dangerous_paths = [
                "/branches/",
                "/protection",
                "/required_status_checks",
                "/enforce_admins",
                "/required_pull_request_reviews",
                "/comments/",
                "/reviews/",
                "/pulls/comments",
                "/issues/comments",
                "/replies",
            ]
            for path in dangerous_paths:
                if path in endpoint:
                    errors.append(
                        f"GitHub API mutates protected path: "
                        f"{method} {endpoint}"
                    )
                    break

    # ---- GraphQL mutation ----
    if not allow_gh_api_mutation:
        if _is_gh_graphql_mutation(args) or _is_mutation_denylist_pattern(args):
            errors.append("GraphQL mutation requires --allow-gh-api-mutation")

    # ---- Hermes kanban mutation (heuristic) ----
    if "hermes" in cmd_lower and any(k in cmd_lower for k in ["kanban move", "kanban add", "kanban update", "kanban delete"]):
        errors.append("Hermes kanban mutation not allowed")

    return errors


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
    stdout_tail: str,
    stderr_tail: str,
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
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
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
    Run a bounded command with policy checks, streaming bounded output,
    and process-group timeout cleanup.

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
            stdout_tail="",
            stderr_tail="",
            killed=False,
            policy_errors=policy_errors,
            status="COMMAND_POLICY_DENIED",
        )
        return result

    # Execute
    started = datetime.now(timezone.utc)
    killed = False
    exit_code: int | None = None
    status = "COMMAND_UNKNOWN_ERROR"

    # Streaming ring buffers
    stdout_buf = RingBuffer(stdout_tail_bytes)
    stderr_buf = RingBuffer(stderr_tail_bytes)
    stdout_closed = threading.Event()
    stderr_closed = threading.Event()

    try:
        # Build Popen kwargs
        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": cwd,
            "shell": False,  # never shell=True
        }
        # On POSIX, start in a new session so we can kill the whole group
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(command, **popen_kwargs)

        # Start background reader threads
        stdout_thread = threading.Thread(
            target=_reader_thread,
            args=(proc.stdout, stdout_buf, stdout_closed),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_reader_thread,
            args=(proc.stderr, stderr_buf, stderr_closed),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        # Wait for process with timeout
        try:
            proc.wait(timeout=timeout_seconds)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            # Timeout — try graceful termination of the process group first
            killed = True
            status = "COMMAND_TIMEOUT"
            exit_code = -1

            if sys.platform != "win32":
                # Try SIGTERM on the process group
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    # Give it 2 seconds to clean up gracefully
                    gone = proc.wait(timeout=2)
                except (ProcessLookupError, OSError):
                    # Process already gone
                    pass
                except Exception:
                    pass
                else:
                    # If still alive after 2s, SIGKILL
                    if proc.poll() is None:
                        try:
                            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                            proc.wait(timeout=2)
                        except Exception:
                            pass
            else:
                # Windows fallback — proc.terminate() then proc.kill()
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    pass
                if proc.poll() is None:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception:
                        pass

        ended = datetime.now(timezone.utc)

    except FileNotFoundError:
        ended = datetime.now(timezone.utc)
        exit_code = 127
        status = "COMMAND_UNKNOWN_ERROR"

    except Exception as e:
        ended = datetime.now(timezone.utc)
        exit_code = 1
        status = "COMMAND_UNKNOWN_ERROR"
        # Return early with what we have
        return build_result(
            command=command,
            cwd=cwd or os.getcwd(),
            timeout_seconds=timeout_seconds,
            started_at=started,
            ended_at=ended,
            exit_code=exit_code,
            stdout_tail=stdout_buf.read(),
            stderr_tail=stderr_buf.read(),
            killed=killed,
            policy_errors=[],
            status=status,
        )

    if status == "COMMAND_UNKNOWN_ERROR":
        if killed:
            status = "COMMAND_TIMEOUT"
        elif exit_code == 0:
            status = "COMMAND_SUCCEEDED"
        else:
            status = "COMMAND_FAILED"

    # Read final tails from ring buffers
    stdout_tail = stdout_buf.read()
    stderr_tail = stderr_buf.read()

    return build_result(
        command=command,
        cwd=cwd or os.getcwd(),
        timeout_seconds=timeout_seconds,
        started_at=started,
        ended_at=ended,
        exit_code=exit_code,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
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
        sys.exit(0)  # Runner itself doesn't fail on invalid input

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

    # Exit code: runner always exits 0; JSON status is the contract
    sys.exit(0)


if __name__ == "__main__":
    main()