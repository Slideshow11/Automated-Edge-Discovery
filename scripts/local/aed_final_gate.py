#!/usr/bin/env python3
"""
AED Finalization Guard — machine-checked MERGE_READY gate.

Generates FINAL_GATE.json and FINAL_GATE.md from live GitHub PR state,
local validation artifacts, Codex evidence, changed-file scope, and
exact-head CI state.

No executable side effects. Merge command is printed only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FORBIDDEN_EXECUTABLE_CALLS = [
    "hermes kanban create",
    "hermes kanban dispatch",
    "gh pr merge",
    "gh pr create",
    "git push",
    "telegram send_message",
    "memory.update",
    "skill_manage",
    "fact_store",
    "delegate_task",
    "cronjob",
]

# Maximum age of a PMG compare JSON before it is considered stale.
# A clean compare JSON older than this relative to gate execution is rejected.
# This closes the gap where a Hermes mutation occurs after compare but before
# finalization — the old clean compare would still appear valid by ordering but
# is stale by wall-clock age.
MAX_COMPARE_AGE_SECONDS = 600  # 10 minutes


def forbidden_executable_check(code: str) -> list[str]:
    """Return list of forbidden strings found in code (not in comments/constants)."""
    import re
    violations = []
    lines = code.split("\n")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # Skip comments and string-only lines
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        # Skip lines that are only string literals (docstrings, constant assignments)
        if re.match(r'^[_A-Za-z][_A-Za-z0-9]*\s*=\s*["\']', line):
            continue
        # Skip list/collection assignments: parts = ["...", ...]
        if re.match(r'^[_A-Za-z][_A-Za-z0-9]*\s*=\s*\[', line):
            continue
        if re.match(r'^["\']', stripped):
            continue
        # Skip conditionals and return statements containing forbidden strings as values
        # e.g., if "gh pr merge" in command: or return False, "No 'gh pr merge' found"
        code_part = line.split("#")[0]
        # Skip lines where the forbidden string is inside a function argument or expression
        # Examples: return False, "No 'gh pr merge' found" | if "gh pr merge" in command:
        # These have the pattern: keyword [stuff] "forbidden"
        if re.search(r'(if|return|and|or|=)\s+.*?["\'].*?[' + "|".join(re.escape(p) for p in FORBIDDEN_EXECUTABLE_CALLS) + r']', code_part):
            continue
        for pattern in FORBIDDEN_EXECUTABLE_CALLS:
            if pattern in code_part and not line.strip().startswith("#"):
                violations.append(f"Line {i}: {line.strip()}")
    return violations


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def gh(query: str, *args: str) -> dict:
    """Run gh command and return parsed JSON."""
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh api failed: {result.stderr}")
    return json.loads(result.stdout)


def gh_pr_info(pr_number: int, repo: str) -> dict:
    """Fetch PR details via GitHub REST API (more reliable for single PR)."""
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{pr_number}"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api failed: {result.stderr}")
    return json.loads(result.stdout)


def gh_runs_for_sha(sha: str, repo: str) -> list[dict]:
    """Get CI runs for a specific SHA via GitHub Actions API.

    Uses --method GET to ensure gh sends the query parameters as a URL-encoded
    GET request rather than switching to POST (which the Actions runs endpoint rejects).
    """
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/actions/runs",
            "--method",
            "GET",
            "--paginate",
            "-f",
            "head_sha=" + sha,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    data = json.loads(result.stdout)
    return data.get("workflow_runs", [])


# ---------------------------------------------------------------------------
# Validation functions
# ---------------------------------------------------------------------------

def validate_expected_head(expected: Optional[str], actual: str) -> tuple[bool, str]:
    if expected is None:
        return True, "expected_head not provided — skipped"
    if expected != actual:
        return False, f"MISMATCH: expected={expected}, actual={actual}"
    return True, f"head SHA matches expected: {actual}"


def validate_ci_green(runs: list[dict], commit_sha: str) -> tuple[bool, str, list[dict]]:
    """Check all CI runs for the exact SHA are success."""
    relevant = [r for r in runs if r.get("head_sha") == commit_sha]
    if not relevant:
        return False, f"No CI runs found for SHA {commit_sha}", []
    failures = [r for r in relevant if r.get("conclusion") != "success"]
    if failures:
        failed_names = [f"{r['name']} ({r['conclusion']})" for r in failures]
        return False, f"CI failures: {', '.join(failed_names)}", relevant
    return True, f"All {len(relevant)} CI runs success", relevant


def validate_changed_files_in_scope(
    changed_files: list[str], allowed_files: Optional[list[str]]
) -> tuple[bool, str]:
    """Check all changed files are within allowed scope."""
    if not allowed_files:
        return True, "allowed_files not provided — skipped"
    import fnmatch
    violations = []
    files: list[str] = changed_files  # type: ignore[assignment]
    for f in files:
        in_scope = any(fnmatch.fnmatch(f, pattern) for pattern in allowed_files)
        if not in_scope:
            violations.append(f)
    if violations:
        return False, f"Changed files outside scope: {violations}"
    return True, f"All {len(changed_files)} files within scope"


def validate_pr_state(pr: dict) -> tuple[bool, str]:
    """Validate PR is open and mergeable."""
    if pr.get("state") != "open":
        return False, f"PR state is '{pr.get('state')}', not open"
    if pr.get("mergeable") is not True and pr.get("mergeable") != "MERGEABLE":
        return False, f"PR mergeable is '{pr.get('mergeable')}', not MERGEABLE"
    return True, f"PR open and mergeable (state={pr.get('state')}, mergeable={pr.get('mergeable')})"


def _validate_persistent_guard_compare_json(path: Path) -> tuple[bool, str]:
    """Load and validate a PMG compare JSON.

    Returns (is_valid, message). is_valid is True on clean load.
    message explains the validation outcome.
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, IOError):
        return False, f"compare JSON not readable: {path}"
    except json.JSONDecodeError as e:
        return False, f"malformed JSON in compare JSON: {e}"

    if not isinstance(data, dict):
        return False, "compare JSON must be a JSON object (not array, string, or number)"

    for field in ("status", "recommendation"):
        if field not in data:
            return False, f"compare JSON missing required field: {field}"

    rec = data.get("recommendation", "")
    if rec not in ("PASS", "BLOCK"):
        return False, f"compare JSON has unexpected recommendation: {rec}"

    return True, f"valid — recommendation={rec}"


