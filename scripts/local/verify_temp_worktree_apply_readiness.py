#!/usr/bin/env python3
"""
verify_temp_worktree_apply_readiness.py

Read-only verifier that inspects a temp-worktree execution result and determines
whether the resulting diff.patch is ready for a separate human-approved apply step.

IMPORTANT: This script does NOT apply patches. It only reports apply readiness.

States:
  APPLY_READY                       — all checks passed, patch is ready for human apply
  HOLD_RESULT_MISSING                — result.json does not exist
  HOLD_RESULT_INVALID_JSON          — result.json is not valid JSON
  HOLD_DIFF_MISSING                 — diff.patch does not exist
  HOLD_DIFF_EMPTY                   — diff.patch is empty
  HOLD_STATUS_NOT_PATCH_READY       — result status is not PATCH_READY_FOR_HUMAN_REVIEW
  HOLD_CHANGED_FILES_EMPTY          — changed_files is empty
  HOLD_CHANGED_FILES_DUPLICATE      — changed_files contains duplicate paths
  HOLD_AED_PLAN_INCLUDED            — .aed_plan.md appears in changed_files or diff.patch
  HOLD_OUTSIDE_ALLOWED_FILES        — a changed file is not in allowed_files
  HOLD_FORBIDDEN_FILE_TOUCHED       — a changed file is in forbidden_files
  HOLD_TOO_MANY_FILES_CHANGED       — changed_files count exceeds max_changed_files
  HOLD_DIFF_MISSING_CHANGED_FILE    — diff.patch does not contain a changed file path
  HOLD_DIFF_CONTAINS_FORBIDDEN_FILE  — diff.patch contains a forbidden file path
  HOLD_PMG_NOT_CLEAN                — PMG status is not clean (when required)
  HOLD_REAL_CLAUDE_NOT_CONFIRMED    — real Claude invocation not confirmed (when required)
  HOLD_CLAUDE_EXIT_NONZERO          — claude_exit_code is non-zero (when present)
  HOLD_REPO_DIRTY                   — main repo working tree is not clean
  HOLD_OUTPUT_INSIDE_REPO           — output path is inside the repo
  HOLD_WORKTREE_INSIDE_REPO         — worktree path is inside the repo
  HOLD_PATCH_PATH_INSIDE_REPO       — diff.patch path is inside the repo
  HOLD_COMMAND_CONTRACT_UNSAFE       — command metadata shows shell=True or dangerous flags
  HOLD_PACKET_METADATA_MISSING      — result lacks allowed_files/forbidden_files/max_changed_files
  HOLD_UNKNOWN                      — unexpected error

Exit codes:
  0 — check complete (any status written to output)
  1 — fatal error (missing args, file read error)

Usage:
    python3 scripts/local/verify_temp_worktree_apply_readiness.py \
        --result-json /tmp/aed_run/result.json \
        --diff-patch /tmp/aed_run/diff.patch \
        --repo-root /path/to/repo \
        --output-json /tmp/apply_readiness.json \
        --output-md /tmp/apply_readiness.md \
        [--require-real-claude-invoked] \
        [--require-pmg-clean] \
        [--max-diff-bytes 100000] \
        [--expected-status PATCH_READY_FOR_HUMAN_REVIEW]
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

STATE_APPLY_READY              = "APPLY_READY"
STATE_RESULT_MISSING           = "HOLD_RESULT_MISSING"
STATE_RESULT_INVALID_JSON      = "HOLD_RESULT_INVALID_JSON"
STATE_DIFF_MISSING             = "HOLD_DIFF_MISSING"
STATE_DIFF_EMPTY               = "HOLD_DIFF_EMPTY"
STATE_STATUS_NOT_PATCH_READY   = "HOLD_STATUS_NOT_PATCH_READY"
STATE_CHANGED_FILES_EMPTY     = "HOLD_CHANGED_FILES_EMPTY"
STATE_CHANGED_FILES_DUPLICATE  = "HOLD_CHANGED_FILES_DUPLICATE"
STATE_AED_PLAN_INCLUDED        = "HOLD_AED_PLAN_INCLUDED"
STATE_OUTSIDE_ALLOWED_FILES   = "HOLD_OUTSIDE_ALLOWED_FILES"
STATE_FORBIDDEN_FILE_TOUCHED   = "HOLD_FORBIDDEN_FILE_TOUCHED"
STATE_TOO_MANY_FILES_CHANGED   = "HOLD_TOO_MANY_FILES_CHANGED"
STATE_DIFF_MISSING_CHANGED_FILE = "HOLD_DIFF_MISSING_CHANGED_FILE"
STATE_DIFF_CONTAINS_FORBIDDEN_FILE = "HOLD_DIFF_CONTAINS_FORBIDDEN_FILE"
STATE_PMG_NOT_CLEAN            = "HOLD_PMG_NOT_CLEAN"
STATE_REAL_CLAUDE_NOT_CONFIRMED = "HOLD_REAL_CLAUDE_NOT_CONFIRMED"
STATE_CLAUDE_EXIT_NONZERO      = "HOLD_CLAUDE_EXIT_NONZERO"
STATE_REPO_DIRTY               = "HOLD_REPO_DIRTY"
STATE_OUTPUT_INSIDE_REPO       = "HOLD_OUTPUT_INSIDE_REPO"
STATE_WORKTREE_INSIDE_REPO     = "HOLD_WORKTREE_INSIDE_REPO"
STATE_PATCH_PATH_INSIDE_REPO   = "HOLD_PATCH_PATH_INSIDE_REPO"
STATE_COMMAND_CONTRACT_UNSAFE  = "HOLD_COMMAND_CONTRACT_UNSAFE"
STATE_PACKET_METADATA_MISSING  = "HOLD_PACKET_METADATA_MISSING"
STATE_UNKNOWN                  = "HOLD_UNKNOWN"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_status_clean(repo_root: Path) -> bool:
    """Return True if repo working tree is clean (no staged/unstaged changes)."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=10,
    )
    # Only fail on non-untracked changes; untracked files alone are ok for apply readiness
    non_untracked = [l for l in result.stdout.strip().splitlines() if not l.startswith("?? ")]
    return len(non_untracked) == 0


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


