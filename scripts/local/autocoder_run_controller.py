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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

# Round-70 PHASE 3-P1: bring the standalone planner and runner
# into this controller's namespace without introducing
# side-effect imports at module load. The seam is a dict the
# production flow uses by default and tests can swap.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Round-120: run-identity, supervisor-lock, mutation-authorization,
# and launch-receipt modules. These are imported here (not at top
# of file) so existing tests that import symbols from this module
# continue to load without circular dependencies.
from scripts.local import (
    aed_run_identity as _run_identity,
    aed_supervisor_lock as _supervisor_lock,
    aed_mutation_authorization as _mutation_auth,
    aed_launch_receipt as _launch_receipt,
)
from scripts.local.aed_supervisor_lock import LockOutcome
from scripts.local.aed_pr_lib import is_full_sha as _is_full_sha


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
    "codex_repair_limit_exceeded",
    "same_codex_blocker_repeated",
    "persistent_mutation_detected",   # persistent mutation guard found unexpected Hermes mutations
    "persistent_mutation_guard_error",  # guard report missing or malformed
])


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_MAX_LOCAL_REPAIR = 3
DEFAULT_MAX_CODEX_REPAIR = 2
DEFAULT_MAX_CI_REPAIR = 2
DEFAULT_MAX_SCOPE_EXPANSION = 0

CODEX_REVIEW_STATUSES = frozenset([
    "not_started",
    "in_progress",
    "clean",
    "findings",
    "blocked",
    "repair_limit_exceeded",
])

SEVERITY_ORDER = ["none", "P3", "P2", "P1", "HIGH"]

