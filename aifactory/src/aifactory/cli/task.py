"""``giro task``."""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aifactory.cli.backlog import _backlog_root, _deprecated_operating_flag
from aifactory.cli.envelope import emit_fail, emit_ok, fail_from_exception, problem_warnings
from aifactory.skill.envelope import strip_payload

if TYPE_CHECKING:
    from aifactory.backlog import Backlog
    from aifactory.run import ChainResult, TaskRunResult
    from aifactory.runstore.rows import TaskRunRow
    from aifactory.workflow import CodeRunner


TASK_EPILOG = """\
error codes (--json: error.code): with --json every command prints one envelope
{ok, data, error, warnings}; the full list of codes, formats and procedures is in
'factory --skill'. Exit 0 ok; 1 the command ran and found a problem (a failed run, a
write that would break the backlog, a PR that could not be opened); 2 the command could
not do its job (bad input, config, state, provider), nothing was changed.

task run: a failed run keeps its worktree and branch. After a successful run the branch
  is pushed and a pull request opened; when that fails the run stays succeeded and
  pr_error says why. Repository-local auto_continue starts the next ready task of
  the step, then of the project. --auto is deprecated and has no effect. Repository-local
  auto_merge (factory config local set --auto-merge on/off): a succeeded run with a review
  phase whose last review approved and a PR that
  merges cleanly is approved and merged like 'task approve' (CI wait, ci-fix runs); the chain
  continues only after that merge. Otherwise the PR stays open and auto_merge says why.
task publish: for the last succeeded run without a pull request (pr_failed) push the
  branch and open the PR as 'task run' does, or adopt the open PR the hosting has for the
  branch. Then finish effective auto-merge and auto-continue as after task run.
  No succeeded run (no_succeeded_run), a PR already known (pr_exists) or a run in
  progress (already_running): exit 2, nothing changed. Push or PR failed again: pr_failed,
  exit 1. A push failing on the network (RPC, HTTP2, hung up, timeout) is retried a few
  times, including hosting HTTP 5xx; a rejected push is not.
task approve: on conflict run 'factory task resolve <task-id>'. Waits for the CI checks of
  the pushed head (none: merge at once); red checks get up to 2 ci-fix runs on the PR
  branch, then checks_failing, checks_pending or ci_fix_failed and the PR stays open. A
  ci-fix run that needs paths outside the task's writes leaves it open with writes_needed
  and the paths; --allow-writes adds them to the task's writes (committed to base like
  task retry --allow-writes, same refusals) and approves again, so the new ci-fix runs may
  write them. On
  GitHub an approve review is sent as github_approver (config.yaml) when it is set;
  otherwise none is sent.
task resolve: a run that fails leaves the branch as it was before the rebase.
task stop: the run is marked stopped; the next run or task clean removes eligible worktrees.
  A paused run can be stopped too.
task pause: the phase in progress runs to its end (the run shows pausing), then the run
  waits as paused; its process stays alive and keeps its place among the parallel runs.
task resume: the paused run goes on with its next phase (a pending pause is dropped).
task retry: continues the task's newest failed, stopped or aborted run (or --run) as its
  next attempt: same run id, worktree, branch and trace (tokens and costs add up). Done
  phases are not repeated; the run goes on from the phase that failed or was stopped, after
  an accept_not_met failure from the step whose result failed accept (a review gets a new
  round), or from --from PHASE. Agents rejoin their sessions; --note goes to every agent of the
  continuing phases. Base is pulled from the remote first (as 'config pull'); when it moved
  past the branch, the branch is rebased onto it, a conflict resolved like 'task resolve',
  and the tests run again before any review. Refused: a task merged or done (task_done), a
  run that cannot be retried (run_not_retryable), a running task (already_running).
  --allow-writes after a writes_violation (paths written outside writes) or a
  writes_needed (paths an agent reported in needs_writes) first adds the paths to the
  task's writes and commits the backlog to base (by base_changes); the retry continues
  from the failed phase and its Retry note names the allowed paths. Paths in
  protected_files, in the backlog or in the main checkout are never added; refused: a run
  of another kind (not_writes_violation), a ci-fix run (run_not_retryable: use task
  approve --allow-writes), no path to add (nothing_to_allow), a change
  waiting in an unmerged backlog PR (writes_in_pr), or base missing the new writes
  (writes_not_committed: commit the backlog, then factory task retry ID).
  --harness [--model --thinking] continues with every agent on another harness (after a
  quota failure); the switch is recorded in the run's retries, its events and its PR.
quota fallback: a run (or retry) that fails with failure_kind quota continues once by itself
  as a retry on the configured fallback harness: 'fallback: {harness, model?, thinking?}' in
  .factory/local.yaml (this computer, wins), on the agent in agents.yaml, or in its defaults.
  When the fallback fails too, the run stays failed (quota); a switch never switches again.
"""


