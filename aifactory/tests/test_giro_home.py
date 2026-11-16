"""GIRO home and environment alias resolution: isolated homes only."""

from __future__ import annotations

import io
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from aifactory import cli, migration_guard
from aifactory.engine.utils import agent_environment, operator_env
from aifactory.environment import SUFFIXES, aliased, child_environment, names, resolve, value
from aifactory.home import haifa_home, load_env_file
from aifactory.versioning import UpgradeError
from aifactory.web.server import APP_NAME


@pytest.mark.parametrize("suffix", SUFFIXES)
def test_alias_precedence_and_child_normalization(suffix: str) -> None:
    new, old = names(suffix)
    err = io.StringIO()
    result = resolve({new: "new", old: "legacy"}, stderr=err)
    assert result[new] == result[old] == "new"
    assert new in err.getvalue() and old in err.getvalue()
    assert "legacy" not in err.getvalue()
    assert value(suffix, {new: "", old: "legacy"}) == "legacy"
    if suffix != "HOME":
        result = resolve({old: "shell"}, {new: "file"})
        assert result[new] == result[old] == "shell"
        result = resolve({new: ""}, {new: "file", old: "old-file"}, stderr=err)
        assert result[new] == result[old] == "file"
    result = child_environment({new: "parent", old: "old-parent"}, **{old: "child"})
    assert result[new] == result[old] == "child"


def test_env_file_keeps_shell_origin_and_cannot_redirect_home(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "env").write_text(
        "GIRO_LIBRARY=file\nGIRO_HOME=/wrong\nHAIFA_HOME=/wrong\nHOME=/wrong\n"
    )
    (selected / "env").chmod(0o600)
    env = {"HAIFA_HOME": str(selected), "HAIFA_LIBRARY": "shell"}
    load_env_file(env)
    assert env["GIRO_HOME"] == env["HAIFA_HOME"] == str(selected)
    assert env["GIRO_LIBRARY"] == env["HAIFA_LIBRARY"] == "shell"
    assert haifa_home(env) == selected


def test_worker_environment_overrides_both_run_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIRO_RUN_ID", "parent")
    with agent_environment({"HAIFA_RUN_ID": "worker"}):
        env = operator_env()
    assert env["GIRO_RUN_ID"] == env["HAIFA_RUN_ID"] == "worker"


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_default_paths_and_explicit_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    assert haifa_home({"HOME": str(tmp_path)}) == tmp_path / ".config/giro"
    assert (
        haifa_home({"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "xdg")})
        == tmp_path / "xdg/giro"
    )
    assert haifa_home({"HAIFA_HOME": str(tmp_path / "legacy")}) == tmp_path / "legacy"
    assert haifa_home({"GIRO_HOME": str(tmp_path / "new"), "HAIFA_HOME": "old"}) == tmp_path / "new"


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


def test_invalid_new_library_url_does_not_fall_back_to_legacy() -> None:
    from aifactory.library.share import default_library_url
    from aifactory.library.store import LibraryStoreError

    with pytest.raises(LibraryStoreError):
        default_library_url(
            {"GIRO_LIBRARY_URL": " ", "HAIFA_LIBRARY_URL": "https://valid.example/library"}
        )


def test_legacy_names_are_spelled_only_in_environment() -> None:
    src = Path(cli.__file__).parent
    offenders = [
        str(path.relative_to(src))
        for path in src.rglob("*.py")
        if path != src / "environment.py"
        and ("HAIFA_" in (text := path.read_text(encoding="utf-8")) or "AIFACTORY_" in text)
    ]
    assert offenders == []


def test_every_legacy_spelling_still_works() -> None:
    assert names("HOME") == ("GIRO_HOME", "HAIFA_HOME")
    assert names("HAIFA_RUN_ID") == ("GIRO_RUN_ID", "HAIFA_RUN_ID")
    assert names("GH") == ("GIRO_GH", "AIFACTORY_GH")
    assert names("AIFACTORY_AZ") == ("GIRO_AZ", "AIFACTORY_AZ")
    assert names("PATH") == ("PATH",)
    assert value("GIRO_GH", {"AIFACTORY_GH": "/opt/gh"}) == "/opt/gh"
    assert value("RUN_ID", {"HAIFA_RUN_ID": "r1"}) == "r1"
    assert aliased({"GIRO_RUN_ID": "r1", "PATH": "/bin"}) == {
        "GIRO_RUN_ID": "r1",
        "HAIFA_RUN_ID": "r1",
        "PATH": "/bin",
    }
    child = child_environment({"AIFACTORY_AZ": "/opt/az"}, GIRO_RUN_ID="r2")
    assert child["GIRO_AZ"] == child["AIFACTORY_AZ"] == "/opt/az"
    assert child["GIRO_RUN_ID"] == child["HAIFA_RUN_ID"] == "r2"
