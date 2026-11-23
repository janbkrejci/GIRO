"""Transient screen input, guards, disconnect cleanup and mocked Windows ABI."""

from __future__ import annotations

import asyncio
import ctypes
import importlib
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient
from starlette.types import Message
from test_web_screen import _client, _request

from aifactory.web import screen
from aifactory.web import screen_input as inputs
from aifactory.web.screen_capture import Geometry, Unsupported
from aifactory.web.screen_input_windows import InputBackend


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("GIRO_SCREEN_BACKEND", "fake")
    with _client(tmp_path, "127.0.0.1", "127.0.0.1") as api:
        yield api


def _backend(client: TestClient) -> inputs.FakeBackend:
    return cast(inputs.FakeBackend, cast(Starlette, client.app).state.screen_input.backend)


def test_batch_order_unknown_key_and_release(client: TestClient) -> None:
    events = [
        {"type": "move", "x": 0, "y": 1},
        {"type": "key", "code": "KeyA", "down": True},
        {"type": "key", "code": "Unknown", "down": True},
        {"type": "button", "button": "left", "down": True, "x": 0.5, "y": 0.5},
        {"type": "wheel", "dx": -1, "dy": 2},
        {"type": "release_all"},
    ]
    response = client.post("/api/screen/input", json={"events": events})
    assert response.status_code == 200
    assert response.json()["data"] == {"applied": 5, "ignored": ["Unknown"]}
    assert _backend(client).events == [
        ("move", 0, 1),
        ("key", "KeyA", True),
        ("move", 0.5, 0.5),
        ("button", "left", True),
        ("wheel", -1, 2),
        ("key", "KeyA", False),
        ("button", "left", False),
        ("release_all",),
    ]
    client.post("/api/screen/input", json={"events": [{"type": "release_all"}]})
    assert _backend(client).events[-1] == ("release_all",)
    assert _backend(client).events.count(("key", "KeyA", False)) == 1


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"events": {}},
        {"events": [], "extra": 1},
        {"events": [{"type": "release_all"}] * 201},
        {"events": [{"type": "unknown"}]},
        {"events": [None]},
        {"events": [{"type": "move", "x": -0.01, "y": 0}]},
        {"events": [{"type": "move", "x": 0, "y": 1.01}]},
        {"events": [{"type": "move", "x": True, "y": 0}]},
        {"events": [{"type": "move", "x": "0", "y": 0}]},
        {"events": [{"type": "button", "button": "left", "down": 1, "x": 0, "y": 0}]},
        {"events": [{"type": "button", "button": "other", "down": True, "x": 0, "y": 0}]},
        {"events": [{"type": "button", "button": "left", "down": True}]},
        {"events": [{"type": "key", "code": [], "down": True}]},
        {"events": [{"type": "key", "code": "KeyA", "down": "false"}]},
        {"events": [{"type": "wheel", "dx": 2**32, "dy": 0}]},
    ],
)
def test_invalid_batches_do_not_open_backend(client: TestClient, body: Any) -> None:
    response = client.post("/api/screen/input", json=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "usage_error"
    assert not hasattr(cast(Starlette, client.app).state, "screen_input")


def test_validation_is_atomic_and_accepts_maximum(client: TestClient) -> None:
    good = {"type": "key", "code": "KeyA", "down": True}
    response = client.post("/api/screen/input", json={"events": [good, {"type": "bad"}]})
    assert response.status_code == 400
    assert not hasattr(cast(Starlette, client.app).state, "screen_input")
    response = client.post("/api/screen/input", json={"events": [good] * 200})
    assert response.status_code == 200
    assert response.json()["data"]["applied"] == 200


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b'{"events":[{"type":"move","x":NaN,"y":0}]}',
        b'{"events":[{"type":"wheel","dx":Infinity,"dy":0}]}',
        b'{"events":[{"type":"move","x":' + b"9" * 400 + b',"y":0}]}',
    ],
)
def test_invalid_json_numbers(client: TestClient, raw: bytes) -> None:
    response = client.post(
        "/api/screen/input", content=raw, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "host,peer",
    [
        ("0.0.0.0", "127.0.0.1"),
        ("127.0.0.1", "192.0.2.1"),
    ],
)
def test_loopback_required(tmp_path: Path, host: str, peer: str) -> None:
    with _client(tmp_path, host, peer) as api:
        assert api.post("/api/screen/input", json={"events": []}).status_code == 403
        assert not hasattr(cast(Starlette, api.app).state, "screen_input")


