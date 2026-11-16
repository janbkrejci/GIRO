"""Git repos and a client for the multi-repo dashboard tests (no model, no network)."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

import repo_templates
from aifactory.web.app import create_multi_app
from gitutil import commit_all as commit_all
from gitutil import git as git
from gitutil import write as write

BASE = "http://127.0.0.1:4700"
CONFIG = "base: main\ndocs_root: .\n"
AGENTS = "defaults:\n  harness: claude\nagents:\n  - name: builder\n    purpose: Build.\n"
SSSF_ROSTER = "adws/adw_sssf_config/sssf.config.yaml"


def _clear_readonly(func: Callable[[str], Any], path: str, _exc: object) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


def rmtree(path: Path) -> None:
    """``shutil.rmtree``; on Windows it clears the read-only bit git objects have first."""
    if sys.platform == "win32":
        shutil.rmtree(path, onerror=_clear_readonly)
    else:
        shutil.rmtree(path)


def symlink(link: Path, target: Path) -> None:
    """``link.symlink_to(target)``; on Windows skip the test when symlinks need a privilege."""
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as error:
        if sys.platform == "win32":
            pytest.skip(f"cannot create symlinks here: {error}")
        raise


def init_repo(path: Path, *, commit: bool = True) -> Path:
    """A git repo on ``main`` at ``path`` (with one commit unless ``commit`` is False)."""
    repo_templates.build(path, f"multi-{commit}", lambda root: _build_repo(root, commit))
    return path.resolve()


def _build_repo(path: Path, commit: bool) -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    if commit:
        write(path, "README.md", "readme\n")
        commit_all(path, "readme")


def install(root: Path) -> None:
    """Commit config and agents: the ``installed`` state."""
    write(root, ".factory/config.yaml", CONFIG)
    write(root, ".factory/agents.yaml", AGENTS)
    commit_all(root, "install")


def multi_client(home: Path, tmp_path: Path, **kwargs: Any) -> TestClient:
    app = create_multi_app(home=home, static_dir=tmp_path / "nostatic", **kwargs)
    return TestClient(app, base_url=BASE)


def register(client: TestClient, path: Path) -> dict[str, Any]:
    """Register ``path`` in the client's registry without installing factory (what
    ``POST /api/repos`` did before it installed); returns the ``repo_status`` item."""
    from aifactory.repo_vet import register_repo
    from aifactory.web.repos import repo_status

    entry, _created, _warnings = register_repo(client.app.state.registry, str(path))  # type: ignore[attr-defined]
    return repo_status(entry)
