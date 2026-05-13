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
import importlib.util
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


# ── Safety constants ──────────────────────────────────────────────────────────

HERMES_PREFIX = "/home/max/.hermes"
FORBIDDEN_PREFIXES = (HERMES_PREFIX,)
PACKET_KIND = "aed.merge_ready.v1"
REVIEW_EVIDENCE_KIND = "aed.pr_gate.review_evidence.v1"
SCHEMA_VERSION = 1


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
        "authorization_head_sha": head_sha,
        "head_sha_source": "packet",
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


# ── Review Evidence Packet ─────────────────────────────────────────────────────

ALLOWED_REVIEW_SOURCES = ("github_codex", "codex_cli_fallback", "reviewer", "none")
ALLOWED_REVIEW_STATUSES = ("clean", "suggestions", "pending", "unavailable", "stale", "missing", "unknown")
ALLOWED_SCOPE_STATUSES = ("clean", "dirty", "unknown")
REQUIRED_CI_JOBS = ("test", "validator", "governance-validators", "pr-gate-live-smoke")


# ── Scope checker ─────────────────────────────────────────────────────────────

def _run_scope_check(
    changed_files: list[str],
    allowed_files: list[str],
    forbidden_files: list[str],
) -> dict:
    """Run check_pr_scope.py as an imported module and return its result dict.

    Falls back to a synthetic error packet if the module cannot be loaded
    or check_scope() raises — never lets a scope-check failure silently pass.
    """
    try:
        spec = importlib.util.spec_from_file_location(
            "check_pr_scope",
            str(Path(__file__).parent / "check_pr_scope.py"),
        )
        if spec is None or spec.loader is None:
            raise ImportError("spec is None")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.check_scope(
            changed_files=list(changed_files),
            allowed_files=list(allowed_files),
            forbidden_files=list(forbidden_files),
        )
    except Exception:
        # scope check unavailable — return a blocking packet so merge_allowed=False
        return {
            "packet_kind": "aed.pr_gate.scope_check.v1",
            "schema_version": 1,
            "scope_status": "unknown",
            "passed": False,
            "blockers": ["scope_check_unavailable"],
            "out_of_scope_files": [],
            "forbidden_files_touched": [],
            "changed_files": list(changed_files),
            "allowed_files": list(allowed_files),
            "forbidden_files": list(forbidden_files),
        }