def _add_task_commands(parser: argparse.ArgumentParser) -> None:
    parser.epilog = TASK_EPILOG
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.set_defaults(task_parser=parser)
    sub = parser.add_subparsers(dest="task_command", metavar="<subcommand>")
    add = sub.add_parser(
        "add",
        help="create a new task in a step",
        description="Create a new todo task file in the directory of STEP. The backlog is "
        "validated before writing; a change that would break it is rejected.",
    )
    add.add_argument("step", metavar="STEP", help="id of the step the task belongs to")
    add.add_argument("title", metavar="TITLE", help="task title")
    add.add_argument(
        "--id",
        dest="task_id",
        metavar="CODE",
        help="short code, e.g. T05; the id is STEP-CODE (default: the next free)",
    )
    add.add_argument("--slug", metavar="SLUG", help="file name slug (default: from the title)")
    add.add_argument("--workflow", metavar="NAME", help="workflow (default: inherited)")
    add.add_argument("--writes", nargs="*", metavar="PATH", help="paths the task writes")
    add.add_argument("--depends-on", nargs="+", default=[], metavar="ID", help="dependencies")
    add.add_argument("--related", nargs="+", metavar="ID", help="related tasks")
    add.add_argument("--body", default="", metavar="TEXT", help="text of '## Zadání'")
    add.add_argument(
        "--assignee",
        choices=("agent", "human"),
        help="human: a task for a person, no run starts it (default: inherited, agent)",
    )

    edit = sub.add_parser(
        "edit",
        help="change header fields of a task",
        description="Change title, status (todo|cancelled, or done for a person's task), "
        "workflow, writes or assignee of a task. --auto-merge is deprecated; "
        "use factory config local set --auto-merge on|off.",
    )
    edit.add_argument("task_id", metavar="ID", help="task id")
    edit.add_argument("--title", metavar="TEXT", help="new title")
    edit.add_argument(
        "--status",
        metavar="STATUS",
        help="todo or cancelled; done for a task with assignee human (a person did it)",
    )
    edit.add_argument("--workflow", metavar="NAME", help="set the workflow")
    edit.add_argument(
        "--clear-workflow", action="store_true", help="remove the workflow (inherit it)"
    )
    edit.add_argument("--writes", nargs="*", metavar="PATH", help="set writes (none = [])")
    edit.add_argument("--clear-writes", action="store_true", help="remove writes (inherit)")
    edit.add_argument(
        "--auto-merge",
        choices=("on", "off", "inherit"),
        help="deprecated; use factory config local set --auto-merge on|off",
    )
    edit.add_argument(
        "--assignee",
        choices=("agent", "human", "inherit"),
        help="human: the task is for a person and no run starts it; inherit removes it",
    )

    follow_up = sub.add_parser(
        "follow-up",
        help="create a task that follows a done task",
        description="Create a new todo task in the step of the done task ID with follows: ID, "
        "the own writes and workflow of ID (inherited values stay inherited) and TEXT as its "
        "'## Zadání'. The title defaults to 'Navazuje na ID: <title of ID>'. A task that is "
        "not done is refused (not_done). With --run the backlog is committed to base "
        "(factory backlog commit) and the new task is run (factory task run).",
    )
    follow_up.add_argument("task_id", metavar="ID", help="id of the done task to follow")
    follow_up.add_argument(
        "--body", required=True, metavar="TEXT", help="text of '## Zadání' of the new task"
    )
    follow_up.add_argument("--title", metavar="TEXT", help="title of the new task")
    follow_up.add_argument(
        "--run",
        action="store_true",
        help="commit the backlog to base and run the new task right away",
    )

    link = sub.add_parser(
        "link",
        help="add or remove depends_on / related references",
        description="Add (or with --remove drop) depends_on and related references of a task.",
    )
    link.add_argument("task_id", metavar="ID", help="task id")
    link.add_argument("--depends-on", nargs="+", default=[], metavar="ID", help="dependencies")
    link.add_argument("--related", nargs="+", default=[], metavar="ID", help="related tasks")
    link.add_argument("--remove", action="store_true", help="remove the references instead")

    show = sub.add_parser(
        "show", help="print a task and its derived state", description="Print one task."
    )
    show.add_argument("task_id", metavar="ID", help="task id")

    list_ = sub.add_parser(
        "list",
        help="list tasks with derived states",
        description="List tasks with their derived state (ready/blocked/done/cancelled).",
    )
    list_.add_argument("--status", metavar="STATE", help="todo|done|cancelled|ready|blocked")
    list_.add_argument("--project", metavar="ID", help="only this project (id or directory)")
    list_.add_argument("--step", metavar="ID", help="only this step")

    runs = sub.add_parser(
        "runs",
        help="list the runs of a task with their state and failure kind",
        description="List every run of a task, newest first: state, failure_kind (why a run "
        "did not succeed: accept_not_met, nothing_to_do, quota, writes_violation, "
        "writes_needed, invalid_output, process_gone, stopped, blocked or other) and the "
        "error; a blocked run (it waits for blocking tasks) lists its blockers. --json gives "
        "each run with failure_kind and failure_detail.",
    )
    runs.add_argument("task_id", metavar="ID", help="task id")

    run = sub.add_parser(
        "run",
        help="run a task in its own worktree and branch",
        description="Run a task from the backlog in base in a new worktree "
        "<worktrees_dir>/<run-id> on a new branch factory/<task-id>-<n>. Agents may change "
        "only the task's writes and its spec/doc files inside the worktree; anything else, "
        "including any change in the main checkout, is rolled back and fails the phase. "
        "Repository-local auto_continue controls whether the next ready task follows. "
        "--auto is deprecated and cannot override local off. "
        "With repository-local auto_merge on a reviewed run's PR is merged first when it may be "
        "(see 'factory config local --help').",
    )
    run.add_argument("task_id", metavar="ID", help="task id")
    run.add_argument("--note", metavar="TEXT", help="a note for the agents, appended to the task")
    run.add_argument(
        "--force",
        action="store_true",
        help="run even if depends_on are not done in base; never overrides a running task",
    )
    run.add_argument(
        "--auto",
        action="store_true",
        help="deprecated compatibility option; automatic continuation follows repository-local "
        "auto_continue, set with factory config local set --auto-continue on/off",
    )
    from aifactory.harness import THINKING_LEVELS

    run.add_argument(
        "--harness",
        metavar="NAME",
        help="run every agent of the roster on this harness for this run only (nothing is "
        "written); a switch without --model takes the harness's preset model. Step overrides "
        "of the workflow still win, so one workflow can mix harnesses. With --auto only the "
        "first run of the chain gets it",
    )
    run.add_argument(
        "--model", metavar="NAME", help="model of every agent for this run only (like --harness)"
    )
    run.add_argument(
        "--thinking",
        metavar="LEVEL",
        choices=THINKING_LEVELS,
        help="thinking level of every agent for this run only (like --harness)",
    )
    # internal: one run of a parallel auto-continue chain (run/members.py), never a chain
    run.add_argument("--member", action="store_true", help=argparse.SUPPRESS)
    # internal: who started a member run (a chain: auto-continue)
    run.add_argument(
        "--started-by",
        choices=("manual", "auto-continue", "auto-resolve", "auto-ci-fix"),
        default="manual",
        help=argparse.SUPPRESS,
    )

    approve = sub.add_parser(
        "approve",
        help="merge the task's pull request after a commit with status: done and green CI",
        description="Add a commit with status: done and a line in '## Běhy' to the task's "
        "pull request, wait for the CI checks of that head, merge it, bring base up to date "
        "with the remote and remove the branch's worktrees. After restacking dependents, "
        "delete the merged branch locally and on the remote unless an open stacked PR, "
        "a running run or a checkout still uses it. Deletion failures are warnings; "
        "deleted_branches lists "
        "deleted refs (remote refs prefixed by the remote name). Red checks start up to 2 "
        "ci-fix repair runs on the PR branch (started_by auto-ci-fix), each followed by a "
        "new wait; checks still red or running leave the PR open (auto_merge_error). Azure "
        "sends an approval vote after the final push and before merge; local and GitHub do "
        "not send a self-approval review. A ci-fix run that needs paths outside the task's "
        "writes leaves the PR open with writes_needed; --allow-writes adds those paths to "
        "the task's writes in base first.",
    )
    approve.add_argument("task_id", metavar="ID", help="task id")
    approve.add_argument(
        "--allow-writes",
        action="store_true",
        help="after a ci-fix run that ended writes_needed: add the paths it needs to the "
        "task's writes (committed to base by base_changes), then approve again; the new "
        "ci-fix runs may write them. Paths in protected_files or in the backlog are never "
        "added",
    )

    return_ = sub.add_parser(
        "return",
        help="send the task back to the agents with a note",
        description="Start a new run on the same branch with the note in the prompt; the "
        "pull request is updated.",
    )
    return_.add_argument("task_id", metavar="ID", help="task id")
    return_.add_argument(
        "--note", required=True, metavar="TEXT", help="what to change, appended to the task"
    )

    resolve = sub.add_parser(
        "resolve",
        help="rebase the task's pull request onto base and resolve conflicts",
        description="Start a new run on the branch of the task's pull request: rebase it onto "
        "the current base; on a conflict an agent changes only the conflicted files. Then the "
        "suite runs, the branch is force-pushed (with a lease) and the pull request updated. "
        "When the run fails, the branch stays as it was before the rebase.",
    )
    resolve.add_argument("task_id", metavar="ID", help="task id")

    publish = sub.add_parser(
        "publish",
        help="push the branch of the last succeeded run and open its pull request",
        description="For the task's last succeeded run that has no pull request (its push or "
        "PR failed at the end of 'task run'): push the branch and open the pull request with "
        "the body from the run, then finish auto-merge and auto-continue as after 'task run'. "
        "An open PR the hosting already "
        "has for the branch is adopted and its body updated. Without a succeeded run, with a "
        "known PR or while a run is in progress it fails with exit 2 and changes nothing.",
    )
    publish.add_argument("task_id", metavar="ID", help="task id")

    clean = sub.add_parser(
        "clean",
        help="remove worktrees that are no longer needed",
        description="Remove worktrees of runs whose PR is merged or closed, and of abandoned "
        "runs (a newer run of the task exists). Also delete local and remote branches of "
        "merged PRs, including older merges, unless an open stacked PR, a running run or "
        "a checkout still uses them. Also clean done/cancelled tasks, closed factory branches "
        "and orphan directories. Keep dirty active tasks and the latest retryable run. "
        "Cleanup runs automatically before and after execution. Deletion failures "
        "are warnings; deleted_branches lists deleted local and remote refs; "
        "branch_reasons records their reasons.",
    )

    stop = sub.add_parser(
        "stop",
        help="stop the running run of a task",
        description="Send SIGTERM to the coding agents of the task's running run and then to "
        "the run's process (SIGKILL after a timeout). The run is marked stopped; its worktree "
        "is cleaned by the next run or factory task clean when eligible; the latest failed "
        "or stopped todo run stays available for retry.",
    )
    stop.add_argument("task_id", metavar="ID", help="task id")
    stop.add_argument(
        "--run", metavar="RUN_ID", help="stop exactly this run (it must belong to the task)"
    )

    pause = sub.add_parser(
        "pause",
        help="pause the running run of a task before its next phase",
        description="Ask the task's running run to pause. The phase in progress (an agent, "
        "a test) runs to its end, then the run waits as paused before the next phase; its "
        "process stays alive. factory task resume lets it go on, factory task stop stops it.",
    )
    pause.add_argument("task_id", metavar="ID", help="task id")
    pause.add_argument(
        "--run", metavar="RUN_ID", help="pause exactly this run (it must belong to the task)"
    )
    resume = sub.add_parser(
        "resume",
        help="let a paused run of a task go on with its next phase",
        description="Resume the task's paused run: it goes on with its next phase in the same "
        "worktree and branch. A pause that is still pending (pausing) is dropped.",
    )
    resume.add_argument("task_id", metavar="ID", help="task id")
    resume.add_argument(
        "--run", metavar="RUN_ID", help="resume exactly this run (it must belong to the task)"
    )

    retry = sub.add_parser(
        "retry",
        help="continue a failed or stopped run of a task where it ended",
        description="Continue the task's newest failed, stopped or aborted run in its own "
        "worktree and branch, as the next attempt of the same run. Phases already done are "
        "not repeated and every agent rejoins its session. Base is pulled from the remote "
        "first; when it moved, the branch is rebased onto it (a conflict is resolved like "
        "factory task resolve) and the tests run again before any review. Configuration, "
        "backlog and workflow come from the current base.",
    )
    retry.add_argument("task_id", metavar="ID", help="task id")
    retry.add_argument(
        "--run", metavar="RUN_ID", help="retry exactly this run (it must belong to the task)"
    )
    retry.add_argument(
        "--note", metavar="TEXT", help="a note for the agents of the continuing phases"
    )
    retry.add_argument(
        "--from",
        dest="from_phase",
        metavar="PHASE",
        help="continue from this phase (a phase name like review_2 or a step like review) "
        "instead of the one that failed; the phases after it run again",
    )
    retry.add_argument(
        "--auto",
        action="store_true",
        help="deprecated compatibility option; follows repository-local auto_continue",
    )
    retry.add_argument(
        "--allow-writes",
        action="store_true",
        help="after a writes_violation or writes_needed: add the paths written outside "
        "writes or reported in needs_writes to the task's writes (committed to base by "
        "base_changes), then retry from the failed phase with the paths in the Retry note. "
        "Paths in protected_files, "
        "in the backlog or in the main checkout are never added. If base lacks the new writes "
        "(writes_not_committed), commit the backlog, then factory task retry ID",
    )
    retry.add_argument(
        "--harness",
        metavar="NAME",
        help="continue with every agent on this harness (e.g. after a quota failure); it "
        "replaces the run's harness override, the switch is recorded in the run and its PR. "
        "Without --model the computer's model for the harness, else its preset model",
    )
    retry.add_argument(
        "--model", metavar="NAME", help="model of every agent for the retry (with --harness)"
    )
    retry.add_argument(
        "--thinking",
        metavar="LEVEL",
        choices=THINKING_LEVELS,
        help="thinking level of every agent for the retry (with --harness)",
    )

    for child in (
        add,
        edit,
        follow_up,
        link,
        show,
        list_,
        runs,
        run,
        approve,
        return_,
        resolve,
        publish,
        clean,
        stop,
        pause,
        resume,
        retry,
    ):
        child.add_argument("--json", action="store_true", help="print machine-readable JSON")
        child.add_argument(
            "--repo",
            metavar="PATH",
            help="repository root with .factory/config.yaml (default: the current git repo)",
        )


