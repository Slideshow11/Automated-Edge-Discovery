#!/usr/bin/env python3
"""
pr_gate_controller.py

AED PR Gate Controller — dry-run by default, optional --apply-create-task.

Runs the existing PR gate chain end-to-end:
  1. Classify PR state via classify_pr_gate_state.py
  2. Generate task draft via pr_gate_task_draft.py
  3. Generate Kanban creation plan via pr_gate_kanban_task_create.py
  4. Optionally create task via pr_gate_kanban_task_create.py --apply

Default mode is read-only dry-run.  No Kanban tasks are created without --apply-create-task.
The controller calls child helpers via subprocess; it does not reimplement their logic.
It does NOT call hermes kanban directly.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PACKET_KIND = "aed.pr_gate.controller_run.v1"
SCHEMA_VERSION = 1

STOP_RULES = [
    "no_dispatch",
    "no_merge",
    "no_pr_patch",
    "no_codex_request",
    "no_memory_update",
    "no_skill_manage",
]

# Forbidden patterns for safety check on output-dir
FORBIDDEN_PATTERNS = [
    "gh pr merge",
    "gh pr comment",
    "gh pr create",
    "git push",
    "git commit",
    "hermes kanban dispatch",
    "memory.update",
    "fact_store",
    "skill_manage",
    "delegate_task",
    "cronjob",
    "requests.get",
    "requests.post",
    "requests.patch",
    "requests.put",
    "httpx",
    "urllib",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_child(script_name: str) -> Path:
    """Resolve path to a scripts/local child helper."""
    base = Path(__file__).resolve().parent
    p = base / script_name
    if not p.exists():
        raise FileNotFoundError(f"Child script not found: {p}")
    return p


def _run_child(args: list, *, capture_output=True, check=True) -> subprocess.CompletedProcess:
    """Run a child script. Returns CompletedProcess. Raises on nonzero exit if check=True."""
    result = subprocess.run(
        args,
        capture_output=capture_output,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Child script failed: {' '.join(str(a) for a in args)}\n"
            f"rc={result.returncode}\nstderr={result.stderr[:500]}"
        )
    return result


def _validate_guard_compare_json(path: Path) -> tuple[bool, str]:
    """Load and validate a guard compare JSON.

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
    snapshot_path: Path | None,
    compare_json_path: Path | None,
    compare_md_path: Path | None,
    guard_root: Path,
) -> dict:
    """Record-only persistent mutation guard validation.

    Accepts pre-existing snapshot and compare report paths.
    Validates compare JSON, checks recommendation, returns guard state dict.

    Returns a guard state dict with keys:
        status, snapshot_path, compare_json_path, compare_md_path,
        blocked_changes_count, allowed_changes_count, message
    """
    # snapshot_path may be None in record-only mode — guard just validates reports
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

    # record-only: snapshot_path is optional, but if compare_json is provided,
    # we validate it
    if compare_json_path is None:
        return state

    # Validate compare JSON exists and is well-formed
    if not compare_json_path.exists():
        state["status"] = "error"
        state["message"] = f"compare JSON not found: {compare_json_path}"
        return state

    is_valid, msg = _validate_guard_compare_json(compare_json_path)
    if not is_valid:
        state["status"] = "error"
        state["message"] = msg
        return state

    # Load compare JSON to extract counts
    with open(compare_json_path) as f:
        data = json.load(f)

    rec = data.get("recommendation", "UNKNOWN")
    state["status"] = "clean" if rec == "PASS" else "blocked"
    state["message"] = f"guard recommendation: {rec}"
    state["blocked_changes_count"] = len(data.get("blocked_changes", []))
    state["allowed_changes_count"] = len(data.get("allowed_changes", []))

    return state


