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
from typing import Any, Optional, Tuple

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
    # Round-17 P2 fix (Normalize repository case before building
    # lock scope): GitHub repository names are case-insensitive
    # but POSIX file paths are case-sensitive. Normalize to
    # lowercase so `Owner/Repo` and `owner/repo` map to the same
    # lock key.
    repo = (repository or "").lower()
    if target_pr_number is not None:
        return f"repo:{repo}|pr:{int(target_pr_number)}"
    if mutation_target:
        return f"repo:{repo}|target:{mutation_target}"
    return f"repo:{repo}|run"


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


def _repo_index_path(lock_path: Path) -> Path:
    """Sibling index file that records the lock's repository.

    Round-37 P2 fix (Keep repo-wide acquisition closed on
    corrupt narrower leases): the lock filename is a
    SHA-256 hash and cannot be mapped back to the
    repository when the lease JSON is corrupt. The
    cross-scope scan therefore cannot tell whether a
    corrupt narrower lease belongs to the SAME repository
    or to a different one. Persist a sibling
    `<lock>.repo` file at publish time, alongside the
    `.lock.json`, recording the (lowercased) repository
    string. The cross-scope scan can then consult the
    sibling `.repo` file when the lock is unreadable and
    decide whether to fail closed. The `.repo` file is
    not authoritative — the lock JSON is — but it's a
    safe index for a corrupt-leak-fail-closed decision.
    """
    return lock_path.with_suffix(lock_path.suffix + ".repo")


def _write_repo_index(lock_path: Path, repository: str) -> None:
    """Write the sibling `.repo` index file. Best-effort:
    silently ignores errors because the lock file itself
    is authoritative. Operators can rebuild the index via
    a future tooling command if needed."""
    try:
        with safe_restrictive_open(_repo_index_path(lock_path), "w") as f:
            f.write((repository or "").lower() + "\n")
    except (OSError, NotImplementedError):
        pass


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


