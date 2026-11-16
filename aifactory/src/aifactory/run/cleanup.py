"""Best-effort cleanup of terminal tasks, finished PRs, superseded runs, unused factory
branches and orphan worktree directories (``factory task clean``, after ``task approve``
and automatically after runs).

``RunContext`` is the repository a review or cleanup works on: main checkout, run
configuration, trace store and git provider.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from aifactory.backlog import Task, load_backlog
from aifactory.config import RunConfig
from aifactory.providers import CLOSED, MERGED, OPEN, GitProvider, ProviderError, PrStatus
from aifactory.providers import git as pgit
from aifactory.run.stack import stacked_on
from aifactory.run.store import RUNNING, SUCCEEDED, TaskPrRow, TaskRunRow, TaskRunStore, _now
from aifactory.workflow.parse import RESOLVE_REVIEWED_WORKFLOW, RESOLVE_WORKFLOW


@dataclass
class CleanResult:
    removed: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    deleted_branches: list[str] = field(default_factory=list)
    branch_reasons: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            "ok": True,
            "removed": list(self.removed),
            "deleted_branches": list(self.deleted_branches),
            "branch_reasons": dict(self.branch_reasons),
            "warnings": list(self.warnings),
        }


@dataclass
class RunContext:
    main: Path
    rc: RunConfig
    store: TaskRunStore
    provider: GitProvider


def record_pr_state(ctx: RunContext, row: TaskPrRow, status: PrStatus) -> None:
    """Write a closed or merged provider state into ``task_prs``."""
    if status.state == CLOSED:
        ctx.store.update_pr(row.branch, state=CLOSED)
    elif status.state == MERGED:
        ctx.store.update_pr(
            row.branch,
            state=MERGED,
            merged_at=status.merged_at or _now(),
            merge_sha=status.merge_sha,
        )


def clean(ctx: RunContext, *, protected: frozenset[str] = frozenset()) -> CleanResult:
    # Claim/reclaim and worktree creation use this same cross-process lock.
    try:
        warnings: list[str] = []
        # Hosting/network latency must not hold up claims in another process.
        for open_pr in ctx.store.open_prs():
            try:
                record_pr_state(ctx, open_pr, ctx.provider.status(open_pr.request()))
            except Exception as exc:
                warnings.append(f"PR {open_pr.url}: {exc}")
        remote = ctx.rc.config.settings.remote
        branches = (
            pgit.remote_branches(ctx.main, remote, "factory/")
            if pgit.has_remote(ctx.main, remote)
            else []
        )

        def remote_exists(branch: str) -> bool | None:
            return None if branches is None else branch in branches

        with ctx.store.serialized():
            result = _clean_locked(ctx, protected, remote_exists)
            result.warnings[:0] = warnings
            from aifactory.harness.agent_dirs import sweep_orphans

            sweep_orphans(
                [ctx.main / ctx.rc.config.settings.worktrees_dir],
                [
                    r.worktree
                    for r in ctx.store.all_runs()
                    if r.state == RUNNING or r.branch in protected
                ],
            )
            return result
    except Exception as exc:
        return CleanResult(warnings=[f"cleanup failed: {exc}"])


def _clean_locked(
    ctx: RunContext, protected: frozenset[str], remote_exists: Callable[[str], bool | None]
) -> CleanResult:
    result = CleanResult()
    backlog = load_backlog(ctx.main, ctx.rc.config.settings, create_missing=False)
    terminal = {
        task_id: task.status
        for task_id, task in backlog.by_id.items()
        if isinstance(task, Task) and task.status in ("done", "cancelled")
    }
    runs = ctx.store.all_runs()  # newest first
    newest: dict[str, str] = {}
    for r in runs:
        newest.setdefault(r.task_id, r.run_id)
    active = {
        Path(r.worktree).resolve() for r in runs if r.state == RUNNING or r.branch in protected
    }
    # An open PR protects every path shared with it, even when another record ended.
    active.update(
        Path(r.worktree).resolve()
        for r in runs
        if (pr := ctx.store.pr_for_branch(r.branch)) is not None and pr.state == OPEN
    )
    active.update(
        Path(r.worktree).resolve()
        for r in runs
        if newest.get(r.task_id) == r.run_id
        and r.state in ("failed", "stopped", "aborted")
        and isinstance(task := backlog.by_id.get(r.task_id), Task)
        and task.status == "todo"
    )
    for r in runs:
        path = Path(r.worktree)
        if path.resolve() in active or not path.exists():
            continue
        pr = ctx.store.pr_for_branch(r.branch)
        if r.task_id in terminal:
            reason = terminal[r.task_id]
        elif pr is not None and pr.state in (MERGED, CLOSED):
            reason = pr.state
        elif pr is None and newest.get(r.task_id) != r.run_id:
            reason = "abandoned"
        else:
            continue  # the open PR's worktree, or the task's last run without a PR
        try:
            if r.task_id not in terminal and pgit.git(path, "status", "--porcelain").strip():
                result.warnings.append(f"worktree {path} has uncommitted changes; kept")
                continue
            registered = _registered_worktrees(ctx)
            if path.resolve() in registered:
                pgit.remove_worktree(ctx.main, path)
            else:
                root = cleanup_root(ctx)
                if path.is_symlink() or path.resolve().parent != root:
                    raise RuntimeError("unregistered worktree is outside the worktrees directory")
                from aifactory.worktree_processes import stop_directory_processes

                stop_directory_processes(path)
                shutil.rmtree(path)
        except (ProviderError, RuntimeError, OSError) as exc:
            result.warnings.append(f"worktree {path} not removed: {exc}")
            continue
        result.removed.append(
            {
                "run_id": r.run_id,
                "task_id": r.task_id,
                "branch": r.branch,
                "worktree": str(path),
                "reason": reason,
            }
        )
    branches = {pr.branch: pr.state for pr in ctx.store.done_prs()}
    branches.update({r.branch: "cancelled" for r in runs if terminal.get(r.task_id) == "cancelled"})
    for branch, reason in branches.items():
        if branch in protected:
            continue
        pr = ctx.store.pr_for_branch(branch)
        if pr is not None and pr.state == OPEN:
            continue
        before = len(result.deleted_branches)
        delete_unused_branch(
            ctx, branch, result.deleted_branches, result.warnings, remote_exists=remote_exists
        )
        result.branch_reasons.update({b: reason for b in result.deleted_branches[before:]})
    _clean_orphans(ctx, active, runs, result)
    return result


def _clean_orphans(
    ctx: RunContext, active: set[Path], runs: list[TaskRunRow], result: CleanResult
) -> None:
    try:
        registered = _registered_worktrees(ctx)
        root = cleanup_root(ctx)
        # Known runs are handled above: dirty/retryable paths are never orphans.
        known = {Path(r.worktree).resolve() for r in runs}
        if root.is_dir():
            for path in root.iterdir():
                resolved = path.resolve()
                if (
                    not path.is_dir()
                    or path.is_symlink()
                    or resolved.parent != root
                    or any(
                        resolved == p or resolved in p.parents for p in active | registered | known
                    )
                ):
                    continue
                try:
                    from aifactory.worktree_processes import stop_directory_processes

                    stop_directory_processes(path)
                    shutil.rmtree(path)
                    result.removed.append(
                        {
                            "worktree": str(path),
                            "reason": "orphan",
                            "task_id": "",
                            "run_id": "",
                            "branch": "",
                        }
                    )
                except OSError as exc:
                    result.warnings.append(f"orphan {path} not removed: {exc}")
    except Exception as exc:
        result.warnings.append(f"orphan cleanup failed: {exc}")
    try:
        pgit.git(ctx.main, "worktree", "prune")
    except Exception as exc:
        result.warnings.append(f"worktree prune failed: {exc}")


def _registered_worktrees(ctx: RunContext) -> set[Path]:
    return {
        Path(line.removeprefix("worktree ")).resolve()
        for line in pgit.git(ctx.main, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    }


def cleanup_root(ctx: RunContext) -> Path:
    root = (ctx.main / ctx.rc.config.settings.worktrees_dir).resolve()
    main = ctx.main.resolve()
    if root == main or not root.is_relative_to(main) or root == (main / ".git").resolve():
        raise RuntimeError(f"unsafe worktrees directory: {root}")
    return root


def delete_unused_branch(
    ctx: RunContext,
    branch: str,
    deleted: list[str],
    warnings: list[str],
    *,
    remote_exists: Callable[[str], bool | None] | None = None,
) -> None:
    try:
        pr = ctx.store.pr_for_branch(branch)
        if (
            not branch.startswith("factory/")
            or branch == ctx.rc.config.settings.base
            or (pr is not None and (branch == pr.base or pr.state == OPEN))
        ):
            return
        for child in ctx.store.open_prs():
            if stacked_on(ctx.store, child.branch) != branch:
                continue
            # stacked_on preserves the original fork even after a successful restack.
            # Only a completed resolve after that fork releases the dependency.
            for run in ctx.store.runs_on_branch(child.branch):
                if (
                    run.state == SUCCEEDED
                    and run.pr_error is None
                    and run.workflow in (RESOLVE_WORKFLOW, RESOLVE_REVIEWED_WORKFLOW)
                ):
                    break
                if run.stacked_on == branch:
                    return
        if any(r.state == RUNNING for r in ctx.store.runs_on_branch(branch)):
            return
        if pgit.checked_out_in(ctx.main, branch) is not None:
            return
    except (ProviderError, RuntimeError, OSError) as exc:
        warnings.append(f"branch {branch} not deleted: {exc}")
        return
    # Try each independently so a failed remote deletion remains retryable by task clean.
    try:
        if pgit.rev_parse(ctx.main, f"refs/heads/{branch}") is not None:
            pgit.git(ctx.main, "branch", "-D", "--", branch)
            deleted.append(branch)
    except (ProviderError, RuntimeError, OSError) as exc:
        warnings.append(f"branch {branch} not deleted: {exc}")
    remote = ctx.rc.config.settings.remote
    try:
        if pgit.has_remote(ctx.main, remote):
            exists = (
                remote_exists(branch)
                if remote_exists is not None
                else pgit.remote_branch_exists(ctx.main, remote, branch)
            )
            if exists is None:
                raise RuntimeError(f"cannot query {remote}")
            if exists:
                pgit.git(ctx.main, "push", remote, "--delete", branch)
                deleted.append(f"{remote}/{branch}")
    except (ProviderError, RuntimeError, OSError) as exc:
        warnings.append(f"remote branch {remote}/{branch} not deleted: {exc}")
