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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


# Codex finding 3455479190 (P1): the planner imports
# ``aed_lifecycle`` (for ``CheckpointState``,
# ``validate_checkpoint``, ``validate_resume_observations``)
# at function-call time, which works only when the script is
# run from the repo root (where ``aed_lifecycle/`` is a
# top-level package). When the operator invokes the documented
# form ``python3 scripts/local/aed_continue_pr.py ...`` from
# anywhere else — including ``/tmp``, a different worktree,
# or any parent directory — Python's import resolution does
# not find ``aed_lifecycle`` and the checkpoint loading path
# silently degrades to "validator unavailable". Add the repo
# root (the parent of ``scripts/`` and of ``aed_lifecycle/``)
# to ``sys.path`` at module load time, computed stably from
# the script's own location so the import works regardless of
# the current working directory. This is the canonical fix
# prescribed by ``docs/aed_continue_pr.md`` — the documented
# invocation assumes the script can find its sibling package.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


SCHEMA_VERSION = 1
PLAN_KIND = "aed.continue_pr.dry_run"
DEFAULT_REPO = "Slideshow11/Automated-Edge-Discovery"
CODEX_LOGIN = "chatgpt-codex-connector[bot]"
# PR #406 Codex finding PRRT_kwDOSHFpYM6LSODH: the clean-signal
# patterns must accept BOTH the ASCII apostrophe (', U+0027) and
# the curly apostrophe (', U+2019) that Codex actually emits. The
# original pattern only matched the straight-apostrophe variant.
# NB: in a raw string r"['’]" the curly apostrophe must be
# embedded as the literal U+2019 character (not the escape).
_APOSTROPHE = r"['’]"
# PR #406 Codex finding 3453151187 (P2): Codex's documented clean
# phrase appears in THREE variants and the patterns must accept
# all of them while remaining conservative (no arbitrary "no issues"
# phrase matching). The three variants are:
#   - "Didn't find any major issues"   (ASCII + contracted)
#   - "Didn’t find any major issues"   (curly  + contracted)
#   - "Did not find any major issues"  (no apostrophe, non-contracted)
# We model this with a single alternation inside the pattern:
#   (?:n['’]?t | not)
# which matches either "nt" / "n't" / "n’t" (contracted, with
# optional apostrophe) OR " not" (non-contracted). The optional
# apostrophe means the contracted variants work with or without
# an apostrophe, while " not" preserves the documented non-
# contracted form. The outer regex stays anchored on "Did" so we
# do not broaden to arbitrary "no issues" text.
_DID_AFFIRMATIVE = r"(?:n['’]?t| not)"
_CLEAN_DID_PHRASE = (
    r"(?:[Nn]o major issues|✅|Swish|👍|"
    + r"[Dd]id"
    + _DID_AFFIRMATIVE
    + r" find any major)"
)
CLEAN_PATTERN = re.compile(
    r"^Codex Review:.*" + _CLEAN_DID_PHRASE
)
CLEAN_FALLBACK_PATTERN = re.compile(
    r"(?:[Nn]o major issues|✅|Swish|👍|looks good|" + _CLEAN_DID_PHRASE + ")",
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
    # PR #407: optional checkpoint envelope. When the operator does
    # NOT pass ``--checkpoint-json`` this is a minimal
    # ``{"present": False, "path": None}`` dict, and the rest of the
    # plan is byte-equivalent to PR #406 for any fixed live input.
    checkpoint: Dict[str, Any] = dataclasses.field(default_factory=dict)

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


def _safe_unlink(path: Path) -> None:
    """Unlink ``path`` if it exists; ignore missing-file errors.

    PR #406 Codex finding 3453151179 (P1): the deterministic gate
    output path ``/tmp/aed_gate_<pr>_<sha>.json`` can persist from
    a prior invocation. ``run_review_gate`` unlinks any pre-existing
    file at that path before launching the subprocess so a stale
    JSON cannot be read as if it were current.
    """
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        # Don't fail the planner if unlink fails (e.g., permission
        # error on someone else's file). The freshness check below
        # will catch a stale or unwritable file anyway.
        return


def _is_fresh_written(path: Path, invocation_start: float) -> bool:
    """Return True iff ``path`` exists and was written at/after ``invocation_start``.

    PR #406 Codex finding 3453151179 (P1): ``run_review_gate`` only
    trusts a gate-output JSON if its mtime is at or after the
    recorded invocation start. Files with older mtime (i.e., from
    a prior run) or files that don't exist return False so the
    caller treats the result as a command failure rather than
    trusting stale data.
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return st.st_mtime >= invocation_start


class GhApiError(RuntimeError):
    """Exception raised when a ``gh api`` call fails.

    Carries the gh exit code (``rc``) and the API endpoint
    (``endpoint``) so callers can distinguish a 404 (unprotected
    branch) from permission/rate-limit/malformed errors. PR #406
    Codex finding PRRT_kwDOSHFpYM6LSOC_ requires this distinction.
    """

    def __init__(self, message: str, rc: int = -1, endpoint: str = ""):
        super().__init__(message)
        self.rc = rc
        self.endpoint = endpoint


def _is_not_found_error(stderr: str) -> bool:
    """Return True if the gh error output indicates a 404 Not Found."""
    s = (stderr or "").lower()
    return (
        "404" in s
        or "not found" in s
        or "branch not protected" in s
    )


def _is_permission_error(stderr: str) -> bool:
    """Return True if the gh error output indicates a permission/403 error."""
    s = (stderr or "").lower()
    return (
        "403" in s
        or "forbidden" in s
        or "permission" in s
        or "access denied" in s
    )


def _is_rate_limit_error(stderr: str) -> bool:
    """Return True if the gh error output indicates a rate-limit/429 error."""
    s = (stderr or "").lower()
    return "rate limit" in s or "429" in s or "abuse detection" in s


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
        raise GhApiError(
            f"gh api GET /repos/{repo}{endpoint} failed (rc={rc}): {stderr.strip()[:500]}",
            rc=rc,
            endpoint=endpoint,
        )
    if not stdout.strip():
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise GhApiError(
            f"gh api GET /repos/{repo}{endpoint} returned non-JSON: {exc}: {stdout[:500]}",
            rc=-1,
            endpoint=endpoint,
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
    """Fetch branch protection rules for ``base_branch``.

    PR #406 Codex finding PRRT_kwDOSHFpYM6LSOC_
    -------------------------------------------
    Distinguish:
      - confirmed unprotected branch (404)
      - protected branch (success)
      - branch-protection API failure (403 permission, 429 rate-limit,
        malformed response, etc.)

    Only a confirmed 404 sets ``is_protected=False``. Any other API
    failure sets ``is_protected=None`` (unknown) and ``protection_status
    = "api_error"`` so the planner can fail closed on merge.
    """
    endpoint = f"/branches/{base_branch}/protection"
    try:
        data = _run_gh_api(repo, endpoint)
    except GhApiError as exc:
        # Try to inspect stderr-like info in the exception message
        # to classify the failure. The exception message embeds the
        # gh stderr (truncated to 500 chars).
        msg = str(exc)
        is_404 = (
            exc.rc == 404
            or _is_not_found_error(msg)
            or "branch not protected" in msg.lower()
        )
        if is_404:
            # Confirmed unprotected branch.
            return {
                "base_branch": base_branch,
                "is_protected": False,
                "protection_status": "unprotected",
                "required_status_checks": [],
                "required_conversation_resolution": False,
                "error": None,
            }
        # Other API failure: permission, rate-limit, malformed, etc.
        kind = "unknown_error"
        if _is_permission_error(msg):
            kind = "permission_denied"
        elif _is_rate_limit_error(msg):
            kind = "rate_limited"
        elif "non-json" in msg.lower() or "returned non-json" in msg.lower():
            # Malformed response (JSON parse error)
            kind = "malformed_response"
        return {
            "base_branch": base_branch,
            "is_protected": None,  # unknown — NOT False
            "protection_status": "api_error",
            "protection_error_kind": kind,
            "required_status_checks": [],
            "required_conversation_resolution": False,
            "error": str(exc),
        }
    except RuntimeError as exc:
        # Defensive: any other RuntimeError → treat as API error.
        return {
            "base_branch": base_branch,
            "is_protected": None,
            "protection_status": "api_error",
            "protection_error_kind": "unknown_error",
            "required_status_checks": [],
            "required_conversation_resolution": False,
            "error": str(exc),
        }
    if not isinstance(data, dict):
        return {
            "base_branch": base_branch,
            "is_protected": None,
            "protection_status": "api_error",
            "protection_error_kind": "malformed_response",
            "required_status_checks": [],
            "required_conversation_resolution": False,
            "error": "branch protection response was not a JSON object",
        }
    status_checks = data.get("required_status_checks") or {}
    reviews = data.get("required_pull_request_reviews") or {}
    return {
        "base_branch": base_branch,
        "is_protected": True,
        "protection_status": "protected",
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
    head_sha: Optional[str] = None,
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

    Exact-head freshness (PR #406 Codex finding PRRT_kwDOSHFpYM6LSOC7)
    ----------------------------------------------------------------
    When ``since_iso`` is NOT provided (the documented default), a
    conservative default freshness check is applied: the verdict can
    only be ``clean`` if at least one Codex record explicitly maps to
    the current head SHA.

    For formal reviews: the review's ``commit_id`` (or
    ``commit_sha``/``commit.oid``) must match ``head_sha`` (prefix
    or full).

    For issue comments: the body must contain a
    ``**Reviewed commit:** `SHA` `` marker that matches ``head_sha``.

    If neither endpoint has an exact-head signal, the verdict is
    ``pending`` with source ``no_exact_head_codex_signal`` — the
    planner will NOT recommend merge authorization from old Codex
    clean comments/reviews.
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

    # --- Exact-head freshness filter (PR #406 Codex finding PRRT_kwDOSHFpYM6LSOC7) ---
    # When the operator does NOT pass --last-known-codex-ts (the
    # documented default), historical Codex activity is NOT trusted
    # as "fresh". A Codex clean/actionable signal must explicitly
    # map to the current head SHA to be accepted.
    #
    # Formal reviews: the review's commit_id / commit_sha /
    # commit.oid must match head_sha.
    #
    # Issue comments: the body must contain a
    # "**Reviewed commit:** `SHA`" marker (or "Reviewed commit: SHA"
    # in plain text) that matches head_sha.
    exact_head_applied = False
    if not since_iso and head_sha:
        # Capture pre-exact-head fresh counts so we can update
        # the stale counts correctly.
        pre_exact_head_formal = list(fresh_formal_reviews)
        pre_exact_head_issue = list(fresh_issue_comments)
        fresh_formal_reviews = [
            r
            for r in fresh_formal_reviews
            if _review_commit_matches_head(r, head_sha)
        ]
        fresh_issue_comments = [
            c
            for c in fresh_issue_comments
            if _body_has_reviewed_commit_marker(c.get("body") or "", head_sha)
        ]
        # The records that were "fresh" by timestamp but did NOT
        # match the current head are reclassified as "stale" (they
        # are not actionable for the current head).
        dropped_formal = len(pre_exact_head_formal) - len(fresh_formal_reviews)
        dropped_issue = len(pre_exact_head_issue) - len(fresh_issue_comments)
        stale_formal_count = stale_formal_count + dropped_formal
        stale_issue_count = stale_issue_count + dropped_issue
        exact_head_applied = True

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

    # --- Fail-closed when no exact-head Codex signal exists (PR #406
    # Codex finding PRRT_kwDOSHFpYM6LSOC7). When --last-known-codex-ts
    # is omitted, we ONLY trust records that map to the current head.
    # If no such record was found, demote the verdict to pending with
    # source "no_exact_head_codex_signal" — the planner must not
    # authorize a merge based on a stale clean Codex signal.
    if (
        exact_head_applied
        and not fresh_formal_reviews
        and not fresh_issue_comments
        and not endpoint_errors
        and verdict in {"clean", "conflicting", "pending"}
    ):
        verdict = "pending"
        source = "no_exact_head_codex_signal"
    # If no cutoff and no head_sha was provided, the freshness check
    # is not possible — be conservative and refuse to call verdict
    # "clean" based on unverified historical activity.
    elif (
        not since_iso
        and not head_sha
        and not endpoint_errors
        and verdict == "clean"
    ):
        verdict = "pending"
        source = "no_exact_head_codex_signal_no_head_sha"
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
        "head_sha_checked": head_sha,
        "exact_head_applied": exact_head_applied,
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


def _review_commit_matches_head(
    review: Dict[str, Any],
    head_sha: str,
) -> bool:
    """Return True if a formal review's commit_id matches the head SHA.

    PR #406 Codex finding PRRT_kwDOSHFpYM6LSOC7: When
    ``--last-known-codex-ts`` is omitted, a formal review is only
    trusted as a fresh signal if its commit_id (the SHA the review
    was anchored to) matches the current PR head SHA.

    GitHub's review API exposes the anchor commit under several
    aliases (``commit_id`` is the field name in the REST response;
    ``commit_sha`` and ``commit.oid`` are also accepted for
    GraphQL-style payloads). We match on full equality or on a
    unique 12+ character prefix.
    """
    if not head_sha or not isinstance(review, dict):
        return False
    head = head_sha.lower()
    candidates: List[str] = []
    if review.get("commit_id"):
        candidates.append(str(review["commit_id"]))
    if review.get("commit_sha"):
        candidates.append(str(review["commit_sha"]))
    commit_obj = review.get("commit")
    if isinstance(commit_obj, dict):
        if commit_obj.get("oid"):
            candidates.append(str(commit_obj["oid"]))
        if commit_obj.get("sha"):
            candidates.append(str(commit_obj["sha"]))
    for c in candidates:
        c_lc = c.lower()
        if c_lc == head:
            return True
        # 12-char prefix match (GitHub short-SHA convention).
        if len(head) >= 12 and len(c_lc) >= 12 and c_lc[:12] == head[:12]:
            return True
    return False


# PR #406 Codex finding PRRT_kwDOSHFpYM6LSOC7: parse a
# "**Reviewed commit:** `SHA`" marker in a Codex issue comment.
_REVIEWED_COMMIT_MARKER = re.compile(
    r"\*\*(?:Reviewed commit|reviewed commit):\*\*\s*[`'\"]?([0-9a-fA-F]{7,40})[`'\"]?",
)
# Plain-text variant: "Reviewed commit: abc1234" (no markdown emphasis).
_REVIEWED_COMMIT_PLAIN = re.compile(
    r"(?:Reviewed commit|reviewed commit):\s*[`'\"]?([0-9a-fA-F]{7,40})[`'\"]?"
)


def _body_has_reviewed_commit_marker(body: str, head_sha: str) -> bool:
    """Return True if a Codex issue comment body contains a
    ``Reviewed commit: SHA`` marker matching ``head_sha``."""
    if not body or not head_sha:
        return False
    head = head_sha.lower()
    for pattern in (_REVIEWED_COMMIT_MARKER, _REVIEWED_COMMIT_PLAIN):
        m = pattern.search(body)
        if m:
            sha = m.group(1).lower()
            if sha == head:
                return True
            if len(head) >= 12 and len(sha) >= 12 and sha[:12] == head[:12]:
                return True
    return False


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
    # Use a temp file for gate output.
    # PR #406 Codex finding 3453151179 (P1): the deterministic
    # ``aed_gate_<pr>_<sha>.json`` path can still exist from a
    # prior invocation. If the current subprocess exits non-zero
    # before writing a fresh report, the V3 logic would otherwise
    # trust the stale JSON from the prior run. We therefore:
    #   1. Unlink any pre-existing file at the deterministic path
    #      before launching the subprocess so a stale file cannot
    #      be read as if it were current.
    #   2. Record the invocation start time so we can verify any
    #      JSON present after the subprocess returns was written
    #      by *this* invocation (mtime >= start time).
    #   3. Only trust the post-subprocess JSON if its mtime is
    #      >= the recorded start time; otherwise treat it as a
    #      command failure / inconclusive.
    tmp_json = Path("/tmp") / f"aed_gate_{pr_number}_{head_sha[:12]}.json"
    tmp_md = Path("/tmp") / f"aed_gate_{pr_number}_{head_sha[:12]}.md"
    invocation_start = time.time()
    _safe_unlink(tmp_json)
    _safe_unlink(tmp_md)
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
    # PR #406 Codex finding 3453151179 (P1): never trust a JSON
    # file unless we can prove it was written by the current
    # invocation. If the file is missing, or its mtime predates
    # this invocation, it is either stale or was never written;
    # treat the run as a command failure regardless of rc.
    fresh_json = _is_fresh_written(tmp_json, invocation_start)
    if rc != 0:
        # PR #406 Codex finding PRRT_kwDOSHFpYM6LSODA: do NOT
        # discard the gate JSON when the subprocess exits non-zero.
        # ``check_pr_review_comments.py`` writes a JSON file with
        # the gate status (``REVIEW_COMMENTS_BLOCKED`` etc.) and
        # exits 1 to signal a blocked result. The previous behavior
        # of returning ``REVIEW_COMMENTS_INCONCLUSIVE`` on any non-
        # zero exit discarded the exact blocker status the gate
        # produced. We now read the JSON (if present AND fresh)
        # and surface the gate's own status; only fall back to
        # INCONCLUSIVE when no fresh JSON exists.
        if fresh_json:
            try:
                gate_data = json.loads(tmp_json.read_text())
                # Surface the gate's verdict along with the non-zero
                # exit code so the planner can still act on the
                # gate's actual status.
                return {
                    "status": gate_data.get("status")
                    or "REVIEW_COMMENTS_INCONCLUSIVE",
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
                    "gate_timeout_used": gate_timeout,
                    "gate_subprocess_nonzero_exit": True,
                    "gate_subprocess_rc": rc,
                    "gate_subprocess_stderr": (stderr or "").strip()[:500],
                    "raw_path": str(tmp_json),
                }
            except (json.JSONDecodeError, OSError) as exc:
                # Fresh JSON but unreadable — fall through to inconclusive.
                return {
                    "status": "REVIEW_COMMENTS_INCONCLUSIVE",
                    "error": (
                        f"gate subprocess failed (rc={rc}) and produced "
                        f"unreadable JSON: {exc}: {stderr.strip()[:500]}"
                    ),
                    "gate_timeout_used": gate_timeout,
                }
        # No fresh JSON — either the subprocess never wrote one,
        # or the file present is stale from a prior invocation.
        # Treat as a genuine command failure.
        return {
            "status": "REVIEW_COMMENTS_INCONCLUSIVE",
            "error": (
                f"gate subprocess failed (rc={rc}) without a fresh JSON "
                f"report (stale or missing output at {tmp_json}): "
                f"{stderr.strip()[:500]}"
            ),
            "gate_timeout_used": gate_timeout,
        }
    if not fresh_json:
        # rc==0 but no fresh JSON — the subprocess claimed success
        # but produced no usable report. This is a command failure
        # (or the file was unlinked between subprocess write and
        # our stat). Treat as inconclusive.
        return {
            "status": "REVIEW_COMMENTS_INCONCLUSIVE",
            "error": (
                f"gate subprocess exited 0 but produced no fresh JSON "
                f"at {tmp_json} (missing or stale)"
            ),
            "gate_timeout_used": gate_timeout,
        }
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


# ---------------------------------------------------------------------------
# PR #407: Checkpoint ingestion helpers
#
# Read-only consumer of aed_lifecycle.checkpoint. We never write back to
# the checkpoint file, never modify aed_lifecycle/*, and never invoke any
# mutating helper from that package. The contract is:
#
#   - _load_checkpoint_payload(path) -> raw dict or {"present": False}
#   - _validate_checkpoint_payload(raw) -> (envelope, blockers, warnings)
#   - _cross_reference_checkpoint(envelope, live_pr) -> (blockers, warnings)
#
# All helpers return structured dicts so the assemble_plan layer can
# combine them with the live signals without ever importing the
# aed_lifecycle dataclasses by name. This keeps the dry-run command
# resilient to aed_lifecycle refactors.
# ---------------------------------------------------------------------------

CHECKPOINT_ENVELOPE_SCHEMA_VERSION = 1


def _absent_checkpoint_envelope(path: Optional[Path] = None) -> Dict[str, Any]:
    """Return a minimal envelope when no checkpoint was provided."""
    return {
        "present": False,
        "path": str(path) if path else None,
        "load_status": "not_provided",
        "schema_version": CHECKPOINT_ENVELOPE_SCHEMA_VERSION,
        "errors": [],
        "warnings": [],
        "validation": {
            "status": "skipped",
            "errors": [],
            "warnings": [],
        },
        "cross_reference": {
            "status": "skipped",
            "blockers": [],
            "warnings": [],
        },
        "combination": {
            "live_state_agrees": True,
            "merge_ready_both_sides": False,
            "blockers": [],
            "warnings": [],
        },
    }


def _load_checkpoint_payload(path: Path) -> Dict[str, Any]:
    """Load a checkpoint JSON file read-only and return a raw dict.

    The function is fail-closed: missing, malformed, or unreadable files
    are surfaced as ``{"present": True, "load_status": "...", "errors":
    [...]}`` rather than raising. Callers should treat any
    ``load_status`` other than ``loaded`` as a fail-closed condition.
    """
    envelope: Dict[str, Any] = {
        "present": True,
        "path": str(path),
        "load_status": "pending",
        "schema_version": CHECKPOINT_ENVELOPE_SCHEMA_VERSION,
        "errors": [],
        "warnings": [],
        "validation": {
            "status": "skipped",
            "errors": [],
            "warnings": [],
        },
        "cross_reference": {
            "status": "skipped",
            "blockers": [],
            "warnings": [],
        },
        "combination": {
            "live_state_agrees": False,
            "merge_ready_both_sides": False,
            "blockers": [],
            "warnings": [],
        },
    }
    if not path.exists():
        envelope["load_status"] = "file_missing"
        envelope["errors"].append(f"checkpoint file not found: {path}")
        return envelope
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        envelope["load_status"] = "malformed_json"
        envelope["errors"].append(f"checkpoint JSON is malformed: {exc}")
        return envelope
    except OSError as exc:
        envelope["load_status"] = "unreadable"
        envelope["errors"].append(f"checkpoint file is unreadable: {exc}")
        return envelope
    if not isinstance(payload, dict):
        envelope["load_status"] = "malformed_json"
        envelope["errors"].append(
            f"checkpoint JSON must be an object, got {type(payload).__name__}"
        )
        return envelope
    envelope["load_status"] = "loaded"
    envelope["raw"] = payload
    return envelope


def _coerce_optional_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _coerce_optional_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _coerce_str_list(value: Any) -> List[str]:
    """STRICT list coercion for checkpoint list fields.

    Codex findings 3455441250 (P2) and 3455479194 (P1): the
    previous coercion silently dropped non-string entries, which
    allowed structurally malformed checkpoints (e.g. ``"foo"``,
    ``42``, ``"bar"``) to validate cleanly because the
    canonical ``aed_lifecycle.checkpoint.validate_checkpoint``
    only ever saw the string-only subset. The raw payload
    is now validated FIRST via
    :func:`_validate_raw_required_list_fields` so this function
    is only ever called with one of three inputs:

      - ``None`` (field missing from payload) — returned as ``[]``
      - a list of strings (already structurally validated) —
        returned verbatim
      - anything else — refused (returned as ``[]`` AND flagged
        by the caller; canonical validation will surface the
        real error)

    The strict pass-through is intentional: callers should
    rely on the canonical validator to report bad lists with
    full diagnostics, not on this helper to silently clean
    them.
    """
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return list(value)
    # Caller has not validated the raw payload yet; refuse to
    # silently coerce. The canonical validator will surface
    # the real error with full diagnostics.
    return []


def _validate_raw_required_list_fields(payload: Dict[str, Any]) -> List[str]:
    """Validate required checkpoint list fields on the RAW payload.

    Codex findings 3455441250 (P2) and 3455479194 (P1):
    before any ``_coerce_str_list`` call, the raw payload must
    be checked for missing/malformed required list fields.
    Returns a list of human-readable error messages (empty =
    structurally acceptable). The canonical
    ``aed_lifecycle.checkpoint._REQUIRED_LIST_FIELDS`` is the
    source of truth for which fields are required; we mirror
    that list here so this validator agrees with the canonical
    one.

    Rules:

    - Field is **missing** from the payload → error (the
      canonical validator requires the field to be a list;
      defaulting it to ``None`` before validation hides the
      "field missing" condition from the operator).
    - Field is **not a list** → error (must be ``list[str]``).
    - Field is a list with **any non-string entries** → error.
    - Field is a list of strings → OK.

    This validator runs BEFORE
    :func:`_checkpoint_state_from_payload` and BEFORE the
    canonical ``validate_checkpoint`` so that malformed
    checkpoints cannot be silently normalized into a clean
    ``CheckpointState``.
    """
    errors: List[str] = []
    required_list_fields = (
        "completed_phases",
        "pending_actions",
        "authorized_thread_ids",
        "unresolved_thread_ids",
    )
    for fname in required_list_fields:
        if fname not in payload:
            errors.append(
                f"checkpoint missing required list field {fname!r}"
            )
            continue
        value = payload[fname]
        if not isinstance(value, list):
            errors.append(
                f"checkpoint field {fname!r} must be list[str], "
                f"got {type(value).__name__}"
            )
            continue
        if not all(isinstance(x, str) for x in value):
            bad = [
                (i, type(x).__name__)
                for i, x in enumerate(value)
                if not isinstance(x, str)
            ]
            errors.append(
                f"checkpoint field {fname!r} must be list[str]; "
                f"non-string entries at indices {bad}"
            )
    return errors


def _checkpoint_state_from_payload(payload: Dict[str, Any]) -> Any:
    """Construct an ``aed_lifecycle.checkpoint.CheckpointState`` from a raw payload.

    Returns the constructed ``CheckpointState`` instance, or ``None`` if
    ``aed_lifecycle`` is unavailable (e.g., running outside the AED
    repo) or the required fields cannot be coerced. The dry-run command
    must never crash if ``aed_lifecycle`` is missing — that would break
    the read-only contract.

    Codex findings 3455441250 (P2) and 3455479194 (P1):
    ``_coerce_str_list`` is now strict — callers must run
    :func:`_validate_raw_required_list_fields` on the raw
    payload FIRST and surface any errors before reaching this
    function. The canonical
    ``aed_lifecycle.checkpoint.validate_checkpoint`` is the
    authority on structural correctness; this function only
    hands the raw payload through and lets the canonical
    validator flag malformed lists.
    """
    try:
        from aed_lifecycle.checkpoint import CheckpointState  # type: ignore
    except ImportError:
        return None
    repo = _coerce_optional_str(payload.get("repo"))
    pr_number = _coerce_optional_int(payload.get("pr_number"))
    branch = _coerce_optional_str(payload.get("branch"))
    current_head = _coerce_optional_str(payload.get("current_head"))
    if not (repo and pr_number is not None and branch and current_head):
        return None
    return CheckpointState(
        repo=repo,
        pr_number=pr_number,
        branch=branch,
        current_head=current_head,
        phase=_coerce_optional_str(payload.get("phase")),
        completed_phases=_coerce_str_list(payload.get("completed_phases")),
        next_phase=_coerce_optional_str(payload.get("next_phase")),
        next_action=_coerce_optional_str(payload.get("next_action")),
        pending_actions=_coerce_str_list(payload.get("pending_actions")),
        last_verified_primary_head=_coerce_optional_str(
            payload.get("last_verified_primary_head")
        ),
        last_verified_pr_head=_coerce_optional_str(
            payload.get("last_verified_pr_head")
        ),
        authorized_thread_ids=_coerce_str_list(payload.get("authorized_thread_ids")),
        unresolved_thread_ids=_coerce_str_list(payload.get("unresolved_thread_ids")),
        terminal_state=_coerce_optional_str(payload.get("terminal_state")),
        updated_at=_coerce_optional_str(payload.get("updated_at")),
    )


def _validate_checkpoint_payload(envelope: Dict[str, Any]) -> List[str]:
    """Run AED checkpoint validators and return a list of error messages.

    Mutates ``envelope["validation"]`` in place to record the
    validator status. Returns a flat list of error messages suitable
    for surfacing as ``CHECKPOINT_VALIDATION_INVALID`` blockers. The
    validators consumed are:

      - ``validate_checkpoint`` from ``aed_lifecycle.checkpoint``
      - ``validate_resume_observations`` (only if the checkpoint
        recorded ``last_verified_pr_head`` and we have a live PR head
        to compare against — handled by ``_cross_reference_checkpoint``).
    """
    if envelope.get("load_status") != "loaded":
        envelope["validation"]["status"] = "skipped"
        envelope["validation"]["errors"] = list(envelope.get("errors", []))
        envelope["validation"]["warnings"] = list(envelope.get("warnings", []))
        return list(envelope.get("errors", []))
    payload = envelope.get("raw") or {}
    # Codex findings 3455441250 (P2) and 3455479194 (P1):
    # validate the raw payload's required list fields BEFORE
    # any coercion / CheckpointState construction so that
    # missing or malformed list fields cannot be silently
    # normalized into a clean validation pass. The raw
    # validator returns a list of structural errors that the
    # canonical ``aed_lifecycle.checkpoint.validate_checkpoint``
    # would otherwise only see AFTER coercion has stripped the
    # non-string entries.
    raw_list_errors = _validate_raw_required_list_fields(payload)
    if raw_list_errors:
        envelope["validation"]["status"] = "invalid"
        envelope["validation"]["errors"] = list(raw_list_errors)
        envelope["validation"]["raw_list_errors"] = list(raw_list_errors)
        envelope["errors"].extend(raw_list_errors)
        return list(raw_list_errors)
    state = _checkpoint_state_from_payload(payload)
    if state is None:
        envelope["validation"]["status"] = "schema_invalid"
        envelope["validation"]["errors"] = [
            "checkpoint is missing required fields "
            "(repo, pr_number, branch, current_head) or aed_lifecycle "
            "is unavailable"
        ]
        envelope["errors"].extend(envelope["validation"]["errors"])
        return list(envelope["validation"]["errors"])
    envelope["validation"]["state_summary"] = {
        "repo": state.repo,
        "pr_number": state.pr_number,
        "branch": state.branch,
        "current_head": state.current_head,
        "phase": state.phase,
        "next_phase": state.next_phase,
        "next_action": state.next_action,
        "terminal_state": state.terminal_state,
        "updated_at": state.updated_at,
        "completed_phases_count": len(state.completed_phases or []),
        "pending_actions_count": len(state.pending_actions or []),
        "authorized_thread_ids_count": len(state.authorized_thread_ids or []),
        "unresolved_thread_ids_count": len(state.unresolved_thread_ids or []),
        "last_verified_pr_head": state.last_verified_pr_head,
        "last_verified_primary_head": state.last_verified_primary_head,
    }
    try:
        from aed_lifecycle.checkpoint import validate_checkpoint  # type: ignore
        structural_errors = validate_checkpoint(state)
    except ImportError:
        envelope["validation"]["status"] = "validator_unavailable"
        envelope["validation"]["errors"] = [
            "aed_lifecycle.checkpoint.validate_checkpoint is unavailable"
        ]
        envelope["errors"].extend(envelope["validation"]["errors"])
        return list(envelope["validation"]["errors"])
    except Exception as exc:  # pragma: no cover — defensive
        envelope["validation"]["status"] = "validator_raised"
        envelope["validation"]["errors"] = [
            f"checkpoint validator raised: {type(exc).__name__}: {exc}"
        ]
        envelope["errors"].extend(envelope["validation"]["errors"])
        return list(envelope["validation"]["errors"])
    if structural_errors:
        envelope["validation"]["status"] = "invalid"
        envelope["validation"]["errors"] = list(structural_errors)
        envelope["errors"].extend(structural_errors)
        return list(structural_errors)
    envelope["validation"]["status"] = "clean"
    envelope["validation"]["errors"] = []
    envelope["validation"]["warnings"] = []
    return []


def _cross_reference_checkpoint(
    envelope: Dict[str, Any],
    live_pr: Dict[str, Any],
    live_main_sha: Optional[str] = None,
) -> Tuple[List[Dict[str, str]], List[str]]:
    """Compare checkpoint evidence against live GitHub state.

    Returns ``(blockers, warnings)`` where each blocker is a dict
    ``{"kind": str, "detail": str}``. The function mutates
    ``envelope["cross_reference"]`` and ``envelope["combination"]`` in
    place so callers can render the structured envelope.

    Live state is passed in as a dict (already fetched by
    ``fetch_pr_state``); ``live_main_sha`` is optional and only used if
    the checkpoint recorded a ``last_verified_primary_head``.
    """
    blockers: List[Dict[str, str]] = []
    warnings: List[str] = []
    if envelope.get("load_status") != "loaded":
        envelope["cross_reference"]["status"] = "skipped"
        envelope["cross_reference"]["blockers"] = []
        envelope["cross_reference"]["warnings"] = []
        envelope["combination"]["blockers"] = []
        envelope["combination"]["warnings"] = []
        envelope["combination"]["live_state_agrees"] = True
        envelope["combination"]["merge_ready_both_sides"] = False
        return blockers, warnings
    state_summary = envelope.get("validation", {}).get("state_summary") or {}
    if envelope.get("validation", {}).get("status") != "clean":
        envelope["cross_reference"]["status"] = "skipped_due_to_validation_error"
        envelope["cross_reference"]["warnings"].append(
            "cross-reference skipped because checkpoint validation failed"
        )
        envelope["combination"]["live_state_agrees"] = False
        envelope["combination"]["merge_ready_both_sides"] = False
        envelope["combination"]["blockers"] = []
        envelope["combination"]["warnings"] = list(
            envelope["cross_reference"]["warnings"]
        )
        return blockers, warnings
    live_pr_number = live_pr.get("number")
    live_pr_head = live_pr.get("head_sha")
    live_pr_state = live_pr.get("state")
    live_pr_draft = live_pr.get("is_draft")
    live_branch = live_pr.get("head_ref")
    live_merge_state = live_pr.get("merge_state_status")
    # Cross-reference rules. Each rule produces either a blocker or a
    # warning; we never silently downgrade a real disagreement.
    if state_summary.get("pr_number") != live_pr_number:
        blockers.append(
            {
                "kind": "CHECKPOINT_PR_NUMBER_MISMATCH",
                "detail": (
                    f"checkpoint pr_number={state_summary.get('pr_number')!r} "
                    f"does not match live pr_number={live_pr_number!r}"
                ),
            }
        )
    if (
        state_summary.get("current_head")
        and live_pr_head
        and state_summary["current_head"] != live_pr_head
    ):
        blockers.append(
            {
                "kind": "CHECKPOINT_HEAD_MISMATCH",
                "detail": (
                    f"checkpoint current_head={state_summary['current_head']!r} "
                    f"does not match live PR head_sha={live_pr_head!r}"
                ),
            }
        )
    if (
        state_summary.get("branch")
        and live_branch
        and state_summary["branch"] != live_branch
    ):
        warnings.append(
            f"checkpoint branch={state_summary['branch']!r} does not match "
            f"live head_ref={live_branch!r}"
        )
    if (
        state_summary.get("last_verified_primary_head")
        and live_main_sha
        and state_summary["last_verified_primary_head"] != live_main_sha
    ):
        # Base/main drift is recorded as a warning rather than a
        # hard blocker, because a base update mid-PR is a normal
        # workflow event (rebase / merge of main into the branch).
        warnings.append(
            f"checkpoint last_verified_primary_head="
            f"{state_summary['last_verified_primary_head']!r} differs from "
            f"live primary HEAD={live_main_sha!r}"
        )
    # Normalize merge_state for comparison. fetch_pr_state returns
    # the canonical lowercase ``clean`` but tests may pass either
    # case. We compare on the normalized value.
    live_merge_state_norm = (
        str(live_merge_state).lower() if live_merge_state else ""
    )
    # Resume-observation drift detector (head-drift only). We only
    # call this when both the live PR head and the recorded
    # last_verified_pr_head are available.
    #
    # Codex finding 3455479198 (P2): the canonical
    # ``aed_lifecycle.checkpoint.validate_resume_observations``
    # produces TWO kinds of errors —
    # ``"PR head changed: ..."`` and
    # ``"primary worktree head changed: ..."``. Local
    # primary/main drift is a normal workflow event (rebase
    # of main into the feature branch, or a merge of main
    # into the PR) and must NOT produce a merge-blocking
    # checkpoint/live-state blocker when:
    #
    #   - PR head is exact (no PR head changed)
    #   - GitHub base/main evidence is current
    #   - checkpoint agrees with live PR state
    #   - all live gates are clean
    #
    # Split the canonical errors into PR-head blockers and
    # primary-head warnings. PR-head drift still blocks
    # (the PR head itself moved). Primary-head drift only
    # emits a warning so a stale local primary can't
    # falsely block a continuation plan whose live evidence
    # is otherwise clean.
    try:
        from aed_lifecycle.checkpoint import (  # type: ignore
            CheckpointState,
            validate_resume_observations,
        )
        # Rebuild a minimal CheckpointState for the validator — only
        # the head fields are needed for head-drift detection.
        from aed_lifecycle.checkpoint import CheckpointState as _CS  # noqa
        payload = envelope.get("raw") or {}
        state_for_obs = _checkpoint_state_from_payload(payload)
        if state_for_obs is not None:
            obs_errors = validate_resume_observations(
                state_for_obs,
                observed_pr_head=live_pr_head or "",
                observed_primary_head=live_main_sha or "",
            )
            for err in obs_errors:
                # The canonical validator prefixes the kind of
                # drift into the message string. We dispatch
                # on that prefix to keep the rest of the
                # diagnostic intact.
                if err.startswith("primary worktree head changed") or err.startswith(
                    "recorded primary head missing"
                ):
                    # Primary drift → warning, not blocker.
                    warnings.append(err)
                else:
                    # PR head changed / recorded PR head
                    # missing → still a blocker.
                    blockers.append(
                        {
                            "kind": "CHECKPOINT_OBSERVATION_DRIFT",
                            "detail": err,
                        }
                    )
    except ImportError:
        warnings.append(
            "aed_lifecycle.checkpoint.validate_resume_observations "
            "is unavailable; observation drift not checked"
        )
    except Exception as exc:  # pragma: no cover — defensive
        warnings.append(
            f"observation drift check raised {type(exc).__name__}: {exc}"
        )
    # Combination rules: when checkpoint says merge-ready but live
    # is blocked, that is a fail-closed blocker. When live is clean
    # but checkpoint says an earlier phase, that is a warning (live
    # trumps checkpoint for forward progress). The only checkpoint
    # terminal state that supports ``merge_ready_both_sides`` is
    # ``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION``. The completed
    # terminal state ``PR_MERGED_AND_CLOSED_OUT`` is EXCLUDED —
    # a closed-out checkpoint paired with a live OPEN PR is a
    # fail-closed cross-reference disagreement (handled below),
    # never a merge-ready signal.
    #
    # Codex finding 3455441244 (P2): ``PR_MERGED_AND_CLOSED_OUT``
    # was previously included here as a "safety net", but that
    # allowed a closed-out checkpoint to satisfy
    # ``merge_ready_both_sides`` and emit a merge authorization
    # preview. The canonical schema in
    # ``schemas/aed_lifecycle_states_v1.json:211-220`` marks
    # ``PR_MERGED_AND_CLOSED_OUT`` as ``merge_allowed=false``,
    # which is inconsistent with a merge-ready recommendation.
    # A closed-out checkpoint paired with a live OPEN PR must
    # surface as a distinct cross-reference blocker so the
    # operator can reconcile the divergence instead of being
    # silently told to merge.
    MERGE_READY_TERMINAL_STATES = {
        "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
    }
    terminal = state_summary.get("terminal_state")
    next_action = state_summary.get("next_action")
    if (
        terminal == "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION"
        and live_merge_state_norm != "clean"
    ):
        blockers.append(
            {
                "kind": "CHECKPOINT_LIVE_GATE_DISAGREEMENT",
                "detail": (
                    f"checkpoint terminal_state=MERGE_READY_AWAITING_HUMAN_AUTHORIZATION "
                    f"but live merge_state_status={live_merge_state!r}"
                ),
            }
        )
    # Codex finding 3455441244 (P2): a checkpoint marked as
    # ``PR_MERGED_AND_CLOSED_OUT`` is a closed-out checkpoint
    # (terminal, ``merge_allowed=false``). If the live PR is
    # still OPEN and not a draft, that is a cross-reference
    # disagreement — the checkpoint thinks the work is closed
    # out, but GitHub shows an OPEN PR. Surface it as a
    # distinct blocker so the operator can reconcile the
    # divergence. ``merge_ready_both_sides`` cannot be true
    # for this pairing under any circumstance.
    if terminal == "PR_MERGED_AND_CLOSED_OUT" and live_pr_state == "OPEN":
        blockers.append(
            {
                "kind": "CHECKPOINT_CLOSED_OUT_LIVE_OPEN",
                "detail": (
                    f"checkpoint terminal_state=PR_MERGED_AND_CLOSED_OUT "
                    f"(closed-out) but live PR is OPEN (state={live_pr_state!r}, "
                    f"is_draft={live_pr_draft!r}); reconciling requires operator action"
                ),
            }
        )
    if next_action == "pr_merge" and live_merge_state_norm != "clean":
        blockers.append(
            {
                "kind": "CHECKPOINT_NEXT_ACTION_UNSAFE",
                "detail": (
                    f"checkpoint next_action=pr_merge but live "
                    f"merge_state_status={live_merge_state!r}"
                ),
            }
        )
    if (
        state_summary.get("phase")
        and live_pr_state == "OPEN"
        and not live_pr_draft
        and live_merge_state_norm == "clean"
        and not str(state_summary["phase"]).startswith("PHASE_5")
        and not str(state_summary["phase"]).startswith("PHASE_4")
    ):
        warnings.append(
            f"live preflight is clean but checkpoint phase="
            f"{state_summary['phase']!r} suggests an earlier stage; "
            "treating live evidence as authoritative"
        )
    # Update envelope fields used by render_markdown and JSON consumers.
    envelope["cross_reference"]["status"] = (
        "clean" if not blockers else "disagreement"
    )
    envelope["cross_reference"]["blockers"] = list(blockers)
    envelope["cross_reference"]["warnings"] = list(warnings)
    envelope["combination"]["live_state_agrees"] = not blockers
    envelope["combination"]["blockers"] = list(blockers)
    envelope["combination"]["warnings"] = list(warnings)
    # merge_ready_both_sides is true only when the checkpoint is
    # loaded, structurally valid, cross-references cleanly with live
    # state, AND the checkpoint signals a merge-ready terminal state.
    live_clean = (
        live_pr_state == "OPEN"
        and not live_pr_draft
        and live_merge_state_norm == "clean"
    )
    envelope["combination"]["merge_ready_both_sides"] = (
        live_clean
        and not blockers
        and terminal in MERGE_READY_TERMINAL_STATES
    )
    return blockers, warnings


def assemble_plan(
    *,
    pr: Dict[str, Any],
    lifecycle: Dict[str, Any],
    checks: Dict[str, Any],
    gate: Dict[str, Any],
    codex: Dict[str, Any],
    branch_protection: Dict[str, Any],
    generated_at: str,
    checkpoint_envelope: Optional[Dict[str, Any]] = None,
    live_main_sha: Optional[str] = None,
) -> ContinuePlan:
    """Assemble the structured continuation plan from all signals.

    The ``checkpoint_envelope`` parameter is PR #407's optional
    checkpoint integration. When omitted (the default, PR #406
    behavior), the function produces a plan that is byte-equivalent
    to PR #406 for any fixed live input. When provided, the envelope
    is rendered into the plan and any cross-reference blockers are
    added to ``blockers_for_merge``.
    """
    blockers_for_merge: List[Dict[str, Any]] = []
    warnings: List[str] = []

    # PR #407: ingest checkpoint evidence if provided. The envelope
    # is the canonical structured form returned by
    # ``_load_checkpoint_payload`` + ``_validate_checkpoint_payload``
    # + ``_cross_reference_checkpoint``. When no checkpoint is
    # provided we use the minimal absent-envelope so the plan output
    # is stable and the markdown section is consistently rendered.
    if checkpoint_envelope is None:
        checkpoint_envelope = _absent_checkpoint_envelope()
    # If the envelope has not yet been cross-referenced (e.g., the
    # caller constructed it manually), do so now. The helper mutates
    # the envelope in place so downstream rendering sees the final
    # state.
    if checkpoint_envelope.get("cross_reference", {}).get("status") == "skipped" \
            and checkpoint_envelope.get("present") \
            and checkpoint_envelope.get("load_status") == "loaded":
        _cross_reference_checkpoint(
            checkpoint_envelope, pr, live_main_sha=live_main_sha
        )
    # Surface checkpoint load and validation errors as blockers. This
    # is the canonical "checkpoint evidence is unusable" code path.
    if checkpoint_envelope.get("present"):
        load_status = checkpoint_envelope.get("load_status")
        if load_status in {"file_missing", "malformed_json", "unreadable"}:
            blockers_for_merge.append(
                {
                    "kind": "CHECKPOINT_LOAD_FAILED",
                    "detail": (
                        f"checkpoint load failed: load_status={load_status!r}; "
                        f"errors={checkpoint_envelope.get('errors', [])}"
                    ),
                }
            )
        validation_status = (
            checkpoint_envelope.get("validation", {}).get("status")
        )
        if validation_status in {"invalid", "schema_invalid", "validator_raised", "validator_unavailable"}:
            blockers_for_merge.append(
                {
                    "kind": "CHECKPOINT_VALIDATION_INVALID",
                    "detail": (
                        f"checkpoint validation status={validation_status!r}; "
                        f"errors={checkpoint_envelope.get('validation', {}).get('errors', [])}"
                    ),
                }
            )
        # Cross-reference disagreements are fail-closed blockers.
        for blocker in (
            checkpoint_envelope.get("cross_reference", {}).get("blockers") or []
        ):
            blockers_for_merge.append(
                {"kind": blocker.get("kind", "CHECKPOINT_DISAGREEMENT"),
                 "detail": blocker.get("detail", "")}
            )
        # Cross-reference warnings surface as plan warnings (not blockers).
        for w in checkpoint_envelope.get("cross_reference", {}).get("warnings", []) or []:
            warnings.append(f"checkpoint cross-reference warning: {w}")

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
    # PR #406 Codex finding PRRT_kwDOSHFpYM6LSOC_: when
    # ``is_protected`` is None (API error), the planner must fail
    # closed on merge authorization. The original code treated None
    # the same as False (no protection), which is unsafe.
    if branch_protection.get("is_protected") is None:
        bp_err_kind = branch_protection.get("protection_error_kind") or "unknown"
        blockers_for_merge.append(
            {
                "kind": "BRANCH_PROTECTION_API_ERROR",
                "detail": (
                    f"branch protection API returned an error "
                    f"(kind={bp_err_kind!r}); cannot confirm required "
                    f"status checks or conversation resolution — fail closed"
                ),
            }
        )
        warnings.append(
            f"Branch protection API error (kind={bp_err_kind!r}): "
            f"{branch_protection.get('error') or 'unknown'}"
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
        checkpoint=checkpoint_envelope,
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

    # PR #407: Checkpoint section. Renders between Lifecycle state
    # and Checks per the design plan. When no checkpoint was provided
    # the envelope is the minimal absent form and the section renders
    # a single "Not provided" line so the markdown shape stays
    # consistent across invocations.
    lines.append("## Checkpoint")
    lines.append("")
    cp = plan.checkpoint or _absent_checkpoint_envelope()
    if not cp.get("present"):
        lines.append("- **Present:** no (use `--checkpoint-json <path>` to ingest)")
    else:
        lines.append(f"- **Present:** yes")
        lines.append(f"- **Path:** `{cp.get('path')}`")
        lines.append(f"- **Load status:** `{cp.get('load_status')}`")
        lines.append(f"- **Schema version:** {cp.get('schema_version')}")
        val = cp.get("validation", {})
        lines.append(f"- **Validation status:** `{val.get('status')}`")
        if val.get("errors"):
            for err in val["errors"]:
                lines.append(f"  - error: {err}")
        if val.get("warnings"):
            for w in val["warnings"]:
                lines.append(f"  - warning: {w}")
        xref = cp.get("cross_reference", {})
        lines.append(f"- **Cross-reference status:** `{xref.get('status')}`")
        if xref.get("blockers"):
            for blk in xref["blockers"]:
                lines.append(f"  - blocker: `{blk.get('kind')}`: {blk.get('detail')}")
        if xref.get("warnings"):
            for w in xref["warnings"]:
                lines.append(f"  - warning: {w}")
        combo = cp.get("combination", {})
        lines.append(f"- **Live/checkpoint agreement:** {combo.get('live_state_agrees')}")
        lines.append(f"- **merge_ready_both_sides:** {combo.get('merge_ready_both_sides')}")
        if combo.get("blockers"):
            for blk in combo["blockers"]:
                lines.append(f"  - blocker: `{blk.get('kind')}`: {blk.get('detail')}")
        if combo.get("warnings"):
            for w in combo["warnings"]:
                lines.append(f"  - warning: {w}")
        ss = val.get("state_summary") or {}
        if ss:
            lines.append(f"- **Recorded head:** `{ss.get('current_head')}`")
            lines.append(f"- **Recorded phase:** `{ss.get('phase')}`")
            lines.append(f"- **Recorded terminal_state:** `{ss.get('terminal_state')}`")
            lines.append(f"- **Recorded next_action:** `{ss.get('next_action')}`")
            lines.append(
                f"- **Recorded updated_at:** `{ss.get('updated_at')}`"
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
    if bp.get("is_protected") is None:
        # PR #406 Codex finding PRRT_kwDOSHFpYM6LSOC_: API error.
        lines.append(
            f"- **Base branch:** {bp.get('base_branch')} "
            f"(branch protection API ERROR: kind={bp.get('protection_error_kind')!r})"
        )
        if bp.get("error"):
            lines.append(f"  - error: {bp['error']}")
    elif not bp.get("is_protected"):
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
    parser.add_argument(
        "--checkpoint-json",
        type=Path,
        default=None,
        help=(
            "Optional path to an AED checkpoint snapshot (aed_lifecycle.checkpoint "
            "CheckpointState serialized as JSON). The file is read-only: this "
            "command never writes back to it. When provided, the plan gains a "
            "checkpoint envelope with load/validation/cross-reference status and "
            "any disagreement becomes a fail-closed blocker."
        ),
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
            # PR #406 Codex finding PRRT_kwDOSHFpYM6LSOC7: pass the
            # current head_sha so fetch_codex_verdict can apply
            # exact-head freshness checking.
            codex = fetch_codex_verdict(
                repo,
                pr_number,
                since_iso=args.last_known_codex_ts,
                head_sha=pr.get("head_sha"),
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

        # 7. Checkpoint ingestion (PR #407, optional). The file is
        # read-only; we never write back. When omitted the envelope
        # is the minimal absent form so the rest of the pipeline is
        # byte-equivalent to PR #406 for fixed live inputs.
        if args.checkpoint_json is not None:
            checkpoint_envelope = _load_checkpoint_payload(args.checkpoint_json)
            _validate_checkpoint_payload(checkpoint_envelope)
            _cross_reference_checkpoint(
                checkpoint_envelope,
                pr,
                live_main_sha=None,  # PR head only at this layer; primary fetch is future work
            )
        else:
            checkpoint_envelope = _absent_checkpoint_envelope()

        # 8. Assemble plan
        plan = assemble_plan(
            pr=pr,
            lifecycle=lifecycle,
            checks=checks,
            gate=gate,
            codex=codex,
            branch_protection=bp,
            generated_at=generated_at,
            checkpoint_envelope=checkpoint_envelope,
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