def _reject_hermes_path(output_dir: Path) -> None:
    """Reject output-dir under /home/max/.hermes."""
    try:
        resolved = output_dir.resolve()
        if str(resolved).startswith("/home/max/.hermes"):
            raise ValueError(f"output-dir cannot be under /home/max/.hermes: {output_dir}")
    except ValueError:
        raise
    except Exception:
        pass  # symlink/cross-device; skip strict check


def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def _write_json(data: dict, path: Path) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _write_text(text: str, path: Path) -> None:
    with open(path, "w") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def run_controller(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    board: str,
    allowed_files: list[str],
    output_dir: Path,
    apply_create_task: bool,
    expected_head: str | None = None,
    base_branch: str = "main",
    require_persistent_guard: bool = False,
    persistent_guard_root: str = "/home/max/.hermes",
    persistent_guard_snapshot: Path | None = None,
    persistent_guard_compare_json: Path | None = None,
    persistent_guard_compare_md: Path | None = None,
) -> dict:
    """Run the full PR gate chain and produce a controller_run packet."""

    output_dir.mkdir(parents=True, exist_ok=True)

    # Persistent mutation guard validation (record-only mode)
    # Validates pre-existing compare JSON against --require-persistent-guard.
    # Does NOT mutate Hermes — only reads report files.
    guard_state: dict = {
        "required": require_persistent_guard,
        "status": "not_required",
        "snapshot_path": str(persistent_guard_snapshot) if persistent_guard_snapshot else None,
        "compare_json_path": str(persistent_guard_compare_json) if persistent_guard_compare_json else None,
        "compare_md_path": str(persistent_guard_compare_md) if persistent_guard_compare_md else None,
        "blocked_changes_count": 0,
        "allowed_changes_count": 0,
        "message": "persistent mutation guard not required",
    }

    if require_persistent_guard or persistent_guard_compare_json is not None:
        guard_state = _run_persistent_guard_validate(
            snapshot_path=persistent_guard_snapshot,
            compare_json_path=persistent_guard_compare_json,
            compare_md_path=persistent_guard_compare_md,
            guard_root=Path(persistent_guard_root),
        )
        guard_state["required"] = require_persistent_guard

        # BLOCK on guard failure when required
        if require_persistent_guard and guard_state["status"] in ("blocked", "error", "not_required"):
            run_packet = {
                "packet_kind": PACKET_KIND,
                "schema_version": SCHEMA_VERSION,
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "repo": {
                    "owner": repo_owner,
                    "name": repo_name,
                    "pr_number": pr_number,
                },
                "board": board,
                "mode": "blocked_on_persistent_guard",
                "apply_create_task_requested": apply_create_task,
                "persistent_mutation_guard": guard_state,
                "artifacts": {},
                "result": {
                    "final_recommendation": "blocked_on_persistent_guard",
                    "classification": "guard_blocked",
                    "task_action": "",
                    "kanban_recommended_action": "",
                    "created_task_id": None,
                    "duplicate_found": False,
                },
                "stop_rules": STOP_RULES,
                "blockers_or_uncertainty": [
                    f"persistent_mutation_guard: {guard_state['message']}",
                ],
            }
            controller_packet_path = output_dir / "CONTROLLER_RUN_PACKET.json"
            _write_json(run_packet, controller_packet_path)
            _write_text(_render_summary(run_packet, {}, {}, {}), output_dir / "CONTROLLER_RUN_SUMMARY.md")
            return run_packet

    # Step 1: Classify PR state
    classifier_json_path = output_dir / "CLASSIFIER_PACKET.json"
    classifier_args = [
        sys.executable,
        str(_resolve_child("classify_pr_gate_state.py")),
        "--repo-owner", repo_owner,
        "--repo-name", repo_name,
        "--pr-number", str(pr_number),
        "--output-json",
    ]
    for f in allowed_files:
        classifier_args += ["--allowed-file", f]
    if expected_head:
        classifier_args += ["--expected-head", expected_head]
    if base_branch != "main":
        classifier_args += ["--base-branch", base_branch]

    result = _run_child(classifier_args, check=True)
    # classify_pr_gate_state.py writes to stdout (--output-json controls compactness).
    # Capture stdout and write to the expected artifact path.
    # If stdout is already set by mock (test harness), use it. Otherwise use real output.
    if result.stdout.strip():
        classifier_json_path.write_text(result.stdout)
    classifier_packet = _load_json(classifier_json_path)

    # Step 2: Generate task draft
    task_draft_json_path = output_dir / "PR_GATE_TASK_DRAFT.json"
    task_draft_md_path = output_dir / "PR_GATE_TASK_DRAFT.md"
    task_draft_args = [
        sys.executable,
        str(_resolve_child("pr_gate_task_draft.py")),
        "generate",
        "--classifier-json", str(classifier_json_path),
        "--output-json", str(task_draft_json_path),
        "--output-md", str(task_draft_md_path),
    ]

    result = _run_child(task_draft_args, check=True)
    task_draft_packet = _load_json(task_draft_json_path)

    # Step 3: Generate Kanban creation plan (always dry-run)
    kanban_plan_json_path = output_dir / "KANBAN_CREATE_PLAN.json"
    kanban_plan_md_path = output_dir / "KANBAN_CREATE_PLAN.md"
    kanban_plan_args = [
        sys.executable,
        str(_resolve_child("pr_gate_kanban_task_create.py")),
        "--task-draft", str(task_draft_json_path),
        "--board", board,
        "--output-json", str(kanban_plan_json_path),
        "--output-md", str(kanban_plan_md_path),
    ]

    result = _run_child(kanban_plan_args, check=True)
    kanban_plan_packet = _load_json(kanban_plan_json_path)

    # Step 4: Optional task creation via _apply_create_task
    duplicate_found = kanban_plan_packet.get("duplicate_check", {}).get("duplicate_found", False)
    created_task_id = None
    idempotency_key = ""
    apply_blockers = []

    if apply_create_task:
        dup, tid, ikey, blockers = _apply_create_task(
            apply_create_task=apply_create_task,
            task_draft_packet=task_draft_packet,
            task_draft_json_path=task_draft_json_path,
            kanban_plan_json_path=kanban_plan_json_path,
            kanban_plan_md_path=kanban_plan_md_path,
            board=board,
        )
        # Override with _apply_create_task results when in apply mode
        duplicate_found = dup if dup is not None else duplicate_found
        created_task_id = tid
        idempotency_key = ikey
        apply_blockers = blockers

    # Build controller run packet
    classification = classifier_packet.get("classification", "unknown")
    task_action = task_draft_packet.get("task_draft", {}).get("action", "")
    kanban_action = kanban_plan_packet.get("recommended_action") or (
        "no_action" if kanban_plan_packet.get("kanban_task") is None else "create_task"
    )

    if apply_create_task and task_action == "no_action_wait":
        final_recommendation = "no_action_wait_downgrade"
    elif duplicate_found:
        final_recommendation = "duplicate_skipped"
    elif kanban_action == "no_action":
        final_recommendation = "no_action"
    elif created_task_id:
        final_recommendation = f"task_created:{created_task_id}"
    else:
        final_recommendation = "plan_ready_for_review"

    run_packet = {
        "packet_kind": PACKET_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": {
            "owner": repo_owner,
            "name": repo_name,
            "pr_number": pr_number,
        },
        "board": board,
        "mode": "apply_create_task" if (apply_create_task and task_action not in ("no_action_wait", "ci_pending", "codex_pending", "unknown", "")) else "dry_run",
        "apply_create_task_requested": apply_create_task,
        "apply_create_task_allowed": apply_create_task and task_action not in ("no_action_wait", "ci_pending", "codex_pending", "unknown", ""),
        "idempotency_key": idempotency_key,
        "downstream_helper": "pr_gate_kanban_task_create.py",
        "no_dispatch_guarantee": True,
        "persistent_mutation_guard": guard_state,
        "artifacts": {
            "classifier_json": str(classifier_json_path),
            "task_draft_json": str(task_draft_json_path),
            "task_draft_md": str(task_draft_md_path),
            "kanban_plan_json": str(kanban_plan_json_path),
            "kanban_plan_md": str(kanban_plan_md_path),
        },
        "result": {
            "classification": classification,
            "task_action": task_action,
            "kanban_recommended_action": kanban_action,
            "created_task_id": created_task_id,
            "duplicate_found": duplicate_found,
            "final_recommendation": final_recommendation,
        },
        "stop_rules": STOP_RULES,
        "blockers_or_uncertainty": apply_blockers,
    }

    # Write controller run packet
    controller_packet_path = output_dir / "CONTROLLER_RUN_PACKET.json"
    _write_json(run_packet, controller_packet_path)

    # Write human-readable summary
    summary_md = _render_summary(run_packet, classifier_packet, task_draft_packet, kanban_plan_packet)
    controller_summary_path = output_dir / "CONTROLLER_RUN_SUMMARY.md"
    _write_text(summary_md, controller_summary_path)

    return run_packet


