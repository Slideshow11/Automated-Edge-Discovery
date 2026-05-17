#!/usr/bin/env python3
"""
build_autocoder_run_summary.py

Read-only run-level aggregator for AED quarantine autocoder runs.
Consumes BUNDLE_INDEX.json and per-task bundle artifacts to produce
a machine-readable JSON and human-readable Markdown run summary.

v1: Read-only. No repo mutation. No git writes. No PR creation.
     No audit append. No Hermes calls. No dispatch. No production board mutation.

Usage:
    python3 scripts/local/build_autocoder_run_summary.py \\
        --run-id aed-run-2026-05-17-001 \\
        --bundle-index /path/to/BUNDLE_INDEX.json \\
        --bundle-root /path/to/bundles \\
        --output-json /path/to/RUN_SUMMARY.json \\
        --output-md /path/to/RUN_SUMMARY.md \\
        [--repo /path/to/repo] \\
        [--base-sha <sha>] \\
        [--integration-branch <branch>] \\
        [--expected-tasks-json '["task1","task2"]'] \\
        [--allow-missing-bundles] \\
        [--strict]

Exit codes:
    0  — summary produced successfully
    1  — validation error or missing required argument
    2  — hard safety failure (hermes_touched/dispatch_occurred/production_board_touched)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CURRENT_VERSION = 1

# Task statuses
TASK_STATUS_READY = "TASK_READY"
TASK_STATUS_BLOCKED = "TASK_BLOCKED"
TASK_STATUS_SKIPPED = "TASK_SKIPPED"
TASK_STATUS_FAILED_VALIDATION = "TASK_FAILED_VALIDATION"
TASK_STATUS_NOT_EVALUATED = "TASK_NOT_EVALUATED"

# Promotion statuses
PROMOTION_NOT_PROMOTED = "not_promoted"
PROMOTION_PROMOTED = "promoted_to_integration"
PROMOTION_BLOCKED = "blocked_from_promotion"
PROMOTION_NOT_APPLICABLE = "not_applicable"

# Overall statuses
OVERALL_RUN_READY = "RUN_READY"
OVERALL_PARTIAL_READY = "PARTIAL_READY"
OVERALL_BLOCKED = "BLOCKED"
OVERALL_FAILED_VALIDATION = "FAILED_VALIDATION"
OVERALL_NO_TASKS = "NO_TASKS"
OVERALL_INVALID_INPUT = "INVALID_INPUT"

# Dependency statuses
DEP_STATUS_NO_DEPENDENCIES = "no_dependencies"
DEP_STATUS_SATISFIED = "satisfied"
DEP_STATUS_BLOCKED_BY_DEPENDENCY = "blocked_by_dependency"
DEP_STATUS_FAILED_VALIDATION = "dependency_failed_validation"
DEP_STATUS_NOT_EVALUATED = "dependency_not_evaluated"
DEP_STATUS_INVALID_GRAPH = "invalid_dependency_graph"

# Human actions
HUMAN_NONE = "none"
HUMAN_REVIEW = "review_report"
HUMAN_AUTHORIZE = "authorize_merge"
HUMAN_RESOLVE = "resolve_blocker"
HUMAN_INSPECT_CI = "inspect_ci"
HUMAN_RERUN = "rerun_required"

# Safety booleans that hard-fail
HARD_FAIL_BOOLEANS = frozenset([
    "hermes_touched",
    "dispatch_occurred",
    "production_board_touched",
])

# Safety booleans that are reported but do not hard-fail
REPORT_ONLY_BOOLEANS = frozenset([
    "pr_created",
    "import_performed",
    "patch_applied",
])

# Required bundle artifacts — missing in strict mode upgrades status to TASK_FAILED_VALIDATION
REQUIRED_BUNDLE_ARTIFACTS = frozenset(["BUNDLE_STATUS.json"])

# Contextually required artifacts — required only when task data is expected
CONTEXTUAL_REQUIRED_ARTIFACTS = frozenset([
    "violations_only.json",   # required when scope/violations collection was expected
    "scope_check.json",        # required when scope check was expected
    "local_gate.txt",         # required when local_gate status is passed or failed
])

# Optional bundle artifacts — always warnings, never errors
OPTIONAL_BUNDLE_ARTIFACTS = frozenset([
    "risk_notes.md",
    "proposed_pr_body.md",
    "FINAL_GATE.json",
    "codex_review_summary.md",
])


# -----------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AED autocoder run summary generator — read-only aggregator."
    )
    parser.add_argument("--run-id", required=True, help="Unique run identifier")
    parser.add_argument("--bundle-index", required=True, help="Path to BUNDLE_INDEX.json")
    parser.add_argument("--bundle-root", required=True, help="Root directory of task bundles")
    parser.add_argument("--output-json", required=True, help="Path for JSON output")
    parser.add_argument("--output-md", required=True, help="Path for Markdown output")
    parser.add_argument("--repo", default=None, help="Repo path or name")
    parser.add_argument("--base-sha", default=None, help="Base commit SHA")
    parser.add_argument(
        "--integration-branch", default=None,
        help="Integration branch name (e.g. integration/aed-run-2026-05-17-001)"
    )
    parser.add_argument(
        "--expected-tasks-json", default=None,
        help="JSON array of expected task IDs, e.g. '[\"task1\",\"task2\"]'"
    )
    parser.add_argument(
        "--allow-missing-bundles", action="store_true",
        help="Treat missing bundle directories as warnings instead of errors"
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Treat missing required bundle files as TASK_FAILED_VALIDATION errors"
    )
    parser.add_argument(
        "--profile-sentinel-before", default=None,
        help="Path to JSON snapshot of profile file hashes BEFORE the run"
    )
    parser.add_argument(
        "--profile-sentinel-after", default=None,
        help="Path to JSON snapshot of profile file hashes AFTER the run"
    )
    return parser


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_bundle_path(path: Path, bundle_root: Path) -> Optional[str]:
    """Ensure a bundle sub-path does not escape the bundle root."""
    try:
        resolved = path.resolve()
        root_resolved = bundle_root.resolve()
        # Must be under bundle_root
        try:
            resolved.relative_to(root_resolved)
            return None
        except ValueError:
            return f"Bundle path '{path}' resolves outside bundle root '{bundle_root}'"
    except Exception as e:
        return f"Cannot resolve bundle path '{path}': {e}"


def read_json_file(path: Path) -> tuple[Optional[dict], Optional[str]]:
    """Read a JSON file, return (obj, error). error is None on success."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except json.JSONDecodeError as e:
        return None, f"Malformed JSON in '{path}': {e}"
    except FileNotFoundError:
        return None, f"File not found: '{path}'"
    except Exception as e:
        return None, f"Cannot read '{path}': {e}"


