"""aed_pr_readiness.py

Shared readiness evaluator for the canonical AED PR-lifecycle controller.

This module is the SINGLE SOURCE OF TRUTH for "is this PR ready to merge?"
consumed by ``aed_pr.py status``, ``aed_pr.py advance``, and
``aed_pr.py merge``. All three commands call :func:`evaluate_readiness`
on the freshly-fetched live PR state; none of them maintain their own
private gating logic.

Why a separate module?
----------------------

The deleted wrapper chain
(``finalize_with_phase_ledger`` -> ``aed_final_gate`` -> ``merge_pr_safely``)
each carried their own partial readiness interpretation. The controller's
``status``, ``advance``, and ``merge`` subcommands used to diverge:
``status`` emitted the highest-level human-facing summary, ``advance``
emitted only next-action guidance with no enforcement, and ``merge``
checked only phrase + state + draft + mergeable. This module replaces
all three with one strict gate list so the three subcommands cannot
disagree about what is and is not ready.

Machine readiness vs human authorization
----------------------------------------

Two distinct evaluations are exposed:

* :func:`evaluate_machine_readiness` - checks the 11 non-phrase gates
  on the supplied evidence. This is what ``status`` always calls so the
  controller can emit ``READY_FOR_MERGE_AUTHORIZATION`` (i.e. "every
  machine gate passes; speak the canonical phrase and run merge")
  without the operator having to supply a phrase first.
* :func:`evaluate_authorization` - byte-exactly compares a supplied
  phrase to the canonical phrase for the live head. The status
  command never calls this; the merge command always does.

A composite :class:`ReadinessVerdict` carries both, exposing:

* ``machine_ready`` - all 11 non-phrase gates passed
* ``authorization_required`` - True iff machine_ready is True and a
  phrase must be supplied to proceed
* ``authorization_valid`` - True iff a supplied phrase byte-matches the
  canonical phrase for the live head. None when no phrase was supplied.
* ``merge_ready`` - True iff machine_ready AND authorization_valid

This separation resolves the round-2 Codex finding that ``status``
always supplied ``authorization_phrase=None``, causing the authorization
gate to fail before ``status`` could emit the canonical phrase.

Gates enforced (all 12)
------------------------

Every gate is checked on the exact authorized head, freshly fetched at
the moment of evaluation. A single failure flips the verdict to
``READY=False`` and emits a :class:`ReadinessReason``. ``aed_pr merge``
must not call ``gh pr merge`` unless every gate passed on the live head
that the operator's authorization phrase names.

1. PR is open (state == "OPEN")
2. PR is non-draft (isDraft == False)
3. PR is mergeable (mergeable == True)
4. Authorization phrase byte-exactly matches the canonical phrase for
   the current full 40-character head SHA
5. Changed-file paths were successfully fetched
6. Changed-file scope exists and is clean
7. All required CI checks exist and pass on the current head
8. Exact-head Codex review evidence exists and is clean
9. Both formal reviews and PR-level issue comments were checked
10. Review-thread inventory was successfully fetched
11. Unresolved review-thread count is zero
12. No required evidence was missing, skipped, stale, malformed, or
    treated as passing by default

Gates 1-3 and 5-12 belong to the machine-readiness evaluation. Gate 4
is the authorization evaluation. ``status`` always evaluates machine
readiness; only ``merge`` evaluates authorization.

The evaluator never falls back to "treat missing as passing". A
missing required field, a stale Codex artifact, a failed CI run, an
unresolved thread, or an inventory-fetch error all block merge with
an explicit reason code.

This module does NOT itself call any ``gh`` command. Each gate
evaluator receives pre-fetched evidence; the controller (or a test)
owns the I/O. The module is pure-Python and stdlib-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


# -----------------------------------------------------------------------------
# Mergeable-state normalization
# -----------------------------------------------------------------------------

# GitHub's `gh pr view --json mergeable` returns the GraphQL MergeableState!
# enum. It is documented as a STRING with values "MERGEABLE", "CONFLICTING",
# and "UNKNOWN". Older CLI versions and some MCP responses may return a
# boolean. The evaluator must accept exactly the values that mean
# "ready to merge" and reject every other value.
#
# Accept:
#   - boolean True
#   - exact string "MERGEABLE" (case-normalized if necessary)
#
# Reject:
#   - boolean False
#   - strings "CONFLICTING", "UNKNOWN" (case-normalized)
#   - null
#   - absent field
#   - any unrecognized value (including empty string, integers, dicts, lists)
#
# IMPORTANT: Generic Python truthiness is NOT safe here. The empty string
# "" is falsy while "CONFLICTING" is truthy; using `if value:` would let
# "CONFLICTING" through and reject "MERGEABLE" only by accident.

_MERGEABLE_ACCEPT_STRINGS = frozenset({"MERGEABLE"})
_MERGEABLE_REJECT_STRINGS = frozenset({"CONFLICTING", "UNKNOWN"})


def normalize_mergeable(value: Any) -> Optional[bool]:
    """Public wrapper for the mergeable-state normalizer.

    Exposed so the controller's ``status``, ``advance``, and ``merge``
    paths all consume the exact same normalization the shared readiness
    evaluator uses. No controller path may apply its own truthiness
    check to the ``mergeable`` field.

    Accepts:
      * boolean ``True``
      * exact string ``"MERGEABLE"`` (case-normalized if necessary)

    Rejects:
      * boolean ``False``
      * strings ``"CONFLICTING"``, ``"UNKNOWN"`` (case-normalized)
      * ``None``, empty/absent field, any unrecognized value

    Returns ``True`` only when the value unambiguously means "PR is
    mergeable". Returns ``False`` when it means "PR is NOT mergeable".
    Returns ``None`` when it cannot be normalized; the evaluator treats
    ``None`` as "fail closed".
    """
    return _normalize_mergeable(value)


def _normalize_mergeable(value: Any) -> Optional[bool]:
    """Normalize GitHub mergeable-state payload to True/False/None.

    Returns True only for values that mean "PR is mergeable".
    Returns False for values that mean "PR is NOT mergeable".
    Returns None for absent / null / unrecognized values (treated as
    "unknown, fail closed" by the readiness gate).
    """
    if value is None:
        return None
    # Reject bool False and True distinctly so we do not accept 1 or 0.
    if value is True:
        return True
    if value is False:
        return False
    if isinstance(value, str):
        s = value.strip().upper()
        if s in _MERGEABLE_ACCEPT_STRINGS:
            return True
        if s in _MERGEABLE_REJECT_STRINGS:
            return False
        # Unrecognized string (including empty string after strip) -> None.
        return None
    # Any other type (int, list, dict, etc.) is not a valid mergeable
    # state representation. Fail closed.
    return None


# -----------------------------------------------------------------------------
# Canonical reason codes
# -----------------------------------------------------------------------------

REASON_PR_NOT_OPEN = "PR_NOT_OPEN"
REASON_PR_IS_DRAFT = "PR_IS_DRAFT"
REASON_PR_NOT_MERGEABLE = "PR_NOT_MERGEABLE"
REASON_PHRASE_MISMATCH = "PHRASE_MISMATCH"
REASON_CHANGED_FILES_MISSING = "CHANGED_FILES_NOT_FETCHED"
REASON_SCOPE_UNKNOWN = "SCOPE_UNKNOWN"
REASON_SCOPE_VIOLATION = "SCOPE_VIOLATION"
REASON_FORBIDDEN_FILE_TOUCHED = "FORBIDDEN_FILE_TOUCHED"
REASON_CI_MISSING = "REQUIRED_CI_MISSING"
REASON_CI_FAILED = "REQUIRED_CI_FAILED"
REASON_CI_PENDING = "REQUIRED_CI_PENDING"
REASON_CODEX_MISSING = "CODEX_EVIDENCE_MISSING"
REASON_CODEX_STALE = "CODEX_EVIDENCE_STALE"
REASON_CODEX_FAILED = "CODEX_EVIDENCE_FAILED"
REASON_CODEX_CLEAN_MISSING = "CODEX_CLEAN_VERDICT_MISSING"
REASON_REVIEWS_INCOMPLETE = "REVIEWS_AND_COMMENTS_INCOMPLETE"
REASON_THREAD_INVENTORY_FAILED = "THREAD_INVENTORY_FETCH_FAILED"
REASON_UNRESOLVED_THREAD = "UNRESOLVED_REVIEW_THREAD"
REASON_EVIDENCE_MISSING = "EVIDENCE_MISSING_OR_TREATED_AS_PASSING"

# Reason codes that mean "evidence must be re-fetched and re-checked
# before the operator is allowed to retry". The controller's status
# command uses this to keep the authorization phrase empty unless
# every gate passed freshly on the current head.
RETRY_REQUIRED_REASONS = frozenset({
    REASON_PR_NOT_OPEN,
    REASON_PR_IS_DRAFT,
    REASON_PR_NOT_MERGEABLE,
    REASON_PHRASE_MISMATCH,
    REASON_CHANGED_FILES_MISSING,
    REASON_SCOPE_UNKNOWN,
    REASON_SCOPE_VIOLATION,
    REASON_FORBIDDEN_FILE_TOUCHED,
    REASON_CI_MISSING,
    REASON_CI_FAILED,
    REASON_CI_PENDING,
    REASON_CODEX_MISSING,
    REASON_CODEX_STALE,
    REASON_CODEX_FAILED,
    REASON_CODEX_CLEAN_MISSING,
    REASON_REVIEWS_INCOMPLETE,
    REASON_THREAD_INVENTORY_FAILED,
    REASON_UNRESOLVED_THREAD,
    REASON_EVIDENCE_MISSING,
})


# -----------------------------------------------------------------------------
# Evidence bundle
# -----------------------------------------------------------------------------

@dataclass
class ReadinessEvidence:
    """Bundle of freshly-fetched evidence consumed by the evaluator.

    Each field is optional because not every caller has every piece of
    evidence yet. The evaluator's contract is: missing required evidence
    is a hard fail, never a pass-by-default. The bundle is plain
    dataclass data so a test can construct one without any I/O.
    """

    pr_state: Optional[str] = None
    is_draft: Optional[bool] = None
    # The mergeable field on a PR view can be either a boolean or the
    # GraphQL MergeableState! enum STRING ("MERGEABLE" | "CONFLICTING"
    # | "UNKNOWN"). The shared evaluator normalizes this through
    # normalize_mergeable(); never use generic Python truthiness.
    mergeable: Optional[Any] = None
    head_sha: Optional[str] = None

    authorization_phrase: Optional[str] = None

    changed_files: Optional[List[str]] = None
    changed_files_fetched: bool = False
    scope_clean: Optional[bool] = None
    out_of_scope_files: Optional[List[str]] = None
    forbidden_files_touched: Optional[List[str]] = None
    scope_blockers: Optional[List[str]] = None

    required_ci_names: Optional[List[str]] = None
    ci_conclusions: Optional[Dict[str, str]] = None  # name -> conclusion
    ci_missing: Optional[List[str]] = None           # required but no run
    ci_pending: Optional[List[str]] = None           # required and running
    ci_failed: Optional[List[str]] = None            # required and failed

    # Round-2 fix: explicit signal that the operator supplied an
    # allowed_files list. When False, the scope gate fails closed.
    allowed_files_supplied: bool = False

    codex_verdict: Optional[str] = None              # "CODEX_CLEAN_PASS" | ...
    codex_source: Optional[str] = None               # "issue_comment" | "review" | ...
    codex_reviewed_sha: Optional[str] = None         # SHA the artifact actually reviewed
    codex_clean_passed: Optional[bool] = None        # True iff verdict was clean
    codex_artifact_present: bool = False
    codex_artifact_fresh: Optional[bool] = None      # True iff reviewed_sha == head_sha
    codex_review_url: Optional[str] = None
    codex_review_id: Optional[str] = None

    reviews_inventory_complete: bool = False
    reviews_inventory_error: Optional[str] = None

    review_threads: Optional[List[Dict[str, Any]]] = None
    review_thread_inventory_complete: bool = False
    review_thread_inventory_error: Optional[str] = None
    unresolved_thread_count: int = 0
    unresolved_thread_ids: List[str] = field(default_factory=list)
    unresolved_human_thread_ids: List[str] = field(default_factory=list)
    unresolved_bot_thread_ids: List[str] = field(default_factory=list)
    outdated_bot_thread_ids: List[str] = field(default_factory=list)

    evidence_sources: Dict[str, str] = field(default_factory=dict)


# -----------------------------------------------------------------------------
# Reason
# -----------------------------------------------------------------------------

@dataclass
class ReadinessReason:
    code: str
    detail: str
    gate: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# -----------------------------------------------------------------------------
# Verdict
# -----------------------------------------------------------------------------

@dataclass
class ReadinessVerdict:
    """Composite readiness verdict consumed by status/advance/merge.

    The ``ready`` field is preserved for backward compatibility and is
    equivalent to ``merge_ready`` (i.e. machine_ready AND
    authorization_valid). New code should consult the four explicit
    fields below:

    * ``machine_ready`` - all 11 non-phrase gates passed
    * ``authorization_required`` - True iff machine_ready is True and a
      phrase must be supplied to proceed
    * ``authorization_valid`` - True iff a supplied phrase byte-matches
      the canonical phrase for the live head. None when no phrase was
      supplied.
    * ``merge_ready`` - True iff machine_ready AND authorization_valid

    The :attr:`reasons` list combines machine-readiness and
    authorization reasons (in that order). When only the phrase is
    missing, ``reasons`` contains exactly one entry with code
    :data:`REASON_PHRASE_MISMATCH`.
    """

    ready: bool
    reasons: List[ReadinessReason]
    gates_passed: List[str]
    gates_failed: List[str]
    # Round-2 additions: split machine vs authorization.
    machine_ready: bool = False
    authorization_required: bool = False
    authorization_valid: Optional[bool] = None

    @property
    def merge_ready(self) -> bool:
        """True iff machine gates passed and a phrase (when supplied) validates.

        When no phrase was supplied (the ``status`` path), this is True
        iff ``machine_ready`` is True - the canonical phrase remains
        for the operator to speak before running ``merge``.
        """
        if not self.machine_ready:
            return False
        if self.authorization_valid is None:
            return True
        return bool(self.authorization_valid)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ready": self.ready,
            "machine_ready": self.machine_ready,
            "authorization_required": self.authorization_required,
            "authorization_valid": self.authorization_valid,
            "merge_ready": self.merge_ready,
            "reasons": [r.to_dict() for r in self.reasons],
            "gates_passed": list(self.gates_passed),
            "gates_failed": list(self.gates_failed),
        }


# -----------------------------------------------------------------------------
# Gate identifiers
# -----------------------------------------------------------------------------

GATE_PR_OPEN = "pr_open"
GATE_PR_NON_DRAFT = "pr_non_draft"
GATE_PR_MERGEABLE = "pr_mergeable"
GATE_AUTHORIZATION_PHRASE = "authorization_phrase"
GATE_CHANGED_FILES_FETCHED = "changed_files_fetched"
GATE_SCOPE_CLEAN = "scope_clean"
GATE_CI_PRESENT = "ci_present_and_passing"
GATE_CODEX_EVIDENCE = "codex_exact_head_clean"
GATE_REVIEWS_AND_COMMENTS = "reviews_and_issue_comments_checked"
GATE_THREAD_INVENTORY = "review_thread_inventory_fetched"
GATE_UNRESOLVED_THREADS = "unresolved_review_threads_zero"
GATE_NO_MISSING_EVIDENCE = "no_evidence_missing_or_treated_as_passing"

ALL_GATES = (
    GATE_PR_OPEN,
    GATE_PR_NON_DRAFT,
    GATE_PR_MERGEABLE,
    GATE_AUTHORIZATION_PHRASE,
    GATE_CHANGED_FILES_FETCHED,
    GATE_SCOPE_CLEAN,
    GATE_CI_PRESENT,
    GATE_CODEX_EVIDENCE,
    GATE_REVIEWS_AND_COMMENTS,
    GATE_THREAD_INVENTORY,
    GATE_UNRESOLVED_THREADS,
    GATE_NO_MISSING_EVIDENCE,
)


# Gates that participate in machine readiness (gates 1-3 and 5-12).
# Gate 4 (authorization phrase) is the single human-supplied gate and
# is NOT part of machine readiness. This separation is the round-2
# fix: ``status`` evaluates MACHINE_GATES so it can emit
# ``READY_FOR_MERGE_AUTHORIZATION`` without requiring the operator to
# have already spoken the canonical phrase.
MACHINE_GATES = (
    GATE_PR_OPEN,
    GATE_PR_NON_DRAFT,
    GATE_PR_MERGEABLE,
    GATE_CHANGED_FILES_FETCHED,
    GATE_SCOPE_CLEAN,
    GATE_CI_PRESENT,
    GATE_CODEX_EVIDENCE,
    GATE_REVIEWS_AND_COMMENTS,
    GATE_THREAD_INVENTORY,
    GATE_UNRESOLVED_THREADS,
    GATE_NO_MISSING_EVIDENCE,
)


# -----------------------------------------------------------------------------
# Phase-ledger / Codex verdict normalization
# -----------------------------------------------------------------------------

# These are the status values emitted by audit_codex_response_for_pr.py
# that count as a clean Codex verdict on the exact head.
CODEX_CLEAN_VERDICTS = frozenset({
    "CODEX_CLEAN_PASS",
    "CODEX_CLEAN_PASS_RESOLVE_ONLY_NEEDED",
    "MERGE_READY_AWAITING_HUMAN_AUTHORIZATION",
})


def is_codex_clean_verdict(verdict: Optional[str]) -> bool:
    """True iff the Codex classifier verdict is a clean verdict.

    Anything outside the canonical set (e.g. ``HOLD_CODEX_RESPONSE_PENDING``,
    ``HOLD_NEW_CODEX_THREAD``, ``HOLD_HEAD_CHANGED``, ``HOLD_MERGE_STATE_BLOCKED``,
    ``ERROR_*``, or ``None``) is treated as not-clean.
    """
    if not isinstance(verdict, str):
        return False
    return verdict in CODEX_CLEAN_VERDICTS


def classify_thread_actor(actor_login: Optional[str]) -> str:
    """Classify a review-thread actor as ``human``, ``bot``, or ``unknown``.

    Code uses these categories to decide which threads are eligible for
    automatic resolution: only the ``bot`` category is eligible, and only
    when the thread is also marked ``is_outdated=True`` by GitHub.
    """
    if not isinstance(actor_login, str) or not actor_login:
        return "unknown"
    lowered = actor_login.lower()
    if lowered.endswith("[bot]") or lowered in {
        "chatgpt-codex-connector",
        "chatgpt-codex-connector[bot]",
        "github-actions[bot]",
        "dependabot[bot]",
        "renovate[bot]",
    }:
        return "bot"
    return "human"


def partition_unresolved_threads(
    threads: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Partition an inventory of review threads by actor and staleness.

    Returns a dict with four lists so the controller can emit the
    full inventory in its status output:
      - ``unresolved_human`` (never auto-resolvable; blocks merge)
      - ``unresolved_bot_current`` (blocks merge; new finding on this head)
      - ``outdated_bot_unresolved`` (eligible for bounded auto-resolution)
      - ``resolved`` (informational only)
    """
    unresolved_human: List[Dict[str, Any]] = []
    unresolved_bot_current: List[Dict[str, Any]] = []
    outdated_bot_unresolved: List[Dict[str, Any]] = []
    resolved: List[Dict[str, Any]] = []

    for thread in threads or []:
        is_resolved = bool(thread.get("isResolved") or thread.get("is_resolved"))
        if is_resolved:
            resolved.append(thread)
            continue
        actor = classify_thread_actor(thread.get("author") or thread.get("author_login"))
        is_outdated = bool(thread.get("isOutdated") or thread.get("is_outdated"))
        if actor == "human":
            unresolved_human.append(thread)
        elif actor == "bot" and is_outdated:
            outdated_bot_unresolved.append(thread)
        elif actor == "bot":
            unresolved_bot_current.append(thread)
        # unknown-actor unresolved threads are conservatively treated as
        # current bot threads: they block merge and are NOT auto-resolvable.
        elif is_outdated:
            outdated_bot_unresolved.append(thread)
        else:
            unresolved_bot_current.append(thread)

    return {
        "unresolved_human": unresolved_human,
        "unresolved_bot_current": unresolved_bot_current,
        "outdated_bot_unresolved": outdated_bot_unresolved,
        "resolved": resolved,
    }


