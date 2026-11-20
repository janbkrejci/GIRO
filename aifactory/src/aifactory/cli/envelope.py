"""The ``--json`` output: one envelope ``{ok, data, error, warnings}`` per command."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping
from typing import Any

from aifactory.skill.envelope import envelope_fail, envelope_ok


def emit(envelope: Mapping[str, Any]) -> None:
    from aifactory.providers.network import diagnostics

    records = diagnostics()
    if records:
        envelope = {
            **envelope,
            "data": {**(envelope.get("data") or {}), "network_operations": records},
        }
    print(json.dumps(envelope, ensure_ascii=False, indent=2))


def emit_ok(data: Mapping[str, Any], warnings: Iterable[str] = ()) -> int:
    emit(envelope_ok(data, warnings))
    return 0


def emit_fail(
    code: str,
    message: str,
    *,
    exit_code: int,
    data: Mapping[str, Any] | None = None,
    path: str | None = None,
    id: str | None = None,
    issues: Iterable[Mapping[str, Any]] = (),
    warnings: Iterable[str] = (),
) -> int:
    emit(
        envelope_fail(code, message, data=data, path=path, id=id, issues=issues, warnings=warnings)
    )
    return exit_code


def config_issues(exc: Exception) -> list[dict[str, Any]]:
    from aifactory.config import ConfigError

    if not isinstance(exc, ConfigError):
        return []
    return [
        {"code": exc.code, "message": i.message, "path": i.path, "id": None} for i in exc.issues
    ]


def fail_from_exception(exc: Exception, as_json: bool, label: str) -> int:
    """Report an expected error of a command (``label``, e.g. ``factory task run``)."""
    from aifactory.backlog import TaskEditError
    from aifactory.config import ConfigError
    from aifactory.engine.role_registry import RolesError
    from aifactory.errors import TaskRunError
    from aifactory.harness.config import HarnessConfigError
    from aifactory.review import ReviewError

    if isinstance(exc, TaskEditError):
        if as_json:
            return emit_fail(
                exc.code,
                exc.message,
                exit_code=exc.exit_code,
                path=exc.path,
                id=exc.id,
                issues=[i.to_dict() for i in exc.issues],
            )
        print(f"{label}: {exc.message}", file=sys.stderr)
        for issue in exc.issues:
            print(f"  {issue.path}: {issue.code}: {issue.message}", file=sys.stderr)
        return exc.exit_code
    if isinstance(exc, TaskRunError | ReviewError):
        if as_json:
            return emit_fail(exc.code, exc.message, exit_code=2)
        print(f"{label}: {exc.code}: {exc.message}", file=sys.stderr)
        return 2
    if as_json:
        if isinstance(exc, RolesError):
            issues = [i.to_dict() for i in exc.issues]
            return emit_fail("invalid_roles", str(exc), exit_code=2, issues=issues)
        issues_: list[dict[str, Any]] = []
        code = "invalid_config"
        if isinstance(exc, ConfigError):
            issues_ = config_issues(exc)
            code = exc.code
        elif isinstance(exc, HarnessConfigError):
            issues_ = [{"code": "invalid_config", "message": p} for p in exc.problems]
        return emit_fail(code, str(exc), exit_code=2, issues=issues_)
    if isinstance(exc, ConfigError) and exc.code != "invalid_config":
        print(f"{label}: {exc.code}: {exc}", file=sys.stderr)
        return 2
    print(f"{label}: {exc}", file=sys.stderr)
    return 2


def problem_warnings(count: int) -> list[str]:
    return [f"{count} problem(s), run 'factory backlog check'"] if count else []
