"""The connection, transactions and locks the trace store's repositories share."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from aifactory import database, oscompat
from aifactory.engine.tracer import BUSY_TIMEOUT
from aifactory.engine.utils import now_iso
from aifactory.errors import TaskRunError
from aifactory.locks import path_lock
from aifactory.runstore.schema import _ensure_wal, ensure


def _detail_json(detail: dict[str, Any]) -> str:
    """A failure detail as stored in ``task_runs.failure_detail``."""
    return json.dumps(detail, ensure_ascii=False, default=str)


def _now() -> str:
    return str(now_iso())


def _marks(values: list[str]) -> str:
    return ", ".join("?" for _ in values)


def _alive(pid: int | None) -> bool:
    if pid is None:
        return True  # unknown owner: never assume it is gone
    return oscompat.alive(pid)


SERIAL_TIMEOUT = 60.0  # seconds to wait for the lock file of ``serialized``


def is_db_busy(exc: BaseException) -> bool:
    """True for SQLite's "database is locked/busy" and for a lock wait that ran out."""
    if isinstance(exc, TaskRunError):
        return exc.code == "trace_db_locked"
    if isinstance(exc, sqlite3.OperationalError):
        text = str(exc).lower()
        return "locked" in text or "busy" in text
    return False


class StoreBase:
    """One sqlite connection to the trace DB, its transactions and lock files."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = database.connect(str(db_path), isolation_level=None, timeout=BUSY_TIMEOUT)
        self.conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT * 1000)};")
        _ensure_wal(self.conn, db_path)
        ensure(self.conn, db_path)

    def owned_here(self, id: str) -> bool:
        if not database.is_remote(self.db_path):
            return True
        owner = self.conn.execute("SELECT machine FROM giro_owners WHERE id = ?", (id,)).fetchone()
        return owner is not None and owner[0] == database.MACHINE

    def _own(self, id: str) -> None:
        if database.is_remote(self.db_path):
            self.conn.execute(
                "INSERT OR REPLACE INTO giro_owners VALUES (?, ?)", (id, database.MACHINE)
            )

    @contextlib.contextmanager
    def _txn(self) -> Iterator[None]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    @contextlib.contextmanager
    def serialized(self) -> Iterator[None]:
        """Hold the lock file next to the trace DB: one process at a time in the block.

        No DB transaction stays open in the block, so slow work (git) inside it
        does not keep other runs or the dashboard from writing the trace DB.
        """
        lock = path_lock(self.db_path.with_name(self.db_path.name + ".lock"))
        with lock.hold(SERIAL_TIMEOUT):
            yield

    @contextlib.contextmanager
    def merge_lock(self, timeout: float = 600.0) -> Iterator[None]:
        """Hold ``<trace db>.merge.lock``: one auto-merge into base at a time, across processes."""
        lock = path_lock(self.db_path.with_name(self.db_path.name + ".merge.lock"))
        with lock.hold(timeout):
            yield

    def _has_table(self, name: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        return row is not None

    def close(self) -> None:
        self.conn.close()
