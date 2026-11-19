"""Read transport contract: no network, no real credentials or Azure CLI."""

from __future__ import annotations

import http.client
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from aifactory.config.settings import AzureSettings, ProjectSettings
from aifactory.providers import azure_rest as rest
from aifactory.providers.azure import BUILD_POLICY, AzureProvider
from aifactory.providers.base import ProviderError, PullRequest

ORG = "https://dev.azure.com/org"
PR = PullRequest(id="4", url="https://example.test/pr/4", branch="task", base="main", title="t")


class Tokens:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def json(self, *args: str) -> Any:
        assert args[:2] == ("account", "get-access-token")
        self.calls += 1
        if self.fail:
            raise ProviderError("az_failed", "credential source unavailable")
        return {"accessToken": f"token-{self.calls}", "expires_on": time.time() + 3600}


class Response:
    def __init__(self, data: Any, status: int = 200, /, **headers: str) -> None:
        self.body = json.dumps(data).encode()
        self.status = status
        self.headers = headers

    def read1(self, limit: int) -> bytes:
        result, self.body = self.body[:limit], self.body[limit:]
        return result

    def getheader(self, name: str) -> str | None:
        return self.headers.get(name)


class Wire:
    def __init__(self) -> None:
        self.responses: list[Response | Exception] = []
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.connections = 0
        self.closed = 0

    def connect(self, host: str, **kwargs: Any) -> Any:
        assert host in ("dev.azure.com", "org.visualstudio.com")
        self.connections += 1
        wire = self

        class Connection:
            sock = None
            timeout = 0.0

            def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
                assert method == "GET"
                wire.requests.append((method, target, headers))

            def getresponse(self) -> Response:
                reply = wire.responses.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return reply

            def close(self) -> None:
                wire.closed += 1

        return Connection()


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Iterator[Wire]:
    rest.close_all()
    for name in (
        "AZURE_DEVOPS_EXT_PAT",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "AZURE_CLI_DISABLE_CONNECTION_VERIFICATION",
        "GIRO_AZ",
    ):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    wire = Wire()
    monkeypatch.setattr(http.client, "HTTPSConnection", wire.connect)
    yield wire
    rest.close_all()


def test_pool_reuses_token_connection_but_never_results(wire: Wire) -> None:
    az = Tokens()
    wire.responses = [Response({"head": "a"}), Response({"head": "b"})]
    assert rest.read("identity", ORG, az, "/p/_apis/pr/1") == {"head": "a"}
    assert rest.read("identity", ORG, az, "/p/_apis/pr/1") == {"head": "b"}
    assert az.calls == wire.connections == 1
    assert len(wire.requests) == 2


def test_context_change_and_expiry_refresh_credentials(wire: Wire) -> None:
    az = Tokens()
    wire.responses = [Response({}) for _ in range(3)]
    rest.read("old", ORG, az, "/p")
    rest._pool["old"].client.expires = 0
    rest.read("old", ORG, az, "/p")
    rest.read("new", ORG, az, "/p")
    assert az.calls == 3 and wire.connections == 2


