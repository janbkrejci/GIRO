"""Offline hosted merges with Windows Git settings and an archived task."""

import os
from contextlib import closing
from pathlib import Path

import pytest
from test_base_changes_pr import _repo

from aifactory import basemoves, providers
from aifactory.backlog import commit_backlog, edit_task
from aifactory.config import ProjectSettings
from aifactory.providers import git
from aifactory.providers.base import PullRequest
from aifactory.providers.local import LocalProvider
from aifactory.web.backlog import status


class HostedMerge(LocalProvider):
    name = "azure"
    remote_change = "none"

    def merge(
        self, pr: PullRequest, head_sha: str, subject: str, strategy: str | None = None
    ) -> str:
        base = git.git(self.root, "rev-parse", "main")
        if self.remote_change != "none":
            changes = []
            if self.remote_change == "content":
                blob = git.hash_blob(self.root, b"another operator's change\n")
                attributes = git.hash_blob(self.root, b"* text=auto eol=lf\n# remote change\n")
                changes = [
                    ("unrelated.txt", "100644", blob),
                    (".gitattributes", "100644", attributes),
                ]
            base = git.commit_tree_with(self.root, base, changes, "remote work")
        merged = git.merge_commit(self.root, base, head_sha, self._strategy(strategy), subject)
        git.push_ref(self.root, "origin", merged, "main")
        return merged


def _prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, strategy: str, remote_change: str
) -> Path:
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")
    repo = _repo(tmp_path, "auto")
    git.git(repo, "config", "core.autocrlf", "true")
    (repo / ".gitattributes").write_bytes(b"* text=auto eol=lf\n")
    edit_task(repo, "M01-S01-T01", assignee="human")
    edit_task(repo, "M01-S01-T01", status="done")
    git.git(repo, "add", "--all")
    git.git(repo, "commit", "-m", "completed task")
    git.git(repo, "push", "origin", "main")
    edit_task(repo, "M01-S01-T01", archived=True)

    def provider(settings: ProjectSettings, root: Path) -> HostedMerge:
        hosted = HostedMerge(root, settings.model_copy(update={"merge_strategy": strategy}))
        hosted.remote_change = remote_change
        return hosted

    monkeypatch.setattr(providers, "get_provider", provider)
    return repo


@pytest.mark.parametrize("strategy", ["squash", "merge"])
@pytest.mark.parametrize("remote_change", ["none", "content", "empty"])
def test_archive_hosted_merge_catches_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, strategy: str, remote_change: str
) -> None:
    repo = _prepare(tmp_path, monkeypatch, strategy, remote_change)
    result = commit_backlog(repo)
    assert result.committed and result.merged
    current = git.git(repo, "rev-parse", "main")
    dashboard, _ = status(repo)
    assert current == git.git(repo, "rev-parse", "origin/main")
    assert dashboard["clean"] is True
    assert git.git(repo, "status", "--porcelain") == ""
    assert not result.warnings
    if remote_change == "content":
        assert (repo / "unrelated.txt").read_bytes() == b"another operator's change\n"


@pytest.mark.parametrize(
    "work", ["staged", "staged_archive", "dirty", "untracked", "collision", "diverged"]
)
def test_catch_up_preserves_operator_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, work: str
) -> None:
    repo = _prepare(tmp_path, monkeypatch, "squash", "content")
    before = git.git(repo, "rev-parse", "main")
    path = repo / ("unrelated.txt" if work == "collision" else "notes.txt")
    path.write_bytes(b"keep my work\n")
    if work == "staged":
        git.git(repo, "add", "--", "notes.txt")
    elif work == "staged_archive":
        path = next((repo / "backlog").rglob("M01-S01-T01-*.md"))
        archived = path.read_bytes()
        path.write_bytes(archived.replace(b"archived: true", b"archived: false"))
        git.git(repo, "add", "--", path.relative_to(repo).as_posix())
        path.write_bytes(archived)
    elif work == "dirty":
        # A tracked local edit outside the archive also blocks the guarded pull.
        path = repo / ".gitattributes"
        path.write_bytes(b"* text=auto eol=lf\n# local change\n")
    elif work == "diverged":
        from aifactory.providers import publish

        original = publish.pull_base

        def moved(*args: object, **kwargs: object) -> publish.PullResult:
            # A local commit races the hosted merge, before catch-up starts.
            local = git.commit_tree_with(repo, before, [], "local concurrent commit")
            git.git(repo, "update-ref", "refs/heads/main", local, before)
            return original(repo, settings=ProjectSettings(), store=None)

        monkeypatch.setattr(publish, "pull_base", moved)
    index = git.git(repo, "diff", "--cached")
    content = path.read_bytes()
    result = commit_backlog(repo)
    assert result.merged
    assert path.read_bytes() == content
    if work == "untracked":
        assert not result.warnings
        assert git.git(repo, "rev-parse", "main") == git.git(repo, "rev-parse", "origin/main")
        assert status(repo)[0]["clean"] is True
    else:
        assert result.warnings
        if work != "diverged":
            assert git.git(repo, "rev-parse", "main") == before
        assert git.git(repo, "diff", "--cached") == index


def test_catch_up_journals_for_live_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aifactory.runstore.rows import TaskRunRow
    from aifactory.runstore.store import TaskRunStore

    repo = _prepare(tmp_path, monkeypatch, "squash", "content")
    monkeypatch.delenv(basemoves.RUN_ENV, raising=False)
    before = git.git(repo, "rev-parse", "main")
    start = basemoves.offset(repo)
    with closing(TaskRunStore(tmp_path / "trace.db")) as store:
        store.claim(
            TaskRunRow(
                run_id="live",
                task_id="T01",
                branch="task",
                worktree="",
                base="main",
                base_sha=before,
                head_sha=None,
                state="running",
                started_at="2026-01-01",
                pid=os.getpid(),
            )
        )
        result = commit_backlog(repo)
        assert result.merged and not result.warnings
        assert store.live_runs()[0].run_id == "live"
    move = basemoves.moves_since(repo, 0)[-1]
    assert move.old == before
    assert move.new == git.git(repo, "rev-parse", "origin/main")
    assert (
        basemoves.wait_for_chain(repo, start, "refs/heads/main", before, move.new, "live", tries=1)
        == move
    )
    assert status(repo)[0]["clean"] is True


def test_config_publication_keeps_explicit_pull_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aifactory.providers import publish

    repo = _prepare(tmp_path, monkeypatch, "squash", "content")
    before = git.git(repo, "rev-parse", "main")
    original = publish.finish_pr

    def config_command(*args: object, **kwargs: object) -> publish.PublishResult:
        kwargs["command"] = "config commit"
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(publish, "finish_pr", config_command)
    result = commit_backlog(repo)
    assert result.merged and result.warnings
    assert git.git(repo, "rev-parse", "main") == before
    assert status(repo)[0]["clean"] is False
