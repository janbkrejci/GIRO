"""Trial merge and merge commits with ``merge-tree`` (git 2.38+) and the worktree fallback.

Every scenario runs on both paths: ``worktree`` (forced, always runs) and ``merge-tree``
(skipped with an older git). The merge-tree commits are compared with those the worktree
path builds for the same input: tree, parents and message.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from provider_repo import commit_file, git, isolate_git, make_repo

from aifactory import gitrun
from aifactory.providers import ProviderError
from aifactory.providers import git as pgit
from gitspy import GitCall, result, spy_git

BRANCH = "factory/T01-1"
PATHS = ("worktree", "merge-tree")


@pytest.fixture(autouse=True)
def _git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git(monkeypatch)


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    return make_repo(tmp_path, with_remote=False)


def use(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Take `path`; skip the merge-tree path when this git has no ``merge-tree --write-tree``."""
    if path == "worktree":
        monkeypatch.setattr(pgit, "merge_tree_supported", lambda: False)
    elif not pgit.merge_tree_supported():
        pytest.skip(f"git {pgit.git_version()} has no merge-tree --write-tree (2.38+)")


def shape(repo: Path, sha: str) -> tuple[str, list[str], str]:
    """Tree, parents and message of commit `sha` (the message without its final newline)."""
    tree = git(repo, "rev-parse", f"{sha}^{{tree}}")
    parents = git(repo, "log", "-1", "--format=%P", sha).split()
    message = git(repo, "cat-file", "commit", sha).split("\n\n", 1)[1]
    return tree, parents, message


def diverged(repo: Path, *, conflict: bool = False) -> tuple[str, str]:
    """(base, tip): main and the branch each add a commit; a conflict edits the same line."""
    if conflict:
        tip = commit_file(repo, BRANCH, "a.txt", "one\nTWO-branch\nthree\n", "branch edit")
        base = commit_file(repo, "main", "a.txt", "one\nTWO-main\nthree\n", "main edit")
    else:
        tip = commit_file(repo, BRANCH, "b.txt", "b\n", "add b")
        commit_file(repo, BRANCH, "c.txt", "c\n", "add c")
        tip = git(repo, "rev-parse", BRANCH)
        base = commit_file(repo, "main", "a.txt", "one\ntwo\nthree\nfour\n", "main edit")
    return base, tip


