"""Tests for engine/edge_discovery/_file_lock.py.

Verifies:
1. msvcrt.unlock is NOT called (static check).
2. LK_UNLCK IS used in Windows unlock path.
3. Retry loop raises TimeoutError (not ValueError from negative sleep).
4. time.sleep is never called with a negative argument.
5. Lock acquisition calls and unlock calls are made correctly.
6. All existing registry/ledger concurrency tests still pass.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import IO
from unittest import mock

import fcntl
import pytest


# ---------------------------------------------------------------------------
# Helper — load _file_lock in isolation so tests can patch it cleanly
# ---------------------------------------------------------------------------

def _import_lock_helper() -> type:
    """Import _file_lock by file path and return the module."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_file_lock",
        Path(__file__).parents[1] / "engine" / "edge_discovery" / "_file_lock.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Static source-code checks
# ---------------------------------------------------------------------------

class TestFileLockSourceStatic:
    """Static checks on the source file — no runtime mocking needed."""

    def test_source_does_not_call_msvcrt_unlock(self):
        """The source must not call the non-existent msvcrt.unlock()."""
        src = Path(__file__).parents[1] / "engine" / "edge_discovery" / "_file_lock.py"
        content = src.read_text()
        assert "msvcrt.unlock(" not in content, (
            "msvcrt.unlock() does not exist — "
            "Windows unlock must use msvcrt.locking(fd, LK_UNLCK, 1)"
        )

    def test_source_uses_lk_unlck_for_windows_unlock(self):
        """Windows _unlock_windows must use msvcrt.locking with LK_UNLCK."""
        src = Path(__file__).parents[1] / "engine" / "edge_discovery" / "_file_lock.py"
        content = src.read_text()
        assert "LK_UNLCK" in content, (
            "Windows unlock must use msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)"
        )


# ---------------------------------------------------------------------------
# POSIX retry-loop tests (run on any platform, real flock mocked)
# ---------------------------------------------------------------------------

class TestFileLockPOSIXRetryLoop:
    """POSIX code-path retry/timeout behavior — mocked on any platform."""

    def test_posix_timeout_raises_timeout_error_not_value_error(self, tmp_path):
        """Deadline expiry must raise TimeoutError, not ValueError.

        Regression: previous code computed time.sleep(min(0.05, deadline - now))
        without checking remaining <= 0 first.  Fixed by checking the deadline
        before calling sleep.
        """
        mod = _import_lock_helper()
        lock_file = tmp_path / "lock"
        fh = lock_file.open("w")

        call_count = [0]

        def monotonic_sequences():
            # Returns 0.0 first (deadline = 0.1), then 10.0, 20.0 … which is
            # already past deadline on the second call → immediate TimeoutError.
            t = call_count[0]
            call_count[0] += 1
            return t * 10.0

        with mock.patch.object(fcntl, "flock", side_effect=BlockingIOError("mocked")):
            with mock.patch("time.monotonic", side_effect=monotonic_sequences):
                with pytest.raises(TimeoutError, match="Could not acquire exclusive lock"):
                    mod._lock_posix(fh, timeout=0.1)

        fh.close()

    def test_posix_sleep_never_gets_negative_value(self, tmp_path):
        """time.sleep must never be called with a negative argument."""
        mod = _import_lock_helper()
        lock_file = tmp_path / "lock"
        fh = lock_file.open("w")
        sleep_args = []

        def tracking_sleep(seconds):
            sleep_args.append(seconds)
            assert seconds >= 0, f"time.sleep received negative value: {seconds}"

        call_count = [0]

        def monotonic_sequences():
            # Returns 0.0 first (deadline = 0.1), then advances 0.05 s per call.
            # This lets sleep be called several times before deadline is reached.
            # Loop: remaining = 0.1 - t*0.05; stops when remaining <= 0.
            t = call_count[0]
            call_count[0] += 1
            return t * 0.05

        with mock.patch.object(fcntl, "flock", side_effect=BlockingIOError("mocked")):
            with mock.patch("time.monotonic", side_effect=monotonic_sequences):
                with mock.patch("time.sleep", side_effect=tracking_sleep):
                    with pytest.raises(TimeoutError):
                        mod._lock_posix(fh, timeout=0.1)

        fh.close()
        assert len(sleep_args) >= 1, "time.sleep should have been called at least once"
        assert all(s >= 0 for s in sleep_args), (
            f"All sleep args must be non-negative: {sleep_args}"
        )

    def test_posix_unlock_calls_flock_with_lock_unlock(self, tmp_path):
        """On exit the context manager must call fcntl.flock(fd, LOCK_UN)."""
        mod = _import_lock_helper()
        lock_file = tmp_path / "lock"
        fh = lock_file.open("w")
        flock_calls = []

        original_flock = fcntl.flock

        def tracking_flock(fd, flags):
            flock_calls.append((fd, flags))
            return original_flock(fd, flags)

        with mock.patch.object(fcntl, "flock", side_effect=tracking_flock):
            with mod.exclusive_file_lock(fh):
                pass  # lock acquired; finally block releases it while still mocked

        fh.close()
        unlock_calls = [(fd, f) for fd, f in flock_calls if f == fcntl.LOCK_UN]
        assert len(unlock_calls) == 1, (
            f"LOCK_UN must be called exactly once. Calls: {flock_calls}"
        )


