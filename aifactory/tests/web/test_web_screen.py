"""Screen API tests use synthetic pixels; no real display or live services."""

from __future__ import annotations

import asyncio
import struct
import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import mss
import pytest
from PIL import Image
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.testclient import TestClient
from starlette.types import Message

from aifactory.codeprint import CodeWatch
from aifactory.skill import envelope_problems
from aifactory.web import screen, screen_service
from aifactory.web import screen_capture as capture
from aifactory.web.app import create_multi_app
from aifactory.web.server import allowed_hosts


@pytest.fixture(autouse=True)
def no_live_session_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screen_service, "sessions", lambda: [])


def _client(tmp_path: Path, host: str, peer: str) -> TestClient:
    app = create_multi_app(
        home=tmp_path / "home",
        allowed_hosts=allowed_hosts(host),
        code_watch=CodeWatch(tmp_path / "code"),
        background_checks=False,
    )
    return TestClient(app, base_url="http://127.0.0.1", client=(peer, 1234))


@pytest.mark.parametrize(
    "host,peer,enabled",
    [
        ("127.0.0.1", "127.0.0.1", True),
        ("0.0.0.0", "127.0.0.1", False),
        ("127.0.0.1", "192.0.2.1", False),
    ],
)
def test_availability_and_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    peer: str,
    enabled: bool,
) -> None:
    monkeypatch.setenv("GIRO_SCREEN_BACKEND", "fake")
    with _client(tmp_path, host, peer) as client:
        assert client.get("/api/screen").json()["data"] == {
            "enabled": enabled,
            "supported": True,
            "reason": None,
            "width": 640,
            "height": 360,
            "sessions": [],
        }
        if not enabled:
            response = client.get("/api/screen/stream")
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "screen_disabled"
            assert envelope_problems(response.json()) == []


@pytest.mark.parametrize("wayland,reason", [(False, "no_display"), (True, "wayland_unsupported")])
def test_unsupported_linux(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wayland: bool,
    reason: str,
) -> None:
    monkeypatch.delenv("GIRO_SCREEN_BACKEND", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    if wayland:
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(capture, "sys", SimpleNamespace(platform="linux"))
    with _client(tmp_path, "127.0.0.1", "127.0.0.1") as client:
        data = client.get("/api/screen").json()["data"]
        assert data == {
            "enabled": True,
            "supported": False,
            "reason": reason,
            "width": None,
            "height": None,
            "sessions": [],
        }
        response = client.get("/api/screen/stream")
        assert response.status_code == 501
        assert response.json()["error"]["code"] == "screen_unsupported"
        assert envelope_problems(response.json()) == []
        assert response.json()["data"]["reason"] == reason
        assert cast(Starlette, client.app).state.screen_session is None


@pytest.mark.parametrize(
    "query",
    [
        "fps=0",
        "fps=31",
        "fps=1.5",
        "fps=x",
        "max_width=319",
        "max_width=3841",
        "quality=19",
        "quality=96",
        "quality=",
        "max_width=",
    ],
)
def test_invalid_parameters(tmp_path: Path, query: str) -> None:
    with _client(tmp_path, "127.0.0.1", "127.0.0.1") as client:
        response = client.get("/api/screen/stream?" + query)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "usage_error"
        assert cast(Starlette, client.app).state.screen_session is None


def _request(app: Starlette, query: bytes = b"") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/screen/stream",
            "query_string": query,
            "headers": [],
            "app": app,
            "client": ("127.0.0.1", 1234),
            "http_version": "1.1",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
        }
    )


def test_stream_frames_exclusion_disconnect_and_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIRO_SCREEN_BACKEND", "fake")

    async def scenario() -> None:
        app = Starlette()
        app.state.terminal_enabled = True
        app.state.screen_session = None
        request = _request(app, b"fps=30&max_width=320&quality=95")
        response = await screen.screen_stream(request)
        assert isinstance(response, screen.ScreenResponse)
        busy = await screen.screen_stream(request)
        assert busy.status_code == 409
        frames: list[bytes] = []
        disconnect = asyncio.Event()

        async def send(message: Message) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                frames.append(message["body"])
                if len(frames) == 2:
                    disconnect.set()

        async def receive() -> Message:
            await disconnect.wait()
            return {"type": "http.disconnect"}

        await asyncio.wait_for(response(request.scope, receive, send), 5)
        assert app.state.screen_session is None
        assert response.session.task.done()
        assert len(frames) == 2
        assert frames[0] != frames[1]
        for frame in frames:
            assert struct.unpack("!I", frame[:4])[0] == len(frame) - 4
            with Image.open(BytesIO(frame[4:])) as image:
                image.load()
                assert image.format == "JPEG"
                assert image.size == (320, 180)
        again = await screen.screen_stream(_request(app))
        assert isinstance(again, screen.ScreenResponse)
        assert again.session.parameters == (15, 1280, 60)
        await again.session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("metadata", [{}, {"is_primary": True}])
