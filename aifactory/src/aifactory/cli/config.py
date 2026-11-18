"""``giro config``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aifactory.cli.backlog import _backlog_root
from aifactory.cli.envelope import emit_fail, emit_ok, fail_from_exception
from aifactory.cli.library import _library_fail
from aifactory.skill.envelope import strip_payload

if TYPE_CHECKING:
    from aifactory.config.commit import ConfigCommitError


def _add_config_commands(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(config_parser=parser)
    sub = parser.add_subparsers(dest="config_command", metavar="<subcommand>")
    local = sub.add_parser("local", help="repository operating settings in GIRO_HOME")
    local.set_defaults(local_parser=local)
    local_commands = local.add_subparsers(dest="local_command", metavar="<subcommand>")
    local_show = local_commands.add_parser("show", help="read local repository settings")
    local_set = local_commands.add_parser("set", help="update supplied local fields only")
    local_set.add_argument("--auto-merge", choices=("on", "off"))
    local_set.add_argument("--auto-continue", choices=("on", "off"))
    local_set.add_argument("--harness", choices=("claude", "codex", "pi", "inherit"))
    legacy = local_commands.add_parser(
        "legacy", help="preview legacy task/step/project values; writes nothing"
    )
    take = local_commands.add_parser(
        "import", help="explicitly import legacy values into GIRO_HOME"
    )
    take.add_argument(
        "--overwrite", action="store_true", help="explicitly replace existing local settings"
    )
    take.add_argument("--auto-merge", choices=("on", "off"))
    take.add_argument("--auto-continue", choices=("on", "off"))
    take.add_argument("--harness", choices=("claude", "codex", "pi", "inherit"))
    for child in (local_show, local_set, legacy, take):
        child.add_argument("--repo", metavar="PATH")
        child.add_argument("--json", action="store_true", help="print the JSON envelope")
    roster = sub.add_parser("roster", help="show or edit local agent harness/model/thinking")
    roster.set_defaults(roster_parser=roster)
    commands = roster.add_subparsers(dest="roster_command", metavar="<subcommand>")
    roster_show = commands.add_parser("show", help="show effective local agent settings")
    roster_set = commands.add_parser(
        "set",
        help="change local roster defaults or one named agent",
        description="Switch presets: codex (gpt-6.1-sol, medium) or claude "
        "(claude-opus-5-5, medium). Explicit flags override the preset. A whole-roster "
        "preset clears per-agent harness/model/thinking overrides. Other settings stay. "
        "Changes remain local until factory config commit; workflow step overrides win.",
    )
    roster_set.add_argument("preset", nargs="?", choices=("codex", "claude"))
    roster_set.add_argument("--agent", metavar="NAME", help="change only this agent")
    roster_set.add_argument("--harness", metavar="NAME", help="explicit harness override")
    roster_set.add_argument("--model", metavar="ID", help="explicit model override")
    roster_set.add_argument("--thinking", metavar="LEVEL", help="explicit thinking override")
    roster_set.add_argument("--dry-run", action="store_true", help="validate and show diff only")
    for child in (roster_show, roster_set):
        child.add_argument("--repo", metavar="PATH", help="repository (default: current git repo)")
        child.add_argument("--json", action="store_true", help="print machine-readable JSON")
    status = sub.add_parser(
        "status",
        help="list uncommitted .factory/ changes that runs will not see",
        description="List uncommitted changes in .factory/ against the base commit. "
        "Runs read the committed configuration, so these changes are ignored by them.",
    )
    show = sub.add_parser(
        "show",
        help="show the configuration a run would use",
        description="Load the configuration from the base commit, as a run does, and show it.",
    )
    for child in (status, show):
        child.add_argument("--json", action="store_true", help="print machine-readable JSON")
        child.add_argument(
            "--base", metavar="REF", help="base ref (default: `base` in .factory/config.yaml)"
        )
    commit = sub.add_parser(
        "commit",
        help="commit uncommitted shared .factory/ config to base (push first, then move base)",
        description="Commit the uncommitted shared configuration (the paths of config status: "
        "config.yaml, agents.yaml, roles.yaml, prompts/, workflows/, "
        "extensions/ and the repo skills in .claude/skills/ and .agents/skills/; never "
        "local.yaml) to base "
        "in one commit, built on base without a checkout; other staged and unstaged work stays. "
        "--dry-run returns the plan: files {path, action, diff, content}, blockers and digest. "
        "--expect DIGEST refuses a plan that changed since (plan_changed). -m/--message TEXT "
        "sets the commit message. The direct target needs the main checkout on base "
        "(not_on_base) and no run in progress (run_in_progress); with a remote, base is fetched "
        "first (base_behind, base_diverged), the commit is pushed without force (push_failed "
        "changes nothing) and only then the local base is fast-forwarded. --pr pushes the "
        "commit to a branch factory-config/<n> and opens a pull request; base and the checkout "
        "stay. Without changes: committed false.",
    )
    commit.add_argument("--dry-run", action="store_true", help="only print the plan")
    commit.add_argument(
        "--pr", action="store_true", help="open a pull request instead of committing to base"
    )
    commit.add_argument(
        "--expect", metavar="DIGEST", help="commit only if the plan still has this digest"
    )
    commit.add_argument("-m", "--message", metavar="TEXT", help="commit message")
    pull = sub.add_parser(
        "pull",
        help="fast-forward local base to the remote base, also during active runs",
        description="Fetch the remote base and fast-forward the local base, also during active "
        "runs. The move is journaled for their guards. A diverged base (base_diverged), "
        "uncommitted changes in the checkout on base (dirty_base) or no remote (no_remote) "
        "are refused and nothing changes.",
    )
    _add_config_edit_commands(sub)
    for child in (commit, pull):
        child.add_argument("--json", action="store_true", help="print machine-readable JSON")
        child.add_argument(
            "--repo",
            metavar="PATH",
            help="repository root with .factory/config.yaml (default: the current git repo)",
        )


_CONFIG_MODES = (
    "Modes: --dry-run returns the plan (files {path, action, diff, content}, removed, "
    "bindings, blockers, validation, digest) and writes nothing; without an option "
    "the plan is written to the working tree (nothing committed; factory config commit "
    "commits it later; --expect DIGEST of a --dry-run refuses a changed plan with "
    "plan_changed; a run in progress blocks with run_in_progress); --commit [--pr] [--expect "
    "DIGEST] [-m TEXT] commits the plan of the tree of base in one commit (pushed before base "
    "moves; --pr opens a pull request from factory-config/<n>), --commit --dry-run shows that "
    "plan. The planned configuration must load and every workflow of the repo must pass its "
    "preflight (invalid_plan). Blockers: run_in_progress, invalid_plan (working tree); "
    "dirty_paths, invalid_plan, not_on_base, run_in_progress, base_behind, base_diverged "
    "(--commit); dirty_paths, invalid_plan (--pr). Nothing to change: changed false, exit 0."
)


def _add_config_edit_commands(sub: Any) -> None:
    """``factory config set``, ``remove`` and ``move``."""
    set_ = sub.add_parser(
        "set",
        help="change the bindings of one agent in .factory/agents.yaml",
        description="Change only the bindings of an agent (harness, model, thinking, "
        "tools, writes, color) by a round trip that keeps comments and key order; the "
        "prompts stay. An agent the repo inherits from the library gets an entry that "
        "overrides only these keys. --tools and --writes take a comma list ('' = empty "
        "list, '-' removes the key; writes expand $specs_dir/ and $docs_dir/); --color "
        "#rrggbb ('-' removes it). An unknown thinking level or harness is invalid_value, an "
        "agent neither in the repo nor in the library unknown_item. " + _CONFIG_MODES,
    )
    set_.add_argument("type", choices=("agent",))
    set_.add_argument("name", metavar="agent", help="agent name")
    set_.add_argument("--harness", metavar="NAME", help="harness (claude, codex, pi)")
    set_.add_argument("--model", metavar="ID", help="model id")
    set_.add_argument("--thinking", metavar="LEVEL", help="thinking level")
    set_.add_argument("--tools", metavar="LIST", help="comma list of tools")
    set_.add_argument("--writes", metavar="LIST", help="comma list of writable paths")
    set_.add_argument("--color", metavar="HEX", help="dashboard color #rrggbb")
    remove = sub.add_parser(
        "remove",
        help="remove the repo's own copy of an item (the library's then applies)",
        description="Remove the repo copy of an agent (roster entry, prompts), workflow, "
        "skill (both .claude/skills/ and .agents/skills/) or extension; the library item of "
        "the same name then applies. Without one, refused with in_use (data.used_by) while "
        "a workflow of the repo or roles.yaml names the agent, a backlog task in base names "
        "the workflow, or a roster agent binds the skill or extension. " + _CONFIG_MODES,
    )
    move = sub.add_parser(
        "move",
        help="move the repo's own copy of an item into the library",
        description="Write the repo copy of an agent (roster purpose and prompts), workflow, "
        "skill or extension into the library under its name (create or replace), then "
        "delete it from the repo; the repo inherits it from the library from then on. A "
        "moved agent keeps the agent.yaml defaults of its library item with the repo purpose; "
        "a new one takes its defaults from the roster entry. Its roster entry keeps only "
        "the bindings (an override), an entry with nothing else is removed. Order: the "
        "library is written first (library.lock, fetch, library_behind, library_diverged, "
        "library_dirty, push without force: a rejected push is push_failed and the repo is "
        "not touched), then the repo. Without a library: library_missing. data.library_plan "
        "has the library files, data.library_commit the library commit. The digest covers "
        "the library and the repo plan. " + _CONFIG_MODES,
    )
    for child in (remove, move):
        child.add_argument("type", choices=("agent", "workflow", "skill", "extension"))
        child.add_argument("name", help="item name in the repo")
    for child in (set_, remove, move):
        child.add_argument("--dry-run", action="store_true", help="only print the plan")
        child.add_argument(
            "--commit", action="store_true", help="commit to base in one commit (default: write)"
        )
        child.add_argument(
            "--pr", action="store_true", help="with --commit: open a pull request instead"
        )
        child.add_argument(
            "--expect", metavar="DIGEST", help="commit only if the plan still has this digest"
        )
        child.add_argument("-m", "--message", metavar="TEXT", help="commit message (--commit)")
        child.add_argument("--json", action="store_true", help="print machine-readable JSON")
        child.add_argument(
            "--repo", metavar="PATH", help="repository (default: the current git repo)"
        )


def _config_edit_conflict(args: argparse.Namespace) -> str | None:
    if args.pr and not args.commit:
        return "--pr needs --commit"
    if args.message is not None and not args.commit:
        return "-m needs --commit"
    if args.expect is not None and args.dry_run:
        return "--expect is not for --dry-run"
    return None


def _config_edit(args: argparse.Namespace) -> int:
    """``factory config set|remove|move``."""
    from aifactory.library.config_edit import run_config
    from aifactory.library.store import LibraryStoreError
    from aifactory.providers.base import ProviderError

    command = args.config_command
    label = f"factory config {command}"
    conflict = _config_edit_conflict(args)
    if conflict is not None:
        return _library_fail(LibraryStoreError("conflicting_options", conflict), args.json, label)
    options: dict[str, Any] = {}
    if command == "set":
        keys = ("harness", "model", "thinking", "tools", "writes", "color")
        options = {k: getattr(args, k) for k in keys}
    try:
        result = run_config(
            command,
            Path(args.repo) if args.repo else Path.cwd(),
            type=args.type,
            name=args.name,
            dry_run=args.dry_run,
            commit=args.commit,
            pr=args.pr,
            expect=args.expect,
            message=args.message,
            **options,
        )
    except (LibraryStoreError, ProviderError) as exc:
        return _library_fail(exc, args.json, label)
    if args.json:
        return emit_ok(result.to_json(), result.warnings)
    plan = result.plan
    publish = plan.publish
    if plan.library_plan is not None:
        for f in plan.library_plan.files:
            print(f"{f.action:<7} library:{f.path}")
    for f in publish.files:
        print(f"{f.action:<7} {f.path}")
    for row in plan.change.removed:
        print(f"removed {row['type']} {row['name']} ({row['reason']})")
    if result.dry_run:
        for b in publish.blockers:
            print(f"blocked: {b.code}: {b.message}")
        print(f"digest  {publish.digest}")
        if plan.changed and not publish.blockers:
            argv = [a for a in sys.argv[1:] if a not in ("--dry-run", "--json")]
            print(f"next: factory {' '.join(argv)} --expect {publish.digest}")
    elif not plan.changed:
        print("nothing to change")
    elif result.written:
        print("next: factory config commit")
    elif result.pr is not None:
        print(f"opened {result.pr.url} from {result.branch}")
    else:
        pushed = "pushed" if result.pushed else "not pushed"
        moved = "base advanced" if result.advanced else "base not advanced"
        short = (result.commit or "")[:12]
        print(
            f"committed {len(publish.files)} file(s) to {publish.base} ({short}, {pushed}, {moved})"
        )
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _config_status(args: argparse.Namespace) -> int:
    from aifactory.config import ConfigError, repo_root
    from aifactory.config.status import config_status

    try:
        root = repo_root(Path.cwd())
        status = config_status(root, args.base.strip() if args.base else None)
    except ConfigError as exc:
        return fail_from_exception(exc, args.json, "factory config status")
    if args.json:
        return emit_ok(status.to_json(), status.warnings)
    base, sha = status.base, status.commit
    if not status.changes:
        print(f"config in sync with {base} ({sha[:7]})")
    else:
        for change in status.changes:
            print(f"{change.status:<9} {change.path}")
        print(f"runs use {base} ({sha[:7]}); commit these changes to include them")
    return 0


def _config_show(args: argparse.Namespace) -> int:
    from aifactory.config import ConfigError, load_run_config

    try:
        run = load_run_config(Path.cwd(), args.base)
    except ConfigError as exc:
        return fail_from_exception(exc, args.json, "factory config show")
    if args.json:
        data, warnings = strip_payload(run.to_json())
        return emit_ok(data, warnings)
    data = run.to_json()
    print(f"base      {run.base} ({run.commit[:7]})")
    print(f"agents    {', '.join(data['agents']) or '-'}")
    print(f"workflows {', '.join(data['workflows']) or '-'}")
    print(f"prompts   {', '.join(data['prompts']) or '-'}")
    print(f"trace db  {run.local.trace_db}")
    for warning in run.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _config(args: argparse.Namespace) -> int:
    if args.config_command is None:
        args.config_parser.print_help()
        return 0
    if args.config_command == "status":
        return _config_status(args)
    if args.config_command == "commit":
        return _config_commit(args)
    if args.config_command == "pull":
        return _config_pull(args)
    if args.config_command in ("set", "remove", "move"):
        return _config_edit(args)
    if args.config_command == "roster":
        return _config_roster(args)
    if args.config_command == "local":
        return _config_local(args)
    return _config_show(args)


def _config_local(args: argparse.Namespace) -> int:
    from aifactory.repo_settings import RepositorySettings, SettingsError

    if args.local_command is None:
        args.local_parser.print_help()
        return 0
    try:
        service = RepositorySettings(Path(args.repo) if args.repo else Path.cwd())
        if args.local_command == "legacy":
            return emit_ok(service.legacy_preview())
        if args.local_command in ("set", "import"):
            changes: dict[str, object] = {}
            for name in ("auto_merge", "auto_continue"):
                supplied = getattr(args, name)
                if supplied is not None:
                    changes[name] = supplied == "on"
            if args.harness is not None:
                changes["harness"] = None if args.harness == "inherit" else args.harness
            if args.local_command == "import":
                return emit_ok(service.import_legacy(changes, overwrite=args.overwrite))
            return emit_ok(service.update_and_show(changes))
        return emit_ok(service.show())
    except SettingsError as exc:
        return emit_fail(exc.code, str(exc), path=str(exc.path), exit_code=2)


def _config_roster(args: argparse.Namespace) -> int:
    from aifactory.config import ConfigError
    from aifactory.config.roster import roster

    if args.roster_command is None:
        args.roster_parser.print_help()
        return 0
    options = {}
    if args.roster_command == "set":
        options = {
            key: getattr(args, key)
            for key in ("preset", "agent", "harness", "model", "thinking", "dry_run")
        }
        options["change"] = True
    try:
        data = roster(Path(args.repo) if args.repo else Path.cwd(), **options)
    except ConfigError as exc:
        return fail_from_exception(exc, args.json, "factory config roster")
    warnings = []
    if data.get("changed"):
        warnings.append("local changes: runs use committed base until factory config commit")
    if data.get("comments_preserved") is False:
        warnings.append("complex YAML was serialized; review formatting and comments in the diff")
    if args.json:
        return emit_ok(data, warnings)
    for agent in data["agents"]:
        print(f"{agent['name']:<20} {agent['harness']:<8} {agent['model']:<28} {agent['thinking']}")
    if "diff" in data:
        print(data["diff"] or "no changes", end="\n")
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _config_commit_fail(exc: ConfigCommitError, as_json: bool, label: str) -> int:
    if as_json:
        return emit_fail(exc.code, exc.message, exit_code=2, data=exc.data, issues=exc.issues)
    print(f"{label}: {exc.code}: {exc.message}", file=sys.stderr)
    for issue in exc.issues:
        print(f"  {issue.get('path')}: {issue.get('message')}", file=sys.stderr)
    return 2


def _config_commit(args: argparse.Namespace) -> int:
    from aifactory.config import ConfigError
    from aifactory.config.commit import ConfigCommitError, commit_config

    label = "factory config commit"
    try:
        result = commit_config(
            _backlog_root(args.repo),
            pr=args.pr,
            dry_run=args.dry_run,
            expect=args.expect,
            message=args.message,
        )
    except ConfigCommitError as exc:
        return _config_commit_fail(exc, args.json, label)
    except ConfigError as exc:
        return fail_from_exception(exc, args.json, label)
    if args.json:
        return emit_ok(result.to_json(), result.warnings)
    plan = result.plan
    if result.dry_run:
        for f in plan.files:
            print(f"{f.action:<7} {f.path}")
        for b in plan.blockers:
            print(f"blocked: {b.code}: {b.message}")
        print(f"digest  {plan.digest}")
    elif not result.committed:
        print("nothing to commit in the configuration")
    elif result.pr is not None:
        print(f"opened {result.pr.url} from {result.branch}")
    else:
        pushed = "pushed" if result.pushed else "not pushed"
        moved = "base advanced" if result.advanced else "base not advanced"
        short = (result.commit or "")[:12]
        print(f"committed {len(plan.files)} file(s) to {plan.base} ({short}, {pushed}, {moved})")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _config_pull(args: argparse.Namespace) -> int:
    from aifactory.config import ConfigError
    from aifactory.config.commit import ConfigCommitError, pull_config

    label = "factory config pull"
    try:
        result = pull_config(_backlog_root(args.repo))
    except ConfigCommitError as exc:
        return _config_commit_fail(exc, args.json, label)
    except ConfigError as exc:
        return fail_from_exception(exc, args.json, label)
    if args.json:
        return emit_ok(result.to_json(), result.warnings)
    if result.updated:
        print(f"{result.base}: {result.before[:7]} -> {result.after[:7]}")
    else:
        print(f"{result.base} is up to date with {result.remote}/{result.base}")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0
