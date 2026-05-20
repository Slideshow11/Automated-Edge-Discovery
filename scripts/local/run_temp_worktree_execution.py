#!/usr/bin/env python3
"""
run_temp_worktree_execution.py

Temp-worktree execution harness v0.

Given a validated execution packet with a human approval marker, creates a
disposable Git worktree, runs a mocked executor inside it, collects the diff,
validates it against the packet constraints, and stops at PATCH_READY_FOR_HUMAN_REVIEW.

No real Claude execution. No git push. No PR creation. No merge. No dispatch.
No Hermes mutation. No board updates. No audit append. No memory/profile writes.

Usage:
    python3 scripts/local/run_temp_worktree_execution.py \
        --packet-json /tmp/packet.json \
        --output-json /tmp/result.json \
        --output-md /tmp/result.md

Exit codes:
    0 — evaluation complete (any state written to output JSON)
    1 — fatal error (missing args, invalid input, etc.)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.resolve()

PROTECTED_GATE_SCRIPTS = [
    "scripts/local/final_gate_status.py",
    "scripts/local/verify_final_head_merge_command.py",
    "scripts/local/check_persistent_mutation_guard.py",
    "scripts/local/plan_preview_eval_status.py",
]

WORKTREE_BASE = Path("/tmp/aed_runs/worktrees")

OUTPUT_STATES = frozenset([
    "HOLD_INVALID_PACKET",
    "HOLD_PLAN_NOT_APPROVED",
    "HOLD_PLAN_HASH_MISMATCH",
    "HOLD_MAIN_DIRTY",
    "HOLD_OUTPUT_PATH_INSIDE_REPO",
    "HOLD_WORKTREE_CREATE_FAILED",
    "HOLD_EXECUTOR_NOT_ALLOWED",
    "HOLD_EXECUTOR_FAILED",
    "HOLD_REPO_MUTATION",
    "HOLD_FORBIDDEN_FILE_TOUCHED",
    "HOLD_OUTSIDE_ALLOWED_FILES",
    "HOLD_TOO_MANY_FILES_CHANGED",
    "HOLD_DIFF_VALIDATION_FAILED",
    "HOLD_UNKNOWN",
    "PATCH_READY_FOR_HUMAN_REVIEW",
])

# ---------------------------------------------------------------------------
# SHA-256 helpers
# ---------------------------------------------------------------------------

def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_str(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git_status(repo_path: str | Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "status", "--porcelain"],
        capture_output=True, text=True, timeout=10
    )
    return result.stdout.strip() or "clean"


def git_status_clean(repo_path: str | Path) -> bool:
    """Check if repo has no staged changes and no unstaged modifications.
    Untracked files (??) are allowed in the working tree and do not block worktree creation.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_path), "status", "--porcelain"],
        capture_output=True, text=True, timeout=10
    )
    lines = [l for l in result.stdout.strip().splitlines() if l.startswith("?? ")]
    # Untracked files are OK; only staged (A/M/D) or unstaged modified ( M) lines block
    non_untracked = [l for l in result.stdout.strip().splitlines() if not l.startswith("?? ")]
    return len(non_untracked) == 0


def git_rev_parse(repo_path: str | Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", ref],
        capture_output=True, text=True, timeout=10
    )
    return result.stdout.strip()


def git_worktree_add(worktree_path: Path, base_sha: str, parent_repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(parent_repo), "worktree", "add", str(worktree_path), base_sha],
        capture_output=True, text=True, timeout=30
    )


def git_worktree_remove(worktree_path: Path, parent_repo: Path) -> None:
    subprocess.run(
        ["git", "-C", str(parent_repo), "worktree", "remove", "--force", str(worktree_path)],
        capture_output=True, text=True, timeout=30
    )
    if worktree_path.exists():
        shutil.rmtree(worktree_path)


def git_diff(worktree_path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(worktree_path), "diff", "-- unified=3"],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout


