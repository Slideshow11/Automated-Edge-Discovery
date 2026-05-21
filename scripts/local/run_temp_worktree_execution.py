#!/usr/bin/env python3
"""
run_temp_worktree_execution.py

Temp-worktree execution harness v0.

Given a validated execution packet with a human approval marker, creates a
disposable Git worktree, runs a mocked executor inside it, collects the diff,
validates it against the packet constraints, checks for external (Hermes)
mutations via PMG, and stops at PATCH_READY_FOR_HUMAN_REVIEW.

No real Claude execution. No git push. No PR creation. No merge. No dispatch.
No Hermes mutation. No board updates. No audit append. No memory/profile writes.

Usage:
    python3 scripts/local/run_temp_worktree_execution.py \
        --packet-json /tmp/packet.json \
        --output-json /tmp/result.json \
        --output-md /tmp/result.md

Exit codes:
    0 — evaluation complete (any state written to output JSON)
    1 — fatal error (missing args, invalid input, etc.)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.resolve()

PROTECTED_GATE_SCRIPTS = [
    "scripts/local/final_gate_status.py",
    "scripts/local/verify_final_head_merge_command.py",
    "scripts/local/check_persistent_mutation_guard.py",
    "scripts/local/plan_preview_eval_status.py",
]

WORKTREE_BASE = Path("/tmp/aed_runs/worktrees")

# Path to the PMG tool (check_persistent_mutation_guard.py)
PMG_TOOL = SCRIPT_DIR / "check_persistent_mutation_guard.py"

OUTPUT_STATES = frozenset([
    "HOLD_INVALID_PACKET",
    "HOLD_PLAN_NOT_APPROVED",
    "HOLD_PLAN_HASH_MISMATCH",
    "HOLD_MAIN_DIRTY",
    "HOLD_OUTPUT_PATH_INSIDE_REPO",
    "HOLD_WORKTREE_CREATE_FAILED",
    "HOLD_EXECUTOR_NOT_ALLOWED",
    "HOLD_EXECUTOR_FAILED",
    "HOLD_REPO_MUTATION",
    "HOLD_FORBIDDEN_FILE_TOUCHED",
    "HOLD_OUTSIDE_ALLOWED_FILES",
    "HOLD_TOO_MANY_FILES_CHANGED",
    "HOLD_DIFF_VALIDATION_FAILED",
    "HOLD_UNKNOWN",
    # Real-Claude states
    "HOLD_REAL_EXECUTOR_NOT_ENABLED",
    "HOLD_CLAUDE_IMPLEMENTATION_PENDING",
    "HOLD_CLAUDE_COMMAND_INVALID",
    "HOLD_CLAUDE_TIMEOUT",
    "HOLD_CLAUDE_NONZERO_EXIT",
    "HOLD_CLAUDE_EMPTY_OUTPUT",
    # PMG states
    "HOLD_PMG_SNAPSHOT_FAILED",
    "HOLD_PMG_COMPARE_FAILED",
    "HOLD_EXTERNAL_MUTATION",
    "PATCH_READY_FOR_HUMAN_REVIEW",
])

# ---------------------------------------------------------------------------
# SHA-256 helpers
# ---------------------------------------------------------------------------

def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_str(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git_status(repo_path: str | Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "status", "--porcelain"],
        capture_output=True, text=True, timeout=10
    )
    return result.stdout.strip() or "clean"


def git_status_clean(repo_path: str | Path) -> bool:
    """Check if repo has no staged changes and no unstaged modifications.
    Untracked files (??) are allowed in the working tree and do not block worktree creation.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_path), "status", "--porcelain"],
        capture_output=True, text=True, timeout=10
    )
    lines = [l for l in result.stdout.strip().splitlines() if l.startswith("?? ")]
    # Untracked files are OK; only staged (A/M/D) or unstaged modified ( M) lines block
    non_untracked = [l for l in result.stdout.strip().splitlines() if not l.startswith("?? ")]
    return len(non_untracked) == 0


def git_rev_parse(repo_path: str | Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", ref],
        capture_output=True, text=True, timeout=10
    )
    return result.stdout.strip()


def git_worktree_add(worktree_path: Path, base_sha: str, parent_repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(parent_repo), "worktree", "add", str(worktree_path), base_sha],
        capture_output=True, text=True, timeout=30
    )


def git_worktree_remove(worktree_path: Path, parent_repo: Path) -> None:
    subprocess.run(
        ["git", "-C", str(parent_repo), "worktree", "remove", "--force", str(worktree_path)],
        capture_output=True, text=True, timeout=30
    )
    if worktree_path.exists():
        shutil.rmtree(worktree_path)


def git_diff(worktree_path: Path) -> str:
    """Capture staged + unstaged diff in unified format."""
    result = subprocess.run(
        ["git", "-C", str(worktree_path), "diff", "--cached", "--unified=3"],
        capture_output=True, text=True, timeout=30
    )
    if not result.stdout:
        # Fall back to full diff if --cached is empty (no staged changes)
        result = subprocess.run(
            ["git", "-C", str(worktree_path), "diff", "--unified=3"],
            capture_output=True, text=True, timeout=30
        )
    return result.stdout


def git_diff_name_only(worktree_path: Path) -> list[str]:
    """Return list of all changed file paths (staged AND unstaged) in worktree."""
    staged = subprocess.run(
        ["git", "-C", str(worktree_path), "diff", "--cached", "--name-only"],
        capture_output=True, text=True, timeout=30
    ).stdout.splitlines()
    unstaged = subprocess.run(
        ["git", "-C", str(worktree_path), "diff", "--name-only"],
        capture_output=True, text=True, timeout=30
    ).stdout.splitlines()
    seen = set()
    result = []
    for line in staged + unstaged:
        line = line.strip()
        if line and line not in seen:
            seen.add(line)
            result.append(line)
    return result


# ---------------------------------------------------------------------------
# PMG helpers (Persistent Mutation Guard)
# ---------------------------------------------------------------------------

