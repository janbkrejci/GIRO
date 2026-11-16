"""``task_queue`` and ``task_priority``: the kanban's queue preferences and blocked tasks."""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence

from aifactory.runstore.base import StoreBase, _now
from aifactory.runstore.rows import Priority, QueuePrefs


class QueueRepo(StoreBase):
    """Queue preferences and priorities."""

    def queue_prefs(self) -> QueuePrefs:
        """The manual order, exclusions and archive preferences of tasks."""
        if not self._has_table("task_queue"):
            return QueuePrefs(priority=self.priorities())
        rows = self.conn.execute(
            "SELECT task_id, rank, excluded, archived FROM task_queue ORDER BY rank, task_id"
        ).fetchall()
        order = tuple(str(r[0]) for r in rows if r[1] is not None)
        excluded = frozenset(str(r[0]) for r in rows if r[2])
        archived = frozenset(str(r[0]) for r in rows if r[3])
        return QueuePrefs(
            order=order, excluded=excluded, priority=self.priorities(), archived=archived
        )

    def set_queue_order(self, task_ids: Sequence[str]) -> None:
        """Rank `task_ids` in this order; every other task loses its manual rank."""
        ids = list(task_ids)
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate task id in queue order")
        now = _now()
        with self._txn():
            self.conn.execute("UPDATE task_queue SET rank = NULL, updated_at = ?", (now,))
            for rank, task_id in enumerate(ids):
                self.conn.execute(
                    "INSERT INTO task_queue (task_id, rank, excluded, updated_at) "
                    "VALUES (?, ?, 0, ?) "
                    "ON CONFLICT(task_id) DO UPDATE SET rank = excluded.rank, "
                    "updated_at = excluded.updated_at",
                    (task_id, rank, now),
                )
            self._prune_queue()

    def queue_front(self, task_ids: Sequence[str]) -> None:
        """Move tasks to the front in the given order, preserving other ranks and exclusions."""
        ids = list(task_ids)
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate task id in queue order")
        if not ids:
            return
        front = set(ids)
        now = _now()
        with self._txn():
            rows = self.conn.execute(
                "SELECT task_id FROM task_queue WHERE rank IS NOT NULL ORDER BY rank, task_id"
            ).fetchall()
            order = [*ids, *(str(r[0]) for r in rows if r[0] not in front)]
            for rank, task_id in enumerate(order):
                self.conn.execute(
                    "INSERT INTO task_queue (task_id, rank, excluded, updated_at) "
                    "VALUES (?, ?, 0, ?) "
                    "ON CONFLICT(task_id) DO UPDATE SET rank = excluded.rank, "
                    "updated_at = excluded.updated_at",
                    (task_id, rank, now),
                )

    def set_excluded(self, task_id: str, excluded: bool) -> None:
        """Exclude `task_id` from auto-continue (or include it again)."""
        now = _now()
        with self._txn():
            self.conn.execute(
                "INSERT INTO task_queue (task_id, rank, excluded, updated_at) "
                "VALUES (?, NULL, ?, ?) "
                "ON CONFLICT(task_id) DO UPDATE SET excluded = excluded.excluded, "
                "updated_at = excluded.updated_at",
                (task_id, int(excluded), now),
            )
            self._prune_queue()

    def set_archived(self, task_id: str, archived: bool) -> None:
        """Archive `task_id` on the kanban (or show it again)."""
        now = _now()
        with self._txn():
            self.conn.execute(
                "INSERT INTO task_queue (task_id, rank, excluded, archived, updated_at) "
                "VALUES (?, NULL, 0, ?, ?) "
                "ON CONFLICT(task_id) DO UPDATE SET archived = excluded.archived, "
                "updated_at = excluded.updated_at",
                (task_id, int(archived), now),
            )
            self._prune_queue()

    def _prune_queue(self) -> None:
        self.conn.execute(
            "DELETE FROM task_queue WHERE rank IS NULL AND excluded = 0 AND archived = 0"
        )

    def priorities(self) -> tuple[Priority, ...]:
        """The tasks blocked during a run with their blockers, oldest first."""
        if not self._has_table("task_priority"):
            return ()
        rows = self.conn.execute(
            "SELECT task_id, blockers, run_id, created_at FROM task_priority "
            "ORDER BY created_at, task_id"
        ).fetchall()
        out: list[Priority] = []
        for task_id, blockers, run_id, created in rows:
            try:
                ids = json.loads(blockers or "[]")
            except ValueError:
                ids = []
            listed = tuple(str(i) for i in ids) if isinstance(ids, list) else ()
            out.append(Priority(str(task_id), listed, run_id, str(created)))
        return tuple(out)

    def set_priority(self, task_id: str, blockers: Sequence[str], run_id: str | None) -> None:
        """Give `task_id` priority with `blockers`; a new block keeps the first time."""
        ids = list(dict.fromkeys(b for b in blockers if b != task_id))
        self.conn.execute(
            "INSERT INTO task_priority (task_id, blockers, run_id, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(task_id) DO UPDATE SET blockers = excluded.blockers, "
            "run_id = excluded.run_id",
            (task_id, json.dumps(ids, ensure_ascii=False), run_id, _now()),
        )

    def clear_priority(self, task_id: str) -> bool:
        """Take the priority away from `task_id`; False when it had none."""
        if not self._has_table("task_priority"):
            return False
        cursor = self.conn.execute("DELETE FROM task_priority WHERE task_id = ?", (task_id,))
        return bool(cursor.rowcount)

    def prune_priority(self, active: Collection[str]) -> list[str]:
        """Drop the priority of every task not in `active` (no longer ``todo``)."""
        gone = [p.task_id for p in self.priorities() if p.task_id not in active]
        for task_id in gone:
            self.clear_priority(task_id)
        return gone
