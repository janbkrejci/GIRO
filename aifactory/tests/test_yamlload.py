"""yamlload: the libyaml safe loader, same results as yaml.safe_load, still safe."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from aifactory import yamlload

DOCUMENT = "name: x\nlist: [1, 2.5, true, null]\nnested:\n  key: 'č'\ndate: 2026-10-14\n"


def test_loader_is_libyaml_when_available() -> None:
    loader: object = yamlload.LOADER
    if yaml.__with_libyaml__:
        assert loader is yaml.CSafeLoader
    else:
        assert loader is yaml.SafeLoader


@pytest.mark.parametrize("stream", [DOCUMENT, DOCUMENT.encode("utf-8")])
def test_same_result_as_safe_load(stream: str | bytes) -> None:
    assert yamlload.safe_load(stream) == yaml.safe_load(stream)


@pytest.mark.parametrize("stream", ["", b""])
def test_empty_input_is_none(stream: str | bytes) -> None:
    assert yamlload.safe_load(stream) is None


def test_rejects_python_tags() -> None:
    with pytest.raises(yaml.YAMLError):
        yamlload.safe_load("!!python/object/apply:os.system [x]")


def test_compose_keeps_marks() -> None:
    node = yamlload.compose(DOCUMENT)
    assert isinstance(node, yaml.MappingNode)
    assert node.value[1][0].start_mark.line == 1


def test_yaml_is_read_only_through_yamlload() -> None:
    """Every YAML read in the package goes through the libyaml loader of ``yamlload``."""
    readers = {"safe_load", "load", "full_load", "unsafe_load", "compose", "parse", "scan"}
    readers |= {f"{name}_all" for name in readers}
    package = Path(yamlload.__file__).parent
    offenders = []
    for path in sorted(package.rglob("*.py")):
        if path.name == "yamlload.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            called = (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "yaml"
                and node.attr in readers
            )
            imported = (
                isinstance(node, ast.ImportFrom)
                and node.module == "yaml"
                and any(alias.name in readers for alias in node.names)
            )
            if (called or imported) and isinstance(node, ast.expr | ast.stmt):
                offenders.append(f"{path.relative_to(package)}:{node.lineno}")
    assert offenders == []


def test_yamledit_imports_ruamel_only_when_editing() -> None:
    code = (
        "import sys\n"
        "import aifactory.config.yamledit as yamledit\n"
        "assert 'ruamel' not in sys.modules\n"
        "assert 'b: 2' in yamledit.edit_yaml('a: 1\\n', lambda doc: doc.update(b=2))\n"
        "assert 'ruamel.yaml' in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
