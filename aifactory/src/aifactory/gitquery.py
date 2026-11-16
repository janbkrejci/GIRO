"""Cached git queries and batch commands on top of ``aifactory.gitrun``.

Use these instead of one git process per file or per repeated question.

Caches (per process, thread-safe enough for the dashboard: a lost race only repeats a
query). Each entry is checked against cheap ``stat`` calls before it is used, so a change
made by another process (a commit, ``git remote set-url``) is seen at once:

- ``toplevel(path)``: the top of the checkout containing ``path``; kept while
  ``<top>/.git`` exists.
- ``dirs(path)``: the git dir and the common dir (``rev-parse --git-dir --git-common-dir``).
- ``resolve(root, ref)``: the commit sha ``rev-parse --verify <ref>^{commit}`` gives; kept
  while the loose ref files ``ref`` may name and ``packed-refs`` are unchanged (mtime, size,
  inode). ``HEAD``, other pseudo-refs, revision expressions and a repository with the
  reftable backend are never cached. ``ref_files(root, ref)`` lists those files.
- ``remote_url(root, remote)``: ``remote get-url``; kept while the repository's
  ``config`` (and ``config.worktree``) are unchanged.
- ``merge_base(root, a, b)``: ``merge-base`` of two commit shas; kept for good, the
  answer of two given commits never changes.

``clear_caches()`` forgets everything (tests).

Batch commands (one git process each, whatever the number of items; every read runs with
``GIT_OPTIONAL_LOCKS=0``):

- ``status(root)``: the working tree state from ``git status --porcelain=v2 -z
  --untracked-files=all --no-renames``; ``status_head(root)`` adds the ``HEAD`` commit
  (``--branch``) in the same process.
- ``object_info(root, names)`` (``cat-file --batch-check``), ``exists(root, names)`` and
  ``read_objects(root, names)`` (``cat-file --batch``). Names are anything ``cat-file``
  accepts (``<sha>``, ``<rev>:<path>``) but must not contain a newline: git 2.37 has no
  ``-z`` for batch input. ``--batch --filters`` with ``<rev>:<path>`` names is broken in
  git 2.37 ("missing path"), so this module offers no filtered batch read.
- ``ls_tree(root, treeish, paths)``: the entries of several paths in one ``ls-tree``
  (split only when the command line would get too long).
- ``update_index(root, changes, env=...)``: several index entries in one
  ``update-index -z --index-info``.
- ``read_filtered(root, items)``: blob contents as a checkout writes them (attribute
  filters such as CRLF conversion applied) in one ``cat-file --batch --filters``; the
  path comes in ``%(rest)`` (git 2.37 has no ``<rev>:<path>`` form there). When the batch
  fails, one ``cat-file --filters`` per blob.
- ``head_state(root)``: the ``HEAD`` commit and its branch in one ``rev-parse``.
- ``write_blobs(root, contents)``: store several blobs in one ``fast-import``;
  ``blob_id(content, like=sha)`` computes a blob id without git.

Failures raise ``gitrun.GitCommandFailed`` unless a function says otherwise.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aifactory import gitrun

# ── caches ─────────────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_TOPLEVELS: dict[str, Path] = {}
_DIRS: dict[str, GitDirs] = {}
_REFS: dict[tuple[str, str], tuple[tuple[object, ...], str]] = {}
_REMOTES: dict[tuple[str, str], tuple[tuple[object, ...], str | None]] = {}
_MERGE_BASES: dict[tuple[str, str, str], str] = {}
_CACHE_LIMIT = 4096


def clear_caches() -> None:
    """Forget every cached answer."""
    with _lock:
        _TOPLEVELS.clear()
        _DIRS.clear()
        _REFS.clear()
        _REMOTES.clear()
        _MERGE_BASES.clear()


def _remember(cache: dict[Any, Any], key: object, value: object) -> None:
    with _lock:
        if len(cache) >= _CACHE_LIMIT:
            cache.pop(next(iter(cache)), None)
        cache[key] = value


def stamp(path: Path) -> tuple[int, int, int] | None:
    """What changes when git rewrites ``path`` (it replaces files by renaming a lock file)."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size, info.st_ino)


