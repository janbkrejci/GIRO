"""The (path, method) pairs the dashboard API serves, read from the app's route tree."""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.routing import BaseRoute, Mount, Route


def api_routes(app: Starlette) -> set[tuple[str, str]]:
    """Every ``(path, method)`` under ``/api`` (``HEAD`` is left out: Starlette adds it to GET)."""
    found: set[tuple[str, str]] = set()

    def walk(routes: list[BaseRoute], prefix: str) -> None:
        for route in routes:
            if isinstance(route, Mount):
                inner = getattr(route.app, "app", route.app)  # RepoScope wraps a Router
                walk(list(getattr(inner, "routes", route.routes or [])), prefix + route.path)
            elif isinstance(route, Route):
                for method in route.methods or ():
                    if method != "HEAD":
                        found.add((prefix + route.path, method))

    walk(list(app.routes), "")
    return {(path, method) for path, method in found if path.startswith("/api")}
