#!/usr/bin/env python3
"""
verify_final_head_merge_command.py

Fetches the canonical PR head SHA from GitHub via `gh pr view` immediately
before merge authorization, and generates the exact safe merge command using
that canonical SHA.

Core rule: any value used to mutate GitHub must come from GitHub at the moment
of mutation, not from a report summary.

Usage:
    python3 scripts/local/verify_final_head_merge_command.py --pr-number 227
    python3 scripts/local/verify_final_head_merge_command.py \\
        --pr-number 227 --reported-head-sha abc123...

    python3 scripts/local/verify_final_head_merge_command.py \\
        --pr-number 227 --reported-head-sha abc123... --output-json /tmp/verify.json

Exit codes:
    0  — verification complete (recommendation emitted, command printed)
    1  — PR not found, not open, or validation error
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_DEFAULT = "Slideshow11/Automated-Edge-Discovery"
HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def validate_reported_sha(sha: Optional[str]) -> Optional[str]:
    """Validate reported SHA format. Returns error message or None."""
    if sha is None:
        return None
    if not HEX_SHA_RE.match(sha):
        return f"reported-head-sha must be a 40-char hex string, got: {sha!r}"
    return None


def gh_pr_view_json(repo: str, pr_number: int) -> dict:
    """Fetch PR data via `gh pr view --json ...`."""
    fields = [
        "number", "state", "mergeable", "headRefOid", "baseRefOid",
        "title", "url", "changedFiles",
    ]
    fields_arg = ",".join(fields)
    cmd = [
        "gh", "pr", "view", str(pr_number),
        "--repo", repo,
        "--json", fields_arg,
    ]
    rc, stdout, stderr = _run(cmd)
    if rc != 0:
        raise RuntimeError(f"`gh pr view` failed: {stderr.strip()}")
    return json.loads(stdout)


def _run(cmd: list[str]) -> tuple[int, str, str]:
    """Run a command, return (rc, stdout, stderr)."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def build_merge_command(pr_number: int, repo: str, head_sha: str) -> str:
    """Generate the safe merge command using the canonical GitHub head SHA."""
    return (
        f"gh pr merge {pr_number} \\\n"
        f"  --repo {repo} \\\n"
        f"  --squash \\\n"
        f"  --delete-branch \\\n"
        f"  --match-head-commit {head_sha}"
    )


def build_authorization_phrase(pr_number: int, head_sha: str) -> str:
    """Generate the authorization phrase using the canonical GitHub head SHA."""
    return (
        f"I confirm merge PR #{pr_number} at {head_sha} "
        f"using final-head reviewed clean state."
    )


def verify(
    repo: str,
    pr_number: int,
    reported_head_sha: Optional[str],
    require_mergeable: bool,
) -> dict:
    """
    Fetch canonical PR data from GitHub and return a verification result dict.

    Returns:
        {
            "recommendation": "MERGE_READY_CANDIDATE" | "PATCH" | "WAIT" | "BLOCK",
            "pr_number": int,
            "repo": str,
            "title": str,
            "url": str,
            "reported_head_sha": str | None,
            "canonical_head_sha": str,
            "base_sha": str,
            "mergeable": bool | None,
            "state": str,
            "head_sha_matches": bool,
            "authorization_phrase": str,
            "merge_command": str,
            "verification_errors": list[str],
        }
    """
    errors: list[str] = []

    # Fetch canonical data from GitHub
    try:
        pr_data = gh_pr_view_json(repo, pr_number)
    except Exception as e:
        return {
            "recommendation": "BLOCK",
            "pr_number": pr_number,
            "repo": repo,
            "title": "",
            "url": "",
            "reported_head_sha": reported_head_sha,
            "canonical_head_sha": "",
            "base_sha": "",
            "mergeable": None,
            "state": "unknown",
            "head_sha_matches": False,
            "authorization_phrase": "",
            "merge_command": "",
            "verification_errors": [str(e)],
        }

    canonical_head_sha: str = pr_data.get("headRefOid", "")
    base_sha: str = pr_data.get("baseRefOid", "")
    state: str = pr_data.get("state", "").lower()
    mergeable: Optional[bool] = pr_data.get("mergeable")
    title: str = pr_data.get("title", "")
    url: str = pr_data.get("url", "")

    # Validate reported SHA format if provided
    sha_validation_error = validate_reported_sha(reported_head_sha)
    if sha_validation_error:
        errors.append(sha_validation_error)

    # Determine recommendation
    recommendation = _determine_recommendation(
        state=state,
        mergeable=mergeable,
        require_mergeable=require_mergeable,
        reported_head_sha=reported_head_sha,
        canonical_head_sha=canonical_head_sha,
        errors=errors,
    )

    # Build output
    head_sha_matches = (
        (reported_head_sha is not None)
        and (reported_head_sha == canonical_head_sha)
    )

    auth_phrase = build_authorization_phrase(pr_number, canonical_head_sha)
    merge_cmd = build_merge_command(pr_number, repo, canonical_head_sha)

    return {
        "recommendation": recommendation,
        "pr_number": pr_number,
        "repo": repo,
        "title": title,
        "url": url,
        "reported_head_sha": reported_head_sha,
        "canonical_head_sha": canonical_head_sha,
        "base_sha": base_sha,
        "mergeable": mergeable,
        "state": state,
        "head_sha_matches": head_sha_matches,
        "authorization_phrase": auth_phrase,
        "merge_command": merge_cmd,
        "verification_errors": errors,
    }


