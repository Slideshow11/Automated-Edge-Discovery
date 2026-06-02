#!/usr/bin/env python3
"""
verify_temp_worktree_applied_branch.py

Read-only verifier that inspects a local branch created from a temp-worktree
apply artifact and determines whether it is ready for manual human review /
PR preparation.

IMPORTANT: This script does NOT commit, push, open PRs, merge, apply patches,
stage files, or invoke Claude. It only reads git state and artifact files.

Exit codes:
  0 — check complete (any status written to output)
  1 — fatal error (missing args, file read error)

Usage:
    python3 scripts/local/verify_temp_worktree_applied_branch.py \\
        --repo-root /path/to/repo \\
        --branch-name apply/smoke-005-2026-05-22 \\
        --expected-base-sha 295d74bb9544b7d5f08acc9012feafb12ef24cac \\
        --result-json /tmp/aed_run/result.json \\
        --diff-patch /tmp/aed_run/diff.patch \\
        --apply-readiness-json /tmp/aed_run/apply_readiness.json \\
        --output-json /tmp/apply_branch_readiness.json \\
        --output-md /tmp/apply_branch_readiness.md
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

STATE_APPLIED_BRANCH_READY       = "APPLIED_BRANCH_READY"
STATE_HOLD_REPO_NOT_FOUND        = "HOLD_REPO_NOT_FOUND"
STATE_HOLD_OUTPUT_INSIDE_REPO    = "HOLD_OUTPUT_INSIDE_REPO"
STATE_HOLD_BRANCH_MISSING        = "HOLD_BRANCH_MISSING"
STATE_HOLD_PROTECTED_BRANCH     = "HOLD_PROTECTED_BRANCH"
STATE_HOLD_EXPECTED_BASE_MISSING = "HOLD_EXPECTED_BASE_MISSING"
STATE_HOLD_BASE_MISMATCH        = "HOLD_BASE_MISMATCH"
STATE_HOLD_MERGE_BASE_MISMATCH   = "HOLD_MERGE_BASE_MISMATCH"
STATE_HOLD_REPO_DIRTY            = "HOLD_REPO_DIRTY"
STATE_HOLD_RESULT_MISSING        = "HOLD_RESULT_MISSING"
STATE_HOLD_RESULT_INVALID_JSON   = "HOLD_RESULT_INVALID_JSON"
STATE_HOLD_DIFF_MISSING          = "HOLD_DIFF_MISSING"
STATE_HOLD_DIFF_EMPTY            = "HOLD_DIFF_EMPTY"
STATE_HOLD_READINESS_MISSING     = "HOLD_READINESS_MISSING"
STATE_HOLD_READINESS_NOT_APPLY_READY = "HOLD_READINESS_NOT_APPLY_READY"
STATE_HOLD_STATUS_NOT_PATCH_READY = "HOLD_STATUS_NOT_PATCH_READY"
STATE_HOLD_CHANGED_FILES_EMPTY   = "HOLD_CHANGED_FILES_EMPTY"
STATE_HOLD_CHANGED_FILES_DUPLICATE = "HOLD_CHANGED_FILES_DUPLICATE"
STATE_HOLD_BRANCH_DIFF_MISMATCH  = "HOLD_BRANCH_DIFF_MISMATCH"
STATE_HOLD_AED_PLAN_INCLUDED     = "HOLD_AED_PLAN_INCLUDED"
STATE_HOLD_FORBIDDEN_FILE_TOUCHED = "HOLD_FORBIDDEN_FILE_TOUCHED"
STATE_HOLD_TOO_MANY_FILES_CHANGED = "HOLD_TOO_MANY_FILES_CHANGED"
STATE_HOLD_PMG_NOT_CLEAN         = "HOLD_PMG_NOT_CLEAN"
STATE_HOLD_UNEXPECTED_UNTRACKED  = "HOLD_UNEXPECTED_UNTRACKED_FILE"
STATE_HOLD_UNKNOWN               = "HOLD_UNKNOWN"

PROTECTED_BRANCH_NAMES = {"main", "master", "HEAD"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command, return the result."""
    return subprocess.run(
        ["git", "-C", str(repo_root)] + list(args),
        capture_output=True,
        text=True,
        timeout=30,
    )


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


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify a local apply branch is ready for manual human review."
    )
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--branch-name", required=True)
    parser.add_argument("--expected-base-sha", required=True)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--diff-patch", required=True)
    parser.add_argument("--apply-readiness-json", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument(
        "--require-tests-passed-json",
        default=None,
        help="Path to optional tests-passed JSON (not implemented yet, accepted for compatibility)",
    )
    parser.add_argument(
        "--max-changed-files",
        type=int,
        default=None,
        help="Override max_changed_files (inferred from apply_readiness.json if present)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Core verification
# ---------------------------------------------------------------------------

def verify(
    repo_root: Path,
    branch_name: str,
    expected_base_sha: str,
    result_json_path: Path,
    diff_patch_path: Path,
    apply_readiness_json_path: Path,
    max_changed_files: int | None = None,
) -> tuple[str, dict]:
    """
    Run all applied-branch verification checks. Returns (status, checks_dict).
    """
    checks: dict[str, object] = {}
    errors: list[str] = []
    warnings: list[str] = []

    # ── 1. Repo is a git repo ─────────────────────────────────────────────────
    r = _run_git(repo_root, "rev-parse", "--is-inside-work-tree")
    if r.returncode != 0 or r.stdout.strip() != "true":
        return STATE_HOLD_REPO_NOT_FOUND, {"repo_is_git": False}
    checks["repo_is_git"] = True

    # ── 2. Branch exists locally ─────────────────────────────────────────────
    r = _run_git(repo_root, "branch", "--list", branch_name)
    branch_exists = r.returncode == 0 and r.stdout.strip() != ""
    if not branch_exists:
        return STATE_HOLD_BRANCH_MISSING, {"branch_exists": False, "branch_name": branch_name}
    checks["branch_exists"] = True
    checks["branch_name"] = branch_name

    # ── 3. Branch is not a protected branch ──────────────────────────────────
    if branch_name in PROTECTED_BRANCH_NAMES:
        return STATE_HOLD_PROTECTED_BRANCH, {"branch_name": branch_name}
    checks["branch_not_protected"] = True

    # ── 4. Expected base SHA exists and is valid ─────────────────────────────
    # Reject null SHA (40 zeros) and obviously invalid strings
    if not expected_base_sha or expected_base_sha == "0000000000000000000000000000000000000000":
        return STATE_HOLD_EXPECTED_BASE_MISSING, {"expected_base_sha": expected_base_sha}
    r = _run_git(repo_root, "rev-parse", "--verify", expected_base_sha)
    if r.returncode != 0:
        return STATE_HOLD_EXPECTED_BASE_MISSING, {"expected_base_sha": expected_base_sha}

    # ── 5. Merge base with expected base matches expected_base_sha ───────────
    r = _run_git(repo_root, "merge-base", expected_base_sha, f"refs/heads/{branch_name}")
    if r.returncode == 0:
        merge_base_sha = r.stdout.strip()
    else:
        merge_base_sha = ""
    checks["merge_base_sha"] = merge_base_sha
    if merge_base_sha != expected_base_sha:
        return STATE_HOLD_MERGE_BASE_MISMATCH, {
            **checks,
            "expected_base_sha": expected_base_sha,
            "merge_base_sha": merge_base_sha,
        }
    checks["merge_base_matches"] = True

    # ── 6. Current branch HEAD ───────────────────────────────────────────────
    r = _run_git(repo_root, "rev-parse", "--verify", f"refs/heads/{branch_name}")
    if r.returncode == 0:
        current_head_sha = r.stdout.strip()
    else:
        current_head_sha = ""
    checks["current_head_sha"] = current_head_sha

    # ── 7. Result JSON exists and is valid ───────────────────────────────────
    result = _load_json(result_json_path)
    if result is None:
        if not result_json_path.exists():
            return STATE_HOLD_RESULT_MISSING, {"result_json_exists": False}
        return STATE_HOLD_RESULT_INVALID_JSON, {"result_json_exists": True, "valid_json": False}
    checks["result_json_exists"] = True
    checks["result_json_valid"] = True

    # ── 8. Diff patch exists and is non-empty ────────────────────────────────
    if not diff_patch_path.exists():
        return STATE_HOLD_DIFF_MISSING, {"diff_patch_exists": False}
    diff_content = diff_patch_path.read_text(encoding="utf-8", errors="replace")
    if not diff_content.strip():
        return STATE_HOLD_DIFF_EMPTY, {"diff_patch_exists": True, "diff_patch_empty": True}
    checks["diff_patch_exists"] = True
    checks["diff_patch_non_empty"] = True

    # ── 9. Apply readiness JSON exists and is APPLY_READY ─────────────────────
    readiness = _load_json(apply_readiness_json_path)
    if readiness is None:
        return STATE_HOLD_READINESS_MISSING, {"apply_readiness_exists": False}
    checks["apply_readiness_exists"] = True
    if readiness.get("status") != "APPLY_READY":
        return STATE_HOLD_READINESS_NOT_APPLY_READY, {
            **checks,
            "apply_readiness_status": readiness.get("status"),
        }
    checks["apply_readiness_status"] = "APPLY_READY"

    # ── 10. Result status is PATCH_READY_FOR_HUMAN_REVIEW ───────────────────
    result_status = result.get("status", "")
    checks["result_status"] = result_status
    if result_status != "PATCH_READY_FOR_HUMAN_REVIEW":
        return STATE_HOLD_STATUS_NOT_PATCH_READY, {
            **checks,
            "expected_status": "PATCH_READY_FOR_HUMAN_REVIEW",
            "actual_status": result_status,
        }
    checks["result_status_ok"] = True

    # ── 11. Changed files from result.json ───────────────────────────────────
    changed_files = result.get("changed_files", [])
    checks["changed_files"] = changed_files
    if not changed_files:
        return STATE_HOLD_CHANGED_FILES_EMPTY, {**checks, "changed_files_count": 0}
    dups = _check_duplicate_paths(changed_files)
    if dups:
        return STATE_HOLD_CHANGED_FILES_DUPLICATE, {
            **checks,
            "duplicate_paths": dups,
            "changed_files_count": len(changed_files),
        }
    checks["changed_files_unique"] = True

    # ── 12. Forbidden files from result.json ─────────────────────────────────
    task = result.get("task", {})
    forbidden_files = task.get("forbidden_files", [])
    checks["forbidden_files"] = forbidden_files

    # ── 13. Max changed files from result.json ───────────────────────────────
    approval = result.get("approval", {})
    result_max_changed = approval.get("max_changed_files", task.get("max_changed_files"))
    # Infer from apply_readiness if not set
    if max_changed_files is None:
        max_changed_files = result_max_changed
    checks["max_changed_files"] = max_changed_files

    # ── 14. Branch diff — get list of changed files ───────────────────────────
    r = _run_git(
        repo_root,
        "diff",
        "--name-only",
        f"{expected_base_sha}...refs/heads/{branch_name}",
    )
    if r.returncode != 0:
        # Fallback: diff against HEAD of branch
        r = _run_git(repo_root, "diff", "--name-only", expected_base_sha, current_head_sha)

    branch_changed_files = [f for f in r.stdout.strip().splitlines() if f]
    checks["branch_changed_files"] = branch_changed_files
    checks["branch_changed_files_count"] = len(branch_changed_files)

    # ── 14b. Untracked files on branch (post-apply untracked are visible via
    #        git status --short with a pathspec that covers the branch working tree).
    #        Run git status from the repo root scoped to the branch's tree.
    #        This does NOT checkout or switch branches — it reads the current working
    #        tree state as-is. Any untracked files are either the result of a prior
    #        `git apply` on this branch (intended) or unexpected artifacts (blocked).
    r_status = _run_git(
        repo_root,
        "status",
        "--short",
        "-uall",
        "--",
    )
    untracked_files = []
    if r_status.returncode == 0:
        untracked_files = [
            line[3:].strip()  # strip "?? " prefix
            for line in r_status.stdout.strip().splitlines()
            if line.startswith("?? ")
        ]

    # Expected untracked: ?? files that are in changed_files
    # Unexpected untracked: ?? files NOT in changed_files
    expected_set = set(changed_files)
    untracked_expected = sorted(f for f in untracked_files if f in expected_set)
    untracked_unexpected = sorted(f for f in untracked_files if f not in expected_set)

    checks["untracked_files"] = untracked_files
    checks["untracked_expected"] = untracked_expected
    checks["untracked_unexpected"] = untracked_unexpected

    # Block unexpected untracked files — fail closed
    if untracked_unexpected:
        return STATE_HOLD_UNEXPECTED_UNTRACKED, {
            **checks,
            "unexpected_untracked_files": untracked_unexpected,
        }
    checks["no_unexpected_untracked"] = True

    # ── 14b.2. Tracked modified files — git apply on same-revision applied
    #        branches leaves modifications in index/worktree rather than as
    #        committed changes (branch head == base sha, so committed diff empty).
    #        Detect via git status --short. Stage codes:
    #          "M " = modified in index (staged)  ← git apply uses this
    #          " M" = modified in worktree
    #          "MM" = modified in both index+worktree
    #        We accept any stage where stage[0] == 'M' and stage != "??".
    tracked_modified = []
    if r_status.returncode == 0:
        for line in r_status.stdout.strip().splitlines():
            if not line:
                continue
            ls = line.lstrip()
            parts = ls.split(" ", 1)
            if len(parts) < 2:
                continue
            stage = parts[0]
            is_modified = len(stage) >= 1 and stage[0] == "M" and stage != "??"
            if is_modified:
                path = parts[1].strip()
                if path:
                    tracked_modified.append(path)

    tracked_modified_expected = sorted(f for f in tracked_modified if f in expected_set)
    tracked_modified_unexpected = sorted(f for f in tracked_modified if f not in expected_set)

    checks["tracked_modified"] = tracked_modified
    checks["tracked_modified_expected"] = tracked_modified_expected
    checks["tracked_modified_unexpected"] = tracked_modified_unexpected

    if tracked_modified_unexpected:
        return STATE_HOLD_UNEXPECTED_UNTRACKED, {
            **checks,
            "unexpected_dirty_tracked_files": tracked_modified_unexpected,
        }
    checks["no_unexpected_dirty_tracked"] = True

    # ── 14b.3. Staged-added files — apply_mock_edits in
    #         run_temp_worktree_execution.py writes the mock-edit content and
    #         then stages the file via the `git ... add` subcommand, so
    #         `git status --short` shows status "A" (staged add). The
    #         apply_to_branch stage (stage 5) already counts these as
    #         "modified" via the M/A/D/R check and reports applied=true. This
    #         verifier must count them too, otherwise a clean mock pipeline
    #         will always halt at HOLD_BRANCH_DIFF_MISMATCH.
    #         Stage codes we treat as staged-added:
    #           "A " = added in index, not in worktree
    #           "AM" = added in index, modified in worktree
    #           "A." = added in index, type-change in worktree (rare)
    staged_added = []
    if r_status.returncode == 0:
        for line in r_status.stdout.strip().splitlines():
            if not line:
                continue
            ls = line.lstrip()
            parts = ls.split(" ", 1)
            if len(parts) < 2:
                continue
            stage = parts[0]
            # First column == "A" means staged add. Worktree-column (second char)
            # can be ' ' (clean), 'M' (modified in worktree), or '.' (unmodified).
            is_staged_added = (
                len(stage) >= 1
                and stage[0] == "A"
                and stage != "??"
            )
            if is_staged_added:
                path = parts[1].strip()
                if path:
                    staged_added.append(path)

    staged_added_expected = sorted(f for f in staged_added if f in expected_set)
    staged_added_unexpected = sorted(f for f in staged_added if f not in expected_set)

    checks["staged_added"] = staged_added
    checks["staged_added_expected"] = staged_added_expected
    checks["staged_added_unexpected"] = staged_added_unexpected

    # Block unexpected staged-added files — fail closed.
    # An arbitrary file that has been staged via the `git ... add` subcommand
    # but is not in the expected changed_files list is just as suspicious as
    # an unexpected untracked file.
    if staged_added_unexpected:
        return STATE_HOLD_UNEXPECTED_UNTRACKED, {
            **checks,
            "unexpected_staged_added_files": staged_added_unexpected,
        }
    checks["no_unexpected_staged_added"] = True

    # ── 15. Branch diff must contain exactly the result changed_files ─────────
    #        Include expected untracked files, expected tracked-modified files,
    #        and expected staged-added files. The branch tree itself is rarely
    #        populated in mock mode because apply_to_branch explicitly does not
    #        commit (see apply_temp_worktree_patch_to_branch.py docstring line 15:
    #        "patch applied to local branch (not committed)"). The expected
    #        files appear in the working tree / index instead, surfaced via the
    #        buckets above.
    actual_applied = (
        set(branch_changed_files)
        | set(untracked_expected)
        | set(tracked_modified_expected)
        | set(staged_added_expected)
    )
    branch_set = actual_applied
    if branch_set != expected_set:
        extra = sorted(branch_set - expected_set)
        missing = sorted(expected_set - branch_set)
        return STATE_HOLD_BRANCH_DIFF_MISMATCH, {
            **checks,
            "extra_files_in_branch": extra,
            "missing_files_from_branch": missing,
        }
    checks["branch_diff_matches_expected"] = True

    # Update branch_changed_files in checks to reflect applied state
    checks["branch_changed_files"] = sorted(actual_applied)

    # ── 16. .aed_plan.md must NOT be in actual applied files ─────────────────
    if ".aed_plan.md" in actual_applied:
        return STATE_HOLD_AED_PLAN_INCLUDED, {
            **checks,
            "aed_plan_in_branch_diff": True,
        }
    checks["aed_plan_excluded"] = True

    # ── 17. Forbidden files must NOT be in actual applied files ──────────────
    if forbidden_files:
        touched = [f for f in actual_applied if f in forbidden_files]
        if touched:
            return STATE_HOLD_FORBIDDEN_FILE_TOUCHED, {
                **checks,
                "forbidden_touched": touched,
            }
    checks["no_forbidden_files_touched"] = True

    # ── 18. Max changed files enforcement ────────────────────────────────────
    if max_changed_files is not None:
        if len(actual_applied) > max_changed_files:
            return STATE_HOLD_TOO_MANY_FILES_CHANGED, {
                **checks,
                "branch_changed_files_count": len(actual_applied),
                "max_allowed": max_changed_files,
            }
        checks["max_changed_files_ok"] = True

    # ── 19. PMG clean (if apply_readiness reports PMG status) ────────────────
    pmg_status = readiness.get("pmg_status", None)
    pmg_blocked = readiness.get("pmg_blocked_files", None)
    checks["pmg_status"] = pmg_status
    checks["pmg_blocked_files"] = pmg_blocked
    if pmg_status is not None and pmg_status != "clean":
        return STATE_HOLD_PMG_NOT_CLEAN, {**checks}
    if pmg_blocked is not None and pmg_blocked > 0:
        return STATE_HOLD_PMG_NOT_CLEAN, {**checks}
    checks["pmg_clean"] = True

    # ── All checks passed ─────────────────────────────────────────────────────
    return STATE_APPLIED_BRANCH_READY, {**checks, "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------

def write_json_output(
    output_path: Path,
    status: str,
    applied_branch_ready: bool,
    checks: dict,
    repo_root: str,
    branch_name: str,
    expected_base_sha: str,
    merge_base_sha: str,
    current_head_sha: str,
    changed_files_expected: list,
    changed_files_actual: list,
    result_json: str,
    diff_patch: str,
    apply_readiness_json: str,
    generated_human_commands: dict,
    errors: list,
    warnings: list,
    generated_at: str,
    safety_statement: str,
) -> None:
    data = {
        "status": status,
        "applied_branch_ready": applied_branch_ready,
        "repo_root": repo_root,
        "branch_name": branch_name,
        "expected_base_sha": expected_base_sha,
        "merge_base_sha": merge_base_sha,
        "current_head_sha": current_head_sha,
        "changed_files_expected": changed_files_expected,
        "changed_files_actual": changed_files_actual,
        "result_json": result_json,
        "diff_patch": diff_patch,
        "apply_readiness_json": apply_readiness_json,
        "generated_human_commands": generated_human_commands,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "generated_at": generated_at,
        "safety_statement": safety_statement,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def write_md_output(
    output_path: Path,
    status: str,
    applied_branch_ready: bool,
    checks: dict,
    repo_root: str,
    branch_name: str,
    expected_base_sha: str,
    merge_base_sha: str,
    current_head_sha: str,
    changed_files_expected: list,
    changed_files_actual: list,
    result_json: str,
    diff_patch: str,
    apply_readiness_json: str,
    generated_human_commands: dict,
    errors: list,
    warnings: list,
    generated_at: str,
    safety_statement: str,
) -> None:
    verdict = "✅ APPLIED_BRANCH_READY" if applied_branch_ready else f"❌ {status}"

    lines = [
        f"# Temp-Worktree Applied Branch Verifier",
        f"",
        f"**Status:** {verdict}",
        f"",
        f"**Repo:** `{repo_root}`",
        f"**Branch:** `{branch_name}`",
        f"**Expected base SHA:** `{expected_base_sha}`",
        f"**Merge base SHA:** `{merge_base_sha}`",
        f"**Current HEAD SHA:** `{current_head_sha}`",
        f"",
        f"## Verdict",
        f"",
        f"**{verdict}**",
        f"",
        f"## Changed Files",
        f"",
        f"**Expected:** {len(changed_files_expected)}",
    ]
    for f in sorted(changed_files_expected):
        lines.append(f"- `{f}`")

    lines.extend([
        f"",
        f"**Actual (branch diff):** {len(changed_files_actual)}",
    ])
    for f in sorted(changed_files_actual):
        lines.append(f"- `{f}`")

    lines.extend([
        f"",
        f"## Human Review Commands",
        f"",
        f"```bash",
        f"# View diff summary",
        f"git -C {repo_root} diff --stat {expected_base_sha}...refs/heads/{branch_name}",
        f"#",
        f"# View full diff",
        f"git -C {repo_root} diff {expected_base_sha}...refs/heads/{branch_name}",
        f"#",
        f"# Suggested tests",
        f"{generated_human_commands.get('suggested_tests', '')}",
        f"```",
    ])

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
        safety_statement,
    ])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    repo_root = Path(args.repo_root).resolve()
    branch_name = args.branch_name
    expected_base_sha = args.expected_base_sha
    result_json_path = Path(args.result_json).resolve()
    diff_patch_path = Path(args.diff_patch).resolve()
    apply_readiness_json_path = Path(args.apply_readiness_json).resolve()
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
            repo_root,
            branch_name,
            expected_base_sha,
            result_json_path,
            diff_patch_path,
            apply_readiness_json_path,
            max_changed_files=args.max_changed_files,
        )
    except Exception as e:
        status = STATE_HOLD_UNKNOWN
        checks = {"fatal_error": str(e)}

    applied_branch_ready = (status == STATE_APPLIED_BRANCH_READY)

    # Extract info for output
    result = _load_json(result_json_path) or {}
    readiness = _load_json(apply_readiness_json_path) or {}
    changed_files_expected = result.get("changed_files", [])
    changed_files_actual = checks.get("branch_changed_files", [])
    merge_base_sha = checks.get("merge_base_sha", "")
    current_head_sha = checks.get("current_head_sha", "")
    errors: list = []
    warnings: list = []
    generated_at = datetime.now(timezone.utc).isoformat()
    safety_statement = (
        "This verifier did not commit, push, open a PR, merge, apply a patch, "
        "stage files, or invoke Claude. It only reads git state and artifact files."
    )

    # Build generated human commands (text only, not executed)
    git_diff_stat = ""
    git_diff = ""
    suggested_tests = "pytest tests/ -q  # run from repo root"

    try:
        r = _run_git(
            repo_root,
            "diff", "--stat",
            f"{expected_base_sha}...refs/heads/{branch_name}",
        )
        if r.returncode == 0:
            git_diff_stat = r.stdout.strip()
    except Exception:
        pass

    try:
        r = _run_git(
            repo_root,
            "diff",
            f"{expected_base_sha}...refs/heads/{branch_name}",
        )
        if r.returncode == 0:
            git_diff = r.stdout.strip()
    except Exception:
        pass

    generated_human_commands = {
        "git_diff_stat": git_diff_stat,
        "git_diff": git_diff[:2000] + ("..." if len(git_diff) > 2000 else ""),
        "suggested_tests": suggested_tests,
        "note": "Commands are TEXT ONLY — not executed by this verifier.",
    }

    # Write outputs
    try:
        write_json_output(
            output_json, status, applied_branch_ready, checks,
            str(repo_root), branch_name, expected_base_sha,
            merge_base_sha, current_head_sha,
            changed_files_expected, changed_files_actual,
            str(result_json_path), str(diff_patch_path),
            str(apply_readiness_json_path),
            generated_human_commands,
            errors, warnings, generated_at, safety_statement,
        )
        print(f"JSON: {output_json}")
    except Exception as e:
        print(f"ERROR writing JSON output: {e}", file=sys.stderr)

    try:
        write_md_output(
            output_md, status, applied_branch_ready, checks,
            str(repo_root), branch_name, expected_base_sha,
            merge_base_sha, current_head_sha,
            changed_files_expected, changed_files_actual,
            str(result_json_path), str(diff_patch_path),
            str(apply_readiness_json_path),
            generated_human_commands,
            errors, warnings, generated_at, safety_statement,
        )
        print(f"Markdown: {output_md}")
    except Exception as e:
        print(f"ERROR writing MD output: {e}", file=sys.stderr)

    print(f"Status: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())