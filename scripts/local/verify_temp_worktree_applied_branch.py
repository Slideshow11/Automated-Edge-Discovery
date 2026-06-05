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
import shlex
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


def _parse_status_path(raw: str) -> str:
    """Strip one layer of git-style surrounding quotes from a status line path.

    `git status --short` quotes paths containing spaces or other special
    characters using C-style backslash-escaped double quotes (e.g.
    `A  "docs/a b.md"`). This helper returns the unquoted text so that
    downstream comparison against the unquoted `expected_set` works
    correctly. P2 HOvFP: without this, the staged-added bucket is empty
    for quoted paths and the verifier falls through to
    `HOLD_BRANCH_DIFF_MISMATCH` even though the expected file is staged.

    Unquoted input is returned unchanged (after stripping whitespace).
    Malformed quoted input falls back to the stripped raw text — the
    verifier must not crash on unusual filenames.
    """
    s = raw.strip()
    if not s:
        return s
    if not (len(s) >= 2 and s.startswith('"') and s.endswith('"')):
        # Unquoted path: just trim. shlex.split would tokenize on
        # whitespace which is wrong for paths that legitimately have
        # no surrounding quotes.
        return s
    # Quoted path: use shlex to handle C-style escapes and embedded
    # spaces, parentheses, and other safe characters. shlex.split raises
    # ValueError on truly malformed input; fall back to the raw text
    # rather than crash the verifier.
    try:
        parts = shlex.split(s, posix=True)
    except ValueError:
        return s
    if not parts:
        return s
    return " ".join(parts)


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
            _parse_status_path(line[3:])  # strip "?? " prefix; unquote if git emitted a quoted path
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
                # P2 HOvFP: git status --short quotes paths with spaces
                # or other special characters; unquote via
                # _parse_status_path so the expected_set lookup works.
                path = _parse_status_path(parts[1])
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
            # P2 Gu-dW: Parse the two-column git-status short format
            # without losing the leading space. The format is
            #   XY<space>path
            # where X is the index status and Y is the worktree status.
            # Both columns may be ' ', 'M', 'A', 'D', '?', etc.
            # Examples:
            #   "A  docs/new.md"  index=A staged, worktree=' ' clean
            #   "AM docs/am.md"   index=A staged, worktree=M modified
            #   " M docs/mod.md"  index=' ' not staged, worktree=M modified
            #   " A docs/intent"  index=' ' not staged, worktree=A added
            #                     (intent-to-add via the -N intent flag)
            #   "?? docs/untracked"  untracked (X='?', Y='?')
            # lstrip would collapse " A" to "A" and mis-classify an
            # intent-to-add as a staged add. Use fixed-column slicing.
            if len(line) < 4:
                # Need at least XY + space + path-start
                continue
            x_status = line[0]
            y_status = line[1]
            # line[2] should be a space separating the two columns from
            # the path. Some porcelain configurations use '->' for renames
            # but the verify pipeline does not exercise renames.
            sep = line[2]
            if sep != " ":
                # Malformed or rename arrow — skip conservatively
                continue
            # P2 HOvFP: git status --short quotes paths with spaces or
            # other special characters (e.g. `A  "docs/a b.md"`). Strip
            # the surrounding C-style double quotes via _parse_status_path
            # before comparing against the unquoted expected_set;
            # otherwise the staged-added bucket ends up empty for those
            # paths and the verifier falls through to
            # HOLD_BRANCH_DIFF_MISMATCH.
            path = _parse_status_path(line[3:])
            if not path:
                continue
            # First column == "A" means staged add. Worktree-column (second
            # char) can be ' ' (clean), 'M' (modified), or '.' (unmodified).
            is_staged_added = x_status == "A"
            if is_staged_added:
                staged_added.append(path)

    # Split staged-added into expected vs unexpected.
    # An AM status file (added in index, modified in worktree) still counts
    # as staged-added for verification, but the worktree-modification part
    # is captured separately in `am_worktree_modified` below. This is the
    # P2 Gm0q4 design: the verifier does NOT block on AM, but it must
    # surface the worktree content to the human reviewer.
    staged_added_expected = sorted(f for f in staged_added if f in expected_set)
    staged_added_unexpected = sorted(f for f in staged_added if f not in expected_set)

    # ── 14b.4. AM worktree modifications ──────────────────────────────────
    # For each AM-status expected file, capture the path so the human
    # reviewer can see what differs between the staged content and the
    # on-disk content. The actual diff body is exposed via
    # `unstaged_worktree_diff` / `unstaged_worktree_diff_stat` in the JSON
    # output, and `git diff` / `git diff --stat` are added to the human
    # commands section. The verifier does NOT block on AM, but the
    # divergence is visible.
    am_worktree_modified: list[str] = []
    if r_status.returncode == 0:
        for line in r_status.stdout.strip().splitlines():
            if not line:
                continue
            # P2 Gu-dW: Use the same fixed-column parser as the
            # staged-added bucket above. AM = index added, worktree
            # modified. "AM docs/am.md" has X='A', Y='M'.
            if len(line) < 4:
                continue
            x_status = line[0]
            y_status = line[1]
            sep = line[2]
            if sep != " ":
                continue
            # P2 HOvFP: same as the staged-added parser above — git
            # status --short quotes paths with spaces or other special
            # characters; unquote via _parse_status_path so the
            # expected_set lookup works for AM-status files.
            path = _parse_status_path(line[3:])
            if not path:
                continue
            if x_status == "A" and y_status == "M":
                if path in expected_set:
                    am_worktree_modified.append(path)

    am_worktree_modified = sorted(am_worktree_modified)
    checks["am_worktree_modified"] = am_worktree_modified
    checks["has_am_status"] = bool(am_worktree_modified)

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
    #
    #        AM-status files (index added, worktree modified) are counted
    #        in BOTH `staged_added_expected` AND `am_worktree_modified`
    #        above; the bucket union below already covers them via the
    #        `staged_added_expected` membership.
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

    # ── 20. Pre-push blockers (P2 Gm5km — staged-only/AM honesty) ────────────
    # Even when APPLIED_BRANCH_READY, the verifier must surface a list of
    # conditions that prevent a plain `git push origin {branch_name}` from
    # producing a meaningful PR. This is the human-apply boundary:
    #   - If `refs/heads/{branch_name}` has no new commits vs base
    #     (no `branch_changed_files`), the push will create a no-op PR
    #     unless the human first commits the staged changes.
    #   - If any AM-status files exist, the worktree content differs from
    #     the staged content; the human must reconcile (e.g. stage the
    #     worktree modification, or restore the worktree to discard it).
    # We do NOT change status to HOLD here — the controller/test regression
    # depends on APPLIED_BRANCH_READY in this case — but the blockers list
    # is exported in JSON/Markdown and the human command checklist must
    # be visibly guarded.
    # P2 Gvbo6: The blocker is based on whether any staged-added
    # expected paths are absent from the branch tree, not on whether
    # the branch tree is entirely empty. A branch that has committed
    # changes plus a staged-only expected file will still push a PR
    # that omits the staged content, so the human must reconcile
    # (commit the staged changes or restore the index).
    pre_push_blockers: list[dict] = []
    branch_changed_files_set = set(branch_changed_files or [])
    staged_only_paths = sorted(
        f for f in staged_added_expected
        if f not in branch_changed_files_set
    )
    if staged_only_paths:
        pre_push_blockers.append({
            "kind": "staged_only_no_branch_commit",
            "paths": staged_only_paths,
            "human_action": (
                "First, make sure the target branch is checked out — "
                "run `git -C <repo> switch <branch>` (or `git -C <repo> "
                "checkout <branch>`) if HEAD is on a different branch, "
                "otherwise the commit below will land on the wrong ref. "
                "Then run `git -C <repo> commit -m 'apply staged "
                "changes'` (or equivalent) before `git push origin "
                "<branch>` to ensure the branch ref carries the staged "
                "file content. Affected paths are not in "
                "refs/heads/<branch> yet."
            ),
        })
    # P1 G69nw: any expected path that lives in the worktree only — either
    # as a tracked modification (M/AM/MM) or as an untracked file (??) — has
    # content that is *not* present in refs/heads/<branch>. A plain
    # `git push` would therefore omit that content from the PR. Block on
    # every such path. (The apply check correctly counts them as applied
    # via `tracked_modified_expected`/`untracked_expected`; this blocker
    # only governs the push boundary.)
    dirty_not_in_branch: list[str] = []
    _seen_dirty: set[str] = set()
    for f in list(tracked_modified_expected) + list(untracked_expected):
        if f in _seen_dirty:
            continue
        _seen_dirty.add(f)
        dirty_not_in_branch.append(f)
    dirty_not_in_branch = sorted(dirty_not_in_branch)
    if dirty_not_in_branch:
        pre_push_blockers.append({
            "kind": "expected_dirty_not_in_branch_ref",
            "paths": dirty_not_in_branch,
            "human_action": (
                "First, make sure the target branch is checked out — "
                "run `git -C <repo> switch <branch>` (or `git -C <repo> "
                "checkout <branch>`) if HEAD is on a different branch, "
                "otherwise the commit below will land on the wrong ref. "
                "Then stage and commit the expected dirty path(s) (or "
                "restore the worktree to discard them) before `git push "
                f"origin <branch>`. refs/heads/{branch_name} does not "
                "currently carry the expected content for these paths; "
                "a plain `git push` would omit them from the PR."
            ),
        })
    if am_worktree_modified:
        pre_push_blockers.append({
            "kind": "am_worktree_modified",
            "paths": sorted(am_worktree_modified),
            "human_action": (
                "First, make sure the target branch is checked out — "
                "run `git -C <repo> switch <branch>` (or `git -C <repo> "
                "checkout <branch>`) if HEAD is on a different branch, "
                "otherwise the reconcile/commit below will land on the "
                "wrong ref. Then reconcile the worktree content with "
                "the staged content for these files (e.g. stage the "
                "worktree modification, or restore the worktree to "
                "discard it) before push. The staged content and the "
                "worktree content are out of sync."
            ),
        })
    checks["pre_push_blockers"] = pre_push_blockers
    if pre_push_blockers:
        for blk in pre_push_blockers:
            warnings.append(
                f"PRE-PUSH BLOCKER ({blk['kind']}): {blk['human_action']} "
                f"Affected paths: {', '.join(blk['paths'])}."
            )

    # ── 21. Push boundary (P2 Gm5km, P1 G69nw) ────────────────────────────────
    # Make the branch-ref boundary impossible to miss. The status can remain
    # APPLIED_BRANCH_READY for controller compatibility, but the explicit
    # fields below let a reviewer and the preview consumer detect
    # "index-only staged additions" or "dirty expected paths absent from
    # the branch ref" without parsing prose.
    all_expected_dirty_not_in_branch = sorted(
        set(staged_only_paths) | set(dirty_not_in_branch)
    )
    branch_ref_contains_all_expected = not bool(all_expected_dirty_not_in_branch)
    push_ready = bool(branch_ref_contains_all_expected) and not bool(pre_push_blockers)
    human_review_ready = True  # status == APPLIED_BRANCH_READY path is reached here
    checks["branch_ref_contains_all_expected"] = branch_ref_contains_all_expected
    checks["push_ready"] = push_ready
    checks["human_review_ready"] = human_review_ready
    if not push_ready:
        # Surface the reason as a warning so it shows up in the Markdown
        # Warnings section too.
        if not branch_ref_contains_all_expected:
            warnings.append(
                f"NOT PUSH READY: {len(all_expected_dirty_not_in_branch)} "
                f"expected dirty path(s) are not in refs/heads/{branch_name}. "
                "A plain `git push` would omit these paths. "
                f"Affected: {', '.join(all_expected_dirty_not_in_branch)}."
            )
        if pre_push_blockers:
            warnings.append(
                "NOT PUSH READY: pre-push blockers are present. "
                "See Pre-push Blockers section for required human action."
            )

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

    staged_added_expected = checks.get("staged_added_expected") or []
    am_worktree_modified = checks.get("am_worktree_modified") or []
    pre_push_blockers = checks.get("pre_push_blockers") or []

    # P2 Gm5km: read explicit push-boundary fields. Defaults are the
    # "fully committed" success case so older callers without the new
    # fields still get a sensible verdict.
    push_ready = bool(checks.get("push_ready", True))
    branch_ref_contains_all_expected = bool(
        checks.get("branch_ref_contains_all_expected", True)
    )
    human_review_ready = bool(checks.get("human_review_ready", applied_branch_ready))
    push_boundary_label = "PUSH READY" if push_ready else "NOT PUSH READY"
    push_boundary_reason_lines = []
    if not branch_ref_contains_all_expected:
        push_boundary_reason_lines.append(
            "Staged/index content is not present in "
            f"refs/heads/{branch_name}."
        )
        push_boundary_reason_lines.append(
            "A plain `git push` would omit these paths."
        )
    if pre_push_blockers:
        push_boundary_reason_lines.append(
            "Pre-push blockers are present (see Pre-push Blockers section)."
        )

    lines = [
        f"# Temp-Worktree Applied Branch Verifier",
        f"",
        f"**Status:** {verdict}",
        f"**Push Boundary:** **{push_boundary_label}**",
        f"**Branch-ref contains all expected:** "
        f"{'✅ yes' if branch_ref_contains_all_expected else '❌ no'}",
        f"**Human review ready:** {'✅ yes' if human_review_ready else '❌ no'}",
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
        f"**{push_boundary_label}**",
        f"",
    ]
    if push_boundary_reason_lines:
        lines.extend([
            f"**Why NOT PUSH READY:**",
            f"",
        ])
        for reason in push_boundary_reason_lines:
            lines.append(f"- {reason}")
        lines.append(f"")
    lines.extend([
        f"",
        f"## Changed Files",
        f"",
        f"**Expected:** {len(changed_files_expected)}",
    ])
    for f in sorted(changed_files_expected):
        lines.append(f"- `{f}`")

    lines.extend([
        f"",
        f"**Actual applied (branch tree + verified index/worktree):** {len(changed_files_actual)}",
    ])
    for f in sorted(changed_files_actual):
        lines.append(f"- `{f}`")

    staged_added_expected = checks.get("staged_added_expected") or []
    am_worktree_modified_paths = checks.get("am_worktree_modified") or []
    pre_push_blockers = checks.get("pre_push_blockers") or []

    if staged_added_expected or am_worktree_modified_paths:
        lines.extend([
            f"",
            f"## Review Diff Sources",
            f"",
            f"Expected staged-added files are part of the ready state. "
            f"The branch HEAD may still equal the base while staged/index changes exist. "
            f"AM-status expected files (added in index, modified in worktree) "
            f"have a separate unstaged/worktree diff that must be reviewed.",
            f"",
            f"**Staged-added expected:** {len(staged_added_expected)}",
        ])
        for f in sorted(staged_added_expected):
            lines.append(f"- `{f}`")

        # P1 GkQhl: render the actual staged/index diff body so a
        # reviewer cannot miss what content is in the index.
        if staged_added_expected:
            _idx_stat = generated_human_commands.get("git_index_diff_stat", "")
            _idx_body = generated_human_commands.get("git_index_diff", "")
            lines.extend([
                f"",
                f"### Staged Additions Diff Content",
                f"",
                f"Actual content of staged/index additions. " +
                f"This is what the human reviewer would see in `git diff --cached`. " +
                f"A plain `git push` will NOT carry this content unless the staged " +
                f"changes are first committed to refs/heads/{branch_name}.",
                f"",
                f"**Staged-added expected paths:**",
            ])
            for f in sorted(staged_added_expected):
                lines.append(f"- `{f}`")
            lines.extend([
                f"",
                f"**Staged/index diff stat:**",
                f"```",
                _idx_stat or "(empty)",
                f"```",
            ])
            if _idx_body:
                lines.extend([
                    f"",
                    f"**Staged/index diff body (bounded excerpt):**",
                    f"```diff",
                    _idx_body,
                    f"```",
                ])
        if am_worktree_modified_paths:
            lines.extend([
                f"",
                f"**AM (added+modified) expected:** {len(am_worktree_modified_paths)}",
            ])
            for f in sorted(am_worktree_modified_paths):
                lines.append(f"- `{f}`")

    lines.extend([
        f"",
        f"## Human Review Commands",
        f"",
        f"```bash",
        f"# View branch tree diff summary",
        f"git -C {repo_root} diff --stat {expected_base_sha}...refs/heads/{branch_name}",
        f"#",
        f"# View branch tree full diff",
        f"git -C {repo_root} diff {expected_base_sha}...refs/heads/{branch_name}",
        f"#",
        f"# View staged/index diff (for staged-added mock edits)",
        f"git -C {repo_root} diff --cached --stat",
        f"git -C {repo_root} diff --cached",
        f"#",
        f"# View unstaged/worktree diff (for AM-status mock edits)",
        f"git -C {repo_root} diff --stat",
        f"git -C {repo_root} diff",
        f"#",
        f"# View git status for staged/index and worktree state",
        f"git -C {repo_root} status --short",
        f"#",
        f"# Suggested tests",
        f"{generated_human_commands.get('suggested_tests', '')}",
        f"```",
    ])

    if pre_push_blockers:
        lines.extend([
            "",
            "## Pre-push Blockers",
            "",
            "**⚠️ The branch HEAD does NOT carry the staged file content yet.** "
            "A plain `git push origin {branch}` would push a no-op PR. "
            "Resolve these blockers before pushing:",
        ])
        for blk in pre_push_blockers:
            lines.append(f"- **{blk['kind']}**: {blk['human_action']}")
            for p in blk.get("paths", []):
                lines.append(f"  - `{p}`")

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
    branch_tree_diff_stat = ""
    branch_tree_diff = ""
    git_index_diff_stat = ""
    git_index_diff = ""
    git_status_short = ""
    suggested_tests = "pytest tests/ -q  # run from repo root"

    try:
        r = _run_git(
            repo_root,
            "diff", "--stat",
            f"{expected_base_sha}...refs/heads/{branch_name}",
        )
        if r.returncode == 0:
            branch_tree_diff_stat = r.stdout.strip()
    except Exception:
        pass

    try:
        r = _run_git(
            repo_root,
            "diff",
            f"{expected_base_sha}...refs/heads/{branch_name}",
        )
        if r.returncode == 0:
            branch_tree_diff = r.stdout.strip()
    except Exception:
        pass

    try:
        r = _run_git(repo_root, "diff", "--cached", "--stat")
        if r.returncode == 0:
            git_index_diff_stat = r.stdout.strip()
    except Exception:
        pass

    try:
        r = _run_git(repo_root, "diff", "--cached")
        if r.returncode == 0:
            git_index_diff = r.stdout.strip()
    except Exception:
        pass

    try:
        r = _run_git(repo_root, "status", "--short")
        if r.returncode == 0:
            git_status_short = r.stdout.strip()
    except Exception:
        pass

    # Unstaged worktree diff (P2 Gm0q4) — captures worktree content that
    # differs from the index. For AM-status expected files, the staged
    # content and worktree content may differ. We surface both so the
    # human reviewer can see what was actually changed on disk vs what
    # is staged.
    unstaged_worktree_diff_stat = ""
    unstaged_worktree_diff = ""
    try:
        r = _run_git(repo_root, "diff", "--stat")
        if r.returncode == 0:
            unstaged_worktree_diff_stat = r.stdout.strip()
    except Exception:
        pass

    try:
        r = _run_git(repo_root, "diff")
        if r.returncode == 0:
            unstaged_worktree_diff = r.stdout.strip()
    except Exception:
        pass

    review_diff_sources = {
        "branch_tree_diff": {
            "stat_key": "branch_tree_diff_stat",
            "diff_key": "branch_tree_diff",
            "command_stat": f"git -C {repo_root} diff --stat {expected_base_sha}...refs/heads/{branch_name}",
            "command_diff": f"git -C {repo_root} diff {expected_base_sha}...refs/heads/{branch_name}",
            "may_be_empty_when_staged_index_changes_exist": bool(checks.get("staged_added_expected")),
        },
        "staged_index_diff": {
            "stat_key": "git_index_diff_stat",
            "diff_key": "git_index_diff",
            "command_stat": f"git -C {repo_root} diff --cached --stat",
            "command_diff": f"git -C {repo_root} diff --cached",
            "covers_staged_added_expected": bool(checks.get("staged_added_expected")),
        },
        "unstaged_worktree_diff": {
            "stat_key": "unstaged_worktree_diff_stat",
            "diff_key": "unstaged_worktree_diff",
            "command_stat": f"git -C {repo_root} diff --stat",
            "command_diff": f"git -C {repo_root} diff",
            "covers_am_status_expected": bool(checks.get("am_worktree_modified")),
        },
        "status": {
            "key": "git_status_short",
            "command": f"git -C {repo_root} status --short",
        },
        "note": (
            "For staged-added mock edits, the branch tree diff may be empty "
            "while the staged/index diff carries the expected file content. "
            "For AM-status expected files, the unstaged/worktree diff "
            "captures on-disk changes that are not yet staged."
        ),
    }

    generated_human_commands = {
        "git_diff_stat": branch_tree_diff_stat,
        "git_diff": branch_tree_diff[:2000] + ("..." if len(branch_tree_diff) > 2000 else ""),
        "branch_tree_diff_stat": branch_tree_diff_stat,
        "branch_tree_diff": branch_tree_diff[:2000] + ("..." if len(branch_tree_diff) > 2000 else ""),
        "git_index_diff_stat": git_index_diff_stat,
        "git_index_diff": git_index_diff[:2000] + ("..." if len(git_index_diff) > 2000 else ""),
        "unstaged_worktree_diff_stat": unstaged_worktree_diff_stat,
        "unstaged_worktree_diff": unstaged_worktree_diff[:2000] + ("..." if len(unstaged_worktree_diff) > 2000 else ""),
        "git_status_short": git_status_short,
        "review_diff_sources": review_diff_sources,
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
