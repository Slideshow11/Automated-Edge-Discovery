"""Hypothesis declaration layer for AED.

This package provides the theory-first hypothesis schema: HypothesisSpec
and its supporting types. It is purely declarative — no execution,
no candidate generation, no registry logic lives here.
"""

from .spec import (
    AssetClass,
    HypothesisSpec,
    HypothesisStatus,
    KillCriterion,
    ParameterConstraint,
    SourceType,
    StrategyFamily,
    ValidationPlan,
)

__all__ = [
    "AssetClass",
    "HypothesisSpec",
    "HypothesisStatus",
    "KillCriterion",
    "ParameterConstraint",
    "SourceType",
    "StrategyFamily",
    "ValidationPlan",
]
