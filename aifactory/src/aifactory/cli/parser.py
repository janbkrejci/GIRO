"""The argument parser of the ``giro`` command and its top-level commands."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import NoReturn

from aifactory import __version__
from aifactory.cli.backlog import _add_backlog_commands
from aifactory.cli.check import _add_check_command
from aifactory.cli.config import _add_config_commands
from aifactory.cli.harness import _add_harness_commands, _add_skills_commands
from aifactory.cli.init import _add_init_command, _add_try_command
from aifactory.cli.learn import _add_learn_commands
from aifactory.cli.library import _add_library_commands
from aifactory.cli.obs import _add_obs_command
from aifactory.cli.task import _add_task_commands
from aifactory.cli.upgrade import _add_upgrade_command
from aifactory.cli.workflow import _add_workflow_commands

SUBCOMMANDS: tuple[tuple[str, str], ...] = (
    ("check", "check that factory will run in this repo and on this machine"),
    ("init", "install factory into this repository (agents and workflows come from the library)"),
    (
        "try",
        "create the sample repository with a backlog of three tasks and register it",
    ),
    ("task", "run and inspect backlog tasks"),
    ("backlog", "load and validate the backlog"),
    ("workflow", "validate and inspect workflows"),
    ("config", "show and validate factory configuration"),
    ("harness", "inspect the coding-agent harnesses"),
    ("skills", "keep the repo's skills mirror for codex and pi in sync"),
    (
        "library",
        "the GIRO library in the home directory: init, list, show, import, clone, status, "
        "pull, push, seed",
    ),
    (
        "learn",
        "collect objections that keep coming back in task runs into lessons: scan, accept, dismiss",
    ),
    ("upgrade", "install a newer GIRO from a giro-<version>.zip bundle"),
    ("obs", "serve the local dashboard (127.0.0.1 unless --host) and open it"),
)


class _UsageError(Exception):
    """argparse rejected the arguments; raised instead of exiting so --json can report it."""

    def __init__(self, parser: argparse.ArgumentParser, message: str) -> None:
        super().__init__(message)
        self.parser = parser
        self.message = message


class _Parser(argparse.ArgumentParser):
    """An ArgumentParser whose usage errors raise ``_UsageError`` (subparsers inherit it)."""

    def error(self, message: str) -> NoReturn:
        raise _UsageError(self, message)


_ADD_ARGUMENTS: dict[str, Callable[[argparse.ArgumentParser], None]] = {
    "check": _add_check_command,
    "init": _add_init_command,
    "try": _add_try_command,
    "harness": _add_harness_commands,
    "skills": _add_skills_commands,
    "library": _add_library_commands,
    "config": _add_config_commands,
    "backlog": _add_backlog_commands,
    "task": _add_task_commands,
    "workflow": _add_workflow_commands,
    "learn": _add_learn_commands,
    "upgrade": _add_upgrade_command,
    "obs": _add_obs_command,
}


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="giro",
        description=(
            "GIRO — Goal in. Release out. Run backlog tasks through coding-agent workflows."
        ),
    )
    parser.add_argument("--version", action="version", version=f"GIRO {__version__}")
    parser.add_argument(
        "--skill",
        action="store_true",
        help="print the agent skill for this version and exit (with --json: as an envelope)",
    )
    parser.add_argument(
        "--json",
        dest="root_json",
        action="store_true",
        help="with --skill: print the skill as a JSON envelope",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for name, help_text in SUBCOMMANDS:
        child = sub.add_parser(name, help=help_text, description=help_text)
        _ADD_ARGUMENTS[name](child)
    return parser
