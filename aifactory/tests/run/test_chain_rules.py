"""Chain rules of ``run_chain`` over a fake ``run_task`` (``chain_fake``): no engine runs.

Each test has the input and the expectations of a former full chain in
``test_auto_continue.py`` (named in its docstring); that file keeps the full chains
that cover the engine, the PRs and the follow-up filing.
"""

from __future__ import annotations

from pathlib import Path

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

from aifactory.repo_settings import RepositorySettings
from aifactory.run import STOP_DISABLED, STOP_EXHAUSTED, ChainResult, run_chain

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
