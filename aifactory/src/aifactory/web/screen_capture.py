"""In-memory primary-monitor capture. Instances belong to their creating thread."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol

import mss
from PIL import Image


class Unsupported(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        message = reason
        if reason == "permission_denied":
            message = (
                "Allow Screen Recording for the dashboard's terminal or application in "
                "System Settings > Privacy & Security > Screen Recording."
            )
        super().__init__(message)


@dataclass(frozen=True)
class Geometry:
    left: int
    top: int
    width: int
    height: int


class Capture(Protocol):
    geometry: Geometry

    def read(self) -> bytes: ...
    def close(self) -> None: ...


class MSSCapture:
    def __init__(self) -> None:
        self.device = mss.mss()
        try:
            self.monitor = self.device.monitors[1]
            self.geometry = Geometry(
                left=self.monitor["left"],
                top=self.monitor["top"],
                width=self.monitor["width"],
                height=self.monitor["height"],
            )
        except BaseException:
            self.device.close()
            raise

    def read(self) -> bytes:
        return self.device.grab(self.monitor).bgra

    def close(self) -> None:
        self.device.close()


class FakeCapture:
    """Deterministic changing frames, explicitly opted into by tests."""

    geometry = Geometry(0, 0, 640, 360)

    def __init__(self) -> None:
        self.counter = 0
        self.base = Image.linear_gradient("L").resize((640, 360)).convert("RGB")

    def read(self) -> bytes:
        frame = self.base.copy()
        frame.paste((self.counter % 256, 100, 200), (0, 0, 64, 64))
        self.counter += 1
        return frame.tobytes("raw", "BGRX")

    def close(self) -> None:
        self.base.close()


def open_capture(target_session: int | None = None) -> Capture:
    if target_session is not None:
        if sys.platform != "win32":
            raise Unsupported("desktop_session_unavailable")
        from aifactory.web.screen_service import WindowsCapture

        return WindowsCapture(target_session)
    if os.environ.get("GIRO_SCREEN_BACKEND") == "fake":
        return FakeCapture()
    if sys.platform == "win32":
        from aifactory.web.screen_service import WindowsCapture

        return WindowsCapture()
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        raise Unsupported(
            "wayland_unsupported" if os.environ.get("WAYLAND_DISPLAY") else "no_display"
        )
    return MSSCapture()


def capture_error() -> Unsupported:
    return Unsupported("permission_denied" if sys.platform == "darwin" else "capture_failed")


def jpeg(raw: bytes, geometry: Geometry, max_width: int, quality: int) -> bytes:
    image = Image.frombytes("RGB", (geometry.width, geometry.height), raw, "raw", "BGRX")
    try:
        if image.width > max_width:
            image = image.resize(
                (max_width, max(1, image.height * max_width // image.width)),
                Image.Resampling.LANCZOS,
            )
        output = BytesIO()
        image.save(output, "JPEG", quality=quality)
        return output.getvalue()
    finally:
        image.close()


def probe(target_session: int | None = None) -> Geometry | Unsupported:
    capture = None
    try:
        capture = open_capture() if target_session is None else open_capture(target_session)
        capture.read()  # Opening alone does not verify macOS recording permission.
        return capture.geometry
    except Unsupported as exc:
        return exc
    except Exception:
        return capture_error()
    finally:
        if capture is not None:
            capture.close()
