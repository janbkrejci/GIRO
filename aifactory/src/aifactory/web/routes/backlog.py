"""A repository's backlog: tasks, containers, queue, status, commit, sync and task runs."""

from __future__ import annotations

from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from aifactory.backlog import TaskEditError
from aifactory.config import ConfigError
from aifactory.errors import TaskRunError
from aifactory.review import sync as review_sync
from aifactory.review.errors import ReviewError
from aifactory.skill.envelope import envelope_ok
from aifactory.web import backlog
from aifactory.web.launcher import Launcher
from aifactory.web.routes.common import _edit_error, _json_body, _repo, _usage_error
from aifactory.web.routes.review import _review_error


def backlog_list(request: Request) -> JSONResponse:
    repo: Path = _repo(request)
    try:
        data, warnings = backlog.backlog_view(
            repo,
            status=request.query_params.get("status") or None,
        )
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


def backlog_summary(request: Request) -> JSONResponse:
    try:
        data, warnings = backlog.summary(_repo(request))
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


def backlog_names(request: Request) -> JSONResponse:
    repo: Path = _repo(request)
    try:
        data, warnings = backlog.names(repo)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


def backlog_task(request: Request) -> JSONResponse:
    repo: Path = _repo(request)
    try:
        data, warnings = backlog.task_detail(repo, request.path_params["task_id"])
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


def backlog_task_documents(request: Request) -> JSONResponse:
    repo: Path = _repo(request)
    try:
        data, warnings = backlog.task_documents(repo, request.path_params["task_id"])
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def backlog_add(request: Request) -> JSONResponse:
    """Create a task with ``aifactory.backlog.add_task``, as ``factory task add`` does."""
    repo: Path = _repo(request)
    try:
        body = await _json_body(request)
        if "workflow_advice_id" in body:
            data, warnings = await run_in_threadpool(
                backlog.add, repo, body, advice=request.app.state.workflow_advice
            )
        else:
            data, warnings = await run_in_threadpool(backlog.add, repo, body)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def backlog_edit(request: Request) -> JSONResponse:
    """Edit a task (and assign its workflow) with ``aifactory.backlog.edit_task``."""
    repo: Path = _repo(request)
    task_id: str = request.path_params["task_id"]
    try:
        body = await _json_body(request)
        if "workflow_advice_id" in body:
            data, warnings = await run_in_threadpool(
                backlog.edit, repo, task_id, body, advice=request.app.state.workflow_advice
            )
        else:
            data, warnings = await run_in_threadpool(backlog.edit, repo, task_id, body)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    if body.get("status") == "cancelled":
        request.app.state.worktree_cleanup.start(repo)
    return JSONResponse(envelope_ok(data, warnings))


async def backlog_follow_up(request: Request) -> JSONResponse:
    """A task that follows a done task, as ``factory task follow-up`` creates it.

    With ``run`` the backlog is committed and the run started (HTTP 202)."""
    repo: Path = _repo(request)
    launcher: Launcher = request.app.state.launcher
    task_id: str = request.path_params["task_id"]
    try:
        body = await _json_body(request)
        data, warnings = await run_in_threadpool(backlog.follow_up, repo, task_id, body, launcher)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    status = 202 if body.get("run") is True else 200
    return JSONResponse(envelope_ok(data, warnings), status_code=status)


async def backlog_link(request: Request) -> JSONResponse:
    """Add or remove links of a task with ``aifactory.backlog.link_task``."""
    repo: Path = _repo(request)
    task_id: str = request.path_params["task_id"]
    try:
        body = await _json_body(request)
        data, warnings = await run_in_threadpool(backlog.link, repo, task_id, body)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def backlog_status(request: Request) -> JSONResponse:
    """Backlog files not committed to base (runs read base)."""
    repo: Path = _repo(request)
    try:
        data, warnings = await run_in_threadpool(backlog.status, repo)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def backlog_commit(request: Request) -> JSONResponse:
    """Commit the backlog changes to base with ``aifactory.backlog.commit_backlog``."""
    repo: Path = _repo(request)
    try:
        body = await _json_body(request)
        data, warnings = await run_in_threadpool(backlog.commit, repo, body)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def backlog_sync(request: Request) -> JSONResponse:
    """``factory backlog sync``: open (or extend) the sync PR for tasks merged outside factory."""
    repo: Path = _repo(request)
    try:
        result = await run_in_threadpool(review_sync.sync_backlog, repo)
    except (ReviewError, TaskRunError, ConfigError) as exc:
        return _review_error(exc)
    data = result.to_json()
    data.pop("ok", None)
    return JSONResponse(envelope_ok(data, list(result.warnings)))


async def backlog_run_check(request: Request) -> JSONResponse:
    """Warnings before a run: uncommitted config (D4), unmet dependencies, a running run."""
    repo: Path = _repo(request)
    launcher: Launcher = request.app.state.launcher
    task_id: str = request.path_params["task_id"]
    try:
        data, warnings = await run_in_threadpool(
            backlog.run_check, repo, task_id, launcher_busy=launcher.busy()
        )
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def backlog_run(request: Request) -> JSONResponse:
    """Start ``factory task run`` as a separate process; HTTP 202."""
    repo: Path = _repo(request)
    launcher: Launcher = request.app.state.launcher
    task_id: str = request.path_params["task_id"]
    try:
        body = await _json_body(request)
        data, warnings = await run_in_threadpool(backlog.start_run, repo, task_id, body, launcher)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings), status_code=202)


