"""The repo a config change starts from (``RepoState``, ``read_state``) and the change itself.

``read_state`` reads the working tree (``worktree``) or the tree of base (``base``).
"""

from __future__ import annotations

import contextlib
import functools
import os
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from aifactory import yamlload
from aifactory.config.errors import ConfigError, ConfigIssue
from aifactory.config.loader import (
    AGENTS_FILE,
    EXTENSIONS_DIR,
    PROMPTS_DIR,
    SKILLS_DIR,
    SKILLS_MIRROR_DIR,
    WORKFLOWS_DIR,
    load_config,
)
from aifactory.config.run import worktree_base
from aifactory.config.settings import CONFIG_FILE, ProjectSettings, parse_project_settings
from aifactory.config.source import FACTORY_DIR, OverlaySource
from aifactory.engine.role_registry import Issue, RoleRegistry, load_roles
from aifactory.library.model import Item, ItemType
from aifactory.library.store import LibraryStoreError, SeedItem
from aifactory.library.tree import read_blobs, tree_files
from aifactory.providers import git
from aifactory.providers.base import ProviderError

Command = Literal["set", "remove", "move"]
Target = Literal["worktree", "direct", "pr"]
_ROOTS = (FACTORY_DIR + "/", SKILLS_DIR + "/", SKILLS_MIRROR_DIR + "/")
_RUNTIME_DIRS = ("data", "worktrees")


def _shared(path: str) -> bool:
    """A path of the shared configuration (never local.yaml, runtime data or the trace DB)."""
    if not path.startswith(_ROOTS):
        return False
    if not path.startswith(FACTORY_DIR + "/"):
        return True
    rest = path[len(FACTORY_DIR) + 1 :]
    first = rest.split("/", 1)[0]
    if rest == "local.yaml" or first in _RUNTIME_DIRS:
        return False
    return not ("/" not in rest and rest.startswith("trace.db"))


# ── the state of the repo ─────────────────────────────────────────────────────


@dataclass
class RepoState:
    """The shared configuration of the repo: the working tree or the tree of base."""

    root: Path
    mode: Literal["worktree", "base"]
    base: str
    base_sha: str
    files: dict[str, bytes]
    executable: set[str] = field(default_factory=set)
    blobs: dict[str, str] = field(default_factory=dict)  # base mode: blob of each path

    def apply(self, change: Mapping[str, bytes | None], executable: set[str]) -> RepoState:
        files = dict(self.files)
        exe = set(self.executable)
        for path, data in change.items():
            exe.discard(path)
            if data is None:
                files.pop(path, None)
            else:
                files[path] = data
                if path in executable:
                    exe.add(path)
        return RepoState(self.root, self.mode, self.base, self.base_sha, files, exe, self.blobs)

    # ── parsed files ──

    @functools.cached_property
    def settings(self) -> ProjectSettings:
        issues: list[ConfigIssue] = []
        data = self.files.get(CONFIG_FILE)
        text = data.decode("utf-8", "replace") if data is not None else None
        found = parse_project_settings(text, CONFIG_FILE, issues)
        if found is None:
            raise _config_error(ConfigError(issues))
        return found

    @functools.cached_property
    def roles(self) -> RoleRegistry:
        """The role registry of the repo (the packaged one when roles.yaml does not load)."""

        try:
            return load_config(OverlaySource(None, self.factory_files())).roles
        except ConfigError:
            return load_roles()

    @functools.cached_property
    def roster(self) -> list[dict[str, Any]]:
        raw = _yaml(self.files.get(AGENTS_FILE))
        agents = raw.get("agents") if isinstance(raw, dict) else None
        if not isinstance(agents, list):
            return []
        return [a for a in agents if isinstance(a, dict) and isinstance(a.get("name"), str)]

    def roster_entry(self, slot: str) -> dict[str, Any] | None:
        return next((a for a in self.roster if a["name"] == slot), None)

    def factory_files(self) -> dict[str, bytes | None]:
        return {p: d for p, d in self.files.items() if p.startswith(FACTORY_DIR + "/")}

    def workflows(self) -> dict[str, bytes]:
        prefix = WORKFLOWS_DIR + "/"
        return {
            p[len(prefix) : -len(".yaml")]: d
            for p, d in sorted(self.files.items())
            if p.startswith(prefix) and p.endswith(".yaml") and "/" not in p[len(prefix) :]
        }

    # ── items ──

    def item_paths(self, type: ItemType, name: str) -> list[str]:
        """The files of an item in the repo (the roster entry is not a file of its own)."""
        if type == "workflow":
            rel = f"{WORKFLOWS_DIR}/{name}.yaml"
            return [rel] if rel in self.files else []
        if type == "agent":
            prefixes: tuple[str, ...] = (f"{PROMPTS_DIR}/{name}/",)
        elif type == "skill":
            prefixes = (f"{SKILLS_DIR}/{name}/", f"{SKILLS_MIRROR_DIR}/{name}/")
        else:
            prefixes = (f"{EXTENSIONS_DIR}/{name}/",)
        return sorted(p for p in self.files if p.startswith(prefixes))

    def has(self, type: ItemType, name: str) -> bool:
        if type == "agent" and self.roster_entry(name) is not None:
            return True
        return bool(self.item_paths(type, name))

    @contextlib.contextmanager
    def materialized(self) -> Iterator[Path]:
        with tempfile.TemporaryDirectory(prefix="factory-config-") as tmp:
            base = Path(tmp)
            for rel, data in self.files.items():
                path = base / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(0o755 if rel in self.executable else 0o644)
            yield base