def _dep_label(backlog: Backlog, dep: str) -> str:
    from aifactory.backlog import Task, derived_state, is_done, progress

    node = backlog.by_id.get(dep)
    if node is None:
        return f"{dep} (unknown)"
    if isinstance(node, Task):
        return f"{dep} ({derived_state(backlog, node)})"
    if is_done(node):
        return f"{dep} (done)"
    done, total = progress(node)
    return f"{dep} ({done}/{total})"


def _task_show(args: argparse.Namespace, root: Path) -> int:
    from aifactory.backlog import (
        blocks,
        check_backlog,
        derived_state,
        effective_workflow,
        effective_writes,
        find_task,
        followed_by,
        load_for_edit,
        task_to_json,
        waits_for,
    )
    from aifactory.runstore.read import task_prs_for, task_runs_for

    backlog = load_for_edit(root)
    issues = check_backlog(backlog)
    task = find_task(backlog, args.task_id, issues)
    reverse = blocks(backlog)
    own_issues = [i.to_dict() for i in issues if i.path == task.path]
    runs = task_runs_for(root, task.id)
    prs = task_prs_for(root, task.id)
    followers = followed_by(backlog, task.id)
    if args.json:
        data = {
            "task": task_to_json(backlog, task, reverse),
            "followed_by": followers,
            "body": task.body,
            "issues": own_issues,
            "runs": [r.to_json() for r in runs],
            "prs": [p.to_json() for p in prs],
        }
        return emit_ok(data, problem_warnings(len(own_issues)))

    def joined(values: list[str]) -> str:
        return ", ".join(values) or "-"

    workflow = effective_workflow(task)
    rows = [
        ("id", task.id),
        ("title", task.title),
        ("path", task.path),
        ("status", task.status),
        ("state", derived_state(backlog, task)),
        ("workflow", "-" if workflow is None else str(workflow)),
        ("depends", joined([_dep_label(backlog, d) for d in task.depends_on])),
        ("waits for", (waits_for(backlog, task) if task.status == "todo" else "") or "-"),
        ("related", joined(task.related)),
        ("writes", joined(effective_writes(task))),
        ("blocks", joined(reverse.get(task.id, []))),
        ("follows", task.follows or "-"),
        ("followed", joined(followers)),
    ]
    for name, value in rows:
        print(f"{name:<9} {value}")
    print()
    print(task.body.strip("\n"))
    print()
    if runs:
        print("runs:")
        for r in runs:
            head = r.head_sha[:7] if r.head_sha else "-"
            line = f"  {r.started_at}  {r.run_id}  {r.shown_state:<9}  {r.branch}  {head}"
            blocked = _run_blocked_text(r)
            print(f"{line}  {blocked}" if blocked else line)
    else:
        print("no runs")
    if prs:
        print("prs:")
        for p in prs:
            print(f"  {p.state:<6}  {p.url}  {p.branch}")
    for issue in own_issues:
        print(f"{issue['path']}: {issue['code']}: {issue['message']}", file=sys.stderr)
    return 0


