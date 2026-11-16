from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifactory.versioning import UpgradeError
from aifactory.web import updates
from aifactory.web.app import create_multi_app
from aifactory.web.server import RESTART_ENV, restart_command


def release(version: str = "0.2.0") -> dict[str, Any]:
    return {
        "tag_name": f"v{version}",
        "assets": [
            {
                "name": f"giro-{version}.zip",
                "browser_download_url": f"https://github.com/test/giro/releases/download/v{version}/giro-{version}.zip",
            }
        ],
    }


def test_cached_check_and_manual_retry() -> None:
    calls: list[str] = []

    def fetch(repo: str) -> dict[str, Any]:
        calls.append(repo)
        return release()

    source = updates.Updates(repository="test/giro", current="0.1.0", fetch=fetch)
    assert source.check()["status"] == "available"
    assert source.check()["status"] == "available"
    assert len(calls) == 1
    source.check(fresh=True)
    assert len(calls) == 2
    source = updates.Updates(repository="test/giro", current="0.2.0", fetch=lambda _: release())
    assert source.check()["status"] == "current"


def test_error_can_be_retried_and_foreign_asset_is_rejected() -> None:
    data = release()
    data["assets"][0]["browser_download_url"] = "https://other.example/install.zip"
    source = updates.Updates(repository="test/giro", current="0.1.0", fetch=lambda _: data)
    assert source.check()["status"] == "error"
    data["assets"][0]["browser_download_url"] = release()["assets"][0]["browser_download_url"]
    assert source.check(fresh=True)["status"] == "available"


def test_install_verifies_target_restarts_and_blocks_other_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    restarts: list[str] = []
    source = updates.Updates(repository="test/giro", current="0.1.0", fetch=lambda _: release())
    monkeypatch.setattr(updates, "editable_install", lambda: False)

    def install(bundle: str, **kw: Any) -> SimpleNamespace:
        assert bundle == release()["assets"][0]["browser_download_url"]
        assert kw["expected_version"] == "0.2.0"
        return SimpleNamespace(installed=True, handed_off=False)

    monkeypatch.setattr(updates, "run_upgrade", install)
    app = create_multi_app(home=tmp_path, restart=lambda: restarts.append("restart"))
    app.state.updates = source
    with TestClient(app, base_url="http://127.0.0.1:4700") as client:
        assert client.get("/api/updates").json()["data"]["status"] == "available"
        assert client.post("/api/updates/install").status_code == 202
        assert restarts == ["restart"]
        assert (
            client.post("/api/machine/harnesses", json={}).json()["error"]["code"] == "update_busy"
        )
        assert client.post("/api/restart").json()["error"]["code"] == "update_busy"
        assert restarts == ["restart"]


def test_failed_install_preserves_dashboard_without_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = updates.Updates(repository="test/giro", current="0.1.0", fetch=lambda _: release())
    monkeypatch.setattr(updates, "editable_install", lambda: False)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise UpgradeError("checksum_mismatch", "bad checksum")

    monkeypatch.setattr(updates, "run_upgrade", fail)
    source.begin()
    restarts: list[str] = []
    source.install(lambda: restarts.append("restart"))
    assert not source.installing and restarts == []
    assert source.check()["status"] == "error"


def test_windows_handoff_leaves_instead_of_restarting(monkeypatch: pytest.MonkeyPatch) -> None:
    source = updates.Updates(repository="test/giro", current="0.1.0", fetch=lambda _: release())
    monkeypatch.setattr(updates, "editable_install", lambda: False)
    seen: dict[str, Any] = {}

    def hand_off(bundle: str, **kw: Any) -> SimpleNamespace:
        seen.update(kw)
        return SimpleNamespace(installed=False, handed_off=True)

    monkeypatch.setattr(updates, "run_upgrade", hand_off)
    source.begin()
    events: list[str] = []
    source.install(lambda: events.append("restart"), leave=lambda: events.append("leave"))
    assert events == ["leave"] and source.installing
    assert seen["restart"] == restart_command()
    assert seen["environ"][RESTART_ENV] == "1"


def test_active_runs_prevent_update(tmp_path: Path) -> None:
    source = updates.Updates(repository="test/giro", current="0.1.0", fetch=lambda _: release())
    app = create_multi_app(home=tmp_path)
    app.state.updates = source
    app.state.launcher = SimpleNamespace(running=lambda: [object()])
    with TestClient(app, base_url="http://127.0.0.1:4700") as client:
        response = client.post("/api/updates/install")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "runs_active"
        assert response.json()["error"]["message"].startswith("Aktualizace počká, běží 1 task.")
        assert not source.installing


