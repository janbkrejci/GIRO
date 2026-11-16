"""Uncommitted shared-config changes against the base commit."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from config_repo import git, make_repo, write

from aifactory.config import ConfigChange, change_summary, change_warnings, config_changes
from aifactory.config.status import (
    CONFIG_PATHSPECS,
    ChangeStatus,
    is_shared_config_path,
    working_tree_changes,
)


def _head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def test_clean_repo_has_no_changes(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    assert config_changes(repo, _head(repo)) == []


def test_every_kind_of_change(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    write(repo, ".factory/agents.yaml", "agents: []\n")
    (repo / ".factory/prompts/builder/user.md").unlink()
    write(repo, ".factory/workflows/new.yaml", "name: new\n")
    write(repo, ".factory/roles.yaml", "roles: {}\n")
    git(repo, "add", ".factory/roles.yaml")
    assert config_changes(repo, _head(repo)) == [
        ConfigChange(".factory/agents.yaml", "modified"),
        ConfigChange(".factory/prompts/builder/user.md", "deleted"),
        ConfigChange(".factory/roles.yaml", "added"),
        ConfigChange(".factory/workflows/new.yaml", "untracked"),
    ]


def test_local_files_are_not_reported(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    write(repo, ".factory/local.yaml", "port: 1234\n")
    write(repo, ".factory/worktrees/x", "run\n")
    write(repo, ".factory/trace.db", "db\n")
    assert config_changes(repo, _head(repo)) == []


def test_shared_paths() -> None:
    assert is_shared_config_path(".factory/config.yaml")
    assert is_shared_config_path(".factory/prompts/a/system.md")
    assert not is_shared_config_path(".factory/local.yaml")
    assert not is_shared_config_path(".factory/trace.db")


def test_warnings_name_path_base_and_commit() -> None:
    sha = "1234567890abcdef"
    [warning] = change_warnings([ConfigChange(".factory/agents.yaml", "modified")], "main", sha)
    assert ".factory/agents.yaml" in warning
    assert "main" in warning
    assert "1234567" in warning
    assert "modified" in warning


@pytest.mark.parametrize("directory", [".factory/prompts", ".claude/skills", ".agents/skills"])
def test_wildcard_pathspec_matches_original_without_index_writes(
    tmp_path: Path, directory: str
) -> None:
    repo = make_repo(tmp_path / "repo")
    for name in ("modified", "deleted", "stale", "nested/deep file"):
        write(repo, f"{directory}/{name}", "original\n")
    write(repo, "outside.txt", "original\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "fixtures")
    sha = _head(repo)
    write(repo, f"{directory}/modified", "changed\n")
    write(repo, f"{directory}/nested/deep file", "changed\n")
    (repo / directory / "deleted").unlink()
    write(repo, f"{directory}/added", "added\n")
    git(repo, "add", f"{directory}/added")
    write(repo, f"{directory}/untracked", "untracked\n")
    write(repo, "outside.txt", "changed\n")
    write(repo, "outside-new.txt", "untracked\n")
    stale = repo / directory / "stale"
    stat = stale.stat()
    os.utime(stale, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    index = repo / ".git/index"
    before, before_stat = index.read_bytes(), index.stat()
    lock = repo / ".git/index.lock"
    lock.write_bytes(b"held by another process")
    expected: tuple[tuple[str, ChangeStatus], ...] = (
        ("modified", "modified"),
        ("nested/deep file", "modified"),
        ("deleted", "deleted"),
        ("added", "added"),
        ("untracked", "untracked"),
    )
    try:
        original = working_tree_changes(repo, sha, (".factory", ".claude/skills", ".agents/skills"))
        optimized = working_tree_changes(repo, sha, CONFIG_PATHSPECS)
        assert (
            optimized
            == original
            == sorted(
                [ConfigChange(f"{directory}/{name}", status) for name, status in expected],
                key=lambda change: change.path,
            )
        )
        assert index.read_bytes() == before
        assert index.stat().st_mtime_ns == before_stat.st_mtime_ns
        assert lock.read_bytes() == b"held by another process"
    finally:
        lock.unlink()


def test_wildcard_reports_wholly_deleted_skill_directory(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    write(repo, ".agents/skills/deep/skill.md", "skill\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "skill")
    sha = _head(repo)
    (repo / ".agents/skills/deep/skill.md").unlink()
    (repo / ".agents/skills/deep").rmdir()
    (repo / ".agents/skills").rmdir()
    assert (
        working_tree_changes(repo, sha, CONFIG_PATHSPECS)
        == working_tree_changes(repo, sha, (".factory", ".claude/skills", ".agents/skills"))
        == [ConfigChange(".agents/skills/deep/skill.md", "deleted")]
    )


def test_summary_lists_every_change_once() -> None:
    sha = "1234567890abcdef"
    one = change_summary([ConfigChange(".factory/agents.yaml", "modified")], "main", sha)
    assert one.startswith("1 file of .factory/")
    assert "main (1234567)" in one and ".factory/agents.yaml (modified)" in one
    two = change_summary(
        [
            ConfigChange(".factory/agents.yaml", "modified"),
            ConfigChange(".factory/workflows/x.yaml", "untracked"),
        ],
        "main",
        sha,
    )
    assert two.startswith("2 files")
    assert ".factory/agents.yaml (modified), .factory/workflows/x.yaml (untracked)" in two


def _scenario(repo: Path) -> None:
    write(repo, ".factory/agents.yaml", "agents: []\n")  # modified
    (repo / ".factory/prompts/builder/user.md").unlink()  # deleted
    write(repo, ".factory/workflows/new.yaml", "name: new\n")  # untracked
    write(repo, ".factory/roles.yaml", "roles: {}\n")
    git(repo, "add", ".factory/roles.yaml")  # added
    write(repo, ".factory/added-then-gone.yaml", "x: 1\n")
    git(repo, "add", ".factory/added-then-gone.yaml")
    (repo / ".factory/added-then-gone.yaml").unlink()  # in neither base nor the tree
    system = repo / ".factory/prompts/builder/system.md"
    system.write_text(system.read_text(encoding="utf-8"), encoding="utf-8")
    os.utime(system, ns=(1, 1))  # stale stat data, same content: not a change
    write(repo, "src/outside.py", "print(1)\n")  # outside the configuration


def test_config_status_from_one_git_status_matches_the_comparison_with_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aifactory.config.status import config_status
    from gitcount import count_git

    repo = make_repo(tmp_path / "repo")
    _scenario(repo)
    expected = config_changes(repo, _head(repo))
    with count_git(monkeypatch, tmp_path / "trace.json") as count:
        status = config_status(repo)
    assert status.changes == expected
    assert status.commit == _head(repo)
    # cold caches: git dirs and base (rev-parse), then one status instead of
    # diff-index, hash-object and ls-files
    assert count.commands == {"status": 1, "rev-parse": 2}


def test_config_status_compares_with_base_when_head_differs(tmp_path: Path) -> None:
    from aifactory.config.status import config_status

    repo = make_repo(tmp_path / "repo")
    base = _head(repo)
    git(repo, "checkout", "-q", "-b", "side")
    write(repo, ".factory/roles.yaml", "roles: {}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "side")
    _scenario(repo)
    status = config_status(repo, base)
    assert status.commit == base
    assert status.changes == config_changes(repo, base)
    assert ConfigChange(".factory/roles.yaml", "added") in status.changes
