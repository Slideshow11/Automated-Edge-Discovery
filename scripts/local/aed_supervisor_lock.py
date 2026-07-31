#!/usr/bin/env python3
"""
aed_supervisor_lock.py

Host-local exclusive supervisor lock for an autocoder run.

Goals:
  - Atomically create a lease file at a scope (repository + PR number,
    or repository + mutation target).
  - Reject a second live lease for the same scope with a clear
    "conflicting run" error that records the existing run identity.
  - Distinguish an active lease from a stale lease using EVIDENCE
    rather than process liveness alone:
      1) The state file (CONTROLLER_STATE.json) at the run's
         workspace still exists AND
      2) Its mtime is within max_age_seconds of now AND
      3) Its run_identity.run_id matches the lock's owner_run_id.
    If the state file is missing or unreadable, return INDETERMINATE
    and FAIL CLOSED.
  - Allow bounded stale-lease recovery via a dedicated method that
    records WHO reclaimed the lease, the EVIDENCE used to declare
    the lease stale, and the PREVIOUS run identity. Two workers
    attempting stale recovery simultaneously must not both succeed.
  - Preserve an audit record after lease release.

Lease file format (JSON):
    {
      "lock_version": 1,
      "scope_key": "<str>",
      "scope": {"repository": "...", "target_pr_number": int|None,
                "mutation_target": "..."|None},
      "owner_run_id": "<str>",
      "owner_host": {"hostname": "...", ...},
      "owner_pid": <int>,                    # initial bootstrap PID (informational)
      "owner_state_path": "<path>",          # CONTROLLER_STATE.json path
      "owner_start_evidence": {...},         # capture_process_start_evidence()
      "created_at": "<iso8601>",
      "last_renewed_at": "<iso8601>",
      "max_age_seconds": <int>,              # bounded staleness claim
      "lock_version_chain": <int>,           # CAS version chain
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


def _lock_filename_for_scope_key(scope_key: str) -> str:
    """Compute a collision-free lock filename for a scope key.

    Round-6 P2 fix: simple character replacement (`/`, `:`, `|`
    → `_`) is not injective. Two valid scope keys such as
    `repo:a/b|target:feature_x` and `repo:a_b|target:feature|x`
    both mapped to the same filename, causing unrelated
    controllers to conflict. Use a SHA-256 hash of the canonical
    scope key as the lock filename. A hash-based filename is
    lossless and collision-free for any practical scope.
    """
    import hashlib
    digest = hashlib.sha256(scope_key.encode("utf-8")).hexdigest()
    return f"{digest}.lock.json"


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
    """Safe filename derived from scope_key (collision-free via SHA-256)."""
    base = Path(base_dir) if base_dir else default_lock_dir(repository="")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base / _lock_filename_for_scope_key(scope_key)


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


def _parse_iso8601(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        from datetime import datetime
        s = ts.rstrip("Z")
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return None


def _state_file_live(state_path: Optional[str], owner_run_id: Optional[str],
                     max_age_seconds: int) -> tuple[bool, bool, str]:
    """Check whether the run's state file is alive within max_age_seconds.

    Returns (is_alive, is_indeterminate, reason).

    Aliveness evidence:
      1) state_path exists and is readable.
      2) state_path's mtime is within max_age_seconds of now.
      3) state_path's run_identity.run_id matches owner_run_id.

    If state_path is None, return (False, False, "no_state_path")
    so the caller falls through to process-based evidence.
    """
    if not state_path:
        return False, False, "no_state_path"
    p = Path(state_path)
    try:
        st = p.stat()
    except (FileNotFoundError, OSError):
        return False, True, "state_path_missing"
    now = datetime.now().timestamp()
    if (now - st.st_mtime) > max_age_seconds:
        return False, False, f"state_mtime_stale_age={int(now - st.st_mtime)}s"
    if owner_run_id:
        try:
            with open(p) as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            return False, True, "state_unreadable"
        rid = state.get("run_identity") or {}
        if rid.get("run_id") != owner_run_id:
            return False, False, "state_run_id_mismatch"
    return True, False, "ok"


def assess_liveness(lock: dict) -> LivenessEvidence:
    """Determine whether the lock's owner lease is still alive.

    Lease-based liveness: state file mtime + run_id evidence is
    the PRIMARY signal. Process-based evidence is a fallback for
    the rare case where the bootstrap subprocess is still running
    but the state file is missing/stale.

    Round-4/5 fix: when the state file is fresh and the run_id
    matches, the lease is alive EVEN IF the bootstrap PID has
    exited. The original contract tied liveness to PID existence,
    which caused the lock to be classified as stale whenever the
    controller's bootstrap subprocess finished — even though the
    controller run was still active and writing to the state
    file. The PID is a transient bootstrap handle, not a run-life
    signal.
    """
    max_age_seconds = int(lock.get("max_age_seconds", DEFAULT_MAX_AGE_SECONDS))
    owner_run_id = lock.get("owner_run_id")
    state_path = lock.get("owner_state_path")

    # Primary: lease check.
    state_alive, state_indeterminate, state_reason = _state_file_live(
        state_path, owner_run_id, max_age_seconds
    )
    if state_alive:
        return LivenessEvidence(
            is_alive=True,
            is_indeterminate=False,
            reason=f"lease_alive:{state_reason}",
            pid_exists=True,
            stat_start_time_match=True,
            ctime_match=True,
        )
    if state_indeterminate:
        return LivenessEvidence(
            is_alive=False,
            is_indeterminate=True,
            reason=f"indeterminate:{state_reason}",
            pid_exists=False,
            stat_start_time_match=False,
            ctime_match=False,
        )

    # State says stale. Use process-based evidence as a fallback
    # only when the state file's lease check definitively says
    # STALE (not INDETERMINATE). This handles transient state-file
    # gaps where the bootstrap process is still alive.
    pid = lock.get("owner_pid")
    if isinstance(pid, int) and pid > 0 and _pid_exists(pid):
        actual_evidence = capture_process_start_evidence(pid=pid)
        if actual_evidence is None or actual_evidence["source"] != "linux_proc":
            return LivenessEvidence(
                is_alive=False,
                is_indeterminate=False,
                reason=f"state_stale:pid_alive_but_proc_unreadable:{state_reason}",
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

        if stat_match or ctime_match:
            return LivenessEvidence(
                is_alive=True,
                is_indeterminate=False,
                reason="lease_alive_via_pid_evidence",
                pid_exists=True,
                stat_start_time_match=stat_match,
                ctime_match=ctime_match,
            )

    # Stale.
    return LivenessEvidence(
        is_alive=False,
        is_indeterminate=False,
        reason=f"stale:{state_reason}",
        pid_exists=False,
        stat_start_time_match=False,
        ctime_match=False,
    )


def _acquire_sentinel_fd(sentinel_path: Path, max_attempts: int = 20) -> Optional[int]:
    """Acquire an exclusive sentinel file descriptor with OS-managed
    release-on-close semantics.

    Uses fcntl.flock with LOCK_EX|LOCK_NB so the sentinel is held by
    the kernel and released automatically when the process exits
    (or the file descriptor is closed). This survives crashes that
    leave the file on disk but releases the kernel lock.

    Returns the file descriptor on success, or None on timeout.
    """
    import fcntl
    try:
        fd = os.open(
            str(sentinel_path),
            os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC,
            0o600,
        )
    except FileExistsError:
        # The sentinel file exists from a prior process; try to open
        # it for write (without O_EXCL) and flock it. If flock fails
        # with EWOULDBLOCK, another live process holds it.
        try:
            fd = os.open(
                str(sentinel_path),
                os.O_WRONLY | os.O_CLOEXEC,
            )
        except OSError:
            return None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return None
        return fd

    # We just created the sentinel. Lock it.
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        try:
            os.unlink(sentinel_path)
        except OSError:
            pass
        return None
    return fd


def _release_sentinel_fd(fd: Optional[int], sentinel_path: Path) -> None:
    """Release the sentinel file descriptor. The sentinel file is
    left on disk so the next contender can flock it without race
    windows. The file is removed only by the test cleanup, never
    on the lock-release path."""
    import fcntl
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass
    # Do NOT unlink the sentinel file: removing the inode creates a
    # race window where another contender could create a new sentinel
    # at the same path. The sentinel file is a stable inode for the
    # lifetime of the controller run; it is removed only by `unlink`
    # in tests or by the user.


def try_acquire(
    *,
    scope: dict,
    owner_run_id: str,
    owner_host: dict,
    owner_pid: int,
    owner_start_evidence: dict,
    owner_state_path: Optional[str] = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    base_dir: Optional[Path] = None,
) -> LockOutcome:
    """
    Try to acquire the lock atomically.

    On success: writes a fresh lock file and returns ok=True.
    On conflict: returns ok=False with the existing owner and a clear reason.

    The "freshness" check is lease-based (state file mtime + run_id
    evidence) with a process-evidence fallback. Indeterminate
    liveness FAILS CLOSED (returns ok=False with indeterminate=True
    and reason="indeterminate_liveness").
    """
    scope_key = build_scope_key(
        repository=scope["repository"],
        target_pr_number=scope.get("target_pr_number"),
        mutation_target=scope.get("mutation_target"),
    )
    path = lock_path_for(scope_key, base_dir=base_dir)

    # Build the candidate lock payload.
    now_iso = _utcnow()
    payload = {
        "lock_version": LOCK_VERSION,
        "lock_version_chain": 1,
        "scope_key": scope_key,
        "scope": scope,
        "owner_run_id": owner_run_id,
        "owner_host": owner_host,
        "owner_pid": owner_pid,
        "owner_state_path": owner_state_path,
        "owner_start_evidence": owner_start_evidence,
        "created_at": now_iso,
        "last_renewed_at": now_iso,
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
    recovered_by_state_path: Optional[str] = None,
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
         path using fcntl.flock. The first contender wins; subsequent
         contenders see EWOULDBLOCK.
      4. Re-read the lock inside the sentinel to detect a winner.
      5. Atomically rename tmp → target. If the lock file already
         contains a different version chain, we abort and release
         the sentinel.
      6. Release the sentinel (the sentinel file itself is kept on
         disk; see _release_sentinel_fd).

    On success: returns ok=True, owner=<new owner>, and writes the
    previous owner into recovery_history. The recovered lease is
    bound to `recovered_by_state_path` (the recovering run's own
    state file), NOT to the predecessor's state path — otherwise the
    newly recovered lease would be immediately stale.
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

    # Strict CAS: acquire an exclusive sentinel file lock using
    # fcntl.flock so the sentinel self-releases on process death.
    # The first contender to acquire the sentinel wins the
    # recovery race. Subsequent contenders see EWOULDBLOCK.
    sentinel_fd = _acquire_sentinel_fd(sentinel_path)
    if sentinel_fd is None:
        # Another worker is already performing recovery. Re-read
        # the lock to report the current owner.
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
        # P1 fix (round 4): even when the chain did not advance, the
        # lock may have been replaced by try_acquire (which resets
        # the chain to 1 when overwriting a stale lock). Re-assess
        # liveness of existing2 under the sentinel to confirm the
        # predecessor is still stale; otherwise we would overwrite a
        # live lease.
        live2 = assess_liveness(existing2)
        if live2.is_alive:
            return LockOutcome(
                ok=False, path=path, owner=existing2,
                reason="recheck_found_lock_live",
            )
        if live2.is_indeterminate:
            return LockOutcome(
                ok=False, path=path, owner=existing2,
                reason=f"recheck_indeterminate:{live2.reason}",
                indeterminate=True,
            )

        observed_version = existing2.get("lock_version_chain", 0) + 1
        now_iso = _utcnow()
        new_payload = {
            "lock_version": LOCK_VERSION,
            "lock_version_chain": observed_version,
            "scope_key": scope_key,
            "scope": scope,
            "owner_run_id": recovered_by_run_id,
            "owner_host": recovered_by_host,
            "owner_pid": recovered_by_pid,
            "owner_state_path": recovered_by_state_path,
            "owner_start_evidence": recovered_by_start_evidence,
            "created_at": now_iso,
            "last_renewed_at": now_iso,
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
        _release_sentinel_fd(sentinel_fd, sentinel_path)


def release(*, scope: dict, owner_run_id: str, base_dir: Optional[Path] = None) -> bool:
    """Release the lock IF owner_run_id matches the current owner.

    The read-and-delete sequence is serialized using the recovery
    sentinel (flock) so that a concurrent recover_stale cannot
    install a new lease between our check and our unlink. This
    closes the round-4 P1 race where release() could delete a
    freshly-installed live lease belonging to the recovering run.
    """
    scope_key = build_scope_key(
        repository=scope["repository"],
        target_pr_number=scope.get("target_pr_number"),
        mutation_target=scope.get("mutation_target"),
    )
    path = lock_path_for(scope_key, base_dir=base_dir)
    sentinel_path = path.with_suffix(path.suffix + ".recovery-sentinel")
    sentinel_fd = _acquire_sentinel_fd(sentinel_path, max_attempts=20)
    if sentinel_fd is None:
        # Another worker is holding the sentinel; do not unlink.
        return False
    try:
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
    finally:
        _release_sentinel_fd(sentinel_fd, sentinel_path)


def is_lease_held_by_run(
    *,
    scope: dict,
    owner_run_id: str,
    base_dir: Optional[Path] = None,
) -> bool:
    """Return True iff a LIVE lease for `scope` is currently held
    by `owner_run_id`. Used by callers that need to gate
    authorization on live lock ownership.

    This acquires the recovery sentinel so the check is
    race-free with respect to recover_stale and release.
    """
    scope_key = build_scope_key(
        repository=scope["repository"],
        target_pr_number=scope.get("target_pr_number"),
        mutation_target=scope.get("mutation_target"),
    )
    path = lock_path_for(scope_key, base_dir=base_dir)
    sentinel_path = path.with_suffix(path.suffix + ".recovery-sentinel")
    sentinel_fd = _acquire_sentinel_fd(sentinel_path, max_attempts=20)
    if sentinel_fd is None:
        return False
    try:
        existing = _read_lock(path)
        if existing is None:
            return False
        if existing.get("owner_run_id") != owner_run_id:
            return False
        live = assess_liveness(existing)
        if live.is_indeterminate:
            return False
        return bool(live.is_alive)
    finally:
        _release_sentinel_fd(sentinel_fd, sentinel_path)


def read(path: Path) -> Optional[dict]:
    """Read the current lock for inspection."""
    return _read_lock(path)


def assess_from_path(path: Path) -> Optional[LivenessEvidence]:
    existing = _read_lock(path)
    if existing is None:
        return None
    return assess_liveness(existing)