_CODEX_REPAIR_SENSITIVE_KEYWORDS = frozenset([
    "dependency", "install", "npm", "pip", "cargo", "gem", "package",
    "auth", "credential", "secret", "api_key", "apikey", "password", "token",
    "payment", "billing", "stripe", "charge", "invoice",
    "production_board", "board:aed", "aed",
    "dispatch", "hermes create", "hermes dispatch",
    "memory", "profile", "skill",
    ".hermes", "~/.hermes", "/hermes",
])


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
    """Write state atomically with restrictive permissions (0o600 on POSIX)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp_path = p.with_suffix(p.suffix + ".tmp")
    fd = os.open(
        str(tmp_path),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC,
        0o600,
    )
    try:
        # Round-11 P2 fix: when <path>.tmp already exists with
        # broader perms (e.g. 0o644 left over from an older or
        # externally interrupted writer), O_CREAT preserves the
        # existing mode. fchmod to 0o600 so the eventual os.replace
        # publishes the new state with restrictive perms.
        try:
            os.fchmod(fd, 0o600)
        except (OSError, NotImplementedError):
            pass
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, p)
    except Exception:
        # Best-effort cleanup of the temp file.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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
        "codex_review": {
            "status": "not_started",
            "head_sha": None,
            "artifact_path": None,
            "findings_count": 0,
            "highest_severity": "none",
            "repair_attempts": 0,
            "max_repair_attempts": DEFAULT_MAX_CODEX_REPAIR,
            "same_blocker_count": 0,
            "last_blocker_fingerprint": None,
        },
        "codex_repair_events": [],
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
        "persistent_mutation_guard": {
            "status": "not_started",  # not_started | snapshot_recorded | clean | blocked | error
            "root": "/home/max/.hermes",
            "snapshot_path": None,
            "compare_json_path": None,
            "compare_md_path": None,
            "blocked_changes_count": 0,
            "allowed_changes_count": 0,
            "last_checked_at": None,
        },
    }

    if args.output_state:
        out_path = args.output_state
    else:
        out_path = str(Path(args.workspace) / "CONTROLLER_STATE.json")
    # Round-11 P2 fix: resolve --output-state to an absolute path
    # BEFORE persisting it in state and the launch receipt. The
    # supervisor lease already resolves the same path. Without
    # this, a relative path becomes relative to the LATER
    # process's CWD when authorize-mutation validates the
    # receipt's state_path field against args.state.
    out_path = str(Path(out_path).resolve())

    # Record the launch time for the run identity (separate from
    # created_at on the state, which is used as a last-write timestamp).
    state["run_identity"] = None  # filled in below after lock acquisition

    # Round-120 P1 fix (round 3): do NOT persist the runnable state
    # before acquiring the lock. We build the full state in memory,
    # attempt lock acquisition, and only _save_state after the
    # lock has been acquired. If lock acquisition fails, we exit
    # without ever writing CONTROLLER_STATE.json so a competing
    # run cannot see a half-initialized state.

    # Round-120: acquire a host-local supervisor lock scoped to
    # (repository, target_pr_number) when provided. The lock is
    # only acquired when those scopes are explicit; otherwise we
    # skip locking and only persist the launch receipt.
    lock_outcome = None
    lock_path_str: Optional[str] = None
    scope: Optional[dict] = None
    # Round-120 P1 fix: use the host-wide lock directory by
    # default. The host-wide dir is derived from the host alone
    # (XDG_RUNTIME_DIR/aed/locks or ~/.aed/locks), so two runs for
    # the same (repository, PR) under different workspaces still
    # collide on a single lock file. Tests pass an explicit
    # --lock-dir to isolate.
    lock_dir_arg: Optional[str] = getattr(args, "lock_dir", None)
    lock_base: Optional[Path] = None
    # Round-20 P2 fix (Require a repository for mutation-target
    # locking): --mutation-target without --repository must be
    # rejected at init time regardless of whether the run is
    # otherwise PR-scoped. The previous code accepted this
    # partial scope when neither --repository nor
    # --target-pr-number was set, built a lease with empty
    # repository, and authorize-mutation later fell back to
    # workspace-scope and skipped lease ownership validation.
    # Two controllers in different workspaces could then
    # concurrently mutate the same real target. Fail closed at
    # init time. This check must run BEFORE the
    # repository-or-target_pr_number branch so it fires even
    # when neither is set.
    if getattr(args, "mutation_target", None) and not getattr(args, "repository", None):
        print(
            "ERROR: --mutation-target requires --repository "
            "(a partial scope is not permitted for lock ownership)",
            file=sys.stderr,
        )
        sys.exit(14)
    if getattr(args, "repository", None) or getattr(args, "target_pr_number", None):
        # Round-10 P2 fix: refuse a PR-scoped lock without an
        # explicit repository. The previous code accepted
        # --target-pr-number N without --repository, built a
        # scope with empty repository, and skipped
        # ownership-validation checks downstream. Reject this
        # partial scope here.
        if getattr(args, "target_pr_number", None) and not getattr(args, "repository", None):
            print(
                "ERROR: --target-pr-number requires --repository "
                "(a partial scope is not permitted for lock ownership)",
                file=sys.stderr,
            )
            sys.exit(14)
        scope = {
            "repository": getattr(args, "repository", None) or "",
            "target_pr_number": getattr(args, "target_pr_number", None),
            "mutation_target": getattr(args, "mutation_target", None),
        }
        proc_evidence = _run_identity.capture_process_start_evidence()
        host_identity = _run_identity.capture_host_identity()
        # proc_evidence is guaranteed non-None on POSIX; guard defensively.
        owner_pid = int(proc_evidence["pid"]) if proc_evidence else os.getpid()
        owner_start_evidence = proc_evidence or {
            "pid": owner_pid,
            "stat_start_time": None,
            "stat_start_time_text": None,
            "ctime_ns": None,
            "source": "unknown",
        }
        if lock_dir_arg:
            lock_base = Path(lock_dir_arg)
        # Round-9 P1 fix (Make recovered lease adoptable by init):
        # when --replace-stale-lock is set and try_acquire fails
        # because the existing lease is stale, attempt inline
        # recovery before exiting. The recovered lease is bound
        # to THIS init's --output-state so the state file becomes
        # the lease's owner_state_path (alive evidence) once init
        # persists it.
        lock_outcome = _supervisor_lock.try_acquire(
            scope=scope,
            owner_run_id=args.run_id,
            owner_host=host_identity,
            owner_pid=owner_pid,
            owner_start_evidence=owner_start_evidence,
            owner_state_path=str(Path(out_path).resolve()),
            base_dir=lock_base,
        )
        if (
            not lock_outcome.ok
            and getattr(args, "replace_stale_lock", False)
            and lock_outcome.reason
            and (
                lock_outcome.reason.startswith("stale_lock_detected:")
                or lock_outcome.reason.startswith("indeterminate_liveness:indeterminate:state_path_missing")
                or lock_outcome.reason.startswith("indeterminate_liveness:indeterminate:state_unreadable")
                or lock_outcome.reason.startswith("corrupt_existing_lease_recovery_required")
                or lock_outcome.reason.startswith("stale:state_terminal:")
            )
        ):
            print(
                f"NOTE: stale lease detected, recovering inline: "
                f"{lock_outcome.reason}",
                file=sys.stderr,
            )
            # Round-10 P1 fix: write a stub state file at the
            # lease's owner_state_path BEFORE recovering. The
            # recovered lease will be bound to this path, and the
            # state file MUST exist for lease-based liveness to
            # succeed on the next try_acquire. Without this stub,
            # the retry returns state_path_missing indeterminate
            # and the init fails.
            #
            # Round-11 P1 fix (Serialize replacement stubs with
            # stale-lock recovery): two concurrent
            # init --replace-stale-lock processes share an
            # output path. The second process's unconditional
            # stub write can overwrite the first's stub or
            # completed state. Hold the scope sentinel across
            # BOTH stub write AND recovery so the entire
            # publish+recover sequence is serialized. The inner
            # recover_stale call uses bypass_sentinel=True to
            # avoid deadlock against the outer hold.
            lock_path = _supervisor_lock.lock_path_for(
                _supervisor_lock.build_scope_key(
                    repository=scope["repository"],
                    target_pr_number=scope.get("target_pr_number"),
                    mutation_target=scope.get("mutation_target"),
                ),
                base_dir=lock_base,
            )
            sentinel_path = lock_path.with_suffix(lock_path.suffix + ".recovery-sentinel")
            from scripts.local.aed_supervisor_lock import (
                _acquire_sentinel_fd,
                _release_sentinel_fd,
            )
            scope_sentinel_fd = _acquire_sentinel_fd(sentinel_path, max_attempts=20)
            if scope_sentinel_fd is None:
                print(
                    "ERROR: inline stale-lock recovery: scope sentinel busy",
                    file=sys.stderr,
                )
                sys.exit(2)
            try:
                # Round-14 P1 fix (Revalidate the predecessor
                # before writing the recovery stub): when this
                # replacement init observed a stale predecessor
                # but another initializer recovered the lease in the
                # gap, the lock owner has changed. The new owner
                # must NOT be silently overwritten with this run's
                # stub. Compare the post-sentinel lock against the
                # original lock_outcome.owner (the predecessor);
                # abort if they differ.
                existing_for_ownership = None
                try:
                    with open(lock_path) as f:
                        existing_for_ownership = json.load(f)
                except (OSError, json.JSONDecodeError):
                    pass
                predecessor_run_id = None
                predecessor_chain = -1
                if existing_for_ownership:
                    predecessor_run_id = existing_for_ownership.get("owner_run_id")
                    predecessor_chain = int(
                        existing_for_ownership.get("lock_version_chain", 0)
                    )
                if (
                    (lock_outcome.owner or "corrupt_predecessor_seen")
                    and existing_for_ownership
                    and (
                        existing_for_ownership.get("owner_run_id")
                        != (lock_outcome.owner or {}).get("owner_run_id", "<corrupt_predecessor>")
                        or int(existing_for_ownership.get("lock_version_chain", 0))
                        != int((lock_outcome.owner or {}).get("lock_version_chain", 0))
                    )
                ):
                    # Another initializer has changed the lease.
                    # Abort; do not overwrite the new owner.
                    print(
                        f"ERROR: inline stale-lock recovery: predecessor changed. "
                        f"Original owner={lock_outcome.owner.get('owner_run_id')!r}; "
                        f"current owner={existing_for_ownership.get('owner_run_id')!r}. "
                        f"Aborting.",
                        file=sys.stderr,
                    )
                    sys.exit(15)
                # Only write the stub if the lock belongs to the
                # predecessor or is unreadable (we can't tell). If
                # the lock's run_id already matches ours, another
                # inits already wrote the same path; skip stub
                # write to avoid clobbering the winner's state.
                if predecessor_run_id != args.run_id:
                    Path(out_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    # Round-16 P1 fix (Recheck liveness before
                    # overwriting predecessor state): the predecessor
                    # may have resumed and refreshed its state file
                    # between our initial stale observation and the
                    # sentinel acquisition. Re-assess liveness of
                    # the current lock under the sentinel; if it's
                    # now live, abort the recovery.
                    current_lock = existing_for_ownership or {}
                    if current_lock:
                        re_live = _supervisor_lock.assess_liveness(current_lock)
                        if re_live.is_alive:
                            print(
                                "ERROR: inline stale-lock recovery: "
                                "predecessor resumed during recovery. "
                                f"current_owner_run_id={current_lock.get('owner_run_id')!r}. "
                                "Aborting.",
                                file=sys.stderr,
                            )
                            sys.exit(15)
                    # Round-15 P1 fix (Keep the recovery stub
                    # non-live until receipts are published): use
                    # RUN_INVALID (a terminal status) instead of
                    # RUN_ACTIVE. If the process is killed between
                    # recovery and full init, _state_file_live
                    # classifies the lease as stale (terminal
                    # status) so replacement recovery works
                    # immediately. Once init completes successfully,
                    # the state is re-written with RUN_ACTIVE.
                    # Round-16 P2 fix (Create the recovery stub
                    # with restrictive permissions): Path.write_text
                    # uses the process umask (commonly 0o644); use
                    # safe_restrictive_open to ensure 0o600.
                    from scripts.local.aed_run_identity import safe_restrictive_open
                    fd = safe_restrictive_open(Path(out_path), "w")
                    try:
                        fd.write(json.dumps({
                            "controller_version": int(state["controller_version"]),
                            "run_id": args.run_id,
                            "workspace": args.workspace,
                            "overall_status": "RUN_INVALID",
                            "updated_at": _utcnow(),
                            "run_identity": {
                                "run_id": args.run_id,
                                "controller_version": int(state["controller_version"]),
                            },
                        }, indent=2, sort_keys=True) + "\n")
                        fd.flush()
                        os.fsync(fd.fileno())
                    finally:
                        fd.close()

                proc_evidence2 = _run_identity.capture_process_start_evidence() or owner_start_evidence
                host_identity2 = _run_identity.capture_host_identity()
                recovered = _supervisor_lock.recover_stale(
                    scope=scope,
                    recovered_by_run_id=args.run_id,
                    recovered_by_host=host_identity2,
                    recovered_by_pid=proc_evidence2["pid"],
                    recovered_by_start_evidence=proc_evidence2,
                    recovered_by_state_path=str(Path(out_path).resolve()),
                    staleness_evidence=f"init inline recovery for run_id={args.run_id}; "
                                        f"original reason={lock_outcome.reason}",
                    base_dir=lock_base,
                    bypass_indeterminate_state=True,
                    bypass_sentinel=True,
                    external_sentinel_fd=scope_sentinel_fd,
                )
            finally:
                _release_sentinel_fd(scope_sentinel_fd, sentinel_path)
            if not recovered.ok:
                # Round-10 P1 fix: roll back the stub state file
                # ONLY if it still belongs to this run.
                try:
                    if Path(out_path).exists():
                        try:
                            with open(out_path) as f:
                                existing_state = json.load(f)
                            if (existing_state.get("run_identity") or {}).get("run_id") == args.run_id:
                                os.unlink(out_path)
                        except (OSError, json.JSONDecodeError):
                            pass
                except OSError:
                    pass
                print(
                    f"ERROR: inline stale-lock recovery failed: {recovered.reason}",
                    file=sys.stderr,
                )
                sys.exit(2)
            # Round-9 P1 fix: the recovered lease IS our
            # acquisition.
            lock_outcome = LockOutcome(
                ok=True,
                path=recovered.path,
                owner=recovered.owner,
                reason="recovered_inline",
            )
        if not lock_outcome.ok:
            owner = lock_outcome.owner or {}
            print(
                f"ERROR: failed to acquire supervisor lock for scope "
                f"{scope}: {lock_outcome.reason}; "
                f"existing_owner_run_id={owner.get('owner_run_id')!r}, "
                f"existing_owner_pid={owner.get('owner_pid')!r}, "
                f"indeterminate={lock_outcome.indeterminate}",
                file=sys.stderr,
            )
            sys.exit(2)
        lock_path_str = str(lock_outcome.path)

    # Round-120: capture run identity and emit the launch receipt
    # BEFORE any controller command that performs a repository or
    # GitHub mutation may execute. Receipt emission is itself the
    # final bootstrap write and is the precondition for mutation
    # authorization.
    proc_evidence = _run_identity.capture_process_start_evidence()
    host_identity = _run_identity.capture_host_identity()
    run_identity = _run_identity.capture_run_identity(
        run_id=args.run_id,
        controller_version=int(state["controller_version"]),
        repository=getattr(args, "repository", None),
        target_pr_number=getattr(args, "target_pr_number", None),
        current_main_sha=getattr(args, "current_main_sha", None),
        starting_target_sha=getattr(args, "starting_target_sha", None),
        current_phase=str(state["overall_status"]),
        pending_action=str(state["next_action"]["action"]),
        merge_policy=getattr(args, "merge_policy", "stop_before_merge"),
    )
    # Round-120 P1 fix (round 2): persist mutation_target in the run
    # identity so _state_mutation_target returns the value used to
    # acquire the lock. capture_run_identity doesn't accept
    # mutation_target because it's not always present, so we set it
    # explicitly here.
    mutation_target_arg = getattr(args, "mutation_target", None)
    if mutation_target_arg:
        run_identity["mutation_target"] = mutation_target_arg
    # Overlay host/proc evidence (already captured by capture_run_identity).
    run_identity["host"] = host_identity
    run_identity["process"] = proc_evidence
    # Round-12 P2 fix: persist the resolved default lock
    # directory even when --lock-dir is omitted. When the lease
    # is later inspected via inspect-lock or recovered via
    # recover-stale-lock without --lock-dir, the controller must
    # find the same directory the init used. If the env var
    # AED_LOCK_DIR was in scope at init time, lock_dir was
    # implicit and the run_identity must record it. Round-13
    # fix: also persist the XDG-derived path when neither
    # --lock-dir nor AED_LOCK_DIR is set, so later commands find
    # the directory.
    lock_dir_env = os.environ.get("AED_LOCK_DIR")
    if lock_dir_arg:
        lock_dir_persisted = lock_dir_arg
    elif lock_dir_env:
        lock_dir_persisted = lock_dir_env
    else:
        # Resolve to the same default the lock module uses.
        from scripts.local.aed_supervisor_lock import default_lock_dir
        lock_dir_persisted = str(default_lock_dir(""))
    if lock_dir_persisted:
        run_identity["lock_dir"] = str(Path(lock_dir_persisted).resolve())

    state["run_identity"] = run_identity
    # Round-8 P1 fix: bootstrap artifacts must be published as a
    # transaction. The previous flow wrote state, then the
    # machine receipt, then the markdown receipt, but a failure
    # in any step left earlier artifacts on disk. For unscoped
    # runs (the explicitly supported flow), authorize-mutation
    # performs no lease check and would accept a state file
    # whose receipt write failed. Now: track each artifact and
    # roll back ALL of them on any failure before reporting
    # finalization to the operator.
    workspace = Path(args.workspace)
    receipt_json_path_predicted = workspace / _launch_receipt.RECEIPT_JSON_FILENAME
    receipt_md_path_predicted = workspace / _launch_receipt.RECEIPT_MD_FILENAME
    bootstrap_artifacts = {
        "state_path": out_path,
        "receipt_json_path": str(receipt_json_path_predicted),
        "receipt_md_path": str(receipt_md_path_predicted),
        "lock_dir": (str(Path(lock_dir_arg).resolve())
                     if lock_dir_arg else None),
    }
    lock_to_release_on_failure = lock_outcome
    try:
        # Round-14 P1 fix (Publish the runnable state only after
        # both receipts): emit BOTH receipts to disk BEFORE
        # publishing the runnable state. If either receipt fails,
        # the state is never visible and no recovery is needed.
        # On signal/host crash between receipts and state, the
        # runnable state file is still absent so a replacement
        # init sees "no state at this path" and proceeds.
        receipt_json_path, receipt_md_path = _launch_receipt.emit(
            workspace,
            run_identity=run_identity,
            state_path=out_path,
            lock_path=lock_path_str,
            pending_action=str(state["next_action"]["action"]),
            current_phase=str(state["overall_status"]),
            merge_policy=getattr(args, "merge_policy", "stop_before_merge"),
        )
        bootstrap_artifacts["receipt_json_path"] = (
            str(receipt_json_path) if receipt_json_path else None
        )
        bootstrap_artifacts["receipt_md_path"] = (
            str(receipt_md_path) if receipt_md_path else None
        )
        _save_state(state, out_path)
    except Exception as exc:
        # Round-14 P1 fix (Roll back artifacts before releasing
        # the supervisor lock): do NOT release the lock before
        # rolling back. Otherwise a waiting initializer could
        # acquire the freed lease in the gap, publish its own
        # state, and have it deleted by this failed initializer's
        # unconditional unlink. Keep the lock through rollback.
        try:
            for key in ("receipt_md_path", "receipt_json_path", "state_path"):
                path = bootstrap_artifacts.get(key)
                if not path:
                    continue
                # Round-14 P1 fix (Revalidate the predecessor
                # before writing the recovery stub) — also
                # applicable here: only unlink if the artifact
                # still belongs to THIS run by checking the
                # receipt's run_id or state's run_identity.run_id.
                try:
                    if key == "receipt_json_path":
                        try:
                            with open(path) as f:
                                existing = json.load(f)
                            rid = (existing.get("run_identity") or {}).get("run_id")
                            if rid == args.run_id:
                                os.unlink(path)
                        except (OSError, json.JSONDecodeError):
                            pass
                    elif key == "state_path":
                        try:
                            with open(path) as f:
                                existing = json.load(f)
                            rid = (existing.get("run_identity") or {}).get("run_id")
                            if rid == args.run_id:
                                os.unlink(path)
                        except (OSError, json.JSONDecodeError):
                            pass
                    else:
                        # For MD receipt, parse out the run ID.
                        try:
                            content = Path(path).read_text()
                            if f"**Run ID:** `{args.run_id}`" in content:
                                os.unlink(path)
                        except OSError:
                            pass
                except OSError:
                    pass
        except Exception:
            # Never let rollback errors mask the original failure.
            pass
        # Now release the lock (after rollback). Retry up to 5
        # times to handle transient sentinel holds.
        if lock_to_release_on_failure is not None and scope is not None:
            released = False
            for attempt in range(5):
                try:
                    released = _supervisor_lock.release(
                        scope=scope,
                        owner_run_id=args.run_id,
                        base_dir=lock_base,
                    )
                    if released:
                        break
                except Exception:
                    pass
                import time as _time
                _time.sleep(0.05)
            if not released:
                print(
                    "ERROR: init failed and could not release the "
                    "supervisor lock; the lock may be orphaned. "
                    "Manual recovery required.",
                    file=sys.stderr,
                )
        raise

    # Past this point, the lock is intentionally held for the
    # run's lifetime. Release on failure is no longer needed.
    lock_to_release_on_failure = None

    print(f"Initialized run controller state: {out_path}")
    print(f"  run_id:       {args.run_id}")
    print(f"  tasks:        {len(state['tasks'])}")
    print(f"  integration:  {args.integration_branch}")
    print(f"  status:       {state['overall_status']}")
    if lock_outcome is not None:
        print(f"  lock:         {lock_path_str}")
    print(f"  receipt:      {receipt_json_path}")
    print(f"  receipt.md:   {receipt_md_path}")


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

        guard = state.get("persistent_mutation_guard", {})
        if guard:
            lines += ["", "## Persistent Mutation Guard", ""]
            guard_status = guard.get("status", "unknown")
            status_icon = "✅" if guard_status == "clean" else ("⚠️" if guard_status in ("blocked", "error") else "🔶")
            lines.append(f"- **Status:** {status_icon} `{guard_status}`")
            lines.append(f"- **Root:** `{guard.get('root', '')}`")
            if guard.get("snapshot_path"):
                lines.append(f"- **Snapshot:** `{guard['snapshot_path']}`")
            if guard.get("compare_json_path"):
                lines.append(f"- **Compare JSON:** `{guard['compare_json_path']}`")
            if guard.get("compare_md_path"):
                lines.append(f"- **Compare MD:** `{guard['compare_md_path']}`")
            lines.append(f"- **Blocked changes:** `{guard.get('blocked_changes_count', 0)}`")
            lines.append(f"- **Allowed changes:** `{guard.get('allowed_changes_count', 0)}`")
            if guard.get("last_checked_at"):
                lines.append(f"- **Last checked:** `{guard['last_checked_at']}`")

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


def _record_codex_review(args: argparse.Namespace) -> None:
    """Record a Codex review result (clean, findings, or blocked).

    A clean review records head_sha and artifact_path.
    A findings review additionally records findings_count, highest_severity, and summary.
    A blocked review triggers request_human immediately.
    """
    state = _load_state(args.state)

    status = args.status
    if status not in CODEX_REVIEW_STATUSES:
        print(f"ERROR: status must be one of {sorted(CODEX_REVIEW_STATUSES)}, got: {status}", file=sys.stderr)
        sys.exit(1)

    codex = state.get("codex_review", {})

    # Validate: clean status requires artifact_path
    if status == "clean" and not args.artifact_path:
        print("ERROR: --artifact-path is required for clean Codex review", file=sys.stderr)
        sys.exit(1)

    # Validate: negative findings_count
    if args.findings_count is not None and args.findings_count < 0:
        print("ERROR: --findings-count must be non-negative, got: {args.findings_count}", file=sys.stderr)
        sys.exit(1)

    # Validate severity
    if args.highest_severity is not None and args.highest_severity not in SEVERITY_ORDER:
        print(f"ERROR: --highest-severity must be one of {sorted(SEVERITY_ORDER)}, got: {args.highest_severity}", file=sys.stderr)
        sys.exit(1)

    # Check for sensitive findings that require human review
    findings_requires_human = False
    findings_reason = None
    if args.summary:
        summary_lower = args.summary.lower()
        for kw in _CODEX_REPAIR_SENSITIVE_KEYWORDS:
            if kw in summary_lower:
                findings_requires_human = True
                findings_reason = kw
                break

    # Check scope expansion in summary
    scope_expansion = False
    if args.summary:
        summary_lower = args.summary.lower()
        scope_expansion_kws = ["scope expansion", "new file outside", "file outside scope",
                               "outside allowed scope", "unbounded scope"]
        for kw in scope_expansion_kws:
            if kw in summary_lower:
                scope_expansion = True
                break

    # Record the review
    codex["status"] = status
    codex["head_sha"] = args.head_sha or codex.get("head_sha")
    codex["artifact_path"] = args.artifact_path or codex.get("artifact_path")

    if args.findings_count is not None:
        codex["findings_count"] = args.findings_count
    if args.highest_severity is not None:
        codex["highest_severity"] = args.highest_severity

    # Append codex_repair_event
    codex_repair_event = {
        "timestamp": _utcnow(),
        "source": "codex_review",
        "head_sha": args.head_sha or codex.get("head_sha") or "",
        "artifact_path": args.artifact_path or codex.get("artifact_path") or "",
        "status": status,
        "findings_count": args.findings_count if args.findings_count is not None else 0,
        "highest_severity": args.highest_severity or codex.get("highest_severity", "none"),
        "repair_attempt": codex.get("repair_attempts", 0),
        "blocker_fingerprint": args.blocker_fingerprint or codex.get("last_blocker_fingerprint") or "",
        "summary": args.summary or "",
        # Round-86 follow-up: persist changed_paths on every
        # repair event so the Round-85
        # ``_derive_changed_paths_from_state`` helper has a
        # reliable second-source after ``codex_review.findings``.
        "changed_paths": list(getattr(args, "changed_path", []) or []),
    }
    state["codex_repair_events"].append(codex_repair_event)
    if codex_repair_event["changed_paths"]:
        # Update last_validated_changed_paths whenever the
        # controller observes a non-empty changed-path list.
        # This is the third derivation source for
        # ``record-codex-repair-result --status repaired``
        # when the documented invocation omits --changed-path.
        existing = list(state.get("last_validated_changed_paths") or [])
        for p in codex_repair_event["changed_paths"]:
            if p not in existing:
                existing.append(p)
        state["last_validated_changed_paths"] = existing
        # Round-89 follow-up: capture the head_sha and the
        # repair_attempt count alongside the validated path
        # list so the Round-89 cycle-scoped derivation helper
        # can verify that the validated list belongs to the
        # current repair cycle. Without these two fields the
        # helper would silently re-use historical validated
        # paths in a later flagless invocation.
        state["last_validated_head_sha"] = codex.get("head_sha") or args.head_sha or ""
        state["last_validated_attempt"] = codex_repair_event.get(
            "repair_attempt", 0
        )

    # Determine next action based on status
    if status == "clean":
        codex["status"] = "clean"
        # Clean after repair attempts clears the repair loop
        next_action = {
            "action": "run_task",
            "task_id": None,
            "reason": "codex_review_clean",
        }
    elif status == "findings":
        # Check if this is the same blocker repeated
        if args.blocker_fingerprint and codex.get("last_blocker_fingerprint") == args.blocker_fingerprint:
            codex["same_blocker_count"] = codex.get("same_blocker_count", 0) + 1
        else:
            codex["same_blocker_count"] = 0
        codex["last_blocker_fingerprint"] = args.blocker_fingerprint or codex.get("last_blocker_fingerprint")

        if scope_expansion:
            next_action = {
                "action": "request_human",
                "task_id": None,
                "reason": "scope_expansion_required",
            }
        elif findings_requires_human:
            next_action = {
                "action": "request_human",
                "task_id": None,
                "reason": f"codex_findings_require_human:{findings_reason}",
            }
        elif codex.get("repair_attempts", 0) >= codex.get("max_repair_attempts", DEFAULT_MAX_CODEX_REPAIR):
            codex["status"] = "repair_limit_exceeded"
            next_action = {
                "action": "request_human",
                "task_id": None,
                "reason": "codex_repair_limit_exceeded",
            }
        else:
            # Round-70 PHASE 3-P1: when --status=findings, the
            # autonomous production path MUST persist a repair
            # plan produced by the planner seam. The operator
            # supplies --findings-file as the evidence input.
            findings_evidence_path = getattr(args, "findings_file", None) or ""
            plan_persisted = False
            plan_error = ""
            # Round-107 follow-up (VUQ6M): initialize
            # ``skip_plan = False`` so the planner-call branch
            # below is gated by an explicit skip flag (set True
            # when ``plan_dir.mkdir`` failed). This prevents
            # the planner call from running AND overwriting
            # ``plan_error`` with a downstream
            # ``planner_failed:plan_dir_unavailable`` note when
            # the user-visible reason is the mkdir failure.
            skip_plan = False
            if not findings_evidence_path or not str(findings_evidence_path).strip():
                plan_error = "findings_evidence_missing"
            else:
                fpath = Path(str(findings_evidence_path))
                if not fpath.exists():
                    plan_error = "findings_evidence_not_found"
                else:
                    try:
                        with open(fpath) as _ffh:
                            findings_data = json.load(_ffh)
                    except (OSError, json.JSONDecodeError) as exc:
                        plan_error = f"findings_evidence_malformed:{type(exc).__name__}"
                        findings_data = None
                    else:
                        if (not isinstance(findings_data, list)
                                or not findings_data):
                            plan_error = "findings_evidence_empty"
                        else:
                            valid = True
                            for _i, _f in enumerate(findings_data):
                                if (not isinstance(_f, dict)
                                        or not (
                                            _f.get("finding_id")
                                            or _f.get("id")
                                        )):
                                    plan_error = (
                                        f"findings_invalid_index:{_i}"
                                    )
                                    valid = False
                                    break
                            if valid:
                                # Round-86 follow-up: persist the
                                # parsed findings list on the
                                # controller state so the
                                # Round-85 derivation helper
                                # ``_derive_changed_paths_from_state``
                                # has findings paths to fall back
                                # on when ``record-codex-repair-result``
                                # is invoked without
                                # ``--changed-path``. Without
                                # this, the derivation helper
                                # returns ``[]`` and the
                                # documented repaired transition
                                # silently drops to
                                # ``validation_failed_no_repair:
                                # no_changed_paths_supplied``.
                                codex["findings"] = list(findings_data)
                                seam = _autonomous_repair_seam()
                                plan_dir = (
                                    Path(str(args.state)).parent
                                    / "plans"
                                )
                                # Round-106 follow-up (VUIvd /
                                # PRRT_kwDOSHFpYM6VUIvd): when
                                # ``<state-parent>/plans`` cannot
                                # be created — because that path
                                # already exists as a regular
                                # file or the directory is
                                # read-only — this unguarded
                                # ``mkdir`` raises before the
                                # planner and persistence
                                # handlers run. The Round-101
                                # guard on the
                                # ``<state.parent>/validations``
                                # path was applied only to
                                # ``record-codex-repair-result``;
                                # the equivalent for plans was
                                # missing. The fix wraps
                                # ``plan_dir.mkdir`` in
                                # try/except and routes the
                                # failure through the same
                                # ``repair_planning_failed``
                                # transition path so the
                                # controller fails closed with
                                # ``next_action = request_human``
                                # instead of crashing.
                                try:
                                    plan_dir.mkdir(
                                        parents=True, exist_ok=True
                                    )
                                except OSError as exc:
                                    # Round-107 follow-up
                                    # (VUQ6M): the previous
                                    # shape assigned the mkdir
                                    # failure to a new local
                                    # ``planner_error`` and
                                    # then went on to call
                                    # ``seam["planner_call"]``
                                    # which overwrote the
                                    # failure category with
                                    # ``planner_failed:plan_dir_unavailable``.
                                    # The state transition below
                                    # only inspects ``plan_error``,
                                    # so the promised
                                    # ``plan_dir_create_failed``
                                    # reason was never persisted.
                                    # The fix: assign
                                    # ``plan_error`` directly,
                                    # raise a non-exception flag
                                    # (``skip_plan = True``),
                                    # and short-circuit the
                                    # planner call so the
                                    # original failure category
                                    # survives to the transition.
                                    skip_plan = True
                                    plan_error = (
                                        f"plan_dir_create_failed:"
                                        f"{type(exc).__name__}"
                                    )
                                    plan_path = None
                                else:
                                    skip_plan = False
                                    plan_path = (
                                        plan_dir
                                        / f"repair-plan-{int(time.time())}.json"
                                    )
                                if not skip_plan:
                                    try:
                                        plan = seam["planner_call"](
                                            findings=findings_data,
                                            changed_paths=[],
                                            tier="tier_2_cohesive_batch",
                                            final_candidate=False,
                                        )
                                    except Exception as exc:
                                        plan_error = (
                                            f"planner_failed:"
                                            f"{type(exc).__name__}"
                                        )
                                    else:
                                        try:
                                            with open(plan_path, "w") as _pfh:
                                                json.dump(plan, _pfh, indent=2)
                                        except OSError as exc:
                                            plan_error = (
                                                f"plan_persist_failed:"
                                                f"{type(exc).__name__}"
                                            )
                                        else:
                                            plan_persisted = True
                                            codex[
                                                "repair_plan_path"
                                            ] = str(plan_path)
                                            codex[
                                                "repair_plan_generated_at"
                                            ] = _utcnow()
                                            codex[
                                                "repair_plan_finding_count"
                                            ] = plan.get(
                                                "finding_count", 0
                                            )
                                            codex[
                                                "repair_plan_batch_count"
                                            ] = plan.get(
                                                "batch_count", 0
                                            )

            if plan_error:
                # Fail closed: do NOT silently advance to
                # repair_task. Request human instead.
                codex["repair_plan_error"] = plan_error
                next_action = {
                    "action": "request_human",
                    "task_id": None,
                    "reason": f"repair_planning_failed:{plan_error}",
                }
            elif plan_persisted:
                next_action = {
                    "action": "repair_task",
                    "task_id": None,
                    "reason": "codex_findings_plan_generated",
                    "source": "codex_review",
                }
            else:
                # No findings-file supplied but counter-intuitive
                # flag (we got past the if/else above). Fail closed.
                next_action = {
                    "action": "request_human",
                    "task_id": None,
                    "reason": "repair_planning_missing_input",
                }
    elif status == "blocked":
        next_action = {
            "action": "request_human",
            "task_id": None,
            "reason": "codex_blocked",
        }
    elif status == "repair_limit_exceeded":
        next_action = {
            "action": "request_human",
            "task_id": None,
            "reason": "codex_repair_limit_exceeded",
        }
    else:
        next_action = {
            "action": "request_human",
            "task_id": None,
            "reason": f"codex_review_status:{status}",
        }

    # Same blocker twice without progress → escalate
    if (codex.get("same_blocker_count", 0) >= 2 and
            next_action["action"] == "repair_task"):
        next_action = {
            "action": "request_human",
            "task_id": None,
            "reason": "same_codex_blocker_repeated",
        }

    state["codex_review"] = codex
    state["next_action"] = next_action
    state["updated_at"] = _utcnow()
    state["human_action_required"] = (next_action["action"] == "request_human")

    _save_state(state, args.state)

    print(f"Recorded Codex review: {status}")
    if args.head_sha:
        print(f"  head_sha: {args.head_sha}")
    if args.artifact_path:
        print(f"  artifact_path: {args.artifact_path}")
    if args.findings_count is not None:
        print(f"  findings_count: {args.findings_count}")
    if args.highest_severity:
        print(f"  highest_severity: {args.highest_severity}")
    print(f"  next action: {next_action['action']} — {next_action['reason']}")


def _derive_changed_paths_from_state(
    state: dict, codex: dict, *, repair_cycle_id: Optional[Any] = None
) -> list:
    """Round-85 follow-up: derive the changed-paths list from
    the controller state when ``record-codex-repair-result``
    is invoked without ``--changed-path``.

    The documented invocation
    ``record-codex-repair-result --status repaired``
    (docs/autocoder_run_controller_v0.md:598-602) does not
    supply ``--changed-path``. Without derivation, the
    handler emits ``validation_failed_no_repair:
    no_changed_paths_supplied`` and the supported repair
    workflow silently stops. The derivation order is:

    1. The most-recent codex_review.findings[].path list.
    2. The last-known changed_paths list from any prior
       repair event recorded on this run.
    3. The controller's ``last_validated_changed_paths``.

    Round-89 follow-up: when ``repair_cycle_id`` is supplied,
    the helper MUST restrict derivation to evidence
    associated with that cycle boundary. Findings paths are
    always eligible (they describe the current finding);
    repair-event ``changed_paths`` are eligible only when
    their ``repair_attempt`` matches ``repair_cycle_id`` or
    the helper was called without a cycle binding;
    ``last_validated_changed_paths`` are eligible only when
    the latest ``head_sha`` matches the most-recent
    codex_review head. The discriminator prevents a
    flagless repaired invocation from silently reusing
    historical paths from an earlier repair cycle.

    Returns a deduplicated, ordered list of strings. Empty
    list means every derivation failed and the caller MUST
    fail closed.
    """
    seen: set = set()
    derived: list = []

    def _add(p: Any) -> None:
        s = str(p or "").strip()
        if s and s not in seen:
            seen.add(s)
            derived.append(s)

    # 1. Findings paths from the current codex review. These
    # always describe the current finding boundary so they
    # are always eligible.
    for finding in (codex.get("findings") or []):
        if isinstance(finding, dict):
            _add(finding.get("path") or finding.get("file_path") or "")
        else:
            _add(finding)
    # 2. Paths from any prior repair event on this run. When
    # ``repair_cycle_id`` is supplied, restrict to the
    # ``codex_repair_events`` entry whose ``repair_attempt``
    # matches the cycle binding. If no event matches, do NOT
    # silently fall back to an unrelated historical event —
    # that would defeat the cycle-scoped repair evidence
    # gate. A later flagless ``repaired`` invocation is then
    # constrained to the cycle it is operating in.
    events = list(state.get("codex_repair_events") or [])
    if repair_cycle_id is not None:
        # Round-89 follow-up: do not silently re-use historical
        # paths from earlier cycles. If no event matches the
        # cycle binding, this source contributes nothing to
        # ``derived`` — the caller's flagless invocation will
        # then fail closed at the no-evidence boundary if no
        # other source has paths.
        matching_events = [
            ev for ev in events
            if isinstance(ev, dict)
            and ev.get("repair_attempt") == repair_cycle_id
        ]
        events = matching_events
    for event in events:
        if not isinstance(event, dict):
            continue
        for p in (event.get("changed_paths") or []):
            _add(p)
    # 3. The controller's last-known validated paths. When
    # ``repair_cycle_id`` is supplied, only include paths whose
    # recorded head_sha matches the most-recent
    # ``codex_review.head_sha`` AND whose ``last_validated_attempt``
    # matches the cycle binding exactly. This keeps validated-path
    # evidence scoped to the current cycle.
    if repair_cycle_id is not None:
        head_sha = codex.get("head_sha") or ""
        if not head_sha:
            return derived
        validated = state.get("last_validated_changed_paths") or []
        # Round-89 follow-up: align last_validated_attempt
        # with the cycle binding. ``last_validated_attempt``
        # is the ``repair_attempt`` count of the
        # controller's last validated event; if it does
        # not match the cycle binding, do not promote the
        # validated list.
        stored_attempt = state.get("last_validated_attempt")
        # Round-90 follow-up: legacy state may have
        # ``last_validated_changed_paths`` set but no
        # ``last_validated_head_sha`` / ``last_validated_attempt``
        # fields. Require BOTH discriminators to be present
        # and match the current cycle before promoting the
        # validated list. Without this guard an existing
        # controller state that was upgraded mid-run could
        # silently re-use historical validated paths against
        # a later cycle whose evidence is no longer
        # available.
        # Round-91 follow-up: compare the stored attempt
        # against ``repair_cycle_id`` directly. The previous
        # comparison ``codex[\"repair_attempts\"] + 1``
        # mis-aligned with the stored value because the
        # cycle binding comes from the just-recorded
        # ``codex_repair_event[\"repair_attempt\"]`` (which
        # is already the post-increment value), while the
        # caller may have already mutated
        # ``codex[\"repair_attempts\"]`` by the time this
        # guard runs. Use ``repair_cycle_id`` directly so
        # every place that produces ``last_validated_attempt``
        # uses the same input.
        stored_head = state.get("last_validated_head_sha", "")
        if (not stored_head
                or stored_head != head_sha
                or stored_attempt is None
                or stored_attempt != repair_cycle_id):
            return derived
        for p in validated:
            _add(p)
    else:
        for p in (state.get("last_validated_changed_paths") or []):
            _add(p)
    return derived


def _record_codex_repair_result(args: argparse.Namespace) -> None:
    """Record the result of a Codex repair attempt.

    Increments repair_attempts. If status is 'repaired', resets for a new review cycle.
    If status is 'failed', checks whether repair limit is exceeded.
    """
    state = _load_state(args.state)

    repair_status = args.status
    if repair_status not in ("repaired", "failed", "blocked"):
        print(f"ERROR: repair status must be 'repaired', 'failed', or 'blocked', got: {repair_status}", file=sys.stderr)
        sys.exit(1)

    codex = state.get("codex_review", {})

    # Compute the cleaned changed_paths list once so the
    # event, the derivation fallback, and the validated-path
    # state all observe the same input.
    cleaned_paths_for_event = [
        str(p or "").strip()
        for p in (getattr(args, "changed_path", []) or [])
    ]
    cleaned_paths_for_event = [
        p for p in cleaned_paths_for_event if p
    ]

    # Append codex_repair_event
    codex_repair_event = {
        "timestamp": _utcnow(),
        "source": "codex_review",
        "head_sha": codex.get("head_sha") or "",
        "artifact_path": codex.get("artifact_path") or "",
        "status": repair_status,
        "findings_count": codex.get("findings_count", 0),
        "highest_severity": codex.get("highest_severity", "none"),
        "repair_attempt": codex.get("repair_attempts", 0) + 1,
        "blocker_fingerprint": args.blocker_fingerprint or codex.get("last_blocker_fingerprint") or "",
        "summary": args.summary or "",
        # Round-87 follow-up: persist the supplied
        # --changed-path list on every repair-result event so
        # the Round-85 derivation helper has a second-source
        # after ``codex_review.findings`` and the
        # ``record-codex-review`` events. Without this a
        # caller that supplies impact evidence ONLY on
        # ``record-codex-repair-result --changed-path`` still
        # leaves subsequent derivations with no evidence.
        "changed_paths": cleaned_paths_for_event,
    }
    state["codex_repair_events"].append(codex_repair_event)
    # Round-103 follow-up: defer the codex_repair_event status
    # until after validation so the append-only history reflects
    # the actual validation outcome. The previous shape appended
    # ``status=repair_status`` (which is the CLI-supplied
    # ``--status``) before the validator ran and never corrected
    # the event when validation failed (e.g. the skip_runner
    # path or a non-zero return code). The status is updated
    # in place after validation below so the append-only
    # history is consistent with ``last_validation_outcome``
    # and ``next_action``.
    if cleaned_paths_for_event:
        # Round-89 follow-up: update last_validated_changed_paths
        # whenever the controller observes a non-empty
        # changed-path list on a repair-result event. The
        # Round-86 fix only updated this on
        # ``record-codex-review`` events; the validator
        # handler in this same function must also persist
        # the impact evidence so the documented flagless
        # ``repaired`` invocation can derive from this state.
        existing = list(state.get("last_validated_changed_paths") or [])
        for p in cleaned_paths_for_event:
            if p not in existing:
                existing.append(p)
        state["last_validated_changed_paths"] = existing
        # Round-89 follow-up: capture the head_sha and the
        # repair_attempt count so the cycle-scoped derivation
        # helper can verify the validated list belongs to the
        # current repair cycle. Without these two fields the
        # helper would silently re-use historical validated
        # paths in a later flagless invocation.
        state["last_validated_head_sha"] = codex.get("head_sha") or ""
        state["last_validated_attempt"] = codex_repair_event.get(
            "repair_attempt", 0
        )

    codex["repair_attempts"] = codex.get("repair_attempts", 0) + 1

    if repair_status == "repaired":
        # Round-70 PHASE 3-P1: when --status=repaired the
        # autonomous production path MUST invoke the
        # impact-selected runner seam. Failed validation
        # preserves findings state (no advance to
        # ``await_codex_review_after_repair``).
        validation_outcome = "pending"
        validation_log_path = ""
        validation_return_code = None
        validation_error = ""
        # Round-87 follow-up: reuse the cleaned paths list
        # computed for the event so the validator observes the
        # same input as the persisted event. The previous
        # Round-85 code rebuilt the list here, which would
        # have silently diverged from the event's cleaned_paths
        # if the args env ever changed between computation
        # points.
        cleaned_paths = list(cleaned_paths_for_event)
        # Round-85 follow-up: when --changed-path is omitted,
        # derive impact evidence from the controller state
        # before failing closed. The previous behavior emitted
        # ``no_changed_paths_supplied`` for the documented
        # ``record-codex-repair-result --status repaired``
        # invocation (docs/autocoder_run_controller_v0.md:598-602),
        # silently stopping the supported repair workflow. The
        # derivation order is:
        #   1. The most-recent codex_review.findings[].path list.
        #   2. The last-known changed_paths list from any prior
        #      repair event recorded on this run.
        #   3. The controller's last_validated_changed_paths.
        # If every derivation is empty the documented command
        # still fails closed, but only AFTER the controller has
        # exhausted every reasonable impact-evidence source.
        if not cleaned_paths:
            # Round-89 follow-up: bind the derivation helper to
            # the current repair cycle so a flagless repaired
            # invocation does not silently reuse historical
            # ``changed_paths`` recorded on an earlier cycle.
            # The repair event (written earlier in this same
            # function) already carries
            # ``repair_attempt = codex[\"repair_attempts\"] + 1``
            # so the cycle binding MUST come from the event,
            # not from re-incrementing the controller counter.
            # Round-90 follow-up: ``codex[\"repair_attempts\"]``
            # is incremented LATER in this function; passing
            # ``codex[\"repair_attempts\"] + 1`` here would over-
            # count by one and align the helper to a future
            # cycle that the validated/recorded sources cannot
            # reach. Use ``codex_repair_event[\"repair_attempt\"]``
            # which is the just-recorded value.
            derived = _derive_changed_paths_from_state(
                state, codex,
                repair_cycle_id=codex_repair_event.get("repair_attempt"),
            )
            if derived:
                cleaned_paths = derived
                codex["changed_paths_derived"] = True
                codex["changed_paths_derived_source"] = "state"
            else:
                validation_error = "no_changed_paths_supplied"
        else:
            codex["changed_paths_derived"] = False
            codex["changed_paths_derived_source"] = "cli"
        if cleaned_paths:
            log_dir = (
                Path(str(args.state)).parent / "validations"
            )
            # Round-101 follow-up (VQb5p): wrap the
            # ``log_dir.mkdir`` call in try/except so a
            # directory-creation failure surfaces as
            # ``validation_log_create_error`` rather than as
            # an uncaught OSError that crashes the command
            # before the controller state is saved. The
            # previous shape raised OSError directly, leaving
            # ``state`` unsaved and the operator without an
            # actionable failure record.
            #
            # Round-102 follow-up (VRxoP): clearing
            # ``cleaned_paths`` to empty was insufficient —
            # the unconditional ``runner_call`` below would
            # still execute with an empty path list and
            # undefined ``validation_log_path``, producing a
            # spurious ``validation_outcome=passed``. The fix
            # flips a ``skip_runner`` flag so the runner
            # block is fully bypassed on a directory-creation
            # failure, and ``validation_log_path`` is left
            # empty (no artifact exists).
            skip_runner = False
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                validation_error = f"validation_log_dir_error: {type(exc).__name__}: {exc}"
                skip_runner = True
                validation_log_path = ""
            else:
                validation_log_path = str(
                    log_dir / f"validation-{int(time.time())}.json"
                )
            if not skip_runner:
                seam = _autonomous_repair_seam()
                try:
                    result = seam["runner_call"](
                        changed_paths=cleaned_paths,
                        tier="tier_2_cohesive_batch",
                        final_candidate=False,
                        log_path=validation_log_path,
                    )
                except Exception as exc:
                    validation_error = (
                        f"runner_failed:{type(exc).__name__}"
                    )
                    # Round-104 follow-up (PRRT_kwDOSHFpYM6VSVDA):
                    # when ``runner_call`` raises (e.g. pytest
                    # cannot be launched), the previous handler
                    # set only ``validation_error`` and left
                    # ``validation_outcome`` as ``"pending"``.
                    # The Round-103 follow-up guard
                    # (``if validation_outcome in ("passed",
                    # "failed")``) then SKIPPED the status
                    # correction and persisted a
                    # ``codex_repair_event`` with
                    # ``status="repaired"`` even though
                    # ``next_action`` reports
                    # ``validation_failed_no_repair:runner_failed``.
                    # The fix sets the exception outcome to
                    # ``"failed"`` and a non-zero return code so
                    # the append-only history remains
                    # consistent with ``last_validation_outcome``
                    # and ``next_action``.
                    validation_outcome = "failed"
                    validation_return_code = -1
                else:
                    # Round-71 PHASE 3-P1-B: the production facade
                    # ``run_selected_tests`` returns canonical keys
                    # ``returncode`` / ``duration_seconds`` / ``selected``.
                    # Older alias keys ``return_code`` / ``duration`` /
                    # ``selected_tests`` are read only as backward-compat
                    # fallbacks; the canonical value always wins.
                    def _canonical_int(keys, default=-1):
                        for k in keys:
                            v = result.get(k)
                            if v is not None:
                                try:
                                    return int(v)
                                except (TypeError, ValueError):
                                    pass
                        return default
                    validation_return_code = _canonical_int(
                        ("returncode", "return_code"), -1
                    )
                    if validation_return_code == 0:
                        validation_outcome = "passed"
                    else:
                        validation_outcome = "failed"
            else:
                # Skip path: the runner was not invoked. Mark
                # the validation as failed so the controller
                # fails closed rather than claiming a passing
                # validation without an evidence artifact.
                validation_outcome = "failed"
                validation_return_code = -1

        codex["last_validation_outcome"] = validation_outcome
        codex["last_validation_log_path"] = validation_log_path
        if validation_return_code is not None:
            codex["last_validation_return_code"] = (
                validation_return_code
            )
        if validation_error:
            codex["last_validation_error"] = validation_error

        # Round-103 follow-up: correct the codex_repair_event
        # status in place to reflect the actual validation
        # outcome. The event was appended above with
        # ``status=repair_status`` (the CLI-supplied
        # ``--status``); after validation we override it to
        # ``failed`` so the append-only history is consistent
        # with ``last_validation_outcome`` and ``next_action``.
        # Only override when validation actually produced a
        # terminal outcome (passed or failed); when the
        # validator was not invoked (no changed-paths
        # supplied) ``validation_outcome`` is still
        # ``"pending"`` and we leave the event's CLI-supplied
        # status alone.
        if validation_outcome in ("passed", "failed"):
            if validation_outcome != "passed":
                codex_repair_event["status"] = "failed"

        if validation_outcome == "passed":
            # Validation succeeded: clear findings state and
            # advance to awaiting another Codex review.
            codex["status"] = "not_started"
            codex["findings_count"] = 0
            codex["highest_severity"] = "none"
            next_action = {
                "action": "request_human",
                "task_id": None,
                "reason": "await_codex_review_after_repair",
            }
        else:
            # Validation failed (or no paths supplied):
            # preserve findings state and do NOT advance.
            next_action = {
                "action": "request_human",
                "task_id": None,
                "reason": (
                    f"validation_failed_no_repair:"
                    f"{validation_error or 'rc' + str(validation_return_code)}"
                ),
            }
    elif repair_status == "failed":
        if codex["repair_attempts"] >= codex.get("max_repair_attempts", DEFAULT_MAX_CODEX_REPAIR):
            codex["status"] = "repair_limit_exceeded"
            next_action = {
                "action": "request_human",
                "task_id": None,
                "reason": "codex_repair_limit_exceeded",
            }
        else:
            next_action = {
                "action": "repair_task",
                "task_id": None,
                "reason": "codex_repair_failed_retry",
                "source": "codex_review",
            }
    else:  # blocked
        codex["status"] = "blocked"
        next_action = {
            "action": "request_human",
            "task_id": None,
            "reason": "codex_repair_blocked",
        }

    # Same blocker twice → escalate regardless of repair count
    if args.blocker_fingerprint:
        if codex.get("last_blocker_fingerprint") == args.blocker_fingerprint:
            codex["same_blocker_count"] = codex.get("same_blocker_count", 0) + 1
            if codex["same_blocker_count"] >= 2:
                next_action = {
                    "action": "request_human",
                    "task_id": None,
                    "reason": "same_codex_blocker_repeated",
                }
        else:
            codex["same_blocker_count"] = 0
        codex["last_blocker_fingerprint"] = args.blocker_fingerprint

    state["codex_review"] = codex
    state["next_action"] = next_action
    state["updated_at"] = _utcnow()
    state["human_action_required"] = (next_action["action"] == "request_human")

    _save_state(state, args.state)

    print(f"Recorded Codex repair result: {repair_status}")
    print(f"  repair_attempts: {codex['repair_attempts']}/{codex.get('max_repair_attempts', DEFAULT_MAX_CODEX_REPAIR)}")
    print(f"  next action: {next_action['action']} — {next_action['reason']}")


def _autonomous_repair_seam():
    """Return the planner/runner seam for autonomous repair.

    Round-70 PHASE 3-P1: the controller's autonomous
    Codex-repair path now consults this seam rather than
    only mutating state. Tests inject a fake dict with the
    keys ``planner_call`` and ``runner_call`` to capture
    what would have been invoked without doing the work.
    """
    try:
        from scripts.local import aed_repair_planner as _planner
        from scripts.local import aed_test_runner as _runner
        return {
            "planner_call": lambda **kw: _planner.build_repair_plan(**kw),
            "runner_call": lambda **kw: _runner.run_impact_selected_tests(**kw),
            "planner_module": _planner,
            "runner_module": _runner,
        }
    except Exception:
        # If the planner/runner modules fail to import, return a
        # fail-closed seam that raises if invoked (the controller
        # must explicitly fail closed when the wiring is broken).
        def _raise(_exc):
            def _f(**_kw):
                raise _exc
            return _f
        return {
            "planner_call": _raise(ImportError("aed_repair_planner unavailable")),
            "runner_call": _raise(ImportError("aed_test_runner unavailable")),
            "planner_module": None,
            "runner_module": None,
        }


def _record_autonomous_repair_plan(args: argparse.Namespace) -> None:
    """Record the plan produced by the autonomous planner.

    Round-70 PHASE 3-P1: this records the plan path and key
    plan metadata in controller state so the operator and
    downstream tooling can verify cohesive batching and
    impact-selected tests were actually produced. Sets
    next_action=repair_task on success. Fails closed (no
    state mutation) when the planner input is missing or
    malformed.
    """
    state = _load_state(args.state)

    if not args.findings_file or not str(args.findings_file).strip():
        print("ERROR: --findings-file is required to record a repair plan",
              file=sys.stderr)
        sys.exit(2)
    findings_path = Path(str(args.findings_file))
    if not findings_path.exists():
        print(f"ERROR: findings file not found: {findings_path}",
              file=sys.stderr)
        sys.exit(2)
    try:
        with open(findings_path) as f:
            findings = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: findings file could not be parsed: {e}",
              file=sys.stderr)
        sys.exit(2)
    if not isinstance(findings, list) or not findings:
        print("ERROR: findings file must contain a non-empty list",
              file=sys.stderr)
        sys.exit(2)
    # Validate each finding is a dict with a finding_id
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            print(f"ERROR: findings[{i}] must be a dict", file=sys.stderr)
            sys.exit(2)
        if not f.get("finding_id") and not f.get("id"):
            print(f"ERROR: findings[{i}] missing finding_id", file=sys.stderr)
            sys.exit(2)

    output_plan = Path(str(args.output_plan or ""))

    # Invoke planner seam (or test seam).
    seam = _autonomous_repair_seam()
    try:
        plan = seam["planner_call"](
            findings=findings,
            changed_paths=[],
            tier="tier_2_cohesive_batch",
            final_candidate=False,
        )
    except Exception as e:
        print(f"ERROR: planner failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        sys.exit(2)

    # Persist the plan if output_plan is given. The planner API
    # performs no GitHub mutation and writes only when given an
    # explicit output path. We then record the path in state.
    if output_plan:
        output_plan.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(output_plan, "w") as fh:
                json.dump(plan, fh, indent=2)
        except OSError as e:
            print(f"ERROR: could not persist plan to {output_plan}: {e}",
                  file=sys.stderr)
            sys.exit(2)

    codex = state.get("codex_review", {})
    codex["repair_plan_path"] = str(output_plan) if output_plan else ""
    codex["repair_plan_generated_at"] = _utcnow()
    codex["repair_plan_finding_count"] = plan.get("finding_count", 0)
    codex["repair_plan_batch_count"] = plan.get("batch_count", 0)
    state["codex_review"] = codex

    next_action = {
        "action": "repair_task",
        "task_id": None,
        "reason": "codex_findings_plan_generated",
        "source": "codex_review",
    }
    state["next_action"] = next_action
    state["updated_at"] = _utcnow()
    state["human_action_required"] = False

    _save_state(state, args.state)

    print(
        f"Recorded autonomous repair plan: "
        f"findings={plan.get('finding_count', 0)} "
        f"batches={plan.get('batch_count', 0)} "
        f"plan={output_plan or '(in-memory)'}"
    )


def _record_autonomous_repair_validation(args: argparse.Namespace) -> None:
    """Record the impact-selected validation run after repair.

    Round-70 PHASE 3-P1: this records the test-runner outcome
    so the operator can verify selected tests actually ran
    before the repaired transition was claimed. Fails closed
    (does NOT advance to await_codex_review_after_repair)
    when validation fails or evidence is malformed.
    """
    state = _load_state(args.state)

    # Round-96 follow-up (VPRYe): the log-write failure flag is
    # initialized to False here so subsequent
    # ``if log_write_failure:`` checks at the codex state write
    # below have a stable binding. ``log_path`` is also
    # normalized so a missing caller-supplied value forces a
    # controller-derived default path under
    # ``<state.parent>/validations/``.
    log_write_failure = False
    log_write_failure_error = ""
    log_write_failure_path = ""

    # argparse action='append' populates args.changed_path (singular)
    raw_paths = list(getattr(args, "changed_path", []) or [])
    cleaned_paths = [p for p in raw_paths if str(p or "").strip()]
    if not cleaned_paths:
        print("ERROR: at least one non-empty --changed-path is required "
              "to record repair validation", file=sys.stderr)
        sys.exit(2)

    log_path: Optional[Path] = (
        Path(str(args.output_log or "")) if str(args.output_log or "").strip() else None
    )
    # Round-96 follow-up (VPRYe): when the caller does not supply
    # an ``--output-log``, the previous handler silently skipped
    # the log write and recorded an empty
    # ``last_validation_log_path`` while still advancing to
    # ``await_codex_review_after_repair``. Derive a default path
    # under ``<state.parent>/validations/`` so the on-disk
    # evidence artifact exists whenever a validation run
    # completes successfully. A separate failure flag handles
    # write-time errors below.
    if log_path is None:
        log_path = (
            Path(str(args.state)).parent
            / "validations"
            / f"validation-{int(time.time())}.json"
        )
    # Invoke the runner seam.
    seam = _autonomous_repair_seam()
    try:
        # argparse action='append' populates args.changed_path (singular)
        changed_paths = cleaned_paths
        result = seam["runner_call"](
            changed_paths=changed_paths,
            tier=str(args.tier or "tier_2_cohesive_batch"),
            final_candidate=bool(args.final_candidate),
            log_path=str(log_path) if log_path else None,
        )
    except Exception as e:
        # On runner failure, do NOT advance to repaired state.
        # Record the failure but leave the finding state intact
        # so the operator can re-run validation.
        codex = state.get("codex_review", {})
        codex["last_validation_status"] = "runner_error"
        codex["last_validation_error"] = (
            f"{type(e).__name__}: {e}"
        )
        codex["last_validation_at"] = _utcnow()
        state["codex_review"] = codex
        state["updated_at"] = _utcnow()
        # Mark human action required to inspect.
        state["human_action_required"] = True
        state["next_action"] = {
            "action": "request_human",
            "task_id": None,
            "reason": "validation_runner_error",
        }
        _save_state(state, args.state)
        print(f"ERROR: runner failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        sys.exit(2)

    # Persist the runner log. Round-96 follow-up (VPRYe):
    # a missing ``log_path`` used to silently skip the log-write
    # step while the rest of the function declared a
    # successful validation. The controller would then
    # record an empty ``last_validation_log_path`` and
    # advance to ``await_codex_review_after_repair`` even
    # though no evidence artifact existed. The default
    # path is supplied above so ``log_path`` is always
    # non-None at this point. Treat a write failure as
    # ``log_write_failure = True`` so the codex transition
    # below surfaces the failure instead of silently
    # treating the run as ``returncode == 0`` against a
    # nonexistent artifact.
    log_path_str = ""
    if log_path and isinstance(log_path, Path) and str(log_path):
        # Round-97 follow-up (VPhYp): put the ``mkdir`` inside
        # the same try block as the write so a parent-directory
        # creation failure surfaces as ``log_write_error``
        # rather than as a raw OSError escaping the function.
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "w") as fh:
                json.dump(result, fh, indent=2)
        except OSError as e:
            # Round-96 follow-up: capture the failure but
            # do not call ``sys.exit(2)`` because the caller
            # is non-interactive. Mark the state shape so the
            # transition below picks up the failure.
            print(f"ERROR: could not persist log to {log_path}: {e}",
                  file=sys.stderr)
            log_write_failure = True
            log_write_failure_error = (
                f"{type(e).__name__}: {e}"
            )
            log_write_failure_path = str(log_path)
            log_path = None  # do not record a missing artifact
        else:
            log_path_str = str(log_path)
    # If log write succeeded, this string is non-empty.

    # Apply transition:
    # alias keys.
    rc = int(result.get("returncode", result.get("return_code", -1)))
    selected = list(result.get("selected", result.get("selected_tests", [])))
    duration = result.get("duration_seconds", result.get("duration", 0.0))

    codex = state.get("codex_review", {})
    codex["last_validation_at"] = _utcnow()
    codex["last_validation_status"] = "passed" if rc == 0 else "failed"
    codex["last_validation_return_code"] = rc
    codex["last_validation_returncode"] = rc
    codex["last_validation_duration_seconds"] = duration
    codex["last_validation_selected"] = selected
    codex["last_validation_selected_tests"] = selected
    codex["last_validation_command"] = result.get("command", [])
    codex["last_validation_selection_reason"] = result.get(
        "selection_reason", ""
    )
    codex["last_validation_tier"] = result.get("tier", "")
    codex["last_validation_requires_full_validation"] = result.get(
        "requires_full_validation", False
    )
    codex["last_validation_log_path"] = str(log_path) if log_path else ""

    # Apply transition:
    #  - rc == 0: validated successfully, allow transition to
    #    await_codex_review_after_repair (mirrors the contract).
    #  - rc != 0: validation failed, do NOT reset findings state
    #    and do NOT claim repair succeeded.
    #  - log_write_failure: the log write failed and the
    #    evidence artifact does not exist; mark the repair
    #    event ``failed`` even when ``rc == 0`` so the
    #    append-only history does not falsely claim a
    #    successful repair. Round-98 follow-up (VPyKS).
    repair_event_status = "failed"
    if rc == 0 and not log_write_failure:
        repair_event_status = "repaired"
    codex_repair_event = {
        "timestamp": _utcnow(),
        "source": "autonomous_validation",
        "head_sha": codex.get("head_sha") or "",
        "artifact_path": codex.get("artifact_path") or "",
        "status": repair_event_status,
        "findings_count": codex.get("findings_count", 0),
        "highest_severity": codex.get("highest_severity", "none"),
        "repair_attempt": codex.get("repair_attempts", 0),
        "blocker_fingerprint": codex.get("last_blocker_fingerprint") or "",
        "summary": (
            f"validation rc={rc} duration={duration:.1f}s"
            + (" log_write_error" if log_write_failure else "")
        ),
    }
    state["codex_repair_events"] = state.get("codex_repair_events", [])
    state["codex_repair_events"].append(codex_repair_event)

    # Round-96 follow-up (VPRYe): when the log write failed but
    # ``rc == 0``, the controller MUST NOT advance to
    # ``await_codex_review_after_repair``. Surface the log-write
    # failure as a separate failure source so the operator
    # notices the missing artifact.
    if log_write_failure:
        # Round-97 follow-up (VPhYm): persist the
        # ``next_action`` dict on the controller state before
        # saving it. The previous shape created a local
        # ``next_action`` and called ``_save_state`` without
        # assigning ``state[\"next_action\"]``, so the
        # failure mode did not persist to disk and was
        # overwritten by the previous ``next_action``.
        next_action = {
            "action": "request_human",
            "task_id": None,
            "reason": "validation_log_write_error",
        }
        codex["last_validation_status"] = "log_write_error"
        codex["last_validation_error"] = log_write_failure_error
        codex["last_validation_at"] = _utcnow()
        state["human_action_required"] = True
        state["codex_review"] = codex
        state["updated_at"] = _utcnow()
        state["next_action"] = next_action
        _save_state(state, args.state)
        sys.exit(2)
    if rc == 0:
        codex["status"] = "not_started"
        codex["findings_count"] = 0
        codex["highest_severity"] = "none"
        next_action = {
            "action": "request_human",
            "task_id": None,
            "reason": "await_codex_review_after_repair",
        }
        state["human_action_required"] = True
    else:
        # Validation failed; do NOT reset finding state. Remain repairable.
        next_action = {
            "action": "request_human",
            "task_id": None,
            "reason": "validation_failed_does_not_repair",
        }
        state["human_action_required"] = True
    state["codex_review"] = codex
    state["next_action"] = next_action
    state["updated_at"] = _utcnow()

    _save_state(state, args.state)
    print(
        f"Recorded autonomous validation: rc={rc} duration={duration:.1f}s "
        f"selected={len(selected)}"
    )



def _finalize_run(args: argparse.Namespace) -> None:
    state = _load_state(args.state)

    # Round-120: refuse finalization while an authorized or started
    # mutation lacks a terminal result. The caller can either record
    # the missing result or mark the run as not-finalizable.
    workspace = Path(state.get("workspace", "")).resolve()
    # P1 fix (round 7): refuse finalization when the recorded
    # workspace is unavailable. If the workspace does not exist
    # (deleted, unmounted), we cannot read MUTATIONS.jsonl and a
    # missing check is NOT proof that no mutations are outstanding.
    # Fail closed.
    if state.get("workspace") and not workspace.is_dir():
        print(
            f"ERROR: refusing to finalize: workspace "
            f"{state.get('workspace')!r} is not a directory "
            f"(deleted, unmounted, or otherwise unavailable). "
            f"Cannot read MUTATIONS.jsonl; refusing to assume zero "
            f"outstanding mutations.",
            file=sys.stderr,
        )
        sys.exit(8)
    if workspace.is_dir():
        # P1 fix (round 4): share the mutation-journal lock across
        # the outstanding-mutations check and the terminal state
        # transition. Without this, authorize-mutation could read
        # RUN_ACTIVE, decide to authorize, append its record after
        # finalization completes its "no outstanding" check, and
        # leave the controller finalized while a mutation was
        # authorized.
        _mutation_auth_path = workspace / _mutation_auth.MUTATIONS_FILENAME
        sentinel_path = _mutation_auth_path.with_suffix(
            _mutation_auth_path.suffix + ".auth-sentinel"
        )
        from scripts.local.aed_supervisor_lock import (
            _acquire_sentinel_fd,
            _release_sentinel_fd,
        )
        sentinel_fd = _acquire_sentinel_fd(sentinel_path, max_attempts=20)
        if sentinel_fd is None:
            print(
                "ERROR: refusing to finalize: mutation journal lock busy",
                file=sys.stderr,
            )
            sys.exit(8)
        try:
            outstanding = _mutation_auth.outstanding_mutations(workspace)
            if outstanding:
                mids = [m.get("mutation_id") for m in outstanding]
                print(
                    f"ERROR: refusing to finalize: outstanding mutations: {mids}",
                    file=sys.stderr,
                )
                sys.exit(8)
            state["overall_status"] = "RUN_COMPLETE"
            state["updated_at"] = _utcnow()
            state["next_action"] = {"action": "stop", "task_id": None, "reason": "run finalized"}
            state["human_action_required"] = False
            _save_state(state, args.state)
        finally:
            _release_sentinel_fd(sentinel_fd, sentinel_path)
    else:
        state["overall_status"] = "RUN_COMPLETE"
        state["updated_at"] = _utcnow()
        state["next_action"] = {"action": "stop", "task_id": None, "reason": "run finalized"}
        state["human_action_required"] = False
        _save_state(state, args.state)

    # Round-120: release the supervisor lock if we own it.
    rid = state.get("run_identity") or {}
    if rid.get("repository"):
        scope = {
            "repository": rid.get("repository") or "",
            "target_pr_number": rid.get("target_pr_number"),
            "mutation_target": rid.get("mutation_target"),
        }
        # Find the lock file in the same directory the init used.
        # We try the persisted lock_dir first, then fall back to
        # the host-wide default.
        lock_base = None
        if rid.get("lock_dir"):
            lock_base = Path(rid["lock_dir"])
        # P1 fix (round 5): retry release if the sentinel is briefly
        # held by another process. Without this, finalization can
        # report success while the lock remains, blocking the next
        # run for up to seven days.
        released = False
        for attempt in range(5):
            try:
                released = _supervisor_lock.release(
                    scope=scope,
                    owner_run_id=state.get("run_id", "unknown"),
                    base_dir=lock_base,
                )
                if released:
                    break
            except Exception:
                pass
            import time as _time
            _time.sleep(0.05)
        if not released:
            print(
                "ERROR: refusing to finalize: failed to release "
                "supervisor lock after 5 attempts; lock may be "
                "orphaned. Manual recovery required.",
                file=sys.stderr,
            )
            sys.exit(13)

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
    # Round-120: hardening — run identity, supervisor lock, mutation policy
    p_init.add_argument("--repository", help="Repository (owner/name) for run scope and lock")
    p_init.add_argument("--target-pr-number", type=int, help="Target PR number for run scope and lock")
    p_init.add_argument("--mutation-target", help="Mutation target when no PR is involved (e.g. branch name)")
    p_init.add_argument("--current-main-sha", help="Current main SHA at run start")
    p_init.add_argument("--starting-target-sha", help="Starting target SHA at run start")
    p_init.add_argument("--merge-policy", default="stop_before_merge", help="Merge policy (default: stop_before_merge)")
    p_init.add_argument("--lock-dir",
                        help="Override the supervisor-lock directory. "
                             "Default: host-wide dir derived from XDG_RUNTIME_DIR or ~/.aed/locks. "
                             "Tests pass an explicit dir to isolate.")
    # Round-9 P1 fix (Make recovered lease adoptable by init):
    # when a stale lease blocks init, allow init to recover it
    # inline rather than requiring a separate recover-stale-lock
    # call. The recovered lease is bound to THIS init's
    # --output-state path so init's state file becomes the
    # lease's owner_state_path, satisfying the lease-based
    # liveness check.
    p_init.add_argument("--replace-stale-lock",
                        action="store_true",
                        help="If a stale lease blocks the scope, recover it "
                             "inline before publishing this run's state. The "
                             "recovered lease is bound to --output-state.")

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

    # record-codex-review
    p_codex_rev = sub.add_parser("record-codex-review",
                                  help="Record a Codex review result (clean, findings, or blocked)")
    p_codex_rev.add_argument("--state", required=True, help="Path to CONTROLLER_STATE.json")
    p_codex_rev.add_argument("--status", required=True,
                             choices=sorted(CODEX_REVIEW_STATUSES),
                             help="Codex review status")
    p_codex_rev.add_argument("--head-sha", required=True, help="PR head commit SHA")
    p_codex_rev.add_argument("--artifact-path", help="Path to Codex artifact JSON")
    p_codex_rev.add_argument("--findings-count", type=int, help="Number of findings (required for findings status)")
    p_codex_rev.add_argument("--highest-severity",
                             choices=["none", "P3", "P2", "P1", "HIGH"],
                             help="Highest severity among findings")
    p_codex_rev.add_argument("--summary", help="Summary text of findings or clean result")
    p_codex_rev.add_argument("--blocker-fingerprint", help="Fingerprint/hash identifying the blocker")
    p_codex_rev.add_argument("--findings-file",
                              help="Path to JSON findings artifact (Round-70 PHASE 3-P1: "
                                   "the controller invokes the planner seam on this). "
                                   "Required when --status=findings to enable the "
                                   "autonomous repair plan path; otherwise the "
                                   "controller fails closed to a human-actionable state.")
    # Round-86 follow-up: --changed-path on record-codex-review
    # persists the impact evidence on every repair event so the
    # Round-85 derivation helper has a second-source after
    # ``codex_review.findings``. Optional for backward
    # compatibility; the documented Round-70 PHASE 3-P1 caller
    # does not supply it but may in future round-71 cycles.
    p_codex_rev.add_argument("--changed-path", action="append",
                              help="Changed path(s) for impact-selected "
                                   "validation. Repeatable. Optional.")

    # record-codex-repair-result
    p_codex_rep = sub.add_parser("record-codex-repair-result",
                                  help="Record the result of a Codex repair attempt")
    p_codex_rep.add_argument("--state", required=True, help="Path to CONTROLLER_STATE.json")
    p_codex_rep.add_argument("--status", required=True,
                             choices=["repaired", "failed", "blocked"],
                             help="Repair outcome")
    p_codex_rep.add_argument("--summary", help="Brief description of what was done")
    p_codex_rep.add_argument("--blocker-fingerprint", help="Fingerprint matching the original blocker")
    # Round-85 follow-up: --changed-path remains optional so the
    # documented invocation
    #   ``record-codex-repair-result --status repaired``
    # (docs/autocoder_run_controller_v0.md:598-602) continues to
    # work. The handler derives the changed paths from the
    # controller state when --changed-path is omitted, falling
    # back to ``codex_review.findings`` paths or the last-known
    # changed-paths list. Round-69 Codex review (this finding's
    # prompt) chose this option over argparse-required so the
    # documented command never silently drops to
    # ``validation_failed_no_repair:no_changed_paths_supplied``.
    p_codex_rep.add_argument("--changed-path", action="append",
                              help="Changed path(s) for impact-selected validation "
                                   "(Round-70 PHASE 3-P1). Repeatable. Optional "
                                   "after Round-85: the controller derives "
                                   "impact evidence from state when omitted.")

    # record-autonomous-repair-plan (Round-70 P1 wiring)
    p_arp = sub.add_parser(
        "record-autonomous-repair-plan",
        help="Record a cohesive repair plan generated from findings evidence",
    )
    p_arp.add_argument("--state", required=True, help="Path to CONTROLLER_STATE.json")
    p_arp.add_argument("--findings-file", required=True,
                       help="JSON file containing the findings list")
    p_arp.add_argument("--output-plan",
                       help="Destination JSON file for the plan")

    # record-autonomous-repair-validation (Round-70 P1 wiring)
    p_arv = sub.add_parser(
        "record-autonomous-repair-validation",
        help="Record the impact-selected validation run after repair",
    )
    p_arv.add_argument("--state", required=True, help="Path to CONTROLLER_STATE.json")
    p_arv.add_argument("--changed-path", action="append", required=True,
                       help="Changed path (repeatable)")
    p_arv.add_argument("--tier",
                       choices=["tier_1_inner_repair", "tier_2_cohesive_batch",
                                "tier_3_final_candidate"],
                       default="tier_2_cohesive_batch")
    p_arv.add_argument("--final-candidate", action="store_true")
    p_arv.add_argument("--output-log",
                       help="Destination JSON file for the validation log")

    # finalize-run
    p_fin = sub.add_parser("finalize-run", help="Mark run as complete")
    p_fin.add_argument("--state", required=True, help="Path to CONTROLLER_STATE.json")

    # record-persistent-guard-snapshot
    p_pgs = sub.add_parser("record-persistent-guard-snapshot",
                           help="Record the persistent mutation guard snapshot path after pre-run snapshot")
    p_pgs.add_argument("--state", required=True, help="Path to CONTROLLER_STATE.json")
    p_pgs.add_argument("--root", default="/home/max/.hermes",
                       help="Hermes root that was snapshotted (default: /home/max/.hermes)")
    p_pgs.add_argument("--snapshot-path", required=True,
                       help="Path to the snapshot JSON file written by the guard")

    # record-persistent-guard-compare
    p_pgc = sub.add_parser("record-persistent-guard-compare",
                            help="Record the persistent mutation guard compare result after run")
    p_pgc.add_argument("--state", required=True, help="Path to CONTROLLER_STATE.json")
    p_pgc.add_argument("--compare-json", required=True,
                       help="Path to the guard's compare JSON output")
    p_pgc.add_argument("--compare-md", help="Path to the guard's compare markdown output")

    # Round-120: mutation-authorization and supervisor-lock subcommands
    p_mauth = sub.add_parser(
        "authorize-mutation",
        help="Authorize a one-time repository or GitHub mutation before the executor performs it",
    )
    p_mauth.add_argument("--state", required=True, help="Path to CONTROLLER_STATE.json")
    p_mauth.add_argument("--workspace", required=True, help="Run workspace directory")
    p_mauth.add_argument("--mutation-type", required=True,
                         help="Type of mutation (e.g. squash_merge, push, pr_body_update)")
    p_mauth.add_argument("--expected-main-sha", help="Expected current main SHA at execution time")
    p_mauth.add_argument("--expected-target-sha", help="Expected target/PR-head SHA at execution time")
    p_mauth.add_argument("--mutation-target", help="Mutation target identifier (e.g. branch name)")
    p_mauth.add_argument("--pending-action", required=True, help="Exact pending action")

    p_mres = sub.add_parser(
        "record-mutation-result",
        help="Record the terminal result of an authorized mutation",
    )
    p_mres.add_argument("--workspace", required=True, help="Run workspace directory")
    p_mres.add_argument("--mutation-id", required=True, help="The mutation_id from authorize-mutation")
    p_mres.add_argument("--status", required=True,
                        choices=sorted(_mutation_auth.TERMINAL_RESULTS),
                        help="Terminal result status")
    p_mres.add_argument("--evidence", help="Optional evidence string")
    p_mres.add_argument("--actual-main-sha", help="Observed main SHA after the mutation")
    p_mres.add_argument("--actual-target-sha", help="Observed target SHA after the mutation")
    p_mres.add_argument("--error-detail", help="Optional error detail")

    p_locks = sub.add_parser(
        "inspect-lock",
        help="Inspect a supervisor lock for a given scope and report liveness",
    )
    p_locks.add_argument("--state", required=True, help="Path to CONTROLLER_STATE.json")
    p_locks.add_argument("--workspace", required=True, help="Run workspace directory")
    p_locks.add_argument("--lock-dir",
                         help="Override the supervisor-lock directory (default: host-wide)")

    p_recover = sub.add_parser(
        "recover-stale-lock",
        help="Atomically reclaim a stale supervisor lock after recording the prior owner",
    )
    # Round-8 P1 fix: --state and --workspace are no longer
    # required. A replacement run that has not yet completed
    # init can recover a stale lease before its state file
    # exists. The caller MUST supply the bootstrap identity
    # flags below instead.
    p_recover.add_argument("--state", help="Path to CONTROLLER_STATE.json (optional when --recovered-run-id is given)")
    p_recover.add_argument("--workspace", help="Run workspace directory (optional when scope flags are given)")
    p_recover.add_argument("--lock-dir",
                           help="Override the supervisor-lock directory (default: host-wide)")
    p_recover.add_argument("--staleness-evidence", required=True,
                           help="One-line description of the evidence declaring the lock stale")
    p_recover.add_argument("--recovered-run-id", required=True,
                           help="The replacement run's run_id (used as the recovered lease owner)")
    p_recover.add_argument("--recovered-state-path",
                           help="Path to the replacement run's CONTROLLER_STATE.json (optional; "
                                "if provided, must NOT exist yet OR will be written by init)")
    p_recover.add_argument("--repository",
                           help="Repository scope for the recovered lease (e.g. Slideshow11/Automated-Edge-Discovery)")
    p_recover.add_argument("--target-pr-number",
                           help="Target PR number for the recovered lease (optional)")
    p_recover.add_argument("--mutation-target",
                           help="Mutation target identifier for the recovered lease (optional)")

    p_outstanding = sub.add_parser(
        "list-outstanding-mutations",
        help="List all authorized mutations with no terminal result",
    )
    p_outstanding.add_argument("--workspace", required=True, help="Run workspace directory")

    return parser


def _record_persistent_guard_snapshot(args: argparse.Namespace) -> None:
    """Record the persistent mutation guard snapshot path after the pre-run snapshot.

    This is called before AED work starts. The runner should have already run:
      python3 scripts/local/check_persistent_mutation_guard.py snapshot \
        --root /home/max/.hermes --output <snapshot-path>

    This function only records the path and updates guard status to snapshot_recorded.
    It does NOT execute the guard script.
    """
    state = _load_state(args.state)

    guard = state.get("persistent_mutation_guard", {})
    guard["status"] = "snapshot_recorded"
    guard["root"] = str(args.root)
    guard["snapshot_path"] = str(args.snapshot_path)
    guard["last_checked_at"] = _utcnow()

    state["persistent_mutation_guard"] = guard
    state["updated_at"] = _utcnow()

    _save_state(state, args.state)

    print(f"Recorded persistent mutation guard snapshot")
    print(f"  status: snapshot_recorded")
    print(f"  root: {guard['root']}")
    print(f"  snapshot_path: {guard['snapshot_path']}")


def _record_persistent_guard_compare(args: argparse.Namespace) -> None:
    """Record the persistent mutation guard compare result after AED work completes.

    This reads the guard's compare JSON output and updates controller state accordingly.
    - If recommendation is PASS → guard status becomes 'clean'
    - If recommendation is BLOCK → next_action becomes request_human, reason=persistent_mutation_detected
    - If compare JSON is missing or malformed → next_action becomes request_human, reason=persistent_mutation_guard_error

    The controller does NOT execute the guard script — the runner calls the guard and
    records the result here. The controller does NOT write to /home/max/.hermes.

    Safety invariants (hermes_touched, dispatch_occurred, production_board_touched) are
    checked first. If any is already true, RUN_FAILED_SAFETY is set and the guard result
    does NOT override the hard stop.
    """
    state = _load_state(args.state)

    # Safety hard stop: check BEFORE processing guard result.
    # If Hermes was already touched before this guard check, hard stop wins.
    safety = state.get("safety_invariants", {})
    if any(safety.get(k) for k in ("hermes_touched", "dispatch_occurred", "production_board_touched")):
        state["overall_status"] = "RUN_FAILED_SAFETY"
        state["next_action"] = {"action": "stop", "task_id": None, "reason": "safety invariant violated"}
        state["human_action_required"] = False
        state["updated_at"] = _utcnow()
        _save_state(state, args.state)
        print(f"Safety invariant already violated; guard result ignored.")
        print(f"  overall_status: RUN_FAILED_SAFETY")
        print(f"  next action: stop — safety invariant violated")
        return

    compare_json_path = Path(args.compare_json)

    # Read the compare JSON
    if not compare_json_path.exists():
        # Missing compare report → error state
        guard = state.get("persistent_mutation_guard", {})
        guard["status"] = "error"
        guard["compare_json_path"] = str(compare_json_path)
        guard["compare_md_path"] = str(args.compare_md) if args.compare_md else None
        guard["last_checked_at"] = _utcnow()
        state["persistent_mutation_guard"] = guard
        state["next_action"] = {
            "action": "request_human",
            "task_id": None,
            "reason": "persistent_mutation_guard_error",
        }
        state["human_action_required"] = True
        state["updated_at"] = _utcnow()
        _save_state(state, args.state)
        print(f"ERROR: compare JSON not found: {compare_json_path}")
        print(f"  status: error")
        print(f"  next action: request_human — persistent_mutation_guard_error")
        return

    try:
        with open(compare_json_path) as f:
            report = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        # Malformed compare JSON → error state
        guard = state.get("persistent_mutation_guard", {})
        guard["status"] = "error"
        guard["compare_json_path"] = str(compare_json_path)
        guard["compare_md_path"] = str(args.compare_md) if args.compare_md else None
        guard["last_checked_at"] = _utcnow()
        state["persistent_mutation_guard"] = guard
        state["next_action"] = {
            "action": "request_human",
            "task_id": None,
            "reason": "persistent_mutation_guard_error",
        }
        state["human_action_required"] = True
        state["updated_at"] = _utcnow()
        _save_state(state, args.state)
        print(f"ERROR: failed to parse compare JSON: {e}")
        print(f"  status: error")
        print(f"  next action: request_human — persistent_mutation_guard_error")
        return

    recommendation = report.get("recommendation", "")
    blocked_changes = report.get("blocked_changes", [])
    allowed_changes = report.get("allowed_changes", [])

    guard = state.get("persistent_mutation_guard", {})
    guard["compare_json_path"] = str(compare_json_path)
    guard["compare_md_path"] = str(args.compare_md) if args.compare_md else None
    guard["blocked_changes_count"] = len(blocked_changes)
    guard["allowed_changes_count"] = len(allowed_changes)
    guard["last_checked_at"] = _utcnow()

    if recommendation == "PASS":
        guard["status"] = "clean"
        state["persistent_mutation_guard"] = guard
        state["updated_at"] = _utcnow()
        # NOTE: clean guard does NOT grant merge authority — run must still complete all tasks
        _save_state(state, args.state)
        print(f"Persistent mutation guard: PASS")
        print(f"  blocked_changes: {len(blocked_changes)}")
        print(f"  allowed_changes: {len(allowed_changes)}")
        print(f"  status: clean")
        print(f"  note: clean guard does not grant merge authority")
    elif recommendation == "BLOCK":
        guard["status"] = "blocked"
        state["persistent_mutation_guard"] = guard
        state["next_action"] = {
            "action": "request_human",
            "task_id": None,
            "reason": "persistent_mutation_detected",
        }
        state["human_action_required"] = True
        state["updated_at"] = _utcnow()
        _save_state(state, args.state)
        print(f"Persistent mutation guard: BLOCK")
        print(f"  blocked_changes: {len(blocked_changes)}")
        print(f"  next action: request_human — persistent_mutation_detected")
    else:
        # Unknown recommendation → error
        guard["status"] = "error"
        state["persistent_mutation_guard"] = guard
        state["next_action"] = {
            "action": "request_human",
            "task_id": None,
            "reason": "persistent_mutation_guard_error",
        }
        state["human_action_required"] = True
        state["updated_at"] = _utcnow()
        _save_state(state, args.state)
        print(f"ERROR: unknown guard recommendation: {recommendation!r}")
        print(f"  status: error")
        print(f"  next action: request_human — persistent_mutation_guard_error")


def _state_target_pr_number(state: dict) -> Optional[int]:
    rid = state.get("run_identity") or {}
    return rid.get("target_pr_number")


def _state_repository(state: dict) -> Optional[str]:
    rid = state.get("run_identity") or {}
    return rid.get("repository")


def _state_mutation_target(state: dict) -> Optional[str]:
    rid = state.get("run_identity") or {}
    return rid.get("mutation_target")


def _authorize_mutation(args: argparse.Namespace) -> None:
    # Round-10 P1 fix: acquire the mutation-journal sentinel
    # BEFORE loading state, checking lease, or any other read.
    # Hold the sentinel through the append. This closes the
    # remaining TOCTOU window where authorize reads RUN_ACTIVE
    # then finalize persists RUN_COMPLETE; finalize's
    # outstanding-mutations check runs under the sentinel so
    # authorize cannot append after finalize completes.
    workspace_path = Path(args.workspace)
    mutations_path_file = workspace_path / _mutation_auth.MUTATIONS_FILENAME
    sentinel_path = mutations_path_file.with_suffix(
        mutations_path_file.suffix + ".auth-sentinel"
    )
    from scripts.local.aed_supervisor_lock import (
        _acquire_sentinel_fd,
        _release_sentinel_fd,
    )
    sentinel_fd = _acquire_sentinel_fd(sentinel_path, max_attempts=20)
    if sentinel_fd is None:
        print(
            "ERROR: cannot authorize mutation: mutation journal lock busy",
            file=sys.stderr,
        )
        sys.exit(12)
    try:
        state = _load_state(args.state)
        _authorize_mutation_locked(args, state, sentinel_fd)
    finally:
        _release_sentinel_fd(sentinel_fd, sentinel_path)


def _authorize_mutation_locked(args: argparse.Namespace, state: dict, sentinel_fd: int) -> None:
    """Body of _authorize_mutation, executed while holding the
    mutation-journal sentinel."""
    # Round-120 P1 fix (round 7): verify the launch receipt
    # exists and matches the current run before authorizing. The
    # receipt is the documented precondition for any repository
    # or GitHub mutation; if it is missing, belongs to an earlier
    # run, or was never emitted, refuse.
    state_workspace_dir = Path(state.get("workspace", ""))
    receipt_path = state_workspace_dir / "LAUNCH_RECEIPT.json"
    if not receipt_path.is_file():
        print(
            f"ERROR: cannot authorize mutation: launch receipt "
            f"missing at {receipt_path}",
            file=sys.stderr,
        )
        sys.exit(13)
    try:
        with open(receipt_path) as f:
            receipt = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(
            f"ERROR: cannot authorize mutation: failed to read "
            f"launch receipt at {receipt_path}: {e}",
            file=sys.stderr,
        )
        sys.exit(13)
    receipt_run_id = (receipt.get("run_identity") or {}).get("run_id")
    if receipt_run_id != state.get("run_id"):
        print(
            f"ERROR: cannot authorize mutation: launch receipt "
            f"run_id={receipt_run_id!r} does not match state "
            f"run_id={state.get('run_id')!r}",
            file=sys.stderr,
        )
        sys.exit(13)
    receipt_workspace = (receipt.get("run_identity") or {}).get("workspace") or (
        receipt.get("workspace")
    )
    # Validate workspace path matches.
    if receipt_workspace and str(Path(receipt_workspace).resolve()) != str(Path(state.get("workspace", "")).resolve()):
        print(
            f"ERROR: cannot authorize mutation: launch receipt "
            f"workspace={receipt_workspace!r} does not match state "
            f"workspace={state.get('workspace')!r}",
            file=sys.stderr,
        )
        sys.exit(13)
    # Round-8 P1 fix: bind receipt validation to the selected
    # state file. A copied state file at a different path would
    # otherwise pass the run_id + workspace checks above (which
    # only compare against state contents, not the caller-
    # supplied --state path). Require receipt['state_path'] to
    # resolve to args.state so the receipt is bound to the state
    # file the operator actually passed to authorize-mutation.
    receipt_state_path = receipt.get("state_path")
    if not receipt_state_path:
        print(
            "ERROR: cannot authorize mutation: launch receipt "
            "missing state_path field",
            file=sys.stderr,
        )
        sys.exit(13)
    if str(Path(receipt_state_path).resolve()) != str(Path(args.state).resolve()):
        print(
            f"ERROR: cannot authorize mutation: launch receipt "
            f"state_path={receipt_state_path!r} does not match "
            f"--state={args.state!r}",
            file=sys.stderr,
        )
        sys.exit(13)
    # Round-120 P2 fix: validate that the caller's --workspace
    # matches the workspace recorded in the state file. A path
    # mix-up could authorize a mutation to a journal the
    # finalization gate never sees.
    state_workspace = state.get("workspace")
    if state_workspace and str(Path(args.workspace).resolve()) != str(Path(state_workspace).resolve()):
        print(
            f"ERROR: workspace mismatch: --workspace={args.workspace} "
            f"does not match state.workspace={state_workspace}",
            file=sys.stderr,
        )
        sys.exit(9)
    # Round-120 P1 fix (round 3): reject authorization after
    # finalization. A finalized run has no live lease; permitting
    # mutation authorization here would let an external executor
    # perform a mutation with no active lock.
    overall_status = state.get("overall_status")
    if overall_status and overall_status != "RUN_ACTIVE":
        print(
            f"ERROR: cannot authorize mutation: run is not active "
            f"(overall_status={overall_status}). The run must be "
            f"in RUN_ACTIVE state to authorize mutations.",
            file=sys.stderr,
        )
        sys.exit(10)
    rid = state.get("run_identity") or {}
    # Round-19 P1 fix: require an exact 40-character lowercase
    # hex SHA for --expected-target-sha when the mutation type
    # is squash_merge. The previous code accepted None, an empty
    # string, a short SHA prefix, or any unvalidated value, which
    # let an executor merge a PR whose head changed between
    # authorization and execution. Fail closed at authorization
    # time so the executor can compare against an exact head.
    if args.mutation_type == "squash_merge":
        target_sha = args.expected_target_sha or ""
        if not _is_full_sha(target_sha):
            print(
                "ERROR: cannot authorize mutation: --expected-target-sha "
                "must be a full 40-character lowercase hex SHA for "
                f"squash_merge, got {target_sha!r}",
                file=sys.stderr,
            )
            sys.exit(14)
    # P2 fix (round 4): resolve owner_state_path to an absolute
    # path. Relative paths become relative to the CURRENT process's
    # CWD when later read, so a controller invocation from a
    # different working directory would fail closed as
    # indeterminate. Stored paths must be absolute.
    if rid.get("lock_dir") and not Path(rid["lock_dir"]).is_absolute():
        rid["lock_dir"] = str(Path(rid["lock_dir"]).resolve())
    scope = {
        "repository": rid.get("repository") or "",
        "target_pr_number": rid.get("target_pr_number"),
        "mutation_target": rid.get("mutation_target"),
    }
    # Round-120 P1 fix (round 4): verify the live scope lock still
    # belongs to this run. If another worker has recovered our
    # stale lease, the state may say RUN_ACTIVE but we no longer
    # own the supervisor lock; refuse authorization.
    if scope.get("repository"):
        run_id = state.get("run_id", "unknown")
        lock_base_for_check = None
        if rid.get("lock_dir"):
            lock_base_for_check = Path(rid["lock_dir"])
        if not _supervisor_lock.is_lease_held_by_run(
            scope=scope, owner_run_id=run_id, base_dir=lock_base_for_check
        ):
            print(
                f"ERROR: cannot authorize mutation: no live supervisor "
                f"lock held by run_id={run_id} for scope={scope}",
                file=sys.stderr,
            )
            sys.exit(11)
    # Round-120 P1 fix (round 4): reject mutation targets outside
    # the locked scope. If the state scope is repo+A but the
    # caller supplies --mutation-target B, we would authorize a
    # mutation against B while another controller legitimately
    # holds B's lock.
    #
    # Round-20 P2 fix: when the run was initialized WITHOUT a
    # mutation target (e.g. a PR-scoped run), `state_mutation_target`
    # is None. The previous check accepted any caller-supplied
    # --mutation-target in that case because the guard
    # `state_mutation_target` short-circuited the comparison.
    # The caller could then authorize a mutation against an
    # arbitrary target while the held lease protects a different
    # PR/repository-run lock path, letting another controller
    # holding the target-specific lock mutate the same target
    # concurrently. Treat a supplied target as a mismatch
    # whenever it is not exactly the target recorded in the
    # state scope (including the None case where the scope has
    # no target).
    state_mutation_target = _state_mutation_target(state)
    if args.mutation_target and args.mutation_target != state_mutation_target:
        print(
            f"ERROR: --mutation-target={args.mutation_target} does not "
            f"match state scope mutation_target={state_mutation_target}",
            file=sys.stderr,
        )
        sys.exit(12)
    # Round-120 P1 fix (round 6): canonicalize the repository
    # used in the authorization record. When the state has no
    # repository, two lexically different --workspace paths
    # (e.g. /tmp/ws vs /tmp/../tmp/ws) would produce different
    # duplicate keys, allowing two calls to authorize the same
    # mutation. Use the state.workspace value, which is
    # canonicalized at init time.
    state_workspace_for_repo = state.get("workspace")
    if rid.get("repository"):
        repository = rid["repository"]
    elif state_workspace_for_repo:
        # Canonical path → the state workspace is the implicit
        # "repository" scope. This matches the existing fallback
        # below, but the value is now the canonical absolute path
        # recorded at init.
        repository = str(Path(state_workspace_for_repo).resolve())
    else:
        repository = args.workspace
    req = _mutation_auth.AuthorizationRequest(
        run_id=state.get("run_id", "unknown"),
        repository=repository,
        target_pr_number=_state_target_pr_number(state),
        mutation_target=args.mutation_target or _state_mutation_target(state),
        mutation_type=args.mutation_type,
        expected_main_sha=args.expected_main_sha,
        expected_target_sha=args.expected_target_sha,
        pending_action=args.pending_action,
    )
    outcome = _mutation_auth.authorize(Path(args.workspace), req, sentinel_fd=sentinel_fd)
    if not outcome.ok:
        print(
            f"ERROR: mutation authorization rejected: {outcome.reason}; "
            f"existing_mutation_id={outcome.record.get('mutation_id') if outcome.record else None!r}",
            file=sys.stderr,
        )
        sys.exit(3)
    assert outcome.record is not None
    print(f"Authorized mutation {outcome.mutation_id}")
    print(f"  type:                {outcome.record.get('mutation_type')}")
    print(f"  run_id:              {outcome.record.get('run_id')}")
    print(f"  repository:          {outcome.record.get('repository')}")
    print(f"  target_pr_number:    {outcome.record.get('target_pr_number')}")
    print(f"  expected_main_sha:   {outcome.record.get('expected_main_sha') or '—'}")
    print(f"  expected_target_sha: {outcome.record.get('expected_target_sha') or '—'}")
    print(f"  pending_action:      {outcome.record.get('pending_action')}")


def _record_mutation_result(args: argparse.Namespace) -> None:
    try:
        updated = _mutation_auth.record_result(
            Path(args.workspace),
            mutation_id=args.mutation_id,
            status=args.status,
            evidence=args.evidence,
            actual_main_sha=args.actual_main_sha,
            actual_target_sha=args.actual_target_sha,
            error_detail=args.error_detail,
        )
    except KeyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(4)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(5)
    result = updated.get("result") or {}
    print(f"Recorded mutation result {args.mutation_id} -> {result.get('status')}")


def _resolve_lock_base(args, workspace: Path) -> Optional[Path]:
    """Resolve the lock base directory for inspect-lock and
    recover-stale-lock. The CLI --lock-dir flag is the primary
    override. When absent, fall back to the lock directory
    persisted in run_identity.lock_dir (set by init) so that
    later commands find the same directory the init used.
    Finally, fall back to None which the supervisor lock
    module treats as the host-wide default."""
    lock_dir_arg = getattr(args, "lock_dir", None)
    if lock_dir_arg:
        return Path(lock_dir_arg)
    state = _load_state(getattr(args, "state", ""))
    rid = state.get("run_identity") or {}
    if rid.get("lock_dir"):
        return Path(rid["lock_dir"])
    return None


def _inspect_lock(args: argparse.Namespace) -> None:
    state = _load_state(args.state)
    rid = state.get("run_identity") or {}
    scope = {
        "repository": rid.get("repository") or "",
        "target_pr_number": rid.get("target_pr_number"),
        "mutation_target": rid.get("mutation_target"),
    }
    if not scope["repository"]:
        print("ERROR: state has no repository scope; cannot inspect lock", file=sys.stderr)
        sys.exit(6)
    scope_key = _supervisor_lock.build_scope_key(
        repository=scope["repository"],
        target_pr_number=scope["target_pr_number"],
        mutation_target=scope["mutation_target"],
    )
    path = _supervisor_lock.lock_path_for(
        scope_key, base_dir=_resolve_lock_base(args, Path(args.workspace))
    )
    evidence = _supervisor_lock.assess_from_path(path)
    if evidence is None:
        print(f"No lock present at {path}")
        return
    print(f"Lock at {path}:")
    print(f"  is_alive:           {evidence.is_alive}")
    print(f"  is_indeterminate:   {evidence.is_indeterminate}")
    print(f"  reason:             {evidence.reason}")
    print(f"  pid_exists:         {evidence.pid_exists}")
    print(f"  stat_match:         {evidence.stat_start_time_match}")
    print(f"  ctime_match:        {evidence.ctime_match}")
    existing = _supervisor_lock.read(path)
    if existing:
        print(f"  owner_run_id:       {existing.get('owner_run_id')}")
        print(f"  owner_pid:          {existing.get('owner_pid')}")
        print(f"  created_at:         {existing.get('created_at')}")


def _recover_stale_lock(args: argparse.Namespace) -> None:
    # Round-8 P1 fix: allow recovery before the replacement
    # run's state file exists. The caller now supplies bootstrap
    # identity directly via CLI flags. The previous behavior of
    # loading args.state is preserved when --state IS provided
    # so existing operators/scripts continue to work.
    scope: dict = {}
    recovered_run_id: str
    recovered_state_path: Optional[str] = None
    lock_dir_override: Optional[Path] = None
    if getattr(args, "lock_dir", None):
        lock_dir_override = Path(args.lock_dir)

    if args.state and os.path.exists(args.state):
        # Legacy path: load the predecessor/replacement state.
        state = _load_state(args.state)
        rid = state.get("run_identity") or {}
        scope = {
            "repository": rid.get("repository") or "",
            "target_pr_number": rid.get("target_pr_number"),
            "mutation_target": rid.get("mutation_target"),
        }
        recovered_run_id = getattr(args, "recovered_run_id", None) or state.get("run_id", "unknown")
        recovered_state_path = str(Path(args.state).resolve())
        # The legacy path allows the state to provide the scope,
        # but the recovered run_id MUST come from CLI so the
        # replacement is bound to its own identity.
        if not scope.get("repository") and not getattr(args, "repository", None):
            print("ERROR: state has no repository scope and --repository not given; cannot recover lock",
                  file=sys.stderr)
            sys.exit(6)
    else:
        # Bootstrap path: caller supplies scope and identity directly.
        scope = {
            "repository": getattr(args, "repository", None) or "",
            "target_pr_number": getattr(args, "target_pr_number", None),
            "mutation_target": getattr(args, "mutation_target", None),
        }
        recovered_run_id = args.recovered_run_id
        if getattr(args, "recovered_state_path", None):
            recovered_state_path = str(Path(args.recovered_state_path).resolve())

    # Apply CLI overrides for scope (in case legacy state scope
    # was empty but CLI flags give us the scope).
    if not scope.get("repository") and getattr(args, "repository", None):
        scope["repository"] = args.repository
    if scope.get("target_pr_number") is None and getattr(args, "target_pr_number", None):
        scope["target_pr_number"] = args.target_pr_number
    if not scope.get("mutation_target") and getattr(args, "mutation_target", None):
        scope["mutation_target"] = args.mutation_target

    if not scope.get("repository"):
        print("ERROR: no repository scope available; pass --repository, --state, or scope via run_identity",
              file=sys.stderr)
        sys.exit(6)
    # Round-16 P2 fix (Allow recovery of repository-wide locks):
    # a scope with only --repository (no PR number, no mutation
    # target) is the valid repository-wide scope produced by
    # build_scope_key as `repo:<r>|run`. Removing the prior
    # rejection lets recovery proceed for repository-wide locks.

    proc_evidence = _run_identity.capture_process_start_evidence() or {
        "pid": os.getpid(),
        "stat_start_time": None,
        "stat_start_time_text": None,
        "ctime_ns": None,
        "source": "unknown",
    }
    host_identity = _run_identity.capture_host_identity()
    # Compute base_dir using the same precedence as try_acquire:
    # CLI --lock-dir, then run_identity.lock_dir (from state when
    # available), then the host-wide default (None).
    base_dir = lock_dir_override
    if base_dir is None and recovered_state_path:
        # Legacy state-provided path may include lock_dir.
        try:
            legacy_state = _load_state(args.state)
            legacy_rid = legacy_state.get("run_identity") or {}
            if legacy_rid.get("lock_dir"):
                base_dir = Path(legacy_rid["lock_dir"])
        except Exception:
            pass
    outcome = _supervisor_lock.recover_stale(
        scope=scope,
        recovered_by_run_id=recovered_run_id,
        recovered_by_host=host_identity,
        recovered_by_pid=proc_evidence["pid"],
        recovered_by_start_evidence=proc_evidence,
        recovered_by_state_path=recovered_state_path,
        staleness_evidence=args.staleness_evidence,
        base_dir=base_dir,
    )
    if not outcome.ok:
        print(
            f"ERROR: stale-lock recovery rejected: {outcome.reason}; "
            f"current_owner_run_id={(outcome.owner or {}).get('owner_run_id')!r}",
            file=sys.stderr,
        )
        sys.exit(7)
    print(f"Recovered stale lock for scope {scope}")
    print(f"  reason: {outcome.reason}")
    print(f"  recovered_by_run_id: {recovered_run_id}")


def _list_outstanding_mutations(args: argparse.Namespace) -> None:
    outstanding = _mutation_auth.outstanding_mutations(Path(args.workspace))
    if not outstanding:
        print("No outstanding mutations.")
        return
    print(f"Outstanding mutations ({len(outstanding)}):")
    for m in outstanding:
        print(f"  - {m.get('mutation_id')} ({m.get('mutation_type')}) run_id={m.get('run_id')}")


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
        "record-codex-review": _record_codex_review,
        "record-codex-repair-result": _record_codex_repair_result,
        "record-autonomous-repair-plan": _record_autonomous_repair_plan,
        "record-autonomous-repair-validation": _record_autonomous_repair_validation,
        "finalize-run": _finalize_run,
        "record-persistent-guard-snapshot": _record_persistent_guard_snapshot,
        "record-persistent-guard-compare": _record_persistent_guard_compare,
        "authorize-mutation": _authorize_mutation,
        "record-mutation-result": _record_mutation_result,
        "inspect-lock": _inspect_lock,
        "recover-stale-lock": _recover_stale_lock,
        "list-outstanding-mutations": _list_outstanding_mutations,
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