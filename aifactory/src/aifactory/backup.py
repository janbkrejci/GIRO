"""Save the uncommitted state of one checkout and put it back exactly.

``capture`` copies every dirty path (tracked changes, staged or not, and
untracked files that are not gitignored) into a backup directory and records
``HEAD`` and the index tree. ``verify`` lists every path whose state on disk no
longer matches: a pre-dirty path must equal its saved bytes, any other path must
equal ``HEAD``. ``restore`` writes that state back. Whatever it overwrites or
deletes is moved to ``<backup>/replaced/`` first, so nothing is ever lost.

Gitignored files are invisible to git and therefore neither saved nor checked.

The backup sits deep below the session directory, so a copy of a nested untracked
file easily passes the 260 characters of a Windows path; every file operation goes
through ``long_path``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from aifactory import gitquery, gitrun

INDEX = "(index)"
"""Pseudo-path reported when the git index differs from the capture."""


def long_path(path: Path) -> Path:
    """`path` with the Windows extended-length prefix (``\\\\?\\``); unchanged elsewhere."""
    if sys.platform != "win32":
        return path
    text = os.path.abspath(path)
    if text.startswith("\\\\?\\"):
        return Path(text)
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text[2:])
    return Path("\\\\?\\" + text)


def ignored(path: str, patterns: tuple[str, ...]) -> bool:
    """True when `path` equals or lies below one of `patterns` (prefix rule)."""
    for pattern in patterns:
        stem = pattern.rstrip("/")
        if stem and (path == stem or path.startswith(stem + "/")):
            return True
    return False


@dataclass(frozen=True)
class FileState:
    kind: Literal["file", "symlink", "dir", "absent"]
    digest: str = ""
    mode: int = 0

    def same_content(self, other: FileState) -> bool:
        if self.kind == "absent" or other.kind == "absent":
            return self.kind == other.kind
        if "dir" in (self.kind, other.kind):
            return self.kind == other.kind
        return self.digest == other.digest


ABSENT = FileState("absent")


@dataclass
class CheckoutBackup:
    root: Path
    dir: Path
    head: str | None
    ref: str | None
    index_tree: str | None
    files: dict[str, FileState] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)
    # (mode, checkout bytes) of paths in ``head`` (None = absent), read in batches
    head_entries: dict[str, tuple[str, bytes] | None] = field(
        default_factory=dict, repr=False, compare=False
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dirty_paths(root: Path, patterns: tuple[str, ...] = ()) -> set[str]:
    """Every path that differs from ``HEAD`` in tree or index, plus untracked files.

    One ``git status`` without optional locks: it never rewrites the index.
    """
    try:
        paths = gitquery.dirty_paths(root)
    except gitrun.GitCommandFailed:
        return set()
    return {p for p in paths if not ignored(p, patterns)}


def head_state(root: Path) -> tuple[str | None, str | None]:
    """(``HEAD`` commit, branch) of `root` in one git call."""
    return gitquery.head_state(root)


def state_of(root: Path, path: str) -> FileState:
    """What `path` is on disk right now."""
    target = long_path(root / path)
    try:
        info = target.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return ABSENT
    if stat.S_ISLNK(info.st_mode):
        return FileState("symlink", _sha(os.fsencode(os.readlink(target))))
    if stat.S_ISDIR(info.st_mode):
        return FileState("dir")
    return FileState("file", _sha(target.read_bytes()), info.st_mode & 0o777)


def _index_path(root: Path) -> Path | None:
    found = gitquery.dirs(root)
    return None if found is None else found.git_dir / "index"


# index file -> (its stamp, its tree): the tree is computed again only when git rewrote it
_INDEX_TREES: dict[str, tuple[tuple[int, int, int], str]] = {}


def _write_tree(root: Path) -> str | None:
    """The tree of the index of `root`, without taking ``index.lock``.

    ``git write-tree`` locks the index to store its cache tree, so it fails while
    a concurrent git command in the same checkout (another task run's guard)
    holds the lock. It runs on a private copy of the index instead; git replaces
    the index atomically, so the copy is always a complete index. While the index
    file keeps its mtime, size and inode, the tree found last time is reused.
    """
    index = _index_path(root)
    before = gitquery.stamp(index) if index is not None else None
    if index is not None and before is not None:
        cached = _INDEX_TREES.get(str(index))
        if cached is not None and cached[0] == before:
            return cached[1]
    tree = _copy_and_write_tree(root, index)
    if tree is not None and index is not None and before is not None:
        if gitquery.stamp(index) == before:  # not rewritten while it was copied
            _INDEX_TREES[str(index)] = (before, tree)
    return tree


def _copy_and_write_tree(root: Path, index: Path | None) -> str | None:
    for attempt in range(20):
        with tempfile.TemporaryDirectory(prefix="giro-index-") as tmp:
            env: dict[str, str] | None = None
            if index is not None:
                copy = Path(tmp) / "index"
                try:
                    shutil.copyfile(index, copy)
                except FileNotFoundError:
                    pass
                else:
                    env = {"GIT_INDEX_FILE": str(copy)}
            proc = gitrun.run(root, "write-tree", env=env)
        if proc.ok:
            return proc.out
        if "index.lock" not in proc.error:
            return None
        time.sleep(0.05 * (attempt + 1))
    return None


def index_tree(root: Path) -> str | None:
    """The tree of the index of `root` (None when git cannot write it)."""
    return _write_tree(root)


def capture(root: Path, dest: Path, patterns: tuple[str, ...] = ()) -> CheckoutBackup:
    """Copy every dirty path of `root` under `dest`; raise ``OSError`` if any copy fails."""
    files_dir = dest / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    head, ref = head_state(root)
    backup = CheckoutBackup(root=root, dir=dest, head=head, ref=ref, index_tree=_write_tree(root))
    for path in sorted(dirty_paths(root, patterns)):
        now = state_of(root, path)
        if now.kind == "file":
            copy = long_path(files_dir / path)
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(long_path(root / path), copy)
        elif now.kind == "symlink":
            backup.links[path] = os.readlink(long_path(root / path))
        backup.files[path] = now
    _write_manifest(backup)
    return backup


def _write_manifest(backup: CheckoutBackup) -> None:
    manifest = {
        "root": str(backup.root),
        "head": backup.head,
        "ref": backup.ref,
        "index_tree": backup.index_tree,
        "files": {
            p: {"kind": s.kind, "sha256": s.digest, "mode": s.mode} for p, s in backup.files.items()
        },
        "links": backup.links,
    }
    (backup.dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8", newline="\n"
    )


def supersede(backup: CheckoutBackup, path: str, data: bytes, mode: int) -> None:
    """Make `data` the expected state of `path` (a factory write during the phase).

    A saved copy of the path is moved to ``superseded/`` first (never deleted),
    so ``verify`` and ``restore`` then compare with and put back the factory's
    content.
    """
    saved = long_path(backup.dir / "files" / path)
    if os.path.lexists(saved):
        target = long_path(backup.dir / "superseded" / path)
        n = 1
        while os.path.lexists(target):
            target = long_path(backup.dir / "superseded" / f"{path}.{n}")
            n += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(saved), str(target))
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_bytes(data)
    backup.files[path] = FileState("file", _sha(data), mode & 0o777)
    backup.links.pop(path, None)
    _write_manifest(backup)


def head_entries(
    root: Path, head: str | None, paths: Iterable[str]
) -> dict[str, tuple[str, bytes] | None]:
    """(mode, checkout bytes) of each path in `head`, None when it is not a file there.

    A constant number of git processes: one ``ls-tree`` for all paths, one
    ``cat-file --batch`` for symlink targets (stored verbatim) and one filtered
    ``cat-file --batch`` for files, so the bytes match a checkout (notably CRLF on
    Windows), as journal entries hash bytes on disk.
    """
    wanted = list(dict.fromkeys(paths))
    found: dict[str, tuple[str, bytes] | None] = dict.fromkeys(wanted)
    if not head or not wanted:
        return found
    try:
        entries = gitquery.ls_tree(root, head, wanted)
        blobs = {p: e for p, e in entries.items() if e.type == "blob"}
        links = gitquery.read_objects(root, [e.sha for e in blobs.values() if e.mode == "120000"])
        files = gitquery.read_filtered(
            root, [(e.sha, p) for p, e in blobs.items() if e.mode != "120000"]
        )
    except gitrun.GitCommandFailed:
        return found
    for path, entry in blobs.items():
        data = links.get(entry.sha) if entry.mode == "120000" else files.get((entry.sha, path))
        if data is not None:
            found[path] = (entry.mode, data)
    return found


def _head_entry(backup: CheckoutBackup, path: str) -> tuple[str, bytes] | None:
    if path not in backup.head_entries:
        backup.head_entries.update(head_entries(backup.root, backup.head, [path]))
    return backup.head_entries[path]


def _prefetch(backup: CheckoutBackup, paths: Iterable[str]) -> None:
    """Read the ``HEAD`` entries of every path not known yet, in one batch."""
    missing = [p for p in paths if p not in backup.files and p not in backup.head_entries]
    if missing:
        backup.head_entries.update(head_entries(backup.root, backup.head, missing))


def expected(backup: CheckoutBackup, path: str) -> FileState:
    """The state `path` must have: its saved state if it was dirty, else ``HEAD``."""
    if path in backup.files:
        return backup.files[path]
    entry = _head_entry(backup, path)
    if entry is None:
        return ABSENT
    mode, data = entry
    return FileState("symlink" if mode == "120000" else "file", _sha(data))


def verify(backup: CheckoutBackup, patterns: tuple[str, ...] = (), index: bool = True) -> list[str]:
    """Every path (and ``INDEX``) whose current state differs from the capture."""
    paths = sorted(set(backup.files) | dirty_paths(backup.root, patterns))
    _prefetch(backup, (p for p in paths if not ignored(p, patterns)))
    off = [
        path
        for path in paths
        if not ignored(path, patterns)
        and not state_of(backup.root, path).same_content(expected(backup, path))
    ]
    if index and backup.index_tree and _write_tree(backup.root) != backup.index_tree:
        off.append(INDEX)
    return off


def _set_aside(backup: CheckoutBackup, path: str) -> None:
    """Move whatever sits at `path` into ``replaced/`` (never delete it)."""
    source = long_path(backup.root / path)
    if not os.path.lexists(source):
        return
    target = long_path(backup.dir / "replaced" / path)
    n = 1
    while os.path.lexists(target):
        target = long_path(backup.dir / "replaced" / f"{path}.{n}")
        n += 1
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))


def _clear_parents(backup: CheckoutBackup, path: str) -> None:
    """A parent of `path` that is now a file or link is set aside so the path can be written."""
    parts = Path(path).parts[:-1]
    for i in range(1, len(parts) + 1):
        rel = "/".join(parts[:i])
        full = long_path(backup.root / rel)
        if os.path.lexists(full) and (full.is_symlink() or not full.is_dir()):
            _set_aside(backup, rel)
            return


def _put(backup: CheckoutBackup, path: str, want: FileState, data: bytes | None) -> None:
    target = long_path(backup.root / path)
    _clear_parents(backup, path)
    _set_aside(backup, path)
    if want.kind == "absent":
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if want.kind == "symlink":
        link = backup.links.get(path)
        if link is None and data is not None:
            link = os.fsdecode(data)
        os.symlink(link or "", target)
        return
    if data is None:
        shutil.copy2(long_path(backup.dir / "files" / path), target)
    else:
        target.write_bytes(data)
    if want.mode:
        os.chmod(target, want.mode)


def _restore_one(backup: CheckoutBackup, path: str, head_moved: bool) -> str:
    if path in backup.files:
        want = backup.files[path]
        _put(backup, path, want, None)
        return "restored from backup"
    if head_moved:
        return "not restored: HEAD moved"
    entry = _head_entry(backup, path)
    if entry is None:
        _set_aside(backup, path)
        return "deleted"
    mode, data = entry
    kind: Literal["file", "symlink"] = "symlink" if mode == "120000" else "file"
    perm = 0o755 if mode == "100755" else 0o644
    _put(backup, path, FileState(kind, _sha(data), perm if kind == "file" else 0), data)
    return "rolled back"


def restore(
    backup: CheckoutBackup,
    patterns: tuple[str, ...],
    paths: Iterable[str],
    index: bool = True,
) -> dict[str, str]:
    """Put `paths` back to the captured state; return what happened to each.

    When ``HEAD`` or the branch moved, only pre-dirty paths are restored (from
    their saved bytes) and the index is left alone. Never raises for one path.
    """
    root = backup.root
    head_moved = head_state(root) != (backup.head, backup.ref)
    outcomes: dict[str, str] = {}
    wanted = [p for p in paths if p != INDEX]
    if not head_moved:
        _prefetch(backup, wanted)
    for path in wanted:
        try:
            outcomes[path] = _restore_one(backup, path, head_moved)
        except OSError as error:
            outcomes[path] = f"could not restore ({error}; backup kept at {backup.dir})"
    if index and not head_moved and backup.index_tree:
        if _write_tree(root) != backup.index_tree:
            ok = gitrun.ok(root, "read-tree", backup.index_tree)
            kept = f"could not restore (backup kept at {backup.dir})"
            outcomes[INDEX] = "restored" if ok else kept
    left = set(verify(backup, patterns, index=index and not head_moved))
    for path in wanted:
        if path in left and not outcomes[path].startswith(("could not", "not restored")):
            outcomes[path] = f"could not restore (backup kept at {backup.dir})"
    return outcomes
