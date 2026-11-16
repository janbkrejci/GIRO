"""oscompat.atomic_write: one file replace with a Windows retry on PermissionError."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from aifactory import oscompat


def test_text_and_bytes_replace_the_file(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "file.json"
    oscompat.atomic_write(target, "č\n")
    assert target.read_bytes() == "č\n".encode()
    oscompat.atomic_write(target, b"\x00raw", fsync=True)
    assert target.read_bytes() == b"\x00raw"
    assert os.listdir(target.parent) == ["file.json"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_mode_is_applied(tmp_path: Path) -> None:
    target = tmp_path / "tool"
    oscompat.atomic_write(target, "x")
    assert target.stat().st_mode & 0o777 == 0o600
    oscompat.atomic_write(target, "x", mode=0o755)
    assert target.stat().st_mode & 0o777 == 0o755


def _locked_replace(monkeypatch: pytest.MonkeyPatch, failures: int) -> list[tuple[str, Path]]:
    calls: list[tuple[str, Path]] = []
    real = os.replace

    def replace(source: str, target: Path) -> None:
        calls.append((source, target))
        if len(calls) <= failures:
            raise PermissionError(13, "The process cannot access the file", str(target))
        real(source, target)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    return calls


def test_held_target_is_retried_until_the_replace_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "settings.yaml"
    target.write_text("old")
    monkeypatch.setattr(oscompat, "REPLACE_DELAYS", (0.05, 0.1, 0.2))
    calls = _locked_replace(monkeypatch, failures=2)
    oscompat.atomic_write(target, "new")
    assert len(calls) == 3
    assert target.read_text() == "new"
    assert os.listdir(tmp_path) == ["settings.yaml"]


def test_exhausted_retries_raise_and_keep_the_target_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "settings.yaml"
    target.write_text("old")
    monkeypatch.setattr(oscompat, "REPLACE_DELAYS", (0.05, 0.1))
    calls = _locked_replace(monkeypatch, failures=10)
    with pytest.raises(PermissionError):
        oscompat.atomic_write(target, "new")
    assert len(calls) == 3
    assert target.read_text() == "old"
    assert os.listdir(tmp_path) == ["settings.yaml"]


def test_without_windows_delays_a_permission_error_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oscompat, "REPLACE_DELAYS", ())
    calls = _locked_replace(monkeypatch, failures=1)
    with pytest.raises(PermissionError):
        oscompat.atomic_write(tmp_path / "file", "new")
    assert len(calls) == 1
    assert os.listdir(tmp_path) == []


def test_retries_are_windows_only() -> None:
    assert bool(oscompat.REPLACE_DELAYS) == (sys.platform == "win32")
