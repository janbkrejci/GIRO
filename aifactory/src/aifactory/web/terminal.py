"""The hidden terminal's availability (loopback only); the PTY sessions are in ``terminal_pty``."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from aifactory.skill.envelope import envelope_ok
from aifactory.web.server import LOOPBACK


def _shell() -> str:
    if sys.platform == "win32":
        return os.environ.get("COMSPEC") or "cmd.exe"
    return os.environ.get("SHELL") or "/bin/sh"


def _enabled(request: Request) -> bool:
    return bool(
        request.app.state.terminal_enabled
        and request.client is not None
        and request.client.host in LOOPBACK
    )


def _pty_supported() -> bool:
    if sys.platform == "win32":
        try:
            import winpty  # type: ignore[import-not-found,import-untyped,unused-ignore]  # noqa: F401
        except ImportError:
            return False
        return True
    return os.name == "posix"


async def terminal_get(request: Request) -> Response:
    return JSONResponse(
        envelope_ok(
            {
                "enabled": _enabled(request),
                "cwd": str(Path.home()),
                "shell": _shell(),
                "pty": _pty_supported(),
            }
        )
    )
