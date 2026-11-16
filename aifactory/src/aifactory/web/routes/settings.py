"""A repository's settings, its operating settings and its shared config status and pull."""

from __future__ import annotations

from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from aifactory import gitops
from aifactory.config import ConfigError
from aifactory.errors import TaskRunError
from aifactory.registry import RepoError
from aifactory.skill.envelope import envelope_fail, envelope_ok
from aifactory.web import backlog, factory, settings
from aifactory.web.routes.common import (
    _ctx,
    _json_body,
    _repo,
    _repo_error,
    _run_error,
    _usage_error,
)
from aifactory.web.routes.factory import _failure


async def repo_operating_settings(request: Request) -> JSONResponse:
    """Immediate machine-local partial updates; never write the repository."""
    from aifactory.repo_settings import RepositorySettings, SettingsError

    try:
        service = await run_in_threadpool(
            RepositorySettings, _repo(request), request.app.state.home
        )
        if request.url.path.endswith("/legacy"):
            if request.method == "POST":
                body = await _json_body(request)
                if set(body) - {"choices", "overwrite"} or not isinstance(
                    body.get("choices", {}), dict
                ):
                    return _usage_error("Supply choices and optional overwrite only.")
                data = await run_in_threadpool(
                    service.import_legacy,
                    body.get("choices", {}),
                    overwrite=body.get("overwrite", False),
                )
            else:
                data = await run_in_threadpool(service.legacy_preview)
        elif request.method == "PATCH":
            body = await _json_body(request)
            if body.get("harness") is not None:
                catalog = await run_in_threadpool(
                    request.app.state.library.harnesses, request.app.state.home
                )
                name = body["harness"]
                if not isinstance(name, str) or not (
                    catalog["available"].get(name)
                    and catalog["settings"]["harnesses"].get(name, {}).get("enabled")
                ):
                    return _usage_error("Harness není na tomto počítači dostupný a zapnutý.")
            data = await run_in_threadpool(service.update_and_show, body)
        else:
            data = await run_in_threadpool(service.show)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except SettingsError as exc:
        return JSONResponse(
            envelope_fail(exc.code, str(exc)),
            status_code=422 if exc.code == "invalid_value" else 503,
        )
    except (ValueError, OSError) as exc:
        return JSONResponse(envelope_fail("harness_catalog_unavailable", str(exc)), status_code=503)
    return JSONResponse(envelope_ok(data))


def _settings_error(exc: settings.SettingsError) -> JSONResponse:
    body = envelope_fail(exc.code, exc.message, issues=exc.issues)
    return JSONResponse(body, status_code=422)


async def settings_get(request: Request) -> JSONResponse:
    """Shared and local settings for the Settings form."""
    repo: Path = _repo(request)
    try:
        data, warnings = await run_in_threadpool(settings.settings_view, repo)
    except (ConfigError, TaskRunError) as exc:
        return _run_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def settings_save(request: Request) -> JSONResponse:
    """Validate and write ``.factory/config.yaml`` / ``.factory/local.yaml`` (all or nothing)."""
    repo: Path = _repo(request)
    try:
        body = await _json_body(request)
        data, warnings = await run_in_threadpool(settings.save_settings, repo, body)
    except (backlog.UsageError, settings.UsageError) as exc:
        return _usage_error(exc.message)
    except settings.SettingsError as exc:
        return _settings_error(exc)
    except (ConfigError, TaskRunError) as exc:
        return _run_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def config_status(request: Request) -> JSONResponse:
    """``factory config status``: uncommitted shared config against base (D4)."""
    repo: Path = _repo(request)
    try:
        root = await run_in_threadpool(gitops.main_root, repo)
    except TaskRunError as exc:
        return _run_error(exc)
    data, warnings = await run_in_threadpool(settings.config_status, root)
    if data is None:
        return JSONResponse(envelope_fail("invalid_config", "; ".join(warnings)), status_code=500)
    return JSONResponse(envelope_ok(data, warnings))


async def config_pull_plan(request: Request) -> JSONResponse:
    ctx = _ctx(request)
    try:
        data, warnings = await run_in_threadpool(factory.pull_plan, ctx.root)
    except factory.Failure as exc:
        return _failure(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def config_pull(request: Request) -> JSONResponse:
    """``factory config pull``: fast-forward the local base to the remote base."""
    ctx = _ctx(request)
    try:
        body = await _json_body(request)
        factory._keys(body, ("digest",), "keys")
        digest = body.get("digest")
        if not isinstance(digest, str) or not digest.strip():
            raise backlog.UsageError("digest must be the non-empty digest of the reviewed plan")
        with factory.exclusive(ctx.factory):
            data, warnings = await run_in_threadpool(
                factory.pull, ctx.root, ctx.factory, digest=digest
            )
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except RepoError as exc:
        return _repo_error(exc)
    except factory.Failure as exc:
        return _failure(exc)
    return JSONResponse(envelope_ok(data, warnings))


REPO_ROUTES: list[BaseRoute] = [
    Route("/settings/local", repo_operating_settings, methods=["GET", "PATCH"]),
    Route("/settings/local/legacy", repo_operating_settings, methods=["GET", "POST"]),
    Route("/settings", settings_get, methods=["GET"]),
    Route("/settings", settings_save, methods=["POST"]),
    Route("/config/status", config_status, methods=["GET"]),
    Route("/config/pull", config_pull, methods=["POST"]),
    Route("/config/pull/plan", config_pull_plan, methods=["GET"]),
]
"""Routes of one repository, relative to ``/api/repos/{repo_id}``."""
