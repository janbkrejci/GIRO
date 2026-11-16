"""Backlog edits on ``MemoryBacklog``: a committed snapshot changed without touching disk."""

from __future__ import annotations

from pathlib import Path

import pytest
from backlog_repo import M01_S01, T01, T02, sample_repo

from aifactory.backlog import (
    Task,
    TaskEditError,
    add_container,
    add_task,
    edit_task,
    link_task,
    load_backlog,
    load_settings,
)
from aifactory.backlog.edit.common import MemoryBacklog
from aifactory.backlog.snapshot import read_snapshot
from gitutil import commit_all, git


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def _memory(tmp_path: Path) -> tuple[Path, MemoryBacklog]:
    root = sample_repo(tmp_path)
    git(root, "init", "-q", "-b", "main")
    sha = commit_all(root, "backlog")
    return root, MemoryBacklog(read_snapshot(root, sha, load_settings(root)))


def test_edits_change_only_the_memory_copy(tmp_path: Path) -> None:
    root, files = _memory(tmp_path)
    before = _tree(root)

    added = add_task(files, "M01-S01", "Nový úkol", body="Udělat to.")
    edit_task(files, "M01-S01-T02", title="Loader v paměti")
    step = add_container(files, "M01", "S09", "Nový krok")
    link_task(files, added.task.id, depends_on=["M01-S01-T02"])

    assert _tree(root) == before
    assert git(root, "status", "--porcelain") == ""
    assert added.path == f"{M01_S01}/M01-S01-T03-novy-ukol.md"
    assert step.path in files.files
    assert "Loader v paměti" in files.files[T02].decode("utf-8")
    after = files.load()
    task = after.by_id[added.task.id]
    assert isinstance(task, Task) and task.depends_on == ["M01-S01-T02"]
    assert after.by_id["M01-S09"].title == "Nový krok"
    assert load_backlog(root).by_id["M01-S01-T02"].title == "Loader"


def test_rejected_change_leaves_the_memory_copy(tmp_path: Path) -> None:
    _, files = _memory(tmp_path)
    before = dict(files.files)

    with pytest.raises(TaskEditError) as cycle:
        link_task(files, "M01-S01-T01", depends_on=["M01-S01-T02"])

    assert cycle.value.code == "backlog_invalid"
    assert [i.code for i in cycle.value.issues] == ["cycle"]
    assert files.files == before
    assert "M01-S01-T02" not in files.files[T01].decode("utf-8")


def test_write_outside_the_backlog_is_refused(tmp_path: Path) -> None:
    _, files = _memory(tmp_path)
    backlog = files.load()

    for rel in ("README.md", "backlog/../README.md", "backlog/M01-core/notes.txt"):
        with pytest.raises(TaskEditError) as refused:
            files.guard(backlog, rel)
        assert refused.value.code == "outside_backlog"
