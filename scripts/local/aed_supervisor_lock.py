#!/usr/bin/env python3
"""
aed_supervisor_lock.py

Host-local exclusive supervisor lock for an autocoder run.

Goals:
  - Atomically create a lock file at a scope (repository + PR number,
    or repository + mutation target).
  - Reject a second live lock for the same scope with a clear
    "conflicting run" error that records the existing run identity.
  - Distinguish an active lock from a stale lock using EVIDENCE rather
    than PID existence alone:
      1) PID exists AND its /proc/<pid>/stat start_time matches the
         recorded start_time, OR
      2) PID exists AND its /proc/<pid> ctime matches the recorded
         ctime within a small tolerance.
    If neither check can be made (non-Linux, proc unreadable), return
    INDETERMINATE and FAIL CLOSED.
  - Allow bounded stale-lock recovery via a dedicated method that
    records WHO reclaimed the lock, the EVIDENCE used to declare the
    lock stale, and the PREVIOUS run identity. Two workers attempting
    stale recovery simultaneously must not both succeed.
  - Preserve an audit record after lock release.

Lock file format (JSON):
    {
      "lock_version": 1,
      "scope_key": "<str>",
      "scope": {"repository": "...", "target_pr_number": int|None,
                "mutation_target": "..."|None},
      "owner_run_id": "<str>",
      "owner_host": {"hostname": "...", ...},
      "owner_pid": <int>,
      "owner_start_evidence": {...},     # capture_process_start_evidence()
      "created_at": "<iso8601>",
      "max_age_seconds": <int>,          # bounded staleness claim
      "recovery_history": [
          {"recovered_at": "<iso>", "recovered_by_run_id": "<str>",
           "recovered_by_host": {...}, "previous_owner": {...},
           "staleness_evidence": "..."}
      ]
    }

API:
    LockResult = namedtuple("LockResult", ("ok", "path", "owner", "reason"))
"""

from __future__ import annotations

import errno
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from scripts.local.aed_run_identity import (
    _utcnow,
    capture_process_start_evidence,
    safe_restrictive_open,
    write_restrictive_json,
    assert_no_secrets,
)


LOCK_VERSION = 1
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600  # 7 days; bounded recovery window


@dataclass
class LockOutcome:
    ok: bool
    path: Path
    owner: Optional[dict] = None
    reason: str = ""
    indeterminate: bool = False


@dataclass
class LivenessEvidence:
    is_alive: bool
    is_indeterminate: bool
    reason: str
    pid_exists: bool
    stat_start_time_match: bool
    ctime_match: bool


def build_scope_key(*, repository: str, target_pr_number: Optional[int] = None,
                    mutation_target: Optional[str] = None) -> str:
    """Stable string key for the scope. Different scopes can have different locks."""
    if target_pr_number is not None:
        return f"repo:{repository}|pr:{int(target_pr_number)}"
    if mutation_target:
        return f"repo:{repository}|target:{mutation_target}"
    return f"repo:{repository}|run"


def default_lock_dir(repository: str) -> Path:
    """Default lock directory under $XDG_RUNTIME_DIR or ~/.aed/locks."""
    base = os.environ.get("AED_LOCK_DIR")
    if base:
        return Path(base)
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "aed" / "locks"
    return Path.home() / ".aed" / "locks"


def lock_path_for(scope_key: str, base_dir: Optional[Path] = None) -> Path:
    """Safe filename derived from scope_key."""
    safe = scope_key.replace("/", "_").replace(":", "_").replace("|", "_")
    base = Path(base_dir) if base_dir else default_lock_dir(repository="")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base / f"{safe}.lock.json"


def _read_lock(path: Path) -> Optional[dict]:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists but we cannot signal it.
        return True
    except OSError:
        return False
    return True


def _ctime_within_tolerance(actual_ns: Optional[int], expected_ns: Optional[int],
                            tolerance_ns: int = 5_000_000_000) -> bool:
    if actual_ns is None or expected_ns is None:
        return False
    return abs(int(actual_ns) - int(expected_ns)) <= tolerance_ns


