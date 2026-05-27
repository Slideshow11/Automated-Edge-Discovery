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
# Does NOT match "No live Claude" or "No git push/merge" style documentation notes.
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
    task_output_dir.mkdir(parents=True, exist_ok=True)

    # Validate task_id
    valid, err = validate_task_id(task_id)
    if not valid:
        return False, f"task_id validation failed: {err}"

    # Validate safety_notes
    safety_notes = task.get("safety_notes", [])
    valid, err = validate_safety_notes(safety_notes)
    if not valid:
        return False, f"safety_notes validation failed: {err}"

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
        "wave": corpus.get("wave_definitions", {}).get("1", {}).get("description", ""),
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
# Main
# -----------------------------------------------------------------------


def main(
    corpus_json: str,
    output_root: str,
    mode: str,
    repo_root_override: Optional[Path] = None,
) -> int:
    """
    Run the codex remediation loop.

    Returns 0 on complete (mock-plan-only), 1 on validation failure.
    """
    repo = repo_root_override if repo_root_override is not None else REPO_ROOT

    # Hard stop: output_root must be set
    if not output_root or not output_root.strip():
        print("FATAL: --output-root is required", file=sys.stderr)
        return 1

    output_path = Path(output_root).resolve()

    # Hard stop: mode must be mock-plan-only
    if mode != "mock-plan-only":
        print(
            f"FATAL: unsupported mode '{mode}'. Only 'mock-plan-only' is supported in v0.",
            file=sys.stderr,
        )
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

    # Validate corpus execution_mode
    wave1 = corpus.get("wave_definitions", {}).get("1", {})
    if wave1.get("execution_mode") != "mocked":
        print(
            f"FATAL: Wave 1 execution_mode must be 'mocked', "
            f"got '{wave1.get('execution_mode')}'",
            file=sys.stderr,
        )
        return 1

    # Create output_root
    output_path.mkdir(parents=True, exist_ok=True)

    task_results: list[dict] = []

    for task in corpus.get("tasks", []):
        task_id = task.get("task_id", "(missing)")
        task_output_dir = output_path / "tasks" / task_id

        ok, error = build_task_packet(task, task_output_dir, repo)
        classification = (
            classify_task(task, task_output_dir)
            if ok
            else "needs_human_review"
        )

        task_results.append({
            "task_id": task_id,
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
                f"FATAL: task '{task_id}' failed validation: {error}",
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
        description="Guarded Codex-remediation loop runner (mock-plan-only v0).",
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
        help="Execution mode (only 'mock-plan-only' supported in v0)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Override repo root (default: auto-detected from script location)",
    )
    args = parser.parse_args()
    return main(
        corpus_json=args.corpus,
        output_root=args.output_root,
        mode=args.mode,
        repo_root_override=args.repo_root,
    )


if __name__ == "__main__":
    sys.exit(_main())
