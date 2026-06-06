#!/usr/bin/env python3
"""
phase_ledger.py — append-only phase execution ledger writer.

Purpose:
  Record per-phase execution evidence (script + argv + exit_code + artifact
  paths + observed summary) as one JSON object per line in a JSONL file.
  This is the substrate for the no-unproven-PASS guard:

    A "PHASE_STATUS: PASS" claim that is not backed by a canonical
    (writer=script or writer=phase_exec) ledger line is unevidenced and
    must be held at HOLD_UNEVIDENCED_PASS.

V1 scope (deliberately minimal):
  - One JSON object per physical line (newline separator safety).
  - Required fields: run_id, phase_id, writer, exit_code, status, timestamp.
  - For canonical writers (script, phase_exec): argv and absolute
    stdout_path/stderr_path are required; non-empty observed_summary for PASS.
  - For narrative writer (agent): argv/paths optional; useful for SKIP
    entries but does NOT satisfy proof for claimed PASS.
  - Optional task-list linkage: source_task_id, task_packet_id, roadmap_item_id.

Hardened-append pattern mirrors scripts/local/append_merge_action_audit.py:
  - Parent dir auto-created.
  - If file is non-empty and does not end with newline, separator inserted.
  - File always ends with newline after append.
  - No de-duplication of (run_id, phase_id); the validator is responsible
    for emitting a warning if duplicates appear.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

AUDIT_LOG_VERSION = 1
LEDGER_KIND = "phase_execution_v1"

VALID_STATUSES = ("PASS", "FAIL", "HOLD", "SKIP")

# Writers that satisfy proof for claimed PASS phases.
CANONICAL_WRITERS = ("script", "phase_exec")

# Writers that produce narrative only and NEVER satisfy proof for PASS.
NARRATIVE_WRITERS = ("agent",)

ALL_WRITERS = CANONICAL_WRITERS + NARRATIVE_WRITERS

REQUIRED_FIELDS = (
    "audit_log_version",
    "ledger_kind",
    "run_id",
    "phase_id",
    "writer",
    "exit_code",
    "status",
    "timestamp",
)


# -----------------------------------------------------------------------------
# Validation helpers
# -----------------------------------------------------------------------------


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def _is_absolute_path_string(value: Any) -> bool:
    return isinstance(value, str) and os.path.isabs(value)


def validate_entry_shape(entry: dict[str, Any]) -> list[str]:
    """
    Return a list of error messages for a single ledger entry dict.
    Empty list means the entry is structurally valid.
    """
    errors: list[str] = []
    if not isinstance(entry, dict):
        return [f"entry must be a dict, got {type(entry).__name__}"]

    for field in REQUIRED_FIELDS:
        if field not in entry or entry[field] in (None, ""):
            errors.append(f"required field missing or empty: {field}")

    if entry.get("audit_log_version") != AUDIT_LOG_VERSION:
        errors.append(
            f"audit_log_version must be {AUDIT_LOG_VERSION}, "
            f"got {entry.get('audit_log_version')!r}"
        )
    if entry.get("ledger_kind") != LEDGER_KIND:
        errors.append(
            f"ledger_kind must be {LEDGER_KIND!r}, "
            f"got {entry.get('ledger_kind')!r}"
        )

    writer = entry.get("writer")
    if writer not in ALL_WRITERS:
        errors.append(
            f"writer must be one of {ALL_WRITERS}, got {writer!r}"
        )

    if entry.get("status") not in VALID_STATUSES:
        errors.append(
            f"status must be one of {VALID_STATUSES}, "
            f"got {entry.get('status')!r}"
        )

    # Canonical-writer strictness
    if writer in CANONICAL_WRITERS:
        argv = entry.get("argv")
        if not isinstance(argv, list) or len(argv) == 0:
            errors.append(
                f"writer={writer!r} requires non-empty argv list"
            )
        if not _is_absolute_path_string(entry.get("stdout_path")):
            errors.append(
                f"writer={writer!r} requires absolute stdout_path"
            )
        if not _is_absolute_path_string(entry.get("stderr_path")):
            errors.append(
                f"writer={writer!r} requires absolute stderr_path"
            )

    return errors


# -----------------------------------------------------------------------------
# Entry builder
# -----------------------------------------------------------------------------


def build_entry(
    *,
    run_id: str,
    phase_id: str,
    writer: str,
    argv: Optional[list[str]] = None,
    exit_code: int = 0,
    status: str,
    observed_summary: str = "",
    timestamp: Optional[str] = None,
    phase_index: Optional[int] = None,
    script: Optional[str] = None,
    stdout_path: Optional[str] = None,
    stderr_path: Optional[str] = None,
    stdout_size_bytes: Optional[int] = None,
    stderr_size_bytes: Optional[int] = None,
    source_task_id: Optional[str] = None,
    task_packet_id: Optional[str] = None,
    roadmap_item_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Build a single phase ledger entry as a dict.

    All keyword arguments are required except where noted. Validation is
    applied at build time; raises ValueError on structural problems.
    """
    if not _is_nonempty_str(run_id):
        raise ValueError("run_id is required and must be a non-empty string")
    if not _is_nonempty_str(phase_id):
        raise ValueError("phase_id is required and must be a non-empty string")
    if writer not in ALL_WRITERS:
        raise ValueError(f"writer must be one of {ALL_WRITERS}, got {writer!r}")
    if status not in VALID_STATUSES:
        raise ValueError(
            f"status must be one of {VALID_STATUSES}, got {status!r}"
        )

    if writer in CANONICAL_WRITERS:
        if not isinstance(argv, list) or len(argv) == 0:
            raise ValueError(
                f"writer={writer!r} requires a non-empty argv list"
            )
        if not _is_absolute_path_string(stdout_path):
            raise ValueError(
                f"writer={writer!r} requires an absolute stdout_path"
            )
        if not _is_absolute_path_string(stderr_path):
            raise ValueError(
                f"writer={writer!r} requires an absolute stderr_path"
            )

    if timestamp is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    entry: dict[str, Any] = {
        "audit_log_version": AUDIT_LOG_VERSION,
        "ledger_kind": LEDGER_KIND,
        "run_id": run_id,
        "phase_id": phase_id,
        "phase_index": phase_index,
        "writer": writer,
        "source_task_id": source_task_id,
        "task_packet_id": task_packet_id,
        "roadmap_item_id": roadmap_item_id,
        "script": script,
        "argv": argv,
        "exit_code": exit_code,
        "stdout_path": stdout_path,
        "stderr_path": stderr_path,
        "stdout_size_bytes": stdout_size_bytes,
        "stderr_size_bytes": stderr_size_bytes,
        "observed_summary": observed_summary,
        "status": status,
        "timestamp": timestamp,
    }
    return entry


