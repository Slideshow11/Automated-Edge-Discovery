#!/usr/bin/env python3
"""
build_autocoder_real_output_result_packet.py — Report-only result packet
builder for the autocoder real-output evaluator (v0).

This tool is the bridge between manually written seed result packets and
future automatic packet emission. It takes structured CLI inputs, validates
them, and writes a JSON result packet that is consumable by
scripts/local/run_autocoder_real_output_eval.py.

Hard rules (this is a REPORT-ONLY local tool):

  - Does not call models.
  - Does not mutate GitHub.
  - Does not require gh.
  - Does not run any external process.
  - Does not import the stdlib `subprocess` module.
  - Never uses a shell-True style of process invocation.
  - Validates required fields and types before writing.
  - Writes a single JSON file with a result_packet_generated_at timestamp.

Statuses:

  RESULT_PACKET_READY     — packet was built and written successfully.
  ERROR_INVALID_ARGS      — CLI args failed validation; nothing was written.
  ERROR_TOOL_FAILURE      — an unexpected internal error occurred.

Output packet fields (always present unless noted):

  task_id                     (string, required)
  source_pr                   (int > 0, required)
  source_commit               (string, required)
  source_head_sha             (string, required)
  title                       (string, required)
  status                      (string, one of PASS|HOLD|ERROR|UNKNOWN)
  changed_files               (list[str], non-empty, required)
  allowed_files               (list[str], non-empty, required)
  scoped_files                (list[str], optional, descriptive only)
  tests_passed                (int >= 0, required)
  ci_green                    (bool, required)
  scope_clean                 (bool, required)
  review_ready                (bool, required)
  merge_ready                 (bool, required)
  human_cleanup_required      (bool, required)
  hold_reason                 (string, optional, emitted only if set)
  error_reason                (string, optional, emitted only if set)
  notes                       (list[str], optional, repeatable)
  result_packet_generated_at  (string, ISO 8601 UTC timestamp)
  builder_status              (string, this tool's own status)

The packet is intentionally tolerant: fields marked "optional" are omitted
from the JSON when not provided. The packet is a strict superset of what
run_autocoder_real_output_eval.py's load_result() needs; the eval simply
ignores unknown fields.

Exit codes:

  0 — RESULT_PACKET_READY (packet written)
  2 — ERROR_INVALID_ARGS
  1 — ERROR_TOOL_FAILURE
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATUS_READY = "RESULT_PACKET_READY"
STATUS_ERROR_INVALID_ARGS = "ERROR_INVALID_ARGS"
STATUS_ERROR_TOOL_FAILURE = "ERROR_TOOL_FAILURE"

# Per spec: only these 4 status tokens are accepted by the builder.
# FAIL is intentionally NOT accepted here; the eval accepts FAIL but the
# builder does not emit it. (Result packets written by other tools may carry
# FAIL; the eval already handles it via the unknown branch.)
ALLOWED_RESULT_STATUSES = frozenset({"PASS", "HOLD", "ERROR", "UNKNOWN"})

# Allowed boolean spellings on the CLI (lowercase only, per spec).
TRUE_LITERALS = frozenset({"true"})
FALSE_LITERALS = frozenset({"false"})

PACKET_KIND_BUILDER = "aed.autocoder.real_output_result_packet_builder.v0"
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser. Kept in its own function so tests can
    introspect the schema without invoking main()."""
    p = argparse.ArgumentParser(
        prog="build_autocoder_real_output_result_packet.py",
        description=(
            "Report-only result-packet builder for the autocoder real-output "
            "evaluator. Writes a JSON packet compatible with "
            "run_autocoder_real_output_eval.py."
        ),
    )
    p.add_argument("--task-id", required=True, type=str,
                   help="Corpus task_id this result packet is for (non-empty).")
    p.add_argument("--source-pr", required=True, type=int,
                   help="PR number that produced this result (positive int).")
    p.add_argument("--source-commit", required=True, type=str,
                   help="Source commit SHA of the PR (string, 40-char hex recommended).")
    p.add_argument("--source-head-sha", required=True, type=str,
                   help="Source HEAD SHA of the PR (string, 40-char hex recommended).")
    p.add_argument("--title", required=True, type=str,
                   help="Human-readable title for the result packet.")
    p.add_argument("--status", required=True, type=str,
                   choices=sorted(ALLOWED_RESULT_STATUSES),
                   help="Result status: one of PASS|HOLD|ERROR|UNKNOWN.")
    p.add_argument("--changed-file", dest="changed_files", action="append",
                   default=[], help="Path of a changed file. Repeatable. Must be non-empty overall.")
    p.add_argument("--allowed-file", dest="allowed_files", action="append",
                   default=[], help="Allowed-file glob. Repeatable. Must be non-empty overall.")
    p.add_argument("--scoped-file", dest="scoped_files", action="append",
                   default=[], help="Optional scoped file (descriptive). Repeatable.")
    p.add_argument("--tests-passed", required=True, type=int,
                   help="Number of tests that passed (int >= 0).")
    for name in ("ci-green", "scope-clean", "review-ready", "merge-ready",
                 "human-cleanup-required"):
        p.add_argument(
            f"--{name}", required=True, type=str, choices=sorted(TRUE_LITERALS | FALSE_LITERALS),
            help="Boolean flag: must be 'true' or 'false' (lowercase).",
        )
    p.add_argument("--hold-reason", type=str, default=None,
                   help="Optional human-readable reason for HOLD status.")
    p.add_argument("--error-reason", type=str, default=None,
                   help="Optional human-readable reason for ERROR status.")
    p.add_argument("--note", dest="notes", action="append", default=[],
                   help="Optional free-form note. Repeatable.")
    p.add_argument("--output-json", required=True, type=str,
                   help="Path where the JSON result packet will be written.")
    return p


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _parse_bool_strict(name: str, raw: str) -> Tuple[Optional[bool], Optional[str]]:
    """Strict lowercase-only bool parse. Returns (value, error_message)."""
    if raw in TRUE_LITERALS:
        return True, None
    if raw in FALSE_LITERALS:
        return False, None
    return None, f"--{name} must be 'true' or 'false' (lowercase); got {raw!r}"


