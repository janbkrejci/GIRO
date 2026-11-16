"""A repository's review list and its PR actions: approve, return and resolve."""

from __future__ import annotations

from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from aifactory.config import ConfigError
from aifactory.errors import TaskRunError
from aifactory.review.errors import ReviewError
from aifactory.skill.envelope import envelope_fail, envelope_ok
from aifactory.web import backlog, review
from aifactory.web.launcher import Launcher
from aifactory.web.routes.common import _json_body, _repo, _run_error, _usage_error

_REVIEW_ERROR_STATUS = {
    "no_pr": 404,
    "unknown_task": 404,
    "unknown_run": 404,
    "no_succeeded_run": 409,
    "pr_exists": 409,
    "invalid_value": 400,
    "unknown_branch": 409,
    "pr_not_open": 409,
    "conflict": 409,
    "checks_head_unconfirmed": 409,
    "stacked_on_open_pr": 409,
    "already_running": 409,
    "task_running": 409,
    "dirty_worktree": 409,
    "branch_checked_out": 409,
    "missing_note": 400,
    "merge_failed": 502,
    "unknown_base": 500,
    "invalid_config": 500,
    "worktree_failed": 500,
    "sync_on_base": 409,
}


def _review_error(exc: ReviewError | TaskRunError | ConfigError) -> JSONResponse:
    if not isinstance(exc, ReviewError):
        return _run_error(exc)
    status = _REVIEW_ERROR_STATUS.get(exc.code, 500)
    return JSONResponse(envelope_fail(exc.code, exc.message), status_code=status)


async def review_list(request: Request) -> JSONResponse:
    """Open task PRs."""
    repo: Path = _repo(request)
    try:
        done = request.query_params.get("done", "").strip().lower() in ("1", "true", "yes")
        data, warnings = await run_in_threadpool(review.review_list, repo, done)
    except (ReviewError, TaskRunError, ConfigError) as exc:
        return _review_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def review_detail(request: Request) -> JSONResponse:
    """The task's newest PR with its diff, gates and tests, verdict and runs."""
    repo: Path = _repo(request)
    try:
        data, warnings = await run_in_threadpool(
            review.review_detail, repo, request.path_params["task_id"]
        )
    except (ReviewError, TaskRunError, ConfigError) as exc:
        return _review_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def review_approve(request: Request) -> JSONResponse:
    """``approve_task`` (``factory task approve``), including Azure's approval vote.

    On a hosting with CI checks the approve waits for them, so it runs as a separate
    process (HTTP 202, ``started``); otherwise the merge result (HTTP 200).
    """
    repo: Path = _repo(request)
    launcher: Launcher = request.app.state.launcher
    try:
        data, warnings = await run_in_threadpool(
            review.approve, repo, request.path_params["task_id"], launcher
        )
    except (ReviewError, TaskRunError, ConfigError) as exc:
        return _review_error(exc)
    status = 202 if data.get("started") else 200
    return JSONResponse(envelope_ok(data, warnings), status_code=status)


async def review_return(request: Request) -> JSONResponse:
    """``factory task return`` as a separate process; HTTP 202."""
    repo: Path = _repo(request)
    launcher: Launcher = request.app.state.launcher
    task_id: str = request.path_params["task_id"]
    try:
        body = await _json_body(request)
        data, warnings = await run_in_threadpool(review.start_return, repo, task_id, body, launcher)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (ReviewError, TaskRunError, ConfigError) as exc:
        return _review_error(exc)
    return JSONResponse(envelope_ok(data, warnings), status_code=202)


async def review_resolve(request: Request) -> JSONResponse:
    """``factory task resolve`` as a separate process; HTTP 202."""
    repo: Path = _repo(request)
    launcher: Launcher = request.app.state.launcher
    task_id: str = request.path_params["task_id"]
    try:
        body = await _json_body(request)
        data, warnings = await run_in_threadpool(
            review.start_resolve, repo, task_id, body, launcher
        )
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (ReviewError, TaskRunError, ConfigError) as exc:
        return _review_error(exc)
    return JSONResponse(envelope_ok(data, warnings), status_code=202)


REPO_ROUTES: list[BaseRoute] = [
    Route("/review", review_list, methods=["GET"]),
    Route("/review/{task_id}", review_detail, methods=["GET"]),
    Route("/review/{task_id}/approve", review_approve, methods=["POST"]),
    Route("/review/{task_id}/return", review_return, methods=["POST"]),
    Route("/review/{task_id}/resolve", review_resolve, methods=["POST"]),
]
"""Routes of one repository, relative to ``/api/repos/{repo_id}``."""
