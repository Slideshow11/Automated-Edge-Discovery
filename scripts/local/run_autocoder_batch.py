#!/usr/bin/env python3
"""
run_autocoder_batch.py

Batch autocoder controller — v0.

Processes a list of strict single-task packets sequentially by invoking
run_autocoder_single_task.py for each task. Aggregates results, writes
batch-level artifacts, and stops at BATCH_READY_FOR_HUMAN_REVIEW.

No live Claude. No push. No PR creation. No merge. No commit. No staging.
No dispatch. No board mutation. No Hermes mutation. No audit append.
No memory/profile writes. No package install. No shell=True.

Usage:
    python3 scripts/local/run_autocoder_batch.py \
        --batch-packet-json <batch_packet.json> \
        --output-json <batch_status.json> \
        --output-md <batch_status.md>

Exit codes:
    0 — batch completed (READY or HOLD), artifacts written
    1 — fatal error (batch packet unparseable, no tasks, etc.)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess as _subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Save real subprocess.run reference before any test can monkeypatch it.
# This allows internal git checks to bypass test fake_run patches.
_real_subprocess_run = _subprocess.run

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.resolve()

VALID_BATCH_PACKET_KIND = "aed.autocoder.batch.v0"
VALID_TASK_PACKET_KIND = "aed.autocoder.single_task.v0"
SINGLE_TASK_SCRIPT = SCRIPT_DIR / "run_autocoder_single_task.py"

# Hard cap on number of tasks in v0
MAX_TASKS_HARD_CAP = 10

# ---------------------------------------------------------------------------
# State definitions
# ---------------------------------------------------------------------------

class State:
    READY = "BATCH_READY_FOR_HUMAN_REVIEW"
    HOLD_BATCH_PACKET_INVALID = "HOLD_BATCH_PACKET_INVALID"
    HOLD_TASK_PACKET_INVALID = "HOLD_TASK_PACKET_INVALID"
    HOLD_TASK_FAILED = "HOLD_TASK_FAILED"
    HOLD_TASK_BRANCH_COLLISION = "HOLD_TASK_BRANCH_COLLISION"
    HOLD_DUPLICATE_TASK_ID = "HOLD_DUPLICATE_TASK_ID"
    HOLD_DUPLICATE_OUTPUT_ROOT = "HOLD_DUPLICATE_OUTPUT_ROOT"
    HOLD_OUTPUT_INSIDE_REPO = "HOLD_OUTPUT_INSIDE_REPO"
    HOLD_BATCH_SIZE_EXCEEDED = "HOLD_BATCH_SIZE_EXCEEDED"
    HOLD_UNSUPPORTED_EXECUTION_MODE = "HOLD_UNSUPPORTED_EXECUTION_MODE"
    HOLD_SINGLE_TASK_SUBPROCESS_FAILED = "HOLD_SINGLE_TASK_SUBPROCESS_FAILED"
    HOLD_SINGLE_TASK_STATUS_MISSING = "HOLD_SINGLE_TASK_STATUS_MISSING"
    HOLD_SINGLE_TASK_STATUS_INVALID = "HOLD_SINGLE_TASK_STATUS_INVALID"
    HOLD_LIVE_EXECUTOR_REQUESTED = "HOLD_LIVE_EXECUTOR_REQUESTED"
    HOLD_UNKNOWN = "HOLD_UNKNOWN"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> Optional[dict]:
    """Load JSON file, return None on error."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path: Path, data: dict) -> None:
    """Write JSON file atomically via temp + rename."""
    tmp = path.with_suffix(".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    tmp.rename(path)


def _git_sha_for_placeholder(sha: str) -> str:
    """Return real SHA if placeholder, else return unchanged."""
    if sha == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb":
        # Resolve real current SHA using real subprocess (not any monkeypatch)
        proc = _real_subprocess_run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    return sha


def _git_sha_exists(sha: str) -> bool:
    """Return True if the given SHA exists in the repo."""
    if not sha or len(sha) != 40:
        return False
    proc = _real_subprocess_run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{sha}:"],
        capture_output=True, text=True, timeout=10,
    )
    return proc.returncode == 0


def _git_branch_exists(branch_name: str) -> bool:
    """Return True if the given branch already exists locally."""
    proc = _real_subprocess_run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--verify", f"refs/heads/{branch_name}"],
        capture_output=True, text=True, timeout=10,
    )
    return proc.returncode == 0


