#!/usr/bin/env python3
"""
run_autocoder_eval_corpus.py

Eval corpus runner for AED autocoder controller stack.

Loads corpus JSON, validates targets, builds a batch packet with run-scoped
branch names, invokes the batch controller, and emits eval_report.json/md.

No live Claude. No --enable-real-claude-executor. No shell=True.
No Hermes memory/profile writes. No push/PR/merge/commit/stage/git add.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# -----------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent
BATCH_SCRIPT = REPO_ROOT / "scripts" / "local" / "run_autocoder_batch.py"
CORPUS_SCHEMA_KIND = "aed.autocoder.corpus.v0"
CORPUS_SCHEMA_VERSION = "0.1.0"
EVAL_RUNNER_VERSION = "0.1.0"
BATCH_READY_STATUS = "BATCH_READY_FOR_HUMAN_REVIEW"
SINGLE_TASK_READY_STATUS = "SINGLE_TASK_READY_FOR_HUMAN_REVIEW"

# -----------------------------------------------------------------------
# Validation helpers
# -----------------------------------------------------------------------

VALID_TASK_PACKET_KIND = "aed.autocoder.single_task.v0"


def validate_corpus_schema(corpus: dict) -> tuple[bool, str]:
    """Validate top-level corpus structure. Returns (valid, error_message)."""
    if corpus.get("corpus_kind") != CORPUS_SCHEMA_KIND:
        return False, f"corpus_kind must be '{CORPUS_SCHEMA_KIND}'"
    if corpus.get("corpus_version") != CORPUS_SCHEMA_VERSION:
        return False, f"corpus_version must be '{CORPUS_SCHEMA_VERSION}'"
    if not corpus.get("corpus_id"):
        return False, "corpus_id is required"
    if not corpus.get("tasks"):
        return False, "tasks must be non-empty"
    return True, ""


def validate_task_packet(task: dict, index: int) -> tuple[bool, str]:
    """Validate a single task packet. Returns (valid, error_message)."""
    if task.get("packet_kind") != VALID_TASK_PACKET_KIND:
        return False, f"tasks[{index}].packet_kind must be '{VALID_TASK_PACKET_KIND}'"
    if not task.get("task_id"):
        return False, f"tasks[{index}].task_id is required"
    if not task.get("goal"):
        return False, f"tasks[{index}].goal is required"
    if task.get("execution_mode") != "mocked":
        mode = task.get("execution_mode", "")
        return False, f"tasks[{index}].execution_mode must be 'mocked', got '{mode}'"
    if not isinstance(task.get("allowed_files"), list) or len(task["allowed_files"]) == 0:
        return False, f"tasks[{index}].allowed_files must be a non-empty list"
    if not isinstance(task.get("mock_edits"), list) or len(task["mock_edits"]) == 0:
        return False, f"tasks[{index}].mock_edits must be a non-empty list"
    return True, ""


def validate_all_tasks(corpus: dict) -> tuple[bool, str]:
    """Validate all tasks in the corpus. Returns (all_valid, error_message)."""
    for i, task in enumerate(corpus.get("tasks", [])):
        valid, err = validate_task_packet(task, i)
        if not valid:
            return False, err
    return True, ""


def resolve_base_sha(policy: str, repo: Path) -> tuple[bool, str, str]:
    """
    Resolve base_sha_policy to a concrete SHA.

    policy: "current_main" or a literal SHA
    repo: Path to the repository

    Returns (ok, message, sha)
    """
    if policy == "current_main":
        # Try origin/main first (CI may not have main checked out as local branch)
        for ref in ["origin/main", "main"]:
            result = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", ref],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                sha = result.stdout.strip()
                if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
                    return True, f"resolved to {sha}", sha
        return False, f"git rev-parse main failed (tried main, origin/main)", ""
    else:
        # Treat as a literal SHA
        if len(policy) != 40 or not all(c in "0123456789abcdef" for c in policy):
            return False, f"base_sha must be a 40-char hex SHA (got '{policy[:8]}...')", ""
        result = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{policy}:README.md"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False, f"SHA '{policy[:8]}...' does not exist in repo", ""
        return True, f"literal SHA {policy}", policy


def _git_branch_exists(repo: Path, branch_name: str) -> bool:
    """Return True if the given branch already exists locally."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"refs/heads/{branch_name}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.returncode == 0


