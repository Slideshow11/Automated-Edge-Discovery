#!/usr/bin/env python3
"""
scope_guard.py — Read-only local scope auditor for declared PR scope contracts.

Checks changed files between two git refs against:
  - explicit allowlists (allow-file, allow-glob)
  - forbidden path patterns (forbid-file, forbid-glob)
  - added diff lines against forbidden command/API patterns
  - optional companion-test allowance for source-file changes

Does NOT call GitHub APIs. Does NOT mutate GitHub state. Does NOT merge.
Does NOT modify files. Does NOT auto-fix.

Usage:
    python3 scripts/local/scope_guard.py \
        --repo-root /path/to/repo \
        [--base-ref origin/main] \
        [--head-ref HEAD] \
        [--allow-file scripts/local/foo.py] \
        [--allow-glob "scripts/**/*.py"] \
        [--forbid-file .github/workflows/x.yml] \
        [--forbid-glob "**/.github/**"] \
        [--forbid-diff-regex "shell=True"] \
        [--allow-companion-tests] \
        [--source-path scripts/local/foo.py] \
        --output-json /tmp/scope.json \
        --output-md /tmp/scope.md

Exit codes:
    0  — report written (any status)
    1  — ERROR_TOOL_FAILURE
"""

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Result statuses
# ---------------------------------------------------------------------------

STATUS_SCOPE_CLEAN = "SCOPE_CLEAN"
STATUS_HOLD_SCOPE_VIOLATION = "HOLD_SCOPE_VIOLATION"
STATUS_HOLD_FORBIDDEN_DIFF_PATTERN = "HOLD_FORBIDDEN_DIFF_PATTERN"
STATUS_HOLD_GIT_DIFF_TOO_LARGE = "HOLD_GIT_DIFF_TOO_LARGE"
STATUS_ERROR_TOOL_FAILURE = "ERROR_TOOL_FAILURE"

# ---------------------------------------------------------------------------
# Built-in forbidden diff patterns (checked against added lines only)
# ---------------------------------------------------------------------------

BUILTIN_FORBIDDEN_DIFF_REGEXES = [
    re.compile(r"gh\s+pr\s+merge.*--admin", re.IGNORECASE),
    re.compile(r"\b--admin\b"),
    re.compile(r"resolveReviewThread"),
    re.compile(r"dismissPullRequestReview"),
    re.compile(r"deleteReviewComment"),
    re.compile(r"deleteIssueComment"),
    re.compile(r"gh\s+api\b.*\s(-X|--method)\s+(POST|PATCH|PUT|DELETE)", re.IGNORECASE),
    re.compile(r"shell\s*=\s*True"),
    re.compile(r"ruff\s+.*--fix"),
]

# ---------------------------------------------------------------------------
# Built-in forbidden path patterns
# ---------------------------------------------------------------------------

