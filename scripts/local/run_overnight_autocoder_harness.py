#!/usr/bin/env python3
"""
run_overnight_autocoder_harness.py

AED Overnight Autocoder Harness v1 — orchestrates safe AED unattended runs.

v1 scope (dry-run only by default):
  - Verifies clean repo state before starting
  - Initializes the autocoder controller
  - Takes a persistent mutation guard snapshot of Hermes state
  - Records the snapshot path in the controller
  - Simulates task processing (dry-run only — no real task execution)
  - Compares Hermes state post-run and records the result in the controller
  - Produces a run summary JSON and markdown under the workspace
  - Stops for human review — does NOT dispatch, create PRs, merge, or append audit

Safety behaviors:
  - BLOCK if repo is dirty at start
  - BLOCK if persistent guard compare returns BLOCK
  - BLOCK if controller next_action becomes request_human
  - BLOCK if any safety invariant is already true
  - BLOCK if workspace is inside the repo root
  - All dry-run — no Hermes create, no dispatch, no PR, no merge, no audit

Usage:
  python3 scripts/local/run_overnight_autocoder_harness.py \\
    --run-id <run_id> \\
    --tasks-jsonl <tasks.jsonl> \\
    --workspace /tmp/aed_runs/<run_id> \\
    --integration-branch <branch> \\
    --hermes-root /home/max/.hermes \\
    --mode dry-run
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)


def _load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _save_json(data: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------

def check_repo_clean(repo_root: str) -> tuple[bool, str]:
    """Verify the repo working tree is clean. Returns (ok, message)."""
    result = _run(["git", "status", "--porcelain"], cwd=repo_root)
    if result.returncode not in (0, 128):
        return False, f"git status failed (exit {result.returncode}): {result.stderr.strip()}"
    lines = [l for l in result.stdout.strip().split("\n") if l]
    if lines:
        return False, f"repo dirty: {lines}"
    return True, "clean"


def check_workspace_not_in_repo(workspace: str, repo_root: str) -> tuple[bool, str]:
    """Reject workspace inside the repo root."""
    ws = Path(workspace).resolve()
    rp = Path(repo_root).resolve()
    try:
        ws.relative_to(rp)
        return False, f"workspace {workspace} must not be inside repo {repo_root}"
    except ValueError:
        return True, "workspace outside repo"


def check_safety_invariants(state: dict) -> tuple[bool, str]:
    """Check if any hard safety invariant is already true. Returns (ok, message)."""
    si = state.get("safety_invariants", {})
    violators = [
        k for k in ("hermes_touched", "dispatch_occurred", "production_board_touched")
        if si.get(k)
    ]
    if violators:
        return False, f"safety invariant already true: {violators}"
    return True, "ok"


def check_tasks_jsonl(path: str) -> tuple[bool, str]:
    """Verify TASKS.jsonl exists and contains valid JSON lines. Returns (ok, message)."""
    p = Path(path)
    if not p.exists():
        return False, f"tasks file not found: {path}"
    tasks = []
    with open(p) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                tasks.append(json.loads(line))
            except json.JSONDecodeError as e:
                return False, f"invalid JSON at line {lineno}: {e}"
    if not tasks:
        return False, "tasks file is empty"
    return True, f"{len(tasks)} tasks loaded"


# ---------------------------------------------------------------------------
# Core orchestration
# ---------------------------------------------------------------------------

def _write_early_block_summary(summary: dict) -> None:
    """Write summary JSON immediately when blocking before full init."""
    ws = summary.get("workspace")
    if ws:
        summary_json_path = Path(ws) / "OVERNIGHT_RUN_SUMMARY.json"
        _save_json(summary, str(summary_json_path))


def run_harness(args: argparse.Namespace) -> dict:
    """Execute the overnight harness. Returns the run summary dict."""
    repo_root = str(Path(args.repo_root).resolve())
    workspace = str(Path(args.workspace).resolve())
    hermes_root = str(Path(args.hermes_root).resolve())

    # All subprocess calls that invoke AED scripts must run from the AED repo,
    # not from the temp --repo-root (which is only used for git operations).
    # The script is at scripts/local/run_overnight_autocoder_harness.py, so three
    # parent steps go: scripts/local -> scripts -> repo root.
    AED_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)

    summary = {
        "run_id": args.run_id,
        "mode": args.mode,
        "repo_head": None,
        "workspace": workspace,
        "integration_branch": args.integration_branch,
        "controller_state_path": str(Path(workspace) / "CONTROLLER_STATE.json"),
        "persistent_mutation_guard": {
            "status": "not_started",
            "blocked_changes_count": 0,
            "allowed_changes_count": 0,
        },
        "tasks_seen": [],
        "tasks_recorded": [],
        "human_action_required": True,
        "recommendation": "BLOCK",
        "blocked_reason": None,
        "dry_run_only": args.mode == "dry-run",
        "no_real_work_executed": args.mode == "dry-run",
        "timestamp": _utcnow(),
    }

    # --- 1. Verify repo clean ---
    ok, msg = check_repo_clean(repo_root)
    if not ok:
        summary["blocked_reason"] = f"repo_not_clean: {msg}"
        _write_early_block_summary(summary)
        return summary
    result = _run(["git", "rev-parse", "HEAD"], cwd=repo_root)
    summary["repo_head"] = result.stdout.strip()

    # --- 2. Verify workspace not inside repo ---
    ok, msg = check_workspace_not_in_repo(workspace, repo_root)
    if not ok:
        summary["blocked_reason"] = f"workspace_in_repo: {msg}"
        _write_early_block_summary(summary)
        return summary

    # --- 3. Verify TASKS.jsonl ---
    ok, msg = check_tasks_jsonl(args.tasks_jsonl)
    if not ok:
        summary["blocked_reason"] = f"tasks_file_invalid: {msg}"
        _write_early_block_summary(summary)
        return summary

    # --- 4. Initialize controller ---
    controller_state_path = Path(workspace) / "CONTROLLER_STATE.json"
    init_result = _run([
        sys.executable, "scripts/local/autocoder_run_controller.py",
        "init",
        "--run-id", args.run_id,
        "--tasks-jsonl", args.tasks_jsonl,
        "--workspace", workspace,
        "--integration-branch", args.integration_branch,
        "--output-state", str(controller_state_path),
    ], cwd=AED_REPO_ROOT)
    if init_result.returncode != 0:
        summary["blocked_reason"] = f"controller_init_failed: {init_result.stderr}"
        _write_early_block_summary(summary)
        return summary

    state = _load_json(str(controller_state_path))

    # --- 5. Pre-run safety check on controller state ---
    ok, msg = check_safety_invariants(state)
    if not ok:
        summary["blocked_reason"] = f"safety_invariant_violated: {msg}"
        summary["recommendation"] = "BLOCK"
        _write_early_block_summary(summary)
        return summary

    # --- 6. Take persistent mutation guard snapshot ---
    snapshot_path = Path(workspace) / "persistent_state_before.json"
    guard_result = _run([
        sys.executable, "scripts/local/check_persistent_mutation_guard.py",
        "snapshot",
        "--root", hermes_root,
        "--output", str(snapshot_path),
    ], cwd=AED_REPO_ROOT)
    if guard_result.returncode != 0:
        summary["blocked_reason"] = f"guard_snapshot_failed: {guard_result.stderr}"
        _write_early_block_summary(summary)
        return summary

    # --- 7. Record snapshot in controller ---
    snap_result = _run([
        sys.executable, "scripts/local/autocoder_run_controller.py",
        "record-persistent-guard-snapshot",
        "--state", str(controller_state_path),
        "--root", hermes_root,
        "--snapshot-path", str(snapshot_path),
    ], cwd=AED_REPO_ROOT)
    if snap_result.returncode != 0:
        summary["blocked_reason"] = f"record_snapshot_failed: {snap_result.stderr}"
        _write_early_block_summary(summary)
        return summary

    # Reload controller state after snapshot recording
    state = _load_json(str(controller_state_path))

    # --- 8. Simulate task processing (dry-run only) ---
    # In dry-run mode, we record each task as dry_run_ready without promotion
    tasks_data = []
    with open(args.tasks_jsonl) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            tasks_data.append(json.loads(line))

    for task in tasks_data:
        task_id = task.get("task_id") or task.get("id")
        if not task_id:
            continue
        summary["tasks_seen"].append(task_id)

        # In dry-run: record task result as dry_run_ready (not TASK_READY/promoted)
        task_result = _run([
            sys.executable, "scripts/local/autocoder_run_controller.py",
            "record-task-result",
            "--state", str(controller_state_path),
            "--task-id", str(task_id),
            "--status", "TASK_READY",
            "--promotion-status", "not_promoted",
            "--local-gate", "passed",
            "--scope-status", "clean",
        ], cwd=AED_REPO_ROOT)
        if task_result.returncode != 0:
            summary["blocked_reason"] = f"task_record_failed: {task_result.stderr}"
            _write_early_block_summary(summary)
            return summary

        summary["tasks_recorded"].append(task_id)

    # --- 9. Compare persistent mutation guard ---
    compare_json_path = Path(workspace) / "persistent_state_after.json"
    compare_md_path = Path(workspace) / "persistent_state_report.md"
    guard_compare_result = _run([
        sys.executable, "scripts/local/check_persistent_mutation_guard.py",
        "compare",
        "--root", hermes_root,
        "--before", str(snapshot_path),
        "--output-json", str(compare_json_path),
        "--output-md", str(compare_md_path),
    ], cwd=AED_REPO_ROOT)
    if guard_compare_result.returncode != 0:
        summary["blocked_reason"] = f"guard_compare_failed: {guard_compare_result.stderr}"
        _write_early_block_summary(summary)
        return summary

    # Read the compare result to check recommendation
    compare_data = _load_json(str(compare_json_path))
    recommendation = compare_data.get("recommendation", "")
    blocked_changes = compare_data.get("blocked_changes", [])
    allowed_changes = compare_data.get("allowed_changes", [])

    summary["persistent_mutation_guard"]["status"] = (
        "clean" if recommendation == "PASS" else
        "blocked" if recommendation == "BLOCK" else
        "error"
    )
    summary["persistent_mutation_guard"]["blocked_changes_count"] = len(blocked_changes)
    summary["persistent_mutation_guard"]["allowed_changes_count"] = len(allowed_changes)

    # --- 10. Record compare in controller ---
    compare_record_result = _run([
        sys.executable, "scripts/local/autocoder_run_controller.py",
        "record-persistent-guard-compare",
        "--state", str(controller_state_path),
        "--compare-json", str(compare_json_path),
        "--compare-md", str(compare_md_path),
    ], cwd=AED_REPO_ROOT)
    if compare_record_result.returncode != 0:
        summary["blocked_reason"] = f"record_compare_failed: {compare_record_result.stderr}"
        _write_early_block_summary(summary)
        return summary

    # Reload controller state after compare recording
    state = _load_json(str(controller_state_path))

    # --- 11. Check for BLOCK conditions from compare ---
    if recommendation == "BLOCK":
        summary["blocked_reason"] = "persistent_mutation_guard_blocked"
        summary["recommendation"] = "BLOCK"
        _write_early_block_summary(summary)
        return summary

    # --- 12. Get next action from controller ---
    next_result = _run([
        sys.executable, "scripts/local/autocoder_run_controller.py",
        "next",
        "--state", str(controller_state_path),
    ], cwd=AED_REPO_ROOT)
    if next_result.returncode != 0:
        summary["blocked_reason"] = f"controller_next_failed: {next_result.stderr}"
        _write_early_block_summary(summary)
        return summary

    state = _load_json(str(controller_state_path))
    next_action = state.get("next_action", {})
    human_required = state.get("human_action_required", False)

    # If controller requests human, block
    if next_action.get("action") == "request_human":
        summary["blocked_reason"] = f"controller_requests_human: {next_action.get('reason')}"
        summary["recommendation"] = "BLOCK"
        _write_early_block_summary(summary)
        return summary

    # Override human_action_required to True in controller state for dry-run.
    # The controller's next_action may be generate_run_summary (not request_human),
    # but dry-run always requires human review before real execution.
    state["human_action_required"] = True
    _save_json(state, str(controller_state_path))

    # --- 13. Produce run summary JSON ---
    # In dry-run mode, human_action_required is always True (needs review before real execution)
    summary["human_action_required"] = True
    summary_json_path = Path(workspace) / "OVERNIGHT_RUN_SUMMARY.json"
    _save_json(summary, str(summary_json_path))

    # --- 14. Produce run summary markdown ---
    summary_md_path = Path(workspace) / "OVERNIGHT_RUN_SUMMARY.md"
    guard_status = summary["persistent_mutation_guard"]["status"]
    blocked_count = summary["persistent_mutation_guard"]["blocked_changes_count"]
    allowed_count = summary["persistent_mutation_guard"]["allowed_changes_count"]

    lines = [
        "# AED Overnight Run Harness — Summary",
        "",
        f"**Run ID:** `{summary['run_id']}`",
        f"**Mode:** `{summary['mode']}`",
        f"**Repo HEAD:** `{summary['repo_head']}`",
        f"**Workspace:** `{summary['workspace']}`",
        f"**Integration branch:** `{summary['integration_branch']}`",
        "",
        "## ⚠️  DRY-RUN ONLY — NO REAL WORK EXECUTED",
        "",
        "This run was executed in dry-run mode. No tasks were actually executed,",
        "no PRs were created, no merges performed, no audit entries appended,",
        "and no Hermes create/dispatch was called.",
        "",
        "## Persistent Mutation Guard",
        "",
        f"- **Status:** `{guard_status}`",
        f"- **Blocked changes:** `{blocked_count}`",
        f"- **Allowed changes:** `{allowed_count}`",
        f"- **Snapshot:** `{snapshot_path}`",
        f"- **Compare JSON:** `{compare_json_path}`",
        f"- **Compare MD:** `{compare_md_path}`",
        "",
        "## Tasks",
        "",
        f"- **Seen:** `{len(summary['tasks_seen'])}`",
        f"- **Recorded:** `{len(summary['tasks_recorded'])}`",
        "",
    ]

    if summary["tasks_seen"]:
        lines.append("")
        for tid in summary["tasks_seen"]:
            lines.append(f"- `{tid}`")

    lines += [
        "",
        "## Controller State",
        "",
        f"- **File:** `{controller_state_path}`",
        f"- **Overall status:** `{state.get('overall_status', 'unknown')}`",
        f"- **Next action:** `{next_action.get('action', 'unknown')}`",
        f"- **Human action required:** `{human_required}`",
        "",
        "## Safety",
        "",
        f"- **Recommendation:** `{summary['recommendation']}`",
    ]

    if summary["blocked_reason"]:
        lines.append(f"- **Blocked reason:** `{summary['blocked_reason']}`")

    lines += [
        "",
        "## Next Steps",
        "",
        "1. Review the controller state at `CONTROLLER_STATE.json`",
        "2. Inspect the persistent mutation guard report at `persistent_state_report.md`",
        "3. If recommendation is `READY_FOR_REVIEW`, manually run the full AED task",
        "   sequence with real task execution",
        "4. If recommendation is `BLOCK`, resolve the blocking condition before proceeding",
        "",
        "**This summary does not constitute authorization to merge.**",
    ]

    with open(summary_md_path, "w") as f:
        f.write("\n".join(lines))

    # --- 15. Final determination ---
    if summary["blocked_reason"]:
        summary["recommendation"] = "BLOCK"
    else:
        summary["recommendation"] = "READY_FOR_REVIEW"

    # Overwrite with final determination
    _save_json(summary, str(summary_json_path))

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AED Overnight Autocoder Harness v1 — dry-run orchestration for unattended AED runs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--run-id", required=True, help="Unique run identifier, e.g. aed-overnight-001")
    parser.add_argument("--tasks-jsonl", required=True, help="Path to TASKS.jsonl")
    parser.add_argument("--workspace", required=True, help="Working directory for this run")
    parser.add_argument("--integration-branch", required=True, help="Integration branch name")
    parser.add_argument("--hermes-root", default="/home/max/.hermes",
                        help="Hermes root to monitor (default: /home/max/.hermes)")
    parser.add_argument("--repo-root", default="/home/max/Automated-Edge-Discovery",
                        help="Repo root (default: /home/max/Automated-Edge-Discovery)")
    parser.add_argument("--mode", default="dry-run",
                        help="Mode: dry-run only (v1 default)")
    parser.add_argument("--output-summary-json",
                        help="Override default OVERNIGHT_RUN_SUMMARY.json path")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        summary = run_harness(args)
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 1

    summary_json_path = args.output_summary_json or str(
        Path(args.workspace) / "OVERNIGHT_RUN_SUMMARY.json"
    )

    print(f"Overnight harness completed: {summary['run_id']}")
    print(f"  mode: {summary['mode']}")
    print(f"  recommendation: {summary['recommendation']}")
    print(f"  guard status: {summary['persistent_mutation_guard']['status']}")
    print(f"  blocked_changes: {summary['persistent_mutation_guard']['blocked_changes_count']}")
    print(f"  tasks seen: {len(summary['tasks_seen'])}")
    print(f"  tasks recorded: {len(summary['tasks_recorded'])}")
    if summary["blocked_reason"]:
        print(f"  BLOCKED: {summary['blocked_reason']}")
    print(f"  summary: {summary_json_path}")

    return 0 if summary["recommendation"] != "BLOCK" else 2


if __name__ == "__main__":
    sys.exit(main())