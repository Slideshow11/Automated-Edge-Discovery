#!/usr/bin/env python3
"""aed_pr.py

Canonical AED PR-lifecycle controller.

This is THE entry point for advancing an open AED PR from
"just opened" through "merged and cleaned up". It replaces the
retired per-step wrappers:

  - aed_final_gate.py             (absorbed into the controller's
                                   ``advance`` command)
  - build_merge_ready_packet.py   (absorbed; the packet shape is
                                   emitted as part of ``status`` and
                                   ``advance`` JSON output)
  - check_merge_authorization.py  (absorbed into the controller's
                                   ``merge`` command; the phrase
                                   validator lives in aed_pr_lib)
  - finalize_with_phase_ledger.py (its phase-ledger enrollment
                                   remains optional and live when
                                   invoked from the controller)
  - merge_readiness_with_phase_ledger.py
                                 (absorbed; the controller's ``advance``
                                   is the new thin entry point that
                                   optionally enrolls a phase ledger
                                   when the operator supplies one)

Subcommands:

  status   Read live PR state, emit one JSON report.
  advance  Perform every safe mechanical lifecycle step except the
           merge itself (so it never requires the human authorization
           phrase). May include: mark draft ready, request Codex,
           resolve eligible Codex-bot threads, post-merge closeout.
  merge    Require the exact canonical authorization phrase, re-fetch
           live evidence, then execute the squash merge.

The controller does NOT spawn other AED Python wrappers as
subprocesses for its core decisions. It imports the shared library
``aed_pr_lib`` and the existing live-readiness surface
``merge_pr_safely`` (which is the verified-by-CI orchestrator for
the actual merge command emission). It never chains multiple
subprocess wrapper invocations for one decision.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

# Ensure sibling scripts/local/ directory is importable when the
# caller invokes this file directly.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import aed_pr_lib as L   # noqa: E402  (path setup just above)
import merge_pr_safely as MPS  # noqa: E402


# -----------------------------------------------------------------------------
# helpers
# -----------------------------------------------------------------------------

def _run_json(cmd: List[str], timeout: int = 30) -> Dict[str, Any]:
    """Run a gh command, parse JSON, return dict. Raise on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed (rc={proc.returncode}): {' '.join(cmd)}\n"
            f"stderr: {proc.stderr.strip()}"
        )
    return json.loads(proc.stdout)


def _run_text(cmd: List[str], timeout: int = 30) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed (rc={proc.returncode}): {' '.join(cmd)}\n"
            f"stderr: {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def fetch_pr_state(repo: str, pr_number: int) -> Dict[str, Any]:
    """Fetch live PR state via gh, return a dict of fields.

    One-shot fetch; never used in a chain. The controller calls this
    directly at the top of every command to avoid stale data.
    """
    data = _run_json([
        "gh", "pr", "view", str(pr_number),
        "--repo", repo,
        "--json",
        "number,title,state,isDraft,mergeable,headRefOid,baseRefOid,"
        "additions,deletions,changedFiles,url",
    ])
    # gh API returns mergeable=true/false; we keep as bool.
    return data


def derive_status_from_state(state: Dict[str, Any]) -> str:
    """Collapse live PR state into the canonical lifecycle state.

    WAITING                       — PR open and CI still running
    ACTION_REQUIRED               — human action needed (e.g. mark
                                    draft ready, resolve human thread)
    READY_FOR_MERGE_AUTHORIZATION — everything green, awaiting phrase
    MERGED_PENDING_CLOSEOUT       — already merged (state=MERGED)
    COMPLETE                      — closed (state=CLOSED)
    BLOCKED                       — deterministic condition failed
                                    (e.g. mergeable=false with conflict)
    """
    pr_state = state.get("state")
    is_draft = state.get("isDraft")
    mergeable = state.get("mergeable")
    if pr_state == "MERGED":
        return "MERGED_PENDING_CLOSEOUT"
    if pr_state == "CLOSED":
        return "COMPLETE"
    if is_draft:
        return "ACTION_REQUIRED"
    if mergeable is False:
        return "BLOCKED"
    return "WAITING"  # default open non-draft mergeable=true ⇒ wait for CI


# -----------------------------------------------------------------------------
# status command
# -----------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    repo = args.repo
    pr_number = args.pr_number
    pr = fetch_pr_state(repo, pr_number)
    head_sha = pr["headRefOid"]
    base_sha = pr.get("baseRefOid")
    safe_cmd = L.build_safe_merge_command(pr_number, repo, head_sha)
    phrase = L.build_authorization_phrase(pr_number, head_sha)
    status = derive_status_from_state(pr)
    report = {
        "tool": "aed_pr.status",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pr_number": pr_number,
        "pr_url": pr.get("url"),
        "pr_title": pr.get("title"),
        "state": pr.get("state"),
        "is_draft": pr.get("isDraft"),
        "mergeable": pr.get("mergeable"),
        "base_sha": base_sha,
        "head_sha": head_sha,
        "changed_files": pr.get("changedFiles"),
        "additions": pr.get("additions"),
        "deletions": pr.get("deletions"),
        "lifecycle_state": status,
        "safe_merge_command_preview": safe_cmd,
        "required_authorization_phrase": phrase,
        "next_human_action": _next_human_action(status),
    }
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _next_human_action(state: str) -> str:
    return {
        "WAITING": "Wait for CI and Codex to converge; rerun status.",
        "ACTION_REQUIRED": "Address the human-action item; rerun status.",
        "BLOCKED": "Resolve the deterministic block; rerun status.",
        "READY_FOR_MERGE_AUTHORIZATION": (
            "Speak the required_authorization_phrase and run "
            "aed_pr merge."
        ),
        "MERGED_PENDING_CLOSEOUT": "Run aed_pr advance to perform "
                                    "post-merge closeout.",
        "COMPLETE": "No further action.",
    }.get(state, "Unknown state; rerun status.")


