#!/usr/bin/env python3
"""
run_plan_preview.py

Plan-preview-only invocation of Claude Code from a worker packet.

BEHAVIOR:
  - Accepts an existing worker packet JSON path.
  - Invokes Claude Code in --permission-mode plan (no file edits possible).
  - Captures plan output under /tmp/aed_runs/<run_id>/plan_preview/ (never in repo).
  - Validates the plan against packet constraints.
  - Returns PLAN_PREVIEW_READY, PLAN_PREVIEW_BLOCKED, or PLAN_PREVIEW_ERROR.

HARDBAN:
  - Does NOT edit repo files as part of plan preview.
  - Does NOT run tests as part of plan preview.
  - Does NOT create a PR from plan preview.
  - Does NOT merge.
  - Does NOT append audit log.
  - Does NOT dispatch.
  - Does NOT touch production boards.
  - Does NOT mutate Hermes skills.
  - Does NOT update memory or profile.
  - Does NOT install packages.
  - Does NOT run Claude Code in execution mode.

CLI:
  python3 scripts/local/run_plan_preview.py \\
    --packet-json /path/to/worker_packet.json \\
    [--output-dir /tmp/aed_runs/<run_id>/plan_preview/] \\
    [--output-json /tmp/plan_preview_result.json] \\
    [--output-md /tmp/plan_preview_result.md]
"""

from __future__ import annotations

import argparse
import json
import os
import select
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PACKET_KIND = "aed.worker.packet.v1"

RESULT_STATUSES = frozenset([
    "PLAN_PREVIEW_READY",
    "PLAN_PREVIEW_BLOCKED",
    "PLAN_PREVIEW_ERROR",
])

# Files that are never allowed as plan targets (relative to repo root)
FORBIDDEN_FILENAME_PATTERNS = frozenset([
    ".hermes",
    "audit",
    "memory",
    "profile",
])

# Paths never allowed as plan targets
FORBIDDEN_PATH_PREFIXES = (
    "/home/max/.hermes",
    "/tmp/hermes",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_forbidden_path(path: str) -> bool:
    """Check whether a path is forbidden by prefix or dot-hermes component."""
    p = Path(path)
    # Check path prefix against forbidden prefixes
    for prefix in FORBIDDEN_PATH_PREFIXES:
        if str(p).startswith(prefix):
            return True
    # Also check path parts for forbidden names
    parts = p.parts
    for part in parts:
        if part in FORBIDDEN_FILENAME_PATTERNS:
            return True
        if part.startswith("."):
            for pat in FORBIDDEN_FILENAME_PATTERNS:
                if part.startswith(pat):
                    return True
    return False


def _resolve_git_root() -> Path | None:
    """Find repo git root, or None if not in a git repo."""
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd="/home/max/Automated-Edge-Discovery",
            capture_output=True,
            text=True,
            timeout=10,
        )
        if root.returncode == 0:
            return Path(root.stdout.strip())
    except Exception:
        pass
    return None


