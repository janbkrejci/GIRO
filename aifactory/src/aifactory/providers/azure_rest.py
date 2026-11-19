"""Pooled Azure DevOps HTTPS transport.

Only reads retry: writes dispatch once and report ambiguous outcomes for read-back.
Credentials stay in memory. Callers gate custom servers/proxies/CA overrides.
"""

from __future__ import annotations

import atexit
import base64
import http.client
import json
import math
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

from aifactory.providers.base import ProviderError

RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"
TIMEOUT = 30.0
MAX_CLIENTS = 16
IDLE_TTL = 300.0
MAX_BODY = 16 * 1024 * 1024
UNAVAILABLE = object()


class WriteUncertain(ProviderError):
    """A dispatched write may have succeeded; reconcile by reading, never replay."""

    def __init__(self, message: str) -> None:
        super().__init__("az_failed", message)


class TokenSource(Protocol):
    def json(self, *args: str) -> Any: ...


def supported(organization: str) -> bool:
    try:
        url = urlsplit(organization)
        port = url.port
    except ValueError:
        return False
    host = url.hostname or ""
    return (
        url.scheme == "https"
        and port in (None, 443)
        and not url.username
        and not url.password
        and not url.query
        and not url.fragment
        and (host == "dev.azure.com" or host.endswith(".visualstudio.com"))
        and not any(
            val
            for name, val in os.environ.items()
            if name.upper()
            in {
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "REQUESTS_CA_BUNDLE",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
                "AZURE_CLI_DISABLE_CONNECTION_VERIFICATION",
            }
        )
    )


