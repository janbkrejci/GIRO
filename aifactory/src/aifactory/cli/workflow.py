"""``giro workflow``."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from aifactory.cli.envelope import emit_fail, emit_ok, fail_from_exception
from aifactory.skill.envelope import strip_payload

if TYPE_CHECKING:
    from aifactory.engine.role_registry import RoleRegistry
    from aifactory.harness.config import SSSFConfig


def _add_workflow_commands(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(workflow_parser=parser)
    sub = parser.add_subparsers(dest="workflow_command", metavar="<subcommand>")
    check = sub.add_parser(
        "check",
        help="validate a workflow against the role registry and the agents",
        description="Validate a workflow file against the role registry (.factory/roles.yaml "
        "or the packaged defaults) and the agent roster (.factory/agents.yaml) of the working "
        "tree. Nothing runs. Exits 1 when the workflow has problems, 2 when the registry or "
        "roster cannot be loaded.",
    )
    check.add_argument("file", type=Path, metavar="FILE", help="workflow YAML file")
    check.add_argument("--json", action="store_true", help="print machine-readable JSON")
    check.add_argument(
        "--roles", type=Path, metavar="PATH", help="role registry instead of .factory/roles.yaml"
    )
    check.add_argument(
        "--agents", type=Path, metavar="PATH", help="agent roster instead of .factory/agents.yaml"
    )


@dataclass
class _WorkflowSources:
    roles: RoleRegistry
    agents: SSSFConfig | None
    roles_source: str
    agents_source: str | None


def _workflow_sources(args: argparse.Namespace) -> _WorkflowSources:
    """The registry and roster to check against: flags first, then the working tree."""
    from aifactory.config import ConfigError, load_worktree_config, repo_root
    from aifactory.config.loader import AGENTS_FILE, ROLES_FILE
    from aifactory.engine.role_registry import load_roles
    from aifactory.harness.config import load_config

    root: Path | None
    try:
        root = repo_root(Path.cwd())
    except ConfigError:
        root = None
    if (
        root is not None
        and (root / ".factory" / "config.yaml").is_file()
        and args.roles is None
        and args.agents is None
    ):
        cfg = load_worktree_config(root)
        has_roles = (root / ROLES_FILE).is_file()
        return _WorkflowSources(
            roles=cfg.roles,
            agents=cfg.agents,
            roles_source=ROLES_FILE if has_roles else "packaged defaults",
            agents_source=AGENTS_FILE,
        )
    roles = load_roles(args.roles) if args.roles is not None else load_roles()
    agents = load_config(args.agents) if args.agents is not None else None
    return _WorkflowSources(
        roles=roles,
        agents=agents,
        roles_source=str(args.roles) if args.roles is not None else "packaged defaults",
        agents_source=str(args.agents) if args.agents is not None else None,
    )


def _workflow_check(args: argparse.Namespace) -> int:
    from aifactory.config import ConfigError
    from aifactory.engine.role_registry import RolesError
    from aifactory.harness.config import HarnessConfigError
    from aifactory.workflow import check_workflow

    try:
        sources = _workflow_sources(args)
    except (ConfigError, RolesError, HarnessConfigError, OSError) as exc:
        return fail_from_exception(exc, args.json, "factory workflow check")
    result = check_workflow(
        args.file,
        sources.roles,
        sources.agents,
        roles_source=sources.roles_source,
        agents_source=sources.agents_source,
    )
    if args.json:
        data, warnings = strip_payload(result.to_json())
        data["issues"] = data.pop("errors", [])
        if result.ok:
            return emit_ok(data, warnings)
        return emit_fail(
            "workflow_invalid",
            f"{len(result.issues)} problem(s) in {args.file}",
            exit_code=1,
            data=data,
            path=str(args.file),
            issues=data["issues"],
            warnings=warnings,
        )
    if result.ok:
        print(f"OK: {result.workflow} ({len(result.outline)} steps)")
    else:
        for issue in result.issues:
            print(f"{args.file}:{issue.path}: {issue.code}: {issue.message}")
        print(f"{len(result.issues)} error(s)")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0 if result.ok else 1


def _workflow(args: argparse.Namespace) -> int:
    if args.workflow_command is None:
        args.workflow_parser.print_help()
        return 0
    return _workflow_check(args)
