"""The dashboard ASGI app: ``/api/`` answers in the CLI's JSON envelope, ``/`` is the Vue build.

The routes are in ``web.routes`` (one module per domain); this module adds the static
build, the middleware, the error handlers and the shared state.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import BaseRoute, Mount, Route, Router
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from aifactory.codeprint import CodeWatch
from aifactory.harness import settings as harness_settings
from aifactory.home import giro_home
from aifactory.registry import Registry, RepoError
from aifactory.run.store import is_db_busy
from aifactory.skill.envelope import envelope_fail
from aifactory.web import machine
from aifactory.web import server as web_server
from aifactory.web.assistant import AssistantManager
from aifactory.web.control import FactoryControl
from aifactory.web.fs import FolderPicker
from aifactory.web.guard import StaleCodeMiddleware, WriteGuardMiddleware, is_api_path
from aifactory.web.intent import IntentManager
from aifactory.web.launcher import Launcher, RunLauncher
from aifactory.web.learn import LearnManager
from aifactory.web.limits import LimitsSource
from aifactory.web.overview import Overview
from aifactory.web.pr_refresh import PR_REFRESH_INTERVAL, refresh_registered
from aifactory.web.repos import RepoContext, RepoContexts
from aifactory.web.routes import advice as advice_routes
from aifactory.web.routes import assistant as assistant_routes
from aifactory.web.routes import backlog as backlog_routes
from aifactory.web.routes import control as control_routes
from aifactory.web.routes import factory as factory_routes
from aifactory.web.routes import library as library_routes
from aifactory.web.routes import live as live_routes
from aifactory.web.routes import machine as machine_routes
from aifactory.web.routes import repos as repos_routes
from aifactory.web.routes import review as review_routes
from aifactory.web.routes import runs as runs_routes
from aifactory.web.routes import settings as settings_routes
from aifactory.web.routes import system as system_routes
from aifactory.web.routes import terminal as terminal_routes
from aifactory.web.routes.common import REPO_SCOPE_KEY, _db_busy, _repo_error
from aifactory.web.terminal_pty import close_sessions
from aifactory.web.updates import Updates
from aifactory.web.workflow_advice import AdviceManager
from aifactory.workflow.adaptive import cleanup_orphans

STATIC_DIR = Path(__file__).parent / "static"
"""The frontend build (``just web-build``), shipped inside the package."""


ALLOWED_HOSTS = ["127.0.0.1", "localhost"]


_NOT_BUILT = "dashboard frontend is not built; run: just web-build"


WORKTREE_CLEANUP_INTERVAL = 3600


log = logging.getLogger(__name__)


class _WorktreeCleanup:
    """Share per-repository exclusion between API jobs and periodic worker threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: set[Path] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def clean(self, repo: Path) -> None:
        from aifactory.review import clean_worktrees

        root = repo.resolve()
        with self._lock:
            if root in self._running:
                return
            self._running.add(root)
        try:
            clean_worktrees(root)
        except Exception:
            log.exception("Dashboard worktree cleanup failed for %s", root)
        finally:
            with self._lock:
                self._running.remove(root)

    def registered(self, registry: Registry) -> None:
        for entry in registry.snapshot()[0].repos:
            self.clean(Path(entry.path))

    def start(self, repo: Path) -> None:
        task = asyncio.create_task(run_in_threadpool(self.clean, repo))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        await asyncio.gather(*self._tasks, return_exceptions=True)


class RepoScope:
    """Put the request's ``RepoContext`` into the scope before the repository's routes."""

    def __init__(self, app: ASGIApp, resolve: Callable[[Scope], Awaitable[RepoContext]]) -> None:
        self.app = app
        self.resolve = resolve

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            ctx = await self.resolve(scope)
        except RepoError as exc:
            await _repo_error(exc)(scope, receive, send)
            return
        scope[REPO_SCOPE_KEY] = ctx
        await self.app(scope, receive, send)


async def frontend_missing(request: Request) -> PlainTextResponse:
    return PlainTextResponse(_NOT_BUILT, status_code=503)


def _is_api(request: Request) -> bool:
    return is_api_path(request.url.path)


async def _http_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, HTTPException)
    if _is_api(request):
        if exc.status_code == 404:
            code, message = "not_found", f"no API endpoint {request.method} {request.url.path}"
        else:
            code, message = "usage_error", f"HTTP {exc.status_code}: {exc.detail}"
        return JSONResponse(
            envelope_fail(code, message),
            status_code=exc.status_code,
            headers=exc.headers,
        )
    return PlainTextResponse(str(exc.detail), status_code=exc.status_code, headers=exc.headers)


