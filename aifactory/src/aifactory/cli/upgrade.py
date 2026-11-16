"""``giro upgrade``."""

from __future__ import annotations

import argparse
import sys

from aifactory.cli.envelope import emit_fail, emit_ok


def _add_upgrade_command(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Install a newer GIRO from a bundle giro-<version>.zip (a path, or an https or "
        "file URL; other schemes are invalid_url). The bundle is unpacked into a temporary "
        "directory, SHA256SUMS is verified (checksum_mismatch), the version is read from the "
        "wheel and compared with the installed one (up_to_date when not newer; a broken zip "
        "or a missing file is bundle_invalid). Then uv tool install --force <wheel> "
        "--constraints constraints.txt runs in the bundle (upgrade_failed, uv_missing). An "
        "editable install is refused (editable_install): update it with git pull. Restart a "
        "running dashboard afterwards. On Windows the running factory cannot replace its own "
        "files: the install runs from a detached script after this command exits (handed_off, "
        "with its log), and a running dashboard is refused (dashboard_running). After the "
        "install the new factory refreshes the library: library clone --seed-fallback when it "
        "is missing, then library seed --all (library_refreshed); a failure is a warning."
    )
    parser.add_argument("bundle", metavar="BUNDLE", help="path or https/file URL of the zip")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="check the bundle and show the versions and the command; install nothing",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")


def _dashboard_running() -> bool:
    """Whether the dashboard of this GIRO home answers on its registered port."""
    from aifactory.home import giro_home
    from aifactory.registry import Registry
    from aifactory.web import server as web_server

    state, _warnings = Registry(giro_home()).snapshot()
    return web_server.probe_dashboard(state.port, web_server.resolve_host(None))


def _upgrade(args: argparse.Namespace) -> int:
    from aifactory.upgrade import (
        HANDOFF_HINT,
        RESTART_HINT,
        needs_handoff,
        run_upgrade,
    )
    from aifactory.versioning import UpgradeError

    try:
        # On Windows a running dashboard locks the tool environment the upgrade replaces.
        if needs_handoff() and not args.dry_run and _dashboard_running():
            raise UpgradeError(
                "dashboard_running",
                "close the running dashboard first, or update from its update chip",
            )
        result = run_upgrade(args.bundle, dry_run=args.dry_run)
    except UpgradeError as exc:
        if args.json:
            return emit_fail(exc.code, exc.message, exit_code=exc.exit_code, data=exc.data)
        print(f"factory upgrade: {exc.code}: {exc.message}", file=sys.stderr)
        if exc.code == "upgrade_failed" and exc.data and exc.data.get("stderr"):
            print(exc.data["stderr"], end="", file=sys.stderr)
        return exc.exit_code
    if args.json:
        data = result.to_json()
        if result.installed:
            data["next"] = RESTART_HINT
        elif result.handed_off:
            data["next"] = f"{HANDOFF_HINT}; log: {result.log}"
        return emit_ok(data, result.warnings)
    print(f"current  {result.current_version}")
    print(f"target   {result.target_version}")
    print(f"library  needs {result.min_factory_version or '-'}")
    print(f"command  {' '.join(result.command)}")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if result.dry_run:
        print("dry run: nothing installed")
    elif result.handed_off:
        print(f"installing GIRO {result.target_version}: {HANDOFF_HINT}; log: {result.log}")
    else:
        print(f"installed GIRO {result.target_version}; {RESTART_HINT}")
    return 0
