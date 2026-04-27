"""Hypothesis declaration layer for AED.

This package provides the theory-first hypothesis schema: HypothesisSpec
and its supporting types, a JSONL registry, a deterministic candidate
generator, and a sequential batch runner.
"""

from .batch import BatchResult, run_candidate_batch
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
    "BatchResult",
    "HypothesisSpec",
    "HypothesisStatus",
    "KillCriterion",
    "ParameterConstraint",
    "SourceType",
    "StrategyFamily",
    "ValidationPlan",
    "generate_candidates",
    "run_candidate_batch",
]