def _git_status(repo_path: Path) -> str:
    """Return 'clean' or 'dirty' based on git status --porcelain."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            return "clean" if not output else f"dirty: {output[:200]}"
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_packet(packet: dict) -> list[str]:
    """
    Validate a worker packet for plan-preview use.
    Returns list of error strings. Empty list = valid.
    """
    errors: list[str] = []

    if not packet.get("packet_kind") == PACKET_KIND:
        errors.append(
            f"packet_kind must be '{PACKET_KIND}' (got '{packet.get('packet_kind', '')}')"
        )

    if not packet.get("task"):
        errors.append("packet.task is required")

    task = packet.get("task", {})

    # Check allowed_files is a list
    allowed = task.get("allowed_files")
    if allowed is not None and not isinstance(allowed, list):
        errors.append("task.allowed_files must be a list")

    # Check forbidden_files is a list
    forbidden = task.get("forbidden_files")
    if forbidden is not None and not isinstance(forbidden, list):
        errors.append("task.forbidden_files must be a list")

    # Check do_not is a list
    do_not = task.get("do_not")
    if do_not is not None and not isinstance(do_not, list):
        errors.append("task.do_not must be a list")

    return errors


def validate_plan_against_packet(plan_text: str, packet: dict) -> list[str]:
    """
    Validate that plan_text does not reference forbidden files or
    violate do_not constraints.

    Returns list of violation strings. Empty list = valid.
    """
    violations: list[str] = []
    task = packet.get("task", {})
    forbidden_files = task.get("forbidden_files", [])
    do_not = task.get("do_not", [])
    allowed_files = task.get("allowed_files", [])

    # Check for forbidden file references in plan
    for fpath in forbidden_files:
        if fpath in plan_text:
            violations.append(f"plan references forbidden file: {fpath}")

    # Check do_not constraints using word-boundary regex.
    # Each constraint is split into words; ALL words must be present
    # as whole words in plan_text (case-insensitive) for the constraint
    # to be violated. This prevents "do" in "do_not" from matching
    # "edit" or other partial words.
    import re
    for constraint in do_not:
        constraint_words = constraint.lower().split()
        plan_lower = plan_text.lower()
        if all(re.search(r'\b' + re.escape(w) + r'\b', plan_lower) for w in constraint_words):
            violations.append(f"plan violates do_not constraint: {constraint}")

    # Check dependency install against policy
    dep_policy = task.get("dependency_install_policy", {})
    if not dep_policy.get("new_dependencies_allowed", False):
        # Check for any package installation language in the plan
        install_indicators = [
            "pip install",
            "npm install",
            "yarn add",
            "poetry add",
            "uv pip install",
            "conda install",
            "apt install",
            "pip3 install",
            "python -m pip install",
        ]
        for indicator in install_indicators:
            if indicator in plan_text:
                violations.append(
                    f"plan proposes dependency installation but policy forbids it: '{indicator}'"
                )
                break

    return violations


def validate_plan_only_allowed_files(plan_text: str, packet: dict) -> list[str]:
    """
    If packet specifies allowed_files, verify the plan only references those.
    Empty allowed_files list means no files are allowed.
    Returns violation strings. Empty list = valid.
    """
    violations: list[str] = []
    task = packet.get("task", {})
    allowed_files = task.get("allowed_files", [])

    if allowed_files is not None and len(allowed_files) > 0:
        # Normalize plan text for checking
        for line in plan_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            words = stripped.split()
            for word in words:
                if "/" in word or word.startswith("."):
                    if "://" not in word and not word.startswith("-"):
                        path_part = word.rstrip(".,;:").split("<")[0].split(">")[0]
                        if path_part and not _is_forbidden_path(path_part):
                            matched = False
                            for allowed in allowed_files:
                                if path_part.startswith(allowed) or allowed in path_part:
                                    matched = True
                                    break
                            if not matched:
                                violations.append(
                                    f"plan references file not in allowed_files: {path_part}"
                                )
    elif allowed_files is not None and len(allowed_files) == 0:
        # Empty allowed_files list — no files permitted.
        # Flag any path-like word in the plan as a violation.
        for line in plan_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            words = stripped.split()
            for word in words:
                if ("/" in word or word.startswith("./") or word.startswith("../")) and "://" not in word:
                    violations.append(f"plan references file but allowed_files is empty: {word}")

    return violations


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

def build_plan_system_prompt(packet: dict) -> str:
    """
    Build a system prompt for --permission-mode plan that encodes the
    packet constraints and explicitly requests plan-only output.
    """
    task = packet.get("task", {})
    allowed_files = task.get("allowed_files", [])
    forbidden_files = task.get("forbidden_files", [])
    do_not = task.get("do_not", [])
    existing_code_reuse = task.get("existing_code_reuse", {})

    lines = [
        "You are producing a READ-ONLY PLAN for a coding task.",
        "You MUST NOT edit any files. You MUST NOT run any commands that modify the filesystem.",
        "You MUST NOT install packages. You MUST NOT create or close PRs. You MUST NOT merge.",
        "Your output is only a plan — a sequence of steps describing what would be done.",
        "Stop after presenting the plan.",
        "",
        "=== TASK ===",
        task.get("description", "(no description)"),
        "",
    ]

    if allowed_files:
        lines.append("=== ALLOWED FILES (read and plan against these only) ===")
        for f in allowed_files:
            lines.append(f"  {f}")
        lines.append("")

    if forbidden_files:
        lines.append("=== FORBIDDEN FILES (never reference or propose changes to these) ===")
        for f in forbidden_files:
            lines.append(f"  {f}")
        lines.append("")

    if do_not:
        lines.append("=== DO NOT ===")
        for d in do_not:
            lines.append(f"  {d}")
        lines.append("")

    ecr = existing_code_reuse or {}
    if ecr.get("enabled", False):
        lines.append("=== EXISTING CODE REUSE ===")
        for instr in ecr.get("instructions", []):
            lines.append(f"  - {instr}")
        lines.append("")

    dep_policy = task.get("dependency_install_policy", {})
    if not dep_policy.get("new_dependencies_allowed", False):
        lines.append("=== DEPENDENCY POLICY: No new dependencies may be installed ===")
        lines.append("")

    lines.extend([
        "=== OUTPUT FORMAT ===",
        "Provide your plan as a numbered list of steps.",
        "Each step should describe one action: which file would be changed, what the change is, and why.",
        "If a step references a file not in ALLOWED FILES, flag it in the step.",
        "If a step would install a new package, flag it as disallowed per the DEPENDENCY POLICY.",
        "If you cannot produce a safe plan given the constraints, say so explicitly.",
        "",
        "=== PLAN ===",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claude Code invocation
# ---------------------------------------------------------------------------

def invoke_claude_plan(
    packet: dict,
    output_dir: Path,
    *,
    timeout: int = 120,
) -> tuple[str, int]:
    """
    Invoke Claude Code in --permission-mode plan with the packet constraints.
    Returns (stdout, exit_code).
    """
    # Build system prompt
    system_prompt = build_plan_system_prompt(packet)

    # Build --add-dir arguments from allowed files (dedupe to directories)
    allowed_files = packet.get("task", {}).get("allowed_files", [])
    add_dirs: set[str] = set()
    for f in allowed_files:
        p = Path(f)
        if len(p.parts) > 1:
            add_dirs.add(str(p.parent))
        else:
            add_dirs.add(str(p))

    # Build claude command
    repo_root = _resolve_git_root()
    claude_args = [
        "claude",
        "--permission-mode", "plan",
        "-p", "PLAN",
        "--output-format", "stream-json",
        "--verbose",
    ]

    # Add --add-dir for each unique parent dir
    for d in sorted(add_dirs):
        claude_args.extend(["--add-dir", d])

    # If repo root known, add it
    if repo_root:
        claude_args.extend(["--add-dir", str(repo_root)])

    # Set ANTHROPIC_API_KEY from env if present (claude needs it)
    env = dict(os.environ)
    if "ANTHROPIC_API_KEY" not in env:
        # Try to make sure claude has the key — borrow from current env
        for k in ["ANTHROPIC_API_KEY"]:
            if k in env:
                pass  # already present

    # Write system prompt to temp file
    # Note: the file path is passed via --system-prompt-file arg; Claude reads it.
    # stdin=subprocess.DEVNULL is safe because --system-prompt-file provides
    # the full system prompt content to Claude. This avoids stdin timing issues
    # with piped mode when the process is long-running.
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        delete=False,
    ) as sp_f:
        sp_f.write(system_prompt)
        sp_path = sp_f.name

    try:
        claude_args.extend(["--system-prompt-file", sp_path])

        proc = subprocess.Popen(
            claude_args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(repo_root or "/tmp"),
            env=env,
        )

        stdout_parts: list[bytes] = []
        stderr_parts: list[bytes] = []

        # Collect fds to watch
        stdout_fd = proc.stdout.fileno()
        stderr_fd = proc.stderr.fileno() if proc.stderr else None

        elapsed = 0
        interval = 0.5
        while True:
            reads = [stdout_fd]
            if stderr_fd is not None:
                reads.append(stderr_fd)
            try:
                readable, _, _ = select.select(reads, [], [], interval)
            except OSError:
                break
            if stdout_fd in readable:
                data = os.read(stdout_fd, 8192)
                if not data:
                    break
                stdout_parts.append(data)
            if stderr_fd is not None and stderr_fd in readable:
                data = os.read(stderr_fd, 4096)
                if data:
                    stderr_parts.append(data)
            # Check if process exited
            poll_result = proc.poll()
            if poll_result is not None:
                break
            elapsed += interval
            if timeout > 0 and elapsed >= timeout:
                proc.kill()
                proc.wait()
                break

        exit_code = proc.wait()

        stdout = b"".join(stdout_parts).decode("utf-8", errors="replace")
        stderr = b"".join(stderr_parts).decode("utf-8", errors="replace")

        return stdout, stderr, exit_code

    finally:
        Path(sp_path).unlink(missing_ok=True)


def extract_plan_from_stream(stdout: str) -> str:
    """
    Extract plan text from Claude Code --output-format stream-json output.
    Returns the accumulated text content.
    """
    lines = stdout.splitlines()
    plan_parts: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            # Look for text content in streaming JSON
            if isinstance(obj, dict):
                # delta format
                if "content" in obj:
                    content = obj["content"]
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                plan_parts.append(block.get("text", ""))
                    elif isinstance(content, str):
                        plan_parts.append(content)
                # simple text format
                elif "text" in obj:
                    plan_parts.append(obj["text"])
        except json.JSONDecodeError:
            continue
    return "\n".join(plan_parts)


# ---------------------------------------------------------------------------
# Result builder
# ---------------------------------------------------------------------------

def build_result(
    status: str,
    packet_path: str,
    output_dir: Path,
    plan_text: str,
    validation_errors: list[str],
    git_status_before: str,
    git_status_after: str,
    metadata: dict,
) -> dict:
    """Build a plan-preview result dict."""
    run_id = output_dir.name if output_dir.name else "unknown"
    return {
        "status": status,
        "run_id": run_id,
        "packet_path": str(packet_path),
        "output_dir": str(output_dir),
        "timestamp": _now_iso(),
        "git_status_before": git_status_before,
        "git_status_after": git_status_after,
        "repo_mutated": git_status_before != git_status_after,
        "validation_errors": validation_errors,
        "plan_length_chars": len(plan_text),
        "plan_preview": plan_text[:2000] if plan_text else "",
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    # Load packet
    packet = _load_json(args.packet_json)
    if not packet:
        print(f"ERROR: packet not found or not readable: {args.packet_json}", file=sys.stderr)
        return 1

    # Validate packet
    errors = validate_packet(packet)
    if errors:
        print("ERROR: invalid packet:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        result = build_result(
            "PLAN_PREVIEW_ERROR",
            args.packet_json,
            Path(args.output_dir),
            "",
            errors,
            "unknown",
            "unknown",
            {"error_type": "invalid_packet"},
        )
        _write_result(result, args)
        return 1

    # Determine output dir
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Git status before
    repo_root = _resolve_git_root()
    git_status_before = _git_status(repo_root) if repo_root else "not_in_repo"

    # Check if output dir is inside repo
    if repo_root:
        try:
            output_dir.relative_to(repo_root)
            # Output dir is inside repo — BLOCK
            print(f"ERROR: output_dir must be outside repo: {output_dir}", file=sys.stderr)
            result = build_result(
                "PLAN_PREVIEW_ERROR",
                args.packet_json,
                output_dir,
                "",
                ["output_dir must be outside the repo"],
                git_status_before,
                git_status_before,
                {"error_type": "output_dir_in_repo"},
            )
            _write_result(result, args)
            return 1
        except ValueError:
            pass  # OK — outside repo

    # Invoke Claude Code plan mode
    stdout, stderr, exit_code = invoke_claude_plan(packet, output_dir, timeout=args.timeout)

    # Extract plan
    plan_text = extract_plan_from_stream(stdout)

    # Git status after — always checked, even on Claude failure/timeout
    git_status_after = _git_status(repo_root) if repo_root else "not_in_repo"

    # Fail closed on Claude errors: nonzero exit, empty output, or timeout
    # timeout is detected by checking if elapsed >= timeout in invoke_claude_plan
    # which results in partial/empty output. Treat these as PLAN_PREVIEW_ERROR.
    if exit_code != 0:
        # Grab a safe snippet of stderr to help diagnose the error.
        # Truncate to first 200 chars — enough to identify the error category
        # without leaking session content, keys, or hook internals.
        stderr_snippet = (stderr or "").strip()[:200]
        result = build_result(
            "PLAN_PREVIEW_ERROR",
            args.packet_json,
            output_dir,
            plan_text,
            [f"claude exited with code {exit_code}" + (f": {stderr_snippet}" if stderr_snippet else "")],
            git_status_before,
            git_status_after,
            {"error_type": "claude_nonzero_exit", "claude_exit_code": exit_code, "stderr_snippet": stderr_snippet},
        )
        _write_result(result, args)
        return 1

    if not plan_text or not plan_text.strip():
        result = build_result(
            "PLAN_PREVIEW_ERROR",
            args.packet_json,
            output_dir,
            plan_text,
            ["claude returned empty plan output"],
            git_status_before,
            git_status_after,
            {"error_type": "empty_plan_output"},
        )
        _write_result(result, args)
        return 1

    # Detect repo mutation — any change from before is a block
    if git_status_before != git_status_after:
        result = build_result(
            "PLAN_PREVIEW_BLOCKED",
            args.packet_json,
            output_dir,
            plan_text,
            [f"repo git status changed during preview: {git_status_before} -> {git_status_after}"],
            git_status_before,
            git_status_after,
            {"error_type": "repo_mutated"},
        )
        _write_result(result, args)
        return 1

    # Validate plan against packet
    violations: list[str] = []
    violations.extend(validate_plan_against_packet(plan_text, packet))
    violations.extend(validate_plan_only_allowed_files(plan_text, packet))

    if violations:
        result = build_result(
            "PLAN_PREVIEW_BLOCKED",
            args.packet_json,
            output_dir,
            plan_text,
            violations,
            git_status_before,
            git_status_after,
            {"error_type": "plan_violates_constraints"},
        )
        _write_result(result, args)
        return 1

    # Success
    result = build_result(
        "PLAN_PREVIEW_READY",
        args.packet_json,
        output_dir,
        plan_text,
        [],
        git_status_before,
        git_status_after,
        {"claude_exit_code": exit_code},
    )
    _write_result(result, args)
    return 0


def _write_result(result: dict, args: argparse.Namespace) -> None:
    if args.output_json:
        with open(args.output_json, "w") as f:
            json.dump(result, f, indent=2)
    print(f"STATUS: {result['status']}")
    if args.output_md:
        lines = [
            f"# Plan Preview Result: {result['status']}",
            f"",
            f"**Packet:** `{result['packet_path']}`",
            f"**Output dir:** `{result['output_dir']}`",
            f"**Timestamp:** {result['timestamp']}",
            f"**Git status:** before={result['git_status_before']}, after={result['git_status_after']}",
            f"**Repo mutated:** {result['repo_mutated']}",
            f"",
        ]
        if result["validation_errors"]:
            lines.append("## Validation Errors")
            for e in result["validation_errors"]:
                lines.append(f"- {e}")
            lines.append("")
        if result["plan_preview"]:
            lines.append("## Plan Preview")
            lines.append("```")
            lines.append(result["plan_preview"][:1500])
            lines.append("```")
        with open(args.output_md, "w") as f:
            f.write("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan-preview Claude Code from a worker packet. No file edits, no execution."
    )
    parser.add_argument(
        "--packet-json",
        required=True,
        help="Path to aed.worker.packet.v1 JSON file",
    )
    parser.add_argument(
        "--output-dir",
        default="/tmp/aed_runs/default/plan_preview",
        help="Output directory for plan-preview results (must be outside repo)",
    )
    parser.add_argument(
        "--output-json",
        help="Path to write result JSON",
    )
    parser.add_argument(
        "--output-md",
        help="Path to write result markdown",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Timeout for Claude Code invocation in seconds",
    )
    args = parser.parse_args()

    return run(args)


if __name__ == "__main__":
    sys.exit(main())