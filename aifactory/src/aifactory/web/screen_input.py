"""Validated, transient screen input shared by the stream and input endpoint."""

from __future__ import annotations

import importlib
import math
import os
import sys
import threading
from typing import Any, Protocol, cast

from aifactory.web.screen_capture import Geometry, Unsupported, probe

# Set-1 scan codes on the physical US keyboard, independent of the active layout.
SCANCODES: dict[str, tuple[int, bool]] = {
    **{
        f"Key{k}": (v, False)
        for k, v in zip(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            (
                30,
                48,
                46,
                32,
                18,
                33,
                34,
                35,
                23,
                36,
                37,
                38,
                50,
                49,
                24,
                25,
                16,
                19,
                31,
                20,
                22,
                47,
                17,
                45,
                21,
                44,
            ),
            strict=True,
        )
    },
    **{f"Digit{k}": (v, False) for k, v in zip("1234567890", range(2, 12), strict=True)},
    **{f"F{k}": (v, False) for k, v in enumerate((*range(59, 69), 87, 88), 1)},
    **{
        k: (v, False)
        for k, v in {
            "Escape": 1,
            "Backspace": 14,
            "Tab": 15,
            "Enter": 28,
            "Space": 57,
            "Minus": 12,
            "Equal": 13,
            "BracketLeft": 26,
            "BracketRight": 27,
            "Backslash": 43,
            "Semicolon": 39,
            "Quote": 40,
            "Backquote": 41,
            "Comma": 51,
            "Period": 52,
            "Slash": 53,
            "ControlLeft": 29,
            "ShiftLeft": 42,
            "ShiftRight": 54,
            "AltLeft": 56,
            "CapsLock": 58,
            "NumLock": 69,
            "ScrollLock": 70,
            "NumpadMultiply": 55,
            "NumpadSubtract": 74,
            "NumpadAdd": 78,
            "NumpadDecimal": 83,
            "Numpad0": 82,
            "Numpad1": 79,
            "Numpad2": 80,
            "Numpad3": 81,
            "Numpad4": 75,
            "Numpad5": 76,
            "Numpad6": 77,
            "Numpad7": 71,
            "Numpad8": 72,
            "Numpad9": 73,
        }.items()
    },
    **{
        k: (v, True)
        for k, v in {
            "ArrowLeft": 75,
            "ArrowRight": 77,
            "ArrowUp": 72,
            "ArrowDown": 80,
            "Delete": 83,
            "Insert": 82,
            "Home": 71,
            "End": 79,
            "PageUp": 73,
            "PageDown": 81,
            "ControlRight": 29,
            "AltRight": 56,
            "MetaLeft": 91,
            "MetaRight": 92,
            "OSLeft": 91,
            "OSRight": 92,
            "NumpadDivide": 53,
            "NumpadEnter": 28,
        }.items()
    },
}


class Backend(Protocol):
    def move(self, x: float, y: float) -> None: ...
    def button(self, name: str, down: bool) -> None: ...
    def wheel(self, dx: float, dy: float) -> None: ...
    def key(self, code: str, down: bool) -> None: ...
    def release_all(self) -> None: ...


class FakeBackend:
    """Opt-in test backend; its recording lives only in memory."""

    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def move(self, x: float, y: float) -> None:
        self.events.append(("move", x, y))

    def button(self, name: str, down: bool) -> None:
        self.events.append(("button", name, down))

    def wheel(self, dx: float, dy: float) -> None:
        self.events.append(("wheel", dx, dy))

    def key(self, code: str, down: bool) -> None:
        self.events.append(("key", code, down))

    def release_all(self) -> None:
        self.events.append(("release_all",))


def open_backend() -> Backend:
    if os.environ.get("GIRO_SCREEN_BACKEND") == "fake":
        return FakeBackend()
    module = (
        "windows"
        if sys.platform == "win32"
        else "macos"
        if sys.platform == "darwin"
        else "x11"
        if sys.platform.startswith("linux")
        else None
    )
    if module is None:
        raise Unsupported("input_unsupported")
    try:
        implementation = importlib.import_module(f"aifactory.web.screen_input_{module}")
    except ImportError:
        raise Unsupported("input_unsupported") from None
    geometry = probe()
    if not isinstance(geometry, Geometry):
        raise geometry
    if sys.platform == "win32":
        from aifactory.web.screen_service import WindowsInput

        return WindowsInput(geometry)
    return cast(Backend, implementation.InputBackend(geometry))


def validate(body: Any) -> list[dict[str, Any]]:
    """Validate the entire batch before allowing any side effects."""
    if not isinstance(body, dict) or set(body) != {"events"}:
        raise ValueError
    events = body["events"]
    if not isinstance(events, list) or len(events) > 200:
        raise ValueError
    fields = {
        "move": {"type", "x", "y"},
        "button": {"type", "button", "down", "x", "y"},
        "wheel": {"type", "dx", "dy"},
        "key": {"type", "code", "down"},
        "release_all": {"type"},
    }
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise ValueError
        kind = event["type"]
        if kind not in fields or set(event) != fields[kind]:
            raise ValueError
        for name in ("x", "y", "dx", "dy"):
            if name in event:
                value = event[name]
                if type(value) not in (float, int) or not math.isfinite(value):
                    raise ValueError
                if name in ("x", "y") and not 0 <= value <= 1:
                    raise ValueError
                # SendInput wheel data is a signed 32-bit count of wheel units.
                if name in ("dx", "dy") and abs(value) > (2**31 - 1) / 120:
                    raise ValueError
        if "down" in event and type(event["down"]) is not bool:
            raise ValueError
        if kind == "button" and event["button"] not in ("left", "middle", "right"):
            raise ValueError
        if kind == "key" and not isinstance(event["code"], str):
            raise ValueError
    return cast(list[dict[str, Any]], events)


class InputState:
    """Serialize batches and cleanup, retaining only currently held inputs."""

    def __init__(self) -> None:
        self.backend: Backend | None = None
        self.keys: dict[str, None] = {}
        self.buttons: dict[str, None] = {}
        self.lock = threading.RLock()

    def release_all(self) -> None:
        with self.lock:
            if self.backend is None:
                return
            # Keep failed releases tracked so another cleanup can retry them.
            for code in list(self.keys):
                self.backend.key(code, False)
                del self.keys[code]
            for name in list(self.buttons):
                self.backend.button(name, False)
                del self.buttons[name]
            self.backend.release_all()

    def apply(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        with self.lock:
            if self.backend is None:
                self.backend = open_backend()
            ignored: list[str] = []
            applied = 0
            for event in events:
                kind = event["type"]
                if kind == "key" and event["code"] not in SCANCODES:
                    ignored.append(event["code"])
                    continue
                if kind == "move" or kind == "button":
                    self.backend.move(event["x"], event["y"])
                if kind in ("button", "key"):
                    name = event["button" if kind == "button" else "code"]
                    held = self.buttons if kind == "button" else self.keys
                    if event["down"]:
                        held[name] = None
                    if kind == "button":
                        self.backend.button(name, event["down"])
                    else:
                        self.backend.key(name, event["down"])
                    if not event["down"]:
                        held.pop(name, None)
                elif kind == "wheel":
                    self.backend.wheel(event["dx"], event["dy"])
                elif kind == "release_all":
                    self.release_all()
                applied += 1
            return {"applied": applied, "ignored": ignored}
