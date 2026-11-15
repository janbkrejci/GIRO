"""The dashboard notices a changed package: /api/code, blocked writes and the restart."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient
from web_repo import git_repo

from aifactory.codeprint import CodeWatch
from aifactory.upgrade import UpgradeError
from aifactory.web import create_app, guard

BASE = "http://127.0.0.1:4700"


@pytest.mark.parametrize("committed", [False, True])
def test_migration_validation_keeps_reads_responsive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, committed: bool
) -> None:
    started, release, blocked = threading.Event(), threading.Event(), threading.Event()
    calls: list[str] = []
    statuses: list[int] = []

    def slow_check(home: Path, *, writing: bool = False) -> None:
        assert home == tmp_path and writing
        started.set()
        if not release.wait(2):
            blocked.set()
        if not committed:
            raise UpgradeError("migration_pending", "Not committed")

    monkeypatch.setattr(guard, "check_home", slow_check)

    async def scenario() -> None:
        from starlette.types import Message, Scope

        async def endpoint(scope: Scope, receive: object, send: object) -> None:
            calls.append(scope["method"])

        async def receive() -> Message:
            return {"type": "http.request", "body": b""}

        async def send(message: Message) -> None:
            if message["type"] == "http.response.start":
                statuses.append(message["status"])

        middleware = guard.StaleCodeMiddleware(endpoint)
        scope: Scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/terminal/sessions",
            "app": SimpleNamespace(state=SimpleNamespace(home=tmp_path)),
        }
        task = asyncio.create_task(middleware(scope, receive, send))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            await middleware({**scope, "method": "GET", "path": "/api/health"}, receive, send)
            assert calls == ["GET"]
            assert not blocked.is_set(), "disk validation blocked the health endpoint"
            release.set()
            await asyncio.wait_for(task, 3)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert calls == (["GET", "POST"] if committed else ["GET"])
    assert statuses == ([] if committed else [409])


def _client(tmp_path: Path, restarts: list[str]) -> tuple[TestClient, Path]:
    root = git_repo(tmp_path / "repo")
    package = tmp_path / "package"
    package.mkdir()
    (package / "mod.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    app = create_app(
        root,
        static_dir=tmp_path / "nostatic",
        code_watch=CodeWatch(package, ttl=0.0),
        restart=lambda: restarts.append("restart"),
    )
    return TestClient(app, base_url=BASE), package


def test_fresh_dashboard_is_not_stale(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, [])
    body = client.get("/api/code").json()
    assert body["ok"] is True
    assert body["data"]["stale"] is False
    assert body["data"]["started"] == body["data"]["current"]


def test_changed_package_blocks_writes_but_not_reads(tmp_path: Path) -> None:
    client, package = _client(tmp_path, [])
    (package / "mod.py").write_text("x = 22\n", encoding="utf-8", newline="\n")
    assert client.get("/api/code").json()["data"]["stale"] is True
    response = client.post("/api/backlog/commit", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_code"
    assert "restartuj" in response.json()["error"]["message"].lower()
    assert client.get("/api/health").status_code == 200


def test_restart_answers_and_then_restarts(tmp_path: Path) -> None:
    restarts: list[str] = []
    client, package = _client(tmp_path, restarts)
    (package / "mod.py").write_text("x = 22\n", encoding="utf-8", newline="\n")
    response = client.post("/api/restart")
    assert response.status_code == 200
    assert response.json()["data"] == {"restarting": True}
    assert restarts == ["restart"]
