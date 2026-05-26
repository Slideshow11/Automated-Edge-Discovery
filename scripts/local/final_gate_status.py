#!/usr/bin/env python3
"""
final_gate_status.py — Canonical AED PR final-gate status reporter.

Evaluates a PR against all pre-merge gates and returns a single actionable state:
    READY_TO_MERGE
    HOLD_<reason>

No execution, no merging, no dispatch, no board touch, no Hermes mutation.

Usage:
    python3 scripts/local/final_gate_status.py \
        --pr-number 265 \
        --reported-head-sha <sha> \
        --codex-reviewed-sha <sha> \
        --pmg-guard-state-json /tmp/pmg_compare_pr265.json \
        [--output-json /tmp/final_gate_pr265.json] \
        [--output-md /tmp/final_gate_pr265.md] \
        [--repo Slideshow11/Automated-Edge-Discovery]

Exit codes:
    0  — output written successfully (any state)
    1  — fatal error (missing required arg, gh not installed, etc.)
"""

import argparse
import json
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# State definitions
# ---------------------------------------------------------------------------

class State:
    READY_TO_MERGE = "READY_TO_MERGE"
    HOLD_CI_RED = "HOLD_CI_RED"
    HOLD_CODEX_REQUIRED = "HOLD_CODEX_REQUIRED"
    HOLD_CODEX_STALE = "HOLD_CODEX_STALE"
    HOLD_REVIEW_COMMENTS_BLOCKED = "HOLD_REVIEW_COMMENTS_BLOCKED"
    HOLD_REVIEW_COMMENTS_INCONCLUSIVE = "HOLD_REVIEW_COMMENTS_INCONCLUSIVE"
    HOLD_PMG_MISSING = "HOLD_PMG_MISSING"
    HOLD_PMG_DIRTY = "HOLD_PMG_DIRTY"
    HOLD_HEAD_MISMATCH = "HOLD_HEAD_MISMATCH"
    HOLD_PR_NOT_OPEN = "HOLD_PR_NOT_OPEN"
    HOLD_GIT_DIRTY = "HOLD_GIT_DIRTY"
    HOLD_UNKNOWN = "HOLD_UNKNOWN"


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AED final gate status reporter. Returns READY_TO_MERGE or HOLD_<reason>.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Example:
                python3 scripts/local/final_gate_status.py \\
                    --pr-number 265 \\
                    --reported-head-sha abc123def... \\
                    --codex-reviewed-sha abc123def... \\
                    --pmg-guard-state-json /tmp/pmg_compare_265.json
        """),
    )
    parser.add_argument(
        "--pr-number", type=int, required=True,
        help="GitHub PR number",
    )
    parser.add_argument(
        "--reported-head-sha",
        help="40-char SHA reported by a prior tool or user (exact hex)",
    )
    parser.add_argument(
        "--codex-reviewed-sha",
        help="SHA that Codex explicitly reviewed (exact hex). Required for READY_TO_MERGE.",
    )
    parser.add_argument(
        "--pmg-guard-state-json",
        help="Path to PMG compare output JSON (must have status=clean)",
    )
    parser.add_argument(
        "--output-json",
        help="Path to write result JSON (optional)",
    )
    parser.add_argument(
        "--output-md",
        help="Path to write result Markdown (optional)",
    )
    parser.add_argument(
        "--repo",
        default="Slideshow11/Automated-Edge-Discovery",
        help="GitHub repository (default: Slideshow11/Automated-Edge-Discovery)",
    )
    parser.add_argument(
        "--allow-docs-only-codex-waiver",
        action="store_true",
        default=False,
        help=(
            "When set and the PR contains only documentation changes "
            "(markdown/text files under docs/, README.md, etc.), "
            "Codex exact-head review may be waived for the final gate."
        ),
    )
    parser.add_argument(
        "--review-comments-json",
        help=(
            "Path to a check_pr_review_comments.py output JSON. "
            "When supplied, a fresh local BLOCKED or INCONCLUSIVE review-comment "
            "gate result will override the Codex SHA check and cause a HOLD, "
            "preventing merge authorization even when --codex-reviewed-sha matches. "
            "This closes the gap where Codex posts comments after CI completes. "
            "If the file is absent or invalid, a HOLD is issued."
        ),
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# GitHub data fetching
# ---------------------------------------------------------------------------

def gh_json(args: list[str]) -> dict:
    """Run gh with --json and return parsed output. Raises RuntimeError on failure.

    All gh api calls use list-form args with no shell interpolation.
    --jq '.' is placed at the end of the argument list (required for gh 2.x).
    """
    cmd = ["gh", "api"] + args + ["--jq", "."]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError("gh CLI not found in PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError("gh API call timed out after 30s")
    if result.returncode != 0:
        raise RuntimeError(f"gh failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def fetch_pr_state(pr_number: int, repo: str) -> dict:
    """Fetch PR state, head SHA, mergeability, and check rollup."""
    query = """
        query($owner: String!, $repo: String!, $pr: Int!) {
            repository(owner: $owner, name: $repo) {
                pullRequest(number: $pr) {
                    state
                    headRefOid
                    mergeable
                    isDraft
                    title
                    url
                    commits(last: 1) {
                        nodes {
                            commit {
                                oid
                                statusCheckRollup {
                                    state
                                }
                            }
                        }
                    }
                }
            }
        }
    """
    data = gh_json([
        "graphql",
        "-f", f"query={query}",
        "-F", f"owner={repo.split('/')[0]}",
        "-F", f"repo={repo.split('/')[1]}",
        "-F", f"pr={pr_number}",
    ])
    repo_data = data.get("data", {}).get("repository", {})
    pr_data = repo_data.get("pullRequest")
    if pr_data is None:
        raise RuntimeError(f"PR #{pr_number} not found in {repo}")
    return pr_data


def get_pr_changed_files(pr_number: int, repo: str, head_sha: str) -> list[str]:
    """
    Return the list of files changed between main and the given head SHA.

    Uses ``git diff --name-only main...<head_sha>`` locally so it respects
    local branch state and never touches the network beyond the local repo.
    cwd is always the current working directory (the local repo root), not
    the GitHub repo slug passed via --repo.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"main...{head_sha}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        raise RuntimeError("git not found in PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError("git diff timed out")
    if result.returncode != 0:
        raise RuntimeError(f"git diff failed: {result.stderr.strip()}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def get_required_checks(pr_number: int, repo: str, head_sha: str) -> list[dict]:
    """Get the latest commit status for a given SHA."""
    try:
        status = gh_json([
            f"repos/{repo}/commits/{head_sha}/status",
        ])
        return status.get("statuses", [])
    except Exception:
        return []


def get_workflow_runs(pr_number: int, repo: str, head_sha: str) -> list[dict]:
    """Get workflow runs for the PR's head SHA."""
    try:
        runs = gh_json([
            f"repos/{repo}/actions/runs",
            "--jq", ".workflow_runs",
            "--method", "GET",
        ])
        # Filter to runs associated with this PR's head SHA
        filtered = [r for r in runs if r.get("head_sha") == head_sha]
        return filtered
    except Exception:
        return []


def is_ci_green(pr_number: int, repo: str, head_sha: str) -> tuple[bool, str]:
    """
    Determine if CI is green for a given PR head SHA.
    Checks:
    - GitHub Actions status via workflow runs
    - Each run must have conclusion=success

    Returns (is_green, reason).
    """
    try:
        # Note: gh_json appends --jq "." at the end. For actions/runs we
        # need a custom --jq filter, so we cannot pass --jq in args (it
        # would be overwritten by gh_json's append). Instead, fetch
        # workflow_runs directly and filter in Python.
        all_runs = gh_json([
            f"repos/{repo}/actions/runs",
        ])
        runs = [r for r in all_runs.get("workflow_runs", []) if r.get("head_sha") == head_sha]
    except Exception as e:
        return False, f"Failed to fetch workflow runs: {e}"

    if not runs:
        return False, f"No workflow runs found for SHA {head_sha[:8]}"

    all_success = True
    failing = []
    for run in runs:
        conclusion = run.get("conclusion")
        status = run.get("status")
        name = run.get("name", "unknown")
        if status in ("queued", "in_progress", "requested", "waiting", "startup_failure"):
            return False, f"Workflow '{name}' is {status}"
        if conclusion != "success":
            all_success = False
            failing.append(f"{name} ({conclusion or status})")

    if not all_success:
        return False, f"Failed checks: {'; '.join(failing)}"

    return True, f"All {len(runs)} workflow run(s) succeeded"


# --------------------------------------------------------------------------
# Documentation-only diff classifier
# --------------------------------------------------------------------------

# Safe documentation paths — these never contain executable code, tests,
# workflows, schemas, configs, or any other gate-relevant artifacts.
_DOCS_ONLY_PREFIXES: tuple[str, ...] = (
    "docs/",
    "doc/",
)

# Safe root-level documentation filenames.
_DOCS_ONLY_ROOT_FILES: frozenset[str] = frozenset({
    "README.md",
    "CHANGELOG.md",
    "CHANGES.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "LICENSE",
    "LICENSE.md",
    "SECURITY.md",
    "SUPPORT.md",
    "GOVERNANCE.md",
    "docs.md",
    "ROADMAP.md",
    "FAQ.md",
    "Glossary.md",
    "glossary.md",
})

# File extensions that are always safe for documentation-only PRs.
_DOCS_ONLY_EXTENSIONS: frozenset[str] = frozenset({".md", ".txt", ".rst"})


def is_docs_only(paths: list[str]) -> bool:
    """
    Return True if every path in ``paths`` is a documentation-only file.

    A docs-only file is one that:
      - Lives under docs/ or doc/
      - Is a known root-level documentation filename (README.md, etc.)
      - Has a documentation-safe extension (.md, .txt, .rst) under docs/

    Any path that is a directory, an executable, a script, a test, a workflow,
    a config file, a schema, or any file with a non-doc extension is NOT
    docs-only, even if it lives inside docs/ (e.g. docs/script.py).
    """
    if not paths:
        return False

    for path in paths:
        p = path.startswith("./") and path[2:] or path
        if p.endswith("/"):
            return False
        # Determine whether this is a top-level (root) path or a nested path.
        # Only root-level filenames (not inside any directory) are allowed
        # as a docs-only safe list.
        is_root = "/" not in p
        filename = p.rsplit("/", 1)[-1] if not is_root else p

        # Root-level known doc files (README.md, LICENSE, etc.) are safe
        # only when they are at the repo root — not inside subdirectories.
        if is_root and filename in _DOCS_ONLY_ROOT_FILES:
            continue
        if any(p.startswith(prefix) for prefix in _DOCS_ONLY_PREFIXES):
            ext = filename[filename.rfind("."):] if "." in filename else ""
            if ext in _DOCS_ONLY_EXTENSIONS:
                continue
            return False
        return False

    return True


# --------------------------------------------------------------------------
# Git status check
# --------------------------------------------------------------------------

def is_git_clean(repo_path: str = ".") -> tuple[bool, str]:
    """Return (is_clean, output). Checks git status --porcelain."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return False, "git not found in PATH"
    except subprocess.TimeoutExpired:
        return False, "git status timed out"

    if result.returncode != 0:
        return False, f"git status failed: {result.stderr.strip()}"

    lines = [l for l in result.stdout.strip().splitlines() if l]
    if lines:
        return False, f"Git status not clean:\n" + "\n".join(lines[:10])
    return True, "Git status is clean"


# ---------------------------------------------------------------------------
# PMG guard state validation
# ---------------------------------------------------------------------------

def load_pmg_guard_state(path: str) -> tuple[bool, Optional[dict], str]:
    """
    Load and validate a PMG guard state JSON.
    Returns (is_valid, data, error_message).
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return False, None, f"PMG guard state file not found: {path}"
    except json.JSONDecodeError as e:
        return False, None, f"Invalid JSON in PMG guard state: {e}"

    if not isinstance(data, dict):
        return False, None, "PMG guard state must be a JSON object"

    status = data.get("status")
    if status == "clean":
        return True, data, "PMG guard state is clean"
    elif status == "blocked":
        return False, data, f"PMG guard state is blocked: {data.get('message', 'unknown')}"
    elif status == "error":
        return False, data, f"PMG guard state error: {data.get('message', 'unknown')}"
    elif status is None:
        return False, None, "PMG guard state has no 'status' field"
    else:
        return False, data, f"PMG guard state status is '{status}' (expected 'clean')"


def load_review_comments_state(path: str) -> tuple[bool, Optional[dict], str]:
    """
    Load and validate a check_pr_review_comments.py output JSON.

    Returns (is_valid, data, error_message).
    A valid file with status REVIEW_COMMENTS_CLEAN is treated as clean.
    REVIEW_COMMENTS_BLOCKED or REVIEW_COMMENTS_INCONCLUSIVE is treated as not-clean.
    A missing or invalid file causes a HOLD.
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return False, None, f"Review-comments JSON not found: {path}"
    except json.JSONDecodeError as e:
        return False, None, f"Invalid JSON in review-comments file: {e}"

    if not isinstance(data, dict):
        return False, None, "Review-comments file must be a JSON object"

    status = data.get("status")
    if status == "REVIEW_COMMENTS_CLEAN":
        return True, data, "Review-comments gate is clean"
    elif status == "REVIEW_COMMENTS_BLOCKED":
        blockers = data.get("blockers", [])
        if not isinstance(blockers, list):
            return False, data, (
                "Review-comments file 'blockers' field is not a list "
                f"(got {type(blockers).__name__})"
            )
        blocker_summary = "; ".join(
            f"{b.get('severity', '?')} on {b.get('file_path', '?') or 'PR'}"
            for b in blockers[:3]
        ) or "unknown"
        return False, data, f"Review-comments gate is BLOCKED: {blocker_summary}"
    elif status == "REVIEW_COMMENTS_INCONCLUSIVE":
        return False, data, (
            "Review-comments gate is INCONCLUSIVE "
            "(review-comment-gate returned INCONCLUSIVE — stale findings or API errors)"
        )
    elif status is None:
        return False, None, "Review-comments file has no 'status' field"
    else:
        return False, data, f"Review-comments file status is '{status}' (expected CLEAN/BLOCKED/INCONCLUSIVE)"


# ---------------------------------------------------------------------------
# Build output report
# ---------------------------------------------------------------------------

def build_authorization_phrase(pr_number: int, head_sha: str) -> str:
    """Build the standard AED authorization phrase."""
    return f"merge PR #{pr_number} at {head_sha}"


def build_merge_command(pr_number: int, repo: str, head_sha: str) -> str:
    """Build the safe merge command with --match-head-commit."""
    return (
        f"gh pr merge {pr_number} --squash --delete-branch "
        f"--match-head-commit {head_sha}"
    )


def compute_result(
    state: str,
    pr_number: int,
    head_sha: str,
    checks: dict,
    blockers: list[str],
    repo: str,
    pmg_data: Optional[dict],
) -> dict:
    """Compute authorization phrase and merge command based on state."""
    if state == State.READY_TO_MERGE:
        auth = build_authorization_phrase(pr_number, head_sha)
        merge_cmd = build_merge_command(pr_number, repo, head_sha)
        next_action = "merge"
    else:
        auth = ""
        merge_cmd = ""
        # Derive next_action from first blocker
        if blockers:
            first = blockers[0].lower()
            if "pmg" in first:
                next_action = "fix PMG blocker and rerun final gate status"
            elif "ci" in first or "check" in first:
                next_action = "wait for CI to become green"
            elif "codex" in first:
                if "stale" in first:
                    next_action = "run Codex exact-head review of current head"
                else:
                    next_action = "supply --codex-reviewed-sha and rerun"
            elif "head" in first or "sha" in first:
                next_action = "update --reported-head-sha to current PR head"
            elif "not open" in first:
                next_action = "PR is closed/merged, manual review required"
            elif "git" in first:
                next_action = "clean git status and rerun"
            elif "review-comment" in first:
                next_action = "run local check_pr_review_comments.py and address findings, then rerun"
            else:
                next_action = "resolve blocker and rerun final gate status"
        else:
            next_action = "resolve unknown blocker"

    result = {
        "status": state,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "repo": repo,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "blockers": blockers,
        "authorization_phrase": auth,
        "merge_command": merge_cmd,
        "next_action": next_action,
    }

    if pmg_data is not None:
        result["pmg_guard_state"] = {
            "status": pmg_data.get("status"),
            "files_added": pmg_data.get("files_added", []),
            "files_removed": pmg_data.get("files_removed", []),
            "files_modified": pmg_data.get("files_modified", []),
        }

    return result


def write_json(data: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def write_md(data: dict, path: str) -> None:
    state = data["status"]
    lines = [
        f"# AED Final Gate Status — PR #{data['pr_number']}",
        f"**Status:** `{state}`",
        f"**Head SHA:** `{data['head_sha']}`",
        f"**Repo:** `{data['repo']}`",
        f"**Generated:** `{data['generated_at']}`",
        "",
        "## Checks",
    ]
    checks = data["checks"]
    for key, val in checks.items():
        icon = "✅" if val else "❌"
        lines.append(f"- {icon} {key}: {val}")

    if data["blockers"]:
        lines.append("")
        lines.append("## Blockers")
        for b in data["blockers"]:
            lines.append(f"- ❌ {b}")
    else:
        lines.append("")
        lines.append("## Blockers")
        lines.append("- (none)")

    if state == "READY_TO_MERGE":
        lines.extend([
            "",
            "## Authorization",
            f"**Phrase:** `{data['authorization_phrase']}`",
            f"**Merge command:** `{data['merge_command']}`",
            "",
            f"**Next action:** `{data['next_action']}`",
        ])
    else:
        lines.extend([
            "",
            "## Authorization",
            "*Authorization phrase and merge command withheld — PR is not READY_TO_MERGE*",
            "",
            f"**Next action:** `{data['next_action']}`",
        ])

    with open(path, "w") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Main gate evaluation
# ---------------------------------------------------------------------------

def evaluate(args: argparse.Namespace) -> dict:
    """
    Run all pre-merge gate checks in sequence.
    Returns a result dict (does not write files — caller handles that).
    """
    repo = args.repo
    pr_number = args.pr_number

    # --- Fetch PR state ---
    try:
        pr_data = fetch_pr_state(pr_number, repo)
    except RuntimeError as e:
        return _fatal_result(pr_number, repo, str(e))

    pr_state = pr_data.get("state", "unknown").lower()
    canonical_head_sha = pr_data.get("headRefOid", "")
    mergeable = pr_data.get("mergeable")
    pr_title = pr_data.get("title", "")
    pr_url = pr_data.get("url", "")

    # --- Check 1: PR must be open ---
    if pr_state != "open":
        blockers = [f"PR state is '{pr_state}', expected 'open'"]
        return compute_result(
            State.HOLD_PR_NOT_OPEN, pr_number, canonical_head_sha,
            {"pr_open": False, "head_matches": True, "ci_green": True,
             "codex_exact_head": True, "pmg_clean": True, "git_status_clean": True},
            blockers, repo, None,
        )

    # --- Check 2: Head SHA match ---
    reported = args.reported_head_sha
    head_matches = True
    if reported and reported != canonical_head_sha:
        blockers = [
            f"Head SHA mismatch: reported {reported[:8]}... but canonical is {canonical_head_sha[:8]}..."
        ]
        return compute_result(
            State.HOLD_HEAD_MISMATCH, pr_number, canonical_head_sha,
            {"pr_open": True, "head_matches": False, "ci_green": True,
             "codex_exact_head": True, "pmg_clean": True, "git_status_clean": True},
            blockers, repo, None,
        )

    # --- Check 3 (moved before CI): Fresh local review-comment gate ---
    # When --review-comments-json is provided, check it BEFORE is_ci_green.
    # This ensures a fresh local BLOCKED/INCONCLUSIVE result takes precedence
    # over stale CI state, closing the structural gap identified in PR #326.
    rc_path = getattr(args, "review_comments_json", None)
    if rc_path:
        rc_valid, rc_data, rc_reason = load_review_comments_state(rc_path)
        if not rc_valid:
            blockers = [f"Review-comments gate: {rc_reason}"]
            rc_blocked = (
                State.HOLD_REVIEW_COMMENTS_BLOCKED
                if "BLOCKED" in rc_reason
                else State.HOLD_REVIEW_COMMENTS_INCONCLUSIVE
            )
            return compute_result(
                rc_blocked, pr_number, canonical_head_sha,
                {"pr_open": True, "head_matches": True, "ci_green": True,
                 "codex_exact_head": True, "pmg_clean": True, "git_status_clean": True},
                blockers, repo, None,
            )

    # --- Check 4: CI must be green ---
    ci_green, ci_reason = is_ci_green(pr_number, repo, canonical_head_sha)
    if not ci_green:
        blockers = [f"CI is not green: {ci_reason}"]
        return compute_result(
            State.HOLD_CI_RED, pr_number, canonical_head_sha,
            {"pr_open": True, "head_matches": True, "ci_green": False,
             "codex_exact_head": True, "pmg_clean": True, "git_status_clean": True},
            blockers, repo, None,
        )

    # --- Check 5: Codex exact-head review ---
    # Docs-only waiver: if --allow-docs-only-codex-waiver is set AND the PR
    # diff is docs-only, skip the Codex SHA requirement.
    codex_sha = args.codex_reviewed_sha
    docs_waiver_used = False
    if not codex_sha and args.allow_docs_only_codex_waiver:
        # Fetch changed files for this PR to determine if it is docs-only.
        try:
            changed_files = get_pr_changed_files(pr_number, repo, canonical_head_sha)
            if is_docs_only(changed_files):
                docs_waiver_used = True
                # Set codex_sha equal to head so the existing SHA-match logic
                # at the bottom of this block passes.  This lets the flow
                # continue to the PMG and git-status checks rather than
                # returning HOLD_CODEX_REQUIRED here.
                codex_sha = canonical_head_sha
        except Exception:
            # If we cannot determine changed files, do NOT waive
            codex_sha = None  # fall through to normal HOLD_CODEX_REQUIRED

    if not codex_sha:
        blockers = ["No --codex-reviewed-sha supplied; Codex exact-head review cannot be verified"]
        return compute_result(
            State.HOLD_CODEX_REQUIRED, pr_number, canonical_head_sha,
            {"pr_open": True, "head_matches": True, "ci_green": True,
             "codex_exact_head": False, "pmg_clean": True, "git_status_clean": True},
            blockers, repo, None,
        )
    if codex_sha != canonical_head_sha:
        blockers = [
            f"Codex reviewed SHA {codex_sha[:8]}... but PR head has moved to {canonical_head_sha[:8]}... — review is stale"
        ]
        return compute_result(
            State.HOLD_CODEX_STALE, pr_number, canonical_head_sha,
            {"pr_open": True, "head_matches": True, "ci_green": True,
             "codex_exact_head": False, "pmg_clean": True, "git_status_clean": True},
            blockers, repo, None,
        )

    # --- Check 5: PMG guard state must be clean ---
    pmg_path = args.pmg_guard_state_json
    if not pmg_path:
        blockers = ["No --pmg-guard-state-json supplied; PMG guard state is required"]
        return compute_result(
            State.HOLD_PMG_MISSING, pr_number, canonical_head_sha,
            {"pr_open": True, "head_matches": True, "ci_green": True,
             "codex_exact_head": True, "pmg_clean": False, "git_status_clean": True},
            blockers, repo, None,
        )
    pmg_valid, pmg_data, pmg_reason = load_pmg_guard_state(pmg_path)
    if not pmg_valid:
        blockers = [f"PMG guard state invalid: {pmg_reason}"]
        return compute_result(
            State.HOLD_PMG_DIRTY, pr_number, canonical_head_sha,
            {"pr_open": True, "head_matches": True, "ci_green": True,
             "codex_exact_head": True, "pmg_clean": False, "git_status_clean": True},
            blockers, repo, pmg_data,
        )

    # --- Check 7: Git status must be clean ---
    git_clean, git_reason = is_git_clean()
    if not git_clean:
        blockers = [f"Git status is not clean: {git_reason.split(chr(10))[0]}"]
        return compute_result(
            State.HOLD_GIT_DIRTY, pr_number, canonical_head_sha,
            {"pr_open": True, "head_matches": True, "ci_green": True,
             "codex_exact_head": True, "pmg_clean": True, "git_status_clean": False},
            blockers, repo, pmg_data,
        )

    # --- All checks pass ---
    checks = {
        "pr_open": True,
        "head_matches": True,
        "ci_green": True,
        "codex_exact_head": not docs_waiver_used,
        "pmg_clean": True,
        "git_status_clean": True,
    }
    if docs_waiver_used:
        checks["codex_review_waived_for_docs_only"] = True
    blockers = []
    return compute_result(
        State.READY_TO_MERGE, pr_number, canonical_head_sha,
        checks, blockers, repo, pmg_data,
    )


def _fatal_result(pr_number: int, repo: str, error: str) -> dict:
    """Return a fatal-error result."""
    return {
        "status": State.HOLD_UNKNOWN,
        "pr_number": pr_number,
        "repo": repo,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "head_sha": "",
        "checks": {},
        "blockers": [f"Fatal error: {error}"],
        "authorization_phrase": "",
        "merge_command": "",
        "next_action": "resolve fatal error and rerun",
    }


def print_result(data: dict) -> None:
    """Print human-readable status to stdout."""
    state = data["status"]
    pr = data["pr_number"]
    head = data["head_sha"]
    checks = data.get("checks", {})

    print(f"=== AED Final Gate Status — PR #{pr} ===")
    print(f"Head SHA:   {head}")
    print(f"Status:     {state}")
    if data["blockers"]:
        print("Blockers:")
        for b in data["blockers"]:
            print(f"  - {b}")
    print("Checks:")
    for key, val in checks.items():
        icon = "✅" if val else "❌"
        print(f"  {icon} {key}: {val}")
    if state == "READY_TO_MERGE":
        print(f"\nAuthorization phrase: {data['authorization_phrase']}")
        print(f"Merge command:         {data['merge_command']}")
    print(f"\nNext action: {data['next_action']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    try:
        result = evaluate(args)
    except Exception as e:
        result = _fatal_result(args.pr_number, args.repo, str(e))

    # Write outputs
    if args.output_json:
        write_json(result, args.output_json)
        print(f"JSON written to {args.output_json}", file=sys.stderr)
    if args.output_md:
        write_md(result, args.output_md)
        print(f"Markdown written to {args.output_md}", file=sys.stderr)

    # Always print to stdout
    print_result(result)

    # Exit code 0 for any result state (output already written)
    return 0


if __name__ == "__main__":
    sys.exit(main())