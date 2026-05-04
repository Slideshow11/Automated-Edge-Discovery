"""Cross-platform exclusive file lock helper.

Provides exclusive write locking for JSONL append operations.
Uses fcntl.flock on POSIX (Linux/WSL/macOS) and msvcrt on Windows.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import IO

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


@contextmanager
def exclusive_file_lock(fh: IO, timeout: float = 10.0):
    """Acquire an exclusive (write) lock on an already-open file handle.

    Parameters
    ----------
    fh : file handle
        An already-opened file handle (as returned by ``open()``).
    timeout : float, default 10.0
        Maximum seconds to wait for the lock.  Raises TimeoutError if
        the lock cannot be acquired within this window.

    Yields
    ------
    The file handle, with the exclusive lock held.

    Notes
    -----
    * On POSIX (Linux/WSL/macOS) this maps to ``fcntl.flock(fh, LOCK_EX)``.
    * On Windows this maps to ``msvcrt.locking(fh, LK_NBLCK, ...``) over the
      entire file size.  The handle must have been opened with at least
      read access (``"r"``, ``"r+"``, ``"a"``, etc. — any mode that opens
      the file for I/O).
    * The lock is automatically released when the context manager exits,
      including on exception.
    * The lock is *advisory* — co-operating processes that all use this
      helper will not interfere, but a rogue process that ignores advisory
      locks can still write.
    * This helper does **not** call ``os.fsync()``; callers that require
      crash-durability should flush and sync after the write if needed.

    Raises
    ------
    TimeoutError
        If the lock cannot be acquired within ``timeout`` seconds.
    """
    if sys.platform == "win32":
        _lock_windows(fh, timeout)
    else:
        _lock_posix(fh, timeout)
    try:
        yield fh
    finally:
        if sys.platform == "win32":
            _unlock_windows(fh)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# POSIX (Linux / macOS / WSL)
# ---------------------------------------------------------------------------

def _lock_posix(fh: IO, timeout: float) -> None:
    assert sys.platform != "win32"
    import time

    deadline = time.monotonic() + timeout
    fd = fh.fileno()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            elapsed = time.monotonic() - deadline
            if elapsed >= 0:
                raise TimeoutError(f"Could not acquire exclusive lock within {timeout}s")
            # Sleep a short interval before retrying
            time.sleep(min(0.05, deadline - time.monotonic()))


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

def _lock_windows(fh: IO, timeout: float) -> None:
    assert sys.platform == "win32"
    import time

    # msvcrt.locking(LK_NBLCK) locks a region; we lock the whole file by
    # seeking to end to get file size, then locking that many bytes.
    fh.seek(0, 2)  # seek to end
    file_size = fh.tell()
    fh.seek(0)

    deadline = time.monotonic() + timeout
    while True:
        try:
            # Lock 1-byte region at offset 0 — exclusive advisory lock
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:  # lock held
            elapsed = time.monotonic() - deadline
            if elapsed >= 0:
                raise TimeoutError(f"Could not acquire exclusive lock within {timeout}s")
            time.sleep(min(0.05, deadline - time.monotonic()))


def _unlock_windows(fh: IO) -> None:
    assert sys.platform == "win32"
    try:
        msvcrt.unlock(fh.fileno())
    except OSError:
        # If the region is not locked (e.g. process exiting), ignore.
        pass
