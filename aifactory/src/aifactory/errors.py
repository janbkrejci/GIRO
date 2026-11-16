"""Errors shared by domain services, task runs and the dashboard."""

from __future__ import annotations


class UsageError(Exception):
    """A request the service does not understand (``usage_error``)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class TaskRunError(Exception):
    """The task cannot be started (or its runs cannot be read). Nothing was created."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
