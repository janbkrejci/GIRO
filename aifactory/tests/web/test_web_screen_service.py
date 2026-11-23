"""Service protocol and fallback tests; no installed service, desktop, or live input."""

from __future__ import annotations

import ctypes
import json
import struct
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest

from aifactory.web import screen_capture as capture
from aifactory.web import screen_input as inputs
from aifactory.web import screen_input_windows, screen_pipe, screen_service


def packet(width: int = 2, height: int = 1) -> bytes:
    return b"GDS1" + struct.pack("!III", width, height, 0) + b"\x01\x02\x03\xff" * width * height


@pytest.fixture(autouse=True)
def isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screen_pipe, "exchange", Mock(side_effect=FileNotFoundError))
    monkeypatch.setattr(screen_pipe, "protected_desktop", lambda: False)
    monkeypatch.setattr(screen_service, "MSSCapture", capture.FakeCapture)
    monkeypatch.setattr(screen_input_windows, "InputBackend", lambda geometry: inputs.FakeBackend())


def test_compatible_service_and_binary_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def exchange(body: bytes) -> bytes:
        calls.append(json.loads(body))
        return packet() if calls[-1]["operation"] == "capture" else b'{"version":1,"ok":true}'

    monkeypatch.setattr(screen_pipe, "exchange", exchange)
    device = screen_service.WindowsCapture()
    assert device.geometry == capture.Geometry(0, 0, 2, 1)
    assert device.read() == packet()[16:]
    assert device.local is None
    backend = screen_service.WindowsInput(device.geometry)
    backend.move(0.5, 0.25)
    backend.key("ControlRight", True)
    backend.button("left", True)
    backend.wheel(1, -1)
    backend.release_all()
    assert {"version": 1, "operation": "key", "scan": 29, "extended": True, "down": True} in calls
    assert calls[-1]["operation"] == "release"
    device.close()


@pytest.mark.parametrize(
    "reply",
    [b"{}", b'{"version":2,"ok":true}', b'{"version":1,"ok":false}', b"[]", b"bad", b"\xff"],
)
def test_incompatible_service_rejected(monkeypatch: pytest.MonkeyPatch, reply: bytes) -> None:
    monkeypatch.setattr(screen_pipe, "exchange", lambda _: reply)
    with pytest.raises(OSError):
        screen_service.request("hello")


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"GDS1",
        packet()[:-1],
        packet() + b"x",
        b"GDS1" + struct.pack("!III", 0, 1, 0),
        b"GDS1" + struct.pack("!III", 2, 1, 1) + bytes(8),
    ],
)
def test_malformed_frame_rejected(raw: bytes) -> None:
    with pytest.raises(OSError):
        screen_service.frame(raw)


def test_missing_service_preserves_local_capture_and_input() -> None:
    device = screen_service.WindowsCapture()
    assert device.geometry == capture.FakeCapture.geometry
    assert len(device.read()) == 640 * 360 * 4
    backend = screen_service.WindowsInput(device.geometry)
    backend.key("KeyA", True)
    backend.key("KeyA", False)
    backend.move(0, 1)
    assert backend.local.events == [("key", "KeyA", True), ("key", "KeyA", False), ("move", 0, 1)]  # type: ignore[attr-defined]
    device.close()


def test_lock_without_service_blocks_capture_and_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screen_pipe, "protected_desktop", lambda: True)
    local = Mock()
    monkeypatch.setattr(screen_service, "MSSCapture", local)
    with pytest.raises(capture.Unsupported, match="desktop_service_required"):
        screen_service.WindowsCapture()
    local.assert_not_called()
    backend = screen_service.WindowsInput(capture.Geometry(0, 0, 2, 1))
    with pytest.raises(capture.Unsupported, match="desktop_service_required"):
        backend.key("KeyA", True)
    assert backend.local.events == []  # type: ignore[attr-defined]


