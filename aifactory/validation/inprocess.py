"""Run ``validation.worker.main`` in this process, isolated like a worker subprocess.

The validation tests use it for commands over the fake harness: a step no longer
pays for a new interpreter and a fresh CLI import. During the call ``os.environ`` is
exactly the step's environment and the working directory is the step's; afterwards both
are restored, and so is the global state the worker installs (the fake harness in
``agents.INTERFACES`` and on the adapter modules, the hidden test wrapper of the test
step and the command prefix of chain members and dashboard runs).
"""

from __future__ import annotations

import io
import os
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType
from typing import Any

_MISSING = object()
ADAPTER_ATTRS = ("run", "resolve_model")


def _adapter_modules() -> list[ModuleType]:
    from aifactory import harness

    return [harness.load(name) for name in harness.HARNESSES]


@contextmanager
def _restored_globals() -> Iterator[None]:
    from aifactory import harness
    from aifactory.run import members
    from aifactory.testing import executor

    agents = harness.install()
    interfaces = dict(agents.INTERFACES)
    modules = _adapter_modules()
    attrs = [(m, a, m.__dict__.get(a, _MISSING)) for m in modules for a in ADAPTER_ATTRS]
    execute = executor.execute
    command = members._command
    try:
        yield
    finally:
        agents.INTERFACES.clear()
        agents.INTERFACES.update(interfaces)
        for module, name, value in attrs:
            if value is _MISSING:
                module.__dict__.pop(name, None)
            else:
                setattr(module, name, value)
        executor.execute = execute
        members._command = command


@contextmanager
def _isolated(env: Mapping[str, str], cwd: Path) -> Iterator[None]:
    saved_env = dict(os.environ)
    saved_cwd = os.getcwd()
    os.environ.clear()
    os.environ.update(env)
    try:
        os.chdir(cwd)
        with _restored_globals():
            yield
    finally:
        os.chdir(saved_cwd)
        os.environ.clear()
        os.environ.update(saved_env)


def _exit_code(value: Any) -> int:
    if value is None:
        return 0
    return value if isinstance(value, int) else 1


def call(argv: list[str], env: Mapping[str, str], cwd: Path) -> tuple[int, str, str]:
    """``python -m validation.worker *argv`` in this process: exit code, stdout, stderr."""
    from validation import worker

    out, err = io.StringIO(), io.StringIO()
    with _isolated(env, cwd), redirect_stdout(out), redirect_stderr(err):
        try:
            code = worker.main(argv)
        except SystemExit as exc:
            code = _exit_code(exc.code)
        except Exception:  # a crash of the step is its result, like a worker's traceback
            traceback.print_exc()
            code = 1
    return code, out.getvalue(), err.getvalue()