BUILTIN_FORBIDDEN_GLOBS = [
    ".github/workflows/**",
    ".github/CODEOWNERS",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_git(args: list[str], repo_root: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    cmd = ["git", "-C", str(repo_root)] + args
    return subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)


def compute_changed_file_records(
    repo_root: Path, base_ref: str, head_ref: str
) -> tuple[list[dict[str, Any]], str]:
    """Return (records, stderr) from git diff --name-status -M base...head.

    Each record has:
      - path     : primary path (destination for R/C, the file for M/A/D)
      - old_path : source path for R (rename) and C (copy); absent otherwise
      - new_path : destination path for R and C; absent otherwise
      - status   : R/C/M/A/D
    """
    result = run_git(
        ["diff", "--name-status", "-M", f"{base_ref}...{head_ref}"], repo_root
    )
    if result.returncode != 0:
        return [], result.stderr
    records: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status in ("R", "C"):
            if len(parts) >= 3:
                records.append({
                    "path": parts[2],
                    "old_path": parts[1],
                    "new_path": parts[2],
                    "status": status,
                })
        else:
            path = parts[1] if len(parts) >= 2 else ""
            records.append({
                "path": path,
                "old_path": None,
                "new_path": None,
                "status": status,
            })
    return records, ""


def compute_diff_patch(
    repo_root: Path,
    base_ref: str,
    head_ref: str,
    changed_files: list[str],
    max_lines: int = 20000,
) -> tuple[str, int, str]:
    """Return (patch_text, line_count, stderr) from git diff for changed files."""
    if not changed_files:
        return "", 0, ""
    # Use -- *.py to limit to changed files (space-separated after --)
    file_args: list[str] = []
    for f in changed_files:
        file_args.append("--")
        file_args.append(f)

    result = run_git(
        ["diff", "--unified=0", f"{base_ref}...{head_ref}"] + file_args,
        repo_root,
        timeout=120,
    )
    if result.returncode != 0:
        return "", 0, result.stderr

    patch = result.stdout
    # Count added lines (lines starting with + but not +++)
    added_lines = sum(
        1 for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    return patch, added_lines, ""


def _path_glob_matches(pattern: str, path: str) -> bool:
    """Return True if path matches the glob pattern with path-segment semantics.

    - "*" matches within one path segment only (no "/" allowed in the match).
    - "?" matches within one path segment only.
    - "**" may match across "/" boundaries (zero or more segments).
    - All other characters match literally.

    Examples:
      pattern "scripts/*.py" matches "scripts/foo.py"
      pattern "scripts/*.py" does NOT match "scripts/local/foo.py"
      pattern "scripts/**/*.py" matches "scripts/local/foo.py"
      pattern ".github/workflows/**" matches ".github/workflows/ci.yml"
    """
    # Normalize to POSIX-style forward slashes
    norm_pat = pattern.replace("\\", "/")
    norm_path = path.replace("\\", "/")

    pat_parts = norm_pat.split("/")
    path_parts = norm_path.split("/")

    def match_segments(pi: int, pj: int, xi: int, xj: int) -> bool:
        """Match pat_parts[pi:pj] against path_parts[xi:xj]."""
        while pi < pj and xi < xj:
            pp = pat_parts[pi]
            xp = path_parts[xi]
            if pp == "**":
                # ** can match zero or more segments; try all possibilities
                for k in range(xi, xj + 1):
                    if match_segments(pi + 1, pj, k, xj):
                        return True
                return False
            elif pp == "*":
                # * matches any single segment (no "/" in segment)
                if "/" in xp:
                    return False
                pi += 1
                xi += 1
            elif pp == "?":
                # ? matches any single character, no "/"
                if "/" in xp or len(xp) != 1:
                    return False
                pi += 1
                xi += 1
            else:
                # Literal segment — must match exactly
                if not fnmatch.fnmatchcase(xp, pp):
                    return False
                pi += 1
                xi += 1
        # Handle trailing ** that can consume remaining path segments
        if pi < pj and pat_parts[pi] == "**":
            return True
        if pi < pj and pat_parts[pi] == "*":
            # Trailing * must not consume "/" segments
            for k in range(xi, xj):
                if "/" in path_parts[k]:
                    return False
            return True
        return pi == pj and xi == xj

    return match_segments(0, len(pat_parts), 0, len(path_parts))


def matches_glob(path: str, pattern: str) -> bool:
    """Return True if path matches the glob pattern (path-segment aware)."""
    return _path_glob_matches(pattern, path)


def is_companion_test(file_path: str, source_path: str) -> bool:
    """Return True if file_path is a companion test for source_path."""
    # e.g. scripts/local/foo.py -> tests/test_foo.py or tests/foo_test.py
    src = Path(source_path)
    src_stem = src.stem  # "foo" from "foo.py"
    src_parent = str(src.parent)  # "scripts/local"

    file_p = Path(file_path)

    # Must be under tests/
    if not str(file_p).startswith("tests/"):
        return False

    # tests/test_<stem>.py  or  tests/<stem>_test.py
    name = file_p.name
    if name == f"test_{src_stem}.py":
        return True
    if name == f"{src_stem}_test.py":
        return True
    return False


def scan_added_lines_for_patterns(
    patch: str,
    patterns: list[re.Pattern[str]],
    skip_files: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Scan added lines (+, not +++) for regex matches. Return match records."""
    if skip_files is None:
        skip_files = set()
    matches: list[dict[str, Any]] = []
    current_file = ""
    current_hunk = ""
    _skip_current_file = False

    for line in patch.splitlines():
        if line.startswith("diff --git"):
            # Extract filename from "diff --git a/foo.py b/foo.py"
            parts = line.split(" b/", 1)
            if len(parts) == 2:
                current_file = parts[1].split()[0]
            current_hunk = ""
            _skip_current_file = current_file in skip_files
        elif line.startswith("@@"):
            current_hunk = line
        elif line.startswith("+") and not line.startswith("+++"):
            if _skip_current_file:
                continue
            text = line[1:]
            for pat in patterns:
                if pat.search(text):
                    matches.append({
                        "file": current_file,
                        "hunk": current_hunk,
                        "pattern": pat.pattern,
                        "excerpt": text[:200],
                    })

    return matches


# ---------------------------------------------------------------------------
# Core audit logic
# ---------------------------------------------------------------------------

def audit_scope(
    repo_root: Path,
    base_ref: str,
    head_ref: str,
    allow_files: list[str],
    allow_globs: list[str],
    forbid_files: list[str],
    forbid_globs: list[str],
    forbid_diff_patterns: list[re.Pattern[str]],
    allow_companion_tests: bool,
    source_paths: list[str],
    max_diff_lines: int = 20000,
) -> dict[str, Any]:
    """Run the full scope audit and return the report dict."""

    # Step 1: resolve refs to SHAs
    base_result = run_git(["rev-parse", base_ref], repo_root)
    if base_result.returncode != 0:
        return _error_report(repo_root, base_ref, head_ref, "ERROR_TOOL_FAILURE",
                             f"Failed to resolve base-ref: {base_result.stderr}")
    base_sha = base_result.stdout.strip()

    head_result = run_git(["rev-parse", head_ref], repo_root)
    if head_result.returncode != 0:
        return _error_report(repo_root, base_ref, head_ref, "ERROR_TOOL_FAILURE",
                             f"Failed to resolve head-ref: {head_result.stderr}")
    head_sha = head_result.stdout.strip()

    # Step 2: compute changed file records (with rename/copy metadata)
    file_records, rec_err = compute_changed_file_records(repo_root, base_ref, head_ref)
    if rec_err and not file_records:
        return _error_report(repo_root, base_ref, head_ref, "ERROR_TOOL_FAILURE",
                             f"git diff --name-status failed: {rec_err}")

    # Derive simple changed_files list for backward compatibility
    changed_files = [rec["path"] for rec in file_records]

    # Step 3: compute diff patch
    patch, added_line_count, diff_err = compute_diff_patch(
        repo_root, base_ref, head_ref, changed_files, max_diff_lines
    )
    if diff_err:
        return _error_report(repo_root, base_ref, head_ref, "ERROR_TOOL_FAILURE",
                             f"git diff patch failed: {diff_err}")

    if added_line_count > max_diff_lines:
        return _build_report(
            repo_root, base_ref, head_ref, base_sha, head_sha,
            changed_files, [], [], [], [], [],
            STATUS_HOLD_GIT_DIFF_TOO_LARGE,
            allow_files, allow_globs, forbid_files, forbid_globs,
            allow_companion_tests, source_paths,
            file_records=[],
        )

    # Step 4: classify files (including rename/copy source paths)
    file_results: list[dict[str, Any]] = []
    forbidden_path_matches: list[dict[str, Any]] = []
    not_allowlisted_files: list[str] = []
    companion_test_files: list[str] = []

    has_allowlist = bool(allow_files or allow_globs)

    for rec in file_records:
        # Collect all paths that need classification for this record
        paths_to_check: list[tuple[str, str | None]] = []
        paths_to_check.append((rec["path"], None))  # (path, matched_side)
        if rec["status"] in ("R", "C") and rec["old_path"]:
            paths_to_check.append((rec["old_path"], "old_path"))
        if rec["status"] in ("R", "C") and rec["new_path"]:
            paths_to_check.append((rec["new_path"], "new_path"))

        record_has_forbidden = False
        record_file_results: list[dict[str, Any]] = []

        for path, side in paths_to_check:
            classification = "allowed"
            reason = ""

            # Check forbidden exact paths
            if path in forbid_files:
                classification = "forbidden_file"
                reason = f"exact forbid-file match: {path}"
                forbidden_path_matches.append({
                    "file": path, "type": "forbidden_file", "match": path,
                    "old_path": rec.get("old_path"), "new_path": rec.get("new_path"),
                    "status": rec["status"], "matched_side": side or "path",
                })
                record_has_forbidden = True
            else:
                for glob_pat in forbid_globs:
                    if matches_glob(path, glob_pat):
                        classification = "forbidden_glob"
                        reason = f"forbid-glob match: {glob_pat}"
                        forbidden_path_matches.append({
                            "file": path, "type": "forbidden_glob", "match": glob_pat,
                            "old_path": rec.get("old_path"), "new_path": rec.get("new_path"),
                            "status": rec["status"], "matched_side": side or "path",
                        })
                        record_has_forbidden = True
                        break

            # Check allowlist (if provided)
            if classification == "allowed" and has_allowlist:
                allowed = False
                if path in allow_files:
                    allowed = True
                else:
                    for g in allow_globs:
                        if matches_glob(path, g):
                            allowed = True
                            break
                if not allowed:
                    if allow_companion_tests:
                        for src in source_paths:
                            if is_companion_test(path, src):
                                classification = "companion_test_allowed"
                                companion_test_files.append(path)
                                reason = f"companion test for {src}"
                                break
                    if classification == "allowed":
                        classification = "not_allowlisted"
                        reason = f"not in allowlist"
                        not_allowlisted_files.append(path)

            record_file_results.append({
                "file": path,
                "classification": classification,
                "reason": reason,
                "matched_side": side or "path",
            })

        # Store primary path result
        file_results.append(record_file_results[0])

    # Step 5: scan diff for forbidden patterns
    # Exclude scope_guard's own source files from diff scanning — they
    # necessarily contain the forbid-diff-regex literal strings as the
    # built-in pattern list and would always trigger false positives.
    forbid_patterns = list(forbid_diff_patterns)
    scope_guard_own_files = {"scripts/local/scope_guard.py", "tests/test_scope_guard.py"}
    forbidden_diff_matches = scan_added_lines_for_patterns(
        patch, forbid_patterns, skip_files=scope_guard_own_files
    )

    # Step 6: determine status
    if forbidden_diff_matches:
        status = STATUS_HOLD_FORBIDDEN_DIFF_PATTERN
    elif forbidden_path_matches or not_allowlisted_files:
        status = STATUS_HOLD_SCOPE_VIOLATION
    else:
        status = STATUS_SCOPE_CLEAN

    return _build_report(
        repo_root, base_ref, head_ref, base_sha, head_sha,
        changed_files, file_results, forbidden_path_matches,
        not_allowlisted_files, companion_test_files, forbidden_diff_matches,
        status,
        allow_files, allow_globs, forbid_files, forbid_globs,
        allow_companion_tests, source_paths,
        file_records=file_records,
    )


def _error_report(
    repo_root: Path,
    base_ref: str,
    head_ref: str,
    status: str,
    message: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "repo_root": str(repo_root),
        "base_ref": base_ref,
        "head_ref": head_ref,
        "base_sha": "",
        "head_sha": "",
        "changed_files": [],
        "file_results": [],
        "forbidden_path_matches": [],
        "not_allowlisted_files": [],
        "companion_test_files": [],
        "forbidden_diff_matches": [],
        "allowlist": {"files": [], "globs": []},
        "forbidlist": {"files": [], "globs": [], "diff_regexes": []},
        "mutated_github": False,
        "modified_files": False,
        "audit_only": True,
        "error_message": message,
    }


def _build_report(
    repo_root: Path,
    base_ref: str,
    head_ref: str,
    base_sha: str,
    head_sha: str,
    changed_files: list[str],
    file_results: list[dict[str, Any]],
    forbidden_path_matches: list[dict[str, Any]],
    not_allowlisted_files: list[str],
    companion_test_files: list[str],
    forbidden_diff_matches: list[dict[str, Any]],
    status: str,
    allow_files: list[str],
    allow_globs: list[str],
    forbid_files: list[str],
    forbid_globs: list[str],
    allow_companion_tests: bool,
    source_paths: list[str],
    file_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if file_records is None:
        file_records = []
    return {
        "status": status,
        "repo_root": str(repo_root),
        "base_ref": base_ref,
        "head_ref": head_ref,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "changed_files": changed_files,
        "file_results": file_results,
        "forbidden_path_matches": forbidden_path_matches,
        "not_allowlisted_files": not_allowlisted_files,
        "companion_test_files": companion_test_files,
        "forbidden_diff_matches": forbidden_diff_matches,
        "allowlist": {"files": allow_files, "globs": allow_globs},
        "forbidlist": {
            "files": forbid_files,
            "globs": forbid_globs,
            "diff_regexes": [p.pattern for p in BUILTIN_FORBIDDEN_DIFF_REGEXES],
        },
        "mutated_github": False,
        "modified_files": False,
        "audit_only": True,
        "companion_tests_used": allow_companion_tests,
        "source_paths": source_paths,
        "changed_file_records": file_records,
    }


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------

def write_json(path: str, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def write_md(path: str, data: dict) -> None:
    lines = [
        "# PR Scope Audit",
        "",
        f"**Status**: `{data['status']}`",
        "",
        f"**Repository**: `{data.get('repo_root', '?')}`",
        f"**Base ref**: `{data.get('base_ref', '?')}`  "
        f"(`{data.get('base_sha', '?')[:8]}`)",
        f"**Head ref**: `{data.get('head_ref', '?')}`  "
        f"(`{data.get('head_sha', '?')[:8]}`)",
        "",
    ]

    changed = data.get("changed_files", [])
    if changed:
        lines.append("## Changed Files")
        lines.append("")
        for f in changed:
            lines.append(f"- `{f}`")
        lines.append("")

    violations = data.get("forbidden_path_matches", [])
    if violations:
        lines.append("## Scope Violations")
        lines.append("")
        for v in violations:
            lines.append(f"- `{v['file']}` — {v['type']} (match: `{v['match']}`)")
        lines.append("")

    not_allowed = data.get("not_allowlisted_files", [])
    if not_allowed:
        lines.append("## Not Allowlisted")
        lines.append("")
        for f in not_allowed:
            lines.append(f"- `{f}`")
        lines.append("")

    diff_matches = data.get("forbidden_diff_matches", [])
    if diff_matches:
        lines.append("## Forbidden Diff Patterns")
        lines.append("")
        for m in diff_matches:
            lines.append(f"- `{m['file']}`: `{m['pattern']}`")
            lines.append(f"  > {m['excerpt'][:120]}")
        lines.append("")

    companion = data.get("companion_test_files", [])
    if companion:
        lines.append("## Companion Tests Allowed")
        lines.append("")
        for f in companion:
            lines.append(f"- `{f}`")
        lines.append("")

    lines.extend([
        "## Safety Invariants",
        "",
        "**v1 is audit-only. No files are modified. No GitHub state is mutated.**",
        "",
        "- :x: No `--admin` is ever passed (rejected at argv parse time)",
        "- :x: No GitHub API calls are made",
        "- :x: No GraphQL mutations are called",
        "- :x: No files are modified",
        "- :x: No branches are updated",
        "- :x: No workflows are changed",
        "- :x: No branch protection is modified",
        "- :white_check_mark: `mutated_github` is always `false`",
        "- :white_check_mark: `modified_files` is always `false`",
        "- :white_check_mark: `audit_only` is always `true`",
        "",
        "*This report was produced by scope_guard.py v1 — read-only scope auditor.*",
    ])

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only local scope auditor for declared PR scope contracts.",
    )
    parser.add_argument("--repo-root", required=True, type=Path,
                        help="Path to the git repository root")
    parser.add_argument("--base-ref", default="origin/main",
                        help="Base git ref (default: origin/main)")
    parser.add_argument("--head-ref", default="HEAD",
                        help="Head git ref (default: HEAD)")
    parser.add_argument("--allow-file", dest="allow_files", action="append", default=[],
                        help="Explicitly allowed file path (repeatable)")
    parser.add_argument("--allow-glob", dest="allow_globs", action="append", default=[],
                        help="Glob pattern for allowed files (repeatable)")
    parser.add_argument("--forbid-file", dest="forbid_files", action="append", default=[],
                        help="Explicitly forbidden file path (repeatable)")
    parser.add_argument("--forbid-glob", dest="forbid_globs", action="append", default=[],
                        help="Glob pattern for forbidden files (repeatable)")
    parser.add_argument("--forbid-diff-regex", dest="forbid_diff_regexes", action="append",
                        default=[],
                        help="Regex pattern to scan added diff lines (repeatable)")
    parser.add_argument("--allow-companion-tests", action="store_true",
                        help="Allow companion tests for changed source files")
    parser.add_argument("--source-path", dest="source_paths", action="append", default=[],
                        help="Source file path for companion-test mapping (repeatable)")
    parser.add_argument("--output-json", required=True, help="Path to JSON report")
    parser.add_argument("--output-md", required=True, help="Path to Markdown report")
    parser.add_argument("--max-diff-lines", type=int, default=20000,
                        help="Maximum added diff lines to accept (default: 20000)")

    # Reject --admin before argparse sees it (argparse would exit with "unknown option")
    if "--admin" in sys.argv:
        print("ERROR: --admin is forbidden by scope_guard.py", file=sys.stderr)
        return 1

    args = parser.parse_args()

    # Verify repo-root exists and is a git repo
    if not args.repo_root.exists():
        print(f"ERROR: repo-root does not exist: {args.repo_root}", file=sys.stderr)
        return 1
    git_dir = args.repo_root / ".git"
    if not git_dir.exists():
        print(f"ERROR: repo-root is not a git repository: {args.repo_root}", file=sys.stderr)
        return 1

    # Build forbid diff patterns (built-in + CLI)
    forbid_patterns: list[re.Pattern[str]] = list(BUILTIN_FORBIDDEN_DIFF_REGEXES)
    for pat_str in args.forbid_diff_regexes:
        try:
            forbid_patterns.append(re.compile(pat_str))
        except re.error as e:
            print(f"ERROR: invalid forbid-diff-regex '{pat_str}': {e}", file=sys.stderr)
            return 1

    # Build forbid globs (built-in + CLI)
    forbid_globs = list(BUILTIN_FORBIDDEN_GLOBS) + args.forbid_globs

    try:
        report = audit_scope(
            repo_root=args.repo_root,
            base_ref=args.base_ref,
            head_ref=args.head_ref,
            allow_files=args.allow_files,
            allow_globs=args.allow_globs,
            forbid_files=args.forbid_files,
            forbid_globs=forbid_globs,
            forbid_diff_patterns=forbid_patterns,
            allow_companion_tests=args.allow_companion_tests,
            source_paths=args.source_paths,
            max_diff_lines=args.max_diff_lines,
        )
    except Exception as e:
        report = _error_report(
            args.repo_root, args.base_ref, args.head_ref,
            STATUS_ERROR_TOOL_FAILURE, f"Unexpected error: {e}"
        )

    write_json(args.output_json, report)
    write_md(args.output_md, report)

    # Exit code 0 always (report written), caller checks status
    return 0


if __name__ == "__main__":
    sys.exit(main())
