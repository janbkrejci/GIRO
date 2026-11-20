"""Write contracts exercised entirely with fake REST and injected CLI transports."""

from __future__ import annotations

import copy
import http.client
import json
import logging
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from aifactory.cli.envelope import emit_fail, emit_ok
from aifactory.config.settings import AzureSettings, ProjectSettings
from aifactory.providers import azure_rest, network
from aifactory.providers.azure import AzCli, AzError, AzureProvider
from aifactory.providers.base import ProviderError, PullRequest

PR = PullRequest(id="7", url="https://example.test/7", branch="task", base="main", title="Title")


class Fake(AzCli):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.data: dict[str, Any] = {
            "pullRequestId": 7,
            "sourceRefName": "refs/heads/task",
            "targetRefName": "refs/heads/main",
            "title": "Title",
            "description": "old",
            "status": "active",
            "mergeStatus": "succeeded",
            "lastMergeSourceCommit": {"commitId": "head"},
            "reviewers": [],
        }
        self.listed: list[dict[str, Any]] = []
        self.threads: list[dict[str, Any]] = []
        self.writes: list[tuple[str, str, dict[str, Any]]] = []
        self.reads: list[str] = []
        self.mode = "success"
        self.transport = "rest"
        self.race = False
        self.read_failure = False

    def read(self, path: str, *args: Any, **kwargs: Any) -> Any:
        if self.transport == "cli":
            return azure_rest.UNAVAILABLE
        return self.snapshot(path)

    def snapshot(self, path: str) -> Any:
        self.reads.append(path)
        if self.read_failure and self.writes:
            raise ProviderError("az_failed", "read unavailable")
        if path.endswith("connectionData"):
            return {"authenticatedUser": {"id": "self"}}
        if path.endswith("threads"):
            return copy.deepcopy(self.threads)
        if path.endswith("pullrequests"):
            return copy.deepcopy(self.listed)
        return copy.deepcopy(self.data)

    def write(
        self,
        key: str,
        org: str,
        az: Any,
        path: str,
        method: str,
        body: dict[str, Any],
        params: Any = None,
    ) -> Any:
        self.writes.append((path, method, copy.deepcopy(body)))
        result: dict[str, Any] = self.data
        if self.mode in ("success", "lost"):
            if path.endswith("threads"):
                result = {"id": 1, **copy.deepcopy(body)}
                self.threads.append(result)
            elif "/reviewers/" in path:
                result = body
                self.data["reviewers"] = [body]
            elif method == "POST":
                self.data.update(body)
                self.data["creationDate"] = datetime.now(UTC).isoformat()
                self.listed.append(copy.deepcopy(self.data))
            else:
                self.data.update(body)
                if body.get("status") == "completed":
                    self.data["lastMergeCommit"] = {"commitId": "merged"}
        if self.race:
            self.data["lastMergeSourceCommit"] = {"commitId": "other"}
        if self.mode == "denied":
            if self.transport == "cli":
                raise AzError(("repos", "pr", "update"), 1, "403 Forbidden")
            raise ProviderError("az_not_logged_in", "HTTP 403")
        if self.mode in ("lost", "unaccepted"):
            if self.transport == "cli":
                raise AzError(("repos", "pr", "update"), 1, "Connection reset")
            raise azure_rest.WriteUncertain("response lost")
        return copy.deepcopy(result)

    def json(self, *args: str) -> Any:
        prefix = args[:3]
        if prefix == ("repos", "pr", "list"):
            return self.snapshot("pullrequests")
        if prefix == ("repos", "pr", "show"):
            return self.snapshot("pr")
        if prefix == ("devops", "invoke", "--area"):
            assert "GET" in args
            return {"value": self.snapshot("threads")}
        if prefix == ("repos", "pr", "create"):
            body = {
                "sourceRefName": "refs/heads/task",
                "targetRefName": "refs/heads/main",
                "title": "Title",
                "description": args[args.index("--description") + 1],
            }
            return self.write("", "", self, "pullrequests", "POST", body)
        if prefix == ("repos", "pr", "update"):
            if "--status" in args:
                body = {"status": "completed"}
            else:
                body = {"description": args[args.index("--description") + 1]}
            return self.write("", "", self, "pr", "PATCH", body)
        raise AssertionError(args)

    def run(self, *args: str) -> str:
        if args[:3] == ("repos", "pr", "set-vote"):
            self.write("", "", self, "pr/reviewers/self", "PUT", {"id": "self", "vote": 10})
        elif "--in-file" in args:
            path = Path(args[args.index("--in-file") + 1])
            self.write("", "", self, "threads", "POST", json.loads(path.read_text()))
        else:
            raise AssertionError(args)
        return "{}"