def toplevel(path: Path | str, *, required: bool = False) -> Path | None:
    """The resolved top of the checkout containing ``path``; None outside one.

    With ``required`` a path outside a checkout raises ``gitrun.GitCommandFailed``.
    """
    key = str(path)
    cached = _TOPLEVELS.get(key)
    if cached is not None and os.path.lexists(cached / ".git"):
        return cached
    result = gitrun.run(path, "rev-parse", "--show-toplevel", read_only=True)
    if not result.ok or not result.out:
        if required:
            raise gitrun.GitCommandFailed(result, Path(path))
        return None
    top = Path(result.out).resolve()
    _remember(_TOPLEVELS, key, top)
    return top


@dataclass(frozen=True)
class GitDirs:
    """Absolute ``--git-dir`` (per worktree) and ``--git-common-dir`` (shared refs, config)."""

    git_dir: Path
    common_dir: Path


def dirs(path: Path | str) -> GitDirs | None:
    """The git dirs of the repository at ``path``; None outside a repository."""
    key = str(path)
    cached = _DIRS.get(key)
    if cached is not None and os.path.isdir(cached.git_dir):
        return cached
    result = gitrun.run(
        path,
        "rev-parse",
        "--path-format=absolute",
        "--git-dir",
        "--git-common-dir",
        read_only=True,
    )
    lines = result.text.splitlines() if result.ok else []
    if len(lines) < 2:
        return None
    found = GitDirs(Path(lines[0].strip()), Path(lines[1].strip()))
    _remember(_DIRS, key, found)
    return found


# Characters that make a ref argument a revision expression rather than a ref name.
_EXPRESSION = set("^~:@{}?*[\\ ")
# Per-worktree refs (in the git dir, not the common dir).
_PER_WORKTREE = ("refs/bisect/", "refs/worktree/", "refs/rewritten/")


def _ref_files(found: GitDirs, ref: str) -> list[Path] | None:
    """The files that decide what ``ref`` resolves to; None when it is not cacheable."""
    if not ref or ref.endswith("/HEAD") or ref.endswith(".lock") or _EXPRESSION & set(ref):
        return None
    if (found.common_dir / "reftable").exists():
        return None
    if ref.startswith("refs/"):
        if ref.startswith(_PER_WORKTREE):
            return None
        names = [ref]
    else:
        if ref.isupper() or ref.startswith(tuple(p.removeprefix("refs/") for p in _PER_WORKTREE)):
            return None  # HEAD, FETCH_HEAD, ... are files in the git dir that change often
        # The rule list of git-rev-parse(1) "SPECIFYING REVISIONS", minus <ref> in $GIT_DIR.
        names = [
            f"refs/{ref}",
            f"refs/tags/{ref}",
            f"refs/heads/{ref}",
            f"refs/remotes/{ref}",
            f"refs/remotes/{ref}/HEAD",
        ]
        if (found.git_dir / ref).exists():
            return None
    files = [found.common_dir / name for name in names]
    if not ref.startswith("refs/") and files[-1].exists():
        return None  # a symbolic remote HEAD: its target can move without the file changing
    return [*files, found.common_dir / "packed-refs"]


def ref_files(root: Path | str, ref: str) -> list[Path] | None:
    """The files whose stamps change when ``ref`` moves; None when that is not known."""
    found = dirs(root)
    return _ref_files(found, ref) if found is not None else None


def resolve(root: Path | str, ref: str) -> str | None:
    """The commit ``ref`` names (``rev-parse --verify --quiet <ref>^{commit}``), or None."""
    found = dirs(root)
    files = _ref_files(found, ref) if found is not None else None
    key = (str(found.common_dir if found else root), ref)
    current: tuple[object, ...] | None = None
    if files is not None:
        current = tuple(stamp(f) for f in files)
        cached = _REFS.get(key)
        if cached is not None and cached[0] == current:
            return cached[1]
    result = gitrun.run(
        root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", read_only=True
    )
    if not result.ok or not result.out:
        return None
    if current is not None:
        _remember(_REFS, key, (current, result.out))
    return result.out


