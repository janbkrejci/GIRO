"""``/api/library/``: the machine's library, its inventory, plan, apply and sync."""

from __future__ import annotations

import functools
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from aifactory.registry import RepoError
from aifactory.skill.envelope import envelope_ok
from aifactory.web import backlog, factory, library
from aifactory.web.routes.common import _json_body, _repo_error, _usage_error
from aifactory.web.routes.factory import _failure, _flag
from aifactory.web.routes.repos import _registry


async def library_get(request: Request) -> JSONResponse:
    try:
        items = "items" not in request.query_params or _flag(request, "items")
        data, warnings = await run_in_threadpool(
            functools.partial(
                library.status, request.app.state.home, items=items, fetch=_flag(request, "fetch")
            )
        )
    except factory.Failure as exc:
        return _failure(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def library_item(request: Request) -> JSONResponse:
    try:
        data = await run_in_threadpool(
            library.detail,
            request.app.state.home,
            request.path_params["type"],
            request.path_params["name"],
            request.query_params.get("version"),
        )
    except factory.Failure as exc:
        return _failure(exc)
    return JSONResponse(envelope_ok(data))


async def library_overrides(request: Request) -> JSONResponse:
    """The registered repositories' own copies of library items (reads only)."""
    registry = _registry(request)

    def build() -> tuple[dict[str, Any], list[str]]:
        state, warnings = registry.snapshot()
        from aifactory.library.load import repo_path
        from aifactory.web.library_inventory import matches

        data = library.overrides(request.app.state.home, state.repos)
        rules = {entry.id: registry.library_rules(entry.id) for entry in state.repos}
        data["overrides"] = [
            row
            for row in data["overrides"]
            if not any(
                matches({**row, "path": repo_path(row["type"], row["name"])}, rule)
                for rule in rules[row["repo"]]
            )
        ]
        return data, warnings

    try:
        data, warnings = await run_in_threadpool(build)
    except factory.Failure as exc:
        return _failure(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def library_inventory(request: Request) -> JSONResponse:
    """Read repo inventory/details or save dashboard-only ignore rules."""
    from aifactory.web import library_inventory as inventory

    try:
        registry = _registry(request)
        if request.method == "GET":
            data = await run_in_threadpool(inventory.inventory, registry)
        elif request.url.path.endswith("/ignore"):
            repo, rules = inventory.rules_body(await _json_body(request))
            await run_in_threadpool(registry.set_library_rules, repo, rules)
            data = await run_in_threadpool(inventory.inventory, registry)
        else:
            data = await run_in_threadpool(inventory.detail, registry, await _json_body(request))
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except RepoError as exc:
        return _repo_error(exc)
    except factory.Failure as exc:
        return _failure(exc)
    except OSError as exc:
        return _failure(factory.Failure("repo_unavailable", str(exc), 409))
    return JSONResponse(envelope_ok(data))


async def library_plan(request: Request) -> JSONResponse:
    try:
        req = library.parse_body(await _json_body(request))
        data, warnings = await run_in_threadpool(
            library.plan,
            request.app.state.home,
            request.app.state.user_home,
            req,
        )
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except factory.Failure as exc:
        return _failure(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def library_apply(request: Request) -> JSONResponse:
    try:
        req = library.parse_body(await _json_body(request), apply=True)
        data, warnings = await run_in_threadpool(
            library.apply,
            request.app.state.home,
            request.app.state.user_home,
            req,
            request.app.state.library,
        )
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except factory.Failure as exc:
        return _failure(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def library_remote(request: Request) -> JSONResponse:
    """Share a library without a remote through an empty git remote."""
    try:
        url = library.parse_remote_body(await _json_body(request))
        data = await run_in_threadpool(
            library.connect, request.app.state.home, request.app.state.library, url
        )
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except factory.Failure as exc:
        return _failure(exc)
    return JSONResponse(envelope_ok(data))


async def library_sync(request: Request) -> JSONResponse:
    try:
        body = await _json_body(request) if await request.body() else {}
        if body:
            raise backlog.UsageError("pull and push accept no options")
        data = await run_in_threadpool(
            library.sync,
            request.app.state.home,
            request.app.state.library,
            push=request.url.path.endswith("/push"),
        )
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except factory.Failure as exc:
        return _failure(exc)
    return JSONResponse(envelope_ok(data))


ROUTES: list[BaseRoute] = [
    Route("/library", library_get, methods=["GET"]),
    Route("/library/items/{type}/{name}", library_item, methods=["GET"]),
    Route("/library/overrides", library_overrides, methods=["GET"]),
    Route("/library/inventory", library_inventory, methods=["GET"]),
    Route("/library/ignore", library_inventory, methods=["POST"]),
    Route("/library/repo-item", library_inventory, methods=["POST"]),
    Route("/library/plan", library_plan, methods=["POST"]),
    Route("/library/apply", library_apply, methods=["POST"]),
    Route("/library/remote", library_remote, methods=["POST"]),
    Route("/library/pull", library_sync, methods=["POST"]),
    Route("/library/push", library_sync, methods=["POST"]),
]
"""Dashboard-wide routes, relative to ``/api``."""
