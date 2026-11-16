"""GIRO settings (``GIRO_*``) from the process environment and the env file.

Only ``GIRO_*`` names are read and set, also for child processes. The pre-GIRO names
(``HAIFA_*``, ``AIFACTORY_GH``/``AIFACTORY_AZ``) are no longer read: when one is set,
the first read prints one warning per process naming its ``GIRO_*`` replacement, so a
user with an old variable does not silently get another home or setting. This is the
only module that spells the legacy names.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping
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

LEGACY_NAMES: dict[str, str] = {
    **{f"HAIFA_{suffix}": f"{CANONICAL_PREFIX}{suffix}" for suffix in SUFFIXES},
    "AIFACTORY_GH": f"{CANONICAL_PREFIX}GH",
    "AIFACTORY_AZ": f"{CANONICAL_PREFIX}AZ",
}
"""Pre-GIRO names (no longer read) and the ``GIRO_*`` name that replaces each."""

_warned = False


def canonical(name: str) -> str:
    """The ``GIRO_*`` name of setting `name` (its suffix or its ``GIRO_*`` name).

    A name that is no known setting is returned unchanged.
    """
    suffix = name.removeprefix(CANONICAL_PREFIX)
    return CANONICAL_PREFIX + suffix if suffix in SUFFIXES else name


def legacy_in(layers: Iterable[Mapping[str, object]]) -> list[str]:
    """The legacy names set (non-empty) in any of `layers`, sorted."""
    return sorted({name for layer in layers for name in LEGACY_NAMES if layer.get(name)})


def warn_legacy(*layers: Mapping[str, object], stderr: TextIO | None = None) -> None:
    """Warn once per process about legacy names set in `layers` (their values are not read)."""
    global _warned
    if _warned:
        return
    found = legacy_in(layers)
    if not found:
        return
    _warned = True
    replacements = ", ".join(f"{old} -> {LEGACY_NAMES[old]}" for old in found)
    print(
        f"giro: warning: legacy environment variables are ignored, rename them: {replacements}",
        file=sys.stderr if stderr is None else stderr,
    )


def value(name: str, environ: Mapping[str, str] | None = None) -> str | None:
    """Read setting `name` (``GIRO_*`` only); empty means unset, invalid values stay invalid."""
    env = os.environ if environ is None else environ
    warn_legacy(env)
    return env.get(canonical(name)) or None


def resolve(
    process: Mapping[str, str],
    file: Mapping[str, str | None] | None = None,
    *,
    stderr: TextIO | None = None,
) -> dict[str, str]:
    """Lay the env file under the process environment; the shell wins for each setting.

    The env file cannot set ``HOME``, ``XDG_CONFIG_HOME`` or ``GIRO_HOME``. Legacy names
    from either layer are neither read nor copied; they only cause the warning.
    """
    settings = file or {}
    warn_legacy(process, {k: v for k, v in settings.items() if v}, stderr=stderr)
    result = dict(process)
    for key, item in settings.items():
        if key in {"HOME", "XDG_CONFIG_HOME", canonical("HOME")} or key in LEGACY_NAMES:
            continue
        if item is not None and key not in result:
            result[key] = item
    for suffix in SUFFIXES:
        name = canonical(suffix)
        layers = (process,) if suffix == "HOME" else (process, settings)
        selected = next((item for layer in layers if (item := layer.get(name))), None)
        if selected is not None:
            result[name] = selected
    return result


def child_environment(base: Mapping[str, str] | None = None, **overrides: str) -> dict[str, str]:
    """The environment of a child process; a known setting may be given by its suffix.

    Legacy names are left out: a child never sees them (and does not warn again).
    """
    env = resolve(os.environ if base is None else base)
    for legacy in LEGACY_NAMES:
        env.pop(legacy, None)
    for key, item in overrides.items():
        env[canonical(key)] = item
    return env
