"""``giro backlog``."""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

from aifactory.cli.envelope import emit_fail, emit_ok, fail_from_exception, problem_warnings
from aifactory.skill.envelope import strip_payload


def _add_backlog_commands(parser: argparse.ArgumentParser) -> None:
    from aifactory.backlog.model import CONTAINER_KEYS, STATUS_FILTERS

    parser.set_defaults(backlog_parser=parser)
    sub = parser.add_subparsers(dest="backlog_command", metavar="<subcommand>")
    check = sub.add_parser(
        "check",
        help="validate the backlog and list every problem",
        description="Load the backlog and report duplicate ids, unknown references, "
        "dependency cycles, invalid states and missing fields. Exits 1 on problems.",
    )
    list_ = sub.add_parser(
        "list",
        help="print the backlog tree with derived states",
        description="Print the backlog tree with task states (ready/blocked derived from "
        "depends_on) and progress of every container.",
    )
    sync = sub.add_parser(
        "sync",
        help="open one PR that marks tasks done whose PR was merged outside factory",
        description="Refresh the states of task PRs; for tasks whose PR was merged outside "
        "factory and that are not done in base, open one PR that sets status: done and adds "
        "a line to '## Běhy'. The commit goes only to its own branch factory-sync/<n>, never "
        "to base, and the sync PR is not merged. Closed PRs leave the task alone.",
    )
    auto = sub.add_parser(
        "auto-continue",
        help="deprecated; use factory config local set --auto-continue on|off",
        description="Deprecated legacy write. Use factory config local set --auto-continue "
        "on|off --repo PATH. Preview old values with factory config local legacy; "
        "explicitly take them with factory config local import. Nothing is committed.",
    )
    auto.add_argument("id", metavar="ID", help="project or step id")
    mode = auto.add_mutually_exclusive_group(required=True)
    mode.add_argument("--on", dest="auto_mode", action="store_const", const="on")
    mode.add_argument("--off", dest="auto_mode", action="store_const", const="off")
    mode.add_argument(
        "--inherit",
        dest="auto_mode",
        action="store_const",
        const="inherit",
        help="remove the field, inherit it from the parent",
    )
    merge = sub.add_parser(
        "auto-merge",
        help="deprecated; use factory config local set --auto-merge on|off",
        description="Deprecated legacy write. Use factory config local set --auto-merge "
        "on|off --repo PATH. Preview old values with factory config local legacy; "
        "explicitly take them with factory config local import. Nothing is committed.",
    )
    merge.add_argument("id", metavar="ID", help="project or step id")
    merge_mode = merge.add_mutually_exclusive_group(required=True)
    merge_mode.add_argument("--on", dest="auto_mode", action="store_const", const="on")
    merge_mode.add_argument("--off", dest="auto_mode", action="store_const", const="off")
    merge_mode.add_argument(
        "--inherit",
        dest="auto_mode",
        action="store_const",
        const="inherit",
        help="remove the field, inherit it from the parent",
    )
    commit = sub.add_parser(
        "commit",
        help="commit the backlog working-tree changes to base (only files in the backlog root)",
        description="Commit every change under the backlog root <docs_root>/backlog (new, "
        "edited and deleted task files) to base, so runs see it. Other changes in the "
        "checkout are left alone. The "
        "main checkout must be on base and the backlog must be valid. With a remote, the "
        "commit is pushed.",
    )
    commit.add_argument("-m", "--message", metavar="TEXT", help="commit message")
    add = sub.add_parser(
        "add",
        help="create a project, or a step in a project (a directory with index.md)",
        description="Create a project (without PARENT) or a step of project PARENT: the "
        "directory <code>-<slug> with an index.md holding id and title (a step's directory "
        "drops the project's prefix: M01-S03 -> S03-<slug>). The id matches the task id "
        "pattern, is unique, and a step's id starts with its project's id and '-'. The "
        "backlog is validated before writing; a change that would break it is rejected. "
        "Nothing is committed (factory backlog commit).",
    )
    add.add_argument(
        "parent", nargs="?", metavar="PARENT", help="project id for a new step (none: a project)"
    )
    add.add_argument(
        "--id",
        dest="container_id",
        required=True,
        metavar="CODE",
        help="short code, e.g. M01 or S10; a step's id is PARENT-CODE",
    )
    add.add_argument("--title", required=True, metavar="TEXT", help="short title")
    add.add_argument("--body", default="", metavar="TEXT", help="description in index.md")
    edit = sub.add_parser(
        "edit",
        help="change the title and settings of a project or step (its index.md)",
        description="Change the title of a project or step and set or remove (--clear) the "
        f"keys {', '.join(CONTAINER_KEYS)} of its index.md. Other keys and the description "
        "stay as they are. The backlog is validated before writing; nothing is committed.",
    )
    edit.add_argument("id", metavar="ID", help="project or step id")
    edit.add_argument("--title", metavar="TEXT", help="new title")
    edit.add_argument("--workflow", metavar="NAME", help="set the workflow")
    edit.add_argument("--writes", nargs="*", metavar="PATH", help="set writes (none = [])")
    edit.add_argument("--auto-continue", choices=("on", "off"), help="set auto_continue")
    edit.add_argument(
        "--auto-heal",
        choices=("on", "off"),
        help="set auto_heal: the chain's automatic actions after a failure (default on)",
    )
    edit.add_argument(
        "--quota-switch",
        nargs="*",
        metavar="HARNESS",
        help="project only: set quota_switch, the backup harnesses in order a run switches "
        "to when its harness runs out of quota (none = [], off)",
    )
    edit.add_argument(
        "--quota-switch-back",
        choices=("on", "off"),
        help="project only: set quota_switch_back: new runs go back up the chain once a "
        "harness has its limit back",
    )
    edit.add_argument(
        "--clear",
        nargs="+",
        action="extend",
        default=[],
        choices=CONTAINER_KEYS,
        metavar="KEY",
        help=f"remove keys (inherit them again): {', '.join(CONTAINER_KEYS)}",
    )
    propose = sub.add_parser(
        "propose",
        help="let a read-only agent propose tasks from an intent, placed in a step it chooses "
        "or one you give",
        description="Run the read-only agent 'proposer' (prompts overridable in "
        ".factory/prompts/proposer/) in an isolated checkout of the base commit. It explores "
        "the repo and the backlog and proposes a new step of --project with its tasks, or new "
        "tasks of the existing --step. Without either it places them itself: in an existing "
        "step, a new step of an existing project, or a new project with a new step, with a "
        "one-sentence placement_reason. Per task it gives the title, the assignment with a 'Hotovo "
        "znamená' section, depends_on (also on existing tasks), writes and a workflow of the "
        "catalog. The proposal is validated like 'factory task add' (ids, dependencies, no "
        "cycles or self references, existing workflows); an invalid one goes back to the agent "
        "once, a second invalid one fails with proposal_invalid and writes nothing. Findings of "
        "the quality check of writes (files the assignment names, protected_files and the "
        "backlog, overlap of tasks that can run at the same time) go back in the same "
        "correction; the rest are shown in the preview. Without --apply only the preview "
        "(project ->) step -> tasks is shown; --apply writes the files and commits nothing "
        "(factory backlog commit).",
    )
    propose.add_argument("intent", metavar="INTENT", help="what the new tasks should achieve")
    target = propose.add_mutually_exclusive_group()
    target.add_argument(
        "--project",
        metavar="ID",
        help="propose a new step of this project (default: the agent chooses the place)",
    )
    target.add_argument(
        "--step",
        metavar="ID",
        help="propose new tasks of this existing step (default: the agent chooses the place)",
    )
    propose.add_argument(
        "--apply", action="store_true", help="write the proposal to the backlog (no commit)"
    )
    for child in (check, list_, sync, auto, merge, commit, add, edit, propose):
        child.add_argument("--json", action="store_true", help="print machine-readable JSON")
        child.add_argument(
            "--repo",
            metavar="PATH",
            help="repository root with .factory/config.yaml (default: the current git repo)",
        )
    list_.add_argument("--status", choices=STATUS_FILTERS, help="show only tasks in this state")
    list_.add_argument("--project", metavar="ID", help="show only this project (id or directory)")


