"""Shared primitives of backlog writes: errors, the write lock, lookups, ids and the
validated write of changed files (``_write_checked``).

The edit functions read and write the backlog through a ``BacklogFiles``:
``DiskBacklog`` (the checkout under a root path; a plain ``Path`` means this one) or
``MemoryBacklog`` (a committed snapshot edited in memory, for follow-ups). A change is
validated on a copy before it is written: a staged directory for the checkout, an
overlay of the snapshot in memory.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import shutil
import tempfile
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from functools import wraps
from pathlib import Path, PurePosixPath
from posixpath import normpath
from typing import TYPE_CHECKING, ParamSpec, Protocol, TypeVar

from aifactory import mainwrites
from aifactory.backlog.loader import iter_containers, load_backlog
from aifactory.backlog.model import (
    Backlog,
    Container,
    Issue,
    Task,
)
from aifactory.backlog.roots import backlog_roots, owning_root
from aifactory.backlog.taskfile import (
    remove_field,
    set_field,
)
from aifactory.backlog.validate import check_backlog
from aifactory.locks import path_lock
from aifactory.oscompat import atomic_write

if TYPE_CHECKING:
    from aifactory.backlog.snapshot import _Snapshot
    from aifactory.config.settings import ProjectSettings

EDITABLE_STATUSES: tuple[str, ...] = ("todo", "cancelled")


_SLUG_MAX = 40


class TaskEditError(Exception):
    """A rejected task write or lookup, with a stable ``code``."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        exit_code: int = 2,
        path: str | None = None,
        id: str | None = None,
        issues: list[Issue] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.path = path
        self.id = id
        self.issues = list(issues or [])

    def errors(self) -> list[dict[str, object]]:
        if self.issues:
            return [i.to_dict() for i in self.issues]
        return [{"code": self.code, "message": self.message, "path": self.path, "id": self.id}]


P = ParamSpec("P")


R = TypeVar("R")


@contextlib.contextmanager
def write_lock(root: Path, operation: str = "backlog") -> Iterator[None]:
    from aifactory.home import giro_home

    key = hashlib.sha256(str(root.resolve()).encode()).hexdigest()
    path = giro_home() / f"{operation}-locks" / f"{key}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path_lock(path).hold(30):
        yield


def serialized_write(fn: Callable[P, R]) -> Callable[P, R]:
    """Hold the repository lock across loading, validation and replacement."""

    @wraps(fn)
    def locked(*args: P.args, **kwargs: P.kwargs) -> R:
        root = args[0] if args else kwargs.get("root", kwargs.get("repo"))
        if isinstance(root, MemoryBacklog):  # private to its caller, nothing to share
            return fn(*args, **kwargs)
        with write_lock(Path(str(root))):
            return fn(*args, **kwargs)

    return locked


