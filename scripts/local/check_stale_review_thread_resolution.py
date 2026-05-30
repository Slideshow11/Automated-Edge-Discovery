#!/usr/bin/env python3
"""
check_stale_review_thread_resolution.py

Dry-run helper that evaluates whether a single stale review thread is
eligible for auto-resolution under the policy defined in:
  docs/stale_review_thread_auto_resolution_policy.md

This script is inspect-only. It NEVER calls resolveReviewThread, never
dismisses a review, and never mutates any GitHub state.

Usage:
    python3 scripts/local/check_stale_review_thread_resolution.py \
        --repo OWNER/REPO \
        --pr-number <num> \
        --thread-id <PRRT_id> \
        --expected-head <sha> \
        --base-ref main \
        --flagged-pattern <pattern> \
        [--replacement-pattern <pattern>] \
        [--path-scope <path>] \
        --output-json /tmp/status.json \
        --output-md /tmp/status.md

Exit codes:
    0 = ELIGIBLE_STALE_THREAD_RESOLUTION
    1 = HOLD_* (blocked)
    2 = HOLD_UNKNOWN (error)
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Result statuses
# ---------------------------------------------------------------------------
ELIGIBLE_STALE_THREAD_RESOLUTION = "ELIGIBLE_STALE_THREAD_RESOLUTION"

HOLD_PR_NOT_OPEN = "HOLD_PR_NOT_OPEN"
HOLD_HEAD_MISMATCH = "HOLD_HEAD_MISMATCH"
HOLD_THREAD_NOT_FOUND = "HOLD_THREAD_NOT_FOUND"
HOLD_THREAD_NOT_OUTDATED = "HOLD_THREAD_NOT_OUTDATED"
HOLD_THREAD_ALREADY_RESOLVED = "HOLD_THREAD_ALREADY_RESOLVED"
HOLD_FLAGGED_PATTERN_NOT_FOUND_IN_THREAD = "HOLD_FLAGGED_PATTERN_NOT_FOUND_IN_THREAD"
HOLD_FLAGGED_PATTERN_STILL_IN_DIFF = "HOLD_FLAGGED_PATTERN_STILL_IN_DIFF"
HOLD_REPLACEMENT_PATTERN_MISSING = "HOLD_REPLACEMENT_PATTERN_MISSING"
HOLD_DIFF_FETCH_FAILED = "HOLD_DIFF_FETCH_FAILED"
HOLD_UNEXPECTED_CHANGED_FILES = "HOLD_UNEXPECTED_CHANGED_FILES"
HOLD_UNKNOWN = "HOLD_UNKNOWN"

HOLD_STATUSES = (
    HOLD_PR_NOT_OPEN,
    HOLD_HEAD_MISMATCH,
    HOLD_THREAD_NOT_FOUND,
    HOLD_THREAD_NOT_OUTDATED,
    HOLD_THREAD_ALREADY_RESOLVED,
    HOLD_FLAGGED_PATTERN_NOT_FOUND_IN_THREAD,
    HOLD_FLAGGED_PATTERN_STILL_IN_DIFF,
    HOLD_REPLACEMENT_PATTERN_MISSING,
    HOLD_DIFF_FETCH_FAILED,
    HOLD_UNEXPECTED_CHANGED_FILES,
    HOLD_UNKNOWN,
)


# ---------------------------------------------------------------------------
# REST path helper
# ---------------------------------------------------------------------------

def repo_api_path(repo: str, *parts: str) -> str:
    """Build a GitHub REST API path from owner/name and trailing segments.

    >>> repo_api_path("Slideshow11/Automated-Edge-Discovery", "pulls", "364")
    'repos/Slideshow11/Automated-Edge-Discovery/pulls/364'
    >>> repo_api_path("Slideshow11/Automated-Edge-Discovery", "compare", "main...abc123")
    'repos/Slideshow11/Automated-Edge-Discovery/compare/main...abc123'
    """
    owner, name = repo.split("/", 1)
    return "/".join(["repos", owner, name, *parts])


# ---------------------------------------------------------------------------
# GitHub API helpers (list-argv, no shell=True)
# ---------------------------------------------------------------------------

def gh_api(*args: str) -> dict:
    """Run gh api with given args. Returns parsed JSON dict or raises."""
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


def fetch_pr_state_and_head(repo: str, pr_number: int) -> tuple[str, str]:
    """Return (state, head_sha) for a PR via REST."""
    data = gh_api(repo_api_path(repo, "pulls", str(pr_number)))
    return data.get("state", "UNKNOWN"), data.get("head", {}).get("sha", "")


def fetch_thread(repo: str, pr_number: int, thread_id: str) -> Optional[dict]:
    """Fetch a single review thread via GraphQL. Returns thread dict or None."""
    owner, name = repo.split("/", 1)
    q = (
        '{repository(owner:"' + owner + '",name:"' + name + '"){'
        'pullRequest(number:' + str(pr_number) + '){'
        'reviewThreads(first:100){nodes{'
        'id isResolved isOutdated '
        'comments(first:10){nodes{body author{login}}}}}}}}'
    )
    data = gh_api("graphql", "--field", f"query={q}")
    nodes = (
        data.get("data", {})
        .get("repository", {})
        .get("pullRequest", {})
        .get("reviewThreads", {})
        .get("nodes", [])
    )
    for node in nodes:
        if node["id"] == thread_id:
            return node
    return None


def _collect_patch_parts(data: dict) -> list[str]:
    """Collect all non-empty patch strings from a GitHub compare response.

    Handles two sources that may overlap on GitHub's compare API:
    - Top-level data["files"] — primary carrier of changed-file details and patches
    - Per-commit data["commits"][n]["files"] — supplementary carrier (may overlap)

    Deduplicates by patch string so identical content from both sources appears once.
    """
    seen: set[str] = set()
    parts: list[str] = []
    # Top-level files array is the primary source on GitHub's compare API.
    for file in data.get("files", []):
        patch = file.get("patch", "")
        if patch and patch not in seen:
            seen.add(patch)
            parts.append(patch)
    # Per-commit files arrays are supplementary; include any patches not already collected.
    for commit in data.get("commits", []):
        for file in commit.get("files", []):
            patch = file.get("patch", "")
            if patch and patch not in seen:
                seen.add(patch)
                parts.append(patch)
    return parts


def fetch_compare_diff(repo: str, base_ref: str, head_sha: str) -> str:
    """Fetch the patch text for all files changed between base_ref and head_sha.

    Uses GitHub compare API (base...head) so it captures the full PR diff
    regardless of local checkout state. Never uses 'git diff HEAD'.

    Collects from both top-level files array and per-commit file arrays,
    deduplicating identical patches.
    """
    data = gh_api(repo_api_path(repo, "compare", f"{base_ref}...{head_sha}"))
    return "\n".join(_collect_patch_parts(data))


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def evaluate(
    repo: str,
    pr_number: int,
    thread_id: str,
    expected_head: str,
    base_ref: str,
    flagged_pattern: str,
    *,
    replacement_pattern: Optional[str] = None,
    path_scope: Optional[str] = None,
) -> tuple[str, dict]:
    """
    Evaluate whether the thread passes all policy preconditions.
    Returns (status, audit_dict).
    """
    audit = dict(
        repo=repo,
        pr_number=pr_number,
        thread_id=thread_id,
        expected_head=expected_head,
        base_ref=base_ref,
        flagged_pattern=flagged_pattern,
        replacement_pattern=replacement_pattern,
        path_scope=path_scope,
    )

    try:
        state, head = fetch_pr_state_and_head(repo, pr_number)
        audit["pr_state"] = state
        audit["pr_head"] = head
    except Exception as e:
        audit["pr_fetch_error"] = str(e)
        return HOLD_UNKNOWN, audit

    if state != "open":
        return HOLD_PR_NOT_OPEN, audit

    if head != expected_head:
        audit["head_mismatch"] = True
        return HOLD_HEAD_MISMATCH, audit

    try:
        thread = fetch_thread(repo, pr_number, thread_id)
        audit["thread_found"] = thread is not None
    except Exception as e:
        audit["thread_fetch_error"] = str(e)
        return HOLD_UNKNOWN, audit

    if thread is None:
        return HOLD_THREAD_NOT_FOUND, audit

    # Audit keys for thread state — always set so assertions are predictable
    audit["is_outdated"] = thread.get("isOutdated", False)
    audit["is_resolved"] = thread.get("isResolved", False)
    audit["thread_found"] = True

    if not thread.get("isOutdated"):
        audit["is_outdated"] = False
        return HOLD_THREAD_NOT_OUTDATED, audit

    if thread.get("isResolved"):
        audit["is_resolved"] = True
        return HOLD_THREAD_ALREADY_RESOLVED, audit

    # Thread comment bodies must contain the flagged pattern
    comment_bodies = [c["body"] for c in thread.get("comments", {}).get("nodes", [])]
    audit["comment_bodies_found"] = bool(comment_bodies)
    if not any(flagged_pattern in body for body in comment_bodies):
        return HOLD_FLAGGED_PATTERN_NOT_FOUND_IN_THREAD, audit

    # Diff must not contain the flagged pattern (use compare API, not git diff HEAD)
    try:
        diff = fetch_compare_diff(repo, base_ref, expected_head)
        audit["diff_length"] = len(diff)
    except Exception as e:
        audit["diff_fetch_error"] = str(e)
        return HOLD_DIFF_FETCH_FAILED, audit

    if flagged_pattern in diff:
        audit["flagged_pattern_in_diff"] = True
        return HOLD_FLAGGED_PATTERN_STILL_IN_DIFF, audit
    audit["flagged_pattern_in_diff"] = False

    # Replacement must be present if specified
    if replacement_pattern is not None:
        if replacement_pattern not in diff:
            audit["replacement_in_diff"] = False
            return HOLD_REPLACEMENT_PATTERN_MISSING, audit
        audit["replacement_in_diff"] = True

    # Changed files must be within scope if path_scope is set
    if path_scope:
        filenames = set()
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                fn = line[6:].strip()
                if fn:
                    filenames.add(fn)
        audit["changed_files"] = sorted(filenames)
        if filenames and filenames != {path_scope}:
            audit["unexpected_files"] = sorted(filenames - {path_scope})
            return HOLD_UNEXPECTED_CHANGED_FILES, audit

    return ELIGIBLE_STALE_THREAD_RESOLUTION, audit


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_json(path: str, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def write_md(path: str, status: str, audit: dict) -> None:
    lines = [
        f"# Stale Thread Resolution Check — PR #{audit.get('pr_number', '?')}",
        f"",
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
        description="Dry-run stale review-thread auto-resolution eligibility checker."
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--base-ref", default="main")
    parser.add_argument("--flagged-pattern", required=True)
    parser.add_argument("--replacement-pattern", default=None)
    parser.add_argument("--path-scope", default=None)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    status, audit = evaluate(
        repo=args.repo,
        pr_number=args.pr_number,
        thread_id=args.thread_id,
        expected_head=args.expected_head,
        base_ref=args.base_ref,
        flagged_pattern=args.flagged_pattern,
        replacement_pattern=args.replacement_pattern,
        path_scope=args.path_scope,
    )

    audit["status"] = status
    audit["evaluated_at"] = datetime.now(timezone.utc).isoformat()

    write_json(args.output_json, audit)
    write_md(args.output_md, status, audit)

    print(f"Status: {status}")
    print(f"Output: {args.output_json}")

    if status == ELIGIBLE_STALE_THREAD_RESOLUTION:
        return 0
    if status in HOLD_STATUSES:
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())