"""Lazy package re-exports: a name is imported from its submodule on first access.

A package ``__init__`` lists its exports under ``if TYPE_CHECKING:`` for type checkers and
passes the same names to ``lazy_exports``, so importing one light submodule does not import
every heavy sibling (pydantic, rich, sqlite3) the package re-exports.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any


def lazy_exports(
    package: str, exports: Mapping[str, Sequence[str]]
) -> tuple[Callable[[str], Any], Callable[[], list[str]]]:
    """``__getattr__`` and ``__dir__`` for ``package`` from ``{submodule: names}``."""
    owner = {name: module for module, names in exports.items() for name in names}
    namespace = importlib.import_module(package).__dict__

    def __getattr__(name: str) -> Any:
        module = owner.get(name)
        if module is None:
            raise AttributeError(f"module {package!r} has no attribute {name!r}")
        value = getattr(importlib.import_module(module), name)
        namespace[name] = value
        return value

    def __dir__() -> list[str]:
        return sorted({*namespace, *owner})

    return __getattr__, __dir__