def test_run_started_during_release_check_prevents_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = updates.Updates(repository="test/giro", current="0.1.0", fetch=lambda _: release())
    monkeypatch.setattr(updates, "editable_install", lambda: False)
    results = iter([[], [object()]])
    app = create_multi_app(home=tmp_path)
    app.state.updates = source
    app.state.launcher = SimpleNamespace(running=lambda: next(results))
    with TestClient(app, base_url="http://127.0.0.1:4700") as client:
        assert client.post("/api/updates/install").json()["error"]["code"] == "runs_active"
    assert not source.installing
    assert source.check()["status"] == "available"


@pytest.mark.parametrize(
    ("count", "phrase"),
    [(2, "běží 2 tasky."), (4, "běží 4 tasky."), (5, "běží 5 tasků.")],
)
def test_refusal_names_active_runs_with_czech_plural(
    tmp_path: Path, count: int, phrase: str
) -> None:
    source = updates.Updates(repository="test/giro", current="0.1.0", fetch=lambda _: release())
    app = create_multi_app(home=tmp_path)
    app.state.updates = source
    app.state.launcher = SimpleNamespace(running=lambda: [object()] * count)
    with TestClient(app, base_url="http://127.0.0.1:4700") as client:
        response = client.post("/api/updates/install")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "runs_active"
    assert error["message"].startswith(f"Aktualizace počká, {phrase}")
    assert not source.installing


def test_runs_seen_in_overview_prevent_update(tmp_path: Path) -> None:
    class StubOverview:
        async def collect(self) -> tuple[dict[str, Any], None]:
            return {"totals": {"running": 3}}, None

    source = updates.Updates(repository="test/giro", current="0.1.0", fetch=lambda _: release())
    app = create_multi_app(home=tmp_path)
    app.state.updates = source
    app.state.launcher = SimpleNamespace(running=lambda: [])
    with TestClient(app, base_url="http://127.0.0.1:4700") as client:
        app.state.overview = StubOverview()
        response = client.post("/api/updates/install")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "runs_active"
    assert "běží 3 tasky" in error["message"]
    assert not source.installing


def giro_release(version: str = "1.0.1") -> dict[str, Any]:
    return {
        "tag_name": f"v{version}",
        "assets": [
            {
                "name": f"giro-{version}.zip",
                "browser_download_url": f"https://github.com/janbkrejci/GIRO/releases/download/v{version}/giro-{version}.zip",
            }
        ],
    }


@pytest.mark.parametrize(
    "failure", ["network", "missing", "draft", "legacy_zip", "foreign_url", "duplicate_asset"]
)
def test_wrong_or_unavailable_release_is_rejected_and_can_retry(
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = giro_release()
    if failure == "missing":
        data["assets"] = []
    elif failure == "draft":
        data["draft"] = True
    elif failure == "legacy_zip":
        data["assets"][0]["name"] = "haifa-1.0.1.zip"
    elif failure == "foreign_url":
        data["assets"][0]["browser_download_url"] = (
            "https://github.com/janbkrejci/HAIFA/releases/download/v1.0.1/giro-1.0.1.zip"
        )
    elif failure == "duplicate_asset":
        data["assets"] *= 2

    def fetch(repo: str) -> dict[str, Any]:
        assert repo == "janbkrejci/GIRO"
        if failure == "network":
            raise OSError("hosting offline")
        return data

    monkeypatch.setattr(updates, "editable_install", lambda: False)
    source = updates.Updates(repository="janbkrejci/GIRO", current="1.0.0", fetch=fetch)
    with pytest.raises(UpgradeError, match="."):
        source.begin()
    assert not source.installing
    assert source.check()["status"] == "error"
    source.fetch = lambda _: giro_release()
    assert source.begin()["status"] == "installing"
    with pytest.raises(UpgradeError) as busy:
        source.begin()
    assert busy.value.code == "update_busy"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("", "janbkrejci/GIRO"), ("company/giro", "company/giro")],
)
def test_update_repository_follows_giro_update_repo(
    monkeypatch: pytest.MonkeyPatch, configured: str, expected: str
) -> None:
    monkeypatch.setenv("GIRO_UPDATE_REPO", configured)
    source = updates.Updates(current="1.0.1", fetch=lambda repo: giro_release("1.0.2"))
    assert source.repository == expected