def _determine_recommendation(
    state: str,
    mergeable: Optional[bool],
    require_mergeable: bool,
    reported_head_sha: Optional[str],
    canonical_head_sha: str,
    errors: list[str],
) -> str:
    # Block on validation errors
    if errors:
        return "BLOCK"

    # Block on non-open PR
    if state != "open":
        return "BLOCK"

    # Block on empty or malformed canonical SHA (should not happen from GitHub
    # but guards against malformed or forged API responses)
    if not canonical_head_sha or not HEX_SHA_RE.match(canonical_head_sha):
        return "BLOCK"

    # Mismatch between reported and canonical = PATCH.
    # Check this before mergeable gating so a stale report SHA surfaces as
    # PATCH even when mergeable is None (unknown) rather than being suppressed.
    if reported_head_sha is not None and reported_head_sha != canonical_head_sha:
        return "PATCH"

    # Block or wait on non-mergeable
    if require_mergeable:
        if mergeable is False:
            return "BLOCK"
        if mergeable is None:
            return "WAIT"

    # Open + mergeable (when required) + SHA matches = candidate
    return "MERGE_READY_CANDIDATE"


def print_result(result: dict) -> None:
    """Print human-readable verification result."""
    print(f"=== Final-Head Merge Command Verifier ===")
    print(f"PR #{result['pr_number']}  |  {result['title']}")
    print(f"URL: {result['url']}")
    print(f"State: {result['state']}  |  Mergeable: {result['mergeable']}")
    print(f"Base SHA: {result['base_sha']}")
    print(f"Canonical head SHA: {result['canonical_head_sha']}")
    print(f"Reported head SHA: {result['reported_head_sha']}")
    print(f"Head SHA matches:  {result['head_sha_matches']}")
    if result["verification_errors"]:
        print(f"Errors: {result['verification_errors']}")
    print()
    print(f"RECOMMENDATION: {result['recommendation']}")
    print()
    print("--- Authorization Phrase ---")
    print(result["authorization_phrase"])
    print()
    print("--- Merge Command ---")
    print(result["merge_command"])


def write_json(result: dict, path: str) -> None:
    """Write result as JSON to path."""
    with open(path, "w") as f:
        json.dump(result, f, indent=2)


def write_markdown(result: dict, path: str) -> None:
    """Write result as Markdown report to path."""
    lines = [
        "# Final-Head Merge Command Verifier",
        "",
        f"**PR:** #{result['pr_number']} — {result['title']}",
        f"**URL:** {result['url']}",
        f"**State:** {result['state']}  |  **Mergeable:** {result['mergeable']}",
        "",
        f"**Base SHA:** `{result['base_sha']}`",
        f"**Canonical head SHA:** `{result['canonical_head_sha']}`",
        f"**Reported head SHA:** `{result['reported_head_sha']}`",
        f"**Head SHA matches:** {result['head_sha_matches']}",
        "",
        f"## Recommendation: `{result['recommendation']}`",
        "",
        "## Authorization Phrase",
        "",
        "```text",
        result["authorization_phrase"],
        "```",
        "",
        "## Merge Command",
        "",
        "```bash",
        result["merge_command"],
        "```",
        "",
    ]
    if result["verification_errors"]:
        lines.append("## Errors")
        for err in result["verification_errors"]:
            lines.append(f"- {err}")
        lines.append("")

    with open(path, "w") as f:
        f.write("\n".join(lines))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify PR head SHA and generate safe merge command "
                    "using canonical GitHub data."
    )
    parser.add_argument(
        "--repo",
        default=REPO_DEFAULT,
        help=f"Repository (default: {REPO_DEFAULT})",
    )
    parser.add_argument(
        "--pr-number",
        type=int,
        required=True,
        help="PR number",
    )
    parser.add_argument(
        "--reported-head-sha",
        help="Reported head SHA from a report (40-char hex). "
             "Mismatch triggers PATCH recommendation.",
    )
    parser.add_argument(
        "--require-mergeable",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require PR to be mergeable before recommending MERGE_READY (default: true)",
    )
    parser.add_argument(
        "--output-json",
        help="Write result as JSON to this path",
    )
    parser.add_argument(
        "--output-md",
        help="Write result as Markdown to this path",
    )

    args = parser.parse_args(argv)

    reported_sha: Optional[str] = args.reported_head_sha
    if reported_sha is not None:
        reported_sha = reported_sha.strip().lower()

    try:
        result = verify(
            repo=args.repo,
            pr_number=args.pr_number,
            reported_head_sha=reported_sha,
            require_mergeable=args.require_mergeable,
        )
    except Exception as e:
        # Fatal error — PR data could not be fetched
        print(f"FATAL: {e}", file=sys.stderr)
        return 1

    print_result(result)

    if args.output_json:
        write_json(result, args.output_json)

    if args.output_md:
        write_markdown(result, args.output_md)

    return 0


if __name__ == "__main__":
    sys.exit(main())