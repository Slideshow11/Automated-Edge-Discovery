#!/usr/bin/env python3
"""Build a read-only MERGE_READY_PACKET from PR gate data.

Produces MERGE_READY_PACKET.json and MERGE_READY_PACKET.md from explicit CLI args.

Does NOT call gh, does NOT merge, does NOT post comments.
Does NOT auto-authorize. Human must run check_merge_authorization.py.

Usage:
  python3 scripts/local/build_merge_ready_packet.py \\
    --pr-number 193 \\
    --pr-url https://github.com/Slideshow11/Automated-Edge-Discovery/pull/193 \\
    --base-branch main \\
    --head-sha af386e4c75341a2a6e7a6f68b680844de5cef1df \\
    --mergeable true \\
    --ci-status green \\
    --codex-status reviewed_clean \\
    --reviewer-status approved \\
    --changed-files "docs/README.md,docs/aed_tasker_packet_usage.md,scripts/local/aed_tasker_collect_context.py,tests/test_aed_tasker_collect_context.py,docs/current_project_status.md" \\
    --allowed-files "docs/README.md,docs/aed_tasker_packet_usage.md,docs/current_project_status.md,scripts/local/aed_tasker_collect_context.py,tests/test_aed_tasker_collect_context.py" \\
    --recommendation merge \\
    --output-json /tmp/MERGE_READY_PACKET.json \\
    --output-md /tmp/MERGE_READY_PACKET.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


# ── Safety constants ──────────────────────────────────────────────────────────

HERMES_PREFIX = "/home/max/.hermes"
FORBIDDEN_PREFIXES = (HERMES_PREFIX,)
PACKET_KIND = "aed.merge_ready.v1"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _is_forbidden_path(path: str) -> bool:
    abs_path = str(Path(path).resolve())
    for prefix in FORBIDDEN_PREFIXES:
        if abs_path.startswith(prefix) or abs_path == prefix:
            return True
    return False


def build_packet(
    pr_number: int,
    pr_url: str,
    base_branch: str,
    head_sha: str,
    mergeable: bool,
    ci_status: str,
    codex_status: str,
    reviewer_status: str,
    changed_files: list[str],
    allowed_files: list[str],
    recommendation: str,
) -> dict:
    """Build a MERGE_READY_PACKET dict.

    recommendation must be "merge" for the authorization guard to pass.
    Any other value (e.g. "patch", "block", "wait") causes the guard to fail.
    """
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=72)

    # Normalize recommendation to lowercase
    rec = recommendation.lower().strip()

    # Derive blockers from gate data — any gate failure is a blocker
    blockers: list[str] = []

    if not mergeable:
        blockers.append("mergeable is false")

    if ci_status not in ("green", "pending"):
        blockers.append(f"ci_status is '{ci_status}', not 'green' or 'pending'")

    if codex_status not in ("reviewed_clean", "unavailable", "not_requested"):
        blockers.append(f"codex_status is '{codex_status}' — not reviewed_clean/unavailable/not_requested")

    if reviewer_status not in ("approved", "pending"):
        blockers.append(f"reviewer_status is '{reviewer_status}' — not approved/pending")

    # Check changed_files are subset of allowed_files
    allowed_set = set(allowed_files)
    for f in changed_files:
        if f not in allowed_set:
            blockers.append(f"changed file '{f}' is not in allowed_files")
            break

    # recommendation != "merge" is always a blocker
    if rec != "merge":
        blockers.append(f"recommendation is '{rec}', not 'merge'")

    return {
        "packet_kind": PACKET_KIND,
        "pr_number": pr_number,
        "pr_url": pr_url,
        "base_branch": base_branch,
        "head_sha": head_sha,
        "mergeable": mergeable,
        "ci_status": ci_status,
        "codex_status": codex_status,
        "reviewer_status": reviewer_status,
        "changed_files": changed_files,
        "allowed_files": allowed_files,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "required_authorization_phrase": f"I confirm merge PR #{pr_number} at {head_sha}",
        "blockers": blockers,
        "recommendation": rec,
    }


def serialize_packet(packet: dict) -> str:
    """Serialize to stable JSON."""
    return json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def render_markdown(packet: dict) -> str:
    """Render human-readable MERGE_READY_PACKET."""
    rec = packet.get("recommendation", "?")
    ci = packet.get("ci_status", "?")
    codex = packet.get("codex_status", "?")
    reviewer = packet.get("reviewer_status", "?")
    blockers = packet.get("blockers", [])

    lines = [
        "# MERGE_READY_PACKET",
        "",
        f"**Kind:** `{packet.get('packet_kind', '?')}`",
        f"**PR:** [{packet.get('pr_number', '?')}]({packet.get('pr_url', '')})",
        f"**Branch:** `{packet.get('base_branch', '?')}` → `{packet.get('head_sha', '?')[:8]}`",
        f"**Generated:** {packet.get('generated_at', '?')}",
        f"**Expires:** {packet.get('expires_at', '?')}",
        "",
        f"**CI:** {ci}  |  **Codex:** {codex}  |  **Reviewer:** {reviewer}",
        f"**Recommendation:** `{rec}`",
        f"**Mergeable:** {packet.get('mergeable', '?')}",
        "",
    ]

    if blockers:
        lines.append("## Blockers")
        for b in blockers:
            lines.append(f"  - ❌ {b}")
        lines.append("")
    else:
        lines.append("## Blockers\n  - (none)\n")

    lines.append("## Changed Files")
    for f in packet.get("changed_files", []):
        lines.append(f"  - `{f}`")
    lines.append("")

    lines.append("## Authorization")
    phrase = packet.get("required_authorization_phrase", "?")
    lines.append(f"```\n{phrase}\n```")
    lines.append("")
    lines.append("> Copy the exact phrase above and pass it to `check_merge_authorization.py`.")
    lines.append("> **Do not** guess or paraphrase — the guard requires an exact match.")
    lines.append("")
    lines.append("### Why this phrase is required")
    lines.append("")
    lines.append("The phrase encodes three facts:")
    lines.append("  1. Which PR (PR #N)")
    lines.append("  2. Which exact HEAD (`at <sha>`)")
    lines.append("  3. The action (merge)")
    lines.append("")
    lines.append("Saying only `\"I confirm\"` or `\"merge\"` is ambiguous — it could apply")
    lines.append("to any PR at any HEAD. The guard rejects partial phrases.")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Build a MERGE_READY_PACKET from PR gate data. "
                    "Does NOT merge. Human must run check_merge_authorization.py.",
    )
    p.add_argument("--pr-number", type=int, required=True)
    p.add_argument("--pr-url", type=str, required=True)
    p.add_argument("--base-branch", type=str, required=True)
    p.add_argument("--head-sha", type=str, required=True)
    p.add_argument("--mergeable", type=str, required=True)  # "true" or "false"
    p.add_argument("--ci-status", type=str, required=True)
    p.add_argument("--codex-status", type=str, required=True)
    p.add_argument("--reviewer-status", type=str, required=True)
    p.add_argument("--changed-files", type=str, required=True)
    p.add_argument("--allowed-files", type=str, required=True)
    p.add_argument("--recommendation", type=str, required=True)
    p.add_argument("--output-json", type=str, default=None)
    p.add_argument("--output-md", type=str, default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Validate output paths
    if args.output_json and _is_forbidden_path(args.output_json):
        print(f"ERROR: Output path may not be inside {HERMES_PREFIX}", file=sys.stderr)
        return 1
    if args.output_md and _is_forbidden_path(args.output_md):
        print(f"ERROR: Output path may not be inside {HERMES_PREFIX}", file=sys.stderr)
        return 1

    # Parse mergeable
    mergeable = args.mergeable.lower() in ("true", "1", "yes")

    # Parse comma-separated file lists
    changed_files = [f.strip() for f in args.changed_files.split(",") if f.strip()]
    allowed_files = [f.strip() for f in args.allowed_files.split(",") if f.strip()]

    packet = build_packet(
        pr_number=args.pr_number,
        pr_url=args.pr_url,
        base_branch=args.base_branch,
        head_sha=args.head_sha,
        mergeable=mergeable,
        ci_status=args.ci_status,
        codex_status=args.codex_status,
        reviewer_status=args.reviewer_status,
        changed_files=changed_files,
        allowed_files=allowed_files,
        recommendation=args.recommendation,
    )

    json_bytes = serialize_packet(packet).encode("utf-8")

    if args.output_json:
        Path(args.output_json).write_bytes(json_bytes)
        print(f"JSON written to {args.output_json}", file=sys.stderr)

    if args.output_md:
        md = render_markdown(packet)
        Path(args.output_md).write_text(md + "\n", encoding="utf-8")
        print(f"Markdown written to {args.output_md}", file=sys.stderr)

    if not args.output_json and not args.output_md:
        print(serialize_packet(packet))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())