"""GIRO's machine home: explicit GIRO_HOME, else the default.

Defaults use XDG_CONFIG_HOME/giro or ~/.config/giro (Path.home on Windows).
Environment files cannot redirect the already selected home.
"""

from __future__ import annotations

import io
import os
import stat
import sys
import threading
from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, TextIO

from dotenv.main import DotEnv
from dotenv.variables import parse_variables

from aifactory import oscompat

HOME_ENV = "GIRO_HOME"
XDG_ENV = "XDG_CONFIG_HOME"
ENV_FILE = "env"


def _env(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name)
    return value if value else None


def giro_home(environ: Mapping[str, str] | None = None) -> Path:
    """Explicit GIRO_HOME, otherwise ``giro`` under XDG_CONFIG_HOME or ~/.config."""
    env = os.environ if environ is None else environ
    from aifactory.environment import value

    explicit = value("HOME", env)
    if explicit is not None:
        return Path(explicit).expanduser()
    xdg = _env(env, XDG_ENV)
    home = _env(env, "HOME")
    base = Path(home) if home is not None else Path.home()
    parent = Path(xdg).expanduser() if xdg is not None else base / ".config"
    return parent / "giro"


def logs_dir(environ: Mapping[str, str] | None = None) -> Path:
    """``<home>/logs``; created (mode 0700) when missing."""
    path = giro_home(environ) / "logs"
    if not path.is_dir():
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    return path


def create_private(path: Path) -> BinaryIO:
    """A new file at `path` with mode 0600, open for binary writing; an existing file fails."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        oscompat.fchmod(fd, 0o600)
    except OSError:
        os.close(fd)
        raise
    return os.fdopen(fd, "wb")


def env_file(environ: Mapping[str, str] | None = None) -> Path:
    """``<home>/env``: KEY=VALUE lines loaded into the environment at ``factory`` start."""
    return giro_home(environ) / ENV_FILE


def load_env_file(
    environ: MutableMapping[str, str] | None = None, stderr: TextIO | None = None
) -> list[str]:
    """Load ``<home>/env`` into `environ` (default ``os.environ``) if the file exists.

    Variables `environ` already has are kept. A file whose mode is not 0600 is still
    loaded, with an ``env_file_mode`` warning on `stderr` (not on Windows: no modes there).
    Returns the names it set.
    """
    env: MutableMapping[str, str] = os.environ if environ is None else environ
    path = env_file(env)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return []
    if not path.is_file():
        return []
    if mode != 0o600 and sys.platform != "win32":  # Windows has no POSIX modes to check
        out = sys.stderr if stderr is None else stderr
        print(
            f"factory: warning: env_file_mode: {path} has mode {mode:04o}, expected 0600",
            file=out,
        )
    with path.open(encoding="utf-8") as stream:
        return _load_env_stream(env, stream, stderr=stderr)


def _load_env_stream(
    env: MutableMapping[str, str], stream: TextIO, *, stderr: TextIO | None = None
) -> list[str]:
    from aifactory.environment import resolve

    before = dict(env)
    settings: dict[str, str | None] = {}
    scope: dict[str, str | None] = dict(before)
    for key, raw in DotEnv(None, stream=stream).parse():
        item = (
            None if raw is None else "".join(part.resolve(scope) for part in parse_variables(raw))
        )
        settings[key] = scope[key] = item
    resolved = resolve(before, settings, stderr=stderr)
    env.update(resolved)
    return [key for key in settings if key not in before and key in resolved]


class _EnvFileWatch:
    def __init__(self) -> None:
        self.path = env_file().absolute()
        self.stamp: tuple[int, int, int, int] | None = None
        self.loaded: dict[str, str] = {}
        self.shadowed: dict[str, str] = {}
        self.lock = threading.Lock()

    def _stamp(self) -> tuple[int, int, int, int] | None:
        try:
            info = self.path.stat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode):
            return None
        return info.st_mtime_ns, info.st_size, info.st_ino, info.st_mode

    def _process_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        for key, item in self.loaded.items():
            if env.get(key) == item:
                if key in self.shadowed:
                    env[key] = self.shadowed[key]
                else:
                    del env[key]
        return env

    def process_environment(self) -> dict[str, str]:
        with self.lock:
            return self._process_environment()

    def reload(self, *, initial: bool = False) -> None:
        with self.lock:
            try:
                stamp = self._stamp()
            except OSError:
                return
            if not initial and stamp == self.stamp:
                return
            try:
                before = self._process_environment()
                after = dict(before)
                # Keep startup diagnostics. Reload is silent and a failed read is retried.
                if stamp is not None:
                    if initial:
                        load_env_file(after)
                    else:
                        with self.path.open(encoding="utf-8") as stream:
                            _load_env_stream(after, stream, stderr=io.StringIO())
                        if stamp != self._stamp() or any(
                            not key or "=" in key or "\0" in key or "\0" in item
                            for key, item in after.items()
                        ):
                            return
            except (OSError, UnicodeError):
                if initial:
                    raise
                return
            for key in self.loaded:
                if key in after:
                    os.environ[key] = after[key]
                else:
                    os.environ.pop(key, None)
            self.loaded = {key: item for key, item in after.items() if before.get(key) != item}
            self.shadowed = {key: before[key] for key in self.loaded if key in before}
            os.environ.update(self.loaded)
            self.stamp = stamp


_watch: _EnvFileWatch | None = None


@contextmanager
def watch_env_file() -> Iterator[None]:
    """Load the env file at CLI startup and track its values for later reloads."""
    global _watch
    previous = _watch
    watch = _EnvFileWatch()
    watch.reload(initial=True)
    _watch = watch
    try:
        yield
    finally:
        _watch = previous


def reload_env_file() -> None:
    if (watch := _watch) is not None:
        watch.reload()


def giro_process_environment() -> dict[str, str]:
    """Environment for a new GIRO CLI process, which loads its own env file."""
    watch = _watch
    return dict(os.environ) if watch is None else watch.process_environment()