def _backlog_root(repo: str | None) -> Path:
    from aifactory.config import ConfigError, repo_root

    if repo:
        return Path(repo)
    try:
        return repo_root(Path.cwd())
    except ConfigError:
        return Path.cwd()


def _backlog_sync(args: argparse.Namespace) -> int:
    from aifactory.config import ConfigError
    from aifactory.errors import TaskRunError
    from aifactory.review import ReviewError, sync_backlog

    try:
        result = sync_backlog(_backlog_root(args.repo))
    except (ConfigError, ReviewError, TaskRunError) as exc:
        return fail_from_exception(exc, args.json, "factory backlog sync")
    if args.json:
        data, warnings = strip_payload(result.to_json())
        return emit_ok(data, warnings)
    ids = ", ".join(t.task_id for t in result.tasks)
    if result.pr is None:
        print("nothing to sync")
    elif result.created:
        print(f"opened {result.pr.url}: done for {ids}")
    elif result.updated:
        print(f"updated {result.pr.url}: done for {ids}")
    else:
        print(f"sync PR {result.pr.url} already open: done for {ids}")
    for skip in result.skipped:
        print(f"skipped {skip.task_id}: {skip.reason}")
    for state in result.states:
        print(f"PR {state['url']} of {state['task_id']} is {state['state']}")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _backlog_auto_continue(args: argparse.Namespace) -> int:
    return _deprecated_operating_flag(args, "auto_continue")


