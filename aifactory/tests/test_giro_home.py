"""GIRO home and environment alias resolution: isolated homes only."""

from __future__ import annotations

import io
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from aifactory import cli, environment, migration_guard
from aifactory.engine.utils import agent_environment, operator_env
from aifactory.environment import (
    LEGACY_NAMES,
    SUFFIXES,
    canonical,
    child_environment,
    resolve,
    value,
)
from aifactory.home import haifa_home, load_env_file
from aifactory.versioning import UpgradeError
from aifactory.web.server import APP_NAME


@pytest.fixture(name="unwarned")
def unwarned_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """The legacy warning has not been printed yet in this process."""
    monkeypatch.setattr(environment, "_warned", False)


@pytest.mark.parametrize("suffix", SUFFIXES)
def test_giro_names_are_read_and_set_shell_first(suffix: str) -> None:
    name = canonical(suffix)
    assert name == f"GIRO_{suffix}"
    assert value(suffix, {name: "shell"}) == value(name, {name: "shell"}) == "shell"
    assert value(suffix, {name: ""}) is None
    if suffix != "HOME":
        assert resolve({name: "shell"}, {name: "file"})[name] == "shell"
        assert resolve({name: ""}, {name: "file"})[name] == "file"
    assert child_environment({name: "parent"}, **{suffix: "child"})[name] == "child"


@pytest.mark.parametrize("legacy", sorted(LEGACY_NAMES))
def test_legacy_name_is_not_read_or_set(legacy: str, unwarned: None) -> None:
    new = LEGACY_NAMES[legacy]
    err = io.StringIO()
    assert value(new, {legacy: "old"}) is None
    result = resolve({legacy: "old"}, {legacy: "old-file"}, stderr=err)
    assert new not in result
    child = child_environment({}, **{new: "child"})
    assert child[new] == "child" and legacy not in child


def test_legacy_names_warn_once_per_process_without_their_values(
    unwarned: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert value("HOME", {"HAIFA_HOME": "/secret/old", "AIFACTORY_GH": "/opt/gh"}) is None
    value("HOME", {"HAIFA_HOME": "/secret/old"})
    err = capsys.readouterr().err
    assert err.count("giro: warning") == 1
    assert "HAIFA_HOME -> GIRO_HOME" in err and "AIFACTORY_GH -> GIRO_GH" in err
    assert "/secret/old" not in err and "/opt/gh" not in err


def test_env_file_legacy_names_warn_and_are_not_copied(tmp_path: Path, unwarned: None) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "env").write_text("HAIFA_LIBRARY=/old\nOPEN_KEY=1\n")
    (home / "env").chmod(0o600)
    env = {"GIRO_HOME": str(home)}
    err = io.StringIO()
    assert load_env_file(env, stderr=err) == ["OPEN_KEY"]
    assert "HAIFA_LIBRARY" not in env and "GIRO_LIBRARY" not in env
    assert "HAIFA_LIBRARY -> GIRO_LIBRARY" in err.getvalue()


def test_env_file_keeps_shell_origin_and_cannot_redirect_home(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "env").write_text("GIRO_LIBRARY=file\nGIRO_HOME=/wrong\nHOME=/wrong\n")
    (selected / "env").chmod(0o600)
    env = {"GIRO_HOME": str(selected), "GIRO_LIBRARY": "shell"}
    load_env_file(env)
    assert env["GIRO_HOME"] == str(selected)
    assert env["GIRO_LIBRARY"] == "shell"
    assert haifa_home(env) == selected


