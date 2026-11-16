"""``task_prs`` and ``sync_prs``: pull requests of task runs and of ``backlog sync``."""

from __future__ import annotations

from typing import Any

from aifactory.runstore.base import StoreBase, _now
from aifactory.runstore.rows import SyncPrRow, TaskPrRow
from aifactory.runstore.schema import _PR_COLUMNS, _SYNC_COLUMNS


class PrRepo(StoreBase):
    """Pull requests of task runs and of ``backlog sync``."""

    def _prs(self, where: str, args: tuple[Any, ...]) -> list[TaskPrRow]:
        sql = (
            f"SELECT {', '.join(_PR_COLUMNS)} FROM task_prs WHERE {where} "
            "ORDER BY created_at DESC, rowid DESC"
        )
        return [
            TaskPrRow(**dict(zip(_PR_COLUMNS, r, strict=True)))
            for r in self.conn.execute(sql, args).fetchall()
        ]

    def save_pr(self, row: TaskPrRow) -> None:
        self.conn.execute(
            f"INSERT OR REPLACE INTO task_prs ({', '.join(_PR_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _PR_COLUMNS)})",
            tuple(getattr(row, c) for c in _PR_COLUMNS),
        )

    def update_pr(self, branch: str, **fields: object) -> None:
        """Set `fields` of the PR of `branch`; ``updated_at`` is set too."""
        unknown = set(fields) - set(_PR_COLUMNS)
        if unknown:
            raise ValueError(f"unknown task_prs columns: {sorted(unknown)}")
        fields.setdefault("updated_at", _now())
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE task_prs SET {sets} WHERE branch = ?", (*fields.values(), branch)
        )

    def pr_for_branch(self, branch: str) -> TaskPrRow | None:
        rows = self._prs("branch = ?", (branch,))
        return rows[0] if rows else None

    def latest_pr(self, task_id: str) -> TaskPrRow | None:
        """The newest PR of `task_id`, in any state."""
        rows = self._prs("task_id = ?", (task_id,))
        return rows[0] if rows else None

    def open_pr(self, task_id: str) -> TaskPrRow | None:
        rows = self._prs("task_id = ? AND state = 'open'", (task_id,))
        return rows[0] if rows else None

    def prs_for_task(self, task_id: str) -> list[TaskPrRow]:
        return self._prs("task_id = ?", (task_id,))

    def done_prs(self) -> list[TaskPrRow]:
        """Merged and closed pull requests, newest first (merged_at, else updated_at)."""
        sql = (
            f"SELECT {', '.join(_PR_COLUMNS)} FROM task_prs "
            "WHERE state IN ('merged', 'closed') "
            "ORDER BY COALESCE(merged_at, updated_at) DESC, created_at DESC, rowid DESC"
        )
        return [
            TaskPrRow(**dict(zip(_PR_COLUMNS, r, strict=True)))
            for r in self.conn.execute(sql).fetchall()
        ]

    def open_prs(self) -> list[TaskPrRow]:
        """Every open pull request, newest first."""
        return self._prs("state = 'open'", ())

    def merged_task_ids(self) -> list[str]:
        """Distinct ids of tasks with at least one merged PR, in id order."""
        rows = self.conn.execute(
            "SELECT DISTINCT task_id FROM task_prs WHERE state = 'merged' ORDER BY task_id"
        ).fetchall()
        return [str(r[0]) for r in rows]

    def _sync_prs(self, where: str, args: tuple[Any, ...]) -> list[SyncPrRow]:
        sql = (
            f"SELECT {', '.join(_SYNC_COLUMNS)} FROM sync_prs WHERE {where} "
            "ORDER BY created_at DESC, rowid DESC"
        )
        return [
            SyncPrRow(**dict(zip(_SYNC_COLUMNS, r, strict=True)))
            for r in self.conn.execute(sql, args).fetchall()
        ]

    def save_sync_pr(self, row: SyncPrRow) -> None:
        self.conn.execute(
            f"INSERT OR REPLACE INTO sync_prs ({', '.join(_SYNC_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _SYNC_COLUMNS)})",
            tuple(getattr(row, c) for c in _SYNC_COLUMNS),
        )

    def update_sync_pr(self, branch: str, **fields: object) -> None:
        """Set `fields` of the sync PR of `branch`; ``updated_at`` is set too."""
        unknown = set(fields) - set(_SYNC_COLUMNS)
        if unknown:
            raise ValueError(f"unknown sync_prs columns: {sorted(unknown)}")
        fields.setdefault("updated_at", _now())
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE sync_prs SET {sets} WHERE branch = ?", (*fields.values(), branch)
        )

    def sync_pr_for_branch(self, branch: str) -> SyncPrRow | None:
        rows = self._sync_prs("branch = ?", (branch,))
        return rows[0] if rows else None

    def open_sync_prs(self) -> list[SyncPrRow]:
        """Every open sync pull request, newest first."""
        return self._sync_prs("state = 'open'", ())

    def sync_branches(self) -> list[str]:
        return [r.branch for r in self._sync_prs("1 = 1", ())]
