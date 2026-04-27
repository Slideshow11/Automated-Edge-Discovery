"""Pure read-only evaluator for WFA batch execution results.

Classifies BatchResult and preearn_candidate_batch ledger entries for
human review readiness. Produces EvaluationLabel labels only — does not
update registry, write ledger, call subprocesses, or make promotion decisions.

Labels
------
invalid_run        : Nothing ran; no execution occurred.
execution_failed   : Execution was attempted but all candidates errored.
needs_more_data    : Execution occurred but thresholds not met.
promising_for_review : Execution passed thresholds; ready for human review.

No label implies hypothesis acceptance, rejection, or any trading signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .hypotheses.batch import BatchResult
from .ledger import LedgerEntry


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class EvaluationLabel(str, Enum):
    """Execution outcome label for review-readiness classification.

    These labels describe execution quality only. No label implies
    hypothesis acceptance, rejection, or any trading recommendation.
    """

    INVALID_RUN = "invalid_run"
    EXECUTION_FAILED = "execution_failed"
    NEEDS_MORE_DATA = "needs_more_data"
    PROMISING = "promising_for_review"


@dataclass
class EvaluationResult:
    """Result of evaluating a batch execution or ledger entry.

    Fields
    ------
    label : EvaluationLabel
        High-level review-readiness classification.
    reason : str
        Human-readable explanation of the label assignment.
    metrics : dict
        Key execution metrics used in the evaluation.
    warnings : tuple[str, ...]
        Non-fatal concerns flagged during evaluation.
    source_type : str
        Always "batch_result" for direct BatchResult evaluation,
        "ledger_entry" for ledger-based evaluation.
    source_id : str
        batch_id (for batch_result) or run_id (for ledger_entry).
    hypothesis_id : str
        hypothesis_id from the evaluated BatchResult.
    """

    label: EvaluationLabel
    reason: str
    metrics: dict = field(default_factory=dict)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    source_type: str = "batch_result"
    source_id: str = ""
    hypothesis_id: str = ""


# ---------------------------------------------------------------------------
# BatchResult evaluation
# ---------------------------------------------------------------------------


def evaluate_batch_result(
    result: BatchResult,
    *,
    min_candidates: int = 1,
    min_successes: int = 1,
    max_error_rate: float = 0.5,
) -> EvaluationResult:
    """Classify a BatchResult for human review readiness.

    This function is pure — it reads ``result`` and returns a classification.
    It does not write to any file, registry, or ledger.

    Parameters
    ----------
    result : BatchResult
        The batch result to evaluate.
    min_candidates : int
        Minimum n_candidates_selected required.
        Default 1.
    min_successes : int
        Minimum n_success required.
        Default 1.
    max_error_rate : float
        Maximum acceptable n_error / n_candidates_selected ratio.
        Must be in [0.0, 1.0]. Default 0.5.

    Returns
    -------
    EvaluationResult

    Raises
    ------
    ValueError
        If ``max_error_rate`` is outside [0.0, 1.0].
    """
    if not (0.0 <= max_error_rate <= 1.0):
        raise ValueError(
            f"max_error_rate must be in [0.0, 1.0], got {max_error_rate!r}."
        )

    n_sel = result.n_candidates_selected
    n_suc = result.n_success
    n_err = result.n_error
    status = result.status

    # 1. Dry run — no execution occurred
    if status == "dry_run":
        return EvaluationResult(
            label=EvaluationLabel.NEEDS_MORE_DATA,
            reason="dry_run_no_execution: no backtest execution occurred.",
            metrics={},
            warnings=(),
            source_type="batch_result",
            source_id=result.batch_id,
            hypothesis_id=result.hypothesis_id,
        )

    # 2. No candidates selected — nothing could have run
    if n_sel == 0:
        return EvaluationResult(
            label=EvaluationLabel.INVALID_RUN,
            reason="no_candidates_selected: zero candidates were selected for execution.",
            metrics={"n_candidates_selected": 0},
            warnings=(),
            source_type="batch_result",
            source_id=result.batch_id,
            hypothesis_id=result.hypothesis_id,
        )

    # 3. All candidates errored — total execution failure
    if n_suc == 0 and n_err > 0:
        return EvaluationResult(
            label=EvaluationLabel.EXECUTION_FAILED,
            reason=f"all_candidates_errored: all {n_err} candidate(s) errored.",
            metrics={
                "n_candidates_selected": n_sel,
                "n_success": n_suc,
                "n_error": n_err,
            },
            warnings=("zero_successful_runs",),
            source_type="batch_result",
            source_id=result.batch_id,
            hypothesis_id=result.hypothesis_id,
        )

    # 4. Zero successes, zero errors — edge case (shouldn't happen with current adapter)
    if n_suc == 0 and n_err == 0:
        return EvaluationResult(
            label=EvaluationLabel.NEEDS_MORE_DATA,
            reason="no_successful_runs_and_no_errors: unclear execution state.",
            metrics={
                "n_candidates_selected": n_sel,
                "n_success": n_suc,
                "n_error": n_err,
            },
            warnings=("unusual_execution_state",),
            source_type="batch_result",
            source_id=result.batch_id,
            hypothesis_id=result.hypothesis_id,
        )

    # 5. Build shared metrics
    n_total = n_sel  # n_sel > 0 here
    error_rate = n_err / n_total
    metrics = {
        "n_candidates_selected": n_sel,
        "n_success": n_suc,
        "n_error": n_err,
        "error_rate": round(error_rate, 4),
    }
    warnings: list[str] = []

    # 6. Error rate threshold
    if error_rate > max_error_rate:
        warnings.append(f"error_rate_{round(error_rate * 100, 1)}pct_exceeds_limit")
        return EvaluationResult(
            label=EvaluationLabel.NEEDS_MORE_DATA,
            reason=(
                f"error_rate_too_high: error rate {error_rate:.1%} exceeds "
                f"threshold {max_error_rate:.1%}."
            ),
            metrics=metrics,
            warnings=tuple(warnings),
            source_type="batch_result",
            source_id=result.batch_id,
            hypothesis_id=result.hypothesis_id,
        )

    # 7. Insufficient successes
    if n_suc < min_successes:
        warnings.append("insufficient_successes")
        return EvaluationResult(
            label=EvaluationLabel.NEEDS_MORE_DATA,
            reason=(
                f"insufficient_successful_runs: only {n_suc} successful run(s); "
                f"minimum {min_successes} required."
            ),
            metrics=metrics,
            warnings=tuple(warnings),
            source_type="batch_result",
            source_id=result.batch_id,
            hypothesis_id=result.hypothesis_id,
        )

    # 8. Insufficient candidates selected
    if n_sel < min_candidates:
        warnings.append("insufficient_candidates")
        return EvaluationResult(
            label=EvaluationLabel.NEEDS_MORE_DATA,
            reason=(
                f"insufficient_candidates_selected: only {n_sel} candidate(s) "
                f"selected; minimum {min_candidates} required."
            ),
            metrics=metrics,
            warnings=tuple(warnings),
            source_type="batch_result",
            source_id=result.batch_id,
            hypothesis_id=result.hypothesis_id,
        )

    # 9. Promising for review
    return EvaluationResult(
        label=EvaluationLabel.PROMISING,
        reason=(
            f"execution_thresholds_met: {n_suc}/{n_sel} candidates succeeded "
            f"with {error_rate:.1%} error rate. Ready for human review."
        ),
        metrics=metrics,
        warnings=tuple(warnings),
        source_type="batch_result",
        source_id=result.batch_id,
        hypothesis_id=result.hypothesis_id,
    )


# ---------------------------------------------------------------------------
# LedgerEntry evaluation
# ---------------------------------------------------------------------------


def evaluate_ledger_entry(
    entry: LedgerEntry,
    *,
    min_candidates: int = 1,
    min_successes: int = 1,
    max_error_rate: float = 0.5,
) -> EvaluationResult:
    """Evaluate a preearn_candidate_batch ledger entry for review readiness.

    This function reads a previously-written LedgerEntry and returns a
    classification. It does not write anything or re-execute anything.

    Only run_type="preearn_candidate_batch" is supported. Other run_types
    return EvaluationLabel.INVALID_RUN without raising.

    Parameters
    ----------
    entry : LedgerEntry
        The ledger entry to evaluate.
    min_candidates, min_successes, max_error_rate
        Passed through to evaluate_batch_result.

    Returns
    -------
    EvaluationResult
        With source_type="ledger_entry" and source_id=entry.run_id.
    """
    if entry.run_type != "preearn_candidate_batch":
        return EvaluationResult(
            label=EvaluationLabel.INVALID_RUN,
            reason=f"unsupported_run_type: {entry.run_type!r} is not supported.",
            metrics={},
            warnings=(),
            source_type="ledger_entry",
            source_id=entry.run_id,
        )

    ms = entry.metrics_summary
    batch_result = BatchResult(
        batch_id=entry.run_id,
        hypothesis_id=entry.input_artifacts.get("hypothesis_id", ""),
        status=entry.status,
        n_candidates_generated=ms.get("n_candidates_generated", 0),
        n_candidates_selected=ms.get("n_candidates_selected", 0),
        n_success=ms.get("n_success", 0),
        n_error=ms.get("n_error", 0),
        results=(),
    )

    ev = evaluate_batch_result(
        batch_result,
        min_candidates=min_candidates,
        min_successes=min_successes,
        max_error_rate=max_error_rate,
    )
    # Override source fields to reflect ledger origin
    ev.source_type = "ledger_entry"
    ev.source_id = entry.run_id
    return ev