def _make_controller_idempotency_key(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    head_sha: str,
    task_action: str,
) -> str:
    """Build a deterministic controller-level idempotency key.

    Format: aed:<owner>/<name>:pr:<pr>:head:<sha>:action:<action>

    Returns empty string if any component is missing or empty.
    """
    if not all([repo_owner, repo_name, pr_number, head_sha, task_action]):
        return ""
    return f"aed:{repo_owner}/{repo_name}:pr:{pr_number}:head:{head_sha}:action:{task_action}"


def _apply_create_task(
    apply_create_task: bool,
    task_draft_packet: dict,
    task_draft_json_path: Path,
    kanban_plan_json_path: Path,
    kanban_plan_md_path: Path,
    board: str,
) -> tuple[bool | None, bool | None, str | None, list[str]]:
    """Apply Kanban task creation once.

    Returns (duplicate_found, created_task_id, idempotency_key, blockers).
    Refuses to apply if task_action is no_action_wait or other unsafe states.
    """
    if not apply_create_task:
        return None, None, "", []

    task_action = task_draft_packet.get("task_draft", {}).get("action", "")
    source = task_draft_packet.get("source", {})
    pr_number_raw = source.get("pr_number", "") or task_draft_packet.get("pr_number", "")
    # Normalize: classifier provides int, schema uses string
    try:
        pr_number = int(pr_number_raw) if pr_number_raw else 0
    except (ValueError, TypeError):
        pr_number = 0
    head_sha = source.get("head_sha", "") or task_draft_packet.get("head_sha", "")
    repo_owner = source.get("repo_owner", "") or task_draft_packet.get("repo", {}).get("owner", "")
    repo_name = source.get("repo_name", "") or task_draft_packet.get("repo", {}).get("name", "")

    # Build idempotency key
    controller_ikey = _make_controller_idempotency_key(
        repo_owner, repo_name, pr_number, head_sha, task_action
    )

    blockers = []

    # Refuse apply if required fields are missing
    if not head_sha:
        blockers.append("apply_create_task: head_sha is missing from task draft source")
    if not pr_number:
        blockers.append("apply_create_task: pr_number is missing from task draft source")
    if not task_action:
        blockers.append("apply_create_task: task_action is missing from task draft")

    # Refuse apply for no-action states
    if task_action in ("no_action_wait", "ci_pending", "codex_pending", "unknown", ""):
        blockers.append(f"apply_create_task: task_action '{task_action}' cannot produce a task")

    if blockers:
        return None, None, controller_ikey, blockers

    # Write controller idempotency key into task draft so kanban helper uses it
    # for duplicate detection (P2 fix: align controller key with downstream key)
    task_draft_packet["idempotency_key"] = controller_ikey
    with open(task_draft_json_path, "w") as f:
        json.dump(task_draft_packet, f, indent=2)

    # Exactly one invocation
    apply_args = [
        sys.executable,
        str(_resolve_child("pr_gate_kanban_task_create.py")),
        "--task-draft", str(task_draft_json_path),
        "--board", board,
        "--output-json", str(kanban_plan_json_path),
        "--output-md", str(kanban_plan_md_path),
        "--apply",
    ]
    result = _run_child(apply_args, check=True)

    # Re-read plan after apply
    apply_plan = _load_json(kanban_plan_json_path)
    duplicate_found = apply_plan.get("duplicate_check", {}).get("duplicate_found", False)
    existing_id = apply_plan.get("duplicate_check", {}).get("existing_task_id")

    if duplicate_found and existing_id:
        created_task_id = existing_id
    else:
        created_task_id = apply_plan.get("apply_result", {}).get("created_task_id")

    return duplicate_found, created_task_id, controller_ikey, []


