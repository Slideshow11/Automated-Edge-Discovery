"""Decision model for the AED policy engine.

Decisions are pure data. Each :class:`AEDDecision` carries:

- the allow/deny verdict
- a stable decision code (so callers can branch on it)
- a human-readable reason
- the required evidence fields (when the decision is conditional)
- the rule IDs that matched (so the decision is auditable)

This module is stdlib-only and importable in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


class AEDDecisionCode(str, Enum):
    """Stable decision codes returned by the policy engine.

    Codes beginning with ``REQUIRE_`` mean the action is denied
    until the caller supplies the named evidence; supplying it
    may flip the decision to allow. ``DENY`` and ``HOLD`` are
    hard denials that the caller cannot override.
    """

    ALLOW = "ALLOW"
    DENY = "DENY"
    HOLD = "HOLD"
    REQUIRE_EXPLICIT_AUTHORIZATION = "REQUIRE_EXPLICIT_AUTHORIZATION"
    REQUIRE_EXACT_HEAD_AUTHORIZATION = "REQUIRE_EXACT_HEAD_AUTHORIZATION"
    REQUIRE_THREAD_LIST_AUTHORIZATION = "REQUIRE_THREAD_LIST_AUTHORIZATION"
    REQUIRE_APPEND_ONLY_AUDIT = "REQUIRE_APPEND_ONLY_AUDIT"
    REQUIRE_CLEAN_MERGE_STATE = "REQUIRE_CLEAN_MERGE_STATE"
    REQUIRE_CLEAN_CI = "REQUIRE_CLEAN_CI"
    REQUIRE_CLEAN_SCOPE = "REQUIRE_CLEAN_SCOPE"
    REQUIRE_ISOLATED_WORKSPACE = "REQUIRE_ISOLATED_WORKSPACE"
    REQUIRE_NO_PRIMARY_MUTATION = "REQUIRE_NO_PRIMARY_MUTATION"
    REQUIRE_NO_DUPLICATE_CODEX_PING = "REQUIRE_NO_DUPLICATE_CODEX_PING"
    REQUIRE_NO_UNRESOLVED_THREADS = "REQUIRE_NO_UNRESOLVED_THREADS"


@dataclass
class AEDDecision:
    """A single policy decision.

    The class is a plain dataclass with primitive fields so it
    can be serialized to JSON via :meth:`to_dict` and round-
    tripped. The ``matched_rule_ids`` list references the
    canonical ``AED-RULE-NNN`` identifiers in
    ``docs/governance/aed_rules_inventory.md``.
    """

    allowed: bool
    code: AEDDecisionCode
    reason: str
    required_evidence: List[str] = field(default_factory=list)
    matched_rule_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-friendly dict.

        The output is stable for the same decision: the key
        order, value types, and field names are all fixed, so
        downstream report builders can rely on a stable shape.
        """
        return {
            "allowed": bool(self.allowed),
            "code": self.code.value,
            "reason": str(self.reason),
            "required_evidence": list(self.required_evidence),
            "matched_rule_ids": list(self.matched_rule_ids),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AEDDecision":
        """Reconstruct from a :meth:`to_dict` payload.

        Provided so the decision shape can round-trip through
        JSON without callers building the dataclass by hand.
        """
        return cls(
            allowed=bool(payload.get("allowed", False)),
            code=AEDDecisionCode(str(payload.get("code", AEDDecisionCode.DENY.value))),
            reason=str(payload.get("reason", "")),
            required_evidence=list(payload.get("required_evidence", [])),
            matched_rule_ids=list(payload.get("matched_rule_ids", [])),
        )
