"""Committed backlog reads preserve disk-loader semantics without temporary files."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from backlog_repo import T02, index_md, sample_repo, task_md, write

from aifactory.backlog import backlog_to_json, check_backlog, load_backlog, load_settings
from aifactory.backlog.snapshot import load_backlog_at, read_snapshot
from aifactory.config import ProjectSettings
from aifactory.config.source import git
from aifactory.run.gitops import extract_backlog


def commit(root: Path) -> str:
    if not (root / ".git").exists():
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.name", "Test")
        git(root, "config", "user.email", "test@example.com")
    git(root, "add", ".")
    git(root, "commit", "-qm", "backlog")
    return git(root, "rev-parse", "HEAD").strip()


@pytest.mark.parametrize("malformed", [False, True])
def test_snapshot_matches_archive_and_ignores_checkout(tmp_path: Path, malformed: bool) -> None:
    root = sample_repo(tmp_path)
    path = root / T02
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    if malformed:
        write(root, "backlog/M02-ui/S01-list/bad.md", "no header\n")
        write(root, "backlog/M02-ui/S01-list/deep/.keep", "")
        write(root, "backlog/orphan.md", task_md("orphan"))
        write(root, "backlog/no-index/.keep", "")
        write(root, "backlog/M02-ui/S01-list/duplicate.md", task_md("M01-S01-T02"))
    sha = commit(root)
    settings = load_settings(root)
    dest = tmp_path / "archive"
    dest.mkdir()
    extract_backlog(root, sha, settings.backlog_patterns, dest)
    expected = load_backlog(dest, settings)
    write(root, T02, task_md("UNCOMMITTED", "done"))
    write(root, "backlog/M02-ui/S01-list/new.md", task_md("NEW"))
    actual = load_backlog_at(root, sha, settings)
    assert backlog_to_json(actual, check_backlog(actual)) == backlog_to_json(
        expected, check_backlog(expected)
    )
    assert actual.roots == expected.roots
    assert read_snapshot(root, sha, settings).markdown_files()[T02] == git_bytes(root, sha, T02)
    assert "UNCOMMITTED" not in actual.by_id and "NEW" not in actual.by_id
    next_sha = commit(root)
    assert "NEW" in load_backlog_at(root, next_sha, settings).by_id
    assert "NEW" not in load_backlog_at(root, sha, settings).by_id


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


@pytest.mark.parametrize("create_missing", [False, True])
def test_missing_root_is_only_virtual(tmp_path: Path, create_missing: bool) -> None:
    write(tmp_path, "README.md", "empty repository")
    sha = commit(tmp_path)
    backlog = load_backlog_at(tmp_path, sha, ProjectSettings(), create_missing=create_missing)
    assert not backlog.by_id
    assert [i.code for i in backlog.issues] == ([] if create_missing else ["missing_backlog_dir"])
    assert not (tmp_path / "factory").exists()


@pytest.mark.parametrize("docs_root", ["žluťoučký prostor", "modules/*", "."])
def test_configured_roots(tmp_path: Path, docs_root: str) -> None:
    folder = docs_root.replace("*", "modul")
    write(tmp_path, f"{folder}/backlog/P/index.md", index_md("P"))
    write(tmp_path, f"{folder}/backlog/P/T.md", task_md("P-T", extra="writes: [src/]\n"))
    sha = commit(tmp_path)
    settings = ProjectSettings(docs_root=docs_root, levels=("project", "task"))
    expected = load_backlog(tmp_path, settings)
    actual = load_backlog_at(tmp_path, sha, settings)
    assert backlog_to_json(actual, check_backlog(actual)) == backlog_to_json(
        expected, check_backlog(expected)
    )


def git_bytes(root: Path, sha: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{sha}:{path}"], cwd=root)


@pytest.mark.parametrize("target", ["task.txt", "T.md", "../../../../outside.md"])
def test_committed_symlink_is_resolved_inside_snapshot_or_rejected(
    tmp_path: Path, target: str
) -> None:
    write(tmp_path, "factory/backlog/P/index.md", index_md("P"))
    write(tmp_path, "factory/backlog/P/task.txt", task_md("P-T"))
    commit(tmp_path)
    # Index-only symlinks also exercise this on Windows without symlink privileges.
    blob = (
        subprocess.check_output(
            ["git", "hash-object", "-w", "--stdin"], cwd=tmp_path, input=target.encode()
        )
        .decode()
        .strip()
    )
    git(tmp_path, "update-index", "--add", "--cacheinfo", "120000", blob, "factory/backlog/P/T.md")
    git(tmp_path, "commit", "-qm", "symlink")
    settings = ProjectSettings(levels=("project", "task"))
    if target.startswith("../"):
        with pytest.raises(RuntimeError, match="symlink escapes"):
            load_backlog_at(tmp_path, "HEAD", settings)
    elif target == "T.md":
        assert "P-T" not in load_backlog_at(tmp_path, "HEAD", settings).by_id
    else:
        assert "P-T" in load_backlog_at(tmp_path, "HEAD", settings).by_id


def test_git_failure_does_not_become_an_empty_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = sample_repo(tmp_path)
    sha = commit(root)
    settings = load_settings(root)
    with pytest.raises(RuntimeError, match="cannot read committed backlog"):
        load_backlog_at(root, "missing-commit", settings)
    from aifactory import gitrun
    from gitspy import GitCall, spy_git

    def fail_batch(call: GitCall) -> gitrun.Result | None:
        if call.argv == ["git", "cat-file", "--batch"]:
            return gitrun.Result(tuple(call.argv), 1, b"", b"cannot read objects")
        return None

    spy_git(monkeypatch, fail_batch)
    with pytest.raises(RuntimeError, match="cannot read objects"):
        load_backlog_at(root, sha, settings)


@pytest.mark.parametrize(
    ("directory", "target"),
    [("deep/sub", "alias/../task.txt"), ("deep/a/b/c/d/e", "alias/../../../../../task.txt")],
)
def test_symlink_parent_is_resolved_after_following_a_directory_link(
    tmp_path: Path, directory: str, target: str
) -> None:
    write(tmp_path, "factory/backlog/P/index.md", index_md("P"))
    write(tmp_path, "factory/backlog/P/deep/task.txt", task_md("P-T"))
    write(tmp_path, f"factory/backlog/P/{directory}/.keep", "")
    commit(tmp_path)
    for path, destination in [("alias", directory), ("T.md", target)]:
        blob = (
            subprocess.check_output(
                ["git", "hash-object", "-w", "--stdin"], cwd=tmp_path, input=destination.encode()
            )
            .decode()
            .strip()
        )
        git(
            tmp_path,
            "update-index",
            "--add",
            "--cacheinfo",
            "120000",
            blob,
            f"factory/backlog/P/{path}",
        )
    git(tmp_path, "commit", "-qm", "nested symlink")
    backlog = load_backlog_at(tmp_path, "HEAD", ProjectSettings(levels=("project", "task")))
    assert "P-T" in backlog.by_id