def validate_args(args: argparse.Namespace) -> Tuple[bool, List[str]]:
    """Validate parsed CLI args. Returns (ok, list_of_error_messages)."""
    errors: List[str] = []

    # task_id: non-empty string
    if not isinstance(args.task_id, str) or not args.task_id.strip():
        errors.append("--task-id must be a non-empty string")

    # source_pr: positive int
    if not isinstance(args.source_pr, int) or isinstance(args.source_pr, bool):
        errors.append("--source-pr must be an int")
    elif args.source_pr <= 0:
        errors.append(f"--source-pr must be a positive int; got {args.source_pr}")

    # source_commit / source_head_sha: non-empty strings
    for f in ("source_commit", "source_head_sha"):
        v = getattr(args, f, None)
        if not isinstance(v, str) or not v.strip():
            errors.append(f"--{f.replace('_', '-')} must be a non-empty string")

    # title: non-empty string
    if not isinstance(args.title, str) or not args.title.strip():
        errors.append("--title must be a non-empty string")

    # status: already restricted by argparse `choices`, but re-check defensively
    if args.status not in ALLOWED_RESULT_STATUSES:
        errors.append(
            f"--status must be one of {sorted(ALLOWED_RESULT_STATUSES)}; got {args.status!r}"
        )

    # changed_files: non-empty list of non-empty strings
    if not isinstance(args.changed_files, list) or len(args.changed_files) == 0:
        errors.append("--changed-file must be provided at least once (non-empty list)")
    else:
        for i, f in enumerate(args.changed_files):
            if not isinstance(f, str) or not f.strip():
                errors.append(f"--changed-file[{i}] must be a non-empty string")
                break

    # allowed_files: non-empty list of non-empty strings
    if not isinstance(args.allowed_files, list) or len(args.allowed_files) == 0:
        errors.append("--allowed-file must be provided at least once (non-empty list)")
    else:
        for i, f in enumerate(args.allowed_files):
            if not isinstance(f, str) or not f.strip():
                errors.append(f"--allowed-file[{i}] must be a non-empty string")
                break

    # scoped_files: optional list (may be empty). If provided, must be strings.
    if not isinstance(args.scoped_files, list):
        errors.append("--scoped-file values must be a list")
    else:
        for i, f in enumerate(args.scoped_files):
            if not isinstance(f, str) or not f.strip():
                errors.append(f"--scoped-file[{i}] must be a non-empty string")
                break

    # tests_passed: int, not bool, >= 0
    if not isinstance(args.tests_passed, int) or isinstance(args.tests_passed, bool):
        errors.append("--tests-passed must be an int")
    elif args.tests_passed < 0:
        errors.append(f"--tests-passed must be >= 0; got {args.tests_passed}")

    # Booleans (strict lowercase parse)
    for name in ("ci_green", "scope_clean", "review_ready", "merge_ready",
                 "human_cleanup_required"):
        v, err = _parse_bool_strict(name.replace("_", "-"), getattr(args, name))
        if err is not None:
            errors.append(err)

    # hold_reason / error_reason: optional strings (already typed as str)
    for f in ("hold_reason", "error_reason"):
        v = getattr(args, f, None)
        if v is not None and (not isinstance(v, str) or not v.strip()):
            errors.append(f"--{f.replace('_', '-')} must be a non-empty string when provided")

    # notes: optional list of strings
    if not isinstance(args.notes, list):
        errors.append("--note values must be a list")
    else:
        for i, n in enumerate(args.notes):
            if not isinstance(n, str) or not n.strip():
                errors.append(f"--note[{i}] must be a non-empty string")
                break

    # output_json: non-empty string
    if not isinstance(args.output_json, str) or not args.output_json.strip():
        errors.append("--output-json must be a non-empty string")

    if errors:
        return False, errors
    return True, []