def pmg_snapshot(target_path: str | Path, output_json: str | Path) -> tuple[bool, str]:
    """
    Run PMG snapshot over target_path and write result to output_json.
    Returns (success, error_message).
    """
    cmd = [
        sys.executable,
        str(PMG_TOOL),
        "snapshot",
        "--root", str(target_path),
        "--output", str(output_json),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return True, ""
        else:
            return False, f"PMG snapshot failed with exit {result.returncode}: {result.stderr}"
    except subprocess.TimeoutExpired:
        return False, "PMG snapshot timed out after 60s"
    except Exception as e:
        return False, f"PMG snapshot exception: {e}"


def pmg_compare(snapshot_json: str | Path, output_json: str | Path, output_md: str | Path) -> tuple[bool, str]:
    """
    Run PMG compare against snapshot_json and write JSON+MD results.
    Returns (success, error_message).
    """
    pmg_target = os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
    cmd = [
        sys.executable,
        str(PMG_TOOL),
        "compare",
        "--root", pmg_target,
        "--before", str(snapshot_json),
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return True, ""
        else:
            return False, f"PMG compare failed with exit {result.returncode}: {result.stderr}"
    except subprocess.TimeoutExpired:
        return False, "PMG compare timed out after 60s"
    except Exception as e:
        return False, f"PMG compare exception: {e}"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def normalize_path(path: str, base: Path) -> Path:
    """Resolve path relative to base, error on escape."""
    p = (base / path).resolve()
    if not str(p).startswith(str(base.resolve())):
        raise ValueError(f"Path escapes base: {path}")
    return p


def path_inside_repo(path: Path, repo_root: Path) -> bool:
    """True if path is inside repo_root."""
    try:
        resolved = path.resolve().relative_to(repo_root.resolve())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Packet validation
# ---------------------------------------------------------------------------

def validate_packet(packet: dict) -> tuple[bool, str]:
    """Check packet_kind and required top-level fields. Returns (ok, error)."""
    if packet.get("packet_kind") != "aed.temp_worktree.execution.v0":
        return False, f"packet_kind must be 'aed.temp_worktree.execution.v0', got '{packet.get('packet_kind')}'"

    for field in ["run_id", "task_id", "base_sha", "approved_plan_path", "approved_plan_sha256",
                  "approval", "task", "execution"]:
        if field not in packet:
            return False, f"missing required field: {field}"

    return True, ""


def validate_approval(approval: dict, plan_path: str) -> tuple[bool, str]:
    """Validate human approval marker. Returns (ok, error)."""
    if not approval.get("approved_for_temp_worktree_execution"):
        return False, "approval.approved_for_temp_worktree_execution must be true"

    if approval.get("approved_by") != "human":
        return False, "approval.approved_by must be 'human'"

    expected_sha = approval.get("approved_plan_sha256", "")
    if not expected_sha:
        return False, "approval.approved_plan_sha256 is required"

    # Verify SHA-256 of the plan file
    plan_file = Path(plan_path)
    if not plan_file.is_file():
        return False, f"approved_plan_path does not exist: {plan_path}"

    actual_sha = sha256_file(plan_file)
    if actual_sha != expected_sha:
        return False, (
            f"approved_plan_sha256 mismatch: expected {expected_sha}, got {actual_sha}"
        )

    # Check timestamp is within 24h
    approved_at = approval.get("approved_at", "")
    if not approved_at:
        return False, "approval.approved_at is required"

    try:
        approved_time = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_hours = (now - approved_time).total_seconds() / 3600
        if age_hours > 24:
            return False, f"approval.approved_at is more than 24h old ({age_hours:.1f}h)"
    except ValueError as e:
        return False, f"approval.approved_at is not valid ISO-8601: {e}"

    return True, ""


# ---------------------------------------------------------------------------
# Constraint checking
# ---------------------------------------------------------------------------

def check_forbidden_file_touched(
    changed_files: list[str],
    forbidden_files: list[str],
    worktree_path: Path
) -> list[str]:
    """Return list of forbidden files that were changed."""
    violated = []
    for cf in changed_files:
        cf_resolved = (worktree_path / cf).resolve()
        for fb in forbidden_files:
            # Normalize forbidden path
            fb_clean = fb.rstrip("/")
            # Check exact match or prefix match
            if cf == fb_clean or cf.startswith(fb_clean + "/"):
                violated.append(cf)
    return violated


def check_outside_allowed(
    changed_files: list[str],
    allowed_files: list[str],
    worktree_path: Path
) -> list[str]:
    """Return list of changed files not in allowed_files."""
    violated = []
    for cf in changed_files:
        if cf not in allowed_files:
            violated.append(cf)
    return violated


def check_protected_gate_scripts(
    changed_files: list[str],
    worktree_path: Path
) -> list[str]:
    """Return list of changed gate scripts."""
    violated = []
    for cf in changed_files:
        if cf in PROTECTED_GATE_SCRIPTS:
            violated.append(cf)
    return violated


def check_too_many_files(
    changed_files: list[str],
    max_changed_files: int
) -> bool:
    """Return True if changed files exceed limit."""
    return len(changed_files) > max_changed_files


# ---------------------------------------------------------------------------
# Mock executor
# ---------------------------------------------------------------------------

def apply_mock_edits(worktree_path: Path, mock_edits: list[dict]) -> list[str]:
    """
    Apply mock_edits to worktree and return list of changed file paths.

    mock_edits format: [{"path": "relative/path.md", "content": "new content"}]
    Content is always a full-file replacement.
    Files are written and `git add` is called so they appear in `git diff --cached`
    and `git diff --name-only` as staged additions.
    """
    changed = []
    for edit in mock_edits:
        file_path = worktree_path / edit["path"]
        # Security: reject absolute paths or paths with ..
        if not str(file_path.resolve()).startswith(str(worktree_path.resolve())):
            raise ValueError(f"mock edit path escapes worktree: {edit['path']}")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(edit["content"], encoding="utf-8")
        changed.append(edit["path"])

    # Stage all written files so git diff captures them
    if changed:
        subprocess.run(
            ["git", "-C", str(worktree_path), "add", "--"] + changed,
            capture_output=True, timeout=10
        )
    return changed


def run_claude_executor_stub(
    packet: dict,
    worktree_root: Path,
    output_root: Path,
    repo_root: Path = REPO_ROOT,
) -> dict:
    """
    Placeholder stub for real executor.
    Returns HOLD_CLAUDE_IMPLEMENTATION_PENDING (valid contract) or
    HOLD_CLAUDE_COMMAND_INVALID (contract validation failed).

    This stub does NOT:
    - Call subprocess with external executor binary
    - Import LLM client libraries
    - Invoke the shell
    - Create any worktree (already created by caller if needed)
    - Run PMG snapshot
    - Write to Hermes or audit
    - Dispatch or merge
    """
    # Build command contract (pure, no side effects)
    contract = build_claude_command_contract(packet, worktree_root, output_root)

    # Validate contract (pure, no side effects)
    is_valid, validation_errors = validate_claude_command_contract(
        contract, packet, worktree_root, output_root, repo_root
    )

    # Build safe summary string (not a command to run)
    summary_parts = [
        f"argv={contract['argv']}",
        f"cwd={contract['cwd']}",
        f"timeout={contract['timeout_seconds']}s",
    ]
    contract_summary = "; ".join(summary_parts)

    if not is_valid:
        return {
            "status": "HOLD_CLAUDE_COMMAND_INVALID",
            "run_id": packet.get("run_id", "unknown"),
            "base_sha": packet.get("base_sha", ""),
            "worktree_path": str(worktree_root),
            "output_root": str(output_root),
            "changed_files": [],
            "validation_errors": validation_errors,
            "main_git_status_before": "unknown",
            "main_git_status_after": "unknown",
            "worktree_git_status_before": "unknown",
            "worktree_git_status_after": "unknown",
            "diff_path": "",
            "patch_ready": False,
            "next_action": "fix command contract validation errors",
            "pmg_snapshot_path": "",
            "pmg_compare_json_path": "",
            "pmg_compare_md_path": "",
            "pmg_status": "not_run",
            "pmg_blocked_files": 0,
            "claude_command_contract_valid": False,
            "claude_command_contract_errors": validation_errors,
            "claude_command_contract_summary": contract_summary,
        }

    return {
        "status": "HOLD_CLAUDE_IMPLEMENTATION_PENDING",
        "run_id": packet.get("run_id", "unknown"),
        "base_sha": packet.get("base_sha", ""),
        "worktree_path": str(worktree_root),
        "output_root": str(output_root),
        "changed_files": [],
        "validation_errors": [
            "Real Claude executor not yet implemented. "
            "Skeleton present: execution.mode='claude' is recognized but blocked. "
            "Use --enable-real-claude-executor flag when real implementation is ready."
        ],
        "main_git_status_before": "unknown",
        "main_git_status_after": "unknown",
        "worktree_git_status_before": "unknown",
        "worktree_git_status_after": "unknown",
        "diff_path": "",
        "patch_ready": False,
        "next_action": "implement run_claude_executor() stub body with real Claude invocation",
        "pmg_snapshot_path": "",
        "pmg_compare_json_path": "",
        "pmg_compare_md_path": "",
        "pmg_status": "not_run",
        "pmg_blocked_files": 0,
        "claude_command_contract_valid": True,
        "claude_command_contract_errors": [],
        "claude_command_contract_summary": contract_summary,
    }


# ---------------------------------------------------------------------------
# Claude command contract builder and validator
# ---------------------------------------------------------------------------
# NOTE: These functions define a SAFE, RESTRICTED command contract for future
# real-Claude executor implementation. They are PURE (no subprocess calls).
# All contract parameters must be verified before any real execution.
# Claude CLI flags listed here are CONSERVATIVE PLACEHOLDERS and must be
# re-verified against actual Claude CLI documentation before implementation.
# ---------------------------------------------------------------------------

# Approved Claude binary names (conservative, no shell expansion)
APPROVED_CLAUDE_BINARIES = frozenset(["claude", "claude.ai", "claude-cli"])

# Forbidden argv patterns for command contract validation.
# Stored as multi-word lists so token-element scanning avoids false positives from path substrings.
# Single-token words caught by direct element comparison in validator.
# Multi-token sequences caught by contiguous subsequence scan.
_FORBIDDEN_ARGV_PATTERNS = [
    ["git", "push"],
    ["gh", "pr", "create"],
    ["gh", "pr", "merge"],
    ["gh", "workflow", "run"],
    ["gh", "api"],
    ["pip", "install"],
    ["npm", "install"],
    ["apt", "install"],
    ["yum", "install"],
    ["brew", "install"],
    ["repository_dispatch"],
    ["board"],
    ["|"],
]

# Maximum timeout in seconds (prevent runaway processes)
MAX_TIMEOUT_SECONDS = 300  # 5 minutes


def build_claude_command_contract(
    packet: dict,
    worktree_root: Path,
    output_root: Path,
) -> dict:
    """
    Build a conservative, safe command contract for future real-Claude executor.

    This is a PURE function — no subprocess, no network, no side effects.

    Returns a structured contract dict with:
      argv          : approved Claude command as list of strings
      cwd           : worktree path (must be validated by caller)
      timeout_seconds: bounded timeout
      env_policy    : restricted environment variable policy
      stdout_path   : output_root / stdout file
      stderr_path   : output_root / stderr file
      transcript_path: output_root / transcript file

    NOTE: The argv constructed here uses CONSERVATIVE PLACEHOLDER flags.
    Actual Claude CLI flags must be re-verified against live Claude documentation
    before replacing this placeholder with real subprocess invocation.
    """
    run_id = packet.get("run_id", "unknown")
    timeout = int(packet.get("execution", {}).get("timeout_seconds", 60))
    # Bound timeout to prevent runaway
    timeout = min(max(timeout, 1), MAX_TIMEOUT_SECONDS)

    # Use a placeholder plan path in worktree for the command
    plan_file_in_worktree = worktree_root / ".aed_plan.md"

    # Conservative placeholder argv — no shell, no prompts, no auto-accept
    # Flags here are PLACEHOLDERS and MUST be re-verified with real Claude CLI docs
    argv = [
        "claude",
        "--no-input",           # no interactive prompt
        "--output-format=md",    # structured output (placeholder)
        str(plan_file_in_worktree),
    ]

    return {
        "argv": argv,
        "cwd": str(worktree_root),
        "timeout_seconds": timeout,
        "argv_summary": f"argv={argv}",  # safe summary for logging
        "env_policy": {
            "allow": [
                "PATH",
                "HOME",
                "USER",
                "LANG",
                "LC_*",
            ],
            "block": [
                "HERMES_HOME",
                "HERMES_PROFILE",
                "HERMES_CONFIG",
                "AED_*",
                "GITHUB_TOKEN",
                "GH_TOKEN",
            ],
        },
        "stdout_path": str(output_root / f"{run_id}_stdout.txt"),
        "stderr_path": str(output_root / f"{run_id}_stderr.txt"),
        "transcript_path": str(output_root / f"{run_id}_transcript.md"),
    }


def validate_claude_command_contract(
    contract: dict,
    packet: dict,
    worktree_root: Path,
    output_root: Path,
    repo_root: Path,
) -> tuple[bool, list[str]]:
    """
    Validate a Claude command contract for safety.

    This is a PURE function — no subprocess, no network, no side effects.

    Returns (is_valid, list_of_error_messages).

    Validates:
    - cwd resolves under /tmp/aed_runs/worktrees/
    - cwd resolves outside the main repo
    - argv is a list of strings
    - argv[0] is an approved Claude binary
    - timeout_seconds is positive and bounded
    - stdout/stderr/transcript paths are under output_root
    - output_root is outside the repo
    - approved_plan_path is outside the repo
    - no forbidden argv patterns (git push, gh pr create, etc.)
    - no permission bypass flags
    """
    errors: list[str] = []

    # --- cwd validation ---
    try:
        cwd_resolved = Path(contract["cwd"]).resolve()
    except Exception as e:
        errors.append(f"cwd is not a valid path: {e}")
        return False, errors

    # Must be under /tmp/aed_runs/worktrees/
    worktrees_base = Path("/tmp/aed_runs/worktrees").resolve()
    if not str(cwd_resolved).startswith(str(worktrees_base) + "/"):
        errors.append(
            f"cwd must be under {worktrees_base}/, got {cwd_resolved}"
        )

    # Must be outside the main repo
    try:
        repo_root_resolved = repo_root.resolve()
    except Exception:
        repo_root_resolved = repo_root

    if str(cwd_resolved).startswith(str(repo_root_resolved) + "/"):
        errors.append(
            f"cwd must be outside main repo {repo_root_resolved}, got {cwd_resolved}"
        )

    # --- argv validation ---
    argv = contract.get("argv", [])
    if not isinstance(argv, list):
        errors.append(f"argv must be a list, got {type(argv).__name__}")
        return False, errors

    if not all(isinstance(arg, str) for arg in argv):
        errors.append("all argv elements must be strings")

    if not argv:
        errors.append("argv cannot be empty")
        return False, errors

    # argv[0] must be an approved binary name
    binary = argv[0]
    # Strip path components for binary name check (no / in binary name)
    binary_name = binary.split("/")[-1].lower()
    if binary_name not in APPROVED_CLAUDE_BINARIES:
        errors.append(
            f"argv[0] must be one of {sorted(APPROVED_CLAUDE_BINARIES)}, "
            f"got '{binary}'"
        )

    # Check for forbidden argv patterns.
    # Check single-token forbidden words and multi-token forbidden sequences.
    # We iterate over argv elements to avoid false matches from path substrings.
    FORBIDDEN_TOKENS = frozenset([
        "git", "push",
        "gh", "pr", "create", "merge",
        "gh", "workflow", "run",
        "gh", "api",
        "repository_dispatch",
        "board",
        "sudo",
        "pip", "npm", "apt", "yum", "brew",
        "--dangerously-skip-permissions",
        "--dangerously-skip-perm",
        "--bypasspermissions",
        "--unrestricted",
        "--allow-write",
    ])
    FORBIDDEN_SEQUENCES = [
        ("gh", "pr", "create"),
        ("gh", "pr", "merge"),
        ("gh", "workflow", "run"),
        ("gh", "api"),
        ("pip", "install"),
        ("npm", "install"),
        ("apt", "install"),
        ("yum", "install"),
        ("brew", "install"),
        ("git", "push"),
        ("|",),
    ]

    # Single-token check: only check argv elements, not joined string (avoids path false positives)
    argv_lower = [a.lower() for a in argv]
    for i, token in enumerate(argv_lower):
        if token in FORBIDDEN_TOKENS:
            errors.append(f"forbidden token '{token}' found in argv")
            break

    # Sequence check: check contiguous subsequences
    for seq in FORBIDDEN_SEQUENCES:
        seq_len = len(seq)
        found = any(
            tuple(argv_lower[i:i+seq_len]) == seq
            for i in range(len(argv_lower) - seq_len + 1)
        )
        if found:
            errors.append(f"forbidden sequence {' '.join(seq)!r} found in argv")
            break

    # --- timeout validation ---
    timeout = contract.get("timeout_seconds", 0)
    if not isinstance(timeout, (int, float)):
        errors.append(f"timeout_seconds must be numeric, got {type(timeout).__name__}")
    elif timeout <= 0:
        errors.append(f"timeout_seconds must be positive, got {timeout}")
    elif timeout > MAX_TIMEOUT_SECONDS:
        errors.append(
            f"timeout_seconds exceeds maximum {MAX_TIMEOUT_SECONDS}, got {timeout}"
        )

    # --- output path validation ---
    output_root_resolved = output_root.resolve()
    for path_key in ["stdout_path", "stderr_path", "transcript_path"]:
        path_val = contract.get(path_key, "")
        if not path_val:
            errors.append(f"{path_key} is required")
            continue
        try:
            resolved = Path(path_val).resolve()
            if not str(resolved).startswith(str(output_root_resolved) + "/"):
                errors.append(
                    f"{path_key} must be under output_root {output_root_resolved}, "
                    f"got {resolved}"
                )
        except Exception as e:
            errors.append(f"{path_key} is not a valid path: {e}")

    # --- output_root outside repo ---
    if path_inside_repo(output_root, repo_root):
        errors.append(
            f"output_root must be outside main repo, got {output_root}"
        )

    # --- approved_plan_path outside repo ---
    plan_path_str = packet.get("approved_plan_path", "")
    if plan_path_str:
        try:
            plan_resolved = Path(plan_path_str).resolve()
            if path_inside_repo(plan_resolved, repo_root):
                errors.append(
                    f"approved_plan_path must be outside main repo, "
                    f"got {plan_resolved}"
                )
        except Exception:
            pass  # will be caught by other validation

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Real Claude executor
# ---------------------------------------------------------------------------
# Called only when execution.mode="claude" AND --enable-real-claude-executor
# is set AND command contract is valid.
# Runs Claude CLI in the worktree, captures output, writes artifact files.
# ---------------------------------------------------------------------------

def run_claude_executor(
    packet: dict,
    worktree_root: Path,
    output_root: Path,
    contract: dict,
    repo_root: Path = REPO_ROOT,
) -> dict:
    """
    Run real Claude CLI in the worktree using the validated command contract.

    This function is ONLY reached when:
      1. execution.mode == "claude"
      2. --enable-real-claude-executor flag is set
      3. build_claude_command_contract succeeded
      4. validate_claude_command_contract returned True

    Responsibilities:
      - Call subprocess.run with list-form argv, cwd=worktree_root, no shell=True
      - Capture stdout and stderr
      - Write claude_stdout.txt, claude_stderr.txt, claude_transcript.md
      - Handle timeout, nonzero exit, empty output
      - Return a result dict that the caller (run()) propagates

    This function does NOT:
      - Call git push, gh pr, or any write to the main repo
      - Import LLM client libraries (claude SDK, anthropic, openai, etc.)
      - Use shell=True
      - Dispatch, update boards, append audit, or write to memory/profile
    """
    run_id = packet.get("run_id", "unknown")
    argv = contract["argv"]          # list, e.g. ["claude", "--continue", ...]
    timeout = contract["timeout_seconds"]

    # Sanity-check argv is a list of strings (contract validated but we double-check)
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return {
            "status": "HOLD_CLAUDE_COMMAND_INVALID",
            "claude_exit_code": None,
            "claude_started_at": "",
            "claude_finished_at": "",
            "claude_elapsed_seconds": 0.0,
            "claude_stdout_path": "",
            "claude_stderr_path": "",
            "claude_transcript_path": "",
            "claude_command_contract_valid": True,
            "claude_command_contract_errors": [],
            "claude_command_contract_summary": "",
        }

    output_root_path = Path(output_root)
    output_root_path.mkdir(parents=True, exist_ok=True)

    claude_started_at = datetime.now(timezone.utc).isoformat()

    stdout_path = output_root_path / "claude_stdout.txt"
    stderr_path = output_root_path / "claude_stderr.txt"
    transcript_path = output_root_path / "claude_transcript.md"

    try:
        proc = subprocess.run(
            argv,
            cwd=str(worktree_root.resolve()),
            capture_output=True,
            text=True,
            timeout=timeout,
            # NOTE: shell=False (list-form argv only)
        )
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        finished_at = datetime.now(timezone.utc).isoformat()
        # Write whatever partial output we can capture
        stdout_path.write_text("(timeout - partial output)", encoding="utf-8")
        stderr_path.write_text(f"(timeout after {timeout}s)", encoding="utf-8")
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(claude_started_at)).total_seconds()
        return {
            "status": "HOLD_CLAUDE_TIMEOUT",
            "claude_exit_code": -1,
            "claude_started_at": claude_started_at,
            "claude_finished_at": finished_at,
            "claude_elapsed_seconds": elapsed,
            "claude_stdout_path": str(stdout_path),
            "claude_stderr_path": str(stderr_path),
            "claude_transcript_path": "",
            "claude_command_contract_valid": True,
            "claude_command_contract_errors": [f"Claude timed out after {timeout}s"],
            "claude_command_contract_summary": contract.get("argv_summary", ""),
        }
    except Exception as e:
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            "status": "HOLD_CLAUDE_COMMAND_INVALID",
            "claude_exit_code": -2,
            "claude_started_at": claude_started_at,
            "claude_finished_at": finished_at,
            "claude_elapsed_seconds": 0.0,
            "claude_stdout_path": "",
            "claude_stderr_path": "",
            "claude_transcript_path": "",
            "claude_command_contract_valid": True,
            "claude_command_contract_errors": [f"subprocess error: {e}"],
            "claude_command_contract_summary": contract.get("argv_summary", ""),
        }

    finished_at = datetime.now(timezone.utc).isoformat()
    elapsed = (datetime.fromisoformat(finished_at) - datetime.fromisoformat(claude_started_at)).total_seconds()

    # Write artifacts
    stdout_path.write_text(proc.stdout, encoding="utf-8")
    stderr_path.write_text(proc.stderr, encoding="utf-8")

    # Build combined transcript
    transcript_lines = [
        f"# Claude Execution Transcript",
        f"# Run: {run_id}",
        f"# Started: {claude_started_at}",
        f"# Finished: {finished_at}",
        f"# Elapsed: {elapsed:.1f}s",
        f"# Exit code: {exit_code}",
        f"# CWD: {worktree_root}",
        f"# Command: {' '.join(argv)}",
        "",
        "## STDOUT",
        proc.stdout or "(empty)",
        "",
        "## STDERR",
        proc.stderr or "(empty)",
    ]
    transcript_path.write_text("\n".join(transcript_lines), encoding="utf-8")

    # Nonzero exit → HOLD
    if exit_code != 0:
        return {
            "status": "HOLD_CLAUDE_NONZERO_EXIT",
            "claude_exit_code": exit_code,
            "claude_started_at": claude_started_at,
            "claude_finished_at": finished_at,
            "claude_elapsed_seconds": elapsed,
            "claude_stdout_path": str(stdout_path),
            "claude_stderr_path": str(stderr_path),
            "claude_transcript_path": str(transcript_path),
            "claude_command_contract_valid": True,
            "claude_command_contract_errors": [],  # contract valid; execution failed
            "claude_command_contract_summary": contract.get("argv_summary", ""),
        }

    return {
        "status": "CLAUDE_EXECUTOR_SUCCESS",
        "claude_exit_code": exit_code,
        "claude_started_at": claude_started_at,
        "claude_finished_at": finished_at,
        "claude_elapsed_seconds": elapsed,
        "claude_stdout_path": str(stdout_path),
        "claude_stderr_path": str(stderr_path),
        "claude_transcript_path": str(transcript_path),
        "claude_command_contract_valid": True,
        "claude_command_contract_errors": [],
        "claude_command_contract_summary": contract.get("argv_summary", ""),
    }


