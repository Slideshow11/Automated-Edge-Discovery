#!/usr/bin/env python3
"""Validate docs/edge_hypothesis_registry.csv

Checks:
- required columns exist
- hypothesis_id format AED-HYP-0001
- status and evidence_stage values in allowed sets
- no duplicate hypothesis_id values

Exits with non-zero on failure.
"""
import csv
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = REPO_ROOT / "docs" / "edge_hypothesis_registry.csv"

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


if not CSV_PATH.exists():
    fail(f"Registry CSV not found at {CSV_PATH}")

with CSV_PATH.open(newline="") as f:
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
