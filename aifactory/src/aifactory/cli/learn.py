"""``giro learn``."""

from __future__ import annotations

import argparse
import contextlib
import sys

from aifactory.cli.backlog import _backlog_root
from aifactory.cli.envelope import emit_ok, fail_from_exception


def _add_learn_commands(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="learn_command", metavar="<command>", required=True)
    scan = sub.add_parser(
        "scan",
        help="group objections repeated across task runs into lessons",
        description="Read the objections of every task run of the repo from the trace DB "
        "(blocking items and unmet findings of review and test review envelopes, test triage "
        "of failed checks, revise phases) and let the read-only agent 'learner' (prompts "
        "overridable in .factory/prompts/learner/) group similar ones into lessons, in an "
        "isolated checkout of the base commit. A lesson has a short rule, where it belongs "
        "(CLAUDE.md, AGENTS.md, a skill of the repo or the prompt of a role), its occurrences "
        "(the distinct runs), the runs and snippets of the objections. A lesson seen in one "
        "run only is not proposed, nor one a person accepted (factory learn accept) or "
        "rejected (factory learn dismiss). Proposed lessons are saved in the trace DB; nothing "
        "in the repo is written. With --all the objections of every repo registered in the "
        "dashboard are read and every lesson names the repo its rule belongs to.",
    )
    scan.add_argument(
        "--since",
        metavar="DATE",
        help="only objections from this date (YYYY-MM-DD) or ISO timestamp on",
    )
    scan.add_argument(
        "--all",
        dest="all_repos",
        action="store_true",
        help="read the objections of every repo registered in the dashboard; each lesson "
        "names the repo its rule belongs to and is saved in that repo's trace DB (factory "
        "shortcomings go to the GIRO repo, target 'seed' only there)",
    )
    accept = sub.add_parser(
        "accept",
        help="turn a lesson into a backlog task that changes the repo's rules",
        description="File a lesson of 'factory learn scan' as a task in the step LEARN of the "
        "repo's project (the step is created when it is missing): workflow build-review, "
        "writes on the lesson's target only (CLAUDE.md, AGENTS.md, .claude/skills/<name>/ and "
        ".agents/skills/<name>/, or .factory/prompts/<role>/) and the rule with the objections "
        "of the runs as evidence. The backlog change is committed to base; the task then runs "
        "the ordinary way up to a pull request. The factory writes no rule itself. The project "
        "is --project, else the one of the tasks whose runs objected, else the only project. "
        "Accepting the lesson again shows its task and files nothing new.",
    )
    accept.add_argument("lesson", metavar="LESSON", help="lesson id from 'factory learn scan'")
    accept.add_argument("--project", metavar="ID", help="project whose LEARN step gets the task")
    dismiss = sub.add_parser(
        "dismiss",
        help="reject a lesson so that no scan offers it again",
        description="Mark a lesson of 'factory learn scan' rejected in the trace DB; later "
        "scans do not offer it (nor its objections) again.",
    )
    dismiss.add_argument("lesson", metavar="LESSON", help="lesson id from 'factory learn scan'")
    dismiss.add_argument("--reason", metavar="TEXT", help="why the lesson is rejected")
    for child in (scan, accept, dismiss):
        child.add_argument("--json", action="store_true", help="print machine-readable JSON")
        child.add_argument(
            "--repo",
            metavar="PATH",
            help="repository root with .factory/config.yaml (default: the current git repo)",
        )


def _learn(args: argparse.Namespace) -> int:
    from aifactory.backlog import TaskEditError
    from aifactory.config import ConfigError
    from aifactory.learn.accept import accept, format_accepted
    from aifactory.learn.scan import dismiss, format_scan, scan

    label = f"factory learn {args.learn_command}"
    root = _backlog_root(args.repo)
    try:
        if args.learn_command == "accept":
            outcome = accept(root, args.lesson, args.project)
            if args.json:
                return emit_ok(outcome.to_json(), outcome.warnings)
            print(format_accepted(outcome))
            for warning in outcome.warnings:
                print(f"warning: {warning}", file=sys.stderr)
            return 0
        if args.learn_command == "dismiss":
            data = dismiss(root, args.lesson, args.reason)
        else:
            # the agent's progress goes to stderr; stdout carries only the lessons or JSON
            with contextlib.redirect_stdout(sys.stderr):
                data = scan(root, args.since, all_repos=args.all_repos)
    except (ConfigError, TaskEditError) as exc:
        return fail_from_exception(exc, args.json, label)
    warnings = [str(w) for w in data.pop("warnings", [])]
    if args.json:
        return emit_ok(data, warnings)
    if args.learn_command == "dismiss":
        print(f"dismissed {data['id']}: {data['rule']}")
    else:
        print(format_scan(data))
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0
