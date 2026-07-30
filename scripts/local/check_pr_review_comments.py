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
from typing import Any, Optional

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
    # Initial Codex-review request comments posted by the PR
    # author (e.g. the canonical body that begins with
    # ``Codex automated review request for PR #N``). These are
    # coordination messages that name the SHA to be reviewed and
    # the scope to inspect; they are NOT findings. Without this
    # entry, the gate treats every such request as an
    # ``UNSPECIFIED_BLOCKING`` current-head blocker because the
    # body includes meta-vocabulary like ``stale`` / ``malformed``
    # far past the leading 100 chars. The match is exact-prefix,
    # so a Codex finding that incidentally contains the phrase
    # in the middle of its body is unaffected.
    "codex automated review request",
    # Round-81 P1 follow-up: malformed Codex-review request
    # comments that lack the canonical ``@codex review``
    # trigger but begin with the PR author's ``Codex: please
    # re-review`` coordination phrase. These name a specific
    # head SHA, summarize the prior-round repairs, and are NOT
    # findings. Without this entry, the gate treats them as
    # current-head blockers because the body mentions the
    # ``P1`` / ``P2`` severity tokens when describing what the
    # prior-round commits repaired. The match is exact-prefix.
    "codex: please re-review",
)


# ---------------------------------------------------------------------------
# Actionable-finding-signal detector tables.
# ---------------------------------------------------------------------------
# These tables are used by ``_has_direct_text_severity_declaration``
# (the copula-based detector) to identify text-alias severity/priority
# declarations of the shape
#     [subject?] <verb> [negation?] [article?] <intensifier{0,2}> <level> <noun>
# where <verb> is a copula (``is``/``has`` only; ``as`` and ``with``
# are excluded so meta-discussion forms like ``classified as high
# priority`` and ``with high priority context`` do NOT match).
#
# The detector is invoked by ``_has_actionable_finding_signal`` as
# the copula-based actionable-signal component of the centralized
# actionability decision. See that helper for the overall design.
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
# Past-participles / meta-verbs that mark a comment as REFERENCING
# a prior fix rather than DECLARING a current issue. The narrow
# colon-form helper uses this to reject coordination-prefixed
# bodies of the shape
#     ``Re-requesting Codex review: fixed the high severity
#     regression from the prior finding``
# which contain the words ``high severity`` but are
# meta-discussion about a prior cycle's fix (Codex finding
# 3448570717). The list is intentionally short and closed: no
# arbitrary grammar expansion, no natural-language heuristic.
_META_VERBS = frozenset({
    "fixed",
    "addressed",
    "described",
    "classified",
    "flagged",
    "reviewed",
    "identified",
    "mentioned",
})
# Trailing punctuation that may follow a level or noun token
# without breaking the pattern (e.g. ``is medium priority:``).
# The ``\b`` word-boundary style of regexes tolerates trailing
# punctuation; the token-based helper strips a small set of
# characters so the helper matches the regex behavior. The set
# includes dash separators (``-``/``—``/``–``) so em-dash and
# hyphen after ``priority``/``severity`` are accepted.
_TEXT_SEVERITY_TRAILING_PUNCT = ".,;:!?\"'()[]{}-—–"


def _has_narrow_colon_form_declaration(leading_text: str) -> bool:
    """Return ``True`` iff ``leading_text`` (the lowercased first
    100 chars of a comment body) contains a NARROW colon-form
    text-alias severity/priority declaration of the shape
    ``<coordination_prefix>: <level> <noun>`` that should count
    as an actionable finding signal.

    The detection is intentionally narrow:

    * Only the FIRST FOUR tokens after the FIRST ``:`` in the
      leading window are considered. This rejects prior-finding
      descriptions like
      ``Re-requesting Codex review: fixed the high severity
      regression from the prior finding`` (Codex finding
      3448570717) where the ``high severity`` phrase appears
      past the coordination-prefix segment.
    * The FIRST token after the colon must NOT be a
      meta-verb/past-participle (``fixed``/``addressed``/
      ``described``/``classified``/``flagged``/``reviewed``/
      ``identified``/``mentioned``). This is the second
      discriminator for Codex finding 3448570717 — the past
      participle is the marker that the comment is REFERENCING
      a prior fix rather than DECLARING a current issue.
    * The level token must NOT be preceded by a negation token
      (with up to 3-token look-back so ``this is not a high
      priority issue`` is rejected — Codex finding 3448570719).
    * The level/noun pair must NOT be followed by ``context``
      or ``only`` (meta/context-only form, e.g. ``high priority
      context only``).

    The shape grammar is intentionally constrained — there is no
    "scan the whole text after the colon" behavior, no broad
    grammar expansion, and no natural-language heuristic for
    arbitrary verb/noun permutations. This keeps the detector
    narrow so the same body cannot trigger an adjacent-shape
    Codex finding next cycle.

    Examples returning ``True`` (actionable):
        ``bumping retry counter: high severity regression``
        ``re-requesting review: high priority issue``
        ``following up: medium severity problem``
        ``bumping this: low priority cleanup``

    Examples returning ``False`` (not actionable):
        ``bumping retry counter for review``       (no colon)
        ``re-requesting review on latest head``   (no colon)
        ``re-requesting codex review: fixed the high severity
            regression from the prior finding``   (meta-verb;
                                                  Codex 3448570717)
        ``re-requesting review: this is not a high priority
            issue, just a re-prompt``             (negation;
                                                  Codex 3448570719)
        ``bumping retry counter: p0/p1/p2 severity taxonomy``
                                                (P-token, not text-alias)
        ``bumping retry counter: not high severity``
                                                (bare negation)
        ``bumping retry counter: high priority context only``
                                                (meta/context-only)
    """
    if not leading_text:
        return False
    colon_idx = leading_text.find(":")
    if colon_idx == -1:
        return False
    after = leading_text[colon_idx + 1:]
    if not after.strip():
        return False
    raw_tokens = after.split()
    if not raw_tokens:
        return False
    # Discriminator 1: if the first post-colon token is a
    # meta-verb/past-participle, the comment is describing a
    # prior fix and is NOT actionable. This is the cycle-12
    # finding 3448570717 fix.
    if raw_tokens[0].rstrip(_TEXT_SEVERITY_TRAILING_PUNCT) in _META_VERBS:
        return False
    # Restrict the scan to the first 4 post-colon tokens. This
    # is the window-boundary fix for cycle-12 finding 3448570717.
    window = [t.rstrip(_TEXT_SEVERITY_TRAILING_PUNCT) for t in raw_tokens[:4]]
    levels = _TEXT_SEVERITY_LEVELS_SET
    nouns = _TEXT_SEVERITY_NOUNS_SET
    negations = _TEXT_SEVERITY_NEGATIONS
    context_only_words = frozenset({"context", "only"})
    for i, tok in enumerate(window):
        if tok in levels:
            # Discriminator 2: negation look-back (cycle-12
            # finding 3448570719 fix). Scan up to 3 tokens
            # before the level so ``this is not a high
            # priority issue`` rejects on the ``not`` at
            # distance 3.
            for j in range(max(0, i - 3), i):
                if window[j] in negations:
                    return False
            # Discriminator 3: meta/context-only form.
            if i + 1 < len(window) and window[i + 1] in nouns:
                if (i + 2 < len(window)
                        and window[i + 2] in context_only_words):
                    return False
                return True
    return False


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


