#!/usr/bin/env python3
"""
Phase 2 Quarantine Autocoder — Dry-Run Read-Only Trace Collection

WARNING: This tool produces a bundle ONLY. It does NOT:
  - Apply any patch
  - Execute any agent
  - Touch Hermes
  - Dispatch any Kanban task
  - Create any PR
  - Perform any import

Phase 2 still does NOT execute real operations. It adds read-only evidence
collection: git diff, scope check, safety grep, and local gate preview.
No command in this phase mutates repo state, GitHub state, Hermes state,
memory, skills, cron, Telegram, or production boards.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_SLUG_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
FORBIDDEN_BUNDLE_PREFIXES = (".git", "hermes", ".hermes", "workflows", ".github")
FORBIDDEN_BUNDLE_INFIXES = (".git/", "/.git")
# Context-suppressed patterns: these strings are allowed in the specified
# contexts even when they appear in non-test Python files. They represent
# policy documentation, not executable behavior.
CONTEXT_SUPPRESSION_PATTERNS = frozenset([
    # argparse help text
    ("hermes kanban create", "argparse_help"),
    ("hermes kanban dispatch", "argparse_help"),
    ("gh pr merge", "argparse_help"),
    ("gh pr create", "argparse_help"),
    ("git push", "argparse_help"),
    ("git commit", "argparse_help"),
    ("telegram", "argparse_help"),
    ("send_message", "argparse_help"),
    ("memory.update", "argparse_help"),
    ("skill_manage", "argparse_help"),
    ("fact_store", "argparse_help"),
    ("delegate_task", "argparse_help"),
    ("cronjob", "argparse_help"),
    # docstrings describing policy
    ("hermes kanban create", "docstring"),
    ("hermes kanban dispatch", "docstring"),
    ("gh pr merge", "docstring"),
    ("gh pr create", "docstring"),
    ("git push", "docstring"),
    ("git commit", "docstring"),
    ("telegram", "docstring"),
    ("send_message", "docstring"),
    ("memory.update", "docstring"),
    ("skill_manage", "docstring"),
    ("fact_store", "docstring"),
    ("delegate_task", "docstring"),
    ("cronjob", "docstring"),
])


EXECUTABLE_MUTATION_COMMANDS = frozenset([
    "hermes kanban create",
    "hermes kanban dispatch",
    "gh pr merge",
    "gh pr create",
    "git push",
    "git commit",
    "telegram",
    "send_message",
    "memory.update",
    "skill_manage",
    "fact_store",
    "delegate_task",
    "cronjob",
])


# Executable-context patterns: if a forbidden command string appears in a line
# containing one of these patterns, it is considered an executable violation rather
# than a policy mention or suppressed context.
EXECUTABLE_CONTEXT_PATTERNS = frozenset([
    "subprocess.run",
    "subprocess.call",
    "subprocess.Popen",
    "subprocess.check_output",
    "subprocess.check_call",
    "os.system",
    "os.popen",
    "shell=True",
    "exec(",
    "eval(",
])


def is_suppressed_context(line: str, pattern: str) -> tuple[bool, str]:
    """
    Determine whether a pattern match in a line is in a suppressed context.

    Suppressed contexts (policy mentions, not executable):
    - Line starts with # (comment)
    - Line is inside a triple-quote docstring (starts with \"\"\" or ''')
    - Line is an argparse help string (contains '--' and a command example)
    - Line contains the pattern inside a string literal that is clearly
      documentation/help rather than code

    Returns (is_suppressed, reason).

    Reason is one of: "comment", "docstring", "argparse_help", "generated_example",
    "suppressed", "executable_context", "unknown".
    """
    stripped = line.strip()

    # Comment line — policy mention, not executable
    if stripped.startswith("#"):
        return (True, "comment")

    # Docstring boundary — policy/documentation, not executable
    if stripped.startswith('"""') or stripped.startswith("'''") or \
            stripped.startswith('r"""') or stripped.startswith("r'''"):
        return (True, "docstring")

    # Check if inside a docstring (simple heuristic: line contains the pattern
    # but the preceding non-blank line was a docstring opener not yet closed)
    # For simplicity: if line contains '"""' or "'''" and pattern is in a
    # string context (line has odd quote count), treat as docstring

    # Check argparse help pattern
    if "--" in stripped and any(cmd in stripped for cmd in EXECUTABLE_MUTATION_COMMANDS):
        # Looks like: "--hermes-kanban-create TEXT  help text for the option"
        # or "hermes kanban create is forbidden in Phase 1"
        if any(phrase in stripped.lower() for phrase in [
            "help=", "help text", "helpmsg", "description=",
            "is forbidden", "is not allowed", "must not be",
            "do not use", "avoid", "suppress",
        ]):
            return (True, "argparse_help")

    # Check if line is inside a multi-line string that looks like help text
    # Heuristic: line has a command string in quotes with surrounding explanatory text
    if '"' in line or "'" in line:
        # Check for generated example pattern: commented or docstring example
        if any(phrase in line.lower() for phrase in [
            "example:", "e.g.", "for example", "such as",
            "command example", "usage:", "syntax:",
        ]):
            return (True, "generated_example")

    return (False, "executable_context")


def is_executable_context(line: str) -> bool:
    """Return True if the line contains executable-context patterns."""
    for pattern in EXECUTABLE_CONTEXT_PATTERNS:
        if pattern in line:
            return True
    return False


def is_test_file(path: str) -> bool:
    """Return True if the file is a test file (test_ prefix or _test.py suffix).

    Test files parameterize forbidden strings as test data — these are not
    executable violations even when they contain forbidden command strings.
    """
    name = os.path.basename(path)
    return name.startswith("test_") or name.endswith("_test.py")


def validate_scope_json_args(
    allowed_files_json: str | None,
    forbidden_files_json: str | None,
) -> tuple[list[str] | None, list[str] | None]:
    """Parse and validate scope JSON arguments.

    Validates both --allowed-files-json and --forbidden-files-json:
    - Must be valid JSON
    - Must be a list (not dict, string, number, etc.)
    - Items must be non-empty strings
    - No absolute paths, no '..', no empty strings

    Raises SystemExit(1) on any validation failure — called before any bundle
    files are written so that malformed scope JSON fails fast.
    """
    parsed_allowed = None
    parsed_forbidden = None

    if allowed_files_json is not None:
        try:
            parsed_allowed = json.loads(allowed_files_json)
        except json.JSONDecodeError as e:
            print(f"VALIDATION ERROR: --allowed-files-json is not valid JSON: {e}")
            sys.exit(1)
        if not isinstance(parsed_allowed, list):
            print("VALIDATION ERROR: --allowed-files-json must be a JSON array")
            sys.exit(1)
        try:
            _validate_scope_path_list(parsed_allowed, "allowed_files")
        except ValueError as e:
            print(f"VALIDATION ERROR: {e}")
            sys.exit(1)

    if forbidden_files_json is not None:
        try:
            parsed_forbidden = json.loads(forbidden_files_json)
        except json.JSONDecodeError as e:
            print(f"VALIDATION ERROR: --forbidden-files-json is not valid JSON: {e}")
            sys.exit(1)
        if not isinstance(parsed_forbidden, list):
            print("VALIDATION ERROR: --forbidden-files-json must be a JSON array")
            sys.exit(1)
        try:
            _validate_scope_path_list(parsed_forbidden, "forbidden_files")
        except ValueError as e:
            print(f"VALIDATION ERROR: {e}")
            sys.exit(1)

    return parsed_allowed, parsed_forbidden


def validate_base_sha(sha: str) -> None:
    if not HEX_SHA_RE.match(sha):
        raise ValueError(f"base_sha must be a 40-char hex string, got: {sha!r}")


def validate_candidate_id(candidate_id: str) -> None:
    if not SAFE_SLUG_RE.match(candidate_id):
        raise ValueError(
            f"candidate_id must be a safe slug (alphanumeric, underscore, hyphen), "
            f"got: {candidate_id!r}"
        )


def validate_source_repo(source_repo: str) -> None:
    """
    Validate that source_repo is an existing local path, not a GitHub slug.

    Rejects:
    - GitHub slugs (like "Slideshow11/Automated-Edge-Discovery") —
      two simple identifiers separated by "/" with no leading slash
    - Non-existent paths
    - Files (must be a directory)
    - Filesystem root '/'

    Accepts:
    - Absolute paths like "/home/max/Automated-Edge-Discovery"
    - Relative paths that resolve to an existing directory
    """
    # Reject bare GitHub slugs: two identifiers separated by "/" with no leading slash.
    # Pattern: no leading '/', exactly two non-empty parts when split by '/',
    # each part looks like a simple identifier (no path separators inside).
    # This catches "Slideshow11/Automated-Edge-Discovery" but NOT "/tmp/xxx" or "docs/".
    if not source_repo.startswith("/") and source_repo.count("/") == 1:
        owner, repo = source_repo.split("/", 1)
        if owner and repo and not any(c in ("/", "\\") for c in owner) and not any(c in ("/", "\\") for c in repo):
            raise ValueError(
                f"--source-repo must be an existing local path, not a remote repo slug. "
                f"Received: {source_repo!r}. "
                f"Use an absolute path like /home/max/Automated-Edge-Discovery."
            )

    source_repo = os.path.abspath(source_repo)
    if source_repo == "/":
        raise ValueError("source_repo cannot be the filesystem root '/'")

    if not os.path.exists(source_repo):
        raise ValueError(
            f"--source-repo must be an existing local path. "
            f"Received: {source_repo!r} which does not exist."
        )

    if not os.path.isdir(source_repo):
        raise ValueError(
            f"--source-repo must be a directory, not a file. "
            f"Received: {source_repo!r}."
        )


def validate_bundle_dir(bundle_dir: str, force: bool) -> None:
    # Use resolved (real) paths to handle symlinks — prevents bypass via
    # symlink that points into .git or repo root from outside the repo.
    bundle_dir_resolved = Path(bundle_dir).resolve()
    repo_root = Path(__file__).resolve().parents[2]  # .../Automated-Edge-Discovery
    repo_root_resolved = repo_root.resolve()

    # Check against forbidden production directories under repo root
    for prefix in FORBIDDEN_BUNDLE_PREFIXES:
        protected = (repo_root_resolved / prefix).resolve()
        # relative_to raises ValueError when bundle is NOT inside protected.
        # is_relative_to() (Python 3.9+) returns True when bundle IS inside.
        # Use the flag pattern to correctly handle the try/except logic.
        is_inside = False
        try:
            bundle_dir_resolved.relative_to(protected)
            is_inside = True
        except ValueError:
            pass  # not inside this prefix, continue
        if is_inside:
            raise ValueError(
                f"bundle_dir cannot be inside production directory: {prefix}"
            )

    # Reject if bundle dir IS the repo root
    if bundle_dir_resolved == repo_root_resolved:
        raise ValueError("bundle_dir cannot be the production repository root")

    # Check .git infix even after resolve (covers both /path/.git and symlink resolved path)
    resolved_str = str(bundle_dir_resolved)
    for infix in FORBIDDEN_BUNDLE_INFIXES:
        if infix in resolved_str:
            raise ValueError(f"bundle_dir cannot contain: {infix}")

    # Test file detection: skip files whose basename starts with "test_" or ends
    # with "_test.py" — these are test files and forbidden strings within them
    # are parameterized test data, not executable violations.
    def is_test_file(path: str) -> bool:
        name = os.path.basename(path)
        return name.startswith("test_") or name.endswith("_test.py")

    if not force and any(Path(bundle_dir).iterdir() if Path(bundle_dir).is_dir() else []):
        raise ValueError(
            f"bundle_dir is not empty. Use --force to overwrite or re-run."
        )


def _normalize_trailing_slash(path: str) -> str:
    """Normalize a scope path: strip trailing slashes for consistent comparison."""
    return path.rstrip("/")


def _is_absolute_or_parent_ref(path: str) -> bool:
    """Return True if path is absolute or contains '..' or is empty."""
    if not path:
        return True
    if os.path.isabs(path):
        return True
    if ".." in path.split(os.sep):
        return True
    return False


def _validate_scope_path_list(paths: list[str], name: str) -> None:
    """Validate a list of scope paths. Raises ValueError on first invalid entry."""
    for p in paths:
        if not isinstance(p, str):
            raise ValueError(f"{name} contains non-string entry: {p!r}")
        normalized = _normalize_trailing_slash(p)
        if not normalized:
            raise ValueError(f"{name} contains empty string entry")
        if _is_absolute_or_parent_ref(normalized):
            raise ValueError(f"{name} contains invalid path: {p!r} (absolute paths and '..' are forbidden)")


def safety_grep_content(content: str) -> list[str]:
    """Check content for executable mutation commands. Returns list of matches."""
    found = []
    for cmd in EXECUTABLE_MUTATION_COMMANDS:
        if cmd in content:
            found.append(cmd)
    return found


# ---------------------------------------------------------------------------
# Read-only collection helpers
# ---------------------------------------------------------------------------

# Git env vars that can cause external command execution during diff operations.
# Stripping these prevents Git config or environment from triggering external
# diff drivers or textconv filters, preserving the read-only invariant.
_SANITIZED_GIT_ENV_VARS = frozenset([
    "GIT_EXTERNAL_DIFF",
    "GIT_DIFF_OPTS",
    "GIT_TEXTCONV",
    "GIT_DIFF_TOOL",
    "GIT_DIFFTOOL",
    "GIT_DIFFTOOL_CMD",
    "GIT_DIFFTOOL_PROMPT",
])


def _build_sanitized_env() -> dict:
    """Build a sanitized environment that blocks external diff execution.

    Returns a copy of os.environ with all known external-diff-triggering git
    vars removed.  This prevents Git config or environment from triggering
    external diff drivers or textconv filters, preserving the read-only
    invariant.
    """
    import os
    env = dict(os.environ)
    for var in _SANITIZED_GIT_ENV_VARS:
        env.pop(var, None)
    return env


def _run_git(repo_path: str, *args, timeout: int = 30) -> tuple[int, str, str]:
    """Run a read-only git command. Returns (returncode, stdout, stderr).

    Hardened against external diff execution:
    - GIT_EXTERNAL_DIFF, GIT_DIFF_OPTS, GIT_TEXTCONV, and related vars are
      explicitly unset before the subprocess starts.
    - All diff invocations include --no-ext-diff and --no-textconv flags.
    """
    # Always add hardened diff flags for diff-family commands to guard against
    # GIT_EXTERNAL_DIFF / GIT_TEXTCONV even if the caller forgets them.
    _args = list(args)
    if _args and _args[0] == "diff":
        _args.extend(["--no-ext-diff", "--no-textconv"])

    try:
        result = subprocess.run(
            ["git", "-C", repo_path] + _args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_build_sanitized_env(),
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "git command timed out"
    except FileNotFoundError:
        return -1, "", "git not found"


def collect_git_status(repo_path: str) -> dict:
    """Read-only: git status --porcelain"""
    rc, stdout, stderr = _run_git(repo_path, "status", "--porcelain")
    return {
        "command": "git status --porcelain",
        "returncode": rc,
        "stderr": stderr[:500] if stderr else "",
        "files": [line[3:] for line in stdout.splitlines() if line.strip()]
    }


def collect_git_diff(repo_path: str, base_sha: str) -> dict:
    """Read-only: git diff <base_sha>..HEAD

    On failure (nonzero rc), returns explicit failure state:
    - patch = ""
    - has_changes = null
    - git_rc = nonzero
    - git_error = truncated stderr
    Callers must NOT treat failure as "no changes".
    """
    rc, stdout, stderr = _run_git(repo_path, "diff", f"{base_sha}..HEAD")
    return {
        "command": f"git diff {base_sha}..HEAD",
        "git_rc": rc,
        "git_error": stderr[:500] if stderr else "",
        "patch": stdout if rc == 0 else "",
        "has_changes": bool(stdout.strip()) if rc == 0 else None,
        "failed": rc != 0,
    }


def collect_git_diff_name_only(repo_path: str, base_sha: str) -> tuple[list[str], dict]:
    """Read-only: git diff --name-only <base_sha>..HEAD

    Returns (files, meta) where meta contains git_rc, git_error, and failed.
    Callers must check meta['failed'] rather than treating [] as "no changes".
    """
    rc, stdout, stderr = _run_git(repo_path, "diff", "--name-only", f"{base_sha}..HEAD")
    meta = {
        "git_rc": rc,
        "git_error": stderr[:500] if stderr else "",
        "failed": rc != 0,
    }
    return (stdout.splitlines() if rc == 0 else [], meta)


def collect_git_rev_parse(repo_path: str, ref: str = "HEAD") -> str:
    """Read-only: git rev-parse"""
    rc, stdout, stderr = _run_git(repo_path, "rev-parse", ref)
    return stdout.strip() if rc == 0 else ""


def collect_changed_files_list(repo_path: str, base_sha: str) -> tuple[int, list[str], dict]:
    """Read-only: get changed file count, list, and git meta.

    Returns (count, files, meta) where meta contains git_rc, git_error, failed.
    Callers must check meta['failed'] instead of treating count==0 as "no changes".
    """
    files, meta = collect_git_diff_name_only(repo_path, base_sha)
    return len(files), files, meta


def collect_scope_check(source_repo: str, bundle_dir: str, base_sha: str) -> dict:
    """
    Read-only scope check using git commands.
    Uses resolved paths for symlink safety.

    On git failure, scope_clean is set to None (explicit unknown), not True.
    Callers must not treat git failure as "scope is clean".
    """
    source_resolved = Path(source_repo).resolve()
    bundle_resolved = Path(bundle_dir).resolve()
    repo_root = Path(__file__).resolve().parents[2]
    repo_root_resolved = repo_root.resolve()

    current_head = collect_git_rev_parse(source_repo, "HEAD")
    files_count, changed_files, git_meta = collect_changed_files_list(source_repo, base_sha)

    # Check whether bundle dir is outside repo root
    try:
        bundle_rel_to_repo = bundle_resolved.relative_to(repo_root_resolved)
        bundle_outside_repo_root = False
    except ValueError:
        # bundle dir is outside the repo root — allowed unless it violates other rules
        bundle_outside_repo_root = True

    # Check whether bundle dir is inside .git (already handled by validation,
    # but we verify with resolved path here for the record)
    bundle_in_git = ".git" in str(bundle_resolved)

    # Determine diff_status from git result and scope status
    diff_status = "unknown"
    if git_meta["failed"]:
        diff_status = "failed"
    elif files_count == 0:
        diff_status = "clean"
    else:
        diff_status = "dirty"

    # Explicit failure: do NOT set scope_clean=true when git failed
    if git_meta["failed"]:
        scope_clean = None
        scope_status = "failed"
    elif files_count == 0:
        scope_clean = True
        scope_status = "clean"
    else:
        scope_clean = False
        scope_status = "changed"

    return {
        "source_repo": str(source_resolved),
        "bundle_dir": str(bundle_resolved),
        "base_sha": base_sha,
        "current_head": current_head,
        "files_changed_count": files_count,
        "changed_files": changed_files[:100],  # cap at 100 for readability
        "bundle_dir_outside_repo_root": bundle_outside_repo_root,
        "bundle_dir_inside_git": bundle_in_git,
        "scope_clean": scope_clean,
        "scope_status": scope_status,
        "diff_status": diff_status,
        "git_rc": git_meta["git_rc"],
        "git_error": git_meta["git_error"],
    }


def collect_safety_grep(
    source_repo: str,
    bundle_dir: str,
    allowed_files: list[str] | None = None,
    forbidden_files: list[str] | None = None,
) -> dict:
    """
    Read-only safety grep: scan Python files in source_repo for forbidden
    executable mutation command strings.

    Classifies each match into one of:
    - executable_violation: forbidden command in executable context (subprocess.run,
      os.system, shell=True, exec(), etc.)
    - policy_mention: forbidden command in comment, docstring, argparse help,
      generated example, or policy constant
    - suppressed_context: match in a context that is documented/suppressed and
      not actionable

    clean_for_task is based only on executable_violations in allowed scope.
    Policy mentions and suppressed contexts do NOT make clean_for_task false.

    The distinction between executable and non-executable is determined by:
    1. is_suppressed_context() — checks for comment, docstring, argparse_help,
       generated_example
    2. is_executable_context() — checks for subprocess.run, os.system, shell=True,
       exec(, eval( — these make a match an executable violation even if it
       would otherwise look like a policy mention
    """
    patterns = list(EXECUTABLE_MUTATION_COMMANDS)

    source_resolved = Path(source_repo).resolve()
    py_files = []
    try:
        for root, dirs, files in os.walk(source_resolved):
            # Skip .git, hermes, .hermes, __pycache__, .pytest_cache
            dirs[:] = [d for d in dirs if d not in (
                ".git", "hermes", ".hermes", "__pycache__",
                ".pytest_cache", ".mypy_cache", "node_modules",
                ".tox", ".eggs", "*.egg-info"
            )]
            for fname in files:
                if fname.endswith(".py"):
                    py_files.append(os.path.join(root, fname))
    except PermissionError:
        return {
            "source_repo": str(source_resolved),
            "error": "PermissionError walking source_repo",
            "forbidden_patterns_found": [],
            "clean": None,
        }

    # Track per-classification match lists
    # Structure: {
    #   "executable_violations": [...],
    #   "policy_mentions": [...],
    #   "suppressed_contexts": [...],
    # }
    classified_matches = {
        "executable_violations": [],
        "policy_mentions": [],
        "suppressed_contexts": [],
    }

    for fpath in py_files:
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            continue

        rel_path = os.path.relpath(fpath, source_resolved)

        for lineno, line in enumerate(lines, 1):
            for pattern in patterns:
                if pattern not in line:
                    continue

                # Determine if this match is in a suppressed/policy context
                suppressed, reason = is_suppressed_context(line, pattern)
                # Override: if the line has executable-context patterns, it is
                # always an executable violation, not a policy mention
                is_exec_ctx = is_executable_context(line)

                if is_exec_ctx:
                    classification = "executable_violations"
                    reason = "executable_context"
                elif suppressed:
                    if reason in ("comment", "docstring"):
                        classification = "policy_mentions"
                    else:
                        classification = "suppressed_contexts"
                else:
                    # Not suppressed, not in executable context
                    # Check if it's in a non-test file that is clearly code
                    # (not a docstring or comment)
                    classification = "executable_violations"
                    reason = "unknown"

                match_entry = {
                    "pattern": pattern,
                    "line": lineno,
                    "text": line.rstrip(),
                    "file": rel_path,
                    "classification": classification,
                    "classification_reason": reason,
                }

                classified_matches[classification].append(match_entry)

    # Compute PRE-FILTER totals BEFORE test-file filtering.
    # These count ALL matches (including test files) for full transparency.
    # We call these "raw" counts — they represent what was found before
    # any suppression logic removed matches.
    # Naming convention: _raw postfix = pre-filter total.
    raw_executable_violations = len(classified_matches["executable_violations"])
    raw_policy_mentions = len(classified_matches["policy_mentions"])
    raw_suppressed_contexts = len(classified_matches["suppressed_contexts"])
    # raw_matches_total: all pattern hits before any suppression/filtering
    raw_matches_total = raw_executable_violations + raw_suppressed_contexts

    # Skip test files from all classified match lists
    for bucket in ("executable_violations", "policy_mentions", "suppressed_contexts"):
        classified_matches[bucket] = [
            m for m in classified_matches[bucket]
            if not (
                is_test_file(m["file"]) or
                m["file"].startswith("tests/") or
                m["file"].startswith("/tests/") or
                "/tests/" in m["file"] or
                m["file"].startswith("tests\\") or
                m["file"].startswith("\\tests\\")
            )
        ]

    # ---- Scope classification ----
    allowed_normalized = {_normalize_trailing_slash(p) for p in (allowed_files or [])}
    forbidden_normalized = {_normalize_trailing_slash(p) for p in (forbidden_files or [])}
    scope_applied = bool(allowed_normalized or forbidden_normalized)

    def _classify_path(rel_path: str) -> str:
        """Classify a matched file path against task scope."""
        for forbidden in forbidden_normalized:
            if rel_path.startswith(forbidden):
                return "inside_forbidden_scope"
        if allowed_normalized:
            for allowed in allowed_normalized:
                if rel_path.startswith(allowed):
                    return "inside_allowed_scope"
            return "outside_allowed_scope"
        else:
            return "inside_allowed_scope"

    # Scope-classify all matches
    for bucket_key in classified_matches:
        scoped_matches = []
        for m in classified_matches[bucket_key]:
            scope = _classify_path(m["file"])
            scoped_matches.append({**m, "scope_classification": scope})
        classified_matches[bucket_key] = scoped_matches

    # Build scope-bucketed lists using comprehensions (cleaner, no unbound variable risk)
    allowed_scope_executable_violations = [
        m for m in classified_matches["executable_violations"]
        if m["scope_classification"] == "inside_allowed_scope"
    ]
    forbidden_scope_executable_violations = [
        m for m in classified_matches["executable_violations"]
        if m["scope_classification"] == "inside_forbidden_scope"
    ]
    oos_scope_executable_violations = [
        m for m in classified_matches["executable_violations"]
        if m["scope_classification"] == "outside_allowed_scope"
    ]

    allowed_scope_policy_mentions = [
        m for m in classified_matches["policy_mentions"]
        if m["scope_classification"] == "inside_allowed_scope"
    ]
    forbidden_scope_policy_mentions = [
        m for m in classified_matches["policy_mentions"]
        if m["scope_classification"] == "inside_forbidden_scope"
    ]
    oos_scope_policy_mentions = [
        m for m in classified_matches["policy_mentions"]
        if m["scope_classification"] == "outside_allowed_scope"
    ]

    allowed_scope_suppressed = [
        m for m in classified_matches["suppressed_contexts"]
        if m["scope_classification"] == "inside_allowed_scope"
    ]
    forbidden_scope_suppressed = [
        m for m in classified_matches["suppressed_contexts"]
        if m["scope_classification"] == "inside_forbidden_scope"
    ]
    oos_scope_suppressed = [
        m for m in classified_matches["suppressed_contexts"]
        if m["scope_classification"] == "outside_allowed_scope"
    ]

    # clean_for_task: based only on executable violations in allowed scope
    clean_for_task = len(allowed_scope_executable_violations) == 0

    # files_scanned_in_allowed_scope
    files_in_allowed = set()
    for bucket_key in classified_matches:
        for m in classified_matches[bucket_key]:
            if m["scope_classification"] == "inside_allowed_scope":
                files_in_allowed.add(m["file"])
    files_scanned_in_allowed_scope = len(files_in_allowed)

    # Compute legacy-compatible "violations" bucket (same as allowed_scope_executable_violations)
    violations = [
        {k: v for k, v in m.items() if k != "scope_classification"}
        for m in allowed_scope_executable_violations
    ]

    # Total counts (POST-filter — after test-file removal)
    # These drive scope classification and actionable_violations.
    post_executable_violations = len(classified_matches["executable_violations"])
    post_policy_mentions = len(classified_matches["policy_mentions"])
    post_suppressed_contexts = len(classified_matches["suppressed_contexts"])

    # Build legacy-compatible dicts from classified matches
    forbidden_executable_by_path: dict[str, list] = {}
    forbidden_policy_by_path: dict[str, list] = {}
    for bucket_key, path_dict in [("executable_violations", forbidden_executable_by_path), ("policy_mentions", forbidden_policy_by_path)]:
        for m in classified_matches[bucket_key]:
            path = m["file"]
            if path not in path_dict:
                path_dict[path] = []
            path_dict[path].append(m)

    return {
        "source_repo": str(source_resolved),
        "bundle_dir": str(Path(bundle_dir).resolve()),
        "patterns_checked": list(patterns),
        # Legacy fields (backward compatibility)
        "files_scanned": len(py_files),
        "raw_matches": raw_matches_total,  # alias: pre-filter total (backward compat)
        "policy_mentions": post_policy_mentions,
        "test_or_context_matches": post_policy_mentions + post_suppressed_contexts,
        "actionable_violations": len(allowed_scope_executable_violations),
        "forbidden_executable_matches": forbidden_executable_by_path,  # legacy: executable violations by path (test files excluded)
        "forbidden_policy_mentions": forbidden_policy_by_path,  # legacy: policy mentions by path (test files excluded)
        "total_executable_matches": raw_executable_violations,  # pre-filter count (backward compat)
        "total_policy_mentions": raw_policy_mentions,  # pre-filter count (backward compat)
        "clean": clean_for_task,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # Scope
        "allowed_files": allowed_files or [],
        "forbidden_files": forbidden_files or [],
        "scope_applied": scope_applied,
        "files_scanned_total": len(py_files),
        "files_scanned_in_allowed_scope": files_scanned_in_allowed_scope,
        # Normalized explicit count fields — always present, never null
        "executable_matches_in_allowed_scope": len(allowed_scope_executable_violations),
        "executable_matches_in_forbidden_scope": len(forbidden_scope_executable_violations),
        "executable_matches_out_of_scope": len(oos_scope_executable_violations),
        "policy_mentions_in_allowed_scope": len(allowed_scope_policy_mentions),
        "policy_mentions_in_forbidden_scope": len(forbidden_scope_policy_mentions),
        "policy_mentions_out_of_scope": len(oos_scope_policy_mentions),
        "suppressed_context_in_allowed_scope": len(allowed_scope_suppressed),
        "suppressed_context_in_forbidden_scope": len(forbidden_scope_suppressed),
        "suppressed_context_out_of_scope": len(oos_scope_suppressed),
        # New classification-aware fields
        "executable_violations_count": raw_executable_violations,  # pre-filter: all executable violations before test-file suppression
        "policy_mentions_count": raw_policy_mentions,  # pre-filter: all policy mentions before test-file suppression
        "suppressed_context_count": raw_suppressed_contexts,  # pre-filter: all suppressed contexts before test-file suppression
        "raw_matches_total": raw_matches_total,  # pre-filter: all pattern hits before any suppression/filtering
        "post_filter_matches_total": post_executable_violations + post_suppressed_contexts,  # post-filter: remaining after test-file suppression,
        "executable_violations_in_allowed_scope": len(allowed_scope_executable_violations),
        "executable_violations_in_forbidden_scope": len(forbidden_scope_executable_violations),
        "executable_violations_out_of_scope": len(oos_scope_executable_violations),
        "clean_for_task": clean_for_task,
        "violations": violations,
        "matches_by_scope": {
            "allowed_scope_violations": [
                {k: v for k, v in m.items() if k != "scope_classification"}
                for m in allowed_scope_executable_violations
            ],
            "forbidden_scope_violations": [
                {k: v for k, v in m.items() if k != "scope_classification"}
                for m in forbidden_scope_executable_violations
            ],
            "out_of_scope_violations": [
                {k: v for k, v in m.items() if k != "scope_classification"}
                for m in oos_scope_executable_violations
            ],
            "allowed_scope_policy_mentions": [
                {k: v for k, v in m.items() if k != "scope_classification"}
                for m in allowed_scope_policy_mentions
            ],
            "forbidden_scope_policy_mentions": [
                {k: v for k, v in m.items() if k != "scope_classification"}
                for m in forbidden_scope_policy_mentions
            ],
            "out_of_scope_policy_mentions": [
                {k: v for k, v in m.items() if k != "scope_classification"}
                for m in oos_scope_policy_mentions
            ],
            "allowed_scope_suppressed": allowed_scope_suppressed,
            "forbidden_scope_suppressed": forbidden_scope_suppressed,
            "out_of_scope_suppressed": oos_scope_suppressed,
        },
    }


def collect_local_gate_preview(source_repo: str) -> dict:
    """
    Preview-only: lists commands that would be run during local gate,
    but does NOT execute them in Phase 2.

    Commands listed:
    - python3 -m compileall engine scripts
    - PYTHONPATH=. python3 -m pytest tests/... -q
    - bash scripts/ci/validate_governance_manifests.sh
    - bash scripts/ci/validate_event_options_contract.sh
    - git diff --check
    """
    repo_root = Path(__file__).resolve().parents[2]

    return {
        "phase": "Phase 2 (read-only preview — no execution)",
        "note": (
            "Phase 2 does NOT execute pytest, compileall, governance validators, "
            "or any local gate commands. It only previews what would run in a later phase."
        ),
        "preview_commands": [
            {
                "command": "python3 -m compileall engine scripts",
                "purpose": "Syntax/compile check of engine and scripts",
                "executed_in_phase2": False,
            },
            {
                "command": "PYTHONPATH=. python3 -m pytest tests/test_run_quarantine_autocoder_dry_run.py -q",
                "purpose": "Run quarantine autocoder unit tests",
                "executed_in_phase2": False,
            },
            {
                "command": (
                    "PYTHONPATH=. python3 -m pytest "
                    "tests/test_append_merge_action_audit.py "
                    "tests/test_pr_gate_kanban_task_create.py "
                    "tests/test_pr_gate_task_draft.py "
                    "tests/test_pr_gate_controller_live_smoke.py "
                    "tests/test_pr_gate_controller.py "
                    "tests/test_check_pr_scope.py "
                    "tests/test_merge_authorization_guard.py "
                    "tests/test_pr_gate_merge_ready_notify.py "
                    "tests/test_validate_ci_workflow_invariants.py -q"
                ),
                "purpose": "AED regression suite",
                "executed_in_phase2": False,
            },
            {
                "command": "bash scripts/ci/validate_governance_manifests.sh",
                "purpose": "Governance manifest validation",
                "executed_in_phase2": False,
            },
            {
                "command": "bash scripts/ci/validate_event_options_contract.sh",
                "purpose": "Event/options contract validation",
                "executed_in_phase2": False,
            },
            {
                "command": "git diff --check",
                "purpose": "Check for whitespace errors in working tree",
                "executed_in_phase2": False,
            },
        ],
        "local_gate_passed": None,
        "compiles": None,
        "tests_pass": None,
    }


# ---------------------------------------------------------------------------
# Bundle file generators
# ---------------------------------------------------------------------------

def compute_reviewer_summary(mode: str, diff_status: str, safety_clean: bool,
                              patch_applied: bool) -> str:
    """Generate a one-line reviewer summary for BUNDLE_STATUS.json."""
    if mode == "placeholder_bundle":
        patch_str = "patch not applied." if not patch_applied else "patch applied."
        diff_str = f"diff_status={diff_status}" if diff_status is not None else "no git diff run"
        return (
            f"Placeholder bundle. No read-only traces collected yet. "
            f"{diff_str}. {patch_str}"
        )
    # read_only_trace_collection
    if diff_status == "clean" and safety_clean and not patch_applied:
        return (
            "Read-only trace bundle. No repo changes detected. "
            "No actionable safety violations found."
        )
    elif diff_status == "clean" and safety_clean and patch_applied:
        return (
            "Read-only trace bundle. No repo changes. No actionable safety violations. "
            "Patch applied."
        )
    elif diff_status == "dirty":
        if safety_clean is None:
            safety_str = "safety status unknown"
        else:
            safety_str = f"actionable safety violations: {'yes' if not safety_clean else 'none'}"
        return (
            f"Read-only trace bundle. Repo changes detected. "
            f"{safety_str}."
        )
    elif diff_status in ("failed", "unknown"):
        if safety_clean is None:
            safety_str = "safety status unknown"
        else:
            safety_str = f"actionable safety violations: {'yes' if not safety_clean else 'none'}"
        return (
            f"Read-only trace bundle. git diff {diff_status}. "
            f"{safety_str}."
        )
    # safety_grep without scope collection (diff_status=None)
    if safety_clean is None:
        safety_str = "safety status unknown"
    else:
        safety_str = f"actionable safety violations: {'yes' if not safety_clean else 'none'}"
    return (
        f"Read-only trace bundle. {safety_str}."
    )


def write_bundle_status(bundle_dir: str, read_only_collections: dict,
                        scope_check: dict = None, safety_grep: dict = None) -> dict:
    # Determine mode: if any collection flag is True, mode is read_only_trace_collection;
    # otherwise mode is placeholder_bundle (Phase 1 style output)
    has_any_collection = any(read_only_collections.values())
    mode = "read_only_trace_collection" if has_any_collection else "placeholder_bundle"

    diff_status = None
    if scope_check:
        diff_status = scope_check.get("diff_status", None)
    safety_clean = None
    if safety_grep:
        if safety_grep.get("scope_applied"):
            safety_clean = safety_grep.get("clean_for_task", None)
        else:
            safety_clean = safety_grep.get("clean", None)

    patch_applied = False  # Phase 2 never applies a patch

    reviewer_summary = compute_reviewer_summary(mode, diff_status, safety_clean, patch_applied)

    status = {
        "phase": "Phase 2",
        "mode": mode,
        "reviewer_summary": reviewer_summary,
        "dry_run": True,
        "agent_executed": False,
        "patch_applied": False,
        "dispatch_occurred": False,
        "hermes_touched": False,
        "production_board_touched": False,
        "pr_created": False,
        "import_performed": False,
        "bundle_created_at": datetime.now(timezone.utc).isoformat(),
        "read_only_collections": read_only_collections,
        "warning": (
            "NO PATCH APPLIED — NO AGENT EXECUTED — NO HERMES TOUCHED — "
            "NO DISPATCH OCCURRED — NO PR CREATED — NO IMPORT PERFORMED"
        ),
    }
    path = os.path.join(bundle_dir, "BUNDLE_STATUS.json")
    with open(path, "w") as f:
        json.dump(status, f, indent=2)
    return status


def write_text_file(bundle_dir: str, filename: str, content: str) -> str:
    path = os.path.join(bundle_dir, filename)
    with open(path, "w") as f:
        f.write(content)
    return path


def write_markdown_file(bundle_dir: str, filename: str, content: str) -> str:
    path = os.path.join(bundle_dir, filename)
    with open(path, "w") as f:
        f.write(content)
    return path


def generate_codex_review_summary() -> dict:
    return {
        "phase": "Phase 2",
        "mode": "placeholder",
        "codex_reviewed": False,
        "note": (
            "Codex was not run in Phase 2. Read-only trace collection only. "
            "This file is a placeholder for future phases or manual review output. "
            "Codex review may be added in Phase 3 or later."
        ),
        "clean": None,
    }


def generate_risk_notes(base_sha: str, candidate_id: str, objective: str,
                        read_only_collections: dict) -> str:
    collected = [k for k, v in read_only_collections.items() if v]
    lines = [
        "# Risk Notes — Phase 2 Dry-Run Read-Only Traces",
        "",
        f"**base_sha**: {base_sha}",
        f"**candidate_id**: {candidate_id}",
        f"**objective**: {objective}",
        "",
        "## Phase 2 Disclaimer",
        "",
        "This bundle contains real read-only evidence from git operations:",
    ]
    if collected:
        for name in collected:
            lines.append(f"- `{name}`: collected")
    else:
        lines.append("- No read-only collectors were enabled (all --collect-* flags off)")
    lines.extend([
        "",
        "Phase 2 still does NOT:",
        "- Apply any patch",
        "- Execute any agent",
        "- Run pytest or compileall (local gate preview only)",
        "- Touch Hermes",
        "- Dispatch any Kanban task",
        "- Create any PR",
        "- Perform any import",
        "",
        "All git operations in Phase 2 are read-only.",
    ])
    return "\n".join(lines)


def generate_proposed_pr_body(bundle_dir: str, candidate_id: str, objective: str) -> str:
    bundle_dir_name = os.path.basename(bundle_dir)
    return (
        f"# Proposed PR Body — Phase 2 Dry-Run Read-Only Traces\n"
        f"\n"
        f"**candidate_id**: {candidate_id}\n"
        f"**objective**: {objective}\n"
        f"**bundle**: {bundle_dir_name}\n"
        f"\n"
        f"## Phase 2 Disclaimer\n"
        f"\n"
        f"This PR body is a SCAFFOLD PLACEHOLDER.\n"
        f"Phase 2 does NOT create a real PR. It only produces a bundle with read-only traces.\n"
        f"\n"
        f"## Next Steps\n"
        f"\n"
        f"- Phase 3 (if approved) would execute the real autocoder against the scaffold.\n"
        f"- Phase 4 (if approved) would create and merge a real PR.\n"
    )


def generate_import_command_sh(bundle_dir: str, candidate_id: str) -> str:
    return (
        "#!/bin/bash\n"
        "# import_command.sh — Phase 2 Dry-Run Read-Only Trace Collection\n"
        "#\n"
        "# WARNING: This file is NON-EXECUTABLE by default.\n"
        "# It contains commented instructions only.\n"
        "# No git push, gh pr create, gh pr merge, Hermes, or dispatch commands\n"
        "# are executed in Phase 1 or Phase 2.\n"
        "#\n"
        f"# bundle_dir : {bundle_dir}\n"
        f"# candidate_id: {candidate_id}\n"
        "#\n"
        "# Instructions:\n"
        "# 1. Review bundle contents in full.\n"
        "# 2. Phase 2 read-only traces are now populated in scope_check.json, safety_grep.txt,\n"
        "#    changed_files.txt, diff.patch, and local_gate.txt.\n"
        "# 3. Run local gate (compileall + pytest) manually before any import.\n"
        "# 4. Obtain human approval before running any executable import commands.\n"
        "# 5. Codex review the bundle before any import.\n"
        "#\n"
        "# === DO NOT UNCOMMENT OR EXECUTE ANYTHING BELOW THIS LINE ===\n"
        "#\n"
        "# git fetch origin <base-sha>\n"
        "# git diff <base-sha>..HEAD -- > diff.patch\n"
        "# gh pr create --title '...'\n"
        "# gh pr merge --admin --squash\n"
        "# hermes kanban dispatch --max 1\n"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Phase 2 Quarantine Autocoder — Dry-Run Read-Only Trace Collection",
        epilog=(
            "Phase 2 produces a bundle with read-only evidence collection. "
            "No patch applied, no agent executed, Hermes untouched, no dispatch, no PR, no import. "
            "All git operations are read-only."
        ),
    )
    parser.add_argument("--source-repo", required=True, help="Path to source repository")
    parser.add_argument("--bundle-dir", required=True, help="Output directory for bundle")
    parser.add_argument("--base-sha", required=True, help="40-char hex commit SHA")
    parser.add_argument("--candidate-id", required=True, help="Safe slug identifier")
    parser.add_argument("--objective", required=True, help="Objective description")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="REQUIRED flag. Refuses to run without this.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite or re-run into an existing non-empty bundle-dir",
    )
    # Phase 2 read-only collection flags
    parser.add_argument(
        "--collect-scope",
        action="store_true",
        help="Run read-only git scope check (git diff --name-only, git rev-parse HEAD)",
    )
    parser.add_argument(
        "--collect-safety-grep",
        action="store_true",
        help="Run read-only safety grep (scan .py files for forbidden mutation commands)",
    )
    parser.add_argument(
        "--collect-local-gate-preview",
        action="store_true",
        help="Write local gate preview (commands that WOULD run — not executed in Phase 2)",
    )
    parser.add_argument(
        "--collect-git-diff",
        action="store_true",
        help="Run read-only git diff (populate diff.patch and changed_files.txt)",
    )
    parser.add_argument(
        "--allowed-files-json",
        type=str,
        default=None,
        help="JSON-encoded list of allowed file patterns, e.g. '[\"docs/\", \"scripts/\"]'",
    )
    parser.add_argument(
        "--forbidden-files-json",
        type=str,
        default=None,
        help="JSON-encoded list of forbidden file patterns, e.g. '[\"scripts/\", \".github/\"]'",
    )
    args = parser.parse_args(argv)

    # ---- Dry-run enforcement ----
    if not args.dry_run:
        print("ERROR: --dry-run is REQUIRED. Refusing to run.")
        print(
            "This tool is Phase 2 dry-run only. "
            "It will not execute without --dry-run."
        )
        sys.exit(1)

    # ---- Early scope JSON validation (before ANY bundle files are written) ----
    # Validate BEFORE bundle_dir is created so malformed JSON fails fast with no partial bundle.
    parsed_allowed = None
    parsed_forbidden = None
    if args.allowed_files_json is not None or args.forbidden_files_json is not None:
        try:
            parsed_allowed, parsed_forbidden = validate_scope_json_args(
                args.allowed_files_json, args.forbidden_files_json
            )
        except SystemExit:
            raise  # re-raise sys.exit(1) from validate_scope_json_args

    # ---- Validations ----
    try:
        validate_base_sha(args.base_sha)
    except ValueError as e:
        print(f"VALIDATION ERROR: {e}")
        sys.exit(1)

    try:
        validate_candidate_id(args.candidate_id)
    except ValueError as e:
        print(f"VALIDATION ERROR: {e}")
        sys.exit(1)

    try:
        validate_source_repo(args.source_repo)
    except ValueError as e:
        print(f"VALIDATION ERROR: {e}")
        sys.exit(1)

    try:
        validate_bundle_dir(args.bundle_dir, args.force)
    except ValueError as e:
        print(f"VALIDATION ERROR: {e}")
        sys.exit(1)

    # ---- Clean bundle dir under --force ----
    if args.force and os.path.isdir(args.bundle_dir):
        for entry in os.listdir(args.bundle_dir):
            entry_path = os.path.join(args.bundle_dir, entry)
            if os.path.isfile(entry_path) or os.path.islink(entry_path):
                os.remove(entry_path)
            elif os.path.isdir(entry_path):
                import shutil
                shutil.rmtree(entry_path)
    os.makedirs(args.bundle_dir, exist_ok=True)

    # ---- Read-only collection state ----
    read_only_collections = {
        "collect_scope": args.collect_scope,
        "collect_safety_grep": args.collect_safety_grep,
        "collect_local_gate_preview": args.collect_local_gate_preview,
        "collect_git_diff": args.collect_git_diff,
    }

    # ---- BUNDLE_STATUS.json ----
    # Collect scope_check and safety_grep results first so we can compute reviewer_summary
    scope_check = None
    safety_grep = None
    if args.collect_scope:
        scope_check = collect_scope_check(args.source_repo, args.bundle_dir, args.base_sha)
    if args.collect_safety_grep:
        # Parse allowed/forbidden scope from JSON args (before BUNDLE_STATUS.json computation)
        parsed_allowed = None
        parsed_forbidden = None
        if args.allowed_files_json is not None:
            try:
                parsed_allowed = json.loads(args.allowed_files_json)
                if not isinstance(parsed_allowed, list):
                    raise ValueError("allowed_files_json must be a JSON array")
                _validate_scope_path_list(parsed_allowed, "allowed_files")
            except json.JSONDecodeError as e:
                print(f"VALIDATION ERROR: --allowed-files-json is not valid JSON: {e}")
                sys.exit(1)
            except ValueError as e:
                print(f"VALIDATION ERROR: {e}")
                sys.exit(1)
        if args.forbidden_files_json is not None:
            try:
                parsed_forbidden = json.loads(args.forbidden_files_json)
                if not isinstance(parsed_forbidden, list):
                    raise ValueError("forbidden_files_json must be a JSON array")
                _validate_scope_path_list(parsed_forbidden, "forbidden_files")
            except json.JSONDecodeError as e:
                print(f"VALIDATION ERROR: --forbidden-files-json is not valid JSON: {e}")
                sys.exit(1)
            except ValueError as e:
                print(f"VALIDATION ERROR: {e}")
                sys.exit(1)

        safety_grep = collect_safety_grep(
            args.source_repo,
            args.bundle_dir,
            allowed_files=parsed_allowed,
            forbidden_files=parsed_forbidden,
        )
    status = write_bundle_status(args.bundle_dir, read_only_collections,
                                  scope_check=scope_check, safety_grep=safety_grep)
    print(f"[Phase 2] Wrote BUNDLE_STATUS.json (read_only_collections={read_only_collections})")

    # ---- Static text files ----
    write_text_file(args.bundle_dir, "base_sha.txt", args.base_sha)
    print(f"[Phase 2] Wrote base_sha.txt")

    write_text_file(args.bundle_dir, "candidate_id.txt", args.candidate_id)
    print(f"[Phase 2] Wrote candidate_id.txt")

    write_markdown_file(args.bundle_dir, "objective.md", f"# Objective\n{args.objective}\n")
    print(f"[Phase 2] Wrote objective.md")

    # ---- Read-only: git diff ----
    if args.collect_git_diff:
        diff_result = collect_git_diff(args.source_repo, args.base_sha)
        diff_content = diff_result.get("patch", "")
        if diff_result.get("failed"):
            # Explicit failure: report it, do not claim "no changes"
            diff_content = (
                f"# git diff {args.base_sha}..HEAD FAILED\n"
                f"# git_rc={diff_result.get('git_rc')}\n"
                f"# git_error={diff_result.get('git_error')!r}\n"
                f"# Collection failed — check git_rc and git_error above.\n"
            )
        elif not diff_content:
            diff_content = (
                f"# git diff {args.base_sha}..HEAD returned no output\n"
                f"# No changes between {args.base_sha} and HEAD\n"
            )
        write_text_file(args.bundle_dir, "diff.patch", diff_content)
        print(f"[Phase 2] Wrote diff.patch (read-only git diff, {len(diff_content)} chars)")

        # changed_files.txt from git diff --name-only
        files_count, changed_files, git_meta = collect_changed_files_list(args.source_repo, args.base_sha)
        if git_meta.get("failed"):
            changed_files_content = (
                f"# git diff --name-only FAILED\n"
                f"# git_rc={git_meta.get('git_rc')}\n"
                f"# git_error={git_meta.get('git_error')!r}\n"
                f"# changed_files could not be enumerated.\n"
            )
        elif changed_files:
            changed_files_content = "\n".join(changed_files)
        else:
            changed_files_content = "(no changed files)"
        write_text_file(args.bundle_dir, "changed_files.txt", changed_files_content)
        print(f"[Phase 2] Wrote changed_files.txt ({files_count} files)")
    else:
        # Phase 2 default: placeholder
        write_text_file(
            args.bundle_dir, "changed_files.txt",
            "(placeholder — no git diff run in Phase 2. Use --collect-git-diff to populate.)\n"
        )
        print(f"[Phase 2] Wrote changed_files.txt (placeholder)")

        diff_content = (
            f"# diff.patch — Phase 2 placeholder\n"
            f"# No diff computed. Use --collect-git-diff to populate.\n"
            f"# git diff {args.base_sha}..HEAD would populate this file.\n"
        )
        write_text_file(args.bundle_dir, "diff.patch", diff_content)
        print(f"[Phase 2] Wrote diff.patch (placeholder)")

    # ---- Read-only: scope check ----
    if args.collect_scope:
        scope_result = collect_scope_check(args.source_repo, args.bundle_dir, args.base_sha)
        # Inject scope metadata from early-validated JSON args (available even when
        # --collect-scope is used without --collect-safety-grep).
        scope_result["allowed_files"] = parsed_allowed if parsed_allowed is not None else []
        scope_result["forbidden_files"] = parsed_forbidden if parsed_forbidden is not None else []
        scope_result["scope_applied"] = bool(parsed_allowed or parsed_forbidden)
        scope_check_path = os.path.join(args.bundle_dir, "scope_check.json")
        with open(scope_check_path, "w") as f:
            json.dump(scope_result, f, indent=2)
        print(f"[Phase 2] Wrote scope_check.json (read-only git, {scope_result.get('files_changed_count', 0)} files)")
    else:
        # Placeholder
        scope_check = {
            "source_repo": args.source_repo,
            "base_sha": args.base_sha,
            "note": "Phase 2: scope check is a placeholder. Use --collect-scope to run read-only git scope check.",
            "files_changed_count": "unknown (not computed)",
            "scope_clean": None,
        }
        scope_check_path = os.path.join(args.bundle_dir, "scope_check.json")
        with open(scope_check_path, "w") as f:
            json.dump(scope_check, f, indent=2)
        print(f"[Phase 2] Wrote scope_check.json (placeholder)")

    # ---- Read-only: safety grep ----
    if args.collect_safety_grep:
        # parsed_allowed / parsed_forbidden already validated in early validation block above.
        # Reuse them directly — do not re-parse to keep early-validation guarantee.

        safety_grep_result = collect_safety_grep(
            args.source_repo,
            args.bundle_dir,
            allowed_files=parsed_allowed,
            forbidden_files=parsed_forbidden,
        )
        safety_grep_path = os.path.join(args.bundle_dir, "safety_grep.txt")
        with open(safety_grep_path, "w") as f:
            # Human-readable summary header
            f.write("# Safety Grep Summary\n")
            f.write(f"scope_applied: {str(safety_grep_result.get('scope_applied', False)).lower()}\n")
            f.write(f"clean_for_task: {str(safety_grep_result.get('clean_for_task', False)).lower()}\n")
            f.write(f"executable_violations_in_allowed_scope: {safety_grep_result.get('executable_violations_in_allowed_scope', 0)}\n")
            f.write(f"policy_mentions_in_allowed_scope: {safety_grep_result.get('policy_mentions_in_allowed_scope', 0)}\n")
            f.write(f"suppressed_context_in_allowed_scope: {safety_grep_result.get('suppressed_context_in_allowed_scope', 0)}\n")
            f.write(f"executable_violations_count: {safety_grep_result.get('executable_violations_count', 0)}\n")
            f.write(f"policy_mentions_count: {safety_grep_result.get('policy_mentions_count', 0)}\n")
            f.write(f"suppressed_context_count: {safety_grep_result.get('suppressed_context_count', 0)}\n")
            f.write(f"policy_mentions: {safety_grep_result.get('policy_mentions_total', safety_grep_result.get('policy_mentions', 0))}\n")
            f.write(f"actionable_violations: {safety_grep_result.get('actionable_violations', 0)}\n")
            f.write(f"files_scanned: {safety_grep_result.get('files_scanned_total', 0)}\n")
            f.write(f"files_scanned_total: {safety_grep_result.get('files_scanned_total', 0)}\n")
            f.write(f"files_scanned_in_allowed_scope: {safety_grep_result.get('files_scanned_in_allowed_scope', 0)}\n")
            f.write(f"raw_matches: {safety_grep_result.get('raw_matches', 0)}\n")
            f.write(f"executable_matches_total: {safety_grep_result.get('executable_matches_total', 0)}\n")
            f.write(f"executable_matches_in_allowed_scope: {safety_grep_result.get('executable_matches_in_allowed_scope', 0)}\n")
            f.write(f"executable_matches_in_forbidden_scope: {safety_grep_result.get('executable_matches_in_forbidden_scope', 0)}\n")
            f.write(f"executable_matches_out_of_scope: {safety_grep_result.get('executable_matches_out_of_scope', 0)}\n")
            f.write(f"policy_mentions_in_forbidden_scope: {safety_grep_result.get('policy_mentions_in_forbidden_scope', 0)}\n")
            f.write(f"suppressed_context_in_forbidden_scope: {safety_grep_result.get('suppressed_context_in_forbidden_scope', 0)}\n")
            f.write(f"clean: {str(safety_grep_result.get('clean', safety_grep_result.get('clean_for_task', False))).lower()}\n")
            f.write(f"details_format: json_below\n")
            f.write(f"violations_only_file: violations_only.json\n")
            f.write("\n")
            # Then full JSON
            json.dump(safety_grep_result, f, indent=2)
        print(f"[Phase 2] Wrote safety_grep.txt (read-only scan, "
              f"{safety_grep_result.get('files_scanned_total', 0)} files total, "
              f"{safety_grep_result.get('executable_matches_in_allowed_scope', 0)} in allowed scope, "
              f"{safety_grep_result.get('executable_matches_in_forbidden_scope', 0)} in forbidden scope)")
        # Write violations_only.json — normalized bucket format with scope classification
        # All count fields are always explicit integers (0, never null).
        # All array fields are always arrays ([] if empty).
        # clean_for_task is based on allowed_scope executable violations only.
        # Policy mentions and suppressed contexts do NOT dirty the task.
        matches_by_scope = safety_grep_result.get("matches_by_scope", {})
        allowed_violations = matches_by_scope.get("allowed_scope_violations", [])
        forbidden_violations = matches_by_scope.get("forbidden_scope_violations", [])
        oos_violations = matches_by_scope.get("out_of_scope_violations", [])

        # New classification-aware counts
        allowed_scope_policy = matches_by_scope.get("allowed_scope_policy_mentions", [])
        forbidden_scope_policy = matches_by_scope.get("forbidden_scope_policy_mentions", [])
        oos_scope_policy = matches_by_scope.get("out_of_scope_policy_mentions", [])

        allowed_scope_supp = matches_by_scope.get("allowed_scope_suppressed", [])
        forbidden_scope_supp = matches_by_scope.get("forbidden_scope_suppressed", [])
        oos_scope_supp = matches_by_scope.get("out_of_scope_suppressed", [])

        violations_only_payload = {
            # Backward-compatible keys
            "actionable_violations": len(allowed_violations),
            "violations": allowed_violations,
            # Scope classification
            "scope_applied": safety_grep_result.get("scope_applied", False),
            "clean_for_task": safety_grep_result.get("clean_for_task", True),
            # Normalized explicit count fields — always present, never null
            "executable_matches_in_allowed_scope": len(allowed_violations),
            "executable_matches_in_forbidden_scope": len(forbidden_violations),
            "executable_matches_out_of_scope": len(oos_violations),
            "allowed_scope_violations_count": len(allowed_violations),
            "forbidden_scope_violations_count": len(forbidden_violations),
            "out_of_scope_violations_count": len(oos_violations),
            # Classification-aware counts (new)
            "executable_violations_count": safety_grep_result.get("executable_violations_count", 0),
            "policy_mentions_count": safety_grep_result.get("policy_mentions_count", 0),
            "suppressed_context_count": safety_grep_result.get("suppressed_context_count", 0),
            "policy_mentions_in_allowed_scope": len(allowed_scope_policy),
            "policy_mentions_in_forbidden_scope": len(forbidden_scope_policy),
            "policy_mentions_out_of_scope": len(oos_scope_policy),
            "suppressed_context_in_allowed_scope": len(allowed_scope_supp),
            "suppressed_context_in_forbidden_scope": len(forbidden_scope_supp),
            "suppressed_context_out_of_scope": len(oos_scope_supp),
            # Normalized explicit arrays — always present, never null
            "allowed_scope_violations": allowed_violations,
            "forbidden_scope_violations": forbidden_violations,
            "out_of_scope_violations": oos_violations,
            "allowed_scope_policy_mentions": allowed_scope_policy,
            "forbidden_scope_policy_mentions": forbidden_scope_policy,
            "out_of_scope_policy_mentions": oos_scope_policy,
            "allowed_scope_suppressed": allowed_scope_supp,
            "forbidden_scope_suppressed": forbidden_scope_supp,
            "out_of_scope_suppressed": oos_scope_supp,
            # Task cleanliness summary
            "task_clean_summary": {
                "allowed_scope": "clean" if len(allowed_violations) == 0 else "dirty",
                "forbidden_scope": "dirty" if len(forbidden_violations) > 0 else "clean",
                "out_of_scope_suppressed": len(oos_violations),
                "clean_for_task": safety_grep_result.get("clean_for_task", True),
            },
            # Aggregated counts for the summary
            "summary": {
                "total": len(allowed_violations) + len(forbidden_violations) + len(oos_violations),
                "in_allowed_scope": len(allowed_violations),
                "in_forbidden_scope": len(forbidden_violations),
                "out_of_scope": len(oos_violations),
            },
        }
        violations_only_path = os.path.join(args.bundle_dir, "violations_only.json")
        with open(violations_only_path, "w") as f:
            json.dump(violations_only_payload, f, indent=2)
        print(f"[Phase 2] Wrote violations_only.json "
              f"(clean_for_task={safety_grep_result.get('clean_for_task', True)}, "
              f"allowed_scope={len(matches_by_scope.get('allowed_scope_violations', []))}, "
              f"forbidden_scope={len(matches_by_scope.get('forbidden_scope_violations', []))})")
    else:
        # Placeholder
        safety_grep_placeholder = {
            "source_repo": args.source_repo,
            "note": "Phase 2: safety grep is a placeholder. Use --collect-safety-grep to scan for forbidden commands.",
            "forbidden_patterns_found": [],
            "clean": None,
        }
        safety_grep_path = os.path.join(args.bundle_dir, "safety_grep.txt")
        with open(safety_grep_path, "w") as f:
            json.dump(safety_grep_placeholder, f, indent=2)
        print(f"[Phase 2] Wrote safety_grep.txt (placeholder)")

    # ---- Read-only: local gate preview ----
    if args.collect_local_gate_preview:
        local_gate_preview = collect_local_gate_preview(args.source_repo)
        local_gate_path = os.path.join(args.bundle_dir, "local_gate.txt")
        with open(local_gate_path, "w") as f:
            json.dump(local_gate_preview, f, indent=2)
        print(f"[Phase 2] Wrote local_gate.txt (preview — no pytest/compileall executed)")
    else:
        local_gate_placeholder = {
            "phase": "Phase 2",
            "local_gate_passed": None,
            "note": "Phase 2: local gate is a preview placeholder. Use --collect-local-gate-preview to list commands.",
            "compiles": None,
            "tests_pass": None,
        }
        local_gate_path = os.path.join(args.bundle_dir, "local_gate.txt")
        with open(local_gate_path, "w") as f:
            json.dump(local_gate_placeholder, f, indent=2)
        print(f"[Phase 2] Wrote local_gate.txt (placeholder)")

    # ---- Codex summary: still placeholder in Phase 2 ----
    codex_summary = generate_codex_review_summary()
    codex_path = os.path.join(args.bundle_dir, "codex_review_summary.md")
    with open(codex_path, "w") as f:
        json.dump(codex_summary, f, indent=2)
    print(f"[Phase 2] Wrote codex_review_summary.md (placeholder — no Codex run in Phase 2)")

    # ---- Markdown files ----
    write_markdown_file(args.bundle_dir, "risk_notes.md",
                        generate_risk_notes(args.base_sha, args.candidate_id, args.objective, read_only_collections))
    print(f"[Phase 2] Wrote risk_notes.md")

    write_markdown_file(args.bundle_dir, "proposed_pr_body.md",
                        generate_proposed_pr_body(args.bundle_dir, args.candidate_id, args.objective))
    print(f"[Phase 2] Wrote proposed_pr_body.md")

    # ---- import_command.sh — non-executable by default ----
    import_sh = generate_import_command_sh(args.bundle_dir, args.candidate_id)
    import_sh_path = os.path.join(args.bundle_dir, "import_command.sh")
    with open(import_sh_path, "w") as f:
        f.write(import_sh)
    os.chmod(import_sh_path, 0o644)
    print(f"[Phase 2] Wrote import_command.sh (non-executable, commented only)")

    print()
    print("=== Phase 2 Bundle Complete (Read-Only Traces) ===")
    print(f"Bundle: {args.bundle_dir}")
    print(f"Dry-run: {status['dry_run']}")
    print(f"Read-only collections: {read_only_collections}")
    print(f"Agent executed: {status['agent_executed']}")
    print(f"Patch applied: {status['patch_applied']}")
    print(f"Dispatch occurred: {status['dispatch_occurred']}")
    print(f"Hermes touched: {status['hermes_touched']}")
    print(f"Production board touched: {status['production_board_touched']}")
    print(f"PR created: {status['pr_created']}")
    print(f"Import performed: {status['import_performed']}")
    print()
    print("NO PATCH APPLIED — NO AGENT EXECUTED — NO HERMES TOUCHED")
    print("NO DISPATCH OCCURRED — NO PR CREATED — NO IMPORT PERFORMED")
    print("All git operations in Phase 2 are READ-ONLY.")
    return 0


if __name__ == "__main__":
    sys.exit(main())