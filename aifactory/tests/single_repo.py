"""The dashboard with one registered repository, for tests of the repository API.

``repo_app(root)`` is ``create_multi_app`` with ``root`` registered (in the test's
``GIRO_HOME`` unless ``home`` is given). ``RepoClient`` sends ``/api/<path>`` to that
repository's API at ``/api/repos/<id>/<path>``, except the dashboard routes in
``DASHBOARD``, so a test names the repository API by its own paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.testclient import TestClient

from aifactory.web.app import create_multi_app

# First path segments under /api/ that stay dashboard-wide in RepoClient.
DASHBOARD = frozenset({"health", "code", "restart", "updates", "guide"})


def repo_app(root: Path, **kwargs: Any) -> Starlette:
    """``create_multi_app(**kwargs)`` with ``root`` registered (id in ``app.state.test_repo``)."""
    app = create_multi_app(**kwargs)
    # Registered as is, without the vetting of POST /api/repos (tests use repos without commits).
    entry, _created = app.state.registry.add(root.resolve())
    app.state.test_repo = entry.id
    return app


def repo_url(repo_id: str, url: str) -> str:
    """``/api/<path>`` of the repository API as served by the multi-repository dashboard."""
    if not url.startswith("/api/"):
        return url
    rest = url[len("/api/") :]
    if rest.split("/", 1)[0].split("?", 1)[0] in DASHBOARD:
        return url
    return f"/api/repos/{quote(repo_id, safe='')}/{rest}"


class RepoClient(TestClient):
    """A ``TestClient`` of a ``repo_app`` whose ``/api/`` paths name the repository API."""

    def request(self, method: str, url: Any, *args: Any, **kwargs: Any) -> Any:
        repo_id: str = self.app.state.test_repo  # type: ignore[attr-defined]
        return super().request(method, repo_url(repo_id, str(url)), *args, **kwargs)
