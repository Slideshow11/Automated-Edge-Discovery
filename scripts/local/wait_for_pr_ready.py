#!/usr/bin/env python3
"""
wait_for_pr_ready.py — Read-only PR readiness waiter.

Polls CI, collects evidence, writes JSON/Markdown reports.
Does NOT merge, push, commit, resolve review threads, invoke live Claude,
run autocoder batch, or mutate Hermes.

Usage:
    python3 scripts/local/wait_for_pr_ready.py \\
        --pr-number 336 \\
        --timeout-minutes 30 \\
        --poll-seconds 30 \\
        --require-review-comments-clean \\
        --require-pmg \\
        --require-final-gates \\
        --output-json /tmp/aed_runs/pr336_wait/status.json \\
        --output-md /tmp/aed_runs/pr336_wait/status.md

Exit codes:
    0  — report written (status may be HOLD_* or ERROR_TOOLING)
    1  — required argument missing
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default required CI checks for this repository.
DEFAULT_REQUIRED_CHECKS = [
    "test (3.11)",
    "review-comment-gate",
    "validator",
    "governance-validators",
    "pr-gate-live-smoke",
]

# Repository and working-directory context.
# These are overridden by --repo and --repo-root CLI arguments.
REPO = "Slideshow11/Automated-Edge-Discovery"
HERMES_ROOT = os.path.expanduser("~/.hermes")

# Populate REPO_CONTEXT and REPO_ROOT from CLI args in main().
REPO_CONTEXT: List[str] = []   # ["--repo", REPO] — passed to every gh command
REPO_ROOT: str = ""            # Absolute path to the AED repo root

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------

STATUS_READY_FOR_FINAL_GATES = "READY_FOR_FINAL_GATES"
STATUS_READY_TO_MERGE_CANDIDATE = "READY_TO_MERGE_CANDIDATE"
STATUS_READY_PR_ALREADY_MERGED = "READY_PR_ALREADY_MERGED"
STATUS_HOLD_CI_PENDING = "HOLD_CI_PENDING"
STATUS_HOLD_CI_FAILED = "HOLD_CI_FAILED"
STATUS_HOLD_REVIEW_COMMENTS_BLOCKED = "HOLD_REVIEW_COMMENTS_BLOCKED"
STATUS_HOLD_REVIEW_COMMENTS_INCONCLUSIVE = "HOLD_REVIEW_COMMENTS_INCONCLUSIVE"
STATUS_HOLD_REVIEW_COMMENTS_REQUIRED_BY_BRANCH_PROTECTION = "HOLD_REVIEW_COMMENTS_REQUIRED_BY_BRANCH_PROTECTION"
STATUS_HOLD_PMG_DIRTY = "HOLD_PMG_DIRTY"
STATUS_HOLD_HEAD_CHANGED = "HOLD_HEAD_CHANGED"
STATUS_HOLD_TIMEOUT = "HOLD_TIMEOUT"
STATUS_HOLD_PR_NOT_OPEN = "HOLD_PR_NOT_OPEN"

STATUS_ERROR_TOOLING = "ERROR_TOOLING"

# ---------------------------------------------------------------------------
# Subprocess helpers (shell=False only)
# ---------------------------------------------------------------------------

def gh_run(args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    """
    Run a gh command. Always uses shell=False.

    IMPORTANT: When check=False, the caller is responsible for inspecting
    result.returncode. This is intentional for gh pr checks (exit code 8 means
    "checks pending" but data is still valid) and for get_pr_state / get_live_head_sha
    where a nonzero exit must be caught and converted into a structured HOLD/ERROR
    status rather than raising before JSON/MD reports can be written.

    All calls include explicit --repo context from REPO_CONTEXT so the waiter
    works from any working directory.
    """
    GH_REPO_SUBCMDS = {"pr", "issue", "api", "run", "secret", "label", "milestone", "release"}

    def _add_repo_context(args: List[str]) -> List[str]:
        """Add --repo after the gh subcommand, but only for gh API subcommands.

        PMG compare/snapshot and other non-gh-script helpers do not accept --repo.
        """
        if not args:
            return args
        # args[0] is the gh subcommand (e.g. "pr", "issue", "compare")
        if args[0] in GH_REPO_SUBCMDS and "--repo" not in args:
            return args + REPO_CONTEXT
        return args

    result = subprocess.run(
        ["gh"] + _add_repo_context(args),
        capture_output=True,
        text=True,
        shell=False,
    )
    # gh pr checks returns exit code 8 when checks are pending — this is not
    # an error, it means the data was returned successfully but CI hasn't finished.
    # Treat it as success so the caller can parse the data normally.
    if result.returncode != 0 and result.returncode != 8:
        if check:
            raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def run_external_script(
    cmd: List[str],
    check: bool = True,
    cwd: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """Run an external Python script. Always uses shell=False."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        shell=False,
        cwd=cwd or REPO_ROOT or None,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"command {' '.join(cmd)} failed: {result.stderr.strip()}")
    return result


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def get_pr_state(pr_number: int) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetch the current PR state (open, merged, closed) and head SHA.

    Returns:
        (state, head_sha) — state is 'open', 'merged', 'closed', or None if error
    """
    try:
        result = gh_run(
            ["pr", "view", str(pr_number), "--json", "state,headRefOid", "--jq", "{state:.state,head:.headRefOid}"],
            check=True,
        )
        data = json.loads(result.stdout.strip())
        return data.get("state", "").lower(), data.get("head", "")
    except Exception:
        return None, None


def get_live_head_sha(pr_number: int) -> str:
    """Fetch the current head SHA of a PR via gh."""
    result = gh_run(
        ["pr", "view", str(pr_number), "--json", "headRefOid", "--jq", ".headRefOid"],
        check=True,
    )
    return result.stdout.strip()


def get_branch_protection(repo: str, branch: str) -> Optional[dict]:
    """
    Fetch branch protection settings for the given repo and branch.

    Returns the protection dict or None if the API call fails.
    """
    try:
        result = gh_run(
            ["api", f"repos/{repo}/branches/{branch}/protection"],
            check=True,
        )
        return json.loads(result.stdout.strip())
    except Exception:
        return None


def conversation_resolution_required(repo: str, base_branch: str) -> bool:
    """
    Return True if the base branch has required_conversation_resolution enabled
    in its branch protection settings.

    Catches all errors (network, auth, parse) and returns False so a failure
    to read branch protection is treated as "not required" — fail open on
    tooling errors, fail closed on actual policy.
    """
    try:
        protection = get_branch_protection(repo, base_branch)
        if protection is None:
            return False
        return bool(protection.get("required_conversation_resolution", {}).get("enabled", False))
    except Exception:
        return False


def poll_ci_checks(
    pr_number: int,
    required_checks: List[str],
    timeout_minutes: int,
    poll_seconds: int,
) -> Tuple[str, Dict, Optional[str]]:
    """
    Poll CI checks until all required checks are complete, or timeout reached.

    Returns:
        (status, ci_checks_dict, error_detail)

    ci_checks_dict structure:
        { "checks": [ {name, state, conclusion, link}, ... ], "polled_at": timestamp }

    Possible status values:
        STATUS_READY_FOR_FINAL_GATES   — all required checks passed
        STATUS_HOLD_CI_PENDING         — at least one required check still pending, within timeout
        STATUS_HOLD_CI_FAILED          — a required check failed/cancelled/skipped/missing/unknown
        STATUS_HOLD_TIMEOUT            — timeout reached with pending checks remaining
        STATUS_ERROR_TOOLING          — gh command failed
    """
    deadline = time.time() + (timeout_minutes * 60)

    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return STATUS_HOLD_TIMEOUT, {"checks": [], "polled_at": datetime.now(timezone.utc).isoformat()}, "timeout reached with pending checks"

        try:
            # Use check=False because gh pr checks returns exit code 8 (checks
            # pending) even when it successfully returns data — this is not an
            # error and must not raise ERROR_TOOLING.
            result = gh_run(
                ["pr", "checks", str(pr_number), "--json", "name,state,link"],
                check=False,
            )
            if result.returncode not in (0, 8):
                return STATUS_ERROR_TOOLING, {"checks": [], "polled_at": datetime.now(timezone.utc).isoformat(), "error": f"gh pr checks failed: {result.stderr.strip()}"}, f"exit code {result.returncode}"
        except RuntimeError as e:
            return STATUS_ERROR_TOOLING, {"checks": [], "polled_at": datetime.now(timezone.utc).isoformat(), "error": str(e)}, str(e)

        raw = result.stdout.strip()
        if not raw:
            checks = []
        else:
            try:
                checks = json.loads(raw)
            except json.JSONDecodeError as e:
                return STATUS_ERROR_TOOLING, {"checks": [], "polled_at": datetime.now(timezone.utc).isoformat(), "error": f"JSON parse failed: {e}"}, f"JSON parse failed: {e}"

        # Group checks by name to handle duplicate entries from parallel workflow runs.
        # If a required check appears multiple times (e.g., two workflows running on the same
        # head), we apply precedence: SUCCESS wins over SKIPPED/FAILURE/PENDING.
        # This prevents a transient SKIPPED entry from overwriting a SUCCESS entry.
        from collections import defaultdict
        checks_by_name: Dict[str, List[Dict]] = defaultdict(list)
        for c in checks:
            checks_by_name[c["name"]].append(c)

        failed_checks = []
        pending_checks = []
        missing_checks = []
        unknown_checks = []

        for name in required_checks:
            records = checks_by_name.get(name, [])
            if not records:
                missing_checks.append(name)
                continue

            # Evaluate all records for this check name using precedence:
            # SUCCESS > PENDING/IN_PROGRESS > FAILURE/CANCELLED/SKIPPED > UNKNOWN
            state_vals = [r.get("state", "").lower() for r in records]

            if any(s == "success" for s in state_vals):
                pass  # at least one SUCCESS — check satisfied
            elif any(s in ("pending", "in_progress", "queued", "requested", "waiting") or not s for s in state_vals):
                pending_checks.append(name)
            elif any(s in ("failure", "cancelled", "skipped", "neutral", "timed_out", "action_required") for s in state_vals):
                failed_checks.append(name)
            else:
                unknown_checks.append(f"{name} (states={state_vals})")

        # During polling window: a missing required check means the CI workflow
        # has not yet posted results for that check (workflow may still be spinning
        # up). Treat as pending — keep polling — so we do not false-fail on a
        # transient missing check (e.g., pr-gate-live-smoke not yet posted).
        #
        # Fail-closed ONLY when:
        #   (a) timeout reached with still-missing checks  → HOLD_TIMEOUT
        #   (b) check appears with only FAILURE/SKIPPED   → HOLD_CI_FAILED
        #   (c) check appears with unknown state          → HOLD_CI_FAILED
        #
        # SKIPPED is fail-closed because a check that ran and was deliberately
        # skipped is not equivalent to "check has not started yet".
        if missing_checks:
            pending_checks.extend(missing_checks)
            # Do NOT add to failed_checks — keep them separate so the
            # fail-closed path (HOLD_CI_FAILED) only triggers on checks that
            # actually appeared with a terminal non-success state.
            missing_checks = []  # consumed into pending_checks

        if failed_checks or unknown_checks:
            reason = []
            if failed_checks:
                reason.append(f"failed/cancelled/skipped: {failed_checks}")
            if unknown_checks:
                reason.append(f"unknown state: {unknown_checks}")
            return STATUS_HOLD_CI_FAILED, {"checks": checks, "polled_at": datetime.now(timezone.utc).isoformat()}, "; ".join(reason)

        if not pending_checks:
            return STATUS_READY_FOR_FINAL_GATES, {"checks": checks, "polled_at": datetime.now(timezone.utc).isoformat()}, None

        if remaining <= poll_seconds:
            return STATUS_HOLD_TIMEOUT, {"checks": checks, "polled_at": datetime.now(timezone.utc).isoformat(), "pending_at_timeout": pending_checks}, f"timeout with pending: {pending_checks}"

        time.sleep(poll_seconds)


def run_review_comment_gate(
    pr_number: int,
    head_sha: str,
    output_json: str,
    output_md: str,
) -> Tuple[str, Dict, Optional[str]]:
    """
    Run check_pr_review_comments.py and return the status.

    Returns:
        (status, gate_result_dict, error_detail)

    Possible statuses:
        STATUS_READY_FOR_FINAL_GATES          — gate clean
        STATUS_HOLD_REVIEW_COMMENTS_BLOCKED   — blocking comments found
        STATUS_HOLD_REVIEW_COMMENTS_INCONCLUSIVE — could not determine
        STATUS_ERROR_TOOLING                   — tool itself failed
    """
    script = Path(__file__).parent / "check_pr_review_comments.py"
    cmd = [
        sys.executable,
        str(script),
        "--repo", REPO,
        "--pr-number", str(pr_number),
        "--reported-head-sha", head_sha,
        "--output-json", output_json,
        "--output-md", output_md,
    ]
    try:
        result = run_external_script(cmd, check=False)
        with open(output_json, "r") as f:
            data = json.load(f)
        status_str = data.get("status", "")
        blockers = data.get("blockers", [])
        if status_str == "REVIEW_COMMENTS_CLEAN":
            return STATUS_READY_FOR_FINAL_GATES, data, None
        elif status_str == "REVIEW_COMMENTS_BLOCKED":
            return STATUS_HOLD_REVIEW_COMMENTS_BLOCKED, data, f"{len(blockers)} blocker(s) found"
        elif status_str == "REVIEW_COMMENTS_INCONCLUSIVE":
            return STATUS_HOLD_REVIEW_COMMENTS_INCONCLUSIVE, data, data.get("thread_api_error", "inconclusive")
        else:
            return STATUS_HOLD_REVIEW_COMMENTS_INCONCLUSIVE, data, f"unknown status: {status_str}"
    except FileNotFoundError:
        return STATUS_ERROR_TOOLING, {}, f"check_pr_review_comments.py not found at {script}"
    except json.JSONDecodeError:
        return STATUS_ERROR_TOOLING, {}, f"check_pr_review_comments.py output was not valid JSON"
    except Exception as e:
        return STATUS_ERROR_TOOLING, {}, str(e)


def run_pmg_compare(before_json: str, output_json: str, output_md: str) -> Tuple[str, Dict, Optional[str]]:
    """
    Run PMG compare and return status.

    Returns:
        (status, pmg_result_dict, error_detail)

    Possible statuses:
        STATUS_READY_FOR_FINAL_GATES — PMG clean
        STATUS_HOLD_PMG_DIRTY          — Hermes state was modified
        STATUS_ERROR_TOOLING          — tool failed
    """
    script = Path(__file__).parent / "check_persistent_mutation_guard.py"
    cmd = [
        sys.executable,
        str(script),
        "compare",
        "--root", HERMES_ROOT,
        "--before", before_json,
        "--output-json", output_json,
        "--output-md", output_md,
    ]
    try:
        result = run_external_script(cmd, check=False)
        with open(output_json, "r") as f:
            data = json.load(f)
        guard_status = data.get("status", "")
        if guard_status == "clean":
            return STATUS_READY_FOR_FINAL_GATES, data, None
        else:
            blocked = data.get("blocked_changes", []) + data.get("blocked", [])
            return STATUS_HOLD_PMG_DIRTY, data, f"PMG status={guard_status}, blocked={blocked}"
    except FileNotFoundError:
        return STATUS_ERROR_TOOLING, {}, f"check_persistent_mutation_guard.py not found at {script}"
    except json.JSONDecodeError:
        return STATUS_ERROR_TOOLING, {}, "PMG compare output was not valid JSON"
    except Exception as e:
        return STATUS_ERROR_TOOLING, {}, str(e)


def run_final_gate_status(
    pr_number: int,
    head_sha: str,
    pmg_state_json: Optional[str],
    review_comments_json: Optional[str],
    output_json: str,
    output_md: str,
) -> Tuple[str, Dict, Optional[str]]:
    """
    Run final_gate_status.py and return the status.

    Returns:
        (status, gate_result_dict, error_detail)
    """
    script = Path(__file__).parent / "final_gate_status.py"
    cmd = [
        sys.executable,
        str(script),
        "--pr-number", str(pr_number),
        "--reported-head-sha", head_sha,
        "--codex-reviewed-sha", head_sha,
        "--output-json", output_json,
        "--output-md", output_md,
    ]
    if pmg_state_json:
        cmd.extend(["--pmg-guard-state-json", pmg_state_json])
    if review_comments_json:
        cmd.extend(["--review-comments-json", review_comments_json])

    try:
        result = run_external_script(cmd, check=False)
        with open(output_json, "r") as f:
            data = json.load(f)
        status_str = data.get("status", "")
        blockers = data.get("blockers", [])
        if status_str == "READY_TO_MERGE":
            return STATUS_READY_TO_MERGE_CANDIDATE, data, None
        elif status_str == "HOLD_PR_NOT_OPEN":
            return STATUS_HOLD_PR_NOT_OPEN, data, "PR is not open"
        else:
            return status_str if status_str.startswith("HOLD_") else f"HOLD_UNKNOWN({status_str})", data, f"final_gate_status={status_str}, blockers={blockers}"
    except FileNotFoundError:
        return STATUS_ERROR_TOOLING, {}, f"final_gate_status.py not found"
    except json.JSONDecodeError:
        return STATUS_ERROR_TOOLING, {}, "final_gate_status.py output was not valid JSON"
    except Exception as e:
        return STATUS_ERROR_TOOLING, {}, str(e)


def run_merge_ready_verifier(
    pr_number: int,
    head_sha: str,
    pmg_state_json: Optional[str],
    output_json: str,
) -> Tuple[str, Dict, Optional[str]]:
    """
    Run verify_final_head_merge_command.py and return status.

    Returns:
        (status, verifier_result_dict, error_detail)
    """
    script = Path(__file__).parent / "verify_final_head_merge_command.py"
    cmd = [
        sys.executable,
        str(script),
        "--pr-number", str(pr_number),
        "--reported-head-sha", head_sha,
        "--output-json", output_json,
    ]
    if pmg_state_json:
        cmd.extend(["--pmg-guard-state-json", pmg_state_json, "--require-pmg"])

    try:
        result = run_external_script(cmd, check=False)
        with open(output_json, "r") as f:
            data = json.load(f)
        recommendation = data.get("recommendation", "")
        state = data.get("state", "")
        if recommendation == "MERGE_READY_CANDIDATE":
            return STATUS_READY_TO_MERGE_CANDIDATE, data, None
        else:
            return "HOLD_VERIFY_FAILED", data, f"recommendation={recommendation}"
    except FileNotFoundError:
        return STATUS_ERROR_TOOLING, {}, f"verify_final_head_merge_command.py not found"
    except json.JSONDecodeError:
        return STATUS_ERROR_TOOLING, {}, "verify output was not valid JSON"
    except Exception as e:
        return STATUS_ERROR_TOOLING, {}, str(e)


def next_action_for_status(status: str, pr_number: int, head_sha: str) -> str:
    """Build the human-readable next action string."""
    if status == STATUS_READY_TO_MERGE_CANDIDATE:
        return f"gh pr merge {pr_number} --squash --delete-branch --match-head-commit {head_sha}"
    elif status == STATUS_READY_PR_ALREADY_MERGED:
        return f"PR #{pr_number} is already merged. Verify main HEAD and post-merge checks. No merge action needed."
    elif status.startswith("HOLD_"):
        return f"Stop and resolve: {status}. Do not merge yet."
    elif status == STATUS_ERROR_TOOLING:
        return "Investigate tooling error in logs. Do not merge until resolved."
    else:
        return f"Unexpected status: {status}. Investigate before proceeding."


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Read-only PR readiness waiter. Polls CI, collects evidence, writes reports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pr-number", type=int, required=True, help="PR number to poll")
    parser.add_argument("--timeout-minutes", type=int, default=30, help="Max wait time in minutes (default: 30)")
    parser.add_argument("--poll-seconds", type=int, default=30, help="Seconds between CI polls (default: 30)")
    parser.add_argument("--require-review-comments-clean", action="store_true", help="Run review-comment gate after CI green")
    parser.add_argument("--require-pmg", action="store_true", help="Run PMG compare")
    parser.add_argument("--require-final-gates", action="store_true", help="Run final_gate_status.py")
    parser.add_argument("--require-merge-ready", action="store_true", help="Run verify_final_head_merge_command.py")
    parser.add_argument(
        "--required-checks",
        type=str,
        default=None,
        help="Comma-separated list of required CI check names (default: AED defaults)",
    )
    parser.add_argument("--output-json", type=str, required=True, help="Path to JSON report (required)")
    parser.add_argument("--output-md", type=str, default=None, help="Path to Markdown report (optional)")
    parser.add_argument(
        "--pmg-snapshot",
        action="store_true",
        help="Take a PMG snapshot at start (used automatically with --require-pmg)",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="GitHub repository in 'owner/name' form (default: Slideshow11/Automated-Edge-Discovery)",
    )
    parser.add_argument(
        "--repo-root",
        type=str,
        default=None,
        help="Absolute path to the AED repository root (default: auto-detected from script location)",
    )

    args = parser.parse_args()

    # Populate global repo context for gh commands and working directory for local scripts.
    global REPO_CONTEXT, REPO_ROOT
    REPO_CONTEXT = ["--repo", args.repo or "Slideshow11/Automated-Edge-Discovery"]
    if args.repo_root:
        REPO_ROOT = str(Path(args.repo_root).resolve())
    else:
        # Auto-detect: script is in <repo_root>/scripts/local/wait_for_pr_ready.py
        REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)

    if args.required_checks:
        required_checks = [c.strip() for c in args.required_checks.split(",") if c.strip()]
    else:
        required_checks = list(DEFAULT_REQUIRED_CHECKS)

    os.makedirs(os.path.dirname(os.path.abspath(args.output_json)), exist_ok=True)
    if args.output_md:
        os.makedirs(os.path.dirname(os.path.abspath(args.output_md)), exist_ok=True)

    report: Dict = {
        "pr_number": args.pr_number,
        "tool_version": 1,
        "status": STATUS_ERROR_TOOLING,
        "stages": [],
    }

    def _add_stage(name: str, status: str, detail: str = "", data: Optional[Dict] = None):
        entry = {
            "stage": name,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if detail:
            entry["detail"] = detail
        if data:
            entry["data"] = data
        report["stages"].append(entry)

    def _write_report(final_status: str, next_action: str):
        report["status"] = final_status
        report["next_safe_action"] = next_action
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        with open(args.output_json, "w") as f:
            json.dump(report, f, indent=2)
        if args.output_md:
            _write_markdown(report, args.output_md)

    def _write_markdown(r: Dict, path: str):
        lines = [
            f"# PR Readiness Report — PR #{r['pr_number']}",
            f"",
            f"**Status**: {r['status']}",
            f"",
            f"**Next safe action**: `{r.get('next_safe_action', 'unknown')}`",
            f"",
            f"## Stages",
            "",
        ]
        for stage in r.get("stages", []):
            lines.append(f"### {stage['stage']} — {stage['status']}")
            lines.append(f"_Polled at: {stage.get('timestamp', '?')}_")
            if stage.get("detail"):
                lines.append(f"_{stage['detail']}_")
            lines.append("")
        with open(path, "w") as f:
            f.write("\n".join(lines))

    try:
        # ---- Stage 0: Read PR state and head SHA ----
        pr_state, initial_head_sha = get_pr_state(args.pr_number)
        report["pr_state"] = pr_state
        report["head_sha"] = initial_head_sha
        _add_stage("pr_state_check", "ok", f"state={pr_state}, SHA={initial_head_sha}")

        if pr_state is None:
            _write_report(STATUS_ERROR_TOOLING, next_action_for_status(STATUS_ERROR_TOOLING, args.pr_number, initial_head_sha or "unknown"))
            sys.exit(0)

        # ---- If PR is already merged, stop immediately ----
        if pr_state == "merged":
            _add_stage("merged_pr_check", STATUS_READY_PR_ALREADY_MERGED, "PR is already merged; CI and review gates are not applicable")
            _write_report(
                STATUS_READY_PR_ALREADY_MERGED,
                next_action_for_status(STATUS_READY_PR_ALREADY_MERGED, args.pr_number, initial_head_sha),
            )
            sys.exit(0)

        # ---- If PR is closed but not merged, stop ----
        if pr_state == "closed":
            _add_stage("closed_pr_check", STATUS_HOLD_PR_NOT_OPEN, "PR is closed and cannot be merged")
            _write_report(
                STATUS_HOLD_PR_NOT_OPEN,
                next_action_for_status(STATUS_HOLD_PR_NOT_OPEN, args.pr_number, initial_head_sha),
            )
            sys.exit(0)

        # ---- Stage PMG snapshot (if needed) ----
        pmg_before_json = None
        if args.require_pmg or args.pmg_snapshot:
            pmg_script = Path(__file__).parent / "check_persistent_mutation_guard.py"
            pmg_before_json = args.output_json.replace(".json", "_pmg_before.json")
            pmg_out = pmg_before_json
            cmd = [
                sys.executable,
                str(pmg_script),
                "snapshot",
                "--root", HERMES_ROOT,
                "--output", pmg_out,
            ]
            try:
                run_external_script(cmd, check=True)
                _add_stage("pmg_snapshot", "ok", f"snapshot at {pmg_before_json}")
            except Exception as e:
                _write_report(STATUS_ERROR_TOOLING, next_action_for_status(STATUS_ERROR_TOOLING, args.pr_number, initial_head_sha))
                sys.exit(0)

        # ---- Stage 1: Poll CI ----
        ci_status, ci_data, ci_error = poll_ci_checks(
            args.pr_number,
            required_checks,
            args.timeout_minutes,
            args.poll_seconds,
        )
        report["ci_checks"] = ci_data
        _add_stage("ci_poll", ci_status, ci_error or "", ci_data)

        if ci_status != STATUS_READY_FOR_FINAL_GATES:
            _write_report(ci_status, next_action_for_status(ci_status, args.pr_number, initial_head_sha))
            sys.exit(0)

        # ---- Stage 2: Re-read head SHA (detect change after CI) ----
        current_head_sha = get_live_head_sha(args.pr_number)
        _add_stage("head_sha_recheck", "ok", f"SHA={current_head_sha}")
        if current_head_sha != initial_head_sha:
            _write_report(STATUS_HOLD_HEAD_CHANGED, next_action_for_status(STATUS_HOLD_HEAD_CHANGED, args.pr_number, current_head_sha))
            sys.exit(0)

        # ---- Stage 3: Review-comment gate (optional) ----
        review_gate_json = args.output_json.replace(".json", "_review_gate.json")
        if args.require_review_comments_clean:
            rg_status, rg_data, rg_error = run_review_comment_gate(
                args.pr_number,
                current_head_sha,
                review_gate_json,
                (args.output_md or "").replace(".md", "_review_gate.md"),
            )
            report["review_comment_gate"] = rg_data
            _add_stage("review_comment_gate", rg_status, rg_error or "", rg_data)
            if rg_status != STATUS_READY_FOR_FINAL_GATES:
                _write_report(rg_status, next_action_for_status(rg_status, args.pr_number, current_head_sha))
                sys.exit(0)

        # ---- Conversation resolution enforcement ----
        # If branch protection requires conversation resolution, the review-comment
        # gate must have been run. Omitting --require-review-comments-clean when the
        # base branch mandates conversation resolution is a policy violation.
        # Detect it here and fail closed.
        repo_name = args.repo or "Slideshow11/Automated-Edge-Discovery"
        # Get base branch name from the PR
        pr_view_result = gh_run(
            ["pr", "view", str(args.pr_number), "--json", "baseRefName", "--jq", ".baseRefName"],
            check=True,
        )
        base_branch = pr_view_result.stdout.strip().strip('"')
        if conversation_resolution_required(repo_name, base_branch) and not args.require_review_comments_clean:
            _add_stage(
                "conversation_resolution_enforcement",
                STATUS_HOLD_REVIEW_COMMENTS_REQUIRED_BY_BRANCH_PROTECTION,
                f"branch protection requires conversation resolution but "
                f"--require-review-comments-clean was not set; set the flag or "
                f"remove the conversation_resolution requirement from the base branch",
            )
            _write_report(
                STATUS_HOLD_REVIEW_COMMENTS_REQUIRED_BY_BRANCH_PROTECTION,
                next_action_for_status(STATUS_HOLD_REVIEW_COMMENTS_REQUIRED_BY_BRANCH_PROTECTION, args.pr_number, current_head_sha),
            )
            sys.exit(0)

        # ---- Stage 4: PMG compare (optional) ----
        pmg_compare_json = args.output_json.replace(".json", "_pmg_compare.json")
        if args.require_pmg and pmg_before_json:
            pmg_status, pmg_data, pmg_error = run_pmg_compare(
                pmg_before_json,
                pmg_compare_json,
                (args.output_md or "").replace(".md", "_pmg_compare.md"),
            )
            report["pmg_compare"] = pmg_data
            _add_stage("pmg_compare", pmg_status, pmg_error or "", pmg_data)
            if pmg_status != STATUS_READY_FOR_FINAL_GATES:
                _write_report(pmg_status, next_action_for_status(pmg_status, args.pr_number, current_head_sha))
                sys.exit(0)

        # ---- Stage 5: Re-read head SHA (final check before final gates) ----
        final_head_sha = get_live_head_sha(args.pr_number)
        _add_stage("head_sha_final", "ok", f"SHA={final_head_sha}")
        if final_head_sha != current_head_sha:
            _write_report(STATUS_HOLD_HEAD_CHANGED, next_action_for_status(STATUS_HOLD_HEAD_CHANGED, args.pr_number, final_head_sha))
            sys.exit(0)

        # ---- Stage 6: final_gate_status.py (optional) ----
        final_gate_json = args.output_json.replace(".json", "_final_gate.json")
        final_gate_md = (args.output_md or "").replace(".md", "_final_gate.md") if args.output_md else None
        if args.require_final_gates:
            fg_status, fg_data, fg_error = run_final_gate_status(
                args.pr_number,
                final_head_sha,
                pmg_compare_json if args.require_pmg else None,
                review_gate_json if args.require_review_comments_clean else None,
                final_gate_json,
                final_gate_md or "",
            )
            report["final_gate_status"] = fg_data
            _add_stage("final_gate_status", fg_status, fg_error or "", fg_data)
            if fg_status not in (STATUS_READY_TO_MERGE_CANDIDATE, STATUS_HOLD_PR_NOT_OPEN):
                _write_report(fg_status, next_action_for_status(fg_status, args.pr_number, final_head_sha))
                sys.exit(0)
            # If HOLD_PR_NOT_OPEN from final_gate_status, the PR merged during the wait.
            # Treat as success since CI was green.
            elif fg_status == STATUS_HOLD_PR_NOT_OPEN:
                _add_stage("complete", STATUS_READY_TO_MERGE_CANDIDATE, "PR merged during wait; CI was green")
                _write_report(
                    STATUS_READY_TO_MERGE_CANDIDATE,
                    next_action_for_status(STATUS_READY_TO_MERGE_CANDIDATE, args.pr_number, final_head_sha),
                )
                sys.exit(0)

        # ---- Stage 7: verify_final_head_merge_command.py ----
        if args.require_final_gates or args.require_merge_ready:
            vr_json = args.output_json.replace(".json", "_merge_verifier.json")
            vr_status, vr_data, vr_error = run_merge_ready_verifier(
                args.pr_number,
                final_head_sha,
                pmg_compare_json if args.require_pmg else None,
                vr_json,
            )
            report["merge_ready_verifier"] = vr_data
            _add_stage("merge_ready_verifier", vr_status, vr_error or "", vr_data)
            if vr_status != STATUS_READY_TO_MERGE_CANDIDATE:
                _write_report(vr_status, next_action_for_status(vr_status, args.pr_number, final_head_sha))
                sys.exit(0)

        # ---- All gates passed ----
        _add_stage("complete", STATUS_READY_TO_MERGE_CANDIDATE, "all gates passed")
        _write_report(
            STATUS_READY_TO_MERGE_CANDIDATE,
            next_action_for_status(STATUS_READY_TO_MERGE_CANDIDATE, args.pr_number, final_head_sha),
        )
        sys.exit(0)

    except Exception as e:
        report["fatal_error"] = str(e)
        report["status"] = STATUS_ERROR_TOOLING
        report["next_safe_action"] = next_action_for_status(STATUS_ERROR_TOOLING, args.pr_number, report.get("head_sha", "unknown"))
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        with open(args.output_json, "w") as f:
            json.dump(report, f, indent=2)
        sys.exit(0)


if __name__ == "__main__":
    main()