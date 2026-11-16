"""``giro obs``: the local dashboard."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path
from typing import Any

from aifactory import __version__
from aifactory.cli.envelope import emit_fail, emit_ok


def _add_obs_command(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo",
        metavar="PATH",
        help="register this repository and open it (any folder inside it): its backlog, "
        "or its Factory tab when the factory is not ready",
    )
    parser.add_argument(
        "--port",
        metavar="N",
        type=int,
        help="port (default: port in the registry dashboard.yaml, else 4700)",
    )
    parser.add_argument(
        "--host",
        metavar="ADDR",
        help="address to listen on (default: $GIRO_DASH_HOST, else 127.0.0.1); "
        "0.0.0.0 serves the whole network, with no login",
    )
    parser.add_argument("--no-open", action="store_true", help="do not open the browser")
    parser.add_argument(
        "--json", action="store_true", help="print the URL as a JSON envelope before serving"
    )


def _obs(args: argparse.Namespace) -> int:
    import webbrowser

    from aifactory.config import ConfigError, load_local_checked
    from aifactory.home import giro_home
    from aifactory.registry import Registry, RepoEntry, RepoError
    from aifactory.repo_vet import register_repo
    from aifactory.web import app as web_app
    from aifactory.web import server as web_server
    from aifactory.web.backlog import UsageError
    from aifactory.web.repos import repo_status

    label = "factory obs"
    if args.port is not None and not 1 <= args.port <= 65535:
        message = f"--port must be between 1 and 65535, got {args.port}"
        if args.json:
            return emit_fail("invalid_value", message, exit_code=2)
        print(f"{label}: {message}", file=sys.stderr)
        return 2
    home = giro_home()
    registry = Registry(home)
    state, snapshot_warnings = registry.snapshot()
    warnings: list[str] = list(snapshot_warnings)
    port: int = args.port if args.port is not None else state.port
    entry: RepoEntry | None = None
    fragment = ""
    if args.repo:
        raw = str(Path(args.repo).expanduser().absolute())
        try:
            entry, _created, add_warnings = register_repo(registry, raw)
        except RepoError as exc:
            if args.json:
                return emit_fail(exc.code, exc.message, exit_code=2, data=exc.data or None)
            print(f"{label}: {exc.code}: {exc.message}", file=sys.stderr)
            return 2
        except UsageError as exc:
            if args.json:
                return emit_fail("usage_error", exc.message, exit_code=2)
            print(f"{label}: {exc.message}", file=sys.stderr)
            return 2
        warnings.extend(w for w in add_warnings if w not in warnings)
        try:
            _local, local_warnings = load_local_checked(Path(entry.path))
        except (ConfigError, OSError):
            local_warnings = []  # factory check reports a broken local.yaml
        warnings.extend(local_warnings)
        # a repo that is not ready (no factory, uncommitted config, ...) opens its Factory tab
        screen = "backlog" if repo_status(entry)["status"] == "ok" else "factory"
        fragment = f"#/r/{entry.id}/{screen}"

    host = web_server.resolve_host(args.host)
    url = web_server.dashboard_url(port, host)
    restarted = bool(os.environ.pop(web_server.RESTART_ENV, None))
    data: dict[str, Any] = {
        "url": url,
        "host": host,
        "port": port,
        "repo": entry.path if entry is not None else None,
        "version": __version__,
        "repo_id": entry.id if entry is not None else None,
        "home": str(home),
        "reused": False,
    }
    try:
        web_server.check_port(port, wait=10.0 if restarted else 0.0, host=host)
    except web_server.PortInUseError:
        if not restarted and web_server.probe_dashboard(port, host):
            if not args.no_open:
                webbrowser.open(url + fragment)
            data["reused"] = True
            if args.json:
                return emit_ok(data, warnings)
            print(f"GIRO dashboard už běží: {url}{fragment}")
            for warning in warnings:
                print(f"warning: {warning}", file=sys.stderr)
            return 0
        message = f"port {port} on {host} is already in use; pick another with --port"
        if args.json:
            return emit_fail("port_in_use", message, exit_code=2, warnings=warnings)
        print(f"{label}: {message}", file=sys.stderr)
        return 2
    app = web_app.create_multi_app(
        home=home, port=port, allowed_hosts=web_server.allowed_hosts(host), background_checks=True
    )
    if args.json:
        emit_ok(data, warnings)
    else:
        where = "" if host in web_server.LOOPBACK else f", naslouchá na {host}:{port}"
        print(f"GIRO dashboard: {url}{fragment}  (Ctrl+C ukončí{where})")
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
    sys.stdout.flush()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            web_server.serve(
                app,
                port,
                open_browser=not args.no_open and not restarted,
                host=host,
                open_path=fragment,
            )
    finally:
        left = len(app.state.launcher.running())
        message = f"Běhy spuštěné z dashboardu, které pokračují: {left}"
        if left:
            message += " (zastaví je factory task stop ID)"
        print(message, file=sys.stderr if args.json else sys.stdout)
    return 0
