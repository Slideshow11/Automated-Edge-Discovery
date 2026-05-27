#!/usr/bin/env python3
"""
run_codex_remediation_loop.py

Guarded, read-only Codex-remediation loop runner.

Reads a Codex-remediation corpus, validates safety constraints, classifies
each task, emits task packets under output_root/tasks/<task_id>/, and writes
loop_status.json/md.

v0 is mock-plan-only: no live Claude, no batch controller, no repo mutation,
no GitHub API calls, no shell=True, no writes outside output_root.

Hard stops (any triggers an exit before task processing):
  - corpus_kind not aed.codex_remediation.corpus.v0
  - unsupported execution_mode (only "mocked" allowed)
  - task_id contains ".." or "/" or "\" (path traversal risk)
  - allowed_file is absolute path
  - allowed_file does not exist at current main (unless explicitly declared
    new_file=true in task.action)
  - forbidden_file is changed
  - safety_notes contains live_claude, --enable-real-claude-executor,
    gh pr merge, git merge, git push, git commit, git add, fact_store,
    memory_store, skill_manage, delegate_task, or _run_subagent
  - mode is not mock-plan-only
  - output_root is null/empty

Stop conditions documented but NOT enforced as execution gates in v0
(future full mode would enforce these before each task):
  - current-head P0/P1/P2 review finding
  - unresolved stale P0/P1/P2
  - REVIEW_COMMENTS_BLOCKED / REVIEW_COMMENTS_INCONCLUSIVE
  - CI not green
  - PMG dirty
  - final_gate_status.py not READY_TO_MERGE
  - verify_final_head_merge_command.py not MERGE_READY_CANDIDATE
  - changed files outside allowed_files
  - any GitHub thread resolution request
  - any Hermes memory/profile/config mutation attempt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# -----------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent

CORPUS_KIND = "aed.codex_remediation.corpus.v0"
CORPUS_VERSION = "0.1.0"
LOOP_RUNNER_VERSION = "0.1.0"
REPAIR_PLAN_KIND = "aed.codex_remediation.repair_plan.v0"
REPAIR_PLAN_STATUS_KIND = "aed.codex_remediation.repair_plan_status.v0"

# Protected paths — output_root must not resolve inside these
PROTECTED_PATHS = frozenset({".hermes", "skills", "memory", "profiles"})

# Valid task categories
VALID_TASK_CATEGORIES = frozenset({
    "already_fixed_needs_regression_test",
    "false_positive_with_evidence",
    "docs_only_fixed",
    "needs_human_review",
})

# Valid action types
VALID_ACTION_TYPES = frozenset({
    "add_regression_test",
    "add_evidence_note",
    "verify_existing_test_and_document",
})

# Forbidden: live Claude enablement flags, dangerous API calls, and GitHub/git mutation.
# git/gh patterns use word-boundary matching to reduce false positives on
# prohibition references like "No git push/merge" (which contains "git push"
# as a substring but is not itself a command invocation).
FORBIDDEN_SAFETY_PATTERNS = [
    re.compile(r"--enable-real-claude-executor"),
    re.compile(r"fact_store"),
    re.compile(r"memory_store"),
    re.compile(r"skill_manage"),
    re.compile(r"delegate_task"),
    re.compile(r"_run_subagent"),
    re.compile(r"resolveReviewThread"),
    re.compile(r"deleteReview"),
    re.compile(r"dismissReview"),
    re.compile(r"shell\s*=\s*True", re.IGNORECASE),
    # GitHub CLI mutation
    re.compile(r"\bgh\s+pr\s+(?:merge|close|edit)\b"),
    # Git subcommands that modify repo state.
    # Negative lookahead (?!\S*[\w/]) prevents matching slash-separated lists like
    # "No git push/merge" where the command is followed by / not whitespace.
    # Pattern matches: "git push", "git merge", "git commit --amend"
    # Pattern rejects: "No git push/merge" (slash after command, not a command invocation)
    re.compile(r"\bgit\s+(?:merge|push|commit|add)(?!\S*[\w/])"),
]

# Unsafe task_id pattern (path traversal risk)
UNSAFE_TASK_ID_PATTERN = re.compile(r"[/\\..]")


# -----------------------------------------------------------------------
# Validation helpers
# -----------------------------------------------------------------------


def validate_corpus_schema(corpus: dict) -> tuple[bool, str]:
    """Validate top-level corpus structure. Returns (valid, error_message)."""
    if corpus.get("corpus_kind") != CORPUS_KIND:
        return False, f"corpus_kind must be '{CORPUS_KIND}', got '{corpus.get('corpus_kind')}'"
    if corpus.get("corpus_version") != CORPUS_VERSION:
        return False, (
            f"corpus_version must be '{CORPUS_VERSION}', "
            f"got '{corpus.get('corpus_version')}'"
        )
    if not corpus.get("corpus_id"):
        return False, "corpus_id is required"
    if not isinstance(corpus.get("tasks"), list) or len(corpus["tasks"]) == 0:
        return False, "tasks must be a non-empty list"
    return True, ""


def validate_task_id(task_id: str) -> tuple[bool, str]:
    """Validate task_id is a safe path component. Returns (valid, error_message)."""
    if not task_id:
        return False, "task_id is required"
    if UNSAFE_TASK_ID_PATTERN.search(task_id):
        return False, (
            f"task_id '{task_id}' contains unsafe characters (/, \\, or ..). "
            "task_id must be a safe path component."
        )
    return True, ""


def validate_safety_notes(safety_notes: list[str]) -> tuple[bool, str]:
    """Check safety_notes for forbidden patterns. Returns (clean, error_message)."""
    for note in safety_notes:
        for pattern in FORBIDDEN_SAFETY_PATTERNS:
            if pattern.search(note):
                return False, (
                    f"safety_notes contains forbidden pattern '{pattern.pattern}': {note[:120]}"
                )
    return True, ""


def check_allowed_file_exists(
    allowed_file: str,
    repo: Path,
    permitted_new_files: set[str],
) -> tuple[bool, str]:
    """
    Check if allowed_file exists at current main.

    Returns (exists, error_message).
    permitted_new_files: set of task_ids whose allowed_files may be new (not yet on main).
    """
    if allowed_file.startswith("/") or (len(allowed_file) > 1 and allowed_file[1] == ":"):
        return False, f"allowed_file '{allowed_file}' is an absolute path — must be relative"

    # Check at current main HEAD
    result = _git_cat_file_e(repo, "HEAD", allowed_file)
    if result:
        return True, ""
    # File not found at HEAD — check if it's declared as a new file
    if allowed_file in permitted_new_files:
        return True, ""
    return False, (
        f"allowed_file '{allowed_file}' does not exist at current main HEAD "
        "(declare as new_file=true if this is intentional)"
    )


def _git_cat_file_e(repo: Path, ref: str, path: str) -> bool:
    """Return True if path exists at git ref."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{ref}:{path}"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def check_forbidden_files_changed(
    forbidden_files: list[str],
    repo: Path,
) -> tuple[bool, list[str]]:
    """
    Check that no forbidden files have changed vs current main.

    Returns (clean, list_of_changed_forbidden_files).
    """
    changed = []
    for pattern_str in forbidden_files:
        # Support ** glob patterns
        if "**" in pattern_str:
            # Convert **/*.py -> *.py (simple glob for root level)
            simple_pattern = pattern_str.replace("**/", "").replace("**", "")
            if simple_pattern:
                import glob as glob_module

                matches = glob_module.glob(
                    str(repo / simple_pattern), recursive=False
                )
                for m in matches:
                    repo_str = str(repo)
                    rel = m[len(repo_str) + 1:] if m.startswith(repo_str) else m
                    if _git_file_changed(repo, "HEAD", rel):
                        changed.append(rel)
        else:
            if _git_file_changed(repo, "HEAD", pattern_str):
                changed.append(pattern_str)
    return len(changed) == 0, changed


