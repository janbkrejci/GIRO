"""The repository registry, the demo, folder browsing, dashboard settings and the overview."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from aifactory.demo import is_demo
from aifactory.registry import Registry, RepoError
from aifactory.skill.envelope import envelope_fail, envelope_ok
from aifactory.web import backlog, library
from aifactory.web.fs import FolderPicker, list_dirs
from aifactory.web.overview import Overview
from aifactory.web.repos import RepoContexts, add_repo, inspect_repo, remove_repo, repo_status
from aifactory.web.routes.common import _json_body, _repo_error, _usage_error


def _registry(request: Request) -> Registry:
    registry: Registry = request.app.state.registry
    return registry


async def repos_list(request: Request) -> JSONResponse:
    """The registered repositories with their status and factory state."""
    registry = _registry(request)

    def build() -> tuple[list[dict[str, Any]], list[str]]:
        state, warnings = registry.snapshot()
        return [
            {
                **repo_status(entry),
                **entry.to_json(),
                "demo": is_demo(entry.path, request.app.state.home),
            }
            for entry in state.repos
        ], warnings

    items, warnings = await run_in_threadpool(build)
    data = {"repos": items, "home": str(request.app.state.home)}
    return JSONResponse(envelope_ok(data, warnings))


def _path_body(body: dict[str, Any]) -> object:
    unknown = sorted(set(body) - {"path"})
    if unknown:
        raise backlog.UsageError(f"unknown keys: {', '.join(unknown)}")
    return body.get("path")


async def repos_add(request: Request) -> JSONResponse:
    """Add a repository: register it and, unless base already has factory, commit
    ``.factory/config.yaml`` and ``.gitignore`` (``web.repos.add_repo``). ``base_changes``
    (``direct`` or ``pr``; ``target: "pr"`` is the same) and ``base_pr_merge`` (``manual``
    or ``auto``) go to ``config.yaml`` and apply to this commit already. HTTP 201 when newly
    registered, 200 when it was there."""
    registry = _registry(request)

    def work(
        raw: object, changes: str | None, merge: str | None
    ) -> tuple[dict[str, Any], bool, dict[str, Any], list[str]]:
        entry, created, install, warnings = add_repo(
            registry,
            raw,
            library.environment(request.app.state.home),
            base_changes=changes,
            base_pr_merge=merge,
        )
        return repo_status(entry), created, install, warnings

    try:
        body = await _json_body(request)
        target = body.pop("target", None)
        changes = body.pop("base_changes", "pr" if target == "pr" else None)
        merge = body.pop("base_pr_merge", None)
        if target not in (None, "base", "pr"):
            raise backlog.UsageError("target must be base or pr")
        if changes not in (None, "direct", "pr"):
            raise backlog.UsageError("base_changes must be direct or pr")
        if merge not in (None, "manual", "auto"):
            raise backlog.UsageError("base_pr_merge must be manual or auto")
        raw = _path_body(body)
        item, created, install, warnings = await run_in_threadpool(work, raw, changes, merge)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except RepoError as exc:
        return _repo_error(exc)
    body = envelope_ok({"repo": item, "created": created, "install": install}, warnings)
    return JSONResponse(body, status_code=201 if created else 200)


async def repos_removal(request: Request) -> JSONResponse:
    """What removing the repository commits."""
    registry = _registry(request)
    repo_id: str = request.path_params["repo_id"]

    def work() -> dict[str, Any]:
        from aifactory.library.store import LibraryStoreError
        from aifactory.library.uninstall import plan_uninstall

        entry = registry.snapshot()[0].by_id(repo_id)
        if entry is None:
            raise RepoError("unknown_repo", f"no registered repository {repo_id!r}")
        try:
            return plan_uninstall(
                Path(entry.path), library.environment(request.app.state.home)
            ).to_json()
        except LibraryStoreError as exc:
            raise RepoError(exc.code, exc.message) from exc

    try:
        data = await run_in_threadpool(work)
    except RepoError as exc:
        return _repo_error(exc)
    return JSONResponse(envelope_ok(data))


async def repos_inspect(request: Request) -> JSONResponse:
    """What adding a folder would do; reads only, writes nothing anywhere."""
    registry = _registry(request)

    def work(raw: object) -> tuple[dict[str, Any], list[str]]:
        state, warnings = registry.snapshot()
        return inspect_repo(raw, state), warnings

    try:
        raw = _path_body(await _json_body(request))
        data, warnings = await run_in_threadpool(work, raw)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    return JSONResponse(envelope_ok(data, warnings))


async def repos_remove(request: Request) -> JSONResponse:
    """Remove a repository from the dashboard: drop the registry entry and close its live
    hub; ``.factory/`` stays in the repository (``uninstall: true`` commits its removal)."""
    registry = _registry(request)
    repo_id: str = request.path_params["repo_id"]
    contexts: RepoContexts = request.app.state.repos
    try:
        payload = await _json_body(request) if await request.body() else {}
        unknown = sorted(set(payload) - {"uninstall"})
        if unknown:
            raise backlog.UsageError(f"unknown keys: {', '.join(unknown)}")
        uninstall = payload.get("uninstall", False)
        if not isinstance(uninstall, bool):
            raise backlog.UsageError("uninstall must be a boolean")
        environ = library.environment(request.app.state.home)
        entry, done = await run_in_threadpool(
            lambda: remove_repo(registry, repo_id, uninstall=uninstall, environ=environ)
        )
        await contexts.drop(repo_id)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except RepoError as exc:
        return _repo_error(exc)
    return JSONResponse(envelope_ok({"removed": entry.to_json(), "uninstall": done}))


_DEMO_ERROR_STATUS = {"invalid_value": 422, "git_identity_missing": 409}


async def demo_create(request: Request) -> JSONResponse:
    """Create the sample repository in the default folder and register it (``factory try``).
    HTTP 201 when the folder was created, 200 when it was there already."""
    from aifactory.demo import create_demo
    from aifactory.library.store import LibraryStoreError

    try:
        body = await _json_body(request) if await request.body() else {}
        if body:
            raise backlog.UsageError("the sample takes no options")
        result = await run_in_threadpool(create_demo, home=request.app.state.home)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except LibraryStoreError as exc:
        return JSONResponse(
            envelope_fail(exc.code, exc.message),
            status_code=_DEMO_ERROR_STATUS.get(exc.code, 500),
        )
    if not result.registered:
        git_repo = (result.path / ".git").exists()
        return JSONResponse(
            envelope_fail(
                "registry_invalid" if git_repo else "not_git",
                "; ".join(result.warnings) or str(result.path),
            ),
            status_code=409 if git_repo else 422,
        )
    return JSONResponse(
        envelope_ok(result.to_json(), result.warnings),
        status_code=201 if result.created else 200,
    )


async def demo_remove(request: Request) -> JSONResponse:
    """Remove the sample: its registry entry and its whole default folder."""
    from aifactory.demo import remove_demo

    contexts: RepoContexts = request.app.state.repos
    try:
        result = await run_in_threadpool(remove_demo, request.app.state.home)
    except RepoError as exc:
        return _repo_error(exc)
    if result.repo_id is not None:
        await contexts.drop(result.repo_id)
    return JSONResponse(envelope_ok(result.to_json()))


async def fs_dirs(request: Request) -> JSONResponse:
    """The visible subdirectories of ``?path=`` under the user's home; reads only."""
    home: Path = request.app.state.user_home
    try:
        data = await run_in_threadpool(list_dirs, request.query_params.get("path"), home)
    except RepoError as exc:
        return _repo_error(exc)
    return JSONResponse(envelope_ok(data))


