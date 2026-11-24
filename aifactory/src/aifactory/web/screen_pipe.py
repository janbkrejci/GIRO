"""Bounded local Windows pipe RPC. Authenticate SYSTEM ownership before sending input."""

from __future__ import annotations

import ctypes
import struct
import time
from typing import Any

MAX_REPLY = 64 * 1024 * 1024
TIMEOUT = 2.0


class Overlapped(ctypes.Structure):
    _fields_ = [
        ("internal", ctypes.c_size_t),
        ("internal_high", ctypes.c_size_t),
        ("offset", ctypes.c_uint32),
        ("offset_high", ctypes.c_uint32),
        ("event", ctypes.c_void_p),
    ]


def _function(dll: Any, name: str, result: Any, *args: Any) -> Any:
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = list(args)
    return function


class Pipe:
    def __init__(self) -> None:
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.security = ctypes.WinDLL("advapi32", use_last_error=True)
        pointer, number, boolean = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int
        out_number = ctypes.POINTER(number)
        self.create = _function(
            self.kernel,
            "CreateFileW",
            pointer,
            ctypes.c_wchar_p,
            number,
            number,
            pointer,
            number,
            number,
            pointer,
        )
        self.close = _function(self.kernel, "CloseHandle", boolean, pointer)
        self.event = _function(
            self.kernel, "CreateEventW", pointer, pointer, boolean, boolean, pointer
        )
        self.wait = _function(self.kernel, "WaitForSingleObject", number, pointer, number)
        self.cancel = _function(
            self.kernel, "CancelIoEx", boolean, pointer, ctypes.POINTER(Overlapped)
        )
        self.result = _function(
            self.kernel,
            "GetOverlappedResult",
            boolean,
            pointer,
            ctypes.POINTER(Overlapped),
            out_number,
            boolean,
        )
        self.read = _function(
            self.kernel,
            "ReadFile",
            boolean,
            pointer,
            pointer,
            number,
            out_number,
            ctypes.POINTER(Overlapped),
        )
        self.write = _function(
            self.kernel,
            "WriteFile",
            boolean,
            pointer,
            pointer,
            number,
            out_number,
            ctypes.POINTER(Overlapped),
        )

    def _trusted(self, handle: int) -> None:
        pointer = ctypes.c_void_p
        owner, descriptor, system = pointer(), pointer(), pointer()
        get_security = _function(
            self.security,
            "GetSecurityInfo",
            ctypes.c_uint32,
            pointer,
            ctypes.c_int,
            ctypes.c_uint32,
            ctypes.POINTER(pointer),
            pointer,
            pointer,
            pointer,
            ctypes.POINTER(pointer),
        )
        convert = _function(
            self.security,
            "ConvertStringSidToSidW",
            ctypes.c_int,
            ctypes.c_wchar_p,
            ctypes.POINTER(pointer),
        )
        equal = _function(self.security, "EqualSid", ctypes.c_int, pointer, pointer)
        free = _function(self.kernel, "LocalFree", pointer, pointer)
        try:
            status = get_security(
                handle, 6, 1, ctypes.byref(owner), None, None, None, ctypes.byref(descriptor)
            )
            if status or not convert("S-1-5-18", ctypes.byref(system)):
                raise OSError("Cannot authenticate desktop service")
            if not equal(owner, system):
                raise OSError("Untrusted desktop service")
        finally:
            if descriptor:
                free(descriptor)
            if system:
                free(system)

    def _transfer(
        self, handle: int, buffer: Any, size: int, deadline: float, *, write: bool
    ) -> int:
        event = self.event(None, True, False, None)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())
        operation = Overlapped(event=event)
        count = ctypes.c_uint32()
        pending = False
        try:
            function = self.write if write else self.read
            if not function(handle, buffer, size, ctypes.byref(count), ctypes.byref(operation)):
                if ctypes.get_last_error() != 997:  # ERROR_IO_PENDING
                    raise ctypes.WinError(ctypes.get_last_error())
                pending = True
                remaining = max(0, int((deadline - time.monotonic()) * 1000))
                if self.wait(event, remaining) != 0:
                    raise TimeoutError("Desktop service timed out")
                pending = False
                if not self.result(handle, ctypes.byref(operation), ctypes.byref(count), False):
                    raise ctypes.WinError(ctypes.get_last_error())
            if count.value == 0:
                raise OSError("Desktop service disconnected")
            return count.value
        finally:
            if pending:
                # Do not release the OVERLAPPED or buffer until cancellation has completed.
                self.cancel(handle, ctypes.byref(operation))
                self.result(handle, ctypes.byref(operation), ctypes.byref(count), True)
            self.close(event)

    def _read_exactly(self, handle: int, length: int, deadline: float) -> bytes:
        buffer = ctypes.create_string_buffer(length)
        offset = 0
        while offset < length:
            offset += self._transfer(
                handle, ctypes.byref(buffer, offset), length - offset, deadline, write=False
            )
        return buffer.raw

    def exchange(self, body: bytes, target_session: int | None = None) -> bytes:
        if not 1 <= len(body) <= 4096:
            raise ValueError("Invalid desktop request size")
        number = ctypes.c_uint32
        session = number()
        get_pid = _function(self.kernel, "GetCurrentProcessId", number)
        get_session = _function(
            self.kernel, "ProcessIdToSessionId", ctypes.c_int, number, ctypes.POINTER(number)
        )
        if not get_session(get_pid(), ctypes.byref(session)) or not session.value:
            raise OSError("No interactive session")
        if target_session is not None:
            if type(target_session) is not int or not 0 < target_session < 0xFFFFFFFF:
                raise ValueError("Invalid desktop session")
            session.value = target_session
        name = rf"\\.\pipe\GIRO.Desktop.v1.{session.value}"
        deadline = time.monotonic() + TIMEOUT
        # Read/write + READ_CONTROL; overlapped I/O; identification-only impersonation.
        wait_pipe = _function(self.kernel, "WaitNamedPipeW", ctypes.c_int, ctypes.c_wchar_p, number)
        while True:
            handle = self.create(name, 0xC0020000, 0, None, 3, 0x40110000, None)
            if handle != ctypes.c_void_p(-1).value:
                break
            error = ctypes.get_last_error()
            if error != 231:  # ERROR_PIPE_BUSY; missing service fails immediately.
                raise ctypes.WinError(error)
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0 or not wait_pipe(name, remaining):
                raise TimeoutError("Desktop service busy")
        try:
            self._trusted(handle)
            data = struct.pack("!I", len(body)) + body
            buffer = ctypes.create_string_buffer(data)
            offset = 0
            while offset < len(data):
                offset += self._transfer(
                    handle, ctypes.byref(buffer, offset), len(data) - offset, deadline, write=True
                )
            length = struct.unpack("!I", self._read_exactly(handle, 4, deadline))[0]
            if not 1 <= length <= MAX_REPLY:
                raise OSError("Invalid desktop response size")
            return self._read_exactly(handle, length, deadline)
        finally:
            self.close(handle)


def exchange(body: bytes, target_session: int | None = None) -> bytes:
    return Pipe().exchange(body, target_session)


def protected_desktop() -> bool:
    """An ordinary process cannot open Winlogon; do not capture stale default pixels."""
    user = ctypes.WinDLL("user32", use_last_error=True)
    open_desktop = _function(
        user, "OpenInputDesktop", ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32
    )
    close = _function(user, "CloseDesktop", ctypes.c_int, ctypes.c_void_p)
    get_name = _function(
        user,
        "GetUserObjectInformationW",
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    )
    desktop = open_desktop(0, False, 1)
    if not desktop:
        return True
    try:
        name = ctypes.create_unicode_buffer(256)
        size = ctypes.c_uint32()
        return not get_name(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(size)) or (
            name.value.casefold() != "default"
        )
    finally:
        close(desktop)