def test_service_start_stop_and_resolution_change(monkeypatch: pytest.MonkeyPatch) -> None:
    device = screen_service.WindowsCapture()
    device.read()
    monkeypatch.setattr(screen_pipe, "exchange", lambda _: packet(3, 2))
    assert device.read() == packet(3, 2)[16:]
    assert device.geometry == capture.Geometry(0, 0, 3, 2)
    monkeypatch.setattr(screen_pipe, "exchange", Mock(side_effect=OSError))
    assert len(device.read()) == 640 * 360 * 4
    monkeypatch.setattr(screen_pipe, "protected_desktop", lambda: True)
    with pytest.raises(capture.Unsupported):
        device.read()
    monkeypatch.setattr(screen_pipe, "exchange", lambda _: packet())
    assert device.read() == packet()[16:]
    device.close()


def test_transient_lock_transition_keeps_service_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screen_pipe, "exchange", Mock(side_effect=[packet(), OSError(), packet()]))
    monkeypatch.setattr(screen_pipe, "protected_desktop", lambda: True)
    device = screen_service.WindowsCapture()
    device.read()
    assert device.read() == packet()[16:]
    assert device.local is None
    device.close()


def test_input_timeout_never_replayed_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    def exchange(body: bytes) -> bytes:
        if json.loads(body)["operation"] == "hello":
            return b'{"version":1,"ok":true}'
        raise TimeoutError

    monkeypatch.setattr(screen_pipe, "exchange", exchange)
    backend = screen_service.WindowsInput(capture.Geometry(0, 0, 2, 1))
    with pytest.raises(TimeoutError):
        backend.key("KeyA", True)
    assert backend.local.events == []  # type: ignore[attr-defined]
    backend.release_all()


def test_key_up_uses_original_backend_after_service_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = screen_service.WindowsInput(capture.Geometry(0, 0, 2, 1))
    backend.key("ShiftLeft", True)
    exchange = Mock(return_value=b'{"version":1,"ok":true}')
    monkeypatch.setattr(screen_pipe, "exchange", exchange)
    backend.key("ShiftLeft", True)  # Auto-repeat stays on its original backend too.
    backend.key("ShiftLeft", False)
    exchange.assert_not_called()
    assert backend.local.events[-1] == ("key", "ShiftLeft", False)  # type: ignore[attr-defined]


def test_service_loss_does_not_block_disconnect_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screen_pipe, "exchange", lambda _: b'{"version":1,"ok":true}')
    backend = screen_service.WindowsInput(capture.Geometry(0, 0, 2, 1))
    state = inputs.InputState()
    state.backend = backend
    state.apply(
        [
            {"type": "key", "code": "ShiftLeft", "down": True},
            {"type": "button", "button": "left", "down": True, "x": 0, "y": 0},
        ]
    )
    monkeypatch.setattr(screen_pipe, "exchange", Mock(side_effect=TimeoutError))
    state.release_all()
    assert not state.keys and not state.buttons
    assert not backend.keys and not backend.buttons


def test_capture_failure_closes_local_device(monkeypatch: pytest.MonkeyPatch) -> None:
    local = Mock(geometry=capture.Geometry(0, 0, 1, 1))
    local.read.side_effect = OSError
    monkeypatch.setattr(screen_service, "MSSCapture", lambda: local)
    with pytest.raises(OSError):
        screen_service.WindowsCapture()
    local.close.assert_called_once()


def test_windows_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIRO_SCREEN_BACKEND", raising=False)
    monkeypatch.setattr(capture, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(inputs, "sys", SimpleNamespace(platform="win32"))
    device = capture.open_capture()
    assert isinstance(device, screen_service.WindowsCapture)
    assert isinstance(inputs.open_backend(), screen_service.WindowsInput)
    device.close()


def test_overlapped_timeout_cancels_before_freeing_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    pipe = object.__new__(screen_pipe.Pipe)
    calls: list[str] = []
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 997, raising=False)
    for name, value in {
        "event": lambda *args: 123,
        "read": lambda *args: False,
        "wait": lambda *args: 258,
        "cancel": lambda *args: calls.append("cancel"),
        "result": lambda *args: calls.append("completed"),
        "close": lambda *args: calls.append("close"),
    }.items():
        monkeypatch.setattr(pipe, name, value, raising=False)
    with pytest.raises(TimeoutError):
        pipe._transfer(1, ctypes.create_string_buffer(4), 4, 0, write=False)
    assert calls == ["cancel", "completed", "close"]