def _run_blocked_text(run: TaskRunRow) -> str:
    """``blocked: waits for A, B`` for a run that ended blocked; empty for any other run."""
    from aifactory import failure

    if run.shown_failure_kind != failure.BLOCKED:
        return ""
    detail = run.failure_detail_dict() or {}
    blockers = [str(b) for b in detail.get("blockers") or []]
    return f"blocked: waits for {', '.join(blockers) or '-'}"


def _task_runs(args: argparse.Namespace, root: Path) -> int:
    from aifactory.runstore.read import task_runs_for

    runs = task_runs_for(root, args.task_id)
    if args.json:
        return emit_ok({"task_id": args.task_id, "runs": [r.to_json() for r in runs]})
    if not runs:
        print(f"no runs of {args.task_id}")
        return 0
    for r in runs:
        kind = r.shown_failure_kind or "-"
        error = (r.error or "").splitlines()[0][:100] if r.error else ""
        error = _run_blocked_text(r) or error
        print(f"{r.started_at}  {r.run_id}  {r.shown_state:<9}  {kind:<16}  {error}".rstrip())
    return 0


def _task_priority(root: Path, backlog: Any) -> dict[str, dict[str, Any]]:
    """Tasks with priority (blocked during a run, and their blockers) by id: rank and owners.

    Read from the trace DB (``task_priority``); a missing or unreadable DB gives none.
    """
    import sqlite3

    from aifactory.errors import TaskRunError
    from aifactory.run.queue_select import priority_map
    from aifactory.runstore.read import existing_store

    try:
        store = existing_store(root)
        if store is None:
            return {}
        try:
            records = store.priorities()
        finally:
            store.close()
    except (TaskRunError, sqlite3.Error):
        return {}
    own = {p.task_id: list(p.blockers) for p in records}
    found: dict[str, dict[str, Any]] = {
        task_id: {"rank": rank, "of": owners, "blockers": own.get(task_id)}
        for rank, (task_id, owners) in enumerate(priority_map(backlog, records).items())
    }
    for task_id, blockers in own.items():  # blocked, its blockers go first
        node = backlog.by_id.get(task_id)
        if task_id not in found and getattr(node, "status", None) == "todo":
            found[task_id] = {"rank": None, "of": [task_id], "blockers": blockers}
    return found


