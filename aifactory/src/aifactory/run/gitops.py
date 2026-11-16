"""The git calls a task run makes in the main checkout and in its worktree."""

from __future__ import annotations

import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from aifactory import gitquery, gitrun
from aifactory.run.errors import TaskRunError

BRANCH_PREFIX = "factory/"


class _Branches(Protocol):
    def branches(self, task_id: str) -> list[str]: ...


def git(root: Path, *args: str) -> str:
    """Run git in `root`; return stripped stdout, raise ``gitrun.GitCommandFailed`` (a
    RuntimeError carrying stderr) on failure."""
    return gitrun.out(root, *args)


def common_dir(root: Path) -> Path | None:
    """The absolute git common dir of the repo at ``root`` (None outside a repo); cached."""
    found = gitquery.dirs(root)
    return None if found is None else found.common_dir


def git_ok(root: Path, *args: str) -> bool:
    return gitrun.ok(root, *args)


def main_root(repo: Path) -> Path:
    """The top of the checkout that contains `repo` (cached)."""
    try:
        top = gitquery.toplevel(repo, required=True)
    except (RuntimeError, OSError) as exc:
        raise TaskRunError("invalid_config", f"{repo} is not a git repository: {exc}") from exc
    assert top is not None
    return top


def ensure_excluded(root: Path, lines: Iterable[str]) -> None:
    """Add `lines` to ``info/exclude`` of the shared ``.git`` unless present (idempotent)."""
    common = common_dir(root)
    if common is None:
        raise RuntimeError(f"git rev-parse --git-common-dir failed: {root} is not a repository")
    exclude = common / "info" / "exclude"
    existing = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    present = set(existing.splitlines())
    missing = [line for line in dict.fromkeys(lines) if line and line not in present]
    if not missing:
        return
    exclude.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    exclude.write_text(
        existing + prefix + "".join(f"{m}\n" for m in missing), encoding="utf-8", newline="\n"
    )


def ensure_untracked_cache(root: Path) -> None:
    """Set ``core.untrackedCache=true`` in the repo config unless any config sets it.

    The cache spares git a walk of the whole tree on each untracked-files check. An
    existing value (from any config scope) is kept. ``core.fsmonitor`` stays off: its
    daemon keeps folders open on Windows and would block removing worktrees.
    """
    if git(root, "config", "--default", "", "--get", "core.untrackedCache") == "":
        git(root, "config", "--local", "core.untrackedCache", "true")