def task_revision(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slugify(text: str) -> str:
    """ASCII lowercase slug: ``Migrace hlavičky faktury`` -> ``migrace-hlavicky-faktury``."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug[:_SLUG_MAX].strip("-")


def load_for_edit(root: Root) -> Backlog:
    """The backlog under ``root``; ``missing_backlog_dir`` when a backlog root is missing."""
    backlog = files_of(root).load()
    missing = next((i for i in backlog.issues if i.code == "missing_backlog_dir"), None)
    if missing is not None:
        raise TaskEditError("missing_backlog_dir", missing.message, path=missing.path)
    return backlog


def find_task(backlog: Backlog, task_id: str, issues: list[Issue] | None = None) -> Task:
    """The task ``task_id``; ``TaskEditError`` when it is unknown or ambiguous."""
    node = backlog.by_id.get(task_id)
    if not isinstance(node, Task):
        raise TaskEditError("unknown_task", f"unknown task '{task_id}'", id=task_id)
    if issues is None:
        issues = check_backlog(backlog)
    if any(i.code == "duplicate_id" and i.id == task_id for i in issues):
        raise TaskEditError(
            "duplicate_id", f"task id '{task_id}' is not unique", id=task_id, path=node.path
        )
    return node


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _issue_key(issue: Issue) -> tuple[str, str, str]:
    return issue.code, issue.path, issue.message


def _guard(root: Path, backlog: Backlog, rel: str) -> Path:
    owner = owning_root(rel, backlog.settings)
    target = (root / rel).resolve()
    base = (root / owner).resolve() if owner is not None else None
    if not rel.endswith(".md") or base is None or not target.is_relative_to(base) or target == base:
        raise TaskEditError(
            "outside_backlog", f"refusing to write '{rel}' outside the backlog", path=rel
        )
    return root / rel


def _atomic_write(path: Path, text: str, rel: str) -> None:
    try:
        atomic_write(path, text)
    except OSError as exc:
        raise TaskEditError("write_failed", f"cannot write '{rel}': {exc}", path=rel) from exc


def _new_issues(backlog: Backlog, baseline: list[Issue], pending: dict[str, str]) -> list[Issue]:
    """The problems ``pending`` would add to the checkout's backlog, checked on a staged copy."""
    root = backlog.root
    settings = backlog.settings
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        for backlog_dir in backlog_roots(root, settings):
            if (root / backlog_dir).is_dir():
                shutil.copytree(root / backlog_dir, stage / backlog_dir)
        for rel, text in pending.items():
            staged = stage / rel
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_text(text, encoding="utf-8", newline="\n")
        return _added(baseline, check_backlog(load_backlog(stage, settings)))


def _added(baseline: list[Issue], candidate: list[Issue]) -> list[Issue]:
    known = {_issue_key(i) for i in baseline}
    return [i for i in candidate if _issue_key(i) not in known]


class BacklogFiles(Protocol):
    """Where the edit functions read, validate and write backlog files."""

    root: Path

    def load(self, settings: ProjectSettings | None = None) -> Backlog: ...

    def read(self, rel: str) -> str: ...

    def exists(self, rel: str) -> bool: ...

    def guard(self, backlog: Backlog, rel: str) -> None: ...

    def new_issues(
        self, backlog: Backlog, baseline: list[Issue], pending: dict[str, str]
    ) -> list[Issue]: ...

    def write(self, backlog: Backlog, pending: dict[str, str], command: str) -> None: ...


class DiskBacklog:
    """The backlog of the checkout under `root`: atomic writes, shared database, journal."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self, settings: ProjectSettings | None = None) -> Backlog:
        return load_backlog(self.root, settings)

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def exists(self, rel: str) -> bool:
        return (self.root / rel).exists()

    def guard(self, backlog: Backlog, rel: str) -> None:
        _guard(self.root, backlog, rel)

    def new_issues(
        self, backlog: Backlog, baseline: list[Issue], pending: dict[str, str]
    ) -> list[Issue]:
        return _new_issues(backlog, baseline, pending)

    def write(self, backlog: Backlog, pending: dict[str, str], command: str) -> None:
        from aifactory.database.backlog import publish

        root = self.root
        publish(root, pending)
        for rel, text in pending.items():
            target = root / rel
            try:
                old: bytes | None = target.read_bytes()
            except OSError:
                old = None
            _atomic_write(target, text, rel)
            try:
                written = target.read_bytes()
                mode = target.stat().st_mode & 0o777
            except OSError:
                written, mode = text.encode("utf-8"), 0
            mainwrites.record(root, rel, old, written, mode=mode, command=command)


class MemoryBacklog:
    """A committed backlog edited in memory: nothing is written to disk.

    Reads go through the snapshot loader (``backlog.snapshot``), writes replace entries of
    ``files`` (path -> bytes, as committed) and a change is validated on an overlay
    snapshot. A path through a committed symlink is written to the link's target.
    """

    def __init__(self, snapshot: _Snapshot) -> None:
        self.root = snapshot.root
        self.settings = snapshot.settings
        self.roots = snapshot.roots()
        self.directories = set(snapshot.directories)
        self.files = dict(snapshot.files)
        self.links = dict(snapshot.links)

    def _loader(
        self, files: dict[str, bytes] | None = None, directories: set[str] | None = None
    ) -> _Snapshot:
        from aifactory.backlog.snapshot import _Snapshot

        return _Snapshot(
            self.root,
            self.settings,
            self.roots,
            self.directories if directories is None else directories,
            self.files if files is None else files,
            self.links,
        )

    def load(self, settings: ProjectSettings | None = None) -> Backlog:
        return self._loader().load()

    def _key(self, rel: str) -> str:
        """The stored path of `rel`, with committed symlinks followed."""
        try:
            return self._loader().resolve(self.root / rel)
        except (OSError, RuntimeError) as exc:
            raise TaskEditError("write_failed", f"cannot write '{rel}': {exc}", path=rel) from exc

    def read(self, rel: str) -> str:
        return self._loader().read_text(self.root / rel)

    def read_bytes(self, rel: str) -> bytes:
        """The stored bytes of `rel` (line ends as committed)."""
        try:
            return self.files[self._key(rel)]
        except KeyError as exc:
            raise FileNotFoundError(rel) from exc

    def exists(self, rel: str) -> bool:
        loader = self._loader()
        return loader.is_file(self.root / rel) or loader.is_dir(self.root / rel)

    def guard(self, backlog: Backlog, rel: str) -> None:
        owner = owning_root(rel, backlog.settings)
        target = normpath(rel)
        base = normpath(owner) if owner is not None else None
        if not rel.endswith(".md") or base is None or not target.startswith(f"{base}/"):
            raise TaskEditError(
                "outside_backlog", f"refusing to write '{rel}' outside the backlog", path=rel
            )
        self._key(rel)

    def _overlay(self, pending: dict[str, str]) -> tuple[dict[str, bytes], set[str]]:
        files = dict(self.files)
        directories = set(self.directories)
        for rel, text in pending.items():
            key = self._key(rel)
            files[key] = text.encode("utf-8")
            parent = PurePosixPath(key).parent
            while str(parent) != ".":
                directories.add(str(parent))
                parent = parent.parent
        return files, directories

    def new_issues(
        self, backlog: Backlog, baseline: list[Issue], pending: dict[str, str]
    ) -> list[Issue]:
        files, directories = self._overlay(pending)
        return _added(baseline, check_backlog(self._loader(files, directories).load()))

    def write(self, backlog: Backlog, pending: dict[str, str], command: str) -> None:
        self.files, self.directories = self._overlay(pending)


Root = Path | MemoryBacklog


def files_of(root: Root) -> BacklogFiles:
    """The ``BacklogFiles`` behind an edit function's `root` argument."""
    return root if isinstance(root, MemoryBacklog) else DiskBacklog(root)


def _unchanged(files: BacklogFiles, rel: str, text: str) -> bool:
    try:
        return files.read(rel) == text
    except (FileNotFoundError, IsADirectoryError):
        return False


def _write_checked(
    files: BacklogFiles,
    backlog: Backlog,
    baseline: list[Issue],
    changes: dict[str, str],
    command: str,
) -> bool:
    """Validate `changes` on a copy of the backlog, then write them; True when written.

    `command` names the factory command in the journal of factory writes.
    """
    for rel in changes:
        files.guard(backlog, rel)
    pending = {rel: text for rel, text in changes.items() if not _unchanged(files, rel, text)}
    if pending:
        new = files.new_issues(backlog, baseline, pending)
        if new:
            raise TaskEditError(
                "backlog_invalid",
                f"change rejected: {len(new)} new problem(s) in the backlog",
                exit_code=1,
                issues=new,
            )
        files.write(backlog, pending, command)
    return bool(pending)


def _find_step(backlog: Backlog, step: str) -> Container:
    levels = backlog.settings.levels
    for container in iter_containers(backlog.containers):
        if container.id == step and container.level == levels[-2]:
            return container
    raise TaskEditError("unknown_step", f"unknown {levels[-2]} '{step}'", id=step)


_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._]*$")


