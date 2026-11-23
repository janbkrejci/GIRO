"""Windows SendInput backend. Importing this module never loads user32."""

from __future__ import annotations

from typing import Any

from aifactory.web.screen_capture import Geometry
from aifactory.web.screen_input import SCANCODES


class InputBackend:
    def __init__(self, geometry: Geometry, *, user32: Any = None) -> None:
        import ctypes

        self.ctypes = ctypes
        self.geometry = geometry
        self.user32 = user32 if user32 is not None else ctypes.WinDLL("user32", use_last_error=True)
        ulong = ctypes.c_uint32
        pointer = ctypes.c_size_t

        class MouseInput(ctypes.Structure):
            _fields_ = [
                ("dx", ctypes.c_int32),
                ("dy", ctypes.c_int32),
                ("mouseData", ulong),
                ("dwFlags", ulong),
                ("time", ulong),
                ("dwExtraInfo", pointer),
            ]

        class KeyboardInput(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_uint16),
                ("wScan", ctypes.c_uint16),
                ("dwFlags", ulong),
                ("time", ulong),
                ("dwExtraInfo", pointer),
            ]

        class Payload(ctypes.Union):
            _fields_ = [("mi", MouseInput), ("ki", KeyboardInput)]

        class Input(ctypes.Structure):
            _anonymous_ = ("payload",)
            _fields_ = [("type", ulong), ("payload", Payload)]

        self.input = Input
        self.mouse = MouseInput
        self.keyboard = KeyboardInput
        self.user32.SendInput.argtypes = [ctypes.c_uint32, ctypes.POINTER(Input), ctypes.c_int]
        self.user32.SendInput.restype = ctypes.c_uint32
        self.user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        self.user32.GetSystemMetrics.restype = ctypes.c_int

    def _send(self, kind: int, payload: Any) -> None:
        value = self.input()
        value.type = kind
        if kind == 0:
            value.mi = payload
        else:
            value.ki = payload
        if self.user32.SendInput(1, self.ctypes.byref(value), self.ctypes.sizeof(value)) != 1:
            raise OSError("Screen input could not be sent.")

    def move(self, x: float, y: float) -> None:
        left, top, width, height = (self.user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))
        g = self.geometry
        # Endpoints address the first and last pixels of the primary monitor.
        px = g.left + x * (g.width - 1)
        py = g.top + y * (g.height - 1)
        ax = round((px - left) * 65535 / max(1, width - 1))
        ay = round((py - top) * 65535 / max(1, height - 1))
        self._send(0, self.mouse(ax, ay, 0, 0x0001 | 0x8000 | 0x4000, 0, 0))

    def button(self, name: str, down: bool) -> None:
        flags = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}
        self._send(0, self.mouse(0, 0, 0, flags[name][0 if down else 1], 0, 0))

    def wheel(self, dx: float, dy: float) -> None:
        # API deltas are wheel detents: positive is right/up; one detent is 120 units.
        for delta, flag in ((dx, 0x1000), (dy, 0x0800)):
            if delta:
                self._send(0, self.mouse(0, 0, round(delta * 120) & 0xFFFFFFFF, flag, 0, 0))

    def key(self, code: str, down: bool) -> None:
        scan, extended = SCANCODES[code]
        flags = 0x0008 | (0x0001 if extended else 0) | (0 if down else 0x0002)
        self._send(1, self.keyboard(0, scan, flags, 0, 0))

    def release_all(self) -> None:
        """Held inputs are released by the shared InputState before this hook."""
