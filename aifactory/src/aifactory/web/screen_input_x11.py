"""Lazy X11/XTest input. Pure Wayland sessions are unsupported."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import weakref
from typing import Any

from aifactory.web.screen_capture import Geometry, Unsupported

KEYSYMS = {
    **{f"Key{k}": k.lower() for k in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    **{f"Digit{k}": k for k in "0123456789"},
    **{f"F{k}": f"F{k}" for k in range(1, 13)},
    **{f"Numpad{k}": f"KP_{k}" for k in range(10)},
    "Enter": "Return",
    "Escape": "Escape",
    "Backspace": "BackSpace",
    "Tab": "Tab",
    "Space": "space",
    "Minus": "minus",
    "Equal": "equal",
    "BracketLeft": "bracketleft",
    "BracketRight": "bracketright",
    "Backslash": "backslash",
    "Semicolon": "semicolon",
    "Quote": "apostrophe",
    "Backquote": "grave",
    "Comma": "comma",
    "Period": "period",
    "Slash": "slash",
    "ShiftLeft": "Shift_L",
    "ShiftRight": "Shift_R",
    "ControlLeft": "Control_L",
    "ControlRight": "Control_R",
    "AltLeft": "Alt_L",
    "AltRight": "Alt_R",
    "MetaLeft": "Super_L",
    "MetaRight": "Super_R",
    "OSLeft": "Super_L",
    "OSRight": "Super_R",
    "CapsLock": "Caps_Lock",
    "NumLock": "Num_Lock",
    "ScrollLock": "Scroll_Lock",
    "ArrowLeft": "Left",
    "ArrowRight": "Right",
    "ArrowUp": "Up",
    "ArrowDown": "Down",
    "Insert": "Insert",
    "Delete": "Delete",
    "Home": "Home",
    "End": "End",
    "PageUp": "Prior",
    "PageDown": "Next",
    "NumpadMultiply": "KP_Multiply",
    "NumpadSubtract": "KP_Subtract",
    "NumpadAdd": "KP_Add",
    "NumpadDecimal": "KP_Decimal",
    "NumpadDivide": "KP_Divide",
    "NumpadEnter": "KP_Enter",
}


class InputBackend:
    def __init__(self, geometry: Geometry, *, x11: Any = None, xtst: Any = None) -> None:
        if not os.environ.get("DISPLAY"):
            raise Unsupported(
                "wayland_unsupported" if os.environ.get("WAYLAND_DISPLAY") else "no_display"
            )
        try:
            self.x11 = (
                x11
                if x11 is not None
                else ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
            )
            self.xtst = (
                xtst
                if xtst is not None
                else ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
            )
        except OSError:
            raise Unsupported("input_unsupported") from None
        pointer, integer, uint = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint
        for library, name, args, result in (
            (self.x11, "XOpenDisplay", [ctypes.c_char_p], pointer),
            (self.x11, "XCloseDisplay", [pointer], integer),
            (self.x11, "XDefaultScreen", [pointer], integer),
            (self.x11, "XStringToKeysym", [ctypes.c_char_p], ctypes.c_ulong),
            (self.x11, "XKeysymToKeycode", [pointer, ctypes.c_ulong], ctypes.c_ubyte),
            (self.x11, "XFlush", [pointer], integer),
            (
                self.xtst,
                "XTestQueryExtension",
                [pointer, *([ctypes.POINTER(integer)] * 4)],
                integer,
            ),
            (
                self.xtst,
                "XTestFakeMotionEvent",
                [pointer, integer, integer, integer, ctypes.c_ulong],
                integer,
            ),
            (self.xtst, "XTestFakeButtonEvent", [pointer, uint, integer, ctypes.c_ulong], integer),
            (self.xtst, "XTestFakeKeyEvent", [pointer, uint, integer, ctypes.c_ulong], integer),
        ):
            function = getattr(library, name)
            function.argtypes = args
            function.restype = result
        self.display = self.x11.XOpenDisplay(None)
        if not self.display:
            raise Unsupported("no_display")
        self._connection = weakref.finalize(self, self.x11.XCloseDisplay, self.display)
        values = [integer() for _ in range(4)]
        if not self.xtst.XTestQueryExtension(self.display, *(ctypes.byref(v) for v in values)):
            self._connection()
            raise Unsupported("input_unsupported")
        self.screen = self.x11.XDefaultScreen(self.display)
        self.geometry = geometry
        self.keys: dict[str, int] = {}
        self.buttons: dict[str, None] = {}
        self.remainder = [0.0, 0.0]

    def _send(self, name: str, *args: Any) -> None:
        if not getattr(self.xtst, name)(self.display, *args, 0):
            raise OSError("XTest could not send an input event.")

    def move(self, x: float, y: float) -> None:
        g = self.geometry
        self._send(
            "XTestFakeMotionEvent",
            self.screen,
            g.left + round(x * (g.width - 1)),
            g.top + round(y * (g.height - 1)),
        )
        self.x11.XFlush(self.display)

    def button(self, name: str, down: bool) -> None:
        if down:
            self.buttons[name] = None
        self._send("XTestFakeButtonEvent", {"left": 1, "middle": 2, "right": 3}[name], down)
        if not down:
            self.buttons.pop(name, None)
        self.x11.XFlush(self.display)

    def wheel(self, dx: float, dy: float) -> None:
        # XTest has discrete wheel buttons. Retain sub-detent input between calls.
        for axis, delta, positive, negative in ((0, dx, 7, 6), (1, dy, 4, 5)):
            self.remainder[axis] += delta
            while abs(self.remainder[axis]) >= 1:
                direction = 1 if self.remainder[axis] > 0 else -1
                button = positive if direction > 0 else negative
                self._send("XTestFakeButtonEvent", button, True)
                self._send("XTestFakeButtonEvent", button, False)
                self.remainder[axis] -= direction
        self.x11.XFlush(self.display)

    def key(self, code: str, down: bool) -> None:
        keycode = self.keys.get(code)
        if keycode is None:
            keysym = self.x11.XStringToKeysym(KEYSYMS[code].encode("ascii"))
            keycode = int(self.x11.XKeysymToKeycode(self.display, keysym))
        if not keycode:
            raise OSError("The X11 keymap has no keycode for this key.")
        if down:
            self.keys[code] = keycode
        self._send("XTestFakeKeyEvent", keycode, down)
        if not down:
            self.keys.pop(code, None)
        self.x11.XFlush(self.display)

    def release_all(self) -> None:
        for code in list(self.keys):
            self.key(code, False)
        for name in list(self.buttons):
            self.button(name, False)
        self.remainder = [0.0, 0.0]
        # The T02 contract has no batch hook; flush each operation and cleanup.
        self.x11.XFlush(self.display)