def worktrees(repo: Path) -> list[str]:
    return [
        line for line in git(repo, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]  # fmt: skip


@pytest.mark.parametrize("path", PATHS)
def test_trial_merge_clean_and_conflict(
    repo: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    use(path, monkeypatch)
    base, tip = diverged(repo)
    assert pgit.trial_merge(repo, base, tip) is True
    assert pgit.trial_merge(repo, tip, base) is True
    clash = commit_file(repo, "factory/T02-1", "a.txt", "one\nTWO-other\nthree\nfour\n", "x")
    assert pgit.trial_merge(repo, git(repo, "rev-parse", "main"), clash) is True
    moved = commit_file(repo, "main", "a.txt", "one\nTWO-main\nthree\nfour\n", "y")
    assert pgit.trial_merge(repo, moved, clash) is False
    assert len(worktrees(repo)) == 1 and git(repo, "status", "--porcelain") == ""


@pytest.mark.parametrize("strategy", ["merge", "squash"])
def test_worktree_path_builds_the_commit(
    repo: Path, monkeypatch: pytest.MonkeyPatch, strategy: str
) -> None:
    use("worktree", monkeypatch)
    base, tip = diverged(repo)
    sha = pgit.merge_commit(repo, base, tip, strategy, "T01: done  \n\n\n")
    tree, parents, message = shape(repo, sha)
    assert parents == ([base] if strategy == "squash" else [base, tip])
    assert message == "T01: done"
    files = git(repo, "ls-tree", "--name-only", tree).split()
    assert files == ["a.txt", "b.txt", "c.txt"]
    assert git(repo, "show", f"{sha}:a.txt") == "one\ntwo\nthree\nfour"
    assert len(worktrees(repo)) == 1


@pytest.mark.parametrize("strategy", ["merge", "squash"])
def test_merge_tree_commit_equals_the_worktree_commit(
    repo: Path, monkeypatch: pytest.MonkeyPatch, strategy: str
) -> None:
    use("merge-tree", monkeypatch)
    base, tip = diverged(repo)
    subject = "  T01: done  \n\nbody line\t\n\n\nlast\n"
    fast = pgit.merge_commit(repo, base, tip, strategy, subject)
    calls = spy_git(monkeypatch)
    monkeypatch.setattr(pgit, "merge_tree_supported", lambda: False)
    slow = pgit.merge_commit(repo, base, tip, strategy, subject)
    assert any("worktree" in c.argv for c in calls)
    assert shape(repo, fast) == shape(repo, slow)


def test_merge_tree_path_starts_no_worktree(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use("merge-tree", monkeypatch)
    base, tip = diverged(repo)
    calls = spy_git(monkeypatch)
    pgit.merge_commit(repo, base, tip, "squash", "T01")
    pgit.trial_merge(repo, base, tip)
    assert not [c.argv for c in calls if "worktree" in c.argv or "merge" in c.argv]


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("strategy", ["merge", "squash"])
def test_conflict_raises_and_leaves_no_trace(
    repo: Path, monkeypatch: pytest.MonkeyPatch, path: str, strategy: str
) -> None:
    use(path, monkeypatch)
    base, tip = diverged(repo, conflict=True)
    with pytest.raises(ProviderError) as info:
        pgit.merge_commit(repo, base, tip, strategy, "T01")
    assert info.value.code == "conflict"
    assert info.value.message.startswith(f"{tip[:7]} does not merge cleanly: ")
    assert git(repo, "rev-parse", "main") == base
    assert len(worktrees(repo)) == 1 and git(repo, "status", "--porcelain") == ""


@pytest.mark.parametrize("path", PATHS)
def test_merge_of_a_tip_already_in_base_returns_base(
    repo: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    use(path, monkeypatch)
    base, tip = diverged(repo)
    merged = pgit.merge_commit(repo, base, tip, "merge", "T01")
    assert pgit.merge_commit(repo, merged, tip, "merge", "again") == merged


@pytest.mark.parametrize("path", PATHS)
def test_squash_without_changes_fails_like_git_commit(
    repo: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    use(path, monkeypatch)
    base, tip = diverged(repo)
    merged = pgit.merge_commit(repo, base, tip, "squash", "T01")
    with pytest.raises(RuntimeError):
        pgit.merge_commit(repo, merged, tip, "squash", "again")


@pytest.fixture(name="fresh_version")
def fresh_version_fixture() -> Iterator[None]:
    pgit.git_version.cache_clear()
    yield
    pgit.git_version.cache_clear()


@pytest.mark.parametrize(
    ("text", "version", "supported"),
    [
        ("git version 2.37.1 (Apple Git-137.1)", (2, 37, 1), False),
        ("git version 2.38.0", (2, 38, 0), True),
        ("git version 2.45.2.windows.1", (2, 45, 2), True),
        ("git version 3.0", (3, 0), True),
        ("no version here", (), False),
    ],
)
def test_git_version_is_parsed_once_per_process(
    monkeypatch: pytest.MonkeyPatch, fresh_version: None, text: str, version: tuple[int, ...],
    supported: bool,
) -> None:  # fmt: skip
    calls = spy_git(monkeypatch, lambda call: result(call.argv, text + "\n"))
    assert pgit.git_version() == version
    assert pgit.merge_tree_supported() is supported
    assert pgit.git_version() == version
    assert [c.argv for c in calls] == [["git", "--version"]]


def test_failing_git_version_takes_the_worktree_path(
    monkeypatch: pytest.MonkeyPatch, fresh_version: None
) -> None:
    spy_git(monkeypatch, lambda call: result(call.argv, "", returncode=1))
    assert pgit.git_version() == () and not pgit.merge_tree_supported()


@pytest.mark.parametrize(
    ("message", "stored"),
    [
        ("T01: done", "T01: done\n"),
        ("\n\n  T01: done  \n\n\n\nbody\t\n\n", "  T01: done\n\nbody\n"),
        ("# kept\nline", "# kept\nline\n"),
        ("   \n\n", ""),
    ],
)
def test_clean_message_matches_git_commit_m(repo: Path, message: str, stored: str) -> None:
    assert pgit.clean_message(message) == stored
    if stored:
        git(repo, "commit", "-q", "--allow-empty", "-m", message)
        assert git(repo, "cat-file", "commit", "HEAD").split("\n\n", 1)[1] + "\n" == stored


def emulate_merge_tree(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Answer ``git merge-tree --write-tree`` the way git 2.38 does, on any git.

    The tree comes from ``git merge`` in a throwaway worktree; a conflict exits 1. The rest
    of the merge-tree path (``commit-tree``, parents, message) runs for real.
    """
    monkeypatch.setattr(pgit, "merge_tree_supported", lambda: True)
    asked: list[list[str]] = []

    def answer(call: GitCall) -> gitrun.Result | None:
        if "merge-tree" not in call.argv:
            return None
        asked.append(call.argv)
        base, tip = call.argv[-2:]
        assert call.cwd is not None
        with pgit.detached_worktree(call.cwd, base) as wt:
            merged = gitrun.run(wt, "merge", "--no-commit", "--no-ff", tip)
            tree = gitrun.out(wt, "write-tree") if merged.ok else "0" * 40
            gitrun.run(wt, "merge", "--abort")
        if not merged.ok:
            return result(call.argv, f"{tree}\nCONFLICT (content): a.txt\n", returncode=1)
        return result(call.argv, f"{tree}\n\nAuto-merging a.txt\n")

    spy_git(monkeypatch, answer)
    return asked


@pytest.mark.parametrize("strategy", ["merge", "squash"])
def test_emulated_merge_tree_commit_equals_the_worktree_commit(
    repo: Path, monkeypatch: pytest.MonkeyPatch, strategy: str
) -> None:
    base, tip = diverged(repo)
    subject = "  T01: done  \n\nbody line\t\n\n\nlast\n"
    with monkeypatch.context() as patch:
        asked = emulate_merge_tree(patch)
        fast = pgit.merge_commit(repo, base, tip, strategy, subject)
        assert pgit.trial_merge(repo, base, tip) is True
    assert [argv[1:4] for argv in asked] == [["merge-tree", "--write-tree", "--messages"]] * 2
    use("worktree", monkeypatch)
    slow = pgit.merge_commit(repo, base, tip, strategy, subject)
    assert shape(repo, fast) == shape(repo, slow)


@pytest.mark.parametrize("strategy", ["merge", "squash"])
def test_emulated_merge_tree_conflict(
    repo: Path, monkeypatch: pytest.MonkeyPatch, strategy: str
) -> None:
    base, tip = diverged(repo, conflict=True)
    emulate_merge_tree(monkeypatch)
    assert pgit.trial_merge(repo, base, tip) is False
    with pytest.raises(ProviderError) as info:
        pgit.merge_commit(repo, base, tip, strategy, "T01")
    assert info.value.code == "conflict" and "CONFLICT (content): a.txt" in info.value.message
    assert git(repo, "rev-parse", "main") == base


def test_emulated_merge_tree_no_op_merges_behave_like_git_merge(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, tip = diverged(repo)
    asked = emulate_merge_tree(monkeypatch)
    merged = pgit.merge_commit(repo, base, tip, "merge", "T01")
    assert pgit.merge_commit(repo, merged, tip, "merge", "again") == merged
    with pytest.raises(RuntimeError):
        pgit.merge_commit(repo, merged, tip, "squash", "again")
    assert len(asked) == 1  # only the first merge: a tip already in base asks nothing


def test_emulated_merge_tree_squash_without_changes_fails_like_git_commit(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, tip = diverged(repo)
    asked = emulate_merge_tree(monkeypatch)
    squashed = pgit.merge_commit(repo, base, tip, "squash", "T01")
    calls = spy_git(monkeypatch)
    with pytest.raises(RuntimeError):
        pgit.merge_commit(repo, squashed, tip, "squash", "again")
    assert len(asked) == 2  # the tree equals the base tree: the worktree path decides
    assert any("worktree" in c.argv for c in calls)
    assert not any("commit-tree" in c.argv for c in calls)
