"""The hidden terminal: real local processes, no models or external services."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aifactory.codeprint import CodeWatch
from aifactory.skill import envelope_problems
from aifactory.web import terminal
from aifactory.web.app import create_multi_app
from aifactory.web.server import allowed_hosts


def _client(tmp_path: Path, host: str = "127.0.0.1", peer: str = "127.0.0.1") -> TestClient:
    app = create_multi_app(
        home=tmp_path / "haifa",
        allowed_hosts=allowed_hosts(host),
        code_watch=CodeWatch(tmp_path / "code"),
    )
    return TestClient(app, base_url="http://127.0.0.1:4700", client=(peer, 12345))


def test_get(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/terminal")
    assert envelope_problems(response.json()) == []
    assert response.json()["data"] == {
        "enabled": True,
        "cwd": str(Path.home()),
        "shell": terminal._shell(),
        "pty": True,
    }


@pytest.mark.parametrize(
    "host,peer", [("0.0.0.0", "127.0.0.1"), ("192.0.2.1", "127.0.0.1"), ("127.0.0.1", "192.0.2.2")]
)
def test_disabled(tmp_path: Path, host: str, peer: str) -> None:
    with _client(tmp_path, host, peer) as client:
        assert client.get("/api/terminal").json()["data"]["enabled"] is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell selection")
def test_shell_environment_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/custom-shell")
    assert terminal._shell() == "/bin/custom-shell"
    monkeypatch.delenv("SHELL")
    assert terminal._shell() == "/bin/sh"


@pytest.mark.parametrize("available", [False, True])
def test_windows_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, available: bool
) -> None:
    from types import SimpleNamespace

    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    # Replace the module reference, without changing the platform for Starlette/asyncio.
    monkeypatch.setattr(terminal, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setitem(sys.modules, "winpty", SimpleNamespace() if available else None)
    assert terminal._shell() == r"C:\Windows\System32\cmd.exe"
    with _client(tmp_path) as client:
        info = client.get("/api/terminal").json()["data"]
    assert info["pty"] is available
    assert info["shell"] == r"C:\Windows\System32\cmd.exe"
    monkeypatch.delenv("COMSPEC")
    assert terminal._shell() == "cmd.exe"
