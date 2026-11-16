"""Share Markdown backlog files with optimistic conflict detection and a local mirror."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from aifactory.backlog.roots import backlog_roots, owning_root
from aifactory.config.settings import ProjectSettings, load_local
from aifactory.database import adopt_legacy_table, connect, is_remote
from aifactory.locks import path_lock
from aifactory.oscompat import atomic_write


def _path(root: Path) -> Path | None:
    # Validation copies are deliberately local; they have no Git checkout.
    if not (root / ".git").exists():
        return None
    path = load_local(root).trace_db_path(root)
    return path if is_remote(path) else None


# Trigger name prefix of pre-GIRO shared databases.
_LEGACY_TRIGGER = "haifa_backlog"

# Protected by the same cross-process/local lock as synchronization.
_SYNCED: dict[str, tuple[tuple[tuple[str, int, int, int], ...], int]] = {}


def _stamps(
    root: Path, settings: ProjectSettings, path: Path
) -> tuple[tuple[str, int, int, int], ...]:
    files = [
        file
        for directory in backlog_roots(root, settings)
        for file in (root / directory).rglob("*.md")
        if file.is_file()
    ]
    files.append(_baseline(path))
    return tuple(
        sorted(
            (str(file), stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns)
            for file in files
            if file.is_file()
            for stat in [file.stat()]
        )
    )


def _revision(conn: sqlite3.Connection) -> int | None:
    try:
        row = conn.execute("SELECT revision FROM giro_backlog_revision WHERE id = 1").fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        return None
    return int(row[0]) if row is not None else None


def _adopt_legacy_tables(conn: sqlite3.Connection) -> None:
    """Take over the backlog tables of a pre-GIRO shared database; drop its old triggers."""
    adopt_legacy_table(conn, "giro_backlog")
    adopt_legacy_table(conn, "giro_backlog_revision")
    for event in ("insert", "update", "delete"):
        conn.execute(f"DROP TRIGGER IF EXISTS {_LEGACY_TRIGGER}_{event}")


def _create_triggers(conn: sqlite3.Connection) -> None:
    for event in ("INSERT", "UPDATE", "DELETE"):
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS giro_backlog_{event.lower()} "
            f"AFTER {event} ON giro_backlog "
            "BEGIN UPDATE giro_backlog_revision SET revision = revision + 1 WHERE id = 1; END"
        )


def _snapshot(conn: sqlite3.Connection) -> dict[str, str]:
    if _revision(conn) is None:
        _adopt_legacy_tables(conn)
        conn.execute("CREATE TABLE IF NOT EXISTS giro_backlog (path TEXT PRIMARY KEY, text TEXT)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS giro_backlog_revision "
            "(id INTEGER PRIMARY KEY, revision INTEGER NOT NULL)"
        )
        conn.execute("INSERT OR IGNORE INTO giro_backlog_revision VALUES (1, 0)")
        _create_triggers(conn)
    return dict(conn.execute("SELECT path, text FROM giro_backlog").fetchall())


def _baseline(path: Path) -> Path:
    return path.with_suffix(".backlog.json")


def _save(path: Path, files: dict[str, str]) -> None:
    target = _baseline(path)
    atomic_write(target, json.dumps(files, ensure_ascii=False))


def _synchronize(root: Path, settings: ProjectSettings) -> None:
    """Merge local edits and remote changes, rejecting edits to the same file."""
    path = _path(root)
    if path is None:
        return
    from aifactory import mainwrites
    from aifactory.backlog.edit.common import TaskEditError, _atomic_write, _guard
    from aifactory.backlog.model import Backlog

    stamps = _stamps(root, settings, path)
    conn = connect(path, isolation_level=None)
    try:
        revision = _revision(conn)
        if revision is not None and _SYNCED.get(str(path)) == (stamps, revision):
            return
    finally:
        conn.close()

    local = {
        file.relative_to(root).as_posix(): file.read_text(encoding="utf-8")
        for directory in backlog_roots(root, settings)
        for file in (root / directory).rglob("*.md")
        if file.is_file()
    }
    previous = json.loads(_baseline(path).read_text()) if _baseline(path).is_file() else None
    conn = connect(path, isolation_level=None)
    try:
        conn.execute(
            "BEGIN"
            if previous is not None and local == previous and revision is not None
            else "BEGIN IMMEDIATE"
        )
        remote = _snapshot(conn)
        if not remote:
            for rel, text in local.items():
                conn.execute("INSERT INTO giro_backlog VALUES (?, ?)", (rel, text))
            remote = dict(local)
        elif previous is None and any(local.get(rel) != text for rel, text in remote.items()):
            from aifactory.gitops import read_git

            dirty = read_git(
                root,
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                *backlog_roots(root, settings),
            )
            if dirty:
                raise TaskEditError(
                    "remote_backlog_conflict",
                    "preserve or commit local backlog edits before joining the team database",
                )
        elif previous is not None:
            # A file present in the baseline but removed locally is a conflict rather
            # than an implicit team-wide delete. Git pulls need explicit reconciliation.
            for rel in set(previous) | set(local):
                before, here, there = previous.get(rel), local.get(rel), remote.get(rel)
                if here == before or here == there:
                    continue
                if there != before or here is None:
                    raise TaskEditError(
                        "remote_backlog_conflict",
                        f"backlog conflict in {rel}; preserve your edit and reconcile",
                        path=rel,
                    )
                conn.execute("INSERT OR REPLACE INTO giro_backlog VALUES (?, ?)", (rel, here))
                remote[rel] = here
        revision = _revision(conn)
        conn.execute("COMMIT")
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    for rel, text in remote.items():
        if owning_root(rel, settings) is None:
            raise TaskEditError(
                "outside_backlog", f"remote backlog path outside configured roots: {rel}"
            )
        target = _guard(root, Backlog(root, settings), rel)
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            continue
        old = target.read_bytes() if target.is_file() else None
        _atomic_write(target, text, rel)
        mainwrites.record(
            root,
            rel,
            old,
            target.read_bytes(),
            mode=target.stat().st_mode & 0o777,
            command="backlog remote sync",
        )
    _save(path, remote)
    if revision is not None:
        _SYNCED[str(path)] = (_stamps(root, settings, path), revision)


def _publish(root: Path, changes: dict[str, str]) -> None:
    """Atomically publish validated CLI/dashboard edits against the loaded snapshot."""
    path = _path(root)
    if path is None:
        return
    from aifactory.backlog.edit import TaskEditError

    previous = json.loads(_baseline(path).read_text(encoding="utf-8"))
    conn = connect(path, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        remote = _snapshot(conn)
        if remote != previous:
            raise TaskEditError(
                "remote_backlog_conflict", "backlog changed remotely; reload and retry"
            )
        for rel, text in changes.items():
            conn.execute("INSERT OR REPLACE INTO giro_backlog VALUES (?, ?)", (rel, text))
            remote[rel] = text
        conn.execute("COMMIT")
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def synchronize(root: Path, settings: ProjectSettings) -> None:
    path = _path(root)
    if path is not None:
        from aifactory.backlog.edit import write_lock

        path.parent.mkdir(parents=True, exist_ok=True)
        # Same order as edits: protect their loaded baseline through publication.
        with write_lock(root), path_lock(path.with_suffix(".backlog.lock")).hold(30):
            _synchronize(root, settings)


def publish(root: Path, changes: dict[str, str]) -> None:
    path = _path(root)
    if path is not None:
        from aifactory.backlog.edit import write_lock

        path.parent.mkdir(parents=True, exist_ok=True)
        # Same order as edits: protect their loaded baseline through publication.
        with write_lock(root), path_lock(path.with_suffix(".backlog.lock")).hold(30):
            _publish(root, changes)
