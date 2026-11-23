"""Track API route changes against the inventory before splitting ``web/app.py``.

``api_routes.txt`` is the list from before the split; only the two routes nothing called
(``GET .../backlog/task-advice/options`` and ``POST /api/terminal/exec``) are gone since.
The screen input endpoint was added after the split.
"""

from __future__ import annotations

from pathlib import Path

from api_routes import api_routes

from aifactory.web.app import create_multi_app

REMOVED = {
    ("/api/repos/{repo_id}/backlog/task-advice/options", "GET"),
    ("/api/terminal/exec", "POST"),
}
ADDED = {("/api/screen/input", "POST")}


def _before() -> set[tuple[str, str]]:
    lines = (Path(__file__).parent / "api_routes.txt").read_text(encoding="utf-8").splitlines()
    return {(path, method) for method, path in (line.split(" ", 1) for line in lines if line)}


def test_routes_match_the_inventory_with_explicit_changes(tmp_path: Path) -> None:
    app = create_multi_app(home=tmp_path / "home", static_dir=tmp_path / "nostatic")
    before = _before()
    assert REMOVED <= before
    assert ADDED.isdisjoint(before)
    assert api_routes(app) == (before - REMOVED) | ADDED