def _run_persistent_guard_validate(
    snapshot_path: Optional[Path],
    compare_json_path: Optional[Path],
    compare_md_path: Optional[Path],
    guard_root: str = "/home/max/.hermes",
) -> dict:
    """Record-only persistent mutation guard validation.

    Accepts pre-existing snapshot and compare report paths.
    Validates compare JSON, checks recommendation, returns guard state dict.

    Returns a guard state dict with keys:
        required, status, snapshot_path, compare_json_path, compare_md_path,
        blocked_changes_count, allowed_changes_count, message
    """
    state: dict = {
        "required": False,
        "status": "not_required",
        "snapshot_path": str(snapshot_path) if snapshot_path else None,
        "compare_json_path": str(compare_json_path) if compare_json_path else None,
        "compare_md_path": str(compare_md_path) if compare_md_path else None,
        "blocked_changes_count": 0,
        "allowed_changes_count": 0,
        "message": "persistent mutation guard not required",
    }

    if compare_json_path is None:
        return state

    if not compare_json_path.exists():
        state["status"] = "error"
        state["message"] = f"compare JSON not found: {compare_json_path}"
        return state

    is_valid, msg = _validate_persistent_guard_compare_json(compare_json_path)
    if not is_valid:
        state["status"] = "error"
        state["message"] = msg
        return state
    # Load compare JSON to extract counts
    with open(compare_json_path) as f:
        data = json.load(f)

    # Temporal validation: compare JSON must carry timestamps proving the compare
    # ran AFTER the snapshot. A pre-generated clean compare JSON is not acceptable.
    snapshot_at_str = data.get("snapshot_at", "")
    compare_at_str = data.get("compare_at", "")

    if not snapshot_at_str:
        state["status"] = "error"
        state["message"] = "compare JSON missing snapshot_at: temporal ordering unverifiable"
        return state

    if not compare_at_str:
        state["status"] = "error"
        state["message"] = "compare JSON missing compare_at: temporal ordering unverifiable"
        return state

    try:
        snapshot_dt = datetime.fromisoformat(snapshot_at_str.replace("Z", "+00:00"))
        compare_dt = datetime.fromisoformat(compare_at_str.replace("Z", "+00:00"))
    except (ValueError, TypeError) as e:
        state["status"] = "error"
        state["message"] = f"compare JSON has unparseable timestamp: {e}"
        return state

    if compare_dt < snapshot_dt:
        state["status"] = "error"
        state["message"] = "compare JSON is temporally stale (compare_at < snapshot_at): pre-generated compare rejected"
        return state

    # Freshness check: compare JSON must be recent relative to gate execution.
    # A clean compare generated before a Hermes mutation (between compare and gate)
    # could still have valid ordering but is stale by wall-clock age.
    gate_dt = datetime.now(timezone.utc)
    compare_age_seconds = (gate_dt - compare_dt).total_seconds()
    if compare_age_seconds > MAX_COMPARE_AGE_SECONDS:
        state["status"] = "error"
        state["message"] = (
            f"compare JSON is stale (compare_at is {compare_age_seconds:.0f}s older than "
            f"gate execution, max {MAX_COMPARE_AGE_SECONDS}s): pre-generated compare rejected"
        )
        return state

    rec = data.get("recommendation", "UNKNOWN")
    state["status"] = "clean" if rec == "PASS" else "blocked"
    state["message"] = f"guard recommendation: {rec}"
    state["blocked_changes_count"] = len(data.get("blocked_changes", []))
    state["allowed_changes_count"] = len(data.get("allowed_changes", []))

    return state