def _render_summary(
    run_packet: dict,
    classifier_packet: dict,
    task_draft_packet: dict,
    kanban_plan_packet: dict,
) -> str:
    """Render CONTROLLER_RUN_SUMMARY.md from run packet and child artifacts."""

    mode = run_packet["mode"]
    repo = run_packet["repo"]
    result = run_packet["result"]
    artifacts = run_packet.get("artifacts", {})
    board = run_packet["board"]

    lines = [
        f"# AED PR Gate Controller Run — {PACKET_KIND}",
        "",
        f"**Generated:** {run_packet['generated_at']}",
        f"**Repo:** {repo['owner']}/{repo['name']} PR #{repo['pr_number']}",
        f"**Board:** `{board}`",
        f"**Mode:** `{mode}`",
        "",
        "## Classification",
        "",
        f"- **Classification:** `{classifier_packet.get('classification', 'unknown') if classifier_packet else 'unknown'}`",
        f"- **CI Status:** `{classifier_packet.get('ci_status', 'unknown') if classifier_packet else 'unknown'}`",
        f"- **Codex Status:** `{classifier_packet.get('codex_status', 'unknown') if classifier_packet else 'unknown'}`",
        "",
        "## Task Draft",
        "",
        f"- **Action:** `{result['task_action']}`",
        f"- **Idempotency key:** `{run_packet.get('idempotency_key', task_draft_packet.get('idempotency_key', '') if task_draft_packet else '')}`",
        f"- **PR head:** `{task_draft_packet.get('head_sha', '') if task_draft_packet else ''}`",
        "",
        "## Kanban Plan",
        "",
        f"- **Recommended action:** `{result['kanban_recommended_action']}`",
        f"- **Duplicate found:** `{result['duplicate_found']}`",
        f"- **Created task ID:** `{result['created_task_id'] or 'none'}`",
        "",
        "## Final Recommendation",
        "",
        f"`{result['final_recommendation']}`",
        "",
    ]

    if artifacts:
        lines += [
            "## Artifacts",
            "",
            f"- Classifier: `{artifacts.get('classifier_json', 'not available')}`",
            f"- Task draft JSON: `{artifacts.get('task_draft_json', 'not available')}`",
            f"- Task draft MD: `{artifacts.get('task_draft_md', 'not available')}`",
            f"- Kanban plan JSON: `{artifacts.get('kanban_plan_json', 'not available')}`",
            f"- Kanban plan MD: `{artifacts.get('kanban_plan_md', 'not available')}`",
            "",
        ]

    lines += ["## Stop Rules", ""]
    for rule in run_packet["stop_rules"]:
        lines.append(f"- `{rule}`")

    if run_packet.get("blockers_or_uncertainty"):
        lines += ["", "## Blockers / Uncertainty", ""]
        for b in run_packet["blockers_or_uncertainty"]:
            lines.append(f"- {b}")

    guard = run_packet.get("persistent_mutation_guard", {})
    if guard and guard.get("status") != "not_required":
        lines += ["", "## Persistent Mutation Guard", ""]
        lines.append(f"- **Required:** `{guard.get('required', False)}`")
        lines.append(f"- **Status:** `{guard.get('status', 'unknown')}`")
        lines.append(f"- **Message:** `{guard.get('message', '')}`")
        lines.append(f"- **Blocked changes:** `{guard.get('blocked_changes_count', 0)}`")
        lines.append(f"- **Allowed changes:** `{guard.get('allowed_changes_count', 0)}`")
        if guard.get("compare_json_path"):
            lines.append(f"- **Compare JSON:** `{guard['compare_json_path']}`")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="AED PR Gate Controller — dry-run by default, optional --apply-create-task"
    )
    p.add_argument("--repo-owner", required=True, help="GitHub repository owner")
    p.add_argument("--repo-name", required=True, help="GitHub repository name")
    p.add_argument("--pr-number", required=True, type=int, help="PR number")
    p.add_argument("--board", default="aed", help="Kanban board name (default: aed)")
    p.add_argument(
        "--allowed-file", action="append", default=[],
        dest="allowed_files", help="Allowed changed file; may be repeated"
    )
    p.add_argument(
        "--output-dir", required=True, type=Path,
        help="Directory to write all artifact files"
    )
    p.add_argument(
        "--apply-create-task", action="store_true",
        help="Apply Kanban task creation via pr_gate_kanban_task_create.py --apply. "
             "Without this flag, dry-run mode runs the full chain with no Kanban mutation."
    )
    p.add_argument(
        "--expected-head", help="Expected PR head SHA for classifier"
    )
    p.add_argument(
        "--base-branch", default="main",
        help="Base branch (default: main)"
    )
    p.add_argument(
        "--persistent-guard-root",
        default="/home/max/.hermes",
        help="Hermes root for persistent mutation guard (default: /home/max/.hermes)"
    )
    p.add_argument(
        "--persistent-guard-snapshot",
        type=Path, default=None,
        help="Path to pre-existing snapshot JSON for record-only guard validation"
    )
    p.add_argument(
        "--persistent-guard-compare-json",
        type=Path, default=None,
        help="Path to pre-existing guard compare JSON report"
    )
    p.add_argument(
        "--persistent-guard-compare-md",
        type=Path, default=None,
        help="Path to pre-existing guard compare markdown report"
    )
    p.add_argument(
        "--require-persistent-guard", action="store_true",
        help="Require guard validation — BLOCK if compare JSON is missing, malformed, or recommendation=BLOCK"
    )
    return p


