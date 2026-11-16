"""What approve, return, publish, resolve and sync share: the repository context with
``ReviewError`` for its failures, the checked PR, catching up ``base`` and the worktrees
of a PR branch. ``clean_worktrees`` (``factory task clean``) runs ``run.cleanup``.
"""

from __future__ import annotations

import datetime
import tempfile
from pathlib import Path

from aifactory import gitops
from aifactory.backlog.taskfile import run_entry
from aifactory.config import ConfigError, load_run_config
from aifactory.errors import TaskRunError
from aifactory.providers import (
    CLOSED,
    MERGED,
    OPEN,
    GitProvider,
    ProviderError,
    PrStatus,
    get_provider,
)
from aifactory.providers import git as pgit
from aifactory.review.errors import ReviewError
from aifactory.review.prbody import branch_cost
from aifactory.run.cleanup import CleanResult, RunContext, clean, cleanup_root, record_pr_state
from aifactory.run.store import RUNNING, SUCCEEDED, TaskPrRow, TaskRunStore
from aifactory.workflow.parse import CI_FIX_WORKFLOW, RESOLVE_REVIEWED_WORKFLOW, RESOLVE_WORKFLOW


def _context(repo: Path, provider: GitProvider | None) -> RunContext:
    try:
        main = gitops.main_root(repo)
    except TaskRunError as exc:
        raise ReviewError(exc.code, exc.message) from exc
    try:
        rc = load_run_config(main)
    except ConfigError as exc:
        raise ReviewError("invalid_config", str(exc)) from exc
    if provider is None:
        try:
            provider = get_provider(rc.config.settings, main)
        except ProviderError as exc:
            raise ReviewError(exc.code, exc.message) from exc
    try:
        store = TaskRunStore(rc.local.trace_db_path(main))
    except TaskRunError as exc:
        raise ReviewError(exc.code, exc.message) from exc
    return RunContext(main, rc, store, provider)


def _checked_pr(ctx: RunContext, task_id: str) -> tuple[TaskPrRow, PrStatus]:
    """The task's newest PR if it is open at the provider; raise ``ReviewError`` otherwise."""
    row = ctx.store.latest_pr(task_id)
    if row is None:
        raise ReviewError("no_pr", f"task {task_id} has no pull request, run it first")
    if row.state != OPEN:
        raise ReviewError("pr_not_open", f"PR {row.url} is {row.state}")
    busy = ctx.store.running(task_id)
    if busy is not None:
        raise ReviewError(
            "already_running", f"task {task_id} is running: run {busy.run_id} on {busy.branch}"
        )
    status = ctx.provider.status(row.request())
    record_pr_state(ctx, row, status)
    if status.state == CLOSED:
        raise ReviewError("pr_not_open", f"PR {row.url} is closed")
    if status.state == MERGED:
        raise ReviewError(
            "pr_not_open",
            f"PR {row.url} was merged outside factory; `factory backlog sync` marks it done",
        )
    return row, status


def _catch_up_base(ctx: RunContext, *, strict: bool = False) -> list[str]:
    """Catch up base; resolve must fail when the remote base cannot be used."""
    settings = ctx.rc.config.settings
    base, remote, main = settings.base, settings.remote, ctx.main
    try:
        if not pgit.has_remote(main, remote):
            return []
        pgit.fetch(main, remote, base)
        theirs = pgit.rev_parse(main, f"refs/remotes/{remote}/{base}") or pgit.rev_parse(
            main, "FETCH_HEAD"
        )
        ours = pgit.rev_parse(main, f"refs/heads/{base}")
        if theirs is None or ours is None:
            raise ProviderError("unknown_base", f"cannot read {base} or {remote}/{base}")
        if theirs == ours:
            return []
        if pgit.is_ancestor(main, ours, theirs):
            pgit.advance_branch(main, base, theirs, ours, command="task approve")
            return []
        if pgit.is_ancestor(main, theirs, ours):
            return []
        raise ProviderError("base_diverged", f"base {base} and {remote}/{base} have diverged")
    except ProviderError as exc:
        if strict:
            raise ReviewError(
                exc.code, f"base cannot be brought up to date: {exc.message}"
            ) from exc
        return [f"base not updated: {exc.code}: {exc.message}"]
    except (RuntimeError, OSError) as exc:
        if strict:
            raise ReviewError("pull_failed", f"base cannot be brought up to date: {exc}") from exc
        return [f"base not updated: {exc}"]


def _remove(ctx: RunContext, path: Path, removed: list[str], warnings: list[str]) -> None:
    try:
        if pgit.remove_worktree(ctx.main, path):
            removed.append(str(path))
    except (ProviderError, RuntimeError, OSError) as exc:
        warnings.append(f"worktree {path} not removed: {exc}")


