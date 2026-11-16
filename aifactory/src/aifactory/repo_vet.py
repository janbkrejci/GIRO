"""Vet a folder before it joins the repository registry, and register it.

``vet_path`` and ``examine`` look at a folder GIRO may not know yet. They only read:
``~`` is expanded, the folder must exist, the repository's top level is taken and the
only git commands are ``rev-parse`` and the reads of ``repo_layout`` (with
``GIT_OPTIONAL_LOCKS=0``). GIRO never runs ``git init``. A folder without git, a bare
repository, a linked worktree (the main checkout is offered), a path under
``.factory/worktrees/``, a repository without a commit and a repository whose trace DB
another registered repository already uses are refused. ``register_repo`` is the
validation of ``POST /api/repos``, ``giro obs --repo`` and the demo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from aifactory import gitrun, procrun
from aifactory.config import ConfigError
from aifactory.config.settings import load_local
from aifactory.errors import UsageError
from aifactory.gitops import read_git as _read_git
from aifactory.gitops import repo_layout
from aifactory.registry import Registry, RegistryState, RepoEntry, RepoError, same_repo

RUN_WORKTREE_PARTS = (".factory", "worktrees")
DEFAULT_TRACE_DB = ".factory/trace.db"


# A new Windows git process needs useful time even when the enclosing probe has
# almost exhausted its deadline. Propagate exhaustion; never misreport "no commits".
MIN_GIT_BUDGET = procrun.MIN_GIT_BUDGET


def require_git_budget() -> None:
    left = procrun.remaining()
    if left is not None and left < MIN_GIT_BUDGET:
        raise procrun.BudgetExceeded("git repository read")


def read_git(path: Path, *args: str) -> str | None:
    require_git_budget()
    if procrun.remaining() is None:
        return _read_git(path, *args)
    return gitrun.try_out(path, *args, read_only=True, timeout=10.0)


def _input_path(raw: object) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise UsageError("path must be a non-empty string")
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        raise UsageError(f"path must be absolute (or start with ~), got {raw!r}")
    return path


def in_run_worktree(path: Path) -> bool:
    """``path`` lies under a ``.factory/worktrees/`` directory (a task run's checkout)."""
    parts = path.parts
    n = len(RUN_WORKTREE_PARTS)
    return any(parts[i : i + n] == RUN_WORKTREE_PARTS for i in range(len(parts) - n + 1))


def trace_db_of(root: Path) -> Path:
    """The trace DB of the repository at ``root`` (from ``.factory/local.yaml`` on disk)."""
    try:
        return load_local(root).trace_db_path(root)
    except (ConfigError, OSError):
        return (root / DEFAULT_TRACE_DB).resolve()


def same_file(a: Path, b: Path) -> bool:
    if os.path.realpath(a) == os.path.realpath(b):
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


@dataclass
class Examined:
    """What ``examine`` found out about a path; ``problem`` is the first refusal."""

    path: Path
    root: Path | None = None
    subdir: str | None = None
    has_commit: bool = False
    trace_db: Path | None = None
    registered: str | None = None
    problem: RepoError | None = None


def _vet(ex: Examined, state: RegistryState) -> None:
    path = ex.path
    if not path.exists():
        raise RepoError("path_not_found", f"{path} does not exist")
    if not path.is_dir():
        raise RepoError("not_a_directory", f"{path} is not a directory")
    real = path.resolve()
    if in_run_worktree(real):
        raise RepoError("run_worktree", f"{path} is a worktree of a task run, not a repository")
    require_git_budget()
    layout = repo_layout(real)
    if layout is not None and layout.bare:
        raise RepoError("bare_repo", f"{path} is a bare repository without a working tree")
    if layout is None or layout.inside_git_dir:
        raise RepoError("not_git", f"{path} is not in a git repository; GIRO does not run git init")
    if layout.toplevel is not None and in_run_worktree(layout.toplevel):
        raise RepoError("run_worktree", f"{path} is a worktree of a task run, not a repository")
    if layout.linked:
        main = layout.main_checkout
        hint = f"; add the main checkout {main}" if main is not None else ""
        raise RepoError(
            "linked_worktree",
            f"{path} is a linked worktree{hint}",
            data={"main_checkout": str(main) if main is not None else None},
        )
    if layout.toplevel is None:
        raise RepoError("not_git", f"{path} is not in a git repository; GIRO does not run git init")
    root = layout.toplevel
    ex.root = root
    if real != root:
        ex.subdir = real.relative_to(root).as_posix() if root in real.parents else None
    for entry in state.repos:
        if same_repo(entry.path, root):
            ex.registered = entry.id
            break
    if read_git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}") is None:
        raise RepoError("no_commits", f"{root} has no commit yet; commit something first")
    ex.has_commit = True
    ex.trace_db = trace_db_of(root)
    check_trace_db(root, state)


def check_trace_db(root: Path, state: RegistryState) -> None:
    """``trace_db_shared`` when another registered repository uses the trace DB of ``root``."""
    db = trace_db_of(root)
    for entry in state.repos:
        other = Path(entry.path)
        if not other.is_dir() or same_repo(other, root):
            continue
        if same_file(trace_db_of(other), db):
            raise RepoError(
                "trace_db_shared",
                f"the trace DB {db} is already used by the registered repository {entry.id}",
                data={"repo": entry.id},
            )


def examine(raw: object, state: RegistryState) -> Examined:
    """Look at the path ``raw`` (body ``path``); ``UsageError`` for a bad value."""
    ex = Examined(_input_path(raw))
    try:
        _vet(ex, state)
    except RepoError as exc:
        ex.problem = exc
    return ex


def vet_path(raw: object, state: RegistryState) -> Examined:
    """``examine`` that raises the first refusal."""
    ex = examine(raw, state)
    if ex.problem is not None:
        raise ex.problem
    return ex


def register_repo(registry: Registry, raw: object) -> tuple[RepoEntry, bool, list[str]]:
    """Vet the path ``raw`` and register its repository: ``(entry, created, warnings)``.

    The validation of ``POST /api/repos`` and ``factory obs --repo``; raises
    ``UsageError`` for a bad value and ``RepoError`` for a refused folder.
    """
    state, warnings = registry.snapshot()
    examined = vet_path(raw, state)
    root = examined.root
    assert root is not None
    entry, created = registry.add(root, check=lambda current: check_trace_db(root, current))
    return entry, created, warnings
