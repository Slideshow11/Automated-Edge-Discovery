#!/usr/bin/env python3
"""
Validate docs/edge_hypothesis_card_v1.md has required sections and does not include authorization language that grants automated promotion or production trading.
"""
import sys
from pathlib import Path

REQUIRED_SECTIONS = [
    "Purpose",
    "When this card is required",
    "Required fields",
    "Hypothesis statement",
    "Economic mechanism",
    "Instrument universe",
    "Data sources",
    "Point-in-time constraints",
    "Testable prediction",
    "Primary metric",
    "Secondary diagnostics",
    "Null result definition",
    "Multiple testing controls",
    "Leakage risks",
    "Execution realism assumptions",
    "Required falsification checks",
    "Promotion restrictions",
    "Example card",
]

FORBIDDEN_AUTHS = [
    "authorize automated promotion",
    "authorize promotion",
    "automated promotion allowed",
    "automatic promotion",
    "promote automatically",
    "authorize production",
    "production use",
    "authorize trading",
    "automated trading",
    "registry mutation",
    "registry_mutation",
]

NEGATIONS = ["not", "no", "does not", "cannot", "never", "without"]


def has_negation(line):
    low = line.lower()
    return any(neg in low for neg in NEGATIONS)


def main():
    p = Path("docs/edge_hypothesis_card_v1.md")
    if not p.exists():
        print("ERROR: docs/edge_hypothesis_card_v1.md not found", file=sys.stderr)
        sys.exit(2)
    text = p.read_text()

    missing = [s for s in REQUIRED_SECTIONS if s not in text]
    if missing:
        print("MISSING SECTIONS:", missing, file=sys.stderr)
        sys.exit(3)

    # Check for forbidden auth phrases that are not negated on the same line
    bad = []
    for phrase in FORBIDDEN_AUTHS:
        for line in text.splitlines():
            if phrase in line.lower():
                if not has_negation(line):
                    bad.append((phrase, line.strip()))
    if bad:
        print("FORBIDDEN AUTHORIZATION LANGUAGE FOUND:")
        for ph, ln in bad:
            print(f" - phrase: {ph} \n   line: {ln}")
        sys.exit(4)

    print("edge_hypothesis_card_v1.md validation OK")
    return 0

if __name__ == '__main__':
    sys.exit(main())
