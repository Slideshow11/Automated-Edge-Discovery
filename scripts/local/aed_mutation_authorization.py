#!/usr/bin/env python3
"""
aed_mutation_authorization.py

Durable one-time mutation-authorization lifecycle for the controller.

A "mutation" is any repository or GitHub mutation performed by an
external executor on behalf of the controller (e.g., squash-merge,
force-push, commit-amend, PR-body update, branch delete, label
change, etc.). The controller does NOT itself perform the mutation;
it records an authorization record before the executor performs it,
and the executor records the result after.

Schema (v1):
    MUTATIONS_FILE = <workspace>/MUTATIONS.jsonl
    Each line is one JSON object:
        {
          "mutation_id": "<uuid4>",
          "run_id": "<str>",
          "repository": "<str>",
          "target_pr_number": <int> | None,
          "mutation_target": "<str>" | None,
          "mutation_type": "<str>",
          "expected_main_sha": "<sha>" | None,
          "expected_target_sha": "<sha>" | None,
          "pending_action": "<str>",
          "created_at": "<iso>",
          "authorization_status": "authorized" |
                                    "rejected" |
                                    "superseded",
          "result": {
              "status": "success" | "failure" | "indeterminate" | None,
              "recorded_at": "<iso>" | None,
              "evidence": "<str>" | None,
              "actual_main_sha": "<sha>" | None,
              "actual_target_sha": "<sha>" | None,
              "error_detail": "<str>" | None
          } | None
        }

Authorization record write rules:
  - One-time write; once a mutation_id is authorized, it cannot be
    re-authorized.
  - If the authorization would be a DUPLICATE of an existing
    authorized mutation for the same (run_id, mutation_type, target),
    the second request is REJECTED and the existing record is marked
    superseded=False (the duplicate is logged into the existing record's
    "duplicate_attempts" list). The controller may instead emit a new
    mutation_id and the executor may attempt to use it, but a different
    mutation_id for an identical (run_id, type, target, expected heads)
    IS itself an authorization that is logged.

Result recording rules:
  - A mutation result MUST reference an existing authorized mutation_id.
  - An exact replay (same fields) is accepted as idempotent.
  - Any non-identical replay FAILS CLOSED (raises).
  - Terminal statuses: success | failure | indeterminate.
  - Once a mutation has a terminal result, no further results can be
    recorded (fail closed).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from scripts.local.aed_run_identity import (
    _utcnow,
    safe_restrictive_open,
    assert_no_secrets,
    file_mode,
)


MUTATIONS_FILENAME = "MUTATIONS.jsonl"
MUTATIONS_LOCK_FILENAME = "MUTATIONS.jsonl.lock"

AUTHORIZED = "authorized"
REJECTED = "rejected"
SUPERSEDED = "superseded"

RESULT_SUCCESS = "success"
RESULT_FAILURE = "failure"
RESULT_INDETERMINATE = "indeterminate"

TERMINAL_RESULTS = frozenset({RESULT_SUCCESS, RESULT_FAILURE, RESULT_INDETERMINATE})


@dataclass
class AuthorizationRequest:
    run_id: str
    repository: str
    target_pr_number: Optional[int]
    mutation_target: Optional[str]
    mutation_type: str
    expected_main_sha: Optional[str]
    expected_target_sha: Optional[str]
    pending_action: str


@dataclass
class AuthorizationOutcome:
    ok: bool
    mutation_id: Optional[str]
    record: Optional[dict]
    reason: str = ""


def mutations_path(workspace: Path) -> Path:
    return Path(workspace) / MUTATIONS_FILENAME


def _read_all_records(workspace: Path) -> list[dict]:
    path = mutations_path(workspace)
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path) as f:
        for line_no, line in enumerate(f, start=1):
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"corrupt MUTATIONS.jsonl at line {line_no}: {e}"
                ) from e
    return out


def _append_record(workspace: Path, record: dict) -> None:
    path = mutations_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    assert_no_secrets(record, context=str(path))
    # Append a single line atomically with restrictive permissions.
    line = json.dumps(record, sort_keys=True) + "\n"
    fd = os.open(
        str(path),
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC,
        0o600,
    )
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def _rewrite_record(workspace: Path, updated: dict) -> None:
    """Atomically rewrite MUTATIONS.jsonl so that the authorization
    record with the matching mutation_id is replaced with `updated`.
    Other records (journal entries) are preserved."""
    records = _read_all_records(workspace)
    found = False
    new_lines: list[str] = []
    for rec in records:
        if (
            rec.get("kind") not in ("result", "result_replay_idempotent")
            and rec.get("mutation_id") == updated.get("mutation_id")
        ):
            new_lines.append(json.dumps(updated, sort_keys=True))
            found = True
        else:
            new_lines.append(json.dumps(rec, sort_keys=True))
    if not found:
        new_lines.append(json.dumps(updated, sort_keys=True))
    path = mutations_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(
        str(tmp_path),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC,
        0o600,
    )
    try:
        with os.fdopen(fd, "w") as f:
            for line in new_lines:
                f.write(line + "\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def authorize(workspace: Path, req: AuthorizationRequest) -> AuthorizationOutcome:
    """
    Authorize a one-time mutation. Returns ok=False and a clear
    reason when:
      - An identical authorization already exists for this run/type/target
        (treated as a duplicate and rejected).
      - A different authorization exists for the same scope with
        non-matching expected heads (treated as a duplicate and
        rejected).

    The duplicate scan and append are serialized with an exclusive
    sentinel file so that two concurrent executors cannot both
    succeed in authorizing the same scope.
    """
    path = mutations_path(workspace)
    sentinel_path = path.with_suffix(path.suffix + ".auth-sentinel")

    # Acquire an exclusive sentinel. The first caller to reach the
    # O_EXCL open wins; subsequent callers see FileExistsError and
    # back off briefly. We retry the sentinel acquisition briefly to
    # handle the case where another caller holds it momentarily.
    sentinel_fd = None
    for _ in range(20):
        try:
            sentinel_fd = os.open(
                str(sentinel_path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                0o600,
            )
            break
        except FileExistsError:
            import time as _time
            _time.sleep(0.05)
    if sentinel_fd is None:
        return AuthorizationOutcome(
            ok=False,
            mutation_id=None,
            record=None,
            reason="authorization_lock_busy",
        )

    try:
        existing = _read_all_records(workspace)
        for rec in existing:
            if rec.get("run_id") != req.run_id:
                continue
            if rec.get("mutation_type") != req.mutation_type:
                continue
            if rec.get("repository") != req.repository:
                continue
            if rec.get("target_pr_number") != req.target_pr_number:
                continue
            if rec.get("mutation_target") != req.mutation_target:
                continue
            if rec.get("authorization_status") != AUTHORIZED:
                continue
            heads_match = (
                rec.get("expected_main_sha") == req.expected_main_sha
                and rec.get("expected_target_sha") == req.expected_target_sha
            )
            if heads_match and rec.get("result") is None:
                return AuthorizationOutcome(
                    ok=False,
                    mutation_id=None,
                    record=rec,
                    reason="duplicate_authorization",
                )
            if heads_match and rec.get("result") is not None:
                return AuthorizationOutcome(
                    ok=False,
                    mutation_id=None,
                    record=rec,
                    reason="duplicate_authorization_already_completed",
                )
            return AuthorizationOutcome(
                ok=False,
                mutation_id=None,
                record=rec,
                reason="duplicate_authorization_with_drifted_heads",
            )

        mutation_id = str(uuid.uuid4())
        record = {
            "mutation_id": mutation_id,
            "run_id": req.run_id,
            "repository": req.repository,
            "target_pr_number": req.target_pr_number,
            "mutation_target": req.mutation_target,
            "mutation_type": req.mutation_type,
            "expected_main_sha": req.expected_main_sha,
            "expected_target_sha": req.expected_target_sha,
            "pending_action": req.pending_action,
            "created_at": _utcnow(),
            "authorization_status": AUTHORIZED,
            "result": None,
        }
        _append_record(workspace, record)
        return AuthorizationOutcome(ok=True, mutation_id=mutation_id, record=record)
    finally:
        try:
            os.close(sentinel_fd)
        except OSError:
            pass
        try:
            os.unlink(sentinel_path)
        except OSError:
            pass


def record_result(
    workspace: Path,
    *,
    mutation_id: str,
    status: str,
    evidence: Optional[str] = None,
    actual_main_sha: Optional[str] = None,
    actual_target_sha: Optional[str] = None,
    error_detail: Optional[str] = None,
) -> dict:
    """
    Record the terminal result of an authorized mutation.

    Returns the updated record. Raises:
      - KeyError if mutation_id does not exist
      - ValueError if the result is not a terminal status, the
        mutation was never authorized, or a duplicate non-identical
        result is replayed.
    """
    if status not in TERMINAL_RESULTS:
        raise ValueError(f"non_terminal_status:{status}")

    records = _read_all_records(workspace)
    target: Optional[dict] = None
    target_idx: Optional[int] = None
    for i, rec in enumerate(records):
        # Skip journal entries that are not the authorization record.
        if rec.get("kind") in ("result", "result_replay_idempotent"):
            continue
        if rec.get("mutation_id") == mutation_id:
            target = rec
            target_idx = i
            break
    if target is None:
        raise KeyError(f"unknown_mutation_id:{mutation_id}")

    # Determine the existing result from the on-disk authorization
    # record. The authorization record may have been updated by
    # prior results, so we re-read the latest state.
    existing_result = target.get("result")
    # If the on-disk record's result is None, look for an appended
    # result line matching this mutation_id (this happens when the
    # previous call appended the result line but didn't mutate the
    # on-disk record).
    if existing_result is None:
        for rec in records:
            if rec.get("kind") == "result" and rec.get("mutation_id") == mutation_id:
                existing_result = rec.get("result")
                break

    if target.get("authorization_status") != AUTHORIZED:
        raise ValueError(
            f"mutation_not_authorized:{target.get('authorization_status')}"
        )

    new_result = {
        "status": status,
        "recorded_at": _utcnow(),
        "evidence": evidence,
        "actual_main_sha": actual_main_sha,
        "actual_target_sha": actual_target_sha,
        "error_detail": error_detail,
    }
    if existing_result is None:
        # First result. Persist the updated authorization record
        # (with result populated) atomically and append a result
        # journal entry for traceability.
        target["result"] = new_result
        _rewrite_record(workspace, target)
        result_record = {
            "kind": "result",
            "mutation_id": mutation_id,
            "run_id": target["run_id"],
            "result": new_result,
            "recorded_at": _utcnow(),
        }
        _append_record(workspace, result_record)
        return target

    # Already has a result. Either idempotent replay or fail-closed.
    if _results_equal(existing_result, new_result):
        # Exact idempotent replay. Append a replay audit line.
        replay_record = {
            "kind": "result_replay_idempotent",
            "mutation_id": mutation_id,
            "run_id": target["run_id"],
            "result": new_result,
            "recorded_at": _utcnow(),
        }
        _append_record(workspace, replay_record)
        target["result"] = existing_result
        return target

    raise ValueError(
        f"duplicate_non_identical_result:existing={existing_result};new={new_result}"
    )


def _results_equal(a: dict, b: dict) -> bool:
    # Compare only the meaningful fields.
    keys = ("status", "evidence", "actual_main_sha", "actual_target_sha", "error_detail")
    return all(a.get(k) == b.get(k) for k in keys)


def outstanding_mutations(workspace: Path) -> list[dict]:
    """Return all authorized mutations that have no terminal result."""
    out: list[dict] = []
    seen_ids: set[str] = set()
    records = _read_all_records(workspace)
    for rec in records:
        if rec.get("kind") in ("result", "result_replay_idempotent"):
            continue
        mid = rec.get("mutation_id")
        if mid is None:
            continue
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        if rec.get("authorization_status") == AUTHORIZED and rec.get("result") is None:
            out.append(rec)
    return out


def find_authorization(workspace: Path, mutation_id: str) -> Optional[dict]:
    records = _read_all_records(workspace)
    for rec in records:
        if rec.get("kind") in ("result", "result_replay_idempotent"):
            continue
        if rec.get("mutation_id") == mutation_id:
            return rec
    return None


def mutations_file_mode(workspace: Path) -> Optional[int]:
    return file_mode(mutations_path(workspace))


__all__ = [
    "AuthorizationRequest",
    "AuthorizationOutcome",
    "MUTATIONS_FILENAME",
    "AUTHORIZED",
    "REJECTED",
    "SUPERSEDED",
    "RESULT_SUCCESS",
    "RESULT_FAILURE",
    "RESULT_INDETERMINATE",
    "TERMINAL_RESULTS",
    "mutations_path",
    "authorize",
    "record_result",
    "outstanding_mutations",
    "find_authorization",
    "mutations_file_mode",
]