async def fs_pick_status(request: Request) -> JSONResponse:
    """Whether ``POST /api/fs/pick`` can open a dialog; opens nothing."""
    picker: FolderPicker = request.app.state.picker
    return JSONResponse(envelope_ok({"available": picker.available()}))


async def fs_pick(request: Request) -> JSONResponse:
    """Open the native folder dialog on a worker thread: ``{path}`` or ``{cancelled}``."""
    picker: FolderPicker = request.app.state.picker
    try:
        data = await run_in_threadpool(picker.pick)
    except RepoError as exc:
        return _repo_error(exc)
    return JSONResponse(envelope_ok(data))


def _dashboard_settings(request: Request, port: int) -> dict[str, Any]:
    served: int | None = request.app.state.served_port
    return {
        "port": port,
        "home": str(request.app.state.home),
        "registry": str(_registry(request).path),
        "restart_required": served is not None and port != served,
    }


async def dashboard_settings_get(request: Request) -> JSONResponse:
    """The dashboard port from the registry and whether a restart would change it."""
    state, warnings = await run_in_threadpool(_registry(request).snapshot)
    return JSONResponse(envelope_ok(_dashboard_settings(request, state.port), warnings))


def _port_value(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise RepoError(
            "invalid_value",
            f"port must be an integer from 1 to 65535, got {value!r}",
        )
    return value


async def dashboard_settings_save(request: Request) -> JSONResponse:
    """Store the dashboard port (``{port}``); it applies from the next start."""
    registry = _registry(request)
    try:
        body = await _json_body(request)
        unknown = sorted(set(body) - {"port"})
        if unknown:
            raise backlog.UsageError(f"unknown keys: {', '.join(unknown)}")
        if "port" in body:
            state = await run_in_threadpool(registry.set_port, _port_value(body["port"]))
            warnings: list[str] = []
        else:
            state, warnings = await run_in_threadpool(registry.snapshot)
    except backlog.UsageError as exc:
        return _usage_error(exc.message)
    except RepoError as exc:
        if exc.code == "invalid_value":
            issues = [
                {
                    "code": "invalid_value",
                    "message": exc.message,
                    "message_code": "port_range",
                    "params": {"min": 1, "max": 65535},
                    "path": None,
                    "id": "port",
                }
            ]
            body = envelope_fail(exc.code, exc.message, issues=issues)
            return JSONResponse(body, status_code=422)
        return _repo_error(exc)
    return JSONResponse(envelope_ok(_dashboard_settings(request, state.port), warnings))


async def overview_get(request: Request) -> JSONResponse:
    """What runs, waits for review and failed in every registered repository (reads only)."""
    ov: Overview = request.app.state.overview
    data, warnings = await ov.collect()
    return JSONResponse(envelope_ok(data, warnings))


async def repo_overview_get(request: Request) -> JSONResponse:
    """One repository item of ``GET /api/overview`` (same timeout and ``stale`` rules)."""
    ov: Overview = request.app.state.overview
    repo_id: str = request.path_params["repo_id"]
    state, _warnings = await run_in_threadpool(_registry(request).snapshot)
    entry = state.by_id(repo_id)
    if entry is None:
        return _repo_error(RepoError("unknown_repo", f"no registered repository {repo_id!r}"))
    return JSONResponse(envelope_ok(await ov.item(entry)))


ROUTES: list[BaseRoute] = [
    Route("/repos", repos_list, methods=["GET"]),
    Route("/repos", repos_add, methods=["POST"]),
    Route("/repos/inspect", repos_inspect, methods=["POST"]),
    Route("/repos/{repo_id}", repos_remove, methods=["DELETE"]),
    Route("/demo", demo_create, methods=["POST"]),
    Route("/demo", demo_remove, methods=["DELETE"]),
    Route("/repos/{repo_id}/removal", repos_removal, methods=["GET"]),
    Route("/dashboard/settings", dashboard_settings_get, methods=["GET"]),
    Route("/dashboard/settings", dashboard_settings_save, methods=["POST"]),
    Route("/fs/dirs", fs_dirs, methods=["GET"]),
    Route("/fs/pick", fs_pick_status, methods=["GET"]),
    Route("/fs/pick", fs_pick, methods=["POST"]),
    Route("/overview", overview_get, methods=["GET"]),
    Route("/repos/{repo_id}/overview", repo_overview_get, methods=["GET"]),
]
"""Dashboard-wide routes, relative to ``/api``."""