def git_diff_name_only(worktree_path: Path) -> list[str]:
    """Return list of staged changed file paths in worktree."""
    result = subprocess.run(
        ["git", "-C", str(worktree_path), "diff", "--cached", "--name-only"],
        capture_output=True, text=True, timeout=30
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def normalize_path(path: str, base: Path) -> Path:
    """Resolve path relative to base, error on escape."""
    p = (base / path).resolve()
    if not str(p).startswith(str(base.resolve())):
        raise ValueError(f"Path escapes base: {path}")
    return p


def path_inside_repo(path: Path, repo_root: Path) -> bool:
    """True if path is inside repo_root."""
    try:
        resolved = path.resolve().relative_to(repo_root.resolve())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Packet validation
# ---------------------------------------------------------------------------

def validate_packet(packet: dict) -> tuple[bool, str]:
    """Check packet_kind and required top-level fields. Returns (ok, error)."""
    if packet.get("packet_kind") != "aed.temp_worktree.execution.v0":
        return False, f"packet_kind must be 'aed.temp_worktree.execution.v0', got '{packet.get('packet_kind')}'"

    for field in ["run_id", "task_id", "base_sha", "approved_plan_path", "approved_plan_sha256",
                  "approval", "task", "execution"]:
        if field not in packet:
            return False, f"missing required field: {field}"

    return True, ""


def validate_approval(approval: dict, plan_path: str) -> tuple[bool, str]:
    """Validate human approval marker. Returns (ok, error)."""
    if not approval.get("approved_for_temp_worktree_execution"):
        return False, "approval.approved_for_temp_worktree_execution must be true"

    if approval.get("approved_by") != "human":
        return False, "approval.approved_by must be 'human'"

    expected_sha = approval.get("approved_plan_sha256", "")
    if not expected_sha:
        return False, "approval.approved_plan_sha256 is required"

    # Verify SHA-256 of the plan file
    plan_file = Path(plan_path)
    if not plan_file.is_file():
        return False, f"approved_plan_path does not exist: {plan_path}"

    actual_sha = sha256_file(plan_file)
    if actual_sha != expected_sha:
        return False, (
            f"approved_plan_sha256 mismatch: expected {expected_sha}, got {actual_sha}"
        )

    # Check timestamp is within 24h
    approved_at = approval.get("approved_at", "")
    if not approved_at:
        return False, "approval.approved_at is required"

    try:
        approved_time = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_hours = (now - approved_time).total_seconds() / 3600
        if age_hours > 24:
            return False, f"approval.approved_at is more than 24h old ({age_hours:.1f}h)"
    except ValueError as e:
        return False, f"approval.approved_at is not valid ISO-8601: {e}"

    return True, ""


# ---------------------------------------------------------------------------
# Constraint checking
# ---------------------------------------------------------------------------

def check_forbidden_file_touched(
    changed_files: list[str],
    forbidden_files: list[str],
    worktree_path: Path
) -> list[str]:
    """Return list of forbidden files that were changed."""
    violated = []
    for cf in changed_files:
        cf_resolved = (worktree_path / cf).resolve()
        for fb in forbidden_files:
            # Normalize forbidden path
            fb_clean = fb.rstrip("/")
            # Check exact match or prefix match
            if cf == fb_clean or cf.startswith(fb_clean + "/"):
                violated.append(cf)
    return violated


def check_outside_allowed(
    changed_files: list[str],
    allowed_files: list[str],
    worktree_path: Path
) -> list[str]:
    """Return list of changed files not in allowed_files."""
    violated = []
    for cf in changed_files:
        if cf not in allowed_files:
            violated.append(cf)
    return violated


def check_protected_gate_scripts(
    changed_files: list[str],
    worktree_path: Path
) -> list[str]:
    """Return list of changed gate scripts."""
    violated = []
    for cf in changed_files:
        if cf in PROTECTED_GATE_SCRIPTS:
            violated.append(cf)
    return violated


def check_too_many_files(
    changed_files: list[str],
    max_changed_files: int
) -> bool:
    """Return True if changed files exceed limit."""
    return len(changed_files) > max_changed_files


# ---------------------------------------------------------------------------
# Mock executor
# ---------------------------------------------------------------------------

def apply_mock_edits(worktree_path: Path, mock_edits: list[dict]) -> list[str]:
    """
    Apply mock_edits to worktree and return list of changed file paths.

    mock_edits format: [{"path": "relative/path.md", "content": "new content"}]
    Content is always a full-file replacement.
    Files are written and `git add` is called so they appear in `git diff --cached`
    and `git diff --name-only` as staged additions.
    """
    changed = []
    for edit in mock_edits:
        file_path = worktree_path / edit["path"]
        # Security: reject absolute paths or paths with ..
        if not str(file_path.resolve()).startswith(str(worktree_path.resolve())):
            raise ValueError(f"mock edit path escapes worktree: {edit['path']}")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(edit["content"], encoding="utf-8")
        changed.append(edit["path"])

    # Stage all written files so git diff captures them
    if changed:
        subprocess.run(
            ["git", "-C", str(worktree_path), "add", "--"] + changed,
            capture_output=True, timeout=10
        )
    return changed


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

def run(packet: dict, output_json: str, output_md: str) -> dict:
    """
    Main execution path. Returns the result dict (also written to output_json).
    """
    run_id = packet.get("run_id", "unknown")
    worktree_root = WORKTREE_BASE / run_id

    result = {
        "status": "HOLD_UNKNOWN",
        "run_id": run_id,
        "base_sha": packet.get("base_sha", ""),
        "worktree_path": str(worktree_root),
        "changed_files": [],
        "validation_errors": [],
        "main_git_status_before": "unknown",
        "main_git_status_after": "unknown",
        "worktree_git_status_before": "unknown",
        "worktree_git_status_after": "unknown",
        "diff_path": "",
        "patch_ready": False,
        "next_action": "fix validation error and retry",
    }

    # ---- Phase 1: Packet validation ---------------------------------------

    ok, err = validate_packet(packet)
    if not ok:
        result["status"] = "HOLD_INVALID_PACKET"
        result["validation_errors"] = [err]
        result["next_action"] = "fix packet format"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 2: Approval marker -----------------------------------------

    approval = packet.get("approval", {})
    plan_path = packet.get("approved_plan_path", "")
    ok, err = validate_approval(approval, plan_path)
    if not ok:
        result["status"] = "HOLD_PLAN_NOT_APPROVED"
        result["validation_errors"] = [err]
        result["next_action"] = "obtain valid human approval marker"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 3: Main repo clean check ------------------------------------

    main_status_before = git_status(REPO_ROOT)
    result["main_git_status_before"] = main_status_before

    if not git_status_clean(REPO_ROOT):
        result["status"] = "HOLD_MAIN_DIRTY"
        result["validation_errors"] = [f"main repo has staged or unstaged changes; untracked files are allowed: {main_status_before}"]
        result["next_action"] = "clean main repo (commit, reset, or discard staged/unstaged changes) and retry"
        _write_output(result, output_json, output_md)
        return result

    # Verify main is at base_sha
    main_head = git_rev_parse(REPO_ROOT, "HEAD")
    if main_head != packet.get("base_sha"):
        result["status"] = "HOLD_MAIN_DIRTY"
        result["validation_errors"] = [
            f"main HEAD ({main_head}) != packet base_sha ({packet.get('base_sha')})"
        ]
        result["next_action"] = "ensure main is at base_sha and retry"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 4: Path safety checks ---------------------------------------

    output_root = Path(packet.get("execution", {}).get("output_root", f"/tmp/aed_runs/{run_id}"))
    if path_inside_repo(output_root, REPO_ROOT):
        result["status"] = "HOLD_OUTPUT_PATH_INSIDE_REPO"
        result["validation_errors"] = [f"output_root cannot be inside repo: {output_root}"]
        result["next_action"] = "move output_root outside repo"
        _write_output(result, output_json, output_md)
        return result

    if path_inside_repo(worktree_root, REPO_ROOT):
        result["status"] = "HOLD_WORKTREE_CREATE_FAILED"
        result["validation_errors"] = [f"worktree path cannot be inside repo: {worktree_root}"]
        result["next_action"] = "ensure worktree root is outside repo"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 5: Execution mode check -------------------------------------

    exec_mode = packet.get("execution", {}).get("mode", "mock")
    if exec_mode != "mock":
        result["status"] = "HOLD_EXECUTOR_NOT_ALLOWED"
        result["validation_errors"] = [f"execution.mode must be 'mock', got '{exec_mode}'"]
        result["next_action"] = "set execution.mode to 'mock' or use a different harness"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 6: Create worktree -----------------------------------------

    if worktree_root.exists():
        try:
            git_worktree_remove(worktree_root, REPO_ROOT)
        except Exception:
            pass
        shutil.rmtree(worktree_root, ignore_errors=True)

    worktree_root.mkdir(parents=True, exist_ok=True)

    base_sha = packet.get("base_sha", "")
    wt_result = git_worktree_add(worktree_root, base_sha, REPO_ROOT)
    if wt_result.returncode != 0:
        result["status"] = "HOLD_WORKTREE_CREATE_FAILED"
        result["validation_errors"] = [f"git worktree add failed: {wt_result.stderr}"]
        result["next_action"] = "check base_sha is valid and worktree path is available"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 7: Pre-execution git status --------------------------------

    main_status_after_create = git_status(REPO_ROOT)
    if not git_status_clean(REPO_ROOT):
        result["status"] = "HOLD_REPO_MUTATION"
        result["validation_errors"] = [
            f"main repo became dirty after worktree creation: {main_status_after_create}"
        ]
        result["next_action"] = "investigate main repo mutation"
        _write_output(result, output_json, output_md)
        return result

    worktree_status_before = git_status(worktree_root)
    result["worktree_git_status_before"] = worktree_status_before

    # ---- Phase 8: Run mock executor ---------------------------------------

    mock_edits = packet.get("execution", {}).get("mock_edits", [])
    try:
        changed_files = apply_mock_edits(worktree_root, mock_edits)
    except Exception as e:
        result["status"] = "HOLD_EXECUTOR_FAILED"
        result["validation_errors"] = [f"mock executor failed: {e}"]
        result["next_action"] = "check mock_edits format"
        _write_output(result, output_json, output_md)
        return result

    # If no mock_edits, the worktree should be clean (no files changed)
    if not changed_files:
        # Write an empty diff for the no-change case
        diff_path = str(worktree_root / "diff.patch")
        Path(diff_path).write_text("", encoding="utf-8")
        result["status"] = "PATCH_READY_FOR_HUMAN_REVIEW"
        result["changed_files"] = []
        result["validation_errors"] = []
        result["patch_ready"] = True
        result["next_action"] = "human reviews empty diff; no patch to apply"
        result["worktree_git_status_after"] = git_status(worktree_root)
        result["main_git_status_after"] = git_status(REPO_ROOT)
        result["diff_path"] = diff_path
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 9: Post-execution status capture ---------------------------

    worktree_status_after = git_status(worktree_root)
    result["worktree_git_status_after"] = worktree_status_after

    main_status_after = git_status(REPO_ROOT)
    result["main_git_status_after"] = main_status_after

    if not git_status_clean(REPO_ROOT):
        result["status"] = "HOLD_REPO_MUTATION"
        result["validation_errors"] = [
            f"main repo git status changed during execution: {main_status_after}"
        ]
        result["next_action"] = "investigate main repo mutation"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 10: Collect changed files and diff --------------------------

    diff_text = git_diff(worktree_root)
    result["diff_path"] = str(worktree_root / "diff.patch")
    Path(result["diff_path"]).write_text(diff_text, encoding="utf-8")

    result["changed_files"] = changed_files

    # ---- Phase 11: Diff validation -----------------------------------------

    task = packet.get("task", {})
    allowed_files = task.get("allowed_files", [])
    forbidden_files = task.get("forbidden_files", [])

    validation_errors: list[str] = []

    # Check each class of violation
    outside_allowed = check_outside_allowed(changed_files, allowed_files, worktree_root)
    if outside_allowed:
        for f in outside_allowed:
            validation_errors.append(f"file changed outside allowed_files: {f}")

    forbidden_touched = check_forbidden_file_touched(changed_files, forbidden_files, worktree_root)
    if forbidden_touched:
        for f in forbidden_touched:
            validation_errors.append(f"forbidden file touched: {f}")

    gate_scripts = check_protected_gate_scripts(changed_files, worktree_root)
    if gate_scripts:
        for f in gate_scripts:
            validation_errors.append(f"protected gate script modified: {f}")

    max_files = approval.get("max_changed_files", 999)
    if check_too_many_files(changed_files, max_files):
        validation_errors.append(
            f"changed files ({len(changed_files)}) exceeds max_changed_files ({max_files})"
        )

    if validation_errors:
        result["status"] = (
            "HOLD_FORBIDDEN_FILE_TOUCHED" if forbidden_touched else
            "HOLD_OUTSIDE_ALLOWED_FILES" if outside_allowed else
            "HOLD_TOO_MANY_FILES_CHANGED" if check_too_many_files(changed_files, max_files) else
            "HOLD_DIFF_VALIDATION_FAILED"
        )
        result["validation_errors"] = validation_errors
        result["next_action"] = "fix constraint violations in plan"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 12: Success ------------------------------------------------

    result["status"] = "PATCH_READY_FOR_HUMAN_REVIEW"
    result["patch_ready"] = True
    result["next_action"] = "human reviews diff.patch; manually apply or discard"
    _write_output(result, output_json, output_md)
    return result


def _write_output(result: dict, output_json: str, output_md: str) -> None:
    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    md = _render_markdown(result)
    Path(output_md).parent.mkdir(parents=True, exist_ok=True)
    with open(output_md, "w", encoding="utf-8") as f:
        f.write(md)


def _render_markdown(result: dict) -> str:
    status = result["status"]
    patch_ready = result.get("patch_ready", False)

    lines = [
        "# Temp-Worktree Execution Result",
        "",
        f"**Status**: `{status}`",
        f"**Run ID**: `{result.get('run_id', 'unknown')}`",
        f"**Base SHA**: `{result.get('base_sha', '')}`",
        f"**Worktree**: `{result.get('worktree_path', '')}`",
        f"**Changed files**: {len(result.get('changed_files', []))}",
        "",
    ]

    if result.get("changed_files"):
        lines.append("## Changed Files")
        for cf in result["changed_files"]:
            lines.append(f"- `{cf}`")
        lines.append("")

    errors = result.get("validation_errors", [])
    if errors:
        lines.append("## Validation Errors")
        for err in errors:
            lines.append(f"- `{err}`")
        lines.append("")

    lines.extend([
        f"**Patch ready**: {patch_ready}",
        f"**Next action**: {result.get('next_action', 'unknown')}",
        "",
        f"**Main git status before**: `{result.get('main_git_status_before', 'unknown')}`",
        f"**Main git status after**: `{result.get('main_git_status_after', 'unknown')}`",
        f"**Worktree git status before**: `{result.get('worktree_git_status_before', 'unknown')}`",
        f"**Worktree git status after**: `{result.get('worktree_git_status_after', 'unknown')}`",
        "",
        f"**Diff**: `{result.get('diff_path', 'none')}`",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Temp-worktree execution harness v0. "
                    "Mock execution only. No real Claude."
    )
    parser.add_argument(
        "--packet-json", required=True,
        help="Path to execution packet JSON"
    )
    parser.add_argument(
        "--output-json", required=True,
        help="Path to write result JSON"
    )
    parser.add_argument(
        "--output-md", required=True,
        help="Path to write result Markdown"
    )

    args = parser.parse_args()

    packet_path = Path(args.packet_json)
    if not packet_path.is_file():
        print(f"FATAL: packet file not found: {args.packet_json}", file=sys.stderr)
        return 1

    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"FATAL: invalid JSON in packet: {e}", file=sys.stderr)
        return 1

    result = run(packet, args.output_json, args.output_md)
    print(f"Status: {result['status']}")
    print(f"Output: {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())