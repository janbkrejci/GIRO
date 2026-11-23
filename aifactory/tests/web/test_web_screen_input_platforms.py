"""Platform input ABIs exercised without native libraries or a live desktop."""

from __future__ import annotations

import ctypes
import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, call

import pytest
from test_web_screen import _client

from aifactory.web import screen_input as inputs
from aifactory.web import screen_input_macos as mac
from aifactory.web import screen_input_x11 as x11
from aifactory.web.screen_capture import Geometry, Unsupported

GEOMETRY = Geometry(0, 0, 2880, 1800)


def _mac(trusted: bool = True) -> tuple[Any, Any]:
    api = SimpleNamespace(
        **{
            name: Mock(return_value=1)
            for name in (
                "AXIsProcessTrusted",
                "CGMainDisplayID",
                "CGDisplayBounds",
                "CGEventCreateMouseEvent",
                "CGEventCreateKeyboardEvent",
                "CGEventCreateScrollWheelEvent",
                "CGEventSetFlags",
                "CGEventPost",
            )
        }
    )
    api.AXIsProcessTrusted.return_value = trusted
    api.CGDisplayBounds.return_value = mac.CGRect(mac.CGPoint(10, 20), mac.CGSize(1440, 900))
    return api, SimpleNamespace(CFRelease=Mock())


