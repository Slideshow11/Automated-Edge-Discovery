#!/usr/bin/env python3
"""
aed continue-pr --dry-run — read-only continuation workflow planner.

Given a PR number, computes and emits a structured continuation plan
(current state, completed phases, remaining permitted mutations, and
what ``aed continue-pr`` *would* do next) without mutating GitHub,
the worktree, or repo state.

This script is **READ-ONLY by design**. It never:

- pushes commits, branches, or tags
- posts PR comments or review comments
- resolves review threads
- approves or dismisses reviews
- modifies labels, checks, or merge state
- mutates worktrees, the local repo, or the operator's environment

A future PR (#407+) will introduce a separate ``aed continue-pr --execute``
command that consumes the JSON plan emitted here. This script intentionally
omits any ``--execute`` flag.

Dual-endpoint Codex detection
-----------------------------
Per the lesson learned in PR #405, this planner checks **both** the formal
review endpoint (``/pulls/{N}/reviews``) **and** the PR-level issue comment
endpoint (``/issues/{N}/comments``). A "no major issues" verdict posted as
a PR-level issue comment is treated as a valid clean signal.

Usage
-----
::

    python3 scripts/local/aed_continue_pr.py \\
        --pr-number 407 \\
        --dry-run \\
        --output-json /tmp/aed_runs/plan_407.json \\
        --output-md /tmp/aed_runs/plan_407.md

Exit codes
----------
- 0  : plan emitted (any ``status`` value)
- 1  : mandatory arguments missing
- 2  : forbidden flag present (--execute / --force / --admin / --no-dry-run)
- 3  : GitHub API error
- 4  : gate subprocess error
- 5  : output write error
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


SCHEMA_VERSION = 1
PLAN_KIND = "aed.continue_pr.dry_run"
DEFAULT_REPO = "Slideshow11/Automated-Edge-Discovery"
CODEX_LOGIN = "chatgpt-codex-connector[bot]"
CLEAN_PATTERN = re.compile(
    r"^Codex Review:.*(?:[Nn]o major issues|✅|Swish|👍|[Dd]id(?:n't| not)? find any major)"
)
CLEAN_FALLBACK_PATTERN = re.compile(
    r"(?:[Nn]o major issues|✅|Swish|👍|looks good|[Dd]id(?:n't| not)? find any major)",
    re.IGNORECASE,
)
BLOCKED_PATTERN = re.compile(
    r"(?:[Cc]hanges [Rr]equested|CHANGES_REQUESTED|\bblocking issue\b|\bblocking merge\b|\bcritical issue\b|\bmerge blocked\b|\baction required\b)",
    re.IGNORECASE,
)
DEFAULT_MAX_POLL_SECONDS = 30
MAX_POLL_SECONDS_CAP = 300


# ---------------------------------------------------------------------------
# Custom argument type for rejecting forbidden flags
# ---------------------------------------------------------------------------


def _reject_forbidden(value: str, forbidden: Sequence[str]) -> str:
    """Reject CLI values that contain forbidden tokens (substring match)."""
    for token in forbidden:
        if token in value:
            raise argparse.ArgumentTypeError(
                f"forbidden flag detected: {token!r} is not allowed in this PR"
            )
    return value


# ---------------------------------------------------------------------------
# Dataclass for the structured plan
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ContinuePlan:
    """Structured continuation plan emitted by ``aed continue-pr --dry-run``."""

    schema_version: int
    plan_kind: str
    generated_at: str
    dry_run: bool
    pr: Dict[str, Any]
    lifecycle: Dict[str, Any]
    checks: Dict[str, Any]
    gate: Dict[str, Any]
    codex: Dict[str, Any]
    branch_protection: Dict[str, Any]
    proposed_actions: List[Dict[str, Any]]
    blockers_for_merge: List[Dict[str, Any]]
    mutations_proposed: int
    warnings: List[str]
    recommendation: str

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Subprocess helpers (no shell, allow mocking in tests)
# ---------------------------------------------------------------------------


def _run_subprocess(
    argv: Sequence[str],
    cwd: Optional[Path] = None,
    timeout: int = 60,
) -> Tuple[int, str, str]:
    """Run ``argv`` without shell. Returns ``(rc, stdout, stderr)``."""
    try:
        result = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            shell=False,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"command timed out after {timeout}s: {' '.join(argv)}"
        ) from exc
    return result.returncode, result.stdout, result.stderr


def _run_gh_api(
    repo: str,
    endpoint: str,
    *,
    timeout: int = 60,
) -> Any:
    """Run ``gh api`` against ``/repos/{repo}{endpoint}`` (GET only)."""
    if not endpoint.startswith("/"):
        raise ValueError(f"endpoint must start with '/': {endpoint!r}")
    # Sanity: must be GET (no POST/PUT/PATCH/DELETE)
    argv = [
        "gh",
        "api",
        "-H",
        "Accept: application/vnd.github+json",
        f"/repos/{repo}{endpoint}",
    ]
    rc, stdout, stderr = _run_subprocess(argv, timeout=timeout)
    if rc != 0:
        raise RuntimeError(
            f"gh api GET /repos/{repo}{endpoint} failed (rc={rc}): {stderr.strip()[:500]}"
        )
    if not stdout.strip():
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"gh api GET /repos/{repo}{endpoint} returned non-JSON: {exc}: {stdout[:500]}"
        ) from exc


def _run_gh_api_paginated(
    repo: str,
    endpoint: str,
    *args: Any,
    **kwargs: Any,
) -> List[Any]:
    """Run ``gh api --paginate`` against ``/repos/{repo}{endpoint}``.

    PR #406 Codex finding PRRT_kwDOSHFpYM6LRny5
    -------------------------------------------
    GitHub's list endpoints return at most 100 records per page. To
    see all pages we must invoke ``gh api`` with ``--paginate`` and
    ``--slurp`` so the per-page JSON arrays are flattened into one
    combined array (the default ``--paginate`` would just dump
    newline-separated arrays, which is not parseable as a single
    JSON document).

    Returns a flat list of records across all pages. Any error from
    the underlying ``gh api`` invocation propagates as a
    ``RuntimeError`` so the caller can fail closed.
    """
    if not endpoint.startswith("/"):
        raise ValueError(f"endpoint must start with '/': {endpoint!r}")
    # ``--slurp`` tells gh api to collect each page's JSON output
    # into a single JSON array, which is then parseable as one
    # document. ``--paginate`` walks the Link header. The result is
    # a JSON array of JSON arrays; we flatten it.
    argv = [
        "gh",
        "api",
        "-H",
        "Accept: application/vnd.github+json",
        "--paginate",
        "--slurp",
        f"/repos/{repo}{endpoint}",
    ]
    timeout = kwargs.get("timeout", 60)
    rc, stdout, stderr = _run_subprocess(argv, timeout=timeout)
    if rc != 0:
        raise RuntimeError(
            f"gh api --paginate --slurp /repos/{repo}{endpoint} "
            f"failed (rc={rc}): {stderr.strip()[:500]}"
        )
    if not stdout.strip():
        return []
    try:
        pages = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"gh api --paginate --slurp /repos/{repo}{endpoint} "
            f"returned non-JSON: {exc}: {stdout[:500]}"
        ) from exc
    # ``--slurp`` wraps every page's output in a top-level array.
    # Each element is either an object (if the endpoint returns a
    # single object per page, unusual for our list endpoints) or an
    # array of objects (the common case for /reviews and
    # /comments). Flatten into one list of records.
    flat: List[Any] = []
    if isinstance(pages, list):
        for page in pages:
            if isinstance(page, list):
                flat.extend(page)
            elif page is not None:
                flat.append(page)
    return flat


def _normalize_merge_state(raw: Any) -> Optional[str]:
    """Normalize a merge-state value to an uppercase lifecycle string.

    Returns one of: ``CLEAN``, ``BLOCKED``, ``DIRTY``, ``BEHIND``,
    ``UNSTABLE``, ``DRAFT``, ``UNKNOWN``.  ``None`` or empty returns
    ``None`` (no signal — caller should treat as not-clean).

    PR #406 Codex finding PRRT_kwDOSHFpYM6LRnyv
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s_upper = s.upper()
    if s_upper in {"CLEAN"}:
        return "CLEAN"
    if s_upper in {"BLOCKED", "DIRTY", "BEHIND", "UNSTABLE", "DRAFT"}:
        return s_upper
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Fetchers (one per external signal)
# ---------------------------------------------------------------------------


