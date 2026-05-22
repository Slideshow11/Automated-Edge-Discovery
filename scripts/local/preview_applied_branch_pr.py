#!/usr/bin/env python3
"""
preview_applied_branch_pr.py

Read-only PR preparation preview for applied temp-worktree branches.
Consumes APPLIED_BRANCH_READY evidence from verify_temp_worktree_applied_branch.py
and emits human-review commands, suggested PR title/body, and checklists.

IMPORTANT: This script does NOT push, open PRs, merge, commit, stage files,
apply patches, or invoke Claude. It only reads git state and artifact files.

Exit codes:
  0 — check complete (any status written to output)
  1 — fatal error (missing args, file read error)

Usage:
    python3 scripts/local/preview_applied_branch_pr.py \\
        --repo-root /path/to/repo \\
        --applied-branch-json /tmp/apply_branch_readiness.json \\
        --branch-name apply/smoke-005-2026-05-22 \\
        --base-branch main \\
        --expected-base-sha a1e8bec... \\
        --output-json /tmp/pr_preview.json \\
        --output-md /tmp/pr_preview.md
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

STATE_PR_PREVIEW_READY        = "PR_PREVIEW_READY"
STATE_HOLD_REPO_NOT_FOUND    = "HOLD_REPO_NOT_FOUND"
STATE_HOLD_OUTPUT_INSIDE_REPO = "HOLD_OUTPUT_INSIDE_REPO"
STATE_HOLD_VERIFICATION_MISSING = "HOLD_VERIFICATION_MISSING"
STATE_HOLD_VERIFICATION_INVALID_JSON = "HOLD_VERIFICATION_INVALID_JSON"
STATE_HOLD_VERIFICATION_NOT_READY = "HOLD_VERIFICATION_NOT_READY"
STATE_HOLD_BRANCH_MISMATCH    = "HOLD_BRANCH_MISMATCH"
STATE_HOLD_EXPECTED_BASE_MISMATCH = "HOLD_EXPECTED_BASE_MISMATCH"
STATE_HOLD_BRANCH_MISSING    = "HOLD_BRANCH_MISSING"
STATE_HOLD_BASE_BRANCH_MISSING = "HOLD_BASE_BRANCH_MISSING"
STATE_HOLD_PROTECTED_BRANCH  = "HOLD_PROTECTED_BRANCH"
STATE_HOLD_REPO_DIRTY        = "HOLD_REPO_DIRTY"
STATE_HOLD_CHANGED_FILES_EMPTY = "HOLD_CHANGED_FILES_EMPTY"
STATE_HOLD_CHANGED_FILES_DUPLICATE = "HOLD_CHANGED_FILES_DUPLICATE"
STATE_HOLD_BRANCH_DIFF_MISMATCH = "HOLD_BRANCH_DIFF_MISMATCH"
STATE_HOLD_AED_PLAN_INCLUDED = "HOLD_AED_PLAN_INCLUDED"
STATE_HOLD_FORBIDDEN_FILE_TOUCHED = "HOLD_FORBIDDEN_FILE_TOUCHED"
STATE_HOLD_UNKNOWN           = "HOLD_UNKNOWN"

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


def _git_status_clean(repo_root: Path) -> bool:
    """Return True if repo working tree is clean."""
    r = _run_git(repo_root, "status", "--porcelain")
    return r.returncode == 0 and r.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only PR preparation preview for applied temp-worktree branches."
    )
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--applied-branch-json", required=True,
        help="Path to APPLIED_BRANCH_READY JSON from verify_temp_worktree_applied_branch.py")
    parser.add_argument("--branch-name", required=True)
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--expected-base-sha", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--suggested-pr-title", default=None)
    parser.add_argument("--suggested-pr-body", default=None,
        help="Text or path to a file containing suggested PR body")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Core verification
# ---------------------------------------------------------------------------

def verify(
    repo_root: Path,
    applied_branch_json_path: Path,
    branch_name: str,
    base_branch: str,
    expected_base_sha: str,
) -> tuple[str, dict]:
    """
    Run all PR preview verification checks. Returns (status, checks_dict).
    """
    checks: dict[str, object] = {}
    errors: list[str] = []
    warnings: list[str] = []

    # ── 1. Repo is a git repo ─────────────────────────────────────────────────
    r = _run_git(repo_root, "rev-parse", "--is-inside-work-tree")
    if r.returncode != 0 or r.stdout.strip() != "true":
        return STATE_HOLD_REPO_NOT_FOUND, {"repo_is_git": False}
    checks["repo_is_git"] = True

    # ── 2. Applied branch verification JSON exists and is valid ────────────
    verification = _load_json(applied_branch_json_path)
    if verification is None:
        if not applied_branch_json_path.exists():
            return STATE_HOLD_VERIFICATION_MISSING, {
                "applied_branch_json_exists": False,
            }
        return STATE_HOLD_VERIFICATION_INVALID_JSON, {
            "applied_branch_json_exists": True,
            "valid_json": False,
        }
    checks["applied_branch_json_valid"] = True

    # ── 3. Verification status is APPLIED_BRANCH_READY ────────────────────────
    checks["verification_status"] = verification.get("status")
    if verification.get("status") != "APPLIED_BRANCH_READY":
        return STATE_HOLD_VERIFICATION_NOT_READY, {
            **checks,
            "expected_status": "APPLIED_BRANCH_READY",
        }

    # ── 4. Branch name matches verification JSON ─────────────────────────────
    checks["verification_branch_name"] = verification.get("branch_name")
    if verification.get("branch_name") != branch_name:
        return STATE_HOLD_BRANCH_MISMATCH, {
            **checks,
            "expected_branch_name": branch_name,
            "actual_branch_name": verification.get("branch_name"),
        }
    checks["branch_name_ok"] = True

    # ── 5. Expected base SHA matches verification JSON ───────────────────────
    checks["verification_expected_base_sha"] = verification.get("expected_base_sha")
    if verification.get("expected_base_sha") != expected_base_sha:
        return STATE_HOLD_EXPECTED_BASE_MISMATCH, {
            **checks,
            "expected_base_sha": expected_base_sha,
            "verification_expected_base_sha": verification.get("expected_base_sha"),
        }
    checks["expected_base_sha_ok"] = True

    # ── 6. Local branch exists ───────────────────────────────────────────────
    r = _run_git(repo_root, "branch", "--list", branch_name)
    branch_exists = r.returncode == 0 and r.stdout.strip() != ""
    checks["branch_exists"] = branch_exists
    if not branch_exists:
        return STATE_HOLD_BRANCH_MISSING, {**checks}
    checks["branch_name"] = branch_name

    # ── 7. Base branch resolvable ────────────────────────────────────────────
    r = _run_git(repo_root, "rev-parse", "--verify", f"refs/heads/{base_branch}")
    base_exists = r.returncode == 0
    checks["base_branch_exists"] = base_exists
    if not base_exists:
        return STATE_HOLD_BASE_BRANCH_MISSING, {**checks, "base_branch": base_branch}
    checks["base_branch"] = base_branch

    # ── 8. Branch is not protected ────────────────────────────────────────────
    if branch_name in PROTECTED_BRANCH_NAMES:
        return STATE_HOLD_PROTECTED_BRANCH, {**checks, "branch_name": branch_name}
    checks["branch_not_protected"] = True

    # ── 9. Repo working tree is clean ─────────────────────────────────────────
    repo_clean = _git_status_clean(repo_root)
    checks["repo_git_status_clean"] = repo_clean
    if not repo_clean:
        return STATE_HOLD_REPO_DIRTY, {**checks}
    checks["repo_clean"] = True

    # ── 10. Changed files from verification are non-empty and unique ─────────
    changed_files = verification.get("changed_files_actual") or verification.get("changed_files_expected") or []
    checks["changed_files"] = changed_files
    if not changed_files:
        return STATE_HOLD_CHANGED_FILES_EMPTY, {**checks}
    dups = _check_duplicate_paths(changed_files)
    if dups:
        return STATE_HOLD_CHANGED_FILES_DUPLICATE, {
            **checks,
            "duplicate_paths": dups,
            "changed_files_count": len(changed_files),
        }
    checks["changed_files_unique"] = True

    # ── 11. Branch diff matches verification changed_files ───────────────────
    r = _run_git(repo_root, "diff", "--name-only", f"{expected_base_sha}...refs/heads/{branch_name}")
    branch_diff_files = [f for f in r.stdout.strip().splitlines() if f]
    checks["branch_diff_files"] = branch_diff_files

    branch_set = set(branch_diff_files)
    expected_set = set(changed_files)
    if branch_set != expected_set:
        extra = sorted(branch_set - expected_set)
        missing = sorted(expected_set - branch_set)
        return STATE_HOLD_BRANCH_DIFF_MISMATCH, {
            **checks,
            "extra_files": extra,
            "missing_files": missing,
        }
    checks["branch_diff_matches_expected"] = True

    # ── 12. .aed_plan.md not in branch diff ──────────────────────────────────
    if ".aed_plan.md" in branch_diff_files:
        return STATE_HOLD_AED_PLAN_INCLUDED, {**checks}
    checks["aed_plan_excluded"] = True

    # ── 13. Forbidden files not in branch diff ──────────────────────────────
    task = verification.get("task", {})
    forbidden_files = task.get("forbidden_files", [])
    checks["forbidden_files"] = forbidden_files
    if forbidden_files:
        touched = [f for f in branch_diff_files if f in forbidden_files]
        if touched:
            return STATE_HOLD_FORBIDDEN_FILE_TOUCHED, {
                **checks,
                "forbidden_touched": touched,
            }
    checks["no_forbidden_files_touched"] = True

    # ── All checks passed ─────────────────────────────────────────────────────
    return STATE_PR_PREVIEW_READY, {
        **checks,
        "errors": errors,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------

def _generate_pr_title(branch_name: str, changed_files: list[str]) -> str:
    """Generate a suggested PR title from branch name and changed files."""
    # Use branch name as basis, strip apply/ prefix
    clean_name = branch_name.replace("apply/", "").replace("feat/", "").replace("fix/", "")
    if changed_files:
        return f"{clean_name}: {changed_files[0]}"
    return f"{clean_name}: update"


def _generate_pr_body(
    branch_name: str,
    base_branch: str,
    changed_files: list[str],
    diff_stat: str,
) -> str:
    """Generate a suggested PR body."""
    lines = [
        f"## Summary",
        f"",
        f"Branch: `{branch_name}`",
        f"Base: `{base_branch}`",
        f"",
        f"## Changed files ({len(changed_files)})",
    ]
    for f in sorted(changed_files):
        lines.append(f"- `{f}`")
    lines.extend([
        f"",
        f"## Diff stat",
        f"",
        f"```",
        f"{diff_stat}",
        f"```",
        f"",
        f"## Pre-merge checklist",
        f"",
        f"- [ ] Review diff manually",
        f"- [ ] Confirm PMG clean",
        f"- [ ] Run tests locally",
        f"- [ ] Human approval of push and PR",
    ])
    return "\n".join(lines)


def _make_gh_pr_create_command(repo: str, branch: str, title: str) -> str:
    """Build a gh pr create command as TEXT ONLY."""
    safe_title = title.replace("'", "'\"'\"'")
    lines = [
        f"gh pr create \\",
        f"  --repo {repo} \\",
        f"  --base main \\",
        f"  --head {branch} \\",
        f"  --title '{safe_title}' \\",
        f"  --body-file /dev/null",
    ]
    return "\n".join(lines)


def write_json_output(
    output_path: Path,
    status: str,
    pr_preview_ready: bool,
    checks: dict,
    repo_root: str,
    base_branch: str,
    branch_name: str,
    expected_base_sha: str,
    changed_files: list,
    diff_stat: str,
    generated_commands: dict,
    suggested_pr_title: str,
    suggested_pr_body: str,
    checklist: list,
    errors: list,
    warnings: list,
    generated_at: str,
    safety_statement: str,
) -> None:
    data = {
        "status": status,
        "pr_preview_ready": pr_preview_ready,
        "repo_root": repo_root,
        "base_branch": base_branch,
        "branch_name": branch_name,
        "expected_base_sha": expected_base_sha,
        "changed_files": changed_files,
        "diff_stat": diff_stat,
        "generated_commands": generated_commands,
        "suggested_pr_title": suggested_pr_title,
        "suggested_pr_body": suggested_pr_body,
        "checklist": checklist,
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
    pr_preview_ready: bool,
    checks: dict,
    repo_root: str,
    base_branch: str,
    branch_name: str,
    expected_base_sha: str,
    changed_files: list,
    diff_stat: str,
    generated_commands: dict,
    suggested_pr_title: str,
    suggested_pr_body: str,
    checklist: list,
    errors: list,
    warnings: list,
    generated_at: str,
    safety_statement: str,
) -> None:
    verdict = "✅ PR_PREVIEW_READY" if pr_preview_ready else f"❌ {status}"

    lines = [
        f"# Temp-Worktree Applied Branch PR Preview",
        f"",
        f"**Status:** {verdict}",
        f"",
        f"**Repo:** `{repo_root}`",
        f"**Base branch:** `{base_branch}`",
        f"**Apply branch:** `{branch_name}`",
        f"**Expected base SHA:** `{expected_base_sha}`",
        f"",
        f"## Verdict",
        f"",
        f"**{verdict}**",
        f"",
        f"## Changed Files ({len(changed_files)})",
    ]
    for f in sorted(changed_files):
        lines.append(f"- `{f}`")

    if diff_stat:
        lines.extend([
            f"",
            f"## Diff stat",
            f"",
            f"```",
            f"{diff_stat}",
            f"```",
        ])

    lines.extend([
        f"",
        f"## Suggested PR Title",
        f"",
        f"{suggested_pr_title}",
        f"",
        f"## Suggested PR Body",
        f"",
        suggested_pr_body,
    ])

    lines.extend([
        f"",
        f"## Human Review Commands (TEXT ONLY — NOT EXECUTED)",
        f"",
        f"```bash",
        f"# View full diff",
        f"git -C {repo_root} diff {expected_base_sha}...refs/heads/{branch_name}",
        f"#",
        f"# Push branch (after human approval)",
        f"git -C {repo_root} push origin {branch_name}",
        f"#",
        f"# Suggested gh pr create command (after human approval)",
        generated_commands.get("suggested_pr_create_command_text_only", "gh pr create ..."),
        f"```",
        f"",
        f"## Pre-push Checklist",
        f"",
    ] + [
        f"- [ ] {item}" for item in checklist
    ] + [
        f"",
        f"---",
        f"",
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
    applied_branch_json_path = Path(args.applied_branch_json).resolve()
    branch_name = args.branch_name
    base_branch = args.base_branch
    expected_base_sha = args.expected_base_sha
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
            applied_branch_json_path,
            branch_name,
            base_branch,
            expected_base_sha,
        )
    except Exception as e:
        status = STATE_HOLD_UNKNOWN
        checks = {"fatal_error": str(e)}

    pr_preview_ready = (status == STATE_PR_PREVIEW_READY)

    # Extract info from verification JSON
    verification = _load_json(applied_branch_json_path) or {}
    changed_files = (
        verification.get("changed_files_actual")
        or verification.get("changed_files_expected")
        or []
    )
    diff_stat = ""
    try:
        r = _run_git(repo_root, "diff", "--stat", f"{expected_base_sha}...refs/heads/{branch_name}")
        if r.returncode == 0:
            diff_stat = r.stdout.strip()
    except Exception:
        pass

    # Suggested PR title and body
    suggested_pr_title = args.suggested_pr_title
    if not suggested_pr_title:
        suggested_pr_title = _generate_pr_title(branch_name, changed_files)

    suggested_pr_body = args.suggested_pr_body
    if suggested_pr_body:
        body_path = Path(suggested_pr_body)
        if body_path.exists():
            suggested_pr_body = body_path.read_text(encoding="utf-8")
    if not suggested_pr_body:
        suggested_pr_body = _generate_pr_body(branch_name, base_branch, changed_files, diff_stat)

    # Repo path for gh commands
    repo_name = f"{repo_root.name}"
    gh_repo = "Slideshow11/Automated-Edge-Discovery"

    # Build generated commands (text only, not executed)
    generated_commands = {
        "git_diff_stat": diff_stat,
        "git_diff": "",
        "suggested_pr_create_command_text_only": _make_gh_pr_create_command(gh_repo, branch_name, suggested_pr_title),
        "suggested_pr_view_command_text_only": f"gh pr view {branch_name} --repo {gh_repo}",
        "note": "Commands are TEXT ONLY — not executed by this tool.",
    }

    # Try to get full diff for output
    try:
        r = _run_git(repo_root, "diff", f"{expected_base_sha}...refs/heads/{branch_name}")
        if r.returncode == 0:
            generated_commands["git_diff"] = r.stdout.strip()
    except Exception:
        pass

    # Checklist
    checklist = [
        "Review diff manually",
        "Confirm PMG clean",
        "Run tests locally",
        "Push only after human approval",
        "Open PR only after human approval",
    ]

    errors: list = []
    warnings: list = []
    generated_at = datetime.now(timezone.utc).isoformat()
    safety_statement = (
        "This preview did not push, open a PR, merge, commit, stage files, "
        "apply a patch, or invoke Claude. It only reads git state and artifact files."
    )

    # Write outputs
    try:
        write_json_output(
            output_json, status, pr_preview_ready, checks,
            str(repo_root), base_branch, branch_name, expected_base_sha,
            changed_files, diff_stat, generated_commands,
            suggested_pr_title, suggested_pr_body, checklist,
            errors, warnings, generated_at, safety_statement,
        )
        print(f"JSON: {output_json}")
    except Exception as e:
        print(f"ERROR writing JSON output: {e}", file=sys.stderr)

    try:
        write_md_output(
            output_md, status, pr_preview_ready, checks,
            str(repo_root), base_branch, branch_name, expected_base_sha,
            changed_files, diff_stat, generated_commands,
            suggested_pr_title, suggested_pr_body, checklist,
            errors, warnings, generated_at, safety_statement,
        )
        print(f"Markdown: {output_md}")
    except Exception as e:
        print(f"ERROR writing MD output: {e}", file=sys.stderr)

    print(f"Status: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())