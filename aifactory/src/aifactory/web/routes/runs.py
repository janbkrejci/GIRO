"""A repository's runs and chains: lists, detail, tail, stop, pause, retry, publish, archive."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from aifactory.backlog import TaskEditError
from aifactory.config import ConfigError
from aifactory.errors import TaskRunError
from aifactory.review.errors import ReviewError
from aifactory.run import pause as run_pause
from aifactory.run import stop as run_stop
from aifactory.skill.envelope import envelope_ok
from aifactory.web import backlog, chains, runs
from aifactory.web.launcher import Launcher
from aifactory.web.routes.common import (
    _edit_error,
    _int_param,
    _json_body,
    _repo,
    _run_error,
    _usage_error,
)
from aifactory.web.routes.review import _review_error


def list_runs(request: Request) -> JSONResponse:
    repo: Path = _repo(request)
    try:
        data, warnings = runs.list_runs(
            repo,
            state=request.query_params.get("state") or None,
            task=request.query_params.get("task") or None,
            archived=request.query_params.get("archived") in ("1", "true"),
            limit=_int_param(request, "limit", 100),
            offset=_int_param(request, "offset", 0),
        )
    except (TaskRunError, ConfigError) as exc:
        return _run_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


def runs_totals(request: Request) -> JSONResponse:
    repo: Path = _repo(request)
    try:
        data, warnings = runs.run_totals(repo)
    except (TaskRunError, ConfigError) as exc:
        return _run_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


def list_chains(request: Request) -> JSONResponse:
    repo: Path = _repo(request)
    try:
        data, warnings = chains.list_chains(repo)
    except (TaskRunError, ConfigError) as exc:
        return _run_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


def dismiss_chain(request: Request) -> JSONResponse:
    """Erase one ended chain: ``{dismissed: chain_id}``."""
    try:
        data = chains.dismiss_chain(_repo(request), request.path_params["chain_id"])
    except (TaskRunError, ConfigError) as exc:
        return _run_error(exc)
    return JSONResponse(envelope_ok(data))


def run_detail(request: Request) -> JSONResponse:
    repo: Path = _repo(request)
    try:
        data, warnings = runs.run_detail(repo, request.path_params["run_id"])
    except (TaskRunError, ConfigError) as exc:
        return _run_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


def run_phase_prompts(request: Request) -> JSONResponse:
    repo: Path = _repo(request)
    try:
        data, warnings = runs.phase_prompts(
            repo, request.path_params["run_id"], request.path_params["phase_id"]
        )
    except (TaskRunError, ConfigError) as exc:
        return _run_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


def run_events(request: Request) -> JSONResponse:
    repo: Path = _repo(request)
    after = _int_param(request, "after", 0)
    limit = _int_param(request, "limit", runs.DEFAULT_EVENTS_LIMIT)
    try:
        data = runs.run_events(repo, request.path_params["run_id"], after=after, limit=limit)
    except (TaskRunError, ConfigError) as exc:
        return _run_error(exc)
    return JSONResponse(envelope_ok(data))


_MAX_OPEN = 50


def run_tail(request: Request) -> JSONResponse:
    """New events, phases, gates and envelopes of a run since the rowid cursors."""
    repo: Path = _repo(request)
    raw_open = request.query_params.get("open") or ""
    open_phases = [p for p in (x.strip() for x in raw_open.split(",")) if p][:_MAX_OPEN]
    events_after = _int_param(request, "events", 0)
    phases_after = _int_param(request, "phases", 0)
    gates_after = _int_param(request, "gates", 0)
    envelopes_after = _int_param(request, "envelopes", 0)
    limit = _int_param(request, "limit", runs.DEFAULT_EVENTS_LIMIT)
    try:
        data = runs.run_tail(
            repo,
            request.path_params["run_id"],
            events_after=events_after,
            phases_after=phases_after,
            gates_after=gates_after,
            envelopes_after=envelopes_after,
            open_phases=open_phases,
            limit=limit,
        )
    except (TaskRunError, ConfigError) as exc:
        return _run_error(exc)
    return JSONResponse(envelope_ok(data))


def stop_run(request: Request) -> JSONResponse:
    """Stop a running run with ``aifactory.run.stop.stop_run``, as ``factory task stop`` does."""
    repo: Path = _repo(request)
    run_id: str = request.path_params["run_id"]
    try:
        result = run_stop.stop_run(repo, run_id)
        summary = runs.run_summary(repo, run_id)
    except (TaskRunError, ConfigError) as exc:
        return _run_error(exc)
    data = {"run": summary, "signalled": result.signalled, "killed": result.killed}
    return JSONResponse(envelope_ok(data))


def _paused(repo: Path, run_id: str) -> dict[str, Any]:
    run_pause.pause_run(repo, run_id)
    return {"run": runs.run_summary(repo, run_id)}


def _resumed(repo: Path, run_id: str) -> dict[str, Any]:
    run_pause.resume_run(repo, run_id)
    return {"run": runs.run_summary(repo, run_id)}


def pause_run(request: Request) -> JSONResponse:
    """Ask a running run to pause before its next phase, as ``factory task pause`` does."""
    return _run_action(request, _paused)


def resume_run(request: Request) -> JSONResponse:
    """Let a paused run go on with its next phase, as ``factory task resume`` does."""
    return _run_action(request, _resumed)


async def publish_run(request: Request) -> JSONResponse:
    """``factory task publish`` for a succeeded run without a PR: ``{run, pr, pr_error}``."""
    repo: Path = _repo(request)
    try:
        data, warnings = await run_in_threadpool(
            runs.publish_run, repo, request.path_params["run_id"], request.app.state.launcher
        )
    except (ReviewError, TaskRunError, ConfigError) as exc:
        return _review_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


async def retry_run(request: Request) -> JSONResponse:
    """``factory task retry`` of a failed or stopped run as a separate process; HTTP 202."""
    repo: Path = _repo(request)
    launcher: Launcher = request.app.state.launcher
    try:
        body = await _json_body(request)
        data, warnings = await run_in_threadpool(
            runs.start_retry,
            repo,
            request.path_params["run_id"],
            body,
            launcher,
            request.app.state.home,
        )
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (ReviewError, TaskRunError, ConfigError) as exc:
        return _review_error(exc)
    return JSONResponse(envelope_ok(data, warnings), status_code=202)


async def harness_choices(request: Request) -> JSONResponse:
    """The harnesses „Pokračovat s jiným harnessem“ offers: enabled, tested, not exhausted."""
    repo: Path = _repo(request)
    try:
        data = await run_in_threadpool(
            runs.harness_choices, repo, request.path_params["run_id"], request.app.state.home
        )
    except (TaskRunError, ConfigError) as exc:
        return _review_error(exc)
    except ValueError as exc:  # an invalid harnesses.json
        return _usage_error(str(exc))
    return JSONResponse(envelope_ok(data))


async def allow_writes_plan(request: Request) -> JSONResponse:
    """The paths „Povolit tyto cesty a pokračovat“ adds to writes and those it refuses."""
    repo: Path = _repo(request)
    try:
        data = await run_in_threadpool(runs.allow_writes_plan, repo, request.path_params["run_id"])
    except (TaskEditError, TaskRunError, ConfigError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data))


async def allow_writes_run(request: Request) -> JSONResponse:
    """``factory task retry --allow-writes`` after ``writes_violation`` or ``writes_needed``:
    extend writes in base, start the retry; for a ``ci-fix`` run start the approve again
    (``factory task approve --allow-writes``); 202."""
    repo: Path = _repo(request)
    launcher: Launcher = request.app.state.launcher
    try:
        body = await _json_body(request)
        data, warnings = await run_in_threadpool(
            runs.start_allow_writes, repo, request.path_params["run_id"], body, launcher
        )
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except (TaskEditError, TaskRunError, ConfigError) as exc:
        return _edit_error(exc)
    except ReviewError as exc:
        return _review_error(exc)
    return JSONResponse(envelope_ok(data, warnings), status_code=202)


async def nothing_to_do_done(request: Request) -> JSONResponse:
    """„Hotovo“ of a ``nothing_to_do`` run: the task is done in base without changes."""
    repo: Path = _repo(request)
    try:
        data, warnings = await run_in_threadpool(
            runs.finish_nothing_to_do, repo, request.path_params["run_id"]
        )
    except (TaskEditError, TaskRunError, ConfigError) as exc:
        return _edit_error(exc)
    return JSONResponse(envelope_ok(data, warnings))


def _run_action(request: Request, action: Any) -> JSONResponse:
    repo: Path = _repo(request)
    try:
        data = action(repo, request.path_params["run_id"])
    except (TaskRunError, ConfigError) as exc:
        return _run_error(exc)
    return JSONResponse(envelope_ok(data))


def archive_run(request: Request) -> JSONResponse:
    """Archive one finished run: ``{run}``."""
    return _run_action(request, lambda repo, run_id: {"run": runs.archive_run(repo, run_id)})


def unarchive_run(request: Request) -> JSONResponse:
    """Return one archived run to the active ones: ``{run}``."""
    return _run_action(request, lambda repo, run_id: {"run": runs.unarchive_run(repo, run_id)})


def delete_run(request: Request) -> JSONResponse:
    """Erase one archived run and its trace: ``{deleted: [run_id]}``."""
    return _run_action(request, runs.delete_run)


def archive_finished(request: Request) -> JSONResponse:
    """Archive every finished run: ``{archived: [run_id, ...]}``."""
    try:
        data = runs.archive_finished(_repo(request))
    except (TaskRunError, ConfigError) as exc:
        return _run_error(exc)
    return JSONResponse(envelope_ok(data))


def delete_archived(request: Request) -> JSONResponse:
    """Erase every archived run and its trace: ``{deleted: [run_id, ...]}``."""
    try:
        data = runs.delete_archived(_repo(request))
    except (TaskRunError, ConfigError) as exc:
        return _run_error(exc)
    return JSONResponse(envelope_ok(data))


REPO_ROUTES: list[BaseRoute] = [
    Route("/runs", list_runs, methods=["GET"]),
    Route("/runs/totals", runs_totals, methods=["GET"]),
    Route("/chains", list_chains, methods=["GET"]),
    Route("/chains/{chain_id}/dismiss", dismiss_chain, methods=["POST"]),
    Route("/runs/archive-finished", archive_finished, methods=["POST"]),
    Route("/runs/delete-archived", delete_archived, methods=["POST"]),
    Route("/runs/{run_id}", run_detail, methods=["GET"]),
    Route("/runs/{run_id}/events", run_events, methods=["GET"]),
    Route("/runs/{run_id}/tail", run_tail, methods=["GET"]),
    Route(
        "/runs/{run_id}/phases/{phase_id}/prompts",
        run_phase_prompts,
        methods=["GET"],
    ),
    Route("/runs/{run_id}/stop", stop_run, methods=["POST"]),
    Route("/runs/{run_id}/pause", pause_run, methods=["POST"]),
    Route("/runs/{run_id}/resume", resume_run, methods=["POST"]),
    Route("/runs/{run_id}/retry", retry_run, methods=["POST"]),
    Route("/runs/{run_id}/harness-choices", harness_choices, methods=["GET"]),
    Route("/runs/{run_id}/allow-writes", allow_writes_plan, methods=["GET"]),
    Route("/runs/{run_id}/allow-writes", allow_writes_run, methods=["POST"]),
    Route("/runs/{run_id}/done", nothing_to_do_done, methods=["POST"]),
    Route("/runs/{run_id}/publish", publish_run, methods=["POST"]),
    Route("/runs/{run_id}/archive", archive_run, methods=["POST"]),
    Route("/runs/{run_id}/unarchive", unarchive_run, methods=["POST"]),
    Route("/runs/{run_id}/delete", delete_run, methods=["POST"]),
]
"""Routes of one repository, relative to ``/api/repos/{repo_id}``."""