def compose_id(parent_id: str | None, code: str) -> str:
    """The full id of a new node from its short code: ``S10`` under ``GIRO`` is ``HAIFA-S10``.

    The id is always composed (the parent's id, ``-``, the code); a code that already
    carries the parent's prefix is taken without it. A code is letters, digits, ``.``
    and ``_``, without ``-``.
    """
    value = code.strip()
    if parent_id and value.startswith(f"{parent_id}-"):
        value = value[len(parent_id) + 1 :]
    if not _CODE_RE.match(value):
        raise TaskEditError(
            "invalid_id",
            f"invalid code '{code}': letters, digits, '.' and '_' (e.g. S10, T05)",
            id=code,
        )
    return f"{parent_id}-{value}" if parent_id else value


def _next_id(container: Container, step: str, reserved_ids: Iterable[str] = ()) -> str:
    pattern = re.compile(rf"^{re.escape(step)}-T(\d+)$")
    numbers = [
        m.group(1)
        for task_id in [*(t.id for t in container.tasks), *reserved_ids]
        if (m := pattern.match(task_id))
    ]
    n = 1 + max((int(x) for x in numbers), default=0)
    width = max([2, *(len(x) for x in numbers)])
    return f"{step}-T{n:0{width}d}"


def _read(files: BacklogFiles, task: Task) -> str:
    return files.read(task.path)


def _apply(text: str, fn: str, key: str, value: str | list[str] | bool | int | None = None) -> str:
    try:
        if fn == "set":
            assert value is not None
            return set_field(text, key, value)
        return remove_field(text, key)
    except ValueError as exc:
        raise TaskEditError("invalid_value", f"cannot edit field '{key}': {exc}") from exc