def head_state(root: Path | str) -> tuple[str | None, str | None]:
    """(``HEAD`` commit, branch ``refs/heads/...``) of the checkout; None for each unknown.

    One ``rev-parse HEAD --symbolic-full-name HEAD``. A detached ``HEAD`` has no branch;
    an unborn branch (no commit yet) costs a second process (``symbolic-ref``).
    """
    result = gitrun.run(root, "rev-parse", "HEAD", "--symbolic-full-name", "HEAD", read_only=True)
    lines = result.text.split()
    if result.ok and len(lines) == 2:
        ref = lines[1] if lines[1].startswith("refs/") else None
        return lines[0], ref
    branch = gitrun.run(root, "symbolic-ref", "-q", "HEAD", read_only=True)
    return None, branch.out if branch.ok and branch.out else None


def remote_url(root: Path | str, remote: str) -> str | None:
    """``git remote get-url <remote>``, or None when there is no such remote."""
    found = dirs(root)
    current: tuple[object, ...] | None = None
    key = (str(found.git_dir if found else root), remote)
    if found is not None:
        current = (stamp(found.common_dir / "config"), stamp(found.git_dir / "config.worktree"))
        cached = _REMOTES.get(key)
        if cached is not None and cached[0] == current:
            return cached[1]
    result = gitrun.run(root, "remote", "get-url", remote, read_only=True)
    url = result.out if result.ok and result.out else None
    if current is not None:
        _remember(_REMOTES, key, (current, url))
    return url


# ── working tree state ───────────────────────────────────────────────────────────────


_SHA = frozenset("0123456789abcdef")


def merge_base(root: Path | str, a: str, b: str) -> str | None:
    """The merge base of commits `a` and `b` (``git merge-base``), or None without one.

    Cached when both are full commit shas (an answer that cannot change); any other
    revision is asked every time.
    """
    found = dirs(root)
    key = (str(found.common_dir if found else root), a, b)
    pure = all(len(sha) in (40, 64) and set(sha) <= _SHA for sha in (a, b))
    if pure:
        cached = _MERGE_BASES.get(key)
        if cached is not None:
            return cached
    result = gitrun.run(root, "merge-base", a, b, read_only=True)
    if not result.ok or not result.out:
        return None
    if pure:
        _remember(_MERGE_BASES, key, result.out)
    return result.out


@dataclass(frozen=True)
class StatusEntry:
    """One path of ``git status --porcelain=v2``.

    ``kind`` is ``changed`` (``1``), ``unmerged`` (``u``) or ``untracked`` (``?``).
    ``index`` and ``worktree`` are the two status letters (``.`` = unchanged; ``?`` for
    an untracked file).
    """

    path: str
    kind: str
    index: str
    worktree: str


def status(root: Path | str, *, untracked: bool = True) -> list[StatusEntry]:
    """Every path that differs from ``HEAD`` in index or tree, plus untracked files.

    Renames are reported as a deletion and an addition (``--no-renames``); ignored files
    are not listed. Paths are decoded with ``surrogateescape``.
    """
    return _status(root, untracked=untracked, branch=False)[1]


def status_head(root: Path | str) -> tuple[str | None, list[StatusEntry]]:
    """``(HEAD commit, status(root))`` from one ``git status --branch``.

    The commit is None for an unborn branch.
    """
    return _status(root, untracked=True, branch=True)


def _status(
    root: Path | str, *, untracked: bool, branch: bool
) -> tuple[str | None, list[StatusEntry]]:
    result = gitrun.run(
        root,
        "status",
        "--porcelain=v2",
        "-z",
        *(["--branch"] if branch else []),
        "--untracked-files=all" if untracked else "--untracked-files=no",
        "--no-renames",
        read_only=True,
        check=True,
    )
    head: str | None = None
    entries: list[StatusEntry] = []
    for record in result.z():
        tag = record[:1]
        if tag == "#":
            if record.startswith("# branch.oid ") and record[13:] != "(initial)":
                head = record[13:]
        elif tag == "?":
            entries.append(StatusEntry(record[2:], "untracked", "?", "?"))
        elif tag == "1":
            # 1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
            fields = record.split(" ", 8)
            if len(fields) == 9:
                entries.append(StatusEntry(fields[8], "changed", fields[1][0], fields[1][1]))
        elif tag == "u":
            # u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
            fields = record.split(" ", 10)
            if len(fields) == 11:
                entries.append(StatusEntry(fields[10], "unmerged", fields[1][0], fields[1][1]))
    return head, entries


def dirty_paths(root: Path | str) -> set[str]:
    """The paths ``status`` lists."""
    return {entry.path for entry in status(root)}


# ── objects ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ObjectInfo:
    sha: str
    type: str
    size: int