def test_worker_environment_overrides_the_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIRO_RUN_ID", "parent")
    with agent_environment({"GIRO_RUN_ID": "worker"}):
        env = operator_env()
    assert env["GIRO_RUN_ID"] == "worker"


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_default_paths_and_explicit_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    assert haifa_home({"HOME": str(tmp_path)}) == tmp_path / ".config/giro"
    assert (
        haifa_home({"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "xdg")})
        == tmp_path / "xdg/giro"
    )
    assert haifa_home({"GIRO_HOME": str(tmp_path / "new")}) == tmp_path / "new"


def test_legacy_only_and_empty_collision_do_not_write(tmp_path: Path) -> None:
    source = tmp_path / "haifa"
    source.mkdir()
    env = {"XDG_CONFIG_HOME": str(tmp_path)}
    with pytest.raises(UpgradeError, match="GIRO_HOME"):
        haifa_home(env)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["haifa"]
    (tmp_path / "giro").mkdir()
    with pytest.raises(UpgradeError) as caught:
        haifa_home(env)
    assert caught.value.code == "migration_collision"
    assert list(source.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="symlink privilege is optional on Windows")
def test_same_directory_via_symlink_is_not_a_collision(tmp_path: Path) -> None:
    (tmp_path / "haifa").mkdir()
    (tmp_path / "giro").symlink_to(tmp_path / "haifa", target_is_directory=True)
    assert haifa_home({"XDG_CONFIG_HOME": str(tmp_path)}) == tmp_path / "giro"


def test_entry_points_share_implementation_and_health_identity() -> None:
    project = Path(__file__).resolve().parents[1]
    data = tomllib.loads((project / "pyproject.toml").read_text())
    assert data["project"]["scripts"] == {
        "giro": "aifactory.cli:main",
        "factory": "aifactory.cli:main",
    }
    assert cli.build_parser().prog == "giro"
    assert "GIRO" in cli.build_parser().format_help()
    assert APP_NAME == "giro-dashboard"


def test_giro_publication_is_gated_to_new_repository() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow: dict[str, Any] = yaml.safe_load(
        (root / ".github/workflows/giro-release.yml").read_text()
    )
    job = workflow["jobs"]["release"]
    assert "github.repository == 'janbkrejci/GIRO'" in job["if"]
    assert "GIRO_RELEASE_ENABLED == 'true'" in job["if"]
    steps = "\n".join(step.get("run", "") for step in job["steps"])
    assert "dist/giro-$VERSION.zip" in steps
    assert "dist/haifa-$VERSION.zip" not in steps


def test_new_default_only_does_not_create_legacy(tmp_path: Path) -> None:
    (tmp_path / "giro").mkdir()
    assert haifa_home({"XDG_CONFIG_HOME": str(tmp_path)}) == tmp_path / "giro"
    assert not (tmp_path / "haifa").exists()


def test_agents_do_not_inherit_either_validation_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    names = [
        f"{prefix}_VALIDATE_{kind}" for prefix in ("GIRO", "HAIFA") for kind in ("FAKE", "HIDDEN")
    ]
    for name in names:
        monkeypatch.setenv(name, "private-test-path")
    env = operator_env()
    assert all(name not in env for name in names)


def test_version_is_giro_and_does_not_open_data(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aifactory.home

    def forbidden(*args: Any) -> Path:
        raise AssertionError("version must not open the data home")

    monkeypatch.setattr(aifactory.home, "haifa_home", forbidden)
    with pytest.raises(SystemExit) as exited:
        cli.main(["--version"])
    assert exited.value.code == 0
    assert capsys.readouterr().out.startswith("GIRO 1.")


@pytest.mark.skipif(sys.platform == "win32", reason="symlink privilege is optional on Windows")
def test_migration_locks_share_canonical_home_identity(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(source, target_is_directory=True)
    with migration_guard.migration_lock(source):
        with pytest.raises(UpgradeError) as caught:
            with migration_guard.migration_lock(alias):
                raise AssertionError("second lock unexpectedly acquired")
    assert caught.value.code == "update_busy"


def test_invalid_library_url_is_refused() -> None:
    from aifactory.library.share import default_library_url
    from aifactory.library.store import LibraryStoreError

    with pytest.raises(LibraryStoreError):
        default_library_url({"GIRO_LIBRARY_URL": " "})


def test_legacy_names_are_spelled_only_in_environment() -> None:
    src = Path(cli.__file__).parent
    offenders = [
        str(path.relative_to(src))
        for path in src.rglob("*.py")
        if path != src / "environment.py"
        and ("HAIFA_" in (text := path.read_text(encoding="utf-8")) or "AIFACTORY_" in text)
    ]
    assert offenders == []
