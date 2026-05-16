#!/usr/bin/env python3
"""
build_quarantine_bundle_index.py

Manifest-driven batch scaffold for AED quarantine overnight work.
Produces a BUNDLE_INDEX.json from a TASKS.jsonl manifest.

v1: No agent execution. No patch generation. No PR creation.
     Each task is a planned bundle, not yet run.

Usage:
    python3 scripts/local/build_quarantine_bundle_index.py \\
        --tasks-jsonl tasks.jsonl \\
        --bundle-root /path/to/bundles \\
        --repo Slideshow11/Automated-Edge-Discovery \\
        --base-sha ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0 \\
        --output-index BUNDLE_INDEX.json \\
        --dry-run

Exit codes:
    0  — index produced successfully
    1  — validation error or missing required argument
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO_DEFAULT = "Slideshow11/Automated-Edge-Discovery"
HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

VALID_TASK_TYPES = frozenset([
    "docs_consistency",
    "test_gap",
    "fixture_schema_alignment",
    "dependency_hygiene",
    "ci_hygiene",
    "safety_grep_audit",
    "repo_map",
    "design_note",
    "other",
])

VALID_RISK_LEVELS = frozenset(["low", "medium", "high"])

REQUIRED_TASK_FIELDS = frozenset([
    "task_id",
    "objective",
    "task_type",
    "risk_level",
    "allowed_files",
    "forbidden_files",
    "expected_outputs",
])

SAFE_TASK_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_base_sha(sha: Optional[str]) -> Optional[str]:
    if sha is None:
        return None
    if not HEX_SHA_RE.match(sha):
        return f"base_sha must be a 40-char hex string, got: {sha!r}"
    return None


def validate_task_id(task_id: Any) -> Optional[str]:
    if not isinstance(task_id, str):
        return f"task_id must be a string, got: {type(task_id).__name__}"
    if not task_id:
        return "task_id must be non-empty"
    if not SAFE_TASK_ID_RE.match(task_id):
        return (
            f"task_id must be a safe slug (a-z, A-Z, 0-9, _, -), "
            f"got: {task_id!r}"
        )
    return None


def validate_objective(objective: Any) -> Optional[str]:
    if not isinstance(objective, str):
        return f"objective must be a string, got: {type(objective).__name__}"
    if not objective.strip():
        return "objective must be non-empty"
    return None


def validate_task_type(task_type: Any) -> Optional[str]:
    if not isinstance(task_type, str):
        return f"task_type must be a string, got: {type(task_type).__name__}"
    if task_type not in VALID_TASK_TYPES:
        return (
            f"task_type must be one of {sorted(VALID_TASK_TYPES)}, "
            f"got: {task_type!r}"
        )
    return None


def validate_risk_level(risk_level: Any) -> Optional[str]:
    if not isinstance(risk_level, str):
        return f"risk_level must be a string, got: {type(risk_level).__name__}"
    if risk_level not in VALID_RISK_LEVELS:
        return (
            f"risk_level must be one of {sorted(VALID_RISK_LEVELS)}, "
            f"got: {risk_level!r}"
        )
    return None


def validate_file_list(field_name: str, value: Any, allow_empty: bool = False) -> Optional[str]:
    if not isinstance(value, list):
        return f"{field_name} must be a list, got: {type(value).__name__}"
    if not allow_empty and len(value) == 0:
        return f"{field_name} must be non-empty (got empty list)"
    return None


def validate_expected_outputs(value: Any) -> Optional[str]:
    if not isinstance(value, list):
        return f"expected_outputs must be a list, got: {type(value).__name__}"
    if len(value) == 0:
        return "expected_outputs must be non-empty"
    for item in value:
        if not isinstance(item, str):
            return (
                f"expected_outputs items must be strings, "
                f"got: {type(item).__name__} in {value!r}"
            )
    return None


def validate_task(task: dict[str, Any], seen_task_ids: set[str]) -> list[str]:
    """Validate a single task dict. Returns list of error messages."""
    errors = []

    # Required field presence
    for field in REQUIRED_TASK_FIELDS:
        if field not in task:
            errors.append(f"missing required field: {field}")

    if errors:
        return errors

    # Field-level validation
    if e := validate_task_id(task.get("task_id")):
        errors.append(f"task_id: {e}")

    if e := validate_objective(task.get("objective")):
        errors.append(f"objective: {e}")

    if e := validate_task_type(task.get("task_type")):
        errors.append(f"task_type: {e}")

    if e := validate_risk_level(task.get("risk_level")):
        errors.append(f"risk_level: {e}")

    if e := validate_file_list("allowed_files", task.get("allowed_files"), allow_empty=False):
        errors.append(f"allowed_files: {e}")

    # forbidden_files must be present even if empty
    if e := validate_file_list("forbidden_files", task.get("forbidden_files"), allow_empty=True):
        errors.append(f"forbidden_files: {e}")

    if e := validate_expected_outputs(task.get("expected_outputs")):
        errors.append(f"expected_outputs: {e}")

    # Duplicate task_id check
    task_id = task.get("task_id")
    if task_id in seen_task_ids:
        errors.append(f"duplicate task_id: {task_id!r}")

    # Per-task base_sha validation (index-level CLI --base-sha is validated separately)
    per_task_base_sha = task.get("base_sha")
    if per_task_base_sha is not None:
        if e := validate_base_sha(per_task_base_sha):
            errors.append(f"base_sha: {e}")

    return errors


# ---------------------------------------------------------------------------
# Bundle path generation
# ---------------------------------------------------------------------------

def make_bundle_path(bundle_root: str, task_id: str) -> str:
    """Generate the bundle directory path for a task."""
    return os.path.join(bundle_root, task_id)


# ---------------------------------------------------------------------------
# Main index builder
# ---------------------------------------------------------------------------

def build_index(
    tasks_jsonl: str,
    bundle_root: str,
    repo: str,
    base_sha: Optional[str],
    output_index: str,
    force: bool,
) -> dict[str, Any]:
    """
    Build BUNDLE_INDEX.json from a TASKS.jsonl manifest.

    Args:
        tasks_jsonl: path to TASKS.jsonl
        bundle_root: root directory for all bundles
        repo: repository name
        base_sha: optional base SHA override
        output_index: path to write BUNDLE_INDEX.json
        force: overwrite existing index without error

    Returns:
        The index dict (also written to output_index)

    Raises:
        ValueError: on validation errors
        RuntimeError: on file I/O errors
    """
    if not os.path.exists(tasks_jsonl):
        raise ValueError(f"TASKS.jsonl not found: {tasks_jsonl}")

    # Validate base_sha format if provided
    if base_sha is not None:
        if e := validate_base_sha(base_sha):
            raise ValueError(e)

    # Parse TASKS.jsonl
    tasks: list[dict[str, Any]] = []
    errors_by_line: dict[int, list[str]] = {}
    seen_task_ids: set[str] = set()

    with open(tasks_jsonl, "r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue  # skip blank lines
            try:
                task = json.loads(raw_line)
            except json.JSONDecodeError as ex:
                errors_by_line[line_no] = [f"invalid JSON: {ex}"]
                continue

            errs = validate_task(task, seen_task_ids)
            if errs:
                errors_by_line[line_no] = errs
                continue

            tasks.append(task)
            seen_task_ids.add(task["task_id"])

    if errors_by_line:
        lines_summary = ", ".join(f"line {ln}: {'; '.join(errs)}" for ln, errs in errors_by_line.items())
        raise ValueError(f"TASK VALIDATION FAILED: {lines_summary}")

    if len(tasks) == 0:
        raise ValueError("TASKS.jsonl contains no valid tasks")

    # Check output_index
    if os.path.exists(output_index) and not force:
        raise ValueError(
            f"output index {output_index!r} exists. Use --force to overwrite."
        )

    # Build task entries
    task_entries = []
    for task in tasks:
        entry = {
            "task_id": task["task_id"],
            "objective": task["objective"],
            "task_type": task["task_type"],
            "risk_level": task["risk_level"],
            "allowed_files": task.get("allowed_files", []),
            "forbidden_files": task.get("forbidden_files", []),
            "expected_outputs": task.get("expected_outputs", []),
            "bundle_path": make_bundle_path(bundle_root, task["task_id"]),
            "status": "planned",
            "promotion_recommendation": "not_evaluated",
            "process_score_status": "not_evaluated",
        }
        # Copy optional fields if present
        for opt_field in ("base_sha", "priority", "notes", "reviewer_hint", "promotion_target"):
            if opt_field in task:
                entry[opt_field] = task[opt_field]

        task_entries.append(entry)

    index = {
        "index_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": repo,
        "base_sha": base_sha or "",
        "bundle_root": bundle_root,
        "dry_run": True,
        "task_count": len(task_entries),
        "tasks": task_entries,
        "agent_executed": False,
        "patch_applied": False,
        "dispatch_occurred": False,
        "hermes_touched": False,
        "production_board_touched": False,
        "pr_created": False,
        "import_performed": False,
    }

    # Write index
    with open(output_index, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)

    return index


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a BUNDLE_INDEX.json from a TASKS.jsonl manifest. "
                    "v1: no agent execution, no patch generation, no PR creation."
    )
    parser.add_argument(
        "--tasks-jsonl",
        required=True,
        help="Path to TASKS.jsonl manifest",
    )
    parser.add_argument(
        "--bundle-root",
        required=True,
        help="Root directory for all task bundles",
    )
    parser.add_argument(
        "--repo",
        default=REPO_DEFAULT,
        help=f"Repository name (default: {REPO_DEFAULT})",
    )
    parser.add_argument(
        "--base-sha",
        help="Base SHA for all tasks (40-char hex, optional)",
    )
    parser.add_argument(
        "--output-index",
        required=True,
        help="Path to write BUNDLE_INDEX.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Required flag. Refuses to run without --dry-run.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing output index without error",
    )

    args = parser.parse_args(argv)

    # Refuse without --dry-run
    if not args.dry_run:
        print(
            "ERROR: --dry-run is required. This tool does not execute agents, "
            "generate patches, or create PRs. Use --dry-run to confirm intent.",
            file=sys.stderr,
        )
        return 1

    try:
        index = build_index(
            tasks_jsonl=args.tasks_jsonl,
            bundle_root=args.bundle_root,
            repo=args.repo,
            base_sha=args.base_sha,
            output_index=args.output_index,
            force=args.force,
        )
    except ValueError as e:
        print(f"VALIDATION ERROR: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # Summary output
    print(f"BUNDLE_INDEX.json written: {args.output_index}")
    print(f"Tasks: {index['task_count']}")
    print(f"Repo: {index['repo']}")
    print(f"Base SHA: {index['base_sha'] or '(not specified)'}")
    print(f"Safety: agent_executed={index['agent_executed']} "
          f"patch_applied={index['patch_applied']} "
          f"dispatch_occurred={index['dispatch_occurred']}")
    print()
    print("All tasks status=planned, promotion_recommendation=not_evaluated.")
    print("Future phases may run the quarantine bundle generator per task.")

    return 0


if __name__ == "__main__":
    sys.exit(main())