"""Background advice jobs on the backlog: task and workflow advice, intents and lessons."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from aifactory.backlog import TaskEditError
from aifactory.config import ConfigError
from aifactory.errors import TaskRunError
from aifactory.skill.envelope import envelope_fail, envelope_ok
from aifactory.web import backlog
from aifactory.web.routes.common import _edit_error, _json_body, _repo, _usage_error


async def workflow_advice_start(request: Request) -> JSONResponse:
    try:
        body = await _json_body(request)
        data = await run_in_threadpool(
            request.app.state.workflow_advice.start, _repo(request), body
        )
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data), status_code=202)


async def workflow_advice_get(request: Request) -> JSONResponse:
    try:
        data = request.app.state.workflow_advice.get(
            _repo(request), request.path_params["job_id"]
        ).json()
    except TaskEditError as exc:
        return JSONResponse(envelope_fail(exc.code, exc.message), status_code=404)
    return JSONResponse(envelope_ok(data))


async def _intent_call(
    request: Request, call: Callable[..., dict[str, Any]], *args: Any, status: int = 200
) -> JSONResponse:
    """One request of the Nový záměr dialog (``web.intent``)."""
    try:
        data = await run_in_threadpool(call, _repo(request), *args)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, list(data.get("warnings") or [])), status_code=status)


async def intent_start(request: Request) -> JSONResponse:
    """Start a proposal from an intent: ``{intent}`` (the agent places it), or with
    ``project`` (a new step) or ``step`` (new tasks) -> the job."""
    try:
        body = await _json_body(request)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    return await _intent_call(request, request.app.state.intent.start, body, status=202)


async def intent_get(request: Request) -> JSONResponse:
    """The proposal job: state, progress of the current turn, the proposal."""
    manager = request.app.state.intent
    return await _intent_call(
        request, lambda repo, job: manager.get(repo, job).json(), request.path_params["job_id"]
    )


async def intent_refine(request: Request) -> JSONResponse:
    """``{note, tasks?}``: the agent revises the proposal in the same session."""
    try:
        body = await _json_body(request)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    return await _intent_call(
        request, request.app.state.intent.refine, request.path_params["job_id"], body, status=202
    )


async def intent_apply(request: Request) -> JSONResponse:
    """``{tasks?}``: write the edited proposal to the backlog and commit it."""
    try:
        body = await _json_body(request)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    return await _intent_call(
        request, request.app.state.intent.apply, request.path_params["job_id"], body
    )


async def intent_discard(request: Request) -> JSONResponse:
    """Close the proposal without writing anything."""
    return await _intent_call(
        request, request.app.state.intent.discard, request.path_params["job_id"]
    )


async def _learn_call(
    request: Request, call: Callable[..., dict[str, Any]], *args: Any, status: int = 200
) -> JSONResponse:
    """One request of the Lekce section (``web.learn``)."""
    try:
        data = await run_in_threadpool(call, _repo(request), *args)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    warnings = data.pop("warnings", None) if isinstance(data.get("warnings"), list) else None
    return JSONResponse(envelope_ok(data, list(warnings or [])), status_code=status)


async def learn_state(request: Request) -> JSONResponse:
    """The lessons and the repo's scan job."""
    return await _learn_call(request, request.app.state.learn.state)


async def learn_scan(request: Request) -> JSONResponse:
    """``{since?, all?}``: start ``factory learn scan`` (``--all``) in the background."""
    try:
        body = await _json_body(request)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    return await _learn_call(request, request.app.state.learn.start, body, status=202)


async def learn_accept(request: Request) -> JSONResponse:
    """``{project?}``: the lesson becomes a backlog task (``factory learn accept``)."""
    try:
        body = await _json_body(request)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    return await _learn_call(
        request, request.app.state.learn.accept, request.path_params["lesson_id"], body
    )


async def learn_dismiss(request: Request) -> JSONResponse:
    """``{reason?}``: reject the lesson for good (``factory learn dismiss``)."""
    try:
        body = await _json_body(request)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    return await _learn_call(
        request, request.app.state.learn.dismiss, request.path_params["lesson_id"], body
    )


async def task_advice_start(request: Request) -> JSONResponse:
    try:
        data = await run_in_threadpool(
            request.app.state.task_advice.start, _repo(request), await _json_body(request)
        )
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data), status_code=202)


async def task_advice_get(request: Request) -> JSONResponse:
    try:
        data = request.app.state.task_advice.get(
            _repo(request), request.path_params["job_id"]
        ).json()
    except TaskEditError as exc:
        return JSONResponse(envelope_fail(exc.code, exc.message), status_code=404)
    return JSONResponse(envelope_ok(data))


REPO_ROUTES: list[BaseRoute] = [
    Route("/backlog/task-advice", task_advice_start, methods=["POST"]),
    Route("/backlog/task-advice/{job_id}", task_advice_get, methods=["GET"]),
    Route("/backlog/workflow-advice", workflow_advice_start, methods=["POST"]),
    Route("/backlog/workflow-advice/{job_id}", workflow_advice_get, methods=["GET"]),
    Route("/backlog/intent", intent_start, methods=["POST"]),
    Route("/backlog/intent/{job_id}", intent_get, methods=["GET"]),
    Route("/backlog/intent/{job_id}/refine", intent_refine, methods=["POST"]),
    Route("/backlog/intent/{job_id}/apply", intent_apply, methods=["POST"]),
    Route("/backlog/intent/{job_id}/discard", intent_discard, methods=["POST"]),
    Route("/learn", learn_state, methods=["GET"]),
    Route("/learn/scan", learn_scan, methods=["POST"]),
    Route("/learn/lessons/{lesson_id}/accept", learn_accept, methods=["POST"]),
    Route("/learn/lessons/{lesson_id}/dismiss", learn_dismiss, methods=["POST"]),
]
"""Routes of one repository, relative to ``/api/repos/{repo_id}``."""
