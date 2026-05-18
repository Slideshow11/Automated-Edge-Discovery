#!/usr/bin/env python3
"""
build_worker_packet.py

Produces a Claude Code Worker Handoff Packet v1 from a single task JSON,
controller state, and optional bundle index.

The packet tells Claude Code:
  1. exactly what task to implement
  2. which files are allowed
  3. which files are forbidden
  4. what tests to run
  5. what context files to read
  6. what it must not do
  7. what evidence it must return

The packet is NOT an authority grant. It does not let Claude Code push,
create PRs, merge, append audit logs, dispatch, create boards, update
memory/profile, or create skills.

Usage:
  python3 scripts/local/build_worker_packet.py \\
    --task-json /tmp/task.json \\
    --controller-state /tmp/CONTROLLER_STATE.json \\
    --bundle-index /tmp/BUNDLE_INDEX.json \\
    --workspace /tmp/aed_run \\
    --worker claude_code \\
    --output-json /tmp/worker_packet.json \\
    --output-md /tmp/worker_packet.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

PACKET_KIND = "aed.worker.packet.v1"
SCHEMA_VERSION = 1

# Forbidden paths that the worker packet never exposes
HERMES_PREFIX = "/home/max/.hermes"
FORBIDDEN_PREFIXES = (HERMES_PREFIX, "/tmp/hermes", ".hermes")

# Only supported worker for v1
SUPPORTED_WORKERS = frozenset(["claude_code"])

# Safety actions that are always forbidden
FORBIDDEN_ACTIONS = [
    "do not push",
    "do not create PR",
    "do not merge",
    "do not append audit log",
    "do not dispatch",
    "do not touch production board",
    "do not update memory or profile",
    "do not create skills",
]

# Required return fields
REQUIRED_RETURN_FIELDS = [
    "changed_files",
    "test_results",
    "blockers",
    "risk_notes",
    "scope_notes",
    # existing_code_reuse required return fields
    "existing_code_searches",
    "reuse_candidates",
    "reuse_decision",
    "service_layer_extraction_notes",
]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _is_forbidden_path(path: str, *, base: str | None = None) -> bool:
    """
    Check whether a path is forbidden.

    Resolves `path` against `base` (or cwd if base is None), then checks
    whether the resolved absolute path starts with any FORBIDDEN_PREFIX.
    Also checks raw path components for .hermes.

    Args:
        path: The path to check (may be absolute or relative).
        base: Optional base directory to resolve relative paths against.
              If None, resolves against the current working directory.
    """
    if base:
        abs_path = str((Path(base) / path).resolve())
    else:
        abs_path = str(Path(path).resolve())
    # Check resolved absolute path
    for prefix in FORBIDDEN_PREFIXES:
        if abs_path.startswith(prefix) or abs_path == prefix:
            return True
    # Also check raw/original path components (handles relative .hermes, etc.)
    raw = Path(path)
    parts = raw.parts
    if ".hermes" in parts:
        return True
    return False


def _load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    try:
        with open(p) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in {path}: {e}", file=sys.stderr)
        sys.exit(1)


def _is_safe_opensrc_path(opensrc_home: str, workspace: str) -> bool:
    """
    Validate that OPENSRC_HOME is safe for a run-scoped workspace.

    Rules:
    - Must be under the run workspace (or /tmp under /tmp/aed_runs/<run_id>/)
    - Must not escape to .hermes, repo source tree, or system paths
    - Must not be a symlink escape from workspace

    Args:
        opensrc_home: The proposed OPENSRC_HOME path.
        workspace: Absolute path to the run workspace.

    Returns:
        True if safe, False otherwise.
    """
    abs_ws = Path(workspace).resolve()
    abs_home = Path(opensrc_home).resolve()

    # Reject .hermes in path
    if ".hermes" in abs_home.parts:
        return False

    # Rule: under workspace (normalized)
    try:
        abs_home.relative_to(abs_ws)
        return True
    except ValueError:
        pass

    # Rule: absolute /tmp path under /tmp/aed_runs/<run_id>/
    if str(abs_home).startswith("/tmp/aed_runs/"):
        return True

    return False


def _build_dependency_context(task: dict, workspace: str) -> dict:
    """
    Build dependency_context dict from task JSON.

    Defaults are conservative (disabled) when not provided:
    - enabled: False
    - packages_to_inspect: []
    - read_only: True
    - record_inspected_files: True

    Validates opensrc_home and rejects unsafe paths.

    Does NOT invoke opensrc or install packages.
    """
    raw = task.get("dependency_context", {})
    enabled = bool(raw.get("enabled", False))
    packages = list(raw.get("packages_to_inspect", []))

    # Build default OPENSRC_HOME: <workspace>/opensrc_cache
    default_home = str(Path(workspace) / "opensrc_cache")
    opensrc_home = raw.get("opensrc_home") or default_home

    # Validate safety
    if not _is_safe_opensrc_path(opensrc_home, workspace):
        print(
            f"ERROR: opensrc_home is not safe: {opensrc_home}", file=sys.stderr
        )
        print(
            "ERROR: opensrc_home must be under workspace or /tmp/aed_runs/<run_id>/",
            file=sys.stderr
        )
        print(
            "ERROR: opensrc_home must not contain .hermes or escape the run workspace",
            file=sys.stderr
        )
        sys.exit(1)

    return {
        "enabled": enabled,
        "tool": "opensrc",
        "opensrc_home": opensrc_home,
        "packages_to_inspect": packages,
        "read_only": True,  # always read-only — opensrc is never a write tool
        "record_inspected_files": bool(raw.get("record_inspected_files", True)),
        "rules": [
            "read only dependency inspection only",
            "do not vendor dependency source into repo",
            "do not patch cached dependency source",
            "do not treat dependency cache as allowed source scope",
            "record package name, version, source, and inspected files",
        ],
    }


def _build_dependency_install_policy(task: dict) -> dict:
    """
    Build dependency_install_policy dict from task JSON.

    Defaults are conservative when not provided:
    - new_dependencies_allowed: False
    - requires_human_approval: True
    - minimum_package_age_days: 14
    - lockfile_review_required: True
    - postinstall_scripts_require_approval: True
    """
    raw = task.get("dependency_install_policy", {})

    return {
        "new_dependencies_allowed": bool(raw.get("new_dependencies_allowed", False)),
        "requires_human_approval": bool(raw.get("requires_human_approval", True)),
        "minimum_package_age_days": int(raw.get("minimum_package_age_days", 14)),
        "lockfile_review_required": bool(raw.get("lockfile_review_required", True)),
        "postinstall_scripts_require_approval": bool(
            raw.get("postinstall_scripts_require_approval", True)
        ),
    }


def _build_existing_code_reuse(task: dict) -> dict:
    """
    Build existing_code_reuse dict from task JSON.

    Defaults are always conservative for harness-controlled fields:
    - enabled: True
    - enforced: False (always — cannot be enabled by task JSON)
    - search_required: True (always — cannot be disabled by task JSON)
    - reuse_candidates_required: True (always — cannot be disabled by task JSON)
    - service_layer_extraction_required_when_duplicate_runtime_logic_found: True

    Task JSON may override:
    - enabled: True/False
    - instructions: extend the default instructions list

    Task JSON may NOT override:
    - enforced (always False)
    - search_required (always True)
    - reuse_candidates_required (always True)

    This does NOT grant any additional authority to the worker.
    """
    raw = task.get("existing_code_reuse", {})
    enabled = bool(raw.get("enabled", True))

    default_instructions = [
        "search for existing helpers, services, validators, and utilities before adding new logic",
        "list candidate reusable modules or explain why none apply",
        "prefer reusing existing service-layer logic over creating parallel implementations",
        "if duplication is found, propose extraction or consolidation before adding new code",
        "record the reuse decision in the worker return",
    ]
    task_instructions = raw.get("instructions", [])
    # Task instructions are appended to defaults (task can add guidance, not remove requirements)
    instructions = default_instructions + task_instructions

    return {
        "enabled": enabled,
        "enforced": False,  # always advisory — cannot be set True by task JSON
        "search_required": True,  # always required — cannot be disabled by task JSON
        "reuse_candidates_required": True,  # always required — cannot be disabled by task JSON
        "service_layer_extraction_required_when_duplicate_runtime_logic_found": True,
        "instructions": instructions,
        "required_return_fields": [
            "existing_code_searches",
            "reuse_candidates",
            "reuse_decision",
            "service_layer_extraction_notes",
        ],
    }


# ---------------------------------------------------------------------------
# Packet builder
# ---------------------------------------------------------------------------

def build_packet(
    task: dict,
    controller_state: dict | None,
    bundle_index: dict | None,
    workspace: str,
    worker: str,
) -> dict:
    """
    Build a worker handoff packet dict.

    Parameters
    ----------
    task : dict
        A single task object. Must contain task_id, objective, task_type.
        May contain allowed_files, forbidden_files, tests_to_run,
        context_files, expected_outputs.
    controller_state : dict | None
        CONTROLLER_STATE.json contents (optional).
    bundle_index : dict | None
        BUNDLE_INDEX.json contents (optional).
    workspace : str
        Absolute path to the run workspace.
    worker : str
        Worker name (only "claude_code" supported in v1).

    Returns
    -------
    dict
        Worker packet conforming to PACKET_KIND = "aed.worker.packet.v1".
    """
    if worker not in SUPPORTED_WORKERS:
        print(
            f"ERROR: unsupported worker '{worker}'. v1 supports only: {sorted(SUPPORTED_WORKERS)}",
            file=sys.stderr,
        )
        sys.exit(1)

    task_id = task.get("task_id") or task.get("id")
    if not task_id:
        print("ERROR: task is missing required field 'task_id' (or 'id')", file=sys.stderr)
        sys.exit(1)

    objective = task.get("objective") or task.get("title") or task.get("goal", "")
    if not objective:
        print("ERROR: task is missing required field 'objective' (or 'title' / 'goal')", file=sys.stderr)
        sys.exit(1)

    task_type = task.get("task_type", "unknown")

    allowed_files = task.get("allowed_files", [])
    forbidden_files = task.get("forbidden_files", [])
    expected_outputs = task.get("expected_outputs", [])
    tests_to_run = task.get("tests_to_run", [])
    context_files = task.get("context_files", [])

    # Validate task paths — reject any that expose Hermes / forbidden prefixes.
    # Check both against cwd (base=None) and against workspace (base=workspace).
    # This catches workspace-relative symlinks such as
    #   /tmp/aed_run/link_to_hermes -> /home/max/.hermes
    # where a relative path like "link_to_hermes/secret" resolves to the
    # forbidden target when anchored to the workspace.
    for f in allowed_files:
        if _is_forbidden_path(f) or _is_forbidden_path(f, base=workspace):
            print(f"ERROR: allowed_files contains forbidden path: {f}", file=sys.stderr)
            sys.exit(1)
    for f in context_files:
        if _is_forbidden_path(f) or _is_forbidden_path(f, base=workspace):
            print(f"ERROR: context_files contains forbidden path: {f}", file=sys.stderr)
            sys.exit(1)

    if not allowed_files:
        print("ERROR: task is missing required field 'allowed_files' (must be non-empty)", file=sys.stderr)
        sys.exit(1)

    # Derive risk_level from task_type
    risk_level = _derive_risk_level(task_type, allowed_files)

    # Derive worker recommendation reason
    recommended_worker_reason = _derive_worker_reason(task_type, allowed_files)

    # Build controller_context from controller_state
    # NOTE: safety_invariants are copied verbatim from controller_state.
    # If the controller run has any hard-safety flag set to true,
    # that information must propagate into the packet so the worker
    # (and the operator) know a safety boundary was crossed.
    # We do NOT reset them to false — that would hide a safety violation.
    si = {"hermes_touched": False, "dispatch_occurred": False,
          "production_board_touched": False, "memory_or_profile_updated": False,
          "skills_created": False}
    controller_context = {"run_id": None, "integration_branch": None,
                          "current_task_id": task_id, "next_action": "run_task"}
    if controller_state:
        si = dict(controller_state.get("safety_invariants", si))
        controller_context["run_id"] = controller_state.get("run_id")
        controller_context["integration_branch"] = controller_state.get("integration_branch")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    packet: dict = {
        "packet_kind": PACKET_KIND,
        "packet_version": SCHEMA_VERSION,
        "generated_at": now,
        "worker": worker,
        "task_id": str(task_id),
        "objective": objective,
        "task_type": task_type,
        "risk_level": risk_level,
        "allowed_files": allowed_files,
        "forbidden_files": forbidden_files,
        "expected_outputs": expected_outputs,
        "context_files": context_files,
        "tests_to_run": tests_to_run,
        "do_not": list(FORBIDDEN_ACTIONS),
        "required_return": list(REQUIRED_RETURN_FIELDS),
        "controller_context": controller_context,
        "safety_invariants": si,
        "existing_code_reuse": _build_existing_code_reuse(task),
        "recommended_worker_reason": recommended_worker_reason,
        "dependency_context": _build_dependency_context(task, workspace),
        "dependency_install_policy": _build_dependency_install_policy(task),
    }

    return packet


def _derive_risk_level(task_type: str, allowed_files: list[str]) -> str:
    """Derive risk_level from task_type and file scope."""
    if task_type == "docs":
        return "low"
    if len(allowed_files) > 1:
        return "medium"
    return "low"


def _derive_worker_reason(task_type: str, allowed_files: list[str]) -> str:
    """
    Recommend worker based on task characteristics.

    For v1, always recommends claude_code (the only supported worker),
    but adds a human-readable reason field.
    """
    if task_type == "docs":
        return "docs task, Claude Code optional"
    if len(allowed_files) > 1:
        return "multi-file implementation or debugging task, use Claude Code"
    return "single-file task, Claude Code optional"


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def render_markdown(packet: dict) -> str:
    """Render the worker packet as a Telegram-ready markdown document."""
    lines: list[str] = []

    lines.append("# Claude Code Worker Packet")
    lines.append("")
    lines.append(f"**Task:** `{packet.get('task_id', '')}`")
    lines.append(f"**Worker:** `{packet.get('worker', '')}`")
    lines.append(f"**Objective:** {packet.get('objective', '')}")
    lines.append(f"**Risk level:** `{packet.get('risk_level', 'low')}`")
    lines.append("")

    # Allowed files
    lines.append("## Allowed files")
    lines.append("")
    for f in packet.get("allowed_files", []):
        lines.append(f"- `{f}`")
    lines.append("")

    # Forbidden files
    lines.append("## Forbidden files")
    lines.append("")
    forbidden = packet.get("forbidden_files", [])
    if forbidden:
        for f in forbidden:
            lines.append(f"- `{f}`")
    else:
        lines.append("_none declared_")
    lines.append("")

    # Expected outputs
    outputs = packet.get("expected_outputs", [])
    if outputs:
        lines.append("## Expected outputs")
        lines.append("")
        for o in outputs:
            lines.append(f"- {o}")
        lines.append("")

    # Required tests
    lines.append("## Required tests")
    lines.append("")
    tests = packet.get("tests_to_run", [])
    if tests:
        for t in tests:
            lines.append(f"- `{t}`")
    else:
        lines.append("_none declared_")
    lines.append("")

    # Context files
    ctx = packet.get("context_files", [])
    if ctx:
        lines.append("## Context files to read")
        lines.append("")
        for c in ctx:
            lines.append(f"- `{c}`")
        lines.append("")

    # Existing Code Reuse Check
    ecr = packet.get("existing_code_reuse", {})
    lines.append("## Existing Code Reuse Check")
    lines.append("")
    if ecr.get("enabled"):
        lines.append("Before implementing:")
        lines.append("")
        for i, instruction in enumerate(ecr.get("instructions", []), 1):
            lines.append(f"{i}. {instruction}")
        lines.append("")
        lines.append("This does not grant extra authority. You may only edit `allowed_files`.")
        lines.append("Service extraction must stay within `allowed_files` or be returned as a blocker.")
        lines.append("")
    else:
        lines.append("_existing code reuse check is not enabled for this task_")
        lines.append("")
        lines.append("This does not grant extra authority.")
        lines.append("")

    # Hard constraints
    lines.append("## Hard constraints")
    lines.append("")
    for action in packet.get("do_not", []):
        lines.append(f"- {action}.")
    lines.append("")

    # Return format
    lines.append("## Return format")
    lines.append("")
    lines.append("Return the following fields when done:")
    for field in packet.get("required_return", []):
        lines.append(f"- `{field}`")
    lines.append("")

    # Controller context
    cc = packet.get("controller_context", {})
    if cc.get("run_id"):
        lines.append("## Controller context")
        lines.append("")
        lines.append(f"- **run_id:** `{cc.get('run_id', '')}`")
        lines.append(f"- **integration_branch:** `{cc.get('integration_branch', '')}`")
        lines.append(f"- **current_task_id:** `{cc.get('current_task_id', '')}`")
        lines.append(f"- **next_action:** `{cc.get('next_action', '')}`")
        lines.append("")

    # Dependency Context section
    dc = packet.get("dependency_context", {})
    if dc.get("enabled"):
        lines.append("## Dependency Context")
        lines.append("")
        lines.append(f"**Tool:** `{dc.get('tool', 'opensrc')}`")
        lines.append(f"**OPENSRC_HOME:** `{dc.get('opensrc_home', '')}`")
        lines.append(f"**Mode:** `read-only`")
        lines.append("")
        packages = dc.get("packages_to_inspect", [])
        if packages:
            lines.append("**Allowed package inspection:**")
            lines.append("")
            for pkg in packages:
                lines.append(f"- `{pkg}`")
            lines.append("")
        else:
            lines.append("**Allowed package inspection:** _none declared_")
            lines.append("")

        lines.append("**Rules:**")
        lines.append("")
        for rule in dc.get("rules", []):
            lines.append(f"- {rule}.")
        lines.append("")

        dip = packet.get("dependency_install_policy", {})
        lines.append("**Install policy:**")
        lines.append("")
        lines.append(f"- **new_dependencies_allowed:** `{dip.get('new_dependencies_allowed', False)}`")
        lines.append(f"- **requires_human_approval:** `{dip.get('requires_human_approval', True)}`")
        lines.append(f"- **minimum_package_age_days:** `{dip.get('minimum_package_age_days', 14)}`")
        lines.append(f"- **lockfile_review_required:** `{dip.get('lockfile_review_required', True)}`")
        lines.append(f"- **postinstall_scripts_require_approval:** `{dip.get('postinstall_scripts_require_approval', True)}`")
        lines.append("")
        if not dip.get("new_dependencies_allowed"):
            lines.append("**New dependency installation is not allowed for this task.**")
            lines.append("")
        else:
            if dip.get("requires_human_approval"):
                lines.append(
                    "New dependency installation requires human approval, "
                    "package age check, lockfile review, and postinstall-script review."
                )
            else:
                lines.append(
                    "New dependency installation is allowed by task policy "
                    "but still requires package age check, lockfile review, "
                    "and postinstall-script review."
                )
            lines.append("")
    else:
        lines.append("## Dependency Context")
        lines.append("")
        lines.append("**Tool:** `opensrc` (disabled)")
        lines.append("")
        lines.append("_dependency inspection is not enabled for this task_")
        lines.append("")
        lines.append("**New dependency installation is not allowed for this task.**")
        lines.append("")

    lines.append(f"**Packet generated:** `{packet.get('generated_at', '')}`")

    return "\n".join(lines)


def serialize_packet(packet: dict) -> str:
    """Serialize packet to stable JSON."""
    return json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Build a Claude Code Worker Handoff Packet v1. "
                    "The packet does NOT grant authority — it only describes scope and constraints.",
    )
    p.add_argument(
        "--task-json", type=str, required=True,
        help="Path to a single task JSON file (task object, not array)",
    )
    p.add_argument(
        "--controller-state", type=str, default=None,
        help="Path to CONTROLLER_STATE.json (optional)",
    )
    p.add_argument(
        "--bundle-index", type=str, default=None,
        help="Path to BUNDLE_INDEX.json (optional)",
    )
    p.add_argument(
        "--workspace", type=str, required=True,
        help="Absolute path to the run workspace",
    )
    p.add_argument(
        "--worker", type=str, required=True,
        help="Worker name (only 'claude_code' supported in v1)",
    )
    p.add_argument(
        "--output-json", type=str, required=True,
        help="Path to write the packet JSON",
    )
    p.add_argument(
        "--output-md", type=str, required=True,
        help="Path to write the packet markdown",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Guard output paths against Hermes prefix
    for path_attr in ("output_json", "output_md"):
        path = getattr(args, path_attr)
        if path and _is_forbidden_path(path):
            print(f"ERROR: {path_attr} may not be inside {HERMES_PREFIX}", file=sys.stderr)
            return 1

    # Load inputs
    task = _load_json(args.task_json)

    controller_state = None
    if args.controller_state:
        controller_state = _load_json(args.controller_state)

    bundle_index = None
    if args.bundle_index:
        bundle_index = _load_json(args.bundle_index)

    # Build packet
    packet = build_packet(
        task=task,
        controller_state=controller_state,
        bundle_index=bundle_index,
        workspace=args.workspace,
        worker=args.worker,
    )

    # Write JSON
    with open(args.output_json, "w") as f:
        json.dump(packet, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # Write Markdown
    md = render_markdown(packet)
    with open(args.output_md, "w") as f:
        f.write(md)
        if not md.endswith("\n"):
            f.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())