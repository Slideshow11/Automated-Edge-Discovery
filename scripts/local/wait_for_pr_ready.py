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
import signal
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
STATUS_TIMEOUT_WAIT_FOR_READY = "TIMEOUT_WAIT_FOR_READY"
STATUS_TERMINATED_WAIT_FOR_READY = "TERMINATED_WAIT_FOR_READY"
STATUS_INTERRUPTED_WAIT_FOR_READY = "INTERRUPTED_WAIT_FOR_READY"
STATUS_ERROR_UNEXPECTED_EXCEPTION = "ERROR_UNEXPECTED_EXCEPTION"
STATUS_ERROR_TOOL_TIMEOUT = "ERROR_TOOL_TIMEOUT"

# ---------------------------------------------------------------------------
# Review-comment gate — allowed ignore users (auditable suppression allowlist)
# ---------------------------------------------------------------------------

ALLOWED_REVIEW_IGNORE_USERS: set = {"chatgpt-codex-connector[bot]"}
STATUS_HOLD_DISALLOWED_IGNORE_USER = "HOLD_DISALLOWED_IGNORE_USER"


def validate_ignore_users(ignore_users_str: str) -> Tuple[bool, List[str]]:
    """
    Validate --ignore-users value against ALLOWED_REVIEW_IGNORE_USERS.

    Returns:
        (is_valid, disallowed_users)
        is_valid=True  → all users are in the allowlist
        is_valid=False → disallowed_users lists every user not in the allowlist
    """
    if not ignore_users_str:
        return True, []
    requested = [u.strip() for u in ignore_users_str.split(",") if u.strip()]
    disallowed = [u for u in requested if u not in ALLOWED_REVIEW_IGNORE_USERS]
    return len(disallowed) == 0, disallowed


# --------------------------------------------------------------------------
# Report-building helpers
# --------------------------------------------------------------------------