class ReadClient:
    def __init__(self, organization: str) -> None:
        url = urlsplit(organization)
        self.host = url.hostname or ""
        self.prefix = url.path.rstrip("/")
        self.connection: http.client.HTTPSConnection | None = None
        self.authorization = ""
        self.expires = 0.0
        self.unavailable_until = 0.0

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def authenticate(self, az: TokenSource) -> bool:
        pat = os.environ.get("AZURE_DEVOPS_EXT_PAT")
        if pat:
            self.authorization = "Basic " + base64.b64encode(f":{pat}".encode()).decode()
            return True
        if self.authorization and time.time() < self.expires - 60:
            return True
        if time.monotonic() < self.unavailable_until:
            return False
        try:
            data = az.json(
                "account",
                "get-access-token",
                "--resource",
                RESOURCE,
                "--output",
                "json",
                "--only-show-errors",
            )
            token = data.get("accessToken") if isinstance(data, dict) else None
            expires = float(data.get("expires_on", 0)) if isinstance(data, dict) else 0.0
            if (
                not isinstance(token, str)
                or not token
                or not math.isfinite(expires)
                or expires <= time.time() + 60
            ):
                raise ValueError("no unexpired token")
        except (ProviderError, ValueError, TypeError, OverflowError):
            # CLI may use credentials (e.g. its own PAT store) unavailable to REST.
            self.unavailable_until = time.monotonic() + 60
            return False
        self.authorization = f"Bearer {token}"
        self.expires = expires
        return True

    def write(
        self, path: str, method: str, body: bytes, params: dict[str, str], *, deadline: float
    ) -> Any:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("az_timeout", "Azure REST write exceeded its deadline")
        target = self.prefix + path + "?" + urlencode(params)
        if self.connection is None:
            self.connection = http.client.HTTPSConnection(self.host, timeout=remaining)
        try:
            self.connection.timeout = remaining
            if self.connection.sock is not None:
                self.connection.sock.settimeout(remaining)
            # No retry, even when request() fails: bytes may already have been sent.
            self.connection.request(
                method,
                target,
                body=body,
                headers={
                    "Authorization": self.authorization,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WriteUncertain("Azure REST write exceeded its deadline")
            if self.connection.sock is not None:
                self.connection.sock.settimeout(remaining)
            response = self.connection.getresponse()
            status = response.status
            if not 200 <= status < 300:
                # A known rejection stays explicit even if its body is unreadable.
                # Discard the connection rather than drain an untrusted error body.
                self.close()
                if status >= 500:
                    raise WriteUncertain(f"Azure REST write returned HTTP {status}")
                if status == 401:
                    self.authorization = ""
                    self.expires = 0
                    self.unavailable_until = 0
                code = "az_not_logged_in" if status in (401, 403) else "az_failed"
                raise ProviderError(code, f"Azure REST write returned HTTP {status}")
            chunks: list[bytes] = []
            size = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WriteUncertain("Azure REST write exceeded its deadline")
                if self.connection.sock is not None:
                    self.connection.sock.settimeout(remaining)
                chunk = response.read1(min(65536, MAX_BODY + 1 - size))
                if time.monotonic() >= deadline:
                    raise WriteUncertain("Azure REST write exceeded its deadline")
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BODY:
                    raise WriteUncertain("Azure REST response exceeds the read limit")
            length = response.getheader("Content-Length")
            if length is not None and int(length) != size:
                raise WriteUncertain("Azure REST write returned a truncated response")
            return json.loads(b"".join(chunks))
        except WriteUncertain:
            self.close()
            raise
        except (OSError, http.client.HTTPException):
            self.close()
            raise WriteUncertain("Azure REST write connection failed") from None
        except (ValueError, UnicodeError):
            self.close()
            raise WriteUncertain("Azure REST write returned an invalid response") from None

    def get(
        self, az: TokenSource, path: str, params: dict[str, str], *, deadline: float
    ) -> tuple[Any, str | None]:
        target = self.prefix + path + "?" + urlencode(params)
        for attempt in range(3):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError("az_timeout", "Azure REST read exceeded its deadline")
            if self.connection is None:
                self.connection = http.client.HTTPSConnection(self.host, timeout=remaining)
            self.connection.timeout = remaining
            if self.connection.sock is not None:
                self.connection.sock.settimeout(remaining)
            try:
                self.connection.request(
                    "GET",
                    target,
                    headers={"Authorization": self.authorization, "Accept": "application/json"},
                )
                response = self.connection.getresponse()
                chunks: list[bytes] = []
                size = 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.close()
                        raise ProviderError("az_timeout", "Azure REST read exceeded its deadline")
                    if self.connection.sock is not None:
                        self.connection.sock.settimeout(remaining)
                    chunk = response.read1(min(65536, MAX_BODY + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > MAX_BODY:
                        break
                body = b"".join(chunks)
                status = response.status
                continuation = response.getheader("x-ms-continuationtoken")
                retry_after = response.getheader("Retry-After")
                if len(body) > MAX_BODY:
                    self.close()
                    raise ProviderError("az_failed", "Azure REST response exceeds the read limit")
            except (OSError, http.client.HTTPException):
                self.close()
                if attempt < 2 and time.monotonic() < deadline:
                    continue
                raise ProviderError("az_failed", "Azure REST connection failed") from None
            if status == 401 and attempt == 0:
                self.authorization = ""
                self.expires = 0
                self.unavailable_until = 0
                if self.authenticate(az):
                    continue
            if status in (429, 503) and attempt < 2:
                delay = _retry_delay(retry_after)
                if delay >= deadline - time.monotonic():
                    raise ProviderError("az_timeout", "Azure REST retry exceeds the read deadline")
                time.sleep(delay)
                continue
            if not 200 <= status < 300:
                # Never return response bodies/headers, URLs or credentials in diagnostics.
                code = "az_not_logged_in" if status in (401, 403) else "az_failed"
                raise ProviderError(code, f"Azure REST read returned HTTP {status}")
            try:
                return json.loads(body), continuation
            except (ValueError, UnicodeError):
                raise ProviderError("az_failed", "Azure REST read returned invalid JSON") from None
        raise ProviderError("az_failed", "Azure REST read exhausted retries")


def _retry_delay(value: str | None) -> float:
    if value:
        try:
            delay = float(value)
            return max(0.0, delay) if math.isfinite(delay) else TIMEOUT
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                pass
    return 1.0


@dataclass
class _Entry:
    client: ReadClient
    used: float = field(default_factory=time.monotonic)
    users: int = 0
    lock: Any = field(default_factory=threading.Lock)


_pool: dict[str, _Entry] = {}
_pool_lock = threading.Lock()


@contextmanager
def lease(key: str, organization: str) -> Iterator[ReadClient]:
    with _pool_lock:
        now = time.monotonic()
        for old_key, old_entry in list(_pool.items()):
            if not old_entry.users and now - old_entry.used >= IDLE_TTL:
                del _pool[old_key]
                old_entry.client.close()
        entry = _pool.get(key)
        if entry is None:
            if len(_pool) >= MAX_CLIENTS:
                idle = [(k, e) for k, e in _pool.items() if not e.users]
                if idle:
                    old_key, old = min(idle, key=lambda item: item[1].used)
                    del _pool[old_key]
                    old.client.close()
            entry = _Entry(ReadClient(organization))
            if len(_pool) < MAX_CLIENTS:
                _pool[key] = entry
        entry.users += 1
    try:
        if not entry.lock.acquire(timeout=TIMEOUT):
            raise ProviderError("az_timeout", "Azure REST read waited too long for its connection")
        try:
            yield entry.client
        finally:
            entry.lock.release()
    finally:
        with _pool_lock:
            entry.users -= 1
            entry.used = time.monotonic()
            if _pool.get(key) is not entry:
                entry.client.close()


def read(
    key: str,
    organization: str,
    az: TokenSource,
    path: str,
    params: dict[str, str] | None = None,
    *,
    collection: bool = False,
    limit: int | None = None,
) -> Any:
    """Fresh GET, or UNAVAILABLE when CLI-only authentication must be retained."""
    if not path.startswith("/") or "?" in path or "#" in path:
        raise ValueError("REST path must be an absolute path without a query")
    with lease(key, organization) as client:
        if not client.authenticate(az):
            return UNAVAILABLE
        deadline = time.monotonic() + TIMEOUT
        query = {"api-version": "7.1", **(params or {})}
        items: list[Any] = []
        seen: set[str] = set()
        while True:
            data, continuation = client.get(az, path, query, deadline=deadline)
            if not collection:
                return data
            if not isinstance(data, dict) or not isinstance(data.get("value"), list):
                raise ProviderError("az_failed", "Azure REST list returned an invalid collection")
            items.extend(data["value"])
            if limit is not None and len(items) >= limit:
                return items[:limit]
            if not continuation:
                return items
            if continuation in seen:
                raise ProviderError("az_failed", "Azure REST repeated a continuation token")
            seen.add(continuation)
            query["continuationToken"] = continuation


def write(
    key: str,
    organization: str,
    az: TokenSource,
    path: str,
    method: str,
    body: Any,
    params: dict[str, str] | None = None,
) -> Any:
    """Dispatch JSON once; UNAVAILABLE only for missing pre-dispatch authentication.

    WriteUncertain requires read-back reconciliation, never CLI fallback or replay.
    As for read(), callers must gate organizations with supported().
    """
    if not path.startswith("/") or "?" in path or "#" in path:
        raise ValueError("REST path must be an absolute path without a query")
    if method not in {"POST", "PATCH", "PUT"}:
        raise ValueError("REST write method must be POST, PATCH or PUT")
    try:
        payload = json.dumps(body, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ProviderError("az_failed", "Azure REST write requires a JSON body") from None
    if len(payload) > MAX_BODY:
        raise ProviderError("az_failed", "Azure REST request exceeds the write limit")
    with lease(key, organization) as client:
        deadline = time.monotonic() + TIMEOUT
        if not client.authenticate(az):
            return UNAVAILABLE
        return client.write(
            path, method, payload, {"api-version": "7.1", **(params or {})}, deadline=deadline
        )


@atexit.register
def close_all() -> None:
    with _pool_lock:
        for entry in _pool.values():
            entry.client.close()
        _pool.clear()
