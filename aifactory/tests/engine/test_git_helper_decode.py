from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aifactory.engine import git_helper
from gitutil import git


def test_diff_of_a_non_utf8_file_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # legacy VB6 sources are cp1250: their diff is no valid UTF-8
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    legacy = tmp_path / "ROBEST.FRM"
    legacy.write_bytes("Příjmení\n".encode("cp1250"))
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout.strip()
    legacy.write_bytes("Příjmení a jméno\n".encode("cp1250"))
    monkeypatch.chdir(tmp_path)

    text = git_helper.diff_text(base)

    assert "ROBEST.FRM" in text
    assert "�" in text


def test_guard_snapshot_names_non_ascii_paths_unquoted(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from aifactory.engine import permissions

    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    folder = tmp_path / "Testovací sady"
    folder.mkdir()
    (folder / "a.md").write_text("a\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    (folder / "a.md").write_text("b\n", encoding="utf-8")
    (folder / "nový.md").write_text("c\n", encoding="utf-8")

    snap = permissions.snapshot(SimpleNamespace(repo_root=tmp_path))

    assert set(snap) == {"Testovací sady/a.md", "Testovací sady/nový.md"}


def test_engine_git_goes_through_the_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from aifactory.engine import permissions
    from gitspy import spy_git

    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.md").write_text("a\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    monkeypatch.chdir(tmp_path)
    calls = spy_git(monkeypatch)

    assert git_helper.repo_root() == tmp_path.resolve()
    assert git_helper.ref_exists("HEAD")
    permissions.snapshot(SimpleNamespace(repo_root=tmp_path))

    commands = [call.argv[1:] for call in calls]
    assert ["rev-parse", "--show-toplevel"] in [c[-2:] for c in commands]
    assert any(c[-3:] == ["diff", "HEAD", "--numstat"] for c in commands)
    # Reads of the write guard take no optional locks.
    reads = [c for c in calls if c.cwd == tmp_path]
    assert reads and all(c.env and c.env.get("GIT_OPTIONAL_LOCKS") == "0" for c in reads)
