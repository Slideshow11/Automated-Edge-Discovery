#!/usr/bin/env python3
"""
Local Event/Options contract validator v1 (minimal implementation)
"""
import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import List, Dict, Any

# Enums / constants
EVENT_SESSIONS = {"BMO", "AMC", "INTRA", "UNKNOWN"}
EVENT_HOLD_FLAGS = {"no_event_hold", "partial_event_hold", "full_event_hold", "unknown_event_hold"}
GAP_EXPOSURE = {"none", "partial", "full", "unknown"}

# Alias mapping for minimal fixtures
ALIAS_MAP = {
    "event_time": "event_time_utc",
    "option_symbol": "option_contract_symbol",
    "observation_date": "option_observation_date",
}

@dataclass
class Problem:
    code: str
    file: str
    row: int
    field: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "file": self.file,
            "row": self.row,
            "field": self.field,
            "message": self.message,
        }


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--events", required=True)
    p.add_argument("--options", required=True)
    p.add_argument("--profile", choices=("minimal_fixture_profile", "strict_contract_profile"), default="minimal_fixture_profile")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--allow-warnings", action="store_true")
    return p.parse_args()


def load_csv(path: str, alias_map: Dict[str, str], profile: str):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    with p.open(newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = []
        for r in reader:
            if profile == "minimal_fixture_profile":
                nr = {}
                for k, v in r.items():
                    nk = alias_map.get(k, k)
                    nr[nk] = v.strip() if v is not None else v
                rows.append(nr)
            else:
                nr = {k: (v.strip() if v is not None else v) for k, v in r.items()}
                rows.append(nr)
    return headers, rows


def parse_iso_datetime(s: str):
    if not s:
        raise ValueError("empty")
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except Exception:
        raise


def validate(events_rows: List[Dict[str, str]], options_rows: List[Dict[str, str]], profile: str, allow_warnings: bool):
    blockers: List[Problem] = []
    warnings: List[Problem] = []

    required_events_minimal = ["event_id", "event_type", "event_date", "event_time_utc", "event_session"]
    required_events_strict = required_events_minimal + ["event_timestamp_quality", "calendar_id", "timezone", "event_source", "point_in_time_policy"]
    required_events = required_events_minimal if profile == "minimal_fixture_profile" else required_events_strict

    seen_event_ids = set()
    for i, er in enumerate(events_rows, start=2):
        rownum = i
        for f in required_events:
            if not er.get(f):
                blockers.append(Problem("missing_required_field", "events", rownum, f, f + " is required"))
        eid = er.get("event_id")
        if eid:
            if eid in seen_event_ids:
                blockers.append(Problem("duplicate_event_id", "events", rownum, "event_id", f"duplicate event_id {eid}"))
            else:
                seen_event_ids.add(eid)
        session = er.get("event_session")
        if session and session not in EVENT_SESSIONS:
            blockers.append(Problem("invalid_enum", "events", rownum, "event_session", f"invalid session {session}"))
        if session == "UNKNOWN":
            blockers.append(Problem("unknown_event_session", "events", rownum, "event_session", "UNKNOWN event_session blocks advancement"))
        et = er.get("event_time_utc")
        if et:
            try:
                parse_iso_datetime(et)
            except Exception:
                blockers.append(Problem("invalid_timestamp", "events", rownum, "event_time_utc", f"cannot parse {et}"))

    required_options_minimal = ["event_id", "option_observation_id", "option_contract_symbol", "option_observation_date"]
    required_options_strict = [
        "option_observation_id", "option_contract_symbol", "option_observation_date", "event_id", "event_time_utc",
        "option_type", "option_expiry", "expiry_covers_event", "event_hold_flag", "gap_exposure",
        "fill_model", "stale_quote_policy", "spread_metric", "liquidity_metric",
    ]
    required_options = required_options_minimal if profile == "minimal_fixture_profile" else required_options_strict

    for i, orow in enumerate(options_rows, start=2):
        rownum = i
        for f in required_options:
            if not orow.get(f):
                blockers.append(Problem("missing_required_field", "options", rownum, f, f + " is required"))
        eid = orow.get("event_id")
        if not eid:
            blockers.append(Problem("missing_event_link", "options", rownum, "event_id", "event_id missing"))
        else:
            if eid not in seen_event_ids:
                blockers.append(Problem("invalid_event_link", "options", rownum, "event_id", f"event_id {eid} not found in events"))
        eh = orow.get("event_hold_flag")
        if eh and eh not in EVENT_HOLD_FLAGS:
            blockers.append(Problem("invalid_enum", "options", rownum, "event_hold_flag", f"invalid event_hold_flag {eh}"))
        if eh == "unknown_event_hold":
            blockers.append(Problem("unknown_event_hold", "options", rownum, "event_hold_flag", "unknown_event_hold blocks advancement"))
        ge = orow.get("gap_exposure")
        if ge and ge not in GAP_EXPOSURE:
            blockers.append(Problem("invalid_enum", "options", rownum, "gap_exposure", f"invalid gap_exposure {ge}"))
        if ge == "unknown":
            blockers.append(Problem("unknown_gap_exposure", "options", rownum, "gap_exposure", "unknown gap_exposure blocks promotion or advancement"))

        feature_ts = orow.get("quote_timestamp") or orow.get("option_observation_date")
        decision_ts = orow.get("event_time_utc") or None
        if feature_ts and decision_ts:
            try:
                fts = parse_iso_datetime(feature_ts)
                dts = parse_iso_datetime(decision_ts)
                if fts > dts:
                    blockers.append(Problem("future_feature_timestamp", "options", rownum, "feature_timestamp", "feature timestamp after decision timestamp"))
            except Exception:
                pass

    return blockers, warnings


def print_text(profile, events_count, options_count, blockers, warnings):
    print(f"profile: {profile}")
    print(f"events_count: {events_count}")
    print(f"options_count: {options_count}")
    print(f"blockers_count: {len(blockers)}")
    print(f"warnings_count: {len(warnings)}")
    if blockers:
        print("\nBlockers:")
        for b in blockers:
            print(f"- {b.code} | {b.file} | row {b.row} | {b.field} | {b.message}")


def print_json(profile, events_count, options_count, blockers, warnings):
    out = {
        "profile": profile,
        "events_count": events_count,
        "options_count": options_count,
        "blockers": [b.to_dict() for b in blockers],
        "warnings": [w.to_dict() for w in warnings],
    }
    print(json.dumps(out, indent=2))


def main():
    args = parse_args()
    try:
        events_hdr, events_rows = load_csv(args.events, ALIAS_MAP, args.profile)
        options_hdr, options_rows = load_csv(args.options, ALIAS_MAP, args.profile)
    except FileNotFoundError as e:
        print(f"ERROR: file not found: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    blockers, warnings = validate(events_rows, options_rows, args.profile, args.allow_warnings)

    if args.format == "json":
        print_json(args.profile, len(events_rows), len(options_rows), blockers, warnings)
    else:
        print_text(args.profile, len(events_rows), len(options_rows), blockers, warnings)

    if blockers:
        sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    main()
