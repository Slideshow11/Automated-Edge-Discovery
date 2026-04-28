#!/usr/bin/env python3
"""Local PR readiness/status report.

Default behavior is read-only and performs only local git commands. The
--include-pr flag is the only mode that may call the GitHub CLI (gh).

Outputs JSON by default; text mode is also available.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional


def run_cmd(cmd: List[str]) -> subprocess.CompletedProcess:
    """Run a subprocess command and return CompletedProcess-like object.

    We intentionally keep this small so tests can mock it. Do not run any
    mutating git or gh commands here.
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except Exception:
        # return a dummy failure
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")


def git_read(cmd: List[str]) -> Dict[str, Optional[str]]:
    cp = run_cmd(cmd)
    out = cp.stdout.strip() if getattr(cp, "stdout", None) else ""
    err = cp.stderr.strip() if getattr(cp, "stderr", None) else ""
    return {"ok": cp.returncode == 0, "out": out, "err": err, "rc": cp.returncode}


def get_repo_root() -> str:
    cp = run_cmd(["git", "rev-parse", "--show-toplevel"])
    if cp.returncode == 0 and cp.stdout:
        return cp.stdout.strip()
    # fallback to cwd
    return str(Path.cwd())


def safe_git_status() -> Dict[str, object]:
    res: Dict[str, object] = {}
    # repo root
    try:
        res["repo_root"] = get_repo_root()
    except Exception as e:
        res["repo_root"] = str(Path.cwd())
        res.setdefault("warnings", []).append(f"failed to determine repo root: {e}")

    # current branch
    curr = git_read(["git", "branch", "--show-current"])
    res["current_branch"] = curr["out"] if curr["ok"] else None
    if not curr["ok"]:
        res.setdefault("warnings", []).append("failed to determine current branch")
    # head commit
    head = git_read(["git", "rev-parse", "HEAD"])
    res["head_commit"] = head["out"] if head["ok"] else None
    if not head["ok"]:
        res.setdefault("warnings", []).append("failed to determine HEAD commit")
    # upstream
    upstream = git_read(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    res["upstream_branch"] = upstream["out"] if upstream["ok"] else None
    if not upstream["ok"]:
        # not fatal
        res.setdefault("warnings", []).append("no upstream or failed to resolve @{u}")
    # status short
    status = git_read(["git", "status", "--short"])
    res["git_status_short"] = status["out"].splitlines() if status["ok"] and status["out"] else []
    res["is_worktree_clean"] = len(res["git_status_short"]) == 0
    return res


def diff_info(base: str) -> Dict[str, object]:
    out: Dict[str, object] = {}
    # diff stat
    cp = run_cmd(["git", "diff", "--stat", f"{base}...HEAD"])
    if cp.returncode == 0:
        out["diff_stat"] = cp.stdout.strip()
    else:
        out["diff_stat"] = ""
        if getattr(cp, "stderr", None):
            out.setdefault("warnings", []).append(f"git diff --stat {base}...HEAD failed: {cp.stderr.strip()}")
    # changed files
    cp2 = run_cmd(["git", "diff", "--name-only", f"{base}...HEAD"])
    if cp2.returncode == 0:
        out["changed_files"] = [l for l in cp2.stdout.splitlines() if l]
    else:
        out["changed_files"] = []
        if getattr(cp2, "stderr", None):
            out.setdefault("warnings", []).append(f"git diff --name-only {base}...HEAD failed: {cp2.stderr.strip()}")
    return out


def recent_commits(n: int) -> List[str]:
    cp = run_cmd(["git", "log", "--oneline", f"-{n}"])
    if cp.returncode == 0 and cp.stdout:
        return [l for l in cp.stdout.splitlines() if l]
    return []


def include_pr_info() -> Dict[str, object]:
    info: Dict[str, object] = {}
    # gh pr view --json state,mergeable,number,url
    cp = run_cmd(["gh", "pr", "view", "--json", "state,mergeable,number,url"])  # may fail if gh not available
    if cp.returncode == 0 and cp.stdout:
        try:
            info["pr_view"] = json.loads(cp.stdout)
        except Exception:
            info["pr_view"] = cp.stdout.strip()
    else:
        info.setdefault("warnings", []).append("gh pr view failed or is not available in PATH/config")
    # gh pr checks
    cp2 = run_cmd(["gh", "pr", "checks", "--json", "status"]) if True else None
    if cp2 and cp2.returncode == 0 and cp2.stdout:
        try:
            info["pr_checks"] = json.loads(cp2.stdout)
        except Exception:
            info["pr_checks"] = cp2.stdout.strip()
    else:
        if cp2:
            info.setdefault("warnings", []).append("gh pr checks failed or is not available in PATH/config")
    return info


def render_text(report: Dict[str, object]) -> str:
    lines: List[str] = []
    lines.append(f"repo_root: {report.get('repo_root')}")
    lines.append(f"current_branch: {report.get('current_branch')}")
    lines.append(f"head_commit: {report.get('head_commit')}")
    lines.append(f"upstream_branch: {report.get('upstream_branch')}")
    lines.append(f"is_worktree_clean: {report.get('is_worktree_clean')}")
    lines.append("\nchanged_files:")
    for f in report.get("changed_files", []):
        lines.append(f"  - {f}")
    lines.append("\nrecent_commits:")
    for c in report.get("recent_commits", []):
        lines.append(f"  - {c}")
    if report.get("warnings"):
        lines.append("\nwarnings:")
        for w in report.get("warnings", []):
            lines.append(f"  - {w}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="origin/main", help="Diff base (default: origin/main)")
    p.add_argument("--format", choices=("json", "text"), default="json")
    p.add_argument("--include-pr", action="store_true", help="Include gh PR info (only mode that may call gh)")
    p.add_argument("--max-commits", type=int, default=5)
    args = p.parse_args(argv)

    report: Dict[str, object] = {}

    status = safe_git_status()
    report.update(status)

    report["diff_base"] = args.base
    diff = diff_info(args.base)
    report.update(diff)

    report["recent_commits"] = recent_commits(args.max_commits)

    # normalize warnings into top-level list
    warnings: List[str] = []
    v = report.pop("warnings", None)
    if v:
        warnings.extend(v)
    # collect warnings from diff (already merged by update)
    if "warnings" in diff and diff["warnings"]:
        warnings.extend(diff["warnings"])
    report["warnings"] = warnings

    if args.include_pr:
        gh_info = include_pr_info()
        report["gh"] = gh_info
        if gh_info.get("warnings"):
            report["warnings"].extend(gh_info.get("warnings"))

    # stable output ordering
    ordered = {k: report[k] for k in sorted(report.keys())}

    if args.format == "json":
        json.dump(ordered, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(render_text(ordered))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