def test_mss_thread_affinity_geometry_unchanged_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    metadata: dict[str, bool],
) -> None:
    threads: list[int] = []
    closed = threading.Event()
    reads = 0

    class Device:
        monitors = [
            {"left": 0, "top": 0, "width": 800, "height": 400},
            {"left": -100, "top": 20, "width": 640, "height": 360, **metadata},
        ]

        def __init__(self) -> None:
            threads.append(threading.get_ident())

        def grab(self, monitor: dict[str, int]) -> SimpleNamespace:
            nonlocal reads
            assert monitor == self.monitors[1]
            threads.append(threading.get_ident())
            reads += 1
            return SimpleNamespace(bgra=bytes([20, 40, 80, 255]) * (640 * 360))

        def close(self) -> None:
            threads.append(threading.get_ident())
            closed.set()

    monkeypatch.delenv("GIRO_SCREEN_BACKEND", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(mss, "mss", Device)
    monkeypatch.setattr(capture, "open_capture", capture.MSSCapture)

    assert capture.probe() == capture.Geometry(-100, 20, 640, 360)
    assert closed.is_set()
    threads.clear()
    closed.clear()

    async def scenario() -> None:
        session = screen.ScreenSession(30, 320, 60)
        try:
            assert await session.ready == capture.Geometry(-100, 20, 640, 360)
            frame = await asyncio.wait_for(session.frames.get(), 2)
            assert frame is not None
            with Image.open(BytesIO(frame[4:])) as image:
                assert image.getpixel((100, 100)) == pytest.approx((80, 40, 20), abs=3)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(session.frames.get(), 0.15)
            assert reads > 1
        finally:
            await session.close()
        assert closed.is_set()
        assert len(set(threads)) == 1
        assert threads[0] != threading.get_ident()

    asyncio.run(scenario())


def test_macos_permission_failure_closes_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []

    class Denied(capture.FakeCapture):
        def read(self) -> bytes:
            raise PermissionError("denied")

        def close(self) -> None:
            closed.append(True)
            super().close()

    monkeypatch.setattr(capture, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(capture, "open_capture", Denied)
    with _client(tmp_path, "127.0.0.1", "127.0.0.1") as client:
        assert client.get("/api/screen").json()["data"]["reason"] == "permission_denied"
        response = client.get("/api/screen/stream")
        assert response.status_code == 501
        assert "System Settings" in response.json()["error"]["message"]
    assert len(closed) == 2


def test_shutdown_closes_active_stream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIRO_SCREEN_BACKEND", "fake")
    with _client(tmp_path, "127.0.0.1", "127.0.0.1") as client:
        assert client.portal is not None
        response: Response = client.portal.call(
            screen.screen_stream,
            _request(cast(Starlette, client.app)),
        )
        assert isinstance(response, screen.ScreenResponse)
        session = response.session
    assert session.stop.is_set()
    assert session.task.done()
    assert cast(Starlette, client.app).state.screen_session is None


@pytest.mark.parametrize("value", ["0", "-1", "abc", "4294967295", "1.5"])
def test_invalid_session_selector(
    tmp_path: Path, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIRO_SCREEN_BACKEND", "fake")
    with _client(tmp_path, "127.0.0.1", "127.0.0.1") as client:
        for path in ("/api/screen", "/api/screen/stream"):
            assert client.get(path + "?session=" + value).status_code == 400
        assert (
            client.post("/api/screen/input?session=" + value, json={"events": []}).status_code
            == 400
        )


def test_switching_session_closes_previous_input_and_routes_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aifactory.web.screen_input import FakeBackend

    opened: list[int | None] = []

    def open_capture(target: int | None = None) -> capture.FakeCapture:
        opened.append(target)
        return capture.FakeCapture()

    monkeypatch.setattr(capture, "open_capture", open_capture)

    async def scenario() -> None:
        app = Starlette()
        app.state.terminal_enabled = True
        app.state.screen_session = None
        first = await screen.screen_stream(_request(app, b"session=1"))
        assert isinstance(first, screen.ScreenResponse)
        state = first.session.input_state
        assert state is not None
        backend = FakeBackend()
        state.backend = backend
        state.apply([{"type": "key", "code": "ShiftLeft", "down": True}])
        second = await screen.screen_stream(_request(app, b"session=2"))
        assert isinstance(second, screen.ScreenResponse)
        try:
            assert state.closed
            assert first.session.stop.is_set()
            assert backend.events[-2:] == [("key", "ShiftLeft", False), ("release_all",)]
            assert second.session.target_session == 2
            assert opened == [1, 2]
            with pytest.raises(capture.Unsupported):
                state.apply([{"type": "move", "x": 0, "y": 0}])
        finally:
            await second.session.close()

    asyncio.run(scenario())
