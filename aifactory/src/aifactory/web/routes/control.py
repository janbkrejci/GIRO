"""The Overview's factory controls: start, pause, resume and stop for one repository."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from aifactory.config import ConfigError
from aifactory.errors import TaskRunError
from aifactory.skill.envelope import envelope_ok
from aifactory.web import backlog
from aifactory.web.control import FactoryControl
from aifactory.web.launcher import Launcher
from aifactory.web.routes.common import _json_body, _repo, _run_error, _usage_error


async def _control(
    request: Request, action: Callable[[Path], Any], status: int = 200
) -> JSONResponse:
    """One of the Overview's factory controls on the request's repository."""
    repo: Path = _repo(request)
    try:
        if await request.body() and await _json_body(request):
            return _usage_error("the request takes no fields")
        data = await run_in_threadpool(action, repo)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskRunError, ConfigError) as exc:
        return _run_error(exc)
    return JSONResponse(envelope_ok(data), status_code=status)


async def control_start(request: Request) -> JSONResponse:
    """Start ready tasks into the free parallel slots (``FactoryControl.start``)."""
    control: FactoryControl = request.app.state.control
    launcher: Launcher = request.app.state.launcher
    return await _control(request, lambda repo: control.start(repo, launcher))


async def control_pause(request: Request) -> JSONResponse:
    """Pause every running run of the repository."""
    control: FactoryControl = request.app.state.control
    return await _control(request, control.pause)


async def control_resume(request: Request) -> JSONResponse:
    """Resume every paused run of the repository."""
    control: FactoryControl = request.app.state.control
    return await _control(request, control.resume)


async def control_stop(request: Request) -> JSONResponse:
    """Stop every running run of the repository in the background; HTTP 202."""
    control: FactoryControl = request.app.state.control
    return await _control(request, control.stop, 202)


REPO_ROUTES: list[BaseRoute] = [
    Route("/control/start", control_start, methods=["POST"]),
    Route("/control/pause", control_pause, methods=["POST"]),
    Route("/control/resume", control_resume, methods=["POST"]),
    Route("/control/stop", control_stop, methods=["POST"]),
]
"""Routes of one repository, relative to ``/api/repos/{repo_id}``."""
