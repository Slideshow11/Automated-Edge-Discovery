#!/usr/bin/env python3
"""
check_current_bot_thread_resolution.py

Dry-run helper that evaluates whether a single current-head bot review
thread is eligible for auto-resolution under the policy defined in:

    CURRENT-HEAD BOT REVIEW THREAD RESOLUTION POLICY

This script is inspect-only. It NEVER performs a thread-resolution
GraphQL mutation, never dismisses a review, and never mutates any
GitHub state. It only reads PR state (REST) and review threads
(a single read-only GraphQL query).

Usage:
    python3 scripts/local/check_current_bot_thread_resolution.py \\
        --repo OWNER/REPO \\
        --pr-number <num> \\
        --thread-id <PRRT_id> \\
        --expected-head <sha> \\
        [--approved-bot LOGIN] (repeatable or comma-separated) \\
        --diff-scope-status SCOPE_CLEAN \\
        --tests-status TESTS_GREEN \\
        --ci-status CI_GREEN \\
        --scope-guard-status SCOPE_CLEAN \\
        --verifier-status FIX_VERIFIED \\
        --output-json /tmp/status.json \\
        --output-md /tmp/status.md

Exit codes:
    0 = ELIGIBLE_CURRENT_BOT_THREAD_RESOLUTION
    1 = HOLD_* (blocked)
    2 = ERROR_TOOL_FAILURE
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Result statuses
# ---------------------------------------------------------------------------

ELIGIBLE_CURRENT_BOT_THREAD_RESOLUTION = "ELIGIBLE_CURRENT_BOT_THREAD_RESOLUTION"

HOLD_THREAD_NOT_FOUND = "HOLD_THREAD_NOT_FOUND"
HOLD_THREAD_ALREADY_RESOLVED = "HOLD_THREAD_ALREADY_RESOLVED"
HOLD_THREAD_OUTDATED_USE_STALE_CHECKER = "HOLD_THREAD_OUTDATED_USE_STALE_CHECKER"
HOLD_THREAD_NOT_CURRENT_HEAD = "HOLD_THREAD_NOT_CURRENT_HEAD"
HOLD_THREAD_NOT_BOT_AUTHORED = "HOLD_THREAD_NOT_BOT_AUTHORED"
HOLD_THREAD_AUTHOR_NOT_APPROVED = "HOLD_THREAD_AUTHOR_NOT_APPROVED"
HOLD_HEAD_MISMATCH = "HOLD_HEAD_MISMATCH"
HOLD_DIFF_SCOPE_NOT_CLEAN = "HOLD_DIFF_SCOPE_NOT_CLEAN"
HOLD_TESTS_NOT_GREEN = "HOLD_TESTS_NOT_GREEN"
HOLD_CI_NOT_GREEN = "HOLD_CI_NOT_GREEN"
HOLD_SCOPE_GUARD_NOT_CLEAN = "HOLD_SCOPE_GUARD_NOT_CLEAN"
HOLD_FIX_NOT_VERIFIED = "HOLD_FIX_NOT_VERIFIED"
ERROR_TOOL_FAILURE = "ERROR_TOOL_FAILURE"

HOLD_STATUSES = (
    HOLD_THREAD_NOT_FOUND,
    HOLD_THREAD_ALREADY_RESOLVED,
    HOLD_THREAD_OUTDATED_USE_STALE_CHECKER,
    HOLD_THREAD_NOT_CURRENT_HEAD,
    HOLD_THREAD_NOT_BOT_AUTHORED,
    HOLD_THREAD_AUTHOR_NOT_APPROVED,
    HOLD_HEAD_MISMATCH,
    HOLD_DIFF_SCOPE_NOT_CLEAN,
    HOLD_TESTS_NOT_GREEN,
    HOLD_CI_NOT_GREEN,
    HOLD_SCOPE_GUARD_NOT_CLEAN,
    HOLD_FIX_NOT_VERIFIED,
    ERROR_TOOL_FAILURE,
)


# ---------------------------------------------------------------------------
# Evidence expectations
# ---------------------------------------------------------------------------

# Required string values for the deterministic evidence fields. The caller
# is responsible for running the relevant checks (tests, CI, scope_guard,
# verifier) and reporting their results via these flags. The checker only
# confirms the reported values match the policy.
EXPECTED_DIFF_SCOPE_STATUS = "SCOPE_CLEAN"
EXPECTED_TESTS_STATUS = "TESTS_GREEN"
EXPECTED_CI_STATUS = "CI_GREEN"
EXPECTED_SCOPE_GUARD_STATUS = "SCOPE_CLEAN"
EXPECTED_VERIFIER_STATUS = "FIX_VERIFIED"

# Default approved bot logins (normalized to bare login, no [bot] suffix).
# Additional bots can be added via repeated --approved-bot flags.
DEFAULT_APPROVED_BOTS = {"chatgpt-codex-connector"}


# ---------------------------------------------------------------------------
# Login normalization
# ---------------------------------------------------------------------------

def normalize_login(login: str) -> str:
    """Return the login with the [bot] suffix stripped and lower-cased.

    >>> normalize_login("chatgpt-codex-connector[bot]")
    'chatgpt-codex-connector'
    >>> normalize_login("chatgpt-codex-connector")
    'chatgpt-codex-connector'
    >>> normalize_login("Some-Human")
    'some-human'
    """
    if not login:
        return ""
    s = login.strip()
    if s.endswith("[bot]"):
        s = s[: -len("[bot]")]
    return s.lower()


def is_bot_login(login: str) -> bool:
    """Return True if the login represents a GitHub bot account.

    GitHub bot logins conventionally end with [bot]. The GraphQL API may
    return either the suffixed or unsuffixed form, so we accept both as
    bot-shaped inputs but only treat explicitly [bot]-suffixed logins
    as bots for the policy check.
    """
    if not login:
        return False
    return login.strip().endswith("[bot]")


# ---------------------------------------------------------------------------
# REST path helper
# ---------------------------------------------------------------------------

def repo_api_path(repo: str, *parts: str) -> str:
    """Build a GitHub REST API path from owner/name and trailing segments.

    >>> repo_api_path("Slideshow11/Automated-Edge-Discovery", "pulls", "376")
    'repos/Slideshow11/Automated-Edge-Discovery/pulls/376'
    """
    owner, name = repo.split("/", 1)
    return "/".join(["repos", owner, name, *parts])


# ---------------------------------------------------------------------------
# GitHub API helpers (list-argv, never invokes a shell, no mutations)
# ---------------------------------------------------------------------------

def gh_api(*args: str) -> dict:
    """Run gh api with given args. Returns parsed JSON dict or raises.

    Only read-only REST and GraphQL query endpoints are used by this
    module. The single GraphQL call is a read-only query operation.
    """
    result = subprocess.run(
        ["gh", "api"] + list(args),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api failed: {result.stderr}")
    if result.stdout.strip():
        return json.loads(result.stdout)
    return {}


def fetch_pr_state_and_head(repo: str, pr_number: int) -> Tuple[str, str]:
    """Return (state, head_sha) for a PR via the REST pulls endpoint."""
    data = gh_api(repo_api_path(repo, "pulls", str(pr_number)))
    return data.get("state", "UNKNOWN"), data.get("head", {}).get("sha", "")


# ------------------------------------------------------------------
# Pagination knobs
# ------------------------------------------------------------------

# Threads per page when scanning pullRequest.reviewThreads. GitHub's
# Relay-style connection accepts any value; 50 keeps the per-page
# payload small and lets us fan out across the most common PRs.
THREAD_PAGE_SIZE = 50

# Comments per page when reading thread.comments. 50 mirrors the
# thread page size so a single bot thread with up to 50 comments
# fits in one round trip.
COMMENT_PAGE_SIZE = 50

# Defensive upper bound on pages scanned. 40 pages * 50 = 2000,
# which is many orders of magnitude above a typical PR but caps
# pathological inputs (corrupt cursors, GitHub API bugs, etc.).
MAX_THREAD_PAGES = 40
MAX_COMMENT_PAGES = 40


def fetch_thread(repo: str, pr_number: int, thread_id: str) -> Optional[dict]:
    """Fetch a single review thread via paginated, read-only GraphQL.

    Phase 1 pages through pullRequest.reviewThreads using
    pageInfo.hasNextPage / endCursor until the target thread is found
    or all pages are exhausted. Phase 2 then pages the target thread's
    comments using comments.pageInfo until every comment is collected.

    Returns a thread dict with all comments merged, or None if the
    target thread is not present after the full scan. No write
    operation is ever constructed here; every gh_api call is a
    read-only query.
    """
    owner, name = repo.split("/", 1)

    # ----- Phase 1: page through reviewThreads to find the target ---
    cursor_arg = "null"
    pages_scanned = 0
    target_meta: Optional[dict] = None
    while pages_scanned < MAX_THREAD_PAGES:
        pages_scanned += 1
        q = (
            '{repository(owner:"' + owner + '",name:"' + name + '"){'
            'pullRequest(number:' + str(pr_number) + '){'
            'reviewThreads(first:' + str(THREAD_PAGE_SIZE)
            + ',after:' + cursor_arg + '){'
            'pageInfo{hasNextPage endCursor}'
            'nodes{id isResolved isOutdated path line}'
            '}}}}'
        )
        data = gh_api("graphql", "--field", f"query={q}")
        conn = (
            data.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
        ) or {}
        for node in conn.get("nodes") or []:
            if node.get("id") == thread_id:
                target_meta = node
                break
        if target_meta is not None:
            break
        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return None
        cursor_arg = '"' + (page_info.get("endCursor") or "") + '"'

    if target_meta is None:
        # Defensive: bounded scan reached the page limit without
        # finding the target. Treat as not-found rather than
        # silently truncating.
        return None

    # ----- Phase 2: page through comments on the target thread -------
    return _fetch_thread_with_all_comments(target_meta)


def _fetch_thread_with_all_comments(thread_meta: dict) -> Optional[dict]:
    """Fetch every comment on the given thread via paginated GraphQL.

    Pages thread.comments using comments.pageInfo.hasNextPage /
    endCursor until the connection is exhausted. Returns a fully
    populated thread dict with all comments merged into a single
    "comments": {"nodes": [...]} list, or None if the node lookup
    fails. The function only issues read-only queries and never
    mutates GitHub state.
    """
    thread_id = thread_meta.get("id", "")
    if not thread_id:
        return None

    cursor_arg = "null"
    pages_scanned = 0
    all_comments: list = []
    last_node: dict = {}
    while pages_scanned < MAX_COMMENT_PAGES:
        pages_scanned += 1
        q = (
            '{node(id:"' + thread_id + '"){'
            '... on PullRequestReviewThread{'
            'id isResolved isOutdated path line '
            'comments(first:' + str(COMMENT_PAGE_SIZE)
            + ',after:' + cursor_arg + '){'
            'pageInfo{hasNextPage endCursor}'
            'nodes{body author{login} createdAt}'
            '}}}}'
        )
        data = gh_api("graphql", "--field", f"query={q}")
        node = data.get("data", {}).get("node")
        if not node:
            return None
        last_node = node
        comments_conn = node.get("comments") or {}
        all_comments.extend(comments_conn.get("nodes") or [])
        page_info = comments_conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor_arg = '"' + (page_info.get("endCursor") or "") + '"'

    return {
        "id": last_node.get("id"),
        "isResolved": last_node.get("isResolved"),
        "isOutdated": last_node.get("isOutdated"),
        "path": last_node.get("path"),
        "line": last_node.get("line"),
        "comments": {"nodes": all_comments},
    }


def parse_approved_bots(raw: Optional[Iterable[str]]) -> Set[str]:
    """Parse --approved-bot values, supporting both repeat and comma forms.

    Returns a set of normalized (lower-cased, [bot]-stripped) bot logins.
    """
    bots: Set[str] = set(DEFAULT_APPROVED_BOTS)
    if not raw:
        return bots
    for r in raw:
        if not r:
            continue
        for part in str(r).split(","):
            part = part.strip()
            if not part:
                continue
            bots.add(normalize_login(part))
    return bots


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def evaluate(
    repo: str,
    pr_number: int,
    thread_id: str,
    expected_head: str,
    approved_bots: Set[str],
    diff_scope_status: str,
    tests_status: str,
    ci_status: str,
    scope_guard_status: str,
    verifier_status: str,
) -> Tuple[str, dict]:
    """Evaluate whether the thread passes all policy preconditions.

    Returns (status, audit_dict). The audit dict is always populated with
    the inputs and any evidence gathered during evaluation, regardless of
    the final status.
    """
    audit = dict(
        repo=repo,
        pr_number=pr_number,
        thread_id=thread_id,
        expected_head=expected_head,
        approved_bots=sorted(approved_bots),
        diff_scope_status=diff_scope_status,
        tests_status=tests_status,
        ci_status=ci_status,
        scope_guard_status=scope_guard_status,
        verifier_status=verifier_status,
    )

    # 1. PR state + head (REST read)
    try:
        state, head = fetch_pr_state_and_head(repo, pr_number)
        audit["pr_state"] = state
        audit["actual_head"] = head
    except Exception as e:  # pragma: no cover - error path
        audit["pr_fetch_error"] = str(e)
        return ERROR_TOOL_FAILURE, audit

    # 2. Head match (deterministic evidence: expected SHA must match actual)
    if head != expected_head:
        audit["head_mismatch"] = True
        return HOLD_HEAD_MISMATCH, audit
    audit["head_mismatch"] = False

    # 3. Thread fetch (GraphQL read)
    try:
        thread = fetch_thread(repo, pr_number, thread_id)
        audit["thread_found"] = thread is not None
    except Exception as e:  # pragma: no cover - error path
        audit["thread_fetch_error"] = str(e)
        return ERROR_TOOL_FAILURE, audit

    if thread is None:
        return HOLD_THREAD_NOT_FOUND, audit

    # 4. Capture thread state for the audit
    is_resolved = bool(thread.get("isResolved", False))
    is_outdated = bool(thread.get("isOutdated", False))
    audit["is_resolved"] = is_resolved
    audit["is_outdated"] = is_outdated

    # 5. Already resolved — not eligible under any policy
    if is_resolved:
        return HOLD_THREAD_ALREADY_RESOLVED, audit

    # 6. Outdated — redirect to the stale-thread checker
    if is_outdated:
        return HOLD_THREAD_OUTDATED_USE_STALE_CHECKER, audit

    # 7. Defensive not-current-head check. If neither isResolved nor
    # isOutdated is True, the thread IS current-head; this branch is
    # only reachable if both flags are present and truthy in an
    # unexpected state, or if the GraphQL response is malformed.
    if is_resolved or is_outdated:
        return HOLD_THREAD_NOT_CURRENT_HEAD, audit

    # 8. Author checks
    comments = thread.get("comments", {}).get("nodes", []) or []
    authors_raw = [c.get("author", {}).get("login", "") for c in comments]
    audit["authors"] = authors_raw
    audit["authors_normalized"] = [normalize_login(a) for a in authors_raw]
    all_bots = bool(authors_raw) and all(is_bot_login(a) for a in authors_raw)
    audit["all_authors_bots"] = all_bots

    # 8a. Human-authored threads are never eligible
    if not all_bots:
        return HOLD_THREAD_NOT_BOT_AUTHORED, audit

    # 8b. Bot authors must all be in the approved set
    approved_normalized = {normalize_login(b) for b in approved_bots}
    unapproved = [a for a in authors_raw if normalize_login(a) not in approved_normalized]
    audit["unapproved_authors"] = unapproved
    if unapproved:
        return HOLD_THREAD_AUTHOR_NOT_APPROVED, audit

    # 9. Deterministic evidence field checks
    if diff_scope_status != EXPECTED_DIFF_SCOPE_STATUS:
        audit["expected_diff_scope_status"] = EXPECTED_DIFF_SCOPE_STATUS
        return HOLD_DIFF_SCOPE_NOT_CLEAN, audit

    if tests_status != EXPECTED_TESTS_STATUS:
        audit["expected_tests_status"] = EXPECTED_TESTS_STATUS
        return HOLD_TESTS_NOT_GREEN, audit

    if ci_status != EXPECTED_CI_STATUS:
        audit["expected_ci_status"] = EXPECTED_CI_STATUS
        return HOLD_CI_NOT_GREEN, audit

    if scope_guard_status != EXPECTED_SCOPE_GUARD_STATUS:
        audit["expected_scope_guard_status"] = EXPECTED_SCOPE_GUARD_STATUS
        return HOLD_SCOPE_GUARD_NOT_CLEAN, audit

    if verifier_status != EXPECTED_VERIFIER_STATUS:
        audit["expected_verifier_status"] = EXPECTED_VERIFIER_STATUS
        return HOLD_FIX_NOT_VERIFIED, audit

    return ELIGIBLE_CURRENT_BOT_THREAD_RESOLUTION, audit


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_json(path: str, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def write_md(path: str, status: str, audit: dict) -> None:
    lines = [
        f"# Current-Head Bot Thread Resolution Check — PR #{audit.get('pr_number', '?')}",
        "",
        f"**Status**: `{status}`",
        "",
        "## Audit Record",
        "",
    ]
    for k, v in audit.items():
        if isinstance(v, list):
            lines.append(f"- **{k}**: {', '.join(str(x) for x in v)}")
        else:
            lines.append(f"- **{k}**: `{v}`")
    lines.extend([
        "",
        "## Determination",
        "",
        f"`{status}`",
        "",
        "*Dry-run only: no GitHub state was modified.*",
    ])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run current-head bot review-thread eligibility checker "
            "(audit-only, never mutates GitHub state)."
        )
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument(
        "--approved-bot",
        action="append",
        default=[],
        help="Approved bot login (repeatable or comma-separated).",
    )
    parser.add_argument("--diff-scope-status", required=True)
    parser.add_argument("--tests-status", required=True)
    parser.add_argument("--ci-status", required=True)
    parser.add_argument("--scope-guard-status", required=True)
    parser.add_argument("--verifier-status", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    approved = parse_approved_bots(args.approved_bot)

    status, audit = evaluate(
        repo=args.repo,
        pr_number=args.pr_number,
        thread_id=args.thread_id,
        expected_head=args.expected_head,
        approved_bots=approved,
        diff_scope_status=args.diff_scope_status,
        tests_status=args.tests_status,
        ci_status=args.ci_status,
        scope_guard_status=args.scope_guard_status,
        verifier_status=args.verifier_status,
    )
    audit["status"] = status
    audit["evaluated_at"] = datetime.now(timezone.utc).isoformat()

    write_json(args.output_json, audit)
    write_md(args.output_md, status, audit)

    print(f"Status: {status}")
    print(f"Output: {args.output_json}")

    if status == ELIGIBLE_CURRENT_BOT_THREAD_RESOLUTION:
        return 0
    if status in HOLD_STATUSES:
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
