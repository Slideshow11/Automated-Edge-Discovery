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
import re
from datetime import datetime, timezone
from pathlib import Path


PACKET_KIND = "aed.merge_ready.v1"
REVIEW_EVIDENCE_KIND = "aed.pr_gate.review_evidence.v1"


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


# ── Review Evidence Packet checks ─────────────────────────────────────────────

def _is_valid_sha(sha: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{40}", sha)) if sha else False


def load_review_evidence(path: str) -> tuple[dict | None, str]:
    """Load and parse a REVIEW_EVIDENCE_PACKET JSON file."""
    try:
        with open(path, encoding="utf-8") as fh:
            packet = json.load(fh)
    except FileNotFoundError:
        return None, f"review evidence file not found: {path}"
    except json.JSONDecodeError as e:
        return None, f"invalid JSON in review evidence: {e}"
    except Exception as e:
        return None, f"failed to read review evidence: {e}"

    kind = packet.get("packet_kind", "")
    if kind != REVIEW_EVIDENCE_KIND:
        return None, f"packet_kind is '{kind}', expected '{REVIEW_EVIDENCE_KIND}'"
    return packet, ""


def check_review_evidence(
    packet: dict,
    auth_head_sha: str | None = None,
    current_head: str | None = None,
) -> list[tuple[str, bool, str]]:
    """Check a review evidence packet against the authorization packet head_sha.

    Returns list of (check_name, passed, message).
    Rejects when:
      - review_source is "none", empty, or missing
      - review_status is not "clean"
      - reviewed_head_sha != current_head_sha (stale)
      - current_head_sha missing or empty
      - review evidence current_head_sha != authorization packet head_sha
      - --current-head is supplied and does not match review evidence current_head_sha
      - ci_all_green is not True
      - scope_status is not "clean"
      - packet's merge_allowed disagrees with recomputed facts
    """
    checks = []

    # Packet kind
    kind = packet.get("packet_kind", "")
    ok = kind == REVIEW_EVIDENCE_KIND
    checks.append(("review_evidence_packet_kind", ok,
                    "" if ok else f"packet_kind is '{kind}', expected '{REVIEW_EVIDENCE_KIND}'"))

    # Required SHA fields must be present
    reviewed_head_sha = packet.get("reviewed_head_sha", "")
    current_head_sha = packet.get("current_head_sha", "")
    ok = bool(current_head_sha) and _is_valid_sha(current_head_sha)
    checks.append(("review_evidence_has_current_head_sha", ok,
                    "" if ok else "current_head_sha is missing or invalid"))
    ok = bool(reviewed_head_sha) and _is_valid_sha(reviewed_head_sha)
    checks.append(("review_evidence_has_reviewed_head_sha", ok,
                    "" if ok else "reviewed_head_sha is missing or invalid"))

    # Reject review_source not in allowed set (bogus/typo sources)
    review_source = packet.get("review_source", "")
    allowed_sources = ("github_codex", "codex_cli_fallback", "reviewer")
    ok = review_source in allowed_sources
    checks.append(("review_source_valid", ok,
                    "" if ok else f"review_source '{review_source}' not in allowed set {allowed_sources}"))

    # Reject review_source="none"/empty/None even if packet claims merge_allowed=True
    missing_source = review_source in ("none", "", None) or not review_source
    ok = not missing_source
    checks.append(("review_source_not_none", ok,
                    "" if ok else f"review_source is '{review_source}' — evidence is missing"))

    # review_status must be "clean"
    review_status = packet.get("review_status", "")
    ok = review_status == "clean"
    checks.append(("review_status_clean", ok,
                    "" if ok else f"review_status is '{review_status}', not 'clean'"))

    # Recompute staleness from raw SHA fields
    actual_stale = (
        bool(current_head_sha)
        and bool(reviewed_head_sha)
        and current_head_sha != reviewed_head_sha
    )
    ok = not actual_stale
    checks.append(("review_not_stale", ok,
                    "" if ok else "review is stale: reviewed_head_sha != current_head_sha"))

    # Authorization packet head_sha must match review evidence current_head_sha
    if auth_head_sha:
        ok = bool(current_head_sha) and current_head_sha == auth_head_sha
        checks.append(("auth_head_sha_matches_review_evidence", ok,
                        "" if ok else f"auth head {auth_head_sha[:8]} != review evidence current_head_sha {current_head_sha[:8]}"))

    # --current-head must match review evidence current_head_sha
    if current_head:
        ok = bool(current_head_sha) and current_head == current_head_sha
        checks.append(("current_head_matches_review_evidence", ok,
                        "" if ok else f"--current-head {current_head[:8]} != review evidence current_head_sha {current_head_sha[:8]}"))

    # ci_all_green
    ci_all_green = packet.get("ci_all_green")
    ok = ci_all_green is True
    checks.append(("ci_all_green", ok,
                    "" if ok else f"ci_all_green is {ci_all_green}"))

    # scope_status clean
    scope_status = packet.get("scope_status", "")
    ok = scope_status == "clean"
    checks.append(("scope_clean", ok,
                    "" if ok else f"scope_status is '{scope_status}', not 'clean'"))

    # Recompute merge_allowed from facts (not from packet boolean)
    packet_mergeable = packet.get("mergeable") is True
    actual_merge_allowed = (
        not missing_source
        and review_status == "clean"
        and not actual_stale
        and bool(current_head_sha)
        and ci_all_green is True
        and scope_status == "clean"
        and packet_mergeable
        and (not auth_head_sha or current_head_sha == auth_head_sha)
        and (not current_head or current_head_sha == current_head)
    )
    packet_merge_allowed = packet.get("merge_allowed")
    ok = packet_merge_allowed is actual_merge_allowed or (actual_merge_allowed and packet_merge_allowed is True)
    checks.append(("merge_allowed_accurate", ok,
                    "" if ok else f"packet merge_allowed={packet_merge_allowed} disagrees with recomputed {actual_merge_allowed}"))

    return checks


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
    p.add_argument(
        "--review-evidence", type=str, default=None,
        help="Path to REVIEW_EVIDENCE_PACKET.json (optional). "
             "If supplied, review evidence checks are run."
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Load MERGE_READY_PACKET
    packet, load_err = load_packet(args.packet)
    if packet is None:
        print(f"ERROR: {load_err}", file=sys.stderr)
        return 1

    # Run all MERGE_READY_PACKET checks
    results = run_all_checks(packet, args.phrase, args.current_head)

    # Load and check review evidence if provided
    if args.review_evidence:
        rev_packet, rev_err = load_review_evidence(args.review_evidence)
        if rev_packet is None:
            print(f"ERROR: {rev_err}", file=sys.stderr)
            return 1
        rev_results = check_review_evidence(rev_packet, auth_head_sha=packet.get("head_sha"), current_head=args.current_head)
        results.extend(rev_results)
    else:
        rev_packet = None

    # Print results
    all_passed = all(passed for _, passed, _ in results)

    print(f"MERGE AUTHORIZATION GUARD")
    print(f"{'='*50}")
    print(f"PR: {packet.get('pr_number', '?')} | {packet.get('pr_url', '?')}")
    print(f"Packet head: {packet.get('head_sha', '?')[:8]}")
    print(f"Recommendation: {packet.get('recommendation', '?')}")
    if rev_packet:
        print(f"Review evidence: {rev_packet.get('current_head_sha', '?')[:8]} "
              f"[{rev_packet.get('review_source', '?')}] "
              f"stale={rev_packet.get('review_is_stale', '?')} "
              f"merge_allowed={rev_packet.get('merge_allowed', '?')}")
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