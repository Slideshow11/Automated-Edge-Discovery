#!/usr/bin/env python3
"""
audit_codex_response_for_pr.py — Read-only Codex response classifier.

Classifies the current Codex response state for a PR and returns a
machine-readable lifecycle status. Inspects BOTH PR-level issue comments
AND formal PullRequestReview submissions, because Codex sometimes posts
clean passes as PR-level issue comments rather than as formal review
submissions. A classifier that only watches formal review submissions
will miss those clean passes and report HOLD_CODEX_RESPONSE_PENDING
indefinitely.

This helper is REPORT-ONLY and READ-ONLY. It performs only read
operations against GitHub via `gh` and never mutates repository,
branch, comment, thread, or review state. It uses bounded polling
with a hard cap; it does not run watch commands and does not sleep
after the budget is exhausted.

Usage (one-shot read):
    python3 scripts/local/audit_codex_response_for_pr.py \\
        --repo Slideshow11/Automated-Edge-Discovery \\
        --pr 401 \\
        --expected-head 5ed3bdf8cea13b463fa1319338d273dd0e0601b6 \\
        --ping-comment-id 4677095302 \\
        --ping-created-at 2026-06-11T17:30:00Z \\
        --max-polls 1 \\
        --output-json /tmp/codex_response.json \\
        --output-md /tmp/codex_response.md

Usage (bounded poll, max 10 polls, 30s between):
    python3 scripts/local/audit_codex_response_for_pr.py \\
        --repo Slideshow11/Automated-Edge-Discovery \\
        --pr 401 \\
        --expected-head 5ed3bdf8cea13b463fa1319338d273dd0e0601b6 \\
        --ping-comment-id 4677095302 \\
        --ping-created-at 2026-06-11T17:30:00Z \\
        --max-polls 10 \\
        --poll-seconds 30 \\
        --output-json /tmp/codex_response.json \\
        --output-md /tmp/codex_response.md

Exit codes:
    0  — packet written (status may be any of the lifecycle values below)
    2  — ERROR_INVALID_ARGS
    1  — unexpected internal error / ERROR_TOOL_FAILURE
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Status taxonomy
# ---------------------------------------------------------------------------

STATUS_CLEAN_PASS = "CODEX_CLEAN_PASS"
STATUS_CLEAN_PASS_RESOLVE_ONLY = "CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED"
STATUS_HOLD_CODEX_PENDING = "HOLD_CODEX_RESPONSE_PENDING"
STATUS_HOLD_NEW_THREAD = "HOLD_NEW_CODEX_THREAD"
STATUS_HOLD_HEAD_CHANGED = "HOLD_HEAD_CHANGED"
STATUS_HOLD_PR_NOT_OPEN = "HOLD_PR_NOT_OPEN"
STATUS_HOLD_MERGE_STATE_BLOCKED = "HOLD_MERGE_STATE_BLOCKED"
STATUS_MERGE_READY = "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION"
STATUS_ERROR_INVALID_ARGS = "ERROR_INVALID_ARGS"
STATUS_ERROR_TOOL_FAILURE = "ERROR_TOOL_FAILURE"

# Codex bot identifiers
CODEX_BOT_LOGINS = frozenset({
    "chatgpt-codex-connector",
    "chatgpt-codex-connector[bot]",
})

# Exact phrase Codex uses to denote a clean pass in issue-level comments.
CODEX_CLEAN_PASS_PHRASE = "Codex Review: Didn\u2019t find any major issues"
# Accept both curly and straight apostrophes
CODEX_CLEAN_PASS_PHRASE_ALT = "Codex Review: Didn't find any major issues"
CODEX_CLEAN_PASS_PHRASES = (CODEX_CLEAN_PASS_PHRASE, CODEX_CLEAN_PASS_PHRASE_ALT)

# Exact 40-character lowercase hex
SHA_REGEX = re.compile(r"^[0-9a-f]{40}$")

PACKET_KIND = "aed.codex_response.classifier.v0"
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Recommendation text per status
# ---------------------------------------------------------------------------

RECOMMENDATIONS = {
    STATUS_CLEAN_PASS: (
        "Codex clean-passed the current head with no unresolved threads. "
        "PR is not yet merge-ready in this state (operator may want to also "
        "verify mergeStateStatus); report CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED "
        "or MERGE_READY_AWAITING_HUMAN_AUTHORIZATION instead if those are the "
        "authoritative state."
    ),
    STATUS_CLEAN_PASS_RESOLVE_ONLY: (
        "Codex clean-passed the current head. Outdated or stale unresolved "
        "review threads remain. Operator must request explicit human "
        "authorization to resolve outdated threads before merge."
    ),
    STATUS_HOLD_CODEX_PENDING: (
        "Codex has not responded within the bounded poll budget. Do not "
        "continue sleeping; report HOLD_CODEX_RESPONSE_PENDING and stop."
    ),
    STATUS_HOLD_NEW_THREAD: (
        "Codex raised a new current-head finding or a finding after the "
        "clean pass. Do not resolve threads; do not merge. Apply a "
        "fix-and-resubmit turn."
    ),
    STATUS_HOLD_HEAD_CHANGED: (
        "PR headRefOid does not match --expected-head. Re-fetch PR state "
        "and re-verify before any further mutation."
    ),
    STATUS_HOLD_PR_NOT_OPEN: (
        "PR is not in OPEN state. Inspect; do not classify Codex response "
        "for a closed or merged PR unless this is a deliberate post-merge "
        "resume."
    ),
    STATUS_HOLD_MERGE_STATE_BLOCKED: (
        "Codex clean-passed and no unresolved threads remain, but "
        "mergeStateStatus is not CLEAN. Investigate branch protection or "
        "other GitHub-side block before retrying merge."
    ),
    STATUS_MERGE_READY: (
        "Codex clean-passed, no unresolved threads remain, and "
        "mergeStateStatus is CLEAN. Merge is permitted only after explicit "
        "human authorization with the exact live 40-character head SHA."
    ),
    STATUS_ERROR_INVALID_ARGS: "Stop and inspect tool error.",
    STATUS_ERROR_TOOL_FAILURE: "Stop and inspect tool error.",
}


# ---------------------------------------------------------------------------
# GitHub API helpers (read-only, list-argv, no shell-equals-True)
# ---------------------------------------------------------------------------


def gh_api_paginated(repo: str, endpoint: str, timeout: int = 30) -> Tuple[bool, List[Dict[str, Any]], str]:
    """
    Call `gh api` for the given endpoint (no leading slash) with --paginate.
    Returns (success, data_list, error_msg).
    """
    cmd = ["gh", "api", f"repos/{repo}/{endpoint}", "--paginate"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except OSError as exc:
        return False, [], f"gh invocation failed: {exc}"
    if result.returncode != 0:
        return False, [], f"gh api returned {result.returncode}: {result.stderr[:500]}"
    if not result.stdout.strip():
        return True, [], ""
    try:
        data = json.loads(result.stdout)
        if isinstance(data, list):
            return True, data, ""
        return True, [data], ""
    except json.JSONDecodeError as exc:
        return False, [], f"invalid JSON from gh api: {exc}"


def gh_pr_view_min(repo: str, pr_number: int) -> Tuple[bool, Dict[str, Any], str]:
    """
    Fetch PR metadata needed for head/mergeState/reviewDecision checks.
    Uses REST `repos/.../pulls/{n}` (does not require git repo cwd).
    """
    cmd = [
        "gh", "api", f"repos/{repo}/pulls/{pr_number}",
        "--jq", "{sha:.head.sha, state:.state, mergeStateStatus:.merge_state_status, "
                "mergeable:.mergeable, reviewDecision:.review_decision, baseRefName:.base.ref, "
                "headRefName:.head.ref, url:.html_url}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except OSError as exc:
        return False, {}, f"gh api invocation failed: {exc}"
    if result.returncode != 0:
        return False, {}, f"gh api returned {result.returncode}: {result.stderr[:300]}"
    try:
        return True, json.loads(result.stdout), ""
    except json.JSONDecodeError:
        return False, {}, "gh api --jq returned non-JSON"


def gh_graphql_review_threads(
    repo: str, pr_number: int, timeout: int = 30
) -> Tuple[bool, List[Dict[str, Any]], str]:
    """
    Fetch PR review-thread resolution state via GraphQL with --paginate
    on reviewThreads (100 per page). Each returned entry has:
      {thread_id, is_resolved, is_outdated, comment_database_id, comment_url, author, body, path, line}
    """
    owner, name = repo.split("/", 1)
    query_parts = [
        "query {",
        f'repository(owner:"{owner}", name:"{name}") {{',
        f"pullRequest(number:{pr_number}) {{",
        "reviewThreads(first:100) {",
        "pageInfo { hasNextPage endCursor }",
        "nodes {",
        "id isResolved isOutdated",
        "comments(first:50) { nodes { databaseId url body path line "
        "author { login } } }",
        "}",
        "}",
        "}",
        "}",
        "}",
    ]
    query_literal = " ".join(query_parts)
    cmd = ["gh", "api", "graphql", "--raw-field", f"query={query_literal}"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except OSError as exc:
        return False, [], f"gh graphql invocation failed: {exc}"
    if result.returncode != 0:
        return False, [], f"gh graphql returned {result.returncode}: {result.stderr[:500]}"
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return False, [], f"invalid GraphQL response: {exc}"
    errors = data.get("errors")
    if errors:
        return False, [], f"GraphQL errors: {errors}"
    pr_data = (
        data.get("data", {}).get("repository", {}).get("pullRequest", {})
    )
    threads_container = pr_data.get("reviewThreads", {})
    page_info = threads_container.get("pageInfo", {})
    if page_info.get("hasNextPage"):
        # Fail closed: pagination is required to be exhaustive.
        return False, [], (
            "reviewThreads pagination required (hasNextPage=true); "
            "this classifier does not yet paginate review threads."
        )
    nodes = threads_container.get("nodes", [])
    threads: List[Dict[str, Any]] = []
    for node in nodes:
        thread_id = node.get("id", "")
        is_resolved = bool(node.get("isResolved", False))
        is_outdated = bool(node.get("isOutdated", False))
        for comment in (node.get("comments", {}) or {}).get("nodes", []):
            author_login = (
                (comment.get("author") or {}).get("login", "")
                if isinstance(comment.get("author"), dict) else ""
            )
            threads.append({
                "thread_id": thread_id,
                "is_resolved": is_resolved,
                "is_outdated": is_outdated,
                "comment_database_id": comment.get("databaseId"),
                "comment_url": comment.get("url") or "",
                "author": author_login,
                "body": comment.get("body") or "",
                "path": comment.get("path") or "",
                "line": comment.get("line"),
            })
    return True, threads, ""


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def parse_iso_utc(s: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; return None on failure."""
    if not s:
        return None
    s2 = s.strip()
    if s2.endswith("Z"):
        s2 = s2[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s2)
    except ValueError:
        return None


