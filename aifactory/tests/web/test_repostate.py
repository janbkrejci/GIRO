"""The shared repository snapshot of the dashboard (``web.repostate``)."""

from __future__ import annotations

import threading
from pathlib import Path

from overview_fixture import make_installed_repo

from aifactory.web.repostate import RepoStates


def test_nested_memo_does_not_deadlock(tmp_path: Path) -> None:
    root = make_installed_repo(tmp_path / "repo")
    snap = RepoStates().get(root)
    result: list[object] = []

    def nested() -> None:
        # The overview's config view is memoized and reads the memoized config status.
        result.append(snap.memo("outer", lambda: snap.config_status().changes))

    worker = threading.Thread(target=nested, daemon=True)
    worker.start()
    worker.join(timeout=30)
    assert not worker.is_alive(), "memo deadlocked on a nested call"
    assert result == [[]]