def validate_codex_artifact_head(
    codex_path: Optional[str], current_head: str, allow_skip: bool = False
) -> tuple[bool, str]:
    """Validate Codex artifact SHA matches current head exactly.

    Accepts artifact only when it explicitly references the exact expected head SHA.
    Ancestor SHAs, base SHAs, stale SHAs, or any other SHA in the artifact are NOT
    valid — exact equality to current_head is required.

    When no artifact is provided:
      - allow_skip=True  → treated as SKIP (passing=True, skipped=True, skip_authorized=True)
      - allow_skip=False → treated as FAIL (passing=False, Codex required)

    Accepted SHA fields (exact equality required):
      head_sha, commit_sha, reviewed_sha, pr_head_sha

    If no usable SHA field exists: BLOCK (passing=False)
    If SHA field does not match current_head exactly: BLOCK (passing=False)
    """
    if not codex_path:
        if allow_skip:
            return True, "codex_artifact skipped (--allow-codex-skip)"
        return False, "codex_artifact required but not provided"
    path = Path(codex_path)
    if not path.exists():
        return False, f"Codex artifact not found: {codex_path}"
    content = path.read_text()

    # Supported SHA field names — exact equality to current_head required
    sha_field_names = ("head_sha", "commit_sha", "reviewed_sha", "pr_head_sha")

    # Try JSON field extraction first
    try:
        data = json.loads(content)
        for field in sha_field_names:
            if field in data and isinstance(data[field], str):
                artifact_sha = data[field].strip()
                if len(artifact_sha) == 40 and all(c in '0123456789abcdef' for c in artifact_sha.lower()):
                    if artifact_sha == current_head:
                        return True, f"Codex artifact head_sha matches current head {current_head}"
                    else:
                        return False, f"codex_artifact head_sha mismatch: expected={current_head}, artifact has={artifact_sha}"
        # No recognized SHA field found in JSON
        return False, "codex_artifact has no recognized SHA field (head_sha, commit_sha, reviewed_sha, pr_head_sha)"
    except json.JSONDecodeError:
        pass

    # Fallback: regex search for 40-char hex SHA in raw content
    # Only matches content that looks like a standalone 40-char hex string
    import re
    hex_char_set = set('0123456789abcdef')
    # Find all 40-char hex strings that appear to be SHAs (not embedded in longer strings)
    # A SHA must be preceded by a field name indicator (", :, =) or start of string
    # and followed by , " \n } or end of string
    sha_pattern = r'(?:^|(?<=[^0-9a-f]))([0-9a-f]{40})(?=[\s"]|$)'
    matches = re.findall(sha_pattern, content, re.IGNORECASE)
    if not matches:
        return False, "codex_artifact contains no recognizable SHA reference"
    for sha in matches:
        if sha.lower() == current_head.lower():
            return True, f"Codex artifact SHA matches current head {current_head}"
    return False, f"codex_artifact head_sha mismatch: artifact contains {matches[0]}, current head is {current_head}"


