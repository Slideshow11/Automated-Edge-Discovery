#!/usr/bin/env python3
"""Read-only AED Tasker input collector.

Collects structured internal repo context for a future AED Tasker agent.
Must NOT call LLMs, mutate GitHub, create Kanban tasks, or make network calls.

Usage:
  python3 scripts/local/aed_tasker_collect_context.py \\
    --repo-root /home/max/Automated-Edge-Discovery \\
    --output-json /tmp/AED_TASKER_CONTEXT.json \\
    --output-md /tmp/AED_TASKER_CONTEXT.md
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Safety constants ──────────────────────────────────────────────────────────

HERMES_PREFIX = "/home/max/.hermes"
FORBIDDEN_OUTPUT_PREFIXES = (HERMES_PREFIX,)

# ── Core collectors ────────────────────────────────────────────────────────────


def git_rev_parse(repo_root: Path, ref: str = "HEAD") -> str:
    """Return the canonical SHA for a ref using git rev-parse."""
    result = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def git_branch_name(repo_root: Path) -> str:
    """Return the current branch name."""
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def git_status_clean(repo_root: Path) -> bool:
    """Return True if the repo working tree is clean."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip() == ""


def git_log(
    repo_root: Path,
    max_commits: int = 20,
) -> list[dict]:
    """Return recent commits as dicts.

    Uses %x00 (null) as field delimiter to avoid JSON injection from
    commit messages containing quotes or backslashes.
    """
    result = subprocess.run(
        [
            "git", "log",
            f"-{max_commits}",
            "--format=%H%x00%h%x00%s%x00%an%x00%aI",
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    commits = []
    for raw in result.stdout.split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split("\x00")
        if len(parts) >= 5:
            commits.append({
                "sha": parts[0],
                "short_sha": parts[1],
                "subject": parts[2],
                "author": parts[3],
                "date": parts[4],
            })
        elif len(parts) == 4:
            # Fallback for compatibility: subject might have contained a null byte
            commits.append({
                "sha": parts[0],
                "short_sha": parts[1],
                "subject": parts[2],
                "author": parts[3],
                "date": "",
            })
    return commits


def read_file_snippet(path: Path, max_lines: int = 80) -> dict | None:
    """Read file with optional snippet. Returns None if file absent or unreadable."""
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        content = "".join(lines[:max_lines])
        has_more = len(lines) > max_lines
        return {
            "path": str(path),
            "exists": True,
            "read": True,
            "truncated": has_more,
            "total_lines": len(lines),
            "snippet": content,
        }
    except Exception as e:
        return {
            "path": str(path),
            "exists": True,
            "read": False,
            "error": str(e),
            "snippet": None,
        }


def file_exists(path: Path) -> bool:
    """Check if a file exists."""
    return path.is_file()


def collect_doc_info(path: Path, max_lines: int = 80) -> dict:
    """Collect info about a doc file: existence and optional snippet.

    When max_lines is 0, skips reading entirely (metadata-only mode).
    """
    result = {
        "path": str(path),
        "exists": path.is_file(),
        "snippet": None,
        "truncated": False,
        "total_lines": None,
    }
    if not result["exists"]:
        return result
    if max_lines <= 0:
        # Metadata-only mode: skip reading the file entirely
        return result
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        content = "".join(lines[:max_lines])
        result["truncated"] = len(lines) > max_lines
        result["total_lines"] = len(lines)
        result["snippet"] = content
    except Exception as e:
        result["error"] = str(e)
        result["read"] = False
    return result


def collect_script_info(path: Path, max_lines: int = 80) -> dict:
    """Collect info about a script file: existence and optional snippet."""
    return collect_doc_info(path, max_lines)


def collect_test_info(path: Path) -> dict:
    """Collect info about a test file: existence."""
    return {
        "path": str(path),
        "exists": path.is_file(),
    }


def collect_schema_info(path: Path) -> dict:
    """Collect info about a schema file: existence."""
    return {
        "path": str(path),
        "exists": path.is_file(),
    }


def collect_context(
    repo_root: Path,
    *,
    include_snippets: bool = False,
    max_snippet_lines: int = 80,
    max_git_commits: int = 20,
) -> dict:
    """Collect all structured context from the repo."""

    # Ensure repo_root is a valid git directory
    git_dir = repo_root / ".git"
    if not git_dir.is_dir():
        raise ValueError(f"Not a git repository: {repo_root}")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # Core git info
    head_sha = git_rev_parse(repo_root, "HEAD")
    branch = git_branch_name(repo_root)
    clean = git_status_clean(repo_root)

    # Recent commits
    recent_commits = git_log(repo_root, max_commits=max_git_commits)

    # Docs
    doc_map = {
        "current_project_status":         repo_root / "docs" / "current_project_status.md",
        "aed_tasker_executor_design":     repo_root / "docs" / "aed_tasker_executor_design.md",
        "evidence_tiers_and_claim_levels": repo_root / "docs" / "evidence_tiers_and_claim_levels.md",
        "runner_trial_accounting_linkage":repo_root / "docs" / "runner_trial_accounting_linkage.md",
        "aed_tasker_packet_usage":        repo_root / "docs" / "aed_tasker_packet_usage.md",
    }
    docs = {
        key: collect_doc_info(path, max_snippet_lines if include_snippets else 0)
        for key, path in doc_map.items()
    }

    # Scripts
    script_map = {
        "classify_pr_gate_state":        repo_root / "scripts" / "local" / "classify_pr_gate_state.py",
        "watch_pr_gate_state":           repo_root / "scripts" / "local" / "watch_pr_gate_state.py",
        "run_pr_gate_watchdog_once":      repo_root / "scripts" / "local" / "run_pr_gate_watchdog_once.py",
        "aed_tasker_packet":             repo_root / "scripts" / "local" / "aed_tasker_packet.py",
    }
    scripts = {
        key: collect_script_info(path, max_snippet_lines if include_snippets else 0)
        for key, path in script_map.items()
    }

    # Tests
    test_map = {
        "test_pr_gate_state_classifier":   repo_root / "tests" / "test_pr_gate_state_classifier.py",
        "test_pr_gate_watchdog":           repo_root / "tests" / "test_pr_gate_watchdog.py",
        "test_scheduled_pr_gate_watchdog": repo_root / "tests" / "test_scheduled_pr_gate_watchdog.py",
        "test_aed_tasker_packet":           repo_root / "tests" / "test_aed_tasker_packet.py",
    }
    tests = {
        key: collect_test_info(path)
        for key, path in test_map.items()
    }

    # Schemas
    schema_map = {
        "runner_output_spec_v1": repo_root / "schemas" / "runner_output_spec_v1.schema.json",
    }
    schemas = {
        key: collect_schema_info(path)
        for key, path in schema_map.items()
    }

    # Count summary
    docs_present = sum(1 for d in docs.values() if d["exists"])
    scripts_present = sum(1 for s in scripts.values() if s["exists"])
    tests_present = sum(1 for t in tests.values() if t["exists"])
    schemas_present = sum(1 for s in schemas.values() if s["exists"])

    return {
        "collected_at": timestamp,
        "collector_version": "1.0.0",
        "repo": {
            "path": str(repo_root),
            "branch": branch,
            "head_sha": head_sha,
            "clean": clean,
        },
        "options": {
            "include_snippets": include_snippets,
            "max_snippet_lines": max_snippet_lines,
            "max_git_commits": max_git_commits,
        },
        "docs": docs,
        "scripts": scripts,
        "tests": tests,
        "schemas": schemas,
        "recent_commits": recent_commits,
        "summary": {
            "docs_present": docs_present,
            "docs_total": len(docs),
            "scripts_present": scripts_present,
            "scripts_total": len(scripts),
            "tests_present": tests_present,
            "tests_total": len(tests),
            "schemas_present": schemas_present,
            "schemas_total": len(schemas),
        },
    }


def deterministic_json(packet: dict) -> str:
    """Serialize context to JSON with stable key ordering."""
    return json.dumps(
        packet,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def render_markdown(context: dict) -> str:
    """Render a readable markdown summary of the collected context."""
    lines = []
    indent = "  "

    repo = context.get("repo", {})
    summary = context.get("summary", {})

    lines.append("# AED Tasker Context Collection")
    lines.append("")
    lines.append(f"> Collected: {context.get('collected_at', 'unknown')} | "
                 f"Branch: {repo.get('branch', '?')} | "
                 f"Head: {repo.get('head_sha', '?')[:8]}")
    lines.append("")
    lines.append(f"**Clean:** {repo.get('clean', '?')}")

    # Summary counts
    lines.append("")
    lines.append("## Presence Summary")
    lines.append("")
    for key in ("docs", "scripts", "tests", "schemas"):
        s = summary.get(f"{key}_present", 0)
        t = summary.get(f"{key}_total", 0)
        pct = f"{s/t*100:.0f}%" if t else "n/a"
        lines.append(f"  **{key.capitalize()}:** {s}/{t} ({pct}) present")

    # Docs
    lines.append("")
    lines.append("## Docs")
    for key, info in context.get("docs", {}).items():
        status = "✅" if info["exists"] else "❌"
        lines.append(f"  {status} `{key}`: {info['path']}")
        if info.get("truncated"):
            lines.append(f"      → truncated snippet ({info.get('total_lines', '?')} total lines)")

    # Scripts
    lines.append("")
    lines.append("## Scripts")
    for key, info in context.get("scripts", {}).items():
        status = "✅" if info["exists"] else "❌"
        lines.append(f"  {status} `{key}`: {info['path']}")
        if info.get("truncated"):
            lines.append(f"      → truncated snippet ({info.get('total_lines', '?')} total lines)")

    # Tests
    lines.append("")
    lines.append("## Tests")
    for key, info in context.get("tests", {}).items():
        status = "✅" if info["exists"] else "❌"
        lines.append(f"  {status} `{key}`: {info['path']}")

    # Schemas
    lines.append("")
    lines.append("## Schemas")
    for key, info in context.get("schemas", {}).items():
        status = "✅" if info["exists"] else "❌"
        lines.append(f"  {status} `{key}`: {info['path']}")

    # Recent commits
    commits = context.get("recent_commits", [])
    if commits:
        lines.append("")
        lines.append(f"## Recent Commits ({len(commits)} total)")
        lines.append("")
        for commit in commits:
            sha = commit.get("short_sha", "?")
            subj = commit.get("subject", "?")
            author = commit.get("author", "?")
            lines.append(f"  `{sha}` {subj} — {author}")

    lines.append("")
    lines.append(f"*Collector version: {context.get('collector_version', '?')}*")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only AED Tasker input collector. "
                    "Must NOT call LLMs, mutate GitHub, or create Kanban tasks.",
    )
    parser.add_argument(
        "--repo-root", type=str, required=True,
        help="Path to the AED repository root",
    )
    parser.add_argument(
        "--output-json", type=str, default=None,
        help="Path to write JSON output",
    )
    parser.add_argument(
        "--output-md", type=str, default=None,
        help="Path to write markdown output",
    )
    parser.add_argument(
        "--include-snippets", action="store_true",
        help="Include file content snippets in the output",
    )
    parser.add_argument(
        "--max-snippet-lines", type=int, default=80,
        help="Max lines per snippet (default: 80)",
    )
    parser.add_argument(
        "--max-git-commits", type=int, default=20,
        help="Max number of recent git commits to include (default: 20)",
    )
    return parser


def _is_under_forbidden_prefix(path: str) -> bool:
    """Check if the given path starts with any forbidden prefix."""
    abs_path = str(Path(path).resolve())
    for prefix in FORBIDDEN_OUTPUT_PREFIXES:
        if abs_path.startswith(prefix) or abs_path == prefix:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Validate repo-root
    # Accept both .git/ directory (normal clone) and .git file (worktree)
    repo_root = Path(args.repo_root).resolve()
    git_dir = repo_root / ".git"
    is_normal_git = git_dir.is_dir()
    is_worktree_git = git_dir.is_file() and git_dir.suffix == ""
    # Worktree check: .git is a file containing "gitdir: /path/to/actual/.git"
    if is_worktree_git:
        try:
            content = git_dir.read_text(errors="replace")
            if content.startswith("gitdir: "):
                is_worktree_git = True
            else:
                is_worktree_git = False
        except Exception:
            is_worktree_git = False
    if not is_normal_git and not is_worktree_git:
        print(f"ERROR: Not a git repository: {repo_root}", file=sys.stderr)
        return 1

    # Validate output paths
    if args.output_json and _is_under_forbidden_prefix(args.output_json):
        print(f"ERROR: Output path may not be inside {HERMES_PREFIX}: {args.output_json}", file=sys.stderr)
        return 1
    if args.output_md and _is_under_forbidden_prefix(args.output_md):
        print(f"ERROR: Output path may not be inside {HERMES_PREFIX}: {args.output_md}", file=sys.stderr)
        return 1

    # Collect context
    try:
        context = collect_context(
            repo_root,
            include_snippets=args.include_snippets,
            max_snippet_lines=args.max_snippet_lines,
            max_git_commits=args.max_git_commits,
        )
    except Exception as e:
        print(f"ERROR: Collection failed: {e}", file=sys.stderr)
        return 1

    # Write JSON
    if args.output_json:
        json_bytes = deterministic_json(context).encode("utf-8")
        Path(args.output_json).write_bytes(json_bytes)
        print(f"JSON written to {args.output_json}", file=sys.stderr)

    # Write Markdown
    if args.output_md:
        md_text = render_markdown(context)
        Path(args.output_md).write_text(md_text + "\n", encoding="utf-8")
        print(f"Markdown written to {args.output_md}", file=sys.stderr)

    # If neither output specified, print JSON to stdout
    if not args.output_json and not args.output_md:
        print(deterministic_json(context))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())