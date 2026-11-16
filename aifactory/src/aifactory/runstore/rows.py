"""Row types and state names of the trace store's tables."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from aifactory import failure
from aifactory.errors import TaskRunError
from aifactory.providers.base import PullRequest

RUNNING, SUCCEEDED, FAILED, ABORTED = "running", "succeeded", "failed", "aborted"


STOPPED = "stopped"


# the states a run can be retried from (``factory task retry``)
RETRYABLE = (FAILED, STOPPED, ABORTED)


# ``task_runs.pause`` of a running run; PAUSED is also the state a paused run is shown in
PAUSING, PAUSED = "pausing", "paused"


MERGED_BY_AUTO = "auto-merge"


MERGED_BY_OPERATOR = "operator"


CHAIN_FINISHED = "finished"


# the stop of a chain whose finished task has auto-continue off (``chain.STOP_DISABLED``)
_CHAIN_DISABLED = "disabled"


# who started a run (``task_runs.started_by``)
STARTED_MANUAL, STARTED_AUTO_CONTINUE, STARTED_AUTO_RESOLVE = (
    "manual",
    "auto-continue",
    "auto-resolve",
)


STARTED_AUTO_CI_FIX = "auto-ci-fix"


@dataclass
class TaskRunRow:
    run_id: str
    task_id: str
    branch: str
    worktree: str
    base: str
    base_sha: str
    head_sha: str | None
    state: str
    started_at: str
    ended_at: str | None = None
    pid: int | None = None
    workflow: str | None = None
    note: str | None = None
    error: str | None = None
    archived: int = 0  # 1 = hidden from the list of active runs
    started_by: str | None = None  # manual, auto-continue, auto-resolve, auto-ci-fix; None: none
    pause: str | None = None  # of a running run: None, PAUSING or PAUSED
    pr_error: str | None = None  # why a succeeded run has no PR (push or hosting failed)
    library_commit: str | None = None  # the library commit the run read; None: the seed
    retries: str | None = None  # JSON list of the run's retries (``retry_list``); None: none
    # the branch of the dependency's open PR the run forked from (``stacked_prs``); None: base
    stacked_on: str | None = None
    # of a run that did not succeed: one of ``failure.FAILURE_KINDS``; None: older run
    failure_kind: str | None = None
    failure_detail: str | None = None  # JSON object with the failure's facts
    # JSON object: the harness switch at the start of a run (``run/quota_chain.py``); None: none
    harness_start: str | None = None
    pid_created_at: float | None = None

    def retry_list(self) -> list[dict[str, Any]]:
        """One item per retry, oldest first (see the module docstring)."""
        return [r for r in _json_list(self.retries) if isinstance(r, dict)]

    @property
    def attempt(self) -> int:
        """1 for the first attempt, +1 for every retry."""
        return 1 + len(self.retry_list())

    @property
    def shown_state(self) -> str:
        """The state to show: ``paused`` for a running run that waits, else ``state``."""
        return PAUSED if self.state == RUNNING and self.pause == PAUSED else self.state

    @property
    def shown_failure_kind(self) -> str | None:
        """The failure kind; for an ended unsuccessful run recorded without one ``other``
        (``blocked`` when its error says so, ``failure.shown_kind``)."""
        if self.state in (RUNNING, SUCCEEDED):
            return None
        return failure.shown_kind(self.failure_kind, self.error)

    def failure_detail_dict(self) -> dict[str, Any] | None:
        """The failure's facts; ``{}`` when the run has none, None when it did not fail."""
        if self.shown_failure_kind is None:
            return None
        try:
            loaded = json.loads(self.failure_detail or "{}")
        except ValueError:
            loaded = {}
        detail: dict[str, Any] = loaded if isinstance(loaded, dict) else {}
        if self.shown_failure_kind == failure.BLOCKED and "blockers" not in detail:
            # a run recorded before the kind: the blockers are in its error
            detail["blockers"] = failure.error_blockers(self.error) or []
        return detail

    def harness_start_dict(self) -> dict[str, Any] | None:
        """The switch the run started with (the project's quota chain); None without one."""
        try:
            loaded = json.loads(self.harness_start or "null")
        except ValueError:
            return None
        return loaded if isinstance(loaded, dict) else None

    def to_json(self) -> dict[str, object]:
        data: dict[str, object] = dict(asdict(self))
        data["retries"] = self.retry_list()
        data["attempt"] = self.attempt
        data["failure_kind"] = self.shown_failure_kind
        data["failure_detail"] = self.failure_detail_dict()
        return data


