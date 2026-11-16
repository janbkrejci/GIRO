"""``task_runs`` and ``task_operations``: runs, their claim, pause, processes and archive.

A finished run writes or clears the task's priority, so the repository builds on ``QueueRepo``.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from aifactory import failure, oscompat
from aifactory.engine.utils import now_iso
from aifactory.errors import TaskRunError
from aifactory.runstore.base import _alive, _detail_json, _marks, _now
from aifactory.runstore.queue import QueueRepo
from aifactory.runstore.rows import (
    ABORTED,
    PAUSED,
    PAUSING,
    RETRYABLE,
    RUNNING,
    STOPPED,
    SUCCEEDED,
    TaskRunRow,
    already_running,
)
from aifactory.runstore.schema import _COLUMNS, _TRACE_TABLES


class RunRepo(QueueRepo):
    """Task runs and task operations."""

    def _row(self, values: tuple[Any, ...]) -> TaskRunRow:
        return TaskRunRow(**dict(zip(_COLUMNS, values, strict=True)))

    def _select(self, where: str, args: tuple[Any, ...]) -> list[TaskRunRow]:
        sql = (
            f"SELECT {', '.join(_COLUMNS)} FROM task_runs WHERE {where} "
            "ORDER BY started_at DESC, rowid DESC"
        )
        return [self._row(r) for r in self.conn.execute(sql, args).fetchall()]

    def _reap(self, task_id: str | None = None) -> None:
        """Mark `running` rows whose process is gone as `aborted`. Caller holds the txn."""
        where = "state = ?"
        args: tuple[str, ...] = (RUNNING,)
        if task_id is not None:
            where, args = "state = ? AND task_id = ?", (RUNNING, task_id)
        for row in self._select(where, args):
            if self.owned_here(row.run_id) and (
                not _alive(row.pid)
                or (
                    row.pid is not None
                    and oscompat.process_matches(row.pid, row.pid_created_at) is False
                )
            ):
                self.conn.execute(
                    "UPDATE task_runs SET state = ?, ended_at = ?, error = ?, pause = NULL, "
                    "failure_kind = ?, failure_detail = ? WHERE run_id = ?",
                    (
                        ABORTED,
                        _now(),
                        f"process {row.pid} is gone or its PID was reused",
                        failure.PROCESS_GONE,
                        _detail_json({"pid": row.pid}),
                        row.run_id,
                    ),
                )

    def claim(self, row: TaskRunRow) -> None:
        """Insert a `running` row unless the task is running already, atomically."""
        with self.serialized(), self._txn():
            self._reap(row.task_id)
            busy = self._select("state = ? AND task_id = ?", (RUNNING, row.task_id))
            if busy:
                raise already_running(busy[0])
            self._own(row.run_id)
            row.pid_created_at = oscompat.process_created_at(row.pid)
            self.conn.execute(
                f"INSERT INTO task_runs ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                tuple(getattr(row, c) for c in _COLUMNS),
            )

    def reclaim(
        self,
        run_id: str,
        pid: int,
        retry: dict[str, Any],
        note: str | None = None,
        *,
        stacked_on: str | None = None,
    ) -> TaskRunRow:
        """Turn the ended run `run_id` back into ``running`` for a retry, atomically.

        Like ``claim``: fails with ``already_running`` while a run of the task is running.
        Only a ``failed``, ``stopped`` or ``aborted`` run can be retried
        (``run_not_retryable``). `retry` is appended to ``retries``. `note` (a retry on
        another harness: the new roster override) replaces the run's note.
        ``stacked_on`` records a newly available dependency PR used by the retry.
        """
        with self.serialized(), self._txn():
            row = self.get(run_id)
            if row is None:
                raise TaskRunError("unknown_run", f"no run {run_id}")
            self._reap(row.task_id)
            busy = self._select("state = ? AND task_id = ?", (RUNNING, row.task_id))
            if busy:
                raise already_running(busy[0])
            row = self.get(run_id) or row
            if row.state not in RETRYABLE:
                raise TaskRunError(
                    "run_not_retryable",
                    f"run {run_id} of task {row.task_id} is {row.state}; only a failed, "
                    "stopped or aborted run can be retried",
                )
            retries = json.dumps([*row.retry_list(), retry], ensure_ascii=False)
            self._own(run_id)
            self.conn.execute(
                "UPDATE task_runs SET state = ?, pid = ?, pid_created_at = ?, "
                "ended_at = NULL, error = NULL, "
                "pause = NULL, pr_error = NULL, pr_body = NULL, retries = ?, "
                "failure_kind = NULL, failure_detail = NULL, note = COALESCE(?, note), "
                "stacked_on = COALESCE(?, stacked_on) "
                "WHERE run_id = ?",
                (RUNNING, pid, oscompat.process_created_at(pid), retries, note, stacked_on, run_id),
            )
        final = self.get(run_id)
        assert final is not None
        return final

    def set_base_sha(self, run_id: str, base_sha: str) -> None:
        """The commit of base the run's branch now starts from (after a retry's rebase)."""
        self.conn.execute("UPDATE task_runs SET base_sha = ? WHERE run_id = ?", (base_sha, run_id))

    def start_operation(self, task_id: str, action: str, pid: int) -> str:
        """Publish process ownership before a run exists, or while publishing again."""
        operation_id = "operation-" + secrets.token_hex(8)
        self.conn.execute(
            "INSERT INTO task_operations VALUES (?, ?, ?, ?, ?)",
            (operation_id, task_id, action, pid, now_iso()),
        )
        self._own(operation_id)
        return operation_id

    def finish_operation(self, operation_id: str) -> None:
        self.conn.execute("DELETE FROM task_operations WHERE operation_id = ?", (operation_id,))

    def live_operations(self) -> list[TaskRunRow]:
        """Live operation evidence without changing run claims; reap dead owners."""
        rows = self.conn.execute(
            "SELECT operation_id, task_id, action, pid, started_at FROM task_operations "
            "ORDER BY started_at DESC, rowid DESC"
        ).fetchall()
        result = []
        for operation_id, task_id, action, pid, started_at in rows:
            if self.owned_here(operation_id) and not _alive(pid):
                self.finish_operation(operation_id)
                continue
            result.append(
                TaskRunRow(
                    run_id=operation_id,
                    task_id=task_id,
                    branch="",
                    worktree="",
                    base="",
                    base_sha="",
                    head_sha=None,
                    state=RUNNING,
                    started_at=started_at,
                    pid=pid,
                    workflow=action,
                )
            )
        return result

    def live_runs_locked(self) -> list[TaskRunRow]:
        """`running` rows whose process is alive (dead ones become `aborted`).

        The caller holds ``serialized()``, so no run can start before it lets go.
        """
        with self._txn():
            self._reap()
            self.live_operations()
            return self._select("state = ?", (RUNNING,))

    def live_runs(self) -> list[TaskRunRow]:
        with self.serialized():
            return self.live_runs_locked()

    def running(self, task_id: str) -> TaskRunRow | None:
        with self._txn():
            self._reap(task_id)
        busy = self._select("state = ? AND task_id = ?", (RUNNING, task_id))
        return busy[0] if busy else None

    def finish(
        self,
        run_id: str,
        state: str,
        head_sha: str | None,
        error: str | None,
        failure_kind: str | None = None,
        failure_detail: dict[str, Any] | None = None,
    ) -> None:
        """Record the end of a run; a `stopped` run keeps its state (the user stopped it).

        A run that did not succeed gets `failure_kind` (``other`` when None) and
        `failure_detail`; a succeeded run gets neither.
        """
        kind: str | None = None
        detail: str | None = None
        if state != SUCCEEDED:
            kind = failure_kind or failure.OTHER
            detail = _detail_json(failure_detail or {})
        cursor = self.conn.execute(
            "UPDATE task_runs SET state = ?, head_sha = ?, error = ?, ended_at = ?, pause = NULL, "
            "failure_kind = ?, failure_detail = ? WHERE run_id = ? AND state != ?",
            (state, head_sha, error, _now(), kind, detail, run_id, STOPPED),
        )
        if cursor.rowcount:
            self._note_priority(run_id, state, kind, error, failure_detail)

    def _note_priority(
        self,
        run_id: str,
        state: str,
        kind: str | None,
        error: str | None,
        detail: dict[str, Any] | None,
    ) -> None:
        """A blocked run gives its task priority; a succeeded run takes it away."""
        found = self.conn.execute(
            "SELECT task_id FROM task_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if found is None:
            return
        task_id = str(found[0])
        if state == SUCCEEDED:
            self.clear_priority(task_id)
            return
        if kind != failure.BLOCKED and failure.error_blockers(error) is None:
            return  # neither the kind nor the error says blocked
        raw = (detail or {}).get("blockers")
        if isinstance(raw, list):
            blockers = [str(b) for b in raw if str(b).strip()]
        else:
            blockers = failure.error_blockers(error) or []
        self.set_priority(task_id, blockers, run_id)

    def set_pr_error(self, run_id: str, error: str | None, body: str | None = None) -> None:
        """Record why the run's push or PR failed (None: it is published now).

        `body` is the PR body the run meant to send; ``factory task publish`` sends it
        later. Clearing the error drops the kept body.
        """
        if error is None:
            self.conn.execute(
                "UPDATE task_runs SET pr_error = NULL, pr_body = NULL WHERE run_id = ?",
                (run_id,),
            )
            return
        self.conn.execute(
            "UPDATE task_runs SET pr_error = ?, pr_body = COALESCE(?, pr_body) WHERE run_id = ?",
            (error, body, run_id),
        )

    def pr_body_of(self, run_id: str) -> str | None:
        """The PR body kept by ``set_pr_error``, or None."""
        row = self.conn.execute(
            "SELECT pr_body FROM task_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return None if row is None or row[0] is None else str(row[0])

    def mark_stopped(self, run_id: str, note: str) -> bool:
        """Set a `running` run to `stopped`; False when it is not running (any more)."""
        with self._txn():
            cursor = self.conn.execute(
                "UPDATE task_runs SET state = ?, ended_at = ?, error = ?, pause = NULL, "
                "failure_kind = ?, failure_detail = ? WHERE run_id = ? AND state = ?",
                (
                    STOPPED,
                    _now(),
                    note,
                    failure.STOPPED,
                    _detail_json({"note": note}),
                    run_id,
                    RUNNING,
                ),
            )
            return cursor.rowcount == 1

    def request_pause(self, run_id: str) -> bool:
        """Ask a `running` run without a pause to pause (``pausing``); False otherwise."""
        with self._txn():
            cursor = self.conn.execute(
                "UPDATE task_runs SET pause = ? WHERE run_id = ? AND state = ? AND pause IS NULL",
                (PAUSING, run_id, RUNNING),
            )
            return cursor.rowcount == 1

    def request_resume(self, run_id: str) -> bool:
        """Drop the pause (``paused`` or ``pausing``) of a `running` run; False when it has none."""
        with self._txn():
            cursor = self.conn.execute(
                "UPDATE task_runs SET pause = NULL "
                "WHERE run_id = ? AND state = ? AND pause IN (?, ?)",
                (run_id, RUNNING, PAUSING, PAUSED),
            )
            return cursor.rowcount == 1

    def enter_pause(self, run_id: str) -> bool:
        """Turn an asked-for pause (``pausing``) into ``paused``; False when none was asked."""
        with self._txn():
            cursor = self.conn.execute(
                "UPDATE task_runs SET pause = ? WHERE run_id = ? AND state = ? AND pause = ?",
                (PAUSED, run_id, RUNNING, PAUSING),
            )
            return cursor.rowcount == 1

    def pause_of(self, run_id: str) -> tuple[str, str | None] | None:
        """(state, pause) of the run, or None when there is no such run."""
        row = self.conn.execute(
            "SELECT state, pause FROM task_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return None if row is None else (str(row[0]), row[1])

    def live_processes(self, run_id: str) -> list[tuple[str, str, int, str]]:
        """(kind, name, pid, command) of the run's processes believed alive, newest first."""
        if not self._has_table("processes"):
            return []
        rows = self.conn.execute(
            "SELECT kind, name, pid, command FROM processes "
            "WHERE adw_id = ? AND ended_at IS NULL ORDER BY id DESC",
            (run_id,),
        ).fetchall()
        return [(str(k or ""), str(n or ""), int(p), str(c or "")) for k, n, p, c in rows]

    def process_identities(self, run_id: str) -> dict[int, float | None]:
        """Creation times of the latest live process rows; legacy rows are unknown."""
        if not self._has_table("processes"):
            return {}
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(processes)")}
        if "pid_created_at" not in columns:
            return {}
        rows = self.conn.execute(
            "SELECT pid, pid_created_at FROM processes WHERE adw_id = ? "
            "AND ended_at IS NULL ORDER BY id",
            (run_id,),
        ).fetchall()
        return {int(pid): created_at for pid, created_at in rows}

    def close_processes(self, run_id: str) -> None:
        """Close the run's process rows and a still `running` session in the trace."""
        now = _now()
        if self._has_table("processes"):
            self.conn.execute(
                "UPDATE processes SET ended_at = ? WHERE adw_id = ? AND ended_at IS NULL",
                (now, run_id),
            )
        if self._has_table("sessions"):
            self.conn.execute(
                "UPDATE sessions SET status = 'fail', ended_at = ? "
                "WHERE adw_id = ? AND status = 'running'",
                (now, run_id),
            )

    def get(self, run_id: str) -> TaskRunRow | None:
        rows = self._select("run_id = ?", (run_id,))
        return rows[0] if rows else None

    def for_task(self, task_id: str) -> list[TaskRunRow]:
        """Every run of `task_id`, newest first (dead `running` rows become `aborted`)."""
        with self._txn():
            self._reap(task_id)
        return self._select("task_id = ?", (task_id,))

    def branches(self, task_id: str) -> list[str]:
        return [r.branch for r in self._select("task_id = ?", (task_id,))]

    def runs_on_branch(self, branch: str) -> list[TaskRunRow]:
        """Runs on `branch`, newest first."""
        return self._select("branch = ?", (branch,))

    def all_runs(self) -> list[TaskRunRow]:
        """Every run, newest first (dead `running` rows become `aborted`)."""
        with self._txn():
            self._reap()
        return self._select("1 = 1", ())

    def run_page(
        self,
        *,
        state: str | None = None,
        task: str | None = None,
        archived: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[TaskRunRow], int, list[str]]:
        """Filter and page in SQL, hydrating only the requested slice of history."""
        where = ["archived = ?"]
        args: list[Any] = [int(archived)]
        if state == PAUSED:
            where.extend(["state = ?", "pause = ?"])
            args.extend([RUNNING, PAUSED])
        elif state:
            where.append("state = ?")
            args.append(state)
        if task:
            where.append("task_id = ?")
            args.append(task)
        clause = " AND ".join(where)
        with self._txn():
            self._reap()
            total = int(
                self.conn.execute(
                    f"SELECT COUNT(*) FROM task_runs WHERE {clause}", tuple(args)
                ).fetchone()[0]
            )
            rows = self.conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM task_runs WHERE {clause} "
                "ORDER BY started_at DESC, rowid DESC LIMIT ? OFFSET ?",
                (*args, max(0, limit), max(0, offset)),
            ).fetchall()
            tasks = [
                r[0]
                for r in self.conn.execute(
                    "SELECT DISTINCT task_id FROM task_runs ORDER BY task_id"
                )
            ]
        return [self._row(r) for r in rows], total, tasks

    def archive(self, run_id: str) -> bool:
        """Archive a finished run; False when there is no such run.

        A running run (whose process is alive) raises ``run_running``.
        """
        with self._txn():
            self._reap()
            row = self.get(run_id)
            if row is None:
                return False
            if row.state == RUNNING:
                raise TaskRunError("run_running", f"run {run_id} is still running")
            self.conn.execute("UPDATE task_runs SET archived = 1 WHERE run_id = ?", (run_id,))
            return True

    def archive_finished(self) -> list[str]:
        """Archive every finished run that is not archived yet; their ids, newest first."""
        with self._txn():
            self._reap()
            ids = [r.run_id for r in self._select("archived = 0 AND state != ?", (RUNNING,))]
            if ids:
                self.conn.execute(
                    f"UPDATE task_runs SET archived = 1 WHERE run_id IN ({_marks(ids)})",
                    tuple(ids),
                )
            return ids

    def unarchive(self, run_id: str) -> bool:
        """Return an archived run to the active ones; False when there is no such run."""
        cursor = self.conn.execute("UPDATE task_runs SET archived = 0 WHERE run_id = ?", (run_id,))
        return cursor.rowcount == 1

    def _purge(self, ids: list[str]) -> None:
        """Delete the runs `ids` and their trace rows. Caller holds the txn."""
        for table in _TRACE_TABLES:
            if self._has_table(table):
                self.conn.execute(
                    f"DELETE FROM {table} WHERE adw_id IN ({_marks(ids)})", tuple(ids)
                )
        self.conn.execute(f"DELETE FROM task_runs WHERE run_id IN ({_marks(ids)})", tuple(ids))

    def delete_run(self, run_id: str) -> bool:
        """Erase an archived run with its trace; False when there is no such run.

        A run that is not archived raises ``run_not_archived``.
        """
        with self._txn():
            row = self.get(run_id)
            if row is None:
                return False
            if not row.archived:
                raise TaskRunError("run_not_archived", f"run {run_id} is not archived")
            self._purge([run_id])
            return True

    def delete_archived(self) -> list[str]:
        """Erase every archived run with its trace, in one transaction; their ids."""
        with self._txn():
            ids = [r.run_id for r in self._select("archived = 1", ())]
            if ids:
                self._purge(ids)
            return ids