def fetch_pr_state(repo: str, pr_number: int) -> Dict[str, Any]:
    """Fetch the current PR state via ``gh api /pulls/{N}``.

    Normalizes the GitHub REST ``state`` field to uppercase (GitHub
    returns ``open``/``closed`` lowercase; downstream readiness logic
    compares against ``OPEN``/``CLOSED`` uppercase). The original
    case is preserved as ``state_raw`` for diagnostic purposes.
    """
    data = _run_gh_api(repo, f"/pulls/{pr_number}")
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected PR API response shape: {type(data).__name__}")
    head = data.get("head", {})
    base = data.get("base", {})
    user = data.get("user") or {}
    state_raw = data.get("state")
    # Normalize state to uppercase so downstream code can compare
    # against OPEN/CLOSED/MERGED consistently. Unknown values
    # are preserved as-is (uppercased) for downstream handling.
    state_normalized = state_raw.upper() if isinstance(state_raw, str) else state_raw
    # Resolve the merge-state field. GitHub's REST endpoint is
    # ``mergeable_state`` (not ``merge_state_status``). We read
    # whichever of the known aliases is present, normalize to
    # uppercase, and preserve the raw value + source field for
    # diagnostics. PR #406 Codex finding PRRT_kwDOSHFpYM6LRnyv.
    raw_merge_value: Any = None
    raw_merge_field: Optional[str] = None
    for _ms_field in (
        "mergeable_state",        # REST /pulls/{N}
        "mergeStateStatus",       # GraphQL PullRequest
        "merge_state_status",     # legacy / internal
    ):
        if _ms_field in data and data.get(_ms_field) is not None:
            raw_merge_value = data.get(_ms_field)
            raw_merge_field = _ms_field
            break
    if raw_merge_value is None and data.get("mergeable") is False:
        raw_merge_value = "BLOCKED"
        raw_merge_field = "mergeable_false_inferred"
    merge_state_normalized = _normalize_merge_state(raw_merge_value)

    return {
        "number": data.get("number"),
        "url": data.get("html_url"),
        "title": data.get("title"),
        "head_sha": head.get("sha"),
        "head_ref": head.get("ref"),
        "base_ref": base.get("ref"),
        "base_sha": base.get("sha"),
        "state": state_normalized,
        "state_raw": state_raw,
        "is_draft": bool(data.get("draft")),
        "is_mergeable": data.get("mergeable"),
        "merge_state_status": merge_state_normalized,
        "merge_state_raw": raw_merge_value,
        "merge_state_field": raw_merge_field,
        "author_login": user.get("login"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def fetch_branch_protection(repo: str, base_branch: str) -> Dict[str, Any]:
    """Fetch branch protection rules for ``base_branch``."""
    try:
        data = _run_gh_api(repo, f"/branches/{base_branch}/protection")
    except RuntimeError as exc:
        # If the branch is not protected, the API may 404.
        return {"base_branch": base_branch, "is_protected": False, "error": str(exc)}
    if not isinstance(data, dict):
        return {"base_branch": base_branch, "is_protected": False}
    status_checks = data.get("required_status_checks") or {}
    reviews = data.get("required_pull_request_reviews") or {}
    return {
        "base_branch": base_branch,
        "is_protected": True,
        "required_status_checks": status_checks.get("contexts") or [],
        "strict_status_checks": bool(status_checks.get("strict")),
        "required_conversation_resolution": bool(
            data.get("required_conversation_resolution", {}).get("enabled")
        ),
        "required_linear_history": bool(
            data.get("required_linear_history", {}).get("enabled")
        ),
        "required_approving_review_count": int(
            reviews.get("required_approving_review_count") or 0
        ),
        "enforce_admins": bool(data.get("enforce_admins", {}).get("enabled")),
        "allow_force_pushes": bool(data.get("allow_force_pushes", {}).get("enabled")),
        "violations": [],
    }


def fetch_required_checks(
    repo: str,
    pr_number: int,
    head_sha: str,
    *,
    required_check_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Fetch the status check rollup for the PR's head SHA.

    Reconciliation with branch-protection required contexts
    -----------------------------------------------------
    When ``required_check_names`` is provided (the branch protection's
    list of required status-check contexts), this function reconciles
    the present check-runs against that list. Per PR #406 Codex
    finding 3449089428:

    - If any required context is **missing** from the check-runs
      response, ``all_required_green`` is ``False`` even if other
      contexts are successful.
    - If any required context is present but not ``success`` /
      ``neutral`` / ``skipped``, ``all_required_green`` is ``False``.
    - If ``required_check_names`` is empty or ``None``, the old
      behavior is preserved (all present checks must be successful).
    """
    if not head_sha:
        return {
            "all_required_green": False,
            "per_check_status": {},
            "error": "no head_sha",
            "missing_required": list(required_check_names or []),
        }
    try:
        data = _run_gh_api(repo, f"/commits/{head_sha}/check-runs?per_page=100")
    except RuntimeError as exc:
        return {
            "all_required_green": False,
            "per_check_status": {},
            "error": str(exc),
            "missing_required": list(required_check_names or []),
        }
    if not isinstance(data, dict):
        return {
            "all_required_green": False,
            "per_check_status": {},
            "missing_required": list(required_check_names or []),
        }
    runs = data.get("check_runs") or []
    per_check: Dict[str, str] = {}
    for run in runs:
        name = run.get("name")
        conclusion = run.get("conclusion") or run.get("status") or "unknown"
        if name:
            per_check[name] = str(conclusion).lower()

    # Reconcile against required contexts (PR #406 Codex finding 3449089428).
    # Default to required_check_names if provided; otherwise, no reconciliation
    # (legacy behavior: only check present runs).
    required = list(required_check_names or [])
    if required:
        # Determine missing required contexts (not present in check-runs at all).
        missing_required = [
            name for name in required if name not in per_check
        ]
        # Determine failing required contexts (present but not successful).
        failing_required = [
            name
            for name in required
            if name in per_check
            and per_check[name] not in {"success", "neutral", "skipped"}
        ]
        # Present required contexts that passed.
        passing_required = [
            name
            for name in required
            if name in per_check
            and per_check[name] in {"success", "neutral", "skipped"}
        ]
        all_required_green = (
            len(missing_required) == 0 and len(failing_required) == 0
        )
        return {
            "all_required_green": all_required_green,
            "per_check_status": per_check,
            "required_check_names": required,
            "missing_required": missing_required,
            "failing_required": failing_required,
            "passing_required": passing_required,
        }
    # Legacy: when no required_check_names is provided, only verify present runs.
    return {
        "all_required_green": all(
            v in {"success", "neutral", "skipped"} for v in per_check.values()
        )
        if per_check
        else False,  # Changed: empty check-runs is NOT all green by default.
        "per_check_status": per_check,
        "missing_required": [],
    }


def fetch_codex_verdict(
    repo: str,
    pr_number: int,
    *,
    since_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Dual-endpoint Codex verdict fetcher (PR #405 lesson).

    Checks BOTH endpoints:

    1. ``/pulls/{N}/reviews`` — formal reviews submitted by Codex
    2. ``/issues/{N}/comments`` — PR-level issue comments by Codex

    Returns a dict describing what each endpoint returned and the
    resolved verdict (with source attribution).

    Cutoff filtering
    ----------------
    When ``since_iso`` is provided (as an ISO 8601 timestamp string),
    BOTH endpoints' records are filtered to only those whose
    timestamp (``submitted_at`` for formal reviews; ``created_at``
    for issue comments) is at-or-after ``since_iso``. Records older
    than the cutoff are excluded from the verdict computation, the
    per-endpoint ``found_fresh_review`` / ``found_clean_comment``
    counts, and the latest-activity timestamp.
    """
    endpoint_formal = f"/pulls/{pr_number}/reviews?per_page=100"
    endpoint_issue = f"/issues/{pr_number}/comments?per_page=100"

    formal_endpoint_checked = True
    issue_endpoint_checked = True
    formal_reviews: List[Dict[str, Any]] = []
    issue_comments: List[Dict[str, Any]] = []
    formal_error: Optional[str] = None
    issue_error: Optional[str] = None

    # --- Formal reviews endpoint (paginated) ---
    # PR #406 Codex finding PRRT_kwDOSHFpYM6LRny5: use --paginate so
    # we see all pages, not just the first 100.
    try:
        data = _run_gh_api_paginated(repo, endpoint_formal)
        if isinstance(data, list):
            formal_reviews = [
                r
                for r in data
                if isinstance(r, dict)
                and (r.get("user") or {}).get("login") == CODEX_LOGIN
            ]
        else:
            formal_reviews = []
    except RuntimeError as exc:
        # PR #406 Codex finding PRRT_kwDOSHFpYM6LRnyz: preserve the
        # error (do NOT silently convert to empty list) so the
        # verdict can fail closed.
        formal_endpoint_checked = False
        formal_error = str(exc)
        formal_reviews = []

    # --- Issue-level comments endpoint (paginated) ---
    try:
        data = _run_gh_api_paginated(repo, endpoint_issue)
        if isinstance(data, list):
            issue_comments = [
                c
                for c in data
                if isinstance(c, dict)
                and (c.get("user") or {}).get("login") == CODEX_LOGIN
            ]
        else:
            issue_comments = []
    except RuntimeError as exc:
        issue_endpoint_checked = False
        issue_error = str(exc)
        issue_comments = []

    # --- Apply cutoff filter to both sources (PR #406 Codex finding 3449089427) ---
    # Records older than the cutoff are excluded. The cutoff is the
    # ISO 8601 timestamp the operator passed via --last-known-codex-ts.
    fresh_formal_reviews, stale_formal_count = _filter_by_cutoff(
        formal_reviews, since_iso, "submitted_at"
    )
    fresh_issue_comments, stale_issue_count = _filter_by_cutoff(
        issue_comments, since_iso, "created_at"
    )

    # --- Resolve verdict using ONLY fresh (post-cutoff) records ---
    verdict, source = _resolve_codex_verdict(fresh_formal_reviews, fresh_issue_comments)

    # --- Fail-closed on endpoint errors (PR #406 Codex finding PRRT_kwDOSHFpYM6LRnyz) ---
    # If EITHER endpoint failed, the verdict MUST be ``pending`` /
    # ``inconclusive`` (NOT ``clean``) regardless of what the
    # successful endpoint returned. A clean signal from one side
    # must never override a hard error on the other side.
    endpoint_errors: List[str] = []
    if not formal_endpoint_checked:
        endpoint_errors.append(f"formal_review_endpoint: {formal_error}")
    if not issue_endpoint_checked:
        endpoint_errors.append(f"issue_comment_endpoint: {issue_error}")
    if endpoint_errors:
        verdict = "pending"
        source = "endpoint_error_fail_closed"

    # If a blocked signal is found in fresh data, prefer it; otherwise
    # if one endpoint has stale-clean and the other has no fresh signal,
    # we report a conservative "no fresh Codex confirmation" status
    # rather than promoting stale-clean to fresh-clean.
    if (
        not endpoint_errors
        and not fresh_formal_reviews
        and not fresh_issue_comments
        and (formal_reviews or issue_comments)
    ):
        # All Codex activity is older than the cutoff — be conservative
        verdict = "pending"
        source = "all_codex_activity_stale"
    last_receipt_at = _latest_codex_activity_at(fresh_formal_reviews, fresh_issue_comments)
    last_review_id = (
        sorted(fresh_formal_reviews, key=lambda r: r.get("submitted_at") or "")[-1].get("id")
        if fresh_formal_reviews
        else None
    )
    last_comment_id = (
        sorted(fresh_issue_comments, key=lambda c: c.get("created_at") or "")[-1].get("id")
        if fresh_issue_comments
        else None
    )
    return {
        "verdict": verdict,
        "source": source,
        "last_receipt_at": last_receipt_at,
        "last_review_id": last_review_id,
        "last_comment_id": last_comment_id,
        "would_ping_codex": verdict in {"pending", "blocked"},
        "duplicate_ping_detected": False,
        "cutoff_applied": since_iso,
        "fresh_formal_count": len(fresh_formal_reviews),
        "fresh_issue_count": len(fresh_issue_comments),
        "stale_formal_count": stale_formal_count,
        "stale_issue_count": stale_issue_count,
        "endpoint_errors": endpoint_errors,
        "dual_endpoint_check": {
            "formal_review_endpoint": {
                "checked": formal_endpoint_checked,
                "found_fresh_review": len(formal_reviews) > 0,
                "error": formal_error,
            },
            "issue_comment_endpoint": {
                "checked": issue_endpoint_checked,
                "found_clean_comment": any(
                    _is_clean_signal(c.get("body") or "") for c in issue_comments
                ),
                "error": issue_error,
            },
        },
    }


def _is_clean_signal(body: str) -> bool:
    """Return True if the comment body matches a Codex clean-signal pattern."""
    return bool(CLEAN_PATTERN.search(body) or CLEAN_FALLBACK_PATTERN.search(body))


def _is_blocked_signal(body: str) -> bool:
    """Return True if the comment body matches a Codex blocked-signal pattern."""
    return bool(BLOCKED_PATTERN.search(body))


def _filter_by_cutoff(
    records: List[Dict[str, Any]],
    since_iso: Optional[str],
    timestamp_field: str,
) -> Tuple[List[Dict[str, Any]], int]:
    """Filter ``records`` to those whose ``timestamp_field`` is at-or-after ``since_iso``.

    Returns ``(filtered_records, stale_count)``.

    If ``since_iso`` is ``None`` or empty, all records are returned
    (no cutoff). Records with a missing or unparseable timestamp are
    excluded (treated as stale) when a cutoff is provided — this is the
    conservative behavior per PR #406 Codex finding 3449089427.
    """
    if not since_iso or not records:
        return list(records), 0
    try:
        cutoff_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        # Invalid cutoff — be conservative: treat all records as stale.
        return [], len(records)
    filtered: List[Dict[str, Any]] = []
    stale_count = 0
    for r in records:
        ts = r.get(timestamp_field)
        if not ts:
            stale_count += 1
            continue
        try:
            rec_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if rec_dt >= cutoff_dt:
                filtered.append(r)
            else:
                stale_count += 1
        except (ValueError, AttributeError):
            stale_count += 1
    return filtered, stale_count


def _resolve_codex_verdict(
    formal_reviews: List[Dict[str, Any]],
    issue_comments: List[Dict[str, Any]],
) -> Tuple[str, str]:
    """Resolve a single Codex verdict from both endpoints.

    Rules (in priority order):
      1. If any formal review has state CHANGES_REQUESTED -> "blocked"
      2. If any issue comment matches BLOCKED_PATTERN -> "blocked"
      3. If any formal review has state COMMENTED with clean body -> "clean" from review
      4. If any issue comment matches CLEAN_PATTERN -> "clean" from issue comment
         (this is the PR #405 lesson: PR-level issue comments count!)
      5. If both endpoints have activity but signals conflict -> "conflicting"
      6. If neither endpoint has any Codex activity -> "pending"
    """
    has_clean_formal = any(
        r.get("state") in {"COMMENTED", "APPROVED"}
        and _is_clean_signal(r.get("body") or "")
        for r in formal_reviews
    )
    has_blocked_formal = any(
        r.get("state") in {"CHANGES_REQUESTED", "REQUEST_CHANGES"}
        for r in formal_reviews
    )
    has_clean_issue = any(_is_clean_signal(c.get("body") or "") for c in issue_comments)
    has_blocked_issue = any(
        _is_blocked_signal(c.get("body") or "") for c in issue_comments
    )

    if has_blocked_formal:
        return "blocked", "review_CHANGES_REQUESTED"
    if has_blocked_issue:
        return "blocked", "issue_comment_BLOCKED_pattern"
    if has_clean_formal and has_clean_issue:
        return "clean", "review_and_issue_comment_both_clean"
    if has_clean_formal:
        return "clean", "review_clean_signal"
    if has_clean_issue:
        return "clean", "issue_comment_clean_signal"
    if formal_reviews or issue_comments:
        return "conflicting", "endpoints_disagree"
    return "pending", "no_codex_activity"


def _latest_codex_activity_at(
    formal_reviews: List[Dict[str, Any]],
    issue_comments: List[Dict[str, Any]],
) -> Optional[str]:
    timestamps: List[str] = []
    for r in formal_reviews:
        ts = r.get("submitted_at")
        if ts:
            timestamps.append(ts)
    for c in issue_comments:
        ts = c.get("created_at")
        if ts:
            timestamps.append(ts)
    if not timestamps:
        return None
    return max(timestamps)

def run_review_gate(
    repo: str,
    pr_number: int,
    head_sha: str,
    *args: Any,
    repo_root: Optional[Path] = None,
    gate_script: str = "scripts/local/check_pr_review_comments.py",
    max_poll_seconds: int = DEFAULT_MAX_POLL_SECONDS,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run the B2 review-comment gate via subprocess and parse its JSON output.

    NEVER imports or modifies ``check_pr_review_comments.py``.

    PR #406 Codex finding PRRT_kwDOSHFpYM6LRny_
    --------------------------------------------
    The subprocess timeout MUST use the parsed/clamped
    ``max_poll_seconds`` value (defaulting to ``DEFAULT_MAX_POLL_SECONDS``),
    NOT a hard-coded ``120``. This preserves the bounded no-stall
    contract: passing ``--max-poll-seconds 1`` actually passes
    ``timeout=1`` to the gate subprocess.
    """
    if not head_sha:
        return {"status": "REVIEW_COMMENTS_INCONCLUSIVE", "error": "no head_sha"}
    # Clamp the gate timeout to safe lower/upper bounds, matching
    # the CLI parser's clamping in ``parse_args``. This keeps the
    # documented contract intact: the subprocess timeout equals the
    # operator-supplied (or default) max-poll-seconds value.
    gate_timeout = int(max_poll_seconds)
    if gate_timeout < 1:
        gate_timeout = 1
    if gate_timeout > MAX_POLL_SECONDS_CAP:
        gate_timeout = MAX_POLL_SECONDS_CAP
    # Use a temp file for gate output
    tmp_json = Path("/tmp") / f"aed_gate_{pr_number}_{head_sha[:12]}.json"
    tmp_md = Path("/tmp") / f"aed_gate_{pr_number}_{head_sha[:12]}.md"
    python_exe = sys.executable or "python3"
    cmd = [
        python_exe,
        gate_script,
        "--pr-number",
        str(pr_number),
        "--repo",
        repo,
        "--reported-head-sha",
        head_sha,
        "--output-json",
        str(tmp_json),
        "--output-md",
        str(tmp_md),
    ]
    cwd = str(repo_root) if repo_root else None
    try:
        # PR #406 Codex finding PRRT_kwDOSHFpYM6LRny_: use the
        # operator-supplied (or default) max-poll-seconds as the
        # subprocess timeout, NOT a hard-coded 120.
        rc, stdout, stderr = _run_subprocess(
            cmd, cwd=Path(cwd) if cwd else None, timeout=gate_timeout
        )
    except RuntimeError as exc:
        return {
            "status": "REVIEW_COMMENTS_INCONCLUSIVE",
            "error": str(exc),
            "gate_timeout_used": gate_timeout,
        }
    if rc != 0:
        return {
            "status": "REVIEW_COMMENTS_INCONCLUSIVE",
            "error": f"gate subprocess failed (rc={rc}): {stderr.strip()[:500]}",
        }
    if not tmp_json.exists():
        return {"status": "REVIEW_COMMENTS_INCONCLUSIVE", "error": "gate output JSON missing"}
    try:
        gate_data = json.loads(tmp_json.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return {"status": "REVIEW_COMMENTS_INCONCLUSIVE", "error": f"gate JSON unreadable: {exc}"}
    return {
        "status": gate_data.get("status"),
        "head_sha_mismatch": gate_data.get("head_sha_mismatch"),
        "reported_head_sha": gate_data.get("reported_head_sha"),
        "live_head_sha": gate_data.get("live_head_sha"),
        "blockers": len(gate_data.get("blockers") or []),
        "stale_blockers": len(gate_data.get("stale_blockers") or []),
        "p0_count": (gate_data.get("summary_counts") or {}).get("P0", 0),
        "p1_count": (gate_data.get("summary_counts") or {}).get("P1", 0),
        "p2_count": (gate_data.get("summary_counts") or {}).get("P2", 0),
        "p3_count": (gate_data.get("summary_counts") or {}).get("P3", 0),
        "current_unresolved_threads": (
            gate_data.get("current_unresolved_threads", 0)
        ),
        # PR #406 Codex finding PRRT_kwDOSHFpYM6LRny_: surface the
        # actual subprocess timeout used so the operator can verify
        # the bounded no-stall contract.
        "gate_timeout_used": gate_timeout,
        "raw_path": str(tmp_json),
    }


# ---------------------------------------------------------------------------
# Plan assembly
# ---------------------------------------------------------------------------


def assemble_plan(
    *,
    pr: Dict[str, Any],
    lifecycle: Dict[str, Any],
    checks: Dict[str, Any],
    gate: Dict[str, Any],
    codex: Dict[str, Any],
    branch_protection: Dict[str, Any],
    generated_at: str,
) -> ContinuePlan:
    """Assemble the structured continuation plan from all signals."""
    blockers_for_merge: List[Dict[str, Any]] = []
    warnings: List[str] = []

    if pr.get("is_draft"):
        warnings.append("PR is a draft; no merge proposed")
    if pr.get("state") != "OPEN":
        warnings.append(f"PR state is {pr.get('state')!r}; no merge proposed")
    if pr.get("is_mergeable") is False:
        blockers_for_merge.append(
            {
                "kind": "MERGE_CONFLICT",
                "detail": "GitHub reports mergeable=False (likely conflict or blocked)",
            }
        )

    # Merge-state gate (PR #406 Codex finding PRRT_kwDOSHFpYM6LRnyv).
    # Per the repo cookbook, we hold unless ``mergeable_state`` /
    # ``mergeStateStatus`` is ``CLEAN``. Any non-clean value
    # (BLOCKED, DIRTY, BEHIND, UNSTABLE, DRAFT, UNKNOWN, or
    # missing) blocks the merge command from being emitted.
    merge_state = pr.get("merge_state_status")
    if merge_state is None or str(merge_state).upper() != "CLEAN":
        blockers_for_merge.append(
            {
                "kind": "MERGE_STATE_NOT_CLEAN",
                "detail": (
                    f"merge_state_status={merge_state!r} "
                    f"(raw={pr.get('merge_state_raw')!r}, "
                    f"field={pr.get('merge_state_field')!r}); "
                    f"cookbook requires CLEAN before authorizing merge"
                ),
            }
        )
    gate_status = gate.get("status") or "UNKNOWN"
    if gate_status == "REVIEW_COMMENTS_BLOCKED":
        blockers_for_merge.append(
            {
                "kind": "GATE_BLOCKED",
                "detail": f"review-comment gate reports blockers={gate.get('blockers')}",
            }
        )
    elif gate_status == "REVIEW_COMMENTS_INCONCLUSIVE":
        blockers_for_merge.append(
            {
                "kind": "GATE_INCONCLUSIVE",
                "detail": "review-comment gate is inconclusive; cannot safely merge",
            }
        )
    if not checks.get("all_required_green"):
        blockers_for_merge.append(
            {
                "kind": "REQUIRED_CHECKS_NOT_GREEN",
                "detail": f"required checks: {checks.get('per_check_status')}",
            }
        )
    if codex.get("verdict") == "blocked":
        blockers_for_merge.append(
            {
                "kind": "CODEX_BLOCKED",
                "detail": f"Codex verdict: blocked (source: {codex.get('source')})",
            }
        )
    if codex.get("verdict") == "conflicting":
        warnings.append(
            "Codex signals conflict between formal review and issue comment endpoints"
        )
    # Surface Codex endpoint errors as a warning and an explicit
    # blocker when the verdict was demoted to pending because of an
    # endpoint failure. PR #406 Codex finding PRRT_kwDOSHFpYM6LRnyz.
    codex_endpoint_errors = codex.get("endpoint_errors") or []
    if codex_endpoint_errors:
        for err in codex_endpoint_errors:
            warnings.append(f"Codex endpoint error: {err}")
        if codex.get("source") == "endpoint_error_fail_closed":
            blockers_for_merge.append(
                {
                    "kind": "CODEX_ENDPOINT_ERROR",
                    "detail": (
                        f"one or more Codex endpoints failed; "
                        f"verdict demoted to pending: {codex_endpoint_errors}"
                    ),
                }
            )
    if branch_protection.get("is_protected") and branch_protection.get(
        "required_conversation_resolution"
    ):
        if gate.get("current_unresolved_threads", 0) > 0:
            blockers_for_merge.append(
                {
                    "kind": "UNRESOLVED_THREADS",
                    "detail": (
                        f"branch protection requires conversation resolution but "
                        f"current_unresolved_threads={gate.get('current_unresolved_threads')}"
                    ),
                }
            )

    # Determine recommendation
    if pr.get("is_draft"):
        recommendation = "NOT_READY_PR_IS_DRAFT"
    elif pr.get("state") != "OPEN":
        recommendation = f"NOT_READY_PR_STATE_{pr.get('state')}"
    elif blockers_for_merge:
        recommendation = "NOT_READY_BLOCKERS_PRESENT"
    elif codex.get("verdict") == "pending":
        recommendation = "WAITING_FOR_CODEX_VERDICT"
    elif codex.get("verdict") == "clean":
        recommendation = "READY_TO_AUTHORIZE_HUMAN_MERGE"
    else:
        recommendation = "NOT_READY_UNKNOWN"

    # Build proposed actions (previews only, never executed)
    proposed_actions: List[Dict[str, Any]] = []
    if recommendation == "READY_TO_AUTHORIZE_HUMAN_MERGE":
        proposed_actions.append(
            {
                "order": 1,
                "action_kind": "merge",
                "rationale": (
                    "PR is mergeable with mergeStateStatus=CLEAN; gate is "
                    "REVIEW_COMMENTS_CLEAN with 0 blockers; Codex verdict "
                    "is clean from "
                    f"{codex.get('source')}; all required checks green."
                ),
                "command_preview": (
                    f"gh pr merge {pr.get('number')} "
                    f"--repo {pr.get('_repo', DEFAULT_REPO)} "
                    f"--squash --delete-branch "
                    f"--match-head-commit {pr.get('head_sha')}"
                ),
                "mutates_github": True,
                "requires_human_authorization": True,
            }
        )
    elif recommendation == "WAITING_FOR_CODEX_VERDICT":
        proposed_actions.append(
            {
                "order": 1,
                "action_kind": "wait_for_codex",
                "rationale": (
                    "No fresh Codex activity on either endpoint. Wait for "
                    "Codex to respond before proposing merge."
                ),
                "command_preview": "(no command; wait for Codex)",
                "mutates_github": False,
                "requires_human_authorization": False,
            }
        )

    mutations_proposed = sum(
        1 for a in proposed_actions if a.get("mutates_github")
    )

    return ContinuePlan(
        schema_version=SCHEMA_VERSION,
        plan_kind=PLAN_KIND,
        generated_at=generated_at,
        dry_run=True,
        pr=pr,
        lifecycle=lifecycle,
        checks=checks,
        gate=gate,
        codex=codex,
        branch_protection=branch_protection,
        proposed_actions=proposed_actions,
        blockers_for_merge=blockers_for_merge,
        mutations_proposed=mutations_proposed,
        warnings=warnings,
        recommendation=recommendation,
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_markdown(plan: ContinuePlan) -> str:
    """Render the plan as a human-readable markdown memo."""
    lines: List[str] = []
    lines.append(f"# aed continue-pr --dry-run plan for PR #{plan.pr.get('number')}")
    lines.append("")
    lines.append(f"**Plan kind:** `{plan.plan_kind}`")
    lines.append(f"**Schema version:** {plan.schema_version}")
    lines.append(f"**Generated at:** {plan.generated_at}")
    lines.append(f"**Dry-run:** {plan.dry_run}")
    lines.append(f"**Recommendation:** `{plan.recommendation}`")
    lines.append("")

    lines.append("## PR status")
    lines.append("")
    lines.append(f"- **Number:** {plan.pr.get('number')}")
    lines.append(f"- **Title:** {plan.pr.get('title')}")
    lines.append(f"- **URL:** {plan.pr.get('url')}")
    lines.append(f"- **State:** {plan.pr.get('state')}")
    lines.append(f"- **Draft:** {plan.pr.get('is_draft')}")
    lines.append(f"- **Head SHA:** {plan.pr.get('head_sha')}")
    lines.append(f"- **Head ref:** {plan.pr.get('head_ref')}")
    lines.append(f"- **Base ref:** {plan.pr.get('base_ref')}")
    lines.append(f"- **Base SHA:** {plan.pr.get('base_sha')}")
    lines.append(f"- **mergeable:** {plan.pr.get('is_mergeable')}")
    lines.append(f"- **merge_state_status:** {plan.pr.get('merge_state_status')}")
    lines.append("")

    lines.append("## Lifecycle state")
    lines.append("")
    lines.append(f"- **Current state:** `{plan.lifecycle.get('current_state')}`")
    lines.append(f"- **Source:** {plan.lifecycle.get('source')}")
    lines.append(f"- **Completed phases:** {', '.join(plan.lifecycle.get('completed_phases') or []) or '(none detected)'}")
    lines.append(
        f"- **Remaining permitted mutations:** "
        f"{', '.join(plan.lifecycle.get('remaining_permitted_mutations') or []) or '(none detected)'}"
    )
    lines.append(
        f"- **Already performed mutations:** "
        f"{', '.join(plan.lifecycle.get('already_performed_mutations') or []) or '(none detected)'}"
    )
    lines.append(
        f"- **Blocked mutations:** "
        f"{', '.join(plan.lifecycle.get('blocked_mutations') or []) or '(none)'}"
    )
    lines.append("")

    lines.append("## Checks")
    lines.append("")
    lines.append(f"- **All required green:** {plan.checks.get('all_required_green')}")
    if plan.checks.get("per_check_status"):
        lines.append("- **Per-check status:**")
        for name, status in sorted(plan.checks["per_check_status"].items()):
            lines.append(f"  - `{name}`: {status}")
    if plan.checks.get("error"):
        lines.append(f"- **Error:** {plan.checks['error']}")
    lines.append("")

    lines.append("## Review-comment gate (B2 source-aware)")
    lines.append("")
    lines.append(f"- **Status:** `{plan.gate.get('status')}`")
    lines.append(f"- **head_sha_mismatch:** {plan.gate.get('head_sha_mismatch')}")
    lines.append(f"- **blockers:** {plan.gate.get('blockers')}")
    lines.append(f"- **stale_blockers:** {plan.gate.get('stale_blockers')}")
    lines.append(
        f"- **severity breakdown:** "
        f"P0={plan.gate.get('p0_count')} P1={plan.gate.get('p1_count')} "
        f"P2={plan.gate.get('p2_count')} P3={plan.gate.get('p3_count')}"
    )
    lines.append(
        f"- **current_unresolved_threads:** {plan.gate.get('current_unresolved_threads')}"
    )
    # PR #406 Codex finding PRRT_kwDOSHFpYM6LRny_: surface the
    # subprocess timeout used for the gate so the operator can
    # verify the bounded no-stall contract.
    if plan.gate.get("gate_timeout_used") is not None:
        lines.append(
            f"- **gate_timeout_used (s):** {plan.gate.get('gate_timeout_used')}"
        )
    if plan.gate.get("error"):
        lines.append(f"- **Error:** {plan.gate['error']}")
    lines.append("")

    lines.append("## Codex verdict (dual-endpoint)")
    lines.append("")
    lines.append(f"- **Verdict:** `{plan.codex.get('verdict')}`")
    lines.append(f"- **Source:** {plan.codex.get('source')}")
    lines.append(f"- **Last receipt at:** {plan.codex.get('last_receipt_at')}")
    lines.append(f"- **Last formal review ID:** {plan.codex.get('last_review_id')}")
    lines.append(f"- **Last issue comment ID:** {plan.codex.get('last_comment_id')}")
    lines.append(f"- **Would ping Codex:** {plan.codex.get('would_ping_codex')}")
    lines.append(f"- **Duplicate ping detected:** {plan.codex.get('duplicate_ping_detected')}")
    dual = plan.codex.get("dual_endpoint_check") or {}
    formal = dual.get("formal_review_endpoint") or {}
    issue = dual.get("issue_comment_endpoint") or {}
    lines.append(f"- **Formal review endpoint:** checked={formal.get('checked')}, "
                 f"found_fresh_review={formal.get('found_fresh_review')}")
    if formal.get("error"):
        lines.append(f"  - error: {formal['error']}")
    lines.append(f"- **Issue comment endpoint:** checked={issue.get('checked')}, "
                 f"found_clean_comment={issue.get('found_clean_comment')}")
    if issue.get("error"):
        lines.append(f"  - error: {issue['error']}")
    # PR #406 Codex finding PRRT_kwDOSHFpYM6LRnyz: surface the
    # raw endpoint_errors list (may contain error messages from
    # either endpoint).
    endpoint_errors = plan.codex.get("endpoint_errors") or []
    if endpoint_errors:
        lines.append("- **endpoint_errors:**")
        for err in endpoint_errors:
            lines.append(f"  - {err}")
    lines.append("")

    lines.append("## Branch protection")
    lines.append("")
    bp = plan.branch_protection
    if not bp.get("is_protected"):
        lines.append(f"- **Base branch:** {bp.get('base_branch')} (no protection / 404)")
    else:
        lines.append(f"- **Base branch:** {bp.get('base_branch')}")
        lines.append(
            f"- **Required status checks:** "
            f"{', '.join(bp.get('required_status_checks') or []) or '(none)'}"
        )
        lines.append(f"- **Strict status checks:** {bp.get('strict_status_checks')}")
        lines.append(
            f"- **Required conversation resolution:** "
            f"{bp.get('required_conversation_resolution')}"
        )
        lines.append(
            f"- **Required linear history:** {bp.get('required_linear_history')}"
        )
        lines.append(
            f"- **Required approving review count:** "
            f"{bp.get('required_approving_review_count')}"
        )
        lines.append(f"- **Enforce admins:** {bp.get('enforce_admins')}")
        lines.append(
            f"- **Allow force pushes:** {bp.get('allow_force_pushes')}"
        )
    if bp.get("violations"):
        lines.append(f"- **Violations:** {bp['violations']}")
    lines.append("")

    lines.append("## Proposed actions (preview only — never executed)")
    lines.append("")
    if not plan.proposed_actions:
        lines.append("(no actions proposed)")
    else:
        for action in plan.proposed_actions:
            lines.append(f"### Order {action.get('order')}: `{action.get('action_kind')}`")
            lines.append("")
            lines.append(f"- **Rationale:** {action.get('rationale')}")
            lines.append(f"- **Command preview:** `{action.get('command_preview')}`")
            lines.append(f"- **Mutates GitHub:** {action.get('mutates_github')}")
            lines.append(
                f"- **Requires human authorization:** "
                f"{action.get('requires_human_authorization')}"
            )
            lines.append("")
    lines.append(f"**Total mutations proposed (preview only):** {plan.mutations_proposed}")
    lines.append("")

    lines.append("## Blockers for merge")
    lines.append("")
    if not plan.blockers_for_merge:
        lines.append("(no blockers)")
    else:
        for blocker in plan.blockers_for_merge:
            lines.append(f"- **{blocker.get('kind')}:** {blocker.get('detail')}")
    lines.append("")

    if plan.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in plan.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("## Operator action")
    lines.append("")
    if plan.recommendation == "READY_TO_AUTHORIZE_HUMAN_MERGE":
        lines.append(
            "The plan is clean. Review the proposed merge command above, then "
            "execute it manually from your terminal. This script does **not** "
            "perform the merge. Do not execute any merge command until you "
            "have independently verified the state shown above."
        )
    else:
        lines.append(
            f"The plan is **not** ready for merge. Recommendation: "
            f"`{plan.recommendation}`. Address the blockers listed above "
            "before re-running this dry-run."
        )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aed_continue_pr",
        description=(
            "Compute a structured continuation plan for a PR without mutating "
            "GitHub. Mandatory --dry-run; refuses any --execute, --force, "
            "--admin, or --no-dry-run flag."
        ),
    )
    parser.add_argument(
        "--pr-number",
        type=int,
        required=True,
        help="PR number to plan continuation for",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="MANDATORY: compute plan without mutating GitHub",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=DEFAULT_REPO,
        help=f"GitHub repository in 'owner/name' form (default: {DEFAULT_REPO})",
    )
    parser.add_argument(
        "--repo-root",
        type=str,
        default=None,
        help="Absolute path to the AED repository root (for gate subprocess invocation)",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        required=True,
        help="Path to write the structured plan JSON",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        required=True,
        help="Path to write the human-readable markdown memo",
    )
    parser.add_argument(
        "--check-codex",
        action="store_true",
        default=True,
        help="Query both Codex endpoints (default: enabled)",
    )
    parser.add_argument(
        "--no-check-codex",
        dest="check_codex",
        action="store_false",
        help="Disable Codex dual-endpoint query",
    )
    parser.add_argument(
        "--max-poll-seconds",
        type=int,
        default=DEFAULT_MAX_POLL_SECONDS,
        help=(
            f"Bounded timeout for the gate subprocess invocation "
            f"(default: {DEFAULT_MAX_POLL_SECONDS}, cap: {MAX_POLL_SECONDS_CAP})"
        ),
    )
    parser.add_argument(
        "--last-known-codex-ts",
        type=str,
        default=None,
        help="Optional ISO timestamp for filtering stale Codex signals",
    )
    # We register these as hidden/forbidden for clarity (argparse
    # can reject them with a custom action). They are explicitly
    # rejected to prevent accidental misuse.
    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    # Pre-parse: reject forbidden flags early with a clear error.
    raw = list(sys.argv[1:] if argv is None else argv)
    forbidden_flags = {"--execute", "--force", "--admin", "--no-dry-run"}
    for flag in forbidden_flags:
        for token in raw:
            if token == flag or token.startswith(flag + "="):
                print(
                    f"ERROR: flag {flag!r} is forbidden in aed_continue_pr.py "
                    f"(this PR is dry-run only)",
                    file=sys.stderr,
                )
                sys.exit(2)
    parser = build_arg_parser()
    args = parser.parse_args(raw)
    if not args.dry_run:
        print(
            "ERROR: --dry-run is mandatory in this PR. "
            "A future PR will introduce a separate --execute command.",
            file=sys.stderr,
        )
        sys.exit(1)
    if args.max_poll_seconds > MAX_POLL_SECONDS_CAP:
        args.max_poll_seconds = MAX_POLL_SECONDS_CAP
    if args.max_poll_seconds < 1:
        args.max_poll_seconds = 1
    return args


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def compute_lifecycle_state(
    pr: Dict[str, Any], checks: Dict[str, Any]
) -> Dict[str, Any]:
    """Compute a lifecycle state hint from PR + checks (pure inference)."""
    if pr.get("state") != "OPEN":
        return {
            "current_state": f"PR_{pr.get('state')}",
            "source": "inferred_from_pr_state",
            "completed_phases": [],
            "remaining_permitted_mutations": [],
            "already_performed_mutations": [],
            "blocked_mutations": ["pr_merge", "thread_resolve", "pr_close"],
        }
    if not checks.get("all_required_green"):
        return {
            "current_state": "HOLD_PR_CI_PENDING",
            "source": "inferred_from_required_checks_not_green",
            "completed_phases": ["PHASE_1_PROTECTED_STATE_VERIFICATION"],
            "remaining_permitted_mutations": ["codex_re_request", "wait_for_ci"],
            "already_performed_mutations": [],
            "blocked_mutations": ["pr_merge"],
        }
    return {
        "current_state": "READY_FOR_FINAL_PREFLIGHT",
        "source": "inferred_from_pr_open_and_checks_green",
        "completed_phases": [
            "PHASE_1_PROTECTED_STATE_VERIFICATION",
            "PHASE_2_CI_PROTECTION_GATE",
        ],
        "remaining_permitted_mutations": [
            "codex_re_request_if_idle",
            "thread_resolve_if_safe",
            "pr_merge",
        ],
        "already_performed_mutations": [],
        "blocked_mutations": ["worktree_update"],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    repo = args.repo
    pr_number = args.pr_number
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        # 1. Fetch PR state
        pr = fetch_pr_state(repo, pr_number)
        pr["_repo"] = repo

        # 2. Fetch branch protection
        bp = fetch_branch_protection(repo, pr.get("base_ref") or "main")

        # 3. Fetch required checks
        # Pass the branch-protection required contexts so the check
        # can be reconciled against branch protection requirements
        # (PR #406 Codex finding 3449089428).
        required_names = []
        if isinstance(bp, dict):
            required_names = bp.get("required_status_checks") or []
        checks = fetch_required_checks(
            repo,
            pr_number,
            pr.get("head_sha") or "",
            required_check_names=required_names,
        )

        # 4. Run gate
        repo_root = Path(args.repo_root) if args.repo_root else None
        # PR #406 Codex finding PRRT_kwDOSHFpYM6LRny_: pass the
        # operator-supplied (or default) max-poll-seconds into the
        # gate subprocess timeout, NOT a hard-coded 120.
        gate = run_review_gate(
            repo,
            pr_number,
            pr.get("head_sha") or "",
            repo_root=repo_root,
            max_poll_seconds=args.max_poll_seconds,
        )

        # 5. Dual-endpoint Codex
        if args.check_codex:
            codex = fetch_codex_verdict(
                repo, pr_number, since_iso=args.last_known_codex_ts
            )
        else:
            codex = {
                "verdict": "unknown",
                "source": "skipped_by_arg",
                "last_receipt_at": None,
                "last_review_id": None,
                "last_comment_id": None,
                "would_ping_codex": False,
                "duplicate_ping_detected": False,
                "dual_endpoint_check": {
                    "formal_review_endpoint": {"checked": False},
                    "issue_comment_endpoint": {"checked": False},
                },
            }

        # 6. Compute lifecycle
        lifecycle = compute_lifecycle_state(pr, checks)

        # 7. Assemble plan
        plan = assemble_plan(
            pr=pr,
            lifecycle=lifecycle,
            checks=checks,
            gate=gate,
            codex=codex,
            branch_protection=bp,
            generated_at=generated_at,
        )
        plan_dict = plan.to_dict()

        # 8. Write JSON output
        try:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(json.dumps(plan_dict, indent=2, sort_keys=True))
        except OSError as exc:
            print(f"ERROR: failed to write JSON output: {exc}", file=sys.stderr)
            return 5

        # 9. Write markdown output
        try:
            args.output_md.parent.mkdir(parents=True, exist_ok=True)
            args.output_md.write_text(render_markdown(plan))
        except OSError as exc:
            print(f"ERROR: failed to write markdown output: {exc}", file=sys.stderr)
            return 5

        # 10. Print summary
        print(
            f"[aed_continue_pr] plan_kind={plan.plan_kind} "
            f"recommendation={plan.recommendation} "
            f"mutations_proposed={plan.mutations_proposed} "
            f"warnings={len(plan.warnings)} "
            f"blockers={len(plan.blockers_for_merge)} "
            f"output_json={args.output_json} "
            f"output_md={args.output_md}"
        )
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # pragma: no cover — defensive
        print(f"ERROR: unexpected: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
