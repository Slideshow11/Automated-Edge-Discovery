#!/usr/bin/env python3
"""
check_pr_review_comments.py

Fetch and classify GitHub PR review feedback from all relevant endpoints.
Fails closed on P0/P1 unresolved blockers; P2 blocks unless explicitly waived.

Usage:
    python3 scripts/local/check_pr_review_comments.py \
        --repo OWNER/REPO \
        --pr-number 320 \
        --reported-head-sha <sha> \
        --output-json /tmp/status.json \
        --output-md /tmp/status.md

Exit codes:
    0 = REVIEW_COMMENTS_CLEAN
    1 = REVIEW_COMMENTS_BLOCKED
    2 = REVIEW_COMMENTS_INCONCLUSIVE
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Needles and blocking words
# ---------------------------------------------------------------------------

CODEX_NEEDLES = (
    "codex",
    "chatgpt-codex",
    "p0",
    "p1",
    "p2",
    "p3",
    "badge",
    "suggestion",
    "review suggestion",
    "high",
    "medium",
)

# Words that make an unspecified or low-severity Codex comment blocking.
BLOCKING_WORDS = (
    "must fix",
    "can fail",
    "security",
    "path traversal",
    "stale",
    "malformed",
    "nonzero",
    "unsafe",
    "shell=True",
    "live claude",
    "real executor",
    "hermes mutation",
    "memory",
    "profile",
    "outside repo",
    "bypass",
    "ready false positive",
)

# Coordination comment patterns (case-insensitive substrings).
# Human PR authors post "Re-requesting Codex review..." or
# "Gentle nudge to @chatgpt-codex-connector..." issue comments
# after pushing fixes. These are coordination messages, not
# actual findings, but they contain Codex needles and were
# being misclassified as blocking findings. The gate must
# skip them while still detecting real Codex review findings.
_COORDINATION_PATTERNS = (
    "re-requesting",
    "re-request",
    "gentle nudge",
    "bumping",
    "nudge to @",
    # Any direct @-mention of the Codex bot (e.g. ``@codex
    # review``) is a coordination signal from the human PR
    # author asking the bot to re-review. The
    # ``chatgpt-codex-connector[bot]`` user is already excluded
    # by ``--ignore-users``, so this only fires for human
    # comments that mention @codex.
    "@codex",
)


# ---------------------------------------------------------------------------
# Guard 6 helper: direct-affirmative text-severity declaration detector.
# ---------------------------------------------------------------------------
# Cycle 8 (PR #405 fresh Codex P2 finding, 2026-06-21T03:52:50Z on head
# 7377eada08, dbID 3447871114): the cycle-7 regex required the level
# token to come IMMEDIATELY after the optional ``a``/``an`` article,
# which silently dropped real Codex findings phrased with an
# intensifier between the article and the level — e.g. ``is an
# extremely high severity issue``. The fix is to extract Guard 6
# into a small, table-driven helper that tokenizes the leading
# window and verifies the copula → [negation?] → [article?] →
# [intensifier{0,2}] → level → noun shape using explicit word
# tables, rather than a single brittle regex.
#
# Design constraints (PR #405 cycle 8 design-narrow extension):
#   1. The verb set is narrowed to the copulas ``is``/``has`` only —
#      ``as`` and ``with`` remain excluded so meta-discussion
#      forms (``classified as high priority``, ``with high
#      priority context``) keep failing the rescue.
#   2. Negation is treated as definitive (returns False immediately
#      on the first copula-following negation token), matching
#      cycle-5/6/7 behavior.
#   3. Up to TWO intensifier tokens are accepted from a fixed
#      whitelist (``very``, ``extremely``, ``particularly``,
#      ``especially``, ``clearly``, ``obviously``, ``materially``,
#      ``highly``) — arbitrary words between article and level
#      are not accepted, to avoid the regex-whack-a-mole pattern
#      that produced finding cycles 3-7.
#   4. The subject pronoun (``this``/``that``/``it``) before the
#      verb is supported but not required.
_TEXT_SEVERITY_VERBS = ("is", "has")
_TEXT_SEVERITY_ARTICLES = ("a", "an")
_TEXT_SEVERITY_INTENSIFIERS = (
    "very",
    "extremely",
    "particularly",
    "especially",
    "clearly",
    "obviously",
    "materially",
    "highly",
)
_TEXT_SEVERITY_LEVELS = ("high", "medium", "low")
_TEXT_SEVERITY_NOUNS = ("severity", "priority")
_TEXT_SEVERITY_NEGATIONS = frozenset({
    "not",
    "no",
    "never",
    "without",
    "isnt",
    "hasnt",
    "arent",
    "wasnt",
    "werent",
})
_MAX_TEXT_SEVERITY_INTENSIFIERS = 2
# frozenset views for O(1) membership tests inside the hot loop.
_TEXT_SEVERITY_VERBS_SET = frozenset(_TEXT_SEVERITY_VERBS)
_TEXT_SEVERITY_ARTICLES_SET = frozenset(_TEXT_SEVERITY_ARTICLES)
_TEXT_SEVERITY_INTENSIFIERS_SET = frozenset(_TEXT_SEVERITY_INTENSIFIERS)
_TEXT_SEVERITY_LEVELS_SET = frozenset(_TEXT_SEVERITY_LEVELS)
_TEXT_SEVERITY_NOUNS_SET = frozenset(_TEXT_SEVERITY_NOUNS)
# Trailing punctuation that may follow a level or noun token
# without breaking the pattern (e.g. ``is medium priority:``).
# The cycle-7 regex used ``\b`` word boundaries which tolerate
# this; the cycle-8 token-based helper strips a small set of
# trailing punctuation characters to preserve the same behavior.
#
# Cycle 9 (PR #405 fresh Codex P2 finding, 2026-06-21T04:20:36Z
# on head d44c5ddaea8, dbID 3447899921): the cycle-8 punct set
# did NOT include dash separators, so real Codex findings phrased
# with em-dash / en-dash / hyphen separators (e.g. ``Bumping
# retry is high priority—this skips CI``) had ``priority—this``
# as a single token after ``.split()`` and ``.rstrip()`` did not
# strip the dash. The cycle-7 regex's ``\b`` word boundary
# accepted these common Markdown separators. Cycle 9 adds the
# three dash characters (``-``, ``—``, ``–``) to the trailing
# punct set so the helper matches the cycle-7 behavior again.
_TEXT_SEVERITY_TRAILING_PUNCT = ".,;:!?\"'()[]{}-—–"


def _has_direct_text_severity_declaration(leading_text: str) -> bool:
    """Return ``True`` iff ``leading_text`` (the lowercased first
    100 chars of a comment body) contains a direct affirmative
    severity/priority declaration that should be rescued from
    Guard 6's coordination-skip path.

    Recognized shape (whitespace-tokenized):

        [subject?] <verb> [negation?] [article?] <intensifier>{0,2} <level> <noun>

    where:

        * ``<verb>``    ∈ ``{"is", "has"}``  (copulas only;
          ``as`` and ``with`` excluded)
        * ``[subject?]``∈ ``{"this", "that", "it"}``  (optional)
        * ``[negation?]`` ∈ ``NOT_NEGATIONS``  — if present
          IMMEDIATELY after the verb, that candidate copula
          is rejected and the helper moves on to the NEXT
          copula in the input. The negation is per-phrase,
          not per-helper-call: a later affirmative copula in
          the same input still rescues the comment.
        * ``[article?]`` ∈ ``{"a", "an"}``  (optional)
        * ``<intensifier>`` ∈ ``INTENSIFIER_WHITELIST``, max 2
          consecutive tokens
        * ``<level>``    ∈ ``{"high", "medium", "low"}``
        * ``<noun>``     ∈ ``{"severity", "priority"}``

    The helper iterates every copula in the input. If a copula
    has an immediate negation, that copula is rejected and the
    helper continues to the next copula. If any copula matches
    the full pattern (copula → [article?] → [intensifier{0,2}]
    → level → noun), it returns ``True``. If no copula matches
    the pattern, it returns ``False``.

    Examples returning ``True``:
        ``is high priority``
        ``is a high priority issue``
        ``is an extremely high severity issue``
        ``has high severity``
        ``has a high severity impact``
        ``this has a very high priority impact``
        ``this is a very extremely high severity issue``
        # Multiple copulas where a later one is affirmative:
        ``Bumping the retry counter is not safe; this is high priority because it skips CI``
        ``Re-requesting review: this has high severity impact``

    Examples returning ``False``:
        ``is not high priority``           (negation, no later affirmative copula)
        ``is not a high priority issue``  (negation with article, no later affirmative copula)
        ``has no high severity impact``   (negation with article, no later affirmative copula)
        ``not high severity``             (no copula, bare negation)
        ``classified as high priority``   (no copula)
        ``with high priority context``    (no copula)
        ``P0/P1/P2 severity taxonomy``    (no copula)
        ``not actually high priority``    (no copula)
    """
    if not leading_text:
        return False
    text = leading_text.lower()
    # Cycle 9 (dbID 3447899921): insert a whitespace token
    # boundary immediately after a recognized severity/priority
    # noun when it is followed (with no whitespace) by a dash
    # separator. This converts tokens like ``priority\u2014this``
    # (a single token after ``.split()``) into the two-token
    # sequence ``priority this``, so the noun check succeeds.
    # The substitution is restricted to (severity|priority) +
    # dash so it does not affect other compound words (e.g.
    # ``high-priority issue`` keeps its dash intact because
    # ``high`` is not a severity/priority noun).
    text = re.sub(
        r"\b(severity|priority)([\-\u2014\u2013])",
        r"\1 \2",
        text,
    )
    raw_tokens = text.split()
    # Strip trailing punctuation so ``priority:`` matches the
    # ``priority`` noun entry (the cycle-7 regex tolerated this
    # via ``\b`` word boundaries).
    tokens = [t.rstrip(_TEXT_SEVERITY_TRAILING_PUNCT) for t in raw_tokens]
    n = len(tokens)
    for i, tok in enumerate(tokens):
        if tok not in _TEXT_SEVERITY_VERBS_SET:
            continue
        # Found a copula. Validate the pattern that follows.
        j = i + 1
        # Definitive negation immediately after the copula.
        # Cycle-10 fix (Codex 3448488549): the previous helper
        # returned ``False`` for the WHOLE helper on this branch,
        # which silently filtered out a real P1 finding whenever
        # the comment contained ANY negated copula early in the
        # body. Reject only THIS candidate copula and continue
        # scanning subsequent copulas so a later affirmative
        # declaration (e.g. ``Bumping the retry counter is not
        # safe; this is high priority because it skips CI``)
        # still triggers the Guard 6 rescue.
        if j < n and tokens[j] in _TEXT_SEVERITY_NEGATIONS:
            continue
        # Optional article.
        if j < n and tokens[j] in _TEXT_SEVERITY_ARTICLES_SET:
            j += 1
        # Up to MAX intensifiers (whitelist only — arbitrary words
        # between article and level are not accepted).
        intensifier_count = 0
        while (
            j < n
            and tokens[j] in _TEXT_SEVERITY_INTENSIFIERS_SET
            and intensifier_count < _MAX_TEXT_SEVERITY_INTENSIFIERS
        ):
            j += 1
            intensifier_count += 1
        # Required level.
        if j >= n or tokens[j] not in _TEXT_SEVERITY_LEVELS_SET:
            continue  # try the next copula, if any
        j += 1
        # Required noun.
        if j >= n or tokens[j] not in _TEXT_SEVERITY_NOUNS_SET:
            continue  # try the next copula, if any
        return True
    return False


def is_coordination_comment(body: str) -> bool:
    """Return True if ``body`` matches a coordination-comment
    pattern (human PR-author messages that re-request Codex
    review, nudge the Codex bot, or describe which fix addresses
    a prior finding).

    These comments contain Codex needles and would otherwise be
    misclassified as blocking findings, even though they are
    coordination messages and not actual review findings. The
    check is case-insensitive substring matching against
    :data:`_COORDINATION_PATTERNS`.

    Guard 1: a body that contains an explicit P0/P1/P2 SEVERITY
    DECLARATION (i.e. ``P0:``/``P1:``/``P2:`` followed by a
    colon) is NEVER treated as a coordination comment, even if
    it also matches a coordination pattern. This prevents the
    broad patterns (``@codex``, ``bumping``, etc.) from
    silently discarding real blockers like
    ``P1: @codex flagged a security issue; must fix`` (Codex
    finding AJ).

    The colon requirement distinguishes a severity declaration
    (``P1: ...``) from a reference to a prior finding
    (``The active P1 current-head finding``), which commonly
    appears in coordination comments describing which fix
    addresses which prior finding.

    Guard 2: a body that contains a badge-style severity marker
    (``![P0 Badge]``, ``![P1 Badge]``, ``![P2 Badge]``,
    ``![P3 Badge]``) or a bracketed priority marker
    (``[P0]``, ``[P1]``, ``[P2]``) is NEVER treated as a
    coordination comment, even if it also matches a
    coordination pattern. Badge-formatted and bracketed
    findings are always real findings — these formats
    indicate actual review findings rather than coordination
    messages. Without this guard, a finding like
    ``![P1 Badge] Bumping the retry counter can skip failures``
    or ``[P1] Bumping the retry counter can skip failures``
    would be silently dropped (Codex findings AK and AM).
    """
    body_str = body or ""
    upper = body_str.upper()
    for sev in ("P0", "P1", "P2"):
        # Guard 1: explicit P0:/P1:/P2: severity declaration.
        if sev + ":" in upper:
            return False
        # Guard 2: badge-style severity marker.
        if f"![{sev} BADGE]" in upper:
            return False
        # Guard 3: bracketed priority marker.
        if f"[{sev}]" in upper:
            return False
    # Guard 4 (Fix AO, Codex Finding AO): a body that STARTS
    # with an explicit severity DECLARATION (in any recognized
    # format) or a blocking-word indicator must NOT be dropped
    # as a coordination comment, even if it also contains a
    # coordination pattern elsewhere. This catches the
    # ``High severity: ...`` text-alias form (mapped to P1 by
    # :func:`extract_severity`) and the ``Codex finding: ...
    # must fix`` form (mapped to UNSPECIFIED_BLOCKING by
    # :func:`is_blocking`). Without this guard, a genuine
    # finding like
    # ``High severity: Bumping retry counter can skip failures``
    # or
    # ``Codex finding: Bumping retry counter can skip failures
    # — must fix`` would be silently dropped by the
    # coordination skip.
    #
    # The check is intentionally targeted to START-of-body
    # declarations to avoid false positives from bare P-token
    # references or blocking-word mentions in the middle of
    # coordination text (e.g. ``Re-requesting Codex review...
    # The active P1 current-head finding has been addressed...
    # allowing a malformed checkpoint...``).
    body_lower = body_str.lower().lstrip()
    # Text-alias severity declarations at start of body.
    text_alias_decls = (
        "high severity:",
        "medium:",
        "low:",
    )
    if any(body_lower.startswith(decl) for decl in text_alias_decls):
        return False
    # Blocking-word indicators at start of body.
    if any(body_lower.startswith(bw) for bw in BLOCKING_WORDS):
        return False
    # Coordination check: only flag as coordination if the
    # body STARTS with a coordination pattern. This prevents
    # coordination messages that contain blocking-word
    # mentions mid-sentence from being incorrectly classified
    # as findings, while still skipping coordination messages
    # that begin with ``Re-requesting``, ``Gentle nudge``,
    # ``Bumping``, etc.
    #
    # Guard 5 (Fix AQ, Codex Finding AQ): a body that STARTS
    # with a coordination pattern must NOT be treated as a
    # coordination comment if it ALSO contains a blocking-word
    # indicator within the LEADING 100 characters. Real
    # coordination messages like ``Re-requesting Codex review
    # on 3982ee6 (Fix AF). The active P1 current-head
    # finding ...`` or ``Bumping this thread — Fix AG is now
    # on 266a92e.`` are typically 100+ characters and only
    # mention blocking vocabulary (``stale``, ``malformed``)
    # in the meta-discussion about which fix addressed which
    # prior finding, well past the leading 100 characters.
    #
    # Real blocker findings that happen to start with a
    # coordination word — e.g. ``Bumping the retry counter can
    # fail when Codex reruns after a stale head`` — DO
    # contain blocking vocabulary (``can fail``, ``stale``)
    # tightly within the leading 100 characters and must be
    # detected as findings rather than silently dropped.
    # The previous ``startswith``-only check returned before
    # ``is_blocking()`` could run, so the gate could report
    # clean despite an unresolved blocker.
    leading = body_str[:100].lower()
    if any(bw in leading for bw in BLOCKING_WORDS):
        return False
    # Guard 6 (PR #405 fresh Codex reviews, 2026-06-21T02:15:04Z,
    # 2026-06-21T02:46:26Z, 2026-06-21T02:54:45Z,
    # 2026-06-21T03:01:27Z, 2026-06-21T03:25:34Z, and
    # 2026-06-21T03:52:50Z, dbIDs 3447794638 + 3447818802 +
    # 3447825478 + 3447830523 + 3447849261 + 3447871114): a
    # body that STARTS with a coordination pattern but
    # declares severity using a text alias in the leading 100
    # characters must NOT be treated as a coordination
    # comment. The previous Guard 4 only protected the
    # colon-start forms (``high severity:`` at body start),
    # so a real P1/P2/P3 text-severity finding like
    # ``Bumping the retry counter is high severity ... must
    # fix`` was silently dropped as coordination.
    #
    # The initial implementation used a plain substring tuple
    # of the ``severity`` and ``priority`` noun forms. A
    # follow-up Codex review found a false-positive rescue:
    # a coordination message saying ``Re-requesting Codex
    # review — this is not high priority`` matched the
    # literal ``high priority`` substring and was incorrectly
    # classified as a P1 finding. The cycle-5 implementation
    # used a regex with affirmative verbs
    # (``is``/``has``/``as``/``with``) before the priority or
    # severity token, which rejected negations but still
    # rescued meta-discussion forms like ``classified as
    # high priority`` and ``with high priority context``
    # (dbID 3447830523). The cycle-6 implementation narrowed
    # the verb set to copulas (``is``/``has``) only and
    # dropped the optional ``a``/``an`` article, which fixed
    # the meta-priority false-positive but created a new
    # false-negative: real Codex findings phrased as ``is a
    # high priority issue`` or ``has a high severity impact``
    # no longer matched the regex and were dropped as
    # coordination before ``extract_severity`` could classify
    # them (dbID 3447849261). The cycle-7 implementation
    # restored the optional article but still required the
    # level token to come IMMEDIATELY after the article,
    # which dropped real findings phrased with an intensifier
    # between the article and the level — e.g. ``is an
    # extremely high severity issue`` (dbID 3447871114).
    #
    # The cycle-8 implementation replaces the regex with a
    # small table-driven helper
    # (:func:`_has_direct_text_severity_declaration`) that
    # tokenizes the leading window and verifies the
    # copula → [negation?] → [article?] → [intensifier{0,2}]
    # → level → noun shape using explicit word tables.
    # This:
    #   - keeps the copula verb set narrowed to ``is``/``has``
    #     (preserves cycle-6/7 protections against meta
    #     ``as``/``with`` phrases),
    #   - accepts the optional ``a``/``an`` article
    #     (restores cycle-7 protection against false-negative
    #     on direct article-bearing declarations),
    #   - accepts up to TWO intensifier tokens from a fixed
    #     whitelist between the article and the level
    #     (new in cycle 8; rejects arbitrary words to avoid
    #     regex whack-a-mole),
    #   - treats negation immediately after a copula as
    #     definitive (preserves cycle-5 negation rejection),
    # while still rescuing every form from prior cycles:
    #   - direct copula:    ``is high priority``
    #   - article copula:   ``is a high priority issue``
    #   - intensifier:      ``is an extremely high severity issue``
    #   - subject pronoun:  ``this has a very high priority impact``
    # and still rejecting every meta/context/negated form:
    #   - ``classified as high priority`` (no copula)
    #   - ``flagged as high severity`` (no copula)
    #   - ``with high priority context`` (no copula)
    #   - ``described as a high priority issue`` (no copula)
    #   - ``P0/P1/P2 severity taxonomy`` (no copula)
    #   - ``high priority context only`` (no copula)
    #   - ``is not high priority`` (negation after copula)
    #   - ``is not a high priority issue`` (negation with article)
    #   - ``has no high severity impact`` (negation with article)
    # The leading-100-char window keeps the rescue narrow
    # and matches the leading-window pattern of Guard 5.
    if _has_direct_text_severity_declaration(leading):
        return False
    return any(body_lower.startswith(pat) for pat in _COORDINATION_PATTERNS)

SEVERITY_RECORDS = {"P0": "P0", "P1": "P1", "P2": "P2", "P3": "P3"}
SEVERITY_MAP = {
    "high": "P1",
    "medium": "P2",
    "low": "P3",
}


# ---------------------------------------------------------------------------
# GitHub API helpers (list-argv, no shell=True)
# ---------------------------------------------------------------------------

# --------------------------------------------------------------------------
# GitHub GraphQL helper (review thread resolution state)
# --------------------------------------------------------------------------


def gh_graphql_review_threads(
    repo: str, pr_number: int
) -> tuple[bool, list[dict[str, Any]], str]:
    """
    Fetch PR review-thread resolution state via GraphQL.

    Returns (success, threads_list, error_msg).
    threads_list entries: {id, isResolved, isOutdated, comments: [{databaseId, url}]}

    Note: gh api graphql -f passes all variables as strings, which GraphQL rejects
    for Int. We embed the PR number as a raw integer literal in the query.
    Also note: nested braces must be balanced; comments(first:50) has its own
    nodes subfield requiring a closing '}' before the comments block closes.
    """
    owner, name = repo.split("/", 1)
    # Build with explicit brace counting via a list to ensure balance.
    # comments(first:50) { nodes { databaseId url } }
    #                                     ^^--- +1 extra } to close the inner nodes
    query_parts = [
        "query {",
        f'repository(owner:"{owner}", name:"{name}") {{',
        f"pullRequest(number:{pr_number}) {{",
        "reviewThreads(first:100) {",
        "nodes {",
        "id isResolved isOutdated",
        "comments(first:50) { nodes { databaseId url } }",  # note: inner nodes needs extra }
        "}",  # close nodes
        "}",  # close reviewThreads
        "}",  # close pullRequest
        "}",  # close repository
        "}",  # close query
    ]
    query_literal = " ".join(query_parts)
    cmd = ["gh", "api", "graphql", "--raw-field",
           f"query={query_literal}"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
    except OSError as exc:
        return False, [], f"gh graphql invocation failed: {exc}"

    if result.returncode != 0:
        return False, [], f"gh graphql returned {result.returncode}: {result.stderr[:500]}"

    try:
        data = json.loads(result.stdout)
        errors = data.get("errors")
        if errors:
            return False, [], f"GraphQL errors: {errors}"
        nodes = (
            data.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
            .get("nodes", [])
        )
        # Flatten: keep thread metadata + each comment's databaseId/url
        threads: list[dict[str, Any]] = []
        for node in nodes:
            thread_id = node.get("id", "")
            is_resolved = node.get("isResolved", False)
            is_outdated = node.get("isOutdated", False)
            for comment in (node.get("comments", {}) or {}).get("nodes", []):
                threads.append({
                    "thread_id": thread_id,
                    "is_resolved": is_resolved,
                    "is_outdated": is_outdated,
                    "database_id": comment.get("databaseId"),
                    "url": comment.get("url") or "",
                })
        return True, threads, ""
    except (json.JSONDecodeError, KeyError) as exc:
        return False, [], f"invalid GraphQL response: {exc}"


# --------------------------------------------------------------------------
# GitHub REST API helpers (list-argv, no shell=True)
# --------------------------------------------------------------------------


def gh_api(repo: str, endpoint: str) -> tuple[bool, list[dict[str, Any]], str]:
    """
    Call `gh api` for the given endpoint (no leading slash).

    Returns (success, data_list, error_msg).
    Fails closed: any non-zero return code, stderr, or bad JSON => error.

    Uses ``--paginate --slurp`` so that multi-page responses are
    wrapped into a single JSON array of arrays, which is then
    flattened into a single list of items. Without ``--slurp``,
    ``gh api --paginate`` writes each page as a separate JSON
    document and ``json.loads`` fails on the concatenated output
    (Codex finding AH).
    """
    cmd = [
        "gh", "api", f"repos/{repo}/{endpoint}",
        "--paginate", "--slurp",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
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

    # ``--slurp`` wraps all pages into a single JSON array, so
    # the result is either:
    #   - a list of items (single page)
    #   - a list of lists (multi-page: each page is a list)
    # Flatten the latter into a single list of items.
    if isinstance(data, list):
        flat: list[dict[str, Any]] = []
        for item in data:
            if isinstance(item, list):
                flat.extend(item)
            else:
                flat.append(item)
        return True, flat, ""
    return True, [data], ""


def gh_pr_view(repo: str, pr_number: int) -> tuple[bool, dict[str, Any], str]:
    """Return --json fields needed for SHA alignment check.

    Uses `gh api repos/.../pulls/{n}` rather than `gh pr view` to avoid
    a git-repository requirement in the caller's cwd.  `gh pr view` invokes
    git status internally, which fails when run from /tmp or any non-git
    directory.  The REST call returns the same headRefOid field.
    """
    cmd = [
        "gh", "api",
        f"repos/{repo}/pulls/{pr_number}",
        "--jq", "{sha:.head.sha, state:.state, url:.url}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except OSError as exc:
        return False, {}, f"gh api invocation failed: {exc}"
    if result.returncode != 0:
        return False, {}, f"gh api returned {result.returncode}: {result.stderr[:300]}"
    try:
        parsed = json.loads(result.stdout)
        # Normalise key names to match what the rest of the module expects
        return True, {"headRefOid": parsed.get("sha", ""), "state": parsed.get("state", ""), "url": parsed.get("url", "")}, ""
    except json.JSONDecodeError:
        return False, {}, "gh api --jq returned non-JSON"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def extract_severity(text: str) -> str | None:
    """Return P0-P3 from text or None if not found.

    Priority order (highest specificity first):

    1. Badge-style severity marker: ``![P0 Badge]``, ``![P1 Badge]``,
       ``![P2 Badge]``, ``![P3 Badge]``. The badge URL contains
       ``badge/P1-orange`` etc. which would otherwise be matched as
       a plain ``P1`` substring; the badge wrapper is the
       unambiguous signal that the author is declaring severity.
    2. Bracketed priority marker: ``[P0]``, ``[P1]``, ``[P2]``,
       ``[P3]``. This is the second-most-specific declaration form
       used by some Codex findings.
    3. Explicit colon declaration: ``P0:``, ``P1:``, ``P2:``,
       ``P3:`` followed by a colon. The colon distinguishes a
       declaration from a reference (e.g. "the active P1
       current-head finding has been addressed" contains ``P1``
       but not ``P1:``).
    4. Plain ``P0``/``P1``/``P2``/``P3`` substring (the previous
       behavior). Kept last because it is the most ambiguous:
       a comment body that documents the severity taxonomy
       ("P0/P1/P2 findings") contains all three tokens; the
       first one (``P0``) would otherwise be picked
       incorrectly.
    5. Text-alias ``high``/``medium``/``low`` matched as whole
       WORDS using regex word boundaries. Same rationale as
       before (Codex finding AI).

    Codex findings K87fX (Do not skip bracketed priority
    findings) and K8vlc (Narrow coordination skips) both have
    bodies that include ``P0/P1/P2`` as part of describing the
    severity taxonomy. Under the previous substring-first order,
    these were misclassified as P0 even though the actual
    declared severity is P1 (K87fX) and P2 (K8vlc). The
    badge-priority fix ensures the declared severity is the
    one returned, not the first substring match.
    """
    upper = text.upper()

    # Priority 1: badge-style severity marker.
    for sev in ("P0", "P1", "P2", "P3"):
        if f"![{sev} BADGE]" in upper:
            return sev

    # Priority 2: bracketed priority marker.
    for sev in ("P0", "P1", "P2", "P3"):
        if f"[{sev}]" in upper:
            return sev

    # Priority 3: explicit colon declaration.
    for sev in ("P0", "P1", "P2", "P3"):
        if sev + ":" in upper:
            return sev

    # Priority 4: plain P-token substring (legacy behavior,
    # only reached if no more-specific form was found).
    for sev in ("P0", "P1", "P2", "P3"):
        if sev in upper:
            return sev

    # Priority 5: text-alias severity declarations.
    for token, sev in SEVERITY_MAP.items():
        # Use regex word-boundary matching for the text aliases
        # to avoid false positives like "highlight" matching
        # "high". P0-P3 tokens are already unambiguous.
        if re.search(r"\b" + re.escape(token) + r"\b", text, re.IGNORECASE):
            return sev
    return None


def is_blocking(text: str) -> bool:
    """Return True if an unspecified-severity comment contains blocking words."""
    lower = text.lower()
    return any(bw in lower for bw in BLOCKING_WORDS)


def make_finding_id(
    user: str,
    file_path: str,
    line: str,
    severity: str,
    body: str,
) -> str:
    """
    Deterministic, stable finding ID derived from content fields.
    Format: codex-<12-char-sha256>
    Same finding harvested from any endpoint -> same ID.
    source_kind is NOT included so duplicate endpoints merge correctly.
    """
    normalized = re.sub(r"\s+", " ", body).strip()
    payload = "|".join([
        user, file_path, str(line), severity,
        normalized[:200],
    ])
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"codex-{digest}"


def classify_item(item: dict[str, Any], source_kind: str, ignore_users: set[str]) -> list[dict[str, Any]]:
    """
    Given a single comment/review dict from any endpoint, scan for Codex
    findings and return a list of finding dicts (may be empty).
    """
    findings = []
    user = (item.get("user") or {}).get("login", "")
    if user in ignore_users:
        return findings

    body = item.get("body") or ""

    # Human PR-author coordination comments (re-requests, nudges,
    # fix descriptions) are NOT findings. They contain Codex
    # needles and would otherwise be misclassified as blocking
    # findings. The check is source-agnostic: any comment body
    # matching a coordination pattern is skipped, regardless of
    # whether it arrived as an issue comment, inline review
    # comment, or review submission. This prevents false-positive
    # gate failures caused by the human workflow of re-requesting
    # Codex review after each fix push.
    if is_coordination_comment(body):
        return findings

    state = item.get("state") or ""
    file_path = item.get("path") or ""
    line = item.get("line") or item.get("original_line") or ""
    commit_id = item.get("commit_id") or ""
    html_url = item.get("html_url") or item.get("url") or ""

    combined = f"{body} {user} {state} {file_path}".lower()
    if not any(needle in combined for needle in CODEX_NEEDLES):
        return findings

    # Classify severity: explicit P0-P3 tokens take priority. High/Medium/Low are
    # mapped. Only if no severity keyword is found do we check blocking words.
    severity = extract_severity(combined)
    if severity is None and is_blocking(combined):
        severity = "UNSPECIFIED_BLOCKING"
    elif severity is None:
        severity = "UNSPECIFIED_INFO"

    finding_id = make_finding_id(user, file_path, str(line), severity, body)
    finding = {
        "finding_id": finding_id,
        "user": user,
        "body": body,
        "severity": severity,
        "state": state,
        "file_path": file_path,
        "line": line,
        "commit_id": commit_id[:12] if commit_id else "",
        "url": html_url,
    }
    findings.append(finding)
    return findings


def load_waiver(path: str, pr_number: int, reported_sha: str) -> tuple[bool, dict[str, Any], str]:
    """Load and validate a waiver JSON file. Fails if SHA mismatches."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return False, {}, f"waiver file unreadable: {exc}"

    if data.get("pr_number") != pr_number:
        return False, {}, f"waiver pr_number {data.get('pr_number')} != {pr_number}"
    if data.get("reported_head_sha") != reported_sha:
        return False, {}, (
            f"waiver head SHA {data.get('reported_head_sha')} "
            f"!= reported {reported_sha}"
        )

    return True, data, ""


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def dedup_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Remove duplicate findings by finding_id.
    Same finding from different endpoints (inline_review_comment, per_review_comment,
    etc.) collapses into one entry with a 'sources' list.
    """
    merged: dict[str, dict[str, Any]] = {}
    for f in findings:
        fid = f.get("finding_id", "")
        if not fid:
            # Pre-v1: create deterministic ID from user+body
            user_str = f["user"] if isinstance(f["user"], str) else f["user"].get("login", "")
            key_payload = f"pre-v1|{user_str}|{f['body'][:200]}"
            fid = f"pre-v1-{hashlib.sha256(key_payload.encode()).hexdigest()[:12]}"

        if fid in merged:
            # Collapse duplicate: merge source endpoints
            existing = merged[fid]
            src = f.get("_source_kind", "unknown")
            if "sources" not in existing:
                existing["sources"] = [src]
            elif src not in existing["sources"]:
                existing["sources"].append(src)
            # Preserve non-empty URL if we didn't have one
            if not existing.get("url") and f.get("url"):
                existing["url"] = f["url"]
        else:
            f["sources"] = [f.get("_source_kind", "unknown")]
            merged[fid] = f

    return list(merged.values())


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_md(
    status: str,
    pr_number: int,
    reported_sha: str,
    live_sha: str,
    sha_mismatch: bool,
    sources: list[str],
    findings: list[dict[str, Any]],
    current_head_blockers: list[dict[str, Any]],
    stale_blockers: list[dict[str, Any]],
    resolved_stale_blockers: list[dict[str, Any]],
    resolved_non_blockers: list[dict[str, Any]],
    waivers: list[dict[str, Any]],
    counts: dict[str, int],
    thread_api_error: str | None = None,
) -> str:
    lines = [
        f"# PR Review Comment Gate — PR #{pr_number}\n",
        f"**Reported head SHA:** `{reported_sha}`  ",
        f"**Live head SHA:** `{live_sha}`  ",
        f"**Status:** `{status}`  ",
        f"**Harvested at:** {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}\n",
    ]
    if sha_mismatch:
        lines.append("**⚠️  Live SHA mismatch — waivers blocked, status is INCONCLUSIVE.**\n")
    lines.extend([
        f"## Summary\n",
        f"| Severity | Count |\n",
        f"|---|---|\n",
    ])
    for sev in ("P0", "P1", "P2", "P3", "UNSPECIFIED_BLOCKING", "UNSPECIFIED_INFO"):
        count = counts.get(sev, 0)
        lines.append(f"| {sev} | {count} |\n")
    lines.extend([
        f"\n**Blocked:** {counts.get('blocked', 0)}  ",
        f"**Waived:** {counts.get('waived', 0)}\n",
        f"## Sources Fetched\n",
    ])
    for src in sources:
        lines.append(f"- {src}\n")
    lines.append(f"\n## Findings\n")
    if not findings:
        lines.append("_No Codex/automated-review findings detected._\n")
    for f in findings:
        waiver_str = " *(waived)*" if f.get("_waived") else ""
        stale_tag = " *(STALE)*" if f.get("is_stale_head") else " *(CURRENT)*"
        thread_tag = ""
        if f.get("thread_resolved"):
            thread_tag = " *(thread:RESOLVED)*"
        elif f.get("thread_id"):
            thread_tag = " *(thread:OPEN)*"
        sev = f["severity"]
        lines.extend([
            f"### {sev} — {f['user']} @ {f['file_path']}:{f['line']}{waiver_str}{stale_tag}{thread_tag}\n",
            f"- URL: {f['url'] or 'N/A'}\n",
            f"- Commit: `{f['commit_id']}`\n",
            f"- Thread: `{f.get('thread_id', 'N/A')}`\n",
            f"\n{f['body'][:2000]}\n",
        ])
    lines.append(f"\n## Current-Head Blockers\n")
    if not current_head_blockers:
        lines.append("_No current-head blockers._\n")
    else:
        for b in current_head_blockers:
            thread_tag = ""
            if b.get("thread_id"):
                state = "RESOLVED" if b.get("thread_resolved") else "OPEN"
                thread_tag = f" [thread:{state}]"
            lines.append(
                f"- **[{b['severity']}]** {b['user']} — {b['file_path']}:{b['line']}  "
                f"[link]({b['url']}){thread_tag}\n"
            )
            lines.append(f"  {b['body'][:300]}\n")
    if stale_blockers:
        lines.append(f"\n## Stale Blockers (require exact-head re-review — INCONCLUSIVE)\n")
        for b in stale_blockers:
            lines.append(
                f"- **[{b['severity']}]** {b['user']} — {b['file_path']}:{b['line']}  "
                f"[link]({b['url']})  *(STALE — attached to old commit)*\n"
            )
            lines.append(f"  {b['body'][:300]}\n")
    if resolved_stale_blockers:
        lines.append(f"\n## Resolved Stale Blockers (reported as history — not blocking)\n")
        for b in resolved_stale_blockers:
            lines.append(
                f"- **[{b['severity']}]** {b['user']} — {b['file_path']}:{b['line']}  "
                f"[link]({b['url']})  *(STALE + THREAD RESOLVED — reported as history)*\n"
            )
            lines.append(f"  thread_id: `{b.get('thread_id', 'N/A')}`\n")
            lines.append(f"  {b['body'][:300]}\n")
    if resolved_non_blockers:
        lines.append(f"\n## Resolved Review Threads (not blocking)\n")
        for b in resolved_non_blockers:
            lines.append(
                f"- **[{b['severity']}]** {b['user']} — {b['file_path']}:{b['line']}  "
                f"[link]({b['url']})  *(RESOLVED — not blocking)*\n"
            )
            lines.append(f"  thread_id: `{b.get('thread_id', 'N/A')}`\n")
            lines.append(f"  {b['body'][:300]}\n")
    lines.append(f"\n## P2 Waivers\n")
    if not waivers:
        lines.append("_No waivers applied._\n")
    else:
        for w in waivers:
            lines.append(
                f"- **{w['finding_id']}** ({w['severity']}): "
                f"{w['reason']}  "
                f"[expires after PR #{w.get('expires_after_pr', '?')}]\n"
            )
    lines.append(f"\n## Recommended Action\n")
    if status == "REVIEW_COMMENTS_CLEAN":
        lines.append(
            "✅ All findings resolved or waived. Safe to proceed to `final_gate_status.py`.\n"
        )
    elif status == "REVIEW_COMMENTS_BLOCKED":
        lines.append(
            "❌ Unresolved current-head blockers remain. Fix or explicitly waive before proceeding.\n"
        )
    elif stale_blockers:
        lines.append(
            "⚠️  Stale P0/P1 findings attached to old commits — not indefinitely blocking.\n"
            "    Trigger an exact-head Codex re-review to clear stale blockers.\n"
            "    Status is INCONCLUSIVE until clean exact-head review evidence exists.\n"
        )
    else:
        lines.append(
            "⚠️  Could not determine status. Review API errors and retry.\n"
        )
    return "".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

EXIT_CLEAN = 0
EXIT_BLOCKED = 1
EXIT_INCONCLUSIVE = 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch and classify GitHub PR review comments."
    )
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--reported-head-sha", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument(
        "--allow-p2-waivers", default=None,
        help="Path to JSON waiver file (optional)"
    )
    parser.add_argument(
        "--fail-on-p2", action="store_true",
        help="Treat P2 as blocking even without a waiver"
    )
    parser.add_argument(
        "--ignore-users", default="",
        help="Comma-separated logins to ignore"
    )
    args = parser.parse_args()

    ignore_users = set(u.strip() for u in args.ignore_users.split(",") if u.strip())

    # Policy safeguard: refuse to silently ignore the Codex bot.
    # The chatgpt-codex-connector[bot] is the source of all automated
    # review findings for this repository. Globally ignoring its
    # findings via --ignore-users would re-introduce the gate
    # false-negative that caused PR #405's review-comment-gate to be
    # green while 18 unresolved P1/P2 Codex findings remained
    # actionable.
    #
    # If a legitimate need to ignore the Codex bot arises (e.g. a
    # coordination-noise experiment), the caller must opt in
    # explicitly by setting AED_ALLOW_CODEX_IGNORE=1 in the
    # environment. The override is logged to stderr so it is visible
    # in CI output.
    CODEX_BOT_LOGIN = "chatgpt-codex-connector[bot]"
    if CODEX_BOT_LOGIN in ignore_users:
        if os.environ.get("AED_ALLOW_CODEX_IGNORE") != "1":
            print(
                f"ERROR: --ignore-users contains '{CODEX_BOT_LOGIN}' "
                f"but AED_ALLOW_CODEX_IGNORE is not set to '1'.",
                file=sys.stderr,
            )
            print(
                "Refusing to silently filter all Codex findings. "
                "Codex review findings must be classified by the "
                "gate, not globally ignored. Set "
                "AED_ALLOW_CODEX_IGNORE=1 only if you have an "
                "explicit, documented reason to bypass this "
                "safeguard.",
                file=sys.stderr,
            )
            return 1
        print(
            f"WARNING: ignoring '{CODEX_BOT_LOGIN}' per "
            f"AED_ALLOW_CODEX_IGNORE=1",
            file=sys.stderr,
        )

    all_findings: list[dict[str, Any]] = []
    sources_fetched: list[str] = []
    api_errors: list[str] = []

    # 1. Issue comments
    ok, data, err = gh_api(args.repo, f"issues/{args.pr_number}/comments")
    if not ok:
        api_errors.append(f"issue_comments: {err}")
    else:
        sources_fetched.append(f"issues/{args.pr_number}/comments ({len(data)} items)")
        for item in data:
            findings = classify_item(item, "issue_comment", ignore_users)
            for f in findings:
                f["_source_kind"] = "issue_comment"
            all_findings.extend(findings)

    # 2. Inline PR review comments
    ok, data, err = gh_api(args.repo, f"pulls/{args.pr_number}/comments")
    if not ok:
        api_errors.append(f"inline_review_comments: {err}")
    else:
        sources_fetched.append(f"pulls/{args.pr_number}/comments ({len(data)} items)")
        for item in data:
            findings = classify_item(item, "inline_review_comment", ignore_users)
            for f in findings:
                f["_source_kind"] = "inline_review_comment"
            all_findings.extend(findings)

    # 3. PR reviews
    ok, data, err = gh_api(args.repo, f"pulls/{args.pr_number}/reviews")
    if not ok:
        api_errors.append(f"reviews: {err}")
    else:
        sources_fetched.append(f"pulls/{args.pr_number}/reviews ({len(data)} items)")
        for item in data:
            findings = classify_item(item, "review", ignore_users)
            for f in findings:
                f["_source_kind"] = "review"
            all_findings.extend(findings)
            # 4. Per-review comments
            rev_id = item.get("id")
            if rev_id:
                ok2, comments2, err2 = gh_api(
                    args.repo, f"pulls/{args.pr_number}/reviews/{rev_id}/comments"
                )
                if not ok2:
                    api_errors.append(f"review_{rev_id}_comments: {err2}")
                else:
                    sources_fetched.append(
                        f"pulls/{args.pr_number}/reviews/{rev_id}/comments ({len(comments2)} items)"
                    )
                    for c in comments2:
                        findings2 = classify_item(c, "per_review_comment", ignore_users)
                        for f2 in findings2:
                            f2["_source_kind"] = "per_review_comment"
                        all_findings.extend(findings2)

    all_findings = dedup_findings(all_findings)

    # -----------------------------------------------------------------------
    # Review-thread resolution state via GraphQL (read-only)
    # -----------------------------------------------------------------------
    # Build a mapping: finding URL -> thread metadata (isResolved, isOutdated, thread_id).
    # Findings in resolved threads are reported but do not block.
    # Key: URL (from inline_review_comment or per_review_comment).
    # Fallback key: "databaseId:<id>" for findings with a known databaseId.
    thread_meta_by_url: dict[str, dict[str, Any]] = {}
    thread_api_error: str | None = None
    ok_threads, thread_entries, err_threads = gh_graphql_review_threads(
        args.repo, args.pr_number
    )
    if not ok_threads:
        thread_api_error = err_threads
    else:
        for entry in thread_entries:
            url = entry.get("url", "")
            if url:
                thread_meta_by_url[url] = entry

    # Attach thread metadata to each finding by URL.
    for f in all_findings:
        url = f.get("url", "")
        meta = thread_meta_by_url.get(url, {})
        f["thread_id"] = meta.get("thread_id", "")
        f["thread_resolved"] = meta.get("is_resolved", False)
        f["thread_outdated"] = meta.get("is_outdated", False)

    # P1-B: Verify live head SHA against --reported-head-sha.
    # This check MUST happen before any waiver loading or blocker classification.
    # A stale SHA can never reach the waiver-loading code path.
    live_head_sha = ""
    head_sha_mismatch = False
    ok_live, live_data, err_live = gh_pr_view(args.repo, args.pr_number)
    if not ok_live:
        api_errors.append(f"live_pr_fetch: {err_live}")
        head_sha_mismatch = True
    else:
        live_head_sha = live_data.get("headRefOid", "")
        if live_head_sha and live_head_sha != args.reported_head_sha:
            api_errors.append(
                f"live_head_mismatch: reported={args.reported_head_sha[:8]} "
                f"live={live_head_sha[:8]} — waivers blocked until SHA is corrected"
            )
            head_sha_mismatch = True

    # -----------------------------------------------------------------------
    # FAIL-FAST: if head SHA mismatch, do not load or apply any waivers.
    # load_waiver() and waiver application are UNREACHABLE here.
    # -----------------------------------------------------------------------
    if head_sha_mismatch:
        # Stale/current-head classification is skipped on mismatch.
        # Findings are reported as-harvested; no waivers applied.
        output = {
            "status": "REVIEW_COMMENTS_INCONCLUSIVE",
            "pr_number": args.pr_number,
            "reported_head_sha": args.reported_head_sha,
            "live_head_sha": live_head_sha,
            "head_sha_mismatch": head_sha_mismatch,
            "harvested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "sources_fetched": sources_fetched,
            "api_errors": api_errors,
            "findings": all_findings,
            "blockers": [],
            "stale_blockers": [],
            "resolved_stale_blockers": [],
            "resolved_non_blockers": [],
            "stale_findings_summary": {
                "total_stale": 0,
                "stale_blockers": 0,
                "resolved_stale_blockers": 0,
                "stale_finding_ids": [],
            },
            "current_head_findings_count": 0,
            "stale_findings_count": 0,
            "p2_waivers": [],
            "summary_counts": {},
            "thread_api_error": None,
        }
        Path(args.output_json).write_text(json.dumps(output, indent=2))
        md = f"# PR Review Comment Gate — PR #{args.pr_number}\n\n"
        md += f"**Reported head SHA:** `{args.reported_head_sha}`  \n"
        md += f"**Live head SHA:** `{live_head_sha}`  \n"
        md += f"**Status:** `REVIEW_COMMENTS_INCONCLUSIVE`  \n"
        md += f"\n**⚠️  Live SHA mismatch — waivers blocked, status is INCONCLUSIVE.**\n\n"
        md += f"**Error:** {api_errors[0]}\n\n"
        md += f"_Findings are reported as-harvested; no waivers applied on mismatch._\n"
        md += f"_Trigger an exact-head Codex re-review to clear this state._\n"
        Path(args.output_md).write_text(md)
        print(f"[check_pr_review_comments] status=REVIEW_COMMENTS_INCONCLUSIVE "
              f"(head_sha_mismatch=True, waivers unreachable)")
        return EXIT_INCONCLUSIVE

    # -----------------------------------------------------------------------
    # Stale vs current-head classification
    # -----------------------------------------------------------------------
    # A finding is "current-head" if its commit_id matches the live PR head SHA
    # (GitHub stores 12-char prefixes on inline/per-review comments).
    # A finding with no commit_id is treated as current-head (pre-v1 compat).
    # Findings attached to an older commit are "stale" — they represent issues
    # that were already addressed in later commits and must not indefinitely
    # block the gate.
    live_head_12 = live_head_sha[:12] if live_head_sha else ""

    current_head_findings: list[dict[str, Any]] = []
    stale_findings: list[dict[str, Any]] = []

    for f in all_findings:
        fid_commit = f.get("commit_id", "")
        if not fid_commit:
            # Pre-v1 finding or comment without commit_id — treat as current.
            is_current = True
            is_stale = False
        elif fid_commit == live_head_12:
            is_current = True
            is_stale = False
        else:
            is_current = False
            is_stale = True
        f["is_current_head"] = is_current
        f["is_stale_head"] = is_stale
        if is_current:
            current_head_findings.append(f)
        else:
            stale_findings.append(f)

    # Load waivers (only reached when live head == reported head — mismatch impossible here).
    waivers_applied: list[dict[str, Any]] = []
    waiver_map: dict[str, dict[str, Any]] = {}
    if args.allow_p2_waivers:
        ok, waiver_data, err = load_waiver(
            args.allow_p2_waivers, args.pr_number, args.reported_head_sha
        )
        if not ok:
            print(f"WAIVER FILE INVALID: {err}", file=sys.stderr)
            # Fail closed: invalid waiver => do not apply waivers
            args.allow_p2_waivers = None
        else:
            for w in waiver_data.get("waivers", []):
                waiver_map[w.get("finding_id", "")] = w

    # Mark current-head findings as waived.
    # Waivers only apply to current-head findings — stale findings cannot be waived
    # because they represent issues on a superseded commit.
    for f in current_head_findings:
        matched_waiver = None
        fid = f.get("finding_id", "")
        if fid in waiver_map:
            matched_waiver = waiver_map[fid]
        else:
            # Fallback: match by severity + body prefix
            sev = f["severity"]
            body_prefix = f["body"][:100].lower()
            for w in waiver_map.values():
                if (w.get("severity") == sev or w.get("severity") == "P2") and \
                        w.get("body_prefix", "").lower() == body_prefix:
                    matched_waiver = w
                    break
        if matched_waiver:
            f["_waived"] = True
            f["_waiver_reason"] = matched_waiver.get("reason", "")
            waivers_applied.append(matched_waiver)

    # Classify blockers — only current-head findings can block.
    # Stale findings (on older commits) are reported but cannot indefinitely block.
    # Findings in resolved GitHub review threads are reported but do not block.
    # If thread-resolution metadata is unavailable for a P0/P1/P2, fail closed.
    #
    # Resolved stale findings (thread_resolved=True): reported as history, NOT blocking.
    # Unresolved stale findings (thread_resolved=False): INCONCLUSIVE.
    current_head_blockers: list[dict[str, Any]] = []
    stale_blockers: list[dict[str, Any]] = []
    resolved_stale_blockers: list[dict[str, Any]] = []
    resolved_non_blockers: list[dict[str, Any]] = []

    for f in current_head_findings:
        sev = f["severity"]
        thread_resolved = f.get("thread_resolved", False)
        has_thread_meta = bool(f.get("thread_id") or f.get("url"))

        if sev in ("P0", "P1", "UNSPECIFIED_BLOCKING"):
            # P0/P1 always blocking unless resolved via GitHub review thread.
            if thread_resolved:
                resolved_non_blockers.append(f)
            elif not has_thread_meta:
                # No thread metadata — fail closed.
                current_head_blockers.append(f)
            else:
                current_head_blockers.append(f)
        elif sev == "P2":
            if args.fail_on_p2:
                if thread_resolved:
                    resolved_non_blockers.append(f)
                elif not has_thread_meta:
                    current_head_blockers.append(f)
                else:
                    current_head_blockers.append(f)
            else:
                # Default P2: blocks unless waived OR resolved via GitHub thread.
                if thread_resolved:
                    resolved_non_blockers.append(f)
                elif not f.get("_waived"):
                    current_head_blockers.append(f)
        # P3 and UNSPECIFIED_INFO are informational only
    for f in stale_findings:
        sev = f["severity"]
        if sev not in ("P0", "P1", "UNSPECIFIED_BLOCKING", "P2"):
            continue
        thread_resolved = f.get("thread_resolved", False)
        if thread_resolved:
            # Resolved stale findings: reported as history, NOT blocking.
            resolved_stale_blockers.append(f)
        elif sev == "P2" and not args.fail_on_p2:
            # Stale P2 without fail_on_p2: informational only.
            continue
        else:
            # Unresolved stale P0/P1/P2: INCONCLUSIVE.
            stale_blockers.append(f)

    # Count severity buckets
    counts: dict[str, int] = {k: 0 for k in (
        "P0", "P1", "P2", "P3", "UNSPECIFIED_BLOCKING", "UNSPECIFIED_INFO",
        "blocked", "waived",
    )}
    for f in all_findings:
        sev = f["severity"]
        counts[sev] = counts.get(sev, 0) + 1
    counts["blocked"] = len(current_head_blockers)
    counts["waived"] = len(waivers_applied)

    # Status determination:
    # 1. API errors (REST) => INCONCLUSIVE (incomplete data — fail closed)
    # 2. GraphQL thread-resolution failure => INCONCLUSIVE (cannot determine resolved state)
    # 3. Current-head P0/P1/P2 blockers => BLOCKED
    # 4. Unresolved stale P0/P1/P2 blockers => INCONCLUSIVE (stale findings require exact-head re-review)
    # 5. Resolved stale P0/P1/P2 blockers => reported as history, NOT blocking
    # 6. No blockers => CLEAN
    if api_errors:
        status = "REVIEW_COMMENTS_INCONCLUSIVE"
    elif thread_api_error:
        api_errors.append(f"review_threads_graphql: {thread_api_error}")
        status = "REVIEW_COMMENTS_INCONCLUSIVE"
    elif current_head_blockers:
        status = "REVIEW_COMMENTS_BLOCKED"
    elif stale_blockers:
        status = "REVIEW_COMMENTS_INCONCLUSIVE"
    elif all_findings:
        status = "REVIEW_COMMENTS_CLEAN"
    else:
        status = "REVIEW_COMMENTS_CLEAN"

    # stale_findings_summary for reporting
    stale_findings_summary = {
        "total_stale": len(stale_findings),
        "stale_blockers": len(stale_blockers),
        "resolved_stale_blockers": len(resolved_stale_blockers),
        "stale_finding_ids": [f["finding_id"] for f in stale_findings],
    }

    # Write outputs
    output = {
        "status": status,
        "pr_number": args.pr_number,
        "reported_head_sha": args.reported_head_sha,
        "live_head_sha": live_head_sha,
        "head_sha_mismatch": head_sha_mismatch,
        "harvested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sources_fetched": sources_fetched,
        "api_errors": api_errors,
        "thread_api_error": thread_api_error,
        "findings": all_findings,
        "blockers": current_head_blockers,
        "stale_blockers": stale_blockers,
        "resolved_stale_blockers": resolved_stale_blockers,
        "resolved_non_blockers": resolved_non_blockers,
        "stale_findings_summary": stale_findings_summary,
        "current_head_findings_count": len(current_head_findings),
        "stale_findings_count": len(stale_findings),
        "p2_waivers": waivers_applied,
        "summary_counts": counts,
    }

    Path(args.output_json).write_text(json.dumps(output, indent=2))
    md = render_md(
        status, args.pr_number, args.reported_head_sha,
        live_head_sha, head_sha_mismatch,
        sources_fetched, all_findings, current_head_blockers,
        stale_blockers, resolved_stale_blockers, resolved_non_blockers,
        waivers_applied, counts,
        thread_api_error,
    )
    Path(args.output_md).write_text(md)

    print(f"[check_pr_review_comments] status={status} blockers={len(current_head_blockers)} "
          f"stale={len(stale_blockers)} resolved_stale={len(resolved_stale_blockers)} "
          f"resolved={len(resolved_non_blockers)} "
          f"findings={len(all_findings)} waivers={len(waivers_applied)}")

    if status == "REVIEW_COMMENTS_BLOCKED":
        return EXIT_BLOCKED
    if status == "REVIEW_COMMENTS_INCONCLUSIVE":
        return EXIT_INCONCLUSIVE
    return EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())