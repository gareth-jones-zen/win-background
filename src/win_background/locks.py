"""Locks between processes: one watcher at a time, and one run at a time.

On Windows these are named mutexes in the session's Local namespace. Windows releases a mutex
when the process holding it dies, so a crash can never leave a lock stuck, which a lock file can.
Elsewhere they're file locks (fcntl), which the OS also releases on exit.

Two names that are equal hold the same lock across every tool, so name locks after the thing
they protect: `key()` turns a path (a settings file, a browser profile) into a short stable name.
"""

from __future__ import annotations

import contextlib
import hashlib
import pathlib
import sys
import tempfile
import time

WAIT_OBJECT_0, WAIT_ABANDONED = 0, 0x80


def key(text: str | pathlib.Path) -> str:
    """16 hex characters from a path or other text, ignoring case (Windows paths)."""
    return hashlib.sha256(str(text).lower().encode()).hexdigest()[:16]


class Lock:
    """A named lock. acquire(timeout) waits up to timeout seconds; 0 means try once."""

    def __init__(self, name: str, folder: pathlib.Path | None = None):
        self.name = name
        self.folder = folder or pathlib.Path(tempfile.gettempdir())   # for the file lock, off Windows
        self.handle = None
        self.file = None

    def acquire(self, timeout: float = 0) -> bool:
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            self.handle = kernel32.CreateMutexW(None, False, f"Local\\{self.name}")
            result = kernel32.WaitForSingleObject(ctypes.c_void_p(self.handle), int(timeout * 1000))
            if result in (WAIT_OBJECT_0, WAIT_ABANDONED):   # abandoned: the last holder died, so it's ours
                return True
            kernel32.CloseHandle(ctypes.c_void_p(self.handle))
            self.handle = None
            return False
        import fcntl
        self.folder.mkdir(parents=True, exist_ok=True)
        self.file = open(self.folder / f"{self.name}.lock", "w")
        deadline = time.time() + timeout
        while True:
            try:
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                if time.time() >= deadline:
                    self.file.close()
                    self.file = None
                    return False
                time.sleep(min(1, timeout) or 0.1)

    def release(self) -> None:
        if self.handle is not None:
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.ReleaseMutex(ctypes.c_void_p(self.handle))
            kernel32.CloseHandle(ctypes.c_void_p(self.handle))
            self.handle = None
        if self.file is not None:
            self.file.close()
            self.file = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


def single_instance(name: str, folder: pathlib.Path | None = None) -> Lock | None:
    """The lock for a long-running process, such as a watcher: held until it exits or releases it.
    None if another process already holds it. Keep the returned object alive (assign it)."""
    lock = Lock(name, folder)
    return lock if lock.acquire(0) else None


@contextlib.contextmanager
def exclusive(name: str, timeout: float = 600, folder: pathlib.Path | None = None, what: str = "run"):
    """One at a time: waits up to timeout seconds for the lock, then raises TimeoutError."""
    lock = Lock(name, folder)
    if not lock.acquire(timeout):
        raise TimeoutError(f"Another {what} didn't finish within {timeout / 60:.0f} minutes.")
    try:
        yield lock
    finally:
        lock.release()
