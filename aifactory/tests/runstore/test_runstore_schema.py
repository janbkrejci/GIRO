"""The trace store creates and migrates its schema once per process and database file."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aifactory.runstore import schema
from aifactory.runstore.store import TaskRunStore


def _open(path: Path) -> None:
    TaskRunStore(path).close()


@pytest.fixture
def ddl(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    original = schema._add_columns

    def counted(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        calls.append(table)
        original(conn, table, columns)

    monkeypatch.setattr(schema, "_add_columns", counted)
    return calls


def test_second_open_runs_no_schema_statements(tmp_path: Path, ddl: list[str]) -> None:
    db = tmp_path / "trace.db"
    _open(db)
    assert sorted(ddl) == ["task_chains", "task_prs", "task_queue", "task_runs"]
    ddl.clear()
    _open(db)
    _open(db)
    assert ddl == []


def test_a_new_file_at_the_same_path_gets_the_schema(tmp_path: Path, ddl: list[str]) -> None:
    db = tmp_path / "trace.db"
    _open(db)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db}{suffix}").unlink(missing_ok=True)
    ddl.clear()
    store = TaskRunStore(db)
    try:
        assert ddl, "a deleted trace DB must get its tables again"
        assert store.all_runs() == []
    finally:
        store.close()


def test_an_older_trace_db_gets_its_columns(tmp_path: Path) -> None:
    db = tmp_path / "trace.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE task_prs (branch TEXT PRIMARY KEY, task_id TEXT)")
    conn.commit()
    conn.close()
    _open(db)
    conn = sqlite3.connect(db)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(task_prs)")}
    finally:
        conn.close()
    assert {"merged_by", "auto_merge_error", "approved_at"} <= columns


def test_a_column_dropped_after_the_first_open_comes_back(tmp_path: Path) -> None:
    db = tmp_path / "trace.db"
    _open(db)
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE task_prs DROP COLUMN approved_at")
    conn.commit()
    conn.close()
    _open(db)
    conn = sqlite3.connect(db)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(task_prs)")}
    finally:
        conn.close()
    assert "approved_at" in columns
