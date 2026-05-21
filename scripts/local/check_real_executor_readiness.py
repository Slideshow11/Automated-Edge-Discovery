#!/usr/bin/env python3
"""
check_real_executor_readiness.py

Reads the readiness gate doc and verifies AED is allowed to begin implementing
real Claude executor mode. No execution, no Claude invocation, no state changes.

Exit codes:
    0 — check complete (any status written to output)
    1 — fatal error (missing args, file read error)

Usage:
    python3 scripts/local/check_real_executor_readiness.py \
        --output-json /tmp/real_executor_readiness.json \
        --output-md /tmp/real_executor_readiness.md
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.resolve()

# Files that must exist for READY_TO_IMPLEMENT_REAL_EXECUTOR
REQUIRED_SCRIPTS = [
    "scripts/local/run_temp_worktree_execution.py",
    "scripts/local/build_temp_worktree_execution_packet.py",
    "scripts/local/run_plan_preview.py",
    "scripts/local/plan_preview_eval_status.py",
    "scripts/local/final_gate_status.py",
    "scripts/local/verify_final_head_merge_command.py",
    "scripts/local/check_persistent_mutation_guard.py",
]

REQUIRED_DOCS = [
    "docs/real_claude_executor_readiness_gate.md",
    "docs/temp_worktree_execution_v1_design.md",
]

# Checklist items that MUST appear in the readiness gate doc.
# Each entry is a string that must appear in the doc (case-insensitive).
# We check the normalized doc (strip backticks, lowercased).
REQUIRED_CHECKLIST_ITEMS: list[str] = [
    # Item: explicit human approval
    "explicit human",
    # Item: interactive TTY required
    "interactive tty",
    # Item: no shell=True constraint
    "shell=true",
    # Item: Path.resolve canonical path checks
    "path.resolve",
    # Item: no package install constraint
    "package install",
    # Item: no git push constraint
    "git push",
    # Item: no gh pr create constraint
    "gh pr create",
    # Item: no gh pr merge constraint
    "gh pr merge",
    # Item: no unattended execution constraint
    "unattended execution",
]

# Marker that would indicate real Claude implementation is present.
# These are specific patterns that indicate REAL execution (subprocess, import),
# NOT the disabled skeleton mode-guard code itself.
REAL_CLAUDE_IMPLEMENTATION_MARKERS = [
    # Real subprocess invocation of claude binary
    "subprocess.run.*claude",
    # Direct LLM client imports (any of these = active implementation)
    "import claude",
    "from claude",
    "import anthropic",
    "from anthropic",
    "import openai",
    "from openai",
]

# ---------------------------------------------------------------------------
# File existence check
# ---------------------------------------------------------------------------

def file_exists(relative_path: str) -> tuple[bool, str]:
    """Return (exists, abs_path)."""
    abs_path = str((REPO_ROOT / relative_path).resolve())
    return (REPO_ROOT / relative_path).is_file(), abs_path


def check_all_files() -> dict[str, bool]:
    """Check all required files exist."""
    results = {}
    for rel_path in REQUIRED_SCRIPTS + REQUIRED_DOCS:
        exists, _ = file_exists(rel_path)
        results[rel_path] = exists
    return results


# ---------------------------------------------------------------------------
# Git status check
# ---------------------------------------------------------------------------

def git_status_clean() -> tuple[bool, str]:
    """Check if main repo has no staged/unstaged changes. Returns (clean, output)."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True, text=True, timeout=10
    )
    lines = [l for l in result.stdout.strip().splitlines() if l.startswith("?? ")]
    non_untracked = [l for l in result.stdout.strip().splitlines() if not l.startswith("?? ")]
    clean = len(non_untracked) == 0
    return clean, result.stdout.strip()


# ---------------------------------------------------------------------------
# Source inspection
# ---------------------------------------------------------------------------

