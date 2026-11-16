"""Execute a config plan: ``--expect`` and blockers, then the library (move) and the repo.

The repo side goes to the working tree, a commit on base or a pull request.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from aifactory.config.commit import CONFIG_BRANCH_PREFIX
from aifactory.config.loader import SKILLS_DIR, SKILLS_MIRROR_DIR
from aifactory.config.source import FACTORY_DIR
from aifactory.library.config_edit.plan import ConfigPlan, _library_source, plan_config
from aifactory.library.config_edit.state import Change, Command
from aifactory.library.install_commit import _store
from aifactory.library.store import LibraryStoreError, import_items
from aifactory.oscompat import atomic_write
from aifactory.providers import get_provider, publish
from aifactory.providers.base import ProviderError, PullRequest


@dataclass(frozen=True)
class ConfigResult:
    plan: ConfigPlan
    dry_run: bool
    written: bool = False
    committed: bool = False
    commit: str | None = None
    pushed: bool = False
    advanced: bool = False
    branch: str | None = None
    pr: PullRequest | None = None
    extra_warnings: tuple[str, ...] = ()
    library_commit: str | None = None  # move: the library commit written first
    merged: bool = False

    @property
    def warnings(self) -> list[str]:
        return list(self.plan.warnings) + list(self.extra_warnings)

    def to_json(self) -> dict[str, Any]:
        pr = None
        if self.pr is not None:
            pr = {"id": self.pr.id, "url": self.pr.url, "branch": self.pr.branch}
        return {
            **self.plan.to_json(),
            "dry_run": self.dry_run,
            "written": self.written,
            "committed": self.committed,
            "commit": self.commit,
            "pushed": self.pushed,
            "advanced": self.advanced,
            "branch": self.branch,
            "pr": pr,
            "merged": self.merged,
            **({"library_commit": self.library_commit} if self.plan.command == "move" else {}),
        }


def _subject(plan: ConfigPlan) -> str:
    if plan.command == "set":
        return f"factory: set agent {plan.name} ({', '.join(plan.change.keys) or 'bindings'})"
    if plan.command == "move":
        return f"factory: move {plan.type} {plan.name} to the library"
    return f"factory: remove {plan.type} {plan.name}"


def _body(plan: ConfigPlan) -> str:
    lines = [f"- {f.action} `{f.path}`" for f in plan.publish.files]
    return (
        f"{_subject(plan)}:\n\n" + "\n".join(lines) + f"\n\nPlan digest: `{plan.publish.digest}`\n"
    )


_STOPS = (FACTORY_DIR, SKILLS_DIR, SKILLS_MIRROR_DIR)


def _prune_up(root: Path, folder: Path) -> None:
    stops = {(root / s).resolve() for s in _STOPS} | {root.resolve()}
    current = folder.resolve()
    while current not in stops and root.resolve() in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def write_worktree(root: Path, change: Change) -> None:
    """Write the change to the working tree: atomic replaces, deletions, empty folders go."""
    for path, data in sorted(change.files.items()):
        target = root / path
        if data is None:
            if target.is_file() or target.is_symlink():
                target.unlink()
                _prune_up(root, target.parent)
            continue
        atomic_write(target, data, mode=0o755 if path in change.executable else 0o644)


def run_config(
    command: Command,
    path: Path,
    *,
    type: str,
    name: str,
    dry_run: bool = False,
    commit: bool = False,
    pr: bool = False,
    expect: str | None = None,
    message: str | None = None,
    environ: Mapping[str, str] | None = None,
    **options: Any,
) -> ConfigResult:
    """Plan, then write to the working tree or commit to base; see the module docstring.

    ``move`` writes the library first and stops there when that fails.
    """
    plan = plan_config(
        command, path, type=type, name=name, commit=commit, pr=pr, environ=environ, **options
    )
    return execute_plan(plan, dry_run=dry_run, expect=expect, message=message, environ=environ)


def execute_plan(
    plan: ConfigPlan,
    *,
    dry_run: bool = False,
    expect: str | None = None,
    message: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ConfigResult:
    """Return the dry run, or check ``--expect`` and the blockers and write the plan."""
    command = plan.command
    if dry_run:
        return ConfigResult(plan, True)
    if expect is not None and expect.strip() != plan.publish.digest:
        raise LibraryStoreError(
            "plan_changed",
            "the plan changed since it was reviewed (--expect); run --dry-run again",
            data=plan.to_json(),
        )
    if plan.blockers:
        first = plan.blockers[0]
        issues = plan.issues if first.code == "invalid_plan" else []
        raise LibraryStoreError(first.code, first.message, data=plan.to_json(), issues=issues)
    if not plan.changed:
        return ConfigResult(plan, False)
    library_commit = _write_library(plan, environ)
    try:
        result = _write_repo(plan, command, message)
    except LibraryStoreError as exc:
        if library_commit is None:
            raise
        data = dict(exc.data or plan.to_json())
        data["library_commit"] = library_commit
        data["fix"] = "run the same giro config move again; the library is done"
        raise LibraryStoreError(
            exc.code,
            f"{exc.message}; the library already has the item "
            f"({library_commit[:12]}), run the move again to delete the repo copy",
            data=data,
            issues=exc.issues,
        ) from exc
    if library_commit is not None:
        result = replace(result, library_commit=library_commit)
    return result


def _write_library(plan: ConfigPlan, environ: Mapping[str, str] | None) -> str | None:
    """Move: write the library first (lock, fetch, a pull request when shared, fast-forward)."""
    if not plan.library_changed or plan.library_plan is None:
        return None

    source, subject = _library_source(plan.type, plan.name, plan.state.root)
    written = import_items(
        plan.change.library_items,
        source,
        subject,
        environ=environ,
        expect_head=plan.library_plan.head,
    )
    return written.commit


def _write_repo(plan: ConfigPlan, command: Command, message: str | None) -> ConfigResult:
    """Write the repo side of the plan: the working tree, a commit on base or a PR."""

    root = plan.state.root
    if not plan.publish.files:
        return ConfigResult(plan, False)
    if plan.target == "worktree":
        write_worktree(root, plan.change)
        return ConfigResult(
            plan,
            False,
            written=True,
            extra_warnings=(
                f"written to the working tree only; runs use {plan.state.base} until "
                "giro config commit",
            ),
        )
    subject = (message or "").strip() or _subject(plan)
    settings = plan.state.settings
    try:
        if plan.target == "pr":
            provider = get_provider(settings, root)
            result = publish.publish_pr(
                root,
                provider=provider,
                settings=settings,
                plan=plan.publish,
                message=subject,
                body=_body(plan),
                prefix=CONFIG_BRANCH_PREFIX,
            )
            if settings.base_pr_merge == "auto":
                store = _store(root)
                try:
                    result = publish.finish_pr(
                        root,
                        provider=provider,
                        settings=settings,
                        plan=plan.publish,
                        result=result,
                        message=subject,
                        store=store,
                        materialize=True,
                        command=f"config {command}",
                    )
                finally:
                    if store is not None:
                        store.close()
        else:
            store = _store(root)
            try:
                result = publish.publish_direct(
                    root,
                    settings=settings,
                    plan=plan.publish,
                    message=subject,
                    store=store,
                    materialize=True,
                    command=f"config {command}",
                )
            finally:
                if store is not None:
                    store.close()
    except ProviderError as exc:
        raise LibraryStoreError(exc.code, exc.message, data=plan.to_json()) from exc
    except RuntimeError as exc:
        raise LibraryStoreError("commit_failed", str(exc), data=plan.to_json()) from exc
    return ConfigResult(
        plan,
        False,
        committed=True,
        commit=result.commit,
        pushed=result.pushed,
        advanced=result.advanced,
        branch=result.branch,
        pr=result.pr,
        extra_warnings=tuple(result.warnings),
        merged=result.merged,
    )
