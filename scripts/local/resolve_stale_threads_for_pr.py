#!/usr/bin/env python3
"""
resolve_stale_threads_for_pr.py — Read-only PR review-thread lifecycle auditor.

Lists unresolved review threads, classifies current-head vs outdated,
identifies candidate stale-checker invocations, and produces JSON/Markdown
reports. Does NOT resolve threads, mutate GitHub state, or call any GraphQL
mutation in v1.

Usage:
    python3 scripts/local/resolve_stale_threads_for_pr.py \
        --repo OWNER/REPO \
        --pr-number INT \
        [--expected-head SHA] \
        [--base-ref main] \
        --output-json /tmp/threads.json \
        --output-md /tmp/threads.md \
        [--max-threads 100] \
        [--ignore-users user1,user2] \
        [--include-resolved] \
        [--suggest-patterns]

Exit codes:
    0  — report written
    1  — ERROR_TOOL_FAILURE

Status values (JSON report.status):
    THREAD_AUDIT_CLEAN
    HOLD_CURRENT_THREADS_PRESENT
    HOLD_OUTDATED_THREADS_PRESENT
    HOLD_HEAD_CHANGED
    HOLD_REVIEW_THREAD_PAGINATION_REQUIRED
    ERROR_TOOL_FAILURE
"""

import argparse
import json
import subprocess
import sys
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Result statuses
# ---------------------------------------------------------------------------

STATUS_THREAD_AUDIT_CLEAN = "THREAD_AUDIT_CLEAN"
STATUS_HOLD_CURRENT_THREADS_PRESENT = "HOLD_CURRENT_THREADS_PRESENT"
STATUS_HOLD_OUTDATED_THREADS_PRESENT = "HOLD_OUTDATED_THREADS_PRESENT"
STATUS_HOLD_HEAD_CHANGED = "HOLD_HEAD_CHANGED"
STATUS_HOLD_REVIEW_THREAD_PAGINATION_REQUIRED = "HOLD_REVIEW_THREAD_PAGINATION_REQUIRED"
STATUS_ERROR_TOOL_FAILURE = "ERROR_TOOL_FAILURE"

# Words too broad to use as flagged-pattern suggestions.
BROAD_TOKENS = frozenset({
    "mutation", "file", "line", "code", "thread", "comment",
    "pr", "repo", "git", "api", "json", "md", "sha", "head",
    "merge", "review", "resolve", "status", "error", "warning",
    "check", "test", "run", "workflow", "action",
})

# ---------------------------------------------------------------------------
# Admin guard
# ---------------------------------------------------------------------------

ADMIN_FORBIDDEN = frozenset({"--admin", "--admin-flag"})


def reject_admin(argv: list[str]) -> None:
    """Raise ValueError if any forbidden admin flag is present."""
    for token in argv:
        if token in ADMIN_FORBIDDEN:
            raise ValueError(f"Forbidden flag in argv: {token}")


# ---------------------------------------------------------------------------
# GitHub API helpers (read-only, list-argv, no shell=True)
# ---------------------------------------------------------------------------


