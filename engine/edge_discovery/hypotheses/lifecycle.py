"""Lifecycle orchestration for hypothesis batch runs.

Coordinates hypothesis registration, batch execution, and registry status
updates in a single call: HypothesisSpec -> HypothesisRegistry ->
run_candidate_batch -> registry status update.

No promotion or falsification logic lives here — execution outcome is
recorded but not interpreted.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import get_config
from .batch import BatchResult, run_candidate_batch
from .registry import HypothesisRegistry
from .spec import HypothesisSpec


# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------


@dataclass
class LifecycleResult:
    """Result of a lifecycle run.

    Fields
    ------
    hypothesis_id : str
    initial_status : str
        Status when the lifecycle call started.
    final_status : str
        Status when the lifecycle call finished.
    batch_result : BatchResult | None
        The batch result if execution was attempted (including dry_run).
        None if registration alone failed.
    registry_path : str
        Resolved path to the registry file.
    error : str | None
        Top-level error message. Set when registration or execution
        failed entirely.
    """

    hypothesis_id: str
    initial_status: str
    final_status: str
    batch_result: BatchResult | None
    registry_path: str
    error: str | None


# ---------------------------------------------------------------------------
# Lifecycle orchestration
# ---------------------------------------------------------------------------


def register_and_run_batch(
    hypothesis: HypothesisSpec,
    *,
    registry_path: str | None = None,
    options_db_path: str,
    preearn_repo_path: str,
    ledger_path: str | None = None,
    output_dir: str = ".wfa/preearn_batch",
    max_candidates: int | None = None,
    dry_run: bool = True,
    timeout: float | None = 600.0,
) -> LifecycleResult:
    """Register a hypothesis and run a candidate batch.

    Lifecycle steps
    ---------------
    1. Resolve registry path.
    2. Determine initial status from registry (or None if new).
    3. Register or validate the hypothesis.
    4. Transition draft -> registered if new.
    5. Transition registered -> testing before a non-dry-run batch.
    6. Run run_candidate_batch(...).
    7. Set final_status:
         - registered for dry_run (no evaluation occurred)
         - testing for non-dry-run
    8. Return LifecycleResult.

    Execution outcome (success / partial / error) is recorded in the
    batch result but does NOT drive hypothesis promotion or falsification
    in this PR.

    Parameters
    ----------
    hypothesis : HypothesisSpec
    registry_path : str | None
        Path to JSONL registry. None uses get_config()["hypothesis_registry_path"].
    options_db_path, preearn_repo_path, ledger_path, output_dir,
    max_candidates, dry_run, timeout
        Passed directly to run_candidate_batch().

    Returns
    -------
    LifecycleResult

    Raises
    ------
    ValueError
        If the hypothesis_id is already registered with a conflicting spec.
    """

    # 1. Resolve registry path
    if registry_path is None:
        registry_path = get_config()["hypothesis_registry_path"]

    registry = HypothesisRegistry(registry_path)
    resolved_path = str(registry._path)

    # 2. Determine initial status
    existing = registry.get(hypothesis.hypothesis_id)
    if existing is not None:
        initial_status = existing.status.value
    else:
        initial_status = hypothesis.status.value

    # 3-4. Register or validate
    if existing is None:
        # Register new hypothesis
        registry.register(hypothesis)
        if hypothesis.status.value == "draft":
            registry.update_status(hypothesis.hypothesis_id, "registered")
    else:
        # Existing — must be identical spec (ignoring status, which is lifecycle state).
        # Use JSON-round-trip normalization to handle tuple<->list conversions.
        def _spec_dict(spec: HypothesisSpec) -> dict:
            """Return a spec dict with tuples normalised to lists for comparison."""
            import json
            raw = spec.to_dict()
            # Remove status — it is lifecycle state, not spec content
            del raw["status"]
            # Normalise tuples to lists so JSON-serialised forms compare equal
            normalised = json.loads(
                json.dumps(raw, sort_keys=True),
                object_hook=lambda d: {k: list(v) if isinstance(v, tuple) else v for k, v in d.items()},
            )
            return normalised

        if _spec_dict(existing) != _spec_dict(hypothesis):
            raise ValueError(
                f"Hypothesis {hypothesis.hypothesis_id!r} is already registered "
                f"with a different spec. Refusing to overwrite."
            )
        # Identical — reuse existing record

    # 5. Pre-batch status transition (non-dry-run only)
    current = registry.get(hypothesis.hypothesis_id)
    assert current is not None
    if not dry_run and current.status.value in ("draft", "registered"):
        registry.update_status(hypothesis.hypothesis_id, "testing")

    # 6. Run batch
    batch_result: BatchResult | None = None
    error: str | None = None
    try:
        batch_result = run_candidate_batch(
            hypothesis,
            options_db_path=options_db_path,
            preearn_repo_path=preearn_repo_path,
            ledger_path=ledger_path,
            output_dir=output_dir,
            max_candidates=max_candidates,
            dry_run=dry_run,
            timeout=timeout,
        )
    except Exception:
        # Re-raise without swallowing — lifecycle failure is fatal.
        raise

    # 7. Determine final status
    if dry_run:
        final_status = "registered"
    else:
        final_status = "testing"

    return LifecycleResult(
        hypothesis_id=hypothesis.hypothesis_id,
        initial_status=initial_status,
        final_status=final_status,
        batch_result=batch_result,
        registry_path=resolved_path,
        error=error,
    )
