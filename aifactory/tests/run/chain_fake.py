"""A fake ``run_task`` for unit tests of chain rules: no engine, worktree or push.

``FakeRuns.install`` replaces ``aifactory.run.task.run_task`` (``run_chain`` calls it
through the module attribute). Every call records a finished run in the repository's
trace DB: a succeeded run with an open PR by default, a failed or aborted run of a
given failure kind (``fail``), or a failed run blocked on follow-ups (``block``). An
automatic start (``started_by="auto-continue"``) is claimed under
``policy.acceptance`` like the real one, so it is refused once auto-continue is off.
A blocked run leaves the backlog the way ``followups.record``
does: each blocker filed as a ``todo`` task with the follow-up marker of the blocked
task, which gets the blockers in its ``depends_on``, committed to base.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from run_repo import commit_all, git, write

from aifactory.backlog import Task, load_backlog
from aifactory.run import TaskPrRow, TaskRunRow, TaskRunStore, policy
from aifactory.run import task as task_mod
from aifactory.run.followups import follow_up_marker

FAILED, SUCCEEDED = "failed", "succeeded"


@dataclass
class FakeRuns:
    repo: Path
    # task id -> blockers (id, step directory relative to the repo) its next run files
    blocks: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    # task id -> (state, failure kind, failure detail) its next run ends with
    failures: dict[str, tuple[str, str, dict[str, Any]]] = field(default_factory=dict)
    started: list[tuple[str, str | None]] = field(default_factory=list)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeRuns:
        monkeypatch.setattr(task_mod, "run_task", self.run_task)
        return self

    def block(self, task_id: str, blocker: str, step_dir: str) -> None:
        """The next run of `task_id` ends blocked on the follow-up `blocker` in `step_dir`."""
        self.blocks.setdefault(task_id, []).append((blocker, step_dir))

    def fail(
        self,
        task_id: str,
        kind: str = "other",
        *,
        state: str = FAILED,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """The next run of `task_id` ends `state` with the failure `kind` and its `detail`."""
        self.failures[task_id] = (state, kind, detail or {})

    @property
    def db(self) -> Path:
        return self.repo / ".factory" / "trace.db"

    def run_task(self, repo: Path, task_id: str, **kwargs: Any) -> task_mod.TaskRunResult:
        started_by = kwargs.get("started_by")
        with policy.acceptance(self.repo, started_by == "auto-continue"):
            pass  # refused (TaskRunError) when an automatic start is not accepted
        self.started.append((task_id, started_by))
        base_sha = git(self.repo, "rev-parse", "main")
        run_id = uuid.uuid4().hex[:12]
        branch = f"factory/{task_id}-{run_id}"
        row = TaskRunRow(
            run_id,
            task_id,
            branch,
            str(self.repo / "wt" / run_id),
            "main",
            base_sha,
            None,
            "running",
            "now",
            pid=os.getpid(),
            started_by=started_by,
        )
        blockers = self.blocks.pop(task_id, [])
        failed = self.failures.pop(task_id, None)
        store = TaskRunStore(self.db)
        try:
            store.claim(row)
            pr: TaskPrRow | None = None
            if blockers:
                ids = self._file_blockers(task_id, blockers)
                error = task_mod.blocked_reason(task_id, ids)
                store.finish(run_id, FAILED, None, error, "blocked", {"blockers": ids})
            elif failed is not None:
                state, kind, detail = failed
                store.finish(run_id, state, None, f"{kind}: fake failure", kind, detail)
            else:
                store.finish(run_id, SUCCEEDED, base_sha, None)
                pr = TaskPrRow(
                    branch, task_id, "local", run_id, f"local:{branch}", "main", base_sha,
                    f"Task {task_id}", "", "open", "now", "now",
                )  # fmt: skip
                store.save_pr(pr)
            final = store.get(run_id)
        finally:
            store.close()
        assert final is not None
        return task_mod.TaskRunResult(final, None, (), self.db, pr=pr)

    def _file_blockers(self, task_id: str, blockers: list[tuple[str, str]]) -> list[str]:
        task = load_backlog(self.repo).by_id[task_id]
        assert isinstance(task, Task) and not task.depends_on
        ids = [blocker for blocker, _ in blockers]
        for blocker, step_dir in blockers:
            write(
                self.repo,
                f"{step_dir}/{blocker}.md",
                f"---\nid: {blocker}\ntitle: Blocker {blocker}\nstatus: todo\n---\n\n"
                f"{follow_up_marker(task_id)}\nBlocks {task_id}.\n",
            )
        path = self.repo / task.path
        deps = ", ".join(ids)
        text = path.read_text(encoding="utf-8")
        text = text.replace("status: todo\n", f"status: todo\ndepends_on: [{deps}]\n", 1)
        path.write_text(text, encoding="utf-8", newline="\n")
        commit_all(self.repo, f"follow-ups of {task_id}")
        return ids
