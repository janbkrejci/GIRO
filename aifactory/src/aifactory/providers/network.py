"""Bounded retries for network operations, with command-local diagnostics.

Retry whole guarded PR operations so their expected-head checks and read-back
reconciliation run again. Non-idempotent creates/comments are never blindly replayed.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import ParamSpec, TypeVar

from aifactory import procrun
from aifactory.providers.base import ProviderError

ATTEMPTS = 5
DELAYS = (10.0, 20.0, 40.0, 80.0)
_sleep = time.sleep
_log = logging.getLogger(__name__)
_records: ContextVar[list[dict[str, object]] | None] = ContextVar("network_records", default=None)
_active: ContextVar[int] = ContextVar("network_retry_attempt", default=0)
P = ParamSpec("P")
T = TypeVar("T")


def transient(detail: str) -> bool:
    """Explicit permanent failures win over accompanying network diagnostics."""
    text = detail.lower()
    if any(
        word in text
        for word in (
            "authentication",
            "unauthorized",
            "forbidden",
            "permission",
            "not found",
            "repository does not exist",
            "couldn't find remote ref",
            "not logged in",
            "certificate verify failed",
            "certificate verification failed",
            "conflict",
        )
    ) or re.search(r"\b(?:http\s*|error[: ]+)(?:401|403|404)\b", text):
        return False
    return bool(re.search(r"\b(?:http\s*|error[: ]+|status[: ]+)5\d\d\b", text)) or any(
        word in text
        for word in (
            "could not resolve",
            "couldn't resolve",
            "name resolution",
            "no such host",
            "getaddrinfo failed",
            "name or service not known",
            "connection reset",
            "connection aborted",
            "connection failed",
            "connection refused",
            "connection closed",
            "remote end closed",
            "remote end hung up",
            "operation too slow",
            "timed out",
            "timeout",
            "time out",
            "network is unreachable",
            "broken pipe",
            "unexpected eof",
            "http2 framing layer",
            "internal server error",
            "bad gateway",
            "service unavailable",
            "gateway timeout",
        )
    )


@contextmanager
def capture() -> Iterator[list[dict[str, object]]]:
    """Isolate one CLI invocation; inherited worker contexts share its diagnostics."""
    records: list[dict[str, object]] = []
    token = _records.set(records)
    try:
        yield records
    finally:
        _records.reset(token)


def diagnostics() -> list[dict[str, object]]:
    return list(_records.get() or [])


def retrying() -> bool:
    """Whether the guarded operation is being replayed after a transient failure."""
    return _active.get() > 1


def retry_network(operation: Callable[P, T]) -> Callable[P, T]:
    """Retry transient ProviderError, preserving its type/code on exhaustion.

    Nested guarded operations share the outer limit; a probe budget is never
    extended by a backoff. Diagnostics contain labels/counts, never URLs or secrets.
    """

    @wraps(operation)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        if _active.get():
            return operation(*args, **kwargs)
        token = _active.set(1)
        label = getattr(operation, "__qualname__", type(operation).__name__)
        attempt = 0
        failed = False
        saw_error = False
        try:
            for attempt in range(1, ATTEMPTS + 1):
                _active.set(attempt)
                try:
                    return operation(*args, **kwargs)
                except ProviderError as exc:
                    saw_error = True
                    delay = DELAYS[attempt - 1] if attempt < ATTEMPTS else None
                    left = procrun.remaining()
                    if (
                        delay is None
                        or not exc.retryable
                        or exc.code
                        in {
                            "invalid_config",
                            "conflict",
                            "az_not_logged_in",
                            "gh_missing",
                            "az_missing",
                            "forgejo_not_configured",
                        }
                        or not transient(exc.message)
                        or (left is not None and left <= delay)
                    ):
                        failed = True
                        raise
                    _log.warning(
                        "network_retry operation=%s attempt=%d/%d delay_seconds=%g",
                        label,
                        attempt,
                        ATTEMPTS,
                        delay,
                    )
                    _sleep(delay)
            raise AssertionError("unreachable")
        except BaseException:
            failed = True
            raise
        finally:
            _active.reset(token)
            if saw_error:
                record = {"operation": label, "attempts": attempt, "succeeded": not failed}
                records = _records.get()
                if records is not None:
                    records.append(record)
                _log.warning(
                    "network_result operation=%s attempts=%d succeeded=%s",
                    label,
                    attempt,
                    not failed,
                )

    return wrapped
