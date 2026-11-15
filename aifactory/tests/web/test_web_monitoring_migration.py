"""Prepared migrations must not start background writes while health is validated."""

import asyncio
import json
import threading
from pathlib import Path

import pytest

from aifactory import migration_guard
from aifactory.upgrade import UpgradeError
from aifactory.web import monitoring


def test_startup_check_waits_until_migration_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAIFA_HOME", str(tmp_path))
    marker = tmp_path / migration_guard.MARKER
    marker.write_text(json.dumps({"state": "prepared"}))
    calls: list[str] = []

    async def scenario() -> None:
        task = asyncio.create_task(monitoring.once(lambda: calls.append("checked")))
        await asyncio.sleep(0.05)
        assert calls == []
        marker.unlink()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
    assert calls == ["checked"]


@pytest.mark.parametrize("committed", [False, True])
def test_slow_migration_validation_does_not_block_health_loop(
    monkeypatch: pytest.MonkeyPatch, committed: bool
) -> None:
    started = threading.Event()
    release = threading.Event()
    blocked_loop = threading.Event()
    calls: list[str] = []

    def slow_check(home: Path, *, writing: bool = False) -> None:
        assert writing
        started.set()
        if not release.wait(2):
            blocked_loop.set()
        if not committed:
            raise UpgradeError("migration_pending", "The coordinator has not committed yet")

    monkeypatch.setattr(monitoring, "check_home", slow_check)

    async def scenario() -> None:
        task = asyncio.create_task(monitoring.once(lambda: calls.append("checked")))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            assert not blocked_loop.is_set(), "health requests must run during disk validation"
            assert calls == []
            release.set()
            if committed:
                await asyncio.wait_for(task, timeout=2)
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert calls == (["checked"] if committed else [])
