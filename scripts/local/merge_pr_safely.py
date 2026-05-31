#!/usr/bin/env python3
"""
merge_pr_safely.py — v1
=======================
Report-only PR readiness orchestrator.

Composes existing AED governance tools (waiter, PMG, review-comment gate,
final gate) into one bounded, auditable script that emits a verified safe
merge command but does NOT execute the merge.

v1 scope (verifier + merge-command emitter only):
  - Reads PR metadata and checks
  - Inspects review threads
  - Runs existing wait_for_pr_ready.py
  - Runs existing verify_final_head_merge_command.py
  - Emits exact safe gh pr merge command if READY
  - Refuses --admin always

NOT implemented in v1:
  - Actual merge execution
  - Branch update/rebase mutation
  - Stale-thread resolution
  - Any GitHub state mutation

Usage:
    python3 scripts/local/merge_pr_safely.py \\
        --repo Slideshow11/Automated-Edge-Discovery \\
        --repo-root /path/to/repo \\
        --pr-number 368 \\
        --output-json /tmp/aed_runs/pr368_merge/status.json \\
        --output-md /tmp/aed_runs/pr368_merge/status.md

Exit codes:
    0  — report written (any status)
    1  — missing required argument
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_DEFAULT = "Slideshow11/Automated-Edge-Discovery"
HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# ------------------------------------------------------------------
# Status constants
# ------------------------------------------------------------------
STATUS_SAFE_MERGE_COMMAND_READY       = "SAFE_MERGE_COMMAND_READY"
STATUS_HOLD_WAITER_NOT_READY          = "HOLD_WAITER_NOT_READY"
STATUS_HOLD_GIT_DIRTY                 = "HOLD_GIT_DIRTY"
STATUS_HOLD_INVALID_REPO_ROOT         = "HOLD_INVALID_REPO_ROOT"
STATUS_HOLD_COMMAND_VERIFICATION_FAILED = "HOLD_COMMAND_VERIFICATION_FAILED"
STATUS_ERROR_TOOL_FAILURE             = "ERROR_TOOL_FAILURE"


# ------------------------------------------------------------------
# Subprocess helpers (shell=False only)
# ------------------------------------------------------------------

def _run(argv: List[str], check: bool = True, cwd: Optional[str] = None,
         timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a command. Always shell=False. Returns CompletedProcess."""
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            shell=False,
            cwd=cwd,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"command timed out after {timeout}s: {' '.join(argv)}")
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed (rc={result.returncode}): "
            f"{' '.join(argv)}: {result.stderr.strip()}"
        )
    return result


# ------------------------------------------------------------------
# Admin guard
# ------------------------------------------------------------------

def reject_admin(argv: List[str]) -> None:
    """Raise ValueError if --admin appears anywhere in argv or string args."""
    for arg in argv:
        if isinstance(arg, str) and "--admin" in arg:
            raise ValueError("--admin is forbidden in merge_pr_safely.py")


# ------------------------------------------------------------------
# Repo-root validation
# ------------------------------------------------------------------

def validate_repo_root(repo_root: str) -> Tuple[bool, str]:
    """
    Check that repo_root is a valid git worktree with clean status.
    Returns (ok, detail).
    """
    path = Path(repo_root).resolve()
    if not path.exists():
        return False, f"repo-root does not exist: {repo_root}"

    # Must be a git repository
    proc = _run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        check=False,
        timeout=10,
    )
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        return False, f"repo-root is not a git worktree: {repo_root}"

    # Must be clean
    proc2 = _run(
        ["git", "-C", str(path), "status", "--porcelain"],
        check=False,
        timeout=10,
    )
    if proc2.returncode != 0:
        return False, f"git status failed in repo-root: {proc2.stderr}"
    if proc2.stdout.strip():
        return False, f"repo-root worktree is dirty: {proc2.stdout.strip()}"

    return True, "ok"


# ------------------------------------------------------------------
# PR head SHA
# ------------------------------------------------------------------

def fetch_pr_head_sha(repo: str, pr_number: int) -> str:
    """Fetch the live PR head SHA via gh pr view."""
    result = _run(
        ["gh", "pr", "view", str(pr_number), "--repo", repo,
         "--json", "headRefOid", "--jq", ".headRefOid"],
        check=True,
        timeout=20,
    )
    sha = result.stdout.strip()
    if not HEX_SHA_RE.match(sha):
        raise RuntimeError(f"gh pr view returned invalid SHA: {sha!r}")
    return sha


# ------------------------------------------------------------------
# Waiter
# ------------------------------------------------------------------