# Round-2 fix: deterministic thread-eligibility check for auto-resolution.
#
# A thread may be eligible for bounded auto-resolution only when EVERY
# condition below is true. Any uncertainty must make the thread
# ineligible. This is the safety boundary between "stale bot finding
# from a previous round" (auto-resolvable) and "current-bot finding
# on this head" or "human finding" (NEVER auto-resolvable).
#
# Conditions:
#
#  1. The top-level author is the recognized Codex bot (or another
#     recognized bot - the actor classification helper covers both).
#  2. Every comment and reply in the thread is bot-authored (no human
#     reply anywhere in the thread).
#  3. The thread is reported by GitHub as outdated.
#  4. The underlying finding was addressed by a later commit than
#     the thread's commit anchor. The thread's ``comment_sha`` is
#     the SHA on which the top-level comment was posted.
#  5. Exact-head Codex evidence on the live head is clean (no current
#     Codex finding supersedes or repeats the outdated one).
#  6. The thread's bot actor appears in the recognized-bot set
#     explicitly (defence against unknown-bot false positives).
#
# The function returns (eligible: bool, reason: str). When ``eligible``
# is False, ``reason`` is one of the documented ineligibility reasons
# and is safe to surface in the controller's action report.

# Recognized bot authors eligible for bounded auto-resolution. Keep in
# sync with aed_pr.classify_thread_actor / partition_unresolved_threads.
_RECOGNIZED_BOT_LOGINS = frozenset({
    "chatgpt-codex-connector",
    "chatgpt-codex-connector[bot]",
    "github-actions[bot]",
    "dependabot[bot]",
    "renovate[bot]",
})


