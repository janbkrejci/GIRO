"""``harness_quota`` and ``project_harness``: quota state of this computer."""

from __future__ import annotations

from datetime import UTC, datetime

from aifactory.runstore.base import StoreBase, _now


class QuotaRepo(StoreBase):
    """Harness quota and the harness of a project's quota chain."""

    def mark_quota(self, harness: str, message: str | None, retry_at: str) -> None:
        """Record that `harness` ran out of quota on this computer until `retry_at`."""
        self.conn.execute(
            "INSERT OR REPLACE INTO harness_quota (harness, exhausted_at, retry_at, message) "
            "VALUES (?, ?, ?, ?)",
            (harness, _now(), retry_at, (message or "")[:500] or None),
        )

    def clear_quota(self, harness: str) -> None:
        """`harness` has quota again (a run on it succeeded)."""
        self.conn.execute("DELETE FROM harness_quota WHERE harness = ?", (harness,))

    def quota_retry_at(self, harness: str) -> str | None:
        """When `harness` may have quota again; None when it is not known to be out."""
        row = self.conn.execute(
            "SELECT retry_at FROM harness_quota WHERE harness = ?", (harness,)
        ).fetchone()
        return str(row[0]) if row is not None else None

    def quota_available(self, harness: str, now: datetime | None = None) -> bool:
        """True unless `harness` ran out of quota and its ``retry_at`` is still ahead."""
        retry_at = self.quota_retry_at(harness)
        if retry_at is None:
            return True
        try:
            when = datetime.fromisoformat(retry_at)
        except ValueError:
            return True
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return when <= (now or datetime.now(UTC))

    def project_position(self, project_id: str) -> str | None:
        """The harness of the project's quota chain it is on now; None: its own harness."""
        row = self.conn.execute(
            "SELECT harness FROM project_harness WHERE project_id = ?", (project_id,)
        ).fetchone()
        return str(row[0]) if row is not None else None

    def set_project_position(self, project_id: str, harness: str | None) -> None:
        """Put the project on `harness` of its chain; None: back on its own harness."""
        if harness is None:
            self.conn.execute("DELETE FROM project_harness WHERE project_id = ?", (project_id,))
            return
        self.conn.execute(
            "INSERT OR REPLACE INTO project_harness (project_id, harness, updated_at) "
            "VALUES (?, ?, ?)",
            (project_id, harness, _now()),
        )
