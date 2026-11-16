"""Read the trace store of a repository without creating it.

``existing_store`` opens the trace DB of the main checkout only when it exists;
``task_runs_for`` and ``task_prs_for`` read a task's runs and pull requests through it.
"""

from __future__ import annotations

from pathlib import Path

from aifactory import database, gitops
from aifactory.config import ConfigError, load_local
from aifactory.errors import TaskRunError
from aifactory.runstore.rows import TaskPrRow, TaskRunRow
from aifactory.runstore.store import TaskRunStore


def existing_store(repo: Path) -> TaskRunStore | None:
    """The store of the trace DB of `repo`, or None when there is no trace DB yet."""
    try:
        main = gitops.main_root(repo)
    except TaskRunError:
        main = repo.resolve()
    try:
        local = load_local(main)
    except ConfigError as exc:
        raise TaskRunError("invalid_config", str(exc)) from exc
    db = local.trace_db_path(main)
    if not database.available(db):
        return None
    return TaskRunStore(db)


def task_runs_for(repo: Path, task_id: str) -> list[TaskRunRow]:
    """Every run of `task_id`, newest first. Creates nothing when there is no trace DB yet.

    Outside a git repository (a bare backlog directory) `repo` itself is the root.
    """
    store = existing_store(repo)
    if store is None:
        return []
    try:
        return store.for_task(task_id)
    finally:
        store.close()


def task_prs_for(repo: Path, task_id: str) -> list[TaskPrRow]:
    """Every pull request of `task_id`, newest first; `[]` when there is no trace DB yet."""
    store = existing_store(repo)
    if store is None:
        return []
    try:
        return store.prs_for_task(task_id)
    finally:
        store.close()