def gh_pr_view(repo: str, pr_number: int) -> dict[str, Any]:
    """Fetch PR metadata via gh pr view (read-only)."""
    result = subprocess.run(
        [
            "gh", "pr", "view", str(pr_number),
            "--repo", repo,
            "--json", "number,title,state,headRefOid,baseRefOid,url",
            "--jq", ".",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh pr view failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def _build_threads_query(owner: str, name: str, pr_number: int,
                         first: int, after_cursor: str | None) -> str:
    """Build a review-threads GraphQL query string (read-only)."""
    after_part = ""
    if after_cursor:
        after_part = ",after:\"" + after_cursor + "\""
    return (
        "{repository(owner:\""
        + owner + "\",name:\""
        + name + "\"){"
        "pullRequest(number:"
        + str(pr_number) + "){"
        "reviewThreads(first:"
        + str(first)
        + after_part
        + "){"
        "pageInfo{hasNextPage endCursor}"
        "nodes{"
        "id isResolved isOutdated path line"
        "comments(first:20){nodes{body author{login}}}"
        "}}}"
        "}}"
    )


def gh_graphql_review_threads(
    repo: str,
    pr_number: int,
    after_cursor: str | None = None,
    first: int = 100,
) -> tuple[bool, list[dict[str, Any]], str, bool]:
    """
    Fetch PR review threads via GraphQL (read-only query).

    Returns (success, nodes, error_msg, has_next_page).
    """
    owner, name = repo.split("/", 1)
    query_literal = _build_threads_query(owner, name, pr_number, first, after_cursor)

    cmd = ["gh", "api", "graphql", "--raw-field", "query=" + query_literal]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)
    except OSError as exc:
        return False, [], "gh graphql invocation failed: " + str(exc), False

    if result.returncode != 0:
        return False, [], ("gh graphql returned " + str(result.returncode) + ": " + result.stderr[:500]), False

    try:
        data = json.loads(result.stdout)
        errors = data.get("errors")
        if errors:
            return False, [], "GraphQL errors: " + str(errors), False

        pr_data = (
            data.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
        )
        threads_data = pr_data.get("reviewThreads", {})
        page_info = threads_data.get("pageInfo", {})
        nodes = threads_data.get("nodes", [])
        has_next_page = page_info.get("hasNextPage", False)
        return True, nodes, "", has_next_page

    except (json.JSONDecodeError, KeyError) as exc:
        return False, [], "invalid GraphQL response: " + str(exc), False


# ---------------------------------------------------------------------------
# Severity extraction
# ---------------------------------------------------------------------------

SEVERITY_PATTERN = re.compile(r"\b(P0|P1|P2|P3)\b", re.IGNORECASE)
SEVERITY_MAP = {"high": "P1", "medium": "P2", "low": "P3"}


def extract_severity(text: str) -> str:
    """Return P0-P3 from text, or 'unknown' if not found."""
    upper = text.upper()
    for sev in ("P0", "P1", "P2", "P3"):
        if sev in upper:
            return sev
    for token, sev in SEVERITY_MAP.items():
        if token in upper:
            return sev
    return "unknown"


# ---------------------------------------------------------------------------
# Suggestion generation
# ---------------------------------------------------------------------------

QUOTE_PATTERN = re.compile(r"`[^`]+`|'[^']+'|\"[^\"]+\"")


def extract_candidate_patterns(body: str) -> list[dict[str, Any]]:
    """
    Extract candidate flagged patterns from a comment body.

    Returns a list of suggestion dicts with keys:
        candidate_only, pattern_type, pattern_value, pattern_source

    Each pattern is marked candidate_only=True — not verified as addressing
    the issue, only extracted from the comment as a candidate suggestion.
    """
    suggestions = []

    # Extract quoted code-like snippets
    for match in QUOTE_PATTERN.finditer(body):
        snippet = match.group(0)[1:-1].strip()
        if snippet and len(snippet) >= 3 and snippet.lower() not in BROAD_TOKENS:
            suggestions.append({
                "candidate_only": True,
                "pattern_type": "quoted_snippet",
                "pattern_value": snippet,
                "pattern_source": "comment_body_quoted",
            })

    # Extract first sentence fragment (up to 8 words)
    first_sentence = body.split(".")[0].strip()
    words = first_sentence.split()
    if len(words) >= 3:
        fragment = " ".join(words[:8])
        lower_fragment = fragment.lower()
        if lower_fragment not in BROAD_TOKENS:
            suggestions.append({
                "candidate_only": True,
                "pattern_type": "sentence_fragment",
                "pattern_value": fragment,
                "pattern_source": "comment_body_first_sentence",
            })

    # Deduplicate by pattern_value
    seen: set[str] = set()
    deduped = []
    for s in suggestions:
        if s["pattern_value"] not in seen:
            seen.add(s["pattern_value"])
            deduped.append(s)

    return deduped


# ---------------------------------------------------------------------------
# Core classification
# ---------------------------------------------------------------------------


def classify_threads(
    threads: list[dict[str, Any]],
    ignore_users: set[str],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """
    Classify all threads into:
      - resolved
      - unresolved_outdated
      - unresolved_current
      - ignored (unresolved current threads by ignored users)

    Returns (resolved, unresolved_outdated, unresolved_current, ignored).
    """
    resolved = []
    unresolved_outdated = []
    unresolved_current = []
    ignored = []

    for thread in threads:
        is_resolved = thread.get("isResolved", False)
        is_outdated = thread.get("isOutdated", False)

        comments = thread.get("comments", {}).get("nodes", [])
        first_author = ""
        if comments:
            first_author = (comments[0].get("author", {}) or {}).get("login", "")

        body_text = (comments[0].get("body", "") if comments else "")
        thread_info = {
            "id": thread.get("id", ""),
            "isResolved": is_resolved,
            "isOutdated": is_outdated,
            "path": thread.get("path") or "",
            "line": thread.get("line") or None,
            "author": first_author,
            "comment_excerpt": body_text[:200],
            "severity": extract_severity(body_text),
            "suggested_patterns": extract_candidate_patterns(body_text),
        }

        if is_resolved:
            resolved.append(thread_info)
        elif is_outdated:
            unresolved_outdated.append(thread_info)
        elif first_author in ignore_users:
            ignored.append(thread_info)
        else:
            unresolved_current.append(thread_info)

    return resolved, unresolved_outdated, unresolved_current, ignored


def build_checker_suggestion(
    repo: str,
    pr_number: int,
    thread: dict[str, Any],
    expected_head: str,
    base_ref: str,
) -> dict[str, Any]:
    """Build a candidate check_stale_review_thread_resolution.py invocation dict."""
    suggestions = thread.get("suggested_patterns", [])
    primary_pattern = (suggestions[0]["pattern_value"] if suggestions else "FIXED")

    cmd_parts = [
        "python3", "scripts/local/check_stale_review_thread_resolution.py",
        "--repo", repo,
        "--pr-number", str(pr_number),
        "--thread-id", thread["id"],
        "--expected-head", expected_head,
        "--base-ref", base_ref,
        "--flagged-pattern", primary_pattern,
        "--output-json", "/tmp/stale_check.json",
        "--output-md", "/tmp/stale_check.md",
    ]

    return {
        "thread_id": thread["id"],
        "thread_path": thread.get("path", ""),
        "thread_line": thread.get("line"),
        "author": thread.get("author", ""),
        "severity": thread.get("severity", "unknown"),
        "command": " ".join(cmd_parts),
        "command_parts": cmd_parts,
        "primary_candidate_pattern": primary_pattern,
        "candidate_patterns": suggestions,
        "candidate_only": True,
    }


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------


def write_json(path: str, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def write_md(path: str, status: str, report: dict) -> None:
    lines = [
        "# PR Review Thread Audit — PR #" + str(report.get("pr_number", "?")),
        "",
        "**Status**: `" + status + "`",
        "",
        "## PR / Head",
        "",
        "- **Repo**: `" + report.get("repo", "") + "`",
        "- **PR**: #" + str(report.get("pr_number", "")),
        "- **Head SHA**: `" + report.get("head_sha", "") + "`",
        "- **Expected Head**: `" + report.get("expected_head", "") + "`",
        "",
        "## Thread Counts",
        "",
        "- **Unresolved current**: " + str(report.get("unresolved_current_count", 0)),
        "- **Unresolved outdated**: " + str(report.get("unresolved_outdated_count", 0)),
        "- **Resolved** (included): " + str(report.get("resolved_count", 0)),
        "- **Ignored author**: " + str(report.get("ignored_count", 0)),
        "",
    ]

    current = report.get("unresolved_current_threads", [])
    if current:
        lines.append("## Current Unresolved Blockers")
        lines.append("")
        for t in current:
            sev_marker = "[" + t.get("severity", "unknown").upper() + "]"
            path_str = t.get("path", "")
            line_str = str(t.get("line", "?"))
            lines.append("- **" + sev_marker + "** `" + t.get("id", "") + "` — `" + path_str + ":" + line_str + "` by @" + t.get("author", ""))
            excerpt = t.get("comment_excerpt", "")
            if excerpt:
                lines.append("  > " + excerpt[:120])
            lines.append("")

    outdated = report.get("unresolved_outdated_threads", [])
    if outdated:
        lines.append("## Outdated Unresolved Threads (Stale Checker Candidates)")
        lines.append("")
        for t in outdated:
            path_str = t.get("path", "")
            line_str = str(t.get("line", "?"))
            lines.append("- `" + t.get("id", "") + "` — `" + path_str + ":" + line_str + "` by @" + t.get("author", ""))
            lines.append("")

    suggestions = report.get("suggested_checker_invocations", [])
    if suggestions:
        lines.append("## Suggested Stale-Checker Invocations")
        lines.append("")
        lines.append("*The following are candidate suggestions only. Do not execute without review.*")
        lines.append("")
        for s in suggestions:
            lines.append("```bash")
            lines.append(s.get("command", ""))
            lines.append("```")
            lines.append("")

    lines.extend([
        "## Safety Invariants",
        "",
        "**v1 is audit/report-only. No threads were resolved.**",
        "",
        "- :x: `--admin` is forbidden (rejected at argv parse time)",
        "- :x: `--execute-resolutions` does not exist in v1",
        "- :x: No GraphQL mutation is called (read-only query only)",
        "- :x: `resolved_any` is always `false` in v1",
        "- :x: `execute_resolutions_supported` is always `false` in v1",
        "- :white_check_mark: `mutated_github` is always `false` in v1",
        "",
        "*This report was produced by resolve_stale_threads_for_pr.py v1 — read-only auditor.*",
    ])

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only PR review-thread lifecycle auditor.",
    )
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--pr-number", required=True, type=int, help="PR number")
    parser.add_argument("--expected-head", default=None, help="Expected head SHA")
    parser.add_argument("--base-ref", default="main", help="Base branch ref (default: main)")
    parser.add_argument("--output-json", required=True, help="Path to JSON report")
    parser.add_argument("--output-md", required=True, help="Path to Markdown report")
    parser.add_argument("--max-threads", default=100, type=int,
                        help="Max threads to fetch per page (default: 100)")
    parser.add_argument("--ignore-users", default="",
                        help="Comma-separated users to ignore in reporting")
    parser.add_argument("--include-resolved", action="store_true",
                        help="Include resolved threads in report")
    parser.add_argument("--suggest-patterns", dest="suggest_patterns",
                        action="store_true", default=True,
                        help="Generate candidate stale-checker patterns (default: true)")
    # Hidden flag to suppress patterns without breaking existing calls
    parser.add_argument("--no-suggest-patterns", dest="no_suggest",
                        action="store_true", default=False,
                        help=argparse.SUPPRESS)

    args = parser.parse_args()

    # --admin rejection
    try:
        reject_admin(sys.argv)
    except ValueError as e:
        report = {
            "status": STATUS_ERROR_TOOL_FAILURE,
            "repo": args.repo,
            "pr_number": args.pr_number,
            "error": str(e),
            "mutated_github": False,
            "resolved_any": False,
            "execute_resolutions_supported": False,
            "unresolved_current_count": 0,
            "unresolved_outdated_count": 0,
            "resolved_count": 0,
            "ignored_count": 0,
        }
        write_json(args.output_json, report)
        write_md(args.output_md, STATUS_ERROR_TOOL_FAILURE, report)
        return 0  # report written, exit cleanly

    if args.no_suggest:
        args.suggest_patterns = False

    # Normalise ignore users
    ignore_set = {
        u.strip()
        for u in args.ignore_users.split(",")
        if u.strip()
    }

    # Fetch PR metadata
    try:
        pr_data = gh_pr_view(args.repo, args.pr_number)
    except Exception as e:
        report = {
            "status": STATUS_ERROR_TOOL_FAILURE,
            "repo": args.repo,
            "pr_number": args.pr_number,
            "error": str(e),
            "mutated_github": False,
            "resolved_any": False,
            "execute_resolutions_supported": False,
        }
        write_json(args.output_json, report)
        write_md(args.output_md, STATUS_ERROR_TOOL_FAILURE, report)
        return 0

    head_sha = pr_data.get("headRefOid", "")
    pr_state = pr_data.get("state", "unknown")

    # Head mismatch check
    if args.expected_head and head_sha != args.expected_head:
        status = STATUS_HOLD_HEAD_CHANGED
        report = {
            "status": status,
            "repo": args.repo,
            "pr_number": args.pr_number,
            "head_sha": head_sha,
            "expected_head": args.expected_head,
            "pr_state": pr_state,
            "unresolved_current_count": 0,
            "unresolved_outdated_count": 0,
            "resolved_count": 0,
            "ignored_count": 0,
            "mutated_github": False,
            "resolved_any": False,
            "execute_resolutions_supported": False,
            "unresolved_current_threads": [],
            "unresolved_outdated_threads": [],
            "resolved_threads": [],
            "ignored_threads": [],
            "suggested_checker_invocations": [],
            "pagination_used": False,
        }
        write_json(args.output_json, report)
        write_md(args.output_md, status, report)
        return 0

    # Fetch all threads with pagination
    all_nodes: list[dict[str, Any]] = []
    after_cursor: str | None = None
    has_next_page = False
    page_count = 0

    while True:
        success, nodes, err_msg, has_next_page = gh_graphql_review_threads(
            args.repo, args.pr_number, after_cursor=after_cursor, first=args.max_threads
        )
        if not success:
            report = {
                "status": STATUS_ERROR_TOOL_FAILURE,
                "repo": args.repo,
                "pr_number": args.pr_number,
                "head_sha": head_sha,
                "expected_head": args.expected_head or "",
                "error": err_msg,
                "mutated_github": False,
                "resolved_any": False,
                "execute_resolutions_supported": False,
            }
            write_json(args.output_json, report)
            write_md(args.output_md, STATUS_ERROR_TOOL_FAILURE, report)
            return 0

        all_nodes.extend(nodes)
        page_count += 1

        if not has_next_page:
            break

        # Safety: prevent infinite pagination (max 10 pages)
        if page_count >= 10:
            report = {
                "status": STATUS_HOLD_REVIEW_THREAD_PAGINATION_REQUIRED,
                "repo": args.repo,
                "pr_number": args.pr_number,
                "head_sha": head_sha,
                "expected_head": args.expected_head or "",
                "threads_collected": len(all_nodes),
                "pagination_pages": page_count,
                "mutated_github": False,
                "resolved_any": False,
                "execute_resolutions_supported": False,
            }
            write_json(args.output_json, report)
            write_md(args.output_md, STATUS_HOLD_REVIEW_THREAD_PAGINATION_REQUIRED, report)
            return 0

        # Get endCursor from this page's pageInfo
        # Re-run the same query and extract endCursor from the last returned page
        _, _, _, _ = gh_graphql_review_threads(
            args.repo, args.pr_number, after_cursor=None, first=args.max_threads
        )
        # Simpler: track the cursor from the last successful response
        # gh_graphql_review_threads with after_cursor=None re-fetches page 1
        # Instead, we capture endCursor from the page we just received
        # by re-calling with the same cursor to get pageInfo
        if has_next_page:
            # Get cursor from the page we just got by re-querying with after cursor
            cursor_success, cursor_nodes, _, _ = gh_graphql_review_threads(
                args.repo, args.pr_number,
                after_cursor=after_cursor if after_cursor else None,
                first=args.max_threads
            )
            # The nodes we want are already in all_nodes; we need pageInfo from
            # a query that includes endCursor. Use the query directly.
            owner, name = args.repo.split("/", 1)
            cursor_query = _build_threads_query(
                owner, name, args.pr_number, args.max_threads,
                after_cursor
            )
            cursor_result = subprocess.run(
                ["gh", "api", "graphql", "--raw-field", "query=" + cursor_query],
                capture_output=True, text=True, check=False, timeout=30,
            )
            end_cursor = None
            if cursor_result.returncode == 0:
                try:
                    cursor_data = json.loads(cursor_result.stdout)
                    end_cursor = (
                        cursor_data.get("data", {})
                        .get("repository", {})
                        .get("pullRequest", {})
                        .get("reviewThreads", {})
                        .get("pageInfo", {})
                        .get("endCursor")
                    )
                except Exception:
                    pass
            if end_cursor:
                after_cursor = end_cursor
            else:
                break

    # Classify threads
    resolved, unresolved_outdated, unresolved_current, ignored = classify_threads(
        all_nodes, ignore_set
    )

    # Determine status
    if unresolved_current:
        status = STATUS_HOLD_CURRENT_THREADS_PRESENT
    elif unresolved_outdated:
        status = STATUS_HOLD_OUTDATED_THREADS_PRESENT
    else:
        status = STATUS_THREAD_AUDIT_CLEAN

    # Build checker suggestions for outdated threads
    suggestions = []
    expected = args.expected_head or head_sha
    for thread in unresolved_outdated:
        suggestions.append(build_checker_suggestion(
            args.repo, args.pr_number, thread, expected, args.base_ref
        ))

    # Build thread lists for JSON
    def thread_to_dict(t: dict[str, Any], outdated: bool) -> dict[str, Any]:
        return {
            "id": t["id"],
            "path": t.get("path", ""),
            "line": t.get("line"),
            "author": t.get("author", ""),
            "severity": t.get("severity", "unknown"),
            "comment_excerpt": t.get("comment_excerpt", ""),
            "isOutdated": outdated,
            "isResolved": False,
            "suggested_patterns": t.get("suggested_patterns", []) if args.suggest_patterns else [],
        }

    unresolved_current_out = [thread_to_dict(t, False) for t in unresolved_current]
    unresolved_outdated_out = [thread_to_dict(t, True) for t in unresolved_outdated]

    resolved_out = []
    if args.include_resolved:
        for t in resolved:
            d = thread_to_dict(t, t.get("isOutdated", False))
            d["isResolved"] = True
            d["suggested_patterns"] = []
            resolved_out.append(d)

    ignored_out = []
    for t in ignored:
        d = thread_to_dict(t, t.get("isOutdated", False))
        d["ignored"] = True
        d["suggested_patterns"] = []
        ignored_out.append(d)

    # Build final report
    report = {
        "status": status,
        "repo": args.repo,
        "pr_number": args.pr_number,
        "head_sha": head_sha,
        "expected_head": args.expected_head or "",
        "base_ref": args.base_ref,
        "pr_state": pr_state,
        "thread_counts": {
            "total": len(all_nodes),
            "unresolved_current": len(unresolved_current),
            "unresolved_outdated": len(unresolved_outdated),
            "resolved": len(resolved),
            "ignored": len(ignored),
        },
        "unresolved_current_count": len(unresolved_current),
        "unresolved_outdated_count": len(unresolved_outdated),
        "resolved_count": len(resolved),
        "ignored_count": len(ignored),
        "unresolved_current_threads": unresolved_current_out,
        "unresolved_outdated_threads": unresolved_outdated_out,
        "resolved_threads": resolved_out,
        "ignored_threads": ignored_out,
        "suggested_checker_invocations": suggestions if args.suggest_patterns else [],
        "pagination_used": page_count > 1,
        "pagination_pages": page_count,
        "mutated_github": False,
        "resolved_any": False,
        "execute_resolutions_supported": False,
    }

    write_json(args.output_json, report)
    write_md(args.output_md, status, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())