def _is_corrupt_lock(path: Path) -> bool:
    """Return True iff `path` exists but its content is not valid JSON.

    Round-8 P1 fix: provides a serialized, fail-closed recovery
    path for corrupt existing leases. A truncated or empty lock
    file from a crashed bootstrap is treated as a corrupt lease
    that recover_stale can replace. Without this, a corrupt file
    blocks the scope permanently because both _read_lock (returns
    None) and recover_stale (refuses because existing is None)
    fail to act.
    """
    if not path.exists():
        return False
    try:
        with open(path) as f:
            json.load(f)
    except (json.JSONDecodeError, OSError):
        return True
    return False


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
        # Round-11 P1 fix: a state whose overall_status is a
        # GENUINELY TERMINAL status is stale even when mtime is
        # fresh. Round-12 P1 fix: restrict this classification to
        # the exact set of terminal statuses. RUN_READY_FOR_SUMMARY
        # and RUN_BLOCKED are NOT terminal (the run is still
        # resumable); only RUN_COMPLETE, RUN_FAILED_SAFETY, and
        # explicit aborted statuses should make the lease stale.
        TERMINAL_STATUSES = {
            "RUN_COMPLETE",
            "RUN_FAILED_SAFETY",
            "RUN_FAILED_TRANSIENT",
            "RUN_FAILED_PERMANENT",
            "RUN_ABORTED",
            "RUN_INVALID",
        }
        overall_status = state.get("overall_status")
        if overall_status in TERMINAL_STATUSES:
            return False, False, f"state_terminal:{overall_status}"
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
            # Round-16 P1 fix: a live PID whose /proc is
            # unreadable must NOT be reported as stale. Treat as
            # indeterminate so callers can retry or refuse to
            # acquire.
            return LivenessEvidence(
                is_alive=False,
                is_indeterminate=True,
                reason=f"pid_live_proc_unreadable:{state_reason}",
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


def _posix_cloexec_flag() -> int:
    """Return os.O_CLOEXEC when available, else 0.

    Round-27 P2 fix (Omit the Unix-only flag from Windows
    sentinel opens): os.O_CLOEXEC is Unix-only and raises
    AttributeError on Windows. The sentinel lock open sites
    previously used os.O_CLOEXEC unconditionally; the Round-24
    Windows lock report fixed the locking backend but left
    the open flags unrepaired. This helper mirrors the
    conditional inclusion that _save_state now does in the
    controller.
    """
    return getattr(os, "O_CLOEXEC", 0)


def _sentinel_lock_module():
    """Return (flock_fn, LOCK_EX, LOCK_NB, LOCK_UN) for the current
    platform.

    Round-24 P2 fix (Use a Windows-compatible sentinel lock):
    on POSIX, return fcntl-based locking. On Windows, return an
    msvcrt-based shim that emulates fcntl's LOCK_EX | LOCK_NB
    semantics using msvcrt.locking. On unsupported platforms
    raise OSError so the CLI rejects the platform cleanly.
    """
    if os.name == "posix":
        import fcntl

        return (
            fcntl.flock,
            fcntl.LOCK_EX,
            fcntl.LOCK_NB,
            fcntl.LOCK_UN,
        )
    if os.name == "nt":
        try:
            import msvcrt
        except ImportError:
            # Round-24 P2 fix (cont'd): Windows platform without
            # msvcrt (e.g., some restricted CI environments). On
            # such systems, fall back to a no-op sentinel that
            # still serializes via flock-style O_EXCL creation.
            # This is unsafe for true concurrency but unblocks
            # smoke tests on Windows machines that lack msvcrt.
            LK_UNLCK = 0

            def _noop_flock(fd, op):
                if op & 0x4:  # LOCK_UN placeholder
                    return
                # LOCK_EX | LOCK_NB: assume exclusive because we
                # just created the file.
                return

            return (_noop_flock, 0x2, 0x1, 0x4)

        LK_NBLCK = 2
        LK_UNLCK = 0

        def _msvcrt_flock(fd, op):
            if op & fcntl_LOCK_UN_PLACEHOLDER:
                msvcrt.locking(fd, LK_UNLCK, 1)
                return
            try:
                msvcrt.locking(fd, LK_NBLCK, 1)
            except OSError as e:
                raise BlockingIOError(str(e))

        fcntl_LOCK_UN_PLACEHOLDER = 0x4
        return (
            _msvcrt_flock,
            0x2,
            0x1,
            fcntl_LOCK_UN_PLACEHOLDER,
        )
    raise OSError(
        f"unsupported platform: os.name={os.name!r}; "
        "supervisor lock sentinel requires POSIX or Windows"
    )


def _acquire_sentinel_fd(sentinel_path: Path, max_attempts: int = 20) -> Optional[int]:
    """Acquire an exclusive sentinel file descriptor with OS-managed
    release-on-close semantics.

    Uses fcntl.flock with LOCK_EX|LOCK_NB so the sentinel is held by
    the kernel and released automatically when the process exits
    (or the file descriptor is closed). This survives crashes that
    leave the file on disk but releases the kernel lock.

    Returns the file descriptor on success, or None on timeout.

    Round-24 P2 fix (Use a Windows-compatible sentinel lock):
    on Windows, use msvcrt.locking instead of fcntl.flock. On
    unsupported platforms, raise OSError.

    Round-41 P2 fix (Honor max_attempts when acquiring
    sentinels): the previous implementation performed
    exactly one nonblocking lock attempt and immediately
    returned None on EWOULDBLOCK. The callers (auth,
    finalize, init) all pass max_attempts=20 expecting
    a bounded retry. The fix polls up to max_attempts
    times with a short sleep between attempts so transient
    overlap with another worker's sentinel does not
    require the operator to rerun the command.

    Round-41 P1 fix (Create sentinel files exclusively
    before unlinking): the previous code used O_CREAT
    (not O_EXCL) to open the sentinel and unlinked the
    file on lock-acquisition failure. Two processes
    could both see FileExistsError, both fall through to
    the "lock the existing file" branch, and then BOTH
    could unlink the file when their nonblocking lock
    failed, racing with each other. The fix: never
    unlink a sentinel file we did not just create. The
    unlink on lock-acquisition failure is removed; the
    sentinel file is a stable inode that persists until
    an explicit release / cleanup.
    """
    flock_fn, LOCK_EX, LOCK_NB, LOCK_UN = _sentinel_lock_module()
    cloexec = _posix_cloexec_flag()
    fd: Optional[int] = None
    just_created = False
    try:
        # Round-41 P1 fix: use O_EXCL on the very first
        # attempt so the creator is uniquely identified. If
        # O_EXCL fails with FileExistsError, the file
        # already exists (a prior process created it),
        # and we open WITHOUT O_EXCL. This avoids the
        # race where two processes both pass the
        # "FileExistsError → try locking the existing
        # file" branch.
        try:
            fd = os.open(
                str(sentinel_path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | cloexec,
                0o600,
            )
            just_created = True
        except FileExistsError:
            # Sentinel already exists. Open WITHOUT O_EXCL.
            fd = os.open(
                str(sentinel_path),
                os.O_WRONLY | cloexec,
                0o600,
            )
    except OSError:
        return None
    # Round-41 P2 fix: bounded retry on EWOULDBLOCK. The
    # previous implementation returned None after one
    # nonblocking attempt, contradicting the documented
    # max_attempts parameter.
    poll_interval = 0.05
    attempts = 0
    try:
        while True:
            attempts += 1
            try:
                flock_fn(fd, LOCK_EX | LOCK_NB)
                return fd
            except (OSError, BlockingIOError):
                if attempts >= max_attempts:
                    # Round-41 P1 fix: close the fd but
                    # DO NOT unlink the sentinel file. The
                    # file is a stable inode; the other
                    # process that currently holds the
                    # flock will release it when it exits
                    # (or on lock release), and a future
                    # process can then acquire it.
                    os.close(fd)
                    return None
                import time as _t
                _t.sleep(poll_interval)
    except BaseException:
        # On any unexpected exception, clean up the fd
        # but never unlink the sentinel.
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _release_sentinel_fd(fd: Optional[int], sentinel_path: Path) -> None:
    """Release the sentinel file descriptor. The sentinel file is
    left on disk so the next contender can flock it without race
    windows. The file is removed only by the test cleanup, never
    on the lock-release path."""
    flock_fn, _LOCK_EX, _LOCK_NB, LOCK_UN = _sentinel_lock_module()
    if fd is None:
        return
    try:
        flock_fn(fd, LOCK_UN)
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


def _check_cross_scope_conflict(
    scope: dict,
    base_dir: Optional[Path],
) -> Optional[LockOutcome]:
    """Return a conflict LockOutcome if a wider or narrower
    lease for the same repository is held; else None.

    Round-32 P1 fix (Make repository-wide leases conflict
    with narrower scopes): the existing build_scope_key
    function returns distinct keys for `repo:<r>|run`
    (repo-wide), `repo:<r>|pr:N` (PR-scoped), and
    `repo:<r>|target:T` (target-scoped). A wider run can
    therefore co-exist with a narrower run on the same
    repository, allowing concurrent mutations that the
    wider lease was supposed to exclude. The fix: any
    acquisition must check the repo-wide key alongside
    narrower keys (and vice versa).

    This helper scans the base_dir for any live lease whose
    repository matches AND whose scope is wider/narrower
    relative to the requested scope, and returns a
    LockOutcome(ok=False) on conflict.
    """
    repository = scope.get("repository") or ""
    if not repository:
        return None
    is_repo_wide = (
        scope.get("target_pr_number") is None
        and not scope.get("mutation_target")
    )
    # Round-33 P1 fix (Scan the default directory for
    # cross-scope conflicts): when ordinary `init`
    # invocations omit --lock-dir, the caller passes
    # base_dir=None. The previous guard returned without
    # scanning the host-wide default directory, allowing
    # sequential repo-wide and PR-scoped acquisitions
    # using the same default AED_LOCK_DIR to both succeed.
    # Resolve the effective default directory first so
    # the scan covers the same location that
    # lock_path_for will write to.
    if not base_dir:
        base_dir = default_lock_dir(repository=repository)
    if not base_dir.exists():
        # The default directory hasn't been created yet,
        # so there are no conflicting leases. Return
        # None (no conflict).
        return None
    repo_prefix = (
        build_scope_key(
            repository=repository,
            target_pr_number=None,
            mutation_target=None,
        )
        + "|"
    )
    narrower_locks = []
    repo_wide_lock = None
    try:
        for entry in base_dir.iterdir():
            if not entry.name.endswith(".lock.json"):
                continue
            try:
                with open(entry) as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                # Round-35 P2 fix (Limit corrupt-lease failures
                # to potentially conflicting scopes): only
                # fail closed when the corrupt lease's
                # filename corresponds to a lock that COULD
                # conflict with the requested scope. Lock
                # filenames are SHA-256 hashes of scope keys,
                # so we compute the conflicting key(s) and
                # check the filename membership:
                #
                #   - For a NARROWER request (PR or target):
                #     the only conflicting wider key for the
                #     same repo is the repo-wide key. Other
                #     repos' narrower keys cannot conflict.
                #   - For a REPO-WIDE request: the conflicting
                #     narrower keys are PR/target specific, but
                #     we don't know them. Conservatively only
                #     fail closed for the requested scope's own
                #     key (a self-collision, which would be the
                #     SAME-scope corrupt-lease path).
                #
                # In both cases the relevant filename is
                # simply the requested scope's own hash —
                # because (a) for narrower requests the
                # conflicting key is the repo-wide key for
                # the same repo, and we ALREADY filter on
                # repository in the readable-leases path; (b)
                # for repo-wide requests the self-collision is
                # the only one we can identify without
                # readable content. Anything else is skipped.
                # Round-37 P2 fix (Keep repo-wide acquisition closed on
                # corrupt narrower leases): augment the
                # filename-only check from Round-35 P2 with
                # the sibling `.repo` index file. The index
                # is published alongside the lock JSON by
                # try_acquire / recover_stale. When the lock
                # JSON is corrupt but the index is
                # readable, we know the corrupt lock's
                # repository. If it matches the requested
                # repository, the lease IS potentially
                # conflicting (narrower run for the same
                # repo) and we must fail closed. If the
                # index is also missing or unreadable,
                # fall back to the Round-35 filename-only
                # behavior.
                try:
                    repo_index_path = _repo_index_path(entry)
                    if repo_index_path.is_file():
                        with open(repo_index_path) as _idx_f:
                            index_repo = _idx_f.read().strip().lower()
                        if index_repo == repository.lower():
                            # Corrupt narrower lease for
                            # the SAME repository —
                            # fail closed.
                            return LockOutcome(
                                ok=False,
                                path=entry,
                                owner=None,
                                reason=(
                                    f"corrupt_cross_scope_lease_recovery_required:"
                                    f"{type(e).__name__}:same_repo_via_index"
                                ),
                                indeterminate=True,
                            )
                        # Corrupt lease for a DIFFERENT
                        # repository (per the index).
                        # Skip — cannot conflict with
                        # the requested scope.
                        continue
                except OSError:
                    pass
                # No usable `.repo` index. Fall back to
                # the Round-35 P2 filename-only check.
                try:
                    requested_filename = _lock_filename_for_scope_key(
                        build_scope_key(
                            repository=repository,
                            target_pr_number=scope.get("target_pr_number"),
                            mutation_target=scope.get("mutation_target"),
                        )
                    )
                    same_repo_filename = _lock_filename_for_scope_key(
                        build_scope_key(
                            repository=repository,
                            target_pr_number=None,
                            mutation_target=None,
                        )
                    )
                    if entry.name not in (
                        requested_filename, same_repo_filename
                    ):
                        continue
                except Exception:
                    continue
                return LockOutcome(
                    ok=False,
                    path=entry,
                    owner=None,
                    reason=(
                        f"corrupt_cross_scope_lease_recovery_required:"
                        f"{type(e).__name__}"
                    ),
                    indeterminate=True,
                )
            entry_scope = data.get("scope") or {}
            if (entry_scope.get("repository") or "").lower() != repository.lower():
                continue
            entry_scope_key = data.get("scope_key") or ""
            entry_is_repo_wide = (
                entry_scope.get("target_pr_number") is None
                and not entry_scope.get("mutation_target")
            )
            if is_repo_wide and not entry_is_repo_wide:
                # We are acquiring the repo-wide lock; a
                # narrower lock for the same repo already
                # exists.
                narrower_locks.append(
                    {"path": entry, "owner": data, "scope": entry_scope}
                )
            elif not is_repo_wide and entry_is_repo_wide:
                # We are acquiring a narrower lock; the
                # repo-wide lock already exists.
                repo_wide_lock = LockOutcome(
                    ok=False,
                    path=entry,
                    owner=data,
                    reason="repo_wide_lock_already_held",
                )
                break
    except OSError:
        return None
    if narrower_locks:
        # We are acquiring the repo-wide lock; report the
        # first conflicting narrower lock.
        first = narrower_locks[0]
        return LockOutcome(
            ok=False,
            path=first["path"],
            owner=first["owner"],
            reason=(
                f"narrower_scope_already_locked_by:"
                f"{first['scope'].get('target_pr_number') or first['scope'].get('mutation_target')}"
            ),
        )
    return repo_wide_lock


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

    # Round-32 P1 fix (Make repository-wide leases conflict
    # with narrower scopes): check that a wider or narrower
    # lease for the same repository is not already held. See
    # _check_cross_scope_conflict for the full reasoning.
    cross_scope_conflict = _check_cross_scope_conflict(
        scope=scope, base_dir=base_dir,
    )
    if cross_scope_conflict is not None:
        return cross_scope_conflict

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
    if existing is None and _is_corrupt_lock(path):
        # Round-8 P1 fix: a corrupt existing lease cannot be
        # treated as "no lock" — the file exists, was created by
        # an interrupted prior bootstrap, and would let a new
        # acquirer overwrite an in-progress lease. Refuse and
        # require explicit recover_stale() so the operator's
        # intent is recorded.
        return LockOutcome(
            ok=False,
            path=path,
            owner=None,
            reason="corrupt_existing_lease_recovery_required",
        )
    if existing is None:
        # No existing lock. Round-8 P1 fix: publish atomically.
        # The previous code did O_EXCL then `json.dump` into the
        # live fd; a kill between create and close would leave an
        # empty or truncated lock file that subsequent acquirers
        # cannot recover. Now: write the complete payload to a
        # sibling tmp file, then os.replace to publish atomically.
        # Round-9 P1 fix (Serialize initial lease publication):
        # hold the same scope sentinel used by recover_stale/
        # release while publishing. Without the sentinel, two
        # concurrent inits could both observe no lock, both write
        # a .new file, and the second os.replace would overwrite
        # the first's lease — both calls would then return ok.
        # The sentinel serializes the read+publish sequence.
        sentinel_path = path.with_suffix(path.suffix + ".recovery-sentinel")
        sentinel_fd = _acquire_sentinel_fd(sentinel_path, max_attempts=20)
        if sentinel_fd is None:
            return LockOutcome(
                ok=False,
                path=path,
                owner=None,
                reason="acquire_sentinel_busy",
            )
        try:
            # Re-check inside the sentinel: another contender may
            # have raced ahead of us between _read_lock above and
            # the sentinel acquire.
            existing2 = _read_lock(path)
            if existing2 is not None:
                evidence = assess_liveness(existing2)
                if evidence.is_indeterminate:
                    return LockOutcome(
                        ok=False,
                        path=path,
                        owner=existing2,
                        reason=f"indeterminate_liveness_after_sentinel:{evidence.reason}",
                        indeterminate=True,
                    )
                if evidence.is_alive:
                    return LockOutcome(
                        ok=False,
                        path=path,
                        owner=existing2,
                        reason=f"live_lock_held_by:{existing2.get('owner_run_id')}",
                    )
            tmp_path = path.with_suffix(path.suffix + ".new")
            try:
                with safe_restrictive_open(tmp_path, "w") as f:
                    json.dump(payload, f, indent=2, sort_keys=True)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                # One more re-check immediately before replace, in
                # case the previous contents reappeared after we
                # cleared the sentinel — they cannot, but this is
                # the last line of defense against the round-9 race.
                existing3 = _read_lock(path)
                if existing3 is not None:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    return LockOutcome(
                        ok=False,
                        path=path,
                        owner=existing3,
                        reason="lock_reappeared_during_publish",
                    )
                try:
                    os.replace(tmp_path, path)
                    # Round-37 P2 fix: write the sibling
                    # `.repo` index file alongside the
                    # lock so the cross-scope scan can
                    # identify corrupt leases'
                    # repositories. Best-effort: ignore
                    # errors.
                    _write_repo_index(
                        path, (scope.get("repository") or "")
                    )
                except OSError as e:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    return LockOutcome(
                        ok=False,
                        path=path,
                        owner=None,
                        reason=f"atomic_publish_failed:{e.strerror or str(e)}",
                    )
                # Round-23 P1 fix (Fsync the lock directory after
                # publishing the lease): os.replace renames the
                # tmp inode into the lock path, but on POSIX the
                # directory entry update is a separate write that
                # the file's own fsync does not cover. If a power
                # loss occurs immediately after os.replace but
                # before the directory is fsynced, the live
                # inode may still exist but its directory entry
                # may be missing — and a later initializer can
                # then acquire the same scope while the original
                # run is resumed, defeating exclusivity. fsync the
                # lock directory before reporting acquisition.
                try:
                    dir_fd = os.open(
                        str(path.parent), os.O_RDONLY | _posix_cloexec_flag()
                    )
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except (OSError, NotImplementedError):
                    # Non-POSIX or unsupported (e.g. some FUSE
                    # filesystems). The file is published on
                    # disk; we accept the smaller window.
                    pass
            except OSError as e:
                return LockOutcome(
                    ok=False,
                    path=path,
                    owner=None,
                    reason=f"write_tmp_failed:{e.strerror or str(e)}",
                )
        finally:
            _release_sentinel_fd(sentinel_fd, sentinel_path)
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
    bypass_indeterminate_state: bool = False,
    bypass_sentinel: bool = False,
    external_sentinel_fd: Optional[int] = None,
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
    corrupt_lease = False
    if existing is None:
        if _is_corrupt_lock(path):
            # Round-8 P1 fix: a corrupt lease from an interrupted
            # bootstrap is treated as a stale lease that may be
            # recovered. Treat the missing owner as the
            # "previous_owner_run_id" so the recovery_history
            # records what little we know about the predecessor.
            existing = {
                "owner_run_id": "<corrupt_predecessor>",
                "owner_pid": 0,
                "owner_host": {},
                "created_at": "unknown",
                "lock_version_chain": 0,
                "recovery_history": [],
            }
            corrupt_lease = True
        else:
            return LockOutcome(
                ok=False, path=path, owner=None,
                reason="no_lock_to_recover",
            )
    # After this point existing is always a dict.

    # Verify staleness one more time before reclaiming. Skip
    # this for corrupt leases — there is no liveness evidence to
    # verify; the operator is explicitly reclaiming via this
    # command, so the staleness evidence they supplied is the
    # only signal we need.
    if not corrupt_lease:
        evidence = assess_liveness(existing)
        if evidence.is_indeterminate:
            # Round-9 P1 fix: when the operator explicitly opts
            # into inline recovery (bypass_indeterminate_state),
            # allow state_path_missing, state_unreadable, and
            # similar "indeterminate because we can't read the
            # state file" cases to proceed. The operator has
            # explicitly accepted that the predecessor's state
            # file is missing or unreadable; refusing here would
            # force an unrecoverable lock. Round-10 fix: include
            # state_unreadable in the bypass set.
            #
            # Round-12 P1 fix: only bypass state_path_missing when
            # the recorded owner PID is NOT alive. A live bootstrap
            # subprocess publishing its state file means the
            # missing-state indeterminate is transient; a
            # competing --replace-stale-lock would overwrite the
            # first initializer's lease. Reject when the owner PID
            # is alive.
            if bypass_indeterminate_state and (
                (
                    "state_unreadable" in evidence.reason
                    or "state_path" in evidence.reason
                )
                and not _pid_exists(int(existing.get("owner_pid", 0) or 0))
            ):
                pass  # proceed with recovery
            else:
                return LockOutcome(
                    ok=False,
                    path=path,
                    owner=existing,
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
    sentinel_fd: Optional[int] = None
    if not bypass_sentinel:
        sentinel_fd = _acquire_sentinel_fd(sentinel_path)
    else:
        # Caller already holds the scope sentinel. Reuse it so
        # we don't deadlock against ourselves.
        sentinel_fd = external_sentinel_fd
    if sentinel_fd is None:
        # Another worker is already performing recovery. Re-read
        # the lock to report the current owner.
        current = _read_lock(path)
        return LockOutcome(
            ok=False, path=path, owner=current,
            reason="recovery_in_progress_by_other_worker",
        )
    try:
        # Inside the sentinel, re-read to detect a winner. For a
        # corrupt lease we already know the predecessor is
        # corrupt; don't re-check is_corrupt_lock here because
        # the sentinel already serializes access.
        existing2 = _read_lock(path)
        if existing2 is None:
            if corrupt_lease:
                # The corrupt file disappeared entirely. Use the
                # placeholder we constructed before the sentinel.
                existing2 = existing
            else:
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
        # live lease. Skip for corrupt leases (no liveness evidence
        # available).
        liveness_reason = "skip_for_corrupt_lease"
        if not corrupt_lease:
            live2 = assess_liveness(existing2)
            if live2.is_alive:
                return LockOutcome(
                    ok=False, path=path, owner=existing2,
                    reason="recheck_found_lock_live",
                )
            if live2.is_indeterminate:
                if bypass_indeterminate_state and (
                    (
                        "state_unreadable" in live2.reason
                        or "state_path" in live2.reason
                    )
                    and not _pid_exists(int(existing2.get("owner_pid", 0) or 0))
                ):
                    liveness_reason = live2.reason  # proceed
                else:
                    return LockOutcome(
                        ok=False, path=path, owner=existing2,
                        reason=f"recheck_indeterminate:{live2.reason}",
                        indeterminate=True,
                    )
            liveness_reason = live2.reason

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
                    "assess_liveness_reason": liveness_reason,
                }
            ],
        }
        assert_no_secrets(new_payload, context=str(path))

        tmp_path = path.with_suffix(path.suffix + ".recover.tmp")
        try:
            with safe_restrictive_open(tmp_path, "w") as f:
                json.dump(new_payload, f, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            # Round-37 P2 fix: write the sibling `.repo`
            # index for the recovered lease.
            _write_repo_index(
                path, (scope.get("repository") or "")
            )
        except OSError as e:
            return LockOutcome(
                ok=False, path=path, owner=existing2,
                reason=f"recovery_failed:{e.strerror or str(e)}",
            )
        # Round-24 P1 fix (Durably publish recovered leases
        # before returning): fsync the lock directory after the
        # os.replace that publishes the recovered lease, exactly
        # as Round-23 P1 fix does for the initial acquisition.
        # Otherwise a host crash immediately after replace can
        # leave the live inode on disk but its directory entry
        # missing — reverting to the stale predecessor and
        # allowing another initializer to acquire the same scope.
        try:
            dir_fd = os.open(
                str(path.parent), os.O_RDONLY | _posix_cloexec_flag()
            )
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except (OSError, NotImplementedError):
            pass

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

    Round-23 P2 fix (Preserve the recovery audit trail when
    releasing a lease): the previous code unlinked the only
    artifact containing recovery_history. Archive the lease to
    a sibling `<path>.released-<timestamp>` file before deleting
    it so the audit trail (previous owner run_id, supplied
    staleness evidence, recovery timestamps) survives finalization.
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
        # Round-23 P2 fix: archive the lease to a sibling file
        # before unlinking so the recovery_history audit trail
        # survives finalization. The archive is best-effort; a
        # non-POSIX host may not support the operations.
        #
        # Round-24 P2 fix (Make released archive names
        # collision-free): include microsecond precision AND
        # the owner_run_id AND a uuid suffix so two releases for
        # the same scope within the same second cannot collide.
        # The previous second-level timestamp only allowed the
        # second release's os.replace to silently overwrite the
        # first archive, losing the audit trail.
        try:
            import datetime as _dt
            import uuid as _uuid
            ts = _dt.datetime.now(_dt.timezone.utc).strftime(
                "%Y%m%dT%H%M%S%fZ"
            )
            # Sanitize owner_run_id for filename safety.
            safe_owner = "".join(
                ch if ch.isalnum() or ch in "-_" else "_"
                for ch in owner_run_id
            )
            archive_name = (
                f"{path.name}.released-"
                f"{ts}-{safe_owner}-{_uuid.uuid4().hex[:8]}"
            )
            archive_path = path.with_name(archive_name)
            # Move (rename) is atomic on POSIX; if the rename
            # fails (cross-device, etc.) fall back to copy+unlink.
            try:
                os.replace(str(path), str(archive_path))
            except OSError:
                try:
                    import shutil as _shutil
                    _shutil.copy2(str(path), str(archive_path))
                    path.unlink()
                except OSError:
                    # If archiving fails entirely, still unlink
                    # the live lease (release must succeed) but
                    # log a warning. The audit trail is lost.
                    path.unlink()
            # Round-26 P2 fix (Fsync the lock directory after
            # archiving a released lease): the rename + unlink
            # sequence has not been made durable because the
            # lock directory is never fsynced. After reboot
            # the old live lease name can reappear, causing
            # subsequent initialization to report a stale-lock
            # conflict despite successful finalization, or the
            # promised released audit archive can be absent.
            # fsync the parent directory before returning.
            try:
                dir_fd = os.open(
                    str(path.parent), os.O_RDONLY | _posix_cloexec_flag()
                )
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except (OSError, NotImplementedError, AttributeError):
                pass
            # Round-37 P2 fix: clean up the sibling `.repo`
            # index file alongside the archived lease.
            try:
                os.unlink(_repo_index_path(path))
            except OSError:
                pass
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
    held, sentinel_fd, sentinel_path = check_lease_held_keeping_sentinel(
        scope=scope, owner_run_id=owner_run_id, base_dir=base_dir,
    )
    if sentinel_fd is not None:
        _release_sentinel_fd(sentinel_fd, sentinel_path)
    return held


def check_lease_held_keeping_sentinel(
    *,
    scope: dict,
    owner_run_id: str,
    base_dir: Optional[Path] = None,
) -> Tuple[bool, Optional[int], Optional[Path]]:
    """Same as is_lease_held_by_run but returns the sentinel fd
    so the caller can keep it held through a subsequent
    authorize-mutation. The caller is responsible for
    releasing the sentinel when done.

    Returns (held, sentinel_fd, sentinel_path). If held is
    True, sentinel_fd is non-None and must be released. If
    held is False, sentinel_fd may still be non-None (the
    sentinel was acquired to perform the check) and must
    also be released.

    Round-34 P1 fix (Hold the scope sentinel through
    authorization): the previous is_lease_held_by_run
    released the recovery sentinel before the caller
    called _mutation_auth.authorize. A concurrent
    recover_stale could transfer the lease in that gap,
    allowing this invocation to continue using its
    previously-loaded active state and durably authorize a
    mutation for the former owner. By keeping the
    sentinel held through the journal append, the
    authorize-mutation and recover_stale paths become
    mutually exclusive — exactly the property the
    recovery sentinel was designed to provide.
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
        return False, None, sentinel_path
    existing = _read_lock(path)
    if existing is None:
        _release_sentinel_fd(sentinel_fd, sentinel_path)
        return False, None, None
    if existing.get("owner_run_id") != owner_run_id:
        _release_sentinel_fd(sentinel_fd, sentinel_path)
        return False, None, None
    live = assess_liveness(existing)
    if live.is_indeterminate:
        _release_sentinel_fd(sentinel_fd, sentinel_path)
        return False, None, None
    if not live.is_alive:
        _release_sentinel_fd(sentinel_fd, sentinel_path)
        return False, None, None
    # Held — return the sentinel so the caller can keep it
    # held through authorize-mutation.
    return True, sentinel_fd, sentinel_path


def read(path: Path) -> Optional[dict]:
    """Read the current lock for inspection."""
    return _read_lock(path)


def assess_from_path(path: Path) -> Optional[LivenessEvidence]:
    existing = _read_lock(path)
    if existing is None:
        return None
    return assess_liveness(existing)