def run_waiter(
    repo: str,
    repo_root: str,
    pr_number: int,
    head_sha: str,
    timeout_minutes: int,
    poll_seconds: int,
    ignore_users: Optional[str],
    output_dir: str,
) -> Tuple[str, dict]:
    """
    Invoke wait_for_pr_ready.py with all required gates.
    Returns (waiter_status, waiter_result_dict).
    """
    waiter_script = Path(__file__).parent / "wait_for_pr_ready.py"
    if not waiter_script.exists():
        return STATUS_ERROR_TOOL_FAILURE, {"error": f"waiter script not found: {waiter_script}"}

    waiter_json = os.path.join(output_dir, "waiter_status.json")
    waiter_md   = os.path.join(output_dir, "waiter_status.md")

    cmd = [
        sys.executable, str(waiter_script),
        "--pr-number", str(pr_number),
        "--repo", repo,
        "--repo-root", repo_root,
        "--timeout-minutes", str(timeout_minutes),
        "--poll-seconds", str(poll_seconds),
        "--require-review-comments-clean",
        "--require-pmg",
        "--require-final-gates",
        "--output-json", waiter_json,
        "--output-md", waiter_md,
    ]
    if ignore_users:
        cmd.extend(["--ignore-users", ignore_users])

    # Remove any stale waiter output from previous runs so a prior
    # READY_TO_MERGE_CANDIDATE cannot be reused after a failure.
    for stale in [waiter_json, waiter_md]:
        if os.path.exists(stale):
            os.remove(stale)

    result = _run(cmd, check=False, timeout=timeout_minutes * 60 + 30)
    if result.returncode != 0:
        return STATUS_ERROR_TOOL_FAILURE, {
            "error": f"waiter subprocess exited {result.returncode}",
            "waiter_returncode": result.returncode,
            "waiter_stderr": result.stderr.strip()[:500],
        }

    try:
        with open(waiter_json, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return STATUS_ERROR_TOOL_FAILURE, {"error": f"waiter JSON not readable: {e}"}

    status = data.get("status", "")
    return status, data


# ------------------------------------------------------------------
# Merge command builder
# ------------------------------------------------------------------

def build_safe_merge_command(pr_number: int, repo: str, head_sha: str) -> str:
    """
    Build the exact safe merge command.
    NEVER includes --admin.
    """
    return (
        f"gh pr merge {pr_number} "
        f"--repo {repo} "
        f"--squash "
        f"--delete-branch "
        f"--match-head-commit {head_sha}"
    )


# ------------------------------------------------------------------
# Verify merge command
# ------------------------------------------------------------------

def verify_merge_command(
    repo: str,
    pr_number: int,
    head_sha: str,
    pmg_state_json: Optional[str],
    output_dir: str,
) -> Tuple[bool, dict]:
    """
    Run verify_final_head_merge_command.py to verify the merge command.
    Returns (verified, verifier_result_dict).
    """
    verifier_script = Path(__file__).parent / "verify_final_head_merge_command.py"
    if not verifier_script.exists():
        return False, {"error": f"verifier script not found: {verifier_script}"}

    verify_json = os.path.join(output_dir, "command_verifier.json")
    cmd = [
        sys.executable, str(verifier_script),
        "--repo", repo,
        "--pr-number", str(pr_number),
        "--reported-head-sha", head_sha,
        "--output-json", verify_json,
    ]
    if pmg_state_json:
        cmd.extend(["--pmg-guard-state-json", pmg_state_json, "--require-pmg"])

    _run(cmd, check=False, timeout=30)
    try:
        with open(verify_json, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return False, {"error": f"verifier JSON not readable: {e}"}

    recommendation = data.get("recommendation", "")
    verified = (recommendation == "MERGE_READY_CANDIDATE")
    return verified, data


# ------------------------------------------------------------------
# Report writers
# ------------------------------------------------------------------

def write_json_report(path: str, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def write_md_report(path: str, data: dict) -> None:
    lines = [
        "# merge_pr_safely.py — PR Readiness Report",
        "",
        f"**Status**: `{data['status']}`",
        "",
        "## PR",
        f"- **Number**: {data['pr_number']}",
        f"- **Repository**: {data['repo']}",
        f"- **Head SHA**: `{data['head_sha']}`",
        "",
        "## Repo Root",
        f"- **Path**: {data['repo_root']}",
        "",
        "## Waiter",
        f"- **Status**: `{data['waiter_status']}`",
        f"- **Report**: {data.get('waiter_json_path', 'n/a')}",
        "",
        "## Merge Command",
    ]
    if data.get("safe_merge_command_text"):
        lines.append(f"```\n{data['safe_merge_command_text']}\n```")
    else:
        lines.append("*No merge command ready in this status.*")

    lines.extend([
        "",
        "## Verification",
        f"- **Command verified**: {data.get('command_verified', False)}",
        f"- **Admin refused**: {data.get('admin_refused', False)}",
        "",
        "## Safety",
        f"- **mutated_github**: {data['mutated_github']}",
        f"- **merged**: {data['merged']}",
        f"- **execute_merge_supported**: {data['execute_merge_supported']}",
        "",
        "## Notes",
        "- **v1 does not execute merges** — this script is a read-only verifier and merge-command emitter.",
        "- **--admin is forbidden** — any merge command containing --admin is rejected at every layer.",
        "- No GitHub state is mutated. No comments deleted. No reviews dismissed. No threads resolved.",
        "- Branch protection and workflow files are unchanged.",
    ])

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines))


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safe PR readiness orchestrator (v1 — report + merge-command only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo", required=True,
                        help="GitHub repository in 'owner/name' form")
    parser.add_argument("--repo-root", required=True,
                        help="Absolute path to the AED repository root")
    parser.add_argument("--pr-number", type=int, required=True,
                        help="GitHub PR number")
    parser.add_argument("--timeout-minutes", type=int, default=15,
                        help="Max wait time in minutes (default: 15)")
    parser.add_argument("--poll-seconds", type=int, default=30,
                        help="Seconds between CI polls (default: 30)")
    parser.add_argument("--ignore-users", default=None,
                        help="Comma-separated users to ignore in review-comment gate")
    parser.add_argument("--output-json", required=True,
                        help="Path to JSON report")
    parser.add_argument("--output-md", default=None,
                        help="Path to Markdown report (optional)")
    args = parser.parse_args()

    # Normalize output path
    output_dir = os.path.dirname(os.path.abspath(args.output_json))
    md_path = args.output_md or args.output_json.replace(".json", ".md")

    # ---- 1. Admin guard ----
    try:
        reject_admin(sys.argv)
    except ValueError as e:
        report = {
            "status": STATUS_ERROR_TOOL_FAILURE,
            "error": str(e),
            "admin_refused": True,
            "pr_number": args.pr_number,
            "repo": args.repo,
            "repo_root": args.repo_root,
            "head_sha": "",
            "waiter_status": "",
            "waiter_json_path": "",
            "safe_merge_command_text": "",
            "command_verified": False,
            "execute_merge_supported": False,
            "merged": False,
            "mutated_github": False,
        }
        write_json_report(args.output_json, report)
        write_md_report(md_path, report)
        print(f"ERROR: {e}", file=sys.stderr)
        return 0

    # ---- 2. Repo-root validation ----
    ok, detail = validate_repo_root(args.repo_root)
    if not ok:
        report = {
            "status": STATUS_HOLD_INVALID_REPO_ROOT,
            "error": detail,
            "admin_refused": False,
            "pr_number": args.pr_number,
            "repo": args.repo,
            "repo_root": args.repo_root,
            "head_sha": "",
            "waiter_status": "",
            "waiter_json_path": "",
            "safe_merge_command_text": "",
            "command_verified": False,
            "execute_merge_supported": False,
            "merged": False,
            "mutated_github": False,
        }
        write_json_report(args.output_json, report)
        write_md_report(md_path, report)
        print(f"HOLD_INVALID_REPO_ROOT: {detail}", file=sys.stderr)
        return 0

    # ---- 3. Fetch PR head SHA ----
    try:
        head_sha = fetch_pr_head_sha(args.repo, args.pr_number)
    except Exception as e:
        report = {
            "status": STATUS_ERROR_TOOL_FAILURE,
            "error": str(e),
            "admin_refused": False,
            "pr_number": args.pr_number,
            "repo": args.repo,
            "repo_root": args.repo_root,
            "head_sha": "",
            "waiter_status": "",
            "waiter_json_path": "",
            "safe_merge_command_text": "",
            "command_verified": False,
            "execute_merge_supported": False,
            "merged": False,
            "mutated_github": False,
        }
        write_json_report(args.output_json, report)
        write_md_report(md_path, report)
        print(f"ERROR_TOOL_FAILURE: {e}", file=sys.stderr)
        return 0

    # ---- 4. Run waiter ----
    waiter_status, waiter_data = run_waiter(
        repo=args.repo,
        repo_root=args.repo_root,
        pr_number=args.pr_number,
        head_sha=head_sha,
        timeout_minutes=args.timeout_minutes,
        poll_seconds=args.poll_seconds,
        ignore_users=args.ignore_users,
        output_dir=output_dir,
    )
    waiter_json_path = os.path.join(output_dir, "waiter_status.json")

    # ---- 5. Determine status ----
    # ERROR_TOOL_FAILURE means the waiter subprocess itself crashed —
    # report it as an error, not as a hold.
    if waiter_status == STATUS_ERROR_TOOL_FAILURE:
        report = {
            "status": STATUS_ERROR_TOOL_FAILURE,
            "admin_refused": False,
            "pr_number": args.pr_number,
            "repo": args.repo,
            "repo_root": args.repo_root,
            "head_sha": head_sha,
            "waiter_status": waiter_status,
            "waiter_json_path": waiter_json_path,
            "safe_merge_command_text": "",
            "safe_merge_command_list": [],
            "command_verified": False,
            "execute_merge_supported": False,
            "merged": False,
            "mutated_github": False,
            "waiter_stages": waiter_data.get("stages", []),
        }
        # Forward waiter failure details if present
        if "error" in waiter_data:
            report["error"] = waiter_data["error"]
        if "waiter_returncode" in waiter_data:
            report["waiter_returncode"] = waiter_data["waiter_returncode"]
        if "waiter_stderr" in waiter_data:
            report["waiter_stderr"] = waiter_data["waiter_stderr"]
        write_json_report(args.output_json, report)
        write_md_report(md_path, report)
        print(f"ERROR_TOOL_FAILURE: waiter subprocess failed: {waiter_data.get('error', waiter_status)}")
        return 0

    if waiter_status != "READY_TO_MERGE_CANDIDATE":
        report = {
            "status": STATUS_HOLD_WAITER_NOT_READY,
            "admin_refused": False,
            "pr_number": args.pr_number,
            "repo": args.repo,
            "repo_root": args.repo_root,
            "head_sha": head_sha,
            "waiter_status": waiter_status,
            "waiter_json_path": waiter_json_path,
            "safe_merge_command_text": "",
            "safe_merge_command_list": [],
            "command_verified": False,
            "execute_merge_supported": False,
            "merged": False,
            "mutated_github": False,
            "waiter_stages": waiter_data.get("stages", []),
        }
        write_json_report(args.output_json, report)
        write_md_report(md_path, report)
        print(f"HOLD_WAITER_NOT_READY: waiter returned {waiter_status}")
        return 0

    # ---- 6. Build safe merge command ----
    merge_cmd_text = build_safe_merge_command(args.pr_number, args.repo, head_sha)
    merge_cmd_list = merge_cmd_text.split()

    # ---- 7. Verify merge command ----
    pmg_json = waiter_data.get("pmg_guard_state_json")
    verified, verifier_data = verify_merge_command(
        repo=args.repo,
        pr_number=args.pr_number,
        head_sha=head_sha,
        pmg_state_json=pmg_json,
        output_dir=output_dir,
    )

    if not verified:
        report = {
            "status": STATUS_HOLD_COMMAND_VERIFICATION_FAILED,
            "admin_refused": False,
            "pr_number": args.pr_number,
            "repo": args.repo,
            "repo_root": args.repo_root,
            "head_sha": head_sha,
            "waiter_status": waiter_status,
            "waiter_json_path": waiter_json_path,
            "safe_merge_command_text": merge_cmd_text,
            "safe_merge_command_list": merge_cmd_list,
            "command_verified": False,
            "verification_errors": verifier_data.get("verification_errors", []),
            "execute_merge_supported": False,
            "merged": False,
            "mutated_github": False,
            "waiter_stages": waiter_data.get("stages", []),
        }
        write_json_report(args.output_json, report)
        write_md_report(md_path, report)
        print(f"HOLD_COMMAND_VERIFICATION_FAILED: verifier returned {verifier_data.get('recommendation', '?')}")
        return 0

    # ---- 8. Ready ----
    report = {
        "status": STATUS_SAFE_MERGE_COMMAND_READY,
        "admin_refused": False,
        "pr_number": args.pr_number,
        "repo": args.repo,
        "repo_root": args.repo_root,
        "head_sha": head_sha,
        "waiter_status": waiter_status,
        "waiter_json_path": waiter_json_path,
        "safe_merge_command_text": merge_cmd_text,
        "safe_merge_command_list": merge_cmd_list,
        "command_verified": True,
        "execute_merge_supported": False,   # v1 — merge not implemented
        "merged": False,
        "mutated_github": False,
        "waiter_stages": waiter_data.get("stages", []),
    }
    write_json_report(args.output_json, report)
    write_md_report(md_path, report)
    print(f"SAFE_MERGE_COMMAND_READY: {merge_cmd_text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())