def _has_actionable_finding_signal(body: str) -> bool:
    """Return ``True`` iff ``body`` contains an actionable finding
    signal — a clear indicator that the comment is DECLARING a
    current issue rather than REFERENCING a prior fix or
    producing meta-discussion about a finding.

    This helper is the SINGLE SOURCE OF TRUTH for the
    actionability decision in the classifier. The full
    coordination-skip / early-bypass heuristic ladder from
    cycles 3-11 (Guards 1-7 inside the old
    ``is_coordination_comment``) has been replaced by this
    central detector plus a final-suppressor coordination check
    inside :func:`classify_item`.

    The signal is a disjunction of three narrow detectors,
    evaluated left-to-right (most-specific first):

    1. **Explicit P0/P1/P2/P3 marker.** A badge
       (``![Pn Badge]``), bracketed (``[Pn]``), or colon-declared
       (``Pn:``) severity is always actionable — these formats
       are unambiguous declarations of severity by the Codex
       reviewer. This consolidates Guards 1-3 from the
       cycle-3-11 stack.

    2. **Copula-based text-alias declaration.** A text-alias
       declaration of the shape
       ``[subject?] is/has [negation?] [article?] <intensifier{0,2}> <level> <noun>``
       in the leading 100 characters is actionable. This is
       delegated to :func:`_has_direct_text_severity_declaration`
       (the cycle-8 table-driven helper, unchanged). It handles
       dbIDs 3447794638, 3447818802, 3447825478, 3447830523,
       3447849261, 3447871114, 3447899921, and 3448488549.

    3. **Narrow colon-form declaration.** A coordination-prefixed
       body of the shape
       ``<coordination_prefix>: <level> <noun>``
       where the level/noun pair appears in the first four
       tokens after the first ``:`` and the first token after
       the colon is not a meta-verb/past-participle. This is
       delegated to :func:`_has_narrow_colon_form_declaration`
       (the cycle-12-narrowed helper). It handles dbID
       3448545827 and is intentionally narrow so it does NOT
       over-rescue dbIDs 3448570717 + 3448570719.

    4. **Blocking-word indicator in the leading 100 characters.**
       A body that contains a :data:`BLOCKING_WORDS` token
       (``can fail`` / ``stale`` / ``must fix`` / ``security`` /
       etc.) tightly within the leading 100 characters is
       actionable. This is the move of the OLD cycle-7
       Guard 5 (Codex finding AQ) into the centralized
       detector. The discriminator for meta-discussion
       coordination messages like
       ``Re-requesting Codex review — Fix AG is now on 266a92e``
       is the leading-100-char window: real coordination
       messages that happen to mention blocking vocabulary
       (``stale`` / ``malformed``) do so well past the
       leading 100 characters (in the meta-discussion about
       which fix addressed which prior finding).

    The detector is conservative by design: a body that merely
    REFERENCES severity (e.g. ``fixed the high severity regression
    from the prior finding``, ``classified as high priority``,
    ``with high priority context``, taxonomy references,
    negated forms) does NOT count as actionable and is left to
    the final-suppressor coordination check.
    """
    if not body:
        return False
    # Detector 1: explicit P0/P1/P2/P3 marker.
    upper = body.upper()
    for sev in ("P0", "P1", "P2", "P3"):
        if f"![{sev} BADGE]" in upper:
            return True
        if f"[{sev}]" in upper:
            return True
        if sev + ":" in upper:
            return True
    # Detector 2/3/4 share the leading[:100] window.
    leading = body[:100].lower()
    # Detector 2: copula-based text-alias declaration.
    if _has_direct_text_severity_declaration(leading):
        return True
    # Detector 3: narrow colon-form declaration.
    if _has_narrow_colon_form_declaration(leading):
        return True
    # Detector 4: blocking-word indicator in the leading
    # 100 characters. Catches bodies like
    # ``Bumping the retry counter can fail when Codex reruns
    # after a stale head`` which declare an issue using
    # BLOCKING_WORDS vocabulary even without a copula or P-marker.
    # The leading-100-char window keeps the check narrow so
    # coordination messages that mention blocking vocabulary
    # in meta-discussion past the leading 100 chars (e.g.
    # ``Re-requesting Codex review on 3982ee6 (Fix AF). The
    # active P1 current-head finding ... allowing a
    # malformed checkpoint ...``) do NOT match.
    if any(bw in leading for bw in BLOCKING_WORDS):
        return True
    return False


