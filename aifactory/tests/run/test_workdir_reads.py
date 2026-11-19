"""Deduplicate base tree reads without weakening directory/symlink validation."""

from pathlib import Path

import pytest

from aifactory import gitops
from aifactory.errors import TaskRunError
from aifactory.run import workdir


def test_shared_ancestors_and_workdir_read_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def git(root: Path, *args: str) -> str:
        calls.append(args)
        return "040000 tree abc\t" + args[-1]

    monkeypatch.setattr(gitops, "git", git)
    for commit in ("old", "new"):
        workdir.validate_base_dirs(
            Path("."),
            commit,
            ("packages/core", "packages/core/docs", "packages/core"),
            "packages/core",
        )
    assert [c[-1] for c in calls] == ["packages", "packages/core", "packages/core/docs"] * 2
    assert [c[1] for c in calls] == ["old"] * 3 + ["new"] * 3


@pytest.mark.parametrize("mode", ["120000", "100644"])
def test_cached_parent_never_hides_non_directory(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(gitops, "git", lambda *args: f"{mode} blob abc\tpackages")
    with pytest.raises(TaskRunError, match="not a directory"):
        workdir.validate_base_dirs(Path("."), "sha", ("packages/core/docs",), ".")


def test_missing_workdir_rejected_even_if_cached_as_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gitops, "git", lambda *args: "")
    with pytest.raises(TaskRunError, match="workdir"):
        workdir.validate_base_dirs(Path("."), "sha", ("missing/docs", "missing"), "missing")
