"""The Factory tab: ``giro check``, library items, the agent roster, plan and apply."""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from aifactory.registry import Registry, RepoError
from aifactory.skill.envelope import envelope_fail, envelope_ok
from aifactory.web import backlog, factory, library
from aifactory.web.routes.common import _ctx, _json_body, _repo_error, _usage_error

_TRUE = ("1", "true", "yes")


_FALSE = ("", "0", "false", "no")


def _flag(request: Request, name: str) -> bool:
    raw = request.query_params.get(name)
    if raw is None or raw.lower() in _FALSE:
        return False
    if raw.lower() in _TRUE:
        return True
    raise HTTPException(400, f"{name} must be 1 or 0, got {raw!r}")


def _failure(exc: factory.Failure) -> JSONResponse:
    body = envelope_fail(exc.code, exc.message, data=exc.data, issues=exc.issues)
    return JSONResponse(body, status_code=exc.status)


async def factory_check(request: Request) -> JSONResponse:
    """``factory check`` of the repository (60 s cache, ``fresh=1``); HTTP 200 either way."""
    ctx = _ctx(request)
    offline, fresh = _flag(request, "offline"), _flag(request, "fresh")
    registry: Registry | None = getattr(request.app.state, "registry", None)
    try:
        reg_state = None
        if registry is not None:
            reg_state = (await run_in_threadpool(registry.snapshot))[0]
        data, ok, message = await run_in_threadpool(
            lambda: factory.check_view(
                ctx.root,
                ctx.factory,
                offline=offline,
                fresh=fresh,
                machine=request.app.state.check_machine,
                registry_state=reg_state,
            )
        )
    except factory.Failure as exc:
        return _failure(exc)
    except RepoError as exc:
        return _repo_error(exc)
    if ok:
        return JSONResponse(envelope_ok(data))
    return JSONResponse(envelope_fail("checks_failed", message, data=data))


async def factory_items(request: Request) -> JSONResponse:
    """The items of the repository with their origin (the repo's copy or the library)."""
    try:
        data = await run_in_threadpool(factory.items, _ctx(request).root)
    except factory.Failure as exc:
        return _failure(exc)
    return JSONResponse(envelope_ok(data))


async def factory_roster(request: Request) -> JSONResponse:
    try:
        read = (
            factory.roster_summary if request.query_params.get("summary") == "1" else factory.roster
        )
        data = await run_in_threadpool(read, _ctx(request).root)
    except factory.Failure as exc:
        return _failure(exc)
    return JSONResponse(envelope_ok(data))


async def factory_roster_set(request: Request) -> JSONResponse:
    """``factory config roster set`` on the working tree; ``dry_run`` previews the diff."""
    ctx = _ctx(request)
    try:
        change = factory.parse_roster_body(await _json_body(request))
        if change.dry_run:
            data = await run_in_threadpool(factory.roster_change, ctx.root, change)
        else:
            with factory.exclusive(ctx.factory):
                data = await run_in_threadpool(factory.roster_change, ctx.root, change, ctx.factory)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except RepoError as exc:
        return _repo_error(exc)
    except factory.Failure as exc:
        return _failure(exc)
    return JSONResponse(envelope_ok(data))


async def factory_plan(request: Request) -> JSONResponse:
    """The core plan of a Factory operation with its digest; reads only."""
    ctx = _ctx(request)
    try:
        req = factory.parse_plan_body(await _json_body(request))
        data, warnings = await run_in_threadpool(
            factory.plan, ctx.root, req, environ=library.environment(request.app.state.home)
        )
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except factory.Failure as exc:
        return _failure(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def factory_apply(request: Request) -> JSONResponse:
    """Recompute the plan and commit it to base or a PR when the digest still matches."""
    ctx = _ctx(request)
    try:
        req = factory.parse_apply_body(await _json_body(request))
        with factory.exclusive(ctx.factory):
            if req.action == "move":
                with library.exclusive(request.app.state.library):
                    try:
                        data, warnings = await run_in_threadpool(
                            factory.apply,
                            ctx.root,
                            req,
                            ctx.factory,
                            environ=library.environment(request.app.state.home),
                        )
                    finally:
                        request.app.state.library.invalidate()
            else:
                data, warnings = await run_in_threadpool(
                    factory.apply,
                    ctx.root,
                    req,
                    ctx.factory,
                    environ=library.environment(request.app.state.home),
                )
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except RepoError as exc:
        return _repo_error(exc)
    except factory.Failure as exc:
        return _failure(exc)
    return JSONResponse(envelope_ok(data, warnings))


REPO_ROUTES: list[BaseRoute] = [
    Route("/factory/check", factory_check, methods=["GET"]),
    Route("/factory/items", factory_items, methods=["GET"]),
    Route("/factory/roster", factory_roster, methods=["GET"]),
    Route("/factory/roster", factory_roster_set, methods=["POST"]),
    Route("/factory/plan", factory_plan, methods=["POST"]),
    Route("/factory/apply", factory_apply, methods=["POST"]),
]
"""Routes of one repository, relative to ``/api/repos/{repo_id}``."""
