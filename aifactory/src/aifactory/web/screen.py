"""Loopback-only availability and a single bounded binary screen stream."""

from __future__ import annotations

import asyncio
import hashlib
import struct
import threading
import time
from collections.abc import AsyncIterator

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from aifactory.skill.envelope import envelope_fail, envelope_ok
from aifactory.web import screen_capture as capture
from aifactory.web.terminal import _enabled


class ScreenSession:
    def __init__(self, fps: int, max_width: int, quality: int) -> None:
        self.parameters = fps, max_width, quality
        self.stop = threading.Event()
        self.frames: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=1)
        self.ready: asyncio.Future[capture.Geometry | capture.Unsupported] = (
            asyncio.get_running_loop().create_future()
        )
        self.task = asyncio.create_task(anyio.to_thread.run_sync(self.work))

    def offer(self, frame: bytes | None) -> None:
        if self.frames.full():
            self.frames.get_nowait()
        self.frames.put_nowait(frame)

    def work(self) -> None:
        device = None
        initialized = False
        try:
            device = capture.open_capture()
            raw = device.read()
            anyio.from_thread.run_sync(self.ready.set_result, device.geometry)
            initialized = True
            previous = b""
            fps, max_width, quality = self.parameters
            while not self.stop.is_set():
                started = time.monotonic()
                digest = hashlib.sha256(raw).digest()
                if digest != previous:
                    data = capture.jpeg(raw, device.geometry, max_width, quality)
                    anyio.from_thread.run_sync(self.offer, struct.pack("!I", len(data)) + data)
                    previous = digest
                if self.stop.wait(max(0, 1 / fps - (time.monotonic() - started))):
                    break
                raw = device.read()
        except Exception as exc:
            if not initialized:
                problem = exc if isinstance(exc, capture.Unsupported) else capture.capture_error()
                anyio.from_thread.run_sync(self.ready.set_result, problem)
        finally:
            try:
                if device is not None:
                    device.close()
            finally:
                anyio.from_thread.run_sync(self.offer, None)

    async def close(self) -> None:
        self.stop.set()
        with anyio.CancelScope(shield=True):
            await self.task

    async def stream(self) -> AsyncIterator[bytes]:
        while (frame := await self.frames.get()) is not None:
            yield frame


class ScreenResponse(StreamingResponse):
    def __init__(self, request: Request, session: ScreenSession) -> None:
        super().__init__(
            session.stream(),
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
        self.request = request
        self.session = session

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.session.close()
            if self.request.app.state.screen_session is self.session:
                self.request.app.state.screen_session = None


async def screen_get(request: Request) -> Response:
    info = await anyio.to_thread.run_sync(capture.probe)
    supported = isinstance(info, capture.Geometry)
    return JSONResponse(
        envelope_ok(
            {
                "enabled": _enabled(request),
                "supported": supported,
                "reason": info.reason if isinstance(info, capture.Unsupported) else None,
                "width": info.width if isinstance(info, capture.Geometry) else None,
                "height": info.height if isinstance(info, capture.Geometry) else None,
            }
        )
    )


async def screen_stream(request: Request) -> Response:
    if not _enabled(request):
        return JSONResponse(
            envelope_fail("screen_disabled", "Screen requires loopback."),
            status_code=403,
        )
    values: list[int] = []
    for name, default, low, high in (
        ("fps", 15, 1, 30),
        ("max_width", 1280, 320, 3840),
        ("quality", 60, 20, 95),
    ):
        try:
            value = int(request.query_params.get(name, str(default)))
            if not low <= value <= high:
                raise ValueError
            values.append(value)
        except ValueError:
            return JSONResponse(
                envelope_fail(
                    "usage_error", f"{name} must be an integer between {low} and {high}."
                ),
                status_code=400,
            )
    if request.app.state.screen_session is not None:
        return JSONResponse(
            envelope_fail("busy", "Screen already has a subscriber."), status_code=409
        )
    session = ScreenSession(*values)
    request.app.state.screen_session = session
    try:
        info = await asyncio.shield(session.ready)
        if isinstance(info, capture.Unsupported):
            await session.close()
            request.app.state.screen_session = None
            return JSONResponse(
                envelope_fail("screen_unsupported", str(info), data={"reason": info.reason}),
                status_code=501,
            )
        return ScreenResponse(request, session)
    except BaseException:
        await session.close()
        request.app.state.screen_session = None
        raise
