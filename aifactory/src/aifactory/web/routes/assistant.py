"""``/api/assistant/``: the AI guide's threads, messages, actions and stream."""

from __future__ import annotations

from collections.abc import AsyncIterator

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import BaseRoute, Route

from aifactory.skill.envelope import envelope_fail, envelope_ok
from aifactory.web import backlog
from aifactory.web.assistant import AssistantError, AssistantManager
from aifactory.web.routes.common import _json_body, _usage_error
from aifactory.web.routes.live import _PING_S, _sse


def _assistant(request: Request) -> AssistantManager:
    manager: AssistantManager = request.app.state.assistant
    return manager


def _assistant_error(exc: AssistantError) -> JSONResponse:
    return JSONResponse(envelope_fail(exc.code, exc.message), status_code=exc.status)


async def assistant_threads(request: Request) -> JSONResponse:
    """The guide's threads, newest first."""
    data = await run_in_threadpool(_assistant(request).summaries)
    return JSONResponse(envelope_ok({"threads": data}))


async def assistant_harnesses(request: Request) -> JSONResponse:
    """Supported local choices, without invoking a model or checking login."""
    try:
        data = await run_in_threadpool(_assistant(request).harnesses)
    except AssistantError as exc:
        return _assistant_error(exc)
    return JSONResponse(envelope_ok({"harnesses": data}))


async def assistant_create(request: Request) -> JSONResponse:
    """Create ``{title?, harness?, screen?: {repo?, task?, run?}}``.

    Explicit harness uses its computer model/thinking; otherwise inherit as before.
    Invalid context is HTTP 400 ``usage_error``; unavailable harness is HTTP 409
    ``harness_unavailable``. Explicit choices never fall back.
    """
    try:
        body = await _json_body(request) if await request.body() else {}
        data = await run_in_threadpool(_assistant(request).create, body)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except AssistantError as exc:
        return _assistant_error(exc)
    return JSONResponse(envelope_ok(data), status_code=201)


async def assistant_thread(request: Request) -> JSONResponse:
    """One thread with its messages."""
    try:
        data = await run_in_threadpool(_assistant(request).get, request.path_params["thread_id"])
    except AssistantError as exc:
        return _assistant_error(exc)
    return JSONResponse(envelope_ok(data))


async def assistant_delete(request: Request) -> JSONResponse:
    """Delete a thread of the guide for good (409 ``busy`` while it answers)."""
    try:
        data = await run_in_threadpool(_assistant(request).delete, request.path_params["thread_id"])
    except AssistantError as exc:
        return _assistant_error(exc)
    return JSONResponse(envelope_ok(data))


async def assistant_send(request: Request) -> JSONResponse:
    """Send ``{text, screen?: {repo, task, run}}``; the answer streams on ``/stream``."""
    try:
        body = await _json_body(request)
        data = await run_in_threadpool(
            _assistant(request).send, request.path_params["thread_id"], body
        )
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except AssistantError as exc:
        return _assistant_error(exc)
    return JSONResponse(envelope_ok(data), status_code=202)


async def assistant_action(request: Request) -> JSONResponse:
    """Record the outcome of a proposed action (``{status, message?}``); writes nothing else."""
    try:
        body = await _json_body(request)
        data = await run_in_threadpool(
            _assistant(request).record_action,
            request.path_params["thread_id"],
            request.path_params["action_id"],
            body,
        )
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except AssistantError as exc:
        return _assistant_error(exc)
    return JSONResponse(envelope_ok(data))


async def assistant_stream(request: Request) -> Response:
    """Server-Sent Events of the current answer after ``?after=SEQ``: ``start``, ``text``,
    ``tool``, then ``done`` or ``error``; the stream ends when the thread is idle."""
    manager = _assistant(request)
    thread_id: str = request.path_params["thread_id"]
    try:
        after = int(request.query_params.get("after") or 0)
        await run_in_threadpool(manager.get, thread_id)
    except ValueError:
        return _usage_error("after must be an integer")
    except AssistantError as exc:
        return _assistant_error(exc)

    async def stream() -> AsyncIterator[str]:
        nonlocal after
        yield "retry: 2000\n\n"
        while True:
            events, busy = await run_in_threadpool(manager.wait, thread_id, after, _PING_S)
            for event in events:
                after = event["seq"]
                yield f"id: {after}\n" + _sse(event["type"], {"seq": after, **event["data"]})
            if not busy and not events:
                yield _sse("idle", {"seq": after})
                return
            if not events:
                if await request.is_disconnected():
                    return
                yield ": ping\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


ROUTES: list[BaseRoute] = [
    Route("/assistant/harnesses", assistant_harnesses, methods=["GET"]),
    Route("/assistant/threads", assistant_threads, methods=["GET"]),
    Route("/assistant/threads", assistant_create, methods=["POST"]),
    Route("/assistant/threads/{thread_id}", assistant_thread, methods=["GET"]),
    Route("/assistant/threads/{thread_id}", assistant_delete, methods=["DELETE"]),
    Route("/assistant/threads/{thread_id}/messages", assistant_send, methods=["POST"]),
    Route("/assistant/threads/{thread_id}/stream", assistant_stream, methods=["GET"]),
    Route(
        "/assistant/threads/{thread_id}/actions/{action_id}",
        assistant_action,
        methods=["POST"],
    ),
]
"""Dashboard-wide routes, relative to ``/api``."""
