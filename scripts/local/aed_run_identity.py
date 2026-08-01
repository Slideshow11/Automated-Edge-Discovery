#!/usr/bin/env python3
"""
aed_run_identity.py

Captures the auditable identity of a single controller run.

Provides:
  - capture_run_identity(): build an immutable, JSON-serializable identity
    record including run_id, repository, target PR number, controller
    version, host, PID, process-start identity, current main SHA,
    starting target SHA, creation timestamp, current phase, pending
    action, and merge policy.
  - capture_process_start_evidence(): record PID + /proc/<pid>/stat
    start_time field + ctime evidence sufficient to detect PID reuse on
    Linux. On non-Linux platforms, returns None and callers must
    treat liveness as indeterminate.
  - safe_restrictive_open(): open a file with restrictive permissions
    (0o600 on POSIX; refuses to write secrets-bearing content via
    the assert_no_secrets check).
  - assert_no_secrets(payload, *, context=""): refuse to persist
    strings that contain known secret-bearing patterns (tokens,
    api keys, passwords, secret-bearing command lines, etc.).
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# Patterns considered sensitive. These are intentionally conservative.
# Match any string that looks like a token / key / bearer / password.
# Also matches command-line invocations that contain secrets.
_SECRET_PATTERNS = [
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"(?i)\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bghs_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bgho_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bghr_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bsk_live_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bsk_test_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)password\s*=\s*[^\s\"']{6,}"),
    re.compile(r"(?i)api[_-]?key\s*=\s*[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)secret\s*=\s*[A-Za-z0-9._\-]{12,}"),
    # Long hex/base64-looking strings (>=40 chars) inside an
    # environment variable assignment — likely a token/key.
    re.compile(r"(?i)(?:token|secret|key)=[A-Za-z0-9._\-]{40,}"),
    # Long URL-encoded access tokens (e.g., ?token=..., &access_token=...)
    re.compile(r"(?i)(?:access_)?token=[A-Za-z0-9._\-%]{20,}"),
]

# Command-line tokens that indicate a credential was on the command line.
_SECRET_ARGV_TOKENS = frozenset([
    "--token",
    "--api-key",
    "--api_key",
    "--secret",
    "--password",
    "--passwd",
    "-p",  # mysql/psql style
])


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def capture_process_start_evidence(pid: Optional[int] = None) -> Optional[dict]:
    """
    Return evidence sufficient to detect PID reuse on Linux.

    On Linux, reads /proc/<pid>/stat to capture the kernel-space
    start_time field (jiffies since boot). Also returns the proc's
    ctime (inode change time, ~ process creation) when available.
    On other platforms, returns None and callers must treat liveness
    as indeterminate.

    Returns a dict with keys:
      pid (int)
      stat_start_time (int | None)   # kernel jiffies at exec
      stat_start_time_text (str)     # raw field 22 from /proc/<pid>/stat
      ctime_ns (int | None)          # process ctime in nanoseconds, when available
      source (str)                   # "linux_proc" or "unknown"
    """
    if pid is None:
        pid = os.getpid()

    if not _is_linux():
        return {
            "pid": pid,
            "stat_start_time": None,
            "stat_start_time_text": None,
            "ctime_ns": None,
            "source": "unknown",
        }

    try:
        with open(f"/proc/{pid}/stat") as f:
            stat_content = f.read()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return {
            "pid": pid,
            "stat_start_time": None,
            "stat_start_time_text": None,
            "ctime_ns": None,
            "source": "linux_proc_unreadable",
        }

    # /proc/<pid>/stat format: pid (comm) state ppid ...
    # The comm field may contain spaces or parens, so find the LAST ')'.
    last_paren = stat_content.rfind(")")
    if last_paren < 0:
        return {
            "pid": pid,
            "stat_start_time": None,
            "stat_start_time_text": None,
            "ctime_ns": None,
            "source": "linux_proc_malformed",
        }
    fields = stat_content[last_paren + 2 :].split()
    # Field index 21 (zero-based) is start_time in jiffies after boot.
    # In /proc/<pid>/stat the fields after the comm are numbered from 0
    # in the list above; the kernel's start_time (1-indexed field 22)
    # is field index 19 in the zero-based list of fields after ')'.
    # (fields[0] = state = 1-indexed field 3; so field 22 = fields[19].)
    stat_start_time_text = fields[19] if len(fields) > 19 else None
    stat_start_time_int: Optional[int] = None
    if stat_start_time_text is not None:
        try:
            stat_start_time_int = int(stat_start_time_text)
        except ValueError:
            pass

    # ctime: best effort via os.stat on /proc/<pid>
    ctime_ns: Optional[int] = None
    try:
        st = os.stat(f"/proc/{pid}")
        ctime_ns = int(st.st_ctime_ns) if hasattr(st, "st_ctime_ns") else int(st.st_ctime * 1_000_000_000)
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        pass

    return {
        "pid": pid,
        "stat_start_time": stat_start_time_int,
        "stat_start_time_text": stat_start_time_text,
        "ctime_ns": ctime_ns,
        "source": "linux_proc",
    }


def capture_host_identity() -> dict:
    """Capture host identity (hostname, fqdn if resolvable, platform)."""
    hostname = socket.gethostname()
    fqdn: Optional[str] = None
    try:
        fqdn = socket.getfqdn()
        if fqdn == hostname:
            fqdn = None
    except (OSError, socket.gaierror):
        fqdn = None

    return {
        "hostname": hostname,
        "fqdn": fqdn,
        "platform": sys.platform,
        "python_version": sys.version.split()[0],
    }


def capture_run_identity(
    *,
    run_id: str,
    controller_version: int,
    repository: Optional[str] = None,
    target_pr_number: Optional[int] = None,
    current_main_sha: Optional[str] = None,
    starting_target_sha: Optional[str] = None,
    current_phase: str = "INIT",
    pending_action: str = "init",
    merge_policy: str = "stop_before_merge",
) -> dict:
    """
    Build a complete, JSON-serializable run identity record.

    All fields are recorded with explicit None sentinels for unknowns.
    """
    now = _utcnow()
    proc_evidence = capture_process_start_evidence()

    return {
        "run_id": run_id,
        "created_at": now,
        "controller_version": controller_version,
        "repository": repository,
        "target_pr_number": target_pr_number,
        "current_main_sha": current_main_sha,
        "starting_target_sha": starting_target_sha,
        "current_phase": current_phase,
        "pending_action": pending_action,
        "merge_policy": merge_policy,
        "host": capture_host_identity(),
        "process": proc_evidence,
    }


def assert_no_secrets(payload: Any, *, context: str = "") -> None:
    """
    Raise ValueError if `payload` contains any string that looks like
    a secret. Walks dicts and lists recursively.

    Used to gate persistence of state, lock, and receipt files.
    """
    violations = _scan_for_secrets(payload, path=context)
    if violations:
        joined = "; ".join(violations[:5])
        raise ValueError(
            f"Refusing to persist content with secret-like patterns "
            f"in {context!r}: {joined}"
        )


def _scan_for_secrets(payload: Any, *, path: str) -> list[str]:
    violations: list[str] = []

    def walk(node: Any, trail: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{trail}.{k}" if trail else str(k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{trail}[{i}]")
        elif isinstance(node, str):
            for pat in _SECRET_PATTERNS:
                if pat.search(node):
                    violations.append(f"{trail}: matched pattern {pat.pattern!r}")
                    break

    walk(payload, path)
    return violations


def assert_no_secrets_in_argv(argv: Optional[list[str]] = None, *, context: str = "") -> None:
    """
    Raise ValueError if any argv element is a known secret-bearing
    command-line token or contains a secret pattern.
    """
    if argv is None:
        argv = sys.argv
    violations: list[str] = []
    for i, tok in enumerate(argv):
        if tok in _SECRET_ARGV_TOKENS:
            # Check whether the next arg looks secret-like
            if i + 1 < len(argv):
                nxt = argv[i + 1]
                for pat in _SECRET_PATTERNS:
                    if pat.search(nxt):
                        violations.append(f"{context}: argv[{i+1}] follows {tok!r}")
                        break
        for pat in _SECRET_PATTERNS:
            if pat.search(tok):
                violations.append(f"{context}: argv[{i}] matches {pat.pattern!r}")
                break
    if violations:
        joined = "; ".join(violations[:5])
        raise ValueError(
            f"Refusing to persist argv with secret-like patterns "
            f"in {context!r}: {joined}"
        )


def safe_restrictive_open(path: Path, mode: str = "w"):
    """
    Open `path` for writing with restrictive permissions (0o600 on
    POSIX). Creates parent dirs with 0o700.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Round-23 P2 fix (Create private files restrictively on
    # every POSIX host): the previous code used the safe
    # os.open(..., 0o600) + fchmod path only when _is_linux()
    # returned True. On macOS, FreeBSD, and other POSIX systems
    # the function fell through to plain `open(path, mode)`
    # followed by os.chmod — which creates the file using the
    # process umask (typically 022) before the chmod call. A
    # crash between the open and the chmod leaves the file
    # world-readable, leaking the launch receipt / lock
    # contents. Use the restrictive os.open(..., 0o600) path
    # for ALL POSIX platforms (the Linux-vs-other branch was
    # only there because Windows / non-POSIX needs the
    # fallback). The Linux `_is_linux()` was effectively a
    # proxy for "is POSIX + supports fchmod", which is true
    # on macOS and FreeBSD too.
    if os.name == "posix":
        # Write atomically with explicit 0o600 mode.
        # Round-8 P2 fix: on POSIX, when the file already
        # exists with broader permissions (e.g. 0o644 left
        # over from a prior partial write), `O_CREAT` with
        # mode 0o600 does NOT restrict an existing file's
        # mode — the existing inode keeps its current
        # permissions. Explicitly chmod via the open fd to
        # 0o600 so re-opening an existing file cannot leak
        # the supposedly private contents.
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o600)
        try:
            os.fchmod(fd, 0o600)
        except (OSError, NotImplementedError):
            pass
        return os.fdopen(fd, mode)
    # Fallback for non-POSIX (Windows): write then chmod.
    f = open(path, mode)
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass
    return f


