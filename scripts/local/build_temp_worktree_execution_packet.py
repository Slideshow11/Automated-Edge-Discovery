#!/usr/bin/env python3
"""
build_temp_worktree_execution_packet.py

Bridge from approved plan to temp-worktree execution packet.

Given a validated, human-approved plan file and constraint lists, produces a
fully-formed execution packet for the mock-only temp-worktree harness.

No Claude execution. No worktree creation. No repo mutation. No push. No PR.

Usage:
    python3 scripts/local/build_temp_worktree_execution_packet.py \\
        --run-id <run_id> \\
        --task-id <task_id> \\
        --task-description <text> \\
        --approved-plan-path /path/to/approved_plan.txt \\
        --allowed-files-json /path/to/allowed_files.json \\
        --forbidden-files-json /path/to/forbidden_files.json \\
        --do-not-json /path/to/do_not.json \\
        --output-root /tmp/aed_runs/<run_id>/ \\
        --output-json /tmp/aed_runs/<run_id>/execution_packet.json \\
        [--base-sha <sha>] \\
        [--max-changed-files <n>] \\
        [--mock-edits-json /path/to/mock_edits.json]

Exit codes:
    0 — packet written
    1 — validation error (missing file, bad JSON, etc.)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent.resolve()


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a temp-worktree execution packet from an approved plan."
    )
    parser.add_argument("--run-id", required=True, help="Unique run identifier")
    parser.add_argument("--task-id", required=True, help="Task identifier (e.g. TASK-001)")
    parser.add_argument("--task-description", required=True, help="Human-readable task description")
    parser.add_argument("--approved-plan-path", required=True, type=str,
                        help="Path to the approved plan file")
    parser.add_argument("--allowed-files-json", required=True, type=str,
                        help="Path to JSON file containing list of allowed file paths")
    parser.add_argument("--forbidden-files-json", required=True, type=str,
                        help="Path to JSON file containing list of forbidden file paths")
    parser.add_argument("--do-not-json", required=True, type=str,
                        help="Path to JSON file containing list of do-not instructions")
    parser.add_argument("--output-root", required=True, type=str,
                        help="Root directory for harness output (must be outside repo)")
    parser.add_argument("--output-json", required=True, type=str,
                        help="Path to write the execution packet JSON")
    parser.add_argument("--base-sha", type=str, default=None,
                        help="Base SHA for worktree (defaults to current HEAD)")
    parser.add_argument("--max-changed-files", type=int, default=2,
                        help="Maximum changed files allowed (default: 2)")
    parser.add_argument("--mock-edits-json", type=str, default=None,
                        help="Path to JSON file containing list of mock edits")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_json_list_file(path: Path, name: str) -> list[str]:
    """Read a JSON file and validate it is a list of strings. Returns the list."""
    if not path.is_file():
        raise FileNotFoundError(f"{name} file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"{name} is not valid JSON: {e}")

    if not isinstance(data, list):
        raise ValueError(f"{name} must be a JSON list, got {type(data).__name__}")

    for item in data:
        if not isinstance(item, str):
            raise ValueError(f"{name} must contain only strings; got {type(item).__name__}: {item!r}")

    return data


def validate_mock_edits_file(path: Path) -> list[dict]:
    """Read and validate a mock_edits JSON file. Returns the list."""
    if not path.is_file():
        raise FileNotFoundError(f"mock_edits JSON file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"mock_edits JSON is not valid JSON: {e}")

    if not isinstance(data, list):
        raise ValueError(f"mock_edits must be a JSON list, got {type(data).__name__}")

    for i, edit in enumerate(data):
        if not isinstance(edit, dict):
            raise ValueError(f"mock_edits[{i}] must be an object, got {type(edit).__name__}")
        if "path" not in edit:
            raise ValueError(f"mock_edits[{i}] missing required field: path")
        if "content" not in edit:
            raise ValueError(f"mock_edits[{i}] missing required field: content")
        if not isinstance(edit["path"], str) or not edit["path"]:
            raise ValueError(f"mock_edits[{i}].path must be a non-empty string")
        if not isinstance(edit["content"], str):
            raise ValueError(f"mock_edits[{i}].content must be a string")

    return data


def validate_output_root_not_in_repo(output_root: Path) -> None:
    """Fail if output_root is inside the repo."""
    resolved = str(output_root.resolve())
    repo_str = str(REPO_ROOT.resolve())
    if resolved.startswith(repo_str):
        raise ValueError(f"output_root cannot be inside repo: {resolved}")


def now_iso() -> str:
    """Return current UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_packet(args: argparse.Namespace) -> dict:
    """Build and return the execution packet dict. Does not write files."""

    # 1. Validate approved plan path
    approved_plan_path = Path(args.approved_plan_path).resolve()
    if not approved_plan_path.is_file():
        raise FileNotFoundError(f"approved plan file not found: {approved_plan_path}")

    # 2. Compute SHA-256 of approved plan
    approved_plan_sha256 = sha256_file(approved_plan_path)

    # 3. Validate allowed_files_json
    allowed_files = validate_json_list_file(
        Path(args.allowed_files_json), "allowed_files_json"
    )

    # 4. Validate forbidden_files_json
    forbidden_files = validate_json_list_file(
        Path(args.forbidden_files_json), "forbidden_files_json"
    )

    # 5. Validate do_not_json
    do_not = validate_json_list_file(Path(args.do_not_json), "do_not_json")

    # 6. Validate mock_edits_json if provided
    mock_edits = None
    if args.mock_edits_json:
        mock_edits = validate_mock_edits_file(Path(args.mock_edits_json))

    # 7. Resolve base_sha
    if args.base_sha:
        base_sha = args.base_sha.strip()
        if not base_sha:
            raise ValueError("base-sha cannot be empty")
    else:
        import subprocess
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            raise RuntimeError(f"git rev-parse HEAD failed: {result.stderr.strip()}")
        base_sha = result.stdout.strip()

    # 8. Validate output_root not inside repo
    output_root = Path(args.output_root).resolve()
    validate_output_root_not_in_repo(output_root)

    # 9. Build packet
    packet = {
        "packet_kind": "aed.temp_worktree.execution.v0",
        "run_id": args.run_id,
        "task_id": args.task_id,
        "base_sha": base_sha,
        "approved_plan_path": str(approved_plan_path),
        "approved_plan_sha256": approved_plan_sha256,
        "approval": {
            "approved_for_temp_worktree_execution": True,
            "approved_by": "human",
            "approved_plan_sha256": approved_plan_sha256,
            "approved_at": now_iso(),
            "max_changed_files": args.max_changed_files,
        },
        "task": {
            "description": args.task_description,
            "allowed_files": allowed_files,
            "forbidden_files": forbidden_files,
            "do_not": do_not,
        },
        "execution": {
            "mode": "mock",
            "output_root": str(output_root),
            "timeout_seconds": 60,
        },
    }

    # 10. Add mock_edits only if provided
    if mock_edits is not None:
        packet["execution"]["mock_edits"] = mock_edits

    return packet


def write_packet(packet: dict, output_json: str) -> None:
    """Write packet to output_json path."""
    output_path = Path(output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(packet, f, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    try:
        packet = build_packet(args)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"VALIDATION ERROR: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    try:
        write_packet(packet, args.output_json)
    except Exception as e:
        print(f"ERROR: failed to write packet: {e}", file=sys.stderr)
        return 1

    print(f"Packet written to {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())