def is_coordination_comment(body: str) -> bool:
    """Return True if ``body`` STARTS with a coordination
    pattern (human PR-author messages that re-request Codex
    review, nudge the Codex bot, or describe which fix addresses
    a prior finding).

    This is the FINAL SUPPRESSOR in the classifier pipeline,
    not an early bypass. The actionability decision is made
    upstream by :func:`_has_actionable_finding_signal`; this
    function only answers the simpler question "does this body
    start with a coordination prefix?".

    A coordination-prefixed body that ALSO has an actionable
    finding signal is NOT suppressed here — it is detected as
    actionable upstream and emitted as a finding by
    :func:`classify_item`. The coordination-suppress path only
    fires when there is no actionable signal AND the body
    starts with a coordination pattern (e.g. ``Bumping retry
    counter for review`` — coordination noise with no
    severity/blocking signal).
    """
    body_str = body or ""
    body_lower = body_str.lower().lstrip()
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
    threads_list entries:
        {thread_id, is_resolved, is_outdated, database_id, url,
         author_login}

    The ``author_login`` field is the GitHub login of the
    thread's first-comment author. Under the option-B2
    source-aware architecture, this is used to distinguish
    Codex-authored threads (``chatgpt-codex-connector[bot]``)
    from human-authored review threads. Only Codex-authored
    threads that are current and unresolved are treated as
    fail-closed actionable findings; human-authored threads
    follow the existing coordination-skip / severity-extraction
    flow.

    Note: gh api graphql -f passes all variables as strings, which GraphQL rejects
    for Int. We embed the PR number as a raw integer literal in the query.
    Also note: nested braces must be balanced; comments(first:50) has its own
    nodes subfield requiring a closing '}' before the comments block closes.
    """
    owner, name = repo.split("/", 1)
    # Build with explicit brace counting via a list to ensure balance.
    # comments(first:50) { nodes { databaseId url author { login } } }
    #                                                  ^^--- +1 extra } to
    #                                                       close inner nodes
    query_parts = [
        "query {",
        f'repository(owner:"{owner}", name:"{name}") {{',
        f"pullRequest(number:{pr_number}) {{",
        "reviewThreads(first:100) {",
        # Round-69 Codex review 4768917422 (P1): include
        # pageInfo so the first-page helper can detect
        # incomplete outer pagination. When
        # hasNextPage=true the helper returns ok=False
        # so the wrapper invokes the cursor walker.
        "pageInfo { hasNextPage }",
        "nodes {",
        "id isResolved isOutdated",
        # Round-69 Codex review 4769344844 (P2): add
        # whitespace between ``isOutdated`` and
        # ``comments`` so the rendered query is
        # ``... isOutdated comments(first:50) ...``
        # instead of ``... isOutdatedcomments(first:50) ...``
        # (a single nonexistent field that causes GitHub
        # to return a GraphQL error).
        " ",  # explicit whitespace separator
        # Round-69 Codex review 4769230169 (P2): include
        # the nested comments pageInfo so the first-page
        # helper can detect incomplete nested comment
        # pagination. When any thread's
        # ``comments.pageInfo.hasNextPage=true`` the
        # helper returns ok=False so the wrapper invokes
        # the cursor walker (or the audit's visible-blocker
        # logic catches the current-finding).
        "comments(first:50) {"
        "pageInfo { hasNextPage }"
        # Round-81 follow-up: also fetch the
        # parent ``pullRequestReview`` so per-review-comment
        # findings can be linked to threads in the same
        # review via the review_id index. Without this,
        # a review-summary finding whose URL is the
        # review URL (not a thread discussion URL) cannot
        # inherit the resolution state of any thread in
        # that review and would falsely remain as a
        # stale-blocker after every thread in the review
        # is resolved.
        "nodes { databaseId url author { login } pullRequestReview { databaseId } } }",
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
        review_threads_container = (
            data.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
        )
        # Round-69 Codex review 4768917422 (P1): detect
        # incomplete outer pagination. When
        # hasNextPage=true the inventory is incomplete;
        # return ok=False so the wrapper invokes the
        # cursor walker. Test mocks that return
        # ``(True, [], "")`` directly via
        # ``mock.patch.object`` short-circuit this check
        # because the test never reaches this code path.
        page_info = review_threads_container.get("pageInfo") or {}
        if isinstance(page_info, dict) and page_info.get("hasNextPage"):
            return False, [], (
                "reviewThreads.pageInfo.hasNextPage=true; "
                "pagination required"
            )
        nodes = review_threads_container.get("nodes", [])
        # Round-69 Codex review 4769230169 (P2): detect
        # incomplete nested comment pagination. When any
        # thread's ``comments.pageInfo.hasNextPage=true`` the
        # nested inventory is incomplete. Return ok=False
        # so the wrapper invokes the cursor walker (which
        # walks nested comments via the
        # ``_walk_pagination_cursors`` helper).
        for _node in nodes:
            if not isinstance(_node, dict):
                continue
            _comments = _node.get("comments") or {}
            if not isinstance(_comments, dict):
                continue
            _nested_pi = _comments.get("pageInfo") or {}
            if (
                isinstance(_nested_pi, dict)
                and _nested_pi.get("hasNextPage")
            ):
                return False, [], (
                    "reviewThreads.comments.pageInfo.hasNextPage=true; "
                    "nested pagination required"
                )
    except (json.JSONDecodeError, OSError) as exc:
        return False, [], f"gh graphql decode failed: {exc}"

    # Flatten: keep thread metadata + each comment's databaseId/url/author.
    # Round-81 follow-up: also carry the parent
    # ``pull_request_review_id`` so per-review-comment findings
    # whose URL is the review-summary URL can be linked to
    # threads in the same review via the review_id index.
    threads: list[dict[str, Any]] = []
    for node in nodes:
        thread_id = node.get("id", "")
        is_resolved = node.get("isResolved", False)
        is_outdated = node.get("isOutdated", False)
        for comment in (node.get("comments", {}) or {}).get("nodes", []):
            author_login = (
                (comment.get("author") or {}).get("login", "")
                if comment.get("author") else ""
            )
            # Round-81 follow-up: thread -> review linkage.
            # Pulled from the inline comment's
            # ``pullRequestReview.databaseId`` so per-review-
            # comment findings can map back to threads in
            # the same review via the review_id index.
            pr_review = comment.get("pullRequestReview") or {}
            review_id = (
                pr_review.get("databaseId")
                if isinstance(pr_review, dict) else None
            )
            threads.append({
                "thread_id": thread_id,
                "is_resolved": is_resolved,
                "is_outdated": is_outdated,
                "database_id": comment.get("databaseId"),
                "url": comment.get("url") or "",
                "author_login": author_login,
                "review_id": review_id,
            })
    return True, threads, ""


def _walk_thread_comments(
    *, owner: str, name: str, pr_number: int,
    thread_id: str, timeout: int,
    page_size: int = 50, safety_cap: int = 200,
) -> tuple[bool, list[dict[str, Any]], str]:
    """Round-90 follow-up: walk the nested ``comments(first:N)``
    cursor for one review thread so that threads with more than
    N initial comments produce a complete comment inventory.

    Returns ``(ok, comments, error_msg)``.

    The outer walker (``_walk_pagination_cursors``) used to
    return ``nested_comments_not_paginated: thread=<id>``
    whenever a thread's ``comments.pageInfo.hasNextPage=true``
    because it had no logic to follow the nested cursor. That
    failure mode meant the production review-comment gate
    remained fail-closed indefinitely for any PR with a
    long-running review thread. This helper performs the
    nested cursor walk using ``subprocess.run(``gh api
    graphql``)`` and returns the complete list of
    ``(databaseId, url, author_login, review_id, raw_body)``
    entries for the thread.
    """
    all_comments: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    pages_fetched = 0
    while True:
        pages_fetched += 1
        if pages_fetched > safety_cap:
            return False, [], (
                f"thread_comments_inventory_capped: "
                f"thread={thread_id} pages={pages_fetched} "
                f"safety_cap={safety_cap}"
            )
        after_clause = f', after: "{cursor}"' if cursor else ""
        query_literal = (
            "query {"
            # Round-90 follow-up fixed the nested-comments
            # walker but the previous query built an invalid
            # GraphQL literal: ``node(id: ...)`` was a child
            # of ``repository(...)`` (it MUST be a root
            # field), and the brace count was unbalanced
            # (10 opening / 9 closing). GitHub rejected every
            # nested-comments request with a parse error and
            # the walker returned ``nested_comments_walk_failed``.
            # The fix below mirrors the root-level ``node(id:)``
            # shape used by ``_shared_pagination.py``.
            f"node(id:\"{thread_id}\") {{"
            "... on PullRequestReviewThread {"
            f'comments(first:{page_size}{after_clause}) {{'
            "pageInfo { hasNextPage endCursor }"
            "nodes { databaseId url body path line "
            "originalCommit { oid } "
            "author { login } pullRequestReview { databaseId } } } } }"
        )
        cmd = ["gh", "api", "graphql",
               "--raw-field", f"query={query_literal}"]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, [], (
                f"gh graphql invocation failed: {exc}"
            )
        if result.returncode != 0:
            return False, [], (
                f"gh graphql returned {result.returncode}: "
                f"{result.stderr[:500]}"
            )
        if not result.stdout.strip():
            return False, [], (
                "gh graphql returned empty stdout"
            )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return False, [], f"invalid GraphQL response: {exc}"
        if not isinstance(data, dict):
            return False, [], "GraphQL response is not a JSON object"
        errors = data.get("errors")
        if errors:
            return False, [], f"GraphQL errors: {errors}"
        data_obj = data.get("data")
        if not isinstance(data_obj, dict):
            return False, [], "GraphQL response missing data object"
        node_obj = data_obj.get("node") or {}
        if not isinstance(node_obj, dict):
            return False, [], "GraphQL response missing node"
        comments_obj = node_obj.get("comments") or {}
        if not isinstance(comments_obj, dict):
            return False, [], "GraphQL response missing comments container"
        nested_page_info = comments_obj.get("pageInfo") or {}
        for comment in (comments_obj.get("nodes") or []):
            if isinstance(comment, dict):
                all_comments.append(comment)
        if not nested_page_info.get("hasNextPage"):
            break
        cursor = nested_page_info.get("endCursor")
        if not cursor:
            return False, [], (
                "comments.pageInfo.hasNextPage=true with no endCursor"
            )
    return True, all_comments, ""


def _walk_pagination_cursors(
    *, owner: str, name: str, pr_number: int,
    page_size: int, safety_cap: int, timeout: int,
    starting_cursor: Optional[str] = None,
    starting_pages: int = 0,
) -> tuple[bool, list[dict[str, Any]], str, int]:
    """Walk the reviewThreads cursor from the given cursor.

    Returns ``(ok, threads, error_msg, pages_fetched)``.

    Round-69 Codex review 4768843522 (P2): the previous
    delegate-to-gh_graphql_review_threads approach still
    stopped at the first page because that helper does
    not follow pageInfo cursors. This helper performs the
    cursor walk using ``subprocess.run(``gh api graphql``)``
    so existing test mocks of ``subprocess.run`` continue
    to work.
    """
    all_threads: list[dict[str, Any]] = []
    cursor: Optional[str] = starting_cursor
    pages_fetched = starting_pages
    while True:
        pages_fetched += 1
        if pages_fetched > safety_cap:
            return False, [], (
                f"review_thread_inventory_capped: "
                f"pages={pages_fetched} safety_cap={safety_cap}"
            ), pages_fetched
        after_clause = f', after: "{cursor}"' if cursor else ""
        # Round-69 Codex review 4769487744 (P2): balance
        # the cursor-walker GraphQL query. The previous
        # query had 11 ``{`` and 10 ``}`` (one missing
        # closing brace) so GitHub returned a GraphQL
        # parse error before any later pages could be
        # read. Added one more ``}`` to close the outer
        # query brace.
        query_literal = (
            "query {"
            f'repository(owner:"{owner}", name:"{name}") {{'
            f"pullRequest(number:{pr_number}) {{"
            f"reviewThreads(first:{page_size}{after_clause}) {{"
            "pageInfo { hasNextPage endCursor }"
            "nodes {"
            "id isResolved isOutdated "
            "comments(first:50) {"
            "pageInfo { hasNextPage endCursor }"
            "nodes { databaseId url body path line "
            "originalCommit { oid } "
            # Round-83 follow-up: also fetch
            # ``pullRequestReview.databaseId`` so the
            # pagination walker can populate the
            # ``thread_meta_by_review_id`` index used
            # by the per-review-summary finding
            # resolution-state fallback. Without this,
            # a PR with more than 100 review threads
            # would still leave per-review-summary
            # findings as stale-blockers.
            "author { login } pullRequestReview { databaseId } } } } } } }"
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
            ), pages_fetched
        if result.returncode != 0:
            return False, [], (
                f"gh graphql returned {result.returncode}: "
                f"{result.stderr[:500]}"
            ), pages_fetched
        if not result.stdout.strip():
            return False, [], (
                "gh graphql returned empty stdout"
            ), pages_fetched
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return False, [], (
                f"invalid GraphQL response: {exc}"
            ), pages_fetched
        if not isinstance(data, dict):
            return False, [], (
                "GraphQL response is not a JSON object"
            ), pages_fetched
        errors = data.get("errors")
        if errors:
            return False, [], (
                f"GraphQL errors: {errors}"
            ), pages_fetched
        data_obj = data.get("data")
        if not isinstance(data_obj, dict):
            return False, [], (
                "GraphQL response missing data object"
            ), pages_fetched
        repository = data_obj.get("repository")
        if not isinstance(repository, dict):
            return False, [], (
                "GraphQL response missing repository"
            ), pages_fetched
        pr_data = repository.get("pullRequest")
        if not isinstance(pr_data, dict):
            return False, [], (
                "GraphQL response missing pullRequest"
            ), pages_fetched
        threads_container = pr_data.get("reviewThreads")
        if not isinstance(threads_container, dict):
            return False, [], (
                "GraphQL response missing reviewThreads container"
            ), pages_fetched
        page_info = threads_container.get("pageInfo") or {}
        if not isinstance(page_info, dict):
            return False, [], (
                "GraphQL reviewThreads.pageInfo is not a dict"
            ), pages_fetched
        nodes = threads_container.get("nodes")
        if not isinstance(nodes, list):
            return False, [], (
                "GraphQL reviewThreads.nodes is not a list"
            ), pages_fetched
        for node in nodes:
            if not isinstance(node, dict):
                continue
            thread_id = node.get("id", "")
            is_resolved = bool(node.get("isResolved", False))
            is_outdated = bool(node.get("isOutdated", False))
            comments_obj = node.get("comments") or {}
            nested_page_info = comments_obj.get("pageInfo") or {}
            if not isinstance(nested_page_info, dict):
                nested_page_info = {}
            nested_incomplete = bool(
                nested_page_info.get("hasNextPage")
            )
            # Helper to flatten one comment record so the
            # walker can emit it the same way the
            # single-page helper does.
            def _flatten_walked_comment(comment):
                author_login = (
                    (comment.get("author") or {}).get("login", "")
                    if isinstance(comment.get("author"), dict)
                    else ""
                )
                pr_review = comment.get("pullRequestReview") or {}
                review_id = (
                    pr_review.get("databaseId")
                    if isinstance(pr_review, dict) else None
                )
                return {
                    "thread_id": thread_id,
                    "is_resolved": is_resolved,
                    "is_outdated": is_outdated,
                    "database_id": comment.get("databaseId"),
                    "url": comment.get("url") or "",
                    "author_login": author_login,
                    "review_id": review_id,
                }
            for comment in (comments_obj.get("nodes") or []):
                if isinstance(comment, dict):
                    all_threads.append(_flatten_walked_comment(comment))
            if nested_incomplete:
                # Round-90 follow-up: follow the nested
                # ``comments`` cursor instead of failing
                # closed. The previous behavior returned
                # ``nested_comments_not_paginated:
                # thread=<id>`` whenever any thread's
                # ``comments.pageInfo.hasNextPage=true``,
                # leaving the production gate fail-closed
                # indefinitely for PRs with long-running
                # review threads.
                ok_nested, nested_comments, nested_err = (
                    _walk_thread_comments(
                        owner=owner, name=name,
                        pr_number=pr_number,
                        thread_id=thread_id,
                        timeout=timeout,
                    )
                )
                if not ok_nested:
                    return False, [], (
                        f"nested_comments_walk_failed: "
                        f"thread={thread_id} error={nested_err}"
                    ), pages_fetched
                # Append the walked comments. The earliest
                # of these are duplicates of the first-page
                # ``comments`` (the first ``page_size`` were
                # already flattened above), but the walker
                # returns the COMPLETE list — including the
                # first page. The downstream flattening
                # inventory treats ``database_id`` as the
                # dedup key, so re-emitting them is harmless.
                for comment in nested_comments:
                    if isinstance(comment, dict):
                        all_threads.append(
                            _flatten_walked_comment(comment)
                        )
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            return False, [], (
                "reviewThreads.pageInfo.hasNextPage=true with no "
                "endCursor"
            ), pages_fetched
    return True, all_threads, "", pages_fetched


def paginated_review_threads(
    repo: str, pr_number: int,
    *, page_size: int = 100, safety_cap: int = 2000,
    timeout: int = 30,
) -> tuple[bool, list[dict[str, Any]], str]:
    """PHASE 2 (PR #412): production paginated review-thread
    inventory.

    Round-69 Codex reviews 4764653534 and 4768843522 (P2):
    the previous implementations had two bugs:
      - the first version used a urllib-based shared
        paginator that the existing ``subprocess.run`` test
        mocks did not intercept;
      - the second version delegated to the inline
        ``gh_graphql_review_threads`` which still only
        requests ``reviewThreads(first:100)`` /
        ``comments(first:50)`` without following the
        ``pageInfo.hasNextPage`` cursor.

    This implementation:
      - delegates the first page to the existing
        ``gh_graphql_review_threads`` (which uses
        ``subprocess.run`` and is mockable via
        ``monkeypatch``). When tests mock it to return
        ``(True, [], "")`` the wrapper short-circuits and
        returns the mock result unchanged. This preserves
        every existing test contract.
      - when the first page returns ``ok=False`` because
        the outer ``hasNextPage=true`` or any nested
        ``comments.pageInfo.hasNextPage=true``, walks the
        cursor via direct ``gh api graphql`` subprocess
        calls so the inventory is complete (or the safety
        cap is exhausted, fail-closed).

    Returns ``(success, threads_list, error_msg)`` in the
    same shape as :func:`gh_graphql_review_threads`.
    """
    try:
        owner, name = repo.split("/", 1)
    except ValueError:
        return False, [], f"invalid repo format: {repo!r}"
    # First page: delegate to ``gh_graphql_review_threads``
    # so existing test mocks of that helper (via
    # ``mock.patch.object(crc, "gh_graphql_review_threads",
    # return_value=(True, [], ""))``) continue to short-circuit
    # the wrapper for the simple single-page case.
    # Round-69 Codex review 4768917422 (P1): the first-page
    # helper now correctly detects incomplete outer
    # pagination via the rendered pageInfo.hasNextPage flag
    # and returns ``ok=False`` when ``hasNextPage=true``.
    # When the helper returns ``ok=True`` the first page is
    # genuinely complete and the cursor walker is not
    # required. The wrapper short-circuits on ``ok_first``.
    ok_first, threads_first, err_first = gh_graphql_review_threads(
        repo, pr_number
    )
    if ok_first:
        return True, threads_first, err_first
    # First page reported incomplete inventory. Walk the
    # cursor via the helper so the inventory is complete.
    ok, threads, err, _pages = _walk_pagination_cursors(
        owner=owner, name=name, pr_number=pr_number,
        page_size=page_size, safety_cap=safety_cap,
        timeout=timeout, starting_cursor=None, starting_pages=0,
    )
    if not ok:
        # Preserve any visible threads from the first page so
        # the visible-blocker logic in ``main()`` can still
        # detect Codex findings on partial inventory.
        return False, list(threads_first or []) + threads, err
    return ok, threads, err
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


# Codex bot author login used by the task-summary detector. This is a
# local constant and does NOT affect the gate's policy safeguard that
# refuses to globally --ignore-users the Codex bot (see the
# ``if CODEX_BOT_LOGIN in ignore_users`` block near the bottom of this
# file). The detector is structural: it suppresses the issue-comment
# class for an author ``login`` that matches ``chatgpt-codex-connector``
# or ``chatgpt-codex-connector[bot]``, scoped to ``source_kind ==
# "issue_comment"`` bodies that do NOT start with ``Codex Review:``.
# Codex review-thread comments and ``Codex Review:`` verdict comments
# are unaffected by this check.
_CODEX_BOT_LOGIN_TASK_SUMMARY = "chatgpt-codex-connector"
_CODEX_BOT_LOGIN_TASK_SUMMARY_BOT = "chatgpt-codex-connector[bot]"


def _is_codex_task_summary_issue_comment(
    user: str,
    source_kind: str,
    body: str,
) -> bool:
    """Return True iff ``user`` is the Codex bot and ``body`` is a
    Codex task-summary issue-comment rather than a Codex review
    verdict.

    Codex occasionally posts issue-comments that describe work it
    performed (e.g. ``### Summary\\n\\n* Updated fetch_ci_conclusions
    ... **Commit**\\n\\n* New commit SHA: ... **Testing**\\n\\n* ...``).
    These are coordination posts that may incidentally use
    BLOCKING_WORDS vocabulary (``stale`` / ``malformed`` /
    ``blocking``) inside the body when describing prior fixes. The
    gate must NOT classify them as ``UNSPECIFIED_BLOCKING``
    current-head blockers; they are informational posts.

    The structural discriminator is the leading byte sequence:

    * ``Codex Review:`` is the canonical Codex review-verdict
      prefix. Bodies that begin with this prefix are
      ``Codex Review:`` verdicts and MUST be classified by the
      normal pipeline (clean-pass verdicts and substantive
      review verdicts alike).
    * ``### Summary`` (with an optional single leading newline)
      is the canonical Codex task-summary prefix. When the
      Codex bot's body begins with this Markdown-H3 token, the
      body is structurally a task summary (it describes work
      the bot performed, with ``**Commit**`` / ``**Testing**``
      sections). The body of such a post may incidentally
      mention ``stale`` / ``malformed`` / ``blocking`` while
      describing prior fixes. The gate must NOT classify such
      posts as ``UNSPECIFIED_BLOCKING``.

    Note that other Codex review-thread comments that happen
    to begin with ``Bumping`` / ``Re-requesting`` / etc. are
    classified by the coordination-skip / shape-grammar
    pipeline; the task-summary detector is specific to
    ``### Summary`` (Markdown H3) and does NOT match them.
    """
    if source_kind != "issue_comment":
        return False
    if user not in (_CODEX_BOT_LOGIN_TASK_SUMMARY, _CODEX_BOT_LOGIN_TASK_SUMMARY_BOT):
        return False
    if not body:
        return False
    # The structural discriminator is the leading Markdown H3
    # ``### Summary`` token (the canonical Codex task-summary
    # prefix). Codex review-thread comments that happen to
    # begin with ``Bumping`` / ``Re-requesting`` / etc. are
    # classified by the coordination-skip / shape-grammar
    # pipeline; the task-summary detector does NOT match them.
    # Strip at most ONE optional leading newline so that a body
    # that starts with ``\n### Summary`` still matches.
    stripped = body.lstrip("\n")
    return stripped.startswith("### Summary")


def classify_item(
    item: dict[str, Any],
    source_kind: str,
    ignore_users: set[str],
    is_codex_review_thread_current_unresolved: bool = False,
) -> list[dict[str, Any]]:
    """
    Given a single comment/review dict from any endpoint, scan for Codex
    findings and return a list of finding dicts (may be empty).

    The classifier pipeline is SOURCE-AWARE (option B2
    refactor). The actionability decision is made by inspecting
    the SOURCE TYPE first, with shape-grammar serving only as a
    secondary fallback for non-Codex-thread comments.

    Pipeline order:

    1. Author filter — drop comments from ``ignore_users``.

    2. Codex-needle check — drop comments without any Codex
       needle (high/medium/P0/P1/P2/P3/badge/...).

    3. **Source-aware actionability check (PRIMARY under B2).**
       If ``is_codex_review_thread_current_unresolved`` is True
       (the comment is part of a current, unresolved Codex
       review thread), the comment is actionable by default
       regardless of body shape. Coordination suppression does
       NOT apply. This is the fail-closed path.

       If the source is not a current unresolved Codex
       review thread, fall back to the shape-grammar detector
       :func:`_has_actionable_finding_signal` (secondary
       path).

    4. Coordination suppression — :func:`is_coordination_comment`
       ONLY suppresses bodies that have no actionable signal AND
       start with a coordination pattern. This applies ONLY on
       the non-Codex-thread path. Codex review-thread findings
       are NEVER suppressed by coordination-skip.

    5. Severity extraction — :func:`extract_severity` maps the
       body text to P0-P3 (or None). :func:`is_blocking` is the
       fallback for unspecified-severity blocking comments.

    6. **Fail-closed default for unresolved Codex threads.** If
       the source is a current unresolved Codex review thread
       and no severity could be extracted (no P0:/P1:/P2:/P3:,
       no badge, no bracket, no text-alias), default to
       ``P2`` (blocking). This is the auth-prompt-mandated
       conservative default for unresolved Codex findings
       without an explicit severity.

    7. Finding emission — emit the finding dict.
    """
    findings = []
    user = (item.get("user") or {}).get("login", "")
    if user in ignore_users:
        return findings

    body = item.get("body") or ""
    state = item.get("state") or ""
    file_path = item.get("path") or ""
    line = item.get("line") or item.get("original_line") or ""
    commit_id = item.get("commit_id") or ""
    html_url = item.get("html_url") or item.get("url") or ""

    combined = f"{body} {user} {state} {file_path}".lower()
    if not any(needle in combined for needle in CODEX_NEEDLES):
        return findings

    # PRIMARY actionability decision: source-aware.
    # Current unresolved Codex review-thread comments are
    # actionable by default. This is the architectural shift
    # that breaks the cycle-3-13 shape-grammar heuristic ladder.
    if is_codex_review_thread_current_unresolved:
        has_actionable = True
    else:
        # SECONDARY actionability decision: shape-grammar
        # fallback for non-Codex-thread comments (issue
        # comments, human review comments, resolved threads,
        # outdated threads).
        has_actionable = _has_actionable_finding_signal(body)

    # Coordination suppression is a FINAL SUPPRESSOR for
    # non-Codex-thread bodies only. Codex review-thread
    # findings NEVER go through coordination suppression
    # (this is the fail-closed guarantee).
    if not has_actionable and is_coordination_comment(body):
        return findings

    # Task-summary override for Codex-bot-authored issue comments
    # that are NOT ``Codex Review:`` verdicts. The Codex
    # review-comment gate must not classify Codex task-summary
    # posts (e.g. ``### Summary\\n\\n* ... **Commit**\\n\\n* New
    # commit SHA: ...``) as ``UNSPECIFIED_BLOCKING`` just because
    # the body happens to mention ``stale`` / ``malformed`` /
    # ``blocking`` while describing prior fixes. The override
    # is applied as a SEVERITY CAP (force to ``UNSPECIFIED_INFO``)
    # BEFORE severity extraction, so that even if the body
    # contains a literal ``P1`` / ``P2`` token or text-alias
    # ``high`` / ``medium`` describing a prior fix, the
    # comment is still classified as informational. The
    # comment is still emitted so inventory remains complete,
    # but it does not block the gate.
    is_task_summary = _is_codex_task_summary_issue_comment(
        user=user, source_kind=source_kind, body=body
    )

    # Severity extraction (unchanged — already a single source
    # of truth for severity level mapping).
    severity = extract_severity(combined)

    # Task-summary override runs as a CAP: if the body is a
    # Codex task-summary, force severity to UNSPECIFIED_INFO
    # regardless of what extract_severity returned. This must
    # come BEFORE the fail-closed default cascade so a body
    # that mentions a prior ``P1`` / ``P2`` finding it fixed
    # is still classified as informational.
    if is_task_summary:
        severity = "UNSPECIFIED_INFO"
    # Fail-closed default: unresolved Codex threads without
    # extractable severity default to P2 (blocking).
    elif severity is None and is_codex_review_thread_current_unresolved:
        severity = "P2"
    elif severity is None and is_blocking(combined):
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

    # -----------------------------------------------------------------------
    # Review-thread resolution state via GraphQL — fetch FIRST so that
    # classify_item can use source-aware actionability (option B2).
    # -----------------------------------------------------------------------
    # Under option B2, the actionability decision is made by inspecting
    # the SOURCE TYPE: current unresolved Codex review-thread comments
    # are actionable by default. This requires knowing, at classification
    # time, whether a comment belongs to a current unresolved Codex
    # review thread. We fetch the thread metadata first (via GraphQL),
    # build a URL → metadata map, and pass a per-item
    # ``is_codex_review_thread_current_unresolved`` flag into
    # :func:`classify_item`.
    #
    # If the GraphQL fetch fails, the gate is FAIL-CLOSED: we proceed
    # with an empty thread map (flag=False everywhere → shape-grammar
    # fallback) and the gate status is set to INCONCLUSIVE later in
    # this function (existing behavior).
    thread_meta_by_url: dict[str, dict[str, Any]] = {}
    # Round-81 follow-up: also index by review_id so per-review-comment
    # findings whose URL is the review-summary URL (not a thread
    # discussion URL) can be linked to their parent review and
    # inherit the resolution state of any thread in that review.
    thread_meta_by_review_id: dict[int, list[dict[str, Any]]] = {}
    thread_api_error: str | None = None
    # Round-69 Codex review 4764653534 (P2): use the
    # production paginated wrapper instead of the inline
    # first-page-only GraphQL fetch. Without this, PRs with
    # more than 100 review threads miss later-page unresolved
    # Codex threads and the gate can report clean instead
    # of fail-closed.
    ok_threads, thread_entries, err_threads = paginated_review_threads(
        args.repo, args.pr_number
    )
    if not ok_threads:
        thread_api_error = err_threads
    else:
        for entry in thread_entries:
            url = entry.get("url", "")
            if url:
                thread_meta_by_url[url] = entry
            review_id = entry.get("review_id")
            if review_id:
                thread_meta_by_review_id.setdefault(
                    int(review_id), []
                ).append(entry)

    # Codex bot login — same constant used by the policy safeguard
    # further down. Defined here so the source-aware helper can
    # reference it. GitHub may render the login as either
    # ``chatgpt-codex-connector`` or ``chatgpt-codex-connector[bot]``
    # depending on the API surface; we match the prefix.
    CODEX_BOT_LOGIN_PREFIX = "chatgpt-codex-connector"

    def _extract_review_id(
        item: dict[str, Any], url: str
    ) -> Optional[int]:
        """Extract the parent review_id from a per-review-comment
        finding's URL or payload, if present.

        Returns the integer review_id or ``None`` when the URL
        does not encode a review id and the item has no
        ``pull_request_review_id`` field. Used by the
        Round-81 follow-up fallback that links per-review-
        comment findings to their parent review's threads.
        """
        import re as _re
        # 1. URL fragment ``#pullrequestreview-<id>``
        m = _re.search(r"#pullrequestreview-(\d+)", url or "")
        if m:
            return int(m.group(1))
        # 2. Explicit field on the item.
        for key in ("pull_request_review_id", "review_id"):
            v = item.get(key)
            if v is not None:
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return None
        return None

    def _is_codex_review_thread_current_unresolved(item: dict[str, Any]) -> bool:
        """Return True iff ``item`` belongs to a current unresolved
        Codex review thread.

        The check uses the URL of the item to look up thread
        metadata in ``thread_meta_by_url``. A thread is
        considered a "Codex review-thread" iff:

        * the thread is NOT resolved,
        * the thread is NOT outdated,
        * the thread's first comment is authored by a
          ``chatgpt-codex-connector`` user (bot or not).
        """
        url = item.get("html_url") or item.get("url") or ""
        meta = thread_meta_by_url.get(url)
        if not meta:
            return False
        if meta.get("is_resolved", False):
            return False
        if meta.get("is_outdated", False):
            return False
        author_login = meta.get("author_login", "") or ""
        return author_login.startswith(CODEX_BOT_LOGIN_PREFIX)

    # 1. Issue comments
    ok, data, err = gh_api(args.repo, f"issues/{args.pr_number}/comments")
    if not ok:
        api_errors.append(f"issue_comments: {err}")
    else:
        sources_fetched.append(f"issues/{args.pr_number}/comments ({len(data)} items)")
        for item in data:
            is_codex_thread = _is_codex_review_thread_current_unresolved(item)
            findings = classify_item(
                item, "issue_comment", ignore_users,
                is_codex_review_thread_current_unresolved=is_codex_thread,
            )
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
            is_codex_thread = _is_codex_review_thread_current_unresolved(item)
            findings = classify_item(
                item, "inline_review_comment", ignore_users,
                is_codex_review_thread_current_unresolved=is_codex_thread,
            )
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
            is_codex_thread = _is_codex_review_thread_current_unresolved(item)
            findings = classify_item(
                item, "review", ignore_users,
                is_codex_review_thread_current_unresolved=is_codex_thread,
            )
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
                        is_codex_thread = _is_codex_review_thread_current_unresolved(c)
                        findings2 = classify_item(
                            c, "per_review_comment", ignore_users,
                            is_codex_review_thread_current_unresolved=is_codex_thread,
                        )
                        for f2 in findings2:
                            f2["_source_kind"] = "per_review_comment"
                        all_findings.extend(findings2)

    all_findings = dedup_findings(all_findings)

    # -----------------------------------------------------------------------
    # Attach thread metadata to each finding for rendering / blocker logic.
    # This is the existing behavior — preserved unchanged. The
    # source-aware flag passed into classify_item above already used
    # the thread metadata for the PRIMARY actionability decision; this
    # post-classify attachment is for the thread_id / is_resolved /
    # is_outdated fields used by the blocker classification below.
    #
    # Round-81 follow-up: for findings whose URL is a per-review
    # summary (not a thread discussion URL), the URL-based lookup
    # returns empty metadata and ``thread_id`` / ``thread_resolved``
    # would be empty/False. Without a fallback those findings
    # would remain as stale-blockers even after every thread in
    # their parent review is resolved. The review_id index lets us
    # inherit the resolution state from any thread in the same
    # review: if ALL threads in the review are resolved, the
    # per-review-comment finding is treated as resolved as well.
    # -----------------------------------------------------------------------
    for f in all_findings:
        url = f.get("url", "")
        meta = thread_meta_by_url.get(url, {})
        thread_id = meta.get("thread_id", "")
        thread_resolved = meta.get("is_resolved", False)
        thread_outdated = meta.get("is_outdated", False)
        if not thread_id:
            # Fallback: per-review-comment finding whose URL is
            # the review-summary URL. Look up threads by review_id
            # (encoded in the URL fragment ``#pullrequestreview-<id>``
            # or in the item's ``pull_request_review_id`` field).
            review_id = _extract_review_id(f, url)
            if review_id is not None:
                review_threads = thread_meta_by_review_id.get(
                    int(review_id), []
                )
                if review_threads:
                    # Inherit from the FIRST thread in the review.
                    # If every thread is resolved, the review-
                    # summary finding is moot.
                    thread_id = review_threads[0].get("thread_id", "")
                    thread_resolved = all(
                        t.get("is_resolved", False)
                        for t in review_threads
                    )
                    thread_outdated = all(
                        t.get("is_outdated", False)
                        for t in review_threads
                    )
                    # Mark the review_id so the gate can show
                    # the linkage in the rendered report.
                    f["_review_id"] = int(review_id)
        f["thread_id"] = thread_id
        f["thread_resolved"] = thread_resolved
        f["thread_outdated"] = thread_outdated

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