def run(
    packet: dict,
    output_json: str,
    output_md: str,
    enable_real_claude_executor: bool = False,
) -> dict:
    """
    Main execution path. Returns the result dict (also written to output_json).

    Args:
        packet: execution packet dict
        output_json: path to write result JSON
        output_md: path to write result Markdown
        enable_real_claude_executor: if True, allow execution.mode='claude' to
            proceed to the real executor (with full PMG + worktree guard).
            Defaults to False (claude mode blocked without this flag).
    """
    run_id = packet.get("run_id", "unknown")
    worktree_root = WORKTREE_BASE / run_id
    output_root = Path(packet.get("execution", {}).get("output_root", f"/tmp/aed_runs/{run_id}"))

    result = {
        "status": "HOLD_UNKNOWN",
        "run_id": run_id,
        "base_sha": packet.get("base_sha", ""),
        "worktree_path": str(worktree_root),
        "output_root": str(output_root),
        "changed_files": [],
        "validation_errors": [],
        "main_git_status_before": "unknown",
        "main_git_status_after": "unknown",
        "worktree_git_status_before": "unknown",
        "worktree_git_status_after": "unknown",
        "diff_path": "",
        "patch_ready": False,
        "next_action": "fix validation error and retry",
        # PMG fields
        "pmg_snapshot_path": "",
        "pmg_compare_json_path": "",
        "pmg_compare_md_path": "",
        "pmg_status": "not_run",
        "pmg_blocked_files": 0,
        # Claude command contract fields (for execution.mode="claude" path)
        "claude_command_contract_valid": None,
        "claude_command_contract_errors": [],
        "claude_command_contract_summary": "",
    }

    # ---- Phase 1: Packet validation ---------------------------------------

    ok, err = validate_packet(packet)
    if not ok:
        result["status"] = "HOLD_INVALID_PACKET"
        result["validation_errors"] = [err]
        result["next_action"] = "fix packet format"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 2: Approval marker -----------------------------------------

    approval = packet.get("approval", {})
    plan_path = packet.get("approved_plan_path", "")
    ok, err = validate_approval(approval, plan_path)
    if not ok:
        result["status"] = "HOLD_PLAN_NOT_APPROVED"
        result["validation_errors"] = [err]
        result["next_action"] = "obtain valid human approval marker"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 3: Main repo clean check ------------------------------------

    main_status_before = git_status(REPO_ROOT)
    result["main_git_status_before"] = main_status_before

    if not git_status_clean(REPO_ROOT):
        result["status"] = "HOLD_MAIN_DIRTY"
        result["validation_errors"] = [f"main repo has staged or unstaged changes; untracked files are allowed: {main_status_before}"]
        result["next_action"] = "clean main repo (commit, reset, or discard staged/unstaged changes) and retry"
        _write_output(result, output_json, output_md)
        return result

