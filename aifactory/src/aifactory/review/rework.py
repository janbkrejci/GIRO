"""New work on an open task pull request: return, publish and resolve.

* ``return_task``: a new run on the same branch with the note in the prompt;
  the PR body is updated when the run succeeds.
* ``publish_task``: push the branch of the last succeeded run without a PR and open
  its PR (or adopt the open one the hosting has), as at the end of ``task run``.
* ``resolve_task``: a PR that does not merge (``conflict``) gets a new run of
  the workflow ``resolve`` on its branch: rebase onto the current ``base``, the
  agent settles the conflicts in the conflicted files only, the suite runs, the
  branch is force-pushed with a lease and the PR updated. A failed run leaves
  the branch as it was before the rebase. Never started automatically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from aifactory.backlog import Task, load_backlog
from aifactory.engine.data_types import ReviewOutput
from aifactory.providers import GitProvider, ProviderError
from aifactory.providers import git as pgit
from aifactory.review.context import _catch_up_base, _checked_pr, _context, _free_branch
from aifactory.review.errors import ReviewError
from aifactory.review.publish import PublishResult, publish
from aifactory.run.cleanup import RunContext
from aifactory.run.stack import open_stack
from aifactory.run.store import STARTED_MANUAL, SUCCEEDED, already_running
from aifactory.run.task import TaskRunResult, run_task, session_dir_of
from aifactory.run.unchanged import finish_unchanged, unchanged
from aifactory.workflow import CodeRunner, WorkflowRun
from aifactory.workflow.journal import JOURNAL_FILE, read_journal
from aifactory.workflow.parse import RESOLVE_WORKFLOW

if TYPE_CHECKING:
    from aifactory.run.queue import ChainResult


def return_task(
    repo: Path,
    task_id: str,
    note: str,
    *,
    provider: GitProvider | None = None,
    code: CodeRunner | None = None,
) -> TaskRunResult:
    """Start a new run of `task_id` on its PR branch with `note` in the prompt."""
    if not note or not note.strip():
        raise ReviewError("missing_note", "task return needs --note with what to change")
    ctx = _context(repo, provider)
    try:
        row, _ = _checked_pr(ctx, task_id)
        warnings = _free_branch(ctx, row)
        ctx.provider.comment(row.request(), f"Vráceno k přepracování:\n\n{note.strip()}")
    except ProviderError as exc:
        raise ReviewError(exc.code, exc.message) from exc
    except (RuntimeError, OSError) as exc:
        raise ReviewError("worktree_failed", str(exc)) from exc
    finally:
        ctx.store.close()
    result = run_task(
        repo,
        task_id,
        note=note.strip(),
        force=True,
        code=code,
        provider=ctx.provider,
        branch=row.branch,
    )
    result.warnings = (*result.warnings, *warnings)
    return result


@dataclass
class TaskPublishResult(PublishResult):
    chain: ChainResult | None = None

    def to_json(self) -> dict[str, object]:
        data = super().to_json()
        data["auto_merge"] = None
        if self.chain is not None:
            first = self.chain.runs[0]
            data["auto_merge"] = first.auto_merge.to_json() if first.auto_merge else None
            data["chain"] = self.chain.to_json()
        return data


def publish_task(
    repo: Path,
    task_id: str,
    *,
    run_id: str | None = None,
    provider: GitProvider | None = None,
    code: CodeRunner | None = None,
) -> TaskPublishResult:
    """Push the branch of the task's last succeeded run and open its PR (``task publish``).

    For a run whose push or PR failed at the end of ``task run`` (``pr_failed``): the
    same push (transient failures retried) and PR as there, with the body kept from the
    run. A PR the hosting already has for the branch is adopted (recorded and its body
    updated). A run that left no commit on its branch marks the task done instead
    (``run/unchanged.py``). Without a succeeded run (``no_succeeded_run``), with a PR already known
    (``pr_exists``) or while a run of the task is in progress (``already_running``)
    nothing changes. `run_id` (the dashboard) must name that last succeeded run. A failed
    push or hosting call is ``result.pr_error`` and is kept on the run. After publication,
    repository-local ``auto_merge`` enables ``run_chain`` to finish auto-merge and auto-continue
    with the completed attempt as its first run; its review is recovered from the
    workflow journal. Otherwise the PR stays open for review without continuing the queue.
    """
    ctx = _context(repo, provider)
    operation_id = ctx.store.start_operation(task_id, "publish", os.getpid())
    try:
        published = _publish(ctx, task_id, run_id)
    except RuntimeError as exc:
        raise ReviewError("worktree_failed", str(exc)) from exc
    finally:
        ctx.store.finish_operation(operation_id)
        ctx.store.close()

    from aifactory.run.queue import _fresh_backlog, auto_merge_enabled, run_chain

    def first() -> TaskRunResult:
        # A retry's journal includes the replayed review of the finished attempt.
        journal = read_journal(session_dir_of(repo, published.run.run_id) / JOURNAL_FILE)
        wf = WorkflowRun(exit_code=0, accepted=True, adw_id=published.run.run_id)
        for entry in journal.entries:
            if entry.kept and entry.output == "ReviewOutput" and entry.envelope is not None:
                wf.history.append((entry.step, ReviewOutput.model_validate(entry.envelope)))
        return TaskRunResult(
            run=published.run,
            workflow_run=wf,
            warnings=tuple(published.warnings),
            trace_db=ctx.rc.local.trace_db_path(ctx.main),
            pr=published.pr,
            pr_error=published.pr_error,
            done_without_pr=published.done_without_pr,
        )

    chain = None
    if published.ok:
        with _fresh_backlog(repo) as backlog:
            task = backlog.by_id.get(task_id)
            resume_chain = isinstance(task, Task) and auto_merge_enabled(repo)
        if resume_chain:
            chain = run_chain(repo, task_id, first_run=first, provider=provider, code=code)
    return TaskPublishResult(
        run=published.run,
        pr=published.pr,
        pr_error=published.pr_error,
        warnings=published.warnings,
        done_without_pr=published.done_without_pr,
        chain=chain,
    )


def _publish(ctx: RunContext, task_id: str, run_id: str | None) -> PublishResult:
    busy = ctx.store.running(task_id)
    if busy is not None:
        err = already_running(busy)
        raise ReviewError(err.code, err.message)
    succeeded = [r for r in ctx.store.for_task(task_id) if r.state == SUCCEEDED]
    if not succeeded:
        raise ReviewError("no_succeeded_run", f"task {task_id} has no succeeded run to publish")
    row = succeeded[0]
    if run_id is not None and run_id != row.run_id:
        raise ReviewError(
            "invalid_value",
            f"run {run_id} is not the last succeeded run of task {task_id} ({row.run_id})",
        )
    known = ctx.store.open_pr(task_id) or ctx.store.pr_for_branch(row.branch)
    if known is not None:
        raise ReviewError(
            "pr_exists", f"task {task_id} already has pull request {known.url} ({known.state})"
        )
    if pgit.rev_parse(ctx.main, f"refs/heads/{row.branch}") is None:
        raise ReviewError("unknown_branch", f"branch {row.branch} of run {row.run_id} is gone")
    settings = ctx.rc.config.settings
    worktree = Path(row.worktree)
    source = worktree if worktree.is_dir() else ctx.main
    task = load_backlog(source, settings).by_id.get(task_id)
    if not isinstance(task, Task):
        raise ReviewError("unknown_task", f"no task {task_id} in {source}")
    if unchanged(row):
        failure, notes = finish_unchanged(ctx.main, ctx.store, row)
        ctx.store.set_pr_error(row.run_id, failure)
        return PublishResult(
            run=ctx.store.get(row.run_id) or row,
            pr=None,
            pr_error=failure,
            warnings=notes,
            done_without_pr=failure is None,
        )
    body = ctx.store.pr_body_of(row.run_id)
    try:
        pr = publish(
            ctx.main,
            settings,
            ctx.store,
            row,
            task,
            None,
            ctx.provider,
            body=body,
            adopt_first=True,
        )
    except ProviderError as exc:
        error = f"{exc.code}: {exc.message}"
        ctx.store.set_pr_error(row.run_id, error)
        return PublishResult(
            run=ctx.store.get(row.run_id) or row,
            pr=ctx.store.pr_for_branch(row.branch),
            pr_error=error,
        )
    ctx.store.set_pr_error(row.run_id, None)
    return PublishResult(run=ctx.store.get(row.run_id) or row, pr=pr)


def resolve_task(
    repo: Path,
    task_id: str,
    *,
    provider: GitProvider | None = None,
    code: CodeRunner | None = None,
    workflow: str = RESOLVE_WORKFLOW,
    started_by: str = STARTED_MANUAL,
) -> TaskRunResult:
    """Rebase the branch of `task_id`'s open PR onto the current base in a new run.

    Works for a PR in ``conflict`` and for a ``mergeable`` one (then it only
    brings the branch up to date). Base is caught up with the remote first.
    ``workflow`` is ``resolve`` or ``resolve-reviewed`` (auto-merge, a review of the resolution).
    """
    ctx = _context(repo, provider)
    try:
        row, _ = _checked_pr(ctx, task_id)
        stack = open_stack(ctx.store, row.branch)
        if stack is not None:
            raise ReviewError(
                "stacked_on_open_pr",
                f"the PR of {task_id} builds on {stack.url} ({stack.task_id}), which is not "
                "merged yet; it is rebased onto base after that merge",
            )
        warnings = _catch_up_base(ctx, strict=True)
        warnings.extend(_free_branch(ctx, row))
        base = ctx.rc.config.settings.base
        onto = pgit.rev_parse(ctx.main, f"refs/heads/{base}")
        if onto is None:
            raise ReviewError("unknown_base", f"base {base!r} does not exist")
    except ProviderError as exc:
        raise ReviewError(exc.code, exc.message) from exc
    except (RuntimeError, OSError) as exc:
        raise ReviewError("worktree_failed", str(exc)) from exc
    finally:
        ctx.store.close()
    result = run_task(
        repo,
        task_id,
        force=True,
        code=code,
        provider=ctx.provider,
        branch=row.branch,
        resolve_onto=onto,
        resolve_with=workflow,
        started_by=started_by,
    )
    result.warnings = (*result.warnings, *warnings)
    return result