@pytest.mark.parametrize("length", [0, screen_pipe.MAX_REPLY + 1])
def test_pipe_rejects_unbounded_reply(monkeypatch: pytest.MonkeyPatch, length: int) -> None:
    pipe = _pipe(monkeypatch)
    reads = Mock(return_value=struct.pack("!I", length))
    monkeypatch.setattr(pipe, "_read_exactly", reads)
    with pytest.raises(OSError, match="response size"):
        pipe.exchange(b"{}")
    reads.assert_called_once()


def _pipe(monkeypatch: pytest.MonkeyPatch) -> screen_pipe.Pipe:
    pipe = object.__new__(screen_pipe.Pipe)

    def session(pid: int, value: Any) -> bool:
        ctypes.cast(value, ctypes.POINTER(ctypes.c_uint32))[0] = 1
        return True

    monkeypatch.setattr(
        pipe,
        "kernel",
        SimpleNamespace(
            GetCurrentProcessId=lambda: 1, ProcessIdToSessionId=session, WaitNamedPipeW=Mock()
        ),
        raising=False,
    )
    monkeypatch.setattr(screen_pipe, "_function", lambda dll, name, *args: getattr(dll, name))
    monkeypatch.setattr(pipe, "create", lambda *args: 123, raising=False)
    monkeypatch.setattr(pipe, "close", Mock(), raising=False)
    monkeypatch.setattr(pipe, "_trusted", Mock())
    monkeypatch.setattr(pipe, "_transfer", lambda handle, buffer, size, deadline, **kwargs: size)
    return pipe


def test_untrusted_server_never_receives_request(monkeypatch: pytest.MonkeyPatch) -> None:
    pipe = _pipe(monkeypatch)
    monkeypatch.setattr(pipe, "_trusted", Mock(side_effect=OSError("untrusted")))
    write = Mock()
    monkeypatch.setattr(pipe, "_transfer", write)
    with pytest.raises(OSError, match="untrusted"):
        pipe.exchange(b"{}")
    write.assert_not_called()


def test_partial_reads_are_reassembled(monkeypatch: pytest.MonkeyPatch) -> None:
    pipe = _pipe(monkeypatch)
    chunks = iter([b"ab", b"c", b"def"])

    def transfer(handle: int, buffer: Any, size: int, deadline: float, **kwargs: Any) -> int:
        chunk = next(chunks)
        ctypes.memmove(buffer, chunk, len(chunk))
        return len(chunk)

    monkeypatch.setattr(pipe, "_transfer", transfer)
    assert pipe._read_exactly(1, 6, 0) == b"abcdef"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows pipe security ABI")
def test_native_pipe_rejects_user_owned_server(monkeypatch: pytest.MonkeyPatch) -> None:
    # A real local pipe, but no service, screenshot, input, or elevated process.
    pipe = screen_pipe.Pipe()
    create_server = screen_pipe._function(
        pipe.kernel,
        "CreateNamedPipeW",
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    )
    name = rf"\\.\pipe\giro-test-{uuid4()}"
    server = create_server(name, 3, 0, 1, 4096, 4096, 0, None)
    assert server != ctypes.c_void_p(-1).value
    original_create = pipe.create
    monkeypatch.setattr(pipe, "create", lambda ignored_name, *args: original_create(name, *args))
    send = Mock()
    monkeypatch.setattr(pipe, "_transfer", send)
    try:
        with pytest.raises(OSError, match="Untrusted desktop service"):
            pipe.exchange(b'{"version":1,"operation":"hello"}')
        send.assert_not_called()
    finally:
        pipe.close(server)
