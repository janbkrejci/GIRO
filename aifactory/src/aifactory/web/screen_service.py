"""Optional Windows service adapter; ordinary desktop capture remains self-contained."""

from __future__ import annotations

import json
import struct
import time
from typing import Any

from aifactory.web import screen_pipe
from aifactory.web.screen_capture import Geometry, MSSCapture, Unsupported


def request(operation: str, **parameters: Any) -> bytes:
    raw = screen_pipe.exchange(
        json.dumps({"version": 1, "operation": operation, **parameters}).encode("utf-8")
    )
    if operation == "capture" and raw.startswith(b"GDS1"):
        return raw
    try:
        reply = json.loads(raw)
        if not isinstance(reply, dict) or reply.get("version") != 1 or reply.get("ok") is not True:
            raise ValueError
    except (ValueError, UnicodeDecodeError) as exc:
        raise OSError("Desktop service unavailable or incompatible") from exc
    if operation == "capture":
        raise OSError("Desktop service returned no frame")
    return raw


def frame(raw: bytes) -> tuple[Geometry, bytes]:
    if len(raw) < 16 or raw[:4] != b"GDS1":
        raise OSError("Invalid desktop frame")
    width, height, reserved = struct.unpack("!III", raw[4:16])
    if not width or not height or reserved or width * height * 4 != len(raw) - 16:
        raise OSError("Invalid desktop geometry")
    return Geometry(0, 0, width, height), raw[16:]


class WindowsCapture:
    def __init__(self) -> None:
        self.local: MSSCapture | None = None
        self.geometry = Geometry(0, 0, 1, 1)
        self.service_active = False
        self.pending: bytes | None = None
        try:
            self.pending = self._read()
        except BaseException:
            self.close()
            raise

    def _read(self) -> bytes:
        # Switching input desktops can briefly make GDI unavailable. Retry capture only;
        # input commands are never retried because a failed reply can follow a successful send.
        attempts = 4 if self.service_active else 1
        for attempt in range(attempts):
            try:
                self.geometry, raw = frame(request("capture"))
                self.service_active = True
                return raw
            except OSError:
                if attempt + 1 < attempts:
                    time.sleep(0.05)
        self.service_active = False
        if screen_pipe.protected_desktop():
            raise Unsupported("desktop_service_required") from None
        if self.local is None:
            self.local = MSSCapture()
        self.geometry = self.local.geometry
        return self.local.read()

    def read(self) -> bytes:
        if self.pending is not None:
            raw, self.pending = self.pending, None
            return raw
        return self._read()

    def close(self) -> None:
        if self.local is not None:
            self.local.close()
        self.pending = None


class WindowsInput:
    def __init__(self, geometry: Geometry) -> None:
        from aifactory.web.screen_input_windows import InputBackend

        self.local = InputBackend(geometry)
        self.service_held = False
        self.keys: dict[str, bool] = {}
        self.buttons: dict[str, bool] = {}

    def _send(self, operation: str, **parameters: Any) -> None:
        # Never replay input after an ambiguous timeout: it may already have been applied.
        request(operation, **parameters)

    def _available(self) -> bool:
        try:
            request("hello")
            return True
        except OSError:
            if screen_pipe.protected_desktop():
                raise Unsupported("desktop_service_required") from None
            return False

    def move(self, x: float, y: float) -> None:
        if self._available():
            self._send("move", x=x, y=y)
        else:
            self.local.move(x, y)

    def button(self, name: str, down: bool) -> None:
        service = self.buttons.get(name)
        if service is None:
            service = self._available()
        if down:
            self.buttons[name] = service
        if service:
            self.service_held = True
            try:
                self._send("button", button=name, down=down)
            except OSError:
                if down:
                    raise
                # The service's independent lease also handles failed release replies.
        else:
            self.local.button(name, down)
        if not down:
            self.buttons.pop(name, None)

    def wheel(self, dx: float, dy: float) -> None:
        if self._available():
            self._send("wheel", dx=dx, dy=dy)
        else:
            self.local.wheel(dx, dy)

    def key(self, code: str, down: bool) -> None:
        from aifactory.web.screen_input import SCANCODES

        service = self.keys.get(code)
        if service is None:
            service = self._available()
        if down:
            self.keys[code] = service
        if service:
            scan, extended = SCANCODES[code]
            self.service_held = True
            try:
                self._send("key", scan=scan, extended=extended, down=down)
            except OSError:
                if down:
                    raise
        else:
            self.local.key(code, down)
        if not down:
            self.keys.pop(code, None)

    def release_all(self) -> None:
        for code, service in list(self.keys.items()):
            if not service:
                self.local.key(code, False)
                del self.keys[code]
        for name, service in list(self.buttons.items()):
            if not service:
                self.local.button(name, False)
                del self.buttons[name]
        if self.service_held:
            try:
                request("release")
            except OSError:
                pass  # The service independently releases held input after its lease expires.
            self.service_held = False
            self.keys = {key: route for key, route in self.keys.items() if not route}
            self.buttons = {key: route for key, route in self.buttons.items() if not route}
        self.local.release_all()
