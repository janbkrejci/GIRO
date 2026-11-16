"""Record (and optionally answer) the git commands code starts through ``aifactory.gitrun``.

``spy_git(monkeypatch)`` replaces ``gitrun.run`` for the test and returns the list of
``GitCall`` it fills: the argv (starting with ``git``) and the environment the command
gets (None when it inherits the caller's unchanged). With ``answer`` a call may be
answered without starting git: return a ``gitrun.Result`` or None to run the real git.
The thread's time budget is applied first, exactly as the runner does.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from aifactory import gitrun, procrun


@dataclass
class GitCall:
    argv: list[str]
    env: dict[str, str] | None
    cwd: Path | None


def result(argv: list[str], stdout: str = "", returncode: int = 0) -> gitrun.Result:
    return gitrun.Result(tuple(argv), returncode, stdout.encode(), b"")


def spy_git(
    monkeypatch: pytest.MonkeyPatch,
    answer: Callable[[GitCall], gitrun.Result | None] | None = None,
) -> list[GitCall]:
    calls: list[GitCall] = []
    real = gitrun.run

    def run(cwd: Any, *args: str, **kwargs: Any) -> gitrun.Result:
        argv = ["git", *args]
        procrun.limit(None, argv)
        env = kwargs.get("env")
        merged: dict[str, str] | None = None
        if env or kwargs.get("read_only"):
            ro = gitrun.READ_ONLY_ENV if kwargs.get("read_only") else {}
            merged = {**os.environ, **ro, **(env or {})}
        call = GitCall(argv, merged, None if cwd is None else Path(cwd))
        calls.append(call)
        if answer is not None:
            answered = answer(call)
            if answered is not None:
                return answered
        return real(cwd, *args, **kwargs)

    monkeypatch.setattr(gitrun, "run", run)
    return calls
