"""``giro check``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aifactory.cli.envelope import emit_fail, emit_ok


def _add_check_command(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Check, only by reading, whether factory will run in this repository and on this "
        "machine, and what to fix. Reports the state of the repo read from base "
        "(installed, unsupported, uncommitted or none) with its action (config_commit or "
        "init, or null) and findings {code, scope, severity, message, "
        "fix, action}: scope 'repo' is fixed and committed to base, 'machine' is fixed "
        "locally, 'library' is fixed in the library in $GIRO_HOME; severity is error, "
        "warning or info; action names the fix (init, config_commit, config_pull) or is "
        "null. "
        "Outside a git repository (without --repo) only the machine and the library are "
        "checked (in_repo false). "
        "Ahead/behind compare base and the library with local remote-tracking refs (no "
        "fetch). Exits 0 without errors, 1 with an error (checks_failed), 2 when --repo is "
        "not a git repository."
    )
    parser.add_argument(
        "--repo", metavar="PATH", help="repository to check (default: the current git repo)"
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help=(
            "skip calls to the hosting and the harness logins (gh auth status, az account "
            "show, claude auth status, codex login status, pi auth check, pi --list-models)"
        ),
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")


def _check(args: argparse.Namespace) -> int:
    from aifactory import check

    start = Path(args.repo) if args.repo else Path.cwd()
    try:
        report = check.run_check(start, offline=args.offline, require_repo=args.repo is not None)
    except check.NotARepositoryError as exc:
        if args.json:
            return emit_fail("not_a_repository", str(exc), exit_code=2, path=str(start))
        print(f"factory check: {exc}", file=sys.stderr)
        return 2
    errors = report.errors()
    if args.json:
        data = report.to_json()
        if report.ok:
            return emit_ok(data)
        codes = ", ".join(dict.fromkeys(f.code for f in errors))
        return emit_fail(
            "checks_failed", f"{len(errors)} error(s): {codes}", exit_code=1, data=data
        )
    if not report.in_repo:
        print("state:   outside a git repository (machine and library only)")
    else:
        where = (
            f"{report.base} @ {report.commit[:7]}" if report.commit else f"{report.base}, no commit"
        )
        nxt = f"  next: {report.action}" if report.action else ""
        print(f"state:   {report.state} ({where})  {report.repo}{nxt}")
    if report.ahead is not None and report.behind is not None:
        print(f"remote:  {report.remote}  ahead {report.ahead}, behind {report.behind}")
    for finding in report.findings:
        print(f"{finding.severity:<7} {finding.scope:<7} {finding.code}: {finding.message}")
        if finding.fix or finding.action:
            action = f" [{finding.action}]" if finding.action else ""
            print(f"        fix: {finding.fix or ''}{action}")
    counts = report.counts()
    print(f"{counts['error']} error(s), {counts['warning']} warning(s), {counts['info']} info")
    return 0 if report.ok else 1
