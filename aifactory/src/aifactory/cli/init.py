"""``giro init`` and ``giro try``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from aifactory.cli.envelope import emit_ok
from aifactory.cli.library import _library_fail


def _add_init_command(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Install factory into a git repository that has none: .factory/config.yaml, the "
        "backlog directory and the .gitignore lines. Agents, workflows, skills and "
        "extensions are not copied: runs take them from the library in the home directory "
        "(the seed in the package without a library); a copy in .factory/ wins. Three "
        "modes. Without --dry-run and --commit the files are written to the working tree, "
        "existing files are skipped unless --force and nothing is committed. --dry-run "
        "returns the plan: files with content, blockers, warnings, digest and the detected "
        "remote, base, provider and harness CLIs (detected); it writes nothing. --commit "
        "makes the same plan one commit on base (pushed before base moves; --pr opens a "
        "pull request from factory-init/<n> instead); --expect DIGEST refuses a plan that "
        "changed (plan_changed). Blockers: already_installed (config.yaml in base), "
        "existing_config (an sssf installation; GIRO does not take it over), "
        "config_not_committed (run factory config commit), dirty_paths, invalid_plan, "
        "not_on_base, run_in_progress, base_behind, base_diverged. Nothing is written to "
        "the library."
    )
    parser.add_argument(
        "--repo", metavar="PATH", help="repository to install into (default: the current git repo)"
    )
    parser.add_argument(
        "--base",
        metavar="BRANCH",
        help="base branch of the runs (default: the remote HEAD with --dry-run/--commit, "
        "else the current branch)",
    )
    parser.add_argument(
        "--provider",
        choices=("local", "github", "azure"),
        default=None,
        help="git provider (default: from the remote URL with --dry-run/--commit, else local)",
    )
    parser.add_argument("--azure-org", metavar="ORG", help="Azure DevOps organization")
    parser.add_argument("--azure-project", metavar="PROJECT", help="Azure DevOps project")
    parser.add_argument("--azure-repo", metavar="REPO", help="Azure DevOps repository")
    parser.add_argument(
        "--docs-root",
        metavar="DIR",
        help="root of the factory documents with backlog/, specs/ and docs/ (default: factory)",
    )
    parser.add_argument(
        "--base-changes",
        choices=("direct", "pr"),
        default=None,
        help="how factory changes base from now on: direct commits (default) or a pull "
        "request per change; pr also makes --commit open a pull request",
    )
    parser.add_argument(
        "--base-pr-merge",
        choices=("manual", "auto"),
        default=None,
        help="factory's own pull requests: left to a reviewer (default) or approved where "
        "allowed and merged at once (GitHub with --admin)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="only return the plan; write nothing"
    )
    parser.add_argument(
        "--commit", action="store_true", help="commit the plan to base in one commit"
    )
    parser.add_argument(
        "--pr", action="store_true", help="with --commit: open a pull request instead"
    )
    parser.add_argument(
        "--expect", metavar="DIGEST", help="with --commit: refuse unless the plan has this digest"
    )
    parser.add_argument("-m", "--message", metavar="TEXT", help="with --commit: commit message")
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")


def _add_try_command(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Create the sample repository: a small Python library with tests "
        "(python -m unittest discover -s tests -t .) and a backlog of three tasks of "
        "growing size (DEMO-S01-T01..T03). The folder (default: demo in the GIRO home) "
        "gets git init with base main, .factory/config.yaml with git_provider local (no "
        "GitHub, no gh), the .gitignore lines and one commit; the repository is registered "
        "in the dashboard. A folder that exists and is not empty is not changed: its path "
        "is returned with created false (a git repository there is registered). git needs "
        "user.name and user.email (git_identity_missing); a path that is a file is "
        "invalid_value."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        metavar="DIR",
        help="folder of the sample repository (default: $GIRO_HOME/demo)",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")


def _try(args: argparse.Namespace) -> int:
    from aifactory.demo import create_demo
    from aifactory.library.store import LibraryStoreError

    directory = Path(args.directory) if args.directory else None
    try:
        result = create_demo(directory)
    except LibraryStoreError as exc:
        return _library_fail(exc, args.json, "factory try")
    if args.json:
        return emit_ok(result.to_json(), result.warnings)
    if result.created:
        print(f"created {result.path}")
        print(f"next: factory obs --repo {result.path}  (or: factory task run DEMO-S01-T01)")
    else:
        print(result.path)
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _init_conflict(args: argparse.Namespace) -> str | None:
    plan = args.dry_run or args.commit
    azure = [args.azure_org, args.azure_project, args.azure_repo]
    if args.force and plan:
        return "--force only writes the working tree; not with --dry-run or --commit"
    if args.pr and not plan:
        return "--pr needs --commit or --dry-run"
    if args.expect is not None and (args.dry_run or not args.commit):
        return "--expect needs --commit without --dry-run"
    if args.message is not None and not args.commit:
        return "-m needs --commit"
    if any(v is not None for v in azure) and args.provider not in (None, "azure"):
        return f"--azure-* need --provider azure, not {args.provider}"
    return None


def _init(args: argparse.Namespace) -> int:
    from aifactory.library.install import init_repo
    from aifactory.library.store import LibraryStoreError
    from aifactory.providers.base import ProviderError

    conflict = _init_conflict(args)
    if conflict is not None:
        error = LibraryStoreError("conflicting_options", conflict)
        return _library_fail(error, args.json, "factory init")
    azure_given = {
        "organization": args.azure_org,
        "project": args.azure_project,
        "repository": args.azure_repo,
    }
    provider = args.provider
    if provider is None and any(azure_given.values()):
        provider = "azure"
    options: dict[str, Any] = {
        "base": args.base,
        "docs_root": args.docs_root,
        "base_changes": args.base_changes,
        "base_pr_merge": args.base_pr_merge,
    }
    repo = Path(args.repo) if args.repo else Path.cwd()
    if args.dry_run or args.commit:
        return _init_plan(args, repo, provider, azure_given, options)
    try:
        azure = None
        if provider == "azure":
            if not all(azure_given.values()):
                raise LibraryStoreError(
                    "invalid_value",
                    "--provider azure needs --azure-org, --azure-project and --azure-repo",
                )
            azure = {k: str(v) for k, v in azure_given.items()}
        result = init_repo(
            repo,
            provider=provider or "local",
            azure=azure,
            force=args.force,
            **options,
        )
    except (LibraryStoreError, ProviderError) as exc:
        return _library_fail(exc, args.json, "factory init")
    if args.json:
        return emit_ok(result.to_json(), result.warnings)
    for f in result.files:
        print(f"{f.action:<11} {f.path}")
    for line in result.gitignore_added:
        print(f"{'gitignore':<11} {line}")
    print(f"next: review, commit .factory/ {result.backlog_dir}/ .gitignore to {result.base}")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _init_plan(
    args: argparse.Namespace,
    repo: Path,
    provider: str | None,
    azure: dict[str, str | None],
    options: dict[str, Any],
) -> int:
    """``factory init --dry-run`` and ``factory init --commit``."""
    from aifactory.library.install_commit import commit_init
    from aifactory.library.store import LibraryStoreError
    from aifactory.providers.base import ProviderError

    try:
        result = commit_init(
            repo,
            expect=args.expect,
            message=args.message,
            pr=args.pr,
            dry_run=args.dry_run,
            provider=provider,
            azure=azure,
            **options,
        )
    except (LibraryStoreError, ProviderError) as exc:
        return _library_fail(exc, args.json, "factory init")
    if args.json:
        return emit_ok(result.to_json(), result.warnings)
    plan = result.plan.publish
    if result.dry_run:
        for f in plan.files:
            print(f"{f.action:<7} {f.path}")
        if result.plan.exclude is not None:
            for line in result.plan.exclude["lines"]:
                print(f"{'exclude':<7} {line}")
        for b in plan.blockers:
            print(f"blocked: {b.code}: {b.message}")
        print(f"digest  {plan.digest}")
        if not plan.blockers:
            pr = " --pr" if args.pr else ""
            for flag, value in (
                ("--base-changes", args.base_changes),
                ("--base-pr-merge", args.base_pr_merge),
            ):
                if value is not None:
                    pr += f" {flag} {value}"
            print(f"next: factory init --commit{pr} --expect {plan.digest}")
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
