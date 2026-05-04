"""Cross-platform exclusive file lock helper.

Provides exclusive write locking for JSONL append operations.
Uses fcntl.flock on POSIX (Linux/WSL/macOS) and msvcrt on Windows.
"""

from __future__ import annotations

import fcntl
import sys
import time
from contextlib import contextmanager
from typing import IO

__all__ = ["exclusive_file_lock"]

# Always import fcntl (POSIX).
import fcntl as _fcntl

# msvcrt is needed by _lock_windows / _unlock_windows which are defined
# unconditionally below (so tests can call them on any platform).
# On Windows: normal import. On Linux/non-Windows: try import, fall back to None.
# If None, calling _lock_windows on Linux raises a clear RuntimeError.
if sys.platform == "win32":
    import msvcrt as _msvcrt
else:
    # On non-Windows, try to import msvcrt (may be patched into sys.modules
    # by tests). If not present, fall back to None so _lock_windows raises a
    # clear RuntimeError instead of a cryptic NameError.
    try:
        import msvcrt as _msvcrt
    except (ImportError, ModuleNotFoundError):
        _msvcrt = sys.modules.get("msvcrt")


# ---------------------------------------------------------------------------
# POSIX (Linux / macOS / WSL)
# ---------------------------------------------------------------------------

def _lock_posix(fh: IO, timeout: float) -> None:
    """Acquire exclusive non-blocking lock with retry loop on POSIX systems."""
    deadline = time.monotonic() + timeout
    fd = fh.fileno()
    while True:
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            return
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Could not acquire exclusive lock within {timeout}s")
            time.sleep(min(0.05, remaining))


def _unlock_posix(fh: IO) -> None:
    """Release exclusive lock on POSIX systems."""
    _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Windows (msvcrt)
# ---------------------------------------------------------------------------

def _lock_windows(fh: IO, timeout: float) -> None:
    """Acquire exclusive non-blocking lock with retry loop on Windows."""
    if _msvcrt is None:
        raise RuntimeError("msvcrt is not available on this platform")
    deadline = time.monotonic() + timeout
    fd = fh.fileno()
    while True:
        try:
            _msvcrt.locking(fd, _msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Could not acquire exclusive lock within {timeout}s")
            time.sleep(min(0.05, remaining))


def _unlock_windows(fh: IO) -> None:
    """Release exclusive lock on Windows using msvcrt.locking."""
    if _msvcrt is None:
        raise RuntimeError("msvcrt is not available on this platform")
    _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@contextmanager
def exclusive_file_lock(fh: IO, timeout: float = 10.0):
    """Context manager: acquire exclusive lock, release on exit."""
    if sys.platform == "win32":
        _lock_windows(fh, timeout=timeout)
    else:
        _lock_posix(fh, timeout=timeout)
    try:
        yield fh
    finally:
        if sys.platform == "win32":
            _unlock_windows(fh)
        else:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
