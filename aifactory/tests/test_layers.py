"""The direction of package dependencies, read from the import statements of the source.

Every import counts: module level, inside functions and under ``TYPE_CHECKING``.

- Only ``web/``, the CLI and ``skill/`` (it renders the CLI tree) import ``aifactory.web``
  or ``aifactory.cli``; ``__main__`` runs the CLI.
- ``providers``, ``config``, ``backlog``, ``library`` and ``workflow`` never import
  ``aifactory.run``.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

import aifactory

SRC = Path(aifactory.__file__).parent
TOP_LAYERS = ("web", "cli", "skill", "__main__")
BELOW_RUN = ("providers", "config", "backlog", "library", "workflow")

# Opening the trace store still needs run.store; these go when the store moves lower.
ALLOWED = {
    ("aifactory.config.commit", "aifactory.run.store"),
    ("aifactory.library.install_commit", "aifactory.run.store"),
}


def _module(path: Path, src: Path) -> str:
    parts = list(path.relative_to(src.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path, src: Path) -> set[str]:
    """Every module ``path`` imports, with ``from x import y`` also as ``x.y``."""
    module = _module(path, src)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - node.level + 1]
                origin = ".".join([*base, node.module] if node.module else base)
            else:
                origin = node.module or ""
            found.add(origin)
            found.update(f"{origin}.{alias.name}" for alias in node.names)
    return found


def _layer(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else ""


@functools.cache
def _violations(src: Path = SRC) -> frozenset[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in src.rglob("*.py"):
        module = _module(path, src)
        layer = _layer(module)
        for target in _imports(path, src):
            if not target.startswith("aifactory."):
                continue
            above_all = _layer(target) in ("web", "cli") and layer not in TOP_LAYERS
            run_from_below = _layer(target) == "run" and layer in BELOW_RUN
            if above_all or run_from_below:
                # the module itself: ``aifactory.run.store.X`` is ``aifactory.run.store``
                found.add((module, ".".join(target.split(".")[:3])))
    # ``from aifactory.web import backlog`` names ``aifactory.web`` and its submodule
    return frozenset(
        (module, target)
        for module, target in found
        if not any(m == module and t.startswith(target + ".") for m, t in found)
    )


def test_lower_layers_do_not_import_upper_layers() -> None:
    unexpected = sorted(_violations() - ALLOWED)
    assert not unexpected, "\n".join(f"{module} imports {target}" for module, target in unexpected)


def test_allowed_exceptions_are_still_needed() -> None:
    assert ALLOWED <= _violations(), sorted(ALLOWED - _violations())


def test_the_check_sees_every_import_form(tmp_path: Path) -> None:
    package = tmp_path / "aifactory"
    (package / "config").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "config" / "__init__.py").write_text("")
    (package / "config" / "probe.py").write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from aifactory.run.store import TaskRunStore\n"
        "def f():\n"
        "    from ..web import backlog\n"
        "    import aifactory.cli\n"
    )
    assert _violations(package) == {
        ("aifactory.config.probe", "aifactory.run.store"),
        ("aifactory.config.probe", "aifactory.web.backlog"),
        ("aifactory.config.probe", "aifactory.cli"),
    }
