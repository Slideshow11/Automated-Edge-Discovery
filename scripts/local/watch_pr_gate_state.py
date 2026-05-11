#!/usr/bin/env python3
"""Read-only PR gate watchdog for AED.

Reads PR state via classify_pr_gate_state.py and prints a compact
Telegram-friendly summary or JSON packet. Exit codes are deterministic.

Must NOT mutate GitHub, Kanban, repo files, request Codex, or merge.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Import the classifier directly to reuse its types and logic.
# The watchdog is a thin wrapper — it does not reimplement classification.
sys.path.insert(0, str(Path(__file__).parent))
from classify_pr_gate_state import (  # noqa: E402
    CLASSIFICATIONS,
    classify_payloads,
    fetch_live_payloads,
)

EXIT_NETWORK_ERROR = 2
EXIT_ARGUMENT_ERROR = 3

STATE_LABELS = {
    "ci_pending": "ci_pending",
    "ci_failed": "ci_failed",
    "codex_request_needed": "codex_request_needed",
    "codex_pending": "codex_pending",
    "codex_suggestions": "codex_suggestions",
    "codex_clean": "codex_clean",
    "ready_for_reviewer": "ready_for_reviewer",
    "blocked_scope": "blocked_scope",
    "blocked_wrong_base": "blocked_wrong_base",
    "blocked_pr_closed": "blocked_pr_closed",
    "blocked_pr_merged": "blocked_pr_merged",
    "unknown": "unknown",
}

CI_STATE_LABELS = {
    "ci_pending": "pending",
    "ci_failed": "fail",
    "ci_pass": "pass",
}

CODEX_STATE_LABELS = {
    "codex_clean": "clean",
    "codex_pending": "pending",
    "codex_suggestions": "suggestions",
    "codex_request_needed": "needed",
    "codex_clean_but_no_review": "clean",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AED read-only PR gate watchdog. "
        "Prints compact summary and/or JSON. Exit codes are deterministic.",
    )
    parser.add_argument("--repo-owner", required=True, help="GitHub repository owner (e.g. Slideshow11)")
    parser.add_argument("--repo-name", required=True, help="GitHub repository name (e.g. Automated-Edge-Discovery)")
    parser.add_argument("--pr-number", required=True, type=int, help="PR number to watch")
    parser.add_argument("--base-branch", default="main", help="Base branch (default: main)")
    parser.add_argument("--json", action="store_true", help="Print raw JSON packet from classifier")
    parser.add_argument(
        "--compact", action="store_true", help="Print single-line Telegram-friendly summary"
    )
    parser.add_argument(
        "--exit-code-only", action="store_true", help="Exit with code only; no stdout"
    )
    return parser


def _ci_label(classification: str) -> str:
    if classification in ("ci_pending",):
        return "pending"
    if classification in ("ci_failed",):
        return "fail"
    return "pass"


def _codex_label(classification: str) -> str:
    if classification in ("codex_request_needed",):
        return "needed"
    if classification in ("codex_pending",):
        return "pending"
    if classification in ("codex_suggestions",):
        return "suggestions"
    if classification in ("codex_clean",):
        return "clean"
    return "NA"


def build_compact(pr_number: int, classification: str, blockers: list[str]) -> str:
    ci = _ci_label(classification)
    codex = _codex_label(classification)
    blockers_str = ", ".join(blockers) if blockers else "none"
    return f"[PR #{pr_number}] {classification} · CI={ci} · CODEX={codex} · blockers: {blockers_str}"


def build_telegram_summary(pr_number: int, classification: str, blockers: list[str]) -> str:
    ci = _ci_label(classification)
    codex = _codex_label(classification)
    blockers_str = ", ".join(blockers) if blockers else "none"
    return f"PR #{pr_number} gate: {classification} — CI: {ci}, Codex: {codex}, blockers: {blockers_str}"


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()

    # Override argparse's error to exit with 3 (argument error), not 2.
    def _fail(msg: str) -> None:
        sys.stderr.write(f"{msg}\n")
        sys.exit(EXIT_ARGUMENT_ERROR)

    parser.error = _fail  # type: ignore[assignment]

    args = parser.parse_args(argv)

    try:
        pr, files, check_runs, comments, reviews, reactions = fetch_live_payloads(
            args.repo_owner, args.repo_name, args.pr_number
        )
    except urllib.error.URLError as exc:
        if args.exit_code_only:
            sys.stderr.write(f"network error: {exc}\n")
        else:
            print(f"error: network error contacting GitHub API: {exc}", file=sys.stderr)
        return EXIT_NETWORK_ERROR
    except Exception as exc:
        if args.exit_code_only:
            sys.stderr.write(f"error: {exc}\n")
        else:
            print(f"error: {exc}", file=sys.stderr)
        return EXIT_ARGUMENT_ERROR

    packet = classify_payloads(
        pr=pr,
        changed_files=files,
        check_runs=check_runs,
        issue_comments=comments,
        reviews=reviews,
        allowed_files=[],  # watchdog doesn't restrict files
        expected_head=None,
        codex_bot_login="chatgpt-codex-connector[bot]",
        base_branch=args.base_branch,
        latest_request_reactions=reactions,
    )

    classification = packet.get("classification", "unknown")
    blockers = packet.get("blockers", [])

    if not args.exit_code_only:
        if args.json:
            print(json.dumps(packet, sort_keys=True, indent=2))
        elif args.compact:
            print(build_compact(args.pr_number, classification, blockers))
        else:
            print(build_telegram_summary(args.pr_number, classification, blockers))

    if classification not in CLASSIFICATIONS:
        classification = "unknown"

    return 0


def main(argv: list[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())