def assess_liveness(lock: dict) -> LivenessEvidence:
    """
    Determine whether the lock's owner is still alive.

    Evidence required (ALL):
      - PID exists (signal 0 doesn't raise ProcessLookupError)
      - PID's /proc/<pid>/stat start_time matches recorded start_time,
        OR PID's /proc ctime matches recorded ctime within tolerance.

    If /proc is unreadable for any reason, return is_indeterminate=True
    so callers can fail closed.
    """
    pid = lock.get("owner_pid")
    if not isinstance(pid, int) or pid <= 0:
        return LivenessEvidence(
            is_alive=False,
            is_indeterminate=False,
            reason="missing_or_invalid_pid",
            pid_exists=False,
            stat_start_time_match=False,
            ctime_match=False,
        )

    pid_exists = _pid_exists(pid)

    # Read /proc/<pid>/stat start_time and ctime from live process.
    if not pid_exists:
        return LivenessEvidence(
            is_alive=False,
            is_indeterminate=False,
            reason="pid_does_not_exist",
            pid_exists=False,
            stat_start_time_match=False,
            ctime_match=False,
        )

    # PID exists. Now check process-start evidence.
    actual_evidence = capture_process_start_evidence(pid=pid)
    if actual_evidence is None or actual_evidence["source"] != "linux_proc":
        # We can't read /proc — fail closed.
        return LivenessEvidence(
            is_alive=False,
            is_indeterminate=True,
            reason=f"proc_unreadable_for_pid_{pid}",
            pid_exists=True,
            stat_start_time_match=False,
            ctime_match=False,
        )

    recorded_evidence = lock.get("owner_start_evidence", {}) or {}
    if recorded_evidence is None:
        recorded_evidence = {}

    stat_match = False
    if (
        recorded_evidence.get("stat_start_time") is not None
        and actual_evidence.get("stat_start_time") is not None
    ):
        stat_match = int(recorded_evidence["stat_start_time"]) == int(
            actual_evidence["stat_start_time"]
        )

    ctime_match = _ctime_within_tolerance(
        actual_evidence.get("ctime_ns"),
        recorded_evidence.get("ctime_ns"),
    )

    is_alive = bool(stat_match or ctime_match)
    reason = "ok" if is_alive else "start_evidence_mismatch_pid_reuse"

    return LivenessEvidence(
        is_alive=is_alive,
        is_indeterminate=False,
        reason=reason,
        pid_exists=True,
        stat_start_time_match=stat_match,
        ctime_match=ctime_match,
    )