def validate_corpus_targets(corpus: dict, base_sha: str, repo: Path, skip_branch_check: bool = False) -> tuple[bool, list[str]]:
    """
    Validate all corpus target files exist at base_sha and have no .aed_plan text.
    Validate all branch names do not already exist (unless skip_branch_check=True).

    Returns:
        (all_valid, list_of_error_messages)
    """
    errors: list[str] = []
    seen_task_ids: set[str] = set()
    seen_branch_names: set[str] = set()

    for i, task in enumerate(corpus.get("tasks", [])):
        task_id = task.get("task_id", "")
        if not task_id:
            errors.append(f"tasks[{i}]: missing task_id")
            continue
        if task_id in seen_task_ids:
            errors.append(f"tasks[{i}] ({task_id}): duplicate task_id")
        seen_task_ids.add(task_id)

        branch_name = task.get("branch_name", "")
        if not branch_name:
            errors.append(f"tasks[{i}] ({task_id}): missing branch_name")
        elif branch_name in seen_branch_names:
            errors.append(f"tasks[{i}] ({task_id}): duplicate branch_name '{branch_name}'")
        else:
            seen_branch_names.add(branch_name)

        # Check branch does not already exist
        if branch_name and not skip_branch_check and _git_branch_exists(repo, branch_name):
            errors.append(
                f"tasks[{i}] ({task_id}): branch_name '{branch_name}' already exists locally "
                "(use --run-id to scope branch names for repeated runs)"
            )

        # Each allowed_files entry must exist at base_sha and have no .aed_plan
        for f in task.get("allowed_files", []):
            result = subprocess.run(
                ["git", "-C", str(repo), "cat-file", "-e", f"{base_sha}:{f}"],
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                errors.append(f"tasks[{i}] ({task_id}): allowed_files '{f}' does not exist at {base_sha[:8]}")
                continue
            # Check for .aed_plan in content
            content = subprocess.check_output(
                ["git", "-C", str(repo), "show", f"{base_sha}:{f}"],
                text=True,
            )
            if ".aed_plan" in content or "aed_plan" in content:
                errors.append(
                    f"tasks[{i}] ({task_id}): allowed_files '{f}' contains '.aed_plan' text "
                    "(must be clean at base_sha for smoke test)"
                )

        # mock_edit.path must be in allowed_files
        for me in task.get("mock_edits", []):
            mp = me.get("path", "")
            allowed = task.get("allowed_files", [])
            if mp not in allowed:
                errors.append(
                    f"tasks[{i}] ({task_id}): mock_edit.path '{mp}' not in allowed_files {allowed}"
                )

    return not bool(errors), errors


# -----------------------------------------------------------------------
# Build batch packet
# -----------------------------------------------------------------------


def build_batch_packet(
    corpus: dict,
    base_sha: str,
    output_root: Path,
    run_id: str,
) -> dict:
    """
    Build a batch packet from a validated corpus, using run-scoped branch names.

    run_id sanitized for branch safety: only alphanumeric, dash, underscore.
    Each task gets: branch_name = f"apply/{corpus_id}-{run_id}-{task_id}"

    Returns the batch packet dict.
    """
    tasks_out = []
    for task in corpus["tasks"]:
        task_copy = dict(task)  # shallow copy includes all fields
        # Ensure required fields present (fallback to sensible defaults)
        task_copy.setdefault("suggested_pr_title", f"Corpus task: {task['task_id']}")
        task_copy.setdefault("suggested_pr_body", f"Corpus run {run_id} task {task['task_id']}")
        # Generate run-scoped branch name
        task_copy["branch_name"] = f"apply/{corpus['corpus_id']}-{run_id}-{task['task_id']}"
        # Inject unique output_root per task
        task_copy["output_root"] = str(output_root / "tasks" / task["task_id"])
        tasks_out.append(task_copy)

    return {
        "packet_kind": "aed.autocoder.batch.v0",
        "batch_id": corpus["corpus_id"],
        "base_sha": base_sha,
        "output_root": str(output_root),
        "stop_on_first_hold": False,
        "tasks": tasks_out,
    }


# -----------------------------------------------------------------------
# Invoke batch controller
# -----------------------------------------------------------------------


def invoke_batch_controller(
    batch_packet: dict,
    output_root: Path,
) -> tuple[bool, str, int]:
    """
    Write batch packet JSON and invoke run_autocoder_batch.py.

    Returns: (batch_exited_zero, stdout_and_stderr_combined, exit_code)

    The batch controller exits 0 for both READY and HOLD statuses.
    Non-zero exit indicates a fatal error (file not found, etc.).
    """
    batch_packet_path = output_root / "batch_packet.json"
    output_root.mkdir(parents=True, exist_ok=True)
    batch_packet_path.write_text(json.dumps(batch_packet, indent=2), encoding="utf-8")

    batch_output_json = output_root / "batch_status.json"
    batch_output_md = output_root / "batch_status.md"

    argv = [
        sys.executable,  # python3 from the running interpreter
        str(BATCH_SCRIPT),
        "--batch-packet-json", str(batch_packet_path),
        "--output-json", str(batch_output_json),
        "--output-md", str(batch_output_md),
    ]

    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=600,
    )

    combined = result.stdout + result.stderr
    return result.returncode == 0, combined, result.returncode