def is_codex_clean_pass_comment(body: str) -> bool:
    """Return True if the body contains the Codex clean-pass phrase."""
    if not body:
        return False
    return any(phrase in body for phrase in CODEX_CLEAN_PASS_PHRASES)


def extract_review_commit_oid(review: Dict[str, Any]) -> str:
    """Extract the commit OID from a review submission dict."""
    return (
        (review.get("commit_id") or "")
        or ((review.get("commit") or {}).get("oid") if isinstance(review.get("commit"), dict) else "")
        or ""
    )


# ---------------------------------------------------------------------------
# Main classification pipeline
# ---------------------------------------------------------------------------


def classify(
    *,
    repo: str,
    pr_number: int,
    expected_head_sha: str,
    ping_comment_id: Optional[str],
    ping_created_at: Optional[str],
    max_polls: int,
    poll_seconds: int,
    api_timeout: int = 30,
) -> Dict[str, Any]:
    """
    Run the bounded poll and return a complete packet (also serializable
    to JSON). Caller is responsible for writing the packet to disk.

    Returns a dict with at least the keys listed in PACKET_KIND spec.
    """
    polls_used = 0
    polling_exhausted = False
    last_seen_codex_review_ts: Optional[str] = None
    last_seen_codex_review_id: Optional[str] = None
    last_seen_codex_comment_ts: Optional[str] = None
    last_seen_codex_comment_id: Optional[str] = None

    api_errors: List[str] = []
    final_status: str = STATUS_HOLD_CODEX_PENDING
    recommendation: str = RECOMMENDATIONS[STATUS_HOLD_CODEX_PENDING]

    # PR-level issue comments (Codex sometimes posts clean passes here)
    pr_issue_comments: List[Dict[str, Any]] = []
    # Formal PullRequestReview submissions
    pr_reviews: List[Dict[str, Any]] = []
    # Review threads
    review_threads: List[Dict[str, Any]] = []

    # PR metadata
    pr_state: str = ""
    pr_url: str = ""
    pr_base_ref: str = ""
    pr_head_ref: str = ""
    observed_head_sha: str = ""
    merge_state_status: Optional[str] = None
    mergeable: Optional[str] = None
    review_decision: Optional[str] = None
    head_matches_expected = False

    clean_pass_detected = False
    clean_pass_comment_id: Optional[int] = None
    clean_pass_review_id: Optional[int] = None
    clean_pass_source: Optional[str] = None
    clean_pass_at: Optional[str] = None

    latest_codex_response_type: str = "none"
    latest_codex_response_id: Optional[str] = None
    latest_codex_response_created_at: Optional[str] = None

    # Active thread inventory
    active_threads: List[Dict[str, Any]] = []
    outdated_threads: List[Dict[str, Any]] = []
    resolved_threads: List[Dict[str, Any]] = []

    # Stop conditions
    stop_reason: Optional[str] = None

    # If ping is supplied, parse it for filtering
    ping_dt: Optional[datetime] = None
    if ping_created_at:
        ping_dt = parse_iso_utc(ping_created_at)
        if ping_dt is None:
            api_errors.append(
                f"ping_created_at could not be parsed: {ping_created_at!r}"
            )

    for poll_idx in range(1, max_polls + 1):
        polls_used = poll_idx

        # ---- 1. PR metadata (head alignment + state) ----
        ok_pr, pr_data, err_pr = gh_pr_view_min(repo, pr_number)
        if not ok_pr:
            api_errors.append(f"pr_view: {err_pr}")
            # If we cannot read PR metadata at all, surface tool failure
            final_status = STATUS_ERROR_TOOL_FAILURE
            recommendation = RECOMMENDATIONS[STATUS_ERROR_TOOL_FAILURE]
            stop_reason = "tool_failure"
            break
        pr_state = pr_data.get("state", "") or ""
        pr_url = pr_data.get("url", "") or ""
        pr_base_ref = pr_data.get("baseRefName", "") or ""
        pr_head_ref = pr_data.get("headRefName", "") or ""
        observed_head_sha = pr_data.get("sha", "") or ""
        merge_state_status = pr_data.get("mergeStateStatus")
        mergeable = pr_data.get("mergeable")
        review_decision = pr_data.get("reviewDecision")
        head_matches_expected = bool(
            expected_head_sha and observed_head_sha
            and observed_head_sha == expected_head_sha
        )

        if not head_matches_expected:
            # If state is merged/closed and the observed head moved on, that
            # is a post-merge state, not necessarily a head-change.
            if (pr_state or "").upper() == "MERGED":
                # Treat MERGED as HOLD_PR_NOT_OPEN; the head cannot be
                # "expected" after merge because the branch was deleted.
                final_status = STATUS_HOLD_PR_NOT_OPEN
                recommendation = RECOMMENDATIONS[STATUS_HOLD_PR_NOT_OPEN]
                stop_reason = "pr_not_open"
                break
            if (pr_state or "").upper() == "CLOSED":
                final_status = STATUS_HOLD_PR_NOT_OPEN
                recommendation = RECOMMENDATIONS[STATUS_HOLD_PR_NOT_OPEN]
                stop_reason = "pr_not_open"
                break
            final_status = STATUS_HOLD_HEAD_CHANGED
            recommendation = RECOMMENDATIONS[STATUS_HOLD_HEAD_CHANGED]
            stop_reason = "head_changed"
            break

        if (pr_state or "").upper() != "OPEN":
            final_status = STATUS_HOLD_PR_NOT_OPEN
            recommendation = RECOMMENDATIONS[STATUS_HOLD_PR_NOT_OPEN]
            stop_reason = "pr_not_open"
            break

        # ---- 2. PR-level issue comments (Codex clean passes live here) ----
        ok_issue, issue_data, err_issue = gh_api_paginated(
            repo, f"issues/{pr_number}/comments", timeout=api_timeout,
        )
        if not ok_issue:
            api_errors.append(f"issue_comments: {err_issue}")
        else:
            pr_issue_comments = issue_data

        # ---- 3. Formal PullRequestReview submissions ----
        ok_rev, review_data, err_rev = gh_api_paginated(
            repo, f"pulls/{pr_number}/reviews", timeout=api_timeout,
        )
        if not ok_rev:
            api_errors.append(f"reviews: {err_rev}")
        else:
            pr_reviews = review_data

        # ---- 4. Review threads (resolution + outdated state) ----
        ok_thr, thread_data, err_thr = gh_graphql_review_threads(
            repo, pr_number, timeout=api_timeout,
        )
        if not ok_thr:
            api_errors.append(f"review_threads: {err_thr}")
        else:
            review_threads = thread_data

        # ---- 5. Identify latest Codex response after ping ----
        codex_issue_comments: List[Dict[str, Any]] = []
        for c in pr_issue_comments:
            author = ((c.get("user") or {}).get("login", "")
                      if isinstance(c.get("user"), dict) else "")
            if author in CODEX_BOT_LOGINS:
                codex_issue_comments.append(c)

        codex_review_submissions: List[Dict[str, Any]] = []
        for r in pr_reviews:
            author = ((r.get("user") or {}).get("login", "")
                      if isinstance(r.get("user"), dict) else "")
            if author in CODEX_BOT_LOGINS:
                codex_review_submissions.append(r)

        # Determine latest response (newest by createdAt/submittedAt)
        def _iso(s: str) -> str:
            return s or ""

        latest_issue = None
        for c in codex_issue_comments:
            ts = _iso(c.get("createdAt", ""))
            if ping_dt is not None:
                c_dt = parse_iso_utc(ts)
                if c_dt is None or c_dt < ping_dt:
                    continue
            if latest_issue is None or ts > _iso(latest_issue.get("createdAt", "")):
                latest_issue = c
        latest_review = None
        for r in codex_review_submissions:
            ts = _iso(r.get("submittedAt", ""))
            if ping_dt is not None:
                r_dt = parse_iso_utc(ts)
                if r_dt is None or r_dt < ping_dt:
                    continue
            # Only consider as a controlling response if it's for expected
            # head OR if no commit_oid is set (legacy / legacy reviews)
            rev_commit = extract_review_commit_oid(r)
            if rev_commit and expected_head_sha and rev_commit != expected_head_sha:
                # The review is anchored to a different commit. Track it
                # as "last seen" but not as authoritative.
                if ts > (last_seen_codex_review_ts or ""):
                    last_seen_codex_review_ts = ts
                    last_seen_codex_review_id = str(r.get("id", ""))
                continue
            if latest_review is None or ts > _iso(latest_review.get("submittedAt", "")):
                latest_review = r
            if ts > (last_seen_codex_review_ts or ""):
                last_seen_codex_review_ts = ts
                last_seen_codex_review_id = str(r.get("id", ""))

        # Track last-seen Codex activity even if filtered out
        for c in codex_issue_comments:
            ts = _iso(c.get("createdAt", ""))
            if ts > (last_seen_codex_comment_ts or ""):
                last_seen_codex_comment_ts = ts
                last_seen_codex_comment_id = str(c.get("id", ""))

        # Pick the newer of the two surfaces
        candidates = []
        if latest_issue is not None:
            candidates.append((
                _iso(latest_issue.get("createdAt", "")),
                "issue_comment",
                str(latest_issue.get("id", "")),
            ))
        if latest_review is not None:
            candidates.append((
                _iso(latest_review.get("submittedAt", "")),
                "pull_request_review",
                str(latest_review.get("id", "")),
            ))
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            latest_codex_response_ts, latest_codex_response_type, latest_codex_response_id = candidates[0]
            latest_codex_response_created_at = latest_codex_response_ts

        # ---- 6. Detect Codex clean pass ----
        # A clean pass is a Codex-authored PR-level issue comment whose
        # body contains the canonical clean-pass phrase. We consider ALL
        # Codex clean-pass comments after the ping, not just the latest
        # one: a later finding might have superseded the clean pass.
        # Filter by ping_dt so old pre-ping clean passes do not count.
        latest_clean_pass = None
        for c in codex_issue_comments:
            if not is_codex_clean_pass_comment(c.get("body", "")):
                continue
            ts = c.get("createdAt", "")
            if ping_dt is not None:
                c_dt = parse_iso_utc(ts)
                if c_dt is None or c_dt < ping_dt:
                    continue
            if latest_clean_pass is None or ts > latest_clean_pass.get("createdAt", ""):
                latest_clean_pass = c
        if latest_clean_pass is None and latest_review is not None:
            # Treat formal review clean pass as valid only if state is
            # APPROVED/COMMENTED and body contains the clean-pass phrase.
            state_value = (latest_review.get("state") or "").upper()
            body_value = latest_review.get("body", "") or ""
            if state_value in ("APPROVED", "COMMENTED") and is_codex_clean_pass_comment(body_value):
                clean_pass_detected = True
                clean_pass_review_id = latest_review.get("id")
                clean_pass_source = "pull_request_review"
                clean_pass_at = latest_review.get("submittedAt")
        if latest_clean_pass is not None:
            clean_pass_detected = True
            clean_pass_comment_id = latest_clean_pass.get("databaseId") or latest_clean_pass.get("id")
            clean_pass_source = "issue_comment"
            clean_pass_at = latest_clean_pass.get("createdAt")

        # ---- 7. Inventory threads ----
        # Active = is_resolved=false AND is_outdated=false
        # Outdated = is_outdated=true (regardless of resolved state for reporting)
        # Resolved = is_resolved=true
        for t in review_threads:
            entry = {
                "thread_id": t.get("thread_id", ""),
                "comment_database_id": t.get("comment_database_id"),
                "comment_url": t.get("comment_url", ""),
                "author": t.get("author", ""),
                "path": t.get("path", ""),
                "line": t.get("line"),
                "is_resolved": bool(t.get("is_resolved", False)),
                "is_outdated": bool(t.get("is_outdated", False)),
                "body": (t.get("body", "") or "")[:500],
            }
            if entry["is_resolved"]:
                resolved_threads.append(entry)
            elif entry["is_outdated"]:
                outdated_threads.append(entry)
            else:
                active_threads.append(entry)

        # ---- 8. Decide ----
        # Order of precedence:
        # 1) Codex raised a current-head active finding (thread) -> HOLD_NEW_CODEX_THREAD
        # 2) Codex clean-pass exists AND a newer Codex finding exists
        #    after it -> HOLD_NEW_CODEX_THREAD
        # 3) Codex clean-pass exists AND no newer active finding -> resolve-only or merge-ready
        # 4) Otherwise -> HOLD_CODEX_RESPONSE_PENDING

        has_active_blocker = any(
            t.get("author", "") in CODEX_BOT_LOGINS for t in active_threads
        )
        # If a clean pass exists, we also need to check whether any NEWER
        # Codex comment/review (with a real finding) arrived after it.
        newer_finding_after_clean_pass = False
        if clean_pass_detected and clean_pass_at:
            cp_dt = parse_iso_utc(clean_pass_at)
            for c in codex_issue_comments:
                c_dt = parse_iso_utc(c.get("createdAt", ""))
                if c_dt is None or cp_dt is None or c_dt <= cp_dt:
                    continue
                # Any post-clean-pass Codex issue comment other than
                # another clean pass is treated as a finding.
                if not is_codex_clean_pass_comment(c.get("body", "")):
                    newer_finding_after_clean_pass = True
                    break
            if not newer_finding_after_clean_pass:
                for r in codex_review_submissions:
                    r_dt = parse_iso_utc(r.get("submittedAt", ""))
                    if r_dt is None or cp_dt is None or r_dt <= cp_dt:
                        continue
                    body = r.get("body", "") or ""
                    state_v = (r.get("state") or "").upper()
                    if state_v in ("CHANGES_REQUESTED", "REQUEST_CHANGES"):
                        newer_finding_after_clean_pass = True
                        break
                    if state_v in ("APPROVED", "COMMENTED") and not is_codex_clean_pass_comment(body):
                        newer_finding_after_clean_pass = True
                        break

        if has_active_blocker or newer_finding_after_clean_pass:
            final_status = STATUS_HOLD_NEW_THREAD
            recommendation = RECOMMENDATIONS[STATUS_HOLD_NEW_THREAD]
            stop_reason = "active_finding"
            break

        if clean_pass_detected:
            # Decide between CODEX_CLEAN_PASS, RESOLVE_ONLY, MERGE_READY,
            # MERGE_STATE_BLOCKED, and HOLD_PR_NOT_OPEN.
            unresolved_count = len(active_threads) + len(outdated_threads)
            if unresolved_count == 0:
                if merge_state_status == "CLEAN":
                    final_status = STATUS_MERGE_READY
                    recommendation = RECOMMENDATIONS[STATUS_MERGE_READY]
                    stop_reason = "merge_ready"
                    break
                final_status = STATUS_HOLD_MERGE_STATE_BLOCKED
                recommendation = RECOMMENDATIONS[STATUS_HOLD_MERGE_STATE_BLOCKED]
                stop_reason = "merge_state_blocked"
                break
            final_status = STATUS_CLEAN_PASS_RESOLVE_ONLY
            recommendation = RECOMMENDATIONS[STATUS_CLEAN_PASS_RESOLVE_ONLY]
            stop_reason = "resolve_only"
            break

        # No clean pass and no active finding on this poll: keep polling
        # until budget is exhausted. Never sleep after the last poll.
        if poll_idx < max_polls:
            time.sleep(poll_seconds)

    # If we exited the loop without a stop_reason, polling is exhausted
    if stop_reason is None:
        polling_exhausted = True
        final_status = STATUS_HOLD_CODEX_PENDING
        recommendation = RECOMMENDATIONS[STATUS_HOLD_CODEX_PENDING]

    # Build the JSON packet
    packet: Dict[str, Any] = {
        "packet_kind": PACKET_KIND,
        "schema_version": SCHEMA_VERSION,
        "status": final_status,
        "repo": repo,
        "pr_number": pr_number,
        "expected_head_sha": expected_head_sha,
        "observed_head_sha": observed_head_sha,
        "head_matches_expected": head_matches_expected,
        "pr_state": pr_state,
        "pr_url": pr_url,
        "pr_base_ref_name": pr_base_ref,
        "pr_head_ref_name": pr_head_ref,
        "ping_comment_id": ping_comment_id,
        "ping_created_at": ping_created_at,
        "latest_codex_response_type": latest_codex_response_type,
        "latest_codex_response_id": latest_codex_response_id,
        "latest_codex_response_created_at": latest_codex_response_created_at,
        "clean_pass_detected": clean_pass_detected,
        "clean_pass_source": clean_pass_source,
        "clean_pass_comment_id": clean_pass_comment_id,
        "clean_pass_review_id": clean_pass_review_id,
        "clean_pass_at": clean_pass_at,
        "last_seen_codex_review_id": last_seen_codex_review_id,
        "last_seen_codex_review_at": last_seen_codex_review_ts,
        "last_seen_codex_comment_id": last_seen_codex_comment_id,
        "last_seen_codex_comment_at": last_seen_codex_comment_ts,
        "active_threads": active_threads,
        "outdated_threads": outdated_threads,
        "resolved_threads": resolved_threads,
        "unresolved_thread_count": len(active_threads) + len(outdated_threads),
        "current_head_active_blocker_count": len(active_threads),
        "outdated_unresolved_thread_count": len(outdated_threads),
        "merge_state_status": merge_state_status,
        "mergeable": mergeable,
        "review_decision": review_decision,
        "polls_used": polls_used,
        "polling_exhausted": polling_exhausted,
        "max_polls": max_polls,
        "poll_seconds": poll_seconds,
        "api_errors": api_errors,
        "recommendation": recommendation,
        "harvested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    return packet


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_markdown(packet: Dict[str, Any]) -> str:
    """Render the packet as a human-readable markdown report."""
    lines: List[str] = []
    pr_number = packet.get("pr_number", "?")
    expected = packet.get("expected_head_sha", "")
    observed = packet.get("observed_head_sha", "")
    status = packet.get("status", "")
    harvested = packet.get("harvested_at", "")
    lines.append(f"# Codex Response Classifier — PR #{pr_number}\n")
    lines.append(f"**Expected head SHA:** `{expected}`  ")
    lines.append(f"**Observed head SHA:** `{observed}`  ")
    lines.append(f"**Status:** `{status}`  ")
    lines.append(f"**Harvested at:** {harvested}\n")

    pr_state = packet.get("pr_state", "")
    if pr_state and pr_state != "OPEN":
        lines.append(f"**⚠️  PR state is `{pr_state}` (not OPEN).**\n")

    lines.append("## PR metadata\n")
    lines.append(f"- **State:** `{packet.get('pr_state', '')}`  ")
    lines.append(f"- **Base ref:** `{packet.get('pr_base_ref_name', '')}`  ")
    lines.append(f"- **Head ref:** `{packet.get('pr_head_ref_name', '')}`  ")
    lines.append(f"- **mergeStateStatus:** `{packet.get('merge_state_status', '')}`  ")
    lines.append(f"- **mergeable:** `{packet.get('mergeable', '')}`  ")
    lines.append(f"- **reviewDecision:** `{packet.get('review_decision', '')}`  ")
    lines.append(f"- **URL:** {packet.get('pr_url', '')}\n")

    lines.append("## Latest Codex response\n")
    rt = packet.get("latest_codex_response_type", "none")
    if rt == "none":
        lines.append("_No Codex-authored response found after the ping._\n")
    else:
        lines.append(f"- **Type:** `{rt}`  ")
        lines.append(f"- **ID:** `{packet.get('latest_codex_response_id', '')}`  ")
        lines.append(f"- **Created at:** `{packet.get('latest_codex_response_created_at', '')}`\n")

    lines.append("## Clean-pass evidence\n")
    if packet.get("clean_pass_detected"):
        lines.append("- **Clean pass detected:** ✅  ")
        lines.append(f"- **Source:** `{packet.get('clean_pass_source', '')}`  ")
        lines.append(f"- **Comment DB ID:** `{packet.get('clean_pass_comment_id', '')}`  ")
        lines.append(f"- **Review ID:** `{packet.get('clean_pass_review_id', '')}`  ")
        lines.append(f"- **At:** `{packet.get('clean_pass_at', '')}`\n")
    else:
        lines.append("_No clean-pass comment or review detected for this head._\n")

    active = packet.get("active_threads", []) or []
    outdated = packet.get("outdated_threads", []) or []
    resolved = packet.get("resolved_threads", []) or []
    lines.append("## Active current-head blockers\n")
    if not active:
        lines.append("_No active current-head blockers._\n")
    else:
        for t in active:
            lines.append(
                f"- **[{t.get('author', '')}]** {t.get('path', '')}:{t.get('line', '')}  "
                f"[thread]({t.get('comment_url', '')}) (dbid={t.get('comment_database_id', '')})"
            )
        lines.append("")

    lines.append("## Outdated unresolved threads (resolve-only candidates)\n")
    if not outdated:
        lines.append("_None._\n")
    else:
        for t in outdated:
            lines.append(
                f"- **[{t.get('author', '')}]** {t.get('path', '')}:{t.get('line', '')}  "
                f"[thread]({t.get('comment_url', '')}) (dbid={t.get('comment_database_id', '')})"
            )
        lines.append("")

    lines.append("## Resolved threads (history)\n")
    if not resolved:
        lines.append("_None._\n")
    else:
        for t in resolved:
            lines.append(
                f"- **[{t.get('author', '')}]** {t.get('path', '')}:{t.get('line', '')}  "
                f"[thread]({t.get('comment_url', '')}) (dbid={t.get('comment_database_id', '')})"
            )
        lines.append("")

    lines.append("## Polling summary\n")
    lines.append(f"- **Polls used:** `{packet.get('polls_used', 0)}` / `{packet.get('max_polls', 0)}`  ")
    lines.append(f"- **Poll seconds:** `{packet.get('poll_seconds', 0)}`  ")
    lines.append(f"- **Polling exhausted:** `{packet.get('polling_exhausted', False)}`  ")
    lines.append(f"- **Last seen Codex comment:** `{packet.get('last_seen_codex_comment_at', '')}` "
                 f"(id=`{packet.get('last_seen_codex_comment_id', '')}`)  ")
    lines.append(f"- **Last seen Codex review:** `{packet.get('last_seen_codex_review_at', '')}` "
                 f"(id=`{packet.get('last_seen_codex_review_id', '')}`)\n")

    errs = packet.get("api_errors", []) or []
    if errs:
        lines.append("## API errors\n")
        for e in errs:
            lines.append(f"- {e}")
        lines.append("")

    lines.append("## Recommendation\n")
    lines.append(packet.get("recommendation", "") + "\n")
    lines.append("## Next safe action\n")
    rec_action = _next_action_for(packet)
    lines.append(rec_action + "\n")
    return "\n".join(lines)


def _next_action_for(packet: Dict[str, Any]) -> str:
    """Map a packet status to a one-line next-action hint."""
    status = packet.get("status", "")
    if status == STATUS_MERGE_READY:
        return (
            "Request explicit human authorization to merge with the exact live "
            "40-character head SHA. Use guarded squash merge with "
            "--match-head-commit. Do not include the admin bypass or the "
            "auto-merge enablement flag."
        )
    if status == STATUS_CLEAN_PASS_RESOLVE_ONLY:
        return (
            "Request explicit human authorization to resolve only the listed "
            "outdated unresolved threads. Do not resolve any thread where "
            "isOutdated=false. Re-run this classifier after the resolve to "
            "re-classify."
        )
    if status == STATUS_HOLD_NEW_THREAD:
        return (
            "Apply a fix-and-resubmit turn against the new Codex current-head "
            "finding. Do not resolve threads; do not merge."
        )
    if status == STATUS_HOLD_CODEX_PENDING:
        return (
            "Stop. Do not continue sleeping. Re-run later with a fresh budget. "
            "If a ping was not yet posted, post a gate-safe Codex review ping."
        )
    if status == STATUS_HOLD_HEAD_CHANGED:
        return (
            "Re-fetch PR state and re-verify the expected head. Do not proceed "
            "with a stale head."
        )
    if status == STATUS_HOLD_PR_NOT_OPEN:
        return (
            "Inspect the PR state. Codex response classification is meaningful "
            "only on OPEN PRs (or a deliberate post-merge resume)."
        )
    if status == STATUS_HOLD_MERGE_STATE_BLOCKED:
        return (
            "Investigate branch protection rules or other GitHub-side "
            "blockers. Do not bypass via the admin flag or the auto-merge "
            "enablement flag."
        )
    if status == STATUS_CLEAN_PASS:
        return (
            "Codex clean-pass detected. If there are no unresolved threads, "
            "consider also running a final merge-readiness check that includes "
            "mergeStateStatus."
        )
    if status in (STATUS_ERROR_INVALID_ARGS, STATUS_ERROR_TOOL_FAILURE):
        return "Stop and inspect tool error."
    return "Stop and inspect tool error."


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Codex response classifier for a PR. Inspects both "
            "PR-level issue comments and formal review submissions, with "
            "a hard polling budget. Reports a lifecycle status."
        ),
    )
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--pr", required=True, type=int, help="PR number")
    parser.add_argument(
        "--expected-head", required=True,
        help="40-char expected PR head SHA (lowercase hex)",
    )
    parser.add_argument(
        "--ping-comment-id", default=None,
        help="Optional Codex ping comment databaseId for filtering",
    )
    parser.add_argument(
        "--ping-created-at", default=None,
        help="Optional ISO-8601 timestamp of the ping for filtering",
    )
    parser.add_argument(
        "--max-polls", type=int, default=1,
        help="Hard cap on the number of polls (default: 1 = one-shot read)",
    )
    parser.add_argument(
        "--poll-seconds", type=int, default=30,
        help="Seconds to sleep between polls (default: 30)",
    )
    parser.add_argument("--output-json", default=None, help="Path to write JSON packet")
    parser.add_argument("--output-md", default=None, help="Path to write Markdown report")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    # Validate SHA
    if not SHA_REGEX.match(args.expected_head):
        # Emit a degraded packet so callers always get a JSON file
        if args.output_json:
            Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output_json).write_text(json.dumps({
                "packet_kind": PACKET_KIND,
                "schema_version": SCHEMA_VERSION,
                "status": STATUS_ERROR_INVALID_ARGS,
                "error": "expected_head is not a 40-char lowercase hex SHA",
                "expected_head_sha": args.expected_head,
            }, indent=2) + "\n")
        if args.output_md:
            Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output_md).write_text(
                f"# Codex Response Classifier — INVALID ARGS\n\n"
                f"**Error:** expected_head `{args.expected_head}` is not a "
                f"40-char lowercase hex SHA.\n"
            )
        return 2

    # Validate poll budget
    if args.max_polls < 1:
        if args.output_json:
            Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output_json).write_text(json.dumps({
                "packet_kind": PACKET_KIND,
                "schema_version": SCHEMA_VERSION,
                "status": STATUS_ERROR_INVALID_ARGS,
                "error": "max-polls must be >= 1",
                "max_polls": args.max_polls,
            }, indent=2) + "\n")
        return 2
    if args.poll_seconds < 0 or args.poll_seconds > 30:
        if args.output_json:
            Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output_json).write_text(json.dumps({
                "packet_kind": PACKET_KIND,
                "schema_version": SCHEMA_VERSION,
                "status": STATUS_ERROR_INVALID_ARGS,
                "error": "poll-seconds must be in [0, 30]",
                "poll_seconds": args.poll_seconds,
            }, indent=2) + "\n")
        return 2

    packet = classify(
        repo=args.repo,
        pr_number=args.pr,
        expected_head_sha=args.expected_head,
        ping_comment_id=args.ping_comment_id,
        ping_created_at=args.ping_created_at,
        max_polls=args.max_polls,
        poll_seconds=args.poll_seconds,
    )

    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(json.dumps(packet, indent=2) + "\n")
    if args.output_md:
        Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_md).write_text(render_markdown(packet))

    return 0


if __name__ == "__main__":
    sys.exit(main())
