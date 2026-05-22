#!/usr/bin/env python3
"""
apply_temp_worktree_patch_to_branch.py

Gated apply tool that applies a temp-worktree patch to a newly created local branch.

IMPORTANT SAFETY BOUNDARY:
  This tool does NOT push, does NOT create PRs, does NOT merge, does NOT invoke Claude.
  It creates a local branch and applies a patch to it, but all output stays local.
  Human must push and open PR manually after reviewing.

States:
  APPLY_TO_BRANCH_READY                   — all checks passed; patch ready to apply
  APPLY_TO_BRANCH_DRY_RUN_READY          — dry-run passed; no mutation occurred
  APPLY_TO_BRANCH_APPLIED                 — patch applied to local branch (not committed)
  HOLD_REAL_APPLY_NOT_ALLOWED            — --allow-real-apply not passed; dry-run only
  HOLD_RESULT_JSON_MISSING               — result.json does not exist
  HOLD_RESULT_JSON_INVALID               — result.json is not valid JSON
  HOLD_DIFF_PATCH_MISSING                — diff.patch does not exist
  HOLD_DIFF_PATCH_EMPTY                  — diff.patch is empty
  HOLD_TARGET_REPO_INVALID               — target repo is not a valid git repo
  HOLD_REPO_DIRTY                        — repo has staged/unstaged changes
  HOLD_OUTPUT_INSIDE_REPO                — output path is inside the repo
  HOLD_BRANCH_NAME_INVALID               — branch name contains unsafe characters
  HOLD_BRANCH_ALREADY_EXISTS             — branch already exists
  HOLD_BASE_SHA_MISMATCH                 — current HEAD does not match expected base
  HOLD_APPLY_NOT_READY                   — apply-readiness not APPLY_READY/APPLY_PREVIEW_READY
  HOLD_GIT_APPLY_CHECK_FAILED            — git apply --check failed
  HOLD_GIT_BRANCH_CREATE_FAILED          — git checkout -b failed
  HOLD_GIT_APPLY_FAILED                  — git apply failed
  HOLD_CHANGED_FILES_MISMATCH            — applied files don't match expected changed_files
  HOLD_FORBIDDEN_FILE_CHANGED            — a forbidden file was changed by the patch
  HOLD_PROTECTED_FILE_CHANGED            — a protected file was changed by the patch
  HOLD_UNEXPECTED_MUTATION               — unexpected change detected post-apply
  HOLD_INTERNAL_ERROR                    — unexpected exception

Safety behavior:
  - Never runs git push
  - Never runs gh pr create
  - Never runs git merge
  - Never invokes Claude
  - Never installs packages
  - Never uses shell=True
  - apply_allowed is false unless --allow-real-apply is explicitly passed

Usage:
  # Dry-run (no mutation):
  python3 scripts/local/apply_temp_worktree_patch_to_branch.py \\
    --result-json /tmp/aed_run/result.json \\
    --diff-patch /tmp/aed_run/diff.patch \\
    --target-repo /home/max/Automated-Edge-Discovery \\
    --branch-name apply/test-branch \\
    --output-json /tmp/apply_result.json \\
    --output-md /tmp/apply_result.md

  # Real apply (local branch + apply, no push/PR/merge):
  python3 scripts/local/apply_temp_worktree_patch_to_branch.py \\
    --result-json /tmp/aed_run/result.json \\
    --diff-patch /tmp/aed_run/diff.patch \\
    --target-repo /home/max/Automated-Edge-Discovery \\
    --branch-name apply/test-branch \\
    --allow-real-apply \\
    --output-json /tmp/apply_result.json \\
    --output-md /tmp/apply_result.md
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import re
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

STATE_READY                  = "APPLY_TO_BRANCH_READY"
STATE_DRY_RUN_READY          = "APPLY_TO_BRANCH_DRY_RUN_READY"
STATE_APPLIED                = "APPLY_TO_BRANCH_APPLIED"
STATE_REAL_APPLY_NOT_ALLOWED = "HOLD_REAL_APPLY_NOT_ALLOWED"
STATE_RESULT_MISSING         = "HOLD_RESULT_JSON_MISSING"
STATE_RESULT_INVALID         = "HOLD_RESULT_JSON_INVALID"
STATE_DIFF_MISSING           = "HOLD_DIFF_PATCH_MISSING"
STATE_DIFF_EMPTY             = "HOLD_DIFF_PATCH_EMPTY"
STATE_REPO_INVALID           = "HOLD_TARGET_REPO_INVALID"
STATE_REPO_DIRTY             = "HOLD_REPO_DIRTY"
STATE_OUTPUT_INSIDE_REPO     = "HOLD_OUTPUT_INSIDE_REPO"
STATE_BRANCH_INVALID         = "HOLD_BRANCH_NAME_INVALID"
STATE_BRANCH_EXISTS          = "HOLD_BRANCH_ALREADY_EXISTS"
STATE_BASE_SHA_MISMATCH      = "HOLD_BASE_SHA_MISMATCH"
STATE_APPLY_NOT_READY        = "HOLD_APPLY_NOT_READY"
STATE_APPLY_CHECK_FAILED     = "HOLD_GIT_APPLY_CHECK_FAILED"
STATE_BRANCH_CREATE_FAILED   = "HOLD_GIT_BRANCH_CREATE_FAILED"
STATE_APPLY_FAILED           = "HOLD_GIT_APPLY_FAILED"
STATE_CHANGED_FILES_MISMATCH = "HOLD_CHANGED_FILES_MISMATCH"
STATE_FORBIDDEN_CHANGED      = "HOLD_FORBIDDEN_FILE_CHANGED"
STATE_PROTECTED_CHANGED     = "HOLD_PROTECTED_FILE_CHANGED"
STATE_UNEXPECTED_MUTATION    = "HOLD_UNEXPECTED_MUTATION"
STATE_UNEXPECTED_UNTRACKED   = "HOLD_UNEXPECTED_UNTRACKED_FILE"
STATE_INTERNAL_ERROR         = "HOLD_INTERNAL_ERROR"

# Files that are forbidden from being changed by any patch
FORBIDDEN_PATHS = {
    ".aed_plan.md",
    "scripts/local/run_temp_worktree_execution.py",
    "scripts/local/check_real_claude_env_preflight.py",
}

# Protected paths that require extra review before change
PROTECTED_PATHS = {
    "scripts/local/check_persistent_mutation_guard.py",
    "scripts/local/final_gate_status.py",
    "scripts/local/verify_final_head_merge_command.py",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_head(repo_root: Path) -> str | None:
    """Return current HEAD SHA or None."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def _git_branch_exists(repo_root: Path, branch: str) -> bool:
    """Return True if local branch exists."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _git_status_clean(repo_root: Path) -> bool:
    """Return True if repo working tree is clean (no staged/unstaged changes)."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        non_untracked = [
            l for l in result.stdout.strip().splitlines()
            if not l.startswith("?? ")
        ]
        return len(non_untracked) == 0
    except Exception:
        return False