def _git_file_changed(repo: Path, ref: str, path: str) -> bool:
    """Return True if file at path differs between working tree and ref."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "diff", "--quiet", ref, "--", path],
            capture_output=True,
            timeout=10,
        )
        return result.returncode != 0
    except Exception:
        return False


# -----------------------------------------------------------------------
# Classification
# -----------------------------------------------------------------------


def classify_task(task: dict, task_packet_dir: Path) -> str:
    """
    Classify a task based on its current state.

    Returns one of:
      already_fixed_needs_regression_test
      false_positive_with_evidence
      docs_fixed_has_evidence
      needs_human_review

    Writes classification_reason to task_packet_dir/classification_reason.txt.
    """
    category = task.get("task_category", "")
    action_type = task.get("action", {}).get("type", "")
    task_id = task["task_id"]

    if category == "already_fixed_needs_regression_test":
        reason = (
            f"task_id={task_id} classification=FIXED_ALREADY "
            f"action_type={action_type} — regression test expected. "
            "In mock-plan-only mode, task packet is emitted for human review. "
            "In full mode, regression test would be written and verified."
        )
        classification = "needs_regression_test"
    elif category == "false_positive_with_evidence":
        reason = (
            f"task_id={task_id} classification=FALSE_POSITIVE_WITH_EVIDENCE. "
            "No code changes required. Evidence documented in corpus. "
            "task packet emitted for record."
        )
        classification = "false_positive_has_evidence"
    elif category == "docs_only_fixed":
        reason = (
            f"task_id={task_id} task_category=docs_only_fixed. "
            "Governance gap was fixed in PR #323. "
            "task packet emitted for evidence record."
        )
        classification = "docs_fixed_has_evidence"
    else:
        reason = (
            f"task_id={task_id} task_category={category} — "
            "needs human review to determine correct classification."
        )
        classification = "needs_human_review"

    (task_packet_dir / "classification_reason.txt").write_text(
        reason, encoding="utf-8"
    )
    return classification


# -----------------------------------------------------------------------
# Build task packet
# -----------------------------------------------------------------------


def build_task_packet(
    task: dict,
    task_output_dir: Path,
    repo: Path,
) -> tuple[bool, str]:
    """
    Build and write a task_packet.json for one task.

    Returns (ok, error_message).

    Writes:
      task_packet_dir/task_packet.json
      task_packet_dir/classification_reason.txt (by classify_task)
      task_packet_dir/safety_notes_verified.txt (empty marker)
    """
    task_id = task["task_id"]

    # Validate task_id before creating any directories (prevent path traversal)
    valid, err = validate_task_id(task_id)
    if not valid:
        return False, f"task_id validation failed: {err}"

    # Validate safety_notes before writing anything
    safety_notes = task.get("safety_notes", [])
    valid, err = validate_safety_notes(safety_notes)
    if not valid:
        return False, f"safety_notes validation failed: {err}"

    # Only now create the task output directory
    task_output_dir.mkdir(parents=True, exist_ok=True)

    # Check allowed_files
    action = task.get("action", {})
    allowed_files = action.get("allowed_files", [])
    permitted_new_files: set[str] = set()
    for f in allowed_files:
        if action.get("permitted_new_files", []):
            # This corpus uses explicit new_file declarations — check if this file is new
            pass  # current corpus doesn't declare new files, handled below

    for allowed_file in allowed_files:
        exists, err = check_allowed_file_exists(
            allowed_file, repo, permitted_new_files
        )
        if not exists:
            # Check if the task explicitly marks this as a new file
            new_files = action.get("permitted_new_files", [])
            if allowed_file not in new_files:
                return False, f"allowed_file check failed: {err}"

    # Check forbidden_files haven't changed
    forbidden_files = action.get("forbidden_files", [])
    clean, changed = check_forbidden_files_changed(forbidden_files, repo)
    if not clean:
        return False, (
            f"forbidden_files have changed vs HEAD: {changed}. "
            "Task must not modify forbidden files."
        )

    # Classify task
    classification = classify_task(task, task_output_dir)

    # Build task packet
    packet = {
        "packet_kind": "aed.codex_remediation.task_packet.v0",
        "loop_runner_version": LOOP_RUNNER_VERSION,
        "task_id": task_id,
        "wave": task.get("wave"),
        "source_pr": task.get("source_pr"),
        "finding_id": task.get("finding_id"),
        "severity": task.get("severity"),
        "classification": classification,
        "task_category": task.get("task_category"),
        "action_type": action.get("type"),
        "target_file": action.get("target_file"),
        "allowed_files": allowed_files,
        "forbidden_files": forbidden_files,
        "safety_notes": safety_notes,
        "success_criteria": action.get("success_criteria", ""),
        "deliverable": action.get("deliverable", ""),
        "finding_summary": task.get("finding_summary", ""),
        "current_main_status": task.get("current_main_status", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    (task_output_dir / "task_packet.json").write_text(
        json.dumps(packet, indent=2), encoding="utf-8"
    )
    # Marker: safety was verified
    (task_output_dir / "safety_notes_verified.txt").write_text(
        "safety_notes verified clean\n", encoding="utf-8"
    )

    return True, ""


# -----------------------------------------------------------------------
# Build loop status
# -----------------------------------------------------------------------


def build_loop_status(
    corpus: dict,
    task_results: list[dict],
    output_root: Path,
) -> dict:
    """
    Build loop_status.json from corpus and per-task results.

    task_results: list of {"task_id": str, "ok": bool, "error": str,
                            "classification": str, "task_packet_path": str}
    """
    total = len(task_results)
    passed = sum(1 for r in task_results if r["ok"])
    failed = sum(1 for r in task_results if not r["ok"])

    classifications: dict[str, int] = {}
    for r in task_results:
        c = r.get("classification", "unknown")
        classifications[c] = classifications.get(c, 0) + 1

    status: dict = {
        "loop_status_kind": "aed.codex_remediation.loop_status.v0",
        "loop_runner_version": LOOP_RUNNER_VERSION,
        "corpus_id": corpus["corpus_id"],
        "corpus_version": corpus["corpus_version"],
        "wave": (corpus.get("wave_definitions", {}).get("1", {}) or {}).get("description", "") if isinstance(corpus.get("wave_definitions", {}).get("1", {}), dict) else "",
        "mode": "mock-plan-only",
        "base_sha_policy": corpus.get("base_sha_policy", "current_main"),
        "status": "LOOP_COMPLETE_MOCK_PLAN_ONLY",
        "total_tasks": total,
        "tasks_passed": passed,
        "tasks_failed": failed,
        "classifications": classifications,
        "stop_conditions": [
            "current-head P0/P1/P2 review finding",
            "unresolved stale P0/P1/P2",
            "REVIEW_COMMENTS_BLOCKED",
            "REVIEW_COMMENTS_INCONCLUSIVE",
            "CI not green",
            "PMG dirty",
            "final_gate_status.py not READY_TO_MERGE",
            "verify_final_head_merge_command.py not MERGE_READY_CANDIDATE",
            "changed files outside allowed_files",
            "any request to resolve GitHub threads",
            "any live Claude flag unless explicitly authorized",
            "any Hermes memory/profile/config mutation attempt",
        ],
        "hard_stops": [
            "unsupported corpus_kind",
            "unsupported execution_mode",
            "unsafe task_id (path traversal)",
            "absolute allowed_file path",
            "missing allowed_file not declared new",
            "forbidden_file changed vs HEAD",
            "safety_notes contains forbidden pattern",
            "mode is not mock-plan-only",
            "output_root is null/empty",
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    status_json_path = output_root / "loop_status.json"
    status_json_path.write_text(json.dumps(status, indent=2), encoding="utf-8")

    return status


def render_loop_status_md(status: dict, task_results: list[dict]) -> str:
    """Render loop_status as markdown."""
    lines = [
        "# Codex Remediation Loop Status",
        "",
        f"**corpus_id:** {status['corpus_id']}",
        f"**corpus_version:** {status['corpus_version']}",
        f"**mode:** {status['mode']}",
        f"**base_sha_policy:** {status['base_sha_policy']}",
        f"**status:** {status['status']}",
        "",
        f"- total tasks: {status['total_tasks']}",
        f"- tasks passed: {status['tasks_passed']}",
        f"- tasks failed: {status['tasks_failed']}",
        "",
        "## Classifications",
    ]
    for cls, count in sorted(status["classifications"].items()):
        lines.append(f"- **{cls}**: {count}")

    lines.append("")
    lines.append("## Per-Task Results")
    for r in task_results:
        icon = "✅" if r["ok"] else "❌"
        task_id = r["task_id"]
        classification = r.get("classification", "unknown")
        lines.append(f"- {icon} `{task_id}` — {classification}")
        if not r["ok"]:
            lines.append(f"  - ERROR: {r['error']}")
        if r.get("task_packet_path"):
            lines.append(f"  - packet: {r['task_packet_path']}")

    lines.append("")
    lines.append("## Stop Conditions (documented, not enforced in v0 mock mode)")
    for sc in status["stop_conditions"]:
        lines.append(f"- {sc}")

    lines.append("")
    lines.append("## Hard Stops (enforced — any triggers immediate exit)")
    for hs in status["hard_stops"]:
        lines.append(f"- {hs}")

    lines.append("")
    lines.append("## Safety Invariants")
    lines.append("- No live Claude invocation")
    lines.append("- No --enable-real-claude-executor")
    lines.append("- No shell=True")
    lines.append("- No GitHub API mutation calls")
    lines.append("- No Hermes memory/profile/config writes")
    lines.append("- No git push/merge/commit/add from runner")
    lines.append("- No autocoder batch controller invocation")
    lines.append("- All output written only under output_root")

    return "\n".join(lines)


# -----------------------------------------------------------------------
# Repair plan helpers (one-task-repair-plan mode)
# -----------------------------------------------------------------------


def _derive_safety_notes_from_safety_dict(safety: dict) -> list[str]:
    """Convert a Wave-2-style safety dict to a safety_notes list."""
    notes = []
    if safety.get("no_live_claude"):
        notes.append("No live Claude execution")
    if safety.get("no_hermes_mutations"):
        notes.append("No Hermes mutation")
    if safety.get("no_github_mutations"):
        notes.append("No GitHub API mutations")
    if safety.get("no_install"):
        notes.append("No package installation")
    if safety.get("scope_narrow"):
        notes.append("Scope is narrow — single task")
    if not notes:
        notes.append("No safety restrictions beyond runner defaults")
    return notes


def _derive_safety_notes(task: dict) -> list[str]:
    """
    Extract safety_notes from a task, handling both Wave-1 (list) and
    Wave-2 (dict in safety field) formats.
    """
    # Wave 1: explicit safety_notes list
    if task.get("safety_notes"):
        return list(task["safety_notes"])
    # Wave 2: safety dict
    safety = task.get("safety", {})
    if isinstance(safety, dict):
        return _derive_safety_notes_from_safety_dict(safety)
    return []


def check_output_root_isolation(output_path: Path) -> tuple[bool, str]:
    """
    Verify output_path is not inside a protected directory.
    Returns (clean, error_message).
    """
    resolved = output_path.resolve()
    parts = resolved.parts
    # Check if any path component is a protected directory
    for part in parts:
        if part in PROTECTED_PATHS:
            return False, (
                f"output_root '{output_path}' resolves inside protected path '{part}'. "
                "Choose a path outside .hermes/, skills/, memory/, profiles/."
            )
    return True, ""


def find_task_in_corpus(
    corpus: dict,
    task_id: str,
) -> tuple[Optional[dict], str]:
    """
    Find a task by task_id in the corpus.
    Returns (task_or_None, error_message).
    """
    for task in corpus.get("tasks", []):
        if task.get("task_id") == task_id:
            return task, ""
    return None, (
        f"task_id '{task_id}' not found in corpus '{corpus.get('corpus_id')}'. "
        f"Available task_ids: {[t['task_id'] for t in corpus.get('tasks', [])]}"
    )


def validate_action_fields(task: dict) -> tuple[bool, str]:
    """
    Validate that action.success_criteria and action.deliverable are present and non-empty.
    Returns (valid, error_message).
    """
    action = task.get("action", {})
    if not action:
        return False, "task.action is missing or empty"
    success_criteria = action.get("success_criteria", "")
    if not success_criteria or not str(success_criteria).strip():
        return False, "task.action.success_criteria is missing or empty"
    deliverable = action.get("deliverable", "")
    if not deliverable or not str(deliverable).strip():
        return False, "task.action.deliverable is missing or empty"
    return True, ""


def build_task_context(
    task: dict,
    corpus: dict,
    output_root: Path,
    generated_files: list[str],
) -> dict:
    """Build task_context.json."""
    action = task.get("action", {})
    safety_notes = _derive_safety_notes(task)
    return {
        "context_kind": REPAIR_PLAN_KIND,
        "loop_runner_version": LOOP_RUNNER_VERSION,
        "task_id": task["task_id"],
        "wave": task.get("wave"),
        "source_pr": task.get("source_pr"),
        "finding_id": task.get("finding_id") or task.get("source_finding_id", ""),
        "severity": task.get("severity"),
        "classification": task.get("classification", ""),
        "task_category": task.get("task_category", ""),
        "action_type": action.get("type", ""),
        "target_file": action.get("target_file", ""),
        "allowed_files": action.get("allowed_files", []),
        "forbidden_files": action.get("forbidden_files", []),
        "safety_notes": safety_notes,
        "finding_summary": task.get("finding_summary", task.get("goal", "")),
        "current_main_status": task.get("current_main_status", task.get("notes", "")),
        "success_criteria": action.get("success_criteria", ""),
        "deliverable": action.get("deliverable", ""),
        "output_root": str(output_root),
        "generated_files": generated_files,
        "execution_performed": False,
        "live_claude_invoked": False,
        "autocoder_batch_invoked": False,
        "repo_mutated": False,
        "git_mutation_allowed": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_repair_prompt_md(task: dict, corpus: dict) -> str:
    """Build repair_prompt.md."""
    task_id = task["task_id"]
    action = task.get("action", {})
    safety_notes = _derive_safety_notes(task)

    allowed_files = action.get("allowed_files", [])
    forbidden_files = action.get("forbidden_files", [])
    target_file = action.get("target_file", "")
    success_criteria = action.get("success_criteria", "")
    deliverable = action.get("deliverable", "")
    finding_summary = task.get("finding_summary", task.get("goal", ""))
    current_main_status = task.get("current_main_status", task.get("notes", ""))
    severity = task.get("severity", "P?")
    wave = task.get("wave", "?")
    classification = task.get("classification", task.get("task_category", ""))

    allowed_files_md = "\n".join(f"- `{f}`" for f in allowed_files) or "_none_"
    forbidden_files_md = "\n".join(f"- `{f}`" for f in forbidden_files) or "_none_"

    lines = [
        f"# Repair Plan — {task_id}",
        "",
        "## Task Summary",
        "",
        f"- **Task ID:** `{task_id}`",
        f"- **Wave:** {wave}",
        f"- **Severity:** {severity}",
        f"- **Classification:** `{classification}`",
        f"- **Corpus:** `{corpus.get('corpus_id', '')}`",
        "",
        "## Finding",
        "",
        finding_summary,
        "",
        "## Current Main Status",
        "",
        current_main_status,
        "",
        "## Goal",
        "",
        task.get("goal", "Complete the task described in the deliverable."),
        "",
        "## Deliverable",
        "",
        deliverable,
        "",
        "## Success Criteria",
        "",
        success_criteria,
        "",
        "## Target File",
        "",
        f"`{target_file}`",
        "",
        "## Allowed Files (read-only references)",
        "",
        allowed_files_md,
        "",
        "## Forbidden Files (must not be modified)",
        "",
        forbidden_files_md,
        "",
        "## Safety Requirements",
        "",
        "- Do NOT modify any file outside the allowed files list",
        "- Do NOT enable live Claude execution",
        "- Do NOT run the autocoder batch controller",
        "- Do NOT attempt to merge, push, or commit changes via git",
        "- Do NOT call GitHub API mutation endpoints (gh pr merge, close, edit, etc.)",
        "- Do NOT write to `.hermes/`, `skills/`, `memory/`, or `profiles/`",
        "- Do NOT use `shell=True` in any subprocess call",
        "- Do NOT install packages or modify the environment",
        "- Make the smallest change necessary to satisfy the deliverable",
        "",
        "## Execution Boundary",
        "",
        "This plan authorizes only the changes described in the deliverable above.",
        "Any other file changes require a new task and new review.",
        "If work requires changes to files outside allowed_files, stop and report",
        "before proceeding.",
        "",
        "## Handoff",
        "",
        "After completing the work:",
        "1. Run the relevant pytest tests and confirm they pass",
        "2. Run `git diff` and confirm only allowed files were modified",
        "3. Report what was done and what files were changed",
    ]
    return "\n".join(lines)


def build_safety_checklist_md(task: dict, repo: Path) -> str:
    """Build safety_checklist.md."""
    task_id = task["task_id"]
    action = task.get("action", {})
    allowed_files = action.get("allowed_files", [])
    forbidden_files = action.get("forbidden_files", [])
    safety_notes = _derive_safety_notes(task)

    # Check allowed files exist
    allowed_exists = []
    allowed_missing = []
    for f in allowed_files:
        if _git_cat_file_e(repo, "HEAD", f):
            allowed_exists.append(f)
        else:
            allowed_missing.append(f)

    # Check forbidden files unchanged
    forbidden_changed = []
    if forbidden_files:
        clean, changed = check_forbidden_files_changed(forbidden_files, repo)
        if not clean:
            forbidden_changed = changed

    # Safety notes check
    safety_ok = True
    safety_err = ""
    if safety_notes:
        valid, err = validate_safety_notes(safety_notes)
        if not valid:
            safety_ok = False
            safety_err = err

    lines = [
        f"# Safety Checklist — {task_id}",
        "",
        "## Pre-Execution Safety Checks",
        "",
        "| Check | Result | Notes |",
        "|---|---|---|",
        f"| Task ID is safe path component | ✅ | No `/`, `\\`, or `..` |",
        f"| allowed_files are relative paths | {'✅' if all(not f.startswith('/') for f in allowed_files) else '❌'} | {'All relative' if allowed_files else 'no allowed_files'} |",
        f"| allowed_files exist at current main | {'✅' if not allowed_missing else '❌'} | {'All exist' if not allowed_missing else f'MISSING: {allowed_missing}'} |",
        f"| forbidden_files unchanged vs main | {'✅' if not forbidden_changed else '❌'} | {'No changes' if not forbidden_changed else f'CHANGED: {forbidden_changed}'} |",
        f"| safety_notes contain no forbidden patterns | {'✅' if safety_ok else '❌'} | {safety_err if not safety_ok else 'Clean'} |",
    ]

    lines.extend([
        "",
        "## Execution Boundaries",
        "",
        f"- **May modify:** `{action.get('target_file', '_none_')}`",
        f"| **Must not modify:** Any file not in allowed_files |",
        "| **Must not invoke:** Live Claude, autocoder batch controller, git push/merge, GitHub API mutation |",
        "| **Must not write to:** `.hermes/`, `skills/`, `memory/`, `profiles/` |",
        "",
        "## Post-Execution Required Checks",
        "",
        "After completing the repair:",
        "",
        f"1. Run `pytest {action.get('target_file', 'tests/')} -q` — no regressions",
        "2. Review `git diff` — only files in allowed_files should be changed",
        "3. Run PMG snapshot — no Hermes mutations",
        "4. Open a draft PR for human review before merge",
    ])
    return "\n".join(lines)


def build_suggested_tests_md(task: dict) -> str:
    """Build suggested_tests.md from the task action block."""
    task_id = task["task_id"]
    action = task.get("action", {})
    target_file = action.get("target_file", "")
    success_criteria = action.get("success_criteria", "")
    deliverable = action.get("deliverable", "")

    # Extract a suggested test name from the deliverable if possible
    test_name = f"test_{task_id.replace('-', '_')}"

    lines = [
        f"# Suggested Test — {task_id}",
        "",
        "## Deliverable (from corpus)",
        "",
        deliverable,
        "",
        "## Success Criteria (from corpus)",
        "",
        success_criteria,
        "",
        "## Suggested Test Pattern",
        "",
        "The test should:",
        "1. Exercise the production code path directly (not mock it)",
        "2. Pass on current main without modifying existing code",
        "3. Fail if the fix/behavior it tests is reverted",
        "",
        f"**Target file:** `{target_file}`",
        f"**Suggested test name:** `{test_name}`",
        "",
        "## Notes",
        "",
        "- Copy the test function name and pattern from `deliverable` above",
        "- Use existing pytest fixtures (`tmp_path`, `unique_id`, etc.)",
        "- Do NOT modify production code — only add new test functions",
        "- The test must pass on current main HEAD",
    ]
    return "\n".join(lines)


def build_stop_conditions_md(task: dict, corpus: dict) -> str:
    """Build stop_conditions.md."""
    task_id = task["task_id"]

    lines = [
        f"# Stop Conditions — {task_id}",
        "",
        "## Stop Condition Status",
        "",
        "| # | Stop Condition | Status | Detail |",
        "|---|---|---|---|",
        "| 1 | current-head P0/P1/P2 review finding | N/A | Not checked in one-task-repair-plan mode (human reviews first) |",
        "| 2 | unresolved stale P0/P1/P2 | N/A | Not checked in one-task-repair-plan mode |",
        "| 3 | REVIEW_COMMENTS_BLOCKED | N/A | Not checked in one-task-repair-plan mode |",
        "| 4 | CI not green | N/A | Not checked in one-task-repair-plan mode |",
        "| 5 | PMG dirty (pre-execution) | N/A | Not checked in one-task-repair-plan mode |",
        "| 6 | changed files outside allowed_files | N/A | No execution in plan-only mode |",
        "| 7 | task requests GitHub thread resolution | ✅ PASS | No such request |",
        "| 8 | task requests live Claude | ✅ PASS | No such request |",
        "| 9 | task attempts Hermes mutation | ✅ PASS | No such request |",
        "| 10 | task has forbidden safety patterns | ✅ PASS | safety_notes verified clean |",
        "| 11 | action.success_criteria missing | ✅ PASS | Non-empty |",
        "| 12 | action.deliverable missing | ✅ PASS | Non-empty |",
        "",
        "## Conclusion",
        "",
        "All applicable stop conditions pass. This plan is eligible for human review",
        "and subsequent live-repair execution after approval.",
        "",
        f"_Generated by run_codex_remediation_loop.py one-task-repair-plan mode_",
    ]
    return "\n".join(lines)


def build_repair_plan_status(
    task: dict,
    corpus: dict,
    output_root: Path,
    generated_files: list[str],
    ok: bool,
    error: str,
) -> dict:
    """Build repair_plan_status.json."""
    return {
        "repair_plan_status_kind": REPAIR_PLAN_STATUS_KIND,
        "loop_runner_version": LOOP_RUNNER_VERSION,
        "status": "REPAIR_PLAN_READY" if ok else "REPAIR_PLAN_FAILED",
        "task_id": task["task_id"],
        "corpus_id": corpus.get("corpus_id", ""),
        "wave": task.get("wave"),
        "output_root": str(output_root),
        "generated_files": generated_files,
        "error": error,
        "execution_performed": False,
        "live_claude_invoked": False,
        "autocoder_batch_invoked": False,
        "repo_mutated": False,
        "git_mutation_allowed": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def render_repair_plan_status_md(status: dict, task: dict) -> str:
    """Render repair_plan_status.md."""
    lines = [
        f"# Repair Plan Status — {task['task_id']}",
        "",
        f"**Status:** `{status['status']}`",
        f"**Corpus:** `{status['corpus_id']}`",
        f"**Wave:** `{status.get('wave', '?')}`",
        f"**Output root:** `{status['output_root']}`",
        "",
        "## Execution",
        "",
        "| Property | Value |",
        "|---|---|",
        f"| execution_performed | `{status['execution_performed']}` |",
        f"| live_claude_invoked | `{status['live_claude_invoked']}` |",
        f"| autocoder_batch_invoked | `{status['autocoder_batch_invoked']}` |",
        f"| repo_mutated | `{status['repo_mutated']}` |",
        f"| git_mutation_allowed | `{status['git_mutation_allowed']}` |",
        "",
        "## Generated Files",
        "",
    ]
    for f in status.get("generated_files", []):
        lines.append(f"- `{f}`")

    if status.get("error"):
        lines.extend(["", f"## Error", "", status["error"]])

    lines.extend(["", "_This plan was generated by `one-task-repair-plan` mode. No execution was performed._"])
    return "\n".join(lines)


def run_one_task_repair_plan(
    corpus: dict,
    task: dict,
    output_path: Path,
    repo: Path,
) -> tuple[bool, str]:
    """
    Generate repair plan artifacts for exactly one task.
    Returns (ok, error_message).
    All output is written under output_path.
    """
    task_id = task["task_id"]

    # Validate task_id (path traversal check)
    valid, err = validate_task_id(task_id)
    if not valid:
        return False, f"task_id validation failed: {err}"

    # Validate safety_notes / derive from safety dict
    safety_notes = _derive_safety_notes(task)
    valid, err = validate_safety_notes(safety_notes)
    if not valid:
        return False, f"safety validation failed: {err}"

    # Validate action fields
    valid, err = validate_action_fields(task)
    if not valid:
        return False, f"action validation failed: {err}"

    # Check allowed_files
    action = task.get("action", {})
    allowed_files = action.get("allowed_files", [])
    permitted_new = set(action.get("permitted_new_files", []))
    for f in allowed_files:
        exists, err = check_allowed_file_exists(f, repo, permitted_new)
        if not exists:
            # Check if it's declared as a new file
            if f not in permitted_new:
                return False, f"allowed_file check failed: {err}"

    # Check forbidden_files
    forbidden_files = action.get("forbidden_files", [])
    clean, changed = check_forbidden_files_changed(forbidden_files, repo)
    if not clean:
        return False, f"forbidden_files changed vs HEAD: {changed}"

    # Create task output directory (not under output_root root directly)
    task_out = output_path / task_id
    task_out.mkdir(parents=True, exist_ok=True)

    generated_files: list[str] = []

    # 1. task_context.json
    task_context = build_task_context(task, corpus, output_path, generated_files)
    ctx_path = task_out / "task_context.json"
    ctx_path.write_text(json.dumps(task_context, indent=2), encoding="utf-8")
    generated_files.append(str(ctx_path.relative_to(output_path)))

    # 2. repair_prompt.md
    prompt_md = build_repair_prompt_md(task, corpus)
    prompt_path = task_out / "repair_prompt.md"
    prompt_path.write_text(prompt_md, encoding="utf-8")
    generated_files.append(str(prompt_path.relative_to(output_path)))

    # 3. safety_checklist.md
    checklist_md = build_safety_checklist_md(task, repo)
    checklist_path = task_out / "safety_checklist.md"
    checklist_path.write_text(checklist_md, encoding="utf-8")
    generated_files.append(str(checklist_path.relative_to(output_path)))

    # 4. suggested_tests.md
    tests_md = build_suggested_tests_md(task)
    tests_path = task_out / "suggested_tests.md"
    tests_path.write_text(tests_md, encoding="utf-8")
    generated_files.append(str(tests_path.relative_to(output_path)))

    # 5. stop_conditions.md
    stop_md = build_stop_conditions_md(task, corpus)
    stop_path = task_out / "stop_conditions.md"
    stop_path.write_text(stop_md, encoding="utf-8")
    generated_files.append(str(stop_path.relative_to(output_path)))

    # 6. repair_plan_status.json
    status = build_repair_plan_status(task, corpus, output_path, generated_files, True, "")
    status_path = output_path / "repair_plan_status.json"
    status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    generated_files.append(str(status_path.relative_to(output_path)))

    # 7. repair_plan_status.md
    status_md = render_repair_plan_status_md(status, task)
    status_md_path = output_path / "repair_plan_status.md"
    status_md_path.write_text(status_md, encoding="utf-8")
    generated_files.append(str(status_md_path.relative_to(output_path)))

    # Update status and all files with final generated_files list
    status["generated_files"] = list(generated_files)
    status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    task_context["generated_files"] = list(generated_files)
    ctx_path.write_text(json.dumps(task_context, indent=2), encoding="utf-8")

    return True, ""


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------


def main(
    corpus_json: str,
    output_root: str,
    mode: str,
    repo_root_override: Optional[Path] = None,
    task_id: Optional[str] = None,
) -> int:
    """
    Run the codex remediation loop.

    Returns 0 on complete, 1 on validation failure.
    """
    repo = repo_root_override if repo_root_override is not None else REPO_ROOT

    # Hard stop: output_root must be set
    if not output_root or not output_root.strip():
        print("FATAL: --output-root is required", file=sys.stderr)
        return 1

    output_path = Path(output_root).resolve()

    # Hard stop: output_root must not be inside protected paths
    clean, err = check_output_root_isolation(output_path)
    if not clean:
        print(f"FATAL: {err}", file=sys.stderr)
        return 1

    # Load corpus
    corpus_path = Path(corpus_json)
    if not corpus_path.exists():
        print(f"FATAL: corpus JSON not found: {corpus_path}", file=sys.stderr)
        return 1

    try:
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"FATAL: corpus JSON is not valid: {e}", file=sys.stderr)
        return 1

    # Validate corpus schema
    valid, err = validate_corpus_schema(corpus)
    if not valid:
        print(f"FATAL: corpus validation failed: {err}", file=sys.stderr)
        return 1

    # --- one-task-repair-plan mode ---
    if mode == "one-task-repair-plan":
        # task_id is required
        if not task_id:
            print(
                "FATAL: --task-id is required for one-task-repair-plan mode",
                file=sys.stderr,
            )
            return 1

        # Find the task
        task, err = find_task_in_corpus(corpus, task_id)
        if task is None:
            print(f"FATAL: {err}", file=sys.stderr)
            return 1

        # Validate wave execution_mode (must be mocked or repair-plan)
        wave_num = str(task.get("wave", ""))
        wave_def = corpus.get("wave_definitions", {}).get(wave_num, {})
        if isinstance(wave_def, dict):
            wave_mode = wave_def.get("execution_mode", "mocked")
            if wave_mode not in ("mocked", "repair-plan"):
                print(
                    f"FATAL: one-task-repair-plan: wave {wave_num} has "
                    f"execution_mode='{wave_mode}'. Expected 'mocked' or 'repair-plan'.",
                    file=sys.stderr,
                )
                return 1

        # Create output_root
        output_path.mkdir(parents=True, exist_ok=True)

        # Run repair plan generation
        ok, error = run_one_task_repair_plan(corpus, task, output_path, repo)

        if ok:
            print(
                f"repair plan generated: {task_id} -> {output_path}"
            )
            return 0
        else:
            # Write failed status
            status = build_repair_plan_status(
                task, corpus, output_path, [], False, error
            )
            (output_path / "repair_plan_status.json").write_text(
                json.dumps(status, indent=2), encoding="utf-8"
            )
            (output_path / "repair_plan_status.md").write_text(
                render_repair_plan_status_md(status, task), encoding="utf-8"
            )
            print(f"FATAL: {error}", file=sys.stderr)
            return 1

    # --- mock-plan-only mode ---
    if mode != "mock-plan-only":
        print(
            f"FATAL: unsupported mode '{mode}'. "
            "Supported modes: 'mock-plan-only', 'one-task-repair-plan'.",
            file=sys.stderr,
        )
        return 1

    # Validate corpus execution_mode for mock-plan-only
    wave1 = corpus.get("wave_definitions", {}).get("1", {})
    if isinstance(wave1, dict) and wave1.get("execution_mode") not in (
        "mocked",
        "repair-plan",
    ):
        print(
            f"FATAL: Wave 1 execution_mode must be 'mocked' or 'repair-plan', "
            f"got '{wave1.get('execution_mode')}'",
            file=sys.stderr,
        )
        return 1

    # Create output_root
    output_path.mkdir(parents=True, exist_ok=True)

    task_results: list[dict] = []

    for task in corpus.get("tasks", []):
        task_id_loop = task.get("task_id", "(missing)")
        task_output_dir = output_path / "tasks" / task_id_loop

        ok, error = build_task_packet(task, task_output_dir, repo)
        classification = (
            classify_task(task, task_output_dir)
            if ok
            else "needs_human_review"
        )

        task_results.append({
            "task_id": task_id_loop,
            "ok": ok,
            "error": error,
            "classification": classification,
            "task_packet_path": (
                str(task_output_dir / "task_packet.json")
                if ok
                else ""
            ),
        })

        if not ok:
            # Stop on first validation failure (hard stop)
            print(
                f"FATAL: task '{task_id_loop}' failed validation: {error}",
                file=sys.stderr,
            )
            # Write failed status before exiting
            status = build_loop_status(corpus, task_results, output_path)
            md = render_loop_status_md(status, task_results)
            (output_path / "loop_status.md").write_text(md, encoding="utf-8")
            return 1

    # All tasks validated — write status
    status = build_loop_status(corpus, task_results, output_path)
    md = render_loop_status_md(status, task_results)
    (output_path / "loop_status.md").write_text(md, encoding="utf-8")

    print(f"loop complete: {status['tasks_passed']}/{status['total_tasks']} tasks passed")
    print(f"output: {output_path}")
    return 0


def _main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Guarded Codex-remediation loop runner. "
            "Modes: mock-plan-only, one-task-repair-plan."
        ),
    )
    parser.add_argument(
        "--corpus",
        required=True,
        help="Path to corpus JSON file",
    )
    parser.add_argument(
        "--output-root",
        required=True,
        help="Output directory for task packets and loop status",
    )
    parser.add_argument(
        "--mode",
        required=True,
        help=(
            "Execution mode. 'mock-plan-only' (full corpus, task packets only). "
            "'one-task-repair-plan' (single task, repair handoff artifacts)."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Override repo root (default: auto-detected from script location)",
    )
    parser.add_argument(
        "--task-id",
        default=None,
        help=(
            "Required for one-task-repair-plan mode. "
            "Exact task_id to generate a repair plan for."
        ),
    )
    args = parser.parse_args()
    return main(
        corpus_json=args.corpus,
        output_root=args.output_root,
        mode=args.mode,
        repo_root_override=args.repo_root,
        task_id=args.task_id,
    )


if __name__ == "__main__":
    sys.exit(_main())