# ---------------------------------------------------------------------------
# Windows retry-loop tests (mocked on any platform via sys.platform + sys.modules)
# ---------------------------------------------------------------------------

class TestFileLockWindowsRetryLoopMocked:
    """Windows code-path tests — run on any platform via mocking.

    exclusive_file_lock checks sys.platform at call time.  By patching
    sys.platform = "win32" we force the Windows code path.  The msvcrt
    import is intercepted via sys.modules so no real Windows CRT is needed.
    """

    def test_windows_timeout_raises_timeout_error_not_value_error(self, tmp_path):
        """Deadline expiry must raise TimeoutError, not ValueError."""
        import types

        fake_msvcrt = types.ModuleType("msvcrt")
        fake_msvcrt.LK_NBLCK = 16
        fake_msvcrt.LK_UNLCK = 2
        fake_msvcrt.locking = mock.Mock(side_effect=OSError("mocked"))

        call_count = [0]

        def monotonic_sequences():
            t = call_count[0]
            call_count[0] += 1
            return t * 10.0  # 0.0 → deadline=0.1; 10.0 → remaining=-9.9 → TimeoutError

        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                mod = _import_lock_helper()  # must be inside mocks so it sees fake msvcrt
                lock_file = tmp_path / "lock"
                fh = lock_file.open("w")
                with mock.patch("time.monotonic", side_effect=monotonic_sequences):
                    with pytest.raises(TimeoutError, match="Could not acquire exclusive lock"):
                        mod._lock_windows(fh, timeout=0.1)
                fh.close()

    def test_windows_sleep_never_gets_negative_value(self, tmp_path):
        """time.sleep must never be called with a negative argument on the Windows path."""
        import types

        fake_msvcrt = types.ModuleType("msvcrt")
        fake_msvcrt.LK_NBLCK = 16
        fake_msvcrt.LK_UNLCK = 2
        fake_msvcrt.locking = mock.Mock(side_effect=OSError("mocked"))

        sleep_args = []

        def tracking_sleep(seconds):
            sleep_args.append(seconds)
            assert seconds >= 0, f"time.sleep received negative value: {seconds}"

        call_count = [0]

        def monotonic_sequences():
            # 0.0 → deadline=0.1; advances 0.05 s per call → several sleeps before deadline
            t = call_count[0]
            call_count[0] += 1
            return t * 0.05

        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                mod = _import_lock_helper()  # must be inside mocks
                lock_file = tmp_path / "lock"
                fh = lock_file.open("w")
                with mock.patch("time.monotonic", side_effect=monotonic_sequences):
                    with mock.patch("time.sleep", side_effect=tracking_sleep):
                        with pytest.raises(TimeoutError):
                            mod._lock_windows(fh, timeout=0.1)
                fh.close()
        assert len(sleep_args) >= 1, "time.sleep should have been called at least once"
        assert all(s >= 0 for s in sleep_args), (
            f"All sleep args must be non-negative: {sleep_args}"
        )

    def test_windows_unlock_calls_locking_with_lk_unlck(self, tmp_path):
        """_unlock_windows must call msvcrt.locking(fd, LK_UNLCK, 1)."""
        import types

        fake_msvcrt = types.ModuleType("msvcrt")
        fake_msvcrt.LK_NBLCK = 16
        fake_msvcrt.LK_UNLCK = 2
        locking_calls = []

        def fake_locking(fd, mode, nbytes):
            locking_calls.append((fd, mode, nbytes))
            return 0

        fake_msvcrt.locking = fake_locking

        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                mod = _import_lock_helper()  # must be inside mocks
                lock_file = tmp_path / "lock"
                fh = lock_file.open("w")
                fd = fh.fileno()
                with mod.exclusive_file_lock(fh):
                    pass  # acquire then exit → _unlock_windows called in finally
                fh.close()

        # Verify exactly one LK_UNLCK call
        unlock_calls = [
            (fd, mode, nbytes)
            for fd, mode, nbytes in locking_calls
            if mode == fake_msvcrt.LK_UNLCK
        ]
        assert len(unlock_calls) == 1, (
            f"Must call msvcrt.locking with LK_UNLCK exactly once. "
            f"Calls: {locking_calls}"
        )
        assert unlock_calls[0][0] == fd, (
            f"LK_UNLCK must be called on fd={fd}. "
            f"Calls: {locking_calls}"
        )
