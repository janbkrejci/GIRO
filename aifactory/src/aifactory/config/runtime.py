"""The engine-side runtime of agent runs in a main checkout.

``data_dir`` is the shared session runtime, ``prepare_cfg`` the engine roster with
prompts written out and runtime and trace DB in the main checkout, ``main_ignored`` what a
write guard ignores in the main checkout, and ``apply_agents_override`` the in-memory
harness/model/thinking override of a run. Task runs, triage, learn and workflow advice
use them.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from aifactory import harness as harness_mod
from aifactory.config.loader import write_prompts
from aifactory.config.run import RunConfig
from aifactory.errors import TaskRunError
from aifactory.harness import THINKING_LEVELS
from aifactory.harness.config import SSSFConfig

SSSF_DATA_DIR = "adws/adw_data"  # the engine's default; a task run uses FACTORY_DATA_DIR
FACTORY_DATA_DIR = ".factory/data"
TRACE_DB_SUFFIXES = ("", "-wal", "-shm", "-journal")


def _absolute(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


def data_dir(main: Path, rc: RunConfig) -> Path:
    """The session runtime of task runs: agents.yaml ``data_dir``, else ``.factory/data``.

    Always in (or relative to) the main checkout, shared by all runs.
    """
    value = str(rc.config.agents.defaults.data_dir)
    if not value.strip() or value.rstrip("/") == SSSF_DATA_DIR:
        value = FACTORY_DATA_DIR
    return _absolute(value, main)


def prepare_cfg(main: Path, rc: RunConfig, prompts_dir: Path) -> SSSFConfig:
    """The roster of `rc` with prompts in `prompts_dir`, runtime and trace in the main checkout."""
    cfg = write_prompts(rc.config, prompts_dir).model_copy(deep=True)
    cfg.defaults.data_dir = str(data_dir(main, rc))
    cfg.observability.db = str(rc.local.trace_db_path(main))
    protected = [*cfg.defaults.protected_files, *rc.config.settings.protected_files]
    cfg.defaults.protected_files = list(dict.fromkeys(protected))
    return cfg


def _relative_to(path: Path, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return None


def main_ignored(main: Path, worktrees_dir: str, cfg: SSSFConfig) -> tuple[str, ...]:
    """What the guard ignores in the main checkout: worktrees, session runtime, trace DB."""
    ignored = [worktrees_dir.strip("/").removeprefix("./") + "/"]
    data = _relative_to(Path(cfg.defaults.data_dir), main)
    if data:
        ignored.append(data.rstrip("/") + "/")
    db = _relative_to(Path(cfg.observability.db), main)
    if db:
        ignored += [db + suffix for suffix in TRACE_DB_SUFFIXES]
    return tuple(ignored)


OVERRIDE_KEYS = ("harness", "model", "thinking")
# The model a harness switch picks when no model is given (``config.roster.PRESETS``).
_HARNESS_MODELS = {"claude": "claude-opus-5-5", "codex": "gpt-6.1-sol"}


def apply_agents_override(rc: RunConfig, override: dict[str, str] | None) -> RunConfig:
    """`rc` with every agent of the roster on the given harness/model/thinking (in memory).

    The same as ``factory config roster set`` with defaults only: every role-level value
    is replaced. Nothing is written. A harness switch without a model takes that
    harness's preset model. Step overrides of the workflow (``harness/override.py``) still
    apply on top, so one workflow can still mix harnesses. Unknown harness, thinking or a
    model the harness rejects raise ``invalid_override``.
    """
    values = {k: v.strip() for k, v in (override or {}).items() if v and v.strip()}
    unknown = sorted(set(values) - set(OVERRIDE_KEYS))
    if unknown:
        raise TaskRunError("invalid_override", f"unknown override key(s): {', '.join(unknown)}")
    if not values:
        return rc
    harness = values.get("harness")
    if harness is not None:
        try:
            harness = harness_mod.canonical(harness)
        except ValueError as exc:
            raise TaskRunError("invalid_override", str(exc)) from exc
    thinking = values.get("thinking")
    if thinking is not None and thinking not in THINKING_LEVELS:
        raise TaskRunError(
            "invalid_override", f"thinking {thinking!r} is not one of {list(THINKING_LEVELS)}"
        )
    model = values.get("model")
    agents = []
    for agent in rc.config.agents.agents:
        update: dict[str, Any] = {}
        agent_harness = harness or agent.harness
        if harness is not None:
            update["coding_agent"] = harness
        if model is not None:
            update["model"] = model
        elif harness is not None and harness != agent.harness and harness in _HARNESS_MODELS:
            update["model"] = _HARNESS_MODELS[harness]
        if thinking is not None:
            update["thinking"] = thinking
        elif harness is not None and harness != agent.harness:
            from aifactory.harness.thinking import default_level

            update["thinking"] = default_level(harness, update.get("model", agent.model))
        if agent_harness != "pi" and agent.harness_engineering:
            update["harness_engineering"] = []  # pi extensions belong to pi only
        try:
            harness_mod.load(agent_harness).resolve_model(update.get("model", agent.model))
        except (ValueError, ImportError) as exc:
            raise TaskRunError("invalid_override", f"agent {agent.name!r}: {exc}") from exc
        agents.append(agent.model_copy(update=update, deep=True))
    defaults = rc.config.agents.defaults.model_copy(
        update={
            k: v
            for k, v in (("coding_agent", harness), ("model", model), ("thinking", thinking))
            if v is not None
        },
        deep=True,
    )
    roster = rc.config.agents.model_copy(update={"agents": agents, "defaults": defaults})
    return replace(rc, config=replace(rc.config, agents=roster))
