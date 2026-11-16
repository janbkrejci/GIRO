"""Dashboard-wide routes: health, stale code and restart, updates, the guide and limits."""

from __future__ import annotations

from collections.abc import Callable

from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from aifactory import __version__, guide
from aifactory.codeprint import CodeWatch
from aifactory.skill.envelope import envelope_fail, envelope_ok
from aifactory.web import server as web_server
from aifactory.web.limits import LimitsSource
from aifactory.web.routes.common import _repo
from aifactory.web.updates import Updates

APP_NAME = web_server.APP_NAME


async def code_state(request: Request) -> JSONResponse:
    """Whether the package on disk changed since the dashboard started (``stale``)."""
    watch: CodeWatch = request.app.state.code
    current = await run_in_threadpool(watch.current)
    data = {"stale": current != watch.started, "started": watch.started, "current": current}
    return JSONResponse(envelope_ok(data))


async def restart_dashboard(request: Request) -> JSONResponse:
    """Answer, then replace the server process with a fresh one running the code on disk."""
    restart: Callable[[], None] = request.app.state.restart
    return JSONResponse(envelope_ok({"restarting": True}), background=BackgroundTask(restart))


async def updates_check(request: Request) -> JSONResponse:
    source: Updates = request.app.state.updates
    data = await run_in_threadpool(source.check, request.query_params.get("fresh") == "1")
    return JSONResponse(envelope_ok(data))


async def guide_get(request: Request) -> JSONResponse:
    """The user and administrator guide: every chapter with its markdown."""
    # Preserve the existing response for clients without a locale parameter.
    locale = request.query_params.get("locale", "cs")
    if locale not in {"en", "cs"}:
        locale = "en"
    items = await run_in_threadpool(guide.chapters, locale=locale)
    return JSONResponse(envelope_ok({"chapters": [item.as_dict() for item in items]}))


def _runs_active(count: int) -> JSONResponse:
    """409 for an update refused while task runs are active.

    Every run is a separate ``factory`` process from the same tool environment the update
    replaces: on Windows its locked files make ``uv tool install --force`` fail half way,
    elsewhere the run would import modules of the new version into the old one.
    """
    if count == 1:
        runs = "běží 1 task"
    elif 2 <= count <= 4:
        runs = f"běží {count} tasky"
    else:
        runs = f"běží {count} tasků"
    message = (
        f"Aktualizace počká, {runs}. Instalace nahrazuje soubory GIRO, které běžící tasky "
        "používají, a mohla by je shodit. Až tasky doběhnou, klikni na chip znovu."
    )
    return JSONResponse(envelope_fail("runs_active", message), status_code=409)


async def _active_runs(request: Request) -> int:
    """How many task runs are active: launched by this server or seen in the overview."""
    count = len(request.app.state.launcher.running())
    overview = getattr(request.app.state, "overview", None)
    if overview is not None:
        data, _ = await overview.collect()
        count = max(count, int(data["totals"]["running"]))
    return count


async def updates_install(request: Request) -> JSONResponse:
    from aifactory.versioning import UpgradeError

    source: Updates = request.app.state.updates
    if active := await _active_runs(request):
        return _runs_active(active)
    try:
        data = await run_in_threadpool(source.begin)
    except UpgradeError as exc:
        return JSONResponse(envelope_fail(exc.code, exc.message), status_code=409)
    # The fresh release request can take seconds. A run may have started meanwhile;
    # begin now blocks new writes, so check again before scheduling installation.
    if active := await _active_runs(request):
        source.cancel()
        return _runs_active(active)
    return JSONResponse(
        envelope_ok(data),
        status_code=202,
        background=BackgroundTask(source.install, request.app.state.restart),
    )


async def limits(request: Request) -> JSONResponse:
    """Session limits of the subscriptions the repository uses or this machine has."""
    source: LimitsSource = request.app.state.limits
    data = await run_in_threadpool(source.get, _repo(request))
    return JSONResponse(envelope_ok(data))


async def global_limits(request: Request) -> JSONResponse:
    """Session limits of the subscriptions available on this machine (no repository)."""
    source: LimitsSource = request.app.state.limits
    data = await run_in_threadpool(source.get, None)
    return JSONResponse(envelope_ok(data))


async def multi_health(request: Request) -> JSONResponse:
    data = {"app": APP_NAME, "version": __version__, "home": str(request.app.state.home)}
    return JSONResponse(envelope_ok(data))


REPO_ROUTES: list[BaseRoute] = [
    Route("/limits", limits, methods=["GET"]),
]
"""Routes of one repository, relative to ``/api/repos/{repo_id}``."""


ROUTES: list[BaseRoute] = [
    Route("/health", multi_health, methods=["GET"]),
    Route("/code", code_state, methods=["GET"]),
    Route("/restart", restart_dashboard, methods=["POST"]),
    Route("/updates", updates_check, methods=["GET"]),
    Route("/guide", guide_get, methods=["GET"]),
    Route("/updates/install", updates_install, methods=["POST"]),
    Route("/limits", global_limits, methods=["GET"]),
]
"""Dashboard-wide routes, relative to ``/api``."""
