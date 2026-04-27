"""Theory-first hypothesis schema for AED.

Provides HypothesisSpec and supporting types for declaring market
hypotheses before candidate generation or backtesting.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SourceType(str, Enum):
    """Origin of the hypothesis."""

    academic_paper = "academic_paper"
    empirical_observation = "empirical_observation"
    market_mechanic = "market_mechanic"
    internal_research = "internal_research"
    llm_generated = "llm_generated"


class AssetClass(str, Enum):
    """Asset class the hypothesis operates on."""

    equity = "equity"
    equity_options = "equity_options"
    index_options = "index_options"
    futures = "futures"
    crypto = "crypto"
    macro = "macro"


class StrategyFamily(str, Enum):
    """Strategy family the hypothesis belongs to."""

    preearn_options = "preearn_options"
    momentum = "momentum"
    mean_reversion = "mean_reversion"
    news_sentiment = "news_sentiment"
    volatility = "volatility"
    execution_cost = "execution_cost"


class HypothesisStatus(str, Enum):
    """Lifecycle status of a hypothesis."""

    draft = "draft"
    registered = "registered"
    testing = "testing"
    accepted = "accepted"
    rejected = "rejected"
    killed = "killed"


_VALID_KILL_OPS = {"lt", "lte", "gt", "gte", "eq", "neq"}


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParameterConstraint:
    """Constraint on a single strategy parameter.

    A parameter may be specified as an explicit list of ``values``,
    or as a numeric range with optional ``min_value``, ``max_value``,
    and ``step``.  At least one of ``values`` or ``min_value`` must be
    provided.
    """

    name: str
    values: tuple[Any, ...] = ()
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None

    def __post_init__(self) -> None:
        has_values = bool(self.values)
        has_bounds = self.min_value is not None or self.max_value is not None
        if not has_values and not has_bounds:
            raise ValueError(
                f"ParameterConstraint '{self.name}' must have either "
                f"'values' or numeric bounds (min_value/max_value)."
            )
        if self.min_value is not None and self.max_value is not None:
            if self.min_value > self.max_value:
                raise ValueError(
                    f"ParameterConstraint '{self.name}': "
                    f"min_value ({self.min_value}) > max_value ({self.max_value})."
                )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ParameterConstraint:
        return cls(**data)


@dataclass(frozen=True)
class KillCriterion:
    """A metric-based kill switch for a hypothesis.

    When a backtest result is evaluated, each KillCriterion is checked:
    the ``metric`` value from the result dict is compared against
    ``threshold`` using the operator ``op``.  If the condition holds,
    the hypothesis is marked ``killed``.
    """

    metric: str
    op: str
    threshold: float
    reason: str = ""

    def __post_init__(self) -> None:
        if self.op not in _VALID_KILL_OPS:
            raise ValueError(
                f"Invalid kill criterion op {self.op!r}. "
                f"Must be one of: {sorted(_VALID_KILL_OPS)}."
            )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> KillCriterion:
        return cls(**data)


@dataclass(frozen=True)
class ValidationPlan:
    """Validation methodology for a hypothesis.

    Describes how the hypothesis should be tested (e.g. CPCV, walk-forward)
    and the minimum sample size required before a result is considered
    meaningful.
    """

    methods: tuple[str, ...]
    holdout_required: bool = True
    min_trades: int | None = None
    min_years: int | None = None
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ValidationPlan:
        return cls(**data)


# ---------------------------------------------------------------------------
# HypothesisSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HypothesisSpec:
    """Theory-first declaration of a market hypothesis.

    A HypothesisSpec captures the *intent* and *design constraints* of a
    candidate family before any candidate is generated or backtested.
    It is immutable, hashable, and serializable to JSON.

    Parameters
    ----------
    hypothesis_id : str
        Stable identifier.  Format is free-form but should be unique
        within the project (e.g. ``"preearn-dpe2-delta50-v1"``).
    version : str
        Version string for this hypothesis (e.g. ``"1.0.0"``).
    source_type : SourceType
        Where the hypothesis originated.
    source_reference : str
        Citation, URL, or ``"internal"``.
    market_mechanism : str
        The economic mechanism driving the expected effect
        (e.g. ``"IV crush"``, ``"delta hedging pressure"``).
    expected_effect : str
        Human-readable description of the predicted outcome.
    asset_class : AssetClass
        Asset class the hypothesis operates on.
    strategy_family : StrategyFamily
        High-level strategy family.
    required_data : tuple[str, ...]
        Data sources required to test this hypothesis
        (e.g. ``"options_db"``, ``"short_interest"``).
    candidate_constraints : tuple[ParameterConstraint, ...]
        Parameter space to explore for this hypothesis.
    validation_plan : ValidationPlan
        How the hypothesis should be validated.
    failure_modes : tuple[str, ...]
        Known ways the hypothesis can fail
        (e.g. ``"low_volume_filter"``, ``"wide_spread_filter"``).
    kill_criteria : tuple[KillCriterion, ...]
        Metric-based kill switches.
    status : HypothesisStatus, default HypothesisStatus.draft
        Current lifecycle status.
    notes : str, default ""
        Free-text audit trail.
    """

    hypothesis_id: str
    version: str
    source_type: SourceType
    source_reference: str
    market_mechanism: str
    expected_effect: str
    asset_class: AssetClass
    strategy_family: StrategyFamily
    required_data: tuple[str, ...]
    candidate_constraints: tuple[ParameterConstraint, ...]
    validation_plan: ValidationPlan
    failure_modes: tuple[str, ...]
    kill_criteria: tuple[KillCriterion, ...]
    status: HypothesisStatus = HypothesisStatus.draft
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.hypothesis_id or not self.hypothesis_id.strip():
            raise ValueError("hypothesis_id must be non-empty.")
        if not self.version or not self.version.strip():
            raise ValueError("version must be non-empty.")
        if not self.market_mechanism or not self.market_mechanism.strip():
            raise ValueError("market_mechanism must be non-empty.")
        if not self.expected_effect or not self.expected_effect.strip():
            raise ValueError("expected_effect must be non-empty.")
        if not self.required_data:
            raise ValueError("required_data must not be empty.")
        if not self.candidate_constraints:
            raise ValueError("candidate_constraints must not be empty.")

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a plain dict representation."""
        return {
            "hypothesis_id": self.hypothesis_id,
            "version": self.version,
            "source_type": self.source_type.value,
            "source_reference": self.source_reference,
            "market_mechanism": self.market_mechanism,
            "expected_effect": self.expected_effect,
            "asset_class": self.asset_class.value,
            "strategy_family": self.strategy_family.value,
            "required_data": list(self.required_data),
            "candidate_constraints": [c.to_dict() for c in self.candidate_constraints],
            "validation_plan": self.validation_plan.to_dict(),
            "failure_modes": list(self.failure_modes),
            "kill_criteria": [k.to_dict() for k in self.kill_criteria],
            "status": self.status.value,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> HypothesisSpec:
        """Reconstruct from a plain dict."""
        return cls(
            hypothesis_id=data["hypothesis_id"],
            version=data["version"],
            source_type=SourceType(data["source_type"]),
            source_reference=data["source_reference"],
            market_mechanism=data["market_mechanism"],
            expected_effect=data["expected_effect"],
            asset_class=AssetClass(data["asset_class"]),
            strategy_family=StrategyFamily(data["strategy_family"]),
            required_data=tuple(data["required_data"]),
            candidate_constraints=tuple(
                ParameterConstraint.from_dict(c) for c in data["candidate_constraints"]
            ),
            validation_plan=ValidationPlan.from_dict(data["validation_plan"]),
            failure_modes=tuple(data["failure_modes"]),
            kill_criteria=tuple(KillCriterion.from_dict(k) for k in data["kill_criteria"]),
            status=HypothesisStatus(data.get("status", "draft")),
            notes=data.get("notes", ""),
        )

    def to_json(self) -> str:
        """Return a canonical JSON string."""
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=True)

    @classmethod
    def from_json(cls, text: str) -> HypothesisSpec:
        """Reconstruct from JSON."""
        return cls.from_dict(json.loads(text))

    def stable_hash(self) -> str:
        """Return a 16-character SHA-256 hash of the canonical JSON.

        The hash is deterministic: identical HypothesisSpec objects always
        produce the same hash regardless of field ordering.
        """
        return hashlib.sha256(self.to_json().encode()).hexdigest()[:16]