def _remove_run_worktrees(ctx: RunContext, branch: str) -> tuple[list[str], list[str]]:
    removed: list[str] = []
    warnings: list[str] = []
    runs = ctx.store.runs_on_branch(branch)
    active = {Path(r.worktree).resolve() for r in runs if r.state == RUNNING}
    for r in runs:
        if Path(r.worktree).resolve() in active:
            continue
        _remove(ctx, Path(r.worktree), removed, warnings)
    return removed, warnings


def _main_checkout_error(ctx: RunContext, branch: str) -> ReviewError:
    return ReviewError(
        "branch_checked_out",
        f"{branch} is checked out in the main checkout {ctx.main}, switch away first",
    )


def _worktrees_dir(ctx: RunContext) -> str:
    return ctx.rc.config.settings.worktrees_dir.strip("/").removeprefix("./")


def _branch_worktree(ctx: RunContext, branch: str, prefix: str) -> tuple[Path, Path | None]:
    """``(worktree, temp)``: where `branch` is checked out, or a temporary worktree on it."""
    main = ctx.main
    worktree = pgit.checked_out_in(main, branch)
    if worktree is not None and worktree.resolve() == main.resolve():
        raise _main_checkout_error(ctx, branch)
    if worktree is not None and worktree.is_dir():
        return worktree, None
    worktrees_dir = _worktrees_dir(ctx)
    temp = main / worktrees_dir / f"{prefix}-{branch.replace('/', '-')}"
    pgit.remove_worktree(main, temp)
    gitops.ensure_excluded(main, ["/" + worktrees_dir + "/"])
    temp.parent.mkdir(parents=True, exist_ok=True)
    gitops.git(main, *pgit.PARALLEL_CHECKOUT, "worktree", "add", "--quiet", str(temp), branch)
    return temp, temp


def _done_entry(ctx: RunContext, row: TaskPrRow) -> str:
    """The ``## Běhy`` line for the PR `row`: workflow of its last successful run and the cost."""
    runs = ctx.store.runs_on_branch(row.branch)
    done = [r for r in runs if r.state == SUCCEEDED and r.workflow]
    # A resolve or ci-fix run only rebased or repaired the work; the line names the
    # workflow that did it.
    resolves = (RESOLVE_WORKFLOW, RESOLVE_REVIEWED_WORKFLOW, CI_FIX_WORKFLOW)
    workflow = next((r.workflow for r in done if r.workflow not in resolves), None)
    if workflow is None:
        workflow = next((r.workflow for r in done), None)
    cost, _ = branch_cost(ctx.store.db_path, [r.run_id for r in runs])
    return run_entry(datetime.date.today().isoformat(), workflow or "?", row.url, cost)


def _dirty(path: Path) -> bool:
    return bool(gitops.git(path, "status", "--porcelain"))


def _free_branch(ctx: RunContext, row: TaskPrRow) -> list[str]:
    """Remove every worktree of the PR's branch so a new run can check it out.

    Refuses when the branch is checked out in the main checkout or a worktree
    of it has uncommitted changes.
    """
    worktrees = [Path(r.worktree) for r in ctx.store.runs_on_branch(row.branch)]
    other = pgit.checked_out_in(ctx.main, row.branch)
    if other is not None and other.resolve() == ctx.main.resolve():
        raise _main_checkout_error(ctx, row.branch)
    if other is not None and other not in worktrees:
        worktrees.append(other)
    warnings: list[str] = []
    for path in worktrees:
        if not path.is_dir():
            continue
        # A failed git worktree remove can erase its metadata before failing to
        # delete locked files on Windows. Preserve the remainder, never run git
        # inside this broken checkout (it may even discover the main checkout).
        top = gitops.read_git(path, "rev-parse", "--show-toplevel")
        if top is None or Path(top).resolve() != path.resolve():
            if path.is_symlink() or path.resolve().parent != cleanup_root(ctx):
                raise ReviewError("worktree_remove_failed", f"unsafe incomplete worktree: {path}")
            from aifactory.worktree_processes import stop_directory_processes

            try:
                stop_directory_processes(path)
            except OSError as exc:
                raise ReviewError("worktree_remove_failed", f"{path}: {exc}") from exc
            recovery = ctx.main / ".factory/recovered-worktrees"
            gitops.ensure_excluded(ctx.main, ["/.factory/recovered-worktrees/"])
            recovery.mkdir(parents=True, exist_ok=True)
            saved = Path(tempfile.mkdtemp(prefix=f"{path.name}-", dir=recovery)) / path.name
            path.rename(saved)
            pgit.git(ctx.main, "worktree", "prune")
            warnings.append(f"incomplete worktree {path}: remaining files preserved in {saved}")
        elif _dirty(path):
            raise ReviewError(
                "dirty_worktree", f"{path} has uncommitted changes, commit or drop them"
            )
    for path in worktrees:
        pgit.remove_worktree(ctx.main, path)
    return warnings


def clean_worktrees(repo: Path, *, provider: GitProvider | None = None) -> CleanResult:
    """Clean finished runs, unused factory branches and orphan directories."""
    ctx = _context(repo, provider)
    try:
        return clean(ctx)
    finally:
        ctx.store.close()