def safe_excerpt(text: str, limit: int = 4000) -> str:
    """Return text truncated to limit chars, with ellipsis if longer."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def build_report(
    status: str,
    pr_number: int,
    started_at: str,
    finished_at: str,
    elapsed_seconds: float,
    timeout_minutes: int,
    poll_seconds: int,
    current_phase: str,
    last_completed_phase: str,
    head_sha: str,
    repo: str,
    output_json: str,
    output_md: str,
    gate_results: Optional[Dict] = None,
    last_known_state: Optional[str] = None,
    ready_to_merge: bool = False,
    merge_allowed: bool = False,
    next_safe_action: str = "",
    fatal_error: Optional[str] = None,
    error_detail: Optional[str] = None,
    error_type: Optional[str] = None,
    tool_command: Optional[str] = None,
    tool_returncode: Optional[int] = None,
    tool_timeout_seconds: Optional[int] = None,
    tool_stdout_excerpt: Optional[str] = None,
    tool_stderr_excerpt: Optional[str] = None,
    missing_artifact: Optional[str] = None,
    malformed_artifact: Optional[str] = None,
    malformed_artifact_error: Optional[str] = None,
) -> Dict:
    """Build a fully-populated report dict."""
    report: Dict = {
        "status": status,
        "ready_to_merge": ready_to_merge,
        "merge_allowed": merge_allowed,
        "repo": repo,
        "pr_number": pr_number,
        "head_sha": head_sha or "",
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "timeout_minutes": timeout_minutes,
        "poll_seconds": poll_seconds,
        "current_phase": current_phase,
        "last_completed_phase": last_completed_phase,
        "gate_results": gate_results or {},
        "last_known_state": last_known_state or "",
        "output_json": output_json,
        "output_md": output_md,
        "mutated_github": False,
        "merged": False,
        "used_admin": False,
        "used_auto": False,
        "next_safe_action": next_safe_action,
    }
    if fatal_error:
        report["fatal_error"] = fatal_error
    if error_detail:
        report["error_detail"] = error_detail
    if error_type:
        report["error_type"] = error_type
    if tool_command:
        report["tool_command"] = tool_command
    if tool_returncode is not None:
        report["tool_returncode"] = tool_returncode
    if tool_timeout_seconds is not None:
        report["tool_timeout_seconds"] = tool_timeout_seconds
    if tool_stdout_excerpt:
        report["tool_stdout_excerpt"] = tool_stdout_excerpt
    if tool_stderr_excerpt:
        report["tool_stderr_excerpt"] = tool_stderr_excerpt
    if missing_artifact:
        report["missing_artifact"] = missing_artifact
    if malformed_artifact:
        report["malformed_artifact"] = malformed_artifact
    if malformed_artifact_error:
        report["malformed_artifact_error"] = malformed_artifact_error
    return report


def write_reports(report: Dict, output_json: str, output_md: str) -> None:
    """Write report to JSON and Markdown paths. Creates parent dirs."""
    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    with open(output_json, "w") as f:
        json.dump(report, f, indent=2)
    if output_md:
        Path(output_md).parent.mkdir(parents=True, exist_ok=True)
        _write_markdown(report, output_md)


def _write_markdown(r: Dict, path: str) -> None:
    """Write a markdown report for the PR readiness waiter."""
    lines = [
        f"# PR Readiness Report — PR #{r['pr_number']}",
        "",
        f"**Status**: {r['status']}",
        "",
        f"**Ready to merge**: {r.get('ready_to_merge', False)}",
        f"**Merge allowed**: {r.get('merge_allowed', False)}",
        "",
        f"**Next safe action**: `{r.get('next_safe_action', 'unknown')}`",
        "",
        "## Summary",
        "",
        f"- **Repository**: {r.get('repo', 'n/a')}",
        f"- **PR number**: {r.get('pr_number', 'n/a')}",
        f"- **Head SHA**: `{r.get('head_sha', 'n/a')}`",
        f"- **Started at**: {r.get('started_at', 'n/a')}",
        f"- **Finished at**: {r.get('finished_at', 'n/a')}",
        f"- **Elapsed seconds**: {r.get('elapsed_seconds', 0.0)}",
        f"- **Timeout minutes**: {r.get('timeout_minutes', 'n/a')}",
        f"- **Poll seconds**: {r.get('poll_seconds', 'n/a')}",
        f"- **Current phase**: {r.get('current_phase', 'n/a')}",
        f"- **Last completed phase**: {r.get('last_completed_phase', 'n/a')}",
        "",
        "## Safety invariants",
        "",
        f"- **mutated_github**: {r.get('mutated_github', False)}",
        f"- **merged**: {r.get('merged', False)}",
        f"- **used_admin**: {r.get('used_admin', False)}",
        f"- **used_auto**: {r.get('used_auto', False)}",
        "",
        "## Gate results",
        "",
    ]
    gate_results = r.get("gate_results", {})
    if gate_results:
        for key, val in gate_results.items():
            lines.append(f"- **{key}**: {val}")
    else:
        lines.append("- _no gate results_")
    lines.append("")

    if r.get("error_detail"):
        lines.extend([
            "## Error detail",
            "",
            f"- **Error type**: {r.get('error_type', 'unknown')}",
            f"- **Error detail**: {r.get('error_detail', '')}",
            "",
        ])

    if r.get("stages"):
        lines.extend([
            "## Stages",
            "",
        ])
        for stage in r["stages"]:
            lines.append(f"### {stage['stage']} — {stage['status']}")
            lines.append(f"_Polled at: {stage.get('timestamp', '?')}_")
            if stage.get("detail"):
                lines.append(f"_{stage['detail']}_")
            lines.append("")

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines))


def fail_report(
    status: str,
    pr_number: int,
    started_at: str,
    finished_at: str,
    elapsed_seconds: float,
    timeout_minutes: int,
    poll_seconds: int,
    current_phase: str,
    last_completed_phase: str,
    head_sha: str,
    repo: str,
    output_json: str,
    output_md: str,
    error_detail: str,
    error_type: Optional[str] = None,
    ready_to_merge: bool = False,
    merge_allowed: bool = False,
    next_safe_action: str = "",
    **kwargs,
) -> Dict:
    """Build and write a failure report, then return it."""
    report = build_report(
        status=status,
        pr_number=pr_number,
        started_at=started_at,
        finished_at=finished_at,
        elapsed_seconds=elapsed_seconds,
        timeout_minutes=timeout_minutes,
        poll_seconds=poll_seconds,
        current_phase=current_phase,
        last_completed_phase=last_completed_phase,
        head_sha=head_sha,
        repo=repo,
        output_json=output_json,
        output_md=output_md,
        ready_to_merge=ready_to_merge,
        merge_allowed=merge_allowed,
        next_safe_action=next_safe_action,
        error_detail=error_detail,
        error_type=error_type,
        **kwargs,
    )
    write_reports(report, output_json, output_md)
    return report


def _sigterm_handler(signum, frame) -> None:
    """Best-effort SIGTERM handler — write report and exit nonzero."""
    global _REPORT, _REPORT_OUTPUT_JSON, _REPORT_OUTPUT_MD
    if _REPORT_OUTPUT_JSON:
        _REPORT["status"] = STATUS_TERMINATED_WAIT_FOR_READY
        _REPORT["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_reports(_REPORT, _REPORT_OUTPUT_JSON, _REPORT_OUTPUT_MD)
    sys.exit(1)


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


def check_conversation_resolution(
    repo: str, pr_number: str, require_conv_resolution: bool
) -> Tuple[str, Dict, Optional[str]]:
    """
    Check whether the PR has any unresolved review threads using GraphQL.

    This directly enforces GitHub's branch protection "Require conversation
    resolution before merging" setting, which blocks gh pr merge without --admin
    when any thread is unresolved.

    Behavior:
    - If branch protection does NOT require conversation resolution: returns
      READY_FOR_FINAL_GATES immediately (no API call needed).
    - If branch protection DOES require conversation resolution AND GraphQL call
      fails: returns HOLD_CONVERSATION_CHECK_UNAVAILABLE (fail closed — if we
      cannot verify, we must not merge).
    - If any unresolved review thread exists: returns HOLD_CONVERSATION_UNRESOLVED.
    - If GraphQL returns hasNextPage=true: returns
      HOLD_CONVERSATION_CHECK_PAGINATION_REQUIRED (pagination not yet implemented).
    - If all threads resolved: returns READY_FOR_FINAL_GATES.
    """
    if not require_conv_resolution:
        return STATUS_READY_FOR_FINAL_GATES, {}, ""

    owner, name = repo.split("/", 1)

    try:
        query = """query PullRequestReviewThreads($owner:String!,$name:String!,$number:Int!) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      reviewThreads(first:100) {
        pageInfo { hasNextPage }
        nodes {
          id
          isResolved
          isOutdated
          comments(first:10) {
            nodes {
              id
              body
              author { login }
            }
          }
        }
      }
    }
  }
}"""
        result = gh_run(
            ["api", "graphql", "-f", f"query={query}", "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={pr_number}"],
            check=True,
        )
        data = json.loads(result.stdout.strip())
        threads_data = (
            data.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
        )
        page_info = threads_data.get("pageInfo", {})
        if page_info.get("hasNextPage", False):
            return (
                "HOLD_CONVERSATION_CHECK_PAGINATION_REQUIRED",
                {},
                "reviewThreads pageInfo.hasNextPage=true; pagination not implemented; resolve all threads or implement cursor-based pagination before merging",
            )
        threads = threads_data.get("nodes", [])
        unresolved = [t for t in threads if not t.get("isResolved", False)]
        if unresolved:
            details = {
                "unresolved_thread_count": len(unresolved),
                "unresolved_threads": [
                    {
                        "id": t["id"],
                        "outdated": t.get("isOutdated", False),
                        "comment_count": len(t.get("comments", {}).get("nodes", [])),
                    }
                    for t in unresolved
                ],
            }
            return (
                "HOLD_CONVERSATION_UNRESOLVED",
                details,
                f"{len(unresolved)} unresolved review thread(s); resolve all threads before merging",
            )
        return STATUS_READY_FOR_FINAL_GATES, {}, ""
    except Exception as e:
        return (
            "HOLD_CONVERSATION_CHECK_UNAVAILABLE",
            {},
            f"conversation resolution check failed: {e}",
        )


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
    ignore_users: str = "",
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
        STATUS_HOLD_DISALLOWED_IGNORE_USER    — ignore user not in allowlist
    """
    # Parse and validate ignored users before calling the subprocess
    ignored_users_list: List[str] = []
    if ignore_users:
        valid, disallowed = validate_ignore_users(ignore_users)
        if not valid:
            # Fail closed — disallowed ignore user is never forwarded
            error_data = {
                "ignored_users": [],
                "ignore_users_allowlist": sorted(ALLOWED_REVIEW_IGNORE_USERS),
                "disallowed_ignore_users_requested": disallowed,
                "suppression_used": False,
            }
            return (
                STATUS_HOLD_DISALLOWED_IGNORE_USER,
                error_data,
                f"ignore user(s) not in allowlist: {disallowed}",
            )
        ignored_users_list = [u.strip() for u in ignore_users.split(",") if u.strip()]

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
    if ignore_users:
        cmd += ["--ignore-users", ignore_users]
    try:
        result = run_external_script(cmd, check=False)
        with open(output_json, "r") as f:
            data = json.load(f)
        # Stamp audit fields into the gate result so the caller can see
        # exactly what suppression was active and whether it was allowed.
        data["ignored_users"] = ignored_users_list
        data["ignore_users_allowlist"] = sorted(ALLOWED_REVIEW_IGNORE_USERS)
        data["suppression_used"] = bool(ignored_users_list)
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
        "--repo-root", REPO_ROOT,
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

