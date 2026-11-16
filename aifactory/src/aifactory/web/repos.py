"""Repositories of the multi-repo dashboard: inspect, add, remove, status and contexts.

``inspect_repo`` reports what ``aifactory.repo_vet.examine`` found out about a folder the
dashboard may not know yet, plus its branch and ``origin`` URL.

``repo_status`` is one item of ``GET /api/repos``: ``ok``, ``uncommitted``,
``not_installed``, ``missing`` or ``not_git``, with the factory state from
``aifactory.config.repo_state.repo_state``. ``RepoContexts`` keeps one ``LiveHub`` per
registered repository and closes it when the repository leaves the registry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aifactory.config import ConfigError
from aifactory.config.repo_state import RepoState, repo_state
from aifactory.gitops import repo_layout
from aifactory.registry import Registry, RegistryState, RepoEntry, RepoError
from aifactory.repo_vet import examine, read_git, register_repo, require_git_budget
from aifactory.web import settings as web_settings
from aifactory.web.factory import FactoryState
from aifactory.web.live import LiveHub

JsonDict = dict[str, Any]

REPO_STATUSES = ("ok", "uncommitted", "not_installed", "missing", "not_git")


def add_repo(
    registry: Registry,
    raw: object,
    environ: Mapping[str, str] | None = None,
    *,
    base_changes: str | None = None,
    base_pr_merge: str | None = None,
) -> tuple[RepoEntry, bool, JsonDict, list[str]]:
    """The dashboard's "add": register the repository and install factory in one go.

    A repository whose base already has ``.factory/config.yaml`` is only registered:
    nothing is committed or pushed, and the runtime lines factory needs ignored that its
    ``.gitignore`` lacks go to ``info/exclude`` of this clone. Otherwise the install writes
    only ``.factory/config.yaml`` and the ``.gitignore`` lines, in one commit on base,
    pushed when there is a remote (see ``plan_init(replace=True)``); agents and workflows
    come from the library.
    ``base_changes`` and ``base_pr_merge`` go to ``config.yaml`` and already apply to this
    commit: with ``pr`` it goes to ``factory-init/<n>`` with a pull request (merged at once
    with ``auto``), and factory is installed once base has it. A refused install leaves no
    new registry entry. Returns ``(entry, created, install, warnings)``.
    """
    from aifactory.library.install_commit import commit_init
    from aifactory.library.store import LibraryStoreError

    entry, created, warnings = register_repo(registry, raw)
    root = Path(entry.path)
    found = _factory(root)
    if found is not None and found.state == "installed":
        return entry, created, _keep_installed(), [*warnings, *_exclude_runtime(root)]
    try:
        result = commit_init(
            root,
            replace=True,
            environ=environ,
            pr=base_changes == "pr",
            base_changes=base_changes,
            base_pr_merge=base_pr_merge,
        )
    except LibraryStoreError as exc:
        if created:
            registry.remove(entry.id)
        raise RepoError(exc.code, exc.message, data=dict(exc.data or {})) from exc
    install = {
        "committed": result.committed,
        "commit": result.commit,
        "pushed": result.pushed,
        "files": [f.to_json() for f in result.plan.publish.files],
        "pr": {"url": result.pr.url, "branch": result.pr.branch} if result.pr else None,
        "merged": result.merged,
    }
    return entry, created, install, [*warnings, *result.warnings]


def _keep_installed() -> JsonDict:
    """The ``install`` answer of ``add_repo`` for a repository that already has factory."""
    return {
        "committed": False,
        "commit": None,
        "pushed": False,
        "files": [],
        "pr": None,
        "merged": False,
    }


def _exclude_runtime(root: Path) -> list[str]:
    """Put the runtime lines missing from ``.gitignore`` into ``info/exclude``; warnings."""
    from aifactory import gitops
    from aifactory.library.install import GITIGNORE_LINES

    gitignore = root / ".gitignore"
    try:
        text = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    except (OSError, UnicodeDecodeError):
        text = ""
    present = {line.strip() for line in text.splitlines()}
    missing = [line for line in GITIGNORE_LINES if line not in present]
    if not missing:
        return []
    try:
        gitops.ensure_excluded(root, missing)
    except (RuntimeError, OSError) as exc:
        return [f"info/exclude not updated: {exc}"]
    return []


def remove_repo(
    registry: Registry,
    repo_id: str,
    *,
    uninstall: bool = False,
    environ: Mapping[str, str] | None = None,
) -> tuple[RepoEntry, JsonDict | None]:
    """The dashboard's "remove": unregister; the repository keeps ``.factory/``.

    ``uninstall`` also commits the removal of ``.factory/`` on base first.
    """
    from aifactory.library.store import LibraryStoreError
    from aifactory.library.uninstall import uninstall as remove_factory

    entry = registry.snapshot()[0].by_id(repo_id)
    if entry is None:
        raise RepoError("unknown_repo", f"no registered repository {repo_id!r}")
    done: JsonDict | None = None
    if uninstall and (Path(entry.path) / ".git").exists():
        try:
            done = remove_factory(Path(entry.path), environ=environ).to_json()
        except LibraryStoreError as exc:
            raise RepoError(exc.code, exc.message, data=dict(exc.data or {})) from exc
    removed = registry.remove(repo_id)
    if removed is None:
        raise RepoError("unknown_repo", f"no registered repository {repo_id!r}")
    return removed, done


def _problem_json(exc: RepoError) -> JsonDict:
    return {"code": exc.code, "message": exc.message, **exc.data}


def _factory(root: Path) -> RepoState | None:
    try:
        return repo_state(root)
    except (ConfigError, OSError):
        return None


def inspect_repo(raw: object, state: RegistryState) -> JsonDict:
    """What adding the folder ``raw`` would do; reads only (``POST /api/repos/inspect``)."""
    ex = examine(raw, state)
    root = ex.root
    branch: str | None = None
    remote: JsonDict | None = None
    factory: JsonDict | None = None
    if root is not None and ex.has_commit:
        branch = read_git(root, "rev-parse", "--abbrev-ref", "HEAD") or None
        url = read_git(root, "remote", "get-url", "origin")
        remote = {"name": "origin", "url": url} if url else None
        found = _factory(root)
        factory = found.to_json() if found is not None else None
    return {
        "path": str(ex.path),
        "root": str(root) if root is not None else None,
        "subdir": ex.subdir,
        "registered": ex.registered,
        "addable": ex.problem is None,
        "problem": _problem_json(ex.problem) if ex.problem is not None else None,
        "branch": branch,
        "remote": remote,
        "trace_db": str(ex.trace_db) if ex.trace_db is not None else None,
        "factory": factory,
    }


def repo_status(entry: RepoEntry) -> JsonDict:
    """One item of ``GET /api/repos``: the entry, its ``status`` and ``factory`` state."""
    item: JsonDict = {**entry.to_json(), "status": "ok", "factory": None}
    root = Path(entry.path)
    if not root.is_dir():
        item["status"] = "missing"
        return item
    require_git_budget()
    layout = repo_layout(root)
    if layout is None or layout.toplevel is None:
        item["status"] = "not_git"
        return item
    factory = _factory(layout.toplevel)
    item["factory"] = factory.to_json() if factory is not None else None
    if factory is None:
        item["status"] = "not_git"
        return item
    state = factory.state
    if state == "none" or (state == "unsupported" and not factory.config_in_base):
        item["status"] = "not_installed"
    elif state == "uncommitted":
        item["status"] = "uncommitted"
    else:
        data, _warnings = web_settings.config_status(layout.toplevel)
        if data is not None and data.get("clean") is False:
            item["status"] = "uncommitted"
    return item


@dataclass
class RepoContext:
    """One repository the API serves: its registry ``id`` (None for one repo), root, hub.

    ``factory`` holds the Factory tab's lock (one apply or pull at a time) and check cache.
    """

    id: str | None
    root: Path
    live: LiveHub = field(repr=False)
    factory: FactoryState = field(default_factory=FactoryState, repr=False)


class RepoContexts:
    """The contexts of the registered repositories, created on first use."""

    def __init__(self, registry: Registry, live_interval: float) -> None:
        self.registry = registry
        self.live_interval = live_interval
        self._contexts: dict[str, tuple[str, RepoContext]] = {}

    async def _prune(self, state: RegistryState) -> None:
        for repo_id, (path, ctx) in list(self._contexts.items()):
            entry = state.by_id(repo_id)
            if entry is None or entry.path != path:
                del self._contexts[repo_id]
                await ctx.live.aclose()

    async def resolve(self, repo_id: str) -> RepoContext:
        """The context of ``repo_id``; ``unknown_repo`` or ``repo_missing`` (HTTP 404)."""
        state, _warnings = self.registry.snapshot()
        await self._prune(state)
        entry = state.by_id(repo_id)
        if entry is None:
            raise RepoError("unknown_repo", f"no registered repository {repo_id!r}")
        root = Path(entry.path)
        if not root.is_dir():
            raise RepoError(
                "repo_missing",
                f"the folder {entry.path} of repository {repo_id!r} is missing",
                data={"repo": repo_id, "path": entry.path},
            )
        known = self._contexts.get(repo_id)
        if known is not None:
            return known[1]
        ctx = RepoContext(
            repo_id, root.resolve(), LiveHub(root.resolve(), interval=self.live_interval)
        )
        self._contexts[repo_id] = (entry.path, ctx)
        return ctx

    async def drop(self, repo_id: str) -> None:
        """Close the hub of ``repo_id`` and forget its context."""
        known = self._contexts.pop(repo_id, None)
        if known is not None:
            await known[1].live.aclose()

    async def aclose(self) -> None:
        for repo_id in list(self._contexts):
            await self.drop(repo_id)
