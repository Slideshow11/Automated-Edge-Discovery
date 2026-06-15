"""AED policy engine skeleton (v1).

Pure policy logic for the AED governance stack. The skeleton
converts the canonical 30-rule inventory at
``docs/governance/aed_rules_inventory.md`` into importable Python
decisions. It is intentionally a pure-logic library:

- It does not run shell commands.
- It does not call the GitHub API.
- It does not mutate the filesystem, the network, or any
  external service.

Every input is passed in via :class:`AEDRunState`; every output
is a structured :class:`AEDDecision`. The OpenHands plugin, the
Humphry command bridge, and the safe tool wrappers come in later
PRs and will consult this engine.
"""
from __future__ import annotations

from .action_types import AEDActionType
from .decisions import AEDDecision, AEDDecisionCode
from .policy import evaluate_action
from .reporting import decision_to_paragraph, missing_evidence, summarize_denied
from .run_state import AEDRunState

__all__ = [
    "AEDActionType",
    "AEDDecision",
    "AEDDecisionCode",
    "AEDRunState",
    "decision_to_paragraph",
    "evaluate_action",
    "missing_evidence",
    "summarize_denied",
]