def _task_list(args: argparse.Namespace, root: Path) -> int:
    from aifactory.backlog import (
        STATUS_FILTERS,
        TaskEditError,
        blocks,
        check_backlog,
        derived_state,
        iter_containers,
        iter_tasks,
        load_for_edit,
        select_projects,
        task_matches,
        task_to_json,
        waits_for,
    )
    from aifactory.backlog.derived import descendant_tasks

    status: str | None = args.status
    if status is not None and status not in STATUS_FILTERS:
        raise TaskEditError(
            "invalid_status",
            f"invalid status filter '{status}', allowed: {', '.join(STATUS_FILTERS)}",
        )
    backlog = load_for_edit(root)
    issues = check_backlog(backlog)
    tasks = list(iter_tasks(backlog))
    if args.project is not None:
        try:
            projects = select_projects(backlog, args.project)
        except LookupError:
            level = backlog.settings.levels[0]
            raise TaskEditError(
                "unknown_project", f"unknown {level} '{args.project}'", id=args.project
            ) from None
        allowed = {t.path for p in projects for t in descendant_tasks(p)}
        tasks = [t for t in tasks if t.path in allowed]
    if args.step is not None:
        level = backlog.settings.levels[-2]
        steps = [
            c for c in iter_containers(backlog.containers) if c.id == args.step and c.level == level
        ]
        if not steps:
            raise TaskEditError("unknown_step", f"unknown {level} '{args.step}'", id=args.step)
        allowed = {t.path for s in steps for t in descendant_tasks(s)}
        tasks = [t for t in tasks if t.path in allowed]
    tasks = [t for t in tasks if task_matches(backlog, t, status)]
    prio = _task_priority(root, backlog)
    if args.json:
        reverse = blocks(backlog)
        data = {
            "filters": {"status": status, "project": args.project, "step": args.step},
            "tasks": [
                {**task_to_json(backlog, t, reverse), "priority": prio.get(t.id)} for t in tasks
            ],
            "issues": [i.to_dict() for i in issues],
        }
        return emit_ok(data, problem_warnings(len(issues)))
    width = max((len(t.id) for t in tasks), default=0)
    for task in tasks:
        state = derived_state(backlog, task)
        line = f"{task.id:<{width}} {state:<9} {task.title}"
        if state == "blocked":
            line += f"  (waits for: {waits_for(backlog, task)})"
        if task.id in prio:
            rank = prio[task.id]["rank"]
            after = ", ".join(prio[task.id]["blockers"] or [])
            mark = f"#{rank + 1}" if rank is not None else f"po {after}"
            line += f"  [přednostně {mark}]"
        print(line)
    if issues:
        print(f"{len(issues)} problem(s), run 'factory backlog check'", file=sys.stderr)
    return 0