def _git_current_branch() -> str:
    """
    Return the current branch name.  Returns '' if the worktree is in a
    detached HEAD state or if the command fails.
    """
    proc = _real_subprocess_run(
        ["git", "-C", str(REPO_ROOT), "branch", "--show-current"],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode == 0:
        return proc.stdout.strip()
    return ""


def _git_rev_parse_head() -> str:
    """Return the current HEAD SHA as a 40-char hex string."""
    proc = _real_subprocess_run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _git_worktree_add(worktree_path: Path, base_sha: str) -> tuple[bool, str]:
    """
    Create a new git worktree at worktree_path checked out at base_sha.

    Returns (success, diagnostic_message).
    The new worktree is NOT a bare path inside the repo — it is outside by design.
    """
    wt_resolved = worktree_path.resolve()
    # Validate the worktree destination is truly outside the main repo.
    # If it is inside REPO_ROOT, reject it.
    try:
        wt_resolved.relative_to(REPO_ROOT.resolve())
        return False, f"worktree path {wt_resolved} is inside the repo — not allowed"
    except ValueError:
        pass  # Outside the repo — OK

    proc = _real_subprocess_run(
        [
            "git", "-C", str(REPO_ROOT),
            "worktree", "add",
            "--detach",
            str(wt_resolved),
            base_sha,
        ],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        return False, proc.stderr.strip()
    return True, ""


def _git_worktree_remove(worktree_path: Path) -> tuple[bool, str]:
    """
    Remove a git worktree at worktree_path (used only for failed/incomplete setup;
    successful task worktrees are kept for human review).

    Returns (success, diagnostic_message).
    """
    proc = _real_subprocess_run(
        ["git", "-C", str(REPO_ROOT), "worktree", "remove", str(worktree_path), "--force"],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        return False, proc.stderr.strip()
    return True, ""


def _git_status_clean() -> bool:
    """
    Return True only when the main repo worktree has no staged or unstaged
    tracked changes.  Untracked files (?? lines) are ignored since they are
    not part of the committed state.
    """
    proc = _real_subprocess_run(
        ["git", "-C", str(REPO_ROOT), "status", "--short"],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        return False
    # Filter to only lines with a status character in column 2 (tracked changes).
    # Untracked files start with '??' in columns 1-2.
    for line in proc.stdout.splitlines():
        if len(line) >= 2 and line[0] in "MADRC" and line[1] == " ":
            # Staged/unstaged tracked change found
            return False
    return True


def _derive_batch_starting_point() -> tuple[str, str]:
    """
    Capture the batch controller's starting point:
      (batch_start_branch, batch_start_head)

    batch_start_branch may be empty string if in detached HEAD state.
    batch_start_head is a 40-char SHA.

    Returns ("", sha) when detached.
    """
    branch = _git_current_branch()
    head = _git_rev_parse_head()
    return branch, head


def _is_path_inside_repo(path_str: str) -> bool:
    """Return True if path is inside REPO_ROOT."""
    # Reject path traversal before resolution
    parts = path_str.split("/")
    if ".." in parts:
        return True  # Traversal is always unsafe
    try:
        p = Path(path_str).resolve()
        r = REPO_ROOT.resolve()
        p.relative_to(r)
        return True
    except ValueError:
        return False


def _normalize_task_packet(task_packet: dict, batch_base_sha: str, batch_output_root: Path, task_id: str) -> dict:
    """
    Normalize a task packet for the batch context:
    - Fill in base_sha if missing
    - Set output_root to batch_tasks_dir/task_id if not set or invalid
    - Derive suggested_pr_title and suggested_pr_body from goal if missing
    Returns the normalized copy.
    """
    normalized = dict(task_packet)
    if not normalized.get("base_sha"):
        normalized["base_sha"] = batch_base_sha
    normalized["base_sha"] = batch_base_sha
    task_output = batch_output_root / "tasks" / task_id
    normalized["output_root"] = str(task_output)
    # Derive missing PR fields from goal so single-task controller validation passes
    goal = normalized.get("goal", "")
    if not normalized.get("suggested_pr_title"):
        normalized["suggested_pr_title"] = goal[:80] if goal else f"task: {task_id}"
    if not normalized.get("suggested_pr_body"):
        normalized["suggested_pr_body"] = goal or f"Task {task_id} from batch controller"
    return normalized


# ---------------------------------------------------------------------------
# Batch packet validation
# ---------------------------------------------------------------------------

def validate_batch_packet(packet: dict) -> tuple[bool, str]:
    """
    Validate a batch packet.
    Returns (is_valid, error_message_or_empty).
    """
    # packet_kind
    packet_kind = packet.get("packet_kind", "")
    if packet_kind != VALID_BATCH_PACKET_KIND:
        return False, (
            f"packet_kind must be '{VALID_BATCH_PACKET_KIND}', got '{packet_kind}'"
        )

    # batch_id
    batch_id = packet.get("batch_id", "")
    if not batch_id:
        return False, "batch_id is required"
    if not re.match(r"^[a-zA-Z0-9_-]+$", batch_id):
        return False, (
            f"batch_id must be alphanumeric + '-' + '_', got '{batch_id}'"
        )
    if len(batch_id) > 64:
        return False, f"batch_id must be at most 64 chars, got {len(batch_id)}"

    # base_sha
    base_sha = packet.get("base_sha", "")
    if not base_sha:
        return False, "base_sha is required"
    if not re.match(r"^[0-9a-f]{40}$", base_sha):
        return False, f"base_sha must be a 40-char hex SHA, got '{base_sha}'"
    # Resolve placeholder to real SHA if needed
    base_sha_resolved = _git_sha_for_placeholder(base_sha)
    if not _git_sha_exists(base_sha_resolved):
        return False, f"base_sha does not exist in repo: {base_sha_resolved}"

    # output_root
    output_root_str = packet.get("output_root", "")
    if not output_root_str:
        return False, "output_root is required"
    if _is_path_inside_repo(output_root_str):
        return False, (
            f"output_root must be outside the repo. "
            f"Got: {output_root_str} (inside {REPO_ROOT})"
        )

    # max_tasks
    max_tasks = packet.get("max_tasks")
    if max_tasks is not None:
        if not isinstance(max_tasks, int) or max_tasks < 1:
            return False, "max_tasks must be a positive integer or null"

    # tasks
    tasks = packet.get("tasks")
    if tasks is None:
        return False, "tasks is required and must be non-empty"
    if not isinstance(tasks, list):
        return False, "tasks must be a list"
    if not tasks:
        return False, "tasks must be non-empty"

    return True, ""


# ---------------------------------------------------------------------------
# Task constraint validation (between-task checks)
# ---------------------------------------------------------------------------

def validate_task_constraints(tasks: list[dict]) -> tuple[bool, str]:
    """
    Validate inter-task constraints that require the full task list.
    Checks: duplicate task_id, duplicate branch_name, duplicate output_root,
    duplicate allowed_files, output_root inside repo, execution_mode,
    and that each task is a valid aed.autocoder.single_task.v0 packet.

    Returns (is_valid, error_message_or_empty).
    """
    seen_task_ids: set[str] = set()
    seen_branch_names: set[str] = set()
    seen_output_roots: set[str] = set()
    seen_allowed_files: set[str] = set()

    for i, task in enumerate(tasks):
        task_id = task.get("task_id", "")
        branch_name = task.get("branch_name", "")
        output_root_str = task.get("output_root", "")
        allowed_files = task.get("allowed_files") or []

        # --- packet_kind ---
        if task.get("packet_kind") != VALID_TASK_PACKET_KIND:
            pkt = task.get("packet_kind", "")
            return False, (
                f"tasks[{i}].packet_kind must be '{VALID_TASK_PACKET_KIND}', "
                f"got '{pkt}'"
            )

        # --- task_id uniqueness ---
        if not task_id:
            return False, f"tasks[{i}] is missing task_id"
        if task_id in seen_task_ids:
            return False, f"tasks[{i}] has duplicate task_id: '{task_id}'"
        seen_task_ids.add(task_id)

        # --- branch_name uniqueness ---
        if not branch_name:
            return False, f"tasks[{i}] is missing branch_name"
        if branch_name in seen_branch_names:
            return False, f"tasks[{i}] has duplicate branch_name: '{branch_name}'"
        seen_branch_names.add(branch_name)

        # --- output_root uniqueness ---
        if not output_root_str:
            return False, f"tasks[{i}] is missing output_root"
        # Check if task output_root is inside REPO_ROOT (before normalization).
        # This catches packets that request inside-repo paths.
        if _is_path_inside_repo(output_root_str):
            return False, (
                f"tasks[{i}].output_root is inside repo: {output_root_str}"
            )
        if output_root_str in seen_output_roots:
            return False, (
                f"tasks[{i}] has duplicate output_root: '{output_root_str}'"
            )
        seen_output_roots.add(output_root_str)

        # --- execution_mode: mocked only in v0 ---
        execution_mode = task.get("execution_mode", "")
        if not execution_mode:
            return False, f"tasks[{i}] is missing execution_mode"
        if execution_mode not in frozenset(["mocked"]):
            return False, (
                f"tasks[{i}] has unsupported execution_mode: '{execution_mode}'. "
                f"Only 'mocked' is allowed in v0."
            )

        # --- forbidden execution modes ---
        forbidden_modes = frozenset(["claude", "live", "real"])
        if execution_mode in forbidden_modes:
            return False, (
                f"tasks[{i}] execution_mode '{execution_mode}' is not allowed. "
                f"Use 'mocked'."
            )

        # --- allowed_files uniqueness within task ---
        if allowed_files:
            for f in allowed_files:
                if f in seen_allowed_files:
                    return False, (
                        f"tasks[{i}] has duplicate allowed_file across tasks: '{f}'"
                    )
                seen_allowed_files.add(f)

    return True, ""


# ---------------------------------------------------------------------------
# Main batch controller
# ---------------------------------------------------------------------------

def run_autocoder_batch(
    batch_packet_path: Path,
    output_json_path: Path,
    output_md_path: Path,
) -> dict:
    """
    Run the batch autocoder controller.

    Returns the batch status dict (also written to output_json_path).
    """
    # --- Load batch packet ---
    batch_packet = _load_json(batch_packet_path)
    if batch_packet is None:
        status = State.HOLD_BATCH_PACKET_INVALID
        result = {
            "status": status,
            "error": f"Failed to load batch packet JSON: {batch_packet_path}",
            "batch_packet_path": str(batch_packet_path),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(output_json_path, result)
        return result

    # --- Validate batch packet ---
    valid, err = validate_batch_packet(batch_packet)
    if not valid:
        status = State.HOLD_BATCH_PACKET_INVALID
        result = {
            "status": status,
            "error": err,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(output_json_path, result)
        return result

    tasks: list[dict] = batch_packet.get("tasks", [])
    batch_id: str = batch_packet["batch_id"]
    base_sha: str = batch_packet["base_sha"]
    base_sha = _git_sha_for_placeholder(base_sha)
    output_root_str: str = batch_packet["output_root"]
    output_root = Path(output_root_str).resolve()
    stop_on_first_hold: bool = batch_packet.get("stop_on_first_hold", True)
    max_tasks: Optional[int] = batch_packet.get("max_tasks")

    # --- Batch size cap ---
    effective_max = MAX_TASKS_HARD_CAP if max_tasks is None else min(max_tasks, MAX_TASKS_HARD_CAP)
    if len(tasks) > effective_max:
        status = State.HOLD_BATCH_SIZE_EXCEEDED
        result = {
            "status": status,
            "error": (
                f"Batch has {len(tasks)} tasks but v0 cap is {effective_max}. "
                f"Reduce tasks or increase max_tasks."
            ),
            "task_count": len(tasks),
            "max_tasks_cap": effective_max,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(output_json_path, result)
        return result

    # --- Validate task constraints ---
    valid, err = validate_task_constraints(tasks)
    if not valid:
        status = State.HOLD_TASK_PACKET_INVALID
        result = {
            "status": status,
            "error": err,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(output_json_path, result)
        return result

    # --- Create output root ---
    batch_tasks_dir = output_root / "tasks"
    batch_tasks_dir.mkdir(parents=True, exist_ok=True)

    # --- Write batch packet copy ---
    _write_json(output_root / "batch_packet.json", batch_packet)

    # --- Capture batch starting point ---
    batch_start_branch, batch_start_head = _derive_batch_starting_point()

    # --- Process tasks sequentially ---
    task_results: list[dict] = []
    failed_task_id: Optional[str] = None
    failed_task_status: Optional[str] = None
    errors: list[str] = []
    warnings: list[str] = []
    all_ready = True

    for i, task in enumerate(tasks):
        task_id: str = task["task_id"]
        branch_name: str = task["branch_name"]

        # Per-task isolated worktree
        # Each task runs from its own git worktree so that task A's apply-branch
        # dirty state does not block task B.  Successful task worktrees are kept
        # for human review; only failed-setup worktrees are removed.
        task_worktrees_root = output_root / "task_worktrees"
        task_worktree_path = task_worktrees_root / task_id

        ok, diag = _git_worktree_add(task_worktree_path, base_sha)
        if not ok:
            errors.append(f"tasks[{i}] ({task_id}): worktree creation failed: {diag}")
            tr = {
                "task_id": task_id,
                "status": State.HOLD_TASK_FAILED,
                "branch_name": branch_name,
                "output_root": str(batch_tasks_dir / task_id),
                "task_worktree_path": str(task_worktree_path),
                "error": f"worktree add failed: {diag}",
            }
            task_results.append(tr)
            if stop_on_first_hold:
                failed_task_id = task_id
                failed_task_status = State.HOLD_TASK_FAILED
                # Clean up so the failed worktree does not block future operations
                _git_worktree_remove(task_worktree_path)
                break
            all_ready = False
            warnings.append(f"worktree creation failed for {task_id}, continuing: {diag}")
            continue

        # Normalize task packet
        normalized_task = _normalize_task_packet(task, base_sha, output_root, task_id)

        # Write normalized task packet
        task_output_dir = batch_tasks_dir / task_id
        task_packet_json_path = task_output_dir / "task_packet.json"
        task_output_json_path = task_output_dir / "final_status.json"
        task_output_md_path = task_output_dir / "final_status.md"

        _write_json(task_packet_json_path, normalized_task)

        # Invoke single-task controller via explicit argv subprocess.
        # Use the TRUSTED script from the parent (batch controller's own) checkout,
        # NOT the script inside the task worktree. The worktree may be at an
        # older or untrusted base_sha, so we always run the reviewed controller.
        # Pass --repo-root <task_worktree_path> so the controller's stage tools
        # operate on the correct worktree files under test.
        task_script = SINGLE_TASK_SCRIPT
        argv = [
            "python3",
            str(task_script),
            "--task-packet-json", str(task_packet_json_path),
            "--output-json", str(task_output_json_path),
            "--output-md", str(task_output_md_path),
            "--repo-root", str(task_worktree_path),
        ]

        try:
            proc = _real_subprocess_run(
                argv,
                cwd=str(task_worktree_path),
                capture_output=True,
                text=True,
                timeout=600,
            )
        except _subprocess.TimeoutExpired:
            errors.append(f"tasks[{i}] ({task_id}): single-task subprocess timed out")
            task_results.append({
                "task_id": task_id,
                "status": State.HOLD_SINGLE_TASK_SUBPROCESS_FAILED,
                "branch_name": branch_name,
                "output_root": str(task_output_dir),
                "task_worktree_path": str(task_worktree_path),
                "error": "subprocess timeout",
            })
            if stop_on_first_hold:
                failed_task_id = task_id
                failed_task_status = State.HOLD_SINGLE_TASK_SUBPROCESS_FAILED
                break
            all_ready = False
            continue
        except Exception as e:
            errors.append(f"tasks[{i}] ({task_id}): subprocess exception: {e}")
            task_results.append({
                "task_id": task_id,
                "status": State.HOLD_SINGLE_TASK_SUBPROCESS_FAILED,
                "branch_name": branch_name,
                "output_root": str(task_output_dir),
                "task_worktree_path": str(task_worktree_path),
                "error": str(e),
            })
            if stop_on_first_hold:
                failed_task_id = task_id
                failed_task_status = State.HOLD_SINGLE_TASK_SUBPROCESS_FAILED
                break
            all_ready = False
            continue

        if proc.returncode != 0:
            errors.append(
                f"tasks[{i}] ({task_id}): single-task subprocess returned {proc.returncode}"
            )
            task_results.append({
                "task_id": task_id,
                "status": State.HOLD_SINGLE_TASK_SUBPROCESS_FAILED,
                "branch_name": branch_name,
                "output_root": str(task_output_dir),
                "task_worktree_path": str(task_worktree_path),
                "error": f"returncode {proc.returncode}",
                "stderr": proc.stderr[:500],
            })
            if stop_on_first_hold:
                failed_task_id = task_id
                failed_task_status = State.HOLD_SINGLE_TASK_SUBPROCESS_FAILED
                break
            all_ready = False
            continue

        # --- Read single-task result ---
        task_status_data = _load_json(task_output_json_path)
        if task_status_data is None:
            warnings.append(
                f"tasks[{i}] ({task_id}): final_status.json missing or invalid"
            )
            task_results.append({
                "task_id": task_id,
                "status": State.HOLD_SINGLE_TASK_STATUS_MISSING,
                "branch_name": branch_name,
                "output_root": str(task_output_dir),
                "error": "final_status.json not found",
            })
            if stop_on_first_hold:
                failed_task_id = task_id
                failed_task_status = State.HOLD_SINGLE_TASK_STATUS_MISSING
                break
            all_ready = False
            continue

        task_status: str = task_status_data.get("status", "")

        # Validate it's a known status
        known_statuses = frozenset([
            "SINGLE_TASK_READY_FOR_HUMAN_REVIEW",
            "HOLD_TASK_PACKET_INVALID",
            "HOLD_EXECUTION_MODE_NOT_ALLOWED",
            "HOLD_EXECUTION_NOT_PATCH_READY",
            "HOLD_APPLY_NOT_READY",
            "HOLD_APPLY_PREVIEW_NOT_READY",
            "HOLD_APPLY_TO_BRANCH_FAILED",
            "HOLD_APPLIED_BRANCH_NOT_READY",
            "HOLD_PR_PREVIEW_NOT_READY",
            "HOLD_OUTPUT_PATH_INSIDE_REPO",
            "HOLD_BRANCH_ALREADY_EXISTS",
            "HOLD_UNKNOWN",
        ])
        if task_status not in known_statuses:
            task_results.append({
                "task_id": task_id,
                "status": State.HOLD_SINGLE_TASK_STATUS_INVALID,
                "branch_name": branch_name,
                "output_root": str(task_output_dir),
                "error": f"unknown status: '{task_status}'",
                "task_status_data": task_status_data,
            })
            if stop_on_first_hold:
                failed_task_id = task_id
                failed_task_status = State.HOLD_SINGLE_TASK_STATUS_INVALID
                break
            all_ready = False
            continue

        # Record result
        task_results.append({
            "task_id": task_id,
            "status": task_status,
            "branch_name": branch_name,
            "output_root": str(task_output_dir),
            "task_worktree_path": str(task_worktree_path),
            "final_status_json": str(task_output_json_path),
            "final_status_md": str(task_output_md_path),
        })

        # Check status
        if task_status == "SINGLE_TASK_READY_FOR_HUMAN_REVIEW":
            continue
        elif task_status.startswith("HOLD_"):
            if stop_on_first_hold:
                failed_task_id = task_id
                failed_task_status = task_status
                break
            else:
                all_ready = False
                continue
        else:
            # Unknown non-HOLD status
            all_ready = False
            continue

    # --- Build batch status ---
    if failed_task_id is not None:
        batch_status = State.HOLD_TASK_FAILED
    elif not all_ready:
        # At least one task had a HOLD and stop_on_first_hold=false
        batch_status = State.HOLD_TASK_FAILED
    else:
        batch_status = State.READY

    # --- Compile artifact paths ---
    artifact_paths: list[str] = [
        str(output_root / "batch_packet.json"),
        str(output_root / "batch_status.json"),
        str(output_root / "batch_status.md"),
    ]
    for tr in task_results:
        task_id = tr["task_id"]
        task_dir = batch_tasks_dir / task_id
        artifact_paths.append(str(task_dir / "task_packet.json"))
        fs_json = tr.get("final_status_json")
        if fs_json and Path(fs_json).exists():
            artifact_paths.append(fs_json)
        fs_md = tr.get("final_status_md")
        if fs_md and Path(fs_md).exists():
            artifact_paths.append(fs_md)

    result = {
        "status": batch_status,
        "batch_ready_for_human_review": batch_status == State.READY,
        "batch_id": batch_id,
        "base_sha": base_sha,
        "output_root": output_root_str,
        "task_worktrees_root": str(output_root / "task_worktrees"),
        "batch_start_branch": batch_start_branch,
        "batch_start_head": batch_start_head,
        "task_count": len(tasks),
        "completed_task_count": len(task_results),
        "failed_task_id": failed_task_id,
        "failed_task_status": failed_task_status,
        "stop_on_first_hold": stop_on_first_hold,
        "tasks": task_results,
        "artifact_paths": artifact_paths,
        "errors": errors,
        "warnings": warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "safety_statement": (
            "This batch was executed by creating per-task git worktrees for each "
            "single-task controller run so that task apply-branch dirty state does "
            "not block the next task. No live Claude was used. No push, no PR creation, "
            "no merge, no commit, no staging, no dispatch, no board mutation, "
            "no Hermes mutation, no audit appends, no memory/profile updates, "
            "and no package installation. Successful task worktrees are preserved "
            "for human review and are not auto-deleted."
        ),
    }

    # --- Write batch status JSON ---
    _write_json(output_json_path, result)
    _write_json(output_root / "batch_status.json", result)

    # --- Write batch status Markdown ---
    _write_batch_markdown(output_root / "batch_status.md", result, tasks)
    output_md_path.parent.mkdir(parents=True, exist_ok=True)
    _write_batch_markdown(output_md_path, result, tasks)

    return result


def _write_batch_markdown(path: Path, result: dict, tasks: list[dict]) -> None:
    """Write human-readable batch status markdown."""
    lines = [
        "# Batch Autocoder — Batch Status",
        "",
        f"**Status:** {result['status']}",
        "",
        f"**Batch ID:** {result['batch_id']}",
        f"**Base SHA:** {result['base_sha']}",
        f"**Task Count:** {result['task_count']}",
        f"**Completed:** {result['completed_task_count']}",
        f"**Output Root:** `{result['output_root']}`",
        "",
    ]

    if result.get("failed_task_id"):
        lines.extend([
            f"⚠️ **Failed Task:** `{result['failed_task_id']}`",
            f"**Failed Status:** `{result['failed_task_status']}`",
            "",
        ])

    lines.extend([
        "## Task Results",
        "",
        "| # | Task ID | Branch | Status |",
        "|---|---------|--------|--------|",
    ])
    for i, tr in enumerate(result["tasks"], 1):
        status_icon = "✅" if tr["status"] == "SINGLE_TASK_READY_FOR_HUMAN_REVIEW" else "❌"
        lines.append(
            f"| {i} | `{tr['task_id']}` | `{tr['branch_name']}` | {status_icon} {tr['status']} |"
        )

    lines.extend(["", "## Artifacts", ""])
    for p in result.get("artifact_paths", []):
        lines.append(f"- `{p}`")

    if result.get("errors"):
        lines.extend(["", "## Errors", ""])
        for e in result["errors"]:
            lines.append(f"- {e}")

    if result.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        for w in result["warnings"]:
            lines.append(f"- {w}")

    lines.extend([
        "",
        "## Human Review Instructions",
        "",
        "1. Review each task's `final_status.json` and `final_status.md`",
        "2. For each READY task branch, decide whether to open a PR",
        "3. For each HOLD task, address the issue and re-run the task",
        "4. Delete task branches you do not want to keep",
        "",
        "---",
        result.get("safety_statement", ""),
        "",
        f"*Generated: {result['generated_at']}*",
    ])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch autocoder controller — orchestrates multiple single-task autocoder runs.",
    )
    parser.add_argument(
        "--batch-packet-json",
        required=True,
        help="Path to batch packet JSON",
    )
    parser.add_argument(
        "--output-json",
        required=True,
        help="Path to write batch status JSON",
    )
    parser.add_argument(
        "--output-md",
        required=True,
        help="Path to write batch status Markdown",
    )

    args = parser.parse_args()

    batch_packet_path = Path(args.batch_packet_json).resolve()
    output_json_path = Path(args.output_json).resolve()
    output_md_path = Path(args.output_md).resolve()

    # Fatal: batch packet not found
    if not batch_packet_path.exists():
        err = {
            "status": State.HOLD_BATCH_PACKET_INVALID,
            "error": f"File not found: {batch_packet_path}",
        }
        _write_json(output_json_path, err)
        print(f"FATAL: batch packet not found: {batch_packet_path}", file=sys.stderr)
        return 1

    try:
        result = run_autocoder_batch(
            batch_packet_path=batch_packet_path,
            output_json_path=output_json_path,
            output_md_path=output_md_path,
        )
        print(f"Status: {result['status']}")
        print(f"Output JSON: {output_json_path}")
        print(f"Output MD: {output_md_path}")
        return 0
    except Exception as e:
        err = {
            "status": State.HOLD_UNKNOWN,
            "error": str(e),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(output_json_path, err)
        print(f"FATAL: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())