# Verify main is at base_sha
    main_head = git_rev_parse(REPO_ROOT, "HEAD")
    if main_head != packet.get("base_sha"):
        result["status"] = "HOLD_MAIN_DIRTY"
        result["validation_errors"] = [
            f"main HEAD ({main_head}) != packet base_sha ({packet.get('base_sha')})"
        ]
        result["next_action"] = "ensure main is at base_sha and retry"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 4: Path safety checks ---------------------------------------

    output_root = Path(packet.get("execution", {}).get("output_root", f"/tmp/aed_runs/{run_id}"))
    if path_inside_repo(output_root, REPO_ROOT):
        result["status"] = "HOLD_OUTPUT_PATH_INSIDE_REPO"
        result["validation_errors"] = [f"output_root cannot be inside repo: {output_root}"]
        result["next_action"] = "move output_root outside repo"
        _write_output(result, output_json, output_md)
        return result

    if path_inside_repo(worktree_root, REPO_ROOT):
        result["status"] = "HOLD_WORKTREE_CREATE_FAILED"
        result["validation_errors"] = [f"worktree path cannot be inside repo: {worktree_root}"]
        result["next_action"] = "ensure worktree root is outside repo"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 5: Execution mode check -------------------------------------

    exec_mode = packet.get("execution", {}).get("mode", "mock")

    # Unsupported modes (not mock, not claude) → always blocked
    unsupported_modes = {"real", "execute", "run", "agent"}
    if exec_mode in unsupported_modes:
        result["status"] = "HOLD_EXECUTOR_NOT_ALLOWED"
        result["validation_errors"] = [f"execution.mode must be 'mock', got '{exec_mode}'"]
        result["next_action"] = "set execution.mode to 'mock' or use a different harness"
        _write_output(result, output_json, output_md)
        return result

    # claude mode: blocked by default unless --enable-real-claude-executor is set
    if exec_mode == "claude":
        if not enable_real_claude_executor:
            result["status"] = "HOLD_REAL_EXECUTOR_NOT_ENABLED"
            result["validation_errors"] = [
                "execution.mode='claude' requires --enable-real-claude-executor flag. "
                "Real Claude executor is not yet enabled."
            ]
            result["next_action"] = "pass --enable-real-claude-executor to enable real Claude mode, " \
                                   "or use execution.mode='mock' for mock execution"
            _write_output(result, output_json, output_md)
            return result

        # Flag present: build and validate command contract first.
        # Only proceed to real execution if contract is valid.
        contract = build_claude_command_contract(packet, worktree_root, output_root)
        is_valid, validation_errors = validate_claude_command_contract(
            contract, packet, worktree_root, output_root, REPO_ROOT
        )
        result["claude_command_contract_valid"] = is_valid
        result["claude_command_contract_errors"] = validation_errors
        contract_summary = "; ".join([
            f"argv={contract['argv']}",
            f"cwd={contract['cwd']}",
            f"timeout={contract['timeout_seconds']}s",
        ])
        result["claude_command_contract_summary"] = contract_summary

        if not is_valid:
            result["status"] = "HOLD_CLAUDE_COMMAND_INVALID"
            result["validation_errors"] = validation_errors
            result["next_action"] = "fix command contract validation errors"
            _write_output(result, output_json, output_md)
            return result

        # Contract valid: proceed to real execution with full PMG + worktree guard.
        # Phase 5b: PMG pre-snapshot (before worktree creation)
        pmg_target = os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
        pmg_snapshot_path = str(output_root / "pmg_snapshot.json")
        pmg_compare_json_path = str(output_root / "pmg_compare.json")
        pmg_compare_md_path = str(output_root / "pmg_compare.md")
        result["pmg_snapshot_path"] = pmg_snapshot_path
        result["pmg_compare_json_path"] = pmg_compare_json_path
        result["pmg_compare_md_path"] = pmg_compare_md_path

        output_root_path = Path(output_root)
        output_root_path.mkdir(parents=True, exist_ok=True)
        ok, snap_err = pmg_snapshot(pmg_target, pmg_snapshot_path)
        if not ok:
            result["status"] = "HOLD_PMG_SNAPSHOT_FAILED"
            result["validation_errors"] = [f"PMG snapshot failed: {snap_err}"]
            result["next_action"] = "check HERMES_HOME path and PMG tool availability"
            _write_output(result, output_json, output_md)
            return result

        # Phase 6: Create worktree
        if worktree_root.exists():
            try:
                git_worktree_remove(worktree_root, REPO_ROOT)
            except Exception:
                pass
            shutil.rmtree(worktree_root, ignore_errors=True)
        worktree_root.mkdir(parents=True, exist_ok=True)
        base_sha = packet.get("base_sha", "")
        wt_result = git_worktree_add(worktree_root, base_sha, REPO_ROOT)
        if wt_result.returncode != 0:
            result["status"] = "HOLD_WORKTREE_CREATE_FAILED"
            result["validation_errors"] = [f"git worktree add failed: {wt_result.stderr}"]
            result["next_action"] = "check base_sha is valid and worktree path is available"
            _write_output(result, output_json, output_md)
            return result

        # Phase 7: Pre-execution git status
        main_status_after_create = git_status(REPO_ROOT)
        if not git_status_clean(REPO_ROOT):
            result["status"] = "HOLD_REPO_MUTATION"
            result["validation_errors"] = [
                f"main repo became dirty after worktree creation: {main_status_after_create}"
            ]
            result["next_action"] = "investigate main repo mutation"
            _write_output(result, output_json, output_md)
            return result

        worktree_status_before = git_status(worktree_root)
        result["worktree_git_status_before"] = worktree_status_before

        # Phase 7b: Copy approved plan into worktree so Claude has the instructions
        approved_plan_path = packet.get("approved_plan_path", "")
        if approved_plan_path:
            plan_src = Path(approved_plan_path)
            plan_dst = worktree_root / ".aed_plan.md"
            if plan_src.is_file():
                try:
                    shutil.copy2(plan_src, plan_dst)
                except Exception as e:
                    result["status"] = "HOLD_PLAN_COPY_FAILED"
                    result["validation_errors"] = [f"failed to copy approved plan to worktree: {e}"]
                    result["next_action"] = "check approved_plan_path is accessible"
                    _write_output(result, output_json, output_md)
                    return result

        # Phase 8: Run real Claude executor
        claude_result = run_claude_executor(packet, worktree_root, output_root, contract, repo_root=REPO_ROOT)
        result.update(claude_result)

        # If executor returned a HOLD, propagate it immediately
        if result["status"].startswith("HOLD_"):
            _write_output(result, output_json, output_md)
            return result

        # Executor succeeded: continue to post-execution validation
        worktree_status_after = git_status(worktree_root)
        result["worktree_git_status_after"] = worktree_status_after
        main_status_after = git_status(REPO_ROOT)
        result["main_git_status_after"] = main_status_after

        if not git_status_clean(REPO_ROOT):
            result["status"] = "HOLD_REPO_MUTATION"
            result["validation_errors"] = [f"main repo git status changed during execution: {main_status_after}"]
            result["next_action"] = "investigate main repo mutation"
            _write_output(result, output_json, output_md)
            return result

        # Phase 9: PMG post-compare
        ok, cmp_err = pmg_compare(pmg_snapshot_path, pmg_compare_json_path, pmg_compare_md_path)
        if not ok:
            result["status"] = "HOLD_PMG_COMPARE_FAILED"
            result["validation_errors"] = [f"PMG compare failed: {cmp_err}"]
            result["next_action"] = "check PMG tool and snapshot integrity"
            _write_output(result, output_json, output_md)
            return result

        try:
            compare_data = json.loads(Path(pmg_compare_json_path).read_text(encoding="utf-8"))
            pmg_status = compare_data.get("status", "unknown")
            result["pmg_status"] = pmg_status
            result["pmg_blocked_files"] = compare_data.get("blocked", 0)
        except Exception as e:
            result["status"] = "HOLD_PMG_COMPARE_FAILED"
            result["validation_errors"] = [f"failed to read PMG compare result: {e}"]
            _write_output(result, output_json, output_md)
            return result

        if pmg_status != "clean":
            result["status"] = "HOLD_EXTERNAL_MUTATION"
            blocked = compare_data.get("blocked", "?")
            result["validation_errors"] = [f"external mutation detected by PMG: status={pmg_status}, blocked={blocked}"]
            result["next_action"] = "investigate Hermes tree mutation; clean environment and retry"
            _write_output(result, output_json, output_md)
            return result

        # Phase 10: Collect changed files and diff
        diff_text = git_diff(worktree_root)
        diff_path = str(output_root / "diff.patch")
        Path(diff_path).write_text(diff_text, encoding="utf-8")
        result["diff_path"] = str(diff_path)
        result["changed_files"] = git_diff_name_only(worktree_root)

        # Filter out the harness-managed plan file from collected changes
        result["changed_files"] = [f for f in result["changed_files"] if f != ".aed_plan.md"]

        # Empty output check (Claude succeeded but no files changed)
        if not result["changed_files"]:
            result["status"] = "HOLD_CLAUDE_EMPTY_OUTPUT"
            result["validation_errors"] = ["Claude executor returned successfully but no files were changed"]
            result["next_action"] = "verify the approved plan produces file changes, or investigate executor behavior"
            _write_output(result, output_json, output_md)
            return result

        if result["changed_files"] and not diff_text.strip():
            result["status"] = "HOLD_DIFF_VALIDATION_FAILED"
            result["validation_errors"] = ["changed_files is non-empty but diff.patch is empty"]
            result["next_action"] = "check git diff capture; ensure edits are staged correctly"
            _write_output(result, output_json, output_md)
            return result

        # Phase 11: Diff validation
        task = packet.get("task", {})
        allowed_files = task.get("allowed_files", [])
        forbidden_files = task.get("forbidden_files", [])
        validation_errors: list[str] = []
        if check_forbidden_file_touched(result["changed_files"], forbidden_files):
            validation_errors.append("changed_files contains a forbidden file")
        if check_outside_allowed(result["changed_files"], allowed_files):
            validation_errors.append("changed_files contains a file outside allowed scope")
        if check_protected_gate_scripts(result["changed_files"], PROTECTED_GATE_SCRIPTS):
            validation_errors.append("changed_files contains a protected gate script")
        if check_too_many_files(result["changed_files"], approval.get("max_changed_files", task.get("max_changed_files", 50))):
            max_allowed = approval.get("max_changed_files", task.get("max_changed_files", 50))
            validation_errors.append(f"too many files changed: {len(result['changed_files'])} (max {max_allowed})")

        if validation_errors:
            result["status"] = "HOLD_POST_EXEC_VALIDATION_FAILED"
            result["validation_errors"] = validation_errors
            result["next_action"] = "review changed files against task constraints"
            _write_output(result, output_json, output_md)
            return result

        result["status"] = "PATCH_READY_FOR_HUMAN_REVIEW"
        result["patch_ready"] = True
        result["next_action"] = "human reviews diff.patch; manually apply or discard"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 5b: PMG pre-snapshot ----------------------------------------
    # Snapshot the Hermes home tree before any worktree creation.
    # This detects if something already mutated Hermes before we started.

    pmg_target = os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
    pmg_snapshot_path = str(output_root / "pmg_snapshot.json")
    pmg_compare_json_path = str(output_root / "pmg_compare.json")
    pmg_compare_md_path = str(output_root / "pmg_compare.md")

    result["pmg_snapshot_path"] = pmg_snapshot_path

    output_root_path = Path(output_root)
    output_root_path.mkdir(parents=True, exist_ok=True)

    ok, snap_err = pmg_snapshot(pmg_target, pmg_snapshot_path)
    if not ok:
        result["status"] = "HOLD_PMG_SNAPSHOT_FAILED"
        result["validation_errors"] = [f"PMG snapshot failed: {snap_err}"]
        result["next_action"] = "check HERMES_HOME path and PMG tool availability"
        _write_output(result, output_json, output_md)
        return result
    # ---- Phase 6: Create worktree -----------------------------------------

    if worktree_root.exists():
        try:
            git_worktree_remove(worktree_root, REPO_ROOT)
        except Exception:
            pass
        shutil.rmtree(worktree_root, ignore_errors=True)

    worktree_root.mkdir(parents=True, exist_ok=True)

    base_sha = packet.get("base_sha", "")
    wt_result = git_worktree_add(worktree_root, base_sha, REPO_ROOT)
    if wt_result.returncode != 0:
        result["status"] = "HOLD_WORKTREE_CREATE_FAILED"
        result["validation_errors"] = [f"git worktree add failed: {wt_result.stderr}"]
        result["next_action"] = "check base_sha is valid and worktree path is available"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 7: Pre-execution git status --------------------------------

    main_status_after_create = git_status(REPO_ROOT)
    if not git_status_clean(REPO_ROOT):
        result["status"] = "HOLD_REPO_MUTATION"
        result["validation_errors"] = [
            f"main repo became dirty after worktree creation: {main_status_after_create}"
        ]
        result["next_action"] = "investigate main repo mutation"
        _write_output(result, output_json, output_md)
        return result

    worktree_status_before = git_status(worktree_root)
    result["worktree_git_status_before"] = worktree_status_before

    # ---- Phase 8: Run mock executor -----------------------------------------

    mock_edits = packet.get("execution", {}).get("mock_edits", [])
    try:
        changed_files = apply_mock_edits(worktree_root, mock_edits)
    except Exception as e:
        result["status"] = "HOLD_EXECUTOR_FAILED"
        result["validation_errors"] = [f"mock executor failed: {e}"]
        result["next_action"] = "check mock_edits format"
        _write_output(result, output_json, output_md)
        return result

    # If no mock_edits, the worktree should be clean (no files changed)
    if not changed_files:
        # Write an empty diff for the no-change case
        output_root = Path(packet.get("execution", {}).get("output_root", f"/tmp/aed_runs/{run_id}"))
        diff_path = str(output_root / "diff.patch")
        Path(diff_path).write_text("", encoding="utf-8")
        result["status"] = "PATCH_READY_FOR_HUMAN_REVIEW"
        result["changed_files"] = []
        result["validation_errors"] = []
        result["patch_ready"] = True
        result["next_action"] = "human reviews empty diff; no patch to apply"
        result["worktree_git_status_after"] = git_status(worktree_root)
        result["main_git_status_after"] = git_status(REPO_ROOT)
        result["diff_path"] = diff_path
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 9: Post-execution status capture ---------------------------

    worktree_status_after = git_status(worktree_root)
    result["worktree_git_status_after"] = worktree_status_after

    main_status_after = git_status(REPO_ROOT)
    result["main_git_status_after"] = main_status_after

    if not git_status_clean(REPO_ROOT):
        result["status"] = "HOLD_REPO_MUTATION"
        result["validation_errors"] = [
            f"main repo git status changed during execution: {main_status_after}"
        ]
        result["next_action"] = "investigate main repo mutation"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 9b: PMG post-compare ----------------------------------------
    # Run PMG compare to detect any external (Hermes) mutations that occurred
    # during worktree creation and mock execution.

    result["pmg_compare_json_path"] = pmg_compare_json_path
    result["pmg_compare_md_path"] = pmg_compare_md_path

    ok, cmp_err = pmg_compare(pmg_snapshot_path, pmg_compare_json_path, pmg_compare_md_path)
    if not ok:
        result["status"] = "HOLD_PMG_COMPARE_FAILED"
        result["validation_errors"] = [f"PMG compare failed: {cmp_err}"]
        result["next_action"] = "check PMG tool and snapshot integrity"
        _write_output(result, output_json, output_md)
        return result

    # Read compare result to check status
    try:
        compare_data = json.loads(Path(pmg_compare_json_path).read_text(encoding="utf-8"))
        pmg_status = compare_data.get("status", "unknown")
        result["pmg_status"] = pmg_status
        result["pmg_blocked_files"] = compare_data.get("blocked", 0)
    except Exception as e:
        result["status"] = "HOLD_PMG_COMPARE_FAILED"
        result["validation_errors"] = [f"failed to read PMG compare result: {e}"]
        result["next_action"] = "check PMG compare output file"
        _write_output(result, output_json, output_md)
        return result

    # If PMG detected mutations, block with external mutation state
    if pmg_status != "clean":
        result["status"] = "HOLD_EXTERNAL_MUTATION"
        blocked = compare_data.get("blocked", "?")
        result["validation_errors"] = [
            f"external mutation detected by PMG: status={pmg_status}, blocked={blocked}"
        ]
        result["next_action"] = "investigate Hermes tree mutation; clean environment and retry"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 10: Collect changed files and diff --------------------------

    diff_text = git_diff(worktree_root)
    output_root = Path(packet.get("execution", {}).get("output_root", f"/tmp/aed_runs/{run_id}"))
    diff_path = output_root / "diff.patch"
    diff_path.write_text(diff_text, encoding="utf-8")
    result["diff_path"] = str(diff_path)

    result["changed_files"] = changed_files

    # If changed_files is non-empty but diff is empty, block — diff is required for human review
    if changed_files and not diff_text.strip():
        result["status"] = "HOLD_DIFF_VALIDATION_FAILED"
        result["validation_errors"] = ["changed_files is non-empty but diff.patch is empty"]
        result["next_action"] = "check git diff capture; ensure mock_edits are staged correctly"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 11: Diff validation -----------------------------------------

    task = packet.get("task", {})
    allowed_files = task.get("allowed_files", [])
    forbidden_files = task.get("forbidden_files", [])

    validation_errors: list[str] = []

    # Check each class of violation
    outside_allowed = check_outside_allowed(changed_files, allowed_files, worktree_root)
    if outside_allowed:
        for f in outside_allowed:
            validation_errors.append(f"file changed outside allowed_files: {f}")

    forbidden_touched = check_forbidden_file_touched(changed_files, forbidden_files, worktree_root)
    if forbidden_touched:
        for f in forbidden_touched:
            validation_errors.append(f"forbidden file touched: {f}")

    gate_scripts = check_protected_gate_scripts(changed_files, worktree_root)
    if gate_scripts:
        for f in gate_scripts:
            validation_errors.append(f"protected gate script modified: {f}")

    max_files = approval.get("max_changed_files", 999)
    if check_too_many_files(changed_files, max_files):
        validation_errors.append(
            f"changed files ({len(changed_files)}) exceeds max_changed_files ({max_files})"
        )

    if validation_errors:
        result["status"] = (
            "HOLD_FORBIDDEN_FILE_TOUCHED" if forbidden_touched else
            "HOLD_OUTSIDE_ALLOWED_FILES" if outside_allowed else
            "HOLD_TOO_MANY_FILES_CHANGED" if check_too_many_files(changed_files, max_files) else
            "HOLD_DIFF_VALIDATION_FAILED"
        )
        result["validation_errors"] = validation_errors
        result["next_action"] = "fix constraint violations in plan"
        _write_output(result, output_json, output_md)
        return result

    # ---- Phase 12: Success ------------------------------------------------

    result["status"] = "PATCH_READY_FOR_HUMAN_REVIEW"
    result["patch_ready"] = True
    result["next_action"] = "human reviews diff.patch; manually apply or discard"
    _write_output(result, output_json, output_md)
    return result


