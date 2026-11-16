"""Cached git queries and batch commands (``aifactory.gitquery``)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aifactory import gitquery, gitrun
from gitcount import count_git


def _git(root: Path, *args: str) -> str:
    return gitrun.out(root, *args)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("a\n")
    (root / "dir").mkdir()
    (root / "dir" / "b.txt").write_text("b\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "one")
    return root


def test_toplevel_is_cached(repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert gitquery.toplevel(repo / "dir") == repo.resolve()
    with count_git(monkeypatch, tmp_path / "trace") as count:
        assert gitquery.toplevel(repo / "dir") == repo.resolve()
    assert count.total == 0
    outside = tmp_path / "outside"
    outside.mkdir()
    assert gitquery.toplevel(outside) is None
    with pytest.raises(gitrun.GitCommandFailed):
        gitquery.toplevel(outside, required=True)


def test_resolve_follows_commits(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = _git(repo, "rev-parse", "HEAD")
    assert gitquery.resolve(repo, "main") == first
    with count_git(monkeypatch, tmp_path / "trace") as count:
        assert gitquery.resolve(repo, "main") == first
        assert gitquery.resolve(repo, "refs/heads/main") == first
        assert gitquery.resolve(repo, "refs/heads/main") == first
    assert count.total == 1  # refs/heads/main once, then cached
    _git(repo, "commit", "-q", "--allow-empty", "-m", "two")
    second = _git(repo, "rev-parse", "HEAD")
    assert gitquery.resolve(repo, "main") == second
    _git(repo, "pack-refs", "--all")
    assert gitquery.resolve(repo, "main") == second
    _git(repo, "update-ref", "refs/heads/main", first)
    assert gitquery.resolve(repo, "main") == first
    assert gitquery.resolve(repo, "missing") is None
    assert gitquery.resolve(repo, "HEAD~0") == first


def test_head_is_never_cached(repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gitquery.resolve(repo, "HEAD")
    with count_git(monkeypatch, tmp_path / "trace") as count:
        gitquery.resolve(repo, "HEAD")
    assert count.total == 1


def test_remote_url_follows_config(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert gitquery.remote_url(repo, "origin") is None
    _git(repo, "remote", "add", "origin", "https://example.invalid/a.git")
    assert gitquery.remote_url(repo, "origin") == "https://example.invalid/a.git"
    with count_git(monkeypatch, tmp_path / "trace") as count:
        assert gitquery.remote_url(repo, "origin") == "https://example.invalid/a.git"
    assert count.total == 0
    _git(repo, "remote", "set-url", "origin", "https://example.invalid/b.git")
    assert gitquery.remote_url(repo, "origin") == "https://example.invalid/b.git"


def test_status_lists_every_dirty_path(repo: Path) -> None:
    (repo / "a.txt").write_text("changed\n")
    (repo / "new dir").mkdir()
    (repo / "new dir" / "n.txt").write_text("n\n")
    _git(repo, "mv", "dir/b.txt", "c.txt")
    entries = {e.path: e for e in gitquery.status(repo)}
    assert set(entries) == {"a.txt", "new dir/n.txt", "dir/b.txt", "c.txt"}
    assert entries["a.txt"].kind == "changed" and entries["a.txt"].worktree == "M"
    assert entries["new dir/n.txt"].kind == "untracked"
    assert entries["dir/b.txt"].index == "D" and entries["c.txt"].index == "A"


def test_objects_in_one_process(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    names = ["HEAD:a.txt", "HEAD:dir/b.txt", "HEAD:missing", "HEAD:dir"]
    with count_git(monkeypatch, tmp_path / "trace") as count:
        info = gitquery.object_info(repo, names)
        content = gitquery.read_objects(repo, names[:3])
        entries = gitquery.ls_tree(repo, "HEAD", ["a.txt", "dir/b.txt", "nope"])
        dirs = gitquery.ls_tree(repo, "HEAD", ["dir"])
    assert count.total == 4
    assert info["HEAD:missing"] is None
    assert info["HEAD:a.txt"] is not None and info["HEAD:a.txt"].type == "blob"
    assert info["HEAD:dir"] is not None and info["HEAD:dir"].type == "tree"
    assert gitquery.exists(repo, names) == {
        "HEAD:a.txt": True,
        "HEAD:dir/b.txt": True,
        "HEAD:missing": False,
        "HEAD:dir": True,
    }
    assert content == {"HEAD:a.txt": b"a\n", "HEAD:dir/b.txt": b"b\n", "HEAD:missing": None}
    assert set(entries) == {"a.txt", "dir/b.txt"}
    assert entries["a.txt"].mode == "100644" and dirs["dir"].type == "tree"


def test_blobs_and_index(repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    contents = [b"x\n", b"", b"\x00binary\xff"]
    with count_git(monkeypatch, tmp_path / "trace") as count:
        ids = gitquery.write_blobs(repo, contents)
    assert count.commands["fast-import"] == 1 and count.total <= 2
    for content, sha in zip(contents, ids, strict=True):
        expected = (
            subprocess.run(
                ["git", "hash-object", "--stdin"], cwd=repo, input=content, capture_output=True
            )
            .stdout.decode()
            .strip()
        )
        assert sha == expected == gitquery.blob_id(content, like=sha)
        assert gitquery.read_objects(repo, [sha])[sha] == content
    index = tmp_path / "index"
    env = {"GIT_INDEX_FILE": str(index)}
    gitrun.run(repo, "read-tree", "HEAD", env=env, check=True)
    gitquery.update_index(
        repo,
        [("x.txt", "100644", ids[0]), ("dir/b.txt", None, None), ("bin", "100755", ids[2])],
        env=env,
    )
    listed = gitrun.run(repo, "ls-files", "-s", env=env, check=True).text
    assert {line.split("\t")[1] for line in listed.splitlines()} == {"a.txt", "x.txt", "bin"}
    assert f"100755 {ids[2]}" in listed


def test_head_state(repo: Path, tmp_path: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    assert gitquery.head_state(repo) == (head, "refs/heads/main")
    _git(repo, "checkout", "-q", "--detach")
    assert gitquery.head_state(repo) == (head, None)
    unborn = tmp_path / "unborn"
    unborn.mkdir()
    _git(unborn, "init", "-q", "-b", "trunk")
    assert gitquery.head_state(unborn) == (None, "refs/heads/trunk")


@pytest.mark.parametrize("batch", [True, False])
def test_read_filtered_applies_checkout_filters(
    repo: Path, monkeypatch: pytest.MonkeyPatch, batch: bool
) -> None:
    from gitspy import GitCall, spy_git
    from gitspy import result as fake

    (repo / ".gitattributes").write_text("*.crlf text eol=crlf\n")
    (repo / "x.crlf").write_bytes(b"one\ntwo\n")
    (repo / "empty.crlf").write_bytes(b"")
    (repo / "plain bin").write_bytes(b"a\nb\x00\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "attrs")
    items = [
        (_git(repo, "rev-parse", f"HEAD:{p}"), p) for p in ("x.crlf", "empty.crlf", "plain bin")
    ]
    items.append(("0" * 40, "gone.crlf"))

    def broken(call: GitCall) -> object:
        return fake(call.argv, "", 128) if any("--batch" in a for a in call.argv) else None

    calls = spy_git(monkeypatch, None if batch else broken)  # type: ignore[arg-type]
    found = gitquery.read_filtered(repo, items)
    assert [found[i] for i in items] == [b"one\r\ntwo\r\n", b"", b"a\nb\x00\n", None]
    assert len(calls) == (1 if batch else 1 + len(items))
