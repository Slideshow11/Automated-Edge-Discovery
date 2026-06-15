"""Simple reporting helpers for AED decisions.

These helpers produce a final-report paragraph, list the missing
evidence fields for a denied decision, and summarize a list of
decisions. They are pure formatting and do not perform any side
effects.
"""
from __future__ import annotations

from typing import Iterable, List

from .decisions import AEDDecision, AEDDecisionCode


def decision_to_paragraph(decision: AEDDecision) -> str:
    """Convert a single decision into a one-line human-readable paragraph.

    The format is:

        ``ALLOW (<code>): <reason> [rules: <comma-separated rule ids>]``

    or, for a denial:

        ``DENY (<code>): <reason> [rules: <comma-separated rule ids>]``
    """
    rules = (
        ", ".join(decision.matched_rule_ids)
        if decision.matched_rule_ids
        else "(no rule matched)"
    )
    verdict = "ALLOW" if decision.allowed else "DENY"
    return f"{verdict} ({decision.code.value}): {decision.reason} [rules: {rules}]"


def missing_evidence(decision: AEDDecision) -> List[str]:
    """Return the required-evidence fields for a denied decision.

    An empty list is returned for allowed decisions.
    """
    if decision.allowed:
        return []
    return list(decision.required_evidence)


def summarize_denied(decisions: Iterable[AEDDecision]) -> str:
    """Summarize a list of decisions, focusing on the denials.

    The output is a multi-line block. If every decision is
    allowed, the summary is a single line.
    """
    decisions_list = list(decisions)
    denied = [d for d in decisions_list if not d.allowed]
    if not denied:
        return f"All {len(decisions_list)} decision(s) allowed."
    lines = [f"{len(denied)} of {len(decisions_list)} decision(s) denied:"]
    for d in denied:
        lines.append(f"  - {decision_to_paragraph(d)}")
    return "\n".join(lines)


__all__ = [
    "decision_to_paragraph",
    "missing_evidence",
    "summarize_denied",
]
