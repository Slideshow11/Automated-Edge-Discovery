#!/usr/bin/env python3
"""Read-only merge authorization guard.

Verifies a MERGE_READY_PACKET and a human-provided phrase before merge.
Does NOT call gh pr merge. Does NOT push. Does NOT update memory.
Only prints result and exits 0 (authorized) or 1 (denied).

Usage:
  python3 scripts/local/check_merge_authorization.py \\
    --packet /tmp/MERGE_READY_PACKET.json \\
    --phrase "I confirm merge PR #193 at af386e4c75341a2a6e7a6f68b680844de5cef1df"

  # Or with current HEAD check:
  python3 scripts/local/check_merge_authorization.py \\
    --packet /tmp/MERGE_READY_PACKET.json \\
    --phrase "I confirm merge PR #193 at af386e4c75341a2a6e7a6f68b680844de5cef1df" \\
    --current-head af386e4c75341a2a6e7a6f68b680844de5cef1df
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PACKET_KIND = "aed.merge_ready.v1"


# ── Validation helpers ────────────────────────────────────────────────────────

def check_packet_kind(packet: dict) -> tuple[bool, str]:
    kind = packet.get("packet_kind", "")
    if kind != PACKET_KIND:
        return False, f"packet_kind is '{kind}', expected '{PACKET_KIND}'"
    return True, ""


def check_not_expired(packet: dict) -> tuple[bool, str]:
    expires_str = packet.get("expires_at", "")
    if not expires_str:
        return False, "expires_at is missing"
    try:
        expires = datetime.fromisoformat(expires_str.replace("+00:00", "+00:00"))
        now = datetime.now(timezone.utc)
        if now > expires:
            return False, f"packet expired at {expires_str}"
        return True, ""
    except ValueError as e:
        return False, f"invalid expires_at format: {e}"


def check_phrase_match(packet: dict, provided_phrase: str) -> tuple[bool, str]:
    required = packet.get("required_authorization_phrase", "")
    if provided_phrase != required:
        return False, (
            f"phrase mismatch:\n"
            f"  required: {required}\n"
            f"  provided: {provided_phrase}"
        )
    return True, ""


def check_head_sha_match(packet: dict, current_head: str | None) -> tuple[bool, str]:
    if current_head is None:
        return True, ""
    packet_head = packet.get("head_sha", "")
    if packet_head and current_head != packet_head:
        return False, (
            f"HEAD mismatch:\n"
            f"  packet head: {packet_head}\n"
            f"  current head: {current_head}"
        )
    return True, ""


def check_no_blockers(packet: dict) -> tuple[bool, str]:
    blockers = packet.get("blockers", [])
    if blockers:
        blocker_list = ", ".join(f"'{b}'" for b in blockers)
        return False, f"blockers present: {blocker_list}"
    return True, ""


def check_recommendation_merge(packet: dict) -> tuple[bool, str]:
    rec = packet.get("recommendation", "")
    if rec != "merge":
        return False, f"recommendation is '{rec}', not 'merge'"
    return True, ""


def check_required_fields(packet: dict) -> tuple[bool, str]:
    required_fields = [
        "packet_kind",
        "pr_number",
        "pr_url",
        "base_branch",
        "head_sha",
        "mergeable",
        "ci_status",
        "codex_status",
        "reviewer_status",
        "changed_files",
        "allowed_files",
        "generated_at",
        "expires_at",
        "required_authorization_phrase",
        "blockers",
        "recommendation",
    ]
    missing = [f for f in required_fields if f not in packet]
    if missing:
        return False, f"missing required fields: {', '.join(missing)}"
    return True, ""


def load_packet(path: str) -> tuple[dict | None, str]:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), ""
    except FileNotFoundError:
        return None, f"packet file not found: {path}"
    except json.JSONDecodeError as e:
        return None, f"invalid JSON in packet: {e}"
    except Exception as e:
        return None, f"failed to read packet: {e}"


def run_all_checks(packet: dict, provided_phrase: str, current_head: str | None) -> list[tuple[str, bool, str]]:
    """Run all checks. Returns list of (check_name, passed, message)."""
    checks = [
        ("packet_kind", *check_packet_kind(packet)),
        ("required_fields", *check_required_fields(packet)),
        ("not_expired", *check_not_expired(packet)),
        ("phrase_match", *check_phrase_match(packet, provided_phrase)),
        ("head_sha_match", *check_head_sha_match(packet, current_head)),
        ("no_blockers", *check_no_blockers(packet)),
        ("recommendation_is_merge", *check_recommendation_merge(packet)),
    ]
    return checks


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Read-only merge authorization guard. "
                    "Verifies MERGE_READY_PACKET and human phrase. Does NOT merge.",
    )
    p.add_argument("--packet", type=str, required=True, help="Path to MERGE_READY_PACKET.json")
    p.add_argument("--phrase", type=str, required=True, help="Authorization phrase")
    p.add_argument(
        "--current-head", type=str, default=None,
        help="Optional: verify current HEAD matches packet head_sha"
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Load packet
    packet, load_err = load_packet(args.packet)
    if packet is None:
        print(f"ERROR: {load_err}", file=sys.stderr)
        return 1

    # Run all checks
    results = run_all_checks(packet, args.phrase, args.current_head)

    # Print results
    all_passed = all(passed for _, passed, _ in results)

    print(f"MERGE AUTHORIZATION GUARD")
    print(f"{'='*50}")
    print(f"PR: {packet.get('pr_number', '?')} | {packet.get('pr_url', '?')}")
    print(f"Packet head: {packet.get('head_sha', '?')[:8]}")
    print(f"Recommendation: {packet.get('recommendation', '?')}")
    print()

    for name, passed, msg in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  [{status}] {name}")
        if msg:
            print(f"         {msg}")

    print()
    if all_passed:
        print("✅ AUTHORIZED — all checks passed.")
        print("   Run `gh pr merge ...` manually to complete merge.")
        return 0
    else:
        print("❌ DENIED — one or more checks failed.")
        print("   Fix failures before merging.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())