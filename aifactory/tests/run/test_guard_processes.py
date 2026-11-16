"""The write guard spends a constant number of git processes per agent phase."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aifactory.config import load_run_config
from aifactory.config.runtime import prepare_cfg
from aifactory.engine import agents
from aifactory.guard import TaskWriteGuard
from aifactory.scope import TaskScope
from gitcount import count_git

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_repo import git, make_run_repo, write  # noqa: E402


def _phase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, n: int) -> int:
    """Git processes of one snapshot and enforce with `n` dirty files in each checkout."""
    root = tmp_path / f"n{n}"
    repo = make_run_repo(root / "repo")
    worktree = root / "wt"
    git(repo, "worktree", "add", "-q", "-b", f"work{n}", str(worktree))
    for i in range(n):
        write(repo, f"src/main {i}.txt", "operator draft\n")  # pre-dirty main checkout
        write(worktree, f"src/pre {i}.txt", "earlier phase\n")  # pre-dirty worktree
    runtime = root / "runtime"
    cfg = prepare_cfg(repo, load_run_config(repo), runtime / "prompts")
    agent = agents.resolve(cfg, "builder")
    run = SimpleNamespace(
        repo_root=worktree, cfg=cfg, adw_id="run-1", session_dir=runtime, current_agent=agent
    )
    guard = TaskWriteGuard(repo, worktree, TaskScope("T", ("src/",), ()), (".factory/data/",))
    with count_git(monkeypatch, root / "trace") as count:
        before = guard.snapshot(run)
        for i in range(n):
            write(worktree, f"src/new {i}.txt", "agent work\n")
        changed = guard.enforce(run, None, agent, before)
    assert len(changed) == n
    return count.total


def test_git_processes_per_phase_do_not_grow_with_changed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    one = _phase(tmp_path, monkeypatch, 1)
    many = _phase(tmp_path, monkeypatch, 12)
    assert one == many, (one, many)


def test_backup_reads_head_of_many_paths_in_constant_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aifactory import backup

    totals = []
    for n in (1, 10):
        repo = make_run_repo(tmp_path / f"r{n}")
        for i in range(n):
            write(repo, f"src/f{i}.txt", f"v{i}\n")
            if i % 2:
                (repo / f"src/l{i}").symlink_to(f"f{i}.txt")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "files")
        saved = backup.capture(repo, tmp_path / f"b{n}")
        for i in range(n):
            write(repo, f"src/f{i}.txt", "agent\n")
        with count_git(monkeypatch, tmp_path / f"t{n}") as count:
            off = backup.verify(saved)
            outcomes = backup.restore(saved, (), off)
        assert sorted(off) == sorted(f"src/f{i}.txt" for i in range(n))
        assert set(outcomes.values()) == {"rolled back"}
        assert all((repo / f"src/f{i}.txt").read_text() == f"v{i}\n" for i in range(n))
        totals.append(count.total)
    assert totals[0] == totals[1], totals