def _yaml(data: bytes | None) -> Any:
    if data is None:
        return None
    try:
        return yamlload.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return None


def _config_error(exc: Exception) -> LibraryStoreError:
    issues = []
    if isinstance(exc, ConfigError):
        issues = [Issue("invalid_config", i.message, i.path) for i in exc.issues]
    return LibraryStoreError(
        "invalid_config", "the configuration of the repo does not load", issues=issues
    )


def _worktree_files(root: Path) -> tuple[dict[str, bytes], set[str]]:
    files: dict[str, bytes] = {}
    exe: set[str] = set()
    for top in _ROOTS:
        base = root / top
        if not base.is_dir() or base.is_symlink():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            here = Path(dirpath)
            rel_dir = here.relative_to(root).as_posix()
            if rel_dir == FACTORY_DIR:
                dirnames[:] = [d for d in dirnames if d not in _RUNTIME_DIRS]
            dirnames[:] = [d for d in dirnames if d != ".git" and not (here / d).is_symlink()]
            for name in filenames:
                path = here / name
                rel = f"{rel_dir}/{name}"
                if path.is_symlink() or not path.is_file() or not _shared(rel):
                    continue
                files[rel] = path.read_bytes()
                if os.access(path, os.X_OK) and os.name != "nt":
                    exe.add(rel)
    return files, exe


def read_state(root: Path, mode: Literal["worktree", "base"]) -> RepoState:
    """The shared configuration of the working tree, or of the tree of base."""

    base = worktree_base(root)
    base_sha = git.rev_parse(root, f"refs/heads/{base}")
    if base_sha is None:
        raise LibraryStoreError("unknown_base", f"base {base!r} does not exist in {root}")
    if mode == "worktree":
        files, exe = _worktree_files(root)
        return RepoState(root, mode, base, base_sha, files, exe)
    try:
        found = [
            f
            for f in tree_files(root, base_sha, list(_ROOTS))
            if f.mode in ("100644", "100755") and _shared(f.path)
        ]
        blobs = read_blobs(root, (f.blob for f in found))
    except ProviderError as exc:
        raise LibraryStoreError(exc.code, exc.message) from exc
    return RepoState(
        root,
        mode,
        base,
        base_sha,
        {f.path: blobs[f.blob] for f in found},
        {f.path for f in found if f.mode == "100755"},
        {f.path: f.blob for f in found},
    )


# ── the change ────────────────────────────────────────────────────────────────


@dataclass
class Change:
    files: dict[str, bytes | None] = field(default_factory=dict)  # None = delete
    executable: set[str] = field(default_factory=set)
    removed: list[dict[str, Any]] = field(default_factory=list)
    bindings: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)  # set: the binding keys given
    library_items: list[SeedItem] = field(default_factory=list)  # move: written first
    moved: Item | None = None  # move: the item as the library will hold it