def next_branch(root: Path, task_id: str, store: _Branches) -> str:
    """``factory/<task-id>-<n>``, one above every existing branch and recorded run."""
    stem = f"{BRANCH_PREFIX}{task_id}-"
    names = git(root, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{stem}*")
    highest = 0
    for name in [*names.splitlines(), *store.branches(task_id)]:
        name = name.strip()
        suffix = name.removeprefix(stem)
        if name.startswith(stem) and suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{stem}{highest + 1}"


def _commit_dirs(root: Path, sha: str, pattern: str) -> list[str]:
    """Directories of commit `sha` that `pattern` names (``*`` stays inside one part)."""
    from fnmatch import fnmatchcase

    found = [""]
    for part in [p for p in pattern.strip("/").split("/") if p and p != "."]:
        nxt: list[str] = []
        if not any(ch in part for ch in "*?["):
            paths = [f"{prefix}/{part}" if prefix else part for prefix in found]
            present = gitquery.exists(root, [f"{sha}:{path}" for path in paths])
            found = [path for path in paths if present.get(f"{sha}:{path}")]
            continue
        for prefix in found:
            tree = f"{sha}:{prefix}" if prefix else sha
            try:
                out = git(root, "ls-tree", "-d", "-z", "--name-only", tree)
            except RuntimeError:
                continue
            for name in sorted(n for n in out.split("\0") if n):
                if name.startswith(".") and not part.startswith("."):
                    continue
                if fnmatchcase(name, part):
                    nxt.append(f"{prefix}/{name}" if prefix else name)
        found = nxt
    return found


def extract_backlog(root: Path, sha: str, backlog_dirs: str | Iterable[str], dest: Path) -> None:
    """Write the backlog roots as they are in commit `sha` under `dest` (nothing if absent).

    `backlog_dirs` is one root or the configured patterns (``ProjectSettings.backlog_patterns``).
    """
    patterns = [backlog_dirs] if isinstance(backlog_dirs, str) else list(backlog_dirs)
    rels: list[str] = []
    for pattern in patterns:
        rel = pattern.strip("/").removeprefix("./") or "."
        if rel == ".":
            matches = ["."]
        else:
            matches = _commit_dirs(root, sha, rel)
        rels.extend(m for m in matches if m not in rels)
    if not rels:
        return
    archive = dest / ".base.tar"
    data = gitrun.run(root, "archive", "--format=tar", sha, "--", *rels, check=True).stdout
    archive.write_bytes(data)
    with tarfile.open(archive) as tar:
        tar.extractall(dest, filter="data")
    archive.unlink()


def head(path: Path) -> str | None:
    """The commit `HEAD` points to in `path`, or None."""
    try:
        return git(path, "rev-parse", "HEAD")
    except (RuntimeError, OSError):
        return None


def symbolic_head(path: Path) -> str | None:
    """The branch `HEAD` is on in `path` (``refs/heads/...``), or None when detached."""
    try:
        return git(path, "symbolic-ref", "-q", "HEAD")
    except (RuntimeError, OSError):
        return None


def _stdout(root: Path, *args: str) -> str:
    # unquoted paths: core.quotePath would octal-escape "Testovací sady/"
    proc = gitrun.run(root, "-c", "core.quotePath=false", *args)
    return proc.text if proc.ok else ""


def fingerprint(root: Path) -> dict[str, str]:
    """What `permissions.snapshot` records, for any checkout: numstat per tracked change,
    ``untracked`` per new file (gitignored files are invisible)."""
    prints: dict[str, str] = {}
    for line in _stdout(root, "diff", "HEAD", "--numstat").splitlines():
        fields = line.split("\t")
        if len(fields) >= 3:
            prints[fields[-1].strip()] = f"{fields[0]},{fields[1]}"
    for path in _stdout(root, "ls-files", "--others", "--exclude-standard").splitlines():
        if path.strip():
            prints[path.strip()] = "untracked"
    return prints


def pr_changed_files(
    main: Path, base: str, branch: str, base_sha: str | None = None
) -> list[str] | None:
    """The files `branch` changes since it left `base` (its pull request's diff), sorted.

    The merge base of ``refs/heads/<base>`` (else `base_sha`) and ``refs/heads/<branch>``
    in the main checkout; None when git cannot tell (a missing branch, ...). Never raises.
    """
    head_ref = f"refs/heads/{branch}"
    try:
        if git_ok(main, "rev-parse", "--verify", "-q", f"refs/heads/{base}"):
            start = git(main, "merge-base", f"refs/heads/{base}", head_ref)
        elif base_sha:
            start = git(main, "merge-base", base_sha, head_ref)
        else:
            return None
        proc = gitrun.run(main, "diff", "--name-only", "-z", start, head_ref)
    except (RuntimeError, OSError):
        return None
    if proc.returncode != 0:
        return None
    names = proc.stdout.decode("utf-8", errors="replace").split("\0")
    return sorted({n for n in names if n})


# ── read-only layout of a repository the dashboard does not know yet ────────────────

READ_ONLY_ENV = gitrun.READ_ONLY_ENV


def read_git(path: Path, *args: str) -> str | None:
    """Stripped stdout of a read-only git call in ``path``, or None when it fails.

    Runs with ``GIT_OPTIONAL_LOCKS=0`` so a read never refreshes the index. The dashboard
    calls only ``rev-parse``, ``cat-file``, ``ls-tree``, ``for-each-ref`` and
    ``remote get-url`` through it in a repository it has not registered.
    """
    try:
        return gitrun.try_out(path, *args, read_only=True)
    except OSError:
        return None


@dataclass(frozen=True)
class RepoLayout:
    """Where ``path`` sits in git: bare, inside ``.git``, the git dirs and the top level."""

    bare: bool
    inside_git_dir: bool
    git_dir: Path
    common_dir: Path
    toplevel: Path | None

    @property
    def linked(self) -> bool:
        """A linked worktree (``git worktree add``): its git dir is not the common dir."""
        return self.git_dir.resolve() != self.common_dir.resolve()

    @property
    def main_checkout(self) -> Path | None:
        """The main checkout of a linked worktree when its common dir is a ``.git``."""
        common = self.common_dir.resolve()
        return common.parent if common.name == ".git" else None


def repo_layout(path: Path) -> RepoLayout | None:
    """The git layout of ``path`` (``rev-parse`` only), or None when it is in no repository."""
    out = read_git(
        path,
        "rev-parse",
        "--path-format=absolute",
        "--is-bare-repository",
        "--is-inside-git-dir",
        "--git-dir",
        "--git-common-dir",
    )
    if out is None:
        return None
    lines = out.splitlines()
    if len(lines) < 4:
        return None
    bare = lines[0].strip() == "true"
    inside = lines[1].strip() == "true"
    toplevel: Path | None = None
    if not bare and not inside:
        try:
            toplevel = gitquery.toplevel(path)
        except OSError:
            toplevel = None
    return RepoLayout(
        bare=bare,
        inside_git_dir=inside,
        git_dir=Path(lines[2].strip()),
        common_dir=Path(lines[3].strip()),
        toplevel=toplevel,
    )