def _task_write(args: argparse.Namespace, root: Path) -> int:
    from aifactory.backlog import add_task, blocks, edit_task, link_task, task_to_json

    command: str = args.task_command
    if command == "edit" and args.auto_merge is not None:
        return _deprecated_operating_flag(args, "auto_merge")
    if command == "add":
        result = add_task(
            root,
            args.step,
            args.title,
            task_id=args.task_id,
            slug=args.slug,
            workflow=args.workflow,
            writes=args.writes,
            depends_on=args.depends_on,
            related=args.related,
            body=args.body,
            assignee=args.assignee,
        )
    elif command == "edit":
        result = edit_task(
            root,
            args.task_id,
            title=args.title,
            status=args.status,
            workflow=args.workflow,
            clear_workflow=args.clear_workflow,
            writes=args.writes,
            clear_writes=args.clear_writes,
            auto_merge={"on": True, "off": False}.get(args.auto_merge or ""),
            clear_auto_merge=args.auto_merge == "inherit",
            assignee=args.assignee if args.assignee != "inherit" else None,
            clear_assignee=args.assignee == "inherit",
        )
    else:
        result = link_task(
            root,
            args.task_id,
            depends_on=args.depends_on,
            related=args.related,
            remove=args.remove,
        )
    if args.json:
        data = {
            "action": result.action,
            "changed": result.changed,
            "path": result.path,
            "task": task_to_json(result.backlog, result.task, blocks(result.backlog)),
            "issues": [i.to_dict() for i in result.issues],
        }
        return emit_ok(data, problem_warnings(len(result.issues)))
    if not result.changed:
        print(f"unchanged {result.task.id}")
    else:
        verb = "added" if result.action == "add" else "updated"
        print(f"{verb} {result.task.id} {result.path}")
    if result.issues:
        print(f"{len(result.issues)} problem(s), run 'factory backlog check'", file=sys.stderr)
    return 0


def _task_follow_up(args: argparse.Namespace, root: Path) -> int:
    from aifactory.backlog import (
        blocks,
        commit_backlog,
        follow_up_commit_message,
        follow_up_task,
        task_to_json,
    )

    result = follow_up_task(root, args.task_id, args.body, title=args.title)
    data: dict[str, Any] = {
        "action": result.action,
        "changed": result.changed,
        "path": result.path,
        "task": task_to_json(result.backlog, result.task, blocks(result.backlog)),
        "issues": [i.to_dict() for i in result.issues],
    }
    warnings = problem_warnings(len(result.issues))
    if not args.run:
        if args.json:
            return emit_ok(data, warnings)
        print(f"added {result.task.id} {result.path} (follows {args.task_id})")
        if result.issues:
            print(f"{len(result.issues)} problem(s), run 'factory backlog check'", file=sys.stderr)
        return 0
    from aifactory.run import run_chain

    new_id = result.task.id
    if not args.json:
        print(f"added {new_id} {result.path} (follows {args.task_id})")
    committed = commit_backlog(root, follow_up_commit_message(new_id, args.task_id))
    data["commit"] = committed.to_json()
    if not args.json and committed.commit:
        print(f"backlog committed to {committed.base} ({committed.commit[:7]})")
    args.task_id = new_id
    args.task_command = "run"
    return _chain_output(
        args, lambda: run_chain(root, new_id, note=None, force=False, auto=False), data
    )


def _run_json(result: TaskRunResult) -> dict[str, Any]:
    return {
        "run": result.run.to_json(),
        "pr": result.pr.to_json() if result.pr is not None else None,
        "pr_error": result.pr_error,
        "auto_merge": result.auto_merge.to_json() if result.auto_merge is not None else None,
        "resolve_run": result.resolve_run.run.to_json() if result.resolve_run is not None else None,
        "follow_ups": result.follow_ups.to_json() if result.follow_ups is not None else None,
        "done_without_pr": result.done_without_pr,
    }


def _emit_runs(
    data: dict[str, Any], results: Sequence[TaskRunResult], warnings: Iterable[str]
) -> int:
    """The envelope of a run: ``run_failed``/``pr_failed`` from the first failing run."""
    failed = next((r for r in results if not r.ok), None)
    if failed is None:
        return emit_ok(data, warnings)
    from aifactory.runstore.rows import SUCCEEDED

    if failed.run.state == SUCCEEDED and failed.pr_error is not None:
        # the run itself succeeded; only the push or the pull request failed
        return emit_fail("pr_failed", failed.pr_error, exit_code=1, data=data, warnings=warnings)
    message = failed.run.error or f"run {failed.run.run_id} {failed.run.state}"
    return emit_fail(
        "run_failed", message, exit_code=1, data=data, id=failed.run.task_id, warnings=warnings
    )


def _print_run(command: str, result: TaskRunResult) -> None:
    row = result.run
    print(f"run {row.run_id} {row.state}: branch {row.branch}, worktree {row.worktree}")
    if result.pr is not None:
        print(f"pr {result.pr.url}")
    if result.done_without_pr:
        print(f"no changes: task {row.task_id} is done without a pull request")
    if row.error:
        print(f"factory task {command}: {row.error}", file=sys.stderr)
    if result.pr_error:
        print(f"factory task {command}: pr: {result.pr_error}", file=sys.stderr)
    if result.follow_ups is not None:
        for item in result.follow_ups.created:
            kind = "blocks this task" if item.blocks else "new task"
            print(f"follow-up ({kind}): {item.id} {item.title}")
    merge = result.auto_merge
    if merge is not None and merge.merged:
        sha = f" ({merge.merge_sha[:7]})" if merge.merge_sha else ""
        base = result.pr.base if result.pr is not None else "base"
        print(f"auto-merge: merged {merge.pr_url} into {base}{sha}")
    elif merge is not None:
        print(
            f"auto-merge: not merged ({merge.code}): {merge.reason}; the PR stays open, "
            f"approve it with factory task approve {row.task_id}"
        )


def _run_output(args: argparse.Namespace, start: Callable[[], TaskRunResult]) -> int:
    """Start a run with `start` and print its result (``task return``, ``resolve``)."""
    command: str = args.task_command
    if args.json:
        # The engine narrates on stdout; with --json stdout carries only the result.
        with contextlib.redirect_stdout(sys.stderr):
            result = start()
        return _emit_runs(_run_json(result), [result], result.warnings)
    result = start()
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    _print_run(command, result)
    return 0 if result.ok else 1