async def backlog_graph(request: Request) -> JSONResponse:
    """The dependency graph of a module or step."""
    repo: Path = _repo(request)
    try:
        data, warnings = await run_in_threadpool(
            backlog.container_graph, repo, request.path_params["container_id"]
        )
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


def backlog_container(request: Request) -> JSONResponse:
    repo: Path = _repo(request)
    try:
        data, warnings = backlog.container_detail(repo, request.path_params["container_id"])
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def backlog_container_add(request: Request) -> JSONResponse:
    """Create a project or step with ``aifactory.backlog.add_container`` (``backlog add``)."""
    repo: Path = _repo(request)
    try:
        body = await _json_body(request)
        data, warnings = await run_in_threadpool(backlog.add_container, repo, body)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def backlog_container_edit(request: Request) -> JSONResponse:
    """Edit a project or step with ``aifactory.backlog.edit_container`` (``backlog edit``)."""
    repo: Path = _repo(request)
    container_id: str = request.path_params["container_id"]
    try:
        body = await _json_body(request)
        data, warnings = await run_in_threadpool(backlog.edit_container, repo, container_id, body)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def backlog_auto_continue(request: Request) -> JSONResponse:
    """Set auto-continue of a module or step with ``aifactory.backlog.set_auto_continue``."""
    repo: Path = _repo(request)
    container_id: str = request.path_params["container_id"]
    try:
        body = await _json_body(request)
        data, warnings = await run_in_threadpool(backlog.auto_continue, repo, container_id, body)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def backlog_queue_order(request: Request) -> JSONResponse:
    """Save the kanban's order of ready tasks for auto-continue (trace DB ``task_queue``)."""
    repo: Path = _repo(request)
    try:
        body = await _json_body(request)
        data, warnings = await run_in_threadpool(backlog.queue_order, repo, body)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def backlog_auto_exclude(request: Request) -> JSONResponse:
    """Exclude a task from auto-continue, or include it again (``{excluded: bool}``)."""
    repo: Path = _repo(request)
    task_id: str = request.path_params["task_id"]
    try:
        body = await _json_body(request)
        data, warnings = await run_in_threadpool(backlog.auto_exclude, repo, task_id, body)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def backlog_archive(request: Request) -> JSONResponse:
    """Archive a done task, or show it again (``{archived: bool}``)."""
    repo: Path = _repo(request)
    task_id: str = request.path_params["task_id"]
    try:
        body = await _json_body(request)
        data, warnings = await run_in_threadpool(backlog.archive, repo, task_id, body)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def backlog_clear_priority(request: Request) -> JSONResponse:
    """Take the priority of a blocked task (or of a blocker) away (``task_priority``)."""
    repo: Path = _repo(request)
    task_id: str = request.path_params["task_id"]
    try:
        data, warnings = await run_in_threadpool(backlog.clear_priority, repo, task_id)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def backlog_auto_merge(request: Request) -> JSONResponse:
    """Set auto-merge of a module or step with ``aifactory.backlog.set_auto_merge``."""
    repo: Path = _repo(request)
    container_id: str = request.path_params["container_id"]
    try:
        body = await _json_body(request)
        data, warnings = await run_in_threadpool(backlog.auto_merge, repo, container_id, body)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, ConfigError, TaskRunError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


REPO_ROUTES: list[BaseRoute] = [
    Route("/backlog", backlog_list, methods=["GET"]),
    Route("/backlog/summary", backlog_summary, methods=["GET"]),
    Route("/backlog/names", backlog_names, methods=["GET"]),
    Route("/backlog/tasks", backlog_add, methods=["POST"]),
    Route("/backlog/status", backlog_status, methods=["GET"]),
    Route("/backlog/commit", backlog_commit, methods=["POST"]),
    Route("/backlog/sync", backlog_sync, methods=["POST"]),
    Route("/backlog/tasks/{task_id}", backlog_task, methods=["GET"]),
    Route("/backlog/tasks/{task_id}/documents", backlog_task_documents, methods=["GET"]),
    Route("/backlog/tasks/{task_id}/edit", backlog_edit, methods=["POST"]),
    Route("/backlog/tasks/{task_id}/link", backlog_link, methods=["POST"]),
    Route("/backlog/tasks/{task_id}/follow-up", backlog_follow_up, methods=["POST"]),
    Route("/backlog/tasks/{task_id}/run-check", backlog_run_check, methods=["GET"]),
    Route("/backlog/tasks/{task_id}/run", backlog_run, methods=["POST"]),
    Route("/backlog/tasks/{task_id}/auto-exclude", backlog_auto_exclude, methods=["POST"]),
    Route("/backlog/tasks/{task_id}/archive", backlog_archive, methods=["POST"]),
    Route("/backlog/tasks/{task_id}/priority", backlog_clear_priority, methods=["DELETE"]),
    Route("/backlog/queue/order", backlog_queue_order, methods=["POST"]),
    Route("/backlog/containers", backlog_container_add, methods=["POST"]),
    Route("/backlog/containers/{container_id}", backlog_container, methods=["GET"]),
    Route(
        "/backlog/containers/{container_id}/edit",
        backlog_container_edit,
        methods=["POST"],
    ),
    Route("/backlog/containers/{container_id}/graph", backlog_graph, methods=["GET"]),
    Route(
        "/backlog/containers/{container_id}/auto-continue",
        backlog_auto_continue,
        methods=["POST"],
    ),
    Route(
        "/backlog/containers/{container_id}/auto-merge",
        backlog_auto_merge,
        methods=["POST"],
    ),
]
"""Routes of one repository, relative to ``/api/repos/{repo_id}``."""