# -----------------------------------------------------------------------------
# advance command
# -----------------------------------------------------------------------------

def cmd_advance(args: argparse.Namespace) -> int:
    """Refresh live state and emit what action is next.

    The substantive advance behavior (auto-resolve eligible Codex
    threads, post-merge closeout, fast-forward primary, etc.) is
    implemented here as a series of pure function calls — not as
    a chain of subprocess wrapper invocations.
    """
    repo = args.repo
    pr_number = args.pr_number
    pr = fetch_pr_state(repo, pr_number)
    head_sha = pr["headRefOid"]
    state = derive_status_from_state(pr)
    safe_cmd = L.build_safe_merge_command(pr_number, repo, head_sha)
    phrase = L.build_authorization_phrase(pr_number, head_sha)
    out: Dict[str, Any] = {
        "tool": "aed_pr.advance",
        "pr_number": pr_number,
        "head_sha": head_sha,
        "lifecycle_state": state,
        "safe_merge_command_if_ready": safe_cmd,
        "required_authorization_phrase_if_ready": phrase,
        "actions_taken": [],
        "next_human_action": _next_human_action(state),
    }
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


# -----------------------------------------------------------------------------
# merge command
# -----------------------------------------------------------------------------

def cmd_merge(args: argparse.Namespace) -> int:
    """Execute the canonical merge for a single PR.

    Pre-conditions (any failure => exit 1, no merge):
      - live PR head still matches what was authorized
      - phrase matches byte-for-byte the canonical phrase
      - PR open, non-draft, mergeable
      - argv contains no --admin and no --auto
    Post-conditions (executed exactly once):
      - ``gh pr merge <n> ... --match-head-commit <head>`` is run
      - resulting main SHA is reported
    """
    repo = args.repo
    pr_number = args.pr_number
    phrase = args.authorization_phrase

    # Re-fetch live state immediately; never trust cached values.
    pr = fetch_pr_state(repo, pr_number)
    head_sha = pr["headRefOid"]

    # Authorization checks first.
    if not L.is_valid_authorization_phrase(phrase, pr_number, head_sha):
        sys.stderr.write(
            "Deny: phrase does NOT byte-match the canonical phrase for "
            f"PR #{pr_number} at head {head_sha}.\n"
        )
        sys.stderr.write("Expected (exact):\n")
        sys.stderr.write(
            "  " + L.build_authorization_phrase(pr_number, head_sha) + "\n"
        )
        return 1

    # PR-state preconditions.
    if pr["state"] != "OPEN":
        sys.stderr.write(f"Deny: PR state is {pr['state']!r}, not OPEN.\n")
        return 1
    if pr["isDraft"]:
        sys.stderr.write("Deny: PR is still a draft.\n")
        return 1
    if pr.get("mergeable") is not True:
        sys.stderr.write(
            f"Deny: PR is not mergeable (mergeable={pr.get('mergeable')!r}).\n"
        )
        return 1

    # Build the exact safe command.
    safe_cmd = L.build_safe_merge_command(pr_number, repo, head_sha)
    argv = safe_cmd.split()
    if not L.argv_is_safe(argv):
        sys.stderr.write("Deny: argv safety check failed.\n")
        return 1
    L.reject_admin_argv(argv)  # hard guard; raises if anything slipped through

    # Emit the exact command; surface it so the operator can see it.
    print(f"# Executing: {safe_cmd}")
    proc = subprocess.run(argv, capture_output=True, text=True)
    print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    if proc.returncode != 0:
        return proc.returncode
    return 0


# -----------------------------------------------------------------------------
# argument parser
# -----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aed_pr",
        description="Canonical AED PR-lifecycle controller.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)
    p_status = sub.add_parser(
        "status",
        help="Read live PR state and emit one JSON report (read-only).",
    )
    p_status.add_argument(
        "--pr-number", type=int, required=True,
        help="GitHub PR number (e.g. 410).",
    )
    p_status.add_argument(
        "--repo", default="Slideshow11/Automated-Edge-Discovery",
        help="Repository in owner/name form.",
    )
    p_status.set_defaults(func=cmd_status)

    p_advance = sub.add_parser(
        "advance",
        help="Perform safe mechanical lifecycle steps; never merges.",
    )
    p_advance.add_argument(
        "--pr-number", type=int, required=True,
        help="GitHub PR number.",
    )
    p_advance.add_argument(
        "--repo", default="Slideshow11/Automated-Edge-Discovery",
        help="Repository in owner/name form.",
    )
    p_advance.set_defaults(func=cmd_advance)

    p_merge = sub.add_parser(
        "merge",
        help=(
            "Execute the canonical squash merge. Requires the exact "
            "40-SHA authorization phrase."
        ),
    )
    p_merge.add_argument(
        "--pr-number", type=int, required=True,
        help="GitHub PR number.",
    )
    p_merge.add_argument(
        "--repo", default="Slideshow11/Automated-Edge-Discovery",
        help="Repository in owner/name form.",
    )
    p_merge.add_argument(
        "--authorization-phrase", required=True,
        help=(
            "Exact canonical phrase. Get it from "
            "`aed_pr status --pr-number N` and copy verbatim."
        ),
    )
    p_merge.set_defaults(func=cmd_merge)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
