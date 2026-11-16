"""``/api/terminal``: the hidden terminal's availability and its PTY sessions."""

from __future__ import annotations

from starlette.routing import BaseRoute, Route

from aifactory.web.terminal import terminal_get
from aifactory.web.terminal_pty import terminal_session

ROUTES: list[BaseRoute] = [
    Route("/terminal", terminal_get, methods=["GET"]),
    Route("/terminal/sessions", terminal_session, methods=["POST"]),
    Route("/terminal/sessions/{id}/stream", terminal_session, methods=["GET"]),
    Route("/terminal/sessions/{id}/input", terminal_session, methods=["POST"]),
    Route("/terminal/sessions/{id}/resize", terminal_session, methods=["POST"]),
    Route("/terminal/sessions/{id}", terminal_session, methods=["DELETE"]),
]
"""Dashboard-wide routes, relative to ``/api``."""
