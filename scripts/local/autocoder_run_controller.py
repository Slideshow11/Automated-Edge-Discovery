#!/usr/bin/env python3
"""
autocoder_run_controller.py

AED Autocoder Run Controller v0 — state machine for managing AED patch runs.

V0 Scope:
  - Read/write only to its own controller state files under a user-specified workspace.
  - Does NOT edit source code, run Codex, run CI, push, create PRs, merge, or append audit log.
  - Provides a durable state machine that converts repeated manual prompts into recorded state transitions.

State transitions:
  RUN_ACTIVE         → RUN_READY_FOR_SUMMARY  (all tasks done/reviewed)
  RUN_ACTIVE         → RUN_BLOCKED            (no runnable tasks, none complete)
  RUN_ACTIVE         → RUN_FAILED_SAFETY      (safety invariant triggered)
  RUN_ACTIVE         → RUN_INVALID            (init failure)
  RUN_BLOCKED        → RUN_ACTIVE             (repair resolves blocker)
  RUN_READY_FOR_SUMMARY → RUN_COMPLETE        (summary written, human authorizes)
  Any                → RUN_INVALID             (terminal corruption/integrity failure)

Usage:
  python3 scripts/local/autocoder_run_controller.py init ...
  python3 scripts/local/autocoder_run_controller.py status ...
  python3 scripts/local/autocoder_run_controller.py next ...
  python3 scripts/local/autocoder_run_controller.py record-task-result ...
  python3 scripts/local/autocoder_run_controller.py record-repair-result ...
  python3 scripts/local/autocoder_run_controller.py record-pr-result ...
  python3 scripts/local/autocoder_run_controller.py finalize-run ...
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Enums (as frozensets for fast membership testing)
# ---------------------------------------------------------------------------

RUN_STATUSES = frozenset([
    "RUN_ACTIVE",
    "RUN_READY_FOR_SUMMARY",
    "RUN_BLOCKED",
    "RUN_FAILED_SAFETY",
    "RUN_COMPLETE",
    "RUN_INVALID",
])

TASK_STATUSES = frozenset([
    "TASK_PENDING",
    "TASK_RUNNING",
    "TASK_READY",
    "TASK_BLOCKED",
    "TASK_SKIPPED",
    "TASK_FAILED_VALIDATION",
])

PROMOTION_STATUSES = frozenset([
    "not_promoted",
    "promoted_to_integration",
    "promotion_failed",
])

NEXT_ACTIONS = frozenset([
    "run_task",
    "repair_task",
    "promote_task",
    "skip_task",
    "generate_run_summary",
    "prepare_pr",
    "request_human",
    "run_codex_review",  # triggers Codex review step (wired via run-codex-review command)
    "stop",
])

HUMAN_ACTION_REASONS = frozenset([
    "scope_expansion_required",
    "forbidden_file_required",
    "repair_limit_exceeded",
    "safety_invariant_failed",
    "merge_authorization_required",
    "codex_artifact_required",  # finalization guard requires Codex evidence
    "ambiguous_task_decision",
    "external_system_failure",
])


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_MAX_LOCAL_REPAIR = 3
DEFAULT_MAX_CODEX_REPAIR = 2
DEFAULT_MAX_CI_REPAIR = 2
DEFAULT_MAX_SCOPE_EXPANSION = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_state(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        print(f"ERROR: state file not found: {path}", file=sys.stderr)
        sys.exit(1)
    try:
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"ERROR: failed to load state from {path}: {e}", file=sys.stderr)
        sys.exit(1)


def _save_state(state: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def _load_bundle_index(path: Optional[str]) -> Optional[dict]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def _load_tasks_jsonl(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        print(f"ERROR: TASKS.jsonl not found: {path}", file=sys.stderr)
        sys.exit(1)
    tasks = []
    with open(p) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                tasks.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"ERROR: invalid JSON at line {lineno} of {path}: {e}", file=sys.stderr)
                sys.exit(1)
    if not tasks:
        print(f"ERROR: TASKS.jsonl is empty: {path}", file=sys.stderr)
        sys.exit(1)
    return tasks


def _parse_repair_source(source: str) -> str:
    valid = frozenset(["local_gate", "codex", "ci", "scope_check", "finalization_guard"])
    if source not in valid:
        print(f"ERROR: repair source must be one of {sorted(valid)}, got: {source}", file=sys.stderr)
        sys.exit(1)
    return source


# ---------------------------------------------------------------------------
# State machine logic
# ---------------------------------------------------------------------------

def _build_task_entry(task: dict, integration_plan: Optional[dict]) -> dict:
    """Convert a TASKS.jsonl entry (or BUNDLE_INDEX task) into a controller task record."""
    task_id = task.get("task_id") or task.get("id")
    if not task_id:
        print("ERROR: task entry missing 'task_id' or 'id' field", file=sys.stderr)
        sys.exit(1)

    depends_on = task.get("depends_on", [])
    blocks = task.get("blocks", [])
    promotion_group = task.get("promotion_group")
    pr_group = task.get("pr_group")
    integration_order = task.get("integration_order")
    can_run_in_parallel = task.get("can_run_in_parallel", False)
    promotion_target = task.get("promotion_target")

    # Determine initial dependency_status
    if not depends_on:
        dependency_status = "satisfied"
    else:
        dependency_status = "unsatisfied"

    return {
        "task_id": str(task_id),
        "status": "TASK_PENDING",
        "dependency_status": dependency_status,
        "promotion_status": "not_promoted",
        "local_gate_status": "not_run",
        "scope_status": "not_run",
        "repair_attempts": 0,
        "max_repair_attempts": DEFAULT_MAX_LOCAL_REPAIR,
        "blocker_code": None,
        "blocker_summary": None,
        "bundle_path": None,
        "depends_on": [str(d) for d in depends_on],
        "blocks": [str(b) for b in blocks],
        "promotion_group": promotion_group,
        "pr_group": pr_group,
        "integration_order": integration_order,
        "can_run_in_parallel": can_run_in_parallel,
        "promotion_target": promotion_target,
        "repair_history": [],
    }


def _resolve_dependency_status(
    task_id: str,
    tasks: list[dict],
    completed_task_ids: set[str],
) -> str:
    """
    A task's dependency_status is 'satisfied' if all its `depends_on` tasks
    are in `completed_task_ids`. Otherwise 'blocked_by_dependency'.
    """
    entry = next((t for t in tasks if t["task_id"] == task_id), None)
    if not entry:
        return "satisfied"  # treat unknown as satisfied to avoid blocking
    deps = entry.get("depends_on", [])
    if not deps:
        return "satisfied"
    if all(d in completed_task_ids for d in deps):
        return "satisfied"
    return "blocked_by_dependency"


def _update_dependency_chain(tasks: list[dict], completed_task_ids: set[str]) -> list[dict]:
    """
    Recompute dependency_status for all tasks after a state change.
    Also marks downstream tasks as blocked if their dependency is blocked.
    """
    for task in tasks:
        task["dependency_status"] = _resolve_dependency_status(
            task["task_id"], tasks, completed_task_ids
        )
    return tasks


def _compute_next_action(state: dict) -> dict:
    """
    Core state-machine logic: determine what to do next.

    Returns a dict with keys: action, task_id, reason
    """
    if state.get("overall_status") in ("RUN_COMPLETE", "RUN_FAILED_SAFETY", "RUN_INVALID"):
        return {"action": "stop", "task_id": None, "reason": "run in terminal state"}

    tasks = state.get("tasks", [])
    if not tasks:
        return {"action": "stop", "task_id": None, "reason": "no tasks defined"}

    # Collect sets
    pending = {t["task_id"] for t in tasks if t["status"] == "TASK_PENDING"}
    ready = {t["task_id"] for t in tasks if t["status"] == "TASK_READY"}
    promoted = {t["task_id"] for t in tasks if t["promotion_status"] == "promoted_to_integration"}
    blocked = {t["task_id"] for t in tasks if t["status"] == "TASK_BLOCKED"}
    skipped = {t["task_id"] for t in tasks if t["status"] == "TASK_SKIPPED"}
    failed = {t["task_id"] for t in tasks if t["status"] == "TASK_FAILED_VALIDATION"}

    # Completed = promoted + ready (TASK_READY means ready for summary/pr)
    completed = promoted | ready

    # Recompute dependency_status for all pending tasks
    all_task_ids = {t["task_id"] for t in tasks}

    # Tasks that are runnable: pending + dependency satisfied
    runnable = set()
    for tid in pending:
        entry = next((t for t in tasks if t["task_id"] == tid), None)
        if not entry:
            continue
        dep_status = _resolve_dependency_status(tid, tasks, completed)
        entry["dependency_status"] = dep_status
        if dep_status == "satisfied":
            runnable.add(tid)

    # Safety check
    si = state.get("safety_invariants", {})
    if any(si.get(k) for k in ("hermes_touched", "dispatch_occurred", "production_board_touched")):
        state["overall_status"] = "RUN_FAILED_SAFETY"
        return {
            "action": "stop",
            "task_id": None,
            "reason": "safety invariant violated",
        }

    # All tasks done or skipped/failed → generate summary
    non_skipped = all_task_ids - skipped - failed
    if completed >= non_skipped and completed:
        state["overall_status"] = "RUN_READY_FOR_SUMMARY"
        return {
            "action": "generate_run_summary",
            "task_id": None,
            "reason": "all non-skipped tasks are promoted or ready",
        }

    # No runnable tasks but some pending → blocked
    if not runnable and pending:
        # Check if any repair is possible
        any_repairable = any(
            t["status"] == "TASK_BLOCKED" and t["repair_attempts"] < t["max_repair_attempts"]
            for t in tasks
        )
        if any_repairable:
            # Find first repairable blocked task
            for t in tasks:
                if t["status"] == "TASK_BLOCKED" and t["repair_attempts"] < t["max_repair_attempts"]:
                    return {
                        "action": "repair_task",
                        "task_id": t["task_id"],
                        "reason": f"blocked task with repair attempts remaining (attempt {t['repair_attempts']+1})",
                    }
        # None repairable → need human
        return {
            "action": "request_human",
            "task_id": None,
            "reason": "no runnable tasks and no repairable blocked tasks",
        }

    if not runnable:
        return {
            "action": "stop",
            "task_id": None,
            "reason": "no runnable tasks remain",
        }

    # Pick first runnable task (by integration_order if available, else order in list)
    runnable_tasks = [t for t in tasks if t["task_id"] in runnable]
    # Sort by integration_order (None sorts last), then by list order
    runnable_tasks.sort(key=lambda t: (t.get("integration_order") is None, t.get("integration_order") or 0, tasks.index(t)))
    chosen = runnable_tasks[0]

    # Check repair limit
    if chosen["repair_attempts"] >= chosen["max_repair_attempts"]:
        return {
            "action": "request_human",
            "task_id": chosen["task_id"],
            "reason": f"repair limit exceeded for {chosen['task_id']} ({chosen['repair_attempts']} attempts)",
        }

    return {
        "action": "run_task",
        "task_id": chosen["task_id"],
        "reason": "next dependency-satisfied pending task",
    }


def _init(args: argparse.Namespace) -> None:
    # Load tasks
    tasks_data = _load_tasks_jsonl(args.tasks_jsonl)

    # Load optional BUNDLE_INDEX
    bundle_index = _load_bundle_index(args.bundle_index)

    # Determine ordered task IDs
    if bundle_index:
        plan = bundle_index.get("integration_plan", {})
        ordered_ids = plan.get("ordered_task_ids", [])
    else:
        ordered_ids = [t.get("task_id") or t.get("id") for t in tasks_data]

    # Build task records in dependency-satisfied order
    task_map: dict[str, dict] = {}
    for t in tasks_data:
        tid = t.get("task_id") or t.get("id")
        if not tid:
            continue
        task_map[str(tid)] = _build_task_entry(t, bundle_index)

    # Reorder to match ordered_ids (any missing from ordered_ids go last)
    ordered_tasks: list[dict] = []
    for tid in ordered_ids:
        if tid in task_map:
            ordered_tasks.append(task_map.pop(tid))
    # Append any tasks not in ordered_ids (e.g., from TASKS.jsonl not in BUNDLE_INDEX)
    ordered_tasks.extend(list(task_map.values()))

    # Apply dependency ordering: ensure depends_on tasks come before dependents
    # Topological sort within ordered_tasks using dependency_edges
    if bundle_index:
        dep_edges = bundle_index.get("integration_plan", {}).get("dependency_edges", [])
    else:
        dep_edges = []

    edge_map: dict[str, list[str]] = {}
    for edge in dep_edges:
        frm = edge.get("from") or edge.get("from_task_id")
        to = edge.get("to") or edge.get("to_task_id")
        if frm and to:
            edge_map.setdefault(frm, []).append(to)

    def topological_sort(tasks_list: list[dict]) -> list[dict]:
        visited = set()
        result = []
        def _visit(t: dict):
            if t["task_id"] in visited:
                return
            visited.add(t["task_id"])
            for dep in t.get("depends_on", []):
                dep_t = next((x for x in tasks_list if x["task_id"] == dep), None)
                if dep_t:
                    _visit(dep_t)
            result.append(t)
        for t in tasks_list:
            _visit(t)
        return result

    ordered_tasks = topological_sort(ordered_tasks)

    # Initialize state
    state: dict = {
        "controller_version": 1,
        "run_id": args.run_id,
        "created_at": _utcnow(),
        "updated_at": _utcnow(),
        "workspace": str(Path(args.workspace).resolve()),
        "integration_branch": args.integration_branch,
        "overall_status": "RUN_ACTIVE",
        "tasks": ordered_tasks,
        "repair_events": [],
        "pr_results": [],
        "human_action_required": False,
        "next_action": {
            "action": "run_task",
            "task_id": ordered_tasks[0]["task_id"] if ordered_tasks else None,
            "reason": "initial task selection",
        },
        "safety_invariants": {
            "hermes_touched": False,
            "dispatch_occurred": False,
            "production_board_touched": False,
            "memory_or_profile_updated": False,
            "skills_created": False,
        },
    }

    if args.output_state:
        out_path = args.output_state
    else:
        out_path = str(Path(args.workspace) / "CONTROLLER_STATE.json")

    _save_state(state, out_path)

    print(f"Initialized run controller state: {out_path}")
    print(f"  run_id:       {args.run_id}")
    print(f"  tasks:        {len(state['tasks'])}")
    print(f"  integration:  {args.integration_branch}")
    print(f"  status:       {state['overall_status']}")


def _status(args: argparse.Namespace) -> None:
    state = _load_state(args.state)

    output_md = args.output_md

    if output_md:
        lines = [
            "# AED Run Controller: Status",
            "",
            f"**Run:** `{state.get('run_id', '')}`",
            f"**Status:** `{state.get('overall_status', '')}`",
            f"**Workspace:** `{state.get('workspace', '')}`",
            f"**Integration branch:** `{state.get('integration_branch', '')}`",
            "",
            "## Next Action",
            "",
            f"- **Action:** `{state.get('next_action', {}).get('action', '')}`",
            f"- **Task:** `{state.get('next_action', {}).get('task_id', '—')}`",
            f"- **Reason:** {state.get('next_action', {}).get('reason', '')}",
            "",
            "## Safety Invariants",
            "",
        ]
        si = state.get("safety_invariants", {})
        for k, v in si.items():
            lines.append(f"- `{k}`: {'⚠️ true' if v else '✅ false'}")
        lines += ["", "## Task Table", ""]
        lines.append("| Task | Status | Promotion | Dep | Gate | Scope | Repairs | Blocker |")
        lines.append("|------|--------|-----------|-----|------|-------|---------|---------|")
        for t in state.get("tasks", []):
            repair_info = f"{t.get('repair_attempts', 0)}/{t.get('max_repair_attempts', 0)}"
            blocker = t.get("blocker_summary", "") or t.get("blocker_code", "") or "—"
            lines.append(
                f"| `{t['task_id']}` | {t['status']} | "
                f"{t['promotion_status']} | {t.get('dependency_status','satisfied')} | "
                f"{t.get('local_gate_status','not_run')} | {t.get('scope_status','not_run')} | "
                f"{repair_info} | {blocker} |"
            )
        with open(output_md, "w") as f:
            f.write("\n".join(lines))
        print(f"Status written to: {output_md}")
    else:
        # JSON to stdout
        print(json.dumps(state, indent=2))


def _next(args: argparse.Namespace) -> None:
    state = _load_state(args.state)

    # Recompute next action
    next_action = _compute_next_action(state)

    # Update state
    state["next_action"] = next_action
    state["updated_at"] = _utcnow()
    state["human_action_required"] = (next_action["action"] == "request_human")

    _save_state(state, args.state)

    if args.output_md:
        action = next_action["action"]
        task_id = next_action.get("task_id") or "—"
        reason = next_action.get("reason", "")
        lines = [
            "# AED Run Controller: Next Action",
            "",
            f"**Run:** `{state.get('run_id', '')}`",
            f"**Status:** `{state.get('overall_status', '')}`",
            f"**Next action:** `{action}`",
            f"**Task:** `{task_id}`",
            f"**Reason:** {reason}",
            "",
            "## Operator Instruction",
            "",
        ]
        if action == "run_task":
            lines.append(f"Run task `{task_id}` on the `{state.get('integration_branch')}` branch.")
            lines.append("After completion, call `record-task-result`.")
        elif action == "repair_task":
            lines.append(f"Attempt repair on task `{task_id}`.")
            lines.append("After fixing, call `record-repair-result`.")
        elif action == "generate_run_summary":
            lines.append("All tasks promoted. Generate run summary using `build_autocoder_run_summary.py`.")
            lines.append("After review, call `finalize-run` to close the run.")
        elif action == "request_human":
            lines.append(f"Human intervention required: {reason}")
            lines.append("Resolve the issue, then call `record-task-result` or `record-repair-result`.")
        elif action == "stop":
            lines.append(f"Run is stopped: {reason}")
        with open(args.output_md, "w") as f:
            f.write("\n".join(lines))
        print(f"Next action written to: {args.output_md}")
    else:
        print(json.dumps(next_action, indent=2))


def _record_task_result(args: argparse.Namespace) -> None:
    state = _load_state(args.state)

    task_id = args.task_id
    new_status = args.status
    promotion_status = args.promotion_status
    local_gate = args.local_gate
    scope_status = args.scope_status
    bundle_path = args.bundle_path

    if new_status not in TASK_STATUSES:
        print(f"ERROR: status must be one of {sorted(TASK_STATUSES)}, got: {new_status}", file=sys.stderr)
        sys.exit(1)
    if promotion_status not in PROMOTION_STATUSES:
        print(f"ERROR: promotion_status must be one of {sorted(PROMOTION_STATUSES)}, got: {promotion_status}", file=sys.stderr)
        sys.exit(1)

    task_entry = next((t for t in state["tasks"] if t["task_id"] == task_id), None)
    if not task_entry:
        print(f"ERROR: task not found in state: {task_id}", file=sys.stderr)
        sys.exit(1)

    # Update task
    task_entry["status"] = new_status
    task_entry["promotion_status"] = promotion_status
    task_entry["local_gate_status"] = local_gate or task_entry.get("local_gate_status", "not_run")
    task_entry["scope_status"] = scope_status or task_entry.get("scope_status", "not_run")
    if bundle_path:
        task_entry["bundle_path"] = bundle_path

    # If TASK_BLOCKED, mark for repair
    if new_status == "TASK_BLOCKED":
        task_entry["blocker_code"] = args.blocker_code
        task_entry["blocker_summary"] = args.blocker_summary

    # If TASK_READY or promoted, clear blocked status
    if new_status in ("TASK_READY", "TASK_SKIPPED"):
        task_entry["blocker_code"] = None
        task_entry["blocker_summary"] = None

    state["updated_at"] = _utcnow()

    # Recompute completed set and dependency chain
    promoted = {t["task_id"] for t in state["tasks"] if t["promotion_status"] == "promoted_to_integration"}
    ready = {t["task_id"] for t in state["tasks"] if t["status"] == "TASK_READY"}
    completed = promoted | ready

    state["tasks"] = _update_dependency_chain(state["tasks"], completed)

    # Recompute next action
    next_action = _compute_next_action(state)
    state["next_action"] = next_action
    state["human_action_required"] = (next_action["action"] == "request_human")

    _save_state(state, args.state)

    print(f"Recorded result for task `{task_id}`")
    print(f"  status: {new_status}")
    print(f"  promotion: {promotion_status}")
    print(f"  next action: {next_action['action']} — {next_action['reason']}")


def _record_repair_result(args: argparse.Namespace) -> None:
    state = _load_state(args.state)

    task_id = args.task_id
    repair_id = args.repair_id
    source = _parse_repair_source(args.source)
    repair_status = args.status
    summary = args.summary

    if repair_status not in ("repaired", "failed"):
        print(f"ERROR: repair status must be 'repaired' or 'failed', got: {repair_status}", file=sys.stderr)
        sys.exit(1)

    task_entry = next((t for t in state["tasks"] if t["task_id"] == task_id), None)
    if not task_entry:
        print(f"ERROR: task not found in state: {task_id}", file=sys.stderr)
        sys.exit(1)

    # Record repair event
    repair_event = {
        "repair_id": repair_id,
        "task_id": task_id,
        "source": source,
        "status": repair_status,
        "summary": summary or "",
        "recorded_at": _utcnow(),
    }
    state["repair_events"].append(repair_event)

    # Update repair history on task
    repair_history = task_entry.get("repair_history", [])
    repair_history.append({
        "repair_id": repair_id,
        "source": source,
        "status": repair_status,
        "summary": summary or "",
        "recorded_at": repair_event["recorded_at"],
    })
    task_entry["repair_history"] = repair_history

    if repair_status == "repaired":
        task_entry["repair_attempts"] = task_entry.get("repair_attempts", 0) + 1
        # After repair, task should be re-evaluated; set to TASK_PENDING to pick up again
        if task_entry["status"] in ("TASK_BLOCKED", "TASK_FAILED_VALIDATION"):
            task_entry["status"] = "TASK_PENDING"
    else:
        # Failed repair
        task_entry["repair_attempts"] = task_entry.get("repair_attempts", 0) + 1
        # Check if limit exceeded
        if task_entry["repair_attempts"] >= task_entry["max_repair_attempts"]:
            task_entry["status"] = "TASK_BLOCKED"
            task_entry["blocker_code"] = "repair_limit_exceeded"
            task_entry["blocker_summary"] = (
                f"Repair limit ({task_entry['max_repair_attempts']}) exceeded for {task_id}; "
                f"human intervention required."
            )

    state["updated_at"] = _utcnow()

    # Recompute dependency chain
    promoted = {t["task_id"] for t in state["tasks"] if t["promotion_status"] == "promoted_to_integration"}
    ready = {t["task_id"] for t in state["tasks"] if t["status"] == "TASK_READY"}
    completed = promoted | ready
    state["tasks"] = _update_dependency_chain(state["tasks"], completed)

    # Recompute next action
    next_action = _compute_next_action(state)
    state["next_action"] = next_action
    state["human_action_required"] = (next_action["action"] == "request_human")

    _save_state(state, args.state)

    print(f"Recorded repair for task `{task_id}`")
    print(f"  repair_id: {repair_id}")
    print(f"  status: {repair_status}")
    print(f"  total attempts: {task_entry['repair_attempts']}/{task_entry['max_repair_attempts']}")
    print(f"  next action: {next_action['action']} — {next_action['reason']}")


def _record_pr_result(args: argparse.Namespace) -> None:
    state = _load_state(args.state)

    pr_result = {
        "pr_number": args.pr_number,
        "status": args.status,
        "url": args.url or "",
        "head_sha": args.head_sha or "",
        "merge_sha": args.merge_sha or "",
        "recorded_at": _utcnow(),
    }
    state["pr_results"].append(pr_result)
    state["updated_at"] = _utcnow()

    _save_state(state, args.state)

    print(f"Recorded PR result: #{args.pr_number} — {args.status}")
    print(f"  next action: {state['next_action']['action']} — {state['next_action']['reason']}")


def _run_codex_review(args: argparse.Namespace) -> None:
    """Record that a Codex review step is in progress.

    This command is called when the finalization guard returns WAIT with
    codex_status.passing=False (codex_artifact_required). The operator runs
    Codex review and records the review session here before providing the artifact.
    After the review is complete, the operator reruns the finalization guard with
    the artifact to determine the final recommendation.
    """
    state = _load_state(args.state)

    state["codex_review"] = {
        "status": "in_progress",
        "reason": args.reason,
        "summary": args.summary or "",
        "recorded_at": _utcnow(),
    }
    state["updated_at"] = _utcnow()
    state["human_action_required"] = True
    state["next_action"] = {
        "action": "run_codex_review",
        "task_id": None,
        "reason": args.reason,
    }

    _save_state(state, args.state)

    print(f"Codex review in progress — reason: {args.reason}")
    print("Complete the review, then rerun the finalization guard with --codex-artifact.")


def _finalize_run(args: argparse.Namespace) -> None:
    state = _load_state(args.state)

    state["overall_status"] = "RUN_COMPLETE"
    state["updated_at"] = _utcnow()
    state["next_action"] = {"action": "stop", "task_id": None, "reason": "run finalized"}
    state["human_action_required"] = False

    _save_state(state, args.state)

    print(f"Run finalized: {state.get('run_id', 'unknown')}")
    print(f"  final status: RUN_COMPLETE")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AED Autocoder Run Controller v0 — state machine for AED patch runs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = sub.add_parser("init", help="Initialize a new run controller state")
    p_init.add_argument("--run-id", required=True, help="Unique run identifier, e.g. aed-run-001")
    p_init.add_argument("--tasks-jsonl", required=True, help="Path to TASKS.jsonl")
    p_init.add_argument("--bundle-index", help="Path to BUNDLE_INDEX.json (optional)")
    p_init.add_argument("--workspace", required=True, help="Working directory for this run")
    p_init.add_argument("--integration-branch", required=True, help="Integration branch name")
    p_init.add_argument("--output-state", help="Output state file path (default: <workspace>/CONTROLLER_STATE.json)")

    # status
    p_status = sub.add_parser("status", help="Show current run controller state")
    p_status.add_argument("--state", required=True, help="Path to CONTROLLER_STATE.json")
    p_status.add_argument("--output-md", help="Write status as Markdown to this path")

    # next
    p_next = sub.add_parser("next", help="Compute and record the next action")
    p_next.add_argument("--state", required=True, help="Path to CONTROLLER_STATE.json")
    p_next.add_argument("--output-md", help="Write next action as Markdown to this path")

    # record-task-result
    p_rec = sub.add_parser("record-task-result", help="Record a task execution result")
    p_rec.add_argument("--state", required=True, help="Path to CONTROLLER_STATE.json")
    p_rec.add_argument("--task-id", required=True, help="Task ID")
    p_rec.add_argument("--status", required=True,
                        choices=sorted(TASK_STATUSES), help="New task status")
    p_rec.add_argument("--promotion-status", required=True,
                        choices=sorted(PROMOTION_STATUSES), help="Promotion status")
    p_rec.add_argument("--local-gate", help="Local gate result (passed/failed/not_run)")
    p_rec.add_argument("--scope-status", help="Scope status (clean/dirty/not_run)")
    p_rec.add_argument("--bundle-path", help="Path to task bundle directory")
    p_rec.add_argument("--blocker-code", help="Blocker code if status is TASK_BLOCKED")
    p_rec.add_argument("--blocker-summary", help="Human-readable blocker summary")

    # record-repair-result
    p_rep = sub.add_parser("record-repair-result", help="Record a repair attempt result")
    p_rep.add_argument("--state", required=True, help="Path to CONTROLLER_STATE.json")
    p_rep.add_argument("--task-id", required=True, help="Task ID")
    p_rep.add_argument("--repair-id", required=True, help="Repair attempt identifier, e.g. task-id.R1")
    p_rep.add_argument("--source", required=True,
                        choices=["local_gate", "codex", "ci", "scope_check", "finalization_guard"],
                        help="What triggered the repair")
    p_rep.add_argument("--status", required=True, choices=["repaired", "failed"],
                        help="Repair outcome")
    p_rep.add_argument("--summary", help="Brief description of what was done")

    # record-pr-result
    p_pr = sub.add_parser("record-pr-result", help="Record a PR creation/merge result")
    p_pr.add_argument("--state", required=True, help="Path to CONTROLLER_STATE.json")
    p_pr.add_argument("--pr-number", type=int, required=True, help="PR number")
    p_pr.add_argument("--status", required=True, help="PR status (opened/merged/closed)")
    p_pr.add_argument("--url", help="PR URL")
    p_pr.add_argument("--head-sha", help="PR head commit SHA")
    p_pr.add_argument("--merge-sha", help="Merge commit SHA (if merged)")

    # run-codex-review
    p_codex = sub.add_parser("run-codex-review", help="Record that a Codex review is in progress")
    p_codex.add_argument("--state", required=True, help="Path to CONTROLLER_STATE.json")
    p_codex.add_argument("--reason", default="codex_artifact_required",
                        help="Reason for Codex review (default: codex_artifact_required)")
    p_codex.add_argument("--summary", help="Brief summary of what triggered the review")

    # finalize-run
    p_fin = sub.add_parser("finalize-run", help="Mark run as complete")
    p_fin.add_argument("--state", required=True, help="Path to CONTROLLER_STATE.json")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "init": _init,
        "status": _status,
        "next": _next,
        "record-task-result": _record_task_result,
        "record-repair-result": _record_repair_result,
        "record-pr-result": _record_pr_result,
        "run-codex-review": _run_codex_review,
        "finalize-run": _finalize_run,
    }

    try:
        dispatch[args.command](args)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())