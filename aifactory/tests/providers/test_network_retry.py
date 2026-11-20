"""Network retry policy and command diagnostics, without live services or sleeps."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import Mock

import pytest

from aifactory import gitrun, procrun
from aifactory.cli.envelope import emit_fail, emit_ok
from aifactory.config.settings import AzureSettings, ProjectSettings
from aifactory.providers import MERGEABLE, OPEN, ProviderError, PrStatus, PullRequest, git, network
from aifactory.providers.azure import AzureProvider
from aifactory.providers.github import GitHubProvider

PR = PullRequest("1", "https://example.test/pr/1", "factory/T1-1", "main", "Task")
STATUS = PrStatus(OPEN, MERGEABLE, head_sha="abc")
LOW_SPEED = "Operation too slow. Less than 1 bytes/sec transferred the last 30 seconds"


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []
    monkeypatch.setattr(network, "_sleep", delays.append)
    return delays


@pytest.mark.parametrize(
    "detail",
    [
        "Could not resolve host: asolcz.visualstudio.com",
        "Connection reset by peer",
        "Operation timed out",
        LOW_SPEED,
        "HTTP 500",
        "HTTP 502",
        "HTTP 503",
        "HTTP 599",
        "Temporary failure in name resolution",
        "getaddrinfo failed",
    ],
)
def test_transient(detail: str) -> None:
    assert network.transient(detail)


@pytest.mark.parametrize(
    "detail",
    [
        "Authentication failed",
        "HTTP 401",
        "HTTP 403",
        "HTTP 404: Not Found",
        "Could not resolve host; authentication failed",
        "fatal: couldn't find remote ref main",
        "unable to access repository",
        "SSL certificate verification failed",
        "HTTP 503; permission denied",
        "unexpected failure",
    ],
)
def test_permanent(detail: str) -> None:
    assert not network.transient(detail)


@pytest.mark.parametrize("transient_detail", ["Could not resolve host", LOW_SPEED])
@pytest.mark.parametrize("outcome,expected", [("recovered", 3), ("exhausted", 5), ("permanent", 1)])
def test_fetch_attempts_backoff_log_and_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sleeps: list[float],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    outcome: str,
    expected: int,
    transient_detail: str,
) -> None:
    detail = b"Authentication failed" if outcome == "permanent" else transient_detail.encode()
    failure = gitrun.Result(("git", "fetch"), 128, b"", detail)
    success = gitrun.Result(("git", "fetch"), 0, b"", b"")
    results = [failure, failure, success] if outcome == "recovered" else [failure] * expected
    run = Mock(side_effect=results)
    monkeypatch.setattr(git, "_run", run)
    with network.capture(), caplog.at_level(logging.WARNING):
        if outcome == "recovered":
            git.fetch(tmp_path, "origin", "main")
            emit_ok({"merged": True})
        else:
            with pytest.raises(ProviderError) as exc:
                git.fetch(tmp_path, "origin", "main")
            assert exc.value.code == "fetch_failed"
            emit_fail(exc.value.code, exc.value.message, exit_code=2)
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is (outcome == "recovered")
    assert envelope["data"]["network_operations"] == [
        {"operation": "fetch", "attempts": expected, "succeeded": outcome == "recovered"}
    ]
    assert run.call_count == expected
    assert sleeps == list(network.DELAYS[: expected - 1])
    assert f"attempts={expected}" in caplog.text
    assert "Could not resolve host" not in caplog.text
    assert network.diagnostics() == []


@pytest.mark.parametrize("provider_type", [GitHubProvider, AzureProvider])
@pytest.mark.parametrize("outcome,expected", [("recovered", 2), ("exhausted", 5), ("permanent", 1)])
def test_provider_status_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sleeps: list[float],
    provider_type: type[GitHubProvider] | type[AzureProvider],
    outcome: str,
    expected: int,
) -> None:
    provider = provider_type(
        tmp_path,
        ProjectSettings(azure=AzureSettings(organization="example", project="p", repository="r")),
    )
    if isinstance(provider, AzureProvider):
        monkeypatch.setattr(provider, "_ready", lambda: None)
    error = ProviderError("status_failed", "HTTP 404" if outcome == "permanent" else "HTTP 503")
    values = [error, STATUS] if outcome == "recovered" else [error] * expected
    query = Mock(side_effect=values)
    monkeypatch.setattr(provider, "_status_once", query)
    with network.capture() as records:
        if outcome == "recovered":
            assert provider.status(PR) == STATUS
        else:
            with pytest.raises(ProviderError) as caught:
                provider.status(PR)
            assert caught.value is error
    assert query.call_count == expected
    assert sleeps == list(network.DELAYS[: expected - 1])
    assert records[0]["attempts"] == expected


def test_nested_operations_share_one_limit(sleeps: list[float]) -> None:
    query = Mock(side_effect=ProviderError("merge_failed", "connection reset"))

    @network.retry_network
    def inner() -> None:
        query()

    @network.retry_network
    def outer() -> None:
        inner()

    with network.capture() as records, pytest.raises(ProviderError):
        outer()
    assert query.call_count == 5
    assert len(records) == 1 and records[0]["attempts"] == 5
    assert sleeps == list(network.DELAYS)


def test_probe_budget_prevents_backoff(sleeps: list[float]) -> None:
    query = Mock(side_effect=ProviderError("fetch_failed", "connection reset"))
    with procrun.budget(1), pytest.raises(ProviderError):
        network.retry_network(query)()
    assert query.call_count == 1 and sleeps == []


def test_git_timeout_is_fetch_failed_after_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sleeps: list[float],
) -> None:
    run = Mock(side_effect=gitrun.GitTimeout(("git", "fetch"), 30, tmp_path))
    monkeypatch.setattr(git, "_run", run)
    with pytest.raises(ProviderError) as error:
        git.fetch(tmp_path, "origin", "main")
    assert error.value.code == "fetch_failed" and run.call_count == 5
    assert sleeps == list(network.DELAYS)


@pytest.mark.parametrize("hosting", ["github", "azure", "forgejo"])
@pytest.mark.parametrize("outcome,expected", [("recovered", 2), ("exhausted", 5), ("permanent", 1)])
def test_guarded_merge_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sleeps: list[float],
    hosting: str,
    outcome: str,
    expected: int,
) -> None:
    from unittest.mock import PropertyMock

    from aifactory.providers import MERGED, MergeFailed
    from aifactory.providers.azure import AzError
    from aifactory.providers.forgejo import ForgejoProvider
    from aifactory.providers.forgejo_rest import ForgejoHttpError
    from aifactory.providers.github import GhError

    settings = ProjectSettings(
        azure=AzureSettings(organization="example", project="p", repository="r")
    )
    calls = 0
    succeeded = False
    permanent = outcome == "permanent"
    errors = {
        "github": GhError(("pr", "merge"), 1, "HTTP 403" if permanent else "HTTP 503"),
        "azure": AzError(("repos", "pr", "update"), 1, "HTTP 403" if permanent else "HTTP 503"),
        "forgejo": ForgejoHttpError(403 if permanent else 503),
    }

    def write(*args: object, **kwargs: object) -> object:
        nonlocal calls, succeeded
        calls += 1
        if outcome != "recovered" or calls == 1:
            raise errors[hosting]
        succeeded = True
        if hosting == "azure":
            return {
                "status": "completed",
                "lastMergeSourceCommit": {"commitId": "abc"},
                "lastMergeCommit": {"commitId": "merged"},
            }
        return ""

    def status(*args: object) -> PrStatus:
        return PrStatus(MERGED, head_sha="abc", merge_sha="merged") if succeeded else STATUS

    provider: GitHubProvider | AzureProvider | ForgejoProvider
    if hosting == "github":
        provider = GitHubProvider(tmp_path, settings)
        monkeypatch.setattr(provider.gh, "run", write)
        monkeypatch.setattr(provider, "_status_once", status)
    elif hosting == "azure":
        from aifactory.providers import azure_rest

        provider = AzureProvider(tmp_path, settings, attempts=1)
        monkeypatch.setattr(provider, "_ready", lambda: None)
        monkeypatch.setattr(provider, "_status_once", status)
        monkeypatch.setattr(provider, "_write", lambda *args: azure_rest.UNAVAILABLE)
        monkeypatch.setattr(provider.az, "json", write)
    else:
        provider = ForgejoProvider(tmp_path, settings)
        monkeypatch.setattr(type(provider), "_api", PropertyMock(return_value=Mock(request=write)))
        monkeypatch.setattr(provider, "_pr_path", lambda pr: "/prs/1")
        monkeypatch.setattr(provider, "status", status)
    with network.capture() as records:
        if outcome == "recovered":
            assert provider.merge(PR, "abc", "Task") == "merged"
        else:
            with pytest.raises(MergeFailed):
                provider.merge(PR, "abc", "Task")
    assert calls == expected
    assert sleeps == list(network.DELAYS[: expected - 1])
    assert records[0]["attempts"] == expected


def test_create_reconciles_lost_response_before_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sleeps: list[float],
) -> None:
    from aifactory.providers.github import GhError

    provider = GitHubProvider(tmp_path, ProjectSettings())
    write = Mock(side_effect=GhError(("pr", "create"), 1, "connection reset"))
    monkeypatch.setattr(provider.gh, "run", write)
    lookup = Mock(return_value=PR)
    monkeypatch.setattr(provider, "find_open_pr", lookup)
    with network.capture() as records:
        assert provider.create_pr(PR.branch, PR.title, "body") == PR
    assert write.call_count == lookup.call_count == 1
    assert sleeps == [10.0] and records[0]["attempts"] == 2


def test_cli_invocations_do_not_leak_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from aifactory import cli

    result = gitrun.Result(("git", "fetch"), 128, b"", b"Authentication failed")
    monkeypatch.setattr(git, "_run", Mock(return_value=result))

    def fail(argv: object) -> int:
        try:
            git.fetch(tmp_path, "origin", "main")
        except ProviderError as exc:
            return emit_fail(exc.code, exc.message, exit_code=2)
        raise AssertionError("fetch must fail")

    monkeypatch.setattr(cli, "_main", fail)
    assert cli.main(["--json"]) == 2
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"]["network_operations"][0]["attempts"] == 1
    monkeypatch.setattr(cli, "_main", lambda argv: emit_ok({"clean": True}))
    assert cli.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"] == {"clean": True}


def test_unverified_write_forbids_retry(
    sleeps: list[float],
    capsys: pytest.CaptureFixture[str],
) -> None:
    error = ProviderError("approve_failed", "connection reset after dispatch")
    error.retryable = False
    write = Mock(side_effect=error)
    with network.capture():
        with pytest.raises(ProviderError) as caught:
            network.retry_network(write)()
        assert caught.value is error
        emit_fail(error.code, error.message, exit_code=2)
    assert write.call_count == 1 and sleeps == []
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "approve_failed"
    assert result["data"]["network_operations"][0]["attempts"] == 1
    assert result["data"]["network_operations"][0]["succeeded"] is False
