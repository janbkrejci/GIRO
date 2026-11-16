"""One shared read of each repository's state for the dashboard.

The overview poll, ``GET /api/config/status``, ``GET /api/backlog/status`` and
``GET /api/repos`` read the same ``RepoSnapshot`` from ``REPO_STATES``. A snapshot is kept
while the fingerprint of the checkout is unchanged. The fingerprint is ``stat`` data only
(mtime, ctime, size, inode), never a git process:

- git: ``<root>/.git``, ``HEAD`` and ``index`` of the git dir, the branch file ``HEAD``
  points to, the loose ref files of ``base`` (``gitquery.ref_files``), ``packed-refs`` and
  ``info/exclude``;
- shared configuration: ``config/status.SHARED_FILES``, ``local.yaml``, ``.gitignore``
  and every entry under ``SHARED_DIRS``;
- backlog: every file and directory under the backlog roots of the working tree's
  ``config.yaml`` and the directories a wildcard root pattern passes through.

When the fingerprint changes, the new snapshot costs one ``git status`` (``TreeState``);
the base commit (``gitquery.resolve``) and its tree (``repo_state``) are cached by their own
ref and commit keys. A snapshot is reused for at most ``STATE_TTL`` seconds, which bounds
what the fingerprint cannot see (global excludes, ``core.*`` settings), and for
``VOLATILE_TTL`` seconds when ``base`` is not tracked by ref files (a revision expression,
a reftable repository).

Nothing here writes: git runs with ``GIT_OPTIONAL_LOCKS=0`` and the index is never refreshed.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from aifactory import gitquery
from aifactory.backlog.commit import _pathspecs
from aifactory.backlog.loader import load_settings
from aifactory.backlog.roots import backlog_roots, owning_root
from aifactory.config import ConfigError
from aifactory.config.errors import ConfigIssue
from aifactory.config.repo_state import RepoState, repo_state
from aifactory.config.run import worktree_base
from aifactory.config.settings import CONFIG_FILE, ProjectSettings
from aifactory.config.status import (
    SHARED_DIRS,
    SHARED_FILES,
    ConfigChange,
    ConfigStatus,
    TreeState,
    config_status,
    read_tree,
)

STATE_TTL = 60.0  # s, the longest a snapshot is reused
VOLATILE_TTL = 2.0  # s, when base is not tracked by ref files
_LIMIT = 256  # repositories kept

T = TypeVar("T")
Stamp = tuple[int, int, int, int] | None


def _stamp(path: Path) -> Stamp:
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino)


def _walk(top: Path, into: set[Path]) -> None:
    into.add(top)
    for directory, dirs, files in os.walk(top, followlinks=False):
        into.update(Path(directory) / name for name in (*dirs, *files))


def backlog_paths(root: Path, settings: ProjectSettings | None) -> set[Path]:
    """The backlog files and directories, and the directories wildcard roots pass through."""
    paths = {root / CONFIG_FILE}
    if settings is None:
        return paths
    for pattern in settings.backlog_patterns:
        parts = Path(pattern).parts
        for i in range(len(parts)):
            parents = root.glob(str(Path(*parts[:i]))) if i else [root]
            paths.update(parent for parent in parents if parent.is_dir())
    for directory in backlog_roots(root, settings):
        _walk(root / directory, paths)
    return paths


def _git_paths(root: Path, base: str) -> tuple[set[Path], bool]:
    """The git metadata files of the fingerprint and whether ``base`` is tracked by them."""
    paths = {root / ".git"}
    try:
        found = gitquery.dirs(root)
    except OSError:
        found = None
    if found is None:
        return paths, False
    paths.update(
        {
            found.git_dir / "HEAD",
            found.git_dir / "index",
            found.common_dir / "packed-refs",
            found.common_dir / "info" / "exclude",
        }
    )
    try:
        head = (found.git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        head = ""
    if head.startswith("ref: refs/"):
        paths.add(found.common_dir / head[5:])
    refs = gitquery.ref_files(root, base)
    paths.update(refs or ())
    return paths, refs is not None


def _config_paths(root: Path) -> set[Path]:
    paths = {root / p for p in (*SHARED_FILES, ".factory/local.yaml", ".gitignore")}
    for directory in SHARED_DIRS:
        _walk(root / directory, paths)
    return paths


def _stamps(paths: set[Path]) -> tuple[tuple[str, Stamp], ...]:
    return tuple((str(p), _stamp(p)) for p in sorted(paths))


def _unresolved(root: Path, base: str) -> ConfigError:
    return ConfigError(
        [ConfigIssue(str(root), f"base '{base}' does not resolve to a commit in {root}")]
    )


@dataclass
class RepoSnapshot:
    """The state of the checkout at ``root`` at one fingerprint; read-only for callers.

    ``state`` is ``repo_state`` and ``tree`` the ``TreeState`` (None with the matching
    ``*_error``). ``settings`` are the working tree's backlog settings. ``backlog_stamps``
    changes whenever a backlog file or ``config.yaml`` changes (task caches compare it).
    """

    root: Path
    base: str
    backlog_stamps: tuple[tuple[str, Stamp], ...]
    settings: ProjectSettings | None
    settings_error: ConfigError | None
    state: RepoState | None = None
    state_error: ConfigError | None = None
    tree: TreeState | None = None
    tree_error: ConfigError | None = None
    _memo: dict[object, Any] = field(default_factory=dict, repr=False)
    # reentrant: a memoized computation may read another memoized value
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def memo(self, key: object, compute: Callable[[], T]) -> T:
        """``compute()`` once per snapshot (exceptions are not kept)."""
        with self._lock:
            if key in self._memo:
                value: T = self._memo[key]
                return value
            value = compute()
            self._memo[key] = value
            return value

    def require_tree(self) -> TreeState:
        if self.tree is None:
            raise self.tree_error or _unresolved(self.root, self.base)
        return self.tree

    def config_status(self) -> ConfigStatus:
        """``giro config status`` of the checkout; ``ConfigError`` without a base commit."""
        tree = self.require_tree()
        return self.memo("config_status", lambda: config_status(self.root, tree=tree))

    def backlog_changes(self) -> list[ConfigChange]:
        """Backlog files not committed to base (``backlog.commit.backlog_changes``)."""
        tree = self.require_tree()
        if self.settings is None:
            raise self.settings_error or ConfigError([])
        settings = self.settings

        def compute() -> list[ConfigChange]:
            return tree.changes(
                _pathspecs(settings), lambda path: owning_root(path, settings) is not None
            )

        return self.memo("backlog_changes", compute)


def _read(root: Path, base: str, settings: Any, stamps: Any) -> RepoSnapshot:
    snap = RepoSnapshot(
        root=root,
        base=base,
        backlog_stamps=stamps,
        settings=settings if isinstance(settings, ProjectSettings) else None,
        settings_error=settings if isinstance(settings, ConfigError) else None,
    )
    try:
        snap.state = repo_state(root, base)
    except ConfigError as exc:
        snap.state_error = exc
    commit = snap.state.commit if snap.state is not None else None
    if snap.state is not None and commit is None:
        snap.tree_error = _unresolved(root, base)
        return snap
    try:
        snap.tree = read_tree(root, base)
    except ConfigError as exc:
        snap.tree_error = exc
    return snap


@dataclass
class _Entry:
    lock: threading.Lock = field(default_factory=threading.Lock)
    config: tuple[Stamp, str, ProjectSettings | ConfigError] | None = None
    fingerprint: object = None
    checked: float = 0.0
    snapshot: RepoSnapshot | None = None


class RepoStates:
    """Snapshots per checkout root; thread-safe, one computation per root at a time."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._items: OrderedDict[str, _Entry] = OrderedDict()

    def get(self, root: Path) -> RepoSnapshot:
        """The snapshot of the checkout whose top level is ``root``."""
        key = str(root)
        with self._lock:
            entry = self._items.pop(key, None) or _Entry()
            self._items[key] = entry
            while len(self._items) > _LIMIT:
                self._items.popitem(last=False)
        with entry.lock:
            base, settings = self._config(root, entry)
            git_paths, tracked = _git_paths(root, base)
            backlog = _stamps(
                backlog_paths(root, settings if isinstance(settings, ProjectSettings) else None)
            )
            fingerprint = (_stamps(git_paths), _stamps(_config_paths(root)), backlog)
            ttl = STATE_TTL if tracked else VOLATILE_TTL
            now = self._clock()
            if (
                entry.snapshot is not None
                and entry.fingerprint == fingerprint
                and now - entry.checked < ttl
            ):
                return entry.snapshot
            snapshot = _read(root, base, settings, backlog)
            entry.fingerprint, entry.checked, entry.snapshot = fingerprint, now, snapshot
            return snapshot

    @staticmethod
    def _config(root: Path, entry: _Entry) -> tuple[str, ProjectSettings | ConfigError]:
        """``base`` and the backlog settings of ``config.yaml``, parsed again only on change."""
        current = _stamp(root / CONFIG_FILE)
        if entry.config is not None and entry.config[0] == current:
            return entry.config[1], entry.config[2]
        base = worktree_base(root)
        settings: ProjectSettings | ConfigError
        try:
            settings = load_settings(root)
        except ConfigError as exc:
            settings = exc
        except (OSError, ValueError) as exc:
            settings = ConfigError([ConfigIssue(str(root / CONFIG_FILE), str(exc))])
        entry.config = (current, base, settings)
        return base, settings

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


REPO_STATES = RepoStates()


def snapshot(root: Path) -> RepoSnapshot:
    """``REPO_STATES.get(root)``."""
    return REPO_STATES.get(root)