@pytest.mark.parametrize(
    "headers,status",
    [
        ({"Origin": "http://evil.example"}, 403),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({"Content-Type": "text/plain"}, 415),
    ],
)
def test_write_guard(client: TestClient, headers: dict[str, str], status: int) -> None:
    response = client.post(
        "/api/screen/input",
        content=b'{"events":[]}',
        headers={
            "Content-Type": "application/json",
            **headers,
        },
    )
    assert response.status_code == status
    assert not hasattr(cast(Starlette, client.app).state, "screen_input")


@pytest.mark.parametrize(
    "platform,module",
    [
        ("darwin", "macos"),
        ("linux", "x11"),
    ],
)
def test_missing_platform_module(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    module: str,
) -> None:
    monkeypatch.delenv("GIRO_SCREEN_BACKEND")
    monkeypatch.setattr(inputs, "sys", SimpleNamespace(platform=platform))

    def missing(name: str) -> None:
        assert name == f"aifactory.web.screen_input_{module}"
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", missing)
    response = client.post("/api/screen/input", json={"events": []})
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "screen_unsupported"
    assert response.json()["data"]["reason"] == "input_unsupported"


def test_unknown_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIRO_SCREEN_BACKEND", raising=False)
    monkeypatch.setattr(inputs, "sys", SimpleNamespace(platform="unknown"))
    with pytest.raises(Unsupported):
        inputs.open_backend()


def test_stream_disconnect_releases_held_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIRO_SCREEN_BACKEND", "fake")

    async def scenario() -> None:
        app = Starlette()
        app.state.terminal_enabled = True
        app.state.screen_session = None
        request = _request(app)
        response = await screen.screen_stream(request)
        assert isinstance(response, screen.ScreenResponse)
        state = app.state.screen_input
        state.apply(
            inputs.validate(
                {
                    "events": [
                        {"type": "key", "code": "ControlRight", "down": True},
                        {"type": "button", "button": "right", "down": True, "x": 1, "y": 1},
                    ]
                }
            )
        )
        disconnect = asyncio.Event()

        async def send(message: Message) -> None:
            if message["type"] == "http.response.body":
                disconnect.set()

        async def receive() -> Message:
            await disconnect.wait()
            return {"type": "http.disconnect"}

        await asyncio.wait_for(response(request.scope, receive, send), 5)
        assert state.backend.events[-3:] == [
            ("key", "ControlRight", False),
            ("button", "right", False),
            ("release_all",),
        ]
        assert not state.keys and not state.buttons
        assert app.state.screen_session is None

    asyncio.run(scenario())


def test_shutdown_releases_held_input(client: TestClient) -> None:
    assert client.portal is not None
    response = client.portal.call(screen.screen_stream, _request(cast(Starlette, client.app)))
    assert isinstance(response, screen.ScreenResponse)
    client.post(
        "/api/screen/input",
        json={
            "events": [
                {"type": "key", "code": "ShiftLeft", "down": True},
            ]
        },
    )
    client.portal.call(response.session.close)
    assert _backend(client).events[-2:] == [("key", "ShiftLeft", False), ("release_all",)]


def _windows(
    geometry: Geometry, metrics: tuple[int, int, int, int]
) -> tuple[InputBackend, list[Any]]:
    sent: list[Any] = []
    user32 = SimpleNamespace(
        SendInput=Mock(),
        GetSystemMetrics=Mock(side_effect=lambda i: metrics[i - 76]),
    )
    backend = InputBackend(geometry, user32=user32)

    def send(count: int, pointer: Any, size: int) -> int:
        assert count == 1
        assert size == ctypes.sizeof(backend.input)
        assert size == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        value = ctypes.cast(pointer, ctypes.POINTER(backend.input)).contents
        sent.append(backend.input.from_buffer_copy(value))
        return 1

    user32.SendInput.side_effect = send
    return backend, sent