# Round-4 fix #2: anchor source list. The reviewer-comment commit
# anchor must be one of these concrete fields, in order of preference.
# ``outdated``, timestamps, line numbers, and thread order are NOT
# considered anchors. The live
# ``scripts/local/audit_codex_response_for_pr.py`` packet SHOULD
# populate ``original_commit_sha`` from the top-level review comment;
# the helpers below normalize that input.
_ANCHOR_FIELDS = ("original_commit_sha", "comment_sha", "head_sha")


def _extract_thread_anchor(thread: Dict[str, Any]) -> Optional[str]:
    """Extract a canonical commit SHA anchor from a review thread.

    Returns the first non-empty field from :data:`_ANCHOR_FIELDS` that
    is a canonical 40-lowercase-hex SHA. Returns ``None`` when none of
    the fields hold a canonical SHA. The result is suitable as a
    ``comment_sha``-equivalent for the eligibility check.
    """
    if not isinstance(thread, dict):
        return None
    for key in _ANCHOR_FIELDS:
        value = thread.get(key)
        if isinstance(value, str) and is_canonical_head_sha(value):
            return value
    return None


def normalize_thread_anchor(
    thread: Dict[str, Any],
    *,
    fallback_head_sha: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a copy of ``thread`` with the anchor populated.

    Reads ``original_commit_sha``, ``comment_sha``, or ``head_sha``
    (in that order). When none of the fields hold a canonical SHA,
    copies the thread unchanged so the caller can detect the missing
    anchor via :func:`_extract_thread_anchor` returning ``None``.
    """
    if not isinstance(thread, dict):
        return dict(thread) if hasattr(thread, "items") else {}
    out = dict(thread)
    anchor = _extract_thread_anchor(out)
    if anchor is None:
        # Annotate the thread with the canonical field name so the
        # controller's classification report can surface the missing
        # anchor explicitly. The original fields are NOT modified.
        out.setdefault("_missing_anchor_fields", [])
        if not isinstance(out["_missing_anchor_fields"], list):
            out["_missing_anchor_fields"] = []
        for key in _ANCHOR_FIELDS:
            value = out.get(key)
            if not (isinstance(value, str) and is_canonical_head_sha(value)):
                if key not in out["_missing_anchor_fields"]:
                    out["_missing_anchor_fields"].append(key)
        return out
    # Promote the discovered anchor to the canonical field.
    out["original_commit_sha"] = anchor
    out["comment_sha"] = anchor
    out.pop("_missing_anchor_fields", None)
    return out


def verify_anchor_ancestry(
    repo: str,
    anchor_sha: str,
    head_sha: str,
    *,
    runner: Optional[Any] = None,
) -> Tuple[bool, str]:
    """Verify that ``head_sha`` descends from ``anchor_sha``.

    Round-5 follow-up (Codex review 4724989281 on ``301ef32``): the
    previous eligibility check treated ``anchor_sha != head_sha`` as
    proof that a later commit addressed the finding. This is wrong
    on a rebase or force-push: the anchor can be unrelated to the
    current branch's history. The controller must call GitHub's
    ``compare`` API and require ``status="ahead"`` to prove the
    ancestry.

    Args:
        repo: ``owner/name`` repository string.
        anchor_sha: the thread's commit anchor (40-hex canonical).
        head_sha: the live PR head SHA (40-hex canonical).
        runner: optional ``subprocess.run`` replacement for tests.
            When ``None``, ``subprocess.run`` is used. The runner
            must accept ``(cmd, capture_output, text, timeout)``
            and return an object with ``returncode``, ``stdout``,
            ``stderr`` attributes.

    Returns:
        ``(is_ancestor, reason)``. ``reason`` is one of:

        - ``anchor_is_ancestor`` when ancestry is proven;
        - ``anchor_equals_head`` when both SHAs are identical;
        - ``missing_commit_anchor`` when ``anchor_sha`` is empty;
        - ``malformed_commit_anchor`` when either SHA is not
          canonical;
        - ``ancestry_unavailable`` when the comparison API call
          fails, returns malformed JSON, or returns an unexpected
          ``status`` value.

    The function never infers ancestry from outdated status,
    timestamps, thread order, or SHA inequality. The comparison
    response must explicitly state ``status == "ahead"`` for the
    anchor to be considered an ancestor.
    """
    if runner is None:
        import subprocess as _subprocess
        runner = _subprocess.run
    if not isinstance(repo, str) or "/" not in repo:
        return False, "ancestry_unavailable"
    if not isinstance(anchor_sha, str) or not anchor_sha:
        return False, "missing_commit_anchor"
    if not isinstance(head_sha, str) or not head_sha:
        return False, "ancestry_unavailable"
    if not is_canonical_head_sha(anchor_sha):
        return False, "malformed_commit_anchor"
    if not is_canonical_head_sha(head_sha):
        return False, "ancestry_unavailable"
    if anchor_sha == head_sha:
        return False, "anchor_equals_head"
    cmd = [
        "gh", "api",
        f"repos/{repo}/compare/{anchor_sha}...{head_sha}",
        "--jq", ".status",
    ]
    try:
        proc = runner(
            cmd, capture_output=True, text=True, timeout=30
        )
    except (OSError, TimeoutError) as exc:
        return False, "ancestry_unavailable"
    if proc.returncode != 0:
        return False, "ancestry_unavailable"
    status = (proc.stdout or "").strip()
    if status == "ahead":
        return True, "anchor_is_ancestor"
    # Identical handled above; diverged, behind, missing or
    # unexpected statuses all block.
    return False, "ancestry_unavailable"


def is_eligible_for_bot_resolution(
    thread: Dict[str, Any],
    *,
    head_sha: Optional[str],
    codex_verdict: Optional[str],
    codex_clean_passed: Optional[bool],
    codex_reviewed_sha: Optional[str] = None,
    repo: Optional[str] = None,
    ancestry_runner: Optional[Any] = None,
) -> Tuple[bool, str]:
    """Return ``(eligible, reason)`` for a single review thread.

    The eligibility contract is documented in the module-level
    comment above. Any uncertainty produces ``eligible=False`` with a
    specific reason code. Round-4 fix #2 adds ``missing_commit_anchor``
    and ``malformed_commit_anchor`` to the reason vocabulary; both
    fire when the thread lacks a canonical 40-hex SHA anchor that
    GitHub's review-thread API supplied (or a normalizer promoted
    into ``original_commit_sha``/``comment_sha``/``head_sha``).
    """
    if not isinstance(thread, dict):
        return False, "actor_not_bot"

    # Idempotency: a thread GitHub already reports as resolved must
    # never be re-resolved. Even if it is otherwise eligible, the
    # mutation would be a no-op on the live side; the controller
    # treats it as ineligible so re-running advance does not issue
    # redundant resolveReviewThread calls.
    if bool(thread.get("isResolved") or thread.get("is_resolved")):
        return False, "already_resolved"

    # Condition 3: thread must be outdated.
    is_outdated = bool(thread.get("isOutdated") or thread.get("is_outdated"))
    if not is_outdated:
        return False, "not_outdated"

    # Condition 1: top-level author must be a recognized bot.
    top_actor_login = thread.get("author") or thread.get("author_login")
    if not isinstance(top_actor_login, str) or not top_actor_login:
        return False, "actor_not_bot"
    if top_actor_login.lower() not in _RECOGNIZED_BOT_LOGINS:
        return False, "actor_not_bot"

    # Condition 2: every comment in the thread must be bot-authored.
    comments = thread.get("comments") or thread.get("comment_list") or []
    if not isinstance(comments, list):
        # If the inventory does not contain a comment list, we cannot
        # prove condition 2. Refuse to be eligible.
        return False, "unknown_actor_in_thread"
    for c in comments:
        if not isinstance(c, dict):
            return False, "unknown_actor_in_thread"
        author = c.get("author") or c.get("author_login") or c.get("user")
        if not isinstance(author, str) or not author:
            return False, "unknown_actor_in_thread"
        if author.lower() not in _RECOGNIZED_BOT_LOGINS:
            return False, "human_reply"

    # Condition 4: the underlying finding must have been addressed
    # by a later commit. The anchor must be a canonical 40-hex SHA
    # GitHub's review-thread API supplied (or that a normalizer
    # populated). Missing or malformed anchors fail closed - the
    # brief is explicit: do not infer the anchor from outdated,
    # timestamps, line numbers, or thread order.
    anchor_sha = _extract_thread_anchor(thread)
    if anchor_sha is None:
        # Distinguish between "no anchor field at all" and "anchor
        # field present but malformed". Both fail closed; the reason
        # is reported as ``missing_commit_anchor`` so the controller
        # can surface the diagnostic to the operator.
        if any(
            thread.get(key)
            for key in _ANCHOR_FIELDS
        ):
            return False, "malformed_commit_anchor"
        return False, "missing_commit_anchor"
    if not isinstance(head_sha, str) or not head_sha:
        return False, "head_unknown"
    if anchor_sha == head_sha:
        # The thread is anchored to the current head - not a stale
        # finding. Refuse.
        return False, "no_later_commit"

    # Round-5 follow-up: require VERIFIED ancestry via the GitHub
    # compare API. ``anchor_sha != head_sha`` alone is NOT proof of
    # a later commit; on a rebase or force-push the anchor can be
    # unrelated to the current branch's history. ``verify_anchor_
    # ancestry`` returns the precise reason on failure.
    if not isinstance(repo, str) or not repo:
        # No repo supplied -> refuse with a precise reason. The
        # controller's caller MUST supply ``repo`` so the verifier
        # can run.
        return False, "ancestry_unavailable"
    is_ancestor, ancestry_reason = verify_anchor_ancestry(
        repo, anchor_sha, head_sha, runner=ancestry_runner,
    )
    if not is_ancestor:
        if ancestry_reason == "anchor_equals_head":
            return False, "no_later_commit"
        return False, ancestry_reason

    # Condition 5/6: exact-head Codex evidence must be clean and the
    # reviewed SHA must match the live head.
    if codex_reviewed_sha is not None and isinstance(codex_reviewed_sha, str):
        if codex_reviewed_sha != head_sha:
            return False, "codex_head_mismatch"
    if codex_clean_passed is not True:
        return False, "codex_not_clean"
    if not is_codex_clean_verdict(codex_verdict):
        return False, "codex_not_clean"

    return True, "eligible"


def is_canonical_head_sha(sha: Optional[str]) -> bool:
    """True iff sha is exactly 40 lowercase hex characters."""
    if not isinstance(sha, str) or len(sha) != 40:
        return False
    return all(c in "0123456789abcdef" for c in sha)


# -----------------------------------------------------------------------------
# Evaluator
# -----------------------------------------------------------------------------

def _evaluate_machine_gates(
    evidence: ReadinessEvidence,
) -> Tuple[List[ReadinessReason], List[str], List[str]]:
    """Evaluate the 11 non-phrase gates on the supplied evidence.

    Returns ``(reasons, passed, failed)``. Caller owns the assembly of
    the composite verdict. Each gate's logic mirrors what was in
    :func:`evaluate_readiness`; this helper exists so :func:`evaluate_readiness`
    can be expressed as ``machine + authorization`` without duplication.

    The returned ``passed`` / ``failed`` lists contain only
    :data:`MACHINE_GATES`. Gate 4 (authorization phrase) is intentionally
    excluded here.
    """
    reasons: List[ReadinessReason] = []
    passed: List[str] = []
    failed: List[str] = []
    head_sha = evidence.head_sha

    # 1. PR is open
    if evidence.pr_state == "OPEN":
        passed.append(GATE_PR_OPEN)
    else:
        failed.append(GATE_PR_OPEN)
        reasons.append(ReadinessReason(
            code=REASON_PR_NOT_OPEN,
            detail=f"PR state is {evidence.pr_state!r}, not OPEN",
            gate=GATE_PR_OPEN,
        ))

    # 2. PR is non-draft
    if evidence.is_draft is False:
        passed.append(GATE_PR_NON_DRAFT)
    else:
        failed.append(GATE_PR_NON_DRAFT)
        reasons.append(ReadinessReason(
            code=REASON_PR_IS_DRAFT,
            detail="PR is still a draft",
            gate=GATE_PR_NON_DRAFT,
        ))

    # 3. PR is mergeable.
    # GitHub's `gh pr view --json mergeable` returns the GraphQL
    # MergeableState! enum as a STRING: "MERGEABLE" | "CONFLICTING"
    # | "UNKNOWN". Some CLI versions may also return a real bool. The
    # evaluator must accept exactly the values that mean "ready to
    # merge" and reject every other value, including the truthy-looking
    # string "CONFLICTING". Generic Python truthiness is NOT safe
    # because the empty string "" is falsy while "CONFLICTING" is truthy.
    _mergeable_norm = _normalize_mergeable(evidence.mergeable)
    if _mergeable_norm is True:
        passed.append(GATE_PR_MERGEABLE)
    else:
        failed.append(GATE_PR_MERGEABLE)
        reasons.append(ReadinessReason(
            code=REASON_PR_NOT_MERGEABLE,
            detail=(
                "PR is not mergeable "
                f"(mergeable={evidence.mergeable!r}, "
                f"normalized={_mergeable_norm!r})"
            ),
            gate=GATE_PR_MERGEABLE,
        ))

    # 5. Changed-file paths were successfully fetched
    if evidence.changed_files_fetched and isinstance(evidence.changed_files, list):
        passed.append(GATE_CHANGED_FILES_FETCHED)
    else:
        failed.append(GATE_CHANGED_FILES_FETCHED)
        reasons.append(ReadinessReason(
            code=REASON_CHANGED_FILES_MISSING,
            detail="changed-file paths were not successfully fetched",
            gate=GATE_CHANGED_FILES_FETCHED,
        ))

    # 6. Changed-file scope exists and is clean.
    # The controller MUST have supplied an explicit allowed_files list.
    # When allowed_files_supplied is False, the gate fails closed even
    # if scope_clean happens to be True (a defensive belt-and-braces
    # check that prevents the controller from silently treating a PR
    # as in-scope just because the scope check returned no violations).
    if not evidence.allowed_files_supplied and evidence.changed_files_fetched:
        failed.append(GATE_SCOPE_CLEAN)
        reasons.append(ReadinessReason(
            code=REASON_SCOPE_UNKNOWN,
            detail=(
                "operator did not supply an allowed_files list; the scope "
                "gate fails closed"
            ),
            gate=GATE_SCOPE_CLEAN,
        ))
    elif evidence.scope_clean is True:
        passed.append(GATE_SCOPE_CLEAN)
    elif evidence.scope_clean is False:
        failed.append(GATE_SCOPE_CLEAN)
        if evidence.forbidden_files_touched:
            reasons.append(ReadinessReason(
                code=REASON_FORBIDDEN_FILE_TOUCHED,
                detail=(
                    "forbidden files touched: "
                    + ",".join(evidence.forbidden_files_touched)
                ),
                gate=GATE_SCOPE_CLEAN,
            ))
        elif evidence.out_of_scope_files:
            reasons.append(ReadinessReason(
                code=REASON_SCOPE_VIOLATION,
                detail=(
                    "out-of-scope files: "
                    + ",".join(evidence.out_of_scope_files)
                ),
                gate=GATE_SCOPE_CLEAN,
            ))
        else:
            reasons.append(ReadinessReason(
                code=REASON_SCOPE_VIOLATION,
                detail="scope check reported non-clean without specific "
                       "out-of-scope or forbidden-file detail",
                gate=GATE_SCOPE_CLEAN,
            ))
    else:
        failed.append(GATE_SCOPE_CLEAN)
        reasons.append(ReadinessReason(
            code=REASON_SCOPE_UNKNOWN,
            detail="scope status is unknown (allowed_files missing or "
                   "scope check did not run)",
            gate=GATE_SCOPE_CLEAN,
        ))

    # 7. Required CI checks exist and pass
    if (
        isinstance(evidence.required_ci_names, list)
        and len(evidence.required_ci_names) > 0
        and not (evidence.ci_missing or [])
        and not (evidence.ci_pending or [])
        and not (evidence.ci_failed or [])
    ):
        passed.append(GATE_CI_PRESENT)
    else:
        failed.append(GATE_CI_PRESENT)
        if evidence.ci_missing:
            reasons.append(ReadinessReason(
                code=REASON_CI_MISSING,
                detail="missing required CI runs: "
                       + ",".join(evidence.ci_missing),
                gate=GATE_CI_PRESENT,
            ))
        if evidence.ci_pending:
            reasons.append(ReadinessReason(
                code=REASON_CI_PENDING,
                detail="pending required CI runs: "
                       + ",".join(evidence.ci_pending),
                gate=GATE_CI_PRESENT,
            ))
        if evidence.ci_failed:
            reasons.append(ReadinessReason(
                code=REASON_CI_FAILED,
                detail="failed required CI runs: "
                       + ",".join(evidence.ci_failed),
                gate=GATE_CI_PRESENT,
            ))
        if not (evidence.ci_missing or evidence.ci_pending or evidence.ci_failed):
            reasons.append(ReadinessReason(
                code=REASON_CI_MISSING,
                detail="required_ci_names is empty or not provided",
                gate=GATE_CI_PRESENT,
            ))

    # 8. Exact-head Codex review evidence exists and is clean
    if (
        evidence.codex_artifact_present
        and evidence.codex_artifact_fresh is True
        and is_codex_clean_verdict(evidence.codex_verdict)
        and evidence.codex_clean_passed is True
    ):
        passed.append(GATE_CODEX_EVIDENCE)
    else:
        failed.append(GATE_CODEX_EVIDENCE)
        if not evidence.codex_artifact_present:
            reasons.append(ReadinessReason(
                code=REASON_CODEX_MISSING,
                detail="Codex artifact not provided",
                gate=GATE_CODEX_EVIDENCE,
            ))
        elif evidence.codex_artifact_fresh is False:
            reasons.append(ReadinessReason(
                code=REASON_CODEX_STALE,
                detail=(
                    "Codex artifact reviewed SHA "
                    f"{evidence.codex_reviewed_sha!r} does not match "
                    f"current head {head_sha!r}"
                ),
                gate=GATE_CODEX_EVIDENCE,
            ))
        elif not is_codex_clean_verdict(evidence.codex_verdict):
            reasons.append(ReadinessReason(
                code=REASON_CODEX_FAILED,
                detail=(
                    f"Codex verdict {evidence.codex_verdict!r} is not a "
                    "clean verdict"
                ),
                gate=GATE_CODEX_EVIDENCE,
            ))
        else:
            reasons.append(ReadinessReason(
                code=REASON_CODEX_CLEAN_MISSING,
                detail=(
                    "Codex artifact present but clean_passed flag is "
                    f"{evidence.codex_clean_passed!r}"
                ),
                gate=GATE_CODEX_EVIDENCE,
            ))

    # 9. Both formal reviews and PR-level issue comments were checked
    if evidence.reviews_inventory_complete:
        passed.append(GATE_REVIEWS_AND_COMMENTS)
    else:
        failed.append(GATE_REVIEWS_AND_COMMENTS)
        reasons.append(ReadinessReason(
            code=REASON_REVIEWS_INCOMPLETE,
            detail=(
                "review / issue-comment inventory incomplete: "
                f"{evidence.reviews_inventory_error or 'unknown error'}"
            ),
            gate=GATE_REVIEWS_AND_COMMENTS,
        ))

    # 10. Review-thread inventory was successfully fetched
    if evidence.review_thread_inventory_complete:
        passed.append(GATE_THREAD_INVENTORY)
    else:
        failed.append(GATE_THREAD_INVENTORY)
        reasons.append(ReadinessReason(
            code=REASON_THREAD_INVENTORY_FAILED,
            detail=(
                "review-thread inventory fetch failed: "
                f"{evidence.review_thread_inventory_error or 'unknown error'}"
            ),
            gate=GATE_THREAD_INVENTORY,
        ))

    # 11. Unresolved review-thread count is zero
    if (
        evidence.review_thread_inventory_complete
        and evidence.unresolved_thread_count == 0
    ):
        passed.append(GATE_UNRESOLVED_THREADS)
    else:
        failed.append(GATE_UNRESOLVED_THREADS)
        if evidence.unresolved_human_thread_ids:
            reasons.append(ReadinessReason(
                code=REASON_UNRESOLVED_THREAD,
                detail=(
                    "unresolved human-involved threads: "
                    + ",".join(evidence.unresolved_human_thread_ids)
                ),
                gate=GATE_UNRESOLVED_THREADS,
            ))
        if evidence.unresolved_thread_ids:
            reasons.append(ReadinessReason(
                code=REASON_UNRESOLVED_THREAD,
                detail=(
                    f"{evidence.unresolved_thread_count} unresolved review "
                    "thread(s): "
                    + ",".join(evidence.unresolved_thread_ids)
                ),
                gate=GATE_UNRESOLVED_THREADS,
            ))
        elif not evidence.review_thread_inventory_complete:
            reasons.append(ReadinessReason(
                code=REASON_THREAD_INVENTORY_FAILED,
                detail="review-thread inventory was not successfully fetched",
                gate=GATE_UNRESOLVED_THREADS,
            ))

    # 12. No required evidence missing or treated-as-passing.
    # The evidence_sources ledger is intentionally separated from
    # ``pr_number`` (which is metadata, not evidence). Anything else
    # recorded there must be ``"fetched"``; anything else is a fail.
    missing_sources = [
        name for name, status in evidence.evidence_sources.items()
        if status != "fetched"
    ]
    if not missing_sources:
        passed.append(GATE_NO_MISSING_EVIDENCE)
    else:
        failed.append(GATE_NO_MISSING_EVIDENCE)
        reasons.append(ReadinessReason(
            code=REASON_EVIDENCE_MISSING,
            detail=(
                "missing/skipped/stale evidence sources: "
                + ",".join(missing_sources)
            ),
            gate=GATE_NO_MISSING_EVIDENCE,
        ))

    return reasons, passed, failed


def evaluate_machine_readiness(
    evidence: ReadinessEvidence,
) -> ReadinessVerdict:
    """Evaluate ONLY the 11 non-phrase machine gates.

    The canonical phrase (gate 4) is deliberately not evaluated here;
    it is the operator's responsibility and is checked separately by
    :func:`evaluate_authorization`. ``status`` always calls this
    function so the controller can emit ``READY_FOR_MERGE_AUTHORIZATION``
    when every machine gate passes.

    Returns a :class:`ReadinessVerdict` with
    ``machine_ready=True`` and ``authorization_valid=None``. The
    ``authorization_required`` field is set to ``True`` iff
    ``machine_ready`` is True (i.e. the operator's next action is to
    speak the canonical phrase and run ``merge``).
    """
    if not is_canonical_head_sha(evidence.head_sha):
        # Without a canonical 40-hex head SHA no machine gate can be
        # evaluated against the exact authorized head. Every machine
        # gate fails closed.
        return ReadinessVerdict(
            ready=False,
            reasons=[ReadinessReason(
                code=REASON_EVIDENCE_MISSING,
                detail=f"head_sha must be exactly 40 lowercase hex chars; got {evidence.head_sha!r}",
                gate=GATE_NO_MISSING_EVIDENCE,
            )],
            passed=[],
            failed=list(MACHINE_GATES),
            machine_ready=False,
            authorization_required=False,
            authorization_valid=None,
        )

    reasons, passed, failed = _evaluate_machine_gates(evidence)
    machine_ready = not failed
    return ReadinessVerdict(
        # For backward compatibility, ``ready`` mirrors ``machine_ready``
        # when no phrase has been supplied. ``merge_ready`` (the new
        # canonical field) follows the same logic.
        ready=machine_ready,
        reasons=reasons,
        gates_passed=passed,
        gates_failed=failed,
        machine_ready=machine_ready,
        authorization_required=machine_ready,
        authorization_valid=None,
    )


def evaluate_authorization(
    evidence: ReadinessEvidence,
    supplied_phrase: Optional[str],
) -> Tuple[Optional[ReadinessReason], List[str]]:
    """Evaluate the authorization phrase gate (gate 4).

    Returns ``(reason, gates_failed)`` where ``reason`` is None on a
    pass and a :class:`ReadinessReason` on a fail; ``gates_failed``
    contains ``[GATE_AUTHORIZATION_PHRASE]`` on a fail and ``[]`` on a
    pass. A ``None`` or empty supplied_phrase always fails closed - the
    merge command MUST refuse to merge without an exact authorization
    phrase.
    """
    canonical_phrase = _build_canonical_phrase(evidence)
    if (
        supplied_phrase
        and isinstance(supplied_phrase, str)
        and canonical_phrase is not None
        and supplied_phrase == canonical_phrase
    ):
        return None, []
    reason = ReadinessReason(
        code=REASON_PHRASE_MISMATCH,
        detail=(
            "authorization phrase does NOT byte-match the canonical "
            "phrase for the current head SHA"
        ),
        gate=GATE_AUTHORIZATION_PHRASE,
    )
    return reason, [GATE_AUTHORIZATION_PHRASE]


def evaluate_readiness(
    evidence: ReadinessEvidence,
    expected_canonical_phrase: Optional[str] = None,
) -> ReadinessVerdict:
    """Evaluate the full 12-gate readiness verdict on the supplied evidence.

    ``expected_canonical_phrase`` is the phrase the operator spoke;
    if not supplied, ``evidence.authorization_phrase`` is used.

    This function is the canonical composite evaluator. It runs:

    1. :func:`evaluate_machine_readiness` (gates 1-3, 5-12)
    2. :func:`evaluate_authorization` (gate 4) using the supplied
       phrase. When no phrase is supplied (the ``status`` path),
       ``authorization_valid`` is set to ``None`` and the only
       authorization-related output is the
       :data:`REASON_PHRASE_MISMATCH` reason code.

    The returned verdict has explicit ``machine_ready``,
    ``authorization_required``, ``authorization_valid``, and
    ``merge_ready`` fields. The historical ``ready`` field is preserved
    for backward compatibility and equals ``merge_ready``.
    """
    machine = evaluate_machine_readiness(evidence)
    phrase = evidence.authorization_phrase or expected_canonical_phrase
    auth_reason, auth_failed = evaluate_authorization(evidence, phrase)

    reasons = list(machine.reasons)
    passed = list(machine.gates_passed)
    failed = list(machine.gates_failed)
    if auth_reason is not None:
        reasons.append(auth_reason)
        failed.extend(auth_failed)
    else:
        # Authorization gate (gate 4) is part of ALL_GATES so it must
        # appear in ``passed`` when the supplied phrase matches the
        # canonical phrase for the live head.
        passed.append(GATE_AUTHORIZATION_PHRASE)

    # ``authorization_valid`` reflects whether the supplied phrase
    # matched the canonical phrase for the live head. The brief's
    # contract: ``missing phrase`` (None/empty) is False, not None -
    # because the merge command refuses merge on a missing phrase.
    if phrase is None:
        authorization_valid = False
    else:
        authorization_valid = auth_reason is None

    merge_ready = machine.machine_ready and bool(authorization_valid)

    return ReadinessVerdict(
        ready=merge_ready,
        reasons=reasons,
        gates_passed=passed,
        gates_failed=failed,
        machine_ready=machine.machine_ready,
        authorization_required=machine.machine_ready,
        authorization_valid=authorization_valid,
    )


def _build_canonical_phrase(evidence: ReadinessEvidence) -> Optional[str]:
    """Build the canonical authorization phrase for the current head.

    Returns None when evidence lacks a full 40-hex head SHA or when
    the controller has not recorded a PR number under the
    ``pr_number`` metadata key. The PR number used for the phrase
    is the integer that the controller stamped onto
    ``evidence.evidence_sources["pr_number"]``; the controller
    always records it as ``"fetched"`` (it is metadata, not evidence).
    When the ledger entry is anything else, the controller has not
    provided a PR number and the phrase builder returns None so gate
    4 fails closed.
    """
    pr_meta = evidence.evidence_sources.get("pr_number")
    if pr_meta != "fetched":
        return None
    # The PR number is also passed through evidence_sources under a
    # private prefix so the build helper can recover the integer form.
    # The controller stamps the integer onto evidence via the
    # ``_pr_number_int`` attribute; when absent, gate 4 fails closed.
    pr_int = getattr(evidence, "_pr_number_int", None)
    if not isinstance(pr_int, int):
        return None
    if not is_canonical_head_sha(evidence.head_sha):
        return None
    return (
        f"I confirm merge PR #{pr_int} at {evidence.head_sha} "
        f"using final-head reviewed clean state."
    )


__all__ = [
    "ReadinessEvidence",
    "ReadinessReason",
    "ReadinessVerdict",
    "REASON_PR_NOT_OPEN",
    "REASON_PR_IS_DRAFT",
    "REASON_PR_NOT_MERGEABLE",
    "REASON_PHRASE_MISMATCH",
    "REASON_CHANGED_FILES_MISSING",
    "REASON_SCOPE_UNKNOWN",
    "REASON_SCOPE_VIOLATION",
    "REASON_FORBIDDEN_FILE_TOUCHED",
    "REASON_CI_MISSING",
    "REASON_CI_FAILED",
    "REASON_CI_PENDING",
    "REASON_CODEX_MISSING",
    "REASON_CODEX_STALE",
    "REASON_CODEX_FAILED",
    "REASON_CODEX_CLEAN_MISSING",
    "REASON_REVIEWS_INCOMPLETE",
    "REASON_THREAD_INVENTORY_FAILED",
    "REASON_UNRESOLVED_THREAD",
    "REASON_EVIDENCE_MISSING",
    "RETRY_REQUIRED_REASONS",
    "CODEX_CLEAN_VERDICTS",
    "GATE_PR_OPEN",
    "GATE_PR_NON_DRAFT",
    "GATE_PR_MERGEABLE",
    "GATE_AUTHORIZATION_PHRASE",
    "GATE_CHANGED_FILES_FETCHED",
    "GATE_SCOPE_CLEAN",
    "GATE_CI_PRESENT",
    "GATE_CODEX_EVIDENCE",
    "GATE_REVIEWS_AND_COMMENTS",
    "GATE_THREAD_INVENTORY",
    "GATE_UNRESOLVED_THREADS",
    "GATE_NO_MISSING_EVIDENCE",
    "ALL_GATES",
    "MACHINE_GATES",
    "evaluate_readiness",
    "evaluate_machine_readiness",
    "evaluate_authorization",
    "is_codex_clean_verdict",
    "classify_thread_actor",
    "partition_unresolved_threads",
    "is_eligible_for_bot_resolution",
    "is_canonical_head_sha",
    "normalize_mergeable",
]
