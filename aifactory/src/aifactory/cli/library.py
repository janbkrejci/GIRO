"""``giro library``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from aifactory.cli.envelope import emit_fail, emit_ok

ITEM_TYPES: tuple[str, ...] = ("agent", "workflow", "skill", "extension")  # library.model


def _add_library_commands(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(library_parser=parser)
    sub = parser.add_subparsers(dest="library_command", metavar="<subcommand>")
    init = sub.add_parser(
        "init",
        help="create the library from the seed in one commit",
        description=(
            "Create the library ($GIRO_HOME/library, overridden by GIRO_LIBRARY) as a git "
            "repository from the packaged seed in one commit: library.yaml (format, id, name, "
            "min_factory_version, seed) and the seed agents, workflows and the factory skill. "
            "An existing library is refused (library_exists); git needs user.name and user.email "
            "(git_identity_missing). With --remote URL the commit is pushed to the empty remote "
            "before the library exists locally; a remote with any ref is refused "
            "(remote_not_empty) and a failed push (push_failed) leaves no library. The remote "
            "is stored as origin without user and password."
        ),
    )
    init.add_argument("--name", metavar="N", help="name of the library (default: library)")
    init.add_argument("--remote", metavar="URL", help="share the library through this empty remote")
    list_ = sub.add_parser(
        "list",
        help="list the items of the library with their versions",
        description=(
            "List the items in HEAD of the library: type, name, version, short_version "
            "(8 characters), and commit, date and author of the last change."
        ),
    )
    list_.add_argument("--type", choices=ITEM_TYPES, help="list only items of this type")
    show = sub.add_parser(
        "show",
        help="show the files and version history of an item",
        description=(
            "Show an item in HEAD of the library (or at --version, a full version or a prefix "
            "of at least 4 hex digits): files {path, executable, size, content} and history "
            "{n, version, short_version, commit, date, author}, oldest first. unknown_item, "
            "unknown_version."
        ),
    )
    show.add_argument("type", metavar="TYP", choices=ITEM_TYPES, help="item type")
    show.add_argument("name", metavar="JMENO", help="item name")
    show.add_argument("--version", metavar="V", help="show this version of the item")
    import_ = sub.add_parser(
        "import",
        help="import a folder or file under the home directory into the library",
        description=(
            "Import a folder (agent, skill, extension) or file (workflow) from under the "
            "user's home directory (else outside_home, also for a symlink that leaves it) "
            "into the library. The item is validated (invalid_item with error.issues) and the "
            "plan {items, files with diffs, digest} is returned. Equal content is a no-op "
            "(committed false); other content under an existing name is a new version. "
            "Without --dry-run the write holds $GIRO_HOME/library.lock, refuses a library "
            "with uncommitted changes (library_dirty), commits on HEAD without a checkout "
            "and fast-forwards the branch and working tree. A library with a remote sends "
            "the change as a pull request (data.pull_request), never a push to its branch."
        ),
    )
    import_.add_argument("path", metavar="CESTA", help="folder or file to import")
    import_.add_argument("--type", required=True, choices=ITEM_TYPES, help="type of the item")
    import_.add_argument(
        "--name", metavar="N", help="item name (default: folder name or file stem)"
    )
    import_.add_argument("--dry-run", action="store_true", help="only print the plan")
    clone = sub.add_parser(
        "clone",
        help="clone a shared library from a git remote (default: the GIRO library)",
        description=(
            "Clone the library from URL into the library path and return its status. Without "
            "URL the default library is cloned: $GIRO_LIBRARY_URL, else "
            "https://github.com/janbkrejci/GIRO-LIBRARY.git. An "
            "existing library is refused (library_exists), a failed clone is clone_failed and "
            "a remote without a valid library.yaml (format, id, name) is invalid_library; "
            "nothing is left behind. The remote is stored as origin without user and password. "
            "With --seed-fallback a failed clone (no network, no access) creates a local "
            "library from the seed instead (data.fallback true, with a warning)."
        ),
    )
    clone.add_argument(
        "url",
        metavar="URL",
        nargs="?",
        help="git remote of the library (default: the GIRO library)",
    )
    clone.add_argument(
        "--seed-fallback",
        action="store_true",
        help="when the clone fails, create a local library from the seed",
    )
    clone.add_argument("--branch", metavar="B", help="branch to clone (default: the remote HEAD)")
    status = sub.add_parser(
        "status",
        help="show the library's remote, branch, ahead/behind and uncommitted changes",
        description=(
            "Show path, id, name, remote (without credentials), branch, ahead and behind "
            "against origin (from the last fetched refs; --fetch fetches first), last_fetch, "
            "uncommitted changes and min_factory_version against the installed factory "
            "(compatible). Without a remote, ahead and behind are null. sync_state is "
            "unshared (no remote), local (changes not sent), pending (every local change "
            "waits in a pull request), behind or synced; pull_requests lists the open pull "
            "requests sent from this library (--fetch asks GitHub which are still open)."
        ),
    )
    status.add_argument("--fetch", action="store_true", help="fetch the remote first")
    pull = sub.add_parser(
        "pull",
        help="bring the library up to its remote",
        description=(
            "Fetch origin and fast-forward the library. Local commits the remote lacks (sent "
            "in pull requests) are rebased onto it; the merged ones drop out. A library with "
            "uncommitted changes (library_dirty) or whose rebase conflicts (library_diverged) "
            "is refused and nothing changes; without a remote it is no_remote."
        ),
    )
    push = sub.add_parser(
        "push",
        help="send local library commits to its remote as a pull request",
        description=(
            "Fetch origin and send the local commits that are in no open pull request as one "
            "pull request against the library branch; the branch itself is never pushed. On "
            "GitHub the pull request comes from a branch in the library repo when you may "
            "write to it, else from your fork (gh repo fork); other remotes get only the "
            "branch giro-library/... GIRO never merges or approves it. A library behind the "
            "remote (library_behind: run factory library pull) or diverged from it "
            "(library_diverged) is refused; a rejected push is push_failed, a failed gh call "
            "pr_failed."
        ),
    )
    seed = sub.add_parser(
        "seed",
        help="take the new seed versions into the library after a GIRO upgrade",
        description=(
            "Compare every item of the installed seed with HEAD of the library and the seed "
            "version recorded in library.yaml (seed). Missing in the library: create. Library "
            "head equal to the recorded version: update to the new seed version. Library head "
            "changed by the team: kept, the plan shows the diff (items[].diff); --take "
            "TYP/JMENO takes the seed version anyway (take). Then seed in library.yaml is set "
            "to the installed seed. Items the seed no longer has are not touched. The write "
            "goes like import: library.lock, library_dirty, fetch (library_behind, "
            "library_diverged), commit, push, fast-forward. A second run without a new seed "
            "commits nothing (committed false). A --take item the seed lacks is not_in_seed. "
            "--all replaces every seed item with the installed seed version, also items the "
            "team changed (take); items the seed lacks and the repos' .factory/ stay. Install "
            "and upgrade run seed --all. --all with --take is usage_error."
        ),
    )
    seed_pick = seed.add_mutually_exclusive_group()
    seed_pick.add_argument(
        "--all",
        action="store_true",
        help="replace every seed item with the installed seed version, also items the team "
        "changed; install and upgrade run this",
    )
    seed_pick.add_argument(
        "--take",
        action="append",
        default=[],
        metavar="TYP/JMENO",
        help="take the seed version of this team-changed item (repeatable)",
    )
    seed.add_argument("--dry-run", action="store_true", help="only print the plan")
    for child in (init, list_, show, import_, clone, status, pull, push, seed):
        child.add_argument("--json", action="store_true", help="print machine-readable JSON")


def _library_fail(exc: Exception, as_json: bool, label: str) -> int:
    from aifactory.library.store import LibraryStoreError

    code = getattr(exc, "code", "internal_error")
    message = getattr(exc, "message", str(exc))
    issues = [i.to_dict() for i in exc.issues] if isinstance(exc, LibraryStoreError) else []
    data = exc.data if isinstance(exc, LibraryStoreError) else None
    if as_json:
        return emit_fail(code, message, exit_code=2, data=data, issues=issues)
    print(f"{label}: {code}: {message}", file=sys.stderr)
    for issue in issues:
        print(f"  {issue['path']}: {issue['code']}: {issue['message']}", file=sys.stderr)
    return 2


def _library(args: argparse.Namespace) -> int:
    if args.library_command is None:
        args.library_parser.print_help()
        return 0
    from aifactory.library import store
    from aifactory.providers.base import ProviderError

    label = f"factory library {args.library_command}"
    try:
        if args.library_command in ("clone", "status", "pull", "push"):
            return _library_remote(args)
        if args.library_command == "init":
            result = store.init_library(args.name, remote=args.remote)
            if args.json:
                return emit_ok(result.to_json(), result.warnings)
            print(f"created {result.plan.library} ({(result.commit or '')[:12]})")
            for item in result.plan.items:
                print(f"  {item.type}/{item.name} {item.version}")
            return 0
        if args.library_command == "list":
            data = store.list_items(args.type)
            if args.json:
                return emit_ok(data)
            for entry in data["items"]:
                version = entry["short_version"] or "invalid"
                print(f"{entry['type']:<9} {entry['name']:<24} {version}  {entry['date']}")
            return 0
        if args.library_command == "show":
            data = store.show_item(args.type, args.name, args.version)
            if args.json:
                return emit_ok(data)
            print(f"{data['type']}/{data['name']} {data['version']} ({data['commit'][:12]})")
            for f in data["files"]:
                print(f"  {f['path']}{' (x)' if f['executable'] else ''}")
            for rev in data["history"]:
                print(f"  v{rev['n']} {rev['short_version']} {rev['date']} {rev['author']}")
            return 0
        if args.library_command == "seed":
            return _library_seed(args)
        result = store.import_item(Path(args.path), args.type, args.name, dry_run=args.dry_run)
    except (store.LibraryStoreError, ProviderError) as exc:
        return _library_fail(exc, args.json, label)
    if args.json:
        return emit_ok(result.to_json(), result.warnings)
    for f in result.plan.files:
        print(f"{f.action:<7} {f.path}")
    item = result.plan.items[0]
    if result.dry_run:
        print(f"digest  {result.plan.digest}")
    elif result.committed:
        print(f"imported {item.type}/{item.name} {item.version} ({(result.commit or '')[:12]})")
        _print_pull_request(result.pull_request)
    else:
        print(f"{item.type}/{item.name} is unchanged")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _print_pull_request(pr: dict[str, Any] | None) -> None:
    if pr is not None:
        where = pr["url"] or f"branch {pr['branch']} (open the pull request by hand)"
        print(f"sent for review: {where}")


def _library_seed(args: argparse.Namespace) -> int:
    """`library seed`; errors propagate to `_library`."""
    from aifactory.library.reseed import seed_library

    result = seed_library(args.take, take_all=args.all, dry_run=args.dry_run)
    if args.json:
        return emit_ok(result.to_json(), result.warnings)
    for item in result.plan.items:
        if item.action != "unchanged":
            print(f"{item.action:<9} {item.type}/{item.name} {item.version}")
        for f in item.diff:
            print(f.diff(), end="")
    for f in result.plan.files:
        print(f"{f.action:<7} {f.path}")
    if result.dry_run:
        print(f"digest  {result.plan.digest}")
    elif result.committed:
        print(f"took the seed into the library ({(result.commit or '')[:12]})")
        _print_pull_request(result.pull_request)
    else:
        print("the library already has the seed")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _library_remote(args: argparse.Namespace) -> int:
    """`library clone|status|pull|push`; errors propagate to `_library`."""
    from aifactory.library import remote

    command = args.library_command
    if command == "clone" and args.seed_fallback:
        data = remote.clone_or_seed(args.url, args.branch)
    elif command == "clone":
        data = remote.clone_library(args.url, args.branch)
    elif command == "status":
        data = remote.library_status(args.fetch)
    elif command == "pull":
        data = remote.pull_library()
    else:
        data = remote.push_library()
    warnings: list[str] = [data["warning"]] if data.get("warning") else []
    if command in ("clone", "status") and data["compatible"] is False:
        warnings.append(
            f"the library needs factory {data['min_factory_version']}, "
            f"installed is {data['factory_version']}"
        )
    if command in ("clone", "status") and data["seed_update_available"]:
        warnings.append(
            f"seed_update_available: the installed seed has new versions of "
            f"{', '.join(data['seed_updates'])}; run factory library seed"
        )
    if args.json:
        return emit_ok(data, warnings)
    if command in ("clone", "status"):
        print(f"library  {data['library']} ({data['name']}, {data['id']})")
        print(f"remote   {data['remote'] or '-'}")
        print(f"branch   {data['branch']}")
        if data["remote"] is not None:
            print(f"ahead    {data['ahead']}, behind {data['behind']}")
            print(f"fetched  {data['last_fetch'] or 'never'}")
        print(f"changes  {len(data['uncommitted'])} uncommitted")
        if data["remote"] is not None:
            print(f"state    {data['sync_state']}")
        for pr in data["pull_requests"]:
            print(f"pr       {pr['url'] or pr['branch']}  {pr['title']}")
        print(f"factory  {data['factory_version']} (needs {data['min_factory_version'] or '-'})")
    elif command == "pull":
        print(f"pulled {data['pulled']} commit(s) from {data['remote']} {data['branch']}")
    elif data["pull_request"] is None:
        print("nothing to send: every local change is in an open pull request")
    else:
        pr = data["pull_request"]
        print(f"sent {data['pushed']} commit(s) for review: {pr['url'] or pr['branch']}")
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0