def read_batch_status(output_root: Path) -> tuple[bool, dict, str]:
    """Read and parse batch_status.json. Returns (ok, status_dict, error_message)."""
    batch_status_path = output_root / "batch_status.json"
    if not batch_status_path.exists():
        return False, {}, f"batch_status.json not found at {batch_status_path}"
    try:
        status = json.loads(batch_status_path.read_text(encoding="utf-8"))
        return True, status, ""
    except json.JSONDecodeError as e:
        return False, {}, f"batch_status.json is not valid JSON: {e}"


# -----------------------------------------------------------------------
# Build eval report
# -----------------------------------------------------------------------


def build_eval_report(
    corpus: dict,
    base_sha: str,
    batch_status: dict,
    output_root: Path,
    run_id: str,
) -> dict:
    """
    Build the eval_report.v0 dict from batch_status and corpus.

    eval_pass is True only when:
      - batch_status.status == BATCH_READY_FOR_HUMAN_REVIEW
      - ALL tasks have status == SINGLE_TASK_READY_FOR_HUMAN_REVIEW

    Records generated branch names (run-scoped) and corpus branch names.
    """
    tasks = batch_status.get("tasks", [])
    total = len(tasks)
    passed = sum(1 for t in tasks if t.get("status") == SINGLE_TASK_READY_STATUS)
    held = sum(1 for t in tasks if t.get("status", "").startswith("HOLD_"))
    failed = total - passed - held

    task_results = []
    failure_summary = []

    for i, task in enumerate(corpus.get("tasks", [])):
        task_id = task["task_id"]
        bt = next((t for t in tasks if t.get("task_id") == task_id), {})

        generated_branch = f"apply/{corpus['corpus_id']}-{run_id}-{task_id}"
        corpus_branch = task.get("branch_name", "")

        # Check if diff.patch applies cleanly
        diff_applies = False
        if bt.get("task_worktree_path"):
            diff_path = Path(bt["task_worktree_path"]) / "diff.patch"
            if diff_path.exists():
                result = subprocess.run(
                    ["git", "-C", str(bt["task_worktree_path"]), "apply", "--check", str(diff_path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                diff_applies = result.returncode == 0

        tr = {
            "task_id": task_id,
            "status": bt.get("status", "UNKNOWN"),
            "generated_branch_name": generated_branch,
            "corpus_branch_name": corpus_branch,
            "task_worktree_path": bt.get("task_worktree_path", ""),
            "artifacts_present": bool(bt.get("task_worktree_path")),
            "diff_patch_applies_cleanly": diff_applies,
        }
        task_results.append(tr)

        if bt.get("status", "").startswith("HOLD_"):
            failure_summary.append({
                "task_id": task_id,
                "status": bt["status"],
                "generated_branch": generated_branch,
                "corpus_branch": corpus_branch,
                "task_worktree_path": bt.get("task_worktree_path", ""),
            })

    eval_pass = (
        batch_status.get("status") == BATCH_READY_STATUS
        and passed == total
        and held == 0
    )

    return {
        "report_kind": "aed.autocoder.eval_report.v0",
        "corpus_id": corpus["corpus_id"],
        "corpus_version": corpus["corpus_version"],
        "eval_runner_version": EVAL_RUNNER_VERSION,
        "run_id": run_id,
        "base_sha": base_sha,
        "batch_status": batch_status.get("status", "UNKNOWN"),
        "total_tasks": total,
        "passed_tasks": passed,
        "failed_tasks": failed,
        "held_tasks": held,
        "eval_pass": eval_pass,
        "failure_summary": failure_summary,
        "task_results": task_results,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_eval_report(
    report: dict,
    output_root: Path,
    report_json_path: Path,
    report_md_path: Path,
) -> None:
    """Write eval_report.json and eval_report.md."""
    report_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report_md_path.write_text(_render_eval_report_md(report), encoding="utf-8")


def _render_eval_report_md(report: dict) -> str:
    """Render eval_report as markdown."""
    lines = [
        "# Eval Report",
        f"**corpus_id:** {report['corpus_id']}",
        f"**run_id:** {report['run_id']}",
        f"**base_sha:** `{report['base_sha']}`",
        f"**batch_status:** {report['batch_status']}",
        f"**eval_pass:** {'✅' if report['eval_pass'] else '❌'}",
        "",
        f"- total: {report['total_tasks']}",
        f"- passed: {report['passed_tasks']}",
        f"- held: {report['held_tasks']}",
        f"- failed: {report['failed_tasks']}",
        "",
        "## Task Results",
    ]
    for tr in report.get("task_results", []):
        badge = "✅" if tr["status"] == SINGLE_TASK_READY_STATUS else f"❌({tr['status']})"
        lines.append(f"- {tr['task_id']} {badge}")
        lines.append(f"  - generated_branch: `{tr['generated_branch_name']}`")
        if tr.get("corpus_branch_name") and tr["corpus_branch_name"] != tr["generated_branch_name"]:
            lines.append(f"  - corpus_branch: `{tr['corpus_branch_name']}`")
        lines.append(f"  - artifacts_present: {tr['artifacts_present']}")
        lines.append(f"  - diff_patch_applies_cleanly: {tr['diff_patch_applies_cleanly']}")

    if report.get("failure_summary"):
        lines.append("")
        lines.append("## Failure Summary")
        for fs in report["failure_summary"]:
            lines.append(f"- **{fs['task_id']}**: `{fs['status']}`")
            lines.append(f"  - generated_branch: `{fs['generated_branch']}`")

    return "\n".join(lines)


def write_eval_run_metadata(output_root: Path, corpus: dict, base_sha: str, eval_pass: bool, run_id: str) -> None:
    """Write eval_run_metadata.json."""
    meta = {
        "corpus_id": corpus["corpus_id"],
        "corpus_version": corpus["corpus_version"],
        "eval_runner_version": EVAL_RUNNER_VERSION,
        "run_id": run_id,
        "base_sha": base_sha,
        "eval_pass": eval_pass,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (output_root / "eval_run_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


# -----------------------------------------------------------------------
# Sanitize run_id for branch safety
# -----------------------------------------------------------------------

def sanitize_run_id(run_id: str) -> str:
    """Sanitize run_id to be safe for use in a git branch name.
    Replaces any run of non-alphanumeric characters with a single '-'.
    """
    result = []
    prev_alnum = False
    for c in run_id.strip():
        if c.isalnum() or c in "-_":
            result.append(c)
            prev_alnum = True
        else:
            if prev_alnum:
                result.append("-")
                prev_alnum = False
    return "".join(result).strip("-")


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------

def main(
    corpus_json: str,
    output_root: str,
    report_json: str,
    report_md: str,
    run_id: str | None = None,
    repo_root_override: Path | None = None,
    base_sha_override: str | None = None,
) -> int:
    """Returns 0 on eval_pass, 1 on eval_fail or fatal error."""

    # Use override if provided, otherwise fall back to script-inferred REPO_ROOT
    repo = repo_root_override if repo_root_override is not None else REPO_ROOT

    corpus_path = Path(corpus_json)
    if not corpus_path.exists():
        print(f"FATAL: corpus JSON not found: {corpus_path}", file=sys.stderr)
        return 1

    try:
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"FATAL: corpus JSON is not valid: {e}", file=sys.stderr)
        return 1

    # Step 1: Validate corpus schema
    valid, err = validate_corpus_schema(corpus)
    if not valid:
        print(f"FATAL: corpus schema validation failed: {err}", file=sys.stderr)
        return 1

    # Step 2: Validate all tasks
    valid, err = validate_all_tasks(corpus)
    if not valid:
        print(f"FATAL: task validation failed: {err}", file=sys.stderr)
        return 1

    # Step 3: Resolve base_sha — use override if provided
    if base_sha_override:
        base_sha = base_sha_override
        print(f"INFO: using --base-sha override: base_sha='{base_sha}'")
    else:
        policy = corpus.get("base_sha_policy", "current_main")
        if policy == "current_main":
            # Try multiple refs: GITHUB_BASE_REF (CI), origin/main, main, HEAD
            # GITHUB_BASE_REF is set by GitHub Actions for PRs
            import os
            github_base_ref = os.environ.get("GITHUB_BASE_REF", "")
            refs_to_try = []
            if github_base_ref:
                refs_to_try.append(github_base_ref)  # e.g., "main" in CI
            refs_to_try.extend(["origin/main", "main"])
            for ref in refs_to_try:
                result = subprocess.run(
                    ["git", "-C", str(repo), "rev-parse", ref],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    sha = result.stdout.strip()
                    if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
                        print(f"INFO: resolved main to {sha} via {ref}")
                        base_sha = sha
                        break
            else:
                print(f"FATAL: could not resolve main (tried: {', '.join(refs_to_try)})", file=sys.stderr)
                return 1
        else:
            ok, msg, base_sha = resolve_base_sha(policy, repo)
            if not ok:
                print(f"FATAL: base_sha_policy resolution failed: {msg}", file=sys.stderr)
                return 1
            print(f"INFO: base_sha_policy='{policy}' resolved to base_sha='{base_sha}'")

    # Step 4: Validate target files and branch names
    # Note: We skip corpus branch collision checking here because the batch packet
    # uses run-scoped branch names. Generated branch collisions are checked after
    # build_batch_packet generates them.
    targets_valid, target_errors = validate_corpus_targets(corpus, base_sha, repo, skip_branch_check=True)
    if not targets_valid:
        for te in target_errors:
            print(f"FATAL: corpus target validation: {te}", file=sys.stderr)
        return 1
    print(f"INFO: all {len(corpus['tasks'])} tasks pass corpus target validation")

    # Step 5: Generate run_id
    if run_id is None:
        run_id = f"{int(time.time())}-{sanitize_run_id(corpus['corpus_id'])}"
    print(f"INFO: run_id='{run_id}'")

    # Step 6: Build batch packet with run-scoped branch names
    out_root = Path(output_root)
    batch_packet = build_batch_packet(corpus, base_sha, out_root, run_id)
    print(f"INFO: batch packet built with batch_id='{corpus['corpus_id']}', stop_on_first_hold=False, {len(corpus['tasks'])} tasks")

    # Step 7: Invoke batch controller
    print(f"INFO: invoking batch controller: python3 {BATCH_SCRIPT}")
    batch_ok, combined, rc = invoke_batch_controller(batch_packet, out_root)
    print(f"INFO: batch controller exited zero (status={batch_ok}), reading batch_status.json")

    # Step 8: Read batch status
    status_ok, batch_status, status_err = read_batch_status(out_root)
    if not status_ok:
        print(f"FATAL: could not read batch_status.json: {status_err}", file=sys.stderr)
        return 1

    batch_status_val = batch_status.get("status", "UNKNOWN")
    task_count = len(batch_status.get("tasks", []))
    print(f"INFO: batch_status='{batch_status_val}', tasks={task_count}")

    # Step 9: Build and write eval report
    eval_report = build_eval_report(corpus, base_sha, batch_status, out_root, run_id)
    report_json_path = Path(report_json)
    report_md_path = Path(report_md)
    write_eval_report(eval_report, out_root, report_json_path, report_md_path)
    print(f"INFO: eval_report.json written to {report_json_path}")
    print(f"INFO: eval_report.md written to {report_md_path}")

    # Step 10: Write eval_run_metadata.json
    write_eval_run_metadata(out_root, corpus, base_sha, eval_report["eval_pass"], run_id)

    # Step 11: Emit summary and exit code
    if eval_report["eval_pass"]:
        print(f"\n✅ eval_pass=True — passed={eval_report['passed_tasks']}/{eval_report['total_tasks']}, batch_status={batch_status_val}")
        print("INFO: exiting 0 (eval_pass)")
        return 0
    else:
        failures = eval_report["failure_summary"]
        print(f"\n❌ eval_pass=False — passed={eval_report['passed_tasks']}, held={eval_report['held_tasks']}, failed={eval_report['failed_tasks']}, batch_status={batch_status_val}")
        if failures:
            print("Failures:")
            for f in failures:
                print(f"  - {f['task_id']}: {f['status']} (generated_branch={f['generated_branch']})")
        print("INFO: exiting 1 (eval_fail)")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AED autocoder eval corpus runner. Validates corpus JSON, resolves base_sha, builds a batch packet with run-scoped branch names, invokes batch controller, emits eval_report.json/md.",
    )
    parser.add_argument("--corpus-json", required=True, help="Path to corpus JSON file")
    parser.add_argument("--output-root", required=True, help="Root directory for batch output")
    parser.add_argument("--report-json", required=True, help="Path to write eval_report.json")
    parser.add_argument("--report-md", required=True, help="Path to write eval_report.md")
    parser.add_argument("--run-id", default=None, help="Optional run ID for generated branch names. If omitted, a timestamp-based ID is generated.")
    parser.add_argument("--repo-root", default=None, help="Path to the repository root. If omitted, inferred from script location.")
    parser.add_argument("--base-sha", default=None, help="Override base_sha directly. If omitted, base_sha_policy in the corpus JSON determines it (default: current main branch).")

    args = parser.parse_args()

    # Determine effective repo root
    if args.repo_root:
        repo_root = Path(args.repo_root).resolve()
    else:
        repo_root = REPO_ROOT.resolve()

    # If --base-sha is provided, use it directly; otherwise resolve via policy
    if args.base_sha:
        base_sha_override = args.base_sha
    else:
        base_sha_override = None

    sys.exit(main(
        corpus_json=args.corpus_json,
        output_root=args.output_root,
        report_json=args.report_json,
        report_md=args.report_md,
        run_id=args.run_id,
        repo_root_override=repo_root,
        base_sha_override=base_sha_override,
    ))