@dataclass
class TaskPrRow:
    """The factory's record of a task's pull request (``state``: open, merged, closed)."""

    branch: str
    task_id: str
    provider: str
    pr_id: str
    url: str
    base: str
    base_sha: str
    title: str
    body: str
    state: str
    created_at: str
    updated_at: str
    merged_at: str | None = None
    merge_sha: str | None = None
    merged_by: str | None = None  # MERGED_BY_AUTO | MERGED_BY_OPERATOR once merged
    auto_merge_error: str | None = None  # why auto-merge left the PR open
    approved_at: str | None = None  # operator approval waiting for CI and merge

    def to_json(self) -> dict[str, object]:
        return dict(asdict(self))

    def request(self) -> PullRequest:
        return PullRequest(self.pr_id, self.url, self.branch, self.base, self.title)


@dataclass
class SyncPrRow:
    """A ``backlog sync`` pull request; ``tasks`` is a JSON list of the task ids it marks done."""

    branch: str
    provider: str
    pr_id: str
    url: str
    base: str
    base_sha: str
    title: str
    body: str
    state: str
    tasks: str
    created_at: str
    updated_at: str
    merged_at: str | None = None
    merge_sha: str | None = None

    def task_ids(self) -> list[str]:
        loaded = json.loads(self.tasks or "[]")
        return [str(t) for t in loaded] if isinstance(loaded, list) else []

    def to_json(self) -> dict[str, object]:
        data: dict[str, object] = dict(asdict(self))
        data["tasks"] = self.task_ids()
        return data

    def request(self) -> PullRequest:
        return PullRequest(self.pr_id, self.url, self.branch, self.base, self.title)


@dataclass
class TaskChainRow:
    """One auto-continue chain (``state``: running, finished, aborted).

    ``run_ids`` (in start order), ``skipped`` (``Skip.to_json()`` of the last selection),
    ``exclusive`` (task ids that ran alone: wide writes) and ``events`` (the chain's
    automatic actions after a failure, ``heal.HealEvent.to_json()``) are JSON lists.
    """

    chain_id: str
    task_id: str
    pid: int | None
    max_parallel: int
    state: str
    started_at: str
    updated_at: str
    stop: str | None = None
    ended_at: str | None = None
    run_ids: str = "[]"
    skipped: str = "[]"
    exclusive: str = "[]"
    events: str = "[]"
    auto: int = 0  # --auto applies to every continuation
    continued_at: str | None = None

    def run_id_list(self) -> list[str]:
        return [str(r) for r in _json_list(self.run_ids)]

    def skipped_list(self) -> list[dict[str, Any]]:
        return [s for s in _json_list(self.skipped) if isinstance(s, dict)]

    def exclusive_list(self) -> list[str]:
        return [str(t) for t in _json_list(self.exclusive)]

    def event_list(self) -> list[dict[str, Any]]:
        return [e for e in _json_list(self.events) if isinstance(e, dict)]

    def to_json(self) -> dict[str, object]:
        data: dict[str, object] = dict(asdict(self))
        data["run_ids"] = self.run_id_list()
        data["skipped"] = self.skipped_list()
        data["exclusive"] = self.exclusive_list()
        data["events"] = self.event_list()
        return data


def _json_list(text: str | None) -> list[Any]:
    try:
        loaded = json.loads(text or "[]")
    except ValueError:
        return []
    return loaded if isinstance(loaded, list) else []


def already_running(row: TaskRunRow) -> TaskRunError:
    return TaskRunError(
        "already_running",
        f"task {row.task_id} is already running: run {row.run_id} (pid {row.pid}) "
        f"since {row.started_at} on {row.branch}",
    )


@dataclass(frozen=True)
class Priority:
    """A task that ended blocked during a run, with the blockers it waits for."""

    task_id: str
    blockers: tuple[str, ...] = ()
    run_id: str | None = None
    created_at: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "blockers": list(self.blockers),
            "run_id": self.run_id,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class QueuePrefs:
    """Kanban preferences for auto-continue: manual order, excluded and archived tasks.

    ``priority``: the blocked tasks with their blockers (``task_priority``), oldest first.
    """

    order: tuple[str, ...] = ()
    excluded: frozenset[str] = frozenset()
    priority: tuple[Priority, ...] = ()
    archived: frozenset[str] = frozenset()