def test_pat_skips_token_cli_and_unauthorized_is_safe(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_EXT_PAT", "pat-secret")
    wire.responses = [Response({}, 401), Response({"secret": "pat-secret"}, 401)]
    az = Tokens()
    with pytest.raises(ProviderError) as error:
        rest.read("pat", ORG, az, "/p")
    assert error.value.code == "az_not_logged_in"
    assert "pat-secret" not in str(error.value)
    assert az.calls == 0


def test_token_401_refresh_once(wire: Wire) -> None:
    wire.responses = [Response({}, 401), Response({"ok": True})]
    az = Tokens()
    assert rest.read("k", ORG, az, "/p") == {"ok": True}
    assert az.calls == 2
    assert wire.requests[0][2]["Authorization"] != wire.requests[1][2]["Authorization"]


def test_unavailable_auth_has_short_negative_cache(wire: Wire) -> None:
    az = Tokens()
    az.fail = True
    assert rest.read("k", ORG, az, "/p") is rest.UNAVAILABLE
    assert rest.read("k", ORG, az, "/p") is rest.UNAVAILABLE
    assert az.calls == 1 and not wire.requests
    rest._pool["k"].client.unavailable_until = 0
    az.fail = False
    wire.responses = [Response({})]
    assert rest.read("k", ORG, az, "/p") == {}


def test_reconnects_closed_idle_connection(wire: Wire) -> None:
    wire.responses = [Response({}), ConnectionResetError(), Response({"fresh": True})]
    az = Tokens()
    rest.read("k", ORG, az, "/p")
    assert rest.read("k", ORG, az, "/p") == {"fresh": True}
    assert wire.connections == 2 and az.calls == 1


def test_pagination_and_escaping(wire: Wire) -> None:
    wire.responses = [
        Response({"value": [1]}, **{"x-ms-continuationtoken": "x&y"}),
        Response({"value": [2]}),
    ]
    assert rest.read("k", ORG, Tokens(), "/p", {"branch": "a&b"}, collection=True) == [1, 2]
    query = parse_qs(urlsplit(wire.requests[1][1]).query)
    assert query["continuationToken"] == ["x&y"] and query["branch"] == ["a&b"]


@pytest.mark.parametrize("data", [{}, {"value": "invalid"}])
def test_invalid_collection_never_means_no_ci(wire: Wire, data: Any) -> None:
    wire.responses = [Response(data)]
    with pytest.raises(ProviderError, match="invalid collection"):
        rest.read("k", ORG, Tokens(), "/p", collection=True)


def test_repeated_continuation_refused(wire: Wire) -> None:
    wire.responses = [Response({"value": []}, **{"x-ms-continuationtoken": "x"}) for _ in range(2)]
    with pytest.raises(ProviderError, match="continuation"):
        rest.read("k", ORG, Tokens(), "/p", collection=True)


def test_retry_after_respected_and_bounded(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    wire.responses = [Response({}, 429, **{"Retry-After": "2"}), Response({})]
    assert rest.read("k", ORG, Tokens(), "/p") == {}
    assert sleeps == [2]
    wire.responses = [Response({}, 503, **{"Retry-After": "1000"})]
    with pytest.raises(ProviderError) as error:
        rest.read("k", ORG, Tokens(), "/p")
    assert error.value.code == "az_timeout"


@pytest.mark.parametrize("status", [301, 403, 500])
def test_errors_are_not_empty_success_and_redirects_are_not_followed(
    wire: Wire, status: int
) -> None:
    wire.responses = [Response({"token": "secret"}, status, Location="https://elsewhere.test")]
    with pytest.raises(ProviderError) as error:
        rest.read("k", ORG, Tokens(), "/p")
    assert "secret" not in str(error.value) and len(wire.requests) == 1


def test_response_size_limit(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rest, "MAX_BODY", 8)
    wire.responses = [Response("too much response")]
    with pytest.raises(ProviderError, match="read limit"):
        rest.read("k", ORG, Tokens(), "/p")


def test_deadline_prevents_next_page(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    wire.responses = [Response({"value": [1]}, **{"x-ms-continuationtoken": "x"})]
    ticks = iter([0.0, 0.0, 0.0, 0.0, 0.0, 100.0, 100.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 100.0))
    with pytest.raises(ProviderError) as error:
        rest.read("k", ORG, Tokens(), "/p", collection=True)
    assert error.value.code == "az_timeout"


def test_pool_bound_and_eviction(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rest, "MAX_CLIENTS", 2)
    wire.responses = [Response({}) for _ in range(3)]
    for i in range(3):
        rest.read(str(i), ORG, Tokens(), "/p")
    assert len(rest._pool) == 2 and wire.closed == 1


def test_unsupported_network_keeps_cli(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    assert rest.supported(ORG)
    assert rest.supported("https://org.visualstudio.com")
    for url in ("http://dev.azure.com/org", "https://server/tfs", "https://dev.azure.com.evil/org"):
        assert not rest.supported(url)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy")
    assert not rest.supported(ORG)


def provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AzureProvider:
    result = AzureProvider(
        tmp_path,
        ProjectSettings(
            git_provider="azure",
            azure=AzureSettings(organization=ORG, project="p space", repository="r#"),
        ),
        attempts=1,
    )
    monkeypatch.setattr(result, "_ready", lambda: None)
    monkeypatch.setattr(result, "_preflight_key", lambda: "provider")
    monkeypatch.setenv("AZURE_CONFIG_DIR", str(tmp_path / "azure"))
    token = Tokens()
    monkeypatch.setattr(result.az, "json", token.json)
    return result


def pr_data(head: str = "a") -> dict[str, Any]:
    return {
        "pullRequestId": 4,
        "status": "active",
        "mergeStatus": "succeeded",
        "lastMergeSourceCommit": {"commitId": head},
        "repository": {"project": {"id": "project-guid"}},
    }


def test_provider_ci_snapshot_is_fresh_and_shared_only_within_cycle(
    wire: Wire, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = provider(tmp_path, monkeypatch)
    run = {
        "sourceVersion": "a",
        "status": "completed",
        "result": "failed",
        "definition": {"id": 1, "name": "build"},
    }
    wire.responses = [Response(pr_data()), Response({"value": []}), Response({"value": [run]})]
    assert p.checks(PR).state == "failing"
    assert len(wire.requests) == 3
    assert "/p%20space/_apis/git/repositories/r%23/pullrequests/4?" in wire.requests[0][1]
    assert "project-guid" in wire.requests[1][1]
    wire.responses = [Response(pr_data("b")), Response({"value": []}), Response({"value": [run]})]
    assert p.checks(PR).state == "none"  # old SHA build must not count


def test_provider_policy_checks_and_batched_statuses(
    wire: Wire, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = provider(tmp_path, monkeypatch)
    policy = {"configuration": {"type": {"id": BUILD_POLICY}}, "status": "running"}
    wire.responses = [Response(pr_data()), Response({"value": [policy]})]
    assert p.checks(PR).state == "pending"
    wire.responses = [Response({"value": [pr_data()]})]
    assert p.statuses([PR, PR])["4"].head_sha == "a"  # type: ignore[union-attr]


def test_head_change_prevents_vote(
    wire: Wire, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = provider(tmp_path, monkeypatch)
    wire.responses = [Response(pr_data("changed"))]
    with pytest.raises(ProviderError, match="head changed"):
        p.approve(PR, "a")


def test_injected_cli_is_not_bypassed(
    wire: Wire, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = provider(tmp_path, monkeypatch)
    p._shared_preflight = False
    monkeypatch.setattr(p.az, "json", lambda *args: pr_data())
    assert p.status(PR).head_sha == "a"
    assert not wire.requests


def test_stored_pat_retains_cli(
    wire: Wire, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = provider(tmp_path, monkeypatch)
    pat = tmp_path / "azure/azuredevops/personalAccessTokens"
    pat.parent.mkdir(parents=True)
    pat.write_text("credential store", encoding="utf-8")
    monkeypatch.setattr(p.az, "json", lambda *args: pr_data())
    assert p.status(PR).head_sha == "a"
    assert not wire.requests


def test_head_change_after_vote_does_not_repeat_write(
    wire: Wire, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = provider(tmp_path, monkeypatch)
    writes: list[tuple[str, ...]] = []

    def write(*args: str) -> str:
        writes.append(args)
        return ""

    monkeypatch.setattr(p.az, "run", write)
    wire.responses = [Response(pr_data("a")), Response(pr_data("b"))]
    with pytest.raises(ProviderError, match="head changed"):
        p.approve(PR, "a")
    assert len(writes) == 1 and writes[0][:3] == ("repos", "pr", "set-vote")


def test_head_change_prevents_merge(
    wire: Wire, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = provider(tmp_path, monkeypatch)
    writes: list[tuple[str, ...]] = []

    def write(*args: str) -> str:
        writes.append(args)
        return ""

    monkeypatch.setattr(p.az, "run", write)
    wire.responses = [Response(pr_data("b"))]
    with pytest.raises(ProviderError, match="moved"):
        p.merge(PR, "a", "merge")
    assert writes == []


def test_invalid_json_and_connection_failure_never_mean_no_checks(wire: Wire) -> None:
    reply = Response({})
    reply.body = b"not json"
    wire.responses = [reply]
    with pytest.raises(ProviderError, match="invalid JSON"):
        rest.read("k", ORG, Tokens(), "/p")
    wire.responses = [ConnectionResetError() for _ in range(3)]
    with pytest.raises(ProviderError, match="connection failed"):
        rest.read("k", ORG, Tokens(), "/p")


def test_concurrent_reads_share_bootstrap_without_closing_busy_client(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    entered = threading.Event()
    release = threading.Event()
    az = Tokens()
    authenticate = az.json

    def blocked(*args: str) -> Any:
        entered.set()
        assert release.wait(5)
        return authenticate(*args)

    monkeypatch.setattr(az, "json", blocked)
    wire.responses = [Response({}), Response({})]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(rest.read, "k", ORG, az, "/p")
        assert entered.wait(5)
        second = pool.submit(rest.read, "k", ORG, az, "/p")
        release.set()
        assert first.result(timeout=5) == second.result(timeout=5) == {}
    assert az.calls == wire.connections == 1 and wire.closed == 0


def test_busy_connection_timeout_preserves_lease(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    wire.responses = [Response({})]
    az = Tokens()
    rest.read("k", ORG, az, "/p")
    entry = rest._pool["k"]
    monkeypatch.setattr(rest, "TIMEOUT", 0.001)
    with entry.lock:
        with pytest.raises(ProviderError) as error:
            rest.read("k", ORG, az, "/p")
        assert error.value.code == "az_timeout"
    assert entry.users == 0 and wire.closed == 0


def test_idle_pool_entries_are_closed(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    wire.responses = [Response({}), Response({})]
    rest.read("old", ORG, Tokens(), "/p")
    monkeypatch.setattr(rest, "IDLE_TTL", 0)
    rest.read("new", ORG, Tokens(), "/p")
    assert "old" not in rest._pool and wire.closed == 1


def test_find_open_pr_rest_filters_source_and_target(
    wire: Wire, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = provider(tmp_path, monkeypatch)
    wire.responses = [Response({"value": [pr_data()]})]
    found = p.find_open_pr("task & more")
    assert found is not None and found.id == "4"
    query = parse_qs(urlsplit(wire.requests[0][1]).query)
    assert query["searchCriteria.sourceRefName"] == ["refs/heads/task & more"]
    assert query["searchCriteria.targetRefName"] == ["refs/heads/main"]
    assert query["searchCriteria.status"] == ["active"]
    wire.responses = [Response({"value": []})]
    assert p.find_open_pr("task") is None
