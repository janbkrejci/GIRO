"""Bounded HTTPS requests to Forgejo's REST API."""

from __future__ import annotations

import http.client
import json
from typing import Any
from urllib.parse import urlencode, urlsplit

from aifactory.providers.base import ProviderError

URL_ENV = "GIRO_FORGEJO_URL"
TOKEN_ENV = "GIRO_FORGEJO_TOKEN"
NOT_CONFIGURED = "forgejo_not_configured"
TIMEOUT = 30.0
MAX_BODY = 16 * 1024 * 1024
LOG_BYTES = 128 * 1024


class ForgejoHttpError(ProviderError):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__("forgejo_failed", f"Forgejo REST returned HTTP {status}")


class ForgejoRest:
    def __init__(self, base_url: str, token: str) -> None:
        base_url, token = base_url.strip(), token.strip()
        if not base_url:
            raise ProviderError(
                NOT_CONFIGURED, f"{URL_ENV} is unset (process environment or $GIRO_HOME/env)"
            )
        try:
            url = urlsplit(base_url)
            host, port = url.hostname, url.port
        except ValueError:
            host = None
        if (
            not host
            or url.scheme != "https"
            or url.username is not None
            or url.query
            or url.fragment
            or any(char.isspace() for char in base_url)
        ):
            raise ProviderError("invalid_config", f"{URL_ENV} must be a Forgejo HTTPS base URL")
        if not token:
            raise ProviderError(
                NOT_CONFIGURED, f"{TOKEN_ENV} is unset (process environment or $GIRO_HOME/env)"
            )
        if not token.isascii() or not token.isprintable() or any(char.isspace() for char in token):
            raise ProviderError("invalid_config", f"{TOKEN_ENV} must be a Forgejo access token")
        self.host = host
        self.port = port or 443
        self.prefix = url.path.rstrip("/")
        self._authorization = f"token {token}"

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, str] | None = None,
        log: bool = False,
    ) -> tuple[bytes, int | None]:
        target = f"{self.prefix}/api/v1{path}"
        if query:
            target += "?" + urlencode(query)
        headers = {
            "Authorization": self._authorization,
            "Accept": "text/plain" if log else "application/json",
        }
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode()
        if log:
            headers["Range"] = f"bytes=-{LOG_BYTES}"

        # A fixed HTTPS origin keeps credentials away from redirects and status target URLs.
        conn = http.client.HTTPSConnection(self.host, self.port, timeout=TIMEOUT)
        try:
            conn.request(method, target, body=payload, headers=headers)
            with conn.getresponse() as response:
                if not 200 <= response.status < 300:
                    raise ForgejoHttpError(response.status)
                limit = LOG_BYTES if log else MAX_BODY
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise ProviderError(
                        "forgejo_failed", "Forgejo REST response exceeds read limit"
                    )
                count = response.getheader("X-Total-Count") or ""
                return data, int(count) if count.isascii() and count.isdecimal() else None
        except TimeoutError:
            raise ProviderError("forgejo_timeout", "Forgejo REST request timed out") from None
        except (OSError, ValueError, http.client.HTTPException):
            raise ProviderError("forgejo_failed", "Forgejo REST connection failed") from None
        finally:
            conn.close()

    def json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, str] | None = None,
    ) -> tuple[Any, int | None]:
        data, count = self.request(method, path, query=query, body=body)
        try:
            return json.loads(data), count
        except (ValueError, UnicodeError):
            raise ProviderError("forgejo_failed", "Forgejo REST returned invalid JSON") from None