def main() -> int:
    parser = _build_argparser()
    args = parser.parse_args()

    # Reject hermes output path
    try:
        _reject_hermes_path(args.output_dir)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # Ensure at least one allowed file is specified
    if not args.allowed_files:
        print("ERROR: at least one --allowed-file must be specified", file=sys.stderr)
        return 1

    try:
        run_controller(
            repo_owner=args.repo_owner,
            repo_name=args.repo_name,
            pr_number=args.pr_number,
            board=args.board,
            allowed_files=args.allowed_files,
            output_dir=args.output_dir,
            apply_create_task=args.apply_create_task,
            expected_head=args.expected_head,
            base_branch=args.base_branch,
            require_persistent_guard=args.require_persistent_guard,
            persistent_guard_root=args.persistent_guard_root,
            persistent_guard_snapshot=args.persistent_guard_snapshot,
            persistent_guard_compare_json=args.persistent_guard_compare_json,
            persistent_guard_compare_md=args.persistent_guard_compare_md,
        )
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except (IOError, OSError) as e:
        print(f"ERROR: I/O error: {e}", file=sys.stderr)
        return 1

    print(f"[controller] run complete — output: {args.output_dir}")
    print(f"  mode: {'apply_create_task' if args.apply_create_task else 'dry_run'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())