def main() -> int:
    """Run the PR readiness waiter. Returns exit code (0=success/report written, 1=failure)."""
    global _REPORT, _REPORT_OUTPUT_JSON, _REPORT_OUTPUT_MD, _REPORT_STARTED_AT

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
        "--ignore-users",
        type=str,
        default="",
        help="Comma-separated GitHub usernames to ignore in review-comment gate (default: none, matching CI config)",
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

    # Initialize global report state for signal handlers
    _REPORT_OUTPUT_JSON = args.output_json
    _REPORT_OUTPUT_MD = args.output_md or ""
    _REPORT_STARTED_AT = datetime.now(timezone.utc).isoformat()
    _REPORT = {}

    repo_name = args.repo or "Slideshow11/Automated-Edge-Discovery"

    # ---- Build initial report骨架 ----
    def _init_report(head_sha: str = "") -> Dict:
        return {
            "pr_number": args.pr_number,
            "tool_version": 2,
            "status": STATUS_ERROR_TOOLING,
            "ready_to_merge": False,
            "merge_allowed": False,
            "repo": repo_name,
            "pr_state": None,
            "head_sha": head_sha,
            "started_at": _REPORT_STARTED_AT,
            "finished_at": "",
            "elapsed_seconds": 0.0,
            "timeout_minutes": args.timeout_minutes,
            "poll_seconds": args.poll_seconds,
            "current_phase": "initializing",
            "last_completed_phase": "",
            "gate_results": {},
            "last_known_state": "",
            "output_json": args.output_json,
            "output_md": _REPORT_OUTPUT_MD,
            "mutated_github": False,
            "merged": False,
            "used_admin": False,
            "used_auto": False,
            "next_safe_action": "",
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
        _REPORT.setdefault("stages", []).append(entry)
        _REPORT["last_completed_phase"] = name

    def _build_ready_report(
        status: str,
        head_sha: str,
        next_action: str,
        ready_to_merge: bool,
        merge_allowed: bool,
    ) -> Dict:
        finished_at = datetime.now(timezone.utc).isoformat()
        elapsed = (datetime.fromisoformat(finished_at) - datetime.fromisoformat(_REPORT_STARTED_AT)).total_seconds()
        r = _REPORT.copy()
        r["status"] = status
        r["ready_to_merge"] = ready_to_merge
        r["merge_allowed"] = merge_allowed
        r["head_sha"] = head_sha
        r["finished_at"] = finished_at
        r["elapsed_seconds"] = round(elapsed, 3)
        r["current_phase"] = "complete"
        r["next_safe_action"] = next_action
        return r

    def _write_final(
        status: str,
        head_sha: str,
        next_action: str,
        ready_to_merge: bool,
        merge_allowed: bool,
    ) -> None:
        global _REPORT
        report = _build_ready_report(status, head_sha, next_action, ready_to_merge, merge_allowed)
        write_reports(report, args.output_json, _REPORT_OUTPUT_MD)
        _REPORT = report

    # Register SIGTERM handler
    signal.signal(signal.SIGTERM, _sigterm_handler)

    exit_code = 0
    current_head_sha = ""

    try:
        # ---- Stage 0: Read PR state and head SHA ----
        _REPORT = _init_report()
        _REPORT["current_phase"] = "pr_state_check"
        pr_state, initial_head_sha = get_pr_state(args.pr_number)
        _REPORT["pr_state"] = pr_state
        _REPORT["head_sha"] = initial_head_sha or ""
        _add_stage("pr_state_check", "ok", f"state={pr_state}, SHA={initial_head_sha}")

        if pr_state is None:
            _REPORT["last_known_state"] = "pr_state_check_failed"
            _REPORT["error_detail"] = "get_pr_state returned (None, None)"
            _REPORT["error_type"] = "ERROR_TOOL_FAILURE"
            _REPORT["next_safe_action"] = next_action_for_status(STATUS_ERROR_TOOLING, args.pr_number, initial_head_sha or "unknown")
            _write_final(STATUS_ERROR_TOOLING, initial_head_sha or "", _REPORT["next_safe_action"], False, False)
            return 1

        if pr_state == "merged":
            _add_stage("merged_pr_check", STATUS_READY_PR_ALREADY_MERGED, "PR is already merged")
            _REPORT["ready_to_merge"] = True
            _REPORT["merge_allowed"] = True
            _write_final(
                STATUS_READY_PR_ALREADY_MERGED,
                initial_head_sha,
                next_action_for_status(STATUS_READY_PR_ALREADY_MERGED, args.pr_number, initial_head_sha),
                True,
                True,
            )
            return 0

        if pr_state == "closed":
            _add_stage("closed_pr_check", STATUS_HOLD_PR_NOT_OPEN, "PR is closed and cannot be merged")
            _write_final(
                STATUS_HOLD_PR_NOT_OPEN,
                initial_head_sha,
                next_action_for_status(STATUS_HOLD_PR_NOT_OPEN, args.pr_number, initial_head_sha),
                False,
                False,
            )
            return 1

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
                _REPORT["last_known_state"] = "pmg_snapshot_failed"
                _REPORT["error_detail"] = str(e)
                _REPORT["error_type"] = "ERROR_TOOL_FAILURE"
                _REPORT["next_safe_action"] = next_action_for_status(STATUS_ERROR_TOOLING, args.pr_number, initial_head_sha)
                _write_final(STATUS_ERROR_TOOLING, initial_head_sha, _REPORT["next_safe_action"], False, False)
                return 1

        # ---- Stage 1: Poll CI ----
        _REPORT["current_phase"] = "ci_poll"
        ci_status, ci_data, ci_error = poll_ci_checks(
            args.pr_number,
            required_checks,
            args.timeout_minutes,
            args.poll_seconds,
        )
        _REPORT["ci_checks"] = ci_data
        _add_stage("ci_poll", ci_status, ci_error or "", ci_data)

        if ci_status == STATUS_HOLD_TIMEOUT:
            _REPORT["last_known_state"] = "ci_poll_timeout"
            _REPORT["error_detail"] = ci_error or "timeout reached with pending CI checks"
            _REPORT["error_type"] = "TIMEOUT_WAIT_FOR_READY"
            _REPORT["next_safe_action"] = next_action_for_status(ci_status, args.pr_number, initial_head_sha)
            _write_final(ci_status, initial_head_sha, _REPORT["next_safe_action"], False, False)
            return 1

        if ci_status != STATUS_READY_FOR_FINAL_GATES:
            _REPORT["last_known_state"] = f"ci_poll_{ci_status}"
            _REPORT["error_detail"] = ci_error or f"CI check failed: {ci_status}"
            _REPORT["error_type"] = "ERROR_TOOL_FAILURE"
            _REPORT["next_safe_action"] = next_action_for_status(ci_status, args.pr_number, initial_head_sha)
            _write_final(ci_status, initial_head_sha, _REPORT["next_safe_action"], False, False)
            return 1

        # ---- Stage 2: Re-read head SHA (detect change after CI) ----
        current_head_sha = get_live_head_sha(args.pr_number)
        _add_stage("head_sha_recheck", "ok", f"SHA={current_head_sha}")
        if current_head_sha != initial_head_sha:
            _REPORT["last_known_state"] = "head_sha_changed_after_ci"
            _REPORT["error_detail"] = f"initial={initial_head_sha} current={current_head_sha}"
            _REPORT["error_type"] = "HOLD_HEAD_CHANGED"
            _REPORT["next_safe_action"] = next_action_for_status(STATUS_HOLD_HEAD_CHANGED, args.pr_number, current_head_sha)
            _write_final(STATUS_HOLD_HEAD_CHANGED, current_head_sha, _REPORT["next_safe_action"], False, False)
            return 1

        # ---- Stage 3: Review-comment gate (optional) ----
        review_gate_json = args.output_json.replace(".json", "_review_gate.json")
        if args.require_review_comments_clean:
            _REPORT["current_phase"] = "review_comment_gate"
            rg_status, rg_data, rg_error = run_review_comment_gate(
                args.pr_number,
                current_head_sha,
                review_gate_json,
                (_REPORT_OUTPUT_MD or "").replace(".md", "_review_gate.md"),
                args.ignore_users,
            )
            _REPORT["review_comment_gate"] = rg_data
            _add_stage("review_comment_gate", rg_status, rg_error or "", rg_data)
            if rg_status != STATUS_READY_FOR_FINAL_GATES:
                _REPORT["last_known_state"] = f"review_comment_gate_{rg_status}"
                _REPORT["error_detail"] = rg_error or f"review comment gate failed: {rg_status}"
                _REPORT["error_type"] = "ERROR_TOOL_FAILURE"
                _REPORT["next_safe_action"] = next_action_for_status(rg_status, args.pr_number, current_head_sha)
                _write_final(rg_status, current_head_sha, _REPORT["next_safe_action"], False, False)
                return 1

        # ---- Conversation resolution enforcement ----
        pr_view_result = gh_run(
            ["pr", "view", str(args.pr_number), "--json", "baseRefName", "--jq", ".baseRefName"],
            check=True,
        )
        base_branch = pr_view_result.stdout.strip().strip('"')
        if conversation_resolution_required(repo_name, base_branch) and not args.require_review_comments_clean:
            _add_stage(
                "conversation_resolution_enforcement",
                STATUS_HOLD_REVIEW_COMMENTS_REQUIRED_BY_BRANCH_PROTECTION,
                "branch protection requires conversation resolution but --require-review-comments-clean not set",
            )
            _REPORT["last_known_state"] = "conversation_resolution_required_but_gate_not_run"
            _REPORT["error_detail"] = "branch protection requires conversation resolution but --require-review-comments-clean not set"
            _REPORT["error_type"] = "HOLD_REVIEW_COMMENTS_REQUIRED_BY_BRANCH_PROTECTION"
            _REPORT["next_safe_action"] = next_action_for_status(STATUS_HOLD_REVIEW_COMMENTS_REQUIRED_BY_BRANCH_PROTECTION, args.pr_number, current_head_sha)
            _write_final(STATUS_HOLD_REVIEW_COMMENTS_REQUIRED_BY_BRANCH_PROTECTION, current_head_sha, _REPORT["next_safe_action"], False, False)
            return 1

        # ---- Stage 4: PMG compare (optional) ----
        pmg_compare_json = args.output_json.replace(".json", "_pmg_compare.json")
        if args.require_pmg and pmg_before_json:
            _REPORT["current_phase"] = "pmg_compare"
            pmg_status, pmg_data, pmg_error = run_pmg_compare(
                pmg_before_json,
                pmg_compare_json,
                (_REPORT_OUTPUT_MD or "").replace(".md", "_pmg_compare.md"),
            )
            _REPORT["pmg_compare"] = pmg_data
            _add_stage("pmg_compare", pmg_status, pmg_error or "", pmg_data)
            if pmg_status != STATUS_READY_FOR_FINAL_GATES:
                _REPORT["last_known_state"] = f"pmg_compare_{pmg_status}"
                _REPORT["error_detail"] = pmg_error or f"PMG compare failed: {pmg_status}"
                _REPORT["error_type"] = "ERROR_TOOL_FAILURE"
                _REPORT["next_safe_action"] = next_action_for_status(pmg_status, args.pr_number, current_head_sha)
                _write_final(pmg_status, current_head_sha, _REPORT["next_safe_action"], False, False)
                return 1

        # ---- Stage 5: Re-read head SHA (final check before final gates) ----
        final_head_sha = get_live_head_sha(args.pr_number)
        _add_stage("head_sha_final", "ok", f"SHA={final_head_sha}")
        if final_head_sha != current_head_sha:
            _REPORT["last_known_state"] = "head_sha_changed_before_final_gates"
            _REPORT["error_detail"] = f"previous={current_head_sha} current={final_head_sha}"
            _REPORT["error_type"] = "HOLD_HEAD_CHANGED"
            _REPORT["next_safe_action"] = next_action_for_status(STATUS_HOLD_HEAD_CHANGED, args.pr_number, final_head_sha)
            _write_final(STATUS_HOLD_HEAD_CHANGED, final_head_sha, _REPORT["next_safe_action"], False, False)
            return 1

        # ---- Stage 6: final_gate_status.py (optional) ----
        final_gate_json = args.output_json.replace(".json", "_final_gate.json")
        final_gate_md = (_REPORT_OUTPUT_MD or "").replace(".md", "_final_gate.md") if _REPORT_OUTPUT_MD else None
        if args.require_final_gates:
            _REPORT["current_phase"] = "final_gate_status"
            fg_status, fg_data, fg_error = run_final_gate_status(
                args.pr_number,
                final_head_sha,
                pmg_compare_json if args.require_pmg else None,
                review_gate_json if args.require_review_comments_clean else None,
                final_gate_json,
                final_gate_md or "",
            )
            _REPORT["final_gate_status"] = fg_data
            _add_stage("final_gate_status", fg_status, fg_error or "", fg_data)
            if fg_status not in (STATUS_READY_TO_MERGE_CANDIDATE, STATUS_HOLD_PR_NOT_OPEN):
                _REPORT["last_known_state"] = f"final_gate_status_{fg_status}"
                _REPORT["error_detail"] = fg_error or f"final gate failed: {fg_status}"
                _REPORT["error_type"] = "ERROR_TOOL_FAILURE"
                _REPORT["next_safe_action"] = next_action_for_status(fg_status, args.pr_number, final_head_sha)
                _write_final(fg_status, final_head_sha, _REPORT["next_safe_action"], False, False)
                return 1
            if fg_status == STATUS_HOLD_PR_NOT_OPEN:
                _add_stage("complete", STATUS_READY_TO_MERGE_CANDIDATE, "PR merged during wait; CI was green")
                _REPORT["ready_to_merge"] = True
                _REPORT["merge_allowed"] = True
                _write_final(
                    STATUS_READY_TO_MERGE_CANDIDATE,
                    final_head_sha,
                    next_action_for_status(STATUS_READY_TO_MERGE_CANDIDATE, args.pr_number, final_head_sha),
                    True,
                    True,
                )
                return 0

        # ---- Conversation resolution check ----
        require_conv = conversation_resolution_required(repo_name, base_branch)
        conv_status, conv_data, conv_error = check_conversation_resolution(
            repo_name, str(args.pr_number), require_conv
        )
        _add_stage("conversation_resolution_check", conv_status, conv_error or "", conv_data)
        if conv_status != STATUS_READY_FOR_FINAL_GATES:
            _REPORT["last_known_state"] = f"conversation_resolution_{conv_status}"
            _REPORT["error_detail"] = conv_error or f"conversation resolution check failed: {conv_status}"
            _REPORT["error_type"] = "ERROR_TOOL_FAILURE"
            _REPORT["next_safe_action"] = next_action_for_status(conv_status, args.pr_number, final_head_sha)
            _write_final(conv_status, final_head_sha, _REPORT["next_safe_action"], False, False)
            return 1

        # ---- Stage 7: verify_final_head_merge_command.py ----
        if args.require_final_gates or args.require_merge_ready:
            _REPORT["current_phase"] = "merge_ready_verifier"
            vr_json = args.output_json.replace(".json", "_merge_verifier.json")
            vr_status, vr_data, vr_error = run_merge_ready_verifier(
                args.pr_number,
                final_head_sha,
                pmg_compare_json if args.require_pmg else None,
                vr_json,
            )
            _REPORT["merge_ready_verifier"] = vr_data
            _add_stage("merge_ready_verifier", vr_status, vr_error or "", vr_data)
            if vr_status != STATUS_READY_TO_MERGE_CANDIDATE:
                _REPORT["last_known_state"] = f"merge_ready_verifier_{vr_status}"
                _REPORT["error_detail"] = vr_error or f"merge verifier failed: {vr_status}"
                _REPORT["error_type"] = "ERROR_TOOL_FAILURE"
                _REPORT["next_safe_action"] = next_action_for_status(vr_status, args.pr_number, final_head_sha)
                _write_final(vr_status, final_head_sha, _REPORT["next_safe_action"], False, False)
                return 1

        # ---- All gates passed ----
        _add_stage("complete", STATUS_READY_TO_MERGE_CANDIDATE, "all gates passed")
        _REPORT["ready_to_merge"] = True
        _REPORT["merge_allowed"] = True
        _write_final(
            STATUS_READY_TO_MERGE_CANDIDATE,
            final_head_sha,
            next_action_for_status(STATUS_READY_TO_MERGE_CANDIDATE, args.pr_number, final_head_sha),
            True,
            True,
        )
        return 0

    except KeyboardInterrupt:
        _REPORT["status"] = STATUS_INTERRUPTED_WAIT_FOR_READY
        _REPORT["finished_at"] = datetime.now(timezone.utc).isoformat()
        _REPORT["error_type"] = "INTERRUPTED_WAIT_FOR_READY"
        _REPORT["error_detail"] = "KeyboardInterrupt received"
        _REPORT["next_safe_action"] = "Investigate interruption. Do not merge until waiter reports READY_TO_MERGE_CANDIDATE."
        _REPORT["ready_to_merge"] = False
        _REPORT["merge_allowed"] = False
        write_reports(_REPORT, args.output_json, _REPORT_OUTPUT_MD)
        return 1

    except Exception as e:
        _REPORT["status"] = STATUS_ERROR_UNEXPECTED_EXCEPTION
        _REPORT["finished_at"] = datetime.now(timezone.utc).isoformat()
        _REPORT["error_type"] = type(e).__name__
        _REPORT["error_detail"] = str(e)
        _REPORT["fatal_error"] = str(e)
        _REPORT["next_safe_action"] = next_action_for_status(STATUS_ERROR_TOOLING, args.pr_number, _REPORT.get("head_sha", "unknown"))
        _REPORT["ready_to_merge"] = False
        _REPORT["merge_allowed"] = False
        write_reports(_REPORT, args.output_json, _REPORT_OUTPUT_MD)
        return 1



if __name__ == "__main__":
    sys.exit(main())