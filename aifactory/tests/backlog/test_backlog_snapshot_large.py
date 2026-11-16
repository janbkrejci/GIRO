"""Committed backlog with 1655 files is read in one git batch (slow tier)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from backlog_repo import index_md, task_md, write

from aifactory.backlog import check_backlog
from aifactory.backlog.snapshot import load_backlog_at
from aifactory.config import ProjectSettings
from aifactory.config.source import git


def commit(root: Path) -> str:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    git(root, "add", ".")
    git(root, "commit", "-qm", "backlog")
    return git(root, "rev-parse", "HEAD").strip()


def test_1655_files_use_one_batch_and_no_python_file_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    write(root, "factory/backlog/P/index.md", index_md("P"))
    write(root, "factory/backlog/P/S/index.md", index_md("P-S"))
    for i in range(1653):
        write(root, f"factory/backlog/P/S/T{i}.md", task_md(f"P-S-T{i}"))
    write(root, "outside.md", "must not be included")
    sha = commit(root)
    from gitspy import GitCall, spy_git

    calls: list[list[str]] = []

    def spy(call: GitCall) -> None:
        calls.append(call.argv)
        assert "archive" not in call.argv

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("committed backlog must not open or create filesystem paths")

    with monkeypatch.context() as patch:
        spy_git(patch, spy)
        patch.setattr(Path, "open", forbidden)
        patch.setattr(Path, "mkdir", forbidden)
        backlog = load_backlog_at(root, sha, ProjectSettings())
    assert len(backlog.by_id) == 1655
    assert check_backlog(backlog) == []
    assert sum(argv == ["git", "cat-file", "--batch"] for argv in calls) == 1