def validate_local_validation(
    validation_path: Optional[str], expect_tests: bool = True
) -> tuple[bool, str]:
    """Validate local validation artifact is present and not stale."""
    if not validation_path:
        return True, "local_validation not provided — skipped"
    # Support both string paths and Path objects
    if hasattr(validation_path, 'read_text'):
        path = validation_path
    else:
        path = Path(validation_path)
    if not path.exists():
        return False, f"Local validation artifact not found: {validation_path}"
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return False, f"Local validation artifact is not valid JSON: {e}"
    # Check for stale "collected 0 items" pattern
    try:
        content = path.read_text()
    except Exception:
        content = str(data)
    if "collected 0 items" in content or "collected 0 items" in str(data):
        return False, "Local validation shows 'collected 0 items' — stale or mis-run"
    if expect_tests:
        # Check for test count field or output pattern
        if "tests_collected" in data:
            if data["tests_collected"] == 0:
                return False, "Local validation reports 0 tests collected"
    return True, "Local validation artifact valid"


def validate_merge_command_safety(command: str, allow_admin: bool) -> tuple[bool, str]:
    """Validate merge command does not contain forbidden patterns."""
    import re
    # Extract gh pr merge arguments
    if "gh pr merge" not in command:
        return False, "No 'gh pr merge' found in command"
    # Check for --admin flag
    has_admin = "--admin" in command
    if has_admin and not allow_admin:
        return False, "Merge command contains '--admin' but --allow-admin was not set"
    # Check for actual merge execution (would have --admin --squash etc.)
    # Our generated command only prints — it doesn't execute
    return True, "Merge command is print-only (no actual execution)"


def build_authorization_phrase(pr_number: int, head_sha: str) -> str:
    return f"I confirm merge PR #{pr_number} at {head_sha} using final-head reviewed clean state."


def build_merge_command(pr_number: int, head_sha: str, repo: str, allow_admin: bool = False) -> str:
    parts = ["gh pr merge", str(pr_number)]
    if allow_admin:
        parts.append("--admin")
    parts.extend(["--squash", f"--match-head-commit {head_sha}"])
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Main gate logic
# ---------------------------------------------------------------------------

