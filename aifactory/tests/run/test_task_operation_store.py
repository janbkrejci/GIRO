"""Process reservations before claim, with no external services."""

import os
from pathlib import Path

import pytest

from aifactory import oscompat
from aifactory.repo_settings import RepositorySettings
from aifactory.run import store as store_module
from aifactory.run.queue import Slots
from aifactory.run.store import TaskRunRow, TaskRunStore


@pytest.mark.parametrize("finished", [False, True])
def test_parallel_member_detects_reclaimed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finished: bool
) -> None:
    from types import SimpleNamespace

    from run_repo import make_run_repo

    from aifactory.run import members

    repo = make_run_repo(tmp_path / "repo")
    RepositorySettings(repo).update({"auto_continue": True})
    db = repo / ".factory" / "trace.db"
    store = TaskRunStore(db)
    try:
        store.claim(
            TaskRunRow(
                run_id="existing",
                task_id="T01",
                branch="b",
                worktree="",
                base="main",
                base_sha="a",
                head_sha=None,
                state="running",
                started_at="2026-01-01",
                pid=os.getpid(),
            )
        )
        store.finish("existing", "failed", None, "Needs fix")
    finally:
        store.close()

    class Process:
        pid = os.getpid()
        returncode = 0 if finished else None

        def poll(self) -> int | None:
            return self.returncode

    def spawn(*args: object, **kwargs: object) -> Process:
        store = TaskRunStore(db)
        try:
            store.reclaim("existing", os.getpid(), {"started_at": "2026-01-02"})
            if finished:
                store.finish("existing", "succeeded", None, None)
        finally:
            store.close()
        return Process()

    monkeypatch.setattr(members, "subprocess", SimpleNamespace(Popen=spawn, DEVNULL=-3))
    runner = members.ProcessRunner(repo)
    row = runner.start("T01", started_by="auto-continue")
    assert row.run_id == "existing" and row.attempt == 2
    assert row.state == ("succeeded" if finished else "running")
    assert runner.active() == ["T01"]
    store = TaskRunStore(db)
    try:
        assert len(store.for_task("T01")) == 1
        assert store.live_operations() == []
    finally:
        store.close()


def test_operations_preserve_capacity_and_are_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = TaskRunStore(tmp_path / "trace.db")
    try:
        first = store.start_operation("T01", "run", os.getpid())
        second = store.start_operation("T01", "retry", os.getpid())
        assert Slots.of(store).running == ()
        store.claim(
            TaskRunRow(
                run_id="r1",
                task_id="T01",
                branch="b",
                worktree="",
                base="main",
                base_sha="a",
                head_sha=None,
                state="running",
                started_at="2026-01-01",
                pid=os.getpid(),
            )
        )
        assert [r.run_id for r in store.live_runs()] == ["r1"]
        assert Slots.of(store).running == ("T01",)
        store.finish_operation(first)
        assert [r.run_id for r in store.live_operations()] == [second]
        monkeypatch.setattr(store_module, "_alive", lambda pid: False)
        assert store.live_operations() == []
        assert store.conn.execute("SELECT COUNT(*) FROM task_operations").fetchone()[0] == 0
    finally:
        store.close()


def test_member_failure_before_claim_removes_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from run_repo import make_run_repo

    from aifactory.errors import TaskRunError
    from aifactory.run import members

    repo = make_run_repo(tmp_path / "repo")

    class Process:
        pid = os.getpid()
        returncode = 3

        def poll(self) -> int:
            return self.returncode

    monkeypatch.setattr(
        members, "subprocess", SimpleNamespace(Popen=lambda *args, **kwargs: Process(), DEVNULL=-3)
    )

    def before_claim(*args: object) -> None:
        store = TaskRunStore(repo / ".factory" / "trace.db")
        try:
            assert [(r.task_id, r.workflow) for r in store.live_operations()] == [("T01", "run")]
        finally:
            store.close()

    monkeypatch.setattr(members, "new_row", before_claim)
    with pytest.raises(TaskRunError, match="without a readable envelope"):
        members.ProcessRunner(repo).start("T01")
    store = TaskRunStore(repo / ".factory" / "trace.db")
    try:
        assert store.conn.execute("SELECT COUNT(*) FROM task_operations").fetchone()[0] == 0
    finally:
        store.close()


