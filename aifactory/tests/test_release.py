from pathlib import Path

import pytest
from bundle.build import version
from bundle.release import stamp


def test_ci_version_and_repository_are_embedded_together(tmp_path: Path) -> None:
    package = tmp_path / "src/aifactory"
    (package / "web").mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.1.0"\n')
    (package / "web/updates.py").write_text(
        'UPDATE_REPOSITORY = "old/repo"\nMIGRATION_REPOSITORY = "janbkrejci/GIRO"\n'
    )
    assert stamp(42, "example/haifa", tmp_path) == "0.1.42"
    assert version(tmp_path) == "0.1.42"
    assert '"example/haifa"' in (package / "web/updates.py").read_text()
    assert 'MIGRATION_REPOSITORY = "janbkrejci/GIRO"' in (package / "web/updates.py").read_text()
    assert stamp(43, "example/haifa", tmp_path) == "0.1.43"


def test_ci_rejects_invalid_release_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        stamp(0, "example/haifa", tmp_path)
    with pytest.raises(ValueError):
        stamp(1, "bad repository", tmp_path)


@pytest.mark.parametrize("relative", ["__init__.py", "web/updates.py"])
def test_ci_stamp_preserves_utf8_with_an_ansi_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    package = tmp_path / "src/aifactory"
    (package / "web").mkdir(parents=True)
    sources = {
        "__init__.py": '__version__ = "1.0.0"\n',
        "web/updates.py": 'UPDATE_REPOSITORY = "old/repo"\n',
    }
    sources[relative] += "# čeština při přechodu\n"
    for name, source in sources.items():
        (package / name).write_text(source, encoding="utf-8")
    read_text = Path.read_text

    def read_ansi(path: Path, encoding: str | None = None, errors: str | None = None) -> str:
        return read_text(path, encoding=encoding or "cp1252", errors=errors)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", read_ansi)
        assert stamp(2, "janbkrejci/GIRO", tmp_path) == "1.0.2"
    assert "# čeština při přechodu" in (package / relative).read_text(encoding="utf-8")