def check_run_temp_worktree_execution_source() -> dict:
    """
    Inspect run_temp_worktree_execution.py for:
    1. Real Claude implementation markers
    2. Non-mock mode blocking

    If the harness file is missing, returns has_blocking=False, implementation_found=None.
    """
    source_path = REPO_ROOT / "scripts/local/run_temp_worktree_execution.py"
    if not source_path.is_file():
        return {
            "implementation_found": None,
            "has_blocking": False,
            "file_missing": True,
        }

    content = source_path.read_text(encoding="utf-8")

    # Check for real Claude implementation markers
    implementation_found = None
    for marker in REAL_CLAUDE_IMPLEMENTATION_MARKERS:
        # Exclude false positives: comments and docstrings
        for line_no, line in enumerate(content.splitlines(), 1):
            if marker in line:
                stripped = line.strip()
                # Skip comments
                if stripped.startswith("#"):
                    continue
                # Skip docstring lines
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                implementation_found = (marker, line_no)
                break
        if implementation_found:
            break

    # Check that non-mock modes are blocked.
    # Look for: if exec_mode != "mock": ... return HOLD_EXECUTOR_NOT_ALLOWED
    # OR: if exec_mode in {"real", ...}: ... return HOLD_EXECUTOR_NOT_ALLOWED
    blocking_pattern_old = re.compile(
        r'if\s+exec_mode\s*!=\s*["\']mock["\'].*?HOLD_EXECUTOR_NOT_ALLOWED',
        re.DOTALL
    )
    blocking_pattern_new = re.compile(
        r'if\s+exec_mode\s+in\s+.*?unsupported.*?HOLD_EXECUTOR_NOT_ALLOWED',
        re.DOTALL
    )
    has_blocking = bool(blocking_pattern_old.search(content) or blocking_pattern_new.search(content))

    return {
        "implementation_found": implementation_found,
        "has_blocking": has_blocking,
    }


# ---------------------------------------------------------------------------
# Checklist item verification
# ---------------------------------------------------------------------------

def _normalize_doc(text: str) -> str:
    """Normalize doc for checklist matching: strip backticks, lower case."""
    # Remove markdown backticks (both single and double)
    text = re.sub(r'`+', '', text)
    # Lower case
    text = text.lower()
    return text


def check_readiness_gate_checklist(doc_content: str) -> dict:
    """
    Check that the readiness gate doc contains all required checklist items.
    Each item is a string that must appear in the normalized doc.
    Returns dict with found items and missing items.
    """
    normalized = _normalize_doc(doc_content)

    found = {}
    missing = []

    for item in REQUIRED_CHECKLIST_ITEMS:
        item_normalized = item.lower()
        if item_normalized in normalized:
            found[item] = True
        else:
            missing.append(item)
            found[item] = False

    return {"found": found, "missing": missing}


# ---------------------------------------------------------------------------
# Main check
# ---------------------------------------------------------------------------

def run_checks() -> dict:
    """
    Run all readiness checks and return the result dict.
    """
    checks = {}

    # 1. File existence checks
    file_results = check_all_files()
    checks["required_files"] = file_results
    missing_files = [rel for rel, exists in file_results.items() if not exists]

    # 2. Git status
    clean, git_output = git_status_clean()
    checks["git_status_clean"] = clean
    checks["git_status_output"] = git_output

    # 3. Source inspection
    source_check = check_run_temp_worktree_execution_source()
    checks["source"] = source_check

    # 4. Read the readiness gate doc
    gate_doc_path = REPO_ROOT / "docs/real_claude_executor_readiness_gate.md"
    if gate_doc_path.is_file():
        doc_content = gate_doc_path.read_text(encoding="utf-8")
    else:
        doc_content = ""

    # 5. Checklist verification
    checklist = check_readiness_gate_checklist(doc_content)
    checks["checklist"] = checklist

    # ---------------------------------------------------------------------------
    # Determine overall status
    # ---------------------------------------------------------------------------

    status = "READY_TO_IMPLEMENT_REAL_EXECUTOR"
    missing: list[str] = []
    recommendation = (
        "All prerequisites satisfied. AED may begin real Claude executor "
        "implementation in a future PR."
    )

    # Check missing files
    if missing_files:
        if "docs/real_claude_executor_readiness_gate.md" in missing_files:
            status = "HOLD_DESIGN_DOC_MISSING"
            recommendation = "Hold: real_claude_executor_readiness_gate.md is missing."
            missing = ["HOLD_DESIGN_DOC_MISSING"]
        elif "scripts/local/run_temp_worktree_execution.py" in missing_files:
            status = "HOLD_MOCK_HARNESS_MISSING"
            recommendation = "Hold: run_temp_worktree_execution.py is missing."
            missing = ["HOLD_MOCK_HARNESS_MISSING"]
        else:
            status = "HOLD_MISSING_SCRIPT"
            recommendation = f"Hold: missing required files: {missing_files}"
            missing = [f"HOLD_MISSING_SCRIPT: {missing_files}"]

    # Check git status
    elif not clean:
        status = "HOLD_MAIN_DIRTY"
        recommendation = "Hold: main repo has staged or unstaged changes."
        missing = ["HOLD_MAIN_DIRTY"]

    # Check real Claude implementation markers
    elif source_check["implementation_found"] is not None:
        marker, line_no = source_check["implementation_found"]
        status = "HOLD_IMPLEMENTATION_FOUND"
        recommendation = (
            f"Hold: real Claude implementation marker '{marker}' found at "
            f"line {line_no} in run_temp_worktree_execution.py."
        )
        missing = [f"HOLD_IMPLEMENTATION_FOUND: {marker} at line {line_no}"]

    # Check non-mock blocking
    elif not source_check["has_blocking"]:
        status = "HOLD_MOCK_BLOCKING_MISSING"
        recommendation = (
            "Hold: run_temp_worktree_execution.py does not block non-mock modes."
        )
        missing = ["HOLD_MOCK_BLOCKING_MISSING"]

    # Check missing checklist items
    elif checklist["missing"]:
        status = "HOLD_READINESS_ITEM_MISSING"
        recommendation = (
            f"Hold: readiness gate doc is missing required checklist items: "
            f"{checklist['missing']}"
        )
        missing = [f"HOLD_READINESS_ITEM_MISSING: {checklist['missing']}"]

    # All checks passed
    else:
        status = "READY_TO_IMPLEMENT_REAL_EXECUTOR"
        recommendation = (
            "All prerequisites satisfied. AED may begin real Claude executor "
            "implementation in a future PR. Note: real_executor_allowed remains "
            "false — this verifier only authorizes starting an implementation PR."
        )
        missing = []

    result = {
        "status": status,
        "checks": checks,
        "missing": missing,
        "recommendation": recommendation,
        "real_executor_allowed": False,  # Always False — verifier only authorizes implementation PR
    }

    return result


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_json(result: dict, output_path: str) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