def _raised_at(exc: Exception) -> str:
    """`` (in function, file.py:line)`` of the innermost frame of GIRO that raised ``exc``.

    An error such as ``FileNotFoundError`` from ``os.getcwd()`` names no file; the place in
    the code then says what failed.
    """
    place = ""
    tb = exc.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_code
        if str(tb.tb_frame.f_globals.get("__name__", "")).startswith("aifactory."):
            place = f" (in {code.co_name}, {Path(code.co_filename).name}:{tb.tb_lineno})"
        tb = tb.tb_next
    return place


async def _internal_error(request: Request, exc: Exception) -> Response:
    if _is_api(request) and is_db_busy(exc):
        return _db_busy()
    if _is_api(request):
        message = f"{type(exc).__name__}: {exc}{_raised_at(exc)}"
        return JSONResponse(envelope_fail("internal_error", message), status_code=500)
    return PlainTextResponse("Internal Server Error", status_code=500)


def _repo_routes() -> list[BaseRoute]:
    """The API of one repository, relative to its prefix."""
    return [
        *settings_routes.REPO_ROUTES,
        *system_routes.REPO_ROUTES,
        *runs_routes.REPO_ROUTES,
        *live_routes.REPO_ROUTES,
        *control_routes.REPO_ROUTES,
        *backlog_routes.REPO_ROUTES,
        *advice_routes.REPO_ROUTES,
        *review_routes.REPO_ROUTES,
        *factory_routes.REPO_ROUTES,
    ]


def _starlette(
    api: Mount,
    static_dir: Path | None,
    lifespan: Callable[[Starlette], Any],
    allowed_hosts: Sequence[str] = ALLOWED_HOSTS,
) -> Starlette:
    """The app around the ``/api`` mount: static build, middleware, error handlers.

    ``allowed_hosts`` are the accepted Host headers (``["*"]`` on a network address).
    """
    static = STATIC_DIR if static_dir is None else static_dir
    routes: list[BaseRoute] = [api]
    if (static / "index.html").is_file():
        routes.append(Mount("/", app=StaticFiles(directory=static, html=True), name="static"))
    else:
        routes.append(Route("/", frontend_missing))

    return Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[
            Middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts)),
            Middleware(WriteGuardMiddleware),
            Middleware(StaleCodeMiddleware),
        ],
        exception_handlers={
            HTTPException: _http_error,
            sqlite3.OperationalError: _internal_error,  # answered, not re-raised
            Exception: _internal_error,
        },
    )


def _shared_state(
    app: Starlette,
    launcher: Launcher | None,
    code_watch: CodeWatch | None,
    restart: Callable[[], None] | None,
    limits_source: LimitsSource | None,
) -> None:
    app.state.launcher = launcher if launcher is not None else RunLauncher()
    app.state.code = code_watch if code_watch is not None else CodeWatch()
    app.state.restart = restart if restart is not None else web_server.restart_process
    app.state.updates = Updates()
    app.state.control = FactoryControl()
    app.state.worktree_cleanup = _WorktreeCleanup()
    overview: Overview | None = getattr(app.state, "overview", None)
    if overview is not None:
        overview.control = app.state.control

    def enabled(name: str) -> bool:
        home = getattr(app.state, "home", None)
        choices = harness_settings.read(home)
        if choices:
            choice = choices.harnesses[name]
            return choice.enabled and not harness_settings.test_failed(name, choice.model, home)
        tests = harness_settings.read_tests(home).get(name, {})
        latest: dict[str, Any] = max(tests.values(), key=lambda t: t.get("at", ""), default={})
        return latest.get("ok") is not False

    def roots() -> list[Path]:
        """Repositories whose GIRO runs carry the Claude limits: the registered ones."""
        registry: Registry = app.state.registry
        return [Path(entry.path) for entry in registry.snapshot()[0].repos]

    app.state.limits = (
        limits_source if limits_source is not None else LimitsSource(enabled=enabled, roots=roots)
    )
    app.state.check_machine = None  # factory check: the real machine (tests set a fake)


