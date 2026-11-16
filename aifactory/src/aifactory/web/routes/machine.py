"""``/api/machine/``: the machine's harnesses, system information, cleanup and ``giro check``."""

from __future__ import annotations

from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from aifactory.harness import settings as harness_settings
from aifactory.skill.envelope import envelope_fail, envelope_ok
from aifactory.web import backlog, machine, system
from aifactory.web.routes.common import _json_body, _usage_error
from aifactory.web.routes.factory import _flag
from aifactory.web.routes.repos import _registry


async def machine_harnesses(request: Request) -> JSONResponse:
    try:
        home = request.app.state.home
        if request.method == "POST":
            body = await _json_body(request)
            await run_in_threadpool(harness_settings.write, body, home)
            request.app.state.library.invalidate()
        data = await run_in_threadpool(request.app.state.library.harnesses, home)
        return JSONResponse(envelope_ok(data))
    except (ValueError, OSError, backlog.UsageError) as exc:
        return _usage_error(str(exc))


async def machine_harness_test(request: Request) -> JSONResponse:
    from aifactory.harness.probe import test_and_record

    try:
        body = await _json_body(request)
        if (
            not {"harness", "model"} <= set(body) <= {"harness", "model", "thinking"}
            or not all(isinstance(body[key], str) for key in ("harness", "model"))
            or (body.get("thinking") is not None and not isinstance(body["thinking"], str))
        ):
            raise ValueError("give harness and model strings")
        result = await run_in_threadpool(
            test_and_record,
            body["harness"],
            body["model"],
            request.app.state.home,
            body.get("thinking"),
        )
        request.app.state.library.invalidate()
        # Localize from the outcome code, never by interpreting harness diagnostics.
        result = {**result, "error_code": None if result.get("ok") else "harness_test_failed"}
        return JSONResponse(envelope_ok(result))
    except (ValueError, OSError, backlog.UsageError) as exc:
        return _usage_error(str(exc))


async def machine_system(request: Request) -> JSONResponse:
    def work() -> dict[str, Any]:
        state, warnings = _registry(request).snapshot()
        return envelope_ok(system.system_info(request.app.state.home, state.repos), warnings)

    return JSONResponse(await run_in_threadpool(work))


async def machine_cleanup(request: Request) -> JSONResponse:
    if "origin" not in request.headers:
        return JSONResponse(
            envelope_fail("cross_origin", "Origin is required for system cleanup"), status_code=403
        )
    try:
        await _json_body(request)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)

    def work() -> dict[str, Any] | None:
        # Own the lock in the worker so cancellation cannot release it mid-cleanup.
        lock = request.app.state.system_cleanup_lock
        if not lock.acquire(blocking=False):
            return None
        try:
            state, warnings = _registry(request).snapshot()
            data = system.cleanup(request.app.state.home, state.repos)
            return envelope_ok(data, [*warnings, *data["warnings"]])
        finally:
            lock.release()

    result = await run_in_threadpool(work)
    if result is None:
        return JSONResponse(
            envelope_fail("busy", "system cleanup is already running"), status_code=409
        )
    return JSONResponse(result)


async def machine_check(request: Request) -> JSONResponse:
    offline, fresh = _flag(request, "offline"), _flag(request, "fresh")
    data, ok, message = await run_in_threadpool(
        machine.check_view,
        request.app.state.home,
        request.app.state.library,
        offline=offline,
        fresh=fresh,
        machine=request.app.state.check_machine,
    )
    return JSONResponse(
        envelope_ok(data)
        if ok
        else envelope_fail(
            "check_timeout" if data.get("timed_out") else "checks_failed",
            message,
            data=data,
        )
    )


async def machine_check_progress(request: Request) -> JSONResponse:
    """The step and elapsed time of the running machine check (``job``: None when idle)."""
    offline = _flag(request, "offline")
    return JSONResponse(envelope_ok(request.app.state.library.progress(offline)))


ROUTES: list[BaseRoute] = [
    Route("/machine/check", machine_check, methods=["GET"]),
    Route("/machine/progress", machine_check_progress, methods=["GET"]),
    Route("/machine/system", machine_system, methods=["GET"]),
    Route("/machine/cleanup", machine_cleanup, methods=["POST"]),
    Route("/machine/harnesses", machine_harnesses, methods=["GET", "POST"]),
    Route("/machine/harnesses/test", machine_harness_test, methods=["POST"]),
]
"""Dashboard-wide routes, relative to ``/api``."""