@pytest.mark.parametrize(
    "geometry,metrics,expected",
    [
        (Geometry(0, 0, 1920, 1080), (0, 0, 1920, 1080), (32768, 32768)),
        (Geometry(0, 0, 1920, 1080), (-1920, -1080, 3840, 2160), (49156, 49159)),
        (Geometry(-100, 20, 640, 360), (-100, 20, 640, 360), (32768, 32768)),
    ],
)
def test_windows_absolute_coordinates(
    geometry: Geometry,
    metrics: tuple[int, int, int, int],
    expected: tuple[int, int],
) -> None:
    backend, sent = _windows(geometry, metrics)
    backend.move(0.5, 0.5)
    assert (sent[0].mi.dx, sent[0].mi.dy) == expected
    assert sent[0].mi.dwFlags == 0xC001
    assert sent[0].type == 0


def test_windows_edges_buttons_wheel_and_scancodes() -> None:
    backend, sent = _windows(Geometry(0, 0, 640, 360), (0, 0, 640, 360))
    backend.move(0, 0)
    backend.move(1, 1)
    assert (sent[0].mi.dx, sent[0].mi.dy) == (0, 0)
    assert (sent[1].mi.dx, sent[1].mi.dy) == (65535, 65535)
    for name, flags in (("left", (2, 4)), ("middle", (32, 64)), ("right", (8, 16))):
        backend.button(name, True)
        backend.button(name, False)
        assert [v.mi.dwFlags for v in sent[-2:]] == list(flags)
    backend.wheel(-1, 2)
    assert [(v.mi.dwFlags, v.mi.mouseData) for v in sent[-2:]] == [
        (0x1000, (-120) & 0xFFFFFFFF),
        (0x0800, 240),
    ]
    for code, scan, extended in (
        ("KeyA", 30, False),
        ("Enter", 28, False),
        ("ArrowLeft", 75, True),
        ("ControlRight", 29, True),
        ("NumpadEnter", 28, True),
    ):
        backend.key(code, True)
        backend.key(code, False)
        assert [v.type for v in sent[-2:]] == [1, 1]
        assert [v.ki.wScan for v in sent[-2:]] == [scan, scan]
        assert [v.ki.wVk for v in sent[-2:]] == [0, 0]
        assert [v.ki.dwFlags for v in sent[-2:]] == [8 | int(extended), 10 | int(extended)]


def test_windows_send_failure_retains_key_for_release(client: TestClient) -> None:
    backend, _ = _windows(Geometry(0, 0, 640, 360), (0, 0, 640, 360))
    state = inputs.InputState()
    state.backend = backend
    cast(Starlette, client.app).state.screen_input = state
    send_input = cast(Mock, backend.user32.SendInput)
    send_input.return_value = 0
    send_input.side_effect = None
    response = client.post(
        "/api/screen/input",
        json={
            "events": [
                {"type": "key", "code": "KeyA", "down": True},
            ]
        },
    )
    assert response.status_code == 502
    assert "KeyA" in state.keys
    send_input.return_value = 1
    state.release_all()
    assert not state.keys


def test_windows_module_import_does_not_load_user32(monkeypatch: pytest.MonkeyPatch) -> None:
    load = Mock(side_effect=AssertionError("user32 must load lazily"))
    monkeypatch.setattr(ctypes, "WinDLL", load, raising=False)
    importlib.reload(importlib.import_module("aifactory.web.screen_input_windows"))
    load.assert_not_called()


@pytest.mark.parametrize(
    "platform,module", [("win32", "windows"), ("darwin", "macos"), ("linux", "x11")]
)
def test_platform_selection_uses_capture_geometry(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    module: str,
) -> None:
    monkeypatch.delenv("GIRO_SCREEN_BACKEND", raising=False)
    monkeypatch.setattr(inputs, "sys", SimpleNamespace(platform=platform))
    geometry = Geometry(-100, 20, 640, 360)
    monkeypatch.setattr(inputs, "probe", lambda: geometry)
    backend = inputs.FakeBackend()
    constructor = Mock(return_value=backend)

    def load(name: str) -> SimpleNamespace:
        assert name == f"aifactory.web.screen_input_{module}"
        return SimpleNamespace(InputBackend=constructor)

    monkeypatch.setattr(importlib, "import_module", load)
    assert inputs.open_backend() is backend
    constructor.assert_called_once_with(geometry)
