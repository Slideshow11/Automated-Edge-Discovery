#!/usr/bin/env python3
"""
phase_exec.py — small command wrapper that records canonical phase-execution
evidence in the phase ledger.

Purpose:
  Wrap any subprocess call (e.g. `gh pr checks 389 ...`) with one-line
  invocation:

      python3 scripts/local/phase_exec.py \\
          --ledger /tmp/aed_runs/example/phase_ledger.jsonl \\
          --run-id example \\
          --phase-id PHASE_2_CONFIRM_CI \\
          --phase-index 2 \\
          --observed-summary "5/5 CI checks passed" \\
          --source-task-id aed.phase-ledger.v0 \\
          --task-packet-id phase-ledger-pr1 \\
          -- gh pr checks 389 --repo Slideshow11/Automated-Edge-Discovery

Behavior:
  1. Creates a unique per-invocation artifact directory under
     <ledger_parent>/artifacts/<phase_id>-<timestamp>/.
  2. Captures stdout → <artifact_dir>/stdout.txt
                stderr → <artifact_dir>/stderr.txt
  3. Runs the command with subprocess.run; does NOT enforce a timeout
     by default (caller can pass --timeout-seconds).
  4. Builds a canonical phase-ledger entry with writer=phase_exec and
     appends it to the ledger.
  5. Exits with the wrapped command's exit code (so callers can chain).

The wrapper itself never reports PASS/FAIL — it records what the
underlying command observed (exit code) and writes the artifact files.
The validator (validate_phase_ledger.py) is the only authority for
emitting HOLD_UNEVIDENCED_PASS.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from phase_ledger import build_entry, append_entry


def _default_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(value: str) -> str:
    """Make a filesystem-friendly slug from a phase_id."""
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in value)


def _unique_artifact_suffix() -> str:
    """Return a high-entropy suffix for an artifact directory name.

    Combines a microsecond-precision UTC timestamp with a short random
    uuid hex prefix. This guarantees uniqueness for two rapid invocations
    of the same phase_id — including invocations within the same wall
    clock second — so that artifact files (stdout.txt, stderr.txt) and
    the ledger entries pointing at them are never silently overwritten.
    """
    microstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    nonce = uuid.uuid4().hex[:8]
    return f"{microstamp}-{nonce}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a command and record canonical phase-execution evidence "
            "in the phase ledger."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--ledger", required=True,
        help="Path to phase_ledger.jsonl (created if missing)",
    )
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--phase-id", required=True, help="Phase identifier (e.g. PHASE_2_CONFIRM_CI)")
    parser.add_argument("--phase-index", type=int, default=None, help="Phase index (1-based)")
    parser.add_argument(
        "--observed-summary", default="",
        help="Human-readable summary recorded as observed_summary",
    )
    parser.add_argument(
        "--source-task-id", default=None,
        help="Optional task-list linkage: source task id",
    )
    parser.add_argument(
        "--task-packet-id", default=None,
        help="Optional task-list linkage: task packet id",
    )
    parser.add_argument(
        "--roadmap-item-id", default=None,
        help="Optional task-list linkage: roadmap item id",
    )
    parser.add_argument(
        "--timeout-seconds", type=int, default=None,
        help="Optional subprocess timeout in seconds",
    )
    parser.add_argument(
        "--cwd", default=None,
        help="Optional working directory for the wrapped command",
    )
    parser.add_argument(
        "--artifacts-dir-name", default=None,
        help="Optional override for the artifacts subdirectory name",
    )
    # Everything after `--` is the command to run.
    parser.add_argument(
        "rest", nargs=argparse.REMAINDER,
        help="Command and arguments, separated from phase_exec args by `--`",
    )

    args = parser.parse_args(argv)

    # Strip a single leading "--" if REMAINDER captured it
    cmd = list(args.rest)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("ERROR: no command provided after `--`", file=sys.stderr)
        return 1

    ledger_path = Path(args.ledger).resolve()
    artifacts_root = ledger_path.parent / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)

    slug = _slugify(args.phase_id)
    if args.artifacts_dir_name:
        artifact_dir_name = args.artifacts_dir_name
    else:
        artifact_dir_name = f"{slug}-{_unique_artifact_suffix()}"
    artifact_dir = artifacts_root / artifact_dir_name
    # If the directory already exists, that is a hard error: the unique
    # suffix should make this impossible for rapid invocations of the
    # same phase_id, and a pre-existing directory means evidence from a
    # prior run is about to be silently overwritten.
    artifact_dir.mkdir(parents=True, exist_ok=False)

    stdout_path = artifact_dir / "stdout.txt"
    stderr_path = artifact_dir / "stderr.txt"

    cwd = args.cwd or str(Path.cwd())

    # Run the wrapped command
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=args.timeout_seconds,
        )
        exit_code = proc.returncode
        stdout_text = proc.stdout
        stderr_text = proc.stderr
    except subprocess.TimeoutExpired as e:
        stdout_text = e.stdout.decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr_text = (e.stderr.decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")) + f"\n[phase_exec] timeout after {args.timeout_seconds}s"
        exit_code = -1
    except FileNotFoundError as e:
        stdout_text = ""
        stderr_text = f"[phase_exec] command not found: {e}"
        exit_code = 127
    except Exception as e:
        stdout_text = ""
        stderr_text = f"[phase_exec] invocation error: {e}"
        exit_code = -1

    # Write artifact files
    stdout_path.write_text(stdout_text)
    stderr_path.write_text(stderr_text)

    stdout_bytes = stdout_path.stat().st_size
    stderr_bytes = stderr_path.stat().st_size

    # Map exit code to ledger status
    if exit_code == 0:
        status = "PASS"
    else:
        status = "FAIL"

    # Build and append the canonical ledger entry
    entry = build_entry(
        run_id=args.run_id,
        phase_id=args.phase_id,
        phase_index=args.phase_index,
        writer="phase_exec",
        script=None,  # the wrapped command itself is the script
        argv=cmd,
        exit_code=exit_code,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        stdout_size_bytes=stdout_bytes,
        stderr_size_bytes=stderr_bytes,
        observed_summary=args.observed_summary,
        status=status,
        timestamp=_default_timestamp(),
        source_task_id=args.source_task_id,
        task_packet_id=args.task_packet_id,
        roadmap_item_id=args.roadmap_item_id,
    )
    append_entry(entry, ledger_path)

    # Echo a one-line summary for log readability
    print(
        f"phase_exec: phase={args.phase_id} exit_code={exit_code} "
        f"status={status} stdout={stdout_path} stderr={stderr_path} "
        f"ledger={ledger_path}"
    )

    # Propagate the wrapped command's exit code
    return 0 if exit_code == 0 else exit_code


if __name__ == "__main__":
    raise SystemExit(main())
