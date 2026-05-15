#!/usr/bin/env python3
"""
AED Merge and Action Audit Log — append-only event recorder.

Purpose:
  Every major merge or controlled external action is recorded as one JSON
  object per line (JSONL) in a local audit log file.  This gives AED a
  durable, queryable history of governance events without external services.

V1 scope (deliberately minimal):
  - Append one validated JSON object per line.
  - Support dry-run mode (print without writing).
  - Validate required fields: event_type, pr_number (for pr_merge events),
    head_sha, merge_sha (for pr_merge events), timestamp.
  - Reject malformed SHA fields (must be 40-hex characters).
  - Record dispatch_occurred and production_board_touched explicitly.
  - No Hermes calls, no GitHub API calls, no post-merge hooks.

Usage:
  # Dry-run (print to stdout):
  python scripts/local/append_merge_action_audit.py \
    --event-type pr_merge \
    --pr-number 217 \
    --head-sha 62e602e374cf666cf63e29de3bd28acb0fae97ea \
    --merge-sha d3de12a348da42009767887d05ff6dcd66b1c900 \
    --merged-at 2026-05-14T20:09:40Z \
    --ci-status success \
    --codex-status clean \
    --scope-status clean \
    --authorization-phrase "I confirm merge PR #217 ..." \
    --gate-catches codex,scope \
    --dry-run

  # Backward-compatible --authorization (emits authorization_phrase):
  python scripts/local/append_merge_action_audit.py \
    --event-type pr_merge \
    --pr-number 217 \
    --head-sha 62e602e374cf666cf63e29de3bd28acb0fae97ea \
    --merge-sha d3de12a348da42009767887d05ff6dcd66b1c900 \
    --merged-at 2026-05-14T20:09:40Z \
    --authorization "I confirm merge PR #217 ..." \
    --dry-run

  # Append to default log (~/.hermes/aed/audit/log.jsonl):
  python scripts/local/append_merge_action_audit.py --event-type pr_merge ...

  # Append to custom path:
  python scripts/local/append_merge_action_audit.py --output /path/to/log.jsonl ...

  # Controlled smoke create event:
  python scripts/local/append_merge_action_audit.py \
    --event-type controlled_smoke_create \
    --board aed-test \
    --task-id t_58d1338c \
    --status triage \
    --assignee "" \
    --no-dispatch-occurred \
    --no-worker-run-spawned \
    --no-production-board-touched \
    --dry-run

  # Blocked action event:
  python scripts/local/append_merge_action_audit.py \
    --event-type blocked_action \
    --action-requested "hermes kanban dispatch t_abc123" \
    --blocked-reason "dispatch requires explicit authorization" \
    --stop-rule-triggered "unreviewed_external_mutation" \
    --files-or-boards-involved aed t_abc123 \
    --remediation-path "Obtain explicit dispatch authorization from human operator" \
    --no-dispatch-occurred \
    --no-production-board-touched \
    --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AUDIT_LOG_VERSION = 1
VALID_EVENT_TYPES = frozenset([
    "pr_merge",
    "controlled_smoke_create",
    "external_action",
    "blocked_action",
])
VALID_CI_STATUSES = frozenset(["success", "failure", "pending", "unknown"])
VALID_CODEX_STATUSES = frozenset(["clean", "suggestions", "pending", "unknown"])
VALID_SCOPE_STATUSES = frozenset(["clean", "dirty", "unknown"])
DEFAULT_LOG_PATH = Path(os.environ.get(
    "AED_AUDIT_LOG",
    str(Path.home() / ".hermes" / "aed" / "audit" / "log.jsonl"),
))

# -----------------------------------------------------------------------------
# Authorization resolution helpers
# -----------------------------------------------------------------------------

def _resolve_authorization(
    authorization_arg: str | None,
    authorization_phrase_arg: str | None,
) -> tuple[str | None, str | None]:
    """
    Resolve authorization field handling for Trace Policy V1 alignment.

    Returns (value_to_emit, deprecation_warning_or_none).
    - Prefers authorization_phrase when available.
    - Keeps --authorization as a backward-compatible CLI alias.
    - Fails if both are provided and differ.
    - If both match, emits only authorization_phrase.
    """
    if authorization_arg is None and authorization_phrase_arg is None:
        return None, None
    if authorization_arg is not None and authorization_phrase_arg is None:
        return authorization_arg, None
    if authorization_arg is None and authorization_phrase_arg is not None:
        return authorization_phrase_arg, None
    # Both provided
    if authorization_arg != authorization_phrase_arg:
        raise ValueError(
            "--authorization and --authorization-phrase were both provided "
            "but have different values. Use only --authorization-phrase, "
            "or remove --authorization to avoid ambiguity."
        )
    # Both provided and identical — emit authorization_phrase, drop the alias
    return authorization_phrase_arg, (
        "note: --authorization is deprecated; --authorization-phrase will be "
        "used going forward"
    )


# -----------------------------------------------------------------------------
# Validation helpers
# -----------------------------------------------------------------------------

def _is_valid_sha(value: str) -> bool:
    """Return True if value is a 40-character hexadecimal string."""
    if not isinstance(value, str):
        return False
    if len(value) != 40:
        return False
    try:
        int(value, 16)
        return True
    except ValueError:
        return False


def _validate_pr_merge_fields(entry: dict[str, Any]) -> list[str]:
    """Return list of error messages for a pr_merge event."""
    errors = []
    required = ["pr_number", "head_sha", "merge_sha", "merged_at"]
    for field in required:
        if field not in entry or not entry[field]:
            errors.append(f"{field} is required for pr_merge events")
    if "pr_number" in entry:
        try:
            n = int(entry["pr_number"])
            if n <= 0:
                errors.append("pr_number must be a positive integer")
        except (TypeError, ValueError):
            errors.append("pr_number must be an integer")
    for sha_field in ("head_sha", "merge_sha"):
        if sha_field in entry and entry[sha_field] and not _is_valid_sha(entry[sha_field]):
            errors.append(f"{sha_field} must be a 40-hex character SHA (got '{entry[sha_field]}')")
    return errors


def _validate_controlled_smoke_create_fields(entry: dict[str, Any]) -> list[str]:
    """Return list of error messages for a controlled_smoke_create event."""
    errors = []
    if "board" not in entry or not entry["board"]:
        errors.append("board is required for controlled_smoke_create events")
    if "task_id" not in entry or not entry["task_id"]:
        errors.append("task_id is required for controlled_smoke_create events")
    # Governance fields must be present and explicitly False when applicable
    if "dispatch_occurred" not in entry:
        errors.append("dispatch_occurred is required for controlled_smoke_create events")
    elif entry["dispatch_occurred"] is not False:
        errors.append("dispatch_occurred must be False for controlled_smoke_create events")
    if "production_board_touched" not in entry:
        errors.append("production_board_touched is required for controlled_smoke_create events")
    elif entry["production_board_touched"] is not False:
        errors.append("production_board_touched must be False for controlled_smoke_create events")
    return errors


def _validate_blocked_action_fields(entry: dict[str, Any]) -> list[str]:
    """Return list of error messages for a blocked_action event."""
    errors = []
    required_event_fields = [
        ("action_requested", "action_requested is required for blocked_action events"),
        ("blocked_reason", "blocked_reason is required for blocked_action events"),
        ("stop_rule_triggered", "stop_rule_triggered is required for blocked_action events"),
        ("files_or_boards_involved", "files_or_boards_involved is required for blocked_action events"),
        ("remediation_path", "remediation_path is required for blocked_action events"),
    ]
    for field, msg in required_event_fields:
        if field not in entry or not entry[field]:
            errors.append(msg)
    # Governance booleans — must be present and False when applicable
    if "dispatch_occurred" not in entry:
        errors.append("dispatch_occurred is required for blocked_action events")
    elif entry["dispatch_occurred"] is not False:
        errors.append("dispatch_occurred must be False for blocked_action events")
    if "production_board_touched" not in entry:
        errors.append("production_board_touched is required for blocked_action events")
    elif entry["production_board_touched"] is not False:
        errors.append("production_board_touched must be False for blocked_action events")
    return errors


def _validate_entry(entry: dict[str, Any]) -> list[str]:
    """Return list of error messages for a complete audit entry."""
    errors = []
    if "event_type" not in entry or entry["event_type"] not in VALID_EVENT_TYPES:
        errors.append(
            f"event_type is required and must be one of {sorted(VALID_EVENT_TYPES)} "
            f"(got '{entry.get('event_type', '')}')"
        )
        return errors  # can't validate further without event_type
    if entry["event_type"] == "pr_merge":
        errors.extend(_validate_pr_merge_fields(entry))
    elif entry["event_type"] == "controlled_smoke_create":
        errors.extend(_validate_controlled_smoke_create_fields(entry))
    elif entry["event_type"] == "blocked_action":
        errors.extend(_validate_blocked_action_fields(entry))
    return errors


# ---------------------------------------------------------------------------
# Entry builder
# ---------------------------------------------------------------------------

def build_entry(
    event_type: str,
    *,
    pr_number: int | None = None,
    branch: str | None = None,
    head_sha: str | None = None,
    merge_sha: str | None = None,
    merged_at: str | None = None,
    ci_status: str | None = None,
    codex_status: str | None = None,
    scope_status: str | None = None,
    authorization: str | None = None,
    authorization_phrase: str | None = None,
    hermes_touched: bool | None = None,
    dispatch_occurred: bool | None = None,
    board: str | None = None,
    task_ids: list[str] | None = None,
    smoke_artifact_ids: list[str] | None = None,
    task_id: str | None = None,  # singular for single-task events (e.g. smoke_create)
    status: str | None = None,  # e.g. triage, done — for task-related events
    assignee: str | None = None,  # empty string = unassigned
    worker_run_spawned: bool | None = None,
    production_board_touched: bool | None = None,
    blocker_or_exception: str | None = None,
    gate_catches: dict[str, str] | None = None,
    action_requested: str | None = None,
    blocked_reason: str | None = None,
    stop_rule_triggered: str | None = None,
    files_or_boards_involved: list[str] | None = None,
    remediation_path: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build a validated audit log entry.

    All keyword arguments are optional except where required by event_type.
    Unknown extra keys are passed through as-is for forward compatibility.

    authorization_phrase is the canonical field for Trace Policy V1.
    authorization is accepted as a backward-compatible alias and is converted
    to authorization_phrase internally.
    gate_catches is a list of gate names that caught real issues.
    """
    entry: dict[str, Any] = {
        "audit_log_version": AUDIT_LOG_VERSION,
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if pr_number is not None:
        entry["pr_number"] = pr_number
    if branch is not None:
        entry["branch"] = branch
    if head_sha is not None:
        entry["head_sha"] = head_sha
    if merge_sha is not None:
        entry["merge_sha"] = merge_sha
    if merged_at is not None:
        entry["merged_at"] = merged_at
    if ci_status is not None:
        entry["ci_status"] = ci_status
    if codex_status is not None:
        entry["codex_status"] = codex_status
    if scope_status is not None:
        entry["scope_status"] = scope_status
    # authorization_phrase is canonical; authorization is backward-compat alias
    if authorization_phrase is not None:
        entry["authorization_phrase"] = authorization_phrase
    elif authorization is not None:
        entry["authorization_phrase"] = authorization
    if hermes_touched is not None:
        entry["hermes_touched"] = hermes_touched
    if dispatch_occurred is not None:
        entry["dispatch_occurred"] = dispatch_occurred
    if board is not None:
        entry["board"] = board
    if task_id is not None:
        entry["task_id"] = task_id
    if task_ids is not None:
        entry["task_ids"] = task_ids
    if smoke_artifact_ids is not None:
        entry["smoke_artifact_ids"] = smoke_artifact_ids
    if status is not None:
        entry["status"] = status
    if assignee is not None:
        entry["assignee"] = assignee
    if worker_run_spawned is not None:
        entry["worker_run_spawned"] = worker_run_spawned
    if production_board_touched is not None:
        entry["production_board_touched"] = production_board_touched
    if blocker_or_exception is not None:
        entry["blocker_or_exception"] = blocker_or_exception
    if gate_catches is not None:
        entry["gate_catches"] = gate_catches
    if action_requested is not None:
        entry["action_requested"] = action_requested
    if blocked_reason is not None:
        entry["blocked_reason"] = blocked_reason
    if stop_rule_triggered is not None:
        entry["stop_rule_triggered"] = stop_rule_triggered
    if files_or_boards_involved is not None:
        entry["files_or_boards_involved"] = files_or_boards_involved
    if remediation_path is not None:
        entry["remediation_path"] = remediation_path
    if extra is not None:
        entry.update(extra)
    return entry


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

def append_entry(entry: dict[str, Any], path: Path) -> None:
    """
    Append one JSON-serialized entry to the JSONL file at path.
    Creates parent directories if they do not exist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Append a merge or action audit log entry.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--event-type",
        required=True,
        choices=sorted(VALID_EVENT_TYPES),
        help="Type of governance event",
    )
    p.add_argument("--pr-number", type=int, help="PR number (required for pr_merge)")
    p.add_argument("--branch", help="Branch name")
    p.add_argument("--head-sha", dest="head_sha", help="40-hex commit SHA before merge")
    p.add_argument("--merge-sha", dest="merge_sha", help="40-hex merge commit SHA")
    p.add_argument(
        "--merged-at", dest="merged_at",
        help="ISO-8601 timestamp of merge (YYYY-MM-DDTHH:MM:SSZ)"
    )
    p.add_argument(
        "--ci-status", dest="ci_status",
        choices=sorted(VALID_CI_STATUSES),
        help="CI status at time of merge"
    )
    p.add_argument(
        "--codex-status", dest="codex_status",
        choices=sorted(VALID_CODEX_STATUSES),
        help="Codex review status"
    )
    p.add_argument(
        "--scope-status", dest="scope_status",
        choices=sorted(VALID_SCOPE_STATUSES),
        help="Scope review status"
    )
    p.add_argument(
        "--authorization-phrase", dest="authorization_phrase",
        help="Authorization phrase (canonical, Trace Policy V1)"
    )
    p.add_argument(
        "--authorization", help="Authorization phrase (backward-compatible alias for --authorization-phrase)"
    )
    p.add_argument(
        "--gate-catches",
        help="Comma-separated list of gate names that caught issues, e.g. codex,scope,ci"
    )
    p.add_argument(
        "--hermes-touched",
        dest="hermes_touched",
        const=True,
        action="store_const",
        default=None,
        help="Set if Hermes was called",
    )
    p.add_argument(
        "--no-hermes-touched",
        dest="hermes_touched",
        const=False,
        action="store_const",
        help="Set explicitly if Hermes was NOT called",
    )
    p.add_argument(
        "--dispatch-occurred",
        dest="dispatch_occurred",
        const=True,
        action="store_const",
        default=None,
        help="Set if a worker was dispatched",
    )
    p.add_argument(
        "--no-dispatch-occurred",
        dest="dispatch_occurred",
        const=False,
        action="store_const",
        help="Set explicitly if no worker was dispatched",
    )
    p.add_argument("--board", help="Kanban board name (for smoke_create events)")
    p.add_argument("--task-id", dest="task_id", help="Task ID (for smoke_create events)")
    p.add_argument(
        "--task-ids", dest="task_ids", nargs="+",
        help="Multiple task IDs (for multi-task events)"
    )
    p.add_argument(
        "--smoke-artifact-id", dest="smoke_artifact_ids", action="append", default=[],
        help="Smoke artifact IDs (repeatable)"
    )
    p.add_argument("--status", help="Task status (e.g. triage, done) — for smoke_create events")
    p.add_argument("--assignee", help="Assignee (empty string = unassigned)")
    p.add_argument(
        "--worker-run-spawned",
        dest="worker_run_spawned",
        const=True,
        action="store_const",
        default=None,
        help="Set if a worker run was spawned"
    )
    p.add_argument(
        "--no-worker-run-spawned",
        dest="worker_run_spawned",
        const=False,
        action="store_const",
        help="Set explicitly if no worker run spawned"
    )
    p.add_argument(
        "--production-board-touched",
        dest="production_board_touched",
        const=True,
        action="store_const",
        default=None,
        help="Set if the production board was touched"
    )
    p.add_argument(
        "--no-production-board-touched",
        dest="production_board_touched",
        const=False,
        action="store_const",
        help="Set explicitly if production board was NOT touched"
    )
    p.add_argument(
        "--blocker-or-exception", dest="blocker_or_exception",
        help="Blocker or exception note"
    )
    p.add_argument(
        "--action-requested", dest="action_requested",
        help="Action that was requested (for blocked_action events)"
    )
    p.add_argument(
        "--blocked-reason", dest="blocked_reason",
        help="Reason the action was blocked (for blocked_action events)"
    )
    p.add_argument(
        "--stop-rule-triggered", dest="stop_rule_triggered",
        help="Name of the stop rule that fired (for blocked_action events)"
    )
    p.add_argument(
        "--files-or-boards-involved", dest="files_or_boards_involved", nargs="+",
        help="File paths or board names involved (for blocked_action events)"
    )
    p.add_argument(
        "--remediation-path", dest="remediation_path",
        help="How to unblock the action (for blocked_action events)"
    )
    p.add_argument(
        "--output", "-o",
        default=str(DEFAULT_LOG_PATH),
        help=f"Path to JSONL audit log (default: {DEFAULT_LOG_PATH})"
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print entry to stdout without writing to the log file"
    )
    return p


def main() -> int:
    parser = _build_argparser()
    args = parser.parse_args()

    task_ids = args.task_ids if args.task_ids else None
    smoke_ids = args.smoke_artifact_ids if args.smoke_artifact_ids else None

    # Resolve authorization: --authorization-phrase canonical, --authorization alias
    # Fails fast if both differ
    try:
        resolved_auth, _ = _resolve_authorization(
            getattr(args, "authorization", None),
            getattr(args, "authorization_phrase", None),
        )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # Parse --gate-catches: comma-separated string -> list of strings -> object
    # Trace Policy V1 defines gate_catches as an object: {"gate_name": "description"} or {} when empty
    # CLI accepts comma-separated names for convenience (descriptions not provided via CLI)
    gate_catches: dict | None = None
    if getattr(args, "gate_catches", None):
        names = [g.strip() for g in args.gate_catches.split(",") if g.strip()]
        gate_catches = {name: "" for name in names}  # empty description for CLI-provided values
    # Emit gate_catches even when empty: Trace Policy V1 requires it on every PR trace
    if gate_catches is None:
        gate_catches = {}

    entry = build_entry(
        event_type=args.event_type,
        pr_number=args.pr_number,
        branch=args.branch,
        head_sha=args.head_sha,
        merge_sha=args.merge_sha,
        merged_at=args.merged_at,
        ci_status=args.ci_status,
        codex_status=args.codex_status,
        scope_status=args.scope_status,
        authorization_phrase=resolved_auth,
        hermes_touched=args.hermes_touched,
        dispatch_occurred=args.dispatch_occurred,
        board=args.board,
        task_id=args.task_id,
        task_ids=task_ids,
        smoke_artifact_ids=smoke_ids,
        status=args.status,
        assignee=args.assignee,
        worker_run_spawned=args.worker_run_spawned,
        production_board_touched=args.production_board_touched,
        blocker_or_exception=args.blocker_or_exception,
        gate_catches=gate_catches,
        action_requested=getattr(args, "action_requested", None),
        blocked_reason=getattr(args, "blocked_reason", None),
        stop_rule_triggered=getattr(args, "stop_rule_triggered", None),
        files_or_boards_involved=getattr(args, "files_or_boards_involved", None),
        remediation_path=getattr(args, "remediation_path", None),
    )

    errors = _validate_entry(entry)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    json_line = json.dumps(entry, separators=(",", ":"))
    if args.dry_run:
        print(json_line)
        return 0

    path = Path(args.output)
    append_entry(entry, path)
    print(f"Audit entry appended to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())