@pytest.fixture(params=["rest", "cli"])
def setup(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[AzureProvider, Fake]:
    fake = Fake(tmp_path)
    fake.transport = request.param
    provider = AzureProvider(
        tmp_path,
        ProjectSettings(
            azure=AzureSettings(organization="example", project="p space", repository="r/name")
        ),
        fake,
        attempts=1,
        delay=0,
    )
    monkeypatch.setattr(provider, "_ready", lambda: None)
    monkeypatch.setattr(provider, "_read", fake.read)
    monkeypatch.setattr(provider, "_rest_key", lambda: "key" if fake.transport == "rest" else None)
    monkeypatch.setattr(azure_rest, "write", fake.write)
    return provider, fake


def perform(provider: AzureProvider, operation: str) -> Any:
    if operation == "create":
        return provider.create_pr("task", "Title", "body")
    if operation == "update":
        return provider.update_pr(PR, "body")
    if operation == "approve":
        return provider.approve(PR, "head")
    if operation == "merge":
        return provider.merge(PR, "head", "subject")
    return provider.comment(PR, "body")


@pytest.mark.parametrize("operation", ["create", "update", "approve", "merge", "comment"])
@pytest.mark.parametrize("mode", ["success", "lost", "unaccepted", "denied"])
def test_write_outcomes(setup: tuple[AzureProvider, Fake], operation: str, mode: str) -> None:
    provider, fake = setup
    fake.mode = mode
    failure = mode in ("unaccepted", "denied") or (
        operation == "approve" and mode == "lost" and fake.transport == "cli"
    )
    if failure:
        with pytest.raises(ProviderError):
            perform(provider, operation)
    else:
        result = perform(provider, operation)
        if operation == "create":
            assert result.id == "7"
        if operation == "merge":
            assert result == "merged"
    retries = fake.transport == "cli" and mode == "unaccepted" and operation in ("update", "merge")
    assert len(fake.writes) == (5 if retries else 1)
    if mode == "lost" and operation == "approve":
        assert any(path.endswith("7") or path == "pr" for path in fake.reads)


@pytest.mark.parametrize("operation", ["create", "update", "approve", "merge", "comment"])
def test_unreadable_after_acceptance_fails_closed(
    setup: tuple[AzureProvider, Fake], operation: str
) -> None:
    provider, fake = setup
    fake.mode, fake.read_failure = "lost", True
    with pytest.raises(ProviderError):
        perform(provider, operation)
    assert len(fake.writes) == 1


@pytest.mark.parametrize("operation", ["update", "approve"])
def test_explicit_rejection_cannot_adopt_existing_state(
    setup: tuple[AzureProvider, Fake], operation: str
) -> None:
    provider, fake = setup
    fake.data.update(description="body", reviewers=[{"id": "self", "vote": 10}])
    fake.mode = "denied"
    with pytest.raises(ProviderError):
        perform(provider, operation)
    assert len(fake.writes) == 1


@pytest.mark.parametrize("operation", ["approve", "merge"])
@pytest.mark.parametrize("when", ["before", "during", "merged"])
def test_head_races(setup: tuple[AzureProvider, Fake], operation: str, when: str) -> None:
    provider, fake = setup
    if when == "before" or when == "merged":
        fake.data["lastMergeSourceCommit"] = {"commitId": "other"}
        if when == "merged":
            fake.data["status"] = "completed"
            if operation == "approve":
                assert provider.approve(PR, "head") is False
                return
    else:
        fake.race = True
    with pytest.raises(ProviderError):
        perform(provider, operation)
    assert len(fake.writes) == (1 if when == "during" else 0)


def test_comment_does_not_adopt_old_identical_text(setup: tuple[AzureProvider, Fake]) -> None:
    provider, fake = setup
    fake.threads = [
        {
            "id": 1,
            "comments": [{"content": "body"}],
            "properties": {"GIRO.OperationId": {"$value": "old"}},
        }
    ]
    fake.mode = "unaccepted"
    with pytest.raises(ProviderError):
        provider.comment(PR, "body")
    payload = fake.writes[0][2]
    assert payload["comments"][0]["content"] == "body"
    assert payload["properties"]["GIRO.OperationId"]["$value"] != "old"


@pytest.mark.parametrize("kind", ["old", "unrelated", "multiple", "no_snapshot"])
def test_create_does_not_adopt_unverified_pr(
    setup: tuple[AzureProvider, Fake], kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, fake = setup
    candidate = {**fake.data, "description": "body", "creationDate": datetime.now(UTC).isoformat()}
    if kind == "old":
        fake.listed = [candidate]
        fake.mode = "unaccepted"
    elif kind == "no_snapshot":

        def unavailable(branch: str) -> Any:
            raise ProviderError("az_failed", "snapshot unavailable")

        monkeypatch.setattr(provider, "_creation_candidates", unavailable)
        fake.mode = "lost"
    else:
        calls = 0

        def candidates(branch: str) -> list[dict[str, Any]]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return []
            candidate["creationDate"] = datetime.now(UTC).isoformat()
            if kind == "unrelated":
                return [{**candidate, "title": "someone else's PR"}]
            return [candidate, {**candidate, "pullRequestId": 8}]

        monkeypatch.setattr(provider, "_creation_candidates", candidates)
        fake.mode = "unaccepted"
    with network.capture() as records, pytest.raises(ProviderError):
        provider.create_pr("task", "Title", "body")
    assert len(fake.writes) == 1
    assert records[0]["attempts"] == 1


def test_rest_merge_contract(setup: tuple[AzureProvider, Fake]) -> None:
    provider, fake = setup
    if fake.transport == "cli":
        pytest.skip("REST payload")
    provider.merge(PR, "head", "subject", "merge")
    path, method, body = fake.writes[0]
    assert path.startswith("/p%20space/_apis/git/repositories/r%2Fname/")
    assert method == "PATCH"
    assert body["lastMergeSourceCommit"] == {"commitId": "head"}
    assert body["completionOptions"] == {
        "mergeStrategy": "noFastForward",
        "mergeCommitMessage": "subject",
        "deleteSourceBranch": False,
        "bypassPolicy": False,
    }


def test_cli_approval_unknown_identity_reads_then_fails(setup: tuple[AzureProvider, Fake]) -> None:
    provider, fake = setup
    if fake.transport != "cli":
        pytest.skip("CLI identity limitation")
    fake.mode = "lost"
    with network.capture() as records, pytest.raises(ProviderError, match="response|Connection"):
        provider.approve(PR, "head")
    assert fake.reads == ["pr", "pr"]
    assert len(fake.writes) == 1 and records[0]["attempts"] == 1


@pytest.mark.parametrize("operation", ["create", "update", "approve", "merge", "comment"])
def test_rest_explicit_az_failed_never_reconciles(
    setup: tuple[AzureProvider, Fake], operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, fake = setup
    if fake.transport == "cli":
        pytest.skip("REST explicit errors")
    fake.data.update(description="body", reviewers=[{"id": "self", "vote": 10}])

    def rejected(*args: Any, **kwargs: Any) -> Any:
        raise ProviderError("az_failed", "HTTP 400")

    monkeypatch.setattr(azure_rest, "write", rejected)
    with pytest.raises(ProviderError, match="HTTP 400"):
        perform(provider, operation)


def test_unavailable_before_dispatch_preserves_cli(
    setup: tuple[AzureProvider, Fake], monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, fake = setup
    monkeypatch.setattr(provider, "_rest_key", lambda: "key")
    monkeypatch.setattr(azure_rest, "write", lambda *args, **kwargs: azure_rest.UNAVAILABLE)
    provider.update_pr(PR, "body")
    assert len(fake.writes) == 1
    assert fake.data["description"] == "body"


@pytest.mark.parametrize("operation", ["create", "update", "approve", "merge", "comment"])
def test_cli_timeout_reads_back_without_replaying(
    setup: tuple[AzureProvider, Fake], operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, fake = setup
    if fake.transport != "cli":
        pytest.skip("CLI timeout")
    original = fake.write

    def timed_out(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        raise ProviderError("az_timeout", "timed out")

    monkeypatch.setattr(fake, "write", timed_out)
    if operation == "approve":
        with pytest.raises(ProviderError):
            perform(provider, operation)
    else:
        perform(provider, operation)
    assert len(fake.writes) == 1


@pytest.mark.parametrize("failures", [1, 5])
def test_approve_retries_dns_before_rest_vote(
    setup: tuple[AzureProvider, Fake],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    failures: int,
) -> None:
    provider, fake = setup
    if fake.transport != "rest":
        pytest.skip("REST DNS transport")
    recovered = failures == 1
    connection = Mock(sock=None)
    connection.request.side_effect = [
        socket.gaierror(socket.EAI_AGAIN, "secret") for _ in range(failures)
    ] + ([None] if recovered else [])
    response = Mock(status=200)
    response.read1.side_effect = [b'{"id": "self", "vote": 10}', b""]
    response.getheader.return_value = None
    connection.getresponse.return_value = response
    monkeypatch.setattr(http.client, "HTTPSConnection", Mock(return_value=connection))
    client = azure_rest.ReadClient("https://dev.azure.com/example")

    def write(path: str, method: str, body: dict[str, Any]) -> Any:
        return client.write(
            path,
            method,
            json.dumps(body).encode(),
            {},
            deadline=time.monotonic() + azure_rest.TIMEOUT,
        )

    monkeypatch.setattr(provider, "_write", write)
    cli = Mock(side_effect=AssertionError("DNS failure must not fall back to CLI"))
    monkeypatch.setattr(fake, "run", cli)
    delays: list[float] = []
    monkeypatch.setattr(network, "_sleep", delays.append)
    with network.capture(), caplog.at_level(logging.WARNING):
        if recovered:
            assert provider.approve(PR, "head") is True
            emit_ok({"reviewed": True})
        else:
            with pytest.raises(ProviderError) as error:
                provider.approve(PR, "head")
            assert error.value.code == "approve_failed"
            emit_fail(error.value.code, error.value.message, exit_code=2)
    attempts = 2 if recovered else 5
    assert connection.request.call_count == attempts
    assert connection.close.call_count == failures
    assert connection.getresponse.call_count == int(recovered)
    assert cli.call_count == 0 and fake.writes == []
    assert delays == list(network.DELAYS[: attempts - 1])
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is recovered
    assert envelope["data"]["network_operations"] == [
        {
            "operation": "AzureProvider.approve",
            "attempts": attempts,
            "succeeded": recovered,
        }
    ]
    assert f"attempts={attempts}" in caplog.text and "secret" not in caplog.text
