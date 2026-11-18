"""Startup failures finish the claim, and only uncreated branches may be retried."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from run_repo import SPEC, T01, Script, fake_env, git, make_run_repo, ok, write

from aifactory import failure
from aifactory.config.runtime import prepare_cfg
from aifactory.errors import TaskRunError
from aifactory.locks import path_lock
from aifactory.run import run_task
from aifactory.run import task as task_mod
from aifactory.run.retry import retry_run
from aifactory.runstore import base
from aifactory.runstore.store import TaskRunStore


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> Iterator[Script]:
    yield from fake_env(monkeypatch)


def test_lock_after_claim_finishes_run_and_retry_creates_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: Script
) -> None:
    repo = make_run_repo(tmp_path / "repo")
    acquired, release = threading.Event(), threading.Event()
    threads: list[threading.Thread] = []
    original_claim = task_mod._claim

    def claim(*args: Any, **kwargs: Any) -> None:
        original_claim(*args, **kwargs)

        def hold() -> None:
            with path_lock(repo / ".factory/trace.db.lock").hold(5):
                acquired.set()
                release.wait(20)

        thread = threading.Thread(target=hold)
        threads.append(thread)
        thread.start()
        assert acquired.wait(5)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(base, "SERIAL_TIMEOUT", 0.1)
            patch.setattr(task_mod, "_claim", claim)
            with pytest.raises(TaskRunError, match="trace_db_locked"):
                run_task(repo, T01)
    finally:
        release.set()
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()

    store = TaskRunStore(repo / ".factory/trace.db")
    try:
        row = store.for_task(T01)[0]
        assert row.state == "failed" and row.ended_at is not None
        assert row.failure_kind == failure.INITIALIZATION_FAILED
        assert row.failure_detail_dict() == {"code": "trace_db_locked", "can_recreate_branch": True}
        assert store.running(T01) is None
    finally:
        store.close()
    assert not Path(row.worktree).exists()
    assert git(repo, "for-each-ref", "--format=%(refname)", f"refs/heads/{row.branch}") == ""

    script.on("planner", lambda wt: write(wt, SPEC, "# initialized on retry\n"))
    script.add("planner", ok(artifacts=[SPEC], commit_message="Initialize on retry"))
    retried = retry_run(repo, T01, run_id=row.run_id)
    assert retried.run.run_id == row.run_id
    assert retried.run.state == "succeeded", retried.run.error
    assert git(repo, "show", f"{row.branch}:{SPEC}") == "# initialized on retry"


@pytest.mark.parametrize("error", [ValueError("bad configuration"), OSError("disk unavailable")])
def test_prompt_preparation_failure_finishes_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: Script, error: Exception
) -> None:
    repo = make_run_repo(tmp_path / "repo")

    def prepare(*args: Any, **kwargs: Any) -> Any:
        # Leave preflight intact; fail only once the claimed session is being prepared.
        if "sessions" in Path(args[2]).parts:
            raise error
        return prepare_cfg(*args, **kwargs)

    monkeypatch.setattr(task_mod, "prepare_cfg", prepare)
    with pytest.raises(TaskRunError, match="worktree_failed"):
        run_task(repo, T01)
    store = TaskRunStore(repo / ".factory/trace.db")
    try:
        row = store.for_task(T01)[0]
        assert row.state == "failed" and row.ended_at is not None
        assert row.failure_kind == failure.INITIALIZATION_FAILED
        assert not Path(row.worktree).exists()
    finally:
        store.close()


def test_retry_does_not_recreate_a_deleted_existing_branch(tmp_path: Path, script: Script) -> None:
    repo = make_run_repo(tmp_path / "repo")
    script.add("planner", {"status": "fail", "summary": "failed after creating the worktree"})
    result = run_task(repo, T01)
    assert result.run.state == "failed"
    git(repo, "worktree", "remove", "--force", result.run.worktree)
    git(repo, "branch", "-D", result.run.branch)
    with pytest.raises(TaskRunError, match="unknown_branch"):
        retry_run(repo, T01, run_id=result.run.run_id)