def write_restrictive_json(path: Path, payload: Any) -> None:
    """Serialize and write JSON with restrictive permissions; checks for secrets first."""
    assert_no_secrets(payload, context=str(path))
    with safe_restrictive_open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        # Round-27 P1 fix (Durably publish the machine launch
        # receipt): fsync the file descriptor before
        # closing. The write_restrictive_json path is used by
        # the launch receipt's write_machine_readable, and
        # authorize-mutation later requires a readable
        # LAUNCH_RECEIPT.json to gate mutations. If the file
        # does not survive a host crash, the recovered state
        # cannot authorize any mutation and there is no
        # command to recreate the receipt without
        # reinitializing the run. Fsync the descriptor here
        # so the file's contents are durable on disk.
        os.fsync(f.fileno())


def file_mode(path: Path) -> Optional[int]:
    """Return the file's permission bits, or None if not statable."""
    try:
        return Path(path).stat().st_mode & 0o777
    except (FileNotFoundError, OSError):
        return None


def posix_cloexec_flag() -> int:
    """Return os.O_CLOEXEC when available, else 0.

    Round-28 P2 fix (Build mutation-journal flags
    conditionally on Windows): the mutation journal's
    _append_record and _rewrite_record open sites use
    os.O_CLOEXEC unconditionally, which raises
    AttributeError on Windows. The controller's _save_state
    and the supervisor lock's sentinel acquisitions already
    use this same helper pattern. Add it to aed_run_identity
    so aed_mutation_authorization can import it.
    """
    return getattr(os, "O_CLOEXEC", 0)