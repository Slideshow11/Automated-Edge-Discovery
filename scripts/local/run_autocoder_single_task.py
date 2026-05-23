#!/usr/bin/env python3
"""
run_autocoder_single_task.py

Single-task autocoder controller — v0.

Chains existing safe AED tools to execute one strict task packet through the
verified six-stage pipeline, then stops at PR_PREVIEW_READY.

No live Claude. No push. No PR creation. No merge. No commit. No staging.
No dispatch. No board mutation. No Hermes mutation. No audit append.
No memory/profile writes. No package install. No shell=True.

Usage:
    python3 scripts/local/run_autocoder_single_task.py \
        --task-packet-json <task_packet.json> \
        --output-json <final_status.json> \
        --output-md <final_status.md>

Exit codes:
    0 — evaluation complete (any state written to output JSON)
    1 — fatal error (missing args, invalid packet, etc.)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.resolve()

VALID_PACKET_KIND = "aed.autocoder.single_task.v0"
VALID_EXECUTION_MODES = frozenset(["mocked"])
FORBIDDEN_EXECUTION_MODES = frozenset(["claude", "live", "real"])

# Subprocess timeout for each stage (seconds)
STAGE_TIMEOUT = 600

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    """Compute SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_rev_parse(repo: Path, ref: str = "HEAD") -> str:
    """Return the SHA for a given git ref."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ref],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


# ---------------------------------------------------------------------------
# State definitions
# ---------------------------------------------------------------------------

class State:
    READY = "SINGLE_TASK_READY_FOR_HUMAN_REVIEW"
    HOLD_TASK_PACKET_INVALID = "HOLD_TASK_PACKET_INVALID"
    HOLD_EXECUTION_MODE_NOT_ALLOWED = "HOLD_EXECUTION_MODE_NOT_ALLOWED"
    HOLD_EXECUTION_NOT_PATCH_READY = "HOLD_EXECUTION_NOT_PATCH_READY"
    HOLD_APPLY_NOT_READY = "HOLD_APPLY_NOT_READY"
    HOLD_APPLY_PREVIEW_NOT_READY = "HOLD_APPLY_PREVIEW_NOT_READY"
    HOLD_APPLY_TO_BRANCH_FAILED = "HOLD_APPLY_TO_BRANCH_FAILED"
    HOLD_APPLIED_BRANCH_NOT_READY = "HOLD_APPLIED_BRANCH_NOT_READY"
    HOLD_PR_PREVIEW_NOT_READY = "HOLD_PR_PREVIEW_NOT_READY"
    HOLD_OUTPUT_PATH_INSIDE_REPO = "HOLD_OUTPUT_PATH_INSIDE_REPO"
    HOLD_BRANCH_ALREADY_EXISTS = "HOLD_BRANCH_ALREADY_EXISTS"
    HOLD_UNKNOWN = "HOLD_UNKNOWN"


# ---------------------------------------------------------------------------
# Helper: load JSON
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


def _is_path_forbidden(path: str, forbidden: list[str]) -> bool:
    """
    Return True if path is forbidden by the forbidden list.

    Rules:
    - Normalizes both path and forbidden entries to use "/" as separator.
    - Rejects absolute paths regardless of forbidden list.
    - Rejects paths containing ".." segment regardless of forbidden list.
    - Exact match: path == forbidden_entry
    - Prefix match: if forbidden_entry ends with "/", blocks any path starting with that prefix.
      Without trailing slash, also blocks any path that starts with entry + "/".
    """
    # Normalize
    path = path.strip().replace("\\", "/")
    # Reject absolute paths unconditionally
    if path.startswith("/"):
        return True
    # Reject traversal unconditionally
    parts = path.split("/")
    if ".." in parts:
        return True
    # If no forbidden entries, path is not forbidden (but absolute/traversal already rejected above)
    if not forbidden:
        return False
    for entry in forbidden:
        entry_norm = entry.strip().replace("\\", "/").rstrip("/")
        if not entry_norm:
            continue
        if entry.endswith("/"):
            # Prefix: block any path that starts with entry
            if path == entry_norm or path.startswith(entry_norm + "/"):
                return True
        else:
            # Exact or prefix within directory
            if path == entry_norm or path.startswith(entry_norm + "/"):
                return True
    return False


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_task_packet(packet: dict) -> tuple[bool, str]:
    """
    Validate task packet against all rules.
    Returns (is_valid, error_message).
    """
    # packet_kind
    packet_kind = packet.get("packet_kind")
    if packet_kind != VALID_PACKET_KIND:
        return False, (
            f"packet_kind must be '{VALID_PACKET_KIND}', "
            f"got '{packet_kind}'"
        )

    # task_id
    task_id = packet.get("task_id", "")
    if not task_id:
        return False, "task_id is required"
    if not re.match(r"^[a-zA-Z0-9_-]+$", task_id):
        return False, (
            f"task_id must be alphanumeric + '-' + '_', got '{task_id}'"
        )

    # goal
    goal = packet.get("goal", "")
    if not goal:
        return False, "goal is required"
    if len(goal) < 10 or len(goal) > 1000:
        return False, (
            f"goal must be 10–1000 chars, got {len(goal)}"
        )

    # execution_mode
    execution_mode = packet.get("execution_mode", "")
    if not execution_mode:
        return False, "execution_mode is required"
    if execution_mode in FORBIDDEN_EXECUTION_MODES:
        return False, (
            f"execution_mode '{execution_mode}' is not allowed in v0. "
            "Use 'mocked'."
        )
    if execution_mode not in VALID_EXECUTION_MODES:
        return False, (
            f"execution_mode must be one of {sorted(VALID_EXECUTION_MODES)}, "
            f"got '{execution_mode}'"
        )

    # branch_name must not exist locally
    branch_name = packet.get("branch_name", "")
    if not branch_name:
        return False, "branch_name is required"
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branch_name}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode == 0:
        return False, f"branch '{branch_name}' already exists locally"

    # output_root must be outside repo
    output_root_str = packet.get("output_root", "")
    if not output_root_str:
        return False, "output_root is required"
    try:
        output_root = Path(output_root_str).resolve()
    except Exception:
        return False, f"output_root is not a valid path: {output_root_str}"

    try:
        repo_root_resolved = REPO_ROOT.resolve()
    except Exception:
        repo_root_resolved = REPO_ROOT.absolute()

    # Check if output_root is inside repo
    try:
        output_root.relative_to(repo_root_resolved)
        return False, (
            f"output_root must be outside the repo. "
            f"Got: {output_root_str} (inside {REPO_ROOT})"
        )
    except ValueError:
        pass  # correctly outside repo

    # allowed_files: null or list of non-empty strings
    allowed = packet.get("allowed_files")
    if allowed is not None:
        if not isinstance(allowed, list):
            return False, "allowed_files must be a list or null"
        for f in allowed:
            if not isinstance(f, str) or not f:
                return False, "allowed_files entries must be non-empty strings"
            if f.strip() != f or re.search(r'\s', f):
                return False, f"allowed_files entry has whitespace: '{f}'"

    # forbidden_files: null or list of non-empty strings
    forbidden = packet.get("forbidden_files")
    if forbidden is not None:
        if not isinstance(forbidden, list):
            return False, "forbidden_files must be a list or null"
        for f in forbidden:
            if not isinstance(f, str) or not f:
                return False, "forbidden_files entries must be non-empty strings"
            if f.strip() != f or re.search(r'\s', f):
                return False, f"forbidden_files entry has whitespace: '{f}'"

    # max_changed_files: null or positive int
    mcf = packet.get("max_changed_files")
    if mcf is not None:
        if not isinstance(mcf, int) or mcf < 1:
            return False, "max_changed_files must be a positive integer or null"

    # required_tests: null or list of non-empty strings
    rt = packet.get("required_tests")
    if rt is not None:
        if not isinstance(rt, list):
            return False, "required_tests must be a list or null"
        for t in rt:
            if not isinstance(t, str) or not t:
                return False, "required_tests entries must be non-empty strings"

    # suggested_pr_title
    title = packet.get("suggested_pr_title", "")
    if not title:
        return False, "suggested_pr_title is required"

    # suggested_pr_body
    body = packet.get("suggested_pr_body", "")
    if not body:
        return False, "suggested_pr_body is required"

    # mock_edits: null or list of dicts with "path" and "content"
    mock_edits = packet.get("mock_edits")
    if mock_edits is not None:
        if not isinstance(mock_edits, list):
            return False, "mock_edits must be a list or null"
        if not mock_edits:
            return False, "mock_edits must be non-empty when present"
        for i, edit in enumerate(mock_edits):
            if not isinstance(edit, dict):
                return False, f"mock_edits[{i}] must be a dict"
            path = edit.get("path")
            content = edit.get("content")
            if not isinstance(path, str) or not path:
                return False, f"mock_edits[{i}].path must be a non-empty string"
            if not isinstance(content, str):
                return False, f"mock_edits[{i}].content must be a string"
            # Each mock edit path must be in allowed_files
            if allowed is not None and path not in allowed:
                return False, f"mock_edits[{i}].path '{path}' is not in allowed_files"
            # Each mock edit path must not be forbidden (exact, prefix, or dir)
            # Always call _is_path_forbidden so absolute/traversal checks run even
            # when forbidden_files is null. Empty list is safe — absolute/traversal
            # checks run before the early return.
            if _is_path_forbidden(path, forbidden or []):
                return False, f"mock_edits[{i}].path '{path}' is forbidden"
        # Number of mock edits must not exceed max_changed_files
        mcf = packet.get("max_changed_files")
        if mcf is not None and len(mock_edits) > mcf:
            return False, f"mock_edits count ({len(mock_edits)}) exceeds max_changed_files ({mcf})"

    return True, ""


# ---------------------------------------------------------------------------
# Stage 1: Build execution packet
# ---------------------------------------------------------------------------

def build_execution_packet(task_packet: dict, plan_sha: str, approved_plan_file: Path) -> dict:
    """
    Convert task packet into an execution packet for run_temp_worktree_execution.py.

    Args:
        task_packet: high-level autocoder task packet (aed.autocoder.single_task.v0)
        plan_sha: SHA-256 of the approved plan file (pre-written to output_root)
        approved_plan_file: Path to the written approved plan file

    The execution packet schema (aed.temp_worktree.execution.v0) requires:
        packet_kind, run_id, task_id, base_sha, approved_plan_path,
        approved_plan_sha256, approval, task, execution,
    plus execution metadata fields.
    """
    allowed = task_packet.get("allowed_files")
    forbidden = task_packet.get("forbidden_files")
    task_id = task_packet["task_id"]

    # Resolve base_sha: use explicit value from task packet, else current HEAD
    base_sha = task_packet.get("base_sha") or _git_rev_parse(REPO_ROOT, "HEAD")

    # Normalize execution_mode "mocked" -> "mock" for the nested execution dict
    exec_mode_raw = task_packet.get("execution_mode", "mocked")
    exec_mode = "mock" if exec_mode_raw == "mocked" else exec_mode_raw

    exec_packet = {
        "packet_kind": "aed.temp_worktree.execution.v0",
        # run_id and task_id
        "run_id": task_id,
        "task_id": task_id,
        # base_sha for worktree creation
        "base_sha": base_sha,
        # Execution mode (nested, as required by run_temp_worktree_execution.py)
        "execution": {
            "mode": exec_mode,
            "output_root": task_packet["output_root"],
            "timeout_seconds": 300,
            # Pass through mock_edits for smoke testing with synthetic diffs
            "mock_edits": task_packet.get("mock_edits", []),
        },
        # Goal
        "goal": task_packet.get("goal", ""),
        # File constraints
        "allowed_files": allowed,
        "forbidden_files": forbidden,
        "max_changed_files": task_packet.get("max_changed_files"),
        # Branch metadata
        "branch_name": task_packet.get("branch_name", ""),
        "suggested_pr_title": task_packet.get("suggested_pr_title", ""),
        "suggested_pr_body": task_packet.get("suggested_pr_body", ""),
        # Approval (required by validate_packet and validate_approval)
        "approval": {
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "approved_by": "human",
            "approved_plan_sha256": plan_sha,
            "approved_for_temp_worktree_execution": True,
        },
        # Approved plan file info
        "approved_plan_path": str(approved_plan_file),
        "approved_plan_sha256": plan_sha,
        # Task section (required by validate_packet and post-execution diff validation)
        "task": {
            "description": task_packet.get("goal", ""),
            "allowed_files": allowed if allowed else [],
            "forbidden_files": forbidden if forbidden else [],
            "do_not": task_packet.get("do_not", []),
        },
        # Metadata
        "execution_packet_created_at": datetime.now(timezone.utc).isoformat(),
        "source_packet_kind": task_packet.get("packet_kind"),
    }
    return exec_packet


# ---------------------------------------------------------------------------
# Stage runner
# ---------------------------------------------------------------------------

def run_stage(
    argv: list,
    cwd: Path,
    timeout: int = STAGE_TIMEOUT,
) -> tuple[int, str, str]:
    """
    Run a subprocess and return (returncode, stdout, stderr).
    """
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except Exception as e:
        return -1, "", str(e)


def load_stage_json(path: Path) -> Optional[dict]:
    """Load stage output JSON, return None on error."""
    return _load_json(path)


# ---------------------------------------------------------------------------
# Main controller
# ---------------------------------------------------------------------------

def run_autocoder_single_task(
    task_packet_path: Path,
    output_json_path: Path,
    output_md_path: Path,
) -> dict:
    """
    Run the single-task autocoder controller.

    Returns the final status dict (also written to output_json_path).
    """
    # Load task packet
    task_packet = _load_json(task_packet_path)
    if task_packet is None:
        status = State.HOLD_TASK_PACKET_INVALID
        result = {
            "status": status,
            "error": f"Failed to load task packet JSON: {task_packet_path}",
            "task_packet_path": str(task_packet_path),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(output_json_path, result)
        return result

    # Validate
    valid, err = validate_task_packet(task_packet)
    if not valid:
        status = State.HOLD_TASK_PACKET_INVALID
        result = {
            "status": status,
            "error": err,
            "task_packet": task_packet,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(output_json_path, result)
        return result

    # Extract fields
    output_root_str = task_packet["output_root"]
    output_root = Path(output_root_str).resolve()
    task_id = task_packet["task_id"]
    branch_name = task_packet["branch_name"]

    # Output sub-paths
    execution_packet_path = output_root / "execution_packet.json"
    result_json_path = output_root / "result.json"
    result_md_path = output_root / "result.md"
    diff_patch_path = output_root / "diff.patch"
    apply_readiness_json_path = output_root / "apply_readiness.json"
    apply_readiness_md_path = output_root / "apply_readiness.md"
    apply_preview_json_path = output_root / "apply_preview.json"
    apply_preview_md_path = output_root / "apply_preview.md"
    apply_to_branch_json_path = output_root / "apply_to_branch.json"
    apply_to_branch_md_path = output_root / "apply_to_branch.md"
    applied_branch_verification_json_path = output_root / "applied_branch_verification.json"
    applied_branch_verification_md_path = output_root / "applied_branch_verification.md"
    pr_preview_json_path = output_root / "pr_preview.json"
    pr_preview_md_path = output_root / "pr_preview.md"
    final_review_packet_json_path = output_root / "final_review_packet.json"
    final_review_packet_md_path = output_root / "final_review_packet.md"

    # Make output root
    output_root.mkdir(parents=True, exist_ok=True)

    # Write approved_plan.md before building the execution packet.
    # SHA must match approved_plan_sha256 that goes into the packet.
    approved_plan_file = output_root / "approved_plan.md"
    goal_text = task_packet.get("goal", "")
    plan_content = f"# AED Single-Task Smoke Plan\n\ntask_id: {task_id}\ngoal: {goal_text}\n"
    approved_plan_file.write_text(plan_content, encoding="utf-8")
    plan_sha = _sha256_file(approved_plan_file)

    # Stage 1: Build execution packet
    exec_packet = build_execution_packet(task_packet, plan_sha, approved_plan_file)
    _write_json(execution_packet_path, exec_packet)
    _write_json(task_packet_path.parent / f"task_packet_{task_id}.json", task_packet)

    # Extract resolved base_sha for use in subsequent stages
    base_sha = exec_packet["base_sha"]

    # -------------------------------------------------------------------------
    # Stage 2: Temp worktree execution
    # -------------------------------------------------------------------------
    stage2_argv = [
        "python3",
        str(SCRIPT_DIR / "run_temp_worktree_execution.py"),
        "--packet-json", str(execution_packet_path),
        "--output-json", str(result_json_path),
        "--output-md", str(result_md_path),
    ]
    rc2, stdout2, stderr2 = run_stage(stage2_argv, REPO_ROOT)
    del stage2_argv

    # Load result
    stage2_data = load_stage_json(result_json_path)
    stage2_status = stage2_data.get("status") if stage2_data else None

    if stage2_status != "PATCH_READY_FOR_HUMAN_REVIEW":
        result = {
            "status": State.HOLD_EXECUTION_NOT_PATCH_READY,
            "stage": "stage_2_temp_worktree_execution",
            "expected": "PATCH_READY_FOR_HUMAN_REVIEW",
            "actual": stage2_status,
            "returncode": rc2,
            "stderr": stderr2,
            "execution_packet_path": str(execution_packet_path),
            "result_json_path": str(result_json_path),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(output_json_path, result)
        return result

    # -------------------------------------------------------------------------
    # Stage 3: Apply readiness verification
    # -------------------------------------------------------------------------
    # Determine diff.patch path from stage 2 result
    diff_patch_path = Path(stage2_data.get("diff_patch", str(output_root / "diff.patch")))

    stage3_argv = [
        "python3",
        str(SCRIPT_DIR / "verify_temp_worktree_apply_readiness.py"),
        "--result-json", str(result_json_path),
        "--diff-patch", str(diff_patch_path),
        "--repo-root", str(REPO_ROOT),
        "--output-json", str(apply_readiness_json_path),
        "--output-md", str(apply_readiness_md_path),
        "--require-pmg-clean",
    ]
    rc3, stdout3, stderr3 = run_stage(stage3_argv, REPO_ROOT)
    del stage3_argv

    stage3_data = load_stage_json(apply_readiness_json_path)
    stage3_status = stage3_data.get("status") if stage3_data else None

    if stage3_status != "APPLY_READY":
        result = {
            "status": State.HOLD_APPLY_NOT_READY,
            "stage": "stage_3_apply_readiness_verification",
            "expected": "APPLY_READY",
            "actual": stage3_status,
            "returncode": rc3,
            "stderr": stderr3,
            "apply_readiness_json_path": str(apply_readiness_json_path),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(output_json_path, result)
        return result

    # -------------------------------------------------------------------------
    # Stage 4: Apply preview
    # -------------------------------------------------------------------------
    stage4_argv = [
        "python3",
        str(SCRIPT_DIR / "preview_temp_worktree_apply.py"),
        "--result-json", str(result_json_path),
        "--diff-patch", str(diff_patch_path),
        "--apply-readiness-json", str(apply_readiness_json_path),
        "--repo-root", str(REPO_ROOT),
        "--expected-head", str(base_sha),
        "--output-json", str(apply_preview_json_path),
        "--output-md", str(apply_preview_md_path),
    ]
    rc4, stdout4, stderr4 = run_stage(stage4_argv, REPO_ROOT)
    del stage4_argv

    stage4_data = load_stage_json(apply_preview_json_path)
    stage4_status = stage4_data.get("status") if stage4_data else None

    if stage4_status != "APPLY_PREVIEW_READY":
        result = {
            "status": State.HOLD_APPLY_PREVIEW_NOT_READY,
            "stage": "stage_4_apply_preview",
            "expected": "APPLY_PREVIEW_READY",
            "actual": stage4_status,
            "returncode": rc4,
            "stderr": stderr4,
            "apply_preview_json_path": str(apply_preview_json_path),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(output_json_path, result)
        return result

    # -------------------------------------------------------------------------
    # Stage 5: Apply to local branch
    # -------------------------------------------------------------------------
    # Resolve base_sha from stage 3 data only if not already set
    # (base_sha was resolved earlier in build_execution_packet from task_packet.base_sha or HEAD)
    if base_sha is None and stage3_data:
        base_sha = stage3_data.get("base_sha") or stage3_data.get("checks", {}).get("base_sha")
    if base_sha is None:
        base_sha_result = subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        base_sha = base_sha_result.stdout.strip() if base_sha_result.returncode == 0 else None

    stage5_argv = [
        "python3",
        str(SCRIPT_DIR / "apply_temp_worktree_patch_to_branch.py"),
        "--target-repo", str(REPO_ROOT),
        "--result-json", str(result_json_path),
        "--diff-patch", str(diff_patch_path),
        "--apply-readiness-json", str(apply_readiness_json_path),
        "--expected-base-sha", str(base_sha),
        "--branch-name", branch_name,
        "--output-json", str(apply_to_branch_json_path),
        "--output-md", str(apply_to_branch_md_path),
        "--allow-real-apply",
    ]
    rc5, stdout5, stderr5 = run_stage(stage5_argv, REPO_ROOT)
    del stage5_argv

    stage5_data = load_stage_json(apply_to_branch_json_path)
    stage5_status = stage5_data.get("status") if stage5_data else None

    if stage5_status != "APPLY_TO_BRANCH_APPLIED":
        result = {
            "status": State.HOLD_APPLY_TO_BRANCH_FAILED,
            "stage": "stage_5_apply_to_branch",
            "expected": "APPLY_TO_BRANCH_APPLIED",
            "actual": stage5_status,
            "returncode": rc5,
            "stderr": stderr5,
            "apply_to_branch_json_path": str(apply_to_branch_json_path),
            "cleanup_command": (
                f"git branch -D {branch_name}  # delete local branch on failure"
            ),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(output_json_path, result)
        return result

    # -------------------------------------------------------------------------
    # Stage 6: Applied branch verification
    # -------------------------------------------------------------------------
    stage6_argv = [
        "python3",
        str(SCRIPT_DIR / "verify_temp_worktree_applied_branch.py"),
        "--repo-root", str(REPO_ROOT),
        "--branch-name", branch_name,
        "--expected-base-sha", str(base_sha),
        "--result-json", str(result_json_path),
        "--diff-patch", str(diff_patch_path),
        "--apply-readiness-json", str(apply_readiness_json_path),
        "--output-json", str(applied_branch_verification_json_path),
        "--output-md", str(applied_branch_verification_md_path),
    ]
    rc6, stdout6, stderr6 = run_stage(stage6_argv, REPO_ROOT)
    del stage6_argv

    stage6_data = load_stage_json(applied_branch_verification_json_path)
    stage6_status = stage6_data.get("status") if stage6_data else None

    if stage6_status != "APPLIED_BRANCH_READY":
        result = {
            "status": State.HOLD_APPLIED_BRANCH_NOT_READY,
            "stage": "stage_6_applied_branch_verification",
            "expected": "APPLIED_BRANCH_READY",
            "actual": stage6_status,
            "returncode": rc6,
            "stderr": stderr6,
            "applied_branch_verification_json_path": str(applied_branch_verification_json_path),
            "cleanup_command": (
                f"git branch -D {branch_name}  # delete local branch on failure"
            ),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(output_json_path, result)
        return result

    # -------------------------------------------------------------------------
    # Stage 7: PR preview
    # -------------------------------------------------------------------------
    stage7_argv = [
        "python3",
        str(SCRIPT_DIR / "preview_applied_branch_pr.py"),
        "--repo-root", str(REPO_ROOT),
        "--applied-branch-json", str(applied_branch_verification_json_path),
        "--branch-name", branch_name,
        "--base-branch", "main",
        "--expected-base-sha", str(base_sha),
        "--output-json", str(pr_preview_json_path),
        "--output-md", str(pr_preview_md_path),
        "--suggested-pr-title", task_packet["suggested_pr_title"],
        "--suggested-pr-body", task_packet["suggested_pr_body"],
    ]
    rc7, stdout7, stderr7 = run_stage(stage7_argv, REPO_ROOT)
    del stage7_argv

    stage7_data = load_stage_json(pr_preview_json_path)
    stage7_status = stage7_data.get("status") if stage7_data else None

    if stage7_status != "PR_PREVIEW_READY":
        result = {
            "status": State.HOLD_PR_PREVIEW_NOT_READY,
            "stage": "stage_7_pr_preview",
            "expected": "PR_PREVIEW_READY",
            "actual": stage7_status,
            "returncode": rc7,
            "stderr": stderr7,
            "pr_preview_json_path": str(pr_preview_json_path),
            "cleanup_command": (
                f"git branch -D {branch_name}  # delete local branch on failure"
            ),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(output_json_path, result)
        return result

    # -------------------------------------------------------------------------
    # Stage 8: Final review packet
    # -------------------------------------------------------------------------
    final_review = {
        "packet_kind": "aed.autocoder.single_task.review.v0",
        "task_id": task_id,
        "goal": task_packet["goal"],
        "branch_name": branch_name,
        "base_sha": base_sha,
        "execution_mode": task_packet.get("execution_mode"),
        "stages_completed": [
            "stage_1_execution_packet_built",
            "stage_2_temp_worktree_execution",
            "stage_3_apply_readiness_verification",
            "stage_4_apply_preview",
            "stage_5_apply_to_branch",
            "stage_6_applied_branch_verification",
            "stage_7_pr_preview",
        ],
        "artifacts": {
            "task_packet": str(task_packet_path),
            "execution_packet": str(execution_packet_path),
            "result_json": str(result_json_path),
            "diff_patch": str(diff_patch_path),
            "apply_readiness_json": str(apply_readiness_json_path),
            "apply_preview_json": str(apply_preview_json_path),
            "apply_to_branch_json": str(apply_to_branch_json_path),
            "applied_branch_verification_json": str(applied_branch_verification_json_path),
            "pr_preview_json": str(pr_preview_json_path),
        },
        "final_status": State.READY,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(final_review_packet_json_path, final_review)

    # Write markdown final review
    md_lines = [
        f"# Single-Task Autocoder — Final Review Packet",
        f"",
        f"**Status:** {State.READY}",
        f"",
        f"**Task ID:** {task_id}",
        f"**Goal:** {task_packet['goal']}",
        f"**Branch:** {branch_name}",
        f"**Base SHA:** {base_sha}",
        f"**Execution Mode:** {task_packet.get('execution_mode')}",
        f"",
        f"## Stages Completed",
        "",
    ]
    for s in final_review["stages_completed"]:
        md_lines.append(f"- {s}")
    md_lines.extend(["", "## Artifacts", ""])
    for k, v in final_review["artifacts"].items():
        md_lines.append(f"- **{k}:** `{v}`")
    md_lines.extend(["", "---", f"*Generated: {final_review['generated_at']}*"])
    final_review_packet_md_path.write_text("\n".join(md_lines))

    # Final success result
    result = {
        "status": State.READY,
        "task_id": task_id,
        "branch_name": branch_name,
        "base_sha": base_sha,
        "execution_mode": task_packet.get("execution_mode"),
        "stages_completed": final_review["stages_completed"],
        "artifacts": final_review["artifacts"],
        "final_review_packet_json": str(final_review_packet_json_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(output_json_path, result)

    # Write markdown summary
    md_summary_lines = [
        f"# Single-Task Autocoder — Final Status",
        f"",
        f"**Status:** {State.READY}",
        f"",
        f"**Task ID:** {task_id}",
        f"**Branch:** {branch_name}",
        f"**Base SHA:** {base_sha}",
        f"",
        f"All 7 pipeline stages completed successfully.",
        f"Human review is required before any follow-on action.",
        f"",
        f"## Next Steps",
        f"",
        f"1. Review `{result_json_path}` and `{diff_patch_path}`",
        f"2. Review `{pr_preview_json_path}`",
        f"3. If approved: open PR, get Codex reviews, run final gate, merge",
        f"",
        f"## Safety Boundaries",
        f"",
        f"- No push, PR, merge, commit, stage, dispatch, or Hermes mutation",
        f"- All artifacts preserved at `/tmp/aed_runs/autocoder_single_task_{task_id}/`",
        f"",
        f"---",
        f"*Generated: {result['generated_at']}*",
    ]
    output_md_path.parent.mkdir(parents=True, exist_ok=True)
    output_md_path.write_text("\n".join(md_summary_lines))

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Single-task autocoder controller — chains AED tools to execute one task packet.",
    )
    parser.add_argument(
        "--task-packet-json",
        required=True,
        help="Path to task packet JSON",
    )
    parser.add_argument(
        "--output-json",
        required=True,
        help="Path to write final status JSON",
    )
    parser.add_argument(
        "--output-md",
        required=True,
        help="Path to write final status Markdown",
    )

    args = parser.parse_args()

    task_packet_path = Path(args.task_packet_json).resolve()
    output_json_path = Path(args.output_json).resolve()
    output_md_path = Path(args.output_md).resolve()

    # Fatal: task packet not found
    if not task_packet_path.exists():
        err = {"status": "HOLD_TASK_PACKET_INVALID", "error": f"File not found: {task_packet_path}"}
        _write_json(output_json_path, err)
        print(f"FATAL: task packet not found: {task_packet_path}", file=sys.stderr)
        return 1

    try:
        result = run_autocoder_single_task(
            task_packet_path=task_packet_path,
            output_json_path=output_json_path,
            output_md_path=output_md_path,
        )
        print(f"Status: {result['status']}")
        print(f"Output JSON: {output_json_path}")
        print(f"Output MD: {output_md_path}")
        return 0
    except Exception as e:
        err = {"status": State.HOLD_UNKNOWN, "error": str(e)}
        _write_json(output_json_path, err)
        print(f"FATAL: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())