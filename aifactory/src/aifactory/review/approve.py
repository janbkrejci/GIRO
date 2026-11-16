"""Approve a task pull request (step 6 of "Běh úkolu").

``approve_task``: PR state -> one commit on the PR branch with ``status: done`` and a
``## Běhy`` line -> push -> the CI checks of that head (``review.ci``; red checks get up
to ``CI_FIX_ROUNDS`` ``ci-fix`` repair runs on the same branch, each followed by a new
done commit, push and wait) -> supported host approval -> merge -> ``task_prs`` merged ->
``base`` caught up with the remote -> worktrees removed -> dependents restacked -> merged
branch deleted locally and remotely when no open stack or checkout uses it. ``merged_by``
(``operator``, or ``auto-merge`` from ``review/automerge.py``) goes to ``task_prs``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from aifactory import failure as failures
from aifactory import gitops
from aifactory.backlog import Task, load_backlog, unmet
from aifactory.backlog.followup_ids import advance_pr, publication_lock, reconcile_ids
from aifactory.backlog.roots import owning_root
from aifactory.backlog.taskfile import mark_done
from aifactory.config.settings import ProjectSettings
from aifactory.errors import TaskRunError
from aifactory.providers import (
    CHECKS_FAILING,
    CONFLICT,
    MERGED,
    OPEN,
    GitProvider,
    MergeFailed,
    ProviderError,
)
from aifactory.providers import git as pgit
from aifactory.review import ci
from aifactory.review.context import (
    _branch_worktree,
    _catch_up_base,
    _checked_pr,
    _context,
    _done_entry,
    _free_branch,
    _remove,
    _remove_run_worktrees,
)
from aifactory.review.errors import ReviewError
from aifactory.run.cleanup import RunContext, delete_unused_branch
from aifactory.runstore.base import _now
from aifactory.runstore.rows import MERGED_BY_OPERATOR, SUCCEEDED, TaskPrRow, TaskRunRow
from aifactory.runstore.store import TaskRunStore
from aifactory.scope import matches
from aifactory.workflow import CodeRunner

if TYPE_CHECKING:
    from aifactory.run.allow_writes import AllowWritesResult
    from aifactory.run.chain import ChainResult


@dataclass
class ApproveResult:
    task_id: str
    pr: TaskPrRow
    merge_sha: str | None
    strategy: str
    reviewed: bool  # True only for a successful host approval
    base_sha: str | None
    removed_worktrees: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    merged_by: str = MERGED_BY_OPERATOR
    ci_fix_runs: list[str] = field(default_factory=list)  # ci-fix runs before the merge
    deleted_branches: list[str] = field(default_factory=list)
    allow_writes: dict[str, object] | None = None  # --allow-writes: the paths allowed first
    chain: ChainResult | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "ok": True,
            "task_id": self.task_id,
            "pr": self.pr.to_json(),
            "merge_sha": self.merge_sha,
            "merged_by": self.merged_by,
            "strategy": self.strategy,
            "reviewed": self.reviewed,
            "base_sha": self.base_sha,
            "removed_worktrees": list(self.removed_worktrees),
            "warnings": list(self.warnings),
            "ci_fix_runs": list(self.ci_fix_runs),
            "chain": self.chain.to_json() if self.chain is not None else None,
            "deleted_branches": list(self.deleted_branches),
            **({"allow_writes": self.allow_writes} if self.allow_writes is not None else {}),
        }


def require_auto_merge(repo: Path) -> None:
    """Fail closed on disabled or unreadable local policy; manual approve bypasses it."""
    from aifactory.run.policy import merge_enabled

    try:
        allowed = merge_enabled(repo)
    except TaskRunError as exc:
        raise ReviewError(exc.code, exc.message) from exc
    if not allowed:
        raise ReviewError(
            "auto_merge_disabled",
            "Repository-local auto merge is off; PR remains open. The done commit, "
            "if already pushed for CI, stays on the PR branch. Approve manually or retry "
            "after enabling auto merge. Hosting requests already sent cannot be withdrawn.",
        )


def approve_task(
    repo: Path,
    task_id: str,
    *,
    provider: GitProvider | None = None,
    merged_by: str = MERGED_BY_OPERATOR,
    code: CodeRunner | None = None,
    allow_writes: bool = False,
) -> ApproveResult:
    """Commit ``status: done`` to the task's PR branch, wait for its CI and merge the PR.

    ``merged_by`` is recorded in ``task_prs`` (``operator`` or ``auto-merge``). The
    checks of the pushed head decide (``review.ci``): none or green merge; red ones
    start up to ``ci.CI_FIX_ROUNDS`` ``ci-fix`` runs on the PR branch (``code`` runs
    their code steps), each followed by a new wait. Checks still red or running leave
    the PR open with the reason in ``task_prs.auto_merge_error`` and raise ``ReviewError``.
    A ``ci-fix`` run that needs paths outside the task's writes (``writes_needed``) stops
    with the code ``writes_needed`` and its paths. ``allow_writes`` (``factory task approve
    --allow-writes``) first adds those paths of the newest ``ci-fix`` run to the task's
    writes in base (``run.allow_writes``), so the new ``ci-fix`` runs may write them.
    """
    allowed = _allow_ci_fix_writes(repo, task_id) if allow_writes else None
    ctx = _context(repo, provider)
    row = ctx.store.latest_pr(task_id) if merged_by == MERGED_BY_OPERATOR else None
    try:
        if row is not None and row.state == OPEN:
            ctx.store.update_pr(row.branch, approved_at=_now(), auto_merge_error=None)
        try:
            if allowed is not None:
                _take_base_backlog(ctx, task_id)
            result = _approve_gated(repo, ctx, task_id, merged_by, code)
        except ProviderError as exc:
            raise ReviewError(exc.code, exc.message) from exc
        except RuntimeError as exc:
            raise ReviewError("worktree_failed", str(exc)) from exc
    except ReviewError as exc:
        if row is not None:
            saved = ctx.store.pr_for_branch(row.branch)
            if saved is not None and saved.state == OPEN:
                ctx.store.update_pr(
                    row.branch, approved_at=None, auto_merge_error=f"{exc.code}: {exc.message}"
                )
        raise
    finally:
        ctx.store.close()
    if allowed is not None:
        result.allow_writes = allowed.to_json()
        result.warnings += list(allowed.commit.warnings)
    # Local import: review.stack resolves and merges through this module.
    from aifactory.review.stack import restack_dependents

    result.warnings += restack_dependents(
        repo, ctx.store.db_path, result.pr.branch, provider=ctx.provider, code=code
    )
    # Restacking may recursively approve other PRs and writes through separate stores.
    ctx.store = TaskRunStore(ctx.store.db_path)
    try:
        _delete_merged_branch(ctx, result.pr, result.deleted_branches, result.warnings)
    finally:
        ctx.store.close()
    if merged_by == MERGED_BY_OPERATOR:
        from aifactory.run.chain import continue_after_merge

        result.chain = continue_after_merge(repo, task_id, provider=ctx.provider, code=code)
    return result


def _allow_ci_fix_writes(repo: Path, task_id: str) -> AllowWritesResult:
    """``approve --allow-writes``: the paths of the newest ``ci-fix`` run into ``writes``."""
    from aifactory.run.allow_writes import allow_writes

    try:
        return allow_writes(repo, task_id, ci_fix=True)
    except TaskRunError as exc:
        raise ReviewError(exc.code, exc.message) from exc


def _take_base_backlog(ctx: RunContext, task_id: str) -> None:
    """Merge base into the PR branch after ``--allow-writes`` changed the task in base.

    An earlier approve left its ``status: done`` commit on the branch; it and the new
    ``writes`` in base change the same front matter, so the PR would not merge. Backlog
    files take base's version (``_prepare_approve`` commits ``status: done`` again); a
    conflict in any other file aborts the merge (``conflict``).
    """
    row, _ = _checked_pr(ctx, task_id)
    settings = ctx.rc.config.settings
    base = f"refs/heads/{settings.base}"
    _free_branch(ctx, row)
    worktree, temp = _branch_worktree(ctx, row.branch, "allow-writes")
    try:
        if gitops.git(worktree, "merge-base", "HEAD", base) == gitops.git(
            worktree, "rev-parse", base
        ):
            return  # base is in the branch already
        try:
            gitops.git(worktree, "merge", "--no-ff", "--no-commit", base)
        except RuntimeError:
            pass
        conflicted = [
            p
            for p in gitops.git(worktree, "diff", "--name-only", "--diff-filter=U").splitlines()
            if p.strip()
        ]
        others = [
            p
            for p in conflicted
            if owning_root(p, settings) is None
            and not any(matches(p, root) for root in settings.backlog_patterns)
        ]
        if others:
            gitops.git(worktree, "merge", "--abort")
            raise ReviewError(
                "conflict",
                f"PR {row.url} does not merge into {row.base} ({', '.join(others)}); run "
                f"`factory task resolve {task_id}`, then approve again",
            )
        for path in conflicted:
            gitops.git(worktree, "checkout", "--theirs", "--", path)
            gitops.git(worktree, "add", "--", path)
        gitops.git(
            worktree,
            "commit",
            "-q",
            "--no-edit",
            "-m",
            f"{task_id}: merge {settings.base} (writes)",
        )
        ctx.provider.push(worktree, row.branch)
    finally:
        if temp is not None:
            _remove(ctx, temp, [], [])


def ci_fix_needs(run: TaskRunRow) -> str | None:
    """The reason of a ``ci-fix`` run that ended ``writes_needed``: its paths; else None."""
    if run.shown_failure_kind != failures.WRITES_NEEDED:
        return None
    detail = run.failure_detail_dict() or {}
    items = failures.needed_paths(detail.get("needs_writes") or [])
    if not items:
        items = failures.needed_paths([{"path": p} for p in detail.get("paths") or []])
    paths = "; ".join(f"{i['path']} ({i['reason']})" if i["reason"] else i["path"] for i in items)
    return (
        f"ci-fix run {run.run_id} needs paths outside the task's writes: {paths or '?'}; "
        f"allow them and approve again (factory task approve {run.task_id} --allow-writes)"
    )


def _approve_gated(
    repo: Path, ctx: RunContext, task_id: str, merged_by: str, code: CodeRunner | None
) -> ApproveResult:
    """``_prepare_approve``, the CI gate with its repair runs, then ``_finish_approve``."""
    fixes: list[str] = []
    refreshes = 0
    while True:
        if merged_by != MERGED_BY_OPERATOR:
            require_auto_merge(repo)
        prepared = _prepare_approve(ctx, task_id)
        row = prepared.row

        def stop(code_: str, reason: str, row: TaskPrRow = row) -> ReviewError:
            fields: dict[str, object] = {"auto_merge_error": f"{code_}: {reason}"}
            if merged_by == MERGED_BY_OPERATOR:
                fields["approved_at"] = None
            ctx.store.update_pr(row.branch, **fields)
            return ReviewError(code_, reason)

        try:
            if merged_by == MERGED_BY_OPERATOR:
                checks = ci.head_checks(ctx.provider, row.request(), prepared.head)
            else:
                checks = ci.head_checks(
                    ctx.provider, row.request(), prepared.head, lambda: require_auto_merge(repo)
                )
        except ReviewError as exc:
            raise stop(exc.code, exc.message) from exc
        except ProviderError as exc:
            raise stop("checks_unavailable", f"{exc.code}: {exc.message}") from exc
        if checks.state != CHECKS_FAILING or len(fixes) >= ci.CI_FIX_ROUNDS:
            blocker = ci.checks_reason(checks, len(fixes))
            if blocker is not None:
                raise stop(*blocker)
            try:
                result = _finish_approve(ctx, prepared, merged_by)
            except ReviewError as exc:
                if exc.code == "follow_up_head_changed" and refreshes < 3:
                    refreshes += 1
                    if prepared.temp is not None:
                        _remove(ctx, prepared.temp, [], [])
                    continue  # The repaired head must pass CI again.
                raise stop(exc.code, exc.message) from exc
            result.ci_fix_runs = fixes
            return result
        try:
            logs = ctx.provider.check_logs(row.request(), checks.failing)
        except ProviderError as exc:
            logs = {name: f"log unavailable: {exc.code}: {exc.message}" for name in checks.failing}
        billing = ci.billing_block(logs)
        if billing is not None:
            raise stop("checks_unavailable", billing)
        if prepared.temp is not None:
            _remove(ctx, prepared.temp, [], [])
        _free_branch(ctx, row)
        try:
            if merged_by != MERGED_BY_OPERATOR:
                require_auto_merge(repo)
            fixed = ci.run_ci_fix(
                repo,
                task_id,
                row.branch,
                ci.ci_report(checks.failing, logs),
                ctx.provider,
                code,
            )
        except ReviewError as exc:
            raise stop(exc.code, exc.message) from exc
        except TaskRunError as exc:
            reason = f"ci-fix run did not start: {exc.code}: {exc.message}"
            raise stop("ci_fix_failed", reason) from exc
        fixes.append(fixed.run.run_id)
        needs = ci_fix_needs(fixed.run) if fixed.run.state != SUCCEEDED else None
        if needs is not None:
            raise stop("writes_needed", needs)
        if fixed.run.state != SUCCEEDED:
            detail = f": {fixed.run.error}" if fixed.run.error else ""
            raise stop(
                "ci_fix_failed",
                f"ci-fix run {fixed.run.run_id} ended {fixed.run.state}{detail} "
                f"(red checks: {', '.join(checks.failing) or '?'})",
            )


def _refuse_blocked(main: Path, settings: ProjectSettings, task_id: str) -> None:
    """``task_blocked`` when the task has unmet ``depends_on`` in the main checkout's backlog.

    A run whose agents reported a blocker links the task to it there
    (``run/followups.py``); a blocked task must not become done or be merged.
    """
    backlog = load_backlog(main, settings)
    node = backlog.by_id.get(task_id)
    if not isinstance(node, Task):
        return
    missing = unmet(backlog, node)
    if missing:
        ids = ", ".join(u.id for u in missing)
        raise ReviewError("task_blocked", f"task {task_id} is blocked by {ids}; finish them first")


@dataclass
class _Prepared:
    """The PR after the ``status: done`` commit was pushed: ``head`` is what gets merged."""

    row: TaskPrRow
    worktree: Path
    temp: Path | None
    head: str


def _prepare_approve(ctx: RunContext, task_id: str) -> _Prepared:
    """Check the PR, commit ``status: done`` on its branch and push; safe to call again."""
    row, status = _checked_pr(ctx, task_id)
    settings = ctx.rc.config.settings
    main, branch = ctx.main, row.branch
    _refuse_blocked(main, settings, task_id)
    worktree, temp = _branch_worktree(ctx, branch, "approve")

    with publication_lock(main):
        repaired = _refresh_follow_up_ids(ctx, row, worktree)
    if status.mergeability == CONFLICT and not repaired:
        raise ReviewError(
            "conflict",
            f"PR {row.url} does not merge into {row.base}; run `factory task resolve {task_id}` "
            f"to rebase it onto {row.base}",
        )

    backlog = load_backlog(worktree, settings)
    node = backlog.by_id.get(task_id)
    if not isinstance(node, Task):
        raise ReviewError("unknown_task", f"no task {task_id} on {branch}")
    entry = _done_entry(ctx, row)
    task_file = worktree / node.path
    text = task_file.read_text(encoding="utf-8")
    new = mark_done(text, entry, pr_url=row.url)
    if new != text:
        task_file.write_text(new, encoding="utf-8", newline="\n")
        gitops.git(worktree, "add", "--", node.path)
        gitops.git(worktree, "commit", "-q", "-m", f"{task_id}: status done")
    ctx.provider.push(worktree, branch)
    head = gitops.git(worktree, "rev-parse", "HEAD")
    return _Prepared(row, worktree, temp, head)


def _finish_approve(ctx: RunContext, prepared: _Prepared, merged_by: str) -> ApproveResult:

    with publication_lock(ctx.main):
        if _refresh_follow_up_ids(ctx, prepared.row, prepared.worktree):
            raise ReviewError(
                "follow_up_head_changed",
                "target changed; follow-up IDs reconciled, checks required for the new head",
            )
        return _finish_approve_current(ctx, prepared, merged_by)


def _refresh_follow_up_ids(ctx: RunContext, row: TaskPrRow, worktree: Path) -> bool:

    head = gitops.git(worktree, "rev-parse", "HEAD")
    fixed, _ = reconcile_ids(ctx.main, head, row.base, ctx.rc.config.settings)
    if fixed == head:
        return False
    advance_pr(ctx.main, row.branch, head, fixed)
    ctx.provider.push(worktree, row.branch)
    return True


def _finish_approve_current(ctx: RunContext, prepared: _Prepared, merged_by: str) -> ApproveResult:
    """Host approval, merge of ``prepared.head``, ``task_prs``, base caught up, cleanup."""
    row, head, temp = prepared.row, prepared.head, prepared.temp
    settings = ctx.rc.config.settings
    main, branch, task_id = ctx.main, row.branch, row.task_id
    try:
        if merged_by != MERGED_BY_OPERATOR:
            require_auto_merge(main)
        reviewed = ctx.provider.approve(row.request(), head)
    except ProviderError as exc:
        raise ReviewError(
            exc.code,
            f"{exc.message} (merge stopped; the done commit stays on {branch}; retry approve)",
        ) from exc

    try:
        if merged_by == MERGED_BY_OPERATOR:
            merge_sha = ctx.provider.merge(row.request(), head, row.title)
        else:
            from aifactory.repo_settings import RepositorySettings, SettingsError

            try:
                with RepositorySettings(main).automatic_merge() as allowed:
                    if not allowed:
                        raise ReviewError(
                            "auto_merge_disabled", "Auto merge is off; PR remains open."
                        )
                    merge_sha = ctx.provider.merge(row.request(), head, row.title)
            except SettingsError as exc:
                raise ReviewError(exc.code, str(exc)) from exc
    except MergeFailed as exc:
        # The done commit stays on the branch; mark_done is idempotent, so approve can be rerun.
        raise ReviewError(exc.code, f"{exc.message} (the done commit stays on {branch})") from exc
    ctx.store.update_pr(
        branch,
        state=MERGED,
        merged_at=_now(),
        merge_sha=merge_sha,
        merged_by=merged_by,
        auto_merge_error=None,
    )

    warnings = _catch_up_base(ctx)
    removed, remove_warnings = _remove_run_worktrees(ctx, branch)
    warnings += remove_warnings
    if temp is not None:
        _remove(ctx, temp, removed, warnings)
    saved = ctx.store.pr_for_branch(branch)
    assert saved is not None
    return ApproveResult(
        task_id=task_id,
        pr=saved,
        merge_sha=merge_sha,
        strategy=settings.merge_strategy,
        reviewed=reviewed,
        base_sha=pgit.rev_parse(main, f"refs/heads/{settings.base}"),
        removed_worktrees=removed,
        warnings=warnings,
        merged_by=merged_by,
    )


def _delete_merged_branch(
    ctx: RunContext, pr: TaskPrRow, deleted: list[str], warnings: list[str]
) -> None:
    """Best-effort cleanup after restack; keep branches still used by open PRs or runs."""
    delete_unused_branch(ctx, pr.branch, deleted, warnings)
