"""Request helpers of the route modules: the request's repository, JSON bodies, errors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from aifactory.backlog import TaskEditError
from aifactory.config import ConfigError
from aifactory.errors import TaskRunError
from aifactory.registry import RepoError
from aifactory.runstore.base import is_db_busy
from aifactory.skill.envelope import envelope_fail
from aifactory.web import backlog
from aifactory.web.repos import RepoContext

REPO_SCOPE_KEY = "giro.repo"
"""The ASGI scope key under which ``RepoScope`` puts the request's ``RepoContext``."""


def _ctx(request: Request) -> RepoContext:
    ctx: RepoContext = request.scope[REPO_SCOPE_KEY]
    return ctx


def _repo(request: Request) -> Path:
    """The root of the repository the request is for (set by ``RepoScope``)."""
    return _ctx(request).root


_REPO_ERROR_STATUS = {
    "unknown_repo": 404,
    "repo_missing": 404,
    "path_not_found": 404,
    "not_a_directory": 422,
    "not_git": 422,
    "bare_repo": 422,
    "linked_worktree": 422,
    "run_worktree": 422,
    "no_commits": 422,
    "trace_db_shared": 409,
    "library_missing": 409,
    "registry_invalid": 409,
    "invalid_value": 422,
    "outside_home": 403,
    "permission_denied": 403,
    "picker_unavailable": 503,
    "picker_busy": 409,
    "picker_timeout": 504,
    "picker_failed": 502,
    "busy": 409,
    # install and removal (commit on base)
    "run_in_progress": 409,
    "not_on_base": 409,
    "base_behind": 409,
    "base_diverged": 409,
    "dirty_paths": 409,
    "invalid_plan": 422,
    "push_failed": 502,
    "fetch_failed": 502,
    "commit_failed": 500,
    "base_moved": 409,
}


def _repo_error(exc: RepoError) -> JSONResponse:
    body = envelope_fail(exc.code, exc.message, data=exc.data or None)
    return JSONResponse(body, status_code=_REPO_ERROR_STATUS.get(exc.code, 500))


_RUN_ERROR_STATUS = {
    "unknown_run": 404,
    "unknown_phase": 404,
    "run_not_running": 409,
    "run_already_paused": 409,
    "run_not_paused": 409,
    "run_not_retryable": 409,
    "not_writes_violation": 409,
    "nothing_to_allow": 409,
    "writes_in_pr": 409,
    "writes_not_committed": 409,
    "not_nothing_to_do": 409,
    "task_done": 409,
    "unknown_branch": 404,
    "base_diverged": 409,
    "dirty_base": 409,
    "run_in_progress": 409,
    "fetch_failed": 502,
    "pull_failed": 500,
    "run_running": 409,
    "process_identity_unconfirmed": 409,
    "remote_run": 409,
    "run_not_archived": 409,
    "unknown_chain": 404,
    "chain_running": 409,
    "invalid_status": 400,
    "invalid_pagination": 400,
    "invalid_value": 400,
    "trace_db_locked": 503,
    "already_running": 409,
    "task_running": 409,
    "unmet_dependencies": 409,
    "task_not_in_base": 409,
    "no_writes": 422,
    "no_workflow": 422,
    "human_task": 422,
    "unknown_workflow": 422,
    "invalid_workflow": 422,
    "unknown_task": 404,
    "worktree_failed": 500,
    "no_pr": 404,
    "pr_not_open": 409,
    "conflict": 409,
    "checks_head_unconfirmed": 409,
    "dirty_worktree": 409,
    "branch_checked_out": 409,
    "missing_note": 400,
    "merge_failed": 502,
    "unknown_base": 500,
    "invalid_config": 500,
}


DB_BUSY = "trace_db_locked"


DB_BUSY_MESSAGE = (
    "the trace DB is busy with other runs and stayed locked while we waited; try again"
)


def _db_busy() -> JSONResponse:
    """A trace DB that stayed locked through the whole wait: HTTP 503, retry later."""
    return JSONResponse(envelope_fail(DB_BUSY, DB_BUSY_MESSAGE), status_code=503)


def _run_error(exc: TaskRunError | ConfigError) -> JSONResponse:
    if isinstance(exc, ConfigError):
        return JSONResponse(envelope_fail("invalid_config", str(exc)), status_code=500)
    if is_db_busy(exc):
        return _db_busy()
    status = _RUN_ERROR_STATUS.get(exc.code, 500)
    return JSONResponse(envelope_fail(exc.code, exc.message), status_code=status)


def _int_param(request: Request, name: str, default: int) -> int:
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(400, f"{name} must be an integer, got {raw!r}") from None


_EDIT_ERROR_STATUS = {
    "unknown_workflow_advice": 404,
    "unknown_intent": 404,
    "unknown_lesson": 404,
    "project_required": 409,
    "learn_failed": 422,
    "lessons_invalid": 422,
    "agent_unavailable": 409,
    "proposal_failed": 422,
    "proposal_invalid": 422,
    "busy": 409,
    "backlog_invalid": 422,
    "unknown_task": 404,
    "unknown_step": 404,
    "missing_backlog_dir": 404,
    "file_exists": 409,
    "duplicate_id": 409,
    "edit_conflict": 409,
    "write_failed": 500,
    "unknown_container": 404,
    "no_index": 409,
    "invalid_value": 400,
    "not_on_base": 409,
    "invalid_config": 500,
    "commit_failed": 500,
    "push_failed": 502,
}


def _edit_error(exc: TaskEditError | ConfigError | TaskRunError) -> JSONResponse:
    if isinstance(exc, ConfigError):
        issues = [
            {"code": "invalid_config", "message": i.message, "path": i.path, "id": None}
            for i in exc.issues
        ]
        return JSONResponse(
            envelope_fail("invalid_config", str(exc), issues=issues), status_code=500
        )
    if isinstance(exc, TaskRunError):
        return _run_error(exc)
    status = _EDIT_ERROR_STATUS.get(exc.code, 400)
    body = envelope_fail(
        exc.code,
        exc.message,
        path=exc.path,
        id=exc.id,
        issues=[i.to_dict() for i in exc.issues],
    )
    return JSONResponse(body, status_code=status)


def _usage_error(message: str) -> JSONResponse:
    return JSONResponse(envelope_fail("usage_error", message), status_code=400)


async def _json_body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        raise backlog.UsageError("the request body is not valid JSON") from None
    if not isinstance(body, dict):
        raise backlog.UsageError("the request body must be a JSON object")
    return body
