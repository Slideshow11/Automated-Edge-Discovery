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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Round-42 fix: import the shared Codex task-summary
# predicate robustly. Round-41 imported it via
# ``from scripts.local.check_pr_review_comments import ...``
# but the documented live invocation
# (``python scripts/local/audit_codex_response_for_pr.py ...``
# or via ``aed_pr status``/``merge`` with ``sys.path[0]``
# pointing at ``scripts/local``) does NOT have the repository
# root on ``sys.path``, so the absolute import raised
# ``ModuleNotFoundError`` and the broad ``except Exception``
# silently set the predicate to ``None``. As a result the
# Round-41 task-summary exclusion never ran under the live
# CLI, and a Codex ``### Summary`` issue-comment after a
# clean pass could still downgrade the audit to
# ``HOLD_NEW_CODEX_THREAD``.
#
# The fix has three parts:
#  1. Compute the repository root from this file's location
#     (``scripts/local/`` → parent is the repo root) and add
#     it to ``sys.path`` BEFORE attempting the absolute
#     import. This makes the import succeed under both
#     invocation modes.
#  2. Try the absolute import first
#     (``scripts.local.check_pr_review_comments``); on
#     failure, fall back to a relative-style import via the
#     SCRIPT_DIR-on-sys.path mode (the way the audit is
#     invoked directly).
#  3. Fail closed but visibly: if both import attempts
#     fail, log a stderr warning so a missing module
#     surfaces in CI logs instead of silently disabling
#     task-summary filtering. The runtime check at the call
#     site still gates on ``is not None`` so behavior is
#     unchanged when the predicate truly is unavailable.
# Round-44 fix: compute the repository root correctly.
# Round-42 computed it with a single ``dirname`` of
# ``__file__``, but ``__file__`` is
# ``<repo>/scripts/local/audit_codex_response_for_pr.py``,
# so ``_SCRIPT_DIR_HERE = <repo>/scripts/local`` and
# ``_REPO_ROOT_HERE = dirname(_SCRIPT_DIR_HERE)`` =
# ``<repo>/scripts``. That means the absolute import
# ``from scripts.local.check_pr_review_comments import ...``
# still fails in script-local mode unless something else
# has already added the repository root to ``sys.path``.
#
# The repository root is TWO ``dirname`` calls up from
# ``__file__`` (``scripts/local/<file>.py`` → parent is
# ``scripts/local`` → parent is ``scripts`` → parent is
# the repo root). Walk up to the directory whose basename
# is ``scripts`` and use ITS parent. This is robust to
# both the canonical layout (where ``scripts/`` lives at
# the repo root) and any future nested-layout refactor.
import os as _os
import sys as _sys

# Round-412: delegate to the shared Codex classifier.
try:
    from scripts.local._shared_codex_classifier import (
        CODEX_CLEAN_PASS_PHRASES,
        CODEX_CLEAN_PASS_EXTRA_FRAGMENTS,
        CODEX_REVIEW_SUMMARY_PREFIX,
        CODEX_FINDING_BADGE_PREFIX,
        is_codex_clean_pass_comment as _shared_is_clean,
        is_codex_finding_body as _shared_is_finding,
        is_codex_review_summary as _shared_is_summary,
        extract_review_commit_oid as _shared_extract_oid,
        body_has_finding_badge as _shared_body_has_finding,
        # Round-412 (MINIMAX P2 Finding 1): route
        # ``has_active_blocker`` through the canonical
        # case-insensitive ``is_codex_login`` predicate
        # so a Codex-authored active thread whose
        # author field is not lowercased is still
        # recognized as a current-head finding. The
        # previous code used a case-sensitive ``in``
        # check against ``CODEX_BOT_LOGINS`` (the
        # audit's local frozenset), which silently
        # missed uppercase/mixed-case Codex logins.
        is_codex_login as _shared_is_codex_login,
    )
except ImportError:
    _shared_is_clean = None
    _shared_is_finding = None
    _shared_is_summary = None
    _shared_extract_oid = None
    _shared_body_has_finding = None
    _shared_is_codex_login = None

_SCRIPT_DIR_HERE = _os.path.dirname(_os.path.abspath(__file__))
# Walk up to find the parent of the ``scripts/`` directory.
# The repo root sits one level above ``scripts/`` in the
# canonical layout; walking up to the directory whose name
# is ``scripts`` and then taking its parent is layout-agnostic.
_scripts_dir = None
_candidate = _SCRIPT_DIR_HERE
while _candidate and _candidate != _os.path.dirname(_candidate):
    if _os.path.basename(_candidate) == "scripts":
        _scripts_dir = _candidate
        break
    _candidate = _os.path.dirname(_candidate)
if _scripts_dir is not None:
    _REPO_ROOT_HERE = _os.path.dirname(_scripts_dir)
else:
    # Fallback: the standard two-parent walk (covers
    # ``<repo>/scripts/local/<file>.py`` layouts).
    _REPO_ROOT_HERE = _os.path.dirname(_os.path.dirname(_SCRIPT_DIR_HERE))
if _REPO_ROOT_HERE not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT_HERE)
if _SCRIPT_DIR_HERE not in _sys.path:
    _sys.path.insert(0, _SCRIPT_DIR_HERE)
_co_is_codex_task_summary_issue_comment = None
try:
    from scripts.local.check_pr_review_comments import (
        _is_codex_task_summary_issue_comment as
        _co_is_codex_task_summary_issue_comment,
    )
except Exception as _co_abs_exc:
    # Fallback: when this module is run directly
    # (``python scripts/local/audit_codex_response_for_pr.py``)
    # ``scripts.local`` is not importable because the repo
    # root is not on sys.path; try a top-level import that
    # works when ``scripts/local`` itself is on sys.path.
    try:
        from check_pr_review_comments import (  # type: ignore[import-not-found]
            _is_codex_task_summary_issue_comment as
            _co_is_codex_task_summary_issue_comment,
        )
    except Exception as _co_fallback_exc:
        import warnings as _warnings
        _warnings.warn(
            "audit_codex_response_for_pr: could not import "
            "_is_codex_task_summary_issue_comment from "
            "check_pr_review_comments; task-summary "
            "filtering will be disabled. "
            f"abs_exc={type(_co_abs_exc).__name__}:{_co_abs_exc}; "
            f"fallback_exc={type(_co_fallback_exc).__name__}:{_co_fallback_exc}",
            RuntimeWarning,
            stacklevel=2,
        )


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

# Precomputed normalized (lowercase) fallback login set. Used by
# ``_local_codex_login_fallback`` when the canonical shared
# ``is_codex_login`` predicate is unavailable. Precomputed once
# at module load so the generator inside ``has_active_blocker``
# does not rebuild the set for every thread.
_LOCAL_CODEX_LOGINS_LOWER: frozenset = frozenset(
    a.lower() for a in CODEX_BOT_LOGINS
)


def _local_codex_login_fallback(login: Any) -> bool:
    """Local fallback Codex-login predicate.

    Round-412 (FINAL direct-CLI micro-repair): the canonical
    ``is_codex_login`` predicate from ``_shared_codex_classifier``
    requires a non-empty string and normalizes with ``.lower()``.
    The previous fallback in ``has_active_blocker`` was
    ``(t.get("author", "") or "").lower() in {a.lower() for a in CODEX_BOT_LOGINS}``
    which raises ``AttributeError`` when ``author`` is a truthy
    non-string (e.g., an integer from a malformed GraphQL
    response). This fallback matches the canonical predicate's
    type-safety and case-insensitive identity semantics: it
    returns False for any non-string value (None, int, list,
    dict, empty string) and otherwise compares
    ``login.lower()`` against the precomputed normalized
    ``_LOCAL_CODEX_LOGINS_LOWER`` set. Never raises.

    The canonical predicate is still preferred when available
    (single source of truth); this fallback is only used when
    the shared classifier import fails.
    """
    if not isinstance(login, str) or not login:
        return False
    return login.lower() in _LOCAL_CODEX_LOGINS_LOWER


# Exact phrase Codex uses to denote a clean pass in issue-level comments.
CODEX_CLEAN_PASS_PHRASE = "Codex Review: Didn\u2019t find any major issues"
# Accept both curly and straight apostrophes
CODEX_CLEAN_PASS_PHRASE_ALT = "Codex Review: Didn't find any major issues"
CODEX_CLEAN_PASS_PHRASES = (CODEX_CLEAN_PASS_PHRASE, CODEX_CLEAN_PASS_PHRASE_ALT)

# Round-64: additional clean-pass fragments accepted by the
# poller (``scripts/local/codex_review_poller.py``). The audit
# MUST accept the same fragments as the poller, or a clean
# response that uses the newer summary format (e.g. "No
# findings reported") will be incorrectly classified as a
# newer finding and downgrade a valid clean pass to
# HOLD_NEW_CODEX_THREAD. This mirrors the poller's
# ``CLEAN_PASS_FRAGMENTS`` vocabulary.
CODEX_CLEAN_PASS_EXTRA_FRAGMENTS = (
    "no findings reported",
    "no issues found",
    "all clear",
    "looks good to me",
    "no blocking findings",
    "no major issues",
)

# Round-52: Codex's newer formal-review summaries start with
# the ``### 💡 Codex Review`` Markdown header. A review with
# this prefix and NO inline review comments is a clean
# pass; the findings live in inline comments. The audit
# must recognize this format the same way the poller does
# (Round-47..51 poller fix), or the readiness verifier will
# keep reporting missing/failed Codex evidence even after
# the poller has confirmed a clean exact-head response.
CODEX_REVIEW_SUMMARY_PREFIX = "### \U0001f4a1 Codex Review"
CODEX_FINDING_BADGE_PREFIX = "**<sub><sub>"


def is_codex_review_summary(body: str) -> bool:
    """Return True if the body is a Codex formal-review
    summary (Round-52).

    Codex's newer automated review summaries start with
    the ``### \U0001f4a1 Codex Review`` Markdown header.
    A review with this prefix carries inline review
    comments for the actual findings; the summary body
    itself is not a finding.

    PHASE 3 (PR #412): delegates to the shared module so
    the audit, poller, gate, and controller share one
    canonical predicate.
    """
    if _shared_is_summary is not None:
        return _shared_is_summary(body)
    # Fail-closed default if the shared module could not be
    # imported at startup.
    if not body:
        return False
    return body.lstrip().startswith(CODEX_REVIEW_SUMMARY_PREFIX)


def is_codex_finding_body(body: str) -> bool:
    """Return True if the body looks like a Codex
    inline review comment carrying a finding badge
    (Round-52). Used to classify the summary-format
    review body's inline comments.

    PHASE 3 (PR #412): delegates to the shared module.
    """
    if _shared_is_finding is not None:
        return _shared_is_finding(body)
    if not body:
        return False
    return body.lstrip().startswith(CODEX_FINDING_BADGE_PREFIX)

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

    gh api --paginate prints each page as a separate JSON document; if the
    caller does not pass --slurp, the concatenated stdout is NOT valid JSON
    for multi-page responses. We always pass --slurp so the output is a
    single JSON array of arrays (one entry per page) that we then flatten
    in flatten_paginated_items().

    This fixes the live Codex finding that gh api --paginate on issue
    comments and review submissions can return multiple top-level JSON
    documents which would otherwise break json.loads().
    """
    cmd = ["gh", "api", f"repos/{repo}/{endpoint}", "--paginate", "--slurp"]
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
    except json.JSONDecodeError as exc:
        return False, [], f"invalid JSON from gh api: {exc}"
    flat, ok_pages = flatten_paginated_items(data)
    if not ok_pages:
        return False, [], f"unexpected gh api --paginate payload shape: {type(data).__name__}"
    return True, flat, ""


def flatten_paginated_items(payload: Any) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Flatten a gh api --paginate --slurp payload into a single list of items.


    The slurped output is a JSON array where each element is one page. Each
    page is itself a JSON array of items. Older REST endpoints sometimes
    wrap items in an object ({"items": [...]}); we unwrap that case too.
    For fixtures or partial responses we accept:
      - already-flat list of items -> returned as-is
      - list of pages [[...], [...]] -> flattened
      - list of wrappers [{items: [...]}, ...] -> flattened and joined

    Returns (flat_list, shape_ok). shape_ok is False if the payload is
    structurally invalid (e.g. None, dict at top level).
    """
    if payload is None:
        return [], False
    if isinstance(payload, dict):
        return [], False
    if not isinstance(payload, list):
        return [], False
    flat: List[Dict[str, Any]] = []
    for page in payload:
        if page is None:
            continue
        if isinstance(page, list):
            for item in page:
                if isinstance(item, dict):
                    flat.append(item)
        elif isinstance(page, dict):
            # Could be {items: [...]} or a single-item object
            if "items" in page and isinstance(page["items"], list):
                for item in page["items"]:
                    if isinstance(item, dict):
                        flat.append(item)
            else:
                flat.append(page)
        else:
            # Unexpected scalar; skip
            continue
    return flat, True


