"""GIRO settings and their supported legacy aliases, before env-file flattening.

Canonical names are ``GIRO_*``. ``HAIFA_*`` (and ``AIFACTORY_GH``/``AIFACTORY_AZ``) are
still read. This is the only module that spells the legacy names: code names a setting
by its ``GIRO_*`` name (or suffix) and reads it with ``value``; a child environment gets
every spelling through ``aliased``/``child_environment``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import TextIO

CANONICAL_PREFIX = "GIRO_"
"""Every setting's canonical name is ``GIRO_<suffix>``."""

SUFFIXES = (
    "HOME",
    "LIBRARY",
    "LIBRARY_URL",
    "UPDATE_REPO",
    "DATABASE_URL",
    "DASH_HOST",
    "FACTORY_REPO",
    "RUN_ID",
    "GUIDE_REPO",
    "OBS_RESTART",
    "SANDBOX_REPO",
    "AZURE_SANDBOX_REPO",
    "VALIDATE_FAKE",
    "VALIDATE_HIDDEN",
    "E2E_CHANNEL",
    "LAUNCH_STUB",
    "GH",
    "AZ",
)

# The legacy prefixes each setting is still read under, in order of precedence.
# The ``gh``/``az`` executables were ``AIFACTORY_GH``/``AIFACTORY_AZ`` before GIRO.
_LEGACY: dict[str, tuple[str, ...]] = {
    **{suffix: ("HAIFA_",) for suffix in SUFFIXES},
    "GH": ("AIFACTORY_",),
    "AZ": ("AIFACTORY_",),
}
_PREFIXES = (CANONICAL_PREFIX, "HAIFA_", "AIFACTORY_")


def _suffix(name: str) -> str:
    for prefix in _PREFIXES:
        if name.startswith(prefix):
            return name.removeprefix(prefix)
    return name


def names(name: str) -> tuple[str, ...]:
    """Every spelling of setting `name` (a suffix or any spelling), canonical first.

    A name that is no known setting is returned alone.
    """
    suffix = _suffix(name)
    if suffix not in _LEGACY:
        return (name,)
    return (CANONICAL_PREFIX + suffix, *(prefix + suffix for prefix in _LEGACY[suffix]))


def canonical(name: str) -> str:
    """The ``GIRO_*`` name of setting `name`."""
    return names(name)[0]


def aliased(values: Mapping[str, str]) -> dict[str, str]:
    """`values` with every known setting under all its spellings (for a child's env)."""
    out: dict[str, str] = {}
    for key, item in values.items():
        for spelling in names(key):
            out[spelling] = item
    return out


def _conflict(layer: Mapping[str, str], spellings: tuple[str, ...]) -> str | None:
    """The first legacy spelling whose value differs from the canonical one, if any."""
    new = spellings[0]
    if not layer.get(new):
        return None
    return next((old for old in spellings[1:] if layer.get(old) and layer[old] != layer[new]), None)


def value(name: str, environ: Mapping[str, str] | None = None) -> str | None:
    """Read a setting from one layer; empty means unset, invalid values stay invalid."""
    env = os.environ if environ is None else environ
    spellings = names(name)
    old = _conflict(env, spellings)
    if old is not None:
        print(
            f"giro: warning: {spellings[0]} conflicts with {old}; {spellings[0]} wins",
            file=sys.stderr,
        )
    return next((env[n] for n in spellings if env.get(n)), None)


def resolve(
    process: Mapping[str, str],
    file: Mapping[str, str | None] | None = None,
    *,
    stderr: TextIO | None = None,
) -> dict[str, str]:
    """Normalize aliases, keeping shell precedence over either env-file spelling."""
    out = sys.stderr if stderr is None else stderr
    result = dict(process)
    settings = file or {}
    for key, item in settings.items():
        if key not in {"HOME", "XDG_CONFIG_HOME", *names("HOME")}:
            if item is not None and key not in result:
                result[key] = item
    for suffix in SUFFIXES:
        spellings = names(suffix)
        layers = (process,) if suffix == "HOME" else (process, settings)
        selected = None
        for layer in layers:
            plain = {n: v for n in spellings if (v := layer.get(n))}
            old = _conflict(plain, spellings)
            if old is not None:
                print(
                    f"giro: warning: {spellings[0]} conflicts with {old}; "
                    f"{spellings[0]} wins in this layer",
                    file=out,
                )
            candidate = next((plain[n] for n in spellings if n in plain), None)
            if selected is None and candidate:
                selected = candidate
        if selected is not None:
            for spelling in spellings:
                result[spelling] = selected
    return result


def child_environment(base: Mapping[str, str] | None = None, **overrides: str) -> dict[str, str]:
    """A child sees identical aliases; explicit per-call values replace both names."""
    env = resolve(os.environ if base is None else base)
    env.update(aliased(overrides))
    return env
