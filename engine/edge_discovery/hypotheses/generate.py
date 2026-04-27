"""Pure deterministic generator: HypothesisSpec -> pre-earnings CandidateSpec.

This module is purely transformational — no I/O, no subprocess calls,
no state. It translates a theory-first HypothesisSpec into a sorted
tuple of CandidateSpec objects ready for backtesting.
"""

from __future__ import annotations

import itertools
from typing import Any

from ..adapters.preearn_options import CandidateSpec
from .spec import HypothesisSpec, ParameterConstraint, StrategyFamily


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _constraint_by_name(spec: HypothesisSpec, name: str) -> ParameterConstraint:
    """Return the ParameterConstraint with the given name, or raise."""
    for c in spec.candidate_constraints:
        if c.name == name:
            return c
    raise ValueError(f"Required constraint {name!r} not found in hypothesis.")


def _explicit_values(constraint: ParameterConstraint) -> tuple[Any, ...]:
    """Return explicit values from a constraint.

    Raises ValueError if the constraint uses range bounds without
    explicit values (v1 requires explicit enumeration).
    """
    if constraint.values:
        return constraint.values
    raise ValueError(
        f"Constraint {constraint.name!r} uses range bounds without explicit values. "
        f"v1 generation requires explicit values."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_candidates(
    hypothesis: HypothesisSpec,
    *,
    options_db_path: str,
    preearn_repo_path: str,
    fill_policy: str = "MID",
    spread_penalty_k: float = 0.5,
    contract_multiplier: float = 100.0,
    output_dir: str = ".wfa/preearn",
) -> tuple[CandidateSpec, ...]:
    """Generate pre-earnings CandidateSpec objects from a HypothesisSpec.

    Generates the Cartesian product of ``entry_dpe``, ``delta_target``, and
    ``expiry_rank`` constraint values declared in the hypothesis.

    Parameters
    ----------
    hypothesis : HypothesisSpec
        Must have ``strategy_family == StrategyFamily.preearn_options``.
    options_db_path : str
        Required. Absolute path to the options SQLite database.
    preearn_repo_path : str
        Required. Absolute path to the pre-earnings engine_linux_main checkout.
    fill_policy : str
        Fill policy passed to each CandidateSpec. Default ``"MID"``.
    spread_penalty_k : float
        Spread penalty coefficient passed to each CandidateSpec.
    contract_multiplier : float
        Contract multiplier passed to each CandidateSpec.
    output_dir : str
        Output directory for per-candidate artifacts.

    Returns
    -------
    tuple[CandidateSpec, ...]
        Sorted by ``(entry_dpe, delta_target, expiry_rank)`` ascending.

    Raises
    ------
    ValueError
        If ``strategy_family`` is not ``preearn_options``.
    ValueError
        If a required constraint (``entry_dpe``, ``delta_target``,
        ``expiry_rank``) is missing from the hypothesis.
    ValueError
        If a required constraint uses ``min_value``/``max_value`` bounds
        without providing explicit ``values`` (v1 limitation).
    """
    if hypothesis.strategy_family != StrategyFamily.preearn_options:
        raise ValueError(
            f"Unsupported strategy family: {hypothesis.strategy_family.value!r}. "
            f"v1 generator only supports {StrategyFamily.preearn_options.value!r}."
        )

    entry_dpe_vals = _explicit_values(_constraint_by_name(hypothesis, "entry_dpe"))
    delta_vals     = _explicit_values(_constraint_by_name(hypothesis, "delta_target"))
    rank_vals      = _explicit_values(_constraint_by_name(hypothesis, "expiry_rank"))

    candidates: list[CandidateSpec] = []
    for dpe, dtl, rk in itertools.product(entry_dpe_vals, delta_vals, rank_vals):
        candidates.append(
            CandidateSpec(
                entry_dpe=int(dpe),
                delta_target=float(dtl),
                expiry_rank=int(rk),
                options_db_path=options_db_path,
                preearn_repo_path=preearn_repo_path,
                fill_policy=fill_policy,
                spread_penalty_k=spread_penalty_k,
                contract_multiplier=contract_multiplier,
                output_dir=output_dir,
            )
        )

    return tuple(sorted(candidates, key=lambda c: (c.entry_dpe, c.delta_target, c.expiry_rank)))
