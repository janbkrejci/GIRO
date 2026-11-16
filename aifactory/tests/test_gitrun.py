"""The git runner: timeouts with typed errors, read-only environment, no other git caller."""

from __future__ import annotations

import ast
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aifactory import gitrun, procrun
from aifactory.providers import ProviderError, github
from fake_exe import make_executable

SRC = Path(gitrun.__file__).parent

# A git that starts a helper keeping the inherited pipes open, then hangs.
_HANGING = """\
#!/usr/bin/env python3
import subprocess, sys, time
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
time.sleep(60)
"""


def _fake_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / name
    script.write_text(_HANGING.replace("python3", Path(sys.executable).as_posix(), 1))
    make_executable(script)
    monkeypatch.setenv("PATH", str(bin_dir), prepend=__import__("os").pathsep)


def test_out_and_failure(tmp_path: Path) -> None:
    gitrun.run(tmp_path, "init", "-q", check=True)
    assert gitrun.out(tmp_path, "rev-parse", "--is-inside-work-tree") == "true"
    assert gitrun.try_out(tmp_path, "rev-parse", "--verify", "-q", "HEAD") is None
    with pytest.raises(gitrun.GitCommandFailed) as info:
        gitrun.out(tmp_path, "rev-parse", "--verify", "HEAD")
    error = info.value
    assert error.returncode != 0 and error.cwd == tmp_path
    assert isinstance(error, RuntimeError) and isinstance(error, subprocess.CalledProcessError)
    assert "git rev-parse --verify HEAD failed" in str(error)


def test_read_only_sets_no_optional_locks(tmp_path: Path) -> None:
    gitrun.run(tmp_path, "init", "-q", check=True)
    shown = gitrun.out(tmp_path, "var", "GIT_EDITOR", env={"GIT_EDITOR": "probe"})
    assert shown == "probe"
    env = gitrun.run(tmp_path, "-c", "alias.e=!env", "e", read_only=True, check=True).text
    assert "GIT_OPTIONAL_LOCKS=0" in env.splitlines()
    plain = gitrun.run(tmp_path, "-c", "alias.e=!env", "e", check=True).text
    assert "GIT_OPTIONAL_LOCKS=0" not in plain.splitlines()


def test_hanging_git_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_on_path(tmp_path, monkeypatch, "git")
    started = time.monotonic()
    with pytest.raises(gitrun.GitTimeout) as info:
        gitrun.run(tmp_path, "status", timeout=1.0)
    assert time.monotonic() - started < 1.0 + 2 * gitrun.KILL_GRACE
    assert isinstance(info.value, subprocess.TimeoutExpired)
    assert isinstance(info.value, gitrun.GitError)
    assert info.value.timeout == 1.0


def test_default_timeout_applies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller that passes no timeout still gets one."""
    _fake_on_path(tmp_path, monkeypatch, "git")
    monkeypatch.setattr(gitrun, "DEFAULT_TIMEOUT", 0.5)
    monkeypatch.setattr(gitrun, "KILL_GRACE", 0.5)
    with pytest.raises(gitrun.GitTimeout):
        gitrun.ok(tmp_path, "status")


def test_budget_shortens_and_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_on_path(tmp_path, monkeypatch, "git")
    monkeypatch.setattr(gitrun, "KILL_GRACE", 0.5)
    with procrun.budget(1.5), pytest.raises(gitrun.GitTimeout) as info:
        gitrun.run(tmp_path, "status")
    assert info.value.timeout <= 1.5
    with procrun.budget(0.1), pytest.raises(procrun.BudgetExceeded):
        gitrun.run(tmp_path, "status")


def test_hanging_gh_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_on_path(tmp_path, monkeypatch, "gh")
    monkeypatch.delenv("GIRO_GH", raising=False)
    monkeypatch.setattr(github, "GH_TIMEOUT", 1)
    started = time.monotonic()
    with pytest.raises(ProviderError) as info:
        github.GhCli(tmp_path).run("pr", "view")
    assert info.value.code == "gh_timeout"
    assert time.monotonic() - started < 30


def _git_subprocess_calls(path: Path) -> list[int]:
    """Lines where ``path`` starts git through ``subprocess`` or ``procrun`` directly."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        owner = (
            func.value.id
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            else None
        )
        if owner not in ("subprocess", "procrun"):
            continue
        first = node.args[0]
        if isinstance(first, ast.List) and first.elts:
            head = first.elts[0]
            if isinstance(head, ast.Constant) and head.value == "git":
                lines.append(node.lineno)
    return lines


def test_only_the_runner_starts_git() -> None:
    offenders = {
        str(path.relative_to(SRC)): found
        for path in SRC.rglob("*.py")
        if "engine" not in path.relative_to(SRC).parts
        and path.name != "gitrun.py"
        and (found := _git_subprocess_calls(path))
    }
    assert offenders == {}


def test_engine_git_helpers_use_the_runner() -> None:
    engine = SRC / "engine"
    assert {
        name: found
        for name in ("git_helper.py", "permissions.py")
        if (found := _git_subprocess_calls(engine / name))
    } == {}
