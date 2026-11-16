"""The one way GIRO starts git (outside ``engine/``): a bounded runner with typed errors.

Every git process goes through ``run``. It

- stops the command after ``timeout`` seconds (``DEFAULT_TIMEOUT`` unless the caller
  passes another value, ``None`` for no limit) and raises ``GitTimeout``. Inside a probe
  budget (``procrun.budget``) the timeout is shortened to the time left, and
  ``procrun.BudgetExceeded`` is raised unchanged before git starts when too little is left;
- with ``read_only=True`` runs with ``GIT_OPTIONAL_LOCKS=0``, so a read never refreshes
  the index or takes ``index.lock`` while task runs commit in the same repository;
- returns a ``Result`` with raw bytes; ``check=True`` (or ``out``) raises
  ``GitCommandFailed`` on a non-zero exit status;
- lets ``OSError`` (no git on ``PATH``, a missing ``cwd``) through unchanged.

The exceptions keep the shapes callers already catch: ``GitError`` is a ``RuntimeError``
and a ``subprocess.SubprocessError``, ``GitCommandFailed`` is also a
``subprocess.CalledProcessError`` and ``GitTimeout`` a ``subprocess.TimeoutExpired``.

On a timeout the git process is killed (on Windows with every process it started, through
a job object) and its pipes are drained for at most ``KILL_GRACE`` seconds: a helper that
inherited them (ssh, a credential manager) cannot keep the caller waiting. On POSIX the
process stays in the caller's session, so git can still ask for credentials on the
terminal as before.

Cached queries and batch commands built on this runner live in ``aifactory.gitquery``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from aifactory import oscompat, procrun

# Seconds a git command may run unless the caller says otherwise. Generous: it is a guard
# against a hung process (a lock wait, a stuck credential helper), not a performance limit.
DEFAULT_TIMEOUT: float | None = 600.0
# Seconds to wait for the pipes after killing a timed-out command.
KILL_GRACE = 5.0
# The environment of a read: no optional locks (no index refresh, no ``index.lock``).
READ_ONLY_ENV: Mapping[str, str] = {"GIT_OPTIONAL_LOCKS": "0"}

_WINDOWS = sys.platform == "win32"


class _Unset(Enum):
    UNSET = "unset"


UNSET: Literal[_Unset.UNSET] = _Unset.UNSET
# A timeout argument: seconds, None for no limit, UNSET for ``DEFAULT_TIMEOUT``.
Timeout = float | None | Literal[_Unset.UNSET]


class GitError(RuntimeError, subprocess.SubprocessError):
    """A git command did not succeed (base class of ``GitCommandFailed`` and ``GitTimeout``)."""

    def __init__(self, message: str, argv: Sequence[str], cwd: Path | None) -> None:
        Exception.__init__(self, message)
        self.message = message
        self.argv = tuple(argv)
        self.cwd = cwd

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class Result:
    """A finished git command: ``args`` is the full argv (starting with ``git``)."""

    args: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def text(self) -> str:
        """stdout decoded as UTF-8 (invalid bytes replaced)."""
        return self.stdout.decode("utf-8", "replace")

    @property
    def out(self) -> str:
        """``text`` without surrounding whitespace."""
        return self.text.strip()

    @property
    def error(self) -> str:
        """stderr decoded and stripped."""
        return self.stderr.decode("utf-8", "replace").strip()

    def z(self) -> list[str]:
        """NUL-separated stdout (``-z`` output), empty fields dropped.

        Decoded with ``surrogateescape``: a non-UTF-8 file name survives a round trip
        through ``os.fsencode``.
        """
        return [p for p in self.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


class GitCommandFailed(GitError, subprocess.CalledProcessError):
    """git exited with a non-zero status; ``result`` holds its output."""

    def __init__(self, result: Result, cwd: Path | None) -> None:
        detail = result.error or f"exit status {result.returncode}"
        GitError.__init__(self, f"{_label(result.args)} failed: {detail}", result.args, cwd)
        self.returncode = result.returncode
        self.cmd = list(result.args)
        self.output = result.stdout.decode("utf-8", "replace")
        self.stderr = result.error
        self.result = result


class GitTimeout(GitError, subprocess.TimeoutExpired):
    """git ran longer than its timeout and was stopped."""

    def __init__(self, argv: Sequence[str], timeout: float, cwd: Path | None) -> None:
        GitError.__init__(self, f"{_label(argv)} timed out after {timeout:g}s", argv, cwd)
        self.cmd = list(argv)
        self.timeout = timeout
        self.output = None
        self.stderr = None


def run(
    cwd: Path | str | None,
    *args: str,
    input: bytes | str | None = None,
    env: Mapping[str, str] | None = None,
    read_only: bool = False,
    timeout: Timeout = UNSET,
    check: bool = False,
) -> Result:
    """Run ``git *args`` in ``cwd``.

    ``env`` is laid over ``os.environ`` (and over ``READ_ONLY_ENV`` with ``read_only``).
    ``input`` (text is UTF-8 encoded) is written to stdin; without it stdin is inherited.
    ``timeout`` defaults to ``DEFAULT_TIMEOUT``. Raises ``GitTimeout`` when it runs out,
    ``GitCommandFailed`` on a non-zero exit with ``check``.
    """
    argv = ("git", *args)
    where = None if cwd is None else Path(cwd)
    seconds = procrun.limit(DEFAULT_TIMEOUT if timeout is UNSET else timeout, argv)
    environ: dict[str, str] | None = None
    if env or read_only:
        environ = {**os.environ, **(READ_ONLY_ENV if read_only else {}), **(env or {})}
    data = input.encode("utf-8") if isinstance(input, str) else input
    with ExitStack() as stack:
        tree = stack.enter_context(oscompat.ProcessTree()) if _WINDOWS else None
        process = stack.enter_context(
            subprocess.Popen(
                argv,
                cwd=where,
                env=environ,
                stdin=None if data is None else subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=(oscompat.NEW_GROUP | tree.creationflags) if tree else 0,
            )
        )
        if tree is not None:
            tree.adopt(process)
        try:
            stdout, stderr = process.communicate(data, timeout=seconds)
        except subprocess.TimeoutExpired:
            _stop(process, tree)
            assert seconds is not None
            raise GitTimeout(argv, seconds, where) from None
    result = Result(argv, process.returncode, stdout or b"", stderr or b"")
    if check and not result.ok:
        raise GitCommandFailed(result, where)
    return result


def _stop(process: subprocess.Popen[bytes], tree: oscompat.ProcessTree | None) -> None:
    """Kill a timed-out command and drain its pipes for at most ``KILL_GRACE`` seconds."""
    if tree is not None:
        with suppress(Exception):
            tree.kill(process)
    with suppress(OSError):
        process.kill()
    try:
        process.communicate(timeout=KILL_GRACE)
    except (subprocess.TimeoutExpired, ValueError):
        # A descendant still holds the pipes: abandon them, the git process itself is gone.
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                with suppress(OSError):
                    stream.close()
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=KILL_GRACE)


def out(
    cwd: Path | str | None,
    *args: str,
    input: bytes | str | None = None,
    env: Mapping[str, str] | None = None,
    read_only: bool = False,
    timeout: Timeout = UNSET,
) -> str:
    """Stripped stdout of ``git *args``; raises ``GitCommandFailed`` on failure."""
    return run(
        cwd, *args, input=input, env=env, read_only=read_only, timeout=timeout, check=True
    ).out


def ok(
    cwd: Path | str | None,
    *args: str,
    env: Mapping[str, str] | None = None,
    read_only: bool = False,
    timeout: Timeout = UNSET,
) -> bool:
    """Whether ``git *args`` exits with status 0 (a timeout still raises ``GitTimeout``)."""
    return run(cwd, *args, env=env, read_only=read_only, timeout=timeout).ok


def try_out(
    cwd: Path | str | None,
    *args: str,
    env: Mapping[str, str] | None = None,
    read_only: bool = False,
    timeout: Timeout = UNSET,
) -> str | None:
    """Stripped stdout of ``git *args``, or None on a non-zero exit."""
    result = run(cwd, *args, env=env, read_only=read_only, timeout=timeout)
    return result.out if result.ok else None


def _label(argv: Sequence[str]) -> str:
    return " ".join(argv)
