"""Lazy CoreGraphics input in main-display points, independent of Retina pixels."""

from __future__ import annotations

import ctypes
from typing import Any

from aifactory.web.screen_capture import Geometry, Unsupported


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


KEYCODES = {
    **dict(
        zip(
            (f"Key{k}" for k in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
            (
                0,
                11,
                8,
                2,
                14,
                3,
                5,
                4,
                34,
                38,
                40,
                37,
                46,
                45,
                31,
                35,
                12,
                15,
                1,
                17,
                32,
                9,
                13,
                7,
                16,
                6,
            ),
            strict=True,
        )
    ),
    **dict(
        zip(
            (f"Digit{k}" for k in "1234567890"),
            (18, 19, 20, 21, 23, 22, 26, 28, 25, 29),
            strict=True,
        )
    ),
    **dict(
        zip(
            (f"F{k}" for k in range(1, 13)),
            (122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111),
            strict=True,
        )
    ),
    "Enter": 36,
    "Tab": 48,
    "Space": 49,
    "Backspace": 51,
    "Escape": 53,
    "Minus": 27,
    "Equal": 24,
    "BracketLeft": 33,
    "BracketRight": 30,
    "Backslash": 42,
    "Semicolon": 41,
    "Quote": 39,
    "Backquote": 50,
    "Comma": 43,
    "Period": 47,
    "Slash": 44,
    "MetaLeft": 55,
    "MetaRight": 54,
    "OSLeft": 55,
    "OSRight": 54,
    "ShiftLeft": 56,
    "ShiftRight": 60,
    "AltLeft": 58,
    "AltRight": 61,
    "ControlLeft": 59,
    "ControlRight": 62,
    "CapsLock": 57,
    "ArrowLeft": 123,
    "ArrowRight": 124,
    "ArrowDown": 125,
    "ArrowUp": 126,
    "Home": 115,
    "End": 119,
    "PageUp": 116,
    "PageDown": 121,
    "Delete": 117,
    "Insert": 114,
    "NumLock": 71,
    "ScrollLock": 107,
    "Numpad0": 82,
    "Numpad1": 83,
    "Numpad2": 84,
    "Numpad3": 85,
    "Numpad4": 86,
    "Numpad5": 87,
    "Numpad6": 88,
    "Numpad7": 89,
    "Numpad8": 91,
    "Numpad9": 92,
    "NumpadDecimal": 65,
    "NumpadMultiply": 67,
    "NumpadAdd": 69,
    "NumpadDivide": 75,
    "NumpadEnter": 76,
    "NumpadSubtract": 78,
}
MODIFIERS = {
    "ShiftLeft": 1 << 17,
    "ShiftRight": 1 << 17,
    "ControlLeft": 1 << 18,
    "ControlRight": 1 << 18,
    "AltLeft": 1 << 19,
    "AltRight": 1 << 19,
    "MetaLeft": 1 << 20,
    "MetaRight": 1 << 20,
    "OSLeft": 1 << 20,
    "OSRight": 1 << 20,
}


class InputBackend:
    def __init__(self, geometry: Geometry, *, services: Any = None, core: Any = None) -> None:
        try:
            self.api = (
                services
                if services is not None
                else ctypes.CDLL(
                    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
                )
            )
            self.core = (
                core
                if core is not None
                else ctypes.CDLL(
                    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
                )
            )
        except OSError:
            raise Unsupported("input_unsupported") from None
        api = self.api
        for name, args, result in (
            ("AXIsProcessTrusted", [], ctypes.c_bool),
            ("CGMainDisplayID", [], ctypes.c_uint32),
            ("CGDisplayBounds", [ctypes.c_uint32], CGRect),
            (
                "CGEventCreateMouseEvent",
                [ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32],
                ctypes.c_void_p,
            ),
            (
                "CGEventCreateKeyboardEvent",
                [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool],
                ctypes.c_void_p,
            ),
            # Only fixed arguments of this variadic function belong in argtypes.
            (
                "CGEventCreateScrollWheelEvent",
                [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32],
                ctypes.c_void_p,
            ),
            ("CGEventSetFlags", [ctypes.c_void_p, ctypes.c_uint64], None),
            ("CGEventPost", [ctypes.c_uint32, ctypes.c_void_p], None),
        ):
            function = getattr(api, name)
            function.argtypes = args
            function.restype = result
        self.core.CFRelease.argtypes = [ctypes.c_void_p]
        self.core.CFRelease.restype = None
        if not api.AXIsProcessTrusted():
            error = Unsupported("permission_denied")
            error.args = (
                "Allow Accessibility for the dashboard's terminal or application in "
                "System Settings > Privacy & Security > Accessibility.",
            )
            raise error
        self.bounds = api.CGDisplayBounds(api.CGMainDisplayID())
        self.position = self.bounds.origin
        self.keys: dict[str, None] = {}
        self.buttons: dict[str, None] = {}

    def _post(self, event: Any) -> None:
        if not event:
            raise OSError("CoreGraphics could not create an input event.")
        try:
            flags = 0
            for code in self.keys:
                flags |= MODIFIERS.get(code, 0)
            self.api.CGEventSetFlags(event, flags)
            self.api.CGEventPost(0, event)
        finally:
            self.core.CFRelease(event)

    def move(self, x: float, y: float) -> None:
        b = self.bounds
        self.position = CGPoint(
            b.origin.x + x * (b.size.width - 1), b.origin.y + y * (b.size.height - 1)
        )
        name = next(iter(self.buttons), None)
        kind, button = {"left": (6, 0), "right": (7, 1), "middle": (27, 2)}.get(name or "", (5, 0))
        self._post(self.api.CGEventCreateMouseEvent(None, kind, self.position, button))

    def button(self, name: str, down: bool) -> None:
        button, press, release = {"left": (0, 1, 2), "right": (1, 3, 4), "middle": (2, 25, 26)}[
            name
        ]
        if down:
            self.buttons[name] = None
        self._post(
            self.api.CGEventCreateMouseEvent(
                None, press if down else release, self.position, button
            )
        )
        if not down:
            self.buttons.pop(name, None)

    def wheel(self, dx: float, dy: float) -> None:
        # API detents are positive right/up; Quartz horizontal is positive left.
        self._post(
            self.api.CGEventCreateScrollWheelEvent(
                None, 1, 2, ctypes.c_int32(round(dy)), ctypes.c_int32(-round(dx))
            )
        )

    def key(self, code: str, down: bool) -> None:
        previous = self.keys.copy()
        if down:
            self.keys[code] = None
        else:
            self.keys.pop(code, None)
        try:
            self._post(self.api.CGEventCreateKeyboardEvent(None, KEYCODES[code], down))
        except OSError:
            if not down:
                self.keys = previous
            raise

    def release_all(self) -> None:
        for code in list(self.keys):
            self.key(code, False)
        for name in list(self.buttons):
            self.button(name, False)