def build_review_evidence_packet(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    current_head_sha: str,
    reviewed_head_sha: str,
    review_source: str,
    review_status: str,
    codex_github_review_id: str | None = None,
    codex_cli_fallback_id: str | None = None,
    ci_status: str = "unknown",
    ci_required_jobs: list[str] | None = None,
    changed_files: list[str] | None = None,
    allowed_files: list[str] | None = None,
    forbidden_files: list[str] | None = None,
    mergeable: bool = True,
) -> dict:
    """Build an aed.pr_gate.review_evidence.v1 packet.

    Rules:
      - review_is_stale is True when reviewed_head_sha != current_head_sha.
      - merge_allowed is False when review_is_stale is True.
      - GitHub Codex evidence only counts if review_source==github_codex
        AND reviewed_head_sha==current_head_sha AND review_status==clean.
      - Codex CLI fallback evidence only counts if review_source==codex_cli_fallback
        AND reviewed_head_sha==current_head_sha AND review_status==clean.
      - Missing/pending/suggestions review evidence sets merge_allowed=False.
      - ci_all_green must be True for merge_allowed.
      - scope_status must be clean for merge_allowed.
      - Any changed file outside allowed_files sets merge_allowed=False.
    """
    now = datetime.now(timezone.utc)

    # Derived fields
    review_is_stale = reviewed_head_sha != current_head_sha

    ci_required_jobs = ci_required_jobs or list(REQUIRED_CI_JOBS)
    changed_files = changed_files or []
    allowed_files = allowed_files or []
    forbidden_files = forbidden_files or []

    # CI: all_green is true only if ci_status is "green" and all required jobs present
    ci_all_green = ci_status == "green"

    # Scope: use the authoritative mechanical scope checker
    scope_result = _run_scope_check(changed_files, allowed_files, forbidden_files)
    scope_status = scope_result.get("scope_status", "unknown")
    scope_passed = scope_result.get("passed", False)
    scope_blockers = scope_result.get("blockers", [])
    out_of_scope_files = scope_result.get("out_of_scope_files", [])
    forbidden_files_touched = scope_result.get("forbidden_files_touched", [])

    # Merge allowed logic
    blockers: list[str] = []

    if review_is_stale:
        blockers.append("review is stale: reviewed_head_sha != current_head_sha")

    if review_source not in ALLOWED_REVIEW_SOURCES:
        blockers.append(f"review_source '{review_source}' is not valid")
    elif review_source in ("none", "", None):
        blockers.append("missing review evidence: review_source is 'none' or empty")
    elif review_status == "missing":
        blockers.append("missing review evidence: review_status is 'missing'")
    elif review_status == "unknown":
        blockers.append("missing review evidence: review_status is 'unknown'")
    elif review_status == "pending":
        blockers.append("review is pending")
    elif review_status == "suggestions":
        blockers.append("review has suggestions")
    elif review_status == "stale":
        blockers.append("review status is stale")

    # merge_allowed requires review_status == "clean" regardless of source
    if review_status != "clean":
        blockers.append(f"review_status is '{review_status}', not 'clean' — merge_allowed=False for all sources")

    if not ci_all_green:
        blockers.append(f"CI is not all-green: ci_status='{ci_status}'")

    if scope_status != "clean":
        for b in scope_blockers:
            blockers.append(f"scope: {b}")

    if not mergeable:
        blockers.append("PR is not mergeable")

    merge_allowed = len(blockers) == 0

    # Recommended merge command
    recommended_merge_command = (
        f"gh pr merge {pr_number} "
        f"--repo {repo_owner}/{repo_name} "
        f"--squash --delete-branch --match-head-commit {current_head_sha}"
    )

    packet = {
        "packet_kind": REVIEW_EVIDENCE_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "repo_owner": repo_owner,
        "repo_name": repo_name,
        "pr_number": pr_number,
        "current_head_sha": current_head_sha,
        "reviewed_head_sha": reviewed_head_sha,
        "review_source": review_source,
        "review_status": review_status,
        "review_is_stale": review_is_stale,
        "codex_github_review_id": codex_github_review_id,
        "codex_cli_fallback_id": codex_cli_fallback_id,
        "ci_status": ci_status,
        "ci_required_jobs": list(ci_required_jobs),
        "ci_all_green": ci_all_green,
        "changed_files": list(changed_files),
        "allowed_files": list(allowed_files),
        "scope_status": scope_status,
        "scope_passed": scope_passed,
        "scope_blockers": list(scope_blockers),
        "out_of_scope_files": list(out_of_scope_files),
        "forbidden_files_touched": list(forbidden_files_touched),
        "mergeable": mergeable,
        "merge_allowed": merge_allowed,
        "blockers_or_uncertainty": list(blockers),
        "recommended_merge_command": recommended_merge_command,
    }
    return packet


