"""Read a committed backlog without materializing an archive or touching the checkout."""

from __future__ import annotations

import posixpath
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from aifactory.backlog.loader import _Loader
from aifactory.backlog.model import Backlog
from aifactory.backlog.roots import is_glob
from aifactory.config import ConfigError, ProjectSettings
from aifactory.config.source import _read_blobs, git, resolve_commit


class _Snapshot(_Loader):
    def __init__(
        self,
        root: Path,
        settings: ProjectSettings,
        roots: list[str],
        directories: set[str],
        files: dict[str, bytes],
        links: dict[str, str],
    ) -> None:
        super().__init__(root, settings, create_missing=False)
        self._roots = roots
        self.directories = directories
        self.files = files
        self.links = links
        self.children: dict[str, set[str]] = {}
        for rel in directories | files.keys():
            path = PurePosixPath(rel)
            self.children.setdefault(str(path.parent), set()).add(path.name)

    def roots(self) -> list[str]:
        return self._roots

    def resolve(self, path: Path) -> str:
        rel = self.rel(path)
        for _ in range(40):
            parts = PurePosixPath(rel).parts
            for i in range(1, len(parts) + 1):
                prefix = "/".join(parts[:i])
                if prefix in self.links:
                    rel = posixpath.normpath(posixpath.join(self.links[prefix], *parts[i:]))
                    break
            else:
                return rel
        raise OSError(f"symlink loop in committed backlog: {self.rel(path)}")

    def is_dir(self, path: Path) -> bool:
        try:
            return self.resolve(path) in self.directories
        except OSError:
            return False

    def is_file(self, path: Path) -> bool:
        try:
            return self.resolve(path) in self.files
        except OSError:
            return False

    def iterdir(self, path: Path) -> Iterator[Path]:
        return (path / name for name in self.children.get(self.resolve(path), ()))

    def read_text(self, path: Path) -> str:
        try:
            # Match Path.read_text's universal newline conversion; raw bytes remain
            # available to publication so untouched CRLF files are not rewritten.
            return (
                self.files[self.resolve(path)]
                .decode("utf-8")
                .replace("\r\n", "\n")
                .replace("\r", "\n")
            )
        except KeyError as exc:
            raise FileNotFoundError(self.rel(path)) from exc

    def markdown_files(self) -> dict[str, bytes]:
        return {
            rel: self.files[self.resolve(self.root / rel)]
            for rel in self.files
            if rel.endswith(".md") and self.is_file(self.root / rel)
        }


def load_backlog_at(
    root: Path, sha: str, settings: ProjectSettings, *, create_missing: bool = True
) -> Backlog:
    """Load the backlog committed at ``sha``, without a checkout or temporary files."""
    return read_snapshot(root, sha, settings, create_missing=create_missing).load()


def read_snapshot(
    root: Path, sha: str, settings: ProjectSettings, *, create_missing: bool = True
) -> _Snapshot:
    """The tree at ``sha``, parsed by the same loader as the working backlog.

    Only the configured roots are listed. Their blobs are read by one cat-file batch;
    no temporary files, index updates, remote mirror synchronization or global cache.
    ``create_missing`` preserves the disk loader's empty-root behavior in memory only.
    """
    from aifactory.run.gitops import _commit_dirs

    try:
        sha = resolve_commit(root, sha)
    except ConfigError as exc:
        raise RuntimeError(f"cannot read committed backlog: {exc}") from exc
    roots: list[str] = []
    existing: list[str] = []
    for pattern in settings.backlog_patterns:
        matches = _commit_dirs(root, sha, pattern) if is_glob(pattern) else [pattern]
        existing.extend(m for m in matches if m not in existing)
        roots.extend(r for r in (matches if is_glob(pattern) else [pattern]) if r not in roots)
    try:
        entries = git(root, "ls-tree", "-r", "-t", "-z", sha, "--", *existing) if existing else ""
        directories: set[str] = set()
        oids: dict[str, str] = {}
        symlinks: set[str] = set()
        for record in entries.split("\0"):
            if not record:
                continue
            meta, rel = record.split("\t", 1)
            mode, kind, oid = meta.split()
            if kind == "blob":
                oids[rel] = oid
                if mode == "120000":
                    symlinks.add(rel)
            else:
                directories.add(rel)
        files = _read_blobs(root, oids) if oids else {}
    except ConfigError as exc:
        raise RuntimeError(f"cannot read committed backlog: {exc}") from exc
    links: dict[str, str] = {}
    for rel in symlinks:
        target = files[rel].decode("utf-8")
        destination = posixpath.normpath(posixpath.join(posixpath.dirname(rel), target))
        if target.startswith("/") or destination == ".." or destination.startswith("../"):
            raise RuntimeError(f"backlog symlink escapes snapshot: {rel}")
        links[rel] = destination
    if create_missing:
        directories.update(r for r in roots if r not in files)
    return _Snapshot(root, settings, roots, directories, files, links)