def _member_run(
    root: Path,
    task_id: str,
    note: str | None,
    force: bool,
    code: CodeRunner | None = None,
    started_by: str = "manual",
    agents_override: dict[str, str] | None = None,
) -> TaskRunResult:
    """One run of a parallel chain: the task, then its auto-merge under the merge lock.

    A PR in conflict is resolved once (``resolve_and_merge``) outside the lock and merged
    under it, as the sequential chain does; the resolve run is ``result.resolve_run``.
    `code` reaches the resolve run only (tests).
    """
    from aifactory.run import run_task
    from aifactory.run.chain import member_auto_merge

    result = run_task(
        root,
        task_id,
        note=note,
        force=force,
        started_by=started_by,
        agents_override=agents_override,
    )
    return member_auto_merge(root, result, code=code)


def _agents_override(args: argparse.Namespace) -> dict[str, str] | None:
    """``--harness``/``--model``/``--thinking`` of ``task run``; None when none is given."""
    values = {
        key: value
        for key in ("harness", "model", "thinking")
        if (value := getattr(args, key, None))
    }
    return values or None


def _task_run(args: argparse.Namespace, root: Path) -> int:
    from aifactory.run import run_chain

    if getattr(args, "member", False):

        def member() -> TaskRunResult:
            return _member_run(
                root,
                args.task_id,
                args.note,
                args.force,
                started_by=args.started_by,
                agents_override=_agents_override(args),
            )

        return _run_output(args, member)

    def start() -> ChainResult:
        return run_chain(
            root,
            args.task_id,
            note=args.note,
            force=args.force,
            auto=args.auto,
            automatic=args.started_by == "auto-continue",
            agents_override=_agents_override(args),
        )

    return _chain_output(args, start)


def _chain_output(
    args: argparse.Namespace,
    start: Callable[[], ChainResult],
    extra: dict[str, Any] | None = None,
) -> int:
    """Start a chain with `start` and print it (``task run``, ``task retry``)."""
    if getattr(args, "auto", False):
        print(
            "warning: --auto is deprecated and has no effect; use factory config local set "
            "--auto-continue on/off. Repository-local off always blocks automatic starts.",
            file=sys.stderr,
        )
    if args.json:
        with contextlib.redirect_stdout(sys.stderr):
            chain = start()
        chain_json = chain.to_json()
        data = _run_json(chain.runs[0])
        keys = (
            "runs",
            "stop",
            "stop_detail",
            "waiting",
            "max_parallel_runs",
            "exclusive",
            "chain_id",
        )
        data["chain"] = {k: chain_json[k] for k in keys}
        data.update(extra or {})
        if chain.stop == "failed":
            locked = next(
                (s for s in chain.waiting if s.detail.startswith("trace_db_locked:")), None
            )
            if locked is not None:
                return emit_fail(
                    "trace_db_locked",
                    locked.detail,
                    exit_code=2,
                    data=data,
                    id=locked.task_id,
                    warnings=chain.runs[0].warnings,
                )
        return _emit_runs(data, chain.runs, chain.runs[0].warnings)
    chain = start()
    for warning in chain.runs[0].warnings:
        print(f"warning: {warning}", file=sys.stderr)
    _print_chain(chain)
    return 0 if chain.ok else 1


def _print_chain(chain: ChainResult) -> None:
    from aifactory.run import STOP_EXHAUSTED, STOP_NOT_MERGED

    if chain.stop == "disabled":
        from aifactory.run.policy import DISABLED_MESSAGE

        print(DISABLED_MESSAGE)
    if chain.max_parallel > 1:
        print(f"auto-continue: up to {chain.max_parallel} parallel runs")
    for result in chain.runs:
        _print_run("run", result)
    for task_id in chain.exclusive:
        print(f"exclusive: {task_id} ran alone (wide writes)")
    if len(chain.runs) > 1 or chain.waiting or chain.stop in (STOP_EXHAUSTED, STOP_NOT_MERGED):
        print(f"auto-continue: stopped ({chain.stop})")
        for skip in chain.waiting:
            print(f"waiting: {skip.task_id} {skip.reason}: {skip.detail}")
        if chain.stop == STOP_EXHAUSTED and not chain.waiting:
            print("auto-continue: nothing left to run")


def _task_return(args: argparse.Namespace, root: Path) -> int:
    from aifactory.review import return_task

    return _run_output(args, lambda: return_task(root, args.task_id, args.note))


def _task_resolve(args: argparse.Namespace, root: Path) -> int:
    from aifactory.review import resolve_task

    return _run_output(args, lambda: resolve_task(root, args.task_id))


def _task_retry(args: argparse.Namespace, root: Path) -> int:
    from aifactory.run import retry_chain

    run_id: str | None = args.run
    note: str | None = args.note
    extra: dict[str, Any] = {}
    if args.allow_writes:
        from aifactory.run.allow_writes import allow_writes, retry_note

        allowed = allow_writes(root, args.task_id, run_id)
        run_id = allowed.plan.run_id
        note = retry_note(allowed, note)
        extra["allow_writes"] = allowed.to_json()
        if not args.json:
            print(f"writes of {args.task_id} += {', '.join(allowed.plan.allow)}")
            for refused in allowed.plan.refused:
                print(f"not allowed: {refused.path}: {refused.to_json()['message']}")
            if allowed.commit.commit:
                print(f"backlog committed to {allowed.commit.base} ({allowed.commit.commit[:7]})")
    return _chain_output(
        args,
        lambda: retry_chain(
            root,
            args.task_id,
            run_id=run_id,
            note=note,
            from_phase=args.from_phase,
            auto=args.auto,
            agents_override=_agents_override(args),
        ),
        extra,
    )


