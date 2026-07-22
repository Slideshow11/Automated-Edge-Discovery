#!/usr/bin/env python3
"""Impact-based test selection policy.

Implements PHASE 3 R-5:

  * explicit autocoder and AED component boundaries
    (without extracting repos);
  * deterministic, inspectable test-impact mechanism based on:
      - changed paths;
      - component manifests;
      - shared dependency paths;
      - test ownership;
      - explicit fail-closed fallback rules.

Validation tiers:

  Tier 1 — inner repair loop:
    * exact bug-reproduction tests;
    * nearest unit tests for affected modules.

  Tier 2 — cohesive batch validation:
    * complete autocoder-focused suite for autocoder changes;
    * relevant shared contract tests;
    * affected AED subsystem tests only when AED code is touched.

  Tier 3 — final candidate validation:
    * complete repository suite on the final candidate head
      before ready status;
    * also run the full suite immediately when the selector
      cannot classify a changed path safely or when shared
      foundational code changes.

The selected test plan and reason MUST be included in
machine-readable output.
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence


class ValidationTier(str, Enum):
    TIER_1_INNER_REPAIR = "tier_1_inner_repair"
    TIER_2_COHESIVE_BATCH = "tier_2_cohesive_batch"
    TIER_3_FINAL_CANDIDATE = "tier_3_final_candidate"


class Component(str, Enum):
    AUTOCODER = "autocoder"
    AED = "aed"
    SHARED = "shared"
    UNKNOWN = "unknown"


# Component manifests: glob → component mapping.
# The first matching glob wins. Keep order broad→narrow.
_COMPONENT_MANIFEST: List[tuple] = [
    # Autocoder (Tier 2 autocoder-focused suite).
    ("autocoder_*.py", Component.AUTOCODER),
    ("build_autocoder_*.py", Component.AUTOCODER),
    ("run_autocoder_*.py", Component.AUTOCODER),
    ("aed_continue_pr.py", Component.AUTOCODER),
    ("aed_tasker_*.py", Component.AUTOCODER),
    ("apply_temp_worktree_patch_to_branch.py", Component.AUTOCODER),
    ("aed_pr189_gate_classifier/**", Component.AUTOCODER),
    ("scripts/local/aed_continue_pr.py", Component.AUTOCODER),
    ("scripts/local/aed_tasker_*.py", Component.AUTOCODER),
    ("scripts/local/build_autocoder_*.py", Component.AUTOCODER),
    ("scripts/local/apply_temp_worktree_patch_to_branch.py", Component.AUTOCODER),
    ("tests/test_autocoder_*.py", Component.AUTOCODER),
    ("tests/test_aed_continue_pr.py", Component.AUTOCODER),
    ("tests/test_aed_tasker_*.py", Component.AUTOCODER),
    ("tests/test_apply_temp_worktree_*.py", Component.AUTOCODER),
    # AED.
    ("scripts/local/aed_pr.py", Component.AED),
    ("scripts/local/aed_pr_*.py", Component.AED),
    ("scripts/local/audit_*.py", Component.AED),
    ("scripts/local/codex_review_poller.py", Component.AED),
    ("scripts/local/check_pr_review_comments.py", Component.AED),
    ("scripts/local/build_merge_ready_packet.py", Component.AED),
    ("scripts/local/finalize_with_phase_ledger.py", Component.AED),
    ("scripts/local/merge_readiness_with_phase_ledger.py", Component.AED),
    ("tests/test_aed_pr.py", Component.AED),
    ("tests/test_aed_pr_*.py", Component.AED),
    ("tests/test_audit_*.py", Component.AED),
    ("tests/test_codex_review_poller.py", Component.AED),
    ("tests/test_check_pr_review_comments.py", Component.AED),
    ("tests/test_merge_*.py", Component.AED),
    # Shared foundational paths always force full validation.
    ("scripts/local/_shared_*.py", Component.SHARED),
    ("aed_policy/policy.py", Component.SHARED),
    ("docs/aed_*.md", Component.SHARED),
    # CI workflows are shared.
    (".github/workflows/*.yml", Component.SHARED),
    ("pyproject.toml", Component.SHARED),
    ("scripts/local/aed_pr_lib.py", Component.SHARED),
    ("tests/conftest.py", Component.SHARED),
]


# Tier-2 focused suites (by component).
TIER_2_SUITES: Dict[Component, List[str]] = {
    Component.AUTOCODER: [
        "tests/test_autocoder_run_controller.py",
        "tests/test_aed_continue_pr.py",
        "tests/test_aed_tasker_packet.py",
        "tests/test_apply_temp_worktree_patch_to_branch.py",
        "tests/test_bounded_command_runner.py",
        "tests/test_shared_codex_classifier.py",
        "tests/test_shared_pagination.py",
        "tests/test_shared_non_human_policy.py",
        "tests/test_shared_batching.py",
        "tests/test_shared_test_selection.py",
    ],
    Component.AED: [
        "tests/test_aed_pr.py",
        "tests/test_aed_pr_round3.py",
        "tests/test_aed_pr_round4.py",
        "tests/test_aed_pr_round5.py",
        "tests/test_aed_pr_canonical_guide.py",
        "tests/test_aed_lifecycle_states.py",
        "tests/test_aed_policy_engine.py",
        "tests/test_audit_codex_response_for_pr.py",
        "tests/test_check_pr_review_comments.py",
        "tests/test_codex_review_poller.py",
        "tests/test_merge_pr_safely.py",
        "tests/test_phase_ledger_unit.py",
        "tests/test_shared_codex_classifier.py",
        "tests/test_shared_pagination.py",
        "tests/test_shared_non_human_policy.py",
        "tests/test_shared_batching.py",
        "tests/test_shared_test_selection.py",
    ],
    Component.SHARED: ["FULL_REPOSITORY_SUITE"],
    Component.UNKNOWN: ["FULL_REPOSITORY_SUITE"],
}


@dataclass(frozen=True)
class TestPlan:
    """Machine-readable test plan."""

    tier: ValidationTier
    components: List[Component]
    selected_tests: List[str]
    requires_full_validation: bool
    selection_reason: str
    classification_failures: List[str] = field(default_factory=list)

    def to_machine_readable(self) -> Dict:
        return {
            "tier": self.tier.value,
            "components": [c.value for c in self.components],
            "selected_tests": self.selected_tests,
            "requires_full_validation": self.requires_full_validation,
            "selection_reason": self.selection_reason,
            "classification_failures": self.classification_failures,
        }


def classify_path(path: str) -> Component:
    """Classify a single changed path into a component."""
    for pattern, component in _COMPONENT_MANIFEST:
        if fnmatch.fnmatch(path, pattern):
            return component
    # Default fall-back: anything under scripts/local/ or tests/
    # that's not matched is treated as UNKNOWN → full validation.
    return Component.UNKNOWN


def classify_paths(paths: Sequence[str]) -> Dict:
    """Classify a list of changed paths.

    Returns ``{"components": [...], "failures": [...], "shared": bool}``.
    """
    components: List[Component] = []
    failures: List[str] = []
    for p in paths:
        c = classify_path(p)
        components.append(c)
        if c == Component.UNKNOWN:
            failures.append(p)
    return {
        "components": list(dict.fromkeys(components)),
        "failures": failures,
        "shared": Component.SHARED in components,
    }


def select_tests(
    *,
    changed_paths: Sequence[str],
    tier: ValidationTier,
    final_candidate: bool = False,
) -> TestPlan:
    """Select the test plan for a given tier and changed paths.

    Rules:

      * Tier 3 (final candidate) ALWAYS uses full repository
        suite;
      * Any SHARED component or any UNKNOWN path forces full
        validation;
      * Otherwise Tier 2 uses the focused suite for the
        detected component(s).
    """
    classification = classify_paths(changed_paths)
    components = classification["components"]
    failures = classification["failures"]
    is_shared = classification["shared"]
    has_unknown = bool(failures)

    if final_candidate:
        return TestPlan(
            tier=ValidationTier.TIER_3_FINAL_CANDIDATE,
            components=components,
            selected_tests=["FULL_REPOSITORY_SUITE"],
            requires_full_validation=True,
            selection_reason=(
                "Tier 3 final candidate always selects the full "
                "repository suite before ready status."
            ),
            classification_failures=failures,
        )

    if is_shared or has_unknown:
        return TestPlan(
            tier=tier,
            components=components,
            selected_tests=["FULL_REPOSITORY_SUITE"],
            requires_full_validation=True,
            selection_reason=(
                "Foundational shared code or unknown paths — "
                "full validation required."
            ),
            classification_failures=failures,
        )

    # Collect focused suites (de-duped, order preserved).
    selected: List[str] = []
    for c in components:
        for t in TIER_2_SUITES.get(c, []):
            if t not in selected and t != "FULL_REPOSITORY_SUITE":
                selected.append(t)

    return TestPlan(
        tier=tier,
        components=components,
        selected_tests=selected,
        requires_full_validation=False,
        selection_reason=(
            f"Focused Tier-{tier.value} suite for components "
            f"{[c.value for c in components]}."
        ),
        classification_failures=failures,
    )
