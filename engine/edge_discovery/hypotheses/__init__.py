"""Hypothesis declaration layer for AED.

This package provides the theory-first hypothesis schema: HypothesisSpec
and its supporting types, a JSONL registry, a deterministic candidate
generator, a sequential batch runner, and lifecycle orchestration.
"""

from .batch import BatchResult, run_candidate_batch
from .generate import generate_candidates
from .lifecycle import LifecycleResult, register_and_run_batch
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
    "LifecycleResult",
    "ParameterConstraint",
    "SourceType",
    "StrategyFamily",
    "ValidationPlan",
    "generate_candidates",
    "register_and_run_batch",
    "run_candidate_batch",
]