def _task_approve(args: argparse.Namespace, root: Path) -> int:
    from aifactory.review import approve_task

    # ci-fix runs narrate on stdout; with --json stdout carries only the result
    with contextlib.redirect_stdout(sys.stderr) if args.json else contextlib.nullcontext():
        result = approve_task(root, args.task_id, allow_writes=args.allow_writes)
    if args.json:
        data, warnings = strip_payload(result.to_json())
        return emit_ok(data, warnings)
    sha = result.merge_sha[:7] if result.merge_sha else "?"
    print(f"merged {result.pr.url} into {result.pr.base} ({sha})")
    print("note: approve review sent" if result.reviewed else "note: approve review not sent")
    allowed = (result.allow_writes or {}).get("allow")
    if isinstance(allowed, list):
        print(f"writes of {args.task_id} += {', '.join(map(str, allowed))}")
    if result.ci_fix_runs:
        print(f"ci-fix runs: {', '.join(result.ci_fix_runs)}")
    for branch in result.deleted_branches:
        print(f"deleted branch {branch}")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if result.chain is not None:
        for run in result.chain.runs:
            for warning in run.warnings:
                print(f"warning: {warning}", file=sys.stderr)
        _print_chain(result.chain)
    return 0


def _task_publish(args: argparse.Namespace, root: Path) -> int:
    from aifactory.review import publish_task

    with contextlib.redirect_stdout(sys.stderr) if args.json else contextlib.nullcontext():
        result = publish_task(root, args.task_id)
    chain = result.chain
    if chain is not None:
        return _chain_output(args, lambda: chain)
    if args.json:
        data = result.to_json()
        if result.pr_error is not None:
            return emit_fail("pr_failed", result.pr_error, exit_code=1, data=data)
        return emit_ok(data)
    row = result.run
    if result.pr_error is not None:
        print(f"factory task publish: pr: {result.pr_error}", file=sys.stderr)
        return 1
    if result.done_without_pr:
        print(f"run {row.run_id}: no changes, task {row.task_id} is done without a pull request")
        return 0
    assert result.pr is not None
    print(f"run {row.run_id}: branch {row.branch} pushed")
    print(f"pr {result.pr.url}")
    return 0


def _task_clean(args: argparse.Namespace, root: Path) -> int:
    from aifactory.review import clean_worktrees

    result = clean_worktrees(root)
    if args.json:
        data, warnings = strip_payload(result.to_json())
        return emit_ok(data, warnings)
    for item in result.removed:
        print(f"removed {item['worktree']} ({item['reason']}, {item['task_id']})")
    print(f"{len(result.removed)} worktree(s) removed")
    for branch in result.deleted_branches:
        print(f"deleted branch {branch}")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _task_stop(args: argparse.Namespace, root: Path) -> int:
    from aifactory.errors import TaskRunError
    from aifactory.run import stop as run_stop
    from aifactory.runstore.read import task_runs_for

    task_id: str = args.task_id
    run_id: str | None = args.run
    if run_id is None:
        row = run_stop.running_run(root, task_id)
        if row is None:
            raise TaskRunError("run_not_running", f"task {task_id} has no running run")
        run_id = row.run_id
    elif run_id not in {r.run_id for r in task_runs_for(root, task_id)}:
        raise TaskRunError("invalid_value", f"run {run_id} is not a run of task {task_id}")
    result = run_stop.stop_run(root, run_id)
    if args.json:
        return emit_ok(result.to_json())
    print(f"stopped run {run_id} of {task_id}")
    return 0


def _task_pause(args: argparse.Namespace, root: Path, *, resume: bool) -> int:
    from aifactory.errors import TaskRunError
    from aifactory.run import pause_run, resume_run
    from aifactory.run import stop as run_stop
    from aifactory.runstore.read import task_runs_for

    task_id: str = args.task_id
    run_id: str | None = args.run
    if run_id is None:
        row = run_stop.running_run(root, task_id)
        if row is None:
            raise TaskRunError("run_not_running", f"task {task_id} has no running run")
        run_id = row.run_id
    elif run_id not in {r.run_id for r in task_runs_for(root, task_id)}:
        raise TaskRunError("invalid_value", f"run {run_id} is not a run of task {task_id}")
    result = resume_run(root, run_id) if resume else pause_run(root, run_id)
    if args.json:
        return emit_ok({"run": result.to_json()})
    if resume:
        print(f"resumed run {run_id} of {task_id}")
    else:
        print(f"pausing run {run_id} of {task_id}: it pauses when the current phase ends")
    return 0


def _task(args: argparse.Namespace) -> int:
    if args.task_command is None:
        args.task_parser.print_help()
        return 0
    from aifactory.backlog import TaskEditError
    from aifactory.config import ConfigError
    from aifactory.errors import TaskRunError
    from aifactory.review import ReviewError

    command: str = args.task_command
    root = _backlog_root(args.repo)
    try:
        if command == "run":
            return _task_run(args, root)
        if command == "approve":
            return _task_approve(args, root)
        if command == "return":
            return _task_return(args, root)
        if command == "resolve":
            return _task_resolve(args, root)
        if command == "retry":
            return _task_retry(args, root)
        if command == "publish":
            return _task_publish(args, root)
        if command == "clean":
            return _task_clean(args, root)
        if command == "stop":
            return _task_stop(args, root)
        if command in ("pause", "resume"):
            return _task_pause(args, root, resume=command == "resume")
        if command == "show":
            return _task_show(args, root)
        if command == "list":
            return _task_list(args, root)
        if command == "runs":
            return _task_runs(args, root)
        if command == "follow-up":
            return _task_follow_up(args, root)
        return _task_write(args, root)
    except (ConfigError, TaskEditError, TaskRunError, ReviewError) as exc:
        return fail_from_exception(exc, args.json, f"factory task {command}")
