"""Server-sent events: one repository's live hub and ``/api/live`` across every repository."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import BaseRoute, Route

from aifactory.registry import RepoError
from aifactory.web.live import QUEUE_SIZE as LIVE_QUEUE_SIZE
from aifactory.web.live import LiveEvent, LiveHub
from aifactory.web.repos import RepoContexts
from aifactory.web.routes.common import _ctx
from aifactory.web.routes.repos import _registry

_PING_S = 15.0


def _sse(kind: str, data: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(data)}\n\n"


async def live(request: Request) -> StreamingResponse:
    """Server-Sent Events: ``hello``, then ``files``/``trace``/``resync`` as they happen."""
    hub: LiveHub = _ctx(request).live

    async def stream() -> AsyncIterator[str]:
        queue = await hub.subscribe()
        try:
            yield "retry: 2000\n\n"
            yield _sse("hello", {"interval": hub.interval})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_PING_S)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if event is None:
                    return
                yield _sse(event.kind, event.data)
        finally:
            hub.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def live_all(request: Request) -> StreamingResponse:
    """Server-Sent Events of every registered repository (the overview's live channel).

    ``hello``, then each repository hub's ``files`` and ``trace`` events with ``repo`` (the
    registry id) added, and ``resync`` when events may have been missed (a full queue, a
    closed hub, a repository added or removed). The registry is checked again with every
    ping; a missing folder has no hub and is left out until it appears.
    """
    contexts: RepoContexts = request.app.state.repos
    registry = _registry(request)

    async def stream() -> AsyncIterator[str]:
        merged: asyncio.Queue[tuple[str, LiveEvent]] = asyncio.Queue(maxsize=LIVE_QUEUE_SIZE)
        subs: dict[str, tuple[LiveHub, asyncio.Queue[LiveEvent | None], asyncio.Task[None]]] = {}

        def put(repo_id: str, event: LiveEvent) -> None:
            try:
                merged.put_nowait((repo_id, event))
            except asyncio.QueueFull:
                while not merged.empty():
                    merged.get_nowait()
                merged.put_nowait((repo_id, LiveEvent("resync")))

        async def forward(repo_id: str, queue: asyncio.Queue[LiveEvent | None]) -> None:
            while True:
                event = await queue.get()
                if event is None:  # the hub closed (repository removed or moved)
                    subs.pop(repo_id, None)
                    put(repo_id, LiveEvent("resync"))
                    return
                put(repo_id, event)

        def drop(repo_id: str) -> None:
            hub, queue, task = subs.pop(repo_id)
            task.cancel()
            hub.unsubscribe(queue)

        async def sync() -> bool:
            """Follow the registry; True when the set of watched repositories changed."""
            state, _warnings = await run_in_threadpool(registry.snapshot)
            ids = {entry.id for entry in state.repos}
            changed = False
            for repo_id in [r for r in subs if r not in ids]:
                drop(repo_id)
                changed = True
            for repo_id in sorted(ids - set(subs)):
                try:
                    ctx = await contexts.resolve(repo_id)
                except RepoError:
                    continue
                queue = await ctx.live.subscribe()
                task = asyncio.create_task(forward(repo_id, queue))
                subs[repo_id] = (ctx.live, queue, task)
                changed = True
            return changed

        try:
            await sync()
            yield "retry: 2000\n\n"
            yield _sse("hello", {"repos": sorted(subs)})
            while True:
                try:
                    repo_id, event = await asyncio.wait_for(merged.get(), timeout=_PING_S)
                except TimeoutError:
                    yield ": ping\n\n"
                    if await sync():
                        yield _sse("resync", {"repos": sorted(subs)})
                    continue
                yield _sse(event.kind, {**event.data, "repo": repo_id})
        finally:
            for repo_id in list(subs):
                drop(repo_id)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


REPO_ROUTES: list[BaseRoute] = [
    Route("/live", live, methods=["GET"]),
]
"""Routes of one repository, relative to ``/api/repos/{repo_id}``."""


ROUTES: list[BaseRoute] = [
    Route("/live", live_all, methods=["GET"]),
]
"""Dashboard-wide routes, relative to ``/api``."""
