"""Follow-up task IDs across concurrent publications.

Two runs may add follow-up tasks with the same new ID on different branches.
``reconcile_ids`` renumbers the colliding follow-ups a branch added, merges the current
target and validates the result; ``advance_pr`` moves the repaired PR branch.
``publication_lock`` serializes publications that may reconcile IDs: it is the trace
DB's cross-process lock, or a lock in the git common directory before a trace DB exists.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator
from pathlib import Path

from aifactory import gitquery
from aifactory.backlog import Task, check_backlog, is_human, iter_tasks
from aifactory.backlog.edit import _next_id
from aifactory.backlog.roots import owning_root
from aifactory.backlog.snapshot import read_snapshot
from aifactory.config import ProjectSettings, load_local
from aifactory.locks import path_lock
from aifactory.providers import ProviderError, git


def follow_up_marker(task_id: str) -> str:
    return f"<!-- factory: follow-up-of={task_id} -->"


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


@contextlib.contextmanager
def publication_lock(root: Path) -> Iterator[None]:
    """Use the run store's reentrant cross-process lock, also before a store exists."""
    db = load_local(root).trace_db_path(root)
    if db.exists():
        lock = db.with_name(db.name + ".lock")
    else:
        common = Path(git.git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        lock = common / "factory-followups.lock"
    with path_lock(lock).hold(600):
        yield


def current_target(root: Path, base: str, settings: ProjectSettings) -> str:
    """Fetch before reading IDs; a failed fetch must never fall back to stale base."""
    if git.has_remote(root, settings.remote):
        git.fetch(root, settings.remote, base)
        target = git.remote_tip(root, settings.remote, base)
    else:
        target = git.rev_parse(root, f"refs/heads/{base}")
    if target is None:
        raise ProviderError("follow_up_collision", f"cannot read target branch {base}")
    return target


def reconcile_ids(
    root: Path, head: str, base: str, settings: ProjectSettings
) -> tuple[str, dict[str, str]]:
    """Repair newly added follow-ups, merge fresh base and validate the resulting backlog.

    Only references changed on this branch are rewritten: a target task with the
    colliding ID continues to mean that target task. Call under publication_lock.
    The returned commit is unpublished; conflicts leave both branches untouched.
    """
    target = current_target(root, base, settings)
    ancestor = git.git(root, "merge-base", head, target)
    changed = git.git(root, "diff", "--name-only", "-z", ancestor, head).split("\0")
    if not any(
        path.lower().endswith(".md") and owning_root(path, settings) is not None for path in changed
    ):
        return head, {}
    ours = read_snapshot(root, head, settings)
    old = read_snapshot(root, ancestor, settings)
    theirs = read_snapshot(root, target, settings)
    branch = ours.load()
    duplicates = [i for i in check_backlog(branch) if i.code == "duplicate_id"]
    if duplicates:
        raise ProviderError("follow_up_collision", "; ".join(i.message for i in duplicates))
    previous = old.load()
    latest = theirs.load()
    additions = [t for t in iter_tasks(branch) if t.id not in previous.by_id]
    if not additions:
        return head, {}
    reserved = set(branch.by_id) | set(latest.by_id)
    renamed: dict[str, str] = {}
    paths: dict[str, str] = {}
    for task in additions:
        taken = latest.by_id.get(task.id)
        if taken is None:
            continue
        # The same follow-up already published by an earlier attempt is not a collision.
        markers = re.findall(r"<!-- factory: follow-up-of=([^ ]+) -->", task.body)
        same = (
            isinstance(taken, Task)
            and taken.path == task.path
            and _norm(taken.title) == _norm(task.title)
            and is_human(taken) == is_human(task)
            and (markers and all(follow_up_marker(m) in taken.body for m in markers))
        )
        if same:
            continue
        if not markers and not task.follows:
            raise ProviderError("follow_up_collision", f"duplicate id {task.id}: not a follow-up")
        step = task.parent.id
        assert step is not None
        new_id = _next_id(task.parent, step, reserved)
        reserved.add(new_id)
        renamed[task.id] = new_id
        task_path = Path(task.path)
        paths[task.path] = task_path.with_name(
            task_path.name.replace(task.id, new_id, 1)
        ).as_posix()

    changes: list[git.TreeChange] = []
    if renamed:
        pattern = re.compile(
            r"(?<![\w.-])(" + "|".join(re.escape(k) for k in renamed) + r")(?![\w.-])"
        )
        before = old.markdown_files()
        rewrites: list[tuple[str, bytes]] = []
        for path, content in ours.markdown_files().items():
            if before.get(path) == content:
                continue
            rewritten = pattern.sub(lambda m: renamed[m.group()], content.decode("utf-8"))
            destination = paths.get(path, path)
            if destination != path:
                changes.append((path, None, None))
            if destination != path or rewritten.encode("utf-8") != content:
                rewrites.append((destination, rewritten.encode()))
        # Every rewritten file is stored by one git process.
        blobs = gitquery.write_blobs(root, [data for _, data in rewrites])
        changes += [
            (destination, "100644", blob)
            for (destination, _), blob in zip(rewrites, blobs, strict=True)
        ]
    fixed = (
        git.commit_tree_with(root, head, changes, "Renumber colliding follow-up IDs")
        if changes
        else head
    )
    if git.is_ancestor(root, target, fixed):
        merged = fixed
    else:
        try:
            merged = git.merge_commit(
                root,
                fixed,
                target,
                "merge",
                "Merge current target and reconcile follow-up IDs",
            )
        except ProviderError as exc:
            raise ProviderError(
                "follow_up_collision", f"cannot merge follow-ups into {base}: {exc}"
            ) from exc
    combined = read_snapshot(root, merged, settings)
    baseline = {(i.code, i.path, i.message) for i in check_backlog(latest)}
    problems = [
        i
        for i in check_backlog(combined.load())
        if i.code == "duplicate_id" or (i.code, i.path, i.message) not in baseline
    ]
    if problems:
        raise ProviderError("follow_up_collision", "; ".join(i.message for i in problems))
    return merged, renamed


def advance_pr(root: Path, branch: str, old: str, new: str) -> None:
    """Move a repaired PR branch without overwriting edits in a checked-out branch."""
    if git.rev_parse(root, f"refs/heads/{branch}") != old:
        raise ProviderError("follow_up_collision", f"{branch} moved during ID reconciliation")
    checkout = git.checked_out_in(root, branch)
    if checkout is not None:
        if git.git(checkout, "status", "--porcelain"):
            raise ProviderError("follow_up_collision", f"{branch} has uncommitted changes")
        git.git(checkout, "merge", "--ff-only", new)
    else:
        git.git(root, "update-ref", f"refs/heads/{branch}", new, old)
