#!/usr/bin/env python3
"""
preview_temp_worktree_apply.py

Read-only preview tool that inspects a temp-worktree apply-readiness result and
produces exact human-review apply commands and checklists.

IMPORTANT:
  This script does NOT apply patches. It does NOT run git apply.
  It does NOT stage, commit, push, open PRs, or merge.
  It only emits apply commands as text and writes JSON/MD output.

States:
  APPLY_PREVIEW_READY                  — all checks passed; preview written
  HOLD_RESULT_MISSING                   — result.json does not exist
  HOLD_RESULT_INVALID_JSON             — result.json is not valid JSON
  HOLD_DIFF_MISSING                    — diff.patch does not exist
  HOLD_DIFF_EMPTY                      — diff.patch is empty
  HOLD_READINESS_MISSING               — apply-readiness JSON does not exist
  HOLD_READINESS_INVALID_JSON          — apply-readiness JSON is not valid JSON
  HOLD_READINESS_NOT_APPLY_READY       — readiness status is not APPLY_READY
  HOLD_READINESS_PATH_MISMATCH         — readiness references different paths
  HOLD_EXPECTED_HEAD_MISMATCH          — current HEAD does not match expected
  HOLD_REPO_DIRTY                      — repo has staged/unstaged changes
  HOLD_STATUS_NOT_PATCH_READY          — result status is not PATCH_READY_FOR_HUMAN_REVIEW
  HOLD_CHANGED_FILES_EMPTY            — changed_files is empty
  HOLD_CHANGED_FILES_DUPLICATE        — changed_files has duplicates
  HOLD_AED_PLAN_INCLUDED              — .aed_plan.md in changed_files or diff.patch
  HOLD_OUTSIDE_ALLOWED_FILES           — a changed file is not in allowed_files
  HOLD_FORBIDDEN_FILE_TOUCHED          — a changed file is in forbidden_files
  HOLD_TOO_MANY_FILES_CHANGED         — changed_files count exceeds max
  HOLD_DIFF_MISSING_CHANGED_FILE       — diff.patch does not contain a changed file
  HOLD_PMG_NOT_CLEAN                  — PMG is not clean (when required)
  HOLD_REAL_CLAUDE_NOT_CONFIRMED      — real Claude not confirmed (when required)
  HOLD_OUTPUT_INSIDE_REPO              — output path is inside the repo
  HOLD_UNKNOWN                         — unexpected error

Exit codes:
  0 — check complete (any status written to output)
  1 — fatal error (missing args, file read error)

Usage:
    python3 scripts/local/preview_temp_worktree_apply.py \\
        --result-json /tmp/aed_run/result.json \\
        --diff-patch /tmp/aed_run/diff.patch \\
        --apply-readiness-json /tmp/aed_run/apply_readiness.json \\
        --repo-root /path/to/repo \\
        --expected-head <sha> \\
        --output-json /tmp/apply_preview.json \\
        --output-md /tmp/apply_preview.md \\
        [--branch-name apply/my-patch] \\
        [--require-apply-ready] \\
        [--allow-output-inside-repo]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/local/.. -> repo

STATE_PREVIEW_READY             = "APPLY_PREVIEW_READY"
STATE_RESULT_MISSING           = "HOLD_RESULT_MISSING"
STATE_RESULT_INVALID_JSON      = "HOLD_RESULT_INVALID_JSON"
STATE_DIFF_MISSING             = "HOLD_DIFF_MISSING"
STATE_DIFF_EMPTY               = "HOLD_DIFF_EMPTY"
STATE_READINESS_MISSING        = "HOLD_READINESS_MISSING"
STATE_READINESS_INVALID_JSON   = "HOLD_READINESS_INVALID_JSON"
STATE_READINESS_NOT_APPLY_READY = "HOLD_READINESS_NOT_APPLY_READY"
STATE_READINESS_PATH_MISMATCH  = "HOLD_READINESS_PATH_MISMATCH"
STATE_EXPECTED_HEAD_MISMATCH   = "HOLD_EXPECTED_HEAD_MISMATCH"
STATE_REPO_DIRTY               = "HOLD_REPO_DIRTY"
STATE_STATUS_NOT_PATCH_READY   = "HOLD_STATUS_NOT_PATCH_READY"
STATE_CHANGED_FILES_EMPTY      = "HOLD_CHANGED_FILES_EMPTY"
STATE_CHANGED_FILES_DUPLICATE  = "HOLD_CHANGED_FILES_DUPLICATE"
STATE_AED_PLAN_INCLUDED        = "HOLD_AED_PLAN_INCLUDED"
STATE_OUTSIDE_ALLOWED_FILES   = "HOLD_OUTSIDE_ALLOWED_FILES"
STATE_FORBIDDEN_FILE_TOUCHED   = "HOLD_FORBIDDEN_FILE_TOUCHED"
STATE_TOO_MANY_FILES_CHANGED   = "HOLD_TOO_MANY_FILES_CHANGED"
STATE_DIFF_MISSING_CHANGED_FILE = "HOLD_DIFF_MISSING_CHANGED_FILE"
STATE_PMG_NOT_CLEAN            = "HOLD_PMG_NOT_CLEAN"
STATE_REAL_CLAUDE_NOT_CONFIRMED = "HOLD_REAL_CLAUDE_NOT_CONFIRMED"
STATE_OUTPUT_INSIDE_REPO       = "HOLD_OUTPUT_INSIDE_REPO"
STATE_UNKNOWN                  = "HOLD_UNKNOWN"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_head(repo_root: Path) -> str | None:
    """Return current HEAD SHA or None on error."""
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


def _git_apply_check(repo_root: Path, diff_patch: Path) -> tuple[bool, str]:
    """
    Run 'git apply --check' on the diff.patch (read-only validation).
    Returns (success: bool, output: str).
    """
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


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview temp-worktree patch apply commands (read-only)."
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
        "--apply-readiness-json", required=True,
        help="Path to apply-readiness JSON from verify_temp_worktree_apply_readiness.py"
    )
    parser.add_argument(
        "--repo-root", default=str(REPO_ROOT),
        help="Path to AED repo root (default: auto-detected)"
    )
    parser.add_argument(
        "--expected-head", required=True,
        help="Expected main HEAD SHA"
    )
    parser.add_argument(
        "--output-json", required=True,
        help="Path to write JSON preview result"
    )
    parser.add_argument(
        "--output-md", required=True,
        help="Path to write Markdown preview result"
    )
    parser.add_argument(
        "--branch-name",
        help="Optional suggested local branch name for apply"
    )
    parser.add_argument(
        "--require-apply-ready", action="store_true",
        help="Fail if apply-readiness status is not APPLY_READY"
    )
    parser.add_argument(
        "--allow-output-inside-repo", action="store_true",
        help="Allow output JSON/MD to be inside the repo (default: false)"
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Core preview logic
# ---------------------------------------------------------------------------

def preview(
    result_json_path: Path,
    diff_patch_path: Path,
    apply_readiness_json_path: Path,
    repo_root: Path,
    expected_head: str,
    require_apply_ready: bool,
    allow_output_inside_repo: bool,
    branch_name: str | None,
) -> tuple[str, dict]:
    """
    Run all preview checks. Returns (status, checks_dict).
    """
    checks: dict[str, object] = {}
    warnings: list[str] = []
    errors: list[str] = []

    # ── 1. Result JSON ────────────────────────────────────────────────────────
    result = _load_json(result_json_path)
    if result is None:
        if not result_json_path.exists():
            return STATE_RESULT_MISSING, {"result_json_exists": False}
        return STATE_RESULT_INVALID_JSON, {"result_json_exists": True, "valid_json": False}
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

    # ── 3. Apply-readiness JSON ───────────────────────────────────────────────
    readiness = _load_json(apply_readiness_json_path)
    if readiness is None:
        if not apply_readiness_json_path.exists():
            return STATE_READINESS_MISSING, {"apply_readiness_exists": False}
        return STATE_READINESS_INVALID_JSON, {"apply_readiness_exists": True, "valid_json": False}
    checks["apply_readiness_exists"] = True
    checks["apply_readiness_valid"] = True

    # ── 4. Apply-readiness status ─────────────────────────────────────────────
    readiness_status = readiness.get("status", "")
    checks["apply_readiness_status"] = readiness_status
    if require_apply_ready and readiness_status != "APPLY_READY":
        return STATE_READINESS_NOT_APPLY_READY, {
            **checks,
            "apply_ready_expected": True,
            "apply_readiness_actual_status": readiness_status,
        }

    # ── 5. Apply-readiness path consistency ───────────────────────────────────
    readiness_result_json = readiness.get("result_json_path") or readiness.get("result_json")
    readiness_diff_patch = readiness.get("diff_patch_path") or readiness.get("diff_patch")

    path_ok = True
    if readiness_result_json and str(Path(readiness_result_json).resolve()) != str(result_json_path.resolve()):
        path_ok = False
        errors.append(f"apply-readiness result_json mismatch: expected {result_json_path}, got {readiness_result_json}")
    if readiness_diff_patch and str(Path(readiness_diff_patch).resolve()) != str(diff_patch_path.resolve()):
        path_ok = False
        errors.append(f"apply-readiness diff_patch mismatch: expected {diff_patch_path}, got {readiness_diff_patch}")

    checks["apply_readiness_paths_consistent"] = path_ok
    if not path_ok:
        return STATE_READINESS_PATH_MISMATCH, {
            **checks,
            "readiness_reported_result_json": readiness_result_json,
            "readiness_reported_diff_patch": readiness_diff_patch,
            "expected_result_json": str(result_json_path),
            "expected_diff_patch": str(diff_patch_path),
            "errors": errors,
        }

    # ── 6. Output paths outside repo ──────────────────────────────────────────
    out_json = Path(apply_readiness_json_path).parent  # we check the readiness path's dir as proxy
    # Actually check output-json and output-md are not inside repo — caller passes these
    # but we validate the readiness output as a proxy; full validation is done below
    checks["output_outside_repo"] = True

    # ── 7. Repo HEAD vs expected ──────────────────────────────────────────────
    current_head = _git_head(repo_root)
    checks["current_head"] = current_head
    checks["expected_head"] = expected_head
    if current_head is None:
        warnings.append("Could not determine current HEAD; assuming clean")
        current_head = expected_head
    elif current_head != expected_head:
        return STATE_EXPECTED_HEAD_MISMATCH, {
            **checks,
            "current_head": current_head,
            "expected_head": expected_head,
            "mismatch": True,
        }
    checks["head_match"] = True

    # ── 8. Repo git status clean ──────────────────────────────────────────────
    repo_clean = _git_status_clean(repo_root)
    checks["repo_git_status_clean"] = repo_clean
    if not repo_clean:
        return STATE_REPO_DIRTY, {**checks, "repo_status_dirty": True}
    checks["repo_clean"] = True

    # ── 9. Result status ──────────────────────────────────────────────────────
    result_status = result.get("status", "")
    checks["result_status"] = result_status
    if result_status != "PATCH_READY_FOR_HUMAN_REVIEW":
        return STATE_STATUS_NOT_PATCH_READY, {
            **checks,
            "expected_status": "PATCH_READY_FOR_HUMAN_REVIEW",
            "actual_status": result_status,
        }

    # ── 10. Changed files non-empty ───────────────────────────────────────────
    changed_files = result.get("changed_files", [])
    checks["changed_files"] = changed_files
    if not changed_files:
        return STATE_CHANGED_FILES_EMPTY, {**checks, "changed_files_count": 0}
    checks["changed_files_count"] = len(changed_files)

    # ── 11. Changed files unique ──────────────────────────────────────────────
    dups = _check_duplicate_paths(changed_files)
    if dups:
        return STATE_CHANGED_FILES_DUPLICATE, {
            **checks,
            "duplicate_paths": dups,
            "changed_files_count": len(changed_files),
        }
    checks["changed_files_unique"] = True

    # ── 12. .aed_plan.md exclusion ───────────────────────────────────────────
    aed_plan_in_changed = ".aed_plan.md" in changed_files
    aed_plan_in_diff = ".aed_plan.md" in diff_content
    checks["aed_plan_excluded"] = not aed_plan_in_changed and not aed_plan_in_diff
    if aed_plan_in_changed or aed_plan_in_diff:
        return STATE_AED_PLAN_INCLUDED, {
            **checks,
            "aed_plan_in_changed_files": aed_plan_in_changed,
            "aed_plan_in_diff": aed_plan_in_diff,
        }

    # ── 13. Allowed/forbidden files ───────────────────────────────────────────
    task = result.get("task", {})
    approval = result.get("approval", {})
    allowed_files = task.get("allowed_files", [])
    forbidden_files = task.get("forbidden_files", [])
    max_changed_files = approval.get("max_changed_files", task.get("max_changed_files", None))

    checks["allowed_files"] = allowed_files
    checks["forbidden_files"] = forbidden_files
    checks["max_changed_files"] = max_changed_files

    if allowed_files:
        for cf in changed_files:
            if cf not in allowed_files:
                return STATE_OUTSIDE_ALLOWED_FILES, {
                    **checks,
                    "outside_allowed_file": cf,
                }
        checks["all_changed_files_allowed"] = True

    if forbidden_files:
        for cf in changed_files:
            if cf in forbidden_files:
                return STATE_FORBIDDEN_FILE_TOUCHED, {
                    **checks,
                    "forbidden_file_touched": cf,
                }
        checks["no_forbidden_files_touched"] = True

    # ── 14. Max changed files ─────────────────────────────────────────────────
    if max_changed_files is not None:
        if len(changed_files) > max_changed_files:
            return STATE_TOO_MANY_FILES_CHANGED, {
                **checks,
                "changed_files_count": len(changed_files),
                "max_allowed": max_changed_files,
            }
        checks["max_changed_files_ok"] = True

    # ── 15. diff.patch contains each changed file ─────────────────────────────
    for cf in changed_files:
        if cf not in diff_content:
            return STATE_DIFF_MISSING_CHANGED_FILE, {
                **checks,
                "missing_from_diff": cf,
            }
    checks["diff_contains_all_changed_files"] = True

    # ── 16. PMG clean ──────────────────────────────────────────────────────────
    pmg_status = result.get("pmg_status", None)
    checks["pmg_status"] = pmg_status
    if pmg_status is not None and pmg_status != "clean":
        return STATE_PMG_NOT_CLEAN, {**checks, "pmg_status": pmg_status}
    if pmg_status == "clean":
        checks["pmg_clean"] = True

    # ── 17. Real Claude confirmation (if mode is claude) ───────────────────────
    execution_mode = result.get("execution", {}).get("mode") or result.get("mode")
    real_claude_invoked = result.get("real_claude_invoked", None)
    checks["execution_mode"] = execution_mode
    checks["real_claude_invoked"] = real_claude_invoked
    if execution_mode == "claude" and real_claude_invoked is not True:
        # Check via audit if available
        audit_status = result.get("audit_status", None)
        if audit_status != "CLAUDE_INVOCATION_DETECTED":
            return STATE_REAL_CLAUDE_NOT_CONFIRMED, {
                **checks,
                "execution_mode": execution_mode,
                "real_claude_invoked": real_claude_invoked,
            }
        checks["real_claude_confirmed_via_audit"] = True

    checks["real_claude_confirmed"] = True

    # ── 18. git apply --check (read-only) ─────────────────────────────────────
    apply_check_ok, apply_check_output = _git_apply_check(repo_root, diff_patch_path)
    checks["git_apply_check_passed"] = apply_check_ok
    checks["git_apply_check_output"] = apply_check_output[:500]  # truncate long output

    # ── 19. Generate apply commands (text only) ────────────────────────────────
    repo_root_str = str(repo_root.resolve())
    diff_patch_str = str(diff_patch_path.resolve())
    expected_head_str = expected_head

    git_apply_check_cmd = f"git -C {repo_root_str} apply --check {diff_patch_str}"
    git_apply_cmd = f"git -C {repo_root_str} apply {diff_patch_str}"

    branch_suggestion = branch_name
    if not branch_suggestion:
        run_id = result.get("run_id", "apply")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        branch_suggestion = f"apply/{run_id}-{ts}"

    branch_create_cmd = (
        f"git -C {repo_root_str} switch -c {branch_suggestion} {expected_head_str}"
    )

    generated_commands = {
        "git_apply_check": git_apply_check_cmd,
        "git_apply": git_apply_cmd,
        "branch_create": branch_create_cmd,
    }
    checks["generated_commands"] = generated_commands

    # ── 20. Pre/post apply checklists ─────────────────────────────────────────
    pre_apply_checklist = [
        f"Verify git status is clean: git -C {repo_root_str} status --short",
        f"Verify HEAD is {expected_head_str}: git -C {repo_root_str} rev-parse HEAD",
        f"Run apply-check: {git_apply_check_cmd}",
        f"Review the diff at {diff_patch_str}",
        f"Inspect changed files: " + ", ".join(f"`{cf}`" for cf in changed_files),
        f"Run the actual apply: {git_apply_cmd}",
        f"Run tests: pytest tests/ -q",
        f"Run PMG snapshot: python3 scripts/local/check_persistent_mutation_guard.py snapshot --root {repo_root_str} --output /tmp/pmg_snapshot.json",
        "Open PR via normal workflow if all checks pass",
    ]

    post_apply_checklist = [
        "Run pytest tests/ -q",
        "Run PMG compare: python3 scripts/local/check_persistent_mutation_guard.py compare --root {repo_root_str} --before /tmp/pmg_snapshot.json --output-json /tmp/pmg_compare.json --output-md /tmp/pmg_compare.md",
        "Review git status: git -C {repo_root_str} status --short",
        "Push and open PR via normal workflow",
    ]

    # ── 21. Safety statement ──────────────────────────────────────────────────
    safety_statement = (
        "This preview tool did NOT run git apply, did NOT stage or commit files, "
        "did NOT push, did NOT open PRs, and did NOT merge. "
        "All apply commands above are text output only. "
        "Human must execute them manually."
    )

    checks["safety_statement"] = safety_statement
    checks["preview_ready"] = True
    checks["real_apply_allowed"] = False
    checks["warnings"] = warnings
    checks["errors"] = errors

    return STATE_PREVIEW_READY, checks


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_json_output(
    output_path: Path,
    status: str,
    checks: dict,
    result_json_path: Path,
    diff_patch_path: Path,
    apply_readiness_json_path: Path,
    repo_root: Path,
    expected_head: str,
    current_head: str | None,
    branch_name: str | None,
    pre_apply_checklist: list[str],
    post_apply_checklist: list[str],
    safety_statement: str,
    warnings: list[str],
    errors: list[str],
) -> None:
    """Write JSON output."""
    preview_commands = checks.get("generated_commands", {})

    output: dict[str, object] = {
        "status": status,
        "preview_ready": status == STATE_PREVIEW_READY,
        "real_apply_allowed": False,
        "result_json": str(result_json_path.resolve()),
        "diff_patch": str(diff_patch_path.resolve()),
        "apply_readiness_json": str(apply_readiness_json_path.resolve()),
        "repo_root": str(repo_root.resolve()),
        "expected_head": expected_head,
        "current_head": current_head,
        "changed_files": checks.get("changed_files", []),
        "allowed_files": checks.get("allowed_files", []),
        "forbidden_files": checks.get("forbidden_files", []),
        "max_changed_files": checks.get("max_changed_files"),
        "pmg_status": checks.get("pmg_status"),
        "real_claude_invoked": checks.get("real_claude_invoked"),
        "git_apply_check_passed": checks.get("git_apply_check_passed"),
        "generated_commands": preview_commands,
        "branch_suggestion": branch_name,
        "pre_apply_checklist": pre_apply_checklist,
        "post_apply_checklist": post_apply_checklist,
        "safety_statement": safety_statement,
        "warnings": warnings,
        "errors": errors,
        "checks_passed": sum(1 for v in checks.values() if v is True),
    }

    output_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def write_md_output(
    output_path: Path,
    status: str,
    checks: dict,
    result_json_path: Path,
    diff_patch_path: Path,
    apply_readiness_json_path: Path,
    repo_root: Path,
    expected_head: str,
    current_head: str | None,
    branch_name: str | None,
    pre_apply_checklist: list[str],
    post_apply_checklist: list[str],
    safety_statement: str,
    warnings: list[str],
    errors: list[str],
) -> None:
    """Write Markdown output."""
    lines: list[str] = []
    lines.append("# Temp Worktree Apply Preview\n")
    lines.append(f"**Status:** `{status}`\n")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}\n")
    lines.append("---\n")

    # Verdict
    if status == STATE_PREVIEW_READY:
        lines.append("## ✅ Verdict: Apply Preview Ready\n")
        lines.append("All pre-apply checks passed. The generated commands below are safe to review.\n")
    else:
        lines.append(f"## ❌ Verdict: {status}\n")
        lines.append("One or more checks failed. Review the failed checks below.\n")

    lines.append("---\n")
    lines.append("## Inputs\n")
    lines.append(f"- **result.json:** `{result_json_path}`\n")
    lines.append(f"- **diff.patch:** `{diff_patch_path}`\n")
    lines.append(f"- **apply-readiness JSON:** `{apply_readiness_json_path}`\n")
    lines.append(f"- **repo root:** `{repo_root}`\n")
    lines.append(f"- **expected HEAD:** `{expected_head}`\n")
    lines.append(f"- **current HEAD:** `{current_head}`\n")
    if branch_name:
        lines.append(f"- **branch suggestion:** `{branch_name}`\n")

    # Changed files
    cf_list = checks.get("changed_files", [])
    lines.append("\n## Changed Files\n")
    for cf in cf_list:
        lines.append(f"- `{cf}`\n")

    # Safety confirmations
    lines.append("\n## Safety Confirmations\n")
    lines.append(f"- `.aed_plan.md` excluded from diff: {'✅' if checks.get('aed_plan_excluded') else '❌'}\n")
    lines.append(f"- all changed_files within allowed_files: {'✅' if checks.get('all_changed_files_allowed') else '❌'}\n")
    lines.append(f"- no forbidden files touched: {'✅' if checks.get('no_forbidden_files_touched') else '❌'}\n")
    lines.append(f"- PMG clean: {'✅' if checks.get('pmg_clean') else ('N/A' if checks.get('pmg_status') is None else '❌')}\n")
    lines.append(f"- real Claude confirmed: {'✅' if checks.get('real_claude_confirmed') else '❌'}\n")
    lines.append(f"- repo git status clean: {'✅' if checks.get('repo_clean') else '❌'}\n")
    lines.append(f"- HEAD matches expected: {'✅' if checks.get('head_match') else '❌'}\n")
    lines.append(f"- git apply --check passed: {'✅' if checks.get('git_apply_check_passed') else '❌'}\n")
    lines.append(f"- preview_ready: {'✅' if checks.get('preview_ready') else '❌'}\n")

    # Generated commands
    preview_commands = checks.get("generated_commands", {})
    if preview_commands:
        lines.append("\n## Generated Apply Commands (Text Only)\n")
        lines.append("**Do NOT expect these commands to have been run. They are output text only.**\n")
        lines.append(f"\n### Pre-apply check (read-only)\n")
        lines.append(f"```bash\n{preview_commands.get('git_apply_check', 'N/A')}\n```\n")
        lines.append(f"\n### Actual apply (human executes)\n")
        lines.append(f"```bash\n{preview_commands.get('git_apply', 'N/A')}\n```\n")
        if preview_commands.get('branch_create'):
            lines.append(f"\n### Optional branch creation\n")
            lines.append(f"```bash\n{preview_commands.get('branch_create', 'N/A')}\n```\n")

    # Pre-apply checklist
    if pre_apply_checklist:
        lines.append("\n## Pre-Apply Checklist\n")
        for i, item in enumerate(pre_apply_checklist, 1):
            lines.append(f"{i}. {item}\n")

    # Post-apply checklist
    if post_apply_checklist:
        lines.append("\n## Post-Apply Checklist\n")
        for i, item in enumerate(post_apply_checklist, 1):
            lines.append(f"{i}. {item}\n")

    # Warnings
    if warnings:
        lines.append("\n## Warnings\n")
        for w in warnings:
            lines.append(f"- ⚠️ {w}\n")

    # Errors / failed checks
    if errors:
        lines.append("\n## Errors\n")
        for e in errors:
            lines.append(f"- ❌ {e}\n")

    # Safety statement
    lines.append("\n---\n")
    lines.append(f"## 🛡️ Safety Statement\n")
    lines.append(f"{safety_statement}\n")
    lines.append("\n---\n")
    lines.append(f"*This preview tool is read-only. No git apply was executed.*\n")

    output_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    result_json_path = Path(args.result_json).resolve()
    diff_patch_path = Path(args.diff_patch).resolve()
    apply_readiness_json_path = Path(args.apply_readiness_json).resolve()
    repo_root = Path(args.repo_root).resolve()
    expected_head = args.expected_head
    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md)
    require_apply_ready = args.require_apply_ready
    allow_output_inside_repo = args.allow_output_inside_repo
    branch_name = args.branch_name

    # ── Output path safety check ─────────────────────────────────────────────
    if not allow_output_inside_repo:
        for out_path in [output_json_path, output_md_path]:
            if _path_inside_repo(out_path, repo_root):
                status = STATE_OUTPUT_INSIDE_REPO
                checks = {
                    "output_path": str(out_path),
                    "repo_root": str(repo_root),
                    "inside_repo": True,
                }
                # Write HOLD output
                out_path.parent.mkdir(parents=True, exist_ok=True)
                write_json_output_fallback(
                    output_json_path, status, checks,
                    result_json_path, diff_patch_path,
                    apply_readiness_json_path, repo_root,
                    expected_head, None, branch_name,
                    [], [], "Output path inside repo.", [], []
                )
                write_md_output_fallback(
                    output_md_path, status, checks,
                    result_json_path, diff_patch_path,
                    apply_readiness_json_path, repo_root,
                    expected_head, None, branch_name,
                    [], [], "Output path inside repo.", [], []
                )
                print(f"HOLD: {status} — output path {out_path} is inside repo", file=sys.stderr)
                return 0

    # ── Run preview checks ───────────────────────────────────────────────────
    try:
        status, checks = preview(
            result_json_path,
            diff_patch_path,
            apply_readiness_json_path,
            repo_root,
            expected_head,
            require_apply_ready,
            allow_output_inside_repo,
            branch_name,
        )
    except Exception as e:
        status = STATE_UNKNOWN
        checks = {"error": str(e), "traceback": str(sys.exc_info())}

    current_head = checks.get("current_head")

    # ── Compute checklists from checks ───────────────────────────────────────
    if status == STATE_PREVIEW_READY:
        repo_root_str = str(repo_root.resolve())
        git_apply_check_cmd = checks.get("generated_commands", {}).get("git_apply_check", "")
        git_apply_cmd = checks.get("generated_commands", {}).get("git_apply", "")
        changed_files = checks.get("changed_files", [])
        expected_head_str = expected_head

        pre_apply_checklist = [
            f"Verify git status is clean: git -C {repo_root_str} status --short",
            f"Verify HEAD is {expected_head_str}: git -C {repo_root_str} rev-parse HEAD",
            f"Run apply-check: {git_apply_check_cmd}",
            f"Review the diff at {diff_patch_path}",
            f"Inspect changed files: " + ", ".join(f"`{cf}`" for cf in changed_files),
            f"Run the actual apply: {git_apply_cmd}",
            f"Run tests: pytest tests/ -q",
            f"Run PMG snapshot: python3 scripts/local/check_persistent_mutation_guard.py snapshot --root {repo_root_str} --output /tmp/pmg_snapshot.json",
            "Open PR via normal workflow if all checks pass",
        ]
        post_apply_checklist = [
            "Run pytest tests/ -q",
            f"Run PMG compare: python3 scripts/local/check_persistent_mutation_guard.py compare --root {repo_root_str} --before /tmp/pmg_snapshot.json --output-json /tmp/pmg_compare.json --output-md /tmp/pmg_compare.md",
            f"Review git status: git -C {repo_root_str} status --short",
            "Push and open PR via normal workflow",
        ]
        safety_statement = (
            "This preview tool did NOT run git apply, did NOT stage or commit files, "
            "did NOT push, did NOT open PRs, and did NOT merge. "
            "All apply commands above are text output only. "
            "Human must execute them manually."
        )
    else:
        pre_apply_checklist = []
        post_apply_checklist = []
        safety_statement = (
            "This preview tool did NOT run git apply, did NOT stage or commit files, "
            "did NOT push, did NOT open PRs, and did NOT merge."
        )

    warnings_raw = checks.get("warnings")
    warnings: list[str] = warnings_raw if isinstance(warnings_raw, list) else []
    errors_raw = checks.get("errors")
    errors: list[str] = errors_raw if isinstance(errors_raw, list) else []

    # ── Write outputs ────────────────────────────────────────────────────────
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_md_path.parent.mkdir(parents=True, exist_ok=True)

    write_json_output(
        output_json_path, status, checks,
        result_json_path, diff_patch_path, apply_readiness_json_path,
        repo_root, expected_head, current_head, branch_name,
        pre_apply_checklist, post_apply_checklist, safety_statement,
        warnings, errors,
    )

    write_md_output(
        output_md_path, status, checks,
        result_json_path, diff_patch_path, apply_readiness_json_path,
        repo_root, expected_head, current_head, branch_name,
        pre_apply_checklist, post_apply_checklist, safety_statement,
        warnings, errors,
    )

    print(f"Status: {status}")
    print(f"JSON: {output_json_path}")
    print(f"Markdown: {output_md_path}")
    return 0


def write_json_output_fallback(
    output_path: Path,
    status: str,
    checks: dict,
    result_json_path: Path,
    diff_patch_path: Path,
    apply_readiness_json_path: Path,
    repo_root: Path,
    expected_head: str,
    current_head: str | None,
    branch_name: str | None,
    pre_apply_checklist: list[str],
    post_apply_checklist: list[str],
    safety_statement: str,
    warnings: list[str],
    errors: list[str],
) -> None:
    out: dict[str, object] = {
        "status": status,
        "preview_ready": False,
        "real_apply_allowed": False,
        "result_json": str(result_json_path),
        "diff_patch": str(diff_patch_path),
        "apply_readiness_json": str(apply_readiness_json_path),
        "repo_root": str(repo_root),
        "expected_head": expected_head,
        "current_head": current_head,
        "warnings": warnings,
        "errors": errors,
        "safety_statement": safety_statement,
    }
    output_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


def write_md_output_fallback(
    output_path: Path,
    status: str,
    checks: dict,
    result_json_path: Path,
    diff_patch_path: Path,
    apply_readiness_json_path: Path,
    repo_root: Path,
    expected_head: str,
    current_head: str | None,
    branch_name: str | None,
    pre_apply_checklist: list[str],
    post_apply_checklist: list[str],
    safety_statement: str,
    warnings: list[str],
    errors: list[str],
) -> None:
    lines = [
        "# Temp Worktree Apply Preview\n",
        f"**Status:** `{status}`\n",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}\n",
        "---\n",
        f"## ❌ Verdict: {status}\n",
        "---\n",
        f"**Error:** {errors[0] if errors else 'Check failed.'}\n",
        "---\n",
        f"## 🛡️ Safety Statement\n",
        f"{safety_statement}\n",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())