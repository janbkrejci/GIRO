"""``task_chains``: auto-continue chains."""

from __future__ import annotations

import json
from typing import Any

from aifactory.errors import TaskRunError
from aifactory.runstore.base import StoreBase, _alive, _now
from aifactory.runstore.rows import _CHAIN_DISABLED, ABORTED, CHAIN_FINISHED, RUNNING, TaskChainRow
from aifactory.runstore.schema import _CHAIN_COLUMNS


class ChainRepo(StoreBase):
    """Auto-continue chains."""

    def _chains(
        self, where: str, args: tuple[Any, ...], limit: int | None = None
    ) -> list[TaskChainRow]:
        sql = (
            f"SELECT {', '.join(_CHAIN_COLUMNS)} FROM task_chains WHERE {where} "
            "ORDER BY started_at DESC, rowid DESC"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [
            TaskChainRow(**dict(zip(_CHAIN_COLUMNS, r, strict=True)))
            for r in self.conn.execute(sql, args).fetchall()
        ]

    def start_chain(self, row: TaskChainRow) -> None:
        self._own(row.chain_id)
        self.conn.execute(
            f"INSERT OR REPLACE INTO task_chains ({', '.join(_CHAIN_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _CHAIN_COLUMNS)})",
            tuple(getattr(row, c) for c in _CHAIN_COLUMNS),
        )

    def update_chain(
        self,
        chain_id: str,
        *,
        run_ids: list[str] | None = None,
        skipped: list[dict[str, Any]] | None = None,
        exclusive: list[str] | None = None,
        events: list[dict[str, Any]] | None = None,
        state: str | None = None,
        stop: str | None = None,
    ) -> None:
        """Set the given fields of a chain; ``updated_at`` always, ``ended_at`` when it ends."""
        now = _now()
        fields: dict[str, object] = {"updated_at": now}
        if run_ids is not None:
            fields["run_ids"] = json.dumps(run_ids)
        if skipped is not None:
            fields["skipped"] = json.dumps(skipped, ensure_ascii=False)
        if exclusive is not None:
            fields["exclusive"] = json.dumps(exclusive)
        if events is not None:
            fields["events"] = json.dumps(events, ensure_ascii=False)
        if state is not None:
            fields["state"] = state
            if state != RUNNING:
                fields["ended_at"] = now
        if stop is not None:
            fields["stop"] = stop
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE task_chains SET {sets} WHERE chain_id = ?", (*fields.values(), chain_id)
        )

    def latest_chain_ending_with(self, task_id: str) -> TaskChainRow | None:
        """Newest chain whose last run belongs to this task, including consumed chains."""
        rows = self._chains(
            "json_extract(run_ids, '$[#-1]') IN (SELECT run_id FROM task_runs WHERE task_id = ?)",
            (task_id,),
            1,
        )
        return rows[0] if rows else None

    def claim_chain_continuation(self, chain_id: str) -> bool:
        """Atomically consume a finished not-merged chain, once across processes."""
        now = _now()
        cursor = self.conn.execute(
            "UPDATE task_chains SET continued_at = ?, updated_at = ? "
            "WHERE chain_id = ? AND state = ? AND stop = 'not_merged' "
            "AND continued_at IS NULL",
            (now, now, chain_id, CHAIN_FINISHED),
        )
        return bool(cursor.rowcount)

    def delete_chain(self, chain_id: str) -> None:
        self.conn.execute("DELETE FROM task_chains WHERE chain_id = ?", (chain_id,))

    def get_chain(self, chain_id: str) -> TaskChainRow | None:
        rows = self._chains("chain_id = ?", (chain_id,))
        return rows[0] if rows else None

    def _reap_chains(self) -> None:
        """A `running` chain whose process is gone becomes `aborted`."""
        with self._txn():
            for row in self._chains("state = ?", (RUNNING,)):
                if self.owned_here(row.chain_id) and not _alive(row.pid):
                    now = _now()
                    self.conn.execute(
                        "UPDATE task_chains SET state = ?, ended_at = ?, updated_at = ? "
                        "WHERE chain_id = ?",
                        (ABORTED, now, now, row.chain_id),
                    )

    def chains(self, limit: int = 10) -> list[TaskChainRow]:
        """Running chains, then the newest ended ones, up to `limit` rows in all.

        A `running` chain whose process is gone becomes `aborted`. A chain that stopped
        ``disabled`` after at most one run is left out: a single run without
        auto-continue (such runs no longer get a row, older ones may have one).
        """
        self._reap_chains()
        running = self._chains("state = ?", (RUNNING,))
        rest = max(0, limit - len(running))
        ended = (
            self._chains(
                "state != ? AND NOT (stop IS ? AND json_array_length(run_ids) <= 1)",
                (RUNNING, _CHAIN_DISABLED),
                rest,
            )
            if rest
            else []
        )
        return [*running, *ended]

    def dismiss_chain(self, chain_id: str) -> bool:
        """Erase an ended chain (its runs stay); False when there is no such chain.

        A running chain (whose process is alive) raises ``chain_running``.
        """
        self._reap_chains()
        with self._txn():
            row = self.get_chain(chain_id)
            if row is None:
                return False
            if row.state == RUNNING:
                raise TaskRunError("chain_running", f"chain {chain_id} is still running")
            self.delete_chain(chain_id)
            return True
