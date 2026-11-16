"""Chain rules of ``run_chain`` over a fake ``run_task`` (``chain_fake``): no engine runs.

Each test has the input and the expectations of a former full chain (named in its
docstring) in ``test_auto_continue.py``, ``test_auto_merge.py``,
``test_live_auto_continue.py`` or ``test_auto_heal.py``; those files keep the full chains
that cover the engine, the PRs, the merge, the healing actions and the follow-up filing.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from chain_fake import FakeRuns
from run_repo import commit_all, make_run_repo
from test_auto_continue import (
    BLOCKER,
    CORE,
    S02_T01,
    T01,
    T02,
    T03,
    _store,
    _todo_step,
    ids,
    runs_of,
    setup_chain,
)
from test_auto_heal import _chain_events as chain_events
from test_auto_heal import _repo as heal_repo
from test_auto_heal import _status as heal_status
from test_auto_merge import pr_row
from test_auto_merge import setup as setup_merge

from aifactory import failure
from aifactory.backlog import Task, load_backlog
from aifactory.cli import main
from aifactory.errors import TaskRunError
from aifactory.repo_settings import RepositorySettings
from aifactory.review.automerge import AutoMergeResult
from aifactory.run import (
    STOP_DISABLED,
    STOP_EXHAUSTED,
    STOP_FAILED,
    ChainResult,
    queue,
    run_chain,
)
from aifactory.run import task as task_mod
from aifactory.run.heal import heal
from aifactory.run.policy import DISABLED
from aifactory.runstore.rows import ABORTED

S02 = "writes: [src/other/]\n"
TODO_DIR = f"{CORE}/TODO"


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    return make_run_repo(tmp_path / "repo")


def fake(repo: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRuns:
    return FakeRuns(repo).install(monkeypatch)


def reasons(chain: ChainResult) -> list[tuple[str, str]]:
    return [(s.task_id, s.reason) for s in chain.waiting]


def test_auto_flag_continues(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Was ``test_auto_continue.py::test_auto_flag_continues``."""
    setup_chain(repo)
    runs = fake(repo, monkeypatch)
    chain = run_chain(repo, T01, auto=True)
    assert ids(chain) == [T01, T02, T03]
    assert chain.stop == STOP_EXHAUSTED
    assert [by for _, by in runs.started] == ["manual", "auto-continue", "auto-continue"]


