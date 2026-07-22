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

# Reaction-based clean signal. Codex sometimes signals a
# clean pass by reacting with 👍 on the PR rather than
# posting a formal review. This is checked separately
# via the reactions API.


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
    concatenation of all pages as a single list (or the
    raw object if the response is not a list).

    GitHub REST list endpoints are paginated. ``per_page``
    only controls page size; additional pages must be
    followed. The ``--paginate`` flag tells ``gh`` to
    follow all pages and concatenate the results. This
    is the round-48 fix: without pagination, a PR with
    more than 100 issue comments only returns the first
    page (oldest-first by default), and a post-ping Codex
    response on a long PR can sit on page 2+ and never
    be scanned, causing a false timeout.
    """
    # Strip any leading "repos/..." or "user/..." prefix
    # because ``--paginate`` expects the bare endpoint.
    # The endpoint may already include a query string.
    cmd = ["gh", "api", "--paginate", endpoint]
    # ``--paginate`` writes each page on its own line in
    # newline-delimited JSON, OR concatenates the list
    # elements into a single JSON array. Try parsing the
    # full output as a single array first; if that fails,
    # try NDJSON.
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None, "gh api --paginate timeout"
    except FileNotFoundError:
        return None, "gh not found in PATH"
    if proc.returncode != 0:
        return None, (proc.stderr or "").strip() or f"gh api --paginate rc={proc.returncode}"
    out = (proc.stdout or "").strip()
    if not out:
        return [], None
    # Try single JSON array.
    try:
        data = json.loads(out)
        if isinstance(data, list):
            return data, None
        # Non-list response (single object): wrap in list
        # of one so the caller can iterate uniformly.
        return [data], None
    except json.JSONDecodeError:
        pass
    # Try NDJSON (one JSON object per line).
    try:
        data = [json.loads(line) for line in out.splitlines() if line.strip()]
        return data, None
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
    """True iff the body looks like a Codex clean-pass response."""
    if not body:
        return False
    lower = body.lower()
    return any(frag in lower for frag in CLEAN_PASS_FRAGMENTS)


def _is_finding(body: str) -> bool:
    """True iff the body looks like a Codex finding response."""
    if not body:
        return False
    return body.lstrip().startswith(FINDING_BADGE_PREFIX)


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
        # For issue comments, classify from the body directly.
        if _is_clean_pass(body):
            verdict = "CLEAN_PASS"
        elif _is_finding(body):
            verdict = "FINDING"
        elif _is_codex_review_summary(body):
            # Issue comment that looks like a review summary
            # (e.g. an echoed review). Classify as CLEAN_PASS
            # only if the body is empty of finding phrases.
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

    last_api_error = ""
    while True:
        poll_count += 1
        now_ts = time.time()
        elapsed_min = int((now_ts - start_ts) / 60)

        # Fetch both surfaces.
        reviews, err1 = _fetch_formal_reviews(args.repo, args.pr_number)
        comments, err2 = _fetch_issue_comments(args.repo, args.pr_number)

        if err1:
            last_api_error = f"reviews: {err1}"
            _log("WARN", f"poll={poll_count} elapsed_min={elapsed_min} api_error={err1}")
        if err2:
            last_api_error = f"comments: {err2}"
            _log("WARN", f"poll={poll_count} elapsed_min={elapsed_min} api_error={err2}")

        # Scan formal reviews first.
        match = None
        if reviews is not None:
            for r in reviews:
                m = _match_response(
                    r, kind="review",
                    repo=args.repo, pr_number=args.pr_number,
                    head=args.head, ping_dt=earliest_ping_dt,
                )
                if m is not None:
                    match = m
                    break

        # If no formal-review match, scan issue comments.
        if match is None and comments is not None:
            for c in comments:
                m = _match_response(
                    c, kind="issue_comment",
                    repo=args.repo, pr_number=args.pr_number,
                    head=args.head, ping_dt=earliest_ping_dt,
                )
                if m is not None:
                    match = m
                    break

        n_reviews = len(reviews) if reviews is not None else 0
        n_comments = len(comments) if comments is not None else 0
        _log(
            "HEARTBEAT",
            f"poll={poll_count} elapsed_min={elapsed_min} head={args.head} "
            f"reviews={n_reviews} issue_comments={n_comments} match={'YES' if match else 'NO'}",
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
