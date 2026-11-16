"""Cross-process file locks (``flock``), re-entrant within the process.

``path_lock`` returns one ``PathLock`` per resolved path, so nested ``hold`` calls of
the same thread do not deadlock. The trace DB, backlog writes, the backlog database and
follow-up publication use it. A lock that cannot be taken in time raises
``TaskRunError("trace_db_locked")``.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from aifactory import oscompat
from aifactory.errors import TaskRunError


class PathLock:
    """A cross-process ``flock`` on one file, re-entrant within the process."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.local = threading.RLock()
        self.depth = 0
        self.fd: int | None = None

    @contextlib.contextmanager
    def hold(self, timeout: float) -> Iterator[None]:
        deadline = time.monotonic() + timeout
        if not self.local.acquire(timeout=timeout):
            raise TaskRunError("trace_db_locked", f"{self.path} is held by another thread")
        try:
            if self.depth == 0:
                self.fd = self._lock(deadline)
            self.depth += 1
            try:
                yield
            finally:
                self.depth -= 1
                if self.depth == 0 and self.fd is not None:
                    with contextlib.suppress(OSError):
                        oscompat.unlock(self.fd)
                    os.close(self.fd)
                    self.fd = None
        finally:
            self.local.release()

    def _lock(self, deadline: float) -> int:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        while True:
            try:
                oscompat.lock(fd, blocking=False)
                return fd
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise TaskRunError(
                        "trace_db_locked", f"{self.path} is held by another process"
                    ) from None
                time.sleep(0.05)


_PATH_LOCKS: dict[str, PathLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def path_lock(path: Path) -> PathLock:
    """The one ``PathLock`` of ``path`` in this process."""
    key = str(path.resolve())
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = _PATH_LOCKS[key] = PathLock(path)
        return lock
