"""``giro harness`` and ``giro skills``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aifactory.cli.envelope import emit_fail, emit_ok, fail_from_exception


def _add_harness_commands(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(harness_parser=parser)
    sub = parser.add_subparsers(dest="harness_command", metavar="<subcommand>")
    check = sub.add_parser(
        "check",
        help="verify each harness CLI is on PATH and print its version",
        description="Verify each harness CLI used by the config is on PATH and print versions.",
    )
    check.add_argument("--json", action="store_true", help="print machine-readable JSON")
    check.add_argument(
        "--config",
        metavar="PATH",
        help="GIRO roster (.factory/agents.yaml); only the harnesses it uses are checked",
    )


def _add_skills_commands(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(skills_parser=parser)
    sub = parser.add_subparsers(dest="skills_command", metavar="<subcommand>")
    sync = sub.add_parser(
        "sync",
        help="write .agents/skills as an exact copy of .claude/skills",
        description=(
            "Write .agents/skills/ as an exact copy of .claude/skills/ (new, changed and "
            "deleted skills), so codex and pi read the same skills as claude. Edit skills "
            "in .claude/skills/ only. Writes the working tree only; commit the result. "
            "No symlinks. Exits 0 on success, 2 when the folder is not a git repository "
            "or the mirror cannot be written (skills_sync_failed)."
        ),
    )
    sync.add_argument(
        "--repo", metavar="PATH", help="repository to sync (default: the current git repo)"
    )
    sync.add_argument("--json", action="store_true", help="print machine-readable JSON")


def _skills(args: argparse.Namespace) -> int:
    if args.skills_command is None:
        args.skills_parser.print_help()
        return 0
    from aifactory.config import ConfigError, repo_root
    from aifactory.harness import repo_skills

    label = "factory skills sync"
    try:
        root = repo_root(Path(args.repo) if args.repo else Path.cwd())
    except ConfigError as exc:
        return fail_from_exception(exc, args.json, label)
    try:
        result = repo_skills.sync(root)
    except OSError as exc:
        if args.json:
            return emit_fail("skills_sync_failed", str(exc), exit_code=2)
        print(f"{label}: {exc}", file=sys.stderr)
        return 2
    if args.json:
        return emit_ok(
            {
                "repo": str(root),
                "source": repo_skills.SOURCE,
                "mirror": repo_skills.MIRROR,
                "added": list(result.added),
                "updated": list(result.updated),
                "removed": list(result.removed),
            }
        )
    for verb, names in (
        ("added", result.added),
        ("updated", result.updated),
        ("removed", result.removed),
    ):
        for name in names:
            print(f"{verb} {name}")
    if result.added or result.updated or result.removed:
        print(
            f"{repo_skills.MIRROR}: {len(result.added)} added, {len(result.updated)} updated, "
            f"{len(result.removed)} removed; commit {repo_skills.MIRROR}"
        )
    else:
        print(f"{repo_skills.MIRROR} is up to date")
    return 0


def _harness(args: argparse.Namespace) -> int:
    if args.harness_command is None:
        args.harness_parser.print_help()
        return 0
    from aifactory.config import ConfigError
    from aifactory.config.loader import load_roster_file
    from aifactory.harness import check

    cfg = None
    if args.config:
        try:
            cfg = load_roster_file(args.config)
        except (OSError, ConfigError) as exc:
            return fail_from_exception(exc, args.json, "factory harness check")
    statuses = check.check_all(cfg)
    ok = all(status.ok for status in statuses)
    if args.json:
        data = {"harnesses": [s.to_json() for s in statuses]}
        if ok:
            return emit_ok(data)
        missing = ", ".join(s.name for s in statuses if not s.ok)
        return emit_fail("harness_missing", f"missing: {missing}", exit_code=1, data=data)
    else:
        for status in statuses:
            where = status.path or "not on PATH"
            detail = status.version if status.ok else status.error
            line = f"{status.name:<7} {where}  {detail}"
            if status.agents:
                line += f"  (agents: {', '.join(status.agents)})"
            print(line)
    return 0 if ok else 1
