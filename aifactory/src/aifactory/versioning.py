"""Version comparison and the upgrade error, kept stdlib-only for a light CLI start."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_NUMBER = re.compile(r"\d+")


def version_key(version: str) -> tuple[int, ...]:
    """Leading numbers of the dotted parts: ``0.10.2rc1`` is ``(0, 10, 2)``."""
    key: list[int] = []
    for part in version.split("."):
        match = _NUMBER.match(part)
        if match is None:
            break
        key.append(int(match.group()))
    return tuple(key)


class UpgradeError(Exception):
    """An expected failure of `factory upgrade` with a stable ``code`` and exit code."""

    def __init__(
        self, code: str, message: str, exit_code: int = 2, data: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.data = dict(data) if data is not None else None
