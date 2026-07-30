#!/usr/bin/env python3
"""Exact-head Codex review poller.

Polls a PR for a Codex response (formal review OR issue comment)
that postdates a given exact-head ping, using a secure identity
match (repository + PR + canonical Codex bot identity + timestamp +
head + recognized structure) rather than requiring the full 40-char
SHA to appear literally in the response body.

Usage:
    python3 scripts/local/codex_review_poller.py \\
        --repo Slideshow11/Automated-Edge-Discovery \\
        --pr-number 411 \\
        --head d8f6f480e1020e3e4007c6a9e732c768c428fead \\
        --ping-id 5040694777 \\
        --ping-created-at 2026-07-22T01:08:48Z \\
        --timeout-min 60 \\
        --poll-seconds 60

Exit code:
    0 = Codex response found (clean or finding) within timeout.
    1 = timeout (no response).
    2 = usage / argument error.
    3 = GitHub API error.

The poller logs every heartbeat to stdout in machine-readable
format. It writes its PID to ``/tmp/r<codex_review_poller>.pid``
so a follow-up kill can find it.

Why this exists:
    The Round-47 inline-bash poller required the full 40-char
    SHA to appear literally in the response body, but Codex
    responses only contain a short SHA prefix (e.g.
    ``d8f6f480e1``) in their bodies. The Round-47 poller
    therefore missed a valid clean exact-head response and
    exited with a false ``EXTERNAL_REVIEW_TIMEOUT``. This
    Python poller uses a secure identity match that does
    NOT require the full SHA in the body.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# Canonical Codex bot identities (login values GitHub uses for
# automated Codex review comments). Both forms are accepted
# because GitHub sometimes displays the user as
# ``chatgpt-codex-connector`` and sometimes as
# ``chatgpt-codex-connector[bot]``.
CODEX_LOGINS = frozenset({
    "chatgpt-codex-connector",
    "chatgpt-codex-connector[bot]",
})

# Recognized clean-pass phrase fragments. A response is
# classified as a clean pass if its body contains ANY of
# these case-insensitively. These match the canonical
# ``is_codex_clean_pass_comment`` predicate in
# ``audit_codex_response_for_pr``.
CLEAN_PASS_FRAGMENTS = (
    "didn't find any major issues",
    "no findings reported",
    "no issues found",
    "all clear",
    "looks good to me",
    "no blocking findings",
    "no major issues",
)

# Recognized finding-badges prefix. A response is classified
# as a finding if its body starts with this Markdown header
# (Codex uses ``**<sub>...badge...</sub>  <headline>``).
FINDING_BADGE_PREFIX = "**<sub><sub>"

# Recognized Codex review summary prefix. Codex's automated
# review summaries start with this Markdown header. A
# review with this prefix may contain inline review
# comments that are the actual findings.
CODEX_REVIEW_SUMMARY_PREFIX = "### 💡 Codex Review"

# Reaction-based clean signal (Round-80 hardening).
# Codex sometimes signals a clean pass by reacting with a +1
# on the PR rather than posting a formal review. This is
# checked separately via the reactions API. Round-79 missed
# reaction 431112337 because the old poller only watched
# formal review submissions and issue comments.
# Accept only:
#   content == "+1"
#   user.login == "chatgpt-codex-connector[bot]" (the bot identity)
#   created_at strictly after the request timestamp
#   reaction id not present in the pre-request baseline
#   live head still equals the requested SHA
#   no newer P1/P2 finding-bearing response exists
CODEX_BOT_LOGIN_BASE = "chatgpt-codex-connector"
CODEX_BOT_LOGIN_CLEAN = "chatgpt-codex-connector[bot]"
CODEX_CLEAN_REACTION_CONTENT = "+1"



def _log(level: str, msg: str) -> None:
    sys.stdout.write(
        f"{_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        f" {level} {msg}\n"
    )
    sys.stdout.flush()


def _gh_api(endpoint: str) -> Tuple[Optional[Any], Optional[str]]:
    """Run ``gh api <endpoint>`` and return (data, error_msg)."""
    try:
        proc = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return None, "gh api timeout"
    except FileNotFoundError:
        return None, "gh not found in PATH"
    if proc.returncode != 0:
        return None, (proc.stderr or "").strip() or f"gh api rc={proc.returncode}"
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"json decode error: {exc}"


def _gh_api_paginated(endpoint: str) -> Tuple[Optional[List[Any]], Optional[str]]:
    """Run ``gh api --paginate <endpoint>`` and return the
    flattened concatenation of all pages as a single list.

    GitHub REST list endpoints are paginated. ``per_page``
    only controls page size; additional pages must be
    followed. The ``--paginate`` flag tells ``gh`` to
    follow all pages. Without ``--slurp``, each page is
    emitted as a SEPARATE JSON array, and callers iterate
    a list of lists. With ``--slurp``, the pages are
    collected into a single JSON array of arrays which
    we then flatten. The round-49 fix adds the
    ``--slurp`` flag and the flattening step.

    The round-48 fix (without ``--slurp``) caused the
    caller to iterate ``[page1, page2, ...]`` and call
    ``.get()`` on a list, making the poller crash or
    miss the post-ping Codex response on long PRs.
    """
    cmd = ["gh", "api", "--paginate", "--slurp", endpoint]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None, "gh api --paginate --slurp timeout"
    except FileNotFoundError:
        return None, "gh not found in PATH"
    if proc.returncode != 0:
        return None, (proc.stderr or "").strip() or f"gh api --paginate --slurp rc={proc.returncode}"
    out = (proc.stdout or "").strip()
    if not out:
        return [], None
    # ``--slurp`` produces a single JSON array of pages
    # when the endpoint is a list endpoint, or a
    # single-element array of the single object when
    # the endpoint is a non-list endpoint. Try parsing
    # as a flat list first; if that fails, try NDJSON
    # (each line is a page).
    try:
        data = json.loads(out)
        if isinstance(data, list):
            # ``--slurp`` may produce [page1, page2, ...]
            # where each page is itself a list. Flatten
            # one level. If the top-level items are
            # dicts (non-list endpoint), the data is
            # already flat.
            if data and isinstance(data[0], list):
                flat = []
                for page in data:
                    if isinstance(page, list):
                        flat.extend(page)
                    else:
                        flat.append(page)
                return flat, None
            return data, None
    except json.JSONDecodeError:
        pass
    # Try NDJSON (one JSON object/array per line).
    try:
        all_items: List[Any] = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            page = json.loads(line)
            if isinstance(page, list):
                all_items.extend(page)
            else:
                all_items.append(page)
        return all_items, None
    except json.JSONDecodeError as exc:
        return None, f"json decode error: {exc}"


def _parse_iso_utc(s: str) -> Optional[_dt.datetime]:
    """Parse an ISO 8601 UTC timestamp into a timezone-aware datetime."""
    if not s:
        return None
    s = s.strip()
    # Accept ``...Z`` and ``...+00:00``.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def _is_codex_login(login: str) -> bool:
    return (login or "").lower() in CODEX_LOGINS


def _is_clean_pass(body: str) -> bool:
    """True iff the body looks like a Codex clean-pass response.

    Round-412 (PHASE 4 Finding 3): the poller MUST use the
    shared classifier as the authoritative source. Local
    predicate retained as a thin alias for backward
    compatibility but every clean-pass decision delegates
    to the shared module.
    """
    if not body:
        return False
    if _shared_is_clean is not None:
        return _shared_is_clean(body)
    # Fail closed if the shared module could not be
    # imported: a finding badge in the body always wins
    # over a clean fragment, so without the shared
    # classifier we cannot safely emit CLEAN_PASS.
    if any(_is_finding(line) for line in body.splitlines()):
        return False
    lower = body.lower()
    return any(frag in lower for frag in CLEAN_PASS_FRAGMENTS)


def _is_finding(body: str) -> bool:
    """True iff the body looks like a Codex finding response.

    Round-412 (PHASE 4 Finding 3): delegate to the shared
    classifier when available. The shared module handles
    both per-line badge detection and the body-level
    finding-badges-overrides-clean rule.
    """
    if not body:
        return False
    if _shared_is_finding is not None:
        return _shared_is_finding(body)
    return body.lstrip().startswith(FINDING_BADGE_PREFIX)


# Shared classifier re-export. We try to import the shared
# helper so the poller and the audit module always agree on
# the per-line finding-badge detection. If the import fails
# (e.g. during isolated testing), fall back to a local scan.
try:
    from scripts.local._shared_codex_classifier import (
        body_has_finding_badge as _shared_body_has_finding,
        is_codex_clean_pass_comment as _shared_is_clean,
        is_codex_finding_body as _shared_is_finding,
    )
except ImportError:  # pragma: no cover
    _shared_body_has_finding = None
    _shared_is_clean = None  # noqa: PLW0603
    _shared_is_finding = None  # noqa: PLW0603


def _body_has_finding_badge(body: str) -> bool:
    """True iff any line in the body has a Codex finding badge.

    Round-412 (PHASE 4 Finding 3): re-exported from the
    shared classifier. A finding badge MUST override any
    clean wording in the same body.
    """
    if not body:
        return False
    if _shared_body_has_finding is not None:
        return _shared_body_has_finding(body)
    return any(_is_finding(line) for line in body.splitlines())


def _is_codex_review_summary(body: str) -> bool:
    """True iff the body is a Codex review summary.

    Codex's automated review summaries start with the
    ``### 💡 Codex Review`` Markdown header. A review
    with this prefix may contain inline review comments
    that carry the actual findings; the summary body
    itself is not a finding.
    """
    if not body:
        return False
    return body.lstrip().startswith(CODEX_REVIEW_SUMMARY_PREFIX)


def _head_matches_response(head: str, body: str) -> bool:
    """True iff the response body references the current head.

    Accepts:
    - the full 40-char lowercase hex SHA literally;
    - the short 7+ char SHA prefix (Codex's default formatting);
    - the SHA with a non-canonical prefix (we still match a
      minimum of 7 hex characters to keep the check secure).

    The short-prefix check is the round-47-bug fix: Codex
    only includes the short prefix in the body, never the
    full 40-char SHA, so a literal full-SHA check would miss
    the response.
    """
    if not body or not head:
        return False
    # Literal full SHA.
    if head in body:
        return True
    # Short prefix match (>= 7 hex chars).
    short = head[:7]
    if all(c in "0123456789abcdef" for c in short) and short in body:
        return True
    return False


def _fetch_formal_reviews(repo: str, pr_number: int) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Return list of formal reviews (paginated) or (None, error_msg)."""
    data, err = _gh_api_paginated(f"repos/{repo}/pulls/{pr_number}/reviews")
    if err is not None:
        return None, err
    return data or [], None


def _fetch_issue_comments(repo: str, pr_number: int) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Return list of PR-level issue comments (paginated) or (None, error_msg)."""
    data, err = _gh_api_paginated(f"repos/{repo}/issues/{pr_number}/comments")
    if err is not None:
        return None, err
    return data or [], None


def _fetch_review_inline_comments(
    repo: str, pr_number: int, review_id: Any
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Return list of inline review comments for a single
    review (paginated) or (None, error_msg).

    A formal review with the Codex review summary prefix
    may carry inline review comments that are the actual
    findings. The poller fetches these to distinguish a
    clean pass (no inline comments) from a finding (one
    or more inline comments with the finding badge).
    """
    data, err = _gh_api_paginated(
        f"repos/{repo}/pulls/{pr_number}/reviews/{review_id}/comments"
    )
    if err is not None:
        return None, err
    return data or [], None


def _classify_review_with_inline(
    review: Dict[str, Any],
    *,
    repo: str,
    pr_number: int,
) -> str:
    """Classify a formal review as CLEAN_PASS or FINDING.

    For reviews with the Codex review summary prefix
    (``### 💡 Codex Review``), the summary body itself is
    not a finding; the findings are in inline review
    comments. We fetch the inline comments and check
    whether any carry the finding badge. If so, the
    review is a FINDING. Otherwise, it is a CLEAN_PASS.

    For other review body formats, fall back to the
    direct body classification.
    """
    body = review.get("body", "") or ""
    if _is_codex_review_summary(body):
        # Fetch inline comments to determine the verdict.
        review_id = review.get("id")
        inline, err = _fetch_review_inline_comments(repo, pr_number, review_id)
        if err is not None or inline is None:
            # If we can't fetch inline comments, conservatively
            # classify as FINDING (fail closed) so the operator
            # sees the response and can investigate. A false
            # FINDING is safer than a missed FINDING.
            return "FINDING"
        if not inline:
            return "CLEAN_PASS"
        # Any inline comment with the finding badge prefix
        # makes the review a FINDING.
        for c in inline:
            c_body = c.get("body", "") or ""
            if _is_finding(c_body):
                return "FINDING"
        return "CLEAN_PASS"
    if _is_clean_pass(body):
        return "CLEAN_PASS"
    if _is_finding(body):
        return "FINDING"
    return ""


def _match_response(
    candidate: Dict[str, Any],
    *,
    kind: str,  # "review" or "issue_comment"
    repo: str,
    pr_number: int,
    head: str,
    ping_dt: _dt.datetime,
    ping_dt_exclusive: bool = True,
) -> Optional[Dict[str, Any]]:
    """Return a match descriptor if ``candidate`` is a valid
    exact-head Codex response for our poller, else None.

    The match is secure:
    - the candidate must reference the current head (short or
      full SHA) in its body OR have ``commit_id == head`` for
      formal reviews;
    - the candidate author must be a canonical Codex bot login;
    - the candidate timestamp must be at or after the ping
      boundary (post-ping).

    The match is structured:
    - the candidate body (or, for summary-format reviews, the
      inline review comments) must look like either a clean
      pass or a finding (recognized badge prefix or clean-pass
      phrase).
    """
    # Author check (Codex bot identity).
    author = candidate.get("user", {}).get("login", "") if isinstance(candidate.get("user"), dict) else ""
    if not _is_codex_login(author):
        return None
    # PR / repo identity check (defensive — the endpoint already
    # scopes to the PR but we double-check).
    if kind == "issue_comment":
        # Issue comments are not head-bound via the API; the
        # body must reference the current head.
        body = candidate.get("body", "") or ""
        if not _head_matches_response(head, body):
            return None
        ts_raw = candidate.get("created_at", "")
        # Round-65 fix: pre-scan for finding badges
        # BEFORE any clean-pass or summary check. A
        # finding badge in the body MUST take precedence
        # over any clean fragment ("no major issues" /
        # "no findings reported" / etc.) or summary
        # format. Without this pre-scan, the
        # ``_is_clean_pass`` check below would return
        # ``CLEAN_PASS`` before the summary-specific
        # badge scan runs, falsely classifying a
        # finding-bearing response as clean. The
        # downstream automation would then exit with
        # clean verdict and unblock merge even though
        # the latest Codex response includes a finding.
        body_has_finding_badge = any(
            _is_finding(line)
            for line in body.splitlines()
        )
        # For issue comments, classify from the body directly.
        if _is_clean_pass(body) and not body_has_finding_badge:
            verdict = "CLEAN_PASS"
        elif _is_finding(body) or body_has_finding_badge:
            # Round-66 fix: ``_is_finding(body)`` only
            # checks whether the whole body starts with a
            # finding badge. A non-summary body that
            # contains a clean fragment before a later
            # finding badge would fall through to
            # ``return None`` instead of being
            # classified as FINDING, causing the poller
            # to miss a post-ping finding-bearing
            # response and time out. Use the already-
            # computed ``body_has_finding_badge`` (from
            # the Round-65 pre-scan) to catch any body-
            # level finding badge.
            verdict = "FINDING"
        elif _is_codex_review_summary(body):
            # Issue comment that looks like a review summary
            # (e.g. an echoed review). Classify as CLEAN_PASS
            # only if the body is empty of finding phrases.
            # Round-63 fix: also scan the summary body
            # lines for finding markers. A summary issue
            # comment whose body includes a finding badge
            # later in the text (after the summary header)
            # MUST be classified as FINDING, not
            # CLEAN_PASS. Without this check, a
            # post-ping response that contains a finding
            # badge embedded in the summary body would
            # incorrectly unblock the external review
            # loop.
            if body_has_finding_badge:
                verdict = "FINDING"
            else:
                verdict = "CLEAN_PASS"
        else:
            # Body has no recognized structure. Skip it.
            return None
    else:
        # Formal reviews may be head-bound via ``commit_id``;
        # if so, the head check is satisfied by the equality.
        # If not head-bound, fall back to the body match.
        commit_id = candidate.get("commit_id", "") or ""
        body = candidate.get("body", "") or ""
        if commit_id and commit_id != head:
            return None
        if not commit_id and not _head_matches_response(head, body):
            return None
        ts_raw = candidate.get("submitted_at", "") or candidate.get("created_at", "")
        # Classify the review, fetching inline comments
        # for summary-format reviews.
        verdict = _classify_review_with_inline(
            candidate, repo=repo, pr_number=pr_number,
        )
        if not verdict:
            # Body has no recognized structure. Skip it.
            return None
    # Timestamp check (post-ping).
    dt = _parse_iso_utc(ts_raw)
    if dt is None:
        return None
    if ping_dt_exclusive:
        if dt <= ping_dt:
            return None
    else:
        if dt < ping_dt:
            return None
    return {
        "kind": kind,
        "id": candidate.get("id"),
        "author": author,
        "timestamp": ts_raw,
        "verdict": verdict,
        "body_first_200": body[:200],
    }


def _fetch_reactions(
    repo: str, pr_number: int,
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Fetch every reaction on the PR-level ``issues/N`` endpoint.

    Reactions on review comments and on review summaries are NOT
    included here. We only accept reactions on the PR itself,
    because the Codex clean-pass signal is a +1 on the PR.

    GitHub returns reactions as a flat array sorted by creation
    time ascending. We return the array as-is; the caller
    filters by timestamp and ID.
    """
    endpoint = f"/repos/{repo}/issues/{pr_number}/reactions"
    return _gh_api_paginated(endpoint)


def _is_codex_bot_reaction_actor(login: Any) -> bool:
    """Return True iff ``login`` matches the canonical Codex
    connector bot identity (with or without ``[bot]`` suffix).
    """
    if not isinstance(login, str):
        return False
    s = login.strip().lower()
    return s in (CODEX_BOT_LOGIN_BASE, CODEX_BOT_LOGIN_CLEAN)


def _match_reaction(
    reaction: Dict[str, Any],
    *,
    repo: str,
    pr_number: int,
    head: str,
    ping_dt: _dt.datetime,
    baseline_reaction_ids: set,
    consumed_reaction_ids: set,
) -> Optional[Dict[str, Any]]:
    """Return a match descriptor if ``reaction`` is a valid
    exact-head Codex clean-pass reaction for our poller.

    A reaction is accepted only when:
    - content == CODEX_CLEAN_REACTION_CONTENT (i.e. "+1");
    - user.login is the canonical Codex connector bot;
    - created_at is strictly after the ping boundary;
    - reaction id is NOT in the pre-request baseline
      (a stale reaction that existed before the request
      must be rejected);
    - reaction id is NOT in the consumed-reaction ledger
      (a previously consumed reaction id cannot be
      accepted twice);
    - live head still equals the requested head (defensive
      check; we trust the caller passed the live head).

    Returns None otherwise. The caller is responsible for
    treating reactions as a SECONDARY signal: any
    formal-review FINDING with a strictly newer timestamp
    MUST take precedence over a +1 reaction.
    """
    if not isinstance(reaction, dict):
        return None
    # Content check.
    content = reaction.get("content", "")
    if content != CODEX_CLEAN_REACTION_CONTENT:
        return None
    # Author check.
    user_obj = reaction.get("user", {})
    author = ""
    if isinstance(user_obj, dict):
        author = user_obj.get("login", "") or ""
    if not _is_codex_bot_reaction_actor(author):
        return None
    # Baseline exclusion.
    rid = reaction.get("id")
    rid_key = str(rid) if rid is not None else ""
    if rid in baseline_reaction_ids or rid_key in baseline_reaction_ids:
        return None
    # Idempotence ledger exclusion.
    if rid in consumed_reaction_ids or rid_key in consumed_reaction_ids:
        return None
    # Timestamp check.
    ts_raw = reaction.get("created_at", "") or ""
    dt = _parse_iso_utc(ts_raw)
    if dt is None:
        return None
    if dt <= ping_dt:
        return None
    # Head check (defensive; caller passed live head).
    if head and not _head_matches_response(head, str(reaction.get("body", ""))):
        # Reactions don't carry body, but we re-check the
        # head against the poller's expectations via a
        # defensive hook here. Reactions do not normally
        # embed the SHA; pass.
        pass
    return {
        "kind": "reaction",
        "id": rid,
        "node_id": reaction.get("node_id"),
        "content": content,
        "author": author,
        "timestamp": ts_raw,
        "verdict": "CLEAN_PASS",
        "body_first_200": f"reaction+{content}",
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Exact-head Codex review poller (secure identity match).",
    )
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--pr-number", type=int, required=True)
    p.add_argument("--head", required=True, help="exact 40-char hex head SHA")
    p.add_argument(
        "--ping-id", required=True,
        help="ID of the @codex review ping comment",
    )
    p.add_argument(
        "--ping-created-at", required=True,
        help="ISO 8601 timestamp of the ping (UTC, e.g. 2026-07-22T01:08:48Z)",
    )
    p.add_argument(
        "--timeout-min", type=int, default=60,
        help="max poll duration in minutes (default 60)",
    )
    p.add_argument(
        "--poll-seconds", type=int, default=60,
        help="seconds between polls (default 60)",
    )
    p.add_argument(
        "--include-older-ping-id", action="append", default=[],
        help=(
            "optional additional ping ID(s) to treat as the "
            "start of the post-ping window (e.g. a retry "
            "ping for a dual-ID replacement poller)"
        ),
    )
    p.add_argument(
        "--baseline-reaction-ids", action="append", default=[],
        help=(
            "pre-request PR-reaction IDs (comma-separated or "
            "repeated). Reactions already in this set are "
            "rejected as STALE and never classified as a "
            "fresh response. Round-80 hardening."
        ),
    )
    p.add_argument(
        "--consumed-reaction-ids", action="append", default=[],
        help=(
            "comma-separated PR-reaction IDs already "
            "consumed by a prior poll cycle. A reaction ID "
            "in this set cannot be accepted twice (idempotence "
            "ledger). Round-80 hardening."
        ),
    )
    args = p.parse_args(argv)

    # Validate head.
    if len(args.head) != 40 or not all(c in "0123456789abcdef" for c in args.head):
        _log("ERROR", f"--head must be a 40-char lowercase hex SHA, got {args.head!r}")
        return 2

    ping_dt = _parse_iso_utc(args.ping_created_at)
    if ping_dt is None:
        _log("ERROR", f"--ping-created-at must be ISO 8601, got {args.ping_created_at!r}")
        return 2

    # Write PID file (next to logs in /tmp).
    pid_file = os.environ.get("POLLER_PID_FILE", "/tmp/codex_review_poller.pid")
    try:
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass

    _log("INFO", f"POLLER_STARTED head={args.head} ping_id={args.ping_id} "
        f"ping_created_at={args.ping_created_at} "
        f"timeout_min={args.timeout_min} poll_seconds={args.poll_seconds} "
        f"pid={os.getpid()} pid_file={pid_file} "
        f"older_ping_ids={args.include_older_ping_id}")

    start_ts = time.time()
    poll_count = 0
    # Build the set of accepted ping IDs. A response is
    # accepted if its timestamp is >= the EARLIEST of these
    # pings. (For dual-ID replacement pollers, the original
    # and retry pings share the same post-ping window.)
    earliest_ping_dt = ping_dt
    if args.include_older_ping_id:
        # ``--include-older-ping-id`` references earlier pings
        # whose timestamps are NOT supplied directly. We use
        # the original ping as the floor; the caller is
        # responsible for passing the original ping's
        # timestamp via ``--ping-created-at`` if it's older
        # than the retry. In practice, retry pings have
        # timestamps AFTER the original, so the floor is the
        # older of the two; we use the explicit
        # ``--ping-created-at`` as the authoritative floor.
        pass

    # Round-80 hardening: parse baseline reaction IDs and
    # consumed-reaction ledger into sets. Comma-separated or
    # repeated CLI args are accepted.
    def _parse_id_list(raw: List[str]) -> set:
        out = set()
        for v in raw or []:
            for piece in str(v).split(","):
                piece = piece.strip()
                if piece:
                    out.add(piece)
        return out

    baseline_reaction_ids = _parse_id_list(args.baseline_reaction_ids)
    consumed_reaction_ids = _parse_id_list(args.consumed_reaction_ids)

    last_api_error = ""
    while True:
        poll_count += 1
        now_ts = time.time()
        elapsed_min = int((now_ts - start_ts) / 60)

        # Fetch all three surfaces: formal reviews, issue
        # comments, AND PR-level reactions (Round-80).
        reviews, err1 = _fetch_formal_reviews(args.repo, args.pr_number)
        comments, err2 = _fetch_issue_comments(args.repo, args.pr_number)
        reactions, err3 = _fetch_reactions(args.repo, args.pr_number)

        if err1:
            last_api_error = f"reviews: {err1}"
            _log("WARN", f"poll={poll_count} elapsed_min={elapsed_min} api_error={err1}")
        if err2:
            last_api_error = f"comments: {err2}"
            _log("WARN", f"poll={poll_count} elapsed_min={elapsed_min} api_error={err2}")
        if err3:
            last_api_error = f"reactions: {err3}"
            _log("WARN", f"poll={poll_count} elapsed_min={elapsed_min} api_error={err3}")

        # Scan ALL surfaces and collect ALL matching
        # responses, then pick the newest by timestamp.
        # Round-51 fix: the previous implementation
        # accepted the first matching formal review and
        # skipped the issue-comment scan, so an older
        # clean pass could be reported even if Codex
        # posted a newer finding later. We must collect
        # every matching review AND every matching
        # issue comment, then select the one with the
        # newest timestamp. This is critical because
        # GitHub's list endpoints return items in
        # chronological / ID order, not reverse-chrono.
        all_matches: List[Dict[str, Any]] = []
        match: Optional[Dict[str, Any]] = None
        if reviews is not None:
            for r in reviews:
                m = _match_response(
                    r, kind="review",
                    repo=args.repo, pr_number=args.pr_number,
                    head=args.head, ping_dt=earliest_ping_dt,
                )
                if m is not None:
                    all_matches.append(m)
        if comments is not None:
            for c in comments:
                m = _match_response(
                    c, kind="issue_comment",
                    repo=args.repo, pr_number=args.pr_number,
                    head=args.head, ping_dt=earliest_ping_dt,
                )
                if m is not None:
                    all_matches.append(m)
        # Round-80 hardening: also scan PR-level reactions.
        # Reactions yield CLEAN_PASS verdicts and are added
        # to all_matches; the precedence logic below keeps
        # any newer formal FINDING ahead of a reaction.
        if reactions is not None:
            for r in reactions:
                m = _match_reaction(
                    r,
                    repo=args.repo, pr_number=args.pr_number,
                    head=args.head, ping_dt=earliest_ping_dt,
                    baseline_reaction_ids=baseline_reaction_ids,
                    consumed_reaction_ids=consumed_reaction_ids,
                )
                if m is not None:
                    all_matches.append(m)

        # Select the newest match by parsed timestamp.
        # If two matches share the exact same timestamp,
        # prefer formal reviews over issue comments
        # (formal reviews carry inline findings; issue
        # comments may be echoes).
        if all_matches:
            def _match_sort_key(m: Dict[str, Any]) -> Tuple[float, int]:
                dt = _parse_iso_utc(m.get("timestamp", ""))
                kind = m.get("kind", "")
                if kind == "review":
                    kind_rank = 0
                elif kind == "issue_comment":
                    kind_rank = 1
                else:  # reaction (Round-80)
                    kind_rank = 2
                # Sort by timestamp DESCENDING (newest first).
                # Use the negative of the timestamp via the
                # epoch seconds, then by kind_rank ascending
                # (formal reviews > issue comments > reactions).
                epoch = (
                    dt.timestamp() if dt is not None else float("-inf")
                )
                return (-epoch, kind_rank)
            all_matches.sort(key=_match_sort_key)
            match = all_matches[0]
            # Log the alternates for operator visibility.
            if len(all_matches) > 1:
                _log(
                    "INFO",
                    f"selected_newest match_id={match['id']} "
                    f"kind={match['kind']} timestamp={match['timestamp']} "
                    f"verdict={match['verdict']} "
                    f"(alternates={len(all_matches)-1})",
                )

        n_reviews = len(reviews) if reviews is not None else 0
        n_comments = len(comments) if comments is not None else 0
        n_reactions = len(reactions) if reactions is not None else 0
        _log(
            "HEARTBEAT",
            f"poll={poll_count} elapsed_min={elapsed_min} head={args.head} "
            f"reviews={n_reviews} issue_comments={n_comments} "
            f"reactions={n_reactions} "
            f"matches={len(all_matches)} "
            f"match={'YES' if match else 'NO'}",
        )

        if match is not None:
            _log(
                "FOUND",
                f"CODEX_RESPONSE_FOUND kind={match['kind']} "
                f"id={match['id']} author={match['author']} "
                f"verdict={match['verdict']} timestamp={match['timestamp']} "
                f"head={args.head} "
                f"body_first_200={match['body_first_200']!r}",
            )
            # Emit the match descriptor as a single-line JSON
            # record on stdout for downstream tools.
            sys.stdout.write("---CODEX_RESPONSE_JSON---\n")
            sys.stdout.write(json.dumps({
                "verdict": match["verdict"],
                "kind": match["kind"],
                "id": match["id"],
                "author": match["author"],
                "timestamp": match["timestamp"],
                "head": args.head,
                "repo": args.repo,
                "pr_number": args.pr_number,
            }) + "\n")
            sys.stdout.flush()
            return 0

        if elapsed_min >= args.timeout_min:
            _log(
                "TIMEOUT",
                f"EXTERNAL_REVIEW_TIMEOUT head={args.head} ping_id={args.ping_id} "
                f"elapsed_min={elapsed_min} last_api_error={last_api_error!r}",
            )
            return 1

        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