def read_text_file(path: Path) -> tuple[Optional[str], Optional[str]]:
    """Read a text file, return (content, error). error is None on success."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read(), None
    except FileNotFoundError:
        return None, f"File not found: '{path}'"
    except Exception as e:
        return None, f"Cannot read '{path}': {e}"


# ---------------------------------------------------------------------------
# Task bundle reader
# ---------------------------------------------------------------------------

class TaskBundleReader:
    """Reads and validates a single task bundle directory."""

    def __init__(self, bundle_root: Path, task_id: str):
        self.bundle_root = bundle_root
        self.task_id = task_id
        self.bundle_dir = bundle_root / task_id
        self.errors: list[str] = []      # hard errors (malformed JSON, missing required)
        self.warnings: list[str] = []     # soft warnings (missing optional/contextual)
        self._required_errors: list[str] = []  # errors on REQUIRED artifacts only
        self._contextual_errors: list[str] = []  # errors on CONTEXTUAL artifacts

        # Loaded artifact data
        self.bundle_status: Optional[dict] = None
        self.scope_check: Optional[dict] = None
        self.violations_only: Optional[dict] = None
        self.local_gate_txt: Optional[str] = None
        self.risk_notes_md: Optional[str] = None
        self.proposed_pr_body_md: Optional[str] = None
        self.final_gate_json: Optional[dict] = None
        self.codex_review_md: Optional[str] = None

    def load_all(self, strict: bool = False) -> None:
        """Load all bundle artifacts, categorizing errors by severity."""
        self._load_bundle_status(strict)
        self._load_scope_check(strict)
        self._load_violations_only(strict)
        self._load_local_gate_txt(strict)
        self._load_risk_notes_md(strict)
        self._load_proposed_pr_body_md(strict)
        self._load_final_gate_json(strict)
        self._load_codex_review_md(strict)

    def _load_bundle_status(self, strict: bool) -> None:
        path = self.bundle_dir / "BUNDLE_STATUS.json"
        obj, err = read_json_file(path)
        if err:
            msg = f"Task '{self.task_id}': BUNDLE_STATUS.json: {err}"
            self.errors.append(msg)
            self._required_errors.append(msg)
            return
        if not isinstance(obj, dict):
            msg = (
                f"Task '{self.task_id}': BUNDLE_STATUS.json must be a JSON object, "
                f"got: {type(obj).__name__}"
            )
            self.errors.append(msg)
            self._required_errors.append(msg)
            return
        self.bundle_status = obj

    def _load_scope_check(self, strict: bool) -> None:
        path = self.bundle_dir / "scope_check.json"
        obj, err = read_json_file(path)
        if err:
            self.warnings.append(f"Task '{self.task_id}': scope_check.json: {err}")
            return
        self.scope_check = obj

    def _load_violations_only(self, strict: bool) -> None:
        path = self.bundle_dir / "violations_only.json"
        obj, err = read_json_file(path)
        if err:
            # In strict mode, violations_only.json is always required for attempted tasks
            # (AED always runs scope checking). Missing it is a contextual error only —
            # do NOT also append to warnings here to avoid duplicate reporting.
            if strict:
                self._contextual_errors.append(err)
            else:
                self.warnings.append(f"Task '{self.task_id}': violations_only.json: {err}")
            return
        self.violations_only = obj

    def _load_local_gate_txt(self, strict: bool) -> None:
        path = self.bundle_dir / "local_gate.txt"
        content, err = read_text_file(path)
        if err:
            self.warnings.append(f"Task '{self.task_id}': local_gate.txt: {err}")
            # local_gate.txt is contextually required when task was attempted —
            # track separately for strict mode
            self._contextual_errors.append(err)
            return
        self.local_gate_txt = content

    def _load_risk_notes_md(self, strict: bool) -> None:
        path = self.bundle_dir / "risk_notes.md"
        content, err = read_text_file(path)
        if err:
            self.warnings.append(f"Task '{self.task_id}': risk_notes.md: {err}")
            return
        self.risk_notes_md = content

    def _load_proposed_pr_body_md(self, strict: bool) -> None:
        path = self.bundle_dir / "proposed_pr_body.md"
        content, err = read_text_file(path)
        if err:
            self.warnings.append(f"Task '{self.task_id}': proposed_pr_body.md: {err}")
            return
        self.proposed_pr_body_md = content

    def _load_final_gate_json(self, strict: bool) -> None:
        path = self.bundle_dir / "FINAL_GATE.json"
        obj, err = read_json_file(path)
        if err:
            self.warnings.append(f"Task '{self.task_id}': FINAL_GATE.json: {err}")
            return
        self.final_gate_json = obj

    def _load_codex_review_md(self, strict: bool) -> None:
        path = self.bundle_dir / "codex_review_summary.md"
        content, err = read_text_file(path)
        if err:
            self.warnings.append(f"Task '{self.task_id}': codex_review_summary.md: {err}")
            return
        self.codex_review_md = content

    def get_boolean_field(self, key: str) -> Optional[bool]:
        """Get a safety boolean from bundle_status. Returns None if absent."""
        if self.bundle_status is None:
            return None
        val = self.bundle_status.get(key)
        if val is None:
            return None
        if isinstance(val, bool):
            return val
        # Legacy string booleans
        if isinstance(val, str):
            if val.lower() in ("true", "false"):
                return val.lower() == "true"
        return None

    @property
    def has_required_errors(self) -> bool:
        """True if any REQUIRED artifact (BUNDLE_STATUS.json) had errors."""
        return bool(self._required_errors)

    @property
    def has_contextual_errors(self) -> bool:
        """True if any contextually-required artifact had errors."""
        return bool(self._contextual_errors)

    def contextual_errors_for_task(self) -> list[str]:
        """Return contextual errors; empty list if no errors."""
        return list(self._contextual_errors)

    def is_contextually_dirty(self) -> bool:
        """True if scope is dirty or clean_for_task is False — meaning violations_only.json
        and scope_check.json are required for classification."""
        if self.bundle_status is None:
            return False
        clean = self.bundle_status.get("clean_for_task", True)
        if not clean:
            return True
        # Also require scope_check if scope_status is explicitly dirty
        if self.scope_check is not None and not self.scope_check.get("passed", True):
            return True
        return False


# ---------------------------------------------------------------------------
# Bundle index loader
# ---------------------------------------------------------------------------

def load_bundle_index(path: Path) -> tuple[Optional[dict], Optional[str]]:
    """Load and validate BUNDLE_INDEX.json."""
    obj, err = read_json_file(path)
    if err:
        return None, err
    if not isinstance(obj, dict):
        return None, f"BUNDLE_INDEX.json must be a JSON object, got: {type(obj).__name__}"

    # Required top-level fields (support both 'version' and 'index_version' for compatibility)
    if "version" not in obj and "index_version" not in obj:
        return None, f"BUNDLE_INDEX.json missing required field: 'version'"
    if "bundle_root" not in obj:
        return None, f"BUNDLE_INDEX.json missing required field: 'bundle_root'"
    tasks_val = obj.get("tasks")
    if not isinstance(tasks_val, list):
        return None, f"BUNDLE_INDEX.json 'tasks' must be a list, got: {type(tasks_val).__name__}"

    return obj, None


# ---------------------------------------------------------------------------
# Run summary builder
# ---------------------------------------------------------------------------

class RunSummaryBuilder:
    def __init__(
        self,
        run_id: str,
        bundle_index: dict,
        bundle_root: Path,
        repo: Optional[str],
        base_sha: Optional[str],
        integration_branch: Optional[str],
        expected_task_ids: Optional[list[str]],
        allow_missing: bool,
        strict: bool,
    ):
        self.run_id = run_id
        self.bundle_index = bundle_index
        self.bundle_root = Path(bundle_root)
        self.repo = repo
        self.base_sha = base_sha
        self.integration_branch = integration_branch
        self.expected_task_ids = expected_task_ids or []
        self.allow_missing = allow_missing
        self.strict = strict

        self.tasks: list[dict] = []
        self.blockers: list[dict] = []
        self.warnings: list[dict] = []
        self.errors: list[dict] = []  # local validation errors
        self.overall_status = OVERALL_NO_TASKS
        self.human_action = HUMAN_NONE
        self.human_action_required = False

        # Aggregated safety booleans
        self.safety_invariants = {
            "hermes_touched": False,
            "dispatch_occurred": False,
            "production_board_touched": False,
            "memory_or_profile_updated": False,
            "skills_created": False,
        }
        self.safety_warnings: list[dict] = []

        # Gate summary
        self.gate_summary = {
            "local_gate_passed": 0,
            "local_gate_failed": 0,
            "codex_clean": 0,
            "ci_green": 0,
            "finalization_guard_merge_ready": 0,
        }

        self.task_count = 0
        self.tasks_attempted = 0
        self.tasks_ready = 0
        self.tasks_blocked = 0
        self.tasks_skipped = 0
        self.tasks_promoted = 0
        self.tasks_failed_validation = 0
        self.prs_opened = 0
        self.merge_ready_prs = 0

        # v2: explicit promoted/ready/blocked task ID lists
        self.promoted_task_ids: list[str] = []
        self.ready_task_ids: list[str] = []
        self.blocked_task_ids: list[str] = []

        # v2: dependency tracking
        self.tasks_with_dependencies = 0
        self.tasks_downstream_blocked = 0
        self.dependency_edges_count = 0
        self.block_edges_count = 0
        self.promotion_groups_count = 0
        self.pr_groups_count = 0

        # Integration plan output
        self.integration_plan: dict[str, Any] = {}

    def build(self) -> dict:
        """Build the full run summary dict."""
        self._process_tasks()
        self._compute_overall_status()
        self._compute_human_action()

        return {
            "summary_version": CURRENT_VERSION,
            "run_id": self.run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "repo": self.repo,
            "base_sha": self.base_sha,
            "integration_branch": self.integration_branch,
            "bundle_index_path": None,  # filled by caller
            "bundle_root": str(self.bundle_root),
            "task_count": self.task_count,
            "tasks_attempted": self.tasks_attempted,
            "tasks_ready": self.tasks_ready,
            "tasks_blocked": self.tasks_blocked,
            "tasks_skipped": self.tasks_skipped,
            "tasks_promoted": self.tasks_promoted,
            "tasks_failed_validation": self.tasks_failed_validation,
            "prs_opened": self.prs_opened,
            "merge_ready_prs": self.merge_ready_prs,
            "promoted_task_ids": self.promoted_task_ids,
            "ready_task_ids": self.ready_task_ids,
            "blocked_task_ids": self.blocked_task_ids,
            "human_action_required": self.human_action_required,
            "overall_status": self.overall_status,
            "safety_invariants": self.safety_invariants,
            "gate_summary": self.gate_summary,
            "tasks": self.tasks,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "human_action": self.human_action,
            "human_action_required": self.human_action_required,
            "artifact_index": {
                "json_report": None,  # filled by caller
                "markdown_report": None,  # filled by caller
            },
            # v2: dependency and integration plan
            "dependency_summary": {
                "tasks_with_dependencies": self.tasks_with_dependencies,
                "tasks_downstream_blocked": self.tasks_downstream_blocked,
                "dependency_edges": self.dependency_edges_count,
                "block_edges": self.block_edges_count,
                "promotion_groups": self.promotion_groups_count,
                "pr_groups": self.pr_groups_count,
            },
            "integration_plan": self.integration_plan,
        }

    def _process_tasks(self) -> None:
        """Process each task in the bundle index."""
        seen_task_ids: set[str] = set()
        index_tasks = self.bundle_index.get("tasks", [])

        self.task_count = len(index_tasks)

        for task_entry in index_tasks:
            task_id = task_entry.get("task_id", "UNKNOWN")
            task_dir = self.bundle_root / task_id

            # Duplicate task ID check
            if task_id in seen_task_ids:
                self.warnings.append({
                    "task_id": task_id,
                    "code": "duplicate_task_id",
                    "message": f"Task ID '{task_id}' appears more than once in bundle index",
                })
                continue
            seen_task_ids.add(task_id)

            # Expected task check
            if self.expected_task_ids and task_id not in self.expected_task_ids:
                self.warnings.append({
                    "task_id": task_id,
                    "code": "unexpected_task_id",
                    "message": f"Task ID '{task_id}' not in expected tasks list",
                })

            # Check bundle directory exists
            if not task_dir.exists():
                msg = {
                    "task_id": task_id,
                    "code": "bundle_directory_missing",
                    "message": f"Bundle directory '{task_dir}' does not exist",
                }
                if self.allow_missing:
                    self.warnings.append(msg)
                    self._append_task_summary(task_id, task_entry, TASK_STATUS_NOT_EVALUATED, None)
                    continue
                else:
                    self.warnings.append(msg)
                    self._append_task_summary(task_id, task_entry, TASK_STATUS_FAILED_VALIDATION, None)
                    continue

            # Path traversal check
            err = validate_bundle_path(task_dir, self.bundle_root)
            if err:
                self.warnings.append({"task_id": task_id, "code": "path_traversal", "message": err})
                self._append_task_summary(task_id, task_entry, TASK_STATUS_FAILED_VALIDATION, None)
                continue

            # Load bundle artifacts
            reader = TaskBundleReader(self.bundle_root, task_id)
            reader.load_all(strict=self.strict)

            # Collect errors and warnings
            for e in reader.errors:
                self.warnings.append({"task_id": task_id, "code": "bundle_file_error", "message": e})
            for w in reader.warnings:
                self.warnings.append({"task_id": task_id, "code": "bundle_file_warning", "message": w})

            # If strict mode and reader had errors (malformed JSON or missing required files),
            # mark as TASK_FAILED_VALIDATION
            if self.strict and reader.errors and reader.bundle_status is None:
                self._append_task_summary(task_id, task_entry, TASK_STATUS_FAILED_VALIDATION, None)
                self._add_blocker_entry(task_id, task_entry, "bundle_missing_required", "BUNDLE_STATUS.json missing or malformed — required artifact", reader)
                self.tasks_failed_validation += 1
                self.tasks_attempted += 1
                continue

            # In strict mode, contextually-required artifacts missing/malformed also upgrade
            # status to TASK_FAILED_VALIDATION (but BUNDLE_STATUS.json was present)
            if self.strict and reader.has_contextual_errors and reader.bundle_status is not None:
                contextual_msgs = reader.contextual_errors_for_task()
                summary_text = f"Strict mode: contextually-required artifacts missing or malformed: {'; '.join(contextual_msgs)}"
                self._append_task_summary(task_id, task_entry, TASK_STATUS_FAILED_VALIDATION, reader)
                self._add_blocker_entry(task_id, task_entry, "strict_contextual_required", summary_text, reader)
                self.tasks_failed_validation += 1
                self.tasks_attempted += 1
                continue

            # Determine task status
            status, blocker_code, blocker_summary = self._determine_task_status(
                task_id, task_entry, reader
            )

            self._append_task_summary(task_id, task_entry, status, reader, blocker_code, blocker_summary)

            # Populate top-level blockers list for TASK_BLOCKED tasks
            if status == TASK_STATUS_BLOCKED:
                self._add_blocker_entry(task_id, task_entry, blocker_code, blocker_summary, reader)

            # Update counters
            if status == TASK_STATUS_READY:
                self.tasks_ready += 1
                self.tasks_attempted += 1
                self.ready_task_ids.append(task_id)
            elif status == TASK_STATUS_BLOCKED:
                self.tasks_blocked += 1
                self.tasks_attempted += 1
                self.blocked_task_ids.append(task_id)
            elif status == TASK_STATUS_SKIPPED:
                self.tasks_skipped += 1
            elif status == TASK_STATUS_FAILED_VALIDATION:
                self.tasks_failed_validation += 1
                self.tasks_attempted += 1

            # Check safety booleans from bundle
            self._check_safety_invariants(task_id, reader)

        # Check expected tasks are all present
        if self.expected_task_ids:
            found_ids = {t["task_id"] for t in self.tasks}
            for expected in self.expected_task_ids:
                if expected not in found_ids:
                    self.warnings.append({
                        "task_id": expected,
                        "code": "expected_task_missing",
                        "message": f"Expected task '{expected}' not found in bundle index",
                    })

        # v2: Compute dependency summary and integration plan
        self._compute_dependency_summary()
        self._compute_integration_plan()

    def _determine_task_status(
        self, task_id: str, task_entry: dict, reader: TaskBundleReader
    ) -> tuple[str, Optional[str], Optional[str]]:
        """Determine the status of a task based on its bundle artifacts."""
        bundle_status = reader.bundle_status

        if bundle_status is None:
            # No bundle status — task not executed
            return TASK_STATUS_NOT_EVALUATED, None, None

        status_value = bundle_status.get("status", "unknown")

        # Map status strings to our enum
        status_map = {
            "planned": TASK_STATUS_NOT_EVALUATED,
            "executed": TASK_STATUS_NOT_EVALUATED,
            "task_ready": TASK_STATUS_READY,
            "task_blocked": TASK_STATUS_BLOCKED,
            "task_skipped": TASK_STATUS_SKIPPED,
            "task_failed_validation": TASK_STATUS_FAILED_VALIDATION,
        }

        mapped = status_map.get(status_value, status_value)
        if mapped not in (TASK_STATUS_READY, TASK_STATUS_BLOCKED, TASK_STATUS_SKIPPED,
                          TASK_STATUS_FAILED_VALIDATION, TASK_STATUS_NOT_EVALUATED):
            mapped = TASK_STATUS_NOT_EVALUATED

        # Determine blocker if blocked
        blocker_code = None
        blocker_summary = None
        if mapped == TASK_STATUS_BLOCKED:
            bs = bundle_status.get("blocker_summary", "")
            # blocker_breakdown is {"file": {rule, severity}} — file-path keys, not codes.
            # blocker_code always comes from the top-level field.
            blocker_code = bundle_status.get("blocker_code")
            blocker_summary = bs

        return mapped, blocker_code, blocker_summary

    def _append_task_summary(
        self,
        task_id: str,
        task_entry: dict,
        status: str,
        reader: Optional[TaskBundleReader],
        blocker_code: Optional[str] = None,
        blocker_summary: Optional[str] = None,
    ) -> None:
        """Build and append a task summary entry."""
        bundle_status = reader.bundle_status if reader else None

        # Determine promotion status
        promotion = PROMOTION_NOT_PROMOTED
        if status == TASK_STATUS_READY:
            # Source of truth: per-task bundle's promotion_status field
            # (BUNDLE_INDEX.json planned status is fallback only)
            bundle_promotion = bundle_status.get("promotion_status") if bundle_status else None
            if bundle_promotion == "promoted_to_integration":
                promotion = PROMOTION_PROMOTED
                self.tasks_promoted += 1
                self.promoted_task_ids.append(task_id)
            elif bundle_promotion == "blocked_from_promotion":
                promotion = PROMOTION_BLOCKED
            elif bundle_promotion == "not_promoted":
                promotion = PROMOTION_NOT_PROMOTED
            else:
                # Fallback: check for promoted_to_integration boolean on bundle_status
                if bundle_status and bundle_status.get("promoted_to_integration") is True:
                    promotion = PROMOTION_PROMOTED
                    self.tasks_promoted += 1
                    self.promoted_task_ids.append(task_id)
                elif bundle_status and bundle_status.get("blocked_from_promotion") is True:
                    promotion = PROMOTION_BLOCKED
                else:
                    promotion = PROMOTION_NOT_PROMOTED

        # Determine clean_for_task
        clean = False
        if bundle_status:
            clean = bool(bundle_status.get("clean_for_task", False))

        # Scope violations count
        allowed_violations_count = 0
        if reader and reader.violations_only:
            allowed_violations_count = len(reader.violations_only.get("allowed_scope_violations", []))

        # Scope status — priority: scope_check.passed > violations_only.clean_for_task > bundle_status.scope_status
        scope_status = "unknown"
        if reader and reader.scope_check is not None:
            # scope_check has explicit passed field
            if "passed" in reader.scope_check:
                scope_status = "clean" if reader.scope_check.get("passed") else "dirty"
            # scope_check has scope_clean field
            elif "scope_clean" in reader.scope_check:
                scope_status = "clean" if reader.scope_check.get("scope_clean") else "dirty"
            # scope_check has diff_status field (no changes = clean)
            elif "diff_status" in reader.scope_check:
                scope_status = "clean" if reader.scope_check.get("diff_status") == "no_changes" else "dirty"
        if scope_status == "unknown" and bundle_status:
            scope_status = bundle_status.get("scope_status", "unknown")

        # Local gate status — parse multiple marker formats
        local_gate_status = "not_executed"
        if reader and reader.local_gate_txt is not None:
            content = reader.local_gate_txt.strip()
            # Check for preview-only markers
            if any(marker in content.lower() for marker in ["preview only", "preview-only", "preview_only"]):
                local_gate_status = "not_executed"
            # Check for executed_in_phase2: false
            elif "executed_in_phase2: false" in content or "executed_in_phase2=false" in content:
                local_gate_status = "not_executed"
            # Check for not_executed literal
            elif content == "not_executed":
                local_gate_status = "not_executed"
            # Check for LOCAL_GATE_RESULT=pass / LOCAL_GATE_RESULT=fail
            elif "LOCAL_GATE_RESULT=pass" in content.upper():
                local_gate_status = "passed"
            elif "LOCAL_GATE_RESULT=fail" in content.upper():
                local_gate_status = "failed"
            # Check for PASS / FAILED
            elif content == "PASS":
                local_gate_status = "passed"
            elif content == "FAILED":
                local_gate_status = "failed"
            else:
                # Content exists but unknown format — treat as passed if nonempty
                # (operator wrote evidence but used custom format)
                local_gate_status = "passed"

        # Codex status
        codex_status = "not_run"
        if reader and reader.codex_review_md is not None:
            codex_status = "clean"
        elif bundle_status:
            codex_status = bundle_status.get("codex_status", "not_run")

        # CI status
        ci_status = "not_applicable"
        if bundle_status:
            ci_status = bundle_status.get("ci_status", "not_applicable")

        # Finalization status
        finalization_status = "not_applicable"
        if reader and reader.final_gate_json is not None:
            rec = reader.final_gate_json.get("final_recommendation", "unknown")
            finalization_status = rec

        # Changed files count
        changed_files_count = 0
        if bundle_status:
            changed_files = bundle_status.get("changed_files", [])
            if isinstance(changed_files, list):
                changed_files_count = len(changed_files)

        # Expected outputs present
        expected_outputs = task_entry.get("expected_outputs", [])
        expected_outputs_present = False
        if expected_outputs and reader:
            # Check if at least some expected output files exist
            present_count = 0
            for out in expected_outputs:
                out_path = reader.bundle_dir / out
                if out_path.exists():
                    present_count += 1
            expected_outputs_present = present_count > 0

        # Determine human action for this task
        task_human_action = HUMAN_REVIEW
        if status == TASK_STATUS_READY:
            task_human_action = HUMAN_AUTHORIZE
        elif status == TASK_STATUS_BLOCKED:
            task_human_action = HUMAN_RESOLVE
        elif status == TASK_STATUS_SKIPPED:
            task_human_action = HUMAN_NONE

        task_summary = {
            "task_id": task_id,
            "task_type": task_entry.get("task_type", "unknown"),
            "risk_level": task_entry.get("risk_level", "unknown"),
            "status": status,
            "promotion_status": promotion,
            "bundle_path": str(reader.bundle_dir if reader else self.bundle_root / task_id),
            "clean_for_task": clean,
            "allowed_scope_violations_count": allowed_violations_count,
            "scope_status": scope_status,
            "local_gate_status": local_gate_status,
            "codex_status": codex_status,
            "ci_status": ci_status,
            "finalization_status": finalization_status,
            "changed_files_count": changed_files_count,
            "expected_outputs_present": expected_outputs_present,
            "blocker_code": blocker_code,
            "blocker_summary": blocker_summary,
            "human_action": task_human_action,
            # v2: dependency fields
            # NOTE: dependency_status is computed inline before self.tasks append below.
            # deferred_patch placeholder to avoid linter error — value set after append.
            "depends_on": task_entry.get("depends_on", []),
            "blocks": task_entry.get("blocks", []),
            "blocked_by": task_entry.get("blocked_by", []),
            "downstream_blocked": task_entry.get("downstream_blocked", []),
            "dependency_status": "not_evaluated",  # placeholder — patched below after self.tasks append
            "can_run_in_parallel": task_entry.get("can_run_in_parallel", False),
            "integration_order": task_entry.get("integration_order"),
            "promotion_group": task_entry.get("promotion_group"),
            "pr_group": task_entry.get("pr_group"),
            "promotion_target": task_entry.get("promotion_target"),
        }

        self.tasks.append(task_summary)

        # PATCH the just-appended task's dependency_status now that self.tasks is complete
        # so the status map contains all tasks including this one
        summary_idx = len(self.tasks) - 1
        self.tasks[summary_idx]["dependency_status"] = self._compute_task_dependency_status(
            task_id, task_entry, status
        )

        # Update gate summary counters
        if local_gate_status == "passed":
            self.gate_summary["local_gate_passed"] += 1
        elif local_gate_status == "failed":
            self.gate_summary["local_gate_failed"] += 1

        if codex_status == "clean":
            self.gate_summary["codex_clean"] += 1

        if ci_status == "success":
            self.gate_summary["ci_green"] += 1

        if finalization_status == "MERGE_READY":
            self.gate_summary["finalization_guard_merge_ready"] += 1
            self.merge_ready_prs += 1

        if bundle_status and bundle_status.get("pr_created"):
            self.prs_opened += 1

    def _compute_task_dependency_status(
        self, task_id: str, task_entry: dict, status: str
    ) -> str:
        """Compute dependency_status for a single task."""
        deps = task_entry.get("depends_on", [])
        blocks = task_entry.get("blocks", [])

        if not deps:
            return DEP_STATUS_NO_DEPENDENCIES

        # Get all task statuses from resolved bundle statuses (not bundle-index "planned")
        task_status_map = {t["task_id"]: t.get("status", "planned") for t in self.tasks}

        # If task is not TASK_READY, dependency can't be evaluated
        if status != TASK_STATUS_READY:
            return DEP_STATUS_NOT_EVALUATED

        # Check if dependencies are satisfied
        for dep_tid in deps:
            dep_status = task_status_map.get(dep_tid, "planned")
            if dep_status == "TASK_FAILED_VALIDATION":
                return DEP_STATUS_FAILED_VALIDATION
            if dep_status == "TASK_BLOCKED":
                return DEP_STATUS_BLOCKED_BY_DEPENDENCY
            if dep_status not in ("TASK_READY", "TASK_NOT_EVALUATED"):
                return DEP_STATUS_NOT_EVALUATED

        return DEP_STATUS_SATISFIED

    def _check_safety_invariants(self, task_id: str, reader: TaskBundleReader) -> None:
        """Check safety booleans in bundle and aggregate them."""
        for key in HARD_FAIL_BOOLEANS:
            val = reader.get_boolean_field(key)
            if val is True:
                self.safety_invariants[key] = True

        for key in REPORT_ONLY_BOOLEANS:
            val = reader.get_boolean_field(key)
            if val is True:
                if key not in self.safety_invariants:
                    self.safety_invariants[key] = False  # ensure it exists
                # Report it as a warning
                self.safety_warnings.append({
                    "task_id": task_id,
                    "code": key,
                    "message": f"Task '{task_id}' has {key}=true",
                })

    def _add_blocker_entry(
        self,
        task_id: str,
        task_entry: dict,
        blocker_code: Optional[str],
        blocker_summary: Optional[str],
        reader: Optional[TaskBundleReader],
    ) -> None:
        """Add a top-level blocker entry for a TASK_BLOCKED or TASK_FAILED_VALIDATION task."""
        if not blocker_code:
            self.warnings.append({
                "task_id": task_id,
                "code": "blocker_code_missing",
                "message": f"Task '{task_id}' has status TASK_BLOCKED but blocker_code is null",
            })
            blocker_code = "unknown"

        # Build a descriptive blocker summary if none provided
        if not blocker_summary:
            code_descriptions = {
                "allowed_scope_violations": "Allowed scope contains executable safety violations",
                "bundle_missing_required": "Required bundle artifact BUNDLE_STATUS.json is missing or malformed",
                "strict_contextual_required": "Strict mode: contextually-required artifact missing or malformed",
                "unknown": "Unknown blocker — check task bundle",
            }
            blocker_summary = code_descriptions.get(blocker_code, f"Blocker: {blocker_code}")

        bundle_path = str(reader.bundle_dir) if reader else str(self.bundle_root / task_id)

        # Determine source: prefer violations_only for allowed_scope_violations
        source = "BUNDLE_STATUS.json"
        if reader and reader.violations_only is not None:
            source = "violations_only.json"

        self.blockers.append({
            "task_id": task_id,
            "blocker_code": blocker_code,
            "blocker_summary": blocker_summary,
            "human_action": HUMAN_RESOLVE,
            "bundle_path": bundle_path,
            "source": source,
        })

    def _compute_overall_status(self) -> None:
        """Compute the overall run status."""
        if self.task_count == 0:
            self.overall_status = OVERALL_NO_TASKS
            return

        if self.tasks_attempted == 0:
            self.overall_status = OVERALL_NO_TASKS
            return

        # TASK_FAILED_VALIDATION is a hard failure — takes precedence
        if self.tasks_failed_validation > 0:
            self.overall_status = OVERALL_FAILED_VALIDATION
            return

        all_blocked = (self.tasks_blocked > 0 and self.tasks_ready == 0)
        all_ready = (self.tasks_ready > 0 and self.tasks_blocked == 0 and self.tasks_skipped == 0)
        partial = (self.tasks_ready > 0 and self.tasks_blocked > 0)

        if all_blocked:
            self.overall_status = OVERALL_BLOCKED
        elif all_ready:
            self.overall_status = OVERALL_RUN_READY
        elif partial:
            self.overall_status = OVERALL_PARTIAL_READY
        else:
            self.overall_status = OVERALL_PARTIAL_READY

    def _compute_human_action(self) -> None:
        """Compute the required human action."""
        if self.overall_status in (OVERALL_NO_TASKS, OVERALL_INVALID_INPUT):
            self.human_action = HUMAN_REVIEW
            self.human_action_required = True
            return

        if self.tasks_blocked > 0:
            self.human_action = HUMAN_RESOLVE
            self.human_action_required = True
            return

        if self.merge_ready_prs > 0:
            self.human_action = HUMAN_AUTHORIZE
            self.human_action_required = True
            return

        if self.tasks_ready > 0:
            self.human_action = HUMAN_REVIEW
            self.human_action_required = True
            return

        self.human_action = HUMAN_NONE
        self.human_action_required = False

    def _compute_dependency_summary(self) -> None:
        """Compute dependency summary counts from bundle index tasks."""
        index_tasks = self.bundle_index.get("tasks", [])
        dep_edges = 0
        block_edges = 0
        tasks_with_deps = 0
        downstream_blocked_count = 0
        promotion_groups: set[str] = set()
        pr_groups: set[str] = set()

        for t in index_tasks:
            deps = t.get("depends_on", [])
            blocks = t.get("blocks", [])
            if deps:
                tasks_with_deps += 1
                dep_edges += len(deps)
            if blocks:
                block_edges += len(blocks)
            pg = t.get("promotion_group")
            if pg:
                promotion_groups.add(pg)
            prg = t.get("pr_group")
            if prg:
                pr_groups.add(prg)

        # Count tasks that are downstream blocked (appear in someone else's blocks list)
        all_task_ids = {t["task_id"] for t in index_tasks}
        blocked_task_ids: set[str] = set()
        for t in index_tasks:
            for b in t.get("blocks", []):
                if b in all_task_ids:
                    blocked_task_ids.add(b)
        downstream_blocked_count = len(blocked_task_ids)

        self.tasks_with_dependencies = tasks_with_deps
        self.tasks_downstream_blocked = downstream_blocked_count
        self.dependency_edges_count = dep_edges
        self.block_edges_count = block_edges
        self.promotion_groups_count = len(promotion_groups)
        self.pr_groups_count = len(pr_groups)

    def _compute_integration_plan(self) -> None:
        """Build integration_plan from resolved task statuses and bundle-index metadata."""
        # Use resolved task list (self.tasks) for statuses; bundle_index for metadata
        index_tasks = self.tasks
        integration_branch = self.integration_branch

        # Collect promotion and PR groups from bundle index
        promotion_groups: dict[str, list[str]] = {}
        pr_groups: dict[str, list[str]] = {}
        ordered_task_ids: list[str] = []
        dependency_edges: list[dict] = []
        block_edges: list[dict] = []

        for t in index_tasks:
            tid = t["task_id"]
            pg = t.get("promotion_group")
            if pg:
                if pg not in promotion_groups:
                    promotion_groups[pg] = []
                promotion_groups[pg].append(tid)
            prg = t.get("pr_group")
            if prg:
                if prg not in pr_groups:
                    pr_groups[prg] = []
                pr_groups[prg].append(tid)

        # Build ordered task list respecting dependencies and integration_order
        dependents = {t["task_id"]: [] for t in index_tasks}
        in_degree = {t["task_id"]: 0 for t in index_tasks}
        manifest_order = {t["task_id"]: i for i, t in enumerate(index_tasks)}

        for t in index_tasks:
            tid = t["task_id"]
            for dep in t.get("depends_on", []):
                dependents.setdefault(dep, []).append(tid)
                in_degree[tid] += 1

        from collections import deque
        queue = deque([tid for tid, deg in in_degree.items() if deg == 0])
        topological = []
        while queue:
            node = queue.popleft()
            topological.append(node)
            for dependent in dependents.get(node, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        # Sort: topological order as primary key, integration_order breaks ties
        topo_index = {tid: i for i, tid in enumerate(topological)}
        def sort_key(tid):
            t = next((x for x in index_tasks if x["task_id"] == tid), None)
            order = t.get("integration_order", 9999) if t else 9999
            return (topo_index[tid], order, manifest_order.get(tid, 0))

        ordered_task_ids = sorted(topological, key=sort_key)

        for t in index_tasks:
            tid = t["task_id"]
            for dep in t.get("depends_on", []):
                dependency_edges.append({"from": dep, "to": tid, "type": "depends_on"})
            for blocked in t.get("blocks", []):
                block_edges.append({"from": tid, "to": blocked, "type": "blocks"})

        task_status_map = {t["task_id"]: t.get("status", "planned") for t in index_tasks}

        ready_for_promotion = []
        promoted_to_integration = []
        blocked_from_promotion = []

        for t in index_tasks:
            tid = t["task_id"]
            status = task_status_map.get(tid, "planned")
            # Get actual promotion status from resolved task summaries (self.tasks)
            task_summary = next((x for x in self.tasks if x["task_id"] == tid), None)
            promotion = task_summary.get("promotion_status", PROMOTION_NOT_PROMOTED) if task_summary else PROMOTION_NOT_PROMOTED
            deps = t.get("depends_on", [])
            blocks = t.get("blocks", [])

            # If already promoted, record and skip the readiness checks
            if promotion == PROMOTION_PROMOTED:
                promoted_to_integration.append(tid)
                continue

            if status != "TASK_READY":
                blocked_from_promotion.append(tid)
                continue

            is_blocked = False
            for blocked_id in blocks:
                bs = task_status_map.get(blocked_id, "planned")
                if bs in ("TASK_FAILED_VALIDATION", "TASK_BLOCKED"):
                    is_blocked = True
                    break

            if is_blocked:
                blocked_from_promotion.append(tid)
            elif deps:
                all_deps_satisfied = True
                for dep_tid in deps:
                    dep_status = task_status_map.get(dep_tid, "planned")
                    if dep_status not in ("TASK_READY", "TASK_NOT_EVALUATED"):
                        all_deps_satisfied = False
                        break
                if all_deps_satisfied:
                    ready_for_promotion.append(tid)
                else:
                    blocked_from_promotion.append(tid)
            else:
                ready_for_promotion.append(tid)

        parallel_groups: list[list[str]] = []
        parallel_candidates = [
            t["task_id"] for t in index_tasks
            if t.get("can_run_in_parallel") is True
            and task_status_map.get(t["task_id"], "planned") in ("TASK_READY", "TASK_NOT_EVALUATED")
            and not t.get("depends_on")
        ]
        if parallel_candidates:
            parallel_groups.append(sorted(parallel_candidates))

        suggested_pr_groups: dict[str, list[str]] = {}
        for pg, tids in pr_groups.items():
            filtered = [tid for tid in tids if task_status_map.get(tid) in ("TASK_READY", "TASK_NOT_EVALUATED")]
            if filtered:
                suggested_pr_groups[pg] = filtered

        self.integration_plan = {
            "integration_branch": integration_branch,
            "ordered_task_ids": ordered_task_ids,
            "ready_for_promotion": sorted(ready_for_promotion),
            "promoted_to_integration": sorted(promoted_to_integration),
            "blocked_from_promotion": sorted(blocked_from_promotion),
            "suggested_pr_groups": suggested_pr_groups,
            "parallel_groups": parallel_groups,
            "dependency_edges": dependency_edges,
            "block_edges": block_edges,
        }


# ---------------------------------------------------------------------------
# Markdown report builder
# ---------------------------------------------------------------------------

def build_markdown_report(summary: dict, output_md: Path) -> None:
    """Write a human-readable Markdown run summary."""
    lines = []

    # Header
    lines.append("# AED Autocoder Run Summary\n")
    lines.append(f"**Run ID:** `{summary['run_id']}`\n")
    lines.append(f"**Overall Status:** `{summary['overall_status']}`\n")
    lines.append(f"**Generated:** {summary['generated_at']}\n")

    if summary.get("repo"):
        lines.append(f"**Repo:** `{summary['repo']}`\n")
    if summary.get("base_sha"):
        lines.append(f"**Base SHA:** `{summary['base_sha'][:8]}...`\n")
    if summary.get("integration_branch"):
        lines.append(f"**Integration Branch:** `{summary['integration_branch']}`\n")

    lines.append("\n---\n\n")

    # Task counts
    lines.append("## Task Counts\n")
    tc = summary["task_count"]
    ta = summary["tasks_attempted"]
    tr = summary["tasks_ready"]
    tb = summary["tasks_blocked"]
    ts = summary["tasks_skipped"]
    tp = summary["tasks_promoted"]
    lines.append(f"| Metric | Count |\n")
    lines.append(f"|--------|-------|\n")
    lines.append(f"| Tasks in index | {tc} |\n")
    lines.append(f"| Tasks attempted | {ta} |\n")
    lines.append(f"| TASK_READY | {tr} |\n")
    lines.append(f"| TASK_BLOCKED | {tb} |\n")
    lines.append(f"| TASK_SKIPPED | {ts} |\n")
    lines.append(f"| Tasks promoted | {tp} |\n")
    lines.append(f"| Promoted task IDs | {', '.join(f'`{x}`' for x in summary.get('promoted_task_ids', []))} |\n")
    lines.append(f"| PRs opened | {summary['prs_opened']} |\n")
    lines.append(f"| Merge-ready PRs | {summary['merge_ready_prs']} |\n")
    lines.append("\n")

    # Safety invariants
    si = summary.get("safety_invariants", {})
    lines.append("## Safety Invariants\n")
    lines.append(f"| Boolean | Value |\n")
    lines.append(f"|---------|-------|\n")
    for k, v in si.items():
        flag = "✅ false" if not v else "❌ **true**"
        lines.append(f"| `{k}` | {flag} |\n")
    lines.append("\n")

    # Gate summary
    gs = summary.get("gate_summary", {})
    lines.append("## Gate Summary\n")
    lines.append(f"| Gate | Count |\n")
    lines.append(f"|------|-------|\n")
    lines.append(f"| Local gate passed | {gs.get('local_gate_passed', 0)} |\n")
    lines.append(f"| Local gate failed | {gs.get('local_gate_failed', 0)} |\n")
    lines.append(f"| Codex clean | {gs.get('codex_clean', 0)} |\n")
    lines.append(f"| CI green | {gs.get('ci_green', 0)} |\n")
    lines.append(f"| Finalization guard MERGE_READY | {gs.get('finalization_guard_merge_ready', 0)} |\n")
    lines.append("\n")

    # v2: Dependency Summary
    dep_sum = summary.get("dependency_summary", {})
    if dep_sum:
        lines.append("## Dependency Summary\n")
        lines.append(f"| Metric | Count |\n")
        lines.append(f"|--------|-------|\n")
        lines.append(f"| Tasks with dependencies | {dep_sum.get('tasks_with_dependencies', 0)} |\n")
        lines.append(f"| Tasks downstream blocked | {dep_sum.get('tasks_downstream_blocked', 0)} |\n")
        lines.append(f"| Dependency edges | {dep_sum.get('dependency_edges', 0)} |\n")
        lines.append(f"| Block edges | {dep_sum.get('block_edges', 0)} |\n")
        lines.append(f"| Promotion groups | {dep_sum.get('promotion_groups', 0)} |\n")
        lines.append(f"| PR groups | {dep_sum.get('pr_groups', 0)} |\n")
        lines.append("\n")

    # v2: Integration Plan
    ip = summary.get("integration_plan", {})
    if ip:
        lines.append("## Integration Plan\n")
        ib = ip.get("integration_branch") or "not specified"
        lines.append(f"**Integration branch:** `{ib}`\n")
        promoted = ip.get("promoted_to_integration", [])
        ready = ip.get("ready_for_promotion", [])
        blocked = ip.get("blocked_from_promotion", [])
        if promoted:
            lines.append(f"**Promoted to integration:** {', '.join(f'`{x}`' for x in promoted)}\n")
        if ready:
            lines.append(f"**Ready for promotion:** {', '.join(f'`{x}`' for x in ready)}\n")
        if blocked:
            lines.append(f"**Blocked from promotion:** {', '.join(f'`{x}`' for x in blocked)}\n")
        lines.append("\n")

        # Promotion groups
        pg = ip.get("promotion_groups", {})
        if pg:
            lines.append("### Promotion Groups\n")
            for group_name, task_ids in pg.items():
                lines.append(f"**{group_name}:** {', '.join(f'`{x}`' for x in task_ids)}\n")
            lines.append("\n")

        # Suggested PR groups
        sprg = ip.get("suggested_pr_groups", {})
        if sprg:
            lines.append("### Suggested PR Groups\n")
            for group_name, task_ids in sprg.items():
                lines.append(f"**{group_name}:** {', '.join(f'`{x}`' for x in task_ids)}\n")
            lines.append("\n")

        # Parallel groups
        par = ip.get("parallel_groups", [])
        if par:
            lines.append("### Parallel Groups\n")
            for i, group in enumerate(par):
                lines.append(f"Group {i+1}: {', '.join(f'`{x}`' for x in group)}\n")
            lines.append("\n")

    # Task table
    lines.append("## Task Table\n")
    lines.append("| Task ID | Type | Risk | Status | Promotion | Scope | Local Gate | Codex | CI | Blocker |\n")
    lines.append("|---------|------|------|--------|-----------|-------|------------|-------|-----|--------|\n")
    for t in summary.get("tasks", []):
        tid = f"`{t['task_id']}`"
        ttype = t.get("task_type", "—")
        risk = t.get("risk_level", "—")
        status = f"`{t['status']}`"
        prom = t.get("promotion_status", "—")
        scope = t.get("scope_status", "—")
        lg = t.get("local_gate_status", "—")
        cx = t.get("codex_status", "—")
        ci = t.get("ci_status", "—")
        bc = t.get("blocker_code") or "—"
        lines.append(f"| {tid} | {ttype} | {risk} | {status} | {prom} | {scope} | {lg} | {cx} | {ci} | {bc} |\n")
    lines.append("\n")

    # Blockers
    blockers = summary.get("blockers", [])
    if blockers:
        lines.append("## Blockers\n")
        for b in blockers:
            lines.append(f"- [{b.get('task_id','?')}]: {b.get('summary','no summary')}\n")
        lines.append("\n")
    elif summary.get("tasks_blocked", 0) > 0:
        lines.append("## Blockers\n")
        blocked_tasks = [t for t in summary.get("tasks", []) if t["status"] == TASK_STATUS_BLOCKED]
        for t in blocked_tasks:
            bc = t.get("blocker_code") or "unknown"
            bs = t.get("blocker_summary") or ""
            lines.append(f"- [{t['task_id']}] (`{bc}`): {bs}\n")
        lines.append("\n")

    # Warnings
    warnings = summary.get("warnings", [])
    if warnings:
        lines.append("## Warnings\n")
        for w in warnings:
            lines.append(f"- [{w.get('task_id','?')}]: {w.get('message','no message')}\n")
        lines.append("\n")

    # Recommended next action
    ha = summary.get("human_action", "none")
    har = summary.get("human_action_required", False)
    lines.append("## Recommended Next Action\n")
    lines.append(f"**Action:** `{ha}`\n")
    lines.append(f"**Human intervention required:** {'yes' if har else 'no'}\n")

    # Compute a useful next-action message
    if summary["overall_status"] == OVERALL_RUN_READY:
        lines.append("\nAll attempted tasks are ready. Authorize merge for merge-ready PRs.\n")
    elif summary["overall_status"] == OVERALL_PARTIAL_READY:
        ready_tasks = [t["task_id"] for t in summary.get("tasks", []) if t["status"] == TASK_STATUS_READY]
        blocked_tasks = [t["task_id"] for t in summary.get("tasks", []) if t["status"] == TASK_STATUS_BLOCKED]
        if ready_tasks:
            lines.append(f"\nReady tasks: {', '.join(f'`{x}`' for x in ready_tasks)}. Review and authorize.\n")
        if blocked_tasks:
            lines.append(f"\nBlocked tasks: {', '.join(f'`{x}`' for x in blocked_tasks)}. Resolve blockers first.\n")
    elif summary["overall_status"] == OVERALL_BLOCKED:
        blocked_tasks = [t["task_id"] for t in summary.get("tasks", []) if t["status"] == TASK_STATUS_BLOCKED]
        lines.append(f"\nAll attempted tasks are blocked. {', '.join(f'`{x}`' for x in blocked_tasks)}. Resolve blockers before proceeding.\n")

    lines.append("\n---\n\n")

    # Artifact paths
    ai = summary.get("artifact_index", {})
    lines.append("## Artifact Index\n")
    lines.append(f"- **JSON report:** `{summary.get('output_json', 'N/A')}`\n")
    lines.append(f"- **Markdown report:** `{summary.get('output_md', 'N/A')}`\n")

    with open(output_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = build_argparser()
    args = parser.parse_args()

    # Resolve bundle index path
    bundle_index_path = Path(args.bundle_index).resolve()
    if not bundle_index_path.exists():
        print(f"ERROR: Bundle index not found: {bundle_index_path}", file=sys.stderr)
        return 1

    # Load bundle index
    bundle_index, err = load_bundle_index(bundle_index_path)
    if err:
        print(f"ERROR: Failed to load bundle index: {err}", file=sys.stderr)
        return 1

    # Resolve bundle root
    bundle_root = Path(args.bundle_root).resolve()
    if not bundle_root.exists():
        print(f"ERROR: Bundle root directory not found: {bundle_root}", file=sys.stderr)
        return 1

    # Parse expected tasks
    expected_task_ids: Optional[list[str]] = None
    if args.expected_tasks_json:
        try:
            expected_task_ids = json.loads(args.expected_tasks_json)
            if not isinstance(expected_task_ids, list):
                raise ValueError("expected-tasks-json must be a JSON array")
        except json.JSONDecodeError as e:
            print(f"ERROR: --expected-tasks-json is not valid JSON: {e}", file=sys.stderr)
            return 1

    # Build summary
    builder = RunSummaryBuilder(
        run_id=args.run_id,
        bundle_index=bundle_index,
        bundle_root=bundle_root,
        repo=args.repo,
        base_sha=args.base_sha,
        integration_branch=args.integration_branch,
        expected_task_ids=expected_task_ids,
        allow_missing=args.allow_missing_bundles,
        strict=args.strict,
    )

    # Add a fake errors attribute for the case where we need to track local errors
    builder.errors: list = []

    summary = builder.build()

    # Check hard-fail safety invariants BEFORE writing output
    hard_fail_keys = [
        k for k, v in summary["safety_invariants"].items()
        if k in HARD_FAIL_BOOLEANS and v is True
    ]
    if hard_fail_keys:
        print(
            f"HARD SAFETY FAILURE: The following safety booleans are true: "
            f"{', '.join(hard_fail_keys)}. Aborting summary generation.",
            file=sys.stderr
        )
        return 2

    # Write JSON output
    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    summary["artifact_index"]["json_report"] = str(output_json)
    summary["artifact_index"]["markdown_report"] = str(Path(args.output_md))
    summary["bundle_index_path"] = str(bundle_index_path)
    summary["output_json"] = str(output_json)
    summary["output_md"] = str(Path(args.output_md))

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    # Write Markdown report
    build_markdown_report(summary, Path(args.output_md))

    print(
        f"Run summary written:\n"
        f"  JSON: {output_json}\n"
        f"  MD:   {Path(args.output_md)}"
    )
    print(f"Overall status: {summary['overall_status']}")
    print(f"Human action required: {summary['human_action_required']}")
    print(f"Tasks: {summary['task_count']} total, {summary['tasks_ready']} ready, "
          f"{summary['tasks_blocked']} blocked, {summary['tasks_skipped']} skipped")

    return 0


if __name__ == "__main__":
    sys.exit(main())