def create_multi_app(
    *,
    home: Path | None = None,
    port: int | None = None,
    static_dir: Path | None = None,
    live_interval: float = 1.0,
    launcher: Launcher | None = None,
    code_watch: CodeWatch | None = None,
    restart: Callable[[], None] | None = None,
    limits_source: LimitsSource | None = None,
    user_home: Path | None = None,
    picker: FolderPicker | None = None,
    allowed_hosts: Sequence[str] = ALLOWED_HOSTS,
    background_checks: bool = False,
) -> Starlette:
    """The dashboard for every repository in the registry of ``home`` (default: GIRO home).

    ``port`` is the port the server listens on (``restart_required`` compares it with the
    registry). Each repository's API is under ``/api/repos/{repo_id}/``. ``user_home``
    (default: the user's home) bounds ``/api/fs/dirs`` and ``picker`` opens the folder dialog
    of ``/api/fs/pick``. ``live_interval`` is how often (seconds) the live hubs poll files and
    the trace DB. ``launcher`` starts the runs (default: a ``RunLauncher``, one process per
    run). ``code_watch`` notices a changed package (default: the installed one) and
    ``restart`` replaces the server process (default: ``server.restart_process``).
    ``limits_source`` reads the subscription limits (default: a ``LimitsSource`` with the real
    readers). ``allowed_hosts`` are the accepted Host headers (``server.allowed_hosts``).
    """
    home_dir = giro_home() if home is None else home
    registry = Registry(home_dir)
    contexts = RepoContexts(registry, live_interval)

    async def resolve(scope: Scope) -> RepoContext:
        return await contexts.resolve(scope["path_params"]["repo_id"])

    api = Mount(
        "/api",
        routes=[
            *system_routes.ROUTES,
            *terminal_routes.ROUTES,
            *machine_routes.ROUTES,
            *library_routes.ROUTES,
            *repos_routes.ROUTES,
            *live_routes.ROUTES,
            *assistant_routes.ROUTES,
            Mount("/repos/{repo_id}", app=RepoScope(Router(routes=_repo_routes()), resolve)),
        ],
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        from aifactory.web.monitoring import once, periodic

        checks: list[asyncio.Task[None]] = []
        if background_checks:
            checks = [
                asyncio.create_task(
                    once(
                        lambda: machine.check_view(
                            app.state.home,
                            app.state.library,
                            offline=False,
                            fresh=True,
                            machine=app.state.check_machine,
                        )
                    )
                ),
                # Advice directories left behind by a dashboard that ended mid-advice.
                asyncio.create_task(once(cleanup_orphans)),
                asyncio.create_task(periodic(app.state.updates.check, 60)),
                asyncio.create_task(periodic(lambda: app.state.limits.get(None), 60)),
                asyncio.create_task(
                    periodic(
                        lambda: app.state.worktree_cleanup.registered(app.state.registry),
                        WORKTREE_CLEANUP_INTERVAL,
                    )
                ),
                asyncio.create_task(
                    periodic(
                        lambda: refresh_registered(app.state.registry, launcher=app.state.launcher),
                        PR_REFRESH_INTERVAL,
                    )
                ),
                asyncio.create_task(
                    periodic(
                        lambda: app.state.library.harnesses(
                            app.state.home,
                            fresh=True,
                        ),
                        300,
                    )
                ),
            ]
        try:
            yield
        finally:
            for check in checks:
                check.cancel()
            await asyncio.gather(*checks, return_exceptions=True)
            await app.state.worktree_cleanup.close()
            await close_sessions(app.state.terminal_sessions)
            await run_in_threadpool(app.state.workflow_advice.close)
            await run_in_threadpool(app.state.task_advice.close)
            await run_in_threadpool(app.state.intent.close)
            await run_in_threadpool(app.state.learn.close)
            await run_in_threadpool(app.state.assistant.close)
            await contexts.aclose()

    app = _starlette(api, static_dir, lifespan, allowed_hosts)
    app.state.workflow_advice = AdviceManager()
    app.state.task_advice = AdviceManager(task_parameters=True)
    app.state.intent = IntentManager()
    app.state.learn = LearnManager()
    app.state.terminal_sessions = {}
    app.state.terminal_session_lock = asyncio.Lock()
    app.state.terminal_enabled = bool(allowed_hosts) and set(allowed_hosts) <= web_server.LOOPBACK
    app.state.library = machine.GlobalState()
    app.state.home = home_dir
    app.state.system_cleanup_lock = threading.Lock()
    app.state.served_port = port
    app.state.registry = registry
    app.state.assistant = AssistantManager(
        home_dir, lambda: [(e.id, Path(e.path)) for e in registry.snapshot()[0].repos]
    )
    app.state.overview = Overview(registry)
    app.state.repos = contexts
    app.state.user_home = Path.home() if user_home is None else user_home
    app.state.picker = FolderPicker() if picker is None else picker
    _shared_state(app, launcher, code_watch, restart, limits_source)
    return app
