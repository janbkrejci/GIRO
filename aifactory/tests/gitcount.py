"""Count the git processes a piece of code starts (``GIT_TRACE2_EVENT``).

Every git process, including one git starts itself, writes a ``start`` event to the file
``GIT_TRACE2_EVENT`` names. ``count_git`` points the variable at a temporary file for
the duration of the block; ``GitCount.total`` is the number of processes and
``GitCount.commands`` their sub-commands (``rev-parse``, ``status``, ...).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest


@dataclass
class GitCount:
    trace: Path
    commands: Counter[str] = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(self.commands.values())

    def read(self) -> None:
        self.commands.clear()
        if not self.trace.exists():
            return
        for line in self.trace.read_text(encoding="utf-8", errors="replace").splitlines():
            event = json.loads(line)
            if event.get("event") == "start":
                self.commands[_command(event.get("argv", [])[1:])] += 1


@contextmanager
def count_git(monkeypatch: pytest.MonkeyPatch, trace: Path) -> Iterator[GitCount]:
    """Count the git processes started inside the block (also by child processes)."""
    trace.unlink(missing_ok=True)
    count = GitCount(trace)
    with monkeypatch.context() as patch:
        patch.setenv("GIT_TRACE2_EVENT", str(trace))
        yield count
    count.read()


def _command(args: list[str]) -> str:
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in ("-c", "-C"):
            skip = True
        elif not arg.startswith("-"):
            return str(arg)
    return "?"