def test_module_switch_continues_across_steps(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Was ``test_auto_continue.py::test_module_switch_continues_across_steps``."""
    setup_chain(repo, module_auto=True, s02_writes=S02)
    fake(repo, monkeypatch)
    chain = run_chain(repo, T01)
    assert ids(chain) == [T01, T02, T03, S02_T01]
    assert chain.ok
    assert chain.stop == STOP_EXHAUSTED


def test_local_switch_covers_steps_without_legacy_metadata(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was ``test_auto_continue.py::test_local_switch_covers_steps_without_legacy_metadata``."""
    setup_chain(repo, step_auto=True, s02_writes=S02)
    fake(repo, monkeypatch)
    chain = run_chain(repo, T01)
    assert ids(chain) == [T01, T02, T03, S02_T01]
    assert (chain.stop, chain.waiting) == (STOP_EXHAUSTED, [])
    assert len(runs_of(repo, S02_T01)) == 1


def test_legacy_step_off_does_not_override_local_on(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was ``test_auto_continue.py::test_legacy_step_off_does_not_override_local_on``."""
    setup_chain(repo, module_auto=True, s02_writes=S02 + "auto_continue: false\n")
    fake(repo, monkeypatch)
    chain = run_chain(repo, T01)
    assert ids(chain) == [T01, T02, T03, S02_T01]
    assert (chain.stop, chain.waiting) == (STOP_EXHAUSTED, [])
    assert len(runs_of(repo, S02_T01)) == 1


def test_blocked_task_without_startable_blocker_takes_the_next(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was ``test_auto_continue.py::test_blocked_task_without_startable_blocker_takes_the_next``."""
    setup_chain(repo, module_auto=True)
    _todo_step(repo, "writes: [src/todo/]\nworkflow: null\n")
    commit_all(repo, "todo step")
    runs = fake(repo, monkeypatch)
    runs.block(T01, BLOCKER, TODO_DIR)
    chain = run_chain(repo, T01)
    assert ids(chain) == [T01, T02, T03]
    assert chain.stop == STOP_EXHAUSTED
    assert (T01, "blocked") in reasons(chain) and (BLOCKER, "no_workflow") in reasons(chain)
    assert runs_of(repo, BLOCKER) == []


def test_blocked_task_with_nothing_else_is_exhausted(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was ``test_auto_continue.py::test_blocked_task_with_nothing_else_is_exhausted``."""
    setup_chain(repo, module_auto=True)
    _todo_step(repo, "writes: [src/todo/]\nworkflow: null\n")
    commit_all(repo, "todo step")
    store = _store(repo)
    try:
        store.set_excluded(T02, True)
        store.set_excluded(T03, True)
    finally:
        store.close()
    runs = fake(repo, monkeypatch)
    runs.block(T01, BLOCKER, TODO_DIR)
    chain = run_chain(repo, T01)
    assert ids(chain) == [T01]
    assert chain.stop == STOP_EXHAUSTED
    assert (T01, "blocked") in reasons(chain)


def test_blocked_task_without_auto_continue_stops(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was ``test_auto_continue.py::test_blocked_task_without_auto_continue_stops``."""
    setup_chain(repo)
    RepositorySettings(repo).update({"auto_continue": False})
    _todo_step(repo, "writes: [src/todo/]\n")
    commit_all(repo, "todo step")
    runs = fake(repo, monkeypatch)
    runs.block(T01, BLOCKER, TODO_DIR)
    chain = run_chain(repo, T01)
    assert ids(chain) == [T01]
    assert chain.stop == STOP_DISABLED
    assert runs_of(repo, BLOCKER) == [] and runs_of(repo, T02) == []


def test_blocked_task_with_auto_flag_continues(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Was ``test_auto_continue.py::test_blocked_task_with_auto_flag_continues``."""
    setup_chain(repo)
    _todo_step(repo, "writes: [src/todo/]\n")
    commit_all(repo, "todo step")
    runs = fake(repo, monkeypatch)
    runs.block(T01, BLOCKER, TODO_DIR)
    chain = run_chain(repo, T01, auto=True)
    assert ids(chain) == [T01, BLOCKER, T02, T03]
    assert chain.stop == STOP_EXHAUSTED


# ── auto-merge (test_auto_merge.py) ──────────────────────────────────────────


def _merge_result(monkeypatch: pytest.MonkeyPatch, result: AutoMergeResult) -> list[str]:
    """``try_auto_merge`` answers `result`; returns the task ids it was asked for."""
    asked: list[str] = []

    def merge(repo: Path, last: task_mod.TaskRunResult, *args: Any, **kwargs: Any) -> Any:
        asked.append(last.run.task_id)
        return result

    monkeypatch.setattr("aifactory.review.automerge.try_auto_merge", merge)
    return asked


@pytest.mark.parametrize("auto_continue", [False, True])
def test_failed_run_is_not_merged(
    repo: Path, monkeypatch: pytest.MonkeyPatch, auto_continue: bool
) -> None:
    """Was ``test_auto_merge.py::test_failed_run_is_not_merged``."""
    setup_merge(repo, step=f"auto_merge: true\nauto_continue: {str(auto_continue).lower()}\n")
    runs = fake(repo, monkeypatch)
    runs.fail(T01)
    asked = _merge_result(monkeypatch, AutoMergeResult(True, None, None, "URL"))
    chain = run_chain(repo, T01)
    assert chain.stop == (STOP_FAILED if auto_continue else STOP_DISABLED)
    assert not chain.ok
    assert len(chain.runs) == 1 and not chain.runs[0].ok
    assert chain.runs[0].auto_merge is None and asked == []
    assert pr_row(repo, T01) is None


def test_local_switch_overrides_task_auto_merge(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was ``test_auto_merge.py::test_local_switch_overrides_task_auto_merge``."""
    setup_merge(repo)
    RepositorySettings(repo).update({"auto_merge": False})
    fake(repo, monkeypatch)
    asked = _merge_result(monkeypatch, AutoMergeResult(True, None, None, "URL"))
    chain = run_chain(repo, T01)
    assert chain.stop == STOP_DISABLED
    assert chain.runs[0].auto_merge is None and asked == []
    row = pr_row(repo, T01)
    assert row is not None and row.state == "open" and row.auto_merge_error is None


def test_chain_stops_when_not_merged(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Was ``test_auto_merge.py::test_chain_stops_when_not_merged``."""
    setup_merge(repo, step="auto_merge: true\nauto_continue: true\n")
    fake(repo, monkeypatch)
    rejected = AutoMergeResult(False, "review_rejected", "missing tests", "URL")
    asked = _merge_result(monkeypatch, rejected)
    code = main(["task", "run", T01, "--repo", str(repo)])
    out = capsys.readouterr().out
    assert code == 0 and asked == [T01]
    assert "auto-merge: not merged (review_rejected)" in out
    assert f"factory task approve {T01}" in out
    assert "auto-continue: stopped (not_merged)" in out
    assert pr_row(repo, T02) is None


# ── live repository policy (test_live_auto_continue.py) ─────────────────────


def test_off_between_selection_and_start(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Was ``test_live_auto_continue.py::test_off_between_selection_and_start``."""
    setup_chain(repo)
    fake(repo, monkeypatch)
    select = queue.select_next

    def select_then_off(*args: Any, **kwargs: Any) -> queue.Selection:
        result = select(*args, **kwargs)
        assert result.next is not None
        RepositorySettings(repo).update({"auto_continue": False})
        return result

    monkeypatch.setattr(queue, "select_next", select_then_off)
    chain = run_chain(repo, T01)
    assert chain.stop == "disabled" and ids(chain) == [T01]
    assert chain.waiting[0].detail.startswith(DISABLED)
    assert runs_of(repo, T02) == []


def test_off_while_waiting_for_ci_blocks_following_task(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was ``test_live_auto_continue.py::test_off_while_waiting_for_ci_blocks_following_task``."""
    setup_merge(repo, step="auto_merge: true\nauto_continue: true\n")
    fake(repo, monkeypatch)

    def ci_then_off(*args: Any, **kwargs: Any) -> AutoMergeResult:
        RepositorySettings(repo).update({"auto_continue": False})
        return AutoMergeResult(False, "checks_pending", "CI is still running", None)

    monkeypatch.setattr("aifactory.review.automerge.try_auto_merge", ci_then_off)
    chain = run_chain(repo, T01)
    assert ids(chain) == [T01] and chain.stop == "disabled"
    assert chain.runs[0].ok and runs_of(repo, T02) == []


@pytest.mark.parametrize("auto", [False, True])
def test_cli_auto_cannot_enable_local_off(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], auto: bool
) -> None:
    """Was ``test_live_auto_continue.py::test_cli_auto_cannot_enable_local_off``."""
    setup_chain(repo, module_auto=True)
    RepositorySettings(repo).update({"auto_continue": False})
    fake(repo, monkeypatch)
    argv = ["task", "run", T01, "--repo", str(repo), "--json"]
    assert main([*argv, *(["--auto"] if auto else [])]) == 0
    output = capsys.readouterr()
    assert ("--auto is deprecated" in output.err) == auto
    assert runs_of(repo, T02) == []
    assert not RepositorySettings(repo).read().auto_continue


def test_off_blocks_automatic_heal(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Was ``test_live_auto_continue.py::test_off_blocks_automatic_heal``."""
    setup_chain(repo)
    runs = fake(repo, monkeypatch)
    runs.fail(T01, failure.PROCESS_GONE)
    result = task_mod.run_task(repo, T01)
    assert result.run.state == "failed" and result.run.failure_kind == failure.PROCESS_GONE
    task = load_backlog(repo).by_id[T01]
    assert isinstance(task, Task)
    RepositorySettings(repo).update({"auto_continue": False})

    def forbidden(*args: Any, **kwargs: Any) -> task_mod.TaskRunResult:
        raise AssertionError("automatic heal must not retry")

    assert heal(repo, result, task, retry=forbidden) is None


def test_fake_refuses_an_automatic_start_when_off(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fake claims an automatic run under the live policy, like ``run_task``."""
    setup_chain(repo)
    RepositorySettings(repo).update({"auto_continue": False})
    runs = fake(repo, monkeypatch)
    with pytest.raises(TaskRunError) as caught:
        task_mod.run_task(repo, T01, started_by="auto-continue")
    assert caught.value.code == DISABLED and runs.started == []
    assert task_mod.run_task(repo, T01).ok


# ── self-healing (test_auto_heal.py) ─────────────────────────────────────────


@pytest.fixture(name="heal_repo")
def heal_repo_fixture(tmp_path: Path) -> Callable[..., Path]:
    def make(heal: bool | None = None) -> Path:
        return heal_repo(tmp_path, heal=heal)

    return make


def test_process_gone_without_auto_heal_is_not_retried(
    heal_repo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was ``test_auto_heal.py::test_process_gone_without_auto_heal_is_not_retried``."""
    repo = heal_repo(heal=False)
    fake(repo, monkeypatch).fail(T01, failure.PROCESS_GONE, state=ABORTED, detail={"pid": 1})
    chain = run_chain(repo, T01)
    assert ids(chain) == [T01] and chain.stop == STOP_FAILED
    assert chain.runs[0].run.state == ABORTED
    assert chain.runs[0].run.retry_list() == []
    assert chain.events == [] and chain_events(repo) == []


def test_nothing_to_do_without_a_reason_is_left_to_the_operator(
    heal_repo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was ``test_auto_heal.py::test_nothing_to_do_without_a_reason_is_left_to_the_operator``."""
    repo = heal_repo()
    fake(repo, monkeypatch).fail(T01, failure.NOTHING_TO_DO, detail={"summary": ""})
    chain = run_chain(repo, T01)
    assert ids(chain) == [T01] and chain.stop == STOP_FAILED
    assert heal_status(repo, T01) == "todo"
    assert chain.events == []


def test_nothing_to_do_without_auto_heal_stops_the_chain(
    heal_repo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was ``test_auto_heal.py::test_nothing_to_do_without_auto_heal_stops_the_chain``."""
    repo = heal_repo(heal=False)
    summary = {"summary": "only the front matter must change"}
    fake(repo, monkeypatch).fail(T01, failure.NOTHING_TO_DO, detail=summary)
    chain = run_chain(repo, T01)
    assert ids(chain) == [T01] and chain.stop == STOP_FAILED
    assert not chain.ok
    assert heal_status(repo, T01) == "todo"
    assert chain.events == []


def test_other_failure_is_left_to_the_operator(
    heal_repo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was ``test_auto_heal.py::test_other_failure_is_left_to_the_operator``."""
    repo = heal_repo()
    fake(repo, monkeypatch).fail(T01)
    chain = run_chain(repo, T01)
    assert ids(chain) == [T01] and chain.stop == STOP_FAILED
    assert chain.runs[0].run.retry_list() == []
    assert chain.events == []


def test_needs_environment_is_never_healed(
    heal_repo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was ``test_auto_heal.py::test_needs_environment_is_never_healed``."""
    repo = heal_repo()
    summary = {"agent": "planner", "summary": "desktop cannot receive input"}
    runs = fake(repo, monkeypatch)
    runs.fail(T01, failure.NEEDS_ENVIRONMENT, detail=summary)
    chain = run_chain(repo, T01)
    assert ids(chain) == [T01, T02] and chain.stop == STOP_EXHAUSTED
    result = chain.runs[0]
    assert result.run.failure_kind == failure.NEEDS_ENVIRONMENT
    assert result.run.retry_list() == []
    assert heal_status(repo, T01) == "todo" and result.pr is None
    assert chain.events == [] and chain_events(repo) == []
    assert [task for task, _ in runs.started] == [T01, T02]
    task = load_backlog(repo).by_id[T01]
    assert isinstance(task, Task)

    def retry_forbidden(*args: Any, **kwargs: Any) -> task_mod.TaskRunResult:
        pytest.fail("needs_environment must never retry in the same environment")

    assert heal(repo, result, task, retry=retry_forbidden) is None