def _backlog_auto_merge(args: argparse.Namespace) -> int:
    return _deprecated_operating_flag(args, "auto_merge")


def _deprecated_operating_flag(args: argparse.Namespace, key: str) -> int:
    option = key.replace("_", "-")
    message = (
        f"Legacy {option} writes are deprecated. Use factory config local set "
        f"--{option} on|off --repo PATH; preview with factory config local legacy --repo PATH "
        "and explicitly import with factory config local import --repo PATH."
    )
    if args.json:
        return emit_fail("deprecated_option", message, exit_code=2)
    print(message, file=sys.stderr)
    return 2


def _backlog_commit(args: argparse.Namespace) -> int:
    from aifactory.backlog import TaskEditError, commit_backlog
    from aifactory.config import ConfigError

    try:
        result = commit_backlog(_backlog_root(args.repo), args.message)
    except (ConfigError, TaskEditError) as exc:
        return fail_from_exception(exc, args.json, "factory backlog commit")
    if args.json:
        return emit_ok(result.to_json(), result.warnings)
    if not result.committed:
        print("nothing to commit in the backlog")
        return 0
    short = (result.commit or "")[:12]
    pushed = ", pushed" if result.pushed else ""
    print(f"committed {len(result.paths)} backlog file(s) to {result.base} ({short}{pushed})")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _backlog_propose(args: argparse.Namespace) -> int:
    from aifactory.backlog import TaskEditError
    from aifactory.config import ConfigError
    from aifactory.workflow.propose import Target, format_preview, propose

    try:
        # the agent's progress goes to stderr; stdout carries only the preview or JSON
        with contextlib.redirect_stdout(sys.stderr):
            data = propose(
                _backlog_root(args.repo),
                args.intent,
                Target(project=args.project, step=args.step),
                apply=args.apply,
            )
    except (ConfigError, TaskEditError) as exc:
        return fail_from_exception(exc, args.json, "factory backlog propose")
    if args.json:
        return emit_ok(data)
    print(format_preview(data))
    print()
    if data["applied"]:
        print(f"written {len(data['files'])} file(s); commit with 'factory backlog commit'")
    else:
        print("preview only; write it with --apply")
    return 0