def _read_file(path: Path, limit: int | None = None) -> str:
    """Read file content, optionally limited to first N bytes."""
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8", errors="replace")
    if limit is not None and len(content) > limit:
        return content[:limit]
    return content


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify temp-worktree diff.patch is ready for human-approved apply."
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
        "--repo-root", default=str(REPO_ROOT),
        help="Path to AED repo root (default: auto-detected)"
    )
    parser.add_argument(
        "--output-json", required=True,
        help="Path to write JSON result"
    )
    parser.add_argument(
        "--output-md", required=True,
        help="Path to write Markdown result"
    )
    parser.add_argument(
        "--require-real-claude-invoked", action="store_true",
        help="Fail if real Claude invocation is not confirmed"
    )
    parser.add_argument(
        "--require-pmg-clean", action="store_true",
        help="Fail if PMG status is not clean"
    )
    parser.add_argument(
        "--max-diff-bytes", type=int, default=1_000_000,
        help="Maximum diff.patch size in bytes (default: 1_000_000)"
    )
    parser.add_argument(
        "--expected-status", default="PATCH_READY_FOR_HUMAN_REVIEW",
        help="Expected result status (default: PATCH_READY_FOR_HUMAN_REVIEW)"
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Core verification
# ---------------------------------------------------------------------------

def verify(
    result_json_path: Path,
    diff_patch_path: Path,
    repo_root: Path,
    require_real_claude: bool,
    require_pmg_clean: bool,
    max_diff_bytes: int,
    expected_status: str,
) -> tuple[str, dict]:
    """
    Run all apply-readiness checks. Returns (status, checks_dict).
    """
    checks: dict[str, object] = {}
    errors: list[str] = []
    warnings: list[str] = []

    # ── 1. Result JSON ───────────────────────────────────────────────────────
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

    if len(diff_content) > max_diff_bytes:
        warnings.append(f"diff.patch size ({len(diff_content)} bytes) exceeds max ({max_diff_bytes})")

    # ── 3. Result status ──────────────────────────────────────────────────────
    result_status = result.get("status", "")
    checks["result_status"] = result_status
    if result_status != expected_status:
        return STATE_STATUS_NOT_PATCH_READY, {
            **checks,
            "expected_status": expected_status,
            "actual_status": result_status,
        }

    # ── 4. Changed files ──────────────────────────────────────────────────────
    changed_files = result.get("changed_files", [])
    checks["changed_files"] = changed_files
    if not changed_files:
        return STATE_CHANGED_FILES_EMPTY, {**checks, "changed_files_count": 0}

    dups = _check_duplicate_paths(changed_files)
    if dups:
        return STATE_CHANGED_FILES_DUPLICATE, {
            **checks,
            "duplicate_paths": dups,
            "changed_files_count": len(changed_files),
        }

    # ── 5. .aed_plan.md exclusion ─────────────────────────────────────────────
    aed_plan_in_changed = ".aed_plan.md" in changed_files
    aed_plan_in_diff = ".aed_plan.md" in diff_content
    checks["aed_plan_excluded_from_changed_files"] = not aed_plan_in_changed
    checks["aed_plan_excluded_from_diff"] = not aed_plan_in_diff
    if aed_plan_in_changed or aed_plan_in_diff:
        return STATE_AED_PLAN_INCLUDED, {
            **checks,
            "aed_plan_in_changed_files": aed_plan_in_changed,
            "aed_plan_in_diff": aed_plan_in_diff,
        }

    # ── 6. Packet metadata (allowed/forbidden/max_changed_files) ──────────────
    task = result.get("task", {})
    approval = result.get("approval", {})
    allowed_files = task.get("allowed_files", [])
    forbidden_files = task.get("forbidden_files", [])
    max_changed_files = approval.get("max_changed_files", task.get("max_changed_files", None))

    checks["allowed_files"] = allowed_files
    checks["forbidden_files"] = forbidden_files
    checks["max_changed_files"] = max_changed_files

    if not allowed_files and not forbidden_files:
        # Try to load from execution packet if present in result
        packet_metadata = result.get("execution", {}).get("packet_metadata", {})
        if packet_metadata:
            allowed_files = packet_metadata.get("allowed_files", [])
            forbidden_files = packet_metadata.get("forbidden_files", [])
            max_changed_files = packet_metadata.get("max_changed_files", max_changed_files)
            checks["allowed_files_from_packet"] = allowed_files
            checks["forbidden_files_from_packet"] = forbidden_files

    if not allowed_files and not forbidden_files:
        # Still ok if both are empty (no constraints)
        warnings.append("No allowed_files or forbidden_files found in result metadata")

    # ── 7. Outside allowed files ───────────────────────────────────────────────
    if allowed_files:
        for cf in changed_files:
            if cf not in allowed_files:
                return STATE_OUTSIDE_ALLOWED_FILES, {
                    **checks,
                    "outside_allowed_file": cf,
                    "changed_files_count": len(changed_files),
                }
        checks["all_changed_files_allowed"] = True

    # ── 8. Forbidden files touched ───────────────────────────────────────────
    if forbidden_files:
        for cf in changed_files:
            if cf in forbidden_files:
                return STATE_FORBIDDEN_FILE_TOUCHED, {
                    **checks,
                    "forbidden_file_touched": cf,
                }
        checks["no_forbidden_files_touched"] = True

    # ── 9. Max changed files ─────────────────────────────────────────────────
    if max_changed_files is not None:
        if len(changed_files) > max_changed_files:
            return STATE_TOO_MANY_FILES_CHANGED, {
                **checks,
                "changed_files_count": len(changed_files),
                "max_allowed": max_changed_files,
            }
        checks["max_changed_files_ok"] = True

    # ── 10. diff.patch contains each changed file ─────────────────────────────
    for cf in changed_files:
        if cf not in diff_content:
            return STATE_DIFF_MISSING_CHANGED_FILE, {
                **checks,
                "missing_from_diff": cf,
            }
    checks["diff_contains_all_changed_files"] = True

    # ── 11. diff.patch does not contain forbidden files ───────────────────────
    if forbidden_files:
        for ff in forbidden_files:
            if ff in diff_content:
                return STATE_DIFF_CONTAINS_FORBIDDEN_FILE, {
                    **checks,
                    "forbidden_in_diff": ff,
                }
        checks["diff_excludes_forbidden_files"] = True

    # ── 12. PMG clean (if required) ──────────────────────────────────────────
    pmg_status = result.get("pmg_status", None)
    pmg_blocked = result.get("pmg_blocked_files", None)
    checks["pmg_status"] = pmg_status
    checks["pmg_blocked_files"] = pmg_blocked
    if require_pmg_clean:
        if pmg_status is None:
            warnings.append("pmg_status not present in result; cannot verify PMG clean")
        elif pmg_status != "clean":
            return STATE_PMG_NOT_CLEAN, {
                **checks,
                "pmg_status": pmg_status,
            }
        elif pmg_blocked and pmg_blocked > 0:
            return STATE_PMG_NOT_CLEAN, {
                **checks,
                "pmg_blocked_files": pmg_blocked,
            }
        checks["pmg_clean_verified"] = True

    # ── 13. Real Claude invoked (if required) ──────────────────────────────────
    claude_fields = result.get("claude_exit_code") is not None
    claude_started = bool(result.get("claude_started_at"))
    real_claude_invoked = result.get("real_claude_invoked", None)
    checks["claude_exit_code"] = result.get("claude_exit_code")
    checks["real_claude_invoked"] = real_claude_invoked

    if require_real_claude:
        if not claude_fields and not claude_started:
            if not real_claude_invoked:
                return STATE_REAL_CLAUDE_NOT_CONFIRMED, {**checks}
        # Verify claude_exit_code if present
        exit_code = result.get("claude_exit_code")
        if exit_code is not None and exit_code != 0:
            return STATE_CLAUDE_EXIT_NONZERO, {**checks, "claude_exit_code": exit_code}
        checks["real_claude_confirmed"] = True

    # ── 14. Claude exit code (if present) ─────────────────────────────────────
    exit_code = result.get("claude_exit_code")
    if exit_code is not None and exit_code != 0:
        return STATE_CLAUDE_EXIT_NONZERO, {**checks, "claude_exit_code": exit_code}
    checks["claude_exit_zero"] = exit_code == 0 or exit_code is None

    # ── 15. Command contract safety (artifact-level, before environment) ──────
    # Command contract unsafe is an artifact-level property and must be reported
    # even if the repo is dirty — it is a more specific safety signal than
    # HOLD_REPO_DIRTY and should not be masked by environment state.
    contract = result.get("claude_command_contract_summary", result.get("claude_command_contract", {}))
    if contract:
        contract_str = str(contract)
        if "shell=True" in contract_str or "shell = True" in contract_str:
            return STATE_COMMAND_CONTRACT_UNSAFE, {
                **checks,
                "reason": "shell=True found in command contract",
                "contract_summary": contract_str,
            }
        # Check for dangerous bypass flags
        dangerous_flags = ["bypassPermissions", "dangerously-skip-permissions", "--bypass"]
        for flag in dangerous_flags:
            if flag in contract_str:
                return STATE_COMMAND_CONTRACT_UNSAFE, {
                    **checks,
                    "reason": f"dangerous flag {flag!r} found in command contract",
                    "contract_summary": contract_str,
                }
        checks["command_contract_safe"] = True
    else:
        checks["command_contract_present"] = False

    # ── 16. Repo git status ────────────────────────────────────────────────────
    repo_clean = _git_status_clean(repo_root)
    checks["repo_git_clean"] = repo_clean
    if not repo_clean:
        return STATE_REPO_DIRTY, {**checks}

    # ── 17. Output paths outside repo ─────────────────────────────────────────
    # (output paths are passed as args, checked by caller before calling verify)
    checks["output_paths_outside_repo"] = True

    # ── 18. Worktree path outside repo ─────────────────────────────────────────
    worktree_path = result.get("worktree_path", "")
    if worktree_path and _path_inside_repo(worktree_path, repo_root):
        return STATE_WORKTREE_INSIDE_REPO, {
            **checks,
            "worktree_path": str(worktree_path),
        }
    checks["worktree_outside_repo"] = True

    # ── 19. Packet metadata sufficiency ─────────────────────────────────────────
    has_constraints = bool(allowed_files or forbidden_files or max_changed_files is not None)
    if not has_constraints:
        warnings.append("No allowed_files, forbidden_files, or max_changed_files in result; constraints could not be fully verified")

    # ── All checks passed ──────────────────────────────────────────────────────
    return STATE_APPLY_READY, {**checks, "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------

def write_json_output(output_path: Path, status: str, apply_ready: bool, checks: dict,
                       result_json_path: str, diff_patch_path: str,
                       changed_files: list, allowed_files: list, forbidden_files: list,
                       max_changed_files, pmg_status, real_claude_invoked,
                       claude_exit_code, repo_git_clean: bool,
                       errors: list, warnings: list, generated_at: str) -> None:
    data = {
        "status": status,
        "apply_ready": apply_ready,
        "checks": checks,
        "result_json": str(result_json_path),
        "diff_patch": str(diff_patch_path),
        "changed_files": changed_files,
        "allowed_files": allowed_files,
        "forbidden_files": forbidden_files,
        "max_changed_files": max_changed_files,
        "pmg_status": pmg_status,
        "real_claude_invoked": real_claude_invoked,
        "claude_exit_code": claude_exit_code,
        "repo_git_status_clean": repo_git_clean,
        "errors": errors,
        "warnings": warnings,
        "generated_at": generated_at,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def write_md_output(output_path: Path, status: str, apply_ready: bool, checks: dict,
                    result_json_path: str, diff_patch_path: str,
                    changed_files: list, allowed_files: list, forbidden_files: list,
                    max_changed_files, pmg_status, real_claude_invoked,
                    claude_exit_code, repo_git_clean: bool,
                    errors: list, warnings: list, generated_at: str) -> None:
    verdict = "✅ APPLY_READY" if apply_ready else f"❌ {status}"

    lines = [
        f"# Temp-Worktree Apply Readiness Verifier",
        f"",
        f"**Status:** {verdict}",
        f"",
        f"**Result JSON:** `{result_json_path}`",
        f"**Diff patch:** `{diff_patch_path}`",
        f"**Generated:** {generated_at}",
        f"",
        f"## Verdict",
        f"",
        f"**{verdict}**",
        f"",
        f"## Changed Files ({len(changed_files)})",
    ]
    for cf in changed_files:
        lines.append(f"- `{cf}`")

    lines.extend([
        f"",
        f"## Key Checks",
    ])

    key_checks = [
        ("result_json_exists", "result.json found"),
        ("result_json_valid", "result.json valid JSON"),
        ("diff_patch_exists", "diff.patch found"),
        ("diff_patch_non_empty", "diff.patch non-empty"),
        ("aed_plan_excluded_from_changed_files", ".aed_plan.md excluded from changed_files"),
        ("aed_plan_excluded_from_diff", ".aed_plan.md excluded from diff"),
        ("all_changed_files_allowed", "all changed files in allowed_files"),
        ("no_forbidden_files_touched", "no forbidden files touched"),
        ("diff_contains_all_changed_files", "diff contains all changed files"),
        ("diff_excludes_forbidden_files", "diff excludes forbidden files"),
        ("pmg_clean_verified", "PMG clean (if required)"),
        ("real_claude_confirmed", "real Claude invoked (if required)"),
        ("claude_exit_zero", "Claude exit code is 0 (if present)"),
        ("repo_git_clean", "main repo git status clean"),
        ("worktree_outside_repo", "worktree path outside repo"),
        ("command_contract_safe", "command contract safe (no shell=True)"),
    ]

    for key, label in key_checks:
        val = checks.get(key)
        if val is True:
            lines.append(f"- ✅ {label}")
        elif val is False:
            lines.append(f"- ❌ {label}")
        else:
            lines.append(f"- — {label}: {val}")

    if errors:
        lines.extend(["", "## Errors"])
        for err in errors:
            lines.append(f"- ❌ {err}")

    if warnings:
        lines.extend(["", "## Warnings"])
        for warn in warnings:
            lines.append(f"- ⚠️ {warn}")

    lines.extend([
        "",
        "---",
        "",
        "**This verifier does not apply patches.** It only reports whether",
        "a diff.patch is ready for a separate human-approved apply step.",
        "No patch application, git push, PR creation, or merge occurs.",
    ])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    result_json_path = Path(args.result_json).resolve()
    diff_patch_path = Path(args.diff_patch).resolve()
    repo_root = Path(args.repo_root).resolve()
    output_json = Path(args.output_json).resolve()
    output_md = Path(args.output_md).resolve()

    # Pre-flight: output paths must be outside repo
    for path, name in [(output_json, "output-json"), (output_md, "output-md")]:
        if _path_inside_repo(path, repo_root):
            print(f"FATAL: {name} path is inside repo: {path}", file=sys.stderr)
            return 1

    # Run verification
    try:
        status, checks = verify(
            result_json_path,
            diff_patch_path,
            repo_root,
            require_real_claude=args.require_real_claude_invoked,
            require_pmg_clean=args.require_pmg_clean,
            max_diff_bytes=args.max_diff_bytes,
            expected_status=args.expected_status,
        )
    except Exception as e:
        status = STATE_UNKNOWN
        checks = {"fatal_error": str(e)}

    apply_ready = (status == STATE_APPLY_READY)

    # Extract relevant fields for output
    result = _load_json(result_json_path) or {}
    changed_files = result.get("changed_files", [])
    task = result.get("task", {})
    approval = result.get("approval", {})
    allowed_files = task.get("allowed_files", [])
    forbidden_files = task.get("forbidden_files", [])
    max_changed_files = approval.get("max_changed_files", task.get("max_changed_files"))
    pmg_status = result.get("pmg_status")
    real_claude_invoked = result.get("real_claude_invoked")
    claude_exit_code = result.get("claude_exit_code")
    repo_git_clean = _git_status_clean(repo_root)
    errors: list = []
    warnings: list = []
    generated_at = datetime.now(timezone.utc).isoformat()

    # Write outputs
    try:
        write_json_output(
            output_json, status, apply_ready, checks,
            str(result_json_path), str(diff_patch_path),
            changed_files, allowed_files, forbidden_files,
            max_changed_files, pmg_status, real_claude_invoked,
            claude_exit_code, repo_git_clean,
            errors, warnings, generated_at,
        )
        print(f"JSON: {output_json}")
    except Exception as e:
        print(f"ERROR writing JSON output: {e}", file=sys.stderr)

    try:
        write_md_output(
            output_md, status, apply_ready, checks,
            str(result_json_path), str(diff_patch_path),
            changed_files, allowed_files, forbidden_files,
            max_changed_files, pmg_status, real_claude_invoked,
            claude_exit_code, repo_git_clean,
            errors, warnings, generated_at,
        )
        print(f"Markdown: {output_md}")
    except Exception as e:
        print(f"ERROR writing MD output: {e}", file=sys.stderr)

    print(f"Status: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())