def _write_output(result: dict, output_json: str, output_md: str) -> None:
    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    md = _render_markdown(result)
    Path(output_md).parent.mkdir(parents=True, exist_ok=True)
    with open(output_md, "w", encoding="utf-8") as f:
        f.write(md)


def _render_markdown(result: dict) -> str:
    status = result["status"]
    patch_ready = result.get("patch_ready", False)

    lines = [
        "# Temp-Worktree Execution Result",
        "",
        f"**Status**: `{status}`",
        f"**Run ID**: `{result.get('run_id', 'unknown')}`",
        f"**Base SHA**: `{result.get('base_sha', '')}`",
        f"**Worktree**: `{result.get('worktree_path', '')}`",
        f"**Changed files**: {len(result.get('changed_files', []))}",
        "",
    ]

    if result.get("changed_files"):
        lines.append("## Changed Files")
        for cf in result["changed_files"]:
            lines.append(f"- `{cf}`")
        lines.append("")

    errors = result.get("validation_errors", [])
    if errors:
        lines.append("## Validation Errors")
        for err in errors:
            lines.append(f"- `{err}`")
        lines.append("")

    # PMG section
    pmg_status = result.get("pmg_status", "not_run")
    lines.append("## Persistent Mutation Guard (PMG)")
    lines.append(f"- **PMG status**: `{pmg_status}`")
    if result.get("pmg_snapshot_path"):
        lines.append(f"- **Snapshot**: `{result['pmg_snapshot_path']}`")
    if result.get("pmg_compare_json_path"):
        lines.append(f"- **Compare JSON**: `{result['pmg_compare_json_path']}`")
    if result.get("pmg_compare_md_path"):
        lines.append(f"- **Compare MD**: `{result['pmg_compare_md_path']}`")
    blocked = result.get("pmg_blocked_files", 0)
    lines.append(f"- **Blocked files**: `{blocked}`")
    lines.append("")

    lines.extend([
        f"**Patch ready**: {patch_ready}",
        f"**Next action**: {result.get('next_action', 'unknown')}",
        "",
        f"**Main git status before**: `{result.get('main_git_status_before', 'unknown')}`",
        f"**Main git status after**: `{result.get('main_git_status_after', 'unknown')}`",
        f"**Worktree git status before**: `{result.get('worktree_git_status_before', 'unknown')}`",
        f"**Worktree git status after**: `{result.get('worktree_git_status_after', 'unknown')}`",
        "",
        f"**Diff**: `{result.get('diff_path', 'none')}`",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Temp-worktree execution harness v0. "
                    "Mock execution only. No real Claude."
    )
    parser.add_argument(
        "--packet-json", required=True,
        help="Path to execution packet JSON"
    )
    parser.add_argument(
        "--output-json", required=True,
        help="Path to write result JSON"
    )
    parser.add_argument(
        "--output-md", required=True,
        help="Path to write result Markdown"
    )
    parser.add_argument(
        "--enable-real-claude-executor",
        action="store_true",
        default=False,
        help="Enable real-Claude executor mode (disabled by default). "
             "Without this flag, execution.mode='claude' returns "
             "HOLD_REAL_EXECUTOR_NOT_ENABLED."
    )

    args = parser.parse_args()

    packet_path = Path(args.packet_json)
    if not packet_path.is_file():
        print(f"FATAL: packet file not found: {args.packet_json}", file=sys.stderr)
        return 1

    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"FATAL: invalid JSON in packet: {e}", file=sys.stderr)
        return 1

    result = run(
        packet, args.output_json, args.output_md,
        enable_real_claude_executor=args.enable_real_claude_executor
    )
    print(f"Status: {result['status']}")
    print(f"Output: {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())