def _backlog_container_write(args: argparse.Namespace) -> int:
    from aifactory.backlog import (
        TaskEditError,
        add_container,
        container_detail_json,
        edit_container,
    )
    from aifactory.config import ConfigError

    command: str = args.backlog_command
    try:
        if command == "add":
            result = add_container(
                _backlog_root(args.repo),
                args.parent,
                args.container_id,
                args.title,
                body=args.body,
            )
        else:
            values: dict[str, object] = {}
            for key in ("workflow", "writes"):
                value = getattr(args, key)
                if value is not None:
                    values[key] = value
            if args.auto_continue is not None:
                values["auto_continue"] = args.auto_continue == "on"
            if args.auto_heal is not None:
                values["auto_heal"] = args.auto_heal == "on"
            if args.quota_switch is not None:
                values["quota_switch"] = args.quota_switch
            if args.quota_switch_back is not None:
                values["quota_switch_back"] = args.quota_switch_back == "on"
            result = edit_container(
                _backlog_root(args.repo), args.id, title=args.title, values=values, clear=args.clear
            )
    except (ConfigError, TaskEditError) as exc:
        return fail_from_exception(exc, args.json, f"factory backlog {command}")
    container = result.container
    if args.json:
        data = {
            "action": result.action,
            "changed": result.changed,
            "path": result.path,
            "container": container_detail_json(result.backlog, container),
            "issues": [i.to_dict() for i in result.issues],
        }
        return emit_ok(data, problem_warnings(len(result.issues)))
    if not result.changed:
        print(f"unchanged {container.id}")
    else:
        verb = "added" if result.action == "add" else "updated"
        print(f"{verb} {container.level} {container.id} {result.path}")
    if result.issues:
        print(f"{len(result.issues)} problem(s), run 'factory backlog check'", file=sys.stderr)
    return 0


def _backlog(args: argparse.Namespace) -> int:
    if args.backlog_command is None:
        args.backlog_parser.print_help()
        return 0
    from aifactory.backlog import (
        backlog_to_json,
        check_backlog,
        counts,
        format_tree,
        issues_to_json,
        load_backlog,
    )
    from aifactory.config import ConfigError

    command: str = args.backlog_command
    if command == "sync":
        return _backlog_sync(args)
    if command in ("add", "edit"):
        return _backlog_container_write(args)
    if command == "auto-continue":
        return _backlog_auto_continue(args)
    if command == "auto-merge":
        return _backlog_auto_merge(args)
    if command == "commit":
        return _backlog_commit(args)
    if command == "propose":
        return _backlog_propose(args)
    try:
        backlog = load_backlog(_backlog_root(args.repo))
    except ConfigError as exc:
        return fail_from_exception(exc, args.json, f"factory backlog {command}")
    issues = check_backlog(backlog)

    if command == "check":
        if args.json:
            data, warnings = strip_payload(issues_to_json(backlog, issues))
            data["issues"] = data.pop("errors", [])
            if not issues:
                return emit_ok(data, warnings)
            return emit_fail(
                "backlog_invalid",
                f"{len(issues)} problem(s) in the backlog",
                exit_code=1,
                data=data,
                issues=data["issues"],
                warnings=warnings,
            )
        if issues:
            for issue in issues:
                print(f"{issue.path}: {issue.code}: {issue.message}")
            print(f"{len(issues)} error(s)")
        else:
            summary = ", ".join(f"{n} {level}" for level, n in counts(backlog).items())
            print(f"OK: {summary}")
        return 1 if issues else 0

    missing_dir = any(i.code == "missing_backlog_dir" for i in issues)
    try:
        if args.json:
            payload = backlog_to_json(backlog, issues, status=args.status, project=args.project)
        else:
            lines = format_tree(backlog, status=args.status, project=args.project)
    except LookupError:
        message = f"unknown {backlog.settings.levels[0]} '{args.project}'"
        if args.json:
            return emit_fail("unknown_project", message, exit_code=2, id=args.project)
        print(f"factory backlog list: {message}", file=sys.stderr)
        return 2
    if args.json:
        data, warnings = strip_payload(payload)
        if missing_dir:
            missing = next(i for i in issues if i.code == "missing_backlog_dir")
            return emit_fail(
                "missing_backlog_dir",
                f"backlog directory '{missing.path}' not found",
                exit_code=1,
                data=data,
                path=missing.path,
                issues=[i.to_dict() for i in issues if i.code == "missing_backlog_dir"],
                warnings=warnings,
            )
        return emit_ok(data, [*warnings, *problem_warnings(len(issues))])
    if missing_dir:
        missing = next(i for i in issues if i.code == "missing_backlog_dir")
        print(f"error: backlog directory '{missing.path}' not found", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    if issues:
        print(f"{len(issues)} problem(s), run 'factory backlog check'", file=sys.stderr)
    return 0
