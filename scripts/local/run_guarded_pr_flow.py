#!/usr/bin/env python3
"""
run_guarded_pr_flow.py — v1 skeleton
=====================================
Audit-only orchestrator for the standard AED guarded PR flow.

v1 writes a combined JSON/Markdown skeleton report declaring the planned gate
order. It does NOT call gate subprocesses, merge, resolve threads, mutate
GitHub state, use --admin/--auto, change workflows, or alter branch protection.

Planned gate order (implemented in future passes):
  1. scope_guard     — local git scope auditor
  2. review_threads  — read-only review thread auditor
  3. waiter          — PR readiness waiter
  4. merge_verifier  — safe merge command verifier

Usage:
    python3 scripts/local/run_guarded_pr_flow.py \\
        --repo Slideshow11/Automated-Edge-Discovery \\
        --repo-root /path/to/repo \\
        --pr-number 375 \\
        --output-dir /tmp/aed_runs/pr375 \\
        --output-json /tmp/aed_runs/pr375/flow.json \\
        --output-md /tmp/aed_runs/pr375/flow.md

Exit codes:
    0  — report written (any status)
    1  — ERROR_TOOL_FAILURE
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------
STATUS_GUARD_FLOW_SKELETON_READY = "GUARD_FLOW_SKELETON_READY"
STATUS_ERROR_TOOL_FAILURE = "ERROR_TOOL_FAILURE"

# Forbidden argv tokens (exact match only)
_FORBIDDEN_TOKENS = frozenset({"--admin", "--auto"})

# Planned gate order (future passes wire these in)
PLANNED_GATE_ORDER = [
    "scope_guard",
    "review_threads",
    "waiter",
    "merge_verifier",
]


# ---------------------------------------------------------------------------
# Admin guard
# ---------------------------------------------------------------------------

def reject_forbidden_tokens(argv: list[str]) -> None:
    """Raise ValueError if any forbidden argv token is present."""
    for token in argv:
        if token in _FORBIDDEN_TOKENS:
            raise ValueError(f"Forbidden token in argv: {token}")


# ---------------------------------------------------------------------------
# Repo-root validation
# ---------------------------------------------------------------------------

def validate_repo_root(repo_root: str) -> tuple[bool, str]:
    """
    Check that repo_root exists and is a git worktree.
    Returns (ok, detail).
    """
    path = Path(repo_root).resolve()
    if not path.exists():
        return False, f"repo-root does not exist: {repo_root}"
    import subprocess

    proc = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        shell=False,
        timeout=10,
    )
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        return False, f"repo-root is not a git worktree: {repo_root}"
    return True, "ok"


# ---------------------------------------------------------------------------
# Report building
# ---------------------------------------------------------------------------

def build_report(
    status: str,
    repo: str,
    repo_root: str,
    pr_number: int,
    expected_head: str | None,
    output_dir: str,
    started_at: str,
    finished_at: str,
    elapsed_seconds: float,
) -> dict:
    """Build the skeleton report dict."""
    return {
        "status": status,
        "repo": repo,
        "repo_root": repo_root,
        "pr_number": pr_number,
        "expected_head": expected_head or "",
        "output_dir": output_dir,
        "gate_order": PLANNED_GATE_ORDER,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": round(elapsed_seconds, 3),
        # Safety invariants
        "audit_only": True,
        "merge_executed": False,
        "mutated_github": False,
        "used_admin": False,
        "used_auto": False,
        "comments_deleted": False,
        "reviews_dismissed": False,
        "threads_resolved": False,
        "workflows_changed": False,
        "branch_protection_changed": False,
    }


def write_json_report(path: str, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def write_md_report(path: str, data: dict) -> None:
    lines = [
        "# Guarded PR Flow Report — v1 Skeleton",
        "",
        f"**Status**: `{data['status']}`",
        "",
        "## Summary",
        "",
        f"- **Repo**: `{data['repo']}`",
        f"- **PR**: #{data['pr_number']}",
        f"- **Expected head**: `{data['expected_head'] or '(none)'}`",
        f"- **Started at**: {data['started_at']}",
        f"- **Finished at**: {data['finished_at']}",
        f"- **Elapsed seconds**: {data['elapsed_seconds']}",
        "",
        "## Planned Gate Order",
        "",
    ]
    for i, gate in enumerate(data["gate_order"], 1):
        lines.append(f"  {i}. `{gate}`")

    lines.extend([
        "",
        "## Safety Invariants",
        "",
        f"- **audit_only**: {data['audit_only']}",
        f"- **merge_executed**: {data['merge_executed']}",
        f"- **mutated_github**: {data['mutated_github']}",
        f"- **used_admin**: {data['used_admin']}",
        f"- **used_auto**: {data['used_auto']}",
        f"- **comments_deleted**: {data['comments_deleted']}",
        f"- **reviews_dismissed**: {data['reviews_dismissed']}",
        f"- **threads_resolved**: {data['threads_resolved']}",
        f"- **workflows_changed**: {data['workflows_changed']}",
        f"- **branch_protection_changed**: {data['branch_protection_changed']}",
        "",
        "## Gate Status",
        "",
        "*This is a v1 skeleton. No gate subprocesses are called yet.*",
        "",
        "## Notes",
        "",
        "**v1 skeleton does not merge.**",
        "",
        "This v1 writes the skeleton report and declares the planned gate order.",
        "Future passes will wire in the actual gate subprocesses:",
        "  scope_guard, review_threads, waiter, merge_verifier.",
        "",
        "v1 does NOT:",
        "  - use --admin or --auto",
        "  - call GitHub write APIs",
        "  - call GraphQL mutations",
        "  - resolve threads",
        "  - merge",
        "  - change workflows",
        "  - alter branch protection",
        "  - run live Claude or autocoder batch",
        "  - mutate Hermes or kanban state",
        "",
        "*Report generated by run_guarded_pr_flow.py v1 skeleton.*",
    ])

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Guarded PR flow orchestrator — v1 skeleton (audit-only, no gate calls).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--repo-root", required=True, help="Absolute path to AED repo root")
    parser.add_argument("--pr-number", required=True, type=int, help="PR number")
    parser.add_argument("--expected-head", default=None, help="Expected head SHA (optional)")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--output-json", required=True, help="Output JSON path")
    parser.add_argument("--output-md", required=True, help="Output Markdown path")
    parser.add_argument("--skip-waiter", action="store_true", default=False,
                        help="Skip waiter gate (future use)")
    parser.add_argument("--skip-merge-verifier", action="store_true", default=False,
                        help="Skip merge verifier gate (future use)")
    args = parser.parse_args()

    # ---- Forbidden token guard ----
    try:
        reject_forbidden_tokens(sys.argv)
    except ValueError as e:
        report = build_report(
            status=STATUS_ERROR_TOOL_FAILURE,
            repo=args.repo,
            repo_root=args.repo_root,
            pr_number=args.pr_number,
            expected_head=args.expected_head,
            output_dir=args.output_dir,
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
            elapsed_seconds=0.0,
        )
        report["error"] = str(e)
        write_json_report(args.output_json, report)
        write_md_report(args.output_md, report)
        print(f"ERROR_TOOL_FAILURE: {e}", file=sys.stderr)
        return 1

    started_at = datetime.now(timezone.utc).isoformat()

    # ---- Repo-root validation ----
    ok, detail = validate_repo_root(args.repo_root)
    if not ok:
        finished_at = datetime.now(timezone.utc).isoformat()
        elapsed = 0.0
        report = build_report(
            status=STATUS_ERROR_TOOL_FAILURE,
            repo=args.repo,
            repo_root=args.repo_root,
            pr_number=args.pr_number,
            expected_head=args.expected_head,
            output_dir=args.output_dir,
            started_at=started_at,
            finished_at=finished_at,
            elapsed_seconds=elapsed,
        )
        report["error"] = detail
        write_json_report(args.output_json, report)
        write_md_report(args.output_md, report)
        print(f"ERROR_TOOL_FAILURE: {detail}", file=sys.stderr)
        return 1

    # ---- Create output dir ----
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    # ---- Build and write reports ----
    finished_at = datetime.now(timezone.utc).isoformat()
    elapsed = 0.0

    report = build_report(
        status=STATUS_GUARD_FLOW_SKELETON_READY,
        repo=args.repo,
        repo_root=args.repo_root,
        pr_number=args.pr_number,
        expected_head=args.expected_head,
        output_dir=args.output_dir,
        started_at=started_at,
        finished_at=finished_at,
        elapsed_seconds=elapsed,
    )

    write_json_report(args.output_json, report)
    write_md_report(args.output_md, report)

    print(f"status={report['status']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())