def _git_apply_check(repo_root: Path, diff_patch: Path) -> tuple[bool, str]:
    """Run git apply --check. Returns (success, output)."""
    try:
        result = subprocess.run(
            ["git", "apply", "--check", str(diff_patch)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)


def _git_apply(repo_root: Path, diff_patch: Path) -> tuple[bool, str]:
    """Run git apply (no --check). Returns (success, output)."""
    try:
        result = subprocess.run(
            ["git", "apply", str(diff_patch)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)


def _git_checkout_new_branch(repo_root: Path, branch: str) -> tuple[bool, str]:
    """Create and checkout a new branch. Returns (success, output)."""
    try:
        result = subprocess.run(
            ["git", "checkout", "-b", branch],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)


def _git_status_short(repo_root: Path) -> str:
    """Return git status --short output."""
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout
    except Exception:
        return ""


def _load_json(path: Path) -> dict | None:
    """Load JSON file, return None on error."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _path_inside_repo(path: str | Path, repo_root: Path) -> bool:
    """Return True if path resolves inside the repo."""
    try:
        return str(Path(path).resolve()).startswith(str(repo_root.resolve()))
    except Exception:
        return False


def _check_duplicate_paths(paths: list[str]) -> list[str] | None:
    """Return list of duplicate paths if any, else None."""
    seen: set[str] = set()
    dups: list[str] = []
    for p in paths:
        if p in seen:
            dups.append(p)
        seen.add(p)
    return dups if dups else None


def _validate_branch_name(name: str) -> tuple[bool, str]:
    """
    Validate branch name is safe.
    Returns (is_valid, error_message).
    """
    if not name:
        return False, "Branch name is empty"
    if name.startswith("-"):
        return False, "Branch name must not start with dash"
    # No spaces or other shell metacharacters
    if re.search(r"\s", name):
        return False, f"Branch name contains whitespace: {name}"
    # No git危险的 characters
    if re.search(r"[~^:?*\[]", name):
        return False, f"Branch name contains unsafe characters: {name}"
    # No parent directory traversal
    if ".." in name:
        return False, f"Branch name contains '..': {name}"
    # No double slashes or @{
    if "//" in name or "@{" in name:
        return False, f"Branch name contains reserved git characters: {name}"
    return True, ""


def _patched_files_from_diff(diff_content: str) -> list[str]:
    """Extract list of file paths that would be changed by the diff."""
    files = []
    for line in diff_content.splitlines():
        if line.startswith("diff --git a/"):
            # Extract "docs/foo.md" from "diff --git a/docs/foo.md b/docs/foo.md"
            parts = line.split(" ", 2)
            if len(parts) >= 3:
                path = parts[2].split(" b/")[0] if " b/" in parts[2] else parts[2]
                files.append(path.lstrip("a/"))
    return files


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply temp-worktree patch to a new local branch (gated, no push/PR/merge)."
    )
    parser.add_argument(
        "--result-json", required=True,
        help="Path to result.json from temp-worktree execution"
    )
    parser.add_argument(
        "--diff-patch", required=True,
        help="Path to diff.patch from temp-worktree execution"
    )
    parser.add_argument(
        "--target-repo", required=True,
        help="Path to the AED repository"
    )
    parser.add_argument(
        "--branch-name", required=True,
        help="Local branch name to create (e.g., apply/my-patch)"
    )
    parser.add_argument(
        "--output-json", required=True,
        help="Path to write JSON result"
    )
    parser.add_argument(
        "--output-md",
        help="Path to write Markdown result (optional)"
    )
    parser.add_argument(
        "--require-apply-ready", action="store_true", default=True,
        help="Require APPLY_READY/APPLY_PREVIEW_READY from prior verifier (default: True)"
    )
    parser.add_argument(
        "--apply-readiness-json",
        help="Path to apply-readiness JSON from verify_temp_worktree_apply_readiness.py"
    )
    parser.add_argument(
        "--allow-real-apply", action="store_true",
        help="REQUIRED: allow actual branch creation and patch apply (without this, only dry-run)"
    )
    parser.add_argument(
        "--expected-base-sha",
        help="Expected current HEAD SHA; blocks if HEAD doesn't match"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run all validations without mutating anything"
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def apply_patch_to_branch(
    result_json_path: Path,
    diff_patch_path: Path,
    target_repo: Path,
    branch_name: str,
    require_apply_ready: bool,
    apply_readiness_json_path: Path | None,
    allow_real_apply: bool,
    expected_base_sha: str | None,
    dry_run: bool,
) -> tuple[str, dict]:
    """
    Run all apply checks and optionally apply the patch to a new local branch.
    Returns (status, result_dict).
    """
    checks: dict[str, object] = {}
    command_log: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []

    def log_cmd(cmd: str, safe: bool = True) -> None:
        # Redact paths in command log for safety
        log_entry = f"[{'OK' if safe else 'BLOCKED'}] {cmd}"
        command_log.append(log_entry)

    # ── 1. Result JSON ────────────────────────────────────────────────────────
    result = _load_json(result_json_path)
    if result is None:
        if not result_json_path.exists():
            return STATE_RESULT_MISSING, {"result_json_exists": False}
        return STATE_RESULT_INVALID, {"result_json_exists": True, "valid_json": False}
    checks["result_json_exists"] = True
    checks["result_json_valid"] = True

    # ── 2. Diff patch ─────────────────────────────────────────────────────────
    if not diff_patch_path.exists():
        return STATE_DIFF_MISSING, {**checks, "diff_patch_exists": False}
    checks["diff_patch_exists"] = True

    diff_content = diff_patch_path.read_text(encoding="utf-8", errors="replace")
    if not diff_content.strip():
        return STATE_DIFF_EMPTY, {**checks, "diff_patch_exists": True, "diff_patch_empty": True}
    checks["diff_patch_non_empty"] = True
    checks["diff_patch_size_bytes"] = len(diff_content)

    # ── 3. Target repo is valid git repo ────────────────────────────────────────
    if not (target_repo / ".git").exists():
        # Try git rev-parse as alternative check
        try:
            result_git = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=str(target_repo),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result_git.returncode != 0:
                return STATE_REPO_INVALID, {**checks, "target_repo_valid_git": False}
        except Exception:
            return STATE_REPO_INVALID, {**checks, "target_repo_valid_git": False}
    checks["target_repo_valid_git"] = True

    # ── 4. Repo git status clean ───────────────────────────────────────────────
    repo_clean = _git_status_clean(target_repo)
    checks["repo_git_status_clean"] = repo_clean
    if not repo_clean:
        return STATE_REPO_DIRTY, {**checks, "repo_status_dirty": True}
    checks["repo_clean"] = True

    # ── 5. Output paths outside repo ───────────────────────────────────────────
    # (checked in main() before calling apply_patch_to_branch)

    # ── 6. Branch name validation ──────────────────────────────────────────────
    branch_valid, branch_err = _validate_branch_name(branch_name)
    checks["branch_name"] = branch_name
    checks["branch_name_valid"] = branch_valid
    if not branch_valid:
        return STATE_BRANCH_INVALID, {**checks, "branch_name_error": branch_err}
    log_cmd(f"branch name validated: {branch_name}", safe=True)

    # ── 7. Branch does not already exist ───────────────────────────────────────
    branch_exists = _git_branch_exists(target_repo, branch_name)
    checks["branch_exists"] = branch_exists
    if branch_exists:
        return STATE_BRANCH_EXISTS, {**checks, "branch_already_exists": True}
    log_cmd(f"branch does not exist: {branch_name}", safe=True)

    # ── 8. Expected base SHA match ─────────────────────────────────────────────
    current_head = _git_head(target_repo)
    checks["current_head"] = current_head
    if expected_base_sha:
        checks["expected_base_sha"] = expected_base_sha
        if current_head != expected_base_sha:
            return STATE_BASE_SHA_MISMATCH, {
                **checks,
                "current_head": current_head,
                "expected_base_sha": expected_base_sha,
                "mismatch": True,
            }
        log_cmd(f"HEAD matches expected: {expected_base_sha}", safe=True)

    # ── 9. Apply-readiness JSON validation ────────────────────────────────────
    if require_apply_ready and apply_readiness_json_path:
        readiness = _load_json(apply_readiness_json_path)
        checks["apply_readiness_valid"] = readiness is not None
        if readiness is None:
            if not apply_readiness_json_path.exists():
                return STATE_APPLY_NOT_READY, {
                    **checks, "apply_readiness_exists": False,
                    "require_apply_ready": True,
                }
            return STATE_APPLY_NOT_READY, {
                **checks, "apply_readiness_valid": False,
                "require_apply_ready": True,
            }

        readiness_status = readiness.get("status", "")
        checks["apply_readiness_status"] = readiness_status
        if readiness_status not in ("APPLY_READY", "APPLY_PREVIEW_READY"):
            return STATE_APPLY_NOT_READY, {
                **checks,
                "apply_readiness_status": readiness_status,
                "require_apply_ready": True,
            }
        log_cmd("apply-readiness confirmed: APPLY_READY/APPLY_PREVIEW_READY", safe=True)

    # ── 10. Result status PATCH_READY_FOR_HUMAN_REVIEW ──────────────────────────
    result_status = result.get("status", "")
    checks["result_status"] = result_status
    if result_status != "PATCH_READY_FOR_HUMAN_REVIEW":
        return STATE_APPLY_NOT_READY, {
            **checks,
            "expected_status": "PATCH_READY_FOR_HUMAN_REVIEW",
            "actual_status": result_status,
        }

    # ── 11. Changed files non-empty and unique ─────────────────────────────────
    changed_files = result.get("changed_files", [])
    checks["changed_files"] = changed_files
    if not changed_files:
        return STATE_CHANGED_FILES_MISMATCH, {**checks, "changed_files_empty": True}
    checks["changed_files_count"] = len(changed_files)

    dups = _check_duplicate_paths(changed_files)
    if dups:
        return STATE_CHANGED_FILES_MISMATCH, {
            **checks,
            "duplicate_paths": dups,
            "changed_files_count": len(changed_files),
        }

    # ── 12. .aed_plan.md not in changed_files ───────────────────────────────────
    if ".aed_plan.md" in changed_files:
        return STATE_CHANGED_FILES_MISMATCH, {
            **checks,
            "aed_plan_in_changed_files": True,
        }

    # ── 13. Changed files within allowed_files ─────────────────────────────────
    task = result.get("task", {})
    allowed_files = task.get("allowed_files", [])
    forbidden_files = task.get("forbidden_files", [])

    checks["allowed_files"] = allowed_files
    checks["forbidden_files"] = forbidden_files

    if allowed_files:
        outside = [cf for cf in changed_files if cf not in allowed_files]
        if outside:
            return STATE_CHANGED_FILES_MISMATCH, {
                **checks,
                "files_outside_allowed": outside,
            }
        checks["all_changed_files_allowed"] = True

    # ── 14. No forbidden files changed ─────────────────────────────────────────
    if forbidden_files:
        forbidden_hit = [cf for cf in changed_files if cf in forbidden_files]
        if forbidden_hit:
            return STATE_FORBIDDEN_CHANGED, {
                **checks,
                "forbidden_files_hit": forbidden_hit,
            }
        checks["no_forbidden_files_changed"] = True

    # ── 15. No protected files changed ─────────────────────────────────────────
    protected_hit = [cf for cf in changed_files if cf in PROTECTED_PATHS]
    if protected_hit:
        return STATE_PROTECTED_CHANGED, {
            **checks,
            "protected_files_hit": protected_hit,
        }
    checks["no_protected_files_changed"] = True

    # ── 16. git apply --check ───────────────────────────────────────────────────
    apply_check_ok, apply_check_output = _git_apply_check(target_repo, diff_patch_path)
    checks["git_apply_check_passed"] = apply_check_ok
    checks["git_apply_check_output"] = apply_check_output[:500]
    log_cmd(f"git apply --check: {'PASS' if apply_check_ok else 'FAIL'}", safe=True)
    if not apply_check_ok:
        return STATE_APPLY_CHECK_FAILED, {
            **checks,
            "git_apply_check_passed": False,
            "git_apply_check_output": apply_check_output[:500],
        }

    # ── 17. diff.patch contains each changed file ───────────────────────────────
    for cf in changed_files:
        if cf not in diff_content:
            return STATE_CHANGED_FILES_MISMATCH, {
                **checks,
                "missing_from_diff": cf,
            }
    checks["diff_contains_all_changed_files"] = True

    # ── 18. Check for forbidden/protected paths in diff ───────────────────────
    diff_files = _patched_files_from_diff(diff_content)
    checks["diff_patched_files"] = diff_files

    forbidden_in_diff = [f for f in diff_files if f in FORBIDDEN_PATHS or f in forbidden_files]
    if forbidden_in_diff:
        return STATE_FORBIDDEN_CHANGED, {
            **checks,
            "forbidden_in_diff": forbidden_in_diff,
        }

    protected_in_diff = [f for f in diff_files if f in PROTECTED_PATHS]
    if protected_in_diff:
        return STATE_PROTECTED_CHANGED, {
            **checks,
            "protected_in_diff": protected_in_diff,
        }

    checks["diff_safe"] = True
    log_cmd("diff content validated: no forbidden/protected files", safe=True)

    # ── 19. Dry-run / --allow-real-apply gate ──────────────────────────────────
    checks["dry_run"] = dry_run
    checks["allow_real_apply"] = allow_real_apply

    if dry_run or not allow_real_apply:
        # Dry-run mode: all checks passed, report planned commands only
        planned_commands = [
            f"git -C {target_repo} checkout -b {branch_name}",
            f"git -C {target_repo} apply {diff_patch_path}",
            f"git -C {target_repo} status --short",
        ]
        checks["planned_commands"] = planned_commands
        checks["applied"] = False
        checks["branch_created"] = False
        checks["apply_allowed"] = False

        status = STATE_DRY_RUN_READY if dry_run else STATE_REAL_APPLY_NOT_ALLOWED
        return status, {
            **checks,
            "apply_allowed": False,
            "applied": False,
            "branch_created": False,
            "real_apply_allowed": False,
        }

    # ── 20. Real apply: create branch ───────────────────────────────────────────
    branch_created = False
    applied = False

    create_ok, create_output = _git_checkout_new_branch(target_repo, branch_name)
    checks["git_branch_create_ok"] = create_ok
    checks["git_branch_create_output"] = create_output[:500]
    log_cmd(f"git checkout -b {branch_name}: {'OK' if create_ok else 'FAIL'}", safe=create_ok)
    if not create_ok:
        return STATE_BRANCH_CREATE_FAILED, {
            **checks,
            "git_branch_create_ok": False,
            "git_branch_create_output": create_output[:500],
        }
    branch_created = True

    # ── 21. Real apply: run git apply ───────────────────────────────────────────
    apply_ok, apply_output = _git_apply(target_repo, diff_patch_path)
    checks["git_apply_ok"] = apply_ok
    checks["git_apply_output"] = apply_output[:500]
    log_cmd(f"git apply: {'OK' if apply_ok else 'FAIL'}", safe=apply_ok)
    if not apply_ok:
        checks["git_apply_ok"] = False
        checks["applied"] = False
        # Don't auto-cleanup; report the failure clearly
        return STATE_APPLY_FAILED, {
            **checks,
            "git_apply_ok": False,
            "git_apply_output": apply_output[:500],
            "branch_created": True,
            "branch_name": branch_name,
            "warning": "Branch was created but apply failed. Repo is on failed branch.",
        }
    applied = True

    # ── 22. Verify git status shows only expected changed files ─────────────────
    status_output = _git_status_short(target_repo)
    checks["post_apply_git_status"] = status_output

    # Parse staged/unstaged changes AND expected untracked files.
    # git apply creates new files as untracked (??), not staged (A).
    # We count them as applied only if they appear in expected changed_files.
    modified = []
    untracked_expected = []
    untracked_unexpected = []
    for line in status_output.strip().splitlines():
        if not line:
            continue
        parts = line.strip().split()
        if len(parts) >= 2:
            stage, filepath = parts[0], parts[1]
            if stage in ("M", "A", "D", "R"):
                modified.append(filepath)
            elif stage == "??":
                if filepath in changed_files:
                    untracked_expected.append(filepath)
                else:
                    untracked_unexpected.append(filepath)

    checks["modified_files"] = modified
    checks["untracked_expected"] = untracked_expected
    checks["untracked_unexpected"] = untracked_unexpected
    checks["expected_changed_files"] = changed_files

    # Unexpected tracked modifications are always a problem
    unexpected = [f for f in modified if f not in changed_files]
    if unexpected:
        return STATE_UNEXPECTED_MUTATION, {
            **checks,
            "applied": True,
            "branch_created": True,
            "branch_name": branch_name,
            "unexpected_modified_files": unexpected,
        }

    # Unexpected untracked files are a problem unless we have a clear reason
    # to believe they came from the apply (for now, any unexpected untracked
    # file not in changed_files is treated as suspicious)
    if untracked_unexpected:
        return STATE_UNEXPECTED_UNTRACKED, {
            **checks,
            "applied": True,
            "branch_created": True,
            "branch_name": branch_name,
            "unexpected_untracked_files": untracked_unexpected,
        }

    # Build the complete set of detected applied files
    detected_applied = set(modified) | set(untracked_expected)

    missing = [f for f in changed_files if f not in detected_applied]
    if missing:
        return STATE_CHANGED_FILES_MISMATCH, {
            **checks,
            "applied": True,
            "branch_created": True,
            "branch_name": branch_name,
            "missing_modified_files": missing,
        }

    checks["changed_files_match"] = True
    checks["no_unexpected_mutations"] = True

    # ── 23. Final: set apply_allowed = True only in APPLIED state ───────────────
    checks["apply_allowed"] = True
    checks["real_apply_allowed"] = True

    log_cmd("=== APPLY COMPLETE ===", safe=True)

    return STATE_APPLIED, {
        **checks,
        "applied": True,
        "branch_created": True,
        "branch_name": branch_name,
        "apply_allowed": True,
        "real_apply_allowed": True,
    }


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_json_output(
    output_path: Path,
    status: str,
    result: dict,
    result_json_path: Path,
    diff_patch_path: Path,
    target_repo: Path,
    branch_name: str,
    command_log: list[str],
    apply_allowed: bool,
) -> None:
    """Write JSON output."""
    output: dict[str, object] = {
        "status": status,
        "apply_allowed": apply_allowed,
        "applied": result.get("applied", False),
        "branch_created": result.get("branch_created", False),
        "target_branch": branch_name,
        "base_sha": result.get("current_head"),
        "current_head_before": result.get("current_head"),
        "current_head_after": result.get("current_head"),  # same before apply (no commit)
        "changed_files": result.get("changed_files", []),
        "validation_errors": [],
        "command_log": command_log,
        "safety_checks": {
            "no_push": True,
            "no_pr_create": True,
            "no_merge": True,
            "no_claude_invocation": True,
            "no_package_install": True,
            "no_shell_true": True,
            "apply_allowed_only_with_flag": True,
        },
        "output_paths": {
            "result_json": str(result_json_path.resolve()),
            "diff_patch": str(diff_patch_path.resolve()),
            "target_repo": str(target_repo.resolve()),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": result.get("dry_run", False),
        "git_apply_check_passed": result.get("git_apply_check_passed"),
        "planned_commands": result.get("planned_commands", []),
        "git_apply_ok": result.get("git_apply_ok"),
        "modified_files": result.get("modified_files", []),
        "expected_changed_files": result.get("changed_files", []),
    }
    output_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def write_md_output(
    output_path: Path,
    status: str,
    result: dict,
    result_json_path: Path,
    diff_patch_path: Path,
    target_repo: Path,
    branch_name: str,
    command_log: list[str],
) -> None:
    """Write Markdown output."""
    lines = [
        "# Temp Worktree Apply to Branch Result\n",
        f"**Status:** `{status}`\n",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}\n",
        "---\n",
    ]

    applied = result.get("applied", False)
    branch_created = result.get("branch_created", False)

    if status in (STATE_APPLIED, STATE_DRY_RUN_READY, STATE_READY):
        lines.append("## ✅ Verdict: Patch Ready / Applied\n")
        lines.append(f"- **Branch:** `{branch_name}`\n")
        lines.append(f"- **Branch created:** {'Yes' if branch_created else 'No'}\n")
        lines.append(f"- **Patch applied:** {'Yes' if applied else 'No (dry-run)'}\n")
        lines.append(f"- **apply_allowed:** {result.get('apply_allowed', False)}\n")
    else:
        lines.append(f"## ❌ Verdict: {status}\n")

    lines.extend([
        "---\n",
        "## Inputs\n",
        f"- **result.json:** `{result_json_path}`\n",
        f"- **diff.patch:** `{diff_patch_path}`\n",
        f"- **target repo:** `{target_repo}`\n",
        f"- **branch:** `{branch_name}`\n",
    ])

    changed_files = result.get("changed_files", [])
    if changed_files:
        lines.append("\n## Changed Files\n")
        for cf in changed_files:
            lines.append(f"- `{cf}`\n")

    planned = result.get("planned_commands", [])
    if planned:
        lines.append("\n## Planned Commands (Dry-Run)\n")
        for cmd in planned:
            lines.append(f"```bash\n{cmd}\n```\n")

    if command_log:
        lines.append("\n## Command Log\n")
        for entry in command_log:
            marker = "✅" if "OK" in entry else "❌"
            lines.append(f"{marker} `{entry}`\n")

    safety = result.get("safety_checks", {})
    lines.extend([
        "\n## Safety Guarantees\n",
        f"- no push: {'✅' if safety.get('no_push') else '❌'}\n",
        f"- no gh pr create: {'✅' if safety.get('no_pr_create') else '❌'}\n",
        f"- no merge: {'✅' if safety.get('no_merge') else '❌'}\n",
        f"- no Claude invocation: {'✅' if safety.get('no_claude_invocation') else '❌'}\n",
        f"- no package install: {'✅' if safety.get('no_package_install') else '❌'}\n",
        f"- no shell=True: {'✅' if safety.get('no_shell_true') else '❌'}\n",
        f"- apply_allowed only with --allow-real-apply: {'✅' if safety.get('apply_allowed_only_with_flag') else '❌'}\n",
    ])

    lines.extend([
        "\n---\n",
        "## 🛡️ Safety Statement\n",
        "This tool did NOT push, did NOT create PRs, did NOT merge, and did NOT invoke Claude.\n",
        "All apply operations are local to the target repository.\n",
        "Human must push and open PR manually after reviewing the applied branch.\n",
        "\n---\n",
        f"*apply_allowed: {result.get('apply_allowed', False)}*\n",
    ])

    output_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    result_json_path = Path(args.result_json).resolve()
    diff_patch_path = Path(args.diff_patch).resolve()
    target_repo = Path(args.target_repo).resolve()
    branch_name = args.branch_name
    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md) if args.output_md else None
    require_apply_ready = args.require_apply_ready
    apply_readiness_json_path = Path(args.apply_readiness_json) if args.apply_readiness_json else None
    allow_real_apply = args.allow_real_apply
    expected_base_sha = args.expected_base_sha
    dry_run = args.dry_run

    # ── Output path safety check ─────────────────────────────────────────────
    if _path_inside_repo(output_json_path, target_repo):
        status = STATE_OUTPUT_INSIDE_REPO
        result_dict = {
            "output_path": str(output_json_path),
            "target_repo": str(target_repo),
            "inside_repo": True,
        }
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
        output_json_path.write_text(
            json.dumps({"status": status, **result_dict}, indent=2),
            encoding="utf-8",
        )
        if output_md_path:
            output_md_path.parent.mkdir(parents=True, exist_ok=True)
            output_md_path.write_text(
                f"# Temp Worktree Apply to Branch\n\n**Status:** `{status}`\n\nOutput path is inside the repo.\n",
                encoding="utf-8",
            )
        print(f"HOLD: {status} — output path {output_json_path} is inside repo", file=sys.stderr)
        return 0

    if output_md_path and _path_inside_repo(output_md_path, target_repo):
        status = STATE_OUTPUT_INSIDE_REPO
        result_dict = {
            "output_path": str(output_md_path),
            "target_repo": str(target_repo),
            "inside_repo": True,
        }
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
        output_json_path.write_text(
            json.dumps({"status": status, **result_dict}, indent=2),
            encoding="utf-8",
        )
        output_md_path.parent.mkdir(parents=True, exist_ok=True)
        output_md_path.write_text(
            f"# Temp Worktree Apply to Branch\n\n**Status:** `{status}`\n\nOutput path is inside the repo.\n",
            encoding="utf-8",
        )
        print(f"HOLD: {status} — output MD path {output_md_path} is inside repo", file=sys.stderr)
        return 0

    # ── Run apply logic ─────────────────────────────────────────────────────
    try:
        status, result_dict = apply_patch_to_branch(
            result_json_path,
            diff_patch_path,
            target_repo,
            branch_name,
            require_apply_ready,
            apply_readiness_json_path,
            allow_real_apply,
            expected_base_sha,
            dry_run,
        )
    except Exception as e:
        status = STATE_INTERNAL_ERROR
        result_dict = {
            "error": str(e),
            "traceback": str(sys.exc_info()),
        }

    command_log_raw = result_dict.get("command_log", [])
    command_log: list[str] = command_log_raw if isinstance(command_log_raw, list) else [str(command_log_raw)]
    apply_allowed_bool: bool = bool(result_dict.get("apply_allowed", False))

    # ── Write outputs ────────────────────────────────────────────────────────
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_output(
        output_json_path, status, result_dict,
        result_json_path, diff_patch_path, target_repo,
        branch_name, command_log, apply_allowed_bool,
    )

    if output_md_path:
        output_md_path.parent.mkdir(parents=True, exist_ok=True)
        write_md_output(
            output_md_path, status, result_dict,
            result_json_path, diff_patch_path, target_repo,
            branch_name, command_log,
        )

    print(f"Status: {status}")
    print(f"JSON: {output_json_path}")
    if output_md_path:
        print(f"Markdown: {output_md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())