"""Uncommitted changes to the shared configuration, relative to ``base``.

The shared configuration is ``.factory/`` (without ``local.yaml``, worktrees and the
trace DB) and the skills of the repo in ``.claude/skills/`` and ``.agents/skills/``.

Runs read the configuration from the ``base`` commit, so anything changed in
the working tree is ignored by them; these changes are reported as warnings.

``config_status`` is the one implementation of ``giro config status`` (CLI and dashboard).
It reads one ``TreeState``: the base commit and one ``git status`` of the checkout. When
``HEAD`` is the base commit the changes come from that status alone; otherwise the
comparison with base needs ``diff-index`` and ``ls-files`` (``working_tree_changes``).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from aifactory import gitquery
from aifactory.config.errors import ConfigIssue
from aifactory.config.source import FACTORY_DIR, GitError, git, resolve_commit

SHARED_FILES = (
    ".factory/config.yaml",
    ".factory/agents.yaml",
    ".factory/roles.yaml",
    ".factory/manifest.yaml",  # written by older installs; its removal is committed like config
)
SHARED_DIRS = (
    ".factory/prompts/",
    ".factory/workflows/",
    ".factory/extensions/",
    ".claude/skills/",
    ".agents/skills/",
)
# Git's default wildmatch lets * match slashes, including nested skill files.
# Directory pathspecs take a very slow unpack_trees path in Git for Windows/VFS.
CONFIG_PATHSPECS = (f"{FACTORY_DIR}/*", ".claude/skills/*", ".agents/skills/*")

ChangeStatus = Literal["modified", "added", "deleted", "untracked"]
_STATUS: dict[str, ChangeStatus] = {"M": "modified", "A": "added", "D": "deleted"}


def is_shared_config_path(rel: str) -> bool:
    """True for committed configuration (never ``local.yaml``, worktrees or the trace DB)."""
    return rel in SHARED_FILES or rel.startswith(SHARED_DIRS)


@dataclass(frozen=True)
class ConfigChange:
    path: str
    status: ChangeStatus

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "status": self.status}


_NULL_SHA = frozenset("0")


def _raw_entries(out: str) -> list[tuple[str, str, str, str, str]]:
    """``git diff-index --raw -z`` as (src mode, dst mode, src sha, dst sha + status, path)."""
    fields = out.split("\0")
    entries = []
    for header, path in zip(fields[0::2], fields[1::2], strict=False):
        parts = header.lstrip(":").split()
        if len(parts) != 5 or not path:
            continue
        entries.append((parts[0], parts[1], parts[2], parts[3] + " " + parts[4], path))
    return entries


def config_changes(root: Path, sha: str) -> list[ConfigChange]:
    """Shared config files whose working-tree state differs from commit ``sha``."""
    return [
        c
        for c in working_tree_changes(root, sha, CONFIG_PATHSPECS)
        if is_shared_config_path(c.path)
    ]


def working_tree_changes(root: Path, sha: str, pathspecs: Sequence[str]) -> list[ConfigChange]:
    """Files under `pathspecs` whose working-tree state differs from commit ``sha``.

    Only plumbing that never writes the index: ``git diff`` would refresh it (and take
    ``index.lock``) even with ``GIT_OPTIONAL_LOCKS=0``. Entries whose index stat data
    is stale come back from ``diff-index`` as possibly modified; their content is
    hashed (``hash-object``, nothing written) and compared with the commit.
    """
    found: dict[str, ChangeStatus] = {}
    stale: dict[str, str] = {}
    out = git(root, "diff-index", "--raw", "-z", "--no-renames", sha, "--", *pathspecs)
    for src_mode, dst_mode, src_sha, dst, path in _raw_entries(out):
        dst_sha, code = dst.split(" ", 1)
        status = _STATUS.get(code[:1], "modified")
        if (
            status == "modified"
            and src_mode == dst_mode
            and frozenset(dst_sha) == _NULL_SHA
            and (root / path).is_file()
        ):
            stale[path] = src_sha
            continue
        found[path] = status
    if stale:
        paths = list(stale)
        hashes = git(root, "hash-object", "--", *paths).split()
        for path, current in zip(paths, hashes, strict=False):
            if current != stale[path]:
                found[path] = "modified"
    out = git(root, "ls-files", "-z", "--others", "--exclude-standard", "--", *pathspecs)
    for path in (p for p in out.split("\0") if p):
        found.setdefault(path, "untracked")
    return [ConfigChange(path=path, status=status) for path, status in sorted(found.items())]


def change_warnings(changes: list[ConfigChange], base: str, sha: str) -> list[str]:
    """One stable, human-readable warning per change."""
    return [
        f"{c.path} is {c.status} in the working tree but not committed to {base} "
        f"({sha[:7]}); runs use the committed version"
        for c in changes
    ]


def change_summary(changes: list[ConfigChange], base: str, sha: str) -> str:
    """One warning for all changes: how many, where runs read from, and each path."""
    count = len(changes)
    files = "1 file" if count == 1 else f"{count} files"
    listed = ", ".join(f"{c.path} ({c.status})" for c in changes)
    return (
        f"{files} of .factory/ changed in the working tree but not committed to {base} "
        f"({sha[:7]}); runs use the committed version: {listed}"
    )


@dataclass(frozen=True)
class TreeState:
    """The checkout at ``root`` against ``base``: its commit, ``HEAD`` and one ``git status``."""

    root: Path
    base: str
    commit: str
    head: str | None
    entries: tuple[gitquery.StatusEntry, ...]

    def changes(self, pathspecs: Sequence[str], keep: Callable[[str], bool]) -> list[ConfigChange]:
        """Paths under ``pathspecs`` that ``keep`` accepts whose state differs from base.

        ``keep`` must accept exactly the paths ``pathspecs`` match (and may narrow them).
        """
        if self.head != self.commit:
            found = working_tree_changes(self.root, self.commit, pathspecs)
            return [c for c in found if keep(c.path)]
        result: dict[str, ChangeStatus] = {}
        untracked: list[str] = []
        for entry in self.entries:
            if not keep(entry.path):
                continue
            if entry.kind == "untracked":
                untracked.append(entry.path)
            elif entry.kind == "unmerged":
                result[entry.path] = "modified"
            elif "D" in (entry.index, entry.worktree):
                if entry.index != "A":  # added to the index, then deleted: not in either
                    result[entry.path] = "deleted"
            else:
                result[entry.path] = "added" if entry.index == "A" else "modified"
        for path in untracked:
            result.setdefault(path, "untracked")
        return [ConfigChange(path=path, status=status) for path, status in sorted(result.items())]


def read_tree(root: Path, base: str) -> TreeState:
    """The ``TreeState`` of ``root``; ``ConfigError`` when ``base`` names no commit."""
    commit = resolve_commit(root, base)
    try:
        head, entries = gitquery.status_head(root)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitError([ConfigIssue(str(root), f"git status: {exc}")]) from exc
    return TreeState(root, base, commit, head, tuple(entries))


@dataclass(frozen=True)
class ConfigStatus:
    """``giro config status``: shared config not committed to base (D4)."""

    base: str
    commit: str
    changes: list[ConfigChange]

    def to_json(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "commit": self.commit,
            "clean": not self.changes,
            "changes": [c.to_dict() for c in self.changes],
        }

    @property
    def warnings(self) -> list[str]:
        return change_warnings(self.changes, self.base, self.commit)


def config_status(
    root: Path, base: str | None = None, tree: TreeState | None = None
) -> ConfigStatus:
    """The config status of the checkout ``root`` (from ``tree`` when the caller has one)."""
    if tree is None:
        from aifactory.config.run import worktree_base  # config.run imports this module

        tree = read_tree(root, base if base else worktree_base(root))
    changes = tree.changes(CONFIG_PATHSPECS, is_shared_config_path)
    return ConfigStatus(tree.base, tree.commit, changes)