def try_acquire(
    *,
    scope: dict,
    owner_run_id: str,
    owner_host: dict,
    owner_pid: int,
    owner_start_evidence: dict,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    base_dir: Optional[Path] = None,
) -> LockOutcome:
    """
    Try to acquire the lock atomically.

    On success: writes a fresh lock file and returns ok=True.
    On conflict: returns ok=False with the existing owner and a clear reason.

    The "freshness" check distinguishes the live case (PID exists AND
    evidence matches) from the stale case (no PID, or PID reused).
    Indeterminate liveness FAILS CLOSED (returns ok=False with
    indeterminate=True and reason="indeterminate_liveness").
    """
    scope_key = build_scope_key(
        repository=scope["repository"],
        target_pr_number=scope.get("target_pr_number"),
        mutation_target=scope.get("mutation_target"),
    )
    path = lock_path_for(scope_key, base_dir=base_dir)

    # Build the candidate lock payload.
    payload = {
        "lock_version": LOCK_VERSION,
        "scope_key": scope_key,
        "scope": scope,
        "owner_run_id": owner_run_id,
        "owner_host": owner_host,
        "owner_pid": owner_pid,
        "owner_start_evidence": owner_start_evidence,
        "created_at": _utcnow(),
        "max_age_seconds": int(max_age_seconds),
        "recovery_history": [],
    }
    assert_no_secrets(payload, context=str(path))

    existing = _read_lock(path)
    if existing is None:
        # No existing lock. Atomically create with O_EXCL.
        try:
            fd = os.open(
                str(path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                0o600,
            )
        except FileExistsError:
            # Race: another process beat us between _read_lock and
            # open. Re-read and treat as conflict.
            existing = _read_lock(path)
            assert existing is not None
            return LockOutcome(
                ok=False,
                path=path,
                owner=existing,
                reason="lock_exists_after_race",
            )
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
        return LockOutcome(ok=True, path=path, owner=payload, reason="acquired")

    # Existing lock present. Determine if it's alive.
    evidence = assess_liveness(existing)
    if evidence.is_indeterminate:
        return LockOutcome(
            ok=False,
            path=path,
            owner=existing,
            reason=f"indeterminate_liveness:{evidence.reason}",
            indeterminate=True,
        )
    if evidence.is_alive:
        return LockOutcome(
            ok=False,
            path=path,
            owner=existing,
            reason=f"live_lock_held_by:{existing.get('owner_run_id')}",
        )

    # Stale lock. Do NOT silently overwrite. Caller must use
    # recover_stale() explicitly.
    return LockOutcome(
        ok=False,
        path=path,
        owner=existing,
        reason=f"stale_lock_detected:{evidence.reason}",
    )


def recover_stale(
    *,
    scope: dict,
    recovered_by_run_id: str,
    recovered_by_host: dict,
    recovered_by_pid: int,
    recovered_by_start_evidence: dict,
    staleness_evidence: str,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    base_dir: Optional[Path] = None,
) -> LockOutcome:
    """
    Atomically take over a stale lock with a strict CAS (compare-and-swap).

    Two workers attempting this simultaneously must not both succeed.
    We implement CAS by:
      1. Reading the existing lock (atomically observes the version).
      2. Verifying staleness.
      3. Acquire an exclusive sentinel file lock for the scope's
         path (O_EXCL on a sibling sentinel). The first contender
         wins.
      4. Re-read the lock inside the sentinel to detect a winner.
      5. Atomically rename tmp → target. If the lock file already
         contains a different version chain, we abort and release
         the sentinel.
      6. Release the sentinel.

    On success: returns ok=True, owner=<new owner>, and writes the
    previous owner into recovery_history.
    On failure: returns ok=False with the current owner.
    """
    scope_key = build_scope_key(
        repository=scope["repository"],
        target_pr_number=scope.get("target_pr_number"),
        mutation_target=scope.get("mutation_target"),
    )
    path = lock_path_for(scope_key, base_dir=base_dir)
    sentinel_path = path.with_suffix(path.suffix + ".recovery-sentinel")

    existing = _read_lock(path)
    if existing is None:
        return LockOutcome(
            ok=False, path=path, owner=None,
            reason="no_lock_to_recover",
        )

    # Verify staleness one more time before reclaiming.
    evidence = assess_liveness(existing)
    if evidence.is_indeterminate:
        return LockOutcome(
            ok=False, path=path, owner=existing,
            reason=f"indeterminate_liveness:{evidence.reason}",
            indeterminate=True,
        )
    if evidence.is_alive:
        return LockOutcome(
            ok=False, path=path, owner=existing,
            reason=f"live_lock_held_by:{existing.get('owner_run_id')}",
        )

    # Strict CAS: acquire an exclusive sentinel file lock. The
    # first contender to acquire the sentinel wins the recovery
    # race. Subsequent contenders see FileExistsError.
    try:
        sentinel_fd = os.open(
            str(sentinel_path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o600,
        )
    except FileExistsError:
        # Another worker is already performing recovery. Re-read the
        # lock to report the current owner.
        current = _read_lock(path)
        return LockOutcome(
            ok=False, path=path, owner=current,
            reason="recovery_in_progress_by_other_worker",
        )
    try:
        # Inside the sentinel, re-read to detect a winner.
        existing2 = _read_lock(path)
        if existing2 is None:
            return LockOutcome(
                ok=False, path=path, owner=None,
                reason="lock_disappeared_during_recovery",
            )
        # If the existing lock's version chain advanced (because a
        # winner replaced it), we lose.
        if existing2.get("lock_version_chain", 0) > existing.get("lock_version_chain", 0):
            return LockOutcome(
                ok=False, path=path, owner=existing2,
                reason="cas_lost_recovery_race",
            )

        observed_version = existing2.get("lock_version_chain", 0) + 1
        new_payload = {
            "lock_version": LOCK_VERSION,
            "lock_version_chain": observed_version,
            "scope_key": scope_key,
            "scope": scope,
            "owner_run_id": recovered_by_run_id,
            "owner_host": recovered_by_host,
            "owner_pid": recovered_by_pid,
            "owner_start_evidence": recovered_by_start_evidence,
            "created_at": _utcnow(),
            "max_age_seconds": int(max_age_seconds),
            "observed_predecessor_owner_run_id": existing2.get("owner_run_id"),
            "observed_predecessor_version": existing2.get("lock_version_chain", 0),
            "recovery_history": list(existing2.get("recovery_history", [])) + [
                {
                    "recovered_at": _utcnow(),
                    "recovered_by_run_id": recovered_by_run_id,
                    "recovered_by_host": recovered_by_host,
                    "recovered_by_pid": recovered_by_pid,
                    "previous_owner_run_id": existing2.get("owner_run_id"),
                    "previous_owner_pid": existing2.get("owner_pid"),
                    "previous_owner_created_at": existing2.get("created_at"),
                    "previous_version": existing2.get("lock_version_chain", 0),
                    "staleness_evidence": staleness_evidence,
                    "assess_liveness_reason": evidence.reason,
                }
            ],
        }
        assert_no_secrets(new_payload, context=str(path))

        tmp_path = path.with_suffix(path.suffix + ".recover.tmp")
        try:
            with safe_restrictive_open(tmp_path, "w") as f:
                json.dump(new_payload, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp_path, path)
        except OSError as e:
            return LockOutcome(
                ok=False, path=path, owner=existing2,
                reason=f"recovery_failed:{e.strerror or str(e)}",
            )

        return LockOutcome(
            ok=True, path=path, owner=new_payload,
            reason=f"recovered_from:{existing2.get('owner_run_id')}",
        )
    finally:
        try:
            os.close(sentinel_fd)
        except OSError:
            pass
        try:
            os.unlink(sentinel_path)
        except OSError:
            pass


def release(*, scope: dict, owner_run_id: str, base_dir: Optional[Path] = None) -> bool:
    """Release the lock IF owner_run_id matches the current owner."""
    scope_key = build_scope_key(
        repository=scope["repository"],
        target_pr_number=scope.get("target_pr_number"),
        mutation_target=scope.get("mutation_target"),
    )
    path = lock_path_for(scope_key, base_dir=base_dir)
    existing = _read_lock(path)
    if existing is None:
        return False
    if existing.get("owner_run_id") != owner_run_id:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def read(path: Path) -> Optional[dict]:
    """Read the current lock for inspection."""
    return _read_lock(path)


def assess_from_path(path: Path) -> Optional[LivenessEvidence]:
    existing = _read_lock(path)
    if existing is None:
        return None
    return assess_liveness(existing)