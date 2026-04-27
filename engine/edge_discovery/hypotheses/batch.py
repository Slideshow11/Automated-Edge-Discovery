"""Pre-earnings candidate batch runner.

Orchestrates HypothesisSpec -> CandidateSpec generation -> execution via
the existing preearn_options adapter -> batch summary JSON artifact.

This module is stateful but not persistent — it coordinates the execution
path without writing its own ledger entries (per-candidate entries are
written by the adapter).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..adapters.preearn_options import (
    CandidateSpec,
    PreearnResult,
    candidate_id,
    config_hash,
    run_preearn_backtest,
)
from .generate import generate_candidates
from .spec import HypothesisSpec


# --------------------------------------------------------------------------
# Batch result schema
# --------------------------------------------------------------------------


@dataclass
class BatchResult:
    """Result of a batch run.

    Fields
    ------
    batch_id : str
        Unique identifier. Format: ``batch_{timestamp}_{short_uuid}``.
    hypothesis_id : str
        The ``hypothesis_id`` from the input ``HypothesisSpec``.
    status : str
        One of: ``dry_run``, ``success``, ``partial``, ``error``.
    n_candidates_generated : int
        Number of candidates produced by the generator, before slicing
        to ``max_candidates``.
    n_candidates_selected : int
        Number of candidates that were actually selected for execution,
        after applying ``max_candidates``.
    n_success : int
        Count of candidates where the adapter returned
        ``PreearnResult.status == "success"``.
    n_error : int
        Count of candidates where the adapter raised or returned an
        error result.
    results : tuple[PreearnResult, ...]
        Per-candidate results. Empty when ``dry_run=True``.
    output_artifacts : dict
        Mapping of artifact name to absolute path.
        Always contains at least ``"batch_summary_json"``.
    error : str or None
        Top-level error message. Set when generation failed entirely
        (e.g. unsupported strategy family) or when all selected
        candidates errored.
    started_at : str
        ISO8601 UTC timestamp recorded at the start of the batch.
    completed_at : str
        ISO8601 UTC timestamp recorded when the batch finished.
    """

    batch_id: str
    hypothesis_id: str
    status: str
    n_candidates_generated: int
    n_candidates_selected: int
    n_success: int
    n_error: int
    results: tuple[PreearnResult, ...] = field(default_factory=tuple)
    output_artifacts: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict representation."""
        return {
            "batch_id": self.batch_id,
            "hypothesis_id": self.hypothesis_id,
            "status": self.status,
            "n_candidates_generated": self.n_candidates_generated,
            "n_candidates_selected": self.n_candidates_selected,
            "n_success": self.n_success,
            "n_error": self.n_error,
            "results": [asdict(r) for r in self.results],
            "output_artifacts": self.output_artifacts,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def now_utc() -> str:
    """Return current UTC time as an ISO8601 string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def run_candidate_batch(
    hypothesis: HypothesisSpec,
    *,
    options_db_path: str,
    preearn_repo_path: str,
    ledger_path: str | None = None,
    output_dir: str = ".wfa/preearn_batch",
    max_candidates: int | None = None,
    dry_run: bool = False,
    timeout: float | None = 600.0,
    fill_policy: str = "MID",
    spread_penalty_k: float = 0.5,
    contract_multiplier: float = 100.0,
) -> BatchResult:
    """Run a batch of pre-earnings candidates from a HypothesisSpec.

    Steps
    -----
    1. Generate candidates from ``hypothesis`` via ``generate_candidates()``.
    2. Record ``n_candidates_generated``.
    3. Slice to ``max_candidates`` if provided; record ``n_candidates_selected``.
    4. If ``dry_run=True``: return immediately with a ``dry_run`` status
       and no adapter calls.
    5. If ``dry_run=False``: call ``run_preearn_backtest`` sequentially for
       each selected candidate, catching exceptions and synthesising error
       results for failures.
    6. Write a batch summary JSON to ``{output_dir}/batch_{batch_id}.json``.
    7. Return a ``BatchResult``.

    Per-candidate ledger entries are written by the adapter itself.
    This function does not write a batch-level ledger entry.

    Parameters
    ----------
    hypothesis : HypothesisSpec
        The hypothesis to generate candidates from.
    options_db_path : str
        Required. Path to the options SQLite database.
    preearn_repo_path : str
        Required. Path to the pre-earnings engine checkout.
    ledger_path : str | None
        Passed to each ``run_preearn_backtest`` call.
        None uses the adapter's default (via ``get_config``).
    output_dir : str
        Directory for batch summary JSON. Created if it does not exist.
    max_candidates : int | None
        After generation, limit execution to the first N candidates
        (already sorted by entry_dpe, delta_target, expiry_rank).
    dry_run : bool
        If True, generate candidates and write the summary JSON without
        executing any backtests.
    timeout : float | None
        Per-candidate subprocess timeout in seconds.
        Passed to ``run_preearn_backtest``. None uses the adapter default.
    fill_policy, spread_penalty_k, contract_multiplier
        Passed to ``generate_candidates()``.

    Returns
    -------
    BatchResult

    Raises
    ------
    ValueError
        Propagated from ``generate_candidates()`` when the strategy family
        is unsupported or a required constraint is missing.
    """
    started_at = now_utc()
    batch_id = f"batch_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"

    # ── Step 1: generate candidates ──────────────────────────────────────
    try:
        all_candidates: tuple[CandidateSpec, ...] = generate_candidates(
            hypothesis,
            options_db_path=options_db_path,
            preearn_repo_path=preearn_repo_path,
            fill_policy=fill_policy,
            spread_penalty_k=spread_penalty_k,
            contract_multiplier=contract_multiplier,
            output_dir=output_dir,
        )
    except Exception as exc:
        completed_at = now_utc()
        result = BatchResult(
            batch_id=batch_id,
            hypothesis_id=hypothesis.hypothesis_id,
            status="error",
            n_candidates_generated=0,
            n_candidates_selected=0,
            n_success=0,
            n_error=0,
            results=(),
            output_artifacts={},
            error=str(exc)[:500],
            started_at=started_at,
            completed_at=completed_at,
        )
        _write_summary(result, output_dir)
        return result

    n_generated = len(all_candidates)

    # ── Step 2: apply max_candidates slice ───────────────────────────────
    selected = all_candidates if max_candidates is None else all_candidates[:max_candidates]
    n_selected = len(selected)

    # ── Step 3: dry-run path ─────────────────────────────────────────────
    if dry_run:
        completed_at = now_utc()
        result = BatchResult(
            batch_id=batch_id,
            hypothesis_id=hypothesis.hypothesis_id,
            status="dry_run",
            n_candidates_generated=n_generated,
            n_candidates_selected=n_selected,
            n_success=0,
            n_error=0,
            results=(),
            output_artifacts={},
            error=None,
            started_at=started_at,
            completed_at=completed_at,
        )
        _write_summary(result, output_dir)
        result.output_artifacts["batch_summary_json"] = str(
            Path(output_dir) / f"batch_{batch_id}.json"
        )
        return result

    # ── Step 4: execute candidates sequentially ─────────────────────────
    results_list: list[PreearnResult] = []
    for spec in selected:
        try:
            res = run_preearn_backtest(
                spec,
                ledger_path=ledger_path,
                timeout=timeout,
            )
            results_list.append(res)
        except Exception as exc:  # noqa: BLE001
            results_list.append(
                PreearnResult(
                    run_id=f"err_{int(time.time() * 1000)}",
                    candidate_id=candidate_id(spec),
                    status="error",
                    config_hash="",
                    git_commit=None,
                    command="",
                    repo_path=preearn_repo_path,
                    output_artifacts={},
                    metrics_summary={},
                    error=str(exc)[:500],
                )
            )

    n_success = sum(1 for r in results_list if r.status == "success")
    n_error = len(results_list) - n_success

    if n_success == len(results_list):
        status = "success"
        top_error: str | None = None
    elif n_success > 0:
        status = "partial"
        top_error = None
    else:
        status = "error"
        errors = [r.error for r in results_list if r.error]
        top_error = "; ".join(errors[:3]) if errors else None

    completed_at = now_utc()
    result = BatchResult(
        batch_id=batch_id,
        hypothesis_id=hypothesis.hypothesis_id,
        status=status,
        n_candidates_generated=n_generated,
        n_candidates_selected=n_selected,
        n_success=n_success,
        n_error=n_error,
        results=tuple(results_list),
        output_artifacts={},
        error=top_error,
        started_at=started_at,
        completed_at=completed_at,
    )
    _write_summary(result, output_dir)
    result.output_artifacts["batch_summary_json"] = str(
        Path(output_dir) / f"batch_{batch_id}.json"
    )
    return result


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------


def _write_summary(result: BatchResult, output_dir: str) -> None:
    """Write batch summary JSON to output_dir."""
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    summary_path = path / f"batch_{result.batch_id}.json"
    summary_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
