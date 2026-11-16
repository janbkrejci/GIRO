"""The configuration a run uses: shared files from the ``base`` commit, local.yaml from disk."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

from aifactory import yamlload
from aifactory.config.errors import ConfigError, ConfigIssue
from aifactory.config.loader import FactoryConfig, load_config
from aifactory.config.settings import CONFIG_FILE, LocalSettings, load_local_checked
from aifactory.config.source import CommitSource, WorktreeSource, repo_root, resolve_commit
from aifactory.config.status import ConfigChange, change_warnings, config_changes

DEFAULT_BASE = "main"


@dataclass(frozen=True)
class RunConfig:
    """A loaded run configuration.

    ``changes`` (uncommitted shared-config changes, several git processes) and the
    ``warnings`` built from them are computed on first access only: most callers need
    neither.
    """

    base: str
    commit: str
    config: FactoryConfig
    local: LocalSettings
    root: Path = field(repr=False)
    local_warnings: tuple[str, ...] = ()

    @cached_property
    def changes(self) -> tuple[ConfigChange, ...]:
        return tuple(config_changes(self.root, self.commit))

    @cached_property
    def warnings(self) -> tuple[str, ...]:
        warnings = change_warnings(list(self.changes), self.base, self.commit)
        warnings.extend(self.local_warnings)
        if self.config.settings.base != self.base:
            warnings.append(
                f"committed base is '{self.config.settings.base}' but the run uses '{self.base}'"
            )
        return tuple(warnings)

    def to_json(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "commit": self.commit,
            "source": self.config.source,
            "digest": self.config.digest,
            "agents": [agent.name for agent in self.config.agents.agents],
            "roles": self.config.roles.known_steps(),
            "workflows": sorted(self.config.workflows),
            "prompts": list(self.config.prompts),
            "settings": self.config.settings.model_dump(mode="json"),
            "local": {"trace_db": self.local.trace_db},
            "changes": [change.to_dict() for change in self.changes],
            "warnings": list(self.warnings),
        }


def worktree_base(root: Path) -> str:
    """The ``base`` key of the working tree's config.yaml, or ``main``.

    Only this key is read from the working tree: it says where to read the rest
    from. Other problems in the file are ignored here.
    """
    source = WorktreeSource(root)
    try:
        text = source.read_text(CONFIG_FILE)
    except OSError:
        return DEFAULT_BASE
    if text is None:
        return DEFAULT_BASE
    try:
        data = yamlload.safe_load(text)
    except yaml.YAMLError:
        return DEFAULT_BASE
    if isinstance(data, dict) and isinstance(data.get("base"), str) and data["base"].strip():
        return str(data["base"]).strip()
    return DEFAULT_BASE


def load_run_config(root: Path, base: str | None = None) -> RunConfig:
    """Load the configuration for a run from the tree of the ``base`` commit.

    Two runs started from the same commit get the same configuration, however
    the working tree changes in between. Uncommitted shared-config changes are
    reported as ``warnings`` (computed when first read).
    """
    root = repo_root(root)
    name = base.strip() if base else worktree_base(root)
    if not name:
        raise ConfigError([ConfigIssue(str(root), "base must not be empty")])
    sha = resolve_commit(root, name)
    config = load_config(CommitSource(root, name, sha))
    local, local_warnings = load_local_checked(root)
    return RunConfig(
        base=name,
        commit=sha,
        config=config,
        local=local,
        root=root,
        local_warnings=tuple(local_warnings),
    )
