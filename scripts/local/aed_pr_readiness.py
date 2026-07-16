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

Gates enforced (all 12)
------------------------

Every gate is checked on the exact authorized head, freshly fetched at
the moment of evaluation. A single failure flips the verdict to
``READY=False`` and emits a :class:`ReadinessReason`. ``aed_pr merge``
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
from typing import Any, Dict, List, Optional


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
    ready: bool
    reasons: List[ReadinessReason]
    gates_passed: List[str]
    gates_failed: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ready": self.ready,
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


def is_canonical_head_sha(sha: Optional[str]) -> bool:
    """True iff sha is exactly 40 lowercase hex characters."""
    if not isinstance(sha, str) or len(sha) != 40:
        return False
    return all(c in "0123456789abcdef" for c in sha)


# -----------------------------------------------------------------------------
# Evaluator
# -----------------------------------------------------------------------------

def evaluate_readiness(
    evidence: ReadinessEvidence,
    expected_canonical_phrase: Optional[str] = None,
) -> ReadinessVerdict:
    """Evaluate the 12-gate readiness verdict on the supplied evidence.

    ``expected_canonical_phrase`` is the phrase the operator spoke;
    if not supplied, ``evidence.authorization_phrase`` is used. The
    evaluator never accepts a phrase whose embedded SHA does not
    byte-exactly match ``evidence.head_sha``. A phrase is required to
    pass gate 4; if no phrase is present the gate fails closed.
    """
    reasons: List[ReadinessReason] = []
    passed: List[str] = []
    failed: List[str] = []

    head_sha = evidence.head_sha
    if not is_canonical_head_sha(head_sha):
        # Without a canonical 40-hex head SHA no gate can be evaluated
        # against the exact authorized head. Every downstream gate fails.
        reasons.append(ReadinessReason(
            code=REASON_EVIDENCE_MISSING,
            detail=f"head_sha must be exactly 40 lowercase hex chars; got {head_sha!r}",
            gate=GATE_NO_MISSING_EVIDENCE,
        ))
        failed.extend(ALL_GATES)
        return ReadinessVerdict(False, reasons, passed, failed)

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

    # 4. Authorization phrase byte-exact against current head
    phrase = evidence.authorization_phrase or expected_canonical_phrase
    expected = (
        "I confirm merge PR #"
        + "?"  # placeholder, replaced below
    )
    # We rely on the caller to have already built the canonical phrase;
    # the evaluator only compares byte equality.
    canonical_phrase = _build_canonical_phrase(evidence)
    if phrase and isinstance(phrase, str) and phrase == canonical_phrase:
        passed.append(GATE_AUTHORIZATION_PHRASE)
    else:
        failed.append(GATE_AUTHORIZATION_PHRASE)
        reasons.append(ReadinessReason(
            code=REASON_PHRASE_MISMATCH,
            detail=(
                "authorization phrase does NOT byte-match the canonical "
                "phrase for the current head SHA"
            ),
            gate=GATE_AUTHORIZATION_PHRASE,
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

    # 6. Changed-file scope exists and is clean
    if evidence.scope_clean is True:
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

    ready = len(failed) == 0
    return ReadinessVerdict(ready, reasons, passed, failed)


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
    "evaluate_readiness",
    "is_codex_clean_verdict",
    "classify_thread_actor",
    "partition_unresolved_threads",
    "is_canonical_head_sha",
]
