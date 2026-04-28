#!/usr/bin/env python3
"""Validate docs/edge_hypothesis_registry.csv and docs/edge_hypothesis_registry_v1.md

This script resolves the repository root from its own file location so it works
regardless of the current working directory. It performs lightweight CSV checks
and ensures the registry doc exists.
"""
import csv
import re
import sys
from pathlib import Path

# Resolve script path absolutely and compute repo root reliably
SCRIPT_PATH = Path(__file__).resolve()
# scripts/local -> scripts -> <repo-root>
REPO_ROOT = SCRIPT_PATH.parents[2]
if not (REPO_ROOT / "docs").is_dir():
    sys.exit(f"could not resolve repo root from {SCRIPT_PATH}; expected {REPO_ROOT}/docs to exist")

REGISTRY_CSV = REPO_ROOT / "docs" / "edge_hypothesis_registry.csv"
REGISTRY_DOC = REPO_ROOT / "docs" / "edge_hypothesis_registry_v1.md"

if not REGISTRY_CSV.exists():
    sys.exit(f"Registry CSV not found at {REGISTRY_CSV}")
if not REGISTRY_DOC.exists():
    sys.exit(f"Registry doc not found at {REGISTRY_DOC}")

REQUIRED_COLS = [
    "hypothesis_id",
    "short_name",
    "asset_class",
    "market_universe",
    "signal_family",
    "proposed_mechanism",
    "data_requirements",
    "leakage_risks",
    "test_protocol_link",
    "hypothesis_card_link",
    "status",
    "evidence_stage",
    "owner",
    "created_date",
    "last_updated",
]

STATUS_ALLOWED = {"proposed", "specified", "testing", "falsified", "parked", "promoted"}
EVIDENCE_ALLOWED = {
    "idea",
    "literature_supported",
    "in_sample_tested",
    "falsification_tested",
    "out_of_sample_tested",
    "production_candidate",
}

ID_RE = re.compile(r"^AED-HYP-\d{4}$")


def fail(msg: str) -> None:
    print("ERROR:", msg, file=sys.stderr)
    sys.exit(2)


with REGISTRY_CSV.open(newline="") as f:
    reader = csv.DictReader(f)
    cols = reader.fieldnames or []
    missing = [c for c in REQUIRED_COLS if c not in cols]
    if missing:
        fail(f"Missing required columns: {missing}")

    ids = []
    for i, row in enumerate(reader, start=1):
        hid = (row.get("hypothesis_id") or "").strip()
        if not hid:
            fail(f"Row {i}: empty hypothesis_id")
        if not ID_RE.match(hid):
            fail(f"Row {i}: hypothesis_id '{hid}' does not match AED-HYP-0001 format")
        if hid in ids:
            fail(f"Duplicate hypothesis_id: {hid} (row {i})")
        ids.append(hid)

        status = (row.get("status") or "").strip()
        if status not in STATUS_ALLOWED:
            fail(f"Row {i}: status '{status}' not in allowed set {sorted(STATUS_ALLOWED)}")

        ev = (row.get("evidence_stage") or "").strip()
        if ev not in EVIDENCE_ALLOWED:
            fail(f"Row {i}: evidence_stage '{ev}' not in allowed set {sorted(EVIDENCE_ALLOWED)}")

print("edge_hypothesis_registry.csv validation OK")
print(f"registry doc present: {REGISTRY_DOC}")