@pytest.mark.parametrize("failure", ["interrupted", "reported"])
def test_publish_keeps_previous_error_while_operation_is_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from types import SimpleNamespace

    from aifactory.review import rework
    from aifactory.review.errors import ReviewError
    from aifactory.review.publish import PublishResult

    store = TaskRunStore(tmp_path / "trace.db")
    store.claim(
        TaskRunRow(
            run_id="r1",
            task_id="T01",
            branch="b",
            worktree="",
            base="main",
            base_sha="a",
            head_sha=None,
            state="running",
            started_at="2026-01-01",
            pid=os.getpid(),
        )
    )
    store.finish("r1", "succeeded", None, None)
    store.set_pr_error("r1", "previous push failed")
    monkeypatch.setattr(rework, "_context", lambda *args: SimpleNamespace(store=store))

    def publishing(*args: object) -> PublishResult:
        assert [(r.task_id, r.workflow) for r in store.live_operations()] == [("T01", "publish")]
        row = store.get("r1")
        assert row is not None and row.pr_error == "previous push failed"
        if failure == "interrupted":
            raise RuntimeError("publication interrupted")
        store.set_pr_error("r1", "new publication failed")
        return PublishResult(run=row, pr=None, pr_error="new publication failed")

    monkeypatch.setattr(rework, "_publish", publishing)
    if failure == "interrupted":
        with pytest.raises(ReviewError, match="publication interrupted"):
            rework.publish_task(tmp_path, "T01")
    else:
        assert rework.publish_task(tmp_path, "T01").pr_error == "new publication failed"
    reopened = TaskRunStore(tmp_path / "trace.db")
    try:
        assert reopened.conn.execute("SELECT COUNT(*) FROM task_operations").fetchone()[0] == 0
        row = reopened.get("r1")
        expected = "previous push failed" if failure == "interrupted" else "new publication failed"
        assert row is not None and row.pr_error == expected
    finally:
        reopened.close()


@pytest.mark.parametrize("outcome", ["claim", "timeout"])
def test_member_operation_handoff_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    from types import SimpleNamespace

    from run_repo import make_run_repo

    from aifactory.errors import TaskRunError
    from aifactory.run import members

    repo = make_run_repo(tmp_path / "repo")
    events: list[str] = []

    class Process:
        pid = os.getpid()
        returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def wait(self) -> int:
            events.append("wait")
            assert self.returncode is not None
            return self.returncode

    process = Process()
    monkeypatch.setattr(
        members, "subprocess", SimpleNamespace(Popen=lambda *args, **kwargs: process, DEVNULL=-3)
    )
    clock = iter([0.0, 2.0])
    monkeypatch.setattr(
        members,
        "time",
        SimpleNamespace(strftime=lambda *args: "clock", monotonic=lambda: next(clock)),
    )

    def terminate(pid: int, *, force: bool) -> None:
        assert pid == process.pid and force is False
        events.append("terminate")
        process.returncode = -15

    monkeypatch.setattr(oscompat, "kill_group", terminate)
    row = TaskRunRow(
        run_id="claimed",
        task_id="T01",
        branch="b",
        worktree="",
        base="main",
        base_sha="a",
        head_sha=None,
        state="running",
        started_at="2026-01-01",
        pid=process.pid,
    )

    def before_claim(*args: object) -> TaskRunRow | None:
        store = TaskRunStore(repo / ".factory" / "trace.db")
        try:
            assert store.conn.execute(
                "SELECT task_id, action, pid FROM task_operations"
            ).fetchall() == [("T01", "run", process.pid)]
            events.append("observed")
            if outcome == "claim":
                store.claim(row)
                return row
            return None
        finally:
            store.close()

    monkeypatch.setattr(members, "new_row", before_claim)
    runner = members.ProcessRunner(repo, claim_timeout=1.0)
    if outcome == "claim":
        assert runner.start("T01") == row
        assert process.poll() is None
        assert runner.active() == ["T01"]
        assert events == ["observed"]
    else:
        with pytest.raises(TaskRunError, match="was not claimed"):
            runner.start("T01")
        assert events == ["observed", "terminate", "wait"]
        assert runner.active() == []
    store = TaskRunStore(repo / ".factory" / "trace.db")
    try:
        assert store.conn.execute("SELECT COUNT(*) FROM task_operations").fetchone()[0] == 0
    finally:
        store.close()


def test_member_claim_loop_does_not_run_git_per_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    from types import SimpleNamespace

    from run_repo import make_run_repo

    from aifactory.run import members

    repo = make_run_repo(tmp_path / "repo")
    row = TaskRunRow(
        run_id="claimed",
        task_id="T01",
        branch="b",
        worktree="",
        base="main",
        base_sha="a",
        head_sha=None,
        state="running",
        started_at="2026-01-01",
        pid=os.getpid(),
    )

    class Process:
        pid = os.getpid()

        def poll(self) -> None:
            return None

    monkeypatch.setattr(
        members, "subprocess", SimpleNamespace(Popen=lambda *args, **kwargs: Process(), DEVNULL=-3)
    )
    real_run = subprocess.run
    git_calls: list[tuple[str, ...]] = []

    def counting_run(args: list[str], *rest: object, **kwargs: object) -> object:
        if args and args[0] == "git":
            git_calls.append(tuple(args))
        return real_run(args, *rest, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(subprocess, "run", counting_run)
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 20:
            store = TaskRunStore(repo / ".factory" / "trace.db")
            try:
                store.claim(row)
            finally:
                store.close()

    monkeypatch.setattr(
        members,
        "time",
        SimpleNamespace(strftime=lambda *args: "clock", monotonic=lambda: 0.0, sleep=sleep),
    )
    assert members.ProcessRunner(repo, poll=0.01).start("T01").run_id == "claimed"
    # Starting resolves the repository a fixed number of times, never once per check.
    assert len(git_calls) < 10, len(git_calls)
    assert sleeps == [members.CLAIM_POLL] * 20