# ---------------------------------------------------------------------------
# Packet construction
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_packet(args: argparse.Namespace, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """Build the result-packet dict. Does NOT write to disk; that is
    the responsibility of main(). This is split out so tests can inspect
    the packet without touching the filesystem."""
    # Booleans: we know they parse cleanly here because validate_args
    # already ran (or, when called directly from a test, the caller
    # passed valid lowercase strings).
    def _b(raw: str) -> bool:
        if raw in TRUE_LITERALS:
            return True
        if raw in FALSE_LITERALS:
            return False
        # Should be unreachable given validate_args, but fail safe.
        raise ValueError(f"unexpected boolean literal: {raw!r}")

    packet: Dict[str, Any] = {
        "packet_kind": PACKET_KIND_BUILDER,
        "schema_version": SCHEMA_VERSION,
        "task_id": args.task_id,
        "source_pr": args.source_pr,
        "source_commit": args.source_commit,
        "source_head_sha": args.source_head_sha,
        "title": args.title,
        "status": args.status,
        "changed_files": list(args.changed_files),
        "allowed_files": list(args.allowed_files),
        "scoped_files": list(args.scoped_files),
        "tests_passed": args.tests_passed,
        "ci_green": _b(args.ci_green),
        "scope_clean": _b(args.scope_clean),
        "review_ready": _b(args.review_ready),
        "merge_ready": _b(args.merge_ready),
        "human_cleanup_required": _b(args.human_cleanup_required),
        "result_packet_generated_at": now_iso or _now_iso(),
        "builder_status": STATUS_READY,
    }
    # Optional fields: only include if the user supplied them.
    if args.hold_reason is not None:
        packet["hold_reason"] = args.hold_reason
    if args.error_reason is not None:
        packet["error_reason"] = args.error_reason
    if args.notes:
        packet["notes"] = list(args.notes)
    return packet


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_packet(packet: Dict[str, Any], path: str) -> Tuple[bool, str]:
    """Write the packet as pretty JSON with a trailing newline. Returns (ok, error_message)."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(packet, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        return True, ""
    except OSError as e:
        return False, f"failed to write packet: {e}"


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    ok, errors = validate_args(args)
    if not ok:
        for e in errors:
            print(f"ERROR_INVALID_ARGS: {e}", file=sys.stderr)
        return 2

    try:
        packet = build_packet(args)
        wrote, write_err = write_packet(packet, args.output_json)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR_TOOL_FAILURE: unexpected error: {e}", file=sys.stderr)
        return 1

    if not wrote:
        print(f"ERROR_TOOL_FAILURE: {write_err}", file=sys.stderr)
        return 1

    # Emit a one-line success record to stdout for shell-pipeline visibility.
    print(
        f"RESULT_PACKET_READY task_id={args.task_id} "
        f"source_pr={args.source_pr} status={args.status} "
        f"output_json={args.output_json}",
        file=sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