def _batch_input(names: Sequence[str]) -> bytes:
    for name in names:
        if "\n" in name:
            raise ValueError(f"object name with a newline: {name!r}")
    return "".join(f"{name}\n" for name in names).encode("utf-8", "surrogateescape")


def object_info(root: Path | str, names: Iterable[str]) -> dict[str, ObjectInfo | None]:
    """Type and size of every object ``names`` name (None when missing): one process."""
    wanted = list(dict.fromkeys(names))
    if not wanted:
        return {}
    result = gitrun.run(
        root,
        "cat-file",
        "--batch-check",
        input=_batch_input(wanted),
        read_only=True,
        check=True,
    )
    lines = result.stdout.decode("utf-8", "surrogateescape").split("\n")
    found: dict[str, ObjectInfo | None] = {}
    for name, line in zip(wanted, lines, strict=False):
        parts = line.split(" ")
        if len(parts) == 3 and parts[2].isdigit():
            found[name] = ObjectInfo(parts[0], parts[1], int(parts[2]))
        else:
            found[name] = None  # "<name> missing" or "<name> ambiguous"
    return found


def exists(root: Path | str, names: Iterable[str]) -> dict[str, bool]:
    """Whether each name names an object (``cat-file -e`` for many names at once)."""
    return {name: info is not None for name, info in object_info(root, names).items()}


def read_objects(root: Path | str, names: Iterable[str]) -> dict[str, bytes | None]:
    """The raw content of every object (None when missing): one ``cat-file --batch``."""
    wanted = list(dict.fromkeys(names))
    if not wanted:
        return {}
    result = gitrun.run(
        root, "cat-file", "--batch", input=_batch_input(wanted), read_only=True, check=True
    )
    data = result.stdout
    found: dict[str, bytes | None] = {}
    offset = 0
    for name in wanted:
        end = data.find(b"\n", offset)
        if end < 0:
            raise ValueError(f"cat-file --batch: no answer for {name!r}")
        header = data[offset:end].split(b" ")
        if len(header) == 3 and header[2].isdigit():
            size = int(header[2])
            found[name] = data[end + 1 : end + 1 + size]
            offset = end + 1 + size + 1
        else:
            found[name] = None
            offset = end + 1
    return found


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    type: str
    sha: str


# Characters of paths per ``ls-tree`` command line (Windows limits a line to 32767).
_ARG_BUDGET = 24_000


def ls_tree(root: Path | str, treeish: str, paths: Iterable[str]) -> dict[str, TreeEntry]:
    """The entries of ``paths`` (files or directories) in ``treeish``; absent paths left out.

    One ``ls-tree -z --full-tree`` per ``_ARG_BUDGET`` characters of paths. A directory
    asked for together with a path inside it is not listed itself (git descends into it).
    """
    wanted = [p.strip("/") for p in dict.fromkeys(paths) if p.strip("/")]
    found: dict[str, TreeEntry] = {}
    chunk: list[str] = []
    size = 0
    for path in [*wanted, None]:
        if path is not None and (size + len(path) < _ARG_BUDGET or not chunk):
            chunk.append(path)
            size += len(path) + 1
            continue
        if chunk:
            result = gitrun.run(
                root,
                "ls-tree",
                "-z",
                "--full-tree",
                treeish,
                "--",
                *chunk,
                read_only=True,
                check=True,
            )
            asked = set(chunk)
            for record in result.z():
                meta, _, name = record.partition("\t")
                parts = meta.split()
                if name in asked and len(parts) == 3:
                    found[name] = TreeEntry(parts[0], parts[1], parts[2])
        chunk, size = ([path], len(path) + 1) if path is not None else ([], 0)
    return found


# ── index ────────────────────────────────────────────────────────────────────────────

ZERO_SHA = "0" * 40

# (path, mode, blob); mode and blob None = remove the path from the index
IndexChange = tuple[str, str | None, str | None]


def update_index(
    root: Path | str, changes: Sequence[IndexChange], *, env: Mapping[str, str] | None = None
) -> None:
    """Apply ``changes`` to the index (``GIT_INDEX_FILE`` in ``env``) in one process.

    ``git update-index -z --index-info``: an entry is added or replaced, a path with mode
    and blob None is removed.
    """
    if not changes:
        return
    records = []
    for path, mode, blob in changes:
        if mode is None or blob is None:
            records.append(f"0 {ZERO_SHA}\t{path}\0")
        else:
            records.append(f"{mode} {blob}\t{path}\0")
    gitrun.run(
        root,
        "update-index",
        "-z",
        "--index-info",
        input="".join(records).encode("utf-8", "surrogateescape"),
        env=env,
        check=True,
    )


