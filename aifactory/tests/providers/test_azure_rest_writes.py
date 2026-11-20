"""Single-dispatch write contract, using only an in-memory HTTPS wire."""

from __future__ import annotations

import http.client
import json
import socket
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from aifactory.providers import azure_rest as rest
from aifactory.providers.base import ProviderError

ORG = "https://dev.azure.com/org"


class Tokens:
    calls = 0
    fail = False

    def json(self, *args: str) -> Any:
        assert args[:2] == ("account", "get-access-token")
        self.calls += 1
        if self.fail:
            raise ProviderError("az_failed", "secret")
        return {"accessToken": "secret", "expires_on": time.time() + 3600}


class Wire:
    sock = None
    timeout = 0.0
    status = 200
    closed = 0
    connections = 0
    length: str | None = None
    failure: Exception | None = None
    send_failure: Exception | None = None

    def __init__(self) -> None:
        self.body = b'{"id": 42}'
        self.requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []

    def connect(self, host: str, **kwargs: Any) -> Wire:
        assert host == "dev.azure.com"
        assert kwargs["timeout"] > 0
        self.connections += 1
        return self

    def request(
        self, method: str, target: str, body: bytes | None = None, *, headers: dict[str, str]
    ) -> None:
        self.requests.append((method, target, body, headers))
        if self.send_failure:
            raise self.send_failure

    def getresponse(self) -> Wire:
        if self.failure:
            raise self.failure
        return self

    def read1(self, limit: int) -> bytes:
        chunk, self.body = self.body[:limit], self.body[limit:]
        return chunk

    def getheader(self, name: str) -> str | None:
        return self.length if name == "Content-Length" else None

    def close(self) -> None:
        self.closed += 1


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Iterator[Wire]:
    rest.close_all()
    monkeypatch.delenv("AZURE_DEVOPS_EXT_PAT", raising=False)
    result = Wire()
    monkeypatch.setattr(http.client, "HTTPSConnection", result.connect)
    yield result
    rest.close_all()


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT"])
def test_success_and_shared_read_pool(wire: Wire, method: str) -> None:
    az = Tokens()
    assert rest.write("k", ORG, az, "/p", method, {"title": "ž"}, {"q": "a&b"}) == {"id": 42}
    sent_method, target, body, headers = wire.requests[0]
    assert sent_method == method and body is not None and json.loads(body) == {"title": "ž"}
    assert target.startswith("/org/p?")
    assert parse_qs(urlsplit(target).query) == {"api-version": ["7.1"], "q": ["a&b"]}
    assert headers["Content-Type"] == "application/json"
    assert headers["Authorization"] == "Bearer secret"
    wire.body = b"{}"
    assert rest.read("k", ORG, az, "/p") == {}
    assert az.calls == wire.connections == 1
    assert [r[0] for r in wire.requests] == [method, "GET"]


@pytest.mark.parametrize("stage", ["send", "response", "body"])
@pytest.mark.parametrize(
    "failure",
    [TimeoutError("secret"), ConnectionResetError("secret"), http.client.IncompleteRead(b"secret")],
)
def test_transport_failures_never_replay(
    wire: Wire, monkeypatch: pytest.MonkeyPatch, stage: str, failure: Exception
) -> None:
    if stage == "send":
        wire.send_failure = failure
    elif stage == "response":
        wire.failure = failure
    else:

        def broken(limit: int) -> bytes:
            raise failure

        monkeypatch.setattr(wire, "read1", broken)
    with pytest.raises(rest.WriteUncertain) as error:
        rest.write("k", ORG, Tokens(), "/p", "POST", {})
    assert error.value.code == "az_failed" and "secret" not in str(error.value)
    assert len(wire.requests) == wire.closed == 1


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_server_failure_is_uncertain(wire: Wire, status: int) -> None:
    wire.status = status
    with pytest.raises(rest.WriteUncertain):
        rest.write("k", ORG, Tokens(), "/p", "PATCH", {})
    assert len(wire.requests) == wire.closed == 1


@pytest.mark.parametrize("status", [301, 400, 401, 403, 409, 429])
def test_explicit_rejection_never_refreshes_or_replays(wire: Wire, status: int) -> None:
    wire.status = status
    wire.body = b"secret"
    az = Tokens()
    with pytest.raises(ProviderError) as error:
        rest.write("k", ORG, az, "/p", "PUT", {})
    assert not isinstance(error.value, rest.WriteUncertain)
    assert error.value.code == ("az_not_logged_in" if status in (401, 403) else "az_failed")
    assert "secret" not in str(error.value)
    assert len(wire.requests) == az.calls == 1


