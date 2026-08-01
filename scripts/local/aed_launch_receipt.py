#!/usr/bin/env python3
"""
aed_launch_receipt.py

Emits two artifacts at controller init:
  1) LAUNCH_RECEIPT.json — machine-readable
  2) LAUNCH_RECEIPT.md   — concise human-readable

The launch receipt is the precondition that authorizes any repository
or GitHub mutation. Both files are written with restrictive permissions
and contain no secrets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from scripts.local.aed_run_identity import (
    _utcnow,
    assert_no_secrets,
    safe_restrictive_open,
    write_restrictive_json,
)


RECEIPT_JSON_FILENAME = "LAUNCH_RECEIPT.json"
RECEIPT_MD_FILENAME = "LAUNCH_RECEIPT.md"


def build_machine_readable(
    *,
    run_identity: dict,
    state_path: str,
    lock_path: Optional[str],
    pending_action: str,
    current_phase: str,
    merge_policy: str,
    workspace: str,
    extra: Optional[dict] = None,
) -> dict:
    payload = {
        "receipt_version": 1,
        "kind": "launch_receipt",
        "issued_at": _utcnow(),
        "workspace": workspace,
        "state_path": state_path,
        "lock_path": lock_path,
        "current_phase": current_phase,
        "pending_action": pending_action,
        "merge_policy": merge_policy,
        "run_identity": run_identity,
    }
    if extra:
        payload["extra"] = extra
    return payload


def write_machine_readable(path: Path, payload: dict) -> None:
    assert_no_secrets(payload, context=str(path))
    write_restrictive_json(path, payload)


def build_human_readable(
    *,
    run_identity: dict,
    state_path: str,
    lock_path: Optional[str],
    pending_action: str,
    current_phase: str,
    merge_policy: str,
    workspace: str,
) -> str:
    rid = run_identity
    host = rid.get("host", {})
    proc = rid.get("process", {}) or {}
    lines = []
    lines.append("# AED Run Controller: Launch Receipt")
    lines.append("")
    lines.append(f"**Issued at:** `{rid.get('created_at', '?')}`")
    lines.append(f"**Workspace:** `{workspace}`")
    lines.append("")
    lines.append("## Run identity")
    lines.append("")
    lines.append(f"- **Run ID:** `{rid.get('run_id', '?')}`")
    lines.append(f"- **Controller version:** `{rid.get('controller_version', '?')}`")
    lines.append(f"- **Repository:** `{rid.get('repository') or '—'}`")
    prn = rid.get("target_pr_number")
    lines.append(f"- **Target PR number:** `{prn if prn is not None else '—'}`")
    lines.append(f"- **Current main SHA:** `{rid.get('current_main_sha') or '—'}`")
    lines.append(f"- **Starting target SHA:** `{rid.get('starting_target_sha') or '—'}`")
    lines.append("")
    lines.append("## Host identity")
    lines.append("")
    lines.append(f"- **Hostname:** `{host.get('hostname', '?')}`")
    if host.get("fqdn"):
        lines.append(f"- **FQDN:** `{host.get('fqdn')}`")
    lines.append(f"- **Platform:** `{host.get('platform', '?')}`")
    lines.append(f"- **Python:** `{host.get('python_version', '?')}`")
    lines.append("")
    lines.append("## Process identity")
    lines.append("")
    lines.append(f"- **PID:** `{proc.get('pid', '?')}`")
    lines.append(f"- **/proc source:** `{proc.get('source', '?')}`")
    lines.append(f"- **start_time field:** `{proc.get('stat_start_time_text') or '—'}`")
    lines.append(f"- **ctime (ns):** `{proc.get('ctime_ns') or '—'}`")
    lines.append("")
    lines.append("## Persisted artifacts")
    lines.append("")
    lines.append(f"- **State file:** `{state_path}`")
    if lock_path:
        lines.append(f"- **Lock file:** `{lock_path}`")
    lines.append("")
    lines.append("## Current phase & pending action")
    lines.append("")
    lines.append(f"- **Phase:** `{current_phase}`")
    lines.append(f"- **Pending action:** `{pending_action}`")
    lines.append("")
    lines.append("## Merge policy")
    lines.append("")
    lines.append(f"- **{merge_policy}**")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "This receipt authorizes repository and GitHub mutations. "
        "Any mutation attempted before this receipt was emitted is "
        "out of scope and must be rejected by the controller."
    )
    return "\n".join(lines) + "\n"


def write_human_readable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Markdown content has no secrets by construction, but assert
    # anyway as a defense-in-depth check.
    assert_no_secrets(content, context=str(path))
    with safe_restrictive_open(path, "w") as f:
        f.write(content)


def emit(
    workspace: Path,
    *,
    run_identity: dict,
    state_path: str,
    lock_path: Optional[str],
    pending_action: str,
    current_phase: str,
    merge_policy: str,
    extra: Optional[dict] = None,
) -> tuple[Path, Path]:
    """
    Emit both machine-readable and human-readable launch receipts
    under <workspace>/. Returns the paths.
    """
    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    json_path = workspace / RECEIPT_JSON_FILENAME
    md_path = workspace / RECEIPT_MD_FILENAME

    # Round-12 P2 fix: ensure the workspace path persisted in the
    # receipt is absolute. A later authorize-mutation invocation
    # from a different working directory resolves the receipt's
    # workspace against the new CWD; persisting a relative path
    # would mismatch the absolute workspace stored in state and
    # cause the receipt's workspace comparison to reject an
    # otherwise valid run.
    workspace_str = str(workspace)
    payload = build_machine_readable(
        run_identity=run_identity,
        state_path=state_path,
        lock_path=lock_path,
        pending_action=pending_action,
        current_phase=current_phase,
        merge_policy=merge_policy,
        workspace=workspace_str,
        extra=extra,
    )
    write_machine_readable(json_path, payload)
    md = build_human_readable(
        run_identity=run_identity,
        state_path=state_path,
        lock_path=lock_path,
        pending_action=pending_action,
        current_phase=current_phase,
        merge_policy=merge_policy,
        workspace=workspace_str,
    )
    write_human_readable(md_path, md)
    return json_path, md_path


__all__ = [
    "RECEIPT_JSON_FILENAME",
    "RECEIPT_MD_FILENAME",
    "build_machine_readable",
    "write_machine_readable",
    "build_human_readable",
    "write_human_readable",
    "emit",
]