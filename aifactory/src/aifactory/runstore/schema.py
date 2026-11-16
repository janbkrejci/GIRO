"""The trace store's tables, their column lists and the migration of older trace DBs."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from aifactory import database
from aifactory.errors import TaskRunError
from aifactory.runstore.changes import install as install_changes

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_operations (
  operation_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  action TEXT NOT NULL,
  pid INTEGER NOT NULL,
  started_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_runs (
  run_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  branch TEXT NOT NULL,
  worktree TEXT NOT NULL,
  base TEXT NOT NULL,
  base_sha TEXT NOT NULL,
  head_sha TEXT,
  state TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  pid INTEGER,
  workflow TEXT,
  note TEXT,
  error TEXT,
  archived INTEGER NOT NULL DEFAULT 0,
  started_by TEXT,
  pause TEXT,
  pr_error TEXT,
  library_commit TEXT,
  pr_body TEXT,
  retries TEXT,
  stacked_on TEXT,
  failure_kind TEXT,
  failure_detail TEXT
);
CREATE INDEX IF NOT EXISTS task_runs_task ON task_runs(task_id);
CREATE TABLE IF NOT EXISTS task_prs (
  branch TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  pr_id TEXT NOT NULL,
  url TEXT NOT NULL,
  base TEXT NOT NULL,
  base_sha TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  state TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  merged_at TEXT,
  merge_sha TEXT,
  merged_by TEXT,
  auto_merge_error TEXT,
  approved_at TEXT
);
CREATE INDEX IF NOT EXISTS task_prs_task ON task_prs(task_id);
CREATE TABLE IF NOT EXISTS sync_prs (
  branch TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  pr_id TEXT NOT NULL,
  url TEXT NOT NULL,
  base TEXT NOT NULL,
  base_sha TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  state TEXT NOT NULL,
  tasks TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  merged_at TEXT,
  merge_sha TEXT
);
CREATE TABLE IF NOT EXISTS task_chains (
  chain_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  pid INTEGER,
  max_parallel INTEGER NOT NULL,
  state TEXT NOT NULL,
  stop TEXT,
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  ended_at TEXT,
  run_ids TEXT NOT NULL DEFAULT '[]',
  skipped TEXT NOT NULL DEFAULT '[]',
  exclusive TEXT NOT NULL DEFAULT '[]',
  events TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS task_queue (
  task_id TEXT PRIMARY KEY,
  rank INTEGER,
  excluded INTEGER NOT NULL DEFAULT 0,
  archived INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_priority (
  task_id TEXT PRIMARY KEY,
  blockers TEXT NOT NULL DEFAULT '[]',
  run_id TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS harness_quota (
  harness TEXT PRIMARY KEY,
  exhausted_at TEXT NOT NULL,
  retry_at TEXT NOT NULL,
  message TEXT
);
CREATE TABLE IF NOT EXISTS project_harness (
  project_id TEXT PRIMARY KEY,
  harness TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


_PR_COLUMNS = (
    "branch",
    "task_id",
    "provider",
    "pr_id",
    "url",
    "base",
    "base_sha",
    "title",
    "body",
    "state",
    "created_at",
    "updated_at",
    "merged_at",
    "merge_sha",
    "merged_by",
    "auto_merge_error",
    "approved_at",
)


# columns added to task_prs after its first release; older DBs get them on open
_PR_ADDED_COLUMNS = {"merged_by": "TEXT", "auto_merge_error": "TEXT", "approved_at": "TEXT"}


_SYNC_COLUMNS = (
    "branch",
    "provider",
    "pr_id",
    "url",
    "base",
    "base_sha",
    "title",
    "body",
    "state",
    "tasks",
    "created_at",
    "updated_at",
    "merged_at",
    "merge_sha",
)


_CHAIN_COLUMNS = (
    "chain_id",
    "task_id",
    "pid",
    "max_parallel",
    "state",
    "stop",
    "started_at",
    "updated_at",
    "ended_at",
    "run_ids",
    "skipped",
    "exclusive",
    "events",
    "auto",
    "continued_at",
)


# columns added to task_chains after its first release; older DBs get them on open
_CHAIN_ADDED_COLUMNS = {
    "events": "TEXT NOT NULL DEFAULT '[]'",
    "auto": "INTEGER NOT NULL DEFAULT 0",
    "continued_at": "TEXT",
}


_COLUMNS = (
    "run_id",
    "task_id",
    "branch",
    "worktree",
    "base",
    "base_sha",
    "head_sha",
    "state",
    "started_at",
    "ended_at",
    "pid",
    "workflow",
    "note",
    "error",
    "archived",
    "started_by",
    "pause",
    "pr_error",
    "library_commit",
    "retries",
    "stacked_on",
    "failure_kind",
    "failure_detail",
    "harness_start",
    "pid_created_at",
)


# columns added to task_runs after its first release; older DBs get them on open
# (``pr_body``: the PR body a succeeded run could not send; not part of ``TaskRunRow``)
_RUN_ADDED_COLUMNS = {
    "archived": "INTEGER NOT NULL DEFAULT 0",
    "started_by": "TEXT",
    "pause": "TEXT",
    "pr_error": "TEXT",
    "pr_body": "TEXT",
    "library_commit": "TEXT",
    "retries": "TEXT",
    "stacked_on": "TEXT",
    "failure_kind": "TEXT",
    "failure_detail": "TEXT",
    "harness_start": "TEXT",
    "pid_created_at": "REAL",
}


# the engine's trace tables with rows of a run (``adw_id``), children first
_TRACE_TABLES = (
    "events",
    "envelopes",
    "gate_results",
    "processes",
    "agent_sessions",
    "phases",
    "sessions",
)


_WAL_ATTEMPTS = 50  # x 0.1 s, on top of busy_timeout


def _ensure_wal(conn: sqlite3.Connection, db_path: Path) -> None:
    """Switch the trace DB to WAL, with a busy timeout and retries (see the module docstring)."""
    for _ in range(_WAL_ATTEMPTS):
        try:
            mode = conn.execute("PRAGMA journal_mode=WAL;").fetchone()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) and "busy" not in str(exc):
                raise
        else:
            if mode is not None and str(mode[0]).lower() == "wal":
                return
        time.sleep(0.1)
    raise TaskRunError("trace_db_locked", f"cannot switch {db_path} to WAL")


def _add_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """Add `columns` missing in `table` (a trace DB created by an older version)."""
    present = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, kind in columns.items():
        if name in present:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
        except sqlite3.OperationalError as exc:  # another process added it meanwhile
            if "duplicate column" not in str(exc).lower():
                raise


_READY: set[tuple[str, int, int]] = set()
_READY_LOCK = threading.Lock()


_TABLES = (*re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", _SCHEMA), "trace_changes")


def _tables_present(conn: sqlite3.Connection) -> bool:
    marks = ", ".join("?" for _ in _TABLES)
    row = conn.execute(
        f"SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name IN ({marks})", _TABLES
    ).fetchone()
    return row is not None and row[0] == len(_TABLES)


def _key(db_path: Path) -> tuple[str, int, int]:
    try:
        st = os.stat(db_path)
    except OSError:
        return (str(db_path), 0, 0)
    return (str(db_path.resolve()), st.st_dev, st.st_ino)


def ensure(conn: sqlite3.Connection, db_path: Path) -> None:
    """Create the tables and add the columns older trace DBs lack, once per process and file.

    Later opens of the same file (same path, device and inode) only check, with one query,
    that every table exists, so a file created again or a dropped table gets the schema
    again.
    """
    key = _key(db_path)
    with _READY_LOCK:
        ready = key in _READY
    if ready and _tables_present(conn):
        return
    conn.executescript(_SCHEMA)
    _add_columns(conn, "task_prs", _PR_ADDED_COLUMNS)
    _add_columns(conn, "task_runs", _RUN_ADDED_COLUMNS)
    _add_columns(conn, "task_chains", _CHAIN_ADDED_COLUMNS)
    _add_columns(conn, "task_queue", {"archived": "INTEGER NOT NULL DEFAULT 0"})
    # Older databases acquire archived above, before this index is created.
    conn.execute("CREATE INDEX IF NOT EXISTS task_runs_page ON task_runs(archived, started_at)")
    install_changes(conn)
    if database.is_remote(db_path):
        database.adopt_legacy_table(conn, "giro_owners")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS giro_owners (id TEXT PRIMARY KEY, machine TEXT NOT NULL)"
        )
    with _READY_LOCK:
        _READY.add(key)