# ── blobs ────────────────────────────────────────────────────────────────────────────


def blob_id(content: bytes, *, like: str) -> str:
    """The object id git gives ``content`` as a blob (``hash-object --stdin``), no process.

    ``like`` is any object id of the same repository: its length picks SHA-1 or SHA-256.
    """
    algorithm = hashlib.sha256 if len(like) == 64 else hashlib.sha1
    return algorithm(b"blob %d\0" % len(content) + content).hexdigest()


def write_blobs(root: Path | str, contents: Sequence[bytes]) -> list[str]:
    """Store every content as a blob; return their ids in order. One ``git fast-import``.

    The bytes are stored as given (no attribute filters), like ``hash-object -w --stdin``.
    Below ``fastimport.unpackLimit`` objects fast-import starts ``unpack-objects`` to
    store them loose, as ``hash-object -w`` would: two processes, whatever the number.
    """
    if not contents:
        return []
    stream = bytearray()
    for n, content in enumerate(contents, start=1):
        stream += b"blob\nmark :%d\ndata %d\n" % (n, len(content))
        stream += content + b"\n"
    for n in range(1, len(contents) + 1):
        stream += b"get-mark :%d\n" % n
    stream += b"done\n"
    result = gitrun.run(root, "fast-import", "--quiet", "--done", input=bytes(stream), check=True)
    ids = result.text.split()
    if len(ids) != len(contents):
        raise ValueError(f"git fast-import returned {len(ids)} ids for {len(contents)} blobs")
    return ids


def read_filtered(
    root: Path | str, items: Iterable[tuple[str, str]]
) -> dict[tuple[str, str], bytes | None]:
    """The checkout bytes of each ``(blob sha, path)``: the blob with the attribute filters
    of ``path`` applied (``cat-file --filters``); None when the blob is missing.

    One ``cat-file --batch --filters`` for all items. Its header gives the unfiltered size,
    so every item is followed by a request for a name that cannot exist; its ``missing``
    line marks where the filtered content ends. When the batch fails, one process per item.
    """
    wanted = list(dict.fromkeys(items))
    if not wanted:
        return {}
    for sha, path in wanted:
        if "\n" in path or not sha or " " in sha:
            raise ValueError(f"cannot request {sha!r} {path!r}")
    end = f"giro-end-{secrets.token_hex(16)}"
    request = "".join(f"{sha} {path}\n{end}\n" for sha, path in wanted)
    result = gitrun.run(
        root,
        "cat-file",
        "--batch=%(objectname) %(objectsize) %(rest)",
        "--filters",
        input=request.encode("utf-8", "surrogateescape"),
        read_only=True,
    )
    if result.ok:
        parsed = _split_filtered(result.stdout, wanted, end.encode())
        if parsed is not None:
            return parsed
    return {item: _read_one_filtered(root, *item) for item in wanted}


def _split_filtered(
    data: bytes, wanted: list[tuple[str, str]], end: bytes
) -> dict[tuple[str, str], bytes | None] | None:
    marker = b"\n" + end + b" missing\n"
    found: dict[tuple[str, str], bytes | None] = {}
    offset = 0
    for item in wanted:
        line_end = data.find(b"\n", offset)
        if line_end < 0:
            return None
        header = data[offset:line_end]
        if header.endswith(b" missing"):
            if data[line_end + 1 : line_end + 1 + len(end) + 9] != end + b" missing\n":
                return None
            found[item] = None
            offset = line_end + 1 + len(end) + 9
            continue
        stop = data.find(marker, line_end + 1)
        if stop < 0:
            return None
        # The marker starts with the newline that ends every --batch answer.
        found[item] = data[line_end + 1 : stop]
        offset = stop + len(marker)
    return found


def _read_one_filtered(root: Path | str, sha: str, path: str) -> bytes | None:
    result = gitrun.run(root, "cat-file", "--filters", f"--path={path}", sha, read_only=True)
    return result.stdout if result.ok else None