# -----------------------------------------------------------------------------
# Canonical-evidence predicate
# -----------------------------------------------------------------------------


def is_canonical_evidence(entry: dict[str, Any]) -> bool:
    """
    Return True iff the entry is canonical evidence that can satisfy a
    claimed PHASE_STATUS: PASS line.

    A canonical entry must:
      - have writer in CANONICAL_WRITERS
      - have status == PASS
      - have non-empty argv
      - have absolute stdout_path AND absolute stderr_path
      - have non-empty observed_summary
    """
    if not isinstance(entry, dict):
        return False
    if entry.get("writer") not in CANONICAL_WRITERS:
        return False
    if entry.get("status") != "PASS":
        return False
    argv = entry.get("argv")
    if not isinstance(argv, list) or len(argv) == 0:
        return False
    if not _is_absolute_path_string(entry.get("stdout_path")):
        return False
    if not _is_absolute_path_string(entry.get("stderr_path")):
        return False
    if not _is_nonempty_str(entry.get("observed_summary")):
        return False
    return True


# -----------------------------------------------------------------------------
# File operations
# -----------------------------------------------------------------------------


def append_entry(entry: dict[str, Any], path: Path) -> None:
    """
    Append one JSON-serialized entry to the JSONL file at path.

    Guarantees:
      - One JSON object per physical line.
      - If the file exists, is non-empty, and does not end with a newline,
        one newline is inserted before the new entry.
      - The file always ends with a newline after append.
      - Parent directories are created if they do not exist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    json_line = json.dumps(entry, separators=(",", ":"))

    if path.exists() and path.stat().st_size > 0:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            last_byte = f.read(1)
        if last_byte != b"\n":
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json_line + "\n")
            return

    with open(path, "a", encoding="utf-8") as f:
        f.write(json_line + "\n")


# -----------------------------------------------------------------------------
# Read / query
# -----------------------------------------------------------------------------


def read_entries(path: Path) -> list[dict[str, Any]]:
    """
    Read a JSONL ledger and return a list of valid entries.

    Malformed non-empty lines are silently skipped; for full visibility into
    parse errors (line number + reason), use read_entries_with_errors
    instead. This function is preserved for callers that only need the
    parseable entries; the validator uses the strict variant.
    """
    entries, _parse_errors = read_entries_with_errors(path)
    return entries


def read_entries_with_errors(
    path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Read a JSONL ledger and return (entries, parse_errors).

    entries:       list of valid entry dicts (one per well-formed non-empty
                   JSON object line).
    parse_errors:  list of {"line": int, "raw": str, "error": str} for
                   non-empty lines that failed to parse as a JSON object
                   (or parsed to a non-dict top-level value).

    Blank / whitespace-only lines are silently ignored (no entry, no error).
    Malformed non-empty lines surface in parse_errors and are NOT included
    in entries — the validator must surface them as HOLD_PHASE_EVIDENCE_CORRUPTED
    so that a valid claimed PASS plus a corrupted/tampered extra line does
    NOT validate as HOLD_VALID.
    """
    if not path.exists():
        return [], []
    entries: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    text = path.read_text()
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue  # blank lines are not an error
        try:
            obj, end = decoder.raw_decode(stripped)
        except json.JSONDecodeError as e:
            parse_errors.append({
                "line": line_no,
                "raw": stripped,
                "error": f"json.JSONDecodeError: {e.msg} (line {e.lineno}, col {e.colno})",
            })
            continue
        # Reject trailing data after the decoded JSON object. raw_decode
        # returns successfully if the leading substring is a complete
        # JSON object, even when there is non-whitespace garbage after
        # it. For the corruption/tamper guard this ledger is meant to
        # enforce, any non-whitespace after the decoded object must be
        # surfaced as a parse error (HOLD_PHASE_EVIDENCE_CORRUPTED),
        # not silently accepted.
        trailing = stripped[end:].strip()
        if trailing:
            parse_errors.append({
                "line": line_no,
                "raw": stripped,
                "error": (
                    f"trailing data after JSON object at position {end} of "
                    f"{len(stripped)}: {trailing!r}"
                ),
            })
            continue
        if not isinstance(obj, dict):
            parse_errors.append({
                "line": line_no,
                "raw": stripped,
                "error": (
                    f"top-level JSON value is not a dict, "
                    f"got {type(obj).__name__}"
                ),
            })
            continue
        entries.append(obj)
    return entries, parse_errors


def find_entry(
    entries: list[dict[str, Any]],
    run_id: str,
    phase_id: str,
) -> Optional[dict[str, Any]]:
    """Return the first entry matching (run_id, phase_id), or None."""
    for e in entries:
        if e.get("run_id") == run_id and e.get("phase_id") == phase_id:
            return e
    return None