@pytest.mark.parametrize("status", [301, 400, 401, 403, 409, 429, 500, 503])
@pytest.mark.parametrize("body_fault", ["timeout", "truncated", "oversize"])
def test_error_status_is_classified_without_reading_body(
    wire: Wire, monkeypatch: pytest.MonkeyPatch, status: int, body_fault: str
) -> None:
    wire.status = status
    monkeypatch.setattr(rest, "MAX_BODY", 8)
    if body_fault == "truncated":
        wire.body, wire.length = b"{}", "100"
    elif body_fault == "oversize":
        wire.body = b"secret" * 10
    reads = 0
    original = wire.read1

    def read_body(limit: int) -> bytes:
        nonlocal reads
        reads += 1
        if body_fault == "timeout":
            raise TimeoutError("secret")
        return original(limit)

    monkeypatch.setattr(wire, "read1", read_body)
    az = Tokens()
    with pytest.raises(ProviderError) as error:
        rest.write("k", ORG, az, "/p", "PUT", {})
    assert isinstance(error.value, rest.WriteUncertain) == (status >= 500)
    assert error.value.code == ("az_not_logged_in" if status in (401, 403) else "az_failed")
    assert f"HTTP {status}" in str(error.value) and "secret" not in str(error.value)
    assert len(wire.requests) == wire.closed == az.calls == 1
    assert reads == 0
    client = rest._pool["k"].client
    assert client.connection is None
    if status == 401:
        assert client.authorization == "" and client.expires == client.unavailable_until == 0


@pytest.mark.parametrize(
    "body,length",
    [(b"secret", None), (b"", None), (b"\xff", None), (b"{}", "12"), (b"{}", "secret")],
)
def test_invalid_or_truncated_response(wire: Wire, body: bytes, length: str | None) -> None:
    wire.body, wire.length = body, length
    with pytest.raises(rest.WriteUncertain) as error:
        rest.write("k", ORG, Tokens(), "/p", "POST", {})
    assert "secret" not in str(error.value)
    assert len(wire.requests) == wire.closed == 1


def test_auth_unavailable_only_before_dispatch(wire: Wire) -> None:
    az = Tokens()
    az.fail = True
    for _ in range(2):
        assert rest.write("k", ORG, az, "/p", "POST", {}) is rest.UNAVAILABLE
    assert az.calls == 1 and not wire.requests and wire.connections == 0


def test_pat_auth(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_EXT_PAT", "test-pat")
    az = Tokens()
    assert rest.write("k", ORG, az, "/p", "PUT", {}) == {"id": 42}
    assert az.calls == 0 and wire.requests[0][3]["Authorization"].startswith("Basic ")


def test_size_limits(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rest, "MAX_BODY", 2)
    with pytest.raises(ProviderError, match="write limit") as error:
        rest.write("k", ORG, Tokens(), "/p", "POST", {"secret": 1})
    assert not isinstance(error.value, rest.WriteUncertain) and not wire.requests
    with pytest.raises(rest.WriteUncertain, match="read limit"):
        rest.write("k", ORG, Tokens(), "/p", "POST", {})
    assert len(wire.requests) == wire.closed == 1


@pytest.mark.parametrize("stage", ["auth", "send", "body"])
def test_deadline(wire: Wire, monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    now = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    az = Tokens()
    target: Any = az if stage == "auth" else wire
    name = {"auth": "json", "send": "request", "body": "read1"}[stage]
    original = getattr(target, name)

    def slow(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        now[0] += rest.TIMEOUT + 1
        return result

    monkeypatch.setattr(target, name, slow)
    with pytest.raises(ProviderError) as error:
        rest.write("k", ORG, az, "/p", "POST", {})
    assert isinstance(error.value, rest.WriteUncertain) == (stage != "auth")
    assert len(wire.requests) == (0 if stage == "auth" else 1)


def test_busy_lease_never_dispatches(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    with rest.lease("k", ORG):
        monkeypatch.setattr(rest, "TIMEOUT", 0.001)
        with pytest.raises(ProviderError) as error:
            rest.write("k", ORG, Tokens(), "/p", "POST", {})
    assert error.value.code == "az_timeout" and not wire.requests


@pytest.mark.parametrize(
    "method,path,body",
    [("DELETE", "/p", {}), ("POST", "p", {}), ("POST", "/p?q=x", {}), ("POST", "/p", float("nan"))],
)
def test_invalid_request_never_dispatches(wire: Wire, method: str, path: str, body: Any) -> None:
    with pytest.raises((ValueError, ProviderError)):
        rest.write("k", ORG, Tokens(), path, method, body)
    assert not wire.requests and wire.connections == 0


@pytest.mark.parametrize("stage", ["send", "response", "body"])
def test_dns_failure_is_retryable_only_before_dispatch(
    wire: Wire,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    failure = socket.gaierror(socket.EAI_AGAIN, "secret")
    if stage == "send":

        def unresolved(*args: Any, **kwargs: Any) -> None:
            raise failure

        monkeypatch.setattr(wire, "request", unresolved)
    elif stage == "response":
        wire.failure = failure
    else:

        def unreadable(limit: int) -> bytes:
            raise failure

        monkeypatch.setattr(wire, "read1", unreadable)
    with pytest.raises(ProviderError) as error:
        rest.write("k", ORG, Tokens(), "/p", "PUT", {})
    assert isinstance(error.value, rest.WriteUncertain) == (stage != "send")
    assert error.value.code == "az_failed" and "secret" not in str(error.value)
    assert len(wire.requests) == (0 if stage == "send" else 1)
    assert wire.closed == 1 and rest._pool["k"].client.connection is None
    if stage == "send":
        assert error.value.retryable and "could not resolve host" in error.value.message