def serialize_review_evidence_packet(packet: dict) -> str:
    """Serialize review evidence packet to stable JSON."""
    return json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def render_review_evidence_markdown(packet: dict) -> str:
    """Render human-readable review evidence packet."""
    lines = [
        "# REVIEW EVIDENCE PACKET",
        "",
        f"**Kind:** `{packet.get('packet_kind', '?')}`",
        f"**Schema:** v{packet.get('schema_version', '?')}",
        f"**Generated:** {packet.get('generated_at', '?')}",
        f"**Repo:** `{packet.get('repo_owner', '?')}/{packet.get('repo_name', '?')}`",
        f"**PR:** #{packet.get('pr_number', '?')}",
        "",
        "## Head SHAs",
        f"- **current_head_sha:** `{packet.get('current_head_sha', '?')}`",
        f"- **reviewed_head_sha:** `{packet.get('reviewed_head_sha', '?')}`",
        f"- **review_is_stale:** `{packet.get('review_is_stale', '?')}`",
        "",
        "## Review Evidence",
        f"- **review_source:** `{packet.get('review_source', '?')}`",
        f"- **review_status:** `{packet.get('review_status', '?')}`",
        f"- **codex_github_review_id:** `{packet.get('codex_github_review_id', '?')}`",
        f"- **codex_cli_fallback_id:** `{packet.get('codex_cli_fallback_id', '?')}`",
        "",
        "## CI",
        f"- **ci_status:** `{packet.get('ci_status', '?')}`",
        f"- **ci_all_green:** `{packet.get('ci_all_green', '?')}`",
        f"- **ci_required_jobs:** `{', '.join(packet.get('ci_required_jobs', []))}`",
        "",
        "## Scope",
        f"- **scope_status:** `{packet.get('scope_status', '?')}`",
        f"- **changed_files:** `{len(packet.get('changed_files', []))} files`",
        f"- **allowed_files:** `{len(packet.get('allowed_files', []))} files`",
        "",
        "## Merge Readiness",
        f"- **mergeable:** `{packet.get('mergeable', '?')}`",
        f"- **merge_allowed:** `{packet.get('merge_allowed', '?')}`",
        "",
    ]

    blockers = packet.get("blockers_or_uncertainty", [])
    if blockers:
        lines.append("## Blockers")
        for b in blockers:
            lines.append(f"  - ❌ {b}")
        lines.append("")
    else:
        lines.append("## Blockers\n  - (none)\n")

    lines += [
        "## Merge Command",
        f"```bash",
        f"{packet.get('recommended_merge_command', '?')}",
        f"```",
    ]
    return "\n".join(lines)


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
        description="Build a MERGE_READY_PACKET or REVIEW_EVIDENCE_PACKET from PR gate data. "
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
    # Review evidence sub-command
    p.add_argument("--build-review-evidence", action="store_true",
                    help="Build a REVIEW_EVIDENCE_PACKET instead of MERGE_READY_PACKET")
    p.add_argument("--repo-owner", type=str, default="Slideshow11")
    p.add_argument("--repo-name", type=str, default="Automated-Edge-Discovery")
    p.add_argument("--reviewed-head-sha", type=str, default=None,
                    help="SHA that was reviewed (defaults to --head-sha)")
    p.add_argument("--review-source", type=str, default="none",
                    choices=list(ALLOWED_REVIEW_SOURCES))
    p.add_argument("--review-status", type=str, default="unknown",
                    choices=list(ALLOWED_REVIEW_STATUSES))
    p.add_argument("--codex-github-review-id", type=str, default=None)
    p.add_argument("--codex-cli-fallback-id", type=str, default=None)
    p.add_argument("--review-evidence-output-json", type=str, default=None)
    p.add_argument("--review-evidence-output-md", type=str, default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Handle review evidence packet
    if args.build_review_evidence:
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

        current_head_sha = args.head_sha
        reviewed_head_sha = args.reviewed_head_sha if args.reviewed_head_sha else current_head_sha

        packet = build_review_evidence_packet(
            repo_owner=args.repo_owner,
            repo_name=args.repo_name,
            pr_number=args.pr_number,
            current_head_sha=current_head_sha,
            reviewed_head_sha=reviewed_head_sha,
            review_source=args.review_source,
            review_status=args.review_status,
            codex_github_review_id=args.codex_github_review_id,
            codex_cli_fallback_id=args.codex_cli_fallback_id,
            ci_status=args.ci_status,
            ci_required_jobs=list(REQUIRED_CI_JOBS),
            changed_files=changed_files,
            allowed_files=allowed_files,
            mergeable=mergeable,
        )

        json_bytes = serialize_review_evidence_packet(packet).encode("utf-8")

        if args.review_evidence_output_json:
            Path(args.review_evidence_output_json).write_bytes(json_bytes)
            print(f"Review evidence JSON written to {args.review_evidence_output_json}", file=sys.stderr)

        if args.review_evidence_output_md:
            md = render_review_evidence_markdown(packet)
            Path(args.review_evidence_output_md).write_text(md + "\n", encoding="utf-8")
            print(f"Review evidence Markdown written to {args.review_evidence_output_md}", file=sys.stderr)

        if not args.review_evidence_output_json and not args.review_evidence_output_md:
            print(serialize_review_evidence_packet(packet))

        return 0

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