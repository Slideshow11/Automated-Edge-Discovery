#!/usr/bin/env python3
"""Canonical shared Codex evidence classification.

This module is the single source of truth for Codex clean-pass /
finding / summary detection across every consumer:

  * codex_review_poller.py
  * audit_codex_response_for_pr.py
  * check_pr_review_comments.py
  * aed_pr status / advance / merge
  * every future consumer

Production code MUST import the predicates from this module
and MUST NOT re-implement them locally.

The classifier enforces the hard-coded policy from PHASE 3 R-2:

  * canonical Codex identity;
  * exact PR identity;
  * latest exact-head ping boundary;
  * newest qualifying response wins;
  * finding badges override clean wording fragments;
  * inline findings override summary-body cleanliness;
  * formal reviews, issue comments, summaries, and inline
    comments are classified consistently;
  * task summaries remain informational unless they contain
    a new substantive finding;
  * stale responses before the latest ping are rejected;
  * earlier-head responses are rejected;
  * incomplete response or inline-comment surfaces fail closed;
  * resolved old findings do not veto a later clean re-review;
  * resolving a finding thread alone does not convert the
    original finding review into a clean review.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

CODEX_LOGINS = frozenset({
    "chatgpt-codex-connector",
    "chatgpt-codex-connector[bot]",
})

# Exact phrase Codex uses to denote a clean pass in
# issue-level comments (curly and straight apostrophes).
CODEX_CLEAN_PASS_PHRASE = "Codex Review: Didn\u2019t find any major issues"
CODEX_CLEAN_PASS_PHRASE_ALT = "Codex Review: Didn't find any major issues"
CODEX_CLEAN_PASS_PHRASES = (
    CODEX_CLEAN_PASS_PHRASE,
    CODEX_CLEAN_PASS_PHRASE_ALT,
)

# Additional clean fragments accepted from the poller
# (mirrors CLEAN_PASS_FRAGMENTS in codex_review_poller.py).
CODEX_CLEAN_PASS_EXTRA_FRAGMENTS = (
    "no findings reported",
    "no issues found",
    "all clear",
    "looks good to me",
    "no blocking findings",
    "no major issues",
)

# Codex formal-review summary header.
CODEX_REVIEW_SUMMARY_PREFIX = "### \U0001f4a1 Codex Review"
CODEX_FINDING_BADGE_PREFIX = "**<sub><sub>"


def is_codex_login(login: Any) -> bool:
    """True iff ``login`` is a recognized Codex identity."""
    if not isinstance(login, str) or not login:
        return False
    return login.lower() in CODEX_LOGINS


def is_codex_review_summary(body: Any) -> bool:
    """True iff the body is a Codex formal-review summary.

    A summary is recognized by the
    ``### \U0001f4a1 Codex Review`` Markdown header.
    """
    if not body or not isinstance(body, str):
        return False
    return body.lstrip().startswith(CODEX_REVIEW_SUMMARY_PREFIX)


def is_codex_finding_body(body: Any) -> bool:
    """True iff the body (or single line) is a Codex finding.

    A finding is a Markdown line beginning with
    ``**<sub><sub>`` — i.e. the P0/P1/P2/P3 badge prefix.
    """
    if not body or not isinstance(body, str):
        return False
    return body.lstrip().startswith(CODEX_FINDING_BADGE_PREFIX)


def body_has_finding_badge(body: Any) -> bool:
    """True iff any line of ``body`` is a Codex finding badge.

    Pre-scan helper used by callers that need to consult body-
    level findings before any clean-pass check.
    """
    if not body or not isinstance(body, str):
        return False
    return any(
        is_codex_finding_body(line)
        for line in body.splitlines()
    )


def is_codex_clean_pass_comment(body: Any) -> bool:
    """Canonical clean-pass predicate (R-2 single source).

    A body is a clean pass when:

      1. no line of the body is a finding badge (the badge
         wins over clean fragments); AND
      2. one of:
         a. it is a summary-format body with no inline-finding
            markers;
         b. it contains one of the legacy exact phrases;
         c. it contains one of the additional clean-pass
            fragments (lowercase substring match).

    The classifier rejects empty / non-string bodies.
    """
    if not body or not isinstance(body, str):
        return False
    # Pre-scan: finding badges always win.
    if body_has_finding_badge(body):
        return False
    # Summary format with no inline-finding markers.
    if is_codex_review_summary(body):
        return True
    # Legacy exact phrase.
    if any(phrase in body for phrase in CODEX_CLEAN_PASS_PHRASES):
        return True
    # Additional clean fragments.
    lower = body.lower()
    if any(frag in lower for frag in CODEX_CLEAN_PASS_EXTRA_FRAGMENTS):
        return True
    return False


def is_codex_finding_comment(body: Any) -> bool:
    """Canonical finding predicate.

    A body is a finding when:

      1. any line is a finding badge (preferred signal); OR
      2. the whole body starts with a finding badge (legacy).

    Both checks are evaluated because finding badges may
    appear anywhere in the body.
    """
    if not body or not isinstance(body, str):
        return False
    if body_has_finding_badge(body):
        return True
    return is_codex_finding_body(body)


def extract_review_commit_oid(review: Dict[str, Any]) -> str:
    """Return the commit OID anchored to a formal review, or "".

    Handles both GraphQL camelCase (commitId / commit.oid) and
    REST snake_case (commit_id). Returns "" when no commit is
    anchored (legacy / GitHub-emitted without commit anchor).
    """
    if not isinstance(review, dict):
        return ""
    direct = review.get("commit_id") or review.get("commitId")
    if isinstance(direct, str) and direct:
        return direct
    commit_obj = review.get("commit")
    if isinstance(commit_obj, dict):
        oid = commit_obj.get("oid")
        if isinstance(oid, str):
            return oid
    return ""


def classify_codex_response(
    *,
    kind: str,            # "review" or "issue_comment"
    candidate: Dict[str, Any],
    head: str,
    expected_head_sha: Optional[str],
    ping_dt_exclusive: bool = True,
    ping_dt: Optional[Any] = None,
) -> Optional[str]:
    """Return "CLEAN_PASS" / "FINDING" / None for a Codex candidate.

    This is the canonical entry point for every consumer.

    ``None`` means: not a qualifying response (wrong head, pre-
    ping, non-Codex author, etc.). Callers MUST treat None as
    not-a-match, not as a finding.

    Pre-conditions enforced before classification:

      * exact PR identity (handled by caller via the
        head-bound check);
      * canonical Codex identity (via author login);
      * newest qualifying response wins (caller's job via
        sort-by-timestamp);
      * stale responses before the latest ping are rejected
        (``ping_dt`` boundary).
    """
    if not isinstance(candidate, dict):
        return None
    author = candidate.get("user") or candidate.get("author") or {}
    login = (
        author.get("login")
        if isinstance(author, dict)
        else author
    )
    if not is_codex_login(login):
        return None

    # Round-93 follow-up (VOewE): for formal reviews, the
    # classifier MUST compare the candidate's commit_oid with
    # the expected_head_sha. A formal review from an earlier
    # commit MUST NOT be classified as exact-head evidence,
    # even when the body mentions the head. The caller is
    # expected to supply ``expected_head_sha``; when ``None``,
    # the classifier returns ``None`` so the caller falls back
    # to its own body-bound check.
    #
    # Round-94 follow-up (VOxDr): use the canonical
    # ``extract_review_commit_oid`` extractor so callers
    # supplying ``commitId`` (camelCase GraphQL form) are
    # anchored correctly. The previous inline extraction only
    # honored ``commit_id`` / ``commit_oid`` / ``commit.oid``
    # and silently rejected reviews that used the supported
    # ``commitId`` representation.
    if kind == "review":
        if expected_head_sha is None:
            return None
        commit_oid = extract_review_commit_oid(candidate)
        if not commit_oid:
            return None
        if commit_oid != expected_head_sha:
            return None

    # Issue comments: head-bound via body content (caller).
    body = candidate.get("body", "") or ""
    ts_raw = (
        candidate.get("submitted_at")
        or candidate.get("submittedAt")
        or candidate.get("created_at")
        or candidate.get("createdAt")
        or ""
    )

    # Ping boundary: skip pre-ping responses.
    # Round-93 follow-up (VOewF): the previous behavior skipped
    # freshness when ``ping_dt_exclusive=False`` and silently
    # accepted missing/malformed timestamps under the default
    # mode. The new behavior:
    # - requires a parseable candidate timestamp;
    # - applies the comparison operator named in the finding:
    #   ``<`` for inclusive mode (``ping_dt_exclusive=False``)
    #   and ``<=`` for exclusive mode (``ping_dt_exclusive=True``);
    # - returns ``None`` for missing/malformed timestamps so the
    #   caller never silently accepts a stale response.
    #
    # Round-94 follow-up (VOxDo): the freshness gate must NOT be
    # conditional on ``ts_raw``. When ``ping_dt`` is set, the
    # caller has explicitly opted into a freshness check and a
    # timestamp-free candidate MUST be treated as unanchored
    # regardless of the parse result. Drop the ``and ts_raw``
    # conditional entirely.
    if ping_dt is not None:
        try:
            from datetime import datetime, timezone
            def _parse_iso(value: Any) -> Optional[datetime]:
                if not value or not isinstance(value, str):
                    return None
                s = value.replace("Z", "+00:00")
                try:
                    return datetime.fromisoformat(s)
                except ValueError:
                    return None
            cand_dt = _parse_iso(ts_raw)
            if cand_dt is None:
                # Round-94 follow-up: missing or malformed
                # timestamps (including empty ts_raw) when
                # ``ping_dt`` is supplied MUST NOT silently
                # accept a stale response. Return None so the
                # caller fails closed.
                return None
            # Round-93 follow-up: apply the comparison named
            # in the finding's prescription.
            if ping_dt_exclusive:
                # Exclusive mode: reject if cand_dt <= ping_dt
                # (i.e. accept strictly post-ping).
                if cand_dt <= ping_dt:
                    return None
            else:
                # Inclusive mode: reject if cand_dt < ping_dt
                # (i.e. accept at-or-after-ping).
                if cand_dt < ping_dt:
                    return None
        except Exception:
            return None

    # Finding classification first: badges override clean
    # fragments. This is the canonical ordering required by R-2.
    if is_codex_finding_comment(body):
        return "FINDING"

    # Clean classification: must pass the canonical predicate.
    if is_codex_clean_pass_comment(body):
        return "CLEAN_PASS"

    # Summary-format body that wasn't a finding — count as
    # informational, not a clean pass (caller decides).
    if is_codex_review_summary(body):
        return None

    return None
