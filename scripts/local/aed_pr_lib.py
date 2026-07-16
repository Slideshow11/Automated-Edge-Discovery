"""aed_pr_lib.py

Canonical shared library for the AED PR lifecycle controller.

This module is the single source of truth for the small set of
canonical helpers that compose the public-facing AED PR lifecycle
controller (``aed_pr.py``). The controller calls functions defined
here directly; it does NOT chain subprocess calls to other AED
wrappers.

The functions in this module are intentionally pure-Python and free
of any I/O mode: callers pass in parsed live state and receive
canonical outputs back. The CLIs around them (aed_pr.py, and the
existing merge_pr_safely.py compatibility surface) own the I/O.

Public API:
    Authorization phrase:
        build_authorization_phrase(pr_number, head_sha) -> str
        is_valid_authorization_phrase(phrase, pr_number, head_sha) -> bool

    Safe merge command:
        build_safe_merge_command(pr_number, repo, head_sha) -> str
        reject_admin_argv(argv) -> None
            Raises ValueError if --admin appears anywhere in argv.

    Exact-head enforcement:
        HEAD_SHA_FULL_RE: full 40-hex regex
        HEAD_SHA_PREFIX_RE: at-least-7-hex prefix regex
        extract_full_sha_from_phrase(phrase) -> Optional[str]
            Extracts a full 40-character hex SHA from phrase text.
            Short prefixes (e.g. 7 chars) are NOT accepted.

    Lifecycle state vocabulary (operator-facing) and reason codes.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional


# -----------------------------------------------------------------------------
# Exact-head SHA enforcement
# -----------------------------------------------------------------------------

HEAD_SHA_FULL_RE = re.compile(r"\b([0-9a-f]{40})\b")
HEAD_SHA_PREFIX_RE = re.compile(r"\b([0-9a-f]{7,39})\b")

# Full 40-char SHA, anchored (e.g. for matching against expected-head input).
HEAD_SHA_ANCHORED_RE = re.compile(r"^[0-9a-f]{40}$")


def is_full_sha(s: str) -> bool:
    """True iff s is exactly 40 lowercase hex characters."""
    return bool(s) and bool(HEAD_SHA_ANCHORED_RE.match(s))


def extract_full_sha_from_phrase(phrase: str) -> Optional[str]:
    """Extract a full 40-char hex SHA from phrase text.

    Short prefixes (7..39 chars) are NOT accepted. Only a complete
    40-character hex string is returned. Returns None if none is found.

    The canonical phrase produced by build_authorization_phrase()
    embeds the full head SHA, so this extractor is the single source
    of truth for "what SHA did the human authorize".
    """
    if not isinstance(phrase, str):
        return None
    m = HEAD_SHA_FULL_RE.search(phrase)
    return m.group(1) if m else None


# -----------------------------------------------------------------------------
# Authorization phrase
# -----------------------------------------------------------------------------

# Canonical phrase form. This is the form the operator speaks and the
# form the controller validates against byte-for-byte. The governor
# engine (aed_policy) and the merge-command verifier both produce
# this exact form.
CANONICAL_PHRASE_FORMAT = (
    "I confirm merge PR #{pr_number} at {head_sha} "
    "using final-head reviewed clean state."
)


def build_authorization_phrase(pr_number: int, head_sha: str) -> str:
    """Return the canonical human authorization phrase.

    Both ``pr_number`` (int) and ``head_sha`` (str, full 40 hex) are
    required. The returned string is the single source of truth that
    every gate (policy engine, merge-command verifier, controller)
    must agree on.

    Raises ValueError on shape mismatch — the controller and any
    downstream gate must never produce or accept a malformed phrase.
    """
    if not isinstance(pr_number, int):
        raise ValueError(f"pr_number must be int, got {type(pr_number).__name__}")
    if not is_full_sha(head_sha):
        raise ValueError(
            f"head_sha must be a full 40-character hex SHA, got {head_sha!r}"
        )
    return CANONICAL_PHRASE_FORMAT.format(pr_number=pr_number, head_sha=head_sha)


def is_valid_authorization_phrase(
    phrase: str,
    pr_number: int,
    head_sha: str,
) -> bool:
    """True iff ``phrase`` byte-exactly equals the canonical phrase.

    No whitespace stripping, no whitespace collapsing, no newline
    tolerance, no case folding. Authorization phrases are proof of
    intent; even one extra or missing character is treated as a
    distinct phrase.
    """
    if not isinstance(phrase, str):
        return False
    canonical = build_authorization_phrase(pr_number, head_sha)
    return phrase == canonical


# -----------------------------------------------------------------------------
# Safe merge command
# -----------------------------------------------------------------------------

def build_safe_merge_command(pr_number: int, repo: str, head_sha: str) -> str:
    """Build the exact safe ``gh pr merge`` command.

    The command:
      - PR number <pr_number>
      - repository owner/name <repo>
      - --squash
      - --delete-branch
      - --match-head-commit <head_sha>  (the exact authorized head)
      - NEVER --admin
      - NEVER --auto (auto-merge)

    The command text is the single source of truth emitted by both
    the controller's ``status`` preview and the controller's
    ``merge`` action. Operators copy-paste this exact string.
    """
    if not isinstance(pr_number, int) or pr_number <= 0:
        raise ValueError(f"pr_number must be a positive int, got {pr_number!r}")
    if "/" not in repo:
        raise ValueError(f"repo must be in 'owner/name' form, got {repo!r}")
    if not is_full_sha(head_sha):
        raise ValueError(
            f"head_sha must be a full 40-character hex SHA, got {head_sha!r}"
        )
    return (
        f"gh pr merge {pr_number} "
        f"--repo {repo} "
        f"--squash "
        f"--delete-branch "
        f"--match-head-commit {head_sha}"
    )


def reject_admin_argv(argv: Iterable[str]) -> None:
    """Raise ValueError if --admin appears anywhere in argv.

    Applies both to flag-shaped (``--admin``) and embedded-string
    occurrences (e.g. ``"--admin"`` inside a single argv element,
    or ``--admin --repo ...`` inside a string-typed argument).
    Every merge pathway in AED runs argv through this gate before
    any subprocess spawn.
    """
    for arg in argv:
        if isinstance(arg, str) and "--admin" in arg:
            raise ValueError("--admin is forbidden in AED merge argv")


def parse_argv(argv: List[str]) -> List[str]:
    """Best-effort parsing of argv strings into token lists.

    Used for the embedded-string admin check: if an argv element is
    a string that contains shell-style tokens, check those too.
    """
    out: List[str] = []
    for arg in argv:
        if not isinstance(arg, str):
            continue
        out.extend(arg.split())
    return out


def argv_is_safe(argv: Iterable[str]) -> bool:
    """True iff argv contains no --admin flag and no auto-merge intent."""
    tokens = list(argv) + parse_argv(list(argv))
    for tok in tokens:
        if not isinstance(tok, str):
            continue
        if "--admin" in tok:
            return False
        # --auto by itself or as part of a longer flag is auto-merge intent.
        if tok == "--auto" or tok.startswith("--auto="):
            return False
    return True


# -----------------------------------------------------------------------------
# Operator-facing lifecycle state vocabulary
# -----------------------------------------------------------------------------
#
# The canonical operator faces ONE primary state and one next-action
# per invocation. Internal reason codes may remain richer for machine
# consumers, but the controller's status output MUST collapse into
# the small set below so that the operator does not have to learn the
# history of every old HOLD name from the retired wrappers.
#

LIFECYCLE_STATES = (
    "WAITING",                       # CI in flight, Codex in flight, or
                                     # waiting for human follow-up
    "ACTION_REQUIRED",               # one human action would unblock
    "BLOCKED",                       # a deterministic condition is not
                                     # satisfied; controller cannot
                                     # advance until repaired
    "READY_FOR_MERGE_AUTHORIZATION", # all gates green; human speaks
                                     # the canonical phrase
    "MERGED_PENDING_CLOSEOUT",       # merge commit landed; controller
                                     # performs mechanical closeout
    "COMPLETE",                      # closeout verified
)


__all__ = [
    "HEAD_SHA_FULL_RE",
    "HEAD_SHA_PREFIX_RE",
    "HEAD_SHA_ANCHORED_RE",
    "is_full_sha",
    "extract_full_sha_from_phrase",
    "CANONICAL_PHRASE_FORMAT",
    "build_authorization_phrase",
    "is_valid_authorization_phrase",
    "build_safe_merge_command",
    "reject_admin_argv",
    "argv_is_safe",
    "LIFECYCLE_STATES",
]
