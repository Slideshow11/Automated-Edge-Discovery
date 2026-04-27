"""Hypothesis declaration layer for AED.

This package provides the theory-first hypothesis schema: HypothesisSpec
and its supporting types, a JSONL registry, and a deterministic
candidate generator.
"""

from .generate import generate_candidates
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
    "generate_candidates",
]