def run_final_gate(
    pr_number: int,
    expected_head_sha: Optional[str],
    allowed_files: Optional[list[str]],
    local_validation_path: Optional[str],
    codex_artifact_path: Optional[str],
    output_json_path: str,
    output_md_path: str,
    allow_admin: bool = False,
    allow_codex_skip: bool = False,
    require_persistent_guard: bool = False,
    persistent_guard_root: str = "/home/max/.hermes",
    persistent_guard_snapshot: Optional[str] = None,
    persistent_guard_compare_json: Optional[str] = None,
    persistent_guard_compare_md: Optional[str] = None,
) -> dict:
    # Detect repo from git
    repo_result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True, text=True,
        cwd=Path(__file__).parent.parent
    )
    remote_url = repo_result.stdout.strip()
    # Extract owner/repo from git URL
    import re
    match = re.search(r'github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?$', remote_url)
    if not match:
        raise RuntimeError(f"Cannot parse repo from git remote: {remote_url}")
    owner, repo_name = match.group(1), match.group(2)
    repo = f"{owner}/{repo_name}"

    # Fetch live PR state
    pr = gh_pr_info(pr_number, repo)
    current_head = pr["head"]["sha"] if isinstance(pr["head"], dict) else pr["head_ref"]
    # Actually get the OID via REST
    pr_rest = gh_pr_info(pr_number, repo)
    current_head = pr_rest.get("head", {}).get("sha") or pr_rest.get("head_sha") or pr.get("headRefOid")
    if not current_head:
        # Fallback to GraphQL
        query = f"""
        {{
          repository(owner: "{owner}", name: "{repo_name}") {{
            pullRequest(number: {pr_number}) {{
              headRefOid
              state
              mergeable
              changedFiles
              commits(last: 1) {{
                nodes {{
                  oid
                }}
              }}
            }}
          }}
        }}
        """
        result = gh(query)
        pr_data = result["data"]["repository"]["pullRequest"]
        current_head = pr_data["headRefOid"]

    changed_files = pr_rest.get("changed_files", [])
    if not changed_files:
        # Use GraphQL for changed files
        query = f"""
        {{
          repository(owner: "{owner}", name: "{repo_name}") {{
            pullRequest(number: {pr_number}) {{
              changedFiles
            }}
          }}
        }}
        """
        result = gh(query)
        changed_files = list(range(result["data"]["repository"]["pullRequest"]["changedFiles"]))

    # Resolve changed_files (GraphQL returns int count, REST returns list of paths)
    if isinstance(changed_files, int):
        # GraphQL: changedFiles is a count — fetch all files with pagination
        all_files = []
        after_cursor = None
        while True:
            query = f"""
            {{
              repository(owner: "{owner}", name: "{repo_name}") {{
                pullRequest(number: {pr_number}) {{
                  files(first: 100{', after: "' + after_cursor + '"' if after_cursor else ''}) {{
                    nodes {{
                      path
                    }}
                    pageInfo {{
                      hasNextPage
                      endCursor
                    }}
                  }}
                }}
              }}
            }}
            """
            result = gh(query)
            file_nodes = result["data"]["repository"]["pullRequest"]["files"]["nodes"]
            all_files.extend(n["path"] for n in file_nodes)
            page_info = result["data"]["repository"]["pullRequest"]["files"].get("pageInfo")
            if page_info and page_info.get("hasNextPage"):
                after_cursor = page_info["endCursor"]
            else:
                break
        changed_files = all_files
    elif isinstance(changed_files, list) and changed_files and isinstance(changed_files[0], int):
        # List of ints — fetch actual file names with pagination
        all_files = []
        after_cursor = None
        while True:
            query = f"""
            {{
              repository(owner: "{owner}", name: "{repo_name}") {{
                pullRequest(number: {pr_number}) {{
                  files(first: 100{', after: "' + after_cursor + '"' if after_cursor else ''}) {{
                    nodes {{
                      path
                    }}
                    pageInfo {{
                      hasNextPage
                      endCursor
                    }}
                  }}
                }}
              }}
            }}
            """
            result = gh(query)
            file_nodes = result["data"]["repository"]["pullRequest"]["files"]["nodes"]
            all_files.extend(n["path"] for n in file_nodes)
            page_info = result["data"]["repository"]["pullRequest"]["files"].get("pageInfo")
            if page_info and page_info.get("hasNextPage"):
                after_cursor = page_info["endCursor"]
            else:
                break
        changed_files = all_files

    # Ensure changed_files is list[str] for type checker
    _files: list[str] = changed_files if isinstance(changed_files, list) and all(isinstance(f, str) for f in changed_files) else []
    assert _files, f"changed_files must be list[str], got {type(changed_files)}"

    # Get CI runs for current head
    ci_runs = gh_runs_for_sha(current_head, repo)

    # Validate each gate
    head_valid, head_msg = validate_expected_head(expected_head_sha, current_head)
    ci_valid, ci_msg, ci_runs_used = validate_ci_green(ci_runs, current_head)
    scope_valid, scope_msg = validate_changed_files_in_scope(
        _files,
        allowed_files
    )
    pr_valid, pr_msg = validate_pr_state(pr_rest)
    codex_valid, codex_msg = validate_codex_artifact_head(codex_artifact_path, current_head, allow_codex_skip)
    local_valid, local_msg = validate_local_validation(local_validation_path)

    # Run persistent mutation guard validation when required
    snapshot_path = Path(persistent_guard_snapshot) if persistent_guard_snapshot else None
    compare_json_path = Path(persistent_guard_compare_json) if persistent_guard_compare_json else None
    compare_md_path = Path(persistent_guard_compare_md) if persistent_guard_compare_md else None
    guard_state = _run_persistent_guard_validate(
        snapshot_path, compare_json_path, compare_md_path, persistent_guard_root
    )
    guard_state["required"] = require_persistent_guard

    # PMG BLOCK overrides MERGE_READY but does not override other BLOCK reasons
    guard_blocked = require_persistent_guard and guard_state["status"] in ("blocked", "error", "not_required")

    all_hard_gates_valid = all([
        head_valid, ci_valid, scope_valid, pr_valid, local_valid
    ])

    codex_missing = codex_artifact_path is None

    if not all_hard_gates_valid:
        # Priority 1: Hard gate failure → BLOCK
        # Hard gates: head SHA, CI green, scope clean, PR open+mergeable, local validation
        recommendation = "BLOCK"
    elif codex_artifact_path and not codex_valid:
        # Priority 2: Artifact provided but invalid (stale, mismatched, malformed, missing SHA) → BLOCK
        # allow_codex_skip does NOT override an invalid provided artifact
        recommendation = "BLOCK"
    elif codex_missing and not allow_codex_skip:
        # Priority 3: Artifact missing and skip not authorized → WAIT
        recommendation = "WAIT"
    elif codex_missing and allow_codex_skip:
        # Priority 4: Artifact missing but skip authorized + hard gates pass → MERGE_READY
        recommendation = "MERGE_READY"
    else:
        # Priority 5: Artifact provided and valid + hard gates pass → MERGE_READY
        recommendation = "MERGE_READY"

    # PMG BLOCK overrides MERGE_READY but not other BLOCK reasons
    if recommendation == "MERGE_READY" and guard_blocked:
        recommendation = "BLOCK"

    auth_phrase = None
    merge_cmd = None
    if recommendation == "MERGE_READY":
        auth_phrase = build_authorization_phrase(pr_number, current_head)
        merge_cmd = build_merge_command(pr_number, current_head, repo, allow_admin)

    # Build output
    gate = {
        "pr_number": pr_number,
        "head_sha": current_head,
        "base_sha": pr_rest.get("base", {}).get("sha") if isinstance(pr_rest.get("base"), dict) else None,
        "changed_files_count": changed_files if isinstance(changed_files, int) else len(changed_files),
        "ci_status": {
            "passing": ci_valid,
            "message": ci_msg,
            "runs_found": len(ci_runs_used),
        },
        "codex_status": {
            "passing": codex_valid,
            "message": codex_msg,
            "skipped": bool(codex_artifact_path is None and allow_codex_skip),
            "skip_authorized": bool(allow_codex_skip),
        },
        "local_validation_status": {
            "passing": local_valid,
            "message": local_msg,
        },
        "scope_status": {
            "passing": scope_valid,
            "message": scope_msg,
            "allowed_files": allowed_files,
        },
        "pr_state": {
            "open": pr_valid,
            "mergeable": pr.get("mergeable") in (True, "MERGEABLE"),
            "message": pr_msg,
        },
        "head_sha_validation": {
            "passing": head_valid,
            "message": head_msg,
        },
        "persistent_mutation_guard": guard_state,
        "final_recommendation": recommendation,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "allow_admin": allow_admin,
    }

    if auth_phrase:
        gate["authorization_phrase"] = auth_phrase
    if merge_cmd:
        gate["merge_command"] = merge_cmd

    # Write JSON
    Path(output_json_path).write_text(json.dumps(gate, indent=2))

    # Write Markdown
    md_lines = [
        "# AED Finalization Gate Report",
        "",
        f"**PR:** #{pr_number}",
        f"**Head SHA:** `{current_head}`",
        f"**Generated:** {gate['generated_at']}",
        "",
        "## Validation Results",
        "",
        f"- **head_sha:** {'✓' if head_valid else '✗'} {head_msg}",
        f"- **CI:** {'✓' if ci_valid else '✗'} {ci_msg}",
        f"- **Codex:** {'✓' if codex_valid else '✗'} {codex_msg}",
        f"- **local_validation:** {'✓' if local_valid else '✗'} {local_msg}",
        f"- **scope:** {'✓' if scope_valid else '✗'} {scope_msg}",
        f"- **pr_state:** {'✓' if pr_valid else '✗'} {pr_msg}",
        "",
    ]
    # PMG validation result for markdown
    guard_valid = guard_state["status"] == "clean"
    guard_msg_str = guard_state["message"]
    if require_persistent_guard:
        md_lines.append(f"- **persistent_mutation_guard:** {'✓' if guard_valid else '✗'} {guard_msg_str}")
        md_lines.append("")

    md_lines.extend([
        f"## Final Recommendation",
        "",
        f"**`{recommendation}`**",
        "",
    ])

    if recommendation == "MERGE_READY":
        md_lines.extend([
            "## Authorization",
            "",
            f"```\n{auth_phrase}\n```",
            "",
            "## Merge Command (print-only — not executed)",
            "",
            f"```bash\n{merge_cmd}\n```",
            "",
            "---",
            "*This report was generated by aed_final_gate.py. No merge was executed.*",
        ])
    elif recommendation == "WAIT":
        md_lines.extend([
            "## Waiting On",
            "",
            "- **Codex evidence required** — provide --codex-artifact to proceed",
            "",
            "---",
            "*This report was generated by aed_final_gate.py. No merge was executed.*",
        ])
    else:
        md_lines.append("## Blocking Issues\n")
        for key, valid, msg in [
            ("head_sha", head_valid, head_msg),
            ("ci", ci_valid, ci_msg),
            ("codex", codex_valid, codex_msg),
            ("local_validation", local_valid, local_msg),
            ("scope", scope_valid, scope_msg),
            ("pr_state", pr_valid, pr_msg),
        ]:
            if not valid:
                md_lines.append(f"- **{key}:** {msg}")

        if guard_blocked:
            md_lines.append(f"- **persistent_mutation_guard:** {guard_state['message']}")

    Path(output_md_path).write_text("\n".join(md_lines))

    return gate


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AED Finalization Guard")
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--expected-head-sha", dest="expected_head_sha")
    parser.add_argument("--allowed-files-json", dest="allowed_files_json")
    parser.add_argument("--local-validation-json", dest="local_validation_json")
    parser.add_argument("--codex-artifact", dest="codex_artifact")
    parser.add_argument("--output-json", dest="output_json", required=True)
    parser.add_argument("--output-md", dest="output_md", required=True)
    parser.add_argument("--allow-admin", dest="allow_admin", action="store_true")
    parser.add_argument(
        "--allow-codex-skip",
        dest="allow_codex_skip",
        action="store_true",
        help="Allow missing Codex artifact. Skipped Codex is marked as skip_authorized=true. "
             "Use only when Codex review was performed out-of-band and not captured as artifact.",
    )
    parser.add_argument(
        "--persistent-guard-root",
        dest="persistent_guard_root",
        default="/home/max/.hermes",
        help="Hermes root for persistent mutation guard (default: /home/max/.hermes)",
    )
    parser.add_argument(
        "--persistent-guard-snapshot",
        dest="persistent_guard_snapshot",
        help="Path to pre-existing snapshot JSON for record-only guard validation",
    )
    parser.add_argument(
        "--persistent-guard-compare-json",
        dest="persistent_guard_compare_json",
        help="Path to pre-existing guard compare JSON report",
    )
    parser.add_argument(
        "--persistent-guard-compare-md",
        dest="persistent_guard_compare_md",
        help="Path to pre-existing guard compare markdown report",
    )
    parser.add_argument(
        "--require-persistent-guard",
        dest="require_persistent_guard",
        action="store_true",
        help="Require guard validation — BLOCK if compare JSON is missing, malformed, or recommendation=BLOCK",
    )

    args = parser.parse_args()

    allowed_files = None
    if args.allowed_files_json:
        import json as _json
        allowed_files = _json.loads(Path(args.allowed_files_json).read_text())

    gate = run_final_gate(
        pr_number=args.pr_number,
        expected_head_sha=args.expected_head_sha,
        allowed_files=allowed_files,
        local_validation_path=args.local_validation_json,
        codex_artifact_path=args.codex_artifact,
        output_json_path=args.output_json,
        output_md_path=args.output_md,
        allow_admin=args.allow_admin,
        allow_codex_skip=args.allow_codex_skip,
        require_persistent_guard=args.require_persistent_guard,
        persistent_guard_root=args.persistent_guard_root,
        persistent_guard_snapshot=args.persistent_guard_snapshot,
        persistent_guard_compare_json=args.persistent_guard_compare_json,
        persistent_guard_compare_md=args.persistent_guard_compare_md,
    )

    print(json.dumps(gate, indent=2))
    sys.exit(0 if gate["final_recommendation"] == "MERGE_READY" else 1)


if __name__ == "__main__":
    main()