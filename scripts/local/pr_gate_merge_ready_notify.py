#!/usr/bin/env python3
"""
pr_gate_merge_ready_notify.py

Read-only helper that turns a clean PR gate result into a Telegram-ready merge
authorization packet. Produces JSON and markdown only — does NOT send Telegram,
does NOT merge, does NOT create Kanban tasks, does NOT dispatch workers.

Supports two input modes:
  1. Direct packet mode:   --merge-ready-packet + --controller-run-packet
  2. CLI parameter mode:   --pr-number, --head-sha, --ci-status, etc.

Output:
  --output-json  MERGE_READY_NOTIFICATION.json
  --output-md    MERGE_READY_NOTIFICATION.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACKET_KIND = "aed.pr_gate.merge_ready_notification.v1"
REVIEW_EVIDENCE_KIND = "aed.pr_gate.review_evidence.v1"
SCHEMA_VERSION = 1

STOP_RULES = [
    "no_auto_merge",
    "no_dispatch",
    "no_patch",
    "no_memory_update",
    "no_skill_manage",
]

FORBIDDEN_OUTPUT_PATHS = ["/home/max/.hermes"]

# Patterns that must NOT appear in the output (safety check on script itself)
FORBIDDEN_PATTERNS = [
    "requests.get",
    "requests.post",
    "requests.patch",
    "requests.put",
    "urllib.request",
    "httpx",
    "gh pr merge",
    "gh pr comment",
    "gh pr create",
    "hermes kanban",
    "git push",
    "git commit",
    "memory.update",
    "fact_store",
    "skill_manage",
    "delegate_task",
    "cronjob",
    "telegram",
    "send_message",
]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _reject_hermes_path(output_path: Path) -> None:
    resolved = output_path.resolve()
    for forbidden in FORBIDDEN_OUTPUT_PATHS:
        if str(resolved).startswith(forbidden):
            raise ValueError(f"Output path cannot be under {forbidden}: {output_path}")


def _is_valid_sha(sha: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{40}", sha))


def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def _load_review_evidence(path: Path | None) -> dict | None:
    """Load REVIEW_EVIDENCE_PACKET.json if provided, else None."""
    if path is None:
        return None
    data = _load_json(path)
    if data.get("packet_kind") != REVIEW_EVIDENCE_KIND:
        raise ValueError(f"Expected packet_kind '{REVIEW_EVIDENCE_KIND}', got '{data.get('packet_kind')}'")
    return data


def _write_json(data: dict, path: Path) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _write_text(text: str, path: Path) -> None:
    with open(path, "w") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _build_gate_summary(
    ci_status: str,
    codex_status: str,
    fallback_review_status: str,
    reviewer_status: str,
    scope_status: str,
    mergeable: bool,
    changed_files: list[str],
) -> dict[str, Any]:
    return {
        "ci_status": ci_status,
        "codex_status": codex_status,
        "fallback_review_status": fallback_review_status,
        "reviewer_status": reviewer_status,
        "scope_status": scope_status,
        "mergeable": mergeable,
        "changed_files": changed_files,
    }


def _is_merge_ready(gate_summary: dict[str, Any]) -> bool:
    if gate_summary.get("ci_status") != "green":
        return False
    # scope_status None = absent/not-tracked (e.g. old packet format); pass this gate
    scope = gate_summary.get("scope_status")
    if scope is not None and scope not in ("clean", ""):
        return False
    reviewer = gate_summary.get("reviewer_status", "")
    if reviewer not in ("clean", "approved", "not_required_with_reason"):
        return False
    codex = gate_summary.get("codex_status", "")
    fallback = gate_summary.get("fallback_review_status", "")
    if codex not in ("clean", "reviewed_clean", "unavailable", "not_requested", "") and fallback not in ("clean", ""):
        return False
    if not gate_summary.get("mergeable", False):
        return False
    if gate_summary.get("blockers"):
        return False
    return True


def _build_required_phrase(pr_number: int, head_sha: str) -> str:
    return f"I confirm merge PR #{pr_number} at {head_sha}"


def _build_merge_command(pr_number: int, head_sha: str) -> str:
    return (
        f"gh pr merge {pr_number} "
        f"--repo Slideshow11/Automated-Edge-Discovery "
        f"--squash --delete-branch --match-head-commit {head_sha}"
    )


def _build_telegram_message(
    pr_number: int,
    pr_url: str,
    head_sha: str,
    gate_summary: dict[str, Any],
    required_phrase: str,
    merge_cmd: str,
) -> str:
    ci = gate_summary.get("ci_status", "unknown")
    scope = gate_summary.get("scope_status", "unknown")
    reviewer = gate_summary.get("reviewer_status", "unknown")
    codex = gate_summary.get("codex_status", "unknown")
    fallback = gate_summary.get("fallback_review_status", "unknown")
    mergeable = gate_summary.get("mergeable", False)

    lines = [
        f"✅ PR #{pr_number} — MERGE READY",
        f"",
        f"🔗 {pr_url}",
        f"� commit: `{head_sha[:12]}...`",
        f"",
        f"CI: `{ci}`  |  Scope: `{scope}`  |  Reviewer: `{reviewer}`",
        f"Codex: `{codex}`  |  Fallback: `{fallback}`",
        f"Mergeable: `{mergeable}`",
        f"",
        f"⛔ Exact authorization required:",
        f"{required_phrase}",
        f"",
        f"Merge command:",
        f"`{merge_cmd}`",
    ]
    return "\n".join(lines)


def _render_markdown(
    pr_number: int,
    pr_url: str,
    head_sha: str,
    base_branch: str,
    gate_summary: dict[str, Any],
    required_phrase: str,
    merge_cmd: str,
    blockers: list[str],
    recommendation: str,
    review_evidence_summary: dict[str, Any] | None = None,
) -> str:
    lines = [
        f"# PR #{pr_number} — Merge-Ready Notification",
        "",
        f"**PR:** [{pr_number}]({pr_url})",
        f"**Head SHA:** `{head_sha}`",
        f"**Base branch:** `{base_branch}`",
        "",
        "## Gate Summary",
        "",
        f"- **CI:** `{gate_summary.get('ci_status', 'unknown')}`",
        f"- **Scope:** `{gate_summary.get('scope_status', 'unknown')}`",
        f"- **Reviewer:** `{gate_summary.get('reviewer_status', 'unknown')}`",
        f"- **Codex:** `{gate_summary.get('codex_status', 'unknown')}`",
        f"- **Fallback review:** `{gate_summary.get('fallback_review_status', 'unknown')}`",
        f"- **Mergeable:** `{gate_summary.get('mergeable', False)}`",
        "",
        "## Changed Files",
        "",
    ]
    for f in gate_summary.get("changed_files", []):
        lines.append(f"- `{f}`")

    lines += [
        "",
        "## Authorization",
        "",
        f"**Required phrase:**",
        "",
        f"> {required_phrase}",
        "",
        f"**Merge command:**",
        "",
        f"```bash",
        f"{merge_cmd}",
        f"```",
    ]

    if blockers:
        lines += [
            "",
            "## ⚠️ Blockers",
            "",
        ]
        for b in blockers:
            lines.append(f"- {b}")
    else:
        lines += [
            "",
            "## Recommendation",
            "",
            f"`{recommendation}` — all gates clean.",
        ]

    if review_evidence_summary:
        lines += [
            "",
            "## Review Evidence Summary",
            f"- **review_source:** `{review_evidence_summary.get('review_source', 'unknown')}`",
            f"- **reviewed_head_sha:** `{review_evidence_summary.get('reviewed_head_sha', 'unknown')}`",
            f"- **current_head_sha:** `{review_evidence_summary.get('current_head_sha', 'unknown')}`",
            f"- **review_is_stale:** `{review_evidence_summary.get('review_is_stale', 'unknown')}`",
            f"- **ci_all_green:** `{review_evidence_summary.get('ci_all_green', 'unknown')}`",
            f"- **scope_status:** `{review_evidence_summary.get('scope_status', 'unknown')}`",
            f"- **merge_allowed:** `{review_evidence_summary.get('merge_allowed', 'unknown')}`",
            f"- **review_status:** `{review_evidence_summary.get('review_status', 'unknown')}`",
        ]

    lines += [
        "",
        "## Stop Rules",
        "",
    ]
    for rule in STOP_RULES:
        lines.append(f"- `{rule}`")

    return "\n".join(lines)


def _collect_blockers(gate_summary: dict[str, Any]) -> list[str]:
    blockers = []
    if gate_summary.get("ci_status") != "green":
        blockers.append(f"CI is not green: {gate_summary.get('ci_status')}")
    scope = gate_summary.get("scope_status")
    if scope is not None and scope not in ("clean", ""):
        blockers.append(f"Scope is not clean: {scope}")
    reviewer = gate_summary.get("reviewer_status", "")
    if reviewer and reviewer not in ("clean", "approved", "not_required_with_reason"):
        blockers.append(f"Reviewer status is not clean/approved: {reviewer}")
    elif not reviewer:
        blockers.append("Reviewer status is missing (blank)")
    codex = gate_summary.get("codex_status", "")
    fallback = gate_summary.get("fallback_review_status", "")
    if codex not in ("clean", "reviewed_clean", "unavailable", "not_requested", "") and fallback not in ("clean", ""):
        blockers.append("Neither Codex nor fallback review is clean")
    if not gate_summary.get("mergeable", False):
        blockers.append("PR is not mergeable (has merge conflicts)")
    return blockers


def build_notification(
    pr_number: int,
    pr_url: str,
    head_sha: str,
    base_branch: str,
    ci_status: str,
    codex_status: str,
    fallback_review_status: str,
    reviewer_status: str,
    scope_status: str,
    mergeable: bool,
    changed_files: list[str],
    output_json_path: Path,
    output_md_path: Path,
    review_evidence: dict | None = None,
) -> dict[str, Any]:
    """Build merge-ready notification packet and markdown.

    Args:
        review_evidence: optional REVIEW_EVIDENCE_PACKET dict. If provided,
            review_source, reviewed_head_sha, current_head_sha, review_is_stale,
            ci_all_green, scope_status fields are included. If review evidence
            is stale or missing, no merge_ready authorization phrase is produced.
    """

    gate_summary = _build_gate_summary(
        ci_status=ci_status,
        codex_status=codex_status,
        fallback_review_status=fallback_review_status,
        reviewer_status=reviewer_status,
        scope_status=scope_status,
        mergeable=mergeable,
        changed_files=changed_files,
    )

    blockers = _collect_blockers(gate_summary)

    # PATCH-3: review evidence must be tied to the exact notification head_sha
    if review_evidence:
        rev_current = review_evidence.get("current_head_sha", "")
        if not rev_current or rev_current != head_sha:
            blockers.append(
                f"review evidence head mismatch: evidence current_head_sha='{rev_current}' "
                f"!= notification head_sha='{head_sha}'"
            )

    # If review evidence is provided, recompute merge_allowed from raw fields
    # (do not trust the packet's merge_allowed boolean — it may be forged)
    if review_evidence:
        rev_reviewed = review_evidence.get("reviewed_head_sha", "")
        rev_current = review_evidence.get("current_head_sha", "")
        actual_stale = bool(rev_current) and bool(rev_reviewed) and rev_current != rev_reviewed
        if actual_stale:
            blockers.append("review evidence is stale: reviewed_head_sha != current_head_sha")
        # Recompute from raw fields, not from packet boolean
        rev_source = review_evidence.get("review_source", "")
        rev_status = review_evidence.get("review_status", "")
        ci_green = review_evidence.get("ci_all_green") is True
        scope_clean = review_evidence.get("scope_status") == "clean"
        missing_source = rev_source in ("none", "", None) or not rev_source
        allowed_sources = ("github_codex", "codex_cli_fallback", "reviewer")
        valid_source = rev_source in allowed_sources
        rev_mergeable = review_evidence.get("mergeable") is True
        raw_merge_allowed = (
            valid_source
            and not missing_source
            and rev_status == "clean"
            and not actual_stale
            and bool(rev_current)
            and ci_green
            and scope_clean
            and rev_mergeable
            and (not rev_current or rev_current == head_sha)
        )
        if not raw_merge_allowed:
            blockers.append(
                f"review evidence recomputed merge_allowed=False: "
                f"source='{rev_source}', status='{rev_status}', "
                f"stale={actual_stale}, ci={ci_green}, scope={scope_clean}"
            )

    is_ready = _is_merge_ready(gate_summary) and not blockers

    required_phrase = _build_required_phrase(pr_number, head_sha)
    merge_cmd = _build_merge_command(pr_number, head_sha)

    if is_ready:
        recommendation = "merge_ready"
        telegram_text = _build_telegram_message(
            pr_number=pr_number,
            pr_url=pr_url,
            head_sha=head_sha,
            gate_summary=gate_summary,
            required_phrase=required_phrase,
            merge_cmd=merge_cmd,
        )
    else:
        recommendation = "not_merge_ready"
        telegram_text = (
            f"⚠️ PR #{pr_number} — NOT MERGE READY\n"
            f"commit: `{head_sha[:12]}...`\n"
            f"🔗 {pr_url}\n\n"
            + "\n".join(f"- {b}" for b in blockers)
        )

    # Build review evidence summary for output packet
    review_evidence_summary: dict[str, Any] | None = None
    if review_evidence:
        review_evidence_summary = {
            "review_source": review_evidence.get("review_source"),
            "reviewed_head_sha": review_evidence.get("reviewed_head_sha"),
            "current_head_sha": review_evidence.get("current_head_sha"),
            "review_is_stale": review_evidence.get("review_is_stale"),
            "ci_all_green": review_evidence.get("ci_all_green"),
            "scope_status": review_evidence.get("scope_status"),
            "merge_allowed": review_evidence.get("merge_allowed"),
            "review_status": review_evidence.get("review_status"),
        }

    packet: dict[str, Any] = {
        "packet_kind": PACKET_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pr": {
            "number": pr_number,
            "url": pr_url,
            "head_sha": head_sha,
            "base_branch": base_branch,
        },
        "gate_summary": gate_summary,
        "required_authorization_phrase": required_phrase if is_ready else None,
        "user_message": telegram_text,
        "merge_command_template": merge_cmd if is_ready else None,
        "recommendation": recommendation,
        "stop_rules": STOP_RULES,
        "blockers_or_uncertainty": blockers,
    }

    if review_evidence_summary is not None:
        packet["review_evidence_summary"] = review_evidence_summary

    _write_json(packet, output_json_path)
    md = _render_markdown(
        pr_number=pr_number,
        pr_url=pr_url,
        head_sha=head_sha,
        base_branch=base_branch,
        gate_summary=gate_summary,
        required_phrase=required_phrase if is_ready else "(blocked — see blockers above)",
        merge_cmd=merge_cmd if is_ready else "(blocked)",
        blockers=blockers,
        recommendation=recommendation,
        review_evidence_summary=review_evidence_summary,
    )
    _write_text(md, output_md_path)

    return packet


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Build a Telegram-ready merge authorization notification packet "
                    "from PR gate results. Read-only — does not send, merge, or dispatch."
    )
    p.add_argument(
        "--merge-ready-packet",
        type=Path,
        help="Path to a pre-assembled MERGE_READY_PACKET.json "
             "(alternative to individual --pr-number, --head-sha, etc.)",
    )
    p.add_argument(
        "--controller-run-packet",
        type=Path,
        help="Path to CONTROLLER_RUN_PACKET.json from pr_gate_controller.py "
             "(used to extract gate summary in merge-ready-packet mode).",
    )
    p.add_argument(
        "--review-evidence",
        type=Path,
        default=None,
        help="Path to REVIEW_EVIDENCE_PACKET.json. "
             "If supplied, review evidence fields are included in the notification.",
    )
    p.add_argument("--pr-number", type=int, help="PR number")
    p.add_argument("--pr-url", help="Full PR URL")
    p.add_argument("--head-sha", help="Full 40-character git commit SHA")
    p.add_argument("--base-branch", default="main", help="Base branch (default: main)")
    p.add_argument("--ci-status", default="unknown", help="CI status (green/red/pending)")
    p.add_argument(
        "--codex-status", default="unknown",
        help="Codex review status (clean/pending/needs_fixes)"
    )
    p.add_argument(
        "--fallback-review-status", default="unknown",
        help="Fallback review status (clean/needs_fixes/none)"
    )
    p.add_argument(
        "--reviewer-status", default="unknown",
        help="Human reviewer status (clean/approved/not_required_with_reason/pending/needs_changes)"
    )
    p.add_argument(
        "--scope-status", default="unknown",
        help="Scope status (clean/dirty)"
    )
    p.add_argument(
        "--mergeable", action="store_true",
        help="Pass if PR is mergeable (no conflicts)"
    )
    p.add_argument(
        "--changed-file", action="append", default=[],
        dest="changed_files",
        help="Changed file; may be repeated"
    )
    p.add_argument(
        "--output-json", required=True, type=Path,
        help="Output path for MERGE_READY_NOTIFICATION.json"
    )
    p.add_argument(
        "--output-md", required=True, type=Path,
        help="Output path for MERGE_READY_NOTIFICATION.md"
    )
    return p


def _packet_mode(args: argparse.Namespace) -> int:
    """Handle --merge-ready-packet + --controller-run-packet mode."""
    if not args.merge_ready_packet:
        raise ValueError("--merge-ready-packet is required in packet mode")
    if not args.controller_run_packet:
        raise ValueError("--controller-run-packet is required in packet mode")

    mrp = _load_json(args.merge_ready_packet)
    crp = _load_json(args.controller_run_packet)
    review_evidence = _load_review_evidence(args.review_evidence)

    pr_info = mrp.get("pr", {})
    gate = crp.get("result", {})

    pr_number = pr_info.get("number") or mrp.get("pr_number")
    pr_url = pr_info.get("url") or mrp.get("pr_url")
    head_sha = pr_info.get("head_sha") or mrp.get("head_sha")
    base_branch = pr_info.get("base_branch") or mrp.get("base_branch", "main")

    # Old MERGE_READY_PACKET (PR #193) uses "reviewed_clean" and has no scope_status.
    # Normalize to new format for compatibility.
    raw_codex = gate.get("codex_status") or mrp.get("codex_status", "unknown")
    codex_status = "clean" if raw_codex == "reviewed_clean" else raw_codex

    # scope_status: old packets have none; sentinel None means "not tracked by caller"
    raw_scope = mrp.get("scope_status", None)
    scope_status = raw_scope if raw_scope is not None else None  # None = absent

    ci_status = gate.get("ci_status") or mrp.get("ci_status", "unknown")
    reviewer_status = gate.get("reviewer_status") or mrp.get("reviewer_status", "unknown")
    mergeable = mrp.get("mergeable", False)
    changed_files = mrp.get("changed_files", [])
    fallback = mrp.get("fallback_review_status", "unknown")

    try:
        build_notification(
            pr_number=pr_number,
            pr_url=pr_url,
            head_sha=head_sha,
            base_branch=base_branch,
            ci_status=ci_status,
            codex_status=codex_status,
            fallback_review_status=fallback,
            reviewer_status=reviewer_status,
            scope_status=scope_status,
            mergeable=mergeable,
            changed_files=changed_files,
            output_json_path=args.output_json,
            output_md_path=args.output_md,
            review_evidence=review_evidence,
        )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)

    # Reject hermes output paths
    for path in [args.output_json, args.output_md]:
        try:
            _reject_hermes_path(path)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    # Validate head_sha format
    head_sha = getattr(args, "head_sha", None) or (
        _load_json(args.merge_ready_packet).get("head_sha") if args.merge_ready_packet else None
    )
    if head_sha and not _is_valid_sha(head_sha):
        print(
            f"ERROR: head-sha must be a full 40-character SHA, got: {head_sha}",
            file=sys.stderr,
        )
        return 1

    if args.merge_ready_packet and args.controller_run_packet:
        return _packet_mode(args)

    # CLI parameter mode
    missing = []
    for field, value in [
        ("--pr-number", args.pr_number),
        ("--pr-url", args.pr_url),
        ("--head-sha", args.head_sha),
    ]:
        if not value:
            missing.append(field)
    if missing:
        print(f"ERROR: missing required arguments: {', '.join(missing)}", file=sys.stderr)
        return 1

    if not _is_valid_sha(args.head_sha):
        print(
            f"ERROR: --head-sha must be a full 40-character SHA, got: {args.head_sha}",
            file=sys.stderr,
        )
        return 1

    if args.ci_status == "green" and args.scope_status != "clean":
        print(
            "WARNING: CI is green but scope is not clean — marking not_merge_ready",
            file=sys.stderr,
        )

    review_evidence = _load_review_evidence(args.review_evidence)

    try:
        packet = build_notification(
            pr_number=args.pr_number,
            pr_url=args.pr_url,
            head_sha=args.head_sha,
            base_branch=args.base_branch,
            ci_status=args.ci_status,
            codex_status=args.codex_status,
            fallback_review_status=args.fallback_review_status,
            reviewer_status=args.reviewer_status,
            scope_status=args.scope_status,
            mergeable=args.mergeable,
            changed_files=args.changed_files,
            output_json_path=args.output_json,
            output_md_path=args.output_md,
            review_evidence=review_evidence,
        )
        print(f"[notify] output: {args.output_json}")
        print(f"[notify] recommendation: {packet['recommendation']}")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