def normalize_rest_pr_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a canonical classifier packet from a raw REST
    `Get a pull request` payload (the live response from
    `gh api repos/{owner}/{repo}/pulls/{n}`).

    Real REST fields used (as documented by GitHub):
      - state (lowercase "open" | "closed"; "MERGED" is detected via merged=true)
      - merged (bool)
      - merged_at (string | null)
      - head.sha, head.ref
      - base.ref
      - draft (bool)
      - mergeable (bool | null; null while GitHub is computing)
      - mergeable_state (lowercase "clean" | "blocked" | "dirty" | "unstable" | null)
      - html_url
      - title

    REST does NOT expose:
      - mergeStateStatus (GraphQL-only)
      - reviewDecision (GraphQL-only)

    The returned packet exposes the canonical fields the classifier
    reads (`state`, `sha`, `url`, `baseRefName`, `headRefName`,
    `mergeStateStatus`, `merge_state_status`, `mergeableState`,
    `mergeable_state`, `mergeable`, `reviewDecision`,
    `review_decision`, `title`, `merged`, `merged_at`, `draft`).
    `mergeStateStatus` and `reviewDecision` are explicitly set to
    None because REST never exposes them; downstream code MUST NOT
    treat REST's absence of these fields as a blocker.
    """
    if not isinstance(raw, dict):
        return {}
    head_obj = raw.get("head")
    head: Dict[str, Any] = head_obj if isinstance(head_obj, dict) else {}
    base_obj = raw.get("base")
    base: Dict[str, Any] = base_obj if isinstance(base_obj, dict) else {}
    mergeable_state_raw = raw.get("mergeable_state")
    mergeable_state_str = (
        mergeable_state_raw if isinstance(mergeable_state_raw, str) else None
    )
    mergeable_raw = raw.get("mergeable")
    if not isinstance(mergeable_raw, (bool, type(None))):
        # Some GitHub responses serialize as string "true"/"false".
        if isinstance(mergeable_raw, str):
            lowered = mergeable_raw.strip().lower()
            if lowered == "true":
                mergeable_raw = True
            elif lowered == "false":
                mergeable_raw = False
            else:
                mergeable_raw = None
        else:
            mergeable_raw = None
    return {
        # Canonical scalar fields the classifier reads directly.
        "sha": (head.get("sha") or "") if head.get("sha") else "",
        "state": (raw.get("state") or "") if raw.get("state") else "",
        "merged": bool(raw.get("merged", False)),
        "merged_at": raw.get("merged_at") if "merged_at" in raw else None,
        "title": (raw.get("title") or "") if raw.get("title") else "",
        "draft": bool(raw.get("draft", False)) if isinstance(raw.get("draft"), bool) else False,
        # Merge readiness. REST exposes `mergeable_state` (lowercase);
        # we also expose both snake_case and camelCase alias keys so the
        # existing classifier normalization order works unchanged.
        "mergeableState": mergeable_state_str,  # camelCase alias for fixtures
        "mergeable_state": mergeable_state_str,  # canonical snake_case
        "mergeable": mergeable_raw,
        # GraphQL-only fields are absent in REST; expose as None so the
        # classifier cannot accidentally read a stale fixture value.
        "mergeStateStatus": None,  # GraphQL PullRequest.mergeStateStatus (REST lacks)
        "merge_state_status": None,  # GraphQL-style jq path (REST lacks)
        "reviewDecision": None,  # GraphQL PullRequest.reviewDecision (REST lacks)
        "review_decision": None,  # GraphQL-style jq path (REST lacks)
        # Refs and URL.
        "baseRefName": (base.get("ref") or "") if base.get("ref") else "",
        "headRefName": (head.get("ref") or "") if head.get("ref") else "",
        "url": (raw.get("html_url") or "") if raw.get("html_url") else "",
    }


def gh_pr_view_min(repo: str, pr_number: int) -> Tuple[bool, Dict[str, Any], str]:
    """
    Fetch PR metadata needed for head/mergeState/reviewDecision checks.
    Uses the REST `Get a pull request` endpoint
    (`repos/{owner}/{repo}/pulls/{n}`) and parses the raw JSON in
    Python. Does NOT use `--jq` field-name translation; that path was
    fragile because real REST payloads do not expose
    `merge_state_status` or `review_decision` (both are GraphQL-only).
    """
    cmd = ["gh", "api", f"repos/{repo}/pulls/{pr_number}"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, check=False,
        )
    except OSError as exc:
        return False, {}, f"gh api invocation failed: {exc}"
    if result.returncode != 0:
        return False, {}, f"gh api returned {result.returncode}: {result.stderr[:300]}"
    if not result.stdout.strip():
        return False, {}, "gh api returned empty stdout"
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return False, {}, f"gh api returned invalid JSON: {exc}"
    if not isinstance(raw, dict):
        return False, {}, f"gh api returned non-object: {type(raw).__name__}"
    # Shape detection: raw REST payloads have a `head` dict (and a
    # `base` dict). Canonical/fixture packets have top-level scalar
    # fields like `sha`, `state`, `mergeStateStatus` and no nested
    # `head` object. Normalize raw REST, pass canonical through so
    # existing GraphQL-style fixtures keep working.
    if isinstance(raw.get("head"), dict):
        return True, normalize_rest_pr_payload(raw), ""
    return True, raw, ""


def gh_graphql_review_threads(
    repo: str, pr_number: int, timeout: int = 30
) -> Tuple[bool, List[Dict[str, Any]], str, Dict[str, Any]]:
    """
    Fetch PR review-thread resolution state via GraphQL with --paginate
    on reviewThreads (100 per page). Each returned entry has:
      {thread_id, is_resolved, is_outdated, comment_database_id, comment_url, author, body, path, line}

    Returns (ok, threads, error_msg, metadata). `ok=False` means the
    inventory is incomplete or unavailable: GraphQL command failed,
    response had `errors`, the response was missing expected
    `reviewThreads` data, top-level `hasNextPage=true` and the
    implementation did not paginate further, ANY thread's nested
    `comments.pageInfo.hasNextPage=true` (Codex comment may be
    on a later page), JSON parsing failed, or the page node list
    was not a list. Callers MUST treat `ok=False` as a fail-closed
    signal: the thread list may be empty, partial, or stale, and
    merge readiness cannot be trusted until inventory is complete.

    The metadata dict exposes the nested-comment inventory state
    so the caller can surface it in the JSON packet / markdown
    report:
      - review_thread_comment_inventory_complete: bool
      - review_thread_comment_inventory_error_count: int
      - review_thread_comment_incomplete_thread_ids: list[str]
        (only populated when inventory is incomplete)
    """
    owner, name = repo.split("/", 1)
    # Note: the nested `comments(first:50)` connection on each
    # review thread ALSO has its own `pageInfo { hasNextPage
    # endCursor }`. We must request it explicitly: a thread
    # with more than 50 comments could have its Codex-authored
    # finding on a later page, and treating the inventory as
    # complete on the strength of only the top-level
    # `reviewThreads.pageInfo` is unsafe. The query therefore
    # requests `pageInfo { hasNextPage }` on the nested
    # connection, and the parser below checks every thread's
    # nested pageInfo. If ANY thread has incomplete nested
    # comments, the function returns ok=False and the caller
    # fails closed.
    query_parts = [
        "query {",
        f'repository(owner:"{owner}", name:"{name}") {{',
        f"pullRequest(number:{pr_number}) {{",
        "reviewThreads(first:100) {",
        "pageInfo { hasNextPage endCursor }",
        "nodes {",
        # Round-69 Codex review 4769344844 (P1): add
        # whitespace between ``isOutdated`` and
        # ``comments`` so the rendered query is
        # ``... isOutdated comments(first:50) ...``
        # instead of ``... isOutdatedcomments(first:50) ...``
        # (the latter is a single nonexistent field that
        # causes GitHub to return a GraphQL error on every
        # live run).
        "id isResolved isOutdated ",
        "comments(first:50) {",
        "pageInfo { hasNextPage endCursor }",
        # ``originalCommit`` is the commit the comment was posted
        # against (the review-thread anchor). Round-4 fix #2 requires
        # the thread's eligibility check to be tied to this
        # canonical SHA so a finding can be auto-resolved only when
        # there is evidence that a later commit addressed it.
        "nodes { databaseId url body path line "
        "originalCommit { oid } "
        "author { login } }",
        "}",
        "}",
        "}",
        "}",
        "}",
        "}",
    ]
    query_literal = " ".join(query_parts)
    cmd = ["gh", "api", "graphql", "--raw-field", f"query={query_literal}"]
    # Default empty metadata for early-return failure paths.
    # These paths do not have a parsed response so the nested
    # comment inventory state is conservatively marked
    # incomplete; the per-poll raw reset clears the flag at
    # the start of the next poll.
    empty_metadata: Dict[str, Any] = {
        "review_thread_comment_inventory_complete": False,
        "review_thread_comment_inventory_error_count": 0,
        "review_thread_comment_incomplete_thread_ids": [],
        # Round-74 PHASE 3: every genuine fetch, parse, GraphQL,
        # schema, cursor, or safety-cap failure path must set
        # outer_page_fetch_succeeded=False so the parent walker
        # can distinguish a real outer error from a
        # successfully-fetched terminal outer page with
        # pending nested work.
        "outer_page_fetch_succeeded": False,
        "outer_page_terminal": False,
        "outer_page_has_next": False,
        "current_page_nested_pending_ids": [],
    }
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except OSError as exc:
        return False, [], f"gh graphql invocation failed: {exc}", dict(empty_metadata)
    if result.returncode != 0:
        return False, [], f"gh graphql returned {result.returncode}: {result.stderr[:500]}", dict(empty_metadata)
    if not result.stdout.strip():
        return False, [], "gh graphql returned empty stdout", dict(empty_metadata)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return False, [], f"invalid GraphQL response: {exc}", dict(empty_metadata)
    if not isinstance(data, dict):
        return False, [], f"GraphQL response is not a JSON object: {type(data).__name__}", dict(empty_metadata)
    errors = data.get("errors")
    if errors:
        return False, [], f"GraphQL errors: {errors}", dict(empty_metadata)
    data_obj = data.get("data")
    if not isinstance(data_obj, dict):
        return False, [], "GraphQL response missing data object", dict(empty_metadata)
    repository = data_obj.get("repository")
    if not isinstance(repository, dict):
        return False, [], "GraphQL response missing repository", dict(empty_metadata)
    pr_data = repository.get("pullRequest")
    if not isinstance(pr_data, dict):
        return False, [], "GraphQL response missing pullRequest", dict(empty_metadata)
    threads_container = pr_data.get("reviewThreads")
    if not isinstance(threads_container, dict):
        return False, [], "GraphQL response missing reviewThreads container", dict(empty_metadata)
    page_info = threads_container.get("pageInfo")
    if not isinstance(page_info, dict):
        return False, [], "GraphQL reviewThreads.pageInfo is not a dict", dict(empty_metadata)
    nodes = threads_container.get("nodes")
    if not isinstance(nodes, list):
        return False, [], (
            f"GraphQL reviewThreads.nodes is not a list: "
            f"{type(nodes).__name__}"
        ), dict(empty_metadata)
    # Parse all nodes from this page. When the response is
    # paginated (hasNextPage=true) we still parse the visible
    # threads so the caller can detect findings already on this
    # page. The caller MUST treat ok=False as incomplete
    # inventory and refuse to emit merge-ready states.
    #
    # While parsing, also track each thread's nested
    # `comments.pageInfo.hasNextPage` so we can detect
    # incomplete nested comment pagination. A thread with
    # `comments.pageInfo.hasNextPage=true` means a Codex
    # finding could live on a later comments page; the
    # inventory for that thread is incomplete and the
    # classifier must fail closed rather than trust the
    # first page of comments.
    threads: List[Dict[str, Any]] = []
    incomplete_nested_threads: List[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        thread_id = node.get("id", "")
        is_resolved = bool(node.get("isResolved", False))
        is_outdated = bool(node.get("isOutdated", False))
        comments_obj = node.get("comments") or {}
        # Check the nested comments pageInfo. If the nested
        # connection is paginated (hasNextPage=true) the
        # thread's comment inventory is incomplete. Record
        # the thread id so the caller can surface it in the
        # api_errors message and the markdown report.
        nested_page_info = comments_obj.get("pageInfo") or {}
        if not isinstance(nested_page_info, dict):
            nested_page_info = {}
        nested_incomplete = bool(nested_page_info.get("hasNextPage"))
        if nested_incomplete and thread_id:
            incomplete_nested_threads.append(thread_id)
        # Visibility rule: a thread with `comments.pageInfo.hasNextPage=true`
        # is incomplete (a later page may hold additional findings), but
        # the comments returned on the FIRST page are authoritative
        # evidence the classifier has already seen. We MUST surface
        # those visible comments so a confirmed Codex-authored current-head
        # finding on the visible page can drive
        # HOLD_NEW_CODEX_THREAD (not HOLD_CODEX_RESPONSE_PENDING). The
        # thread is flagged with `nested_incomplete=True` so the
        # markdown report and downstream consumers can see which
        # findings came from partial nested evidence. The unified
        # inventory gate in section 8 still treats the overall
        # thread-inventory as incomplete (the function returns
        # `ok=False` below) and refuses to emit clean-pass /
        # merge-ready states — incomplete nested comments are
        # never trusted as a complete inventory.
        # Round-4 follow-up (Codex review 4724091490 on ``a8ccd9b``):
        # Build a per-thread participant list by iterating
        # ``comments.nodes`` and collecting each comment's author
        # login + databaseId. The participant list is then
        # attached to every entry that shares the same ``thread_id``
        # so the eligibility check can verify "every reply in the
        # thread is bot-authored" rather than looking at a single
        # comment in isolation. Without this aggregation a human
        # reply inside the same review thread would not be detected
        # and ``--resolve-eligible-bot-threads`` would resolve a
        # thread with human participation.
        thread_participants: Dict[str, List[Dict[str, Any]]] = {}
        for comment in (comments_obj.get("nodes") or []):
            if not isinstance(comment, dict):
                continue
            entry = thread_participants.setdefault(thread_id, [])
            entry.append({
                "author": (
                    (comment.get("author") or {}).get("login", "")
                    if isinstance(comment.get("author"), dict)
                    else ""
                ),
                "database_id": comment.get("databaseId"),
            })
        for comment in (comments_obj.get("nodes") or []):
            if not isinstance(comment, dict):
                continue
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
                # Per-thread participant list (every comment in the
                # thread, including replies). The eligibility check
                # iterates this list to verify no human authored
                # any reply.
                "comments": thread_participants.get(thread_id, []),
                # Round-4 fix #2: canonical commit SHA the comment
                # was posted against. Surfaced so the eligibility
                # check can verify a later commit addressed the
                # finding. The ``originalCommit`` GraphQL field
                # returns ``{oid}`` which is the canonical commit
                # SHA. When ``originalCommit`` is null/absent the
                # thread is flagged with ``original_commit_sha=null``
                # so the controller's normalizer can record
                # ``missing_commit_anchor``.
                "original_commit_sha": (
                    (comment.get("originalCommit") or {}).get("oid")
                    if isinstance(comment.get("originalCommit"), dict)
                    else None
                ),
                # True iff the parent thread's nested
                # `comments.pageInfo.hasNextPage=true`. The
                # markdown renderer surfaces this flag so
                # operators can see which findings came
                # from partial nested evidence; the
                # decision logic does NOT use this flag
                # directly — it relies on the existing
                # inventory-completeness gate in section 8
                # to refuse clean-pass / merge-ready
                # states when any thread is flagged.
                "nested_incomplete": nested_incomplete,
            })
    # Build the metadata dict for this call.
    metadata: Dict[str, Any] = {
        "review_thread_comment_inventory_complete": (
            len(incomplete_nested_threads) == 0
        ),
        "review_thread_comment_inventory_error_count": (
            len(incomplete_nested_threads)
        ),
        "review_thread_comment_incomplete_thread_ids": list(
            incomplete_nested_threads
        ),
    }
    if incomplete_nested_threads:
        # Fail closed: at least one thread has incomplete
        # nested comments, so the thread inventory is
        # incomplete. We return the (possibly empty) threads
        # list so the caller can still surface any findings
        # that ARE visible, but ok=False forces the unified
        # inventory gate in section 8 to refuse to emit
        # merge-ready / clean-pass states.
        sample = incomplete_nested_threads[:3]
        return False, threads, (
            "review-thread comments pagination required "
            f"(hasNextPage=true on nested comments for "
            f"{len(incomplete_nested_threads)} thread(s): "
            f"{', '.join(sample)}); classifier cannot trust "
            "a clean-pass / merge-ready decision from partial "
            "thread-comment evidence."
        ), metadata
    if page_info.get("hasNextPage"):
        # Fail closed: top-level pagination is required for
        # exhaustive inventory. Return the partial list
        # (visible page only) so the caller can still
        # surface findings already on this page. The caller
        # MUST treat ok=False as incomplete inventory and
        # refuse to emit merge-ready states.
        return False, threads, (
            "reviewThreads pagination required (hasNextPage=true); "
            "this classifier does not yet paginate review threads."
        ), metadata
    return True, threads, "", metadata


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
    """Return True if the body contains the Codex clean-pass phrase.

    Round-64: also accept the summary-format clean pass and
    the additional clean-pass fragments accepted by the
    poller (``scripts/local/codex_review_poller.py``). Without
    this, a clean response that uses the newer summary
    format (e.g. "No findings reported") would be
    incorrectly classified as a newer finding by the
    audit's post-clean-pass scan, downgrading a valid
    clean pass to HOLD_NEW_CODEX_THREAD. The audit and
    the poller MUST agree on what counts as clean.

    Round-65 fix: a summary-format body that contains a
    finding badge MUST NOT be classified as a clean pass
    even if it also contains a clean fragment. Without
    this guard, the fragment match below would override
    the summary-with-finding-badge detection and falsely
    emit ``is_clean_pass=True`` for a response that
    actually carries a finding. The same rule applies to
    any body that contains a finding badge line.

    PHASE 3 (PR #412): delegates to the shared module so
    the audit, poller, gate, and controller share one
    canonical predicate. The shared module already
    enforces the Round-64/65 invariants.
    """
    if _shared_is_clean is not None:
        return _shared_is_clean(body)
    # Fail-closed default if the shared module could not be
    # imported at startup.
    if not body:
        return False
    if any(
        is_codex_finding_body(line)
        for line in body.splitlines()
    ):
        return False
    if is_codex_review_summary(body):
        return True
    if any(phrase in body for phrase in CODEX_CLEAN_PASS_PHRASES):
        return True
    lower = body.lower()
    if any(frag in lower for frag in CODEX_CLEAN_PASS_EXTRA_FRAGMENTS):
        return True
    return False


def normalize_merge_state(value: Any) -> Optional[str]:
    """
    Normalize a merge-state field from any of the supported GitHub API
    shapes into the canonical uppercase form used in the classifier
    packet.

    Recognized input forms:
      - GraphQL PullRequest.mergeStateStatus: CLEAN | BLOCKED | DIRTY |
        UNSTABLE | BEHIND | DRAFT | UNKNOWN
      - REST Pulls.mergeable_state (lowercase): clean | blocked | dirty |
        unstable | behind | draft | null (unset while computing)
      - REST Pulls.mergeable (boolean-as-string): "true" | "false"
      - GraphQL-style snake_case key from JSON jq filter: merge_state_status

    Returns the canonical uppercase form (e.g. "CLEAN", "BLOCKED",
    "DIRTY", "UNSTABLE", "BEHIND", "DRAFT", "UNKNOWN") or None when the
    value is missing/empty/unrecognized.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "CLEAN" if value else "BLOCKED"
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    upper = s.upper()
    if upper in ("CLEAN", "BLOCKED", "DIRTY", "UNSTABLE", "BEHIND", "DRAFT", "UNKNOWN"):
        return upper
    # Lowercase or title-case from REST
    if s.lower() in ("clean", "blocked", "dirty", "unstable", "behind", "draft"):
        return s.upper()
    return None


def timestamp_field(item: Dict[str, Any], *candidates: str) -> str:
    """
    Return the first non-empty timestamp from a dict under any of the
    candidate keys. Supports BOTH GraphQL camelCase (createdAt,
    submittedAt) and REST snake_case (created_at, submitted_at) shapes.

    Returns "" if none of the candidates are present or all are empty.
    Used for comparing --ping-created-at against issue-comment /
    formal-review timestamps from either API surface.
    """
    if not isinstance(item, dict):
        return ""
    for key in candidates:
        v = item.get(key)
        if isinstance(v, str) and v:
            return v
    return ""


def extract_review_commit_oid(review: Dict[str, Any]) -> str:
    """Extract the commit OID from a review submission dict."""
    return (
        (review.get("commit_id") or "")
        or ((review.get("commit") or {}).get("oid") if isinstance(review.get("commit"), dict) else "")
        or ""
    )


# ---------------------------------------------------------------------------
# Round-60: fetch inline review comments
# ---------------------------------------------------------------------------


def _fetch_review_inline_comments_with_pr(
    repo: str, pr_number: int, review_id: Any,
    timeout: int = 30,
) -> Tuple[bool, List[Dict[str, Any]], str]:
    """Round-60 fix: fetch the inline review comments
    for a specific review on a specific PR. The
    summary-format review body never carries inline
    finding markers (those live in separate inline
    review comments), so the audit MUST fetch this
    surface before accepting a summary review as
    clean. If any inline comments exist, the review
    carries a finding.

    Returns ``(ok, comments, error_msg)``. On any
    failure (network, auth, parse, unexpected shape),
    returns ``(False, [], error)`` so the caller can
    fail closed — the review cannot be proven clean
    without the inline surface.
    """
    endpoint = (
        f"pulls/{pr_number}/reviews/{review_id}/"
        f"comments?per_page=100"
    )
    return gh_api_paginated(repo, endpoint, timeout=timeout)


# ---------------------------------------------------------------------------
# Main classification pipeline
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Canonical shared-pagination wrapper for the audit.
# Round-69 Codex review 4764653534 (P2): the inline
# ``gh_graphql_review_threads`` was retained below the
# ``__main__`` guard, so direct CLI runs exited before this
# wrapper was defined. Move it above ``classify`` and wire
# ``classify`` to use the shared paginator instead of the
# inline first-page-only GraphQL query.
# ---------------------------------------------------------------------------

def _follow_nested_cursor_for_threads(thread_nodes: list, *, safety_cap: int, timeout: int) -> dict:
    """Round-70 PHASE 3-P2: follow nested ``comments(after: <cursor>)`` for every
    thread whose initial comments(first:50) returned
    ``pageInfo.hasNextPage=true``. Aggregates all nested comments by
    stable ``databaseId`` and attaches them to the thread node's
    ``comments`` list under the original key the eligibility/packet
    builders expect (Round-69 ``comments`` key).

    Returns::

      {
        "complete": bool,
        "pages": int,
        "capped": bool,
        "error": Optional[str],
        "fetched_comments_by_thread_id": Dict[str, list],
      }

    Fail closed on:
      - missing or non-importable helper;
      - any thread fetch returning ok=False;
      - per-thread safety cap exceeded while following nested cursors;
      - aggregate pages exceeding the operator cap across all threads;
      - paginate_nested_comments returning complete=False.

    Round-106 follow-up (VUIvY / PRRT_kwDOSHFpYM6VUIvY): the
    previous implementation passed the full ``safety_cap`` to
    each per-thread call and only accumulated ``pages_total``
    without checking it. 31 threads with one additional page
    each returned ``complete=True`` while carrying
    ``thread_count × safety_cap`` comments. The fix enforces a
    separate AGGREGATE pages bound equal to ``safety_cap`` so
    the audit's runtime and memory bound cannot be defeated by
    splitting the inventory across many threads. When the
    aggregate ``pages_total`` crosses the cap, the helper
    breaks early and returns ``capped=True, complete=False``.
    """
    try:
        from scripts.local._shared_pagination import paginate_nested_comments
    except Exception:
        return {
            "complete": False,
            "pages": 0,
            "capped": False,
            "error": "paginate_nested_comments_unavailable",
            "fetched_comments_by_thread_id": {},
        }
    fetched: dict = {}
    pages_total = 0
    for tn in thread_nodes:
        if not isinstance(tn, dict):
            continue
        tid = tn.get("id", "")
        if not tid:
            continue
        # Find the nested cursor from the first-page comments.pageInfo.endCursor.
        comments_field = tn.get("comments") or {}
        if not isinstance(comments_field, dict):
            continue
        page_info = (comments_field.get("pageInfo") or {})
        if not isinstance(page_info, dict):
            page_info = {}
        nested_cursor = page_info.get("endCursor") or ""
        nested_has_next = bool(page_info.get("hasNextPage"))
        if not nested_has_next:
            continue  # nothing to follow
        if not nested_cursor:
            # hasNextPage=true without endCursor is a fail-closed
            # condition per the contract.
            return {
                "complete": False,
                "pages": pages_total,
                "capped": False,
                "error": (
                    f"hasNextPage_without_endCursor: thread={tid}"
                ),
                "fetched_comments_by_thread_id": fetched,
            }
        result = paginate_nested_comments(
            tid,
            page_size=100,
            safety_cap=safety_cap,
            timeout=timeout,
            initial_cursor=nested_cursor,
        )
        pages_total += int(result.get("pages", 0) or 0)
        # Round-107 follow-up (VUQ6I): the previous ``>=``
        # comparison reported the inventory as capped when
        # the aggregate pages count exactly equaled
        # ``safety_cap``. By definition, ``thread_count``
        # successful one-page threads consuming exactly
        # ``safety_cap`` total pages IS a complete bounded
        # inventory. The fix uses strict ``>``: a 31st
        # page triggers ``aggregate_pages_cap_exceeded``,
        # while 30 one-page results with cap=30 stay complete.
        if pages_total > safety_cap:
            return {
                "complete": False,
                "pages": pages_total,
                "capped": True,
                "error": "aggregate_pages_cap_exceeded",
                "fetched_comments_by_thread_id": fetched,
            }
        if not result.get("complete"):
            return {
                "complete": False,
                "pages": pages_total,
                "capped": bool(result.get("capped")),
                "error": result.get("error") or "nested_pagination_failed",
                "fetched_comments_by_thread_id": fetched,
            }
        fetched[tid] = result.get("nodes", [])
    return {
        "complete": True,
        "pages": pages_total,
        "capped": False,
        "error": None,
        "fetched_comments_by_thread_id": fetched,
    }



def _build_raw_thread_node(outer_node: Dict[str, Any]) -> Dict[str, Any]:
    """Round-76 PHASE 3 helper: build the canonical raw thread node
    for the nested-pagination follower. One raw node per stable
    thread id; never per comment.
    """
    return {
        "id": outer_node.get("id", ""),
        "comments": outer_node.get("comments") or {},
        "raw": outer_node,
    }


def _dedup_raw_thread_nodes_by_id(
    raw_nodes: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Round-76 PHASE 3 helper: collapse duplicate raw nodes to one
    per stable thread id. Preserve the most cursor-complete node
    (longest ``comments.pageInfo.endCursor`` wins, deterministic).
    If neither cursor is a prefix-extension of the other AND they
    differ, fail closed with ``conflicting_cursor_for_thread_id``.
    """
    if not raw_nodes:
        return []
    by_id: Dict[str, Dict[str, Any]] = {}
    for rn in raw_nodes:
        if not isinstance(rn, dict):
            continue
        tid = rn.get("id", "") or ""
        if not tid:
            continue
        candidate = rn
        if tid in by_id:
            existing = by_id[tid]
            ec = (
                (existing.get("comments") or {}).get("pageInfo") or {}
            ).get("endCursor") or ""
            cc = (
                (candidate.get("comments") or {}).get("pageInfo") or {}
            ).get("endCursor") or ""
            if ec and cc and ec != cc:
                # Two non-empty differing cursors. Compatible iff
                # one extends the other (the longer is a
                # continuation of the shorter). When they are
                # both non-empty but neither extends the other,
                # they are an irreconcilable conflict — fail
                # closed.
                if cc.startswith(ec) or ec.startswith(cc):
                    if len(cc) > len(ec):
                        by_id[tid] = candidate
                else:
                    raise ValueError(
                        f"conflicting_cursor_for_thread_id: {tid} "
                        f"existing={ec!r} candidate={cc!r}"
                    )
            elif cc and not ec:
                # Candidate has a cursor, existing doesn't.
                by_id[tid] = candidate
            # If both empty, keep existing.
        else:
            by_id[tid] = candidate
    # Preserve original outer-page insertion order by deduplicating
    # in first-seen order.
    seen: set = set()
    ordered: List[Dict[str, Any]] = []
    for rn in raw_nodes:
        if not isinstance(rn, dict):
            continue
        tid = rn.get("id", "") or ""
        if not tid or tid in seen:
            continue
        seen.add(tid)
        ordered.append(by_id[tid])
    return ordered




def _attach_canonical_thread_participants(
    thread_id: str,
    all_threads: List[Dict[str, Any]],
) -> None:
    """Round-78 PHASE 3 P2 fix: ensure every flattened record
    for ``thread_id`` carries the same canonical participant
    evidence so that ``aed_pr.deduplicate_thread_records()``
    never fails closed on a duplicate thread that has an
    empty ``comments`` list on a fetched record.

    The helper:

    1. finds the canonical participant list by taking the
       union of all ``comments`` entries across every record
       that shares ``thread_id`` AND adding the explicit
       ``unknown`` participant for any fetched author label
       not yet present;
    2. deduplicates participants by ``(database_id, author)``
       while preserving first-seen order;
    3. writes the same canonical list back to every matching
       record.

    This is a narrow additive helper. It does not fabricate
    participants that were not actually observed; it only
    propagates the canonical evidence that was already
    assembled for the thread by the Round-77 PHASE 3 P1-B
    branch (which updates the anchor record's ``comments``).
    """
    if not thread_id:
        return
    canonical: list = []
    seen_keys: set = set()
    for rec in all_threads:
        if not isinstance(rec, dict):
            continue
        if (rec.get("thread_id") or rec.get("id") or "") != thread_id:
            continue
        for c in rec.get("comments") or []:
            if not isinstance(c, dict):
                continue
            key = c.get("database_id") or c.get("author") or ""
            if key in seen_keys:
                continue
            seen_keys.add(key)
            canonical.append({
                "author": c.get("author") or "",
                "database_id": c.get("database_id"),
            })
    # Also fold in any fetched author labels that the canonical
    # list has not yet captured (mirrors the Round-77 anchor
    # update path).
    for rec in all_threads:
        if not isinstance(rec, dict):
            continue
        if (rec.get("thread_id") or rec.get("id") or "") != thread_id:
            continue
        author_label = rec.get("author") or ""
        if not author_label:
            continue
        anchor_key = author_label
        if anchor_key not in seen_keys:
            seen_keys.add(anchor_key)
            canonical.append({
                "author": author_label,
                "database_id": None,
            })
    if not canonical:
        return
    for rec in all_threads:
        if not isinstance(rec, dict):
            continue
        if (rec.get("thread_id") or rec.get("id") or "") != thread_id:
            continue
        rec["comments"] = list(canonical)



def _flatten_review_thread_comment(
    thread_state: Dict[str, Any],
    raw_comment: Dict[str, Any],
) -> Dict[str, Any]:
    """Round-76 PHASE 3 helper: build a canonical flattened
    inventory record for any comment (initial-page or fetched
    via nested-follower), preserving thread id, anchor state,
    and the comment's author, body, URL, path, line, original
    commit OID, and database id.
    """
    author_login = ""
    au = raw_comment.get("author") or {}
    if isinstance(au, dict):
        author_login = au.get("login", "") or ""
    elif isinstance(au, str):
        author_login = au
    oc = raw_comment.get("originalCommit")
    original_commit_oid = ""
    if isinstance(oc, dict):
        original_commit_oid = oc.get("oid", "") or ""
    return {
        "thread_id": thread_state.get("thread_id", ""),
        "is_resolved": bool(thread_state.get("is_resolved", False)),
        "is_outdated": bool(thread_state.get("is_outdated", False)),
        "comment_database_id": raw_comment.get("databaseId"),
        "comment_url": raw_comment.get("url", "") or "",
        "author": author_login,
        "body": raw_comment.get("body", "") or "",
        "path": raw_comment.get("path", "") or "",
        "line": raw_comment.get("line"),
        "original_commit_sha": original_commit_oid,
        "comments": [],
        "nested_incomplete": False,
    }


def _merge_flattened_comment(
    records: List[Dict[str, Any]],
    record: Dict[str, Any],
    dedup_index: Optional[Dict[Tuple[Any, Any], int]] = None,
) -> None:
    """Round-76 PHASE 3 helper: append a flattened comment record
    to the inventory list, deduplicating by stable
    ``comment_database_id`` and preserving useful original order.

    Round-77 PHASE 3 P1-A defense-in-depth: a null
    ``comment_database_id`` cannot be deduplicated, so a
    runaway caller could append the same record many times.

    Round-89 follow-up: the previous implementation used
    ``for existing in records[:2000]`` which silently
    allowed duplicate records whose position was past
    index 2000 to slip through and inflate the audit packet.
    To support the full inventory correctly, callers that
    pre-build a ``dedup_index`` (mapping
    ``(thread_id, comment_database_id) -> index in records``)
    should pass it in; the loop becomes O(1). Callers that
    omit ``dedup_index`` fall back to the linear scan but still
    MUST NOT silently truncate. The linear fallback
    documents the truncation explicitly so future audits can
    detect it.
    """
    db_id = record.get("comment_database_id")
    thread_id = record.get("thread_id")
    if db_id is not None:
        if dedup_index is not None:
            key = (thread_id, db_id)
            if key in dedup_index:
                # Already materialized; do not duplicate.
                return
        else:
            for existing in records:
                if (existing.get("thread_id") == thread_id
                        and existing.get("comment_database_id") == db_id):
                    # Already materialized; do not duplicate.
                    return
    records.append(record)
    if dedup_index is not None and db_id is not None:
        dedup_index[(thread_id, db_id)] = len(records) - 1


def _canonical_review_thread_inventory(
    *, owner, name, pr_number, page_size: int = 100,
    timeout: int = 30,
    starting_cursor: Optional[str] = None,
    starting_pages: int = 0,
    safety_cap: int = 2000,
    do_walk: bool = False,
):
    """Canonical review-thread inventory.

    Round-69 Codex review 4769640328 (P2): the production
    status/advance/merge path calls this helper with
    ``max_polls=1`` and cannot advance the cursor across
    multiple poll iterations. To make the one-shot
    controller path work, the polling loop in
    ``classify()`` calls this helper with ``do_walk=True``
    on the first poll to walk every page internally. The
    multi-poll cursor path uses ``do_walk=False`` so each
    poll iteration returns just one page.

    This implementation:
      - keeps the ``subprocess.run(``gh api graphql``)`` call
        shape so existing test mocks via ``monkeypatch``
        continue to work;
      - requests a single page of ``reviewThreads`` with
        nested ``comments.pageInfo`` (so the nested-comment
        fail-closed check is preserved);
      - returns the visible threads (with
        ``nested_incomplete=True`` per-thread) so the
        visible-blocker logic in ``classify()`` can still
        detect Codex findings on partial inventory;
      - marks the overall inventory as incomplete via
        ``ok=False`` and the metadata flags when the
        first page's outer ``hasNextPage=true`` OR any
        thread's nested ``comments.pageInfo.hasNextPage=true``.

    Returns ``(ok, threads, error_msg, metadata)``.
    """
    empty_metadata: Dict[str, Any] = {
        "review_thread_comment_inventory_complete": False,
        "review_thread_comment_inventory_error_count": 0,
        "review_thread_comment_incomplete_thread_ids": [],
        "review_thread_inventory_complete": False,
        "review_thread_inventory_pages": 0,
        "review_thread_inventory_capped": False,
        "review_thread_inventory_error": "",
        # Round-74 PHASE 3: explicit structured per-page status
        # to distinguish real outer-fetch failure from a
        # successfully-fetched terminal outer page with
        # pending nested work.
        "outer_page_fetch_succeeded": False,
        "outer_page_terminal": False,
        "outer_page_has_next": False,
        "current_page_nested_pending_ids": [],
    }
    all_threads: List[Dict[str, Any]] = []
    # Round-71 PHASE 3-P2-A: keep raw outer review-thread
    # nodes (with their full ``id`` and nested
    # ``comments.pageInfo.endCursor``) alongside the
    # flattened audit records. The nested-pagination
    # follower needs the raw shape to issue the correct
    # GraphQL query against the canonical endpoint.
    raw_thread_nodes: List[Dict[str, Any]] = []
    incomplete_nested_thread_ids: List[str] = []
    # Round-76 PHASE 3 P1-F2: per-thread raw-node dedup.
    per_thread_raw_added: set = set()
    outer_node_id: str = ""
    outer_has_next = False
    outer_end_cursor: Optional[str] = None
    try:
        after_clause = (
            f', after: "{starting_cursor}"'
            if starting_cursor
            else ""
        )
        query_literal = (
            "query {"
            f'repository(owner:"{owner}", name:"{name}") {{'
            f"pullRequest(number:{pr_number}) {{"
            f"reviewThreads(first:{page_size}{after_clause}) {{"
            "pageInfo { hasNextPage endCursor }"
            "nodes {"
            # Round-69 Codex review 4769344844 (P1): add
            # whitespace between ``isOutdated`` and
            # ``comments`` so the rendered query is
            # ``... isOutdated comments(first:50) ...``
            # instead of ``... isOutdatedcomments(first:50) ...``.
            "id isResolved isOutdated "
            "comments(first:50) {"
            "pageInfo { hasNextPage endCursor }"
            "nodes { databaseId url body path line "
            "originalCommit { oid } "
            "author { login } } } } } } } }"
        )
        cmd = [
            "gh", "api", "graphql",
            "--raw-field", f"query={query_literal}",
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, [], (
                f"gh graphql invocation failed: {exc}"
            ), dict(empty_metadata)
        if result.returncode != 0:
            return False, [], (
                f"gh graphql returned {result.returncode}: "
                f"{result.stderr[:500]}"
            ), dict(empty_metadata)
        if not result.stdout.strip():
            return False, [], (
                "gh graphql returned empty stdout"
            ), dict(empty_metadata)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return False, [], (
                f"invalid GraphQL response: {exc}"
            ), dict(empty_metadata)
        if not isinstance(data, dict):
            return False, [], (
                "GraphQL response is not a JSON object"
            ), dict(empty_metadata)
        errors = data.get("errors")
        if errors:
            return False, [], (
                f"GraphQL errors: {errors}"
            ), dict(empty_metadata)
        data_obj = data.get("data")
        if not isinstance(data_obj, dict):
            return False, [], (
                "GraphQL response missing data object"
            ), dict(empty_metadata)
        repository = data_obj.get("repository")
        if not isinstance(repository, dict):
            return False, [], (
                "GraphQL response missing repository"
            ), dict(empty_metadata)
        pr_data = repository.get("pullRequest")
        if not isinstance(pr_data, dict):
            return False, [], (
                "GraphQL response missing pullRequest"
            ), dict(empty_metadata)
        threads_container = pr_data.get("reviewThreads")
        if not isinstance(threads_container, dict):
            return False, [], (
                "GraphQL response missing reviewThreads container"
            ), dict(empty_metadata)
        page_info = threads_container.get("pageInfo") or {}
        if not isinstance(page_info, dict):
            return False, [], (
                "GraphQL reviewThreads.pageInfo is not a dict"
            ), dict(empty_metadata)
        nodes = threads_container.get("nodes")
        if not isinstance(nodes, list):
            return False, [], (
                "GraphQL reviewThreads.nodes is not a list"
            ), dict(empty_metadata)
        for node in nodes:
            if not isinstance(node, dict):
                continue
            outer_node_id = node.get("id", "") or ""
            thread_id = outer_node_id
            is_resolved = bool(node.get("isResolved", False))
            is_outdated = bool(node.get("isOutdated", False))
            comments_obj = node.get("comments") or {}
            nested_page_info = comments_obj.get("pageInfo") or {}
            if not isinstance(nested_page_info, dict):
                nested_page_info = {}
            nested_incomplete = bool(
                nested_page_info.get("hasNextPage")
            )
            if nested_incomplete and thread_id:
                incomplete_nested_thread_ids.append(thread_id)
            # Aggregate per-thread participants so the
            # eligibility check can verify "every reply in
            # the thread is bot-authored" rather than
            # looking at a single comment in isolation.
            thread_participants: Dict[str, List[Dict[str, Any]]] = {}
            for comment in (comments_obj.get("nodes") or []):
                if not isinstance(comment, dict):
                    continue
                entry = thread_participants.setdefault(
                    thread_id, []
                )
                entry.append({
                    "author": (
                        (comment.get("author") or {}).get(
                            "login", ""
                        )
                        if isinstance(comment.get("author"), dict)
                        else ""
                    ),
                    "database_id": comment.get("databaseId"),
                })
            for comment in (comments_obj.get("nodes") or []):
                if not isinstance(comment, dict):
                    continue
                author_login = (
                    (comment.get("author") or {}).get("login", "")
                    if isinstance(comment.get("author"), dict)
                    else ""
                )
                original_commit_oid = ""
                oc = comment.get("originalCommit")
                if isinstance(oc, dict):
                    original_commit_oid = oc.get("oid", "") or ""
                entry = {
                    "thread_id": thread_id,
                    "is_resolved": is_resolved,
                    "is_outdated": is_outdated,
                    "comment_database_id": comment.get("databaseId"),
                    "comment_url": comment.get("url", "") or "",
                    "author": author_login,
                    "body": comment.get("body", "") or "",
                    "path": comment.get("path", "") or "",
                    "line": comment.get("line"),
                    "original_commit_sha": original_commit_oid,
                    # Round-69 Codex review 4768843522 (P2):
                    # the packet builder at line 2233 reads
                    # ``t.get("comments")`` to extract the
                    # per-thread participant list. Storing
                    # it under "participants" made the
                    # shared non-human policy reject
                    # otherwise eligible Codex-only threads
                    # as ``unknown_actor_in_thread``. Use
                    # "comments" to match the original key
                    # the packet builder expects.
                    "comments": thread_participants.get(
                        thread_id, []
                    ),
                    "nested_incomplete": nested_incomplete,
                }
                # Round-71 PHASE 3-P2-A: keep the raw outer
                # thread node so the nested-pagination
                # follower can issue the canonical
                # ``node(id: $threadId)`` query against the
                # original payload, not against the
                # flattened participant record.
                # Round-76 PHASE 3 P1-F2: build the raw outer
                # thread node ONCE per thread (NOT per comment),
                # so a thread with 50 first-page comments no
                # longer creates 50 duplicate raw nodes. The
                # helper returns a canonical node; we add it
                # only on the first time we observe the thread
                # id on this outer page.
                if (
                    outer_node_id
                    and outer_node_id not in per_thread_raw_added
                ):
                    raw_thread_nodes.append(
                        _build_raw_thread_node(node)
                    )
                    per_thread_raw_added.add(outer_node_id)
                all_threads.append(entry)
        # Round-76 PHASE 3 P1-F2: dedup raw_thread_nodes by
        # stable thread id so the nested-pagination follower
        # walks each connection exactly once.
        try:
            raw_thread_nodes = _dedup_raw_thread_nodes_by_id(
                raw_thread_nodes
            )
        except ValueError as _dedup_err:
            return False, all_threads, str(_dedup_err), {
                **empty_metadata,
                "review_thread_inventory_pages": 1,
                "review_thread_inventory_capped": False,
                "review_thread_inventory_error": str(_dedup_err),
                "review_thread_inventory_complete": False,
                "review_thread_comment_inventory_complete": False,
            }
        outer_has_next = bool(page_info.get("hasNextPage"))
        outer_end_cursor = page_info.get("endCursor")
        if outer_has_next and not outer_end_cursor:
            return False, [], (
                "reviewThreads.pageInfo.hasNextPage=true with no "
                "endCursor"
            ), {
                **empty_metadata,
                "review_thread_inventory_pages": 1,
                "review_thread_inventory_capped": False,
            }
        # When outer or nested pagination is incomplete,
        # return visible threads but mark inventory as
        # incomplete so the unified inventory gate in
        # section 8 fails closed. The visible threads are
        # preserved so the visible-blocker logic can detect
        # Codex findings on the visible page.
        # Round-69 Codex review 4769706200 (P2): when
        # ``do_walk`` is True and the inventory is
        # incomplete, do NOT return False early. Fall
        # through to the walker below so the one-shot
        # controller path can complete the inventory.
        # Only return early when ``do_walk`` is False
        # (i.e. the multi-poll cursor path wants just
        # one page).
        if outer_has_next or incomplete_nested_thread_ids:
            if not do_walk:
                # Round-74 PHASE 3: distinguish successful terminal
                # page-with-nested-work from genuine outer-fetch
                # failure. We do NOT use the parent's accumulated
                # nested IDs as the gate — we use the current-page
                # ``outer_page_has_next`` flag plus the explicit
                # structured status fields below.
                #
                # If outer_has_next=True, the parent walker must
                # advance the outer cursor — return False with
                # ``review_thread_pagination_incomplete=True`` so
                # the parent knows to continue walking outer pages.
                #
                # If outer_has_next=False and there is pending
                # nested work, the outer page itself was SUCCESSFULLY
                # fetched; it is just a terminal page. Return True
                # with structured ``outer_page_fetch_succeeded=True``,
                # ``outer_page_terminal=True``, and
                # ``current_page_nested_pending_ids=[...]``. The
                # parent walker will then run nested-follow.
                if outer_has_next:
                    err_msg = (
                        "reviewThreads.pageInfo.hasNextPage=true; "
                        "pagination required"
                    )
                    metadata = {
                        "review_thread_comment_inventory_complete":
                            not incomplete_nested_thread_ids,
                        "review_thread_comment_inventory_error_count":
                            len(incomplete_nested_thread_ids),
                        "review_thread_comment_incomplete_thread_ids":
                            list(incomplete_nested_thread_ids),
                        "review_thread_inventory_complete": False,
                        "review_thread_inventory_pages": 1,
                        "review_thread_inventory_capped": False,
                        "review_thread_inventory_error": err_msg,
                        "review_thread_pagination_incomplete": True,
                        "review_thread_pagination_end_cursor":
                            outer_end_cursor,
                        # Round-74 PHASE 3: explicit structured
                        # status: outer page fetched successfully
                        # and has more pages.
                        "outer_page_fetch_succeeded": True,
                        "outer_page_terminal": False,
                        "outer_page_has_next": True,
                        "current_page_nested_pending_ids": list(
                            incomplete_nested_thread_ids
                        ),
                        # Round-75 PHASE 3 P1-A: every
                        # successfully parsed outer page must
                        # publish the raw review-thread nodes
                        # it collected. The parent walker needs
                        # them for nested-pagination follow-up.
                        "_raw_thread_nodes": list(raw_thread_nodes),
                    }
                    return False, all_threads, err_msg, metadata
                # outer_has_next=False: this is a SUCCESSFUL
                # terminal page. If nested work is pending, the
                # parent walker must run nested-follow.
                if incomplete_nested_thread_ids:
                    metadata = {
                        "review_thread_comment_inventory_complete": False,
                        "review_thread_comment_inventory_error_count":
                            len(incomplete_nested_thread_ids),
                        "review_thread_comment_incomplete_thread_ids":
                            list(incomplete_nested_thread_ids),
                        "review_thread_inventory_complete": False,
                        "review_thread_inventory_pages": 1,
                        "review_thread_inventory_capped": False,
                        "review_thread_inventory_error": "",
                        "review_thread_pagination_incomplete": False,
                        "review_thread_pagination_end_cursor":
                            outer_end_cursor,
                        # Round-74 PHASE 3: structured status for
                        # terminal page with nested work.
                        "outer_page_fetch_succeeded": True,
                        "outer_page_terminal": True,
                        "outer_page_has_next": False,
                        "current_page_nested_pending_ids": list(
                            incomplete_nested_thread_ids
                        ),
                        # Round-75 PHASE 3 P1-A: every successful
                        # outer-page must publish the raw
                        # review-thread nodes it collected so
                        # the parent walker can pass them to the
                        # nested follower. The terminal page is
                        # where the previously-suppressed bug
                        # manifested: a pending ID without a
                        # corresponding raw node.
                        "_raw_thread_nodes": list(raw_thread_nodes),
                    }
                    # Round-74 PHASE 3: return True with the
                    # structured terminal-page status. Parent
                    # walker reads outer_page_fetch_succeeded /
                    # outer_page_terminal / current_page_nested_pending_ids
                    # to decide whether to invoke nested-follow.
                    return True, all_threads, "", metadata
            # else: do_walk=True and inventory is
            # incomplete. Fall through to the success
            # metadata + walker below so the one-shot
            # controller path can complete the inventory.
            # Mark inventory as not complete so the
            # walker's early-return check sees it.
            outer_has_next_for_walk = True
            err_msg = ""
        else:
            outer_has_next_for_walk = False
            err_msg = ""
        nested_complete = not incomplete_nested_thread_ids
        metadata = {
            "review_thread_comment_inventory_complete":
                nested_complete,
            "review_thread_comment_inventory_error_count":
                len(incomplete_nested_thread_ids),
            "review_thread_comment_incomplete_thread_ids":
                list(incomplete_nested_thread_ids),
            # Round-71 PHASE 3-P2-A: publish raw outer
            # thread nodes so the outer walker drains
            # them into its unified raw cache before
            # invoking the nested follower.
            "_raw_thread_nodes": list(raw_thread_nodes),
            "review_thread_inventory_complete":
                not outer_has_next_for_walk,
            "review_thread_inventory_pages": 1,
            "review_thread_inventory_capped": False,
            "review_thread_inventory_error": err_msg,
            "review_thread_pagination_incomplete":
                outer_has_next_for_walk,
            "review_thread_pagination_end_cursor":
                outer_end_cursor,
        }
        # Round-69 Codex review 4769640328 (P2):
        # when ``do_walk`` is True and the inventory is
        # incomplete (``review_thread_pagination_incomplete``),
        # keep walking additional pages until
        # ``hasNextPage=false`` (or the safety cap fires,
        # fail-closed). This is the one-shot controller
        # path; the polling-loop path uses
        # ``do_walk=False`` (currently the polling loop
        # also relies on ``do_walk=True`` because the
        # production path uses ``max_polls=1``).
        # Round-72 PHASE 3 P1: outer-pagination and nested-cursor
        # pagination are two separate completion conditions.
        # Enter the walker whenever EITHER is incomplete.
        # The walker's body does not issue another outer
        # request when ``outer_incomplete`` is False, so a
        # purely nested-pending inventory does not make
        # any spurious outer requests.
        # Round-73 PHASE 3 P1-A: derive outer_incomplete from
        # the FIRST outer page's ``outer_has_next`` flag,
        # NOT from the combined-inventory metadata flag.
        # The metadata's ``review_thread_pagination_incomplete``
        # conflates outer pagination and nested-cursor state
        # (because it is set by the ``outer_has_next_for_walk``
        # boolean below which mirrors ``True`` whenever any
        # inventory remains). Deriving the outer-loop guard
        # from it caused the walker to re-fetch the first
        # outer page even when only nested work remained.
        outer_incomplete = bool(outer_has_next)
        nested_pending_on_entry = bool(
            incomplete_nested_thread_ids
        ) and bool(
            metadata.get(
                "review_thread_comment_incomplete_thread_ids", []
            )
        )
        if not do_walk or (
            not outer_incomplete and not nested_pending_on_entry
        ):
            return True, all_threads, "", metadata
        # The first page had ``hasNextPage=true``. Walk
        # additional pages internally so the one-shot
        # controller path can complete the inventory.
        # Round-72 PHASE 3 P1: gate the outer-while loop on
        # ``outer_incomplete``. When only nested cursors are
        # pending, the loop body is skipped entirely and we
        # transition directly to nested-follow further down.
        cursor = metadata.get(
            "review_thread_pagination_end_cursor"
        )
        pages = 1
        # Round-72 PHASE 3 P1: if only nested cursors are
        # pending (outer pagination already complete on
        # entry), skip outer page fetching entirely and
        # proceed directly to nested-follow. We achieve
        # this by setting the outer-while loop guard to
        # False so it doesn't execute.
        if not outer_incomplete and incomplete_nested_thread_ids:
            # Round-75 PHASE 3 P1-C: validate ID-to-node
            # coverage in this single-page terminal
            # path too. Same validation as the multi-page
            # path below.
            missing_node_ids = []
            for _tid in incomplete_nested_thread_ids:
                _matched = [
                    rn for rn in raw_thread_nodes
                    if isinstance(rn, dict)
                    and rn.get("id") == _tid
                ]
                if not _matched:
                    missing_node_ids.append(_tid)
                    continue
                _node = _matched[0]
                _comments = _node.get("comments")
                if not isinstance(_comments, dict):
                    missing_node_ids.append(_tid)
                    continue
                _page_info = _comments.get("pageInfo") or {}
                if not isinstance(_page_info, dict):
                    missing_node_ids.append(_tid)
                    continue
                if not bool(_page_info.get("hasNextPage")):
                    missing_node_ids.append(_tid)
                    continue
                _end_cursor = _page_info.get("endCursor") or ""
                if not isinstance(_end_cursor, str) or not _end_cursor:
                    missing_node_ids.append(_tid)
                    continue
            if missing_node_ids:
                return False, all_threads, (
                    "nested_pending_raw_node_missing"
                ), {
                    **empty_metadata,
                    "review_thread_comment_inventory_complete": False,
                    "review_thread_comment_inventory_error_count": (
                        len(missing_node_ids)
                    ),
                    "review_thread_comment_incomplete_thread_ids": (
                        list(missing_node_ids)
                    ),
                    "review_thread_inventory_complete": False,
                    "review_thread_inventory_pages": pages,
                    "review_thread_inventory_capped": False,
                    "review_thread_inventory_error": (
                        "nested_pending_raw_node_missing"
                    ),
                }
            # Transition directly to nested-follow. The
            # existing nested-follow code lives inside the
            # outer-while body under the
            # "Inventory complete. Done." branch. We here
            # replicate its core logic for the
            # outer-already-complete case.
            nested_follow = _follow_nested_cursor_for_threads(
                raw_thread_nodes,
                safety_cap=safety_cap,
                timeout=timeout,
            )
            if not nested_follow.get("complete"):
                return False, all_threads, (
                    nested_follow.get("error")
                    or "nested_pagination_failed"
                ), {
                    **empty_metadata,
                    "review_thread_comment_inventory_complete": False,
                    "review_thread_comment_inventory_error_count": (
                        len(incomplete_nested_thread_ids)
                    ),
                    "review_thread_comment_incomplete_thread_ids": (
                        list(incomplete_nested_thread_ids)
                    ),
                    "review_thread_inventory_pages": pages,
                    "review_thread_inventory_capped": bool(
                        nested_follow.get("capped", False)
                    ),
                    "review_thread_inventory_error": (
                        nested_follow.get("error")
                        or "nested_pagination_failed"
                    ),
                }
            # Round-76 PHASE 3 P1-F1: materialize every fetched
            # nested comment as a canonical flattened inventory
            # record via the shared helpers (Finding 1).
            fetched = nested_follow.get(
                "fetched_comments_by_thread_id", {}
            )
            # Round-77 PHASE 3 P1-A: iterate a snapshot of
            # ``all_threads`` so that appended fetched
            # records do not re-enter the loop. The
            # previous loop iterated ``all_threads``
            # directly while _merge_flattened_comment
            # appended to it, producing cubic growth on
            # large threads (and indefinite growth when a
            # fetched record has a null databaseId). The
            # do_walk=True walker branch (line 2190+)
            # already uses this snapshot; this commit
            # extends the same fix to the do_walk=True
            # terminal-page fast path.
            _r77_terminal_snapshot = list(all_threads)
            # Round-79 PHASE 3 P2: collect affected thread IDs
            # in stable order so canonical attachment runs once
            # per unique thread.
            _r79_affected_thread_ids: List[str] = []
            _r79_affected_seen: set = set()
            # Round-89 follow-up: build the dedup index once
            # outside the nested-materialization loops so
            # O(1) lookup replaces the O(2000) linear scan that
            # silently allowed duplicate records past index
            # 2000. The index maps ``(thread_id, comment_database_id)``
            # to the index of the corresponding record in
            # ``all_threads``. ``Optional`` import is at the
            # top of this file.
            _r89_dedup_index: Dict[Tuple[Any, Any], int] = {}
            _r89_dedup_rebuild = False
            for _idx, _rt in enumerate(all_threads):
                _db = _rt.get("comment_database_id")
                if _db is not None:
                    _r89_dedup_index[
                        (_rt.get("thread_id"), _db)
                    ] = _idx
            for nt in _r77_terminal_snapshot:
                tid = nt.get("thread_id") or nt.get("id") or ""
                if tid not in fetched:
                    continue
                thread_state = {
                    "thread_id": tid,
                    "is_resolved": bool(nt.get("is_resolved", False)),
                    "is_outdated": bool(nt.get("is_outdated", False)),
                }
                _r77_fetched_records: list = []
                _r77_fetched_authors: list = []
                for en in fetched[tid]:
                    if not isinstance(en, dict):
                        continue
                    _rec = _flatten_review_thread_comment(
                        thread_state, en
                    )
                    _r77_fetched_records.append(_rec)
                    _au_obj = en.get("author") or {}
                    if isinstance(_au_obj, dict):
                        _r77_fetched_authors.append(
                            _au_obj.get("login", "") or ""
                        )
                    elif isinstance(_au_obj, str):
                        _r77_fetched_authors.append(_au_obj)
                    else:
                        _r77_fetched_authors.append("")
                # Extend all_threads once after the loop.
                for _r77_r in _r77_fetched_records:
                    _merge_flattened_comment(
                        all_threads, _r77_r,
                        dedup_index=_r89_dedup_index,
                    )
                # Update anchor participant evidence. Round-77
                # PHASE 3 P1-B: preserve blank authors as
                # explicit "unknown" participants so partial
                # actor evidence fails closed instead of
                # being dropped (which would let a thread
                # with a deleted-account comment appear
                # Codex-only and become eligible for
                # automatic resolution).
                if isinstance(nt.get("comments"), list):
                    _r77_seen_authors = {
                        c.get("author") if isinstance(c, dict) else ""
                        for c in nt["comments"]
                    }
                    for _r77_au in _r77_fetched_authors:
                        _r77_label = _r77_au or "unknown"
                        if _r77_label not in _r77_seen_authors:
                            nt["comments"].append({
                                "author": _r77_label,
                                "database_id": None,
                            })
                            _r77_seen_authors.add(_r77_label)
                nt["nested_incomplete"] = False
                # Record the affected thread ID once.
                if tid not in _r79_affected_seen:
                    _r79_affected_seen.add(tid)
                    _r79_affected_thread_ids.append(tid)
            # Round-78 PHASE 3 P2 + Round-79 PHASE 3 P2:
            # propagate canonical participant evidence
            # exactly once per unique affected thread.
            for _r79_tid in _r79_affected_thread_ids:
                _attach_canonical_thread_participants(
                    _r79_tid, all_threads
                )
            return True, all_threads, "", {
                **empty_metadata,
                "review_thread_comment_inventory_complete": True,
                "review_thread_comment_inventory_error_count": 0,
                "review_thread_comment_incomplete_thread_ids": [],
                "review_thread_inventory_complete": (
                    not bool(nested_follow.get("capped", False))
                ),
                "review_thread_inventory_pages": pages,
                "review_thread_inventory_capped": bool(
                    nested_follow.get("capped", False)
                ),
                "review_thread_inventory_error": "",
            }
        while outer_incomplete:
            if pages >= safety_cap:
                # Safety cap fired. The inventory is
                # incomplete in this case; the section 8
                # inventory gate will refuse merge-ready.
                return False, all_threads, (
                    f"review_thread_pagination_capped: "
                    f"pages={pages} safety_cap={safety_cap}"
                ), {
                    **empty_metadata,
                    "review_thread_comment_inventory_complete": (
                        not incomplete_nested_thread_ids
                    ),
                    "review_thread_inventory_pages": pages,
                    "review_thread_inventory_capped": True,
                    "review_thread_inventory_error": (
                        "review_thread_pagination_capped"
                    ),
                }
            pages += 1
            # Recursively call ourselves for the next page.
            ok_next, more_threads, err_next, meta_next = (
                _canonical_review_thread_inventory(
                    owner=owner, name=name, pr_number=pr_number,
                    starting_cursor=cursor,
                    starting_pages=pages,
                    do_walk=False,
                )
            )
            all_threads.extend(more_threads or [])
            # Round-71 PHASE 3-P2-A: drain raw outer-page nodes
            # the recursive page call collected into our own
            # raw cache so nested-follow sees the full set.
            for raw_node in (meta_next.get("_raw_thread_nodes", [])
                             if isinstance(meta_next, dict) else []):
                if not any(r.get("id") == raw_node.get("id")
                           for r in raw_thread_nodes):
                    raw_thread_nodes.append(raw_node)
            # Propagate nested-comment completeness across
            # pages. If any page reports an incomplete
            # nested inventory, the overall inventory is
            # incomplete.
            if not meta_next.get(
                "review_thread_comment_inventory_complete", False
            ):
                metadata[
                    "review_thread_comment_inventory_complete"
                ] = False
            # Round-69 Codex review 4769796846 (P2): when
            # the recursive call returns ok_next=False
            # AND ``review_thread_pagination_incomplete`` is
            # True, the recursive call signaled "more
            # pages available" (the helper's
            # single-page-mode ``do_walk=False`` returns
            # ok=False with pagination_incomplete=True
            # for the controller's polling protocol).
            # Treat this as a normal "keep walking" signal
            # by updating the cursor and continuing the
            # walker loop. Only treat it as a terminal
            # error when the recursive call's
            # ``err_next`` indicates a real failure (e.g.
            # GraphQL error, cursor missing, cap fired).
            cursor = meta_next.get(
                "review_thread_pagination_end_cursor"
            )
            # Round-73 PHASE 3 P1-B: when ``ok_next=False`` and
            # the recursion says pagination is complete,
            # check whether the recursion still has
            # incomplete nested thread IDs. If so, this is
            # NOT a terminal outer error — it is terminal
            # outer pagination with nested work pending
            # on the terminal page. Transfer that work into
            # the parent state, drain raw nodes, and let
            # the normal nested-follow branch (further down
            # this same walker invocation) handle it. The
            # pre-existing outer-metadata ``err_next`` is
            # typically empty for the success-but-with-
            # nested-pending case; if a real error
            # accompanies the nested work, we'll surface it
            # via the nested-follow branch fail-closed
            # return.
            if isinstance(meta_next, dict):
                # Promote any recursive-page raw nodes
                # into our cache so nested-follow sees the
                # terminal-page threads.
                for raw_node in meta_next.get(
                    "_raw_thread_nodes", []
                ):
                    if not any(
                        r.get("id") == raw_node.get("id")
                        for r in raw_thread_nodes
                    ):
                        raw_thread_nodes.append(raw_node)
                # Promote the recursive incomplete-nested
                # thread IDs into our list.
                for tid in meta_next.get(
                    "review_thread_comment_incomplete_thread_ids",
                    []
                ) or []:
                    if tid not in incomplete_nested_thread_ids:
                        incomplete_nested_thread_ids.append(tid)
            # Round-74 PHASE 3 P1: a recursive call returning
            # ``ok_next=False`` is ALWAYS a real outer failure
            # (subprocess non-zero, GraphQL errors, malformed
            # JSON, missing outer connection, missing cursor,
            # safety cap, etc.). The Round-73 fix relaxed this
            # condition to ``and not incomplete_nested_thread_ids``
            # but that suppressed genuine outer failures whenever
            # earlier pages had pending nested work. Outer fetch
            # success MUST be proven independently of
            # accumulated nested IDs. Fail closed; preserve the
            # outer failure reason; do NOT invoke the nested
            # follower; do NOT mark inventory complete.
            if not ok_next and not meta_next.get(
                "review_thread_pagination_incomplete", False
            ):
                # The page walker hit a real error
                # (not "more pages available"). Propagate
                # ``err_next`` and return ok=False so the
                # section 8 inventory gate fails closed.
                return False, all_threads, err_next, {
                    **empty_metadata,
                    "review_thread_comment_inventory_complete": (
                        metadata.get(
                            "review_thread_comment_inventory_complete",
                            False,
                        )
                    ),
                    "review_thread_comment_inventory_error_count": (
                        metadata.get(
                            "review_thread_comment_inventory_error_count",
                            0,
                        )
                    ),
                    "review_thread_comment_incomplete_thread_ids": (
                        metadata.get(
                            "review_thread_comment_incomplete_thread_ids",
                            [],
                        )
                    ),
                    "review_thread_inventory_pages": pages,
                    "review_thread_inventory_capped": False,
                    "review_thread_inventory_error": err_next,
                }
            if not meta_next.get(
                "review_thread_pagination_incomplete", False
            ):
                # Outer inventory complete. If any nested
                # pagination remained incomplete across
                # pages, follow cursors now.
                if incomplete_nested_thread_ids:
                    # Round-75 PHASE 3 P1-C: BEFORE invoking
                    # the nested follower, validate that every
                    # pending nested thread ID has exactly one
                    # matching cursor-bearing raw node in
                    # ``raw_thread_nodes``. A pending ID without
                    # a matching valid raw node means a terminal
                    # page failed to publish its raw evidence
                    # and the nested follower would silently
                    # skip that thread. Fail closed with a
                    # specific ``nested_pending_raw_node_missing``
                    # error and preserve the affected thread IDs.
                    missing_node_ids = []
                    for _tid in incomplete_nested_thread_ids:
                        _matched = [
                            rn for rn in raw_thread_nodes
                            if isinstance(rn, dict)
                            and rn.get("id") == _tid
                        ]
                        if not _matched:
                            missing_node_ids.append(_tid)
                            continue
                        _node = _matched[0]
                        _comments = _node.get("comments")
                        if not isinstance(_comments, dict):
                            missing_node_ids.append(_tid)
                            continue
                        _page_info = _comments.get("pageInfo") or {}
                        if not isinstance(_page_info, dict):
                            missing_node_ids.append(_tid)
                            continue
                        if not bool(_page_info.get("hasNextPage")):
                            missing_node_ids.append(_tid)
                            continue
                        _end_cursor = _page_info.get("endCursor") or ""
                        if not isinstance(_end_cursor, str) or not _end_cursor:
                            missing_node_ids.append(_tid)
                            continue
                    if missing_node_ids:
                        # Round-75 PHASE 3 P1-C fail-closed:
                        # preserve every missing ID, mark both
                        # outer and nested inventories incomplete,
                        # do NOT invoke the nested follower, do
                        # NOT report clean or merge-ready.
                        return False, all_threads, (
                            "nested_pending_raw_node_missing"
                        ), {
                            **empty_metadata,
                            "review_thread_comment_inventory_complete": False,
                            "review_thread_comment_inventory_error_count": (
                                len(missing_node_ids)
                            ),
                            "review_thread_comment_incomplete_thread_ids": (
                                list(missing_node_ids)
                            ),
                            "review_thread_inventory_complete": False,
                            "review_thread_inventory_pages": pages,
                            "review_thread_inventory_capped": False,
                            "review_thread_inventory_error": (
                                "nested_pending_raw_node_missing"
                            ),
                        }
                    # Round-71 PHASE 3-P2-A: pass the *raw*
                    # outer thread nodes (with their full
                    # ``id`` and nested
                    # ``comments.pageInfo.endCursor``) to
                    # the nested follower so the helper
                    # can issue the canonical
                    # ``node(id: $threadId) { ... }``
                    # GraphQL query against the raw
                    # shape, not against the flattened
                    # audit record.
                    nested_follow = (
                        _follow_nested_cursor_for_threads(
                            raw_thread_nodes,
                            safety_cap=safety_cap,
                            timeout=timeout,
                        )
                    )
                    if not nested_follow.get("complete"):
                        # Round-71 PHASE 3-P2-B: fail closed.
                        return False, all_threads, (
                            nested_follow.get("error")
                            or "nested_pagination_failed"
                        ), {
                            **empty_metadata,
                            "review_thread_comment_inventory_complete": False,
                            "review_thread_comment_inventory_error_count": len(incomplete_nested_thread_ids),
                            "review_thread_comment_incomplete_thread_ids": list(incomplete_nested_thread_ids),
                            "review_thread_inventory_pages": pages,
                            "review_thread_inventory_capped": bool(nested_follow.get("capped", False)),
                            "review_thread_inventory_error": (
                                nested_follow.get("error") or "nested_pagination_failed"
                            ),
                        }
                    # Round-76 PHASE 3 P1-F1: materialize every
                    # fetched nested comment as a canonical
                    # flattened inventory record (not just a
                    # participant-list entry). The active-blocker
                    # scan reads top-level record authors and
                    # bodies, so a later Codex finding on the
                    # 51st comment MUST appear as a top-level
                    # record.
                    fetched = nested_follow.get(
                        "fetched_comments_by_thread_id", {}
                    )
                    # Round-77 PHASE 3 P1-A: snapshot
                    # ``all_threads`` before iteration so that
                    # fetched records appended via
                    # _merge_flattened_comment do not re-enter
                    # the outer loop (cubic growth / indefinite
                    # append on null-databaseId records).
                    _r77_anchor_snapshot = list(all_threads)
                    # Round-89 follow-up: build the dedup index
                    # once outside the nested-materialization
                    # loops so O(1) lookup replaces the
                    # O(2000) linear scan that silently allowed
                    # duplicate records past index 2000.
                    _r89_dedup_index: Dict[Tuple[Any, Any], int] = {}
                    for _idx, _rt in enumerate(all_threads):
                        _db = _rt.get("comment_database_id")
                        if _db is not None:
                            _r89_dedup_index[
                                (_rt.get("thread_id"), _db)
                            ] = _idx
                    # Round-79 PHASE 3 P2 narrow repair:
                    # collect affected thread IDs in a stable
                    # ordered set so the canonical participant
                    # attachment runs exactly once per unique
                    # thread, regardless of how many flattened
                    # records the snapshot contains.
                    _r79_affected_thread_ids: List[str] = []
                    _r79_affected_seen: set = set()
                    for nt in _r77_anchor_snapshot:
                        tid = nt.get("thread_id") or nt.get("id") or ""
                        if tid not in fetched:
                            continue
                        thread_state = {
                            "thread_id": tid,
                            "is_resolved": bool(nt.get("is_resolved", False)),
                            "is_outdated": bool(nt.get("is_outdated", False)),
                        }
                        _r77_fetched_records: list = []
                        _r77_fetched_authors: list = []
                        for en in fetched[tid]:
                            if not isinstance(en, dict):
                                continue
                            _flatten_review_thread_comment_rec = (
                                _flatten_review_thread_comment(
                                    thread_state, en
                                )
                            )
                            _r77_fetched_records.append(
                                _flatten_review_thread_comment_rec
                            )
                            _au_obj = en.get("author") or {}
                            if isinstance(_au_obj, dict):
                                _r77_fetched_authors.append(
                                    _au_obj.get("login", "") or ""
                                )
                            elif isinstance(_au_obj, str):
                                _r77_fetched_authors.append(_au_obj)
                            else:
                                _r77_fetched_authors.append("")
                        # Append fetched records once after
                        # the inner loop completes.
                        for _r77_r in _r77_fetched_records:
                            _merge_flattened_comment(
                                all_threads, _r77_r,
                                dedup_index=_r89_dedup_index,
                            )
                        # Round-77 PHASE 3 P1-B: preserve
                        # blank authors as explicit
                        # "unknown" participant entries.
                        if isinstance(nt.get("comments"), list):
                            _r77_seen_authors = {
                                c.get("author") if isinstance(c, dict) else ""
                                for c in nt["comments"]
                            }
                            for _r77_au in _r77_fetched_authors:
                                _r77_label = _r77_au or "unknown"
                                if _r77_label not in _r77_seen_authors:
                                    nt["comments"].append({
                                        "author": _r77_label,
                                        "database_id": None,
                                    })
                                    _r77_seen_authors.add(_r77_label)
                        nt["nested_incomplete"] = False
                        # Record the affected thread ID once;
                        # canonical attachment happens after the
                        # loop, once per unique thread.
                        if tid not in _r79_affected_seen:
                            _r79_affected_seen.add(tid)
                            _r79_affected_thread_ids.append(tid)
                    # Round-78 PHASE 3 P2 + Round-79 PHASE 3 P2:
                    # propagate canonical participant evidence
                    # exactly once per unique affected thread.
                    for _r79_tid in _r79_affected_thread_ids:
                        _attach_canonical_thread_participants(
                            _r79_tid, all_threads
                        )
                    # Round-71 PHASE 3-P2-B: after every
                    # required nested cursor succeeds,
                    # reset the inventory-completeness flags
                    # regardless of the outer-loop metadata
                    # value computed earlier (which reflected
                    # the pre-nested view).
                    final_metadata = {
                        **empty_metadata,
                        "review_thread_comment_inventory_complete": True,
                        "review_thread_comment_inventory_error_count": 0,
                        "review_thread_comment_incomplete_thread_ids": [],
                        "review_thread_inventory_complete": (
                            not nested_follow.get("capped", False)
                        ),
                        "review_thread_inventory_pages": pages,
                        "review_thread_inventory_capped": bool(
                            nested_follow.get("capped", False)
                        ),
                        "review_thread_inventory_error": "",
                    }
                    return True, all_threads, "", final_metadata
                # Inventory complete. Done.
                return True, all_threads, "", {
                    **empty_metadata,
                    "review_thread_comment_inventory_complete": (
                        metadata.get(
                            "review_thread_comment_inventory_complete",
                            False,
                        )
                    ),
                    "review_thread_comment_inventory_error_count": (
                        metadata.get(
                            "review_thread_comment_inventory_error_count",
                            0,
                        )
                    ),
                    "review_thread_comment_incomplete_thread_ids": (
                        metadata.get(
                            "review_thread_comment_incomplete_thread_ids",
                            [],
                        )
                    ),
                    "review_thread_inventory_pages": pages,
                    "review_thread_inventory_capped": False,
                    "review_thread_inventory_error": "",
                }
            # ok_next=False with
            # pagination_incomplete=True: the
            # recursive call signaled "more pages
            # available". Continue walking with the
            # new cursor.
            if not ok_next and not cursor:
                # ``hasNextPage=true`` with no
                # ``endCursor`` is an error inside
                # the wrapper.
                return False, all_threads, (
                    f"{err_next}; cursor_missing"
                ), {
                    **empty_metadata,
                    "review_thread_comment_inventory_complete": (
                        metadata.get(
                            "review_thread_comment_inventory_complete",
                            False,
                        )
                    ),
                    "review_thread_comment_inventory_error_count": (
                        metadata.get(
                            "review_thread_comment_inventory_error_count",
                            0,
                        )
                    ),
                    "review_thread_comment_incomplete_thread_ids": (
                        metadata.get(
                            "review_thread_comment_incomplete_thread_ids",
                            [],
                        )
                    ),
                    "review_thread_inventory_pages": pages,
                    "review_thread_inventory_capped": False,
                    "review_thread_inventory_error": (
                        "cursor_missing"
                    ),
                }
    except Exception as exc:
        return False, [], (
            f"review_thread_inventory_failed: {exc}"
        ), dict(empty_metadata)


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

    # Review-thread inventory completeness. The GraphQL review-thread
    # fetch is REQUIRED evidence for merge readiness. If the fetch
    # fails, returns errors, is missing expected data, has unhandled
    # pagination, or returns malformed JSON, inventory is incomplete
    # and the classifier must fail closed. See the gate in section 8.
    review_thread_inventory_complete: bool = True
    review_thread_inventory_error_count: int = 0
    review_thread_inventory_last_error: str = ""

    # Issue-comment inventory completeness. PR-level issue
    # comments are REQUIRED evidence: a Codex clean pass or
    # a newer finding may live on a PR-level issue comment
    # rather than on a formal review submission. If the
    # /issues/{pr}/comments fetch fails, the classifier MUST
    # NOT emit merge-ready / clean-pass states on that poll.
    # See the gate in section 8.
    issue_comment_inventory_complete: bool = True
    issue_comment_inventory_error_count: int = 0
    issue_comment_inventory_last_error: str = ""

    # Review-submission inventory completeness. Formal
    # PullRequestReview submissions are REQUIRED evidence:
    # a newer formal Codex review such as CHANGES_REQUESTED
    # could be missed if the /pulls/{pr}/reviews fetch fails.
    # See the gate in section 8.
    review_submission_inventory_complete: bool = True
    review_submission_inventory_error_count: int = 0
    review_submission_inventory_last_error: str = ""

    # Review-thread comment (nested) inventory completeness.
    # The GraphQL reviewThreads query returns a nested
    # `comments(first:50)` connection on each thread. The
    # connection itself is paginated. If ANY thread's nested
    # comments connection has `hasNextPage=true`, the
    # inventory for that thread is incomplete: a Codex finding
    # may live on a later comments page. The unified
    # inventory gate in section 8 treats this as incomplete
    # review-thread inventory and refuses to emit merge-ready
    # / clean-pass states. The ids of the incomplete threads
    # are exposed so the markdown report can list them.
    review_thread_comment_inventory_complete: bool = True
    review_thread_comment_inventory_error_count: int = 0
    review_thread_comment_incomplete_thread_ids: List[str] = []

    # Stop conditions
    stop_reason: Optional[str] = None

    # If ping is supplied, parse it for filtering. A malformed
    # --ping-created-at is a HARD error: do NOT silently fall back
    # to "no ping filter" because that would accept pre-ping Codex
    # clean-pass evidence as authoritative and could drive
    # MERGE_READY_AWAITING_HUMAN_AUTHORIZATION when the operator's
    # ping timestamp is broken. We track three states explicitly:
    #   - ping_timestamp_supplied: True if the operator passed a
    #     non-empty --ping-created-at on the CLI
    #   - ping_timestamp_valid: True if the supplied timestamp parsed
    #     cleanly, OR if no timestamp was supplied (in which case
    #     no filter is applied by design)
    #   - ping_dt: the parsed datetime, or None when no filter applies
    ping_dt: Optional[datetime] = None
    ping_timestamp_supplied: bool = bool(ping_created_at)
    ping_timestamp_valid: bool = True
    if ping_created_at:
        ping_dt = parse_iso_utc(ping_created_at)
        if ping_dt is None:
            ping_timestamp_valid = False
            api_errors.append(
                f"ping_created_at could not be parsed: {ping_created_at!r}; "
                "post-ping Codex evidence cannot be trusted. Correct "
                "the ping timestamp and re-run."
            )
        elif ping_dt.tzinfo is None or ping_dt.tzinfo.utcoffset(ping_dt) is None:
            # Naive datetime (no timezone) — reject. The
            # GitHub API returns all createdAt / submittedAt
            # timestamps with a trailing 'Z' (UTC), so a naive
            # ping timestamp would later raise
            # `TypeError: can't compare offset-naive and
            # offset-aware datetimes` during the post-ping
            # filter. The classifier must NOT crash; it must
            # fail closed at HOLD_CODEX_RESPONSE_PENDING and
            # refuse to compare against aware GitHub
            # timestamps. Repo policy: require explicit
            # timezone on the ping timestamp.
            ping_dt = None
            ping_timestamp_valid = False
            api_errors.append(
                f"ping_created_at has no timezone: {ping_created_at!r}; "
                "naive datetimes cannot be safely compared with "
                "aware GitHub timestamps. Add a Z or numeric "
                "offset (e.g. '2026-06-11T17:30:00Z' or "
                "'2026-06-11T17:30:00+00:00') and re-run."
            )

    # Round-69 Codex review 4769230169 (P2): advance the
    # review-thread cursor between audit polls. The
    # previous implementation called
    # ``_canonical_review_thread_inventory`` on every
    # poll with the same ``starting_cursor=None`` so PRs
    # with more than ``page_size`` review threads would
    # refetch the same first page on every poll and
    # ``review_thread_inventory_complete`` would stay
    # False until ``max_polls`` expired. Track the
    # last seen endCursor across polls and pass it as
    # the next poll's starting cursor. Reset on a fresh
    # classification cycle.
    pagination_cursor: Optional[str] = None
    # Round-69 Codex review 4769344844 (P1): accumulate
    # the review-thread page list across polls. Initialize
    # once before the polling loop so the aggregate
    # inventory reflects every page walked. Per-poll
    # resets for inventory completeness flags remain
    # unchanged; only the per-thread list is accumulated.
    accumulated_review_threads: List[Dict[str, Any]] = []
    for poll_idx in range(1, max_polls + 1):
        polls_used = poll_idx

        # ---- Per-poll state reset ----
        # The thread lists, inventory completeness flag, and
        # inventory error count must reflect ONLY the current
        # poll's snapshot, not accumulated state from earlier
        # polls. Stale entries from an earlier poll (e.g. an
        # unresolved thread that was resolved between polls)
        # would otherwise cause CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED
        # instead of MERGE_READY_AWAITING_HUMAN_AUTHORIZATION on
        # a fresh poll whose own inventory has zero unresolved
        # threads. api_errors are accumulated across polls so
        # the operator can see all failures, but the inventory
        # completeness and per-poll thread buckets are reset.
        #
        # We ALSO reset the terminal decision state
        # (final_status, stop_reason, recommendation) and the
        # clean-pass detection state. This is required so a
        # later successful poll can produce a fresh decision
        # instead of inheriting a stale HOLD_NEW_CODEX_THREAD
        # from an earlier poll that saw an active finding on
        # partial inventory. Without this reset, if poll 1
        # emitted final_status=HOLD_NEW_CODEX_THREAD with
        # stop_reason="active_finding_with_incomplete_inventory"
        # and poll 2 completed successfully with no active
        # threads and no clean pass, the loop would exhaust
        # with stop_reason still set from poll 1 and the
        # post-loop exhaustion fallback (HOLD_CODEX_RESPONSE_PENDING)
        # would be skipped.
        active_threads = []
        outdated_threads = []
        resolved_threads = []
        review_thread_inventory_complete = True
        review_thread_inventory_error_count = 0
        review_thread_inventory_last_error = ""
        issue_comment_inventory_complete = True
        issue_comment_inventory_error_count = 0
        issue_comment_inventory_last_error = ""
        review_submission_inventory_complete = True
        review_submission_inventory_error_count = 0
        review_submission_inventory_last_error = ""
        review_thread_comment_inventory_complete = True
        review_thread_comment_inventory_error_count = 0
        review_thread_comment_incomplete_thread_ids = []
        # Reset raw poll snapshots. The fetch helpers at
        # section 2 (issue comments), section 3 (reviews),
        # and section 4 (review threads) will repopulate
        # these on success. If a later poll's fetch fails,
        # the empty reset prevents reusing the previous
        # poll's stale raw data — which would otherwise
        # let stale clean-pass comments or stale review
        # findings drive merge-ready / HOLD_NEW_CODEX_THREAD
        # decisions from evidence the latest poll did not
        # actually observe.
        pr_issue_comments = []
        pr_reviews = []
        # Round-69 Codex review 4769344844 (P1): the
        # ``review_threads`` list is accumulated across
        # polls (see ``accumulated_review_threads``
        # above) so the aggregate inventory reflects
        # every page walked. Do NOT reset it to ``[]``
        # here. The local ``review_threads`` variable
        # below aliases the accumulator for the
        # downstream per-poll logic.
        # Reset terminal decision state.
        final_status = STATUS_HOLD_CODEX_PENDING
        recommendation = RECOMMENDATIONS[STATUS_HOLD_CODEX_PENDING]
        stop_reason = None
        # Reset clean-pass detection; the per-poll code in
        # section 6 will re-detect or leave it as False.
        clean_pass_detected = False
        clean_pass_comment_id = None
        clean_pass_review_id = None
        clean_pass_source = None
        clean_pass_at = None

        # ---- Pre-poll fail-closed gate: malformed ping timestamp ----
        # When the operator supplied --ping-created-at but it
        # could not be parsed, refuse to classify on this poll.
        # The classifier MUST NOT accept pre-ping Codex evidence
        # as authoritative when the operator's ping boundary is
        # broken. Break so the operator sees the explicit hold
        # state in the packet.
        if not ping_timestamp_valid:
            final_status = STATUS_HOLD_CODEX_PENDING
            recommendation = (
                "Supplied --ping-created-at could not be parsed; "
                "post-ping Codex evidence cannot be trusted. "
                "Correct the ping timestamp and re-run. See "
                "api_errors for the underlying parse failure."
            )
            stop_reason = "ping_timestamp_invalid"
            break

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
        # Normalize merge state across GraphQL and REST shapes.
        # Prefer mergeStateStatus / merge_state_status (GraphQL-style),
        # then mergeableState (REST lowercase), then mergeable (boolean).
        # All three forms flow through normalize_merge_state() so the
        # downstream decision logic can compare against canonical
        # uppercase values (CLEAN / BLOCKED / DIRTY / UNSTABLE / etc.).
        merge_state_status = normalize_merge_state(
            pr_data.get("mergeStateStatus")
            or pr_data.get("merge_state_status")
            or pr_data.get("mergeableState")
            or pr_data.get("mergeable_state")
            or pr_data.get("mergeable")
        )
        # REST does not expose reviewDecision; leave it None unless
        # the field is present (e.g. from a GraphQL fixture or jq shim).
        rd_value = pr_data.get("reviewDecision")
        if rd_value is None or rd_value == "":
            rd_value = pr_data.get("review_decision")
        review_decision = rd_value if rd_value not in (None, "") else None
        # Preserve the raw mergeable value for the packet; this is
        # REST's boolean-as-string indicator of whether the PR is
        # currently mergeable (separate from the canonical merge state).
        mergeable_raw = pr_data.get("mergeable")
        mergeable = mergeable_raw
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
        # PR-level issue comments are REQUIRED evidence for
        # merge readiness. A clean pass OR a newer finding may
        # live on a PR-level issue comment rather than on a
        # formal review submission. If this fetch fails, the
        # classifier MUST NOT emit merge-ready / clean-pass
        # states on this poll; the gate in section 8 enforces
        # fail-closed behavior. We still record the raw
        # raw_data=[] (cleared by the per-poll reset) so
        # upstream code can see the failure cleanly.
        ok_issue, issue_data, err_issue = gh_api_paginated(
            repo, f"issues/{pr_number}/comments", timeout=api_timeout,
        )
        if not ok_issue:
            issue_comment_inventory_complete = False
            issue_comment_inventory_error_count += 1
            issue_comment_inventory_last_error = err_issue
            api_errors.append(
                f"issue_comments: {err_issue}; PR-level issue-comment "
                "inventory is incomplete; classifier cannot trust a "
                "clean-pass / merge-ready decision from this poll."
            )
        else:
            pr_issue_comments = issue_data

        # ---- 3. Formal PullRequestReview submissions ----
        # Formal PullRequestReview submissions are REQUIRED
        # evidence for merge readiness. A newer formal Codex
        # review such as CHANGES_REQUESTED could be missed if
        # this fetch fails. If it fails, the classifier MUST
        # NOT emit merge-ready / clean-pass states on this
        # poll; the gate in section 8 enforces fail-closed
        # behavior.
        ok_rev, review_data, err_rev = gh_api_paginated(
            repo, f"pulls/{pr_number}/reviews", timeout=api_timeout,
        )
        if not ok_rev:
            review_submission_inventory_complete = False
            review_submission_inventory_error_count += 1
            review_submission_inventory_last_error = err_rev
            api_errors.append(
                f"reviews: {err_rev}; review-submission inventory is "
                "incomplete; classifier cannot trust a clean-pass / "
                "merge-ready decision from this poll."
            )
        else:
            pr_reviews = review_data

        # ---- 4. Review threads (resolution + outdated state) ----
        # The thread fetch is REQUIRED evidence. Any failure here is
        # treated as incomplete inventory: the empty/partial list is
        # never silently used for merge readiness decisions. The gate
        # in section 8 enforces fail-closed behavior. We still record
        # any partial data the function returned (e.g. visible page
        # on a hasNextPage response) so that confirmed findings on
        # that page can be surfaced as HOLD_NEW_CODEX_THREAD even
        # when inventory is incomplete.
        #
        # The fetch function returns a 4-tuple (ok, threads, err,
        # metadata). The metadata carries the nested-comment
        # pagination state — a thread whose nested `comments`
        # connection has `hasNextPage=true` exposes
        # review_thread_comment_inventory_complete=False so the
        # packet / markdown can show the specific surface that
        # failed. The unified inventory gate in section 8 treats
        # this as incomplete review-thread inventory and fails
        # closed at HOLD_CODEX_RESPONSE_PENDING.
        #
        # Round-69 Codex review 4764653534 (P2): use the
        # canonical shared-pagination wrapper instead of the
        # inline first-page-only GraphQL fetch. The wrapper
        # is defined above ``classify()`` (not below the
        # ``__main__`` guard) so direct CLI runs invoke the
        # shared paginator.
        # Round-69 Codex review 4768977809 (P2): the audit
        # does NOT walk additional pages within a single
        # poll. The classify() loop is the entity that polls
        # and each call to the inventory helper is a
        # single-page fetch. If ``hasNextPage=true`` the
        # helper returns ``ok=False`` so the next poll
        # iteration makes the next call with the cursor.
        owner, name = repo.split("/", 1)
        # Round-69 Codex review 4769640328 (P2): on
        # the first poll (or when the previous poll
        # had no starting cursor), call the helper
        # with ``do_walk=True`` so the cursor walk
        # happens within a single audit pass. The
        # one-shot controller path (``max_polls=1``)
        # then completes the inventory without
        # needing multiple poll iterations. The
        # multi-poll path also benefits: the first
        # poll completes the inventory and the
        # remaining polls re-verify.
        do_walk_this_poll = (
            pagination_cursor is None
        )
        ok_thr, thread_data, err_thr, thread_metadata = _canonical_review_thread_inventory(
            owner=owner, name=name, pr_number=pr_number,
            starting_cursor=pagination_cursor,
            do_walk=do_walk_this_poll,
        )
        if not ok_thr:
            api_errors.append(f"review_threads: {err_thr}")
            review_thread_inventory_complete = False
            review_thread_inventory_error_count += 1
            review_thread_inventory_last_error = err_thr
        # Round-69 Codex review 4769856466 (P2): the
        # helper can return ok_thr=True after walking
        # later outer pages while its metadata still
        # reports
        # review_thread_comment_inventory_complete=False
        # (e.g. a non-final page had
        # comments.pageInfo.hasNextPage=true). The
        # section 8 inventory gate only checks the
        # boolean flag, so on PRs with multiple
        # review-thread pages and a long thread on an
        # earlier page, the audit could emit
        # CODEX_CLEAN_PASS / merge-ready even though
        # later nested comments were never fetched.
        # Honor the metadata's explicit completeness
        # flags here so the gate fails closed when the
        # nested-comment inventory is incomplete.
        if ok_thr and not thread_metadata.get(
            "review_thread_comment_inventory_complete", False
        ):
            review_thread_inventory_complete = False
            review_thread_inventory_error_count += 1
            review_thread_inventory_last_error = (
                "review_thread_comment_inventory_incomplete"
            )
        if ok_thr and not thread_metadata.get(
            "review_thread_inventory_complete", False
        ):
            review_thread_inventory_complete = False
            review_thread_inventory_error_count += 1
            review_thread_inventory_last_error = (
                "review_thread_inventory_incomplete"
            )
        # Round-69 Codex reviews 4769289362 (P1),
        # 4769344844 (P1), and 4769487744 (P1):
        # the per-thread list is now handled by the
        # helper's internal do_walk loop, so the
        # accumulator simply mirrors the helper's
        # return value. When the helper returns
        # ok_thr=True (inventory complete after the
        # walk), reset the accumulator to the helper's
        # threads so a fresh first-page poll starts
        # clean and an old unresolved entry from a
        # previous poll is replaced by the latest
        # state. When the helper returns ok_thr=False
        # (inventory incomplete — walk capped or
        # errored), extend the accumulator with the
        # visible threads so the audit's
        # visible-blocker logic catches the
        # active-finding even when the cursor walker
        # fails.
        if ok_thr:
            accumulated_review_threads[:] = list(
                thread_data or []
            )
        else:
            accumulated_review_threads.extend(
                thread_data or []
            )
        review_threads = accumulated_review_threads
        # Round-69 Codex review 4769230169 (P2): advance
        # the cursor between polls. When the current poll
        # returns ``hasNextPage=true`` and the helper
        # exposes a ``review_thread_pagination_end_cursor``,
        # use it as the next poll's starting cursor so
        # the audit eventually walks every page of the
        # review-thread connection. When ``hasNextPage=false``
        # (inventory complete), reset the cursor to None
        # so the next fresh classification cycle starts at
        # the first page.
        _outer_end_cursor = thread_metadata.get(
            "review_thread_pagination_end_cursor"
        )
        if ok_thr and not thread_metadata.get(
            "review_thread_pagination_incomplete", False
        ):
            pagination_cursor = None
        else:
            pagination_cursor = _outer_end_cursor or pagination_cursor
        # Propagate the nested-comment inventory state from the
        # fetch metadata. The metadata flags are reported as-is;
        # the section 8 inventory gate will refuse merge-ready if
        # any surface is incomplete.
        review_thread_comment_inventory_complete = bool(
            thread_metadata.get(
                "review_thread_comment_inventory_complete", False
            )
        )
        review_thread_comment_inventory_error_count = int(
            thread_metadata.get(
                "review_thread_comment_inventory_error_count", 0
            ) or 0
        )
        review_thread_comment_incomplete_thread_ids = list(
            thread_metadata.get(
                "review_thread_comment_incomplete_thread_ids", []
            ) or []
        )

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

        # Determine latest response (newest by created/submitted timestamp).
        # Timestamps are read via timestamp_field() which supports BOTH
        # GraphQL camelCase (createdAt, submittedAt) AND REST snake_case
        # (created_at, submitted_at) shapes. Without this normalization,
        # a live REST response with created_at would have an empty
        # timestamp and would be silently skipped during ping filtering.
        def _iso(s: str) -> str:
            return s or ""

        latest_issue = None
        for c in codex_issue_comments:
            ts = _iso(timestamp_field(c, "createdAt", "created_at"))
            if ping_dt is not None:
                c_dt = parse_iso_utc(ts)
                if c_dt is None or c_dt < ping_dt:
                    continue
            # Round-43 fix: exclude Codex task-summary
            # issue-comments from the ``latest_issue``
            # selection. The task-summary shape (``###
            # Summary`` body prefix) is a coordination post
            # describing prior work, NOT a real Codex
            # response. When the only post-ping Codex
            # activity is a task-summary, picking it as
            # ``latest_issue`` would populate
            # ``latest_codex_response_type="issue_comment"``
            # with a non-empty id, which the readiness
            # verifier then treats as a present Codex
            # artifact (Round-39 invariant). The audit's
            # ``status`` is ``HOLD_CODEX_RESPONSE_PENDING``
            # in this case, so the verifier emits
            # ``REASON_CODEX_FAILED`` and the lifecycle
            # routes to ``BLOCKED`` instead of ``WAITING``,
            # telling the operator to fix a terminal
            # Codex failure even though no review verdict
            # has arrived yet.
            #
            # The predicate is shared with
            # ``check_pr_review_comments`` so the gate and
            # the audit agree on what counts as a task
            # summary. If the predicate is unavailable
            # (``_co_is_codex_task_summary_issue_comment``
            # is ``None``), fall back to treating every
            # issue-comment as substantive — the runtime
            # check at the call site gates on ``is not
            # None``, so the worst case here preserves the
            # Round-39/41 behavior.
            c_author = (
                (c.get("user") or {}).get("login", "")
                if isinstance(c.get("user"), dict) else ""
            )
            c_body = c.get("body", "") or ""
            if (
                _co_is_codex_task_summary_issue_comment is not None
                and _co_is_codex_task_summary_issue_comment(
                    c_author, "issue_comment", c_body
                )
            ):
                continue
            if latest_issue is None or ts > _iso(timestamp_field(latest_issue, "createdAt", "created_at")):
                latest_issue = c
        latest_review = None
        for r in codex_review_submissions:
            ts = _iso(timestamp_field(r, "submittedAt", "submitted_at", "createdAt", "created_at"))
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
            if latest_review is None or ts > _iso(timestamp_field(latest_review, "submittedAt", "submitted_at", "createdAt", "created_at")):
                latest_review = r
            if ts > (last_seen_codex_review_ts or ""):
                last_seen_codex_review_ts = ts
                last_seen_codex_review_id = str(r.get("id", ""))

        # Track last-seen Codex activity even if filtered out
        for c in codex_issue_comments:
            ts = _iso(timestamp_field(c, "createdAt", "created_at"))
            if ts > (last_seen_codex_comment_ts or ""):
                last_seen_codex_comment_ts = ts
                last_seen_codex_comment_id = str(c.get("id", ""))

        # Pick the newer of the two surfaces
        candidates = []
        if latest_issue is not None:
            candidates.append((
                _iso(timestamp_field(latest_issue, "createdAt", "created_at")),
                "issue_comment",
                str(latest_issue.get("id", "")),
            ))
        if latest_review is not None:
            candidates.append((
                _iso(timestamp_field(latest_review, "submittedAt", "submitted_at", "createdAt", "created_at")),
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
        # Round-61: track review IDs whose inline
        # comments carry findings. Declared at this
        # scope (not inside the ``if`` block below)
        # so the post-clean-pass scan at line ~1986
        # can consult it even when no clean pass
        # was detected from the formal-review path.
        review_ids_with_inline: set = set()
        # Round-26 hardening: PR-level issue comments do not
        # carry a commit anchor in GitHub's data model (REST
        # /repos/{owner}/{repo}/issues/{n}/comments returns no
        # ``commit_oid``). A clean-pass issue comment therefore
        # cannot be matched to ``expected_head_sha`` by the API
        # alone, and accepting it without a head-binding
        # evidence let Codex clean passes from a prior head
        # satisfy the Codex gate for the current head (P1
        # ``PRRC_kwDOSHFpYM7XvLCB``). The ping window alone is
        # not sufficient when the operator runs ``status`` /
        # ``merge`` without a fresh ping (``ping_dt is None``):
        # the entire PR timeline is then accepted.
        #
        # Round-27 hardening (P1 ``PRRC_kwDOSHFpYM7XvfoW``):
        # merely checking "any head-bound formal review exists"
        # is insufficient. A current-head non-clean formal
        # review (a finding) must NOT authorize a stale
        # issue-comment clean pass. The head-binding surface
        # must itself be a CLEAN review — a formal review on
        # ``expected_head_sha`` whose body carries the
        # canonical clean-pass phrase — so the issue comment
        # is a post-clean echo of the current head's clean
        # verdict, not a stale artifact. The issue comment
        # must additionally post-date that clean head-bound
        # review timestamp; otherwise the comment predates
        # the clean review and cannot be an echo of it.
        #
        # Round-46 hardening (P1 ``PRRC_kwDOSHFpYM7XPZN5``):
        # comparing the issue comment ONLY against the
        # latest CLEAN head-bound formal review is
        # insufficient. The latest OVERALL head-bound
        # formal review (clean or non-clean) is the
        # authoritative current-head Codex surface. If
        # the latest head-bound formal review is a
        # non-clean finding, no later issue-comment
        # clean-pass text can authorize a clean-pass
        # verdict on the current head — the finding
        # is the controlling Codex surface, and the
        # issue comment is a stale echo of an earlier
        # clean review that has since been superseded.
        # The fix: also track the timestamp AND body
        # of the latest head-bound formal review, and
        # reject issue-comment clean-pass texts when
        # the latest head-bound formal review is
        # non-clean.
        latest_head_bound_clean_review_ts = ""
        latest_head_bound_formal_review_ts = ""
        latest_head_bound_formal_review_is_clean = True
        latest_head_bound_formal_review_body = ""
        for r in codex_review_submissions:
            rev_commit = extract_review_commit_oid(r)
            if (
                not rev_commit
                or not expected_head_sha
                or rev_commit != expected_head_sha
            ):
                continue
            ts = timestamp_field(
                r, "submittedAt", "submitted_at",
                "createdAt", "created_at",
            )
            body_value = (r.get("body", "") or "")
            is_clean = is_codex_clean_pass_comment(body_value)
            # Track the latest head-bound formal review
            # of ANY kind. A later non-clean formal
            # review (Round-46 bug) is the controlling
            # current-head Codex surface, not the
            # earlier clean review.
            if ts > latest_head_bound_formal_review_ts:
                latest_head_bound_formal_review_ts = ts
                latest_head_bound_formal_review_is_clean = is_clean
                latest_head_bound_formal_review_body = body_value
            if not is_clean:
                # Round-27 fail-closed: a non-clean formal
                # review on the current head is a finding,
                # not a clean-pass authority.
                continue
            if ts > latest_head_bound_clean_review_ts:
                latest_head_bound_clean_review_ts = ts
        for c in codex_issue_comments:
            if not is_codex_clean_pass_comment(c.get("body", "")):
                continue
            # Use timestamp_field() to read BOTH GraphQL camelCase
            # (createdAt) and REST snake_case (created_at). Without
            # this, REST responses with created_at would be filtered
            # out by the ping_dt comparison and the clean pass would
            # be silently dropped.
            ts = timestamp_field(c, "createdAt", "created_at")
            if ping_dt is not None:
                c_dt = parse_iso_utc(ts)
                if c_dt is None or c_dt < ping_dt:
                    continue
            # Round-27 fail-closed head binding: when the
            # operator did not supply a ping boundary, the
            # issue-comment path is the ONLY head-binding
            # surface. The head-binding surface must be a
            # CLEAN formal review on ``expected_head_sha``,
            # and the issue comment must postdate it. A
            # current-head non-clean formal review (a
            # finding) does NOT authorize the issue comment
            # — and a comment that predates the clean
            # review cannot be its echo.
            if ping_dt is None:
                if not latest_head_bound_clean_review_ts:
                    continue
                c_dt = parse_iso_utc(ts)
                r_dt = parse_iso_utc(latest_head_bound_clean_review_ts)
                if c_dt is None or r_dt is None or c_dt < r_dt:
                    continue
            # Round-46 + Round-47 fix: the latest
            # OVERALL head-bound formal review veto.
            # When the latest head-bound formal review
            # is a non-clean finding AND a clean
            # head-bound formal review exists AND the
            # issue comment postdates that clean
            # review, the issue comment is a stale
            # echo of the clean review (a non-clean
            # review has been posted in between) and
            # MUST NOT be accepted as a clean-pass
            # authority. The current-head Codex
            # surface is the finding, not the earlier
            # clean review.
            #
            # The veto MUST run regardless of
            # whether a ping boundary is supplied
            # (Round-47 correction: Round-46 had
            # the veto inside the ``ping_dt is
            # None`` block, which left the
            # post-ping path unchecked). But the
            # veto MUST also be scoped to the
            # candidate-echo case: it only fires
            # when ``latest_head_bound_clean_
            # review_ts`` is non-empty (a clean
            # head-bound formal review exists to
            # echo) AND the comment postdates that
            # clean review. When no clean
            # head-bound formal review exists, the
            # issue comment IS the clean pass (not
            # an echo), and the existing
            # ``newer_finding_after_clean_pass``
            # scan handles later findings.
            if (
                latest_head_bound_formal_review_ts
                and not latest_head_bound_formal_review_is_clean
                and latest_head_bound_clean_review_ts
            ):
                c_dt_check = parse_iso_utc(ts)
                r_dt_check = parse_iso_utc(
                    latest_head_bound_clean_review_ts
                )
                if (
                    c_dt_check is not None
                    and r_dt_check is not None
                    and c_dt_check >= r_dt_check
                ):
                    # The issue comment is a
                    # candidate post-clean echo,
                    # and a later non-clean
                    # formal review (the latest
                    # head-bound formal review) is
                    # in flight. Reject the
                    # comment as a stale echo.
                    continue
            if latest_clean_pass is None or ts > timestamp_field(latest_clean_pass, "createdAt", "created_at"):
                latest_clean_pass = c
        # If no issue-comment clean pass exists, scan ALL post-ping
        # Codex formal review submissions for a clean pass. We must
        # not only check `latest_review`, because Codex may first
        # clean-pass via a formal review and then submit a later
        # non-clean review on the same head — in that case
        # `latest_review` points at the later non-clean one and the
        # earlier clean pass would be missed, the
        # `newer_finding_after_clean_pass` scan below would be
        # skipped, and the classifier would return
        # `HOLD_CODEX_RESPONSE_PENDING` instead of
        # `HOLD_NEW_CODEX_THREAD` (Codex finding 5,
        # PRRT_kwDOSHFpYM6JWe1P, 3408523177).
        #
        # Apply the same expected-head commit-scope filter used by
        # the `latest_review` selection path above: reviews anchored
        # to a different commit than `expected_head_sha` are stale
        # Codex surfaces from a prior head and must NOT be accepted
        # as the current-head clean pass. Reviews with no commit_oid
        # (legacy / GitHub-emitted without a commit anchor) are kept
        # as authoritative — same convention as `latest_review`.
        # Round-62 fix (Finding 2): scan inline
        # comments for ALL summary-format reviews,
        # not just those considered as clean-pass
        # candidates. The Round-60/61 fix only
        # scanned inline comments inside the
        # ``if latest_clean_pass is None and
        # codex_review_submissions`` block. When
        # ``latest_clean_pass`` was already set
        # from a Codex issue-comment clean pass,
        # the formal-review scan was skipped and
        # the inline-comment fetch never ran. A
        # newer summary review with inline
        # findings would then be treated as clean
        # by the post-clean-pass scan, allowing
        # ``MERGE_READY_AWAITING_HUMAN_AUTHORIZATION``
        # despite a newer inline finding.
        #
        # This pre-pass scans every summary-format
        # review and records those with inline
        # comments in ``review_ids_with_inline``.
        # The post-clean-pass scan then consults
        # the set to treat such reviews as newer
        # findings (Round-61).
        for r in codex_review_submissions:
            body_value = r.get("body", "") or ""
            if not is_codex_review_summary(body_value):
                continue
            review_id = r.get("id")
            if not review_id:
                continue
            inline_ok, inline_comments, inline_err = (
                _fetch_review_inline_comments_with_pr(
                    repo, pr_number, review_id
                )
            )
            if not inline_ok:
                # Fetch failed — record the
                # review as having a finding (the
                # review cannot be proven clean
                # without the inline surface).
                # We add it to
                # ``review_ids_with_inline`` so the
                # post-clean-pass scan treats it
                # as a finding AND also record it
                # in ``api_errors`` for
                # observability.
                api_errors.append(
                    f"review_inline_comments:{review_id}:{inline_err}"
                )
                review_ids_with_inline.add(str(review_id))
                continue
            if inline_comments:
                review_ids_with_inline.add(str(review_id))
        # State is restricted to APPROVED or COMMENTED; bodies must
        # contain the canonical clean-pass phrase. Ping-window
        # filtering is applied so old pre-ping clean passes are not
        # accepted. The most recent qualifying review is selected as
        # the clean-pass reference.
        if latest_clean_pass is None and codex_review_submissions:
            latest_formal_clean_pass = None
            latest_formal_clean_pass_ts = ""
            for r in codex_review_submissions:
                state_value = (r.get("state") or "").upper()
                body_value = r.get("body", "") or ""
                if state_value not in ("APPROVED", "COMMENTED"):
                    continue
                # Round-52: accept the older exact clean
                # phrase OR the newer ``### 💡 Codex Review``
                # summary format with no inline-finding
                # marker in the body itself. The summary
                # body itself never carries inline
                # findings (those live in separate inline
                # review comments), so this is a safe
                # match for the clean case. A newer
                # summary-format finding review will be
                # caught by the ``newer_finding_after_
                # clean_pass`` scan below because its
                # body is NOT the exact clean phrase
                # AND not the summary-with-clean prefix.
                is_clean_phrase = is_codex_clean_pass_comment(body_value)
                is_summary_format = (
                    is_codex_review_summary(body_value)
                    and not any(
                        is_codex_finding_body(line)
                        for line in body_value.splitlines()
                    )
                )
                if not (is_clean_phrase or is_summary_format):
                    continue
                # Round-60/62 fix: skip summary
                # reviews with inline comments as
                # clean-pass candidates. The pre-pass
                # above (Round-62) populated
                # ``review_ids_with_inline`` for all
                # summary reviews with inline
                # findings. Here we consult that set.
                if is_summary_format:
                    r_id_str = str(r.get("id", ""))
                    if r_id_str in review_ids_with_inline:
                        continue
                # Round-54 fix: summary-format reviews
                # carry their findings in inline review
                # comments, NOT in the summary body.
                # The ``body_value.splitlines()`` check
                # above only inspects the summary body.
                # If the review has an active Codex-bot
                # inline thread anchored to the same
                # head, the summary review carries a
                # finding and MUST NOT be accepted as a
                # clean pass. This prevents the audit
                # from recording ``clean_pass_detected``
                # when an inline P1/P2 finding exists
                # but the thread was later resolved /
                # outdated or absent from the inventory.
                # The active_threads inventory is
                # populated AFTER this loop (section 7),
                # so we do a targeted inline scan here:
                # walk ``review_threads`` for any active
                # (non-outdated, non-resolved) Codex-bot
                # thread anchored to this head. If such
                # a thread exists, this summary review
                # carries a finding. The check is
                # restricted to active (non-outdated)
                # threads so already-outdated threads
                # from prior heads do not invalidate a
                # current-head clean pass.
                # Round-60 inline-comment fetch is
                # done in the pre-pass above (Round-62
                # fix) so it runs for ALL summary
                # reviews regardless of clean-pass
                # source. Here we only check the
                # ``review_ids_with_inline`` set to
                # skip the review as a clean
                # candidate.
                # Round-54 + Round-56 + Round-57 +
                # Round-59 fix: veto summary-format
                # reviews whenever the associated
                # Codex inline finding exists AND is
                # anchored to the SAME commit as the
                # review being evaluated AND is NOT yet
                # resolved.
                # Round-54 originally only checked
                # active threads. Round-56 extended
                # to resolved threads but was too
                # aggressive. Round-57 tied the thread
                # to the review's commit anchor.
                # Round-59 excluded resolved threads
                # from the veto (they've been
                # addressed).
                # Outdated threads (explicitly marked
                # as anchored to a prior head no longer
                # reachable from the current head) are
                # excluded so they don't invalidate a
                # current-head clean pass.
                if is_summary_format and review_threads:
                    review_commit = extract_review_commit_oid(r)
                    has_same_anchor_thread = False
                    if review_commit:
                        for t in review_threads:
                            if (
                                not bool(t.get("is_outdated", False))
                                and not bool(t.get("is_resolved", False))
                                and t.get("author", "") in CODEX_BOT_LOGINS
                                and (
                                    t.get("original_commit_sha", "")
                                    == review_commit
                                )
                            ):
                                has_same_anchor_thread = True
                                break
                    if has_same_anchor_thread:
                        # Summary review carries an
                        # active (unresolved) inline
                        # finding on THIS commit. Skip
                        # it as a clean pass candidate.
                        continue
                ts = timestamp_field(
                    r, "submittedAt", "submitted_at", "createdAt", "created_at"
                )
                if ping_dt is not None:
                    r_dt = parse_iso_utc(ts)
                    if r_dt is None or r_dt < ping_dt:
                        continue
                # Same expected-head commit-scope filter as
                # `latest_review` and the `newer_finding_after_
                # clean_pass` scan below. Reviews with no
                # commit_oid are kept as authoritative (legacy /
                # GitHub-emitted without a commit anchor).
                rev_commit = extract_review_commit_oid(r)
                if (
                    rev_commit
                    and expected_head_sha
                    and rev_commit != expected_head_sha
                ):
                    # Stale review anchored to a different commit
                    # than expected_head_sha. Ignore for
                    # formal-review clean-pass detection.
                    continue
                if (
                    latest_formal_clean_pass is None
                    or ts > latest_formal_clean_pass_ts
                ):
                    latest_formal_clean_pass = r
                    latest_formal_clean_pass_ts = ts
            if latest_formal_clean_pass is not None:
                clean_pass_detected = True
                clean_pass_review_id = latest_formal_clean_pass.get("id")
                clean_pass_source = "pull_request_review"
                clean_pass_at = latest_formal_clean_pass_ts
        if latest_clean_pass is not None:
            clean_pass_detected = True
            clean_pass_comment_id = latest_clean_pass.get("databaseId") or latest_clean_pass.get("id")
            clean_pass_source = "issue_comment"
            clean_pass_at = timestamp_field(latest_clean_pass, "createdAt", "created_at")

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
                # Round-4 follow-up (Codex review 4724091490 on
                # ``a8ccd9b``): carry the per-thread participant
                # list into the rebuilt ``entry`` dict. Without this
                # field the eligibility check sees ``comments=[]``
                # and reports every reply as bot-authored even when
                # a human reply is present in the same thread.
                "comments": list(t.get("comments") or []),
                # Round-4 fix (Codex review 4724016076 on
                # ``67d68ec``): carry the canonical commit anchor
                # into the rebuilt ``entry`` dict so downstream
                # consumers (``classify()``'s callers, the
                # controller's ``normalize_thread_anchor``) can
                # see the SHA GitHub's review-thread API supplied.
                # Without this field, every live entry arrives
                # anchorless and the eligibility check reports
                # ``missing_commit_anchor`` for every otherwise
                # eligible outdated Codex thread.
                "original_commit_sha": (
                    t.get("original_commit_sha")
                    if isinstance(t.get("original_commit_sha"), str)
                    and len(t.get("original_commit_sha")) == 40
                    and all(
                        c in "0123456789abcdef"
                        for c in t.get("original_commit_sha")
                    )
                    else None
                ),
                # True iff the parent thread's nested
                # comments pageInfo hasNextPage=true.
                # Surfaced in markdown and packet so
                # operators can tell which findings came
                # from partial nested evidence.
                "nested_incomplete": bool(
                    t.get("nested_incomplete", False)
                ),
            }
            if entry["is_resolved"]:
                resolved_threads.append(entry)
            elif entry["is_outdated"]:
                outdated_threads.append(entry)
            else:
                active_threads.append(entry)

        # ---- 8. Decide ----
        # Order of precedence:
        # 0) Review-thread inventory incomplete -> fail closed
        #    (HOLD_NEW_CODEX_THREAD if a confirmed active finding
        #    exists, else HOLD_CODEX_RESPONSE_PENDING)
        # 1) Codex raised a current-head active finding (thread) -> HOLD_NEW_CODEX_THREAD
        # 2) Codex clean-pass exists AND a newer Codex finding exists
        #    after it -> HOLD_NEW_CODEX_THREAD
        # 3) Codex clean-pass exists AND no newer active finding -> resolve-only or merge-ready
        # 4) Otherwise -> HOLD_CODEX_RESPONSE_PENDING

        # Round-412 (FINAL direct-CLI micro-repair): the
        # local fallback uses ``_local_codex_login_fallback``
        # which is type-safe (rejects non-string values) and
        # case-insensitive (delegates to the canonical
        # ``is_codex_login`` when available, otherwise uses
        # the precomputed ``_LOCAL_CODEX_LOGINS_LOWER`` set).
        # The previous inline fallback
        # ``(t.get("author", "") or "").lower() in {...}``
        # raised ``AttributeError`` on truthy non-string
        # authors.
        has_active_blocker = any(
            _shared_is_codex_login(t.get("author", ""))
            if _shared_is_codex_login is not None
            else _local_codex_login_fallback(t.get("author", ""))
            for t in active_threads
        )
        # If a clean pass exists, we also need to check whether any NEWER
        # Codex comment/review (with a real finding) arrived after it.
        newer_finding_after_clean_pass = False
        if clean_pass_detected and clean_pass_at:
            cp_dt = parse_iso_utc(clean_pass_at)
            for c in codex_issue_comments:
                c_dt = parse_iso_utc(timestamp_field(c, "createdAt", "created_at"))
                if c_dt is None or cp_dt is None or c_dt <= cp_dt:
                    continue
                # Round-41 fix: exclude Codex task-summary
                # issue-comments (``### Summary`` body prefix)
                # from the post-clean-pass newer-finding scan.
                # These are coordination posts that describe
                # work the Codex bot performed (e.g. an
                # earlier repair round) and may incidentally
                # contain blocking-vocabulary tokens while
                # describing prior fixes. They are NOT
                # substantive findings and must NOT
                # downgrade a valid current-head clean pass
                # to ``HOLD_NEW_CODEX_THREAD``. The predicate
                # is shared with ``check_pr_review_comments``
                # so the gate and the audit agree.
                c_author = (
                    (c.get("user") or {}).get("login", "")
                    if isinstance(c.get("user"), dict) else ""
                )
                c_body = c.get("body", "") or ""
                if (
                    _co_is_codex_task_summary_issue_comment is not None
                    and _co_is_codex_task_summary_issue_comment(
                        c_author, "issue_comment", c_body
                    )
                ):
                    continue
                # Any post-clean-pass Codex issue comment other than
                # another clean pass is treated as a finding.
                if not is_codex_clean_pass_comment(c_body):
                    newer_finding_after_clean_pass = True
                    break
            if not newer_finding_after_clean_pass:
                for r in codex_review_submissions:
                    r_dt = parse_iso_utc(timestamp_field(r, "submittedAt", "submitted_at", "createdAt", "created_at"))
                    if r_dt is None or cp_dt is None or r_dt <= cp_dt:
                        continue
                    # Apply the same expected-head commit-scope
                    # filter used by the `latest_review`
                    # selection path above. A formal review
                    # anchored to a different commit than
                    # `expected_head_sha` is a stale Codex
                    # surface from a prior head and must
                    # NOT downgrade a valid current-head
                    # clean pass to HOLD_NEW_CODEX_THREAD.
                    # Reviews with no commit_id (legacy /
                    # GitHub-emitted without a commit
                    # anchor) are kept as authoritative —
                    # same convention as `latest_review`.
                    rev_commit = extract_review_commit_oid(r)
                    if (
                        rev_commit
                        and expected_head_sha
                        and rev_commit != expected_head_sha
                    ):
                        # Stale review on a different
                        # commit: ignore for the
                        # newer-finding scan. Do NOT mark
                        # `newer_finding_after_clean_pass`.
                        continue
                    body = r.get("body", "") or ""
                    state_v = (r.get("state") or "").upper()
                    if state_v in ("CHANGES_REQUESTED", "REQUEST_CHANGES"):
                        newer_finding_after_clean_pass = True
                        break
                    if state_v in ("APPROVED", "COMMENTED"):
                        # Round-52: a newer review is a
                        # finding iff its body is NEITHER
                        # the exact clean phrase NOR the
                        # summary-with-clean format. A
                        # summary-format review whose
                        # body contains an inline-finding
                        # marker (rare; findings usually
                        # live in inline review comments,
                        # not in the summary body) is also
                        # a finding.
                        is_clean_phrase = is_codex_clean_pass_comment(body)
                        is_summary_clean = (
                            is_codex_review_summary(body)
                            and not any(
                                is_codex_finding_body(line)
                                for line in body.splitlines()
                            )
                        )
                        # Round-61 fix: if the review
                        # has inline comments (recorded
                        # by the Round-60 fetch), it is a
                        # finding even when the body is
                        # summary-clean. The post-clean-
                        # pass scan MUST consult
                        # ``review_ids_with_inline`` so a
                        # newer summary review with
                        # inline findings correctly
                        # downgrades a current-head clean
                        # pass to HOLD_NEW_CODEX_THREAD.
                        r_id_str = str(r.get("id", ""))
                        if r_id_str and r_id_str in review_ids_with_inline:
                            newer_finding_after_clean_pass = True
                            break
                        if not (is_clean_phrase or is_summary_clean):
                            newer_finding_after_clean_pass = True
                            break

        # ---- Inventory completeness gate (fail closed per poll) ----
        # All three Codex response surfaces are REQUIRED
        # evidence: PR-level issue comments, formal review
        # submissions, and review threads. If the CURRENT
        # poll's inventory for any of them is incomplete
        # (REST or GraphQL failed, response had errors,
        # shape missing expected data, hasNextPage=true, JSON
        # parse failed, etc.), the classifier MUST NOT emit
        # CODEX_CLEAN_PASS, CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED,
        # or MERGE_READY_AWAITING_HUMAN_AUTHORIZATION on this
        # poll. The only safe per-poll states are
        # HOLD_NEW_CODEX_THREAD (when an active finding is
        # already confirmed in the partial inventory) or
        # HOLD_CODEX_RESPONSE_PENDING (when we cannot trust the
        # data). We DO NOT break here: later polls may succeed
        # and yield a clean classification based on their own
        # fresh inventory.
        #
        # Determine which surfaces are incomplete on this poll.
        incomplete_surfaces: List[str] = []
        if not issue_comment_inventory_complete:
            incomplete_surfaces.append("issue_comment")
        if not review_submission_inventory_complete:
            incomplete_surfaces.append("review_submission")
        if not review_thread_inventory_complete:
            incomplete_surfaces.append("review_thread")
        if incomplete_surfaces:
            # When ANY required surface is incomplete on this
            # poll, the classifier fails closed. A confirmed
            # active finding (visible on the partial inventory
            # we did fetch) still drives HOLD_NEW_CODEX_THREAD;
            # otherwise the safe per-poll state is
            # HOLD_CODEX_RESPONSE_PENDING.
            if has_active_blocker or newer_finding_after_clean_pass:
                final_status = STATUS_HOLD_NEW_THREAD
                recommendation = (
                    RECOMMENDATIONS[STATUS_HOLD_NEW_THREAD]
                    + " (Note: required Codex response-surface "
                    "inventory is also incomplete: "
                    + ", ".join(incomplete_surfaces)
                    + "; some findings may not have been seen. "
                    "Inspect api_errors and re-run.)"
                )
                stop_reason = "active_finding_with_incomplete_inventory"
                # Continue to next poll instead of breaking, so
                # a later successful poll can override the
                # classification with a fresh inventory.
                if poll_idx < max_polls:
                    time.sleep(poll_seconds)
                    continue
                break
            final_status = STATUS_HOLD_CODEX_PENDING
            recommendation = (
                "Required Codex response-surface inventory is "
                "incomplete (surfaces: "
                + ", ".join(incomplete_surfaces)
                + "); merge readiness cannot be trusted until "
                "all surfaces are complete. Re-run later with a "
                "fresh budget. See api_errors for the underlying "
                "failures."
            )
            stop_reason = "inventory_incomplete"
            # Continue to next poll instead of breaking, so a
            # later successful poll can override the
            # classification with a fresh inventory.
            if poll_idx < max_polls:
                time.sleep(poll_seconds)
                continue
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

    # If we exited the loop without a stop_reason, polling is
    # exhausted without making a classification. This is the
    # canonical exhaustion fallback. Note that with the
    # per-poll state reset, this branch is reachable: a prior
    # poll may have set stop_reason (e.g. via the inventory
    # gate) but the current poll's reset cleared it; if the
    # current poll also made no decision, we fall through to
    # here and emit the correct HOLD_CODEX_RESPONSE_PENDING
    # exhaustion state.
    if stop_reason is None:
        polling_exhausted = True
        final_status = STATUS_HOLD_CODEX_PENDING
        recommendation = RECOMMENDATIONS[STATUS_HOLD_CODEX_PENDING]
        stop_reason = "polling_exhausted_no_codex_response"

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
        "ping_timestamp_supplied": ping_timestamp_supplied,
        "ping_timestamp_valid": ping_timestamp_valid,
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
        # Review-thread inventory completeness. Required evidence:
        # when incomplete the classifier has already failed closed in
        # section 8 and refused to emit merge-ready states.
        "review_thread_inventory_complete": review_thread_inventory_complete,
        "review_thread_inventory_error_count": review_thread_inventory_error_count,
        "review_thread_inventory_last_error": review_thread_inventory_last_error,
        # Nested review-thread comment (pageInfo.hasNextPage
        # on the per-thread `comments` connection) inventory
        # completeness. When a thread has more than 50 comments
        # and the Codex-authored finding is on a later page,
        # this flag is False and the unified inventory gate in
        # section 8 forces HOLD_CODEX_RESPONSE_PENDING. The
        # thread ids whose nested pagination is incomplete are
        # exposed so the markdown report can list them.
        "review_thread_comment_inventory_complete": review_thread_comment_inventory_complete,
        "review_thread_comment_inventory_error_count": review_thread_comment_inventory_error_count,
        "review_thread_comment_incomplete_thread_ids": list(
            review_thread_comment_incomplete_thread_ids
        ),
        # Issue-comment inventory completeness. Required evidence:
        # PR-level issue comments may carry the Codex clean pass
        # OR a newer Codex finding. When incomplete the classifier
        # has already failed closed in section 8.
        "issue_comment_inventory_complete": issue_comment_inventory_complete,
        "issue_comment_inventory_error_count": issue_comment_inventory_error_count,
        "issue_comment_inventory_last_error": issue_comment_inventory_last_error,
        # Review-submission inventory completeness. Required
        # evidence: a newer formal Codex review (e.g.
        # CHANGES_REQUESTED) may live on a formal review
        # submission. When incomplete the classifier has
        # already failed closed in section 8.
        "review_submission_inventory_complete": review_submission_inventory_complete,
        "review_submission_inventory_error_count": review_submission_inventory_error_count,
        "review_submission_inventory_last_error": review_submission_inventory_last_error,
        "polls_used": polls_used,
        "polling_exhausted": polling_exhausted,
        "stop_reason": stop_reason,
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

    # PR state comparison must be case-insensitive: live REST
    # returns `state="open"` (lowercase) while GraphQL returns
    # `state="OPEN"` (uppercase). A case-sensitive equality
    # check against `"OPEN"` would emit a misleading
    # "not OPEN" warning on every open PR fetched from
    # REST. We normalize via `.upper()` so the warning
    # is reserved for genuinely non-open states (`"CLOSED"`,
    # `"MERGED"`, etc.). The packet field `pr_state` is
    # left untouched; this normalization is purely for
    # markdown rendering.
    pr_state = packet.get("pr_state", "")
    pr_state_normalized = pr_state.upper() if pr_state else ""
    if pr_state and pr_state_normalized != "OPEN":
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

    # Surface ping timestamp status. When the operator supplied
    # --ping-created-at but it could not be parsed, the
    # classifier refused to accept any post-ping Codex evidence
    # and held at HOLD_CODEX_RESPONSE_PENDING.
    lines.append("## Ping timestamp\n")
    ping_supplied = packet.get("ping_timestamp_supplied", False)
    ping_valid = packet.get("ping_timestamp_valid", True)
    if not ping_supplied:
        lines.append("- **--ping-created-at:** _(not supplied; no ping filter applied)_\n")
    elif ping_valid:
        lines.append(f"- **--ping-created-at:** `{packet.get('ping_created_at', '')}`  ")
        lines.append("- **Parsed cleanly:** ✅  \n")
    else:
        lines.append(f"- **--ping-created-at:** `{packet.get('ping_created_at', '')}`  ")
        lines.append("- **Parsed cleanly:** ❌ (classifier failed closed; "
                     "post-ping Codex evidence was NOT trusted)\n")

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

    # Surface review-thread inventory completeness. When the
    # GraphQL review-thread fetch is incomplete the classifier has
    # already failed closed and refused to emit merge-ready states.
    # The same fail-closed rule applies to PR-level issue comments
    # and to formal review submissions — all three Codex response
    # surfaces are required evidence. The section header
    # `## Review-thread inventory` is preserved for downstream
    # tooling and the existing operator scripts that grep for it;
    # the new surfaces are listed alongside the review-thread
    # inventory under the same header so the legacy
    # `## Review-thread inventory` substring is always present in
    # the rendered markdown.
    lines.append("## Review-thread inventory\n")
    issue_complete = packet.get("issue_comment_inventory_complete", True)
    issue_err_count = packet.get("issue_comment_inventory_error_count", 0) or 0
    issue_last_err = packet.get("issue_comment_inventory_last_error", "") or ""
    if issue_complete:
        lines.append(
            "- **Issue-comment inventory complete:** ✅ "
            "(PR-level /issues/{n}/comments fetched and validated)\n"
        )
    else:
        lines.append(
            "- **Issue-comment inventory complete:** ❌ "
            "(classifier failed closed; merge readiness cannot be "
            "trusted)\n"
        )
        lines.append(f"  - **Error count:** `{issue_err_count}`")
        if issue_last_err:
            lines.append(f"  - **Last error:** `{issue_last_err}`")
        lines.append("")
    rev_complete = packet.get("review_submission_inventory_complete", True)
    rev_err_count = packet.get("review_submission_inventory_error_count", 0) or 0
    rev_last_err = packet.get("review_submission_inventory_last_error", "") or ""
    if rev_complete:
        lines.append(
            "- **Review-submission inventory complete:** ✅ "
            "(formal /pulls/{n}/reviews fetched and validated)\n"
        )
    else:
        lines.append(
            "- **Review-submission inventory complete:** ❌ "
            "(classifier failed closed; merge readiness cannot be "
            "trusted)\n"
        )
        lines.append(f"  - **Error count:** `{rev_err_count}`")
        if rev_last_err:
            lines.append(f"  - **Last error:** `{rev_last_err}`")
        lines.append("")
    inv_complete = packet.get("review_thread_inventory_complete", True)
    inv_err_count = packet.get("review_thread_inventory_error_count", 0) or 0
    inv_last_err = packet.get("review_thread_inventory_last_error", "") or ""
    # Nested review-thread comment (per-thread `comments`
    # pageInfo.hasNextPage) inventory state. The new
    # nested-comments check is required evidence: a thread
    # with more than 50 comments could have a Codex finding
    # on a later comments page.
    nested_complete = packet.get(
        "review_thread_comment_inventory_complete", True
    )
    nested_err_count = packet.get(
        "review_thread_comment_inventory_error_count", 0
    ) or 0
    nested_incomplete_ids = packet.get(
        "review_thread_comment_incomplete_thread_ids", []
    ) or []
    if inv_complete:
        if nested_complete:
            lines.append(
                "- **Inventory complete:** ✅ (all review-thread pages fetched and validated; all nested review-thread comments fetched and validated)\n"
            )
        else:
            lines.append(
                "- **Inventory complete:** ❌ (top-level review-thread pages are complete, but "
                f"{nested_err_count} thread(s) have nested comments with hasNextPage=true; "
                "the classifier failed closed; merge readiness cannot be trusted)\n"
            )
            if nested_incomplete_ids:
                lines.append(
                    "  - **Incomplete threads:** "
                    + ", ".join(str(t) for t in nested_incomplete_ids[:5])
                    + "\n"
                )
    else:
        lines.append(
            "- **Inventory complete:** ❌ (classifier failed closed; "
            "merge readiness cannot be trusted)\n"
        )
        lines.append(f"- **Inventory error count:** `{inv_err_count}`  ")
        if inv_last_err:
            lines.append(f"- **Last inventory error:** `{inv_last_err}`\n")
        else:
            lines.append("")
    # Surface the actual lifecycle status the classifier
    # emitted on this poll, NOT a hardcoded "pending"
    # string. After the prior fix the classifier can
    # legitimately return HOLD_NEW_CODEX_THREAD when a
    # visible active Codex finding sits on the visible
    # page of an incomplete-nested-pagination thread —
    # the markdown must reflect the packet's actual
    # `status`, not a stale "pending" wording that
    # contradicts the real lifecycle.
    #
    # Decision semantics, kept explicit in the report:
    # - incomplete inventory alone is NOT enough to
    #   label the report as "pending"; visible active
    #   Codex findings preserved in the partial
    #   inventory take precedence and drive
    #   HOLD_NEW_CODEX_THREAD.
    # - clean-pass / merge-ready decisions are STILL
    #   refused while any required surface is
    #   incomplete (the fail-closed safety rule is
    #   unchanged).
    if not (issue_complete and rev_complete and inv_complete):
        actual_status = packet.get("status", "") or (
            STATUS_HOLD_CODEX_PENDING
        )
        lines.append(
            "_At least one required Codex response-surface "
            "inventory is incomplete. See `api_errors` below and "
            "the `stop_reason` in the Polling summary. "
            f"Classifier is holding at `{actual_status}`. "
            "Clean-pass / merge-ready decisions are still "
            "refused while any required surface is "
            "incomplete. When the partial inventory has "
            "already preserved a visible active Codex "
            "finding, that visible finding takes "
            "precedence and the status is "
            "`HOLD_NEW_CODEX_THREAD`; when no visible "
            "active finding was found yet, the status is "
            "`HOLD_CODEX_RESPONSE_PENDING`._\n"
        )

    lines.append("## Polling summary\n")
    lines.append(f"- **Polls used:** `{packet.get('polls_used', 0)}` / `{packet.get('max_polls', 0)}`  ")
    lines.append(f"- **Poll seconds:** `{packet.get('poll_seconds', 0)}`  ")
    lines.append(f"- **Polling exhausted:** `{packet.get('polling_exhausted', False)}`  ")
    stop_reason = packet.get("stop_reason", "")
    if stop_reason:
        lines.append(f"- **Stop reason:** `{stop_reason}`  ")
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
    _sys.exit(main())

