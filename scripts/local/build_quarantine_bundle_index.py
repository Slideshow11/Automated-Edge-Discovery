#!/usr/bin/env python3
"""
build_quarantine_bundle_index.py

Manifest-driven batch scaffold for AED quarantine overnight work.
Produces a BUNDLE_INDEX.json from a TASKS.jsonl manifest.

v1: No agent execution. No patch generation. No PR creation.
     Each task is a planned bundle, not yet run.

v2 (this file): Adds task dependency and integration planning metadata.

Usage:
    python3 scripts/local/build_quarantine_bundle_index.py \
        --tasks-jsonl tasks.jsonl \
        --bundle-root /path/to/bundles \
        --repo Slideshow11/Automated-Edge-Discovery \
        --base-sha ccf30dfb6f12f792a6f254dfe3d9ff52486f3ec0 \
        --output-index BUNDLE_INDEX.json \
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
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO_DEFAULT = "Slideshow11/Automated-Edge-Discovery"
HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

VALID_TASK_TYPES = frozenset([
    "docs_consistency",
    "test_gap",
    "test_gap_analysis",
    "fixture_schema_alignment",
    "dependency_hygiene",
    "ci_hygiene",
    "ci_review",
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

# New v2 optional dependency fields
DEPENDENCY_OPTIONAL_FIELDS = frozenset([
    "depends_on",
    "blocks",
    "promotion_group",
    "pr_group",
    "can_run_in_parallel",
    "integration_order",
    "promotion_target",
])

# Safe slug pattern for promotion_group and pr_group
SAFE_SLUG_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


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


def validate_safe_slug(value: Any, field_name: str) -> Optional[str]:
    """Validate that a value is a safe slug string (a-z, A-Z, 0-9, _, -)."""
    if not isinstance(value, str):
        return f"{field_name} must be a string, got: {type(value).__name__}"
    if not SAFE_SLUG_RE.match(value):
        return f"{field_name} must be a safe slug (a-z, A-Z, 0-9, _, -), got: {value!r}"
    return None


def validate_task_dependency_fields(task: dict[str, Any]) -> list[str]:
    """Validate optional dependency-related fields in a task. Returns list of error messages."""
    errors = []

    # depends_on
    if "depends_on" in task:
        val = task["depends_on"]
        if not isinstance(val, list):
            errors.append("depends_on must be a list")
        else:
            for item in val:
                if not isinstance(item, str):
                    errors.append(f"depends_on items must be strings, got: {type(item).__name__}")
                elif not SAFE_TASK_ID_RE.match(item):
                    errors.append(f"depends_on contains invalid task_id: {item!r}")

    # blocks
    if "blocks" in task:
        val = task["blocks"]
        if not isinstance(val, list):
            errors.append("blocks must be a list")
        else:
            for item in val:
                if not isinstance(item, str):
                    errors.append(f"blocks items must be strings, got: {type(item).__name__}")
                elif not SAFE_TASK_ID_RE.match(item):
                    errors.append(f"blocks contains invalid task_id: {item!r}")

    # promotion_group
    if "promotion_group" in task:
        if e := validate_safe_slug(task["promotion_group"], "promotion_group"):
            errors.append(e)

    # pr_group
    if "pr_group" in task:
        if e := validate_safe_slug(task["pr_group"], "pr_group"):
            errors.append(e)

    # can_run_in_parallel
    if "can_run_in_parallel" in task:
        val = task["can_run_in_parallel"]
        if not isinstance(val, bool):
            errors.append(f"can_run_in_parallel must be a boolean, got: {type(val).__name__}")

    # integration_order
    if "integration_order" in task:
        val = task["integration_order"]
        if not isinstance(val, int):
            errors.append(f"integration_order must be an integer, got: {type(val).__name__}")

    # promotion_target
    if "promotion_target" in task:
        val = task["promotion_target"]
        if not isinstance(val, str):
            errors.append(f"promotion_target must be a string, got: {type(val).__name__}")

    return errors


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

    # Per-task base_sha validation
    per_task_base_sha = task.get("base_sha")
    if per_task_base_sha is not None:
        if e := validate_base_sha(per_task_base_sha):
            errors.append(f"base_sha: {e}")

    # v2: dependency field validation
    dep_errors = validate_task_dependency_fields(task)
    errors.extend(dep_errors)

    return errors


# ---------------------------------------------------------------------------
# Dependency graph validation
# ---------------------------------------------------------------------------

class DependencyGraphValidator:
    """Validates task dependency graph and computes integration order."""

    def __init__(self, tasks: list[dict[str, Any]]):
        self.tasks = tasks
        self.task_ids = {t["task_id"] for t in tasks}
        self.errors: list[str] = []
        self.dependency_edges: list[dict] = []
        self.block_edges: list[dict] = []
        self.promotion_groups: dict[str, list[str]] = {}
        self.pr_groups: dict[str, list[str]] = {}
        self.parallel_groups: list[list[str]] = []
        self.ordered_task_ids: list[str] = []
        self.downstream_blocked_map: dict[str, list[str]] = {}

    def validate(self) -> list[str]:
        """Run all dependency validations. Returns error list."""
        self._check_self_dependencies()
        self._check_unknown_dependencies()
        self._check_duplicate_dependency_refs()
        self._check_dependency_cycles()
        self._check_self_blocks()
        self._check_unknown_block_targets()
        self._check_duplicate_block_refs()
        self._collect_promotion_groups()
        self._collect_pr_groups()
        self._compute_integration_order()
        self._compute_downstream_blocked()
        self._compute_parallel_groups()
        return self.errors

    def _add_error(self, code: str, message: str):
        self.errors.append(f"{code}: {message}")

    def _check_self_dependencies(self):
        for task in self.tasks:
            tid = task["task_id"]
            deps = task.get("depends_on", [])
            for dep in deps:
                if dep == tid:
                    self._add_error("self_dependency", f"Task '{tid}' depends on itself")

    def _check_unknown_dependencies(self):
        for task in self.tasks:
            tid = task["task_id"]
            deps = task.get("depends_on", [])
            for dep in deps:
                if dep not in self.task_ids:
                    self._add_error("unknown_dependency", f"Task '{tid}' depends on unknown task '{dep}'")

    def _check_duplicate_dependency_refs(self):
        for task in self.tasks:
            tid = task["task_id"]
            deps = task.get("depends_on", [])
            seen = set()
            for dep in deps:
                if dep in seen:
                    self._add_error("duplicate_dependency", f"Task '{tid}' has duplicate dependency '{dep}'")
                seen.add(dep)

    def _check_dependency_cycles(self):
        """Detect cycles using Kahn's algorithm (topological sort)."""
        # Build adjacency: task -> list of tasks it depends on
        # Edges: dep -> dependent (dep must come before dependent)
        in_degree = {t["task_id"]: 0 for t in self.tasks}
        dependents = {t["task_id"]: [] for t in self.tasks}  # dep -> list of tasks that depend on it

        for task in self.tasks:
            tid = task["task_id"]
            for dep in task.get("depends_on", []):
                # Edge: dep -> tid (dep must run before tid)
                dependents.setdefault(dep, []).append(tid)
                in_degree[tid] += 1

        # Kahn's algorithm
        queue = deque([tid for tid, deg in in_degree.items() if deg == 0])
        topological_order = []

        while queue:
            node = queue.popleft()
            topological_order.append(node)
            for dependent in dependents.get(node, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        remaining = [tid for tid, deg in in_degree.items() if deg > 0]
        if remaining:
            cycle_tasks = ", ".join(f"'{t}'" for t in remaining)
            self._add_error("dependency_cycle", f"Dependency cycle detected among tasks: {cycle_tasks}")

        # Record dependency edges
        for task in self.tasks:
            tid = task["task_id"]
            for dep in task.get("depends_on", []):
                self.dependency_edges.append({"from": dep, "to": tid, "type": "depends_on"})

    def _check_self_blocks(self):
        for task in self.tasks:
            tid = task["task_id"]
            blocks = task.get("blocks", [])
            for blocked in blocks:
                if blocked == tid:
                    self._add_error("self_block", f"Task '{tid}' blocks itself")

    def _check_unknown_block_targets(self):
        for task in self.tasks:
            tid = task["task_id"]
            blocks = task.get("blocks", [])
            for blocked in blocks:
                if blocked not in self.task_ids:
                    self._add_error("unknown_block_target", f"Task '{tid}' blocks unknown task '{blocked}'")

    def _check_duplicate_block_refs(self):
        for task in self.tasks:
            tid = task["task_id"]
            blocks = task.get("blocks", [])
            seen = set()
            for blocked in blocks:
                if blocked in seen:
                    self._add_error("duplicate_block_target", f"Task '{tid}' has duplicate block target '{blocked}'")
                seen.add(blocked)

        # Record block edges
        for task in self.tasks:
            tid = task["task_id"]
            for blocked in task.get("blocks", []):
                self.block_edges.append({"from": tid, "to": blocked, "type": "blocks"})

    def _collect_promotion_groups(self):
        for task in self.tasks:
            pg = task.get("promotion_group")
            if pg:
                if pg not in self.promotion_groups:
                    self.promotion_groups[pg] = []
                self.promotion_groups[pg].append(task["task_id"])

    def _collect_pr_groups(self):
        for task in self.tasks:
            pg = task.get("pr_group")
            if pg:
                if pg not in self.pr_groups:
                    self.pr_groups[pg] = []
                self.pr_groups[pg].append(task["task_id"])

    def _compute_integration_order(self):
        """Compute topological order using Kahn's algorithm, then apply integration_order for ties."""
        # Build adjacency
        dependents = {t["task_id"]: [] for t in self.tasks}
        in_degree = {t["task_id"]: 0 for t in self.tasks}

        for task in self.tasks:
            tid = task["task_id"]
            for dep in task.get("depends_on", []):
                dependents.setdefault(dep, []).append(tid)
                in_degree[tid] += 1

        queue = deque([tid for tid, deg in in_degree.items() if deg == 0])
        topological_order = []

        while queue:
            node = queue.popleft()
            topological_order.append(node)
            for dependent in dependents.get(node, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        # Sort: topological order is primary key (dependencies always respected),
        # integration_order breaks ties, manifest order breaks further ties
        topo_index = {tid: i for i, tid in enumerate(topological_order)}
        task_order_map = {t["task_id"]: i for i, t in enumerate(self.tasks)}
        task_integration_order = {}
        for t in self.tasks:
            task_integration_order[t["task_id"]] = t.get("integration_order", task_order_map[t["task_id"]])

        def sort_key(tid):
            return (topo_index.get(tid, 9999), task_integration_order.get(tid, 0), task_order_map.get(tid, 0))

        self.ordered_task_ids = sorted(topological_order, key=sort_key)

    def _compute_downstream_blocked(self):
        """Build map: task -> list of tasks it blocks downstream."""
        for task in self.tasks:
            tid = task["task_id"]
            for blocked in task.get("blocks", []):
                if blocked not in self.downstream_blocked_map:
                    self.downstream_blocked_map[blocked] = []
                self.downstream_blocked_map[blocked].append(tid)

    def _compute_parallel_groups(self):
        """Identify tasks that can run in parallel (no dependencies, no blocking relationships)."""
        task_ids = [t["task_id"] for t in self.tasks]
        # Tasks with no depends_on and no blocks relationships
        parallel_candidates = []
        for task in self.tasks:
            tid = task["task_id"]
            has_dep = bool(task.get("depends_on"))
            is_blocked_by = any(t.get("blocks", []) and tid in t.get("blocks", []) for t in self.tasks)
            blocks_others = bool(task.get("blocks"))
            if not has_dep and not is_blocked_by and not blocks_others:
                parallel_candidates.append(tid)

        if parallel_candidates:
            self.parallel_groups.append(sorted(parallel_candidates))

    def get_downstream_blocked(self, task_id: str) -> list[str]:
        return self.downstream_blocked_map.get(task_id, [])


def build_integration_plan(validator: DependencyGraphValidator) -> dict[str, Any]:
    """Build the integration_plan dict from a validated DependencyGraphValidator."""
    # Determine integration branch from tasks (prefer the promotion_target if consistent)
    promotion_targets = set()
    for task in validator.tasks:
        pt = task.get("promotion_target")
        if pt:
            promotion_targets.add(pt)

    integration_branch = list(promotion_targets)[0] if len(promotion_targets) == 1 else None

    return {
        "plan_version": 1,
        "integration_branch": integration_branch or "integration/aed-run-unknown",
        "ordered_task_ids": validator.ordered_task_ids,
        "promotion_groups": validator.promotion_groups,
        "pr_groups": validator.pr_groups,
        "parallel_groups": validator.parallel_groups,
        "dependency_edges": validator.dependency_edges,
        "block_edges": validator.block_edges,
    }


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

    # v2: Validate dependency graph
    dep_validator = DependencyGraphValidator(tasks)
    dep_errors = dep_validator.validate()
    if dep_errors:
        raise ValueError(f"DEPENDENCY VALIDATION FAILED: {'; '.join(dep_errors)}")

    # Build task entries
    task_entries = []
    for task in tasks:
        tid = task["task_id"]
        entry = {
            "task_id": tid,
            "objective": task["objective"],
            "task_type": task["task_type"],
            "risk_level": task["risk_level"],
            "allowed_files": task.get("allowed_files", []),
            "forbidden_files": task.get("forbidden_files", []),
            "expected_outputs": task.get("expected_outputs", []),
            "bundle_path": make_bundle_path(bundle_root, tid),
            "status": "planned",
            "promotion_recommendation": "not_evaluated",
            "process_score_status": "not_evaluated",
            # v2: dependency metadata
            "depends_on": task.get("depends_on", []),
            "blocks": task.get("blocks", []),
            "promotion_group": task.get("promotion_group") or None,
            "pr_group": task.get("pr_group") or None,
            "can_run_in_parallel": task.get("can_run_in_parallel", False),
            "integration_order": task.get("integration_order"),
            "promotion_target": task.get("promotion_target") or None,
            "dependency_status": "not_evaluated",
            "blocked_by": dep_validator.get_downstream_blocked(tid),
            "downstream_blocked": [],
        }
        # Copy optional fields if present
        for opt_field in ("base_sha", "priority", "notes", "reviewer_hint"):
            if opt_field in task:
                entry[opt_field] = task[opt_field]

        task_entries.append(entry)

    # Build integration plan
    integration_plan = build_integration_plan(dep_validator)

    index = {
        "index_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": repo,
        "base_sha": base_sha or "",
        "bundle_root": bundle_root,
        "dry_run": True,
        "task_count": len(task_entries),
        "tasks": task_entries,
        "integration_plan": integration_plan,
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
    print("Integration plan: ", end="")
    ip = index.get("integration_plan", {})
    print(f"{len(ip.get('ordered_task_ids', []))} ordered tasks, "
          f"{len(ip.get('promotion_groups', {}))} promotion groups, "
          f"{len(ip.get('pr_groups', {}))} pr groups")
    print("Future phases may run the quarantine bundle generator per task.")

    return 0


if __name__ == "__main__":
    sys.exit(main())