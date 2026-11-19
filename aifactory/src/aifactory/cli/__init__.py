"""The `factory` command line.

Implemented: `check`, `harness check`, `config status`, `config show`,
`config commit`, `config pull`, `config items`, `backlog check`,
`backlog list`, `backlog sync`, `backlog auto-continue`, `backlog auto-merge`, `backlog add`,
`backlog edit`, `task add`,
`task edit`, `task link`, `task show`, `task list`, `task run`, `task approve`, `task return`,
`task resolve`, `task publish`, `task clean`, `init`, `try`, `workflow check`,
`skills sync`, `library init`, `library list`, `library show`, `library import`,
`library clone`, `library status`, `library pull`, `library push`, `library seed`, `upgrade`,
`obs` (the local dashboard of every registered repository, ``aifactory.web``), and
`factory --skill` (the agent skill generated from this code, see ``aifactory.skill``).

With `--json` every command prints one envelope ``{ok, data, error, warnings}`` with a stable
``error.code`` (``aifactory.skill.envelope``, codes in ``aifactory.skill.codes``).
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path

from aifactory import oscompat
from aifactory.cli.backlog import _backlog
from aifactory.cli.check import _check
from aifactory.cli.config import _config
from aifactory.cli.envelope import emit_fail, emit_ok
from aifactory.cli.harness import _harness, _skills
from aifactory.cli.init import _init, _try
from aifactory.cli.learn import _learn
from aifactory.cli.library import _library
from aifactory.cli.obs import _obs
from aifactory.cli.parser import SUBCOMMANDS, _UsageError, build_parser
from aifactory.cli.task import _task
from aifactory.cli.upgrade import _upgrade
from aifactory.cli.workflow import _workflow
from aifactory.home import watch_env_file

__all__ = ["COMMANDS", "SUBCOMMANDS", "build_parser", "main"]

COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "check": _check,
    "init": _init,
    "try": _try,
    "harness": _harness,
    "skills": _skills,
    "library": _library,
    "config": _config,
    "backlog": _backlog,
    "task": _task,
    "workflow": _workflow,
    "learn": _learn,
    "upgrade": _upgrade,
    "obs": _obs,
}
"""The handler of each command in ``SUBCOMMANDS``."""


def _leave_deleted_cwd() -> None:
    """Continue from the home folder when the working directory no longer exists.

    A terminal left in an unpacked bundle that was deleted after the install makes every
    child process fail (git: "Unable to read current working directory", claude), so the
    machine check would report a missing git identity and harnesses that are fine.
    """
    try:
        os.getcwd()
    except FileNotFoundError:
        home = Path.home()
        os.chdir(home)
        print(
            f"factory: pracovní složka neexistuje, pokračuji z {home}",
            file=sys.stderr,
        )


def main(argv: Sequence[str] | None = None) -> int:
    from aifactory.home import giro_home
    from aifactory.versioning import UpgradeError

    if list(sys.argv[1:] if argv is None else argv) == ["--data-home"]:
        print(giro_home())
        return 0
    try:
        return _main(argv)
    except UpgradeError as exc:
        if "--json" in (sys.argv[1:] if argv is None else argv):
            return emit_fail(exc.code, exc.message, exit_code=exc.exit_code)
        print(f"factory: {exc.code}: {exc.message}", file=sys.stderr)
        return exc.exit_code


def _main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        oscompat.utf8_stdio()
    _leave_deleted_cwd()
    argv_list = list(sys.argv[1:] if argv is None else argv)
    if argv_list == ["--version"] or "--help" in argv_list:
        return _parse_and_dispatch(argv_list)
    with watch_env_file():
        from aifactory.environment import warn_legacy

        warn_legacy(os.environ)
        return _parse_and_dispatch(argv_list)


def _parse_and_dispatch(argv_list: list[str]) -> int:
    want_json = "--json" in argv_list
    parser = build_parser()
    try:
        args = parser.parse_args(argv_list)
    except _UsageError as exc:
        if want_json:
            return emit_fail("usage_error", f"{exc.parser.prog}: {exc.message}", exit_code=2)
        exc.parser.print_usage(sys.stderr)
        print(f"{exc.parser.prog}: error: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from None
    if args.skill:
        from aifactory.skill import render_skill, skill_json

        if args.root_json:
            return emit_ok(skill_json(parser))
        print(render_skill(parser), end="")
        return 0
    if args.root_json:
        return emit_fail(
            "usage_error", "factory: --json before the command needs --skill", exit_code=2
        )
    if not getattr(args, "json", False):
        return _dispatch(parser, args)
    try:
        return _dispatch(parser, args)
    except Exception as exc:  # --json promises an envelope even for a bug
        traceback.print_exc(file=sys.stderr)
        return emit_fail("internal_error", f"{type(exc).__name__}: {exc}", exit_code=2)


def _dispatch(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    command: str | None = args.command
    if command is None:
        parser.print_help()
        print()
        print("První spuštění: giro check")
        return 0
    handler = COMMANDS.get(command)
    if handler is not None:
        return handler(args)
    print(f"factory {command}: not implemented yet", file=sys.stderr)
    return 2