def _x11(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    monkeypatch.setenv("DISPLAY", ":99")
    return (
        SimpleNamespace(
            XOpenDisplay=Mock(return_value=0x12345678AB),
            XCloseDisplay=Mock(),
            XDefaultScreen=Mock(return_value=0),
            XFlush=Mock(),
            XStringToKeysym=Mock(return_value=0x61),
            XKeysymToKeycode=Mock(return_value=38),
        ),
        SimpleNamespace(
            **{
                name: Mock(return_value=1)
                for name in (
                    "XTestQueryExtension",
                    "XTestFakeMotionEvent",
                    "XTestFakeButtonEvent",
                    "XTestFakeKeyEvent",
                )
            }
        ),
    )


@pytest.mark.parametrize("module", [mac, x11])
def test_import_is_lazy(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    load = Mock(side_effect=AssertionError("libraries must load only at use"))
    monkeypatch.setattr(ctypes, "CDLL", load)
    importlib.reload(module)
    load.assert_not_called()


def test_mac_coordinates_use_display_points() -> None:
    api, core = _mac()
    backend = mac.InputBackend(GEOMETRY, services=api, core=core)
    for x, y, expected in [(0, 0, (10, 20)), (0.5, 0.5, (729.5, 469.5)), (1, 1, (1449, 919))]:
        backend.move(x, y)
        args = api.CGEventCreateMouseEvent.call_args.args
        assert args[:2] == (None, 5)
        assert (args[2].x, args[2].y) == expected
    api.CGDisplayBounds.assert_called_once_with(1)
    assert api.CGDisplayBounds.restype is mac.CGRect
    assert api.CGEventCreateMouseEvent.restype is ctypes.c_void_p
    assert core.CFRelease.call_count == 3


@pytest.mark.parametrize(
    "name,button,press,drag,release",
    [
        ("left", 0, 1, 6, 2),
        ("right", 1, 3, 7, 4),
        ("middle", 2, 25, 27, 26),
    ],
)
def test_mac_drag_and_release(name: str, button: int, press: int, drag: int, release: int) -> None:
    api, core = _mac()
    backend = mac.InputBackend(GEOMETRY, services=api, core=core)
    backend.button(name, True)
    backend.move(1, 1)
    backend.release_all()
    backend.move(0, 0)
    assert [(c.args[1], c.args[3]) for c in api.CGEventCreateMouseEvent.call_args_list] == [
        (press, button),
        (drag, button),
        (release, button),
        (5, 0),
    ]
    assert not backend.buttons


@pytest.mark.parametrize(
    "code,keycode",
    [
        ("KeyA", 0),
        ("Enter", 36),
        ("ArrowLeft", 123),
        ("MetaLeft", 55),
    ],
)
def test_mac_keycodes(code: str, keycode: int) -> None:
    api, core = _mac()
    backend = mac.InputBackend(GEOMETRY, services=api, core=core)
    backend.key(code, True)
    backend.release_all()
    assert api.CGEventCreateKeyboardEvent.call_args_list == [
        call(None, keycode, True),
        call(None, keycode, False),
    ]


def test_mac_command_shortcut_and_modifier_release() -> None:
    api, core = _mac()
    backend = mac.InputBackend(GEOMETRY, services=api, core=core)
    backend.key("MetaLeft", True)
    backend.key("KeyC", True)
    backend.key("KeyC", False)
    backend.key("MetaLeft", False)
    assert [c.args[1] for c in api.CGEventSetFlags.call_args_list] == [1 << 20] * 3 + [0]
    assert api.CGEventCreateKeyboardEvent.call_args_list[1] == call(None, 8, True)
    backend.key("ShiftLeft", True)
    backend.key("ShiftRight", True)
    backend.key("ShiftLeft", False)
    backend.move(0, 0)
    assert api.CGEventSetFlags.call_args.args[1] == 1 << 17
    backend.release_all()
    assert api.CGEventSetFlags.call_args.args[1] == 0


def test_mac_scroll_and_event_failure() -> None:
    api, core = _mac()
    backend = mac.InputBackend(GEOMETRY, services=api, core=core)
    backend.wheel(-2, 3)
    args = api.CGEventCreateScrollWheelEvent.call_args.args
    assert args[:3] == (None, 1, 2)
    assert [v.value for v in args[3:]] == [3, 2]
    api.CGEventCreateKeyboardEvent.return_value = None
    with pytest.raises(OSError):
        backend.key("KeyA", True)
    assert "KeyA" in backend.keys
    assert core.CFRelease.call_count == 1
    api.CGEventCreateKeyboardEvent.return_value = 1
    backend.release_all()
    assert not backend.keys


def test_mac_failed_modifier_release_retains_state_and_frees_event() -> None:
    api, core = _mac()
    backend = mac.InputBackend(GEOMETRY, services=api, core=core)
    backend.key("MetaLeft", True)
    api.CGEventPost.side_effect = OSError("post failed")
    with pytest.raises(OSError):
        backend.key("MetaLeft", False)
    assert "MetaLeft" in backend.keys
    assert core.CFRelease.call_count == 2
    api.CGEventPost.side_effect = None
    backend.release_all()
    assert not backend.keys
    assert api.CGEventSetFlags.call_args.args[1] == 0


def test_mac_permission_denied_reaches_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, core = _mac(False)
    with pytest.raises(Unsupported, match="Privacy & Security > Accessibility") as error:
        mac.InputBackend(GEOMETRY, services=api, core=core)
    assert error.value.reason == "permission_denied"
    api.CGEventPost.assert_not_called()
    with _client(tmp_path, "127.0.0.1", "127.0.0.1") as client:
        monkeypatch.delenv("GIRO_SCREEN_BACKEND", raising=False)
        monkeypatch.setattr(inputs, "sys", SimpleNamespace(platform="darwin"))
        monkeypatch.setattr(inputs, "probe", lambda: GEOMETRY)
        monkeypatch.setattr(ctypes, "CDLL", Mock(side_effect=[api, core]))
        response = client.post("/api/screen/input", json={"events": []})
        assert response.status_code == 501
        assert response.json()["data"]["reason"] == "permission_denied"
        assert "Accessibility" in response.json()["error"]["message"]


def test_x11_coordinates_wheel_and_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    api, xtst = _x11(monkeypatch)
    backend = x11.InputBackend(Geometry(10, 20, 640, 360), x11=api, xtst=xtst)
    state = inputs.InputState()
    state.backend = backend
    state.apply(
        inputs.validate(
            {
                "events": [
                    {"type": "move", "x": 0.5, "y": 0.5},
                    {"type": "wheel", "dx": 0, "dy": 1},
                    {"type": "wheel", "dx": 0, "dy": -1},
                ]
            }
        )
    )
    xtst.XTestFakeMotionEvent.assert_called_once_with(backend.display, 0, 330, 200, 0)
    assert xtst.XTestFakeButtonEvent.call_args_list == [
        call(backend.display, 4, True, 0),
        call(backend.display, 4, False, 0),
        call(backend.display, 5, True, 0),
        call(backend.display, 5, False, 0),
    ]
    assert api.XFlush.call_count == 3
    backend.move(1, 1)
    xtst.XTestFakeMotionEvent.assert_called_with(backend.display, 0, 649, 379, 0)
    backend.wheel(0.5, 0)
    backend.wheel(0.5, 0)
    backend.wheel(-1, 0)
    assert [c.args[1] for c in xtst.XTestFakeButtonEvent.call_args_list[-4:]] == [7, 7, 6, 6]
    assert api.XOpenDisplay.restype is ctypes.c_void_p
    assert api.XKeysymToKeycode.restype is ctypes.c_ubyte


def test_x11_key_mapping_and_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    api, xtst = _x11(monkeypatch)
    backend = x11.InputBackend(GEOMETRY, x11=api, xtst=xtst)
    backend.key("KeyA", True)
    api.XStringToKeysym.assert_called_once_with(b"a")
    api.XKeysymToKeycode.assert_called_once_with(backend.display, 0x61)
    backend.button("left", True)
    backend.release_all()
    assert xtst.XTestFakeKeyEvent.call_args_list == [
        call(backend.display, 38, True, 0),
        call(backend.display, 38, False, 0),
    ]
    assert xtst.XTestFakeButtonEvent.call_args_list[-1] == call(backend.display, 1, False, 0)
    assert not backend.keys and not backend.buttons
    api.XFlush.assert_called_with(backend.display)
    backend._connection()
    api.XCloseDisplay.assert_called_once_with(backend.display)


def test_x11_send_failure_can_retry_release(monkeypatch: pytest.MonkeyPatch) -> None:
    api, xtst = _x11(monkeypatch)
    backend = x11.InputBackend(GEOMETRY, x11=api, xtst=xtst)
    xtst.XTestFakeKeyEvent.return_value = 0
    with pytest.raises(OSError):
        backend.key("KeyA", True)
    assert "KeyA" in backend.keys
    xtst.XTestFakeKeyEvent.return_value = 1
    backend.release_all()
    assert not backend.keys
    backend.key("MetaLeft", True)
    xtst.XTestFakeKeyEvent.return_value = 0
    with pytest.raises(OSError):
        backend.release_all()
    assert "MetaLeft" in backend.keys
    xtst.XTestFakeKeyEvent.return_value = 1
    backend.release_all()
    assert not backend.keys
    api.XKeysymToKeycode.return_value = 0
    with pytest.raises(OSError, match="no keycode"):
        backend.key("Enter", True)


@pytest.mark.parametrize("wayland,reason", [(False, "no_display"), (True, "wayland_unsupported")])
def test_x11_no_display(monkeypatch: pytest.MonkeyPatch, wayland: bool, reason: str) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    if wayland:
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    load = Mock()
    monkeypatch.setattr(ctypes, "CDLL", load)
    with pytest.raises(Unsupported) as error:
        x11.InputBackend(GEOMETRY)
    assert error.value.reason == reason
    load.assert_not_called()


def test_x11_missing_library_display_or_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    api, xtst = _x11(monkeypatch)
    monkeypatch.setattr(ctypes, "CDLL", Mock(side_effect=OSError("missing library")))
    with pytest.raises(Unsupported) as error:
        x11.InputBackend(GEOMETRY, x11=api)
    assert error.value.reason == "input_unsupported"
    api.XOpenDisplay.return_value = None
    with pytest.raises(Unsupported) as error:
        x11.InputBackend(GEOMETRY, x11=api, xtst=xtst)
    assert error.value.reason == "no_display"
    api.XOpenDisplay.return_value = 123
    xtst.XTestQueryExtension.return_value = 0
    with pytest.raises(Unsupported) as error:
        x11.InputBackend(GEOMETRY, x11=api, xtst=xtst)
    assert error.value.reason == "input_unsupported"
    api.XCloseDisplay.assert_called_once_with(123)


def test_platform_maps_cover_t02_codes() -> None:
    assert set(mac.KEYCODES) == set(inputs.SCANCODES)
    assert set(x11.KEYSYMS) == set(inputs.SCANCODES)
