"""An unreachable remote must not be mistaken for an empty branch namespace."""

from pathlib import Path

import pytest
from provider_repo import git, isolate_git, make_repo

from aifactory.config import ProjectSettings
from aifactory.providers import ProviderError
from aifactory.providers import git as pgit
from aifactory.providers.local import LocalProvider
from aifactory.providers.publish import PR, PublishPlan, publish_pr


@pytest.mark.parametrize("remote", [False, True])
def test_allocation_uses_remote_and_local_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remote: bool
) -> None:
    isolate_git(monkeypatch)
    repo = make_repo(tmp_path, with_remote=remote)
    git(repo, "branch", "factory-backlog/494")
    if remote:
        git(repo, "push", "origin", "HEAD:refs/heads/factory-backlog/495")
        git(repo, "update-ref", "-d", "refs/remotes/origin/factory-backlog/495")
    assert pgit.next_numbered_branch(repo, "origin", "factory-backlog/") == (
        "factory-backlog/496" if remote else "factory-backlog/495"
    )


def test_failed_discovery_does_not_create_or_push_a_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_git(monkeypatch)
    repo = make_repo(tmp_path, with_remote=True)
    git(repo, "branch", "factory-backlog/494")
    before = git(repo, "show-ref")
    settings = ProjectSettings()
    provider = LocalProvider(repo, settings)
    monkeypatch.setattr(pgit, "remote_branches", lambda *args: None)

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("failed discovery must stop before push or PR creation")

    monkeypatch.setattr(provider, "push", unexpected)
    monkeypatch.setattr(provider, "create_pr", unexpected)
    plan = PublishPlan(
        base="main",
        base_sha=git(repo, "rev-parse", "HEAD"),
        target=PR,
        files=(),
        blockers=(),
        digest="test",
    )
    with pytest.raises(ProviderError) as error:
        publish_pr(
            repo,
            provider=provider,
            settings=settings,
            plan=plan,
            message="backlog",
            body="archive",
            prefix="factory-backlog/",
        )
    assert error.value.code == "fetch_failed"
    assert git(repo, "show-ref") == before


def test_empty_remote_namespace_is_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_git(monkeypatch)
    repo = make_repo(tmp_path, with_remote=True)
    assert pgit.next_numbered_branch(repo, "origin", "factory-backlog/") == "factory-backlog/1"