def write_md(result: dict, output_path: str) -> None:
    lines = [
        "# Real Claude Executor Readiness Check",
        "",
        f"**Status**: `{result['status']}`",
        f"**real_executor_allowed**: `{result['real_executor_allowed']}`",
        "",
        "## Checks",
        "",
    ]

    checks = result.get("checks", {})

    # File existence
    lines.append("### Required Files")
    files = checks.get("required_files", {})
    for rel_path, exists in sorted(files.items()):
        icon = "✓" if exists else "✗"
        lines.append(f"- {icon} `{rel_path}`")
    lines.append("")

    # Git status
    lines.append("### Git Status")
    clean = checks.get("git_status_clean", False)
    lines.append(f"- {'✓ Clean' if clean else '✗ Dirty'}: `{checks.get('git_status_output', '')}`")
    lines.append("")

    # Source check
    lines.append("### Source Inspection (run_temp_worktree_execution.py)")
    source = checks.get("source", {})
    impl = source.get("implementation_found")
    if impl:
        marker, line_no = impl
        lines.append(f"- ✗ Real Claude implementation marker found: `{marker}` at line {line_no}")
    else:
        lines.append("- ✓ No real Claude implementation markers found")
    blocking = source.get("has_blocking", False)
    lines.append(f"- {'✓' if blocking else '✗'} Non-mock mode blocking: `{blocking}`")
    lines.append("")

    # Checklist
    lines.append("### Readiness Gate Checklist Items")
    checklist = checks.get("checklist", {})
    found = checklist.get("found", {})
    for item in REQUIRED_CHECKLIST_ITEMS:
        is_found = found.get(item, False)
        icon = "✓" if is_found else "✗"
        lines.append(f"- {icon} {item}")
    lines.append("")

    # Missing
    missing = result.get("missing", [])
    if missing:
        lines.append("## Missing Items")
        for m in missing:
            lines.append(f"- ✗ `{m}`")
        lines.append("")

    lines.extend([
        "## Recommendation",
        "",
        result.get("recommendation", ""),
        "",
    ])

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Real Claude executor readiness checker. "
                    "No execution, no Claude invocation, no state changes."
    )
    parser.add_argument(
        "--output-json", required=True,
        help="Path to write result JSON"
    )
    parser.add_argument(
        "--output-md", required=True,
        help="Path to write result Markdown"
    )

    args = parser.parse_args()

    result = run_checks()

    write_json(result, args.output_json)
    write_md(result, args.output_md)

    print(f"Status: {result['status']}")
    print(f"Output: {args.output_json}")
    print(f"Markdown: {args.output_md}")

    return 0


if __name__ == "__main__":
    sys.exit(main())